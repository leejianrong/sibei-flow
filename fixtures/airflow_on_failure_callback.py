"""U1 (thin) — the one-line-ish Airflow hook a team adds to enroll a pipeline.

Shipped as a copy-paste snippet: set this as your DAG/task ``on_failure_callback``
and failures POST a structured payload to the brain's webhook. No standing access
to your infra is required (ADR-0004 / R2.1). Full packaged snippet lands in V5.
"""

from __future__ import annotations

import json
import os
import urllib.request


def sbflow_on_failure(context) -> None:
    ti = context["task_instance"]
    exc = context.get("exception")
    payload = {
        "repo": os.environ.get("SBFLOW_REPO", "acme/analytics"),
        "run_id": context["run_id"],
        "task_id": ti.task_id,
        # The dbt node unique_id the failing task maps to. A dbt-aware wrapper
        # knows this (e.g. model.analytics.orders); expose it via SBFLOW_NODE_UID
        # so the brain/worker can resolve the failing source file. Falls back to
        # the Airflow task_id when the mapping isn't provided.
        "node_uid": os.environ.get("SBFLOW_NODE_UID") or ti.task_id,
        "error_text": str(exc) if exc else "task failed",
        "adapter": os.environ.get("SBFLOW_ADAPTER", "postgres"),
        "source": "airflow",
    }
    url = os.environ.get("SBFLOW_WEBHOOK_URL", "http://localhost:8080/webhook")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)  # fire-and-forget
