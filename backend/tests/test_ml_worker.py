"""Actual process lifecycle checks; model accuracy is evaluated separately."""

import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from app.ml.worker import InferenceProcess, Stopped, process_tree_rss
from test_ml_queue import current_queue_db as queue_db, current_queue_template, queue_template

ROOT = Path(__file__).resolve().parents[2]
requires_ml = pytest.mark.skipif(importlib.util.find_spec("psutil") is None,
                               reason="Process supervision checks require the ML environment")


def echo_child(connection, assets):
    time.sleep(0.75)
    connection.send(("ready", "model", "embeddings", 1024))
    while True:
        body = connection.recv()
        connection.send(("result", {"body": body, "pid": os.getpid()}))


def failed_child(connection, assets):
    raise ValueError("Test loader failure")


def descendant_child(connection, assets):
    process = subprocess.Popen([sys.executable, "-c", """
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
allocation = bytearray(256 * 1024**2)
time.sleep(60)
"""])
    Path(assets).write_text(str(process.pid))
    time.sleep(60)


def hanging_child(connection, assets):
    directory = Path(assets)
    if directory.name != "loading":
        connection.send(("ready", "model", "embeddings", 1024))
        connection.recv()
    descendant = subprocess.Popen([sys.executable, "-c", """
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""])
    if directory.name == "partial":
        # A readable pipe is not necessarily a complete result. recv() blocks
        # after this header unless supervision can still enforce its deadline.
        import struct
        os.write(connection.fileno(), struct.pack("!i", 1000) + b"x")
    (directory / "ready.json").write_text(json.dumps([os.getpid(), descendant.pid]))
    time.sleep(60)


def shared_bge_child(connection, assets):
    """Synthetic extraction + relevance in one actual supervised process."""
    from app.ml import eligibility, relation_syntax, syntax
    from app.ml.runtime import inference_version, normalize
    from ml_relation_helpers import analyze as _analyze, span as _span

    models = json.loads((Path(assets) / "models.json").read_text())["models"]
    connection.send(("ready", inference_version(models), models["embeddings"]["revision"], 1024))
    while True:
        request = connection.recv()
        body = request["text"] if isinstance(request, dict) else request
        with (Path(assets) / "bge-requests.jsonl").open("a") as log:
            log.write(json.dumps({"text": body, "pid": os.getpid(), "eligibility_only": isinstance(request, dict)}) + "\n")
        if "Fragile" in body:
            raise RuntimeError("Test relevance failure")
        names = request["candidates"] if isinstance(request, dict) else [
            [name, body.index(name)] for name in ("Citrine Pump", "Emerald Cell", "Sturdy Pump") if name in body]
        judgment = {"version": eligibility.version(models), "margins": {
            normalize(name): .3 if "Cell" in name else .99 for name, _ in names}}
        if isinstance(request, dict):
            result = judgment
        else:
            result = _analyze(body, [_span(body, name, score=.999) for name, _ in names], {}, full=True)
            result.update(chunks=[], conflict_definitions=[], conflict_coverage_revision=syntax.CONFLICT_REVISION,
                          relation_guard_revision=relation_syntax.REVISION, eligibility=judgment)
        connection.send(("result", result))


def progress_child(connection, assets):
    """Reports inference steps, then waits for a release file so the job can be watched."""
    from app.ml import eligibility, relation_syntax, syntax
    from app.ml.runtime import inference_version
    from ml_relation_helpers import analyze as _analyze, span as _span

    directory = Path(assets)
    models = json.loads((directory / "models.json").read_text())["models"]
    connection.send(("ready", inference_version(models), models["embeddings"]["revision"], 1024))
    while True:
        body = connection.recv()
        judgment = {"version": eligibility.version(models), "margins": {"citrine pump": 0.91}}
        if isinstance(body, dict):
            connection.send(("result", judgment))
            continue
        for window in (1, 2):
            connection.send(("progress", {"step": "extracting relations", "window": window, "windows": 2}))
        while not (directory / "release").exists():
            time.sleep(0.05)
        spans = [_span(body, "Citrine Pump", score=0.999)] if "Citrine Pump" in body else []
        result = _analyze(body, spans, {}, full=True)
        result.update(chunks=[], conflict_definitions=[], conflict_coverage_revision=syntax.CONFLICT_REVISION,
                      relation_guard_revision=relation_syntax.REVISION, eligibility=judgment)
        connection.send(("result", result))


def stand_in_models_child(connection, assets):
    """The production child loop over stand-in models that report steps like LocalModels."""
    from app.ml import runtime, worker

    class Models:
        version, embedding_version, dimensions = "model", "embeddings", 1024

        def __init__(self, directory):
            pass

        def analyze(self, text, progress=None):
            for window in (1, 2):
                progress({"step": "extracting entities", "window": window, "windows": 2})
                time.sleep(0.6)  # longer than one supervision poll, so each note is relayed
            return {"text": text}

    runtime.LocalModels = Models
    worker._model_loop(connection, assets)


def process_ended(pid):
    import psutil

    try:
        return not psutil.Process(pid).is_running() or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


@requires_ml
def test_child_models_are_reused_and_loading_does_not_block_heartbeat(tmp_path):
    child = InferenceProcess(tmp_path, target=echo_child)
    ticks = []
    try:
        first, metadata = child.analyze("first", lambda: ticks.append(time.monotonic()))
        second, _ = child.analyze("second", lambda: None)
        assert first["body"] == "first" and second["body"] == "second"
        assert first["pid"] == second["pid"] != os.getpid()
        assert metadata == ("model", "embeddings", 1024)
        assert len(ticks) >= 3
    finally:
        pid = child.process.pid if child.process else None
        child.close()
    assert pid is not None and process_ended(pid)


@requires_ml
def test_child_inference_steps_reach_the_supervisor(tmp_path):
    child = InferenceProcess(tmp_path, target=stand_in_models_child)
    notes = []
    try:
        result, metadata = child.analyze("body", lambda: None, notes.append)
    finally:
        child.close()
    assert result == {"text": "body"} and metadata == ("model", "embeddings", 1024)
    assert notes[0] == {"step": "loading models"}
    assert {"step": "extracting entities", "window": 1, "windows": 2} in notes
    assert notes[-1] == {"step": "extracting entities", "window": 2, "windows": 2}


@requires_ml
def test_model_load_failure_closes_the_child(tmp_path):
    child = InferenceProcess(tmp_path, target=failed_child)
    with pytest.raises(RuntimeError, match="Test loader failure"):
        child.analyze("body", lambda: None)
    assert child.process is None


@requires_ml
@pytest.mark.parametrize("phase", ["loading", "inference", "partial"])
def test_inference_deadline_keeps_heartbeats_kills_descendants_and_can_retry(tmp_path, monkeypatch, phase):
    from app.ml import worker

    directory = tmp_path / phase
    directory.mkdir()
    marker = directory / "ready.json"
    ticks = []
    marker_ticks = 0
    expired = False

    def heartbeat():
        nonlocal marker_ticks, expired
        ticks.append(time.monotonic())
        if marker.exists():
            marker_ticks += 1
            expired = marker_ticks >= 2

    # Wait for actual child/descendant startup before advancing the clock.
    # This tests finite monotonic deadlines without short real-time deadlines
    # that flake when Linux process startup is emulated or under load.
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + (60 if expired else 0)))
    child = InferenceProcess(directory, target=hanging_child, load_timeout=30, job_timeout=30)
    with pytest.raises(TimeoutError, match="Model loading" if phase == "loading" else "Inference"):
        child.analyze("body", heartbeat)
    assert len(ticks) >= 2 and marker_ticks >= 2
    assert child.process is child.connection is child.metadata is child.exchange_thread is None
    assert all(process_ended(pid) for pid in json.loads(marker.read_text()))

    child.target = echo_child
    try:
        result, _ = child.analyze("retry", lambda: None)
        assert result["body"] == "retry"
    finally:
        child.close()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
@pytest.mark.parametrize("option", ["load_timeout", "job_timeout"])
def test_inference_deadlines_must_be_finite_and_positive(value, option):
    with pytest.raises(ValueError, match="finite positive"):
        InferenceProcess("unused", **{option: value})


@pytest.mark.parametrize("variable", ["MDS_ML_LOAD_TIMEOUT_SECONDS", "MDS_ML_JOB_TIMEOUT_SECONDS"])
def test_invalid_deadline_environment_is_rejected_before_start(monkeypatch, variable):
    from app.ml.worker import main

    monkeypatch.setenv(variable, "nan")
    with pytest.raises(SystemExit) as error:
        main(["--status"])
    assert error.value.code == 2


@requires_ml
def test_cancel_during_loading_terminates_the_complete_child_group(tmp_path):
    pid_file = tmp_path / "descendant.pid"
    child = InferenceProcess(pid_file, target=descendant_child)

    def cancel_when_started():
        if pid_file.exists():
            raise Stopped("Stopped during loading")

    with pytest.raises(Stopped):
        child.analyze("body", cancel_when_started)
    assert child.process is None
    assert process_ended(int(pid_file.read_text()))


@requires_ml
def test_memory_limit_counts_and_terminates_descendants(tmp_path):
    pid_file = tmp_path / "descendant.pid"
    limit = process_tree_rss() + 192 * 1024**2
    child = InferenceProcess(pid_file, target=descendant_child, limit=limit)
    with pytest.raises(MemoryError, match="RSS"):
        child.analyze("body", lambda: None)
    assert child.process is None
    assert process_ended(int(pid_file.read_text()))


@requires_ml
@pytest.mark.skipif(sys.platform != "linux", reason="Deployment uses Linux parent-death signaling")
def test_supervisor_crash_terminates_inference_child(tmp_path):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "backend"), str(ROOT / "backend/tests")))}
    process = subprocess.Popen([sys.executable, "-c", """
import time
from app.ml.worker import InferenceProcess
from test_ml_worker import echo_child
child = InferenceProcess('.', target=echo_child)
result, _ = child.analyze('body', lambda: None)
print(result['pid'], flush=True)
time.sleep(60)
"""], env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 20)[0]
        child_pid = int(process.stdout.readline())
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        import psutil

        try:
            psutil.wait_procs([psutil.Process(child_pid)], timeout=3)
        except psutil.NoSuchProcess:
            pass
        assert process_ended(child_pid)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def test_status_uses_configured_database_readonly_without_ml_imports(tmp_path):
    path = tmp_path / "actual.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE ml_state (
          id INTEGER PRIMARY KEY, revision INTEGER, status TEXT,
          worker_lease_until REAL, backfill_kind TEXT)""")
        connection.execute("INSERT INTO ml_state VALUES (1,42,'idle',NULL,'item')")
        connection.execute("CREATE TABLE ml_jobs(created_at REAL, error TEXT)")
        connection.execute("INSERT INTO ml_jobs VALUES (100,NULL)")
    before = path.read_bytes()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"),
           "MDS_DATABASE_URL": f"sqlite:///{path}", "MDS_DATA_DIR": str(tmp_path / "unused")}
    result = subprocess.run([sys.executable, "-c", """
import json, sys
from app.ml.worker import main
assert main(['--status']) == 0
print(json.dumps([name for name in ('torch', 'gliner2', 'sentence_transformers', 'psutil') if name in sys.modules]))
"""], env=env, capture_output=True, text=True, timeout=20, check=True)
    state, imports = [json.loads(line) for line in result.stdout.splitlines()]
    assert state["database"] == str(path) and state["revision"] == 42
    assert state["pending"] == 1 and not state["worker_active"]
    assert imports == []
    assert path.read_bytes() == before and not (tmp_path / "unused").exists()


