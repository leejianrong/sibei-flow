"""Unit tests for the working copy + diff guard (N9 / B-S5). No DB required."""

from __future__ import annotations

from sbflow_worker.agent.diffguard import DiffGuard
from sbflow_worker.agent.diffing import WorkingCopy

MODEL = "models/marts/orders.sql"
ORIG = "select\n    customer_id,\n    order_ts,\n    amount\nfrom customers\n"


def _wc():
    wc = WorkingCopy()
    wc.load(MODEL, ORIG)
    return wc


def test_targeted_replace_single_occurrence():
    wc = _wc()
    ok, _ = wc.apply_replace(MODEL, "customer_id,", "cust_id,")
    assert ok
    assert "cust_id," in wc.current(MODEL)
    assert "customer_id," not in wc.current(MODEL)


def test_replace_rejects_missing_string():
    wc = _wc()
    ok, msg = wc.apply_replace(MODEL, "nonexistent", "x")
    assert not ok and "not found" in msg


def test_replace_rejects_ambiguous_string():
    wc = WorkingCopy()
    wc.load(MODEL, "a\na\n")
    ok, msg = wc.apply_replace(MODEL, "a", "b")
    assert not ok and "ambiguous" in msg


def test_guard_accepts_minimal_in_scope_edit():
    wc = _wc()
    wc.apply_replace(MODEL, "customer_id,", "cust_id,")
    ok, _ = DiffGuard(max_lines=40).check(MODEL, wc, {MODEL})
    assert ok


def test_guard_rejects_out_of_scope_file():
    wc = WorkingCopy()
    wc.load("models/other.sql", "select 1\n")
    wc.apply_replace("models/other.sql", "1", "2")
    ok, msg = DiffGuard().check("models/other.sql", wc, {MODEL})
    assert not ok and "out of scope" in msg


def test_guard_rejects_oversized_diff():
    wc = _wc()
    # Rewrite the whole file with many new lines.
    wc.set_current(MODEL, "\n".join(f"line {i}" for i in range(100)) + "\n")
    ok, msg = DiffGuard(max_lines=10).check(MODEL, wc, {MODEL})
    assert not ok and "too large" in msg


def test_guard_rejects_whitespace_only_churn():
    wc = _wc()
    # Same tokens, different indentation only.
    wc.set_current(MODEL, ORIG.replace("    ", "        "))
    ok, msg = DiffGuard().check(MODEL, wc, {MODEL})
    assert not ok and "whitespace-only" in msg
