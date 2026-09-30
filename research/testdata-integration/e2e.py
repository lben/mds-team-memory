"""Disposable localhost-only proof. No real UAT/PROD configuration is read."""
import http.cookiejar
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path.cwd()
OUT = ROOT / "build/testdata"
sys.path.insert(0, str(ROOT / "tools"))
import update
import update_session
update_session.getpass.getpass = lambda _: "isolated-update-test"
target, known = update.configured_target("uat", OUT / "test.toml")
assert target.host == "mds@127.0.0.1" and target.ssh_port == 22296
assert target.root == "/home/mds/mds-uat"
ssh = update_session.Session(target, known)
base = "http://127.0.0.1:28096/api/"
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
trace = []


def api(method, path, payload=None, form=False):
    body = None if payload is None else (urllib.parse.urlencode(payload).encode() if form else json.dumps(payload).encode())
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={} if form else {"Content-Type": "application/json"})
    with client.open(req, timeout=60) as response:
        trace.append({"method": method, "path": path, "status": response.status})
        return json.load(response)


def remote_code(code):
    script = "set -eu; set -a; . /home/mds/mds-uat/app.env; set +a; cd /home/mds/mds-uat/current; PYTHONPATH=backend .venv/bin/python -c " + shlex.quote(code)
    result = ssh.run("bash -c " + shlex.quote(script), capture=True)
    if result.returncode:
        raise RuntimeError(result.stderr + result.stdout)
    return result.stdout


def json_outputs(text):
    found = []
    decoder = json.JSONDecoder()
    lines = text.splitlines(keepends=True)
    cursor = 0
    for line in lines:
        if line.startswith(("{", "[")):
            try:
                obj, _ = decoder.raw_decode(text[cursor:])
                found.append(obj)
            except ValueError:
                pass
        cursor += len(line)
    return found


commands = []
def cli(label, args, expected=0):
    command = ["uv", "run", "--offline", "--python", "3.12", "--with", "paramiko==4.0.0", "--no-project",
               str(OUT / "run_client.py"), *args]
    if args[0].lower() != "prod" and args[-1] != "datasets" and "preview" not in args:
        command += ["--config", str(OUT / "test.toml")]
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, timeout=1200)
    (OUT / f"{label}.log").write_text(result.stdout + result.stderr)
    local = "datasets" in args or "preview" in args or args[0].lower() == "prod"
    assert result.returncode == expected, (label, result.stdout, result.stderr)
    assert f"PASSWORD_PROMPT_COUNT {0 if local else 1}" in result.stdout
    commands.append({"label": label, "arguments": args, "exit": result.returncode,
                     "password_prompts": 0 if local else 1, "seconds": time.monotonic() - started})
    print("CLI", label, "PASS", flush=True)
    return json_outputs(result.stdout)


