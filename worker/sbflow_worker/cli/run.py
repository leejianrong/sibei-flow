"""``sbflow run -- <cmd>`` — the cron/script fallback detector (V5 task 4, R1.4).

The webhook is the primary detection path (ADR-0004); this wrapper covers
cron jobs and plain scripts that have no orchestrator callback. It runs an
arbitrary command, streams its output live, and — only if the command exits
non-zero — POSTs the frozen ``Failure`` payload (``source: "cli"``) to the
brain's webhook, then exits with the command's own status code.

For dbt runs it mirrors the structured detection the Airflow callback gets: if a
``run_results.json`` is present (``target/run_results.json`` by default), it
lifts the failed node's ``unique_id`` and ``message`` into ``node_uid`` /
``error_text`` and records the file as ``run_results_ref``. Otherwise it falls
back to the tail of the command's combined output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import CliConfig
from .webhook import build_failure, post_failure

# How many trailing output lines to keep for the error_text fallback.
_TAIL_LINES = 40


def _run_streaming(command: list[str]) -> tuple[int, str]:
    """Run ``command``, tee combined output to our stdout, return (rc, tail).

    stderr is merged into stdout so the captured tail holds the real error text
    regardless of which stream the tool used (dbt writes to stdout).
    """
    tail: deque[str] = deque(maxlen=_TAIL_LINES)
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line.rstrip("\n"))
    proc.wait()
    return proc.returncode, "\n".join(tail)


def _find_run_results(command: list[str], explicit: str | None) -> Path | None:
    """Locate a dbt ``run_results.json`` to enrich the payload from."""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    # Honor a --project-dir/--target-path passed to dbt; else default target/.
    candidates: list[Path] = []
    for i, tok in enumerate(command):
        if tok in ("--target-path",) and i + 1 < len(command):
            candidates.append(Path(command[i + 1]) / "run_results.json")
        if tok in ("--project-dir",) and i + 1 < len(command):
            candidates.append(Path(command[i + 1]) / "target" / "run_results.json")
    candidates.append(Path("target") / "run_results.json")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _failed_node_from_run_results(path: Path) -> tuple[str | None, str | None]:
    """Return (unique_id, message) of the first failed dbt result, if any."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, None
    for r in data.get("results", []):
        status = str(r.get("status", "")).lower()
        if status in ("error", "fail", "runtime error"):
            msg = r.get("message") or f"dbt node {r.get('unique_id')} {status}"
            return r.get("unique_id"), msg
    return None, None


def cmd_run(command: list[str], cfg: CliConfig, args: Any) -> int:
    """Execute ``command`` and report a Failure on non-zero exit. Returns rc."""
    if not command:
        print(
            "sbflow run: no command given (usage: sbflow run -- <cmd>)", file=sys.stderr
        )
        return 2

    rc, output_tail = _run_streaming(command)
    if rc == 0:
        # A passing command posts nothing — detection is failure-only.
        return 0

    # Build the Failure. Prefer structured dbt run_results.json when present.
    run_results = _find_run_results(command, getattr(args, "run_results", None))
    node_from_results, msg_from_results = (None, None)
    run_results_ref: str | None = None
    if run_results is not None:
        node_from_results, msg_from_results = _failed_node_from_run_results(run_results)
        run_results_ref = str(run_results)

    task_id = getattr(args, "task", None) or Path(command[0]).name
    run_id = (
        getattr(args, "run_id", None)
        or os.environ.get("SBFLOW_RUN_ID")
        or f"cli-{int(time.time())}"
    )
    node_uid = node_from_results or task_id
    error_text = (
        msg_from_results
        or output_tail
        or f"command exited with status {rc}: {' '.join(command)}"
    )

    payload = build_failure(
        repo=cfg.repo,
        run_id=run_id,
        task_id=task_id,
        node_uid=node_uid,
        error_text=error_text,
        adapter=cfg.adapter,
        source="cli",
        run_results_ref=run_results_ref,
    )
    try:
        post_failure(cfg.webhook_url, payload)
        print(
            f"[sbflow] command failed (rc={rc}); reported failure of "
            f"'{node_uid}' to {cfg.webhook_url}",
            file=sys.stderr,
        )
    except Exception as e:  # noqa: BLE001 — never mask the command's own failure
        print(f"[sbflow] WARNING: could not reach webhook: {e}", file=sys.stderr)

    # Pass through the command's exit code — cron/CI must still see the failure.
    return rc
