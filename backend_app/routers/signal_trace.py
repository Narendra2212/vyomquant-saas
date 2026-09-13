"""
routers/signal_trace.py — Signal Trace API Router

Owns ``/api/signal-trace/*`` — the paths ``algo22-terminal/src/pages/SignalTrace.jsx``
already calls. Mounted in ``backend_app/main.py`` with
``prefix="/api/signal-trace"``.

A NEW ROUTER, WITH ITS JUSTIFICATION (Requirement 22.1)
------------------------------------------------------
Requirement 22.1 forbids a new, parallel router "without such documented
justification", and ``design.md`` supplies it under "New router: signal_trace.py":
no existing router owns this resource. ``strategy_operations.py`` owns strategies,
backtests and deployments; ``signals.py`` owns the legacy flat signal surface under
``/api/signals``. Neither owns the Signal_Trace_Page's read model, and the frontend
already calls ``/api/signal-trace/*``.

This surface is READ-ONLY per Requirement 17 for everything the spec adds. The
pre-spec write endpoints at the bottom of this file (``POST /signals``, the three
``PUT /signals/{id}/...``) predate that decision and are left untouched rather than
removed, because removing a live endpoint is not this task's change; nothing in this
spec calls them.


╔══════════════════════════════════════════════════════════════════════════╗
║  ROUTE ORDER IS LOAD-BEARING IN THIS FILE. READ BEFORE ADDING A ROUTE.   ║
╚══════════════════════════════════════════════════════════════════════════╝

FastAPI matches routes IN REGISTRATION ORDER and stops at the first match. A
path-parameter segment matches ANY single segment, INCLUDING a literal one. So a
``/signals/{signal_id}`` registered before ``/signals/export`` makes the export
endpoint **permanently unreachable** — every request for it is routed to the detail
handler with ``signal_id="export"``, which then 404s a signal that never existed.

THIS IS NOT HYPOTHETICAL. IT IS ALREADY BROKEN THREE TIMES IN THIS CODEBASE:

  * ``backend_app/routers/signals.py`` registers ``GET /{signal_id}`` (line ~110)
    and ``GET /export`` (line ~248). ``GET /api/signals/export`` is dead today.
  * ``/api/library/creator/analytics``, ``/api/library/recommendations`` and
    ``/api/library/favorites`` were broken the same way.

THE RULE THIS FILE FOLLOWS, AND WHICH TASKS 13.2 AND 13.3 MUST KEEP:

    Every LITERAL path under ``/signals`` is registered in the LITERAL section
    below. Every PARAMETERISED path is registered in the section after it. The
    two sections are separated by a banner, and the boundary is asserted by
    ``tests/test_task_13_1_signal_trace_list.py`` against the REAL
    ``backend_app.main.app`` — not against a router assembled in the test, which
    would not catch a mounting-order regression.

    Task 13.3's ``GET /signals/export`` IS in the LITERAL section, and is named in
    ``SIGNAL_TRACE_LITERAL_PATHS`` so the guard test covers it.
    Task 13.2's ``GET /signals/{signal_id}`` is in the PARAMETERISED section — it
    EXTENDED the pre-spec handler that already owned that path rather than adding a
    second registration for it, because a duplicate path is unreachable for the same
    first-match reason a shadowed literal is.

``SIGNAL_TRACE_LITERAL_PATHS`` below names the literals, so the guard test does not
have to re-derive them from the source.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Request, APIRouter, Depends, HTTPException, Query, Response, Body
from pydantic import BaseModel, Field

from backend_app.backend.order_lifecycle_state import (
    ORDER_LIFECYCLE_STATE_VALUES,
    OrderLifecycleRejected,
)
from backend_app.backend.execution_environment import EXECUTION_ENVIRONMENTS
from backend_app.backend.signal_service import (
    SIGNAL_TRACE_PAGE_SIZE,
    SignalDecision,
    SignalRejected,
    SignalStatus,
    get_signal_service,
)
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter

router = APIRouter()
logger = logging.getLogger("SignalTraceRouter")

#: The three Execution_Environment values, for the ``environment`` filter's description.
#: Read from the platform's own vocabulary rather than transcribed, so the three words are
#: spelled in exactly one place (``backend/execution_environment.py``) — the same discipline
#: ``order_lifecycle_state`` gets two lines above.
ENVIRONMENT_FILTER_VALUES = [member.value for member in EXECUTION_ENVIRONMENTS]

#: Literal (non-parameterised) sub-paths of ``/signals`` this router serves. Every one of
#: these MUST be registered before ``/signals/{signal_id}`` or it becomes unreachable —
#: see this module's docstring. ``tests/test_task_13_1_signal_trace_list.py`` asserts the
#: ordering against the real app for each entry, so adding one here without registering it
#: in the literal section fails the build rather than shipping a dead endpoint.
SIGNAL_TRACE_LITERAL_PATHS = ("/api/signal-trace/signals/export",)


# ══════════════════════════════════════════════════════════════════════════
# REQUEST MODELS
# ══════════════════════════════════════════════════════════════════════════

class SignalCreateRequest(BaseModel):
    """Request model for creating a signal."""
    strategy_id: str = Field(..., description="Strategy ID")
    strategy_version: str = Field(..., description="Strategy version")
    deployment_id: str = Field(..., description="Deployment ID")
    exchange_id: str = Field(..., description="Exchange ID")
    symbol: str = Field(..., description="Trading pair")
    timeframe: str = Field(..., description="Timeframe")
    worker_id: str = Field(..., description="Worker ID")
    decision: str = Field(..., description="Signal decision: BUY, SELL, EXIT, CLOSE, HOLD")
    indicators: Dict = Field(..., description="Indicator values")
    market_info: Dict = Field(..., description="Market information")
    ml_info: Optional[Dict] = Field(None, description="ML/DL information")


class RiskDecisionRequest(BaseModel):
    """Request model for updating risk decision."""
    risk_passed: bool = Field(..., description="Whether risk check passed")
    risk_reason: str = Field(..., description="Reason for risk decision")
    position_size: Optional[float] = Field(None, description="Calculated position size")
    capital: Optional[float] = Field(None, description="Available capital")
    exposure: Optional[float] = Field(None, description="Current exposure")
    expected_loss: Optional[float] = Field(None, description="Expected loss")
    expected_reward: Optional[float] = Field(None, description="Expected reward")
    drawdown_check: Optional[bool] = Field(None, description="Drawdown check result")


class OrderUpdateRequest(BaseModel):
    """Request model for updating order information."""
    order_id: str = Field(..., description="Order ID")
    exchange_order_id: Optional[str] = Field(None, description="Exchange order ID")
    order_status: str = Field(..., description="Order status")
    quantity: float = Field(..., description="Order quantity")
    filled: float = Field(..., description="Filled quantity")
    remaining: float = Field(..., description="Remaining quantity")
    average_price: float = Field(..., description="Average fill price")
    fees: float = Field(..., description="Trading fees")
    slippage: float = Field(..., description="Slippage")
    latency_ms: float = Field(..., description="Order latency in milliseconds")


class ExecutionUpdateRequest(BaseModel):
    """Request model for updating execution information."""
    trade_id: str = Field(..., description="Trade ID")
    pnl: float = Field(..., description="PnL")
    realized_pnl: Optional[float] = Field(None, description="Realized PnL")


# ══════════════════════════════════════════════════════════════════════════
# SIGNAL CRUD OPERATIONS
# ══════════════════════════════════════════════════════════════════════════

@router.post("/signals")
@limiter.limit("100/minute")
async def create_signal(
    request: Request,
    body: SignalCreateRequest = Body(...),
    user: dict = Depends(get_current_user)
):
    """
    Create a new signal record.
    
    Signal generation event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.create_signal(
            user=user,
            strategy_id=body.strategy_id,
            strategy_version=body.strategy_version,
            deployment_id=body.deployment_id,
            exchange_id=body.exchange_id,
            symbol=body.symbol,
            timeframe=body.timeframe,
            worker_id=body.worker_id,
            decision=body.decision,
            indicators=body.indicators,
            market_info=body.market_info,
            ml_info=body.ml_info
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error creating signal for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNAL_CREATE_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#   LITERAL PATHS — every route in this section is registered BEFORE the
#   parameterised section below, and MUST STAY THERE. Adding a literal
#   ``/signals/<word>`` route after ``/signals/{signal_id}`` makes it
#   unreachable. See this module's docstring.
#
#   Task 13.3's ``GET /signals/export`` is here, for that reason.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


@router.get("/signals")
@limiter.limit("200/minute")
async def list_signals(request: Request,
    # ── Requirement 17.2's filter categories. Every one is declared as a LIST so
    #    FastAPI collects a repeated query parameter (?symbol=A&symbol=B) into all of
    #    its values instead of keeping only the last — which is what makes
    #    OR-within-category expressible at all.
    strategy_id: Optional[List[str]] = Query(None),
    strategy_version: Optional[List[str]] = Query(None),
    deployment_id: Optional[List[str]] = Query(None),
    symbol: Optional[List[str]] = Query(None),
    side: Optional[List[str]] = Query(None, description="BUY or SELL; filters the `decision` column"),
    order_lifecycle_state: Optional[List[str]] = Query(
        None, description=f"One of {list(ORDER_LIFECYCLE_STATE_VALUES)}"
    ),
    # ── Requirement 23.4's Execution_Environment filter (task 29.3). ONE MORE OF THE SAME:
    #    declared as a LIST exactly like the categories above, so
    #    ?environment=PAPER&environment=LIVE collects into both instead of keeping only the
    #    last. No default, so the default behaviour is Requirement 23.6's - the caller's own
    #    signals across ALL environments.
    environment: Optional[List[str]] = Query(
        None, description=f"One of {ENVIRONMENT_FILTER_VALUES}; repeatable"
    ),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    # ── Requirement 17.5: at most 100 per page, and the bound is the service's own
    #    constant rather than a second copy of the number.
    limit: int = Query(SIGNAL_TRACE_PAGE_SIZE, ge=1, le=SIGNAL_TRACE_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    # ── Pre-spec filters SignalTrace.jsx still sends today. Kept so the page keeps
    #    working until task 18 rewires it; not part of Requirement 17.2's set.
    exchange_id: Optional[List[str]] = Query(None),
    worker_id: Optional[List[str]] = Query(None),
    decision: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None, description="Legacy signals.status vocabulary"),
    ml_type: Optional[str] = Query(None, description="ml or rule_based"),
    search: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    """List the authenticated user's signals, filtered and paginated.

    Requirements 17.1, 17.2, 17.5, 17.7, 20.1, 21.3.

    Sorted by ``generated_at`` descending (Requirement 17.1). Filters combine
    AND-across-categories / OR-within-category (Requirement 17.2). Ownership is enforced
    twice — the request-scoped client applies RLS and the query adds an explicit
    ``user_id`` predicate — and a filter naming another user's resource matches no row,
    which is the identical response a nonexistent identifier produces (Requirements 20.1,
    20.2).

    Returns
        **200** ``{signals, limit, offset, count, total, total_is_exact, has_more,
        next_offset, filters_active, active_filters, lifecycle_state_source, degraded}``.
        ``filters_active`` is Requirement 17.7's empty-state discriminator; ``degraded``
        is non-null when migration 005b has not been applied.
        **400** ``ORDER_LIFECYCLE_STATE_UNRECOGNISED``.
        **500** ``SIGNALS_LIST_FAILED`` — an explicit error, never a stale or fabricated
        list (Requirement 17.7).

    NO RESPONSE CACHE, DELIBERATELY
        A 10-second Redis cache used to sit here. It is removed rather than extended:
        its key covered only 8 of the 13 filter categories, so two requests differing
        only in ``deployment_id``, ``worker_id``, ``date_from``, ``date_to`` or
        ``search`` collided and the second was served the FIRST one's signals — a
        correctness bug against Requirement 17.1 ("SHALL NOT omit a signal that was
        actually generated"), not just a staleness one. Requirement 18.1 makes this list
        realtime over the ``SIGNAL_FAMILY`` channel anyway, so a cache in front of it
        would be racing the frames that update it.
    """
    try:
        service = await get_signal_service()
        return await service.list_signal_trace(
            user,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            deployment_id=deployment_id,
            symbol=symbol,
            side=side,
            order_lifecycle_state=order_lifecycle_state,
            environment=environment,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            exchange_id=exchange_id,
            worker_id=worker_id,
            decision=decision,
            status=status,
            ml_type=ml_type,
            search=search,
        )
    except (SignalRejected, OrderLifecycleRejected) as e:
        # `SignalRejected` covers task 29.3's SIGNAL_ENVIRONMENT_UNRECOGNISED, spelled as the
        # export handler below already spells its own pair: one clause, the refusal's own
        # status, and the same `{error, message, …}` body every other refusal on this router
        # produces.
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing signals for user {user.get('id')}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNALS_LIST_FAILED", "message": str(e)}
        )


@router.get("/signals/export")
@limiter.limit("50/minute")
async def export_signals(request: Request,
    # No `pattern=` here, deliberately: the service's own `normalise_export_format` is
    # the single place the two accepted formats are named, and it answers an unsupported
    # one with the documented 400 + SIGNAL_EXPORT_FORMAT_UNSUPPORTED below rather than
    # FastAPI's generic 422 on a regex a caller cannot see. It also accepts "CSV" as
    # "csv", which a hand-built URL or a bookmarked one legitimately sends.
    format: str = Query("json", description="json or csv"),
    # ── Requirement 17.2's filter categories, declared EXACTLY as `list_signals`
    #    declares them (lists, so a repeated query parameter is collected instead of
    #    overwritten). This is what makes "export what I am looking at" true rather than
    #    approximately true.
    strategy_id: Optional[List[str]] = Query(None),
    strategy_version: Optional[List[str]] = Query(None),
    deployment_id: Optional[List[str]] = Query(None),
    symbol: Optional[List[str]] = Query(None),
    side: Optional[List[str]] = Query(None, description="BUY or SELL; filters the `decision` column"),
    order_lifecycle_state: Optional[List[str]] = Query(
        None, description=f"One of {list(ORDER_LIFECYCLE_STATE_VALUES)}"
    ),
    # ── Task 29.3's environment filter, declared EXACTLY as the list declares it, for the
    #    same reason every other category is: "export what I am looking at" has to include
    #    the environment I am looking at.
    environment: Optional[List[str]] = Query(
        None, description=f"One of {ENVIRONMENT_FILTER_VALUES}; repeatable"
    ),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    # ── The pre-spec filters SignalTrace.jsx's export button still sends today.
    exchange_id: Optional[List[str]] = Query(None),
    worker_id: Optional[List[str]] = Query(None),
    decision: Optional[List[str]] = Query(None),
    status: Optional[List[str]] = Query(None, description="Legacy signals.status vocabulary"),
    ml_type: Optional[str] = Query(None, description="ml or rule_based"),
    search: Optional[str] = Query(None),
    user: dict = Depends(get_current_user)
):
    """The authenticated user's signals as a downloadable file. Requirement 17.1.

    THE LIST, WALKED TO THE END - NOT A SECOND QUERY
        Every filter above is ``GET /signals``'s own, and the body is rendered from the
        same ``signal_trace_item`` projection that list returns, page by page to the end of
        the filtered result. So an export of a filtered view is that view (Requirement
        17.2's AND-of-categories / OR-within-category semantics included), ownership is
        enforced by the same owner-scoped read (Requirements 20.1, 20.2), and no signal is
        omitted because it fell past the first page (Requirement 17.1).

    Returns
        **200** the file. ``text/csv; charset=utf-8`` or ``application/json``, both as an
        ``attachment`` named ``signal_trace.csv`` / ``signal_trace.json`` - the names
        ``SignalTrace.jsx`` gives the blob it saves.

        Three headers carry what the body cannot: ``X-Export-Row-Count``,
        ``X-Export-Truncated`` and ``X-Export-Max-Rows``. A CSV has nowhere to put
        metadata, and a partial export that does not say it is partial is the one way this
        endpoint could quietly break Requirement 17.1 - so it says so in both formats
        (the JSON body carries the same three fields).

        ``Cache-Control: no-store`` because this is one user's own audit data on a
        response that a shared cache has no business keeping.

        **400** ``SIGNAL_EXPORT_FORMAT_UNSUPPORTED`` or
        ``ORDER_LIFECYCLE_STATE_UNRECOGNISED`` (the list's own refusal, unchanged - an
        unrecognised state filter is refused rather than ignored, because ignoring it
        would export MORE signals than were asked for).
        **500** ``SIGNALS_EXPORT_FAILED`` - an explicit error, never a truncated or empty
        file passed off as a complete one.
    """
    try:
        service = await get_signal_service()
        export = await service.export_signal_trace(
            user,
            format=format,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            deployment_id=deployment_id,
            symbol=symbol,
            side=side,
            order_lifecycle_state=order_lifecycle_state,
            environment=environment,
            date_from=date_from,
            date_to=date_to,
            exchange_id=exchange_id,
            worker_id=worker_id,
            decision=decision,
            status=status,
            ml_type=ml_type,
            search=search,
        )
        return Response(
            content=export["content"],
            media_type=export["media_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{export["filename"]}"',
                "Cache-Control": "no-store",
                "X-Export-Row-Count": str(export["row_count"]),
                "X-Export-Truncated": "true" if export["truncated"] else "false",
                "X-Export-Max-Rows": str(export["max_rows"]),
            },
        )
    except (SignalRejected, OrderLifecycleRejected) as e:
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting signals for user {user.get('id')}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNALS_EXPORT_FAILED", "message": str(e)}
        )


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#   PARAMETERISED PATHS — nothing below this line may be a literal sub-path of
#   ``/signals``. ``{signal_id}`` matches any single segment, so a literal
#   route added here is shadowed by it and becomes unreachable. Put literals
#   in the section above. See this module's docstring.
#
#   Task 13.2's ``GET /signals/{signal_id}`` lives here (the pre-spec handler,
#   extended in place — see its docstring).
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


