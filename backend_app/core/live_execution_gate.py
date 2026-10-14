"""TRADE-04 — the mandatory authorisation boundary for a real exchange write.

WHY THIS MODULE EXISTS
======================
The pre-launch audit reported live trading as OFF on the strength of
``ExecutionFlags.LIVE_TRADING_ENABLED``, a hardcoded ``False`` in
``core/safety_config.py``. An AST trace of the four functions that actually reach a venue
proved that control is not on that path at all::

    master_executor.py:_inner_loop()                -> no gate reference
    core/execution_engine.py:execute_with_idempotency() -> no gate reference
    core/execution_engine.py:_execute_trade_internal()  -> no gate reference
    backend/exchange_executor.py:place_order()           -> no gate reference

``SafetyMonitor.check_execution_allowed`` has six call sites, every one of them on a
cancel, close-all or DAG path. So the earlier "5/5 REFUSED" was a true statement about a
function that cannot refuse an order, and live trading was held off by configuration, a
paid-tier entitlement and the absence of a connected exchange account — defence in depth
with nothing behind it.

WHAT THIS IS, AND WHY IT IS NOT ANOTHER FLAG
--------------------------------------------
A flag can be read in the wrong place, which is exactly what happened. This is an
**artefact that has to be produced and presented**: the execution gateway performs the
required checks, records which ones passed, and signs the result. The venue-write boundary
(:func:`require_live_authorization`, called from ``CCXTExchangeExecutor.place_order``)
refuses to proceed without a valid signature covering **the complete** check set for
**this exact order**.

That inverts the failure mode. A flag read in the wrong place fails open; a missing
artefact fails closed. And because the signature covers the check names, a caller cannot
assemble a partial authorisation and have it accepted — the set is compared against
:data:`REQUIRED_CHECKS` at verification time, not only at issue time.

This is deliberately the same shape as the anti-bypass validation token that already
guards ``place_order`` (``core/global_safety.generate_validation_token``). That token
answers "did this come through the gateway?". It cannot answer "did the gateway establish
that this account is permitted to trade real money?", because it is computed from
``(execution_id, symbol, size)`` alone. This answers the second question, and the two are
checked together.

WHAT IT DOES NOT DO
-------------------
It does not *enable* live trading, and fixing the boundary is not live certification.
Live remains OFF / NOT CERTIFIED. What changes is that it is now off because a control on
the order path says so, rather than because of a configuration coincidence.

Regression: tests/test_trade04_live_order_gate_at_venue_boundary.py
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, FrozenSet, Iterable, Optional

logger = logging.getLogger("LiveExecutionGate")

#: Every condition that must hold before an order may reach a real exchange.
#:
#: Enumerated as names rather than booleans so a refusal can say WHICH condition was not
#: established, and so the signed authorisation records the complete set. Adding a name
#: here immediately invalidates every authorisation that does not carry it -- which is the
#: intended behaviour: a new required check must not be satisfiable by old code.
REQUIRED_CHECKS: FrozenSet[str] = frozenset({
    "authenticated_actor",
    "authorized_tenant_resource",
    "explicit_live_mode",
    "live_trading_entitlement",
    "exchange_binding",
    "kill_switch",
    "strategy_authorized",
    "risk",
    "capital",
    "precision",
    "idempotency",
    "execution_environment",
    "safety_config",
})

#: SEC-13. Generated fresh per process, never a literal in source.
#:
#: A hardcoded secret is knowable to anyone with repository read access, which makes this
#: "signature" no stronger than an unsigned flag against that reader. ``authorize_live_order``
#: (mint) and ``require_live_authorization`` (verify) are always called from the SAME
#: synchronous order-placement call stack, in the SAME process -- see this module's own
#: docstring for the call graph, and neither a :class:`LiveOrderAuthorization` nor this
#: secret is ever persisted or read back by a different process or after a restart. A
#: per-process random key therefore loses nothing a fixed one provided (mint and verify
#: still agree, because they run in the same process with the same ``_GATE_SECRET``) while
#: closing the actual gap: it cannot be read from source, and it rotates on every restart
#: without any operator action or deployment config.
#:
#: Separate secret from the bypass token's (``core/global_safety._VALIDATION_SECRET``,
#: fixed the same way for the same reason). The two artefacts answer different questions,
#: and a single secret would let a holder of one mint the other.
_GATE_SECRET: bytes = os.urandom(32)


class LiveExecutionRefused(Exception):
    """A real exchange write was refused. Carries a machine-readable ``code``.

    Raised, never returned. A returned verdict can be dropped by a caller, and dropping
    this one places a real order with customer money -- the same reasoning
    ``assert_live_environment`` applies.
    """

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class LiveOrderAuthorization:
    """Signed evidence that every condition in :data:`REQUIRED_CHECKS` was established.

    Frozen, and bound to one order: ``execution_id``, ``symbol``, ``side``, ``size`` and
    ``exchange_id`` are all covered by the signature, so an authorisation issued for a
    0.01 BTC buy cannot be replayed against a 10 BTC buy, or against a different venue.
    """

    execution_id: str
    tenant_id: str
    strategy_id: str
    symbol: str
    side: str
    size: str
    exchange_id: str
    checks: FrozenSet[str]
    signature: str

    def missing_checks(self) -> FrozenSet[str]:
        return REQUIRED_CHECKS - self.checks


def _normalise_symbol(symbol: Any) -> str:
    return str(symbol or "").replace("/", "-").upper()


def _normalise_size(size: Any) -> str:
    """Exact decimal text, so 0.010 and 0.01 sign identically and a float cannot sneak in."""
    try:
        return str(Decimal(str(size)).normalize())
    except Exception:  # noqa: BLE001 - an unparseable size must not be signable
        return f"<unparseable:{size!r}>"


def _sign(
    *,
    execution_id: str,
    tenant_id: str,
    strategy_id: str,
    symbol: str,
    side: str,
    size: str,
    exchange_id: str,
    checks: Iterable[str],
) -> str:
    payload = "|".join(
        [
            str(execution_id),
            str(tenant_id),
            str(strategy_id),
            _normalise_symbol(symbol),
            str(side or "").lower(),
            _normalise_size(size),
            str(exchange_id or "").lower(),
            ",".join(sorted(checks)),
        ]
    )
    return hmac.new(_GATE_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()



#: Environment variable holding the operator-managed list of tenants allowed to trade live
#: (comma separated tenant ids, or ``*`` for every tenant). Unset/empty means NOBODY.
LIVE_TENANT_ALLOWLIST_ENV = "VYOMQUANT_LIVE_TENANT_ALLOWLIST"

_LIVE_MODE_VALUES = frozenset({"live", "live_trading"})


def assert_platform_live_trading_enabled(tenant_id: Any = None) -> None:
    """RT-15: refuse unless the PLATFORM itself is in live mode, for this tenant.

    Until this existed, the gate's ``explicit_live_mode`` / ``safety_config`` checks were
    names the engine asserted about itself: a LIVE-environment engine holding an executor
    reached ``place_order`` even with ``VYOMQUANT_MODE=paper``. This reads the deployment's
    own configuration at call time (never cached at import) and is evaluated twice - when
    the authorisation is minted and again at the venue boundary - so flipping the platform
    back to paper/safe stops orders already in flight.

    All three must hold:
      * ``VYOMQUANT_MODE`` is ``live``;
      * ``VYOMQUANT_ENABLE_LIVE_TRADING`` is ``true``;
      * the tenant is in the operator-managed allowlist (``*`` = all; unset = none).

    Raises:
        LiveExecutionRefused: ``LIVE_TRADING_DISABLED_BY_PLATFORM_MODE``,
            ``LIVE_TRADING_NOT_ENABLED`` or ``LIVE_TENANT_NOT_ALLOWLISTED``.
    """
    from backend_app.core.safety_config import (get_live_trading_confirmation,
                                                get_vyomquant_mode)

    mode = str(get_vyomquant_mode("safe") or "").strip().lower()
    if mode not in _LIVE_MODE_VALUES:
        raise LiveExecutionRefused(
            "LIVE_TRADING_DISABLED_BY_PLATFORM_MODE",
            f"the platform trading mode is {mode or 'unset'!r}, not 'live'. "
            "No order was sent.",
        )
    if str(get_live_trading_confirmation("false") or "").strip().lower() != "true":
        raise LiveExecutionRefused(
            "LIVE_TRADING_NOT_ENABLED",
            "VYOMQUANT_ENABLE_LIVE_TRADING is not 'true'. No order was sent.",
        )
    allow = {
        t.strip()
        for t in os.getenv(LIVE_TENANT_ALLOWLIST_ENV, "").split(",")
        if t.strip()
    }
    if "*" not in allow and str(tenant_id or "").strip() not in allow:
        raise LiveExecutionRefused(
            "LIVE_TENANT_NOT_ALLOWLISTED",
            "this tenant is not in the operator-managed live-trading allowlist. "
            "No order was sent.",
        )


def authorize_live_order(
    *,
    execution_id: Any,
    tenant_id: Any,
    strategy_id: Any,
    symbol: Any,
    side: Any,
    size: Any,
    exchange_id: Any,
    checks: Iterable[str],
) -> LiveOrderAuthorization:
    """Issue an authorisation, or refuse.

    Refuses when any member of :data:`REQUIRED_CHECKS` is absent from ``checks``, naming
    the ones that are missing. The caller is the execution gateway; it passes the names of
    the checks it actually performed, so a gateway that gains a new branch which forgets
    one of them produces a refusal rather than an unguarded order.

    Raises:
        LiveExecutionRefused: ``LIVE_CHECKS_INCOMPLETE`` or ``LIVE_ORDER_UNIDENTIFIED``.
    """
    assert_platform_live_trading_enabled(tenant_id)

    supplied = frozenset(str(c) for c in checks)
    missing = REQUIRED_CHECKS - supplied

    if missing:
        raise LiveExecutionRefused(
            "LIVE_CHECKS_INCOMPLETE",
            "a real exchange write was requested without establishing: "
            + ", ".join(sorted(missing))
            + ". No order was sent.",
        )

    if not str(execution_id or "").strip() or not str(tenant_id or "").strip():
        raise LiveExecutionRefused(
            "LIVE_ORDER_UNIDENTIFIED",
            "a live order must carry both an execution_id and a tenant_id so it can be "
            "attributed and reconciled; one or both were empty. No order was sent.",
        )

    normalised = frozenset(supplied & REQUIRED_CHECKS)
    signature = _sign(
        execution_id=str(execution_id),
        tenant_id=str(tenant_id),
        strategy_id=str(strategy_id),
        symbol=symbol,
        side=side,
        size=size,
        exchange_id=exchange_id,
        checks=normalised,
    )
    return LiveOrderAuthorization(
        execution_id=str(execution_id),
        tenant_id=str(tenant_id),
        strategy_id=str(strategy_id),
        symbol=_normalise_symbol(symbol),
        side=str(side or "").lower(),
        size=_normalise_size(size),
        exchange_id=str(exchange_id or "").lower(),
        checks=normalised,
        signature=signature,
    )


def require_live_authorization(
    authorization: Optional[LiveOrderAuthorization],
    *,
    symbol: Any,
    side: Any,
    size: Any,
    exchange_id: Any,
) -> LiveOrderAuthorization:
    """THE GATE. Called immediately before the irreversible exchange write.

    Verifies that an authorisation exists, is signed by this process's gate, covers the
    complete required set, and was issued for **this** order on **this** venue.

    ``symbol``/``side``/``size``/``exchange_id`` are re-derived from the order about to be
    sent rather than read off the authorisation, so a mismatch between what was authorised
    and what is being placed is a refusal rather than being papered over.

    Raises:
        LiveExecutionRefused: with one of ``LIVE_EXECUTION_NOT_AUTHORIZED``,
            ``LIVE_CHECKS_INCOMPLETE``, ``LIVE_AUTHORIZATION_ORDER_MISMATCH`` or
            ``LIVE_AUTHORIZATION_INVALID_SIGNATURE``.
    """
    if authorization is None:
        raise LiveExecutionRefused(
            "LIVE_EXECUTION_NOT_AUTHORIZED",
            "the venue-write boundary received no live-execution authorisation. Every "
            "real exchange order must present one issued by the execution gateway after "
            "all required checks passed. No order was sent.",
        )

    if not isinstance(authorization, LiveOrderAuthorization):
        raise LiveExecutionRefused(
            "LIVE_EXECUTION_NOT_AUTHORIZED",
            f"the authorisation presented is a {type(authorization).__name__}, not a "
            "LiveOrderAuthorization. No order was sent.",
        )

    # RT-15: re-read the platform mode at the boundary itself, per order.
    assert_platform_live_trading_enabled(authorization.tenant_id)

    missing = authorization.missing_checks()
    if missing:
        raise LiveExecutionRefused(
            "LIVE_CHECKS_INCOMPLETE",
            "the authorisation presented does not cover: " + ", ".join(sorted(missing))
            + ". No order was sent.",
        )

    expected_symbol = _normalise_symbol(symbol)
    expected_side = str(side or "").lower()
    expected_size = _normalise_size(size)
    expected_exchange = str(exchange_id or "").lower()

    mismatches = []
    if authorization.symbol != expected_symbol:
        mismatches.append(f"symbol {authorization.symbol!r} != {expected_symbol!r}")
    if authorization.side != expected_side:
        mismatches.append(f"side {authorization.side!r} != {expected_side!r}")
    if authorization.size != expected_size:
        mismatches.append(f"size {authorization.size!r} != {expected_size!r}")
    if authorization.exchange_id != expected_exchange:
        mismatches.append(
            f"exchange {authorization.exchange_id!r} != {expected_exchange!r}"
        )
    if mismatches:
        raise LiveExecutionRefused(
            "LIVE_AUTHORIZATION_ORDER_MISMATCH",
            "the authorisation was issued for a different order: "
            + "; ".join(mismatches)
            + ". No order was sent.",
        )

    expected_signature = _sign(
        execution_id=authorization.execution_id,
        tenant_id=authorization.tenant_id,
        strategy_id=authorization.strategy_id,
        symbol=authorization.symbol,
        side=authorization.side,
        size=authorization.size,
        exchange_id=authorization.exchange_id,
        checks=authorization.checks,
    )
    if not hmac.compare_digest(expected_signature, str(authorization.signature)):
        raise LiveExecutionRefused(
            "LIVE_AUTHORIZATION_INVALID_SIGNATURE",
            "the authorisation's signature does not verify, so it was not issued by this "
            "execution gateway or has been altered. No order was sent.",
        )

    return authorization


__all__ = [
    "REQUIRED_CHECKS",
    "LiveExecutionRefused",
    "LiveOrderAuthorization",
    "authorize_live_order",
    "require_live_authorization",
]
