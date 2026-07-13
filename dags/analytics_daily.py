"""The hero pipeline DAG — `analytics_daily` (Seam-3 harness).

A minimal but realistic nightly analytics pipeline (docs/design/HERO-PIPELINE.md):

    analytics_daily  (@daily)
      dbt_seed          → dbt_run_staging → dbt_build_orders
      (no-op seeds)       stg_customers      orders  ← the drift-failing task

`raw.raw_customers` is the upstream table (seeded/renamed out-of-band by
`make hero` / `make hero-break`, modelling ingestion the pipeline doesn't own).
When `customer_id` drifts to `cust_id`, `dbt_build_orders` fails for real
(`column "customer_id" does not exist`); its `on_failure_callback` fires and
POSTs the structured Failure to the brain's /webhook — enrolling this pipeline in
sibei-flow with a single line (the exact `sbflow_on_failure` fixture callback).

dbt runs inside the Airflow container from an isolated venv (/opt/dbt-venv), so
Airflow's and dbt's dependency pins never collide.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator

# Reuse the EXACT enrollment snippet teams copy-paste (fixtures/…). Mounted at
# /opt/airflow/fixtures in the hero Airflow container.
sys.path.insert(0, "/opt/airflow/fixtures")
from airflow_on_failure_callback import sbflow_on_failure  # noqa: E402

DBT = os.environ.get("DBT_BIN", "/opt/dbt-venv/bin/dbt")
PROJECT_DIR = os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/dbt_project")
PROFILES_DIR = os.environ.get("DBT_PROFILES_DIR", "/opt/airflow/dbt_profiles")

# dbt writes target/ + logs/ under a writable path (the project mount is
# read-only, shared with the worker's read-only source checkout). We cd into the
# project and pass dirs via env so we never depend on dbt's per-subcommand flag
# ordering (--project-dir / --profiles-dir are subcommand options in dbt 1.9).
_ARTIFACTS = "/tmp/dbt"


def _dbt(subcmd: str) -> str:
    return (
        f"mkdir -p {_ARTIFACTS} && cd {PROJECT_DIR} && "
        f"export DBT_PROFILES_DIR={PROFILES_DIR} "
        f"DBT_TARGET_PATH={_ARTIFACTS}/target DBT_LOG_PATH={_ARTIFACTS}/logs && "
        f"{DBT} --no-use-colors {subcmd}"
    )


with DAG(
    dag_id="analytics_daily",
    description="Nightly analytics: dbt seed → staging → orders (hero pipeline).",
    schedule="@daily",
    start_date=datetime(2026, 7, 1),
    catchup=False,
    # The failing task carries the enrollment callback + the dbt node uid so the
    # brain resolves the failing source (env var read by sbflow_on_failure).
    default_args={"on_failure_callback": sbflow_on_failure},
    tags=["hero", "sibei-flow"],
) as dag:
    dbt_seed = BashOperator(
        task_id="dbt_seed",
        bash_command=_dbt("seed"),
    )
    dbt_run_staging = BashOperator(
        task_id="dbt_run_staging",
        bash_command=_dbt("run --select staging"),
    )
    dbt_build_orders = BashOperator(
        task_id="dbt_build_orders",
        bash_command=_dbt("build --select orders"),
        # This task's failure maps to the dbt orders model, not the task_id.
        env={"SBFLOW_NODE_UID": "model.analytics.orders"},
        append_env=True,
    )

    dbt_seed >> dbt_run_staging >> dbt_build_orders
