"""KAN-648 (ADR-0012 decision 4, slice 2) — N-candidate collect-mode fan-out.

`run_repair_satay_candidates` (`sbflow_worker.agent.satay_loop`) must:

1. Degenerate to `run_repair_satay` (slice 1), byte-for-byte, when `n_candidates<=1` —
   the regression guard for "N=1 is unchanged".
2. Among candidates that reach `pr_proposed`/settle with a real confidence, pick the
   highest-confidence one (collect mode: every candidate settles independently).
3. Survive a candidate that raises (a real exception, not a caught `no_fix`) — the
   run still produces a result from whichever candidates settled.
4. Degrade sensibly when every candidate raises.

The exception scenarios (2/4 above, minus the confidence comparison) need no external
infrastructure — a stub `LlmProvider` that raises is enough — so they run under
`make test-fast`. The confidence-ranking scenario needs the real sandbox (Docker) to
produce a non-null, differentiated `confidence`, so it is marked `infra` and reuses
`test_satay_loop.py`'s fixtures.

KAN-649: every `RepairResult` this module produces now carries a `{"kind":"journal",
...}` transcript pointing at a *persisted* journal (one file per test, via
`tmp_path`), not the old hand-built `{"kind":"lines",...}`. Two independent calls with
identical scripted inputs still get two different `run_id`s (each call mints a fresh
run against the journal), so "byte-for-byte" comparisons below are asserted on
everything BUT `transcript` — see `_pop_transcript`/`_assert_journal_transcript`,
shared with `test_satay_loop.py`'s pattern. The N>1 tests additionally assert *which*
run a winning transcript names: the winning candidate's own child run, never the
parent fan-out workflow's run_id and never a losing candidate's — see
`test_multi_candidate_transcript_names_the_winning_candidates_own_run`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy
from sbflow_worker.agent.satay_loop import run_repair_satay, run_repair_satay_candidates
from sbflow_worker.agent.source import LocalSourceProvider
from sbflow_worker.agent.tools import AgentContext
from sbflow_worker.llm.base import AssistantTurn, LlmProvider, ToolSpec
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


def _keyed_factory(providers: list[LlmProvider]) -> Callable[[], LlmProvider]:
    """A `provider_factory` that hands out `providers` one at a time, in call order.

    `run_repair_satay_candidates` calls the factory once per candidate key, in
    `candidate_keys` order (`["c0", "c1", ...]`), so this deterministically pairs
    `providers[i]` with candidate `i`.
    """
    it = iter(providers)
    return lambda: next(it)


class _RaisingProvider(LlmProvider):
    """A provider whose first `complete()` call raises — a genuine candidate
    exception (as opposed to a caught, scored `no_fix`)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    def complete(self, system, messages, tools: list[ToolSpec]) -> AssistantTurn:
        raise self._exc


def _pop_transcript(result: dict) -> tuple[dict, dict | None]:
    """Split `result` into (everything else, its `transcript`), popping the latter —
    see the module docstring for why transcript equality is never asserted directly.
    """
    result = dict(result)
    return result, result.pop("transcript", None)


