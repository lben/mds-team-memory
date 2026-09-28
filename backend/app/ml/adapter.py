"""Apply derived findings through the app's existing canonical records.

The caller owns a short BEGIN IMMEDIATE transaction and validates its job,
source snapshot, and model generation before calling this module.
"""

import json
import re

from sqlalchemy import func, or_, text

from ..models import (RELATED_TO_ID, Account, Concept, ConceptTerm, ExpertiseMapping,
                      ItemConcept, KnowledgeItem, Profile, Relationship,
                      RelationshipType, utcnow)
from . import effective, embeddings, identity, policy, relation_syntax, resolution, syntax
from .models import Embedding, Evidence, Finding, Override, Source
from .queue import enqueue, request_backfill
from .runtime import grounded_span, normalize, specific_name
from .sources import digest, finding_key, snapshot


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _finding(db, kind, identity, payload):
    key = finding_key(kind, *identity)
    row = db.get(Finding, key)
    if row is None:
        row = Finding(key=key, kind=kind, payload=_json(payload), state="held", score=0.0,
                      calibrated=False, policy_version=policy.VERSION, created_at=utcnow(), updated_at=utcnow())
        db.add(row)
        db.flush()
    return row


def _override(db, row, mode, username, payload=None):
    entry = db.get(Override, row.key)
    payload = dict(payload if payload is not None else json.loads(row.payload))
    if row.kind == "expertise" and mode == "pinned":
        profile = db.get(Profile, payload["profile_id"])
        payload.update(account_id=profile.account_id if profile else None,
                       account_binding_revision=profile.account_binding_revision if profile else None)
    values = dict(kind=row.kind, mode=mode, username=username,
                  payload=_json(payload), updated_at=utcnow())
    if entry is None:
        db.add(Override(key=row.key, **values))
    else:
        for name, value in values.items():
            setattr(entry, name, value)
    db.flush()


def _evidence(db, row, source, start, end, score, version, polarity="positive", **features):
    key = finding_key("evidence", row.key, source.kind, source.id, polarity)
    if row.kind in {"concept", "mention", "relationship"} and any(features.get(name) for name in ("identity_routes", "term_routes")):
        # Independent identity witnesses can expire separately. Keep the best
        # evidence for each complete dependency set rather than letting one
        # route erase another route (or an unconditional literal assertion).
        dependencies = {}
        for name in ("identity_routes", "term_routes"):
            if features.get(name):
                routes = sorted({_json(route) for route in features[name]})
                features[name] = [json.loads(route) for route in routes]
                dependencies[name] = routes
        key = finding_key("evidence", row.key, source.kind, source.id, polarity, "dependencies", dependencies)
    prior = db.get(Evidence, key)
    if prior:
        previous_features = json.loads(prior.features)
        if row.kind in {"alias", "alias_definition"}:
            methods = previous_features.get("definition_methods", []) + features.get("definition_methods", [])
            methods = list({_json(method): method for method in methods}.values())
            if prior.raw_score >= score:
                prior.features = _json({**previous_features, "definition_methods": methods})
                return
            features["definition_methods"] = methods
        # Different spellings can resolve to one concept. Relation confidence
        # must not displace its entity evidence from the same source.
        prior_preferred = row.kind == "concept" and previous_features.get("label") != "relation endpoint"
        current_preferred = row.kind == "concept" and features.get("label") != "relation endpoint"
        if row.kind == "relationship":
            # Keep a literal assertion or contradiction before comparing scores.
            # Another mention of the same pair cannot erase it merely because
            # an unsupported extraction has a higher raw model score.
            prior_preferred = bool(previous_features.get("literal_support") and previous_features.get("assertion_allowed"))
            current_preferred = bool(features.get("literal_support") and features.get("assertion_allowed"))
        if (prior_preferred, prior.raw_score) >= (current_preferred, score):
            return
    values = dict(finding_key=row.key, source_kind=source.kind, source_id=source.id,
                  source_hash=source.content_hash, group_key=source.group_key, author_id=source.author_id,
                  start=start, end=end, raw_score=float(score), polarity=polarity,
                  features=_json({"text_hash": source.text_hash, "locator": source.locator,
                                  "origin": source.origin, **features}), model_version=version)
    db.merge(Evidence(key=key, **values))
    db.flush()


def _decide(db, row):
    previous = row.state
    row.state, row.score = policy.decide(row.kind, effective.evidence_rows(db, row.key))
    row.calibrated = False
    row.policy_version, row.updated_at = policy.VERSION, utcnow()
    fixed = db.get(Override, row.key)
    if fixed:
        row.state = "active" if fixed.mode == "pinned" else "suppressed"
    return previous != row.state


def _concept(db, name):
    # An inverse alias projects a name, but never adopts its declaration.
    inverse = db.query(Finding).filter(Finding.kind == "alias",
        func.json_extract(Finding.payload, "$.alias_key") == normalize(name),
        func.json_extract(Finding.payload, "$.direction") == "inverse").first()
    if inverse:
        declaration = db.get(Finding, json.loads(inverse.payload)["declaration_key"])
        if declaration:
            return declaration
    term = db.query(ConceptTerm).filter_by(term=normalize(name)).first()
    match = term or resolution.spelling_match(db, name)
    if isinstance(match, Finding):
        return match
    concept = db.get(Concept, match.concept_id) if match else None
    if concept:
        existing = db.query(Finding).filter_by(kind="concept", canonical_id=concept.id).first()
        if existing:
            return existing
    display = concept.name if concept else name
    row = _finding(db, "concept", [normalize(display)], {"name": display})
    if concept:
        row.canonical_id = concept.id
    return row