@pytest.fixture
def worker_store(app_modules, queue_db, tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.ml import embeddings, queue, worker

    if not queue.sqlite_is_safe(sqlite3.sqlite_version_info):
        pytest.skip("Worker requires the SQLite WAL-reset fix")
    pytest.importorskip("psutil")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "models.json").write_text(json.dumps({"models": {
        "extractor": {"revision": "extractor"}, "embeddings": {"revision": "old"},
        "syntax": {"revision": "syntax"}}}))
    monkeypatch.setattr(worker, "lower_priority", lambda: None)
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: 20 * 1024**3)
    engine = create_engine(f"sqlite:///{queue_db}")
    with Session(engine) as db:
        yield db, queue_db, assets
    engine.dispose()


def run_once(path, assets):
    import threading
    from app.ml.worker import Supervisor

    supervisor = Supervisor(path, assets, threading.Event())
    try:
        return supervisor.run("once"), dict(supervisor.lock_retries)
    finally:
        supervisor.close()


def charged_state(db):
    from sqlalchemy import text

    return {table: db.execute(text(f"SELECT * FROM {table} ORDER BY 1,2")).all()
            for table in ("ml_budget", "ml_sources", "ml_findings", "ml_evidence", "ml_embeddings")}


def test_worker_ignores_old_fitted_assets_and_clears_withdrawn_scores(worker_store):
    from sqlalchemy import text
    from app.ml import adapter, policy, queue
    from app.ml.runtime import inference_version
    from app.ml.models import Finding
    from app.models import utcnow

    db, path, assets = worker_store
    (assets / "calibration.json").write_text("obsolete fitted policy")
    adapter.bootstrap(db)
    db.add(Finding(key="old-expertise", kind="expertise", payload=json.dumps({
        "profile_id": "author", "concept_id": "removed"}), state="held", score=0.9,
        calibrated=True, policy_version="obsolete-fitted", created_at=utcnow(), updated_at=utcnow()))
    version = inference_version(json.loads((assets / "models.json").read_text())["models"]) + ":" + policy.VERSION
    db.execute(text("UPDATE ml_state SET pipeline_version=:version,decision_policy=:policy WHERE id=1"),
               {"version": version, "policy": '{"models": {"expertise": {}}}'})
    queue.enqueue(db, "profile", "author", priority=-1)
    db.commit()

    assert run_once(path, assets)[0] == 0
    assert db.execute(text("SELECT pipeline_version,decision_policy FROM ml_state WHERE id=1")).one() == (version, "{}")
    row = db.get(Finding, "old-expertise")
    assert (row.state, row.score, row.calibrated, row.policy_version) == ("withdrawn", 0.0, False, policy.VERSION)


