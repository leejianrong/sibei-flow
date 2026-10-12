"""``python -m sbflow_worker.evalkit`` — run the eval corpus and print a report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..config import Config
from .case import CaseError, discover
from .report import render_text, summarize
from .runner import RunEnv, run_case

# Matches scripts/test setups: the fixture pipeline owner, used only to build a
# case's upstream schema (never a sibei-flow credential).
_DEFAULT_ADMIN = "postgres://sibei:sibei@warehouse:5432/warehouse"


def _default_cases_dir() -> str:
    # <repo>/worker/sbflow_worker/evalkit/__main__.py -> <repo>/evals/cases
    return str(Path(__file__).resolve().parents[3] / "evals" / "cases")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sbflow_worker.evalkit", description=__doc__)
    ap.add_argument(
        "--cases", default=os.environ.get("EVAL_CASES", _default_cases_dir())
    )
    ap.add_argument(
        "--only", action="append", default=[], help="run only this case id (repeatable)"
    )
    ap.add_argument(
        "--out", default=os.environ.get("EVAL_OUT"), help="write the JSON report here"
    )
    ap.add_argument(
        "--min-pass-rate",
        type=float,
        default=None,
        help="exit non-zero when the pass rate falls below this (0-1); for CI smoke runs",
    )
    args = ap.parse_args(argv)

    try:
        cases = discover(args.cases, set(args.only) or None)
    except CaseError as e:
        print(f"eval: {e}", file=sys.stderr)
        return 2
    if not cases:
        print(f"eval: no cases found under {args.cases}", file=sys.stderr)
        return 2

    cfg = Config.from_env()
    env = RunEnv(
        warehouse_admin_url=os.environ.get("WAREHOUSE_ADMIN_URL", _DEFAULT_ADMIN),
        brain_url=os.environ.get("SBFLOW_BRAIN_URL") or None,
        brain_token=os.environ.get("SBFLOW_DASHBOARD_TOKEN", "sbflow-dev-token"),
        webhook_secret=os.environ.get("SBFLOW_WEBHOOK_SECRET") or None,
    )

    results = []
    for case in cases:
        print(f"[eval] {case.id} …", flush=True)
        results.append(run_case(case, cfg, env))

    meta = {
        "provider": cfg.llm_provider,
        "model": cfg.llm_model if cfg.llm_provider != "replay" else "",
        "satay_loop": cfg.satay_loop_enabled,
        "satay_candidates": cfg.satay_candidates if cfg.satay_loop_enabled else "",
        "commit": os.environ.get("EVAL_COMMIT", ""),
    }
    summary = summarize(results, meta)
    print()
    print(render_text(summary))
    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2) + "\n")
        print(f"\nreport written to {args.out}")

    if args.min_pass_rate is not None:
        rate = summary["pass_rate"] or 0.0
        if rate < args.min_pass_rate:
            print(
                f"eval: pass rate {rate:.2f} < required {args.min_pass_rate:.2f}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
