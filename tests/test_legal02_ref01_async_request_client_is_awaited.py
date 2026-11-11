"""LEGAL-02 / REF-01: handlers given the ASYNC request client never awaited its execute().

``get_request_supabase`` returns an async PostgREST client, so ``.execute()`` is a coroutine.
``routers/legal.py`` and two ``routers/referral.py`` handlers used it synchronously; reading
``.data`` off the coroutine raised, and the ``except`` turned that into an error answer:

* ``GET  /api/legal/acceptance`` -> 503 LEGAL_ACCEPTANCE_UNAVAILABLE on every page load;
* ``POST /api/legal/accept``     -> 503 LEGAL_ACCEPTANCE_NOT_RECORDED, so no signup since
  2026-10-23 had its acceptance (or its age / country declaration) stored, and the
  re-acceptance gate, which fails open on a read error, prompted nobody;
* ``GET  /api/referral/commissions`` and ``/payouts`` -> 500 for every user.

The existing tests used synchronous fakes, which is why none of this was seen. These drive the
handlers with an async fake shaped like the production client.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend_app.routers import legal, referral


class _AsyncQuery:
    """Chainable like postgrest's builder; ``execute()`` is a coroutine, as in production."""

    def __init__(self, rows):
        self._rows = rows

    def __getattr__(self, _name):
        return lambda *a, **k: self

    async def execute(self):
        return SimpleNamespace(data=self._rows)


class _AsyncClient:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _AsyncQuery(self._rows)


USER = {"id": "7169eb77-cccd-4f14-b3dc-5d13cfd386f4"}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_the_acceptance_read_answers_from_an_async_client() -> None:
    body = _run(legal.get_my_acceptance(user=USER, supabase=_AsyncClient([])))

    assert body["needs_acceptance"] is True
    assert body["accepted_version"] is None


def test_a_recorded_current_acceptance_reads_back_as_current() -> None:
    rows = [{"document_version": legal.LEGAL_VERSION, "documents": [], "accepted_at": "x",
             "context": "signup"}]

    body = _run(legal.get_my_acceptance(user=USER, supabase=_AsyncClient(rows)))

    assert body["accepted_version"] == legal.LEGAL_VERSION
    assert body["needs_acceptance"] is False


def test_the_acceptance_write_is_awaited_on_an_async_client() -> None:
    awaited = []

    class _Upsert(_AsyncQuery):
        async def execute(self):
            awaited.append(True)
            return SimpleNamespace(data=[])

    _run(legal._executed(_Upsert([])))

    assert awaited == [True], "an un-awaited upsert never reaches the database"


def test_a_sync_client_still_works() -> None:
    class _SyncQuery(_AsyncQuery):
        def execute(self):
            return SimpleNamespace(data=["row"])

    assert _run(legal._executed(_SyncQuery([]))).data == ["row"]


def test_commission_history_answers_from_an_async_client() -> None:
    result = _run(
        referral.get_commission_history(
            limit=50, offset=0, status=None, user=USER, supabase=_AsyncClient([])
        )
    )
    assert getattr(result, "commissions", result.get("commissions") if isinstance(result, dict) else None) == []


def test_payout_history_answers_from_an_async_client() -> None:
    result = _run(
        referral.get_payout_history(
            limit=50, offset=0, status=None, user=USER, supabase=_AsyncClient([])
        )
    )
    assert getattr(result, "payouts", result.get("payouts") if isinstance(result, dict) else None) == []


def test_the_accept_write_uses_the_service_role_not_the_callers_client() -> None:
    """LEGAL-03: migration 052 grants `authenticated` SELECT only on legal_acceptances, so a
    write over the caller's client is refused with 42501 (seen in production Sentry). The
    acceptance must be written over the service role, after validation, as designed."""
    import inspect as _inspect

    from backend_app.core.dependencies import get_service_supabase_async

    param = _inspect.signature(legal.accept_documents).parameters["supabase"]
    assert param.default.dependency is get_service_supabase_async
