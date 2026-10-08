"""Mechanical integration proof with supplied encoder outputs, not model-quality evidence."""

import json
import uuid

import pytest

from ml_synthetic_records import current_synthetic_metadata, current_synthetic_result, select_synthetic_pipeline


@pytest.fixture(autouse=True)
def automation(app_modules):
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.ml.adapter import bootstrap

    with SessionLocal() as db:
        previous = db.execute(text("SELECT automation_enabled FROM ml_state WHERE id=1")).scalar_one()
        bootstrap(db)
        db.execute(text("UPDATE ml_state SET automation_enabled=1 WHERE id=1"))
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET automation_enabled=:enabled WHERE id=1"), {"enabled": previous})
        db.commit()


@pytest.fixture
def obsolete_policy(app_modules):
    """A previously fitted policy must not alter fixed inference decisions."""
    from sqlalchemy import text

    from app.db import SessionLocal

    model = {"intercept": -100, "coefficients": [0] * 8, "platt_intercept": 0,
             "platt_coefficient": 1, "threshold": 0.99}
    stored = json.dumps({"version": "obsolete-fitted", "models": {
        kind: model for kind in ("concept", "mention", "relationship", "expertise")}})
    with SessionLocal() as db:
        previous = db.execute(text("SELECT decision_policy FROM ml_state WHERE id=1")).scalar_one()
        db.execute(text("UPDATE ml_state SET decision_policy=:policy WHERE id=1"), {"policy": stored})
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET decision_policy=:policy WHERE id=1"), {"policy": previous})
        db.commit()


def _capture(client, body):
    response = client.post("/api/capture", data={"body": body})
    assert response.status_code == 200, response.text
    return response.json()["item"]["id"]


def _apply(source_id, names=(), *, relation=False, cached=False, omitted_entity=None, kind="item"):
    """Replace only expensive model inference; use real source reads and application."""
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.ml import adapter, syntax
    from app.ml.sources import snapshot

    metadata = current_synthetic_metadata()
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        select_synthetic_pipeline(db, metadata)
        source = snapshot(db, kind, source_id)
        result = None
        if source and cached:
            stored = adapter.cached_result(db, source, metadata[0])
            assert stored is not None, "Source output should be reusable without inference"
            result, metadata = stored
        elif source:
            spans = [{"name": name, "start": source.text.index(name),
                      "end": source.text.index(name) + len(name), "score": 0.995,
                      "label": "technology"} for name in names]
            result = {"concepts": [span for span in spans if span["name"] != omitted_entity],
                      "relations": [], "chunks": [], "conflict_definitions": [],
                      "conflict_coverage_revision": syntax.CONFLICT_REVISION}
            if relation:
                result["relations"] = [{"head": spans[0], "tail": spans[1], "predicate": "uses",
                                        "score": 0.9, "start": 0, "end": len(source.text),
                                        "polarity": "positive", "literal_support": True}]
            result = current_synthetic_result(result, source.text)
        adapter.apply_source(db, kind, source_id, source, result, *metadata)
        db.commit()


def _tags(client, item_id):
    return {c["name"]: c["id"] for c in client.get(f"/api/items/{item_id}").json()["concepts"]}


def _edge(client, left, right):
    edges = client.get("/api/graph/global").json()["edges"]
    return next((edge for edge in edges if {edge["source"], edge["target"]} == {left, right}), None)


def _finding(admin, kind, **payload):
    rows = admin.get("/api/ml/findings", params={"kind": kind, "limit": 100}).json()["findings"]
    return next(row for row in rows if all(row["payload"].get(key) == value for key, value in payload.items()))


def _decision(admin, key, mode):
    response = admin.put(f"/api/ml/findings/{key}/decision", json={"mode": mode})
    assert response.status_code == 200, response.text
    return response.json()


def test_sentence_punctuation_is_not_published_as_part_of_a_concept(make_client):
    client = make_client()
    item = _capture(client, "Neither. ECM names edge curvature measure, and the present Elm carving model has no steel armature.")
    _apply(item, [". ECM", "ECM"])
    _apply(item, cached=True)
    assert set(_tags(client, item)) == {"ECM"}
    assert {c["name"] for c in client.get("/api/search", params={"q": ". ECM"}).json()["concepts"]} == {"ECM"}

    dotted = _capture(client, ".NET Core handles the archive import.")
    _apply(dotted, [".NET Core"])
    assert set(_tags(client, dotted)) == {".NET Core"}


def test_automatic_concepts_and_relation_replay_keep_independent_evidence(make_client, admin_client, obsolete_policy):
    from app.db import SessionLocal
    from app.ml import policy
    from app.ml.models import Finding

    first, second = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    names = (f"Asteria{suffix}", f"BorealDB{suffix}")
    body = f"{names[0]} uses {names[1]} to store encrypted audit trails."
    first_id = _capture(first, body)
    _apply(first_id, names, relation=True)
    tags = _tags(first, first_id)
    assert set(tags) == set(names)
    edge = _edge(first, *tags.values())
    assert edge["style"] == "dashed" and edge["support_count"] == 1

    finding = _finding(admin_client, "concept", name=names[0])
    with SessionLocal() as db:
        stored = db.get(Finding, finding["key"])
        stored.calibrated, stored.score, stored.policy_version = True, 0.01, "obsolete-fitted"
        db.commit()
    assert _finding(admin_client, "concept", name=names[0])["raw_model_score"] is None
    _apply(first_id, cached=True)
    refreshed = _finding(admin_client, "concept", name=names[0])
    assert refreshed["raw_model_score"] == 0.995 and refreshed["policy_version"] == policy.VERSION

    for cached in (False, True, True):
        _apply(first_id, names, relation=True, cached=cached)
    replayed = _edge(first, *tags.values())
    assert replayed["link_id"] == edge["link_id"]
    assert replayed["style"] == "dashed" and replayed["support_count"] == 1
    assert _tags(first, first_id) == tags

    second_body = f"Our benchmark found that {names[0]} uses {names[1]} during the archive job. Keep pools bounded."
    second_id = _capture(second, second_body)
    # Relation extraction can find an established endpoint omitted by entity extraction.
    _apply(second_id, names, relation=True, omitted_entity=names[0])
    edge = _edge(first, *tags.values())
    assert (edge["style"], edge["label"], edge["origin"], edge["support_count"]) == ("solid", "uses", "automatic", 2)
    assert edge["directed"] and edge["source"] == tags[names[0]] and edge["target"] == tags[names[1]]
    _apply(second_id, cached=True)
    evidence = first.get(f"/api/graph/links/{edge['link_id']}/evidence").json()
    claim = next(claim for claim in evidence["claims"] if claim["predicate"] == "uses")
    assert {source["source_id"]: source["quote"] for source in claim["sources"]} == {
        first_id: body, second_id: second_body,
    }
    assert claim["support_count"] == 2
    assert any(c["id"] == tags[names[0]] for c in first.get("/api/search", params={"q": names[0]}).json()["concepts"])
    focused = first.get("/api/graph/local", params={"concept_id": tags[names[1]]}).json()
    assert next(e for e in focused["edges"] if e["link_id"] == edge["link_id"])["source"] == f"c:{tags[names[0]]}"
    assert first.get("/api/ml/findings").status_code == 401


