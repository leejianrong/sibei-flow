# ADR-0006: Tiered verification in an ephemeral sandbox

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

"Self-healing" is only credible if a proposed fix is verified before a human is
asked to trust it — but we hold no prod-write access (ADR-0005) and shouldn't
require prod access to verify.

## Decision

Verify each candidate fix in **tiers**, from cheap-and-safe to expensive-and-real:

1. **Always:** compile/lint/parse in an **ephemeral Docker container** on the
   brain's host (v1 runtimes: **Python + dbt**, SQL via `dbt compile`/
   `dbt build`).
2. **If configured:** run against a **sample** or the user's **dev/staging**
   target (optional read-only connection).
3. **Prod:** only after human approval (the merge), via the user's normal
   pipeline run.

The **evidence** from tiers 1–2 (e.g. "compiled ✓ · ran on 10k-row sample ✓ ·
output schema unchanged ✓") is attached to the PR.

## Consequences

- The human's approval is informed, not a leap of faith; directly attacks the
  "runtime-only bugs" weakness of config-driven tools.
- v1 depth is dbt + Python only; Spark and other SQL engines deferred.
- Sample/dev verification is best-effort and optional; absence is disclosed in
  the PR rather than blocking a fix.

## Alternatives considered

- **Re-run against prod after approval only** — rejected as the sole check:
  gives the reviewer no pre-merge signal.
