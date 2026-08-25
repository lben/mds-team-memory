#!/usr/bin/env python3
"""Deploy MDS Team Memory to a UAT or PROD server.

Builds the frontend on this (dev) machine, assembles a self-contained release
(backend + compiled dist), and pushes it to the configured destination.
The server never needs Node: it only needs Python 3.12+.

Usage:
    python tools/deploy.py uat
    python tools/deploy.py prod [--skip-build]

Configure destinations in tools/deploy.toml (see tools/deploy.example.toml).
A destination is either a directory path (local folder or mounted network
share, including Windows UNC paths) or "user@host:/path" for scp over SSH.
"""

import argparse
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "tools" / "deploy.toml"
RELEASE_DIR = ROOT / "build" / "release"

RELEASE_ITEMS = [
    ("backend/app", "backend/app"),
    ("backend/alembic", "backend/alembic"),
    ("backend/alembic.ini", "backend/alembic.ini"),
    ("frontend/dist", "frontend/dist"),
    ("requirements.txt", "requirements.txt"),
    ("SERVER_SETUP.md", "SERVER_SETUP.md"),
]


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_destination(target: str) -> str:
    if not CONFIG_PATH.exists():
        fail(
            f"{CONFIG_PATH} not found. Copy tools/deploy.example.toml to tools/deploy.toml "
            "and set your UAT/PROD destinations."
        )
    config = tomllib.loads(CONFIG_PATH.read_text())
    if target not in config or "destination" not in config[target]:
        fail(f"No [{target}] destination configured in {CONFIG_PATH}")
    return config[target]["destination"]


def build_frontend() -> None:
    print("Building frontend (npm run build)…")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        fail("npm not found on this machine; deploys must run from the dev machine")
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)


def assemble_release(target: str) -> None:
    print(f"Assembling release in {RELEASE_DIR}…")
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    for source, destination in RELEASE_ITEMS:
        src = ROOT / source
        dst = RELEASE_DIR / destination
        if not src.exists():
            fail(f"missing {src} (build the frontend first?)")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (RELEASE_DIR / "RELEASE.txt").write_text(f"target: {target}\nbuilt: {stamp}\n")


def push(destination: str) -> None:
    if ":" in destination and not Path(destination.split(":", 1)[0]).drive and "@" in destination:
        print(f"Copying release to {destination} via scp…")
        subprocess.run(
            ["scp", "-r", *[str(p) for p in RELEASE_DIR.iterdir()], destination], check=True
        )
    else:
        dest = Path(destination)
        print(f"Copying release to {dest}…")
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(RELEASE_DIR, dest, dirs_exist_ok=True)
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["uat", "prod"])
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing frontend/dist")
    args = parser.parse_args()

    destination = load_destination(args.target)
    if not args.skip_build:
        build_frontend()
    assemble_release(args.target)
    push(destination)
    print(
        "\nNext steps on the server (first deploy only — see SERVER_SETUP.md):\n"
        "  1. python -m venv .venv && install requirements.txt\n"
        "  2. every deploy: .venv/bin/alembic -c backend/alembic.ini upgrade head  (data is preserved)\n"
        "  3. restart:      .venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000"
    )


if __name__ == "__main__":
    main()
