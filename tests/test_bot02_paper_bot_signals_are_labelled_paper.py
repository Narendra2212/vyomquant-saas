"""BOT-02: a paper-mode deployment's signals were labelled LIVE.

``SignalService.create_signal`` (the BotRunner path) never named ``environment``, so migration
010's ``DEFAULT 'LIVE'`` labelled every paper bot's signal LIVE. Found in production on
2026-11-11: all 21 signals of paper deployment 6547c7b2 read ``environment = 'LIVE'``.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from backend_app.backend import master_executor
from backend_app.backend import signal_service as ss


class _Table:
    def __init__(self, sink):
        self.sink = sink

    def insert(self, row):
        self.sink.append(row)
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(self.sink[-1])])


class _SB:
    def __init__(self):
        self.rows = []

    def table(self, _name):
        return _Table(self.rows)


def _create(monkeypatch, **extra):
    svc = ss.SignalService.__new__(ss.SignalService)
    svc._local_signals = {}
    sb = _SB()

    async def _get_sb(user):
        return sb

    async def _audit(*a, **k):
        return None

    svc._get_supabase = _get_sb
    svc._record_signal_audit = _audit
    monkeypatch.setattr(ss, "schedule_last_signal_at", lambda *a, **k: None)
    asyncio.run(svc.create_signal(
        {"id": "u", "_server_side": True}, "s", "v1", "d", "binance", "BTC/USDT", "1m", "w",
        "BUY", {}, {}, **extra,
    ))
    return sb.rows[0]


def test_a_paper_signal_names_its_environment(monkeypatch):
    assert _create(monkeypatch, environment="PAPER")["environment"] == "PAPER"


def test_the_live_payload_is_unchanged_and_left_to_the_column_default(monkeypatch):
    assert "environment" not in _create(monkeypatch)


def test_the_bot_passes_paper_for_a_paper_deployment():
    src = inspect.getsource(master_executor)
    assert 'environment="PAPER" if self.paper_trading else None' in src
