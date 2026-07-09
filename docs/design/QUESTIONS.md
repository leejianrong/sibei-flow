# sibei-flow — Grilling Questions

> Scratch file for the "grill with docs" step. Answer by ID, as many per turn
> as you like, in any order. Status: ❓ open · 🔵 answered · ⏭️ deferred.
>
> Legend for why it matters: **[BLOCKER]** shapes v1 scope · **[ADR]** becomes
> an architecture decision record · **[CONTEXT]** defines shared vocabulary.

## A. Problem, users, and the acute pain

- **Q-A1** 🔵 [BLOCKER] First user of v1.
  → **A 3–15-person data team at a Series A/B startup running dbt + Airflow on
  a cloud warehouse (Snowflake/BigQuery/Postgres); 1–2 data engineers, no
  dedicated platform/on-call team.** Has the pain but not the headcount to
  build internal tooling → will try an OSS tool.
- **Q-A2** 🔵 [BLOCKER] The acute pain.
  → **Upstream schema drift and null/type errors break dbt models overnight,
  several times a month.** Dashboard goes stale, noticed hours later, engineer
  burns 30–90 min diagnosing + patching + re-running. Interrupt-driven,
  off-hours, demoralizing.
- **Q-A3** 🔵 [CONTEXT] What does "self-healing" mean in v1?
  → **Always propose-and-approve** (the PR gate). Worker emits a confidence
  score + risk class so an opt-in "auto-merge low-risk" mode is a future config
  flip, not a rewrite.
- **Q-A4** 🔵 Adoption trigger moment.
  → A recurring off-hours breakage (schema drift on dbt) + seeing the Show HN
  demo; the "this happened to me last week" recognition.

## B. Self-healing scope for v1 (the beachhead)

- **Q-B1** 🔵 [BLOCKER] Which failure classes in v1?
  → **v1 = schema drift + code/SQL exceptions.** Defer OOM/resource-mutation
  (highest risk) and data-quality assertions; retry-on-timeout is config, not
  "healing". Rationale: highest LLM fix quality, lowest blast radius, most
  legible before/after diff for the demo.
- **Q-B2** 🔵 [BLOCKER] How is a failure detected?
  → **Primary = webhook/callback on task failure** (Airflow `on_failure_callback`,
  dbt exit codes/artifacts, Dagster run-failure hooks). **Fallback = thin CLI
  wrapper** (`sbflow run -- <cmd>`) for cron/scripts. Log-tailing deferred
  (brittle). One config line, no standing infra access.
- **Q-B3** 🔵 [BLOCKER] Where does the agent get the source?
  → **Read-only from the user's git repo** (scoped token / GitHub App). Never
  writes directly; fixes land as a PR (see B6). Read-to-diagnose, PR-to-fix.
- **Q-B4** 🔵 [ADR] Ephemeral fix-sandbox.
  → **Python + dbt (SQL via `dbt compile`/`dbt build`)** in v1, matching the
  ICP. Runs in an **ephemeral Docker container on the brain's host.** Defer
  Spark / other SQL engines.
- **Q-B5** 🔵 [BLOCKER] How is a fix verified without touching prod?
  → **Tiered:** (1) always compile/lint/parse in the sandbox; (2) run on
  sample / dev-staging target if configured; (3) prod only after human
  approval. Verification evidence shown in the approval.
- **Q-B6** 🔵 [BLOCKER] Approval & apply flow.
  → **Apply = open a Pull Request** against the user's repo. **Approval = the
  PR review**, with a Slack/web notification linking to it + diff + evidence.
  No in-place mutation of running jobs. Auditable, revertible, CI-gated.
- **Q-B7** 🔵 [ADR] Safety boundary.
  → **Hard invariant: sbflow never holds prod-write credentials and never
  writes to `main` or prod tables.** Only write action is opening a PR on a
  branch. Rollback = `git revert` the PR. Blast radius = a reviewable branch.

## C. Integration surface

- **Q-C1** 🔵 [BLOCKER] Day-one integration mechanism.
  → **A single self-hostable service (the "brain") exposing a webhook
  receiver + a thin `sbflow` CLI.** Ship as one Docker image / `docker
  compose up`. Sidecar/K8s-operator packaging is a later option, not the entry
  point.
- **Q-C2** 🔵 Tools supported at launch.
  → **Must: dbt + Airflow**, plus plain cron/scripts via `sbflow run --`.
  Dagster/Prefect are fast-follows.
- **Q-C3** 🔵 Credentials & minimum onboarding.
  → **Read-only git token / GitHub App + LLM API key + optional read-only
  dev/sample warehouse connection.** Onboarding: `docker compose up` → add one
  webhook line → paste git token + LLM key. No prod-write secrets.

## D. Architecture & technology

- **Q-D1** 🔵 [ADR] Core engine language.
  → **Rust.**
