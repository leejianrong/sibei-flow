---
shaping: true
---

# V3 — Verified before you see it

> Slice V3 of `SLICES.md`. Adds the ephemeral sandbox and tiered verification,
> so a drafted fix carries accurate evidence and a confidence/risk label — and
> a fix that can't compile never gets proposed. This is where the product's core
> credibility is proven.

## Goal & demo

**Goal:** every drafted fix is compiled (and sample-run if configured) in a
throwaway sandbox; the run shows honest evidence + confidence/risk; junk is
suppressed.

**Demo:** the rename-drift run now shows **compiled ✓ · ran on 10k sample ✓ ·
output schema unchanged ✓** with a **confidence** score and **risk: low** label.
A deliberately broken draft shows `no_fix` (never proposed) — the review surface
stays clean.

## Affordances (from SLICES.md V3)
N10 `run_sandbox` · N11 evidence builder · N12 confidence/risk scorer ·
compile gate.

## Requirements exercised
R4.1 (compile gate), R4.2 (sample tier when configured), R4.3 (accurate +
disclosed evidence), R5.4 (confidence/risk), plus R0's happy path becomes real.

## Components & files
- **Sandbox** — `worker/sandbox/`: build/pull a **pre-baked** Python+dbt image;
  `docker run --rm` a container with the candidate diff applied to a repo
  checkout; tier-1 `dbt compile --select <model>`; tier-2 `dbt build --select
  <model> --target sample` (10k-row cap) iff a read-only dev connection is set
  (B-S2).
- **Evidence builder** — `worker/sandbox/evidence.py`: structured
  `{tier1{ran,passed,log}, tier2{ran,passed|null,log}, output_schema{changed,
  detail}}`; tier-2 absence rendered as a fact.
- **Confidence/risk scorer** — `worker/agent/score.py`: explainable rubric over
  tiers-passed, diff size, drift ambiguity, attempts → `{confidence, risk_class,
  factors[]}` (B-S4).
- **Compile gate** — `outcome = pr_proposed` only when `tier1.passed`; else the
  loop iterates or emits `no_fix`.

## Contract additions
- **RepairResult.evidence** now populated (structure above); `confidence`,
  `risk_class`, and `factors` set. The *unverified* badge from V2 is replaced by
  rendered evidence.

## Tasks
1. Pre-baked sandbox image + container lifecycle (`--rm`, timeout, no network
   beyond the read-only warehouse when tier-2 runs).
2. Tier-1/tier-2 invocations + parse of `run_results.json`.
3. Output-schema-unchanged capture (pre vs post compile/describe).
4. Evidence builder with intrinsic tier-2 disclosure.
5. Confidence/risk rubric + factor list.
6. Wire the compile gate; thicken U5 to render evidence + confidence/risk.

## Tests (PRD Seam 1 — real Docker sandbox, record/replay LLM)
- A fix failing tier-1 **never** yields `pr_proposed` (story 17).
- A passing fix's evidence reflects the tiers that actually ran; tier-2 absence
  is disclosed, not omitted (R4.3).
- Confidence/risk are derived from the recorded signals and are reproducible.

## Acceptance
Rename-drift run shows accurate compile+sample evidence + confidence/risk; a
non-compiling draft is suppressed to `no_fix`. **No ⚠️.**
