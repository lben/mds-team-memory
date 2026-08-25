import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def app_modules(tmp_path_factory):
    """Point the app at a temporary data dir, then create the schema with the
    real Alembic migrations so tests prove the migration path."""
    data_dir = tmp_path_factory.mktemp("data")
    os.environ["MDS_DATA_DIR"] = str(data_dir)
    os.environ["MDS_DATABASE_URL"] = f"sqlite:///{data_dir / 'test.sqlite3'}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")

    from app.main import app

    return app


@pytest.fixture()
def make_client(app_modules):
    """Factory producing an isolated 'browser' (its own cookie jar / profile)."""
    from fastapi.testclient import TestClient

    clients = []

    def factory() -> "TestClient":
        client = TestClient(app_modules)
        client.get("/api/profile")  # establish the profile cookie
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.close()


@pytest.fixture()
def admin_client(make_client):
    """A client logged in as admin (running first-time setup if needed)."""
    client = make_client()
    state = client.get("/api/admin/state").json()
    credentials = {"username": "rootadmin", "password": "correct-horse-9"}
    if state["needs_setup"]:
        assert client.post("/api/admin/setup", json=credentials).status_code == 200
    else:
        assert client.post("/api/admin/login", json=credentials).status_code == 200
    return client
