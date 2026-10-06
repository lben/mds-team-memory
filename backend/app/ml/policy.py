"""Fixed conservative decisions; raw encoder scores are not probabilities."""

import math
import re

from . import syntax
from .runtime import inference_version, specific_name


VERSION = "grounded-cold-start-v12-bge"
# Cosine is relevance, not proof of substantiveness. Selected once on the
# spent development corpus before application tests; quality targets unchanged.
# eligibility_margin remains the JSON field name for storage compatibility.
MIN_ELIGIBILITY_MARGIN = 0.6423084735870361


def relevant(evidence):
    score = evidence.get("eligibility_margin")
    return type(score) in (int, float) and MIN_ELIGIBILITY_MARGIN <= score <= 1.0


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


# Publication thresholds; the explanations quote them, so they cannot drift apart.
CONCEPT_ALONE, CONCEPT_CORROBORATED = 0.995, 0.8
TOPIC_ALONE, TOPIC_CORROBORATED = 0.985, 0.94
ALIAS_MINIMUM = 0.85
RELATIONSHIP_MINIMUM, RELATIONSHIP_CANDIDATE = 0.70, 0.65
INDEPENDENT_SOURCES = 2
EXPERT_PEOPLE, EXPERT_POSTS, EXPERT_ACCEPTED = 2, 3, 1


def _posts(count):
    return f"{count} independent post{'' if count == 1 else 's'}"


# Reasons round values down and thresholds up, so a value that failed a
# threshold can never be printed as meeting it. A decision must never fail
# because of its explanation, so non-finite values are printed as they are.
def _down(value, places):
    try:
        return math.floor(round(value * 10**places, 6)) / 10**places
    except (OverflowError, ValueError):
        return value


def _up(value, places):
    return math.ceil(round(value * 10**places, 6)) / 10**places


def decide(kind, evidence):
    state, score, _ = assess(kind, evidence)
    return state, score


