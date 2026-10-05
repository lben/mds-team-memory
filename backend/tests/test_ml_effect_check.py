"""Public effects mechanics with synthetic inference; no model accuracy evidence."""

import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checker(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import ml_quality_check

    return ml_quality_check


def effect(kind, expected, **target):
    return {"kind": kind, **target, "expected": expected}


def routing_case():
    def post(actor, kind, body, **extra):
        return {"actor": actor, "kind": kind, "body": body, "visibility": "team", **extra}

    case = {"id": "effects_fixture", "domain": "mechanics", "rationale": "Synthetic engineering fixture.",
            "posts": [
                post("asker", "question", "How is Ember Cache restored?"),
                post("expert", "answer", "Ember Cache restores receipts from the signed recovery journal.", parent=0, accepted=True),
                post("expert", "note", "Ember Cache stores the countersigned receipt before a recovery.", helped_by=["reader"]),
                post("expert", "note", "Check the Ember Cache retention manifest before exporting the archive.", helped_by=["asker"]),
                post("asker", "question", "Which Ember Cache journal should I inspect?"),
                post("asker", "question", "Where is the blue paper folder?")],
            "actions": [{"type": "edit", "post": 2, "body": "The display is blank."}],
            "feedback_contract": "explicit_topics_v1",
            "feedback": [{"after_post": 3, "post": 1, "actor": "asker", "kind": "accepted", "topics": ["Ember Cache"]},
                         {"after_post": 3, "post": 2, "actor": "reader", "kind": "helped", "topics": ["Ember Cache"]},
                         {"after_post": 3, "post": 3, "actor": "asker", "kind": "helped", "topics": ["Ember Cache"]}],
            "routing_suppression": {"actor": "expert", "concept": "Ember Cache", "historical_question": 4,
                                    "challenge_question": 5, "body": "How does Ember Cache rebuild a receipt?"},
            "expect": {"concepts": ["Ember Cache"], "absent_concepts": [], "aliases": [],
                       "absent_aliases": [{"canonical": "Ember Cache", "alias": "ECC"}],
                       "relationships": [], "absent_relationships": [{"head": "Ember Cache", "predicate": "uses", "tail": "Signal Gate"}],
                       "expertise": [], "absent_expertise": [{"actor": "expert", "concept": "Ember Cache"}]},
            "allowed": {"concepts": ["Ember Cache"], "aliases": [], "relationships": [], "expertise": []},
            "hard_negative": [], "retract": {"expertise": [{"actor": "expert", "concept": "Ember Cache"}]}}
    initial = [effect("tags", ["Ember Cache"], post=2), effect("tags", [], post=5),
               effect("search_items", [2], query="countersigned"), effect("search_items", [], query="absentgadget"),
               effect("suggested_experts", ["expert"], post=4), effect("suggested_experts", [], post=5),
               effect("question_match", True, actor="expert", post=4), effect("question_match", False, actor="expert", post=5),
               effect("routing_notifications", [4], actor="expert"), effect("routing_notifications", [], actor="reader")]
    after = copy.deepcopy(initial)
    for check in after:
        if check["kind"] != "routing_notifications":
            check["expected"] = False if check["kind"] == "question_match" else []
    case["effects"] = {"initial": initial, "before_replay": copy.deepcopy(after), "after_actions": after,
                       "after_challenge": [effect("routing_notifications", [4], actor="expert"),
                                           effect("routing_notifications", [], actor="reader"),
                                           effect("tags", ["Ember Cache"], post=5),
                                           effect("suggested_experts", [], post=5),
                                           effect("question_match", False, actor="expert", post=5)]}
    return case


class SyntheticInference:
    """Only replaces the expensive encoder boundary; no production queue stubs."""

    def __init__(self, models):
        from app.ml.runtime import inference_version

        self.models = models
        self.metadata = (inference_version(models), models["embeddings"]["revision"], 1024)
        self.calls = 0
        self.cpu_seconds = {}
        self.case_peak_rss = 0

    def analyze(self, body, heartbeat, progress=None):
        from app.ml import eligibility, relation_syntax, syntax
        from ml_synthetic_records import judged

        heartbeat()
        name = "Ember Cache"
        spans = [] if name not in body else [{"name": name, "start": body.index(name),
                    "end": body.index(name) + len(name), "score": 0.995, "label": "technology"}]
        result = judged({"concepts": spans, "relations": [], "chunks": [], "conflict_definitions": [],
                         "conflict_coverage_revision": syntax.CONFLICT_REVISION,
                         "relation_guard_revision": relation_syntax.REVISION}, body)
        result["eligibility"]["version"] = eligibility.version(self.models)
        self.calls += 1
        with self.path.open("a") as stream:
            stream.write(json.dumps({"phase": self.phase, "body": body, "result": result, "metadata": self.metadata}) + "\n")
        return result, self.metadata

    def check_memory(self):
        pass

    def close(self):
        pass


@pytest.fixture
def run_effect_case(checker, app_modules, tmp_path):
    from app import config, db
    from app.ml.queue import sqlite_is_safe
    import sqlite3

    if not sqlite_is_safe(sqlite3.sqlite_version_info):
        pytest.skip("Production worker requires a SQLite build with the WAL-reset fix")
    saved_engine = db.engine
    saved_config = {key: getattr(config, key) for key in ("DATA_DIR", "UPLOAD_DIR", "DB_PATH", "DATABASE_URL", "SECURE_COOKIES")}
    saved_env = {key: os.environ.get(key) for key in ("MDS_DATA_DIR", "MDS_DATABASE_URL", "MDS_SECURE_COOKIES")}
    models = {role: {"revision": "fixture-" + role} for role in ("extractor", "embeddings", "syntax")}
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "models.json").write_text(json.dumps({"models": models}))
    sequence = 0

    def run(case=None):
        nonlocal sequence
        sequence += 1
        case = case or routing_case()
        checker.ml_effect_check.validate(case)
        result = checker.run_case(case, ROOT, assets, tmp_path / f"case-{sequence}", SyntheticInference(models), version=2)
        assert result["status"] != "ERROR", result.get("traceback")
        directory = Path(result["database"]).parent
        checker.write_json(directory / "fixture.json", {"evidence_type": "synthetic engineering mechanics; zero quality N",
                                                       "case": case})
        checker.write_json(directory / "effects-report.json", gate(checker, case, result))
        return case, result

    yield run
    db.engine.dispose()
    db.engine = saved_engine
    db.SessionLocal.configure(bind=saved_engine)
    for key, value in saved_config.items():
        setattr(config, key, value)
    for key, value in saved_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def gate(checker, case, result):
    return checker.ml_effect_check.summarize({"cases": [case]}, [result])


