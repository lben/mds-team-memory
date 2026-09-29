"""Concept publication judge for the spent expanded corpus (development evidence only).

Final state applies each case's delete/edit actions in order. Evidence per
normalized name comes from the production extractor's specific grounded
proposals in final public sources, with the application's provenance grouping
(post, author, copied text). A rule publishes a name from that evidence.

Metrics (per case set):
- complete-output precision: published names in the case's allowed concept list;
- selected-decision precision: selected positives published / (those + selected
  negatives published);
- selected recall: selected positives published / selected positives.

Selection is leave-one-domain-out: choose a rule on eleven domains, apply it
unchanged to the twelfth, pool the twelve held-out results.
"""
import collections
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "backend"))
from app.ml.policy import independent_support  # noqa: E402

FIXTURE = REPO / "backend/tests/fixtures/ml_heldout.json"
FIXTURE_SHA256 = "823ed8b564ba6d8009b5b9b331a81cf10c5870faa329f15af37f267f059d3464"
NO_FILTER = -math.inf
TAUS = [NO_FILTER, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0]
S1 = [0.5, 0.7, 0.8, 0.9, 0.94, 0.97, 0.985, 0.995]
S2 = [0.5, 0.7, 0.8, 0.9, 0.94]
CONTROL = (NO_FILTER, 0.985, 0.94)
TRAINING_PRECISION = 0.985


def normalize(value):
    return " ".join(value.casefold().split())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configs():
    return [(t, a, b) for t, a, b in itertools.product(TAUS, S1, S2) if b <= a]


def load(scores_path):
    assert sha(FIXTURE) == FIXTURE_SHA256
    cases = json.loads(FIXTURE.read_text())["cases"]
    extracted = {r["source_key"]: r for r in map(json.loads, (HERE / "extract-control/raw-sources.jsonl").read_text().splitlines())}
    scores = ({r["source_key"]: r["scores"] for r in map(json.loads, Path(scores_path).read_text().splitlines())}
              if scores_path else {})
    prepared = []
    for case in cases:
        bodies = {i: (f"{case['id']}/{i}/original", post["body"]) for i, post in enumerate(case["posts"])}
        for k, action in enumerate(case.get("actions", [])):
            if action["type"] == "delete":
                bodies.pop(action["post"], None)
            elif action["type"] == "edit":
                bodies[action["post"]] = (f"{case['id']}/{action['post']}/edit_{k}", action["body"])
            else:
                raise ValueError(action)
        evidence = collections.defaultdict(list)
        for index, (key, body) in bodies.items():
            row = extracted[key]
            assert row["body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
            unique = {}
            for p in row["proposals"]:
                token = (p["start"], p["end"], p["label"])
                if token not in unique or p["score"] > unique[token]["score"]:
                    unique[token] = p
            source_scores = {normalize(n): v for n, v in scores.get(key, {}).items()}
            for p in unique.values():
                if not p["specific_name"]:
                    continue
                name = normalize(p["name"])
                margin = source_scores[name]["margin"] if scores_path else None
                evidence[name].append({"group_key": f"{case['id']}/{index}", "author_id": case["posts"][index]["actor"],
                                       "text_hash": hashlib.sha256(body.encode()).hexdigest(),
                                       "raw_score": p["score"], "margin": margin})
        prepared.append({"id": case["id"], "domain": case["domain"], "evidence": dict(evidence),
                         "allowed": {normalize(n) for n in case["allowed"]["concepts"]},
                         "positives": [normalize(n) for n in case["expect"]["concepts"]],
                         "negatives": [normalize(n) for n in case["expect"]["absent_concepts"]]})
    return prepared


def published(case, config):
    tau, s1, s2 = config
    names = set()
    for name, rows in case["evidence"].items():
        counted = [r for r in rows if tau == NO_FILTER or r["margin"] >= tau]
        if not counted:
            continue
        strong = max(r["raw_score"] for r in counted)
        groups, _ = independent_support(counted)
        if strong >= s1 or (groups >= 2 and strong >= s2):
            names.add(name)
    return names


def metrics(cases, config):
    correct = wrong = hits = negatives_published = positives = 0
    for case in cases:
        names = published(case, config)
        correct += len(names & case["allowed"])
        wrong += len(names - case["allowed"])
        hits += sum(n in names for n in case["positives"])
        negatives_published += sum(n in names for n in case["negatives"])
        positives += len(case["positives"])
    return {"published": correct + wrong, "correct": correct, "unsupported": wrong,
            "complete_output_precision": correct / (correct + wrong) if correct + wrong else None,
            "selected_hits": hits, "selected_positives": positives,
            "selected_negatives_published": negatives_published,
            "selected_decision_precision": hits / (hits + negatives_published) if hits + negatives_published else None,
            "selected_recall": hits / positives if positives else None}


def wilson(successes, total, z=1.959963984540054):
    if not total:
        return None
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(centre - half, 4), round(centre + half, 4)]


