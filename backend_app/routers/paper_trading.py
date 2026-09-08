"""
routers/paper_trading.py — Paper Trading API Router for VyomQuant.

Endpoints for managing virtual paper accounts, paper orders, positions, and performance.
Enforces tenant isolation, parameter validation, rate limiting, and execution safety.

The Paper_Session lifecycle routes (task 28.1) sit below the six retained endpoints and are
purely additive: no existing path, method, body, rate limit or default-account semantic is
touched. See the "PAPER_SESSION LIFECYCLE" banner for what they wire, including the strategy
runtime and Signal_Trace recorder a started session's loop is driven by.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ``paper_channel`` is imported for ONE thing: task 28.2's ``GET /sessions/{id}/events`` calls its
# replay rather than reimplementing it, so the REST history and the WebSocket history cannot
# diverge (Requirements 19.8, 19.9). The import direction is router -> paper package, which is the
# same direction every other import here runs in.
from backend_app.backend.paper import paper_channel
from backend_app.backend.paper import paper_repository as paper_repo
from backend_app.backend.paper import paper_session_service as paper_sessions
from backend_app.backend.paper.errors import (
    NOT_FOUND,
    PAPER_MARKET_DATA_UNAVAILABLE,
    PaperError,
    paper_read_failed,
)
from backend_app.backend.paper.paper_market_feed import FeedHandle, PaperFeedError
from backend_app.backend.paper_trading_service import (
    PAPER_EXECUTION_ENVIRONMENT,
    get_paper_trading_service,
)
from backend_app.core.dependencies import get_current_user
from backend_app.core.rate_limit import limiter

# The router-facing UUID guard, imported rather than re-written: it is the one that answers 422
# for a malformed path parameter, and a second copy here would be a second definition of what a
# well-formed identifier is (Requirement 22.2). ``routers/library.py`` owns it and applies it to
# every path parameter and every caller identity it scopes a query by; these routes do the same.
from backend_app.routers.library import _safe_uuid

router = APIRouter()
logger = logging.getLogger("PaperTradingRouter")


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════════

class PaperOrderRequest(BaseModel):
    symbol: str = Field(..., example="BTC-USDT", description="Trading pair symbol")
    side: str = Field(..., example="buy", description="Order side: 'buy' or 'sell'")
    order_type: str = Field("market", example="market", description="Order type: 'market' or 'limit'")
    quantity: float = Field(..., gt=0, example=0.01, description="Order quantity")
    price: Optional[float] = Field(None, gt=0, example=65000.0, description="Limit price (required for limit orders)")
    strategy_id: Optional[str] = Field(None, description="Optional associated strategy ID")
    deployment_id: Optional[str] = Field(None, description="Optional associated deployment ID")


class PaperResetRequest(BaseModel):
    capital: float = Field(100000.0, gt=0, example=100000.0, description="New virtual starting capital")


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/account")
@limiter.limit("120/minute")
async def get_paper_account(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get the current user's virtual paper trading account details.

    The body is the service's, unchanged, and it already carries the additive
    ``execution_environment`` / ``is_simulated`` / ``session_id`` / ``stale`` /
    ``last_price_at`` fields of task 23.3 beside every field it carried before.
    """
    service = get_paper_trading_service()
    account = service.get_or_create_account(
        user["id"], access_token=user.get("access_token")
    )
    return account


@router.post("/account/reset")
@limiter.limit("30/minute")
async def reset_paper_account(
    request: Request,
    body: PaperResetRequest,
    user: dict = Depends(get_current_user)
):
    """Reset virtual paper account balance to specified capital and clear positions."""
    service = get_paper_trading_service()
    account = service.reset_account(
        user["id"], capital=body.capital, access_token=user.get("access_token")
    )

    try:
        from backend_app.core.notification_dispatcher import dispatch_user_notification
        await dispatch_user_notification(
            user_id=user["id"],
            event_type="paper_account_reset",
            category="system",
            severity="info",
            title="Paper Account Reset",
            message=f"Paper account balance reset to ${body.capital:,.2f}.",
            metadata={"capital": body.capital, "idempotency_key": f"paper_reset:{user['id']}:{account.get('reset_at', '')}"},
        )
    except Exception as notif_err:
        logger.debug(f"[PAPER] Reset notification error: {notif_err}")

    return {
        "status": "success",
        "message": f"Paper account reset with ${body.capital:,.2f} virtual capital",
        "account": account,
        # Task 23.3, additive only: Requirement 13.6's environment label and Requirement 28.1's
        # simulated marker on the envelope as well as inside ``account``, so a consumer that
        # reads only the envelope cannot mistake a simulated reset for a live one.
        "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
        "is_simulated": True,
        "session_id": None,
    }


