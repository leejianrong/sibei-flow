"""Worker side of PRD Seam 2: the claim loop marks an in-scope job ``done`` with
``no_fix``, and never touches already-terminal (dropped) jobs.

Requires a reachable Postgres via ``DATABASE_URL`` (the compose ``postgres``
service works). The schema is created from the brain's canonical migration, so
there is one source of truth for the table shape.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from sbflow_worker.claim import claim_and_process

# Needs a reachable Postgres (DATABASE_URL). Deselect with `-m "not infra"`.
pytestmark = pytest.mark.infra

# These tests cover the claim MECHANICS (SKIP LOCKED, lease, write-back) in
# isolation from the agent, so they inject a trivial processor. The agent loop
# itself is covered by test_agent_loop.py.
_NO_FIX = lambda job: {"outcome": "no_fix"}  # noqa: E731

MIGRATION = (
    Path(__file__).resolve().parents[2] / "brain" / "migrations" / "0001_init.sql"
)
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgres://sibei:sibei@localhost:5432/sibei"
)


@pytest.fixture()
def conn():
    c = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
    # Ensure the schema exists (idempotent), then start each test from empty.
    with c.transaction():
        c.execute(MIGRATION.read_text())
        c.execute("TRUNCATE repair_jobs")
    yield c
    c.rollback()
    c.close()


def _insert_queued(conn, error_text='column "x" does not exist') -> uuid.UUID:
    job_id = uuid.uuid4()
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO repair_jobs
                (id, idem_key, repo, run_id, task_id, node_uid,
                 failure_class, payload, state)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued')
            """,
            (
                job_id,
                "k-" + job_id.hex,
                "acme/analytics",
                "run-1",
                "build_orders",
                "model.analytics.orders",
                "schema_drift",
                Json({"error_text": error_text}),
            ),
        )
    return job_id


def _get(conn, job_id):
    return conn.execute(
        "SELECT state, result, lease_expires_at FROM repair_jobs WHERE id = %s",
        (job_id,),
    ).fetchone()


def test_claims_queued_job_and_writes_no_fix(conn):
    job_id = _insert_queued(conn)

    processed = claim_and_process(conn, lease_seconds=60, process=_NO_FIX)
    assert processed == job_id

    row = _get(conn, job_id)
    assert row["state"] == "done"
    assert row["result"] == {"outcome": "no_fix"}
    # The lease column is populated on claim (used by V5 recovery).
    assert row["lease_expires_at"] is not None


def test_empty_queue_returns_none(conn):
    assert claim_and_process(conn, lease_seconds=60, process=_NO_FIX) is None


def test_dropped_jobs_are_never_claimed(conn):
    """An out-of-scope row recorded as done must not be dispatched to a worker."""
    job_id = uuid.uuid4()
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO repair_jobs
                (id, repo, failure_class, payload, state, result)
            VALUES (%s, %s, %s, %s, 'done', %s)
            """,
            (
                job_id,
                "acme/analytics",
                "out_of_scope:timeout",
                Json({"error_text": "statement timeout"}),
                Json({"outcome": "out_of_scope", "reason": "timeout"}),
            ),
        )

    assert claim_and_process(conn, lease_seconds=60, process=_NO_FIX) is None

    row = _get(conn, job_id)
    assert row["state"] == "done"
    assert row["result"] == {"outcome": "out_of_scope", "reason": "timeout"}


def test_only_one_worker_claims_a_job_skip_locked(conn):
    """A second connection holding the row lock cannot double-claim (SKIP LOCKED)."""
    job_id = _insert_queued(conn)

    other = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
    try:
        # `other` claims + locks the row inside an open transaction.
        other.execute("BEGIN")
        locked = other.execute(
            """
            SELECT id FROM repair_jobs
            WHERE state = 'queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        assert locked["id"] == job_id

        # Our worker must skip the locked row → nothing to do.
        assert claim_and_process(conn, lease_seconds=60, process=_NO_FIX) is None
    finally:
        other.rollback()
        other.close()