@pytest.mark.parametrize("change", ["edit", "delete", "private"])
def test_source_invalidation_withdraws_before_reprocessing(make_client, change):
    owner, reader = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    names = (f"Ceres{suffix}", f"Dorado{suffix}")
    item_id = _capture(owner, f"{names[0]} uses {names[1]} for monthly settlement.")
    _apply(item_id, names, relation=True)
    ids = list(_tags(reader, item_id).values())
    assert _edge(reader, *ids)

    if change == "edit":
        assert owner.put(f"/api/items/{item_id}", json={"body": "The procedure has been retired."}).status_code == 200
    elif change == "delete":
        assert owner.delete(f"/api/items/{item_id}").status_code == 200
    else:
        # No visibility-edit endpoint exists; exercise the real database trigger.
        from app.db import SessionLocal
        from app.models import KnowledgeItem

        with SessionLocal() as db:
            db.get(KnowledgeItem, item_id).visibility = "private"
            db.commit()
        assert reader.get(f"/api/items/{item_id}").status_code == 404

    assert _edge(reader, *ids) is None
    assert reader.get("/api/search", params={"q": names[0]}).json()["concepts"] == []
    if change == "edit":
        assert _tags(reader, item_id) == {}
    _apply(item_id)
    assert _edge(reader, *ids) is None
    assert reader.get("/api/search", params={"q": names[0]}).json()["concepts"] == []


def test_concept_edits_and_suppression_survive_reprocessing_until_restore(make_client, admin_client):
    owner = make_client()
    name = f"Elektra{uuid.uuid4().hex[:6]}"
    item_id = _capture(owner, f"{name} archives the overnight settlement log.")
    _apply(item_id, [name])
    concept_id = _tags(owner, item_id)[name]
    finding = _finding(admin_client, "concept", name=name)
    renamed = name + " Ledger"
    edit = admin_client.put(f"/api/admin/concepts/{concept_id}", json={"name": renamed, "aliases": [name]})
    assert edit.status_code == 200, edit.text
    _apply(item_id, cached=True)
    assert _tags(owner, item_id) == {renamed: concept_id}
    assert admin_client.get(f"/api/ml/findings/{finding['key']}").json()["state"] == "pinned"

    _decision(admin_client, finding["key"], "suppressed")
    _apply(item_id, cached=True)
    assert _tags(owner, item_id) == {}
    assert owner.get("/api/search", params={"q": name}).json()["concepts"] == []
    _decision(admin_client, finding["key"], "automatic")
    _apply(item_id, cached=True)
    assert _tags(owner, item_id) == {renamed: concept_id}
    assert admin_client.delete(f"/api/admin/concepts/{concept_id}").status_code == 200
    _apply(item_id, cached=True)
    assert _tags(owner, item_id) == {}
    _decision(admin_client, finding["key"], "automatic")
    _apply(item_id, cached=True)
    assert set(_tags(owner, item_id)) == {renamed}


def test_link_and_source_topic_controls_persist_through_reprocessing(make_client, admin_client):
    first, second = make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    names = (f"Fornax{suffix}", f"Gemini{suffix}")
    first_id = _capture(first, f"{names[0]} uses {names[1]} to store daily market reports.")
    second_id = _capture(second, f"Capacity testing confirms {names[0]} uses {names[1]} for the historical data archive.")
    for item_id in (first_id, second_id):
        _apply(item_id, names, relation=True)
    ids = _tags(first, first_id)
    edge = _edge(first, *ids.values())
    response = admin_client.patch(f"/api/graph/links/{edge['link_id']}", json={"state": "rejected"})
    assert response.status_code == 200, response.text
    _apply(second_id, cached=True)
    assert _edge(first, *ids.values()) is None
    fixed = next(row for row in admin_client.get("/api/ml/findings", params={"kind": "relationship_pair"}).json()["findings"]
                 if row["canonical_id"] == edge["link_id"])
    _decision(admin_client, fixed["key"], "automatic")
    _apply(second_id, cached=True)
    assert _edge(first, *ids.values())["style"] == "solid"
    assert _edge(first, *ids.values())["origin"] == "automatic"
    revision = first.get("/api/ml/revision").json()["revision"]
    reverse = admin_client.patch(f"/api/graph/links/{edge['link_id']}", json={"reverse": True})
    assert reverse.status_code == 200, reverse.text
    assert (reverse.json()["src_id"], reverse.json()["dst_id"]) == (ids[names[1]], ids[names[0]])
    assert first.get("/api/ml/revision").json()["revision"] > revision
    for item_id in (first_id, second_id):
        _apply(item_id, cached=True)
    reversed_edge = _edge(first, *ids.values())
    assert (reversed_edge["source"], reversed_edge["target"], reversed_edge["origin"]) == (ids[names[1]], ids[names[0]], "manual")
    local = first.get("/api/graph/local", params={"concept_id": ids[names[0]]}).json()
    arrow = next(e for e in local["edges"] if e["link_id"] == edge["link_id"])
    assert (arrow["source"], arrow["target"], arrow["directed"]) == (f"c:{ids[names[1]]}", f"c:{ids[names[0]]}", True)

    rtype = admin_client.post("/api/admin/relationship-types", json={"name": f"feeds{suffix}"}).json()
    assert admin_client.patch(f"/api/graph/links/{edge['link_id']}", json={"type_id": rtype["id"], "note": "Owner confirmed"}).status_code == 200
    _apply(first_id, cached=True)
    assert _edge(first, *ids.values())["label"] == rtype["name"]
    assert _edge(first, *ids.values())["origin"] == "manual"

    mention = _finding(admin_client, "mention", source_id=first_id, concept_id=ids[names[0]])
    _decision(admin_client, mention["key"], "suppressed")
    _apply(first_id, cached=True)
    assert names[0] not in _tags(first, first_id)
    assert names[0] in _tags(second, second_id)
    _decision(admin_client, mention["key"], "automatic")
    _apply(first_id, cached=True)
    assert names[0] in _tags(first, first_id)

    corrected = admin_client.post("/api/admin/concepts", json={"name": f"Hydra{suffix}", "aliases": []}).json()
    response = admin_client.put(f"/api/ml/findings/{mention['key']}/topic", json={"concept_id": corrected["id"]})
    assert response.status_code == 200, response.text
    _apply(first_id, cached=True)
    assert _tags(first, first_id) == {names[1]: ids[names[1]], corrected["name"]: corrected["id"]}
    assert names[0] in _tags(second, second_id)
    assert response.json()["state"] == "pinned"


