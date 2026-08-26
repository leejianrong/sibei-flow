"""N12 confidence/risk rubric (B-S4) — derived from signals, not hardcoded.

These tests assert the *mapping* from observable signals to confidence/risk, so
the label is reproducible and explainable (R5.4). They vary one signal at a time
and check the score moves the way the rubric says it should.

KAN-650 adds `tier3_passed` (fork-replay against a recorded schema snapshot,
`agent/satay_loop.reverify_with_fork`) as a sixth signal, re-deriving the
weights — see `score.py`'s module docstring for the derivation. `FLAGSHIP`
below sets `tier3_passed=None` ("not attempted"), which is the realistic
default for almost every RepairResult today (tier-3 is opt-in, not wired into
the automatic claim loop), so the flagship's confidence sits below the old
rubric's ceiling — a disclosed, intentional consequence, not a regression.
"""

from __future__ import annotations

from sbflow_worker.agent.score import ScoreSignals, score

# The flagship: single-file, 1-line, unambiguous rename, tier-1+tier-2 passed,
# output schema unchanged, one attempt, fork-replay never attempted.
FLAGSHIP = ScoreSignals(
    tier1_passed=True,
    tier2_passed=True,
    tier3_passed=None,
    output_schema_unchanged=True,
    changed_lines=1,
    files_touched=1,
    unambiguous=True,
    attempts=1,
)


def test_flagship_is_low_risk_high_confidence():
    r = score(FLAGSHIP)
    assert r["risk_class"] == "low"
    # Ceiling without ever running tier-3 is 0.85, not 1.00 (see score.py's
    # "KAN-650 rubric re-derivation" docstring) — still comfortably "high
    # confidence" for a rubric that now reserves a perfect score for a fix
    # proven against its own recorded historical conditions.
    assert r["confidence"] >= 0.8
    assert any("compiled" in f for f in r["factors"])
    assert any("sample" in f for f in r["factors"])
    assert any("output schema unchanged" in f for f in r["factors"])
    assert any("fork-replay not run" in f for f in r["factors"])


def test_passing_fork_replay_reaches_full_confidence():
    sig = ScoreSignals(**{**vars(FLAGSHIP), "tier3_passed": True})
    r = score(sig)
    assert r["risk_class"] == "low"
    assert r["confidence"] == 1.0
    assert any("recorded schema snapshot" in f for f in r["factors"])
    assert r["confidence"] > score(FLAGSHIP)["confidence"]


def test_failed_fork_replay_forces_high_risk_and_lowers_confidence():
    sig = ScoreSignals(**{**vars(FLAGSHIP), "tier3_passed": False})
    r = score(sig)
    # A fork-replay that fails against the exact recorded conditions is a red
    # flag on its own — forces high risk even though tier1/tier2/schema/diff
    # all still look clean.
    assert r["risk_class"] == "high"
    assert r["confidence"] < score(FLAGSHIP)["confidence"]
    assert any("fork-replay" in f and "failed" in f for f in r["factors"])


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
