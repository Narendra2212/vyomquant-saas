"""BOT-03: bot signal traces never recorded their order outcome.

``signals.order_id`` / ``signals.trade_id`` are UUID columns; the bot wrote ``exec-...`` and
``tr_...`` there, so every order/execution update was rejected (and logged at DEBUG). Every bot
signal stayed "accepted" - filled, failed or refused by the kill switch alike.
"""
from __future__ import annotations

import inspect
import uuid

from backend_app.backend import master_executor as me


def _is_uuid(v):
    return str(uuid.UUID(v)) == v


def test_an_execution_id_becomes_a_valid_deterministic_uuid():
    a = me.signal_uuid_for("order", "exec-0956ae777735")
    assert _is_uuid(a) and a == me.signal_uuid_for("order", "exec-0956ae777735")
    assert a != me.signal_uuid_for("trade", "exec-0956ae777735"), "order and trade ids differ"


def test_a_value_that_is_already_a_uuid_is_kept():
    u = str(uuid.uuid4())
    assert me.signal_uuid_for("order", u) == u


def test_the_paper_ledger_order_uuid_links_the_trace_to_paper_orders():
    ledger_order = "bfe4992c-8a9c-4514-9aa0-69bd2f7ede24"
    assert me.signal_order_uuid("exec-1", {"order_id": ledger_order}) == ledger_order


def test_a_non_uuid_result_order_id_falls_back_to_the_derived_uuid():
    out = me.signal_order_uuid("exec-1", {"order_id": "paper_abc123"})
    assert out == me.signal_uuid_for("order", "exec-1")
    assert me.signal_order_uuid("exec-1", None) == out


def test_the_bot_writes_uuids_and_no_longer_swallows_trace_failures():
    src = inspect.getsource(me)
    assert "order_id=signal_order_uuid(v_exec_id, v_result)" in src
    assert 'trade_id=signal_uuid_for("trade", v_exec_id)' in src
    assert 'trade_id=f"tr_' not in src
    assert 'logger.debug(f"Signal trace audit logging exception' not in src
