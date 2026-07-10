"""N12 confidence / risk scorer (B-S4, R5.4).

An **explainable** rubric over signals the pipeline already produces — never a
model self-report. The reviewer sees *why*:

Signals: tiers passed (compile vs compile+sample), output-schema-unchanged,
diff size (lines / files touched), drift ambiguity, attempts used.

    confidence  0..1   weighted sum of the signals, normalized
    risk_class  low | medium | high   (B-S4 mapping)
    factors     [str]  the contributing +/- reasons, shown to the reviewer

The scorer is pure and deterministic: same signals → same score (asserted in
tests, not hardcoded).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScoreSignals:
    tier1_passed: bool
    #: True/False if tier-2 ran; None when no dev/sample connection configured.
    tier2_passed: bool | None
    #: True/False, or None when it could not be determined.
    output_schema_unchanged: bool | None
    changed_lines: int
    files_touched: int
    #: A single clean 1:1 rename is unambiguous; multi-candidate is not.
    unambiguous: bool
    attempts: int


# Weights sum to 1.0 across the positive signals; explainability is the point.
_W_TIER1 = 0.30
_W_TIER2 = 0.25
_W_SCHEMA = 0.20
_W_DIFF = 0.15
_W_UNAMBIG = 0.10


def score(sig: ScoreSignals) -> dict[str, Any]:
    factors: list[str] = []
    conf = 0.0

    if sig.tier1_passed:
        conf += _W_TIER1
        factors.append("+ compiled")
    else:
        factors.append("− did not compile")

    if sig.tier2_passed is True:
        conf += _W_TIER2
        factors.append("+ ran on sample")
    elif sig.tier2_passed is None:
        # Not configured: give partial credit and disclose, don't penalize fully.
        conf += _W_TIER2 * 0.4
        factors.append("~ sample run not configured")
    else:
        factors.append("− sample run failed")

    if sig.output_schema_unchanged is True:
        conf += _W_SCHEMA
        factors.append("+ output schema unchanged")
    elif sig.output_schema_unchanged is None:
        factors.append("~ output schema undetermined")
    else:
        factors.append("− output schema changed")

    small = sig.changed_lines <= 15 and sig.files_touched <= 1
    if small:
        conf += _W_DIFF
        factors.append(f"+ small diff ({sig.changed_lines} lines, {sig.files_touched} file)")
    else:
        factors.append(f"− large/multi-file diff ({sig.changed_lines} lines, {sig.files_touched} files)")

    if sig.unambiguous:
        conf += _W_UNAMBIG
        factors.append("+ unambiguous drift")
    else:
        factors.append("− ambiguous drift")

    if sig.attempts > 1:
        conf -= 0.08 * (sig.attempts - 1)
        factors.append(f"− {sig.attempts} attempts")

    confidence = round(max(0.0, min(1.0, conf)), 2)
    return {
        "confidence": confidence,
        "risk_class": _risk_class(sig),
        "factors": factors,
    }


def _risk_class(sig: ScoreSignals) -> str:
    small = sig.changed_lines <= 15 and sig.files_touched <= 1
    # high: multi-file/large diff, ambiguous drift, or tier-2 unavailable AND
    # output schema changed.
    if (
        not small
        or not sig.unambiguous
        or (sig.tier2_passed is None and sig.output_schema_unchanged is False)
    ):
        return "high"
    # low: single-file, small, unambiguous, tier-1 AND tier-2 passed, output
    # schema unchanged, one attempt.
    if (
        sig.tier1_passed
        and sig.tier2_passed is True
        and sig.output_schema_unchanged is True
        and sig.attempts == 1
    ):
        return "low"
    # medium: compiles + (tier-2 passed or not configured), small diff, minor
    # ambiguity.
    if sig.tier1_passed and sig.tier2_passed is not False:
        return "medium"
    return "high"
