#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Select the configured SSH client before resolving optional dependencies."""

from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"update", "testdata"}:
        print("Usage: client_cli.py update|testdata [arguments]", file=sys.stderr)
        return 1
    tool, args = argv[0], argv[1:]
    config = ROOT / "tools/deploy.toml"
    for index, argument in enumerate(args):
        if argument == "--config" and index + 1 < len(args):
            config = Path(args[index + 1])
        elif argument.startswith("--config="):
            config = Path(argument.split("=", 1)[1])
    local_only = any(a in {"--help", "-h", "--check"} for a in args)
    if tool == "testdata":
        commands = args[1:] if args and args[0].lower() in {"uat", "prod"} else args
        local_only = local_only or not commands or commands[0] in {"datasets", "preview"}
        local_only = local_only or bool(args and args[0].lower() == "prod")
    try:
        local = tomllib.loads(config.read_text(encoding="utf-8-sig")).get("local", {}) if not local_only and config.exists() else {}
        transport = local.get("transport", "openssh" if local.get("ssh") or local.get("scp") else "paramiko")
        if str(transport).lower() == "paramiko" and not local_only and config.exists():
            # Retain the pinned, one-login client for existing configurations.
            # Native/local-only commands need only Python's standard library.
            uv = shutil.which("uv")
            if not uv:
                raise ValueError("uv not found on PATH")
            return subprocess.run([uv, "run", "--system-certs", "--python", "3.12",
                                   str(ROOT / "tools" / f"{tool}.py"), *args]).returncode
        if tool == "update":
            from update import main as entry
        else:
            from testdata import main as entry
        return entry(args)
    except Exception as error:
        print(f"{tool.capitalize()} failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
