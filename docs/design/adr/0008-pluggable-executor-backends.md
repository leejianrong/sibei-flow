# ADR-0008: Pluggable executor backends (local / VM / K8s)

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

Flyte's "Kubernetes tax" is a key weakness to exploit: it demands a cluster and
a DevOps engineer before you can do anything. We want to run light out of the
box but still scale to K8s when a user needs it.

## Decision

Define the executor behind a **minimal trait/abstraction from day one**, but in
v1 **implement only the local backend** (Docker Compose / single VM). VM and
Kubernetes backends are *designed-for* (the seam exists) but **deferred** —
selectable later without reworking the core. Light by default; Kubernetes is
opt-in, engaged only when explicitly hooked up.

## Consequences

- `docker compose up` gets a user running with no cluster (matches ADR-0004/0006
  onboarding).
- v1 build cost is only the local backend + a thin seam — avoids gold-plating
  three backends inside a ~6-week appetite (resolves tension T2).
- VM/K8s backends and the Argo/K8s connector (ADR-0001, heavy-isolation) slot in
  behind the existing trait post-v1.

## Alternatives considered

- **K8s-native first** — rejected: concedes Flyte's exact weakness and blocks
  the single-VM adoption story.
