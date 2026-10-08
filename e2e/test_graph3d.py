"""The Home knowledge graph is 3D and interactive: hover, select, rotate, pan, zoom, filter, focus."""

import re
import uuid

from playwright.sync_api import Browser, expect


def _read_position(page, label):
    return page.evaluate("""label => {
        const canvas = document.querySelector('.graph-box canvas')
        const graph = canvas.graph3d
        const node = graph.nodes.find(n => n.label === label) ?? graph.nodes.find(n => n.label.startsWith(label))
        const point = graph.screenPosition(node.id)
        const box = canvas.getBoundingClientRect()
        return {x: box.left + point.x, y: box.top + point.y}
    }""", label)


def _position(page, label):
    """Where the graph draws a node, in page coordinates (from the renderer the canvas carries).

    Long labels are shortened on screen, so a label may be given by its start. New
    content elsewhere redraws the graph, so wait until the node stops moving.
    """
    point = _read_position(page, label)
    for _ in range(30):
        page.wait_for_timeout(100)
        settled = _read_position(page, label)
        if abs(settled["x"] - point["x"]) < 0.5 and abs(settled["y"] - point["y"]) < 0.5:
            return settled
        point = settled
    raise AssertionError(f"{label} kept moving")


def test_graph_is_3d_and_interactive(browser: Browser, base_url_server):
    base = base_url_server.url
    suffix = uuid.uuid4().hex[:5]
    hub, store = f"Citrine Hub {suffix}", f"Quartz Store {suffix}"
    base_url_server.create_admin(f"graph-{suffix}", "graph-admin-password")
    context = browser.new_context(viewport={"width": 1366, "height": 900})
    page = context.new_page()
    try:
        page.goto(base + "/")
        api = page.request
        assert api.post(base + "/api/auth/login", data={"username": f"graph-{suffix}", "password": "graph-admin-password"}).ok
        ids = {name: api.post(base + "/api/admin/concepts", data={"name": name, "aliases": []}).json()["id"] for name in (hub, store)}
        uses = api.post(base + "/api/admin/relationship-types", data={"name": f"uses {suffix}"}).json()["id"]
        assert api.post(base + "/api/graph/links", data={"src_id": ids[hub], "dst_id": ids[store], "type_id": uses,
                                                         "note": "Checked by the platform team"}).ok
        post = f"{hub} writes every receipt to {store} before noon."
        assert api.post(base + "/api/capture", form={"body": post}).ok
        page.reload()

        graph = page.get_by_test_id("graph")
        expect(page.get_by_test_id("graph-legend")).to_contain_text("Concept")
        expect(page.get_by_test_id("graph-legend")).to_contain_text("Post")
        explorer = page.get_by_test_id("graph-explorer")
        expect(explorer).to_contain_text(hub)
        expect(explorer).to_contain_text(post[:40])

        # Hover previews; click selects the node and its explorer entry.
        point = _position(page, hub)
        page.mouse.move(point["x"], point["y"])
        expect(page.get_by_test_id("graph-preview")).to_contain_text(hub)
        page.mouse.click(point["x"], point["y"])
        expect(explorer.locator(".graph3d-entry.active")).to_contain_text(hub)

        # Drag rotates, Shift+drag pans, the wheel zooms.
        box = graph.bounding_box()
        start = {"x": box["x"] + 30, "y": box["y"] + box["height"] - 80}
        before = _position(page, hub)
        page.mouse.move(start["x"], start["y"])
        page.mouse.down()
        page.mouse.move(start["x"] + 120, start["y"] - 30, steps=6)
        page.mouse.up()
        rotated = _position(page, hub)
        assert abs(rotated["x"] - before["x"]) + abs(rotated["y"] - before["y"]) > 3
        page.keyboard.down("Shift")
        page.mouse.move(start["x"], start["y"])
        page.mouse.down()
        page.mouse.move(start["x"] + 40, start["y"] + 25, steps=4)
        page.mouse.up()
        page.keyboard.up("Shift")
        panned = _position(page, hub)
        assert abs(panned["x"] - rotated["x"] - 40) < 2 and abs(panned["y"] - rotated["y"] - 25) < 2
        a, b = _position(page, hub), _position(page, store)
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, -400)
        page.wait_for_timeout(200)
        a2, b2 = _position(page, hub), _position(page, store)
        assert abs(a2["x"] - b2["x"]) + abs(a2["y"] - b2["y"]) > abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])

        # The type filter limits the explorer and the scene; double-click opens a post.
        page.get_by_test_id("graph-filter").select_option(label="Post")
        expect(explorer.locator(".eyebrow")).to_have_text(["Post"] * explorer.locator(".eyebrow").count())
        expect(explorer.locator(".graph3d-entry strong").filter(has_text=re.compile(f"^{re.escape(hub)}$"))).to_have_count(0)
        expect(explorer.locator(".graph3d-entry.active")).to_have_count(0)  # the hidden concept is no longer selected
        page.get_by_test_id("graph-fit").click()
        point = _position(page, post[:25])
        page.mouse.dblclick(point["x"], point["y"])
        expect(page.get_by_test_id("item-detail")).to_contain_text(post)
        page.get_by_test_id("item-detail").get_by_role("button", name="Close", exact=True).click()

        # Reset restores every type and frames all of them.
        page.get_by_test_id("graph-reset").click()
        expect(page.get_by_test_id("graph-filter")).to_have_value("all")
        expect(explorer).to_contain_text(hub)
        for name in (hub, store):
            point = _position(page, name)
            assert box["x"] <= point["x"] <= box["x"] + box["width"] and box["y"] <= point["y"] <= box["y"] + box["height"], name

        # Clicking the link explains it.
        page.get_by_test_id("graph-filter").select_option(label="Concept")
        page.get_by_test_id("graph-fit").click()
        a, b = _position(page, hub), _position(page, store)
        page.mouse.click((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2)
        expect(page.get_by_text("Why these are connected")).to_be_visible()
        expect(page.get_by_text("Checked by the platform team")).to_be_visible()
        page.get_by_role("button", name="Close").click()

        # Double-click focuses a concept; Full map returns.
        point = _position(page, hub)
        page.mouse.dblclick(point["x"], point["y"])
        expect(page.get_by_test_id("graph-title")).to_contain_text(f"Focused on {hub}")
        expect(explorer).to_contain_text("Focused concept")
        page.get_by_test_id("graph-full").click()
        expect(page.get_by_test_id("graph-title")).to_have_text("Knowledge graph")
    finally:
        context.close()
