"""Inference score/lifecycle mechanics and independently recorded parser controls.

Legacy regex-only semantic assertions are retained in review artifacts; parser
semantics now use frozen actual parse fixtures, without loading models.
"""
import re
from types import SimpleNamespace
import pytest
from ml_relation_helpers import analyze as _analyze, span as _span, DeclaredGuard, declaration
from ml_synthetic_records import current_synthetic_metadata, select_synthetic_pipeline

def test_alias_record_requires_exact_disjoint_syntax_fields():
    from app.ml.runtime import corroborated_definitions

    body = "MP denotes Meridian photometer."
    full, short = _span(body, "Meridian photometer", score=0.91), _span(body, "MP", score=0.99)
    proposals = [{"rule": "denotes", "full_name": full, "short_name": short}]
    records = [
        {"full_name": full, "short_name": short},
        {"full_name": _span(body, "photometer"), "short_name": short},
        {"full_name": short, "short_name": full},
        {"full_name": {**full, "start": full["start"] + 1}, "short_name": short},
        {"full_name": full, "short_name": full},
        {"full_name": {**full, "confidence": float("nan")}, "short_name": short},
    ]
    accepted = list(corroborated_definitions(body, {"alias_definition": records}, proposals, 23))
    assert len(accepted) == 1
    assert accepted[0]["full_name"] == {**full, "start": full["start"] + 23, "end": full["end"] + 23}
    assert accepted[0]["short_name"] == {**short, "start": short["start"] + 23, "end": short["end"] + 23}
    assert accepted[0]["syntax_rules"] == ["denotes"]
    assert list(corroborated_definitions(body, {"alias_definition": records}, [], 0)) == []


def test_alias_pass_uses_existing_windows_without_promoting_role_scores(monkeypatch):
    from app.ml import runtime
    from app.ml.sources import digest

    text = "preface " * 180 + "MP denotes Meridian photometer." + " notes" * 210
    model = object.__new__(runtime.LocalModels)
    model.entity_schema, model.relation_schema, model.alias_schema = "entities", "relations", "aliases"
    model.manifest = {"models": {"extractor": {"revision": "extractor"}, "syntax": {"revision": "syntax"}}}
    model.tokenizer = lambda body, **kwargs: {"offset_mapping": [m.span() for m in re.finditer(r"\S+", body)]}
    model.syntax = lambda body: body
    calls = []

    def pair(body):
        if "MP denotes Meridian photometer" not in body:
            return None
        return {"full_name": _span(body, "Meridian photometer", score=0.997),
                "short_name": _span(body, "MP", score=0.999)}

    def extract(body, schema, **kwargs):
        calls.append((body, schema, kwargs))
        record = pair(body)
        if schema == "aliases":
            return {"alias_definition": [record] if record else []}
        if schema == "entities" and record:
            return {"entities": {"named entity": [{**record["full_name"], "confidence": 0.2}]}}
        return {}

    monkeypatch.setattr(runtime.syntax, "candidates", lambda body, **kwargs: [{"rule": "denotes", **pair(body)}] if pair(body) else [])
    monkeypatch.setattr(runtime.relation_syntax, "prepare", lambda parsed, text, offset, **kwargs: DeclaredGuard(text))
    model.extractor = SimpleNamespace(extract=extract)
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b"\0" * 4))
    model.embedding = SimpleNamespace(encode=lambda body, **kwargs: vector)
    result = model.analyze(text)
    bounded = list(runtime.windows(text, model.tokenizer, 192))
    assert [(body, kwargs) for body, schema, kwargs in calls if schema == "aliases"] == [
        (body, {"threshold": 0.5, "max_len": 512, "include_confidence": True, "include_spans": True})
        for _, _, body in bounded]
    assert result["concepts"] == [{"name": "Meridian photometer", "start": text.index("Meridian photometer"),
                                   "end": text.index("Meridian photometer") + len("Meridian photometer"),
                                   "score": 0.2, "label": "named entity"}]
    assert result["corroborated_definitions"]
    for record in result["corroborated_definitions"]:
        assert record["source_text_hash"] == digest(text)
        assert record["full_name"] == _span(text, "Meridian photometer", score=0.997)
        assert record["short_name"] == _span(text, "MP", score=0.999)
    # A two-role runtime retains the original two extractor passes per window.
    model.syntax = None
    calls.clear()
    baseline = model.analyze(text)
    assert baseline["concepts"] == result["concepts"]
    assert baseline["corroborated_definitions"] == []
    assert len(calls) == 2 * len(bounded)


def test_alias_fingerprint_invalidates_changed_models_schema_settings_and_rules(monkeypatch):
    from app.ml import runtime

    models = {role: {"revision": role} for role in ("extractor", "embeddings", "syntax")}
    version = runtime.inference_version(models)
    for role in models:
        changed = {**models, role: {"revision": "changed"}}
        assert runtime.inference_version(changed) != version
    assert runtime.inference_version({k: v for k, v in models.items() if k != "syntax"}) != version
    for target, key, value in ((runtime.ALIAS_SCHEMA, "anchor", "short_name"),
                               (runtime.ALIAS_SETTINGS, "threshold", 0.6)):
        with monkeypatch.context() as scoped:
            scoped.setitem(target, key, value)
            assert runtime.inference_version(models) != version
    monkeypatch.setattr(runtime.syntax, "REVISION", "changed")
    assert runtime.inference_version(models) != version


