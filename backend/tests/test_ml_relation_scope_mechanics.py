"""Explicit dependency mechanics, never presented as actual parser observations."""
import itertools
import re

import pytest


def declared_parse(body, definitions):
    """Hand-supplied roles isolate guard semantics without model inference."""
    matches = list(re.finditer(r"\w+|[^\w\s]", body))
    assert len(matches) == len(definitions)
    tokens = []
    for index, (match, (lemma, pos, tag, dep, head)) in enumerate(zip(matches, definitions)):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        tokens.append({"i": index, "text": match.group(), "start": match.start(), "end": match.end(),
                       "whitespace": body[match.end():end], "lemma": lemma, "pos": pos,
                       "tag": tag, "dep": dep, "head": head, "children": [], "ancestors": [], "is_quote": False})
    for token in tokens:
        if token["head"] != token["i"]:
            tokens[token["head"]]["children"].append(token["i"])
        parent = token["head"]
        while parent != token["i"] and parent not in token["ancestors"]:
            token["ancestors"].append(parent)
            if tokens[parent]["head"] == parent:
                break
            parent = tokens[parent]["head"]
    return {"body": body, "tokens": tokens,
            "noun_chunks": [{"start_token": t["i"], "end_token": t["i"] + 1, "root_token": t["i"], "end": t["end"]}
                            for t in tokens if t["pos"] == "PROPN"],
            "sentences": [{"start_token": 0, "end_token": len(tokens)}]}


def explicit_clauses(operator=None, *, coordinator="and", child_negative=False, shared=False):
    first = (["Maybe", "Orion", "uses", "Copper"] if operator == "maybe" else
             ["Orion", "possibly", "uses", "Copper"] if operator == "possibly" else
             ["Orion", "may", "use", "Copper"] if operator == "modal" else
             ["Orion", "neither", "uses", "Copper"] if operator == "neither" else
             ["Orion", "does", "not", "use", "Copper"] if operator == "negative" else
             ["Orion", "uses", "Copper"])
    words = first + [coordinator] + ([] if shared else ["Vega"]) + (["does", "not", "use"] if child_negative else ["uses"]) + ["Quartz", "."]
    roots = [i for i, word in enumerate(words) if word in {"use", "uses"}]
    definitions = []
    for i, word in enumerate(words):
        root = roots[0] if i <= len(first) else roots[1]
        if word in {"Orion", "Vega", "Copper", "Quartz"}:
            definitions.append((word, "PROPN", "NNP", "nsubj" if word in {"Orion", "Vega"} else "dobj", root))
        elif i in roots:
            definitions.append(("use", "VERB", "VB" if word == "use" else "VBZ", "ROOT" if i == roots[0] else "conj", roots[0]))
        elif word in {"Maybe", "possibly"}:
            definitions.append((word.lower(), "ADV", "RB", "advmod", root))
        elif word in {"may", "does"}:
            definitions.append(("may" if word == "may" else "do", "AUX", "MD" if word == "may" else "VBZ", "aux", root))
        elif word == "not":
            definitions.append((word, "PART", "RB", "neg", root))
        elif word == "neither":
            definitions.append((word, "CCONJ", "CC", "preconj", root))
        elif word == coordinator:
            definitions.append((word, "CCONJ", "CC", "cc", roots[0]))
        else:
            definitions.append((word, "PUNCT", ".", "punct", roots[1]))
    return declared_parse(" ".join(words).replace(" .", "."), definitions)


def supports(row):
    from app.ml import relation_syntax
    guard = relation_syntax.Guard(row, row["body"], 0, relation_syntax.SourceScope(row["body"]))
    spans = [{"name": t["text"], "start": t["start"], "end": t["end"]} for t in row["tokens"] if t["pos"] in {"NOUN", "PROPN"}]
    output = []
    for head, tail in itertools.permutations(spans, 2):
        for predicate in ("uses", "depends_on", "part_of", "produces", "replaces"):
            support = guard.support(head, tail, predicate)
            if support["literal_support"]:
                output.append((head["name"], predicate, tail["name"], support["polarity"]))
    return output


@pytest.mark.parametrize("operator", ["maybe", "possibly", "modal"])
@pytest.mark.parametrize("child_negative", [False, True])
def test_explicit_noncontrast_conjunct_cannot_drop_parent_uncertainty(operator, child_negative):
    assert supports(explicit_clauses(operator, child_negative=child_negative)) == []


def test_explicit_positive_conjunction_and_local_parent_negative_remain_distinct():
    assert supports(explicit_clauses()) == [("Orion", "uses", "Copper", "positive"), ("Vega", "uses", "Quartz", "positive")]
    assert supports(explicit_clauses("negative")) == [("Orion", "uses", "Copper", "negative"), ("Vega", "uses", "Quartz", "positive")]


def test_explicit_contrasting_subject_keeps_its_independent_assertion():
    from ml_relation_helpers import CASES
    assert supports(explicit_clauses("modal", coordinator="but")) == [("Vega", "uses", "Quartz", "positive")]
    # This control really was parsed earlier; its unchanged gold remains valid.
    assert supports(CASES["modal_separate_clause"]["parse"]) == [("Vega", "produces", "Quartz", "positive")]


@pytest.mark.parametrize("shared", [False, True])
def test_disjunctive_predicate_arms_are_not_asserted(shared):
    assert supports(explicit_clauses(coordinator="or", shared=shared)) == []


def test_explicit_nor_needs_neither_and_never_becomes_positive():
    assert supports(explicit_clauses("neither", coordinator="nor")) == [
        ("Orion", "uses", "Copper", "negative"), ("Vega", "uses", "Quartz", "negative")]
    assert supports(explicit_clauses("negative", coordinator="nor")) == [("Orion", "uses", "Copper", "negative")]


def nested_alternatives(*, contrast=False, flat=False):
    words = ["Orion", "uses", "Copper", "but" if contrast else "and", "Vega", "uses", "Quartz", "or", "Helios", "uses", "Silver", "."]
    return declared_parse(" ".join(words).replace(" .", "."), [
        ("Orion", "PROPN", "NNP", "nsubj", 1), ("use", "VERB", "VBZ", "ROOT", 1),
        ("Copper", "PROPN", "NNP", "dobj", 1), (words[3], "CCONJ", "CC", "cc", 1),
        ("Vega", "PROPN", "NNP", "nsubj", 5), ("use", "VERB", "VBZ", "conj", 1),
        ("Quartz", "PROPN", "NNP", "dobj", 5), ("or", "CCONJ", "CC", "cc", 1 if flat else 5),
        ("Helios", "PROPN", "NNP", "nsubj", 9), ("use", "VERB", "VBZ", "conj", 1 if flat else 5),
        ("Silver", "PROPN", "NNP", "dobj", 9), (".", "PUNCT", ".", "punct", 1)])


@pytest.mark.parametrize("contrast", [False, True])
def test_nested_alternative_requires_explicit_contrast_for_independent_outer_clause(contrast):
    # The former mechanics assumed nesting proved the outer conjunction's
    # scope. The actual parser also nests ambiguous unpunctuated "and ... or";
    # that structure alone cannot establish an independent assertion.
    assert supports(nested_alternatives(contrast=contrast)) == (
        [("Orion", "uses", "Copper", "positive")] if contrast else [])


def test_flat_alternative_holds_the_whole_ambiguous_group():
    assert supports(nested_alternatives(flat=True)) == []