def pin_concept(db, concept, username="legacy", previous_terms=()):
    row = db.query(Finding).filter_by(kind="concept", canonical_id=concept.id).first()
    if row is None:
        row = _finding(db, "concept", [normalize(concept.name)], {"name": concept.name})
    row.payload = _json({"name": concept.name})
    row.canonical_id, row.state = concept.id, "active"
    _override(db, row, "pinned", username)
    # Pin an existing automatic identity as well when its canonical name changed.
    for old in db.query(Finding).filter_by(kind="concept", canonical_id=concept.id):
        _override(db, old, "pinned", username, {"name": concept.name})
        old.payload = _json({"name": concept.name})
    present = {term.term for term in concept.terms}
    for old in previous_terms:
        if old not in present:
            removed = _finding(db, "term", [old], {"term": old, "concept_id": concept.id})
            _override(db, removed, "suppressed", username)
    for term in concept.terms:
        excluded = db.get(Finding, finding_key("term", term.term))
        if excluded:
            _override(db, excluded, "pinned", username)
        if not term.is_canonical:
            alias = _finding(db, "alias", [term.term, concept.id], {"alias": term.display, "alias_key": term.term, "concept_id": concept.id})
            alias.canonical_id, alias.state = term.id, "active"
            _override(db, alias, "pinned", username)
    request_backfill(db)
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def suppress_concept(db, concept, username="admin"):
    row = _concept(db, concept.name)
    _override(db, row, "suppressed", username)
    row.state = "suppressed"
    for term in concept.terms:
        excluded = _finding(db, "term", [term.term], {"term": term.term, "concept_id": concept.id})
        _override(db, excluded, "suppressed", username, {"term": term.term, "concept_id": concept.id, "concept_suppression": row.key})
    for owned in db.query(Finding).filter_by(kind="concept", canonical_id=concept.id):
        _override(db, owned, "suppressed", username)
        owned.state = "suppressed"
    request_backfill(db)
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def fix_relationship(db, link, mode, username="admin"):
    pair = sorted((link.src_id, link.dst_id))
    row = _finding(db, "relationship_pair", pair, {"src_id": link.src_id, "dst_id": link.dst_id,
                   "type_id": link.relationship_type_id, "note": link.review_note})
    row.payload = _json({"src_id": link.src_id, "dst_id": link.dst_id, "type_id": link.relationship_type_id, "note": link.review_note})
    row.canonical_id, row.state = link.id, "active" if mode == "pinned" else "suppressed"
    _override(db, row, mode, username)
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def fix_expertise(db, mapping, mode, username="admin"):
    row = _finding(db, "expertise", [mapping.profile_id, mapping.concept_id],
                   {"profile_id": mapping.profile_id, "concept_id": mapping.concept_id})
    row.canonical_id, row.state = mapping.id, "active" if mode == "pinned" else "suppressed"
    _override(db, row, mode, username)
    enqueue(db, "profile", mapping.profile_id)
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def fix_predicate(db, rtype, previous_name, mode, username):
    key = finding_key("predicate", normalize(previous_name))
    entry = db.get(Override, key)
    if entry is None:
        entry = Override(key=key, kind="predicate", mode=mode, payload="{}", username=username, updated_at=utcnow())
        db.add(entry)
    entry.mode, entry.payload = mode, _json({"type_id": rtype.id, "name": rtype.name})
    entry.username, entry.updated_at = username, utcnow()
    if mode == "suppressed":
        for other in db.query(Override).filter(Override.kind == "predicate",
                func.json_extract(Override.payload, "$.type_id") == rtype.id):
            other.mode, other.username, other.updated_at = mode, username, utcnow()
    db.flush()
    enqueue(db, "vocabulary", "all")
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def bootstrap(db):
    """Capture pre-existing manual authority once, including rejected links."""
    marker = finding_key("bootstrap", "legacy-v1")
    if db.get(Override, marker):
        return
    for concept in db.query(Concept).all():
        pin_concept(db, concept)
    for mapping in db.query(ExpertiseMapping).all():
        fix_expertise(db, mapping, "pinned", "legacy")
    for link in db.query(Relationship).filter_by(src_kind="concept", dst_kind="concept"):
        if link.state == "rejected" or link.reviewed_by or link.state == "confirmed":
            fix_relationship(db, link, "suppressed" if link.state == "rejected" else "pinned", "legacy")
    db.add(Override(key=marker, kind="bootstrap", mode="pinned", payload="{}", username="migration", updated_at=utcnow()))
    db.execute(text("UPDATE ml_state SET automation_enabled=1 WHERE id=1"))
    db.flush()


def _publish_concept(db, row):
    if row.state != "active":
        return False
    payload = json.loads(row.payload)
    if not row.canonical_id:
        certificate = identity.route(db, normalize(payload["name"]))
        if certificate and certificate["direction"] == "inverse":
            return False
        term = db.query(ConceptTerm).filter_by(term=normalize(payload["name"])).first()
        inverse = (db.query(Finding).filter(Finding.kind == "alias", Finding.canonical_id == term.id,
                   func.json_extract(Finding.payload, "$.direction") == "inverse").first() if term else None)
        if inverse:
            return False
    blocked = db.get(Override, finding_key("term", normalize(payload["name"])))
    if blocked and blocked.mode == "suppressed":
        row.state = "suppressed"
        return False
    concept = db.get(Concept, row.canonical_id) if row.canonical_id else None
    if concept is None:
        term = db.query(ConceptTerm).filter_by(term=normalize(payload["name"])).first()
        if term:
            row.canonical_id = term.concept_id
            return False
        concept = Concept(id=row.canonical_id) if row.canonical_id else Concept()
        db.add(concept)
        db.flush()
        db.add(ConceptTerm(concept_id=concept.id, term=normalize(payload["name"]),
                           display=payload["name"], is_canonical=True))
        row.canonical_id = concept.id
        db.flush()
        return True
    return False


def _definitions_pending(db):
    return identity.definitions_pending(db)


def _definition_conflict(db, spelling, concept_id):
    definitions = db.query(Finding).filter(Finding.kind == "alias_definition",
        func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling, effective.supported()).limit(33).all()
    if len(definitions) > 32:
        return True
    for definition in definitions:
        target = db.get(Finding, json.loads(definition.payload)["concept_key"])
        if target is None or target.canonical_id != concept_id:
            return True
    return False


