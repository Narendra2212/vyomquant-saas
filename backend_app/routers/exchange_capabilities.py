"""Truthful exchange capability API — the launch dashboard contract.

WHY A NEW ROUTER RATHER THAN A CHANGE TO `routers/exchange.py`
-------------------------------------------------------------
`routers/exchange.py::/supported` answers the question "what does ccxt know about?" by
iterating `ccxt_base.exchanges` and reporting each class's `has` map. That is a true
answer to a question nobody asked. A customer reading it sees ~110 exchanges and
reasonably concludes they can trade on them; seven can receive an order, and of those one
is blocked on a money-safety defect (EXCH-01).

This router answers the questions a customer actually has, and keeps them apart:

    can I see prices here?            -> market_data
    can I connect my account?         -> authenticated_access
    is there a sandbox to practise?   -> sandbox
    can this platform place my order? -> order_execution
    how far has it been certified?    -> certification
    am I allowed to trade it today?   -> trading_authorized

Those are six different facts and they were previously one boolean. The separation is not
presentational: `trading_authorized` is false for every venue right now, and that is the
honest state.

`routers/exchange.py` is being modified by another workstream, so it is left alone. This
router is additive and reads only
`backend_app/core/exchange_capability_registry.py`, which is the canonical source.
Reconciling or retiring the legacy endpoint is a separate, coordinated change — the
duplicate-registry cleanup is tracked as EXCH-04/EXCH-10.

EVERY NUMBER HERE IS COUNTABLE FROM EVIDENCE. `/connectivity/summary` reports the
verified count, the target band, the shortfall, and — critically — the attribution of
that shortfall between "this exchange is not reachable" and "the host that ran the check
could not reach it". A dashboard that conflates those two would turn a network policy
into a product claim.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend_app.core.exchange_capability_registry import (ConnectivityState,
                                                           get_capability_registry)

logger = logging.getLogger("ExchangeCapabilitiesAPI")

router = APIRouter(prefix="/api/exchanges", tags=["exchange-capabilities"])


def _customer_view(rec) -> Dict[str, Any]:
    """One venue, rendered as the six independent facts a customer needs.

    Tri-state is preserved throughout: ``None`` means unverified and is rendered as
    ``null``, never coerced to ``false``. "We have not checked" and "it does not work"
    are different answers and a trader planning around them would act differently.
    """
    c = rec.capabilities
    state = rec.attained()

    return {
        "exchange_id": rec.exchange_id,
        "display_name": rec.display_name,
        "countries": rec.countries,
        "www_url": rec.www_url,

        # 1. Market data — what most venues here are actually good for.
        "market_data": {
            "verified": rec.has_state(ConnectivityState.MARKET_DATA_VERIFIED),
            "connectivity_verified": rec.has_state(
                ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED
            ),
            "ticker": c.public_ticker,
            "order_book": c.public_order_book,
            "ohlcv": c.public_ohlcv,
            "trades": c.public_trades,
            "websocket": c.websocket,
        },

        # 2. Authenticated account access.
        "authenticated_access": {
            "verified": rec.has_state(ConnectivityState.AUTHENTICATED_ACCESS_VERIFIED),
            "required_credentials": rec.required_credentials,
            "balance": c.private_balance,
            "open_orders": c.private_open_orders,
            "order_status": c.private_order_status,
            "my_trades": c.private_my_trades,
        },

        # 3. Sandbox. `declared` is a URL-table lookup; `routing_proven` is a
        #    measurement. They disagree for bitget, which is the entire point.
        "sandbox": {
            "verified": rec.has_state(ConnectivityState.SANDBOX_VERIFIED),
            "declared_by_library": c.sandbox_url_declared,
            "routing_proven": c.sandbox_routing_proven,
        },

        # 4. Order execution — can THIS PLATFORM route an order here?
        #    Declared capability is the library's opinion and is labelled as such.
        "order_execution": {
            "platform_can_route": rec.exchange_id in (
                get_capability_registry().order_routing_allowlisted()
            ),
            "safety_blocked": rec.exchange_id in get_capability_registry().safety_blocked(),
            "declared_create_order": c.order_create,
            "declared_cancel_order": c.order_cancel,
            "declared_order_types": {
                "market": c.order_market,
                "limit": c.order_limit,
                "stop": c.order_stop,
                "stop_limit": c.order_stop_limit,
                "post_only": c.order_post_only,
                "reduce_only": c.order_reduce_only,
            },
        },

        # 5. Certification ladder position.
        "certification": {
            "state": state.name,
            "state_value": int(state),
            "ladder": [s.name for s in ConnectivityState],
            "evidence": {k: v.to_dict() for k, v in rec.evidence.items()},
        },

        # 6. The only field that answers "may I trade this today".
        "trading_authorized": rec.is_tradable(),

        "known_limitations": rec.known_limitations,
        "inconsistencies": rec.inconsistencies(),
    }


@router.get("/capabilities")
async def list_capabilities(
    state_at_least: Optional[str] = Query(
        None, description="Filter to venues holding at least this ConnectivityState"
    ),
    market_data_only: bool = Query(
        False, description="Only venues with MARKET_DATA_VERIFIED"
    ),
    tradable_only: bool = Query(
        False, description="Only venues with LIVE_TRADING_AUTHORIZED"
    ),
) -> Dict[str, Any]:
    """Every eligible venue with its six independent capability facts."""
    reg = get_capability_registry()
    records = reg.eligible_candidates()

    if state_at_least:
        try:
            floor = ConnectivityState[state_at_least.upper()]
        except KeyError:
            raise HTTPException(
                status_code=400,
                detail=f"unknown state {state_at_least!r}; valid: "
                       f"{[s.name for s in ConnectivityState]}",
            )
        # `attained()` returns the highest rung held ON ITS OWN EVIDENCE, so this filter
        # cannot be satisfied by inference from a lower rung.
        records = [r for r in records if r.attained() >= floor]

    if market_data_only:
        records = [r for r in records
                   if r.has_state(ConnectivityState.MARKET_DATA_VERIFIED)]
    if tradable_only:
        records = [r for r in records if r.is_tradable()]

    return {
        "count": len(records),
        "exchanges": [_customer_view(r) for r in
                      sorted(records, key=lambda r: r.exchange_id)],
    }


@router.get("/capabilities/{exchange_id}")
async def get_capability(exchange_id: str) -> Dict[str, Any]:
    """One venue's capability record."""
    rec = get_capability_registry().get(exchange_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"unknown exchange {exchange_id!r}")
    return _customer_view(rec)


