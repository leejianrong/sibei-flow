"""PRD Seam 1 (V3) — the repair worker contract with the REAL Docker sandbox.

These assert external, observable behaviour across the worker contract, using
the record/replay LLM and a real ephemeral dbt sandbox (ADR-0006):

- a fix that fails tier-1 compile **never** yields ``pr_proposed`` (story 17);
- a passing fix carries evidence reflecting the tiers that **actually ran**;
- tier-2 absence is **disclosed** (``ran: false``), not omitted (R4.3);
- confidence/risk are populated and derived from the recorded signals.

Requirements: the Docker socket is available in the test container, the
``warehouse`` service is reachable, and the sandbox work dir is bind-mounted at
the same host path (see the container-based run command in the README).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy
from sbflow_worker.agent.loop import run_repair
from sbflow_worker.agent.schema import WarehouseSchema
from sbflow_worker.agent.source import LocalSourceProvider
from sbflow_worker.agent.tools import AgentContext
from sbflow_worker.llm.replay import ReplayProvider
from sbflow_worker.sandbox.runner import SandboxError, SandboxRunner

REPO = str(Path(__file__).resolve().parents[2] / "fixtures" / "dbt_project")
MODEL = "models/marts/orders.sql"
RENAME_SESSION = str(
    Path(__file__).resolve().parents[1] / "sbflow_worker" / "replays" / "rename_drift.json"
)
WAREHOUSE_URL = os.environ.get(
    "WAREHOUSE_URL", "postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse"
)
SAMPLE_URL = os.environ.get(
    "SAMPLE_WAREHOUSE_URL", "postgres://sbflow_dev:sbflow_dev@warehouse:5432/warehouse"
)
NETWORK = os.environ.get("SANDBOX_NETWORK", "sibei-flow_default")
WORK_DIR = os.environ.get("SANDBOX_WORK_DIR", "/tmp/sbflow-sandbox")

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not available"
)


def _runner(sample_url: str | None = SAMPLE_URL) -> SandboxRunner:
    return SandboxRunner(
        repo_root=REPO,
        warehouse_url=WAREHOUSE_URL,
        sample_url=sample_url,
        network=NETWORK,
        work_dir=WORK_DIR,
        timeout=180,
        build_context=None,  # image is pre-baked on the host
    )


def _ctx(sample_url: str | None = SAMPLE_URL) -> AgentContext:
    return AgentContext(
        source=LocalSourceProvider(REPO),
        warehouse=WarehouseSchema(WAREHOUSE_URL),
        working=WorkingCopy(),
        guard=DiffGuard(max_lines=40),
        allowed_paths={MODEL},
        sandbox=_runner(sample_url),
        model_select="orders",
    )


def _task():
    return {
        "repo": "acme/analytics",
        "node_uid": "model.analytics.orders",
        "adapter": "postgres",
        "error_text": 'column "customer_id" does not exist',
        "failing_file": MODEL,
    }


@pytest.fixture(scope="session", autouse=True)
def _image():
    try:
        _runner().ensure_image()
    except SandboxError as e:
        pytest.skip(f"sandbox image unavailable: {e}")


def test_passing_fix_carries_evidence_of_the_tiers_that_ran():
    provider = ReplayProvider.from_file(RENAME_SESSION)
    result = run_repair(provider, _ctx(sample_url=SAMPLE_URL), _task(), max_turns=6)

    assert result["outcome"] == "pr_proposed"
    ev = result["evidence"]
    # Compile always ran and passed.
    assert ev["tier1"]["ran"] is True and ev["tier1"]["passed"] is True
    # Sample ran and passed (a dev connection IS configured here).
    assert ev["tier2"]["ran"] is True and ev["tier2"]["passed"] is True
    # The alias fix preserves the output contract → schema unchanged.
    assert ev["output_schema"]["changed"] is False
    # Confidence/risk are populated and derived (low risk for the flagship).
    assert 0.0 < result["confidence"] <= 1.0
    assert result["risk_class"] == "low"
    assert result["factors"]


def test_tier2_absence_is_disclosed_not_omitted():
    provider = ReplayProvider.from_file(RENAME_SESSION)
    result = run_repair(provider, _ctx(sample_url=None), _task(), max_turns=6)

    assert result["outcome"] == "pr_proposed"  # tier-1 passed → still proposable
    ev = result["evidence"]
    assert ev["tier1"]["ran"] is True and ev["tier1"]["passed"] is True
    # Disclosure is intrinsic: tier-2 did not run, and that is a rendered fact.
    assert ev["tier2"]["ran"] is False
    assert ev["tier2"]["passed"] is None
    assert "not configured" in ev["tier2"]["log"]
    # No sample run → not "low" risk (calibrates the reviewer honestly).
    assert result["risk_class"] in {"medium", "high"}


def test_non_compiling_draft_is_suppressed_to_no_fix():
    # The edit points the source at a table that does not exist → dbt compile
    # errors → tier-1 fails → the draft must NEVER be proposed (story 17).
    provider = ReplayProvider(
        [
            {
                "tool_calls": [
                    {
                        "name": "edit_file",
                        "input": {
                            "path": MODEL,
                            "old_string": "'raw_customers'",
                            "new_string": "'does_not_exist'",
                        },
                    }
                ]
            },
            {"tool_calls": [{"name": "run_sandbox", "input": {"select": "orders"}}]},
            {"text": "Attempted a fix."},
        ]
    )
    result = run_repair(provider, _ctx(), _task(), max_turns=6)

    assert result["outcome"] == "no_fix"
    assert "diff" not in result  # nothing PR-eligible is emitted
    # Evidence still records that tier-1 was attempted and did not pass.
    assert result["evidence"]["tier1"]["ran"] is True
    assert result["evidence"]["tier1"]["passed"] is False
