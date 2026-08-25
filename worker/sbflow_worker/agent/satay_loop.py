"""Satay-workflow-shaped port of `run_repair` (KAN-648, ADR-0012 decision 4, slice 1).

**This is slice 1 only** — the foundation, not the multi-candidate repair the ADR
names as the actual trigger for the port. It exists so a later slice can grow
`satay.map(..., return_exceptions=True)` fan-out (ADR-0027's collect mode) on top of
something already Satay-shaped, without having ported the single-candidate loop under
time pressure at that point too.

Behind `SBFLOW_SATAY_LOOP` (default OFF, see `Config.satay_loop_enabled`):

- **OFF (default): nothing changes.** `build_processor` calls `run_repair` (`loop.py`)
  exactly as before. This module is not even imported.
- **ON:** `build_processor` calls `run_repair_satay` below instead, which drives the
  *same* single-candidate loop through a `@satay.workflow`, with `provider.complete`,
  each tool `dispatch`, and the terminal `ctx.verify_current` sandbox run recorded as
  `@satay.task` durable calls against a private, throwaway, in-memory journal.

That journal is purely an execution/observability seam for this slice — it is never
persisted, never written into `repair_jobs`, and nothing here touches the lease/claim
loop, brain reconcile, the orphan sweep, or `LISTEN/NOTIFY` (ADR-0012's capability
freeze). `RepairResult.transcript` keeps emitting `{"kind": "lines", ...}` (ADR-0013)
exactly as `run_repair` does — the `journal` arm is separate follow-on work.

**Provably a port, not a rewrite:** `run_repair_satay` must return a behaviorally
identical `RepairResult` to `run_repair` for the same inputs — see
`tests/test_satay_loop.py`, which runs the existing agent-loop and sandbox fixtures
through both paths and asserts equal output.

**Why `provider`/`ctx` are not durable-call arguments.** A task's arguments are
recorded to the journal via `satay.journal.codec.encode`, which accepts JSON-native
values, dataclasses, enums, and datetimes — not live resources like an `LlmProvider`
(a network client) or an `AgentContext` (holds a Docker-backed sandbox runner, a
warehouse connection, and an in-memory working copy). satay-runtime's own examples
keep exactly this kind of live resource out of durable-call arguments and behind
module/closure state instead (see `examples/elt_pipeline_demo.py`'s `PATHS`/`MODEL`
globals, swapped once before the run starts). This module does the same with a
`ContextVar`-scoped "rig", set once per `run_repair_satay` call.

**Nondeterminism.** The workflow body below must not read a clock, env var, or RNG
directly (Satay's replay is strict about this by default — ADR-0003/ADR-0022). It
doesn't: `max_turns` arrives as a plain `int` in the workflow's own input (recorded on
`WorkflowCreated`, like `checkout(cents)` in the quickstart), and the loop bound
(`for _ in range(max_turns)`) reads nothing external. The one call this slice
deliberately leaves as-is is `_detect_prod_action(ctx, task)`: it does real file and
warehouse I/O directly in the workflow body rather than behind a task, exactly as
`run_repair` does. Wrapping it too would be the more "correct" port; the ticket scoped
this slice to the three call sites named above (`provider.complete`, tool `dispatch`,
`ctx.verify_current`), so it is flagged here — and in the PR — as a known gap for a
follow-on slice rather than folded in silently. It does not affect parity: every
scenario `test_satay_loop.py` exercises resolves it to `None` on both paths.
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import satay

from ..llm.base import AssistantTurn, LlmProvider, ToolCall, ToolSpec
from .loop import (
    SYSTEM_PROMPT,
    _TRANSCRIPT_CLIP,
    _detect_prod_action,
    build_initial_prompt,
    lines_transcript,
)
from .tools import TOOL_SPECS, AgentContext, dispatch

if TYPE_CHECKING:
    from ..sandbox.runner import SandboxRun


@dataclass
class _Rig:
    """The live, non-journal-safe resources this job's tasks read via a `ContextVar`.

    Never passed as a task argument — see the module docstring on why.
    """

    provider: LlmProvider
    ctx: AgentContext


#: Scoped for the duration of one `run_repair_satay` call. The claim loop
#: (`claim.py`, left alone per ADR-0012) processes one job at a time, synchronously,
#: so there is never more than one rig live at once.
_RIG: contextvars.ContextVar["_Rig | None"] = contextvars.ContextVar(
    "_sbflow_satay_rig", default=None
)


def _rig() -> _Rig:
    rig = _RIG.get()
    if rig is None:  # pragma: no cover - defensive; only reachable via a bug here
        raise RuntimeError(
            "satay task called outside run_repair_satay's drive (no rig bound)"
        )
    return rig


# --- the durable calls (N6 turns → @satay.task) -----------------------------------


@satay.task()
async def _complete(
    system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
) -> AssistantTurn:
    """Durable-call port of `provider.complete(...)` — one model turn.

    `LlmProvider.complete` stays synchronous (ADR-0007's interface is unchanged; the
    `replay` provider in particular must keep working unmodified for existing tests).
    This task just calls it inline from an `async def` body — it blocks the event loop
    for the call's duration, same as the sync loop blocked the claim loop's thread for
    it. That is fine as long as nothing else is running concurrently on this loop,
    which is true for a single-candidate run; it stops being fine the moment slice 2
    adds concurrent fan-out over candidates, at which point this needs
    `asyncio.to_thread` (or a real async provider) so one candidate's model call
    doesn't stall the others. Flagged in the PR as a slice-2 prerequisite.

    No `retries=` here: the sync loop never retried a completion either, so a raised
    exception is the identical failure mode (modulo satay wrapping it in
    `TaskFailedError` — see the module docstring's "known gaps" note in the PR body).
    """
    rig = _rig()
    return rig.provider.complete(system, messages, tools)


@satay.task()
async def _dispatch(call: ToolCall) -> dict[str, Any]:
    """Durable-call port of one `tools.py::dispatch` invocation.

    `dispatch` already catches every exception internally and returns
    `(content, is_error)` rather than raising (see `tools.py`), so — like `_complete`
    above — no `retries=` is needed to match the sync loop's behaviour.
    """
    rig = _rig()
    content, is_error = dispatch(rig.ctx, call)
    return {"content": content, "is_error": is_error}


@satay.task(side_effect=True)
async def _verify(model_select: str) -> "SandboxRun":
    """Durable-call port of `ctx.verify_current(...)` — a real `docker run` subprocess.

    `side_effect=True` is satay's declaration for exactly this: a task that touches the
    outside world (ADR-0006/A10.2). `retries` stays at the default 0, matching the sync
    loop (which never retries a sandbox run), so satay's "a *retryable* side-effecting
    task must declare `idempotent=True`" check does not apply here — it only fires for
    `retries > 0` (see `satay.replay.engine._enforce_effect_safety`).
    """
    rig = _rig()
    return rig.ctx.verify_current(model_select)


# --- the workflow (N6 bounded loop → @satay.workflow) -----------------------------


@satay.workflow
async def _repair_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """Satay-workflow port of `loop.py::run_repair`'s body. Line-for-line the same
    control flow; only the three call sites named in the module docstring go through
    `await _complete/_dispatch/_verify(...)` instead of a direct synchronous call.
    """
    task: dict[str, Any] = payload["task"]
    max_turns: int = payload["max_turns"]
    tools: list[ToolSpec] = payload["tools"]

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
        turn = await _complete(SYSTEM_PROMPT, messages, tools)
        if turn.text:
            last_text = turn.text
            transcript.append(f"assistant: {turn.text}")

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
            outcome = await _dispatch(tc)
            content, is_error = outcome["content"], outcome["is_error"]
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

    rig = _rig()
    ctx = rig.ctx

    # Same N13 needs_prod_action gate as run_repair. Deliberately still a direct,
    # synchronous call — see the module docstring.
    recommendation = _detect_prod_action(ctx, task)
    if recommendation is not None:
        transcript.append(
            "needs_prod_action: incremental model + non-rename drift → "
            "recommending a prod action instead of a code fix"
        )
        return {
            "outcome": "needs_prod_action",
            "explanation": recommendation,
            "transcript": lines_transcript(transcript),
            "evidence": None,
        }

    diff = ctx.working.full_diff()
    if not diff:
        return {
            "outcome": "no_fix",
            "explanation": last_text or "Could not produce a confident fix.",
            "transcript": lines_transcript(transcript),
            "evidence": None,
        }

    explanation = last_text or "Drafted a minimal fix for the failing model."

    if ctx.sandbox is None:
        return {
            "outcome": "pr_proposed",
            "diff": diff,
            "explanation": explanation,
            "transcript": lines_transcript(transcript),
            "evidence": None,
            "confidence": None,
            "risk_class": None,
        }

    return await _verify_and_gate(ctx, diff, explanation, transcript, edit_attempts)


async def _verify_and_gate(
    ctx: AgentContext,
    diff: str,
    explanation: str,
    transcript: list[str],
    edit_attempts: int,
) -> dict[str, Any]:
    """Durable-call port of `loop.py::_verify_and_gate` — identical logic, one line
    (the sandbox run) going through `await _verify(...)` instead of
    `ctx.verify_current(...)` directly.
    """
    from ..sandbox.evidence import build_evidence
    from .diffing import changed_line_count
    from .score import ScoreSignals, score

    model_select = ctx.model_select or ""
    model_path = next(iter(ctx.working.changed_paths()), "")

    if ctx.last_run is not None and ctx.last_verified_diff == diff:
        run = ctx.last_run  # reuse the model's run_sandbox result (no second run)
    else:
        run = await _verify(model_select)
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

    if not run.tier1.passed:
        transcript.append("compile gate: tier-1 failed → suppressing to no_fix")
        return {
            "outcome": "no_fix",
            "explanation": (
                explanation
                + "\n\nThis draft did not pass tier-1 compile, so it was not proposed."
            ),
            "transcript": lines_transcript(transcript),
            "evidence": evidence,
            "confidence": scored["confidence"],
            "risk_class": scored["risk_class"],
            "factors": scored["factors"],
        }

    return {
        "outcome": "pr_proposed",
        "diff": diff,
        "explanation": explanation,
        "transcript": lines_transcript(transcript),
        "evidence": evidence,
        "confidence": scored["confidence"],
        "risk_class": scored["risk_class"],
        "factors": scored["factors"],
    }


# --- the entry point ---------------------------------------------------------------


def run_repair_satay(
    provider: LlmProvider,
    ctx: AgentContext,
    task: dict[str, Any],
    max_turns: int,
    tools: list[ToolSpec] | None = None,
) -> dict[str, Any]:
    """Drive `_repair_workflow` for one job through Satay, synchronously.

    Called by `build_processor` in place of `run_repair` when
    `Config.satay_loop_enabled` is on. Opens a private in-memory journal
    (`SQLiteStore.open(":memory:")`) for this one job only — nothing here is
    persisted, resumed, or shared across jobs, so this slice makes no claim about
    crash-resume through Satay; V5's job-level lease re-claim (`claim.py`, untouched)
    is still what owns recovery, per ADR-0012.
    """
    from satay.journal.store import SQLiteStore

    tools = tools or TOOL_SPECS
    token = _RIG.set(_Rig(provider=provider, ctx=ctx))
    store = SQLiteStore.open(":memory:")
    try:
        handle = satay.start(
            _repair_workflow,
            {"task": task, "max_turns": max_turns, "tools": tools},
            store=store,
        )
        return asyncio.run(handle.result())
    finally:
        store.close()
        _RIG.reset(token)
