"""Public CLI checks of gate mechanics. These toy labels prove no model quality."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND = Path(__file__).resolve().parents[1]
CATEGORIES = ("concepts", "aliases", "relationships", "expertise")


def decisions(category="concepts", tp=149, fp=1, fn=1, tn=149):
    rows = []
    for gold, applied, count in ((True, True, tp), (False, True, fp), (True, False, fn), (False, False, tn)):
        for _ in range(count):
            identity = f"{category}-{len(rows)}"
            rows.append({"id": identity, "group_id": identity, "category": category,
                         "gold": gold, "applied": applied, "hard_negative": not gold, "split": "heldout"})
    return rows


def run_gate(tmp_path, rows=None, raw=None, development=None):
    labels = tmp_path / "decisions.jsonl"
    labels.write_text(raw if raw is not None else "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    command = [sys.executable, "-m", "app.ml.evaluation", str(labels)]
    if development is not None:
        groups = tmp_path / "development.json"
        groups.write_text(json.dumps(development), encoding="utf-8")
        command += ["--development-groups", str(groups)]
    result = subprocess.run(command, cwd=BACKEND, capture_output=True, text=True)
    return result, json.loads(result.stdout) if result.stdout else None


def test_all_categories_pass_with_complete_independent_samples(tmp_path):
    rows = [row for category in CATEGORIES for row in decisions(category)]
    result, report = run_gate(tmp_path, rows, development=["development-only"])
    assert result.returncode == 0, result.stderr
    assert report["status"] == "pass"
    assert report["development_overlap_checked"] is True
    assert report["decisions_sha256"] == hashlib.sha256((tmp_path / "decisions.jsonl").read_bytes()).hexdigest()
    for category in CATEGORIES:
        summary = report["categories"][category]
        assert summary["status"] == "pass"
        assert summary["counts"]["independent_decisions"] == 300
        assert summary["precision"] == pytest.approx(149 / 150)
        assert summary["recall"] == pytest.approx(149 / 150)
        assert summary["publication_coverage"] == 0.5
        assert summary["abstention_rate"] == 0.5


def test_known_counts_intervals_and_partial_report_cannot_pass_overall(tmp_path):
    result, report = run_gate(tmp_path, decisions(tp=50, fp=50, fn=100, tn=100))
    assert result.returncode == 1
    assert report["status"] == "insufficient"
    summary = report["categories"]["concepts"]
    assert summary["status"] == "fail"
    assert summary["counts"] == {
        "independent_decisions": 300, "positives": 150, "negatives": 150,
        "hard_negatives": 150, "applied": 100, "withheld": 200,
        "true_positives": 50, "false_positives": 50, "false_negatives": 100,
        "true_negatives": 100,
    }
    assert summary["precision"] == 0.5
    assert summary["recall"] == pytest.approx(1 / 3)
    assert summary["precision_wilson_95"] == pytest.approx([0.4038315303659957, 0.5961684696340044])
    assert summary["recall_wilson_95"] == pytest.approx([0.262887648323876, 0.41210243313214945])
    assert report["categories"]["aliases"]["status"] == "insufficient"


def test_publishing_nothing_never_passes(tmp_path):
    result, report = run_gate(tmp_path, decisions(tp=0, fp=0, fn=150, tn=150))
    assert result.returncode == 1
    summary = report["categories"]["concepts"]
    assert summary["status"] == "insufficient"
    assert summary["precision"] is None
    assert summary["precision_wilson_95"] is None
    assert summary["recall"] == 0
    assert summary["abstention_rate"] == 1
    assert "applied: 0 < 100" in summary["insufficient_reasons"]


def test_perfect_precision_with_low_recall_fails(tmp_path):
    result, report = run_gate(tmp_path, decisions(tp=100, fp=0, fn=150, tn=100))
    assert result.returncode == 1
    summary = report["categories"]["concepts"]
    assert summary["status"] == "fail"
    assert summary["precision"] == 1
    assert summary["recall"] == 0.4
    assert not summary["insufficient_reasons"]


def test_aliases_have_stricter_precision_and_complete_failed_gate_exits_nonzero(tmp_path):
    rows = [row for category in CATEGORIES for row in decisions(category, tp=98, fp=2, fn=52, tn=148)]
    result, report = run_gate(tmp_path, rows)
    assert result.returncode == 1
    assert report["status"] == "fail"
    assert report["categories"]["aliases"]["status"] == "fail"
    for category in ("concepts", "relationships", "expertise"):
        assert report["categories"][category]["status"] == "pass"


@pytest.mark.parametrize("counts,minimum", [
    ({"tn": 148}, "independent_decisions"),
    ({"tp": 148, "tn": 150}, "positives"),
    ({"tp": 99, "fp": 0, "fn": 51, "tn": 150}, "applied"),
])
def test_sample_minima_cannot_be_bypassed(tmp_path, counts, minimum):
    result, report = run_gate(tmp_path, decisions(**counts))
    assert result.returncode == 1
    summary = report["categories"]["concepts"]
    assert summary["status"] == "insufficient"
    assert any(reason.startswith(minimum + ":") for reason in summary["insufficient_reasons"])


def test_ordinary_negatives_do_not_satisfy_hard_negative_minimum(tmp_path):
    rows = decisions()
    for row in [row for row in rows if not row["gold"]][:51]:
        del row["hard_negative"]
    result, report = run_gate(tmp_path, rows)
    assert result.returncode == 1
    assert report["categories"]["concepts"]["status"] == "insufficient"
    assert "hard_negatives: 99 < 100" in report["categories"]["concepts"]["insufficient_reasons"]


@pytest.mark.parametrize("change,expected", [
    ({"id": ""}, "nonempty"),
    ({"gold": "true"}, "gold must be a boolean"),
    ({"applied": 1}, "applied must be a boolean"),
    ({"hard_negative": True}, "positive cannot be a hard negative"),
    ({"category": "unknown"}, "unknown category"),
    ({"category": []}, "line 1"),
    ({"split": "development"}, "split must be heldout"),
    ({"accepted": True}, "unknown fields"),
])
def test_malformed_decisions_fail_without_partial_report(tmp_path, change, expected):
    row = decisions()[0] | change
    result, report = run_gate(tmp_path, [row])
    assert result.returncode == 2
    assert report is None
    assert expected in result.stderr


@pytest.mark.parametrize("raw", ["\n", "{broken}\n", "[]\n", "{}\n", '{"id":"a","id":"b"}\n'])
def test_bad_json_and_missing_or_duplicate_fields_are_rejected(tmp_path, raw):
    result, report = run_gate(tmp_path, raw=raw)
    assert result.returncode == 2
    assert report is None
    assert "line 1" in result.stderr


@pytest.mark.parametrize("conflicting", [False, True])
def test_copy_groups_cannot_inflate_independent_sample(tmp_path, conflicting):
    rows = decisions()
    rows[1]["group_id"] = rows[0]["group_id"]
    if conflicting:
        rows[1]["applied"] = False
    result, report = run_gate(tmp_path, rows)
    assert result.returncode == 2
    assert report is None
    assert "duplicate category/group" in result.stderr


def test_duplicate_decision_ids_are_rejected(tmp_path):
    rows = decisions()
    rows[1]["id"] = rows[0]["id"]
    result, report = run_gate(tmp_path, rows)
    assert result.returncode == 2
    assert report is None
    assert "duplicate decision id" in result.stderr


def test_heldout_group_cannot_overlap_development(tmp_path):
    result, report = run_gate(tmp_path, decisions(), development=["concepts-0"])
    assert result.returncode == 2
    assert report is None
    assert "overlaps development" in result.stderr


@pytest.mark.parametrize("inventory", ["a", [1], ["a", "a"], {"schema_version": 1, "development_groups": {}}])
def test_invalid_development_inventory_is_not_ignored(tmp_path, inventory):
    result, report = run_gate(tmp_path, decisions(), development=inventory)
    assert result.returncode == 2
    assert report is None


def test_empty_evaluation_reports_insufficient_evidence(tmp_path):
    result, report = run_gate(tmp_path, [])
    assert result.returncode == 1
    assert report["status"] == "insufficient"
    assert all(category["status"] == "insufficient" for category in report["categories"].values())
