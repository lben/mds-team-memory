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
    old=json_outputs((OUT/"final-batches-after-update.log").read_text())[-1]
    assert len(old)==4 and all(b["state"]=="removed" for b in old)
    original=json_outputs((OUT/"final-wait-workshop.log").read_text())[-1]
    done=cli("final-workshop-current-status", ["status","--batch",original["id"]])[-1]
    assert done["processing"]=="complete" and done["embeddings"]>0
    relationship_published=any(f["kind"]=="relationship" and f["state"]=="active" for f in done["findings"])
    removed=cli("final-remove-workshop", ["remove","--batch",done["id"],"--timeout","900"])[-1][0]
    assert removed["state"]=="removed" and removed["embeddings"]==0 and removed["evidence_rows"]==0
    first=json.loads((OUT/"e2e.json").read_text())
    assert api("GET","items/"+first["preserved"]["post_id"])["body"]=="UAT Preservation Marker must survive every TestData cleanup."
    with urllib.request.urlopen("http://127.0.0.1:28096/", timeout=30) as r:
        assert r.status==200 and b"/assets/" in r.read()
    (OUT/"final-live.json").write_text(json.dumps({"cli_status":"PASS","commands":commands,"receipts_survived_update":old,"workshop_processed":done,"workshop_removed":removed,"model_expectation":{"historical_fixture_expects_relationship":True,"relationship_published":relationship_published,"verdict":"PASS" if relationship_published else "FAIL"}},indent=2)+"\n")
    print("FINAL_TESTDATA_CLI_PASS; WORKSHOP_MODEL_EXPECTATION="+("PASS" if relationship_published else "FAIL"),flush=True)
finally:
    ssh.close()
