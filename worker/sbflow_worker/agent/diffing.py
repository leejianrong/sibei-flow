"""In-memory working copy + unified-diff computation.

The agent never writes to the real checkout (source is read-only, R3.1). Edits
are applied to an in-memory copy; the resulting unified diff is what the
RepairResult carries (and, from V4, what the PR opens).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass
class _FileState:
    original: str
    current: str


class WorkingCopy:
    """Tracks original vs edited content per file path."""

    def __init__(self) -> None:
        self._files: dict[str, _FileState] = {}

    def load(self, path: str, content: str) -> None:
        """Register a file's original content (idempotent — first load wins)."""
        if path not in self._files:
            self._files[path] = _FileState(original=content, current=content)

    def has(self, path: str) -> bool:
        return path in self._files

    def current(self, path: str) -> str:
        return self._files[path].current

    def set_current(self, path: str, content: str) -> None:
        self._files[path].current = content

    def apply_replace(self, path: str, old: str, new: str) -> tuple[bool, str]:
        """Targeted single-occurrence replacement (no full-file rewrites, B-S5).

        Returns ``(ok, message)``; on failure nothing is mutated.
        """
        cur = self._files[path].current
        count = cur.count(old)
        if count == 0:
            return False, "old_string not found in file"
        if count > 1:
            return False, f"old_string is ambiguous (matches {count} places); add more context"
        self._files[path].current = cur.replace(old, new, 1)
        return True, "ok"

    def file_diff(self, path: str) -> str:
        st = self._files[path]
        if st.original == st.current:
            return ""
        return _unified(path, st.original, st.current)

    def changed_paths(self) -> list[str]:
        return [p for p, st in self._files.items() if st.original != st.current]

    def full_diff(self) -> str:
        return "".join(self.file_diff(p) for p in self.changed_paths())


def _unified(path: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    )
    return "".join(diff)


def changed_line_count(diff: str) -> int:
    """Count added/removed content lines (ignore the +++/--- and @@ headers)."""
    n = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            n += 1
    return n
