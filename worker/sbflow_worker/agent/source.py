"""Read-only source access (N7 `read_file`).

V2 reads the failing dbt source from a local checkout mounted at ``repo_root``
(read-only, R3.1). A git-token-backed provider slots in behind this same
`read` method at V4 / the hero pipeline — the loop doesn't care which.
"""

from __future__ import annotations

from pathlib import Path


class LocalSourceProvider:
    def __init__(self, repo_root: str):
        self.root = Path(repo_root).resolve()

    def read(self, path: str, ref: str | None = None) -> str:
        """Read a file relative to the repo root. `ref` is accepted for
        contract stability (git ref) but V2 reads the working tree."""
        target = (self.root / path).resolve()
        # Prevent path traversal outside the read-only checkout.
        if not str(target).startswith(str(self.root)):
            raise ValueError(f"path escapes repo root: {path}")
        if not target.is_file():
            raise FileNotFoundError(f"no such file in repo: {path}")
        return target.read_text()
