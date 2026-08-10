# sibei-flow — Roadmap

Milestone → epic → key story map for the work tracked on the **sibei-flow**
Simple Kanban board (board id `7`). Milestones are a naming convention only
(Simple Kanban has no milestone field); they live as the `M<n>:` prefix on epic
names. Cross-cutting work sits in un-prefixed **Ongoing** epics.

Status legend: **done** shipped · **todo** planned · (nothing is currently in
progress). Detail per slice lives in `docs/design/V*-plan.md`; the slice map is
`docs/design/SLICES.md`.

## Milestones (shippable increments V1–V5)

### M1: Walking Skeleton (V1) — *done*
The durable spine: failure webhook → classify → enqueue → claim → record →
read-only dashboard.
- Webhook receiver + Failure normalization (N1)
- Thin classifier: schema-drift / code-SQL / out-of-scope (N2)
- Enqueue + durable `repair_jobs` queue table (N3/N4)
- Claim loop: poll + `SKIP LOCKED` + lease (N5)
- `no_fix` stub result write-back (N13 thin)
- Read-only dashboard: run history + detail + API (N15/U4/U5)
- `docker compose` packaging (brain + Postgres + worker)

### M2: Drafted Fix (V2) — *done*
The worker runs a bounded agent loop that reads source, confirms drift, and
drafts a minimal edit.
- Bounded agent loop behind `LlmProvider` (N6)
- `read_file` (git read-only source, N7)
- `get_schema` (INFORMATION_SCHEMA drift, N8)
- `edit_file` + diff guard (N9)
- Write-back thickened: diff + explanation + transcript (N13)
- LlmProvider backends: replay / claude / openai
- Dashboard renders diff + explanation + transcript (U5)

### M3: Verified Before You See It (V3) — *done*
Every drafted fix is compiled/sample-run in an ephemeral sandbox; the run
carries honest evidence + confidence/risk; non-compiling drafts are suppressed.
- `run_sandbox`: dbt image + Docker-out-of-Docker, tiered compile/build (N10)
- Evidence builder `{tier1, tier2, output_schema}` with disclosure (N11)
- Confidence/risk scorer rubric (N12)
- Compile gate: `pr_proposed` only if tier-1 passes
- Sandbox image + dev/sample warehouse tier-2 target
- Dashboard renders evidence + confidence/risk (U5)

### M4: The Auto-PR (V4) — *done*
A verified `pr_proposed` result becomes a real Pull Request carrying the diff +
evidence; the merge is the sole path to prod.
- PR opener (`brain/src/pr/`): background poller, `pr_proposed` → branch + PR (N14) — *done*
- Pluggable git-host seam (`offline` default / `github`), ADR-0011 — *done*
- PR body template from RepairResult (U3): explanation + diff + evidence table +
  confidence/risk + collapsible transcript + rollback footer — *done*
- Record PR URL on job row (migration `0002_pr.sql`) + link from dashboard — *done*
- Idempotency: `pr_url IS NULL` dedupe guard (never opens a duplicate PR) — *done*
- Prod-write guardrail test: no prod-write credential ever loaded (R6.1) — *done*
- End-to-end latency measurement (p50 ≤ ~90s): measured ~15s webhook→PR on the
  flagship offline case — *done*
- Live hero pipeline: runnable Airflow+dbt env (Seam-3 harness) — *done* (the
  `docker compose --profile hero` stack + `make hero` / `hero-break`; the live
  loop heals real warehouse drift and opens the PR end to end).

### M5: Hardening & Onboarding (V5) — *done*
Production-trust properties (durability, dedup, honest prod-action
recommendations), one-command onboarding, and the ~90s latency mechanisms.
- Dedupe: idempotency key + unique index + `ON CONFLICT DO NOTHING` (story 27, R7.2) — *done*
- Crash recovery: reconcile + lease re-claim + orphan-container cleanup (story 26, R7.1) — *done*
- PR-opener dedupe gap closed: claim with `FOR UPDATE SKIP LOCKED` + `pr_claimed_at` (concurrency/crash safe) — *done*
- Latency tuning: `LISTEN/NOTIFY` fast dispatch + pre-baked sandbox; measured hero p50 ≈ 10.6s heal / 12.1s to PR (≪ 90s, R5.6) — *done* (warm worker pool / long-lived warm container deferred — see V5-plan)
- Detection ergonomics: Airflow callback + dbt hook + `sbflow run --` cron wrapper — *done*
- `sbflow init` onboarding flow + config file — *done*
- `needs_prod_action` rule (incremental + non-rename drift → recommendation, no
  prod-assuming PR) (story 16, R3.4) — *done*
