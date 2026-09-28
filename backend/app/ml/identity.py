"""Exact scored alias witnesses and their conditional evidence provenance."""

import json
from functools import lru_cache

from sqlalchemy import and_, exists, func, literal_column, or_, select, text

from ..models import Account, ConceptTerm, Profile
from . import policy, syntax
from .models import Evidence, Finding, Override, Source
from .runtime import named_part, normalize
from .sources import digest, finding_key

VERSION = "exact-scored-identity-v2"
TERM_VERSION = "published-term-dependency-v1"


def scope_contract():
    from .runtime import inference_version

    models = {role: {"revision": "alias-scope-contract"}
              for role in ("extractor", "embeddings", "syntax")}
    return digest(["full-source-alias-scope-v1", TERM_VERSION, inference_version(models)])


def term_route(db, spelling, concept_id):
    """Record an existing published mapping, never establish an equivalence."""
    term = db.query(ConceptTerm).filter_by(term=normalize(spelling), concept_id=concept_id, is_canonical=False).first()
    alias = db.query(Finding).filter_by(kind="alias", canonical_id=term.id).first() if term else None
    if alias is None:
        return None
    return {"version": TERM_VERSION, "alias_key": alias.key, "surface": term.term, "concept_id": concept_id}


@lru_cache(maxsize=16)
def _term_authority_query(contract, policy_version, routing_version, scope_guard, route_guard):
    # Cache query structure only. Every statement reads current database data.
    supported = exists(select(Evidence.key).join(Source, and_(
        Source.kind == Evidence.source_kind, Source.id == Evidence.source_id,
        Source.content_hash == Evidence.source_hash, Source.valid.is_(True))).where(
        Evidence.finding_key == Finding.key, current_scope_evidence()).correlate(Finding))
    return (select(Finding.key.label("alias_key"), ConceptTerm.term.label("surface"), ConceptTerm.concept_id, ConceptTerm.id.label("term_id"))
        .join(ConceptTerm, ConceptTerm.id == Finding.canonical_id)
        .outerjoin(Override, Override.key == Finding.key)
        .where(Finding.kind == "alias", ConceptTerm.is_canonical.is_(False),
            func.json_extract(Finding.payload, "$.alias_key") == ConceptTerm.term,
            func.json_extract(Finding.payload, "$.concept_id") == ConceptTerm.concept_id,
            func.coalesce(Override.mode, "") != "suppressed",
            or_(Override.mode == "pinned", and_(Finding.state == "active", supported, scope_guard(), route_guard())))
        .cte("authoritative_aliases").prefix_with("NOT MATERIALIZED"))


def term_authorities():
    return _term_authority_query(scope_contract(), policy.VERSION, VERSION, current_scope_decision, current_decision)


def _term_mapping(route):
    authority = term_authorities()
    return exists(select(authority.c.alias_key).where(
        authority.c.alias_key == func.json_extract(route.c.value, "$.alias_key"),
        authority.c.surface == func.json_extract(route.c.value, "$.surface"),
        authority.c.concept_id == func.json_extract(route.c.value, "$.concept_id"),
        func.json_extract(route.c.value, "$.version") == TERM_VERSION).correlate(route))


@lru_cache(maxsize=16)
def _term_evidence_query(authority, contract, policy_version):
    return select(Evidence.key).where(_valid_term_routes()).cte(
        "current_term_evidence").prefix_with("NOT MATERIALIZED")


def valid_term_routes():
    current = _term_evidence_query(term_authorities(), scope_contract(), policy.VERSION)
    return exists(select(current.c.key).where(current.c.key == Evidence.key).correlate(Evidence))


def _valid_term_routes():
    routes = func.json_each(Evidence.features, "$.term_routes").table_valued("key", "value").alias("term_route")
    owner = exists(select(Source.id).where(Source.kind == Evidence.source_kind, Source.id == Evidence.source_id,
        Source.valid.is_(True), Source.content_hash == Evidence.source_hash, current_scope_evidence())
        .correlate(Evidence))
    return ~exists(select(routes.c.key).where(
        ~func.coalesce(and_(_term_mapping(routes), owner), False)).correlate(Evidence))


