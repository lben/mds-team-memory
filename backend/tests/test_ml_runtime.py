"""Grounding regressions using recorded encoder spans, without loading models."""

import re
from types import SimpleNamespace

import pytest


def _span(text, name, start=None, score=0.99):
    start = text.index(name) if start is None else start
    return {"text": name, "start": start, "end": start + len(name), "confidence": score}


def _analyze(text, entities=(), relations=None):
    from app.ml.runtime import LocalModels

    model = object.__new__(LocalModels)
    model.entity_schema, model.relation_schema = "entities", "relations"
    model.tokenizer = lambda body, **kwargs: {"offset_mapping": [match.span() for match in re.finditer(r"\S+", body)]}
    outputs = {"entities": {"entities": {"named entity": list(entities)}},
               "relations": {"relation_extraction": relations or {}}}
    model.extractor = SimpleNamespace(extract=lambda body, schema, **kwargs: outputs[schema])
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b"\0" * 4))
    model.embedding = SimpleNamespace(encode=lambda body, **kwargs: vector)
    return model.analyze(text)["relations"]


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
