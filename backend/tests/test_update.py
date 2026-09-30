"""Deployment contract: one password, intact assets, safe staging and recovery."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import deploy
import deploylib
import ml_assets
import ml_bundle
import update
import update_remote
import ui_sources
import deployment_package
from ml_storage import allocation


def _frontend_fixture(root):
    for name, data in {
        "src/App.vue": b"<template>Working UI</template>\n",
        "package.json": b'{"scripts":{"build":"vite build"}}\n',
        "public/logo.png": b"\x89PNG\r\n\x1a\n\x00payload",
        ".gitignore": b"node_modules\ndist\n",
        "README.md": b"Instructions\n",
        ".vscode/extensions.json": b'{"recommendations":[]}\n',
    }.items():
        path = root / "frontend" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def test_ui_fingerprint_accepts_windows_line_endings_and_local_editor_files(tmp_path):
    _frontend_fixture(tmp_path)
    expected = ui_sources.frontend_sources(tmp_path)
    for name in ("src/App.vue", "package.json", ".gitignore"):
        path = tmp_path / "frontend" / name
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    for name in ("README.md", ".gitignore", ".vscode/extensions.json", ".idea/local.xml",
                 "node_modules/local.js", "dist/index.html", "dist-ssr/server.js", "tsconfig.tsbuildinfo"):
        path = tmp_path / "frontend" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("local metadata/build output\n")
    ui_sources.check_frontend(tmp_path, expected)
    assert set(expected) == {"frontend/src/App.vue", "frontend/package.json", "frontend/public/logo.png"}


@pytest.mark.parametrize("name", ["src/App.vue", "package.json", "public/logo.png"])
def test_ui_fingerprint_rejects_real_source_or_binary_changes(tmp_path, name):
    _frontend_fixture(tmp_path)
    expected = ui_sources.frontend_sources(tmp_path)
    path = tmp_path / "frontend" / name
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="Bundled UI is stale"):
        ui_sources.check_frontend(tmp_path, expected)


@pytest.mark.parametrize("change", ["added", "removed"])
def test_ui_fingerprint_rejects_added_or_removed_build_inputs(tmp_path, change):
    _frontend_fixture(tmp_path)
    expected = ui_sources.frontend_sources(tmp_path)
    if change == "added":
        (tmp_path / "frontend/src/new.ts").write_text("export const changed = true\n")
    else:
        (tmp_path / "frontend/src/App.vue").unlink()
    with pytest.raises(ValueError, match=f"{change}="):
        ui_sources.check_frontend(tmp_path, expected)


def test_ui_fingerprint_keeps_non_text_assets_byte_exact(tmp_path):
    _frontend_fixture(tmp_path)
    expected = ui_sources.frontend_sources(tmp_path)
    path = tmp_path / "frontend/public/logo.png"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    with pytest.raises(ValueError, match="public/logo.png"):
        ui_sources.check_frontend(tmp_path, expected)


def test_update_accepts_crlf_sources_but_still_refuses_corrupted_shipped_ui(tmp_path, monkeypatch):
    _frontend_fixture(tmp_path)
    directory = tmp_path / "deployment"
    manifest_path = directory / "offline/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n")
    ui = directory / "ui/index.html"
    ui.parent.mkdir()
    ui.write_bytes(b"<html>verified built UI</html>\n")
    lock = tmp_path / "tools/ml-container/requirements-linux.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("fixture==1\n")
    package = {"version": 1, "transfer_manifest": ml_bundle.record(manifest_path),
               "files": {"ui/index.html": ml_bundle.record(ui)},
               "frontend_source_policy": ui_sources.POLICY,
               "frontend_sources": ui_sources.frontend_sources(tmp_path),
               "dependency_lock": ml_bundle.record(lock)}
    (directory / "package.json").write_text(json.dumps(package))
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(deployment_package, "manifest_at", lambda _: {"metadata": {"model_roles": ["extractor", "embeddings", "syntax"]}})
    monkeypatch.setattr(deployment_package, "stream_parts", lambda *a: None)
    for path in (tmp_path / "frontend").rglob("*"):
        if path.is_file() and path.suffix != ".png":
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    update.checked_package()
    ui.write_bytes(ui.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(ValueError, match="checksum mismatch: ui/index.html"):
        update.checked_package()


def test_cli_target_is_case_insensitive_and_defaults_to_uat():
    parser = argparse.ArgumentParser()
    deploylib.add_target_argument(parser)
    assert parser.parse_args([]).target == "uat"
    assert parser.parse_args(["PROD"]).target == "prod"
    assert parser.parse_args(["UAT"]).target == "uat"


def test_update_rejects_shared_environment_paths_and_ports(tmp_path):
    config = tmp_path / "deploy.toml"
    config.write_text('[uat]\nhost="mds@server"\nroot="/app"\n[prod]\nhost="mds@server"\nroot="/app"\n')
    with pytest.raises(ValueError, match="distinct deployment roots"):
        update.configured_target("uat", config)
    config.write_text(config.read_text().replace('[prod]\nhost="mds@server"\nroot="/app"', '[prod]\nhost="mds@server"\nroot="/other"'))
    with pytest.raises(ValueError, match="distinct application ports"):
        update.configured_target("uat", config)


def test_update_configuration_supports_bom_and_bundled_defaults(tmp_path):
    config = tmp_path / "deploy.toml"
    config.write_text('\ufeff[uat]\nhost="mds@server"\nroot="/app"\nssh_port=2222\n')
    target, _ = update.configured_target("uat", config)
    assert (target.uv, target.python, target.ssh_port) == ("bundled", "bundled", 2222)


def test_compressed_bundle_reassembles_and_verifies_all_models(tmp_path):
    models, wheels = tmp_path / "models", tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "fixture-1-py3-none-any.whl").write_bytes(b"fixture")
    manifest = {"version": 1, "models": {}}
    for role in ("extractor", "embeddings", "syntax"):
        path = models / role / "weights"
        path.parent.mkdir(parents=True)
        path.write_bytes((role.encode() + b'\0') * 10000)
        manifest["models"][role] = {"repository": "fixture/model", "revision": "a" * 40, "license": "mit",
                                    "files": [{"path": "weights", **ml_bundle.record(path)}]}
    (models / "models.json").write_text(json.dumps(manifest))
    lock = tmp_path / "requirements-linux.lock"
    lock.write_text("fixture==1\n")
    archive = tmp_path / "managed/bundle.tar.gz"
    with allocation(archive.parent) as budget:
        ml_bundle.pack(archive, models, wheels, lock, budget)
        parts = ml_assets.split(archive, archive.parent / "parts", 128, {}, budget)
        joined = ml_assets.assemble(parts, archive.parent / "received", False, budget)
        ml_bundle.extract(joined, archive.parent / "generation", budget)
    result, _ = ml_bundle.model_files(archive.parent / "generation/models")
    assert result == manifest
    assert archive.stat().st_size < sum(p.stat().st_size for p in models.rglob("*") if p.is_file())


def test_transfer_failure_never_stops_running_server(tmp_path, monkeypatch):
    target = deploylib.Target("uat", {"host": "mds@server", "root": "/app"})
    archive = tmp_path / "release.tar.gz"
    archive.touch()
    calls = []
    monkeypatch.setattr(deploy, "assemble_release", lambda *a: None)
    monkeypatch.setattr(deploy, "pack_release", lambda *a: archive)
    monkeypatch.setattr(deploy, "write_env_file", lambda *a: tmp_path / "env")
    monkeypatch.setattr(deploy, "upload", lambda *a: None)
    monkeypatch.setattr(deploy, "ctl", lambda _, command, *a: calls.append(command) or subprocess.CompletedProcess(command, 0))
    def refused(*args):
        raise deploy.DeployError("uploading model parts")
    with pytest.raises(SystemExit, match="1"):
        deploy.deploy_release(target, skip_build=True, prepare=refused)
    assert calls == ["unpack"]


def test_existing_generation_is_verified_and_skips_upload(monkeypatch):
    target = deploylib.Target("uat", {"host": "mds@server", "root": "/app"})
    target.python = "/python"
    target.env["MDS_ML_GENERATION"] = "/app/ml/generations/id"
    monkeypatch.setattr(update, "ssh", lambda *a, **kw: subprocess.CompletedProcess("probe", 0))
    monkeypatch.setattr(update, "scp", lambda *a: pytest.fail("verified existing models must not upload"))
    update.model_preparer(Path("/package"), {}, {"archive": {"sha256": "id"}})(target, "stamp")


def test_model_corruption_refuses_before_upload(monkeypatch):
    target = deploylib.Target("uat", {"host": "mds@server", "root": "/app"})
    target.python = "/python"
    target.env["MDS_ML_GENERATION"] = "/app/ml/generations/id"
    monkeypatch.setattr(update, "ssh", lambda *a, **kw: subprocess.CompletedProcess("probe", 1, "", "checksum mismatch"))
    monkeypatch.setattr(update, "scp", lambda *a: pytest.fail("corrupt generation must not be overwritten"))
    with pytest.raises(deploy.DeployError):
        update.model_preparer(Path("/package"), {}, {"archive": {"sha256": "id"}})(target, "stamp")


def test_ssh_session_prompts_once_and_never_puts_password_in_commands(tmp_path, monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    import update_session
    target = deploylib.Target("uat", {"host": "mds@server", "root": "/app"})
    passwords, connections, commands = [], [], []
    class Channel:
        def shutdown_write(self): pass
        def recv_ready(self): return False
        def recv_stderr_ready(self): return False
        def recv(self, size): return b""
        def recv_stderr(self, size): return b""
        def exit_status_ready(self): return True
        def recv_exit_status(self): return 0
        def close(self): pass
    class Client:
        def load_system_host_keys(self): pass
        def load_host_keys(self, path): pass
        def set_missing_host_key_policy(self, policy): pass
        def connect(self, **kwargs): connections.append(kwargs)
        def get_transport(self): return self
        def set_keepalive(self, interval): pass
        def open_sftp(self): return self
        def close(self): pass
        def exec_command(self, command):
            commands.append(command)
            return None, type("Stream", (), {"channel": Channel()})(), None
    def password(prompt):
        passwords.append(prompt)
        return "test-secret"
    monkeypatch.setattr(update_session.getpass, "getpass", password)
    session = update_session.Session(target, tmp_path / "known_hosts", client_factory=Client)
    session.run("first step")
    session.run("second step")
    session.close()
    assert len(passwords) == len(connections) == 1
    assert "password" not in connections[0]
    assert connections[0]["auth_strategy"].password is None
    assert all("test-secret" not in command for command in commands)
    assert not hasattr(session, "password")


def test_environment_refuses_invalid_shell_keys():
    with pytest.raises(SystemExit):
        deploylib.Target("uat", {"host": "mds@server", "root": "/app", "env": {"1BAD": "value"}})


def test_native_ml_library_failure_blocks_setup_before_runtime_activation(tmp_path, monkeypatch):
    import zipfile
    root = tmp_path / "server"
    managed = root / "ml"
    generation = managed / "generations/id"
    release = root / "releases/new"
    release.mkdir(parents=True)
    (generation / "wheels").mkdir(parents=True)
    with zipfile.ZipFile(generation / "wheels/fixture-1-py3-none-any.whl", "w") as wheel:
        wheel.writestr("fixture.py", "value=1")
    monkeypatch.setattr(deploylib, "ml_preflight", lambda _: {})
    monkeypatch.setattr(deploylib, "ml_generation", lambda *args: {"lock_sha256": "a" * 64})
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1] == "venv":
            Path(command[-1]).mkdir(parents=True, exist_ok=True)
        if "import torch, spacy" in " ".join(command):
            assert kwargs["env"]["UV_OFFLINE"] == "1"
            assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(deploylib.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        deploylib.ml_setup(root, release, generation, managed, "/uv")
    assert not (release / "ml-runtime.json").exists()
    assert not (release / ".ml-venv").exists()
    assert any("import torch, spacy" in " ".join(command) for command in calls)
