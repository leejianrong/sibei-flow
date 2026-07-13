"""Build + POST the frozen ``Failure`` contract to the brain's ``/webhook``.

The ``Failure`` shape is a FROZEN contract (CLAUDE.md): ``{repo, run_id,
task_id, node_uid, error_text, adapter, run_results_ref?, source}``. Every
enrollment path (Airflow callback, dbt hook, the ``sbflow run`` cron wrapper)
posts exactly this shape; the CLI's ``source`` is ``"cli"``.

``post_failure`` is a thin ``urllib`` POST — no third-party HTTP dependency, and
easy for tests to monkeypatch as a single seam.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

# Keys the ``Failure`` contract allows. ``run_results_ref`` is optional.
_REQUIRED = ("repo", "run_id", "task_id", "node_uid", "error_text", "adapter", "source")


def build_failure(
    *,
    repo: str,
    run_id: str,
    task_id: str,
    node_uid: str,
    error_text: str,
    adapter: str,
    source: str,
    run_results_ref: str | None = None,
) -> dict[str, Any]:
    """Assemble a Failure payload, dropping ``run_results_ref`` when absent."""
    payload: dict[str, Any] = {
        "repo": repo,
        "run_id": run_id,
        "task_id": task_id,
        "node_uid": node_uid,
        "error_text": error_text,
        "adapter": adapter,
        "source": source,
    }
    if run_results_ref:
        payload["run_results_ref"] = run_results_ref
    return payload


def post_failure(url: str, payload: dict[str, Any], timeout: float = 5.0) -> None:
    """POST the Failure payload as JSON. Fire-and-forget; raises on transport error."""
    missing = [k for k in _REQUIRED if k not in payload]
    if missing:
        raise ValueError(f"Failure payload missing required keys: {missing}")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (config-owned URL)
