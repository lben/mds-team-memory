"""Frozen actual parser observations and guarded runtime mechanics; no inference."""
import itertools

import pytest

from ml_relation_helpers import CASES, analyze, span


def normalized(record):
    return {"head": record["head"]["name"], "tail": record["tail"]["name"],
            "predicate": record["predicate"], "polarity": record["polarity"]}


@pytest.mark.parametrize("identity", list(CASES))
def test_recorded_roles_reject_every_unlabelled_endpoint_predicate_pair(identity):
    from app.ml import relation_syntax
    case = CASES[identity]
    guard = relation_syntax.Guard(case["parse"], case["body"], 0, relation_syntax.SourceScope(case["body"]))
    supported = []
    for head, tail in itertools.permutations(case["entities"], 2):
        for predicate in ("uses", "depends_on", "part_of", "produces", "replaces"):
            result = guard.support(head, tail, predicate)
            assert result["relation_guard_revision"] == relation_syntax.REVISION
            if result["literal_support"]:
                supported.append({"head": head["name"], "tail": tail["name"], "predicate": predicate,
                                  "polarity": result["polarity"]})
    assert all(row in case["gold"] for row in supported)
    missing = [row for row in case["gold"] if row not in supported]
    # Preserve this observed conservative loss, rather than changing its gold:
    # the parser attaches the assertion under the neutral heading.
    assert missing == (case["gold"] if identity == "neutral_header" else [])


@pytest.mark.parametrize("prefix,suffix", [
    ("Hypothesis\n\n", ""), ("Unverified report:\n", ""), ("Rejected claim:\n", ""),
    ("```text\n", "\n```"), ('"preceding quoted words\n', '\n"'), ("> ", ""),
    ("The following statement is false.\n", ""),
])
def test_full_source_scope_cannot_be_lost_at_window_boundary(prefix, suffix):
    from app.ml import relation_syntax
    case = CASES["active_uses"]
    text = prefix + case["body"] + suffix
    offset = len(prefix)
    guard = relation_syntax.Guard(case["parse"], text, offset, relation_syntax.SourceScope(text))
    head, tail = [{**e, "start": e["start"] + offset, "end": e["end"] + offset} for e in case["entities"]]
    result = guard.support(head, tail, "uses")
    assert not result["literal_support"] and result["polarity"] == "uncertain"


def test_code_heading_cannot_reset_rejected_source_context():
    from app.ml.relation_syntax import SourceScope
    text = "Rejected claim:\n```text\nVerified configuration:\n```\nOrion uses Copper."
    assert not SourceScope(text).asserted(text.index("Orion"), len(text))


def test_missing_parser_withholds_native_relationship_without_changing_scores():
    body = CASES["active_uses"]["body"]
    head, tail = span(body, "Orion", score=.974), span(body, "Copper", score=.861)
    result = analyze(body, relations={"uses": [{"head": head, "tail": tail}]}, parser=False, full=True)
    relation, = result["relations"]
    assert relation["score"] == .861
    assert not relation["literal_support"] and relation["polarity"] == "uncertain"
    assert "relation_guard_revision" not in relation and "relation_guard_revision" not in result


def test_native_score_owner_survives_real_recorded_guard_and_reverse_proposal():
    from app.ml.relation_syntax import REVISION
    body = CASES["active_uses"]["body"]
    head, tail = span(body, "Orion", score=.974), span(body, "Copper", score=.861)
    result = analyze(body, relations={"uses": [{"head": head, "tail": tail}, {"head": tail, "tail": head}]}, full=True)
    assert result["relation_guard_revision"] == REVISION
    first, second = result["relations"]
    assert first["literal_support"] and first["polarity"] == "positive"
    assert not second["literal_support"] and second["polarity"] == "uncertain"
    assert first["score"] == second["score"] == .861
    assert all(r["relation_guard_revision"] == REVISION for r in result["relations"])
    assert first["head"]["score"] == .974 and first["tail"]["score"] == .861


def test_literal_negative_veto_requires_same_guard_and_creates_no_confidence():
    from app.ml import relation_syntax
    case = CASES["direct_negative"]
    body = case["body"]
    entities = [span(body, "Orion", score=.997), span(body, "Copper", score=.983)]
    relation, = analyze(body, entities)
    assert relation["literal_support"] and relation["polarity"] == "negative"
    assert relation["score"] == 0 and relation["relation_guard_revision"] == relation_syntax.REVISION
    assert analyze(body, entities, parser=False) == []
    prefix = "> "
    quoted = prefix + body
    guard = relation_syntax.Guard(case["parse"], quoted, len(prefix), relation_syntax.SourceScope(quoted))
    assert analyze(quoted, [span(quoted, "Orion"), span(quoted, "Copper")], guard=guard) == []


