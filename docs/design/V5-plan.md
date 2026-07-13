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
1. **[done]** Idempotency key + unique index + `ON CONFLICT` enqueue; dedup test.
   — `idem_key` promoted to a UNIQUE partial index (migration `0003_dedup.sql`);
   enqueue is `INSERT … ON CONFLICT (idem_key) DO NOTHING RETURNING id`; a
   re-delivery returns the existing job flagged `deduplicated`. (seam2 test +
   live-verified.)
2. **[done]** Brain-restart reconcile + lease-expiry re-claim + orphan-container
   cleanup. — `reconcile_orphaned_jobs` requeues expired-lease `claimed`/
   `verifying` jobs at brain startup; the worker claim query re-claims
   expired-lease jobs; ephemeral sandbox containers are labelled `sbflow.sandbox`
   and swept on worker startup. (brain reconcile test + worker re-claim tests.)
3. `needs_prod_action` rule + recommendation rendering (no prod write). — *not in
   this slice's durability half.*
4. Airflow snippet + dbt hook + `sbflow run -- <cmd>` wrapper. — *onboarding half.*
5. `sbflow init` onboarding flow + config file. — *onboarding half.*
6. **[done, safe subset]** `LISTEN/NOTIFY` + warm worker pool + warm sandbox;
   re-measure p50 ≤ ~90s. — Brain `NOTIFY sbflow_jobs` on in-scope enqueue; the
   worker `LISTEN`s and wakes immediately (poll retained as the durable
   fallback). The sandbox image is pre-baked at startup (`_prewarm_sandbox`).
   **Measured hero p50 ≈ 10.6s** enqueue→`pr_proposed`, **≈ 12.1s** to a recorded
   PR (n=7; ≪ 90s). The heal time is dominated by the agent loop + `dbt compile`
   (~10s), not queue latency, so `LISTEN/NOTIFY` (removing the ≤2s poll wait) is
   the meaningful lever. **Deferred:** a multi-process *warm worker pool* and a
   *long-lived warm sandbox container* — both are throughput/cold-start
   optimizations unnecessary to hit the ~90s bar and the latter risks the
   ephemeral `--rm` isolation invariant. Left for a future pass if throughput
   (not latency) becomes the constraint.
7. Expand classifier patterns; keep **unknown → drop** default. — *onboarding half.*

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
