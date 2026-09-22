"""Process ownership is a safety boundary for unattended deployment recovery."""

from contextlib import closing
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def release_controller(tmp_path):
    """Real controller, isolated releases, no application or deployment activity."""
    control = tmp_path / "mdsctl.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "tools/mdsctl.sh", control)
    for name in ("data", "run", "bin"):
        (tmp_path / name).mkdir()
    for name, compatible in (("new", True), ("old", False)):
        release = tmp_path / "releases" / name
        for directory in (".venv/bin", ".ml-venv/bin", "tools", "backend/app", "backend/alembic/versions"):
            (release / directory).mkdir(parents=True, exist_ok=True)
        for environment in (".venv", ".ml-venv"):
            (release / environment / "bin/python").symlink_to(sys.executable)
        (release / "tools/deploylib.py").write_text(
            f"from pathlib import Path\nPath({str(tmp_path / 'target-helper-called')!r}).touch()\n")
        if compatible:
            # Inspect declarations without importing target code or dependencies.
            (release / "backend/app/topic_feedback.py").write_text(
                "CONTRACT_VERSION = 'explicit-topic-feedback-v1'\nraise RuntimeError('do not import')\n")
            (release / "backend/alembic/versions/0015_explicit_topic_feedback.py").write_text(
                "revision = '0015'\nraise RuntimeError('do not import')\n")
    (tmp_path / "current").symlink_to(tmp_path / "releases/new")
    (tmp_path / "previous").symlink_to(tmp_path / "releases/old")
    # Health check for test processes only; no listener is opened.
    (tmp_path / "bin/curl").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "bin/curl").chmod(0o755)
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"MDS_DATA_DIR", "MDS_DATABASE_URL"}}
    environment["PATH"] = str(tmp_path / "bin") + os.pathsep + environment["PATH"]

    def run(*args):
        return subprocess.run(["bash", str(control), *args], capture_output=True,
                              text=True, timeout=10, env=environment)
    return tmp_path, run


def _confirmation_database(path, populated=True):
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE topic_confirmations (id TEXT PRIMARY KEY, human_write TEXT)")
    if populated:
        db.execute("INSERT INTO topic_confirmations VALUES ('confirmation', 'keep this human decision')")
    db.commit()
    # Keep the writer open: schema and human writes may still live in WAL.
    return db


def _test_processes(root, release):
    web = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                            str(release / ".venv/bin/uvicorn"), "app.main:app"])
    runtime = root / "runtime/app/ml"
    runtime.mkdir(parents=True)
    (runtime.parent / "__init__.py").touch()
    (runtime / "__init__.py").touch()
    (runtime / "worker.py").write_text("import time\ntime.sleep(60)\n")
    worker = subprocess.Popen([str(release / ".ml-venv/bin/python"), "-m", "app.ml.worker", "--assets", str(root)],
                              env={**os.environ, "PYTHONPATH": str(root / "runtime")})
    for name, process in (("app", web), ("ml", worker)):
        (root / "run" / f"{name}.pid").write_text(str(process.pid))
    return web, worker


@pytest.mark.parametrize("command", ["rollback", "activate", "start", "ml-start", "restart", "migrate", "preflight"])
def test_incompatible_release_refused_before_process_or_link_changes(release_controller, command):
    root, run = release_controller
    current = root / "releases" / ("old" if command in {"start", "ml-start", "restart", "preflight"} else "new")
    (root / "current").unlink()
    (root / "current").symlink_to(current)
    with closing(_confirmation_database(root / "data/mds.sqlite3")) as db:
        before = (root / "data/mds.sqlite3").read_bytes()
        processes = _test_processes(root, current)
        try:
            args = (command, "old") if command in {"activate", "migrate"} else (command,)
            result = run(*args)
            assert result.returncode != 0
            assert "explicit topic feedback" in result.stderr, result.stderr
            assert (root / "current").resolve() == current
            assert (root / "previous").resolve() == root / "releases/old"
            assert all(process.poll() is None for process in processes)
            for name, process in zip(("app", "ml"), processes):
                assert (root / "run" / f"{name}.pid").read_text() == str(process.pid)
            assert db.execute("SELECT * FROM topic_confirmations").fetchall() == [
                ("confirmation", "keep this human decision")]
            assert (root / "data/mds.sqlite3").read_bytes() == before
            assert not (root / "target-helper-called").exists()
        finally:
            for process in processes:
                process.terminate()
                process.wait(timeout=5)


