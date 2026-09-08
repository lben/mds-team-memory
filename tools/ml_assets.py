#!/usr/bin/env python3
"""Transfer offline archives using numbered parts and a trusted SHA-256 manifest.

    python tools/ml_assets.py split models.tar.gz transfer/models
    python tools/ml_assets.py verify transfer/models/manifest.json
    python tools/ml_assets.py assemble transfer/models/manifest.json assets

Split requires a new destination directory. Assemble preserves an existing archive
unless --force is explicit, and replaces it only after verification. No extraction
is performed. --metadata accepts a JSON object for pinned model revisions, licenses,
and the target runtime; its contents are preserved in the manifest.

Hashes detect corruption, not an untrusted supplier: obtain the manifest through
the same trusted release process as the intended models.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ml_storage import add_budget_arguments, allocation

PART_SIZE = 95_000_000
BUFFER_SIZE = 1024 * 1024
FORMAT = "mds-ml-assets"


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def unique_keys(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream, object_pairs_hook=unique_keys)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    canonical_json(result)  # Reject non-finite values accepted by Python's JSON reader.
    return result


def filename(value: object) -> str:
    # These names must be safe on Windows as well as Linux, regardless of the host.
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value) or value.endswith((".", " "))
            or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", value.split(".")[0], re.IGNORECASE)):
        raise ValueError(f"Unsafe filename: {value!r}")
    return value


def integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def hash_record(value: object) -> bool:
    return (isinstance(value, dict) and integer(value.get("size"))
            and isinstance(value.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)


def manifest_at(path: Path) -> dict:
    manifest = read_json(path)
    archive, parts = manifest.get("archive"), manifest.get("parts")
    part_size = manifest.get("part_size")
    if (manifest.get("format") != FORMAT or type(manifest.get("version")) is not int
            or manifest["version"] != 1 or not hash_record(archive)
            or not integer(part_size, 1) or part_size > PART_SIZE
            or not isinstance(parts, list) or not parts
            or not integer(manifest.get("part_count"), 1)
            or manifest["part_count"] != len(parts)
            or not isinstance(manifest.get("metadata"), dict)):
        raise ValueError("Invalid or unsupported asset manifest")
    filename(archive.get("name"))
    expected_count = max(1, (archive["size"] + part_size - 1) // part_size)
    if len(parts) != expected_count:
        raise ValueError("Part count does not match archive size")
    names = set()
    for index, part in enumerate(parts, 1):
        if not hash_record(part) or type(part.get("index")) is not int or part["index"] != index:
            raise ValueError("Invalid or repeated part order")
        name = filename(part.get("name"))
        if name.casefold() in names or name.casefold() == path.name.casefold():
            raise ValueError("Duplicate part filename or manifest collision")
        names.add(name.casefold())
        expected_size = min(part_size, archive["size"] - (index - 1) * part_size)
        if part["size"] != expected_size:
            raise ValueError("Part length does not match archive size")
    return manifest


def publish(temp: Path, destination: Path, force: bool = False) -> None:
    if force:
        os.replace(temp, destination)
    else:
        # A same-directory hard link atomically refuses an existing destination.
        # A check followed by rename would overwrite a concurrently created file.
        os.link(temp, destination)
        temp.unlink()


def split(archive: Path, destination: Path, part_size: int, metadata: dict, budget=None) -> Path:
    if not 1 <= part_size <= PART_SIZE:
        raise ValueError(f"Part size must be between 1 and {PART_SIZE} bytes")
    filename(archive.name)
    created = []
    with archive.open("rb") as source:
        if budget is not None:
            budget.contains(destination)
            budget.check(archive.stat().st_size)
        destination.mkdir(parents=True, exist_ok=False)
        try:
            parts, total, archive_hash = [], 0, hashlib.sha256()
            while True:
                block = source.read(min(BUFFER_SIZE, part_size))
                if not block and parts:
                    break
                index, size, part_hash = len(parts) + 1, 0, hashlib.sha256()
                part_path = destination / f"part-{index:06d}"
                if budget is not None:
                    budget.check(min(part_size, max(len(block), archive.stat().st_size - total)))
                with part_path.open("xb") as output:
                    created.append(part_path)
                    while block:
                        output.write(block)
                        size += len(block)
                        part_hash.update(block)
                        archive_hash.update(block)
                        if size == part_size:
                            break
                        block = source.read(min(BUFFER_SIZE, part_size - size))
                parts.append({"index": index, "name": part_path.name, "size": size, "sha256": part_hash.hexdigest()})
                total += size
            manifest = {"format": FORMAT, "version": 1, "part_size": part_size,
                        "part_count": len(parts), "parts": parts, "metadata": metadata,
                        "archive": {"name": archive.name, "size": total, "sha256": archive_hash.hexdigest()}}
            manifest_path = destination / "manifest.json"
            data = canonical_json(manifest)
            if budget is not None:
                budget.check(len(data.encode("utf-8")))
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination, delete=False) as output:
                temp = Path(output.name)
                created.append(temp)
                output.write(data)
            publish(temp, manifest_path)
            return manifest_path
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            destination.rmdir()
            raise


def stream_parts(manifest_path: Path, manifest: dict, output=None) -> None:
    total, archive_hash = 0, hashlib.sha256()
    for part in manifest["parts"]:
        part_path = manifest_path.parent / part["name"]
        if part_path.is_symlink() or not part_path.is_file():
            raise ValueError(f"Missing or non-regular part: {part['name']}")
        size, part_hash = 0, hashlib.sha256()
        with part_path.open("rb") as source:
            while block := source.read(BUFFER_SIZE):
                size += len(block)
                if size > part["size"]:
                    raise ValueError(f"Part length mismatch: {part['name']}")
                part_hash.update(block)
                archive_hash.update(block)
                if output is not None:
                    output.write(block)
        if size != part["size"] or part_hash.hexdigest() != part["sha256"]:
            raise ValueError(f"Part checksum or length mismatch: {part['name']}")
        total += size
    if total != manifest["archive"]["size"] or archive_hash.hexdigest() != manifest["archive"]["sha256"]:
        raise ValueError("Archive checksum or length mismatch")


def assemble(manifest_path: Path, destination: Path, force: bool, budget=None) -> Path:
    manifest = manifest_at(manifest_path)
    archive = destination / manifest["archive"]["name"]
    sources = [manifest_path, *(manifest_path.parent / part["name"] for part in manifest["parts"])]
    if any(archive.resolve() == source.resolve() for source in sources):
        raise ValueError("Output would replace the manifest or a part")
    if not force and os.path.lexists(archive):
        raise FileExistsError(f"Output exists; use --force to replace it: {archive}")
    if budget is not None:
        budget.contains(destination)
        budget.check(manifest["archive"]["size"])
    destination.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination, delete=False) as output:
            temp = Path(output.name)
            stream_parts(manifest_path, manifest, output)
            output.flush()
            os.fsync(output.fileno())
        publish(temp, archive, force)
        return archive
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    splitter = commands.add_parser("split", help="Split an archive into a new directory")
    splitter.add_argument("archive", type=Path)
    splitter.add_argument("destination", type=Path)
    splitter.add_argument("--part-size", type=int, default=PART_SIZE, help=f"Maximum part bytes (default: {PART_SIZE})")
    splitter.add_argument("--metadata", type=Path, help="JSON object with model revisions, licenses, and target runtime")
    add_budget_arguments(splitter, required=False)
    assembler = commands.add_parser("assemble", help="Verify parts and restore the archive atomically")
    assembler.add_argument("manifest", type=Path)
    assembler.add_argument("destination", type=Path)
    assembler.add_argument("--force", action="store_true", help="Replace an existing archive after successful verification")
    add_budget_arguments(assembler, required=False)
    verifier = commands.add_parser("verify", help="Verify part sizes, order, and all SHA-256 checksums")
    verifier.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            stream_parts(args.manifest, manifest_at(args.manifest))
            result = args.manifest
        else:
            accounts = args.account + [args.archive if args.command == "split" else args.manifest.parent]
            with allocation(args.managed_root or args.destination.parent, accounts, args.max_bytes, args.reserve_bytes) as budget:
                if args.command == "split":
                    result = split(args.archive, args.destination, args.part_size, read_json(args.metadata) if args.metadata else {}, budget)
                else:
                    result = assemble(args.manifest, args.destination, args.force, budget)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
