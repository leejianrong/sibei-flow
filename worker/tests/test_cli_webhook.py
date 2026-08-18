"""Fast/no-infra tests for the CLI's webhook signing (KAN-929).

Verifies ``post_failure`` matches the brain's HMAC contract
(``brain/src/webhook.rs::verify_signature``, KAN-926) byte-for-byte:
HMAC-SHA256 keyed by the shared secret over the exact bytes
``f"{timestamp}.{raw_body}"`` (raw body bytes, not a re-encoded copy),
hex-encoded, sent as ``X-Sbflow-Signature: sha256=<hex>`` plus
``X-Sbflow-Timestamp: <timestamp>``. Also covers ``CliConfig`` picking up
``SBFLOW_WEBHOOK_SECRET`` from env and from a ``[secrets]`` TOML block.

No network: ``urllib.request.urlopen`` is monkeypatched to capture the built
``Request`` instead of sending it. Runs in the `make test-fast` lane.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
from pathlib import Path

from sbflow_worker.cli.config import CliConfig
from sbflow_worker.cli.webhook import build_failure, post_failure


def _header(req: urllib.request.Request, name: str) -> str | None:
    """Look up a header on a built ``Request``.

    ``Request.add_header`` stores keys via ``str.capitalize()`` (first char
    upper, rest lower) regardless of the case passed in, and
    ``Request.get_header`` does a raw dict lookup (no normalization) — so the
    caller must normalize the same way to find it again.
    """
    return req.headers.get(name.capitalize())


def _capture_request(monkeypatch) -> list[urllib.request.Request]:
    captured: list[urllib.request.Request] = []

    def fake_urlopen(req, timeout=5.0):
        captured.append(req)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def _payload() -> dict:
    return build_failure(
        repo="acme/analytics",
        run_id="manual__2026-07-09",
        task_id="build_orders",
        node_uid="model.analytics.orders",
        error_text='column "customer_id" does not exist',
        adapter="postgres",
        source="cli",
    )


def test_post_failure_with_secret_signs_matching_brain_contract(monkeypatch):
    captured = _capture_request(monkeypatch)
    secret = "test-shared-secret-do-not-use-in-prod"
    payload = _payload()

    post_failure("http://brain/webhook", payload, secret=secret)

    assert len(captured) == 1
    req = captured[0]
    raw_body = req.data
    assert raw_body == json.dumps(payload).encode()

    ts_header = _header(req, "X-Sbflow-Timestamp")
    sig_header = _header(req, "X-Sbflow-Signature")
    assert ts_header is not None
    assert sig_header is not None
    assert ts_header.isdigit()

    # Independently compute the expected HMAC over `{timestamp}.{raw_body}`,
    # matching brain/src/webhook.rs::verify_signature's exact byte contract
    # (and its test helper `sign()` in brain/tests/webhook_auth.rs).
    message = f"{ts_header}.".encode() + raw_body
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    assert sig_header == f"sha256={expected}"


def test_post_failure_without_secret_sends_unsigned(monkeypatch):
    """No secret => exactly today's request shape: no new headers at all."""
    captured = _capture_request(monkeypatch)
    payload = _payload()

    post_failure("http://brain/webhook", payload)

    assert len(captured) == 1
    req = captured[0]
    assert req.data == json.dumps(payload).encode()
    assert req.get_method() == "POST"
    assert _header(req, "X-Sbflow-Signature") is None
    assert _header(req, "X-Sbflow-Timestamp") is None
    assert _header(req, "Content-Type") == "application/json"
    # Exactly the headers sent before this change — nothing extra.
    assert set(req.headers) == {"Content-type"}


def test_post_failure_empty_secret_is_treated_as_unset(monkeypatch):
    captured = _capture_request(monkeypatch)
    post_failure("http://brain/webhook", _payload(), secret="")
    req = captured[0]
    assert _header(req, "X-Sbflow-Signature") is None
    assert _header(req, "X-Sbflow-Timestamp") is None


def test_cliconfig_loads_webhook_secret_from_env(monkeypatch):
    monkeypatch.setenv("SBFLOW_WEBHOOK_SECRET", "from-env-secret")
    cfg = CliConfig.load("/dev/null")  # no config file (matches test_cli_run.py)
    assert cfg.webhook_secret == "from-env-secret"


def test_cliconfig_loads_webhook_secret_from_toml(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SBFLOW_WEBHOOK_SECRET", raising=False)
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        'repo = "acme/analytics"\n'
        'webhook_url = "http://brain:8080/webhook"\n'
        'adapter = "postgres"\n'
        'llm_provider = "replay"\n'
        "\n"
        "[secrets]\n"
        'webhook_secret = "from-toml-secret"\n'
    )
    cfg = CliConfig.load(str(cfg_path))
    assert cfg.webhook_secret == "from-toml-secret"


def test_cliconfig_env_overrides_toml_webhook_secret(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('[secrets]\nwebhook_secret = "from-toml-secret"\n')
    monkeypatch.setenv("SBFLOW_WEBHOOK_SECRET", "from-env-secret")
    cfg = CliConfig.load(str(cfg_path))
    assert cfg.webhook_secret == "from-env-secret"


def test_cliconfig_default_webhook_secret_is_empty(monkeypatch):
    monkeypatch.delenv("SBFLOW_WEBHOOK_SECRET", raising=False)
    cfg = CliConfig.load("/dev/null")
    assert cfg.webhook_secret == ""


def test_cliconfig_to_toml_roundtrips_webhook_secret(tmp_path: Path, monkeypatch):
    """``write`` -> ``load`` roundtrip keeps the secret out of the top-level
    table (mirrors the ``git_token`` treatment) and preserves the value."""
    monkeypatch.delenv("SBFLOW_WEBHOOK_SECRET", raising=False)
    cfg = CliConfig(repo="acme/analytics", webhook_secret="roundtrip-secret")
    cfg_path = tmp_path / "config.toml"
    cfg.write(cfg_path)

    text = cfg_path.read_text()
    assert "[secrets]" in text
    assert "roundtrip-secret" in text

    loaded = CliConfig.load(str(cfg_path))
    assert loaded.webhook_secret == "roundtrip-secret"
