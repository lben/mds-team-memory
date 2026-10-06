"""Each decision carries the reason an administrator sees for it."""

import pytest

from app.ml import policy


def evidence(score=0.999, group="g1", **features):
    return {"polarity": "positive", "raw_score": score, "group_key": group, "text_hash": group, **features}


@pytest.mark.parametrize("kind,rows,state,reason", [
    # Near a threshold, a failing value is never printed as meeting it.
    ("concept", [evidence(0.97, grounded=True, label="named entity", eligibility_margin=0.6422)],
     "held", "It does not look like a subject of the post (relevance 0.642; needs at least 0.643)."),
    ("concept", [evidence(0.97, grounded=True, label="named entity", eligibility_margin=9.0)],  # earlier model's scale
     "held", "Its relevance score is from an earlier model and is not valid; it will be checked again."),
    ("concept", [evidence(0.9946, grounded=True, label="named entity", eligibility_margin=0.91)],
     "held", "The model is 99.4% sure this is a named thing; that needs 99.5%, or 80% with 2 independent posts (it has 1)."),
    ("concept", [evidence(0.97, grounded=True, label="named entity")],
     "held", "Its relevance to the post has not been checked yet."),
    ("concept", [evidence(0.9, group=group, grounded=True, label="named entity", eligibility_margin=0.91) for group in ("g1", "g2")],
     "active", "Named in 2 independent posts, and the model is 90.0% sure."),
    ("mention", [evidence(0.95, grounded=True, label="named entity")],
     "held", "The model is 95.0% sure the post mentions it; that needs 98.5%, or 94% with 2 independent posts (it has 1)."),
    ("alias", [evidence(0.9, explicit_definition=True)],
     "held", "Defined explicitly in 1 independent post; it needs 2."),
    ("alias", [evidence(0.846, explicit_definition=True)],
     "held", "Defined explicitly in 1 independent post; it needs 2 and 85% model certainty (it has 84%)."),
    ("relationship", [evidence(0.8, literal_support=True, assertion_allowed=True),
                      {**evidence(0.8, group="g2", literal_support=True, assertion_allowed=True), "polarity": "negative"}],
     "held", "A post states the opposite."),
    ("relationship", [evidence(0.6)], "weak", "No post states it in plain words; the model only inferred it."),
    ("relationship", [evidence(0.8, literal_support=True, assertion_allowed=True)],
     "held", "Stated in plain words in 1 independent post; it needs 2."),
    ("relationship", [evidence(0.697, group=group, literal_support=True, assertion_allowed=True) for group in ("g1", "g2")],
     "held", "The model is 69% sure; that needs 70%."),
    ("association", [evidence(group=group) for group in ("g1", "g2")],
     "weak", "They appear together in 2 independent posts; this is only a suggested link."),
    ("concept", [], "withdrawn", "No current post supports it."),
])
def test_reason_matches_the_decision(kind, rows, state, reason):
    assessed = policy.assess(kind, rows)
    assert (assessed[0], assessed[2]) == (state, reason)
