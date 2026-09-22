"""Prospective evaluator contract mechanics; no independent accuracy samples."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def feedback_module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import ml_feedback_check
    return ml_feedback_check


def case():
    return {"posts": [{"actor": "author", "kind": "note", "visibility": "team", "body": "A contribution."}],
            "feedback_contract": "explicit_topics_v1",
            "feedback": [{"after_post": 0, "post": 0, "actor": "reader", "kind": "helped", "topics": ["Topic A"]}]}


@pytest.mark.parametrize("change", ["missing_contract", "extra_field", "duplicate_topic", "bad_post", "early", "bad_kind", "bad_status"])
def test_feedback_schema_rejects_ambiguous_or_undeclared_inputs(feedback_module, change):
    value = case()
    action = value["feedback"][0]
    if change == "missing_contract":
        value.pop("feedback_contract")
    elif change == "extra_field":
        action["infer_from_expect"] = True
    elif change == "duplicate_topic":
        action["topics"].append("topic a")
    elif change == "bad_post":
        action["post"] = True
    elif change == "early":
        action["after_post"] = -1
    elif change == "bad_kind":
        action["kind"] = "accepted"
    else:
        action["expected_status"] = 500
    with pytest.raises(ValueError):
        feedback_module.validate(value)


def test_legacy_votes_do_not_become_explicit_topic_inputs(feedback_module):
    value = {"posts": [{"actor": "a", "helped_by": ["b"], "accepted": True}]}
    assert feedback_module.validate(value) is None
    assert feedback_module.actors(value) == set()


@pytest.mark.parametrize("available", [True, False])
def test_exact_declared_topic_only_and_missing_choice_remains_visible(feedback_module, available):
    action = case()["feedback"][0]
    options = [{"concept_id": "a", "name": "Topic A", "identity_revision": 4}] if available else []
    # A tempting other prediction is never a replacement for the frozen choice.
    options.append({"concept_id": "b", "name": "Topic B", "identity_revision": 1})
    sent, trace = [], []

    def request(client, method, path, trace, **kwargs):
        return {"context_token": "x" * 64, "topics": options}

    def put(method, path, json):
        sent.append(json)
        return SimpleNamespace(status_code=200, json=lambda: {"saved": True})

    result = feedback_module.apply(action, [{"id": "item"}], {"reader": SimpleNamespace(request=put)}, trace, request)
    if available:
        assert sent == [{"kind": "helped", "expected_context": "x" * 64,
                         "topics": [{"concept_id": "a", "identity_revision": 4}]}]
    else:
        assert not sent
        assert result["status"] == "UNAVAILABLE" and result["unavailable"] == ["Topic A"]
    assert result["feedback_action"] == action and len(result["lookups"]) == 1


def test_feedback_chronology_follows_source_drain_including_private_post(feedback_module, monkeypatch):
    import ml_quality_check as runner
    value = case()
    value["posts"].append({"actor": "author", "kind": "note", "visibility": "private", "body": "Private context."})
    value["feedback"][0]["after_post"] = 1
    order = []
    monkeypatch.setattr(feedback_module, "apply", lambda action, posts, *args:
                        order.append(("feedback", action["post"], len(posts))))
    monkeypatch.setattr(runner, "request", lambda client, method, path, trace, **kwargs:
                        {"item": {"id": "public"}} if path == "/api/capture" else {"id": "private"})
    runner.create_posts(value, {"author": object()}, [],
                        lambda index: order.append(("source_drain", index)),
                        lambda index: order.append(("feedback_drain", index)))
    assert order == [("source_drain", 0), ("source_drain", 1), ("feedback", 0, 2), ("feedback_drain", 1)]


def test_lifecycle_credit_removal_is_explicit_and_includes_actor_inventory(feedback_module):
    value = case()
    action = copy.deepcopy(value["feedback"][0])
    action.pop("after_post")
    action.update(type="topic_feedback", topics=[], actor="second-reader")
    value["actions"] = [action]
    feedback_module.validate(value)
    assert feedback_module.actors(value) == {"reader", "second-reader"}
