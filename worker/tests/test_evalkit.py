"""SIB-81 — eval harness: case format, scoring and report maths (no infra)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sbflow_worker.evalkit import (
    CaseError,
    CaseResult,
    discover,
    load_case,
    render_text,
    score_repair,
    summarize,
)

REPO = Path(__file__).resolve().parents[2]
BUNDLED = REPO / "evals" / "cases"

FAILURE = {
    "repo": "acme/analytics",
    "run_id": "r1",
    "task_id": "t1",
    "node_uid": "model.analytics.orders",
    "error_text": 'column "x" does not exist',
    "adapter": "postgres",
    "source": "airflow",
}


def _write_case(root: Path, cid: str, **overrides) -> Path:
    d = root / cid
    d.mkdir(parents=True)
    (d / "project").mkdir()
    spec = {
        "id": cid,
        "description": "d",
        "failure": FAILURE,
        "expect": {"outcome": "pr_proposed", "diff_contains": ["fix"]},
    }
    spec.update(overrides)
    (d / "case.json").write_text(json.dumps(spec))
    return d


# --- the bundled corpus must always be loadable -----------------------------


def test_bundled_cases_are_valid():
    cases = discover(BUNDLED)
    ids = {c.id for c in cases}
    assert {"rename_customer_id", "control_statement_timeout"} <= ids
    flagship = next(c for c in cases if c.id == "rename_customer_id")
    assert flagship.dispatched and flagship.project_dir is not None
    assert flagship.replay is not None and flagship.warehouse_sql is not None
    control = next(c for c in cases if c.id == "control_statement_timeout")
    assert not control.dispatched and control.project_dir is None


# --- case validation ---------------------------------------------------------


def test_valid_case_loads(tmp_path):
    case = load_case(_write_case(tmp_path, "ok"))
    assert case.id == "ok"
    assert case.expect_outcome == "pr_proposed"
    assert case.expect_diff_contains == ("fix",)
    assert case.project_dir == (tmp_path / "ok" / "project").resolve()


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"id": "other"}, "must match the directory name"),
        ({"failure": {**FAILURE, "error_text": ""}}, "failure is missing"),
        ({"expect": {"outcome": "great"}}, "expect.outcome"),
        (
            {"expect": {"outcome": "no_fix", "diff_contains": ["x"]}},
            "diff_contains only",
        ),
        ({"project": "nope"}, "project directory not found"),
        ({"replay": "nope.json"}, "replay file not found"),
    ],
)
def test_invalid_cases_are_rejected(tmp_path, overrides, message):
    d = _write_case(tmp_path, "bad", **overrides)
    with pytest.raises(CaseError, match=message):
        load_case(d)


def test_missing_case_json_and_bad_json(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(CaseError, match="missing case.json"):
        load_case(tmp_path / "empty")
    d = tmp_path / "broken"
    d.mkdir()
    (d / "case.json").write_text("{not json")
    with pytest.raises(CaseError, match="invalid JSON"):
        load_case(d)


def test_out_of_scope_case_needs_no_project(tmp_path):
    d = tmp_path / "ctl"
    d.mkdir()
    (d / "case.json").write_text(
        json.dumps(
            {"id": "ctl", "failure": FAILURE, "expect": {"outcome": "out_of_scope"}}
        )
    )
    case = load_case(d)
    assert not case.dispatched and case.project_dir is None


def test_discover_filters_and_rejects_unknown_ids(tmp_path):
    _write_case(tmp_path, "a")
    _write_case(tmp_path, "b")
    assert [c.id for c in discover(tmp_path)] == ["a", "b"]
    assert [c.id for c in discover(tmp_path, {"b"})] == ["b"]
    with pytest.raises(CaseError, match="unknown case id"):
        discover(tmp_path, {"zzz"})


# --- scoring -----------------------------------------------------------------


def _case(tmp_path, **expect):
    spec = {"outcome": "pr_proposed", **expect}
    return load_case(_write_case(tmp_path, "c", expect=spec))


def test_score_correct_fix_passes(tmp_path):
    case = _case(tmp_path, diff_contains=["cust_id"])
    res = score_repair(
        case,
        {
            "outcome": "pr_proposed",
            "diff": "- customer_id\n+ cust_id as customer_id",
            "confidence": 0.9,
            "risk_class": "low",
            "evidence": {"tier1": {"passed": True}},
        },
        1.234,
    )
    assert res.passed and res.fix_correct is True
    assert res.tier1_passed is True and res.confidence == 0.9


def test_score_wrong_but_compiling_fix_fails(tmp_path):
    case = _case(tmp_path, diff_contains=["cust_id"])
    res = score_repair(case, {"outcome": "pr_proposed", "diff": "+ select 1"}, 1.0)
    assert not res.passed and res.fix_correct is False


def test_score_wrong_outcome_fails(tmp_path):
    case = _case(tmp_path)
    res = score_repair(case, {"outcome": "no_fix"}, 1.0)
    assert not res.passed and res.actual == "no_fix"


def test_fix_without_diff_check_is_left_for_manual_review(tmp_path):
    case = _case(tmp_path)  # no diff_contains
    res = score_repair(case, {"outcome": "pr_proposed", "diff": "+x"}, 1.0)
    assert res.passed and res.fix_correct is None


# --- summary maths -----------------------------------------------------------


def _r(cid, expected, actual, passed, elapsed=1.0, fix_correct=None, conf=None):
    return CaseResult(
        case_id=cid,
        expected=expected,
        actual=actual,
        passed=passed,
        elapsed_s=elapsed,
        fix_correct=fix_correct,
        confidence=conf,
    )


def test_summary_rates_and_exclusions():
    results = [
        _r("fix_ok", "pr_proposed", "pr_proposed", True, 2.0, True, 0.9),
        _r("fix_missed", "pr_proposed", "no_fix", False, 4.0),
        _r("falsefix", "no_fix", "pr_proposed", False, 6.0, None, 0.8),
        _r("correct_nofix", "needs_prod_action", "needs_prod_action", True, 8.0),
        _r("control", "out_of_scope", "out_of_scope", True, 0.1),
        _r("boom", "pr_proposed", "error", False),
        _r("skip", "out_of_scope", "skipped", False),
    ]
    s = summarize(results, {"provider": "replay"})
    assert s["cases"] == 7 and s["ran"] == 5 and s["errors"] == 1 and s["skipped"] == 1
    # fix rate: 1 of 2 cases that ran and expected a fix (the error is excluded).
    assert s["fix_rate"] == 0.5
    # false-fix rate: 1 of 3 cases that ran and expected no fix.
    assert s["false_fix_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["pass_rate"] == 0.6
    # latency excludes brain-only out_of_scope controls: median of 2, 4, 6, 8.
    assert s["latency_p50_s"] == 5.0
    assert s["needs_manual_review"] == 1  # the false fix has no diff check
    assert s["mean_confidence"] == 0.85
    assert s["by_actual_outcome"]["error"] == 1


def test_summary_with_no_denominators_is_none_not_a_crash():
    s = summarize([_r("c", "out_of_scope", "out_of_scope", True)])
    assert s["fix_rate"] is None and s["latency_p50_s"] is None


def test_render_text_mentions_every_case_and_headline_numbers():
    s = summarize([_r("fix_ok", "pr_proposed", "pr_proposed", True, 2.0, True, 0.9)])
    text = render_text(s)
    assert "fix_ok" in text and "fix rate" in text and "false-fix rate" in text
