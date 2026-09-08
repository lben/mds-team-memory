"""Optional local evidence fitting; runtime prediction needs only this JSON file.

From backend: python -m app.ml.calibration fit development.jsonl artifact.json
  --pipeline-version EXTRACTOR_REVISION:EMBEDDING_REVISION

Each development JSONL record has exactly id, group_id, kind, features, gold
(a boolean explicit label), and split (train or calibration). Group copies
together before labeling. Never use held-out labels, generated gold, ignored
findings, or routing decisions as labels. The CLI cannot attest label origins.
Supply all eight FEATURE_ORDER fields; counts are capped at COUNT_BOUND both
when exporting examples and when predicting. Missing kinds are explicitly
omitted. A supplied kind needs 30 positives and 30 negatives in EACH split.
This is an engineering minimum, not evidence of useful model quality.

Features and thresholds are fixed before fitting. A fitted artifact remains
unverified; assess actual publication decisions with the frozen held-out
evaluator and this artifact's development group inventory. Do not tune to that
report. No encoders are trained, loaded, or downloaded here.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from .evaluation import identifier, unique_object


FEATURE_ORDER = (
    "raw_score", "independent_groups", "authors", "literal_support",
    "negative_support", "explicit_definitions", "outcome_originals", "accepted_answers",
)
COUNT_BOUND = 100
THRESHOLDS = {"concept": 0.98, "mention": 0.98, "alias": 0.99, "relationship": 0.98, "expertise": 0.95}
MIN_CLASS_COUNT = 30
SKLEARN_VERSION = "1.9.0"
METHOD = "standardized-l2-logistic-platt-v1"
MAX_RECORDS = 50_000
MAX_INPUT_BYTES = 32 * 1024**2
MAX_LINE_BYTES = 8192
MEMORY_CEILING = 8 * 1024**3


def _fields(value, expected):
    if not isinstance(value, dict) or value.keys() != set(expected):
        raise ValueError(f"expected exactly these fields: {', '.join(sorted(expected))}")


def _identifier(value):
    return identifier(value) and len(value) <= 256


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _pipeline_version(value):
    return _identifier(value) and len(value.split(":")) == 2 and all(_identifier(part) for part in value.split(":"))


def feature_vector(features):
    """Validate the shared bounded feature contract and return ordered values."""
    _fields(features, FEATURE_ORDER)
    if not _finite(features["raw_score"]) or not 0 <= features["raw_score"] <= 1:
        raise ValueError("raw_score must be a finite number in [0, 1]")
    for name in FEATURE_ORDER[1:]:
        if type(features[name]) is not int or not 0 <= features[name] <= COUNT_BOUND:
            raise ValueError(f"{name} must be an integer in [0, {COUNT_BOUND}]")
    return [features[name] for name in FEATURE_ORDER]


def read_development(path):
    """Reject split leakage and duplicate independent decisions before fitting."""
    rows, ids, category_groups, group_splits = [], set(), set(), {}
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        for number in range(1, MAX_RECORDS + 2):
            line = source.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            size += len(line)
            if number > MAX_RECORDS or size > MAX_INPUT_BYTES or len(line) > MAX_LINE_BYTES:
                raise ValueError("development input exceeds bounded record, byte, or line limit")
            digest.update(line)
            try:
                row = json.loads(line, object_pairs_hook=unique_object)
                _fields(row, ("id", "group_id", "kind", "features", "gold", "split"))
                if not _identifier(row["id"]) or not _identifier(row["group_id"]):
                    raise ValueError("id and group_id must be nonempty trimmed strings of at most 256 characters")
                if row["kind"] not in THRESHOLDS:
                    raise ValueError("unknown decision kind")
                if row["split"] not in {"train", "calibration"}:
                    raise ValueError("split must be train or calibration; held-out input is prohibited")
                if type(row["gold"]) is not bool:
                    raise ValueError("gold must be an explicit boolean label")
                feature_vector(row["features"])
                if row["id"] in ids:
                    raise ValueError("duplicate decision id")
                group = row["group_id"]
                if group in group_splits and group_splits[group] != row["split"]:
                    raise ValueError("train and calibration groups overlap")
                category_group = (row["kind"], group)
                if category_group in category_groups:
                    raise ValueError("duplicate kind/group; copies cannot increase sample counts")
                ids.add(row["id"])
                category_groups.add(category_group)
                group_splits[group] = row["split"]
                rows.append(row)
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError(f"{path}: line {number}: {error}") from error
    if not rows:
        raise ValueError("development input is empty")
    counts = {}
    for kind in sorted({row["kind"] for row in rows}):
        counts[kind] = {}
        for split in ("train", "calibration"):
            selected = [row for row in rows if row["kind"] == kind and row["split"] == split]
            counts[kind][split] = {"positive": sum(row["gold"] for row in selected),
                                   "negative": sum(not row["gold"] for row in selected)}
            if min(counts[kind][split].values()) < MIN_CLASS_COUNT:
                raise ValueError(f"{kind}/{split} needs at least {MIN_CLASS_COUNT} positive and negative labels")
    groups = {split: sorted(group for group, assigned in group_splits.items() if assigned == split)
              for split in ("train", "calibration")}
    return rows, digest.hexdigest(), groups, counts


def validate_artifact(artifact):
    """Validate untrusted JSON before persisting or applying its coefficients."""
    _fields(artifact, ("schema_version", "pipeline_version", "method", "sklearn_version", "feature_order",
                       "input_sha256", "development_groups", "models", "omitted_kinds", "quality_status"))
    if type(artifact["schema_version"]) is not int or artifact["schema_version"] != 1:
        raise ValueError("unsupported calibration schema version")
    if artifact["method"] != METHOD or artifact["sklearn_version"] != SKLEARN_VERSION:
        raise ValueError("unsupported fitting method or sklearn version")
    if not _pipeline_version(artifact["pipeline_version"]):
        raise ValueError("pipeline_version must identify extractor:embedding revisions")
    if artifact["feature_order"] != list(FEATURE_ORDER):
        raise ValueError("unsupported feature order")
    digest = artifact["input_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid development input SHA-256")
    if artifact["quality_status"] != "unverified":
        raise ValueError("fitting artifacts must retain unverified quality status")
    inventories = artifact["development_groups"]
    _fields(inventories, ("train", "calibration"))
    for values in inventories.values():
        if (not isinstance(values, list) or not values or len(values) > MAX_RECORDS
                or not all(_identifier(value) for value in values) or len(values) != len(set(values))):
            raise ValueError("invalid development group inventory")
    if set(inventories["train"]) & set(inventories["calibration"]):
        raise ValueError("train and calibration groups overlap")
    models, omitted = artifact["models"], artifact["omitted_kinds"]
    if not isinstance(models, dict) or not models or not models.keys() <= THRESHOLDS.keys():
        raise ValueError("invalid fitted decision kinds")
    if not isinstance(omitted, list) or omitted != sorted(THRESHOLDS.keys() - models.keys()):
        raise ValueError("omitted_kinds must list all unfitted kinds")
    total = 0
    for kind, model in models.items():
        _fields(model, ("coefficients", "intercept", "platt_coefficient", "platt_intercept", "threshold", "sample_counts"))
        coefficients = model["coefficients"]
        if (not isinstance(coefficients, list) or len(coefficients) != len(FEATURE_ORDER)
                or not all(_finite(value) and abs(value) <= 1e6 for value in coefficients)):
            raise ValueError("invalid logistic coefficients")
        for name in ("intercept", "platt_coefficient", "platt_intercept"):
            if not _finite(model[name]) or abs(model[name]) > 1e6:
                raise ValueError(f"invalid {name}")
        if not _finite(model["threshold"]) or model["threshold"] != THRESHOLDS[kind]:
            raise ValueError("publication threshold differs from frozen policy")
        _fields(model["sample_counts"], ("train", "calibration"))
        for split, counts in model["sample_counts"].items():
            _fields(counts, ("positive", "negative"))
            if any(type(count) is not int or count < MIN_CLASS_COUNT for count in counts.values()):
                raise ValueError("fitted model lacks minimum development class counts")
            if sum(counts.values()) > len(inventories[split]):
                raise ValueError("sample counts exceed independent development groups")
            total += sum(counts.values())
    if total > MAX_RECORDS:
        raise ValueError("artifact exceeds bounded development record limit")
    return artifact


def load_artifact(path):
    with Path(path).open("rb") as source:
        data = source.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("calibration artifact exceeds byte limit")
    try:
        return validate_artifact(json.loads(data, object_pairs_hook=unique_object))
    except (TypeError, OverflowError) as error:
        raise ValueError("invalid calibration artifact") from error


def predict(artifact, kind, features):
    """Return an unverified calibrated estimate from a previously validated artifact.

    Missing kinds return None, allowing the unchanged cold-start policy. The
    caller must also match pipeline_version and preserve evidence safety rules.
    """
    values = feature_vector(features)
    model = artifact["models"].get(kind)
    if model is None:
        return None
    logit = model["intercept"] + math.fsum(weight * value for weight, value in zip(model["coefficients"], values))
    calibrated = model["platt_intercept"] + model["platt_coefficient"] * logit
    if calibrated >= 0:
        return 1 / (1 + math.exp(-calibrated))
    exponent = math.exp(calibrated)
    return exponent / (1 + exponent)


def _fit(rows, digest, groups, counts, pipeline_version):
    # Called only by the fitting CLI, after its resource limits are installed.
    import warnings
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    if sklearn.__version__ != SKLEARN_VERSION:
        raise ValueError(f"fitting requires scikit-learn=={SKLEARN_VERSION}")
    models = {}
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        for kind in counts:
            train = [row for row in rows if row["kind"] == kind and row["split"] == "train"]
            calibration = [row for row in rows if row["kind"] == kind and row["split"] == "calibration"]
            scaler = StandardScaler()
            x = scaler.fit_transform([feature_vector(row["features"]) for row in train])
            fitted = LogisticRegression(C=1.0, solver="liblinear", max_iter=1000, tol=1e-8, random_state=0)
            fitted.fit(x, [row["gold"] for row in train])
            logits = fitted.decision_function(scaler.transform([feature_vector(row["features"]) for row in calibration]))
            platt = LogisticRegression(C=1.0, solver="liblinear", max_iter=1000, tol=1e-8, random_state=0)
            platt.fit(logits.reshape(-1, 1), [row["gold"] for row in calibration])
            coefficients = fitted.coef_[0] / scaler.scale_
            models[kind] = {
                "coefficients": coefficients.tolist(),
                "intercept": float(fitted.intercept_[0] - coefficients @ scaler.mean_),
                "platt_coefficient": float(platt.coef_[0, 0]), "platt_intercept": float(platt.intercept_[0]),
                "threshold": THRESHOLDS[kind], "sample_counts": counts[kind],
            }
    return validate_artifact({
        "schema_version": 1, "pipeline_version": pipeline_version, "method": METHOD,
        "sklearn_version": SKLEARN_VERSION, "feature_order": list(FEATURE_ORDER),
        "input_sha256": digest, "development_groups": groups, "models": models,
        "omitted_kinds": sorted(THRESHOLDS.keys() - models.keys()), "quality_status": "unverified",
    })


def _resource_guard():
    """Bound native threads before imports and abort fitting above 8 GiB RSS."""
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
        os.environ[name] = "4"
    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])
    if hasattr(os, "nice") and os.nice(0) < 10:
        os.nice(10 - os.nice(0))
    import threading
    import psutil

    stop = threading.Event()
    process = psutil.Process()

    def monitor():
        while not stop.is_set():
            rss = 0
            for child in [process, *process.children(recursive=True)]:
                try:
                    rss += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            if rss > MEMORY_CEILING:
                os.write(2, b"Calibration stopped: ML process RSS exceeded 8 GiB.\n")
                os._exit(2)
            stop.wait(0.1)

    threading.Thread(target=monitor, name="calibration-memory-limit", daemon=True).start()
    return stop


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("fit",))
    parser.add_argument("development", type=Path)
    parser.add_argument("output", type=Path, help="new JSON artifact path; existing files are never replaced")
    parser.add_argument("--pipeline-version", required=True, help="extractor revision:embedding revision")
    args = parser.parse_args(argv)
    guard = None
    try:
        if not _pipeline_version(args.pipeline_version):
            raise ValueError("pipeline_version must identify extractor:embedding revisions")
        if args.output.exists():
            raise ValueError("output already exists; freeze each fitted artifact in a new path")
        rows, digest, groups, counts = read_development(args.development)
        guard = _resource_guard()
        artifact = _fit(rows, digest, groups, counts, args.pipeline_version)
        serialized = json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n"
        with args.output.open("x", encoding="utf-8") as target:
            target.write(serialized)
    except (OSError, ValueError, ImportError, Warning) as error:
        parser.error(str(error))
    finally:
        if guard is not None:
            guard.set()
    print(json.dumps({"artifact": str(args.output), "fitted_kinds": sorted(artifact["models"]),
                      "omitted_kinds": artifact["omitted_kinds"], "quality_status": "unverified",
                      "interpretation": "Development fitting is not held-out quality validation."}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
