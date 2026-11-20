"""RT-21: user-initiated, risk-reducing order control (cancel / cancel-all).

WHY THIS EXISTS
    ``POST /api/orders/cancel/{id}`` and ``/cancel-all`` ended in ``_build_execution_engine``,
    which raises 403 unconditionally ("algo-only"), and the guard in front of them rejected the
    ``side='cancel'`` / ``size=0`` signal the routes built. In an incident a live user could not
    cancel a resting order from the platform. The "algo-only" policy is about OPENING risk; a
    cancel only removes it, so it must always be available -- including when the platform is in
    ``safe`` mode, when the tenant is halted (RT-19) and when the global kill switch is on.

WHAT IT DOES NOT DO
    It never places an order. Flattening a position (close-all) is an order placement and stays
    behind the live gates (see ``routers/portfolio.py``); it is not implemented here.

SAFETY PROPERTIES
    * Tenant-scoped: credentials are loaded for the authenticated user only.
    * Idempotent: an order the venue no longer knows is "already_closed"; a repeated request with
      the same ``Idempotency-Key`` replays the first result instead of calling the venue again
      (replay window 24h, best effort -- a Redis outage degrades to "call the venue", which is
      itself idempotent).
    * Honest about uncertainty: a lost response is reported as ``unknown`` (the order may still
      be resting) and never as success.
    * Audited: every request is written to the order audit trail (RT-21 / AUD-02); an audit
      failure is logged and never blocks the cancel.
    * Uses the single cancel boundary in ``exchange_executor`` (PAPER-01's venue-write inventory
      stays at four).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from backend_app.backend.exchange_executor import get_exchange_executor

logger = logging.getLogger(__name__)

_REPLAY_TTL_SECONDS = 24 * 3600


async def _executor_for(user_id: str, exchange_id: str, vault):
    keys = vault.load_decrypted_keys(user_id, exchange_id)
    executor = get_exchange_executor(
        exchange_id,
        keys["api_key"],
        keys["secret_key"],
        bool(keys.get("sandbox") or keys.get("testnet")),
        keys.get("password"),
        user_id=str(user_id),
    )
    if getattr(executor, "_exchange", None) is None:
        await executor.connect()
    return executor


async def _audit(user_id: str, exchange_id: str, symbol: str, action: str,
                 status: str, ok: bool, execution_id: Optional[str],
                 detail: Dict[str, Any]) -> None:
    try:
        from backend_app.core.audit_trail import (OrderConfirmedEvent,
                                                  OrderFailedEvent,
                                                  get_order_audit_logger)
        audit = get_order_audit_logger()
        meta = {"action": action, "status": status, **detail}
        if ok:
            await audit.log_order_confirmed(OrderConfirmedEvent(
                user_id=str(user_id), strategy_id="manual_control", symbol=symbol,
                side=action, exchange_id=exchange_id, execution_id=execution_id,
                status=status, metadata=meta))
        else:
            await audit.log_order_failed(OrderFailedEvent(
                user_id=str(user_id), strategy_id="manual_control", symbol=symbol,
                side=action, exchange_id=exchange_id, execution_id=execution_id,
                error_message=str(detail.get("error") or status), metadata=meta))
    except Exception:  # noqa: BLE001 - an audit failure must never block a risk-reducing action
        logger.exception("ORDER CONTROL AUDIT WRITE FAILED (%s %s)", action, symbol)


async def _replay_key(redis_client, user_id: str, idempotency_key: Optional[str]):
    return f"order_control_idem:{user_id}:{idempotency_key}" if idempotency_key else None


async def _load_replay(redis_client, key):
    if not key or redis_client is None:
        return None
    try:
        raw = await redis_client.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


async def _store_replay(redis_client, key, result):
    if not key or redis_client is None:
        return
    try:
        await redis_client.set(key, json.dumps(result, default=str), ex=_REPLAY_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("could not store the order-control replay record")


async def cancel_order_for_user(
    *, user_id: str, exchange_id: str, vault, order_id: str, symbol: str,
    execution_id: Optional[str] = None, idempotency_key: Optional[str] = None,
    redis_client=None,
) -> Dict[str, Any]:
    key = await _replay_key(redis_client, user_id, idempotency_key and f"cancel:{order_id}:{idempotency_key}")
    cached = await _load_replay(redis_client, key)
    if cached is not None:
        return {**cached, "replayed": True}

    executor = await _executor_for(user_id, exchange_id, vault)
    res = await executor.cancel_order(str(order_id), symbol)
    result = {
        "status": res.status,
        "success": bool(res.success),
        "order_id": str(order_id),
        "symbol": symbol,
        "error": res.error_message,
        "replayed": False,
    }
    await _audit(user_id, exchange_id, symbol, "cancel", res.status, bool(res.success),
                 execution_id, {"order_id": str(order_id), "error": res.error_message})
    # Only definite outcomes are replayed; an unknown/error outcome must be retried for real.
    if res.success:
        await _store_replay(redis_client, key, result)
    return result


async def cancel_all_for_user(
    *, user_id: str, exchange_id: str, vault, symbol: Optional[str],
    execution_id: Optional[str] = None, idempotency_key: Optional[str] = None,
    redis_client=None,
) -> Dict[str, Any]:
    key = await _replay_key(redis_client, user_id, idempotency_key and f"cancel_all:{symbol}:{idempotency_key}")
    cached = await _load_replay(redis_client, key)
    if cached is not None:
        return {**cached, "replayed": True}

    executor = await _executor_for(user_id, exchange_id, vault)
    summary = await executor.cancel_all_open_orders(symbol)
    result = {**summary, "symbol": symbol or "ALL", "replayed": False}
    await _audit(user_id, exchange_id, symbol or "ALL", "cancel_all",
                 "ok" if summary.get("success") else "partial_or_failed",
                 bool(summary.get("success")), execution_id,
                 {"cancelled": summary.get("cancelled"), "failed": summary.get("failed"),
                  "unknown": summary.get("unknown"), "error": summary.get("error")})
    if summary.get("success"):
        await _store_replay(redis_client, key, result)
    return result
