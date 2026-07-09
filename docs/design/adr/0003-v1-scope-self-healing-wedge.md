# ADR-0003: v1 scope = self-healing wedge (C → B)

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

The full vision is large. Building the orchestrator first (own DSL, unified
state machine, data-aware layer) means rebuilding Kestra/Flyte before earning a
single user — the vision-vaporware trap. We need early traction that funds the
hard moat.

## Decision

Ship v1 as **strictly the self-healing layer over users' existing pipelines**
(wedge C), and sequence the agentic + data-aware moat (wedge B) behind it.
v1 explicitly **excludes**: an authoring DSL, the unified state machine, the
data-aware/Arrow/MCP layer, and autonomous fixes. Self-healing in v1 is always
**propose-and-approve** (a human merges the PR); the repair worker emits a
confidence score + risk class so an opt-in auto-merge mode is a later config
flip, not a rewrite.

## Consequences

- Tiny, buildable surface; zero-migration adoption (users keep dbt+Airflow).
- The demo is relatable and safe (see ADR-0005).
- Phase-B primitives must be deliberately *seeded* (esp. the repair loop —
  ADR-0007) so the graduation to the orchestrator is natural, not a rewrite.

## Alternatives considered

- **Orchestrator-first / all-pillars-thin** — rejected: crowded, no moat, or
  spread too thin for a solo dev (see the wedge decision brief).
