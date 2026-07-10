# Pre-baked verification sandbox (N10 / ADR-0006).
#
# A throwaway container is launched from this image (`docker run --rm`) by the
# worker to verify a candidate fix in tiers:
#   tier-1  `dbt compile` / `dbt parse`  (always)
#   tier-2  `dbt build --target sample`  (only if a dev/sample connection is set)
#
# Everything the run needs is baked in (no runtime `pip install`) so a run is a
# cold `docker run` on a resident image, not an install (B-S6). The container is
# network-restricted and never holds prod-write creds (tier-2 targets a
# read-only/dev *sample*, never prod).
FROM python:3.12-slim-bookworm

# git is part of the contract (a real checkout could be a git worktree); dbt
# needs no build toolchain on slim for the postgres adapter wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Pin dbt-core + dbt-postgres so the image is reproducible / pre-baked.
RUN pip install --no-cache-dir "dbt-core==1.9.4" "dbt-postgres==1.9.0"

# The worker mounts the materialized candidate project at /project and passes a
# profiles dir at /profiles; the entrypoint is just dbt invoked by the worker.
WORKDIR /project
ENV DBT_PROFILES_DIR=/profiles \
    PYTHONUNBUFFERED=1
