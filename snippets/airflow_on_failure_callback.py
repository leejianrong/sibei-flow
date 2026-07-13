"""sibei-flow enrollment for Airflow — the one-line ``on_failure_callback`` (U1).

Copy this file into your Airflow project (or ``pip install`` it alongside your
DAGs) and wire it as the ``on_failure_callback`` on a DAG, a task, or your
``default_args`` — that is the whole enrollment (R1.2):

    from sbflow_on_failure_callback import sbflow_on_failure

    with DAG(..., default_args={"on_failure_callback": sbflow_on_failure}):
        ...

When a task fails, Airflow calls this hook, which POSTs the frozen sibei-flow
``Failure`` contract to the brain's webhook. No standing access to your infra is
required (ADR-0004): the brain receives a structured payload, not scraped logs.

Configuration comes from the task environment (no secrets live here):

    SBFLOW_WEBHOOK_URL   brain webhook (default http://localhost:8080/webhook)
    SBFLOW_REPO          owner/name of the repo the pipeline builds from
    SBFLOW_ADAPTER       warehouse adapter (default postgres)

Trust posture: this hook only *reports* a failure. It holds no credentials and
performs no writes.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

DEFAULT_WEBHOOK_URL = "http://localhost:8080/webhook"


def sbflow_on_failure(context: dict[str, Any]) -> None:
    """Airflow ``on_failure_callback``: POST a Failure to the sibei-flow brain."""
    ti = context["task_instance"]
    exc = context.get("exception")
    # dbt-in-Airflow: the dbt node's unique_id is the best node_uid when exposed
    # (e.g. via a rendered field / XCom); fall back to the Airflow task id.
    node_uid = os.environ.get("SBFLOW_NODE_UID") or ti.task_id
    payload = {
        "repo": os.environ.get("SBFLOW_REPO", "acme/analytics"),
        "run_id": context["run_id"],
        "task_id": ti.task_id,
        "node_uid": node_uid,
        "error_text": str(exc) if exc else "task failed",
        "adapter": os.environ.get("SBFLOW_ADAPTER", "postgres"),
        "source": "airflow",
    }
    run_results_ref = os.environ.get("SBFLOW_RUN_RESULTS_REF")
    if run_results_ref:
        payload["run_results_ref"] = run_results_ref

    url = os.environ.get("SBFLOW_WEBHOOK_URL", DEFAULT_WEBHOOK_URL)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)  # fire-and-forget
    except Exception as e:  # never let reporting mask the pipeline's own failure
        print(f"[sbflow] could not report failure to {url}: {e}")
