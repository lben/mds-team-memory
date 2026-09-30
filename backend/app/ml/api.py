"""Optional finding management and a cross-process browser revision signal."""

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..auth import require_admin
from ..db import get_db
from ..models import Account, Concept, ConceptTerm, ExpertiseMapping, Relationship, utcnow
from . import adapter, effective, policy
from .models import Finding, Override
from .queue import enqueue
from .sources import finding_key, snapshot

router = APIRouter(prefix="/api/ml", tags=["knowledge maintenance"])


@router.get("/revision")
def revision(db=Depends(get_db)):
    row = db.execute(text("SELECT revision,automation_enabled,worker_lease_until FROM ml_state WHERE id=1")).one()
    return {"revision": row.revision, "enabled": bool(row.automation_enabled),
            "running": bool(row.worker_lease_until and row.worker_lease_until > time.time())}


def finding_dict(db, row):
    fixed = db.get(Override, row.key)
    state = fixed.mode if fixed else row.state
    return {"key": row.key, "kind": row.kind, "payload": json.loads(fixed.payload if fixed else row.payload),
            "state": state, "canonical_id": row.canonical_id, "origin": "manual" if fixed and fixed.mode == "pinned" else "automatic",
            "policy_version": row.policy_version,
            "raw_model_score": None if row.calibrated else row.score,
            "updated_at": row.updated_at.isoformat() + "Z"}


@router.get("/findings", dependencies=[Depends(require_admin)])
def findings(state: str | None = None, kind: str | None = None,
             offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), db=Depends(get_db)):
    query = db.query(Finding).filter(~Finding.kind.in_(("term", "bootstrap", "alias_definition")))
    if kind:
        query = query.filter(Finding.kind == kind)
    if state in {"pinned", "suppressed"}:
        query = query.join(Override, Override.key == Finding.key).filter(Override.mode == state)
    elif state:
        query = query.filter(Finding.state == state, ~Finding.key.in_(db.query(Override.key)))
    total = query.count()
    rows = query.order_by(Finding.updated_at.desc(), Finding.key).offset(offset).limit(limit).all()
    return {"total": total, "findings": [finding_dict(db, row) for row in rows]}


@router.get("/findings/{key}", dependencies=[Depends(require_admin)])
def finding_detail(key: str, db=Depends(get_db)):
    row = db.get(Finding, key)
    if row is None:
        raise HTTPException(404, "Finding not found")
    evidence = effective.evidence_rows(db, key)
    for entry in evidence:
        if entry["source_kind"] in {"item", "passage"}:
            source = snapshot(db, entry["source_kind"], entry["source_id"])
            if source:
                entry["quote"] = source.text[entry["start"]:entry["end"]]
    return {**finding_dict(db, row), "evidence": evidence, "features": policy.features(evidence)}


class Decision(BaseModel):
    mode: str = Field(pattern="^(automatic|pinned|suppressed)$")


@router.put("/findings/{key}/decision")
def decide(key: str, decision: Decision, admin: Account = Depends(require_admin), db=Depends(get_db)):
    row = db.get(Finding, key)
    if row is None or row.kind in {"bootstrap", "term", "alias_definition"}:
        raise HTTPException(404, "Finding not found")
    payload = json.loads(row.payload)
    if decision.mode == "pinned" and row.kind == "concept" and not row.canonical_id:
        term = db.query(ConceptTerm).filter_by(term=adapter.normalize(payload["name"])).first()
        alias = db.query(Finding).filter_by(kind="alias", canonical_id=term.id).first() if term else None
        if alias and json.loads(alias.payload).get("direction") == "inverse":
            owner = db.get(Concept, term.concept_id)
            raise HTTPException(400, f"'{term.display}' is already used by the concept '{owner.name if owner else '?'}'")
    if decision.mode == "pinned" and row.kind == "alias":
        term = db.query(ConceptTerm).filter_by(term=adapter.normalize(payload["alias"])).first()
        if term and term.concept_id != payload["concept_id"]:
            owner = db.get(Concept, term.concept_id)
            raise HTTPException(400, f"'{term.display}' is already used by the concept '{owner.name if owner else '?'}'")
    fixed = db.get(Override, key)
    if decision.mode == "automatic":
        if fixed:
            db.delete(fixed)
        row.state = "held"
        if row.kind == "concept":
            for excluded in db.query(Override).filter_by(kind="term"):
                if json.loads(excluded.payload).get("concept_suppression") == row.key:
                    db.delete(excluded)
        elif row.kind == "alias":
            excluded = db.get(Override, finding_key("term", adapter.normalize(payload["alias"])))
            if excluded:
                previous = json.loads(excluded.payload)
                if previous.get("concept_id") == payload["concept_id"] and not previous.get("concept_suppression"):
                    db.delete(excluded)
    else:
        adapter._override(db, row, decision.mode, admin.username)
        row.state = "active" if decision.mode == "pinned" else "suppressed"
    db.flush()
    if row.kind == "concept":
        concept = db.get(Concept, row.canonical_id) if row.canonical_id else None
        if decision.mode == "suppressed" and concept:
            adapter.suppress_concept(db, concept, admin.username)
        elif decision.mode == "pinned":
            adapter._publish_concept(db, row)
    elif row.kind == "alias":
        adapter._publish_alias(db, row)
    elif row.kind in {"relationship", "association", "relationship_pair"}:
        if row.kind == "relationship_pair":
            link = db.get(Relationship, row.canonical_id) if row.canonical_id else None
            if link:
                link.state = "confirmed" if decision.mode == "pinned" else "rejected" if decision.mode == "suppressed" else "suggested"
                link.reviewed_by = admin.username if decision.mode != "automatic" else None
        else:
            adapter._project_relationships(db, [payload["src_id"], payload["dst_id"]])
    elif row.kind == "expertise":
        adapter.apply_profile(db, payload["profile_id"])
    elif row.kind == "mention":
        from ..concepts import retag_item, retag_passage
        from ..models import DocumentPassage, KnowledgeItem
        source = db.get(KnowledgeItem if payload["source_kind"] == "item" else DocumentPassage, payload["source_id"])
        if source:
            (retag_item if payload["source_kind"] == "item" else retag_passage)(db, source)
        enqueue(db, payload["source_kind"], payload["source_id"])
    row.updated_at = utcnow()
    enqueue(db, "vocabulary", "all")
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))
    db.commit()
    return finding_dict(db, row)


class TopicEdit(BaseModel):
    concept_id: str


@router.put("/findings/{key}/topic")
def edit_topic(key: str, edit: TopicEdit, admin: Account = Depends(require_admin), db=Depends(get_db)):
    row = db.get(Finding, key)
    if row is None or row.kind != "mention":
        raise HTTPException(404, "Topic match not found")
    concept = effective.concepts(db).filter(Concept.id == edit.concept_id).first()
    if concept is None:
        raise HTTPException(400, "Choose an active concept")
    payload = json.loads(row.payload)
    if snapshot(db, payload["source_kind"], payload["source_id"]) is None:
        raise HTTPException(404, "Contribution is no longer available")
    adapter._override(db, row, "suppressed", admin.username)
    row.state = "suppressed"
    corrected = adapter._finding(db, "mention", [payload["source_kind"], payload["source_id"], concept.id],
                                 {**payload, "concept_id": concept.id})
    corrected.canonical_id = concept.id
    adapter._override(db, corrected, "pinned", admin.username)
    corrected.state = "active"
    db.flush()
    # Reuse the same public decision path for immediate retagging and routing.
    return decide(corrected.key, Decision(mode="pinned"), admin, db)
