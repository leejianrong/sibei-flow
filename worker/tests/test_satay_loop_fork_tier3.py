"""KAN-650 — fork-replay tier-3: the primitive ADR-0012's Consequences section
names as sibei-flow's "strongest long-term payoff": fork a completed candidate
run right after its recorded `get_schema` call, substitute a different diff,
and re-verify it against that *exact recorded* schema snapshot rather than a
fresh live warehouse query.

Three things this file proves, each independently:

1. `locate_get_schema_fork_point` finds the right call under the confirmed
   policy (last `get_schema` before any `edit_file`; errored calls excluded;
   degrades to `None` gracefully when there is nothing usable).
2. `reverify_with_fork` genuinely **reuses** the recorded `get_schema` call —
   it is never re-executed — proven structurally off the fork's own journal
   (mirroring `satay-runtime/examples/fork_and_compare_demo.py`'s
   `executed_here`/`fork_seq` pattern: a call at or below the `RunForked`
   marker's `seq` is copied history, not fresh work) *and* behaviourally (the
   fork's rig is wired to a warehouse mock that raises if ever called).
3. The fork genuinely **re-verifies the substituted diff**, not the original
   candidate's own diff — proven with a real sandbox: same source run, two
   different substituted diffs (one that compiles, one that does not), two
   different tier-3 verdicts.

Following `test_satay_loop.py`'s pattern: `tmp_path`-scoped journal files, a
freshly-built `AgentContext` per call (mutable state), the `replay` provider
for determinism. `get_schema` is exercised for real (through
`tools.py::AgentContext.get_schema` and the `_dispatch` durable call) but
`WarehouseSchema.describe` is monkeypatched to a canned, network-free response
so the "no re-query" proof needs no live warehouse — the point being tested is
the *fork mechanism*, not the SQL `WarehouseSchema` runs, which
`test_satay_loop.py`'s own infra-marked scenarios already cover with a real
connection.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy
from sbflow_worker.agent.satay_loop import (
    locate_get_schema_fork_point,
    reverify_with_fork,
    run_repair_satay,
)
from sbflow_worker.agent.schema import WarehouseSchema
from sbflow_worker.agent.source import LocalSourceProvider
from sbflow_worker.agent.tools import AgentContext
from sbflow_worker.llm.replay import ReplayProvider

REPO = str(Path(__file__).resolve().parents[2] / "fixtures" / "dbt_project")
MODEL = "models/marts/orders.sql"
#: Never actually connected to — every no-infra scenario here monkeypatches
#: `WarehouseSchema.describe` before it would be called for real.
FAKE_WAREHOUSE_URL = "postgres://unused:unused@nonexistent-host/warehouse"


def _task() -> dict[str, object]:
    return {
        "repo": "acme/analytics",
        "node_uid": "model.analytics.orders",
        "adapter": "postgres",
        "error_text": 'column "customer_id" does not exist',
        "failing_file": MODEL,
    }


def _ctx(warehouse=FAKE_WAREHOUSE_URL, sandbox=None, model_select="orders"):
    return AgentContext(
        source=LocalSourceProvider(REPO),
        warehouse=WarehouseSchema(warehouse) if warehouse else None,
        working=WorkingCopy(),
        guard=DiffGuard(max_lines=40),
        allowed_paths={MODEL},
        sandbox=sandbox,
        model_select=model_select,
    )


def _mock_schema_read(
    monkeypatch, text="Current columns (mocked): order_id, cust_id, amount"
):
    monkeypatch.setattr(WarehouseSchema, "describe", lambda self, source: text)


def _run_source(
    tmp_path: Path, scripted: list[dict], *, sandbox=None
) -> tuple[dict, Path]:
    """Drive a candidate through `run_repair_satay`; return (result, journal_path)."""
    journal_path = tmp_path / "satay.db"
    result = run_repair_satay(
        ReplayProvider(list(scripted)),
        _ctx(sandbox=sandbox),
        _task(),
        max_turns=8,
        journal_path=journal_path,
    )
    return result, journal_path


def _open_store(journal_path: Path):
    from satay.journal.store import SQLiteStore

    return SQLiteStore.open(journal_path)


EDIT_CALL = {
    "name": "edit_file",
    "input": {
        "path": MODEL,
        "old_string": "customer_id,",
        "new_string": "cust_id as customer_id,",
    },
}
GET_SCHEMA_CALL = {"name": "get_schema", "input": {"source": "raw.raw_customers"}}


# --- 1. locate_get_schema_fork_point: the policy -----------------------------


def test_locate_fork_point_picks_last_get_schema_before_first_edit_file(
    tmp_path, monkeypatch
):
    _mock_schema_read(monkeypatch)
    scripted = [
        {"tool_calls": [GET_SCHEMA_CALL]},  # _dispatch ordinal 0
        {"tool_calls": [GET_SCHEMA_CALL]},  # _dispatch ordinal 1 <- expected
        {"tool_calls": [EDIT_CALL]},  # _dispatch ordinal 2
        {"tool_calls": [GET_SCHEMA_CALL]},  # _dispatch ordinal 3, after the edit
        {"text": "done"},
    ]
    result, journal_path = _run_source(tmp_path, scripted)
    run_id = result["transcript"]["run_id"]

    store = _open_store(journal_path)
    try:
        fork_point = asyncio.run(locate_get_schema_fork_point(store, run_id))
    finally:
        store.close()

    assert fork_point is not None
    assert fork_point.get_schema_ordinal == 1
    assert fork_point.source == "raw.raw_customers"


def test_locate_fork_point_returns_none_with_no_get_schema_calls(tmp_path, monkeypatch):
    _mock_schema_read(monkeypatch)
    scripted = [{"tool_calls": [EDIT_CALL]}, {"text": "done"}]
    result, journal_path = _run_source(tmp_path, scripted)
    run_id = result["transcript"]["run_id"]

    store = _open_store(journal_path)
    try:
        fork_point = asyncio.run(locate_get_schema_fork_point(store, run_id))
    finally:
        store.close()

    assert fork_point is None


def test_locate_fork_point_excludes_errored_get_schema_calls(tmp_path):
    # No warehouse configured at all -> every get_schema call errors
    # ("no read-only warehouse connection configured"), so none are eligible.
    scripted = [{"tool_calls": [GET_SCHEMA_CALL]}, {"text": "done"}]
    journal_path = tmp_path / "satay.db"
    result = run_repair_satay(
        ReplayProvider(list(scripted)),
        _ctx(warehouse=None),
        _task(),
        max_turns=8,
        journal_path=journal_path,
    )
    run_id = result["transcript"]["run_id"]

    store = _open_store(journal_path)
    try:
        fork_point = asyncio.run(locate_get_schema_fork_point(store, run_id))
    finally:
        store.close()

    assert fork_point is None


def test_reverify_with_fork_degrades_gracefully_with_no_fork_point(
    tmp_path, monkeypatch
):
    """`reverify_with_fork` itself never crashes on a source run with no usable
    get_schema call — it discloses `tier3.ran = False`, mirroring tier1/tier2's
    own "disclose, don't fabricate" convention.
    """
    _mock_schema_read(monkeypatch)
    scripted = [{"tool_calls": [EDIT_CALL]}, {"text": "done"}]
    result, journal_path = _run_source(tmp_path, scripted)
    run_id = result["transcript"]["run_id"]

    outcome = reverify_with_fork(
        run_id,
        {MODEL: "select 1 as order_id"},
        _ctx(sandbox=None),
        journal_path=journal_path,
    )

    assert outcome.evidence["ran"] is False
    assert outcome.evidence["passed"] is None
    assert outcome.fork_run_id is None
    assert outcome.fresh_result is None
    assert "get_schema" in outcome.evidence["log"]


# --- 2. the fork genuinely reuses the recorded get_schema call --------------


SCRIPTED_ONE_GET_SCHEMA = [
    {"tool_calls": [{"name": "read_file", "input": {"path": MODEL}}]},  # ordinal 0
    {"tool_calls": [GET_SCHEMA_CALL]},  # ordinal 1 <- the fork point
    {"tool_calls": [EDIT_CALL]},  # ordinal 2
    {"text": "Aliased cust_id back to customer_id."},
]

SUBSTITUTED_CONTENT = (
    "with customers as (\n"
    "    select * from {{ source('raw', 'raw_customers') }}\n"
    ")\n\n"
    "select\n"
    "    cust_id as customer_id,\n"
    "    order_ts,\n"
    "    amount\n"
    "from customers\n"
)

#: A substituted diff engineered to fail tier-1, the same way
#: `test_sandbox.py`/`test_satay_loop.py`'s own "non-compiling draft" scenarios
#: do: point the source ref at a table `sources.yml` never declared, which
#: fails dbt's manifest/Jinja resolution at compile time — reliable across dbt
#: versions, unlike literal malformed SQL text (dbt compile renders Jinja and
#: resolves refs; it does not necessarily validate raw SQL syntax against the
#: warehouse the way `dbt build` would).
BROKEN_CONTENT = (
    "with customers as (\n"
    "    select * from {{ source('raw', 'does_not_exist') }}\n"
    ")\n\n"
    "select\n"
    "    cust_id as customer_id,\n"
    "    order_ts,\n"
    "    amount\n"
    "from customers\n"
)


def test_reverify_with_fork_never_re_queries_get_schema(tmp_path, monkeypatch):
    """The core KAN-650 proof: forking after get_schema replays it from the
    copied prefix — it is never re-executed, live or otherwise.
    """
    _mock_schema_read(monkeypatch)
    result, journal_path = _run_source(tmp_path, SCRIPTED_ONE_GET_SCHEMA)
    run_id = result["transcript"]["run_id"]
    assert result["outcome"] == "pr_proposed"  # sanity: the source run succeeded

    # Rearm the mock to explode if get_schema is ever called again. If the
    # fork mechanism accidentally re-executed the recorded get_schema call
    # (rather than replaying it as a journal hit), this fires and the test
    # fails loudly instead of silently passing on a live re-query.
    def _must_not_be_called(self, source):  # pragma: no cover - only if the bug exists
        raise AssertionError(
            "get_schema was re-queried during a fork-replay drive; the "
            "recorded call should have been a journal hit, never re-executed"
        )

    monkeypatch.setattr(WarehouseSchema, "describe", _must_not_be_called)

    outcome = reverify_with_fork(
        run_id,
        {MODEL: SUBSTITUTED_CONTENT},
        _ctx(sandbox=None),
        journal_path=journal_path,
    )

    assert outcome.evidence["ran"] is True
    assert outcome.evidence["get_schema_ordinal"] == 1
    assert outcome.evidence["source_run_id"] == run_id
    # No sandbox configured on the fork's own rig -> undetermined, not a
    # fabricated pass (see `_tier3_passed_from_result`'s docstring).
    assert outcome.evidence["passed"] is None
    assert outcome.fresh_result["outcome"] == "pr_proposed"
    assert "cust_id as customer_id" in outcome.fresh_result["diff"]

    # Structural proof, mirroring `fork_and_compare_demo.py`'s
    # `executed_here`/`fork_seq`: the get_schema call's TaskAttemptStarted
    # must sit at or below the RunForked marker's seq (copied history), never
    # above it (which would mean it was freshly re-executed in the fork).
    from satay.journal.events import EventType

    store = _open_store(journal_path)
    try:
        events = list(asyncio.run(store.read_events(outcome.fork_run_id)))
    finally:
        store.close()

    fork_marker_seq = next(e.seq for e in events if e.type is EventType.RUN_FORKED)
    get_schema_attempts = [
        e
        for e in events
        if e.type is EventType.TASK_ATTEMPT_STARTED
        and e.payload.get("task_name") == "_dispatch"
        and e.payload.get("ordinal") == 1
    ]
    assert get_schema_attempts, "expected the get_schema call as copied history"
    assert all(e.seq <= fork_marker_seq for e in get_schema_attempts)

    # And no NEW _dispatch call of any ordinal ran above the fork marker: the
    # override branch stops immediately, so the only new durable call in the
    # whole fork is `_verify` (skipped here since sandbox=None) — nothing else.
    new_dispatch_attempts = [
        e
        for e in events
        if e.seq > fork_marker_seq
        and e.type is EventType.TASK_ATTEMPT_STARTED
        and e.payload.get("task_name") == "_dispatch"
    ]
    assert new_dispatch_attempts == []


# --- 3. infra + docker: the fork genuinely re-verifies the SUBSTITUTED diff --

WAREHOUSE_URL = os.environ.get(
    "WAREHOUSE_URL", "postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse"
)
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


def _runner():
    from sbflow_worker.sandbox.runner import SandboxRunner

    return SandboxRunner(
        repo_root=REPO,
        warehouse_url=WAREHOUSE_URL,
        sample_url=SAMPLE_URL,
        network=NETWORK,
        work_dir=WORK_DIR,
        timeout=180,
        build_context=None,
    )


@pytest.fixture(scope="module", autouse=True)
def _image():
    from sbflow_worker.sandbox.runner import SandboxError

    try:
        _runner().ensure_image()
    except SandboxError as e:
        pytest.skip(f"sandbox image unavailable: {e}")


@pytest.mark.infra
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_reverify_with_fork_verifies_the_substituted_diff_for_real(
    tmp_path, monkeypatch
):
    """A real sandbox run: fork the same completed source twice, with two
    different substituted diffs — one that compiles, one that does not — and
    confirm tier-3's verdict tracks the SUBSTITUTED diff, not the original
    candidate's own (different) diff.
    """
    _mock_schema_read(monkeypatch)
    result, journal_path = _run_source(tmp_path, SCRIPTED_ONE_GET_SCHEMA)
    run_id = result["transcript"]["run_id"]
    assert result["outcome"] == "pr_proposed"

    passing = reverify_with_fork(
        run_id,
        {MODEL: SUBSTITUTED_CONTENT},
        _ctx(sandbox=_runner()),
        journal_path=journal_path,
    )
    assert passing.evidence["ran"] is True
    assert passing.evidence["passed"] is True
    assert passing.fresh_result["outcome"] == "pr_proposed"
    assert passing.fresh_result["evidence"]["tier1"]["passed"] is True

    broken = reverify_with_fork(
        run_id,
        {MODEL: BROKEN_CONTENT},
        _ctx(sandbox=_runner()),
        journal_path=journal_path,
    )
    assert broken.evidence["ran"] is True
    assert broken.evidence["passed"] is False
    assert broken.fresh_result["outcome"] == "no_fix"
    assert broken.fresh_result["evidence"]["tier1"]["passed"] is False

    # Both forks are independent runs off the SAME source, at the SAME
    # get_schema ordinal, verifying two DIFFERENT diffs to two DIFFERENT
    # verdicts — proof the fork actually re-verifies what was substituted.
    assert passing.fork_run_id != broken.fork_run_id
    assert (
        passing.evidence["get_schema_ordinal"]
        == broken.evidence["get_schema_ordinal"]
        == 1
    )
