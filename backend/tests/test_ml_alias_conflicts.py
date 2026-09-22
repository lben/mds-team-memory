"""Real API regressions with retained inference and parser outputs; no quality claim."""
import copy
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from ml_synthetic_records import current_synthetic_result


CASES = json.loads((Path(__file__).parent / "fixtures/alias_conflict_regressions.json").read_text())["cases"]


def capture_post(client, post):
    if post["kind"] == "question":
        response = client.post("/api/questions", json={"body": post["body"]})
        assert response.status_code == 200, response.text
        return response.json()["id"]
    response = client.post("/api/capture", data={"body": post["body"]})
    assert response.status_code == 200, response.text
    return response.json()["item"]["id"]


@pytest.fixture(autouse=True)
def automated(app_modules):
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
        db.execute(text("UPDATE ml_state SET automation_enabled=:value WHERE id=1"), {"value": previous})
        db.commit()


def apply_post(item_id, post, *, cached=False):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter, policy, runtime, syntax
    from app.ml.sources import digest, snapshot

    models = {"extractor": {"revision": "72ac19b486cd4557424c8d61114e7530c243e9b0"},
              "embeddings": {"revision": "fixture-embedding"},
              "syntax": {"revision": "272a31e9d8530d1e075351d30a462d7e80e31da23574f1b274e200f3fff35bf5"}}
    version = runtime.inference_version(models)
    with SessionLocal() as db:
        source = snapshot(db, "item", item_id)
        if source is None:
            adapter.apply_source(db, "item", item_id, None, None, version, models["embeddings"]["revision"], 1024)
            db.commit()
            return
        if cached:
            result, metadata = adapter.cached_result(db, source, version)
            adapter.apply_source(db, "item", item_id, source, result, *metadata)
            db.commit()
            return
        result = copy.deepcopy(post["result"])
        result["chunks"] = []
        # Saved fixtures remain r4 predictions. Reusing their fixed scores and
        # spans with current identity metadata is a synthetic application input,
        # not a fresh model observation or new quality evidence.
        for definition in result.get("corroborated_definitions", []):
            assert definition["syntax_rule_revision"].startswith("r4:")
            assert "copular_name_for" not in definition["syntax_rules"]
            definition["syntax_rule_revision"] = syntax.REVISION
        possible = syntax.propose(post["syntax"], conflict=True) if post["syntax"] else [
            {"full_name": {k: d["full_name"][k] for k in ("text", "start", "end")},
             "short_name": {k: d["short_name"][k] for k in ("text", "start", "end")},
             "rule": d["syntax_rules"][0]} for d in result.get("corroborated_definitions", [])]
        result["conflict_definitions"] = [{**p, "source_text_hash": digest(source.text)} for p in possible]
        result["conflict_coverage_revision"] = syntax.CONFLICT_REVISION
        # Port the fixed scores/identity spans into an explicit synthetic
        # current-contract input; the recorded model fixture remains unchanged.
        result = current_synthetic_result(result)
        db.execute(text("UPDATE ml_state SET pipeline_version=:version WHERE id=1"),
                   {"version": version + ":" + policy.VERSION})
        adapter.apply_source(db, "item", item_id, source, result, version, models["embeddings"]["revision"], 1024)
        db.commit()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("order", ["post_order", "competing_first"])
def test_current_competing_definition_blocks_public_alias(make_client, case, order):
    clients, items = [], []
    for post in case["posts"]:
        client = make_client()
        item = capture_post(client, post)
        clients.append(client)
        items.append(item)
    competing = int(case["competing_phase"].split("_")[1]) - 1
    indices = list(range(len(items)))
    if order == "competing_first":
        positive = next(i for i, post in enumerate(case["posts"]) if post["result"].get("corroborated_definitions"))
        indices = [competing] + [i for i in indices if i not in (competing, positive)] + [positive]
    for index in indices:
        apply_post(items[index], case["posts"][index])
    try:
        names = [c["name"] for c in clients[0].get("/api/search", params={"q": case["alias"]}).json()["concepts"]]
        assert case["published_name"] not in names
        assert case["competing_name"] not in names
        # Replay all retained current caches: a conflict must stay effective.
        for item, post in zip(items, case["posts"]):
            apply_post(item, post, cached=True)
        assert case["published_name"] not in [c["name"] for c in clients[0].get(
            "/api/search", params={"q": case["alias"]}).json()["concepts"]]
        removed = items.pop(competing)
        owner = clients.pop(competing)
        assert owner.delete(f"/api/items/{removed}").status_code == 200
        apply_post(removed, case["posts"][competing])
        assert case["published_name"] in [c["name"] for c in clients[0].get(
            "/api/search", params={"q": case["alias"]}).json()["concepts"]]
    finally:
        for client, item in zip(clients, items):
            assert client.delete(f"/api/items/{item}").status_code == 200
            apply_post(item, None)


