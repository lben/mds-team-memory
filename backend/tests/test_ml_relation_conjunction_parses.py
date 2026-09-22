"""Real parser observations; fixed source controls, not extractor accuracy."""
import itertools
import json
from pathlib import Path

import pytest

CORPUS = json.loads((Path(__file__).parent / "fixtures/ml_relation_conjunction_parses.json").read_text())


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_actual_coordination_respects_frozen_text_scope(case):
    from app.ml import relation_syntax
    guard = relation_syntax.Guard(case["parse"], case["body"], 0, relation_syntax.SourceScope(case["body"]))
    actual = []
    for head, tail in itertools.permutations(case["entities"], 2):
        for predicate in ("uses", "depends_on", "part_of", "produces", "replaces"):
            result = guard.support(head, tail, predicate)
            if result["literal_support"]:
                actual.append({"head": head["name"], "predicate": predicate,
                               "tail": tail["name"], "polarity": result["polarity"]})
    assert actual == case["gold"]
