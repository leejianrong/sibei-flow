# CI / CD (GitHub Actions — the authoritative gate)

Local hooks are advisory; **CI is the gate that protects `main`.** Mirror the
Makefile so local == CI (fewer "works on my machine" surprises).

## Jobs (run in parallel; all required on `main`)

1. **lint-worker** — `uv sync --extra dev` → `ruff check` + `ruff format --check`.
2. **lint-brain** — `cargo fmt --check` + `cargo clippy -- -D warnings`.
3. **test** — `make test` (brain + worker, incl. the real-Docker sandbox tests).
   GitHub-hosted `ubuntu-latest` runners ship Docker + Compose + a docker socket,
   so the containerized Makefile targets run as-is. Expect minutes (the sandbox
   image bakes `dbt`); cache aggressively (see below).
4. **e2e** — `make up-build` → run `./scripts/demo.sh` → assert the drift run is
   a verified `pr_proposed` (`grep "compiled   : PASS"`); `make down` on always().
5. **security** — dependency/vuln scan: `pip-audit` (worker), `cargo audit`
   (brain), and a container/image scan (Trivy). Start non-blocking (`|| true`)
   to gauge noise, then make blocking.

A concrete starter is in `references/templates/ci.yml`.

## Determinism in CI

CI uses the **replay** `LlmProvider` (default) — no `ANTHROPIC_API_KEY` in the
blocking jobs. A real-model **eval** job (if added) runs on a schedule or a
label, reads the key from repo **secrets**, and is **non-blocking**.

## Speed

- Cache cargo (`~/.cargo`, `target/`) and uv (`~/.cache/uv`); reuse Docker layer
  cache for the compose builds.
- Split lint (fast, fail-early) from the heavy test/e2e jobs so red lint fails in
  ~1 min instead of waiting on the sandbox build.

## Branch protection (one-time, human-only repo Setting)

On GitHub → **Settings → Branches → add rule for `main`:**
- Require PRs before merging; require the CI checks above to pass.
- Require branches up to date; require conversation resolution.
- (Optional) require a review; forbid force-push to `main`.

The agent **cannot** set this — flag it to the human every time CI is added.

## Pages

`.github/workflows/pages.yml` publishes `site/` on push to `main`. It only
deploys once **Settings → Pages → Source: GitHub Actions** is enabled (one-time).

## Release automation (when the project stabilizes)

Conventional commits → automated changelog + version bump via `release-please`
or `git-cliff`. Tag releases; attach the `docker compose` artifacts / image
digests. Not needed for a pre-launch prototype — add when there are real users.
