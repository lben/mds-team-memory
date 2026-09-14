"""Evaluation integrity checks with toy records; these prove no model accuracy."""

import copy
import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    import ml_quality_check

    return ml_quality_check


def test_missing_parser_runtime_fails_before_starting_corpus(checker, monkeypatch):
    checked = []

    def installed(name):
        checked.append(name)
        return None if name in {"spacy", "spacy_curated_transformers"} else object()

    monkeypatch.setattr(checker.importlib.util, "find_spec", installed)
    with pytest.raises(RuntimeError, match="spacy, spacy_curated_transformers"):
        checker.check_runtime_dependencies({"models": {"extractor": {}, "embeddings": {}, "syntax": {}}})
    checked.clear()
    checker.check_runtime_dependencies({"models": {"extractor": {}, "embeddings": {}}})
    assert "spacy" not in checked


def scenario(identity="evaluation_one"):
    targets = {
        "concepts": "Compiler",
        "aliases": {"canonical": "Compiler", "alias": "CC"},
        "relationships": {"head": "Compiler", "predicate": "uses", "tail": "Parser"},
        "expertise": {"actor": "ben", "concept": "Compiler"},
    }
    expected = {category: [value] for category, value in targets.items()}
    expected.update({"absent_" + category: [] for category in targets})
    allowed = copy.deepcopy({category: [value] for category, value in targets.items()})
    allowed["concepts"].append("Parser")
    return {"id": identity, "domain": "toy", "rationale": "Scoring mechanism only.",
            "posts": [{"actor": "ben", "kind": "note", "visibility": "team", "body": identity}],
            "expect": expected, "allowed": allowed, "hard_negative": [], "retract": {}}


def observation():
    return {
        "concepts": [{"id": "one", "name": "Compiler"}, {"id": "two", "name": "Parser"}],
        "terms": [{"name": "Compiler", "concept_id": "one"}, {"name": "CC", "concept_id": "one"},
                  {"name": "Parser", "concept_id": "two"}],
        "aliases": [{"canonical": "Compiler", "canonical_id": "one", "alias": "CC",
                     "public_search_confirmed": True}],
        "relationships": [{"src_id": "one", "dst_id": "two", "predicate": "uses"}],
        "expertise": [{"actor": "ben", "concept": "Compiler"}],
    }


def test_full_output_audit_catches_extra_false_predictions_and_preserves_alias_identity(checker):
    case, observed = scenario(), observation()
    assert all(row["correct"] for rows in checker.audit_predictions(case, observed).values() for row in rows)
    # The canonical spelling may be selected in either direction.
    observed["aliases"][0].update(canonical="CC", alias="Compiler")
    assert checker.grade(case, observed, 2)[1]["passed"]
    assert checker.audit_predictions(case, observed)["aliases"][0]["correct"]
    # Every selected assertion still passes, but the extra reverse claim is false.
    observed["relationships"].append({"src_id": "two", "dst_id": "one", "predicate": "uses"})
    assert all(row["passed"] for row in checker.grade(case, observed, 2))
    assert [row["correct"] for row in checker.audit_predictions(case, observed)["relationships"]] == [True, False]
    observed["aliases"].append({"canonical": "Compiler", "canonical_id": "one", "alias": "Parser",
                                "public_search_confirmed": True})
    observed["terms"][-1]["concept_id"] = "one"
    observed["concepts"].pop()
    assert not checker.audit_predictions(case, observed)["aliases"][-1]["correct"]


def test_predicted_identity_cannot_certify_an_unlabelled_canonical_name(checker):
    case, observed = scenario(), observation()
    observed["concepts"][0]["name"] = "WrongCanonical"
    observed["terms"].append({"name": "WrongCanonical", "concept_id": "one"})
    observed["aliases"] = [
        {"canonical": "WrongCanonical", "canonical_id": "one", "alias": name,
         "public_search_confirmed": True} for name in ("Compiler", "CC")]
    observed["expertise"][0]["concept"] = "WrongCanonical"
    audit = checker.audit_predictions(case, observed)
    assert [row["correct"] for row in audit["concepts"]] == [False, True]
    assert not any(row["correct"] for row in audit["aliases"])
    assert not audit["relationships"][0]["correct"]
    assert not audit["expertise"][0]["correct"]
    assert not any(row["passed"] for row in checker.grade(case, observed, 2))


def test_expanded_fixture_requires_frozen_hash_and_unique_independent_decisions(checker, tmp_path):
    path = tmp_path / "cases.json"
    corpus = {"version": 2, "split": "heldout", "purpose": "Toy validation", "cases": [scenario()]}

    def freeze():
        raw = json.dumps(corpus).encode()
        path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    digest = freeze()
    assert checker.load_cases(path, digest)[1] == corpus
    with pytest.raises(ValueError, match="frozen"):
        checker.load_cases(path)
    corpus["cases"][0]["expect"]["concepts"].append("Parser")
    with pytest.raises(ValueError, match="one independent decision"):
        checker.load_cases(path, freeze())
    corpus["cases"] = [scenario(), scenario()]
    with pytest.raises(ValueError, match="unique"):
        checker.load_cases(path, freeze())
    corpus["cases"][1]["id"] = "other"
    with pytest.raises(ValueError, match="Duplicated source"):
        checker.load_cases(path, freeze())
    corpus["cases"] = [scenario("../outside")]
    with pytest.raises(ValueError, match="safe directory"):
        checker.load_cases(path, freeze())


