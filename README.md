# sibei-flow — V1 + V2

> **V1** (`docs/design/V1-plan.md`) — the walking skeleton: a failure payload
> travels the durable spine (webhook → classify → enqueue → claim → record →
> read-only dashboard).
> **V2** (`docs/design/V2-plan.md`) — drift diagnosis & drafted fix: the worker
> runs a bounded agent loop (read source → confirm drift → draft a minimal edit)
> and the dashboard shows an **unverified** diff + explanation + transcript. No
> sandbox verification (V3) and no PR (V4) yet.

## Architecture

```
POST /webhook ─▶ brain (Rust, axum+sqlx)
                   ├─ normalize → Failure contract
                   ├─ classify  → schema_drift | code_sql | out_of_scope:<reason>
                   ├─ in-scope  → INSERT repair_jobs (state=queued)
                   └─ dropped   → INSERT repair_jobs (state=done, outcome=out_of_scope)  [not dispatched]
                        │
                        ▼  Postgres  (repair_jobs = durable source of truth)
                        │
     worker (Python)    ├─ SELECT … FOR UPDATE SKIP LOCKED + lease  (claim)
                        └─ agent loop (bounded ≤N, behind LlmProvider):
                             read_file (RO source)  ·  get_schema (RO warehouse)  ·  edit_file (+diff guard)
                             → RepairResult {outcome: pr_proposed, diff, explanation, transcript, evidence: null}
                             (or no_fix on give-up)

GET /  ·  GET /api/runs  ·  GET /api/runs/:id     read-only dashboard (no write actions)
```

- **brain/** — Rust: webhook receiver, thin classifier, enqueue, dashboard read
  API + embedded read-only UI (U5 now renders diff/explanation/transcript with
  an *unverified* badge). Owns the Postgres schema (migrations on boot).
- **worker/** — Python 3.12 (uv, psycopg3): claim loop + **agent loop**
  (`sbflow_worker/agent/`) behind an **`LlmProvider`** (`sbflow_worker/llm/`):
  - `replay` — bundled record/replay session (keyless, deterministic; default),
  - `claude` — Anthropic SDK (`claude-opus-4-8`, BYO-key, ADR-0007),
  - `openai` — OpenAI-compatible / local endpoint.
- **docker-compose.yml** — `postgres` (state) + `warehouse` (fixture upstream
  schema, read-only) + `brain` + `worker`.

## Frozen contracts (stable into phase B)

- **Failure (webhook in):** `{repo, run_id, task_id, node_uid, error_text,
  adapter, run_results_ref?, source: airflow|dbt|cli}`
- **RepairResult (worker out):** `{outcome, diff?, explanation?, transcript?,
  evidence?, confidence?, risk_class?}` — V2 emits `pr_proposed` with
  `diff`/`explanation`/`transcript` and **`evidence: null`** (unverified), or
  `no_fix`.
- **Agent tool contract (worker-internal, stable to phase B):**
  `read_file(path, ref)`, `get_schema(source)`, `edit_file(path, old, new)`.
- **Dashboard API:** `GET /api/runs`, `GET /api/runs/:id` (read-only).

## Project conventions

- **Postgres host ports** for this project: state DB on **`5455`**, fixture
  warehouse on **`5456`** (container ports stay `5432`; `5432`/`5433` are used by
  other local projects). Tooling/tests use
  `DATABASE_URL=postgres://sibei:sibei@localhost:5455/sibei` and
  `WAREHOUSE_URL=postgres://sbflow_ro:sbflow_ro@localhost:5456/warehouse`.
- Python is managed with **uv**, targeting **Python 3.12+**.
- No LLM key needed for the demo/tests — the `replay` provider drives them. To
  run against a real model, set `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY`
  (or `LLM_PROVIDER=openai` + `LLM_BASE_URL` for a local model).

## Run the demo

```bash
# 1. Bring up the whole stack (first build compiles the Rust brain — a few min).
docker compose up --build          # add -d to background it

# 2. In another terminal, drive the end-to-end flow:
./scripts/demo.sh

# 3. Open the read-only dashboard and click the schema-drift run:
open http://localhost:8080/        # or just visit it in a browser
```

Or by hand:

```bash
# In-scope schema drift → queued → agent drafts a minimal diff (unverified)
curl -s -X POST http://localhost:8080/webhook \
  -H 'content-type: application/json' --data @fixtures/schema_drift_failure.json

# Out-of-scope timeout → recorded as dropped, never dispatched
curl -s -X POST http://localhost:8080/webhook \
  -H 'content-type: application/json' --data @fixtures/timeout_failure.json

curl -s http://localhost:8080/api/runs | python3 -m json.tool
# then open a run's detail, e.g.:
curl -s http://localhost:8080/api/runs/<id> | python3 -m json.tool
```

**Expected:** the schema-drift run ends `outcome=pr_proposed` with a minimal
`customer_id → cust_id` diff + plain-English explanation + reasoning transcript,
marked **unverified** (`evidence: null`); the timeout run is
`outcome=out_of_scope` and was **never** queued.

## Tests

The suites need a reachable Postgres (state) and, for the rename agent test, the
fixture warehouse. Simplest source is compose:

```bash
docker compose up -d postgres warehouse
export DATABASE_URL=postgres://sibei:sibei@localhost:5455/sibei
export WAREHOUSE_URL=postgres://sbflow_ro:sbflow_ro@localhost:5456/warehouse
```

> ⚠️ `worker/tests/test_claim.py` `TRUNCATE`s `repair_jobs`, so running the
> worker suite against the compose state DB clears any demo runs. Run tests
> before the demo, or just re-run `./scripts/demo.sh` afterwards to repopulate.

**Brain (Rust) — Seam 2 (webhook→job→dispatch) + read-only API:**

```bash
cd brain && cargo test        # #[sqlx::test] provisions a throwaway DB per test
```

**Worker (Python) — Seam 2 claim mechanics + Seam 1 (first cut) agent loop:**

```bash
cd worker && uv sync --extra dev && uv run pytest
# test_claim.py     — SKIP LOCKED / lease / dropped-not-claimed
# test_diffguard.py — targeted edits + oversize/scope/whitespace guard (no DB)
# test_agent_loop.py— rename drift → minimal unverified diff; guard re-draft; N-cap → no_fix
```

No Rust/uv locally? Run each suite in a throwaway container joined to the
compose network (see the exact commands in the project notes) — the demo itself
needs only Docker.

## What V2 deliberately does NOT do (later slices)

- No sandbox / `dbt compile` / verification / evidence / confidence (V3) —
  drafted diffs are labeled **unverified**.
- No git write / branch / PR (V4) — the fix is a diff on the dashboard, not a PR.
- No dedupe uniqueness, crash-recovery reconcile, `LISTEN/NOTIFY`, `sbflow init`,
  `needs_prod_action` (V5).
- The system holds **no** prod-write credentials; source + warehouse access is
  read-only; the web UI has **zero** write actions.
```
