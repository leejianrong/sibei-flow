# sibei-flow — V1 + V2 + V3

> **V1** (`docs/design/V1-plan.md`) — the walking skeleton: a failure payload
> travels the durable spine (webhook → classify → enqueue → claim → record →
> read-only dashboard).
> **V2** (`docs/design/V2-plan.md`) — drift diagnosis & drafted fix: the worker
> runs a bounded agent loop (read source → confirm drift → draft a minimal edit).
> **V3** (`docs/design/V3-plan.md`) — **verified before you see it**: every
> drafted fix is compiled (and sample-run when a dev connection is configured) in
> an ephemeral Docker sandbox, so the run carries **honest evidence**
> (compiled ✓ · sample ✓ / *not configured* · output schema unchanged ✓) plus a
> **confidence/risk** label — and a fix that can't even compile is **suppressed to
> `no_fix`**, never proposed. No PR yet (V4).

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
                             read_file (RO source) · get_schema (RO warehouse) · edit_file (+diff guard)
                             · run_sandbox (N10) ──▶ ephemeral dbt container (docker run --rm)
                                  tier-1  dbt compile         (always)
                                  tier-2  dbt build --sample  (if a dev conn is set)
                             → evidence (N11) → confidence/risk (N12) → compile gate
                             → RepairResult {outcome: pr_proposed, diff, explanation,
                                             transcript, evidence, confidence, risk_class, factors}
                             (or no_fix — including a non-compiling draft, suppressed by the gate)

GET /  ·  GET /api/runs  ·  GET /api/runs/:id     read-only dashboard (no write actions)
```

- **brain/** — Rust: webhook receiver, thin classifier, enqueue, dashboard read
  API + embedded read-only UI. U5 now renders the **verification evidence**
  (compiled / sample / output-schema) and a **confidence + risk** label; the
  blanket *unverified* badge is replaced by a state that reflects real evidence
  (*verified* when tier-1 passed, *suppressed* when the gate blocked a draft).
  Owns the Postgres schema (migrations on boot).
- **worker/** — Python 3.12 (uv, psycopg3): claim loop + **agent loop**
  (`sbflow_worker/agent/`) behind an **`LlmProvider`** (`sbflow_worker/llm/`),
  plus the **verification sandbox** (`sbflow_worker/sandbox/`):
  - `replay` — bundled record/replay session (keyless, deterministic; default),
  - `claude` — Anthropic SDK (`claude-opus-4-8`, BYO-key, ADR-0007),
  - `openai` — OpenAI-compatible / local endpoint.
- **sandbox/** — the pre-baked verification image (`python` + `dbt-core` +
  `dbt-postgres` + `git`). The worker launches ephemeral `--rm` containers from
  it via **Docker-out-of-Docker** (mounted `/var/run/docker.sock`); the candidate
  project is materialized under a host-visible `SANDBOX_WORK_DIR` bind-mounted at
  the same path so `docker run -v <hostpath>:/project` resolves on the daemon.
  Containers are network-scoped, memory-capped, and hold **no** prod-write creds
  (tier-2 targets a read-only *dev/sample*, never prod).
- **docker-compose.yml** — `postgres` (state) + `warehouse` (fixture upstream +
  a writable `sbflow_dev`→`sbflow_sample` dev target for tier-2) + `brain` +
  `worker`.

## Frozen contracts (stable into phase B)

- **Failure (webhook in):** `{repo, run_id, task_id, node_uid, error_text,
  adapter, run_results_ref?, source: airflow|dbt|cli}`
- **RepairResult (worker out):** `{outcome, diff?, explanation?, transcript?,
  evidence?, confidence?, risk_class?}` (+ `factors[]` in V3). V3 populates
  `evidence = {tier1{ran,passed,log}, tier2{ran,passed|null,log},
  output_schema{changed,detail}}` and sets `confidence` (0–1) + `risk_class ∈
  {low,medium,high}`. `pr_proposed` is emitted **only** when `tier1.passed`;
  otherwise `no_fix`.
- **Agent tool contract (worker-internal, stable to phase B):**
  `read_file(path, ref)`, `get_schema(source)`, `edit_file(path, old, new)`,
  `run_sandbox(select?)`.
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
# In-scope schema drift → queued → agent drafts a minimal diff → SANDBOX VERIFY
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
`cust_id as customer_id` diff (aliased so the model's **output contract is
preserved**), a plain-English explanation, the reasoning transcript, and
**verification evidence** — *compiled ✓ · ran on sample ✓ · output schema
unchanged ✓* — with a **confidence** score and **risk: low**. The timeout run is
`outcome=out_of_scope` and was **never** queued. (Comment out
`SAMPLE_WAREHOUSE_URL` in compose to see the honest *"sample run: not
configured"* disclosure; a deliberately non-compiling draft is suppressed to
`no_fix` and never proposed.)

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

**Worker (Python) — Seam 2 claim mechanics + Seam 1 agent loop + REAL sandbox:**

```bash
cd worker && uv sync --extra dev && uv run pytest
# test_claim.py     — SKIP LOCKED / lease / dropped-not-claimed
# test_diffguard.py — targeted edits + oversize/scope/whitespace guard (no DB)
# test_agent_loop.py— rename drift → minimal diff; guard re-draft; N-cap → no_fix
# test_score.py     — confidence/risk rubric derived from signals (no DB/Docker)
# test_sandbox.py   — Seam 1 (V3): non-compiling draft → no_fix (never pr_proposed);
#                     passing fix's evidence reflects tiers that ran; tier-2 absence
#                     disclosed. Needs the Docker socket + the warehouse.
```

The V3 sandbox tests drive a **real** ephemeral dbt container, so the worker
test process needs the Docker socket and the sandbox work dir. No Rust/uv
locally? Run each suite in a throwaway container joined to the compose network —
the worker one needs the socket + work dir mounted:

```bash
# brain (Rust)
docker run --rm --network sibei-flow_default -v "$PWD/brain":/app -w /app \
  -v sibei_brain_target:/app/target -v sibei_cargo_registry:/usr/local/cargo/registry \
  -e DATABASE_URL=postgres://sibei:sibei@postgres:5432/sibei rust:1-slim-bookworm \
  bash -c 'apt-get update -qq && apt-get install -y -qq gcc libc6-dev pkg-config >/dev/null; cargo test'

