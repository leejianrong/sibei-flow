---
shaping: true
---

# SPIKE-B — Resolving the flagged unknowns of Shape B (queue seam)

> Investigations for the flagged rows of Shape B in `SHAPING.md`. Each spike
> states what we need to learn and lands concrete mechanics so the row moves
> **flagged → OK**. Greenfield project, so the "existing system" investigated is
> the surrounding ecosystem (dbt, Airflow, Docker, Postgres, warehouse
> `INFORMATION_SCHEMA`) — the well-documented surfaces v1 rides on.
>
> Flagged rows covered: **R2.3, R2.4** (B-S1) · **R4.1, R4.2, R4.3** (B-S2) ·
> **R3.4** (B-S3) · **R5.4** (B-S4) · **R5.5** (B-S5) · **R5.6** (B-S6) ·
> **R7.2** (B-S7). R0 resolves transitively once these do.

---

## B-S1 — Classify the failure & detect drift (R2.3, R2.4, and part B7)

### Context
Shape B's scope gate (B2) must act only on schema drift + code/SQL errors and
safely drop everything else, from a webhook payload — before any LLM cost.
Drift detection (B7) then needs the *current* upstream schema.

### Goal
Describe how a failure payload is classified into {schema-drift, code-SQL,
out-of-scope}, and how the current upstream schema is read to confirm drift.

### Questions
| # | Question |
|---|----------|
| **B-S1-Q1** | What does an Airflow `on_failure_callback` / dbt failure actually carry, and how do we get a structured payload from it? |
| **B-S1-Q2** | What signal distinguishes schema drift from a code/SQL error from an out-of-scope class (OOM/timeout/data-quality)? |
| **B-S1-Q3** | How do we read the current upstream schema read-only to confirm a rename/removal/retype/nullable change? |

### Findings
- **B-S1-Q1 — payload.** Airflow calls `on_failure_callback(context)` with
  `dag_id`, `task_id`, `run_id`, `try_number`, `exception`, and `log_url`. A
  tiny shipped helper POSTs these as JSON to the brain's webhook. For dbt, the
  richer signal is `target/run_results.json` (per-node `status` ∈
  {success, error, fail, skipped}, `message`, `compiled_code`, `unique_id`,
  `adapter_response`) plus `target/manifest.json` (node graph, `refs`,
  `sources`, declared `columns`). dbt process exit codes: `0` ok, `1` error,
  `2` usage. The helper attaches `run_results.json` (or its path) to the
  payload. `sbflow run -- <cmd>` produces the same shape from exit code + stderr
  for cron.
