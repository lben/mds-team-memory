"""Apply derived findings through the app's existing canonical records.

The caller owns a short BEGIN IMMEDIATE transaction and validates its job,
source snapshot, and model generation before calling this module.
"""

import json

from sqlalchemy import func, or_, text

from ..models import (RELATED_TO_ID, Account, Concept, ConceptTerm, ExpertiseMapping,
                      ImpactEvent, ItemConcept, KnowledgeItem, Profile, Relationship,
                      RelationshipType, utcnow)
from . import effective, embeddings, policy
from .models import Embedding, Evidence, Finding, Override, Source
from .queue import enqueue, request_backfill
from .runtime import normalize, specific_name
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
    values = dict(kind=row.kind, mode=mode, username=username,
                  payload=_json(payload if payload is not None else json.loads(row.payload)), updated_at=utcnow())
    if entry is None:
        db.add(Override(key=row.key, **values))
    else:
        for name, value in values.items():
            setattr(entry, name, value)
    db.flush()


def _evidence(db, row, source, start, end, score, version, polarity="positive", **features):
    key = finding_key("evidence", row.key, source.kind, source.id, polarity)
    prior = db.get(Evidence, key)
    if prior and prior.raw_score >= score:
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
    fitted = policy.artifact(db)
    row.state, row.score = policy.decide(row.kind, effective.evidence_rows(db, row.key), fitted)
    row.calibrated = bool(fitted and row.kind in fitted["models"])
    row.policy_version, row.updated_at = fitted.get("version", policy.VERSION), utcnow()
    fixed = db.get(Override, row.key)
    if fixed:
        row.state = "active" if fixed.mode == "pinned" else "suppressed"
    return previous != row.state


def _concept(db, name):
    term = db.query(ConceptTerm).filter_by(term=normalize(name)).first()
    concept = db.get(Concept, term.concept_id) if term else None
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


