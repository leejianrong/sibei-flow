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

**KAN-651 update (EPIC-84, cost accounting):** `_complete` now self-reports
whatever token usage the provider surfaced on that call (`turn.usage`) onto its
own `TaskContext` via `ctx.record_model_usage(...)` — a generic, provider-agnostic
usage slot satay's executor flushes onto the attempt's own `TaskCompleted`/
`TaskAttemptFailed` event, exactly the mechanism `examples/best_of_n_demo.py`
demonstrates. No parallel accounting is kept in Python: `journal.rs` (brain side)
sums usage straight out of the journal at render time, the same "the journal is
the source of truth, not a second artifact that can drift" posture ADR-0013
already established for the reasoning transcript. `_journal_transcript` gained
one additive, optional key (`cost_run_ids`) so the N>1 path can point a cost
reader at every candidate's own run, not just the winner's — see
`_drive_multi_candidate`'s docstring for that decision's reasoning.

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

**KAN-650 — fork-replay tier-3 (ADR-0012's Consequences section).** The ADR
names the destination directly: "fork the repair run at the failing call and
replay a candidate fix against the recorded inputs... Tier-1 `dbt compile` is
weighted 0.30 precisely because it is a weak signal; replay-against-real-inputs
is a strong one." This module adds the primitive: `locate_get_schema_fork_point`
finds the *last* `get_schema` `_dispatch` call before any `edit_file` call in a
completed candidate's own journal (the schema state the fix was actually
drafted against — see that function's docstring for the full policy, including
the zero/all-errored degrade case), and `reverify_with_fork` forks the run
`fork_point_seq`-inclusive of that call, substitutes a caller-supplied
`override_diff` (full replacement content per changed path, not unified-diff
*text* to parse — see `_apply_override_diff_and_verify`), and drives the fork
to a fresh, independently-verified `RepairResult`. Because satay's replay
engine never re-executes a journal *hit* (`replay/engine.py::durable_call`'s
"hit" branch returns the recorded result without calling the task body), the
`get_schema` call and everything before it replay from the copied prefix — no
live warehouse round-trip — while the fork's own `_verify` call (the
substituted diff's tier-1/tier-2 sandbox run) is genuinely new, real work.

`_run_candidate` is the fork-aware body both `_repair_workflow` and
`_candidate_workflow` share: an optional `override_diff` + matching
`override_after_ordinal` in `payload` (set only by `reverify_with_fork`, never
by the normal single-pass or multi-candidate entry points below) makes it stop
drafting the instant it replays the located `get_schema` call and jump straight
to `_apply_override_diff_and_verify` instead of continuing the turn loop — see
that branch, inline in the tool-call loop, for why a *local* ordinal counter
mirroring satay's own `(task_name, ordinal)` identity (`replay/identity.py`) is
enough to find the exact right moment without any special-casing of tool names.

