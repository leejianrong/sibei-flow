"""N6 bounded agent loop: read → draft → edit → guard → emit.

Behind an :class:`LlmProvider` (ADR-0007). Bounded at ``max_turns`` provider
turns (R3.3 — clean give-up, no runaway). V2 does **no** sandbox verification,
so a drafted fix is emitted as ``pr_proposed`` with ``evidence = null`` (the UI
labels it *unverified* until V3).
"""

from __future__ import annotations

import re
from typing import Any

from ..llm.base import LlmProvider, ToolSpec
from .diffing import changed_line_count
from .prodaction import drift_requires_prod_action
from .score import ScoreSignals, score
from .tools import TOOL_SPECS, AgentContext, dispatch

#: First `source('schema', 'table')` reference in a model — the upstream whose
#: current columns tell a removal from a rename in the `needs_prod_action` rule.
_SOURCE_REF_RE = re.compile(
    r"""source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""", re.IGNORECASE
)

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
        {
            "role": "user",
            "content": [{"type": "text", "text": build_initial_prompt(task)}],
        }
    ]
    transcript: list[str] = []
    last_text = ""
    edit_attempts = 0

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
            if tc.name == "edit_file":
                edit_attempts += 1
            transcript.append(f"→ {tc.name}({tc.input})")
            content, is_error = dispatch(ctx, tc)
            clipped = (
                content
                if len(content) <= _TRANSCRIPT_CLIP
                else content[:_TRANSCRIPT_CLIP] + " …[clipped]"
            )
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

    # N13 `needs_prod_action` gate (V5): before proposing ANY fix, check whether
    # the drift is one that must not be healed by a silent code edit — an
    # incremental model hit by a column removal/retype. If so, emit a
    # recommendation and drop any drafted diff: the fix is never proposed as a
    # PR, and the system never assumes a prod migration happened.
    recommendation = _detect_prod_action(ctx, task)
    if recommendation is not None:
        transcript.append(
            "needs_prod_action: incremental model + non-rename drift → "
            "recommending a prod action instead of a code fix"
        )
        return {
            "outcome": "needs_prod_action",
            "explanation": recommendation,
            "transcript": transcript,
            "evidence": None,
        }

    diff = ctx.working.full_diff()
    if not diff:
        return {
            "outcome": "no_fix",
            "explanation": last_text or "Could not produce a confident fix.",
            "transcript": transcript,
            "evidence": None,
        }

    explanation = last_text or "Drafted a minimal fix for the failing model."

    # V2 behaviour: no sandbox configured → emit an *unverified* draft (evidence
    # null, UI shows the unverified badge).
    if ctx.sandbox is None:
        return {
            "outcome": "pr_proposed",
            "diff": diff,
            "explanation": explanation,
            "transcript": transcript,
            "evidence": None,
            "confidence": None,
            "risk_class": None,
        }

    # V3: verify the drafted diff (reusing the model's `run_sandbox` result when
    # the diff is unchanged), then apply the compile gate + score.
    return _verify_and_gate(ctx, diff, explanation, transcript, edit_attempts)


def _detect_prod_action(ctx: AgentContext, task: dict[str, Any]) -> str | None:
    """Return a prod-action recommendation for this drift, or None.

    Reads the failing model source (read-only) to test for `incremental`, and —
    for a missing-column drift — the current upstream columns (read-only
    warehouse) to tell a removal from a rename. Any lookup failure degrades to
    "no recommendation" (the normal fix path, whose compile gate still protects).
    """
    failing_file = task.get("failing_file")
    if not failing_file:
        return None
    try:
        model_source = ctx.source.read(failing_file)
    except (FileNotFoundError, ValueError, OSError):
        return None

    error_text = task.get("error_text") or ""
    node_uid = task.get("node_uid") or ""
    current_columns = _current_upstream_columns(ctx, model_source)
    return drift_requires_prod_action(
        model_source=model_source,
        error_text=error_text,
        node_uid=node_uid,
        current_columns=current_columns,
    )


def _current_upstream_columns(ctx: AgentContext, model_source: str) -> list[str] | None:
    """Current columns of the model's first `source()` (read-only), or None."""
    if ctx.warehouse is None:
        return None
    m = _SOURCE_REF_RE.search(model_source)
    if not m:
        return None
    source_name = f"{m.group(1)}.{m.group(2)}"
    try:
        return ctx.warehouse.column_names(source_name)
    except Exception:  # warehouse unreachable / table gone — leave it to the fix path
        return None


def _verify_and_gate(
    ctx: AgentContext,
    diff: str,
    explanation: str,
    transcript: list[str],
    edit_attempts: int,
) -> dict[str, Any]:
    from ..sandbox.evidence import build_evidence

    model_select = ctx.model_select or ""
    model_path = next(iter(ctx.working.changed_paths()), "")

    if ctx.last_run is not None and ctx.last_verified_diff == diff:
        run = ctx.last_run  # reuse the model's run_sandbox result (no second run)
    else:
        run = ctx.verify_current(model_select)
        transcript.append(f"→ run_sandbox (compile gate) on '{model_select}'")

    evidence = build_evidence(run, ctx.working, model_path)
    files_touched = len(ctx.working.changed_paths())
    changed_lines = changed_line_count(diff)
    signals = ScoreSignals(
        tier1_passed=bool(run.tier1.passed),
        tier2_passed=run.tier2.passed if run.tier2.ran else None,
        output_schema_unchanged=evidence["output_schema"]["changed"] is False
        if evidence["output_schema"]["changed"] is not None
        else None,
        changed_lines=changed_lines,
        files_touched=files_touched,
        unambiguous=(files_touched <= 1 and changed_lines <= 15),
        attempts=max(edit_attempts, 1),
    )
    scored = score(signals)

    # Compile gate (B-S2-Q4): a draft that does not pass tier-1 is structurally
    # suppressed — never reaches the review queue (PRD story 17).
    if not run.tier1.passed:
        transcript.append("compile gate: tier-1 failed → suppressing to no_fix")
        return {
            "outcome": "no_fix",
            "explanation": (
                explanation
                + "\n\nThis draft did not pass tier-1 compile, so it was not proposed."
            ),
            "transcript": transcript,
            "evidence": evidence,
            "confidence": scored["confidence"],
            "risk_class": scored["risk_class"],
            "factors": scored["factors"],
        }

    return {
        "outcome": "pr_proposed",
        "diff": diff,
        "explanation": explanation,
        "transcript": transcript,
        "evidence": evidence,
        "confidence": scored["confidence"],
        "risk_class": scored["risk_class"],
        "factors": scored["factors"],
    }