def test_explicit_alias_requires_independent_definitions_and_ambiguity_withdraws_it(make_client, admin_client):
    first, second, third = make_client(), make_client(), make_client()
    name, alias = "Kestrel Protocol Relay", "KPR"
    first_id = _capture(first, f"{name} ({alias}) forwards encrypted settlement acknowledgements.")
    _apply(first_id, [name, alias])
    concept_id = _tags(first, first_id)[name]
    assert _tags(first, first_id) == {name: concept_id}
    key = _finding(admin_client, "alias", alias=alias, concept_id=concept_id)["key"]
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"
    _apply(first_id, cached=True)
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"

    second_id = _capture(second, f"Our recovery runbook calls the failover service {name} ({alias}). Operators restart it after receipt reconciliation.")
    _apply(second_id, [name, alias])
    assert _tags(second, second_id) == {name: concept_id}
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "active"
    assert any(c["id"] == concept_id for c in first.get("/api/search", params={"q": alias}).json()["concepts"])

    other = "Kernel Packet Relay"
    third_id = _capture(third, f"{other} ({alias}) traces local network packet loss.")
    _apply(third_id, [other, alias])
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []
    _apply(second_id, cached=True)
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []

    assert third.put(f"/api/items/{third_id}", json={"body": f"{other} traces local network packet loss."}).status_code == 200
    _apply(third_id, [other])
    assert _tags(third, third_id).keys() == {other}
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "active"
    assert any(c["id"] == concept_id for c in first.get("/api/search", params={"q": alias}).json()["concepts"])


def _apply_role_definition(item_id, name, alias, *, cached=False, entity_score=0.995):
    from app.db import SessionLocal
    from app.ml import adapter, syntax
    from app.ml.sources import digest, snapshot

    metadata = current_synthetic_metadata()
    with SessionLocal() as db:
        select_synthetic_pipeline(db, metadata)
        source = snapshot(db, "item", item_id)
        if cached:
            result, cached_metadata = adapter.cached_result(db, source, metadata[0])
            assert cached_metadata == metadata
            assert result["corroborated_definitions"]
        else:
            def field(value, score):
                start = source.text.index(value)
                return {"text": value, "start": start, "end": start + len(value), "confidence": score}

            full, short = field(name, 0.997), field(alias, 0.999)
            result = {"concepts": [{"name": name, "start": full["start"], "end": full["end"],
                                     "score": entity_score, "label": "named entity"}],
                      "relations": [], "chunks": [], "conflict_definitions": [],
                      "conflict_coverage_revision": syntax.CONFLICT_REVISION, "corroborated_definitions": [{
                          "full_name": full, "short_name": short, "source_text_hash": digest(source.text),
                          "syntax_rules": ["parenthetical_compact_name" if "(" in source.text else "denotes"],
                          "syntax_rule_revision": "fixture-r4",
                          "alias_model_revision": "fixture-extractor", "syntax_model_revision": "fixture-syntax"}]}
            result = current_synthetic_result(result, source.text)
        adapter.apply_source(db, "item", item_id, source, result, *metadata)
        db.commit()


