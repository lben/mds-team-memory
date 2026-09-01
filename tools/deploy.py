#!/usr/bin/env python3
"""Deploy MDS Team Knowledge to the UAT (default) or PROD server.

Builds the frontend here on the machine you deploy from, assembles a release
the server can run without Node, uploads it over SSH, creates its Python 3.12
environment with uv, migrates the shared database, and swaps the server onto
it. If the new release does not answer /api/health, the previous is put back.

Usage:
    uv run --python 3.12 tools/deploy.py                  # uat
    uv run --python 3.12 tools/deploy.py prod
    uv run --python 3.12 tools/deploy.py uat --skip-build

Nothing here needs root on the server. Configure the servers in
tools/deploy.toml (see tools/deploy.example.toml).
"""

import argparse
import shlex
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploylib import ROOT, Target, add_target_argument, ctl, fail, load_target, scp, ssh  # noqa: E402

BUILD_DIR = ROOT / "build"
RELEASE_DIR = BUILD_DIR / "release"
CTL_SCRIPT = ROOT / "tools" / "mdsctl.sh"

RELEASE_ITEMS = [
    ("backend/app", "backend/app"),
    ("backend/alembic", "backend/alembic"),
    ("backend/alembic.ini", "backend/alembic.ini"),
    ("frontend/dist", "frontend/dist"),
    ("manage.py", "manage.py"),
    ("requirements.txt", "requirements.txt"),
    ("SERVER_SETUP.md", "SERVER_SETUP.md"),
]


# How far a deploy got, which decides what recovery has to undo.
UNTOUCHED, STOPPED, MIGRATED, SWAPPED = "untouched", "stopped", "migrated", "swapped"


class DeployError(Exception):
    """A remote step failed; main() decides what to restore."""


def run_step(description: str, result: subprocess.CompletedProcess) -> None:
    if result.returncode == 0:
        return
    # Captured output (scp) would otherwise be lost, leaving the operator with a
    # step name and no reason — and this is the step most likely to fail first.
    for stream in (result.stdout, result.stderr):
        if stream:
            print(stream.rstrip(), file=sys.stderr)
    raise DeployError(description)


def confirm_production(target: Target, assume_yes: bool) -> None:
    if target.name != "prod" or assume_yes:
        return
    if not sys.stdin.isatty():
        fail("deploying to prod without a terminal needs --yes")
    print(f"About to deploy to PROD: {target.host}:{target.root}")
    if input("Type 'prod' to continue: ").strip() != "prod":
        fail("cancelled")


def build_frontend() -> None:
    print("Building frontend (npm run build)...")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        fail("npm not found on this machine; deploys must run from the dev machine")
    if subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend").returncode != 0:
        fail("the frontend build failed; nothing was sent to the server")


def assemble_release(target: Target, stamp: str) -> None:
    print(f"Assembling release in {RELEASE_DIR}...")
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
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    (RELEASE_DIR / "RELEASE.txt").write_text(
        f"target: {target.name}\nrelease: {stamp}\nbuilt: {built}\n", encoding="utf-8", newline="\n"
    )


def pack_release(stamp: str) -> Path:
    """One archive uploads far faster over SSH than several hundred files."""
    archive = BUILD_DIR / f"{stamp}.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        for entry in sorted(RELEASE_DIR.iterdir()):
            tar.add(entry, arcname=entry.name)
    return archive


