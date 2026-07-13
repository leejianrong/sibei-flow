"""LISTEN/NOTIFY fast-dispatch tests (V5 task 6).

The worker LISTENs on ``sbflow_jobs``; the brain NOTIFYs on enqueue. These
assert the notifier wakes promptly on a notification and times out cleanly
otherwise, so the poll fallback stays intact.

Requires a reachable Postgres via ``DATABASE_URL``. Deselect with `-m "not infra"`.
"""

from __future__ import annotations

import os
import time

import psycopg
import pytest

from sbflow_worker.notify import CHANNEL, JobNotifier

pytestmark = pytest.mark.infra

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgres://sibei:sibei@localhost:5432/sibei"
)


def test_notifier_wakes_on_pg_notify():
    notifier = JobNotifier(DATABASE_URL)
    try:
        # Fire the notification from an independent connection (as the brain does).
        with psycopg.connect(DATABASE_URL, autocommit=True) as other:
            other.execute(f"SELECT pg_notify('{CHANNEL}', 'job-1')")
        start = time.monotonic()
        woke = notifier.wait(timeout=5.0)
        elapsed = time.monotonic() - start
        assert woke is True, "notifier must wake on a notification"
        assert elapsed < 5.0, "should return well before the timeout"
    finally:
        notifier.close()


def test_notifier_times_out_without_notification():
    notifier = JobNotifier(DATABASE_URL)
    try:
        start = time.monotonic()
        woke = notifier.wait(timeout=0.4)
        elapsed = time.monotonic() - start
        assert woke is False, "no notification → timeout returns False"
        # Blocked roughly for the timeout (the poll fallback interval).
        assert elapsed >= 0.3
    finally:
        notifier.close()
