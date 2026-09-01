"""Configuration and SSH plumbing shared by tools/deploy.py and tools/serverctl.py."""

import shlex
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
        self.ssh_options = [str(o) for o in settings.get("ssh_options", [])]
        self.env = {str(k): str(v) for k, v in dict(settings.get("env", {})).items()}

        if not self.host:
            fail(f"[{name}] in {CONFIG_PATH} needs a host, e.g. host = \"deployer@uat.example.com\"")
        if not self.root.startswith("/"):
            fail(f"[{name}] root must be an absolute path on the server, e.g. /home/deployer/apps/mds-uat")
        # Remote commands are sent as one shell string, and the deploy writes
        # app.env by hand, so a root or an environment value carrying shell
        # syntax would be a quoting hazard rather than a working setting.
        if any(c.isspace() or c in "'\"$`\\" for c in self.root):
            fail(f"[{name}] root must not contain spaces or shell characters: {self.root}")
        for key, value in self.env.items():
            if not key.replace("_", "").isalnum() or "\n" in value:
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
        choices=TARGETS,
        help=f"which server to act on (default: {DEFAULT_TARGET})",
    )


def ssh(target: Target, argv: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    """Run one command on the server. argv is quoted for the remote shell."""
    remote = " ".join(shlex.quote(a) for a in argv)
    return _run(["ssh", *target.ssh_options, target.host, remote], capture=capture)


def ctl(target: Target, *argv: str, capture: bool = False) -> subprocess.CompletedProcess:
    """Run one mdsctl.sh command on the server."""
    return ssh(target, ["bash", target.ctl_path, *argv], capture=capture)


def scp(target: Target, local: Path, remote_path: str) -> subprocess.CompletedProcess:
    # Sent as a bare filename from its own directory: scp splits host:path on the
    # first colon, which a Windows drive letter would otherwise trip over.
    return _run(
        ["scp", *target.ssh_options, local.name, f"{target.host}:{remote_path}"],
        capture=True,
        cwd=str(local.parent),
    )


def _run(command: list[str], capture: bool, cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=capture, text=True, cwd=cwd)
    except FileNotFoundError:
        fail(f"{command[0]} not found on this machine; it is needed to reach the server")
