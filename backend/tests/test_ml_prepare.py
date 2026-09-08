"""Exercise model preparation with only its external download boundary replaced."""

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ml_prepare
from ml_storage import allocation


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
