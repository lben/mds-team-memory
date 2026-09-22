"""Prospectively declared topical feedback; never inferred from quality labels."""

CONTRACT = "explicit_topics_v1"


def actors(case):
    return {row["actor"] for row in case.get("feedback", [])} | {
        row["actor"] for row in case.get("actions", []) if row.get("type") == "topic_feedback"}


def validate_action(case, action, *, scheduled=False):
    fields = {"actor", "post", "kind", "topics"} | ({"after_post"} if scheduled else {"type"})
    if set(action) - {"expected_status"} != fields:
        raise ValueError("Topic feedback has missing or unknown fields")
    index = action["post"]
    if type(index) is not int or not 0 <= index < len(case["posts"]):
        raise ValueError("Topic feedback must reference a case post")
    post = case["posts"][index]
    if post["visibility"] != "team" or post["kind"] not in {"note", "answer"}:
        raise ValueError("Topic feedback needs a public contribution")
    if not isinstance(action["actor"], str) or not action["actor"].strip():
        raise ValueError("Topic feedback needs a named actor")
    if action["kind"] not in {"helped", "accepted"} or (action["kind"] == "accepted" and post["kind"] != "answer"):
        raise ValueError("Topic feedback needs a valid outcome kind")
    topics = action["topics"]
    if (not isinstance(topics, list) or any(not isinstance(t, str) or not t.strip() for t in topics)
            or len({" ".join(t.casefold().split()) for t in topics}) != len(topics)):
        raise ValueError("Topic choices must be explicit unique canonical names")
    if action.get("expected_status", 200) not in {200, 401, 403, 409}:
        raise ValueError("Topic feedback needs an explicit supported HTTP expectation")
    if scheduled and (type(action["after_post"]) is not int
                      or not index <= action["after_post"] < len(case["posts"])):
        raise ValueError("Topic feedback must follow creation of its source")


def validate(case):
    actions = [a for a in case.get("actions", []) if a.get("type") == "topic_feedback"]
    if "feedback" not in case and not actions and "feedback_contract" not in case:
        return
    if case.get("feedback_contract") != CONTRACT or not isinstance(case.get("feedback", []), list):
        raise ValueError("Explicit topic feedback requires the prospective versioned contract")
    previous = -1
    for row in case.get("feedback", []):
        validate_action(case, row, scheduled=True)
        if row["after_post"] < previous:
            raise ValueError("Freeze feedback in chronological order")
        previous = row["after_post"]
    for row in actions:
        validate_action(case, row)


def apply(action, posts, clients, trace, request):
    """Exercise the ordinary user's bounded option search and exact selection."""
    client = clients[action["actor"]]
    path = f"/api/items/{posts[action['post']]['id']}/topic-feedback"
    context = request(client, "GET", path, trace)
    entry = {"feedback_action": action, "context": context, "lookups": []}
    trace.append(entry)
    selected, unavailable = [], []
    for name in action["topics"]:
        options = request(client, "GET", path, trace, params={"q": name})
        entry["lookups"].append({"query": name, "response": options})
        if options["context_token"] != context["context_token"]:
            raise RuntimeError("Feedback context changed during frozen topic selection")
        key = " ".join(name.casefold().split())
        matches = [row for row in options["topics"] if " ".join(row["name"].casefold().split()) == key]
        if len(matches) != 1:
            unavailable.append(name)
        else:
            row = matches[0]
            selected.append({"concept_id": row["concept_id"], "identity_revision": row["identity_revision"]})
    if unavailable:
        # Preserve the intended selection and missed options. Do not seed a
        # topic, guess an alias, choose another prediction, or rewrite gold.
        entry.update(status="UNAVAILABLE", unavailable=unavailable)
        return entry
    payload = {"kind": action["kind"], "expected_context": context["context_token"], "topics": selected}
    response = client.request("PUT", path, json=payload)
    entry.update(request=payload, status=response.status_code, response=response.json())
    expected = action.get("expected_status", 200)
    if response.status_code != expected:
        raise RuntimeError(f"Frozen topic feedback expected HTTP {expected}, received {response.status_code}")
    return entry
