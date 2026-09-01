"""Focused: double-click the three creating actions whose guards were added by
root-cause extension rather than by reproducing a failure. Run once with the
guards in place and once with them removed, to prove these checks can fail."""
import os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, Error as PWError

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv/bin"
data = Path(tempfile.mkdtemp())
env = {**os.environ, "MDS_DATA_DIR": str(data), "MDS_DATABASE_URL": f"sqlite:///{data/'x.sqlite3'}"}
subprocess.run([str(VENV/"alembic"), "-c", str(ROOT/"backend/alembic.ini"), "upgrade", "head"],
               env=env, check=True, capture_output=True, cwd=ROOT/"backend")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
srv = subprocess.Popen([str(VENV/"uvicorn"), "app.main:app", "--app-dir", str(ROOT/"backend"), "--port", str(port)],
                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{port}"
for _ in range(80):
    try: urllib.request.urlopen(base+"/api/health", timeout=1); break
    except OSError: time.sleep(0.25)

FAILS = []
def check(name, ok, detail=""):
    print(f"   {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" :: {detail}"), flush=True)
    if not ok: FAILS.append(name)

with sync_playwright() as pw:
    b = pw.chromium.launch()
    U = b.new_context(viewport={"width":1500,"height":950}).new_page()

    U.goto(base+"/scratchpad"); U.wait_for_timeout(2000)
    U.get_by_test_id("scratch-editor").fill("A shareable line about Lavos and the endings.\nAnother line.")
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
    g = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>{const m=f.filter(i=>i.body.includes('Lavos and the endings'));return {rows:m.length, stored:m.reduce((s,i)=>s+(i.group_size||1),0)}})")
    check("double-clicking Share selected knowledge stores one item", g["stored"] == 1, str(g))

    U.goto(base+"/documents"); U.wait_for_timeout(1400)
    U.locator("input[type=file]").set_input_files({"name":"guide.txt","mimeType":"text/plain",
        "buffer":"A double click on share must not post this guide twice.\n".encode()})
    U.wait_for_timeout(3000)
    U.locator(".share-passage").first.dblclick(); U.wait_for_timeout(3000)
    if U.locator(".modal-backdrop").count(): U.get_by_role("button", name="Add another").click()
    U.wait_for_timeout(900)
    g = U.evaluate("()=>fetch('/api/feed').then(r=>r.json()).then(f=>{const m=f.filter(i=>i.body.includes('must not post this guide twice'));return {rows:m.length, stored:m.reduce((s,i)=>s+(i.group_size||1),0)}})")
    check("double-clicking Share this passage stores one item", g["stored"] == 1, str(g))

    U.goto(base+"/"); U.wait_for_timeout(2200)
    U.get_by_test_id("knowledge-column").locator(".card.result").first.get_by_role("button", name="Details").click()
    U.wait_for_timeout(1600)
    U.get_by_test_id("item-detail").locator("textarea").fill("A correction that must only be proposed once.")
    U.wait_for_timeout(400)
    U.get_by_role("button", name="Propose correction").dblclick(); U.wait_for_timeout(3000)
    n = U.get_by_test_id("item-detail").inner_text().count("must only be proposed once")
    check("double-clicking Propose correction proposes it once", n == 1, f"{n} corrections")
    b.close()

srv.terminate(); srv.wait(timeout=10)
print(f"FAILURES: {len(FAILS)}")
sys.exit(1 if FAILS else 0)