def inject_extra_api_effect(monkeypatch, fault):
    """Introduce faults behind HTTP serialization, outside the grader itself."""
    from app import concepts
    from app.routers import items, search

    if fault == "tag":
        original = items.source_concepts

        def extra_tag(*args, **kwargs):
            return [*original(*args, **kwargs), SimpleNamespace(id="unlabelled", name="Invented Topic")]

        monkeypatch.setattr(items, "source_concepts", extra_tag)
    elif fault == "search":
        original = search.search_all

        def extra_hit(db, profile, query):
            result = original(db, profile, query)
            if query == "countersigned":
                result["items"].append({"id": "unlabelled-item"})
            return result

        monkeypatch.setattr(search, "search_all", extra_hit)
    else:
        from app.impact import notify
        from app.models import Account, Profile

        original = concepts.route_question

        def extra_route(db, question, tags):
            original(db, question, tags)
            if question.body == "How does Ember Cache rebuild a receipt?":
                expert = db.query(Profile).join(Account, Account.id == Profile.account_id).filter(Account.username == "expert").one()
                notify(db, expert.id, "expertise_match", "Incorrect post-withdrawal route", question.id,
                       "malicious-route:" + question.id)

        monkeypatch.setattr(concepts, "route_question", extra_route)


def test_fresh_database_public_api_effects_and_new_target_suppression(checker, run_effect_case):
    case, result = run_effect_case()
    assert result["status"] == "PASS"
    summary = gate(checker, case, result)
    assert summary["status"] == "PASS", summary
    assert summary["independent_quality_samples_added"] == 0
    assert len(result["checks"]) == 4
    assert summary["coverage"]["routing_notifications"]["fresh_target_suppression"] == 1
    assert all(row["passed"] for row in result["routing_suppression"]["checks"])
    # Full before observations survive editing Q2 and final database writes.
    initial = result["phases"]["initial"]["observed"]
    final = result["phases"]["after_challenge"]["observed"]
    assert next(row for row in initial["details"] if row["post"] == 5)["response"]["body"] == case["posts"][5]["body"]
    assert next(row for row in final["details"] if row["post"] == 5)["response"]["body"] == case["routing_suppression"]["body"]
    assert initial["expertise"] and not final["expertise"]
    edits = [i for i, row in enumerate(result["api_trace"]) if row.get("method") == "PUT" and "/api/items/" in row["path"]]
    assert len(edits) == 2
    assert any(row.get("path") == "/api/expertise" for row in result["api_trace"][edits[0] + 1:edits[1]])
    assert (Path(result["database"]).parent / "initial-diagnostics.json").is_file()


