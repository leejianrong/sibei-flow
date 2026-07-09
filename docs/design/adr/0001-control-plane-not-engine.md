# ADR-0001: Control plane over our own engine; heavy tools as connectors

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

The unified vision spans ETL/ELT, ML, and agentic workflows. A tempting
shortcut is to build the core on top of Temporal (state), Ray (compute), and
Argo (container orchestration). But these operate at different layers and
combining all three into the core creates conflicting paradigms, an
unmaintainable dependency surface, and cedes the core IP (Temporal would do the
actual orchestration, leaving us a UI wrapper). Argo as an engine also inherits
its pod-per-task latency.

## Decision

Build sibei-flow as a **master control plane above** these tools, with our own
lightweight state/execution engine as the core. Temporal, Ray, and Argo are
**downstream connectors / execution targets**, triggered and monitored by our
brain, not embedded in it:

- **Ray connector** — dispatch ML/GPU compute, monitor, pull results (partner).
- **Argo/K8s connector** — optional isolated container execution, only when a
  user explicitly needs heavy infra isolation.
- **Temporal/webhook connector** — trigger/manage external workflows.

## Consequences

- We own the core IP and keep the platform light enough to run on one VM.
- We must build and maintain our own state engine (see ADR-0009).
- Connectors are additive and can land incrementally; none are in the v1
  critical path (v1 wraps existing pipelines — see ADR-0003).

## Alternatives considered

- **Build on Temporal internally** — rejected: reduces us to a wrapper, cedes IP.
- **Compile to Argo YAML** — rejected: inherits pod-per-task latency.
