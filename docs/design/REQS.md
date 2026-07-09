# sibei-flow — Initial Requirements (REQS)

> Captured initial idea. This is the seed document for the product-planning
> pipeline (grill → PRD → shaping → breadboarding). It records intent and
> decisions made so far, not a final spec. Open questions are flagged.

## 1. One-liner

An open-source, self-hostable orchestration **control plane** that unifies
data (ETL/ELT), machine learning, and **agentic** workflows — lightweight
enough to run on a single VM, able to scale out to Kubernetes, and AI-native
from the core rather than bolted on.

## 2. The gap being exploited

The market is siloed into three camps, none of which own the intersection:

- **Data orchestrators** (Airflow, Prefect, Dagster) — deterministic batch
  schedulers, structurally ill-equipped for long-running, non-deterministic
  agentic loops.
- **MLOps platforms** (Flyte, Kubeflow) — powerful but carry a heavy
  "Kubernetes tax" and pod-per-task latency; unergonomic for nimble,
  API-driven agentic work.
- **Agentic frameworks** (LangChain, CrewAI, n8n) — great at agent logic and
  tool-calling, but not big-data platforms; they fall over on GB-scale ELT or
  GPU model training.

**The wedge into the "missing middle":** a pragmatic, AI-native orchestrator
that is light to deploy (unlike Flyte), scalable and code-capable (unlike
Kestra's "YAML wall"), and data-aware (unlike agent frameworks).

Primary competitors to benchmark against: **Kestra** and **Flyte**.

## 3. Target audience

- **Primary ICP:** Data engineers — they feel the acute pain directly
  (3am breakages, schema drift, brittle SQL) and can adopt without top-down
  buy-in.
- **Beachhead channel:** OSS early adopters (Show HN, GitHub, r/dataengineering)
  — the cheapest distribution available to a solo founder.
- **Expand later:** ML/AI engineers (phase 2), then platform/DevOps teams
  (post-funding, enterprise).

## 4. Product strategy — wedge sequence

Same long-term vision, sequenced so early traction funds the hard primitives.

### Phase C (v1 — beachhead): Self-Healing Pipelines

A drop-in layer that wraps **any** existing pipeline (Airflow, dbt, cron).
On failure it captures the traceback, spins an ephemeral sandbox, proposes a
fix (rewrites the failing SQL/Python step), tests it, and surfaces a
human-in-the-loop approval in a web UI before applying.

- Smallest surface to build; integrates as sidecar/webhook — no migration
  required to adopt.
- Most dramatic, demoable value; solves acute pain.
- Positioning: **"AI SRE for data pipelines."**
- The repair loop is itself an agent loop (well-suited to building with
  Claude Code).

### Phase B (vision — the moat): Agentic + Data-Aware Orchestrator

- A **unified state machine** that treats a 3-hour agent step and a 50ms SQL
  task as first-class citizens of the same graph.
- **Data-aware tool calling (pointers, not payloads):** agents receive Arrow
  schema + a data pointer, reason over metadata, and dispatch heavy compute to
  downstream engines — never routing gigabytes through the LLM (MCP-style).
- Built-in **human-in-the-loop breakpoints** with state-pausing.

## 5. Architecture decisions (made so far)

### 5.1 Control plane, not a wrapper

sibei-flow sits **above** heavy engines as the master orchestrator. It does
**not** build its core on Temporal, Ray, or Argo (that would be an
over-engineered clash of paradigms and cede the core IP).

- **The Brain (own engine):** a lightweight state/execution engine
  (Postgres/Redis-backed, fast shared worker pool). Owns schedules, graph
  routing, lightweight data mapping, and sub-second execution for light tasks
  (SQL, API calls, conditionals).
- **Connectors (downstream execution targets):**
  - **Ray connector** — dispatch ML/GPU compute jobs, monitor, pull results.
  - **Argo / Kubernetes connector** — optional isolated container execution,
    used only when a user explicitly needs heavy infra isolation (avoids
    inheriting Argo's pod-per-task latency by default).
  - **Temporal / webhook connector** — trigger/manage external workflows for
    microservice teams.

### 5.2 Pluggable runtime from day one

The executor is an abstraction with multiple backends — **local (Docker
Compose / single VM)**, **VM**, and **Kubernetes** — selectable per task.
Light out of the box; scale to K8s only when hooked up. Directly attacks
Flyte's "Kubernetes tax."

### 5.3 Dual-engine authoring

Users author declaratively (YAML) for structure and drop into code-first
(inline Python/TS) when logic gets complex — pivoting without context
switching. Attacks Kestra's "YAML wall." Aim for strong linting/typed
validation and a local testing story.

### 5.4 Secure-by-default OSS core

Generous OSS edition includes basic RBAC and multi-tenancy; gate paid tiers
behind high-scale enterprise needs (compliance auditing, multi-cluster sync,
managed infra) rather than entry-level security. Attacks Kestra's paywalling
of basic enterprise needs.

## 6. Candidate AI agent capabilities (backlog, for later phases)

Not all in v1 — recorded for roadmap consideration:

- **Self-healing / dynamic code correction** (v1 core) — catch traceback,
  sandbox-rewrite-test, request sign-off.
- **Autonomous resource routing** — on OOM, mutate pod spec (node affinity,
  RAM/GPU) and restart.
- **Schema-drift management** — detect upstream schema changes, map columns,
  draft migrations and transform-logic updates.
- **Contextual data profiling & cleaning** — reason over samples, detect
  anomalies, draft/execute cleaning transforms.
- **Schema & logic translation** — e.g. legacy T-SQL → PL/pgSQL or PySpark.
- **Multi-agent migration verification** — analyzer / optimizer / rollback
  sub-agent panel.
- **Agentic data curation & synthetics** — build/label training + eval data.
- **Intelligent ML strategy exploration** — HPO, model comparison,
  drift-triggered retraining.

## 7. Founder / build context

Solo developer building with Claude Code. Goals, in order: **early adoption**
→ **funding**. This biases toward small buildable surface, viral demos, and
low adoption friction first (hence wedge C), with the fundable moat (wedge B)
sequenced behind it.

## 8. Open questions (to resolve during grilling / ADRs)

- **Engine language:** Go vs Rust for the core state engine.
- **Confirm audience:** data engineers (ICP) + OSS early adopters (channel) —
  recommended, pending final confirmation.
- **v1 scope of "self-healing":** which failure classes are in scope for the
  first release (schema drift? OOM? API timeout? all?).
- **Sandbox execution model:** how ephemeral fix-sandboxes are isolated and
  what languages/engines they cover in v1.
- **How agents interact with existing pipelines:** integration surface for
  wrapping Airflow/dbt/cron (webhook, sidecar, log tailing?).
- **Name / branding:** "sibei-flow" — confirm.
- **License:** which OSS license supports the secure-by-default + paid-tier
  model.
