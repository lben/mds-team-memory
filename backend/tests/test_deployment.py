"""Process ownership is a safety boundary for unattended deployment recovery."""

import shutil
import subprocess
from pathlib import Path

import pytest


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
