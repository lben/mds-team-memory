"""Concept-to-concept link discovery, evidence and review state.

Links are stored rather than recomputed per request so that an admin's approve
or reject decision survives, and so a rejected link keeps accumulating evidence
and can be reinstated later.
"""

import json
from itertools import combinations

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .ml import effective, policy
from .ml.models import Finding, Override
from .ml.sources import finding_key, snapshot
from .models import (
    RELATED_TO_ID,
    Concept,
    Document,
    DocumentPassage,
    ItemConcept,
    KnowledgeItem,
    PassageConcept,
    Relationship,
    RelationshipType,
    utcnow,
)

VISIBLE_STATES = ("suggested", "confirmed")


def automation_enabled(db: Session) -> bool:
    return bool(db.execute(text("SELECT automation_enabled FROM ml_state WHERE id=1")).scalar())


def _team_subject_ids(db: Session, concept_id: str) -> set[tuple[str, str]]:
    """(kind, id) of team-visible subjects tagged with a concept.

    Private items are excluded here, which is what keeps private scratchpad
    content out of link counts, evidence and the map.
    """
    item_rows = db.execute(
        select(ItemConcept.item_id)
        .join(KnowledgeItem, KnowledgeItem.id == ItemConcept.item_id)
        .where(ItemConcept.concept_id == concept_id, KnowledgeItem.visibility == "team")
    ).all()
    passage_rows = db.execute(
        select(PassageConcept.passage_id).where(PassageConcept.concept_id == concept_id)
    ).all()
    return {("item", r[0]) for r in item_rows} | {("passage", r[0]) for r in passage_rows}


def shared_subjects(db: Session, concept_a: str, concept_b: str) -> list[tuple[str, str]]:
    return sorted(_team_subject_ids(db, concept_a) & _team_subject_ids(db, concept_b))


def find_link(db: Session, concept_a: str, concept_b: str) -> Relationship | None:
    """A concept pair has at most one link, in either stored direction."""
    return (
        db.query(Relationship)
        .filter(
            Relationship.src_kind == "concept",
            Relationship.dst_kind == "concept",
            (
                ((Relationship.src_id == concept_a) & (Relationship.dst_id == concept_b))
                | ((Relationship.src_id == concept_b) & (Relationship.dst_id == concept_a))
            ),
        )
        .first()
    )


def refresh_for_item(db: Session, concept_ids: list[str]) -> None:
    """Update counts for every concept pair touched by a new contribution.

    Creates a `suggested` link once a pair reaches the configured threshold, and
    keeps refreshing the count of links that already exist — including rejected
    ones, so an admin can watch the evidence for a rejection grow.
    """
    # Source triggers already queue the changed content. Legacy co-occurrence
    # must not overwrite the worker's projection or recreate a suppressed pair.
    if automation_enabled(db) or len(concept_ids) < 2:
        return
    for a, b in combinations(sorted(set(concept_ids)), 2):
        count = len(shared_subjects(db, a, b))
        link = find_link(db, a, b)
        if link:
            link.occurrence_count = count
        elif count >= config.COOCCURRENCE_MIN:
            db.add(
                Relationship(
                    src_kind="concept",
                    src_id=a,
                    dst_kind="concept",
                    dst_id=b,
                    relationship_type_id=RELATED_TO_ID,
                    state="suggested",
                    occurrence_count=count,
                )
            )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


def refresh_for_concept(db: Session, concept_id: str) -> None:
    """Discover links for one concept against content that already exists.

    Creating or renaming a concept retags everything, but discovery only ran
    when somebody posted, so an admin who defined two concepts that already
    co-occur in fifty notes was shown an empty map with no explanation. Scoped
    to this concept's own pairs, so the cost is bounded by how much content
    mentions it rather than by the size of the vocabulary.
    """
    if automation_enabled(db):
        from .ml.queue import enqueue
        enqueue(db, "vocabulary", "all")
        db.commit()
        return
    subjects = _team_subject_ids(db, concept_id)
    if not subjects:
        return
    item_ids = [sid for kind, sid in subjects if kind == "item"]
    passage_ids = [sid for kind, sid in subjects if kind == "passage"]
    others: set[str] = set()
    if item_ids:
        others |= {
            cid
            for (cid,) in db.query(ItemConcept.concept_id)
            .filter(ItemConcept.item_id.in_(item_ids))
            .distinct()
        }
    if passage_ids:
        others |= {
            cid
            for (cid,) in db.query(PassageConcept.concept_id)
            .filter(PassageConcept.passage_id.in_(passage_ids))
            .distinct()
        }
    others.discard(concept_id)
    for other in sorted(others):
        refresh_for_item(db, [concept_id, other])