def test_policy_upgrade_reselects_literal_evidence_from_cache(worker_store, monkeypatch):
    import threading
    from sqlalchemy import text
    from app.ml import adapter, policy, syntax, worker
    from app.ml.models import Evidence, Finding
    from app.ml.runtime import inference_version
    from test_ml_embeddings import add_source
    from ml_relation_helpers import analyze as _analyze, span as _span, DeclaredGuard, declaration

    db, path, assets = worker_store
    body = "Citrine Pump uses Emerald Cell. Citrine Pump was inspected alongside Emerald Cell."
    source = add_source(db, "mixed-source", body=body)
    names = "Citrine Pump", "Emerald Cell"
    first = [_span(body, name, score=.76) for name in names]
    last = [_span(body, name, start=body.rindex(name), score=.99) for name in names]
    result = _analyze(body, [_span(body, name, score=.996) for name in names],
                      {"uses": [{"head": h, "tail": t} for h, t in (first, last)]}, full=True,
                      guard=DeclaredGuard(body, [declaration(body, names[0], "uses", names[1],
                                                            end=body.index(".") + 1)]))
    result.update(chunks=[], conflict_definitions=[], conflict_coverage_revision=syntax.CONFLICT_REVISION)
    models = json.loads((assets / "models.json").read_text())["models"]
    version = inference_version(models)
    from app.ml import eligibility
    result["eligibility"]["version"] = eligibility.version(models)
    adapter.bootstrap(db)
    adapter.apply_source(db, "item", source.id, source, result, version, "old", 1024)
    stored = (db.query(Evidence).join(Finding, Finding.key == Evidence.finding_key)
              .filter(Finding.kind == "relationship", Evidence.source_id == source.id,
                      Evidence.polarity == "positive").one())
    finding = db.get(Finding, stored.finding_key)
    # Reproduce the old committed selection; the complete raw cache still
    # contains the lower-scoring literal extraction needed to repair it.
    unsupported = next(r for r in result["relations"] if not r["literal_support"])
    stored.raw_score, stored.start, stored.end = unsupported["score"], unsupported["start"], unsupported["end"]
    stored.features = json.dumps({**json.loads(stored.features), "literal_support": False,
                                 "head": unsupported["head"], "tail": unsupported["tail"]})
    finding.policy_version = "grounded-cold-start-v5"
    key = stored.key
    db.execute(text("DELETE FROM ml_jobs"))
    db.execute(text("UPDATE ml_state SET backfill_kind=NULL,pipeline_version=:version WHERE id=1"),
               {"version": version + ":grounded-cold-start-v5"})
    generation = db.execute(text("SELECT backfill_generation FROM ml_state WHERE id=1")).scalar_one()
    db.commit()

    def unexpected_inference(*args):
        pytest.fail("A policy-only replay must reuse the complete current model cache")

    monkeypatch.setattr(worker.InferenceProcess, "analyze", unexpected_inference)
    supervisor = worker.Supervisor(path, assets, threading.Event())
    try:
        assert supervisor.run("drain") == 0
    finally:
        supervisor.close()
    db.expire_all()
    restored = db.get(Evidence, key)
    assert restored.raw_score == .76 and json.loads(restored.features)["literal_support"]
    assert body[restored.start:restored.end] == "Citrine Pump uses Emerald Cell."
    assert db.get(Finding, restored.finding_key).policy_version == policy.VERSION
    assert db.execute(text("SELECT pipeline_version,backfill_kind,backfill_generation FROM ml_state WHERE id=1")).one() == (
        version + ":" + policy.VERSION, None, generation + 1)
    assert db.execute(text("SELECT count(*) FROM ml_jobs")).scalar_one() == 0


