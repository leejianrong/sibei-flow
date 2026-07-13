# Hero pipeline — the concrete Airflow target for the wow demo

> **Status:** BUILT (Seam-3 harness live). The runnable environment described
> below now exists behind `docker compose --profile hero` — a real Airflow +
> dbt + offline git remote. `make hero` runs the `analytics_daily` DAG GREEN;
> `make hero-break` renames the column and the DAG fails for real, driving
> sibei-flow's loop to a verified `pr_proposed`. See README §"Run the live hero
> pipeline" and `worker/tests/test_hero.py` (the Seam-3 acceptance test).
>
> The one piece still ahead is **V4's PR opener** — turning that `pr_proposed`
> into an actual Pull Request against the git remote. Everything up to the
> verified diff + evidence is live today.
>
> This doc pins down the *one concrete dbt-in-Airflow pipeline* the v1
> acceptance demo runs against, and records the decision to **actually build and
> run it** as the executable end-to-end (PRD Seam 3) harness — not just fixtures.
>
> **Ground truth it serves:** `PRD.md` §"Acceptance Scenario" (schema drift →
> auto-PR) and `SLICES.md` Seam-3. This is the *instantiation* of that scenario,
> not a new requirement.

## What we're building (one-paragraph reminder)

**sibei-flow** is a self-healing wedge for data pipelines. When a
**dbt-running-inside-Airflow** run breaks from **schema drift** (an upstream
column renamed/removed/retyped) or a **code/SQL error**, sibei-flow —
detected via a webhook, with **no standing access** to your infra — drafts a
fix with a provider-agnostic agent, **verifies it in an ephemeral sandbox**
(compile always; sample run when a read-only dev connection is configured), and
opens a **reviewable Pull Request** carrying the diff, a plain-English
explanation, the reasoning transcript, verification evidence, and a
confidence/risk label — **within ~90s**. The **only** write action anywhere is
opening a PR on a branch: no prod-write creds, no writes to `main`. The team's
one read-only place to see what happened is the web dashboard; approval is
merging the PR; rollback is `git revert`. (See `CONTEXT.md`, `FRAME.md`.)

## The concrete hero pipeline (`acme/analytics`)

A minimal but realistic nightly analytics pipeline — the exact thing the demo
breaks and heals.

**Warehouse (Postgres):** a `raw` schema with a `raw_customers` source table and
a `raw_orders` table.

**dbt project** (seeded today in `fixtures/dbt_project/`):
- `models/marts/orders.sql` — joins orders to customers, **references
  `customer_id`** from `raw_customers`.
- `models/marts/schema.yml` — declares the `orders` model + the `raw` source.
- (later) a `revenue.sql` model to exercise the code/SQL-error class too.

**Airflow DAG** (`dags/analytics_daily.py`, to be built):
```
analytics_daily  (schedule: @daily)
  └─ dbt_seed         BashOperator: dbt seed
  └─ dbt_run_staging  BashOperator: dbt run --select staging
  └─ dbt_build_orders BashOperator: dbt build --select orders   ← the failing task
        on_failure_callback = sbflow_on_failure   (fixtures/airflow_on_failure_callback.py)
```
Enrollment is the single `on_failure_callback` line (R1.2 / U1) — already drafted
in `fixtures/airflow_on_failure_callback.py`, which POSTs the `Failure` payload
to the brain's `/webhook`.

### The break (hero story)

1. Upstream, `raw_customers.customer_id` is **renamed to `cust_id`** (the
   canonical rename from `PRD.md` §Acceptance).
2. The nightly `dbt_build_orders` task fails — `orders.sql` still selects
   `customer_id`:
   > `Database Error in model orders … column "customer_id" does not exist`
3. Airflow's `on_failure_callback` fires → POSTs the structured `Failure` to
   sibei-flow. (This exact payload is the seed fixture
   `fixtures/schema_drift_failure.json`.)

### Expected sibei-flow behavior (the acceptance assertion)

Within ~90s of the failure (per-slice, this thickens V1→V4):
1. webhook received → repair job opened; **[V1 ✓]**
2. failing model read read-only; drift detected by diffing the referenced
   column against the current upstream schema (`customer_id` gone, `cust_id`
   present → candidate 1:1 rename); **[V2]**
3. fix drafted in the sandbox: `customer_id` → `cust_id`, minimal diff; **[V2]**
4. verified: **compiled ✓ · 10k-row sample ✓ · output schema unchanged ✓**; **[V3]**
5. **PR opened** with diff + explanation + transcript + evidence + confidence/risk; **[V4]**
6. run surfaced in the read-only dashboard. **[V1 ✓]**

