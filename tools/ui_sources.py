"""Portable fingerprints for inputs to the frozen frontend build.

Deployment assets keep byte-exact hashes. Source text alone normalizes Git's
LF/CRLF checkout difference; editing code still invalidates the bundled UI.
"""

import hashlib
import os
from pathlib import Path

from ml_bundle import regular_file

POLICY = "frontend-build-inputs-lf-v1"
TEXT_SUFFIXES = {".vue", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                 ".json", ".css", ".scss", ".sass", ".less", ".html",
                 ".svg", ".yaml", ".yml"}


def frontend_sources(root: Path):
    result = {}
    frontend = root / "frontend"
    for directory, directories, names in os.walk(frontend):
        relative = Path(directory).relative_to(frontend)
        directories[:] = sorted(name for name in directories
                                if name not in {"node_modules", "dist", "dist-ssr", ".git"}
                                and not (relative == Path(".") and name in {".vscode", ".idea"}))
        for name in sorted(names):
            path = Path(directory) / name
            if name in {".gitignore", ".DS_Store"} or path.suffix == ".tsbuildinfo":
                continue
            if relative == Path(".") and name.lower() in {"readme", "readme.md", "readme.rst", "readme.txt"}:
                continue
            regular_file(path)
            data = path.read_bytes()
            if path.suffix.lower() in TEXT_SUFFIXES:
                data = data.replace(b"\r\n", b"\n")
            result[path.relative_to(root).as_posix()] = {
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return dict(sorted(result.items()))


def check_frontend(root: Path, expected):
    actual = frontend_sources(root)
    if actual.keys() != expected.keys():
        added = sorted(actual.keys() - expected.keys())
        removed = sorted(expected.keys() - actual.keys())
        raise ValueError(f"Frontend build inputs differ from the packaged UI: added={added}, removed={removed}. "
                         "Pull a matching source and deployment package; custom UI changes require a maintainer build.")
    for name, fingerprint in actual.items():
        if fingerprint != expected[name]:
            raise ValueError(f"Bundled UI is stale for {name}. Pull a matching source and deployment package; "
                             "custom UI changes require a maintainer build.")
