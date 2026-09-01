"""Not pass/fail. Walk the real story and record what a person would actually
see, so the confusing parts are on the record as text rather than as vibes."""
import os, socket, subprocess, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as PWError

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv/bin"
OUT = Path(__file__).parent / "adv"; OUT.mkdir(exist_ok=True)
SHOTS = OUT / "sense"; SHOTS.mkdir(exist_ok=True)
for _old in SHOTS.glob("*.png"): _old.unlink()
data = Path(tempfile.mkdtemp())
env = {**os.environ, "MDS_DATA_DIR": str(data), "MDS_DATABASE_URL": f"sqlite:///{data/'x.sqlite3'}"}
subprocess.run([str(VENV/"alembic"), "-c", str(ROOT/"backend/alembic.ini"), "upgrade", "head"],
               env=env, check=True, capture_output=True, cwd=ROOT/"backend")
subprocess.run([str(VENV/"python"), str(ROOT/"manage.py"), "create-admin", "--username", "benito"],
               input="arcade-1978\narcade-1978\n", capture_output=True, text=True, env=env)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
srv = subprocess.Popen([str(VENV/"uvicorn"), "app.main:app", "--app-dir", str(ROOT/"backend"), "--port", str(port)],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{port}"
for _ in range(80):
    try: urllib.request.urlopen(base+"/api/health", timeout=1); break
    except OSError: time.sleep(0.25)

notes = []
def note(area, what):
    notes.append((area, what)); print(f"  [{area}] {what}", flush=True)

shots = []
def shot(pg, name, full=False):
    """Half of this class of problem is visual and never appears in a log.
    Every screen this walk touches is photographed so it can be LOOKED at."""
    path = SHOTS / f"{len(shots):02d}_{name}.png"
    pg.screenshot(path=str(path), full_page=full)
    shots.append(path.name); print(f"       shot -> {path.name}", flush=True)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    A = b.new_context(viewport={"width":1500,"height":950}).new_page()
    U = b.new_context(viewport={"width":1500,"height":950}).new_page()
    V = b.new_context(viewport={"width":1500,"height":950}).new_page()

    print("\n--- Who am I, according to this app? ---")
    U.goto(base+"/"); U.wait_for_timeout(2000)
    note("identity", f"a new person's sidebar says: {U.get_by_test_id('profile-button').inner_text().strip()!r}")
    shot(U, "first_run_empty_home", full=True)

    print("\n--- Typing a piece of knowledge and pressing Enter ---")
    U.get_by_test_id("home-input").fill("Dark Souls teaches through failure, not tutorials.")
    U.keyboard.press("Enter"); U.wait_for_timeout(1800)
    searched = U.get_by_test_id("search-banner").count() > 0 or U.get_by_test_id("nothing-found").count() > 0
    note("composer", f"pressing Enter mid-sentence ran a SEARCH: {searched}; "
                     f"box still holds the text: {bool(U.get_by_test_id('home-input').input_value().strip())}")
    shot(U, "enter_ran_a_search")
    U.get_by_test_id("home-input").fill("line one")
    U.keyboard.press("Enter"); U.wait_for_timeout(900)
    U.get_by_test_id("home-input").fill("line one")
    U.keyboard.press("Shift+Enter"); U.keyboard.type("line two"); U.wait_for_timeout(600)
    note("composer", f"Shift+Enter for a newline works: {chr(10) in U.get_by_test_id('home-input').input_value()}")

    U.goto(base+"/"); U.wait_for_timeout(1500)
    U.get_by_test_id("home-input").fill("Dark Souls teaches through failure, not tutorials.")
    U.get_by_test_id("do-capture").click(); U.wait_for_timeout(2500)
    if U.locator(".modal-backdrop").count():
        note("capture", f"after sharing, the dialog says: "
                        f"{U.get_by_test_id('success-modal').inner_text().strip().splitlines()[1][:90]!r}")
        shot(U, "capture_success_modal")
        U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(1200)

    print("\n--- What does my contribution look like to a teammate? ---")
    V.goto(base+"/"); V.wait_for_timeout(2200)
    card = V.get_by_test_id("knowledge-column").locator(".card.result").first
    note("attribution", f"a teammate sees it credited to: {card.locator('.meta').inner_text().replace(chr(10),' ')[:90]!r}")
    shot(V, "teammate_sees_the_contribution")
    card.get_by_role("button", name="Details").click(); V.wait_for_timeout(1800)
    meta = V.get_by_test_id("item-detail").locator(".meta").first.inner_text().replace("\n", " ")
    note("attribution", f"the details line reads: {meta[:110]!r}")
    shot(V, "item_details_modal")
    V.keyboard.press("Escape"); V.wait_for_timeout(500)

    print("\n--- I am the admin. Does the app know that? ---")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1400)
    shot(A, "admin_sign_in_form")
    A.get_by_test_id("admin-username").fill("benito"); A.get_by_test_id("admin-password").fill("arcade-1978")
    A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(2200)
    note("identity", f"signed in as admin 'benito', the sidebar still calls me: "
                     f"{A.get_by_test_id('profile-button').inner_text().strip().replace(chr(10), ' ')!r}")
    shot(A, "signed_in_as_admin", full=True)
    A.goto(base+"/"); A.wait_for_timeout(1500)
    A.get_by_test_id("home-input").fill("As the admin I am writing down a fact about Bloodborne.")
    A.get_by_test_id("do-capture").click(); A.wait_for_timeout(2500)
    if A.locator(".modal-backdrop").count(): A.get_by_role("button", name="Add another").click()
    A.wait_for_timeout(1500)
    A.goto(base+"/"); A.wait_for_timeout(1800)
    who = A.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="Bloodborne").first
    note("identity", f"the admin's own contribution is credited to: "
                     f"{who.locator('.meta').inner_text().replace(chr(10),' ')[:70]!r}")

    print("\n--- Routing expertise to a person ---")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2000)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    A.get_by_test_id("concept-name").fill("Soulslike"); A.get_by_test_id("add-concept").click(); A.wait_for_timeout(1200)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2000)
    opts = [o for o in A.get_by_test_id("map-profile").locator("option").all_inner_texts() if "Select" not in o]
    note("expertise", f"the admin must pick an expert from this list: {opts}")
    shot(A, "expertise_routing", full=True)

    print("\n--- The leaderboard ---")
    U.goto(base+"/leaderboard"); U.wait_for_timeout(2000)
    lb = U.get_by_test_id("leaderboard").inner_text().replace("\n", " | ")
    note("leaderboard", f"the ranking reads: {lb[:220]!r}")
    shot(U, "leaderboard", full=True)

    print("\n--- The knowledge graph, at the size a team would actually have ---")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1800)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    for i in range(14):
        A.get_by_test_id("concept-name").fill(f"Topic{i:02d}")
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(280)
    U.goto(base+"/"); U.wait_for_timeout(3500)
    g = U.evaluate("""() => {
        const el = document.querySelector('[data-testid=graph]');
        const cy = el && el._cyreg && el._cyreg.cy;
        const box = el.getBoundingClientRect();
        if (!cy) return null;
        const ext = cy.elements().renderedBoundingBox();
        return {nodes: cy.nodes().length, edges: cy.edges().length,
                boxArea: Math.round(box.width*box.height/1000),
                usedArea: Math.round(ext.w*ext.h/1000)};
    }""")
    note("graph", f"with {g['nodes']} nodes and {g['edges']} links, the drawing fills "
                  f"{round(100*g['usedArea']/g['boxArea'])}% of its panel")
    note("graph", f"panel title: {U.get_by_test_id('graph-title').inner_text().strip()!r}")
    U.screenshot(path=str(OUT/"sense_graph.png"))

    print("\n--- Things you cannot undo ---")
    U.goto(base+"/documents"); U.wait_for_timeout(1200)
    U.locator("input[type=file]").set_input_files({"name":"oops-wrong-file.txt","mimeType":"text/plain",
        "buffer":b"I did not mean to upload this.\n"})
    U.wait_for_timeout(2800)
    note("undo", f"a wrongly uploaded document can be removed: "
                 f"{U.get_by_role('button', name='Delete').count() > 0}")
    shot(U, "documents_after_upload", full=True)
    U.goto(base+"/"); U.wait_for_timeout(1800)
    c = U.get_by_test_id("knowledge-column").locator(".card.result").first
    acts = " / ".join(c.locator(".result-actions button").all_inner_texts())
    note("undo", f"actions on your own contribution: {acts!r}")
    shot(U, "own_contribution_actions")

    print("\n--- The private scratchpad ---")
    U.goto(base+"/scratchpad"); U.wait_for_timeout(1500)
    note("scratchpad", f"the page promises: {U.locator('.chip.private').first.inner_text()!r}")
    shot(U, "scratchpad")
    U.get_by_test_id("scratch-editor").fill("My only copy of something important.")
    U.wait_for_timeout(2000)
    U.context.clear_cookies(); U.goto(base+"/scratchpad"); U.wait_for_timeout(2200)
    left = U.get_by_test_id("scratch-editor").input_value() if U.get_by_test_id("scratch-editor").count() else ""
    note("scratchpad", f"after clearing cookies, the private notes still there: {bool(left.strip())}")
    shot(U, "scratchpad_after_clearing_cookies", full=True)

    print("\n--- Endorsement ---")
    V.goto(base+"/"); V.wait_for_timeout(2000)
    d = V.get_by_test_id("knowledge-column").locator(".card.result").first
    d.get_by_role("button", name="Details").click(); V.wait_for_timeout(1600)
    has_btn = V.get_by_test_id("item-detail").get_by_role("button", name="Endorse as expert").count() > 0
    if has_btn:
        V.get_by_test_id("item-detail").get_by_role("button", name="Endorse as expert").click()
        V.wait_for_timeout(1800)
        t = V.locator(".toast").inner_text() if V.locator(".toast").count() else ""
        shot(V, "endorse_rejected")
        note("endorse", f"the button is offered to everyone; clicking it as a non-expert says: {t!r}")

    print("\n--- Do the numbers add up? ---")
    prof = U.evaluate("() => fetch('/api/profile').then(r => r.json())")
    imp = U.evaluate("() => fetch('/api/impact?period=all').then(r => r.json())")
    mine = next((e for e in imp["leaderboard"] if e["is_me"]), None)
    note("numbers", f"my sidebar profile says shared={prof['totals']['shared']} score={prof['totals']['score']}; "
                    f"my leaderboard row says shared={mine['shared'] if mine else None} "
                    f"score={mine['score'] if mine else None}; rank shown={imp['me']['rank']!r}")
    cols = U.get_by_test_id("leaderboard").locator(".leader-head div").all_inner_texts()
    note("numbers", f"the leaderboard shows these columns: {cols}; "
                    f"the API also returns endorsements per person, which no column displays")

    print("\n--- Does the app ever tell me anything happened? ---")
    U.goto(base+"/"); U.wait_for_timeout(1600)
    U.get_by_test_id("bell").click(); U.wait_for_timeout(1200)
    pop = U.locator(".notif-pop")
    note("notifications", f"the bell opens and says: {pop.inner_text().replace(chr(10),' ')[:150]!r}"
                          if pop.count() else "the bell opened nothing")
    shot(U, "notifications_panel")

    print("\n--- Is every offered action one I can actually complete? ---")
    U.goto(base+"/"); U.wait_for_timeout(1800)
    own = U.get_by_test_id("knowledge-column").locator(".card.result").first
    states = own.evaluate("""el => [...el.querySelectorAll('.result-actions button')]
        .map(b => `${b.innerText.trim()}${b.disabled ? ' [DISABLED, no reason given]' : ''}`)""")
    note("affordance", f"on my own contribution the app offers: {states}")
    shot(U, "actions_on_my_own_contribution")
    b.close()

srv.terminate(); srv.wait(timeout=10)
print("\n" + "="*70)
print(f"OBSERVATIONS: {len(notes)}")
print(f"SCREENSHOTS: {len(shots)} in {SHOTS} — read them as images, do not just read this log")
for s_ in shots: print("   ", s_)
