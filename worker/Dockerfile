# Worker image (Python 3.12, uv-managed).
#
# NOTE: build context is the REPO ROOT (see docker-compose.yml) so the image can
# ship the sandbox build context (../sandbox) for lazy `ensure_image`. The worker
# launches sibling verification containers via the host Docker daemon
# (Docker-out-of-Docker), so it needs the docker CLI + the mounted socket.
FROM python:3.12-slim-bookworm

# uv for dependency management (per project convention).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Docker CLI only (the daemon is the host's, reached over the mounted socket).
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY worker/pyproject.toml ./
COPY worker/sbflow_worker ./sbflow_worker

# Sandbox build context, so the worker can pre-bake the image on first run if the
# host daemon doesn't already have it.
COPY sandbox /opt/sandbox

# Resolve + install into a project venv (main deps only; no dev group).
RUN uv sync

CMD ["uv", "run", "sbflow-worker"]
