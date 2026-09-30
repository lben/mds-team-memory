#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build the frontend for Update, without changing the shipped model package.

    Build.cmd            # reuse verified current UI, otherwise install/build
    Build.cmd --force    # always run npm ci and npm run build
"""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploylib import ROOT
from deployment_package import checked_package, local_build_pointer, verify_ui
from ml_bundle import record
from ui_sources import POLICY, frontend_sources


def npm_environment():
    environment = os.environ.copy()
    environment["NODE_USE_SYSTEM_CA"] = "1"
    return environment


@contextmanager
def build_lock(root):
    managed = root / "build"
    managed.mkdir(exist_ok=True)
    if not managed.resolve().is_relative_to(root.resolve()):
        raise ValueError("The build directory must remain inside the checkout")
    path = managed / "update-ui.lock"
    if path.is_symlink():
        raise ValueError("The UI build lock must not be a symlink")
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Another Build command is running in this checkout") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def build(root: Path, force=False):
    with build_lock(root):
        _build(root, force)


def _build(root: Path, force=False):
    # First validate the non-UI package and shipped assets. A changed Python/ML
    # lock requires a full offline package, not a UI build that blesses it.
    if not force:
        try:
            checked_package(root, verify_models=False)
        except (ValueError, OSError, KeyError, TypeError) as error:
            print(f"Preparing a fresh UI: {error}", flush=True)
        else:
            print("Build ready: UI already matches this checkout. Run Update.cmd [UAT|PROD].", flush=True)
            return
    checked_package(root, use_local=False, check_sources=False, verify_models=False)
    node = shutil.which("node")
    npm = shutil.which("npm.cmd") if os.name == "nt" else shutil.which("npm")
    if not node or not npm:
        raise ValueError("Building changed UI requires Node.js 22.19+ (or 24.6+) and npm on PATH. "
                         "An unchanged packaged UI needs neither.")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"Cannot determine Node.js version: {version}")
    major, minor, patch = map(int, match.groups())
    if not ((major == 22 and (minor, patch) >= (19, 0)) or (major == 24 and minor >= 6) or major > 24):
        raise ValueError(f"Node.js {version} is unsupported for Build; use Node.js 22.19+ or 24.6+.")
    sources = frontend_sources(root)
    base = record(root / "deployment/package.json")
    environment = npm_environment()
    for args in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
        print("Running npm " + " ".join(args) + "...", flush=True)
        subprocess.run([npm, *args], cwd=root / "frontend", env=environment, check=True)
    if frontend_sources(root) != sources or record(root / "deployment/package.json") != base:
        raise ValueError("Sources or deployment package changed during the build. Run Build again.")

    managed = root / "build"
    managed.mkdir(exist_ok=True)
    if not managed.resolve().is_relative_to(root.resolve()):
        raise ValueError("The build directory must remain inside the checkout")
    generations = managed / "update-ui"
    generations.mkdir(exist_ok=True)
    if not generations.resolve().is_relative_to(managed.resolve()):
        raise ValueError("UI build generations must remain inside the build directory")
    generation = uuid.uuid4().hex
    stage = generations / generation
    pointer_tmp = None
    published = False
    try:
        shutil.copytree(root / "frontend/dist", stage / "ui", symlinks=True)
        ui_files = {f"ui/{p.relative_to(stage / 'ui').as_posix()}": record(p)
                    for p in sorted((stage / "ui").rglob("*")) if p.is_file()}
        verify_ui(stage, ui_files)
        local = {"version": 1, "generation": generation, "base_package": base,
                 "frontend_source_policy": POLICY, "frontend_sources": sources, "ui_files": ui_files}
        # Publish only a complete verified generation. Existing readers and
        # the last successful build remain intact on failure/interruption.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=managed, prefix="update-ui-", suffix=".tmp", delete=False) as output:
            pointer_tmp = Path(output.name)
            json.dump(local, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(pointer_tmp, local_build_pointer(root))
        published = True
    finally:
        if pointer_tmp is not None:
            pointer_tmp.unlink(missing_ok=True)
        if not published and stage.exists():
            shutil.rmtree(stage)
    print("Build ready: verified UI and manifest saved under build/. Run Update.cmd [UAT|PROD].", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="reinstall locked npm dependencies and rebuild even if current")
    args = parser.parse_args(argv)
    try:
        build(ROOT, args.force)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
