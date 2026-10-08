import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Set before any test can import the app: app.config reads these once, at import,
# so a test that imports the app without the fixture still never reaches the
# developer's real database.
DATA_DIR = Path(tempfile.mkdtemp(prefix="mds-tests-"))
atexit.register(shutil.rmtree, DATA_DIR, ignore_errors=True)
os.environ["MDS_DATA_DIR"] = str(DATA_DIR)
os.environ["MDS_DATABASE_URL"] = f"sqlite:///{DATA_DIR / 'test.sqlite3'}"


@pytest.fixture(scope="session")
def app_modules():
    """Create the temporary database's schema with the real Alembic migrations,
    so tests prove the migration path."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")

    from app.main import app

    return app


@pytest.fixture()
def make_client(app_modules):
    """Factory producing an isolated 'browser' (its own cookie jar / profile).

    Contributing needs an account, so each browser signs up as a new member
    unless the test is about an anonymous visitor (`account=False`).
    """
    import uuid
    from fastapi.testclient import TestClient

    clients = []

    def factory(account: bool = True) -> "TestClient":
        client = TestClient(app_modules)
        client.get("/api/profile")  # establish the profile cookie
        if account:
            signup = client.post("/api/auth/signup", json={"username": f"member-{uuid.uuid4().hex[:10]}",
                                                           "password": "member-password"})
            assert signup.status_code == 200, signup.text
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.close()


ADMIN_CREDENTIALS = {"username": "rootadmin", "password": "correct-horse-9"}


@pytest.fixture()
def admin_client(make_client, app_modules):
    """A client logged in as admin. The account is seeded directly because
    admin creation is a server-side command, not an HTTP endpoint."""
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models import Account

    db = SessionLocal()
    try:
        if not db.query(Account).filter_by(username=ADMIN_CREDENTIALS["username"]).first():
            db.add(
                Account(
                    username=ADMIN_CREDENTIALS["username"],
                    password_hash=hash_password(ADMIN_CREDENTIALS["password"]),
                    is_admin=True,
                )
            )
            db.commit()
    finally:
        db.close()

    client = make_client(account=False)
    assert client.post("/api/auth/login", json=ADMIN_CREDENTIALS).status_code == 200
    return client
