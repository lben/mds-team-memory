FROM mds-ml-compat:ubi8.10

COPY requirements.txt requirements-ml.txt /home/mds/
COPY tools/ml-container/requirements-linux.lock /home/mds/requirements-linux.lock
RUN uv venv /home/mds/.venv && \
    uv pip sync --python /home/mds/.venv/bin/python --no-cache \
      --only-binary :all: --require-hashes \
      --torch-backend cpu \
      /home/mds/requirements-linux.lock
ENV PATH="/home/mds/.venv/bin:${PATH}" \
    OMP_NUM_THREADS=4 \
    MKL_NUM_THREADS=4 \
    TOKENIZERS_PARALLELISM=false
CMD ["python", "-c", "import torch; from gliner2 import AutoExtractor; from sentence_transformers import SentenceTransformer; assert torch.version.cuda is None; print('CPU ML imports OK:', torch.__version__)"]