- Classifier pattern expansion (Postgres/Snowflake/BigQuery drift/relation/type
  phrasings; unknown → drop preserved) (story 15, R2.3/R2.4) — *done*

### M6: Launch Hardening (v1 public) — *in progress*
Getting the shipped v1 in front of people: licence clarity, landing copy, the hero
demo recording, and the Show HN post.
- LICENSE / open-core clarity + security once-over — *done*
- Tighten README + GitHub Pages landing copy for launch
- Record hero demo GIF/video (webhook → PR, ~12s)
- Draft the Show HN post + launch checklist

### M7: Satay convergence — *todo*
The decision of 2026-08-05: sibei-flow and **Satay Runtime** are two independent
products sharing one execution engine. See
[ADR-0012](design/adr/0012-no-second-execution-engine.md),
[ADR-0013](design/adr/0013-transcript-tagged-union.md) and
[ADR-0014](design/adr/0014-r61-constrains-hosting.md).

Pre-launch (small, and only the first one is time-sensitive):
- `RepairResult.transcript` → tagged union `{kind:"lines"|"journal"}` (ADR-0013),
  worker + `body.rs` + fixtures. **Do this before the Show HN**: after it, the
  frozen contract has third-party readers and the change becomes a migration.

Post-launch, in dependency order:
- Port the repair loop onto Satay **at the multi-candidate-repair boundary** —
  "draft three candidates, keep the best evidence". Needs Satay's collect-mode
  fan-out (KAN-473 there). V5's lease re-claim and orphan sweep stay as they are.
- Emit `kind:"journal"` transcripts; render the PR body from the journal.
- Per-run cost accounting, free once the loop is a Satay workflow.
- **Fork-and-replay as verification tier 3** — fork the repair run at the failing
  call, replay the candidate fix against the recorded inputs. The one thing in this
  roadmap a better-funded competitor cannot copy quickly, and the reason the port
  is worth doing at all.
- Hosted app tier on Satay's shared plane, R6.1-preserving (ADR-0014): worker stays
  on the customer's runner, only the redacted journal crosses.

## Ongoing (cross-cutting, no milestone)

### Product Design & Shaping — *done*
The internal spec/ADR corpus underpinning every slice.
- Design doc set: CONTEXT / FRAME / RAW / QUESTIONS / REQS
- PRD + SHAPING + SLICES
- 10 ADRs (control-plane, Rust core, tiered verification, …)
- SPIKE-B unknowns resolved
- Hero-pipeline spec (`HERO-PIPELINE.md`)

### Dev Experience & Ops — *mixed*
Repo tooling, demo, test suites, and CI/guardrails.
- Makefile for common dev tasks — *done*
- `demo.sh` end-to-end driver + fixtures + warehouse seed — *done*
- Test suites: brain (Seam 2) + worker (claim/diffguard/agent/score/sandbox) — *done*
- CI: automated test workflow (lint + both suites) — *done*
- Pre-commit / pre-push hooks + branch protection — *done*
- Agent brief: `CLAUDE.md` at repo root — *done*
- Dependency vuln scan + update cadence (cargo-audit/pip-audit + dependabot) — *done*
- Secret scanner (gitleaks) in CI + pre-commit — *done*
- Formalize test layering (pytest markers for fast/no-infra layer) — *done*
- Python type checking: mypy (advisory) — *done*
- Clear mypy errors + promote mypy to a blocking gate — *done*
- Frontend tooling: eslint + vitest (when a Svelte/TS UI lands) — *todo*

### Marketing Site — *done*
The externally-shipped GitHub Pages landing site.
- GitHub Pages landing page
- Pages deploy workflow (auto-enable)

## Board

Board: **`sibei-flow` (id `7`)** on Pandan. Read and update it with the `pandan`
CLI (see the `pandan` skill); `pandan epic list --board 7` is the live status.

Deliberately no story counts here: they go stale on the next card and have already
done so once. The board is the source of truth for progress; this document is the
source of truth for *why* the milestones exist.
