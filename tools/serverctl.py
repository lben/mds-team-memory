#!/usr/bin/env python3
"""Start, stop and inspect the MDS Team Knowledge server without deploying.

Every command runs mdsctl.sh on the server over SSH, so it needs a release to
have been deployed there at least once, and it never needs root.

Usage:
    uv run --python 3.12 tools/serverctl.py                 # status of uat
    uv run --python 3.12 tools/serverctl.py start           # after a server reboot
    uv run --python 3.12 tools/serverctl.py restart prod
    uv run --python 3.12 tools/serverctl.py logs uat --lines 200
    uv run --python 3.12 tools/serverctl.py rollback prod   # back to the previous release
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from deploylib import add_target_argument, ctl, load_target  # noqa: E402

COMMANDS = ("status", "start", "stop", "restart", "health", "logs", "releases", "rollback")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="status", choices=COMMANDS, help="default: status")
    add_target_argument(parser)
    parser.add_argument("--lines", type=int, default=100, help="lines of log to show (default: 100)")
    args = parser.parse_args()

    target = load_target(args.target)
    argv = ["logs", str(args.lines)] if args.command == "logs" else [args.command]
    print(f"{target.name}: {args.command} on {target.host}", file=sys.stderr)
    raise SystemExit(ctl(target, *argv).returncode)


if __name__ == "__main__":
    main()
