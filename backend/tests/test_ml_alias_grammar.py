"""Explicit naming and adversarial controls from fixed pretrained outputs."""
import json
from pathlib import Path

import pytest

from app.ml import runtime, syntax

CASES = json.loads((Path(__file__).parent / "fixtures/alias_grammar_recorded.json").read_text())["cases"]


def identities(records):
    return {(record["full_name"]["text"], record["short_name"]["text"])
            for record in records
            if min(record[field]["confidence"] for field in ("full_name", "short_name")) >= 0.85}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["input"]["id"])
def test_explicit_alias_grammar_with_fixed_model_observations(case):
    source = case["input"]
    proposals = syntax.propose(case["parse"])
    records = list(runtime.corroborated_definitions(source["body"], case["raw_aliases"], proposals, 0))
    assert identities(records) == identities(case["candidate"])
    assert identities(case["baseline"]) <= identities(records)
    if source.get("control"):
        expected = source["expected"]
        allowed = {(expected["full_name"], expected["short_name"])} if expected else set()
        assert identities(records) <= allowed
        # Even unscored conflict candidates must describe an actual definition.
        conflicts = syntax.propose(case["parse"], conflict=True)
        assert {(p["full_name"]["text"], p["short_name"]["text"]) for p in conflicts} <= allowed
