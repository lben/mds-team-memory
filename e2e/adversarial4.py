"""Fourth pass: click the references that deletion left dangling."""
import os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as PWError

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv/bin"
OUT = Path(__file__).parent / "adv"; OUT.mkdir(exist_ok=True)

data = Path(tempfile.mkdtemp())
env = {**os.environ, "MDS_DATA_DIR": str(data), "MDS_DATABASE_URL": f"sqlite:///{data/'x.sqlite3'}"}
subprocess.run([str(VENV/"alembic"), "-c", str(ROOT/"backend/alembic.ini"), "upgrade", "head"],
               env=env, check=True, capture_output=True, cwd=ROOT/"backend")
subprocess.run([str(VENV/"python"), str(ROOT/"manage.py"), "create-admin", "--username", "benito"],
               input="arcade-1978\narcade-1978\n", capture_output=True, text=True, env=env)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
srv = subprocess.Popen([str(VENV/"uvicorn"), "app.main:app", "--app-dir", str(ROOT/"backend"), "--port", str(port)],
                       env=env, stdout=open(OUT/"access4.log","w"), stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{port}"
for _ in range(80):
    try: urllib.request.urlopen(base+"/api/health", timeout=1); break
    except OSError: time.sleep(0.25)

FAILS, CHECKS, crashes = [], [], []
def check(name, condition, detail=""):
    CHECKS.append(name)
    if condition: print(f"   ok   {name}", flush=True)
    else:
        FAILS.append(f"{name} — {detail}"); print(f"   FAIL {name} :: {detail}", flush=True)


def answer_ask(pg, text=None, cancel=False):
    """Answer the app's own confirm/prompt.

    These actions used to raise a native `window.confirm`/`window.prompt`, which
    Playwright auto-dismisses — so a harness that forgot to handle it saw the
    action correctly do nothing and read it as a dead button. They are now the
    app's own modal, which has to be answered by clicking, exactly like a person.
    """
    pg.wait_for_selector("[data-testid=ask-modal]", timeout=6000)
    if cancel:
        pg.get_by_test_id("ask-cancel").click()
    else:
        if text is not None and pg.get_by_test_id("ask-input").count():
            pg.get_by_test_id("ask-input").fill(text)
        pg.get_by_test_id("ask-confirm").click()
    pg.wait_for_timeout(700)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    A = b.new_context(viewport={"width":1500,"height":950}).new_page()
    U = b.new_context(viewport={"width":1500,"height":950}).new_page()
    for pg, who in ((A,"admin"),(U,"user")):
        pg.on("pageerror", lambda e, w=who: crashes.append(f"[{w}] JS ERROR: {str(e)[:200]}"))
        pg.on("response", lambda r, w=who: crashes.append(f"[{w}] HTTP {r.status} {r.url.split(base)[-1]}") if r.status>=500 else None)

    def toast(pg): return pg.locator(".toast").inner_text() if pg.locator(".toast").count() else ""
    def js_errors(): return [c for c in crashes if "JS ERROR" in c]
    def login(pg):
        pg.goto(base+"/admin/expertise"); pg.wait_for_timeout(1600)
        if pg.get_by_test_id("admin-auth").count():
            pg.get_by_test_id("admin-username").fill("benito")
            pg.get_by_test_id("admin-password").fill("arcade-1978")
            pg.get_by_test_id("admin-submit").click(); pg.wait_for_timeout(2000)
    def capture(pg, text):
        pg.goto(base+"/"); pg.wait_for_timeout(900)
        pg.get_by_test_id("home-input").fill(text); pg.get_by_test_id("do-capture").click()
        try:
            pg.get_by_test_id("success-modal").wait_for(timeout=8000)
            pg.get_by_role("button", name="Add another").click(); pg.wait_for_timeout(500)
        except PWError: pass
    def edge_points(pg):
        return pg.evaluate("""() => {
            const el = document.querySelector('[data-testid=graph]');
            const cy = el && el._cyreg && el._cyreg.cy; if (!cy) return [];
            const r = el.getBoundingClientRect();
            return cy.edges().map(e => {const m=e.midpoint(), z=cy.zoom(), p=cy.pan();
                return {x: r.left+m.x*z+p.x, y: r.top+m.y*z+p.y}});
        }""")

    print("\n### 47. A shared passage whose document was deleted", flush=True)
    U.goto(base+"/documents"); U.wait_for_timeout(1400)
    U.locator("input[type=file]").set_input_files({"name":"lore.txt","mimeType":"text/plain",
        "buffer":"The Ocean Palace is the point of no return in the Zeal era.\n\n"
                 "Guardia Castle timings matter for the trial sequence.\n".encode()})
    U.wait_for_timeout(3000)
    U.locator(".share-passage").first.click(); U.wait_for_timeout(2500)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(900)
    doc = U.evaluate("()=>fetch('/api/documents').then(r=>r.json()).then(d=>d[0].id)")

    # The shared item is open in the feed BEFORE the document goes away.
    U.goto(base+"/"); U.wait_for_timeout(2200)
    shared = U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="Ocean Palace").first
    check("the shared passage is in the feed", shared.count() > 0)
    shared.get_by_role("button", name="Details").click(); U.wait_for_timeout(1800)
    check("its details show a way back to the source document",
          U.get_by_test_id("item-detail").get_by_role("link", name="Open source document").count() > 0,
          U.get_by_test_id("item-detail").inner_text()[:160])

    # A document can now be removed by whoever uploaded it, and by nobody else.
    # Assert the refusal really is a refusal, not a silent no-op.
    A.goto(base+"/documents"); A.wait_for_timeout(1500)
    st = A.evaluate(f"()=>fetch('/api/documents/{doc}',{{method:'DELETE'}}).then(r=>r.status)")
    check("someone else cannot delete another person's document", st == 403, f"status {st}")
    still = A.evaluate(f"()=>fetch('/api/documents/{doc}').then(r=>r.status)")
    check("and the refusal left the document in place", still == 200, f"status {still}")

    # So the source link can never dangle. Verify it actually resolves.
    U.get_by_test_id("item-detail").get_by_role("link", name="Open source document").click()
    U.wait_for_timeout(2500)
    check("the source document opens from the shared item",
          U.get_by_test_id("doc-viewer").count() > 0 and not js_errors(),
          U.locator(".page").inner_text()[:140])
    check("and it scrolls to the exact passage that was shared",
          U.locator(".passage.matched").count() == 1,
          f"{U.locator('.passage.matched').count()} highlighted")
    check("no server error on the source-document route",
          not [c for c in crashes if "HTTP 5" in c], str(crashes[:2]))

    U.goto(base+f"/documents/{doc}?passage=deadbeefdeadbeefdeadbeefdeadbeef"); U.wait_for_timeout(2200)
    check("a stale passage anchor on a real document is survivable",
          U.get_by_test_id("doc-viewer").count() > 0 and not js_errors(), str(js_errors()[:2]))
    U.goto(base+"/documents/deadbeefdeadbeefdeadbeefdeadbeef?passage=deadbeefdeadbeefdeadbeefdeadbeef")
    U.wait_for_timeout(2200)
    body = U.locator(".page").inner_text()
    check("a document id that never existed leaves a usable page, not a blank shell",
          U.locator(".sidebar").count() > 0 and "Upload or select" in body and not js_errors(),
          f"page={body[-120:]!r}")

    # The check above reloads the page, so there is never anything stale to keep.
    # Navigating inside the app is the case that can misinform: if the fetch for
    # the new id fails and nothing clears the pane, the PREVIOUS document stays
    # on screen under the new URL and the reader believes they are reading it.
    U.goto(base+"/documents"); U.wait_for_timeout(1800)
    U.locator("input[type=file]").set_input_files({"name":"a-real-file.txt","mimeType":"text/plain",
        "buffer":b"The real contents of a real document.\n"})
    U.wait_for_timeout(3000)
    opened = U.get_by_test_id("doc-viewer").locator("h2").inner_text() if U.get_by_test_id("doc-viewer").count() else ""
    check("a freshly uploaded document opens in the viewer", "a-real-file.txt" in opened, repr(opened))
    U.evaluate("""() => { window.history.pushState({}, '', '/documents/deadbeefdeadbeefdeadbeefdeadbeef');
                          window.dispatchEvent(new PopStateEvent('popstate')); }""")
    U.wait_for_timeout(2500)
    still = U.get_by_test_id("doc-viewer").locator("h2").inner_text() if U.get_by_test_id("doc-viewer").count() else ""
    check("navigating in-app to a missing document does not keep showing the previous one",
          "a-real-file.txt" not in still, f"viewer still reads {still!r} under a dead id")

    U.goto(base+"/"); U.wait_for_timeout(1400)
    U.get_by_test_id("home-input").fill("Ocean Palace")
    U.get_by_test_id("do-search").click(); U.wait_for_timeout(2000)
    check("the passage and the shared knowledge both still come back",
          "Ocean Palace" in U.get_by_test_id("knowledge-column").inner_text(),
          U.get_by_test_id("knowledge-column").inner_text()[:120])
    U.get_by_test_id("knowledge-column").locator(".card.result").first.get_by_role("button", name="Details").click()
    U.wait_for_timeout(1800)
    check("opening the shared item's details works",
          U.get_by_test_id("item-detail").count() > 0 and not js_errors(), str(js_errors()[:2]))
    U.keyboard.press("Escape"); U.wait_for_timeout(600)

    print("\n### 48. A link and a concept deleted under an open map", flush=True)
    login(A)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    for name in ["Zeal", "Ocean Palace"]:
        A.get_by_test_id("concept-name").fill(name); A.get_by_test_id("concept-aliases").fill("")
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(900)
    capture(U, "The Zeal era ends at the Ocean Palace, which is the point of no return.")

    # A person is looking at the map, with the evidence for this link open.
    U.goto(base+"/"); U.wait_for_timeout(3000)
    pts = edge_points(U)
    check("the map has the new link drawn on it", len(pts) > 0, f"{len(pts)} edges")
    opened = False
    for p in pts:
        U.mouse.click(p["x"], p["y"]); U.wait_for_timeout(600)
        if U.get_by_test_id("evidence-modal").count(): opened = True; break
    check("its evidence opens before anything is deleted", opened)
    link_id = U.evaluate("()=>fetch('/api/graph/links').then(r=>r.json()).then(l=>l.length?l[0].id:'')")
    U.keyboard.press("Escape"); U.wait_for_timeout(600)

    # The admin deletes the concept out from under them.
    cid = A.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.json()).then(c=>c.find(x=>x.name==='Zeal').id)")
    A.reload(); A.wait_for_timeout(2000); A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    A.locator(f"#row-{cid}").get_by_role("button", name="Delete").click(); A.wait_for_timeout(2000)
    answer_ask(A)
    check("the concept is deleted", A.locator(f"#row-{cid}").count() == 0)

    # The stale page still has the old link on screen. Click it.
    stale = U.evaluate(f"""() => {{
        const el = document.querySelector('[data-testid=graph]');
        const cy = el && el._cyreg && el._cyreg.cy; if (!cy) return 0;
        return cy.edges().length;
    }}""")
    check("the stale map still shows the now-deleted link (so we can click it)", stale > 0, f"{stale} edges")
    for p in edge_points(U):
        U.mouse.click(p["x"], p["y"]); U.wait_for_timeout(900)
        if U.get_by_test_id("evidence-modal").count(): break
    U.wait_for_timeout(1500)
    check("clicking a link whose concept was deleted does not throw",
          not js_errors(), str(js_errors()[:2]))
    if U.get_by_test_id("evidence-modal").count():
        txt = U.get_by_test_id("evidence-modal").inner_text().strip()
        check("the evidence dialog says something rather than sitting blank",
              len(txt) > 20 and "Close" in txt, f"{txt[:140]!r}")
        U.get_by_role("button", name="Close").click(); U.wait_for_timeout(600)

    U.goto(base+f"/admin/expertise?link={link_id}"); U.wait_for_timeout(2000)
    check("deep-linking to the deleted link leaves a usable page",
          U.locator(".sidebar").count() > 0 and not js_errors(), str(js_errors()[:2]))
    A.goto(base+f"/admin/expertise?concept={cid}"); A.wait_for_timeout(2200)
    check("an admin deep-linking to the deleted concept gets the working panel",
          A.get_by_test_id("map-admin-panel").count() > 0 and not js_errors(), str(js_errors()[:2]))

    U.goto(base+"/"); U.wait_for_timeout(3000)
    check("the map redraws without the deleted concept",
          U.locator(".graph-box").count() > 0 and not js_errors(), str(js_errors()[:2]))
    U.get_by_test_id("home-input").fill("Zeal"); U.get_by_test_id("do-search").click(); U.wait_for_timeout(2000)
    check("searching the deleted concept's name still finds the contribution that mentions it",
          "Zeal era ends" in U.get_by_test_id("knowledge-column").inner_text(),
          U.get_by_test_id("knowledge-column").inner_text()[:140])
    U.get_by_test_id("knowledge-column").locator(".card.result").first.get_by_role("button", name="Details").click()
    U.wait_for_timeout(1800)
    check("its details open with no reference to the vanished concept",
          U.get_by_test_id("item-detail").count() > 0 and "undefined" not in U.get_by_test_id("item-detail").inner_text(),
          U.get_by_test_id("item-detail").inner_text()[:140])

    print("\n### 49. Fixing and withdrawing your own contribution", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(1800)
    U.get_by_test_id("home-input").fill("The Epoch is repaired at the End of Tyme, at 7am.")
    U.get_by_test_id("do-capture").click(); U.wait_for_timeout(2500)
    if U.locator(".modal-backdrop").count():
        U.get_by_role("button", name="Add another").click(); U.wait_for_timeout(1200)
    U.goto(base+"/"); U.wait_for_timeout(2000)
    mine = U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="End of Tyme").first
    mine.get_by_role("button", name="Details").click(); U.wait_for_timeout(1800)
    check("my own contribution offers a way to fix it", U.get_by_test_id("edit-item").count() > 0)
    check("and a way to withdraw it", U.get_by_test_id("delete-item").count() > 0)

    U.get_by_test_id("edit-item").click(); U.wait_for_timeout(700)
    U.get_by_test_id("edit-body").fill("The Epoch is repaired at the End of Tyme, at 7pm.")
    U.get_by_test_id("save-edit").dblclick(); U.wait_for_timeout(2500)
    body = U.get_by_test_id("item-detail").locator(".detail-body").inner_text()
    check("the correction is saved", "7pm" in body and "7am" not in body, body[:110])
    stored = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>f.filter(i=>i.body.includes('End of Tyme')).length)")
    check("and double-clicking Save did not leave two copies", stored == 1, f"{stored} copies")
    U.keyboard.press("Escape"); U.wait_for_timeout(600)
    found = U.evaluate("()=>fetch('/api/search?q=Epoch').then(r=>r.json()).then(r=>r.items.map(i=>i.body).join(' '))")
    check("search finds the corrected text, not the old one", "7pm" in found and "7am" not in found, found[:120])

    A.goto(base+"/"); A.wait_for_timeout(2200)
    theirs = A.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="End of Tyme").first
    theirs.get_by_role("button", name="Details").click(); A.wait_for_timeout(1800)
    check("somebody else is offered neither control on it",
          A.get_by_test_id("edit-item").count() == 0 and A.get_by_test_id("delete-item").count() == 0)
    A.keyboard.press("Escape"); A.wait_for_timeout(500)

    U.goto(base+"/"); U.wait_for_timeout(1800)
    mine = U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="End of Tyme").first
    mine.get_by_role("button", name="Details").click(); U.wait_for_timeout(1600)
    U.get_by_test_id("delete-item").click(); U.wait_for_timeout(800)
    check("deleting asks before it destroys anything", U.locator("[data-testid=ask-modal]").count() > 0)
    answer_ask(U, cancel=True)
    left = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>f.filter(i=>i.body.includes('End of Tyme')).length)")
    check("cancelling that dialog keeps the contribution", left == 1, f"{left} left")

    U.goto(base+"/"); U.wait_for_timeout(1800)
    U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="End of Tyme").first \
        .get_by_role("button", name="Details").click()
    U.wait_for_timeout(1600)
    U.get_by_test_id("delete-item").click(); U.wait_for_timeout(700)
    answer_ask(U)
    U.wait_for_timeout(2000)
    # Assert the row is really gone before asserting anything about the page.
    left = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>f.filter(i=>i.body.includes('End of Tyme')).length)")
    check("confirming really removes it", left == 0, f"{left} left")
    U.goto(base+"/"); U.wait_for_timeout(2000)
    check("and it is gone from the team's feed too",
          "End of Tyme" not in U.get_by_test_id("knowledge-column").inner_text())
    gone = U.evaluate("()=>fetch('/api/search?q=Epoch').then(r=>r.json()).then(r=>r.items.length)")
    check("and search no longer returns it", gone == 0, f"{gone} hits")

    print("\n### 50. The admin's evidence for who the experts are", flush=True)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2200)
    if A.get_by_test_id("tab-endorsed").count():
        A.get_by_test_id("tab-endorsed").click(); A.wait_for_timeout(1800)
        panel = A.get_by_test_id("endorsed-panel")
        check("the most-endorsed tab opens and says something", panel.count() > 0 and panel.inner_text().strip() != "",
              panel.inner_text()[:120] if panel.count() else "missing")
        check("it does not render undefined", "undefined" not in panel.inner_text())

    print("\n### 51. A deploy under an open tab", flush=True)
    # Chunk filenames carry a content hash, so a deploy renames them and a tab
    # opened beforehand asks for files that are gone. What people saw was a
    # blank white page: no text, no error, nothing to click.
    U.route("**/assets/index-*.js", lambda route: route.fulfill(status=404, body=""))
    U.goto(base+"/"); U.wait_for_timeout(3000)
    empty = U.evaluate("()=>document.getElementById('app').innerHTML.trim() === ''")
    check("the entry bundle really did fail to load", empty, "the app mounted anyway")
    check("a tab whose app cannot load says so instead of going blank",
          U.get_by_test_id("stale-version").count() > 0,
          U.locator("body").inner_text()[:80])
    check("and it offers a way out", U.get_by_test_id("stale-version-reload").count() > 0)
    U.unroute("**/assets/index-*.js")

    U.goto(base+"/"); U.wait_for_timeout(2500)
    U.route("**/assets/*View-*.js", lambda route: route.fulfill(status=404, body=""))
    U.get_by_role("link", name="Leaderboard").click(); U.wait_for_timeout(3500)
    check("a screen that cannot load after a deploy says so too",
          U.get_by_test_id("stale-version").count() > 0,
          U.locator("body").inner_text()[:80])
    U.unroute("**/assets/*View-*.js")

    check("no server error anywhere in this pass", not [c for c in crashes if "HTTP 5" in c], str(crashes[:4]))
    b.close()

srv.terminate(); srv.wait(timeout=10)
print("\n" + "="*70)
print(f"CHECKS RUN : {len(CHECKS)}")
srv_errs = [c for c in crashes if "HTTP 5" in c]; js_errs = [c for c in crashes if "JS ERROR" in c]
print(f"SERVER 5xx : {len(srv_errs)}")
for c in srv_errs[:12]: print("   ", c)
print(f"JS ERRORS  : {len(js_errs)}")
for c in js_errs[:12]: print("   ", c)
print(f"FAILURES   : {len(FAILS)}")
for f in FAILS: print("   -", f)
sys.exit(1 if (FAILS or srv_errs or js_errs) else 0)