def _publish_alias(db, row):
    changed = False
    payload = json.loads(row.payload)
    spelling = normalize(payload["alias"])
    routed = db.execute(text("""SELECT 1 FROM ml_evidence e, json_each(e.features,'$.identity_routes') route
      WHERE e.finding_key IN (SELECT key FROM ml_findings WHERE kind='concept' AND canonical_id=:cid)
        AND json_extract(route.value,'$.alias_key')=:spelling LIMIT 1"""),
        {"cid": payload["concept_id"], "spelling": spelling}).first()
    if payload.get("identity_routing") or routed:
        payload["identity_routing"] = True
        row.payload = _json(payload)
        if not db.get(Override, row.key):
            certificate = identity.route(db, payload.get("definition_alias_key", spelling))
            if certificate and certificate["published_alias"] != spelling:
                certificate = None
            if certificate is None:
                row.state = "held"
            else:
                for evidence in db.query(Evidence).filter_by(finding_key=row.key):
                    features = json.loads(evidence.features)
                    # An unchanged alias keeps its selected witness. Replaying
                    # its owning source is what may select a replacement.
                    if "identity_routes" not in features:
                        evidence.features = _json({**features, "identity_routes": [certificate]})
                db.flush()
    if row.state == "held" and not db.get(Override, row.key) and not _definitions_pending(db):
        version = db.execute(text("SELECT pipeline_version FROM ml_state WHERE id=1")).scalar_one()
        evidence = [e for e in effective.evidence_rows(db, row.key) if version == e["model_version"] + ":" + policy.VERSION]
        for method in policy.single_alias_definitions(evidence):
            inverse = payload.get("direction") == "inverse"
            if normalize(method["full_name" if inverse else "short_name"]["text"]) != spelling:
                continue
            name = method["short_name" if inverse else "full_name"]["text"]
            target = db.query(ConceptTerm).filter_by(term=normalize(name)).first() or resolution.spelling_match(db, name)
            target_id = target.canonical_id if isinstance(target, Finding) else target.concept_id if target else None
            if target_id != payload["concept_id"]:
                continue
            targets = db.query(Finding).filter_by(kind="concept", canonical_id=target_id)
            if inverse:
                certificate = identity.route(db, payload["definition_alias_key"])
                qualified = certificate and certificate["direction"] == "inverse" and certificate["canonical_id"] == target_id
            else:
                qualified = any(policy.decide("concept", effective.evidence_rows(db, target.key))[0] == "active" for target in targets)
            if qualified:
                row.state = "active"
                break
    blocked = db.get(Override, finding_key("term", spelling))
    if blocked and blocked.mode == "suppressed":
        row.state = "suppressed"
    conflicting = [other for other in db.query(Finding).filter(Finding.kind == "alias",
                   func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling, Finding.key != row.key)
                   if normalize(json.loads(other.payload)["alias"]) == spelling
                   and json.loads(other.payload)["concept_id"] != payload["concept_id"]
                   and effective.evidence_rows(db, other.key)]
    definition_conflict = _definition_conflict(db, spelling, payload["concept_id"])
    if (conflicting or definition_conflict) and row.state in {"active", "held"} and not db.get(Override, row.key):
        row.state = "held"
        for other in conflicting:
            if not db.get(Override, other.key):
                other.state = "held"
                if other.canonical_id:
                    old_term = db.get(ConceptTerm, other.canonical_id)
                    if old_term and not old_term.is_canonical:
                        db.delete(old_term)
                        changed = True
                    other.canonical_id = None
    term = db.get(ConceptTerm, row.canonical_id) if row.canonical_id else None
    if row.state != "active":
        if term and not term.is_canonical:
            db.delete(term)
            row.canonical_id = None
            return True
        return changed
    if not effective.concepts(db).filter(Concept.id == payload["concept_id"]).first():
        return False
    existing = db.query(ConceptTerm).filter_by(term=spelling).first()
    if existing:
        if existing.concept_id == payload["concept_id"]:
            row.canonical_id = existing.id
        else:
            row.state = "held"
        return False
    term = ConceptTerm(concept_id=payload["concept_id"], term=spelling, display=payload["alias"], is_canonical=False)
    db.add(term)
    db.flush()
    row.canonical_id = term.id
    return True


def _project_relationships(db, concept_ids):
    from ..relationships import find_link
    affected = set(concept_ids)
    if not affected:
        return
    claims = db.query(Finding).filter(Finding.kind.in_(("relationship", "association")), or_(
        func.json_extract(Finding.payload, "$.src_id").in_(affected),
        func.json_extract(Finding.payload, "$.dst_id").in_(affected))).all()
    pairs = {}
    for row in claims:
        payload = json.loads(row.payload)
        if not affected.intersection((payload["src_id"], payload["dst_id"])):
            continue
        pairs.setdefault(tuple(sorted((payload["src_id"], payload["dst_id"]))), []).append(row)
    for pair, rows in pairs.items():
        fixed = db.get(Override, finding_key("relationship_pair", *pair))
        if fixed:
            continue
        live = {c.id for c in effective.concepts(db).filter(Concept.id.in_(pair))}
        viable = [r for r in rows if r.state in {"active", "held", "weak"}
                  and effective.predicate_name(db, json.loads(r.payload)["predicate"]) is not None
                  and effective.evidence_rows(db, r.key)] if len(live) == 2 else []
        link = find_link(db, *pair)
        if not viable:
            if link and not link.reviewed_by:
                link.state, link.occurrence_count = "rejected", 0
            continue
        active = sorted([r for r in viable if r.state == "active"], key=lambda r: (-r.score, r.key))
        winner = active[0] if active and (len(active) == 1 or active[0].score >= active[1].score + 0.10) else None
        src_id, dst_id, type_id = *pair, RELATED_TO_ID
        if winner:
            payload = json.loads(winner.payload)
            src_id, dst_id = payload["src_id"], payload["dst_id"]
            predicate = effective.predicate_name(db, payload["predicate"])
            rtype = db.query(RelationshipType).filter_by(name=predicate).first()
            if rtype is None:
                rtype = RelationshipType(name=predicate, is_builtin=False)
                db.add(rtype)
                db.flush()
            type_id = rtype.id
        if link is None:
            link = Relationship(src_kind="concept", src_id=src_id, dst_kind="concept", dst_id=dst_id,
                                relationship_type_id=type_id)
            db.add(link)
            db.flush()
        link.src_id, link.dst_id, link.relationship_type_id = src_id, dst_id, type_id
        link.state = "confirmed" if active else "suggested"
        support = [e for row in (active or viable) for e in effective.evidence_rows(db, row.key)]
        link.occurrence_count = policy.independent_support(support)[0]
        for row in rows:
            row.canonical_id = link.id