def current_scope_evidence():
    from .runtime import inference_version

    models = {role: {"revision": "alias-scope-contract"}
              for role in ("extractor", "embeddings", "syntax")}
    suffix = ":" + ":".join(inference_version(models).split(":")[-2:])
    return and_(
        func.json_extract(Source.result, "$.alias_scope_contract") == scope_contract(),
        Source.model_version == Evidence.model_version,
        Evidence.model_version + ":" + policy.VERSION == literal_column(
            "(SELECT pipeline_version FROM ml_state WHERE id=1)"),
        func.length(Evidence.model_version) - func.length(func.replace(Evidence.model_version, ":", "")) == 4,
        func.substr(Evidence.model_version, -len(suffix)) == suffix)


def current_scope_decision():
    # A stored alias score must not survive loss of any contributing source's
    # inference contract. Explicit admin pins bypass automatic decisions.
    valid = exists(select(Source.id).where(
        Source.kind == Evidence.source_kind, Source.id == Evidence.source_id,
        Source.valid.is_(True), Source.content_hash == Evidence.source_hash,
        current_scope_evidence()).correlate(Evidence))
    return ~exists(select(Evidence.key).where(
        Evidence.finding_key == Finding.key, ~valid).correlate(Finding))


def coverage_current(result):
    return (result.get("definitions_indexed", 0) >= 2
            and result.get("alias_scope_contract") == scope_contract()
            and result.get("conflict_coverage_revision") == syntax.CONFLICT_REVISION
            and isinstance(result.get("conflict_definitions"), list))


def incomplete_coverage(source):
    # Internal table aliases only. Keep publication and stored-route reads at
    # the same generation boundary as cache reuse and replay completion.
    return f"""(COALESCE(json_extract({source}.result,'$.definitions_indexed'),0)<2
      OR COALESCE(json_extract({source}.result,'$.alias_scope_contract'),'')!='{scope_contract()}'
      OR COALESCE(json_extract({source}.result,'$.conflict_coverage_revision'),'')!='{syntax.CONFLICT_REVISION}'
      OR COALESCE(json_type({source}.result,'$.conflict_definitions'),'')!='array')"""


def definitions_pending(db):
    return db.execute(text(f"""SELECT 1 FROM ml_sources WHERE kind IN ('item','passage')
      AND valid=1 AND {incomplete_coverage('ml_sources')} LIMIT 1""")).first() is not None


@lru_cache(maxsize=16)
def _route_evidence_query(contract, policy_version, routing_version, syntax_version):
    return select(Evidence.key).where(_valid_routes()).cte("current_identity_evidence").prefix_with("NOT MATERIALIZED")


def valid_routes():
    current = _route_evidence_query(scope_contract(), policy.VERSION, VERSION, syntax.REVISION)
    return exists(select(current.c.key).where(current.c.key == Evidence.key).correlate(Evidence))