@pytest.mark.parametrize("selected", ["old", "replacement"])
def test_worker_prepares_selected_generation_before_claiming_source_work(worker_store, monkeypatch, selected):
    from sqlalchemy import text
    from app.ml import adapter, embeddings, queue, worker
    from test_ml_embeddings import add_source, save

    db, path, assets = worker_store
    sources = [add_source(db, name) for name in ("one", "two", "three")]
    for source in sources:
        save(db, source)
    save(db, sources[0], "abandoned")
    manifest = json.loads((assets / "models.json").read_text())
    manifest["models"]["embeddings"]["revision"] = selected
    (assets / "models.json").write_text(json.dumps(manifest))
    adapter.bootstrap(db)
    db.execute(text("DELETE FROM ml_jobs"))
    queue.enqueue(db, "profile", "author", priority=-1)
    db.commit()
    monkeypatch.setattr(embeddings, "CANDIDATE_LIMIT", 2)
    original = worker.Supervisor.process_claim
    observed = []

    def check_preparation(supervisor):
        state = supervisor.connection.execute("SELECT active_generation,staging_generation FROM ml_budget WHERE id=1").fetchone()
        assert state == ("old", None)
        count = supervisor.connection.execute("SELECT count(*) FROM ml_embeddings WHERE generation='abandoned'").fetchone()[0]
        assert count == (1 if selected == "old" else 0)
        observed.append(supervisor.claim.source_kind)
        return original(supervisor)

    monkeypatch.setattr(worker.Supervisor, "process_claim", check_preparation)
    assert run_once(path, assets)[0] == 0
    assert observed == ["profile"]


