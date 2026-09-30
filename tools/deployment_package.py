"""Validate the shipped package and an optional local frontend build."""

import json
import re
from pathlib import Path

from ml_assets import manifest_at, stream_parts
from ml_bundle import record
from ui_sources import POLICY, check_frontend


def local_build_pointer(root):
    return root / "build/update-ui.json"


def verify_ui(directory, expected):
    if not isinstance(expected, dict):
        raise ValueError("Invalid UI checksum manifest; run Build.cmd again")
    if "ui/index.html" not in expected:
        raise ValueError("Built UI is missing index.html")
    for name, fingerprint in expected.items():
        path = directory / name
        if not name.startswith("ui/") or not path.resolve().is_relative_to((directory / "ui").resolve()):
            raise ValueError(f"Unsafe UI package path: {name}")
        if record(path) != fingerprint:
            raise ValueError(f"Deployment package checksum mismatch: {name}")
    actual = {f"ui/{p.relative_to(directory / 'ui').as_posix()}"
              for p in (directory / "ui").rglob("*") if p.is_file()}
    if actual != expected.keys():
        raise ValueError("Bundled UI contains missing or unexpected files")


def checked_package(root: Path, *, use_local=True, check_sources=True, verify_models=True):
    directory = root / "deployment"
    package_path = directory / "package.json"
    release = json.loads(package_path.read_text(encoding="utf-8"))
    if release.get("version") != 1:
        raise ValueError("Unsupported deployment package version")
    manifest_path = directory / "offline/manifest.json"
    manifest = manifest_at(manifest_path)
    if record(manifest_path) != release["transfer_manifest"]:
        raise ValueError("The transfer manifest differs from the release package")
    if set(manifest["metadata"].get("model_roles", [])) != {"extractor", "embeddings", "syntax"}:
        raise ValueError("The offline package must contain the three required BGE model roles")
    if verify_models:
        print("Checking local model parts and deployment package...", flush=True)
        stream_parts(manifest_path, manifest)
    else:
        print("Checking deployment package metadata and UI...", flush=True)
    for name, expected in release["files"].items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or record(path) != expected:
            raise ValueError(f"Deployment package checksum mismatch: {name}")
    verify_ui(directory, {name: expected for name, expected in release["files"].items() if name.startswith("ui/")})
    if release.get("frontend_source_policy") != POLICY:
        raise ValueError("Deployment package needs the portable UI source manifest; pull the updated deployment branch")
    if record(root / "tools/ml-container/requirements-linux.lock") != release["dependency_lock"]:
        raise ValueError("The offline dependency lock differs from this checkout; a full model/dependency package is required")

    ui = directory / "ui"
    pointer = local_build_pointer(root)
    if use_local and pointer.exists():
        local = json.loads(pointer.read_text(encoding="utf-8"))
        if not isinstance(local, dict):
            raise ValueError("Invalid local UI manifest; run Build.cmd again")
        if local.get("base_package") == record(package_path):
            if local.get("version") != 1 or local.get("frontend_source_policy") != POLICY:
                raise ValueError("Unsupported local UI build; run Build.cmd again")
            if not isinstance(local.get("frontend_sources"), dict):
                raise ValueError("Invalid local UI source manifest; run Build.cmd again")
            generation = local.get("generation", "")
            if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
                raise ValueError("Invalid local UI build path; run Build.cmd again")
            local_directory = root / "build/update-ui" / generation
            if not local_directory.resolve().is_relative_to((root / "build").resolve()):
                raise ValueError("Local UI build escapes the build directory")
            verify_ui(local_directory, local["ui_files"])
            release = {**release, "frontend_sources": local["frontend_sources"],
                       "files": {**{name: spec for name, spec in release["files"].items() if not name.startswith("ui/")},
                                 **local["ui_files"]}}
            ui = local_directory / "ui"
            print("Using verified local UI build.", flush=True)
        else:
            print("Previous local UI build belongs to another package; using the newly pulled package.", flush=True)
    if check_sources:
        check_frontend(root, release["frontend_sources"])
    return directory, release, manifest, ui
