# Red Hat 8.10 compatibility image

Build from the repository root on a machine with internet access:

```sh
docker build --platform linux/amd64 -t mds-ml-compat:ubi8.10 tools/ml-container
```

The image pins Red Hat UBI 8.10, uv 0.12.10 and Python 3.12.14. Python includes
SQLite 3.53.1, which contains the WAL-reset fix. Commands run as UID 10001.
The compatibility image is about 351 MB before application or model dependencies.

The current Docker Desktop engine has about 5.8 GiB RAM available. The complete
four-model checks use a 5 GiB container limit without changing global Docker
settings. Run heavy inference checks sequentially to avoid host-memory pressure.
The planned worker
ceiling of 8 GiB needs a host with sufficient assigned memory.

UBI provides RHEL 8.10 userspace and glibc 2.28. Docker supplies the host kernel.
On an ARM Mac, `linux/amd64` uses emulation. This verifies package and application
compatibility; production CPU throughput and RHEL kernel resource controls
still require a check on the deployment server.

## CPU ML runtime

After building the compatibility image, build the ML variant from the repository
root. The lock includes application dependencies and the official PyTorch CPU
wheel. Every package version and accepted archive hash is pinned.

```sh
docker build --platform linux/amd64 -t mds-ml-runtime:ubi8.10 \
  -f tools/ml-container/Dockerfile.ml .
docker run --rm --platform linux/amd64 --network none \
  --cpus 4 --memory 5g --memory-swap 5g --pids-limit 256 \
  mds-ml-runtime:ubi8.10
```

The default command checks CPU-only PyTorch, GLiNER2 and Sentence Transformers
imports. Model inference needs the separately prepared local model assets.
Regenerate the lock inside the compatibility image when requirements change:

```sh
docker run --rm --platform linux/amd64 \
  --cpus 4 --memory 4g --memory-swap 4g --pids-limit 256 \
  --mount "type=bind,src=$PWD,dst=/src,readonly" --workdir /src \
  mds-ml-compat:ubi8.10 uv pip compile requirements-ml.txt \
    --python-version 3.12 --python-platform x86_64-manylinux_2_28 \
    --torch-backend cpu --only-binary :all: --generate-hashes \
    --no-annotate --no-header > requirements-linux.new.lock
```

Check the generated file before replacing `tools/ml-container/requirements-linux.lock`.

Prepare a complete generation with `tools/ml_prepare.py` before testing. The
worker requires extraction, embedding, syntax, and verifier assets; a historical
two-model generation is incomplete. To exercise all four models with network
access disabled, mount the repository so the check uses the actual application
runtime:

```sh
docker run --rm --platform linux/amd64 --network none \
  --cpus 4 --memory 5g --memory-swap 5g --pids-limit 256 \
  --mount "type=bind,src=$PWD,dst=/repo,readonly" \
  --mount "type=bind,src=$PWD/data/ml-assets,dst=/models,readonly" \
  mds-ml-runtime:ubi8.10 python /repo/tools/ml-container/verify_models.py /models
```

This verifies the asset hashes, model loading, source span offsets, finite
normalized 1,024-element embeddings, and an actual parser-corroborated alias
record. It is a compatibility smoke check, not a quality evaluation. Rebuild
an older image if it lacks the pinned spaCy dependencies; passing unit tests
alone does not prove the full inference environment is installed.

Run the complete backend suite in a test layer so worker and offline checks
use the Linux ML environment. Build this layer while downloads are available,
then run the suite with network access disabled:

```sh
docker build --platform linux/amd64 -t mds-ml-tests:ubi8.10 - <<'DOCKERFILE'
FROM mds-ml-runtime:ubi8.10
RUN uv pip install --python /home/mds/.venv/bin/python --no-cache pytest==9.1.1 httpx==0.28.1
DOCKERFILE
docker run --rm --platform linux/amd64 --network none \
  --cpus 2 --memory 1500m --memory-swap 1500m --user 10001:10001 \
  --mount "type=bind,src=$PWD,dst=/src,readonly" --workdir /src \
  mds-ml-tests:ubi8.10 python -m pytest backend/tests -q -p no:cacheprovider
```

## Portable verifier wheel for offline Update

`Dockerfile.verifier` builds llama-cpp-python 0.3.35 on the pinned UBI 8.10
compatibility image. It disables native/AVX extensions and links GCC 8's
`stdc++fs` support explicitly. Its wheel hash is pinned in the Linux lock;
normal Update runs install this wheel without compilers or network access.

```sh
docker build --platform linux/amd64 -t mds-update-wheel:ubi8.10 \
  -f tools/ml-container/Dockerfile.verifier .
```

The four-model compatibility helper checks a finite verifier margin in a
separate process before loading the extractor/embeddings/parser. Update also
checks native-library imports before stopping an existing release.
