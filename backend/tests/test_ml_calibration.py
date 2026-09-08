"""Real fitting/JSON prediction mechanics on authored fixtures, not quality proof."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.ml.calibration import FEATURE_ORDER, load_artifact, predict


BACKEND = Path(__file__).resolve().parents[1]
NATIVE_ML = BACKEND.parent / "data/ml-runtime-native/bin/python"
ML_PYTHON = os.environ.get("MDS_ML_PYTHON") or (
    sys.executable if importlib.util.find_spec("sklearn") else str(NATIVE_ML) if NATIVE_ML.is_file() else None
)


def features(positive, index=0):
    return dict(zip(FEATURE_ORDER, (
        (0.8 if positive else 0.2) + index / 1000,
        40 + index if positive else index, 30 if positive else 1,
        20 if positive else 0, 0 if positive else 10,
        3 if positive else 0, 10 if positive else 0, 4 if positive else 0,
    )))


def development(kinds=("concept",)):
    rows = []
    for kind in kinds:
        for split in ("train", "calibration"):
            for positive in (True, False):
                for index in range(30):
                    identity = f"{kind}-{split}-{positive}-{index}"
                    rows.append({"id": identity, "group_id": identity, "kind": kind,
                                 "features": features(positive, index), "gold": positive, "split": split})
    return rows


def run_fit(directory, rows=None, raw=None, interpreter=sys.executable, output="artifact.json"):
    source = directory / "development.jsonl"
    source.write_text(raw if raw is not None else "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = subprocess.run(
        [interpreter, "-m", "app.ml.calibration", "fit", str(source), str(directory / output),
         "--pipeline-version", "extractor-revision:embedding-revision"],
        cwd=BACKEND, capture_output=True, text=True, timeout=60,
    )
    return result, directory / output


@pytest.fixture(scope="module")
def fitted(tmp_path_factory):
    if not ML_PYTHON:
        pytest.skip("Optional fitting proof requires pinned ML environment (MDS_ML_PYTHON)")
    directory = tmp_path_factory.mktemp("calibration-fit")
    rows = development(("concept", "expertise"))
    result, artifact = run_fit(directory, rows, interpreter=ML_PYTHON)
    assert result.returncode == 0, result.stderr
    return directory, artifact, rows, json.loads(result.stdout)


def test_real_cli_fit_and_stdlib_prediction_remain_explicitly_unverified(fitted):
    directory, path, rows, report = fitted
    artifact = load_artifact(path)
    assert report["quality_status"] == artifact["quality_status"] == "unverified"
    assert "not held-out quality validation" in report["interpretation"]
    assert artifact["input_sha256"] == hashlib.sha256((directory / "development.jsonl").read_bytes()).hexdigest()
    assert artifact["pipeline_version"] == "extractor-revision:embedding-revision"
    assert artifact["omitted_kinds"] == ["alias", "mention", "relationship"]
    assert not set(artifact["development_groups"]["train"]) & set(artifact["development_groups"]["calibration"])
    for kind in ("concept", "expertise"):
        assert artifact["models"][kind]["sample_counts"] == {
            "train": {"positive": 30, "negative": 30}, "calibration": {"positive": 30, "negative": 30},
        }
        assert predict(artifact, kind, features(True)) > 0.9
        assert predict(artifact, kind, features(False)) < 0.1
    assert predict(artifact, "alias", features(True)) is None
    runtime = subprocess.run([sys.executable, "-c", (
        "import json,sys; from app.ml.calibration import load_artifact,predict; "
        "a=load_artifact(sys.argv[1]); f=json.loads(sys.argv[2]); "
        "print(json.dumps({'prediction':predict(a,'concept',f),"
        "'heavy_imports':sorted(set(sys.modules)&{'sklearn','numpy','torch','psutil'})}))"
    ), str(path), json.dumps(features(True))], cwd=BACKEND, capture_output=True, text=True, timeout=30)
    assert runtime.returncode == 0, runtime.stderr
    assert json.loads(runtime.stdout) == {"prediction": predict(artifact, "concept", features(True)), "heavy_imports": []}
    repeated, second = run_fit(directory, rows, interpreter=ML_PYTHON, output="second.json")
    assert repeated.returncode == 0, repeated.stderr
    assert second.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("change,expected", [
    ({"split": "heldout"}, "held-out input is prohibited"),
    ({"gold": "true"}, "explicit boolean"),
    ({"gold": 1}, "explicit boolean"),
    ({"applied": True}, "exactly these fields"),
    ({"kind": "unknown"}, "unknown decision kind"),
    ({"features": features(True) | {"raw_score": float("nan")}}, "finite number"),
    ({"features": features(True) | {"raw_score": True}}, "finite number"),
    ({"features": features(True) | {"authors": 101}}, "integer in"),
    ({"features": features(True) | {"authors": False}}, "integer in"),
])
def test_cli_rejects_untrusted_development_before_heavy_imports(tmp_path, change, expected):
    result, artifact = run_fit(tmp_path, [development()[0] | change])
    assert result.returncode == 2
    assert expected in result.stderr
    assert not artifact.exists()


@pytest.mark.parametrize("overlap", ["same_kind", "different_kind", "duplicate_group", "duplicate_id"])
def test_groups_cannot_leak_between_splits_or_inflate_counts(tmp_path, overlap):
    rows = development()
    if overlap in {"same_kind", "different_kind"}:
        rows[60]["group_id"] = rows[0]["group_id"]
        if overlap == "different_kind":
            rows[60]["kind"] = "expertise"
        expected = "groups overlap"
    elif overlap == "duplicate_group":
        rows[1]["group_id"] = rows[0]["group_id"]
        expected = "duplicate kind/group"
    else:
        rows[1]["id"] = rows[0]["id"]
        expected = "duplicate decision id"
    result, artifact = run_fit(tmp_path, rows)
    assert result.returncode == 2
    assert expected in result.stderr
    assert not artifact.exists()


@pytest.mark.parametrize("removed", [0, 30, 60, 90])
def test_each_split_requires_both_minimum_class_counts(tmp_path, removed):
    rows = development()
    del rows[removed]
    result, artifact = run_fit(tmp_path, rows)
    assert result.returncode == 2
    assert "at least 30 positive and negative" in result.stderr
    assert not artifact.exists()


@pytest.mark.parametrize("raw,expected", [
    ('{"id":"a","id":"b"}\n', "duplicate JSON field"),
    ("", "empty"),
    ("x" * 8193, "bounded record, byte, or line limit"),
])
def test_duplicate_fields_and_unbounded_input_are_rejected(tmp_path, raw, expected):
    result, artifact = run_fit(tmp_path, raw=raw)
    assert result.returncode == 2
    assert expected in result.stderr
    assert not artifact.exists()


@pytest.mark.parametrize("tamper", ["threshold", "coefficient", "split", "quality", "pipeline", "feature_order"])
def test_runtime_refuses_changed_contract_or_invalid_artifact(tmp_path, fitted, tamper):
    artifact = copy.deepcopy(load_artifact(fitted[1]))
    if tamper == "threshold":
        artifact["models"]["concept"]["threshold"] = 0.5
    elif tamper == "coefficient":
        artifact["models"]["concept"]["coefficients"][0] = float("inf")
    elif tamper == "split":
        artifact["development_groups"]["calibration"][0] = artifact["development_groups"]["train"][0]
    elif tamper == "quality":
        artifact["quality_status"] = "pass"
    elif tamper == "pipeline":
        artifact["pipeline_version"] = ""
    else:
        artifact["feature_order"].reverse()
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError):
        load_artifact(path)
