"""BGE relevance and publication contracts; real model coverage is separate."""
import re
import numpy as np
import pytest
from app.ml import eligibility, policy


def row(group, score, margin=None, author=None, label="named entity"):
    evidence = {"polarity": "positive", "grounded": True, "raw_score": score, "label": label,
                "group_key": f"item:{group}", "text_hash": f"text-{group}", "author_id": author or f"author-{group}"}
    if margin is not None:
        evidence["eligibility_margin"] = margin
    return evidence


@pytest.mark.parametrize("evidence,state", [
    ([row(1, .999)], "held"),
    ([row(1, .999, margin=policy.MIN_ELIGIBILITY_MARGIN-1e-6)], "held"),
    ([row(1, .999, margin=policy.MIN_ELIGIBILITY_MARGIN)], "active"),
    ([row(1, .99, margin=.9)], "held"),
    ([row(1, .8, margin=.9), row(2, .5, margin=.9)], "active"),
    ([row(1, .8, margin=.9), row(2, .5, margin=.9, author="author-1")], "held"),
    ([row(1, .99, margin=.9), row(2, .999, margin=.3)], "held"),
    ([row(1, .8, margin=.9), row(2, .999, margin=.9, label="relation endpoint")], "active"),
    ([row(1, .999, margin=9.0)], "held"),  # historical Qwen scale is not cosine
    ([row(1, .999, margin=float("nan"))], "held"),
    ([row(1, .999, margin=True)], "held"),
])
def test_concepts_publish_only_from_valid_relevance_evidence(evidence, state):
    assert policy.decide("concept", evidence)[0] == state


class Tokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, body, **kwargs):
        return {"offset_mapping": [m.span() for m in re.finditer(r"\S+", body)]}


class Encoder:
    tokenizer = Tokenizer()
    max_seq_length = 512

    def __init__(self):
        self.calls = []

    def encode(self, bodies, **kwargs):
        self.calls.append(list(bodies))
        return np.asarray([[1., 0.] if "Citrine Pump" in body else [0., 1.] for body in bodies], dtype=np.float32)


def test_relevance_reuses_the_existing_encoder_and_source_embedding():
    encoder = Encoder()
    scorer = eligibility.Relevance(encoder)
    text = "Citrine Pump drives the loop."
    result = scorer.scores(text, [["Citrine Pump", 0]], {text: np.asarray([1., 0.], dtype=np.float32)})
    assert scorer.embedding is encoder
    assert result == {"citrine pump": 1.0}
    assert encoder.calls == [["Citrine Pump"]]


def test_long_sources_include_late_candidates_in_bounded_windows():
    encoder = Encoder()
    text = "word " * 1300 + "Citrine Pump drives the loop. " + "word " * 1300 + "Emerald Cell stores charge."
    names = [[name, text.index(name)] for name in ("Citrine Pump", "Emerald Cell")]
    scores = eligibility.Relevance(encoder).scores(text, names)
    assert scores == {"citrine pump": 1.0, "emerald cell": 1.0}
    sources = encoder.calls[0]
    assert any("Emerald Cell" in source for source in sources)
    assert all(len(source.split()) <= 510 for source in sources)


def test_empty_candidates_do_not_run_the_encoder():
    encoder = Encoder()
    assert eligibility.Relevance(encoder).scores("", []) == {}
    assert encoder.calls == []


def test_non_source_candidate_and_invalid_cosine_are_rejected():
    encoder = Encoder()
    scorer = eligibility.Relevance(encoder)
    with pytest.raises(ValueError, match="not contained"):
        scorer.scores("Citrine Pump drives the loop.", [["Unknown Cell", 0]])
    encoder.encode = lambda bodies, **kwargs: np.full((len(bodies), 2), np.nan)
    with pytest.raises(ValueError, match="cosine"):
        scorer.scores("Citrine Pump drives the loop.", [["Citrine Pump", 0]])


def test_bge_identity_invalidates_historical_judgments_and_model_changes():
    models = {"embeddings": {"revision": "a"}}
    assert eligibility.version(models).startswith("bge:a:")
    assert eligibility.version(models) != eligibility.version({"embeddings": {"revision": "b"}})
