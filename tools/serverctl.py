#!/usr/bin/env python3
"""Start, stop and inspect the MDS Team Knowledge server without deploying.

Every command runs mdsctl.sh on the server over SSH, so it needs a release to
have been deployed there at least once, and it never needs root.

Usage:
    uv run --python 3.12 tools/serverctl.py                 # status of uat
    uv run --python 3.12 tools/serverctl.py start           # after a server reboot
    uv run --python 3.12 tools/serverctl.py restart prod
    uv run --python 3.12 tools/serverctl.py logs uat --lines 200
    uv run --python 3.12 tools/serverctl.py ml-status uat --jobs # the ML worker's step and queue
    uv run --python 3.12 tools/serverctl.py rollback prod   # back to the previous release
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploylib import add_target_argument, ctl, load_target  # noqa: E402

COMMANDS = ("status", "start", "stop", "restart", "health", "logs", "releases", "rollback",
            "ml-status", "ml-start", "ml-stop", "ml-logs", "preflight")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="status", choices=COMMANDS, help="default: status")
    add_target_argument(parser)
    parser.add_argument("--lines", type=int, default=100, help="lines of log to show (default: 100)")
    parser.add_argument("--jobs", action="store_true",
                        help="with ml-status: show the worker's current step and the queued jobs")
    parser.add_argument("--limit", type=int, default=50, help="with --jobs: how many queued jobs to list (default: 50)")
    args = parser.parse_args()
    if args.jobs and args.command != "ml-status":
        parser.error("--jobs applies only to ml-status")

    target = load_target(args.target)
    argv = [args.command, str(args.lines)] if args.command in ("logs", "ml-logs") else [args.command]
    if args.jobs:
        argv += ["--jobs", "--limit", str(args.limit)]
    print(f"{target.name}: {args.command} on {target.host}", file=sys.stderr)
    raise SystemExit(ctl(target, *argv).returncode)


if __name__ == "__main__":
    main()