@router.get("/signals/{signal_id}")
@limiter.limit("200/minute")
async def get_signal(request: Request, 
    signal_id: str,
    # ── Task 29.3's environment filter, declared as a LIST here too, so one URL shape works
    #    across all three read paths. On a detail view it is a predicate on the read: a
    #    signal outside the named environments answers exactly as an unknown one does.
    environment: Optional[List[str]] = Query(
        None, description=f"One of {ENVIRONMENT_FILTER_VALUES}; repeatable"
    ),
    user: dict = Depends(get_current_user)
):
    """One signal's FULL trace detail. Requirements 17.6, 16.7, 20.1, 20.2, 20.3.

    THE PRE-SPEC HANDLER, EXTENDED IN PLACE — NOT A SECOND ROUTE
        A ``GET /signals/{signal_id}`` already existed here before this spec and returned
        ``{signal, timeline}``, where ``signal`` was the raw ``public.signals`` row. Task
        13.2 extends it rather than registering a second handler for the same path,
        because a duplicate path would be dead on arrival: FastAPI stops at the first
        match, so the second registration could never be reached (the same shadowing
        failure this module's route-order banner is about, in its other form).

        ``timeline`` is kept, unchanged, because ``SignalTrace.jsx`` renders it today and
        task 19 is what re-points the page. ``signal`` is now task 13.1's
        ``signal_trace_item`` projection instead of the raw row, so the detail view and the
        list agree field-for-field about the same signal.

    Returns
        **200** ``{signal, trace, lifecycle_transitions, timeline, lifecycle_state_source,
        degraded}``.

        ``trace`` carries Requirement 17.6's four sections — ``dag_nodes``,
        ``ml_inference``, ``risk_validation``, ``execution`` — each labelled with the
        ``source`` it was read from (``signal_trace_engine`` or ``signals_row``), because
        ``signal_trace_engine`` is an in-memory store with an hour's retention and a
        signal older than that legitimately has no record there.

        ``ml_inference.applicable`` is ``false`` — not ``null`` — for a strategy version
        with no ML node, which is Requirement 17.6's "where the signal's strategy version
        includes an ML node" as a fact the page can render rather than an empty panel.

        ``lifecycle_transitions`` is Requirement 16.7's persisted transition history in
        chronological order by ``occurred_at``.

        **404** ``SIGNAL_NOT_FOUND`` — for a signal that does not exist AND for one owned
        by another user, byte-identically (Requirements 20.1, 20.2). That identity is
        structural, not a matched pair of messages: the read is owner-scoped (RLS on the
        caller's own client plus an explicit ``user_id`` predicate), it returns no row in
        both cases, and nothing downstream of it looks the identifier up again — so there
        is no second code path that could reveal existence.

        **500** ``SIGNAL_GET_FAILED``. Reserved for a genuine failure: neither an
        unapplied migration 005b nor an unreadable trace store reaches it. Both degrade,
        and say so on the response (see ``degraded``).

    WHAT A NON-OWNER OF THE STRATEGY GETS (task 29.4, Requirements 7.9, 23.2, 23.3)
        A caller who owns this SIGNAL but did not write the STRATEGY - a subscriber running
        a purchased Listing in their own Paper_Session - is served
        ``{signal, viewer_role, lifecycle_state_source, degraded}``, where ``signal`` is
        exactly ``signal_service.SUBSCRIBER_SIGNAL_FIELDS``. The role is resolved
        SERVER-SIDE from the authenticated identity and ``strategies.user_id``; this route
        declares no parameter that could carry a role, and there is nothing here for a
        caller to claim.
    """
    try:
        service = await get_signal_service()

        detail = await service.get_signal_trace(user, signal_id, environment=environment)
        if not detail:
            raise HTTPException(
                status_code=404,
                detail={"error": "SIGNAL_NOT_FOUND", "message": f"Signal {signal_id} not found"}
            )

        return detail
    except (SignalRejected, OrderLifecycleRejected) as e:
        # Task 29.3: SIGNAL_ENVIRONMENT_UNRECOGNISED (400) and the 404 an environment filter
        # this database cannot answer produces. Same clause, same body shape as the list.
        raise HTTPException(status_code=e.http_status, detail=e.to_detail())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting signal {signal_id} for user {user.get('id')}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNAL_GET_FAILED", "message": str(e)}
        )


