#!/bin/sh
# Initialize a bare repo from the mounted dbt project (/seed), then serve it over
# the git protocol. Idempotent: re-seeds only when the bare repo is absent.
set -eu

REPO=/srv/git/analytics.git

if [ ! -d "$REPO" ]; then
    echo "[git-remote] seeding bare repo at $REPO from /seed"
    mkdir -p /srv/git
    git init -q --bare "$REPO"

    WORK="$(mktemp -d)"
    # Copy the dbt project WITHOUT dbt build artifacts / any stray VCS metadata.
    cp -r /seed/. "$WORK"/
    cd "$WORK"
    rm -rf target logs dbt_packages .git
    git init -q
    git config user.email "hero@sibei-flow.local"
    git config user.name "hero seed"
    git add -A
    git commit -q -m "seed: acme/analytics dbt project"
    git branch -M main
    git push -q "file://$REPO" main
    echo "[git-remote] seeded main branch"
fi

echo "[git-remote] serving git://0.0.0.0:9418/analytics.git (push enabled)"
exec git daemon \
    --reuseaddr \
    --export-all \
    --enable=receive-pack \
    --base-path=/srv/git \
    --verbose \
    /srv/git
