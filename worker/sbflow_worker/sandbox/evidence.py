"""N11 evidence builder (B-S2-Q3).

Turns a :class:`SandboxRun` into the structured, honest evidence record that the
PR / dashboard renders::

    {
      "tier1": {"ran": true,  "passed": bool,       "log": str},
      "tier2": {"ran": bool,  "passed": bool|null,  "log": str},
      "tier3": {"ran": bool,  "passed": bool|null,  "source_run_id": str|null,
                "fork_run_id": str|null, "get_schema_ordinal": int|null,
                "log": str},
      "output_schema": {"changed": bool|null, "detail": str}
    }

Disclosure is *intrinsic*: when no dev/sample connection is configured,
``tier2.ran = false`` and ``tier2.passed = null`` — the UI renders "sample run:
not configured" as a fact, never an omission (R4.3).

Output-schema-unchanged is captured by comparing the failing model's output
column set **before** vs **after** the fix. This is derived from the in-memory
working copy (original vs edited SQL), so it is deterministic and needs no extra
container run. A pure rename that aliases back to the old name (e.g.
``cust_id as customer_id``) keeps the output contract stable → ``changed: false``.

**KAN-650 — tier3.** The fork-replay signal (ADR-0012's Consequences section:
"fork the repair run at the failing call and replay a candidate fix against the
recorded inputs... a strong [signal]", `agent/satay_loop.py`'s
`reverify_with_fork`). Unlike tier1/tier2, tier-3 is not attempted for every
run today (see that module's docstring for the invocation-scope decision), so
`build_evidence` defaults it to the disclosed "not run" shape
(`tier3_not_run`) unless a caller passes an already-computed block from
`reverify_with_fork`'s own result (`tier3_result`/`tier3_failed`) — this keeps
the same "disclose, don't fabricate" contract tier1/tier2 already have: the key
is always present, and its `ran`/`passed` fields never guess.
"""

from __future__ import annotations

import re
from typing import Any

from ..agent.diffing import WorkingCopy
from .runner import SandboxRun


def build_evidence(
    run: SandboxRun,
    working: WorkingCopy,
    model_path: str,
    tier3: dict[str, Any] | None = None,
) -> dict[str, Any]:
    changed, detail = output_schema_delta(working, model_path)
    return {
        "tier1": {
            "ran": run.tier1.ran,
            "passed": run.tier1.passed,
            "log": run.tier1.log,
        },
        "tier2": {
            "ran": run.tier2.ran,
            "passed": run.tier2.passed,
            "log": run.tier2.log,
        },
        "tier3": tier3
        if tier3 is not None
        else tier3_not_run("fork-replay not attempted for this run"),
        "output_schema": {"changed": changed, "detail": detail},
    }


# --- KAN-650: tier-3 (fork-replay) evidence builders -----------------------
#
# Three constructors, one disclosed shape (`ran`/`passed`/provenance/`log`),
# mirroring tier1/tier2's own "always present, never guessed" contract:
#
# - `tier3_not_run(reason)` — no fork-replay was attempted (today's default
#   for every automatically-produced RepairResult — see `satay_loop.py`).
# - `tier3_result(...)` — a fork-replay ran and its outcome (pass/fail) is
#   known; `passed` carries the real verdict.
#
# Both live here (not inline in `satay_loop.py`) so the evidence *shape* has
# exactly one definition, matching how `tier1`/`tier2` are only ever built by
# this module's own `build_evidence`.


def tier3_not_run(reason: str) -> dict[str, Any]:
    """The disclosed "no fork-replay attempted" tier-3 block."""
    return {
        "ran": False,
        "passed": None,
        "source_run_id": None,
        "fork_run_id": None,
        "get_schema_ordinal": None,
        "log": reason,
    }


def tier3_result(
    *,
    passed: bool | None,
    source_run_id: str,
    fork_run_id: str,
    get_schema_ordinal: int,
    log: str,
) -> dict[str, Any]:
    """A completed fork-replay's tier-3 block (`agent/satay_loop.reverify_with_fork`).

    ``passed`` is ``None`` when the fork ran but produced no diff to verify at
    all (e.g. the substituted content was byte-identical to the original) —
    disclosed as undetermined rather than guessed either way, matching
    ``output_schema``'s own tri-state convention above.
    """
    return {
        "ran": True,
        "passed": passed,
        "source_run_id": source_run_id,
        "fork_run_id": fork_run_id,
        "get_schema_ordinal": get_schema_ordinal,
        "log": log,
    }


def output_schema_delta(
    working: WorkingCopy, model_path: str
) -> tuple[bool | None, str]:
    """Compare the model's output columns before vs after the edit.

    Returns ``(changed, detail)`` where ``changed`` is ``None`` when the output
    columns cannot be parsed confidently (disclosed as undetermined, not faked).
    """
    if not working.has(model_path):
        return None, "output columns undetermined (model not loaded)"
    before = working._files[model_path].original
    after = working._files[model_path].current
    cols_before = _final_select_columns(before)
    cols_after = _final_select_columns(after)
    if cols_before is None or cols_after is None:
        return None, "output columns could not be parsed from the model SQL"
    if cols_before == cols_after:
        return False, f"output columns unchanged: {', '.join(cols_before) or '(none)'}"
    added = [c for c in cols_after if c not in cols_before]
    removed = [c for c in cols_before if c not in cols_after]
    bits = []
    if added:
        bits.append("added " + ", ".join(added))
    if removed:
        bits.append("removed " + ", ".join(removed))
    return True, "output columns changed: " + "; ".join(bits)


# --- lightweight SQL output-column parsing --------------------------------
_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")


def _final_select_columns(sql: str) -> list[str] | None:
    """Parse the output column names of the final top-level SELECT.

    Deterministic and intentionally simple — it handles the SELECT-list shapes
    dbt models use (bare columns, ``expr as alias``, dotted refs). On anything it
    cannot parse it returns ``None`` so the caller discloses "undetermined"
    rather than guessing.
    """
    text = _LINE_COMMENT.sub("", _JINJA.sub("x", sql))
    lower = text.lower()
    # Find the last top-level "select" and the next "from" keyword after it.
    sel = lower.rfind("select")
    if sel == -1:
        return None
    m = re.search(r"\bfrom\b", lower[sel + len("select") :])
    if m is None:
        return None
    select_list = text[sel + len("select") : sel + len("select") + m.start()]
    parts = _split_top_level(select_list)
    cols: list[str] = []
    for part in parts:
        name = _output_name(part.strip())
        if name is None:
            return None
        cols.append(name)
    return cols


def _split_top_level(s: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def _output_name(expr: str) -> str | None:
    if not expr or expr == "*":
        return None  # star selects: cannot enumerate → undetermined
    low = expr.lower()
    if " as " in low:
        alias = expr[low.rfind(" as ") + 4 :].strip()
        return alias.strip('"').strip()
    # No alias: the output name is the trailing identifier (strip table qualifier).
    token = re.split(r"\s+", expr.strip())[-1]
    token = token.split(".")[-1].strip('"')
    return token if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token) else None