@pytest.mark.parametrize("identity", list(CASES))
def test_entity_only_fallback_emits_exact_recorded_negatives_and_no_positive_claims(identity):
    from app.ml.relation_syntax import REVISION
    case = CASES[identity]
    entities = [span(case["body"], row["name"], start=row["start"], score=.987)
                for row in case["entities"]]
    actual = analyze(case["body"], entities)
    expected = [row for row in case["gold"] if row["polarity"] == "negative"]
    fields = ("head", "predicate", "tail", "polarity")
    assert sorted(tuple(row[field] for field in fields) for row in map(normalized, actual)) == sorted(
        tuple(row[field] for field in fields) for row in expected)
    assert all(row["score"] == 0.0 and row["literal_support"] for row in actual)
    assert all(row["relation_guard_revision"] == REVISION for row in actual)
    assert all(row[endpoint]["score"] == .987 for row in actual for endpoint in ("head", "tail"))


@pytest.mark.parametrize("identity", ["negative_component", "negative_passive", "neither_predicates"])
def test_negative_veto_needs_both_grounded_entity_endpoints_and_keeps_native_score(identity):
    case = CASES[identity]
    body = case["body"]
    entities = [span(body, row["name"], start=row["start"], score=.987) for row in case["entities"]]
    assert analyze(body, entities[:1]) == []
    assert analyze(body, entities, parser=False) == []
    proposals = {}
    for expected in case["gold"]:
        proposals.setdefault(expected["predicate"], []).append({
            "head": span(body, expected["head"], score=.731),
            "tail": span(body, expected["tail"], score=.893)})
    actual = analyze(body, entities, proposals)
    assert len(actual) == len(case["gold"])
    assert all(row["score"] == .731 and row["polarity"] == "negative" for row in actual)


def test_reported_complement_abstains_instead_of_supplied_entity_scores_proving_fact():
    case = CASES["reported_only"]
    body = case["body"]
    entities = [span(body, "Orion", score=.999), span(body, "Copper", score=.999)]
    result = analyze(body, entities, {"uses": [{"head": entities[0], "tail": entities[1]}]}, full=True)
    assert all(not row["literal_support"] for row in result["relations"])
    assert all(row["polarity"] == "uncertain" for row in result["relations"])
    assert {row["name"] for row in result["concepts"]} == {"Orion", "Copper"}


@pytest.mark.parametrize("body", ["", " \t\n  "])
def test_configured_parser_certifies_empty_relation_result(body):
    from app.ml.relation_syntax import REVISION
    from ml_relation_helpers import DeclaredGuard
    result = analyze(body, guard=DeclaredGuard(body), full=True)
    assert result["relations"] == [] and result["chunks"] == []
    assert result["relation_guard_revision"] == REVISION
    without = analyze(body, parser=False, full=True)
    assert "relation_guard_revision" not in without


def test_relation_revision_changes_full_extraction_identity(monkeypatch):
    from app.ml import relation_syntax, runtime
    models = {role: {"revision": role} for role in ("extractor", "embeddings", "syntax")}
    before = runtime.inference_version(models)
    monkeypatch.setattr(relation_syntax, "REVISION", "next-relation-scope")
    assert runtime.inference_version(models) != before


def test_unique_repeated_endpoint_requires_typed_guard_and_keeps_original_score():
    from app.ml import relation_syntax, runtime
    case = CASES["active_uses"]
    prefix = "Copper is discussed. "
    text = prefix + case["body"]
    guard = relation_syntax.Guard(case["parse"], text, len(prefix), relation_syntax.SourceScope(text))
    head = {"name": "Orion", "start": text.index("Orion"), "end": text.index("Orion") + 5, "score": .7315}
    tail = {"name": "Copper", "start": 0, "end": 6, "score": .7315}
    first, second = runtime.repair_relation_spans(text, head, tail, "uses", 0, len(text), guard=guard)
    assert first == head and second["start"] == text.rindex("Copper")
    assert first["score"] == second["score"] == .7315
    assert runtime.repair_relation_spans(text, head, tail, "uses", 0, len(text)) == (head, tail)
