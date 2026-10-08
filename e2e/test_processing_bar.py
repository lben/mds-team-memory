"""A new post's card shows how far processing has got, then what the post changed."""

import hashlib
import json
import sqlite3
import time
import uuid

from playwright.sync_api import Browser, Route, expect


def test_new_post_shows_progress_then_outcomes(browser: Browser, base_url_server):
    database = base_url_server.data_dir / "e2e.sqlite3"
    activity = base_url_server.data_dir / "e2e.sqlite3.ml-activity.json"
    token = uuid.uuid4().hex
    with sqlite3.connect(database) as db:  # as while the worker runs
        previous = db.execute("SELECT automation_enabled, worker_token, worker_lease_until FROM ml_state").fetchone()
        db.execute("UPDATE ml_state SET automation_enabled=1, worker_token=?, worker_lease_until=?",
                   (token, time.time() + 600))
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(base_url_server.url + "/")
        assert page.request.post(base_url_server.url + "/api/auth/signup",
                                 data={"username": f"writer-{uuid.uuid4().hex[:6]}", "password": "writer-password"}).ok
        page.reload()
        body = f"Night batch {uuid.uuid4().hex[:6]} lands before the morning report."
        page.get_by_test_id("home-input").fill(body)
        page.get_by_test_id("do-capture").click()
        page.get_by_role("button", name="Add another").click()
        card = page.locator("article.result", has_text=body)
        expect(card.get_by_test_id("processing-text")).to_have_text("Ingesting")

        # The worker picks it up and reaches the relationship step.
        with sqlite3.connect(database) as db:
            item = db.execute("SELECT id FROM knowledge_items WHERE body=?", (body,)).fetchone()[0]
            db.execute("UPDATE ml_jobs SET lease_token='w', lease_until=? WHERE source_id=?", (time.time() + 60, item))
        activity.write_text(json.dumps({
            "pid": 1, "started_at": 0, "lease": hashlib.sha256(token.encode()).hexdigest()[:16], "activity": None,
            "recent": [], "job": {"kind": "item", "id": item, "claimed_at": 0, "stage": "running inference",
                                  "stage_since": 0,
                                  "progress": {"step": "extracting relations", "window": 1, "windows": 1}}}))
        expect(card.get_by_test_id("processing-text")).to_have_text("Finding relationships")
        expect(card.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "46")

        # Finished: two outcomes are shown and the rest wait behind "+2 more".
        # (Which outcomes a post earns is decided and tested on the server.)
        def finished(route: Route):
            route.fulfill(json={item: {"state": "done", "percent": 100, "outcomes": [
                {"kind": "new_concept", "name": "Night batch"},
                {"kind": "tagged", "names": ["Morning report", "Billing"]},
                {"kind": "confirmed_connection", "name": "Night batch feeds Morning report"},
                {"kind": "noted_concept", "name": "Report desk"}]}})
        page.route("**/api/ml/contributions*", finished)
        text = card.get_by_test_id("processing-text")
        expect(text).to_contain_text("New concept: Night batch · Tagged with Morning report and Billing")
        more = card.get_by_test_id("processing-more")
        expect(more.get_by_role("tooltip")).to_be_hidden()
        more.hover()
        expect(more.get_by_role("tooltip")).to_be_visible()
        expect(more.get_by_role("tooltip")).to_contain_text("Confirmed connection: Night batch feeds Morning report")
        expect(more.get_by_role("tooltip")).to_contain_text(
            "Noted Report desk; one more post naming it will make it a concept")
        expect(card.get_by_role("progressbar")).to_have_attribute("aria-valuenow", "100")
    finally:
        context.close()
        activity.unlink(missing_ok=True)
        with sqlite3.connect(database) as db:
            db.execute("UPDATE ml_state SET automation_enabled=?, worker_token=?, worker_lease_until=?", previous)
