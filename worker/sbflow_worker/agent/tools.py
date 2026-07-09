"""The agent's tool surface (N7/N8/N9) and dispatch.

Narrow, stable tool contract (worker-internal, stable into phase B):
`read_file(path, ref)`, `get_schema(source)`, `edit_file(path, old_string,
new_string)`. Tools mutate a shared :class:`AgentContext` (the in-memory working
copy); `edit_file` runs the diff guard and reverts a rejected edit so the model
can re-draft.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..llm.base import ToolCall, ToolSpec
from .diffguard import DiffGuard
from .diffing import WorkingCopy
from .schema import WarehouseSchema
from .source import LocalSourceProvider

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
]


@dataclass
class AgentContext:
    source: LocalSourceProvider
    warehouse: WarehouseSchema | None
    working: WorkingCopy
    guard: DiffGuard
    allowed_paths: set[str]

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
        return f"unknown tool: {call.name}", True
    except KeyError as e:
        return f"missing required argument {e} for {call.name}", True
    except Exception as e:  # surface tool errors to the model, don't crash the loop
        return f"{call.name} failed: {e}", True
