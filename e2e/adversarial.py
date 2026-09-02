"""Adversarial pass: reach everything the way a human does, and try to break it."""
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
                       env=env, stdout=open(OUT/"access1.log","w"), stderr=subprocess.STDOUT)
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


def sign_out(pg):
    """Signing out lives with the account in the sidebar now, not in the admin
    nav: admin rights and identity are one thing."""
    pg.get_by_test_id("profile-button").click()
    pg.wait_for_timeout(500)
    pg.get_by_test_id("sign-out").click()
    pg.wait_for_timeout(1800)


def sign_up(pg, username, password="a-good-password"):
    """Create an account the way a person does: the profile button in the
    sidebar. Expertise can only be routed to someone who has one."""
    pg.get_by_test_id("profile-button").click()
    pg.wait_for_timeout(600)
    pg.get_by_test_id("auth-username").fill(username)
    pg.get_by_test_id("auth-password").fill(password)
    pg.get_by_test_id("do-sign-up").click()
    pg.wait_for_timeout(3000)  # signing in reloads, so every screen agrees on who you are

with sync_playwright() as pw:
    b = pw.chromium.launch()
    admin_ctx = b.new_context(viewport={"width":1500,"height":950})
    user_ctx  = b.new_context(viewport={"width":1500,"height":950})
    anon_ctx  = b.new_context(viewport={"width":1366,"height":768})
    A, U, N = admin_ctx.new_page(), user_ctx.new_page(), anon_ctx.new_page()
    for pg, who in ((A,"admin"),(U,"user"),(N,"anon")):
        pg.on("pageerror", lambda e, w=who: crashes.append(f"[{w}] JS ERROR: {str(e)[:160]}"))
        pg.on("response", lambda r, w=who: crashes.append(f"[{w}] HTTP {r.status} {r.url.split(base)[-1]}") if r.status>=500 else None)
    for pg in (A,U,N): pg.goto(base + "/"); pg.wait_for_timeout(500)

    def toast(pg):
        return pg.locator(".toast").inner_text() if pg.locator(".toast").count() else ""
    def sane(pg, where):
        t = pg.locator(".page").inner_text() if pg.locator(".page").count() else ""
        for junk in ("undefined","NaN","[object Object]","Invalid Date"):
            if junk in t:
                check(f"no {junk!r} rendered on {where}", False, t[:150]); return
        check(f"{where} renders no placeholder junk", True)
    def capture(pg, text):
        pg.goto(base+"/"); pg.wait_for_timeout(900)
        pg.get_by_test_id("home-input").fill(text)
        pg.get_by_test_id("do-capture").click()
        try:
            pg.get_by_test_id("success-modal").wait_for(timeout=8000)
            pg.get_by_role("button", name="Add another").click(); pg.wait_for_timeout(400)
            return True
        except PWError:
            return False
    def edge_points(pg):
        """Where a human sees the links on the map, in page coordinates."""
        return pg.evaluate("""() => {
            const el = document.querySelector('[data-testid=graph]');
            const cy = el && el._cyreg && el._cyreg.cy;
            if (!cy) return [];
            const r = el.getBoundingClientRect();
            return cy.edges().map(e => {
                const m = e.midpoint(); const z = cy.zoom(); const p = cy.pan();
                return {x: r.left + m.x*z + p.x, y: r.top + m.y*z + p.y};
            });
        }""")

    print("\n### 1. A stranger pokes every URL they could ever land on", flush=True)
    for path in ["/", "/leaderboard", "/documents", "/scratchpad", "/admin/expertise",
                 "/capture", "/search?q=test", "/questions", "/impact", "/nonsense", "/admin",
                 "/documents/does-not-exist", "/questions/does-not-exist",
                 "/documents/deadbeefdeadbeefdeadbeefdeadbeef", "/%20"]:
        try:
            N.goto(base + path, wait_until="domcontentloaded"); N.wait_for_timeout(800)
            check(f"{path} still shows a usable app", N.locator(".sidebar").count() > 0,
                  N.locator("body").inner_text()[:90])
        except PWError as e:
            check(f"{path} still shows a usable app", False, str(e)[:90])

    print("\n### 2. Hostile text in the fields a user types into", flush=True)
    NASTY = ["<script>window.__pwned=1</script>",
             "<img src=x onerror='window.__pwned=1'>",
             "'; DROP TABLE knowledge_items; --",
             "a" * 6000,
             "🎮🕹️ Ünïcødé ‏מבחן עברית‏ ㊗️ ​zero​width",
             'quote " apostrophe \' AND OR NOT (paren) *star* "phrase" ^caret',
             "line1\nline2\ttabbed\r\ncrlf ending"]
    saved = 0
    for i, text in enumerate(NASTY):
        if capture(U, f"nasty{i} {text}"): saved += 1
    check("every hostile capture was either saved or refused cleanly, none hung", saved >= 5, f"{saved}/7 saved")
    check("no injected script ran in the author's own browser", not U.evaluate("()=>!!window.__pwned"))
    U.goto(base+"/"); U.wait_for_timeout(1400)
    live = U.evaluate("""() => {
        const c = document.querySelector('[data-testid=knowledge-column]');
        return {tags: c.querySelectorAll('script,img,iframe,svg').length,
                handlers: c.querySelectorAll('[onerror],[onload],[onclick]').length,
                text: c.innerText.includes('<script>window.__pwned=1</script>')};
    }""")
    check("injected markup became inert text, no elements were created",
          live["tags"] == 0 and live["handlers"] == 0 and live["text"], str(live))
    check("a 6000-character entry does not push the page sideways",
          not U.evaluate("()=>document.documentElement.scrollWidth>document.documentElement.clientWidth+2"))
    N.goto(base+"/"); N.wait_for_timeout(1500)
    check("no injected script ran in another person's browser either", not N.evaluate("()=>!!window.__pwned"))
    sane(U, "the feed full of hostile content")

    print("\n### 3. Searching for the things that usually break search engines", flush=True)
    for q in ['"unterminated', "AND OR NOT", "*", "((()))", "NEAR/", "a"*300,
              "🎮", "'; DROP TABLE x; --", "-", "^", "nasty4"]:
        U.goto(base+"/"); U.wait_for_timeout(700)
        U.get_by_test_id("home-input").fill(q)
        U.get_by_test_id("do-search").click(); U.wait_for_timeout(1200)
        ok = U.get_by_test_id("search-banner").count() > 0 or U.get_by_test_id("nothing-found").count() > 0
        check(f"searching {q[:22]!r} returns an answer instead of an error", ok, toast(U))
    check("no server error from any hostile search", not [c for c in crashes if "HTTP 5" in c], str(crashes[:3]))

    print("\n### 4. Empty and whitespace submissions on every button", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(900)
    for label, tid in [("Search","do-search"), ("Ask","do-ask"), ("Capture","do-capture")]:
        for value, kind in [("", "empty"), ("     \n\t  ", "whitespace-only")]:
            U.get_by_test_id("home-input").fill(value)
            U.get_by_test_id(tid).click(); U.wait_for_timeout(700)
            check(f"{kind} {label} creates nothing and shows no modal",
                  U.locator(".modal-backdrop").count() == 0, toast(U))
            if U.locator(".modal-backdrop").count(): U.keyboard.press("Escape")
    U.get_by_test_id("home-input").fill("")

    print("\n### 5. Impatient double-clicks on the creating controls", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(900)
    U.get_by_test_id("home-input").fill("double click capture test alpha")
    U.get_by_test_id("do-capture").dblclick(); U.wait_for_timeout(3000)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(700)
    # Count stored items, not feed rows: identical entries are folded into one
    # corroboration row, so a row count cannot see a duplicate at all.
    g = U.evaluate("""()=>fetch('/api/feed').then(r=>r.json()).then(f=>{
        const m=f.filter(i=>i.body.includes('double click capture test alpha'));
        return {rows:m.length, stored:m.reduce((s,i)=>s+(i.group_size||1),0)}})""")
    check("double-clicking Capture stores one entry, not two", g["stored"] == 1, str(g))
    U.goto(base+"/"); U.wait_for_timeout(900)
    U.get_by_test_id("home-input").fill("double click ask test beta")
    U.get_by_test_id("do-ask").dblclick(); U.wait_for_timeout(3000)
    q = U.evaluate("()=>fetch('/api/questions').then(r=>r.json()).then(f=>f.filter(i=>i.body.includes('double click ask test beta')).length)")
    check("double-clicking Ask posts one question, not two", q == 1, f"{q} questions")

    print("\n### 6. Back, forward and refresh in the middle of things", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(900)
    U.get_by_test_id("home-input").fill("roguelike"); U.get_by_test_id("do-search").click(); U.wait_for_timeout(1200)
    U.goto(base+"/leaderboard"); U.wait_for_timeout(1000)
    U.go_back(); U.wait_for_timeout(1400)
    check("back from Leaderboard lands on a working Home", U.get_by_test_id("home-input").count() > 0)
    U.go_forward(); U.wait_for_timeout(1200)
    check("forward returns to Leaderboard", U.get_by_test_id("leaderboard").count() > 0)
    U.go_back(); U.wait_for_timeout(900); U.reload(); U.wait_for_timeout(1600)
    check("refreshing Home after all that is clean", U.get_by_test_id("knowledge-column").count() > 0)
    sane(U, "Home after back/forward/refresh")

    print("\n### 7. Admin signs in and builds the vocabulary", flush=True)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1200)
    check("the admin page asks for credentials first", A.get_by_test_id("admin-auth").count() > 0)
    A.get_by_test_id("admin-username").fill("benito"); A.get_by_test_id("admin-password").fill("wrong-password")
    A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(1200)
    check("a wrong password is refused", A.get_by_test_id("map-admin-panel").count() == 0, toast(A))
    A.get_by_test_id("admin-username").fill("benito"); A.get_by_test_id("admin-password").fill("arcade-1978")
    A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(1600)
    check("the right password gets in", A.get_by_test_id("map-admin-panel").count() > 0)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(500)
    for name in ["Roguelike", "Metroidvania", "Speedrun"]:
        A.get_by_test_id("concept-name").fill(name); A.get_by_test_id("concept-aliases").fill("")
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(800)
    made = A.get_by_test_id("map-admin-panel").locator(".panel-body.concepts").count()
    check("three concepts exist", made == 3, f"{made}")
    A.get_by_test_id("concept-name").fill("Roguelike"); A.get_by_test_id("add-concept").click(); A.wait_for_timeout(900)
    dup = A.get_by_test_id("map-admin-panel").locator(".panel-body.concepts").count()
    check("a duplicate concept name is refused with a message", dup == 3 and toast(A) != "", f"{dup} rows, toast={toast(A)!r}")
    A.get_by_test_id("concept-name").fill("   "); A.get_by_test_id("add-concept").click(); A.wait_for_timeout(900)
    check("a blank concept name creates nothing",
          A.get_by_test_id("map-admin-panel").locator(".panel-body.concepts").count() == 3)
    check("and the button explains itself instead of doing nothing silently", toast(A) != "", "silent")

    print("\n### 8. Hostile input in the admin's own fields", flush=True)
    A.get_by_test_id("concept-name").fill("<script>window.__adminpwned=1</script>")
    A.get_by_test_id("add-concept").click(); A.wait_for_timeout(1000)
    check("a script tag as a concept name does not execute", not A.evaluate("()=>!!window.__adminpwned"))
    check("that concept name is escaped in the table", "<script>" not in A.get_by_test_id("map-admin-panel").inner_html())
    injected = A.get_by_test_id("map-admin-panel").locator(".panel-body.concepts").filter(has_text="__adminpwned")
    check("the injected concept is listed as one ordinary row", injected.count() == 1, f"{injected.count()}")
    if injected.count():
        injected.first.get_by_role("button", name="Delete").click(); A.wait_for_timeout(1200)
        answer_ask(A)
    remaining = A.get_by_test_id("map-admin-panel").locator(".panel-body.concepts")
    check("deleting it leaves exactly the three real concepts",
          remaining.count() == 3 and "__adminpwned" not in remaining.all_inner_texts().__str__(),
          f"{remaining.count()} rows")

    print("\n### 9. Real content, then the links a human curates", flush=True)
    capture(U, "Dead Cells mixes Roguelike runs with Metroidvania level design.")
    capture(U, "Speedrun routing in a Roguelike depends on the seed.")
    A.reload(); A.wait_for_timeout(2000)
    A.get_by_test_id("tab-links").click(); A.wait_for_timeout(800)
    links_n = A.get_by_test_id("map-admin-panel").locator(".panel-body.links").count()
    check("links were detected from what people wrote", links_n > 0, f"{links_n} links")
    if links_n:
        row = A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first
        row.get_by_role("button", name="Approve").click(); A.wait_for_timeout(1400)
        answer_ask(A, "these really do go together")
        check("approving marks it confirmed",
              "CONFIRMED" in A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text(),
              A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text()[:90])
        A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.get_by_role("button", name="Reject").click()
        answer_ask(A, cancel=True)
        A.wait_for_timeout(1200)
        check("cancelling the reason prompt cancels the whole decision",
              "CONFIRMED" in A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text(),
              A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text()[:90])
        A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.get_by_role("button", name="Reject").click()
        answer_ask(A, "")
        A.wait_for_timeout(1200)
        check("but confirming with no reason typed still rejects it",
              "REJECTED" in A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text(),
              A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.inner_text()[:90])
        A.get_by_test_id("map-admin-panel").locator(".panel-body.links").first.get_by_role("button", name="Approve").click()
        answer_ask(A, "restoring for the rest of the run")
        A.wait_for_timeout(1200)

    print("\n### 10. Relationship types, including the built-in ones", flush=True)
    A.get_by_test_id("tab-types").click(); A.wait_for_timeout(700)
    A.get_by_test_id("type-name").fill("shares design ideas with"); A.get_by_test_id("add-type").click(); A.wait_for_timeout(900)
    check("a custom relationship type appears", "shares design ideas with" in A.get_by_test_id("map-admin-panel").inner_text())
    A.get_by_test_id("type-name").fill("shares design ideas with"); A.get_by_test_id("add-type").click(); A.wait_for_timeout(900)
    check("a duplicate relationship type is refused", toast(A) != "", toast(A))
    A.get_by_test_id("type-name").fill("   "); A.get_by_test_id("add-type").click(); A.wait_for_timeout(900)
    check("a blank relationship type says why nothing happened", toast(A) != "", "silent")
    builtin = A.get_by_test_id("map-admin-panel").locator(".panel-body.types", has_text="corroborates").first
    check("built-in types cannot be deleted", builtin.get_by_role("button", name="Delete").count() == 0
          or builtin.get_by_role("button", name="Delete").first.is_disabled())

    print("\n### 11. Two admin tabs disagreeing about reality", flush=True)
    A2 = admin_ctx.new_page(); A2.goto(base+"/admin/expertise"); A2.wait_for_timeout(2000)
    A2.get_by_test_id("tab-concepts").click(); A2.wait_for_timeout(600)
    cid = A.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.json()).then(c=>{const s=c.find(x=>x.name==='Speedrun');return s?s.id:(c[0]?c[0].id:'')})")
    check("the concept we are about to attack is really there", bool(cid))
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(600)
    A.locator(f"#row-{cid}").get_by_role("button", name="Delete").click(); A.wait_for_timeout(1400)
    answer_ask(A)
    check("the concept is gone from the acting tab", A.locator(f"#row-{cid}").count() == 0)
    ghost = A2.locator(f"#row-{cid}")
    check("the stale tab still shows the ghost row (so we can attack it)", ghost.count() > 0)
    if ghost.count():
        ghost.get_by_role("button", name="Delete").click(); A2.wait_for_timeout(1600)
        answer_ask(A2)
        check("deleting an already-deleted concept says so instead of breaking", toast(A2) != "", "silent")
        check("the stale tab recovers a correct list", A2.locator(f"#row-{cid}").count() == 0)
    A2.close()
    check("no JavaScript error from any of the stale-state attacks",
          not [c for c in crashes if "JS ERROR" in c], str([c for c in crashes if "JS ERROR" in c][:2]))

    print("\n### 12. The route from the map to the evidence to the admin row", flush=True)
    A.goto(base+"/"); A.wait_for_timeout(2600)
    pts = edge_points(A)
    check("the map draws links a person can aim at", len(pts) > 0, f"{len(pts)} edges")
    opened = False
    for p in pts:
        A.mouse.click(p["x"], p["y"]); A.wait_for_timeout(500)
        if A.get_by_test_id("evidence-modal").count(): opened = True; break
    check("clicking a link on the map opens its evidence", opened)
    if opened:
        txt = A.get_by_test_id("evidence-modal").inner_text()
        check("the evidence names both concepts and its source", "mentioned together" in txt, txt[:90])
        check("an admin sees the shortcut into curation", A.get_by_test_id("manage-link").count() > 0)
        A.get_by_test_id("manage-link").click(); A.wait_for_timeout(2600)
        check("it lands on the admin table with that row highlighted",
              A.locator(".panel-body.links.highlight").count() > 0, A.url.split(base)[-1])
    U.goto(base+"/"); U.wait_for_timeout(2600)
    uopened = False
    for p in edge_points(U):
        U.mouse.click(p["x"], p["y"]); U.wait_for_timeout(500)
        if U.get_by_test_id("evidence-modal").count(): uopened = True; break
    if uopened:
        check("a non-admin sees the evidence but no curation shortcut", U.get_by_test_id("manage-link").count() == 0)
        U.get_by_role("button", name="Close").click(); U.wait_for_timeout(400)

    print("\n### 13. Signing out while another tab is still open", flush=True)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1600)
    A3 = admin_ctx.new_page(); A3.goto(base+"/admin/expertise"); A3.wait_for_timeout(2000)
    sign_out(A)
    check("signing out leaves the curation panel behind", A.get_by_test_id("map-admin-panel").count() == 0, A.url.split(base)[-1])
    A.get_by_test_id("profile-button").click(); A.wait_for_timeout(500)
    check("the Sign out button is gone once signed out", A.get_by_test_id("sign-out").count() == 0)
    A.keyboard.press("Escape"); A.get_by_test_id("profile-button").click(); A.wait_for_timeout(300)
    check("the Expertise Routing link is still there for everyone", A.get_by_test_id("admin-nav").count() > 0)
    A.go_back(); A.wait_for_timeout(1800)
    check("pressing Back into the admin page after signing out shows the login, not a cached panel",
          A.get_by_test_id("admin-auth").count() > 0 and A.get_by_test_id("map-admin-panel").count() == 0,
          A.url.split(base)[-1])
    A3.get_by_test_id("tab-concepts").click(); A3.wait_for_timeout(500)
    A3.get_by_test_id("concept-name").fill("AfterSignOut"); A3.get_by_test_id("add-concept").click(); A3.wait_for_timeout(1600)
    status = A3.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.status)")
    check("the signed-out stale tab cannot create anything", status == 401, f"status {status}")
    check("and it says so rather than pretending it worked",
          toast(A3) != "" or A3.get_by_test_id("panel-error").count() > 0, "silent")
    A3.reload(); A3.wait_for_timeout(1600)
    check("reloading the stale tab shows the login form", A3.get_by_test_id("admin-auth").count() > 0)
    A3.close()

    print("\n### 14. Documents: files that should not be accepted", flush=True)
    U.goto(base+"/documents"); U.wait_for_timeout(1200)
    for fname, mime, buf, why in [("evil.exe","application/x-msdownload",b"MZ\x90\x00"*20,"an executable"),
                                  ("fake.pdf","application/pdf",b"this is not a pdf at all","a mislabelled PDF"),
                                  ("empty.txt","text/plain",b"","an empty file"),
                                  ("archive.zip","application/zip",b"PK\x03\x04rubbish","an archive"),
                                  ("huge.txt","text/plain",b"x"*(30*1024*1024),"a 30 MB file")]:
        U.locator("input[type=file]").set_input_files({"name":fname,"mimeType":mime,"buffer":buf})
        U.wait_for_timeout(2500)
        check(f"{why} is refused with an explanation", toast(U) != "" and U.get_by_test_id("doc-viewer").count() == 0,
              f"toast={toast(U)!r}")
        U.goto(base+"/documents"); U.wait_for_timeout(900)
    U.locator("input[type=file]").set_input_files({"name":"good.txt","mimeType":"text/plain",
        "buffer":"Speedrun routing notes.\n\nRoguelike seeds are fixed for a whole run.\n".encode()})
    U.wait_for_timeout(2500)
    check("a genuine document uploads and opens", U.get_by_test_id("doc-viewer").count() > 0, toast(U))
    sane(U, "the documents page")

    print("\n### 15. Private notes stay private under pressure", flush=True)
    U.goto(base+"/scratchpad"); U.wait_for_timeout(1200)
    U.get_by_test_id("scratch-editor").fill("SECRETTOKEN-zx99 must never leave my scratchpad\nRoguelike seeds are fixed.")
    U.wait_for_timeout(2000)
    for pg, who in ((N, "a teammate"), (A, "the admin")):
        pg.goto(base+"/"); pg.wait_for_timeout(1200)
        pg.get_by_test_id("home-input").fill("SECRETTOKEN-zx99")
        pg.get_by_test_id("do-search").click(); pg.wait_for_timeout(1500)
        results = pg.get_by_test_id("knowledge-column").inner_text() + pg.get_by_test_id("questions-column").inner_text()
        raw = pg.evaluate("""()=>fetch('/api/search?q=SECRETTOKEN-zx99').then(r=>r.json())
                              .then(d=>d.items.length+d.documents.length+d.scratchpad.length)""")
        check(f"{who} searching for it gets no results", "SECRETTOKEN" not in results, results[:120])
        check(f"and the server hands {who} zero rows for it", raw == 0, f"{raw} rows")
    pad = N.evaluate("()=>fetch('/api/scratchpad').then(r=>r.json()).then(s=>s.default.content||'')")
    check("a teammate's scratchpad is their own empty one", "SECRETTOKEN" not in pad)
    U.goto(base+"/"); U.wait_for_timeout(1000)
    U.get_by_test_id("home-input").fill("SECRETTOKEN-zx99"); U.get_by_test_id("do-search").click(); U.wait_for_timeout(1500)
    check("but the owner does find their own note",
          "SECRETTOKEN" in U.get_by_test_id("knowledge-column").inner_text())

    print("\n### 16. One question, its whole life, through the interface", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(1000)
    U.get_by_test_id("home-input").fill("Which Roguelike has the fairest seed system?")
    U.get_by_test_id("do-ask").click(); U.wait_for_timeout(2000)
    card = U.locator(".question-card", has_text="fairest seed system").first
    check("the new question appears, opened", card.count() > 0 and card.get_by_test_id("answer-text").count() > 0)
    check("its author can delete it while unanswered", card.get_by_test_id("delete-question").count() > 0)
    N.goto(base+"/"); N.wait_for_timeout(1800)
    ncard = N.locator(".question-card", has_text="fairest seed system").first
    ncard.locator(".q-head").click(); N.wait_for_timeout(900)
    check("a different person cannot delete it", ncard.get_by_test_id("delete-question").count() == 0)
    check("nor accept an answer to it", ncard.get_by_test_id("accept-answer").count() == 0)
    ncard.get_by_test_id("answer-text").fill("   ")
    check("an empty answer cannot be posted", ncard.get_by_test_id("post-answer").is_disabled())
    ncard.get_by_test_id("answer-text").fill("Hades: the seed is fixed for the whole run.")
    ncard.get_by_test_id("post-answer").dblclick(); N.wait_for_timeout(2500)
    ans = N.evaluate("()=>fetch('/api/questions').then(r=>r.json()).then(q=>{const f=q.find(x=>x.body.includes('fairest seed'));return f?f.answer_count:-1})")
    check("double-clicking Post answer posts one answer", ans == 1, f"{ans} answers")
    U.reload(); U.wait_for_timeout(2000)
    card = U.locator(".question-card", has_text="fairest seed system").first
    card.locator(".q-head").click(); U.wait_for_timeout(1000)
    check("once answered, the asker can no longer delete it", card.get_by_test_id("delete-question").count() == 0)
    check("the asker can accept the answer", card.get_by_test_id("accept-answer").count() > 0)
    check("exactly one answer is offered for acceptance", card.get_by_test_id("accept-answer").count() == 1,
          f"{card.get_by_test_id('accept-answer').count()} accept buttons")
    card.get_by_test_id("accept-answer").first.click(); U.wait_for_timeout(1600)
    card = U.locator(".question-card", has_text="fairest seed system").first
    check("accepting resolves the question", "RESOLVED" in card.inner_text(), card.inner_text()[:80])
    card.locator(".q-head").click(); U.wait_for_timeout(900)
    check("there is no second accept button afterwards", card.get_by_test_id("accept-answer").count() == 0)

    print("\n### 17. Credit can only be given once, however hard you click", flush=True)
    N.goto(base+"/"); N.wait_for_timeout(1800)
    res = N.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="Dead Cells").first
    btn = res.get_by_role("button", name="Helped me")
    if btn.count():
        for _ in range(6):
            try: btn.click(timeout=1200)
            except PWError: pass
            N.wait_for_timeout(120)
        N.wait_for_timeout(1200)
        marks = N.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>{const x=f.find(i=>i.body.includes('Dead Cells'));return x?x.helped:-1})")
        check("six rapid clicks count as one helpful mark", marks == 1, f"helped={marks}")
    mine = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>{const x=f.find(i=>i.body.includes('Dead Cells'));return x?x.is_mine:null})")
    U.goto(base+"/"); U.wait_for_timeout(1600)
    own = U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="Dead Cells").first
    check("you cannot mark your own contribution helpful",
          mine is True and own.get_by_role("button", name="Helped me").is_disabled())

    print("\n### 18. Deep links to records that do not exist, or are not yours", flush=True)
    N.goto(base+"/admin/expertise?link=deadbeefdeadbeefdeadbeefdeadbeef"); N.wait_for_timeout(1400)
    check("a stranger deep-linking into curation sees only the login",
          N.get_by_test_id("admin-auth").count() > 0 and N.get_by_test_id("map-admin-panel").count() == 0)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1000)
    A.get_by_test_id("admin-username").fill("benito"); A.get_by_test_id("admin-password").fill("arcade-1978")
    A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(1800)
    A.goto(base+"/admin/expertise?link=deadbeefdeadbeefdeadbeefdeadbeef"); A.wait_for_timeout(2000)
    check("an admin opening a dead record id still gets the working panel",
          A.get_by_test_id("map-admin-panel").count() > 0)
    sane(A, "curation opened on a dead record id")
    for p in ["/documents/deadbeefdeadbeefdeadbeefdeadbeef", "/questions/deadbeefdeadbeefdeadbeefdeadbeef"]:
        U.goto(base+p); U.wait_for_timeout(1400)
        check(f"{p} leaves the app usable", U.locator(".sidebar").count() > 0)

    print("\n### 19. Refresh every screen, signed in and signed out", flush=True)
    for pg, who in ((A, "signed-in admin"), (N, "signed-out visitor")):
        for path, what in [("/","Home"), ("/leaderboard","Leaderboard"), ("/documents","Documents"),
                           ("/scratchpad","Scratchpad"), ("/admin/expertise","Expertise routing")]:
            pg.goto(base+path); pg.wait_for_timeout(1300); pg.reload(); pg.wait_for_timeout(1600)
            check(f"{what} survives a refresh as a {who}", pg.locator(".sidebar").count() > 0)
            sane(pg, f"{what} as {who}")

    print("\n### 20. Live notifications survive the connection dropping", flush=True)
    U.goto(base+"/"); U.wait_for_timeout(2000)
    before = U.get_by_test_id("bell").inner_text()
    N.goto(base+"/"); N.wait_for_timeout(1600)
    ncard = N.locator(".question-card", has_text="fairest seed system").first
    if ncard.count():
        ncard.locator(".q-head").click(); N.wait_for_timeout(800)
        ncard.get_by_test_id("answer-text").fill("Slay the Spire also fixes the seed for the run.")
        ncard.get_by_test_id("post-answer").click(); N.wait_for_timeout(2500)
    U.wait_for_timeout(2500)
    check("the asker is notified without refreshing", U.get_by_test_id("bell").inner_text() != before,
          f"{before!r} -> {U.get_by_test_id('bell').inner_text()!r}")
    U.context.set_offline(True); U.wait_for_timeout(2000); U.context.set_offline(False)
    U.wait_for_timeout(7000)
    check("the app is still alive after the connection dropped and came back",
          U.get_by_test_id("home-input").count() > 0 and not [c for c in crashes if "JS ERROR" in c],
          str([c for c in crashes if "JS ERROR" in c][:2]))

    print("\n### 21. Leaderboard and the profile you can rename", flush=True)
    U.goto(base+"/leaderboard"); U.wait_for_timeout(1600)
    check("the leaderboard lists real contributors", len(U.get_by_test_id("leaderboard").inner_text().strip()) > 0)
    sane(U, "Leaderboard")
    U.goto(base+"/"); U.wait_for_timeout(1400)
    U.get_by_test_id("profile-button").click(); U.wait_for_timeout(800)
    check("the profile panel opens", U.locator(".profile-pop").count() > 0)
    U.get_by_test_id("display-name").fill("   ")
    U.get_by_role("button", name="Save name").click(); U.wait_for_timeout(1000)
    check("a whitespace-only display name is refused, and says so",
          U.locator(".profile-pop").count() > 0 and toast(U) != "", f"toast={toast(U)!r}")
    U.get_by_test_id("display-name").fill("Benito")
    U.get_by_role("button", name="Save name").click(); U.wait_for_timeout(1500)
    check("the new name shows in the sidebar", "Benito" in U.get_by_test_id("profile-button").inner_text(),
          U.get_by_test_id("profile-button").inner_text()[:60])
    check("the avatar shows this person's own initial, not someone else's",
          U.locator(".avatar").first.inner_text().strip().upper().startswith("B"),
          U.locator(".avatar").first.inner_text())
    U.reload(); U.wait_for_timeout(1800)
    check("the renamed author is credited on their own contributions",
          "Benito" in U.get_by_test_id("knowledge-column").inner_text(),
          U.get_by_test_id("knowledge-column").inner_text()[:120])

    print("\n### 22. The notification a person actually clicks", flush=True)
    U.get_by_test_id("bell").click(); U.wait_for_timeout(1200)
    check("the notification list opens", U.locator(".notif-pop").count() > 0)
    notifs = U.locator(".notif-pop .notif:not(.muted)")
    check("there is something in it after being answered", notifs.count() > 0, f"{notifs.count()}")
    if notifs.count():
        notifs.first.click(); U.wait_for_timeout(2000)
        check("clicking a notification takes you somewhere real",
              U.locator(".sidebar").count() > 0 and U.locator(".page").count() > 0, U.url.split(base)[-1])
        U.get_by_test_id("bell").click(); U.wait_for_timeout(900)
        U.get_by_role("button", name="Mark all read").click(); U.wait_for_timeout(1200)
        check("marking all read clears the badge", U.locator(".bell .badge").count() == 0,
              U.get_by_test_id("bell").inner_text())

    print("\n### 23. Sharing a private note without leaking the rest", flush=True)
    U.goto(base+"/scratchpad"); U.wait_for_timeout(1500)
    check("nothing can be shared until something is selected", U.get_by_test_id("share-selection").is_disabled())
    U.evaluate("""() => {
        const ta = document.querySelector('[data-testid=scratch-editor]');
        const line = ta.value.split('\\n')[1];
        const start = ta.value.indexOf(line);
        ta.focus(); ta.setSelectionRange(start, start + line.length);
        ta.dispatchEvent(new Event('select', {bubbles: true}));
        ta.dispatchEvent(new Event('mouseup', {bubbles: true}));
        ta.dispatchEvent(new Event('keyup', {bubbles: true}));
    }""")
    U.wait_for_timeout(800)
    if not U.get_by_test_id("share-selection").is_disabled():
        U.get_by_test_id("share-selection").click(); U.wait_for_timeout(1200)
        answer_ask(U)   # it shows what is about to be published first
        U.wait_for_timeout(2000)
        if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
        U.wait_for_timeout(800)
        shared = N.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>JSON.stringify(f.map(i=>i.body)))")
        check("the selected line reached the team", "seeds are fixed" in shared, shared[:150])
        check("the secret line on the other row did not go with it", "SECRETTOKEN" not in shared, shared[:150])

    print("\n### 24. Sharing an exact passage from a document", flush=True)
    U.goto(base+"/documents"); U.wait_for_timeout(1600)
    U.locator(".doc-row").first.click(); U.wait_for_timeout(1600)
    check("the document opens with its passages", U.locator(".passage").count() > 0)
    U.locator(".share-passage").first.click(); U.wait_for_timeout(2200)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(800)
    N.goto(base+"/"); N.wait_for_timeout(1800)
    check("the passage is now team knowledge others can see",
          "Speedrun routing notes" in N.get_by_test_id("knowledge-column").inner_text(),
          N.get_by_test_id("knowledge-column").inner_text()[:140])

    print("\n### 25. Routing a question to the right expert, end to end", flush=True)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1600)
    if A.get_by_test_id("admin-auth").count():
        A.get_by_test_id("admin-username").fill("benito"); A.get_by_test_id("admin-password").fill("arcade-1978")
        A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(2000)
    check("the admin session is still valid on the expertise screen", A.get_by_test_id("mapping-table").count() > 0)
    A.get_by_test_id("add-mapping").click(); A.wait_for_timeout(900)
    check("adding a mapping with nothing selected is refused", toast(A) != "", "silent")

    # Only someone with an account can be an expert, so the contributor makes
    # one. Before this the admin was asked to pick from a list of hex codes.
    U.goto(base+"/"); U.wait_for_timeout(1500)
    sign_up(U, "ursula")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2000)
    opts = A.get_by_test_id("map-profile").locator("option").all_inner_texts()
    real = [o for o in opts if "Select" not in o]
    check("real contributors are offered as experts", len(real) > 0, str(opts[:4]))
    check("and nobody without an account is offered",
          not [o for o in real if o.startswith("Browser profile")], str(real))
    A.get_by_test_id("map-profile").select_option(index=1)
    A.get_by_test_id("map-concept").select_option(label="Roguelike")
    A.get_by_test_id("add-mapping").click(); A.wait_for_timeout(1600)
    check("the mapping is listed", "Roguelike" in A.get_by_test_id("mapping-table").inner_text(),
          A.get_by_test_id("mapping-table").inner_text()[:120])
    A.get_by_test_id("map-profile").select_option(index=1)
    A.get_by_test_id("map-concept").select_option(label="Roguelike")
    A.get_by_test_id("add-mapping").click(); A.wait_for_timeout(1600)
    dup = A.evaluate("""()=>fetch('/api/admin/expertise').then(r=>r.json()).then(ms=>{
        const names = ms.flatMap(m=>m.areas.map(a=>m.profile_id+'|'+a.name));
        return names.length - new Set(names).size})""")
    check("the same person is not mapped to the same area twice", dup == 0, f"{dup} duplicates")
    check("and the attempt was explained rather than ignored", toast(A) != "", "silent")
    chip = A.get_by_test_id("mapping-table").locator(".area-chips .chip").first
    chip.get_by_title("Remove").click(); A.wait_for_timeout(1600)
    left = A.evaluate("()=>fetch('/api/admin/expertise').then(r=>r.json()).then(ms=>ms.reduce((n,m)=>n+m.areas.length,0))")
    check("removing an expertise area removes exactly that one", left == 0, f"{left} left")

    print("\n### 26. Building a link by hand, including the silly cases", flush=True)
    A.get_by_test_id("tab-links").click(); A.wait_for_timeout(900)
    A.get_by_test_id("add-link").click(); A.wait_for_timeout(900)
    check("a link with nothing chosen is refused", toast(A) != "", "silent")
    A.get_by_test_id("link-src").select_option(label="Roguelike")
    A.get_by_test_id("link-dst").select_option(label="Roguelike")
    A.get_by_test_id("link-note").fill("linking a thing to itself")
    A.get_by_test_id("add-link").click(); A.wait_for_timeout(1600)
    self_link = A.evaluate("()=>fetch('/api/graph/links').then(r=>r.json()).then(l=>l.filter(x=>x.src_id===x.dst_id).length)")
    check("a concept cannot be linked to itself", self_link == 0, f"{self_link} self links, toast={toast(A)!r}")
    A.get_by_test_id("link-src").select_option(label="Roguelike")
    A.get_by_test_id("link-dst").select_option(label="Metroidvania")
    A.get_by_test_id("link-note").fill("   ")
    A.get_by_test_id("add-link").click(); A.wait_for_timeout(1200)
    check("a link with no reason given is refused", toast(A) != "", "silent")

    A.screenshot(path=str(OUT/"final_admin.png"), full_page=True)
    U.goto(base+"/"); U.wait_for_timeout(2000); U.screenshot(path=str(OUT/"final_home.png"), full_page=True)
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