def evidence_text(link: Relationship, count: int | None = None) -> str:
    """Derived at read time so the sentence can never go stale as content grows."""
    parts = []
    count = link.occurrence_count if count is None else count
    if count:
        entries = "entry" if count == 1 else "entries"
        parts.append(f"Mentioned together in {count} team {entries}.")
    if link.review_note:
        who = link.reviewed_by or "Admin"
        parts.append(f"Admin note ({who}): {link.review_note}")
    return " ".join(parts) or "No supporting team content yet."


def recount(db: Session, link: Relationship) -> int:
    if automation_enabled(db) and db.query(Finding.key).filter(
        Finding.canonical_id == link.id,
        Finding.kind.in_(("relationship", "association")),
    ).first():
        return link.occurrence_count
    link.occurrence_count = len(shared_subjects(db, link.src_id, link.dst_id))
    return link.occurrence_count


def directional(predicate: str) -> bool:
    return predicate.replace("_", " ").lower() not in {
        "related to", "association", "associated with", "similar to", "co occurs with", "conflicts with",
    }


def relationship_claims(db: Session, link: Relationship) -> list[dict]:
    """Re-evaluate all typed claims against live support, without mutating on reads."""
    claims = []
    for row in db.query(Finding).filter(Finding.canonical_id == link.id,
                                       Finding.kind.in_(("relationship", "association"))):
        payload = json.loads(row.payload)
        predicate = effective.predicate_name(db, payload["predicate"])
        evidence = effective.evidence_rows(db, row.key)
        fitted = policy.artifact(db)
        state, score = policy.decide(row.kind, evidence, fitted)
        fixed = db.get(Override, row.key)
        if fixed:
            state = "active" if fixed.mode == "pinned" else "suppressed"
        if predicate is None:
            state = "suppressed"
        # A co-occurrence can never be presented as a factual relationship.
        if row.kind == "association" and not fixed and state in {"active", "held", "weak"}:
            state = "weak"
        positive = [e for e in evidence if e["polarity"] == "positive"]
        claims.append({"finding_key": row.key, "kind": row.kind, "src_id": payload["src_id"],
                       "dst_id": payload["dst_id"], "predicate": predicate or payload["predicate"].replace("_", " "),
                       "state": state, "origin": "manual" if fixed and fixed.mode == "pinned" else "automatic",
                       "override": fixed.mode if fixed else "automatic", "policy_version": fitted.get("version", policy.VERSION),
                       "support_count": policy.independent_support(positive)[0],
                       "conflicts": any(e["polarity"] == "negative" for e in evidence),
                       "_score": score, "_evidence": evidence})
    return sorted(claims, key=lambda c: ({"active": 0, "held": 1, "weak": 2}.get(c["state"], 3),
                                         -c["_score"], c["finding_key"]))


def graph_link(db: Session, link: Relationship, claims: list[dict] | None = None) -> dict | None:
    """Effective pair summary; competing predicates remain in evidence detail."""
    pair = sorted((link.src_id, link.dst_id))
    fixed = db.get(Override, finding_key("relationship_pair", *pair))
    if fixed and fixed.mode == "suppressed":
        return None
    claims = relationship_claims(db, link) if claims is None else claims
    manual = (fixed and fixed.mode == "pinned") or (
        not fixed and not claims and link.state in VISIBLE_STATES and (link.reviewed_by or link.state == "confirmed")
    )
    enabled = automation_enabled(db)
    label, src_id, dst_id = link.relationship_type.name, link.src_id, link.dst_id
    winner = None
    if manual:
        state, origin = "active", "manual"
        count = len(shared_subjects(db, link.src_id, link.dst_id))
    elif claims:
        visible = [c for c in claims if c["state"] in {"active", "held", "weak"}]
        if not visible:
            return None
        best = visible[0]
        same_state = [c for c in visible if c["state"] == best["state"]]
        dominant = len(same_state) == 1 or best["_score"] >= same_state[1]["_score"] + 0.10
        winner = best if dominant and best["kind"] == "relationship" and best["state"] != "weak" else None
        state, origin = best["state"], best["origin"]
        label = winner["predicate"] if winner else "related to"
        src_id, dst_id = (winner["src_id"], winner["dst_id"]) if winner else pair
        support = [e for c in visible if c["state"] == state for e in c["_evidence"] if e["polarity"] == "positive"]
        count = policy.independent_support(support)[0]
    else:
        if link.state not in VISIBLE_STATES:
            return None
        count = len(shared_subjects(db, link.src_id, link.dst_id))
        if enabled and not count:
            return None
        state, origin = ("weak", "legacy") if enabled else ("held", "legacy")
        label = "related to"
    style = {"active": "solid", "held": "dashed", "weak": "dotted"}[state]
    evidence = evidence_text(link, count) if not enabled or manual else (
        f"{count} independent supporting source groups. {origin.capitalize()}; {state}."
    )
    return {"source": src_id, "target": dst_id, "label": label, "style": style,
            "state": state, "origin": origin, "directed": directional(label), "count": count,
            "support_count": count, "policy_version": policy.artifact(db).get("version", policy.VERSION) if claims and not manual else None,
            "evidence": evidence, "link_id": link.id, "finding_key": winner["finding_key"] if winner else None,
            "alternative_count": max(0, len(claims) - bool(winner)), "conflicts": any(c["conflicts"] for c in claims)}