def test_alias_role_union_holds_one_author_survives_cache_and_respects_removal_and_opt_out(make_client, admin_client):
    first, second = make_client(account=False), make_client(account=False)
    for index, client in enumerate((first, second)):
        response = client.post("/api/auth/signup", json={"username": f"alias-role-owner-{index}", "password": "a-good-password"})
        assert response.status_code == 200, response.text
    name, alias = "Valley Trace Register", "VTR"
    original = _capture(first, f"{name} ({alias}) stores the signed calibration record.")
    _apply_role_definition(original, name, alias)
    concept_id = _tags(first, original)[name]
    key = _finding(admin_client, "alias", alias=alias, concept_id=concept_id)["key"]

    def detail():
        return admin_client.get(f"/api/ml/findings/{key}").json()

    def search_ids():
        return [c["id"] for c in first.get("/api/search", params={"q": alias}).json()["concepts"]]

    held = detail()
    assert held["state"] == "held" and held["features"]["independent_groups"] == 1
    methods = held["evidence"][0]["definition_methods"]
    assert {m["origin"] for m in methods} == {"legacy_entity_definition", "syntax_and_alias_role_record"}
    assert search_ids() == []
    repeated = _capture(first, f"{alias} denotes {name} in the overnight calibration entry.")
    _apply_role_definition(repeated, name, alias)
    assert detail()["state"] == "held" and detail()["features"]["independent_groups"] == 1
    independent = _capture(second, f"{alias} denotes {name} in my separate instrument check.")
    _apply_role_definition(independent, name, alias)
    assert detail()["state"] == "active" and detail()["features"]["independent_groups"] == 2
    assert search_ids() == [concept_id]
    for item in (original, repeated, independent):
        _apply_role_definition(item, name, alias, cached=True)
        assert _tags(first, item) == {name: concept_id}
    assert search_ids() == [concept_id]
    assert len(detail()["evidence"]) == 3
    role = next(m for m in detail()["evidence"][0]["definition_methods"] if m["origin"] == "syntax_and_alias_role_record")
    assert role["full_name"]["confidence"] == 0.997 and role["short_name"]["confidence"] == 0.999
    assert _finding(admin_client, "concept", name=name)["raw_model_score"] == 0.995
    mention = _finding(admin_client, "mention", source_id=independent, concept_id=concept_id)
    assert mention["raw_model_score"] == 0.995
    _decision(admin_client, key, "suppressed")
    _apply_role_definition(independent, name, alias, cached=True)
    assert detail()["state"] == "suppressed" and search_ids() == []
    _decision(admin_client, key, "automatic")
    _apply_role_definition(independent, name, alias, cached=True)
    assert search_ids() == [concept_id]
    assert second.delete(f"/api/items/{independent}").status_code == 200
    _apply(independent)
    assert detail()["state"] == "held" and search_ids() == []
    copied = _capture(second, f"{name} ({alias}) stores the signed calibration record.")
    # Capture updates duplicate grouping, so refresh the original source version.
    _apply_role_definition(original, name, alias, cached=True)
    _apply_role_definition(copied, name, alias)
    assert detail()["state"] == "held" and detail()["features"]["independent_groups"] == 1


def test_alias_role_confidence_cannot_publish_weak_concept_evidence(make_client, admin_client):
    name, alias = "Umber Trace Register", "UTR"
    for client, context in ((make_client(), "first check"), (make_client(), "second check")):
        item = _capture(client, f"{alias} denotes {name} in the {context}.")
        _apply_role_definition(item, name, alias, entity_score=0.2)
        _apply_role_definition(item, name, alias, cached=True)
        assert _tags(client, item) == {}
    finding = _finding(admin_client, "concept", name=name)
    assert (finding["state"], finding["raw_model_score"]) == ("held", 0.2)
    assert client.get("/api/search", params={"q": alias}).json()["concepts"] == []


def test_spelling_variants_reuse_identity_and_respect_removal(make_client, admin_client):
    first, second, third = make_client(), make_client(), make_client()
    name, variant = "Meridian Access-Control", "Meridian Access Control"
    original = _capture(first, f"{name} limits access to the receipt store.")
    _apply(original, [name])
    concept_id = _tags(first, original)[name]
    one = _capture(second, f"Our operators enabled {variant} for ledger recovery.")
    two = _capture(third, f"The audit guide requires {variant} for receipt downloads.")
    for item_id in (one, two):
        _apply(item_id, [variant])
        assert _tags(first, item_id) == {name: concept_id}
    alias_key = _finding(admin_client, "alias", alias=variant, concept_id=concept_id)["key"]
    assert admin_client.get(f"/api/ml/findings/{alias_key}").json()["state"] == "active"
    for item_id in (one, two):
        _apply(item_id, cached=True)
    assert admin_client.get(f"/api/ml/findings/{alias_key}").json()["state"] == "active"
    assert [c["id"] for c in first.get("/api/search", params={"q": variant}).json()["concepts"]] == [concept_id]
    assert admin_client.put(f"/api/admin/concepts/{concept_id}", json={"name": name, "aliases": []}).status_code == 200
    _apply(one, cached=True)
    assert _tags(first, one) == {}
    _decision(admin_client, alias_key, "automatic")
    _apply(one, cached=True)
    assert _tags(first, one) == {name: concept_id}
    concept_key = _finding(admin_client, "concept", name=name)["key"]
    _decision(admin_client, concept_key, "suppressed")
    _apply(two, cached=True)
    assert _tags(first, two) == {}


def test_contextual_equivalence_needs_affirmative_independent_evidence(make_client, admin_client):
    first, second, third = make_client(), make_client(), make_client()
    name, alias = "Sirius Ledger Gateway", "Receipt Bridge"
    one = _capture(first, f"{name} is also known as {alias}. It accepts settlement batches.")
    two = _capture(second, f"Our recovery guide documents {name}, also called {alias}, for incident response.")
    for item_id in (one, two):
        _apply(item_id, [name, alias])
    concept_id = _tags(first, one)[name]
    assert _tags(first, one) == _tags(first, two) == {name: concept_id}
    key = _finding(admin_client, "alias", alias=alias, concept_id=concept_id)["key"]
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "active"
    assert first.delete(f"/api/items/{one}").status_code == 200
    _apply(one)
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"
    uncertain = _capture(third, f"Perhaps {name} is also known as {alias} in the new deployment?")
    _apply(uncertain, [name, alias])
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"
    restored = _capture(third, f"The deployment guide confirms {name} is also known as {alias}. Receipts arrive there.")
    _apply(restored, [name, alias])
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "active"


