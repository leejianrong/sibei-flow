"""N13 `needs_prod_action` rule (V5 task 3, story 16) — fast / no-infra.

Two layers:
- pure logic of :mod:`sbflow_worker.agent.prodaction` (incremental detection,
  retype vs removal vs safe-rename), driven by plain strings; and
- an end-to-end `run_repair` check that an **incremental model + non-rename
  drift** yields ``outcome = needs_prod_action`` with a recommendation and **no
  diff** — so the PR opener (which only opens ``pr_proposed``) can never turn it
  into a prod-assuming PR.

No DB / warehouse / Docker: the model is a `tmp_path` file, the LLM is the
replay provider, and no sandbox/warehouse is wired. Runs in `make test-fast`.
"""

from __future__ import annotations

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy
from sbflow_worker.agent.loop import run_repair
from sbflow_worker.agent.prodaction import (
    drift_requires_prod_action,
    is_incremental_model,
    is_retype_drift,
    parse_missing_column,
)
from sbflow_worker.agent.source import LocalSourceProvider
from sbflow_worker.agent.tools import AgentContext
from sbflow_worker.llm.replay import ReplayProvider

INCREMENTAL_MODEL = """\
{{ config(materialized='incremental', unique_key='event_id') }}

with events as (
    select * from {{ source('raw', 'raw_events') }}
)

select
    event_id,
    event_ts,
    amount
from events
"""

PLAIN_MODEL = """\
{{ config(materialized='view') }}

select
    event_id,
    event_ts,
    amount
from {{ source('raw', 'raw_events') }}
"""

RETYPE_ERROR = 'column "amount" is of type numeric but expression is of type text'
REMOVAL_ERROR = 'column "legacy_flag" does not exist'
RENAME_ERROR = 'column "customer_id" does not exist'


# --- pure logic ------------------------------------------------------------


def test_is_incremental_model_detects_config():
    assert is_incremental_model(INCREMENTAL_MODEL)
    assert is_incremental_model('{{ config(materialized = "incremental") }}')
    assert not is_incremental_model(PLAIN_MODEL)
    assert not is_incremental_model("select 1")


def test_is_retype_drift_across_dialects():
    assert is_retype_drift(RETYPE_ERROR)  # Postgres
    assert is_retype_drift("Cannot coerce expression amount to type INT64")  # BQ
    assert is_retype_drift("Expression type does not match column data type")  # SF
    assert not is_retype_drift(REMOVAL_ERROR)


def test_parse_missing_column():
    assert parse_missing_column(RETYPE_ERROR) == "amount"
    assert parse_missing_column(RENAME_ERROR) == "customer_id"
    assert parse_missing_column("invalid identifier 'ORDER_TS'") == "ORDER_TS"
    assert parse_missing_column("Unrecognized name: order_ts") == "order_ts"


def test_retype_on_incremental_requires_prod_action():
    rec = drift_requires_prod_action(
        model_source=INCREMENTAL_MODEL,
        error_text=RETYPE_ERROR,
        node_uid="model.analytics.daily_metrics",
    )
    assert rec is not None
    assert "incremental" in rec.lower()
    assert "full-refresh" in rec.lower()
    assert "amount" in rec
    # The recommendation is explicit that sibei-flow will not act on prod.
    assert "no prod-write credentials" in rec.lower()


def test_removal_on_incremental_requires_prod_action():
    # The missing column has no similarly-named replacement upstream → removal.
    rec = drift_requires_prod_action(
        model_source=INCREMENTAL_MODEL,
        error_text=REMOVAL_ERROR,
        node_uid="model.analytics.daily_metrics",
        current_columns=["event_id", "event_ts", "amount"],
    )
    assert rec is not None
    assert "legacy_flag" in rec


def test_safe_rename_on_incremental_is_not_prod_action():
    # A similarly-named replacement exists (customer_id -> cust_id): the aliased
    # fix preserves the output contract, so it stays a normal fix even here.
    rec = drift_requires_prod_action(
        model_source=INCREMENTAL_MODEL,
        error_text=RENAME_ERROR,
        node_uid="model.analytics.daily_metrics",
        current_columns=["cust_id", "order_ts", "amount"],
    )
    assert rec is None


def test_non_incremental_retype_is_not_prod_action():
    # The same retype on a view/table model is fixable in SQL — no prod action.
    rec = drift_requires_prod_action(
        model_source=PLAIN_MODEL,
        error_text=RETYPE_ERROR,
        node_uid="model.analytics.daily_metrics",
    )
    assert rec is None


def test_missing_column_without_schema_does_not_fire():
    # Can't tell rename from removal without the current columns → leave it to
    # the normal fix path (its compile gate still protects the output).
    rec = drift_requires_prod_action(
        model_source=INCREMENTAL_MODEL,
        error_text=REMOVAL_ERROR,
        node_uid="model.analytics.daily_metrics",
        current_columns=None,
    )
    assert rec is None


# --- end-to-end through the bounded loop -----------------------------------


def test_incremental_retype_yields_needs_prod_action_and_no_pr(tmp_path):
    model_rel = "models/daily_metrics.sql"
    (tmp_path / "models").mkdir()
    (tmp_path / model_rel).write_text(INCREMENTAL_MODEL)

    # The replay agent even drafts a plausible cast edit; the gate must override
    # to needs_prod_action and DROP the diff so no prod-assuming PR is possible.
    provider = ReplayProvider(
        [
            {"tool_calls": [{"name": "read_file", "input": {"path": model_rel}}]},
            {
                "tool_calls": [
                    {
                        "name": "edit_file",
                        "input": {
                            "path": model_rel,
                            "old_string": "    amount\n",
                            "new_string": "    amount::numeric as amount\n",
                        },
                    }
                ]
            },
            {"text": "Cast amount back to numeric."},
        ]
    )
    ctx = AgentContext(
        source=LocalSourceProvider(str(tmp_path)),
        warehouse=None,
        working=WorkingCopy(),
        guard=DiffGuard(max_lines=40),
        allowed_paths={model_rel},
        sandbox=None,
        model_select="daily_metrics",
    )
    task = {
        "repo": "acme/analytics",
        "node_uid": "model.analytics.daily_metrics",
        "adapter": "postgres",
        "error_text": RETYPE_ERROR,
        "failing_file": model_rel,
    }

    result = run_repair(provider, ctx, task, max_turns=6)

    assert result["outcome"] == "needs_prod_action"
    # No code change is proposed: the opener only ever opens `pr_proposed`, and a
    # needs_prod_action result carries no diff — prod is never touched.
    assert "diff" not in result
    assert result.get("evidence") is None
    rec = result["explanation"]
    assert "incremental" in rec.lower() and "amount" in rec
    assert any("needs_prod_action" in line for line in result["transcript"])
