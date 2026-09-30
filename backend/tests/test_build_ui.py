"""Build/Update contract: local UI builds must not replace shipped ML assets."""

import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import build_ui
import deploy
import deployment_package
import ml_bundle
import ui_sources
import update


@pytest.fixture
def package(tmp_path, monkeypatch):
    for name, content in {
        "frontend/src/App.vue": "<template>source</template>\n",
        "frontend/package.json": '{"scripts":{"build":"vite build"}}\n',
        "frontend/package-lock.json": '{"lockfileVersion":3}\n',
        "deployment/offline/manifest.json": "{}\n",
        "deployment/ui/index.html": "shipped UI\n",
        "deployment/ui/old.js": "shipped old asset\n",
        "deployment/bootstrap.tar.gz": "bootstrap fixture",
        "tools/ml-container/requirements-linux.lock": "fixture==1\n",
    }.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    release = {"version": 1, "transfer_manifest": ml_bundle.record(tmp_path / "deployment/offline/manifest.json"),
               "frontend_source_policy": ui_sources.POLICY, "frontend_sources": ui_sources.frontend_sources(tmp_path),
               "dependency_lock": ml_bundle.record(tmp_path / "tools/ml-container/requirements-linux.lock"),
               "files": {name: ml_bundle.record(tmp_path / "deployment" / name)
                         for name in ("ui/index.html", "ui/old.js", "bootstrap.tar.gz")}}
    (tmp_path / "deployment/package.json").write_text(json.dumps(release))
    monkeypatch.setattr(deployment_package, "manifest_at", lambda _: {"metadata": {"model_roles": ["extractor", "embeddings", "syntax"]},
                                                                    "parts": [], "archive": {"sha256": "fixture", "size": 0}})
    monkeypatch.setattr(deployment_package, "stream_parts", lambda *a: None)
    return tmp_path


def fake_npm(root, monkeypatch, *, fail=False, mutate=False, index=True):
    calls = []
    monkeypatch.setattr(build_ui.shutil, "which", lambda name: f"/tool/{name}")
    monkeypatch.setattr(build_ui.subprocess, "check_output", lambda *a, **kw: "v22.23.2\n")
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == root / "frontend"
        assert kwargs["env"]["NODE_USE_SYSTEM_CA"] == "1"
        assert kwargs["check"]
        if command[1:] == ["run", "build"]:
            if fail:
                raise subprocess.CalledProcessError(1, command)
            if mutate:
                (root / "frontend/src/App.vue").write_text("changed during build\n")
            dist = root / "frontend/dist"
            dist.mkdir(exist_ok=True)
            if index:
                (dist / "index.html").write_text("locally built UI\n")
            (dist / "new.js").write_text("new asset\n")
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(build_ui.subprocess, "run", run)
    return calls


def test_unchanged_package_build_skips_node_and_preserves_shipped_files(package, monkeypatch):
    before = {p: p.read_bytes() for p in (package / "deployment").rglob("*") if p.is_file()}
    monkeypatch.setattr(build_ui.shutil, "which", lambda _: pytest.fail("unchanged UI needs no Node/npm"))
    build_ui.build(package)
    assert not deployment_package.local_build_pointer(package).exists()
    assert all(p.read_bytes() == content for p, content in before.items())


def test_changed_source_build_is_selected_by_update_and_shipped_assets_stay_intact(package, monkeypatch):
    before = {p: p.read_bytes() for p in (package / "deployment").rglob("*") if p.is_file()}
    (package / "frontend/src/App.vue").write_text("changed source\n")
    calls = fake_npm(package, monkeypatch)
    build_ui.build(package)
    assert [call[1:] for call in calls] == [["ci", "--no-audit", "--no-fund"], ["run", "build"]]
    _, release, _, ui = deployment_package.checked_package(package)
    assert ui.is_relative_to(package / "build")
    assert (ui / "index.html").read_text() == "locally built UI\n"
    assert not (ui / "old.js").exists()
    assert release["files"]["bootstrap.tar.gz"] == ml_bundle.record(package / "deployment/bootstrap.tar.gz")
    assert all(p.read_bytes() == content for p, content in before.items())
    # A second run reuses the validated local package rather than reinstalling.
    calls.clear()
    build_ui.build(package)
    assert calls == []