@router.get("/signals/{signal_id}/timeline")
@limiter.limit("200/minute")
async def get_signal_timeline(request: Request, 
    signal_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Get complete signal timeline.

    Returns all events in chronological order. The vocabulary is
    ``signal_service.SIGNAL_TIMELINE_EVENTS``, named there rather than transcribed here so
    the six words are spelled in one place:

    - SIGNAL_GENERATED
    - RISK_EVALUATED
    - ORDER_CREATED
    - EXCHANGE_RESPONSE
    - EXECUTED
    - POSITION_UPDATED

    BC-6 (vyomquant-ui-redesign task 12.6; Requirements 9.1, 9.2, 19.1, 19.2)
        ``POSITION_UPDATED`` is the sixth, added for Requirement 9.1's ninth stage, which
        had no backing record at all (design.md §10.1). It is derived from the same
        ``executed_at`` column ``EXECUTED`` is derived from, so it appears exactly once,
        always immediately after ``EXECUTED``, and never for a signal that has not
        executed. The five pre-spec events are unchanged in name, gate, order and payload.

        Its ``data`` reports the position CHANGE the row actually carries -
        ``symbol``, ``direction``, ``quantity_delta``, ``average_price``, ``trade_id``,
        ``realized_pnl`` - plus ``resulting_position``, which is ``null`` on every current
        database because nothing in this domain records the absolute holding a signal left
        behind. Every unreported member is named in ``data.not_available`` with a
        ``data.not_available_reason``, so the page renders a declared absence rather than a
        computed guess.
    """
    try:
        service = await get_signal_service()
        
        timeline = await service.get_signal_timeline(user, signal_id)
        
        return {
            "signal_id": signal_id,
            "timeline": timeline
        }
    except Exception as e:
        logger.error(f"Error getting timeline for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "TIMELINE_GET_FAILED", "message": str(e)}
        )


@router.put("/signals/{signal_id}/risk")
@limiter.limit("100/minute")
async def update_risk_decision(
    request: Request, 
    signal_id: str,
    body: RiskDecisionRequest = Body(...),
    user: dict = Depends(get_current_user)
):
    """
    Update signal with risk decision.
    
    Risk evaluation event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.update_risk_decision(
            user=user,
            signal_id=signal_id,
            risk_passed=body.risk_passed,
            risk_reason=body.risk_reason,
            position_size=body.position_size,
            capital=body.capital,
            exposure=body.exposure,
            expected_loss=body.expected_loss,
            expected_reward=body.expected_reward,
            drawdown_check=body.drawdown_check
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error updating risk decision for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "RISK_UPDATE_FAILED", "message": str(e)}
        )


