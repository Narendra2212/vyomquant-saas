"""BOT-01: a deployment bot's signals must reach public.signals.

Production deployment 8ac2aa8f (2026-11-11) executed a paper BUY; SignalService logged
"Created signal 4fc02f61" but no row exists: the bot passed ``{"id": user_id}`` with no token,
got a token-less client, RLS refused the INSERT and the error was swallowed at DEBUG.
"""

from __future__ import annotations

import asyncio
import inspect

from backend_app.backend import master_executor
from backend_app.backend import signal_service as ss


def test_a_server_side_caller_gets_the_service_role_client(monkeypatch) -> None:
    sentinel = object()

    async def _service():
        return sentinel

    import backend_app.core.dependencies as deps

    monkeypatch.setattr(deps, "get_service_supabase_async", _service)
    svc = ss.SignalService.__new__(ss.SignalService)
    got = asyncio.run(svc._get_supabase({"id": "u", "_server_side": True}))
    assert got is sentinel


def test_a_tokenless_request_caller_is_not_widened(monkeypatch) -> None:
    called = []
    import backend_app.core.dependencies as deps

    async def _service():
        called.append(1)

    monkeypatch.setattr(deps, "get_service_supabase_async", _service)
    monkeypatch.setattr(ss, "create_request_supabase_async", lambda token: ("request", token))
    svc = ss.SignalService.__new__(ss.SignalService)
    assert asyncio.run(svc._get_supabase({"id": "u"})) == ("request", None)
    assert not called


def test_every_bot_signal_write_is_marked_server_side() -> None:
    src = inspect.getsource(master_executor)
    assert 'user={"id": self.user_id}' not in src
    assert src.count('"_server_side": True') >= 4


def test_no_configured_channel_is_a_single_warning_not_an_error(caplog) -> None:
    """OBS-03: with no Telegram/email configured every data-quality alert was a Sentry ERROR."""
    import logging

    from backend_app.backend.alert_system import AlertLevel, AlertSystem

    system = AlertSystem()
    system._channels = []
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            asyncio.run(system.send_alert(level=AlertLevel.WARNING, title="t", message="m"))
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len([r for r in caplog.records if "No alert channel" in r.getMessage()]) == 1
