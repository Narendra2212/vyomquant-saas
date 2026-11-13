"""LIVE-SBX-01: live mode can be opened for exchange TESTNET keys only.

Live mode is platform-wide (VYOMQUANT_MODE=live + VYOMQUANT_ENABLE_LIVE_TRADING + the tenant
allowlist), so certifying the live path against a testnet would also arm every REAL key an
allowlisted tenant holds. With VYOMQUANT_LIVE_SANDBOX_ONLY=true every venue boundary refuses
unless the client is VERIFIED (not merely requested) to address a sandbox endpoint.
Unset, behaviour is unchanged.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from backend_app.backend import exchange_executor, transactional_execution_manager
from backend_app.core import live_execution_gate as gate
from tests.test_trade04_live_order_gate_at_venue_boundary import harness, place  # noqa: F401

pytestmark = pytest.mark.usefixtures("live_platform_enabled")


def _verified(ok: bool):
    return SimpleNamespace(verified=ok, mechanism="url_swap" if ok else None, detail="t")


def test_unset_changes_nothing(monkeypatch):
    monkeypatch.delenv(gate.LIVE_SANDBOX_ONLY_ENV, raising=False)
    gate.assert_sandbox_only_policy(False)  # no raise


@pytest.mark.parametrize("value", ["true", "1", "YES", "on"])
def test_set_refuses_an_unverified_client(monkeypatch, value):
    monkeypatch.setenv(gate.LIVE_SANDBOX_ONLY_ENV, value)
    with pytest.raises(gate.LiveExecutionRefused) as exc:
        gate.assert_sandbox_only_policy(False, exchange_id="binance")
    assert exc.value.code == "LIVE_SANDBOX_ONLY"


def test_only_a_literal_true_counts_as_verified(monkeypatch):
    monkeypatch.setenv(gate.LIVE_SANDBOX_ONLY_ENV, "true")
    for truthy_but_not_true in (1, "true", object()):
        with pytest.raises(gate.LiveExecutionRefused):
            gate.assert_sandbox_only_policy(truthy_but_not_true)
    gate.assert_sandbox_only_policy(True)


def test_a_real_key_order_never_reaches_the_venue(harness, monkeypatch):  # noqa: F811
    engine, exchange, _ = harness
    monkeypatch.setenv(gate.LIVE_SANDBOX_ONLY_ENV, "true")
    engine.exchange_executor._sandbox_verification = _verified(False)
    place(engine)
    assert exchange.calls == [], "a non-sandbox client reached the venue in sandbox-only mode"


def test_a_verified_testnet_order_reaches_the_venue(harness, monkeypatch):  # noqa: F811
    engine, exchange, _ = harness
    monkeypatch.setenv(gate.LIVE_SANDBOX_ONLY_ENV, "true")
    engine.exchange_executor._sandbox_verification = _verified(True)
    place(engine)
    assert exchange.calls, "a verified sandbox client was refused in sandbox-only mode"


def test_a_requested_but_unverified_sandbox_is_refused(harness, monkeypatch):  # noqa: F811
    """EXCH-01: the REQUEST (self.sandbox) is not evidence; bitget proved it can be false."""
    engine, exchange, _ = harness
    monkeypatch.setenv(gate.LIVE_SANDBOX_ONLY_ENV, "true")
    engine.exchange_executor.sandbox = True
    engine.exchange_executor._sandbox_verification = None
    place(engine)
    assert exchange.calls == []


def _calls(module, name):
    return [n for n in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_every_venue_boundary_applies_the_policy():
    for module, expected in ((exchange_executor, 1), (transactional_execution_manager, 2)):
        assert len(_calls(module, "require_live_authorization")) == expected
        assert len(_calls(module, "assert_sandbox_only_policy")) == expected, module.__name__


# ── LIVE-01: a boundary refusal is pre-submission, never SUBMISSION_UNKNOWN ─────────────


def test_a_sandbox_only_refusal_is_a_clean_refusal_not_an_unknown_submission(harness, monkeypatch):  # noqa: F811
    engine, exchange, _ = harness
    monkeypatch.setenv(gate.LIVE_SANDBOX_ONLY_ENV, "true")
    result = place(engine)
    assert result["status"] == "failed"
    assert "SUBMISSION_UNKNOWN" not in str(result) and "No order was sent" in str(result)
    # Not frozen: a second order is evaluated afresh (and refused for the same reason).
    assert place(engine)["status"] == "failed"


def test_a_kill_switch_engaged_at_the_boundary_is_a_clean_refusal(harness, monkeypatch):  # noqa: F811
    """KILL-03 re-reads the switch inside place_order, BEFORE create_order."""
    engine, exchange, mp = harness
    import backend_app.core.global_safety as gs

    async def _halted():
        raise gs.ExecutionBlocked("kill switch engaged")

    mp.setattr(gs, "enforce_kill_switch", _halted)
    result = place(engine)
    assert exchange.calls == []
    assert result["status"] == "failed", result
    assert "SUBMISSION_UNKNOWN" not in str(result)
