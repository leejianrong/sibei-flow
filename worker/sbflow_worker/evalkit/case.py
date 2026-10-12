"""Eval case format (SIB-81).

A case is a directory under ``eval/cases/`` holding a ``case.json`` plus the
files it points at::

    eval/cases/<id>/
      case.json        # this module's schema (below)
      project/         # the BROKEN dbt project (default; override with "project")
      warehouse.sql    # optional: run on the warehouse ADMIN connection first
      teardown.sql     # optional: run after the case (drop what warehouse.sql made)

``case.json``::

    {
      "id": "rename_customer_id",
      "description": "one line",
      "failure": {                       # the Failure webhook payload
        "repo": "acme/analytics", "run_id": "...", "task_id": "...",
        "node_uid": "model.analytics.orders",
        "error_text": "...", "adapter": "postgres", "source": "airflow"
      },
      "expect": {
        "outcome": "pr_proposed",        # pr_proposed | no_fix | needs_prod_action | out_of_scope
        "diff_contains": ["cust_id as customer_id"]   # optional: substrings a CORRECT fix has
      },
      "replay": "path/to/session.json",  # optional: replay-provider script (keyless runs)
      "project": "../../../fixtures/dbt_project"      # optional: relative to the case dir
    }

``out_of_scope`` cases never reach the worker (the brain's classifier drops
them), so the runner checks them against the brain instead of the repair loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Outcomes a case may expect (the ``RepairResult.outcome`` values, plus the
#: brain-side ``out_of_scope`` drop).
OUTCOMES = ("pr_proposed", "no_fix", "needs_prod_action", "out_of_scope")

_FAILURE_KEYS = (
    "repo",
    "run_id",
    "task_id",
    "node_uid",
    "error_text",
    "adapter",
    "source",
)


class CaseError(ValueError):
    """A case directory is malformed."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    description: str
    failure: dict[str, Any]
    expect_outcome: str
    expect_diff_contains: tuple[str, ...] = ()
    project_dir: Path | None = None
    replay: Path | None = None
    warehouse_sql: Path | None = None
    teardown_sql: Path | None = None
    case_dir: Path = field(default_factory=Path)

    @property
    def dispatched(self) -> bool:
        """Whether the brain would hand this failure to the worker."""
        return self.expect_outcome != "out_of_scope"


def load_case(case_dir: str | Path) -> EvalCase:
    """Load and validate one case directory. Raises :class:`CaseError`."""
    d = Path(case_dir)
    spec_path = d / "case.json"
    if not spec_path.is_file():
        raise CaseError(f"{d}: missing case.json")
    try:
        spec = json.loads(spec_path.read_text())
    except json.JSONDecodeError as e:
        raise CaseError(f"{spec_path}: invalid JSON ({e})") from e

    cid = spec.get("id")
    if not isinstance(cid, str) or not cid:
        raise CaseError(f"{spec_path}: 'id' is required")
    if cid != d.name:
        raise CaseError(
            f"{spec_path}: id {cid!r} must match the directory name {d.name!r}"
        )

    failure = spec.get("failure")
    if not isinstance(failure, dict):
        raise CaseError(f"{cid}: 'failure' must be an object")
    missing = [k for k in _FAILURE_KEYS if not failure.get(k)]
    if missing:
        raise CaseError(f"{cid}: failure is missing {missing}")

    expect = spec.get("expect")
    if not isinstance(expect, dict):
        raise CaseError(f"{cid}: 'expect' must be an object")
    outcome = expect.get("outcome")
    if outcome not in OUTCOMES:
        raise CaseError(
            f"{cid}: expect.outcome must be one of {OUTCOMES}, got {outcome!r}"
        )
    contains = expect.get("diff_contains", [])
    if not isinstance(contains, list) or not all(isinstance(s, str) for s in contains):
        raise CaseError(f"{cid}: expect.diff_contains must be a list of strings")
    if contains and outcome != "pr_proposed":
        raise CaseError(
            f"{cid}: expect.diff_contains only makes sense with outcome pr_proposed"
        )

    project_dir: Path | None = None
    if outcome != "out_of_scope":
        project_dir = (d / spec.get("project", "project")).resolve()
        if not project_dir.is_dir():
            raise CaseError(f"{cid}: project directory not found: {project_dir}")

    replay = (d / spec["replay"]).resolve() if spec.get("replay") else None
    if replay is not None and not replay.is_file():
        raise CaseError(f"{cid}: replay file not found: {replay}")

    def optional(name: str) -> Path | None:
        p = d / name
        return p if p.is_file() else None

    return EvalCase(
        id=cid,
        description=str(spec.get("description", "")),
        failure=failure,
        expect_outcome=outcome,
        expect_diff_contains=tuple(contains),
        project_dir=project_dir,
        replay=replay,
        warehouse_sql=optional("warehouse.sql"),
        teardown_sql=optional("teardown.sql"),
        case_dir=d,
    )


def discover(cases_dir: str | Path, only: set[str] | None = None) -> list[EvalCase]:
    """Load every case under ``cases_dir`` (sorted by id), optionally filtered."""
    root = Path(cases_dir)
    if not root.is_dir():
        raise CaseError(f"cases directory not found: {root}")
    cases = [
        load_case(p) for p in sorted(root.iterdir()) if (p / "case.json").is_file()
    ]
    if only:
        unknown = only - {c.id for c in cases}
        if unknown:
            raise CaseError(f"unknown case id(s): {sorted(unknown)}")
        cases = [c for c in cases if c.id in only]
    return cases
