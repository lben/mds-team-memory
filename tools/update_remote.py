"""Offline model preparation, run on the server before stopping the current app."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploylib import ml_generation, ml_preflight
from ml_assets import assemble, manifest_at
from ml_bundle import extract
from ml_storage import allocation


def prepare(root, release, manifest_path, generation, *, probe=False):
    root, release, manifest_path, generation = map(Path, (root, release, manifest_path, generation))
    managed = root / "ml"
    if not generation.resolve().is_relative_to(managed.resolve() / "generations"):
        raise ValueError("Generation must be inside this deployment's ml/generations")
    if not manifest_path.resolve().is_relative_to(managed.resolve() / "incoming"):
        raise ValueError("Transfer must be inside this deployment's ml/incoming")
    if generation.exists():
        ml_generation(generation, release)
        return 0
    if probe:
        return 2
    ml_preflight(root / "data")
    manifest = manifest_at(manifest_path)
    with allocation(managed) as budget:
        archive_path = manifest_path.parent / manifest["archive"]["name"]
        if archive_path.exists():
            with archive_path.open("rb") as stream:
                if (archive_path.stat().st_size != manifest["archive"]["size"] or
                    hashlib.file_digest(stream, "sha256").hexdigest() != manifest["archive"]["sha256"]):
                    raise ValueError("Interrupted archive is corrupt; remove it before retrying")
        else:
            print("Verifying and joining model parts...", flush=True)
            archive_path = assemble(manifest_path, manifest_path.parent, False, budget)
        # Verified archive now owns these bytes; free transfer copies before
        # expanding the bundle to stay within the fixed 16 GiB storage budget.
        for part in manifest["parts"]:
            (manifest_path.parent / part["name"]).unlink(missing_ok=True)
        print("Verifying and decompressing the offline model/dependency bundle...", flush=True)
        extract(archive_path, generation, budget)
        print("Checking installed generation and dependency lock...", flush=True)
        ml_generation(generation, release)
        archive_path.unlink()
        manifest_path.unlink()
        budget.check()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("probe", "prepare", "reserve"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--bytes", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == "reserve":
            ml_preflight(args.root / "data")
            with allocation(args.root / "ml") as budget:
                budget.check(args.bytes)
            return 0
        return prepare(args.root, args.release, args.manifest, args.generation, probe=args.command == "probe")
    except (OSError, ValueError) as error:
        print(f"Offline preparation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
