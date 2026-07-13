"""Drift/schema context (N8 `get_schema`).

Reads the *current* upstream columns from the warehouse over a **read-only**
connection (`INFORMATION_SCHEMA.COLUMNS`, B-S1). The model compares these
against the columns its failing SQL references to infer the drift (e.g. a
removed/renamed column). The candidate mapping is surfaced to the model, never
auto-applied.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

_QUERY = """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = %s AND table_name = %s
    ORDER BY ordinal_position
"""


class WarehouseSchema:
    def __init__(self, warehouse_url: str):
        self.warehouse_url = warehouse_url

    def describe(self, source: str) -> str:
        """Return a readable current-column listing for a source table.

        Accepts ``schema.table``, a bare ``table`` (defaults to schema ``raw``),
        or a dbt-style ``source.<project>.<schema>.<table>`` — the last two
        dotted segments are taken as schema.table.
        """
        schema, table = _parse_source(source)
        with psycopg.connect(
            self.warehouse_url, row_factory=dict_row, autocommit=True
        ) as conn:
            rows = conn.execute(_QUERY, (schema, table)).fetchall()

        if not rows:
            return (
                f'No columns found for "{schema}.{table}". The table may have been '
                f"renamed or dropped upstream, or the name is wrong."
            )
        lines = [f"Current columns of {schema}.{table} (read-only warehouse):"]
        for r in rows:
            null = "NULL" if r["is_nullable"] == "YES" else "NOT NULL"
            lines.append(f"  - {r['column_name']} {r['data_type']} {null}")
        return "\n".join(lines)

    def column_names(self, source: str) -> list[str]:
        """The current upstream column names for a source table (read-only).

        Used by the `needs_prod_action` rule to tell a removal (no similar
        replacement) from a rename. Returns ``[]`` if the table is gone.
        """
        schema, table = _parse_source(source)
        with psycopg.connect(
            self.warehouse_url, row_factory=dict_row, autocommit=True
        ) as conn:
            rows = conn.execute(_QUERY, (schema, table)).fetchall()
        return [r["column_name"] for r in rows]


def _parse_source(source: str) -> tuple[str, str]:
    parts = [p for p in source.split(".") if p]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return "raw", parts[0]
