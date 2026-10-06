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


def status_jobs(path, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "MDS_DATABASE_URL": f"sqlite:///{path}", "MDS_DATA_DIR": str(tmp_path / "unused")}
    return subprocess.run([sys.executable, "-m", "app.ml.worker", "--status", "--jobs"], env=env,
                          capture_output=True, text=True, timeout=20, check=True).stdout


def test_status_jobs_console_shows_each_job_origin_and_retry_state(current_queue_db, tmp_path):
    seed(current_queue_db)
    record_running_worker(current_queue_db)
    console = status_jobs(current_queue_db, tmp_path)
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


def seed_decisions(path):
    """Findings from team-note and the author's profile, including administrator decisions as the app stores them."""
    from app.ml.runtime import normalize
    from app.ml.sources import finding_key

    with sqlite3.connect(path) as db:
        db.execute("""INSERT INTO ml_sources(kind,id,content_hash,valid,model_version,result,updated_at)
          VALUES ('item','team-note','hash',1,'model','{}',datetime('now'))""")
        for concept_id, term, display in (("c-pump", "citrine pump", "Citrine Pump"), ("c-loop", "cooling loop", "Cooling Loop")):
            db.execute("INSERT INTO concepts(id) VALUES (?)", (concept_id,))
            db.execute("INSERT INTO concept_terms(id,concept_id,term,display,is_canonical) VALUES (?,?,?,?,1)",
                       ("t-" + concept_id, concept_id, term, display))
        relation = lambda predicate: '{"dst_id":"c-loop","predicate":"%s","src_id":"c-pump"}' % predicate
        findings = [  # key, kind, payload, state
            ("f-pump", "concept", '{"name":"Citrine Pump"}', "active"),
            ("f-loop", "concept", '{"name":"loop"}', "held"),
            ("f-drop", "concept", '{"name":"Pump room"}', "suppressed"),
            ("f-stale", "concept", '{"name":"Night shift"}', "stale"),
            ("f-topic", "mention", '{"concept_id":"c-pump","source_id":"team-note","source_kind":"item"}', "active"),
            ("f-uses", "relationship", relation("uses"), "active"),
            ("f-replaces", "relationship", relation("replaces"), "active"),
            ("f-part", "relationship", relation("part_of"), "active"),
            ("f-related", "association", relation("related_to"), "weak"),
            # "Restore automation" removes the pin and sets held until the worker decides again.
            ("f-restored", "concept", '{"name":"Valve log"}', "held"),
            ("f-term", "term", '{"concept_id":"c-pump","term":"citrine pump"}', "active"),
            ("f-secret", "concept", '{"name":"Project Nightingale layoffs"}', "stale"),
            ("f-expert", "expertise", '{"concept_id":"c-pump","profile_id":"author"}', "held"),
        ]
        db.executemany("""INSERT INTO ml_findings(key,kind,payload,state,score,calibrated,policy_version,created_at,updated_at)
          VALUES (?,?,?,?,0,0,'policy',datetime('now'),datetime('now'))""", findings)
        overrides = [  # key, kind, mode, payload: as adapter.fix_relationship / fix_predicate / _override write them
            ("f-uses", "relationship", "pinned", "{}"),
            ("f-drop", "concept", "suppressed", "{}"),
            (finding_key("relationship_pair", "c-loop", "c-pump"), "relationship_pair", "suppressed", "{}"),
            (finding_key("predicate", normalize("part of")), "predicate", "suppressed", '{"type_id":"t","name":"part of"}'),
        ]
        db.executemany("""INSERT INTO ml_overrides(key,kind,mode,payload,username,updated_at)
          VALUES (?,?,?,?,'admin',datetime('now'))""", overrides)
        evidence = [  # finding, source kind, source id, score, features
            ("f-pump", "item", "team-note", .999, '{"text_hash":"t","grounded":true,"label":"named entity","eligibility_margin":0.91}'),
            ("f-loop", "item", "team-note", .97, '{"text_hash":"t","grounded":true,"label":"named entity","eligibility_margin":0.2}'),
            ("f-drop", "item", "team-note", .999, '{"text_hash":"t","grounded":true}'),
            ("f-stale", "item", "team-note", .999, '{"text_hash":"t","grounded":true}'),
            ("f-topic", "item", "team-note", .999, '{"text_hash":"t","grounded":true,"label":"named entity"}'),
            ("f-uses", "item", "team-note", .7, '{"text_hash":"t"}'),
            ("f-replaces", "item", "team-note", .9, '{"text_hash":"t","literal_support":true,"assertion_allowed":true}'),
            ("f-part", "item", "team-note", .9, '{"text_hash":"t","literal_support":true,"assertion_allowed":true}'),
            ("f-related", "item", "team-note", .5, '{"text_hash":"t"}'),
            ("f-restored", "item", "team-note", .999, '{"text_hash":"t","grounded":true,"label":"named entity","eligibility_margin":0.9}'),
            ("f-term", "item", "team-note", 1.0, '{"text_hash":"t"}'),
            ("f-expert", "profile", "author", 0.0, '{"text_hash":"t","actors":["a1"],"originals":1,"accepted_answers":0}'),
            # Made private or deleted: evidence remains until the worker reprocesses the source.
            ("f-secret", "item", "private-note", .999, '{"text_hash":"t"}'),
            ("f-secret", "item", "gone", .999, '{"text_hash":"t"}'),
        ]
        db.executemany("""INSERT INTO ml_evidence(key,finding_key,source_kind,source_id,source_hash,group_key,author_id,
            start,end,raw_score,polarity,features,model_version)
          VALUES (?,?,?,?,'hash','group',NULL,0,1,?,'positive',?,'model')""",
            [(f"e{index}", *row) for index, row in enumerate(evidence)])


