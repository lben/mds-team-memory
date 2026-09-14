"""Public evidence-selection regressions with synthetic model outputs."""
import json
import re

import pytest

from test_ml_automation import automation, _capture, _edge, _tags
from test_ml_runtime import _analyze, _span


@pytest.mark.parametrize("polarity", ["positive", "negative"])
@pytest.mark.parametrize("supported_first", [True, False])
def test_literal_relationship_evidence_survives_higher_unsupported_score(
        make_client, polarity, supported_first):
    from app.db import SessionLocal
    from app.ml import adapter, syntax
    from app.ml.models import Evidence, Finding
    from app.ml.sources import snapshot

    prefix = f"{polarity.title()}{'First' if supported_first else 'Last'}"
    names = f"{prefix} Pump", f"{prefix} Battery"
    owners = [make_client() for _ in range(3)]
    items = []
    metadata = "synthetic-evidence-selection", "fixture-embedding", 1024

    def apply(item_id, body=None, *, mixed=False, cached=False):
        with SessionLocal() as db:
            source = snapshot(db, "item", item_id)
            if source is None:
                result = None
            elif cached:
                result, saved = adapter.cached_result(db, source, metadata[0])
                assert saved == metadata
            else:
                entities = [_span(body, name, start=m.start(), score=.996)
                            for name in names for m in re.finditer(re.escape(name), body)]
                first = [_span(body, name, score=.76) for name in names]
                predictions = [{"head": first[0], "tail": first[1]}]
                if mixed:
                    last = [_span(body, name, start=body.rindex(name), score=.99) for name in names]
                    predictions.append({"head": last[0], "tail": last[1]})
                    if not supported_first:
                        predictions.reverse()
                result = _analyze(body, entities, {"uses": predictions}, full=True)
                result.update(chunks=[], conflict_definitions=[],
                              conflict_coverage_revision=syntax.CONFLICT_REVISION)
                if mixed:
                    assert {(r['polarity'], r['literal_support'], r['score']) for r in result['relations']} == {
                        (polarity, True, .76), (polarity, False, .99)}
            adapter.apply_source(db, "item", item_id, source, result, *metadata)
            db.commit()

    def capture(owner, body, *, mixed=False):
        item = _capture(owner, body)
        items.append((owner, item))
        apply(item, body, mixed=mixed)
        return item

    try:
        ordinary = f"{names[0]} uses {names[1]} during the morning test."
        first_id = capture(owners[0], ordinary)
        tags = _tags(owners[0], first_id)
        if polarity == "negative":
            capture(owners[1], f"{names[0]} uses {names[1]} during the evening run.")
            assert _edge(owners[0], *tags.values())["style"] == "solid"
        literal = f"{names[0]} {'does not use' if polarity == 'negative' else 'uses'} {names[1]}."
        unrelated = f"{names[0]} {'was not inspected alongside' if polarity == 'negative' else 'was inspected alongside'} {names[1]}."
        mixed_id = capture(owners[2], literal + " " + unrelated, mixed=True)
        for replay in (False, True):
            if replay:
                apply(mixed_id, cached=True)
            edge = _edge(owners[0], *tags.values())
            assert (edge is not None and edge["style"] == "solid" and edge["label"] == "uses") == (polarity == "positive")
            with SessionLocal() as db:
                row = (db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key)
                       .filter(Finding.kind == "relationship", Evidence.source_id == mixed_id,
                               Evidence.polarity == polarity).one())
                assert row.raw_score == .76
                assert json.loads(row.features)["literal_support"] is True
                assert (literal + " " + unrelated)[row.start:row.end] == literal
            if polarity == "positive":
                detail = owners[0].get(f"/api/graph/links/{edge['link_id']}/evidence").json()
                claim = next(c for c in detail['claims'] if c['predicate'] == 'uses' and c['state'] == 'active')
                assert claim['support_count'] == 2
                assert next(s['quote'] for s in claim['sources'] if s['source_id'] == mixed_id) == literal
        assert owners[2].delete(f"/api/items/{mixed_id}").status_code == 200
        items.remove((owners[2], mixed_id))
        # Deletion invalidates immediately; removing a contradiction permits
        # republication when the worker applies that invalidation.
        apply(mixed_id)
        edge = _edge(owners[0], *tags.values())
        assert (edge is not None and edge["style"] == "solid" and edge["label"] == "uses") == (polarity == "negative")
    finally:
        for owner, item in items:
            assert owner.delete(f"/api/items/{item}").status_code == 200
