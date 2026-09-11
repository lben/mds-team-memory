"""Grounding regressions using recorded encoder spans, without loading models."""

import re
from types import SimpleNamespace

import pytest


def _span(text, name, start=None, score=0.99):
    start = text.index(name) if start is None else start
    return {"text": name, "start": start, "end": start + len(name), "confidence": score}


def _analyze(text, entities=(), relations=None, *, full=False):
    from app.ml.runtime import LocalModels

    model = object.__new__(LocalModels)
    model.syntax = None
    model.entity_schema, model.relation_schema = "entities", "relations"
    model.tokenizer = lambda body, **kwargs: {"offset_mapping": [match.span() for match in re.finditer(r"\S+", body)]}
    outputs = {"entities": {"entities": {"named entity": list(entities)}},
               "relations": {"relation_extraction": relations or {}}}
    model.extractor = SimpleNamespace(extract=lambda body, schema, **kwargs: outputs[schema])
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b"\0" * 4))
    model.embedding = SimpleNamespace(encode=lambda body, **kwargs: vector)
    result = model.analyze(text)
    return result if full else result["relations"]


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
              f"The export task confirms that {names[0]} produces {names[1]} with the selected settings."]
    if uncertain:
        bodies = [f"Could {body.rstrip('.')}?" for body in bodies]
    item_ids = []
    metadata = "recorded-extraction", "recorded-embedding", 1024

    def apply(item_id, result=None):
        with SessionLocal() as db:
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
        result = _analyze(body, entities, {"produces": [{
            "head": _span(body, names[0], score=score),
            "tail": _span(body, names[1], score=score),
        }]}, full=True)
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
    entities = [_span(body, name, score=0.99), _span(body, "Vega Gateway", score=0.995)]

    def apply(result):
        result["chunks"] = []
        with SessionLocal() as db:
            adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result,
                                 "recorded-extraction", "recorded-embedding", 1024)
            db.commit()

    apply(_analyze(body, entities, full=True))
    concept_id = _tags(client, item_id)[name]
    result = _analyze(body, entities, {"uses": [{
        "head": _span(body, "Vega Gateway", score=0.999),
        "tail": _span(body, variant, score=0.999),
    }]}, full=True)
    if reverse:
        result["concepts"].reverse()
    apply(result)
    finding = _finding(admin_client, "concept", name=name)
    assert (finding["state"], finding["raw_model_score"]) == ("active", 0.99)
    assert _tags(client, item_id)[name] == concept_id
    graph = client.get("/api/graph/local", params={"concept_id": concept_id}).json()
    assert any(node["id"] == f"c:{concept_id}" for node in graph["nodes"])


def test_repeated_endpoint_repairs_only_the_unique_anchored_sentence():
    text = "Earth is part of the Solar System. The Sun is also part of the Solar System and lies at its center."
    head, tail = _span(text, "Sun", score=0.7315430045127869), _span(text, "Solar System", score=0.7315430045127869)
    rows = _analyze(text, relations={"part_of": [{"head": head, "tail": tail}]})
    assert len(rows) == 1
    row = rows[0]
    assert (row["head"]["name"], row["predicate"], row["tail"]["name"]) == ("Sun", "part_of", "Solar System")
    assert row["head"]["start"] == 39 and row["tail"]["start"] == 63
    assert row["score"] == head["confidence"]
    assert row["polarity"] == "positive" and row["literal_support"]
    assert text[row["start"]:row["end"]] == " The Sun is also part of the Solar System and lies at its center."


def test_repeated_endpoint_repair_preserves_reverse_text_order():
    text = "Solar System is discussed. Solar System contains Sun."
    rows = _analyze(text, relations={"part_of": [{"head": _span(text, "Sun"), "tail": _span(text, "Solar System")}]})
    assert rows[0]["head"]["name"] == "Sun" and rows[0]["tail"]["name"] == "Solar System"
    assert rows[0]["tail"]["start"] == text.rindex("Solar System")
    assert rows[0]["literal_support"] and rows[0]["polarity"] == "positive"


@pytest.mark.parametrize("participle", ["included", "contained"])
@pytest.mark.parametrize("passive", [False, True])
def test_explicit_component_of_preserves_subject(make_client, participle, passive):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from test_ml_automation import _capture, _edge, _tags

    names = (f"Cedar {participle.title()} {passive} Sensor", f"Amber {participle.title()} {passive} Camera")
    for index, client in enumerate((make_client(), make_client())):
        phrase = f"is {participle} as" if passive else {"included": "includes", "contained": "contains"}[participle]
        body = f"{names[0]} {phrase} a component of {names[1]}."
        if index:
            body = "The latest inspection confirms that " + body
        spans = [_span(body, name, score=0.995) for name in names]
        result = _analyze(body, spans, {"part_of": [{"head": spans[0], "tail": spans[1]}]}, full=True)
        result["chunks"] = []
        item_id = _capture(client, body)
        with SessionLocal() as db:
            adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result,
                                 "recorded-component-phrase", "recorded-embedding", 1024)
            db.commit()
        tags = _tags(client, item_id)
        edge = _edge(client, *tags.values())
        if not passive:
            assert edge is None or edge["style"] != "solid"
            continue
        assert edge is not None and edge["label"] == "part of"
        assert (edge["source"], edge["target"]) == (tags[names[0]], tags[names[1]])
        assert edge["style"] == ("dashed", "solid")[index]


