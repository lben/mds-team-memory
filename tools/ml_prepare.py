"""Acquire complete, pinned ML assets outside the offline deployment environment."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ml_assets import canonical_json
from ml_bundle import model_files
from ml_storage import add_budget_arguments, allocation, publish_generation


MODELS = {
    "extractor": {
        "repository": "fastino/gliner2.5-base-v1",
        "revision": "72ac19b486cd4557424c8d61114e7530c243e9b0",
        "license": "apache-2.0",
        "files": ["README.md", "config.json", "encoder_config/config.json",
                  "model.safetensors", "tokenizer.json", "tokenizer_config.json"],
    },
    "embeddings": {
        "repository": "BAAI/bge-large-en-v1.5",
        "revision": "d4aa6901d3a41ba39fb536a557fa166f842b0e09",
        "license": "mit",
        "files": ["README.md", "1_Pooling/config.json", "config.json",
                  "config_sentence_transformers.json", "model.safetensors", "modules.json",
                  "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json",
                  "tokenizer_config.json", "vocab.txt"],
    },
}
def read_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def digests(path):
    sha256 = hashlib.sha256()
    git = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
            git.update(chunk)
    return sha256.hexdigest(), git.hexdigest()


def validate_file(path, metadata):
    if not path.is_file() or path.is_symlink() or path.stat().st_size != metadata["size"]:
        return None
    sha256, git = digests(path)
    expected = metadata.get("lfs", {}).get("sha256")
    if (sha256 if expected else git) != (expected or metadata["blobId"]):
        return None
    return {"path": metadata["rfilename"], "size": metadata["size"], "sha256": sha256}


def download_file(url, target, metadata, budget):
    budget.check(metadata["size"])
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(url, timeout=60) as response:
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > metadata["size"]:
                    raise ValueError(f"Unexpected file length: {target.name}")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        entry = validate_file(Path(temporary), metadata)
        if not entry:
            raise ValueError(f"Upstream hash/length verification failed: {target.name}")
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
        return entry
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(destination, budget):
    budget.contains(destination)
    if os.path.lexists(destination):
        existing, _ = model_files(destination)
        if set(existing["models"]) != set(MODELS):
            raise ValueError("Existing generation has different models; choose a new destination")
        for role, spec in MODELS.items():
            found = existing["models"][role]
            if (any(found[key] != spec[key] for key in ("repository", "revision", "license"))
                    or {entry["path"] for entry in found["files"]} != set(spec["files"])):
                raise ValueError("Existing generation has different pins; choose a new destination")
        return existing
    manifest = {"version": 1, "models": {}}
    upstream = {}
    for role, spec in MODELS.items():
        repo, revision = spec["repository"], spec["revision"]
        metadata = read_json(f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true")
        if metadata.get("sha") != revision:
            raise ValueError(f"Unexpected model revision: {repo}")
        files = {entry["rfilename"]: entry for entry in metadata["siblings"]}
        for name in spec["files"]:
            if type(files[name].get("size")) is not int or files[name]["size"] < 0:
                raise ValueError(f"Invalid upstream file size: {role}/{name}")
        upstream[role] = files
    required = sum(upstream[role][name]["size"] for role, spec in MODELS.items() for name in spec["files"])
    budget.check(required + 1024 * 1024)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=destination.parent))
    try:
        for role, spec in MODELS.items():
            entries = []
            for name in spec["files"]:
                print(f"Downloading {role}/{name}", flush=True)
                entries.append(download_file(
                    f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}/{name}",
                    temporary / role / name, upstream[role][name], budget))
            manifest["models"][role] = {key: spec[key] for key in ("repository", "revision", "license")}
            manifest["models"][role]["files"] = entries
        data = canonical_json(manifest)
        budget.check(len(data.encode("utf-8")))
        with (temporary / "models.json").open("w", encoding="utf-8", newline="\n") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary / "models.json", 0o644)
        budget.check()
        publish_generation(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    add_budget_arguments(parser)
    args = parser.parse_args()
    try:
        with allocation(args.managed_root, args.account, args.max_bytes, args.reserve_bytes) as budget:
            manifest = prepare(args.destination.absolute(), budget)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Model preparation failed: {error}\n")
    print(f"Verified {len(manifest['models'])} pinned models in {args.destination}")


if __name__ == "__main__":
    main()
