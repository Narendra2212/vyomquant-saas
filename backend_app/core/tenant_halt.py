"""Durable, cross-worker per-tenant trading halt (RT-19).

The user kill switch used to be a dict entry in ONE worker: other workers, the bot runner and a
restart never saw it, although the API told the user every execution was halted. The halt now
lives in the shared cache (Redis) under ``tenant_halt:<id>`` with NO expiry, mirrored in
process for the synchronous dashboard readers.

Reads are FAIL-CLOSED for the execution path: if the shared store cannot be read, the caller
gets :class:`TenantHaltUnknown` and must refuse to trade. (When no shared store is configured
at all - local dev - the in-process mirror is the only state, as before.)
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger("TenantHalt")

_KEY = "tenant_halt:{}"
_mirror: Dict[str, bool] = {}


class TenantHaltUnknown(RuntimeError):
    """The shared halt state could not be read; the safe answer is "halted"."""


def _key(tenant_id) -> str:
    return _KEY.format(str(tenant_id))


def mirror_is_halted(tenant_id) -> bool:
    """Synchronous in-process view (dashboards). Not authoritative across workers."""
    return _mirror.get(str(tenant_id), False)


async def _client():
    from backend_app.core.cache.redis_manager import redis_manager

    return await redis_manager._get_active_cache()


async def set_halt(tenant_id, active: bool) -> bool:
    """Set or clear the halt. Returns True when it was written to the shared store.

    The in-process mirror is always updated first, so the worker that took the request halts
    immediately even if the shared write fails (the caller is told via the return value).
    """
    tid = str(tenant_id)
    _mirror[tid] = bool(active)
    try:
        client = await _client()
        if client is None:
            return False
        if active:
            await client.set(_key(tid), "1")
        else:
            await client.delete(_key(tid))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("[TenantHalt] could not persist halt for %s: %s", tid[:8], exc)
        return False


async def is_halted(tenant_id) -> bool:
    """Authoritative check for the execution path. Raises :class:`TenantHaltUnknown`."""
    tid = str(tenant_id)
    if _mirror.get(tid):
        return True
    try:
        client = await _client()
        if client is None:
            return False
        raw = await client.get(_key(tid))
    except Exception as exc:  # noqa: BLE001
        raise TenantHaltUnknown(f"halt state unreadable: {exc}") from exc
    halted = raw in ("1", b"1", 1, True)
    _mirror[tid] = halted
    return halted