@router.get("/positions")
@limiter.limit("120/minute")
async def get_paper_positions(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get all open paper trading positions with current unrealized PnL."""
    service = get_paper_trading_service()
    positions = service.get_positions(user["id"], access_token=user.get("access_token"))
    return {
        "positions": positions,
        "count": len(positions),
        "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
        "is_simulated": True,
        "session_id": None,
    }


@router.get("/orders")
@limiter.limit("120/minute")
async def get_paper_orders(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by status (OPEN, FILLED, CANCELLED)"),
    user: dict = Depends(get_current_user)
):
    """Get list of paper trading orders for user."""
    service = get_paper_trading_service()
    orders = service.get_orders(
        user["id"], status=status, access_token=user.get("access_token")
    )
    return {
        "orders": orders,
        "count": len(orders),
        "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
        "is_simulated": True,
        "session_id": None,
    }


@router.post("/orders")
@limiter.limit("60/minute")
async def place_paper_order(
    request: Request,
    body: PaperOrderRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: dict = Depends(get_current_user)
):
    """Place a new paper trading market or limit order."""
    service = get_paper_trading_service()
    try:
        order = await service.place_order(
            user_id=user["id"],
            symbol=body.symbol,
            side=body.side,
            order_type=body.order_type,
            quantity=body.quantity,
            price=body.price,
            strategy_id=body.strategy_id,
            deployment_id=body.deployment_id,
            idempotency_key=idempotency_key,
            access_token=user.get("access_token"),
        )

        try:
            from backend_app.core.notification_dispatcher import dispatch_user_notification
            await dispatch_user_notification(
                user_id=user["id"],
                event_type="paper_order_placed",
                category="trade",
                severity="info",
                title=f"Paper Order: {body.side.upper()} {body.symbol}",
                message=f"Paper {body.order_type} order for {body.quantity} {body.symbol} executed.",
                strategy_id=body.strategy_id,
                metadata={
                    "order_id": order.get("id"),
                    "symbol": body.symbol,
                    "side": body.side,
                    "quantity": body.quantity,
                    "price": body.price,
                    "idempotency_key": f"paper_order:{user['id']}:{order.get('id')}"
                }
            )
        except Exception as notif_err:
            logger.debug(f"[PAPER] Order notification error: {notif_err}")

        return {
            "status": "success",
            "order": order,
            "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
            "is_simulated": True,
            "session_id": None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "PAPER_ORDER_INVALID", "message": str(e)})
    except PaperError:
        # A structured paper refusal already carries its own code and status - 503
        # PAPER_PERSISTENCE_UNAVAILABLE, 503 PAPER_READ_FAILED, 409 PAPER_CONCURRENCY_CONFLICT,
        # 500 PAPER_INVARIANT_VIOLATION - and the registered handler renders it. Letting it fall
        # into the blanket branch below would relabel an unapplied migration as a generic
        # PAPER_ORDER_FAILED and lose the file name an operator needs.
        raise
    except Exception as e:
        logger.error(f"Paper order placement failed: {e}")
        raise HTTPException(status_code=500, detail={"error": "PAPER_ORDER_FAILED", "message": str(e)})


@router.delete("/orders/{order_id}")
@limiter.limit("60/minute")
async def cancel_paper_order(
    request: Request,
    order_id: str,
    user: dict = Depends(get_current_user)
):
    """Cancel an open paper limit order."""
    service = get_paper_trading_service()
    try:
        order = await service.cancel_order(
            user["id"], order_id, access_token=user.get("access_token")
        )
        return {
            "status": "cancelled",
            "order": order,
            "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
            "is_simulated": True,
            "session_id": None,
        }
    except PermissionError:
        # Retained branch. The service no longer raises this - another tenant's order is not
        # found rather than forbidden, because the tenant is a predicate on the read and
        # answering "forbidden" would confirm the order exists (Requirement 21.4).
        raise HTTPException(status_code=403, detail={"error": "FORBIDDEN", "message": "Access denied"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "CANCEL_FAILED", "message": str(e)})
    except PaperError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "CANCEL_ERROR", "message": str(e)})


@router.get("/trades")
@limiter.limit("120/minute")
async def get_paper_trades(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user)
):
    """Get trade fill history for paper trading."""
    service = get_paper_trading_service()
    trades = service.get_trades(
        user["id"], limit=limit, access_token=user.get("access_token")
    )
    return {
        "trades": trades,
        "count": len(trades),
        "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
        "is_simulated": True,
        "session_id": None,
    }


@router.get("/summary")
@limiter.limit("120/minute")
async def get_paper_summary(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Get complete paper trading performance summary, win rates, and account balance.

    The body is the service's, unchanged, and already carries task 23.3's additive fields.
    """
    service = get_paper_trading_service()
    summary = service.get_performance_summary(
        user["id"], access_token=user.get("access_token")
    )
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER_SESSION LIFECYCLE (task 28.1)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Seven routes, all additive. Nothing above this banner changes: the six endpoints
# ``api.paper`` already consumes keep their paths, methods, bodies, rate limits and their
# default-account (``session_id IS NULL``) semantics, which is Requirement 17.12 and is what
# ``tests/test_paper_api_shape_compatibility.py`` and the frozen
# ``tests/regression/baseline/paper_api_shape.json`` hold this file to.
#
# WHAT THIS LAYER DOES, AND WHAT IT REFUSES TO DO
# ----------------------------------------------
# It is a wiring layer and nothing else. Every validation, every state transition, every
# refusal and every write belongs to ``backend/paper/paper_session_service.py`` (tasks 27.1 to
# 27.5) and is CALLED from here, never reproduced here: there is no second admission decision,
# no second state gate, no second capital check and no second "no such session" answer in this
# file. What genuinely belongs to the route layer, and is therefore here, is exactly four
# things:
#
#   1. **The authenticated identity.** ``Depends(get_current_user)`` on every one of the seven
#      routes, and the caller's own id is the only identity that reaches a query. No user,
#      tenant, owner or session identity from a body, query or path participates in
#      authorisation (Requirement 21.1) - which is why the start body has no ``user_id``,
#      ``owner_id``, ``tenant_id`` or ``version_id`` field and why sending one is a 422.
#      ``version_id`` and ``source_strategy_id`` are produced by the entitlement decision,
#      server-side, and never accepted and never returned.
#   2. **The request shape.** One ``extra="forbid"`` model, parsed by hand so the 422 names the
#      offending field and echoes NO supplied value - see :func:`_parse_session_start`.
#   3. **The venue.** ``DEFAULT_EXCHANGE``, the platform's deployment fact, resolved the same
#      way ``routers/strategy_operations._fetch_preview_bars`` and ``ws_routes`` resolve it. It
#      is not a request field, for the reason SB-06 gives: a caller does not choose the feed.
#   4. **The two objects a stop releases.** ``start_session`` returns the ``FeedHandle`` and
#      ``spawn_session_loop`` returns the loop task rather than storing either, because a
#      registry inside the paper package would be a second place a session could be tracked
#      from. So the route layer holds them - :data:`_SESSION_RUNTIMES` - and hands them back to
#      ``stop_session``. The bound and the residual gap are stated on that dict.
#
# THE STRATEGY RUNTIME SEAM IS WIRED HERE, AND ONLY HERE
# ------------------------------------------------------
# ``spawn_session_loop(supabase, evaluate=…, plan=…, record_signal=…)`` is the seam where this
# layer installs the platform's ONE DAG execution runtime and its ONE Signal_Trace recorder.
# Both collaborators live outside the paper package, on this side of the seam, and neither is a
# paper-specific reimplementation:
#
#   * ``paper_session_runtime.build_session_evaluator`` - the ``(evaluate, plan)`` pair, over
#     ``DAGEngine.execute_plan`` (task 28.1).
#   * ``paper_session_runtime.build_session_signal_recorder`` - the ``record_signal=``, over
#     ``signal_service.generate_paper_signal``: the same ``public.signals`` write a ``LIVE``
#     signal takes, with ``environment='PAPER'``, ``paper_session_id`` set and
#     ``deployment_id`` NULL (task 29.2, Requirements 23.1, 23.5).
#
# The wiring is :func:`_session_loop_spawner` and it is performed at spawn time rather than
# before the call, because all four of its inputs are facts about a session that does not exist
# until ``start_session`` has created one: the session id, its symbol and timeframe, and the
# ``strategy_versions`` row the ENTITLEMENT DECISION named (``version_id`` is produced
# server-side and is never a request field). The two remaining arguments - the frozen
# ``SessionConfig`` and the session's Paper_Account - are the pipeline's own and arrive through
# the seam, which is why that seam is ``spawn(session_row, feed_handle, *, config,
# account_id)``: re-deriving the configuration here would be a second implementation of
# Requirement 16.12, free to disagree with the row the session stores. So a started session
# evaluates its strategy end to end, and ``no_session_loop``'s ERROR line - a session ``RUNNING``
# with nothing draining its feed - now means what it says rather than describing the normal case.
#
# One outstanding item, recorded rather than glossed: ``paper_session_service.PaperSignal``'s
# docstring states that ``signal_service.Signal.quantity`` is typed ``Optional[float]``, that a
# ``PaperSignal`` refuses a ``float`` rather than rounding it (Requirement 18.1), and that
# "widening that column's Python type is ``signal_service``'s change to make, not something this
# loop may paper over by rounding". The regression baseline pins that column's type, so a plan
# whose ``quantity`` parameter deserialises from JSONB as a binary float produces a contained,
# logged refusal per bar instead of an order for an amount nobody typed.


#: The stable code these routes answer an unaccepted or malformed request SHAPE with. Not a
#: member of the shared catalogue and deliberately so, for the reason
#: ``routers/library.DEPLOY_REQUEST_INVALID`` gives: ``design.md`` -> "The error code catalogue"
#: has no 422 entry, and "you sent a field this endpoint does not accept" is a request-shape
#: refusal rather than a paper-domain one. Spelled once, so the body, the log line and the tests
#: all read the same string.
PAPER_SESSION_REQUEST_INVALID = "PAPER_SESSION_REQUEST_INVALID"

#: The ONLY keys ``POST /api/paper/sessions`` accepts. Task 28.1: a strategy or listing
#: reference, the initial simulated capital in Minor_Units, its currency, the symbol, the
#: timeframe, and the optional idempotency key. Any other key - ``strategy_definition``,
#: ``graph``, ``compiled_plan``, ``plan``, ``version_id``, ``user_id``, ``owner_id``,
#: ``tenant_id``, ``subscription_id``, ``exchange_id`` - is a 422 that names it and echoes no
#: value (Requirements 7.6, 21.1, 22.2).
_SESSION_START_ALLOWED_FIELDS = frozenset(
    {
        "listing_id",
        "strategy_id",
        "initial_capital_minor",
        "currency",
        "symbol",
        "timeframe",
        "idempotency_key",
    }
)

#: What a session response carries: ``paper_repository.SESSION_LIST_SELECT``'s columns minus the
#: caller's own ``user_id``. DERIVED from that constant rather than re-listed, so the projection
#: a client reads cannot drift from the projection the statement selects - and so the three
#: columns that constant deliberately omits (``config``, ``version_id``, ``source_strategy_id``)
#: cannot be added to a body by editing this file alone.
SESSION_VIEW_FIELDS: Tuple[str, ...] = tuple(
    column.strip()
    for column in paper_repo.SESSION_LIST_SELECT.split(",")
    if column.strip() and column.strip() != "user_id"
)


@dataclass
class _SessionRuntime:
    """The two live objects one Paper_Session's stop has to release, and whose they are.

    ``user_id`` is held beside them so a lookup is scoped: an entry is only ever handed to the
    identity that created it. That is defence in depth rather than the enforcement - the stop
    path's own gate reads the session with ``user_id`` as a predicate and answers ``NOT_FOUND``
    for anybody else - but a process-local dict keyed only by session id is one refactor away
    from being the place a cross-tenant handle leaks out of.
    """

    user_id: str
    feed: FeedHandle
    loop_task: Any = None


#: The Paper_Sessions this PROCESS started, by session id. Task 28.1, and the route layer is
#: where it belongs: ``paper_session_service.stop_session`` takes ``feed=`` and ``loop_task=``
#: precisely because a registry inside that module "would be a second place a session could be
#: tracked from", and ``spawn_session_loop`` returns its task rather than storing it.
#:
#: **The bound.** One entry per session this process started, removed when that session is
#: stopped through this process. It holds no row, no balance and no market data - two object
#: references and an owner id.
#:
#: **The residual gap, stated rather than papered over.** A stop that arrives at a DIFFERENT
#: worker finds no entry here, so that worker passes ``feed=None`` and a Redis handle:
#: ``release_session_resources`` publishes the ``mds`` unsubscribe anyway (that half is
#: process-independent) and reports the LOCAL subscription as outstanding, which makes
#: ``StopOutcome.complete`` ``False`` and is why this route reports ``complete`` from the
#: outcome instead of from the call returning. The worker that holds the handle releases it when
#: its own shutdown reaches ``FeedHandle.close``. That gap is the transport's, not this dict's,
#: and it is the same gap ``release_session_resources``' own docstring records.
_SESSION_RUNTIMES: Dict[str, _SessionRuntime] = {}


def _remember_runtime(
    session_id: str, user_id: str, *, feed: FeedHandle, loop_task: Any
) -> None:
    """Record what this process must release when ``session_id`` is stopped."""
    _SESSION_RUNTIMES[str(session_id)] = _SessionRuntime(
        user_id=str(user_id), feed=feed, loop_task=loop_task
    )


def _take_runtime(session_id: str, user_id: str) -> Optional[_SessionRuntime]:
    """Remove and return this caller's runtime for ``session_id``, or ``None``.

    ``None`` for both "this process did not start it" and "it belongs to someone else", and the
    caller does nothing different in either case: it stops the session with no handle, which is
    a reported outcome (``StopOutcome.subscription_released is False``) and not a refusal. The
    refusal for another tenant's session is the service's, from the ownership-scoped read.
    """
    held = _SESSION_RUNTIMES.get(str(session_id))
    if held is None or held.user_id != str(user_id):
        return None
    return _SESSION_RUNTIMES.pop(str(session_id), None)


def _session_loop_spawner(supabase: Any) -> paper_sessions.SessionLoopSpawner:
    """The ``spawn_loop=`` this layer hands the pipeline: the platform's runtime, installed.

    ``spawn(session_row, feed_handle, *, config, account_id)`` - the seam
    ``paper_session_service.start_session`` calls once the session is ``RUNNING`` and its feed is
    open. Everything it builds is built INSIDE the spawn, and that is not an ordering accident:
    the session id, its symbol, its timeframe and its ``version_id`` are all facts about a row
    that does not exist until the pipeline has created it, and ``version_id`` in particular is a
    value the ENTITLEMENT DECISION produced server-side - never a request field, and never
    something this layer may name (Requirement 21.1). The ``config`` and ``account_id`` the loop
    steps with are the pipeline's own, handed over through the seam rather than re-derived here:
    a second derivation of Requirement 16.12's frozen configuration could disagree with the row
    the session stores.

    Three collaborators, none of them a paper-specific reimplementation:

      * ``paper_session_service.read_strategy_version`` - the SAME reader the pipeline's own
        lifecycle check used, on the ``version_id`` the created row records. Not a second
        admission decision and not a second resolution rule: one function, one answer to "which
        version does this session execute".
      * ``paper_session_runtime.build_session_evaluator`` - ``(evaluate, plan)`` over
        ``DAGEngine.execute_plan``, the platform's ONE evaluation path (Requirement 17.10).
        ``(None, None)`` when the version's plan cannot be resolved, which is a reported
        condition and not a silent one: ``resolve_session_plan`` logs at ERROR and
        ``step_session`` logs at ERROR on every bar. The loop is still spawned, because the feed
        still has to be drained and the equity still has to be revalued.
      * ``paper_session_runtime.build_session_signal_recorder`` - the ``PAPER`` Signal_Trace
        write, through ``signal_service.generate_paper_signal`` (Requirements 23.1, 23.5).

    The returned ``asyncio.Task`` is the pipeline's to hand back: it travels out on
    ``StartedSession.loop_task`` and into :data:`_SESSION_RUNTIMES`, because
    ``stop_session(loop_task=…)`` settles it before it commits a closing figure (Requirement
    17.8, step 3).

    Args:
        supabase: the caller's RLS-scoped handle. The loop's own statements, the version read and
            the Signal_Trace INSERT all run under the identity that started the session.
    """

    def spawn(
        session: Mapping[str, Any],
        feed: FeedHandle,
        *,
        # The pipeline's frozen ``SessionConfig``. Typed ``Any`` deliberately: this layer passes
        # it through untouched and reads nothing off it, so it does not need to know its shape.
        config: Any,
        account_id: Any,
    ) -> Any:
        # Lazy, for the reason ``paper_session_runtime``'s "EVERY IMPORT IS LAZY" section gives:
        # ``main.py`` imports this router at process start and the six retained ``/api/paper/*``
        # endpoints need none of numpy, pandas or the feature graph.
        from backend_app.backend.paper_session_runtime import (
            build_session_evaluator,
            build_session_signal_recorder,
        )

        version_row = paper_sessions.read_strategy_version(
            supabase, session.get("version_id")
        )
        evaluate, plan = build_session_evaluator(
            session_id=session.get("id"),
            symbol=session.get("symbol"),
            timeframe=session.get("timeframe"),
            version_row=version_row,
        )
        record_signal = build_session_signal_recorder(
            supabase, session=session, version_row=version_row
        )
        install = paper_sessions.spawn_session_loop(
            supabase,
            evaluate=evaluate,
            plan=plan,
            record_signal=record_signal,
        )
        return install(session, feed, config=config, account_id=account_id)

    return spawn


def _persistence(user: dict) -> Any:
    """The RLS-scoped Persistence_Layer handle these routes issue their statements through.

    ``PaperTradingService.persistence_client`` and not a fourth resolution rule: the same three
    sources in the same order (a bound handle, then a client carrying this caller's JWT so
    ``auth.uid()`` resolves and the ``paper_*`` row-level-security policies apply to the identity
    that made the request, then the anon singleton), and the same 503
    ``PAPER_PERSISTENCE_UNAVAILABLE`` when there is none. A handle resolved differently here
    would mean a request-scoped read losing its RLS scope on one endpoint and keeping it on
    another.
    """
    return get_paper_trading_service().persistence_client(user.get("access_token"))


def _session_view(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One ``paper_sessions`` row as a client may read it. :data:`SESSION_VIEW_FIELDS`, in order.

    A projection over named columns, never the row: ``insert_session`` and
    ``transition_session_state`` return whatever the table holds, which includes ``config`` (the
    frozen session configuration), ``version_id`` and ``source_strategy_id``. None of the three
    may reach a caller - the first is Protected_Logic-adjacent (Requirement 19.7) and the other
    two are internal identifiers the entitlement decision produced server-side (Requirements
    21.1, 22.9) - and a projection is what makes that structural instead of remembered.
    """
    return {field: row.get(field) for field in SESSION_VIEW_FIELDS}


def _session_envelope(**members: Any) -> Dict[str, Any]:
    """Every session response's common tail: the environment label and the simulated marker.

    The same two fields task 23.3 added to the six existing bodies, for the same reason
    (Requirements 13.6, 28.1): a consumer reading only the envelope must not be able to mistake
    a simulated figure for a live one. ``environment`` is not read off the row -
    ``chk_paper_session_environment`` pins the column to ``'PAPER'``, so a column with one
    possible value is not information and the envelope states it instead.
    """
    return {
        **members,
        "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
        "is_simulated": True,
    }


def _shape_refusal(message: str, **extra: Any) -> HTTPException:
    """A 422 naming what was wrong with the request shape, carrying no supplied value."""
    detail: Dict[str, Any] = {"error": PAPER_SESSION_REQUEST_INVALID, "message": message}
    detail.update(extra)
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
    )


class PaperSessionStartRequest(BaseModel):
    """The body ``POST /api/paper/sessions`` accepts, and it accepts nothing else.

    Six fields plus an optional idempotency key, which is Requirement 7.5's "execution
    parameters only" applied to a Paper_Session start: WHICH strategy (by the reference the
    caller is entitled to name), how much simulated capital, in which currency, on which market,
    at which timeframe. There is deliberately no ``strategy_definition``, ``graph``,
    ``compiled_plan``, ``plan``, ``buy_logic``, ``sell_logic``, ``indicators``,
    ``ml_model_path``, ``version_id``, ``user_id``, ``owner_id``, ``tenant_id`` or
    ``subscription_id`` field: the executable artifact is resolved server-side from the
    entitlement decision and never travels in either direction (Requirements 7.1, 7.5, 7.8,
    21.1), and there is no ``exchange_id`` field either, because the venue is a deployment fact
    (SB-06) rather than something a caller picks.

    ``model_config = ConfigDict(extra="forbid")`` is the second line of defence rather than the
    first: :func:`_parse_session_start` detects an unaccepted key against
    :data:`_SESSION_START_ALLOWED_FIELDS` before this model is constructed, because FastAPI's
    default ``RequestValidationError`` serialiser puts the rejected ``input`` VALUE in the 422
    body - which for a body that might carry somebody's strategy is the leak Requirement 7.6
    forbids. Both layers exist; only the hand-written one produces the response.

    ``initial_capital_minor`` is ``strict=True``: money crosses this boundary as an exact whole
    number of Minor_Units and as nothing else, so ``100000.5``, ``"100000"`` and ``true`` are
    each a 422 here rather than a float that gets rounded into a balance somewhere below. What
    the figure MEANS - positive, within the configured maximum, within the currency's precision -
    is ``paper_session_service.validate_capital``'s answer and is not second-guessed here: those
    refusals carry ``details["validation"]``, and re-stating any of them as a Pydantic bound
    would answer the same request with two different codes depending on which layer noticed.
    """

    model_config = ConfigDict(extra="forbid")

    listing_id: Optional[str] = Field(
        None,
        max_length=64,
        description="The Listing the session runs. Exactly one of listing_id / strategy_id.",
    )
    strategy_id: Optional[str] = Field(
        None,
        max_length=64,
        description="The strategy the session runs, resolved to its Listing server-side.",
    )
    symbol: str = Field(
        ..., min_length=1, max_length=50, description="The market, e.g. BTC/USDT."
    )
    timeframe: str = Field(
        ..., min_length=1, max_length=20, description="The candle timeframe, e.g. 1m."
    )
    initial_capital_minor: int = Field(
        ...,
        strict=True,
        description="The initial simulated capital, as an exact whole number of Minor_Units.",
    )
    currency: str = Field(
        paper_repo.DEFAULT_CURRENCY,
        min_length=3,
        max_length=8,
        description="The Paper_Account's currency.",
    )
    idempotency_key: Optional[str] = Field(
        None,
        min_length=1,
        max_length=128,
        description="An optional caller-supplied key, echoed back on the response.",
    )


async def _parse_session_start(request: Request) -> PaperSessionStartRequest:
    """Read the start body, refusing an unaccepted or malformed field with a value-free 422.

    Parsed by hand for the reason ``routers/library._parse_deploy_request`` documents and this
    endpoint inherits verbatim: FastAPI's default 422 serialiser includes the rejected ``input``
    value, so a caller who sends ``compiled_plan={…}`` or ``version_id="…"`` would see it echoed
    straight back. Task 28.1 requires the opposite - "a 422 that echoes **no** supplied value" -
    so the raw body is read here, its keys are checked against
    :data:`_SESSION_START_ALLOWED_FIELDS`, and every refusal below carries field NAMES only.

    The one-of check on the strategy reference is here as well, rather than as a model validator,
    for a plainer reason: a Pydantic model-level error carries an empty ``loc``, so the 422 it
    produced would name no field at all.
    """
    try:
        raw = await request.json()
    except Exception:
        raise _shape_refusal("Request body must be a JSON object.")

    if not isinstance(raw, dict):
        raise _shape_refusal("Request body must be a JSON object.")

    unexpected = sorted(k for k in raw if k not in _SESSION_START_ALLOWED_FIELDS)
    if unexpected:
        # Named, never echoed. The log line is the same: a field name is a fact about the
        # request, a field value may be somebody's strategy (Requirements 7.6, 19.7).
        logger.warning(
            "[paper-session] refusing a session start carrying unaccepted field(s) %s",
            unexpected,
        )
        raise _shape_refusal(
            "Unexpected field(s) in paper session start request.",
            unexpected_fields=unexpected,
        )

    try:
        body = PaperSessionStartRequest(**raw)
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
        raise _shape_refusal(
            "Invalid paper session start field(s).", invalid_fields=fields
        )

    named = [
        name
        for name, value in (
            ("listing_id", body.listing_id),
            ("strategy_id", body.strategy_id),
        )
        if value is not None and str(value).strip()
    ]
    if len(named) != 1:
        raise _shape_refusal(
            "Name exactly one strategy reference.",
            expected_one_of=["listing_id", "strategy_id"],
            supplied_fields=named,
        )
    return body


def _resolve_listing_key(supabase: Any, body: PaperSessionStartRequest) -> str:
    """The ``library_strategies.id`` the ONE admission decision is keyed on.

    ``entitlement_resolver.resolve`` is that decision (Requirement 11.10, property P-16) and it
    is keyed on a Listing, so a caller naming a ``strategy_id`` needs it translated. This is a
    KEY TRANSLATION and not a second decision: it reads one column of one row and then hands the
    result to the same ``resolve`` a ``listing_id`` would have gone to, which then answers
    ``OWNED``, ``SUBSCRIBED``, ``NOT_SUBSCRIBED``, ``EXPIRED``, ``SUBSCRIPTION_SUSPENDED`` or
    ``LISTING_UNAVAILABLE`` exactly as it always does. Nothing here admits, refuses or reads an
    ``author_id``.

    A ``strategy_id`` that no Listing backs is passed through UNCHANGED rather than refused here,
    so ``resolve`` answers ``LISTING_UNAVAILABLE`` for it - the same 409 as an unknown Listing,
    from the same branch, disclosing neither Protected_Logic nor an owner identity (Requirement
    7.11). A separate refusal in this function would be a second way to say the same thing, and
    two ways to say it are two things that can disagree.
    """
    if body.listing_id:
        return _safe_uuid(body.listing_id, "listing_id")

    strategy_key = _safe_uuid(body.strategy_id, "strategy_id")
    try:
        response = (
            supabase.table("library_strategies")
            .select("id")
            .eq("source_strategy_id", strategy_key)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - reported as a failure, never as "no listing"
        # A read that did not complete is not a read that found nothing: answering
        # LISTING_UNAVAILABLE here would tell an entitled caller their strategy is gone.
        logger.error(
            "[paper-session] the Listing lookup for a strategy reference did not complete: %s",
            exc,
        )
        raise paper_read_failed({"operation": "listing_lookup"}) from exc

    rows = getattr(response, "data", None) or []
    listing_id = str(rows[0].get("id")) if rows and rows[0].get("id") else ""
    return listing_id or strategy_key


def _recorded_measurements() -> Optional[Mapping[str, Any]]:
    """The market-data measurements the source selection decides on, or ``None``.

    ``None`` today, and that is a measurement result rather than a stub. Requirement 14.4's
    correctness floor is applied by ``market_data_latency.choose_market_data_source`` over
    RECORDED measurements of the two candidates, and the platform records them in exactly one
    place: ``reports/market_data_latency_decision.md``, written by
    ``tests/perf/test_market_data_latency.py``. That document is prose for an operator, not a
    machine-readable store, and the run on disk reports **BLOCKED** - neither candidate was
    measurable in this environment.

    So there is nothing to return, and nothing is invented to fill the gap: an unmeasured
    candidate fails the floor, ``start_session`` refuses BEFORE it creates anything, and the
    caller gets 409 ``PAPER_MARKET_DATA_UNAVAILABLE``. That is the honest answer -
    ``paper_market_feed``'s own words are that substituting a plausible measurement "would
    defeat the entire mechanism from the one place nothing downstream could detect it" - and a
    paper session in this deployment starts once the experiment has run against a real feed and
    a measured, floor-clean source exists. This function is the one seam where that record is
    read, so wiring it later changes one function and no route.
    """
    return None


def _paper_venue() -> str:
    """The venue every Paper_Session runs against: the server's ``DEFAULT_EXCHANGE``.

    A deployment fact and not a request field, resolved exactly as
    ``routers/strategy_operations._fetch_preview_bars``, ``strategy_service`` and
    ``api_ws/ws_routes`` resolve it, for the reason SB-06 gives: the caller chooses the market
    (symbol and timeframe), the operator chooses the feed. That is also why
    :class:`PaperSessionStartRequest` has no ``exchange_id`` field and why sending one is a 422.

    Raises:
        PaperError: 409 ``PAPER_MARKET_DATA_UNAVAILABLE`` when the setting is absent. The
            operator learns WHICH setting from the log line; the caller is told only that
            validated market data is not available, because a client has no business learning
            which venue this server would have used.
    """
    venue = (os.getenv("DEFAULT_EXCHANGE") or "").strip()
    if not venue:
        logger.error(
            "[paper-session] no Paper_Session can start: DEFAULT_EXCHANGE is not configured, so "
            "there is no venue whose market metadata a symbol could be validated against and no "
            "feed to subscribe to"
        )
        raise PaperError(
            PAPER_MARKET_DATA_UNAVAILABLE,
            details={"reason": "MARKET_DATA_VENUE_NOT_CONFIGURED"},
        )
    return venue


def _mds_redis() -> Any:
    """The Redis handle a stop publishes the ``mds`` unsubscribe through, or ``None``.

    Only consulted when this process does not hold the session's :class:`FeedHandle` - the
    handle publishes through the same one function otherwise. The resolution mirrors
    ``paper_market_feed._resolve_redis``, which is the one place the platform's handle is named,
    with one difference: it returns ``None`` instead of raising. A stop has already transitioned
    the session by the time the release runs, so raising here would abort a stop that HAS
    happened; ``release_session_resources`` reports an unpublished unsubscribe as outstanding
    instead, which is what makes ``StopOutcome.complete`` ``False``.
    """
    try:
        from backend_app.core.cache import redis_manager

        return getattr(redis_manager, "redis", redis_manager)
    except Exception as exc:  # noqa: BLE001 - reported, and the stop still reports incomplete
        logger.error(
            "[paper-session] no Redis handle could be resolved, so a stop performed without the "
            "session's feed handle cannot publish the mds unsubscribe: %s",
            exc,
        )
        return None


async def _audit_session_reference_refused(*, actor_id: str, session_id: str) -> None:
    """Record Requirement 21.4's Audit_Log entry for a session reference that answered NOT_FOUND.

    Written for BOTH causes - no such session, and another tenant's session - because the read
    that produced the refusal carried ``user_id`` as a predicate and therefore cannot tell them
    apart. That is the point: one entry shape, one action, so the trail records the attempt
    without the API having had to distinguish the two cases in order to log it (Requirements
    21.4, 22.9's byte-identical answer).

    Through the existing ``core.audit_trail.StrategyAuditLogger`` and its existing
    ``MARKETPLACE_CROSS_TENANT_ATTEMPT`` member - no second audit facility and no new member -
    with ``resource_type="paper_session"``, the spelling ``paper_market_feed`` already uses.
    Imported at call time, the way ``paper_market_feed._audit_mock_interface_refusal`` does it.
    A failure to write never changes the refusal: the 404 is returned either way, and the gap is
    logged where the record would have been.
    """
    try:
        from backend_app.core.audit_trail import (
            StrategyAuditAction,
            get_strategy_audit_logger,
        )

        await get_strategy_audit_logger().log(
            StrategyAuditAction.MARKETPLACE_CROSS_TENANT_ATTEMPT,
            actor_id=str(actor_id),
            resource_type="paper_session",
            resource_id=str(session_id),
            reason=(
                "A Paper_Session reference resolved to no session for the authenticated "
                "identity. The read is scoped by that identity, so this entry is written "
                "identically whether the session does not exist or belongs to another user."
            ),
            metadata={"operation": "paper_session_reference"},
        )
    except Exception as exc:  # noqa: BLE001 - never turns a 404 into a 500
        logger.error(
            "[paper-session] the Requirement 21.4 audit entry for a refused session reference "
            "was not written: %s",
            exc,
        )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paper/sessions — start (10/60s)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def start_paper_session(
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    user: dict = Depends(get_current_user),
):
    """Start a Paper_Session. Requirements 17.3, 17.4, 17.5, 17.9, 17.13, 21.1, 22.2, 22.4.

    The whole of the pipeline is ``paper_session_service.start_session``, in its order, and this
    handler adds nothing to it: entitlement, then the simulator guard, then the version's
    lifecycle, then the capital, then the exchange market metadata, then the timeframe, then the
    strategy/market compatibility, then the per-user concurrent cap - and only then the three
    creates, the feed, and ``RUNNING``. Every refusal leaves no Paper_Session, no paper order, no
    paper balance and no market-data subscription (Requirement 17.13), which is a property of
    that function's ordering rather than of anything here.

    What this handler contributes: the authenticated identity (never an identifier from the
    request - Requirement 21.1), the value-free 422 on an unaccepted field, the venue from the
    server's own configuration, and the ``FeedHandle`` recorded in :data:`_SESSION_RUNTIMES` so a
    later stop can release it.

    The idempotency key is accepted, shape-checked and echoed back, and it is NOT a duplicate
    guard: ``paper_sessions`` carries no idempotency column, so there is nothing for a repeated
    start to collide with. It is reported honestly instead of being described as something it is
    not - a client that needs at-most-once starting has the per-user concurrent cap
    (Requirement 27.4) and this endpoint's 10/60s limit, and a stored key would need a column and
    a unique index that 009 does not declare.

    Raises:
        HTTPException: 422 for a request shape this endpoint does not accept. Names the
            field(s), echoes no value.
        PaperError: 403/409/422 ``PAPER_START_REFUSED`` naming the validation, 429
            ``PAPER_SESSION_LIMIT_REACHED``, 409 ``PAPER_MARKET_DATA_UNAVAILABLE``, 500
            ``PAPER_SIMULATOR_MISCONFIGURED``, 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` /
            ``PAPER_READ_FAILED``. Each is re-raised unflattened so the registered structured
            handler renders its own code and status (Requirement 22.9).
    """
    body = await _parse_session_start(request)
    caller_id = _safe_uuid(user["id"], "user_id")
    supabase = _persistence(user)

    key = body.idempotency_key or idempotency_key
    listing_key = _resolve_listing_key(supabase, body)

    try:
        started = await paper_sessions.start_session(
            supabase,
            user,
            listing_id=listing_key,
            exchange_id=_paper_venue(),
            symbol=body.symbol,
            timeframe=body.timeframe,
            initial_capital_minor=body.initial_capital_minor,
            currency=body.currency,
            measurements=_recorded_measurements(),
            # The strategy runtime, the plan and the Signal_Trace recorder, installed by the layer
            # that owns them. Without it the session would be ``RUNNING`` with nothing draining its
            # feed - no bar, no signal, no order, no equity point - and every figure it served
            # would be a truthful zero (Requirement 17.10, and Requirement 28.5's distinction
            # between "no trades" and "not measured").
            spawn_loop=_session_loop_spawner(supabase),
        )
    except PaperError:
        raise
    except PaperFeedError as exc:
        # The feed's internal conditions - no transport, a handshake that did not complete - are
        # not caller-facing types. Reported as the catalogue's 409 rather than as a 500: the
        # request was well formed and nothing was created that a retry would duplicate.
        logger.error("[paper-session] the market-data subscription could not be opened: %s", exc)
        raise PaperError(
            PAPER_MARKET_DATA_UNAVAILABLE, details={"reason": "FEED_NOT_OPENED"}
        ) from exc
    except paper_repo.PaperRepositoryError as exc:
        logger.error("[paper-session] a statement the session start needed failed: %s", exc)
        raise paper_read_failed({"operation": "session_start"}) from exc
    except ValueError as exc:
        # A value the schema admits but the domain does not spell the same way - an unreadable
        # currency, a blank symbol. 422 without the value, for the same reason as above.
        logger.warning("[paper-session] refusing a session start: %s", exc)
        raise _shape_refusal("The paper session start request could not be accepted.")

    session_id = started.session_id
    _remember_runtime(
        session_id,
        caller_id,
        feed=started.feed,
        # The loop the pipeline spawned through :func:`_session_loop_spawner`, held so the stop
        # can settle it BEFORE it writes a closing figure (Requirement 17.8, step 3). ``None``
        # only when the spawn itself failed - which ``_spawn`` logs at ERROR against this session
        # id - and ``stop_session`` reports ``loop_settled=False`` for that case.
        loop_task=started.loop_task,
    )
    logger.info(
        "[paper-session] session %s started for %s on %s at %s (source %s, loop %s)",
        session_id,
        caller_id,
        started.session.get("symbol"),
        started.session.get("timeframe"),
        started.feed.market_data_source,
        # The task itself: its repr names it and states whether it is pending, and ``None`` is
        # printed as ``None`` rather than being dressed up as something.
        started.loop_task,
    )
    return _session_envelope(
        status="started",
        session_id=session_id,
        session=_session_view(started.session),
        idempotency_key=key,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paper/sessions — the caller's own sessions (120/60s)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/sessions")
@limiter.limit("120/minute")
async def list_paper_sessions(
    request: Request,
    session_state: Optional[str] = Query(
        None, description="Filter by state: CREATED, RUNNING, PAUSED or STOPPED."
    ),
    limit: Optional[int] = Query(
        None, ge=1, le=paper_repo.SESSION_LIST_CAP, description="Rows, newest first."
    ),
    user: dict = Depends(get_current_user),
):
    """The authenticated caller's own Paper_Sessions, newest first. Requirements 21.5, 17.3.

    ``paper_repository.list_sessions`` carries the caller's id as a PREDICATE ON THE STATEMENT,
    which is the whole of Requirement 21.5: another tenant's session is never fetched rather
    than fetched and dropped afterwards. There is no post-retrieval ownership filter in this
    handler - not a redundant one and not a hidden one - because there is nothing here to
    filter: the rows that arrive are the caller's.

    ``session_state`` is checked against ``paper_repository.SESSION_STATES``
    (``chk_paper_session_state`` verbatim) before the read, so an unrecognised label is a 422
    naming the permitted values rather than a statement that quietly matches nothing and reads
    as "you have no sessions".
    """
    caller_id = _safe_uuid(user["id"], "user_id")
    state = None if session_state is None else str(session_state).strip().upper()
    if state is not None and state not in paper_repo.SESSION_STATES:
        raise _shape_refusal(
            "Unknown session_state.", permitted=list(paper_repo.SESSION_STATES)
        )

    supabase = _persistence(user)
    try:
        rows = paper_repo.list_sessions(
            supabase, caller_id, session_state=state, limit=limit
        )
    except paper_repo.PaperRepositoryError as exc:
        # Never an empty list: "the read did not complete" reported as "you have no sessions"
        # would tell a user their history is gone (Requirements 17.2, 28.3).
        logger.error("[paper-session] the session list read did not complete: %s", exc)
        raise paper_read_failed({"operation": "session_list"}) from exc

    sessions = [_session_view(row) for row in rows]
    return _session_envelope(sessions=sessions, count=len(sessions))


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paper/sessions/{session_id} — one session (120/60s)
# ─────────────────────────────────────────────────────────────────────────────


async def _owned_session(session_id: str, user: dict) -> Tuple[Dict[str, Any], str, str, Any]:
    """The gate every session-scoped READ passes through. Requirements 21.4, 21.5, 28.3.

    Returns ``(row, session_id, caller_id, supabase)`` for a session the AUTHENTICATED caller owns.
    One implementation for ``GET /sessions/{id}`` and for task 28.2's seven sub-resources, for the
    same reason :func:`_operate` is one implementation for the four operations: eight copies of a
    scope, a refusal and an audit entry are eight things that can disagree, and the one thing they
    must not disagree about is what another tenant's session looks like.

    ``_safe_uuid`` on the path parameter, then ``read_session_summary`` - whose ``user_id`` is a
    PREDICATE ON THE STATEMENT, so another tenant's row is never fetched rather than fetched and
    dropped (Requirement 21.5). ``None`` therefore means both "no such session" and "somebody
    else's session", and this function cannot tell them apart: that is what makes the 404 it raises
    byte-identical for the two cases (Requirements 21.4, 22.9). The refusal itself is
    ``paper_session_service.session_not_found``, the ONE construction site for it.

    Raises:
        HTTPException: 422 for a malformed identifier.
        PaperError: 404 ``NOT_FOUND`` for an unknown or another tenant's session, audited first;
            503 ``PAPER_READ_FAILED`` when the statement DID NOT COMPLETE - never a 404, because
            answering "no such session" for a failed read would tell a caller their session is
            gone (Requirement 28.3).
    """
    sid = _safe_uuid(session_id, "session_id")
    caller_id = _safe_uuid(user["id"], "user_id")
    supabase = _persistence(user)

    try:
        row = paper_repo.read_session_summary(supabase, caller_id, sid)
    except paper_repo.PaperRepositoryError as exc:
        logger.error("[paper-session] the session read did not complete: %s", exc)
        raise paper_read_failed({"operation": "session_read"}) from exc

    if row is None:
        await _audit_session_reference_refused(actor_id=caller_id, session_id=sid)
        raise paper_sessions.session_not_found(sid)

    return dict(row), sid, caller_id, supabase


@router.get("/sessions/{session_id}")
@limiter.limit("120/minute")
async def get_paper_session(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One of the caller's own Paper_Sessions, including its feed record. Requirement 17.3.

    The body carries ``feed_state``, ``market_data_source`` and ``event_sequence`` - task 28.1
    names those three explicitly - because :data:`SESSION_VIEW_FIELDS` is derived from
    ``paper_repository.SESSION_LIST_SELECT``, which carries them. It carries no ``config``, no
    ``version_id`` and no ``source_strategy_id``.

    ``_safe_uuid`` on the path parameter, then the ownership scope IN the read
    (``read_session_summary`` predicates on ``user_id``). Another user's session and an unknown
    one therefore answer byte-identically: the same 404 ``NOT_FOUND``, the same public sentence,
    and ``details`` carrying only the identifier the caller themselves supplied - built by
    ``paper_session_service.session_not_found``, which is the ONE construction site for that
    refusal, so this route cannot drift from the four operation routes below - nor from task 28.2's
    seven sub-resource reads, which pass through the same :func:`_owned_session` gate.
    """
    row, sid, _caller_id, _supabase = await _owned_session(session_id, user)
    return _session_envelope(session_id=sid, session=_session_view(row))


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/paper/sessions/{id}/pause | /resume | /stop | /reset — 60/60s each
# ─────────────────────────────────────────────────────────────────────────────


async def _operate(
    operation: str, session_id: str, user: dict
) -> Tuple[Any, str, str, Any]:
    """One session operation, end to end. The shared body of the four routes below.

    Returns ``(outcome, session_id, caller_id, supabase)`` so each route can add what its own
    operation reports beyond the transition. One implementation rather than four, for the reason
    ``apply_operation`` gives about itself: four copies of a gate, a scope and an error
    translation are four things that can disagree.

    The state machine is the service's and is not re-stated here. "Idempotent per the state
    machine" (task 28.1) means exactly what Requirement 17.14 says it means: a second pause of a
    ``PAUSED`` session, a resume of a ``STOPPED`` one, a reset of a ``RUNNING`` one is 409
    ``PAPER_SESSION_OPERATION_REJECTED`` naming BOTH the current state and the rejected
    operation, with the session's state, orders, positions, balances and persisted history
    unchanged - because the gate runs before any statement is issued. This handler does not
    swallow that 409 into a 200: reporting a pause that did not happen as one is the kind of
    quiet success the whole refusal exists to prevent.
    """
    sid = _safe_uuid(session_id, "session_id")
    caller_id = _safe_uuid(user["id"], "user_id")
    supabase = _persistence(user)

    try:
        if operation == paper_sessions.OPERATION_PAUSE:
            outcome: Any = await paper_sessions.pause_session(supabase, user, sid)
        elif operation == paper_sessions.OPERATION_RESUME:
            outcome = await paper_sessions.resume_session(supabase, user, sid)
        elif operation == paper_sessions.OPERATION_RESET:
            outcome = await paper_sessions.reset_session(supabase, user, sid)
        elif operation == paper_sessions.OPERATION_STOP:
            held = _take_runtime(sid, caller_id)
            outcome = await paper_sessions.stop_session(
                supabase,
                user,
                sid,
                feed=None if held is None else held.feed,
                loop_task=None if held is None else held.loop_task,
                # Only reached when this process holds no handle: the handle publishes the
                # unsubscribe itself, and resolving a second transport beside it would be a
                # second way to release one subscription.
                redis=None if held is not None else _mds_redis(),
            )
        else:  # pragma: no cover - the four routes below pass one of the four constants
            raise ValueError(f"{operation!r} is not a Paper_Session route operation")
    except PaperError as exc:
        if exc.code == NOT_FOUND:
            await _audit_session_reference_refused(actor_id=caller_id, session_id=sid)
        raise
    except paper_repo.PaperRepositoryError as exc:
        logger.error(
            "[paper-session] a statement the %s of session %s needed failed: %s",
            operation,
            sid,
            exc,
        )
        raise paper_read_failed({"operation": f"session_{operation}"}) from exc

    return outcome, sid, caller_id, supabase


def _operation_body(outcome: Any, session_id: str) -> Dict[str, Any]:
    """What every accepted operation reports: the transition, recorded and timestamped.

    Requirement 17.7's "record each accepted operation with the requesting user, the resulting
    state and a timestamp" is the ``paper_events`` row ``apply_operation`` wrote;
    ``event_sequence`` is that row's per-session sequence, which is what a client resumes a
    Paper_Channel replay from.
    """
    return _session_envelope(
        status=str(outcome.to_state).lower(),
        session_id=session_id,
        operation=outcome.operation,
        from_state=outcome.from_state,
        to_state=outcome.to_state,
        event_sequence=outcome.event_sequence,
        at=outcome.at.isoformat(),
        session=_session_view(outcome.session),
    )


@router.post("/sessions/{session_id}/pause")
@limiter.limit("60/minute")
async def pause_paper_session(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """``RUNNING -> PAUSED``. Requirements 17.7, 17.14, 21.4.

    The loop stops stepping because it re-reads ``session_state`` from the Persistence_Layer, so
    a pause taken on one worker is honoured by the worker holding the loop. The subscription
    stays open - that is the whole difference between a pause and a stop - and no order,
    position or balance moves.
    """
    outcome, sid, _caller, _sb = await _operate(
        paper_sessions.OPERATION_PAUSE, session_id, user
    )
    return _operation_body(outcome, sid)


@router.post("/sessions/{session_id}/resume")
@limiter.limit("60/minute")
async def resume_paper_session(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """``PAUSED -> RUNNING``. Requirements 17.7, 17.14, 21.4.

    Nothing is back-filled across the pause: the session resumes from the first event that
    passes validation, and the candles that closed while it was paused are candles it never saw
    (Requirement 14.9).
    """
    outcome, sid, _caller, _sb = await _operate(
        paper_sessions.OPERATION_RESUME, session_id, user
    )
    return _operation_body(outcome, sid)


@router.post("/sessions/{session_id}/stop")
@limiter.limit("60/minute")
async def stop_paper_session(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """``RUNNING`` or ``PAUSED`` -> ``STOPPED``. Requirement 17.8, in its order.

    ``complete`` IS Requirement 17.8's "report the stop as complete only after those steps have
    committed", and it is read off ``StopOutcome.complete`` rather than inferred from the call
    returning. It is ``True`` only when all four happened: the finals committed (the closing
    equity point and the ``paper_metrics`` row), the ``mds:commands`` unsubscribe was published,
    this process's own subscription was released, and the Paper_Channel registrations were
    closed. Any one outstanding makes it ``False`` and names itself in ``outstanding`` - which is
    the honest answer for the real case that produces it: a stop that reached a worker other than
    the one holding the ``FeedHandle`` releases the ``mds`` stream but not the local
    subscription.

    The session is ``STOPPED`` either way - the transition is committed before the release runs -
    so this is a 200 with ``complete: false``, not a failure. ``release_session_resources`` and
    ``commit_session_finals`` are both public and both idempotent-safe, which is how an
    incomplete stop is finished.
    """
    outcome, sid, _caller, _sb = await _operate(
        paper_sessions.OPERATION_STOP, session_id, user
    )
    outstanding = [
        name
        for name, done in (
            ("finals", outcome.finals.committed),
            ("mds_unsubscribe", outcome.mds_released),
            ("local_subscription", outcome.subscription_released),
            ("channel_registrations", outcome.registrations_closed is not None),
        )
        if not done
    ]
    body = _operation_body(outcome, sid)
    body.update(
        {
            # The claim, and nothing but the claim. Requirement 17.8.
            "complete": outcome.complete,
            "outstanding": outstanding,
            "finals_committed": outcome.finals.committed,
            # Present only when something is absent, and it says WHICH thing - never a zero
            # standing in for a figure that could not be computed (Requirements 28.3, 28.5).
            "finals_reason": outcome.finals.reason,
            "stale": outcome.finals.stale,
            "loop_settled": outcome.loop_settled,
            "registrations_closed": outcome.registrations_closed,
        }
    )
    return body


@router.post("/sessions/{session_id}/reset")
@limiter.limit("60/minute")
async def reset_paper_session(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """``STOPPED -> CREATED``. Requirement 17.15.

    Balances back to the RECORDED initial simulated capital - the exact integer
    ``paper_sessions.initial_capital_minor`` holds, never a re-derived figure - every open order
    ``CANCELLED``, every open position closed to size zero, and a new equity series begun at
    ``previous + 1``. **Nothing is deleted**: the pre-reset orders, fills, trades, metrics and
    equity snapshots stay readable, which is structural rather than promised, because
    ``paper_session_service`` contains no ``DELETE`` at all.

    The money in this body is the integer Minor_Units figure and only that. The major-unit
    ``Decimal`` the reset restored is deliberately not serialised: it would reach a client as a
    binary float, and an exact figure that arrives inexact is worse than one that does not
    arrive.
    """
    outcome, sid, _caller, _sb = await _operate(
        paper_sessions.OPERATION_RESET, session_id, user
    )
    body = _operation_body(outcome, sid)
    body.update(
        {
            "initial_capital_minor": outcome.initial_capital_minor,
            "cancelled_orders": list(outcome.cancelled_orders),
            "orders_not_cancellable": list(outcome.orders_not_cancellable),
            "closed_positions": list(outcome.closed_positions),
            "previous_series_index": outcome.previous_series_index,
            "series_index": outcome.series_index,
        }
    )
    return body


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER_SESSION SUB-RESOURCE READS (task 28.2)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Seven more routes, all additive, all reads, all 120/60s. Requirements 17.2, 19.8, 19.9, 21.5,
# 27.2. Nothing above changes: task 28.1's seven keep their paths, bodies and limits, and so do the
# eight endpoints ``api.paper`` already consumes (Requirement 17.12).
#
# ONE SELECT PER SUB-RESOURCE, AND WHAT THE OTHER ONE IS
# -----------------------------------------------------
# Each of the seven issues exactly ONE statement for the rows it serves - ``paper_repository``'s
# existing ``get_orders`` / ``get_fills`` / ``get_positions`` / ``get_trades`` /
# ``get_equity_snapshots`` / ``get_metrics`` / ``read_session_events``, each with its own explicit
# column projection and both ``user_id`` and ``session_id`` as predicates. There is no per-row round
# trip anywhere: no loop issues a statement per order, and no response field is filled in by a
# second read of a child table. That is Requirement 27.2's "fixed number of database round trips
# independent of the number of entries returned", and it is a property of calling those functions
# rather than of anything written here.
#
# Beside it there is exactly one more statement, the same one on all seven: the ownership gate,
# :func:`_owned_session`. It is what makes an unknown session and another tenant's session answer
# byte-identically (Requirement 21.4) instead of both answering ``[]`` - "you have no orders" is a
# claim about the caller's own session, and making it about a session that is not theirs would be
# both a lie and a disclosure. Two statements, fixed, whatever the row count.
#
# ``/events`` issues the gate plus the WebSocket replay's own two reads, which is precisely the
# statement sequence a socket subscriber's replay issues (authorise, then replay) - see
# :func:`get_paper_session_events`.
#
# WHAT NO RESPONSE CARRIES
# ------------------------
# No plan, node, indicator, feature, ML-inference, graph or version field (Requirements 19.7,
# 23.3). Made structural in two independent ways rather than remembered:
#
#   1. **A projection over a derived allow-list.** Each view's field tuple is DERIVED from the
#      repository ``*_SELECT`` constant the statement selects, minus
#      :data:`ROW_WITHHELD_COLUMNS` - so a response cannot carry a column its statement did not
#      ask for, and a column the repository adds later arrives in the response only if it is not
#      withheld. The rows are never returned whole.
#   2. **An assertion at the boundary.** :func:`_assert_no_protected_field` walks what is about to
#      be serialised and refuses to serve it if any key names Protected_Logic. ``paper_events``'s
#      payloads are already a safe projection at the emitter - every one is a closed Pydantic model
#      (``extra="forbid"``) of named fields - but this layer asserts it instead of trusting it,
#      because the emitter's guarantee is a fact about today's payload models and this is the last
#      place before the wire.


#: The columns a sub-resource row never carries to a client. The same idiom
#: :data:`SESSION_VIEW_FIELDS` applies to the session projection, with a wider withheld set because
#: these rows carry three internal values a session row does not:
#:
#: * ``user_id`` - the caller telling themselves who they are, and the predicate the read was
#:   already scoped by. Withheld on the session view too, for the same reason.
#: * ``account_id`` - the ``paper_accounts`` row id. An internal identifier no ``/api/paper``
#:   endpoint accepts and no client can use, which is Requirement 22.9's "SHALL NOT return an
#:   internal identifier to a client".
#: * ``version`` - ``paper_positions``' optimistic-concurrency counter. There is no client-driven
#:   compare-and-set on this surface, so it is an internal write-path detail; it is also the one
#:   column on these tables whose NAME would put a "version field" in a response, which task 28.2
#:   forbids outright.
#: * ``fingerprint`` - ``paper_orders``' internal dedupe hash (task 25.3's probe compares it). A
#:   derived artifact of the write path, not a figure the simulator produced.
ROW_WITHHELD_COLUMNS: FrozenSet[str] = frozenset(
    {"user_id", "account_id", "version", "fingerprint"}
)

#: Substrings that name Protected_Logic, an executable artifact or a strategy version. Checked
#: against every KEY of every mapping a sub-resource response is about to carry, at any depth
#: (:func:`_assert_no_protected_field`).
#:
#: ``version_id`` rather than ``version``: the frames ``/events`` serves carry ``schema_version``,
#: which Requirement 19.3 REQUIRES on every event and which is the event contract's version rather
#: than a strategy's. The bare column ``version`` is kept out by
#: :data:`ROW_WITHHELD_COLUMNS` instead, where it can be withheld without also banning the field
#: Requirement 19.3 mandates.
PROTECTED_FIELD_TOKENS: Tuple[str, ...] = (
    "plan",
    "node",
    "indicator",
    "feature",
    "graph",
    "inference",
    "ml_model",
    "model_path",
    "compiled",
    "definition",
    "buy_logic",
    "sell_logic",
    "version_id",
    "source_strategy",
)


def _view_fields(select: str) -> Tuple[str, ...]:
    """One repository projection's columns, in order, minus :data:`ROW_WITHHELD_COLUMNS`.

    DERIVED from the ``*_SELECT`` constant the statement is issued with, never re-listed, so the
    projection a client reads cannot drift from the projection the statement selects - the same
    reason :data:`SESSION_VIEW_FIELDS` is derived from ``SESSION_LIST_SELECT``.
    """
    return tuple(
        column.strip()
        for column in select.split(",")
        if column.strip() and column.strip() not in ROW_WITHHELD_COLUMNS
    )


#: What each sub-resource row carries, in its table's column order.
ORDER_VIEW_FIELDS: Tuple[str, ...] = _view_fields(paper_repo.ORDER_SELECT)
FILL_VIEW_FIELDS: Tuple[str, ...] = _view_fields(paper_repo.FILL_SELECT)
POSITION_VIEW_FIELDS: Tuple[str, ...] = _view_fields(paper_repo.POSITION_SELECT)
TRADE_VIEW_FIELDS: Tuple[str, ...] = _view_fields(paper_repo.TRADE_SELECT)
EQUITY_VIEW_FIELDS: Tuple[str, ...] = _view_fields(paper_repo.EQUITY_SNAPSHOT_SELECT)
METRICS_VIEW_FIELDS: Tuple[str, ...] = _view_fields(paper_repo.METRICS_SELECT)


def _protected_keys(value: Any) -> List[str]:
    """Every key at any depth of ``value`` that names Protected_Logic. ``[]`` when there is none.

    Recursive, because a payload member may itself be a mapping - ``paper_session_stopped`` carries
    ``final_metrics`` as one - and a nested key is on the wire exactly as a top-level one is.
    """
    found: List[str] = []
    if isinstance(value, Mapping):
        for key, member in value.items():
            name = str(key).lower()
            if any(token in name for token in PROTECTED_FIELD_TOKENS):
                found.append(str(key))
            found.extend(_protected_keys(member))
    elif isinstance(value, (list, tuple)):
        for member in value:
            found.extend(_protected_keys(member))
    return found


def _assert_no_protected_field(value: Any, operation: str) -> None:
    """Refuse to serve ``value`` if any key in it names Protected_Logic. Requirements 19.7, 23.3.

    The response is refused rather than quietly stripped, for two reasons. A silently stripped
    field is a response that differs from the frames a Paper_Channel subscriber receives, which is
    exactly the divergence between the REST and socket histories task 28.2 exists to prevent; and a
    projection that has started dropping columns is a defect an operator needs to see, not one to
    absorb one field at a time.

    ``PAPER_READ_FAILED`` and deliberately not ``PAPER_INVARIANT_VIOLATION``: the second one's
    public sentence says the operation was undone and balances are unchanged, which for a read
    would assert a rollback that never happened. This code's sentence is the true one - the
    information could not be shown and nothing was changed - and the operator learns WHICH field
    from the log line, which is where a field name belongs (Requirement 22.9).
    """
    offenders = sorted(set(_protected_keys(value)))
    if not offenders:
        return
    logger.error(
        "[paper-session] refusing to serve the %s response: it carries field(s) %s, which name "
        "Protected_Logic (Requirements 19.7, 23.3). No Paper_Trading_API response may carry a "
        "plan, node, indicator, feature, graph or strategy version",
        operation,
        offenders,
    )
    raise paper_read_failed({"operation": f"{operation}_projection"})


def _row_view(row: Mapping[str, Any], fields: Tuple[str, ...]) -> Dict[str, Any]:
    """One row as a client may read it: ``fields``, in order, and nothing the row carries besides.

    A projection over named columns, never the row, for the reason :func:`_session_view` gives: the
    repository returns what its statement selected, and a handler that returned the row would carry
    whatever a later projection widening added.
    """
    return {field: row.get(field) for field in fields}


def _sub_resource(
    read: Any, operation: str
) -> Any:
    """Run one sub-resource read, reporting a statement that DID NOT COMPLETE as a failure.

    Requirement 28.3, and the one rule this whole layer turns on: a read that did not complete is
    **not** an empty list. Reporting "you have no orders" for a statement that failed would tell a
    trader their session history is gone, and would let a client draw an equity curve from a set it
    never received. ``paper_repository`` raises for this and nothing here swallows it.
    """
    try:
        return read()
    except paper_repo.PaperRepositoryError as exc:
        logger.error(
            "[paper-session] the %s read did not complete: %s", operation, exc
        )
        raise paper_read_failed({"operation": operation}) from exc


def _collection(
    *,
    name: str,
    rows: Any,
    fields: Tuple[str, ...],
    session_id: str,
    operation: str,
) -> Dict[str, Any]:
    """One sub-resource collection body: the projected rows, their count, and the envelope."""
    view = [_row_view(row, fields) for row in rows]
    _assert_no_protected_field(view, operation)
    return _session_envelope(
        **{name: view}, session_id=session_id, count=len(view)
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/paper/sessions/{id}/orders | /fills | /positions | /trades | /equity
#     | /metrics | /events — 120/60s each
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/orders")
@limiter.limit("120/minute")
async def get_paper_session_orders(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One session's paper orders, newest first. Requirements 17.2, 21.5, 27.2.

    ``get_orders(supabase, caller_id, session_id=sid)`` - ONE select, with ``user_id`` and
    ``session_id`` both predicates on it, and ``ORDER_SELECT``'s explicit column list. The response
    carries every column of that projection except the four of
    :data:`ROW_WITHHELD_COLUMNS`; ``signal_id`` stays, because it is what lets a client line an
    order up against the signal that produced it (Requirement 20.4's signal stream), and it is the
    caller's own signal.
    """
    _row, sid, caller_id, supabase = await _owned_session(session_id, user)
    rows = _sub_resource(
        lambda: paper_repo.get_orders(supabase, caller_id, session_id=sid),
        "session_orders",
    )
    return _collection(
        name="orders",
        rows=rows,
        fields=ORDER_VIEW_FIELDS,
        session_id=sid,
        operation="session_orders",
    )


@router.get("/sessions/{session_id}/fills")
@limiter.limit("120/minute")
async def get_paper_session_fills(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One session's fills, most recently filled first. Requirements 17.2, 21.5, 27.2.

    ``paper_fills`` is one row per FILL, which is a different set from ``paper_trades``' closed
    round-trips - a partial close moves the account's realized PnL and writes no ``paper_trades``
    row (Requirement 18.10) - so both are served, by ``/fills`` and ``/trades``, rather than one
    being derived from the other.
    """
    _row, sid, caller_id, supabase = await _owned_session(session_id, user)
    rows = _sub_resource(
        lambda: paper_repo.get_fills(supabase, caller_id, session_id=sid),
        "session_fills",
    )
    return _collection(
        name="fills",
        rows=rows,
        fields=FILL_VIEW_FIELDS,
        session_id=sid,
        operation="session_fills",
    )


@router.get("/sessions/{session_id}/positions")
@limiter.limit("120/minute")
async def get_paper_session_positions(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One session's OPEN positions. Requirements 17.2, 18.5, 21.5, 27.2.

    Open only, which is ``get_positions``' default and is a predicate on the statement
    (``closed_at IS NULL``) rather than a filter applied afterwards. A closed position persists at
    ``size = 0`` rather than being deleted (Requirement 18.5), and it is deliberately not served
    here: Requirement 17.15 lists the sets a reset keeps readable - orders, fills, trades, metrics
    and equity snapshots - and a position is not one of them, because a closed position's economic
    content is the ``paper_trades`` row it produced.
    """
    _row, sid, caller_id, supabase = await _owned_session(session_id, user)
    rows = _sub_resource(
        lambda: paper_repo.get_positions(supabase, caller_id, session_id=sid),
        "session_positions",
    )
    return _collection(
        name="positions",
        rows=rows,
        fields=POSITION_VIEW_FIELDS,
        session_id=sid,
        operation="session_positions",
    )


@router.get("/sessions/{session_id}/trades")
@limiter.limit("120/minute")
async def get_paper_session_trades(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One session's closed round-trips, most recently closed first. Requirements 17.2, 21.5, 27.2.

    One row per position that reached size zero, which is Requirement 18.10's "closed" trade and
    the set the win rate is computed over. The individual fills are ``/fills``.
    """
    _row, sid, caller_id, supabase = await _owned_session(session_id, user)
    rows = _sub_resource(
        lambda: paper_repo.get_trades(supabase, caller_id, session_id=sid),
        "session_trades",
    )
    return _collection(
        name="trades",
        rows=rows,
        fields=TRADE_VIEW_FIELDS,
        session_id=sid,
        operation="session_trades",
    )


@router.get("/sessions/{session_id}/equity")
@limiter.limit("120/minute")
async def get_paper_session_equity(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One session's persisted equity series, in non-decreasing order. Requirements 18.9, 18.11.

    ``ORDER BY series_index, taken_at ASC`` - the repository's order, ascending on purpose, and not
    re-sorted here: ``paper_accounting.max_drawdown`` refuses a series handed to it out of timestamp
    order rather than sorting it, because the drawdown of a resorted series is not the drawdown of
    the series that was read. ``series_index`` leads because a reset begins a NEW series while
    keeping the old snapshots readable (Requirement 17.15), so a client reading this body sees both
    series and can tell them apart.

    This is the series Requirement 18.11 requires the equity curve to be drawn from - persisted
    snapshots, not live event state.
    """
    _row, sid, caller_id, supabase = await _owned_session(session_id, user)
    rows = _sub_resource(
        lambda: paper_repo.get_equity_snapshots(supabase, caller_id, session_id=sid),
        "session_equity",
    )
    return _collection(
        name="equity",
        rows=rows,
        fields=EQUITY_VIEW_FIELDS,
        session_id=sid,
        operation="session_equity",
    )


@router.get("/sessions/{session_id}/metrics")
@limiter.limit("120/minute")
async def get_paper_session_metrics(
    request: Request,
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """One session's most recently computed metrics, or the honest report that there are none.

    Requirements 17.2, 21.5, 27.2, 28.5.

    ``metrics: null`` with ``computed: false`` means the read completed and NO metrics row exists
    yet. That is a different statement from "the metrics are zero", and it is not flattened into
    one: a total return of ``0`` is a measurement, and reporting one for a session that has
    computed nothing would be the fabricated figure Requirement 28.5 forbids. The same holds field
    by field inside the row - ``win_rate`` is ``null`` while the closed-trade count is zero
    (Requirement 18.10) and ``max_drawdown_*`` is what the snapshots produced - so a ``null`` here
    is always "not computed" and never "zero".
    """
    _row, sid, caller_id, supabase = await _owned_session(session_id, user)
    row = _sub_resource(
        lambda: paper_repo.get_metrics(supabase, caller_id, session_id=sid),
        "session_metrics",
    )
    metrics = None if row is None else _row_view(row, METRICS_VIEW_FIELDS)
    _assert_no_protected_field(metrics, "session_metrics")
    return _session_envelope(
        session_id=sid,
        metrics=metrics,
        # Present, and stated rather than inferred from ``metrics`` being null: a client that
        # branches on this is branching on the same fact the server established.
        computed=metrics is not None,
    )


@router.get("/sessions/{session_id}/events")
@limiter.limit("120/minute")
async def get_paper_session_events(
    request: Request,
    session_id: str,
    since_sequence: int = Query(
        0,
        description=(
            "Replay every retained event above this per-session sequence number. 0 replays the "
            "whole log."
        ),
    ),
    user: dict = Depends(get_current_user),
):
    """The REST equivalent of the Paper_Channel replay. Requirements 19.8, 19.9, 21.5.

    THE SAME HISTORY THE SOCKET SERVES, BECAUSE IT IS THE SAME FUNCTION
    ------------------------------------------------------------------
    ``paper_channel.replay`` is called, not reimplemented. So the cap is the same 5000
    (:data:`paper_channel.REPLAY_ROW_CAP`, which is
    ``paper_repository.SESSION_EVENT_REPLAY_CAP`` - one number, owned by the statement's ``limit``),
    the frames are the same frames (``frame_from_row`` puts each stored row back through the same
    envelope a live event is built with, so a client applies both through one code path and
    deduplicates on ``event_id``), the ordering is the same ascending ``sequence``, and the
    unrecoverable-gap answer is the same ``paper_error{code:'HISTORY_INCOMPLETE'}`` with the same
    sentence and the same ``recoverable: false``. A client that cannot hold a socket is therefore
    not served a different history - not because two implementations were kept in step, but because
    there is one implementation.

    ``since_sequence`` is this endpoint's spelling of the socket's ``last_sequence``: a POSITION in
    a stream, never an identity. It cannot widen what the caller may see, because the read
    underneath it carries ``user_id`` as a predicate.

    A NEGATIVE ``since_sequence`` IS THE GAP CASE, NOT A 422
    -------------------------------------------------------
    A Paper_Channel sequence starts at 1, so a negative value names no position and cannot be
    reconciled - which is precisely Requirement 19.9's unrecoverable gap. It is answered the way
    the socket answers it: nothing partial is replayed, ``history_incomplete`` is ``true``, the
    ``HISTORY_INCOMPLETE`` frame is carried in ``error``, and ``current_sequence`` tells the client
    where the live stream is. Refusing it with a 422 instead would give the two transports two
    different answers to the same question, and would leave a client that reconnected with a bad
    cursor believing it was up to date.

    The status is 200 for that reason: an incomplete history is a REPORTED STATE of a read that
    completed, not a failed read. A read that did not complete is the 503 below, and the two are
    never conflated (Requirement 28.3).

    THE STATEMENTS, AND WHY THERE ARE THREE
    ---------------------------------------
    The ownership gate, then ``replay``'s own two (the session's ``event_sequence``, then the one
    capped select over ``paper_events``). That is exactly the sequence a socket subscriber's replay
    issues - ``authorize_channel_subscription`` refuses an unowned session before
    ``PaperChannelRegistry.replay`` reads anything - and it is fixed: three statements whether the
    session has emitted one event or five thousand.
    """
    _row, sid, _caller_id, supabase = await _owned_session(session_id, user)

    outcome = _sub_resource(
        lambda: paper_channel.replay(
            supabase,
            session_id=sid,
            # The AUTHENTICATED mapping, so the identity the replay scopes its reads by is the
            # dependency's and not a value from the request (Requirement 21.1).
            user=user,
            last_sequence=since_sequence,
        ),
        "session_events",
    )

    frames = [dict(frame) for frame in outcome.frames]
    error = None if outcome.error_frame is None else dict(outcome.error_frame)
    _assert_no_protected_field(frames, "session_events")
    _assert_no_protected_field(error, "session_events")

    return _session_envelope(
        session_id=sid,
        since_sequence=int(since_sequence),
        events=frames,
        count=len(frames),
        # Where the live stream is, so a client knows what to resume a socket from after reloading
        # through this endpoint - which is what Requirement 19.9 tells it to do.
        current_sequence=outcome.current_sequence,
        # The cap was reached and there is more above the last frame: page again from the last
        # sequence applied. Reported rather than left for a client to infer from ``count == 5000``.
        truncated=outcome.truncated,
        history_incomplete=outcome.history_incomplete,
        error=error,
        replay_cap=paper_channel.REPLAY_ROW_CAP,
    )