@pytest.mark.parametrize("predicate,phrase", [
    ("uses", "used"), ("uses", "utilized"), ("uses", "ran on"),
    ("uses", "is using"), ("uses", "was using"),
    ("depends_on", "required"), ("depends_on", "depended on"), ("depends_on", "relied on"),
    ("produces", "produced"), ("produces", "generated"), ("produces", "emitted"),
    ("produces", "created"), ("replaces", "replaced"), ("replaces", "superseded"),
    ("part_of", "contained"), ("part_of", "included"),
])
def test_past_tense_relation_publishes_with_independent_support(make_client, predicate, phrase):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from test_ml_automation import _capture, _edge, _tags

    names = (f"Cedar {phrase.title()} Plan", f"Amber {phrase.title()} Register")
    clients = make_client(), make_client()
    for index, client in enumerate(clients):
        body = f"{names[0]} {phrase} {names[1]}; the inspection cannot proceed without its records."
        if index:
            body = "The audit confirmed that " + body
        spans = [_span(body, name, score=0.995) for name in names]
        head, tail = reversed(spans) if predicate == "part_of" else spans
        result = _analyze(body, spans, {predicate: [{"head": head, "tail": tail}]}, full=True)
        result["chunks"] = []
        item_id = _capture(client, body)
        with SessionLocal() as db:
            adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result,
                                 "recorded-inflections", "recorded-embedding", 1024)
            db.commit()
        tags = _tags(client, item_id)
        edge = _edge(client, *tags.values())
        assert edge is not None and edge["label"] == predicate.replace("_", " ")
        assert edge["style"] == ("dashed", "solid")[index]
        assert edge["support_count"] == index + 1


@pytest.mark.parametrize("predicate,verb", [
    ("uses", "used"), ("uses", "utilized"), ("depends_on", "required"),
    ("produces", "produced"), ("produces", "generated"), ("produces", "emitted"),
    ("produces", "created"), ("replaces", "replaced"), ("replaces", "superseded"),
])
def test_passive_past_tense_keeps_direction(predicate, verb):
    body = f"Amber Register was {verb} by Cedar Plan."
    first, second = _span(body, "Amber Register"), _span(body, "Cedar Plan")
    rows = _analyze(body, relations={predicate: [{"head": first, "tail": second},
                                               {"head": second, "tail": first}]})
    assert not rows[0]["literal_support"]
    assert rows[1]["literal_support"] and rows[1]["polarity"] == "positive"


@pytest.mark.parametrize("predicate,body", [
    ("uses", "Amber Register was used in Cedar Plan."),
    ("produces", "Amber Register is produced from Cedar Plan."),
    ("depends_on", "Amber Register was required for Cedar Plan."),
    ("replaces", "Amber Register has been replaced with Cedar Plan."),
])
def test_passive_with_other_prepositions_cannot_reverse_roles(predicate, body):
    rows = _analyze(body, relations={predicate: [{"head": _span(body, "Amber Register"),
                                               "tail": _span(body, "Cedar Plan")}]})
    assert not rows[0]["literal_support"]


@pytest.mark.parametrize("verb", ["included", "contained"])
def test_passive_containment_cannot_reverse_part_of(verb):
    body = f"Amber Register was {verb} in Cedar Plan."
    rows = _analyze(body, relations={"part_of": [{"head": _span(body, "Cedar Plan"),
                                                "tail": _span(body, "Amber Register")}]})
    assert not rows[0]["literal_support"]


@pytest.mark.parametrize("auxiliary", ["isn't", "aren't", "wasn't", "weren't"])
@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_negated_passive_cannot_withdraw_opposite_relationship(make_client, auxiliary, apostrophe):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from test_ml_automation import _capture, _edge, _tags

    auxiliary = auxiliary.replace("'", apostrophe)
    names = (f"Cedar {auxiliary} Gateway", f"Amber {auxiliary} Register")
    for index in range(3):
        client = make_client()
        phrase = f"{auxiliary} contained in" if index == 2 else "contains"
        body = f"{names[0]} {phrase} {names[1]}."
        if index == 1:
            body = "The audit confirms that " + body
        spans = [_span(body, name, score=0.995) for name in names]
        result = _analyze(body, spans, {"part_of": [{"head": spans[1], "tail": spans[0]}]}, full=True)
        result["chunks"] = []
        item_id = _capture(client, body)
        with SessionLocal() as db:
            adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result,
                                 "recorded-passive-negative", "recorded-embedding", 1024)
            db.commit()
        tags = _tags(client, item_id)
        edge = _edge(client, *tags.values())
        assert edge is not None and edge["label"] == "part of"
        assert edge["style"] == ("dashed" if index == 0 else "solid")


