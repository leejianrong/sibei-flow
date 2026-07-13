"""``sbflow`` — the onboarding + cron-wrapper CLI (V5, U1/U2).

Subcommands:

* ``sbflow init``            — interactive first-run onboarding; writes config.
* ``sbflow run -- <cmd>``    — run a command; on non-zero exit POST a Failure.

Console-script entry point (``[project.scripts] sbflow``) lives in
``worker/pyproject.toml``. The CLI is pure-stdlib (argparse + urllib + tomllib +
subprocess) so it imports and runs without the worker's DB/LLM/Docker stack.
"""

from __future__ import annotations

import argparse
import sys

from .config import CliConfig
from .init import cmd_init
from .run import cmd_run


def _split_run_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split ``argv`` at the first ``--`` into (cli_args, passthrough_command)."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sbflow",
        description="sibei-flow onboarding + cron-wrapper CLI.",
    )
    p.add_argument("--config", help="path to the config file (overrides discovery)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="interactive first-run onboarding")
    p_init.set_defaults(func="init")

    p_run = sub.add_parser(
        "run",
        help="run a command; on non-zero exit report a Failure (use: run -- <cmd>)",
    )
    p_run.add_argument("--repo", help="override configured repo for this run")
    p_run.add_argument("--adapter", help="override configured adapter for this run")
    p_run.add_argument("--webhook-url", dest="webhook_url", help="override webhook URL")
    p_run.add_argument(
        "--task", help="task_id label for the Failure (default: cmd name)"
    )
    p_run.add_argument("--run-id", dest="run_id", help="run_id for the Failure")
    p_run.add_argument(
        "--run-results",
        dest="run_results",
        help="path to a dbt run_results.json to enrich the payload from",
    )
    p_run.set_defaults(func="run")
    return p


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    cli_args, command = _split_run_command(raw)

    parser = build_parser()
    args = parser.parse_args(cli_args)

    if args.func == "init":
        return cmd_init(config_path=args.config)

    if args.func == "run":
        cfg = CliConfig.load(args.config)
        if args.repo:
            cfg.repo = args.repo
        if args.adapter:
            cfg.adapter = args.adapter
        if args.webhook_url:
            cfg.webhook_url = args.webhook_url
        return cmd_run(command, cfg, args)

    parser.error("unknown command")
    return 2  # unreachable


if __name__ == "__main__":
    raise SystemExit(main())
