"""Explicit user gestures against the compiled UI and real feedback API.

Canonical topics are created by an admin for deterministic UI checks. No model
is loaded and these cases are not release-quality evidence.
"""

import uuid

import pytest
from playwright.sync_api import expect


@pytest.fixture
def topic_case(browser, base_url_server):
    base = base_url_server.url
    suffix = uuid.uuid4().hex[:8]
    admin = browser.new_context()
    author = browser.new_context()
    reader = browser.new_context()
    contexts = [admin, author, reader]

    def post(context, path, body):
        response = context.request.post(base + path, data=body)
        assert response.ok, response.text()
        return response.json()

    admin_name = "topic_admin_" + suffix
    base_url_server.create_admin(admin_name, "a-private-test-password")
    post(admin, "/api/auth/login", {"username": admin_name, "password": "a-private-test-password"})
    for name, context in (("author", author), ("reader", reader)):
        post(context, "/api/auth/signup", {"username": name + suffix, "password": "a-private-test-password"})
    topics = {}
    for name in ("Cedar", "Willow", "Promotion"):
        title = name + " " + suffix
        topics[name] = post(admin, "/api/admin/concepts", {"name": title, "aliases": []})
    question = post(reader, "/api/questions", {"body": f"How can I repair {topics['Cedar']['name']} and {topics['Willow']['name']}?"})
    answer = post(author, f"/api/questions/{question['id']}/answers", {
        "body": f"I repaired {topics['Cedar']['name']} by resetting the relay. An unrelated ad mentions {topics['Promotion']['name']}."})
    response = author.request.post(base + "/api/capture", form={
        "body": f"For {topics['Cedar']['name']}, reset the relay after checking the fuse. " + (f"Additional context {suffix}. " * 35) + "Read the final safety note."})
    assert response.ok, response.text()
    note = response.json()["item"]
    page = reader.new_page()
    try:
        yield {"base": base, "post": post, "author": author, "reader": reader, "page": page,
               "topics": topics, "question": question, "answer": answer, "note": note}
    finally:
        for context in contexts:
            context.close()


def feedback(case, item_id):
    response = case["reader"].request.get(case["base"] + f"/api/items/{item_id}/topic-feedback")
    assert response.ok, response.text()
    return response.json()


def open_question(case):
    case["page"].goto(case["base"] + "/?question=" + case["question"]["id"])
    card = case["page"].get_by_test_id("question-" + case["question"]["id"])
    expect(card.get_by_test_id("accept-answer")).to_be_visible()
    return card


def test_acceptance_is_broad_until_topics_are_actively_confirmed(topic_case):
    c = topic_case
    card = open_question(c)
    card.get_by_test_id("accept-answer").click()
    panel = card.get_by_test_id("topic-credit-panel")
    expect(panel.get_by_role("checkbox")).to_have_count(2)
    expect(panel.get_by_role("checkbox", name=c["topics"]["Cedar"]["name"], exact=True)).not_to_be_checked()
    expect(panel.get_by_role("checkbox", name=c["topics"]["Willow"]["name"], exact=True)).not_to_be_checked()
    expect(panel.get_by_role("checkbox", name=c["topics"]["Promotion"]["name"], exact=True)).to_have_count(0)
    expect(panel.get_by_test_id("confirm-topic-credit")).to_be_disabled()
    panel.get_by_test_id("accept-without-topics").click()
    expect(card).to_contain_text("Accepted · no topic confirmed")
    assert not feedback(c, c["answer"]["id"])["feedback"]["accepted"]

    card.get_by_test_id("edit-accepted-topics").click()
    checkbox = panel.get_by_role("checkbox", name=c["topics"]["Cedar"]["name"], exact=True)
    checkbox.focus()
    checkbox.press("Space")
    expect(checkbox).to_be_checked()
    panel.get_by_test_id("confirm-topic-credit").click()
    expect(card.get_by_test_id("accepted-topic-summary")).to_contain_text("Resolved topics: " + c["topics"]["Cedar"]["name"])
    saved = feedback(c, c["answer"]["id"])["feedback"]["accepted"]
    assert [row["concept_id"] for row in saved if row["state"] == "current"] == [c["topics"]["Cedar"]["id"]]
    expect(checkbox).not_to_be_checked()
    panel.get_by_test_id("remove-topic-credit").click()
    expect(card.get_by_test_id("accepted-topic-summary")).to_contain_text("no topic confirmed")
    assert not any(row["state"] == "current" for row in feedback(c, c["answer"]["id"])["feedback"]["accepted"])


def test_atomic_acceptance_and_explicit_missing_topic_search(topic_case):
    c = topic_case
    card = open_question(c)
    card.get_by_test_id("accept-answer").click()
    panel = card.get_by_test_id("topic-credit-panel")
    for name in ("Cedar", "Willow"):
        panel.get_by_role("checkbox", name=c["topics"][name]["name"], exact=True).check()
    panel.get_by_test_id("confirm-topic-credit").click()
    expect(card.get_by_test_id("accepted-topic-summary")).to_contain_text(c["topics"]["Cedar"]["name"])
    expect(card.get_by_test_id("accepted-topic-summary")).to_contain_text(c["topics"]["Willow"]["name"])

    card.get_by_test_id("edit-accepted-topics").click()
    panel.get_by_text("Find another topic", exact=True).click()
    panel.get_by_role("searchbox", name="Search existing topics").fill(c["topics"]["Promotion"]["name"])
    found = panel.get_by_role("checkbox", name=c["topics"]["Promotion"]["name"], exact=True)
    expect(found).to_be_visible()
    expect(found).not_to_be_checked()
    # A searched choice is still not a confirmation. Closing preserves the
    # existing accepted selection; this is a UI check, not a quality label.
    panel.get_by_test_id("close-topic-credit").click()
    saved = feedback(c, c["answer"]["id"])["feedback"]["accepted"]
    assert {row["concept_id"] for row in saved if row["state"] == "current"} == {
        c["topics"]["Cedar"]["id"], c["topics"]["Willow"]["id"]}


