---
shaping: true
---

# V5 — Hardening & onboarding

> Slice V5 of `SLICES.md`. Makes the system durable under crashes and
> re-deliveries, adds the cron path + first-run onboarding, the
> `needs_prod_action` recommendation, and the latency tuning that guarantees the
> ~90s bar. After V5 the PRD's v1 scope is fully met.

## Goal & demo

**Goal:** production-trust properties (durability, dedup safety, honest
prod-action recommendations) and a one-command onboarding, plus the latency
mechanisms behind the ~90s demo.

**Demo:** kill the brain mid-run → the run resumes after restart; re-deliver the
same failure webhook → it collapses to one job (or at worst a second harmless
PR); wrap a cron step with `sbflow run -- <cmd>` and see its failure captured;
`sbflow init` walks a new user through tokens/keys in under a minute; a drift on
an `incremental` model surfaces as a **recommendation**, not a silent prod
assumption.

## Affordances (from SLICES.md V5)
N3 dedupe · crash recovery · N13 `needs_prod_action` · U1 full (Airflow snippet +
cron wrapper) · U2 `sbflow init` · latency tuning · N2 classifier thickened.

## Requirements exercised
R1.2 (one line), R1.4 (cron wrapper), R1.5 (minimal onboarding secrets),
R3.4 (`needs_prod_action`), R5.6 (~90s guaranteed), R7.1 (survive restart),
R7.2 (dup-safe), and broader R2.3/R2.4 coverage.

## Components & files
- **Dedup** — unique index on `idem_key = hash(repo, run_id, task_id, node_uid)`
  + `INSERT … ON CONFLICT DO NOTHING` in `N3` (B-S7).
- **Crash recovery** — brain-restart reconcile (find non-terminal jobs, requeue)
  + worker lease-expiry re-claim; orphaned sandbox container cleanup on worker
  restart.
- **`needs_prod_action`** — rule in `N13`: `materialized = incremental` +
  non-rename drift (removal/retype) → `outcome = needs_prod_action` with a
  recommendation string; brain surfaces it (dashboard + recommendation-only PR),
  never acts on prod (B-S3).
- **Detection ergonomics** — shipped Airflow `on_failure_callback` snippet + dbt
  hook (U1); `sbflow run -- <cmd>` cron wrapper posting the same payload.
- **Onboarding** — `sbflow init`: prompts for read-only git token/App + LLM key
  + optional read-only sample connection; writes local config (U2).
- **Latency tuning** — Postgres `LISTEN/NOTIFY` dispatch; a small warm worker
  pool; pre-baked + warm sandbox container (B-S6).
- **Classifier** — expand the adapter-aware pattern table (Postgres/Snowflake/
  BigQuery) for schema-drift and code/SQL coverage.

## Tasks
1. Idempotency key + unique index + `ON CONFLICT` enqueue; dedup test.
2. Brain-restart reconcile + lease-expiry re-claim + orphan-container cleanup.
3. `needs_prod_action` rule + recommendation rendering (no prod write).
4. Airflow snippet + dbt hook + `sbflow run -- <cmd>` wrapper. **[done]**
5. `sbflow init` onboarding flow + config file. **[done]**
6. `LISTEN/NOTIFY` + warm worker pool + warm sandbox; re-measure p50 ≤ ~90s.
7. Expand classifier patterns; keep **unknown → drop** default.

### Delivered — onboarding & detection ergonomics (tasks 4 & 5)

A new pure-stdlib **`sbflow` CLI** ships with the worker (console-script
`sbflow` → `sbflow_worker.cli:main`; no DB/LLM/Docker imports):

- **`sbflow run -- <cmd>`** — the cron/script fallback detector. Streams the
  command's output, passes its exit code through, and POSTs the frozen
  `Failure` (`source: "cli"`) **only** on non-zero exit. For dbt it lifts the
  failed node's `unique_id`/`message` out of `target/run_results.json` into
  `node_uid`/`error_text` and records it as `run_results_ref`; otherwise it
  falls back to the tail of the command's output.
- **`sbflow init`** — the one-minute onboarding flow. Prompts for
  repo/webhook/adapter and the OPTIONAL secrets (read-only + PR-scoped git
  token, optional LLM key — blank keeps keyless `replay`, optional read-only
  dev/sample warehouse DSN), then writes a **TOML** config (default
  `~/.config/sbflow/config.toml`, `0600`). Config resolution:
  `--config` > `$SBFLOW_CONFIG` > `./sbflow.toml` > `~/.config/sbflow/config.toml`;
  `SBFLOW_REPO`/`SBFLOW_WEBHOOK_URL`/`SBFLOW_ADAPTER` override per call.
- **Shipped snippets (`snippets/`)** — promoted, documented Airflow
  `on_failure_callback`; a dbt `on-run-end` macro that surfaces failed nodes
  (POST happens on the wrapper, since dbt hooks can't make HTTP calls); and a
  README with the one-line enrollment for each path.

**Trust posture (R6.1):** `sbflow init` only ever requests read-only /
PR-scoped credentials — never a prod-write credential; tier-2 targets a
dev/sample schema only. A unit test asserts no prompt mentions prod/write/admin.

Tests (fast/no-infra lane): `worker/tests/test_cli_run.py`,
`worker/tests/test_cli_init.py`.

## Tests (PRD Seam 2 completion + Seam 1 edge)
- A job survives a simulated brain restart mid-run and resumes (R7.1, story 26).
- A re-delivered payload collapses to one job or at worst a second PR — never
  corrupted state (R7.2, story 27).
- Out-of-scope classes still not dispatched after pattern expansion (story 15).
- `incremental` + non-rename drift → `needs_prod_action`, no PR that assumes
  prod (story 16).

## Acceptance
Crash-resume, dedup safety, cron capture, one-minute onboarding, honest
prod-action recommendation, and a guaranteed sub-90s p50 all demonstrated. v1
scope complete. **No ⚠️.**
