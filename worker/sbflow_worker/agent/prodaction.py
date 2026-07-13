"""N13 `needs_prod_action` rule — honest prod-action recommendation (V5).

Some schema drift cannot be healed by a code-only edit. When the failing model
is **`materialized = incremental`** and the drift is **NOT a safe 1:1 column
rename** — specifically a column **removal** or a **retype** — a SQL edit would
silently assume that a prod change (a full-refresh / backfill / migration)
already happened. The already-materialized historical rows still carry the old
shape, so a "fix" that compiles today would corrupt or mis-read history.

The rule below detects those cases from deterministic signals (the model source
+ the error text + the current upstream columns) and returns a plain-English
**recommendation** for a human to act on in prod. The agent loop turns that into
``outcome = needs_prod_action`` (no diff), so the fix is *never* proposed as a PR
and the system never assumes a prod migration happened.

Everything here is pure logic (no DB / warehouse); the loop supplies the current
upstream columns when a read-only warehouse connection is available.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# A model is incremental when its dbt config materializes it as `incremental`,
# e.g. `{{ config(materialized='incremental', unique_key='id') }}`.
_INCREMENTAL_RE = re.compile(
    r"""materialized\s*[=:]\s*['"]?incremental['"]?""", re.IGNORECASE
)

# Retype drift phrasings across Postgres / Snowflake / BigQuery. These are
# unambiguously a *type* change (never a rename), so they always require a prod
# action on an incremental model — no warehouse lookup needed.
_RETYPE_MARKERS = (
    "datatype mismatch",  # generic / dbt
    "cannot cast",  # Snowflake / generic
    "cannot coerce",  # BigQuery
    "but expression is of type",  # Postgres: "column x is of type int but ..."
    "does not match column data type",  # Snowflake
    "cannot be assigned to",  # BigQuery assignment
    "type mismatch",  # generic
    "incompatible type",  # generic
)

# Patterns that name the offending column in a drift error, most specific first.
_MISSING_COL_RES = (
    re.compile(r"""column\s+["']?([a-zA-Z_][\w.]*)""", re.IGNORECASE),
    re.compile(r"""invalid identifier\s+["']?([a-zA-Z_][\w.]*)""", re.IGNORECASE),
    re.compile(r"""unrecognized name:?\s*["']?([a-zA-Z_][\w.]*)""", re.IGNORECASE),
    re.compile(r"""\bname\s+["']?([a-zA-Z_][\w.]*)["']?\s+not found""", re.IGNORECASE),
    re.compile(r"""\bfield\s+["']?([a-zA-Z_][\w.]*)""", re.IGNORECASE),
)

# Above this normalized-name similarity we treat a missing column as *renamed*
# (a similarly-named replacement exists upstream) rather than *removed*.
_RENAME_SIMILARITY = 0.6


def is_incremental_model(model_source: str) -> bool:
    """True when the model's dbt config materializes it as ``incremental``."""
    return bool(_INCREMENTAL_RE.search(model_source or ""))


def is_retype_drift(error_text: str) -> bool:
    """True when the error text names a column *type* change (never a rename)."""
    e = (error_text or "").lower()
    return any(m in e for m in _RETYPE_MARKERS)


def parse_missing_column(error_text: str) -> str | None:
    """Best-effort extraction of the offending column name from a drift error."""
    for rx in _MISSING_COL_RES:
        m = rx.search(error_text or "")
        if m:
            # Keep only the trailing identifier (strip a `schema.table.` prefix).
            return m.group(1).split(".")[-1]
    return None


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _has_similar_column(missing: str | None, current: list[str]) -> bool:
    """A rename is likely when some current column closely matches ``missing``."""
    if not missing:
        return False
    target = _normalize(missing)
    if not target:
        return False
    for col in current:
        cand = _normalize(col)
        if not cand:
            continue
        if cand == target or cand in target or target in cand:
            return True
        if SequenceMatcher(None, target, cand).ratio() >= _RENAME_SIMILARITY:
            return True
    return False


def build_recommendation(kind: str, missing_col: str | None, node_uid: str) -> str:
    """A plain-English prod-action recommendation (no code change implied)."""
    node = node_uid or "the failing model"
    col = f"`{missing_col}`" if missing_col else "a referenced column"
    if kind == "retype":
        what = f"The upstream type of {col} changed"
        why = (
            "Editing the model SQL to cast to the new type would silently assume "
            "the already-materialized historical partitions were migrated too — "
            "they were not."
        )
    else:  # removal
        what = f"{col} was removed upstream with no rename target"
        why = (
            "Dropping the reference (or aliasing a replacement) would change the "
            "output contract and leave already-materialized history inconsistent."
        )
    return (
        f"{what}, and `{node}` is an incremental model, so sibei-flow will NOT "
        f"auto-draft a fix here. {why}\n\n"
        "Recommended prod action (for a human to run, not sibei-flow):\n"
        f"  1. Confirm the intended upstream change for {col}.\n"
        f"  2. Backfill / migrate the existing prod rows so history matches, or "
        f"full-refresh the model: `dbt build --full-refresh --select {node}`.\n"
        "  3. Only then update the model SQL and re-run.\n\n"
        "sibei-flow holds no prod-write credentials and will not open a PR that "
        "assumes this migration already happened."
    )


def drift_requires_prod_action(
    *,
    model_source: str,
    error_text: str,
    node_uid: str,
    current_columns: list[str] | None = None,
) -> str | None:
    """Return a recommendation when this drift needs a prod action, else None.

    Fires only for an **incremental** model hit by **non-rename** drift:
    - a **retype** (detected from the error text alone), or
    - a **removal** — a missing column with no similarly-named replacement in the
      current upstream columns (requires ``current_columns``; when they are
      unavailable a missing-column drift is left to the normal rename path, whose
      compile gate + evidence still protect the output).

    A safe 1:1 **rename** returns ``None`` (the aliased fix is emitted normally,
    even on an incremental model — it preserves the output contract).
    """
    if not is_incremental_model(model_source):
        return None

    missing = parse_missing_column(error_text)

    if is_retype_drift(error_text):
        return build_recommendation("retype", missing, node_uid)

    # Column-missing drift: distinguish rename (safe) from removal (prod action).
    if current_columns is None:
        return None
    if _has_similar_column(missing, current_columns):
        return None  # a similarly-named column exists ⇒ safe rename
    return build_recommendation("removal", missing, node_uid)
