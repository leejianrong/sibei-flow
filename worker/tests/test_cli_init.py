"""Fast/no-infra tests for `sbflow init` — onboarding + config write (V5, R1.5).

Drives the interactive flow non-interactively via an injected prompt queue.
Runs in the `make test-fast` lane (no DB / warehouse / Docker).
"""

from __future__ import annotations

import stat
import tomllib
from pathlib import Path

from sbflow_worker.cli.config import CliConfig
from sbflow_worker.cli.init import cmd_init


def _queue_prompter(answers: list[str]):
    """Return a prompt fn that pops queued answers; captures prompt text."""
    it = iter(answers)
    seen: list[str] = []

    def prompt(label: str) -> str:
        seen.append(label)
        return next(it, "")

    return prompt, seen


def _out_collector():
    lines: list[str] = []
    return (lambda s: lines.append(s)), lines


def test_init_writes_expected_config(tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    prompt, _ = _queue_prompter(
        [
            "acme/analytics",  # repo
            "http://brain:8080/webhook",  # webhook url
            "postgres",  # adapter
            "ghp_readonly_token",  # git token
            "sk-test-key",  # llm api key (non-empty -> asks provider)
            "claude",  # llm provider
            "postgres://ro@wh:5432/dev",  # sample warehouse dsn
        ]
    )
    out, _ = _out_collector()
    rc = cmd_init(config_path=str(cfg_path), prompt=prompt, out=out)
    assert rc == 0

    data = tomllib.loads(cfg_path.read_text())
    assert data["repo"] == "acme/analytics"
    assert data["webhook_url"] == "http://brain:8080/webhook"
    assert data["adapter"] == "postgres"
    assert data["llm_provider"] == "claude"
    assert data["secrets"]["git_token"] == "ghp_readonly_token"
    assert data["secrets"]["llm_api_key"] == "sk-test-key"
    assert data["secrets"]["sample_warehouse_url"] == "postgres://ro@wh:5432/dev"


def test_init_blank_llm_key_keeps_replay_provider(tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    # Blank git token, blank LLM key, blank sample DSN — the minimal path.
    prompt, seen = _queue_prompter(
        ["acme/analytics", "http://brain:8080/webhook", "postgres", "", "", ""]
    )
    out, _ = _out_collector()
    cmd_init(config_path=str(cfg_path), prompt=prompt, out=out)

    data = tomllib.loads(cfg_path.read_text())
    assert data["llm_provider"] == "replay"  # keyless default preserved
    # No secrets set -> the [secrets] table is omitted entirely.
    assert "secrets" not in data
    # A blank LLM key must NOT trigger the provider follow-up prompt.
    assert not any("provider" in label.lower() for label in seen)


def test_init_config_is_owner_only_readable(tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    prompt, _ = _queue_prompter(
        ["acme/analytics", "http://brain/webhook", "postgres", "ghp_x", "", ""]
    )
    out, _ = _out_collector()
    cmd_init(config_path=str(cfg_path), prompt=prompt, out=out)
    mode = stat.S_IMODE(cfg_path.stat().st_mode)
    assert mode == 0o600  # secrets file locked to the owner


def test_init_trust_posture_only_asks_read_only(tmp_path: Path):
    """The flow must never request a prod-write credential (R6.1)."""
    cfg_path = tmp_path / "config.toml"
    prompt, seen = _queue_prompter(
        ["acme/analytics", "http://brain/webhook", "postgres", "", "", ""]
    )
    out, out_lines = _out_collector()
    cmd_init(config_path=str(cfg_path), prompt=prompt, out=out)

    banner = "\n".join(out_lines).lower()
    assert "read-only" in banner
    assert "never" in banner and "prod-write" in banner
    # No prompt asks for a prod / write / admin credential.
    for label in seen:
        low = label.lower()
        assert "prod" not in low
        assert "write" not in low
        assert "admin" not in low


def test_init_roundtrips_through_cliconfig_loader(tmp_path: Path):
    cfg_path = tmp_path / "config.toml"
    prompt, _ = _queue_prompter(
        ["team/repo", "http://brain/webhook", "snowflake", "", "", ""]
    )
    out, _ = _out_collector()
    cmd_init(config_path=str(cfg_path), prompt=prompt, out=out)

    loaded = CliConfig.load(str(cfg_path))
    assert loaded.repo == "team/repo"
    assert loaded.adapter == "snowflake"
    assert loaded.llm_provider == "replay"
