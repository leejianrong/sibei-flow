"""LISTEN/NOTIFY dispatch (V5 task 6) — cut enqueue→claim latency.

The brain issues ``NOTIFY sbflow_jobs`` the moment it enqueues an in-scope job
(see brain/src/webhook.rs). The worker ``LISTEN``s on that channel and wakes
immediately instead of waiting out the poll interval.

This is a *latency* optimisation layered on top of the durable poll, never a
replacement: the caller still polls on every wake and on a timeout, so a missed
or dropped notification (connection blip, notify issued before LISTEN) can never
strand a job — at worst it waits one poll interval. The notifier degrades to a
plain sleep if the LISTEN connection can't be established.
"""

from __future__ import annotations

import time

import psycopg

CHANNEL = "sbflow_jobs"


class JobNotifier:
    """A dedicated autocommit connection that LISTENs for enqueue notifications.

    Robust by construction: any failure falls back to sleeping for the timeout,
    so the worker keeps making progress on the poll fallback regardless.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._conn: psycopg.Connection | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            conn = psycopg.connect(self._url, autocommit=True)
            conn.execute(f"LISTEN {CHANNEL}")
            self._conn = conn
            print(f"[worker] LISTEN {CHANNEL} active (fast dispatch)", flush=True)
        except psycopg.Error as e:
            self._conn = None
            print(
                f"[worker] LISTEN unavailable ({e}); falling back to poll only",
                flush=True,
            )

    def wait(self, timeout: float) -> bool:
        """Block up to ``timeout`` seconds for an enqueue notification.

        Returns ``True`` if a notification arrived (wake and drain now), ``False``
        on timeout. Never raises: on a connection error it reconnects and sleeps
        out the remaining time so the caller's poll loop is unaffected.
        """
        if self._conn is None:
            time.sleep(timeout)
            self._connect()  # try to re-establish for next time
            return False
        try:
            gen = self._conn.notifies(timeout=timeout, stop_after=1)
            for _ in gen:
                return True
            return False
        except psycopg.Error as e:
            print(f"[worker] LISTEN connection lost ({e}); reconnecting", flush=True)
            self.close()
            time.sleep(timeout)
            self._connect()
            return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except psycopg.Error:
                pass
            self._conn = None