def link_dict(db: Session, link: Relationship) -> dict:
    src = db.get(Concept, link.src_id)
    dst = db.get(Concept, link.dst_id)
    return {
        "id": link.id,
        "src_id": link.src_id,
        "src_name": src.name if src else "(deleted concept)",
        "dst_id": link.dst_id,
        "dst_name": dst.name if dst else "(deleted concept)",
        "type_id": link.relationship_type_id,
        "type_name": link.relationship_type.name,
        "state": link.state,
        "occurrence_count": link.occurrence_count,
        "evidence": evidence_text(link),
        "reviewed_by": link.reviewed_by,
        "reviewed_at": link.reviewed_at.isoformat() + "Z" if link.reviewed_at else None,
        "review_note": link.review_note,
        "created_at": link.created_at.isoformat() + "Z",
    }


def evidence_detail(db: Session, link: Relationship) -> dict:
    """The actual team-visible contributions behind a link, for drill-down."""
    subjects = shared_subjects(db, link.src_id, link.dst_id)
    items, passages = [], []
    for kind, subject_id in subjects:
        if kind == "item":
            item = db.get(KnowledgeItem, subject_id)
            if item and item.visibility == "team":
                items.append(
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "body": item.body,
                        "parent_id": item.parent_id,
                        "created_at": item.created_at.isoformat() + "Z",
                    }
                )
        else:
            passage = db.get(DocumentPassage, subject_id)
            if passage:
                document = db.get(Document, passage.document_id)
                passages.append(
                    {
                        "id": passage.id,
                        "document_id": passage.document_id,
                        "filename": document.filename if document else "",
                        "locator": passage.locator,
                        "text": passage.text,
                    }
                )
    src = db.get(Concept, link.src_id)
    dst = db.get(Concept, link.dst_id)
    claims = relationship_claims(db, link)
    summary = graph_link(db, link, claims)
    detailed_claims = []
    for claim in claims:
        sources = []
        for original in claim["_evidence"]:
            spans = [original]
            if original.get("context"):
                spans.append({**original, **original["context"], "context_source": True})
            for support in spans:
                source = snapshot(db, support["source_kind"], support["source_id"])
                if source is None or source.content_hash != support["source_hash"]:
                    continue
                passage = db.get(DocumentPassage, source.id) if source.kind == "passage" else None
                document = db.get(Document, passage.document_id) if passage else None
                item = db.get(KnowledgeItem, source.id) if source.kind == "item" else None
                sources.append({"source_kind": source.kind, "source_id": source.id,
                                "source_hash": source.content_hash, "start": support["start"], "end": support["end"],
                                "quote": source.text[support["start"]:support["end"]], "text": source.text,
                                "context_source": support.get("context_source", False), "polarity": support["polarity"], "model_version": support["model_version"],
                                "locator": source.locator, "origin": source.origin,
                                "document_id": passage.document_id if passage else None,
                                "filename": document.filename if document else None,
                                "parent_id": item.parent_id if item else None})
        head, tail = db.get(Concept, claim["src_id"]), db.get(Concept, claim["dst_id"])
        detailed_claims.append({**{key: value for key, value in claim.items() if not key.startswith("_")},
                                "src_name": head.name if head else "(deleted concept)",
                                "dst_name": tail.name if tail else "(deleted concept)",
                                "directed": directional(claim["predicate"]), "sources": sources})
    return {
        "link_id": link.id,
        "src_name": src.name if src else "",
        "dst_name": dst.name if dst else "",
        "occurrence_count": len(subjects),
        "items": items,
        "passages": passages,
        "summary": summary,
        "claims": detailed_claims,
        "review_note": link.review_note,
        "reviewed_by": link.reviewed_by,
    }


def set_state(db: Session, link: Relationship, state: str, admin_username: str, note: str | None) -> None:
    from .ml.adapter import fix_relationship
    link.state = state
    link.reviewed_by = admin_username
    link.reviewed_at = utcnow()
    link.review_note = (note or "").strip() or None
    if state == "suggested":
        fixed = db.get(Override, finding_key("relationship_pair", *sorted((link.src_id, link.dst_id))))
        if fixed:
            db.delete(fixed)
        link.reviewed_by = link.reviewed_at = None
        from .ml.queue import enqueue
        enqueue(db, "vocabulary", "all")
    else:
        fix_relationship(db, link, "suppressed" if state == "rejected" else "pinned", admin_username)
    recount(db, link)


def type_usage(db: Session, type_id: str) -> int:
    return (
        db.query(func.count(Relationship.id))
        .filter(Relationship.relationship_type_id == type_id)
        .scalar()
        or 0
    )
