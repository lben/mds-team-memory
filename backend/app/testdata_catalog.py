"""Public synthetic UAT content and deterministic selection; no database imports.

Evaluation labels, expected outputs and scripted edit/delete actions are excluded.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import random

DATA = Path(__file__).resolve().parents[1] / "testdata"
DATASETS = ("cross-domain", "expanded", "capacity")
CAPACITY_SIZE = 50_000
NAMES = ("PostgreSQL", "Kafka", "Redis", "Python", "Kubernetes", "Docker", "Prometheus",
         "Grafana", "SQLite", "Linux", "Airflow", "Spark", "Parquet", "FastAPI", "Nginx",
         "Terraform", "Ansible", "Elasticsearch", "Jenkins", "Git")
TEMPLATES = (
    "{a} uses {b} to store audit events. The team reviewed incident {x} on service {y}. The recorded duration was {n} seconds and the batch contained {m} records.",
    "A maintenance check for {a} found a connection issue with {b}. Operator {x} restarted job {y} after {n} attempts. The next run processed {m} records successfully.",
    "The team compared {a} with {b} for project {x}. Ticket {y} documents a memory limit of {n} MiB and a queue length of {m}. A later review will verify the results.",
    "Project {x} depends on {a}. Its {b} configuration uses deployment {y}. The observed retry interval was {n} seconds, with {m} pending requests at the end of the review.",
    "{a} produces events for {b}. The migration for workspace {x} started in partition {y}, copied {m} records, and completed its first validation after {n} seconds.",
)
LONG = " The operations review records deployment checks, recovery steps, timeout budgets, storage usage, backup status, and remaining observations for the next shift."
MEDIUM = " The review covers capacity, ownership, recovery, deployment, and the next maintenance window."


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def catalog(name):
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset {name!r}; choose {', '.join(DATASETS)}")
    if name == "capacity":
        spec = {"version": "capacity-notes-v1", "size": CAPACITY_SIZE, "names": NAMES,
                "templates": TEMPLATES, "long": LONG, "medium": MEDIUM}
        return [{"id": f"capacity-{i:05d}", "domain": "software_operations", "index": i}
                for i in range(CAPACITY_SIZE)], digest(spec)
    raw = (DATA / f"{name}.json").read_bytes()
    return json.loads(raw)["cases"], hashlib.sha256(raw).hexdigest()


def capacity_case(row):
    i = row["index"]
    rng = random.Random(f"seed:{i}")
    a, b = rng.sample(NAMES, 2)
    values = dict(a=a, b=b, x=f"{rng.getrandbits(40):010x}", y=f"{rng.getrandbits(40):010x}",
                  n=rng.randrange(11, 999), m=rng.randrange(1000, 99999))
    body = rng.choice(TEMPLATES).format(**values)
    body += f" Run reference seed-{i:06d}-{rng.getrandbits(64):016x}."
    body += LONG * 45 if i % 100 == 99 else MEDIUM * 9 if i % 10 == 9 else ""
    return {"id": row["id"], "domain": row["domain"],
            "posts": [{"actor": f"operator-{i % 50:02d}", "body": body,
                       "kind": "note", "visibility": "team"}]}


def datasets():
    result = []
    for name in DATASETS:
        cases, checksum = catalog(name)
        result.append({"dataset": name, "units": len(cases),
                       "unit": "items" if name == "capacity" else "scenarios",
                       "posts": len(cases) if name == "capacity" else sum(len(c["posts"]) for c in cases),
                       "topics": sorted({c["domain"] for c in cases}), "dataset_sha256": checksum})
    return result


def select_dataset(name, percent, seed=42, topics=None):
    try:
        amount = Decimal(str(percent))
    except InvalidOperation:
        raise ValueError("--percent must be a number greater than 0 and at most 100") from None
    if not amount.is_finite() or not 0 < amount <= 100:
        raise ValueError("--percent must be greater than 0 and at most 100")
    cases, checksum = catalog(name)
    available = {c["domain"] for c in cases}
    chosen_topics = sorted(set(topics or available))
    unknown = set(chosen_topics) - available
    if unknown:
        raise ValueError(f"Unknown topics: {', '.join(sorted(unknown))}; available: {', '.join(sorted(available))}")
    pool = [c for c in cases if c["domain"] in chosen_topics]
    count = int((len(pool) * amount / 100).to_integral_value(rounding=ROUND_CEILING))
    ranked = sorted(pool, key=lambda c: (digest([int(seed), c["id"]]), c["id"]))
    selected = {c["id"] for c in ranked[:count]}
    rows = [capacity_case(c) if name == "capacity" else c for c in pool if c["id"] in selected]
    selection = {"dataset": name, "dataset_sha256": checksum, "percent": format(amount.normalize(), "f"),
                 "seed": int(seed), "topics": chosen_topics, "eligible_units": len(pool),
                 "selected_units": count, "unit": "items" if name == "capacity" else "scenarios",
                 "posts": sum(len(c["posts"]) for c in rows), "case_ids": [c["id"] for c in rows]}
    selection["fingerprint"] = digest(selection)
    return selection, rows


def add_selection_arguments(parser):
    parser.add_argument("--dataset", required=True, choices=DATASETS)
    parser.add_argument("--percent", required=True, help="percentage of the topic-filtered pool; rounded up")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--topics", help="comma-separated exact topic names, quoted when containing spaces")


def selection_from_args(args):
    topics = [s.strip() for s in args.topics.split(",") if s.strip()] if args.topics else None
    if args.topics is not None and not topics:
        raise ValueError("--topics must contain at least one topic")
    return select_dataset(args.dataset, args.percent, args.seed, topics)
