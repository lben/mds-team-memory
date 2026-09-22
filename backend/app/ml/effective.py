"""The lightweight read boundary for active findings and human opt-outs."""

import json

from sqlalchemy import and_, case, exists, func, literal_column, or_, select, true

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


def _policy_active(kind, encoded):
    from . import policy

    return int(policy.decide(kind, json.loads(encoded))[0] == "active")


def _register_policy(db):
    # Register on the actual Session connection, including worker and isolated
    # migration/test engines. The callback only evaluates supplied JSON; it
    # never queries the database or caches a decision. Do not mark deterministic:
    # the application policy can change while a long-lived connection survives.
    connection = db.connection()
    if not connection.info.get("ml_policy_active_registered"):
        connection.connection.driver_connection.create_function("ml_policy_active", 2, _policy_active)
        connection.info["ml_policy_active_registered"] = True


def current_dependencies(db, kind):
    if kind == "expertise":
        # Human topic confirmations are checked by projection.current. They
        # do not inherit machine alias/mention dependencies from item tags.
        return true()
    if kind not in {"concept", "mention"}:
        return identity.current_decision()
    dependencies = exists(select(Evidence.key).where(
        Evidence.finding_key == Finding.key, or_(
            func.json_array_length(Evidence.features, "$.identity_routes") > 0,
            func.json_array_length(Evidence.features, "$.term_routes") > 0)).correlate(Finding))
    valid = (Evidence.finding_key == Finding.key, identity.valid_routes(),
             identity.valid_term_routes())
    _register_policy(db)
    row = func.json_set(Evidence.features, "$.raw_score", Evidence.raw_score,
                        "$.polarity", Evidence.polarity, "$.group_key", Evidence.group_key,
                        "$.author_id", Evidence.author_id)
    evidence = (select(func.json_group_array(row)).join_from(Evidence, Source, valid_evidence())
                .where(*valid).correlate(Finding).scalar_subquery())
    active = func.ml_policy_active(kind, evidence) == 1
    # CASE guarantees the evidence query and policy callback run only for this
    # finding when it actually has conditional evidence. Every evidence lookup
    # is correlated by the indexed finding_key, including item tag requests.
    return case((dependencies, active), else_=True)


def enabled(db, kind):
    from . import projection

    pinned = exists(select(Override.key).where(Override.key == Finding.key, Override.mode == "pinned").correlate(Finding))
    suppressed = exists(select(Override.key).where(Override.key == Finding.key, Override.mode == "suppressed").correlate(Finding))
    automatic = [Finding.state == "active", supported(), current_dependencies(db, kind)]
    if kind == "alias":
        automatic.append(identity.current_scope_decision())
    elif kind == "expertise":
        pinned = exists(select(Override.key).join(Profile,
            Profile.id == func.json_extract(Override.payload, "$.profile_id")).where(
            Override.key == Finding.key, Override.mode == "pinned",
            Profile.account_id == func.json_extract(Override.payload, "$.account_id"),
            Profile.account_binding_revision == func.json_extract(Override.payload, "$.account_binding_revision")
        ).correlate(Finding))
        automatic.append(projection.current(db))
    return and_(~suppressed, or_(pinned, and_(*automatic)))


def concepts(db):
    owned = exists(select(Finding.key).where(Finding.kind == "concept", Finding.canonical_id == Concept.id))
    active = exists(select(Finding.key).where(Finding.kind == "concept", Finding.canonical_id == Concept.id, enabled(db, "concept")))
    return db.query(Concept).filter(or_(~owned, active))


def terms(db):
    owned = exists(select(Finding.key).where(Finding.kind == "alias", Finding.canonical_id == ConceptTerm.id))
    authority = identity.term_authorities()
    active = exists(select(authority.c.alias_key).where(authority.c.term_id == ConceptTerm.id))
    return db.query(ConceptTerm).filter(ConceptTerm.concept_id.in_(concepts(db).with_entities(Concept.id)), or_(~owned, active))


def expertise(db):
    owned = exists(select(Finding.key).where(Finding.kind == "expertise", Finding.canonical_id == ExpertiseMapping.id))
    active = exists(select(Finding.key).where(Finding.kind == "expertise", Finding.canonical_id == ExpertiseMapping.id, enabled(db, "expertise")))
    return (db.query(ExpertiseMapping).join(Profile, Profile.id == ExpertiseMapping.profile_id)
            .join(Account, Account.id == Profile.account_id)
            .filter(ExpertiseMapping.concept_id.in_(concepts(db).with_entities(Concept.id)), or_(~owned, active)))


def source_tags(db, kind, source_id, exact):
    findings = (db.query(Finding).join(Evidence, Evidence.finding_key == Finding.key)
                .join(Source, valid_evidence()).filter(Finding.kind == "mention", enabled(db, "mention"),
                Evidence.source_kind == kind, Evidence.source_id == source_id).all())
    wanted = set(exact) | {row.canonical_id for row in findings if row.canonical_id}
    pinned = (db.query(Finding).join(Override, Override.key == Finding.key)
              .filter(Override.kind == "mention", Override.mode == "pinned",
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
    from . import policy, relation_syntax, relationship_grounding

    result = []
    finding = db.get(Finding, key)
    relationship = finding is not None and finding.kind == "relationship"
    query = db.query(Evidence).join(Source, valid_evidence()).filter(
        Evidence.finding_key == key, identity.valid_routes(), identity.valid_term_routes())
    if finding is not None and finding.kind in {"alias", "alias_definition"}:
        query = query.filter(identity.current_scope_evidence())
    if finding is not None and finding.kind == "expertise":
        from . import projection

        query = query.join(Finding, Finding.key == Evidence.finding_key).filter(projection.current_confirmations(db))
    if relationship:
        query = query.filter(
            func.json_extract(Source.result, "$.alias_scope_contract") == identity.scope_contract(),
            Source.model_version == Evidence.model_version,
            Evidence.model_version + ":" + policy.VERSION == literal_column(
                "(SELECT pipeline_version FROM ml_state WHERE id=1)"),
            func.json_extract(Source.result, "$.relation_guard_revision") == relation_syntax.REVISION,
            func.json_extract(Evidence.features, "$.relation_guard_revision") == relation_syntax.REVISION)
    for row in query:
        if relationship and not relationship_grounding.current_version(row.model_version):
            continue
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
