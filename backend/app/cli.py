"""Server-side management commands.

Run from the deployed folder:

    python manage.py create-admin
    python manage.py list-admins
    python manage.py reset-database
"""

import argparse
import getpass
import shutil
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError, OperationalError

from . import config
from .auth import hash_password
from .db import SessionLocal, engine
from .models import Account

MIN_USERNAME = 3
MIN_PASSWORD = 8
RESET_CONFIRMATION = "reset"


def _prompt(label: str) -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    return input(f"{label}: ").strip()


def _read_password() -> str:
    """Read a password without echoing it, so it never reaches shell history."""
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Confirm password: "):
        raise SystemExit("error: passwords do not match")
    return first


def create_admin(username: str | None) -> None:
    db = SessionLocal()
    try:
        existing = db.query(Account).filter_by(is_admin=True).order_by(Account.username).all()
        username = (username or _prompt("Username")).strip()
        if len(username) < MIN_USERNAME:
            raise SystemExit(f"error: username must be at least {MIN_USERNAME} characters")
        if any(a.username == username for a in existing):
            raise SystemExit(f"error: admin '{username}' already exists")
        password = _read_password()
        if len(password) < MIN_PASSWORD:
            raise SystemExit(f"error: password must be at least {MIN_PASSWORD} characters")
        db.add(Account(username=username, password_hash=hash_password(password), is_admin=True))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise SystemExit(f"error: admin '{username}' already exists")
        print(f"Created admin '{username}'.")
    except OperationalError:
        raise SystemExit(
            "error: the database is not initialised. Run the migrations first:\n"
            "  alembic -c backend/alembic.ini upgrade head"
        )
    finally:
        db.close()


def list_admins() -> None:
    db = SessionLocal()
    try:
        admins = db.query(Account).filter_by(is_admin=True).order_by(Account.username).all()
        if not admins:
            print("No admin accounts exist. Create one with: python manage.py create-admin")
            return
        for admin in admins:
            print(admin.username)
    except OperationalError:
        raise SystemExit(
            "error: the database is not initialised. Run the migrations first:\n"
            "  alembic -c backend/alembic.ini upgrade head"
        )
    finally:
        db.close()


def _sqlite_file() -> Path:
    """The database file this instance is configured to use."""
    prefix = "sqlite:///"
    if not config.DATABASE_URL.startswith(prefix):
        raise SystemExit(
            f"error: reset only supports a SQLite database file, not {config.DATABASE_URL!r}"
        )
    return Path(config.DATABASE_URL[len(prefix) :])


def _summarise(db_file: Path) -> list[str]:
    """Best-effort description of what is about to be destroyed.

    Read from the live database rather than a hardcoded table list, so this
    keeps working as the schema changes.
    """
    if not db_file.exists():
        return ["no database file yet"]
    lines = []
    try:
        con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' "
                "AND name != 'alembic_version' ORDER BY name"
            )
        ]
        for table in tables:
            count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count:
                lines.append(f"{count} {table}")
        con.close()
    except sqlite3.Error as exc:
        return [f"unreadable database ({exc})"]
    return lines or ["no rows"]


def _migrate() -> str:
    """Rebuild the schema at the current head using the real migrations."""
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    if not ini.exists():
        raise SystemExit(f"error: migrations not found at {ini}")
    cfg = Config(str(ini))
    command.upgrade(cfg, "head")
    with sqlite3.connect(_sqlite_file()) as con:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else "unknown"


def reset_database(assume_yes: bool) -> None:
    """Delete the database and uploads, then migrate back up to head.

    Deleting rather than downgrading means this works from any schema version,
    including one this build has never seen.
    """
    db_file = _sqlite_file()
    uploads = sorted(p for p in config.UPLOAD_DIR.glob("*") if p.is_file())

    print("This permanently deletes:")
    print(f"  database  {db_file}")
    for line in _summarise(db_file):
        print(f"              · {line}")
    print(f"  uploads   {config.UPLOAD_DIR}  ({len(uploads)} file{'' if len(uploads) == 1 else 's'})")

    if not assume_yes:
        if not sys.stdin.isatty():
            raise SystemExit("error: refusing to reset without a terminal; pass --yes")
        if input(f"Type '{RESET_CONFIRMATION}' to confirm: ").strip() != RESET_CONFIRMATION:
            raise SystemExit("Cancelled. Nothing was deleted.")

    # Drop pooled connections first, or SQLite may recreate the file underneath us.
    engine.dispose()
    for path in (db_file, Path(f"{db_file}-wal"), Path(f"{db_file}-shm")):
        path.unlink(missing_ok=True)
    if config.UPLOAD_DIR.exists():
        shutil.rmtree(config.UPLOAD_DIR)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    revision = _migrate()
    print(f"Reset complete. Schema is at revision {revision}.")
    print("Create an administrator with: python manage.py create-admin")
    print("Restart the application so it picks up the new database.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="manage.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create an administrator account")
    create.add_argument("--username", help="prompted for when omitted")
    commands.add_parser("list-admins", help="list existing administrator accounts")
    reset = commands.add_parser(
        "reset-database", help="delete all data and rebuild the schema at the current version"
    )
    reset.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    if args.command == "create-admin":
        create_admin(args.username)
    elif args.command == "reset-database":
        reset_database(args.yes)
    else:
        list_admins()