@pytest.mark.parametrize("predicate,phrase", [
    ("uses", "didn't use"), ("uses", "hasn't used"),
    ("uses", "haven't used"), ("depends_on", "hadn't required"),
])
@pytest.mark.parametrize("model_returns_relation", [False, True])
@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_negative_contractions_cannot_publish_positive_relationship(
        make_client, predicate, phrase, model_returns_relation, apostrophe):
    from app.db import SessionLocal
    from app.ml import adapter
    from app.ml.sources import snapshot
    from test_ml_automation import _capture, _edge, _tags

    phrase = phrase.replace("'", apostrophe)
    suffix = f"{phrase.split()[0]} {model_returns_relation}"
    names = (f"Cedar {suffix} Gateway", f"Amber {suffix} Register")
    for index, client in enumerate((make_client(), make_client())):
        body = f"{names[0]} {phrase} {names[1]}."
        if index:
            body = "The audit confirmed that " + body
        spans = [_span(body, name, score=0.995) for name in names]
        extracted = {predicate: [{"head": spans[0], "tail": spans[1]}]} if model_returns_relation else None
        result = _analyze(body, spans, extracted, full=True)
        assert result["relations"] and all(row["polarity"] == "negative" for row in result["relations"])
        result["chunks"] = []
        item_id = _capture(client, body)
        with SessionLocal() as db:
            adapter.apply_source(db, "item", item_id, snapshot(db, "item", item_id), result,
                                 "recorded-contractions", "recorded-embedding", 1024)
            db.commit()
        tags = _tags(client, item_id)
        edge = _edge(client, *tags.values())
        assert edge is None or edge["style"] != "solid"


@pytest.mark.parametrize("body,expected", [
    ("Cedar Plan required Amber Register; the task cannot run without its records.", "positive"),
    ("Cedar Plan never required Amber Register; the audit confirmed the result.", "negative"),
    ("If Cedar Plan required Amber Register; the task would need its records.", "uncertain"),
    ("Cedar Plan required Amber Register; whether this is correct remains open.", "uncertain"),
    ("Did Cedar Plan require Amber Register?", "uncertain"),
])
def test_relation_scope_preserves_negation_and_uncertainty(body, expected):
    rows = _analyze(body, relations={"depends_on": [{"head": _span(body, "Cedar Plan"),
                                                   "tail": _span(body, "Amber Register")}]})
    assert rows[0]["literal_support"] and rows[0]["polarity"] == expected


@pytest.mark.parametrize("text,expected", [
    ("Our astronomy chart identifies Earth as part of the Solar System. The Sun is part of the Solar System, along with its planets and smaller bodies.", "positive"),
    ("I plan to confirm that Sun is part of Solar System.", "uncertain"),
    ("Our plan states that Sun is part of Solar System.", "uncertain"),
    ("Our plans state that Sun is part of Solar System.", "uncertain"),
    ("The planned model says Sun is part of Solar System.", "uncertain"),
    ("We are planning to check that Sun is part of Solar System.", "uncertain"),
])
def test_planning_language_does_not_include_planets(text, expected):
    rows = _analyze(text, relations={"part_of": [{"head": _span(text, "Sun"), "tail": _span(text, "Solar System")}]})
    assert len(rows) == 1 and rows[0]["literal_support"] and rows[0]["polarity"] == expected


@pytest.mark.parametrize("text", [
    "Solar System is discussed. Sun is part of Solar System and part of Solar System.",
    "Solar System contains Sun. Sun is part of Solar System.",
    "Solar System is discussed. Sun is nearby and Earth is part of Solar System.",
])
def test_repeated_endpoint_ambiguity_does_not_create_support(text):
    head = _span(text, "Sun", text.rindex("Sun"))
    rows = _analyze(text, relations={"part_of": [{"head": head, "tail": _span(text, "Solar System")}]})
    assert len(rows) == 1 and not rows[0]["literal_support"]