try:
    remote_code("from app.cli import create_admin; import app.cli as c; c._read_password=lambda:'isolated-admin-test'; create_admin('testdata-admin')")
    api("POST", "auth/login", {"username": "testdata-admin", "password": "isolated-admin-test"})
    manual = api("POST", "admin/concepts", {"name": "UAT Preservation Marker", "aliases": ["UPM"]})
    marker = api("POST", "capture", {"body": "UAT Preservation Marker must survive every TestData cleanup."}, form=True)["item"]
    cli("datasets", ["datasets"])
    preview = cli("preview", ["UAT", "preview", "--dataset", "expanded", "--percent", "25", "--seed", "42"])[0]
    same = cli("preview-repeat", ["preview", "--dataset", "expanded", "--percent", "25.0", "--seed", "42"])[0]
    assert preview == same and preview["posts"] == 378
    cli("prod-client-refusal", ["PROD", "batches"], expected=1)
    # Independently check the server-side guard, including a misdirected UAT client.
    original = ssh.sftp.open("/home/mds/mds-uat/app.env").read()
    try:
        with ssh.sftp.open("/home/mds/mds-uat/app.env", "w") as f:
            f.write(original.replace(b"MDS_ENVIRONMENT=uat", b"MDS_ENVIRONMENT=prod"))
        cli("prod-server-refusal", ["UAT", "batches"], expected=1)
    finally:
        with ssh.sftp.open("/home/mds/mds-uat/app.env", "w") as f:
            f.write(original)
    batch = cli("add-astronomy", ["UAT", "add", "--dataset", "cross-domain", "--topics", "astronomy", "--percent", "100", "--seed", "42"])[-1]["id"]
    cli("duplicate-refusal", ["add", "--dataset", "cross-domain", "--topics", "astronomy", "--percent", "100", "--seed", "42"], expected=1)
    done = cli("wait-astronomy", ["status", "--batch", batch, "--wait", "--timeout", "900"])[-1]
    assert done["processing"] == "complete" and done["embeddings"] > 0
    search = api("GET", "search?q=HST")
    assert any(c["name"] == "Hubble Space Telescope" for c in search["concepts"]), search
    graph = api("GET", "graph/global?show_weak=true")
    assert any(f["kind"] == "relationship" and f["state"] == "active" for f in done["findings"])
    question = json.loads(remote_code("import json; from app.db import SessionLocal; from sqlalchemy import text;\nwith SessionLocal() as db:\n print(json.dumps(db.execute(text(\"SELECT i.id FROM knowledge_items i JOIN testdata_objects o ON o.id=i.id WHERE o.batch_id=:batch AND o.kind='item' AND i.kind='question' LIMIT 1\"),{'batch':" + repr(batch) + "}).scalar()))"))
    answer = api("POST", f"questions/{question}/answers", {"body": "Human answer: preserve this real contribution during test cleanup."})
    cli("human-answer-refusal", ["remove", "--batch", batch, "--timeout", "900"], expected=1)
    assert api("GET", "items/" + answer["id"])["body"] == answer["body"]
    api("DELETE", "items/" + answer["id"])
    removed = cli("remove-astronomy", ["remove", "--batch", batch, "--timeout", "900"])[-1][0]
    assert removed["state"] == "removed" and removed["embeddings"] == 0 and removed["evidence_rows"] == 0
    assert not any(c["name"] == "Hubble Space Telescope" for c in api("GET", "search?q=HST")["concepts"])
    repeat = cli("remove-repeat", ["remove", "--batch", batch, "--timeout", "30"])[-1][0]
    assert repeat["state"] == "removed"
    reimport = cli("reimport-removed-selection", ["add", "--dataset", "cross-domain", "--topics", "astronomy", "--percent", "20", "--seed", "42"])[-1]
    assert reimport["id"] != batch and reimport["remaining_posts"] == 3
    cli("remove-reimport", ["remove", "--batch", reimport["id"], "--timeout", "900"])
    expanded = cli("add-expanded", ["add", "--dataset", "expanded", "--percent", "1", "--seed", "42"])[-1]
    cli("wait-expanded", ["status", "--batch", expanded["id"], "--wait", "--timeout", "900"])
    capacity = cli("add-capacity", ["add", "--dataset", "capacity", "--percent", "0.01", "--seed", "42"])[-1]
    assert capacity["remaining_posts"] == 5
    cli("batches", ["batches"])
    all_removed = cli("remove-all", ["remove", "--all-batches", "--timeout", "900"])[-1]
    assert len(all_removed) == 2 and all(b["state"] == "removed" for b in all_removed)
    assert api("GET", "items/" + marker["id"])["body"] == marker["body"]
    assert manual in api("GET", "admin/concepts")
    integrity = json.loads(remote_code("import json; from app.db import SessionLocal; from sqlalchemy import text;\nwith SessionLocal() as db:\n print(json.dumps({'check':db.execute(text('PRAGMA quick_check')).scalar(),'foreign_keys':[list(r) for r in db.execute(text('PRAGMA foreign_key_check'))],'test_objects_remaining':db.execute(text(\"SELECT count(*) FROM testdata_objects o WHERE (o.kind='item' AND EXISTS(SELECT 1 FROM knowledge_items i WHERE i.id=o.id)) OR (o.kind='account' AND EXISTS(SELECT 1 FROM accounts a WHERE a.id=o.id)) OR (o.kind='profile' AND EXISTS(SELECT 1 FROM profiles p WHERE p.id=o.id)) OR (o.kind='scratchpad' AND EXISTS(SELECT 1 FROM scratchpads s WHERE s.id=o.id))\")).scalar()}))"))
    assert integrity == {"check": "ok", "foreign_keys": [], "test_objects_remaining": 0}, integrity
    result = {"status": "PASS", "commands": commands, "api_trace": trace,
              "astronomy_processed": done, "published_search": search, "graph": graph,
              "expanded_import": expanded, "capacity_import": capacity, "integrity": integrity,
              "preserved": {"post_id": marker["id"], "concept_id": manual["id"], "account": "testdata-admin"}}
    (OUT / "e2e.json").write_text(json.dumps(result, indent=2) + "\n")
    print("TESTDATA_E2E_PASS", flush=True)
finally:
    ssh.close()
