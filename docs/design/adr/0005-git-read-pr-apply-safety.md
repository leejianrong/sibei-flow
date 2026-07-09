# ADR-0005: Git-read + PR-based apply; prod-write-never

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

To propose a real fix the agent must read the failing source, and the fix must
land somewhere. The scariest permissions to request — and the biggest adoption
and trust blockers — are write access to production repos and to production
data.

## Decision

- The agent reads source **read-only** from the user's git repo (scoped token
  or GitHub App).
- The **only write action is opening a Pull Request** on a branch, containing
  the diff, a plain-English explanation, and verification evidence.
- **Hard invariant:** sibei-flow **never holds prod-write credentials and never
  writes to `main` or production tables.**
- **Approval = the PR review.** v1 notification surface = the **PR itself + the
  read-only web dashboard**; Slack/other chat notifications are a fast-follow,
  out of v1 scope (resolves tension T3). No in-place mutation of running jobs.
- **Rollback = `git revert`** the PR. Blast radius is a reviewable branch.

## Consequences

- Fits existing review/CI/audit culture; a strong "safe to adopt" claim for
  platform teams.
- Requires per-forge PR integration (GitHub first).
- A fix that would require prod-side action (e.g. a data backfill) is
  out of scope for v1's auto-apply; surfaced as a recommendation only.

## Alternatives considered

- **Patch the running job in place** — rejected: unreviewed prod mutation, the
  thing that gets a tool vetoed.