@router.get("/connectivity/summary")
async def connectivity_summary() -> Dict[str, Any]:
    """The launch dashboard. Counts only, each traceable to per-venue evidence.

    `shortfall_attribution` is the field that keeps this honest. A verified count on its
    own invites the reader to treat every non-verified venue as a broken exchange, and on
    the host this was last run from, 12 of them were unreachable because of a DNS sinkhole
    and a TLS-intercepting middlebox — facts about the network, not the venues.
    """
    reg = get_capability_registry()
    s = reg.summary()

    return {
        "ccxt_version": s["ccxt_version"],
        "generated_at": s["generated_at"],
        "evidence_artifact": s["evidence_artifact"],

        "universe": {
            "raw_ccxt_ids": s["raw_ccxt_ids"],
            "aliases_excluded": s["aliases_excluded"],
            "eligible_candidates": s["eligible_candidates"],
        },

        # The ladder, each rung counted independently.
        "states": s["state_counts"],

        "target": s["target"],

        "execution": {
            "order_routing_allowlisted": s["order_routing_allowlisted"],
            "safety_blocked": s["safety_blocked"],
            "trading_authorized_count": s["tradable_count"],
            # Stated rather than implied, because it is the single most load-bearing
            # fact on this page and a reader scanning counts could otherwise miss it.
            "note": (
                "trading_authorized_count is 0. Connectivity verification does not "
                "authorize trading; LIVE_TRADING_AUTHORIZED is granted only by the "
                "production safety and release gates."
            ),
        },

        "registry_inconsistencies": s["inconsistencies"],
    }


@router.get("/connectivity/launch-list")
async def launch_list() -> Dict[str, Any]:
    """The proposed launch connectivity list, with every venue's reason for inclusion.

    Deliberately NOT a list of tradable venues. It is the set whose market-data
    connectivity is verified by evidence, which is what the launch requirement asks for
    and is a genuinely useful product surface: a trader can chart and backtest on these.
    """
    reg = get_capability_registry()

    verified_md, verified_conn, not_verified = [], [], []
    for r in reg.eligible_candidates():
        if r.has_state(ConnectivityState.MARKET_DATA_VERIFIED):
            verified_md.append(r)
        elif r.has_state(ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED):
            verified_conn.append(r)
        else:
            not_verified.append(r)

    def brief(r) -> Dict[str, Any]:
        ev = r.evidence.get("PUBLIC_CONNECTIVITY_VERIFIED")
        md = r.evidence.get("MARKET_DATA_VERIFIED")
        return {
            "exchange_id": r.exchange_id,
            "display_name": r.display_name,
            "state": r.attained().name,
            "connectivity_checked_at": ev.checked_at if ev else None,
            "connectivity_detail": ev.detail if ev else None,
            "market_data_detail": md.detail if md else None,
            "failure_class": (ev.failure_class if ev and not ev.attained else None),
            "websocket": r.capabilities.websocket,
        }

    return {
        "market_data_verified": {
            "count": len(verified_md),
            "exchanges": [brief(r) for r in sorted(verified_md,
                                                   key=lambda r: r.exchange_id)],
        },
        "connectivity_only": {
            "count": len(verified_conn),
            "note": "the venue answered, but no market-data method returned usable data",
            "exchanges": [brief(r) for r in sorted(verified_conn,
                                                   key=lambda r: r.exchange_id)],
        },
        "not_verified": {
            "count": len(not_verified),
            "exchanges": [brief(r) for r in sorted(not_verified,
                                                   key=lambda r: r.exchange_id)],
        },
    }


@router.get("/connectivity/states")
async def ladder() -> Dict[str, Any]:
    """The certification ladder, documented for API consumers."""
    meaning = {
        "DISCOVERED": "the exchange id exists in the installed ccxt. No network call. No claim.",
        "CLIENT_INITIALIZED": "the ccxt class instantiated. Still no network call — a "
                              "constructor check is explicitly not connectivity.",
        "PUBLIC_CONNECTIVITY_VERIFIED": "a real public request was answered by the venue.",
        "MARKET_DATA_VERIFIED": "required public market-data operations returned usable data.",
        "AUTHENTICATED_ACCESS_VERIFIED": "authenticated access succeeded with authorized "
                                         "credentials.",
        "SANDBOX_VERIFIED": "the venue's sandbox was verified, INCLUDING a proof that the "
                            "client left the production endpoint.",
        "TRADING_CERTIFIED": "order lifecycle and safety tests passed.",
        "LIVE_TRADING_AUTHORIZED": "approved by the production safety and release gates. "
                                   "Cannot be set by any verification run.",
    }
    return {
        "ladder": [
            {"value": int(s), "name": s.name, "meaning": meaning[s.name]}
            for s in ConnectivityState
        ],
        "rule": (
            "States are never inferred from one another. Each requires its own evidence "
            "and its own prerequisites. Holding a rung says nothing about the next one."
        ),
    }
