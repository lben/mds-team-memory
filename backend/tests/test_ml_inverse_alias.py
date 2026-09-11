"""Public regressions from observed development records, not quality evidence."""
import copy
import json
from pathlib import Path

import pytest

from test_ml_alias_conflicts import automated
from test_ml_identity_routing import capture, embedding_generation_isolation, names

CASES = json.loads((Path(__file__).parent / "fixtures/inverse_alias_recorded.json").read_text())["cases"]


def apply(item, record, *, cached=False, empty=False, roles=True):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter, policy, runtime, syntax
    from app.ml.sources import snapshot

    with SessionLocal() as db:
        source = snapshot(db, "item", item)
        parts = record["original_metadata"]["model_version"].split(":")
        models = {role: {"revision": revision} for role, revision in zip(
            ("extractor", "embeddings", "syntax"), parts[:3])}
        version = runtime.inference_version(models)
        metadata = (version, record["original_metadata"]["embedding_version"], 1024)
        db.execute(text("UPDATE ml_state SET pipeline_version=:version WHERE id=1"),
                   {"version": version + ":" + policy.VERSION})
        result = copy.deepcopy(record["result"]) if source else None
        if result:
            # These original definitions have no fronted context adjunct. Their
            # retained spans and scores are replayed, not recertified inference.
            for definition in result.get("corroborated_definitions", []):
                assert definition["syntax_rule_revision"].startswith("r5-copular-development:")
                definition["syntax_rule_revision"] = syntax.REVISION
            if not roles:
                result["corroborated_definitions"] = []
        if cached and source:
            result, metadata = adapter.cached_result(db, source, version)
        if empty and result:
            result.update(concepts=[], relations=[], corroborated_definitions=[], conflict_definitions=[])
        adapter.apply_source(db, "item", item, source, result, *metadata)
        db.commit()


def replay(items, records):
    for item, record in zip(items, records):
        apply(item, record, cached=True)


def terms():
    from app.db import SessionLocal
    from app.ml import effective

    with SessionLocal() as db:
        return {term.term: (term.concept_id, term.is_canonical) for term in effective.terms(db)}


def cleanup(clients, items, case, records):
    for item, post, record in reversed(list(zip(items, case["posts"], records))):
        clients[post["actor"]].delete(f"/api/items/{item}")
        apply(item, record)


@pytest.mark.parametrize("saved", CASES, ids=lambda saved: saved["case"]["id"])
def test_late_exact_definition_keeps_existing_id_and_score_ownership(make_client, admin_client, saved):
    from app.ml.sources import finding_key

    case, records = saved["case"], saved["records"]
    pair = case["expect"]["aliases"][0]
    full, short = pair["canonical"], pair["alias"]
    clients, items = capture(make_client, case)
    reader = next(iter(clients.values()))
    full_key, short_key = finding_key("concept", full.casefold()), finding_key("concept", short.casefold())
    try:
        for item, record in zip(items, records):
            apply(item, record)
        original = admin_client.get(f"/api/ml/findings/{short_key}").json()["canonical_id"]
        replay(items, records)
        replay(items, records)
        current = terms()
        assert current[short.casefold()] == (original, True)
        full_detail = admin_client.get(f"/api/ml/findings/{full_key}").json()
        if case["id"] == "acronym_dev_019":
            assert full_detail["canonical_id"] and full_detail["canonical_id"] != original
            assert current[full.casefold()] == (full_detail["canonical_id"], True)
            return
        assert current[full.casefold()] == (original, False)
        assert names(reader, full)[short] == original
        assert full_detail["canonical_id"] is None
        short_detail = admin_client.get(f"/api/ml/findings/{short_key}").json()
        for evidence in short_detail["evidence"]:
            assert not evidence.get("identity_routes")
            assert evidence["quote"].casefold() == short.casefold()
            index = items.index(evidence["source_id"])
            assert any(span["name"].casefold() == short.casefold()
                       and span["score"] == evidence["raw_score"]
                       for span in records[index]["result"]["concepts"])
        for evidence in full_detail["evidence"]:
            assert not evidence.get("identity_routes")
        alias_key = finding_key("alias", full.casefold(), original)
        alias = admin_client.get(f"/api/ml/findings/{alias_key}").json()
        assert alias["evidence"]
        for evidence in alias["evidence"]:
            certificate = evidence["identity_routes"][0]
            assert certificate["direction"] == "inverse"
            assert certificate["concept_key"] == short_key
            assert certificate["declaration_key"] == full_key
            assert certificate["canonical_id"] == original and certificate["anchors"]
        for item, post in zip(items, case["posts"]):
            if full in post["body"]:
                assert original in {c["id"] for c in reader.get(f"/api/items/{item}").json()["concepts"]}
        # Ownership conflicts are explicit and cannot adopt the declaration.
        conflict = admin_client.put(f"/api/ml/findings/{full_key}/decision", json={"mode": "pinned"})
        assert conflict.status_code == 400
        assert admin_client.get(f"/api/ml/findings/{full_key}").json()["canonical_id"] is None
        assert admin_client.put(f"/api/ml/findings/{alias_key}/decision", json={"mode": "suppressed"}).status_code == 200
        assert full.casefold() not in terms()
        assert terms()[short.casefold()] == (original, True)
        replay(items, records)
        assert full.casefold() not in terms()
        assert admin_client.put(f"/api/ml/findings/{alias_key}/decision", json={"mode": "automatic"}).status_code == 200
        replay(items, records)
        replay(items, records)
        assert terms()[full.casefold()] == (original, False)
    finally:
        cleanup(clients, items, case, records)


