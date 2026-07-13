"""Config file loading + writing for the ``sbflow`` CLI (V5, U2).

The CLI (``sbflow run`` / ``sbflow init``) is intentionally decoupled from the
worker runtime: it needs only where the brain's webhook lives and a few labels
for the ``Failure`` payload. Config is TOML, read with the stdlib ``tomllib``
(3.11+) and written by a tiny flat serializer here (no third-party TOML dep).

**Trust posture (CLAUDE.md R6.1):** the CLI only ever records READ-ONLY /
PR-scoped secrets. It never asks for, stores, or transmits a prod-write
credential.

Resolution order (first hit wins), so a project-local file beats the user one
and an explicit ``--config`` beats everything:

1. explicit ``--config PATH`` (or the ``path=`` arg here)
2. ``$SBFLOW_CONFIG``
3. ``./sbflow.toml`` (project-local, handy for a cron/CI checkout)
4. ``$XDG_CONFIG_HOME/sbflow/config.toml`` (default ``~/.config/sbflow/…``)

Any individual field can still be overridden at call time by an env var
(``SBFLOW_REPO``, ``SBFLOW_WEBHOOK_URL``, ``SBFLOW_ADAPTER``) so containers and
CI don't need a file at all.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_WEBHOOK_URL = "http://localhost:8080/webhook"
DEFAULT_ADAPTER = "postgres"
DEFAULT_LLM_PROVIDER = "replay"


def default_user_config_path() -> Path:
    """``$XDG_CONFIG_HOME/sbflow/config.toml`` (default ``~/.config/…``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "sbflow" / "config.toml"


def resolve_config_path(explicit: str | None = None) -> Path | None:
    """Locate the config file per the documented resolution order.

    Returns the first path that exists, or ``None`` when the CLI should fall
    back to env vars + defaults only.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("SBFLOW_CONFIG")
    if env:
        return Path(env)
    local = Path.cwd() / "sbflow.toml"
    if local.is_file():
        return local
    user = default_user_config_path()
    if user.is_file():
        return user
    return None


@dataclass
class CliConfig:
    """Resolved CLI settings. Secrets are kept out of the payload path."""

    repo: str = "unknown"
    webhook_url: str = DEFAULT_WEBHOOK_URL
    adapter: str = DEFAULT_ADAPTER
    llm_provider: str = DEFAULT_LLM_PROVIDER
    # READ-ONLY / PR-scoped only. Empty strings mean "not set".
    git_token: str = ""
    llm_api_key: str = ""
    sample_warehouse_url: str = ""
    # Where this config came from (diagnostics only; never persisted).
    source_path: Path | None = field(default=None, compare=False)

    @staticmethod
    def load(path: str | None = None) -> "CliConfig":
        """Load config from disk (if any), then apply env-var overrides."""
        cfg = CliConfig()
        resolved = resolve_config_path(path)
        if resolved and resolved.is_file():
            data = tomllib.loads(resolved.read_text())
            secrets = data.get("secrets", {})
            cfg = CliConfig(
                repo=str(data.get("repo", cfg.repo)),
                webhook_url=str(data.get("webhook_url", cfg.webhook_url)),
                adapter=str(data.get("adapter", cfg.adapter)),
                llm_provider=str(data.get("llm_provider", cfg.llm_provider)),
                git_token=str(secrets.get("git_token", "")),
                llm_api_key=str(secrets.get("llm_api_key", "")),
                sample_warehouse_url=str(secrets.get("sample_warehouse_url", "")),
                source_path=resolved,
            )
        # Env overrides (containers/CI need no file).
        cfg.repo = os.environ.get("SBFLOW_REPO", cfg.repo)
        cfg.webhook_url = os.environ.get("SBFLOW_WEBHOOK_URL", cfg.webhook_url)
        cfg.adapter = os.environ.get("SBFLOW_ADAPTER", cfg.adapter)
        return cfg

    def to_toml(self) -> str:
        """Serialize to TOML. Only non-empty secrets are written."""

        def q(v: str) -> str:
            # Minimal escaping for TOML basic strings.
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'

        lines = [
            "# sibei-flow CLI config — written by `sbflow init`.",
            "#",
            "# TRUST POSTURE: this file holds only READ-ONLY / PR-scoped secrets.",
            "# sibei-flow never asks for, stores, or uses a prod-write credential.",
            "# Keep it chmod 600; prefer env vars (SBFLOW_GIT_TOKEN, etc.) in CI.",
            "",
            f"repo = {q(self.repo)}",
            f"webhook_url = {q(self.webhook_url)}",
            f"adapter = {q(self.adapter)}",
            f"llm_provider = {q(self.llm_provider)}",
        ]
        secret_lines: list[str] = []
        if self.git_token:
            secret_lines.append(
                "# Read-only source + PR-scoped git token (opens PRs; NOT prod-write)."
            )
            secret_lines.append(f"git_token = {q(self.git_token)}")
        if self.llm_api_key:
            secret_lines.append(
                "# Optional LLM key. Empty => keyless `replay` provider (default)."
            )
            secret_lines.append(f"llm_api_key = {q(self.llm_api_key)}")
        if self.sample_warehouse_url:
            secret_lines.append(
                "# READ-ONLY dev/sample warehouse DSN for tier-2 sample builds "
                "(never prod)."
            )
            secret_lines.append(
                f"sample_warehouse_url = {q(self.sample_warehouse_url)}"
            )
        if secret_lines:
            lines.append("")
            lines.append("[secrets]")
            lines.extend(secret_lines)
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> None:
        """Write the config to ``path`` with owner-only (0600) permissions."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_toml())
        # Secrets may live here — lock it down.
        os.chmod(path, 0o600)