def _valid_routes():
    # The same guard is used for scores and stored decisions. A stale maximum
    # cannot remain public merely because weaker direct evidence still exists.
    # A new pin also authorizes older automatic inverse routes that lack a
    # pinned_alias_key; their exact spelling, declaration and owner still must
    # match the current pinned alias below.
    return literal_column(f"""NOT EXISTS (
      SELECT 1 FROM json_each(ml_evidence.features,'$.identity_routes') route
      WHERE NOT EXISTS (
        SELECT 1 FROM ml_evidence witness
        JOIN ml_sources source ON source.kind=witness.source_kind AND source.id=witness.source_id
        JOIN ml_findings definition ON definition.key=witness.finding_key AND definition.kind='alias_definition'
        JOIN ml_findings target ON target.key=json_extract(route.value,'$.concept_key') AND target.kind='concept'
        JOIN ml_findings declaration ON declaration.key=json_extract(route.value,'$.declaration_key') AND declaration.kind='concept'
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
          AND json_extract(definition.payload,'$.concept_key')=declaration.key
          AND (json_extract(route.value,'$.direction')='forward' AND declaration.key=target.key
            OR json_extract(route.value,'$.direction')='inverse' AND declaration.canonical_id IS NULL
              AND target.canonical_id=json_extract(route.value,'$.canonical_id') AND target.state='active'
              AND EXISTS (SELECT 1 FROM concept_terms native WHERE native.is_canonical=1
                AND native.term=json_extract(route.value,'$.alias_key') AND native.concept_id=target.canonical_id)
              AND json_array_length(route.value,'$.anchors')>0
              AND NOT EXISTS (SELECT 1 FROM json_each(route.value,'$.anchors') anchor WHERE NOT EXISTS (
                SELECT 1 FROM ml_evidence native JOIN ml_sources native_source
                  ON native_source.kind=native.source_kind AND native_source.id=native.source_id
                WHERE native.key=json_extract(anchor.value,'$.key') AND native.finding_key=target.key
                  AND native.raw_score=json_extract(anchor.value,'$.raw_score')
                  AND native.source_kind=json_extract(anchor.value,'$.source_kind')
                  AND native.source_id=json_extract(anchor.value,'$.source_id')
                  AND native.source_hash=json_extract(anchor.value,'$.source_hash')
                  AND native_source.valid=1 AND native_source.content_hash=native.source_hash
                  AND native_source.model_version=native.model_version
                  AND native.model_version=witness.model_version
                  AND json_type(native.features,'$.identity_routes') IS NULL
                  AND json_type(native.features,'$.term_routes') IS NULL)))
          AND EXISTS (SELECT 1 FROM json_each(witness.features,'$.definition_methods') method
            WHERE method.value=json_extract(route.value,'$.method_json')
              AND json_extract(method.value,'$.syntax_rule_revision')='{syntax.REVISION}'
              AND json_extract(method.value,'$.source_text_hash')=json_extract(source.result,'$.text_hash'))
          AND EXISTS (SELECT 1 FROM ml_sources owner WHERE owner.kind=ml_evidence.source_kind
            AND owner.id=ml_evidence.source_id AND owner.valid=1 AND owner.content_hash=ml_evidence.source_hash
            AND owner.model_version=ml_evidence.model_version)
          AND NOT EXISTS (SELECT 1 FROM ml_sources pending WHERE pending.kind IN ('item','passage')
            AND pending.valid=1 AND {incomplete_coverage('pending')})
          AND NOT EXISTS (SELECT 1 FROM concept_terms short WHERE short.term=json_extract(route.value,'$.alias_key')
            AND short.is_canonical=1 AND (target.canonical_id IS NULL OR short.concept_id!=target.canonical_id))
          AND NOT EXISTS (SELECT 1 FROM ml_overrides fixed WHERE fixed.mode='suppressed' AND
            (fixed.key=target.key OR fixed.key=declaration.key OR fixed.key=json_extract(route.value,'$.term_key')
             OR fixed.key=json_extract(route.value,'$.full_term_key')
             OR (fixed.kind='concept' AND json_extract(fixed.payload,'$.name') IN
                 (json_extract(target.payload,'$.name'),json_extract(declaration.payload,'$.name')))
             OR (fixed.kind='alias' AND json_extract(fixed.payload,'$.alias_key')=json_extract(route.value,'$.published_alias')
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
              AND (alternative.key IS NULL OR (alternative.key!=declaration.key AND
                (declaration.canonical_id IS NULL OR alternative.canonical_id IS NULL OR alternative.canonical_id!=declaration.canonical_id))))
          AND NOT EXISTS (SELECT 1 FROM concept_terms claimed
            WHERE claimed.term=json_extract(route.value,'$.published_alias')
              AND target.canonical_id IS NOT NULL AND claimed.concept_id!=target.canonical_id)
          AND NOT EXISTS (SELECT 1 FROM ml_findings claimed WHERE claimed.kind='alias'
            AND json_extract(claimed.payload,'$.alias_key')=json_extract(route.value,'$.published_alias')
            AND json_extract(claimed.payload,'$.concept_id')!=target.canonical_id
            AND EXISTS (SELECT 1 FROM ml_evidence e JOIN ml_sources s ON s.kind=e.source_kind AND s.id=e.source_id
              AND s.valid=1 AND s.content_hash=e.source_hash WHERE e.finding_key=claimed.key))
      ) AND NOT EXISTS (
        SELECT 1 FROM ml_findings alias
        JOIN ml_overrides fixed ON fixed.key=alias.key AND fixed.mode='pinned'
        JOIN concept_terms term ON term.id=alias.canonical_id
        JOIN ml_findings target ON target.key=json_extract(route.value,'$.concept_key') AND target.kind='concept'
        JOIN ml_sources owner ON owner.kind=ml_evidence.source_kind AND owner.id=ml_evidence.source_id
        JOIN ml_state state ON state.id=1
        WHERE alias.kind='alias'
          AND (json_extract(route.value,'$.pinned_alias_key') IS NULL
            OR alias.key=json_extract(route.value,'$.pinned_alias_key'))
          AND json_extract(alias.payload,'$.direction')='inverse'
          AND json_extract(route.value,'$.direction')='inverse'
          AND json_extract(route.value,'$.routing_policy')='{VERSION}'
          AND json_extract(alias.payload,'$.alias_key')=json_extract(route.value,'$.published_alias')
          AND json_extract(alias.payload,'$.definition_alias_key')=json_extract(route.value,'$.alias_key')
          AND json_extract(alias.payload,'$.declaration_key')=json_extract(route.value,'$.declaration_key')
          AND term.term=json_extract(route.value,'$.published_alias') AND term.is_canonical=0
          AND term.concept_id=target.canonical_id AND target.canonical_id=json_extract(route.value,'$.canonical_id')
          AND json_extract(alias.payload,'$.concept_id')=target.canonical_id
          AND owner.valid=1 AND owner.content_hash=ml_evidence.source_hash
          AND owner.model_version=ml_evidence.model_version
          AND ml_evidence.model_version=json_extract(route.value,'$.model_version')
          AND state.pipeline_version=ml_evidence.model_version || ':{policy.VERSION}'
          AND NOT EXISTS (SELECT 1 FROM ml_overrides excluded WHERE excluded.mode='suppressed' AND (
            excluded.key=target.key OR excluded.key=json_extract(route.value,'$.declaration_key')
            OR excluded.key=json_extract(route.value,'$.term_key') OR excluded.key=json_extract(route.value,'$.full_term_key')
            OR (excluded.kind='mention' AND json_extract(excluded.payload,'$.source_kind')=ml_evidence.source_kind
                AND json_extract(excluded.payload,'$.source_id')=ml_evidence.source_id
                AND json_extract(excluded.payload,'$.concept_id')=target.canonical_id)))
      ))""")