@pytest.mark.parametrize("fault", ["tag", "search", "route"])
def test_real_api_extra_effect_fails_even_when_selected_quality_passes(checker, run_effect_case, monkeypatch, fault):
    inject_extra_api_effect(monkeypatch, fault)
    case, result = run_effect_case()
    assert all(row["passed"] for row in result["checks"])
    assert all(row["correct"] for rows in result["prediction_audit"].values() for row in rows)
    assert result["status"] == "FAIL"
    assert gate(checker, case, result)["status"] == "FAIL"
    assert checker.evaluation_status("PASS", "PASS", gate(checker, case, result)["status"]) == "FAIL"


def test_summary_regrades_frozen_api_evidence_and_rejects_missing_or_extra_records(checker, run_effect_case):
    case, result = run_effect_case()
    assert gate(checker, case, result)["status"] == "PASS"
    for mutation in ("wrong_flag", "changed_gold", "extra", "missing", "missing_api", "false_empty", "duplicate_case"):
        changed = copy.deepcopy(result)
        if mutation == "wrong_flag":
            changed["effect_checks"][0]["passed"] = False
            assert gate(checker, case, changed)["status"] == "PASS"  # recomputed, never trusted
            continue
        if mutation == "changed_gold":
            changed["effect_checks"][0]["assertion"]["expected"] = []
        elif mutation == "extra":
            changed["effect_checks"].append(copy.deepcopy(changed["effect_checks"][0]))
        elif mutation == "missing":
            changed["effect_checks"].pop()
        elif mutation == "missing_api":
            del changed["phases"]["initial"]["observed"]["details"][2]["response"]["concepts"]
        elif mutation == "false_empty":
            changed["phases"]["after_actions"]["observed"]["details"][2]["response"]["concepts"] = [{"name": "Malicious Tag"}]
        else:
            assert checker.ml_effect_check.summarize({"cases": [case]}, [result, changed])["status"] != "PASS"
            continue
        assert gate(checker, case, changed)["status"] != "PASS", mutation


def test_empty_coverage_and_old_route_dedup_cannot_certify_suppression(checker, run_effect_case):
    case, result = run_effect_case()
    case.pop("routing_suppression")
    case["effects"].pop("after_challenge")
    result["effect_checks"] = [row for row in result["effect_checks"] if row["phase"] != "after_challenge"]
    assert gate(checker, case, result)["status"] == "NOT_DEMONSTRATED"
    assert checker.evaluation_status("PASS", "PASS", "NOT_DEMONSTRATED") == "INCOMPLETE"
    case["effects"] = {}
    result["effect_checks"] = []
    assert gate(checker, case, result)["status"] == "NOT_DEMONSTRATED"


