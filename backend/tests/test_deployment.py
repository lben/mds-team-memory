"""Process ownership is a safety boundary for unattended deployment recovery."""

import shutil
import subprocess
from pathlib import Path


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
