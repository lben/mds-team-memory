"""Enter in the sign-in form signs in; it never creates an account."""

from playwright.sync_api import Browser, expect


def test_enter_signs_in_and_never_creates_an_account(browser: Browser, base_url_server):
    base_url_server.create_admin("enter-user", "enter-user-password")
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(base_url_server.url + "/")
        page.get_by_test_id("profile-button").click()
        page.get_by_test_id("auth-username").fill("enter-newcomer")
        page.get_by_test_id("auth-password").fill("a-good-password")
        page.get_by_test_id("auth-password").press("Enter")
        expect(page.get_by_test_id("auth-error")).to_contain_text("New here? Click Create account.")
        assert page.request.post(base_url_server.url + "/api/auth/login", data={
            "username": "enter-newcomer", "password": "a-good-password"}).status == 401

        page.get_by_test_id("auth-username").fill("enter-user")
        page.get_by_test_id("auth-password").fill("enter-user-password")
        page.get_by_test_id("auth-username").press("Enter")
        expect(page.get_by_test_id("profile-button")).to_contain_text("enter-user")
    finally:
        context.close()


def test_an_expired_sign_in_shows_as_signed_out_everywhere(browser: Browser, base_url_server):
    import sqlite3

    base_url_server.create_admin("expiring-admin", "expiring-admin-password")
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(base_url_server.url + "/")
        page.get_by_test_id("profile-button").click()
        page.get_by_test_id("auth-username").fill("expiring-admin")
        page.get_by_test_id("auth-password").fill("expiring-admin-password")
        page.get_by_test_id("do-sign-in").click()
        expect(page.get_by_test_id("admin-nav")).to_be_visible()

        # The sign-in runs out while the page stays open and in use.
        with sqlite3.connect(base_url_server.data_dir / "e2e.sqlite3") as db:
            db.execute("""UPDATE sessions SET expires_at='2000-01-01 00:00:00'
              WHERE account_id=(SELECT id FROM accounts WHERE username='expiring-admin')""")
        page.get_by_test_id("home-input").fill("Written after the sign-in ran out")
        page.get_by_test_id("do-capture").click()
        expect(page.get_by_test_id("sign-in-reason")).to_contain_text("create an account")
        expect(page.get_by_test_id("auth-username")).to_be_visible()  # a way back in, not a Sign out button
        expect(page.get_by_test_id("sign-out")).to_have_count(0)
        page.get_by_test_id("auth-username").fill("expiring-admin")
        page.get_by_test_id("auth-password").fill("expiring-admin-password")
        with page.expect_navigation():  # signing in reloads the page
            page.get_by_test_id("do-sign-in").click()
        expect(page.get_by_test_id("home-input")).to_have_value("Written after the sign-in ran out")

        # Running out again while away: the admin pages agree with the sidebar.
        with sqlite3.connect(base_url_server.data_dir / "e2e.sqlite3") as db:
            db.execute("""UPDATE sessions SET expires_at='2000-01-01 00:00:00'
              WHERE account_id=(SELECT id FROM accounts WHERE username='expiring-admin')""")
        page.get_by_test_id("admin-nav").get_by_role("link", name="ML Queue").click()
        expect(page.get_by_test_id("ml-queue-auth")).to_contain_text("Administrator sign-in required")
        page.get_by_test_id("profile-button").click()
        expect(page.get_by_test_id("do-sign-in")).to_be_visible()  # offered a way back in, not a sign-out
        expect(page.get_by_test_id("sign-out")).to_have_count(0)
        expect(page.get_by_test_id("display-name")).to_have_count(0)  # names belong to accounts
    finally:
        context.close()


def test_signing_in_from_a_post_s_details_is_not_hidden_behind_it(browser: Browser, base_url_server):
    import uuid

    writer, visitor = browser.new_context(), browser.new_context()
    try:
        page = writer.new_page()
        page.goto(base_url_server.url + "/")
        api = page.request
        assert api.post(base_url_server.url + "/api/auth/signup",
                        data={"username": f"writer-{uuid.uuid4().hex[:6]}", "password": "writer-password"}).ok
        body = f"The {uuid.uuid4().hex[:6]} batch closes at noon."
        assert api.post(base_url_server.url + "/api/capture", form={"body": body}).ok

        page = visitor.new_page()
        page.goto(base_url_server.url + "/")
        for _ in range(2):  # the second time, the panel is already open
            page.get_by_test_id("knowledge-column").locator(".card.result", has_text=body).get_by_role(
                "button", name="Details").click()
            page.get_by_role("button", name="Sign in to suggest a correction").click()
            expect(page.get_by_test_id("item-detail")).to_have_count(0)
            expect(page.get_by_test_id("sign-in-reason")).to_contain_text("suggest a correction")
        page.get_by_test_id("auth-username").fill("still-typable")  # the panel is on top and usable
        expect(page.get_by_test_id("auth-username")).to_have_value("still-typable")
    finally:
        writer.close()
        visitor.close()


def test_the_scratchpad_needs_an_account_and_keeps_text_typed_as_the_sign_in_ran_out(browser: Browser, base_url_server):
    import sqlite3
    import uuid

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(base_url_server.url + "/scratchpad")
        expect(page.get_by_test_id("scratchpad-needs-account")).to_be_visible()
        expect(page.get_by_test_id("scratch-editor")).to_have_count(0)
        page.get_by_role("button", name="Sign in or create an account").click()
        expect(page.get_by_test_id("sign-in-reason")).to_contain_text("private scratchpad")
        name = f"notes-{uuid.uuid4().hex[:6]}"
        page.get_by_test_id("auth-username").fill(name)
        page.get_by_test_id("auth-password").fill("notes-password")
        with page.expect_navigation():
            page.get_by_test_id("do-sign-up").click()
        editor = page.get_by_test_id("scratch-editor")
        editor.fill("First line, saved while signed in.")
        expect(page.get_by_test_id("autosave")).to_contain_text("Saved")

        with sqlite3.connect(base_url_server.data_dir / "e2e.sqlite3") as db:
            db.execute("UPDATE sessions SET expires_at='2000-01-01 00:00:00' "
                       "WHERE account_id=(SELECT id FROM accounts WHERE username=?)", (name,))
        editor.fill("First line, saved while signed in.\nSecond line, typed after the sign-in ran out.")
        expect(page.get_by_test_id("auth-username")).to_be_visible()  # asked to sign in again
        page.get_by_test_id("auth-username").fill(name)
        page.get_by_test_id("auth-password").fill("notes-password")
        with page.expect_navigation():
            page.get_by_test_id("do-sign-in").click()
        expect(page.get_by_test_id("scratch-editor")).to_have_value(
            "First line, saved while signed in.\nSecond line, typed after the sign-in ran out.")
        stored = ""
        for _ in range(20):  # saved on the way out of the reload, or right after it
            stored = page.evaluate("()=>fetch('/api/scratchpad').then(r=>r.json()).then(s=>s.default.content)")
            if "Second line" in stored:
                break
            page.wait_for_timeout(250)
        assert "Second line, typed after the sign-in ran out." in stored
    finally:
        context.close()