# worker (Python) — note the mounted docker.sock + sandbox work dir
docker run --rm --network sibei-flow_default -v "$PWD":/work -w /work/worker \
  -v /var/run/docker.sock:/var/run/docker.sock -v /tmp/sbflow-sandbox:/tmp/sbflow-sandbox \
  -e DATABASE_URL=postgres://sibei:sibei@postgres:5432/sibei \
  -e WAREHOUSE_URL=postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse \
  -e SAMPLE_WAREHOUSE_URL=postgres://sbflow_dev:sbflow_dev@warehouse:5432/warehouse \
  -e SANDBOX_NETWORK=sibei-flow_default -e SANDBOX_WORK_DIR=/tmp/sbflow-sandbox \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim \
  bash -c 'apt-get update -qq && apt-get install -y -qq docker.io >/dev/null; uv sync --extra dev -q; uv run pytest'
```

## What V3 deliberately does NOT do (later slices)

- No git write / branch / PR (V4) — the verified fix is a diff + evidence on the
  dashboard, not yet a Pull Request.
- No dedupe uniqueness, crash-recovery reconcile, `LISTEN/NOTIFY`, `sbflow init`,
  `needs_prod_action` (V5).
- Only the **local Docker** executor backend (ADR-0008 seam only); no VM/K8s.
- The system holds **no** prod-write credentials; source + warehouse access is
  read-only and tier-2 builds only into a **dev/sample** schema (never prod); the
  web UI has **zero** write actions.
```
