"""Environment-driven worker configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Default record/replay session bundled in the package (flagship rename case).
_DEFAULT_REPLAY = str(Path(__file__).parent / "replays" / "rename_drift.json")


@dataclass(frozen=True)
class Config:
    """Worker runtime settings."""

    # --- V1: durable queue -------------------------------------------------
    database_url: str
    #: Lease/visibility timeout set on claim (seconds). V5 uses this for
    #: crash-recovery re-claim; in V1 it is simply populated.
    lease_seconds: int
    #: Poll interval when the queue is empty (seconds). A plain poll is fine for
    #: V1; LISTEN/NOTIFY latency work is V5.
    poll_interval: float

    # --- V2: agent loop ----------------------------------------------------
    #: Read-only warehouse connection for `get_schema` (INFORMATION_SCHEMA).
    #: Optional: when unset, `get_schema` reports "not configured".
    warehouse_url: str | None
    #: Local checkout root the agent reads source from (read-only). A
    #: git-token-backed provider slots in behind the same interface at V4.
    repo_root: str
    #: LlmProvider selection: replay | claude | openai.
    llm_provider: str
    #: Recommended model when using the Claude provider (ADR-0007).
    llm_model: str
    #: Base URL for the OpenAI-compatible / local provider (BYO endpoint).
    llm_base_url: str | None
    #: Recorded session for the replay provider (keyless, deterministic).
    replay_session: str
    #: Bounded loop cap — after N provider turns, give up cleanly (R3.3).
    max_turns: int
    #: Diff guard: reject an edit whose file diff exceeds this many lines (B-S5).
    diff_max_lines: int

    # --- V3: tiered verification sandbox (ADR-0006, B-S2) ------------------
    #: Pre-baked sandbox image (python + dbt-core + dbt-postgres + git).
    sandbox_image: str
    #: Docker network the sandbox joins to reach the warehouse (compose default).
    sandbox_network: str | None
    #: Host-visible working root the sandbox project is materialized under. Must
    #: be bind-mounted into the worker at the SAME path so `docker run -v` paths
    #: resolve on the host daemon (Docker-out-of-Docker).
    sandbox_work_dir: str
    #: Hard timeout (seconds) on any single sandbox `docker run`.
    sandbox_timeout: int
    #: Writable read-only *dev/sample* connection for tier-2 `dbt build`. When
    #: unset, tier-2 does not run and its absence is disclosed (never prod).
    sample_warehouse_url: str | None
    #: Row cap passed to tier-2 as a dbt var (a real project gates on it).
    sample_limit: int
    #: Enable the sandbox at all. Off keeps the V2 behaviour (evidence=null).
    sandbox_enabled: bool

    @staticmethod
    def from_env() -> "Config":
        return Config(
            database_url=os.environ.get(
                "DATABASE_URL", "postgres://sibei:sibei@localhost:5455/sibei"
            ),
            lease_seconds=int(os.environ.get("LEASE_SECONDS", "60")),
            poll_interval=float(os.environ.get("POLL_INTERVAL", "2.0")),
            warehouse_url=os.environ.get("WAREHOUSE_URL") or None,
            repo_root=os.environ.get("REPO_ROOT", "/repo"),
            llm_provider=os.environ.get("LLM_PROVIDER", "replay"),
            llm_model=os.environ.get("LLM_MODEL", "claude-opus-4-8"),
            llm_base_url=os.environ.get("LLM_BASE_URL") or None,
            replay_session=os.environ.get("REPLAY_SESSION", _DEFAULT_REPLAY),
            max_turns=int(os.environ.get("MAX_TURNS", "6")),
            diff_max_lines=int(os.environ.get("DIFF_MAX_LINES", "40")),
            sandbox_image=os.environ.get("SANDBOX_IMAGE", "sbflow-sandbox:latest"),
            sandbox_network=os.environ.get("SANDBOX_NETWORK") or None,
            sandbox_work_dir=os.environ.get("SANDBOX_WORK_DIR", "/tmp/sbflow-sandbox"),
            sandbox_timeout=int(os.environ.get("SANDBOX_TIMEOUT", "120")),
            sample_warehouse_url=os.environ.get("SAMPLE_WAREHOUSE_URL") or None,
            sample_limit=int(os.environ.get("SAMPLE_LIMIT", "10000")),
            sandbox_enabled=os.environ.get("SANDBOX_ENABLED", "1") not in ("0", "", "false"),
        )
