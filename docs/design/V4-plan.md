---
shaping: true
---

# V4 — The auto-PR (the wow)

> Slice V4 of `SLICES.md`. Adds the single write action — opening the Pull
> Request — completing the flagship end-to-end acceptance and the Show HN demo.

## Goal & demo

**Goal:** a verified `pr_proposed` result becomes a real Pull Request on a
branch, carrying everything the reviewer needs; the merge is the sole path to
prod.

**Demo (the wow, PRD acceptance):** rename `customer_id → cust_id`; the nightly
dbt run fails; **within ~90s** a PR appears with the minimal diff, plain-English
explanation, reasoning transcript, verification evidence, and confidence/risk.
Merge it → the next dbt run is green. Rollback would be `git revert`.

## Affordances (from SLICES.md V4)
N14 PR opener · U3 the Pull Request.

## Requirements exercised
R5.1 (normal PR), R5.6 (~90s — measured here), R6.1 (no prod-write creds),
R6.2 (PR is the only write), R6.3/R6.4 (merge=approve/close=reject/revert),
R6.5 (propose-and-approve), and R0 proven end to end.

## Components & files
- **PR opener (brain)** — `brain/pr/`: watches for terminal `pr_proposed` job
  rows; creates a branch, commits the diff, opens a PR via the git host
  (GitHub App / scoped token) with a rendered body (U3). This token is **PR-
  scoped only** — no prod-write capability anywhere in the system (R6.1).
- **PR body template** — renders diff summary + explanation + transcript
  (collapsible) + evidence table + confidence/risk badge + a "rollback = git
  revert" footer.

## Contract additions
- **RepairResult.outcome = pr_proposed** now triggers a real PR; the job row
  records the PR URL, surfaced in U5.

## Tasks
1. Git integration: branch + commit the diff + open PR (GitHub App and token
   modes).
2. PR body template from the RepairResult.
3. Record PR URL back on the job; link it from the dashboard.
4. End-to-end latency measurement on the flagship case; confirm p50 ≤ ~90s
   (latency tuning lives in V5 if the target is missed).
5. Guardrail test: assert no credential capable of prod-write is ever loaded.

## Tests (PRD Seam 3 — end-to-end acceptance)
- Full stack (local Postgres + dbt + brain + worker): rename-drift failure in →
  PR out with compile + sample evidence attached, within the latency target.
- No prod-write credential is ever used; only a PR-on-branch write occurs.
- Closing the PR (reject) and `git revert` (rollback) leave a clean trail.

## Acceptance
The Show HN demo runs on a laptop: failure → auto-PR with minimal correct diff +
evidence within ~90s; merge → green. **No ⚠️.**
