"""Outer-source alias controls using retained local parser/model observations.

These deliberately isolate the same later window under varied source scopes.
They are conditional boundary regressions, not new inference observations.
"""
import copy
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("context,asserted", [
    ("Hypothesis:\n", False),
    ("# Hypothesis\n", False),
    ("> ", False),
    ('"', False),
    ("```text\n", False),
    ("Hypothesis:\n```text\nVerified configuration:\n```\n", False),
    ("Hypothesis:\nThe rejected candidate is archived.\nVerified configuration:\n", True),
    ("Verified configuration:\n", True),
])
@pytest.mark.parametrize("with_roles", [False, True])
def test_runtime_alias_and_conflict_records_retain_full_source_scope(monkeypatch, context, asserted, with_roles):
    from app.ml import relation_syntax, runtime, syntax
    from app.ml.sources import digest
    from test_ml_copular_integration import CASES, MODELS

    post = CASES[0]["posts"][1]
    fragment = post["body"]
    stages = {stage["stage"]: copy.deepcopy(stage) for stage in post["recorded"]["stages"]}
    # Both local role and unscored conflict grammars accept this retained parse.
    assert syntax.propose(stages["syntax"])
    assert syntax.propose(stages["syntax"], conflict=True)
    suffix = '"' if context == '"' else "\n```" if context == "```text\n" else ""
    filler = "A neutral archival context line is being transcribed. " * 50
    body = context + filler + fragment + suffix
    offset = len(context) + len(filler)
    assert offset > 192 * 8
    assert relation_syntax.SourceScope(body).asserted(offset, offset + len(fragment)) == asserted
    if not with_roles:
        stages["alias_roles"]["raw"] = {"alias_definition": []}
    model = object.__new__(runtime.LocalModels)
    model.entity_schema, model.relation_schema, model.alias_schema = "generic_entities", "relations", "alias_roles"
    model.manifest = {"models": MODELS}
    model.tokenizer = None
    model.extractor = SimpleNamespace(extract=lambda local, schema, **kwargs: stages[schema]["raw"])
    model.syntax = lambda local: stages["syntax"]
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b"\0" * 4096))
    model.embedding = SimpleNamespace(encode=lambda local, **kwargs: vector)
    monkeypatch.setattr(runtime, "windows", lambda source, *args: [(offset, offset + len(fragment), fragment)])
    monkeypatch.setattr(syntax, "candidates", lambda parsed, **kwargs: syntax.propose(parsed, **kwargs))
    monkeypatch.setattr(relation_syntax, "serialize", lambda parsed: parsed)
    result = model.analyze(body)
    assert bool(result["corroborated_definitions"]) == (asserted and with_roles)
    assert bool(result["conflict_definitions"]) == asserted
    # A context restriction on identity does not erase independent mentions.
    assert result["concepts"]
    for record in [*result["corroborated_definitions"], *result["conflict_definitions"]]:
        assert record["source_text_hash"] == digest(body)
        for field in ("full_name", "short_name"):
            assert offset <= record[field]["start"] < record[field]["end"] <= offset + len(fragment)
            assert body[record[field]["start"]:record[field]["end"]] == record[field]["text"]
    if asserted and with_roles:
        observed = stages["alias_roles"]["raw"]["alias_definition"]
        assert [(r["full_name"]["confidence"], r["short_name"]["confidence"])
                for r in result["corroborated_definitions"]] == [
                    (r["full_name"]["confidence"], r["short_name"]["confidence"]) for r in observed]
