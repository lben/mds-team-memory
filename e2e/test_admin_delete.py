"""An administrator deletes someone else's answer and question from the Home page."""

import uuid

from playwright.sync_api import Browser, expect


def test_admin_deletes_an_answer_then_the_question(browser: Browser, base_url_server):
    base = base_url_server.url
    suffix = uuid.uuid4().hex[:6]
    base_url_server.create_admin(f"mod-{suffix}", "moderator-password")
    member = browser.new_context()
    admin = browser.new_context()
    try:
        writer = member.new_page()
        writer.goto(base + "/")
        api = writer.request
        assert api.post(base + "/api/auth/signup", data={"username": f"member-{suffix}", "password": "member-password"}).ok
        question = api.post(base + "/api/questions", data={"body": f"Who owns the {suffix} export?"}).json()["id"]
        assert api.post(base + f"/api/questions/{question}/answers", data={"body": f"The {suffix} desk."}).ok

        page = admin.new_page()
        page.goto(base + "/")
        page.get_by_test_id("profile-button").click()
        page.get_by_test_id("auth-username").fill(f"mod-{suffix}")
        page.get_by_test_id("auth-password").fill("moderator-password")
        page.get_by_test_id("do-sign-in").click()
        expect(page.get_by_test_id("admin-nav")).to_be_visible()

        card = page.get_by_test_id(f"question-{question}")
        card.locator(".q-head").click()
        card.get_by_test_id("delete-answer").click()
        page.get_by_test_id("ask-modal").get_by_role("button", name="Delete answer").click()
        expect(card.locator(".answer")).to_have_count(0)
        expect(card).to_contain_text("OPEN")

        card.get_by_test_id("delete-question").click()
        page.get_by_test_id("ask-modal").get_by_role("button", name="Delete question").click()
        expect(page.get_by_test_id(f"question-{question}")).to_have_count(0)
    finally:
        member.close()
        admin.close()
