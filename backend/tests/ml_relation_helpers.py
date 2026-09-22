"""Recorded parser boundary and explicit synthetic mechanics; no model quality claim."""
from contextlib import ExitStack
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

CORPUS = json.loads((Path(__file__).parent / "fixtures/ml_relation_scope_parses.json").read_text())
CASES = {row["id"]: row for row in CORPUS["cases"]}
BY_TEXT = {row["body"]: row["parse"] for row in CORPUS["cases"]}


def span(text, name, start=None, score=.99):
    start = text.index(name) if start is None else start
    return {"text": name, "start": start, "end": start + len(name), "confidence": score}


class DeclaredGuard:
    """Explicitly supplied support for lifecycle/score tests, not grammatical proof."""
    def __init__(self, text, declarations=()):
        self.text, self.declarations = text, declarations

    def support(self, head, tail, predicate):
        from app.ml.relation_syntax import REVISION
        default = {"literal_support": False, "polarity": "uncertain",
                   "start": min(head["start"], tail["start"]), "end": max(head["end"], tail["end"]),
                   "relation_guard_revision": REVISION}
        for declaration in self.declarations:
            if (head["name"], predicate, tail["name"], head["start"], tail["start"]) == (
                    declaration["head"], declaration["predicate"], declaration["tail"],
                    declaration["head_start"], declaration["tail_start"]):
                return {**default, "literal_support": True, "polarity": declaration["polarity"],
                        "start": declaration.get("start", 0), "end": declaration.get("end", len(self.text))}
        return default


def declaration(text, head, predicate, tail, polarity="positive", *, start=0, end=None):
    return {"head": head, "predicate": predicate, "tail": tail, "polarity": polarity,
            "head_start": text.index(head), "tail_start": text.index(tail), "start": start,
            "end": len(text) if end is None else end}


def analyze(text, entities=(), relations=None, *, full=False, guard=None, parsed=None, parser=True):
    from app.ml import relation_syntax, runtime
    model = object.__new__(runtime.LocalModels)
    model.entity_schema, model.relation_schema, model.alias_schema = "entities", "relations", "aliases"
    model.manifest = {"models": {"extractor": {"revision": "fixture"}, "syntax": {"revision": "fixture"}}}
    model.tokenizer = lambda body, **kwargs: {"offset_mapping": [m.span() for m in re.finditer(r"\S+", body)]}
    outputs = {"entities": {"entities": {"named entity": list(entities)}},
               "relations": {"relation_extraction": relations or {}}, "aliases": {}}
    model.extractor = SimpleNamespace(extract=lambda body, schema, **kwargs: outputs[schema])
    vector = SimpleNamespace(astype=lambda dtype: SimpleNamespace(tobytes=lambda: b"\0" * 4))
    model.embedding = SimpleNamespace(encode=lambda body, **kwargs: vector)
    parsed = parsed if parsed is not None else BY_TEXT.get(text)
    model.syntax = (lambda body: SimpleNamespace(text=body, row=parsed)) if parser and (parsed is not None or guard is not None) else None
    with ExitStack() as stack:
        if model.syntax is not None:
            stack.enter_context(patch.object(runtime.syntax, "candidates", return_value=[]))
            if guard is not None:
                stack.enter_context(patch.object(relation_syntax, "prepare", return_value=guard))
            else:
                stack.enter_context(patch.object(relation_syntax, "serialize", side_effect=lambda doc: doc.row))
        result = model.analyze(text)
    return result if full else result["relations"]
