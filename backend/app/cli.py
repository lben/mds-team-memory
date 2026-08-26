"""Server-side management commands.

Run from the deployed folder:

    python manage.py create-admin
    python manage.py list-admins
"""

import argparse
import getpass
import sys

from sqlalchemy.exc import IntegrityError, OperationalError

from .auth import hash_password
from .db import SessionLocal
from .models import AdminUser

MIN_USERNAME = 3
MIN_PASSWORD = 8


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
        existing = db.query(AdminUser).order_by(AdminUser.username).all()
        username = (username or _prompt("Username")).strip()
        if len(username) < MIN_USERNAME:
            raise SystemExit(f"error: username must be at least {MIN_USERNAME} characters")
        if any(a.username == username for a in existing):
            raise SystemExit(f"error: admin '{username}' already exists")
        password = _read_password()
        if len(password) < MIN_PASSWORD:
            raise SystemExit(f"error: password must be at least {MIN_PASSWORD} characters")
        db.add(AdminUser(username=username, password_hash=hash_password(password)))
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
        admins = db.query(AdminUser).order_by(AdminUser.username).all()
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="manage.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="create an administrator account")
    create.add_argument("--username", help="prompted for when omitted")
    commands.add_parser("list-admins", help="list existing administrator accounts")
    args = parser.parse_args()

    if args.command == "create-admin":
        create_admin(args.username)
    else:
        list_admins()
