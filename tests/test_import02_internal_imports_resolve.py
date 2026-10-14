"""IMPORT-02 regression + guard.

1. ``portfolio_cache_updater`` imported ``KeyVault`` from a module that does not exist
   (``backend_app.backend.key_manager``), so the live-balance cache could never populate.
2. A repo-wide AST guard: every ``from backend_app.x import ...`` must name a module file
   that exists. Two older offenders are listed EXPLICITLY (IMPORT-01, IMPORT-03) so they stay
   visible; any NEW broken import fails this test.
"""
import ast
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]

# Known, tracked offenders - do not add to this set; fix the import instead.
KNOWN_BROKEN = {
    ("backend_app/backend/position_engine.py", "backend_app.core.models.trade_history"),  # IMPORT-03
    ("backend_app/tee/main.py", "backend_app.core.fleet_manager"),                          # IMPORT-01
}


def _module_exists(mod: str) -> bool:
    base = ROOT.joinpath(*mod.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def test_no_new_broken_backend_app_imports():
    broken = set()
    for p in (ROOT / "backend_app").rglob("*.py"):
        if "node_modules" in p.parts or ".git" in p.parts:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend_app"):
                if node.level == 0 and not _module_exists(node.module):
                    # `from backend_app.pkg import submodule` is fine when pkg/submodule exists
                    parent_ok = _module_exists(node.module)
                    if not parent_ok:
                        broken.add((p.relative_to(ROOT).as_posix(), node.module))
    unexpected = broken - KNOWN_BROKEN
    assert not unexpected, f"new imports of modules that do not exist: {sorted(unexpected)}"


def test_portfolio_cache_updater_uses_the_real_vault_class(monkeypatch):
    import backend_app.backend.api_key_vault as akv
    import backend_app.backend.connection_engine as ce
    from backend_app.backend.portfolio_cache_updater import PortfolioCacheUpdater

    class FakeVault:
        def load_decrypted_keys(self, user_id, exchange_id):
            return {"api_key": "k", "secret_key": "s", "password": None}

    class FakeExchange:
        async def fetch_balance(self):
            return {"free": {"USDT": 5}, "total": {"USDT": 10}}

        async def fetch_positions(self):
            return []

    monkeypatch.setattr(akv, "APIKeyVault", FakeVault)
    monkeypatch.setattr(ce, "get_or_create_exchange", AsyncMock(return_value=FakeExchange()))

    pipe = MagicMock()
    redis_client = MagicMock()
    redis_client.pipeline.return_value = pipe
    pipe.execute = AsyncMock()

    upd = PortfolioCacheUpdater.__new__(PortfolioCacheUpdater)
    upd.ttl = 30
    # Must not raise ModuleNotFoundError / ImportError (the IMPORT-02 defect).
    asyncio.run(upd._update_user_portfolio(redis_client, "u1", "binance"))
    pipe.hset.assert_called_once()
