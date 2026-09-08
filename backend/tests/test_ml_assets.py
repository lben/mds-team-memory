"""Exercise offline archive transfer through its dependency-free public CLI."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ml_assets.py"


def cli(*args, success=True):
    result = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def bundle(tmp_path, content=b"some real archive bytes\x00\xff" * 5):
    source = tmp_path / "models.tar.gz"
    source.write_bytes(content)
    parts = tmp_path / "parts"
    cli("split", source, parts, "--part-size", 31)
    return source, parts / "manifest.json"


@pytest.mark.parametrize("content", [b"", b"a" * 31, bytes(range(256)) * 11])
def test_cli_round_trip_and_canonical_manifest(tmp_path, content):
    source = tmp_path / "models.tar.gz"
    source.write_bytes(content)
    metadata = {"models": [{"revision": "pinned-revision", "license": "Apache-2.0"}], "runtime": "RHEL 8.10 / Python 3.12 / CPU"}
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    cli("split", source, tmp_path / "parts", "--part-size", 31, "--metadata", metadata_path)
    manifest_path = tmp_path / "parts" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["metadata"] == metadata
    assert manifest_path.read_text() == json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    assert manifest["archive"]["sha256"] == hashlib.sha256(content).hexdigest()
    assert all(part["size"] <= 31 for part in manifest["parts"])
    cli("verify", manifest_path)
    cli("assemble", manifest_path, tmp_path / "restored")
    assert (tmp_path / "restored" / source.name).read_bytes() == content


@pytest.mark.parametrize("damage", ["missing", "modified", "truncated", "appended", "archive_hash"])
def test_corrupt_transfer_preserves_existing_archive(tmp_path, damage):
    source, manifest_path = bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    part = manifest_path.parent / manifest["parts"][1]["name"]
    if damage == "missing":
        part.unlink()
    elif damage == "modified":
        part.write_bytes(b"!" + part.read_bytes()[1:])
    elif damage == "truncated":
        part.write_bytes(part.read_bytes()[:-1])
    elif damage == "appended":
        with part.open("ab") as output:
            output.write(b"unexpected")
    else:
        manifest["archive"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
    destination = tmp_path / "restored"
    destination.mkdir()
    old_archive = destination / source.name
    old_archive.write_bytes(b"previous working version")
    cli("verify", manifest_path, success=False)
    cli("assemble", manifest_path, destination, "--force", success=False)
    assert old_archive.read_bytes() == b"previous working version"
    assert list(destination.iterdir()) == [old_archive]


@pytest.mark.parametrize("damage", ["path", "windows_path", "archive_path", "duplicate_name", "duplicate_order", "count", "size", "version", "duplicate_key"])
def test_invalid_manifest_cannot_publish_an_archive(tmp_path, damage):
    _, manifest_path = bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    if damage == "path":
        manifest["parts"][0]["name"] = "../outside"
    elif damage == "windows_path":
        manifest["parts"][0]["name"] = "C:\\outside"
    elif damage == "archive_path":
        manifest["archive"]["name"] = "../outside"
    elif damage == "duplicate_name":
        manifest["parts"][1]["name"] = manifest["parts"][0]["name"].upper()
    elif damage == "duplicate_order":
        manifest["parts"][1]["index"] = 1
    elif damage == "count":
        manifest["part_count"] += 1
    elif damage == "size":
        manifest["parts"][0]["size"] -= 1
    elif damage == "version":
        manifest["version"] = True
    serialized = json.dumps(manifest)
    if damage == "duplicate_key":
        serialized = '{"version": 1,' + serialized[1:]
    manifest_path.write_text(serialized)
    cli("verify", manifest_path, success=False)
    cli("assemble", manifest_path, tmp_path / "restored", success=False)
    assert not (tmp_path / "restored").exists()


def test_existing_outputs_require_explicit_assemble_replacement(tmp_path):
    source, manifest_path = bundle(tmp_path)
    original_manifest = manifest_path.read_bytes()
    cli("split", source, manifest_path.parent, "--part-size", 17, success=False)
    assert manifest_path.read_bytes() == original_manifest
    cli("verify", manifest_path)
    destination = tmp_path / "restored"
    destination.mkdir()
    old_archive = destination / source.name
    old_archive.write_bytes(b"previous")
    cli("assemble", manifest_path, destination, success=False)
    assert old_archive.read_bytes() == b"previous"
    cli("assemble", manifest_path, destination, "--force")
    assert old_archive.read_bytes() == source.read_bytes()


def test_assemble_cannot_replace_its_own_inputs(tmp_path):
    _, manifest_path = bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    manifest["archive"]["name"] = manifest["parts"][0]["name"]
    manifest_path.write_text(json.dumps(manifest))
    before = {path.name: path.read_bytes() for path in manifest_path.parent.iterdir()}
    cli("assemble", manifest_path, manifest_path.parent, "--force", success=False)
    assert {path.name: path.read_bytes() for path in manifest_path.parent.iterdir()} == before


@pytest.mark.parametrize("part_size", [0, -1, 95_000_001])
def test_parts_cannot_exceed_transfer_limit(tmp_path, part_size):
    source = tmp_path / "archive"
    source.write_bytes(b"content")
    cli("split", source, tmp_path / "parts", "--part-size", part_size, success=False)
    assert not (tmp_path / "parts").exists()


def test_transfer_staging_obeys_aggregate_budget(tmp_path):
    source = tmp_path / "archive"
    source.write_bytes(b"content" * 1024)
    cli("split", source, tmp_path / "parts", "--managed-root", tmp_path,
        "--max-bytes", 10_000, success=False)
    assert not (tmp_path / "parts").exists()
    cli("split", source, tmp_path / "parts")
    cli("assemble", tmp_path / "parts" / "manifest.json", tmp_path / "restored",
        "--managed-root", tmp_path, "--max-bytes", 20_000, success=False)
    assert not (tmp_path / "restored").exists()
