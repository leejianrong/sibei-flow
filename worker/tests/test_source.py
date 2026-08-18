"""Unit tests for `LocalSourceProvider` (N7 `read_file`). No DB required.

Covers the path-traversal boundary check (KAN-223 security review): a naive
`str(target).startswith(str(self.root))` is a *prefix* match, not a path
boundary, so it would wrongly let a sibling directory whose name extends the
root's (e.g. root `/repo` letting `/repo-secret/x` through) escape the
read-only checkout.
"""

from __future__ import annotations

import pytest

from sbflow_worker.agent.source import LocalSourceProvider


def test_reads_file_inside_root(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "orders.sql").write_text("select 1\n")
    provider = LocalSourceProvider(str(tmp_path))
    assert provider.read("models/orders.sql") == "select 1\n"


def test_rejects_dotdot_traversal(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("top secret\n")
    provider = LocalSourceProvider(str(root))
    with pytest.raises(ValueError, match="escapes repo root"):
        provider.read("../secret.txt")


def test_rejects_sibling_dir_sharing_root_prefix(tmp_path):
    # Regression for the prefix-match bug: a sibling directory named
    # `<root><suffix>` shares a string prefix with root but is NOT inside it.
    root = tmp_path / "repo"
    root.mkdir()
    sibling = tmp_path / "repo-secret"
    sibling.mkdir()
    (sibling / "leak.txt").write_text("leaked\n")
    provider = LocalSourceProvider(str(root))
    with pytest.raises(ValueError, match="escapes repo root"):
        provider.read("../repo-secret/leak.txt")


def test_missing_file_raises_file_not_found(tmp_path):
    provider = LocalSourceProvider(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        provider.read("nope.sql")
