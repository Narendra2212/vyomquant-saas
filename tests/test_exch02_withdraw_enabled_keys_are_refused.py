"""EXCH-02: keys that can withdraw funds are refused before storage; IP allowlist is reported."""
from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend_app.backend import exchange_key_permissions as kp
from backend_app.routers import exchange as exch


# ── parsers ────────────────────────────────────────────────────────────────
def test_binance_parser():
    r = kp.parse_binance({"enableWithdrawals": True, "ipRestrict": False})
    assert (r.verified, r.withdraw_enabled, r.ip_restricted) == (True, True, False)
    r = kp.parse_binance({"enableWithdrawals": False, "ipRestrict": True})
    assert (r.withdraw_enabled, r.ip_restricted) == (False, True)
    assert kp.parse_binance({"code": -2015}).verified is False


def test_okx_parser():
    assert kp.parse_okx({"data": [{"perm": "read_only,trade"}]}).withdraw_enabled is False
    assert kp.parse_okx({"data": [{"perm": "withdraw"}]}).withdraw_enabled is True
    assert kp.parse_okx({"data": [{"perm": "read_only,withdraw", "ip": "1.2.3.4"}]}).ip_restricted is True
    assert kp.parse_okx({}).verified is False


def test_bybit_parser():
    ok = kp.parse_bybit({"result": {"permissions": {"Wallet": ["AccountTransfer"]}, "ips": ["*"]}})
    assert (ok.withdraw_enabled, ok.ip_restricted) == (False, False)
    bad = kp.parse_bybit({"result": {"permissions": {"Wallet": ["AccountTransfer", "Withdraw"]}, "ips": ["1.1.1.1"]}})
    assert (bad.withdraw_enabled, bad.ip_restricted) == (True, True)
    assert kp.parse_bybit({"retCode": 10003}).verified is False


def test_inspect_never_raises_and_unknown_is_not_safe():
    ex = MagicMock(); ex.sapiGetAccountApiRestrictions = AsyncMock(side_effect=RuntimeError("boom"))
    r = asyncio.run(kp.inspect_key_permissions(ex, "binance"))
    assert r.verified is False and r.withdraw_enabled is None
    r2 = asyncio.run(kp.inspect_key_permissions(MagicMock(), "kraken"))  # no introspection implemented
    assert r2.verified is False


def test_strict_mode_refuses_unverifiable(monkeypatch):
    unknown = kp.KeyPermissionReport(False, None, None, "kraken")
    assert unknown.refuse_reason is None
    monkeypatch.setenv("EXCHANGE_KEY_PERMISSIONS_STRICT", "true")
    assert unknown.refuse_reason == "PERMISSIONS_UNVERIFIABLE"


# ── the real handler ───────────────────────────────────────────────────────
@contextlib.asynccontextmanager
async def _guard(*_a, **_k):
    yield


def _run(report, exchange_id="binance"):
    vault = MagicMock()
    body = SimpleNamespace(exchange_id=exchange_id, api_key="k", secret_key="s", password=None, uid=None, label=None)
    bridge = MagicMock(); bridge.connect = AsyncMock(return_value=MagicMock()); bridge.disconnect = AsyncMock()
    with patch.object(exch, "ConnectionEngine", return_value=bridge), \
         patch.object(exch, "DataEngine") as de, \
         patch.object(exch, "check_exchange_connection_slot", new=AsyncMock()), \
         patch.object(exch, "exchange_connection_slot_guard", new=_guard), \
         patch.object(exch, "inspect_key_permissions", new=AsyncMock(return_value=report)), \
         patch.object(exch, "release_exchange", new=AsyncMock()):
        de.return_value.fetch_wallet_balance_snapshot = AsyncMock()
        try:
            out = asyncio.run(exch.store_keys(body=body, user={"id": "u1"}, vault=vault,
                                              redis_manager=None, supabase=MagicMock()))
            return out, vault, None
        except HTTPException as e:
            return None, vault, e


def test_withdraw_enabled_key_is_refused_and_never_stored():
    out, vault, err = _run(kp.KeyPermissionReport(True, True, True, "binance"))
    assert err is not None and err.status_code == 400
    assert err.detail["error"] == exch.EXCHANGE_KEY_WITHDRAW_ENABLED
    vault.store_exchange_keys.assert_not_called()


def test_trade_only_key_is_stored_and_ip_warning_reported():
    out, vault, err = _run(kp.KeyPermissionReport(True, False, False, "binance"))
    assert err is None and out["status"] == "ok"
    assert "ip_allowlist_not_enabled" in out["key_warnings"]
    vault.store_exchange_keys.assert_called_once()


def test_unverified_key_stored_with_warning_by_default_refused_in_strict(monkeypatch):
    unknown = kp.KeyPermissionReport(False, None, None, "kraken")
    out, vault, err = _run(unknown, "kraken")
    assert err is None and "withdrawal_permission_unverified" in out["key_warnings"]
    monkeypatch.setenv("EXCHANGE_KEY_PERMISSIONS_STRICT", "1")
    out, vault, err = _run(unknown, "kraken")
    assert err is not None and err.detail["error"] == exch.EXCHANGE_KEY_PERMISSIONS_UNVERIFIABLE
    vault.store_exchange_keys.assert_not_called()
