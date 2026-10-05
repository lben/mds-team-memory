"""The admin ML queue report: access, processing order, live activity and privacy."""

import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

from test_ml_queue import current_queue_db, current_queue_template, queue_template  # noqa: F401


def seed(path):
    now = time.time()
    with sqlite3.connect(path) as db:
        for item_id, body, visibility in (("team-note", "Citrine Pump feeds the cooling loop.", "team"),
                                          ("private-note", "Private salary details.", "private")):
            db.execute("""INSERT INTO knowledge_items(id,kind,body,visibility,author_profile_id,created_at,updated_at)
              VALUES (?,'note',?,?,'author',datetime('now'),datetime('now'))""", (item_id, body, visibility))
        db.execute("INSERT INTO document_passages(id,document_id,ord,text,locator) VALUES ('p1','doc',0,'Valve schedule text.','Page 1')")
        db.execute("DELETE FROM ml_jobs")
        jobs = [  # kind, id, priority, available offset, created offset, attempts, error, lease
            ("item", "team-note", 0, -50, -50, 1, "RuntimeError: earlier failure", "claim"),
            ("passage", "p1", 0, -40, -40, 0, None, None),
            ("item", "gone", 0, -30, -30, 0, None, None),
            ("item", "private-note", 10, -100, -100, 0, None, None),
            ("profile", "author", 0, 120, -200, 2, "TimeoutError: Inference exceeded its deadline", None),
            ("vocabulary", "all", 0, -5, -5, 0, None, None),
        ]
        db.executemany("""INSERT INTO ml_jobs(source_kind,source_id,priority,available_at,created_at,updated_at,
            attempts,error,lease_token,lease_until) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [(kind, sid, priority, now + available, now + created, now, attempts, error, lease, now + 60 if lease else None)
             for kind, sid, priority, available, created, attempts, error, lease in jobs])
        db.execute("UPDATE ml_state SET worker_token='worker-token', worker_lease_until=?, status='running' WHERE id=1",
                   (now + 60,))


def record_running_worker(path):
    from app.ml import activity
    from app.ml.queue import Claim

    recorder = activity.Recorder(activity.activity_path(path))
    recorder.lease("worker-token")
    recorder.start_job(Claim("passage", "p1", 1, "earlier", time.time() + 60, "worker-token"))
    recorder.finish_job("failed", "RuntimeError: earlier failure")
    recorder.start_job(Claim("item", "team-note", 1, "claim", time.time() + 60, "worker-token"))
    recorder.stage("running inference")
    recorder.progress({"step": "extracting entities", "window": 2, "windows": 5})


def test_queue_report_is_admin_only_ordered_live_and_private(make_client, admin_client, current_queue_db, monkeypatch):
    from app import config

    seed(current_queue_db)
    record_running_worker(current_queue_db)
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{current_queue_db}")

    assert make_client().get("/api/ml/queue").status_code == 401
    response = admin_client.get("/api/ml/queue")
    assert response.status_code == 200 and response.headers["content-type"] == "application/json"
    report = response.json()

    assert report["worker"]["running"] and report["activity"]["live"]
    current = report["current"]
    assert (current["kind"], current["id"], current["attempt"]) == ("item", "team-note", 2)
    assert current["stage"] == "running inference"
    assert current["progress"] == {"step": "extracting entities", "window": 2, "windows": 5}
    assert current["label"] == "Note: Citrine Pump feeds the cooling loop."

    assert [(job["id"], job["state"]) for job in report["jobs"]] == [
        ("team-note", "processing"), ("p1", "waiting"), ("gone", "waiting"),
        ("all", "waiting"), ("private-note", "waiting"), ("author", "retrying")]
    labels = {job["id"]: job["label"] for job in report["jobs"]}
    assert labels["p1"] == "file.txt · Page 1: Valve schedule text."
    assert labels["gone"] == "Deleted contribution (withdrawing its findings)"
    assert labels["private-note"] == "Private contribution (withdrawing its findings)"
    assert labels["author"] == "Expertise of Browser profile AUTH"
    assert "salary" not in response.text
    retrying = report["jobs"][5]
    assert retrying["attempts"] == 2 and retrying["error"] == "TimeoutError: Inference exceeded its deadline"
    assert report["jobs"][4]["origin"] == "reprocessing older content"
    assert {key: report["totals"][key] for key in ("total", "processing", "waiting", "retrying", "reprocessing")} == {
        "total": 6, "processing": 1, "waiting": 4, "retrying": 1, "reprocessing": 1}
    assert report["totals"]["by_kind"] == {"item": 3, "passage": 1, "profile": 1, "vocabulary": 1}
    assert [(entry["id"], entry["outcome"], entry["error"]) for entry in report["recent"]] == [
        ("p1", "failed", "RuntimeError: earlier failure")]

    page = admin_client.get("/api/ml/queue?offset=2&limit=2").json()
    assert [job["id"] for job in page["jobs"]] == ["gone", "all"]

    # A file left by an earlier lease is not presented as the current work.
    with sqlite3.connect(current_queue_db) as db:
        db.execute("UPDATE ml_state SET worker_token='replacement-token' WHERE id=1")
    before = current_queue_db.read_bytes()
    stale = admin_client.get("/api/ml/queue").json()
    assert stale["worker"]["running"] and not stale["activity"]["live"] and stale["current"] is None
    assert current_queue_db.read_bytes() == before


def test_status_jobs_console_shows_each_job_origin_and_retry_state(current_queue_db, tmp_path):
    seed(current_queue_db)
    record_running_worker(current_queue_db)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "MDS_DATABASE_URL": f"sqlite:///{current_queue_db}", "MDS_DATA_DIR": str(tmp_path / "unused")}
    console = subprocess.run([sys.executable, "-m", "app.ml.worker", "--status", "--jobs"], env=env,
                             capture_output=True, text=True, timeout=20, check=True).stdout
    rows = {line.split()[0]: line for line in console.splitlines() if re.match(r"\s+\d+\s", line)}
    assert "processing" in rows["1"] and "new or changed content" in rows["1"] and "Citrine Pump" in rows["1"]
    assert "reprocessing older content" in rows["5"] and "Private contribution" in rows["5"]
    assert "retrying" in rows["6"] and "new or changed content" in rows["6"]
    assert "previous attempt failed; last error: RuntimeError: earlier failure" in console
    assert re.search(r"next try in \S+; last error: TimeoutError: Inference exceeded its deadline", console)
    assert "Stage: running inference — window 2 of 5: extracting entities" in console
    assert "salary" not in console


def test_activity_write_failures_never_interrupt_work(tmp_path, capsys):
    from app.ml import activity
    from app.ml.queue import Claim

    recorder = activity.Recorder(tmp_path / "missing-directory" / "mds.sqlite3.ml-activity.json")
    recorder.lease("worker-token")
    recorder.start_job(Claim("item", "team-note", 1, "claim", time.time() + 60, "worker-token"))
    recorder.stage("running inference")
    recorder.progress({"step": "extracting entities", "window": 1, "windows": 1})
    recorder.finish_job("done")
    recorder.stopped()
    assert capsys.readouterr().err.count("ML activity is not being reported") == 1
