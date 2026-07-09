"""N6 bounded agent loop: read → draft → edit → guard → emit.

Behind an :class:`LlmProvider` (ADR-0007). Bounded at ``max_turns`` provider
turns (R3.3 — clean give-up, no runaway). V2 does **no** sandbox verification,
so a drafted fix is emitted as ``pr_proposed`` with ``evidence = null`` (the UI
labels it *unverified* until V3).
"""

from __future__ import annotations

from typing import Any

from ..llm.base import LlmProvider, ToolSpec
from .tools import TOOL_SPECS, AgentContext, dispatch

SYSTEM_PROMPT = """\
You are a careful data engineer fixing a failed dbt model. The failure is either
schema drift (an upstream column was renamed/removed/retyped) or a code/SQL error.

Diagnose, then make the smallest correct fix:
- Use read_file to read the failing model's SQL.
- Use get_schema to see the CURRENT upstream columns and confirm the drift
  (e.g. a column your SQL references is gone and a similarly-named one appears).
- Use edit_file for a targeted, minimal edit to the failing model ONLY. Do not
  reformat or touch unrelated files.
When the fix is in place, stop calling tools and reply with a short plain-English
explanation: what changed upstream and why your edit addresses it. If you cannot
fix it confidently, say so plainly instead of guessing.
"""

#: Truncate long tool outputs in the transcript so it stays legible.
_TRANSCRIPT_CLIP = 1200


def build_initial_prompt(task: dict[str, Any]) -> str:
    lines = [
        "A dbt model failed. Diagnose and fix it.",
        f"repo: {task.get('repo')}",
        f"failing node: {task.get('node_uid')}",
        f"adapter: {task.get('adapter')}",
    ]
    if task.get("failing_file"):
        lines.append(f"failing model file: {task['failing_file']}")
    lines.append("error:")
    lines.append(task.get("error_text", "") or "(no error text)")
    return "\n".join(lines)


def run_repair(
    provider: LlmProvider,
    ctx: AgentContext,
    task: dict[str, Any],
    max_turns: int,
    tools: list[ToolSpec] | None = None,
) -> dict[str, Any]:
    """Run the bounded loop and return a RepairResult dict."""
    tools = tools or TOOL_SPECS
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": build_initial_prompt(task)}]}
    ]
    transcript: list[str] = []
    last_text = ""

    for _ in range(max_turns):
        turn = provider.complete(SYSTEM_PROMPT, messages, tools)
        if turn.text:
            last_text = turn.text
            transcript.append(f"assistant: {turn.text}")

        # Record the assistant turn in the neutral history.
        assistant_content: list[dict[str, Any]] = []
        if turn.text:
            assistant_content.append({"type": "text", "text": turn.text})
        for tc in turn.tool_calls:
            assistant_content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
            )
        messages.append({"role": "assistant", "content": assistant_content})

        if not turn.tool_calls:
            break  # model produced a final answer

        results: list[dict[str, Any]] = []
        for tc in turn.tool_calls:
            transcript.append(f"→ {tc.name}({tc.input})")
            content, is_error = dispatch(ctx, tc)
            clipped = content if len(content) <= _TRANSCRIPT_CLIP else content[:_TRANSCRIPT_CLIP] + " …[clipped]"
            transcript.append(f"  {'ERROR' if is_error else 'result'}: {clipped}")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    diff = ctx.working.full_diff()
    if diff:
        # V2: verified-before-seen is V3, so evidence is null (UI shows unverified).
        return {
            "outcome": "pr_proposed",
            "diff": diff,
            "explanation": last_text or "Drafted a minimal fix for the failing model.",
            "transcript": transcript,
            "evidence": None,
            "confidence": None,
            "risk_class": None,
        }
    return {
        "outcome": "no_fix",
        "explanation": last_text or "Could not produce a confident fix.",
        "transcript": transcript,
        "evidence": None,
    }
