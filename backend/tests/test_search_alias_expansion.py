"""Public search vocabulary regressions; lexical retagging only, no inference."""

import sqlite3
import uuid

import pytest


@pytest.fixture(params=["single_word", "multiword"])
def lexical_alias_scenario(request, make_client, admin_client):
    from sqlalchemy import text

    from app.concepts import retag_item
    from app.db import SessionLocal
    from app.ml.models import Finding
    from app.models import ItemConcept, KnowledgeItem

    suffix = uuid.uuid4().hex[:8]
    name = f"Rivet{suffix} Gauge"
    alias = f"RG{suffix}" if request.param == "single_word" else f"Bench Meter{suffix}"
    author, reader = make_client(), make_client()
    created = []
    concept_id = None
    with SessionLocal() as db:
        previous = db.execute(text("SELECT automation_enabled FROM ml_state WHERE id=1")).scalar_one()
        # Keep retagging queued after vocabulary edits, so stale stored tags
        # remain available for the immediate-read regression below.
        db.execute(text("UPDATE ml_state SET automation_enabled=1 WHERE id=1"))
        db.commit()
    try:
        response = author.post("/api/capture", data={
            "body": f"We replaced the worn seal in {alias} before the afternoon inspection."})
        assert response.status_code == 200, response.text
        created.append(("items", response.json()["item"]["id"]))
        response = author.post("/api/questions", json={
            "body": f"Which replacement seal fits {alias} during the winter inspection?"})
        assert response.status_code == 200, response.text
        created.append(("questions", response.json()["id"]))

        # Both public sources precede the vocabulary. Simulate its ordinary
        # lexical backfill without supplying inferred mentions or model output.
        response = admin_client.post("/api/admin/concepts", json={"name": name, "aliases": [alias]})
        assert response.status_code == 200, response.text
        concept_id = response.json()["id"]
        ids = {item_id for _, item_id in created}
        with SessionLocal() as db:
            for item_id in ids:
                assert concept_id in retag_item(db, db.get(KnowledgeItem, item_id))
            db.commit()
            assert {row.item_id for row in db.query(ItemConcept).filter(
                ItemConcept.concept_id == concept_id)} == ids
            assert db.query(Finding).filter(
                Finding.kind == "mention", Finding.canonical_id == concept_id,
                Finding.state == "active").count() == 0
        for route, item_id in created:
            response = reader.get(f"/api/{route}/{item_id}")
            assert response.status_code == 200, response.text
            assert {row["id"] for row in response.json()["concepts"]} == {concept_id}
        yield {"name": name, "alias": alias, "concept_id": concept_id,
               "ids": ids, "created": created, "reader": reader, "admin": admin_client}
    finally:
        for route, item_id in created:
            assert author.delete(f"/api/{route}/{item_id}").status_code == 200
        if concept_id:
            assert admin_client.delete(f"/api/admin/concepts/{concept_id}").status_code == 200
        with SessionLocal() as db:
            db.execute(text("UPDATE ml_state SET automation_enabled=:previous WHERE id=1"),
                       {"previous": previous})
            db.commit()


def _search(scenario, query):
    response = scenario["reader"].get("/api/search", params={"q": query})
    assert response.status_code == 200, response.text
    return response.json()


def test_unquoted_canonical_and_alias_find_earlier_lexically_tagged_posts(lexical_alias_scenario):
    scenario = lexical_alias_scenario
    by_alias = _search(scenario, scenario["alias"])
    by_name = _search(scenario, scenario["name"])
    for result in (by_alias, by_name):
        assert scenario["concept_id"] in {row["id"] for row in result["concepts"]}
    alias_hits = {row["id"] for row in by_alias["items"]} & scenario["ids"]
    canonical_hits = {row["id"] for row in by_name["items"]} & scenario["ids"]
    assert alias_hits == scenario["ids"]
    assert canonical_hits == alias_hits


