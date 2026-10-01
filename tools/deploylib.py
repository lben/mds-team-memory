"""Configuration and SSH plumbing shared by tools/deploy.py and tools/serverctl.py."""

import shlex
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "tools" / "deploy.toml"
TARGETS = ("uat", "prod")
DEFAULT_TARGET = "uat"


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _whole_number(target: str, key: str, value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        fail(f"[{target}] {key} must be a number, not {value!r}")


class Target:
    """One deployment environment, as configured in tools/deploy.toml."""

    def __init__(self, name: str, settings: dict):
        self.name = name
        self.host = str(settings.get("host", "")).strip()
        self.root = str(settings.get("root", "")).strip()
        self.port = _whole_number(name, "port", settings.get("port", 8000))
        self.bind = str(settings.get("bind", "0.0.0.0"))
        self.uv = str(settings.get("uv", "uv"))
        self.python = str(settings.get("python", "3.12"))
        self.keep_releases = _whole_number(name, "keep_releases", settings.get("keep_releases", 5))
        options = settings.get("ssh_options", [])
        if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
            fail(f"[{name}] ssh_options must be an array of strings")
        self.ssh_options = options
        self.ssh_port = _whole_number(name, "ssh_port", settings.get("ssh_port", 22))
        self.bash = str(settings.get("bash", "bash"))
        self.session = None
        self.env = {str(k): str(v) for k, v in dict(settings.get("env", {})).items()}

        if not self.host:
            fail(f"[{name}] in {CONFIG_PATH} needs a host, e.g. host = \"deployer@uat.example.com\"")
        if not 1 <= self.port <= 65535 or not 1 <= self.ssh_port <= 65535:
            fail(f"[{name}] port and ssh_port must be between 1 and 65535")
        if not self.root.startswith("/"):
            fail(f"[{name}] root must be an absolute path on the server, e.g. /home/deployer/apps/mds-uat")
        # Remote commands are sent as one shell string, and the deploy writes
        # app.env by hand, so a root or an environment value carrying shell
        # syntax would be a quoting hazard rather than a working setting.
        if any(c.isspace() or c in "'\"$`\\" for c in self.root):
            fail(f"[{name}] root must not contain spaces or shell characters: {self.root}")
        for key, value in self.env.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\n" in value or "\r" in value:
                fail(f"[{name}] env entry {key!r} is not a usable shell variable")

    @property
    def ctl_path(self) -> str:
        return f"{self.root}/mdsctl.sh"


def load_target(name: str) -> Target:
    if not CONFIG_PATH.exists():
        fail(
            f"{CONFIG_PATH} not found. Copy tools/deploy.example.toml to tools/deploy.toml "
            "and fill in your UAT and PROD servers."
        )
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if name not in config:
        fail(f"no [{name}] section in {CONFIG_PATH}")
    return Target(name, config[name])


def add_target_argument(parser) -> None:
    parser.add_argument(
        "target",
        nargs="?",
        default=DEFAULT_TARGET,
        type=str.lower,
        choices=TARGETS,
        help=f"which server to act on (default: {DEFAULT_TARGET})",
    )


def ssh(target: Target, argv: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """Run one command on the server. argv is quoted for the remote shell."""
    remote = " ".join(shlex.quote(a) for a in argv)
    if target.session is not None:
        return target.session.run(remote, capture=capture)
    return _run(["ssh", "-o", f"Port={target.ssh_port}", *target.ssh_options, target.host, remote], capture=capture)


def ctl(target: Target, *argv: str, capture: bool = False) -> subprocess.CompletedProcess:
    """Run one mdsctl.sh command on the server."""
    command = [target.bash, target.ctl_path, *argv]
    pending = getattr(target, "pending_env", None)
    if pending and argv[0] in {"unpack", "setup", "compatible", "migrate", "activate"}:
        command = ["env", f"MDS_ENV_FILE={pending}", *command]
    return ssh(target, command, capture=capture)


def scp(target: Target, local: Path, remote_path: str) -> subprocess.CompletedProcess:
    if target.session is not None:
        return target.session.put(local, remote_path)
    # Sent as a bare filename from its own directory: scp splits host:path on the
    # first colon, which a Windows drive letter would otherwise trip over.
    return _run(
        ["scp", "-o", f"Port={target.ssh_port}", *target.ssh_options, local.name, f"{target.host}:{remote_path}"],
        capture=True,
        cwd=str(local.parent),
    )


def _run(command: list[str], capture: bool, cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=capture, text=True, cwd=cwd)
    except FileNotFoundError:
        fail(f"{command[0]} not found on this machine; it is needed to reach the server")


def local_sqlite_filesystem(directory: Path) -> str:
    """Require the deployment's supported local storage before opening SQLite."""
    directory = directory.expanduser().resolve()
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        fields, metadata = line.split(" - ", 1)
        mount = fields.split()[4].replace("\\040", " ").replace("\\134", "\\")
        if directory.is_relative_to(mount):
            mounts.append((len(mount), metadata.split()[0]))
    filesystem = max(mounts)[1] if mounts else "unknown"
    if filesystem not in {"ext2", "ext3", "ext4", "xfs", "btrfs", "overlay", "tmpfs", "zfs"}:
        raise ValueError(f"ML needs local SQLite storage; unsupported filesystem {filesystem}")
    return filesystem


def ml_preflight(data_dir: Path) -> dict:
    """Reject an unsupported ML host before allocating or starting a worker."""
    import os
    import platform
    import resource
    import shutil
    import sqlite3

    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("ML requires Linux x86_64")
    release = platform.freedesktop_os_release()
    if release.get("ID") != "rhel" or release.get("VERSION_ID") != "8.10":
        raise ValueError("The offline bundle is approved only for RHEL/UBI 8.10")
    libc, version = platform.libc_ver()
    if libc != "glibc" or tuple(map(int, version.split("."))) < (2, 28):
        raise ValueError("ML requires glibc 2.28 or newer")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("Install an approved Python 3.12 before offline setup")
    if sqlite3.sqlite_version_info < (3, 51, 3):
        raise ValueError(f"ML requires SQLite 3.51.3+; Python has {sqlite3.sqlite_version}")
    cpus = sorted(os.sched_getaffinity(0))
    if not cpus:
        raise ValueError("No permitted CPU affinity")
    cpuinfo = Path("/proc/cpuinfo").read_text()
    flags = next((line.split(":", 1)[1].split() for line in cpuinfo.splitlines() if line.startswith("flags")), [])
    if "sse2" not in flags:
        raise ValueError("The CPU must support SSE2")
    database_url = os.environ.get("MDS_DATABASE_URL")
    database = (data_dir / "mds.sqlite3").expanduser().resolve()
    if database_url:
        if not database_url.startswith("sqlite:///") or "?" in database_url or database_url.endswith(":memory:"):
            raise ValueError("ML requires a local SQLite file URL without query options")
        if Path(database_url[len("sqlite:///"):]).expanduser().resolve() != database:
            raise ValueError("ML deployment requires MDS_DATABASE_URL to use MDS_DATA_DIR/mds.sqlite3 for backup and reset")
    data_dir = database.parent
    filesystem = local_sqlite_filesystem(data_dir)
    if shutil.disk_usage(data_dir).free < 2 * 1024**3:
        raise ValueError("ML requires at least 2 GiB filesystem free space")
    return {"os": release.get("PRETTY_NAME"), "glibc": version, "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "cpu_flags": flags, "allowed_cpus": cpus,
            "worker_cpus": cpus[:4], "data_filesystem": filesystem,
            "free_bytes": shutil.disk_usage(data_dir).free,
            "address_space_limit": resource.getrlimit(resource.RLIMIT_AS),
            "process_limit": resource.getrlimit(resource.RLIMIT_NPROC)}


def ml_generation(generation: Path, release: Path) -> dict:
    import hashlib
    import json
    from ml_bundle import model_files, record, validate_manifest

    if generation.is_symlink() or not generation.is_dir():
        raise ValueError("MDS_ML_GENERATION must name a prepared generation directory")
    manifest = json.loads((generation / "bundle.json").read_text())
    validate_manifest(manifest)
    if manifest.get("target") != {"os": "RHEL 8.10", "architecture": "x86_64", "python": "3.12", "device": "cpu"}:
        raise ValueError("The generation targets a different server runtime")
    for entry in manifest["files"]:
        path = generation / entry["path"]
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent.is_relative_to(generation)):
            raise ValueError("Prepared generation files must not use symlinks")
        if record(path) != {"size": entry["size"], "sha256": entry["sha256"]}:
            raise ValueError(f"Prepared generation checksum mismatch: {entry['path']}")
    expected_files = {entry["path"] for entry in manifest["files"]} | {"bundle.json"}
    actual_files = {path.relative_to(generation).as_posix() for path in generation.rglob("*") if not path.is_dir()}
    if actual_files != expected_files:
        raise ValueError("Prepared generation contains unexpected or missing files")
    model_files(generation / "models")
    lock = (generation / "requirements-linux.lock").read_bytes()
    if lock != (release / "tools/ml-container/requirements-linux.lock").read_bytes():
        raise ValueError("The prepared dependency lock does not match this release")
    return {"generation": str(generation), "generation_sha256": hashlib.sha256((generation / "bundle.json").read_bytes()).hexdigest(),
            "lock_sha256": hashlib.sha256(lock).hexdigest()}


def ml_setup(root: Path, release: Path, generation: Path, managed_root: Path, uv: str) -> None:
    import hashlib
    import json
    import os
    import shutil
    import zipfile
    from ml_storage import allocation

    data_dir = Path(os.environ.get("MDS_DATA_DIR", root / "data"))
    ml_preflight(data_dir)
    generation, managed_root = generation.absolute(), managed_root.resolve()
    if not generation.resolve().is_relative_to(managed_root):
        raise ValueError("Prepared generations and transfer staging must be inside MDS_ML_ROOT")
    with allocation(managed_root) as budget:
        state = ml_generation(generation, release)
        fingerprint = hashlib.sha256((state["lock_sha256"] + sys.version + str(Path(sys.executable).resolve())).encode()).hexdigest()
        runtime = managed_root / "runtimes" / fingerprint
        state["runtime"] = str(runtime)
        marker = runtime / "runtime.json"
        expected = {"lock_sha256": state["lock_sha256"], "python": str(Path(sys.executable).resolve()), "version": sys.version}
        wheels = generation / "wheels"
        expanded = 0
        for wheel in wheels.glob("*.whl"):
            with zipfile.ZipFile(wheel) as archive:
                expanded += sum(entry.file_size for entry in archive.infolist())
        if not expanded:
            raise ValueError("Prepared generation has no wheels")
        # uv may hold an extracted wheel and its installed copy at once. Include
        # both plus metadata before starting the bounded, offline allocation.
        budget.check((2 if not marker.exists() else 1) * expanded + 64 * 1024**2)
        temporary = managed_root / ".install-tmp"
        temporary.mkdir(exist_ok=True)
        env = {**os.environ, "UV_PYTHON_DOWNLOADS": "never", "UV_OFFLINE": "1", "UV_NO_CACHE": "1",
               "UV_LINK_MODE": "copy", "TMPDIR": str(temporary), "PYTHONDONTWRITEBYTECODE": "1",
               "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}

        def run(*args):
            subprocess.run([uv, *map(str, args)], check=True, env=env, cwd=release)

        try:
            if marker.exists():
                if json.loads(marker.read_text()) != expected or not (runtime / "bin/python").is_file():
                    raise ValueError("The shared ML runtime does not match its recorded lock/interpreter")
            else:
                if runtime.exists():
                    raise ValueError(f"Incomplete ML runtime: {runtime}; remove it only after checking release references")
                runtime.parent.mkdir(parents=True, exist_ok=True)
                try:
                    run("venv", "--offline", "--no-python-downloads", "--python", sys.executable, runtime)
                    run("pip", "sync", "--python", runtime / "bin/python", "--offline", "--no-index", "--no-cache",
                        "--find-links", wheels, "--only-binary", ":all:", "--require-hashes", generation / "requirements-linux.lock")
                    budget.check()
                    marker.write_text(json.dumps(expected, sort_keys=True) + "\n")
                except BaseException:
                    shutil.rmtree(runtime)
                    raise
            run("venv", "--offline", "--no-python-downloads", "--python", sys.executable, release / ".venv")
            run("pip", "install", "--python", release / ".venv/bin/python", "--offline", "--no-index", "--no-cache",
                "--find-links", wheels, "--only-binary", ":all:", "-r", release / "requirements.txt")
            # A worker can acquire its lease before lazily loading models.
            # Check native libraries before stopping the serving release.
            subprocess.run([str(runtime / "bin/python"), "-c",
                            "import torch, spacy; from gliner2 import AutoExtractor; "
                            "from sentence_transformers import SentenceTransformer; "
                            "assert torch.version.cuda is None; print('Offline ML imports: OK')"],
                           check=True, env=env, cwd=release)
            budget.check()
            link = release / ".ml-venv"
            link.unlink(missing_ok=True)
            link.symlink_to(runtime, target_is_directory=True)
            staged = release / ".ml-runtime.json.tmp"
            staged.write_text(json.dumps(state, sort_keys=True) + "\n")
            staged.replace(release / "ml-runtime.json")
        finally:
            shutil.rmtree(temporary)
    print("Separate offline web and ML environments ready")


def ml_runtime(release: Path) -> str:
    """Read the release pin; a later app.env change must not change rollback."""
    import hashlib
    import json

    state = json.loads((release / "ml-runtime.json").read_text())
    generation, runtime = Path(state["generation"]), Path(state["runtime"])
    for path, key in ((generation / "bundle.json", "generation_sha256"),
                      (generation / "requirements-linux.lock", "lock_sha256")):
        if hashlib.sha256(path.read_bytes()).hexdigest() != state[key]:
            raise ValueError("The release's pinned ML generation has changed")
    if json.loads((runtime / "runtime.json").read_text())["lock_sha256"] != state["lock_sha256"]:
        raise ValueError("The release's ML runtime no longer matches its lock")
    if (release / ".ml-venv").resolve() != runtime.resolve():
        raise ValueError("The release's ML interpreter has changed")
    return str(generation / "models")


if __name__ == "__main__":
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="Local offline deployment helpers")
    parser.add_argument("command", choices=("ml-setup", "ml-runtime", "preflight"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--generation", type=Path)
    parser.add_argument("--managed-root", type=Path)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    try:
        if args.command == "ml-setup":
            if args.generation is None or args.managed_root is None:
                parser.error("ml-setup requires --generation and --managed-root")
            ml_setup(args.root, args.release, args.generation, args.managed_root, args.uv)
        elif args.command == "ml-runtime":
            print(ml_runtime(args.release))
        else:
            print(json.dumps(ml_preflight(Path(os.environ.get("MDS_DATA_DIR", args.root / "data"))), sort_keys=True))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"ML deployment: {error}\n")
