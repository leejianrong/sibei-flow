# sibei-flow enrollment snippets (U1)

Copy-paste-ready detection affordances. Enrolling a pipeline is essentially one
line (R1.2): a failure POSTs the frozen `Failure` contract to the brain's
webhook, and sibei-flow takes it from there. No standing access to your infra is
required — the brain receives a structured payload, not scraped logs (ADR-0004).

The `Failure` contract every path posts (frozen — see `CLAUDE.md`):

```json
{"repo": "...", "run_id": "...", "task_id": "...", "node_uid": "...",
 "error_text": "...", "adapter": "postgres",
 "run_results_ref": "target/run_results.json", "source": "airflow|dbt|cli"}
```

Shared configuration (no secrets in the snippets themselves):

| env var              | meaning                                 | default                        |
|----------------------|-----------------------------------------|--------------------------------|
| `SBFLOW_WEBHOOK_URL` | brain webhook endpoint                  | `http://localhost:8080/webhook`|
| `SBFLOW_REPO`        | `owner/name` of the pipeline's repo     | `acme/analytics`               |
| `SBFLOW_ADAPTER`     | warehouse adapter                       | `postgres`                     |

## 1. Airflow — `on_failure_callback`

[`airflow_on_failure_callback.py`](airflow_on_failure_callback.py). Wire it as
the `on_failure_callback` on a DAG, task, or `default_args`:

```python
from sbflow_on_failure_callback import sbflow_on_failure

with DAG(..., default_args={"on_failure_callback": sbflow_on_failure}):
    ...
```

## 2. dbt

The one-line dbt enrollment is to run dbt **under the cron wrapper** — it reads
the failed node out of `target/run_results.json` and POSTs the `Failure`:

```bash
sbflow run -- dbt build
```

(That is also the right pattern for any cron/script step; see below.)

For a dbt-native complement, [`dbt/macros/sbflow_on_run_end.sql`](dbt/macros/sbflow_on_run_end.sql)
is an `on-run-end` hook that logs a greppable `SBFLOW_FAILURE {…}` marker per
failed node. Add one line to `dbt_project.yml`:

```yaml
on-run-end:
  - "{{ sbflow_on_run_end(results) }}"
```

Note: dbt hooks are Jinja/SQL and cannot make HTTP calls, so the macro only
*surfaces* failures in the dbt log; the actual POST happens on the wrapper (or,
for dbt-in-Airflow, the `on_failure_callback` above). See the macro's header for
the rationale.

## 3. Cron / plain scripts — `sbflow run`

For anything without an orchestrator callback, wrap the command:

```bash
sbflow run -- <your command>          # e.g. sbflow run -- dbt build
```

`sbflow run` streams the command's output, passes its exit code straight
through, and POSTs a `Failure` (`source: "cli"`) only when the command exits
non-zero. See the repo README ("Onboarding & the `sbflow` CLI") for config and
options.
