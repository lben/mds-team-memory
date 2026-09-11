"""Actual parser records for a reproduced acronym conflict; no quality claim."""
import json
from pathlib import Path

import pytest


CASES = json.loads((Path(__file__).parent / "fixtures/acronym_context_parser.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("conflict", [False, True])
def test_context_does_not_hide_asserted_expansion(case, conflict):
    from app.ml import syntax

    result = syntax.propose(case["parsed"], conflict=conflict)
    expected = [(case["expected_full_name"], "RSR")] if case["expected_full_name"] else []
    assert [(row["full_name"]["text"], row["short_name"]["text"]) for row in result] == expected
    for row in result:
        for field in ("full_name", "short_name"):
            span = row[field]
            assert case["parsed"]["body"][span["start"]:span["end"]] == span["text"]
            assert "confidence" not in span
