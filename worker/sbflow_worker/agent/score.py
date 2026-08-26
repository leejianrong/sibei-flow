"""N12 confidence / risk scorer (B-S4, R5.4).

An **explainable** rubric over signals the pipeline already produces — never a
model self-report. The reviewer sees *why*:

Signals: tiers passed (compile vs compile+sample vs fork-replay against a
recorded schema snapshot), output-schema-unchanged, diff size (lines / files
touched), drift ambiguity, attempts used.

    confidence  0..1   weighted sum of the signals, normalized
    risk_class  low | medium | high   (B-S4 mapping)
    factors     [str]  the contributing +/- reasons, shown to the reviewer

The scorer is pure and deterministic: same signals → same score (asserted in
tests, not hardcoded).

**KAN-650 rubric re-derivation.** ADR-0012's Consequences section names the
payoff directly: "Tier-1 `dbt compile` is weighted 0.30 precisely because it is
a weak signal; replay-against-real-inputs is a strong one." Tier-3 (the
fork-replay signal — `sandbox/evidence.py`'s `tier3` slot, `agent/satay_loop.py`'s
`reverify_with_fork`) is that replay: it re-verifies a candidate diff against the
*exact recorded* `get_schema` snapshot a completed run consulted, rather than
against whatever the warehouse looks like right now. That is strictly more
trustworthy evidence than tier-1 (compiles against today's live fixture) or
tier-2 (builds against today's live sample) — both of which can pass even
though the warehouse has drifted *further* since the fix was diagnosed.

The instruction is to re-derive the rubric, not bolt on a small extra term: the
five original weights (which summed to 1.0) are scaled by 0.75 to make room for
tier-3 at the top of the table, then rounded to two decimals with the residual
folded into tier-1 (the signal tier-3 most directly supersedes) so the six
weights still sum to exactly 1.0:

    old            → new (× 0.75, rounded)      tier-3 (new)
    tier1  0.30    → 0.22 (0.225, rounded down)  0.25
    tier2  0.25    → 0.19 (0.1875, rounded down)
    schema 0.20    → 0.15 (0.15)
    diff   0.15    → 0.11 (0.1125, rounded down)
    unambig 0.10   → 0.08 (0.075, rounded up)

0.25 + 0.22 + 0.19 + 0.15 + 0.11 + 0.08 = 1.00.

**A disclosed consequence, not an accident.** Tier-3 is opt-in (see
`satay_loop.py`'s module docstring for the invocation-scope decision — it is
not wired into the automatic single-pass claim loop by this card), so
`tier3_passed` is `None` — "not run" — for the overwhelming majority of repairs
today, exactly as `tier2_passed` is `None` for a deployment with no dev/sample
connection. It gets the same disclosed partial credit tier2 uses for its own
"not configured" case (`_W_TIER3 * 0.4`), so a repair that never attempts
fork-replay scores `0.25 * 0.4 = 0.10` for this signal instead of the `0.25` a
verified fork-replay would earn. **Consequence, stated plainly:** the same
tier1+tier2+schema+diff+unambiguous evidence that scored a perfect `1.00`
before this change now caps at `0.85` (the six shrunk weights minus tier-3's
sum to `0.75`, plus `0.10` partial credit for "not run") until a fork-replay
is actually run and passes — the ceiling for "fully confident" now requires
proving the fix under the recorded historical conditions, not merely under
today's. That is the intended shape of the change (a strong new signal makes
"as confident as it gets" a higher bar), not a scoring regression to paper over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ScoreSignals:
    tier1_passed: bool
    #: True/False if tier-2 ran; None when no dev/sample connection configured.
    tier2_passed: bool | None
    #: KAN-650 — True/False if a fork-replay re-verification
    #: (`satay_loop.reverify_with_fork`) actually ran for this diff; None when
    #: none was attempted (today's default — see the module docstring).
    tier3_passed: bool | None
    #: True/False, or None when it could not be determined.
    output_schema_unchanged: bool | None
    changed_lines: int
    files_touched: int
    #: A single clean 1:1 rename is unambiguous; multi-candidate is not.
    unambiguous: bool
    attempts: int


# Weights sum to 1.0 across the positive signals; explainability is the point.
# See the module docstring ("KAN-650 rubric re-derivation") for how these six
# numbers were derived from the pre-KAN-650 five-weight rubric.
_W_TIER3 = 0.25
_W_TIER1 = 0.22
_W_TIER2 = 0.19
_W_SCHEMA = 0.15
_W_DIFF = 0.11
_W_UNAMBIG = 0.08


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

    if sig.tier3_passed is True:
        conf += _W_TIER3
        factors.append("+ verified against recorded schema snapshot (fork-replay)")
    elif sig.tier3_passed is None:
        # Not attempted (today's default — tier-3 is opt-in, see module
        # docstring): partial credit, disclosed, mirroring tier-2's "not
        # configured" treatment above.
        conf += _W_TIER3 * 0.4
        factors.append("~ fork-replay not run")
    else:
        factors.append("− fork-replay against recorded schema snapshot failed")

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
        factors.append(
            f"+ small diff ({sig.changed_lines} lines, {sig.files_touched} file)"
        )
    else:
        factors.append(
            f"− large/multi-file diff ({sig.changed_lines} lines, {sig.files_touched} files)"
        )

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
    # A fork-replay that explicitly *failed* against the recorded conditions is
    # a strong red flag on its own — the diff being scored does not survive
    # re-verification against the exact snapshot that justified drafting it —
    # so it forces high risk regardless of what tier1/tier2 said.
    if sig.tier3_passed is False:
        return "high"
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