def insert_token(parsed, index, word, *, dep="advmod", quote=False):
    """Mechanical assertion-control counterpart, not a new parser inference."""
    value = copy.deepcopy(parsed)
    tokens = value["tokens"]
    position = tokens[index]["start"] if index < len(tokens) else len(value["body"])
    addition = word if quote else word + " "
    shift = lambda i: i + (i >= index)
    for token in tokens:
        for key in ("i", "head"):
            token[key] = shift(token[key])
        for key in ("children", "ancestors"):
            token[key] = [shift(i) for i in token[key]]
        if token["start"] >= position:
            token["start"] += len(addition)
            token["end"] += len(addition)
    head = shift(index) if index < len(tokens) else tokens[-1]["head"]
    tokens.insert(index, {"i": index, "text": word, "whitespace": "" if quote else " ",
        "start": position, "end": position + len(word), "lemma": word.casefold(),
        "pos": "PUNCT" if quote else "ADV", "tag": "``" if quote else "RB", "dep": dep,
        "head": head, "children": [], "ancestors": [head], "is_quote": quote})
    if head != index:
        tokens[head]["children"].append(index)
    for chunk in value["noun_chunks"]:
        chunk["start_token"] = shift(chunk["start_token"])
        chunk["end_token"] += chunk["end_token"] > index
        chunk["root_token"] = shift(chunk["root_token"])
        chunk["end"] += len(addition) if chunk["end"] > position else 0
    for sentence in value["sentences"]:
        if sentence["start_token"] > index:
            sentence["start_token"] += 1
        if sentence["end_token"] >= index:
            sentence["end_token"] += 1
    value["body"] = value["body"][:position] + addition + value["body"][position:]
    return value


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("control", ["negative", "hypothetical", "quoted", "question", "question_source"])
def test_nonasserted_counterpart_does_not_block_public_alias(make_client, case, control):
    from app.ml import syntax

    competing = case["posts"][int(case["competing_phase"].split("_")[1]) - 1]
    positive = next(p for p in case["posts"] if p["result"].get("corroborated_definitions"))
    parsed = competing["syntax"]
    trigger = next(t["i"] for t in parsed["tokens"] if t["lemma"] in {"mean", "shorten", "stand"})
    if control == "negative":
        parsed = insert_token(parsed, trigger, "never", dep="neg")
    elif control == "hypothetical":
        parsed = insert_token(parsed, trigger, "perhaps")
    elif control == "quoted":
        parsed = insert_token(parsed, 0, '"', dep="punct", quote=True)
        parsed = insert_token(parsed, len(parsed["tokens"]), '"', dep="punct", quote=True)
    elif control == "question":
        parsed = copy.deepcopy(parsed)
        stop = next(t for t in parsed["tokens"] if t["text"] == ".")
        parsed["body"] = parsed["body"][:stop["start"]] + "?" + parsed["body"][stop["end"]:]
        stop["text"], stop["lemma"] = "?", "?"
    assert bool(syntax.propose(parsed, conflict=True)) == (control == "question_source")
    unsupported = {"body": parsed["body"], "kind": "question" if control.startswith("question") else "note",
                   "syntax": parsed, "result": {"concepts": [], "relations": [], "corroborated_definitions": []}}
    client = make_client()
    items = []
    try:
        for post in (positive, unsupported):
            item = capture_post(client, post)
            items.append(item)
            apply_post(item, post)
        assert case["published_name"] in [c["name"] for c in client.get(
            "/api/search", params={"q": case["alias"]}).json()["concepts"]]
    finally:
        for item in items:
            assert client.delete(f"/api/items/{item}").status_code == 200
            apply_post(item, None)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_syntax_only_definition_never_supplies_publication_evidence(make_client, admin_client, case):
    from app.ml.sources import finding_key

    post = copy.deepcopy(case["posts"][int(case["competing_phase"].split("_")[1]) - 1])
    post["result"] = {"concepts": [], "relations": [], "corroborated_definitions": []}
    client = make_client()
    response = client.post("/api/capture", data={"body": post["body"]})
    assert response.status_code == 200
    item = response.json()["item"]["id"]
    try:
        apply_post(item, post)
        key = finding_key("alias_definition", case["alias"].casefold(), case["competing_name"].casefold())
        detail = admin_client.get(f"/api/ml/findings/{key}").json()
        assert detail["kind"] == "alias_definition"
        assert all(e["raw_score"] == 0 and e["conflict_only"] for e in detail["evidence"])
        concept = admin_client.get(f"/api/ml/findings/{detail['payload']['concept_key']}").json()
        assert concept["evidence"] == []
        assert client.get(f"/api/items/{item}").json()["concepts"] == []
        assert case["competing_name"] not in [c["name"] for c in client.get(
            "/api/search", params={"q": case["alias"]}).json()["concepts"]]
    finally:
        assert client.delete(f"/api/items/{item}").status_code == 200
        apply_post(item, None)


