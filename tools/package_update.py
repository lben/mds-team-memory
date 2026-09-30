#!/usr/bin/env python3
"""Maintainer-only: freeze the checked-in offline package for the Update CLI.

Build frontend/dist first. Supply verified Linux wheels and a Linux-created
bootstrap archive with Python/uv. BGE shares the existing embedding checkpoint.
Normal deployment needs only the completed package, not this preparation tool.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploylib import ROOT
from ml_assets import split, stream_parts, manifest_at
from ml_bundle import pack, record, model_files
from ml_storage import allocation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build/update-package")
    args = parser.parse_args()
    directory = ROOT / "deployment"
    work = args.work_dir
    archive = work / "offline-models.tar.gz"
    models, _ = model_files(args.models)
    if set(models["models"]) != {"extractor", "embeddings", "syntax"}:
        raise ValueError("A complete three-model BGE generation is required")
    directory.mkdir(exist_ok=True)
    if archive.exists() or (directory / "offline").exists():
        raise ValueError("Use a new clean output; do not overwrite an existing frozen package")
    with allocation(work, [args.models, args.wheels]) as budget:
        pack(archive, args.models, args.wheels, ROOT / "tools/ml-container/requirements-linux.lock", budget)
    # Archives and split copies must both be accounted for until splitting is
    # complete. The archive is disposable build output; tracked parts survive.
    with allocation(directory, [archive]) as budget:
        manifest_path = split(archive, directory / "offline", 95_000_000,
                              {"model_roles": sorted(models["models"]), "models": models["models"]}, budget)
    stream_parts(manifest_path, manifest_at(manifest_path))
    shutil.copy2(args.bootstrap, directory / "bootstrap.tar.gz")
    shutil.copytree(ROOT / "frontend/dist", directory / "ui", dirs_exist_ok=True)
    files = {p.relative_to(directory).as_posix(): record(p) for p in sorted((directory / "ui").rglob("*")) if p.is_file()}
    files["bootstrap.tar.gz"] = record(directory / "bootstrap.tar.gz")
    files.update({p.relative_to(directory).as_posix(): record(p) for p in sorted((directory / "licenses").rglob("*")) if p.is_file()})
    sources = {p.relative_to(ROOT).as_posix(): record(p) for p in sorted((ROOT / "frontend").rglob("*"))
               if p.is_file() and not any(x in p.relative_to(ROOT / "frontend").parts for x in ("node_modules", "dist"))
               and p.suffix != ".tsbuildinfo" and p.name != ".DS_Store"}
    (directory / "package.json").write_text(json.dumps({
        "version": 1, "target": "RHEL 8.10 x86_64 / Python 3.12 / CPU only",
        "transfer_manifest": record(manifest_path), "files": files,
        "frontend_sources": sources, "dependency_lock": record(ROOT / "tools/ml-container/requirements-linux.lock"),
        "bundle_total_size": sum(p.stat().st_size for p in args.models.rglob("*") if p.is_file())
                             + sum(p.stat().st_size for p in args.wheels.glob("*.whl"))
                             + (ROOT / "tools/ml-container/requirements-linux.lock").stat().st_size,
    }, indent=2) + "\n")
    print("Prepared", directory, flush=True)


if __name__ == "__main__":
    main()
