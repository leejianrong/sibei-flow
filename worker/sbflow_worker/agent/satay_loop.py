"""Satay-workflow-shaped port of `run_repair` (KAN-648, ADR-0012 decision 4).

**Slice 1** ported the single-candidate loop onto a `@satay.workflow` behind
`SBFLOW_SATAY_LOOP` (default OFF). **Slice 2** (this file, the rest of it) adds the
ADR's actual trigger — multi-candidate repair ("draft three candidate fixes, keep the
one with the best evidence") — via collect-mode fan-out (ADR-0027), behind a second,
independently-gated knob: `SBFLOW_SATAY_CANDIDATES` (see `Config.satay_candidates`,
default `1` == today's single-candidate behaviour, byte-for-byte; only consulted at
all when `SBFLOW_SATAY_LOOP` is also on).

Behind `SBFLOW_SATAY_LOOP` (default OFF, see `Config.satay_loop_enabled`):

- **OFF (default): nothing changes.** `build_processor` calls `run_repair` (`loop.py`)
  exactly as before. This module is not even imported.
- **ON, `satay_candidates <= 1`:** `build_processor` calls `run_repair_satay` below,
  which drives the *same* single-candidate loop through a `@satay.workflow`, with
  `provider.complete`, each tool `dispatch`, and the terminal `ctx.verify_current`
  sandbox run recorded as `@satay.task` durable calls against a private, throwaway,
  in-memory journal.
- **ON, `satay_candidates > 1`:** `build_processor` calls `run_repair_satay_candidates`
  below, which starts N independent candidates as `@satay.workflow` **children**
  (`_candidate_workflow`, one journal each) via `satay.start_child`, fans them out with
  `satay.gather(*children, return_exceptions=True)` (collect mode, ADR-0027), and
  judges the survivors — see `_judge` below and the "Architectural choice" note.

**KAN-649 update (ADR-0012 decision 3, ADR-0013's `journal` arm):** the journal is no
longer purely in-memory/throwaway. Both entry points now open a *persistent*
`SQLiteStore` at a path derived from `Config.satay_journal_dir` (default
`/var/lib/sbflow/satay/satay.db`, one shared file for the whole worker process — see
that field's docstring for why one file rather than one per job), and
`RepairResult.transcript` is populated as `{"kind": "journal", "run_id": ..., "ref": ...}`
instead of the hand-built `{"kind": "lines", ...}` this module used to build itself (via
`lines_transcript(...)`, imported from `loop.py`). `_run_candidate`/`_verify_and_gate`
below therefore no longer build a `transcript: list[str]` at all — the journal already
records everything a hand-built line log used to approximate (every `_complete`/
`_dispatch`/`_verify` call, its input/output, attempt/retry info), so a second,
hand-maintained description of the same run is exactly the drift risk ADR-0013's
"Context" section names. `run_id` in the emitted transcript is always the run whose
OWN journal actually produced the shipped `RepairResult` — `handle.run_id` for N=1, and
the *winning candidate's own child run_id* (not the parent fan-out workflow's run_id,
never a losing candidate's) for N>1 — resolved from the parent's own
`ChildWorkflowScheduled` events after the drive completes (see `run_repair_satay_candidates`).
`ref` is the journal file's basename (e.g. `"satay.db"`): `brain/src/pr/body.rs` already
knows the shared directory from its own `SBFLOW_SATAY_JOURNAL_DIR`, so `ref` only needs
to name *which file inside it*, not repeat the directory.

This still does not touch `repair_jobs`, the lease/claim loop, brain reconcile, the
orphan sweep, or `LISTEN/NOTIFY` (ADR-0012's capability freeze) — the journal file is a
new *artifact on disk*, not new durable state sibei-flow's own claim loop depends on;
crash-resume still runs entirely through V5's job-level lease re-claim, unchanged. The
final return value is still one `RepairResult`-shaped dict, whether N is 1 or 30 — the
frozen contract does not grow a "candidates" field.

**Provably a port, not a rewrite:** `run_repair_satay` must return a behaviorally
identical `RepairResult` to `run_repair` for the same inputs — see
`tests/test_satay_loop.py`, which runs the existing agent-loop and sandbox fixtures
through both paths and asserts equal output. `run_repair_satay_candidates` with
`n_candidates=1` degenerates to calling `run_repair_satay` directly (same code path,
not a re-implementation that merely produces equal output) — see
`tests/test_satay_loop_candidates.py`.

**Architectural choice: `start_child` + `gather`, not `satay.map`.** One "candidate"
here is not a single model call — it's the whole bounded multi-turn drafting loop plus
its own sandbox verify (`_run_candidate` below): several `_complete`/`_dispatch` turns,
then `_verify_and_gate`. That is workflow-shaped work, not a single task, and
`satay.map`'s `_resolve_task` (`satay/api/primitives.py`) raises `TypeError` on
anything that isn't `@satay.task`-decorated — it does not accept a `@satay.workflow`.
`satay.gather`, by contrast, explicitly "awaits heterogeneous durable calls together —
task calls, nested map calls, and `start_child` calls (whose returned handle is
resolved to the child's result)" (same file). Each candidate is therefore its own
`@satay.workflow` (`_candidate_workflow`), started per-candidate via
`satay.start_child(...)` — passing the **coroutine returned by calling `start_child`
without awaiting it** straight into `gather`, not a pre-awaited handle. That distinction
matters: `durable_child` (what `start_child` calls) *drives the child to completion
before returning*, and raises `WorkflowFailedError` immediately on a failed child
(`satay/replay/engine.py::durable_child`) — fail-fast, unconditionally, at the point of
the call. Awaiting each `start_child(...)` in a loop before gathering would (a) run the
candidates **sequentially**, one full drafting-loop-plus-verify at a time, defeating the
entire point of fan-out, and (b) raise on the first failing candidate before the rest
even started. Passing the unawaited coroutines into `gather(*coros,
return_exceptions=True)` lets `_spawn_members` schedule all N as concurrent
`asyncio.Task`s up front (`asyncio.create_task` per member) and lets `_settle_composite`
catch each child's `WorkflowFailedError` into its result slot instead of propagating —
genuine concurrent collect-mode fan-out over workflow-shaped children, which is exactly
what the ADR-0012 decision-4 trigger asks for and what satay's actual (not speculative)
API supports.

**Why `provider`/`ctx` are not durable-call arguments.** A task's arguments are
recorded to the journal via `satay.journal.codec.encode`, which accepts JSON-native
values, dataclasses, enums, and datetimes — not live resources like an `LlmProvider`
(a network client) or an `AgentContext` (holds a Docker-backed sandbox runner, a
warehouse connection, and an in-memory working copy). satay-runtime's own examples
keep exactly this kind of live resource out of durable-call arguments and behind
module/closure state instead (see `examples/elt_pipeline_demo.py`'s `PATHS`/`MODEL`
globals, swapped once before the run starts). This module does the same with a
`ContextVar`-scoped "rig", set once per `run_repair_satay` call (slice 1) — or, for
N>1, once *per candidate* by `_candidate_workflow` itself, reading its own rig out of
a second, plural `ContextVar` (`_RIGS`, a `{candidate_key: _Rig}` map) set once, up
front, by `run_repair_satay_candidates` before any candidate starts. Because
`contextvars.ContextVar.set()` only affects the current `asyncio.Task`'s own context
(and whatever it spawns afterwards) — never sibling Tasks, which each got their own
*copy* of the context at creation — N candidates running concurrently on N separate
`asyncio.Task`s each see their own `_RIG` value without needing a lock: candidate A
setting its rig can never leak into candidate B's context, no matter how the two
interleave on the event loop.

**Each candidate needs its own `AgentContext` (and its own `LlmProvider`).**
`AgentContext.working` is a mutable in-memory `WorkingCopy` that every `edit_file`
tool call mutates in place, and `ctx.last_run`/`ctx.last_verified_diff` are a
single-slot cache — sharing one `AgentContext` across concurrent candidates would let
them stomp each other's drafted edits. `get_provider`'s own docstring already flags the
`replay` provider as stateful ("fresh instance per repair is important... it consumes
scripted turns in order") — that generalizes to "fresh instance per **candidate**" once
candidates run concurrently, so `run_repair_satay_candidates` takes a
`provider_factory` and calls it once per candidate rather than reusing one instance.
`_clone_ctx_for_candidate` below builds each candidate's own `AgentContext` sharing the
job's read-only/stateless resources — confirmed safe to share by reading their
implementations: `LocalSourceProvider.read` is a pure `Path.read_text()` with no shared
mutable state; `WarehouseSchema.describe`/`column_names` open a **new** `psycopg.connect`
per call (no shared connection); `DiffGuard` holds only an int config field, no mutable
state — while giving each candidate its own `working`/`last_run`/`last_verified_diff`.

**Correction (found by CI, not by reading the code — see PR #56's review thread):**
an earlier draft of this docstring claimed `SandboxRunner` was safe to *share* across
concurrent candidates too, on the reasoning that "`verify` materializes each run under
a fresh `uuid.uuid4()` work directory, so concurrent `docker run`s never collide on
disk." That is true, and also **not the whole story**: the *local* `/tmp` work
directory is per-call-unique, but the *warehouse-side* tier-2 materialization target
was not — every `SandboxRunner` used the same hardcoded `sample_schema="sbflow_sample"`
(and every `dbt build` in it targets the same model name, e.g. `orders`), regardless of
which job or candidate was verifying. Under N=1 this was never reachable (the claim
loop drives one job at a time, and one job only ever ran one verification at once), so
it looked safe by inspection. Under real N>1 concurrency it is not: two concurrent
`dbt build --select orders` runs against the same `sbflow_sample.orders` view race on
dbt's create-or-replace backup-swap and one fails with
`relation "orders__dbt_backup" already exists` — reproduced directly (not merely
suspected) by running `run_repair_satay_candidates` against the real fixture warehouse
in a loop; roughly 3 of 8 iterations hit it. The fix: `SandboxRunner` gained a
`sample_schema` field (default `"sbflow_sample"`, unchanged for every existing
single-candidate caller) and `_clone_ctx_for_candidate` now clones the `SandboxRunner`
too, giving each candidate a schema suffixed with its own `candidate_key`
(`sbflow_sample_c0`, `sbflow_sample_c1`, ...) so concurrent tier-2 builds never target
the same relation. `sbflow_dev` already holds `CREATE ON DATABASE` (`db/warehouse/
init.sql`), so dbt auto-creates each schema on first use — no warehouse-fixture change
needed. `verify_schema` (tier-1's compile-only target) got the same per-candidate
treatment for symmetry, though `dbt compile` does not materialize anything so it was
never actually reachable by this race. **The general lesson, not just this one field:**
"per-call-unique local directory" and "per-call-unique remote state" are two different
claims, and sharing a resource across concurrent callers requires checking both — this
is exactly the kind of hole `_clone_ctx_for_candidate`'s per-candidate cloning exists
to close, and this one slipped through the first pass because the checked half (disk)
was real and the unchecked half (the warehouse) wasn't obviously a shared resource from
reading `SandboxRunner.verify`'s signature alone.

**`asyncio.to_thread` on the three durable-call bodies.** Slice 1 flagged
`provider.complete` (in `_complete`) as fine for one candidate but unsafe the moment
concurrent fan-out lands, because it "blocks the event loop for the call's duration...
[which] stops being fine the moment slice 2 adds concurrent fan-out over candidates."
This slice wraps that call site in `asyncio.to_thread` as flagged. The same reasoning
applies equally to `_dispatch` (whose `run_sandbox` tool call reaches
`ctx.verify_current`, a real `docker run` subprocess) and to `_verify` itself (the
terminal compile gate's own `docker run`) — leaving either as a plain blocking call
would silently serialize N candidates' sandbox verification on the single event-loop
thread even though their `_complete` calls run concurrently, which defeats collect-mode
fan-out for the expensive half of the work. All three are therefore wrapped. This is
safe for every provider today: `ReplayProvider.complete` is a pure read of its own
`self._turns[self._i]` plus an `int` increment — no shared state once each candidate
holds its own instance (see above) — and `ClaudeProvider`/`OpenAICompatProvider` each
construct their own `anthropic.Anthropic()`/`openai.OpenAI()` client in `__init__`,
never shared across instances either. `LlmProvider.complete`'s public interface stays
synchronous (ADR-0007 unchanged) — only the call site changed.

**Nondeterminism.** The workflow body below must not read a clock, env var, or RNG
directly (Satay's replay is strict about this by default — ADR-0003/ADR-0022). It
doesn't: `max_turns` arrives as a plain `int` in the workflow's own input (recorded on
`WorkflowCreated`, like `checkout(cents)` in the quickstart), and the loop bound
(`for _ in range(max_turns)`) reads nothing external. `_candidate_workflow`'s per-
candidate identity (`candidate_key`) is likewise a plain string threaded through the
recorded input, mirroring `best_of_n_demo.py`'s `Candidate.strategy` — never read live
from an env var or a clock. (Today's `LlmProvider.complete` interface has no
temperature/strategy knob to vary per candidate — ADR-0007 keeps it frozen at
`complete(system, messages, tools)` — so per-candidate *behavioural* variation, when
using a live model, comes from the model's own inherent sampling variance across
independent calls; the `replay` provider gets its variation from tests handing each
candidate a distinct scripted session via `provider_factory`.) The one call this slice
deliberately leaves as-is is `_detect_prod_action(ctx, task)`: it does real file and
warehouse I/O directly in the workflow body rather than behind a task, exactly as
`run_repair` does. Wrapping it too would be the more "correct" port; slice 1 scoped
itself to the three call sites named above (`provider.complete`, tool `dispatch`,
`ctx.verify_current`), so it is flagged here — and in the PR — as a known gap for a
follow-on slice rather than folded in silently. It does not affect parity: every
scenario `test_satay_loop.py`/`test_satay_loop_candidates.py` exercises resolves it to
`None` on both paths. Because it is drift-detection over the failing model/task, not
candidate-dependent, `_judge` below never needs to reconcile *different*
`needs_prod_action` verdicts across candidates in practice — see `_judge`'s docstring.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import satay

from ..llm.base import AssistantTurn, LlmProvider, ToolCall, ToolSpec
from .diffing import WorkingCopy
from .loop import SYSTEM_PROMPT, _detect_prod_action, build_initial_prompt
from .tools import TOOL_SPECS, AgentContext, dispatch

if TYPE_CHECKING:
    from satay.journal.store import SQLiteStore

    from ..sandbox.runner import SandboxRun

#: Default persistent-journal filename inside `Config.satay_journal_dir` (KAN-649).
#: Callers normally pass an explicit `journal_path` derived from `Config`; this is
#: only the fallback for direct callers (tests, a REPL) that don't build one.
_DEFAULT_JOURNAL_DIR = "/var/lib/sbflow/satay"
JOURNAL_DB_NAME = "satay.db"


def _default_journal_path() -> Path:
    import os

    base = os.environ.get("SBFLOW_SATAY_JOURNAL_DIR", _DEFAULT_JOURNAL_DIR)
    return Path(base) / JOURNAL_DB_NAME


@dataclass
class _Rig:
    """The live, non-journal-safe resources this job's tasks read via a `ContextVar`.

    Never passed as a task argument — see the module docstring on why.
    """

    provider: LlmProvider
    ctx: AgentContext


#: Scoped for the duration of one `run_repair_satay` call (N=1), OR — for N>1 — set
#: locally by `_candidate_workflow` itself for the duration of one candidate's own
#: `asyncio.Task`. The claim loop (`claim.py`, left alone per ADR-0012) processes one
#: job at a time, synchronously, so there is never more than one rig live *per running
#: candidate*; N>1 candidates run concurrently on N separate Tasks, each with its own
#: `_RIG.set()` and therefore its own isolated view of this ContextVar (see the module
#: docstring's "Why provider/ctx are not durable-call arguments" section).
_RIG: contextvars.ContextVar["_Rig | None"] = contextvars.ContextVar(
    "_sbflow_satay_rig", default=None
)

#: Set once, up front, by `run_repair_satay_candidates` (N>1 only) — a read-only map
#: every candidate's own `asyncio.Task` can see (contextvars propagate down through the
#: whole Task tree from wherever they were set). `_candidate_workflow` reads its own
#: entry out of this map and sets `_RIG` from it. Never mutated after being set; the
#: candidates it maps to are themselves the mutable per-candidate state.
_RIGS: contextvars.ContextVar["dict[str, _Rig] | None"] = contextvars.ContextVar(
    "_sbflow_satay_rigs", default=None
)


def _rig() -> _Rig:
    rig = _RIG.get()
    if rig is None:  # pragma: no cover - defensive; only reachable via a bug here
        raise RuntimeError(
            "satay task called outside run_repair_satay's drive (no rig bound)"
        )
    return rig


def _rig_for_candidate(candidate_key: str) -> _Rig:
    rigs = _RIGS.get()
    if rigs is None or candidate_key not in rigs:  # pragma: no cover - defensive
        raise RuntimeError(
            f"candidate {candidate_key!r} started outside "
            "run_repair_satay_candidates's drive (no rig bound)"
        )
    return rigs[candidate_key]


# --- the durable calls (N6 turns → @satay.task) -----------------------------------


@satay.task()
async def _complete(
    system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
) -> AssistantTurn:
    """Durable-call port of `provider.complete(...)` — one model turn.

    `LlmProvider.complete` stays synchronous (ADR-0007's interface is unchanged; the
    `replay` provider in particular must keep working unmodified for existing tests).
    Slice 1 called it inline from this `async def` body, which blocks the event loop
    for the call's duration — fine for a single-candidate run, but not once slice 2
    (this file) adds concurrent fan-out over candidates: an inline blocking call here
    would stall every other candidate's model turn for as long as this one takes. Slice
    2 therefore wraps the call in `asyncio.to_thread`, confirmed safe by the module
    docstring's "asyncio.to_thread on the three durable-call bodies" section (no shared
    provider state across candidates, no un-picklable/thread-unsafe local state in any
    of the three providers).

    No `retries=` here: the sync loop never retried a completion either, so a raised
    exception is the identical failure mode (modulo satay wrapping it in
    `TaskFailedError` — see the module docstring's "known gaps" note in the PR body).
    """
    rig = _rig()
    return await asyncio.to_thread(rig.provider.complete, system, messages, tools)


@satay.task()
async def _dispatch(call: ToolCall) -> dict[str, Any]:
    """Durable-call port of one `tools.py::dispatch` invocation.

    `dispatch` already catches every exception internally and returns
    `(content, is_error)` rather than raising (see `tools.py`), so — like `_complete`
    above — no `retries=` is needed to match the sync loop's behaviour. Wrapped in
    `asyncio.to_thread` for the same reason as `_complete`: the `run_sandbox` tool call
    reaches `ctx.verify_current`, a real blocking `docker run` subprocess, which must
    not stall sibling candidates under concurrent fan-out.
    """
    rig = _rig()
    content, is_error = await asyncio.to_thread(dispatch, rig.ctx, call)
    return {"content": content, "is_error": is_error}


@satay.task(side_effect=True)
async def _verify(model_select: str) -> "SandboxRun":
    """Durable-call port of `ctx.verify_current(...)` — a real `docker run` subprocess.

    `side_effect=True` is satay's declaration for exactly this: a task that touches the
    outside world (ADR-0006/A10.2). `retries` stays at the default 0, matching the sync
    loop (which never retries a sandbox run), so satay's "a *retryable* side-effecting
    task must declare `idempotent=True`" check does not apply here — it only fires for
    `retries > 0` (see `satay.replay.engine._enforce_effect_safety`). Wrapped in
    `asyncio.to_thread` for the same reason as `_complete`/`_dispatch`: this is the
    terminal compile gate's own blocking `docker run`, and N candidates' sandbox
    verifications must be able to run concurrently, not queue behind each other on the
    single event-loop thread.
    """
    rig = _rig()
    return await asyncio.to_thread(rig.ctx.verify_current, model_select)


# --- the per-candidate body (N6 bounded loop, shared by N=1 and N>1) --------------


@satay.workflow
async def _repair_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """The N=1 top-level workflow — `run_repair_satay`'s drive target.

    Thin wrapper: all control flow lives in `_run_candidate` below, shared verbatim
    with the N>1 path's `_candidate_workflow` (KAN-648 slice 2 refactor — the ticket's
    explicit instruction is to reuse the turn loop, not duplicate it). Kept as its own
    named `@satay.workflow` for slice-1 parity: `test_satay_loop.py`'s scenarios drive
    `run_repair_satay` exactly as before, byte-for-byte.
    """
    return await _run_candidate(payload)


async def _run_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    """Satay-workflow-shaped port of `loop.py::run_repair`'s body — one candidate's
    full drafting-loop-then-verify. Line-for-line the same control flow as slice 1's
    `_repair_workflow`; only the three call sites named in the module docstring go
    through `await _complete/_dispatch/_verify(...)` instead of a direct synchronous
    call. Called from both `_repair_workflow` (N=1) and `_candidate_workflow` (N>1) —
    neither wraps it in anything durable of its own, so it stays a plain `async def`,
    not a `@satay.workflow` itself (a workflow calling another workflow inline, rather
    than via `start_child`, would not get its own journal — intentional here, since
    this function's only job is to be inlined into whichever workflow is driving it).

    KAN-649: unlike `loop.py::run_repair`, this function builds **no** hand-written
    `transcript: list[str]` — every `_complete`/`_dispatch`/`_verify` call this
    function makes is already a durable call recorded on this run's own journal
    (input, output, attempt count, timing), so a parallel hand-built log would be
    exactly the redundant, driftable artifact ADR-0013's "Context" section describes.
    The driving entry point (`run_repair_satay`/`run_repair_satay_candidates`) fills
    in `RepairResult.transcript` as `{"kind": "journal", ...}` once, after the drive,
    pointing at this run's own persisted journal.
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
    last_text = ""
    edit_attempts = 0

    for _ in range(max_turns):
        turn = await _complete(SYSTEM_PROMPT, messages, tools)
        if turn.text:
            last_text = turn.text

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
            outcome = await _dispatch(tc)
            content, is_error = outcome["content"], outcome["is_error"]
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
        return {
            "outcome": "needs_prod_action",
            "explanation": recommendation,
            "evidence": None,
        }

    diff = ctx.working.full_diff()
    if not diff:
        return {
            "outcome": "no_fix",
            "explanation": last_text or "Could not produce a confident fix.",
            "evidence": None,
        }

    explanation = last_text or "Drafted a minimal fix for the failing model."

    if ctx.sandbox is None:
        return {
            "outcome": "pr_proposed",
            "diff": diff,
            "explanation": explanation,
            "evidence": None,
            "confidence": None,
            "risk_class": None,
        }

    return await _verify_and_gate(ctx, diff, explanation, edit_attempts)


async def _verify_and_gate(
    ctx: AgentContext,
    diff: str,
    explanation: str,
    edit_attempts: int,
) -> dict[str, Any]:
    """Durable-call port of `loop.py::_verify_and_gate` — identical logic, one line
    (the sandbox run) going through `await _verify(...)` instead of
    `ctx.verify_current(...)` directly. No `transcript` bookkeeping — see
    `_run_candidate`'s docstring.
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
        return {
            "outcome": "no_fix",
            "explanation": (
                explanation
                + "\n\nThis draft did not pass tier-1 compile, so it was not proposed."
            ),
            "evidence": evidence,
            "confidence": scored["confidence"],
            "risk_class": scored["risk_class"],
            "factors": scored["factors"],
        }

    return {
        "outcome": "pr_proposed",
        "diff": diff,
        "explanation": explanation,
        "evidence": evidence,
        "confidence": scored["confidence"],
        "risk_class": scored["risk_class"],
        "factors": scored["factors"],
    }


# --- N>1: one child workflow per candidate, fanned out with collect-mode gather ----


@satay.workflow
async def _candidate_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """One candidate, run as its own `@satay.workflow` child (its own journal).

    Started per-candidate via `satay.start_child` from `_multi_candidate_workflow`.
    Binds this candidate's own `_Rig` (looked up from `_RIGS` by `candidate_key`,
    which `_multi_candidate_workflow` put in the recorded payload) onto `_RIG` for the
    duration of this workflow's own `asyncio.Task` — see the module docstring's
    ContextVar section for why this isolates cleanly across concurrent candidates —
    then delegates to the exact same `_run_candidate` body the N=1 path uses.
    """
    candidate_key: str = payload["candidate_key"]
    token = _RIG.set(_rig_for_candidate(candidate_key))
    try:
        return await _run_candidate(payload)
    finally:
        _RIG.reset(token)


@satay.workflow
async def _multi_candidate_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """Fan out N candidates as children, collect them, judge the survivors.

    Architectural choice (`start_child` + `gather`, not `satay.map`) is explained in
    the module docstring. The coroutines passed to `gather` are the **unawaited**
    return values of calling `satay.start_child(...)` — passing an already-awaited
    `RunHandle` here would have driven each child to completion, and raised on the
    first failure, one at a time, *before* `gather` ever ran (see the docstring).
    """
    task: dict[str, Any] = payload["task"]
    max_turns: int = payload["max_turns"]
    tools: list[ToolSpec] = payload["tools"]
    candidate_keys: list[str] = payload["candidate_keys"]

    coros = [
        satay.start_child(
            _candidate_workflow,
            {
                "task": task,
                "max_turns": max_turns,
                "tools": tools,
                "candidate_key": key,
            },
            key=key,
        )
        for key in candidate_keys
    ]
    outcomes = await satay.gather(*coros, return_exceptions=True)
    result, winning_key = _judge(candidate_keys, outcomes)
    # KAN-649: stash which candidate won so `run_repair_satay_candidates` (outside
    # this workflow, after the drive) can resolve that candidate's own child
    # `run_id` from the parent's `ChildWorkflowScheduled` events and point
    # `RepairResult.transcript` at the run that actually produced this result —
    # never the parent fan-out workflow's own run_id, never a losing candidate's.
    # Popped back off before `RepairResult` is returned to the claim loop, so it
    # never appears in the frozen contract; see that function's docstring.
    return {**result, "_candidate_key": winning_key}


def _score_key(result: dict[str, Any]) -> float:
    """Rank a settled `RepairResult`-shaped dict by confidence; missing → lowest."""
    confidence = result.get("confidence")
    return confidence if confidence is not None else -1.0


def _judge(
    candidate_keys: list[str], outcomes: list[Any]
) -> tuple[dict[str, Any], str]:
    """Pick one `RepairResult` out of N settled candidates (collect mode, ADR-0027).

    Returns ``(result, winning_candidate_key)`` — the key is new in KAN-649, so the
    caller can resolve which child run actually produced ``result`` (see
    `_multi_candidate_workflow`).

    `outcomes[i]` (rejoined positionally by `satay.gather`, per `candidate_keys[i]`)
    is either the candidate's own `RepairResult`-shaped dict, or a
    `satay.WorkflowFailedError` if that candidate's own workflow raised (e.g. every
    `_complete`/`_dispatch`/`_verify` call on that candidate exhausted its retries and
    propagated — collect mode records the failure durably on that child's own journal
    rather than sinking the whole run; see `best_of_n_demo.py`'s "the half people
    miss").

    The verdict, closely mirroring `run_repair`'s existing single-candidate fallback
    shape (this is a port, not new product behaviour):

    1. Among candidates that reached `pr_proposed`, keep the one with the highest
       `score.py` confidence (ties broken by candidate order).
    2. Else, among candidates that reached `needs_prod_action`, keep one. In practice
       there is at most one *distinct* verdict here: `_detect_prod_action` is a
       read-only check over the failing model's source and the upstream warehouse
       schema (see `loop.py`), neither of which varies by candidate, so every
       candidate that reaches it either all agree or none of them get there (a
       candidate that drafted an edit before failing takes the `no_fix`/`pr_proposed`
       path instead). `_score_key` still orders them defensively in case a future
       change makes that no longer strictly true.
    3. Else, among candidates that reached `no_fix`, keep the highest-confidence one
       (a tier-1-failure `no_fix` carries a real score from `_verify_and_gate`; a
       no-diff-drafted `no_fix` does not and sorts last).
    4. Else every candidate raised: synthesize a `no_fix` naming the first failure,
       mirroring `run_repair`'s "give up cleanly" contract (R3.3) — there is no
       drafted diff from any candidate, so this can never legitimately become
       `pr_proposed`.
    """
    results: list[tuple[str, dict[str, Any]]] = []
    exceptions: list[tuple[str, Exception]] = []
    for key, outcome in zip(candidate_keys, outcomes):
        if isinstance(outcome, Exception):
            exceptions.append((key, outcome))
        else:
            results.append((key, outcome))

    proposed = [(k, r) for k, r in results if r.get("outcome") == "pr_proposed"]
    if proposed:
        key, result = max(proposed, key=lambda kr: _score_key(kr[1]))
        return result, key

    needs_prod = [(k, r) for k, r in results if r.get("outcome") == "needs_prod_action"]
    if needs_prod:
        key, result = max(needs_prod, key=lambda kr: _score_key(kr[1]))
        return result, key

    no_fix = [(k, r) for k, r in results if r.get("outcome") == "no_fix"]
    if no_fix:
        key, result = max(no_fix, key=lambda kr: _score_key(kr[1]))
        return result, key

    key, exc = exceptions[0]
    error_type = getattr(exc, "error_type", type(exc).__name__)
    error_message = getattr(exc, "error_message", str(exc))
    # No candidate settled with a result at all: point the transcript at the
    # first-failing candidate's own (failed) journal anyway, rather than at the
    # parent — that is still the run a reviewer would open first to see why.
    return {
        "outcome": "no_fix",
        "explanation": (
            f"All {len(candidate_keys)} candidate(s) failed to run; first failure "
            f"(candidate {key!r}): {error_type}: {error_message}"
        ),
        "evidence": None,
    }, key


def _clone_ctx_for_candidate(base: AgentContext, candidate_key: str) -> AgentContext:
    """A fresh `AgentContext` for one candidate, sharing the job's shared/read-only
    resources with `base` but never its mutable per-run state.

    Safe to share as-is (confirmed by reading each implementation, per the module
    docstring): `source` (stateless file reads), `warehouse` (opens a fresh
    connection per call), `guard` (pure config, no mutable state). NOT shared, ever:
    `working` (the in-memory draft every `edit_file` mutates) and the
    `last_run`/`last_verified_diff` single-slot cache — each candidate gets its own,
    or two candidates editing concurrently would stomp each other's drafts.

    `sandbox` is NOT shared either, despite being read-only from this module's point
    of view — it is cloned with a `candidate_key`-suffixed `sample_schema` (and
    `verify_schema`, for symmetry). This is the fix for a real bug CI caught (see the
    module docstring's "Correction" note): the local `/tmp` work directory
    `SandboxRunner.verify` materializes into IS unique per call, but the *warehouse*
    schema/relation tier-2 (`dbt build`) writes into was a single hardcoded name
    (`sbflow_sample.<model>`) shared by every caller — safe under N=1 (only one
    verification ever ran at a time), but a real, reproduced race under N>1: two
    concurrent `dbt build`s against the same relation collide on dbt's
    create-or-replace backup-swap.
    """
    sandbox = base.sandbox
    if sandbox is not None:
        sandbox = dataclasses.replace(
            sandbox,
            verify_schema=f"{sandbox.verify_schema}_{candidate_key}",
            sample_schema=f"{sandbox.sample_schema}_{candidate_key}",
        )
    return dataclasses.replace(
        base,
        working=WorkingCopy(),
        last_run=None,
        last_verified_diff="",
        sandbox=sandbox,
    )


# --- the entry points ---------------------------------------------------------------


def _journal_transcript(run_id: str, journal_path: Path) -> dict[str, Any]:
    """Build the `{"kind": "journal", ...}` transcript arm (ADR-0013) for `run_id`.

    `ref` is the journal file's basename, not a full path: `brain/src/pr/body.rs`
    already knows the shared directory (its own `SBFLOW_SATAY_JOURNAL_DIR`), so
    `ref` only needs to name *which file inside it* — not repeat the directory, and
    not leak this worker's own filesystem layout into a value a reviewer might see.
    """
    return {"kind": "journal", "run_id": run_id, "ref": journal_path.name}


def run_repair_satay(
    provider: LlmProvider,
    ctx: AgentContext,
    task: dict[str, Any],
    max_turns: int,
    tools: list[ToolSpec] | None = None,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Drive `_repair_workflow` for one job through Satay, synchronously.

    Called by `build_processor` in place of `run_repair` when
    `Config.satay_loop_enabled` is on. KAN-649: opens a **persistent** journal (a
    real file, not `":memory:"`) at `journal_path` (default derived from
    `SBFLOW_SATAY_JOURNAL_DIR`/`Config.satay_journal_dir` — see that field's
    docstring for why one shared file rather than one per job), so the run this call
    drives is still readable after the call returns — by `brain/src/pr/body.rs`, or
    by `satay runs show <run_id>` against the same file. Crash-resume is still owned
    entirely by V5's job-level lease re-claim (`claim.py`, untouched); this file
    existing does not change that (ADR-0012).
    """
    from satay.journal.store import SQLiteStore

    tools = tools or TOOL_SPECS
    path = Path(journal_path) if journal_path is not None else _default_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = _RIG.set(_Rig(provider=provider, ctx=ctx))
    store = SQLiteStore.open(path)
    try:
        handle = satay.start(
            _repair_workflow,
            {"task": task, "max_turns": max_turns, "tools": tools},
            store=store,
        )
        result = asyncio.run(handle.result())
        result["transcript"] = _journal_transcript(handle.run_id, path)
        return result
    finally:
        store.close()
        _RIG.reset(token)


async def _resolve_child_run_id(
    store: "SQLiteStore",
    parent_run_id: str,
    candidate_key: str | None,
) -> str:
    """Find the child `run_id` `_multi_candidate_workflow` started for `candidate_key`.

    Reads the parent's own `ChildWorkflowScheduled` events (public store-read API —
    no reaching into replay-engine internals for this) and matches on the `key`
    field `satay.start_child(..., key=candidate_key)` recorded. Falls back to the
    parent's own `run_id` if `candidate_key` is unset or no match is found (should
    not happen in practice — `_judge` always names a real candidate — but a
    transcript pointing at *a* real, readable run beats crashing the whole job).
    """
    from satay.journal.events import EventType

    if candidate_key is None:
        return parent_run_id
    for event in await store.read_events(parent_run_id):
        if (
            event.type is EventType.CHILD_WORKFLOW_SCHEDULED
            and event.payload.get("key") == candidate_key
        ):
            child_run_id = event.payload.get("child_run_id")
            if isinstance(child_run_id, str) and child_run_id:
                return child_run_id
    return parent_run_id


async def _drive_multi_candidate(
    store: "SQLiteStore", payload: dict[str, Any], path: Path
) -> dict[str, Any]:
    """Drive `_multi_candidate_workflow` and resolve the winning child's `run_id`.

    Both steps run inside the same `asyncio.run(...)` call (one event loop): the
    drive itself, and the follow-up `store.read_events(...)` `_resolve_child_run_id`
    needs, since `SQLiteStore`'s methods are coroutines and there is no live loop
    once `asyncio.run` has returned.
    """
    handle = satay.start(_multi_candidate_workflow, payload, store=store)
    result = await handle.result()
    candidate_key = result.pop("_candidate_key", None)
    run_id = await _resolve_child_run_id(store, handle.run_id, candidate_key)
    result["transcript"] = _journal_transcript(run_id, path)
    return result


def run_repair_satay_candidates(
    provider_factory: Callable[[], LlmProvider],
    ctx: AgentContext,
    task: dict[str, Any],
    max_turns: int,
    n_candidates: int,
    tools: list[ToolSpec] | None = None,
    journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Drive `n_candidates` candidates concurrently through Satay collect-mode
    fan-out (KAN-648 slice 2, ADR-0012 decision 4's actual trigger), and return one
    judged `RepairResult`-shaped dict.

    `n_candidates <= 1` degenerates to calling `run_repair_satay` directly — the exact
    same function slice 1 shipped, called with `provider_factory()`'s one instance and
    `ctx` unmodified — so N=1 is byte-identical to slice 1's behaviour, not merely
    "produces equal output" (see `tests/test_satay_loop_candidates.py`). Note that
    "byte-identical" no longer extends to the transcript's `run_id`: every call to
    `run_repair_satay`/`run_repair_satay_candidates` mints a fresh run against the
    journal, so two calls with identical scripted inputs still get two different
    (but each individually real and resolvable) `run_id`s.

    For N>1: builds N independent `_Rig`s — a fresh `provider_factory()` call and a
    fresh `_clone_ctx_for_candidate(ctx, key)` per candidate (see both docstrings for
    why neither the provider nor the context — including its `SandboxRunner`'s
    warehouse-side verification target — can be shared across concurrently-running
    candidates) — publishes them once, up front, on `_RIGS`, then drives
    `_multi_candidate_workflow` through a **persistent** journal exactly as
    `run_repair_satay` does for N=1 (KAN-649; V5's job-level lease re-claim,
    `claim.py`, is untouched and still owns crash recovery, per ADR-0012). `ctx`
    itself is never mutated by this path — it is only ever read, as the template
    every candidate's own context is cloned from.

    The emitted `transcript.run_id` is the **winning candidate's own child run**
    (its own `@satay.workflow` child, its own journal) — never the parent
    `_multi_candidate_workflow`'s run_id, and never a losing candidate's — resolved
    from the parent's `ChildWorkflowScheduled` events after the drive (see
    `_resolve_child_run_id`). A reviewer clicking through the transcript link should
    land on the run that actually produced the shipped diff, not the fan-out
    coordinator or a candidate that lost the judging.
    """
    if n_candidates < 1:
        raise ValueError(f"n_candidates must be >= 1, got {n_candidates}")
    if n_candidates <= 1:
        return run_repair_satay(
            provider_factory(), ctx, task, max_turns, tools, journal_path
        )

    from satay.journal.store import SQLiteStore

    tools = tools or TOOL_SPECS
    candidate_keys = [f"c{i}" for i in range(n_candidates)]
    rigs = {
        key: _Rig(provider=provider_factory(), ctx=_clone_ctx_for_candidate(ctx, key))
        for key in candidate_keys
    }
    rigs_token = _RIGS.set(rigs)
    path = Path(journal_path) if journal_path is not None else _default_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore.open(path)
    try:
        payload = {
            "task": task,
            "max_turns": max_turns,
            "tools": tools,
            "candidate_keys": candidate_keys,
        }
        return asyncio.run(_drive_multi_candidate(store, payload, path))
    finally:
        store.close()
        _RIGS.reset(rigs_token)