@pytest.mark.parametrize("coverage", ["old_structure", "old_revision"])
def test_old_coverage_requires_reprocessing_before_single_source_publication(make_client, coverage):
    from sqlalchemy import text
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.models import Source
    from app.ml.sources import snapshot

    client = make_client()
    empty = {"body": "Archive status remains recorded.", "kind": "note", "syntax": None,
             "result": {"concepts": [], "relations": [], "corroborated_definitions": []}}
    positive = CASES[0]["posts"][0]
    items = []
    try:
        for post in (empty, positive):
            response = client.post("/api/capture", data={"body": post["body"]})
            assert response.status_code == 200
            item = response.json()["item"]["id"]
            items.append(item)
            apply_post(item, post)
            if post is empty:
                with SessionLocal() as db:
                    stored = db.get(Source, ("item", item))
                    data = json.loads(stored.result)
                    if coverage == "old_structure":
                        data["definitions_indexed"] = 1
                        del data["conflict_definitions"], data["conflict_coverage_revision"]
                    else:
                        data["conflict_coverage_revision"] = "previous-conflict-rules"
                    stored.result = json.dumps(data)
                    db.commit()
                    assert adapter.cached_result(db, snapshot(db, "item", item), stored.model_version) is None
        search = lambda: [c["name"] for c in client.get("/api/search", params={"q": "MPL"}).json()["concepts"]]
        assert "Monitor Position Log" not in search()
        apply_post(items[0], empty)
        with SessionLocal() as db:
            # The last coverage update schedules bounded alias reconsideration.
            assert db.execute(text("SELECT 1 FROM ml_jobs WHERE source_kind='vocabulary' AND source_id='aliases:'")).first()
            adapter.apply_vocabulary(db, "aliases:")
            db.commit()
        assert "Monitor Position Log" in search()
    finally:
        for item in items:
            assert client.delete(f"/api/items/{item}").status_code == 200
            apply_post(item, None)


@pytest.mark.parametrize("case", [CASES[2], CASES[3]], ids=lambda case: case["id"])
def test_arbitrary_relative_clause_is_not_a_heading_assertion(case):
    from app.ml import syntax

    parsed = copy.deepcopy(case["posts"][int(case["competing_phase"].split("_")[1]) - 1]["syntax"])
    trigger = next(t for t in parsed["tokens"] if t["lemma"] in {"mean", "stand"})
    # Hold source words/arguments fixed and exercise a supplied relative-clause parse.
    trigger["dep"] = "relcl"
    assert syntax.propose(parsed, conflict=True) == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_actual_runtime_preserves_conflict_candidates_without_changing_raw_records(monkeypatch, case):
    from app.ml import relation_syntax, runtime, syntax
    from app.ml.sources import digest

    post = case["posts"][int(case["competing_phase"].split("_")[1]) - 1]
    model = object.__new__(runtime.LocalModels)
    model.entity_schema, model.relation_schema, model.alias_schema = "generic_entities", "relations", "alias_roles"
    model.manifest = {"models": {"extractor": {"revision": "recorded-extractor"}, "syntax": {"revision": "recorded-syntax"}}}
    model.tokenizer = lambda body, **kwargs: {"offset_mapping": [m.span() for m in re.finditer(r"\S+", body)]}
    model.extractor = SimpleNamespace(extract=lambda body, schema, **kwargs: post["raw"][schema])
    model.syntax = lambda body: post["syntax"]
    monkeypatch.setattr(syntax, "candidates", lambda parsed, **kwargs: syntax.propose(parsed, **kwargs))
    # These retained parser roles are already serialized. Exercise the real new
    # relationship guard over them, without claiming a fresh parser observation.
    monkeypatch.setattr(relation_syntax, "serialize", lambda parsed: parsed)
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b"\0" * 4))
    model.embedding = SimpleNamespace(encode=lambda body, **kwargs: vector)
    result = model.analyze(post["body"])
    assert result["concepts"] == post["result"]["concepts"]
    # The current grammar deliberately recomputes assertion decisions. Keep
    # extraction scores and argument geometry fixed; the retired decisions and
    # the outer evidence window are both outputs of the changed grammar.
    raw_fields = ("head", "tail", "predicate", "score")
    assert [{key: row[key] for key in raw_fields} for row in result["relations"]] == [
        {key: row[key] for key in raw_fields} for row in post["result"]["relations"]]
    assert result["relation_guard_revision"] == relation_syntax.REVISION
    assert all(row["relation_guard_revision"] == relation_syntax.REVISION for row in result["relations"])
    assert result["corroborated_definitions"] == post["result"]["corroborated_definitions"] == []
    assert result["conflict_coverage_revision"] == syntax.CONFLICT_REVISION
    candidates = result["conflict_definitions"]
    assert [(p["full_name"]["text"], p["short_name"]["text"]) for p in candidates] == [(case["competing_name"], case["alias"])]
    for candidate in candidates:
        assert candidate["source_text_hash"] == digest(post["body"])
        for field in ("full_name", "short_name"):
            span = candidate[field]
            assert "confidence" not in span
            assert post["body"][span["start"]:span["end"]] == span["text"]