def test_cached_source_job_rolls_back_derived_growth_and_can_retract(worker_store, monkeypatch):
    from sqlalchemy import text
    from app.ml import adapter, embeddings, queue
    from app.ml.runtime import inference_version
    from test_ml_embeddings import add_source, save

    db, path, assets = worker_store
    source = add_source(db, "source")
    save(db, source)
    cached = json.loads(db.execute(text("SELECT result FROM ml_sources WHERE id='source'")).scalar_one())
    cached.update(concepts=[{"name": "Settlement ledger", "start": 0, "end": 17,
                             "score": 0.99, "label": "technical concept"}], relations=[])
    db.execute(text("UPDATE ml_sources SET result=:result,model_version=:version WHERE id='source'"),
               {"result": json.dumps(cached), "version": inference_version(json.loads((assets / "models.json").read_text())["models"])})
    adapter.bootstrap(db)
    db.execute(text("DELETE FROM ml_jobs"))
    queue.enqueue(db, "item", "source")
    db.commit()
    before = charged_state(db)
    db.rollback()
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: embeddings.FREE_RESERVE - 1)

    assert run_once(path, assets)[0] == 1
    assert charged_state(db) == before
    job = db.execute(text("SELECT attempts,error FROM ml_jobs WHERE source_kind='item' AND source_id='source'")).one()
    assert job.attempts == 1 and "filesystem reserve" in job.error
    assert db.execute(text("SELECT body FROM knowledge_items WHERE id='source'")).scalar_one() == source.text
    db.execute(text("UPDATE knowledge_items SET visibility='private' WHERE id='source'"))
    queue.enqueue(db, "item", "source", priority=-1)
    db.commit()

    assert run_once(path, assets)[0] == 0
    assert db.execute(text("SELECT valid,result FROM ml_sources WHERE id='source'")).one() == (0, "{}")
    assert db.execute(text("SELECT count(*) FROM ml_embeddings")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM ml_jobs WHERE source_kind='item' AND source_id='source'")).scalar_one() == 0


def test_profile_job_rolls_back_derived_growth_and_can_retract(worker_store, monkeypatch):
    from sqlalchemy import text
    from app.ml import adapter, embeddings, queue
    from test_ml_embeddings import add_source

    db, path, assets = worker_store
    db.execute(text("UPDATE profiles SET account_id='account' WHERE id='author'"))
    db.execute(text("INSERT INTO accounts(id,username,password_hash,is_admin,created_at) VALUES ('actor-account','actor','unused',0,datetime('now'))"))
    db.execute(text("INSERT INTO profiles(id,account_id,claim_locked,created_at) VALUES ('actor','actor-account',0,datetime('now'))"))
    add_source(db, "source")
    db.execute(text("INSERT INTO concepts(id) VALUES ('topic')"))
    db.execute(text("INSERT INTO concept_terms(id,concept_id,term,display,is_canonical) VALUES ('term','topic','settlement ledger','Settlement ledger',1)"))
    db.execute(text("INSERT INTO item_concepts(id,item_id,concept_id) VALUES ('tag','source','topic')"))
    db.execute(text("""INSERT INTO impact_events(id,event_type,actor_profile_id,beneficiary_profile_id,item_id,points,dedup_key,created_at)
      VALUES ('help','helped','actor','author','source',1,'helped:actor:item:source',datetime('now'))"""))
    adapter.bootstrap(db)
    from app.models import Account, Concept, KnowledgeItem, Profile
    from app.topic_feedback import TopicFeedbackIn, feedback_dict, save_selection
    item, actor, account = db.get(KnowledgeItem, 'source'), db.get(Profile, 'actor'), db.get(Account, 'actor-account')
    context = feedback_dict(db, item, actor, account)
    request = TopicFeedbackIn(kind='helped', expected_context=context['context_token'], topics=[
        {'concept_id': 'topic', 'identity_revision': db.get(Concept, 'topic').credit_identity_revision}])
    save_selection(db, item, actor, account, 'helped', request)
    db.execute(text("DELETE FROM ml_jobs"))
    queue.enqueue(db, "profile", "author")
    db.commit()
    before = charged_state(db)
    db.rollback()
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: embeddings.FREE_RESERVE - 1)

    assert run_once(path, assets)[0] == 1
    assert charged_state(db) == before
    job = db.execute(text("SELECT attempts,error FROM ml_jobs WHERE source_kind='profile' AND source_id='author'")).one()
    assert job.attempts == 1 and "filesystem reserve" in job.error
    db.execute(text("UPDATE ml_jobs SET available_at=0"))
    db.commit()
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: 20 * 1024**3)
    assert run_once(path, assets)[0] == 0
    assert db.execute(text("SELECT count(*) FROM ml_evidence WHERE source_kind='profile'")).scalar_one() == 1
    db.execute(text("UPDATE profiles SET account_id=NULL WHERE id='author'"))
    db.commit()
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: embeddings.FREE_RESERVE - 1)

    assert run_once(path, assets)[0] == 0
    assert db.execute(text("SELECT valid FROM ml_sources WHERE kind='profile' AND id='author'")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM ml_evidence WHERE source_kind='profile'")).scalar_one() == 0
    assert db.execute(text("SELECT state FROM ml_findings WHERE kind='expertise'")).scalar_one() == "withdrawn"
    assert db.execute(text("SELECT count(*) FROM ml_jobs WHERE source_kind='profile' AND source_id='author'")).scalar_one() == 0


@pytest.mark.parametrize(("function", "counter"), [
    ("acquire_worker", "acquire"), ("renew_worker", "renew_worker"), ("claim_next", "claim"),
])
def test_worker_counts_real_sqlite_contention(worker_store, monkeypatch, capsys, function, counter):
    from app.ml import queue

    _, path, assets = worker_store
    original = getattr(queue, function)
    blocked = False

    def contend_once(*args, **kwargs):
        nonlocal blocked
        if blocked:
            return original(*args, **kwargs)
        blocked = True
        with sqlite3.connect(path) as holder:
            holder.execute("BEGIN IMMEDIATE")
            try:
                return original(*args, **kwargs)
            finally:
                holder.rollback()

    monkeypatch.setattr(queue, function, contend_once)
    assert run_once(path, assets) == (0, {counter: 1})
    assert f'"{counter}": 1' in capsys.readouterr().err


def test_apply_and_retry_locks_are_counted_and_keep_the_claim(worker_store, monkeypatch, capsys):
    from sqlalchemy import text
    from app.ml import queue, worker

    db, path, assets = worker_store
    queue.enqueue(db, "profile", "author")
    db.commit()
    apply, retry = worker.Supervisor.apply, queue.retry_claim

    def locked(original, *args):
        with sqlite3.connect(path) as holder:
            holder.execute("BEGIN IMMEDIATE")
            try:
                return original(*args)
            finally:
                holder.rollback()

    monkeypatch.setattr(worker.Supervisor, "apply", lambda *args: locked(apply, *args))
    monkeypatch.setattr(queue, "retry_claim", lambda *args: locked(retry, *args))
    assert run_once(path, assets) == (1, {"apply": 1, "retry": 1})
    job = db.execute(text("SELECT lease_token,attempts FROM ml_jobs WHERE source_kind='profile' AND source_id='author'")).one()
    assert job.lease_token and job.attempts == 0
    assert db.execute(text("SELECT count(*) FROM ml_sources")).scalar_one() == 0
    assert 'ML SQLite retries final: {"apply": 1, "retry": 1}' in capsys.readouterr().err


def test_checkpoint_backoff_is_counted(worker_store, monkeypatch, capsys):
    from app.ml import queue

    _, path, assets = worker_store
    original = queue.checkpoint_between_batches
    blocked = False
    monkeypatch.setattr(queue, "WAL_HIGH_WATER", 1)

    def pinned_once(connection):
        nonlocal blocked
        if blocked:
            return original(connection)
        blocked = True
        with sqlite3.connect(path) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT revision FROM ml_state").fetchone()
            with sqlite3.connect(path) as writer:
                writer.execute("UPDATE ml_state SET revision=revision+1")
            try:
                return original(connection)
            finally:
                reader.rollback()

    monkeypatch.setattr(queue, "checkpoint_between_batches", pinned_once)
    assert run_once(path, assets) == (0, {"checkpoint": 1})
    assert '"checkpoint": 1' in capsys.readouterr().err


def test_bootstrap_rolls_back_derived_growth_without_losing_jobs(worker_store, monkeypatch):
    from sqlalchemy import text
    from app.ml import embeddings, queue

    db, path, assets = worker_store
    db.execute(text("INSERT INTO concepts(id) VALUES ('topic')"))
    db.execute(text("INSERT INTO concept_terms(id,concept_id,term,display,is_canonical) VALUES ('term','topic','ledger','Ledger',1)"))
    queue.enqueue(db, "profile", "author")
    db.commit()
    before = charged_state(db)
    db.rollback()
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: embeddings.FREE_RESERVE - 1)

    with pytest.raises(embeddings.StoragePressure, match="filesystem reserve"):
        run_once(path, assets)
    assert charged_state(db) == before
    assert db.execute(text("SELECT count(*) FROM ml_overrides")).scalar_one() == 0
    assert db.execute(text("SELECT count(*) FROM ml_jobs WHERE source_kind='profile' AND source_id='author'")).scalar_one() == 1
    assert db.execute(text("SELECT display FROM concept_terms WHERE id='term'")).scalar_one() == "Ledger"


