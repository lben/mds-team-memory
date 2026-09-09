"""Mechanical integration proof with supplied encoder outputs, not model-quality evidence."""

import json
import uuid

import pytest


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
    from app.ml import adapter
    from app.ml.sources import snapshot

    metadata = ("fixture-extractor:fixture-embedding", "fixture-embedding", 1024)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
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
                      "relations": [], "chunks": []}
            if relation:
                result["relations"] = [{"head": spans[0], "tail": spans[1], "predicate": "uses",
                                        "score": 0.9, "start": 0, "end": len(source.text),
                                        "polarity": "positive", "literal_support": True}]
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


def test_expertise_outcomes_route_old_questions_once_and_respect_overrides(make_client, admin_client, obsolete_policy):
    expert, asker, reader = make_client(), make_client(), make_client()
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
    _profile_work(profile_id)
    assert not any(row["label"] == username for row in reader.get("/api/expertise").json())

    last_note = _capture(expert, f"Inspect the {name} retention manifest before exporting historical receipts.")
    _apply(last_note, [name])
    assert reader.post(f"/api/items/{last_note}/endorse").status_code == 200
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
