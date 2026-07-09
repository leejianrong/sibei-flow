# sibei-flow v1 — Product Requirements (PRD)

> **Scope:** Phase C — the self-healing wedge. "AI SRE for data pipelines: when
> a dbt/Airflow job breaks, `sbflow` sends you the fix as a reviewable pull
> request."
>
> **Grounded in:** `CONTEXT.md`, `REQS.md`, `QUESTIONS.md`. Architecture is
> fixed by ADR-0001 … ADR-0010 in `docs/design/adr/` — this PRD builds on those
> as constraints and does **not** re-open them. ADRs are referenced by number,
> not restated.

---

## Problem Statement

A 3–15-person data team at a Series A/B startup runs dbt on top of Airflow
against a cloud warehouse (Snowflake / BigQuery / Postgres). There are one or
two data engineers and **no dedicated platform or on-call team**. They feel the
pain of broken pipelines acutely but don't have the headcount to build internal
tooling for it.

Several times a month, an **upstream source changes shape** — a column is
renamed, removed, retyped, or turns nullable (**schema drift**) — or a
**code/SQL error** slips in, and a dbt model breaks overnight. Nobody is
watching at 3am. The failure is discovered hours later, usually because a
dashboard has gone stale. A data engineer then burns 30–90 minutes on an
interrupt-driven, off-hours loop: read the traceback, find the failing model,
figure out what upstream changed, patch the SQL/Python, and re-run. It's
demoralizing, it recurs, and it scales badly as the number of models grows.

Existing orchestrators (Airflow, dbt) tell the team *that* something broke.
Nothing tells them *what the fix is*. MLOps platforms (Flyte/Kubeflow) and agent
frameworks (LangChain/n8n) don't address this at all, and heavier tools demand a
Kubernetes cluster and a DevOps hire the team doesn't have. The team will not
adopt anything that requires a migration, a new DSL, or handing a tool
write-access to production.

## Solution

sibei-flow v1 is a **single self-hosted service (the brain) plus a `sbflow`
CLI** that rides on the pipelines the team already runs. Adoption is one
`docker compose up` and **one config line** added to their existing dbt-in-
Airflow setup — no migration, no DSL, no cluster.

When a run fails from schema drift or a code/SQL error, sibei-flow:

1. **Detects** the failure via a webhook/callback from the orchestrator
   (ADR-0004), with a thin `sbflow run -- <cmd>` CLI wrapper as the fallback for
   cron/scripts.
2. **Reads** the failing source **read-only** from the team's git repo
   (ADR-0005) — it never holds prod-write credentials and never writes to `main`
   or production tables.
3. **Drafts a fix** using a thin, in-house **Python repair worker** — an agent
   loop that reads source → drafts a diff → verifies it → iterates up to a
   capped N attempts (ADR-0007). The LLM is provider-agnostic and BYO-key
   (Claude recommended, local/OpenAI-compatible models first-class); no data
   leaves the user's own provider account.
4. **Verifies** the candidate fix in an **ephemeral Docker sandbox** on the
   brain's host, in tiers (ADR-0006): always compile/lint/parse (Python + dbt,
   SQL via `dbt compile`/`dbt build`); optionally run on a sample or dev/staging
   target if the user configured a read-only connection; prod only ever after a
   human merge.
