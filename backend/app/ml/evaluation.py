"""Evaluate the frozen initial-release policy against independent held-out labels.

Run with ``python -m app.ml.evaluation decisions.jsonl`` from backend. Each line
must contain id, group_id, category, gold (bool), and applied (bool). Categories
are concepts, aliases, relationships, expertise. Optional hard_negative (bool)
marks a labeled difficult negative; optional split must be "heldout".

Each category/group must have exactly one decision, chosen before evaluating;
copies must share a group and cannot inflate the sample. `applied` means the
actual automatic publication decision. An eligible positive that was withheld
is a false negative. Include withheld cases, not just extracted candidates.

--development-groups accepts the fitted calibration artifact or a JSON array
of all training/calibration group IDs.
The evaluator checks overlap when this inventory is supplied. It cannot verify
label quality, representative sampling, or that data was genuinely untouched.
Freeze data, grouping, and policy before evaluation; never tune to this report.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


PRECISION_TARGETS = {
    "concepts": 0.98,
    "aliases": 0.99,
    "relationships": 0.98,
    "expertise": 0.95,
}
MINIMUMS = {
    "independent_decisions": 300,
    "positives": 150,
    "hard_negatives": 100,
    "applied": 100,
}
RECALL_TARGET = 0.50


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def identifier(value):
    return isinstance(value, str) and bool(value) and value == value.strip()


def read_decisions(path, development_groups):
    required = {"id", "group_id", "category", "gold", "applied"}
    allowed = required | {"hard_negative", "split"}
    decisions, ids, groups = [], set(), set()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for number, line in enumerate(source, 1):
            digest.update(line)
            try:
                record = json.loads(line, object_pairs_hook=unique_object)
                if not isinstance(record, dict):
                    raise ValueError("expected a JSON object")
                missing, extra = required - record.keys(), record.keys() - allowed
                if missing or extra:
                    raise ValueError(
                        f"missing fields {sorted(missing)}; unknown fields {sorted(extra)}"
                    )
                if not identifier(record["id"]) or not identifier(record["group_id"]):
                    raise ValueError("id and group_id must be nonempty, trimmed strings")
                if record["category"] not in PRECISION_TARGETS:
                    raise ValueError("unknown category")
                for field in ("gold", "applied", "hard_negative"):
                    if type(record.get(field, False)) is not bool:
                        raise ValueError(f"{field} must be a boolean")
                if record.get("hard_negative", False) and record["gold"]:
                    raise ValueError("a positive cannot be a hard negative")
                if "split" in record and record["split"] != "heldout":
                    raise ValueError("split must be heldout")
                if record["id"] in ids:
                    raise ValueError(f"duplicate decision id: {record['id']}")
                group = (record["category"], record["group_id"])
                if group in groups:
                    raise ValueError(
                        "duplicate category/group; select one independent decision "
                        "per group before evaluation"
                    )
                if record["group_id"] in development_groups:
                    raise ValueError("held-out group overlaps development data")
                ids.add(record["id"])
                groups.add(group)
                decisions.append(record)
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError(f"{path}: line {number}: {error}") from error
    return decisions, digest.hexdigest()


def wilson(successes, total):
    if not total:
        return None
    # The two-sided 95% normal quantile; these intervals describe sampling
    # uncertainty, not the probability that an individual claim is correct.
    z = 1.959963984540054
    fraction = successes / total
    denominator = 1 + z * z / total
    center = (fraction + z * z / (2 * total)) / denominator
    half = z * math.sqrt(fraction * (1 - fraction) / total + z * z / (4 * total**2)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def summarize(records, precision_target):
    true_positives = sum(row["gold"] and row["applied"] for row in records)
    false_positives = sum(not row["gold"] and row["applied"] for row in records)
    false_negatives = sum(row["gold"] and not row["applied"] for row in records)
    true_negatives = len(records) - true_positives - false_positives - false_negatives
    positives = true_positives + false_negatives
    applied = true_positives + false_positives
    counts = {
        "independent_decisions": len(records),
        "positives": positives,
        "negatives": false_positives + true_negatives,
        "hard_negatives": sum(row.get("hard_negative", False) for row in records),
        "applied": applied,
        "withheld": len(records) - applied,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negatives": true_negatives,
    }
    precision = true_positives / applied if applied else None
    recall = true_positives / positives if positives else None
    insufficient = [f"{name}: {counts[name]} < {limit}" for name, limit in MINIMUMS.items() if counts[name] < limit]
    failures = []
    if precision is None or precision < precision_target:
        failures.append(f"precision below {precision_target:.2f} or undefined")
    if recall is None or recall < RECALL_TARGET:
        failures.append(f"recall below {RECALL_TARGET:.2f} or undefined")
    return {
        "status": "insufficient" if insufficient else "fail" if failures else "pass",
        "counts": counts,
        "precision": precision,
        "precision_wilson_95": wilson(true_positives, applied),
        "recall": recall,
        "recall_wilson_95": wilson(true_positives, positives),
        "publication_coverage": applied / len(records) if records else None,
        "abstention_rate": (len(records) - applied) / len(records) if records else None,
        "insufficient_reasons": insufficient,
        "threshold_failures": failures,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("decisions", type=Path, help="independent held-out labeled decisions as JSONL")
    parser.add_argument("--development-groups", type=Path, help="JSON array of development group IDs or the fitted calibration artifact")
    args = parser.parse_args(argv)
    try:
        development_groups = set()
        development_digest = None
        if args.development_groups:
            data = args.development_groups.read_bytes()
            inventory = json.loads(data, object_pairs_hook=unique_object)
            if isinstance(inventory, dict) and "schema_version" in inventory:
                from .calibration import validate_artifact

                groups = validate_artifact(inventory)["development_groups"]
                inventory = groups["train"] + groups["calibration"]
            if not isinstance(inventory, list) or not all(identifier(value) for value in inventory):
                raise ValueError("development groups must be a JSON array of nonempty, trimmed strings")
            if len(inventory) != len(set(inventory)):
                raise ValueError("duplicate development group ID")
            development_groups = set(inventory)
            development_digest = hashlib.sha256(data).hexdigest()
        decisions, digest = read_decisions(args.decisions, development_groups)
    except (OSError, ValueError, UnicodeError) as error:
        parser.error(str(error))

    categories = {
        category: summarize([row for row in decisions if row["category"] == category], target)
        for category, target in PRECISION_TARGETS.items()
    }
    statuses = {result["status"] for result in categories.values()}
    status = "insufficient" if "insufficient" in statuses else "fail" if "fail" in statuses else "pass"
    report = {
        "status": status,
        "policy": {
            "version": "initial-release-v1",
            "minimum_precision": PRECISION_TARGETS,
            "minimum_recall": RECALL_TARGET,
            "minimum_counts_per_category": MINIMUMS,
            "gate_uses": "point estimates and independent sample minima; intervals are reported separately",
        },
        "decisions_sha256": digest,
        "development_groups_sha256": development_digest,
        "development_overlap_checked": args.development_groups is not None,
        "categories": categories,
        "interpretation": (
            "This gate checks the supplied labeled decisions, not model quality beyond that sample. "
            "Label correctness, representative coverage, and untouched held-out provenance require "
            "independent evidence. Intervals are Wilson 95% sampling intervals, not per-claim probabilities. "
            "Freeze policy and data before evaluation; never tune to held-out results."
        ),
    }
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
