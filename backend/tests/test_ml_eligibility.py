"""Concept eligibility contract; the worker path is exercised in test_ml_worker."""
import pytest


def row(group, score, margin=None, author=None, label="named entity"):
    evidence = {"polarity": "positive", "grounded": True, "raw_score": score, "label": label,
                "group_key": f"item:{group}", "text_hash": f"text-{group}", "author_id": author or f"author-{group}"}
    if margin is not None:
        evidence["eligibility_margin"] = margin
    return evidence


@pytest.mark.parametrize("evidence,state", [
    ([row(1, .999)], "held"),                                   # not yet judged
    ([row(1, .999, margin=3.9)], "held"),                       # judged incidental
    ([row(1, .999, margin=4.0)], "active"),                     # one strong substantive source
    ([row(1, .99, margin=9.0)], "held"),                        # one source below 0.995
    ([row(1, .9, margin=9.0), row(2, .5, margin=9.0)], "active"),  # two independent substantive sources
    ([row(1, .9, margin=9.0), row(2, .5, margin=9.0, author="author-1")], "held"),  # one author is one source
    ([row(1, .99, margin=9.0), row(2, .999, margin=-1.0)], "held"),  # rejected evidence adds nothing
    ([row(1, .9, margin=9.0), row(2, .999, margin=9.0, label="relation endpoint")], "active"),
])
def test_concepts_publish_only_from_judged_substantive_evidence(evidence, state):
    from app.ml import policy

    assert policy.decide("concept", evidence)[0] == state


def test_long_sources_are_judged_in_windows_that_fit_the_context():
    from app.ml import eligibility

    verifier = object.__new__(eligibility.Verifier)
    verifier._tokens = str.split
    judged = []
    verifier._prefix = lambda body: judged.append(body) or 0
    verifier._margin = lambda prefix, question: 5.0
    filler = "word " * 3000
    text = f"{filler}Citrine Pump drives the loop. {filler}Emerald Cell stores charge. {filler}"
    names = [[name, text.index(name)] for name in ("Citrine Pump", "Emerald Cell")]

    assert verifier.margins(text, names) == {"citrine pump": 5.0, "emerald cell": 5.0}
    assert [name in body for body, (name, _) in zip(judged, names)] == [True, True]
    assert all(len(eligibility.post(body).split()) <= eligibility.POST_TOKENS for body in judged)
