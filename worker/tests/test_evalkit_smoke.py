"""SIB-81 — the eval harness end to end on the bundled flagship case.

Runs the keyless replay path through the real agent loop, warehouse schema reads
and the ephemeral dbt sandbox, then checks the harness scored it. This is the
CI-safe smoke subset: real-model runs are manual (`make eval LLM_PROVIDER=claude`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from sbflow_worker.config import Config
from sbflow_worker.evalkit import discover, summarize
from sbflow_worker.evalkit.runner import RunEnv, run_case
from sbflow_worker.sandbox.runner import SandboxError, SandboxRunner

REPO = Path(__file__).resolve().parents[2]
ADMIN_URL = os.environ.get(
    "WAREHOUSE_ADMIN_URL", "postgres://sibei:sibei@warehouse:5432/warehouse"
)

pytestmark = [
    pytest.mark.infra,
    pytest.mark.skipif(
        shutil.which("docker") is None, reason="docker CLI not available"
    ),
]


@pytest.fixture(scope="module")
def cfg() -> Config:
    base = Config.from_env()
    if not base.warehouse_url:
        pytest.skip("WAREHOUSE_URL not set")
    runner = SandboxRunner(repo_root=str(REPO / "fixtures" / "dbt_project"))
    try:
        runner.ensure_image()
    except SandboxError as e:
        pytest.skip(f"sandbox image unavailable: {e}")
    return base


def test_flagship_case_is_scored_pass_on_the_replay_provider(cfg):
    (case,) = discover(REPO / "evals" / "cases", {"rename_customer_id"})
    result = run_case(case, cfg, RunEnv(warehouse_admin_url=ADMIN_URL))

    assert result.actual == "pr_proposed", result.detail
    assert result.passed and result.fix_correct is True
    assert result.tier1_passed is True  # the compile gate ran and passed

    summary = summarize([result], {"provider": cfg.llm_provider})
    assert summary["fix_rate"] == 1.0 and summary["false_fix_rate"] is None


def test_out_of_scope_control_is_skipped_without_a_brain(cfg):
    (case,) = discover(REPO / "evals" / "cases", {"control_statement_timeout"})
    result = run_case(case, cfg, RunEnv())
    assert result.actual == "skipped" and not result.passed