@pytest.mark.parametrize("change", ["witness", "native_anchor", "full_suppression"])
def test_inverse_dependency_withdraws_before_replay_with_pinned_short(make_client, admin_client, change):
    from app.ml.sources import finding_key

    saved = next(saved for saved in CASES if saved["case"]["id"] == "acronym_dev_021")
    case, records = saved["case"], saved["records"]
    clients, items = capture(make_client, case)
    short, full = "SSR", "Source Sync Receipt"
    short_key, full_key = finding_key("concept", "ssr"), finding_key("concept", full.casefold())
    try:
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        original = terms()[short.casefold()][0]
        assert terms()[full.casefold()] == (original, False)
        assert admin_client.put(f"/api/ml/findings/{short_key}/decision", json={"mode": "pinned"}).status_code == 200
        if change == "full_suppression":
            assert admin_client.put(f"/api/ml/findings/{full_key}/decision", json={"mode": "suppressed"}).status_code == 200
        else:
            index = 2 if change == "witness" else 0
            owner = clients[case["posts"][index]["actor"]]
            assert owner.put(f"/api/items/{items[index]}", json={"body": "The old record has been retired."}).status_code == 200
        assert terms()[short.casefold()] == (original, True)
        assert full.casefold() not in terms()
        if change == "full_suppression":
            replay(items, records)
            assert full.casefold() not in terms()
            assert admin_client.put(f"/api/ml/findings/{full_key}/decision", json={"mode": "automatic"}).status_code == 200
        else:
            apply(items[index], records[index], empty=True)
            replay(items, records)
            assert full.casefold() not in terms()
            assert owner.put(f"/api/items/{items[index]}", json={"body": records[index]["body"]}).status_code == 200
            apply(items[index], records[index])
        replay(items, records)
        replay(items, records)
        assert terms()[full.casefold()] == (original, False)
        assert admin_client.get(f"/api/ml/findings/{full_key}").json()["canonical_id"] is None
    finally:
        admin_client.put(f"/api/ml/findings/{short_key}/decision", json={"mode": "automatic"})
        cleanup(clients, items, case, records)


def test_second_established_id_is_not_joined_after_inverse_alias_withdrawal(make_client, admin_client):
    from app.ml.sources import finding_key

    saved = CASES[0]
    case, records = saved["case"], saved["records"]
    clients, items = capture(make_client, case)
    full_key = finding_key("concept", "packet gap monitor")
    try:
        for item, record in zip(items, records):
            apply(item, record)
        replay(items, records)
        original = terms()["pgm"][0]
        alias_key = finding_key("alias", "packet gap monitor", original)
        assert admin_client.put(f"/api/ml/findings/{alias_key}/decision", json={"mode": "suppressed"}).status_code == 200
        assert admin_client.put(f"/api/ml/findings/{full_key}/decision", json={"mode": "pinned"}).status_code == 200
        second = admin_client.get(f"/api/ml/findings/{full_key}").json()["canonical_id"]
        assert second and second != original
        assert admin_client.put(f"/api/ml/findings/{alias_key}/decision", json={"mode": "automatic"}).status_code == 200
        replay(items, records)
        assert terms()["pgm"] == (original, True)
        assert terms()["packet gap monitor"] == (second, True)
        assert admin_client.get(f"/api/ml/findings/{alias_key}").json()["state"] != "active"
    finally:
        admin_client.put(f"/api/ml/findings/{full_key}/decision", json={"mode": "automatic"})
        cleanup(clients, items, case, records)