def assess(kind, evidence):
    """The decision, its score, and the reason in words an administrator can act on."""
    positive = [row for row in evidence if row["polarity"] == "positive"]
    groups, authors = independent_support(positive)
    score = max((row["raw_score"] for row in positive), default=0.0)
    if not positive:
        return "withdrawn", score, "No current post supports it."
    if kind == "concept":
        grounded = [row for row in positive if row.get("grounded", False)]
        # Unchecked or rejected names stay held; their extraction score is kept for review.
        supported = [row for row in grounded if relevant(row)]
        groups, _ = independent_support(supported)
        # Relation endpoints can corroborate presence, but cannot supply the
        # entity-score requirement: their confidence belongs to the relation.
        strong = max((row["raw_score"] for row in supported if row.get("label") != "relation endpoint"), default=0.0)
        active = strong >= CONCEPT_ALONE or (groups >= INDEPENDENT_SOURCES and strong >= CONCEPT_CORROBORATED)
        score = max((row["raw_score"] for row in grounded if row.get("label") != "relation endpoint"), default=0.0)
        if active:
            return "active", score, (f"The model is {_down(strong, 3):.1%} sure this is a named thing." if strong >= CONCEPT_ALONE
                                     else f"Named in {_posts(groups)}, and the model is {_down(strong, 3):.1%} sure.")
        margins = [row["eligibility_margin"] for row in grounded if type(row.get("eligibility_margin")) in (int, float)]
        # Values above 1.0 come from an earlier model's scale; relevant() rejects them.
        valid = [margin for margin in margins if margin <= 1.0]
        if not grounded:
            why = "The name could not be found exactly as written in the post."
        elif not supported and not margins:
            why = "Its relevance to the post has not been checked yet."
        elif not supported and not valid:
            why = "Its relevance score is from an earlier model and is not valid; it will be checked again."
        elif not supported:
            why = (f"It does not look like a subject of the post (relevance {_down(max(valid), 3):.3f}; "
                   f"needs at least {_up(MIN_ELIGIBILITY_MARGIN, 3):.3f}).")
        elif not strong:
            why = "It was only seen as part of a relationship, not named on its own."
        else:
            why = (f"The model is {_down(strong, 3):.1%} sure this is a named thing; that needs {_up(CONCEPT_ALONE, 3):.1%}, "
                   f"or {_up(CONCEPT_CORROBORATED, 2):.0%} with {_posts(INDEPENDENT_SOURCES)} (it has {groups}).")
        return "held", score, why
    if kind == "mention":
        supported = [row for row in positive if row.get("grounded", False)]
        groups, _ = independent_support(supported)
        # Relation endpoints can corroborate presence, but cannot supply the
        # entity-score requirement: their confidence belongs to the relation.
        entity = [row for row in supported if row.get("label") != "relation endpoint"]
        strong = max((row["raw_score"] for row in entity), default=0.0)
        if strong >= TOPIC_ALONE or (groups >= INDEPENDENT_SOURCES and strong >= TOPIC_CORROBORATED):
            return "active", strong, f"The model is {_down(strong, 3):.1%} sure the post mentions it."
        return "held", strong, (f"The model is {_down(strong, 3):.1%} sure the post mentions it; that needs "
                                f"{_up(TOPIC_ALONE, 3):.1%}, or {_up(TOPIC_CORROBORATED, 2):.0%} with "
                                f"{_posts(INDEPENDENT_SOURCES)} (it has {groups}).")
    if kind == "alias":
        explicit = [row for row in positive if (row.get("explicit_definition") or row.get("spelling_variant"))
                    and row.get("assertion_allowed", True)]
        groups, _ = independent_support(explicit)
        # Different expansions of the same spelling are checked against the
        # complete current finding set before any global term is published.
        if groups >= INDEPENDENT_SOURCES and score >= ALIAS_MINIMUM:
            return "active", score, f"Defined in {_posts(groups)}."
        why = f"Defined explicitly in {_posts(groups)}; it needs {INDEPENDENT_SOURCES}"
        if score < ALIAS_MINIMUM:
            why += f" and {_up(ALIAS_MINIMUM, 2):.0%} model certainty (it has {_down(score, 2):.0%})"
        return "held", score, why + "."
    if kind == "relationship":
        supported = [row for row in positive if row.get("literal_support") and row.get("assertion_allowed")]
        contradicted = any(row["polarity"] == "negative" and row.get("literal_support")
                           and row.get("assertion_allowed") for row in evidence)
        groups, authors = independent_support(supported)
        score = max((row["raw_score"] for row in supported), default=score)
        if groups >= INDEPENDENT_SOURCES and score >= RELATIONSHIP_MINIMUM and not contradicted:
            return "active", score, f"Stated in plain words in {_posts(groups)}."
        if contradicted:
            why = "A post states the opposite."
        elif not supported:
            why = "No post states it in plain words; the model only inferred it."
        elif groups < INDEPENDENT_SOURCES:
            why = f"Stated in plain words in {_posts(groups)}; it needs {INDEPENDENT_SOURCES}."
        else:
            why = f"The model is {_down(score, 2):.0%} sure; that needs {_up(RELATIONSHIP_MINIMUM, 2):.0%}."
        return ("held" if supported or score >= RELATIONSHIP_CANDIDATE else "weak"), score, why
    if kind == "association":
        if groups >= INDEPENDENT_SOURCES:
            return "weak", score, f"They appear together in {_posts(groups)}; this is only a suggested link."
        return "held", score, f"They appear together in {_posts(groups)}; a suggested link needs {INDEPENDENT_SOURCES}."
    raise ValueError(f"Unsupported decision kind: {kind}")


def assess_expertise(people, posts, accepted):
    """Expertise needs confirmations from several people on several posts, one of them accepted."""
    if people >= EXPERT_PEOPLE and posts >= EXPERT_POSTS and accepted >= EXPERT_ACCEPTED:
        return "active", f"Confirmed by {people} people across {posts} separate posts, with an accepted answer."
    return "held", (f"Needs confirmation from {EXPERT_PEOPLE} people (has {people}) across {EXPERT_POSTS} separate "
                    f"posts (has {posts}), including {EXPERT_ACCEPTED} accepted answer (has {accepted}).")


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
