"""QUOTA-LEDGER-01: metered-quota reservations reach the durable ledger.

Found 2026-11-20 in the final launch certification: ``plan_usage_ledger`` held ZERO rows in
production although the QA account's Redis meter read 10/10 backtests. Only the service role
may write the table, but the request path passed the caller's user-scoped client, so every
append was refused and swallowed at debug level -- each metered quota lived only in Redis,
and a Redis flush or eviction would hand every account a fresh month.
"""

from __future__ import annotations

import asyncio

from backend_app.core import usage_ledger


class _Recorder:
    def __init__(self, name):
        self.name, self.inserts, self.deletes = name, [], []

    def table(self, _name):
        return _Query(self)


class _Query:
    def __init__(self, rec):
        self.rec = rec

    def insert(self, row):
        self.rec.inserts.append(row)
        return self

    def select(self, *_a):
        return self

    def delete(self):
        self.rec.deletes.append(True)
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        return type("R", (), {"data": [{"amount": 3}], "error": None})()


def test_append_and_read_use_the_service_client(monkeypatch):
    user_client, service = _Recorder("user"), _Recorder("service")
    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "get_service_supabase", lambda: service)
    asyncio.run(usage_ledger._append_ledger("backtests", "u1", user_client, "2026-11", 1, "k1"))
    total = asyncio.run(usage_ledger._ledger_total("backtests", "u1", user_client, "2026-11"))
    assert service.inserts and not user_client.inserts, "the ledger row went to the user client"
    assert total == 3


def test_falls_back_to_the_given_client_without_service_credentials(monkeypatch):
    user_client = _Recorder("user")
    import backend_app.core.dependencies as deps

    def _boom():
        raise RuntimeError("no service key")

    monkeypatch.setattr(deps, "get_service_supabase", _boom)
    asyncio.run(usage_ledger._append_ledger("backtests", "u1", user_client, "2026-11", 1, "k2"))
    assert user_client.inserts
