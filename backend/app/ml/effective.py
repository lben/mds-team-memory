"""The lightweight read boundary for active findings and human opt-outs."""

import json

from sqlalchemy import and_, exists, func, or_, select

from ..models import Account, Concept, ConceptTerm, ExpertiseMapping, Profile, RelationshipType
from . import identity
from .models import Evidence, Finding, Override, Source
from .sources import finding_key
from .runtime import normalize


def predicate_name(db, predicate):
    name = predicate.replace("_", " ")
    fixed = db.get(Override, finding_key("predicate", normalize(name)))
    if fixed:
        if fixed.mode == "suppressed":
            return None
        rtype = db.get(RelationshipType, json.loads(fixed.payload)["type_id"])
        return rtype.name if rtype else None
    return name


def valid_evidence():
    return and_(Source.kind == Evidence.source_kind, Source.id == Evidence.source_id,
                Source.content_hash == Evidence.source_hash, Source.valid.is_(True))


def supported():
    return exists(select(Evidence.key).join(Source, valid_evidence()).where(Evidence.finding_key == Finding.key).correlate(Finding))


def enabled():
    pinned = exists(select(Override.key).where(Override.key == Finding.key, Override.mode == "pinned").correlate(Finding))
    suppressed = exists(select(Override.key).where(Override.key == Finding.key, Override.mode == "suppressed").correlate(Finding))
    return and_(~suppressed, or_(pinned, and_(Finding.state == "active", supported(), identity.current_decision())))


def concepts(db):
    owned = exists(select(Finding.key).where(Finding.kind == "concept", Finding.canonical_id == Concept.id))
    active = exists(select(Finding.key).where(Finding.kind == "concept", Finding.canonical_id == Concept.id, enabled()))
    return db.query(Concept).filter(or_(~owned, active))


def terms(db):
    owned = exists(select(Finding.key).where(Finding.kind == "alias", Finding.canonical_id == ConceptTerm.id))
    active = exists(select(Finding.key).where(Finding.kind == "alias", Finding.canonical_id == ConceptTerm.id, enabled()))
    return db.query(ConceptTerm).filter(ConceptTerm.concept_id.in_(concepts(db).with_entities(Concept.id)), or_(~owned, active))


def expertise(db):
    owned = exists(select(Finding.key).where(Finding.kind == "expertise", Finding.canonical_id == ExpertiseMapping.id))
    active = exists(select(Finding.key).where(Finding.kind == "expertise", Finding.canonical_id == ExpertiseMapping.id, enabled()))
    return (db.query(ExpertiseMapping).join(Profile, Profile.id == ExpertiseMapping.profile_id)
            .join(Account, Account.id == Profile.account_id)
            .filter(ExpertiseMapping.concept_id.in_(concepts(db).with_entities(Concept.id)), or_(~owned, active)))


def source_tags(db, kind, source_id, exact):
    findings = (db.query(Finding).join(Evidence, Evidence.finding_key == Finding.key)
                .join(Source, valid_evidence()).filter(Finding.kind == "mention", enabled(),
                Evidence.source_kind == kind, Evidence.source_id == source_id).all())
    wanted = set(exact) | {row.canonical_id for row in findings if row.canonical_id}
    pinned = (db.query(Finding).join(Override, Override.key == Finding.key)
              .filter(Finding.kind == "mention", Override.mode == "pinned",
                      func.json_extract(Finding.payload, "$.source_kind") == kind,
                      func.json_extract(Finding.payload, "$.source_id") == source_id))
    wanted.update(row.canonical_id for row in pinned if row.canonical_id)
    if not wanted:
        return wanted
    wanted &= {row.id for row in concepts(db).filter(Concept.id.in_(wanted)).all()}
    keys = {finding_key("mention", kind, source_id, cid): cid for cid in wanted}
    for override in db.query(Override).filter(Override.key.in_(keys), Override.mode == "suppressed"):
        wanted.discard(keys[override.key])
    return wanted


def evidence_rows(db, key):
    result = []
    for row in db.query(Evidence).join(Source, valid_evidence()).filter(Evidence.finding_key == key, identity.valid_routes()):
        features = json.loads(row.features)
        context = features.get("context")
        if context:
            peer = db.get(Source, (context["source_kind"], context["source_id"]))
            if peer is None or not peer.valid or peer.content_hash != context["source_hash"]:
                continue
        result.append({**features, "key": row.key, "source_kind": row.source_kind,
                       "source_id": row.source_id, "source_hash": row.source_hash,
                       "group_key": row.group_key, "author_id": row.author_id,
                       "start": row.start, "end": row.end, "raw_score": row.raw_score,
                       "polarity": row.polarity, "model_version": row.model_version})
    return result
