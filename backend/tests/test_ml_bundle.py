"""Prove the offline bundle boundary through real isolated CLI processes."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "ml_bundle.py"


def cli(*arguments, success=True, tool=TOOL):
    result = subprocess.run([sys.executable, "-I", str(tool), *map(str, arguments)], capture_output=True, text=True)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


def assets(tmp_path):
    models, wheels = tmp_path / "models", tmp_path / "wheels"
    (models / "extractor").mkdir(parents=True)
    wheels.mkdir()
    content = b"verified model weights\x00\xff" * 17
    (models / "extractor" / "weights.bin").write_bytes(content)
    manifest = {"version": 1, "models": {"extractor": {
        "repository": "fixture/model", "revision": "a" * 40, "license": "Apache-2.0",
        "files": [{"path": "weights.bin", "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}],
    }}}
    (models / "models.json").write_text(json.dumps(manifest))
    (wheels / "fixture-1-py3-none-any.whl").write_bytes(b"wheel bytes for archive integrity")
    lock = tmp_path / "requirements-linux.lock"
    lock.write_text("fixture==1\n")
    return models, wheels, lock


def pack_args(tmp_path):
    models, wheels, lock = assets(tmp_path)
    return ["pack", tmp_path / "managed" / "bundle.tar", "--models", models,
            "--wheels", wheels, "--lock", lock, "--managed-root", tmp_path / "managed"]


def test_pack_verify_extract_cli_round_trip(tmp_path):
    arguments = pack_args(tmp_path)
    cli(*arguments)
    archive, destination = arguments[1], tmp_path / "managed" / "generation-1"
    cli("verify", archive)
    parts = tmp_path / "managed" / "parts"
    cli("split", archive, parts, "--part-size", 1024, tool=TOOL.with_name("ml_assets.py"))
    cli("verify", parts / "manifest.json", tool=TOOL.with_name("ml_assets.py"))
    restored = tmp_path / "managed" / "restored"
    cli("assemble", parts / "manifest.json", restored, tool=TOOL.with_name("ml_assets.py"))
    assert (restored / archive.name).read_bytes() == archive.read_bytes()
    cli("extract", restored / archive.name, destination, "--managed-root", tmp_path / "managed")
    for source in (tmp_path / "models").rglob("*"):
        if source.is_file():
            assert (destination / "models" / source.relative_to(tmp_path / "models")).read_bytes() == source.read_bytes()
    assert (destination / "wheels" / "fixture-1-py3-none-any.whl").read_bytes() == (tmp_path / "wheels" / "fixture-1-py3-none-any.whl").read_bytes()
    assert (destination / "requirements-linux.lock").read_bytes() == (tmp_path / "requirements-linux.lock").read_bytes()
    if os.name != "nt":
        for path in [destination, *destination.rglob("*")]:
            assert path.stat().st_mode & (0o555 if path.is_dir() else 0o444) == (0o555 if path.is_dir() else 0o444)
    before = {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
    cli("extract", archive, destination, "--managed-root", tmp_path / "managed", success=False)
    assert before == {path: path.read_bytes() for path in destination.rglob("*") if path.is_file()}
    archive_before = archive.read_bytes()
    cli(*arguments, success=False)
    assert archive.read_bytes() == archive_before


def hostile_archive(path, damage):
    name, content = "models/weights.bin", b"correct content"
    if damage == "traversal":
        name = "../outside"
    elif damage == "windows_path":
        name = "C:\\outside"
    entries = [{"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}]
    if damage == "duplicate_manifest":
        entries.append(entries[0].copy())
    manifest = {"format": "mds-ml-bundle", "version": 1, "files": entries, "total_size": sum(entry["size"] for entry in entries)}
    with tarfile.open(path, "w") as archive:
        data = json.dumps(manifest).encode()
        first = tarfile.TarInfo("bundle.json")
        first.size = len(data)
        archive.addfile(first, io.BytesIO(data))
        member = tarfile.TarInfo(name)
        member.size = len(content)
        if damage in {"symlink", "hardlink"}:
            member.type = tarfile.SYMTYPE if damage == "symlink" else tarfile.LNKTYPE
            member.linkname = "../outside"
            member.size = 0
        if damage != "missing":
            archive.addfile(member, io.BytesIO(b"altered content" if damage == "corrupt" else content))
        if damage == "duplicate_member":
            archive.addfile(member, io.BytesIO(content))
    if damage == "truncated":
        with path.open("r+b") as output:
            output.truncate(1540)


@pytest.mark.parametrize("damage", ["traversal", "windows_path", "symlink", "hardlink", "duplicate_manifest", "duplicate_member", "missing", "corrupt", "truncated"])
def test_untrusted_or_corrupt_bundle_cannot_activate_or_change_prior_generation(tmp_path, damage):
    managed = tmp_path / "managed"
    old = managed / "previous"
    old.mkdir(parents=True)
    (old / "weights").write_bytes(b"previous working generation")
    archive = tmp_path / "bad.tar"
    hostile_archive(archive, damage)
    cli("verify", archive, success=False)
    cli("extract", archive, managed / "new", "--managed-root", managed, success=False)
    assert (old / "weights").read_bytes() == b"previous working generation"
    assert not (managed / "new").exists()
    assert not list(managed.glob(".extract-*"))
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("option,value", [("--max-bytes", 128), ("--reserve-bytes", 1 << 60), ("--max-bytes", 17 * 1024**3)])
def test_pack_respects_aggregate_quota_and_free_space_before_allocation(tmp_path, option, value):
    arguments = pack_args(tmp_path)
    cli(*arguments, option, value, success=False)
    assert not arguments[1].exists()
    assert not list((tmp_path / "managed").glob("tmp*"))
    assert (tmp_path / "models" / "extractor" / "weights.bin").is_file()


def test_extract_counts_archive_prior_runtime_and_staging_together(tmp_path):
    arguments = pack_args(tmp_path)
    cli(*arguments)
    managed = tmp_path / "managed"
    (managed / "runtime").write_bytes(b"existing runtime" * 1024)
    before = {path.name: path.read_bytes() for path in managed.iterdir() if path.is_file()}
    cli("extract", arguments[1], managed / "new", "--managed-root", managed,
        "--max-bytes", 20_000, success=False)
    assert not (managed / "new").exists()
    assert before == {path.name: path.read_bytes() for path in managed.iterdir() if path.is_file()}


def test_pack_rejects_changed_model_weights(tmp_path):
    arguments = pack_args(tmp_path)
    weights = tmp_path / "models" / "extractor" / "weights.bin"
    weights.write_bytes(b"corrupt model")
    cli(*arguments, success=False)
    assert not arguments[1].exists()


def test_concurrent_bundle_allocation_fails_without_writing_then_recovers(tmp_path):
    sys.path.insert(0, str(TOOL.parent))
    from ml_storage import allocation
    arguments = pack_args(tmp_path)
    with allocation(tmp_path / "managed"):
        cli(*arguments, success=False)
        assert not arguments[1].exists()
    cli(*arguments)
    cli("verify", arguments[1])
