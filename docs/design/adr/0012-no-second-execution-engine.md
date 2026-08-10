# ADR-0012: No second execution engine — Satay is the engine of record

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Jian (leejianrong2@gmail.com)

Amends [ADR-0001](0001-control-plane-not-engine.md) — specifically the clause
"with our own lightweight state/execution engine as the core" — and bounds
[ADR-0009](0009-state-durability.md). ADR-0001 remains Accepted for everything
else it decides, above all the connector model.

## Context

ADR-0001 committed sibei-flow to owning a lightweight state/execution engine, and
`CONTEXT.md` §1 names the long-term thesis as being "the AI-native orchestrator
that owns the missing middle between heavy MLOps platforms and lightweight agent
frameworks", with Phase B building a unified state machine.

**Satay Runtime** (`leejianrong/satay-runtime`, Apache-2.0, same author) is a
durable-execution runtime for async Python built against the same category: five
durable primitives (task, durable sleep, external event wait, parallel
map/gather, child workflow), an append-only journal, replay that reuses recorded
results, retries with runtime-derived idempotency keys, fork-from-a-prefix, and
call-by-call run comparison. Its MVP is built and published to PyPI at
`0.1.0a3`.

So one maintainer has two projects claiming one category, which is strictly worse
than either project claiming it alone. That is the collision this ADR resolves.

The obvious framing — "sibei-flow builds no execution engine" — would be false.
V5 shipped `repair_jobs` with a state column, a lease, `FOR UPDATE SKIP LOCKED`
claims, dedup on `idem_key`, brain reconcile, an orphan-container sweep and
`LISTEN/NOTIFY` dispatch. That *is* a durable state machine, it works, and it is
tested. The boundary therefore has to be drawn as a **capability freeze** rather
than as an absence, because a freeze is checkable against a diff and an absence
is not.

## Decision

**1. Capability freeze.** sibei-flow's own durable state may model **repair jobs
and nothing else.**

In scope, and staying exactly as V5 shipped it: the `repair_jobs` table and its
state column, lease and claim, dedup on `idem_key`, brain reconcile, the
orphan-container sweep, `LISTEN/NOTIFY` dispatch, and the PR-opener claim.

Out of scope permanently, and owned by Satay if sibei-flow ever needs them:

- a general workflow or task abstraction, or any authoring DSL
- durable timers or durable sleep
- external event wait, signal delivery, or an event inbox
- fan-out with partial-completion recovery
- child or sub-workflow composition
- replay, journals, forking, or run comparison
- retry-policy machinery beyond the existing job-level lease re-claim

**2. Satay is the engine of record.** If sibei-flow needs any capability from
that second list, it adopts Satay rather than building it. Phase B's "unified
state machine" ambition is **withdrawn**: sibei-flow's long-term shape is a
deeper self-healing layer (more adapters, more failure classes, better evidence),
not an orchestrator.

**3. The coupling surface is the journal read format, not the execution core.**
Satay's journal event model is stdlib frozen dataclasses and the least
churn-prone part of it. sibei-flow may depend on *reading* a Satay journal before
it depends on Satay *driving* its execution. The first integration to land is
therefore the transcript ([ADR-0013](0013-transcript-tagged-union.md)), not the
claim loop.

**4. The port, when it happens, arrives at a capability boundary.** Porting the
repair loop wholesale would replace V5's tested crash-recovery path to win a
marketing line. Instead the port lands when sibei-flow grows a capability that
needs Satay: the designated trigger is **multi-candidate repair** ("draft three
candidate fixes, keep the one with the best evidence"), which requires
collect-mode fan-out that Satay is building anyway. Until that card is picked up,
the loop stays as it is.

## Consequences

- A PR that adds any capability from the out-of-scope list violates this ADR and
  should be rejected, or this ADR superseded first. **This is the check to run
  when reviewing durable-state changes.**
- ADR-0009's closing line ("revisited in phase B when sibei-flow owns execution
  of arbitrary user workflows") no longer applies. sibei-flow will not own
  execution of arbitrary user workflows.
- ADR-0001's connector model survives untouched. Satay joins Ray, Argo and
  Temporal as a downstream execution target, with the one difference that it is
  the **default** one rather than an option.
- [ADR-0008](0008-pluggable-executor-backends.md)'s pluggable executor backends
  stay a seam, still unused. Satay's own `TaskExecutor` seam covers the same
  ground one layer down.
- sibei-flow will eventually depend on a pre-1.0 runtime. Mitigated by rule 3
  (couple on the journal read format) and by leaving the Postgres claim loop
  alone.
- The strongest long-term payoff is a verification tier nobody else can offer:
  fork the repair run at the failing call and replay a candidate fix against the
  recorded inputs. Tier-1 `dbt compile` is weighted 0.30 in `score.py` precisely
  because it is a weak signal; replay-against-real-inputs is a strong one. That
  tier exists only if the run is a Satay journal.

## Alternatives considered

- **Port the whole agent loop onto Satay now, before the v1 launch** — rejected:
  it would replace the lease re-claim and orphan-container sweep that V5 has just
  hardened, days before the Show HN, in exchange for a checkable "built on Satay"
  line. Deferred to the multi-candidate-repair boundary (decision 4).
- **Keep ADR-0001 as written and build the Phase B engine** — rejected: two
  durable execution engines, one maintainer.
- **Declare that sibei-flow builds no engine at all** — rejected as false. V5
  already ships one for repair jobs, so the honest boundary is a capability
  freeze.
- **Merge the two projects** — rejected: they have different users (a data
  engineer with a broken dbt model; a Python developer building an AI feature)
  and each is adoptable without the other. Independent products, one engine.