def test_contested_alias_backfills_drain_and_recover_after_definition_removal(worker_store):
    """Run the real queue and supervisor with supplied extraction, not quality evidence."""
    import threading
    from sqlalchemy import text
    from app.ml import api, eligibility, queue, relation_syntax, syntax, worker
    from app.ml.models import Finding
    from app.ml.runtime import inference_version
    from app.models import Account

    db, path, assets = worker_store
    names = ("Signal Routing Controller", "Storage Recovery Catalog")
    models = json.loads((assets / "models.json").read_text())["models"]
    metadata = (inference_version(models), "old", 1024)

    class FixedExtraction:
        calls = 0
        iterations = 0

        def analyze(self, body, heartbeat, progress=None):
            from ml_synthetic_records import judged

            self.calls += 1
            result = judged({"concepts": [{"name": name, "start": body.index(name),
                    "end": body.index(name) + len(name), "score": 0.96, "label": "named entity"}
                    for name in names if name in body], "relations": [], "chunks": [],
                    "corroborated_definitions": [], "conflict_definitions": [],
                    "conflict_coverage_revision": syntax.CONFLICT_REVISION,
                    "relation_guard_revision": relation_syntax.REVISION}, body)
            result["eligibility"]["version"] = eligibility.version(models)
            return result, metadata

        def check_memory(self):
            self.iterations += 1
            if self.iterations > 120:
                raise RuntimeError("Contested alias never drained after 120 worker iterations")

        def close(self):
            pass

    inference = FixedExtraction()

    def drain():
        db.commit()
        inference.iterations = 0
        supervisor = worker.Supervisor(path, assets, threading.Event())
        supervisor.inference = inference
        try:
            assert supervisor.run("drain") == 0
        finally:
            supervisor.close()
        db.expire_all()
        assert db.execute(text("SELECT count(*) FROM ml_jobs")).scalar_one() == 0
        assert db.execute(text("SELECT backfill_kind FROM ml_state WHERE id=1")).scalar_one() is None

    for index in range(4):
        author, item = f"alias-author-{index}", f"alias-item-{index}"
        db.execute(text("INSERT INTO profiles(id,claim_locked,created_at) VALUES (:id,0,datetime('now'))"), {"id": author})
        db.execute(text("""INSERT INTO knowledge_items(id,kind,body,visibility,author_profile_id,created_at,updated_at)
          VALUES (:id,'note',:body,'team',:author,datetime('now'),datetime('now'))"""),
          {"id": item, "body": f"{names[index // 2]} (SRC) records inspection batch {index}.", "author": author})
        drain()
        if index == 1:
            assert db.execute(text("SELECT count(*) FROM concept_terms WHERE term='src'")).scalar_one() == 1

    assert inference.calls == 4
    concepts = dict(db.execute(text("SELECT display,concept_id FROM concept_terms WHERE is_canonical=1")).all())
    assert set(concepts) == set(names) and len(set(concepts.values())) == 2
    assert db.execute(text("SELECT count(*) FROM concept_terms WHERE term='src'")).scalar_one() == 0
    assert db.execute(text("SELECT state FROM ml_findings WHERE kind='alias'")).scalars().all() == ["held", "held"]
    for _ in range(3):
        previous = db.execute(text("SELECT backfill_generation FROM ml_state WHERE id=1")).scalar_one()
        queue.request_backfill(db)
        drain()
        assert db.execute(text("SELECT backfill_generation FROM ml_state WHERE id=1")).scalar_one() == previous + 1
    assert inference.calls == 4

    # Keep both concepts supported while removing only the competing definitions.
    for item in ("alias-item-2", "alias-item-3"):
        db.execute(text("UPDATE knowledge_items SET body=replace(body,' (SRC)','') WHERE id=:id"), {"id": item})
        drain()
    assert inference.calls == 6
    assert dict(db.execute(text("SELECT display,concept_id FROM concept_terms WHERE is_canonical=1")).all()) == concepts
    assert db.execute(text("SELECT concept_id FROM concept_terms WHERE term='src'")).scalar_one() == concepts[names[0]]

    key = db.query(Finding).filter(Finding.kind == "alias",
            text("json_extract(payload,'$.concept_id')=:id")).params(id=concepts[names[0]]).one().key
    for mode in ("suppressed", "pinned"):
        api.decide(key, api.Decision(mode=mode), Account(username="fixture-admin"), db)
        drain()
        assert db.execute(text("SELECT mode FROM ml_overrides WHERE key=:key"), {"key": key}).scalar_one() == mode
        assert db.execute(text("SELECT count(*) FROM concept_terms WHERE term='src'")).scalar_one() == int(mode == "pinned")
    db.execute(text("UPDATE knowledge_items SET body=replace(body,' records',' (SRC) records') WHERE id IN ('alias-item-2','alias-item-3')"))
    drain()
    assert db.execute(text("SELECT concept_id FROM concept_terms WHERE term='src'")).scalar_one() == concepts[names[0]]