def _publish_alias(db, row):
    payload = json.loads(row.payload)
    spelling = normalize(payload["alias"])
    blocked = db.get(Override, finding_key("term", spelling))
    if blocked and blocked.mode == "suppressed":
        row.state = "suppressed"
    conflicting = [other for other in db.query(Finding).filter(Finding.kind == "alias",
                   func.json_extract(Finding.payload, "$.alias_key") == spelling, Finding.key != row.key)
                   if normalize(json.loads(other.payload)["alias"]) == spelling
                   and json.loads(other.payload)["concept_id"] != payload["concept_id"]
                   and effective.evidence_rows(db, other.key)]
    if conflicting and not db.get(Override, row.key):
        row.state = "held"
        for other in conflicting:
            if not db.get(Override, other.key):
                other.state = "held"
                if other.canonical_id:
                    old_term = db.get(ConceptTerm, other.canonical_id)
                    if old_term and not old_term.is_canonical:
                        db.delete(old_term)
                    other.canonical_id = None
    term = db.get(ConceptTerm, row.canonical_id) if row.canonical_id else None
    if row.state != "active":
        if term and not term.is_canonical:
            db.delete(term)
            row.canonical_id = None
            return True
        return bool(conflicting)
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
    if source is None:
        embeddings.invalidate_source(db, source_kind, source_id)
        if stored:
            stored.valid = False
            stored.result = "{}"
    else:
        embeddings.replace_source(db, source, result["chunks"], embedding_version, dimensions)
        embedding_count = (len(result["chunks"]) if result["chunks"] is not None
                           else json.loads(stored.result)["embedding_count"])
        cached = {"text_hash": digest(source.text), "concepts": result["concepts"], "relations": result["relations"],
                  "embedding_version": embedding_version, "dimensions": dimensions, "embedding_count": embedding_count}
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
        definitions = list(policy.acronym_definitions(source.text))
        for definition in definitions:
            supporting = [span for span in spans if definition["start"] <= span["start"] < span["end"] <= definition["end"]]
            if supporting:
                spans.append({"name": definition["name"], "start": definition["start"],
                              "end": definition["alias_start"] - 2, "score": max(s["score"] for s in supporting),
                              "label": "explicit definition"})
        for span in spans:
            if not specific_name(span["name"]):
                continue
            row = _concept(db, span["name"])
            _evidence(db, row, source, span["start"], span["end"], span["score"], model_version,
                      grounded=True, label=span["label"])
            affected.add(row.key)
            concepts[normalize(span["name"])] = row
        db.flush()
        vocabulary_changed = False
        for row in {r.key: r for r in concepts.values()}.values():
            vocabulary_changed |= _decide(db, row)
            vocabulary_changed |= _publish_concept(db, row)
        db.flush()
        for span in spans:
            concept = concepts.get(normalize(span["name"]))
            if not concept or not concept.canonical_id:
                continue
            mention = _finding(db, "mention", [source_kind, source_id, concept.canonical_id],
                               {"source_kind": source_kind, "source_id": source_id, "concept_id": concept.canonical_id})
            mention.canonical_id = concept.canonical_id
            _evidence(db, mention, source, span["start"], span["end"], span["score"], model_version, grounded=True)
            affected.add(mention.key)
        for definition in definitions:
            concept = concepts.get(normalize(definition["name"]))
            if concept and concept.canonical_id:
                alias = _finding(db, "alias", [normalize(definition["alias"]), concept.canonical_id],
                                 {"alias": definition["alias"], "alias_key": normalize(definition["alias"]), "concept_id": concept.canonical_id})
                score = max(span["score"] for span in spans if normalize(span["name"]) == normalize(definition["name"]))
                _evidence(db, alias, source, definition["start"], definition["end"], score, model_version, explicit_definition=True)
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
            head, tail = (concepts.get(normalize(relation[part]["name"])) for part in ("head", "tail"))
            if not head or not tail or not head.canonical_id or not tail.canonical_id or head.canonical_id == tail.canonical_id:
                continue
            row = _finding(db, "relationship", [head.canonical_id, relation["predicate"], tail.canonical_id],
                           {"src_id": head.canonical_id, "dst_id": tail.canonical_id, "predicate": relation["predicate"]})
            _evidence(db, row, source, relation["start"], relation["end"], relation["score"], model_version,
                      polarity=relation["polarity"], literal_support=relation["literal_support"],
                      assertion_allowed=source.assertion_allowed, head=relation["head"], tail=relation["tail"])
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
            _evidence(db, row, source, 0, len(source.text), 0.0, model_version)
            affected.add(row.key)
        db.flush()
        _context_associations(db, source, concepts.values(), embedding_version, model_version, affected)
        if vocabulary_changed:
            enqueue(db, "vocabulary", "all")
    db.flush()
    concept_ids = set()
    for key in affected:
        row = db.get(Finding, key)
        if not row:
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


def _context_associations(db, source, concepts, embedding_version, model_version, affected):
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
                    _evidence(db, row, source, vector.start, vector.end, 0.0, model_version,
                              context={"source_kind": kind, "source_id": identity, "source_hash": peer.content_hash,
                                       "start": candidate["start"], "end": candidate["end"]})
                    affected.add(row.key)
                    if len(pairs) >= 16:
                        return


def cached_result(db, source, model_version):
    stored = db.get(Source, (source.kind, source.id))
    if stored is None or stored.model_version != model_version:
        return None
    data = json.loads(stored.result)
    if data.get("text_hash") != digest(source.text):
        return None
    count = db.query(Embedding).filter_by(source_kind=source.kind, source_id=source.id,
                                         generation=data.get("embedding_version")).count()
    if data.get("embedding_count") != count:
        return None
    return ({"concepts": data["concepts"], "relations": data["relations"], "chunks": None},
            (model_version, data["embedding_version"], data["dimensions"]))