def current_decision():
    return ~exists(select(Evidence.key).where(Evidence.finding_key == Finding.key,
        func.json_type(Evidence.features, "$.identity_routes") == "array",
        ~valid_routes()).correlate(Finding))


def route(db, spelling):
    """Select one exact retained witness; candidates can veto, never prove it."""
    from . import effective

    pinned = (db.query(Finding).join(Override, Override.key == Finding.key).join(
        ConceptTerm, ConceptTerm.id == Finding.canonical_id).filter(Finding.kind == "alias", Override.mode == "pinned",
        ConceptTerm.term == spelling, ConceptTerm.is_canonical.is_(False),
        func.json_extract(Finding.payload, "$.direction") == "inverse").first())
    if pinned:
        payload = json.loads(pinned.payload)
        target = db.query(Finding).filter_by(kind="concept", canonical_id=payload["concept_id"]).first()
        if target and effective.concepts(db).filter_by(id=target.canonical_id).first():
            keys = [target.key, payload["declaration_key"], finding_key("term", payload["definition_alias_key"]),
                    finding_key("term", spelling)]
            if not db.query(Override).filter(Override.key.in_(keys), Override.mode == "suppressed").first():
                generation = db.execute(text("SELECT pipeline_version FROM ml_state WHERE id=1")).scalar_one()
                suffix = ":" + policy.VERSION
                if generation.endswith(suffix):
                    return {"direction": "inverse", "pinned_alias_key": pinned.key, "routing_policy": VERSION,
                        "alias_key": payload["definition_alias_key"], "published_alias": spelling,
                        "concept_key": target.key, "canonical_id": target.canonical_id,
                        "declaration_key": payload["declaration_key"], "term_key": keys[2], "full_term_key": keys[3],
                        "model_version": generation[:-len(suffix)]}
    if definitions_pending(db):
        return None
    lookup = spelling
    keys = {json.loads(row.payload)["alias_key"] for row in db.query(Finding).filter(
        Finding.kind == "alias_definition", or_(
            func.json_extract(Finding.payload, "$.alias_key") == spelling,
            func.json_extract(Finding.payload, "$.full_key") == spelling), effective.supported()).limit(33)}
    if len(keys) != 1:
        return None
    spelling = next(iter(keys))
    definitions = db.query(Finding).filter(Finding.kind == "alias_definition",
        func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling,
        effective.supported()).order_by(Finding.key).limit(33).all()
    if not definitions or len(definitions) > 32:
        return None
    targets = [db.get(Finding, json.loads(row.payload)["concept_key"]) for row in definitions]
    if any(target is None for target in targets):
        return None
    generation = db.execute(text("SELECT pipeline_version FROM ml_state WHERE id=1")).scalar_one()
    for definition, declaration in zip(definitions, targets):
        if any(other.key != declaration.key and (not declaration.canonical_id or other.canonical_id != declaration.canonical_id)
               for other in targets):
            continue
        target, direction, anchors = declaration, "forward", []
        short = db.query(ConceptTerm).filter_by(term=spelling, is_canonical=True).first()
        if short and short.concept_id != declaration.canonical_id:
            if declaration.canonical_id:
                continue
            target = db.query(Finding).filter_by(kind="concept", canonical_id=short.concept_id).first()
            if target is None or target.state != "active":
                continue
            native = []
            for evidence in effective.evidence_rows(db, target.key):
                source = db.get(Source, (evidence["source_kind"], evidence["source_id"]))
                if (evidence.get("identity_routes") or evidence.get("term_routes") or generation != evidence["model_version"] + ":" + policy.VERSION
                        or source.model_version != evidence["model_version"]):
                    continue
                if any(normalize(span["name"]) == spelling and span["label"] != "relation endpoint"
                       and (span["start"], span["end"], span["score"]) ==
                           (evidence["start"], evidence["end"], evidence["raw_score"])
                       for span in map(named_part, json.loads(source.result).get("concepts", []))):
                    native.append(evidence)
            if policy.decide("concept", native)[0] != "active":
                continue
            anchors = [{key: evidence[key] for key in ("key", "source_kind", "source_id", "source_hash", "raw_score")}
                       for evidence in sorted(native, key=lambda e: e["key"])]
            direction = "inverse"
        name = json.loads(declaration.payload)["name"]
        published = normalize(name) if direction == "inverse" else spelling
        if lookup != spelling and (direction != "inverse" or lookup != published):
            continue
        claimed = db.query(ConceptTerm).filter_by(term=published).first()
        if claimed and target.canonical_id and claimed.concept_id != target.canonical_id:
            continue
        keys = [target.key, finding_key("term", spelling), finding_key("term", normalize(name)), declaration.key]
        if target.canonical_id:
            keys.append(finding_key("alias", published, target.canonical_id))
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
                    "declaration_key": declaration.key, "direction": direction, "published_alias": published,
                    **({"canonical_id": target.canonical_id, "anchors": anchors} if direction == "inverse" else {}),
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
        mapped = {json.loads(row.payload)["alias_key"] for row in db.query(Finding).filter(
            Finding.kind == "alias_definition", or_(
                func.json_extract(Finding.payload, "$.alias_key") == spelling,
                func.json_extract(Finding.payload, "$.full_key") == spelling))}
        if mapped and spelling not in mapped:
            if len(mapped) != 1:
                continue
            spelling = next(iter(mapped))
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
