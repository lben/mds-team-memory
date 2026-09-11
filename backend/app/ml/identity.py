"""Exact scored alias witnesses and their conditional evidence provenance."""

import json

from sqlalchemy import func, literal_column, text

from ..models import Account, ConceptTerm, Profile
from . import policy, syntax
from .models import Finding, Override, Source
from .runtime import normalize
from .sources import digest, finding_key

VERSION = "exact-scored-identity-v1"


def definitions_pending(db):
    return db.execute(text("""SELECT 1 FROM ml_sources WHERE kind IN ('item','passage')
      AND valid=1 AND COALESCE(json_extract(result,'$.definitions_indexed'),0)<2 LIMIT 1""")).first() is not None


def valid_routes():
    # The same guard is used for scores and stored decisions. A stale maximum
    # cannot remain public merely because weaker direct evidence still exists.
    return literal_column(f"""NOT EXISTS (
      SELECT 1 FROM json_each(ml_evidence.features,'$.identity_routes') route
      WHERE NOT EXISTS (
        SELECT 1 FROM ml_evidence witness
        JOIN ml_sources source ON source.kind=witness.source_kind AND source.id=witness.source_id
        JOIN ml_findings definition ON definition.key=witness.finding_key AND definition.kind='alias_definition'
        JOIN ml_findings target ON target.key=json_extract(route.value,'$.concept_key') AND target.kind='concept'
        JOIN ml_state state ON state.id=1
        WHERE witness.key=json_extract(route.value,'$.definition_evidence_key')
          AND source.valid=1 AND source.content_hash=witness.source_hash
          AND source.kind=json_extract(route.value,'$.witness_source_kind')
          AND source.id=json_extract(route.value,'$.witness_source_id')
          AND source.content_hash=json_extract(route.value,'$.witness_source_hash')
          AND source.model_version=witness.model_version
          AND witness.model_version=json_extract(route.value,'$.model_version')
          AND ml_evidence.model_version=witness.model_version
          AND state.pipeline_version=witness.model_version || ':{policy.VERSION}'
          AND json_extract(route.value,'$.routing_policy')='{VERSION}'
          AND witness.polarity='positive' AND json_extract(witness.features,'$.assertion_allowed')=1
          AND json_extract(definition.payload,'$.alias_key')=json_extract(route.value,'$.alias_key')
          AND json_extract(definition.payload,'$.concept_key')=target.key
          AND EXISTS (SELECT 1 FROM json_each(witness.features,'$.definition_methods') method
            WHERE method.value=json_extract(route.value,'$.method_json')
              AND json_extract(method.value,'$.syntax_rule_revision')='{syntax.REVISION}'
              AND json_extract(method.value,'$.source_text_hash')=json_extract(source.result,'$.text_hash'))
          AND EXISTS (SELECT 1 FROM ml_sources owner WHERE owner.kind=ml_evidence.source_kind
            AND owner.id=ml_evidence.source_id AND owner.valid=1 AND owner.content_hash=ml_evidence.source_hash)
          AND NOT EXISTS (SELECT 1 FROM ml_sources pending WHERE pending.kind IN ('item','passage')
            AND pending.valid=1 AND COALESCE(json_extract(pending.result,'$.definitions_indexed'),0)<2)
          AND NOT EXISTS (SELECT 1 FROM concept_terms short WHERE short.term=json_extract(route.value,'$.alias_key')
            AND short.is_canonical=1 AND (target.canonical_id IS NULL OR short.concept_id!=target.canonical_id))
          AND NOT EXISTS (SELECT 1 FROM ml_overrides fixed WHERE fixed.mode='suppressed' AND
            (fixed.key=target.key OR fixed.key=json_extract(route.value,'$.term_key')
             OR fixed.key=json_extract(route.value,'$.full_term_key')
             OR (fixed.kind='concept' AND json_extract(fixed.payload,'$.name')=json_extract(target.payload,'$.name'))
             OR (fixed.kind='alias' AND json_extract(fixed.payload,'$.alias_key')=json_extract(route.value,'$.alias_key')
                 AND json_extract(fixed.payload,'$.concept_id')=target.canonical_id)
             OR (fixed.kind='mention' AND json_extract(fixed.payload,'$.source_kind')=ml_evidence.source_kind
                 AND json_extract(fixed.payload,'$.source_id')=ml_evidence.source_id
                 AND json_extract(fixed.payload,'$.concept_id')=target.canonical_id)))
          AND (SELECT count(*) FROM (
            SELECT possible.key FROM ml_findings possible
            WHERE possible.kind='alias_definition'
              AND json_extract(possible.payload,'$.alias_key')=json_extract(route.value,'$.alias_key')
              AND EXISTS (SELECT 1 FROM ml_evidence e JOIN ml_sources s ON s.kind=e.source_kind AND s.id=e.source_id
                AND s.valid=1 AND s.content_hash=e.source_hash WHERE e.finding_key=possible.key)
            LIMIT 33))<=32
          AND NOT EXISTS (SELECT 1 FROM ml_findings possible
            LEFT JOIN ml_findings alternative ON alternative.key=json_extract(possible.payload,'$.concept_key')
            WHERE possible.kind='alias_definition'
              AND json_extract(possible.payload,'$.alias_key')=json_extract(route.value,'$.alias_key')
              AND EXISTS (SELECT 1 FROM ml_evidence e JOIN ml_sources s ON s.kind=e.source_kind AND s.id=e.source_id
                AND s.valid=1 AND s.content_hash=e.source_hash WHERE e.finding_key=possible.key)
              AND (alternative.key IS NULL OR (alternative.key!=target.key AND
                (target.canonical_id IS NULL OR alternative.canonical_id IS NULL OR alternative.canonical_id!=target.canonical_id))))
      ))""")