def test_question_delete_cleans_history_without_certifying_future_suppression(checker, run_effect_case):
    case = routing_case()
    case.pop("routing_suppression")
    case["effects"].pop("after_challenge")
    case["actions"].append({"type": "delete", "post": 4})
    for phase in ("before_replay", "after_actions"):
        for check in case["effects"][phase]:
            if check["kind"] == "routing_notifications":
                check["expected"] = []
    case, result = run_effect_case(case)
    assert result["status"] == "PASS"
    summary = gate(checker, case, result)
    assert summary["status"] == "NOT_DEMONSTRATED"
    assert summary["coverage"]["routing_notifications"]["fresh_target_suppression"] == 0
    cleanup = [row for row in summary["transitions"] if row["kind"] == "routing_notifications"]
    assert cleanup and all(row["meaning"] == "question_delete_cleanup" and row["demonstrated"] for row in cleanup)


def test_challenge_requires_actual_withdrawal_and_new_open_tagged_target(checker, run_effect_case):
    case, result = run_effect_case()
    for fault in ("never_expert", "still_expert", "already_targeted", "closed_target", "untagged_target"):
        changed = copy.deepcopy(result)
        phases = changed["phases"]
        if fault == "never_expert":
            phases["initial"]["observed"]["expertise"] = []
        elif fault == "still_expert":
            phases["after_actions"]["observed"]["expertise"] = phases["initial"]["observed"]["expertise"]
        else:
            phase = "initial" if fault == "already_targeted" else "after_challenge"
            detail = next(row for row in phases[phase]["observed"]["details"] if row["post"] == 5)["response"]
            if fault == "closed_target":
                detail["question_status"] = "resolved"
            else:
                detail["concepts"] = [{"name": "Ember Cache"}] if fault == "already_targeted" else []
        assert gate(checker, case, changed)["status"] == "FAIL", fault


@pytest.mark.parametrize("fault", ["same_question", "own_question", "pre_mutated", "history_disappears", "unchanged_body"])
def test_routing_challenge_schema_rejects_dedup_and_retention_loopholes(checker, fault):
    case = routing_case()
    challenge = case["routing_suppression"]
    if fault == "same_question":
        challenge["challenge_question"] = challenge["historical_question"]
    elif fault == "own_question":
        case["posts"][5]["actor"] = "expert"
    elif fault == "pre_mutated":
        case["actions"].append({"type": "edit", "post": 5, "body": "Another question"})
    elif fault == "history_disappears":
        case["effects"]["after_challenge"][0]["expected"] = []
    else:
        challenge["body"] = case["posts"][5]["body"]
    with pytest.raises(ValueError):
        checker.ml_effect_check.validate(case)


def test_engineering_assertions_do_not_inflate_quality_N(checker):
    from test_ml_quality_check import complete_toy_records

    corpus, results = complete_toy_records(checker)
    before, decisions = checker.release_quality(corpus, results)
    for result in results:
        result["effect_checks"] = [{"passed": True}] * 100
        result["routing_suppression"] = {"status": "PASS"}
    after, after_decisions = checker.release_quality(corpus, results)
    assert before == after and decisions == after_decisions and len(decisions) == 1200
    assert checker.evaluation_status("PASS", "PASS") == "INCOMPLETE"


@pytest.mark.parametrize("status", [200, 404, 500])
def test_missing_question_match_requires_confirmed_deletion(checker, status):
    case = {"actions": [{"type": "delete", "post": 0}], "effects": {"after_actions": [
        effect("question_match", False, actor="reader", post=0)]}}
    observed = {"details": [{"post": 0, "status": status}], "questions_by_actor": {"reader": []}}
    if status == 404:
        checks = checker.ml_effect_check.grade(case, "after_actions", observed, [{"id": "question"}])
        assert len(checks) == 1 and checks[0]["passed"]
    else:
        with pytest.raises(ValueError, match="confirmed deletion"):
            checker.ml_effect_check.grade(case, "after_actions", observed, [{"id": "question"}])
