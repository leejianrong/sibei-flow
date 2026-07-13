"""Worker entrypoint — a plain poll loop over the Postgres queue.

A plain poll is intentional for V1 (LISTEN/NOTIFY latency tuning is V5). On each
tick the worker drains all currently-claimable jobs, then sleeps.
"""

from __future__ import annotations

import time

import psycopg

from .agent import build_processor
from .claim import claim_and_process
from .config import Config
from .db import connect_with_retry
from .notify import JobNotifier


def wait_for_schema(
    conn: psycopg.Connection, attempts: int = 60, delay: float = 1.0
) -> None:
    """Block until the brain has applied its migration (the table exists).

    The brain owns the schema (ADR-0009); the worker just waits for it.
    """
    for i in range(1, attempts + 1):
        exists = conn.execute(
            "SELECT to_regclass('repair_jobs') IS NOT NULL"
        ).fetchone()
        conn.rollback()  # close the read txn (autocommit is off)
        if exists and next(iter(exists.values())):
            return
        print(f"[worker] waiting for schema (attempt {i}/{attempts})", flush=True)
        time.sleep(delay)
    raise RuntimeError("repair_jobs table never appeared")


def _prewarm_sandbox(cfg: Config) -> None:
    """Pre-bake the verification image so the first job isn't a build (B-S6)."""
    from .sandbox.runner import SandboxError, SandboxRunner, cleanup_orphans

    # Crash recovery (V5 task 2): sweep any ephemeral sandbox containers a
    # previously-crashed worker left behind before we start taking jobs.
    swept = cleanup_orphans()
    if swept:
        print(f"[worker] removed {swept} orphaned sandbox container(s)", flush=True)

    runner = SandboxRunner(repo_root=cfg.repo_root, image=cfg.sandbox_image)
    try:
        print("[worker] ensuring sandbox image is pre-baked…", flush=True)
        runner.ensure_image()
        print(f"[worker] sandbox image ready: {cfg.sandbox_image}", flush=True)
    except SandboxError as e:
        print(
            f"[worker] WARNING: sandbox image not ready ({e}); "
            "jobs will fail verification until it is available",
            flush=True,
        )


def main() -> None:
    cfg = Config.from_env()
    conn = connect_with_retry(cfg.database_url)
    wait_for_schema(conn)
    if cfg.sandbox_enabled:
        _prewarm_sandbox(cfg)
    process = build_processor(cfg)
    # LISTEN/NOTIFY fast dispatch (V5 task 6): wake on enqueue instead of waiting
    # out the poll interval. The poll below is still the durability fallback, so
    # a missed notification never strands a job.
    notifier = JobNotifier(cfg.database_url) if cfg.listen_notify else None
    print(
        f"[worker] started; provider={cfg.llm_provider} model={cfg.llm_model} "
        f"lease={cfg.lease_seconds}s poll={cfg.poll_interval}s "
        f"listen={'on' if notifier else 'off'}",
        flush=True,
    )
    try:
        while True:
            drained = 0
            while True:
                job_id = claim_and_process(conn, cfg.lease_seconds, process)
                if job_id is None:
                    break
                drained += 1
                print(f"[worker] processed job {job_id}", flush=True)
            if drained == 0:
                # Block until notified of a new job, or until the poll interval
                # elapses (fallback). A plain sleep when LISTEN is disabled.
                if notifier is not None:
                    notifier.wait(cfg.poll_interval)
                else:
                    time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        print("[worker] shutting down", flush=True)
    finally:
        if notifier is not None:
            notifier.close()
        conn.close()


if __name__ == "__main__":
    main()
