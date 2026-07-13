"""Postgres connection helpers."""

from __future__ import annotations

import time

import psycopg
from psycopg.rows import dict_row


def connect(database_url: str) -> psycopg.Connection:
    """Open a connection with dict rows and manual transaction control."""
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


def connect_with_retry(
    database_url: str, attempts: int = 30, delay: float = 1.0
) -> psycopg.Connection:
    """Connect, retrying while Postgres is still coming up (compose startup)."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return connect(database_url)
        except psycopg.OperationalError as e:  # not ready yet
            last = e
            print(
                f"[worker] postgres not ready (attempt {i}/{attempts}): {e}", flush=True
            )
            time.sleep(delay)
    raise RuntimeError(
        f"could not connect to postgres after {attempts} attempts: {last}"
    )