def apply_source(db, source_kind, source_id, source, result, model_version, embedding_version, dimensions):
    prior = db.query(Evidence).filter_by(source_kind=source_kind, source_id=source_id).all()
    affected = {row.finding_key for row in prior}
    authors = {row.author_id for row in prior if row.author_id}
    db.query(Evidence).filter_by(source_kind=source_kind, source_id=source_id).delete(synchronize_session="fetch")
    stored = db.get(Source, (source_kind, source_id))
    indexing_old_source = stored is not None and not identity.coverage_current(json.loads(stored.result))
    if source is None:
        embeddings.invalidate_source(db, source_kind, source_id)
        if stored:
            stored.valid = False
            stored.result = "{}"
    else:
        embeddings.replace_source(db, source, result["chunks"], embedding_version, dimensions)
        embedding_count = (len(result["chunks"]) if result["chunks"] is not None
                           else json.loads(stored.result)["embedding_count"])
        covered = (result.get("conflict_coverage_revision") == syntax.CONFLICT_REVISION
                   and isinstance(result.get("conflict_definitions"), list))
        cached = {"definitions_indexed": 2 if covered else 1, "text_hash": digest(source.text), "concepts": result["concepts"], "relations": result["relations"],
                  "alias_scope_contract": identity.scope_contract(),
                  "corroborated_definitions": result.get("corroborated_definitions", []),
                  "embedding_version": embedding_version, "dimensions": dimensions, "embedding_count": embedding_count}
        if "relation_guard_revision" in result:
            cached["relation_guard_revision"] = result["relation_guard_revision"]
        if "eligibility" in result:
            cached["eligibility"] = result["eligibility"]
        if covered:
            cached.update(conflict_definitions=result["conflict_definitions"],
                          conflict_coverage_revision=syntax.CONFLICT_REVISION)
        values = dict(content_hash=source.content_hash, valid=True, model_version=model_version,
                      result=_json(cached), updated_at=utcnow())
        if stored is None:
            db.add(Source(kind=source_kind, id=source_id, **values))
        else:
            for name, value in values.items():
                setattr(stored, name, value)
        if source.author_id:
            authors.add(source.author_id)
        concepts = {}
        spans = list(result["concepts"])
        eligibility = result.get("eligibility", {}).get("margins", {})
        scope = relation_syntax.SourceScope(source.text)
        definitions = [d for d in resolution.definitions(source.text, spans)
                       if scope.asserted(d["start"], d["end"])] if source.assertion_allowed else []
        for definition in definitions:
            spans.append({"name": definition["name"], "start": definition["name_start"],
                          "end": definition["name_end"], "score": definition["score"],
                          "label": "explicit definition"})
            concepts[normalize(definition["name"])] = _concept(db, definition["name"])
        # Role records route identity, but never add their scores to entity spans.
        for record in result.get("corroborated_definitions", []) if source.assertion_allowed else []:
            full = grounded_span(record.get("full_name"), source.text, 0)
            short = grounded_span(record.get("short_name"), source.text, 0)
            if (record.get("source_text_hash") != digest(source.text) or not full or not short
                    or not record.get("syntax_rules") or not specific_name(full["name"])
                    or not specific_name(short["name"]) or normalize(full["name"]) == normalize(short["name"])
                    or not (full["end"] <= short["start"] or short["end"] <= full["start"])):
                raise ValueError("Corroborated definition does not match its source")
            if not scope.asserted(min(full["start"], short["start"]), max(full["end"], short["end"])):
                continue
            definitions.append({"name": full["name"], "alias": short["name"],
                                "name_start": full["start"], "name_end": full["end"],
                                "alias_start": short["start"], "alias_end": short["end"],
                                "start": min(full["start"], short["start"]),
                                "end": max(full["end"], short["end"]),
                                "score": min(full["score"], short["score"]),
                                "method": {"origin": "syntax_and_alias_role_record", **record}})
            if normalize(full["name"]) not in concepts:
                concepts[normalize(full["name"])] = _concept(db, full["name"])
        for candidate in result.get("conflict_definitions", []) if covered and source.assertion_allowed else []:
            full = grounded_span({**candidate["full_name"], "confidence": 0.0}, source.text, 0)
            short = grounded_span({**candidate["short_name"], "confidence": 0.0}, source.text, 0)
            if (candidate.get("source_text_hash") != digest(source.text) or not candidate.get("rule")
                    or not full or not short or not specific_name(full["name"]) or not specific_name(short["name"])
                    or normalize(full["name"]) == normalize(short["name"])
                    or not (full["end"] <= short["start"] or short["end"] <= full["start"])):
                raise ValueError("Conflict definition does not match its source")
            if not scope.asserted(min(full["start"], short["start"]), max(full["end"], short["end"])):
                continue
            target = _concept(db, full["name"])
            payload = {"alias_key": normalize(short["name"]), "name": full["name"],
                       "full_key": normalize(full["name"]), "concept_key": target.key}
            record = _finding(db, "alias_definition", [normalize(short["name"]), normalize(full["name"])], payload)
            record.payload = _json({**json.loads(record.payload), **payload})
            _evidence(db, record, source, min(full["start"], short["start"]), max(full["end"], short["end"]),
                      0.0, model_version, conflict_only=True, rule=candidate["rule"])
            affected.add(record.key)
        for definition in definitions:
            target = concepts[normalize(definition["name"])]
            payload = {"alias_key": normalize(definition["alias"]), "name": definition["name"],
                       "full_key": normalize(definition["name"]), "concept_key": target.key}
            record = _finding(db, "alias_definition", [normalize(definition["alias"]), normalize(definition["name"])], payload)
            record.payload = _json({**json.loads(record.payload), **payload})
            _evidence(db, record, source, definition["start"], definition["end"], definition["score"], model_version,
                      assertion_allowed=source.assertion_allowed,
                      definition_methods=[definition["method"]] if definition.get("method") else [])
            affected.add(record.key)
        # Resolve definitions before an extracted abbreviation can create its
        # own identity. Distinct existing canonical records are never merged.
        aliases = []
        routes_by_spelling = {}
        terms_by_spelling = {}
        for span in spans:
            if not specific_name(span["name"]):
                continue
            spelling = normalize(span["name"])
            blocked = db.get(Override, finding_key("term", spelling))
            if blocked and blocked.mode == "suppressed":
                continue
            certificate = None
            targets = {normalize(d["name"]) for d in definitions if normalize(d["alias"]) == spelling}
            existing = db.query(ConceptTerm).filter_by(term=spelling).first()
            if len(targets) > 1:
                # Multiple expansions in this source cannot establish a single
                # source-wide identity for standalone occurrences.
                continue
            selected_local_definition = bool(targets) and not (existing and existing.is_canonical)
            if selected_local_definition:
                row = concepts[next(iter(targets))]
            else:
                matched = resolution.spelling_match(db, span["name"]) if not existing and not targets else None
                reserved = (not existing and not targets
                    and (matched is None or isinstance(matched, Finding) and not matched.canonical_id)
                    and db.query(Finding).filter(Finding.kind.in_(("alias", "alias_definition")),
                        func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling,
                        effective.supported()).first())
                conditional = None
                if existing and not existing.is_canonical:
                    conditional = db.query(Finding).filter_by(kind="alias", canonical_id=existing.id).first()
                elif not existing:
                    # The alias Finding survives term withdrawal. Its publication
                    # must never erase the identity dependency on later replay.
                    conditional = db.query(Finding).filter(Finding.kind == "alias",
                        func.json_extract(Finding.payload, text("'$.alias_key'")) == spelling,
                        func.json_extract(Finding.payload, "$.identity_routing") == True).first()
                fixed = db.get(Override, conditional.key) if conditional else None
                conditional_row = conditional
                conditional = (conditional and json.loads(conditional.payload).get("identity_routing")
                               and not (fixed and fixed.mode == "pinned"))
                if reserved or conditional:
                    if span not in result["concepts"] or span["label"] == "relation endpoint":
                        continue
                    certificate = identity.route(db, spelling)
                    if certificate is None:
                        if conditional and json.loads(conditional_row.payload).get("direction") == "inverse":
                            row = db.get(Finding, json.loads(conditional_row.payload)["declaration_key"])
                        else:
                            continue
                    else:
                        row = db.get(Finding, certificate["declaration_key"] if certificate["direction"] == "inverse"
                                     else certificate["concept_key"])
                        routes_by_spelling[spelling] = certificate
                else:
                    row = concepts.get(spelling) or _concept(db, span["name"])
            canonical_name = json.loads(row.payload)["name"]
            # A current local definition or an exact scored certificate supplies
            # this identity independently of the published alias. Depending on
            # that alias would temporarily hide its own defining evidence on
            # every cached replay and continually restart vocabulary backfill.
            term_dependency = (identity.term_route(db, spelling, row.canonical_id)
                               if row.canonical_id and not certificate and not selected_local_definition else None)
            if term_dependency:
                terms_by_spelling[spelling] = term_dependency
            if (spelling != normalize(canonical_name)
                    and source.assertion_allowed and scope.asserted(span["start"], span["end"])
                    and resolution.spelling_key(span["name"]) == resolution.spelling_key(canonical_name)):
                aliases.append({"name": canonical_name, "alias": span["name"], "start": span["start"],
                                "end": span["end"], "score": span["score"], "spelling_variant": True})
            _evidence(db, row, source, span["start"], span["end"], span["score"], model_version,
                      grounded=True, label=span["label"],
                      **({"eligibility_margin": eligibility[spelling]} if spelling in eligibility else {}),
                      **({"term_routes": [term_dependency]} if term_dependency else {}),
                      **({"identity_routes": [certificate]} if certificate and certificate["direction"] == "forward" else {}))
            affected.add(row.key)
            concepts[spelling] = row
            concepts.setdefault(normalize(canonical_name), row)
        db.flush()
        vocabulary_changed = False
        for row in {r.key: r for r in concepts.values()}.values():
            vocabulary_changed |= _decide(db, row)
            vocabulary_changed |= _publish_concept(db, row)
        db.flush()
        # Reverse publication changes only downstream identity. Direct entity
        # scores above remain on the unbound full-name declaration.
        for spelling in list(concepts):
            certificate = identity.route(db, spelling)
            if certificate and certificate["direction"] == "inverse" and certificate["published_alias"] == spelling:
                concepts[spelling] = db.get(Finding, certificate["concept_key"])
                routes_by_spelling[spelling] = certificate
        for span in spans:
            concept = concepts.get(normalize(span["name"]))
            if not concept or not concept.canonical_id:
                continue
            mention = _finding(db, "mention", [source_kind, source_id, concept.canonical_id],
                               {"source_kind": source_kind, "source_id": source_id, "concept_id": concept.canonical_id})
            mention.canonical_id = concept.canonical_id
            _evidence(db, mention, source, span["start"], span["end"], span["score"], model_version,
                      grounded=True,
                      **({"term_routes": [terms_by_spelling[normalize(span["name"])]]}
                         if normalize(span["name"]) in terms_by_spelling else {}),
                      **({"identity_routes": [routes_by_spelling[normalize(span["name"])]], "label": span["label"]}
                         if normalize(span["name"]) in routes_by_spelling else {}))
            affected.add(mention.key)
        # Exact vocabulary tagging also depends on a conditional alias, even
        # when the entity pass returns no span. It supplies no entity score.
        conditional_terms = effective.terms(db).filter(ConceptTerm.id.in_(db.query(Finding.canonical_id).filter(
            Finding.kind == "alias", func.json_extract(Finding.payload, "$.identity_routing") == True)))
        for term in conditional_terms:
            alias = db.query(Finding).filter_by(kind="alias", canonical_id=term.id).first()
            if db.get(Override, alias.key):
                continue
            match = re.search(r"(?<!\w)" + re.escape(term.term) + r"(?!\w)", source.text.lower())
            certificate = identity.route(db, term.term) if match else None
            if certificate:
                mention = _finding(db, "mention", [source_kind, source_id, term.concept_id],
                    {"source_kind": source_kind, "source_id": source_id, "concept_id": term.concept_id})
                mention.canonical_id = term.concept_id
                _evidence(db, mention, source, match.start(), match.end(), 0.0, model_version,
                          grounded=True, label="term match", identity_routes=[certificate])
                affected.add(mention.key)
        for definition in definitions + aliases:
            concept = concepts.get(normalize(definition["name"]))
            certificate = identity.route(db, normalize(definition["alias"])) if definition.get("method") else None
            inverse = certificate and certificate["direction"] == "inverse"
            if inverse:
                concept = db.get(Finding, certificate["concept_key"])
            if concept and concept.canonical_id:
                name = definition["name"] if inverse else definition["alias"]
                alias = _finding(db, "alias", [normalize(name), concept.canonical_id],
                                 {"alias": name, "alias_key": normalize(name), "concept_id": concept.canonical_id,
                                  **({"identity_routing": True, "direction": "inverse",
                                      "definition_alias_key": certificate["alias_key"],
                                      "declaration_key": certificate["declaration_key"]} if inverse else {})})
                _evidence(db, alias, source, definition["start"], definition["end"], definition["score"], model_version,
                          explicit_definition=not definition.get("spelling_variant", False),
                          spelling_variant=definition.get("spelling_variant", False), assertion_allowed=source.assertion_allowed,
                          definition_methods=[definition.get("method", {
                              "origin": "genuine_spelling_variant" if definition.get("spelling_variant") else "legacy_entity_definition",
                              "score": definition["score"]})],
                          **({"identity_routes": [certificate]} if inverse else {}))
                affected.add(alias.key)
        for relation in result["relations"]:
            # The relation extractor can identify a known endpoint that the
            # separate entity pass missed. Exact vocabulary identity is enough
            # to attach its grounded relationship evidence to that concept.
            for part in ("head", "tail"):
                name = relation[part]["name"]
                if normalize(name) not in concepts:
                    term = effective.terms(db).filter(ConceptTerm.term == normalize(name)).first()
                    if term:
                        concepts[normalize(name)] = _concept(db, name)
                        dependency = identity.term_route(db, normalize(name), term.concept_id)
                        if dependency:
                            terms_by_spelling[normalize(name)] = dependency
                        alias = db.query(Finding).filter_by(kind="alias", canonical_id=term.id).first()
                        route_payload = json.loads(alias.payload) if alias else {}
                        if route_payload.get("identity_routing") and (
                                route_payload.get("direction") == "inverse" or not db.get(Override, alias.key)):
                            certificate = identity.route(db, normalize(name))
                            if certificate:
                                routes_by_spelling[normalize(name)] = certificate
                                if certificate["direction"] == "inverse":
                                    concepts[normalize(name)] = db.get(Finding, certificate["concept_key"])
            head, tail = (concepts.get(normalize(relation[part]["name"])) for part in ("head", "tail"))
            if not head or not tail or not head.canonical_id or not tail.canonical_id or head.canonical_id == tail.canonical_id:
                continue
            row = _finding(db, "relationship", [head.canonical_id, relation["predicate"], tail.canonical_id],
                           {"src_id": head.canonical_id, "dst_id": tail.canonical_id, "predicate": relation["predicate"]})
            term_dependencies = [terms_by_spelling[normalize(relation[part]["name"])]
                                 for part in ("head", "tail") if normalize(relation[part]["name"]) in terms_by_spelling]
            _evidence(db, row, source, relation["start"], relation["end"], relation["score"], model_version,
                      polarity=relation["polarity"], literal_support=relation["literal_support"],
                      relation_guard_revision=relation.get("relation_guard_revision"),
                      assertion_allowed=source.assertion_allowed, head=relation["head"], tail=relation["tail"],
                      **({"term_routes": term_dependencies} if term_dependencies else {}),
                      **({"identity_routes": [routes_by_spelling[normalize(relation[part]["name"])]
                            for part in ("head", "tail") if normalize(relation[part]["name"]) in routes_by_spelling]}
                         if any(normalize(relation[part]["name"]) in routes_by_spelling for part in ("head", "tail")) else {}))
            affected.add(row.key)
        nearby = {}
        ordered_spans = sorted(spans, key=lambda span: span["start"])
        for index, first_span in enumerate(ordered_spans):
            first_concept = concepts.get(normalize(first_span["name"]))
            if not first_concept or first_concept.state != "active" or not first_concept.canonical_id:
                continue
            for second_span in ordered_spans[index + 1:index + 11]:
                if second_span["start"] - first_span["end"] > 300:
                    break
                second_concept = concepts.get(normalize(second_span["name"]))
                if (not second_concept or second_concept.state != "active" or not second_concept.canonical_id
                        or second_concept.canonical_id == first_concept.canonical_id
                        or "\n\n" in source.text[first_span["end"]:second_span["start"]]):
                    continue
                pair = tuple(sorted((first_concept.canonical_id, second_concept.canonical_id)))
                nearby[pair] = max(nearby.get(pair, 0), min(first_span["score"], second_span["score"]))
        for first, second in sorted(nearby, key=lambda pair: (-nearby[pair], pair))[:16]:
            row = _finding(db, "association", [first, second], {"src_id": first, "dst_id": second, "predicate": "related_to"})
            routes = [route for route in routes_by_spelling.values()
                      if db.get(Finding, route["concept_key"]).canonical_id in (first, second)]
            _evidence(db, row, source, 0, len(source.text), 0.0, model_version,
                      **({"identity_routes": routes} if routes else {}))
            affected.add(row.key)
        db.flush()
        _context_associations(db, source, concepts.values(), embedding_version, model_version, affected,
                              routes_by_spelling.values())
        if vocabulary_changed:
            enqueue(db, "vocabulary", "all")
    db.flush()
    concept_ids = set()
    spellings = [json.loads(row.payload)["alias_key"] for row in db.query(Finding).filter(
        Finding.key.in_(affected), Finding.kind.in_(("alias", "alias_definition")))]
    if spellings:
        affected.update(identity.reconsider(db, spellings))
        affected.update(key for key, in db.query(Finding.key).filter(Finding.kind == "alias",
                        func.json_extract(Finding.payload, text("'$.alias_key'")).in_(spellings)))
    for key in affected:
        row = db.get(Finding, key)
        if not row or row.kind == "alias_definition":
            continue
        changed = _decide(db, row)
        if row.kind == "concept":
            if row.canonical_id:
                concept_ids.add(row.canonical_id)
            published = _publish_concept(db, row)
            if changed or published:
                enqueue(db, "vocabulary", "all")
        elif row.kind == "alias" and _publish_alias(db, row):
            enqueue(db, "vocabulary", "all")
        elif row.kind in {"relationship", "association"}:
            payload = json.loads(row.payload)
            concept_ids.update((payload["src_id"], payload["dst_id"]))
    db.flush()
    if indexing_old_source and not _definitions_pending(db):
        enqueue(db, "vocabulary", "aliases:")
        enqueue(db, "vocabulary", "identities:")
    if source:
        from ..concepts import retag_item, retag_passage
        from ..models import DocumentPassage
        if source_kind == "item":
            item = db.get(KnowledgeItem, source_id)
            retag_item(db, item)
            if item.kind == "question":
                _route_open_question(db, item)
        else:
            retag_passage(db, db.get(DocumentPassage, source_id))
    _project_relationships(db, concept_ids)
    for author in authors:
        enqueue(db, "profile", author)
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def _context_associations(db, source, concepts, embedding_version, model_version, affected, identity_routes=()):
    """Embeddings nominate optional weak links; they never assert a predicate."""
    from ..models import PassageConcept

    own = sorted({row.canonical_id for row in concepts if row.canonical_id and row.state == "active"})[:8]
    if not own:
        return
    vectors = (db.query(Embedding).filter_by(source_kind=source.kind, source_id=source.id, generation=embedding_version)
               .order_by(Embedding.start).limit(4))
    pairs = set()
    for vector in vectors:
        for candidate in embeddings.nearest(db, vector.vector, embedding_version, (source.kind, source.id)):
            if candidate["similarity"] < 0.75:
                continue
            kind, identity = candidate["source_kind"], candidate["source_id"]
            peer = snapshot(db, kind, identity)
            if peer is None or peer.group_key == source.group_key or peer.text_hash == source.text_hash:
                continue
            tags = (db.query(ItemConcept.concept_id).filter_by(item_id=identity) if kind == "item"
                    else db.query(PassageConcept.concept_id).filter_by(passage_id=identity))
            peer_ids = {cid for cid, in tags}
            peer_ids = sorted(row.id for row in effective.concepts(db).filter(Concept.id.in_(peer_ids)))[:8]
            for first in own:
                for second in peer_ids:
                    pair = tuple(sorted((first, second)))
                    if first == second or pair in pairs:
                        continue
                    pairs.add(pair)
                    row = _finding(db, "association", pair, {"src_id": pair[0], "dst_id": pair[1], "predicate": "related_to"})
                    routes = list(identity_routes)
                    for evidence in db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key).filter(
                            Evidence.source_kind == kind, Evidence.source_id == identity,
                            Finding.kind == "mention", Finding.canonical_id == second):
                        routes.extend(json.loads(evidence.features).get("identity_routes", []))
                    routes = [route for route in routes if db.get(Finding, route["concept_key"]).canonical_id in pair]
                    _evidence(db, row, source, vector.start, vector.end, 0.0, model_version,
                              context={"source_kind": kind, "source_id": identity, "source_hash": peer.content_hash,
                                       "start": candidate["start"], "end": candidate["end"]},
                              **({"identity_routes": list({_json(route): route for route in routes}.values())} if routes else {}))
                    affected.add(row.key)
                    if len(pairs) >= 16:
                        return