def test_omitted_explicit_negative_retains_grounded_veto_without_relation_score():
    text = "Grafana does not use Prometheus in the operations dashboard configuration I inspected."
    entities = [_span(text, "Grafana", score=0.9793), _span(text, "Prometheus", score=0.9978)]
    rows = _analyze(text, entities)
    assert len(rows) == 1
    row = rows[0]
    assert (row["head"]["name"], row["predicate"], row["tail"]["name"]) == ("Grafana", "uses", "Prometheus")
    assert row["polarity"] == "negative" and row["literal_support"] and row["score"] == 0.0
    assert text[row["head"]["start"]:row["head"]["end"]] == "Grafana"
    assert text[row["tail"]["start"]:row["tail"]["end"]] == "Prometheus"
    assert text[row["start"]:row["end"]] == text


@pytest.mark.parametrize("text,predicate,head,tail", [
    ("Grafana does not depend on Prometheus.", "depends_on", "Grafana", "Prometheus"),
    ("Sun is not part of Solar System.", "part_of", "Sun", "Solar System"),
    ("Blender does not produce OpenEXR.", "produces", "Blender", "OpenEXR"),
    ("LED does not replace Halogen.", "replaces", "LED", "Halogen"),
    ("Prometheus is not used by Grafana.", "uses", "Grafana", "Prometheus"),
])
def test_negative_veto_preserves_existing_predicate_direction(text, predicate, head, tail):
    rows = _analyze(text, [_span(text, head), _span(text, tail)])
    assert [(row["head"]["name"], row["predicate"], row["tail"]["name"], row["polarity"]) for row in rows] == [
        (head, predicate, tail, "negative")]


@pytest.mark.parametrize("text", [
    "Grafana uses Prometheus, but the dashboard is not available.",
    "Grafana does not use another service, which uses Prometheus.",
    "Grafana does not use\nPrometheus.",
    "Grafana uses Prometheus.",
])
def test_literal_veto_does_not_invent_positive_or_uncertain_claims(text):
    assert _analyze(text, [_span(text, "Grafana"), _span(text, "Prometheus")]) == []


@pytest.mark.parametrize("model_returns_relation", [False, True])
@pytest.mark.parametrize("text", [
    "Grafana does not use Prometheus?",
    "If Grafana does not use Prometheus, the dashboard could fail.",
    "Grafana might not use Prometheus.",
    "Unless Grafana does not use Prometheus, continue the audit.",
    "Check whether Grafana does not use Prometheus.",
    "When Grafana does not use Prometheus, the backup serves metrics.",
])
def test_uncertain_statement_cannot_veto_factual_support(text, model_returns_relation):
    from app.ml import policy

    spans = [_span(text, "Grafana"), _span(text, "Prometheus")]
    extracted = {"uses": [{"head": spans[0], "tail": spans[1]}]} if model_returns_relation else None
    rows = _analyze(text, spans, extracted)
    evidence = [{"polarity": "positive", "literal_support": True, "assertion_allowed": True,
                 "raw_score": 0.86, "group_key": f"source:{index}", "text_hash": str(index)} for index in range(2)]
    evidence.extend({**row, "raw_score": row["score"], "assertion_allowed": True} for row in rows)
    assert policy.decide("relationship", evidence)[0] == "active"
    if model_returns_relation:
        assert len(rows) == 1 and rows[0]["polarity"] == "uncertain"
    else:
        assert rows == []


def test_literal_veto_requires_both_model_grounded_entities():
    text = "Grafana does not use Prometheus."
    assert _analyze(text, [_span(text, "Grafana")]) == []
    invalid = {**_span(text, "Prometheus"), "start": 0}
    assert _analyze(text, [_span(text, "Grafana"), invalid]) == []


@pytest.mark.parametrize("kind,correction_state,expected", [
    ("question", None, "active"),
    ("correction", "proposed", "active"),
    ("correction", "adopted", "held"),
    ("note", None, "held"),
])
def test_only_asserting_sources_can_veto_a_relationship(app_modules, kind, correction_state, expected):
    from app.db import SessionLocal
    from app.ml import policy
    from app.ml.sources import snapshot
    from app.models import KnowledgeItem, Profile

    text = "Grafana does not use Prometheus."
    with SessionLocal() as db:
        author = Profile()
        db.add(author)
        db.flush()
        item = KnowledgeItem(kind=kind, body=text, visibility="team", correction_state=correction_state,
                             author_profile_id=author.id)
        db.add(item)
        db.flush()
        source = snapshot(db, "item", item.id)
    negative, = _analyze(text, [_span(text, "Grafana"), _span(text, "Prometheus")])
    evidence = [{"polarity": "positive", "literal_support": True, "assertion_allowed": True,
                 "raw_score": 0.86, "group_key": f"source:{index}", "text_hash": str(index)} for index in range(2)]
    evidence.append({**negative, "raw_score": negative["score"], "assertion_allowed": source.assertion_allowed})
    assert policy.decide("relationship", evidence)[0] == expected
