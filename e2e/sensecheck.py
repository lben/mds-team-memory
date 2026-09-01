"""Not pass/fail. Walk the real story and record what a person would actually
see, so the confusing parts are on the record as text rather than as vibes.

Every screen is photographed into e2e/adv/sense/. Reading this log is half the
pass; looking at the images is the other half — a whole class of defect here
(labels too small, things clipped, panels of empty space) never reaches a log.
"""
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
    path = SHOTS / f"{len(shots):02d}_{name}.png"
    pg.screenshot(path=str(path), full_page=full)
    shots.append(path.name); print(f"       shot -> {path.name}", flush=True)

def answer_ask(pg, text=None, cancel=False):
    pg.wait_for_selector("[data-testid=ask-modal]", timeout=6000)
    if cancel:
        pg.get_by_test_id("ask-cancel").click()
    else:
        if text is not None and pg.get_by_test_id("ask-input").count():
            pg.get_by_test_id("ask-input").fill(text)
        pg.get_by_test_id("ask-confirm").click()
    pg.wait_for_timeout(700)

def sign_up(pg, username, password="a-good-password"):
    pg.get_by_test_id("profile-button").click(); pg.wait_for_timeout(600)
    pg.get_by_test_id("auth-username").fill(username)
    pg.get_by_test_id("auth-password").fill(password)
    pg.get_by_test_id("do-sign-up").click(); pg.wait_for_timeout(3000)

def who(pg):
    return pg.get_by_test_id("profile-button").inner_text().strip().replace("\n", " · ")