def cached_result(db, source, model_version, eligibility_version=None):
    from . import relationship_grounding

    stored = db.get(Source, (source.kind, source.id))
    if stored is None or stored.model_version != model_version:
        return None
    data = json.loads(stored.result)
    if not relationship_grounding.current_result(data):
        return None
    if len(model_version.split(":")) == 5 and not identity.coverage_current(data):
        return None
    if data.get("text_hash") != digest(source.text):
        return None
    count = db.query(Embedding).filter_by(source_kind=source.kind, source_id=source.id,
                                         generation=data.get("embedding_version")).count()
    if data.get("embedding_count") != count:
        return None
    result = {"concepts": data["concepts"], "relations": data["relations"], "chunks": None,
              "corroborated_definitions": data.get("corroborated_definitions", []),
              "relation_guard_revision": data["relation_guard_revision"]}
    if data.get("conflict_coverage_revision") == syntax.CONFLICT_REVISION:
        result.update(conflict_definitions=data["conflict_definitions"],
                      conflict_coverage_revision=syntax.CONFLICT_REVISION)
    # Judgments from another verifier or prompt are dropped; the sweep rechecks
    # them. Without a configured verifier stored judgments are kept as they are.
    if "eligibility" in data and eligibility_version in (None, data["eligibility"].get("version")):
        result["eligibility"] = data["eligibility"]
    return (result,
            (model_version, data["embedding_version"], data["dimensions"]))