@pytest.mark.parametrize("unsupported", [
    'The caption "{definition}" was rejected.',
    "There is no evidence that {definition}.",
])
def test_unsupported_definition_withdraws_alias_and_cannot_republish_it(make_client, admin_client, unsupported):
    first, second, third = make_client(), make_client(), make_client()
    suffix = uuid.uuid4().hex[:6]
    name, alias = f"Vega Ledger{suffix}", f"Archive Bridge{suffix}"
    definition = f"{name} is also called {alias}"
    one = _capture(first, f"{definition}. It receives daily settlement receipts.")
    two = _capture(second, f"Our recovery guide confirms {definition}. Operators use it for receipt replay.")
    for item_id in (one, two):
        _apply(item_id, [name, alias])
    concept_id = _tags(first, one)[name]
    key = _finding(admin_client, "alias", alias=alias, concept_id=concept_id)["key"]
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "active"
    assert any(c["id"] == concept_id for c in first.get("/api/search", params={"q": alias}).json()["concepts"])

    body = unsupported.format(definition=definition)
    assert second.put(f"/api/items/{two}", json={"body": body}).status_code == 200
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []
    _apply(two, [name, alias])
    finding = admin_client.get(f"/api/ml/findings/{key}").json()
    assert finding["state"] == "held"
    assert finding["raw_model_score"] == 0.995
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []
    another = _capture(third, body + " The archive review records this correction.")
    _apply(another, [name, alias])
    _apply(two, cached=True)
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []


@pytest.mark.parametrize("name, alias, template", [
    ("Orion Plan", "Rejected Gateway", "{name} is also called {alias}"),
    ("Rejected Relay", "Lyra Plan Bridge", "{alias} is an alias for {name}"),
    ("Rejected Plan Service", "RPS", "{name} ({alias})"),
])
def test_alias_assertion_cues_inside_grounded_names_do_not_block_definitions(
        make_client, admin_client, name, alias, template):
    first, second = make_client(), make_client()
    definition = template.format(name=name, alias=alias)
    one = _capture(first, f"{definition}. It archives settlement receipts.")
    two = _capture(second, f"The recovery guide identifies {definition}. Operators use it during receipt replay.")
    _apply(one, [name, alias])
    concept_id = _tags(first, one)[name]
    key = _finding(admin_client, "alias", alias=alias, concept_id=concept_id)["key"]
    assert admin_client.get(f"/api/ml/findings/{key}").json()["state"] == "held"
    _apply(two, [name, alias])
    _apply(two, cached=True)
    assert _tags(first, one) == _tags(first, two) == {name: concept_id}
    finding = admin_client.get(f"/api/ml/findings/{key}").json()
    assert finding["state"] == "active" and finding["raw_model_score"] == 0.995
    assert [c["id"] for c in first.get("/api/search", params={"q": alias}).json()["concepts"]] == [concept_id]


def test_alias_resolution_preserves_distinct_manual_identities_and_word_boundaries(make_client, admin_client):
    client = make_client()
    name, alias = "Titan Packet Collector", "TPC"
    manual = admin_client.post("/api/admin/concepts", json={"name": alias, "aliases": []}).json()
    item_id = _capture(client, f"{name} ({alias}) collects wire receipts.")
    _apply(item_id, [name, alias])
    tags = _tags(client, item_id)
    assert tags[alias] == manual["id"] and tags[name] != manual["id"]
    different = ["Receipt re-sign", "Receipt resign", "C++ Ledger", "C# Ledger"]
    other = _capture(client, "The comparison covers " + ", ".join(different) + ".")
    _apply(other, different)
    assert len(set(_tags(client, other).values())) == 4


def test_deleted_concept_cannot_return_through_a_new_spelling(make_client, admin_client):
    client = make_client()
    name, variant = "Lyra Access-Control", "Lyra Access Control"
    original = _capture(client, f"{name} limits access to the receipt store.")
    _apply(original, [name])
    concept_id = _tags(client, original)[name]
    key = _finding(admin_client, "concept", name=name)["key"]
    assert admin_client.delete(f"/api/admin/concepts/{concept_id}").status_code == 200
    later = _capture(client, f"The recovery guide describes {variant} for operators.")
    _apply(later, [variant])
    assert _tags(client, later) == {}
    assert client.get("/api/search", params={"q": variant}).json()["concepts"] == []
    _decision(admin_client, key, "automatic")
    _apply(later, cached=True)
    assert _tags(client, later) == {name: concept_id}


def test_alias_restore_releases_removed_spelling_but_preserves_concept_suppression(make_client, admin_client):
    first, second = make_client(), make_client()
    name, alias = "Helios Batch Relay", "HBR"
    first_id = _capture(first, f"{name} ({alias}) sends reconciled settlement batches to custody.")
    second_id = _capture(second, f"The recovery guide identifies the retry service as {name} ({alias}). Clear its checkpoint after reconciling receipts.")
    for item_id in (first_id, second_id):
        _apply(item_id, [name])
    concept_id = _tags(first, first_id)[name]
    alias_key = _finding(admin_client, "alias", alias=alias, concept_id=concept_id)["key"]
    assert admin_client.get(f"/api/ml/findings/{alias_key}").json()["state"] == "active"
    removed = admin_client.put(f"/api/admin/concepts/{concept_id}", json={"name": name, "aliases": []})
    assert removed.status_code == 200, removed.text
    _apply(second_id, cached=True)
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []
    _decision(admin_client, alias_key, "automatic")
    _apply(second_id, cached=True)
    assert admin_client.get(f"/api/ml/findings/{alias_key}").json()["state"] == "active"
    assert any(c["id"] == concept_id for c in first.get("/api/search", params={"q": alias}).json()["concepts"])

    concept_key = _finding(admin_client, "concept", name=name)["key"]
    _decision(admin_client, concept_key, "suppressed")
    _decision(admin_client, alias_key, "automatic")
    _apply(second_id, cached=True)
    assert admin_client.get(f"/api/ml/findings/{concept_key}").json()["state"] == "suppressed"
    assert first.get("/api/search", params={"q": alias}).json()["concepts"] == []
    assert _tags(first, first_id) == {}


