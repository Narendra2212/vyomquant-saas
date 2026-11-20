"""EXCH-STATE-01 — a credential's verified state is recorded, not thrown away.

Production report: a Bybit key saved successfully, and its card read "Connection not
reported". The save had connected to Bybit, read the balance and introspected the key's
permissions before storing it -- a real verdict -- and then discarded it. Migration 056 added
``validation_status`` / ``last_validated_at`` / ``can_trade`` to ``exchange_keys`` and nothing
wrote them, so no stored key could ever report its state.

These tests pin:
  * the permission parsers report the venue's own trade flag (and None when it is silent);
  * the save writes the verdict in the SAME upsert as the ciphertext;
  * ``/test-stored`` probes the environment the key was stored for, records ``verified`` on
    success, ``invalid`` only when the venue rejects the key, and nothing on an outage.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

fastapi = pytest.importorskip("fastapi")
ccxt = pytest.importorskip("ccxt")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import backend_app.routers.exchange as exchange_module  # noqa: E402
from backend_app.backend.api_key_vault import (  # noqa: E402
    VALIDATION_INVALID,
    VALIDATION_VERIFIED,
    build_validation_record,
)
from backend_app.backend.exchange_key_permissions import (  # noqa: E402
    parse_binance,
    parse_bybit,
    parse_okx,
)
from backend_app.core.dependencies import get_current_user  # noqa: E402


# ── 1. the venue's own trade flag ────────────────────────────────────────────────────────


def _bybit(read_only: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {"permissions": {"Wallet": ["AccountTransfer"]}, "ips": ["*"]}
    if read_only is not None:
        result["readOnly"] = read_only
    return {"result": result}


def test_bybit_read_write_key_can_trade():
    assert parse_bybit(_bybit(0)).trade_enabled is True


def test_bybit_read_only_key_cannot_trade():
    assert parse_bybit(_bybit(1)).trade_enabled is False


def test_bybit_silent_about_read_only_is_not_reported():
    assert parse_bybit(_bybit(None)).trade_enabled is None


def test_okx_trade_permission_is_read_from_perm():
    assert parse_okx({"data": [{"perm": "read_only,trade"}]}).trade_enabled is True
    assert parse_okx({"data": [{"perm": "read_only"}]}).trade_enabled is False


def test_binance_trade_flag_or_none():
    assert parse_binance({"enableWithdrawals": False, "enableSpotAndMarginTrading": True}).trade_enabled is True
    assert parse_binance({"enableWithdrawals": False}).trade_enabled is None


# ── 2. the record itself ─────────────────────────────────────────────────────────────────


def test_a_verified_record_carries_the_reported_trade_flag_and_a_timestamp():
    rec = build_validation_record(VALIDATION_VERIFIED, None)
    assert rec["validation_status"] == "verified"
    assert rec["can_trade"] is None, "a silent venue must not become a guess"
    assert rec["last_validated_at"]


def test_an_invalid_record_can_never_trade():
    assert build_validation_record(VALIDATION_INVALID, True)["can_trade"] is False


def test_unknown_statuses_are_refused():
    with pytest.raises(ValueError):
        build_validation_record("connected")


# ── 3. /test-stored records the verdict ──────────────────────────────────────────────────


class _Vault:
    def __init__(self, sandbox: bool = False):
        self.sandbox = sandbox
        self.recorded: List[Dict[str, Any]] = []

    def load_decrypted_keys(self, user_id, exchange_id):
        return {"api_key": "k", "secret_key": "s", "password": None, "sandbox": self.sandbox}

    def record_validation(self, user_id, exchange_id, record):
        self.recorded.append(record)
        return True


def _engine(connect_error: BaseException | None, permissions: Dict[str, Any] | None = None):
    calls: List[Dict[str, Any]] = []

    class _Exchange:
        async def privateGetV5UserQueryApi(self):
            return permissions if permissions is not None else _bybit(0)

    class _Engine:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def connect(self):
            if connect_error is not None:
                raise connect_error
            return _Exchange()

        async def check_exchange_status(self):
            return "ok"

        async def disconnect(self):
            pass

    return _Engine, calls


class _Data:
    def __init__(self, exchange):
        pass

    async def fetch_wallet_balance_snapshot(self):
        return {"USDT": {"total": 10}}


def _client(monkeypatch, vault, connect_error=None, permissions=None):
    engine, calls = _engine(connect_error, permissions)
    monkeypatch.setattr(exchange_module, "ConnectionEngine", engine)
    monkeypatch.setattr(exchange_module, "DataEngine", _Data)
    app = FastAPI()
    app.add_api_route("/test-stored", exchange_module.test_stored_connection, methods=["POST"])
    app.dependency_overrides[get_current_user] = lambda: {"id": "user-state01"}
    app.dependency_overrides[exchange_module.get_vault] = lambda: vault
    return TestClient(app), calls


def test_a_successful_probe_records_verified_with_the_trade_flag(monkeypatch):
    vault = _Vault()
    client, _ = _client(monkeypatch, vault)
    res = client.post("/test-stored", json={"exchange_id": "bybit"})

    assert res.status_code == 200, res.text
    assert [r["validation_status"] for r in vault.recorded] == ["verified"]
    assert vault.recorded[0]["can_trade"] is True


def test_the_probe_targets_the_environment_the_key_was_stored_for(monkeypatch):
    vault = _Vault(sandbox=True)
    client, calls = _client(monkeypatch, vault)
    client.post("/test-stored", json={"exchange_id": "bybit"})
    assert calls[-1].get("testnet") is True


def test_a_venue_rejection_records_invalid(monkeypatch):
    vault = _Vault()
    client, _ = _client(monkeypatch, vault, ccxt.AuthenticationError("bad key"))
    res = client.post("/test-stored", json={"exchange_id": "bybit"})

    assert res.status_code >= 400
    assert [r["validation_status"] for r in vault.recorded] == ["invalid"]


def test_an_outage_records_nothing(monkeypatch):
    vault = _Vault()
    client, _ = _client(monkeypatch, vault, ccxt.NetworkError("timeout"))
    client.post("/test-stored", json={"exchange_id": "bybit"})
    assert vault.recorded == [], "a timeout says nothing about the key and must not mark it invalid"


def test_a_vault_that_cannot_record_does_not_fail_the_probe(monkeypatch):
    vault = _Vault()

    def _boom(*a, **k):
        raise RuntimeError("write failed")

    vault.record_validation = _boom
    client, _ = _client(monkeypatch, vault)
    assert client.post("/test-stored", json={"exchange_id": "bybit"}).status_code == 200


# ── 4. the save writes the verdict in the same upsert ────────────────────────────────────


def test_store_exchange_keys_writes_the_verdict_with_the_ciphertext():
    from backend_app.backend.api_key_vault import APIKeyVault

    upserts: List[Dict[str, Any]] = []

    class _Table:
        def upsert(self, data):
            upserts.append(data)
            return SimpleNamespace(execute=lambda: None)

    vault = APIKeyVault.__new__(APIKeyVault)
    vault.supabase = SimpleNamespace(table=lambda name: _Table())
    vault._encrypt = lambda v: f"enc({v})"

    vault.store_exchange_keys(
        user_id="user-state01", exchange_id="bybit", raw_api_key="k", raw_secret="s",
        validation=build_validation_record(VALIDATION_VERIFIED, True),
    )
    (row,) = upserts
    assert row["validation_status"] == "verified"
    assert row["can_trade"] is True
    assert row["last_validated_at"]
    assert row["encrypted_api_key"] == "enc(k)"


def test_store_without_a_verdict_leaves_the_stored_verdict_alone():
    from backend_app.backend.api_key_vault import APIKeyVault

    upserts: List[Dict[str, Any]] = []

    class _Table:
        def upsert(self, data):
            upserts.append(data)
            return SimpleNamespace(execute=lambda: None)

    vault = APIKeyVault.__new__(APIKeyVault)
    vault.supabase = SimpleNamespace(table=lambda name: _Table())
    vault._encrypt = lambda v: f"enc({v})"
    vault.store_exchange_keys(user_id="u1", exchange_id="bybit", raw_api_key="k", raw_secret="s")
    assert "validation_status" not in upserts[0]


# ── 5. EXCH-STATE-02: withdrawal permission is re-checked, and blocks live trading ───────


def test_a_stored_key_that_can_now_withdraw_is_recorded_and_refused(monkeypatch):
    vault = _Vault()
    withdraw = {"result": {"permissions": {"Wallet": ["AccountTransfer", "Withdraw"]},
                           "readOnly": 0, "ips": ["*"]}}
    client, _ = _client(monkeypatch, vault, permissions=withdraw)
    res = client.post("/test-stored", json={"exchange_id": "bybit"})

    assert res.status_code == 400, res.text
    assert res.json()["detail"]["error"] == exchange_module.EXCHANGE_KEY_WITHDRAW_ENABLED
    assert [r["validation_status"] for r in vault.recorded] == ["withdraw_enabled"]
    assert vault.recorded[0]["can_trade"] is False


from backend_app.backend.api_key_vault import (  # noqa: E402
    VALIDATION_WITHDRAW_ENABLED,
    credential_trade_block_reason,
)


@pytest.mark.parametrize(
    "keys",
    [
        {"validation_status": None, "can_trade": None},
        {"validation_status": "invalid", "can_trade": False},
        {"validation_status": VALIDATION_WITHDRAW_ENABLED, "can_trade": False},
        {"validation_status": "verified", "can_trade": False},
        {"validation_status": "unreadable"},
        None,
    ],
)
def test_live_trading_is_refused_without_an_affirmative_verdict(keys):
    assert credential_trade_block_reason(keys)


@pytest.mark.parametrize("can_trade", [True, None])
def test_a_verified_key_may_trade(can_trade):
    assert credential_trade_block_reason({"validation_status": "verified", "can_trade": can_trade}) is None


def test_a_withdraw_enabled_record_can_never_trade():
    assert build_validation_record(VALIDATION_WITHDRAW_ENABLED, True)["can_trade"] is False


def test_the_live_bot_start_consults_the_verdict_before_connecting():
    """master_executor's live branch must refuse an untradeable key BEFORE ConnectionEngine is
    built, and must route by the stored key's environment, not the blueprint's."""
    import inspect

    import backend_app.backend.master_executor as me

    src = inspect.getsource(me)
    live = src[src.index("# LIVE path"):]
    assert live.index("credential_trade_block_reason(keys)") < live.index("ConnectionEngine("), (
        "the verdict must be checked before the live connection is opened"
    )
    assert "testnet=key_sandbox" in live
    assert "sandbox=key_sandbox" in live
    assert 'sandbox=self.blueprint.get("testnet", False)' not in live
