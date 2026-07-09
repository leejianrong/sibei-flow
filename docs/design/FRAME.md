---
shaping: true
---

# sibei-flow v1 — Frame

> The "why" for the v1 shaping effort, at stakeholder level. Distilled from
> `REQS.md` (raw seed), with detail from `PRD.md` and `CONTEXT.md`. The ten
> ADRs in `adr/` are **locked constraints**, not re-opened here.
>
> **Scope of this frame:** Phase C only — the self-healing wedge. The larger
> phase-B vision (unified agentic/data-aware orchestrator) is context for
> *why this wedge*, not part of what we shape now.

---

## Source

Verbatim material that prompted this work. The founder's own framing of the
gap and the wedge, captured before any solutioning.

> **One-liner (REQS §1):** An open-source, self-hostable orchestration
> **control plane** that unifies data (ETL/ELT), machine learning, and
> **agentic** workflows — lightweight enough to run on a single VM, able to
> scale out to Kubernetes, and AI-native from the core rather than bolted on.

> **The gap (REQS §2):** The market is siloed into three camps, none of which
> own the intersection: data orchestrators (Airflow, Prefect, Dagster) —
> deterministic batch schedulers, ill-equipped for long-running agentic loops;
> MLOps platforms (Flyte, Kubeflow) — powerful but carry a heavy "Kubernetes
> tax" and pod-per-task latency; agentic frameworks (LangChain, CrewAI, n8n) —
> great at agent logic but fall over on GB-scale ELT or GPU training. **The
> wedge into the "missing middle":** a pragmatic, AI-native orchestrator that
> is light to deploy, scalable and code-capable, and data-aware.

> **The wedge sequence (REQS §4, Phase C):** Self-Healing Pipelines — a drop-in
> layer that wraps **any** existing pipeline (Airflow, dbt, cron). On failure
> it captures the traceback, spins an ephemeral sandbox, proposes a fix
> (rewrites the failing SQL/Python step), tests it, and surfaces a
> human-in-the-loop approval in a web UI before applying. Positioning:
> **"AI SRE for data pipelines."**

> **The originating market insight (RAW.md):** "If a PySpark, SQL, or Python
> step throws a memory error, a schema mismatch, or an API timeout, the agent
> catches the traceback. It spins up an isolated ephemeral sandbox, rewrites
> the failing code, tests it, and requests human sign-off to push the patch."

> **Founder context (REQS §7):** Solo developer building with Claude Code.
> Goals, in order: **early adoption → funding**. Biases toward a small
> buildable surface, viral demos, and low adoption friction first (wedge C),
> with the fundable moat (wedge B) sequenced behind it.

---

## Problem

A 3–15-person data team at a Series A/B startup runs **dbt on top of Airflow**
against a cloud warehouse (Snowflake / BigQuery / Postgres). There are one or
two data engineers and **no dedicated platform or on-call team**. They feel the
pain of broken pipelines acutely but lack the headcount to build internal
tooling for it.

Several times a month an **upstream source changes shape** — a column renamed,
removed, retyped, or turned nullable (**schema drift**) — or a **code/SQL
error** slips in, and a dbt model breaks overnight. Nobody is watching at 3am.
The break is found hours later because a dashboard went stale. An engineer then
burns 30–90 minutes on an interrupt-driven, off-hours loop: read the traceback,
find the failing model, work out what upstream changed, patch the SQL/Python,
re-run. It is demoralizing, it recurs, and it scales badly as models multiply.

**Why nothing today closes the gap:**

- Airflow and dbt tell the team *that* something broke — never *what the fix
  is*.
- MLOps platforms (Flyte/Kubeflow) and agent frameworks (LangChain/n8n) do not
  address this class of pain at all, and the heavier tools demand a Kubernetes
  cluster and a DevOps hire the team does not have.
- The team will **not** adopt anything that requires a migration, a new DSL, or
  handing a tool write-access to production.

The core tension: the fix is usually small and mechanical, but discovering and
applying it is expensive, off-hours, and human-gated — and any tool that helps
must earn trust without ever being allowed near production credentials.

---

## Outcome

**What success looks like — the v1 "wow":** a data team wires sibei-flow into
their existing dbt-in-Airflow setup with **one config line** and a
`docker compose up`. When a run breaks from schema drift or a code/SQL error,
**within ~90 seconds** the team receives a **reviewable Pull Request** that
already contains the fix, a plain-English explanation of what changed upstream,
the agent's reasoning transcript, verification evidence (*compiled ✓ · ran on a
sample ✓ · output schema unchanged ✓*), and a confidence/risk label. Approving
is nothing more than merging the PR; declining is closing it; rolling back is
`git revert`.

**The outcome holds these properties (the non-negotiables):**

- **No migration, no DSL, no cluster** — it rides on the pipelines the team
  already runs; adoption is additive and reversible.
- **Trust by construction** — the tool never holds prod-write credentials and
  never writes to `main` or prod tables; the only write action anywhere is
  opening a PR on a branch. A fix is *verified before a human sees it*.
- **Propose-and-approve, never autonomous** — the human merge is the sole path
  to production in v1.
- **Runs light** — a single VM or a laptop; no DevOps hire required.
- **Show your work** — every fix exposes its diff, reasoning, and evidence, so
  the team can trust it without re-running it by hand.

**Success signal (business):** the Show HN lands on the HN front page with the
schema-drift → auto-PR demo — ~200+ upvotes / top-10, ~1,000 GitHub stars in
week one, ~10 teams self-hosting within a month, and multiple unsolicited
"we need this" reactions. **Appetite:** one focused ~6-week core build cycle
(~10 calendar weeks at ~15 hrs/wk).

**Explicitly *not* the outcome for v1** (deferred, per CONTEXT §8 / PRD "Out of
Scope"): additional failure classes (OOM/resource, data-quality, timeout); any
autonomy/auto-merge; any prod mutation; orchestrators beyond dbt-in-Airflow;
the phase-B primitives (authoring DSL, unified state machine, Arrow/MCP
data-aware layer); multi-tenancy/RBAC; hosted inference; Slack/chat
notifications.

---

## Locked constraints (ADRs — do not re-open during shaping)

These bound the solution space. Shapes must live **inside** them.

| ADR | Constraint the shaping must respect |
|---|---|
| 0001 | Control plane over our **own lightweight engine**; Temporal/Ray/Argo are downstream connectors, not the core. |
| 0002 | The core "brain" is written in **Rust**. |
| 0003 | v1 = **self-healing wrapper only**, propose-and-approve; no phase-B primitives. |
| 0004 | **Webhook-first** failure detection; `sbflow run --` CLI wrapper as fallback. |
| 0005 | **Git-read + PR-based apply.** Hard invariant: never holds prod-write creds, never writes to `main`/prod. |
| 0006 | **Tiered verification** (compile → sample → prod-after-approval) in an ephemeral Docker sandbox (Python + dbt). |
| 0007 | Provider-agnostic, BYO-key LLM; **own thin Python repair loop** (SDK only as emergency fallback); Rust brain ⇄ Python worker. |
| 0008 | **Pluggable executor backends**; only the **local** backend implemented in v1 (VM/K8s are seams). |
| 0009 | **Postgres-backed, at-least-once** state with crash recovery; not exactly-once in v1. |
| 0010 | **Apache-2.0** core; open-core enterprise tier deferred. |

---

## Open items carried into shaping (from CONTEXT §9)

- Real build capacity (hrs/week) — the appetite check behind own-loop-first.
- Telemetry/opt-in mechanism for measuring "teams self-hosting."
