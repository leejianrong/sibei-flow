# CLAUDE.md — agent brief for sibei-flow

A tight orientation for coding agents and newcomers. It is a brief, not a
duplicate of the docs. **Trust the code over these docs if they disagree**, and
see [`README.md`](README.md) and [`docs/design/`](docs/design/) for depth
(`SLICES.md` = build order; `V1-plan.md … V5-plan.md` = per-slice detail; `adr/`
= decisions).

## Build status (honest)

- **Shipped (full v1 scope):** V1 (walking skeleton: webhook → classify → enqueue
  → claim → record → read-only dashboard), V2 (bounded agent loop drafts a minimal
  fix), V3 (every draft is compiled in an ephemeral Docker sandbox; non-compiling
  drafts are suppressed to `no_fix`, never proposed), V4 (the auto-PR — a
  verified `pr_proposed` becomes a real Pull Request via a brain-side poller
  behind a pluggable git-host seam: `offline` default / `github`; ADR-0011),
  V5 (hardening & onboarding: dedupe via unique `idem_key` + `ON CONFLICT`, crash
  recovery — brain reconcile + worker lease re-claim + orphan-container sweep,
  `LISTEN/NOTIFY` fast dispatch, `sbflow init` + `sbflow run -- <cmd>` CLI +
  Airflow/dbt enrollment snippets, `needs_prod_action` for incremental/non-rename
  drift, and PG/Snowflake/BigQuery classifier expansion).
- **Deferred (intentional):** a multi-process warm worker pool and a long-lived
  warm sandbox container — measured hero p50 (~12s webhook→PR) is already far under
  the ~90s bar, and a persistent container would risk the ephemeral `--rm`
  isolation invariant.

## What it is

A control plane that auto-heals broken data pipelines. A failure webhook hits
the **brain** (Rust, axum+sqlx), which normalizes it to the `Failure` contract,
classifies it (`schema_drift | code_sql | out_of_scope:<reason>`), and enqueues
in-scope jobs into Postgres (`repair_jobs`, the durable source of truth). The
**worker** (Python 3.12, uv + psycopg3) claims a job and runs a bounded agent
loop (behind an `LlmProvider`) that reads source, confirms drift, and drafts a
minimal edit. The **sandbox** (a pre-baked dbt image, launched as ephemeral
`docker run --rm` containers) verifies the draft — tier-1 `dbt compile` always,
tier-2 `dbt build --sample` when a dev connection is set. The **warehouse** is a
fixture upstream (read-only role for reads; a writable dev/sample role for
tier-2). A read-only dashboard shows runs, diffs, evidence, and confidence/risk.

## Exact commands (`make`)

```bash
make up            # bring the whole stack up (build if needed)
make demo          # drive the end-to-end demo
make test          # both suites (brain + worker, incl. real-Docker sandbox tests)
make test-brain    # Rust seam-2 + unit tests (throwaway DB per test)
make test-worker   # worker claim + agent-loop + REAL sandbox tests (needs Docker)
make test-fast     # FAST no-infra lane: worker `-m "not infra"` (no DB/WH/Docker)
make lint          # ruff check (worker)
make typecheck     # mypy (worker) — ADVISORY (pre-existing errors; not yet blocking)
make down / clean  # stop the stack (clean also drops volumes)
make logs          # tail all services · logs-brain / logs-worker for one
```

`make help` lists everything. `make test`/`test-brain`/`test-worker` run in
throwaway containers on the compose network (no local Rust/uv needed);
`make test-fast` runs pytest directly against a local uv env for a quick gate.

**Ports & env** (see README "Project conventions"): state DB on host **`5455`**,
fixture warehouse on host **`5456`** (container ports stay `5432`).

```bash
DATABASE_URL=postgres://sibei:sibei@localhost:5455/sibei
WAREHOUSE_URL=postgres://sbflow_ro:sbflow_ro@localhost:5456/warehouse
SAMPLE_WAREHOUSE_URL=postgres://sbflow_dev:sbflow_dev@localhost:5456/warehouse  # tier-2 dev/sample
```

**LLM:** defaults to the keyless **`replay`** provider (bundled record/replay,
deterministic) — **no API key needed for tests or the demo**. For a real model
set `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=openai` +
`LLM_BASE_URL`.

## Test layering

- **Fast / no-infra:** `test_diffguard.py`, `test_score.py` — pure logic, no DB /
  warehouse / Docker. Run with `make test-fast` (`-m "not infra"`).
- **Infra:** `test_claim.py`, `test_agent_loop.py`, `test_sandbox.py` — marked
  `@pytest.mark.infra`; need Postgres / warehouse / the Docker socket. The
  `infra` marker is registered in `worker/pyproject.toml` under
  `--strict-markers` (an unregistered marker is an error).

## Conventions

- **Branch per slice; PR only; never push red.** Land work on a branch, open a
  PR — don't push straight to `main`.
- **Before pushing**, run the fast gate: `make test-fast`, `ruff` (worker), and
  `cargo fmt` (brain).
- **Before a PR**, run the full `make test` (both suites, real sandbox).
- Python via **uv**, targeting **3.12+**.

### Merging PRs — merge when CI is green

`main` is protected: every change lands via PR, and merging requires the three
required checks — **`brain`, `worker`, `security`** — to pass on an up-to-date
branch (0 approvals required, so you can self-merge).

- **When a PR's checks are all green, merge it** (squash). The standard command
  is merge-on-green: `gh pr merge <n> --squash --auto --delete-branch` — GitHub
  merges automatically the moment checks pass and the branch is up to date.
- This includes **Dependabot** PRs. Because protection is `strict` (branches must
  be up to date), arm the whole batch with `--auto`; Dependabot rebases the
  remaining PRs in cascade as `main` advances, and each merges once green.
- **Never merge a PR with a failing or pending check.** If a dependency bump
  breaks CI (typically a **major-version** bump — e.g. an `axum 0.7 → 0.8` that
  fails the `brain` job), leave it open for a human to review/adapt; do not force
  it through.
- Verify status first with `gh pr checks <n>` (or `gh pr list --json
  number,statusCheckRollup`). Green = merge; red/pending = wait or escalate.

## Invariants to protect (do not break)

- **Frozen contracts** — stable into phase B; do not change shape without an ADR:
  - `Failure` (webhook in): `{repo, run_id, task_id, node_uid, error_text,
    adapter, run_results_ref?, source}`.
  - `RepairResult` (worker out): `{outcome, diff?, explanation?, transcript?,
    evidence?, confidence?, risk_class?, factors?}`.
  - **Agent tool contract:** `read_file(path, ref)`, `get_schema(source)`,
    `edit_file(path, old, new)`, `run_sandbox(select?)`.
- **No fix reaches a PR without passing tier-1 compile** — `pr_proposed` is
  emitted *only* when `tier1.passed`; otherwise `no_fix` (the compile gate).
- **Out-of-scope failures are never dispatched** — they are recorded `done` /
  `out_of_scope`, never queued to the worker.
- **The system holds NO prod-write credentials** — source + warehouse access is
  read-only; tier-2 builds only into a dev/sample schema (never prod); the web UI
  has zero write actions.
