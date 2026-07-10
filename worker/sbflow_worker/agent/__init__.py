"""The repair agent (V2) — bounded loop + tool surface behind an LlmProvider.

`build_processor(cfg)` returns a `process(job) -> RepairResult` callable the
claim loop uses. A fresh provider + context is built per job (the replay
provider is stateful).
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import Config
from ..llm import get_provider
from .diffguard import DiffGuard
from .diffing import WorkingCopy
from .loop import run_repair
from .manifest import allowed_edit_paths, resolve_file
from .schema import WarehouseSchema
from .source import LocalSourceProvider
from .tools import AgentContext

__all__ = ["build_processor", "run_repair", "AgentContext"]


def build_processor(cfg: Config) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Build the per-job repair processor from config."""

    def process(job: dict[str, Any]) -> dict[str, Any]:
        payload = job.get("payload") or {}
        node_uid = job.get("node_uid") or payload.get("node_uid") or ""

        source = LocalSourceProvider(cfg.repo_root)
        warehouse = WarehouseSchema(cfg.warehouse_url) if cfg.warehouse_url else None
        failing_file = resolve_file(cfg.repo_root, node_uid) if node_uid else None
        allowed = (
            allowed_edit_paths(cfg.repo_root, failing_file) if failing_file else set()
        )

        sandbox = None
        if cfg.sandbox_enabled:
            from ..sandbox.runner import SandboxRunner

            sandbox = SandboxRunner(
                repo_root=cfg.repo_root,
                image=cfg.sandbox_image,
                warehouse_url=cfg.warehouse_url,
                sample_url=cfg.sample_warehouse_url,
                network=cfg.sandbox_network,
                work_dir=cfg.sandbox_work_dir,
                timeout=cfg.sandbox_timeout,
                sample_limit=cfg.sample_limit,
            )
        model_select = node_uid.split(".")[-1] if node_uid else None

        ctx = AgentContext(
            source=source,
            warehouse=warehouse,
            working=WorkingCopy(),
            guard=DiffGuard(max_lines=cfg.diff_max_lines),
            allowed_paths=allowed,
            sandbox=sandbox,
            model_select=model_select,
        )
        task = {
            "repo": payload.get("repo"),
            "node_uid": node_uid,
            "adapter": payload.get("adapter"),
            "error_text": payload.get("error_text"),
            "failing_file": failing_file,
        }
        provider = get_provider(cfg)  # fresh per job (replay is stateful)
        return run_repair(provider, ctx, task, max_turns=cfg.max_turns)

    return process
