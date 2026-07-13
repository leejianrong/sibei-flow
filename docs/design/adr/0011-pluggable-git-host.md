# ADR-0011: Pluggable git host for the PR opener (offline / github)

- **Status:** Accepted
- **Date:** 2026-07-14

## Context

V4 adds the single write action in the whole system: turning a verified
`pr_proposed` repair into a real Pull Request (ADR-0005, R6.2). Two needs pull in
opposite directions:

- The Show-HN demo and the CI/acceptance tests must run **fully offline** on a
  laptop — no GitHub account, no token, no network egress (ADR-0004/0006
  onboarding story).
- Real adoption means opening a **real PR** on the user's forge (GitHub first).

We also must not regress the hard invariant: sibei-flow **never holds
prod-write credentials** and the only capability the opener adds is "push a
branch + open a PR" (ADR-0005 / R6.1).

## Decision

Put the git host behind a **narrow pluggable seam** — mirroring ADR-0008's
executor-backend pattern — with two backends selected by `GIT_HOST`:

- **`offline` (default):** push the fix branch to the bundled bare git remote
  (`git-remote`, `git://git-remote:9418/analytics.git`) and record the branch +
  a `base...head` compare reference as the "PR". No credentials, no egress. This
  is what tests and the demo use.
- **`github`:** clone/push over HTTPS with a **PR-scoped token** and open the PR
  via `POST /repos/{owner}/{repo}/pulls`. The token is the only credential the
  system holds and can do nothing but open PRs.

The seam is a trait (`GitHost::open_pr`) with an owned `PrRequest`/`PrRef`. The
brain drives it from a background poller (started in `main.rs` next to the axum
server) that picks up `state='done' AND result->>'outcome'='pr_proposed' AND
pr_url IS NULL`, applies the RepairResult `diff`, and records `pr_url` back on
the job — which doubles as the idempotency guard (a PR is never opened twice).

`git-remote` is promoted from the hero-only profile to the **core stack** so the
offline opener always has a real push target (and `make demo` shows a real
pushed branch).

## Consequences

- Laptop-only demo and CI stay green with zero secrets; the offline "PR" is a
  real branch on a real (local) remote, not a mock.
- Adding a forge later (GitLab, Bitbucket, a GitHub App instead of a token) is a
  new `GitHost` impl behind the same seam — no core rework.
- The opener's config surface cannot express a warehouse/prod-write credential
  (asserted by a guardrail test), preserving R6.1.
- The brain image now ships `git` (clone/apply/push); the git:// client
  transport is built into core git, so no extra daemon on the brain side.

## Alternatives considered

- **A local Gitea/forge container for the offline demo** — rejected: heavier than
  a bare `git daemon`, and a compare ref + pushed branch already proves the write
  end-to-end.
- **GitHub-only from day one** — rejected: concedes the offline, no-secrets
  onboarding story and blocks CI/acceptance from running the real write path.