5. **Opens a Pull Request** (the only write action, ADR-0005) containing the
   diff, a **plain-English explanation**, the agent's **reasoning transcript**,
   and **verification evidence** ("compiled ✓ · ran on 10k-row sample ✓ · output
   schema unchanged ✓"), plus a **confidence score and risk class**.
6. **Surfaces it** to the team via the PR itself and a **read-only web
   dashboard** (run history, transcript, diff, evidence). (Slack/chat
   notifications are a fast-follow, out of v1 scope.)

The human reviews and **merges the PR like any other** — that merge *is* the
approval and the only path to production (**propose-and-approve**; never
autonomous in v1, ADR-0003). Rollback is `git revert`. The whole thing runs
light on a single VM or a laptop.

**Component shape (per ADRs):**
- **The brain** — Rust core (ADR-0002): durable Postgres-backed state (ADR-0009,
  at-least-once, crash recovery), webhook receiver (ADR-0004), scheduling, and
  dispatch of repair jobs to the worker. Executor behind a minimal trait with
  only the **local** backend implemented in v1 (ADR-0008).
- **The repair worker** — separable Python `sbflow-agent` component (ADR-0007):
  owns the agent loop and a small tool surface (`read_file`, `edit_file`,
  `run_sandbox`, `get_schema`) behind the `LlmProvider` interface.
- **The CLI (`sbflow`)** — onboarding, the `sbflow run --` wrapper fallback, and
  local operation.
- **The web UI** — thin, **read-only**; no write actions (approval lives in the
  PR).

## User Stories

**Detection & onboarding**

1. As a data engineer, I want to add a single webhook line to my Airflow
   `on_failure_callback`, so that sibei-flow learns about failures without me
   granting it standing access to my infrastructure.
2. As a data engineer, I want to stand the whole thing up with `docker compose
   up`, so that I can try it on my laptop with no cluster and no DevOps work.
3. As a data engineer, I want to paste a read-only git token (or install a
   GitHub App) and an LLM API key during onboarding, so that the agent can read
   my source and reason without me handing over prod-write secrets.
4. As a data engineer, I want to optionally provide a read-only dev/sample
   warehouse connection, so that fixes can be verified against real-shaped data
   before I see them.
5. As a data engineer running dbt inside Airflow, I want that combination
   supported first-class at launch, so that the flagship path matches how my
   team actually runs.
6. As a data engineer with a cron/script step that has no failure callback, I
   want to wrap it in `sbflow run -- <cmd>`, so that those failures are captured
   too.
7. As a data engineer, I want failure detection to hand sibei-flow a structured
   payload (task id, error, context), so that diagnosis starts from real signal
   rather than scraped logs.

**The auto-PR fix (primary: the engineer who receives it)**

8. As a data engineer, I want an upstream column rename that breaks my dbt model
   to result in an automatically drafted fix, so that I don't have to diagnose
   it by hand at 3am.
9. As a data engineer, I want the fix to arrive as a normal Pull Request, so
   that it flows through my existing review, CI, and audit process unchanged.
10. As a data engineer, I want the PR to include a plain-English explanation of
    what changed upstream and why the fix addresses it, so that I can understand
    it in seconds.
11. As a data engineer, I want the PR to carry verification evidence (compiled,
    ran on a sample, output schema unchanged), so that I trust the fix without
    running it myself first.
12. As a data engineer, I want the agent's full reasoning transcript attached,
    so that I can audit *how* the fix was derived, not just the result.
13. As a data engineer, I want a confidence score and a risk class on each fix,
    so that I can calibrate how carefully to review it.
14. As a data engineer, I want the fix to appear within roughly 90 seconds of
    the failure, so that a stale dashboard is corrected before the business
    notices.
15. As a data engineer, I want fixes scoped to schema drift and code/SQL errors
    only, so that the tool stays in its lane and I'm not handed risky
    resource/OOM changes.
16. As a data engineer, I want a fix that would require a prod-side action (e.g.
    a data backfill) surfaced as a **recommendation only**, so that sibei-flow
    never quietly assumes it can touch prod.
17. As a data engineer, I want sibei-flow to never open a PR when it couldn't
    even get the fix to compile, so that my review queue isn't polluted with
    junk.

**The reviewer**

18. As a reviewer, I want approval to be nothing more than merging the PR, so
    that there's no second tool or approval surface to learn.
19. As a reviewer, I want the diff to be minimal and legible, so that I can
    reason about blast radius quickly.
20. As a reviewer, I want to reject a fix simply by closing the PR, so that
    declining is as cheap as approving.
21. As a reviewer, I want to roll back a merged fix with `git revert`, so that
    the blast radius of any mistake is just a reviewable branch.
22. As a reviewer, I want confidence that the tool holds no prod-write
    credentials and cannot write to `main`, so that I can approve adopting it
    without a security review veto.

**Web dashboard (read-only)**

23. As a data engineer, I want a run history of every failure sibei-flow saw and
    what it did, so that I have one place to see the tool's activity.
24. As a data engineer, I want to open any run and see the transcript, diff, and
    evidence in the browser, so that I can review without hunting through the
    PR.
25. As a data engineer, I want the web UI to be explicitly read-only, so that
    there's no ambiguity about where approval happens (the PR).

**Operation, durability, and trust**

26. As a data engineer, I want a repair job to survive a brain restart mid-run,
    so that a crash doesn't silently drop a failure on the floor (ADR-0009).
27. As a data engineer, I want a re-delivered or duplicate failure to at worst
    produce a second PR proposal, never a corrupted state, so that at-least-once
    delivery is safe (ADR-0009).
28. As a data engineer, I want to bring my own LLM provider (including a local
    or OpenAI-compatible model), so that my source, schema, and sample data
    never leave an account I control (ADR-0007).
29. As a data engineer, I want the agent to give up cleanly after N failed
    attempts and tell me it couldn't fix this one, so that it doesn't loop or
    ship a low-quality guess.

**OSS adopter (channel)**

30. As an OSS early adopter, I want to reproduce the schema-drift → auto-PR demo
    on my laptop from the README, so that I can verify the claim before
    trusting it on real pipelines.
31. As an OSS early adopter, I want an Apache-2.0 license, so that I can adopt
    and build on it without legal friction (ADR-0010).

## Implementation Decisions

All architecture is fixed by the ADRs; this section states the product-level
build decisions that sit on top of them.

- **Two components across a first-class boundary.** The Rust **brain** (state,
  scheduling, webhook receiver, dispatch) and the Python **repair worker** (the
  agent loop) are separate processes communicating over a defined contract
  (ADR-0002, ADR-0007). The brain never runs LLM logic; the worker never owns
  durable state. This boundary is the primary integration seam and is designed
  to remain stable into phase B.

- **The repair-job contract.** The brain dispatches a **repair job** — a unit of
  "a pipeline task failed → attempt a fix" — carrying the structured failure
  payload (task/model id, error text, orchestrator/run context) and the
  read-only git ref to diagnose against. The worker returns a **repair result**:
  `{ diff, explanation, transcript, verification_evidence, confidence,
  risk_class, outcome }` where `outcome ∈ {pr_proposed, no_fix, needs_prod_action}`.
  Repair jobs are **idempotent / re-runnable** because they are human-gated
  (ADR-0009): a duplicate simply yields another PR proposal.

- **Failure detection surface.** Primary: an HTTP **webhook receiver** on the
  brain that accepts the orchestrator's failure callback (Airflow
  `on_failure_callback`; dbt run-results/exit codes). Fallback: the `sbflow run
  -- <cmd>` CLI wrapper that captures non-zero exits from cron/scripts and posts
  the same payload shape (ADR-0004). **v1 flagship integration = dbt running
  inside Airflow.**