Merge → next run green. Rollback → `git revert`.

## Decision: build this pipeline for real, and test against it

We will **implement this pipeline as a runnable environment** and use it as the
Seam-3 executable acceptance test — not leave it as static fixtures. Concretely:

- A `hero/` (or `examples/analytics/`) tree adding to the current fixtures:
  - a real **Airflow** service (compose profile) running `analytics_daily`;
  - a **warehouse Postgres** (separate from the brain's state DB) with `raw`
    seed data;
  - the dbt project wired to that warehouse via a `sample`/dev profile;
  - a **git repo** (local bare remote is fine) holding the dbt project so the
    worker can read at the failing ref and the brain can open a PR against it;
  - a scripted **"rename the column"** step that triggers the real failure.
- An **executable acceptance test** (`Seam 3`) that: applies the rename, lets
  the nightly task fail, and asserts a PR appears with correct minimal diff +
  accurate evidence, no prod-write credential used, within the latency target.

### Where it slots into the slices

| Slice | What the hero pipeline proves |
|---|---|
| V1 (done) | failure payload → classified run in the dashboard (fixture payload stands in for the live DAG). |
| V2 | real read + drift diff + drafted `customer_id → cust_id`. |
| V3 | real ephemeral-sandbox verification + evidence against the sample warehouse. |
| **V4** | **the full live loop**: rename column → DAG fails → PR appears. This is the Show-HN demo and the first slice where the *whole* hero pipeline runs end to end. |
| V5 | hardened: crash/restart mid-run, duplicate webhook safety, `sbflow run --`, `sbflow init`, latency tuning. |

**Action item (DONE):** `hero/` stood up as the Seam-3 harness — see below.

## How it was built (the harness that now exists)

Behind `docker compose --profile hero` (core stack stays lean without it):

- **`airflow`** — a single `apache/airflow:2.10.4-python3.12` container running
  `airflow standalone` with `LocalExecutor` (metadata in a dedicated `airflow-db`
  Postgres). dbt lives in an **isolated venv** (`/opt/dbt-venv`, `dbt-core==1.9.4`
  + `dbt-postgres==1.9.0`, matching the verification sandbox) so Airflow's and
  dbt's pins never collide. It mounts `dags/`, the read-only dbt project, the
  hero dbt `profiles.yml` (the pipeline's own `analytics_app` role), and the
  `fixtures/` enrollment callback.
- **`dags/analytics_daily.py`** — `dbt_seed → dbt_run_staging → dbt_build_orders`
  BashOperators; `on_failure_callback = sbflow_on_failure` (the exact fixture
  snippet). The failing task carries `SBFLOW_NODE_UID=model.analytics.orders` so
  the worker resolves the failing source file.
- **`git-remote`** — a fully-offline bare git repo (git daemon over `git://`)
  seeded from the dbt project: the worker's read-at-ref source and V4's push
  target. (The worker still reads via the compatible `LocalSourceProvider`
  bind-mount today; the git remote is wired for V4.)
- **healthy-then-break, without disturbing the test seed:** `db/warehouse/
  init.sql` is untouched (it still seeds the POST-rename state the worker tests
  rely on). The hero flow drives the warehouse state *live* instead:
  `make hero` runs `db/warehouse/hero_seed.sql` (healthy, `customer_id` present)
  before a GREEN run; `make hero-break` runs `db/warehouse/hero_break.sql`
  (the `customer_id → cust_id` rename). The staging model (`stg_customers`,
  `select *`) survives the rename so the failure surfaces precisely at
  `dbt_build_orders`.

### Choices, resolved

- **Airflow flavor:** single-container `apache/airflow` standalone (LocalExecutor)
  — the lightest thing that runs a `BashOperator` dbt task on a laptop. No
  Astro/Celery.
- **Warehouse:** reuse the fixture Postgres `warehouse` (matches the fixture
  adapter); Snowflake/BigQuery stay pattern-only.
- **Git host:** a local offline bare git remote for the demo. Real GitHub
  (scoped token/App) is a V4 PR-opener config switch behind the same remote.

### Slice status

| Slice | Status against the live harness |
|---|---|
| V1–V3 | proven live: real read + drift diff + drafted `customer_id → cust_id`, verified in the ephemeral sandbox with evidence. |
| **V4** | **remaining:** turn the verified `pr_proposed` into a real PR against `git-remote`. The rest of the loop (DAG fails → verified diff appears) runs end to end today. |
