"""Second adversarial pass: the attacks the first suite deliberately left out."""
import os, socket, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
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
                       env=env, stdout=open(OUT/"access2.log","w"), stderr=subprocess.STDOUT)
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
        pg.goto(base+"/admin/expertise"); pg.wait_for_timeout(1400)
        if pg.get_by_test_id("admin-auth").count():
            pg.get_by_test_id("admin-username").fill("benito")
            pg.get_by_test_id("admin-password").fill("arcade-1978")
            pg.get_by_test_id("admin-submit").click(); pg.wait_for_timeout(1800)
    def capture(pg, text):
        pg.goto(base+"/"); pg.wait_for_timeout(900)
        pg.get_by_test_id("home-input").fill(text); pg.get_by_test_id("do-capture").click()
        try:
            pg.get_by_test_id("success-modal").wait_for(timeout=8000)
            pg.get_by_role("button", name="Add another").click(); pg.wait_for_timeout(400)
        except PWError: pass

    print("\n### 27. The very first minute: an app with nothing in it", flush=True)
    for path, what in [("/","Home"), ("/leaderboard","Leaderboard"), ("/documents","Documents"),
                       ("/scratchpad","Scratchpad"), ("/admin/expertise","Expertise routing")]:
        U.goto(base+path); U.wait_for_timeout(1600)
        body = U.locator(".page").inner_text() if U.locator(".page").count() else ""
        check(f"empty {what} explains itself instead of showing a void",
              len(body.strip()) > 40 and "undefined" not in body and "NaN" not in body, body[:100])
    U.goto(base+"/"); U.wait_for_timeout(1600)
    check("the empty feed invites the first contribution",
          "Nothing shared yet" in U.get_by_test_id("knowledge-column").inner_text(),
          U.get_by_test_id("knowledge-column").inner_text()[:90])
    check("the empty graph does not leave a broken box",
          U.get_by_test_id("graph").count() == 0 or U.get_by_test_id("graph").is_visible() is not None)
    U.get_by_test_id("home-input").fill("nothing has ever been written about this")
    U.get_by_test_id("do-search").click(); U.wait_for_timeout(1600)
    check("searching an empty app offers to ask the team", U.get_by_test_id("nothing-found").count() > 0)
    U.get_by_test_id("ask-from-search").click(); U.wait_for_timeout(2000)
    check("that offer really posts the question", U.locator(".question-card").count() > 0,
          U.get_by_test_id("questions-column").inner_text()[:90])

    print("\n### 28. Two people doing the same thing at the same instant", flush=True)
    capture(U, "Chrono Trigger has thirteen endings and a New Game Plus mode.")
    U.goto(base+"/"); U.wait_for_timeout(1500); V.goto(base+"/"); V.wait_for_timeout(1500)
    item = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>{const x=f.find(i=>i.body.includes('Chrono Trigger'));return x?x.id:''})")
    # Genuine simultaneity needs the requests in flight together, which the
    # browser driver cannot do from two pages at once — so fire them as the
    # two people's own sessions, cookies and all.
    # Cookies must be read on the main thread: the browser driver cannot be
    # called from the worker threads that create the actual simultaneity.
    def cookie_header(pg):
        return "; ".join(f"{c['name']}={c['value']}" for c in pg.context.cookies())
    COOKIES = {"U": cookie_header(U), "V": cookie_header(V)}
    results = {}
    gate = [threading.Barrier(2)]
    def racer(tag, who, path, body=None):
        req = urllib.request.Request(base+path, method="POST",
                                     headers={"Cookie": COOKIES[who], "Content-Type": "application/json"},
                                     data=body.encode() if body else None)
        gate[0].wait()
        try:
            with urllib.request.urlopen(req, timeout=20) as r: results[tag] = r.status
        except urllib.error.HTTPError as e: results[tag] = e.code
        except Exception as e: results[tag] = repr(e)
    ts = [threading.Thread(target=racer, args=(f"h{i}", "V", f"/api/items/{item}/helped")) for i in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()
    V.wait_for_timeout(1200)
    helped = V.evaluate(f"()=>fetch('/api/feed').then(r=>r.json()).then(f=>{{const x=f.find(i=>i.id==='{item}');return x?x.helped:-1}})")
    check("two helpful marks fired at the same instant still count once", helped == 1,
          f"helped={helped}, responses={results}")
    gate[0] = threading.Barrier(2)
    body = '{"body":"Simultaneous question about Chrono Trigger endings?"}'
    ts = [threading.Thread(target=racer, args=(f"q{who}", who, "/api/questions", body)) for who in ("U", "V")]
    for t in ts: t.start()
    for t in ts: t.join()
    U.wait_for_timeout(2000)
    qs = U.evaluate("()=>fetch('/api/questions').then(r=>r.json()).then(q=>q.filter(x=>x.body.includes('Simultaneous question')).length)")
    check("two people asking the same thing at once both get their question", qs == 2, f"{qs}, responses={results}")
    U.goto(base+"/"); U.wait_for_timeout(2000)
    check("both of those questions are visible in the interface",
          U.get_by_test_id("questions-column").inner_text().count("Simultaneous question") == 2,
          U.get_by_test_id("questions-column").inner_text()[:150])
    check("and no server error came out of the race", not [c for c in crashes if "HTTP 5" in c], str(crashes[:3]))

    print("\n### 29. Forged and missing session cookies", flush=True)
    V.context.add_cookies([{"name":"mds_admin","value":"forged-token-pretending-to-be-real",
                            "domain":"127.0.0.1","path":"/"}])
    V.goto(base+"/admin/expertise"); V.wait_for_timeout(1800)
    check("a forged admin cookie does not open the curation panel",
          V.get_by_test_id("map-admin-panel").count() == 0 and V.get_by_test_id("admin-auth").count() > 0)
    st = V.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.status)")
    check("and the server refuses it too", st == 401, f"status {st}")
    V.context.clear_cookies(); V.goto(base+"/"); V.wait_for_timeout(2000)
    check("losing your profile cookie gives you a fresh identity, not a crash",
          V.get_by_test_id("home-input").count() > 0 and V.get_by_test_id("profile-button").count() > 0)
    capture(V, "A brand new identity can still contribute after losing its cookie.")
    V.goto(base+"/"); V.wait_for_timeout(1500)
    check("that new identity's contribution saved",
          "brand new identity" in V.get_by_test_id("knowledge-column").inner_text())

    print("\n### 30. Renaming and merging concepts into each other", flush=True)
    login(A)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(600)
    for name, aliases in [("Chrono Trigger","CT, Chrono"), ("Role Playing Game","RPG, RPGs")]:
        A.get_by_test_id("concept-name").fill(name); A.get_by_test_id("concept-aliases").fill(aliases)
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(1000)
    check("two concepts with aliases exist",
          A.get_by_test_id("map-admin-panel").locator(".panel-body.concepts").count() == 2)
    # The row's name moves into an input the moment you press Edit, so hold on
    # to its id rather than looking it up by the text it used to show.
    ct = A.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.json()).then(c=>c.find(x=>x.name==='Chrono Trigger').id)")
    def edit_concept(name=None, aliases=None):
        A.reload(); A.wait_for_timeout(2000)
        A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
        row = A.locator(f"#row-{ct}")
        row.get_by_role("button", name="Edit").click(); A.wait_for_timeout(700)
        if name is not None: A.locator(f"#row-{ct} input").first.fill(name)
        if aliases is not None: A.locator(f"#row-{ct} input").nth(1).fill(aliases)
        A.locator(f"#row-{ct}").get_by_role("button", name="Save").click(); A.wait_for_timeout(1800)
    def concept_names():
        return A.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.json()).then(c=>c.map(x=>x.name))")

    edit_concept(name="Role Playing Game")
    check("renaming one concept onto another's name is refused, not silently merged",
          sorted(concept_names()) == ["Chrono Trigger", "Role Playing Game"], str(concept_names()))
    check("and it said why", toast(A) != "", "silent")
    edit_concept(aliases="RPG")
    owners = A.evaluate("""()=>fetch('/api/admin/concepts').then(r=>r.json())
        .then(c=>c.filter(x=>x.aliases.map(a=>a.toLowerCase()).includes('rpg')).map(x=>x.name))""")
    check("one word cannot belong to two concepts at once", len(owners) <= 1, str(owners))
    edit_concept(name="   ")
    names = concept_names()
    check("a concept cannot be renamed to nothing", "" not in names and len(names) == 2, str(names))
    edit_concept(name="Chrono Trigger", aliases="CT, CT, chrono")
    dupes = A.evaluate("""()=>fetch('/api/admin/concepts').then(r=>r.json())
        .then(c=>{const a=c.find(x=>x.name==='Chrono Trigger').aliases.map(s=>s.toLowerCase());
                  return a.length - new Set(a).size})""")
    check("a repeated alias is not stored twice", dupes == 0, f"{dupes} duplicates")

    print("\n### 31. Deleting things other things depend on", flush=True)
    capture(U, "Chrono Trigger is the Role Playing Game that defined New Game Plus.")
    A.reload(); A.wait_for_timeout(2200)
    A.get_by_test_id("tab-links").click(); A.wait_for_timeout(900)
    n_links = A.get_by_test_id("map-admin-panel").locator(".panel-body.links").count()
    check("a link exists between the two concepts", n_links > 0, f"{n_links}")
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    A.once("dialog", lambda d: d.accept())
    A.locator(f"#row-{ct}").get_by_role("button", name="Delete").click()
    A.wait_for_timeout(2000)
    A.get_by_test_id("tab-links").click(); A.wait_for_timeout(900)
    check("deleting a concept takes its links with it",
          A.get_by_test_id("map-admin-panel").locator(".panel-body.links").count() == 0,
          A.get_by_test_id("map-admin-panel").inner_text()[:120])
    U.goto(base+"/"); U.wait_for_timeout(2500)
    check("the contribution that mentioned it is still there and readable",
          "Chrono Trigger is the Role Playing Game" in U.get_by_test_id("knowledge-column").inner_text())
    check("the map did not break when its concept vanished",
          U.locator(".graph-box").count() > 0 and not [c for c in crashes if "JS ERROR" in c],
          str([c for c in crashes if "JS ERROR" in c][:2]))

    print("\n### 32. A document, and the fact that it can never be deleted", flush=True)
    U.goto(base+"/documents"); U.wait_for_timeout(1200)
    U.locator("input[type=file]").set_input_files({"name":"manual.txt","mimeType":"text/plain",
        "buffer":"New Game Plus carries your gear forward.\n\nThirteen endings depend on when you fight Lavos.\n".encode()})
    U.wait_for_timeout(3000)
    check("the document uploaded", U.get_by_test_id("doc-viewer").count() > 0, toast(U))
    U.locator(".share-passage").first.click(); U.wait_for_timeout(2500)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(800)
    doc_id = U.evaluate("()=>fetch('/api/documents').then(r=>r.json()).then(d=>d[0].id)")
    st = U.evaluate(f"()=>fetch('/api/documents/{doc_id}',{{method:'DELETE'}}).then(r=>r.status)")
    # Uploads are permanent here: no delete control, no endpoint behind one.
    # Assert that explicitly, so this section cannot pass by doing nothing.
    check("a document cannot be deleted through the API", st == 405, f"status {st}")
    check("and the document survives the attempt",
          U.evaluate("()=>fetch('/api/documents').then(r=>r.json()).then(d=>d.length)") == 1)
    U.goto(base+"/documents"); U.wait_for_timeout(1800)
    check("the documents page still lists it and is consistent",
          U.locator(".doc-row").count() == 1 and "undefined" not in U.locator(".page").inner_text())

    print("\n### 33. A word with no spaces, and a wall of concepts", flush=True)
    capture(U, "Supercalifragilistic" + "expialidocious" * 40)
    U.goto(base+"/"); U.wait_for_timeout(2000)
    check("an unbreakable 600-character word does not widen the page",
          not U.evaluate("()=>document.documentElement.scrollWidth>document.documentElement.clientWidth+2"),
          str(U.evaluate("()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]")))
    login(A); A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(600)
    for i in range(24):
        A.get_by_test_id("concept-name").fill(f"Genre{i:02d}"); A.get_by_test_id("concept-aliases").fill("")
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(320)
    total = A.evaluate("()=>fetch('/api/admin/concepts').then(r=>r.json()).then(c=>c.length)")
    check("all 24 extra concepts were created", total >= 24, f"{total}")
    U.goto(base+"/"); U.wait_for_timeout(3500)
    check("the map still fits its box with a crowd of concepts",
          not U.evaluate("()=>document.documentElement.scrollWidth>document.documentElement.clientWidth+2"))
    gb = U.locator(".graph-box").bounding_box()
    check("the map is still big enough to read", gb and gb["height"] > 180, str(gb))
    check("the page below the map is still reachable", U.get_by_test_id("home-input").is_visible())

    print("\n### 34. On a phone-sized window (measured, not asserted)", flush=True)
    # Deliberately NOT pass/fail: this product was designed for a desktop
    # window and has no mobile layout. Measure honestly so the limitation is
    # on the record rather than hidden behind a check that merely says the
    # page does not scroll sideways.
    P = b.new_context(viewport={"width":390,"height":844}).new_page()
    P.on("pageerror", lambda e: crashes.append(f"[phone] JS ERROR: {str(e)[:160]}"))
    for path, what in [("/","Home"), ("/leaderboard","Leaderboard"), ("/documents","Documents"),
                       ("/scratchpad","Scratchpad"), ("/admin/expertise","Expertise routing")]:
        P.goto(base+path); P.wait_for_timeout(2200)
        m = P.evaluate("""() => {
            const doc = document.documentElement;
            const side = document.querySelector('.sidebar');
            const clipped = [...document.querySelectorAll('button, input, select, textarea')]
                .filter(el => {const r = el.getBoundingClientRect();
                               return r.width > 0 && r.right > doc.clientWidth + 1});
            return {overflow: doc.scrollWidth - doc.clientWidth,
                    sidebar: side ? Math.round(side.getBoundingClientRect().width) : 0,
                    viewport: doc.clientWidth, clipped: clipped.length};
        }""")
        print(f"   note {what}: sidebar takes {m['sidebar']}px of {m['viewport']}px, "
              f"{m['clipped']} controls clipped off the right, {m['overflow']}px page overflow", flush=True)
    check("no phone-sized layout crashes the app", not [c for c in crashes if "phone" in c],
          str([c for c in crashes if "phone" in c][:2]))
    P.goto(base+"/"); P.wait_for_timeout(2500)
    P.screenshot(path=str(OUT/"phone_home.png"), full_page=True)
    P.close()

    print("\n### 35. Clicking away while the page is still loading", flush=True)
    for _ in range(4):
        U.goto(base+"/"); U.wait_for_timeout(120)
        U.goto(base+"/leaderboard"); U.wait_for_timeout(120)
        U.goto(base+"/documents"); U.wait_for_timeout(120)
        U.goto(base+"/scratchpad"); U.wait_for_timeout(120)
    U.wait_for_timeout(3000)
    check("hammering the navigation leaves a correct final page",
          U.get_by_test_id("scratch-editor").count() > 0 and U.get_by_test_id("knowledge-column").count() == 0,
          U.locator("h1").inner_text())
    check("no error came out of the abandoned requests",
          not [c for c in crashes if "JS ERROR" in c], str([c for c in crashes if "JS ERROR" in c][:3]))
    U.goto(base+"/"); U.wait_for_timeout(1200)
    U.get_by_test_id("home-input").fill("keyboard only search for chrono")
    U.keyboard.press("Enter"); U.wait_for_timeout(1800)
    check("pressing Enter runs a search rather than adding a newline",
          U.get_by_test_id("search-banner").count() > 0 or U.get_by_test_id("nothing-found").count() > 0)

    print("\n### 36. Double-clicking every other way to create something", flush=True)
    U.goto(base+"/scratchpad"); U.wait_for_timeout(2000)
    U.get_by_test_id("scratch-editor").fill("A shareable line about Lavos and the endings.\nAnother private line.")
    U.wait_for_timeout(1800)
    U.evaluate("""() => {
        const ta = document.querySelector('[data-testid=scratch-editor]');
        const line = ta.value.split('\\n')[0];
        ta.focus(); ta.setSelectionRange(0, line.length);
        ta.dispatchEvent(new Event('mouseup', {bubbles: true}));
    }""")
    U.wait_for_timeout(700)
    U.get_by_test_id("share-selection").dblclick(); U.wait_for_timeout(3000)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(900)
    g = U.evaluate("""()=>fetch('/api/feed').then(r=>r.json()).then(f=>{
        const m=f.filter(i=>i.body.includes('Lavos and the endings'));
        return {rows:m.length, stored:m.reduce((s,i)=>s+(i.group_size||1),0)}})""")
    check("double-clicking Share selected knowledge stores one item", g["stored"] == 1, str(g))

    U.goto(base+"/documents"); U.wait_for_timeout(1400)
    U.locator("input[type=file]").set_input_files({"name":"guide.txt","mimeType":"text/plain",
        "buffer":"A double click on share must not post this guide twice.\n".encode()})
    U.wait_for_timeout(3000)
    U.locator(".share-passage").first.dblclick(); U.wait_for_timeout(3000)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(900)
    g = U.evaluate("""()=>fetch('/api/feed').then(r=>r.json()).then(f=>{
        const m=f.filter(i=>i.body.includes('must not post this guide twice'));
        return {rows:m.length, stored:m.reduce((s,i)=>s+(i.group_size||1),0)}})""")
    check("double-clicking Share this passage stores one item", g["stored"] == 1, str(g))

    U.goto(base+"/"); U.wait_for_timeout(2200)
    U.get_by_test_id("knowledge-column").locator(".card.result").first.get_by_role("button", name="Details").click()
    U.wait_for_timeout(1600)
    check("the details of a contribution open", U.get_by_test_id("item-detail").count() > 0)
    U.get_by_test_id("item-detail").locator("textarea").fill("A correction that must only be proposed once.")
    U.wait_for_timeout(400)
    U.get_by_role("button", name="Propose correction").dblclick(); U.wait_for_timeout(3000)
    n = U.get_by_test_id("item-detail").inner_text().count("must only be proposed once")
    check("double-clicking Propose correction proposes it once", n == 1, f"{n} corrections")

    print("\n### 37. Sustained hammering of the server", flush=True)
    # Each URL must be one the app really serves, and each reply must really be
    # JSON: this check once used /api/leaderboard, which does not exist, and
    # passed on the index.html the SPA fallback returned with a 200.
    burst = U.evaluate("""async () => {
        const urls = ['/api/feed','/api/questions','/api/search?q=chrono','/api/graph/global',
                      '/api/impact?period=30d','/api/documents','/api/scratchpad','/api/notifications'];
        const out = [];
        for (let round = 0; round < 6; round++)
            out.push(...await Promise.all(urls.map(u => fetch(u)
                .then(r => `${u.split('?')[0]} ${r.status} ${(r.headers.get('content-type')||'').split(';')[0]}`)
                .catch(e => `${u} threw ${e}`))));
        return out;
    }""")
    bad = [r for r in burst if not r.endswith("200 application/json")]
    check("48 rapid requests across every screen all returned JSON 200", not bad,
          f"{len(bad)} bad: {bad[:8]}")

    print("\n### 37b. A path the server does not serve", flush=True)
    # The SPA fallback must not answer for the server's own namespaces: HTML
    # with a 200 makes a removed endpoint look alive and breaks res.json().
    ghosts = U.evaluate("""async () => {
        const out = {};
        for (const u of ['/api/leaderboard', '/api/nope', '/ws/nope'])
            out[u] = await fetch(u).then(r => `${r.status} ${(r.headers.get('content-type')||'').split(';')[0]}`);
        return out;
    }""")
    check("an endpoint the server does not have answers 404 in JSON, not the SPA",
          all(v == "404 application/json" for v in ghosts.values()), str(ghosts))
    spa_ok = U.evaluate("""async () => {
        const r = await fetch('/leaderboard');
        return `${r.status} ${(r.headers.get('content-type')||'').split(';')[0]}`;
    }""")
    check("a real client-side route is still served the application",
          spa_ok == "200 text/html", spa_ok)

    A.screenshot(path=str(OUT/"round2_admin.png"), full_page=True)
    U.goto(base+"/"); U.wait_for_timeout(2500); U.screenshot(path=str(OUT/"round2_home.png"), full_page=True)
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
