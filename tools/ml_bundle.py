#!/usr/bin/env python3
"""Pack or safely extract a pinned offline model and Linux wheel bundle.

    python tools/ml_bundle.py pack assets/models-linux.tar --models assets/models \
        --wheels assets/wheels --lock requirements-linux.lock --managed-root assets
    python tools/ml_bundle.py verify assets/models-linux.tar
    python tools/ml_bundle.py extract assets/models-linux.tar assets/generation-1 --managed-root assets

The archive must come from a trusted release, verified with ml_assets.py after
transfer. Embedded hashes detect corruption; they do not authenticate a supplier.
Extraction requires a new destination. It verifies all members before staging
files, then publishes the complete directory. No archive links are accepted.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ml_assets import BUFFER_SIZE, canonical_json, filename, hash_record, publish, read_json, unique_keys
from ml_storage import MAX_BYTES, add_budget_arguments, allocation, publish_generation

MANIFEST_LIMIT = 8 * 1024 * 1024
FORMAT = "mds-ml-bundle"


def relative_path(value):
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096
            or PurePosixPath(value).as_posix() != value or value.startswith("/")):
        raise ValueError(f"Unsafe bundle path: {value!r}")
    for component in value.split("/"):
        filename(component)
    return value


def regular_file(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")


def record(path):
    regular_file(path)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    return {"size": path.stat().st_size, "sha256": digest}


def model_files(directory):
    """Validate each file of a pinned models.json without importing ML packages."""
    manifest_path = directory / "models.json"
    regular_file(manifest_path)
    manifest = read_json(manifest_path)
    if type(manifest.get("version")) is not int or manifest["version"] != 1 or not isinstance(manifest.get("models"), dict) or not manifest["models"]:
        raise ValueError("Invalid model manifest")
    result, seen = [], set()
    for role, model in manifest["models"].items():
        filename(role)
        if (not isinstance(model, dict) or not isinstance(model.get("files"), list) or not model["files"]
                or not all(isinstance(model.get(key), str) and model[key] for key in ("repository", "revision", "license"))):
            raise ValueError("Incomplete pinned model metadata")
        for entry in model["files"]:
            if not hash_record(entry):
                raise ValueError("Invalid model file checksum or size")
            name = f"{role}/{relative_path(entry.get('path'))}"
            if name.casefold() in seen:
                raise ValueError("Duplicate model file path")
            seen.add(name.casefold())
            source = directory / name
            if any(parent.is_symlink() for parent in [source, *source.parents] if parent.is_relative_to(directory)):
                raise ValueError("Model paths must not contain symlinks")
            actual = record(source)
            if actual != {"size": entry["size"], "sha256": entry["sha256"]}:
                raise ValueError(f"Model checksum or length mismatch: {name}")
            result.append((f"models/{name}", source, actual))
    result.append(("models/models.json", manifest_path, record(manifest_path)))
    return manifest, result


def tar_info(name, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, 0o644, 0
    return info


class CheckedReader:
    def __init__(self, source):
        self.source, self.digest = source, hashlib.sha256()

    def read(self, size=-1):
        block = self.source.read(size)
        self.digest.update(block)
        return block


def pack(archive_path, models, wheels, lock, budget):
    budget.contains(archive_path)
    if os.path.lexists(archive_path):
        raise FileExistsError("Bundle output already exists")
    models_manifest, files = model_files(models)
    wheel_paths = sorted(wheels.glob("*.whl"))
    if not wheel_paths:
        raise ValueError("The wheel directory contains no wheels")
    files.extend((f"wheels/{filename(path.name)}", path, record(path)) for path in wheel_paths)
    files.append(("requirements-linux.lock", lock, record(lock)))
    manifest = {"format": FORMAT, "version": 1,
                "target": {"os": "RHEL 8.10", "architecture": "x86_64", "python": "3.12", "device": "cpu"},
                "models": {role: {key: model[key] for key in ("repository", "revision", "license")} for role, model in models_manifest["models"].items()},
                "files": [{"path": name, **entry} for name, _, entry in files],
                "total_size": sum(entry["size"] for _, _, entry in files)}
    data = canonical_json(manifest).encode("utf-8")
    validate_manifest(manifest)
    if len(data) > MANIFEST_LIMIT:
        raise ValueError("Bundle manifest is too large")
    members = [("bundle.json", len(data)), *((name, entry["size"]) for name, _, entry in files)]
    required = 1024 + sum(len(tar_info(name, size).tobuf(format=tarfile.PAX_FORMAT)) + ((size + 511) // 512) * 512 for name, size in members)
    required = ((required + tarfile.RECORDSIZE - 1) // tarfile.RECORDSIZE) * tarfile.RECORDSIZE
    budget.check(required)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=archive_path.parent, delete=False) as output:
            temporary = Path(output.name)
            with tarfile.open(fileobj=output, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                archive.addfile(tar_info("bundle.json", len(data)), io.BytesIO(data))
                for name, source, entry in files:
                    budget.check(entry["size"])
                    regular_file(source)
                    with source.open("rb") as stream:
                        reader = CheckedReader(stream)
                        archive.addfile(tar_info(name, entry["size"]), reader)
                    if reader.digest.hexdigest() != entry["sha256"] or source.stat().st_size != entry["size"]:
                        raise ValueError(f"Source changed during packing: {name}")
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != required:
            raise ValueError("Unexpected packed archive length")
        budget.check()
        os.chmod(temporary, 0o644)
        publish(temporary, archive_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
            or type(manifest.get("version")) is not int or manifest["version"] != 1
            or not isinstance(manifest.get("files"), list) or not manifest["files"]
            or type(manifest.get("total_size")) is not int or not 0 <= manifest["total_size"] <= MAX_BYTES):
        raise ValueError("Invalid bundle manifest")
    canonical_json(manifest)
    seen = set()
    total = 0
    for entry in manifest["files"]:
        if not hash_record(entry):
            raise ValueError("Invalid bundled file hash or size")
        path = relative_path(entry.get("path"))
        folded = path.casefold()
        if folded == "bundle.json" or folded in seen:
            raise ValueError("Duplicate or reserved bundle path")
        seen.add(folded)
        total += entry["size"]
    if total != manifest["total_size"]:
        raise ValueError("Bundle total does not match file lengths")
    for name in seen:
        if any(parent.as_posix() in seen for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
            raise ValueError("A bundle file path is also a parent directory")


def scan(archive_path, destination=None, budget=None):
    """Verify the complete tar stream; optionally write only validated regular files."""
    regular_file(archive_path)
    with tarfile.open(archive_path, mode="r|") as archive:
        first = archive.next()
        if (not first or first.name != "bundle.json" or first.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                or first.sparse is not None or not 0 < first.size <= MANIFEST_LIMIT):
            raise ValueError("The first member must be a bounded regular bundle.json")
        stream = archive.extractfile(first)
        data = stream.read()
        manifest = json.loads(data, object_pairs_hook=unique_keys)
        validate_manifest(manifest)
        expected = {entry["path"]: entry for entry in manifest["files"]}
        seen = set()
        while member := archive.next():
            name = relative_path(member.name)
            if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.sparse is not None
                    or name in seen or name not in expected or member.size != expected[name]["size"]):
                raise ValueError(f"Unexpected, repeated, linked, or invalid member: {name}")
            seen.add(name)
            output = None
            try:
                if destination is not None:
                    budget.check(member.size)
                    target = destination / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    output = target.open("xb")
                digest = hashlib.sha256()
                with archive.extractfile(member) as source:
                    for block in iter(lambda: source.read(BUFFER_SIZE), b""):
                        digest.update(block)
                        if output is not None:
                            output.write(block)
                if digest.hexdigest() != expected[name]["sha256"]:
                    raise ValueError(f"Bundled file checksum mismatch: {name}")
            finally:
                if output is not None:
                    output.close()
                    os.chmod(target, 0o644)
        if seen != set(expected):
            raise ValueError("Bundle is missing files")
        if destination is not None:
            budget.check(len(data))
            (destination / "bundle.json").write_bytes(data)
            os.chmod(destination / "bundle.json", 0o644)
        return manifest


def extract(archive_path, destination, budget):
    budget.contains(destination)
    if os.path.lexists(destination):
        raise FileExistsError("Extraction requires a new destination generation")
    manifest = scan(archive_path)
    budget.check(manifest["total_size"] + MANIFEST_LIMIT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".extract-", dir=destination.parent))
    try:
        scan(archive_path, temporary, budget)
        budget.check()
        publish_generation(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    packer = commands.add_parser("pack")
    packer.add_argument("archive", type=Path)
    packer.add_argument("--models", type=Path, required=True)
    packer.add_argument("--wheels", type=Path, required=True)
    packer.add_argument("--lock", type=Path, required=True)
    add_budget_arguments(packer)
    extractor = commands.add_parser("extract")
    extractor.add_argument("archive", type=Path)
    extractor.add_argument("destination", type=Path)
    add_budget_arguments(extractor)
    verifier = commands.add_parser("verify")
    verifier.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            scan(args.archive)
        else:
            accounts = args.account + ([args.models, args.wheels, args.lock] if args.command == "pack" else [args.archive])
            with allocation(args.managed_root, accounts, args.max_bytes, args.reserve_bytes) as budget:
                if args.command == "pack":
                    pack(args.archive, args.models, args.wheels, args.lock, budget)
                else:
                    extract(args.archive, args.destination, budget)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Bundle operation failed: {error}\n")
    print(f"{args.command}: {args.archive}")


if __name__ == "__main__":
    sys.exit(main())
