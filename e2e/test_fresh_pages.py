"""After an update, browsers load the new build instead of a cached page."""

from playwright.sync_api import Browser


def test_pages_are_rechecked_so_updates_show_without_a_hard_refresh(browser: Browser, base_url_server):
    context = browser.new_context()
    page = context.new_page()
    try:
        for path in ("/", "/documents"):
            assert page.goto(base_url_server.url + path).headers.get("cache-control") == "no-cache", path
    finally:
        context.close()
