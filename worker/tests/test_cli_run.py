"""Fast/no-infra tests for `sbflow run` — the cron-wrapper detector (V5, R1.4).

No DB / warehouse / Docker: the webhook POST is stubbed via monkeypatch, and the
wrapped command is a trivial local subprocess. Runs in the `make test-fast` lane.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import sbflow_worker.cli as cli
import sbflow_worker.cli.run as run_mod
from sbflow_worker.cli.config import CliConfig

FROZEN_KEYS = {
    "repo",
    "run_id",
    "task_id",
    "node_uid",
    "error_text",
    "adapter",
    "source",
}


def _capture(monkeypatch):
    """Stub the webhook POST; return a list that captures (url, payload)."""
    calls: list[tuple[str, dict]] = []

    def fake_post(url, payload, timeout=5.0):
        calls.append((url, payload))

    monkeypatch.setattr(run_mod, "post_failure", fake_post)
    return calls


def test_passing_command_posts_nothing(monkeypatch):
    calls = _capture(monkeypatch)
    rc = cli.main(
        ["run", "--config", "/dev/null", "--", sys.executable, "-c", "exit(0)"]
    )
    assert rc == 0
    assert calls == []


def test_failing_command_posts_wellformed_failure_and_propagates_rc(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setenv("SBFLOW_WEBHOOK_URL", "http://brain/webhook")
    monkeypatch.setenv("SBFLOW_REPO", "demo/analytics")
    monkeypatch.setenv("SBFLOW_ADAPTER", "postgres")
    rc = cli.main(
        [
            "run",
            "--config",
            "/dev/null",
            "--task",
            "nightly_build",
            "--",
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('kaboom\\n'); sys.exit(7)",
        ]
    )
    assert rc == 7  # command's own exit code is passed through
    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "http://brain/webhook"
    assert FROZEN_KEYS <= set(payload)  # exact frozen contract keys present
    assert payload["source"] == "cli"
    assert payload["repo"] == "demo/analytics"
    assert payload["task_id"] == "nightly_build"
    assert payload["node_uid"] == "nightly_build"  # falls back to task_id
    assert "kaboom" in payload["error_text"]
    assert "run_results_ref" not in payload  # none present -> omitted


def test_dbt_run_results_enriches_payload(monkeypatch, tmp_path: Path):
    calls = _capture(monkeypatch)
    rr = tmp_path / "run_results.json"
    rr.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "status": "success",
                        "unique_id": "model.analytics.stg",
                        "message": None,
                    },
                    {
                        "status": "error",
                        "unique_id": "model.analytics.orders",
                        "message": 'column "customer_id" does not exist',
                    },
                ]
            }
        )
    )
    cfg = CliConfig(
        repo="acme/analytics", webhook_url="http://brain/webhook", adapter="postgres"
    )

    class Args:
        task = None
        run_id = "run-42"
        run_results = str(rr)
        repo = adapter = webhook_url = None

    rc = run_mod.cmd_run([sys.executable, "-c", "exit(1)"], cfg, Args())
    assert rc == 1
    assert len(calls) == 1
    _, payload = calls[0]
    assert payload["node_uid"] == "model.analytics.orders"  # lifted from run_results
    assert "customer_id" in payload["error_text"]
    assert payload["run_results_ref"] == str(rr)
    assert payload["run_id"] == "run-42"


def test_webhook_failure_does_not_mask_command_rc(monkeypatch):
    def boom(url, payload, timeout=5.0):
        raise OSError("connection refused")

    monkeypatch.setattr(run_mod, "post_failure", boom)
    cfg = CliConfig(repo="demo/x", webhook_url="http://unreachable/webhook")

    class Args:
        task = run_id = run_results = repo = adapter = webhook_url = None

    rc = run_mod.cmd_run([sys.executable, "-c", "exit(5)"], cfg, Args())
    assert rc == 5  # webhook transport error is swallowed; command rc survives


def test_no_command_is_usage_error():
    cfg = CliConfig()

    class Args:
        task = run_id = run_results = repo = adapter = webhook_url = None

    assert run_mod.cmd_run([], cfg, Args()) == 2
