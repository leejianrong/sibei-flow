"""U1 (thin) — the one-line-ish Airflow hook a team adds to enroll a pipeline.

Shipped as a copy-paste snippet: set this as your DAG/task ``on_failure_callback``
and failures POST a structured payload to the brain's webhook. No standing access
to your infra is required (ADR-0004 / R2.1). Full packaged snippet lands in V5.
"""

from __future__ import annotations

import json
import os
import urllib.request


def _dbt_failure(run_results_path: str) -> tuple[str, str] | None:
    """Extract ``(node_uid, error_text)`` for the first failed node from a dbt
    ``run_results.json``. This is the *real* dbt error (e.g. ``column
    "customer_id" does not exist``) — an Airflow BashOperator only surfaces a
    generic "Bash command failed" exception, which carries no drift signal."""
    try:
        with open(run_results_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    for res in data.get("results", []):
        if res.get("status") in ("error", "fail"):
            msg = res.get("message") or ""
            uid = res.get("unique_id") or ""
            if msg:
                return uid, msg
    return None


def sbflow_on_failure(context) -> None:
    ti = context["task_instance"]
    exc = context.get("exception")

    node_uid = os.environ.get("SBFLOW_NODE_UID") or ti.task_id
    error_text = str(exc) if exc else "task failed"

    # Prefer the dbt run_results.json (the Failure contract's run_results_ref):
    # it carries the actual database error the brain classifies on, and the dbt
    # node's unique_id — far better than the generic operator exception.
    run_results = os.environ.get("SBFLOW_RUN_RESULTS")
    run_results_ref = None
    if run_results:
        found = _dbt_failure(run_results)
        if found:
            uid, msg = found
            error_text = msg
            node_uid = uid or node_uid
            run_results_ref = run_results

    payload = {
        "repo": os.environ.get("SBFLOW_REPO", "acme/analytics"),
        "run_id": context["run_id"],
        "task_id": ti.task_id,
        # The dbt node unique_id the failing task maps to (from run_results when
        # available, else SBFLOW_NODE_UID, else the Airflow task_id).
        "node_uid": node_uid,
        "error_text": error_text,
        "adapter": os.environ.get("SBFLOW_ADAPTER", "postgres"),
        "source": "airflow",
    }
    if run_results_ref:
        payload["run_results_ref"] = run_results_ref
    url = os.environ.get("SBFLOW_WEBHOOK_URL", "http://localhost:8080/webhook")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)  # fire-and-forget
