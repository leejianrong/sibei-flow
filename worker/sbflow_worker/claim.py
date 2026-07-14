"""N5 claim loop — the durable-queue claim protocol.

``SELECT … FOR UPDATE SKIP LOCKED`` claims the oldest queued job under a lease so
that concurrent workers never grab the same row. In V1 the lease is set (V5 adds
expiry-based re-claim / crash recovery). After claiming, the stubbed agent runs
and the ``no_fix`` result is written back, marking the job ``done``.
"""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from psycopg.types.json import Json

from .db import DictConnection

#: A processor turns a claimed job into a RepairResult dict.
Processor = Callable[[dict[str, Any]], dict[str, Any]]

# Crash recovery (V5 task 2, R7.1): a job is claimable when it is freshly
# `queued`, OR when it is stuck in a non-terminal working state (`claimed` /
# `verifying`) whose lease has expired — i.e. the worker that held it died
# without writing a result. Re-claiming an expired lease lets another worker
# resume the run. `FOR UPDATE SKIP LOCKED` still guarantees exactly one worker
# takes any given row. Repair jobs are idempotent/re-runnable (ADR-0009), so a
# resumed job is safe.
_CLAIM_SQL = """
    SELECT id, payload, failure_class
    FROM repair_jobs
    WHERE (state = 'queued'
           OR (state IN ('claimed', 'verifying') AND lease_expires_at < now()))
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
"""

_LEASE_SQL = """
    UPDATE repair_jobs
    SET state = 'claimed',
        lease_expires_at = now() + make_interval(secs => %s),
        updated_at = now()
    WHERE id = %s
"""

_WRITE_BACK_SQL = """
    UPDATE repair_jobs
    SET state = 'done',
        result = %s,
        updated_at = now()
    WHERE id = %s
"""


def claim_one(conn: DictConnection, lease_seconds: int) -> dict[str, Any] | None:
    """Atomically claim the next queued job and set its lease.

    Returns the claimed job dict, or ``None`` if the queue is empty. The claim
    commits before processing so the lease is durably visible.
    """
    with conn.transaction():
        row = conn.execute(_CLAIM_SQL).fetchone()
        if row is None:
            return None
        conn.execute(_LEASE_SQL, (lease_seconds, row["id"]))
    return row


def write_back(conn: DictConnection, job_id: UUID, result: dict[str, Any]) -> None:
    """Write the RepairResult and mark the job ``done``."""
    with conn.transaction():
        conn.execute(_WRITE_BACK_SQL, (Json(result), job_id))


def claim_and_process(
    conn: DictConnection, lease_seconds: int, process: Processor
) -> UUID | None:
    """Claim one job, run ``process`` (the repair agent), write the result back.
    Returns the job id processed, or ``None`` when the queue is empty."""
    job = claim_one(conn, lease_seconds)
    if job is None:
        return None
    result = process(job)
    write_back(conn, job["id"], result)
    return job["id"]
