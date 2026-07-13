"""Deterministic diff guard (N9 / B-S5).

Runs after each `edit_file` and rejects a change that is out of scope or too
large, feeding the reason back so the model re-drafts. This keeps the diff
minimal and legible (R5.5) and is deterministic (no LLM), so an oversized or
off-scope edit can never reach the PR.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diffing import WorkingCopy, changed_line_count


@dataclass
class DiffGuard:
    #: Reject a single file's diff that changes more than this many lines.
    max_lines: int = 40

    def check(
        self, path: str, working: WorkingCopy, allowed_paths: set[str]
    ) -> tuple[bool, str]:
        # (a) scope: only the failing model file (and its schema yml).
        if allowed_paths and path not in allowed_paths:
            allowed = ", ".join(sorted(allowed_paths))
            return False, (
                f"{path} is out of scope; only the failing model may be edited "
                f"(allowed: {allowed})"
            )

        diff = working.file_diff(path)
        if not diff:
            return False, "edit produced no change"

        # (b) size: keep the blast radius obvious.
        n = changed_line_count(diff)
        if n > self.max_lines:
            return (
                False,
                f"diff too large ({n} lines > {self.max_lines}); keep it minimal",
            )

        # (c) not whitespace-only churn: require a real, non-whitespace change.
        before = "".join(_norm(working, path, original=True))
        after = "".join(_norm(working, path, original=False))
        if before == after:
            return False, "whitespace-only change; no substantive edit"

        return True, "ok"


def _norm(working: WorkingCopy, path: str, original: bool) -> list[str]:
    # Compare content with all whitespace stripped to detect churn-only diffs.
    text = working._files[path].original if original else working._files[path].current
    return ["".join(text.split())]