def test_uploaded_passages_publish_concepts_and_count_documents_independently(make_client):
    client = make_client()
    suffix = uuid.uuid4().hex[:6]
    names = (f"Janus{suffix}", f"Kallisto{suffix}")
    texts = [f"{names[0]} uses {names[1]} to archive encrypted audit receipts.\n\n"
             f"The storage guide confirms {names[0]} uses {names[1]} during receipt compaction.",
             f"Our recovery exercise verified that {names[0]} uses {names[1]} when restoring historical settlements."]
    documents = []
    for index, body in enumerate(texts):
        response = client.post("/api/documents", files={"file": (f"archive-{index}.txt", body.encode(), "text/plain")})
        assert response.status_code == 200, response.text
        document = client.get(f"/api/documents/{response.json()['id']}").json()
        documents.append(document)
        for passage in document["passages"]:
            _apply(passage["id"], names, relation=True, kind="passage")
            _apply(passage["id"], cached=True, kind="passage")
        concepts = client.get("/api/search", params={"q": names[0]}).json()["concepts"]
        left = next(c["id"] for c in concepts if c["name"] == names[0])
        concepts = client.get("/api/search", params={"q": names[1]}).json()["concepts"]
        right = next(c["id"] for c in concepts if c["name"] == names[1])
        edge = _edge(client, left, right)
        assert edge["support_count"] == index + 1
        assert edge["style"] == ("dashed" if index == 0 else "solid")
    assert edge["label"] == "uses" and edge["directed"] and edge["origin"] == "automatic"
    assert (edge["source"], edge["target"]) == (left, right)
    assert len(documents[0]["passages"]) == 2
    evidence = client.get(f"/api/graph/links/{edge['link_id']}/evidence").json()
    claim = next(c for c in evidence["claims"] if c["predicate"] == "uses")
    assert {(s["document_id"], s["locator"], s["quote"]) for s in claim["sources"]} == {
        (document["id"], passage["locator"], passage["text"])
        for document in documents for passage in document["passages"]
    }
    local = client.get("/api/graph/local", params={"concept_id": left}).json()
    assert {node["id"] for node in local["nodes"] if node["type"] == "document"} == {f"d:{d['id']}" for d in documents}
    hits = client.get("/api/search", params={"q": names[0]}).json()["documents"]
    assert {hit["document_id"] for hit in hits} == {d["id"] for d in documents}
    assert client.delete(f"/api/documents/{documents[0]['id']}").status_code == 200
    for passage in documents[0]["passages"]:
        _apply(passage["id"], kind="passage")
    remaining = _edge(client, left, right)
    assert remaining["support_count"] == 1 and remaining["style"] == "dashed"


def _profile_work(profile_id):
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.ml.adapter import apply_profile, apply_vocabulary

    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        apply_profile(db, profile_id)
        apply_vocabulary(db, f"route:{profile_id}:")
        db.commit()


def _confirm_topic(client, item_id, kind, topic):
    """An explicit new user action, never an upgrade of an old generic vote."""
    response = client.get(f"/api/items/{item_id}/topic-feedback", params={"q": topic})
    assert response.status_code == 200, response.text
    context = response.json()
    choice, = [row for row in context["topics"] if row["name"] == topic]
    response = client.put(f"/api/items/{item_id}/topic-feedback", json={
        "kind": kind, "expected_context": context["context_token"],
        "topics": [{"concept_id": choice["concept_id"], "identity_revision": choice["identity_revision"]}]})
    assert response.status_code == 200, response.text
    return response.json()


def test_expertise_outcomes_route_old_questions_once_and_respect_overrides(make_client, admin_client, obsolete_policy):
    expert, asker, reader = make_client(account=False), make_client(account=False), make_client(account=False)
    suffix = uuid.uuid4().hex[:6]
    username = f"keeper{suffix}"
    for client, name in ((expert, username), (asker, f"asker{suffix}"), (reader, f"reader{suffix}")):
        response = client.post("/api/auth/signup", json={"username": name, "password": "a-good-password"})
        assert response.status_code == 200, response.text
    profile_id = expert.get("/api/profile").json()["id"]
    name = f"Iapetus{suffix}"
    note = _capture(expert, f"{name} needs a fresh checksum before a settlement replay.")
    _apply(note, [name])
    cid = _tags(expert, note)[name]
    old_question = asker.post("/api/questions", json={"body": f"How is {name} restored from backup?"}).json()["id"]
    own_question = expert.post("/api/questions", json={"body": f"Who maintains {name} receipts?"}).json()["id"]
    answered = asker.post("/api/questions", json={"body": f"Which {name} checkpoint is recoverable?"}).json()["id"]
    answer = expert.post(f"/api/questions/{answered}/answers", json={"body": f"Use the {name} checkpoint whose receipt count matches the journal."}).json()["id"]
    _apply(answer, [name])
    assert asker.post(f"/api/questions/{answered}/accept", json={"answer_id": answer}).status_code == 200
    assert reader.post(f"/api/items/{note}/helped").status_code == 200
    _confirm_topic(asker, answer, "accepted", name)
    _confirm_topic(reader, note, "helped", name)
    _profile_work(profile_id)
    assert not any(row["label"] == username for row in reader.get("/api/expertise").json())

    last_note = _capture(expert, f"Inspect the {name} retention manifest before exporting historical receipts.")
    _apply(last_note, [name])
    assert reader.post(f"/api/items/{last_note}/endorse").status_code == 200
    _profile_work(profile_id)
    assert not any(row["label"] == username for row in reader.get("/api/expertise").json()), "Generic endorsements supply no topic credit"
    _confirm_topic(reader, last_note, "helped", name)
    _profile_work(profile_id)
    assert next(row for row in reader.get("/api/expertise").json() if row["label"] == username)["areas"] == [name]
    questions = {row["id"]: row for row in expert.get("/api/questions").json()}
    assert questions[old_question]["matches_me"] and not questions[own_question]["matches_me"]
    notes = expert.get("/api/notifications").json()["notifications"]
    assert {row["item_id"] for row in notes if row["kind"] == "expertise_match"} == {old_question}
    routed = [row for row in notes if row["item_id"] == old_question]
    assert len(routed) == 1
    _profile_work(profile_id)
    notes = expert.get("/api/notifications").json()["notifications"]
    assert len([row for row in notes if row["item_id"] == old_question]) == 1

    finding = _finding(admin_client, "expertise", profile_id=profile_id, concept_id=cid)
    assert admin_client.delete(f"/api/admin/expertise/{finding['canonical_id']}").status_code == 200
    _profile_work(profile_id)
    assert not any(row["label"] == username for row in reader.get("/api/expertise").json())
    _decision(admin_client, finding["key"], "automatic")
    _profile_work(profile_id)
    assert any(row["label"] == username for row in reader.get("/api/expertise").json())
    assert expert.delete(f"/api/items/{last_note}").status_code == 200
    assert not any(row["label"] == username for row in reader.get("/api/expertise").json())
    _apply(last_note)
    _profile_work(profile_id)
    assert not any(row["label"] == username for row in reader.get("/api/expertise").json())
    _decision(admin_client, finding["key"], "pinned")
    _profile_work(profile_id)
    assert any(row["label"] == username for row in reader.get("/api/expertise").json())