@router.put("/signals/{signal_id}/order")
@limiter.limit("100/minute")
async def update_order(
    request: Request, 
    signal_id: str,
    body: OrderUpdateRequest = Body(...),
    user: dict = Depends(get_current_user)
):
    """
    Update signal with order information.
    
    Order generation and exchange response event in the execution audit trail.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.update_order(
            user=user,
            signal_id=signal_id,
            order_id=body.order_id,
            exchange_order_id=body.exchange_order_id,
            order_status=body.order_status,
            quantity=body.quantity,
            filled=body.filled,
            remaining=body.remaining,
            average_price=body.average_price,
            fees=body.fees,
            slippage=body.slippage,
            latency_ms=body.latency_ms
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error updating order for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "ORDER_UPDATE_FAILED", "message": str(e)}
        )


@router.put("/signals/{signal_id}/execution")
@limiter.limit("100/minute")
async def update_execution(
    request: Request, 
    signal_id: str,
    body: ExecutionUpdateRequest = Body(...),
    user: dict = Depends(get_current_user)
):
    """
    Update signal with execution and PnL information.

    Execution event in the execution audit trail.

    BC-6 (vyomquant-ui-redesign task 12.6; Requirements 9.1, 9.2, 19.1, 19.2)
        THIS IS ALSO WHERE REQUIREMENT 9.1'S STAGE 9 IS RECORDED. The same write that
        records the execution makes the timeline's ``POSITION_UPDATED`` event appear, because
        that event is derived from this row's ``executed_at`` (``signal_service``'s
        :func:`~backend_app.backend.signal_service._position_updated_event`). There is no
        second write, so this endpoint gained no new way to fail.

        THE REQUEST CONTRACT IS UNCHANGED. ``ExecutionUpdateRequest`` still declares
        exactly ``trade_id``, ``pnl`` and ``realized_pnl``; no field was added, required or
        optional. A position the caller does not report is reported as not-available on the
        event rather than solicited here or invented downstream.

        A REPEAT CALL IS SAFE. This is an overwrite of ``executed_at``, not an append, and
        the timeline is a derivation from the row - so calling it twice for the same signal
        leaves exactly one ``POSITION_UPDATED``, not two.
    """
    try:
        service = await get_signal_service()
        
        signal = await service.update_execution(
            user=user,
            signal_id=signal_id,
            trade_id=body.trade_id,
            pnl=body.pnl,
            realized_pnl=body.realized_pnl
        )
        
        return signal
    except Exception as e:
        logger.error(f"Error updating execution for signal {signal_id} for user {user['id']}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "EXECUTION_UPDATE_FAILED", "message": str(e)}
        )
