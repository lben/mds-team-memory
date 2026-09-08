"""Actual process lifecycle checks; model accuracy is evaluated separately."""

import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

from app.ml.worker import InferenceProcess, Stopped, process_tree_rss

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
def test_model_load_failure_closes_the_child(tmp_path):
    child = InferenceProcess(tmp_path, target=failed_child)
    with pytest.raises(RuntimeError, match="Test loader failure"):
        child.analyze("body", lambda: None)
    assert child.process is None


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