def record_finished(path, *sources):
    from app.ml import activity
    from app.ml.queue import Claim

    recorder = activity.Recorder(activity.activity_path(path))
    recorder.lease("worker-token")
    for kind, source_id in sources:
        recorder.start_job(Claim(kind, source_id, 1, "claim", time.time() + 60, "worker-token"))
        recorder.finish_job("done")


def test_finished_jobs_explain_the_current_model_decisions(admin_client, current_queue_db, monkeypatch, tmp_path):
    from app import config

    seed(current_queue_db)
    seed_decisions(current_queue_db)
    record_finished(current_queue_db, ("item", "private-note"), ("item", "gone"), ("profile", "author"), ("item", "team-note"))
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{current_queue_db}")

    response = admin_client.get("/api/ml/queue")
    recent = {entry["id"]: entry["decisions"] for entry in response.json()["recent"]}
    assert recent["private-note"] == recent["gone"] == [] and "Nightingale" not in response.text
    assert [(d["headline"], d["name"], d["why"]) for d in recent["author"]] == [
        ("Expertise not recognised yet", "Citrine Pump", "Needs confirmation from 2 people (has 1) across 3 separate "
                                                         "posts (has 1), including 1 accepted answer (has 0).")]
    assert [(d["headline"], d["name"], d["why"]) for d in recent["team-note"]] == [
        ("Concept confirmed by the model", "Citrine Pump", "The model is 99.9% sure this is a named thing."),
        ("Concept waiting to be checked again", "Night shift",
         "A post supporting it, or the meaning of one of its names, changed; the worker will check it again."),
        ("Removed by an administrator", "Pump room", "An administrator removed it."),
        ("Concept not confirmed by the model", "Valve log",
         "Not yet re-checked under the current evidence and decisions; the worker will decide again."),
        ("Concept not confirmed by the model", "loop",
         "It does not look like a subject of the post (relevance 0.200; needs at least 0.643)."),
        ("Topic confirmed by the model", "Citrine Pump", "The model is 99.9% sure the post mentions it."),
        ("Removed by an administrator", "Citrine Pump part of Cooling Loop", "An administrator removed this relationship type."),
        ("Link removed by an administrator", "Citrine Pump replaces Cooling Loop", "An administrator removed this link."),
        ("Added by an administrator", "Citrine Pump uses Cooling Loop", "An administrator added it by hand."),
        ("Link removed by an administrator", "Citrine Pump related to Cooling Loop", "An administrator removed this link.")]

    console = status_jobs(current_queue_db, tmp_path)
    for line in ("Concept confirmed by the model: Citrine Pump — The model is 99.9% sure this is a named thing.",
                 "Concept not confirmed by the model: loop — It does not look like a subject of the post (relevance 0.200; needs at least 0.643).",
                 "Link removed by an administrator: Citrine Pump replaces Cooling Loop — An administrator removed this link.",
                 "no findings from this source"):
        assert line in console
    assert "Nightingale" not in console
    # Explaining decisions loads the decision rules, never the ML libraries.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
           "MDS_DATABASE_URL": f"sqlite:///{current_queue_db}", "MDS_DATA_DIR": str(tmp_path / "unused")}
    loaded = subprocess.run([sys.executable, "-c", """
import sys
from app.ml.worker import main
assert main(["--status", "--jobs"]) == 0
print(sorted(name for name in ("torch", "spacy", "gliner2", "transformers", "sentence_transformers", "psutil") if name in sys.modules))
"""], env=env, capture_output=True, text=True, timeout=30, check=True).stdout.splitlines()[-1]
    assert loaded == "[]"


def test_one_report_reads_one_database_snapshot(current_queue_db, monkeypatch):
    from app.ml import activity

    seed(current_queue_db)
    seed_decisions(current_queue_db)
    record_finished(current_queue_db, ("item", "team-note"), ("item", "team-note"))
    with sqlite3.connect(current_queue_db) as db:
        db.execute("PRAGMA journal_mode=WAL")
    original = activity.decisions

    def decisions_then_concurrent_write(db, session, kind, source_id):
        result = original(db, session, kind, source_id)
        with sqlite3.connect(current_queue_db) as writer:  # the worker commits mid-report
            writer.execute("UPDATE ml_findings SET state='held' WHERE key='f-pump'")
        return result

    monkeypatch.setattr(activity, "decisions", decisions_then_concurrent_write)
    first, second = (entry["decisions"] for entry in activity.report(current_queue_db)["recent"])
    assert first == second and first[0]["headline"] == "Concept confirmed by the model"
