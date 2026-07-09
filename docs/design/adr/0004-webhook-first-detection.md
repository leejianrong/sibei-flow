# ADR-0004: Webhook-first failure detection

- **Status:** Accepted
- **Date:** 2026-07-09

## Context

sibei-flow must learn that a task failed without demanding standing access to
the user's infrastructure. Options: tail logs, read exit codes, receive a
webhook/callback, poll the orchestrator API, or wrap execution.

## Decision

**Primary detection = a failure webhook/callback** from the orchestrator
(Airflow `on_failure_callback`, dbt run results/exit codes, Dagster
run-failure hooks). **Fallback = a thin `sbflow run -- <cmd>` CLI wrapper** for
cron/scripts that lack callbacks. Log-tailing is deferred (brittle across
formats/versions).

## Consequences

- Onboarding is one config line; no standing cluster access required.
- We receive a structured payload (task id, error, context) rather than
  scraping logs.
- **v1 flagship = dbt running inside Airflow** — one integration story that
  covers the ICP (resolves tension T4). Standalone dbt and cron/scripts are
  handled via the `sbflow run --` CLI wrapper. Dagster/Prefect and other
  orchestrator adapters are fast-follows.

## Alternatives considered

- **Log-tailing for "zero integration"** — rejected as the foundation; brittle.
  May return as an enhancement.
