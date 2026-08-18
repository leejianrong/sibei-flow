"""Build + POST the frozen ``Failure`` contract to the brain's ``/webhook``.

The ``Failure`` shape is a FROZEN contract (CLAUDE.md): ``{repo, run_id,
task_id, node_uid, error_text, adapter, run_results_ref?, source}``. Every
enrollment path (Airflow callback, dbt hook, the ``sbflow run`` cron wrapper)
posts exactly this shape; the CLI's ``source`` is ``"cli"``.

``post_failure`` is a thin ``urllib`` POST — no third-party HTTP dependency, and
easy for tests to monkeypatch as a single seam.

**KAN-929:** when a webhook secret is configured, the request is signed to
match the brain's HMAC verification (KAN-926,
``brain/src/webhook.rs::verify_signature``): ``X-Sbflow-Signature:
sha256=<hex hmac>``, HMAC-SHA256 keyed by the secret over the exact bytes
``f"{timestamp}.{raw_body}"`` (the plain decimal timestamp, a literal ``.``,
then the raw JSON body bytes actually sent — never a re-encoded copy), plus
``X-Sbflow-Timestamp: <timestamp>``. When no secret is configured, the request
is sent exactly as before this change — no new headers, no behavior change.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
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


def post_failure(
    url: str,
    payload: dict[str, Any],
    timeout: float = 5.0,
    secret: str | None = None,
) -> None:
    """POST the Failure payload as JSON. Fire-and-forget; raises on transport error.

    When ``secret`` is a non-empty string, signs the exact bytes being sent
    (KAN-929) so the request passes the brain's KAN-926 HMAC verification. When
    ``secret`` is empty/``None``, behaves exactly as before this change.
    """
    missing = [k for k in _REQUIRED if k not in payload]
    if missing:
        raise ValueError(f"Failure payload missing required keys: {missing}")
    raw_body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        timestamp = int(time.time())
        # Signed message is `{timestamp}.{raw_body}` over raw bytes, matching
        # brain/src/webhook.rs::verify_signature byte-for-byte.
        message = f"{timestamp}.".encode() + raw_body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        headers["X-Sbflow-Signature"] = f"sha256={signature}"
        headers["X-Sbflow-Timestamp"] = str(timestamp)
    req = urllib.request.Request(
        url,
        data=raw_body,
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (config-owned URL)