with sync_playwright() as pw:
    b = pw.chromium.launch()
    A = b.new_context(viewport={"width":1500,"height":950}).new_page()
    U = b.new_context(viewport={"width":1500,"height":950}).new_page()
    V = b.new_context(viewport={"width":1500,"height":950}).new_page()

    print("\n--- Who am I, according to this app, before I do anything? ---")
    U.goto(base+"/"); U.wait_for_timeout(2200)
    note("identity", f"a new person's sidebar says: {who(U)!r}")
    shot(U, "first_run_empty_home", full=True)
    U.get_by_test_id("profile-button").click(); U.wait_for_timeout(700)
    warn = U.get_by_test_id("no-account-warning")
    note("identity", f"opening it warns: {warn.inner_text().strip()[:170]!r}" if warn.count()
                     else "opening it says nothing about what happens to my data")
    shot(U, "profile_popover_anonymous")
    U.keyboard.press("Escape"); U.get_by_test_id("profile-button").click(); U.wait_for_timeout(400)

    print("\n--- Typing a piece of knowledge and pressing Enter ---")
    U.goto(base+"/"); U.wait_for_timeout(1600)
    U.get_by_test_id("home-input").fill("Dark Souls teaches through failure, not tutorials.")
    U.keyboard.press("Enter"); U.wait_for_timeout(1500)
    searched = U.get_by_test_id("search-banner").count() > 0 or U.get_by_test_id("nothing-found").count() > 0
    note("composer", f"pressing Enter mid-sentence ran a search: {searched}; "
                     f"the box still holds what I typed: {bool(U.get_by_test_id('home-input').input_value().strip())}")
    shot(U, "enter_in_the_composer")

    U.get_by_test_id("home-input").fill("Dark Souls teaches through failure, not tutorials.")
    U.get_by_test_id("do-capture").click(); U.wait_for_timeout(2500)
    if U.locator(".modal-backdrop").count():
        note("capture", f"after sharing, the dialog says: "
                        f"{U.get_by_test_id('success-modal').inner_text().strip().splitlines()[1][:90]!r}")
        note("capture", f"it offers to show me what I just wrote: "
                        f"{U.get_by_test_id('view-contribution').count() > 0}")
        shot(U, "capture_success_modal")
        U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(1200)

    print("\n--- What does my contribution look like to a teammate? ---")
    V.goto(base+"/"); V.wait_for_timeout(2200)
    card = V.get_by_test_id("knowledge-column").locator(".card.result").first
    note("attribution", f"a teammate sees it credited to: {card.locator('.meta').inner_text().replace(chr(10),' ')[:90]!r}")
    shot(V, "teammate_sees_the_contribution")
    card.get_by_role("button", name="Details").click(); V.wait_for_timeout(1800)
    note("attribution", f"the details line reads: "
                        f"{V.get_by_test_id('item-detail').locator('.meta').first.inner_text().replace(chr(10),' ')[:120]!r}")
    shot(V, "item_details_modal")
    V.keyboard.press("Escape"); V.wait_for_timeout(500)

    print("\n--- I make an account. Does my work come with me? ---")
    before = U.evaluate("()=>fetch('/api/profile').then(r=>r.json())")
    sign_up(U, "ursula")
    after = U.evaluate("()=>fetch('/api/profile').then(r=>r.json())")
    note("account", f"before: {before['label']!r} shared={before['totals']['shared']} · "
                    f"after signing up: {after['label']!r} shared={after['totals']['shared']}")
    note("account", f"the sidebar now says: {who(U)!r}")
    shot(U, "signed_up", full=True)
    U.goto(base+"/"); U.wait_for_timeout(1800)
    mine = U.get_by_test_id("knowledge-column").locator(".card.result").first
    note("account", f"my earlier contribution is now credited to: "
                    f"{mine.locator('.meta').inner_text().replace(chr(10),' ')[:70]!r}")

    print("\n--- I am the admin. Does the app know that? ---")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1600)
    shot(A, "admin_sign_in_form")
    A.get_by_test_id("admin-username").fill("benito"); A.get_by_test_id("admin-password").fill("arcade-1978")
    A.get_by_test_id("admin-submit").click(); A.wait_for_timeout(2500)
    note("identity", f"signed in as admin 'benito', the sidebar calls me: {who(A)!r}")
    shot(A, "signed_in_as_admin", full=True)
    A.goto(base+"/"); A.wait_for_timeout(1600)
    A.get_by_test_id("home-input").fill("As the admin I am writing down a fact about Bloodborne.")
    A.get_by_test_id("do-capture").click(); A.wait_for_timeout(2500)
    if A.locator(".modal-backdrop").count(): A.get_by_role("button", name="Add another").click()
    A.goto(base+"/"); A.wait_for_timeout(2000)
    row = A.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="Bloodborne").first
    note("identity", f"the admin's own contribution is credited to: "
                     f"{row.locator('.meta').inner_text().replace(chr(10),' ')[:70]!r}")

    print("\n--- Routing expertise to a person ---")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2000)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    A.get_by_test_id("concept-name").fill("Soulslike"); A.get_by_test_id("add-concept").click(); A.wait_for_timeout(1400)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2200)
    opts = [o for o in A.get_by_test_id("map-profile").locator("option").all_inner_texts() if "Select" not in o]
    note("expertise", f"the admin must pick an expert from this list: {opts}")
    n = A.get_by_test_id("account-required-note")
    note("expertise", f"and the page explains a missing name: {n.inner_text().strip()[:130]!r}" if n.count()
                      else "with no explanation of why somebody might be missing")
    shot(A, "expertise_routing", full=True)

    print("\n--- Endorsing a teammate ---")
    V.goto(base+"/"); V.wait_for_timeout(2200)
    d = V.get_by_test_id("knowledge-column").locator(".card.result").first
    d.get_by_role("button", name="Details").click(); V.wait_for_timeout(1600)
    btn = V.get_by_test_id("item-detail").get_by_role("button", name="Endorse as expert")
    if btn.count():
        btn.click(); V.wait_for_timeout(2000)
        note("endorse", f"an ordinary teammate clicking Endorse gets: "
                        f"{(V.locator('.toast').inner_text() if V.locator('.toast').count() else '(nothing)')!r}")
        shot(V, "endorse_result")
    V.keyboard.press("Escape"); V.wait_for_timeout(400)
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(2000)
    if A.get_by_test_id("tab-endorsed").count():
        A.get_by_test_id("tab-endorsed").click(); A.wait_for_timeout(1600)
        note("endorse", f"the admin's evidence for who the experts are reads: "
                        f"{A.get_by_test_id('endorsed-panel').inner_text().replace(chr(10),' | ')[:200]!r}")
        shot(A, "most_endorsed_tab")

    print("\n--- The leaderboard ---")
    U.goto(base+"/leaderboard"); U.wait_for_timeout(2200)
    note("leaderboard", f"the ranking reads: {U.get_by_test_id('leaderboard').inner_text().replace(chr(10),' | ')[:240]!r}")
    shot(U, "leaderboard", full=True)

    print("\n--- The knowledge graph, at the size a team would actually have ---")
    A.goto(base+"/admin/expertise"); A.wait_for_timeout(1800)
    A.get_by_test_id("tab-concepts").click(); A.wait_for_timeout(700)
    for i in range(6):
        A.get_by_test_id("concept-name").fill(f"Topic{i:02d}")
        A.get_by_test_id("add-concept").click(); A.wait_for_timeout(320)
    U.goto(base+"/"); U.wait_for_timeout(4000)
    g = U.evaluate("""() => {
        const el = document.querySelector('[data-testid=graph]');
        const cy = el && el._cyreg && el._cyreg.cy;
        if (!cy) return null;
        const box = el.getBoundingClientRect(); const ext = cy.elements().renderedBoundingBox();
        return {nodes: cy.nodes().length, edges: cy.edges().length, zoom: +cy.zoom().toFixed(2),
                fill: Math.round(100*(ext.w*ext.h)/(box.width*box.height)),
                labelPx: +(11*cy.zoom()).toFixed(1)};
    }""")
    if g:
        note("graph", f"with {g['nodes']} nodes and {g['edges']} links the drawing fills {g['fill']}% of its panel, "
                      f"at zoom {g['zoom']}, so the labels render at about {g['labelPx']}px")
    shot(U, "knowledge_graph")

    print("\n--- Can I take back a mistake? ---")
    U.goto(base+"/"); U.wait_for_timeout(2000)
    # Whatever is on top may be a teammate's, and then this section silently
    # reports on the wrong card. Pick the one this person actually wrote.
    c = U.get_by_test_id("knowledge-column").locator(".card.result").filter(has_text="Dark Souls").first
    c.get_by_role("button", name="Details").click(); U.wait_for_timeout(1600)
    acts = U.get_by_test_id("item-detail").locator(".result-actions button").all_inner_texts()
    note("undo", f"on my own contribution the app offers: {acts}")
    shot(U, "actions_on_my_own_contribution")
    if U.get_by_test_id("edit-item").count():
        U.get_by_test_id("edit-item").click(); U.wait_for_timeout(700)
        U.get_by_test_id("edit-body").fill("Dark Souls teaches through failure — corrected after the fact.")
        U.get_by_test_id("save-edit").click(); U.wait_for_timeout(2200)
        note("undo", f"after editing, the item reads: "
                     f"{U.get_by_test_id('item-detail').locator('.detail-body').inner_text()[:80]!r}")
        shot(U, "after_editing_my_own_contribution")
    U.keyboard.press("Escape"); U.wait_for_timeout(500)

    U.goto(base+"/documents"); U.wait_for_timeout(1400)
    U.locator("input[type=file]").set_input_files({"name":"oops-wrong-file.txt","mimeType":"text/plain",
        "buffer":b"I did not mean to upload this.\n"})
    U.wait_for_timeout(3000)
    note("undo", f"a wrongly uploaded document offers a way out: {U.get_by_test_id('delete-document').count() > 0}")
    shot(U, "documents_after_upload", full=True)
    if U.get_by_test_id("delete-document").count():
        U.get_by_test_id("delete-document").click(); U.wait_for_timeout(800)
        note("undo", f"and it says what will happen: "
                     f"{U.locator('[data-testid=ask-modal] p').inner_text()[:150]!r}")
        shot(U, "confirm_deleting_a_document")
        answer_ask(U); U.wait_for_timeout(2000)
        note("undo", f"after deleting, the list holds {U.locator('.doc-row').count()} documents")

    print("\n--- Do the numbers add up? ---")
    U.goto(base+"/leaderboard"); U.wait_for_timeout(2000)
    prof = U.evaluate("()=>fetch('/api/profile').then(r=>r.json())")
    imp = U.evaluate("()=>fetch('/api/impact?period=all').then(r=>r.json())")
    mine_row = next((e for e in imp["leaderboard"] if e["is_me"]), None)
    note("numbers", f"my sidebar profile says shared={prof['totals']['shared']} score={prof['totals']['score']}; "
                    f"my leaderboard row says shared={mine_row['shared'] if mine_row else None} "
                    f"score={mine_row['score'] if mine_row else None}; rank shown={imp['me']['rank']!r}")
    cols = U.get_by_test_id("leaderboard").locator(".leader-head div").all_inner_texts()
    note("numbers", f"the leaderboard columns are {cols}, and the API also returns "
                    f"endorsements per person, which no column shows")

    print("\n--- The private scratchpad, and what a cookie clear costs ---")
    U.goto(base+"/scratchpad"); U.wait_for_timeout(1800)
    note("scratchpad", f"the page promises: {U.locator('.chip.private').first.inner_text()!r}")
    U.get_by_test_id("scratch-editor").fill("My only copy of something important.")
    U.wait_for_timeout(2200)
    shot(U, "scratchpad")
    note("scratchpad", f"the chip says: {U.get_by_test_id('privacy-chip').inner_text().strip()!r}")
    U.context.clear_cookies(); U.goto(base+"/scratchpad"); U.wait_for_timeout(2500)
    left = U.get_by_test_id("scratch-editor").input_value() if U.get_by_test_id("scratch-editor").count() else ""
    note("scratchpad", f"after clearing cookies I am {who(U)!r} and the notes are still there: {bool(left.strip())}")
    shot(U, "scratchpad_after_clearing_cookies", full=True)
    # Do not assert recovery — perform it, and report what actually came back.
    U.goto(base+"/"); U.wait_for_timeout(1600)
    U.get_by_test_id("profile-button").click(); U.wait_for_timeout(600)
    U.get_by_test_id("auth-username").fill("ursula"); U.get_by_test_id("auth-password").fill("a-good-password")
    U.get_by_test_id("do-sign-in").click(); U.wait_for_timeout(3000)
    U.goto(base+"/scratchpad"); U.wait_for_timeout(2500)
    back = U.get_by_test_id("scratch-editor").input_value() if U.get_by_test_id("scratch-editor").count() else ""
    note("scratchpad", f"signing back in returns me to {who(U)!r} and my notes come back: {bool(back.strip())}")
    shot(U, "scratchpad_after_signing_back_in", full=True)

    print("\n--- Does the app ever tell me anything happened? ---")
    U.goto(base+"/"); U.wait_for_timeout(1800)
    U.get_by_test_id("bell").click(); U.wait_for_timeout(1200)
    pop = U.locator(".notif-pop")
    note("notifications", f"the bell opens and says: {pop.inner_text().replace(chr(10),' ')[:160]!r}"
                          if pop.count() else "the bell opened nothing")
    shot(U, "notifications_panel")
    b.close()

srv.terminate(); srv.wait(timeout=10)
print("\n" + "="*70)
print(f"OBSERVATIONS: {len(notes)}")
print(f"SCREENSHOTS: {len(shots)} in {SHOTS} — read them as images, do not just read this log")
for s_ in shots: print("   ", s_)