def _route_open_question(db, question):
    if question.visibility != "team" or question.kind != "question" or question.question_status == "resolved" or question.accepted_answer_id:
        return
    from ..concepts import route_question
    ids = {tag.concept_id for tag in db.query(ItemConcept).filter_by(item_id=question.id)}
    route_question(db, question, effective.concepts(db).filter(Concept.id.in_(ids)).all())


def apply_profile(db, profile_id):
    from .. import topic_feedback
    from . import projection

    profile = db.get(Profile, profile_id)
    eligible = profile and profile.account_id and db.get(Account, profile.account_id)
    before = {row.concept_id for row in effective.expertise(db).filter(ExpertiseMapping.profile_id == profile_id)}
    confirmations = topic_feedback.eligible_confirmations(db, profile_id) if eligible else []
    items = {item.id: item for item in db.query(KnowledgeItem).filter(
        KnowledgeItem.id.in_({row.item_id for row in confirmations})).all()}
    questions = {item.id: item for item in db.query(KnowledgeItem).filter(
        KnowledgeItem.id.in_({row.question_id for row in confirmations if row.question_id})).all()}
    groups = projection.original_groups(items.values(), questions)
    source_hash = digest([topic_feedback.CONTRACT_VERSION,
                          [(row.id, row.context_token, row.concept_id, row.concept_identity_revision)
                           for row in sorted(confirmations, key=lambda row: row.id)],
                          sorted(groups.items())])
    pipeline = db.execute(text("SELECT pipeline_version FROM ml_state WHERE id=1")).scalar_one()
    db.merge(Source(kind="profile", id=profile_id, content_hash=source_hash, valid=bool(eligible),
                    model_version=policy.VERSION, updated_at=utcnow(),
                    result=_json({"projection_contract": projection.contract(), "pipeline_version": pipeline,
                                  "topic_feedback_contract": topic_feedback.CONTRACT_VERSION})))
    old = db.query(Finding).filter(Finding.kind == "expertise", func.json_extract(Finding.payload, "$.profile_id") == profile_id)
    affected = {row.key for row in old}
    db.query(Evidence).filter_by(source_kind="profile", source_id=profile_id).delete(synchronize_session="fetch")
    by_concept = {}
    for confirmation in confirmations:
        by_concept.setdefault(confirmation.concept_id, []).append(confirmation)
    for cid, records in by_concept.items():
        row = _finding(db, "expertise", [profile_id, cid], {"profile_id": profile_id, "concept_id": cid})
        affected.add(row.key)
        actors = {confirmation.actor_account_id for confirmation in records}
        originals = {groups[confirmation.item_id] for confirmation in records}
        accepted = len({confirmation.item_id for confirmation in records if confirmation.kind == "accepted"})
        row.state = "active" if len(actors) >= 2 and len(originals) >= 3 and accepted >= 1 else "held"
        db.add(Evidence(key=finding_key("expertise_evidence", row.key), finding_key=row.key,
               source_kind="profile", source_id=profile_id, source_hash=source_hash,
               group_key="profile:" + profile_id, author_id=profile_id, start=0, end=0, raw_score=0.0,
               polarity="positive", model_version=policy.VERSION,
               features=_json({"text_hash": source_hash, "actors": sorted(actors), "originals": len(originals),
                               "accepted_answers": accepted,
                               "item_ids": sorted({confirmation.item_id for confirmation in records}),
                               "topic_feedback_contract": topic_feedback.CONTRACT_VERSION,
                               "topic_confirmations": [{"id": confirmation.id, "kind": confirmation.kind,
                                                        "item_id": confirmation.item_id,
                                                        "concept_id": confirmation.concept_id,
                                                        "context_token": confirmation.context_token}
                                                       for confirmation in sorted(records, key=lambda row: row.id)]})))
    db.flush()
    for key in affected:
        row = db.get(Finding, key)
        row.score, row.calibrated = 0.0, False
        row.policy_version, row.updated_at = policy.VERSION, utcnow()
        payload = json.loads(row.payload)
        if payload["concept_id"] not in by_concept or not eligible:
            row.state = "withdrawn"
        fixed = db.get(Override, key)
        if fixed:
            row.state = "active" if fixed.mode == "pinned" else "suppressed"
        mapping = db.get(ExpertiseMapping, row.canonical_id) if row.canonical_id else None
        if row.state == "active" and eligible and mapping is None:
            mapping = db.query(ExpertiseMapping).filter_by(profile_id=profile_id, concept_id=payload["concept_id"]).first()
            if mapping is None:
                mapping = ExpertiseMapping(profile_id=profile_id, concept_id=payload["concept_id"])
                db.add(mapping)
                db.flush()
            row.canonical_id = mapping.id
        elif row.state != "active" and mapping and not (fixed and fixed.mode == "pinned"):
            db.delete(mapping)
            row.canonical_id = None
    db.flush()
    after = {row.concept_id for row in effective.expertise(db).filter(ExpertiseMapping.profile_id == profile_id)}
    if after - before:
        enqueue(db, "vocabulary", f"route:{profile_id}:")
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))


