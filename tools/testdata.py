#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["paramiko==4.0.0"]
# ///
"""Load, inspect and remove tracked synthetic data on UAT only.

    TestData.cmd datasets
    TestData.cmd UAT preview --dataset expanded --percent 25 --seed 42
    TestData.cmd UAT add --dataset expanded --percent 25 --seed 42
    TestData.cmd UAT batches
    TestData.cmd UAT status --batch <id> --wait
    TestData.cmd UAT remove --batch <id>

UAT is the default. PROD is always refused. Configuration is tools/deploy.toml;
uses the same configured SSH transport as Update.
"""
import argparse
import json
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.testdata_catalog import add_selection_arguments, datasets, selection_from_args
from deploylib import CONFIG_PATH
from update import configured_target
from update_session import open_session as Session


def remote_command(target, argv):
    # Read the deployed environment, never replace its UAT/PROD identity from
    # the client. Arguments are shell-quoted individually, including topics.
    root = shlex.quote(target.root)
    arguments = " ".join(shlex.quote(str(a)) for a in argv)
    script = f"set -eu; set -a; . {root}/app.env; set +a; cd {root}/current; exec .venv/bin/python manage.py test-data {arguments}"
    return " ".join(shlex.quote(a) for a in (target.bash, "-c", script))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Accept the suggested target-first spelling and the default-UAT shorthand.
    if argv and argv[0].lower() in ("uat", "prod"):
        environment = argv.pop(0).lower()
        if environment != "uat":
            print("TestData refuses PROD; only UAT is supported.", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("datasets", help="local dataset sizes and topics; no connection")
    for command in ("preview", "add"):
        p = commands.add_parser(command, help="local preview" if command == "preview" else "import into UAT")
        add_selection_arguments(p)
        if command == "add":
            p.add_argument("--config", type=Path, default=CONFIG_PATH)
    p = commands.add_parser("batches")
    p.add_argument("--config", type=Path, default=CONFIG_PATH)
    p = commands.add_parser("status")
    p.add_argument("--batch", required=True)
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--config", type=Path, default=CONFIG_PATH)
    p = commands.add_parser("remove")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch")
    group.add_argument("--all-batches", action="store_true")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)
    session = None
    try:
        if args.command == "datasets":
            print(json.dumps(datasets(), indent=2))
            return 0
        remote = [args.command]
        if args.command in ("preview", "add"):
            selection, _ = selection_from_args(args)
            view = {k: v for k, v in selection.items() if k != "case_ids"}
            print(json.dumps(view, indent=2), flush=True)
            if args.command == "preview":
                return 0
            remote += ["--dataset", args.dataset, "--percent", args.percent, "--seed", str(args.seed),
                       "--expected-fingerprint", selection["fingerprint"]]
            if args.topics:
                remote += ["--topics", args.topics]
        else:
            if getattr(args, "batch", None):
                remote += ["--batch", args.batch]
            if getattr(args, "all_batches", False):
                remote += ["--all-batches"]
            if getattr(args, "wait", False):
                remote += ["--wait"]
            if hasattr(args, "timeout"):
                if not 0 < args.timeout <= 604800:
                    raise ValueError("--timeout must be positive and at most 604800 seconds")
                remote += ["--timeout", str(args.timeout)]
        target, known_hosts = configured_target("uat", args.config)
        print(f"UAT: {args.command} on {target.host}:{target.root}", flush=True)
        session = Session(target, known_hosts)
        result = session.run(remote_command(target, remote))
        return result.returncode
    except Exception as error:
        print(f"TestData failed: {error}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