@pytest.mark.parametrize("one_topic", [False, True])
def test_zero_or_single_question_topic_does_not_become_implicit_credit(topic_case, one_topic):
    c = topic_case
    body = f"How do I repair {c['topics']['Cedar']['name']}?" if one_topic else "What did we decide at lunch?"
    c["question"] = c["post"](c["reader"], "/api/questions", {"body": body})
    c["answer"] = c["post"](c["author"], f"/api/questions/{c['question']['id']}/answers", {"body": "We agreed to check the relay."})
    card = open_question(c)
    card.get_by_test_id("accept-answer").click()
    panel = card.get_by_test_id("topic-credit-panel")
    expect(panel.get_by_role("checkbox")).to_have_count(int(one_topic))
    if one_topic:
        expect(panel.get_by_role("checkbox")).not_to_be_checked()
    expect(panel.get_by_test_id("confirm-topic-credit")).to_be_disabled()
    panel.get_by_test_id("accept-without-topics").click()
    expect(card).to_contain_text("Accepted · no topic confirmed")
    assert not feedback(c, c["answer"]["id"])["feedback"]["accepted"]


@pytest.mark.parametrize("surface", ["feed", "search", "detail", "question"])
def test_helpful_topic_feedback_on_every_surface(topic_case, surface):
    c, page = topic_case, topic_case["page"]
    target = c["answer"] if surface == "question" else c["note"]
    if surface == "question":
        card = open_question(c).locator(".answer").first
    elif surface == "detail":
        page.goto(c["base"] + "/?item=" + target["id"])
        card = page.get_by_test_id("item-detail")
    else:
        page.goto(c["base"] + "/")
        if surface == "search":
            page.get_by_test_id("home-input").fill(c["topics"]["Cedar"]["name"] + " safety note")
            page.get_by_test_id("do-search").click()
            card = page.get_by_test_id("knowledge-column").locator(".result").first
        else:
            card = page.get_by_test_id("feed-" + target["id"])
    card.get_by_role("button", name="✓ Helped me", exact=True).click()
    expect(card.get_by_role("button", name="✓ Marked helpful", exact=True)).to_be_visible()
    assert not feedback(c, target["id"])["feedback"]["helped"]
    card.get_by_test_id("helpful-topic-feedback").click()
    panel = card.get_by_test_id("topic-credit-panel")
    expect(panel).to_contain_text(target["body"])
    checkbox = panel.get_by_role("checkbox", name=c["topics"]["Cedar"]["name"], exact=True)
    expect(checkbox).not_to_be_checked()
    checkbox.check()
    panel.get_by_test_id("confirm-topic-credit").click()
    expect(panel.get_by_test_id("current-topic-feedback")).to_contain_text(c["topics"]["Cedar"]["name"])
    panel.get_by_test_id("remove-topic-credit").click()
    expect(panel.get_by_test_id("topic-feedback-status")).to_contain_text("Topic credit removed")
    panel.get_by_test_id("close-topic-credit").click()
    expect(card.get_by_test_id("helpful-topic-feedback")).to_be_focused()
    assert not any(row["state"] == "current" for row in feedback(c, target["id"])["feedback"]["helped"])
    result = c["reader"].request.get(c["base"] + f"/api/items/{target['id']}").json()
    assert result["marked_helped"] is True and result["helped"] == 1


def test_stale_context_clears_draft_and_save_error_keeps_helpful_mark(topic_case):
    c, page = topic_case, topic_case["page"]
    target = c["note"]
    # Keep the revision poll stable so the stale-save path itself is exercised.
    page.route("**/api/ml/revision", lambda route: route.fulfill(json={"revision": 0}))
    page.goto(c["base"] + "/?item=" + target["id"])
    card = page.get_by_test_id("item-detail")
    card.get_by_role("button", name="✓ Helped me", exact=True).click()
    card.get_by_test_id("helpful-topic-feedback").click()
    panel = card.get_by_test_id("topic-credit-panel")
    checkbox = panel.get_by_role("checkbox", name=c["topics"]["Cedar"]["name"], exact=True)
    checkbox.check()
    edited = target["body"] + " The relay is now replaced."
    response = c["author"].request.put(c["base"] + f"/api/items/{target['id']}", data={"body": edited})
    assert response.ok, response.text()
    panel.get_by_test_id("confirm-topic-credit").click()
    expect(panel.get_by_test_id("topic-feedback-status")).to_contain_text("choose topics again")
    expect(panel).to_contain_text(edited)
    expect(checkbox).not_to_be_checked()
    expect(panel.get_by_test_id("confirm-topic-credit")).to_be_disabled()

    def reject_write(route):
        if route.request.method == "PUT":
            route.fulfill(status=500, json={"detail": "Temporary write failure"})
        else:
            route.continue_()

    page.route("**/api/items/*/topic-feedback", reject_write)
    checkbox.check()
    panel.get_by_test_id("confirm-topic-credit").click()
    expect(panel.get_by_role("alert")).to_contain_text("Your helpful mark is still saved")
    assert c["reader"].request.get(c["base"] + f"/api/items/{target['id']}").json()["marked_helped"] is True
