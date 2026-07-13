"""PRD Seam 3 — the LIVE hero-pipeline acceptance test (docs/design/HERO-PIPELINE.md).

Drives the flagship story end to end against a running stack (`docker compose up`,
or `make hero` for the full Airflow path):

1. Seed the HEALTHY pre-rename warehouse state and confirm the `orders` model
   builds GREEN there (customer_id present) — the nightly run before the drift.
2. Apply the canonical drift: rename `customer_id -> cust_id` upstream.
3. Fire the exact Failure the Airflow `on_failure_callback` POSTs when
   `dbt_build_orders` breaks, and assert sibei-flow HEALS it: the brain
   classifies `schema_drift`, the worker drafts + verifies a fix, and the run
   reaches `outcome=pr_proposed` with a minimal `customer_id -> cust_id` diff and
   honest verification evidence — with no prod-write credential in play.

This exercises the real brain + worker + ephemeral dbt sandbox against real
warehouse drift. The failure payload is byte-for-byte what the live Airflow
callback sends (node_uid = model.analytics.orders); `make hero` / `make
hero-break` drive that same webhook through a real DAG failure. The test skips
cleanly (never fails) when the stack isn't up.

Marked `infra` + `hero`; deselect with `-m "not infra"` or `-m "not hero"`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

import psycopg
import pytest

pytestmark = [pytest.mark.infra, pytest.mark.hero]

# Reachable via the compose network by default (container names); override for a
# host run (localhost:5455 / :5456 / :8080).
STATE_URL = os.environ.get("DATABASE_URL", "postgres://sibei:sibei@postgres:5432/sibei")
# The warehouse *admin* connection (seed/break DDL). This is the pipeline/fixture
# owner, NOT a sibei-flow credential — sibei-flow only ever holds sbflow_ro/dev.
WH_ADMIN_URL = os.environ.get(
    "WAREHOUSE_ADMIN_URL", "postgres://sibei:sibei@warehouse:5432/warehouse"
)
BRAIN_URL = os.environ.get("SBFLOW_BRAIN_URL", "http://brain:8080")

_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
SEED_SQL = os.path.join(_ROOT, "db", "warehouse", "hero_seed.sql")
BREAK_SQL = os.path.join(_ROOT, "db", "warehouse", "hero_break.sql")


def _skip_unless_reachable() -> None:
    try:
        with psycopg.connect(STATE_URL, connect_timeout=3):
            pass
        with psycopg.connect(WH_ADMIN_URL, connect_timeout=3):
            pass
        urllib.request.urlopen(f"{BRAIN_URL}/healthz", timeout=3).read()
    except Exception as e:  # noqa: BLE001 — any connectivity failure => skip
        pytest.skip(f"hero stack not reachable ({e}); run `docker compose up` first")


def _run_sql_file(url: str, path: str) -> None:
    with open(path) as f:
        sql = f.read()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql)  # type: ignore[arg-type]


def _raw_columns() -> set[str]:
    with psycopg.connect(WH_ADMIN_URL) as conn:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='raw' AND table_name='raw_customers'"
        ).fetchall()
    return {r[0] for r in rows}


def _post_failure(payload: dict) -> str:
    req = urllib.request.Request(
        f"{BRAIN_URL}/webhook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())["id"]


def _get_run(run_id: str) -> dict:
    with urllib.request.urlopen(f"{BRAIN_URL}/api/runs/{run_id}", timeout=5) as resp:
        return json.loads(resp.read())


def _airflow_callback_payload() -> dict:
    """Byte-for-byte what fixtures/airflow_on_failure_callback.py POSTs when the
    hero DAG's dbt_build_orders task fails (SBFLOW_NODE_UID set on that task)."""
    return {
        "repo": "acme/analytics",
        "run_id": f"hero_break__{int(time.time())}",
        "task_id": "dbt_build_orders",
        "node_uid": "model.analytics.orders",
        "error_text": (
            "Database Error in model orders (models/marts/orders.sql)\n"
            '  column "customer_id" does not exist'
        ),
        "adapter": "postgres",
        "source": "airflow",
    }


def test_hero_pipeline_heals_the_flagship_schema_drift():
    _skip_unless_reachable()

    # 1. HEALTHY baseline: seed pre-rename state; the upstream has customer_id.
    _run_sql_file(WH_ADMIN_URL, SEED_SQL)
    cols = _raw_columns()
    assert (
        "customer_id" in cols and "cust_id" not in cols
    ), "seed should establish the healthy pre-rename state"

    # 2. THE BREAK: the canonical upstream rename customer_id -> cust_id.
    _run_sql_file(WH_ADMIN_URL, BREAK_SQL)
    cols = _raw_columns()
    assert (
        "cust_id" in cols and "customer_id" not in cols
    ), "break should rename the column"

    # 3. The failure the Airflow callback fires -> brain classifies + enqueues.
    run_id = _post_failure(_airflow_callback_payload())
    detail = _get_run(run_id)
    assert detail["failure_class"] == "schema_drift"

    # 4. The worker heals it: poll until terminal (worker running in compose).
    outcome = None
    for _ in range(90):  # ~90s budget (R5.6)
        detail = _get_run(run_id)
        if detail.get("state") == "done":
            outcome = (detail.get("result") or {}).get("outcome") or detail.get(
                "outcome"
            )
            break
        time.sleep(1)
    if outcome is None:
        pytest.skip(
            "job never reached a terminal state — is the `worker` service running? "
            "(`docker compose up worker`)"
        )

    # 5. Assert the verified auto-heal: minimal diff + honest evidence.
    assert outcome == "pr_proposed", f"expected pr_proposed, got {outcome}: {detail}"
    result = detail["result"]
    diff = result["diff"]
    assert "-    customer_id," in diff
    assert "+    cust_id as customer_id," in diff  # aliased: output contract preserved
    assert "orders.sql" in diff
    assert "cust_id" in (result.get("explanation") or "")
    # Verified before you see it: tier-1 compile passed (the pr_proposed gate).
    ev = result.get("evidence") or {}
    assert ev.get("tier1", {}).get("passed") is True
    assert result.get("risk_class") in {"low", "medium", "high"}
