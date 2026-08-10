# ADR-0013: `RepairResult.transcript` becomes a tagged union

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Jian (leejianrong2@gmail.com)

Changes one field of the frozen `RepairResult` contract, as `CLAUDE.md` requires
an ADR to do. Follows from [ADR-0012](0012-no-second-execution-engine.md).

## Context

The reasoning transcript is currently built by hand. `loop.py` declares
`transcript: list[str] = []` and appends to it as the loop runs — a line per
assistant turn, a line per tool call (`f"→ {tc.name}({tc.input})"`), and a line
per tool result clipped to `_TRANSCRIPT_CLIP`. The list rides in the
`RepairResult` dict, is written to `repair_jobs`, and `brain/src/pr/body.rs` reads
it back with `.as_array()` to render the collapsible "🧠 Reasoning transcript"
block in the PR body.

That makes the transcript a **parallel artifact**: the loop does the work, then
separately writes a description of the work. Two problems follow from the shape
rather than from the implementation.

**It is lossy by construction.** The clip applies to tool output, so
`get_schema`'s result — the one piece of evidence that justifies the whole fix —
can be truncated at exactly the point a reviewer looks to check the reasoning.

**It can drift from what happened.** Add a tool call and forget the
`transcript.append`, and the PR body describes a run that did not occur. Nothing
enforces the correspondence, because nothing depends on it. It is a log, not a
record.

Under ADR-0012 the eventual answer is a Satay journal, which records inputs,
outputs, attempt counts, retry reasons, durations, native tracebacks and model
usage, spills payloads over 256 KiB to content-addressed blobs so nothing needs
clipping, and cannot drift because replay depends on it. But that requires the
port, which ADR-0012 deliberately defers.

What cannot be deferred is the contract. `RepairResult` is declared frozen and
gains third-party readers the moment v1 launches: the dashboard renders it, the
read API exposes it, and forks depend on it. Changing it now is an edit. Changing
it after the launch is a migration with users attached.

## Decision

**Widen the field to a discriminated union now, before the v1 launch, and keep
emitting the existing shape.**

```
transcript?: {"kind": "lines",   "lines": list[str]}
           | {"kind": "journal", "run_id": str, "ref": str}
```

- The worker emits **only** `kind: "lines"` until the port lands. Behaviour is
  unchanged.
- `brain/src/pr/body.rs` learns both branches. The `journal` arm may render a
  placeholder until there is a journal to render.
- The dashboard and read API handle both shapes.
- **No Satay dependency is introduced by this ADR.** It buys the option; ADR-0012
  decides when the option gets exercised.

The exact key names are the implementation's call, provided the union is
discriminated by an explicit tag rather than by structural sniffing. A reader
must never have to guess which variant it holds.

## Consequences

- One small worker change and one small Rust change, plus the fixture updates in
  `body.rs`'s tests (which currently assert a bare array).
- Once the port lands, the PR body becomes a **render of the journal** rather than
  a second artifact that can disagree with it.
- The `transcript?` entry in `CLAUDE.md`'s frozen-contract list changes shape.
  That list is updated in the same change.
- Doing this after the launch instead would be a breaking migration against
  unknown readers, for no benefit. The whole argument for the timing is that
  asymmetry.

## Alternatives considered

- **Leave it as `list[str]` and migrate later** — rejected: frozen contracts are
  cheap to change before a launch and expensive after, and this one is four days
  from having external readers.
- **Go straight to journal-only** — rejected: requires the port, which ADR-0012
  defers to the multi-candidate-repair boundary.
- **Keep `list[str]` and add a separate `journal_ref?` field** — rejected: two
  fields where one is authoritative invites both being populated and disagreeing,
  which is the drift problem again with extra steps.
