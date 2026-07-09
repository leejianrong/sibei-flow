---
shaping: true
---

# V2 — Drift diagnosis & drafted fix

> Slice V2 of `SLICES.md`. Adds the agent loop that reads source, detects drift,
> and drafts a minimal fix — visible as a diff in the dashboard. No sandbox
> verification and no PR yet (those are V3 and V4), so the drafted diff is
> clearly labeled *unverified* in the UI.

## Goal & demo

**Goal:** the worker turns an in-scope failure into a drafted `{diff,
explanation, transcript}` for the flagship column-rename case.

**Demo:** fail the fixture dbt project by renaming `customer_id → cust_id`
upstream; the dashboard run now shows a **minimal drafted diff** (model updated
to the new column), a plain-English explanation of the drift, and the agent's
reasoning transcript — marked *unverified*.

## Affordances (from SLICES.md V2)
N6 agent loop · N7 `read_file` · N8 `get_schema` · N9 `edit_file` + diff guard ·
N13 write-back thickened · U5 detail thickened.

## Requirements exercised
R3.1 (read-only source), R3.2 (`LlmProvider` BYO/local), R3.3 (bounded ≤N),
R5.2 (explanation), R5.3 (transcript), R5.5 (minimal diff).

## Components & files
- **Worker (Python)** — `worker/agent/`: the bounded loop + tool surface
  (`read_file`, `get_schema`, `edit_file`) behind `LlmProvider`.
- **`LlmProvider`** — `worker/llm/`: interface + a Claude provider + a
  record/replay provider (the test seam) + an OpenAI-compatible/local provider.
- **Drift detection** — `get_schema` queries `INFORMATION_SCHEMA` over the
  read-only warehouse connection; diffs referenced vs current columns (B-S1).
- **Diff guard** — `worker/agent/diffguard.py`: rejects out-of-scope/oversize
  diffs, feeds back for re-draft (B-S5).

## Contract additions
- **RepairResult** now populated with `diff`, `explanation`, `transcript`;
  `outcome = pr_proposed` **but** `evidence = null` (unverified) — the UI shows
  an *unverified* badge until V3.
- **Tool contract** (worker-internal, stable to phase B): `read_file(path,
  ref)`, `get_schema(source)`, `edit_file(path, old, new)`.

## Tasks
1. Implement the bounded loop: read failing source + error + schema → draft →
   `edit_file` → guard → emit; cap at ≤N attempts, `no_fix` on exhaustion.
2. `get_schema` drift diff + candidate rename mapping surfaced to the model.
3. Diff guard: single-file/model scope + line threshold + whitespace-churn
   rejection with re-draft feedback.
4. `LlmProvider` interface + Claude + record/replay + OpenAI-compatible impls.
5. Thicken U5 to render diff + explanation + transcript with an *unverified*
   badge.

## Tests (PRD Seam 1, first cut — record/replay LLM)
- Rename-drift fixture → drafted diff updates the model to the new column;
  explanation names the drift; transcript present.
- Diff guard rejects a full-file rewrite and forces a re-draft.
- Loop stops at N attempts → `no_fix` (story 29).

## Acceptance
Rename drift → dashboard shows a minimal, correct, *unverified* drafted diff +
explanation + transcript. **No ⚠️.**