def test_bge_extraction_and_cached_relevance_share_one_process_and_recheck_model_changes(worker_store):
    import threading
    from sqlalchemy import text
    from app.ml import adapter, eligibility, policy, queue, relation_syntax, syntax, worker
    from app.ml.runtime import inference_version
    from test_ml_embeddings import add_source
    from ml_relation_helpers import analyze as _analyze, span as _span

    db, path, assets = worker_store
    manifest = json.loads((assets / "models.json").read_text())
    version = inference_version(manifest["models"])
    cached_body = "Citrine Pump drives the Emerald Cell loop."
    result = _analyze(cached_body, [_span(cached_body, name, score=.999) for name in ("Citrine Pump", "Emerald Cell")], {}, full=True)
    result.update(chunks=[], conflict_definitions=[], conflict_coverage_revision=syntax.CONFLICT_REVISION,
                  relation_guard_revision=relation_syntax.REVISION)
    # A historical Qwen identity must not be reused as BGE judgment.
    result["eligibility"] = {"version": "qwen:historical", "margins": {"citrine pump": 9.0, "emerald cell": 9.0}}
    adapter.bootstrap(db)
    adapter.apply_source(db, "item", "cached", add_source(db, "cached", body=cached_body), result, version, "old", 1024)
    add_source(db, "fresh", body="Citrine Pump replaced the Emerald Cell valve.")
    db.execute(text("DELETE FROM ml_jobs"))
    queue.enqueue(db, "item", "fresh")
    db.execute(text("UPDATE ml_state SET pipeline_version=:version WHERE id=1"), {"version": version + ":" + policy.VERSION})

    def drain():
        db.commit()
        supervisor = worker.Supervisor(path, assets, threading.Event())
        supervisor.inference = InferenceProcess(assets, target=shared_bge_child)
        try:
            assert supervisor.run("drain") == 0
        finally:
            supervisor.close()

    drain()
    assert concept_states(db) == {"Citrine Pump": "active", "Emerald Cell": "held"}
    requests = assets / "bge-requests.jsonl"
    first = list(map(json.loads, requests.read_text().splitlines()))
    assert len(first) == 2 and len({r["pid"] for r in first}) == 1
    assert {r["eligibility_only"] for r in first} == {False, True}
    assert set(db.execute(text("SELECT json_extract(result,'$.eligibility.version') FROM ml_sources WHERE kind='item'")).scalars()) == {
        eligibility.version(manifest["models"])}
    drain()
    assert len(requests.read_text().splitlines()) == 2  # complete cache hit, no models
    manifest["models"]["embeddings"]["revision"] = "new"
    (assets / "models.json").write_text(json.dumps(manifest))
    drain()
    assert len(requests.read_text().splitlines()) == 4
    assert concept_states(db) == {"Citrine Pump": "active", "Emerald Cell": "held"}


def eligibility_worker(worker_store, body, name):
    """One BGE process over a cached, unjudged source."""
    import threading
    from sqlalchemy import text
    from app.ml import adapter, policy, relation_syntax, syntax, worker
    from app.ml.runtime import inference_version
    from test_ml_embeddings import add_source
    from ml_relation_helpers import analyze as _analyze, span as _span

    db, path, assets = worker_store
    manifest = json.loads((assets / "models.json").read_text())
    version = inference_version(manifest["models"])

    def extraction(text_body, span_name):
        result = _analyze(text_body, [_span(text_body, span_name, score=.999)], {}, full=True)
        result.update(chunks=[], conflict_definitions=[], conflict_coverage_revision=syntax.CONFLICT_REVISION,
                      relation_guard_revision=relation_syntax.REVISION)
        del result["eligibility"]  # extraction alone carries no judgment
        return result

    adapter.bootstrap(db)
    adapter.apply_source(db, "item", "source", add_source(db, "source", body=body), extraction(body, name),
                         version, "old", 1024)
    db.execute(text("DELETE FROM ml_jobs"))
    # A current pipeline: the only pending work is the eligibility judgment.
    db.execute(text("UPDATE ml_state SET pipeline_version=:version,decision_policy='{}',backfill_kind=NULL WHERE id=1"),
               {"version": version + ":" + policy.VERSION})
    db.commit()
    supervisor = worker.Supervisor(path, assets, threading.Event())
    supervisor.inference = InferenceProcess(assets, target=shared_bge_child)
    return supervisor


