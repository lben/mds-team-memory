"""Exercise model preparation with only its external download boundary replaced."""

import hashlib
import io
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ml_prepare
from ml_storage import allocation

SYNTAX_SPEC = ml_prepare.MODELS["syntax"]


@pytest.fixture
def model_download(monkeypatch):
    spec = {"repository": "fixture/model", "revision": "a" * 40, "license": "Apache-2.0",
            "files": ["config.json", "weights.bin"]}
    content = {"config.json": b'{"fixture": true}', "weights.bin": b"model bytes\x00\xff"}
    metadata = {"sha": spec["revision"], "siblings": [{"rfilename": name, "size": len(data),
        "lfs": {"sha256": hashlib.sha256(data).hexdigest()}} for name, data in content.items()]}
    monkeypatch.setattr(ml_prepare, "MODELS", {"extractor": spec})

    def urlopen(url, timeout):
        return io.BytesIO(json.dumps(metadata).encode() if "/api/models/" in url else content[url.rsplit("/", 1)[1]])

    monkeypatch.setattr(ml_prepare.urllib.request, "urlopen", urlopen)
    return content


def test_prepared_generation_verifies_and_is_reusable_without_downloads(tmp_path, model_download, monkeypatch):
    destination = tmp_path / "models-v1"
    with allocation(tmp_path) as budget:
        manifest = ml_prepare.prepare(destination, budget)
    assert manifest["models"]["extractor"]["revision"] == "a" * 40
    assert (destination / "extractor" / "weights.bin").read_bytes() == model_download["weights.bin"]
    if os.name != "nt":
        assert destination.stat().st_mode & 0o555 == 0o555
        assert (destination / "extractor" / "weights.bin").stat().st_mode & 0o444 == 0o444
    monkeypatch.setattr(ml_prepare.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("Verified assets must not download again"))
    with allocation(tmp_path) as budget:
        assert ml_prepare.prepare(destination, budget) == manifest
    assert not list(tmp_path.glob(".prepare-*"))


def test_failed_download_preserves_previous_generation_and_removes_staging(tmp_path, model_download, monkeypatch):
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "weights").write_bytes(b"working model")
    original = ml_prepare.urllib.request.urlopen

    def corrupt(url, timeout):
        return io.BytesIO(b"corrupt bytes") if url.endswith("weights.bin") else original(url, timeout)

    monkeypatch.setattr(ml_prepare.urllib.request, "urlopen", corrupt)
    with allocation(tmp_path) as budget, pytest.raises(ValueError, match="verification failed"):
        ml_prepare.prepare(tmp_path / "new", budget)
    assert (previous / "weights").read_bytes() == b"working model"
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".prepare-*"))


def test_preparation_quota_prevents_download_allocation(tmp_path, model_download):
    with allocation(tmp_path, max_bytes=100) as budget, pytest.raises(ValueError, match="ceiling"):
        ml_prepare.prepare(tmp_path / "new", budget)
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".prepare-*"))


@pytest.fixture
def syntax_download(model_download, monkeypatch):
    content = {name: f"syntax {name}".encode() for name in SYNTAX_SPEC["files"]}
    source = dict(SYNTAX_SPEC["source"])
    spec = {**SYNTAX_SPEC, "source": source, "expanded_size": sum(map(len, content.values()))}
    monkeypatch.setitem(ml_prepare.MODELS, "syntax", spec)
    original = ml_prepare.urllib.request.urlopen

    def archive_download(damage=None):
        buffer = io.BytesIO()
        prefix = "en_core_web_trf/en_core_web_trf-3.8.0/"
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
            wheel.writestr("en_core_web_trf/__init__.py", b"package loader is not needed")
            for name, data in content.items():
                if damage == "missing" and name == "LICENSE":
                    continue
                member = zipfile.ZipInfo(prefix + name)
                if damage == "symlink" and name == "LICENSE":
                    member.external_attr = (stat.S_IFLNK | 0o777) << 16
                wheel.writestr(member, data)
            if damage == "traversal":
                wheel.writestr(prefix + "../outside", b"escape")
            elif damage == "unexpected":
                wheel.writestr(prefix + "unlisted", b"unlisted")
        data = buffer.getvalue()
        source.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        if damage == "expanded_length":
            spec["expanded_size"] -= 1
        if damage == "checksum":
            data = b"x" * len(data)
        monkeypatch.setattr(ml_prepare.urllib.request, "urlopen", lambda url, timeout:
                            io.BytesIO(data) if url == source["url"] else original(url, timeout))

    archive_download()
    return content, archive_download


def test_preparation_preserves_complete_syntax_data_and_source_pin(tmp_path, syntax_download, monkeypatch):
    content, _ = syntax_download
    destination = tmp_path / "models"
    with allocation(tmp_path) as budget:
        manifest = ml_prepare.prepare(destination, budget)
    syntax = manifest["models"]["syntax"]
    assert syntax["source"] == ml_prepare.MODELS["syntax"]["source"]
    assert {entry["path"] for entry in syntax["files"]} == set(content)
    assert {str(path.relative_to(destination / "syntax")): path.read_bytes()
            for path in (destination / "syntax").rglob("*") if path.is_file()} == content
    assert not (destination / "en_core_web_trf").exists()
    assert not (destination / ".syntax.whl").exists()
    monkeypatch.setattr(ml_prepare.urllib.request, "urlopen", lambda *args, **kwargs:
                        pytest.fail("Verified assets must not download again"))
    with allocation(tmp_path) as budget:
        assert ml_prepare.prepare(destination, budget) == manifest
    manifest = json.loads((destination / "models.json").read_text())
    manifest["models"]["syntax"]["source"]["url"] = "https://example.invalid/changed"
    (destination / "models.json").write_text(json.dumps(manifest))
    with allocation(tmp_path) as budget, pytest.raises(ValueError, match="different pins"):
        ml_prepare.prepare(destination, budget)


@pytest.mark.parametrize("damage", ["traversal", "symlink", "unexpected", "missing", "expanded_length", "checksum"])
def test_invalid_syntax_archive_preserves_existing_generation(tmp_path, syntax_download, damage):
    _, archive_download = syntax_download
    archive_download(damage)
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "weights").write_bytes(b"working model")
    with allocation(tmp_path) as budget, pytest.raises(ValueError):
        ml_prepare.prepare(tmp_path / "new", budget)
    assert (previous / "weights").read_bytes() == b"working model"
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".prepare-*"))


def test_syntax_quota_includes_wheel_and_expanded_staging(tmp_path, syntax_download, model_download, monkeypatch):
    source = ml_prepare.MODELS["syntax"]["source"]
    required = (sum(map(len, model_download.values())) + source["size"]
                + ml_prepare.MODELS["syntax"]["expanded_size"] + 1024 * 1024)
    original = ml_prepare.urllib.request.urlopen
    monkeypatch.setattr(ml_prepare.urllib.request, "urlopen", lambda url, timeout:
                        original(url, timeout) if "/api/models/" in url else pytest.fail("Quota must reject before download"))
    with allocation(tmp_path, max_bytes=required - 1) as budget, pytest.raises(ValueError, match="ceiling"):
        ml_prepare.prepare(tmp_path / "new", budget)
    assert not (tmp_path / "new").exists()
