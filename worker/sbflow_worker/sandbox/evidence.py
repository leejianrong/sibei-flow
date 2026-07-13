"""N11 evidence builder (B-S2-Q3).

Turns a :class:`SandboxRun` into the structured, honest evidence record that the
PR / dashboard renders::

    {
      "tier1": {"ran": true,  "passed": bool,       "log": str},
      "tier2": {"ran": bool,  "passed": bool|null,  "log": str},
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
"""

from __future__ import annotations

import re
from typing import Any

from ..agent.diffing import WorkingCopy
from .runner import SandboxRun


def build_evidence(
    run: SandboxRun, working: WorkingCopy, model_path: str
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
        "output_schema": {"changed": changed, "detail": detail},
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
    out, depth, cur = [], 0, []
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