def test_conflict_coverage_revision_invalidates_cached_inference(monkeypatch):
    from app.ml import runtime

    models = {role: {"revision": role} for role in ("extractor", "embeddings", "syntax")}
    version = runtime.inference_version(models)
    monkeypatch.setattr(runtime.syntax, "CONFLICT_REVISION", "next-conflict-contract")
    assert runtime.inference_version(models) != version


@pytest.mark.parametrize("uncertain", [False, True])
def test_grounded_relation_endpoint_corroborates_concept_without_asserting_uncertainty(
        make_client, admin_client, uncertain):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from test_ml_automation import _capture, _edge, _finding, _tags

    names = ("Vega Exporter", "Raster Archive") if not uncertain else ("Orion Exporter", "Pixel Archive")
    clients = make_client(), make_client()
    bodies = [f"{names[0]} produces {names[1]} for the composition stage.",
              f"{names[0]} produces {names[1]} with the selected settings."]
    if uncertain:
        bodies = [f"Could {body.rstrip('.')}?" for body in bodies]
    item_ids = []
    metadata = current_synthetic_metadata()

    def apply(item_id, result=None):
        with SessionLocal() as db:
            select_synthetic_pipeline(db, metadata)
            source = snapshot(db, "item", item_id)
            if result is None:
                result, _ = adapter.cached_result(db, source, metadata[0])
            adapter.apply_source(db, "item", item_id, source, result, *metadata)
            db.commit()

    for index, (client, body) in enumerate(zip(clients, bodies)):
        item_id = _capture(client, body)
        item_ids.append(item_id)
        entities = [_span(body, names[0], score=0.996327)]
        if index == 0:
            entities.append(_span(body, names[1], score=0.981840))
        score = (0.960865, 0.969040)[index]
        # This lifecycle test explicitly supplies grounded support; the separate
        # recorded parser controls test whether the grammar supplies it.
        guard = DeclaredGuard(body, [] if uncertain else [declaration(body, names[0], "produces", names[1])])
        result = _analyze(body, entities, {"produces": [{
            "head": _span(body, names[0], score=score),
            "tail": _span(body, names[1], score=score),
        }]}, full=True, guard=guard)
        endpoint = [span for span in result["concepts"] if span["name"] == names[1]]
        assert len(endpoint) == 1
        assert endpoint[0]["score"] == (0.981840, 0.969040)[index]
        result["chunks"] = []
        apply(item_id, result)
        finding = _finding(admin_client, "concept", name=names[1])
        assert finding["state"] == ("held", "active")[index]
        assert finding["raw_model_score"] == 0.981840

    # Vocabulary backfill revisits the first source after the concept publishes.
    apply(item_ids[0])
    tags = _tags(clients[0], item_ids[0])
    assert set(tags) == set(names)
    edge = _edge(clients[0], *tags.values())
    if uncertain:
        assert edge is None or edge["style"] == "dashed"
    else:
        assert (edge["style"], edge["label"], edge["support_count"]) == ("solid", "produces", 2)


def test_relation_confidence_cannot_establish_an_entity_or_definition():
    from app.ml import policy, resolution

    body = "Aster Processor uses Broad Signal Modulation (BSM)."
    result = _analyze(body, relations={"uses": [{
        "head": _span(body, "Aster Processor", score=0.999),
        "tail": _span(body, "Broad Signal Modulation", score=0.999),
    }]}, full=True)
    assert len(result["concepts"]) == 2
    for span in result["concepts"]:
        assert span["score"] == 0.999
        evidence = [{**span, "raw_score": span["score"], "polarity": "positive", "grounded": True,
                     "group_key": str(index), "text_hash": str(index)} for index in range(2)]
        assert policy.decide("concept", evidence)[0] == "held"
    assert list(resolution.definitions(body, result["concepts"])) == []


@pytest.mark.parametrize("reverse", [False, True])
def test_spelling_equivalent_endpoint_cannot_replace_entity_evidence(make_client, admin_client, reverse):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from test_ml_automation import _capture, _finding, _tags

    name = "Secure Channel" if reverse else "Zero Trust"
    variant = name.replace(" ", "-")
    body = f"{name} is reviewed. Vega Gateway uses {variant}."
    client = make_client()
    item_id = _capture(client, body)
    entities = [_span(body, name, score=0.996), _span(body, "Vega Gateway", score=0.995)]
    metadata = current_synthetic_metadata()

    def apply(result):
        result["chunks"] = []
        with SessionLocal() as db:
            select_synthetic_pipeline(db, metadata)
            adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result,
                                 *metadata)
            db.commit()

    try:
        apply(_analyze(body, entities, full=True))
        concept_id = _tags(client, item_id)[name]
        result = _analyze(body, entities, {"uses": [{
            "head": _span(body, "Vega Gateway", score=0.999),
            "tail": _span(body, variant, score=0.999),
        }]}, full=True)
        # This checks entity-score ownership with an unasserted relation endpoint;
        # a missing parser must remain unable to establish a relationship.
        assert all(not row["literal_support"] for row in result["relations"])
        if reverse:
            result["concepts"].reverse()
        apply(result)
        finding = _finding(admin_client, "concept", name=name)
        assert (finding["state"], finding["raw_model_score"]) == ("active", 0.996)
        assert _tags(client, item_id)[name] == concept_id
        graph = client.get("/api/graph/local", params={"concept_id": concept_id}).json()
        assert any(node["id"] == f"c:{concept_id}" for node in graph["nodes"])
    finally:
        assert client.delete(f'/api/items/{item_id}').status_code == 200
        with SessionLocal() as db:
            adapter.apply_source(db, 'item', item_id, None, None, *metadata)
            db.commit()