@pytest.mark.parametrize("reason", ["npm failure", "source mutation", "missing index", "publish failure"])
def test_failed_rebuild_preserves_last_successful_generation(package, monkeypatch, reason):
    fake_npm(package, monkeypatch)
    build_ui.build(package, force=True)
    pointer = deployment_package.local_build_pointer(package)
    before = pointer.read_bytes()
    _, _, _, old_ui = deployment_package.checked_package(package)
    fake_npm(package, monkeypatch, fail=reason == "npm failure", mutate=reason == "source mutation", index=reason != "missing index")
    if reason == "missing index":
        (package / "frontend/dist/index.html").unlink()
    if reason == "publish failure":
        monkeypatch.setattr(build_ui.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("fixture interruption")))
    with pytest.raises((ValueError, OSError, subprocess.CalledProcessError)):
        build_ui.build(package, force=True)
    assert pointer.read_bytes() == before
    assert (old_ui / "index.html").read_text() == "locally built UI\n"
    assert len(list((package / "build/update-ui").iterdir())) == 1


def test_update_refuses_corrupted_or_stale_local_ui(package, monkeypatch):
    fake_npm(package, monkeypatch)
    build_ui.build(package, force=True)
    _, _, _, ui = deployment_package.checked_package(package)
    (ui / "new.js").write_text("corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        deployment_package.checked_package(package)
    build_ui.build(package)
    (package / "frontend/src/App.vue").write_text("next source change\n")
    with pytest.raises(ValueError, match="Run Build.cmd"):
        deployment_package.checked_package(package)


@pytest.mark.parametrize("path", ["deployment/bootstrap.tar.gz", "tools/ml-container/requirements-linux.lock"])
def test_build_refuses_changed_non_ui_package_before_npm(package, monkeypatch, path):
    (package / path).write_text("different package")
    monkeypatch.setattr(build_ui.shutil, "which", lambda _: pytest.fail("must refuse before npm"))
    with pytest.raises(ValueError):
        build_ui.build(package, force=True)
    assert not deployment_package.local_build_pointer(package).exists()


def test_newly_pulled_package_invalidates_old_local_build(package, monkeypatch):
    fake_npm(package, monkeypatch)
    build_ui.build(package, force=True)
    p = package / "deployment/package.json"
    contents = json.loads(p.read_text())
    contents["release_note"] = "new shipped package"
    p.write_text(json.dumps(contents))
    _, _, _, ui = deployment_package.checked_package(package)
    assert ui == package / "deployment/ui"


@pytest.mark.parametrize("field", ["not_object", "generation", "ui_files", "frontend_sources"])
def test_invalid_local_manifest_is_refused_and_build_repairs_it(package, monkeypatch, field):
    fake_npm(package, monkeypatch)
    build_ui.build(package, force=True)
    pointer = deployment_package.local_build_pointer(package)
    local = json.loads(pointer.read_text())
    if field == "not_object":
        local = None
    else:
        local[field] = "../../outside" if field == "generation" else []
    pointer.write_text(json.dumps(local))
    with pytest.raises(ValueError):
        deployment_package.checked_package(package)
    build_ui.build(package)
    deployment_package.checked_package(package)


def test_parallel_build_is_refused_and_lock_releases(package):
    with build_ui.build_lock(package):
        with pytest.raises(ValueError, match="Another Build"):
            build_ui.build(package)
    build_ui.build(package)


def test_update_deployment_uses_selected_local_ui(package, monkeypatch):
    fake_npm(package, monkeypatch)
    build_ui.build(package, force=True)
    monkeypatch.setattr(update, "ROOT", package)
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kw: None, raising=False)
    target = deploy.Target("uat", {"host": "test@localhost", "root": "/fixture"})
    monkeypatch.setattr(update, "configured_target", lambda *a: (target, package / "known-hosts"))
    class Session:
        def __init__(self, *args): pass
        def close(self): pass
    monkeypatch.setattr(update, "Session", Session)
    monkeypatch.setattr(update, "bootstrap", lambda *a: None)
    monkeypatch.setattr(deploy, "RELEASE_ITEMS", [("frontend/dist", "frontend/dist")])
    monkeypatch.setattr(deploy, "RELEASE_DIR", package / "build/release")
    monkeypatch.setattr(deploy, "BUILD_DIR", package / "build")
    def deploy_local(target, **kwargs):
        assert kwargs["skip_build"]
        deploy.assemble_release(target, "fixture", frontend=kwargs["frontend"])
        archive = deploy.pack_release("fixture")
        with tarfile.open(archive) as tar:
            assert tar.extractfile("frontend/dist/index.html").read() == b"locally built UI\n"
            assert "frontend/dist/old.js" not in tar.getnames()
    monkeypatch.setattr(deploy, "deploy_release", deploy_local)
    assert update.main(["UAT"]) == 0
