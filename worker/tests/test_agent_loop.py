"""PRD Seam 1 (first cut) — the repair worker contract, record/replay LLM.

Asserts external, observable behavior of the agent loop:
- rename-drift fixture → a minimal drafted diff that updates the model to the
  new column; explanation names the drift; transcript present; unverified
  (evidence is null in V2).
- the diff guard rejects an out-of-scope edit and forces a re-draft.
- the loop stops at N turns and returns `no_fix` (story 29).

The rename test reads the fixture warehouse (get_schema); it needs
`WAREHOUSE_URL` (the compose `warehouse` service). The guard/cap tests are
warehouse-free.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy
from sbflow_worker.agent.loop import run_repair
from sbflow_worker.agent.source import LocalSourceProvider
from sbflow_worker.agent.tools import AgentContext
from sbflow_worker.llm.replay import ReplayProvider

REPO = str(Path(__file__).resolve().parents[2] / "fixtures" / "dbt_project")
MODEL = "models/marts/orders.sql"

# The rename test reads the fixture warehouse (WAREHOUSE_URL); mark the module as
# infra-dependent so `-m "not infra"` skips it. Deselect with `-m "not infra"`.
pytestmark = pytest.mark.infra
WAREHOUSE_URL = os.environ.get(
    "WAREHOUSE_URL", "postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse"
)
RENAME_SESSION = str(
    Path(__file__).resolve().parents[1]
    / "sbflow_worker"
    / "replays"
    / "rename_drift.json"
)


def _ctx(warehouse=None, allowed=frozenset({MODEL})):
    from sbflow_worker.agent.schema import WarehouseSchema

    return AgentContext(
        source=LocalSourceProvider(REPO),
        warehouse=WarehouseSchema(warehouse) if warehouse else None,
        working=WorkingCopy(),
        guard=DiffGuard(max_lines=40),
        allowed_paths=set(allowed),
    )


def _task():
    return {
        "repo": "acme/analytics",
        "node_uid": "model.analytics.orders",
        "adapter": "postgres",
        "error_text": 'column "customer_id" does not exist',
        "failing_file": MODEL,
    }


def test_rename_drift_produces_minimal_unverified_diff():
    provider = ReplayProvider.from_file(RENAME_SESSION)
    ctx = _ctx(warehouse=WAREHOUSE_URL)

    result = run_repair(provider, ctx, _task(), max_turns=6)

    assert result["outcome"] == "pr_proposed"
    diff = result["diff"]
    assert "-    customer_id," in diff
    # The fix aliases cust_id back to customer_id (preserves the output contract).
    assert "+    cust_id as customer_id," in diff
    # Minimal: only the failing model, only the one column line.
    assert diff.count("\n+") <= 3 and MODEL in diff
    # Explanation names the drift; transcript present; unverified (no evidence).
    assert "cust_id" in result["explanation"]
    assert result["transcript"]
    assert result["evidence"] is None
    # The transcript shows the real warehouse read confirming the drift.
    assert any("cust_id" in line for line in result["transcript"])


def test_diff_guard_rejects_out_of_scope_edit_then_redrafts():
    # Turn 1 tries to edit an out-of-scope file (rejected); turn 2 fixes the
    # real model; turn 3 explains.
    provider = ReplayProvider(
        [
            {
                "tool_calls": [
                    {
                        "name": "edit_file",
                        "input": {
                            "path": "models/marts/schema.yml",
                            "old_string": "orders",
                            "new_string": "orders_v2",
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "name": "edit_file",
                        "input": {
                            "path": MODEL,
                            "old_string": "customer_id,",
                            "new_string": "cust_id,",
                        },
                    }
                ]
            },
            {"text": "Renamed customer_id to cust_id on the failing model only."},
        ]
    )
    # Only the model file is in scope (not schema.yml).
    ctx = _ctx(allowed={MODEL})

    result = run_repair(provider, ctx, _task(), max_turns=6)

    assert result["outcome"] == "pr_proposed"
    assert "cust_id," in result["diff"]
    assert "schema.yml" not in result["diff"]  # the out-of-scope edit never landed
    assert any("diff guard rejected" in line for line in result["transcript"])


def test_loop_stops_at_cap_and_returns_no_fix():
    # A model that only ever reads files (never edits) must give up cleanly.
    provider = ReplayProvider(
        [{"tool_calls": [{"name": "read_file", "input": {"path": MODEL}}]}] * 10
    )
    ctx = _ctx()

    result = run_repair(provider, ctx, _task(), max_turns=3)

    assert result["outcome"] == "no_fix"
    assert "diff" not in result
    # Capped at 3 provider turns (3 read_file calls in the transcript).
    assert sum(1 for line in result["transcript"] if line.startswith("→ read_file")) == 3
