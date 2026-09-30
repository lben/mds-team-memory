#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["paramiko==4.0.0"]
# ///
"""Update UAT (default) or PROD from this checkout using one password prompt.

    uv run tools/update.py
    uv run tools/update.py PROD
    uv run tools/update.py --check

Configure both servers in tools/deploy.toml. The checked-in deployment package
contains the built UI, split models, offline Linux wheels, Python and uv.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deploy
from deploylib import CONFIG_PATH, ROOT, Target, add_target_argument, fail, scp, ssh
from ml_assets import manifest_at, stream_parts
from ml_bundle import record
from update_session import Session


def checked_package():
    directory = ROOT / "deployment"
    release = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    if release.get("version") != 1:
        raise ValueError("Unsupported deployment package version")
    manifest_path = directory / "offline" / "manifest.json"
    manifest = manifest_at(manifest_path)
    if record(manifest_path) != release["transfer_manifest"]:
        raise ValueError("The transfer manifest differs from the release package")
    if set(manifest["metadata"].get("model_roles", [])) != {"extractor", "embeddings", "syntax"}:
        raise ValueError("The offline package must contain the three required BGE model roles")
    print("Checking local model parts and deployment package...")
    stream_parts(manifest_path, manifest)
    for name, expected in release["files"].items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or record(path) != expected:
            raise ValueError(f"Deployment package checksum mismatch: {name}")
    ui_files = {f"ui/{p.relative_to(directory / 'ui').as_posix()}" for p in (directory / "ui").rglob("*") if p.is_file()}
    if ui_files != {name for name in release["files"] if name.startswith("ui/")}:
        raise ValueError("Bundled UI contains missing or unexpected files")
    for name, expected in release["frontend_sources"].items():
        if record(ROOT / name) != expected:
            raise ValueError(f"Bundled UI is stale for {name}; regenerate the deployment package")
    source_names = {p.relative_to(ROOT).as_posix() for p in (ROOT / "frontend").rglob("*")
                    if p.is_file() and not any(x in p.relative_to(ROOT / "frontend").parts for x in ("node_modules", "dist"))
                    and p.suffix != ".tsbuildinfo" and p.name != ".DS_Store"}
    if source_names != set(release["frontend_sources"]):
        raise ValueError("Bundled UI is stale: frontend source files were added or removed")
    if record(ROOT / "tools/ml-container/requirements-linux.lock") != release["dependency_lock"]:
        raise ValueError("The offline dependency lock differs from this checkout")
    return directory, release, manifest


def configured_target(name, config_path):
    config = tomllib.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    if name not in config:
        raise ValueError(f"No [{name}] section in {config_path}")
    settings = config[name]
    target = Target(name, settings)
    other = config.get("prod" if name == "uat" else "uat", {})
    if target.host == other.get("host"):
        if target.root == other.get("root"):
            raise ValueError("UAT and PROD on the same host must have distinct deployment roots")
        if target.port == int(other.get("port", 8000)):
            raise ValueError("UAT and PROD on the same host must have distinct application ports")
    if "@" not in target.host or not all(target.host.rsplit("@", 1)):
        raise ValueError(f"[{name}] host must include username, e.g. deployer@192.0.2.10")
    if settings.get("ssh_options"):
        raise ValueError("Update uses SSH/SFTP directly; configure ssh_port and host_key_sha256 instead of ssh_options")
    target.host_key_sha256 = str(settings.get("host_key_sha256", ""))
    # Bundled executables are installed before deploy.py writes app.env.
    target.uv = str(settings.get("uv", "bundled"))
    target.python = str(settings.get("python", "bundled"))
    local = config.get("local", {})
    known_hosts = Path(local.get("known_hosts", "build/update-known-hosts"))
    if not known_hosts.is_absolute():
        known_hosts = ROOT / known_hosts
    return target, known_hosts


def checked_step(target, description, argv):
    print(description.capitalize() + "...", flush=True)
    deploy.run_step(description, ssh(target, argv))


def bootstrap(target, directory, package):
    spec = package["files"]["bootstrap.tar.gz"]
    destination = f"{target.root}/ml/bootstrap/{spec['sha256']}"
    archive = f"{target.root}/bootstrap.tar.gz"
    # Only core Linux utilities are needed before our Python is available.
    script = 'set -eu; test "$(uname -m)" = x86_64; command -v tar; command -v sha256sum; mkdir -p "$1/ml" "$1/data" "$1/run" "$1/logs"; test "$(df -Pk "$1" | tail -1 | awk \'{print $4}\')" -ge 3145728'
    checked_step(target, "checking server utilities and bootstrap space", [target.bash, "-c", script, "update", target.root])
    if target.uv == "bundled" or target.python == "bundled":
        probe = ssh(target, ["test", "-x", f"{destination}/python/bin/python3.12"], capture=True)
        if probe.returncode != 0:
            deploy.run_step("uploading bundled Python and uv", scp(target, directory / "bootstrap.tar.gz", archive))
            script = 'set -eu; test "$(sha256sum "$1" | cut -d\' \' -f1)" = "$2"; mkdir -p "$3"; tar --no-same-owner -xzf "$1" -C "$3"; rm "$1"; test -x "$3/uv"; test -x "$3/python/bin/python3.12"'
            checked_step(target, "installing bundled Python and uv", [target.bash, "-c", script, "update", archive, spec["sha256"], destination])
        if target.uv == "bundled":
            target.uv = f"{destination}/uv"
        if target.python == "bundled":
            target.python = f"{destination}/python/bin/python3.12"
    checked_step(target, "checking Python and uv", [target.python, "-c", "import sys, sqlite3; assert sys.version_info[:2] == (3,12); assert sqlite3.sqlite_version_info >= (3,51,3); print(sys.version, 'SQLite', sqlite3.sqlite_version)"])
    checked_step(target, "checking uv", [target.uv, "--version"])


def model_preparer(directory, package, manifest):
    def prepare(target, stamp):
        managed = f"{target.root}/ml"
        generation = target.env["MDS_ML_GENERATION"]
        incoming = f"{managed}/incoming/{manifest['archive']['sha256']}"
        remote_manifest = f"{incoming}/manifest.json"
        base = [target.python, f"{target.root}/releases/{stamp}/tools/update_remote.py"]
        options = ["--root", target.root, "--release", f"{target.root}/releases/{stamp}",
                   "--generation", generation, "--manifest", remote_manifest]
        result = ssh(target, [*base, "probe", *options], capture=True)
        if result.returncode == 0:
            print("Server already has this verified model generation; no model upload needed.")
            return
        if result.returncode != 2:
            deploy.run_step("checking existing model generation", result)
        # Bound both assembly's duplicate compressed bytes and extraction's
        # compressed+expanded bytes before transferring a multi-gigabyte bundle.
        required = max(2 * manifest["archive"]["size"], manifest["archive"]["size"] + package["bundle_total_size"] + 8 * 1024**2)
        checked_step(target, "reserving offline transfer space", [*base, "reserve", *options, "--bytes", str(required)])
        checked_step(target, "creating model transfer directory", ["mkdir", "-p", incoming])
        deploy.run_step("uploading model manifest", scp(target, directory / "offline/manifest.json", remote_manifest))
        count = len(manifest["parts"])
        for index, part in enumerate(manifest["parts"], 1):
            print(f"Uploading model/dependency part {index}/{count} ({part['size'] / 1_000_000:.1f} MB)...")
            deploy.run_step(f"uploading {part['name']}", scp(target, directory / "offline" / part["name"], f"{incoming}/{part['name']}"))
        checked_step(target, "joining, verifying and extracting the offline models", [*base, "prepare", *options])
    return prepare


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_target_argument(parser)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="single TOML configuration file")
    parser.add_argument("--check", action="store_true", help="verify configuration and all local assets without connecting")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    target = None
    try:
        target, known_hosts = configured_target(args.target, args.config)
        directory, package, manifest = checked_package()
        if args.check:
            print(f"Ready locally for {target.name.upper()}: {len(manifest['parts'])} parts; {manifest['archive']['size'] / 1024**3:.2f} GiB. No connection made.")
            return 0
        managed = f"{target.root}/ml"
        reserved = {"MDS_ML_GENERATION": f"{managed}/generations/{manifest['archive']['sha256']}",
                    "MDS_ML_ROOT": managed, "MDS_DATA_DIR": f"{target.root}/data"}
        if any(key in target.env for key in reserved):
            raise ValueError("Update manages MDS_DATA_DIR, MDS_ML_ROOT and MDS_ML_GENERATION; remove these from [target.env]")
        target.env.update(reserved)
        target.env.setdefault("MDS_ML_LOAD_TIMEOUT_SECONDS", "300")
        print(f"Updating {target.name.upper()} at {target.host}:{target.root}")
        target.session = Session(target, known_hosts)
        bootstrap(target, directory, package)
        deploy.deploy_release(target, skip_build=True, assume_yes=True, frontend=directory / "ui",
                              prepare=model_preparer(directory, package, manifest))
        print(f"Web server and ML worker are running. Open http://{target.host.rsplit('@',1)[1]}:{target.port}")
        return 0
    except Exception as error:
        print(f"Update failed: {error}", file=sys.stderr)
        return 1
    finally:
        if target is not None and target.session is not None:
            target.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