def choose(cases):
    """Most selected hits at training precision >= 98.5% on both measures; ties prefer stricter rules."""
    scored = [(config, metrics(cases, config)) for config in configs()]
    qualified = [(c, m) for c, m in scored if (m["complete_output_precision"] or 0) >= TRAINING_PRECISION
                 and (m["selected_decision_precision"] or 0) >= TRAINING_PRECISION]
    if qualified:
        return max(qualified, key=lambda cm: (cm[1]["selected_hits"], cm[0]))
    return max(scored, key=lambda cm: (cm[1]["complete_output_precision"] or 0, cm[1]["selected_hits"], cm[0]))


def pooled(parts):
    total = collections.Counter()
    for m in parts:
        total.update({k: v for k, v in m.items() if isinstance(v, int)})
    t = dict(total)
    t["complete_output_precision"] = t["correct"] / t["published"] if t["published"] else None
    fired = t["selected_hits"] + t["selected_negatives_published"]
    t["selected_decision_precision"] = t["selected_hits"] / fired if fired else None
    t["selected_recall"] = t["selected_hits"] / t["selected_positives"]
    t["wilson_complete_output_precision"] = wilson(t["correct"], t["published"])
    t["wilson_selected_decision_precision"] = wilson(t["selected_hits"], fired)
    t["wilson_selected_recall"] = wilson(t["selected_hits"], t["selected_positives"])
    return t


def main(scores_path, out_path):
    cases = load(scores_path)
    report = {"fixture_sha256": FIXTURE_SHA256, "judge_sha256": sha(__file__),
              "scores_sha256": sha(scores_path) if scores_path else None,
              "control": pooled([metrics(cases, CONTROL)])}
    if scores_path:
        folds = []
        for domain in sorted({c["domain"] for c in cases}):
            train = [c for c in cases if c["domain"] != domain]
            test = [c for c in cases if c["domain"] == domain]
            config, fitted = choose(train)
            folds.append({"domain": domain, "config": config, "training": fitted, "held_out": metrics(test, config),
                          "held_out_control": metrics(test, CONTROL)})
        held = pooled([f["held_out"] for f in folds])
        final_config, final_fit = choose(cases)
        gates = {"complete_output_precision_at_least_98": (held["complete_output_precision"] or 0) >= 0.98,
                 "selected_decision_precision_at_least_98": (held["selected_decision_precision"] or 0) >= 0.98,
                 "selected_recall_at_least_50": held["selected_recall"] >= 0.5}
        report.update(folds=folds, pooled_held_out=held, gates=gates,
                      status="GO_TO_INTEGRATION_AND_FRESH_VALIDATION" if all(gates.values()) else "NO_GO",
                      all_data_selection={"config": final_config, "fit_optimistic": final_fit},
                      release_quality_pass=False)
    Path(out_path).write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("folds",)}, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1] if sys.argv[1] != "-" else None, sys.argv[2])