def _apply_current_definition(item_id, name, alias, *, field_score=0.995, entity_score=0.995,
                              spans=None, cached=False, revision=None, rule="denotes"):
    """Supply synthetic current records at the expensive-inference boundary."""
    from app.db import SessionLocal
    from sqlalchemy import text
    from app.ml import adapter, policy, syntax
    from app.ml.runtime import inference_version
    from app.ml.sources import digest, snapshot

    models = {"extractor": {"revision": "72ac19b486cd4557424c8d61114e7530c243e9b0"},
              "embeddings": {"revision": "fixture-embedding"},
              "syntax": {"revision": "272a31e9d8530d1e075351d30a462d7e80e31da23574f1b274e200f3fff35bf5"}}
    metadata = (inference_version(models), models["embeddings"]["revision"], 1024)
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET pipeline_version=:version WHERE id=1"), {"version": metadata[0] + ":" + policy.VERSION})
        source = snapshot(db, "item", item_id)
        if cached:
            result, metadata = adapter.cached_result(db, source, metadata[0])
        else:
            def field(value, score):
                start = source.text.index(value)
                return {"text": value, "start": start, "end": start + len(value), "confidence": score}

            entities = [field(value, score) for value, score in (spans or [(name, entity_score)])]
            result = {"concepts": [{"name": e["text"], "start": e["start"], "end": e["end"],
                                     "score": e["confidence"], "label": "named entity"} for e in entities],
                      "relations": [], "chunks": [], "conflict_definitions": [],
                      "conflict_coverage_revision": syntax.CONFLICT_REVISION, "corroborated_definitions": [{
                          "full_name": field(name, field_score), "short_name": field(alias, field_score),
                          "source_text_hash": digest(source.text), "syntax_rules": [rule],
                          "syntax_rule_revision": revision or syntax.REVISION,
                          "alias_model_revision": models["extractor"]["revision"],
                          "syntax_model_revision": models["syntax"]["revision"]}]}
            result = current_synthetic_result(result, source.text)
        adapter.apply_source(db, "item", item_id, source, result, *metadata)
        db.commit()


@pytest.mark.parametrize("removal", ["delete", "edit", "private"])
def test_single_exact_definition_publishes_and_retracts_without_role_score_leak(make_client, admin_client, removal):
    from sqlalchemy import text
    from app.db import SessionLocal

    owner = make_client()
    name, alias = "Prospective Trace " + removal.title(), "PT" + removal
    item = _capture(owner, f"{alias} denotes {name} in this instrument log.")
    _apply_current_definition(item, name, alias, field_score=0.85)
    cid = _tags(owner, item)[name]
    key = _finding(admin_client, "alias", alias=alias, concept_id=cid)["key"]
    def published():
        return [c["id"] for c in owner.get("/api/search", params={"q": alias}).json()["concepts"]]
    assert published() == [cid]
    detail = admin_client.get(f"/api/ml/findings/{key}").json()
    assert detail["features"]["independent_groups"] == 1
    assert _finding(admin_client, "concept", name=name)["raw_model_score"] == 0.995
    assert _finding(admin_client, "mention", source_id=item, concept_id=cid)["raw_model_score"] == 0.995
    _apply_current_definition(item, name, alias, cached=True)
    _decision(admin_client, key, "suppressed")
    assert published() == []
    _apply_current_definition(item, name, alias, cached=True)
    assert published() == []
    _decision(admin_client, key, "automatic")
    assert published() == [cid]
    if removal == "delete":
        assert owner.delete(f"/api/items/{item}").status_code == 200
    elif removal == "edit":
        assert owner.put(f"/api/items/{item}", json={"body": "The instrument log no longer contains a name."}).status_code == 200
    else:
        with SessionLocal() as db:
            db.execute(text("UPDATE knowledge_items SET visibility='private' WHERE id=:id"), {"id": item})
            db.commit()
    assert published() == []
    _apply(item)
    assert published() == []


@pytest.mark.parametrize("name,alias,legacy_score,role_score", [
    ("Harbor Deeds", "Harbor Titles", 0.9973875880241394, 0.528182327747345),
    ("opal seismometer", "OS", 0.9704638719558716, 0.7214248180389404),
    ("vine bud map", "VBM", 0.9534861445426941, 0.659421443939209),
])
def test_single_definition_cannot_borrow_legacy_score(make_client, admin_client, name, alias, legacy_score, role_score):
    owner = make_client()
    anchor = _capture(owner, f"{name} stores the observation record.")
    _apply(anchor, [name])
    item = _capture(owner, f"{name} is also called {alias} in the observation log.")
    _apply_current_definition(item, name, alias, field_score=role_score, spans=[(name, legacy_score), (alias, legacy_score)], rule="passive_or_nominal_naming")
    finding = _finding(admin_client, "alias", alias=alias)
    assert finding["raw_model_score"] == legacy_score and finding["state"] == "held"
    assert owner.get("/api/search", params={"q": alias}).json()["concepts"] == []