def complete_toy_records(checker):
    cases, results = [], []
    for index in range(300):
        case = scenario(f"independent_{index}")
        positive = index < 150
        if not positive:
            for category in checker.CATEGORIES:
                case["expect"]["absent_" + category] = case["expect"][category]
                case["expect"][category] = []
            case["hard_negative"] = list(checker.CATEGORIES)
        cases.append(case)
        results.append({"id": case["id"], "status": "PASS", "checks": [
            {"category": category, "expected_present": positive, "observed": ["applied"] if positive else []}
            for category in checker.CATEGORIES], "prediction_audit": {
                category: [{"correct": True}] if positive else [] for category in checker.CATEGORIES}})
    return {"cases": cases}, results


def test_release_gate_includes_extra_predictions_and_cannot_pass_missing_evidence(checker):
    corpus, results = complete_toy_records(checker)
    assert checker.release_quality(corpus, results)[0]["status"] == "PASS"
    for result in results[:10]:
        result["prediction_audit"]["aliases"].append({"correct": False})
    gate, records = checker.release_quality(corpus, results)
    assert gate["status"] == "FAIL" and len(records) == 1200
    assert gate["categories"]["aliases"]["precision"] == 1
    assert gate["categories"]["aliases"]["all_applied_predictions"]["precision"] == 150 / 160
    assert checker.release_quality(corpus, results[:-1])[0]["status"] == "INCOMPLETE"
    results[0]["retractions"] = [{"initially_present": False, "absent_after_actions": True}]
    assert checker.release_quality(corpus, results)[0]["status"] == "FAIL"


def test_untriggered_withdrawal_does_not_change_numeric_quality(checker):
    corpus, results = complete_toy_records(checker)
    expected, _ = checker.release_quality(corpus, results)
    results[-1]["retractions"] = [{"category": "concepts", "assertion": "Compiler",
                                  "initially_present": False, "absent_after_actions": True}]
    actual, _ = checker.release_quality(corpus, results)
    assert actual == expected


def test_lifecycle_requires_demonstrated_transitions_and_fails_surviving_findings(checker):
    results = [{"id": "mutation", "status": "PASS", "retractions": [
        {"category": category, "assertion": category,
         "initially_present": True, "absent_after_actions": True}
        for category in checker.CATEGORIES]}]
    assert checker.lifecycle_quality(results)["status"] == "PASS"
    results[0]["retractions"].append({"category": "concepts", "assertion": "Withheld",
                                     "initially_present": False, "absent_after_actions": True})
    lifecycle = checker.lifecycle_quality(results)
    assert lifecycle["status"] == "PASS"
    assert lifecycle["categories"]["concepts"] == {"DEMONSTRATED": 1, "FAILED": 0, "UNTRIGGERED": 1}
    assert lifecycle["checks"][-1]["status"] == "UNTRIGGERED"
    assert checker.case_status([{"passed": True}], results[0]["retractions"], version=2) == "PASS"
    assert checker.case_status([{"passed": True}], results[0]["retractions"], version=1) == "INCOMPLETE"

    # Missing aliases/relationships cannot be certified by concept/expertise transitions.
    results[0]["retractions"] = [row for row in results[0]["retractions"]
                                 if row["category"] in {"concepts", "expertise"}]
    assert checker.lifecycle_quality(results)["status"] == "NOT_DEMONSTRATED"
    assert checker.evaluation_status("PASS", "NOT_DEMONSTRATED") == "INCOMPLETE"
    assert checker.evaluation_status("FAIL", "NOT_DEMONSTRATED") == "FAIL"
    for initially_present in (True, False):
        results[0]["retractions"][-1].update(initially_present=initially_present, absent_after_actions=False)
        assert checker.lifecycle_quality(results)["status"] == "FAIL"
        assert checker.evaluation_status("PASS", "FAIL") == "FAIL"
        assert checker.case_status([{"passed": True}], results[0]["retractions"], version=2) == "FAIL"


def test_empty_publication_and_small_corpus_do_not_pass_release_gate(checker):
    corpus, results = complete_toy_records(checker)
    corpus["split"] = "development"
    assert checker.release_quality(corpus, results)[0]["status"] == "INSUFFICIENT_EVIDENCE"
    corpus["split"] = "heldout"
    for result in results:
        for check in result["checks"]:
            check["observed"] = []
        result["prediction_audit"] = {category: [] for category in checker.CATEGORIES}
    gate, _ = checker.release_quality(corpus, results)
    assert gate["status"] == "INSUFFICIENT_EVIDENCE"
    assert all(row["counts"]["applied"] == 0 and row["recall"] == 0 for row in gate["categories"].values())
    assert checker.release_quality({"cases": corpus["cases"][:1]}, results[:1])[0]["status"] == "INSUFFICIENT_EVIDENCE"