def _assert_journal_transcript(transcript: dict | None, *, journal_path: Path) -> str:
    """Assert `transcript` is a well-formed `journal` arm resolvable against
    `journal_path`, and return its `run_id`. Mirrors `test_satay_loop.py`'s helper
    of the same name.
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


# --- no-infra scenarios (no warehouse/Docker needed) --------------------------------


def test_n1_degenerates_to_run_repair_satay(tmp_path):
    """`n_candidates=1` must produce the exact same result as calling
    `run_repair_satay` directly — the regression guard for "N=1 unchanged" —
    modulo `transcript.run_id` (KAN-649): each call mints its own fresh run, even
    against the same journal file, so the two `run_id`s legitimately differ while
    everything else (including the transcript's `kind`/`ref`) must match exactly.
    """
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

    journal_path = tmp_path / "satay.db"
    direct = run_repair_satay(
        ReplayProvider(list(scripted)),
        _ctx(),
        _task(),
        max_turns=6,
        journal_path=journal_path,
    )
    via_candidates = run_repair_satay_candidates(
        lambda: ReplayProvider(list(scripted)),
        _ctx(),
        _task(),
        max_turns=6,
        n_candidates=1,
        journal_path=journal_path,
    )

    direct_rest, direct_transcript = _pop_transcript(direct)
    via_rest, via_transcript = _pop_transcript(via_candidates)
    assert via_rest == direct_rest
    assert direct_transcript is not None and via_transcript is not None
    assert direct_transcript["kind"] == via_transcript["kind"] == "journal"
    assert direct_transcript["ref"] == via_transcript["ref"] == journal_path.name
    assert direct_transcript["run_id"] != via_transcript["run_id"]
    _assert_journal_transcript(direct_transcript, journal_path=journal_path)
    _assert_journal_transcript(via_transcript, journal_path=journal_path)


def test_n_candidates_less_than_one_is_rejected():
    with pytest.raises(ValueError):
        run_repair_satay_candidates(
            lambda: ReplayProvider([]), _ctx(), _task(), max_turns=6, n_candidates=0
        )


_GOOD_SCRIPTED = [
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


def test_multi_candidate_survives_a_sibling_exception(tmp_path):
    """Collect mode: one candidate's provider raises outright; the other candidate's
    real, individually-computed result still wins — the exception never sinks the run.
    """
    expected = run_repair_satay(
        ReplayProvider(list(_GOOD_SCRIPTED)),
        _ctx(),
        _task(),
        max_turns=6,
        journal_path=tmp_path / "expected.db",
    )
    assert expected["outcome"] == "pr_proposed"  # sanity: this candidate really wins

    factory = _keyed_factory(
        [
            ReplayProvider(list(_GOOD_SCRIPTED)),
            _RaisingProvider(RuntimeError("simulated model outage")),
        ]
    )
    journal_path = tmp_path / "satay.db"
    result = run_repair_satay_candidates(
        factory,
        _ctx(),
        _task(),
        max_turns=6,
        n_candidates=2,
        journal_path=journal_path,
    )

    expected_rest, _ = _pop_transcript(expected)
    result_rest, result_transcript = _pop_transcript(result)
    assert result_rest == expected_rest
    _assert_journal_transcript(result_transcript, journal_path=journal_path)


def test_multi_candidate_transcript_names_the_winning_candidates_own_run(tmp_path):
    """The shipped transcript's `run_id` is the WINNING candidate's own child run —
    never the parent `_multi_candidate_workflow`'s run_id, never the losing (raised)
    candidate's — so a reviewer clicking through lands on the run that actually
    produced the diff (KAN-649 card's explicit ask).
    """
    factory = _keyed_factory(
        [
            ReplayProvider(list(_GOOD_SCRIPTED)),
            _RaisingProvider(RuntimeError("simulated model outage")),
        ]
    )
    journal_path = tmp_path / "satay.db"
    result = run_repair_satay_candidates(
        factory,
        _ctx(),
        _task(),
        max_turns=6,
        n_candidates=2,
        journal_path=journal_path,
    )
    assert result["outcome"] == "pr_proposed"
    winner_run_id = result["transcript"]["run_id"]

    from satay.journal.events import RunStatus
    from satay.journal.store import SQLiteStore

    async def _inspect() -> tuple[str, dict[str, str]]:
        store = SQLiteStore.open(journal_path)
        try:
            run_ids = list(await store.list_runs())
            statuses = {
                run_id: (await store.get_run(run_id)).status.value for run_id in run_ids
            }
            # `list_runs` orders oldest-first (created_at): the parent
            # (`_multi_candidate_workflow`) is created before either child is
            # scheduled, so it is always the first row.
            return run_ids[0], statuses
        finally:
            store.close()

    parent_run_id, statuses = asyncio.run(_inspect())

    assert winner_run_id != parent_run_id, (
        "transcript must name a candidate's own run, not the parent fan-out "
        "workflow's run_id"
    )
    assert statuses[winner_run_id] == RunStatus.COMPLETED.value
    # The losing (raised) candidate's own child run is also in this same journal
    # file (collect mode records it durably rather than discarding it) — the
    # winner must not be confused with it either.
    losers = {
        run_id
        for run_id, status in statuses.items()
        if run_id not in (parent_run_id, winner_run_id)
    }
    assert losers, "expected the raised candidate's own child run in the journal too"
    assert statuses[next(iter(losers))] == RunStatus.FAILED.value


def test_multi_candidate_all_raise_degrades_to_no_fix(tmp_path):
    """Every candidate raises: no drafted diff exists anywhere, so the run degrades
    to a `no_fix` naming the first failure — never a crash, never a fabricated
    `pr_proposed`. The transcript still points at a real run (the first-failing
    candidate's own, per `_judge`'s docstring) rather than disappearing."""
    factory = _keyed_factory(
        [
            _RaisingProvider(RuntimeError("outage A")),
            _RaisingProvider(RuntimeError("outage B")),
            _RaisingProvider(RuntimeError("outage C")),
        ]
    )
    journal_path = tmp_path / "satay.db"
    result = run_repair_satay_candidates(
        factory,
        _ctx(),
        _task(),
        max_turns=6,
        n_candidates=3,
        journal_path=journal_path,
    )
    assert result["outcome"] == "no_fix"
    assert "diff" not in result or result.get("diff") is None
    assert "3 candidate(s) failed" in result["explanation"]
    _assert_journal_transcript(result["transcript"], journal_path=journal_path)


# --- infra + docker: real sandbox, differentiated confidence ------------------------

WAREHOUSE_URL = os.environ.get(
    "WAREHOUSE_URL", "postgres://sbflow_ro:sbflow_ro@warehouse:5432/warehouse"
)
SAMPLE_URL = os.environ.get(
    "SAMPLE_WAREHOUSE_URL", "postgres://sbflow_dev:sbflow_dev@warehouse:5432/warehouse"
)
NETWORK = os.environ.get("SANDBOX_NETWORK", "sibei-flow_default")
WORK_DIR = os.environ.get("SANDBOX_WORK_DIR", "/tmp/sbflow-sandbox")
RENAME_SESSION = str(
    Path(__file__).resolve().parents[1]
    / "sbflow_worker"
    / "replays"
    / "rename_drift.json"
)


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


def _sandboxed_ctx():
    return _ctx(
        warehouse=WAREHOUSE_URL,
        sandbox=_runner(SAMPLE_URL),
        model_select="orders",
    )


def _rename_turns() -> list[dict[str, Any]]:
    import json

    return json.loads(Path(RENAME_SESSION).read_text())["turns"]


#: A harmless edit_file call the diff guard always rejects (out-of-scope path, per
#: `_ctx()`'s default `allowed={MODEL}`) — reverted immediately, touches no state,
#: but still increments `edit_attempts`. Prepending it to an otherwise-identical
#: script isolates the scorer's `attempts` penalty (`score.py`) as the ONLY thing
#: that can differ between two candidates that end up drafting the same final diff
#: and hitting the same tier-1/tier-2 outcome — so which one wins is deterministic
#: regardless of whether tier-1 actually passes in this environment.
_REJECTED_EDIT_TURN = {
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
}


@pytest.mark.infra
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_multi_candidate_picks_highest_confidence_among_survivors():
    """Two candidates draft the identical fix and hit the identical tier-1/tier-2
    verdict; the only difference is `edit_attempts` (1 vs 2). `score.py` strictly
    penalizes extra attempts, so candidate 0 always outscores candidate 1 — whether
    both land on `pr_proposed` or both are suppressed to `no_fix` by tier-1 in this
    environment, the *judged winner* must always be candidate 0's own result.
    """
    clean_turns = _rename_turns()
    attempts_turns = [_REJECTED_EDIT_TURN, *clean_turns]

    expected_winner = run_repair_satay(
        ReplayProvider(list(clean_turns)), _sandboxed_ctx(), _task(), max_turns=8
    )
    loser = run_repair_satay(
        ReplayProvider(list(attempts_turns)), _sandboxed_ctx(), _task(), max_turns=8
    )

    # Both candidates drafted a diff and reached the scorer (never the "no diff
    # drafted" branch, which carries no confidence) — otherwise this test would not
    # be isolating the attempts penalty at all.
    assert expected_winner["outcome"] in ("pr_proposed", "no_fix")
    assert expected_winner.get("confidence") is not None
    assert loser.get("confidence") is not None
    assert expected_winner["confidence"] >= loser["confidence"]

    factory = _keyed_factory(
        [
            ReplayProvider(list(clean_turns)),
            ReplayProvider(list(attempts_turns)),
        ]
    )
    result = run_repair_satay_candidates(
        factory, _sandboxed_ctx(), _task(), max_turns=8, n_candidates=2
    )

    def _normalized(r: dict[str, Any]) -> dict[str, Any]:
        r = dict(r)
        evidence = r.get("evidence")
        if evidence:
            evidence = {
                k: (dict(v) if isinstance(v, dict) else v) for k, v in evidence.items()
            }
            for tier in ("tier1", "tier2"):
                if tier in evidence and evidence[tier].get("log"):
                    evidence[tier] = {**evidence[tier], "log": "<normalized>"}
            r["evidence"] = evidence
        return r

    assert _normalized(result) == _normalized(expected_winner)


@pytest.mark.infra
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not available")
def test_multi_candidate_survives_a_tier1_compile_failure():
    """One candidate's draft points at a table that does not exist (tier-1 fails,
    deterministically, regardless of environment); collect mode must not let that
    sink the run — the other candidate still settles and the run still produces one
    real result (not the synthetic "all candidates failed" fallback).
    """
    broken_turns = [
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
    broken = run_repair_satay(
        ReplayProvider(list(broken_turns)), _sandboxed_ctx(), _task(), max_turns=6
    )
    assert broken["outcome"] == "no_fix"
    assert broken["evidence"]["tier1"]["passed"] is False

    factory = _keyed_factory(
        [
            ReplayProvider(list(_rename_turns())),
            ReplayProvider(list(broken_turns)),
        ]
    )
    result = run_repair_satay_candidates(
        factory, _sandboxed_ctx(), _task(), max_turns=8, n_candidates=2
    )

    # A real, individually-attributable candidate result — never the synthetic
    # "every candidate raised" fallback (which only fires with zero settled
    # results, and this run always has at least the broken candidate settled).
    assert result["outcome"] in ("pr_proposed", "no_fix")
    assert not result["explanation"].startswith("All 2 candidate(s) failed to run")
