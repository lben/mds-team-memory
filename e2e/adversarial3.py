"""Third pass: the corners the first two passes never walked into."""
import os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as PWError

ROOT = Path("/Users/bleon/ClaudeCodexWorkspace/WorkKnowledgeBase/mds-team-memory")
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
                       env=env, stdout=open(OUT/"access3.log","w"), stderr=subprocess.STDOUT)
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

with sync_playwright() as pw:
    b = pw.chromium.launch()
    A = b.new_context(viewport={"width":1500,"height":950}).new_page()
    U = b.new_context(viewport={"width":1500,"height":950}).new_page()
    V = b.new_context(viewport={"width":1500,"height":950}).new_page()
    for pg, who in ((A,"admin"),(U,"user"),(V,"other")):
        pg.on("pageerror", lambda e, w=who: crashes.append(f"[{w}] JS ERROR: {str(e)[:160]}"))
        pg.on("response", lambda r, w=who: crashes.append(f"[{w}] HTTP {r.status} {r.url.split(base)[-1]}") if r.status>=500 else None)

    def toast(pg): return pg.locator(".toast").inner_text() if pg.locator(".toast").count() else ""
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

    print("\n### 38. Deleting your own question, for real this time", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(1200)
    U.get_by_test_id("home-input").fill("A question I will regret asking about Lavos?")
    U.get_by_test_id("do-ask").click(); U.wait_for_timeout(2200)
    card = U.locator(".question-card", has_text="regret asking").first
    U.once("dialog", lambda d: d.dismiss())
    card.get_by_test_id("delete-question").click(); U.wait_for_timeout(1400)
    check("cancelling the delete confirmation keeps the question",
          U.locator(".question-card", has_text="regret asking").count() == 1)
    card = U.locator(".question-card", has_text="regret asking").first
    U.once("dialog", lambda d: d.accept())
    card.get_by_test_id("delete-question").click(); U.wait_for_timeout(2000)
    check("confirming really deletes it", U.locator(".question-card", has_text="regret asking").count() == 0)
    left = U.evaluate("()=>fetch('/api/questions').then(r=>r.json()).then(q=>q.filter(x=>x.body.includes('regret asking')).length)")
    check("and it is gone from the server too, not just hidden", left == 0, f"{left} left")
    V.goto(base+"/"); V.wait_for_timeout(1800)
    check("nobody else can still see the deleted question",
          "regret asking" not in V.get_by_test_id("questions-column").inner_text())

    print("\n### 39. Endorsing, correcting and adopting a correction", flush=True)
    login(A)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    for name in ["Chrono Trigger", "Time Travel"]:
        A.get_by_test_id("concept-name").fill(name); A.get_by_test_id("concept-aliases").fill("")
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(900)
    capture(U, "Chrono Trigger has twelve endings, counting the developer room.")
    U.goto(base+"/"); U.wait_for_timeout(1200)
    U.get_by_test_id("home-input").fill("How many endings does Chrono Trigger have?")
    U.get_by_test_id("do-ask").click(); U.wait_for_timeout(2200)
    V.goto(base+"/"); V.wait_for_timeout(2000)
    vcard = V.locator(".question-card", has_text="How many endings").first
    vcard.locator(".q-head").click(); V.wait_for_timeout(1000)
    vcard.get_by_test_id("answer-text").fill("Thirteen, including the developer room ending.")
    vcard.get_by_test_id("post-answer").click(); V.wait_for_timeout(2200)
    U.reload(); U.wait_for_timeout(2200)
    ucard = U.locator(".question-card", has_text="How many endings").first
    ucard.locator(".q-head").click(); U.wait_for_timeout(1200)
    endorse = ucard.get_by_role("button", name="Endorse as expert")
    check("someone else's answer offers an endorse action", endorse.count() > 0, ucard.inner_text()[:120])
    endorse.first.click(); U.wait_for_timeout(2000)
    check("endorsing without being a mapped expert is refused, and says so",
          "SME ENDORSED" not in ucard.inner_text() and "expert" in toast(U).lower(), f"toast={toast(U)!r}")

    # Now make this person a real expert for the topic and try again.
    login(A)
    profiles = A.get_by_test_id("map-profile").locator("option").all_inner_texts()
    check("the answering people appear as mappable profiles", len(profiles) > 2, str(profiles))
    for i in range(1, len(profiles)):
        A.get_by_test_id("map-profile").select_option(index=i)
        A.get_by_test_id("map-concept").select_option(label="Chrono Trigger")
        A.get_by_test_id("add-mapping").click(); A.wait_for_timeout(1000)
    U.reload(); U.wait_for_timeout(2200)
    ucard = U.locator(".question-card", has_text="How many endings").first
    ucard.locator(".q-head").click(); U.wait_for_timeout(1200)
    ucard.get_by_role("button", name="Endorse as expert").first.click(); U.wait_for_timeout(2200)
    check("a mapped expert's endorsement shows on the answer",
          "SME ENDORSED" in ucard.inner_text(), ucard.inner_text()[:180])
    V.reload(); V.wait_for_timeout(2000)
    vcard = V.locator(".question-card", has_text="How many endings").first
    vcard.locator(".q-head").click(); V.wait_for_timeout(1200)
    check("you cannot endorse your own answer",
          vcard.get_by_role("button", name="Endorse as expert").count() == 0)

    V.goto(base+"/"); V.wait_for_timeout(2000)
    target = V.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="twelve endings").first
    target.get_by_role("button", name="Details").click(); V.wait_for_timeout(1800)
    check("the contribution's details open for a reader", V.get_by_test_id("item-detail").count() > 0)
    V.get_by_test_id("item-detail").locator("textarea").fill("It is thirteen endings, not twelve.")
    V.get_by_role("button", name="Propose correction").click(); V.wait_for_timeout(2200)
    check("the correction is recorded", "thirteen endings, not twelve" in V.get_by_test_id("item-detail").inner_text())
    check("a reader cannot adopt their own correction into someone else's entry",
          V.get_by_test_id("item-detail").get_by_role("button", name="Adopt").count() == 0)
    V.keyboard.press("Escape"); V.wait_for_timeout(600)
    U.goto(base+"/"); U.wait_for_timeout(2200)
    own = U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="twelve endings").first
    own.get_by_role("button", name="Details").click(); U.wait_for_timeout(1800)
    adopt = U.get_by_test_id("item-detail").get_by_role("button", name="Adopt")
    check("the author is offered the correction to adopt", adopt.count() > 0,
          U.get_by_test_id("item-detail").inner_text()[:200])
    if adopt.count():
        adopt.first.click(); U.wait_for_timeout(2200)
        body = U.get_by_test_id("item-detail").inner_text()
        check("adopting rewrites the entry to the corrected text", "thirteen endings, not twelve" in body, body[:200])
        check("and the correction cannot be adopted a second time",
              U.get_by_test_id("item-detail").get_by_role("button", name="Adopt").count() == 0)
    U.keyboard.press("Escape"); U.wait_for_timeout(600)

    print("\n### 40. Focusing the map on one concept", flush=True)
    capture(U, "Chrono Trigger built its whole story on Time Travel between eras.")
    U.goto(base+"/"); U.wait_for_timeout(3000)
    pts = U.evaluate("""() => {
        const el = document.querySelector('[data-testid=graph]');
        const cy = el && el._cyreg && el._cyreg.cy; if (!cy) return [];
        const r = el.getBoundingClientRect();
        return cy.nodes('[nodeType = "concept"]').map(n => {
            const p = n.renderedPosition();
            return {x: r.left + p.x, y: r.top + p.y};
        });
    }""")
    check("the map shows concept nodes to click", len(pts) > 0, f"{len(pts)}")
    focused = False
    for p in pts:
        U.mouse.click(p["x"], p["y"]); U.wait_for_timeout(1600)
        if U.get_by_test_id("graph-title").count() and "All concepts" not in U.get_by_test_id("graph-title").inner_text():
            focused = True; break
    check("clicking a concept focuses the map on it", focused,
          U.get_by_test_id("graph-title").inner_text() if U.get_by_test_id("graph-title").count() else "no title")
    check("focusing did not error", not [c for c in crashes if "JS ERROR" in c or "HTTP 5" in c], str(crashes[:2]))

    print("\n### 41. The scratchpad's own search, and a second pad", flush=True)
    U.goto(base+"/scratchpad"); U.wait_for_timeout(1800)
    U.get_by_test_id("scratch-editor").fill(
        "Zeal era notes: the Ocean Palace run.\nGuardia castle timings.\nEnd of Time hub shortcuts.")
    U.wait_for_timeout(2000)
    U.get_by_test_id("scratch-find").fill("Ocean Palace")
    U.get_by_role("button", name="Find").click(); U.wait_for_timeout(1800)
    page = U.locator(".page").inner_text()
    check("the scratchpad find shows the matching line", "Ocean Palace" in page, page[:120])
    U.get_by_test_id("scratch-find").fill("nothing like this exists here")
    U.get_by_role("button", name="Find").click(); U.wait_for_timeout(1600)
    check("a find with no matches says so rather than breaking",
          U.locator(".sidebar").count() > 0 and "undefined" not in U.locator(".page").inner_text())
    U.once("dialog", lambda d: d.dismiss())
    U.get_by_role("button", name="Create another scratchpad").click(); U.wait_for_timeout(1400)
    pads = U.evaluate("()=>fetch('/api/scratchpad').then(r=>r.json()).then(s=>1+s.others.length)")
    check("cancelling the name prompt creates no pad", pads == 1, f"{pads} pads")
    U.once("dialog", lambda d: d.accept("Route notes"))
    U.get_by_role("button", name="Create another scratchpad").click(); U.wait_for_timeout(2200)
    pads = U.evaluate("()=>fetch('/api/scratchpad').then(r=>r.json()).then(s=>1+s.others.length)")
    check("naming it creates a second pad", pads == 2, f"{pads} pads")
    check("the second pad is visible in the interface", "Route notes" in U.locator(".page").inner_text())
    check("the first pad's text did not leak into the new one",
          "Ocean Palace" not in (U.get_by_test_id("scratch-editor").input_value() or ""),
          U.get_by_test_id("scratch-editor").input_value()[:80])

    print("\n### 42. Downloading the original file behind a document", flush=True)
    U.goto(base+"/documents"); U.wait_for_timeout(1400)
    U.locator("input[type=file]").set_input_files({"name":"design.txt","mimeType":"text/plain",
        "buffer":"The Ocean Palace sequence is the point of no return.\n".encode()})
    U.wait_for_timeout(3000)
    with U.expect_download(timeout=15000) as dl:
        U.get_by_role("link", name="Download original").click()
    got = dl.value
    saved = Path(OUT/"downloaded.txt"); got.save_as(str(saved))
    check("the original file downloads with its own name", got.suggested_filename == "design.txt",
          got.suggested_filename)
    check("and its contents are the file that was uploaded",
          "point of no return" in saved.read_text(), saved.read_text()[:80])

    print("\n### 43. Previewing where a question would be routed", flush=True)
    login(A)
    A.get_by_test_id("map-profile").select_option(index=1)
    A.get_by_test_id("map-concept").select_option(label="Time Travel")
    A.get_by_test_id("add-mapping").click(); A.wait_for_timeout(1800)
    box = A.get_by_placeholder("Paste a question to preview its routing")
    box.fill("Who knows about Chrono Trigger time travel?")
    box.press("Enter"); A.wait_for_timeout(2000)
    txt = A.locator(".page").inner_text()
    check("the routing preview names the detected concept", "Chrono Trigger" in txt)
    check("and it names somebody to route to",
          "Browser profile" in txt or "Benito" in txt, txt[txt.find("Chrono Trigger"):][:160])
    box.fill("a question about something nobody has any expertise in at all")
    box.press("Enter"); A.wait_for_timeout(2000)
    check("a question that routes nowhere says so instead of erroring",
          "undefined" not in A.locator(".page").inner_text() and A.locator(".sidebar").count() > 0)

    print("\n### 44. Relationship types: rename, and delete one in use", flush=True)
    A.get_by_test_id("tab-types").click(); A.wait_for_timeout(900)
    A.get_by_test_id("type-name").fill("inspired"); A.get_by_test_id("add-type").click(); A.wait_for_timeout(1200)
    row = A.get_by_test_id("map-admin-panel").locator(".panel-body.types", has_text="inspired").first
    A.once("dialog", lambda d: d.dismiss())
    row.get_by_role("button", name="Rename").click(); A.wait_for_timeout(1200)
    check("cancelling a rename changes nothing",
          "inspired" in A.get_by_test_id("map-admin-panel").inner_text())
    A.once("dialog", lambda d: d.accept("corroborates"))
    A.get_by_test_id("map-admin-panel").locator(".panel-body.types", has_text="inspired").first \
        .get_by_role("button", name="Rename").click()
    A.wait_for_timeout(1600)
    names = A.evaluate("()=>fetch('/api/graph/relationship-types').then(r=>r.json()).then(t=>t.map(x=>x.name))")
    check("renaming a type onto a built-in name is refused", len(names) == len(set(names)), str(names))
    A.once("dialog", lambda d: d.accept("directly inspired"))
    A.get_by_test_id("map-admin-panel").locator(".panel-body.types", has_text="inspired").first \
        .get_by_role("button", name="Rename").click()
    A.wait_for_timeout(1800)
    check("a real rename goes through", "directly inspired" in A.get_by_test_id("map-admin-panel").inner_text(),
          A.get_by_test_id("map-admin-panel").inner_text()[:150])

    A.get_by_test_id("tab-links").click(); A.wait_for_timeout(1000)
    link_rows = A.get_by_test_id("map-admin-panel").locator(".panel-body.links")
    check("there is a link to re-type", link_rows.count() > 0, f"{link_rows.count()}")
    if link_rows.count():
        link_rows.first.locator("select").select_option(label="directly inspired")
        A.wait_for_timeout(1800)
        check("the link now uses the renamed type",
              "directly inspired" in A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text())
    A.get_by_test_id("tab-types").click(); A.wait_for_timeout(1200)
    used = A.get_by_test_id("map-admin-panel").locator(".panel-body.types", has_text="directly inspired").first
    check("a type in use reports its usage", "1" in used.inner_text(), used.inner_text()[:80])
    del_btn = used.get_by_role("button", name="Delete")
    if del_btn.count() and not del_btn.first.is_disabled():
        del_btn.first.click(); A.wait_for_timeout(1800)
    still = A.evaluate("()=>fetch('/api/graph/relationship-types').then(r=>r.json()).then(t=>t.filter(x=>x.name==='directly inspired').length)")
    check("a relationship type still in use cannot be silently deleted out from under its links",
          still == 1 and toast(A) != "", f"remaining={still}, toast={toast(A)!r}")

    print("\n### 45. Deleting a link permanently, from the table", flush=True)
    A.get_by_test_id("tab-links").click(); A.wait_for_timeout(1200)
    before = A.get_by_test_id("map-admin-panel").locator(".panel-body.links").count()
    A.once("dialog", lambda d: d.dismiss())
    A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.get_by_role("button", name="Delete").click()
    A.wait_for_timeout(1400)
    check("cancelling the delete confirmation keeps the link",
          A.get_by_test_id("map-admin-panel").locator(".panel-body.links").count() == before)
    A.once("dialog", lambda d: d.accept())
    A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.get_by_role("button", name="Delete").click()
    A.wait_for_timeout(2000)
    check("confirming deletes it", A.get_by_test_id("map-admin-panel").locator(".panel-body.links").count() == before-1,
          f"{A.get_by_test_id('map-admin-panel').locator('.panel-body.links').count()} left")
    U.goto(base+"/"); U.wait_for_timeout(3000)
    check("the map redraws without the deleted link and without errors",
          U.locator(".graph-box").count() > 0 and not [c for c in crashes if "JS ERROR" in c],
          str([c for c in crashes if "JS ERROR" in c][:2]))
    A.get_by_test_id("tab-types").click(); A.wait_for_timeout(1400)
    unused = A.get_by_test_id("map-admin-panel").locator(".panel-body.types", has_text="directly inspired").first
    if unused.count() and unused.get_by_role("button", name="Delete").count():
        unused.get_by_role("button", name="Delete").first.click(); A.wait_for_timeout(1800)
    gone = A.evaluate("()=>fetch('/api/graph/relationship-types').then(r=>r.json()).then(t=>t.filter(x=>x.name==='directly inspired').length)")
    check("once nothing uses it, the type can be deleted", gone == 0, f"{gone} remaining")

    print("\n### 46. Adding a second admin, and the weak passwords it must refuse", flush=True)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1800)
    user_box = A.get_by_placeholder("Username")
    pass_box = A.get_by_placeholder("Password (8+ chars)")
    for u, p, why in [("", "", "an empty form"),
                      ("shortpass", "abc", "a three-character password"),
                      ("benito", "another-password", "a username that already exists")]:
        user_box.fill(u); pass_box.fill(p)
        A.get_by_role("button", name="Add admin").click(); A.wait_for_timeout(1600)
        n = A.evaluate("()=>fetch('/api/admin/admins').then(r=>r.json()).then(a=>a.length)")
        check(f"{why} does not create an admin", n == 1, f"{n} admins, toast={toast(A)!r}")
    user_box.fill("marta"); pass_box.fill("a-proper-password")
    A.get_by_role("button", name="Add admin").click(); A.wait_for_timeout(2000)
    n = A.evaluate("()=>fetch('/api/admin/admins').then(r=>r.json()).then(a=>a.length)")
    check("a valid second admin is created", n == 2, f"{n} admins")
    check("and is listed on screen", "marta" in A.locator(".page").inner_text())
    A.get_by_test_id("sign-out-admin").click(); A.wait_for_timeout(1600)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1600)
    A.get_by_test_id("admin-username").fill("marta"); A.get_by_test_id("admin-password").fill("a-proper-password")
    A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(2200)
    check("the second admin can sign in and curate", A.get_by_test_id("map-admin-panel").count() > 0)
    A.get_by_test_id("admin-username") if A.get_by_test_id("admin-auth").count() else None

    A.screenshot(path=str(OUT/"round3_admin.png"), full_page=True)
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
