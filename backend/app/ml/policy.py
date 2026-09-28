"""Fixed conservative decisions; raw encoder scores are not probabilities."""

import math
import re

from . import syntax
from .runtime import inference_version, specific_name


VERSION = "grounded-cold-start-v11"
# Concept evidence counts only after the eligibility model judged that source's
# name substantive. Selected with the thresholds below by leave-one-domain-out
# development screening (ML_CURRENT_STATUS.md, September 27 batch 2).
MIN_ELIGIBILITY_MARGIN = 4.0


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


def decide(kind, evidence):
    positive = [row for row in evidence if row["polarity"] == "positive"]
    groups, authors = independent_support(positive)
    score = max((row["raw_score"] for row in positive), default=0.0)
    if not positive:
        return "withdrawn", score
    if kind == "concept":
        grounded = [row for row in positive if row.get("grounded", False)]
        # Unchecked or rejected names stay held; their extraction score is kept for review.
        supported = [row for row in grounded if (row.get("eligibility_margin") or -math.inf) >= MIN_ELIGIBILITY_MARGIN]
        groups, _ = independent_support(supported)
        # Relation endpoints can corroborate presence, but cannot supply the
        # entity-score requirement: their confidence belongs to the relation.
        strong = max((row["raw_score"] for row in supported if row.get("label") != "relation endpoint"), default=0.0)
        active = strong >= 0.995 or (groups >= 2 and strong >= 0.9)
        score = max((row["raw_score"] for row in grounded if row.get("label") != "relation endpoint"), default=0.0)
        return ("active" if active else "held"), score
    if kind == "mention":
        supported = [row for row in positive if row.get("grounded", False)]
        groups, _ = independent_support(supported)
        # Relation endpoints can corroborate presence, but cannot supply the
        # entity-score requirement: their confidence belongs to the relation.
        entity = [row for row in supported if row.get("label") != "relation endpoint"]
        strong = max((row["raw_score"] for row in entity), default=0.0)
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
        contradicted = any(row["polarity"] == "negative" and row.get("literal_support")
                           and row.get("assertion_allowed") for row in evidence)
        groups, authors = independent_support(supported)
        score = max((row["raw_score"] for row in supported), default=score)
        active = groups >= 2 and score >= 0.70 and not contradicted
        if active:
            return "active", score
        return ("held" if supported or score >= 0.65 else "weak"), score
    if kind == "association":
        return ("weak" if groups >= 2 else "held"), score
    raise ValueError(f"Unsupported decision kind: {kind}")


def single_alias_definitions(evidence):
    """A score belongs to both exact fields of one current role record."""
    for row in evidence:
        if row["polarity"] != "positive" or not row.get("assertion_allowed"):
            continue
        parts = row["model_version"].split(":")
        for method in row.get("definition_methods", []):
            if (method.get("origin") != "syntax_and_alias_role_record"
                    or method.get("syntax_rule_revision") != syntax.REVISION or not method.get("syntax_rules")
                    or len(parts) != 5 or not method.get("alias_model_revision") or not method.get("syntax_model_revision")):
                continue
            models = {"extractor": {"revision": method["alias_model_revision"]},
                      "embeddings": {"revision": parts[1]}, "syntax": {"revision": method["syntax_model_revision"]}}
            if row["model_version"] != inference_version(models):
                continue
            if all(isinstance(method.get(field), dict)
                   and type(method[field].get("confidence")) in (int, float)
                   and 0.85 <= method[field]["confidence"] <= 1 for field in ("full_name", "short_name")):
                yield method


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
