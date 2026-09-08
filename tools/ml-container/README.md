# Red Hat 8.10 compatibility image

Build from the repository root on a machine with internet access:

```sh
docker build --platform linux/amd64 -t mds-ml-compat:ubi8.10 tools/ml-container
```

The image pins Red Hat UBI 8.10, uv 0.12.10 and Python 3.12.14. Python includes
SQLite 3.53.1, which contains the WAL-reset fix. Commands run as UID 10001.
The compatibility image is about 351 MB before application or model dependencies.

The current Docker Desktop engine has about 5.8 GiB RAM available. Use the
4 GiB test limit without changing global Docker settings. The planned worker
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
  --cpus 4 --memory 4g --memory-swap 4g --pids-limit 256 \
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

To exercise both downloaded models with network access disabled:

```sh
docker run --rm --platform linux/amd64 --network none \
  --cpus 4 --memory 4g --memory-swap 4g --pids-limit 256 \
  --mount "type=bind,src=$PWD/tools/ml-container,dst=/checks,readonly" \
  --mount "type=bind,src=$PWD/data/ml-assets,dst=/models,readonly" \
  mds-ml-runtime:ubi8.10 python /checks/verify_models.py /models
```

This checks model loading, source span offsets and finite normalized 1,024-element
embeddings. It does not measure the quality of concepts or relationships.

Run the complete backend suite in a test layer so process and calibration checks
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
