"""The agent's tool surface (N7/N8/N9) and dispatch.

Narrow, stable tool contract (worker-internal, stable into phase B):
`read_file(path, ref)`, `get_schema(source)`, `edit_file(path, old_string,
new_string)`. Tools mutate a shared :class:`AgentContext` (the in-memory working
copy); `edit_file` runs the diff guard and reverts a rejected edit so the model
can re-draft.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING

from ..llm.base import ToolCall, ToolSpec
from .diffguard import DiffGuard
from .diffing import WorkingCopy
from .schema import WarehouseSchema
from .source import LocalSourceProvider

if TYPE_CHECKING:  # avoid importing docker/sandbox machinery at module load
    from ..sandbox.runner import SandboxRun, SandboxRunner

TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="read_file",
        description="Read a source file (read-only) at the failing ref.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative file path"},
                "ref": {"type": "string", "description": "Git ref (optional)"},
            },
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="get_schema",
        description=(
            "Read the CURRENT columns of an upstream warehouse source "
            "(read-only INFORMATION_SCHEMA). Use to confirm a drift."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Source table, e.g. 'raw.raw_customers'",
                }
            },
            "required": ["source"],
        },
    ),
    ToolSpec(
        name="edit_file",
        description=(
            "Apply a targeted, minimal edit: replace old_string with new_string "
            "(old_string must match exactly once). Only the failing model may be "
            "edited; oversized/out-of-scope edits are rejected."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    ),
    ToolSpec(
        name="run_sandbox",
        description=(
            "Verify the current candidate fix in an ephemeral sandbox: tier-1 "
            "`dbt compile` (always) and tier-2 `dbt build` on a sample (only if a "
            "dev connection is configured). Returns which tiers ran and passed. "
            "Run this after editing; if tier-1 fails, redraft."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "select": {
                    "type": "string",
                    "description": "Model to verify (defaults to the failing model).",
                }
            },
        },
    ),
]


@dataclass
class AgentContext:
    source: LocalSourceProvider
    warehouse: WarehouseSchema | None
    working: WorkingCopy
    guard: DiffGuard
    allowed_paths: set[str]
    #: V3 verification sandbox (N10). None keeps the V2 behaviour (no evidence).
    sandbox: "SandboxRunner | None" = None
    #: The failing model to compile/build (dbt select name, e.g. "orders").
    model_select: str | None = None
    #: Cache of the most recent sandbox run + the diff it verified, so the loop's
    #: terminal compile gate reuses the model's `run_sandbox` result (one run).
    last_run: "SandboxRun | None" = None
    last_verified_diff: str = ""

    # --- tools -------------------------------------------------------------
    def read_file(self, path: str, ref: str | None = None) -> tuple[str, bool]:
        content = self.source.read(path, ref)
        self.working.load(path, content)
        numbered = "".join(
            f"{i + 1:>4}  {line}\n" for i, line in enumerate(content.splitlines())
        )
        return f"{path}:\n{numbered}", False

    def get_schema(self, source: str) -> tuple[str, bool]:
        if self.warehouse is None:
            return "get_schema: no read-only warehouse connection configured", True
        return self.warehouse.describe(source), False

    def edit_file(self, path: str, old_string: str, new_string: str) -> tuple[str, bool]:
        if not self.working.has(path):
            try:
                self.working.load(path, self.source.read(path))
            except (FileNotFoundError, ValueError) as e:
                return f"cannot edit {path}: {e}", True

        prev = self.working.current(path)
        ok, msg = self.working.apply_replace(path, old_string, new_string)
        if not ok:
            return f"edit rejected: {msg}", True

        passed, reason = self.guard.check(path, self.working, self.allowed_paths)
        if not passed:
            self.working.set_current(path, prev)  # revert; let the model re-draft
            return f"diff guard rejected: {reason}", True

        return f"edit applied to {path}", False

    def run_sandbox(self, select: str | None = None) -> tuple[str, bool]:
        """N10 — verify the current candidate fix in the ephemeral sandbox.

        Result is cached against the diff it verified so the loop's terminal
        compile gate can reuse it without a second run.
        """
        if self.sandbox is None:
            return (
                "run_sandbox: no verification sandbox configured (tier verification "
                "is enabled in the V3 deployment).",
                False,
            )
        if not self.working.changed_paths():
            return "run_sandbox: no candidate edit to verify yet.", True
        model = select or self.model_select
        if not model:
            return "run_sandbox: no model to verify (missing failing model).", True
        run = self.verify_current(model)
        return _summarize_run(run), False

    def verify_current(self, select: str) -> "SandboxRun":
        """Run tiered verification on the current working copy and cache it."""
        run = self.sandbox.verify(self.working, select)  # type: ignore[union-attr]
        self.last_run = run
        self.last_verified_diff = self.working.full_diff()
        return run


def dispatch(ctx: AgentContext, call: ToolCall) -> tuple[str, bool]:
    """Execute a tool call; return ``(result_text, is_error)``."""
    try:
        if call.name == "read_file":
            return ctx.read_file(call.input["path"], call.input.get("ref"))
        if call.name == "get_schema":
            return ctx.get_schema(call.input["source"])
        if call.name == "edit_file":
            return ctx.edit_file(
                call.input["path"],
                call.input["old_string"],
                call.input["new_string"],
            )
        if call.name == "run_sandbox":
            return ctx.run_sandbox(call.input.get("select"))
        return f"unknown tool: {call.name}", True
    except KeyError as e:
        return f"missing required argument {e} for {call.name}", True
    except Exception as e:  # surface tool errors to the model, don't crash the loop
        return f"{call.name} failed: {e}", True


def _summarize_run(run: "SandboxRun") -> str:
    """A short, model-facing summary of a sandbox run (so it can react)."""
    t1 = "PASSED" if run.tier1.passed else "FAILED"
    lines = [f"Sandbox verification of '{run.select}':", f"  tier-1 dbt compile: {t1}"]
    if not run.tier1.passed:
        lines.append("  (tier-1 failed — the fix does not compile; redraft.)")
        lines.append("  " + run.tier1.log.splitlines()[-1] if run.tier1.log else "")
    if run.tier2.ran:
        t2 = "PASSED" if run.tier2.passed else "FAILED"
        lines.append(f"  tier-2 dbt build (sample): {t2} (node status: {run.tier2.node_status})")
    else:
        lines.append(f"  tier-2 dbt build (sample): NOT RUN — {run.tier2.log}")
    return "\n".join(x for x in lines if x)