def test_hidden_partial_expansion_blocks_single_definition_until_source_correction(make_client, admin_client):
    owner = make_client()
    name, alias = "Saffron Sampler", "SSamp"
    body = "SSamp is another name for Saffron Sampler on our rack list. I compared two playback starts for the same sample."
    item = _capture(owner, body)
    _apply_current_definition(item, name, alias, spans=[(name, 0.995), (alias, 0.6), ("Saffron", 0.6)], rule="copular_name_for")
    assert _finding(admin_client, "concept", name="Saffron")["canonical_id"] is None
    assert _finding(admin_client, "alias", alias=alias)["state"] == "held"
    assert owner.get("/api/search", params={"q": alias}).json()["concepts"] == []
    assert owner.put(f"/api/items/{item}", json={"body": "SSamp denotes Saffron Sampler on our rack list."}).status_code == 200
    _apply_current_definition(item, name, alias)
    assert [c["name"] for c in owner.get("/api/search", params={"q": alias}).json()["concepts"]] == [name]


def test_single_definition_preserves_existing_short_name_canonical(make_client, admin_client):
    owner = make_client()
    name, alias = "Prospective Canonical Collector", "PCC"
    manual = admin_client.post("/api/admin/concepts", json={"name": alias, "aliases": []}).json()
    item = _capture(owner, f"{alias} denotes {name} in the observation log.")
    _apply_current_definition(item, name, alias)
    target = _finding(admin_client, "concept", name=name)
    assert target["canonical_id"] != manual["id"]
    assert _finding(admin_client, "alias", alias=alias, concept_id=target["canonical_id"])["state"] == "held"
    assert [c["id"] for c in owner.get("/api/search", params={"q": alias}).json()["concepts"]] == [manual["id"]]


def test_single_definition_waits_for_old_source_index_then_redecides_in_pages(make_client, admin_client):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter

    owner = make_client()
    old = _capture(owner, "Historical Observation Register stores calibrated readings.")
    _apply(old, ["Historical Observation Register"])
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_sources SET result=json_remove(result,'$.definitions_indexed') WHERE kind='item' AND id=:id"), {"id": old})
        db.commit()
    name, alias = "Prospective Indexed Register", "PIR"
    item = _capture(owner, f"{alias} denotes {name} in the observation log.")
    _apply_current_definition(item, name, alias)
    assert _finding(admin_client, "alias", alias=alias)["state"] == "held"
    assert owner.get("/api/search", params={"q": alias}).json()["concepts"] == []
    _apply(old, ["Historical Observation Register"])
    with SessionLocal() as db:
        cursor = ""
        while True:
            adapter.apply_vocabulary(db, "aliases:" + cursor)
            later = db.execute(text("SELECT source_id FROM ml_jobs WHERE source_kind='vocabulary' AND source_id LIKE 'aliases:%' AND source_id>:current ORDER BY source_id LIMIT 1"), {"current": "aliases:" + cursor}).scalar()
            if later is None:
                break
            cursor = later.split(":", 1)[1]
        db.commit()
    assert [c["name"] for c in owner.get("/api/search", params={"q": alias}).json()["concepts"]] == [name]


def test_single_definition_requires_current_rule_and_selected_model_contract(make_client, admin_client):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter

    owner = make_client()
    name, alias = "Prospective Version Register", "PVR"
    item = _capture(owner, f"{alias} denotes {name} in the observation log.")
    _apply_current_definition(item, name, alias, revision="obsolete-rules")
    assert _finding(admin_client, "alias", alias=alias)["state"] == "held"
    _apply_current_definition(item, name, alias)
    finding = _finding(admin_client, "alias", alias=alias)
    assert finding["state"] == "active"
    with SessionLocal() as db:
        db.execute(text("UPDATE ml_state SET pipeline_version='a-different-selected-model' WHERE id=1"))
        adapter.apply_vocabulary(db, "aliases:" + finding["key"][:-1])
        db.commit()
    assert owner.get("/api/search", params={"q": alias}).json()["concepts"] == []


def test_single_current_role_record_cannot_qualify_a_weak_entity(make_client, admin_client):
    owner = make_client()
    name, alias = "Prospective Weak Register", "PWR"
    item = _capture(owner, f"{alias} denotes {name} in the observation log.")
    _apply_current_definition(item, name, alias, field_score=0.999, entity_score=0.2)
    finding = _finding(admin_client, "concept", name=name)
    assert (finding["state"], finding["canonical_id"], finding["raw_model_score"]) == ("held", None, 0.2)
    assert owner.get("/api/search", params={"q": alias}).json()["concepts"] == []


def test_exact_definition_survives_higher_scoring_spelling_method(make_client, admin_client):
    owner = make_client()
    name, alias = "Prospective Spelling Register", "Prospective-Spelling-Register"
    item = _capture(owner, f"{name} is also called {alias} in the observation log.")
    _apply_current_definition(item, name, alias, field_score=0.99,
                              spans=[(name, 0.995), (alias, 0.999)], rule="passive_or_nominal_naming")
    finding = _finding(admin_client, "alias", alias=alias)
    assert finding["state"] == "active"
    assert [c["name"] for c in owner.get("/api/search", params={"q": alias}).json()["concepts"]] == [name]


def test_descriptor_word_after_a_multiword_name_publishes_the_name(make_client, admin_client):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from ml_relation_helpers import DeclaredGuard, analyze, declaration, span
    from ml_synthetic_records import current_synthetic_metadata, select_synthetic_pipeline

    body = "Clover Shuttle service uses Vela spectrograph readings."
    client = make_client()
    item_id = _capture(client, body)
    names = ("Clover Shuttle service", "Vela spectrograph")
    guard = DeclaredGuard(body, [declaration(body, names[0], "uses", names[1])])
    result = analyze(body, [span(body, name, score=.999) for name in names],
                     {"uses": [{"head": span(body, names[0]), "tail": span(body, names[1])}]}, full=True, guard=guard)
    result["chunks"] = []
    metadata = current_synthetic_metadata()
    with SessionLocal() as db:
        select_synthetic_pipeline(db, metadata)
        adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result, *metadata)
        db.commit()

    tags = _tags(client, item_id)
    assert set(tags) == {"Clover Shuttle", "Vela spectrograph"}
    relationship = _finding(admin_client, "relationship", predicate="uses")
    assert (relationship["payload"]["src_id"], relationship["payload"]["dst_id"]) == (
        tags["Clover Shuttle"], tags["Vela spectrograph"])
