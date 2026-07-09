# sibei-flow — Shared Context & Vocabulary

> Produced by the "grill with docs" step from `REQS.md` + `QUESTIONS.md`.
> This is the shared language for everything downstream (PRD, shaping,
> breadboarding). Architectural decisions are captured as ADRs in
> `docs/design/adr/` and referenced here.

## 1. What sibei-flow is (one paragraph)

sibei-flow is an open-source, self-hostable **control plane** for data + ML +
agentic workflows. Its long-term thesis is to be the AI-native orchestrator
that owns the "missing middle" between heavy MLOps platforms (Flyte/Kubeflow)
and lightweight agent frameworks (LangChain/n8n). It is delivered in phases:
**v1 is a self-healing layer that rides on the pipelines teams already run**,
and later phases turn it into a full agentic orchestrator.

## 2. The v1 product (the agreed definition)

A single self-hosted service (the **brain**) plus a **`sbflow`** CLI. A user
adds one failure-webhook line to their existing dbt + Airflow setup. When a run
fails from **schema drift** or a **code/SQL error**, sibei-flow reads the
failing source from the user's git repo (read-only), drafts a fix in an
ephemeral sandbox, **compiles and tests it on sample data**, and **opens a pull
request** with the diff, a plain-English explanation, and verification
evidence — pinging the team in Slack/web. The human reviews and merges like any
PR. No DSL, no migration, no prod-write access, no cluster required.

## 3. Glossary (shared terms)

| Term | Meaning in sibei-flow |
|---|---|
| **The brain** | The Rust core service: durable state, scheduling, webhook receiver, and dispatch of repair jobs. The IP we own. See ADR-0002. |
| **Repair worker** | The Python component that runs the agent loop for a single failure: read source → draft fix → sandbox-verify → emit PR + confidence. See ADR-0007. |
| **Repair job** | One unit of "a pipeline task failed → attempt a fix." Human-gated, re-runnable, at-least-once. |
| **Self-healing (v1)** | Always *propose-and-approve*: the tool proposes a fix as a PR; a human approves by merging. Not autonomous in v1. See ADR-0003. |
| **Failure class** | A category of pipeline failure. v1 scope = **schema drift** + **code/SQL exceptions**. Out: OOM/resource, data-quality, timeout. |
| **Schema drift** | An upstream source's shape changed (renamed/removed/retyped/nullable column), breaking a downstream model. The flagship v1 case. |
| **Ephemeral sandbox** | A throwaway Docker container on the brain's host where a candidate fix is compiled and test-run (Python + dbt in v1). See ADR-0006. |
| **Tiered verification** | (1) compile/lint/parse → (2) run on sample / dev target → (3) prod only after human approval. Evidence shown in the PR. See ADR-0006. |
| **Connector** | An integration with a downstream engine sibei-flow sits *above* — Ray (compute), Argo/K8s (isolated containers), Temporal/webhook (external workflows). Not the core. See ADR-0001. |
| **Executor backend** | A pluggable target where work runs: local (Docker Compose/VM), VM, Kubernetes. Selectable; light by default. See ADR-0008. |
| **Provider** | An LLM backend behind the `LlmProvider` interface. BYO-key; Claude is the recommended default, not a hard dependency. See ADR-0007. |
| **Phase C** | v1 — the self-healing wedge (this document's focus). |
| **Phase B** | The moat — agentic + data-aware orchestrator (unified state machine, pointers-not-payloads). Deferred. |
| **Open-core** | Free Apache-2.0 core; enterprise features (SSO, RBAC, multi-tenancy, audit, multi-cluster) licensed separately later. See ADR-0010. |

## 4. Actors

- **Data engineer (primary ICP):** at a 3–15-person data team, Series A/B
  startup, running dbt + Airflow on a cloud warehouse; no dedicated
  platform/on-call team. Feels the pain, adopts without top-down buy-in.
- **The team's reviewer:** whoever merges the PR (often the same engineer).
- **OSS early adopter (channel):** the person who finds the Show HN, stars it,
  and self-hosts to try it.
- **sibei-flow (the system):** the brain + repair worker acting as an
  "AI SRE for data pipelines."

## 5. Positioning one-liner

**"AI SRE for data pipelines: when a dbt/Airflow job breaks, sbflow sends you
the fix as a reviewable pull request."**

## 6. Decision summary (see ADRs for detail)

| # | Decision |
|---|---|
| ADR-0001 | Control plane over our own lightweight engine; Temporal/Ray/Argo are downstream **connectors**, not the core. |
| ADR-0002 | Core "brain" written in **Rust**. |
| ADR-0003 | v1 scope = **self-healing wrapper only** (wedge C → B). No DSL, no phase-B primitives. Propose-and-approve. |
| ADR-0004 | **Webhook-first** failure detection; thin `sbflow run --` CLI wrapper as fallback. |
| ADR-0005 | **Git-read + PR-based apply.** Hard invariant: never holds prod-write creds, never writes to `main`/prod. |
| ADR-0006 | **Tiered verification** (compile → sample → prod-after-approval); sandbox = Python + dbt in a local ephemeral container. |
| ADR-0007 | **Provider-agnostic, BYO-key, privacy-first** LLM strategy; **own-loop-first** — build our own thin **Python repair loop** from v1 (SDK only as emergency fallback); Rust brain ⇄ Python worker. |
| ADR-0008 | **Pluggable executor backends** (local / VM / K8s) from day one; light by default. |
| ADR-0009 | State engine: **Postgres-backed, at-least-once**, crash recovery; not exactly-once in v1. |
| ADR-0010 | **Apache-2.0** core; **open-core enterprise tier** (self-hosted) as the monetization plan of record. |

## 7. Success definition (v1)

Show HN lands on the HN front page with the schema-drift → auto-PR demo.
Targets: ~200+ upvotes / top-10; 1,000 GitHub stars in week one; 10 teams
self-hosting within a month; multiple unsolicited "we need this" signals.
Appetite: one focused ~6-week build cycle (~10 calendar weeks at ~15 hrs/wk).

## 8. Explicitly out of scope for v1

OOM/pod-spec mutation · data-quality "healing" · own authoring DSL · unified
state machine · data-aware/Arrow/MCP layer · multi-tenancy/RBAC · non-dbt/
Airflow integrations · non-git source · in-place prod patching · hosted
inference · Slack/chat notifications (fast-follow) · VM/K8s executor backends
(seam only in v1).

## 9. Open items still to confirm

- Real build capacity (hrs/week) — decides own-loop-first vs SDK-first in v1.
- Telemetry/opt-in mechanism for measuring "teams self-hosting."
