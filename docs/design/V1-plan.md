---
shaping: true
---

# V1 — Walking skeleton: failure in, run out

> Slice V1 of `SLICES.md` (Shape B). The vertical spine of the queue seam with
> no fix logic yet. Proves the durable path end to end and gives us the
> dashboard to observe every later slice.

## Goal & demo

**Goal:** a failure payload travels the whole spine — receive → classify →
enqueue → claim → record — and is visible in a read-only web dashboard.

**Demo:** `docker compose up`, then POST a sample dbt failure payload to the
webhook (or run the bundled fixture dbt project so it fails). A run appears in
the dashboard showing failure class and `outcome = no_fix`; an out-of-scope
payload (e.g. a timeout) is recorded as *dropped*, not dispatched.

## Affordances (from SLICES.md V1)
N1 webhook receiver · N2 classifier (thin) · N3 enqueue (thin) · N4 job queue ·
N5 claim loop · N13 write-back (`no_fix` stub) · N15 dashboard API · U4 history ·
U5 detail (thin) · packaging.

## Requirements exercised
R1.1 (docker compose), R2.1 (no standing access), R2.2 (structured payload),
R2.3/R2.4 (thin classify), R7.3 (Postgres SoT), R8.1/R8.2/R8.3 (read-only UI).

## Components & files
- **Brain (Rust)** — `brain/`: HTTP server (axum), Postgres access (sqlx),
  webhook receiver, classifier, enqueue, dashboard read API, static web UI.
- **Worker (Python)** — `worker/`: claim loop that reads a job, writes a
  `no_fix` result (agent stubbed).
- **Web UI** — minimal static SPA (or server-rendered) served by the brain.
- **Packaging** — `docker-compose.yml`: `brain`, `worker`, `postgres`.

## Data model (Postgres — owned by the brain)
```
repair_jobs(
  id            uuid pk,
  idem_key      text,              -- populated but not yet unique-indexed (V5)
  repo          text,
  run_id        text, task_id text, node_uid text,
  failure_class text,              -- schema_drift | code_sql | out_of_scope:<reason>
  payload       jsonb,             -- normalized Failure
  state         text,              -- received|classified|queued|claimed|verifying|done
  lease_expires_at timestamptz,    -- for V5 recovery; set on claim
  result        jsonb,             -- RepairResult (nullable until done)
  created_at timestamptz, updated_at timestamptz
)
```

## Contracts (frozen here; stable into phase B)
- **Failure (webhook in):** `{repo, run_id, task_id, node_uid, error_text,
  adapter, run_results_ref?, source: airflow|dbt|cli}`.
- **RepairResult (worker out):** `{outcome, diff?, explanation?, transcript?,
  evidence?, confidence?, risk_class?}` — V1 emits `{outcome: "no_fix"}` only.
- **Dashboard API:** `GET /api/runs`, `GET /api/runs/:id` (read-only).

## Tasks
1. Brain: `POST /webhook` → normalize to `Failure`.
2. Brain: thin classifier (schema_drift / code_sql / out_of_scope) — B-S1 rules,
   minimal patterns; **unknown → out_of_scope** (safe default).
3. Brain: enqueue in-scope jobs (`state = queued`); record dropped ones
   (`state = done`, `outcome = out_of_scope`) without dispatch.
4. Worker: claim loop (`SELECT … FOR UPDATE SKIP LOCKED` + lease), write
   `no_fix`, mark `done`.
5. Brain: dashboard read API + minimal read-only UI (history + detail).
6. `docker-compose.yml` + a bundled fixture dbt project that fails on demand.

## Tests (PRD Seam 2, first cut)
- Valid in-scope payload → one `queued` job → worker marks `done` with `no_fix`.
- Out-of-scope payload (timeout/OOM/test-fail) → recorded, **not** dispatched.
- Dashboard API returns the run; UI has no write endpoints.

## Acceptance
Failure POST → visible dashboard run with correct class + `no_fix`;
out-of-scope payloads are dropped; whole thing runs from `docker compose up`.
**No ⚠️.**