def _route_open_question(db, question):
    if question.visibility != "team" or question.kind != "question" or question.question_status == "resolved" or question.accepted_answer_id:
        return
    from ..concepts import route_question
    ids = {tag.concept_id for tag in db.query(ItemConcept).filter_by(item_id=question.id)}
    route_question(db, question, effective.concepts(db).filter(Concept.id.in_(ids)).all())


def apply_profile(db, profile_id):
    profile = db.get(Profile, profile_id)
    eligible = profile and profile.account_id and db.get(Account, profile.account_id)
    before = {row.concept_id for row in effective.expertise(db).filter(ExpertiseMapping.profile_id == profile_id)}
    events = db.query(ImpactEvent).filter_by(beneficiary_profile_id=profile_id).all() if eligible else []
    source_hash = digest([(e.id, e.event_type, e.item_id, e.actor_profile_id) for e in sorted(events, key=lambda e: e.id)])
    db.merge(Source(kind="profile", id=profile_id, content_hash=source_hash, valid=bool(eligible), model_version=policy.VERSION, updated_at=utcnow()))
    old = db.query(Finding).filter(Finding.kind == "expertise", func.json_extract(Finding.payload, "$.profile_id") == profile_id)
    affected = {row.key for row in old}
    db.query(Evidence).filter_by(source_kind="profile", source_id=profile_id).delete(synchronize_session="fetch")
    by_concept = {}
    for event in events:
        if event.event_type not in {"helped", "sme_endorsed", "answer_accepted"} or not event.actor_profile_id or event.actor_profile_id == profile_id:
            continue
        item = db.get(KnowledgeItem, event.item_id) if event.item_id else None
        actor = db.get(Profile, event.actor_profile_id)
        if (not item or item.visibility != "team" or item.author_profile_id != profile_id
                or item.kind not in {"note", "answer"} or not actor or not actor.account_id
                or not db.get(Account, actor.account_id)):
            continue
        if event.event_type == "answer_accepted":
            question = db.get(KnowledgeItem, item.parent_id) if item.parent_id else None
            if not question or question.accepted_answer_id != item.id:
                continue
        for tag in db.query(ItemConcept).filter_by(item_id=item.id):
            if effective.concepts(db).filter(Concept.id == tag.concept_id).first():
                by_concept.setdefault(tag.concept_id, []).append((event, item))
    for cid, records in by_concept.items():
        row = _finding(db, "expertise", [profile_id, cid], {"profile_id": profile_id, "concept_id": cid})
        affected.add(row.key)
        actors = {event.actor_profile_id for event, _ in records}
        originals = {item.group_id or item.normalized_hash or item.id for _, item in records}
        accepted = sum(event.event_type == "answer_accepted" for event, _ in records)
        row.score, row.calibrated = 0.0, False
        row.state = "active" if len(actors) >= 2 and len(originals) >= 3 and accepted >= 1 else "held"
        row.policy_version, row.updated_at = policy.VERSION, utcnow()
        db.add(Evidence(key=finding_key("expertise_evidence", row.key), finding_key=row.key,
               source_kind="profile", source_id=profile_id, source_hash=source_hash,
               group_key="profile:" + profile_id, author_id=profile_id, start=0, end=0, raw_score=0.0,
               polarity="positive", model_version=policy.VERSION,
               features=_json({"text_hash": source_hash, "actors": sorted(actors), "originals": len(originals),
                               "accepted_answers": accepted, "item_ids": sorted({item.id for _, item in records}),
                               "event_ids": sorted({event.id for event, _ in records})})))
    db.flush()
    fitted = policy.artifact(db)
    for key in affected:
        row = db.get(Finding, key)
        payload = json.loads(row.payload)
        if payload["concept_id"] not in by_concept or not eligible:
            row.state = "withdrawn"
        if fitted and "expertise" in fitted["models"]:
            from .calibration import predict

            row.score = predict(fitted, "expertise", policy.features(effective.evidence_rows(db, key)))
            row.calibrated, row.policy_version = True, fitted["version"]
            if row.state == "active" and row.score < fitted["models"]["expertise"]["threshold"]:
                row.state = "held"
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
