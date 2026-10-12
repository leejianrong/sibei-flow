"""Scoring and reporting for eval runs (SIB-81). Pure logic, no infra."""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from .case import EvalCase


@dataclass
class CaseResult:
    case_id: str
    expected: str
    #: What actually happened: a RepairResult outcome, ``out_of_scope`` (brain
    #: dropped it), ``error`` (the run itself blew up) or ``skipped``.
    actual: str
    #: ``actual == expected`` (for ``pr_proposed``, plus every ``diff_contains``).
    passed: bool
    elapsed_s: float = 0.0
    #: True/False when the case lists ``diff_contains``; None when it does not.
    fix_correct: bool | None = None
    confidence: float | None = None
    risk_class: str | None = None
    tier1_passed: bool | None = None
    #: The proposed diff, kept so a human can review wrong-but-compiling fixes.
    diff: str | None = None
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def score_repair(
    case: EvalCase, result: dict[str, Any], elapsed_s: float
) -> CaseResult:
    """Score a worker ``RepairResult`` against a dispatched case's expectation."""
    actual = str(result.get("outcome") or "unknown")
    diff = result.get("diff")
    evidence = result.get("evidence") or {}
    tier1 = evidence.get("tier1") or {}

    fix_correct: bool | None = None
    if case.expect_diff_contains:
        fix_correct = actual == "pr_proposed" and all(
            s in (diff or "") for s in case.expect_diff_contains
        )

    passed = actual == case.expect_outcome and fix_correct is not False
    return CaseResult(
        case_id=case.id,
        expected=case.expect_outcome,
        actual=actual,
        passed=passed,
        elapsed_s=round(elapsed_s, 3),
        fix_correct=fix_correct,
        confidence=result.get("confidence"),
        risk_class=result.get("risk_class"),
        tier1_passed=tier1.get("passed") if tier1 else None,
        diff=diff,
        detail=str(result.get("explanation") or "")[:300],
    )


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def summarize(
    results: list[CaseResult], meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Aggregate per-case results into the report's headline numbers.

    ``fix_rate``: of cases that expect a fix, the share that passed (right
    outcome and, where listed, the right diff). ``false_fix_rate``: of cases
    that expect NO fix (``no_fix`` / ``needs_prod_action`` / ``out_of_scope``),
    the share where a PR was proposed anyway. Errors and skips are counted and
    kept out of both denominators so they cannot flatter the rates.
    """
    ran = [r for r in results if r.actual not in ("error", "skipped")]
    by_actual: dict[str, int] = {}
    for r in results:
        by_actual[r.actual] = by_actual.get(r.actual, 0) + 1

    expect_fix = [r for r in ran if r.expected == "pr_proposed"]
    expect_none = [r for r in ran if r.expected != "pr_proposed"]
    latencies = [r.elapsed_s for r in ran if r.expected != "out_of_scope"]
    confidences = [r.confidence for r in ran if r.confidence is not None]

    return {
        "meta": meta or {},
        "cases": len(results),
        "ran": len(ran),
        "errors": by_actual.get("error", 0),
        "skipped": by_actual.get("skipped", 0),
        "passed": sum(1 for r in ran if r.passed),
        "pass_rate": _rate(sum(1 for r in ran if r.passed), len(ran)),
        "by_actual_outcome": by_actual,
        "fix_rate": _rate(sum(1 for r in expect_fix if r.passed), len(expect_fix)),
        "false_fix_rate": _rate(
            sum(1 for r in expect_none if r.actual == "pr_proposed"), len(expect_none)
        ),
        # Compiled-but-unverified-by-a-human: a PR was proposed and the case has no
        # `diff_contains` to check it, so someone has to read the diff.
        "needs_manual_review": sum(
            1 for r in ran if r.actual == "pr_proposed" and r.fix_correct is None
        ),
        "latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_max_s": max(latencies) if latencies else None,
        "mean_confidence": round(statistics.mean(confidences), 3)
        if confidences
        else None,
        "results": [r.to_json() for r in results],
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def render_text(summary: dict[str, Any]) -> str:
    """A one-screen summary for the terminal."""
    meta = summary.get("meta", {})
    lines = [
        "sibei-flow eval — "
        + ", ".join(f"{k}={v}" for k, v in meta.items() if v not in (None, "")),
        "",
        f"  cases        {summary['cases']}  (ran {summary['ran']}, "
        f"errors {summary['errors']}, skipped {summary['skipped']})",
        f"  pass rate    {_pct(summary['pass_rate'])}  ({summary['passed']}/{summary['ran']})",
        f"  fix rate     {_pct(summary['fix_rate'])}   "
        f"false-fix rate {_pct(summary['false_fix_rate'])}",
        f"  outcomes     {summary['by_actual_outcome']}",
        f"  manual review needed: {summary['needs_manual_review']} proposed diff(s) with no diff_contains check",
        f"  latency      p50 {summary['latency_p50_s']}s  max {summary['latency_max_s']}s"
        f"   mean confidence {summary['mean_confidence']}",
        "",
    ]
    for r in summary["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        lines.append(
            f"  [{mark}] {r['case_id']:<32} expected {r['expected']:<18} got {r['actual']:<18}"
            f" {r['elapsed_s']:>6.1f}s"
        )
        if not r["passed"] and r["detail"]:
            lines.append(f"         {r['detail']}")
    return "\n".join(lines)