- **Source access is read-only.** The worker reads source via a scoped git token
  or GitHub App. The **only** write action anywhere in the system is opening a
  PR on a branch (ADR-0005). The hard invariant — no prod-write creds, no writes
  to `main`/prod — is enforced structurally: the system is never given
  credentials capable of it.

- **The agent loop.** A thin, own-built Python loop (not the Claude Agent SDK,
  which is retained only as an emergency fallback behind the same worker
  interface — ADR-0007). Narrow tool surface: `read_file`, `edit_file`,
  `run_sandbox`, `get_schema`. Iteration is **capped at ≤N attempts** to protect
  the appetite and prevent runaway loops. Loop shape:

  ```
  read failing source + schema + error
    → draft diff
    → run_sandbox (tier-1 compile; tier-2 sample if configured)
    → if pass: emit repair_result{outcome: pr_proposed, evidence, confidence}
    → if fail and attempts < N: feed evidence back, redraft
    → if attempts == N: emit repair_result{outcome: no_fix}
  ```
  *(Loop sketch encodes the ADR-0007 decision; exact retry/backoff is an
  implementation detail.)*

- **LLM provider abstraction.** A small `LlmProvider` interface with Claude
  (Sonnet default, escalate to Opus on hard cases) as the recommended default
  and local/OpenAI-compatible models as first-class alternatives. BYO-key;
  no hosted inference in v1 (ADR-0007).

- **Tiered verification in an ephemeral sandbox.** Each candidate fix is built
  and test-run in a throwaway Docker container on the brain's host (ADR-0006).
  Tier 1 (always): compile/lint/parse — Python + dbt, SQL via `dbt
  compile`/`dbt build`. Tier 2 (if a read-only dev/sample connection is
  configured): run on sample/dev. Tier 3: prod, only via the user's normal
  pipeline after the merge. **A PR is opened only if tier-1 passes**; tier-2
  absence is disclosed in the PR, not treated as a blocker.

