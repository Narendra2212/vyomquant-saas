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
from enum import Enum
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


class HaltWriteOutcome(str, Enum):
    """What actually happened to the SHARED copy of the halt.

    ``set_halt`` returned a bare bool, and returned ``False`` for two situations that need
    OPPOSITE responses:

      * no shared store is configured at all (local dev). The in-process mirror is then the
        whole of the state, so the write is COMPLETE and nothing is wrong.
      * a shared store exists but the write did not land. The shared copy is unchanged, so
        on a CLEAR the halt is still engaged everywhere except this worker's mirror.

    Conflating those is the same defect class as KILL-01 in ``core/global_safety.py``, where
    ``_redis_get`` returned ``None`` for both "the key is not set" and "Redis could not be
    reached" and a kill switch consequently reported itself inactive during an outage. The
    module docstring there records it at length; this enum is that lesson applied here.
    """

    #: Written to the shared store. Every worker will see it.
    PERSISTED = "persisted"

    #: No shared store is configured. The mirror IS the state; the write is complete.
    NO_SHARED_STORE = "no_shared_store"

    #: A shared store exists and the write FAILED. The shared copy is unchanged.
    WRITE_FAILED = "write_failed"


async def set_halt_outcome(tenant_id, active: bool) -> HaltWriteOutcome:
    """Set or clear the halt, saying precisely what happened to the shared copy.

    The in-process mirror is updated FIRST so the worker that took the request halts
    immediately even if the shared write fails. That ordering is right for engaging a halt
    and wrong for clearing one, which is why a caller that CLEARS must inspect the outcome:
    on ``WRITE_FAILED`` the shared copy still says halted, so this worker's mirror now
    disagrees with every other reader, and the mirror has to be put back.
    """
    tid = str(tenant_id)
    previous = _mirror.get(tid, False)
    _mirror[tid] = bool(active)
    try:
        client = await _client()
        if client is None:
            return HaltWriteOutcome.NO_SHARED_STORE
        if active:
            await client.set(_key(tid), "1")
        else:
            await client.delete(_key(tid))
        return HaltWriteOutcome.PERSISTED
    except Exception as exc:  # noqa: BLE001
        logger.error("[TenantHalt] could not persist halt for %s: %s", tid[:8], exc)
        # THE ROLLBACK IS ASYMMETRIC, AND THE ASYMMETRY IS THE WHOLE POINT.
        #
        # On a failed CLEAR, put the mirror back. Leaving it optimistic is what made the
        # dashboards report "trading active" while `is_halted` kept refusing every order:
        # `is_halted` checks the mirror first, falls through to the shared store, and the
        # shared store still said halted. Two surfaces disagreeing about a safety control.
        #
        # On a failed ENGAGE, LEAVE THE MIRROR HALTED. The shared write did not land, so
        # other workers do not know -- but this worker took the request and must stop, and
        # a partial stop beats no stop. Rolling back here to "match the shared store" would
        # resume trading on the one worker the user actually asked to halt, which is the
        # exact opposite of fail-safe. (Written symmetrically at first; the test
        # `test_engaging_a_halt_still_takes_effect_locally_when_the_write_fails` is what
        # caught it.)
        if not active:
            _mirror[tid] = previous
        return HaltWriteOutcome.WRITE_FAILED


async def set_halt(tenant_id, active: bool) -> bool:
    """Set or clear the halt. Returns True when it was written to the shared store.

    Kept as the bool-shaped wrapper for callers that only need "did it reach the shared
    store". Anything that CLEARS a halt should use :func:`set_halt_outcome` instead and tell
    the user the difference between "cleared everywhere" and "could not be cleared".
    """
    return (await set_halt_outcome(tenant_id, active)) is HaltWriteOutcome.PERSISTED


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
