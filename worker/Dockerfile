# Worker image (Python 3.12, uv-managed). Build context = ./worker.
FROM python:3.12-slim-bookworm

# uv for dependency management (per project convention).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app
COPY pyproject.toml ./
COPY sbflow_worker ./sbflow_worker

# Resolve + install into a project venv (main deps only; no dev group).
RUN uv sync

CMD ["uv", "run", "sbflow-worker"]