@pytest.mark.parametrize("populated", [False, True])
def test_database_schema_contract_checks_actual_configured_database(release_controller, populated):
    root, run = release_controller
    database = root / "alternate.sqlite3"
    (root / "app.env").write_text("MDS_DATABASE_URL=" + shlex.quote("sqlite:///" + str(database)) + "\n")
    with closing(_confirmation_database(database, populated=populated)):
        assert run("compatible", "old").returncode != 0
        assert run("compatible", "new").returncode == 0
    assert not (root / "data/mds.sqlite3").exists()
    assert not (root / "target-helper-called").exists()


@pytest.mark.parametrize("database_state", ["missing", "legacy", "corrupt"])
def test_legacy_database_and_unreadable_database_compatibility(release_controller, database_state):
    root, run = release_controller
    database = root / "data/mds.sqlite3"
    if database_state == "legacy":
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE knowledge_items (id TEXT)")
    elif database_state == "corrupt":
        database.write_bytes(b"unreadable database" * 100)
    result = run("compatible", "old")
    assert (result.returncode == 0) == (database_state != "corrupt"), result.stderr
    if database_state == "missing":
        assert not database.exists()


@pytest.mark.skipif(not Path("/proc/self/cmdline").exists(), reason="Exercise actual supported Linux process ownership")
def test_compatible_rollback_starts_release_and_preserves_human_writes(release_controller):
    root, run = release_controller
    previous = root / "releases/old"
    for relative in ("backend/app/topic_feedback.py", "backend/alembic/versions/0015_explicit_topic_feedback.py"):
        shutil.copyfile(root / "releases/new" / relative, previous / relative)
    uvicorn = previous / ".venv/bin/uvicorn"
    uvicorn.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
    uvicorn.chmod(0o755)
    with closing(_confirmation_database(root / "data/mds.sqlite3")) as db:
        processes = _test_processes(root, root / "releases/new")
        try:
            result = run("rollback")
            assert result.returncode == 0, result.stderr
            assert (root / "current").resolve() == previous
            assert (root / "previous").resolve() == previous
            assert all(process.poll() is not None for process in processes)
            assert (root / "run/app.pid").is_file()
            assert db.execute("SELECT human_write FROM topic_confirmations").fetchone() == ("keep this human decision",)
        finally:
            stopped = run("stop")
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            assert stopped.returncode == 0, stopped.stderr


@pytest.mark.parametrize("relative,content", [
    ("backend/app/topic_feedback.py", "CONTRACT_VERSION = 'retired-contract'\n"),
    ("backend/alembic/versions/0015_explicit_topic_feedback.py", "revision = '0014'\n"),
    ("backend/app/topic_feedback.py", "not valid Python!"),
])
def test_incomplete_or_different_target_contract_cannot_start(release_controller, relative, content):
    root, run = release_controller
    (root / "releases/new" / relative).write_text(content)
    with closing(_confirmation_database(root / "data/mds.sqlite3")):
        result = run("start")
        assert result.returncode != 0
        assert "database compatibility check failed" in result.stderr
        assert not (root / "run/app.pid").exists()


@pytest.mark.parametrize("url", ["postgresql://uninspected/database", "sqlite:///:memory:", "sqlite:///mds.sqlite3?mode=ro"])
def test_uninspectable_database_configuration_fails_closed(release_controller, url):
    root, run = release_controller
    (root / "app.env").write_text("MDS_DATABASE_URL=" + shlex.quote(url) + "\n")
    result = run("compatible", "old")
    assert result.returncode != 0
    assert "file-backed sqlite:///" in result.stderr


