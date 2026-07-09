"""Resolve a failing dbt node to its source file (and its schema yml).

Uses the dbt `target/manifest.json` when present (B-S1: file path + referenced
columns come from the manifest). Falls back to a filesystem search by model
name so the demo still works if the manifest is absent.
"""

from __future__ import annotations

import json
from pathlib import Path


def resolve_file(repo_root: str, node_uid: str) -> str | None:
    """Return the failing model's source path (relative to repo root), or None."""
    root = Path(repo_root)
    manifest = root / "target" / "manifest.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text())
            node = data.get("nodes", {}).get(node_uid)
            if node and node.get("original_file_path"):
                return node["original_file_path"]
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: derive the model name from the node uid and search for it.
    name = node_uid.split(".")[-1]
    for candidate in root.rglob(f"{name}.sql"):
        if candidate.is_file():
            return str(candidate.relative_to(root))
    return None


def allowed_edit_paths(repo_root: str, model_path: str) -> set[str]:
    """The scope the diff guard permits: the model file + its sibling schema.yml."""
    allowed = {model_path}
    schema_yml = str(Path(model_path).parent / "schema.yml")
    if (Path(repo_root) / schema_yml).is_file():
        allowed.add(schema_yml)
    return allowed
