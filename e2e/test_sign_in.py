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
