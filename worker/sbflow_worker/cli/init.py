"""``sbflow init`` — one-minute first-run onboarding (V5 task 5, R1.5, U2).

Walks a new user through the minimal setup: repo, brain webhook URL, adapter,
and the OPTIONAL secrets — a read-only / PR-scoped git token, an LLM key
(optional; empty keeps the keyless ``replay`` provider), and an OPTIONAL
read-only dev/sample warehouse DSN for tier-2 sample builds. Writes a TOML
config (default ``~/.config/sbflow/config.toml``, 0600).

**Trust posture is load-bearing (CLAUDE.md R6.1):** this flow only ever asks for
READ-ONLY / PR-scoped credentials and says so at every secret prompt. It never
requests a prod-write credential. Tier-2 targets a dev/sample schema, never prod.

The flow is dependency-injected (``prompt``/``out``) so tests can drive it
non-interactively with a queue of answers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .config import CliConfig, default_user_config_path

Prompt = Callable[[str], str]
Out = Callable[[str], None]

_BANNER = """\
sibei-flow onboarding
─────────────────────
This records how to reach your brain plus a few READ-ONLY / PR-scoped secrets.
sibei-flow never asks for, stores, or uses a prod-write credential:
  • the git token needs read access to source + permission to OPEN PRs (not merge, not prod);
  • the warehouse connection is an OPTIONAL read-only dev/sample DSN (tier-2), never prod;
  • no LLM key is required — leaving it blank keeps the keyless `replay` provider.
Press Enter to accept the [default] shown for any prompt.
"""


def _ask(prompt: Prompt, label: str, default: str = "") -> str:
    """Ask a question, returning the trimmed answer or the default on empty."""
    suffix = f" [{default}]" if default else ""
    ans = prompt(f"{label}{suffix}: ").strip()
    return ans or default


def cmd_init(
    *,
    config_path: str | None = None,
    prompt: Prompt = input,
    out: Out = print,
    existing: CliConfig | None = None,
) -> int:
    """Run the onboarding flow and write the config. Returns an exit code."""
    out(_BANNER)
    base = existing or CliConfig.load(config_path)

    repo = _ask(
        prompt, "Repo (owner/name)", base.repo if base.repo != "unknown" else ""
    )
    webhook_url = _ask(prompt, "Brain webhook URL", base.webhook_url)
    adapter = _ask(
        prompt, "Warehouse adapter (postgres/snowflake/bigquery)", base.adapter
    )

    out("")
    out("Read-only source + PR-scoped git token (opens PRs; NOT prod-write).")
    out("Leave blank to configure later or supply via $SBFLOW_GIT_TOKEN.")
    git_token = _ask(prompt, "Git token", base.git_token)

    out("")
    out("Optional LLM key. Blank keeps the keyless `replay` provider (default).")
    llm_api_key = _ask(prompt, "LLM API key", base.llm_api_key)
    llm_provider = base.llm_provider
    if llm_api_key:
        llm_provider = _ask(prompt, "LLM provider (claude/openai)", "claude")
    else:
        llm_provider = "replay"

    out("")
    out("Optional READ-ONLY dev/sample warehouse DSN for tier-2 sample builds.")
    out("This is NEVER a prod-write connection. Blank => tier-1 compile only.")
    sample_warehouse_url = _ask(
        prompt, "Sample warehouse DSN", base.sample_warehouse_url
    )

    cfg = CliConfig(
        repo=repo or "unknown",
        webhook_url=webhook_url,
        adapter=adapter,
        llm_provider=llm_provider,
        git_token=git_token,
        llm_api_key=llm_api_key,
        sample_warehouse_url=sample_warehouse_url,
    )

    target = Path(config_path) if config_path else default_user_config_path()
    cfg.write(target)

    out("")
    out(f"Wrote config to {target} (chmod 600).")
    out(f"  repo         = {cfg.repo}")
    out(f"  webhook_url  = {cfg.webhook_url}")
    out(f"  adapter      = {cfg.adapter}")
    out(f"  llm_provider = {cfg.llm_provider}")
    out(f"  git token    = {'set' if cfg.git_token else 'not set'}")
    out(
        f"  sample DSN   = {'set (read-only dev/sample)' if cfg.sample_warehouse_url else 'not set'}"
    )
    out("")
    out("Next: enroll a pipeline (one line) — see snippets/ for the Airflow")
    out("callback and dbt hook, or wrap a cron step with `sbflow run -- <cmd>`.")
    return 0