def test_deployment_checks_compatibility_before_stopping(tmp_path, monkeypatch, capfd):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    import deploy
    from deploylib import Target

    target = Target("uat", {"host": "test.invalid", "root": "/isolated-test"})
    archive = tmp_path / "release.tar.gz"
    archive.touch()
    calls = []
    monkeypatch.setattr(sys, "argv", ["deploy.py", "--skip-build"])
    monkeypatch.setattr(deploy, "load_target", lambda _: target)
    monkeypatch.setattr(deploy, "assemble_release", lambda *a: None)
    monkeypatch.setattr(deploy, "pack_release", lambda *a: archive)
    monkeypatch.setattr(deploy, "write_env_file", lambda *a: tmp_path / "app.env")
    monkeypatch.setattr(deploy, "upload", lambda *a: None)

    def control(_target, command, *args):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command == "compatible" else 0)

    monkeypatch.setattr(deploy, "ctl", control)
    with pytest.raises(SystemExit, match="1"):
        deploy.main()
    assert calls == ["unpack", "setup", "compatible"]
    assert "running server was never touched" in capfd.readouterr().err


def test_failed_deploy_recovery_keeps_database_when_rollback_is_incompatible(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    import deploy
    from deploylib import Target

    calls = []
    monkeypatch.setattr(deploy, "ctl", lambda _target, command: (
        calls.append(command) or subprocess.CompletedProcess(command, 1)))
    deploy.recover(Target("uat", {"host": "test.invalid", "root": "/isolated-test"}), deploy.SWAPPED)
    assert calls == ["rollback"]
    message = capsys.readouterr().err
    assert "blocked rollback preserves" in message
    assert "discard later human writes" in message
    assert "nothing is serving" not in message


def test_stop_preserves_unrelated_process_with_stale_pid_files(tmp_path):
    control = tmp_path / "mdsctl.sh"
    shutil.copyfile(Path(__file__).resolve().parents[2] / "tools/mdsctl.sh", control)
    (tmp_path / "run").mkdir()
    process = subprocess.Popen(["sleep", "60"])
    try:
        for name in ("app.pid", "ml.pid"):
            (tmp_path / "run" / name).write_text(str(process.pid))
        result = subprocess.run(["bash", str(control), "stop"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert process.poll() is None
        assert not (tmp_path / "run/app.pid").exists()
        assert not (tmp_path / "run/ml.pid").exists()
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("filesystem", ["fakeowner", "nfs", "overlay"])
def test_ml_database_rejects_shared_filesystems(tmp_path, monkeypatch, filesystem):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    from deploylib import local_sqlite_filesystem

    original = Path.read_text
    mountinfo = f"1 0 0:1 / / rw - overlay overlay rw\n2 1 0:2 / {tmp_path} rw - {filesystem} source rw\n"
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **k: mountinfo
                        if str(path) == "/proc/self/mountinfo" else original(path, *a, **k))
    if filesystem == "overlay":
        assert local_sqlite_filesystem(tmp_path / "database") == "overlay"
    else:
        with pytest.raises(ValueError, match=f"unsupported filesystem {filesystem}"):
            local_sqlite_filesystem(tmp_path / "database")


@pytest.mark.parametrize("new_sources", [0, 1])
def test_load_gate_counts_only_work_completed_during_traffic(monkeypatch, new_sources):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    from ml_load_check import record_load_gates

    report = {
        "inference_before_load": {"sources": 2, "embedding_chunks": 2},
        "samples": [
            {"phase": "worker_on", "sources": 3, "chunks": 3, "inference_observed_elapsed": 9,
             "worker_cpu_affinity": [0, 1, 2, 3]},
            {"phase": "worker_on", "sources": 2 + new_sources, "chunks": 2 + new_sources,
             "worker_cpu_affinity": [0, 1, 2, 3], "inference_observed_elapsed": 15},
            {"phase": "worker_on", "sources": 10, "chunks": 10, "inference_observed_elapsed": 21,
             "worker_cpu_affinity": [0, 1, 2, 3]},
        ],
        "phases": {phase: {"write": {"p95_seconds": 0.5}, "errors": [],
                            "traffic_start_elapsed": 10, "traffic_end_elapsed": 20}
                   for phase in ("worker_off", "worker_on")},
        "worker_exit_before_stop": None,
        "failures": [],
    }
    record_load_gates(report)
    assert report["real_inference"] == {"sources": new_sources, "embedding_chunks": new_sources}
    assert bool(report["failures"]) == (new_sources == 0)
    assert report["phases"]["worker_on"]["write"]["p95_seconds"] == 0.5