def concept_states(db):
    from sqlalchemy import text

    db.expire_all()
    return dict(db.execute(text("SELECT json_extract(payload,'$.name'),state FROM ml_findings WHERE kind='concept'")).all())


def test_failed_eligibility_is_checked_again_after_the_source_changes(worker_store):
    from sqlalchemy import text
    from app.ml import queue

    db = worker_store[0]
    supervisor = eligibility_worker(worker_store, "Fragile Pump drives the loop.", "Fragile Pump")
    try:
        assert supervisor.run("drain") == 0
        assert concept_states(db) == {"Fragile Pump": "held"}
        db.execute(text("UPDATE knowledge_items SET body='Sturdy Pump drives the loop.' WHERE id='source'"))
        queue.enqueue(db, "item", "source")
        db.commit()
        # The same long-lived worker continues after the edit.
        queue.release_worker(supervisor.connection, supervisor.token)
        assert supervisor.run("drain") == 0
    finally:
        supervisor.close()
    assert concept_states(db)["Sturdy Pump"] == "active"


def test_refused_eligibility_storage_pauses_the_sweep_without_stopping_the_worker(worker_store, monkeypatch):
    import time
    from sqlalchemy import text
    from app.ml import embeddings

    db, path, assets = worker_store
    supervisor = eligibility_worker(worker_store, "Citrine Pump drives the loop.", "Citrine Pump")
    monkeypatch.setattr(embeddings, "_free_bytes", lambda db: embeddings.FREE_RESERVE - 1)
    try:
        assert supervisor.run("drain") == 0
        assert supervisor.eligibility_paused_until > time.monotonic()
        assert supervisor.verify_eligibility() == 0
    finally:
        supervisor.close()
    assert concept_states(db) == {"Citrine Pump": "held"}
    assert db.execute(text("SELECT json_extract(result,'$.eligibility') FROM ml_sources WHERE id='source'")).scalar_one() is None
    assert len((assets / "bge-requests.jsonl").read_text().splitlines()) == 1


def test_status_jobs_shows_the_running_step_then_the_finished_job(worker_store, tmp_path):
    import threading
    from sqlalchemy import text
    from app.ml import activity, adapter, policy, queue, worker
    from app.ml.runtime import inference_version
    from test_ml_embeddings import add_source

    db, path, assets = worker_store
    models = json.loads((assets / "models.json").read_text())["models"]
    adapter.bootstrap(db)
    add_source(db, "watched", body="Citrine Pump drives the cooling loop.")
    db.execute(text("DELETE FROM ml_jobs"))
    queue.enqueue(db, "item", "watched")
    db.execute(text("UPDATE ml_state SET pipeline_version=:version,decision_policy='{}',backfill_kind=NULL WHERE id=1"),
               {"version": inference_version(models) + ":" + policy.VERSION})
    db.commit()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"),
           "MDS_DATABASE_URL": f"sqlite:///{path}", "MDS_DATA_DIR": str(tmp_path / "unused")}

    def status_jobs():
        return subprocess.run([sys.executable, "-m", "app.ml.worker", "--status", "--jobs"], env=env,
                              capture_output=True, text=True, timeout=20, check=True).stdout

    def wait_for(fragment):
        deadline = time.monotonic() + 30
        while fragment not in (report := status_jobs()):
            assert time.monotonic() < deadline, report
            time.sleep(0.2)
        return report

    stop = threading.Event()

    def serve():
        # SQLite connections stay on the thread that opened them.
        supervisor = worker.Supervisor(path, assets, stop)
        supervisor.inference = InferenceProcess(assets, target=progress_child)
        try:
            supervisor.run("daemon")
        finally:
            supervisor.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        running = wait_for("window 2 of 2: extracting relations")
        assert "Worker: running since" in running
        assert "Current job: item watched, attempt 1" in running
        assert "Stage: running inference — window 2 of 2: extracting relations" in running
        assert re.search(r"1\s+processing\s+item\s+0\s+\S+\s+new or changed content\s+Note: Citrine Pump drives the cooling loop\.", running)
        (assets / "release").touch()
        finished = wait_for("Recently finished")
        assert re.search(r"done\s+item\s+[\d.]+s\s+Note: Citrine Pump drives the cooling loop\.", finished)
        # The real adapter and policy decided; the report explains that decision.
        wait_for("Concept confirmed by the model: Citrine Pump — The model is 99.9% sure this is a named thing.")
        wait_for("Queue: 0 jobs")
        # The finished job names the concept it created, for the author's progress line.
        created = db.execute(text("SELECT concept_id FROM concept_terms WHERE term='citrine pump'")).scalar()
        recent = json.loads(activity.activity_path(path).read_text())["recent"]
        assert [concept for job in recent if job["id"] == "watched" for concept in job["created"]] == [created]
    finally:
        stop.set()
        thread.join(timeout=30)
    assert not thread.is_alive()
    stopped = status_jobs()
    assert "Worker: not running" in stopped and "stopped" in stopped