**Invocation-scope decision (read before wiring this into anything else).**
This card ships the fork-point-location function, the fork-driving function,
and the evidence/score plumbing (`sandbox/evidence.py`'s `tier3` slot,
`score.py`'s re-derived rubric) — a working, tested primitive — but does
**not** call `reverify_with_fork` automatically from `build_processor`'s
single-pass claim loop, or from every candidate's own verification in the N>1
path. Two reasons: first, "every candidate forks and re-verifies itself" is
close to redundant with the live verification it just did seconds earlier — the
live warehouse has not had time to drift, so the strong-signal property the ADR
describes ("does this fix work against the precise conditions recorded when
the failure was diagnosed, rather than against whatever the warehouse looks
like at verify time") barely applies yet. Second, the more interesting use
this primitive unlocks — re-verifying a *different, later* diff (a
human-edited variant, or a future incremental-fix flow) against an *earlier*
candidate's recorded snapshot — has no caller in this codebase yet: no UI
action, no CLI subcommand, no automatic trigger condition has been decided,
and inventing one here would be a product decision past what this card or the
brief asks for. `reverify_with_fork` is therefore a real, directly callable,
test-proven Python entry point today (see `tests/test_satay_loop_fork_tier3.py`)
— the primitive the ADR calls "the strongest long-term payoff" — waiting on a
follow-on card to decide when it fires.
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

    **KAN-651:** if the provider self-reported usage on this turn (`turn.usage`,
    see `AssistantTurn`'s docstring), record it onto THIS attempt's `TaskContext`
    before returning — mirroring `examples/best_of_n_demo.py`'s `bill()` pattern
    of billing as soon as the answer is in hand, before anything downstream can
    reject it. That ordering matters here too: `satay`'s executor flushes
    `ctx.recorded_usage` onto whichever event ends this attempt, `TaskCompleted`
    if this call returns normally (always true here — nothing below can still
    fail this attempt) or `TaskAttemptFailed` on a raise, so a provider call that
    answered and was then never used for any reason is still priced honestly.
    A provider that reports no usage (`turn.usage is None` — always true for
    `ReplayProvider`, possibly true for a live provider) records nothing, which
    the read side (`journal.rs`) must treat as "not available", never as zero.
    """
    rig = _rig()
    turn = await asyncio.to_thread(rig.provider.complete, system, messages, tools)
    if turn.usage:
        satay.task_context().record_model_usage(**turn.usage)
    return turn


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
    # KAN-650: an optional fork-replay override, set only by
    # `reverify_with_fork` (never by `run_repair_satay`/
    # `run_repair_satay_candidates`) — see the module docstring's "fork-replay
    # tier-3" section. `override_after_ordinal` names the `_dispatch` call, by
    # satay's own per-task-name ordinal (`replay/identity.py`), after which
    # this function must stop drafting and verify `override_diff` instead.
    # Always set together, or not at all.
    override_diff: dict[str, str] | None = payload.get("override_diff")
    override_after_ordinal: int | None = payload.get("override_after_ordinal")

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": build_initial_prompt(task)}],
        }
    ]
    last_text = ""
    edit_attempts = 0
    # Mirrors satay's own `IdentityResolver` counter for the `_dispatch` task
    # name: "the Nth durable call of task T during a drive", 0-indexed,
    # incremented once per call in call order (`replay/identity.py`). Both
    # counters start at 0 and this function's only `_dispatch` call site is
    # the single `await _dispatch(tc)` below, awaited strictly in order, so
    # this local counter always equals the ordinal satay itself assigns —
    # confirmed safe because the nondeterminism check only compares task
    # *names* at each call position, never arguments (`replay/engine.py`'s
    # `durable_call`), so nothing here needs to coordinate with satay beyond
    # "count dispatch calls in the same order it does."
    dispatch_ordinal = 0

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
            this_ordinal = dispatch_ordinal
            dispatch_ordinal += 1
            content, is_error = outcome["content"], outcome["is_error"]
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": content,
                    "is_error": is_error,
                }
            )
            if override_diff is not None and this_ordinal == override_after_ordinal:
                # KAN-650: this was the recorded `get_schema` call
                # `reverify_with_fork` forked right after. Every `_complete`/
                # `_dispatch` call up to and including it was a journal *hit*
                # (satay never re-executes one — see the module docstring), so
                # nothing above touched a live warehouse. Stop drafting here:
                # no further turns, no further tool calls this turn either —
                # jump straight to verifying the substituted diff.
                rig = _rig()
                return await _apply_override_diff_and_verify(
                    rig.ctx, override_diff, edit_attempts
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
        tier3_passed=evidence["tier3"]["passed"],
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


# --- KAN-650: fork-replay tier-3 ----------------------------------------------------


async def _apply_override_diff_and_verify(
    ctx: AgentContext, override_diff: dict[str, str], edit_attempts: int
) -> dict[str, Any]:
    """The fork-replay short-circuit's tail: apply a substituted fix, verify it.

    `override_diff` maps repo-relative path -> the file's full substituted
    content. Deliberately **not** a unified-diff *string* to parse and apply:
    `WorkingCopy` already models an edit as an ``(original, current)`` content
    pair and derives the unified diff itself via ``full_diff()`` (see
    `diffing.py`) — the same mechanism `edit_file` uses. Teaching this module a
    second, bespoke unified-diff-apply routine (accepting arbitrary diff text,
    resolving hunks/fuzz) would be new surface area this codebase has never
    needed; a caller that has a diff to substitute (say, a human-edited
    variant) applies it to get the resulting file content and passes that.

    The original content for any path not already loaded in `ctx.working` is
    read fresh via `ctx.source.read` — a plain, deterministic local file read
    (the same one `read_file` itself performs), not a live warehouse
    round-trip; nothing here re-touches `ctx.warehouse`.

    Reuses `_verify_and_gate` unchanged after that: verifying a fork-replay
    candidate is not a different *kind* of verification, only a different diff
    to verify — same tier-1/tier-2 sandbox gate, same evidence/score shape.
    """
    for path, new_content in override_diff.items():
        if not ctx.working.has(path):
            ctx.working.load(path, ctx.source.read(path))
        ctx.working.set_current(path, new_content)

    diff = ctx.working.full_diff()
    if not diff:
        return {
            "outcome": "no_fix",
            "explanation": (
                "The substituted fix (override_diff) produced no changes "
                "against the recorded original content."
            ),
            "evidence": None,
        }

    explanation = (
        "Re-verified a substituted fix against the recorded schema snapshot "
        "(KAN-650 fork-replay tier-3)."
    )

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


@dataclass(frozen=True)
class ForkPoint:
    """Where a completed candidate run's `get_schema` call sits, for a KAN-650 fork.

    `fork_point_seq` is the `seq` of that call's own `TaskCompleted` event —
    passed to `satay.fork(..., fork_point_seq=...)`, which keeps it
    **inclusive** (the last source event copied, per `satay.api.fork.fork`'s
    docstring), so the copied prefix ends exactly on "get_schema happened,
    nothing after it did." `get_schema_ordinal` is the `_dispatch` ordinal of
    that same call (satay's own `(task_name, ordinal)` identity,
    `replay/identity.py`) — carried into the fork's own `override_after_ordinal`
    payload field so `_run_candidate` knows precisely which call to stop after.
    `source` is the upstream table name that call queried (`ToolCall.input`'s
    `"source"` field), kept only for a readable tier-3 log line.
    """

    fork_point_seq: int
    get_schema_ordinal: int
    source: str | None


async def locate_get_schema_fork_point(
    store: "SQLiteStore", run_id: str
) -> ForkPoint | None:
    """Find the `get_schema` call a KAN-650 fork should cut right after.

    **Policy** (confirmed design — see the ticket): the **last** `get_schema`
    call before any `edit_file` call. That is the schema state the fix was
    actually drafted against — a `get_schema` call *after* the first edit
    would be confirming something the model had already acted on, not the
    diagnostic read that justified the edit. A `get_schema` call that itself
    errored (e.g. no warehouse connection configured —
    `tools.py::AgentContext.get_schema`) is never eligible: it carries no real
    schema snapshot to re-verify against, only a disclosed error string.

    Degrades gracefully to `None` — no crash, no fork attempted — when the run
    never called `get_schema` at all, called it but every call errored, or
    (defensively) scheduled a call that never completed (the run crashed
    mid-call): a run that never established a usable schema snapshot has no
    tier-3 signal available. `reverify_with_fork` discloses this via
    `sandbox/evidence.tier3_not_run` rather than raising, matching this
    codebase's existing "disclose, don't fabricate" convention
    (`evidence.py`/`score.py`).
    """
    from satay.journal.events import EventType

    events = await store.read_events(run_id)

    get_schema_ordinals: list[int] = []
    get_schema_sources: dict[int, str | None] = {}
    first_edit_file_ordinal: int | None = None
    # ordinal -> (TaskCompleted seq, is_error)
    completed: dict[int, tuple[int, bool]] = {}

    for event in events:
        if event.type is EventType.TASK_SCHEDULED:
            if event.payload.get("task_name") != "_dispatch":
                continue
            ordinal = event.payload.get("ordinal")
            if ordinal is None:  # keyed identity — _dispatch never uses one
                continue
            input_ref = event.payload.get("input_ref")
            call = input_ref[0] if isinstance(input_ref, list) and input_ref else None
            name = call.get("name") if isinstance(call, dict) else None
            if name == "get_schema":
                get_schema_ordinals.append(ordinal)
                call_input = call.get("input") if isinstance(call, dict) else None
                get_schema_sources[ordinal] = (
                    call_input.get("source") if isinstance(call_input, dict) else None
                )
            elif name == "edit_file" and first_edit_file_ordinal is None:
                first_edit_file_ordinal = ordinal
        elif event.type is EventType.TASK_COMPLETED:
            if event.payload.get("task_name") != "_dispatch":
                continue
            ordinal = event.payload.get("ordinal")
            if ordinal is None:
                continue
            output_ref = event.payload.get("output_ref")
            is_error = (
                bool(output_ref.get("is_error"))
                if isinstance(output_ref, dict)
                else True
            )
            completed[ordinal] = (event.seq, is_error)

    eligible = [
        ordinal
        for ordinal in get_schema_ordinals
        if (first_edit_file_ordinal is None or ordinal < first_edit_file_ordinal)
        and ordinal in completed
        and not completed[ordinal][1]  # not an error
    ]
    if not eligible:
        return None

    target_ordinal = max(eligible)
    fork_point_seq, _ = completed[target_ordinal]
    return ForkPoint(
        fork_point_seq=fork_point_seq,
        get_schema_ordinal=target_ordinal,
        source=get_schema_sources.get(target_ordinal),
    )


class _UnreachableProvider(LlmProvider):
    """An `LlmProvider` that must never actually be called.

    `reverify_with_fork`'s rig never needs a real one: the override branch in
    `_run_candidate` stops at the recorded `get_schema` call and jumps
    straight to verification, so no further model turn ever happens on a
    fork-replay drive. If `.complete` runs, `override_after_ordinal` did not
    land where this module assumed it would — a bug here, not a runtime
    possibility a caller needs to plan for.
    """

    def complete(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> AssistantTurn:  # pragma: no cover - defensive
        raise AssertionError(
            "reverify_with_fork's rig had provider.complete() called — the "
            "override branch should have short-circuited before any further "
            "model turn; override_after_ordinal did not match the located "
            "get_schema call"
        )


@dataclass
class Tier3Outcome:
    """`reverify_with_fork`'s result.

    `evidence` is exactly `sandbox/evidence.py`'s `tier3` shape (built by
    `tier3_not_run`/`tier3_result`) — pass it straight into
    `ScoreSignals(tier3_passed=evidence["passed"], ...)` to re-score an
    existing candidate, or fold it into an existing evidence dict with
    `merge_tier3_evidence` below. `fresh_result` is the fork's own, fully
    independent `RepairResult`-shaped dict — `None` only when `evidence["ran"]`
    is `False` (no fork was ever attempted; see
    `locate_get_schema_fork_point`'s degrade cases).
    """

    evidence: dict[str, Any]
    fork_run_id: str | None
    fresh_result: dict[str, Any] | None


def merge_tier3_evidence(
    evidence: dict[str, Any], tier3: dict[str, Any]
) -> dict[str, Any]:
    """Fold a `reverify_with_fork` tier-3 block into an existing evidence dict.

    A convenience for a future caller that wants to re-score an *existing*
    `RepairResult`'s evidence with a fork-replay verdict (e.g. a human
    re-verifying a stored candidate through the dashboard) without hand-
    building the merge. Returns a new dict; `evidence` is never mutated.
    """
    return {**evidence, "tier3": tier3}


async def _read_workflow_input(store: "SQLiteStore", run_id: str) -> dict[str, Any]:
    """The recorded `payload` dict `run_id`'s own workflow was started with.

    Read straight off its `WorkflowCreated` event rather than asked of the
    caller: `reverify_with_fork` only needs `source_run_id`, not a second copy
    of `task`/`max_turns`/`tools` the caller would have to keep byte-for-byte
    in sync with what the source run actually recorded — and it must be
    byte-for-byte, since the copied prefix only replays identically if the
    turn loop reaches the exact same point it did originally (see
    `_run_candidate`'s docstring on why `max_turns` in particular matters).
    """
    from satay.journal.events import EventType

    for event in await store.read_events(run_id):
        if event.type is EventType.WORKFLOW_CREATED:
            input_ref = event.payload.get("input_ref")
            return dict(input_ref) if isinstance(input_ref, dict) else {}
    raise ValueError(f"run {run_id!r} has no WorkflowCreated event")


def _tier3_passed_from_result(result: dict[str, Any]) -> bool | None:
    """Derive tier-3 pass/fail from the fork's own fresh `RepairResult`.

    `pr_proposed` **with evidence attached** means the substituted diff passed
    tier-1 (and tier-2, if configured) under the recorded conditions: a clean
    pass. `pr_proposed` with `evidence: null` means the fork's own rig had no
    `sandbox` configured (mirrors `_apply_override_diff_and_verify`'s
    unverified-draft branch) — nothing was actually re-verified, so this is
    undetermined, not a pass, exactly like a normal unverified V2-shaped draft.
    `no_fix` with evidence attached means it was drafted, verified, and
    rejected by the compile gate: a clean fail. `no_fix` with no evidence at
    all means the override diff produced no changes to verify in the first
    place (see `_apply_override_diff_and_verify`) — also undetermined.
    """
    if result.get("outcome") == "pr_proposed":
        return True if result.get("evidence") is not None else None
    if result.get("outcome") == "no_fix" and result.get("evidence") is not None:
        return False
    return None


async def _reverify_with_fork(
    store: "SQLiteStore",
    source_run_id: str,
    override_diff: dict[str, str],
    ctx: AgentContext,
) -> "Tier3Outcome":
    from ..sandbox.evidence import tier3_not_run, tier3_result

    fork_point = await locate_get_schema_fork_point(store, source_run_id)
    if fork_point is None:
        return Tier3Outcome(
            evidence=tier3_not_run(
                f"run {source_run_id!r} never completed a usable get_schema "
                "call before drafting (either it called get_schema zero "
                "times, or every call errored) — no recorded schema snapshot "
                "to fork-replay against"
            ),
            fork_run_id=None,
            fresh_result=None,
        )

    original_payload = await _read_workflow_input(store, source_run_id)
    payload = {
        **original_payload,
        "override_diff": override_diff,
        "override_after_ordinal": fork_point.get_schema_ordinal,
    }

    rig = _Rig(provider=_UnreachableProvider(), ctx=ctx)
    token = _RIG.set(rig)
    rigs_token = None
    candidate_key = original_payload.get("candidate_key")
    if candidate_key is not None:
        # The source run was an N>1 `_candidate_workflow` child — its own body
        # binds `_RIG` from `_RIGS[candidate_key]` (see `_candidate_workflow`),
        # so the fork (re-driving that same workflow function) needs the same
        # lookup to succeed for the same key.
        rigs_token = _RIGS.set({candidate_key: rig})
    try:
        handle = await satay.fork(
            source_run_id,
            fork_point_seq=fork_point.fork_point_seq,
            workflow_input=payload,
            store=store,
        )
        result = await handle.result()
    finally:
        _RIG.reset(token)
        if rigs_token is not None:
            _RIGS.reset(rigs_token)

    fork_run_id = handle.run_id
    tier3 = tier3_result(
        passed=_tier3_passed_from_result(result),
        source_run_id=source_run_id,
        fork_run_id=fork_run_id,
        get_schema_ordinal=fork_point.get_schema_ordinal,
        log=(
            f"forked {source_run_id} at get_schema ordinal "
            f"{fork_point.get_schema_ordinal} (source={fork_point.source!r}); "
            f"fork run {fork_run_id} -> outcome={result.get('outcome')!r}"
        ),
    )
    return Tier3Outcome(evidence=tier3, fork_run_id=fork_run_id, fresh_result=result)


def reverify_with_fork(
    source_run_id: str,
    override_diff: dict[str, str],
    ctx: AgentContext,
    journal_path: str | Path | None = None,
) -> Tier3Outcome:
    """KAN-650 entry point: re-verify `override_diff` against `source_run_id`'s
    recorded `get_schema` snapshot via a real Satay fork, synchronously.

    `source_run_id` must be a completed run of `_repair_workflow` or
    `_candidate_workflow` (i.e. a `transcript.run_id` this module itself
    produced — `run_repair_satay`/`run_repair_satay_candidates`) persisted in
    the journal at `journal_path` (default `Config.satay_journal_dir`, same as
    every other entry point below). `ctx` supplies this call's own live
    resources (a fresh `WorkingCopy`, the same read-only `source`, and a real
    `sandbox` to actually re-verify against — `ctx.warehouse` is never read by
    a fork-replay drive, since the one `get_schema` call in play is always a
    journal hit, so it may safely be `None`).

    Mirrors `run_repair_satay`'s synchronous, open-store-drive-close shape.
    Never raises for a `source_run_id` with no usable `get_schema` call — see
    `locate_get_schema_fork_point` — but does propagate a real
    `satay.control.commands.ForkValidationError` for a genuinely bad
    `source_run_id` (unknown run, non-terminal run, ...), the same way
    `satay.fork` itself would for any other caller.
    """
    from satay.journal.store import SQLiteStore

    path = Path(journal_path) if journal_path is not None else _default_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore.open(path)
    try:
        return asyncio.run(
            _reverify_with_fork(store, source_run_id, override_diff, ctx)
        )
    finally:
        store.close()


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


def _journal_transcript(
    run_id: str,
    journal_path: Path,
    cost_run_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build the `{"kind": "journal", ...}` transcript arm (ADR-0013) for `run_id`.

    `ref` is the journal file's basename, not a full path: `brain/src/pr/body.rs`
    already knows the shared directory (its own `SBFLOW_SATAY_JOURNAL_DIR`), so
    `ref` only needs to name *which file inside it* — not repeat the directory, and
    not leak this worker's own filesystem layout into a value a reviewer might see.

    `cost_run_ids` (KAN-651) is an additive, optional key within the `journal`
    arm — ADR-0013 explicitly leaves "the exact key names" of each arm to the
    implementation, provided the union stays discriminated by `kind`, so this is
    a backward-compatible widening rather than a shape change needing a fresh
    ADR (`CLAUDE.md`'s frozen-contract entry for `transcript?` is updated in the
    same change, per the precedent ADR-0013 itself set).

    Only set for `run_repair_satay_candidates`'s N>1 path: it names *every*
    candidate's own child run_id (including the winner), so a reader summing
    `ctx.record_model_usage` entries across `cost_run_ids` gets the WHOLE job's
    spend — every candidate drafted, not just the one that won the judging (see
    `_drive_multi_candidate`'s docstring for the reasoning). Absent for N=1 and
    for anything that predates this field: a reader must then fall back to
    `[run_id]` — the single run whose journal actually produced the result,
    which is the correct (and only) cost source when there was one candidate.
    """
    t: dict[str, Any] = {"kind": "journal", "run_id": run_id, "ref": journal_path.name}
    if cost_run_ids:
        t["cost_run_ids"] = cost_run_ids
    return t


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


async def _all_child_run_ids(store: "SQLiteStore", parent_run_id: str) -> list[str]:
    """Every child run `_multi_candidate_workflow` started under `parent_run_id`
    (KAN-651) — one per candidate, winner and losers alike, in the order their
    `ChildWorkflowScheduled` events were recorded.

    Reads the same public event stream `_resolve_child_run_id` reads (no reaching
    into replay-engine internals here either), just without filtering to one
    `key`. Used to build `cost_run_ids` — see `_journal_transcript`'s docstring
    and `_drive_multi_candidate`'s for why the cost side deliberately does NOT
    narrow to the winning candidate the way the reasoning-transcript `run_id`
    does.
    """
    from satay.journal.events import EventType

    run_ids: list[str] = []
    for event in await store.read_events(parent_run_id):
        if event.type is EventType.CHILD_WORKFLOW_SCHEDULED:
            child_run_id = event.payload.get("child_run_id")
            if isinstance(child_run_id, str) and child_run_id:
                run_ids.append(child_run_id)
    return run_ids


async def _drive_multi_candidate(
    store: "SQLiteStore", payload: dict[str, Any], path: Path
) -> dict[str, Any]:
    """Drive `_multi_candidate_workflow` and resolve the winning child's `run_id`.

    Both steps run inside the same `asyncio.run(...)` call (one event loop): the
    drive itself, and the follow-up `store.read_events(...)` `_resolve_child_run_id`
    (and, KAN-651, `_all_child_run_ids`) need, since `SQLiteStore`'s methods are
    coroutines and there is no live loop once `asyncio.run` has returned.

    **KAN-651's N-candidate cost decision:** "what did this job cost" is scoped
    to the WHOLE job — every candidate's model calls were real spend, whether or
    not that candidate went on to win the judging (collect mode runs every
    candidate to completion; a losing candidate is not a candidate that never
    ran). Reporting only the winner's cost would silently undercount actual
    spend by up to `n_candidates - 1` candidates' worth of drafting-loop turns
    on every N>1 job. So `cost_run_ids` names every child (`_all_child_run_ids`),
    while `run_id` itself stays the winning candidate's own run — unchanged from
    KAN-649 — because that is still the one run a reviewer clicking the
    "reasoning transcript" link should land on. Cost and reasoning are two
    different questions with two different right answers; `_journal_transcript`
    carries both without conflating them.
    """
    handle = satay.start(_multi_candidate_workflow, payload, store=store)
    result = await handle.result()
    candidate_key = result.pop("_candidate_key", None)
    run_id = await _resolve_child_run_id(store, handle.run_id, candidate_key)
    cost_run_ids = await _all_child_run_ids(store, handle.run_id)
    result["transcript"] = _journal_transcript(run_id, path, cost_run_ids=cost_run_ids)
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
