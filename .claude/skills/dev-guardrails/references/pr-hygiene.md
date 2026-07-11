# PR hygiene

Small, reviewable PRs with enough context to review in one sitting.

## PR template

Ship `.github/pull_request_template.md` (starter in
`references/templates/pull_request_template.md`). It asks for: **what & why**
(link the design doc / ADR / slice), **how it was tested** (commands + output +
new tests), and a **checklist** (tests green, lint clean, frozen contracts
respected, docs updated, no secrets/prod-write creds).

## CODEOWNERS

Add `.github/CODEOWNERS` so reviews route automatically. Solo today
(`* @leejianrong`); split by area (`/brain/`, `/worker/`, `/docs/`) as the team
grows. Pair with a branch-protection rule requiring code-owner review.

## Conventions

- **PR title = Conventional Commit** (`feat: …`, `fix: …`) — feeds changelog
  automation and makes history scannable. A CI check can enforce it.
- **One concern per PR.** Separate refactors from behavior changes; separate a
  new slice from packaging/tooling (this repo already commits that way).
- **Link the design.** Every non-trivial PR points at the `docs/design/` slice
  or ADR it implements, so the "why" outlives the diff.

## Review checklist (reviewer)

- Does it hold the **safety invariants** (no unverified fix → PR; no prod-write
  creds; RO source/warehouse; UI read-only)?
- Are the **frozen contracts** unchanged, or intentionally versioned with a note?
- Tests: is there a cheap test that would catch a regression here? Is the gate
  still deterministic (no real-LLM dependency added)?
- Docs/README updated if behavior changed?
