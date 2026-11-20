"""RT-10 regression: router call sites must match APIKeyVault.load_decrypted_keys(user_id, exchange_id).

The open-orders endpoint passed an ``access_token=`` keyword the vault does not accept, so
GET /api/orders/open answered 500 for every user. The stub below has the REAL signature, so a
call with an extra keyword fails exactly as production did.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend_app.backend.api_key_vault import APIKeyVault
from backend_app.core.dependencies import get_current_user, get_vault
from backend_app.main import app

client = TestClient(app)


class StrictVault:
    def load_decrypted_keys(self, user_id, exchange_id):  # real signature
        return {"api_key": "k", "secret_key": "s", "password": None}


def test_stub_matches_the_real_vault_signature():
    real = list(inspect.signature(APIKeyVault.load_decrypted_keys).parameters)
    stub = list(inspect.signature(StrictVault.load_decrypted_keys).parameters)
    assert real == stub


def _override():
    app.dependency_overrides[get_current_user] = lambda: {"id": "u1", "email": "u@x.io", "access_token": "tok"}
    app.dependency_overrides[get_vault] = lambda: StrictVault()


def test_open_orders_does_not_pass_unsupported_kwargs_to_the_vault():
    _override()
    try:
        engine = MagicMock()
        engine.fetch_open_orders = AsyncMock(return_value=[{"id": "1"}])
        with patch("backend_app.routers.orders.get_or_create_exchange", AsyncMock(return_value=object())), \
             patch("backend_app.routers.orders.DataEngine", return_value=engine):
            res = client.get("/api/orders/open?exchange_id=binance", headers={"Authorization": "Bearer tok"})
        assert res.status_code == 200, res.text
        assert res.json() == [{"id": "1"}]
    finally:
        app.dependency_overrides.clear()


def test_no_router_passes_access_token_to_load_decrypted_keys():
    import pathlib, re
    offenders = []
    for p in pathlib.Path("backend_app").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"load_decrypted_keys\((?:[^()]|\([^()]*\))*\)", text):
            if "access_token" in m.group(0):
                offenders.append(str(p))
    assert not offenders, offenders