- **Q-D2** 🔵 [ADR] State durability.
  → **Durable-but-not-Temporal-grade:** Postgres-backed job state, crash
  recovery, **at-least-once** execution. No exactly-once in v1 (repair jobs are
  human-gated + re-runnable).
- **Q-D3** 🔵 [ADR] LLM strategy.
  → **Provider-agnostic, BYO-key, privacy-first.** No hosted inference in v1;
  data flows only through the user's own provider account. Local /
  OpenAI-compatible models are first-class via a small `LlmProvider`
  interface (not a bolted-on second path). Claude (Sonnet default, escalate to
  Opus on hard cases) is the recommended provider, not a hard dependency.
  → **Agent loop: build our OWN thin Python repair loop** (narrow scope: read
  source → draft diff → sandbox-verify → iterate ≤N → emit PR + confidence),
  as a separable `sbflow-agent` component. Rust "brain" owns
  state/scheduling/webhooks/dispatch; Python worker owns the loop.
  → **Dogfooding path:** the v1 loop is the deliberate SEED of the phase-B
  agentic engine; in phase B the repair agent graduates to run *as* a native
  sbflow workflow. Full dogfooding is a phase-B outcome (the agentic engine
  doesn't exist in v1), not a v1 requirement.
  → **DECIDED: own-loop-first from v1** (not SDK-first). Trades a few weeks of
  build time for provider-agnosticism, local-model support, and the moat at
  launch — and no loop rewrite before phase B. Claude Agent SDK kept only as an
  emergency fallback behind the same worker interface.
- **Q-D4** 🔵 [ADR] Any phase-B data-aware layer in v1?
  → **No.** No Arrow pointers, MCP, or unified state machine in v1. Strictly
  the self-healing wrapper; phase-B primitives deferred until there is
  traction.
- **Q-D5** 🔵 [ADR] Multi-tenancy & RBAC.
  → **Single-team / single-tenant in v1** with simple auth. RBAC +
  multi-tenancy deferred to the enterprise tier (phase B).
- **Q-D6** 🔵 v1 authoring DSL?
  → **No DSL in v1.** v1 wraps existing pipelines; the dual-engine YAML+code
  (REQS §5.3) is deferred to phase B when sibei-flow becomes the orchestrator.

## E. Product & UX

- **Q-E1** 🔵 [BLOCKER] CLI-first vs web.
  → **CLI-first (`sbflow`) + a thin read-only web dashboard** (run history,
  agent transcript, diff, verification evidence). Approval lives in the PR, so
  the web UI has no write actions in v1.
- **Q-E2** 🔵 The "wow" moment.
  → **Rename an upstream column → dbt model fails → within ~90s sbflow opens a
  PR that fixes the model, showing it compiled + ran clean on a sample →
  merge → green.** Runs fully on a laptop (local Postgres + dbt + brain). This
  is the Show HN demo (see G1).
- **Q-E3** 🔵 Transparency.
  → **High by default:** every fix exposes the agent's reasoning transcript,
  the diff, and verification evidence. "Show your work" is a trust feature.

## F. Business, licensing, naming

- **Q-F1** 🔵 [ADR] OSS license.
  → **Apache-2.0 core**, open-core model (enterprise features separately
  licensed later). Permissive core = strongest adoption / "safe to build on"
  signal pre-traction.
- **Q-F2** 🔵 Money motion.
  → **Open-core enterprise tier (self-hosted, customer-operated)** as plan of
  record — most feasible solo (no ops/on-call burden). Gates SSO/SAML, RBAC,
  multi-tenancy, audit, multi-cluster, compliance, priority support. Managed
  cloud = post-funding phase-2. v1 itself is fully free/OSS.
- **Q-F3** 🔵 [CONTEXT] Name.
  → Project **sibei-flow**; CLI **`sbflow`**. Confirmed, no branding issues.

## G. Success & scope boundaries

- **Q-G1** 🔵 v1 success.
  → **Show HN lands on the HN front page** with the schema-drift → auto-PR
  demo. Targets: ~200+ upvotes / top-10; **1,000 GitHub stars in week one**;
  **10 teams self-hosting within the first month**; multiple unsolicited "we
  need this" signals.
- **Q-G2** 🔵 [BLOCKER] Out of scope for v1.
  → OOM/pod-spec mutation · data-quality "healing" · own authoring DSL ·
  unified state machine · data-aware/Arrow/MCP layer · multi-tenancy/RBAC ·
  non-dbt/Airflow integrations · non-git source · in-place prod patching ·
  hosted inference.
- **Q-G3** 🔵 Capacity & timeline.
  → **Appetite = one focused ~6-week build cycle** of core work. Now that
  **own-loop-first** is chosen (Q-D3), expect the calendar to run longer than
  the SDK path — the loop plumbing is extra build. Scope stays protected by a
  small tool surface + capped iterations. *(Still useful to confirm real
  hrs/week to set a target Show HN date.)*
