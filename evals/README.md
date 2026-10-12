# Eval harness

Measures how well sibei-flow repairs failures, against a corpus of cases, with a
real model. CI and the demo run on the scripted `replay` provider, which proves
the plumbing but says nothing about quality. This is the thing that does.

```bash
make eval                                   # all cases, replay provider (keyless smoke)
make eval LLM_PROVIDER=claude ANTHROPIC_API_KEY=...   # a real-model run
make eval ONLY=rename_customer_id           # one case
```

It needs the compose stack's `postgres` and `warehouse`, Docker (for the dbt
sandbox) and, for `out_of_scope` cases, a running brain (`make up`). The JSON
report lands in `evals/reports/` (git-ignored; commit a report you want to keep
under `docs/eval/`).

## What a run does

- **Dispatched cases** run through `build_processor(cfg)`, the same callable the
  claim loop gives every claimed job: the real agent loop, real tools, real
  warehouse schema reads, and the ephemeral dbt sandbox with the tier-1 compile
  gate. The DB claim is skipped on purpose (it has its own tests and would only
  add queue noise to a measurement of repair quality).
- **`out_of_scope` cases** never reach the worker; the brain's classifier drops
  them. The harness POSTs the failure to the brain and checks what it recorded.
  Without `SBFLOW_BRAIN_URL` these are reported as `skipped`.

## Case format

```
evals/cases/<id>/
  case.json        # id (= dir name), description, failure, expect, optional project/replay
  project/         # the BROKEN dbt project (or point "project" at a shared one)
  warehouse.sql    # optional: runs on the warehouse ADMIN connection before the case
  teardown.sql     # optional: runs after the case
```

`case.json` (see `worker/sbflow_worker/evalkit/case.py` for the validated schema):

```json
{
  "id": "rename_customer_id",
  "description": "one line",
  "failure": { "repo": "...", "run_id": "...", "task_id": "...", "node_uid": "model.proj.name",
               "error_text": "...", "adapter": "postgres", "source": "airflow" },
  "expect": { "outcome": "pr_proposed", "diff_contains": ["cust_id"] },
  "project": "project",
  "replay": "path/to/session.json"
}
```

- `expect.outcome` is one of `pr_proposed | no_fix | needs_prod_action | out_of_scope`.
  Be honest: a dropped column or an incremental-model change is usually
  `needs_prod_action` or `no_fix`, not a fix.
- `expect.diff_contains` lists substrings a **correct** fix must contain. Cases
  without it count as "needs manual review" when a PR is proposed, because a fix
  can compile and still be wrong.
- Give each case its **own** warehouse schema (`eval_<id>`) in `warehouse.sql`
  and drop it in `teardown.sql`. The bundled smoke case reuses `raw` only
  because it is a no-op against the state the fixture warehouse boots into.
- `replay` is only for keyless runs; real-model runs ignore it.

## Reading the report

| Number | Meaning |
|---|---|
| **pass rate** | cases where the actual outcome matched (and `diff_contains` held) |
| **fix rate** | of cases that expect a fix, the share that passed |
| **false-fix rate** | of cases that expect *no* fix, the share where a PR was proposed anyway. The number to watch: it is how often the tool is confidently wrong. |
| **needs manual review** | proposed diffs with no `diff_contains`; read them |
| **latency** | p50/max seconds per dispatched case |

Errors and skips are counted separately and are kept out of the rates, so they
cannot flatter them.

Not yet reported: cost per case. The Satay journal records model usage, but the
harness does not read it yet (tracked on the board).