- **B-S1-Q2 — classification.** A deterministic rule over the payload, no LLM:
  | Class | Signal | Action |
  |---|---|---|
  | **schema drift** | dbt node `status = error` with an `adapter_response`/message matching a missing/mismatched-column pattern per adapter — Postgres `column "…" does not exist`, Snowflake `invalid identifier '…'`, BigQuery `Unrecognized name: …`, plus type/nullable errors | **act** |
  | **code/SQL error** | dbt **compilation** error, or a SQL syntax error (`syntax error at or near`), or a Python model exception traceback | **act** |
  | OOM / resource | exit signal 137/OOMKilled, `MemoryError`, adapter OOM | **drop** (out of scope) |
  | timeout | statement-timeout / `canceling statement due to statement timeout` | **drop** (config, not healing) |
  | data-quality | dbt node type `test` with `status = fail` | **drop** |
  | **unknown** | no rule matches | **drop, never guess** (satisfies R2.4's "don't act where not competent") |
  The classifier is pattern-based and adapter-aware; patterns live in a small
  table keyed by adapter, extensible without code changes.
- **B-S1-Q3 — schema read.** All three v1 warehouses expose
  `INFORMATION_SCHEMA.COLUMNS` (Postgres, Snowflake, BigQuery per-dataset). Via
  the configured **read-only** connection, `get_schema` queries the current
  columns (name, type, is_nullable) of the failing model's referenced
  source/ref. The referenced columns come from the compiled SQL / manifest.
  Diffing (referenced ∖ current) yields the drift: a column present in the model
  but absent upstream = removed/renamed; a type/nullable mismatch = retype/
  nullable. A rename is inferred when a removed column has a close-named or
  same-type sibling newly present (candidate mapping surfaced to the LLM, not
  auto-applied).

### Resolution
**R2.3 → OK** (act only on the two in-scope classes), **R2.4 → OK** (recognized
out-of-scope + unknown → drop), **B7 → OK** (schema read via read-only
`INFORMATION_SCHEMA`). Residual: pattern coverage grows over time — acceptable,
because the default is *drop*, so misses are safe (no bad PR), never unsafe.

---

## B-S2 — Ephemeral sandbox, tiered verification & honest evidence (R4.1, R4.2, R4.3)

### Context
The credibility of the whole product: a fix is verified before a human sees it,
and the evidence is accurate and discloses which tiers ran. In Shape B the
**worker** owns the sandbox.

### Goal
Describe the sandbox lifecycle, the exact tier-1/tier-2 commands, how pass/fail
+ tier-2 disclosure are captured, and the gate that blocks a PR on tier-1 fail.

### Questions
| # | Question |
|---|----------|
| **B-S2-Q1** | What is the sandbox and what's in the image? |
| **B-S2-Q2** | What are the exact tier-1 (always) and tier-2 (if configured) invocations, and how is pass/fail captured? |
| **B-S2-Q3** | How is "output schema unchanged" evidence produced, and how is tier-2 absence disclosed rather than hidden? |
| **B-S2-Q4** | How is the "no PR unless tier-1 passes" gate enforced? |

### Findings
- **B-S2-Q1.** A throwaway Docker container from a **pre-baked** image
  (`python`, `dbt-core` + the user's adapter, git) on the brain's host
  (ADR-0006). The candidate diff is applied to a checkout of the repo at the
  failing ref inside the container; nothing is written back to the repo from
  here. Container is removed after the run (`--rm`).
- **B-S2-Q2.** Tier 1 (always): `dbt compile --select <model>` (and `dbt parse`)
  → pass iff exit 0 and the node compiles; captures `compiled_code`. Tier 2 (iff
  a read-only sample/dev connection is configured): `dbt build --select <model>
  --target sample --vars '{limit: 10000}'` (or a `sample` profile that caps
  rows) → pass iff `run_results.json` node `status = success`. Each tier's
  raw output + parsed status is stored as an evidence record.
- **B-S2-Q3.** Output-schema-unchanged: capture the model's column set before
  (from the pre-fix manifest / catalog) and after (post-fix `dbt compile`
  catalog, or a `LIMIT 0` describe against the sample target) and diff. Evidence
  is a structured object: `{tier1: {ran: true, passed: bool, log},
  tier2: {ran: bool, passed: bool|null, log}, output_schema: {changed: bool,
  detail}}`. **Disclosure** is intrinsic: `tier2.ran = false` when no sample
  connection is configured, and the PR template renders "sample run: not
  configured" — absence is a rendered fact, not an omission (R4.3).
- **B-S2-Q4.** The worker only writes `outcome = pr_proposed` when
  `tier1.passed = true`; otherwise it iterates (if attempts remain) or emits
  `no_fix`. The brain opens a PR **only** for `pr_proposed` rows (B9). So a
  non-compiling fix structurally cannot reach the review queue (story 17).

### Resolution
**R4.1 → OK** (compile gate + PR-only-on-pass), **R4.2 → OK** (tier-2 sample run
when configured), **R4.3 → OK** (structured evidence with intrinsic tier-2
disclosure). Note: in Shape B the worker reports evidence; integrity relies on
the worker being our own trusted component (it is). *(This is the one place
Shape C would be structurally stronger; accepted for B.)*

---

## B-S3 — `needs_prod_action` detection (R3.4)

### Context
Some fixes compile and are correct as model SQL but still require a human prod
action (e.g. a historical backfill). These must be surfaced as a recommendation,
never a PR that assumes prod is safe.

### Goal
Describe the concrete rule that emits `outcome = needs_prod_action`.

### Questions
| # | Question |
|---|----------|
| **B-S3-Q1** | Which in-scope drift cases require a prod-side action beyond the model edit? |
| **B-S3-Q2** | How is that detected, and what does the contract carry? |

### Findings
- **B-S3-Q1.** Within v1 scope, the case is a drift that the model SQL alone
  cannot make correct: a column **removed/retyped upstream that feeds an
  `incremental` model's already-materialized history** (past rows can't be
  recomputed by a forward run), or a fix that would need a `--full-refresh` /
  backfill to be truthful. A pure forward-looking rename on a `view`/`table`
  model does **not** need prod action.
- **B-S3-Q2.** Detection signal: (a) the failing node's `materialized` config is
  `incremental`, **and** (b) the drift is a removal/retype (not a clean rename
  resolvable in-SQL). When both hold, the worker emits
  `outcome = needs_prod_action` with a recommendation string ("this fix is
  correct going forward; historical rows need a `--full-refresh`/backfill you
  must run"). The contract carries the drafted diff **plus** the recommendation;
  the brain surfaces it (dashboard + optionally a PR marked recommendation-only)
  and **never** performs the prod action.

### Resolution
**R3.4 → OK** — concrete rule (`incremental` + non-rename drift → recommendation)
and a contract that surfaces without assuming prod. Residual: the exact
rendering (recommendation-only PR vs dashboard note) is a detailing choice, not
an unknown.

---

## B-S4 — Confidence score & risk class rubric (R5.4)

### Context
The reviewer calibrates scrutiny from a confidence/risk label; it must derive
from real signals, not model self-report. Informational in v1 (ADR-0003), but
the seam for a later opt-in auto-merge.

### Goal
Describe the explainable rubric: inputs → confidence + risk.

### Questions
| # | Question |
|---|----------|
| **B-S4-Q1** | What observable signals feed the label? |
| **B-S4-Q2** | How do they map to a confidence score and a discrete risk class? |

### Findings
- **B-S4-Q1 — signals** (all already produced by the pipeline): tiers passed
  (compile only vs compile+sample); output-schema-unchanged (bool); diff size
  (lines changed, files touched); drift unambiguity (single clean 1:1 rename
  mapping vs multi-candidate/ambiguous); attempts used (1 vs near-N).
- **B-S4-Q2 — mapping.** A transparent scored rubric (displayed in the PR):
  | Risk class | Condition |
  |---|---|
  | **low** | single-file, ≤ ~15 changed lines, unambiguous rename, tier-1 **and** tier-2 passed, output schema unchanged, 1 attempt |
  | **medium** | compiles + (tier-2 passed *or* not configured), small diff, minor ambiguity |
  | **high** | multi-file or large diff, ambiguous drift, or tier-2 unavailable **and** output schema changed |
  Confidence = weighted sum of the same signals, normalized 0–1, shown with the
  contributing factors listed ("+ compiled, + sample-ran, + schema unchanged,
  − 2 attempts"). Explainability is the point: the reviewer sees *why*.

### Resolution
**R5.4 → OK** — explainable rubric over signals the pipeline already emits.

---

## B-S5 — Minimal, legible diff (R5.5)

### Context
LLMs reformat whole files; a minimal diff is what makes blast radius obvious and
the demo legible.

### Goal
Describe how the edit is constrained to a minimal diff.

### Questions
| # | Question |
|---|----------|
| **B-S5-Q1** | How does the `edit_file` tool avoid full-file rewrites? |
| **B-S5-Q2** | What guard catches an oversized diff before it becomes a PR? |

### Findings
- **B-S5-Q1.** `edit_file` is a **targeted-replacement** tool (old-string →
  new-string / line-range anchored), not a whole-file writer. The loop prompt
  scopes edits to the failing model file. This mirrors proven agent edit tools.
- **B-S5-Q2.** After each edit, compute the git diff and apply a guard:
  reject + feed back for re-draft if the diff (a) touches files other than the
  failing model (and its schema yml), or (b) exceeds a configurable line
  threshold, or (c) reformats unchanged lines (whitespace-only churn). The guard
  is deterministic and runs before verification, so oversized diffs never reach
  tier-1 or the PR.

### Resolution
**R5.5 → OK** — targeted edit tool + deterministic diff-size/scope guard with
re-draft feedback.

---

## B-S6 — ~90s end-to-end latency budget (R5.6)

### Context
The demo bar. Budget = detect + classify + read + N×(draft + verify) + PR open,
within ~90s. Sandbox cold start dominates; B adds queue-poll latency.

### Goal
Describe a latency budget for the flagship rename scenario showing p50 ≤ ~90s,
and the mechanisms that keep it there.

### Questions
| # | Question |
|---|----------|
| **B-S6-Q1** | What is the per-stage time budget on the flagship case? |
| **B-S6-Q2** | Which mechanisms remove B's specific overheads (poll latency, cold worker, cold sandbox)? |

### Findings
- **B-S6-Q1 — budget (flagship single-column rename, 1–2 LLM iterations):**
  | Stage | Budget |
  |---|---|
  | webhook receive + classify + enqueue | < 1s |
  | dispatch to worker (see B-S6-Q2) | < 1s |
  | git read + `get_schema` diff | 2–5s |
  | LLM draft (Sonnet), attempt 1 | 5–15s |
  | tier-1 compile in sandbox | 2–5s |
  | tier-2 `dbt build` on 10k sample | 5–20s |
  | (optional) 2nd iteration | +10–20s |
  | result → brain → open PR | 2–5s |
  | **p50 total** | **~30–60s**; p90 with a 2nd iteration ~75–85s |
  The ~90s target is met with margin on the happy path; the cap N protects the
  tail (a runaway simply emits `no_fix`).
- **B-S6-Q2 — B's overheads removed:** (1) **Poll latency** → use Postgres
  `LISTEN/NOTIFY`: the brain `NOTIFY`s on enqueue and workers `LISTEN`, so
  claim is event-driven (sub-second), not a poll interval; the poll is only a
  safety-net fallback. (2) **Cold worker** → run a **small standing worker pool**
  (≥1 warm process) rather than spawn-per-job, so no Python interpreter cold
  start on the hot path. (3) **Cold sandbox** → the image is **pre-baked** (no
  runtime `pip install`), and a warm sandbox container can be kept ready so
  `docker run` on a resident image is ~1–3s.

### Resolution
**R5.6 → OK** — a credible sub-90s p50 budget, with B's structural overheads
(poll, cold worker) removed via `LISTEN/NOTIFY` + a warm worker pool; the shared
sandbox cold start is handled by pre-baking + warm containers. Note for the
build: this makes B's worker a *standing pool*, not purely spawn-per-job — a
detailing decision recorded here (see Detail B in `SHAPING.md`).

---

## B-S7 — Duplicate / re-delivery safety (R7.2)

### Context
At-least-once delivery (ADR-0009) means the same failure can arrive twice; state
must never corrupt — at worst a second PR.

### Goal
Describe the idempotency key and the safety argument.

### Questions
| # | Question |
|---|----------|
| **B-S7-Q1** | What is the natural idempotency key for a failure? |
| **B-S7-Q2** | What happens on a re-delivery, and why is it always safe? |

### Findings
- **B-S7-Q1 — key.** A failure is identified by `hash(repo, orchestrator_run_id,
  task_id, failing_node_unique_id)`. Airflow supplies `run_id` + `task_id`; dbt
  supplies `invocation_id` + node `unique_id`. This key is stable across
  re-deliveries of the *same* failure but distinct across genuinely new runs.
- **B-S7-Q2 — behavior.** The job queue has a unique index on the key over a
  recent window. On enqueue, `INSERT … ON CONFLICT DO NOTHING`: a re-delivery of
  the same failure collapses to the existing job (no duplicate work). If the
  window has passed or the collision isn't detected, the worst case is a
  **second, independent repair job → a second PR proposal** — which is harmless
  because every outcome is **human-gated** (nothing is applied without a merge)
  and each PR is on its own branch. There is no shared mutable prod state to
  corrupt: the only write is a PR (R6.2). Under B specifically, the lease also
  prevents two workers from processing one queued job concurrently.

### Resolution
**R7.2 → OK** — idempotency key + `ON CONFLICT` dedupe collapses re-deliveries;
the human-gated, PR-only design makes the fallback (a second PR) provably safe.

---

## Spike summary

| Flagged row | Spike | Verdict |
|---|---|---|
| R2.3, R2.4 | B-S1 | OK (pattern classifier, drop-on-unknown) |
| B7 (drift read) | B-S1 | OK (`INFORMATION_SCHEMA` via read-only conn) |
| R4.1, R4.2, R4.3 | B-S2 | OK (pre-baked sandbox, tiered cmds, structured+disclosed evidence, compile gate) |
| R3.4 | B-S3 | OK (`incremental` + non-rename drift → recommendation) |
| R5.4 | B-S4 | OK (explainable rubric over pipeline signals) |
| R5.5 | B-S5 | OK (targeted edits + diff-size/scope guard) |
| R5.6 | B-S6 | OK (`LISTEN/NOTIFY` + warm worker pool + pre-baked/warm sandbox; p50 ~30–60s) |
| R7.2 | B-S7 | OK (idempotency key + `ON CONFLICT`; human-gated fallback safe) |
| R0 | — | OK transitively once the above hold; proven by end-to-end acceptance (PRD Seam 3) |

**All of Shape B's flagged rows are resolved.** One design refinement surfaced
that must ripple into the shape: B-S6 makes the worker a **small standing pool**
(warm), not purely spawn-per-job. This is folded into **Detail B** in
`SHAPING.md`.
