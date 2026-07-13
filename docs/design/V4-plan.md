---
shaping: true
---

# V4 — The auto-PR (the wow)

> **Status: SHIPPED.** Slice V4 of `SLICES.md`. Adds the single write action —
> opening the Pull Request — completing the flagship end-to-end acceptance and
> the Show HN demo. Built as a brain-side PR-opener poller behind a pluggable
> git-host seam (`offline` default / `github`; ADR-0011). Live-verified: a
> rename-drift webhook reaches a pushed fix branch + recorded PR in ~15s (well
> under the ~90s target).

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

## Components & files (as built)
- **PR opener (brain)** — `brain/src/pr/`: a background poller (started in
  `main.rs` next to the axum server) that finds terminal `state='done'`,
  `result->>'outcome'='pr_proposed'`, `pr_url IS NULL` jobs; clones the dbt repo,
  branches, applies the `diff`, commits, and opens the PR via the selected
  git-host backend with a rendered body (U3).
- **Pluggable git-host seam** — `pr/githost.rs` (`GitHost` trait), with two
  backends (ADR-0011): `pr/offline.rs` (default — push the fix branch to the
  bundled bare remote + record a compare ref; no creds) and `pr/github.rs`
  (`POST /repos/{owner}/{repo}/pulls` with a **PR-scoped** token — no prod-write
  capability anywhere, R6.1). `pr/git.rs` wraps the git CLI.
- **PR body template** — `pr/body.rs`: diff summary + explanation + transcript
  (collapsible `<details>`) + evidence table (tier1/tier2/output_schema) +
  confidence/risk badge + a "rollback = git revert" footer.
- **State** — migration `brain/migrations/0002_pr.sql` adds `pr_url` /
  `pr_branch` / `pr_opened_at` (no RepairResult contract change). `pr_url`
  doubles as the idempotency guard.

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
