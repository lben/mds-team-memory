"""W11: the complete critical browser journey, driven through the real UI."""

import re

from playwright.sync_api import Browser, expect


def test_critical_journey(browser: Browser, base_url_server):
    base = base_url_server.url

    # Three real browser profiles: admin/installer, contributor A, expert B.
    admin_ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    a_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    b_ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    admin, a, b = admin_ctx.new_page(), a_ctx.new_page(), b_ctx.new_page()

    # ---- Root redirects to /capture; profiles get created.
    a.goto(base + "/")
    expect(a).to_have_url(re.compile("/capture$"))
    b.goto(base + "/")
    b_label = b.evaluate("() => fetch('/api/profile').then(r => r.json()).then(p => p.label)")

    # ---- The Expertise Routing link is visible to everyone, but demands credentials
    # and offers no way to create an account.
    expect(a.get_by_test_id("admin-nav")).to_be_visible()
    a.get_by_test_id("admin-nav").get_by_role("link").click()
    expect(a).to_have_url(re.compile("/admin/expertise$"))
    expect(a.get_by_test_id("admin-auth")).to_contain_text("Admin sign in")
    expect(a.get_by_test_id("mapping-table")).to_have_count(0)
    expect(a.get_by_test_id("admin-auth")).not_to_contain_text("Create")

    # Wrong credentials are refused and the tools stay hidden.
    a.get_by_test_id("admin-username").fill("installer")
    a.get_by_test_id("admin-password").fill("not-the-password")
    a.get_by_test_id("admin-submit").click()
    expect(a.locator(".form-error")).to_be_visible()
    expect(a.get_by_test_id("mapping-table")).to_have_count(0)

    # The installer creates the admin account with the deploy-time command.
    base_url_server.create_admin("installer", "first-admin-pw")

    admin.goto(base + "/admin/expertise")
    expect(admin.get_by_test_id("admin-auth")).to_contain_text("Admin sign in")
    admin.get_by_test_id("admin-username").fill("installer")
    admin.get_by_test_id("admin-password").fill("first-admin-pw")
    admin.get_by_test_id("admin-submit").click()
    expect(admin.get_by_test_id("mapping-table")).to_be_visible()

    # The other browser still has no admin session.
    a.reload()
    expect(a.get_by_test_id("admin-auth")).to_be_visible()
    a.goto(base + "/capture")

    # Concepts are created on the Context Map, where their effect is visible.
    admin.goto(base + "/context")
    admin.get_by_test_id("tab-concepts").click()
    admin.get_by_test_id("concept-name").fill("Optima")
    admin.get_by_test_id("concept-aliases").fill("opt-feed")
    admin.get_by_test_id("add-concept").click()
    expect(admin.get_by_test_id("map-admin-panel")).to_contain_text("opt-feed")
    admin.get_by_test_id("concept-name").fill("Olympus")
    admin.get_by_test_id("add-concept").click()
    expect(admin.get_by_test_id("map-admin-panel")).to_contain_text("Olympus")

    admin.goto(base + "/admin/expertise")
    admin.get_by_test_id("map-profile").select_option(label=b_label)
    admin.get_by_test_id("map-concept").select_option(label="Optima")
    admin.get_by_test_id("add-mapping").click()
    expect(admin.get_by_test_id("mapping-table")).to_contain_text(b_label)

    # ---- A captures body-only knowledge (W1) and finds it via Search.
    a.get_by_test_id("capture-text").fill("Optima does not consume SFT directly; Olympus processes the feed first.")
    a.get_by_test_id("share-knowledge").click()
    expect(a.get_by_test_id("success-modal")).to_contain_text("Thank you")
    a.get_by_role("button", name="Add another").click()

    a.goto(base + "/search")
    a.get_by_test_id("search-input").fill("olympus feed")
    a.get_by_test_id("run-search").click()
    expect(a.get_by_test_id("search-results")).to_contain_text("Olympus processes the feed")

    # ---- B searches the same topic and marks it helpful; A's impact grows.
    b.goto(base + "/search")
    b.get_by_test_id("search-input").fill("olympus feed")
    b.get_by_test_id("run-search").click()
    result = b.get_by_test_id("search-results").locator(".result").first
    result.get_by_role("button", name="✓ Helped me").click()
    expect(result.get_by_role("button", name="✓ Marked helpful")).to_be_visible()

    # ---- A's failed search becomes a prefilled question (W2), routed to expert B (W10).
    a.get_by_test_id("search-input").fill("Why is the opt-feed delayed on Mondays")
    a.get_by_test_id("run-search").click()
    expect(a.get_by_test_id("ask-from-search")).to_be_visible()
    a.get_by_test_id("ask-from-search").click()
    expect(a).to_have_url(re.compile("/questions/"))
    expect(a.get_by_test_id("question-detail")).to_contain_text("opt-feed delayed")

    # B sees the routed question among matching open questions and answers it (W3).
    b.goto(base + "/capture")
    expect(b.locator(".question-mini").first).to_contain_text("opt-feed delayed")
    b.locator(".question-mini").first.click()
    b.get_by_test_id("answer-text").fill("The upstream batch only lands at 08:30 on Mondays; Optima waits for it.")
    b.get_by_test_id("post-answer").click()
    expect(b.get_by_test_id("question-detail")).to_contain_text("08:30 on Mondays")

    # A accepts the answer.
    a.reload()
    a.get_by_test_id("accept-answer").click()
    expect(a.locator(".answer.accepted")).to_contain_text("08:30 on Mondays")
    expect(a.get_by_test_id("question-detail")).to_contain_text("RESOLVED")

    # ---- Scratchpad privacy (W5) and share-selection (W6).
    a.goto(base + "/scratchpad")
    secret = "topsecret-alpha rotation password steps"
    shareable = "The AQUA runbook lives in the operations shared drive."
    editor = a.get_by_test_id("scratch-editor")
    editor.fill(secret + "\n" + shareable)
    expect(a.locator(".autosave")).to_contain_text("Saved automatically")

    b.goto(base + "/search")
    b.get_by_test_id("search-input").fill("topsecret-alpha")
    b.get_by_test_id("run-search").click()
    expect(b.get_by_test_id("search-results")).to_contain_text("No matches")

    start = len(secret) + 1
    editor.evaluate(
        "(el, args) => { el.focus(); el.setSelectionRange(args.start, args.end); el.dispatchEvent(new KeyboardEvent('keyup')) }",
        {"start": start, "end": start + len(shareable)},
    )
    a.get_by_test_id("share-selection").click()
    expect(a.get_by_test_id("success-modal")).to_be_visible()
    a.get_by_role("button", name="Add another").click()

    b.get_by_test_id("search-input").fill("AQUA runbook")
    b.get_by_test_id("run-search").click()
    expect(b.get_by_test_id("search-results")).to_contain_text("operations shared drive")
    b.get_by_test_id("search-input").fill("topsecret-alpha")
    b.get_by_test_id("run-search").click()
    expect(b.get_by_test_id("search-results")).to_contain_text("No matches")

    # ---- Document upload, exact-passage search (W8).
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

    a.goto(base + "/search")
    a.get_by_test_id("search-input").fill("lineage metadata")
    a.get_by_test_id("run-search").click()
    doc_hit = a.locator(".result", has_text="governance.txt").first
    doc_hit.get_by_role("button", name="Open exact passage").click()
    expect(a).to_have_url(re.compile("/documents/.+passage="))
    matched = a.locator(".passage.matched")
    expect(matched).to_contain_text("lineage metadata")
    expect(matched.locator(".locator")).to_contain_text("Line 5")

    # ---- Context map shows team knowledge, never private content (W9).
    a.goto(base + "/context")
    expect(a.locator(".map-toolbar strong")).to_have_text("Local context: Optima")
    # The graph actually rendered nodes and the evidence panel lists real relations.
    expect(a.locator(".graph-box canvas").first).to_be_visible()
    expect(a.locator(".relation").first).to_be_visible()
    expect(a.locator(".map-side")).to_contain_text("mentioned in")
    expect(a.locator(".map-side")).not_to_contain_text("topsecret")
    # Ordinary users get no admin panel on the map.
    expect(a.get_by_test_id("map-admin-panel")).to_have_count(0)

    # ---- Admin curates the map: detected links can be rejected and reinstated.
    for i in range(3):
        b.goto(base + "/capture")
        b.get_by_test_id("capture-text").fill(
            f"Optima consumes the Olympus feed on run {i} before the downstream load."
        )
        b.get_by_test_id("share-knowledge").click()
        expect(b.get_by_test_id("success-modal")).to_be_visible()
        b.get_by_role("button", name="Add another").click()

    admin.goto(base + "/context")
    panel = admin.get_by_test_id("map-admin-panel")
    expect(panel).to_be_visible()
    row = panel.locator(".panel-body.links").first
    expect(row).to_contain_text("SUGGESTED")
    expect(row).to_contain_text("related to")

    # The drill-down shows the real contributions behind the detected link.
    row.locator(".count").click()
    evidence = admin.get_by_test_id("evidence-modal")
    expect(evidence).to_contain_text("Optima")
    expect(evidence).to_contain_text("mentioned together in")
    expect(evidence).to_contain_text("Olympus feed on run")
    # Evidence is drawn from real team content, never the private scratchpad.
    expect(evidence).not_to_contain_text("topsecret")
    admin.get_by_test_id("evidence-modal").get_by_role("button", name="Close").click()

    # Rejecting hides the link from the map for everyone...
    admin.once("dialog", lambda d: d.accept("Looks coincidental"))
    row.get_by_role("button", name="Reject").click()
    expect(panel.locator(".panel-body.links").first).to_contain_text("REJECTED")
    a.reload()
    expect(a.locator(".map-side")).not_to_contain_text("related to")

    # ...but it stays in the admin table with its evidence, and can be reinstated.
    expect(panel.locator(".panel-body.links").first).to_contain_text("Looks coincidental")
    panel.locator(".panel-body.links").first.get_by_role("button", name="Approve").click()
    expect(panel.locator(".panel-body.links").first).to_contain_text("CONFIRMED")
    a.reload()
    expect(a.locator(".map-side")).to_contain_text("related to")
    expect(a.locator(".map-side")).to_contain_text("confirmed")

    # Clicking the centred concept in the graph jumps to its row in the table.
    admin.get_by_test_id("tab-links").click()
    graph_box = admin.locator(".graph-box").bounding_box()
    admin.mouse.click(graph_box["x"] + graph_box["width"] / 2, graph_box["y"] + graph_box["height"] / 2)
    expect(admin.get_by_test_id("tab-concepts")).to_have_class(re.compile("active"))
    expect(panel.locator(".panel-body.concepts.highlight")).to_contain_text("Optima")

    # Renaming a relationship type updates the map immediately.
    admin.get_by_test_id("tab-types").click()
    admin.get_by_test_id("type-name").fill("feeds")
    admin.get_by_test_id("add-type").click()
    expect(panel).to_contain_text("feeds")
    admin.get_by_test_id("tab-links").click()
    panel.locator(".panel-body.links").first.locator("select").select_option(label="feeds")
    a.reload()
    expect(a.locator(".map-side")).to_contain_text("feeds")

    # A type in use cannot be deleted.
    admin.get_by_test_id("tab-types").click()
    type_row = panel.locator(".panel-body.types", has_text="feeds").first
    type_row.get_by_role("button", name="Delete").click()
    expect(admin.locator(".toast")).to_contain_text("used by 1 link")

    # ---- Impact: B earned accepted-answer points; leaderboard labels unverified.
    b.goto(base + "/impact")
    expect(b.locator(".impact-hero")).to_contain_text("1")
    row = b.locator(".leader-row.me")
    expect(row).to_contain_text("You · unverified")
    expect(row.locator(".score")).to_contain_text("3")

    # B also received an in-app notification for the accepted answer.
    b.get_by_test_id("bell").click()
    expect(b.locator(".notif-pop")).to_contain_text("accepted")

    for ctx in (admin_ctx, a_ctx, b_ctx):
        ctx.close()