def write_env_file(target: Target) -> Path:
    """The settings mdsctl.sh reads on the server, as a sourceable shell file."""
    values = {
        "PORT": str(target.port),
        "BIND": target.bind,
        "UV": target.uv,
        "PYTHON_VERSION": target.python,
        "KEEP": str(target.keep_releases),
        # Every release gets its own directory, so the data has to live outside
        # them or a deploy would silently start on an empty database.
        "MDS_DATA_DIR": f"{target.root}/data",
        **target.env,
    }
    path = BUILD_DIR / "app.env"
    lines = [f"# Written by tools/deploy.py for {target.name}; replaced by the next deploy."]
    lines += [f"{key}={shlex.quote(value)}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def normalised_ctl_script() -> Path:
    """A copy with LF endings and UTF-8: a Windows checkout can hold the script
    as CRLF, which the RedHat shell rejects on its very first line, and the
    default encoding there is the locale codepage, which would rewrite anything
    non-ASCII in the script into mojibake on its way to the server."""
    copy = BUILD_DIR / "mdsctl.sh"
    copy.write_text(CTL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return copy


def upload(target: Target, archive: Path, env_file: Path, stamp: str) -> None:
    print(f"Uploading to {target.host}:{target.root}...")
    run_step(
        "creating the server directories",
        ssh(target, ["mkdir", "-p", f"{target.root}/releases", f"{target.root}/logs", f"{target.root}/run"]),
    )
    run_step("uploading mdsctl.sh", scp(target, normalised_ctl_script(), target.ctl_path))
    run_step("uploading app.env", scp(target, env_file, f"{target.root}/app.env"))
    run_step("uploading the release", scp(target, archive, f"{target.root}/releases/{stamp}.tar.gz"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_target_argument(parser)
    parser.add_argument("--skip-build", action="store_true", help="reuse the existing frontend/dist")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt when deploying to prod")
    args = parser.parse_args()

    # Keep this tool's progress lines in order with the remote output when the
    # deploy is piped to a log rather than watched in a terminal.
    sys.stdout.reconfigure(line_buffering=True)

    target = load_target(args.target)
    confirm_production(target, args.yes)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(f"Deploying release {stamp} to {target.name} ({target.host}:{target.root})")

    if not args.skip_build:
        build_frontend()
    assemble_release(target, stamp)
    archive = pack_release(stamp)
    env_file = write_env_file(target)

    # What has to be undone if a step fails depends on how far the deploy got.
    reached = UNTOUCHED
    try:
        upload(target, archive, env_file, stamp)
        run_step("unpacking the release", ctl(target, "unpack", stamp))
        # The environment is built while the old release keeps serving; only
        # the migration and the swap need the server down.
        run_step("installing dependencies with uv", ctl(target, "setup", stamp))
        reached = STOPPED
        run_step("stopping the server", ctl(target, "stop"))
        run_step("backing up the database", ctl(target, "backup", stamp))
        # From here the database may have moved on even if the code does not:
        # alembic applies revisions one at a time and does not undo the ones
        # that succeeded before a later one failed.
        reached = MIGRATED
        run_step("running database migrations", ctl(target, "migrate", stamp))
        run_step("activating the release", ctl(target, "activate", stamp))
        reached = SWAPPED
        run_step("starting the server", ctl(target, "start"))
    except DeployError as error:
        print(f"\nDeploy failed while {error}.", file=sys.stderr)
        recover(target, reached)
        raise SystemExit(1)
    finally:
        archive.unlink(missing_ok=True)

    ctl(target, "prune")
    print(f"\nDeployed {stamp} to {target.name}.")
    ctl(target, "status")
    print(
        "\nIf this is the first deploy, create the first administrator:\n"
        f"  ssh -t {target.host} bash {target.ctl_path} manage create-admin"
    )


def recover(target: Target, reached: str) -> None:
    """Put the server back the way this deploy found it."""
    if reached == UNTOUCHED:
        print("The running server was never touched; it is still serving.", file=sys.stderr)
        return
    if reached == SWAPPED:
        restored = ctl(target, "rollback").returncode == 0
    else:
        # Nothing was swapped, so the old release is still the current one.
        restored = ctl(target, "start").returncode == 0
    if reached in (MIGRATED, SWAPPED):
        print(
            f"The database may have been migrated; the copy taken beforehand is in "
            f"{target.root}/backups/. Restoring it is described in SERVER_SETUP.md.",
            file=sys.stderr,
        )
    if restored:
        print("Restored the previous release; it is serving again.", file=sys.stderr)
        return
    print(
        "Could not restore automatically - nothing is serving.\n"
        "  (on a first deploy there is no earlier release to fall back to)\n"
        f"  python tools/serverctl.py logs {target.name}      # what went wrong\n"
        f"  python tools/serverctl.py releases {target.name}  # what is available\n"
        f"  python tools/serverctl.py start {target.name}     # try again once fixed",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