def test_removed_alias_cannot_restore_identity_from_stale_stored_tags(lexical_alias_scenario):
    from app.db import SessionLocal
    from app.models import ItemConcept

    scenario = lexical_alias_scenario
    response = scenario["admin"].put(f"/api/admin/concepts/{scenario['concept_id']}", json={
        "name": scenario["name"], "aliases": []})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert {row.item_id for row in db.query(ItemConcept).filter(
            ItemConcept.concept_id == scenario["concept_id"])} == scenario["ids"]
    for route, item_id in scenario["created"]:
        response = scenario["reader"].get(f"/api/{route}/{item_id}")
        assert response.status_code == 200, response.text
        assert scenario["concept_id"] not in {row["id"] for row in response.json()["concepts"]}
    assert scenario["concept_id"] not in {
        row["id"] for row in _search(scenario, scenario["alias"])["concepts"]}
    assert not scenario["ids"] & {row["id"] for row in _search(scenario, scenario["name"])["items"]}


@pytest.mark.parametrize("query,documents,expected", [
    ("What is RIVET   Gauge repair?", ["RG repair", "Rivet Gauge repair", "RGR repair", "repair"], {0, 1}),
    ("Rivet Gauge Recorder", ["RGR", "Rivet Gauge Recorder", "RG recorder", "RG"], {0, 1}),
    ('"Rivet Gauge"', ["Rivet Gauge", "RG", "Rivet separate Gauge"], {0}),
    ("Riv*", ["Rivet", "Riveter", "RG"], {0, 1}),
    ("Rivet Gauge*", ["Rivet Gauges", "RG", "RGR"], {0}),
    ("Rivet Gaugeboard", ["Rivet Gaugeboard", "RG", "Rivet Gauge"], {0}),
    ("What is RG?", ["RG", "Rivet Gauge", "Questionword"], {0, 1}),
])
def test_query_phrase_boundaries_and_literal_controls(query, documents, expected):
    from app.text import build_fts_match

    aliases = {
        "rivet gauge": ["Rivet Gauge", "RG"],
        "rg": ["Rivet Gauge", "RG"],
        "rivet gauge recorder": ["Rivet Gauge Recorder", "RGR"],
        "riv": ["Riv", "RG"],
        "what": ["what", "Questionword"],
    }
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE records USING fts5(body)")
        db.executemany("INSERT INTO records(rowid,body) VALUES (?,?)", enumerate(documents))
        hits = {row[0] for row in db.execute(
            "SELECT rowid FROM records WHERE records MATCH ?", (build_fts_match(query, aliases),))}
    assert hits == expected


def test_alias_display_quotes_remain_literal_fts_text():
    from app.text import build_fts_match

    unusual_alias = 'RG "West" OR bypass'
    aliases = {"rivet gauge": ["Rivet Gauge", unusual_alias]}
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE records USING fts5(body)")
        db.executemany("INSERT INTO records(rowid,body) VALUES (?,?)", enumerate([
            "Rivet Gauge", unusual_alias, "bypass", "West"]))
        hits = {row[0] for row in db.execute("SELECT rowid FROM records WHERE records MATCH ?", (
            build_fts_match("Rivet Gauge", aliases),))}
    assert hits == {0, 1}


def test_complete_alias_match_outranks_partial_canonical_match(app_modules):
    from app.searchsvc import _coverage, _rank
    from app.text import search_query_groups

    groups = search_query_groups("What is Rivet Gauge maintenance?", {
        "rivet gauge": ["Rivet Gauge", "RG"]})
    complete = {"id": "complete", "coverage": _coverage("RG maintenance checklist", groups), "score": 0.0}
    partial = {"id": "partial", "coverage": _coverage("Rivet Gauge seal replacement", groups), "score": 100.0}
    assert complete["coverage"] == 1.0
    assert partial["coverage"] == 0.5
    assert [row["id"] for row in _rank([partial, complete], group=True)] == ["complete", "partial"]
