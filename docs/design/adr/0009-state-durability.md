# ADR-0009: Postgres-backed, at-least-once state

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

We own the state engine (ADR-0001). Durability guarantees trade directly against
design complexity. Full exactly-once/Temporal-grade semantics are expensive to
build and unnecessary for v1's workload.

## Decision

v1 state engine is **Postgres-backed** with **crash recovery** and
**at-least-once** execution of repair jobs. **No exactly-once** semantics in v1.
(Redis may back ephemeral/queue concerns; Postgres is the source of truth.)

## Consequences

- Repair jobs must be **idempotent / re-runnable** — acceptable because they are
  human-gated (a duplicate just produces another PR proposal) (ADR-0003/0005).
- Simpler schema and faster to build than exactly-once.
- Exactly-once and durable long-running workflow semantics are revisited in
  phase B when sibei-flow owns execution of arbitrary user workflows.

## Alternatives considered

- **Exactly-once / Temporal-grade in v1** — rejected: over-engineering for a
  human-gated, re-runnable workload.