- **Confidence + risk on every result.** The worker emits a confidence score and
  a risk class alongside the fix. In v1 these are informational (shown in the
  PR); they are the seam that makes an opt-in "auto-merge low-risk" mode a later
  config flip rather than a rewrite (ADR-0003).

- **State & durability.** Postgres is the source of truth for job state with
  crash recovery and at-least-once execution (ADR-0009). Redis may back
  ephemeral/queue concerns. No exactly-once in v1.

- **Executor backend.** Behind a minimal trait, but **only the local backend
  (Docker Compose / single VM) is implemented** in v1; VM/K8s are designed-for
  seams only (ADR-0008).

- **Packaging & onboarding.** Ships as a single Docker image / `docker compose
  up` (ADR-0004 §C1). Minimum onboarding: bring up the service → add one webhook
  line → paste a read-only git token + LLM key → optionally add a read-only
  dev/sample warehouse connection.

- **Web UI is read-only.** Run history, transcript, diff, and evidence viewer.
  No write actions; approval is the PR merge (ADR-0005, Q-E1).

- **Single-tenant, simple auth.** No RBAC/multi-tenancy in v1; those are
  enterprise-tier, phase-B concerns, but features are designed so they can be
  cleanly gated later (ADR-0010, Q-D5).

- **License.** Apache-2.0 core; v1 ships fully free/OSS (ADR-0010).

## Testing Decisions

**What makes a good test here:** tests assert **external, observable behavior**
of a component across a stable seam — the repair result a worker returns, the
job state the brain records, the PR that does or doesn't get opened — never the
internal steps of the agent loop or private data structures. Because the LLM is
non-deterministic and the product's whole credibility is "a fix is verified
before a human sees it," the LLM is treated as an **injected dependency behind
`LlmProvider`** and exercised via **record/replay** so that verification
behavior is deterministic and asserted, while model quality is evaluated
separately.

Prior art: greenfield, so no existing tests. Seams are proposed at the highest
stable points (the two component contracts + one end-to-end), matching the
ADR-defined boundaries.

**Seam 1 — Repair worker contract (`RepairJob → RepairResult`). Primary seam.**
This is where "verified before a human sees it" is proven. The worker is driven
with a fixture failure payload against a **fixture dbt git repo**, with
`LlmProvider` in record/replay mode and the **real Docker sandbox** (ADR-0006).
Asserted behaviors:
- A fix that fails tier-1 compile **never** yields `outcome: pr_proposed` — no
  PR is emitted (covers story 17).
- A passing fix yields a result carrying the diff, explanation, transcript, and
  **verification evidence reflecting the tiers that actually ran** (compile
  always; sample only when a dev connection is configured).
- Tier-2 absence is **disclosed** in the evidence, not silently omitted.
- After N failed attempts the worker returns `outcome: no_fix` and does not loop
  (story 29).
- A fix requiring prod-side action returns `outcome: needs_prod_action` as a
  recommendation, never a PR that assumes prod write (story 16).

**Seam 2 — Brain webhook→job→dispatch state machine.** In-process against a
throwaway Postgres. Driven by posting failure payloads to the webhook receiver.
Asserted behaviors:
- A valid failure payload creates a durable repair job and dispatches it
  (ADR-0004).
- A job survives a simulated brain restart mid-run and resumes/recovers
  (ADR-0009, story 26).
- A duplicate/re-delivered payload is safe under at-least-once — at worst a
  second job/PR proposal, never corrupted state (ADR-0009, story 27).
- Failure classes outside v1 scope (OOM/resource, data-quality, timeout) are
  classified and **not** dispatched as repair jobs (story 15).

**Seam 3 — End-to-end acceptance (the wow demo).** The full stack on a laptop —
local Postgres + dbt + brain + worker — run as an executable acceptance test
against a fixture dbt project, with a real (or faithfully recorded) LLM. This is
the schema-drift → auto-PR scenario below, asserted end to end: failure in → PR
out, with compile + sample evidence attached, within the target latency.

