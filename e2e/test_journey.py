"""W11: the complete critical browser journey through the single-window UI."""

import re

from playwright.sync_api import Browser, expect


def _answer_ask(pg, text=None, cancel=False):
    """Answer the app's own confirm/prompt (it replaced the native dialogs)."""
    pg.wait_for_selector("[data-testid=ask-modal]", timeout=6000)
    if cancel:
        pg.get_by_test_id("ask-cancel").click()
    else:
        if text is not None and pg.get_by_test_id("ask-input").count():
            pg.get_by_test_id("ask-input").fill(text)
        pg.get_by_test_id("ask-confirm").click()
    pg.wait_for_timeout(500)

def test_critical_journey(browser: Browser, base_url_server):
    base = base_url_server.url

    # Three real browser profiles: admin/installer, contributor A, expert B.
    admin_ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    a_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    b_ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    admin, a, b = admin_ctx.new_page(), a_ctx.new_page(), b_ctx.new_page()

    # ---- Home is the app: graph, composer, two columns. Old routes redirect.
    a.goto(base + "/capture")
    expect(a).to_have_url(re.compile(f"{base}/$"))
    expect(a.get_by_test_id("home-input")).to_be_visible()
    expect(a.get_by_test_id("knowledge-column")).to_be_visible()
    expect(a.get_by_test_id("questions-column")).to_be_visible()
    b.goto(base + "/")
    # B is the expert, so B needs an account: expertise is only routable to
    # someone whose name survives a cookie clear.
    b.get_by_test_id("profile-button").click()
    b.get_by_test_id("auth-username").fill("bernard")
    b.get_by_test_id("auth-password").fill("a-good-password")
    b.get_by_test_id("do-sign-up").click()
    b.wait_for_timeout(3000)
    b_label = b.evaluate("() => fetch('/api/profile').then(r => r.json()).then(p => p.label)")
    assert b_label == "bernard", b_label

    # ---- Admin: link visible to all, gated by credentials; curation lives here.
    expect(a.get_by_test_id("admin-nav")).to_be_visible()
    base_url_server.create_admin("installer", "first-admin-pw")
    admin.goto(base + "/admin/expertise")
    expect(admin.get_by_test_id("admin-auth")).to_contain_text("Admin sign in")
    admin.get_by_test_id("admin-username").fill("installer")
    admin.get_by_test_id("admin-password").fill("first-admin-pw")
    admin.get_by_test_id("admin-submit").click()
    expect(admin.get_by_test_id("mapping-table")).to_be_visible()
    # The curation table (concepts tab) is on this page now.
    panel = admin.get_by_test_id("map-admin-panel")
    expect(panel).to_be_visible()
    admin.get_by_test_id("tab-concepts").click()
    admin.get_by_test_id("concept-name").fill("Optima")
    admin.get_by_test_id("concept-aliases").fill("opt-feed")
    admin.get_by_test_id("add-concept").click()
    expect(panel).to_contain_text("opt-feed")
    admin.get_by_test_id("concept-name").fill("Olympus")
    admin.get_by_test_id("add-concept").click()
    expect(panel).to_contain_text("Olympus")
    admin.get_by_test_id("map-profile").select_option(label=b_label)
    admin.get_by_test_id("map-concept").select_option(label="Optima")
    admin.get_by_test_id("add-mapping").click()
    expect(admin.get_by_test_id("mapping-table")).to_contain_text(b_label)

    # ---- A captures knowledge from the composer (W1); it lands in the feed.
    a.get_by_test_id("home-input").fill("Optima does not consume SFT directly; Olympus processes the feed first.")
    a.get_by_test_id("do-capture").click()
    expect(a.get_by_test_id("success-modal")).to_contain_text("Thank you")
    a.get_by_role("button", name="Add another").click()
    expect(a.get_by_test_id("knowledge-column")).to_contain_text("Olympus processes the feed")

    # ---- B searches; the graph focuses on the matched concept; helped works.
    b.get_by_test_id("home-input").fill("olympus feed")
    b.get_by_test_id("do-search").click()
    expect(b.get_by_test_id("search-banner")).to_contain_text("olympus feed")
    expect(b.get_by_test_id("graph-title")).to_contain_text("Focused on Olympus")
    result = b.get_by_test_id("knowledge-column").locator(".result").first
    expect(result).to_contain_text("Olympus processes the feed")
    result.get_by_role("button", name="✓ Helped me").click()
    expect(result.get_by_role("button", name="✓ Marked helpful")).to_be_visible()
    b.get_by_test_id("clear-search").click()
    expect(b.get_by_test_id("graph-title")).to_contain_text("Knowledge graph")

    # ---- A asks by mistake and deletes it from the question card.
    a.get_by_test_id("home-input").fill("Oops wrong question entirely")
    a.get_by_test_id("do-ask").click()
    mistaken = a.locator(".question-card", has_text="Oops wrong question").first
    expect(mistaken).to_be_visible()
    mistaken.get_by_test_id("delete-question").click()
    _answer_ask(a)
    expect(a.locator(".question-card", has_text="Oops wrong question")).to_have_count(0)

    # ---- A asks for real (W2 spirit: same box, no retyping) — routed to B (W10).
    a.get_by_test_id("home-input").fill("Why is the opt-feed delayed on Mondays?")
    a.get_by_test_id("do-ask").click()
    question = a.locator(".question-card", has_text="opt-feed delayed").first
    expect(question).to_be_visible()

    # B sees it flagged for their expertise at the top of the questions column and answers (W3).
    b.reload()
    top_q = b.get_by_test_id("questions-column").locator(".question-card").first
    expect(top_q).to_contain_text("opt-feed delayed")
    expect(top_q).to_contain_text("NEEDS YOUR EXPERTISE")
    top_q.locator(".q-head").click()
    top_q.get_by_test_id("answer-text").fill("The upstream batch only lands at 08:30 on Mondays; Optima waits for it.")
    top_q.get_by_test_id("post-answer").click()
    expect(top_q).to_contain_text("08:30 on Mondays")

    # A accepts via the notification, which lands on the expanded question card.
    a.reload()
    a.get_by_test_id("bell").click()
    a.locator(".notif", has_text="new answer").first.click()
    opened = a.locator(".question-card", has_text="opt-feed delayed").first
    expect(opened.get_by_test_id("accept-answer")).to_be_visible()
    opened.get_by_test_id("accept-answer").click()
    expect(opened).to_contain_text("ACCEPTED")
    expect(opened).to_contain_text("RESOLVED")

    # ---- Searching surfaces the resolved question first in the questions column.
    b.get_by_test_id("home-input").fill("opt-feed Mondays delayed")
    b.get_by_test_id("do-search").click()
    first_q = b.get_by_test_id("questions-column").locator(".question-card").first
    expect(first_q).to_contain_text("RESOLVED")
    b.get_by_test_id("clear-search").click()

    # ---- Scratchpad privacy (W5) and share-selection (W6) — separate screen.
    a.goto(base + "/scratchpad")
    secret = "topsecret-alpha rotation password steps"
    shareable = "The AQUA runbook lives in the operations shared drive."
    editor = a.get_by_test_id("scratch-editor")
    editor.fill(secret + "\n" + shareable)
    expect(a.locator(".autosave")).to_contain_text("Saved")

    b.get_by_test_id("home-input").fill("topsecret-alpha")
    b.get_by_test_id("do-search").click()
    expect(b.get_by_test_id("nothing-found")).to_be_visible()
    expect(b.get_by_test_id("knowledge-column")).not_to_contain_text("topsecret")

    start = len(secret) + 1
    editor.evaluate(
        "(el, args) => { el.focus(); el.setSelectionRange(args.start, args.end); el.dispatchEvent(new KeyboardEvent('keyup')) }",
        {"start": start, "end": start + len(shareable)},
    )
    a.get_by_test_id("share-selection").click()
    expect(a.get_by_test_id("success-modal")).to_be_visible()
    a.get_by_role("button", name="Add another").click()

    b.get_by_test_id("home-input").fill("AQUA runbook")
    b.get_by_test_id("do-search").click()
    expect(b.get_by_test_id("knowledge-column")).to_contain_text("operations shared drive")
    b.get_by_test_id("home-input").fill("topsecret-alpha")
    b.get_by_test_id("do-search").click()
    expect(b.get_by_test_id("nothing-found")).to_be_visible()
    expect(b.get_by_test_id("knowledge-column")).not_to_contain_text("topsecret")
    # A failed search becomes a team question without retyping (PRD 2).
    b.get_by_test_id("ask-from-search").click()
    expect(b.locator(".question-card", has_text="topsecret-alpha")).to_have_count(1)
    b.locator(".question-card", has_text="topsecret-alpha").first.get_by_test_id("delete-question").click()
    _answer_ask(b)
    expect(b.locator(".question-card", has_text="topsecret-alpha")).to_have_count(0)

    # ---- Documents (W8) — separate screen; exact passage from a home search.
    a.goto(base + "/documents")
    a.locator("input[type=file]").set_input_files(
        {
            "name": "governance.txt",
            "mimeType": "text/plain",
            "buffer": (
                "Governance policy overview.\n\n"
                "SFT data is processed through Olympus before Optima consumes it downstream.\n\n"
                "All consumers retain lineage metadata.\n"
            ).encode(),
        }
    )
    expect(a.get_by_test_id("doc-viewer")).to_contain_text("governance.txt")

    a.goto(base + "/")
    a.get_by_test_id("home-input").fill("lineage metadata")
    a.get_by_test_id("do-search").click()
    doc_hit = a.locator(".result", has_text="governance.txt").first
    doc_hit.get_by_role("button", name="Open exact passage").click()
    expect(a).to_have_url(re.compile("/documents/.+passage="))
    matched = a.locator(".passage.matched")
    expect(matched).to_contain_text("lineage metadata")
    expect(matched.locator(".locator")).to_contain_text("Line 5")

    # ---- The graph on Home renders real nodes and never private content (W9).
    a.goto(base + "/")
    expect(a.locator(".graph-box canvas").first).to_be_visible()
    expect(a.get_by_test_id("graph-title")).to_contain_text("Knowledge graph")
    page_text = a.get_by_test_id("knowledge-column").inner_text()
    assert "topsecret" not in page_text

    # ---- Leaderboard (renamed from Impact): B earned accepted-answer points.
    b.goto(base + "/impact")  # old bookmark redirects
    expect(b).to_have_url(re.compile("/leaderboard$"))
    expect(b.locator("h1")).to_have_text("Leaderboard")
    row = b.locator(".leader-row.me")
    expect(row).to_contain_text("You")
    expect(row).to_contain_text("bernard")
    # B has an account, so the row must not carry the no-account marker.
    expect(row).not_to_contain_text("No account")
    expect(row.locator(".score")).to_contain_text("3")

    for ctx in (admin_ctx, a_ctx, b_ctx):
        ctx.close()
