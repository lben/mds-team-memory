#!/usr/bin/env python3
"""Publish this branch in small pushes after the owner approves publication.

The offline parts exceed GitHub's 2 GB per-push limit. Asset batch commits
must be pushed in order, then the final code commit. This command only
publishes Git history; it never contacts the deployment servers.

    python tools/push_update.py --dry-run
    python tools/push_update.py
"""

import argparse
import subprocess
import sys


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    branch = git("branch", "--show-current")
    if not branch:
        parser.error("check out the branch before publishing")
    batches = git("log", "--reverse", "--format=%H", "--grep=^Offline deployment assets batch ", "HEAD").splitlines()
    steps = [["git", "push", "origin", f"{revision}:refs/heads/{branch}"] for revision in batches]
    steps.append(["git", "push", "-u", "origin", branch])
    for command in steps:
        print(" ".join(command), flush=True)
        if not args.dry_run:
            if ":refs/heads/" in command[-1]:
                revision, ref = command[-1].split(":", 1)
                remote = git("ls-remote", "origin", ref).split()
                if remote and subprocess.run(["git", "merge-base", "--is-ancestor", revision, remote[0]],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    print("Already published; skipping this batch.", flush=True)
                    continue
            subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
