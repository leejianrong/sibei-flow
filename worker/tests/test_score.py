"""N12 confidence/risk rubric (B-S4) — derived from signals, not hardcoded.

These tests assert the *mapping* from observable signals to confidence/risk, so
the label is reproducible and explainable (R5.4). They vary one signal at a time
and check the score moves the way the rubric says it should.
"""

from __future__ import annotations

from sbflow_worker.agent.score import ScoreSignals, score

# The flagship: single-file, 1-line, unambiguous rename, tier-1+tier-2 passed,
# output schema unchanged, one attempt.
FLAGSHIP = ScoreSignals(
    tier1_passed=True,
    tier2_passed=True,
    output_schema_unchanged=True,
    changed_lines=1,
    files_touched=1,
    unambiguous=True,
    attempts=1,
)


def test_flagship_is_low_risk_high_confidence():
    r = score(FLAGSHIP)
    assert r["risk_class"] == "low"
    assert r["confidence"] >= 0.9
    assert any("compiled" in f for f in r["factors"])
    assert any("sample" in f for f in r["factors"])
    assert any("output schema unchanged" in f for f in r["factors"])


def test_tier2_not_configured_is_medium_and_lower_confidence():
    sig = ScoreSignals(**{**vars(FLAGSHIP), "tier2_passed": None})
    r = score(sig)
    assert r["risk_class"] == "medium"  # not "low": sample never ran
    assert r["confidence"] < score(FLAGSHIP)["confidence"]
    assert any("not configured" in f for f in r["factors"])


def test_failed_compile_scores_below_a_pass():
    passed = score(FLAGSHIP)["confidence"]
    failed = score(ScoreSignals(**{**vars(FLAGSHIP), "tier1_passed": False}))
    assert failed["confidence"] < passed
    assert any("did not compile" in f for f in failed["factors"])


def test_large_multifile_diff_is_high_risk():
    sig = ScoreSignals(**{**vars(FLAGSHIP), "changed_lines": 120, "files_touched": 4})
    r = score(sig)
    assert r["risk_class"] == "high"


def test_ambiguous_drift_is_high_risk():
    r = score(ScoreSignals(**{**vars(FLAGSHIP), "unambiguous": False}))
    assert r["risk_class"] == "high"


def test_more_attempts_lowers_confidence_monotonically():
    one = score(FLAGSHIP)["confidence"]
    three = score(ScoreSignals(**{**vars(FLAGSHIP), "attempts": 3}))["confidence"]
    assert three < one
    assert any(
        "attempts" in f
        for f in score(ScoreSignals(**{**vars(FLAGSHIP), "attempts": 3}))["factors"]
    )


def test_reproducible():
    assert score(FLAGSHIP) == score(FLAGSHIP)
