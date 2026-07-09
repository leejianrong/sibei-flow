# ADR-0007: Provider-agnostic BYO-key LLM; our own Python repair loop

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

The LLM *is* the product in v1 — bad patches destroy trust. Two priorities are
firm: **BYO-key + data privacy** (the agent sees the user's source, schema, and
sample data) and **provider-agnosticism / local-model support**. There is also
a strategic desire to own the agent loop as the seed of the phase-B agentic
engine ("dogfooding").

## Decision

- **Provider-agnostic, BYO-key, privacy-first.** No hosted inference in v1; data
  flows only through the user's own provider account. Local / OpenAI-compatible
  models are first-class behind a small **`LlmProvider`** interface. **Claude**
  (Sonnet default, escalate to Opus on hard cases) is the *recommended*
  provider, not a hard dependency.
- **Build our own thin Python repair loop** — narrow scope: read source → draft
  diff → sandbox-verify → iterate ≤N → emit PR + confidence — as a separable
  `sbflow-agent` component. The **Rust brain** owns state/scheduling/webhooks/
  dispatch; the **Python worker** owns the loop (see ADR-0002).
- **Dogfooding path:** the v1 loop is the deliberate *seed* of the phase-B
  agentic engine; in phase B the repair agent graduates to run *as* a native
  sibei-flow workflow. Full dogfooding is a phase-B outcome, not a v1
  requirement (that engine doesn't exist in v1).
- **Decision: own-loop from v1 (own-loop-first).** Build the Python repair loop
  directly for v1 rather than adopting the Claude Agent SDK. This puts
  provider-agnosticism, local-model support, and the moat in place *at launch*
  and avoids rewriting the loop before phase B. The Claude Agent SDK remains a
  documented **emergency fallback** (behind the same worker interface) only if
  capacity collapses — it is explicitly **not** the default.

## Consequences

- Privacy and provider goals are met natively, not bolted on.
- Owning the loop keeps the moat in-house and *is* the phase-B engine's core, so
  the dogfooding graduation needs no loop rewrite.
- **Costs more upfront build time** (the loop plumbing: message-passing,
  tool-call dispatch, context management, retries) — bounded by the narrow task
  scope. The v1 timeline extends and/or scope tightens to absorb this; the
  appetite is protected by keeping the tool surface small (`read_file`,
  `edit_file`, `run_sandbox`, `get_schema`) and iteration capped at ≤N attempts.

## Alternatives considered

- **SDK-first (ship on the Claude Agent SDK, swap later)** — rejected as the
  default: faster to demo, but delays provider-agnosticism/local models and
  forces a loop rewrite before phase B. Retained only as an emergency fallback.
- **Pure-Rust agent loop** — rejected: weak LLM ecosystem, reinvents plumbing.