def current_decision():
    return literal_column(f"""NOT EXISTS (SELECT 1 FROM ml_evidence WHERE ml_evidence.finding_key=ml_findings.key
      AND json_type(ml_evidence.features,'$.identity_routes')='array' AND NOT ({valid_routes()}))""")


def route(db, spelling):
    """Select one exact retained witness; candidates can veto, never prove it."""
    from . import effective

    if definitions_pending(db):
        return None
    definitions = db.query(Finding).filter(Finding.kind == "alias_definition",
        func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling,
        effective.supported()).order_by(Finding.key).limit(33).all()
    if not definitions or len(definitions) > 32:
        return None
    targets = [db.get(Finding, json.loads(row.payload)["concept_key"]) for row in definitions]
    if any(target is None for target in targets):
        return None
    generation = db.execute(text("SELECT pipeline_version FROM ml_state WHERE id=1")).scalar_one()
    for definition, target in zip(definitions, targets):
        if any(other.key != target.key and (not target.canonical_id or other.canonical_id != target.canonical_id)
               for other in targets):
            continue
        short = db.query(ConceptTerm).filter_by(term=spelling, is_canonical=True).first()
        if short and short.concept_id != target.canonical_id:
            continue
        name = json.loads(target.payload)["name"]
        keys = [target.key, finding_key("term", spelling), finding_key("term", normalize(name))]
        if target.canonical_id:
            keys.append(finding_key("alias", spelling, target.canonical_id))
        if db.query(Override).filter(Override.key.in_(keys), Override.mode == "suppressed").first():
            continue
        for evidence in sorted(effective.evidence_rows(db, definition.key), key=lambda e: e["key"]):
            if generation != evidence["model_version"] + ":" + policy.VERSION:
                continue
            source = db.get(Source, (evidence["source_kind"], evidence["source_id"]))
            if source.model_version != evidence["model_version"]:
                continue
            for method in sorted(policy.single_alias_definitions([evidence]), key=lambda m: json.dumps(m, sort_keys=True)):
                if (normalize(method["short_name"]["text"]) != spelling
                        or normalize(method["full_name"]["text"]) != normalize(json.loads(definition.payload)["name"])
                        or method.get("source_text_hash") != json.loads(source.result).get("text_hash")):
                    continue
                return {"alias_key": spelling, "concept_key": target.key,
                    "definition_evidence_key": evidence["key"],
                    "witness_source_kind": source.kind, "witness_source_id": source.id,
                    "witness_source_hash": source.content_hash, "method_fingerprint": digest(method),
                    "method_json": json.dumps(method, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "model_version": evidence["model_version"], "routing_policy": VERSION,
                    "term_key": keys[1], "full_term_key": keys[2]}
    return None


def reconsider(db, spellings):
    """Remember effective eligibility so identical replay cannot restart backfill."""
    from .queue import request_backfill

    affected = set()
    changed = False
    for spelling in sorted(set(spellings)):
        holder = db.query(Finding).filter(Finding.kind == "alias_definition",
            func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling).order_by(Finding.key).first()
        if holder is None:
            continue
        certificate = route(db, spelling)
        signature = digest(certificate) if certificate else None
        payload = json.loads(holder.payload)
        if payload.get("routing_signature") == signature:
            continue
        payload["routing_signature"] = signature
        holder.payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        dependent = db.execute(text("""SELECT DISTINCT e.finding_key FROM ml_evidence e,
          json_each(e.features,'$.identity_routes') route
          WHERE json_type(e.features,'$.identity_routes')='array'
            AND json_extract(route.value,'$.alias_key')=:spelling"""), {"spelling": spelling}).scalars().all()
        if dependent:
            db.query(Finding).filter(Finding.key.in_(dependent)).update({Finding.state: "stale"}, synchronize_session="fetch")
            affected.update(dependent)
            authors = {profile for profile, in db.query(Source.id).join(Profile, Profile.id == Source.id)
                .join(Account, Account.id == Profile.account_id).filter(Source.kind == "profile")}
            if authors:
                from .queue import enqueue
                db.query(Source).filter(Source.kind == "profile", Source.id.in_(authors)).update(
                    {Source.valid: False}, synchronize_session="fetch")
                for author in authors:
                    enqueue(db, "profile", author)
        changed = True
    if changed:
        request_backfill(db)
    return affected