The `LlmProvider` interface is itself the test seam for provider-agnosticism: a
recorded-fixture provider and at least one real provider (Claude) run the same
worker contract tests.

## Acceptance Scenario — the v1 "wow" demo (schema drift → auto-PR)

This is the Show HN demo and the top-level acceptance criterion. It must run
fully on a laptop (local Postgres + dbt + the brain).

**Given** a running dbt-in-Airflow project wired to sibei-flow with one webhook
line, a read-only git token, an LLM key, and a read-only sample connection,

**When** an upstream source column is **renamed** (e.g. `customer_id` →
`cust_id`) and the nightly dbt run fails on the downstream model that still
references the old name,

**Then** sibei-flow, **within ~90 seconds** of the failure:
1. receives the failure webhook and opens a repair job;
2. reads the failing model source read-only, and detects the drift by comparing
   the referenced column against the current upstream schema;
3. drafts a fix (updates the model to the new column name) in the ephemeral
   sandbox;
4. verifies it — **compiled ✓ · ran on a 10k-row sample ✓ · output schema
   unchanged ✓**;
5. opens a **Pull Request** with the diff, a plain-English explanation, the
   reasoning transcript, the verification evidence, and a confidence/risk label;
6. surfaces the run in the read-only web dashboard.

**And when** the engineer merges the PR, the next dbt run is green. **Rollback**
would be a `git revert` of the PR.

**Pass criteria:** the PR is opened automatically, the diff is minimal and
correct, verification evidence is present and accurate, no prod-write credential
was ever used, and the end-to-end latency meets the ~90s target.

## Out of Scope

Per CONTEXT.md §8 — explicitly **not** in v1:

- **Additional failure classes:** OOM / resource / pod-spec mutation;
  data-quality "healing" / assertions; retry-on-timeout (that's config, not
  healing).
- **Phase-B orchestrator primitives:** an authoring DSL; the unified state
  machine; the data-aware / Arrow / MCP "pointers-not-payloads" layer.
- **Autonomy:** any auto-apply / auto-merge (v1 is strictly propose-and-approve;
  the confidence/risk seam only *enables* a future opt-in flip).
- **Prod mutation:** in-place patching of running jobs; any prod-write
  credential; writes to `main` or production tables; prod-side actions such as
  data backfills (surfaced as recommendations only).
- **Integrations beyond the flagship:** non-dbt/Airflow orchestrators
  (Dagster/Prefect are fast-follows); non-git source; Spark and SQL engines
  other than dbt's; standalone-dbt and cron handled only via `sbflow run --`.
- **Notifications:** Slack/chat notifications (fast-follow); v1 surface is the PR
  + read-only web dashboard.
- **Infra breadth:** VM and Kubernetes executor backends (seam only, ADR-0008);
  the Argo/Ray/Temporal connectors (ADR-0001).
- **Enterprise / multi-team:** multi-tenancy, RBAC, SSO/SAML, audit logging,
  multi-cluster (open-core enterprise tier, ADR-0010); hosted/managed inference
  and managed cloud (post-funding, phase 2).

## Further Notes

- **Success definition (v1, from CONTEXT §7):** the Show HN lands on the HN
  front page with this demo — ~200+ upvotes / top-10, ~1,000 GitHub stars in
  week one, ~10 teams self-hosting within a month, and multiple unsolicited "we
  need this" signals.
- **Appetite:** one focused ~6-week build cycle of core work (~10 calendar weeks
  at ~15 hrs/wk). Own-loop-first (ADR-0007) extends the calendar versus the SDK
  path; scope is protected by the small tool surface and capped iterations.
- **Phase-B seeding:** the v1 repair loop is the deliberate seed of the phase-B
  agentic engine; it must be built so the graduation to running *as* a native
  sibei-flow workflow is natural, not a rewrite (ADR-0003, ADR-0007). Full
  dogfooding is a phase-B outcome, not a v1 requirement.
- **Open items to confirm (CONTEXT §9):** real build capacity (hrs/week), and
  the telemetry/opt-in mechanism for measuring "teams self-hosting."
- **Publication note:** this project is not a git repository and has no
  configured issue tracker, so this PRD is delivered as `docs/design/PRD.md`
  rather than published to a tracker with a `ready-for-agent` label.
