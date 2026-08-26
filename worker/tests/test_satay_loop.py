"""KAN-648 (ADR-0012 decision 4, slice 1) — flag-off/flag-on parity.

`run_repair_satay` (`sbflow_worker.agent.satay_loop`) must be a **port**, not a
rewrite: for the same inputs it must produce the same `RepairResult` as
`run_repair` (`sbflow_worker.agent.loop`), turn for turn — modulo `transcript`
(KAN-649): `run_repair` still emits `{"kind": "lines", ...}` (unchanged, per the
frozen-contract invariant this module also guards), while `run_repair_satay` now
emits `{"kind": "journal", "run_id": ..., "ref": ...}` pointing at a *persisted*
journal. `_assert_parity` below therefore compares everything BUT `transcript`
for equality, and separately asserts the satay side's transcript is a real,
resolvable journal arm — see `_pop_transcript`/`_assert_journal_transcript`.

Each test below builds two independent, freshly-loaded contexts/providers (state
in both is mutated as the loop runs, so they cannot be shared) and runs the
identical scenario through both paths, each satay call against its own
`tmp_path`-scoped journal file (so tests never share or collide on state, unlike
the shared file `Config.satay_journal_dir` points at in production).

The first two scenarios need no external infrastructure at all — no warehouse, no
sandbox/Docker — so this module carries no `infra` marker for them and runs under
`make test-fast`. The remaining scenarios reuse the warehouse- and Docker-backed
fixtures from `test_agent_loop.py` / `test_sandbox.py` and are marked `infra`
individually.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy
from sbflow_worker.agent.loop import run_repair
from sbflow_worker.agent.satay_loop import run_repair_satay
from sbflow_worker.agent.source import LocalSourceProvider
from sbflow_worker.agent.tools import AgentContext
from sbflow_worker.llm.replay import ReplayProvider

REPO = str(Path(__file__).resolve().parents[2] / "fixtures" / "dbt_project")
MODEL = "models/marts/orders.sql"


def _task() -> dict[str, object]:
    return {
        "repo": "acme/analytics",
        "node_uid": "model.analytics.orders",
        "adapter": "postgres",
        "error_text": 'column "customer_id" does not exist',
        "failing_file": MODEL,
    }


def _ctx(warehouse=None, allowed=frozenset({MODEL}), sandbox=None, model_select=None):
    from sbflow_worker.agent.schema import WarehouseSchema

    return AgentContext(
        source=LocalSourceProvider(REPO),
        warehouse=WarehouseSchema(warehouse) if warehouse else None,
        working=WorkingCopy(),
        guard=DiffGuard(max_lines=40),
        allowed_paths=set(allowed),
        sandbox=sandbox,
        model_select=model_select,
    )


def _pop_transcript(result: dict) -> tuple[dict, dict | None]:
    """Split `result` into (everything else, its `transcript`), popping the latter.

    `transcript` is the one field the two paths are *allowed* to disagree on
    (KAN-649) — everything else must still match exactly for parity to hold.
    """
    result = dict(result)
    return result, result.pop("transcript", None)


def _assert_journal_transcript(transcript: dict | None, *, journal_path: Path) -> str:
    """Assert `transcript` is a well-formed `journal` arm pointing at a run that is
    really persisted in `journal_path`, and return its `run_id`.

    Opens the same file `run_repair_satay` wrote to and reads the run's events back
    with satay's own public `SQLiteStore.read_events` — the same check a reader
    outside this process (namely `brain/src/pr/body.rs`) needs to succeed.
    """
    assert transcript is not None
    assert transcript["kind"] == "journal"
    run_id = transcript["run_id"]
    assert isinstance(run_id, str) and run_id
    assert transcript["ref"] == journal_path.name

    from satay.journal.store import SQLiteStore

    store = SQLiteStore.open(journal_path)
    try:
        events = asyncio.run(store.read_events(run_id))
    finally:
        store.close()
    assert events, f"no events persisted for run {run_id!r} in {journal_path}"
    return run_id


def _assert_parity(
    scripted_turns, tmp_path: Path, *, ctx_kwargs=None, max_turns=6
) -> None:
    """Run identical scripted turns through both paths; assert equal RepairResult
    (modulo `transcript` — see the module docstring), and that the satay side's
    transcript really resolves against its own persisted journal.
    """
    ctx_kwargs = ctx_kwargs or {}

    sync_result = run_repair(
        ReplayProvider(list(scripted_turns)),
        _ctx(**ctx_kwargs),
        _task(),
        max_turns=max_turns,
    )
    journal_path = tmp_path / "satay.db"
    satay_result = run_repair_satay(
        ReplayProvider(list(scripted_turns)),
        _ctx(**ctx_kwargs),
        _task(),
        max_turns=max_turns,
        journal_path=journal_path,
    )

    sync_rest, sync_transcript = _pop_transcript(sync_result)
    satay_rest, satay_transcript = _pop_transcript(satay_result)
    assert satay_rest == sync_rest
    assert sync_transcript is not None and sync_transcript["kind"] == "lines"
    _assert_journal_transcript(satay_transcript, journal_path=journal_path)


# --- no-infra scenarios (satay's own store is in-memory; no warehouse/Docker) ------


def test_parity_diff_guard_rejects_then_redrafts(tmp_path):
    scripted = [
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
    _assert_parity(scripted, tmp_path, ctx_kwargs={"allowed": {MODEL}})


def test_parity_loop_stops_at_cap_and_returns_no_fix(tmp_path):
    scripted = [{"tool_calls": [{"name": "read_file", "input": {"path": MODEL}}]}] * 10
    _assert_parity(scripted, tmp_path, max_turns=3)


def test_parity_unverified_draft_no_sandbox_configured(tmp_path):
    # No warehouse, no sandbox: exercises the ctx.sandbox is None → unverified
    # pr_proposed tail on both paths, with a real (warehouse-free) tool dispatch.
    scripted = [
        {
            "tool_calls": [
                {
                    "name": "edit_file",
                    "input": {
                        "path": MODEL,
                        "old_string": "customer_id,",
                        "new_string": "cust_id as customer_id,",
                    },
                }
            ]
        },
        {"text": "Aliased cust_id back to customer_id."},
    ]
    _assert_parity(scripted, tmp_path)


# --- infra: warehouse only (get_schema through a real read-only connection) -------

WAREHOUSE_URL = os.environ.get(
    "WAREHOUSE_URL", "postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse"
)
RENAME_SESSION = str(
    Path(__file__).resolve().parents[1]
    / "sbflow_worker"
    / "replays"
    / "rename_drift.json"
)


@pytest.mark.infra
def test_parity_rename_drift_with_real_warehouse_read(tmp_path):
    import json

    scripted = json.loads(Path(RENAME_SESSION).read_text())["turns"]
    _assert_parity(scripted, tmp_path, ctx_kwargs={"warehouse": WAREHOUSE_URL})


# --- infra + docker: the real sandbox, both the terminal compile gate and the ----
# --- mid-loop run_sandbox tool call that primes the cache reused by the gate. ----

SAMPLE_URL = os.environ.get(
    "SAMPLE_WAREHOUSE_URL", "postgres://sbflow_dev:sbflow_dev@warehouse:5432/warehouse"
)
NETWORK = os.environ.get("SANDBOX_NETWORK", "sibei-flow_default")
WORK_DIR = os.environ.get("SANDBOX_WORK_DIR", "/tmp/sbflow-sandbox")

pytestmark_sandbox = [
    pytest.mark.infra,
    pytest.mark.skipif(
        shutil.which("docker") is None, reason="docker CLI not available"
    ),
]


def _runner(sample_url):
    from sbflow_worker.sandbox.runner import SandboxRunner

    return SandboxRunner(
        repo_root=REPO,
        warehouse_url=WAREHOUSE_URL,
        sample_url=sample_url,
        network=NETWORK,
        work_dir=WORK_DIR,
        timeout=180,
        build_context=None,  # image is pre-baked on the host
    )


@pytest.fixture(scope="module", autouse=True)
def _image():
    from sbflow_worker.sandbox.runner import SandboxError

    try:
        _runner(SAMPLE_URL).ensure_image()
    except SandboxError as e:
        pytest.skip(f"sandbox image unavailable: {e}")


def _normalized(result: dict) -> dict:
    """`result` with real-subprocess log text blanked out before comparing.

    `evidence.tier{1,2}.log` is dbt's captured stdout/stderr, which embeds a real
    wall-clock timestamp per line. The sync path and the satay path each spawn
    their OWN real `docker run` (deliberately — the two scenarios must not share
    mutable state), so their raw logs differ in timestamp even when everything
    that matters (pass/fail, node status, output schema, score) is identical.
    That is a property of running two independent real subprocesses, not a
    parity gap — normalize it away so the comparison asserts what the port
    actually promises. Also pops `transcript` (KAN-649 — see the module
    docstring: the two paths legitimately disagree on it, so it is asserted
    separately via `_assert_journal_transcript`, not folded into this equality).
    """
    result = dict(result)
    evidence = result.get("evidence")
    if evidence:
        evidence = {
            k: (dict(v) if isinstance(v, dict) else v) for k, v in evidence.items()
        }
        for tier in ("tier1", "tier2"):
            if tier in evidence and evidence[tier].get("log"):
                evidence[tier] = {**evidence[tier], "log": "<normalized>"}
        result["evidence"] = evidence
    result.pop("transcript", None)
    return result


@pytest.mark.infra
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_parity_passing_fix_carries_sandbox_evidence(tmp_path):
    import json

    scripted = json.loads(Path(RENAME_SESSION).read_text())["turns"]

    def ctx():
        return _ctx(
            warehouse=WAREHOUSE_URL,
            sandbox=_runner(SAMPLE_URL),
            model_select="orders",
        )

    sync_result = run_repair(
        ReplayProvider(list(scripted)), ctx(), _task(), max_turns=6
    )
    journal_path = tmp_path / "satay.db"
    satay_result = run_repair_satay(
        ReplayProvider(list(scripted)),
        ctx(),
        _task(),
        max_turns=6,
        journal_path=journal_path,
    )

    # Parity is the whole point of this test — assert it unconditionally. Whether
    # tier-1 actually *passes* in a given environment (a real `dbt compile` against
    # the fixture warehouse) is exactly what `test_sandbox.py` already asserts and
    # is not re-asserted here, so this test's verdict is not hostage to sandbox
    # environment flakiness unrelated to the port.
    assert _normalized(satay_result) == _normalized(sync_result)
    assert sync_result["evidence"]["tier1"]["ran"] is True
    assert sync_result["transcript"]["kind"] == "lines"
    _assert_journal_transcript(satay_result["transcript"], journal_path=journal_path)


@pytest.mark.infra
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_parity_non_compiling_draft_suppressed_via_mid_loop_run_sandbox(tmp_path):
    # Exercises the run_sandbox tool call mid-loop (caches ctx.last_run), which the
    # terminal compile gate then reuses without a second sandbox run — on both paths.
    scripted = [
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

    def ctx():
        return _ctx(
            warehouse=WAREHOUSE_URL,
            sandbox=_runner(SAMPLE_URL),
            model_select="orders",
        )

    sync_result = run_repair(
        ReplayProvider(list(scripted)), ctx(), _task(), max_turns=6
    )
    journal_path = tmp_path / "satay.db"
    satay_result = run_repair_satay(
        ReplayProvider(list(scripted)),
        ctx(),
        _task(),
        max_turns=6,
        journal_path=journal_path,
    )

    assert _normalized(satay_result) == _normalized(sync_result)
    # This scenario points the model at a table that does not exist, so tier-1
    # must fail regardless of environment — unlike the "passing fix" scenario
    # above, this outcome does not depend on the fixture warehouse's state.
    assert sync_result["outcome"] == "no_fix"
    assert sync_result["evidence"]["tier1"]["passed"] is False
    assert sync_result["transcript"]["kind"] == "lines"
    _assert_journal_transcript(satay_result["transcript"], journal_path=journal_path)
