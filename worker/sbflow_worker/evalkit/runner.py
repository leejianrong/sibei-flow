"""Run eval cases against the real worker path and the brain (SIB-81).

Dispatched cases go through ``build_processor(cfg)`` — the exact callable the
claim loop hands every claimed job to — so the agent loop, the real tools, the
warehouse schema reads and the ephemeral dbt sandbox are all the production
code. The DB claim itself is skipped on purpose: it is covered by its own tests
and would only add queue noise to a measurement of repair quality.

``out_of_scope`` cases never reach the worker; the brain's Rust classifier drops
them. Those are checked by POSTing the failure to a running brain and reading
the recorded outcome back, which is also how a regression in the classifier
shows up here.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any

import psycopg

from ..agent import build_processor
from ..config import Config
from .case import EvalCase
from .report import CaseResult, score_repair


@dataclasses.dataclass(frozen=True)
class RunEnv:
    """Where the infra lives for this run."""

    #: Admin connection used ONLY to (re)build a case's upstream schema. It is
    #: the fixture/pipeline owner, never a sibei-flow credential.
    warehouse_admin_url: str | None = None
    #: Base URL of a running brain, for ``out_of_scope`` cases.
    brain_url: str | None = None
    brain_token: str | None = None
    webhook_secret: str | None = None


def _run_sql_file(admin_url: str, path: Any) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(path.read_text())


def _http(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (operator-owned URL)
        parsed: dict[str, Any] = json.loads(resp.read())
        return parsed


def classify_via_brain(case: EvalCase, env: RunEnv) -> CaseResult:
    """POST the case's failure to the brain and report what it recorded."""
    assert env.brain_url
    started = time.monotonic()
    payload = dict(case.failure)
    # A unique run_id so brain-side dedupe never collapses two eval runs.
    payload["run_id"] = f"eval-{case.id}-{int(time.time())}"
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if env.webhook_secret:
        ts = int(time.time())
        mac = hmac.new(
            env.webhook_secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256
        )
        headers["X-Sbflow-Signature"] = f"sha256={mac.hexdigest()}"
        headers["X-Sbflow-Timestamp"] = str(ts)
    ack = _http(f"{env.brain_url}/webhook", data=raw, headers=headers)

    auth = {"Authorization": f"Bearer {env.brain_token}"} if env.brain_token else {}
    run = _http(f"{env.brain_url}/api/runs/{ack['id']}", headers=auth)
    # Dropped failures are terminal immediately; an in-scope one stays `queued`
    # (and the live worker will pick it up — itself a failed expectation).
    actual = (
        "out_of_scope"
        if run.get("outcome") == "out_of_scope"
        else str(run.get("state"))
    )
    elapsed = time.monotonic() - started
    return CaseResult(
        case_id=case.id,
        expected=case.expect_outcome,
        actual=actual,
        passed=actual == case.expect_outcome,
        elapsed_s=round(elapsed, 3),
        detail=str(
            (run.get("result") or {}).get("reason") or run.get("failure_class") or ""
        ),
    )


def run_dispatched(case: EvalCase, cfg: Config, env: RunEnv) -> CaseResult:
    """Run one worker-bound case through the production repair path."""
    assert case.project_dir is not None
    if case.warehouse_sql and env.warehouse_admin_url:
        _run_sql_file(env.warehouse_admin_url, case.warehouse_sql)
    try:
        case_cfg = dataclasses.replace(
            cfg,
            repo_root=str(case.project_dir),
            replay_session=str(case.replay) if case.replay else cfg.replay_session,
        )
        process = build_processor(case_cfg)
        job = {"node_uid": case.failure["node_uid"], "payload": case.failure}
        started = time.monotonic()
        result = process(job)
        return score_repair(case, result, time.monotonic() - started)
    finally:
        if case.teardown_sql and env.warehouse_admin_url:
            _run_sql_file(env.warehouse_admin_url, case.teardown_sql)


def run_case(case: EvalCase, cfg: Config, env: RunEnv) -> CaseResult:
    """Run one case, turning any failure of the run itself into an ``error`` result."""
    try:
        if not case.dispatched:
            if not env.brain_url:
                return CaseResult(
                    case_id=case.id,
                    expected=case.expect_outcome,
                    actual="skipped",
                    passed=False,
                    detail="out_of_scope cases need a running brain (set SBFLOW_BRAIN_URL)",
                )
            return classify_via_brain(case, env)
        return run_dispatched(case, cfg, env)
    except (urllib.error.URLError, psycopg.Error, OSError, KeyError, ValueError) as e:
        return CaseResult(
            case_id=case.id,
            expected=case.expect_outcome,
            actual="error",
            passed=False,
            detail=f"{type(e).__name__}: {e}"[:300],
        )