def apply_vocabulary(db, source_id):
    if source_id.startswith("identities:"):
        cursor = source_id.split(":", 1)[1]
        rows = db.query(Finding).filter(Finding.kind == "alias_definition", Finding.key > cursor).order_by(Finding.key).limit(25).all()
        identity.reconsider(db, [json.loads(row.payload)["alias_key"] for row in rows])
        if len(rows) == 25:
            enqueue(db, "vocabulary", "identities:" + rows[-1].key)
        return
    if source_id.startswith("aliases:"):
        cursor = source_id.split(":", 1)[1]
        rows = db.query(Finding).filter(Finding.kind == "alias", Finding.key > cursor).order_by(Finding.key).limit(25).all()
        changed = False
        for row in rows:
            _decide(db, row)
            changed |= _publish_alias(db, row)
        if len(rows) == 25:
            enqueue(db, "vocabulary", "aliases:" + rows[-1].key)
        if changed:
            request_backfill(db)
        db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))
        return
    if not source_id.startswith("route:"):
        request_backfill(db)
        return
    _, profile_id, cursor = source_id.split(":", 2)
    cids = [row.concept_id for row in effective.expertise(db).filter(ExpertiseMapping.profile_id == profile_id)]
    questions = (db.query(KnowledgeItem).join(ItemConcept, ItemConcept.item_id == KnowledgeItem.id)
                 .filter(KnowledgeItem.id > cursor, KnowledgeItem.kind == "question", KnowledgeItem.visibility == "team",
                         KnowledgeItem.question_status != "resolved", KnowledgeItem.accepted_answer_id.is_(None),
                         ItemConcept.concept_id.in_(cids)).distinct().order_by(KnowledgeItem.id).limit(25).all())
    for question in questions:
        _route_open_question(db, question)
    if len(questions) == 25:
        enqueue(db, "vocabulary", f"route:{profile_id}:{questions[-1].id}")
    db.execute(text("UPDATE ml_state SET revision=revision+1 WHERE id=1"))
