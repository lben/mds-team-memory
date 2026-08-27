import os
import socket
import subprocess
import sys
import time
import types
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENV_BIN = Path(sys.executable).parent


@pytest.fixture(scope="session")
def base_url_server(tmp_path_factory):
    """Run migrations and a real uvicorn server against a fresh data dir,
    serving the compiled frontend from frontend/dist."""
    assert (ROOT / "frontend" / "dist" / "index.html").exists(), "run `npm run build` first"
    data_dir = tmp_path_factory.mktemp("e2e-data")
    env = {
        **os.environ,
        "MDS_DATA_DIR": str(data_dir),
        "MDS_DATABASE_URL": f"sqlite:///{data_dir / 'e2e.sqlite3'}",
    }

    subprocess.run(
        [str(VENV_BIN / "alembic"), "-c", str(ROOT / "backend" / "alembic.ini"), "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(
        [
            str(VENV_BIN / "uvicorn"),
            "app.main:app",
            "--app-dir",
            str(ROOT / "backend"),
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{base}/api/health", timeout=1)
            break
        except OSError:
            time.sleep(0.25)
    else:
        server.terminate()
        raise RuntimeError("server did not start")

    def create_admin(username: str, password: str) -> None:
        """Run the real deploy-time command against this server's database."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "manage.py"), "create-admin", "--username", username],
            input=password + "\n",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr

    yield types.SimpleNamespace(url=base, create_admin=create_admin)
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=10)
