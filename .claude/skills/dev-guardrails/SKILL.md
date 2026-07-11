---
name: dev-guardrails
description: >-
  Quality-gate + guardrails playbook for the sibei-flow repo. Use it BEFORE
  committing, pushing, or opening a PR: run the repo's format/lint/tests (via
  `make`) and never push red — and when guardrails are missing, bootstrap them
  (pre-commit/pre-push hooks, GitHub Actions CI, PR hygiene, branch protection).
  Triggers on: commit, push, open a PR, "ship it", cut a release, "run
  lint/tests", "set up CI / hooks / pre-commit / branch protection / coverage",
  "prevent regressions", "definition of done".
---

# dev-guardrails

The engineering guardrails for sibei-flow: what to run before code ships, and
how the automated gates are wired. Goal — stay fast while making regressions
hard to land.

## How enforcement actually works (skill vs. the real gates)

This skill is the **playbook**; it does not enforce anything by itself. Real
enforcement lives in three layers, and this skill's job is to *run* them and
*install* the missing ones:

1. **Repo git hooks** (`.pre-commit-config.yaml`) — fast local feedback on
   commit/push. See `references/git-hooks.md`.
2. **GitHub Actions + branch protection** — the authoritative gate; `main`
   requires green checks. See `references/ci-cd.md`.
3. *(optional)* a **Claude Code Stop hook** in `.claude/settings.json` so the
   agent auto-runs `make test` before declaring work done — this is a harness
   hook, configured via the `update-config` skill, **not** part of this skill.

If a layer is missing, offer to add it from `references/templates/` —
non-destructively, matched to the stack, scaled to how mature the code is
(don't force full CI onto a throwaway spike).

## The gate — run this before you commit / push / PR

1. **Detect the commands.** Prefer the `Makefile` (it's the source of truth):
   `make test` (brain + worker, incl. real-Docker sandbox), `make test-brain`,
   `make test-worker`, `make demo`, `make lint` if present. Fall back to
   `cargo`/`uv`/`ruff` directly only if `make` has no matching target.
2. **Run, fast → slow, scoped to the change:**
   - format + lint (ruff for `worker/`, `cargo fmt`/`clippy` for `brain/`);
   - the tests covering the change (a worker-only change → `make test-worker`;
     a brain-only change → `make test-brain`; anything cross-cutting or before a
     push → full `make test`);
   - for a behavior change on the pipeline, `make demo` as a smoke check.
3. **Never push red.** If lint or a test fails, stop and report the failure with
   output; fix or surface it — do not push or open the PR over a red gate.
4. **Report honestly.** State exactly what ran and the result. If you skipped a
   layer (e.g. didn't run e2e), say so.

> ⚠️ Repo-specific footguns: `worker/tests/test_claim.py` TRUNCATEs
> `repair_jobs` (clears demo data — run tests before a demo or re-run
> `make demo` after). The warehouse tier-2 role (`sbflow_dev`) only seeds on a
> **fresh** volume — `make clean` before `make up` after pulling.

## Bootstrapping missing guardrails

When asked to "set up CI / hooks / prevent regressions" (or when you notice a
gate is absent):

1. **Inventory** what exists: `.pre-commit-config.yaml`? `.github/workflows/`?
   lint config? test targets? branch protection?
2. **Propose** the smallest useful addition first, then offer the rest. Copy
   from `references/templates/` and adapt to the stack — do not paste a generic
   template blind.
3. **Wire it end to end:** for hooks, print the `pre-commit install` commands;
   for CI, ensure the workflow's jobs mirror `make test` so local == CI; call
   out that **branch protection + required checks** on `main` is a one-time repo
   *Settings* change the human must make (the agent can't).

## This repo's specifics

- **Stack:** Rust brain (`brain/`, axum + sqlx), Python 3.12 worker (`worker/`,
  uv + psycopg3), Docker Compose (postgres + warehouse + brain + worker), a
  pre-baked dbt sandbox image. No local Rust/uv toolchain — everything runs in
  containers via the `Makefile`.
- **Determinism:** the LLM is an injected `LlmProvider`; CI and the test gate use
  the **record/replay** provider (keyless, deterministic). A real model belongs
  only in a separate, **non-blocking** eval job — never in the blocking gate.
- **Protect the frozen contracts:** `Failure` (webhook in), `RepairResult`
  (worker out), and the agent tool contract are stable into phase B. A change to
  their shape needs a deliberate contract-test update + a note, not a silent
  edit. Also protect the invariants: "no fix reaches a PR without passing tier-1
  compile," "out-of-scope failures are never dispatched."

## Reference material (load as needed)

- `references/test-strategy.md` — the test pyramid for this repo, determinism,
  coverage ratchet, contract/invariant tests, flaky handling.
- `references/git-hooks.md` — pre-commit/pre-push setup, commit conventions.
- `references/ci-cd.md` — GitHub Actions layout, required checks, branch
  protection, security scans, release automation.
- `references/pr-hygiene.md` — PR template, CODEOWNERS, review checklist.
- `references/templates/` — copy-paste starters (pre-commit config, CI workflow,
  PR template, CODEOWNERS).
