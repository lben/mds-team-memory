"""Conservative cold-start decisions; raw encoder scores are not probabilities."""

import json
import re

from .runtime import specific_name


VERSION = "grounded-cold-start-v1"


def acronym_definitions(text):
    """Resolve explicit definitions only; an acronym alone is not a global alias."""
    for match in re.finditer(r"\(([A-Z][A-Z0-9-]{1,11})\)", text):
        abbreviation = match.group(1)
        letters = re.sub(r"[^A-Z0-9]", "", abbreviation)
        prefix = text[max(0, match.start() - 160):match.start()].rstrip()
        words = list(re.finditer(r"[A-Za-z0-9]+", prefix))
        # Initials must spell the abbreviation. Hyphenated words participate;
        # optional small linking words do not force a false global equivalence.
        for count in range(len(letters), min(len(words), len(letters) + 3) + 1):
            selected = words[-count:]
            initials = "".join(word.group()[0].upper() for word in selected)
            meaningful = [word for word in selected if word.group().lower() not in {"of", "and", "the", "for"}]
            if initials != letters and "".join(word.group()[0].upper() for word in meaningful) != letters:
                continue
            name = prefix[selected[0].start():]
            if not specific_name(name):
                continue
            start = max(0, match.start() - 160) + selected[0].start()
            yield {"name": name, "alias": abbreviation, "start": start,
                   "end": match.end(), "alias_start": match.start(1), "alias_end": match.end(1)}
            break


def independent_support(evidence):
    """Count connected provenance groups once, including copied text."""
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for row in evidence:
        group, copy = root(row["group_key"]), root("text:" + row["text_hash"])
        parents[group] = copy
        if row.get("author_id"):
            parents[root(group)] = root("author:" + row["author_id"])
    groups = {root(row["group_key"]) for row in evidence}
    authors = {row["author_id"] for row in evidence if row.get("author_id")}
    return len(groups), len(authors)


def _cold_decision(kind, evidence):
    positive = [row for row in evidence if row["polarity"] == "positive"]
    groups, authors = independent_support(positive)
    score = max((row["raw_score"] for row in positive), default=0.0)
    if not positive:
        return "withdrawn", score
    if kind in {"concept", "mention"}:
        supported = [row for row in positive if row.get("grounded", False)]
        groups, _ = independent_support(supported)
        strong = max((row["raw_score"] for row in supported), default=0.0)
        active = strong >= 0.985 or (groups >= 2 and strong >= 0.94)
        return ("active" if active else "held"), strong
    if kind == "alias":
        explicit = [row for row in positive if (row.get("explicit_definition") or row.get("spelling_variant"))
                    and row.get("assertion_allowed", True)]
        groups, _ = independent_support(explicit)
        # Different expansions of the same spelling are checked against the
        # complete current finding set before any global term is published.
        return ("active" if groups >= 2 and score >= 0.85 else "held"), score
    if kind == "relationship":
        supported = [row for row in positive if row.get("literal_support") and row.get("assertion_allowed")]
        contradicted = any(row["polarity"] == "negative" and row.get("literal_support") for row in evidence)
        groups, authors = independent_support(supported)
        score = max((row["raw_score"] for row in supported), default=score)
        active = groups >= 2 and score >= 0.70 and not contradicted
        if active:
            return "active", score
        return ("held" if supported or score >= 0.65 else "weak"), score
    if kind == "association":
        return ("weak" if groups >= 2 else "held"), score
    raise ValueError(f"Unsupported decision kind: {kind}")


def artifact(db):
    from sqlalchemy import text

    if "ml_decision_policy" not in db.info:
        db.info["ml_decision_policy"] = json.loads(db.execute(text("SELECT decision_policy FROM ml_state WHERE id=1")).scalar_one())
    return db.info["ml_decision_policy"]


def features(evidence):
    positive = [row for row in evidence if row["polarity"] == "positive"]
    groups, authors = independent_support(positive)
    values = {"raw_score": max((row["raw_score"] for row in positive), default=0.0),
              "independent_groups": groups, "authors": max([authors, *(len(row.get("actors", [])) for row in positive)]),
              "literal_support": sum(bool(row.get("literal_support") and row.get("assertion_allowed")) for row in positive),
              "negative_support": sum(row["polarity"] == "negative" for row in evidence),
              "explicit_definitions": sum(bool(row.get("explicit_definition")) for row in positive),
              "outcome_originals": max((row.get("originals", 0) for row in positive), default=0),
              "accepted_answers": max((row.get("accepted_answers", 0) for row in positive), default=0)}
    return {name: value if name == "raw_score" else min(100, value) for name, value in values.items()}


def decide(kind, evidence, fitted=None):
    state, score = _cold_decision(kind, evidence)
    if fitted and kind in fitted["models"]:
        from .calibration import predict

        score = predict(fitted, kind, features(evidence))
        # Development fitting alone cannot relax the conservative evidence
        # policy. The frozen held-out gate must still establish release quality.
        if state == "active" and score < fitted["models"][kind]["threshold"]:
            state = "held"
    return state, score
