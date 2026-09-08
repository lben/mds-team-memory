# Build with --platform linux/amd64. This is a compatibility test image.
FROM ghcr.io/astral-sh/uv@sha256:34c0f9a55abeb840c34391bb7b63a1a06f8cb9281e028f27757a01a1a8a2d461 AS uv
FROM registry.access.redhat.com/ubi8/ubi@sha256:2dcfc23b79d88fdaeefc6ed0cfb144e41dca600bfa73c38ab5d8dca5b05d1b7f

COPY --from=uv /uv /usr/local/bin/uv
RUN useradd --uid 10001 --create-home mds
USER 10001:10001
ENV PATH="/home/mds/.local/bin:${PATH}" \
    UV_PYTHON_INSTALL_DIR=/home/mds/python \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1
RUN uv python install --default 3.12.14 && uv cache clean
WORKDIR /home/mds
CMD ["python", "--version"]
