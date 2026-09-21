"""
backend/signal_service.py — Signal Trace Service

Professional execution audit system.
Complete signal lifecycle tracking from strategy decision to final execution.
"""

import asyncio
import csv
import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from io import StringIO
from typing import (Any, Callable, Dict, Iterable, List, Mapping, Optional,
                    Tuple)
from uuid import uuid4

import inspect
from backend_app.core.dependencies import create_request_supabase_async

# The canonical vocabulary and its transition gate. Imported, never re-spelled:
# order_lifecycle_state.py is pure (no I/O, no DB, no FastAPI) precisely so that this
# module can consult it. Requirements 16.1, 16.4.
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleState,
    resolve_order_lifecycle_state,
)

# The three Execution_Environment values, imported rather than re-spelled: task 13.1's
# module is the single place BACKTEST/PAPER/LIVE are written, and its own docstring names
# "the Signal_Trace recorder's environment column (Requirement 23.1)" as one of the
# consumers that derives the vocabulary from there. Pure standard library at module scope,
# like order_lifecycle_state above, so importing it costs nothing and cannot cycle.
from backend_app.backend.execution_environment import (
    EXECUTION_ENVIRONMENTS,
    ExecutionEnvironment,
    parse_execution_environment,
)

# Re-exported so callers find the Idempotency_Key derivation where the design's
# module map puts it (signal_service), while the single implementation lives with
# the layer that consumes it. Requirement 19.1.
from backend_app.core.distributed_idempotency import (  # noqa: F401
    SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    idempotency_key_for,
)

# BC-3's producer (vyomquant-ui-redesign task 12.3's follow-up, Requirements 4.1, 19.2).
# Pure standard library at module scope, like the two imports above, so it cannot cycle and
# costs nothing to import. ``schedule_last_signal_at`` is fire-and-forget by construction -
# see that module's docstring for why the signal path schedules rather than awaits it.
from backend_app.backend.strategy_last_signal import schedule_last_signal_at

logger = logging.getLogger("SignalService")


class SignalStatus:
    """Signal status enumeration."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


#: Requirement 17.5's page size for the Signal_Trace_Page: "a maximum of 100 signals per
#: page". Declared here rather than in the router so the service's own default and the
#: route's ``le=`` bound are the same number written once. Defined above
#: :class:`SignalService` because it is a default argument value, which Python evaluates
#: when the class body runs.
SIGNAL_TRACE_PAGE_SIZE = 100

#: The most signals one ``GET /api/signal-trace/signals/export`` will render (task 13.3).
#: NOT a page size - the export walks every page of the filtered list and this is the
#: ceiling on the whole file. Declared here, beside the page size, and above
#: :class:`SignalService` for the same reason: it is a default argument value.
#:
#: WHY THERE IS A CEILING AT ALL, AND WHY IT IS DECLARED ON THE RESPONSE
#:   Requirement 17.1 says the list SHALL NOT omit a signal that was actually generated,
#:   so an export that silently stopped at the first page would break it - which is what
#:   the pre-spec export did (``list_signals(limit=1000)``, no pagination, no notice). But
#:   an unbounded export is a single request that can put a whole account's signal history
#:   on the heap and serialise it in one response. So the export is bounded AND says so:
#:   ``truncated`` and ``max_rows`` are on the returned envelope and on the response
#:   headers, so a caller that hit the ceiling knows the file is partial rather than
#:   believing it has everything. 100 pages of 100.
SIGNAL_TRACE_EXPORT_MAX_ROWS = SIGNAL_TRACE_PAGE_SIZE * 100

#: The two columns migration 010 adds to ``public.signals``: the Execution_Environment a
#: signal was produced under (Requirement 23.1) and, for a ``PAPER`` signal, the
#: Paper_Session it belongs to (Requirement 23.2). Probed as a SET, for the same reason
#: :data:`SIGNAL_LIFECYCLE_COLUMNS` is - PostgREST reports only the FIRST missing column,
#: so a projection naming both is answered by one error naming one of them and "is 010
#: applied" is a single verdict rather than two.
#:
#: Declared here rather than beside the probe because the projections below are built from
#: it; the probe, its cached verdict, its recheck window and its degradation warning live
#: with 005b's in the availability section (see :func:`signal_environment_columns_supported`).
SIGNAL_ENVIRONMENT_COLUMNS: Tuple[str, ...] = ("environment", "paper_session_id")

#: The long-standing ``public.signals`` select projection for a list view. Named so the
#: signal-trace reader can extend it with 005b's ``order_lifecycle_state`` without
#: re-spelling the other thirty-four columns.
#:
#: Task 29.1 APPENDS 010's pair to it - :data:`SIGNAL_ENVIRONMENT_COLUMNS`, joined onto the
#: end - so the thirty-four are not re-spelled, not re-ordered and not re-typed, and the
#: reader that needs the environment does not need a second projection constant.
#: :func:`without_signal_environment_columns` is what a database WITHOUT 010 is read with,
#: and the probe decides which of the two is used (Requirement 24.10 - a handler may not
#: read a column the migration set has not created).
SIGNAL_SUMMARY_COLUMNS = (
    "id,user_id,strategy_id,strategy_version,deployment_id,exchange_id,symbol,"
    "timeframe,worker_id,decision,status,risk_passed,risk_reason,position_size,"
    "capital,exposure,expected_loss,expected_reward,order_id,order_status,"
    "quantity,filled,remaining,average_price,fees,slippage,latency_ms,trade_id,"
    "pnl,realized_pnl,generated_at,risk_evaluated_at,order_updated_at,executed_at"
    + "," + ",".join(SIGNAL_ENVIRONMENT_COLUMNS)
)

#: What the signal-trace list reads: the summary projection (010's pair included, task
#: 29.1), the three JSONB columns the public shape is rebuilt from, and (added
#: conditionally) 005b's canonical column.
SIGNAL_TRACE_COLUMNS = SIGNAL_SUMMARY_COLUMNS + ",indicators,market_info,ml_info"


def without_signal_environment_columns(columns: str) -> str:
    """``columns`` with migration 010's pair removed, preserving the order of the rest.

    The projection a database that has NOT had ``010_signal_environment.sql`` applied is
    read with. Requirement 24.9/24.10's condition - a handler reading a column the schema
    does not have is a PostgreSQL ``42703`` - is avoided by asking the probe first and
    stripping here, rather than by keeping a second full projection constant that could
    drift from :data:`SIGNAL_SUMMARY_COLUMNS` one column at a time.
    """
    return ",".join(
        name
        for name in str(columns).split(",")
        if name.strip() not in SIGNAL_ENVIRONMENT_COLUMNS
    )


class SignalDecision:
    """Signal decision enumeration."""
    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class SignalService:
    """
    Central service for signal trace operations.
    
    Tracks complete signal lifecycle:
    - Signal generation
    - Risk evaluation
    - Order generation
    - Exchange validation
    - Exchange response
    - Execution
    - PnL recording
    """
    
    def __init__(self):
        self._local_signals: Dict[str, Dict[str, Any]] = {}
    
    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        try:
            token = user.get("access_token") if isinstance(user, dict) else None
            res = create_request_supabase_async(token)
            return await res if inspect.isawaitable(res) else res
        except Exception as e:
            logger.debug(f"Supabase client resolution fallback: {e}")
            return None
    
    async def create_signal(
        self,
        user: dict,
        strategy_id: str,
        strategy_version: str,
        deployment_id: str,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        worker_id: str,
        decision: str,
        indicators: Dict,
        market_info: Dict,
        ml_info: Optional[Dict] = None
    ) -> Dict:
        """
        Create a new signal record.
        
        Args:
            user: User dict with id and access_token
            strategy_id: Strategy ID
            strategy_version: Strategy version
            deployment_id: Deployment ID
            exchange_id: Exchange ID
            symbol: Trading pair
            timeframe: Timeframe
            worker_id: Worker ID
            decision: Signal decision (BUY, SELL, EXIT, CLOSE, HOLD)
            indicators: Indicator values
            market_info: Market information
            ml_info: ML/DL information (if applicable)
            
        Returns:
            Signal record
        """
        user_id = str(user.get("id", "anonymous")) if isinstance(user, dict) else "anonymous"
        signal_id = str(uuid4())
        
        signal_data = {
            "id": signal_id,
            "user_id": user_id,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "deployment_id": deployment_id,
            "exchange_id": exchange_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "worker_id": worker_id,
            "decision": decision,
            "status": SignalStatus.PENDING,
            "indicators": indicators or {},
            "market_info": market_info or {},
            "ml_info": ml_info,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Store in local registry immediately
        self._local_signals[signal_id] = signal_data.copy()
        
        try:
            sb = await self._get_supabase(user)
            if sb:
                query_res = sb.table("signals").insert(signal_data).execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result and hasattr(result, "data") and result.data:
                    self._local_signals[signal_id] = result.data[0]
                    # BC-3's producer, on this path too (task 12.3's follow-up,
                    # Requirements 4.1, 19.2). Inside the `if`, so it runs only when the
                    # INSERT actually returned a row: the in-memory fallback below is not
                    # persistence, and a signal only this process knows about must not move
                    # a timestamp every other reader can see. Scheduled, never awaited -
                    # nothing here can fail or delay the signal.
                    schedule_last_signal_at(
                        sb,
                        strategy_id=strategy_id,
                        user_id=user_id,
                        generated_at=signal_data["generated_at"],
                    )
                    return result.data[0]
        except Exception as e:
            logger.debug(f"Supabase signal insert fallback for {signal_id}: {e}")
        
        logger.info(f"Created signal {signal_id} for strategy {strategy_id}")
        return signal_data
    
    async def update_risk_decision(
        self,
        user: dict,
        signal_id: str,
        risk_passed: bool,
        risk_reason: str,
        position_size: Optional[float] = None,
        capital: Optional[float] = None,
        exposure: Optional[float] = None,
        expected_loss: Optional[float] = None,
        expected_reward: Optional[float] = None,
        drawdown_check: Optional[bool] = None
    ) -> Dict:
        """
        Update signal with risk decision.
        """
        user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        
        update_data = {
            "risk_passed": risk_passed,
            "risk_reason": risk_reason,
            "position_size": position_size,
            "capital": capital,
            "exposure": exposure,
            "expected_loss": expected_loss,
            "expected_reward": expected_reward,
            "drawdown_check": drawdown_check,
            "risk_evaluated_at": datetime.now(timezone.utc).isoformat(),
            "status": SignalStatus.ACCEPTED if risk_passed else SignalStatus.REJECTED
        }
        
        if signal_id in self._local_signals:
            self._local_signals[signal_id].update(update_data)
        
        try:
            sb = await self._get_supabase(user)
            if sb and user_id:
                query_res = sb.table("signals").update(update_data).eq("id", signal_id).eq("user_id", user_id).execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result and hasattr(result, "data") and result.data:
                    self._local_signals[signal_id] = result.data[0]
                    return result.data[0]
        except Exception as e:
            logger.debug(f"Supabase update_risk_decision fallback for {signal_id}: {e}")
        
        logger.info(f"Updated risk decision for signal {signal_id}: {risk_passed}")
        return self._local_signals.get(signal_id, update_data)
    
    async def update_order(
        self,
        user: dict,
        signal_id: str,
        order_id: str,
        exchange_order_id: Optional[str],
        order_status: str,
        quantity: float,
        filled: float,
        remaining: float,
        average_price: float,
        fees: float,
        slippage: float,
        latency_ms: float
    ) -> Dict:
        """
        Update signal with order information.
        """
        user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        
        update_data = {
            "order_id": order_id,
            "exchange_order_id": exchange_order_id,
            "order_status": order_status,
            "quantity": quantity,
            "filled": filled,
            "remaining": remaining,
            "average_price": average_price,
            "fees": fees,
            "slippage": slippage,
            "latency_ms": latency_ms,
            "order_updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Update status based on order status
        if order_status in ("FILLED", "PARTIALLY_FILLED"):
            update_data["status"] = SignalStatus.EXECUTED
        elif order_status == "CANCELLED":
            update_data["status"] = SignalStatus.CANCELLED
        elif order_status == "FAILED":
            update_data["status"] = SignalStatus.FAILED
        
        if signal_id in self._local_signals:
            self._local_signals[signal_id].update(update_data)
        
        try:
            sb = await self._get_supabase(user)
            if sb and user_id:
                query_res = sb.table("signals").update(update_data).eq("id", signal_id).eq("user_id", user_id).execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result and hasattr(result, "data") and result.data:
                    self._local_signals[signal_id] = result.data[0]
                    return result.data[0]
        except Exception as e:
            logger.debug(f"Supabase update_order fallback for {signal_id}: {e}")
        
        logger.info(f"Updated order for signal {signal_id}: {order_status}")
        return self._local_signals.get(signal_id, update_data)
    
    async def update_execution(
        self,
        user: dict,
        signal_id: str,
        trade_id: str,
        pnl: float,
        realized_pnl: Optional[float] = None
    ) -> Dict:
        """
        Update signal with execution and PnL information.

        BC-6 (vyomquant-ui-redesign task 12.6; Requirements 9.1, 9.2, 19.1, 19.2)
            THIS IS THE POINT AT WHICH REQUIREMENT 9.1'S STAGE 9 IS RECORDED. Writing
            ``executed_at`` is what makes :data:`POSITION_UPDATED_EVENT` appear on the
            timeline, because :func:`signal_event_timeline` derives that event from this
            row - see :func:`_position_updated_event`. So the position change is recorded
            by the same statement that records the execution, in the same transaction,
            with no second write to fail on its own.

            NOTHING ABOUT THIS METHOD'S CONTRACT MOVED. The signature, the four written
            columns and the return value are unchanged, and
            ``ExecutionUpdateRequest`` gains no field. A REPEAT call is safe by
            construction: it overwrites the same ``executed_at`` rather than appending,
            so the timeline still carries exactly one ``POSITION_UPDATED``.
        """
        user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        
        update_data = {
            "trade_id": trade_id,
            "pnl": pnl,
            "realized_pnl": realized_pnl,
            "executed_at": datetime.now(timezone.utc).isoformat()
        }
        
        if signal_id in self._local_signals:
            self._local_signals[signal_id].update(update_data)
        
        try:
            sb = await self._get_supabase(user)
            if sb and user_id:
                query_res = sb.table("signals").update(update_data).eq("id", signal_id).eq("user_id", user_id).execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result and hasattr(result, "data") and result.data:
                    self._local_signals[signal_id] = result.data[0]
                    return result.data[0]
        except Exception as e:
            logger.debug(f"Supabase update_execution fallback for {signal_id}: {e}")
        
        logger.info(f"Updated execution for signal {signal_id}: pnl={pnl}")
        return self._local_signals.get(signal_id, update_data)
    
    async def get_signal(
        self,
        user: dict,
        signal_id: str,
        environment: Optional[Any] = None,
    ) -> Optional[Dict]:
        """
        Get complete signal data.

        ``environment`` is task 29.3's Execution_Environment filter on the DETAIL path,
        applied as a PREDICATE in the same statement as the ownership predicates rather than
        by inspecting the row after it has been fetched: a signal excluded by the filter is
        then a row the database never returned, so it answers exactly as an unknown
        identifier does (Requirements 20.1, 20.2) with no second code path that could reveal
        that it exists. ``None`` - the default, and what every pre-29.3 caller passes - adds
        no predicate at all, so the unfiltered read is the statement it always was.
        """
        user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        
        try:
            sb = await self._get_supabase(user)
            if sb and user_id:
                query = sb.table("signals").select("*").eq("id", signal_id).eq("user_id", user_id)
                query = _apply_column_filter(query, "environment", environment)
                query_res = query.execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result and hasattr(result, "data") and result.data:
                    return result.data[0]
        except Exception as e:
            logger.debug(f"Supabase get_signal fallback for {signal_id}: {e}")
        
        local = self._local_signals.get(signal_id)
        if local:
            if not _matches_column_filter(local.get("environment"), environment):
                return None
            if not user_id or str(local.get("user_id", "")) == user_id or user_id == "anonymous":
                return local
        return None
    
    async def list_signals(
        self,
        user: dict,
        strategy_id: Optional[Any] = None,
        exchange_id: Optional[Any] = None,
        symbol: Optional[Any] = None,
        worker_id: Optional[Any] = None,
        deployment_id: Optional[Any] = None,
        decision: Optional[Any] = None,
        status: Optional[Any] = None,
        ml_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        strategy_version: Optional[Any] = None,
        order_lifecycle_state: Optional[Any] = None,
        columns: Optional[str] = None,
        environment: Optional[Any] = None,
    ) -> List[Dict]:
        """
        List signals with filters.

        MULTI-VALUE FILTERS (Requirement 17.2, task 13.1)
            Every filter argument above accepts either a single value (``"BTC/USDT"``) or
            a sequence of values (``["BTC/USDT", "ETH/USDT"]``). A sequence becomes one
            PostgREST ``in.(...)`` predicate - OR *within* that category - and the
            predicates for the separate categories are ANDed by the query builder, which
            is exactly Requirement 17.2's "matches at least one selected value in every
            active category". Nothing about the single-value behaviour changed, so every
            existing caller keeps its meaning.

        ``strategy_version`` and ``order_lifecycle_state`` are new filter categories
        Requirement 17.2 asks for. ``order_lifecycle_state`` is 005b's canonical column;
        the signal-trace router translates it onto the legacy ``status`` column through
        ``SIGNALS_STATUS_MAP`` when 005b has not been applied, so this method never has to
        know whether the column exists (see :meth:`list_signal_trace`).

        ``columns`` overrides the select projection for a caller that needs a column this
        summary does not list (again, 005b's ``order_lifecycle_state``). ``None`` keeps the
        long-standing projection.

        ``environment`` is 010's column, and it is applied as a PREDICATE in the statement
        like every other category above - never as a post-filter over rows already fetched,
        which would page the wrong rows (``limit``/``offset`` are applied by the database)
        and read every environment's signals to answer a question about one. On a database
        without 010 there is no column to predicate on: the filtered read answers NOTHING
        rather than answering every environment's signals as though they had been filtered,
        and :func:`build_signal_trace_page` is where that condition is declared to the caller
        (see :func:`_environment_degradation`).
        """
        user_id = str(user.get("id", "")) if isinstance(user, dict) else ""
        
        try:
            sb = await self._get_supabase(user)
            if sb and user_id:
                # 010's pair is in the projection; it is stripped when the probe says the
                # migration is absent, so a pre-010 database answers this list instead of
                # raising 42703 into the fallback below (task 29.1).
                summary_columns, with_environment = await signal_environment_projection(
                    sb, columns or SIGNAL_SUMMARY_COLUMNS
                )
                if _filter_values(environment) and not with_environment:
                    warn_signal_environment_columns_absent(
                        "an environment filter "
                        f"{list(_filter_values(environment))} was requested on the "
                        "signal-trace read path, and there is no environment column to "
                        "answer it with; no signal is reported rather than reporting every "
                        "environment's signals as though they had been filtered"
                    )
                    return []
                query = sb.table("signals").select(summary_columns).eq("user_id", user_id)

                for column, value in (
                    ("environment", environment),
                    ("strategy_id", strategy_id),
                    ("strategy_version", strategy_version),
                    ("exchange_id", exchange_id),
                    ("symbol", symbol),
                    ("worker_id", worker_id),
                    ("deployment_id", deployment_id),
                    ("decision", decision),
                    ("status", status),
                    ("order_lifecycle_state", order_lifecycle_state),
                ):
                    query = _apply_column_filter(query, column, value)

                if ml_type:
                    if ml_type == "ml":
                        query = query.not_.is_("ml_info", None)
                    elif ml_type == "rule_based":
                        query = query.is_("ml_info", None)
                if date_from:
                    query = query.gte("generated_at", date_from)
                if date_to:
                    query = query.lte("generated_at", date_to)
                
                query_res = query.order("generated_at", desc=True).range(offset, offset + limit - 1).execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result and hasattr(result, "data") and result.data:
                    return result.data
        except Exception as e:
            logger.debug(f"Supabase list_signals fallback: {e}")
        
        # Local fallback filter
        results = []
        for s in self._local_signals.values():
            if user_id and str(s.get("user_id", "")) != user_id and str(s.get("user_id", "")) != "anonymous":
                continue
            if not _matches_column_filter(s.get("strategy_id"), strategy_id):
                continue
            if not _matches_column_filter(s.get("strategy_version"), strategy_version):
                continue
            if not _matches_column_filter(s.get("exchange_id"), exchange_id):
                continue
            if not _matches_column_filter(s.get("symbol"), symbol):
                continue
            if not _matches_column_filter(s.get("worker_id"), worker_id):
                continue
            if not _matches_column_filter(s.get("deployment_id"), deployment_id):
                continue
            if not _matches_column_filter(s.get("decision"), decision):
                continue
            if not _matches_column_filter(s.get("status"), status):
                continue
            if not _matches_column_filter(s.get("order_lifecycle_state"), order_lifecycle_state):
                continue
            if not _matches_column_filter(s.get("environment"), environment):
                continue
            if ml_type == "ml" and not s.get("ml_info"):
                continue
            if ml_type == "rule_based" and s.get("ml_info"):
                continue
            if date_from and (s.get("generated_at") or "") < date_from:
                continue
            if date_to and (s.get("generated_at") or "") > date_to:
                continue
            if search and search.lower() not in str(s.get("id", "")).lower():
                continue
            results.append(s)
        
        results.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
        return results[offset : offset + limit]

    async def list_signal_trace(
        self,
        user: dict,
        *,
        strategy_id: Optional[Any] = None,
        strategy_version: Optional[Any] = None,
        deployment_id: Optional[Any] = None,
        symbol: Optional[Any] = None,
        side: Optional[Any] = None,
        order_lifecycle_state: Optional[Any] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = SIGNAL_TRACE_PAGE_SIZE,
        offset: int = 0,
        exchange_id: Optional[Any] = None,
        worker_id: Optional[Any] = None,
        decision: Optional[Any] = None,
        status: Optional[Any] = None,
        ml_type: Optional[str] = None,
        search: Optional[str] = None,
        environment: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """One page of the Signal_Trace_Page's list. See :func:`build_signal_trace_page`.

        A thin method on purpose: it owns the two things only the service can do - resolve
        the caller's own RLS-scoped client and probe 005b through it - and hands everything
        else to the module-level helpers documented under "TASK 13.1" below, so the filter
        semantics and the row projection are testable without a database.
        """
        return await build_signal_trace_page(
            self,
            user,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            deployment_id=deployment_id,
            symbol=symbol,
            side=side,
            order_lifecycle_state=order_lifecycle_state,
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
            environment=environment,
        )
    
    async def get_signal_timeline(
        self,
        user: dict,
        signal_id: str
    ) -> List[Dict]:
        """
        Get complete signal timeline in chronological order.

        One owner-scoped read, then :func:`signal_event_timeline` - which is where the
        event reconstruction itself lives, so the trace-detail surface (task 13.2) can
        build the same timeline from a row it has ALREADY read instead of reading the row
        a second time.
        """
        signal = await self.get_signal(user, signal_id)
        if not signal:
            return []
        return signal_event_timeline(signal)

    async def get_signal_trace(
        self,
        user: dict,
        signal_id: str,
        *,
        environment: Optional[Any] = None,
        viewer_role: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """One signal's full trace detail. See :func:`build_signal_trace_detail`.

        Thin for the same reason :meth:`list_signal_trace` is: the only things that need
        the service are the caller's RLS-scoped client and the 005b probe.
        """
        return await build_signal_trace_detail(
            self, user, signal_id, environment=environment, viewer_role=viewer_role
        )

    async def export_signal_trace(
        self,
        user: dict,
        *,
        format: str = "json",
        strategy_id: Optional[Any] = None,
        strategy_version: Optional[Any] = None,
        deployment_id: Optional[Any] = None,
        symbol: Optional[Any] = None,
        side: Optional[Any] = None,
        order_lifecycle_state: Optional[Any] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        exchange_id: Optional[Any] = None,
        worker_id: Optional[Any] = None,
        decision: Optional[Any] = None,
        status: Optional[Any] = None,
        ml_type: Optional[str] = None,
        search: Optional[str] = None,
        environment: Optional[Any] = None,
        max_rows: int = SIGNAL_TRACE_EXPORT_MAX_ROWS,
    ) -> Dict[str, Any]:
        """The Signal_Trace_Page's export. See :func:`build_signal_trace_export`.

        Thin for the same reason :meth:`list_signal_trace` is. Distinct from the pre-spec
        :meth:`export_signals` below rather than replacing it: that one is called by an
        existing test against the legacy raw-row shape, and this one exports task 13.1's
        projection with task 13.1's filter semantics.
        """
        return await build_signal_trace_export(
            self,
            user,
            format=format,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            deployment_id=deployment_id,
            symbol=symbol,
            side=side,
            order_lifecycle_state=order_lifecycle_state,
            date_from=date_from,
            date_to=date_to,
            exchange_id=exchange_id,
            worker_id=worker_id,
            decision=decision,
            status=status,
            ml_type=ml_type,
            search=search,
            environment=environment,
            max_rows=max_rows,
        )

    async def export_signals(
        self,
        user: dict,
        filters: Dict,
        format: str = "json"
    ) -> str:
        """
        Export signals with filters.
        """
        clean_filters = {k: v for k, v in filters.items() if v is not None}
        signals = await self.list_signals(user, limit=1000, **clean_filters)
        
        if format == "csv":
            import csv
            from io import StringIO
            
            output = StringIO()
            if signals:
                fieldnames = list(signals[0].keys())
                writer = csv.DictWriter(output, fieldnames=fieldnames)
                writer.writeheader()
                for signal in signals:
                    writer.writerow(signal)
            
            return output.getvalue()
        
        import json
        return json.dumps(signals, default=str, indent=2)


# Singleton instance
_signal_service = None

async def get_signal_service() -> SignalService:
    """Get singleton SignalService instance."""
    global _signal_service
    if _signal_service is None:
        _signal_service = SignalService()
    return _signal_service

# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 10.1 - generate_signal(deployment, node_output) -> Signal
#
#  Spec: trading-lifecycle-integration. design.md -> "New module:
#  signal_service.py". Requirements 15.1, 15.2, 15.3, 15.4, 15.5.
#
#  WHAT THIS SECTION OWNS
#  ----------------------
#  The Signal record's final shape (task 9.1 deliberately left it here), the
#  minting of a signal id, and the single GENERATED row that a signal's whole
#  later life is anchored on.
#
#  WHAT IT DELIBERATELY DOES NOT OWN
#  ---------------------------------
#  * Submission. Routing through execute_with_idempotency / risk_engine /
#    execution_engine, and every transition after GENERATED, is task 10.2's
#    submit_signal. Nothing here writes order_lifecycle_transitions: the first
#    row of that log (from_state NULL -> to_state GENERATED) is a write, and
#    10.2 owns the write-through-the-gate path (assert_transition_legal, then
#    persist, then audit) for every transition including the first. What this
#    section guarantees for 10.2 is that the row it will transition FROM exists
#    and says GENERATED.
#  * The Live_Runtime wiring - which ACTION-node evaluation calls this, when a
#    stale feed suspends it, and how a per-event node error is contained - is
#    task 10.3 (Requirements 14.3, 14.5-14.8).
#  * Publishing a WebSocket frame. Task 14.2.
#
#  THE INITIAL STATE IS GENERATED, AND IT IS NOT A DEFAULT
#  ------------------------------------------------------
#  INITIAL_ORDER_LIFECYCLE_STATE is READ FROM the resolver rather than
#  transcribed as OrderLifecycleState.GENERATED, for the reason
#  order_lifecycle_state.py's own docstring gives: "nothing reported yet" is a
#  real state a signal legitimately starts in, and the resolver is where that
#  fact is decided. Transcribing it here would create a second place for it to
#  drift.
#
#  CREDENTIAL CONTAINMENT IS STRUCTURAL, NOT A FILTER (Req 15.3, 15.4, 20.3)
#  ------------------------------------------------------------------------
#  Three properties, none of which is a scrub or a denylist:
#
#    1. Signal is a frozen dataclass with a CLOSED field set. There is no
#       **kwargs, no `extra`, no `raw`, no passthrough of the deployment row or
#       of the node output. A credential has nowhere to land, so no filter is
#       needed to keep it out.
#    2. Nothing on this path reads a credential source. The deployment is read
#       only through _deployment_facts() - a projection over ten NAMED keys -
#       and the node output only through named decision keys. api_key_vault is
#       not imported here, load_decrypted_keys is not called here, and no
#       exchange_keys / exchange_connections row is read here. The keys are
#       read in the live executor, per deployment_binding.py's own note.
#    3. The only account-naming field on the record is exchange_account_id -
#       the platform's INTERNAL Exchange_Account reference, which Requirement
#       20.3 exempts by name ("used solely to name which connected account a
#       strategy, deployment, or signal belongs to"). No exchange-issued
#       account identifier, API key, secret, passphrase or access token is a
#       field of this record or a value this code can reach.
#
#  ON exchange_id, HONESTLY
#  ------------------------
#  public.signals.exchange_id is VARCHAR(50) NOT NULL (002_signal_trace.sql /
#  003_signal_trace_restoration.sql) and no migration in this spec relaxes it,
#  so every inserted row must carry it. It holds the VENUE SLUG ("binance",
#  "kraken") resolved by deployment_binding from the account row - a market
#  descriptor, not an identity and not a credential: design.md's own preflight
#  response returns {"exchange_id": "kraken"} in an API body, and Requirement
#  20.3's prohibition names "exchange-issued account identifier, API key,
#  secret, passphrase, or access token", none of which a venue slug is. It is
#  carried as venue on the record so the name cannot be mistaken for an
#  account reference, and exchange_account_id remains the only field that names
#  WHICH account.
#
#  WHY REQUIREMENT 15.2'S LIST LANDS WHERE IT DOES
#  -----------------------------------------------
#  public.signals offers exactly three JSONB columns - indicators, market_info,
#  ml_info - and this spec adds no more (005b adds two TEXT columns). So the
#  projection is fixed and stated once, here, rather than being re-decided at
#  each call site:
#
#    Requirement 15.2 asks for        ->  column
#    ------------------------------------------------------------------
#    signal identifier                ->  id
#    owning user identifier           ->  user_id
#    strategy identifier              ->  strategy_id
#    strategy version identifier      ->  strategy_version  (+ the version
#                                          row's uuid in market_info, which
#                                          has no column of its own)
#    deployment identifier            ->  deployment_id
#    symbol                           ->  symbol
#    generation timestamp             ->  generated_at
#    signal type                      ->  market_info.signal_type
#    side                             ->  market_info.side (legacy `decision`
#                                          column carries the decision word)
#    requested quantity / sizing      ->  quantity, market_info.sizing_intention
#    source node identifier(s)        ->  market_info.source_node_ids
#    decision metadata: the evaluated
#      state of every upstream node
#      in the closure                 ->  indicators
#    risk validation outcome          ->  risk_passed, risk_reason,
#                                          position_size, capital, exposure,
#                                          expected_loss, expected_reward,
#                                          drawdown_check, risk_evaluated_at
#    ML/DL inference output           ->  ml_info
#    current Order_Lifecycle_State    ->  order_lifecycle_state  (005b)
#    order/execution reference        ->  order_id / trade_id, written only
#                                          once available (task 10.2)
#    Idempotency_Key (task 10.1's
#      own bullet, Req 19.1)          ->  idempotency_key        (005b)
#
#  The legacy `status` column is NOT written. It is NOT NULL DEFAULT 'pending',
#  so the database fills it; writing 'pending' ourselves would be this code
#  claiming a legacy state for a signal that is at GENERATED, and GENERATED has
#  no legacy spelling (SIGNALS_STATUS_MAP has no key for it - see
#  order_lifecycle_state.py). order_lifecycle_state is the canonical column
#  Requirement 16.2 asks for; a reader wanting the canonical state reads it,
#  not `status`.
#
#  005b IS APPLIED BY HAND, SO ITS ABSENCE DEGRADES RATHER THAN 500s
#  ----------------------------------------------------------------
#  Same disposition, same mechanics and the same recheck window as
#  deployment_binding.py's 004e probe, because the cause is identical:
#  migrations here are applied per-file by hand and .github/workflows/
#  03-deploy.yml has no migration step. If idempotency_key and
#  order_lifecycle_state are absent, the row is still written - a signal that
#  is not persisted is a signal that must not be routed (Requirement 15.5), so
#  refusing to write it at all would be strictly worse - and a warning NAMES
#  005b_signal_lifecycle_and_idempotency.sql and says exactly which two
#  guarantees are not in force until it is applied.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


#: The state every signal starts in. Read from the resolver ("no source has
#: reported anything about this signal yet"), not transcribed.
INITIAL_ORDER_LIFECYCLE_STATE: OrderLifecycleState = resolve_order_lifecycle_state()

#: Named in every degradation warning so an operator never has to guess which file to
#: apply. Same convention as DEPLOYMENT_BINDING_MIGRATION.
SIGNAL_LIFECYCLE_MIGRATION = "backend_app/migrations/005b_signal_lifecycle_and_idempotency.sql"

#: The two columns 005b section 1 adds to public.signals. Probed as a set, because
#: PostgREST reports only the first missing one.
SIGNAL_LIFECYCLE_COLUMNS: Tuple[str, ...] = ("idempotency_key", "order_lifecycle_state")

#: How long an "absent" verdict is trusted before it is re-probed, so applying 005b to a
#: running fleet takes effect without a redeploy. Same window as the 004e probe.
SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS = 300.0

#: PostgreSQL's undefined_column and PostgREST's schema-cache equivalent. A missing
#: TABLE (42P01 / PGRST205) is deliberately absent: public.signals is created by 002/003
#: and its absence is a real problem, not something to degrade around.
_MISSING_SIGNAL_COLUMN_CODES = ("42703", "undefined_column", "pgrst204")

#: The legacy `decision` vocabulary public.signals.decision already carries (see
#: SignalDecision above, and 002_signal_trace.sql's own comment). HOLD is accepted by
#: the column but is not a signal - see SignalGenerationRefused's
#: SIGNAL_DECISION_NOT_ACTIONABLE.
ENTRY_DECISIONS: Tuple[str, ...] = ("BUY", "SELL")
EXIT_DECISIONS: Tuple[str, ...] = ("EXIT", "CLOSE")
ACTIONABLE_DECISIONS: Tuple[str, ...] = ENTRY_DECISIONS + EXIT_DECISIONS

#: Aliases the DAG's own emitters already use for a decision. dag_event_loop.Signal
#: spells it `action` and lower-cases it ('buy'/'sell'/'hold'); the ACTION node's
#: compiled output spells it `decision` or `side`. All three normalise to one word.
_DECISION_ALIASES: Dict[str, str] = {
    "buy": "BUY",
    "long": "BUY",
    "sell": "SELL",
    "short": "SELL",
    "exit": "EXIT",
    "close": "CLOSE",
    "hold": "HOLD",
    "flat": "HOLD",
    "none": "HOLD",
}

#: How deep and how wide a value copied out of a node reading may be. A DAG node can
#: hold a whole indicator series; a signal record is an audit row, not a data dump, and
#: an unbounded JSONB payload is how an insert starts failing in production at 3am.
_MAX_JSON_DEPTH = 6
_MAX_JSON_ITEMS = 256
_MAX_JSON_STRING = 4096

_signal_lifecycle_columns_supported: Optional[bool] = None
_signal_lifecycle_columns_checked_at: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS
# ══════════════════════════════════════════════════════════════════════════


class SignalRejected(Exception):
    """One classified refusal on the signal path.

    Field-for-field the shape ``deployment_binding.DeployRejected`` and
    ``order_lifecycle_state.OrderLifecycleRejected`` already establish (``code``,
    ``message``, ``details``, ``http_status``, ``to_detail()``), so a router that maps
    one maps this too.

    Deliberately not a ``ValueError``: several call sites in this codebase already read a
    bare ``ValueError`` as "not found", and a refused or unpersisted signal is neither
    missing nor a client typo.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        http_status: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = dict(details or {})
        self.http_status = int(http_status)

    def to_detail(self) -> Dict[str, Any]:
        """The FastAPI ``detail`` body. ``error`` first, matching the router shape."""
        return {"error": self.code, "message": self.message, **self.details}


class SignalGenerationRefused(SignalRejected):
    """The node output and the deployment do not describe a signal that can be minted.

    Raised BEFORE an id is minted and before anything is written, so a refused candidate
    consumes no identifier and leaves no row. Requirement 15.1's "never reused" is easier
    to keep when a rejected candidate never took an id in the first place.
    """


class SignalPersistenceError(SignalRejected):
    """Requirement 15.5, as an exception.

    The signal could not be persisted, therefore it MUST NOT be reported to the frontend
    and MUST NOT be routed to risk validation or order submission. Raising is how that is
    enforced: :func:`generate_signal` returns a ``Signal`` only when a row exists, so a
    caller cannot accidentally proceed on an unpersisted one.

    Note what this is NOT: it is not the degraded path. A database missing 005b's two
    columns still gets a row (with a warning naming the migration); only a genuine write
    failure - no client, an error from PostgREST, an empty result - is this.
    """

    def __init__(
        self,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        code: str = "SIGNAL_NOT_PERSISTED",
    ):
        super().__init__(code, message, details, http_status=500)


class SignalExportRefused(SignalRejected):
    """The export cannot be produced as asked. A CLIENT error, hence 400 by default.

    Task 13.3's only refusal: a ``format`` outside :data:`SIGNAL_TRACE_EXPORT_FORMATS`.
    Refused rather than silently defaulted to JSON, for the same reason
    :func:`resolve_lifecycle_state_filter` refuses an unrecognised state: a caller that
    asked for XLSX and received JSON under a ``.csv`` filename has been answered a
    question it did not ask.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        http_status: int = 400,
    ):
        super().__init__(code, message, details, http_status=http_status)


class SignalEnvironmentRefused(SignalRejected):
    """An ``environment`` filter value that is not an Execution_Environment. 400 (task 29.3).

    Refused rather than ignored, for the reason
    :class:`~backend_app.backend.order_lifecycle_state.OrderLifecycleRejected` is: silently
    dropping ``?environment=PAPR`` would answer with signals from EVERY environment, which
    is MORE than the caller asked for. On an audit page that is the wrong way to be wrong.
    """

    def __init__(self, unrecognised: Iterable[Any]):
        offending = [str(value) for value in unrecognised]
        accepted = [member.value for member in EXECUTION_ENVIRONMENTS]
        super().__init__(
            "SIGNAL_ENVIRONMENT_UNRECOGNISED",
            f"{offending} is not an Execution_Environment. The filter accepts {accepted}; an "
            "unrecognised value is refused rather than ignored, because ignoring it would "
            "return signals from environments the caller did not ask for.",
            {"unrecognised": offending, "recognised_environments": accepted},
            http_status=400,
        )


class SignalEnvironmentFilterUnanswerable(SignalRejected):
    """An ``environment`` filter on a database that has no ``environment`` column. 404.

    THE DETAIL VIEW'S HALF OF THE PRE-010 DEGRADATION (task 29.3)
        The list answers this condition with an EMPTY page that declares it (see
        :func:`_environment_degradation`). One signal has no empty page to return, so the
        detail says the same thing with the same status the "no such signal in that
        environment" answer carries - 404 - and names the migration in the body.

        Raised BEFORE the row is read, deliberately: the answer is then identical for every
        identifier and every caller, so it cannot become a channel for whether some
        signal exists (Requirements 20.1, 20.2). It reveals one fact, and only one: that
        ``010_signal_environment.sql`` has not been applied to this database.
    """

    def __init__(self, requested: Iterable[Any]):
        asked = [str(value) for value in requested]
        super().__init__(
            "SIGNAL_NOT_FOUND",
            f"An environment filter {asked} was requested, and public.signals has no "
            f"environment column to answer it with. Apply {SIGNAL_ENVIRONMENT_MIGRATION}. "
            "No signal is reported rather than reporting one whose environment could not "
            "be checked.",
            {
                "environment_filter": asked,
                "environment_filter_answered": False,
                "migration": SIGNAL_ENVIRONMENT_MIGRATION,
            },
            http_status=404,
        )


# ══════════════════════════════════════════════════════════════════════════
# 005b AVAILABILITY - detected, never assumed
# ══════════════════════════════════════════════════════════════════════════


async def _execute(query: Any) -> Any:
    return await query if inspect.isawaitable(query) else query


def reset_signal_lifecycle_column_support() -> None:
    """Forget the cached 005b verdict. For tests, and for an operator who just applied it."""
    global _signal_lifecycle_columns_supported, _signal_lifecycle_columns_checked_at
    _signal_lifecycle_columns_supported = None
    _signal_lifecycle_columns_checked_at = 0.0


def signal_lifecycle_column_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False``, or ``None`` for "not yet determined"."""
    return _signal_lifecycle_columns_supported


def _remember_signal_lifecycle_support(supported: bool) -> None:
    global _signal_lifecycle_columns_supported, _signal_lifecycle_columns_checked_at
    _signal_lifecycle_columns_supported = supported
    _signal_lifecycle_columns_checked_at = time.monotonic()


def remember_signal_lifecycle_columns_absent() -> None:
    """Record that 005b is not applied, so the next write degrades without re-probing.

    Public for the same reason ``deployment_binding.remember_binding_columns_absent`` is:
    a process that cached a positive verdict and then met ``42703`` at the INSERT has
    newer information than the probe did.
    """
    _remember_signal_lifecycle_support(False)


def _cached_signal_lifecycle_support() -> Optional[bool]:
    if _signal_lifecycle_columns_supported is None:
        return None
    if _signal_lifecycle_columns_supported:
        return True
    if (
        time.monotonic() - _signal_lifecycle_columns_checked_at
        >= SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS
    ):
        return None
    return False


def is_missing_signal_lifecycle_column_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says a 005b column does not exist.

    Narrow on purpose, and narrow in the same way the 004e probe is: anything this
    returns ``False`` for is re-raised as a persistence failure, because the one outcome
    worse than a signal that fails loudly is a signal that is silently written without
    its canonical state and then routed as if it had one.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text:  # a missing TABLE, which degrading cannot help with
        return False
    if any(code in text for code in _MISSING_SIGNAL_COLUMN_CODES):
        return True
    if not any(column in text for column in SIGNAL_LIFECYCLE_COLUMNS):
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def warn_signal_lifecycle_columns_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says which guarantees are not in force."""
    logger.warning(
        "public.signals is missing the signal lifecycle columns (%s). "
        "Apply %s, then restart or wait %.0fs for the re-probe. Until then a signal row "
        "is written in the legacy shape: order_lifecycle_state and idempotency_key are "
        "NOT persisted, so chk_signals_order_lifecycle_state and "
        "uq_signals_idempotency_key do not exist, the canonical Order_Lifecycle_State of "
        "Requirement 16.1 is not readable from the row (only the legacy `status` default), "
        "and the durable idempotency backstop of Requirement 19.1 is Redis alone. "
        "Detail: %s",
        ", ".join(SIGNAL_LIFECYCLE_COLUMNS),
        SIGNAL_LIFECYCLE_MIGRATION,
        SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS,
        detail,
    )


async def signal_lifecycle_columns_supported(sb: Any) -> bool:
    """Whether ``public.signals`` carries 005b's two columns.

    Read-only and cached: one ``SELECT idempotency_key,order_lifecycle_state ... LIMIT 1``
    per process through the caller's own RLS-scoped client, so the probe sees what the
    write will see. An *indeterminate* answer resolves to ``True`` so the full write is
    attempted and any real failure surfaces at the INSERT rather than being pre-emptively
    downgraded - the same disposition ``deployment_binding.binding_columns_supported``
    and ``strategy_service.canonical_columns_supported`` take.
    """
    cached = _cached_signal_lifecycle_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        result = await _execute(
            sb.table("signals")
            .select(",".join(SIGNAL_LIFECYCLE_COLUMNS))
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if is_missing_signal_lifecycle_column_error(exc):
            _remember_signal_lifecycle_support(False)
            warn_signal_lifecycle_columns_absent(str(exc))
            return False
        logger.warning(
            "Signal lifecycle column probe was inconclusive (%s); attempting the full "
            "write and letting a real error surface.",
            exc,
        )
        return True

    error = getattr(result, "error", None)
    if error is not None and is_missing_signal_lifecycle_column_error(Exception(str(error))):
        _remember_signal_lifecycle_support(False)
        warn_signal_lifecycle_columns_absent(str(error))
        return False

    _remember_signal_lifecycle_support(True)
    return True


# ══════════════════════════════════════════════════════════════════════════
# 010 AVAILABILITY - the same probe, for the environment pair (task 29.1)
#
# WHY A SECOND VERDICT AND NOT A SECOND MECHANISM
# -----------------------------------------------
# Everything below is 005b's probe applied to 010's pair: the same cached verdict, the same
# 300-second recheck window, the same public ``remember_*_absent`` escape hatch for a
# process that met a 42703 at the INSERT after caching a positive verdict, the same narrow
# error classification, and the same disposition - an indeterminate answer resolves to
# "supported" so a real failure surfaces at the statement rather than being pre-emptively
# downgraded. Nothing here is a new way of asking the question.
#
# It is a SEPARATE verdict because 005b and 010 are separate files applied BY HAND, in a
# maintenance window, with no migration table recording which have run (see 010's own
# header). A database can carry either, both or neither, so one boolean cannot answer for
# two migrations.
#
# WHY AN ABSENT PAIR DEGRADES RATHER THAN REFUSES
# ----------------------------------------------
# The same reading 005b's section above takes, and deliberately NOT the one
# ``paper/paper_repository`` takes for a missing paper TABLE. There, an absent table costs
# the Paper_Account balance itself, so refusing is the only safe answer. Here an absent
# COLUMN costs an audit field: the signal row is still written, still owner-scoped, still
# carries its decision, its sizing and its canonical state, and the ONE thing missing is
# which environment produced it. Refusing the write would suppress the record entirely to
# protect one of its columns - strictly worse - so the write proceeds and a warning NAMES
# ``010_signal_environment.sql`` and says exactly which guarantee is not in force.
# ══════════════════════════════════════════════════════════════════════════


#: Named in every degradation warning so an operator never has to guess which file to
#: apply. Same convention as SIGNAL_LIFECYCLE_MIGRATION.
SIGNAL_ENVIRONMENT_MIGRATION = "backend_app/migrations/010_signal_environment.sql"

#: How long an "absent" verdict is trusted before it is re-probed, so applying 010 to a
#: running fleet takes effect without a redeploy. Same window as the 005b probe.
SIGNAL_ENVIRONMENT_COLUMN_RECHECK_SECONDS = 300.0

#: Where the reported Execution_Environment of a page's rows came from. Reported on the list
#: and the export envelopes beside ``lifecycle_state_source``, and read the same way: the
#: page states the provenance of the label instead of leaving a reader to assume one.
ENVIRONMENT_SOURCE_COLUMN = "column"
ENVIRONMENT_SOURCE_UNAVAILABLE = "unavailable"

#: The label a row carries in ``count_by_environment`` when its environment is not known -
#: a pre-010 database, where the column does not exist. NOT ``"LIVE"``: Requirement 23.7
#: back-fills the existing rows to ``LIVE`` *in the migration*, and until that has run a
#: PAPER signal is indistinguishable from a live one (see
#: :func:`warn_signal_environment_columns_absent`), so calling an unlabelled row ``LIVE``
#: here would be this module inventing the very fact the column exists to record.
UNLABELLED_ENVIRONMENT = "UNLABELLED"

_signal_environment_columns_supported: Optional[bool] = None
_signal_environment_columns_checked_at: float = 0.0


def reset_signal_environment_column_support() -> None:
    """Forget the cached 010 verdict. For tests, and for an operator who just applied it."""
    global _signal_environment_columns_supported, _signal_environment_columns_checked_at
    _signal_environment_columns_supported = None
    _signal_environment_columns_checked_at = 0.0


def signal_environment_column_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False``, or ``None`` for "not yet determined"."""
    return _signal_environment_columns_supported


def _remember_signal_environment_support(supported: bool) -> None:
    global _signal_environment_columns_supported, _signal_environment_columns_checked_at
    _signal_environment_columns_supported = supported
    _signal_environment_columns_checked_at = time.monotonic()


def remember_signal_environment_columns_absent() -> None:
    """Record that 010 is not applied, so the next write degrades without re-probing.

    Public for the same reason :func:`remember_signal_lifecycle_columns_absent` is: a
    process that cached a positive verdict and then met ``42703`` at the INSERT has newer
    information than the probe did.
    """
    _remember_signal_environment_support(False)


def _cached_signal_environment_support() -> Optional[bool]:
    if _signal_environment_columns_supported is None:
        return None
    if _signal_environment_columns_supported:
        return True
    if (
        time.monotonic() - _signal_environment_columns_checked_at
        >= SIGNAL_ENVIRONMENT_COLUMN_RECHECK_SECONDS
    ):
        return None
    return False


def is_missing_signal_environment_column_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says a 010 column does not exist.

    Narrow in exactly the way :func:`is_missing_signal_lifecycle_column_error` is narrow,
    and for the same reason: anything this returns ``False`` for is re-raised as a
    persistence failure rather than quietly turned into a signal row with no environment.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text:  # a missing TABLE, which degrading cannot help with
        return False
    if any(code in text for code in _MISSING_SIGNAL_COLUMN_CODES):
        return True
    if not any(column in text for column in SIGNAL_ENVIRONMENT_COLUMNS):
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def _names_signal_environment_column(detail: Any) -> bool:
    """Whether ``detail`` names one of 010's two columns by name.

    Used only to decide WHICH pair an undefined-column error is about when a statement
    carried both 005b's and 010's columns: a bare ``42703`` with no column name matches
    both classifiers, and this is what breaks the tie in favour of the one the database
    actually complained about.
    """
    text = str(detail).lower()
    return any(column in text for column in SIGNAL_ENVIRONMENT_COLUMNS)


def warn_signal_environment_columns_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says which guarantee is not in force."""
    logger.warning(
        "public.signals is missing the signal environment columns (%s). "
        "Apply %s, then restart or wait %.0fs for the re-probe. Until then a signal row is "
        "written in the pre-010 shape: environment and paper_session_id are NOT persisted, "
        "so chk_signals_environment and fk_signals_paper_session do not exist, the "
        "Execution_Environment of Requirement 23.1 is not readable from the row, and a "
        "PAPER signal is not distinguishable from a LIVE one by any column - the Paper_Session "
        "it belongs to is recorded only in paper_orders/paper_events. The signal itself is "
        "still written: an absent column costs an audit field, not the record. Detail: %s",
        ", ".join(SIGNAL_ENVIRONMENT_COLUMNS),
        SIGNAL_ENVIRONMENT_MIGRATION,
        SIGNAL_ENVIRONMENT_COLUMN_RECHECK_SECONDS,
        detail,
    )


async def signal_environment_columns_supported(sb: Any) -> bool:
    """Whether ``public.signals`` carries 010's two columns.

    Read-only and cached: one ``SELECT environment,paper_session_id ... LIMIT 1`` per
    process through the caller's own RLS-scoped client, so the probe sees what the write
    will see. An *indeterminate* answer resolves to ``True`` so the full write is attempted
    and any real failure surfaces at the statement rather than being pre-emptively
    downgraded - the same disposition :func:`signal_lifecycle_columns_supported` takes.
    """
    cached = _cached_signal_environment_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        result = await _execute(
            sb.table("signals")
            .select(",".join(SIGNAL_ENVIRONMENT_COLUMNS))
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if is_missing_signal_environment_column_error(exc):
            _remember_signal_environment_support(False)
            warn_signal_environment_columns_absent(str(exc))
            return False
        logger.warning(
            "Signal environment column probe was inconclusive (%s); attempting the full "
            "projection and letting a real error surface.",
            exc,
        )
        return True

    error = getattr(result, "error", None)
    if error is not None and is_missing_signal_environment_column_error(Exception(str(error))):
        _remember_signal_environment_support(False)
        warn_signal_environment_columns_absent(str(error))
        return False

    _remember_signal_environment_support(True)
    return True


async def signal_environment_projection(sb: Any, columns: str) -> Tuple[str, bool]:
    """``(projection, whether 010's pair is in it)``. ONE probe, ONE decision.

    Both facts from one call because a reader that needs the projection almost always needs
    the verdict too - the pair is what a ``?environment=`` PREDICATE is applied to (task
    29.3), and asking twice is either a second statement or a second chance to disagree.
    """
    supported = await signal_environment_columns_supported(sb)
    if supported:
        return columns, True
    return without_signal_environment_columns(columns), False


async def signal_projection_for(sb: Any, columns: str) -> str:
    """``columns`` as this database can actually answer it: 010's pair kept, or stripped.

    One call, so no reader has to remember both the probe and the strip. Returns
    ``columns`` unchanged when 010 is applied (or when the probe is indeterminate, per that
    function's disposition) and :func:`without_signal_environment_columns` of it when 010
    is definitively absent - with the warning that names the file already emitted by the
    probe.
    """
    projection, _supported = await signal_environment_projection(sb, columns)
    return projection


# ══════════════════════════════════════════════════════════════════════════
# READING THE INPUTS - named keys only, which is what makes containment structural
# ══════════════════════════════════════════════════════════════════════════


def _pick(source: Any, *names: str) -> Any:
    """The first of ``names`` that ``source`` actually reports, or ``None``.

    Duck-typed on purpose, and for the same reason
    ``distributed_idempotency._signal_id_of`` is: the callers on this path are a
    dataclass (``dag_event_loop.Signal``), a mapping (a compiled ACTION node's output),
    a database row read back as a dict, and a test double. All four must work without
    any of them being made to adopt a class from here.

    Reads ONLY the names it is given. It never iterates ``source``, so a key nobody
    asked for cannot travel - that is the whole mechanism behind this module's
    credential containment, and it is why every reader below goes through here.
    """
    if source is None:
        return None
    for name in names:
        if isinstance(source, Mapping):
            if name in source and source[name] is not None:
                return source[name]
            continue
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _text(value: Any) -> Optional[str]:
    """``value`` as a non-empty trimmed string, or ``None``.

    Enum members unwrap to their value, and a ``datetime`` becomes ISO-8601 rather than
    ``str(dt)``: the DAG's event timestamps arrive as ``datetime``, ``str()`` would spell
    them with a space instead of a ``T``, and ``generated_at`` next to them in the same
    row is ISO-8601. Two spellings of an instant in one signal record is one too many.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    raw = getattr(value, "value", value)
    if isinstance(raw, datetime):
        return raw.isoformat()
    text = str(raw).strip()
    return text or None


def _number(value: Any) -> Optional[float]:
    """``value`` as a float, or ``None``. Never raises - an unparseable size is absent."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value: Any) -> Optional[bool]:
    """``value`` as a tri-state boolean: ``True``, ``False``, or ``None`` for unreported.

    ``None`` matters. "The closure did not report its readiness" is not "the closure is
    not ready", and collapsing the two would let this function decide a gate that
    Requirement 14.3 puts in the Live_Runtime (task 10.3).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "ready", "passed", "pass", "ok"):
        return True
    if text in ("false", "0", "no", "n", "blocked", "failed", "fail"):
        return False
    return None


def _jsonable(value: Any, _depth: int = 0) -> Any:
    """``value`` as something PostgREST can put in a JSONB column.

    Bounded in depth, in width and in string length: a node reading may legitimately be
    a whole indicator series, and a signal row is an audit record, not a data dump. What
    exceeds the bound is summarised rather than silently truncated to something that
    reads like the whole value.

    Also the reason ``Decimal`` and ``datetime`` do not need special handling at every
    call site: ``RiskValidationTrace`` is all ``Decimal``, and the DAG's timestamps are
    ``datetime``.
    """
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and len(value) > _MAX_JSON_STRING:
            return value[:_MAX_JSON_STRING] + f"...[{len(value)} chars]"
        return value
    if isinstance(value, float):
        # NaN/Inf are not JSON. They are also a real DAG outcome (an indicator with an
        # insufficient warm-up window), so they are named rather than dropped.
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, Decimal):
        return _jsonable(float(value), _depth)
    if isinstance(value, Enum):
        return _jsonable(value.value, _depth)
    if isinstance(value, datetime):
        return value.isoformat()
    if _depth >= _MAX_JSON_DEPTH:
        return f"[depth>{_MAX_JSON_DEPTH}: {type(value).__name__}]"
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_JSON_ITEMS:
                out["__truncated__"] = f"{len(value)} keys"
                break
            out[str(key)] = _jsonable(item, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out_list = [_jsonable(item, _depth + 1) for item in items[:_MAX_JSON_ITEMS]]
        if len(items) > _MAX_JSON_ITEMS:
            out_list.append(f"[truncated: {len(items)} items]")
        return out_list
    # numpy scalars and anything else that knows how to become a Python scalar.
    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return _jsonable(item_fn(), _depth)
        except Exception:  # noqa: BLE001 - a best-effort unwrap, never fatal
            pass
    return _text(value)


def _dict_of(value: Any) -> Dict[str, Any]:
    """A mapping-ish ``value`` as a JSON-safe dict. ``{}`` for anything else.

    Accepts a mapping, or a dataclass-ish object exposing ``to_dict``/``__dict__``
    (``RiskValidationTrace`` and ``MLInferenceTrace`` are both the latter).
    """
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v, 1) for k, v in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _dict_of(to_dict())
        except Exception:  # noqa: BLE001
            pass
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return {
            str(k): _jsonable(v, 1) for k, v in attrs.items() if not str(k).startswith("_")
        }
    return {}


def _normalise_decision(value: Any) -> Optional[str]:
    """A decision word as ``public.signals.decision`` spells it, or ``None``.

    Folds the DAG's own lower-case ``action`` ('buy') and the compiled ACTION node's
    ``side``/``decision`` onto one uppercase vocabulary. An unrecognised word is NOT
    guessed at - it comes back ``None`` and becomes a refusal, because inventing a side
    for a decision nobody stated is how a real order goes the wrong way.
    """
    text = _text(value)
    if text is None:
        return None
    key = text.strip().lower()
    if key in _DECISION_ALIASES:
        return _DECISION_ALIASES[key]
    upper = text.strip().upper()
    return upper if upper in ACTIONABLE_DECISIONS + ("HOLD",) else None


# ══════════════════════════════════════════════════════════════════════════
# THE SIGNAL RECORD (Requirements 15.1, 15.2)
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Signal:
    """One trading decision, minted once and immutable thereafter.

    ``frozen=True`` is Requirement 15.1's "immutable once assigned", enforced by the
    language rather than by convention: ``signal.id = other`` raises. The order/execution
    reference, which Requirement 15.2 says is persisted "once available" and therefore
    genuinely arrives later, is added with :meth:`with_order_reference`, which returns a
    NEW record - so the id, the attribution and the decision of the original can never be
    edited by the code that learns the order id.

    The field set is CLOSED: no ``**kwargs``, no ``extra``, no ``raw``. That is the whole
    of this record's credential containment (Requirements 15.3, 15.4, 20.3) - a secret has
    no field to land in, and ``exchange_account_id`` (the platform's internal
    Exchange_Account reference, exempted by name in Requirement 20.3) is the only field
    that names which account this signal belongs to.

    :attr:`idempotency_key` is a PROPERTY, not a stored field, and is derived through
    task 9.1's ``idempotency_key_for``. Storing it would create a second copy that could
    disagree with the derivation after a round-trip through the database; deriving it
    means the key a recovery sweep computes and the key the original submission used are
    the same string by construction (Requirement 19.1).
    """

    # ── identity (Requirement 15.1) ─────────────────────────────────────
    #: Globally unique, immutable, never reused. Minted by :func:`mint_signal`.
    id: str
    #: The owning user. Every read of this signal is scoped by it (Requirement 20.1).
    user_id: str

    # ── attribution (Requirement 15.2) ──────────────────────────────────
    strategy_id: str
    #: The version LABEL, as ``public.signals.strategy_version`` and the versioned
    #: deploy route both spell it (e.g. "v3").
    strategy_version: str
    symbol: str
    #: When this decision was made, ISO-8601 UTC. The row's ``generated_at``.
    generated_at: str

    # ── the decision (Requirement 15.2) ─────────────────────────────────
    #: The legacy vocabulary word: BUY, SELL, EXIT or CLOSE. Never HOLD - a HOLD is
    #: not a signal (see :class:`SignalGenerationRefused`).
    decision: str
    #: ENTRY for BUY/SELL, EXIT for EXIT/CLOSE. Requirement 15.2's "signal type".
    signal_type: str
    #: BUY or SELL for an entry. ``None`` for an EXIT/CLOSE whose closing side the DAG
    #: did not state - that side depends on the open position, which this module does
    #: not read and must not guess.
    side: Optional[str]

    # ── optional attribution ────────────────────────────────────────────
    #: The version row's uuid, where the caller knows it. ``public.signals`` has no
    #: column for it, so it is persisted in ``market_info``.
    strategy_version_id: Optional[str] = None
    deployment_id: Optional[str] = None
    #: The platform's INTERNAL Exchange_Account reference - a pointer into the
    #: credential vault, never a credential (Requirement 20.3's exemption).
    exchange_account_id: Optional[str] = None
    #: The venue slug ("binance"), a MARKET descriptor resolved by the deployment
    #: binding. Not an identity and not a credential; see this section's header.
    venue: Optional[str] = None
    timeframe: Optional[str] = None
    worker_id: Optional[str] = None
    #: 'paper' or 'live', from the deployment binding. Carried so a paper signal is
    #: distinguishable from a live one without joining back to the deployment.
    mode: Optional[str] = None

    # ── sizing (Requirement 15.2: "requested quantity OR sizing intention") ──
    quantity: Optional[float] = None
    #: What the DAG said about size when it did not state a quantity (strength,
    #: confidence, a percentage of capital, a notional). At least one of this and
    #: :attr:`quantity` is always present.
    sizing_intention: Optional[Dict[str, Any]] = None

    # ── decision metadata (Requirement 15.2) ────────────────────────────
    #: The ACTION node(s) that produced this decision.
    source_node_ids: Tuple[str, ...] = ()
    #: The evaluated state of every upstream node in the emitting action's closure,
    #: keyed by node id. Requirement 15.2's minimum for decision metadata.
    node_closure: Dict[str, Any] = None  # type: ignore[assignment]
    #: Whether the emitter reported its whole closure READY. ``None`` = not reported;
    #: the gate itself is the Live_Runtime's (Requirement 14.3, task 10.3).
    closure_ready: Optional[bool] = None
    #: The outcome of the risk validation applied to this candidate.
    risk_validation: Dict[str, Any] = None  # type: ignore[assignment]
    #: The ML/DL node's inference output, where one contributed. ``None`` otherwise -
    #: which is a fact about the strategy, not a missing value.
    ml_inference: Optional[Dict[str, Any]] = None
    #: Price, bar time and the other market facts the decision was made against.
    market_context: Dict[str, Any] = None  # type: ignore[assignment]

    # ── lifecycle (Requirements 15.2, 16.1) ─────────────────────────────
    order_lifecycle_state: OrderLifecycleState = INITIAL_ORDER_LIFECYCLE_STATE

    # ── the order/execution reference, once available (Requirement 15.2) ──
    order_id: Optional[str] = None
    execution_id: Optional[str] = None

    def __post_init__(self) -> None:
        # The three mapping fields default to None only because a mutable default is
        # not allowed on a dataclass; they are always dicts on a constructed record, so
        # no reader has to handle both.
        for name in ("node_closure", "risk_validation", "market_context"):
            if getattr(self, name) is None:
                object.__setattr__(self, name, {})

    # ── derived ─────────────────────────────────────────────────────────

    @property
    def idempotency_key(self) -> str:
        """This signal's Idempotency_Key: ``"signal:" + id``. Derived, never stored.

        Task 9.1's function, called here rather than re-implemented, so there is exactly
        one derivation in the codebase (Requirement 19.1).
        """
        return idempotency_key_for(self)

    @property
    def decision_metadata(self) -> Dict[str, Any]:
        """Requirement 15.2's "decision metadata", assembled.

        A view over the fields above rather than a fourth copy of them, so the record
        and the metadata cannot disagree.
        """
        return {
            "signal_type": self.signal_type,
            "side": self.side,
            "decision": self.decision,
            "source_node_ids": list(self.source_node_ids),
            "closure_ready": self.closure_ready,
            "node_closure": dict(self.node_closure or {}),
            "risk_validation": dict(self.risk_validation or {}),
            "ml_inference": dict(self.ml_inference) if self.ml_inference else None,
            "market_context": dict(self.market_context or {}),
            "sizing_intention": dict(self.sizing_intention) if self.sizing_intention else None,
        }

    @property
    def risk_passed(self) -> Optional[bool]:
        """The risk verdict, as a tri-state. ``None`` when no verdict was reported."""
        return _flag(_pick(self.risk_validation or {}, "passed", "risk_passed"))

    def with_order_reference(
        self, *, order_id: Optional[str] = None, execution_id: Optional[str] = None
    ) -> "Signal":
        """A copy carrying the order/execution reference Requirement 15.2 adds later.

        Returns a new record: the original is frozen and stays that way. Task 10.2 owns
        the persistence of these two, and this is the shape it hands back.
        """
        return replace(
            self,
            order_id=order_id if order_id is not None else self.order_id,
            execution_id=execution_id if execution_id is not None else self.execution_id,
        )

    def with_order_lifecycle_state(self, state: OrderLifecycleState) -> "Signal":
        """A copy at ``state``. **Ungated on purpose** - it does not decide legality.

        Task 10.2 calls ``order_lifecycle_state.assert_transition_legal`` first, then
        persists, then rebuilds the in-memory record with this. Putting the gate here
        would hide it inside a data structure, where a caller could skip it by
        constructing a ``Signal`` directly; keeping it out means the gate stays visible
        at the write path where Requirement 16.6 puts it.
        """
        return replace(self, order_lifecycle_state=state)

    # ── projections ─────────────────────────────────────────────────────

    def to_row(
        self,
        *,
        include_lifecycle_columns: bool = True,
        environment: Optional[str] = None,
        paper_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The ``public.signals`` INSERT payload. Exactly the columns that exist.

        ``include_lifecycle_columns=False`` drops 005b's two columns for a database that
        has not had that migration applied yet.

        ``environment`` and ``paper_session_id`` are 010's pair, and both default to
        ``None`` - which is why the LIVE path's payload is byte-for-byte what it was before
        task 29.2. The live writer does not name the environment on purpose: 010 adds the
        column ``NOT NULL DEFAULT 'LIVE'`` precisely so that the only writer which predates
        the Paper_Session still records a true value without being changed (see that file's
        "WHY environment IS NOT NULL"). A PAPER signal must NAME it, because that default
        would otherwise label it ``LIVE``.

        ``paper_session_id`` is written only together with ``environment``: a session
        identifier on a row that does not say which environment produced it would be a
        half-recorded fact, and Requirement 23.2 asks for "the Paper_Session or deployment
        identifier as applicable" - which is only interpretable next to the environment.

        ``status`` is deliberately absent: it is NOT NULL DEFAULT 'pending', so the
        database fills it, and this code does not claim a legacy state for a signal at
        GENERATED (which has no legacy spelling). ``order_id``/``trade_id`` are emitted
        only once set - at generation there is no order to reference, and writing NULL
        would be indistinguishable from a reference that was lost.
        """
        risk = dict(self.risk_validation or {})
        row: Dict[str, Any] = {
            "id": self.id,
            "user_id": self.user_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "deployment_id": self.deployment_id,
            # The venue slug, which this legacy column requires NOT NULL.
            "exchange_id": self.venue,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "worker_id": self.worker_id,
            "decision": self.decision,
            "quantity": self.quantity,
            "generated_at": self.generated_at,
            # Requirement 15.2's decision metadata, over the three JSONB columns this
            # table has. The mapping is stated once in this section's header.
            "indicators": _jsonable(dict(self.node_closure or {})),
            "market_info": _jsonable(self._market_info()),
            "ml_info": _jsonable(dict(self.ml_inference)) if self.ml_inference else None,
            # The risk verdict, over the columns 002/003 already provide for it.
            "risk_passed": self.risk_passed,
            "risk_reason": _text(_pick(risk, "reason", "risk_reason", "block_reason")),
            "position_size": _number(_pick(risk, "position_size", "size")),
            "capital": _number(_pick(risk, "capital")),
            "exposure": _number(_pick(risk, "exposure", "exposure_pct")),
            "expected_loss": _number(_pick(risk, "expected_loss")),
            "expected_reward": _number(_pick(risk, "expected_reward")),
            "drawdown_check": _flag(_pick(risk, "drawdown_check")),
            "risk_evaluated_at": _text(
                _pick(risk, "evaluated_at", "risk_evaluated_at", "validation_end")
            ),
        }
        if include_lifecycle_columns:
            row["order_lifecycle_state"] = self.order_lifecycle_state.value
            row["idempotency_key"] = self.idempotency_key
        if environment is not None:
            row["environment"] = environment
            if paper_session_id is not None:
                row["paper_session_id"] = paper_session_id
        if self.order_id is not None:
            row["order_id"] = self.order_id
        if self.execution_id is not None:
            row["trade_id"] = self.execution_id
        return row

    def _market_info(self) -> Dict[str, Any]:
        """Everything Requirement 15.2 lists that ``public.signals`` has no column for.

        Not a junk drawer: each key is here because the schema offers it no column of
        its own, and the set is closed by this method rather than by a caller.
        """
        info: Dict[str, Any] = dict(self.market_context or {})
        info.update(
            {
                "signal_type": self.signal_type,
                "side": self.side,
                "mode": self.mode,
                "strategy_version_id": self.strategy_version_id,
                # The internal Exchange_Account reference (Requirement 20.3's exemption).
                # public.signals has no column for it, and it is the only field on this
                # record that names which account the signal belongs to.
                "exchange_account_id": self.exchange_account_id,
                "source_node_ids": list(self.source_node_ids),
                "closure_ready": self.closure_ready,
                "sizing_intention": dict(self.sizing_intention)
                if self.sizing_intention
                else None,
            }
        )
        return info

    def to_public_dict(self) -> Dict[str, Any]:
        """What an API response or a WebSocket frame may carry for this signal.

        The same closed field set as the record, which is what makes Requirement 20.3
        hold on the wire for free: there is no credential field to omit. The signal-trace
        router (task 13) and the ``SIGNAL_FAMILY`` channel (task 14) both render from
        here rather than from a row, so one shape reaches the frontend.
        """
        return {
            "id": self.id,
            "user_id": self.user_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_version_id": self.strategy_version_id,
            "deployment_id": self.deployment_id,
            "exchange_account_id": self.exchange_account_id,
            "venue": self.venue,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "worker_id": self.worker_id,
            "mode": self.mode,
            "generated_at": self.generated_at,
            "decision": self.decision,
            "signal_type": self.signal_type,
            "side": self.side,
            "quantity": self.quantity,
            "sizing_intention": dict(self.sizing_intention) if self.sizing_intention else None,
            "order_lifecycle_state": self.order_lifecycle_state.value,
            "idempotency_key": self.idempotency_key,
            "order_id": self.order_id,
            "execution_id": self.execution_id,
            "decision_metadata": self.decision_metadata,
        }


# ══════════════════════════════════════════════════════════════════════════
# MINTING - pure, no I/O, so an id and a shape can be tested without a database
# ══════════════════════════════════════════════════════════════════════════


def _deployment_facts(deployment: Any) -> Dict[str, Any]:
    """The ten named facts a signal takes from its deployment. Nothing else is read.

    Accepts a ``strategy_deployments`` row (a dict), a
    ``deployment_binding.DeploymentBinding`` (which has ``version``/``version_id`` and
    no ``id``), or any object exposing the same names.

    This projection is the second half of this module's credential containment: the
    deployment is never iterated and never copied wholesale, so a column that happens to
    carry something sensitive has no route onto a signal.
    """
    return {
        "deployment_id": _text(_pick(deployment, "id", "deployment_id")),
        "user_id": _text(_pick(deployment, "user_id", "owner_id")),
        "strategy_id": _text(_pick(deployment, "strategy_id")),
        "strategy_version": _text(_pick(deployment, "version", "strategy_version")),
        "strategy_version_id": _text(_pick(deployment, "version_id", "strategy_version_id")),
        "exchange_account_id": _text(_pick(deployment, "exchange_account_id")),
        "venue": _text(_pick(deployment, "exchange_id", "venue", "exchange")),
        # ``exchange_symbol`` is the column ``strategy_deployments`` actually carries the
        # market in (003_signal_trace_restoration.sql), and the one
        # ``strategy_service.deploy_version`` writes. Aliased here for the same reason
        # every other fact above is aliased: a ``DeploymentBinding`` says ``symbol`` and a
        # persisted row says ``exchange_symbol``, and this projection has to read both.
        # Without it a Signal minted from a persisted deployment row alone was refused with
        # SIGNAL_ATTRIBUTION_INCOMPLETE - found by the Requirement 26.4 end-to-end sandbox
        # suite (``tests/sandbox_lifecycle/``).
        "symbol": _text(_pick(deployment, "symbol", "exchange_symbol")),
        "timeframe": _text(_pick(deployment, "timeframe")),
        "mode": _text(_pick(deployment, "mode")),
    }


def _risk_facts(node_output: Any) -> Dict[str, Any]:
    """The risk verdict the candidate was validated against, projected.

    Requirement 15.2 wants "the outcome of the risk validation applied to the candidate
    decision" persisted AT GENERATION, which is consistent with Requirement 14.3's
    ordering: risk validation runs before a Signal exists, so its verdict is an input
    here, not something this function computes.

    Accepts a mapping or a ``signal_trace_engine.RiskValidationTrace``; reads named
    fields from either.
    """
    source = _pick(node_output, "risk_validation", "risk", "risk_trace", "risk_result")
    if source is None:
        return {}
    data = _dict_of(source)
    passed = _flag(_pick(data, "passed", "risk_passed", "approved"))
    blocked = _flag(_pick(data, "blocked"))
    if passed is None and blocked is not None:
        passed = not blocked
    facts: Dict[str, Any] = {
        "passed": passed,
        "blocked": blocked,
        "reason": _text(_pick(data, "reason", "risk_reason", "block_reason")),
        "checks": _jsonable(_pick(data, "checks") or []),
        "position_size": _number(_pick(data, "position_size", "size")),
        "capital": _number(_pick(data, "capital")),
        "exposure": _number(_pick(data, "exposure", "exposure_pct")),
        "expected_loss": _number(_pick(data, "expected_loss")),
        "expected_reward": _number(_pick(data, "expected_reward")),
        "drawdown_check": _flag(_pick(data, "drawdown_check")),
        "evaluated_at": _text(_pick(data, "evaluated_at", "validation_end")),
    }
    return {k: v for k, v in facts.items() if v is not None and v != []}


def _ml_facts(node_output: Any) -> Optional[Dict[str, Any]]:
    """The ML/DL node's inference output, where one contributed. ``None`` otherwise.

    ``None`` is a fact - "this strategy version has no ML node" - not a missing value,
    which is why ``ml_info`` is the one nullable JSONB column on ``public.signals``.
    """
    source = _pick(node_output, "ml_inference", "ml_info", "ml_trace", "inference")
    if source is None:
        return None
    data = _dict_of(source)
    facts = {
        "model_id": _text(_pick(data, "model_id")),
        "model_version": _text(_pick(data, "model_version")),
        "prediction": _text(_pick(data, "prediction")),
        "confidence": _number(_pick(data, "confidence")),
        "probabilities": _jsonable(_pick(data, "probabilities")),
        "features": _jsonable(_pick(data, "features")),
        "inference_ms": _number(_pick(data, "inference_ms")),
    }
    facts = {k: v for k, v in facts.items() if v is not None}
    return facts or None


def _closure_readings(node_output: Any) -> Dict[str, Any]:
    """The evaluated state of every upstream node in the emitting action's closure.

    Keyed by node id, each reading projected to a fixed shape rather than copied as-is,
    so one node's payload cannot change the shape of the metadata. Requirement 15.2's
    minimum for decision metadata.
    """
    source = _pick(
        node_output, "node_closure", "node_states", "closure", "upstream_nodes", "indicators"
    )
    if source is None:
        return {}
    if not isinstance(source, Mapping):
        # A list of per-node readings, each naming its own node.
        readings: Dict[str, Any] = {}
        try:
            items = list(source)
        except TypeError:
            return {}
        for item in items[:_MAX_JSON_ITEMS]:
            node_id = _text(_pick(item, "node_id", "id", "node"))
            if node_id:
                readings[node_id] = _projected_reading(item)
        return readings
    return {
        str(node_id): _projected_reading(reading)
        for node_id, reading in list(source.items())[:_MAX_JSON_ITEMS]
    }


def _projected_reading(reading: Any) -> Any:
    """One node's evaluated state, in a fixed shape.

    A bare value (the common case - an indicator's latest reading) stays a bare value.
    A structured reading is projected onto the four named things a closure reading can
    say; anything else it carries is not persisted, because the audit question this
    answers is "what did this node evaluate to", not "what else was in scope".
    """
    if reading is None or isinstance(reading, (bool, int, float, str, Decimal)):
        return _jsonable(reading, 1)
    if isinstance(reading, (list, tuple)):
        return _jsonable(reading, 1)
    named = {
        "value": _jsonable(_pick(reading, "value", "output", "result", "reading"), 1),
        "node_type": _text(_pick(reading, "node_type", "type")),
        "ready": _flag(_pick(reading, "ready", "is_ready")),
        "status": _text(_pick(reading, "status")),
    }
    named = {k: v for k, v in named.items() if v is not None}
    return named or _jsonable(reading, 1)


def _first_reported(*values: Any) -> Any:
    """The first argument that is not ``None``.

    Not ``a or b``: a price of ``0.0`` and a strength of ``0`` are reported values, and
    ``or`` would silently skip them in favour of a lower-priority source.
    """
    for value in values:
        if value is not None:
            return value
    return None


def _market_facts(node_output: Any, deployment_facts: Mapping[str, Any]) -> Dict[str, Any]:
    """The market context the decision was made against.

    Read from the node output first and the deployment second: the event the decision
    was made on knows its own bar and price, and the deployment knows only what it
    subscribed to.
    """
    declared = _dict_of(_pick(node_output, "market_context", "market_info", "market"))
    facts: Dict[str, Any] = {
        "symbol": _text(_pick(node_output, "symbol")) or deployment_facts.get("symbol"),
        "timeframe": _text(_pick(node_output, "timeframe"))
        or deployment_facts.get("timeframe"),
        "price": _number(
            _first_reported(
                _pick(node_output, "price", "last_price", "close"),
                _pick(declared, "price", "last_price", "close"),
            )
        ),
        "bar_time": _text(
            _first_reported(
                _pick(node_output, "bar_time", "bar_open_time", "timestamp", "event_time"),
                _pick(declared, "bar_time", "bar_open_time", "timestamp"),
            )
        ),
        "strength": _number(_pick(node_output, "strength")),
        "confidence": _number(_pick(node_output, "confidence")),
    }
    facts = {k: v for k, v in facts.items() if v is not None}
    # Named market facts the emitter volunteered, kept under one key so they can never
    # shadow one of the above.
    extra = {k: v for k, v in declared.items() if k not in facts}
    if extra:
        facts["reported"] = _jsonable(extra, 1)
    return facts


def _sizing_facts(node_output: Any) -> Tuple[Optional[float], Optional[Dict[str, Any]]]:
    """``(quantity, sizing_intention)``. Requirement 15.2 wants one or the other.

    A quantity is a stated number of units. A sizing intention is what the DAG said
    when it did not state one - a strength, a confidence, a percentage of capital, a
    notional. ``dag_event_loop.Signal`` always carries ``strength``, so the intention is
    almost always derivable; when NOTHING about size is stated, minting refuses rather
    than persisting a decision whose size nobody can reconstruct.
    """
    quantity = _number(
        _pick(node_output, "quantity", "requested_quantity", "size", "amount")
    )

    declared = _pick(node_output, "sizing_intention", "sizing")
    intention: Dict[str, Any] = _dict_of(declared) if declared is not None else {}
    if not intention:
        for key, names in (
            ("strength", ("strength",)),
            ("confidence", ("confidence",)),
            ("position_size_pct", ("position_size_pct", "size_pct", "percent_of_capital")),
            ("notional", ("notional", "quote_amount")),
            ("position_size", ("position_size",)),
        ):
            value = _number(_pick(node_output, *names))
            if value is not None:
                intention[key] = value
        if intention:
            intention["basis"] = "reported_by_action_node"
    return quantity, (intention or None)


def _source_nodes(node_output: Any) -> Tuple[str, ...]:
    """The ACTION node(s) that produced this decision, de-duplicated, order preserved."""
    raw = _pick(
        node_output, "source_node_ids", "action_nodes", "trigger_nodes", "trigger_node",
        "node_id", "action_node_id",
    )
    if raw is None:
        return ()
    candidates = raw if isinstance(raw, (list, tuple, set, frozenset)) else [raw]
    seen: Dict[str, None] = {}
    for candidate in candidates:
        text = _text(_pick(candidate, "node_id", "id") if not isinstance(candidate, str) else candidate)
        if text is None:
            text = _text(candidate)
        if text:
            seen.setdefault(text, None)
    return tuple(seen)


def mint_signal(
    deployment: Any,
    node_output: Any,
    *,
    now: Optional[datetime] = None,
    signal_id: Optional[str] = None,
) -> Signal:
    """Build the :class:`Signal` for one ACTION-node output. Pure - no I/O.

    Separated from :func:`generate_signal` so the record's shape, its id minting and
    every refusal can be exercised without a database, and so the property tests for
    identifier uniqueness (task 10.7) and credential containment (task 10.6) can generate
    signals in bulk.

    THE ID
        ``uuid4`` - 122 random bits, minted here and nowhere else on this path, matching
        what ``public.signals.id``'s own ``gen_random_uuid()`` default would produce.
        Minted client-side rather than left to the database because the Idempotency_Key
        is derived from it (Requirement 19.1) and the key must exist before the INSERT
        that records it, not after. ``signal_id`` is injectable for a crash-recovery
        sweep re-deriving a known signal's record - it is NOT a way to reuse an id, and
        Requirement 15.1's "never reused" is backed by ``uq_signals_idempotency_key``
        refusing a second row for the same key.

    Raises
        :class:`SignalGenerationRefused` when the inputs do not describe a signal that
        can be attributed, sized or acted on. Raised BEFORE any id is minted where the
        problem is with the attribution, so a refused candidate consumes no identifier.
    """
    facts = _deployment_facts(deployment)

    decision = _normalise_decision(
        _pick(node_output, "decision", "action", "side", "signal_type")
    )
    if decision is None:
        raise SignalGenerationRefused(
            "SIGNAL_DECISION_UNRECOGNISED",
            "This action node output names no decision this platform can act on. A "
            f"signal must say one of {list(ACTIONABLE_DECISIONS)}; guessing a side for "
            "an unnamed decision is how a real order goes the wrong way.",
            {"recognised_decisions": list(ACTIONABLE_DECISIONS)},
        )
    if decision not in ACTIONABLE_DECISIONS:
        raise SignalGenerationRefused(
            "SIGNAL_DECISION_NOT_ACTIONABLE",
            f"A {decision} decision is not a signal: it asks for no order, so it has no "
            "Order_Lifecycle_State to reach and no Idempotency_Key to be guarded by. "
            "Nothing is persisted for it.",
            {"decision": decision, "actionable_decisions": list(ACTIONABLE_DECISIONS)},
        )

    symbol = _text(_pick(node_output, "symbol")) or facts["symbol"]
    missing = [
        name
        for name, value in (
            ("user_id", facts["user_id"]),
            ("strategy_id", facts["strategy_id"]),
            ("strategy_version", facts["strategy_version"]),
            ("symbol", symbol),
            ("exchange_id (venue)", facts["venue"]),
        )
        if not value
    ]
    if missing:
        raise SignalGenerationRefused(
            "SIGNAL_ATTRIBUTION_INCOMPLETE",
            "A signal must be traceable back to exactly which strategy, version, "
            "deployment and market produced it (Requirement 15.2), and this deployment "
            f"does not report: {', '.join(missing)}. No signal is minted, so no "
            "unattributable row and no identifier are consumed.",
            {"missing": missing, "deployment_id": facts["deployment_id"]},
        )

    risk = _risk_facts(node_output)
    if risk.get("passed") is False or risk.get("blocked") is True:
        raise SignalGenerationRefused(
            "SIGNAL_RISK_VALIDATION_FAILED",
            "Risk validation refused this candidate, so no Signal is generated for it "
            "(Requirement 14.3). The refusal itself belongs on the deployment's own "
            "failure record, not on a signal row.",
            {
                "reason": risk.get("reason"),
                "deployment_id": facts["deployment_id"],
                "symbol": symbol,
            },
        )

    quantity, sizing_intention = _sizing_facts(node_output)
    if quantity is None and sizing_intention is None:
        raise SignalGenerationRefused(
            "SIGNAL_SIZING_UNSPECIFIED",
            "A signal must record either a requested quantity or a sizing intention "
            "(Requirement 15.2), and this action node output states neither. Persisting "
            "a decision whose size nobody can reconstruct would make the audit trail "
            "unable to answer what was actually asked for.",
            {"decision": decision, "symbol": symbol},
        )

    side = decision if decision in ENTRY_DECISIONS else _normalise_decision(
        _pick(node_output, "closing_side", "exit_side")
    )
    if side is not None and side not in ENTRY_DECISIONS:
        side = None

    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    return Signal(
        id=str(signal_id) if signal_id else str(uuid4()),
        user_id=facts["user_id"],
        strategy_id=facts["strategy_id"],
        strategy_version=facts["strategy_version"],
        symbol=symbol,
        generated_at=instant.isoformat(),
        decision=decision,
        signal_type="ENTRY" if decision in ENTRY_DECISIONS else "EXIT",
        side=side,
        strategy_version_id=facts["strategy_version_id"],
        deployment_id=facts["deployment_id"],
        exchange_account_id=facts["exchange_account_id"],
        venue=facts["venue"],
        timeframe=_text(_pick(node_output, "timeframe")) or facts["timeframe"],
        worker_id=_text(_pick(node_output, "worker_id")) or _text(_pick(deployment, "worker_id")),
        mode=facts["mode"],
        quantity=quantity,
        sizing_intention=sizing_intention,
        source_node_ids=_source_nodes(node_output),
        node_closure=_closure_readings(node_output),
        closure_ready=_flag(
            _pick(node_output, "closure_ready", "all_upstream_ready", "upstream_ready")
        ),
        risk_validation=risk,
        ml_inference=_ml_facts(node_output),
        market_context=_market_facts(node_output, facts),
        order_lifecycle_state=INITIAL_ORDER_LIFECYCLE_STATE,
    )


# ══════════════════════════════════════════════════════════════════════════
# GENERATION = MINT, THEN PERSIST (Requirements 15.1 - 15.5)
# ══════════════════════════════════════════════════════════════════════════


async def _resolve_client(sb: Any, user: Any, deployment: Any) -> Any:
    """The RLS-scoped PostgREST client to write through.

    Prefers the one the caller passed - the Live_Runtime holds one per deployment - and
    otherwise builds one from the user's own access token, exactly as
    ``SignalService._get_supabase`` does, so the INSERT runs under the owner's identity
    and RLS applies (Requirement 20.1).
    """
    if sb is not None:
        return sb
    token = _text(_pick(user, "access_token")) or _text(_pick(deployment, "access_token"))
    if not token:
        return None
    try:
        result = create_request_supabase_async(token)
        return await result if inspect.isawaitable(result) else result
    except Exception as exc:  # noqa: BLE001 - reported as a persistence failure below
        logger.warning("Could not build a Supabase client for the signal write: %s", exc)
        return None


def _environment_pair_is_absent(
    exc: BaseException, *, environment: Optional[str], with_lifecycle: bool
) -> bool:
    """Whether this INSERT failure is 010's pair missing, rather than 005b's.

    Both classifiers accept a bare ``42703`` with no column name, so when one statement
    carried BOTH pairs the tie is broken by which columns the message names: if it names
    ``environment`` or ``paper_session_id``, 010 is the answer; if it names neither and
    005b's pair was also on the payload, 005b is retried first and a second, still-failing
    attempt converges on 010 (by which point ``with_lifecycle`` is ``False``, so this
    predicate no longer has to share the message with anything).
    """
    if environment is None:
        return False
    if not is_missing_signal_environment_column_error(exc):
        return False
    if with_lifecycle and not _names_signal_environment_column(exc):
        return False
    return True


async def _insert_signal_row(
    sb: Any,
    signal: Signal,
    *,
    with_lifecycle: bool,
    environment: Optional[str] = None,
    paper_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """One INSERT. Degrades to the legacy shape when 005b's or 010's columns are absent.

    ``environment`` / ``paper_session_id`` are 010's pair and default to ``None``, so a LIVE
    write issues exactly the statement it always did. When they ARE given and 010 turns out
    not to be applied, the pair is dropped and the row is written without it - the same
    degradation 005b's pair gets, with a warning naming ``010_signal_environment.sql``.
    This is the ONE insert on this path: a PAPER signal is the same row shape as a LIVE one,
    distinguished by a column, and there is no second store (Requirement 23.5).

    A 23505 on ``uq_signals_idempotency_key`` is translated into ``DuplicateOrderError``
    rather than reported as a persistence failure. That is migration 005b section 1's
    stated contract for the caller of this insert, and it is what makes the partial unique
    index a working durable backstop instead of a 500: the index exists so that a second
    row for the same Idempotency_Key cannot be created even if Redis was flushed, and the
    correct response to hitting it is "an order for this signal is already accounted for",
    which is what ``submit_signal`` then answers with the signal's current state
    (Requirements 19.1, 21.2, 21.7). The translation lives here, at the insert, because
    this is the insert that carries the key. See
    :func:`is_duplicate_idempotency_key_error` on why it is deliberately narrow - a 23505
    on any other index is still a persistence failure.
    """
    payload = signal.to_row(
        include_lifecycle_columns=with_lifecycle,
        environment=environment,
        paper_session_id=paper_session_id,
    )

    def _retry(*, lifecycle: bool, env: Optional[str]) -> Any:
        return _insert_signal_row(
            sb,
            signal,
            with_lifecycle=lifecycle,
            environment=env,
            paper_session_id=paper_session_id if env is not None else None,
        )

    try:
        result = await _execute(sb.table("signals").insert(payload).execute())
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if _environment_pair_is_absent(exc, environment=environment, with_lifecycle=with_lifecycle):
            remember_signal_environment_columns_absent()
            warn_signal_environment_columns_absent(str(exc))
            return await _retry(lifecycle=with_lifecycle, env=None)
        if with_lifecycle and is_missing_signal_lifecycle_column_error(exc):
            remember_signal_lifecycle_columns_absent()
            warn_signal_lifecycle_columns_absent(str(exc))
            return await _retry(lifecycle=False, env=environment)
        if is_duplicate_idempotency_key_error(exc):
            raise DuplicateOrderError(
                f"A signal row already carries idempotency key {signal.idempotency_key} "
                f"(uq_signals_idempotency_key). At most one order exists for this signal "
                f"(Requirement 19.1); read its current Order_Lifecycle_State rather than "
                f"submitting again."
            ) from exc
        raise SignalPersistenceError(
            f"Signal {signal.id} could not be persisted, so it is not reported and not "
            f"routed to risk validation or order submission (Requirement 15.5): {exc}",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        ) from exc

    error = getattr(result, "error", None)
    if error is not None:
        reported = Exception(str(error))
        if _environment_pair_is_absent(
            reported, environment=environment, with_lifecycle=with_lifecycle
        ):
            remember_signal_environment_columns_absent()
            warn_signal_environment_columns_absent(str(error))
            return await _retry(lifecycle=with_lifecycle, env=None)
        if with_lifecycle and is_missing_signal_lifecycle_column_error(reported):
            remember_signal_lifecycle_columns_absent()
            warn_signal_lifecycle_columns_absent(str(error))
            return await _retry(lifecycle=False, env=environment)
        # PostgREST reports a constraint violation as an error OBJECT on the response as
        # often as it raises, so 005b's 23505 contract is honoured on both shapes.
        if is_duplicate_idempotency_key_error(Exception(str(error))):
            raise DuplicateOrderError(
                f"A signal row already carries idempotency key {signal.idempotency_key} "
                f"(uq_signals_idempotency_key). At most one order exists for this signal "
                f"(Requirement 19.1); read its current Order_Lifecycle_State rather than "
                f"submitting again."
            )
        raise SignalPersistenceError(
            f"Signal {signal.id} could not be persisted, so it is not reported and not "
            f"routed to risk validation or order submission (Requirement 15.5): {error}",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        )

    data = getattr(result, "data", None)
    if not data:
        raise SignalPersistenceError(
            f"The INSERT for signal {signal.id} returned no row, so this process cannot "
            f"confirm the signal was persisted and will not act on it "
            f"(Requirement 15.5).",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        )
    return data[0] if isinstance(data, list) else data


async def generate_signal(
    deployment: Any,
    node_output: Any,
    *,
    sb: Any = None,
    user: Any = None,
    now: Optional[datetime] = None,
    signal_id: Optional[str] = None,
) -> Signal:
    """Mint a signal id, persist a ``GENERATED`` row, return the :class:`Signal`.

    ``design.md`` -> "New module: ``signal_service.py``". Requirements 15.1 - 15.5.

    THE ORDER OF EVENTS IS THE POINT
        Mint, then persist, then return. A ``Signal`` comes back from here only when a
        row exists for it, which is how Requirement 15.5 ("do not report it, do not route
        it to risk validation or order submission") and Requirement 14.5 ("persist before
        reporting") are enforced rather than merely documented: there is no return path
        that hands back an unpersisted signal. A failure raises
        :class:`SignalPersistenceError`, and task 10.3's event loop is what records it
        against the deployment and moves on to the next event (Requirement 14.7).

    WHAT IS PERSISTED
        Every field Requirement 15.2 lists, including the decision metadata (the
        evaluated state of every upstream node in the emitting action's closure), the
        risk validation outcome, the ML inference output where an ML node contributed,
        the canonical ``GENERATED`` state, and the Idempotency_Key derived by task 9.1's
        ``idempotency_key_for``. See this section's header for the column-by-column
        mapping and for why ``status`` is left to its database default.

    WHAT IS NOT
        No exchange credential, API key, secret, passphrase, access token or
        exchange-issued account identifier - not because they are stripped, but because
        the record has no field for one and this function reads no source that holds one
        (Requirements 15.3, 15.4, 20.3).

    Args:
        deployment: The ``strategy_deployments`` row or the ``DeploymentBinding`` this
            signal belongs to. Read through ten named keys only.
        node_output: What the ACTION node evaluated to, plus the risk verdict its
            candidate was validated against. A mapping, a ``dag_event_loop.Signal``, or
            any object exposing the same names.
        sb: An RLS-scoped PostgREST client. Built from ``user``'s access token when not
            given.
        user: The owning user, for the client. Not an authorisation decision - the row's
            ``user_id`` comes from the deployment, and RLS is what enforces it.
        now: The generation instant, for a deterministic test.
        signal_id: An id to reuse instead of minting one. For a crash-recovery sweep
            re-deriving a known signal, never for a new decision.

    Returns:
        The persisted :class:`Signal`, at ``GENERATED``.

    Raises:
        SignalGenerationRefused: The inputs do not describe an actionable, attributable,
            sized signal. Nothing is minted and nothing is written.
        SignalPersistenceError: The row could not be written. Requirement 15.5 -
            the caller must not route this signal anywhere.
    """
    signal = mint_signal(deployment, node_output, now=now, signal_id=signal_id)
    return await _persist_signal(signal, sb=sb, user=user, deployment=deployment)


async def _persist_signal(
    signal: Signal,
    *,
    sb: Any,
    user: Any,
    deployment: Any,
    environment: Optional[str] = None,
    paper_session_id: Optional[str] = None,
) -> Signal:
    """Resolve the client, probe, INSERT, reconcile the stored id, log. The ONE write.

    Lifted out of :func:`generate_signal` unchanged in behaviour so that
    :func:`generate_paper_signal` reaches the SAME statement rather than a second one
    (Requirement 23.5: ``PAPER`` signals are recorded through the same path as ``LIVE``
    ones, and no second signal store is introduced). ``environment`` and
    ``paper_session_id`` default to ``None``, which is exactly the live call, so the live
    path issues the same probe and the same INSERT it issued before task 29.2 - the 010
    probe runs only for a caller that names an environment.
    """
    client = await _resolve_client(sb, user, deployment)
    if client is None:
        raise SignalPersistenceError(
            f"No database client is available to persist signal {signal.id}, so it is "
            f"not reported and not routed to risk validation or order submission "
            f"(Requirement 15.5). Note this is deliberately NOT the in-memory fallback "
            f"SignalService.create_signal keeps: a process-local dict is not persistence, "
            f"and a signal that only this process knows about is exactly the signal that "
            f"gets submitted twice after a restart.",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
            code="SIGNAL_NO_PERSISTENCE_CLIENT",
        )

    with_lifecycle = await signal_lifecycle_columns_supported(client)
    with_environment = (
        await signal_environment_columns_supported(client) if environment is not None else False
    )
    row = await _insert_signal_row(
        client,
        signal,
        with_lifecycle=with_lifecycle,
        environment=environment if with_environment else None,
        paper_session_id=paper_session_id if with_environment else None,
    )

    # The database is authoritative about the id it stored. It should be the one that was
    # minted (this INSERT names it), and if it is not, the Idempotency_Key derived from
    # the minted id would guard a different row than the one that exists - so the
    # returned record follows the row rather than the mint.
    stored_id = _text(_pick(row, "id"))
    if stored_id and stored_id != signal.id:
        logger.warning(
            "public.signals stored signal id %s where %s was minted; the returned record "
            "and its Idempotency_Key follow the stored row.",
            stored_id,
            signal.id,
        )
        signal = replace(signal, id=stored_id)

    # ── the strategy row's last_signal_at (BC-3's producer) ─────────────
    # vyomquant-ui-redesign task 12.3's follow-up, Requirements 4.1, 19.2. Scheduled, NOT
    # awaited, and only from HERE - after the row above is confirmed persisted. Nothing on
    # this path waits for it or can be failed by it: a signal recorded without its
    # denormalised timestamp is acceptable, the reverse is not. See
    # ``strategy_last_signal``'s module docstring for the isolation this relies on, and
    # ``015_strategy_last_signal_at.sql`` for why the column is written rather than derived.
    schedule_last_signal_at(
        client,
        strategy_id=signal.strategy_id,
        user_id=signal.user_id,
        generated_at=signal.generated_at,
    )

    logger.info(
        "Generated signal %s: %s %s on %s (deployment %s, strategy %s %s) at %s",
        signal.id,
        signal.decision,
        signal.symbol,
        signal.venue,
        signal.deployment_id,
        signal.strategy_id,
        signal.strategy_version,
        signal.order_lifecycle_state.value,
    )
    return signal


# ══════════════════════════════════════════════════════════════════════════
# TASK 29.2 - the PAPER write, through the path above and no other
#
# A Paper_Session is NOT a deployment, and that is the whole shape of this section.
# ``mint_signal`` reads its attribution through ten NAMED keys (:func:`_deployment_facts`),
# so a Paper_Session is admitted by handing it the same ten facts rather than by teaching
# the minter about a second kind of owner. Three of those facts differ from a deployment's
# and each difference is a fact about paper trading, not a workaround:
#
#   * ``deployment_id`` is absent, so it is written NULL. 010's own comment on
#     ``signals.paper_session_id`` states this in terms: "signals.deployment_id stays NULL
#     for a paper signal: a Paper_Session is not a deployment, and paper_session_id is the
#     applicable identifier." Requirement 23.2 asks for "the Paper_Session or deployment
#     identifier AS APPLICABLE", and for a session the applicable one is the session.
#   * ``exchange_account_id`` is absent, because a Paper_Session trades no real account.
#     There is no credential to point at and none is invented.
#   * ``mode`` is ``'paper'`` and ``environment`` is ``'PAPER'``. The first is the
#     pre-existing spelling inside ``market_info`` and is left exactly as it was; the
#     second is the COLUMN Requirement 23.1 adds, and it is the one a reader filters on.
#
# Everything else - the id, the Idempotency_Key derivation, the decision vocabulary, the
# refusals, the JSONB decision metadata, the canonical ``GENERATED`` state and the INSERT
# itself - is the live path's, unchanged, because it IS the live path.
# ══════════════════════════════════════════════════════════════════════════


def paper_session_facts(session: Any, *, version: Any = None) -> Dict[str, Any]:
    """The attribution a ``PAPER`` signal takes from its Paper_Session, as named keys.

    Shaped for :func:`_deployment_facts` - which is why it is a plain mapping and not a new
    type: the minter reads ten names, this supplies them, and no second attribution path
    is introduced.

    ``session`` is a ``paper_sessions`` row (or anything exposing the same names).
    ``version`` is the ``strategy_versions`` row for ``session.version_id``, supplied by the
    layer that already resolved it for the session's plan; it is what carries the version
    LABEL (``strategy_versions.version``, e.g. ``"v3"``), which a ``paper_sessions`` row does
    not hold. Without it the label falls back to the session's own spelling and, failing
    that, is absent - at which point :func:`mint_signal` refuses with
    ``SIGNAL_ATTRIBUTION_INCOMPLETE`` rather than inventing one.

    NO ``id`` / ``deployment_id`` KEY IS RETURNED, and that is deliberate rather than an
    omission: ``_deployment_facts`` reads ``deployment_id`` from ``id``, and a
    ``paper_sessions.id`` landing in ``signals.deployment_id`` would name a deployment that
    does not exist. The session identifier travels in ``paper_session_id`` instead.
    """
    return {
        "user_id": _text(_pick(session, "user_id", "owner_id")),
        "strategy_id": _text(_pick(session, "source_strategy_id", "strategy_id")),
        "strategy_version": _text(
            _pick(version, "version", "version_label")
            or _pick(session, "strategy_version", "version")
        ),
        "version_id": _text(_pick(session, "version_id") or _pick(version, "id")),
        "exchange_id": _text(_pick(session, "exchange_id", "venue", "exchange")),
        "symbol": _text(_pick(session, "symbol", "exchange_symbol")),
        "timeframe": _text(_pick(session, "timeframe")),
        # The pre-existing in-row spelling (``market_info.mode``), not the new column.
        "mode": "paper",
    }


async def generate_paper_signal(
    session: Any,
    node_output: Any,
    *,
    paper_session_id: Any = None,
    version: Any = None,
    sb: Any = None,
    user: Any = None,
    now: Optional[datetime] = None,
    signal_id: Optional[str] = None,
) -> Signal:
    """Record one Paper_Session decision in the Signal_Trace. Requirements 23.1, 23.5.

    The ``PAPER`` counterpart of :func:`generate_signal`, and NOT a second recording path:
    it mints through :func:`mint_signal` and persists through :func:`_persist_signal`, so
    the table, the projection, the INSERT, the ``42703`` degradations and the duplicate-key
    contract are the ones the live path uses. What it adds is three columns' worth of
    attribution: ``environment='PAPER'``, ``paper_session_id`` set, and ``deployment_id``
    left NULL.

    ``signals.order_lifecycle_state`` carries the unchanged vocabulary from
    ``order_lifecycle_state.py`` - the row starts at ``GENERATED`` exactly as a live one
    does, and no paper-specific state is introduced.

    Args:
        session: The ``paper_sessions`` row this decision was made inside.
        node_output: What the strategy runtime produced for the bar - the evaluator's OWN
            object, read through named keys. Deliberately not a narrowed projection: the
            trace records Requirement 23.2's full detail and Requirement 23.3 restricts it
            for a subscriber at READ time (task 29.4), so narrowing here would shrink the
            record rather than the disclosure.
        paper_session_id: The Paper_Session identifier to record. Defaults to the session
            row's own ``id``.
        version: The ``strategy_versions`` row, for the version label. See
            :func:`paper_session_facts`.
        sb: An RLS-scoped PostgREST client. Required in practice for a paper session: the
            fallback builds one from an access token, and a session loop holds a client
            rather than a token.
        user: The owning user, for that fallback.
        now: The generation instant, for a deterministic test.
        signal_id: An id to reuse instead of minting one - for a replay re-deriving a known
            signal's record (the Paper_Session's identifiers are UUIDv5-derived precisely so
            a replay reproduces them).

    Returns:
        The persisted :class:`Signal`, at ``GENERATED``, with ``deployment_id`` ``None``.

    Raises:
        SignalGenerationRefused: The session and output do not describe an actionable,
            attributable, sized signal. Nothing is minted and nothing is written.
        SignalPersistenceError: The row could not be written. The caller - the paper session
            loop - logs and continues: a paper order is durably recorded in
            ``paper_orders``/``paper_events`` regardless, and that asymmetry with the live
            path is stated where the swallow lives, not here.
    """
    facts = paper_session_facts(session, version=version)
    signal = mint_signal(facts, node_output, now=now, signal_id=signal_id)

    resolved_session_id = _text(paper_session_id) or _text(_pick(session, "id", "session_id"))
    if not resolved_session_id:
        raise SignalGenerationRefused(
            "PAPER_SIGNAL_SESSION_UNIDENTIFIED",
            "A PAPER signal must name the Paper_Session it was generated inside "
            "(Requirement 23.2), and neither the session row nor the caller states an "
            "identifier. Recording it without one would produce a row that says PAPER and "
            "cannot say which session, which is the fact the column exists to hold.",
            {"strategy_id": facts["strategy_id"], "symbol": signal.symbol},
        )

    return await _persist_signal(
        signal,
        sb=sb,
        user=user,
        deployment=session,
        environment=ExecutionEnvironment.PAPER.value,
        paper_session_id=resolved_session_id,
    )


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 10.2 - submit_signal(signal) -> OrderLifecycleState
#
#  Spec: trading-lifecycle-integration. design.md -> "Idempotency key scheme"
#  (the ALGORITHM submit_signal block). Requirements 11.2, 11.3, 16.7, 19.1,
#  19.5.
#
#  WHAT THIS SECTION OWNS
#  ----------------------
#  Everything task 10.1 deliberately left: the routing through
#  execute_with_idempotency, the risk and execution engine calls, and EVERY
#  write to order_lifecycle_transitions including the first one
#  (from_state NULL -> to_state GENERATED). 10.1 guaranteed that the row a
#  transition moves FROM exists and says GENERATED; this section is what moves
#  it, and what writes the log.
#
#  THE ORDER IS GATE, THEN WRITE, THEN AUDIT - THE ONE strategy_lifecycle USES
#  --------------------------------------------------------------------------
#  order_lifecycle_state.assert_transition_legal is consulted BEFORE the write
#  (Requirement 16.6), the canonical column is written second, and the
#  append-only transition row is written third. That is exactly the order
#  strategy_lifecycle.apply_version_state already proves for the sibling
#  problem, and it is the order order_lifecycle_state.py's own docstring says
#  this module owns. Consequences worth stating, because they are the point:
#
#    * An illegal transition raises OrderLifecycleRejected and NOTHING is
#      written - not the column, not the log - so Requirement 16.6's "retain
#      the prior value unchanged" holds by never reaching a write.
#    * The audit row is written after the column, so a transition that appears
#      in the log is a transition the signal actually took. The reverse gap (a
#      column write that succeeded and an audit row that did not) is the one
#      Requirement 16.7 explicitly tolerates - it is conditioned on the audit
#      store being "available (i.e. reachable and accepting writes)" - so an
#      unavailable log degrades with a warning and does not fail the
#      submission. The alternative would be refusing to record a real fill
#      because a log table was missing.
#
#  order_lifecycle_transitions IS APPEND-ONLY, AND THIS CODE NEVER FORGETS IT
#  -------------------------------------------------------------------------
#  005b section 2 gives that table owner-scoped SELECT and INSERT policies and
#  NO UPDATE and NO DELETE policy. There is no .update() and no .delete()
#  against it anywhere in this module, by construction rather than by
#  discipline: _insert_transition_row is the only function that touches it and
#  it only inserts.
#
#  THE FIRST ROW IS NULL -> GENERATED AND IT IS NOT GATED, WHICH IS DELIBERATE
#  --------------------------------------------------------------------------
#  ORDER_LIFECYCLE_TRANSITIONS has no edge INTO GENERATED - nothing transitions
#  to it, because it is where a signal begins. So the log's first row cannot be
#  gated by assert_transition_legal: there is no current state to gate from,
#  and calling it with current=None would (correctly) raise
#  ORDER_LIFECYCLE_STATE_UNRECOGNISED. 005b's own schema says the same thing in
#  DDL - from_state is the one nullable state column on that table, commented
#  "NULL for the first transition".
#
#  What replaces the gate for that row is narrower and checkable: a NULL
#  from_state is accepted ONLY with to_state = GENERATED (see
#  _insert_transition_row). So the genesis row cannot be used to smuggle a
#  signal into SUBMITTED or EXECUTED without a from_state.
#
#  WHY SUBMITTED IS WRITTEN AFTER THE EXECUTION CALL, NOT BEFORE
#  ------------------------------------------------------------
#  The tempting order is to write SUBMITTED first so a crash mid-call cannot
#  leave the row behind the exchange. It is the wrong trade here, and the
#  reason is Requirement 19.2:
#
#    * A signal left at PENDING when an order may exist is RECOVERABLE. The
#      recovery sweep queries the exchange by the signal's Idempotency_Key,
#      and if the exchange cannot answer it marks the signal as needing manual
#      reconciliation. Either way it never resubmits.
#    * A signal written to SUBMITTED when NO order exists is NOT recoverable.
#      The sweep finds nothing at the exchange, and Requirement 19.2 forbids
#      resubmitting under a key that was already used - so a decision that was
#      never actually placed is stranded in a state that claims it was.
#
#  A false PENDING costs a lookup. A false SUBMITTED loses a trade and lies in
#  the audit log. So the state advances only on a report from the execution
#  component, and the duplicate-suppression that makes the PENDING window safe
#  is the Redis lock plus uq_signals_idempotency_key, not an optimistic write.
#
#  AN INDETERMINATE EXECUTION FAILURE IS HELD AT PENDING, NOT WRITTEN AS FAILED
#  ---------------------------------------------------------------------------
#  THIS SUPERSEDES this section's original disposition that "neither accepted
#  nor refused" is always FAILED. That reading had exactly the hole the argument
#  above rules out everywhere else, and the crash-recovery suite Requirement
#  19.4 mandates found it: the venue accepts an order, the response is lost
#  (connection reset, read timeout, TLS teardown - the ordinary in-doubt order),
#  _call_execution_engine catches the exception as it should, and the signal is
#  written PENDING -> FAILED. FAILED is one of Requirement 16.1's four terminal
#  states, ORDER_LIFECYCLE_TRANSITIONS[FAILED] is empty, and recover_signal
#  returns RECOVERY_TERMINAL without ever querying the exchange. The record says
#  FAILED, the venue holds a live order, no reconciliation marker is written,
#  and nothing will ever revisit it. That is a false terminal state, which costs
#  strictly more than the false SUBMITTED this section already refuses to write.
#
#  So a FAILURE is now two facts, not one (ExecutionOutcome.failure_kind, and
#  classify_execution_failure which decides it):
#
#    * DETERMINATE - the venue ANSWERED and refused (an invalid order, insufficient
#      funds, a rejected client order id, a report that stated a negative outcome).
#      No order exists under this key, so PENDING -> FAILED is the truth and today's
#      behaviour is unchanged.
#    * INDETERMINATE - we never got an answer (network, timeout, transport, an
#      unreadable report). resolve_execution_state reports PENDING, which is the
#      state the signal is ALREADY in, so apply_order_lifecycle_state finds no route,
#      writes nothing, and appends no transition row. The signal is held exactly
#      where Requirement 19.2's sweep can resolve it, and a loud warning names it.
#
#  The classification defaults to INDETERMINATE for an exception type it does not
#  recognise, because that is the direction this section's own trade points: a
#  false PENDING costs a lookup, a false FAILED loses a trade permanently.
#
#  What is NOT done here: writing a manual-reconciliation marker. That is
#  Requirement 19.2's own clause and it is conditioned on an exchange-state check
#  this function has not performed - see the TASK 12 header. Submission holds the
#  signal; the sweep is what marks it.
#
#  RISK AND EXECUTION ARE REQUIRED ARGUMENTS, NOT OPTIONAL ONES
#  -----------------------------------------------------------
#  Requirement 11.2 says every generated Signal SHALL route through the
#  platform's existing risk validation and order validation/execution
#  components before any order may be submitted, and SHALL NOT introduce a
#  second order-placement path that bypasses them. A defaulted
#  ``risk_engine=None`` that approved when absent would BE that second path.
#  So both are keyword-only arguments with no default: a caller cannot forget
#  them, and passing None explicitly is refused (SIGNAL_RISK_ENGINE_MISSING /
#  SIGNAL_EXECUTION_ENGINE_MISSING) rather than treated as "no checks
#  configured".
#
#  They are duck-typed rather than typed against a class, for the same reason
#  _pick is: the real risk validator on this path is
#  dag_risk_integration.RiskIntegratedExecutionPipeline (whose check_risk reads
#  only signal.symbol, so this module's Signal fits it unchanged), the real
#  executor is core.execution_engine.ExecutionEngine or a live exchange
#  executor wrapping it, and neither is a module-level singleton - both are
#  per-deployment objects the Live_Runtime constructs (task 10.3). Naming a
#  concrete class here would force this module to import pandas and FastAPI
#  through dag_risk_integration.
#
#  THE 005b CONTRACT ON uq_signals_idempotency_key IS HONOURED HERE
#  ---------------------------------------------------------------
#  005b section 1's header states it in capitals: "THE CALLER OF THAT INSERT
#  MUST TRANSLATE 23505 ON uq_signals_idempotency_key INTO DuplicateOrderError
#  AND RETURN THE SIGNAL'S CURRENT Order_Lifecycle_State". Two halves, both
#  here:
#    * the translation is is_duplicate_idempotency_key_error, applied in
#      _insert_signal_row (task 10.1's INSERT, which is the insert the
#      migration is talking about);
#    * the "return the current state" half is submit_signal's
#      DuplicateOrderError branch, which reads the persisted state back rather
#      than reporting a failure.
#
#  WHAT THIS SECTION STILL DOES NOT OWN
#  ------------------------------------
#  * Which ACTION-node evaluation calls this, stale-feed suspension, and
#    per-event error containment - task 10.3 (Requirements 14.3, 14.5-14.8).
#  * WHAT a GENERATED / STATUS_CHANGED / SNAPSHOT frame on
#    signal.{deployment_id} looks like, and which sequence number it carries -
#    task 14.2, which lives in its own section below and in
#    ``ws_channels.signal_frame``. This section's contribution is that
#    ``apply_order_lifecycle_state`` is the ONE write path, so 14.2's publish
#    hangs off step 3 of it and "every transition is announced" needs no list of
#    call sites. Nothing here decides a frame's shape, its seq or its dedup key.
#  * The crash-recovery sweep itself (query the exchange by Idempotency_Key on
#    restart, bounded to 3 attempts in 30s) - Requirement 19.2, task 12.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


from backend_app.backend.order_lifecycle_state import (  # noqa: E402
    ORDER_LIFECYCLE_TRANSITIONS,
    OrderLifecycleRejected,
    UnknownSourceStatus,
    assert_transition_legal,
    map_order_state,
    normalise_lifecycle_state,
    normalise_source_value,
)
from backend_app.core.distributed_idempotency import (  # noqa: E402
    DuplicateOrderError,
    get_idempotency_layer,
)

#: 005b section 2's append-only transition log. Requirement 16.7.
ORDER_LIFECYCLE_TRANSITIONS_TABLE = "order_lifecycle_transitions"

#: PostgreSQL's undefined_table and PostgREST's schema-cache equivalent. Unlike
#: public.signals - whose absence is a real problem, not something to degrade around -
#: order_lifecycle_transitions is created by 005b section 2 and Requirement 16.7 is
#: explicitly conditioned on the audit store being available, so its absence degrades.
_MISSING_TRANSITIONS_TABLE_CODES = ("42p01", "undefined_table", "pgrst205")

#: Same recheck window as the 005b column probe, for the same reason: an operator who
#: applies the migration to a running fleet should not need a redeploy.
_transitions_table_supported: Optional[bool] = None
_transitions_table_checked_at: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
# REFUSALS ADDED BY THIS SECTION
# ══════════════════════════════════════════════════════════════════════════


class SignalSubmissionRefused(SignalRejected):
    """Submission cannot proceed, and not because a control said no.

    A missing risk engine, a missing execution engine, an engine exposing no seam this
    module knows how to call. Distinct from a risk REJECTION (which is a verdict, and
    becomes ``REJECTED`` on the signal) and from a submission FAILURE (which is an
    outcome, and becomes ``FAILED``): this is a wiring problem, so the signal is left
    where it was and the caller is told what is missing.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Mapping[str, Any]] = None,
        *,
        http_status: int = 500,
    ):
        super().__init__(code, message, details, http_status=http_status)


# ══════════════════════════════════════════════════════════════════════════
# THE OUTCOME SHAPES - one place each verdict is read from
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RiskVerdict:
    """What the risk validation component said about this signal, at submission time.

    Requirement 11.2 asks for the validation to run before any order may be submitted;
    ``generate_signal`` already refused a candidate whose risk verdict was negative AT
    GENERATION (Requirement 14.3), and this is the re-check under the submission lock -
    the drawdown, the daily loss and the account equity can all have moved between the
    two, and the kill switch can have been thrown.
    """

    approved: bool
    reason: Optional[str] = None
    #: A size the risk component reduced the request to (``RiskDecision.REDUCED_SIZE``).
    #: ``None`` when it did not change the size.
    adjusted_quantity: Optional[float] = None
    detail: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.detail is None:
            object.__setattr__(self, "detail", {})


#: A failure the venue ANSWERED. The request reached it, it replied, and the reply was a
#: refusal or an error about the order itself - so no order exists under this key and a
#: terminal state is the truth. Requirement 11.3's ``REJECTED``/``FAILED``.
EXECUTION_FAILURE_DETERMINATE = "DETERMINATE"

#: A failure with NO ANSWER: a connection reset, a read timeout, a TLS teardown, a
#: transport error, an unreadable report. The order MAY be live at the venue and this
#: process cannot tell. Never terminal - see this section's header on why an
#: indeterminate failure is held at ``PENDING``.
EXECUTION_FAILURE_INDETERMINATE = "INDETERMINATE"

#: Exception type names (matched against the whole MRO, case- and underscore-insensitively)
#: that mean WE NEVER GOT AN ANSWER. Checked FIRST, because a name can match both lists
#: and "no answer" is the reading that keeps a live order recoverable.
_INDETERMINATE_FAILURE_MARKERS: Tuple[str, ...] = (
    "network", "timeout", "timedout", "connection", "socket", "unreachable",
    "unavailable", "notavailable", "maintenance", "ssl", "tls", "eof", "brokenpipe",
    "oserror", "ioerror", "cancellederror", "cancelederror", "ddos", "ratelimit",
    "toomanyrequests", "temporar", "transport", "disconnect", "lost", "dropped",
    "incomplete", "partialread", "gateway", "badgateway", "serviceunavailable",
)

#: Exception type names that mean THE VENUE ANSWERED and refused. Only reached when no
#: indeterminate marker matched.
_DETERMINATE_FAILURE_MARKERS: Tuple[str, ...] = (
    "invalidorder", "invalidaddress", "invalidnonce", "insufficientfunds",
    "insufficientbalance", "badrequest", "badsymbol", "badresponse",
    "authentication", "permissiondenied", "accountsuspended", "accountnotenabled",
    "notsupported", "ordernotfound", "ordernotfillable", "orderimmediatelyfillable",
    "duplicate", "rejected", "refused", "denied", "forbidden", "unauthorized",
    "validation", "marginmode", "argumentserror", "notpermitted",
)


def classify_execution_failure(exc: BaseException) -> str:
    """Whether ``exc`` says the venue REFUSED the order or says nothing about it at all.

    The distinction Requirement 19.2 rests on, and the reason it lives here rather than
    at the call site: an in-doubt order (the venue accepted, the response was lost) must
    never be recorded as terminally ``FAILED``, because ``FAILED`` is one of Requirement
    16.1's four terminal states and the crash-recovery sweep will not query the exchange
    for a signal that has reached one. See the TASK 10.2 header, "AN INDETERMINATE
    EXECUTION FAILURE IS HELD AT PENDING".

    HOW IT DECIDES, AND WHY IT DEFAULTS THE WAY IT DOES
        1. An explicit ``order_may_be_live`` attribute on the exception wins. A live
           exchange wrapper that knows whether its request was delivered is more
           authoritative than any inference drawn here, so it is given a way to say so.
        2. Otherwise the exception's own class names - every class in its MRO, so a
           subclass of ``ccxt.NetworkError`` is classified by that base without this
           module importing ccxt - are matched against
           :data:`_INDETERMINATE_FAILURE_MARKERS` and then
           :data:`_DETERMINATE_FAILURE_MARKERS`.
        3. Anything unrecognised is INDETERMINATE.

        Step 3 is the important one and it is asymmetric on purpose. Misreading a refusal
        as indeterminate costs one exchange lookup by the Requirement 19.2 sweep, which
        then finds nothing and reports ``resubmission_allowed``. Misreading an in-doubt
        order as a refusal writes a terminal ``FAILED`` over a live order and no automated
        path ever revisits it. That is the same trade task 10.2's header already makes
        between a false ``PENDING`` and a false ``SUBMITTED``, applied to the one case it
        did not cover.

        The exception's MESSAGE is deliberately not consulted. A type is a contract a
        client offers; a message is prose that changes between releases, and matching on it
        would make this classification depend on wording nobody controls.
    """
    explicit = getattr(exc, "order_may_be_live", None)
    if explicit is not None:
        return (
            EXECUTION_FAILURE_INDETERMINATE
            if bool(explicit)
            else EXECUTION_FAILURE_DETERMINATE
        )

    names = [
        cls.__name__.lower().replace("_", "")
        for cls in type(exc).__mro__
        if cls not in (object, BaseException, Exception)
    ]
    if any(marker in name for name in names for marker in _INDETERMINATE_FAILURE_MARKERS):
        return EXECUTION_FAILURE_INDETERMINATE
    if any(marker in name for name in names for marker in _DETERMINATE_FAILURE_MARKERS):
        return EXECUTION_FAILURE_DETERMINATE
    return EXECUTION_FAILURE_INDETERMINATE


@dataclass(frozen=True)
class ExecutionOutcome:
    """What the order validation/execution component reported.

    Three states, deliberately not two, because Requirement 11.3 distinguishes them:

    * ``refused`` - the execution/guard component REJECTED the signal. No order was
      placed, and the signal becomes ``REJECTED``.
    * ``accepted`` - the order reached the exchange (or its sandbox). The signal becomes
      ``SUBMITTED``, and then ``PARTIALLY_EXECUTED``/``EXECUTED`` if a fill was reported.
    * neither - the submission FAILED. Which is where ``failure_kind`` comes in.

    A report that is both ``accepted`` and ``refused`` is not representable; a report
    that is neither is a failure, not an implicit success.

    ``failure_kind`` SPLITS THAT THIRD CASE IN TWO, AND THE SPLIT IS NOT COSMETIC
        A failure the venue ANSWERED (:data:`EXECUTION_FAILURE_DETERMINATE`) means no
        order exists, so the terminal ``FAILED`` this module has always written is the
        truth. A failure with NO ANSWER (:data:`EXECUTION_FAILURE_INDETERMINATE` - a
        connection reset after the request was sent, a read timeout, an unreadable report)
        means an order may be live at the venue, and writing a terminal state over it
        strands it permanently: the Requirement 19.2 sweep does not query the exchange for
        a signal that has reached a terminal state.

        ``None`` reads as INDETERMINATE (see :attr:`indeterminate`), so a caller that
        constructs a bare failure without thinking about which it is gets the safe one.
        Being terminal is what has to be argued for, not what happens by default.
    """

    accepted: bool
    refused: bool = False
    reason: Optional[str] = None
    order_id: Optional[str] = None
    execution_id: Optional[str] = None
    #: The execution component's own order-state word, where it reports one. Mapped
    #: through ``ORDER_STATE_MAP`` when it belongs to that vocabulary - never guessed.
    order_state: Optional[str] = None
    filled: Optional[float] = None
    quantity: Optional[float] = None
    #: For a FAILURE only (neither ``accepted`` nor ``refused``): one of
    #: :data:`EXECUTION_FAILURE_DETERMINATE` / :data:`EXECUTION_FAILURE_INDETERMINATE`.
    #: ``None`` means "not stated", which reads as INDETERMINATE.
    failure_kind: Optional[str] = None
    detail: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.detail is None:
            object.__setattr__(self, "detail", {})

    @property
    def indeterminate(self) -> bool:
        """Whether an order may be live at the venue despite this outcome not being accepted.

        ``False`` for an accepted order (it IS live, which is not in doubt) and for a
        refusal (the venue answered). ``True`` for a failure that did not explicitly claim
        to be determinate - including one that stated nothing at all.
        """
        if self.accepted or self.refused:
            return False
        return self.failure_kind != EXECUTION_FAILURE_DETERMINATE


# ══════════════════════════════════════════════════════════════════════════
# THE TRANSITION PATH - derived from the table, never transcribed
# ══════════════════════════════════════════════════════════════════════════


def lifecycle_path(current: Any, target: Any) -> Tuple[OrderLifecycleState, ...]:
    """The states to move through to get from ``current`` to ``target``, target last.

    A breadth-first walk of ``ORDER_LIFECYCLE_TRANSITIONS`` itself, so the path is a
    consequence of Requirement 16.4's table rather than a second transcription of it that
    could drift. Every element of the returned tuple is a legal single step from its
    predecessor, which is why each one still goes through ``assert_transition_legal``
    individually - this function decides the route, the gate decides legality.

    Why a path at all: the execution component reports a FILL, and ``PENDING -> EXECUTED``
    is not an edge. The signal really did pass through ``SUBMITTED`` on its way there, and
    Requirement 16.7's history is supposed to show that it did, so the intermediate state
    is recorded rather than skipped.

    Returns
        ``()`` when ``current`` already is ``target`` and no self-edge exists (nothing to
        write - not an error), ``(target,)`` when ``current == target`` and the table has
        a self-edge (``PARTIALLY_EXECUTED``, which Requirement 16.4 asks for explicitly so
        repeated partial fills each record a transition), and ``()`` when ``target`` is
        unreachable - which the caller must treat as a refusal, not as a no-op.
    """
    start = normalise_lifecycle_state(current)
    goal = normalise_lifecycle_state(target)
    if start is None or goal is None:
        return ()
    if start == goal:
        return (goal,) if goal in ORDER_LIFECYCLE_TRANSITIONS.get(start, ()) else ()

    #: BFS over the table. Deterministic: the transition tuples are ordered, so the same
    #: (current, target) pair always yields the same route.
    frontier: List[Tuple[OrderLifecycleState, ...]] = [(start,)]
    seen = {start}
    while frontier:
        next_frontier: List[Tuple[OrderLifecycleState, ...]] = []
        for path in frontier:
            for nxt in ORDER_LIFECYCLE_TRANSITIONS.get(path[-1], ()):
                if nxt in seen:
                    continue
                if nxt == goal:
                    return path[1:] + (goal,)
                seen.add(nxt)
                next_frontier.append(path + (nxt,))
        frontier = next_frontier
    return ()


# ══════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════


def is_duplicate_idempotency_key_error(exc: BaseException) -> bool:
    """True only when ``exc`` is a unique violation on the signal's Idempotency_Key.

    005b section 1's contract, and narrow on purpose. It requires BOTH a unique-violation
    signal (``23505`` or PostgreSQL's own wording) AND the offending object to name the
    idempotency key. A ``23505`` on ``signals_pkey`` or on any other unique index is a
    different fact and must not be reported as "an order for this signal already exists" -
    that would turn an unrelated collision into a silent no-op on a real trading decision.
    """
    text = str(exc).lower()
    if not text:
        return False
    unique_violation = (
        "23505" in text
        or "duplicate key value violates unique" in text
        or "unique constraint" in text
        or ("already exists" in text and "key" in text)
    )
    if not unique_violation:
        return False
    return "uq_signals_idempotency_key" in text or "idempotency_key" in text


def is_missing_transitions_table_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says ``order_lifecycle_transitions`` is absent.

    Narrow in the same way ``is_missing_signal_lifecycle_column_error`` is: anything this
    returns ``False`` for is a real audit-write failure and is reported as one, because a
    transition log that silently stops recording is worse than one that complains.
    """
    text = str(exc).lower()
    if not text:
        return False
    if ORDER_LIFECYCLE_TRANSITIONS_TABLE not in text and "relation" not in text:
        return False
    if any(code in text for code in _MISSING_TRANSITIONS_TABLE_CODES):
        return True
    return ORDER_LIFECYCLE_TRANSITIONS_TABLE in text and any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown table")
    )


def reset_transitions_table_support() -> None:
    """Forget the cached verdict about the transition log. For tests, and for an operator."""
    global _transitions_table_supported, _transitions_table_checked_at
    _transitions_table_supported = None
    _transitions_table_checked_at = 0.0


def transitions_table_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False``, or ``None`` for "not yet determined"."""
    return _transitions_table_supported


def remember_transitions_table_absent() -> None:
    """Record that 005b section 2 is not applied, so the next audit skips without retrying."""
    global _transitions_table_supported, _transitions_table_checked_at
    _transitions_table_supported = False
    _transitions_table_checked_at = time.monotonic()


def _transitions_table_available() -> bool:
    """Whether to attempt an audit write. Optimistic when the verdict has aged out."""
    if _transitions_table_supported is None:
        return True
    if _transitions_table_supported:
        return True
    if (
        time.monotonic() - _transitions_table_checked_at
        >= SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS
    ):
        return True
    return False


def warn_transitions_table_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says which guarantee is not in force."""
    logger.warning(
        "public.%s does not exist, so the Order_Lifecycle_State transition history of "
        "Requirement 16.7 is NOT being recorded. Apply %s (section 2), then restart or "
        "wait %.0fs for the re-probe. The submission itself is unaffected: Requirement "
        "16.7 is conditioned on the audit store being reachable, and refusing to record a "
        "real fill because a log table is missing would be strictly worse. Detail: %s",
        ORDER_LIFECYCLE_TRANSITIONS_TABLE,
        SIGNAL_LIFECYCLE_MIGRATION,
        SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS,
        detail,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE WRITES
# ══════════════════════════════════════════════════════════════════════════


async def _update_signal_row(sb: Any, signal: Signal, payload: Dict[str, Any]) -> None:
    """One owner-scoped UPDATE on ``public.signals``. Raises on a real failure.

    Scoped by ``user_id`` as well as ``id`` even though RLS already scopes it: the
    explicit filter is the application-layer half Requirement 20.1 asks for alongside the
    row-level policies, and it is what makes this write safe if it is ever executed
    through a service-role client that RLS does not constrain.

    A RAISED failure IS CLASSIFIED, exactly as ``result.error`` is
        A PostgREST client reports a refused write two ways - a response carrying an error,
        and a raised exception (a dropped connection, a restarting server, a pooler that
        answers by hanging up). Only the first used to be classified here, so the second
        escaped as whatever the driver raised. That is not cosmetic: the plural
        crash-recovery sweep contains classified refusals per signal, and an unclassified
        driver exception used to abort the whole restart on the first signal that met one.
        Both shapes now become :class:`SignalPersistenceError` carrying the driver's own
        text, so a caller can report the real cause and the sweep can contain it.

    Raises
        :class:`SignalPersistenceError` (code ``SIGNAL_STATE_NOT_PERSISTED``) on any write
        failure, raised or reported. The original exception is chained as ``__cause__``.
    """
    try:
        result = await _execute(
            sb.table("signals")
            .update(payload)
            .eq("id", signal.id)
            .eq("user_id", signal.user_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed
        raise SignalPersistenceError(
            f"Could not update signal {signal.id} with {sorted(payload)}: {exc}",
            {
                "signal_id": signal.id,
                "fields": sorted(payload),
                "error_type": type(exc).__name__,
            },
            code="SIGNAL_STATE_NOT_PERSISTED",
        ) from exc

    error = getattr(result, "error", None)
    if error is not None:
        raise SignalPersistenceError(
            f"Could not update signal {signal.id} with {sorted(payload)}: {error}",
            {"signal_id": signal.id, "fields": sorted(payload)},
            code="SIGNAL_STATE_NOT_PERSISTED",
        )


async def _insert_transition_row(
    sb: Any,
    signal: Signal,
    *,
    from_state: Optional[OrderLifecycleState],
    to_state: OrderLifecycleState,
    reason: Optional[str],
    occurred_at: str,
) -> bool:
    """Append one row to ``order_lifecycle_transitions``. Requirement 16.7.

    INSERT only. This is the single function in this module that names that table, and it
    contains no ``update`` and no ``delete``, because 005b section 2 grants the table
    owner-scoped SELECT and INSERT policies and nothing else - a transition log is
    append-only, matching the convention ``signal_events`` already establishes.

    A NULL ``from_state`` is accepted ONLY for ``to_state = GENERATED``. That is the
    narrow substitute for the transition gate on the log's genesis row (see this section's
    header): nothing transitions INTO ``GENERATED``, so ``assert_transition_legal`` cannot
    gate that row, and without this check a NULL ``from_state`` would be a way to write
    ``SUBMITTED`` with no predecessor.

    Returns
        ``True`` when a row was written, ``False`` when the table is absent and the write
        was skipped with a warning naming the migration.

    Raises
        :class:`SignalRejected` when a NULL ``from_state`` is paired with anything but
        ``GENERATED``, and :class:`SignalPersistenceError` on a real write failure.
    """
    global _transitions_table_supported, _transitions_table_checked_at

    if from_state is None and to_state is not OrderLifecycleState.GENERATED:
        raise SignalRejected(
            "ORDER_LIFECYCLE_TRANSITION_UNANCHORED",
            f"Refusing to record a transition to {to_state.value} with no from_state. "
            f"A NULL from_state is the transition log's genesis row and is only valid for "
            f"GENERATED, the one state nothing transitions into; every other row must "
            f"name the state it came from so the history reads as a chain.",
            {"signal_id": signal.id, "requested_state": to_state.value},
            http_status=500,
        )

    if not _transitions_table_available():
        return False

    payload = {
        "signal_id": signal.id,
        "user_id": signal.user_id,
        "from_state": from_state.value if from_state is not None else None,
        "to_state": to_state.value,
        "reason": reason,
        # Written explicitly rather than left to the column's NOW() default so the log
        # orders by the instant the gate passed in this process, not by the instant the
        # INSERT happened to reach the server - which a retry can reorder. Requirement
        # 16.7's "retrievable in chronological order by timestamp" is about when the
        # transition happened.
        "occurred_at": occurred_at,
    }

    try:
        result = await _execute(
            sb.table(ORDER_LIFECYCLE_TRANSITIONS_TABLE).insert(payload).execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if is_missing_transitions_table_error(exc):
            remember_transitions_table_absent()
            warn_transitions_table_absent(str(exc))
            return False
        raise SignalPersistenceError(
            f"Could not record the {_describe_transition(from_state, to_state)} "
            f"transition for signal {signal.id}: {exc}",
            {
                "signal_id": signal.id,
                "from_state": from_state.value if from_state else None,
                "to_state": to_state.value,
            },
            code="ORDER_LIFECYCLE_TRANSITION_NOT_AUDITED",
        ) from exc

    error = getattr(result, "error", None)
    if error is not None:
        if is_missing_transitions_table_error(Exception(str(error))):
            remember_transitions_table_absent()
            warn_transitions_table_absent(str(error))
            return False
        raise SignalPersistenceError(
            f"Could not record the {_describe_transition(from_state, to_state)} "
            f"transition for signal {signal.id}: {error}",
            {
                "signal_id": signal.id,
                "from_state": from_state.value if from_state else None,
                "to_state": to_state.value,
            },
            code="ORDER_LIFECYCLE_TRANSITION_NOT_AUDITED",
        )

    _transitions_table_supported = True
    _transitions_table_checked_at = time.monotonic()
    return True


def _describe_transition(
    from_state: Optional[OrderLifecycleState], to_state: OrderLifecycleState
) -> str:
    return f"{from_state.value if from_state else 'NULL'} -> {to_state.value}"


async def anchor_generated_transition(
    sb: Any, signal: Signal, *, reason: Optional[str] = None
) -> bool:
    """Write the log's genesis row for ``signal``: ``NULL -> GENERATED``.

    Task 10.1 persisted the signal row at ``GENERATED`` and deliberately wrote nothing to
    the transition log, because the log is a write and this section owns every write to it
    - including the first. This is that first row, and without it Requirement 16.7's
    history for a signal would start at its SECOND state and never say when it was
    generated.

    Idempotent, so a retried submission under the same Idempotency_Key does not append a
    second genesis row: the log is append-only, so a duplicate cannot be cleaned up
    afterwards and must be avoided before the insert. Checked with an owner-scoped SELECT
    for an existing row with a NULL ``from_state``.

    Returns
        ``True`` when a row was written, ``False`` when one already existed or the table
        is absent.
    """
    if not _transitions_table_available():
        return False

    try:
        existing = await _execute(
            sb.table(ORDER_LIFECYCLE_TRANSITIONS_TABLE)
            .select("id")
            .eq("signal_id", signal.id)
            .is_("from_state", None)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if is_missing_transitions_table_error(exc):
            remember_transitions_table_absent()
            warn_transitions_table_absent(str(exc))
            return False
        # An inconclusive read is not a reason to skip the genesis row, but it IS a reason
        # not to claim the row is absent: attempt the insert and let a real failure
        # surface there, where it is classified.
        logger.warning(
            "Could not check for signal %s's genesis transition row (%s); attempting the "
            "insert.",
            signal.id,
            exc,
        )
    else:
        if getattr(existing, "data", None):
            return False

    return await _insert_transition_row(
        sb,
        signal,
        from_state=None,
        to_state=OrderLifecycleState.GENERATED,
        reason=reason or "signal generated",
        occurred_at=signal.generated_at,
    )


async def apply_order_lifecycle_state(
    sb: Any,
    signal: Signal,
    to_state: Any,
    *,
    reason: Optional[str] = None,
    order_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    through_intermediate_states: bool = False,
) -> Signal:
    """Move ``signal`` to ``to_state``: gate, then write, then audit.

    The one write path for a signal's canonical state, and the reason this module can
    claim Requirement 16.6 holds. For each hop on the route from the signal's current
    state to ``to_state`` (see :func:`lifecycle_path`):

      1. ``assert_transition_legal`` - BEFORE the write. An illegal hop raises
         ``OrderLifecycleRejected`` carrying the rejected transition and the legal
         alternatives, and nothing at all is written.
      2. ``UPDATE public.signals SET order_lifecycle_state = ...``, owner-scoped.
      3. ``INSERT INTO order_lifecycle_transitions`` - the append-only history.

    ``order_id`` / ``execution_id`` are written in the same UPDATE as the state they
    arrived with, so a reader never sees ``EXECUTED`` without the execution reference that
    justifies it. They are only ever written when present; a NULL would be
    indistinguishable from a reference that was lost.

    ``through_intermediate_states`` IS OFF BY DEFAULT, AND THAT MATTERS
        By default this is a SINGLE-HOP move: ``to_state`` must be one transition away, and
        anything else is Requirement 16.6's rejected write. Opting in lets the move walk
        the intermediate states :func:`lifecycle_path` finds, and it exists for exactly one
        real situation - the execution component reports, in ONE call, that the order was
        accepted AND that it filled. Both of those things happened, ``PENDING ->
        EXECUTED`` is not an edge, and the history should say the signal passed through
        ``SUBMITTED`` because it did.

        It is off by default because the same mechanism, used without that justification,
        writes a chain of transitions that never occurred: ``GENERATED -> EXECUTED`` would
        silently record a ``PENDING`` (risk validation ran) and a ``SUBMITTED`` (an order
        reached the exchange) on the word of a caller that knows neither. A transition log
        that can be made to claim that is worth less than no log.

    Returns
        A NEW :class:`Signal` at the reached state (the record is frozen). The returned
        record follows what was persisted, so a caller that keeps it is holding the same
        state the database holds.

    Raises
        :class:`OrderLifecycleRejected` for an illegal or unreachable transition,
        :class:`SignalPersistenceError` when the state could not be persisted.
    """
    target = normalise_lifecycle_state(to_state)
    if target is None:
        raise OrderLifecycleRejected(
            "ORDER_LIFECYCLE_STATE_UNRECOGNISED",
            f"Cannot move signal {signal.id} to {to_state!r}: that is not one of the "
            f"canonical Order_Lifecycle_State values.",
            {
                "signal_id": signal.id,
                "order_lifecycle_state": signal.order_lifecycle_state.value,
                "requested_state": None if to_state is None else str(to_state),
            },
        )

    route = lifecycle_path(signal.order_lifecycle_state, target)
    if len(route) > 1 and not through_intermediate_states:
        # A multi-hop route exists but the caller did not claim the intermediate states
        # happened. Refusing is the honest answer, and the gate is what says so - with the
        # single-step alternatives named, which is what tells the caller what it actually
        # may write next.
        assert_transition_legal(
            signal.order_lifecycle_state, target, signal_id=signal.id, reason=reason
        )
    if not route:
        if signal.order_lifecycle_state is target:
            # Already there and no self-edge exists. Not an error, and deliberately not a
            # write: re-writing the same state would append a transition row claiming a
            # transition that did not happen.
            return signal
        # assert_transition_legal is what reports it, so the caller gets the standard
        # refusal shape with the legal alternatives named.
        assert_transition_legal(
            signal.order_lifecycle_state, target, signal_id=signal.id, reason=reason
        )
        raise OrderLifecycleRejected(  # pragma: no cover - the gate above always raises
            "ORDER_LIFECYCLE_TRANSITION_INVALID",
            f"No legal route from {signal.order_lifecycle_state.value} to {target.value}.",
            {
                "signal_id": signal.id,
                "order_lifecycle_state": signal.order_lifecycle_state.value,
                "requested_state": target.value,
            },
        )

    current = signal
    with_lifecycle_column = await signal_lifecycle_columns_supported(sb)

    for index, hop in enumerate(route):
        # 1. GATE (Requirement 16.6) - before any write, so a refusal leaves the prior
        #    value untouched by never reaching step 2.
        from_state, hop_state = assert_transition_legal(
            current.order_lifecycle_state, hop, signal_id=current.id, reason=reason
        )

        final_hop = index == len(route) - 1
        hop_reason = reason if final_hop else f"en route to {route[-1].value}"

        # 2. WRITE the canonical column (plus the references that arrived with it).
        payload: Dict[str, Any] = {}
        if with_lifecycle_column:
            payload["order_lifecycle_state"] = hop_state.value
        else:
            # 005b is not applied, so there is no canonical column to write. The
            # transition still happened and is still audited (where the log exists) and
            # the order reference is still recorded; what is lost is the readable
            # canonical state on the row, which is exactly what the warning names.
            warn_signal_lifecycle_columns_absent(
                f"cannot persist order_lifecycle_state={hop_state.value} for signal "
                f"{current.id}"
            )
        if final_hop:
            if order_id is not None:
                payload["order_id"] = order_id
            if execution_id is not None:
                payload["trade_id"] = execution_id
        if payload:
            await _update_signal_row(sb, current, payload)

        current = current.with_order_lifecycle_state(hop_state)
        if final_hop and (order_id is not None or execution_id is not None):
            current = current.with_order_reference(
                order_id=order_id, execution_id=execution_id
            )

        # 3. AUDIT (Requirement 16.7), after the write, so a row in the log is a
        #    transition the signal actually took.
        await _insert_transition_row(
            sb,
            current,
            from_state=from_state,
            to_state=hop_state,
            reason=hop_reason,
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            "Signal %s: %s (%s)",
            current.id,
            _describe_transition(from_state, hop_state),
            hop_reason or "no reason given",
        )

        # 4. ANNOUNCE (task 14.2, Requirements 18.1/18.4/23.6) - AFTER the audit, so a
        #    frame never describes a transition whose history row failed to write, and
        #    per HOP, so a multi-hop route announces the two transitions it wrote rather
        #    than one summary of both. Contained: a publish failure cannot fail a
        #    transition that is already persisted and audited (Requirement 14.5 -
        #    "persist before reporting", and this is the reporting).
        await publish_signal_status_changed(
            current, previous_state=from_state, reason=hop_reason
        )

    return current


# ══════════════════════════════════════════════════════════════════════════
# READING THE PERSISTED STATE BACK
# ══════════════════════════════════════════════════════════════════════════


async def load_current_order_lifecycle_state(
    sb: Any, signal: Signal, *, strict: bool = False
) -> Optional[OrderLifecycleState]:
    """The signal's Order_Lifecycle_State as the DATABASE currently has it.

    Read, not assumed, because both callers need the authoritative value rather than this
    process's belief:

    * ``DuplicateOrderError`` means another attempt owns this key - possibly in another
      pod, possibly already finished - so this process's in-memory record is exactly the
      thing that may be stale.
    * The idempotency-store-unavailable path must not overwrite a state that has already
      moved past ``PENDING`` (Requirement 19.3 forbids overwriting with an earlier state),
      and only the row can say whether it has.

    Reconciles through ``order_lifecycle_state`` when 005b's column is populated, and
    otherwise through Requirement 16.2's mapping from the legacy ``status`` /
    ``order_status`` columns - which is the whole point of that reconciliation existing.

    ``strict`` - WHO MAY FALL BACK TO PROCESS MEMORY, AND WHO MAY NOT
        By DEFAULT (``strict=False``) an unreadable row falls back to the in-memory record,
        which is right for the two callers this function was written for. Both are inside
        ``submit_signal``, both hold a ``Signal`` this process itself just persisted, and
        for both the fallback only ever makes the answer MORE current than ``GENERATED``:
        the ``DuplicateOrderError`` branch reports a state rather than a failure, and the
        Requirement 19.5 withhold uses it to refuse to write ``PENDING`` over something
        later. Neither derives a route or a verdict from it.

        ``strict=True`` returns ``None`` instead - for an exception, for a refused response
        carrying ``result.error``, for no row, and for legacy columns that will not
        reconcile. It exists for the crash-recovery sweep, which must not report on a
        signal whose record it could not read (see :func:`recover_signal`): a restarted
        process's in-memory value is the ``GENERATED`` the object was minted with, and
        walking a route from it is precisely the "re-deriving it from an earlier,
        superseded state" Requirement 19.3 forbids.

    Returns
        The persisted state, or ``None`` only when ``strict`` and it could not be read.
    """
    columns = "status,order_status"
    if await signal_lifecycle_columns_supported(sb):
        columns = "order_lifecycle_state," + columns

    def unreadable(detail: str) -> Optional[OrderLifecycleState]:
        if strict:
            logger.warning(
                "Could not read signal %s's persisted Order_Lifecycle_State (%s); "
                "reporting NO state, because this caller may not substitute this "
                "process's own memory for the record (Requirement 19.3).",
                signal.id,
                detail,
            )
            return None
        logger.warning(
            "Could not read signal %s's persisted Order_Lifecycle_State (%s); reporting "
            "the state this process last persisted (%s).",
            signal.id,
            detail,
            signal.order_lifecycle_state.value,
        )
        return signal.order_lifecycle_state

    try:
        result = await _execute(
            sb.table("signals")
            .select(columns)
            .eq("id", signal.id)
            .eq("user_id", signal.user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        return unreadable(str(exc))

    # A refused SELECT answers WITH an error rather than by raising - the shape a pooler in
    # front of a restarting server produces. Ignoring it read as "no row", which silently
    # became the fallback; it is a failed read and is reported as one.
    error = getattr(result, "error", None)
    if error is not None:
        return unreadable(str(error))

    data = getattr(result, "data", None)
    row = (data[0] if isinstance(data, list) else data) if data else None
    if not row:
        return unreadable("the owner-scoped SELECT matched no row")

    canonical = normalise_lifecycle_state(_pick(row, "order_lifecycle_state"))
    if canonical is not None:
        return canonical

    return _reconcile_legacy_columns(row, signal, strict=strict)


def _reconcilable_order_state(row: Mapping[str, Any]) -> Optional[str]:
    """``row``'s ``order_status`` as an ``OrderState`` reading, or ``None``.

    ``signals.order_status`` is a free-text column that ``SignalService.update_order``
    writes exchange words into ("FILLED", "PARTIALLY_FILLED"), which are NOT the
    ``OrderState`` vocabulary ``ORDER_STATE_MAP`` covers - so it is offered to the resolver
    only when it actually normalises into that vocabulary. Anything else is dropped rather
    than guessed at, which is the same disposition ``UnknownSourceStatus`` takes: a state
    about real money that no source claimed must not be invented here.

    Stated once, here, because two readers need it: :func:`_reconcile_legacy_columns`
    (which may fall back to process memory) and :func:`reconcile_row_lifecycle_state`
    (which may not, because the signal-trace router holds no in-memory record to fall
    back to).
    """
    raw_order_status = normalise_source_value(_pick(row, "order_status"))
    if raw_order_status is None:
        return None
    try:
        return raw_order_status if map_order_state(raw_order_status) is not None else None
    except UnknownSourceStatus:
        return None


def reconcile_row_lifecycle_state(
    row: Mapping[str, Any],
) -> Optional[OrderLifecycleState]:
    """One persisted ``public.signals`` row's Order_Lifecycle_State. Pure, no I/O.

    Requirement 16.2's reconciliation for a reader that has ONLY the row: the canonical
    ``order_lifecycle_state`` column when 005b is applied and populated, and otherwise
    ``SIGNALS_STATUS_MAP``/``ORDER_STATE_MAP`` over the legacy ``status`` / ``order_status``
    columns, at the priority ``resolve_order_lifecycle_state`` fixes
    (``OrderState`` > ``signals.status``).

    ``None`` - never a guess - when the legacy columns carry a value outside every source
    vocabulary. The signal-trace router renders that as an unknown state rather than
    inventing one; :func:`_reconcile_legacy_columns` is the variant for callers that hold
    an in-memory record they are entitled to fall back to.
    """
    canonical = normalise_lifecycle_state(_pick(row, "order_lifecycle_state"))
    if canonical is not None:
        return canonical
    try:
        return resolve_order_lifecycle_state(
            order_state=_reconcilable_order_state(row),
            signals_status=_pick(row, "status"),
        )
    except UnknownSourceStatus as exc:
        logger.warning(
            "Signal %s's legacy status columns could not be reconciled onto an "
            "Order_Lifecycle_State (%s); reporting no state rather than inventing one.",
            _pick(row, "id"),
            exc,
        )
        return None


def _reconcile_legacy_columns(
    row: Mapping[str, Any], signal: Signal, *, strict: bool = False
) -> Optional[OrderLifecycleState]:
    """Requirement 16.2's reconciliation, applied to a row whose canonical column is NULL.

    The mapping itself is :func:`reconcile_row_lifecycle_state`'s; what this adds is the
    ``strict`` disposition for a caller that holds a ``Signal`` (see
    :func:`load_current_order_lifecycle_state`).
    """
    try:
        return resolve_order_lifecycle_state(
            order_state=_reconcilable_order_state(row),
            signals_status=_pick(row, "status"),
        )
    except UnknownSourceStatus as exc:
        if strict:
            logger.warning(
                "Signal %s's legacy columns could not be reconciled (%s); reporting NO "
                "state rather than this process's own memory.",
                signal.id,
                exc,
            )
            return None
        logger.warning(
            "Signal %s's legacy columns could not be reconciled (%s); reporting the state "
            "this process last persisted (%s).",
            signal.id,
            exc,
            signal.order_lifecycle_state.value,
        )
        return signal.order_lifecycle_state


# ══════════════════════════════════════════════════════════════════════════
# THE ENGINE SEAMS - duck-typed, and never defaulted to "approve"
# ══════════════════════════════════════════════════════════════════════════


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _proposed_size(signal: Signal) -> float:
    """The size to offer the risk component. ``0.0`` only when nothing was stated.

    Reads the quantity, then the sizing intention's own named size keys - the same two
    things Requirement 15.2 accepts as "requested quantity OR sizing intention" - so a
    signal sized by intention is not silently offered as a zero-size request.
    """
    if signal.quantity is not None:
        return float(signal.quantity)
    intention = signal.sizing_intention or {}
    for name in ("position_size", "quantity", "notional", "position_size_pct"):
        value = _number(intention.get(name))
        if value is not None:
            return float(value)
    return 0.0


async def _call_risk_engine(risk_engine: Any, signal: Signal) -> RiskVerdict:
    """Route ``signal`` through the risk validation component. Requirement 11.2.

    Tries the seams that exist in this codebase, in order of specificity, and REFUSES if
    none is present rather than approving by default:

    ``validate_signal(signal)`` / ``validate(signal)``
        An explicit per-signal seam. Anything a live risk validator would expose.
    ``check_risk(signal, proposed_size)``
        ``dag_risk_integration.RiskIntegratedExecutionPipeline``'s own method. It reads
        only ``signal.symbol``, so this module's :class:`Signal` fits it unchanged, and it
        returns a ``RiskCheckResult`` with ``approved``, ``reason`` and ``adjusted_size``.
    ``can_trade()``
        ``core.risk_engine.RiskEngine``'s portfolio-level gate (drawdown + daily loss). No
        per-signal detail, so the verdict carries the reason from
        ``check_drawdown``/``check_daily_loss`` where they exist.
    """
    for name in ("validate_signal", "validate_order", "validate"):
        seam = getattr(risk_engine, name, None)
        if callable(seam):
            return _read_risk_result(await _maybe_await(seam(signal)), seam=name)

    check_risk = getattr(risk_engine, "check_risk", None)
    if callable(check_risk):
        return _read_risk_result(
            await _maybe_await(check_risk(signal, _proposed_size(signal))),
            seam="check_risk",
        )

    can_trade = getattr(risk_engine, "can_trade", None)
    if callable(can_trade):
        allowed = bool(await _maybe_await(can_trade()))
        reason = None
        for probe in ("check_drawdown", "check_daily_loss"):
            gate = getattr(risk_engine, probe, None)
            if not callable(gate):
                continue
            verdict = await _maybe_await(gate())
            if isinstance(verdict, tuple) and len(verdict) == 2 and not verdict[0]:
                reason = _text(verdict[1])
                break
        return RiskVerdict(
            approved=allowed,
            reason=reason or ("risk checks passed" if allowed else "risk checks refused"),
            detail={"seam": "can_trade"},
        )

    raise SignalSubmissionRefused(
        "SIGNAL_RISK_ENGINE_UNUSABLE",
        f"The supplied risk component ({type(risk_engine).__name__}) exposes none of the "
        f"seams this path knows how to call (validate_signal, validate_order, validate, "
        f"check_risk, can_trade), so this signal cannot be routed through risk validation. "
        f"Requirement 11.2 does not permit submitting it anyway.",
        {"signal_id": signal.id, "risk_component": type(risk_engine).__name__},
    )


def _read_risk_result(result: Any, *, seam: str) -> RiskVerdict:
    """One risk component's report, as a :class:`RiskVerdict`.

    Accepts a ``RiskCheckResult``, a mapping, a bare bool, or a ``(allowed, reason)``
    tuple. An UNREADABLE report is a refusal, not an approval: a component that answered
    in a shape nobody here understands has not said yes.
    """
    if isinstance(result, RiskVerdict):
        return result
    if isinstance(result, bool):
        return RiskVerdict(approved=result, detail={"seam": seam})
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
        return RiskVerdict(approved=result[0], reason=_text(result[1]), detail={"seam": seam})

    approved = _flag(_pick(result, "approved", "passed", "allowed", "ok"))
    if approved is None:
        blocked = _flag(_pick(result, "blocked", "rejected", "refused"))
        if blocked is not None:
            approved = not blocked
    if approved is None:
        return RiskVerdict(
            approved=False,
            reason=(
                f"the risk component's {seam} report did not state a verdict this path "
                f"could read, so the signal is treated as refused rather than approved"
            ),
            detail={"seam": seam, "report": _jsonable(_dict_of(result), 1)},
        )

    return RiskVerdict(
        approved=bool(approved),
        reason=_text(_pick(result, "reason", "message", "detail")),
        adjusted_quantity=_number(
            _pick(result, "adjusted_size", "adjusted_quantity", "position_size")
        ),
        detail={"seam": seam, "report": _jsonable(_dict_of(result), 1)},
    )


async def _call_execution_engine(
    execution_engine: Any, signal: Signal, *, quantity: Optional[float]
) -> ExecutionOutcome:
    """Route ``signal`` through the order validation/execution component. Requirement 11.2.

    Tries, in order of specificity:

    ``submit_signal_order(signal)`` / ``submit_order(signal)`` / ``place_order(signal)``
        An explicit per-signal seam, which is what a live exchange executor wrapping
        ``execution_engine`` + ``execution_guard`` would expose.
    ``execute_trade(tenant_id, strategy_id, symbol, side, size, price, ...)``
        ``core.execution_engine.ExecutionEngine``'s unified interface, which returns an
        ``ExecutionResult``.
    ``process_signal(signal)``
        ``dag_risk_integration.RiskIntegratedExecutionPipeline``'s combined path.

    Every exception is caught and reported as a failed outcome rather than escaping,
    because an exchange error is an outcome this signal has and not a bug in this module -
    and because letting it escape would skip the transition that records it.

    WHICH FAILURE IT IS, THOUGH, IS NOT ALWAYS KNOWABLE HERE
        ``FAILED`` is terminal, so reporting an in-doubt order as a failure strands it (see
        :func:`classify_execution_failure` and the TASK 10.2 header). This function cannot
        see whether the request reached the venue - the seam it called is what knows - so it
        classifies the exception's TYPE and defaults to
        :data:`EXECUTION_FAILURE_INDETERMINATE` when the type says nothing, which holds the
        signal at ``PENDING`` for the Requirement 19.2 sweep instead of closing the record
        over a possibly-live order.
    """
    seam_name: Optional[str] = None
    try:
        for name in ("submit_signal_order", "submit_order", "place_order"):
            seam = getattr(execution_engine, name, None)
            if callable(seam):
                seam_name = name
                return _read_execution_result(
                    await _maybe_await(seam(signal)), signal, seam=name
                )

        execute_trade = getattr(execution_engine, "execute_trade", None)
        if callable(execute_trade):
            seam_name = "execute_trade"
            side = signal.side or signal.decision
            price = _number(_pick(signal.market_context or {}, "price", "last_price"))
            return _read_execution_result(
                await _maybe_await(
                    execute_trade(
                        tenant_id=signal.user_id,
                        strategy_id=signal.strategy_id,
                        symbol=signal.symbol,
                        side=side,
                        size=Decimal(str(quantity if quantity is not None else 0)),
                        price=Decimal(str(price if price is not None else 0)),
                        metadata={
                            "signal_id": signal.id,
                            "deployment_id": signal.deployment_id,
                            "idempotency_key": signal.idempotency_key,
                        },
                    )
                ),
                signal,
                seam="execute_trade",
            )

        process_signal = getattr(execution_engine, "process_signal", None)
        if callable(process_signal):
            seam_name = "process_signal"
            return _read_execution_result(
                await _maybe_await(process_signal(signal)), signal, seam="process_signal"
            )
    except Exception as exc:  # noqa: BLE001 - an exchange error is an OUTCOME, not a bug
        kind = classify_execution_failure(exc)
        indeterminate = kind == EXECUTION_FAILURE_INDETERMINATE
        logger.warning(
            "Execution component raised while submitting signal %s through %s: %s. "
            "Classified %s: %s",
            signal.id,
            seam_name or "an unknown seam",
            exc,
            kind,
            (
                "the order MAY be live at the venue, so this signal is held at PENDING "
                "for the Requirement 19.2 sweep rather than closed as FAILED"
                if indeterminate
                else "the venue answered and no order exists under this key"
            ),
        )
        return ExecutionOutcome(
            accepted=False,
            failure_kind=kind,
            reason=(
                f"the execution component raised, and the answer is {kind.lower()}: {exc}"
            ),
            detail={
                "seam": seam_name,
                "error": str(exc),
                "error_type": type(exc).__name__,
                "failure_kind": kind,
            },
        )

    raise SignalSubmissionRefused(
        "SIGNAL_EXECUTION_ENGINE_UNUSABLE",
        f"The supplied execution component ({type(execution_engine).__name__}) exposes "
        f"none of the seams this path knows how to call (submit_signal_order, "
        f"submit_order, place_order, execute_trade, process_signal), so this signal "
        f"cannot be routed through order validation/execution. Requirement 11.2 does not "
        f"permit placing the order by another route.",
        {"signal_id": signal.id, "execution_component": type(execution_engine).__name__},
    )


def _read_execution_result(result: Any, signal: Signal, *, seam: str) -> ExecutionOutcome:
    """One execution component's report, as an :class:`ExecutionOutcome`.

    Accepts an ``ExecutionResult``, a mapping, or an object exposing the same names. A
    report that states no success and no refusal is a FAILURE: an unreadable answer from
    the component that places orders must never be read as "the order is on the exchange".

    A report that STATED a negative outcome (``success=False``, or a status word that
    normalises to a refusal or a non-accepted state) is a DETERMINATE failure: the
    component answered, and it is the component's job to know. A report that stated
    NOTHING this function could read is INDETERMINATE - nobody knows what it said, so
    nobody may close the record on it. See :func:`classify_execution_failure`.
    """
    if isinstance(result, ExecutionOutcome):
        return result

    # _pick reads a mapping and an object identically, so no branch is needed here - which
    # is what lets an ExecutionResult dataclass, a PostgREST-style dict and a live
    # executor's own report all be read by one function.
    data = result
    success = _flag(_pick(data, "success", "accepted", "submitted", "ok"))
    status = _text(_pick(data, "status", "order_status", "state"))
    refused = _flag(_pick(data, "refused", "rejected", "blocked"))

    if refused is None and status is not None:
        refused = status.strip().lower() in ("rejected", "refused", "blocked", "denied")
    if success is None and status is not None:
        success = status.strip().lower() in (
            "completed", "skipped_completed", "success", "submitted", "open", "filled",
            "partial", "partially_filled", "accepted",
        )

    filled = _number(_pick(data, "filled", "filled_quantity", "executed_quantity"))
    quantity = _number(_pick(data, "quantity", "size", "requested_quantity")) or signal.quantity
    order_state = normalise_source_value(_pick(data, "order_state"))

    # The component said SOMETHING negative (a False success flag, or a status word) versus
    # it said nothing this function could read. Only the second is in doubt.
    stated_anything = success is not None or refused is not None or status is not None
    failure_kind = (
        EXECUTION_FAILURE_DETERMINATE if stated_anything else EXECUTION_FAILURE_INDETERMINATE
    )

    return ExecutionOutcome(
        accepted=bool(success) and not bool(refused),
        refused=bool(refused),
        failure_kind=failure_kind,
        reason=_text(_pick(data, "message", "reason", "detail")),
        order_id=_text(_pick(data, "order_id", "client_order_id", "exchange_order_id")),
        execution_id=_text(_pick(data, "execution_id", "trade_id")),
        order_state=order_state,
        filled=filled,
        quantity=quantity,
        detail={"seam": seam, "report": _jsonable(_dict_of(data), 1)},
    )


def resolve_execution_state(outcome: ExecutionOutcome) -> OrderLifecycleState:
    """The Order_Lifecycle_State an :class:`ExecutionOutcome` reports.

    Reconciled through ``order_lifecycle_state`` where the component named an
    ``OrderState`` (Requirement 16.2's whole purpose), and derived from the fill
    quantities otherwise. Never guesses: an ``order_state`` outside the ``OrderState``
    vocabulary is dropped rather than mapped, and the fill arithmetic only reports
    ``EXECUTED`` when the filled quantity actually reaches the requested one.

    AN INDETERMINATE FAILURE REPORTS ``PENDING``, NOT ``FAILED``
        ``PENDING`` is where the signal already is when the execution component is called
        (``submit_signal`` writes ``GENERATED -> PENDING`` first), so reporting it is what
        HOLDS the signal - no transition is written, the record keeps saying the last thing
        that was actually established, and the Requirement 19.2 sweep is what resolves it
        against the venue's own record. See the TASK 10.2 header.
    """
    if outcome.refused:
        return OrderLifecycleState.REJECTED
    if not outcome.accepted:
        if outcome.indeterminate:
            return OrderLifecycleState.PENDING
        return OrderLifecycleState.FAILED

    if outcome.order_state is not None:
        try:
            mapped = map_order_state(outcome.order_state)
        except UnknownSourceStatus:
            mapped = None
        if mapped is not None:
            return mapped

    filled = outcome.filled
    if filled is None or filled <= 0:
        return OrderLifecycleState.SUBMITTED
    requested = outcome.quantity
    if requested is not None and requested > 0 and filled + 1e-12 < requested:
        return OrderLifecycleState.PARTIALLY_EXECUTED
    return OrderLifecycleState.EXECUTED


# ══════════════════════════════════════════════════════════════════════════
# SUBMISSION (Requirements 11.2, 11.3, 16.7, 19.1, 19.5)
# ══════════════════════════════════════════════════════════════════════════


async def submit_signal(
    signal: Signal,
    *,
    risk_engine: Any,
    execution_engine: Any,
    sb: Any = None,
    user: Any = None,
    idempotency_layer: Any = None,
) -> OrderLifecycleState:
    """Submit ``signal`` under its own Idempotency_Key. Returns the state it reached.

    ``design.md`` -> "Idempotency key scheme", the ``ALGORITHM submit_signal`` block.
    Requirements 11.2, 11.3, 16.7, 19.1, 19.5.

    THE ROUTE
        ``execute_with_idempotency`` (task 9.1's layer, keyed by
        ``idempotency_key_for(signal)`` and given the signal path's own result TTL) wraps
        a single operation which, under the lock:

          GENERATED -> PENDING   accepted for submission, risk re-check pending
          risk validation        Requirement 11.2; a refusal is PENDING -> REJECTED
          order execution        Requirement 11.2; a refusal is PENDING -> REJECTED,
                                 a DETERMINATE failure is PENDING -> FAILED, and an
                                 INDETERMINATE one writes nothing at all - see below
          PENDING -> SUBMITTED   only once the component reports the order was accepted
          -> PARTIALLY_EXECUTED / EXECUTED where a fill was reported

        Every one of those transitions goes through ``assert_transition_legal`` first, is
        persisted second, and is appended to ``order_lifecycle_transitions`` third. The
        log's genesis row (``NULL -> GENERATED``) is written before the lock is taken, so
        a signal's history says when it was generated even if submission never happens.

    ON AN EXECUTION FAILURE THAT COULD NOT BE ANSWERED (the in-doubt order)
        A network error, a read timeout or an unreadable report means the venue MAY hold a
        live order. ``FAILED`` is terminal and the Requirement 19.2 sweep does not query
        the exchange for a terminal signal, so writing it would strand that order for
        good. The signal is therefore held at ``PENDING`` - no transition row, no state
        change, a loud warning - and this call returns ``PENDING``. See this section's
        header, "AN INDETERMINATE EXECUTION FAILURE IS HELD AT PENDING".

    ON DuplicateOrderError
        NOT a failure - it is Requirement 19.1's "at most one order" holding. Another
        attempt (this pod's, another pod's, or one that already finished) owns this key, so
        the persisted state is read back and returned.

    ON THE IDEMPOTENCY STORE BEING UNAVAILABLE
        Requirement 19.5. The order is NOT submitted unguarded. The signal is held at
        ``PENDING`` - "submission was withheld pending the idempotency store's
        availability" - and the caller retries under the same key, which is the same key by
        construction because it is derived from ``signal.id``. The withhold is written only
        if the persisted state has not already moved past ``PENDING``: Requirement 19.3
        forbids overwriting an in-flight signal's state with an earlier one, and the store
        can just as easily have failed while STORING the result of a submission that
        already reached the exchange.

    Args:
        signal: A persisted :class:`Signal`. ``generate_signal`` returns one only when a
            row exists, so a signal reaching here has always been persisted.
        risk_engine: The risk validation component. REQUIRED - see this section's header
            on why it has no default.
        execution_engine: The order validation/execution component. REQUIRED.
        sb: An RLS-scoped PostgREST client. Built from ``user``'s access token when absent.
        user: The owning user, for the client only. Never an authorisation decision - the
            row's ``user_id`` is the signal's own and RLS is what enforces it.
        idempotency_layer: The :class:`DistributedIdempotencyLayer` to guard with. Defaults
            to the process singleton; injectable for a test.

    Returns:
        The :class:`OrderLifecycleState` the signal is in when this call returns.

    Raises:
        SignalSubmissionRefused: A control is missing or unusable (no risk engine, no
            execution engine, no database client). The signal is left where it was.
        OrderLifecycleRejected: A transition was refused (Requirement 16.6). The prior
            state stands.
        SignalPersistenceError: A state could not be persisted.
    """
    if risk_engine is None:
        raise SignalSubmissionRefused(
            "SIGNAL_RISK_ENGINE_MISSING",
            f"Signal {signal.id} cannot be submitted without a risk validation component. "
            f"Requirement 11.2 routes every generated Signal through risk validation "
            f"before any order may be submitted, so an absent validator is a refusal, "
            f"never an approval.",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        )
    if execution_engine is None:
        raise SignalSubmissionRefused(
            "SIGNAL_EXECUTION_ENGINE_MISSING",
            f"Signal {signal.id} cannot be submitted without the platform's order "
            f"validation/execution component. Requirement 11.2 forbids a second "
            f"order-placement path that bypasses it, and submitting without it would be "
            f"exactly that path.",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        )

    client = await _resolve_client(sb, user, None)
    if client is None:
        raise SignalSubmissionRefused(
            "SIGNAL_NO_PERSISTENCE_CLIENT",
            f"No database client is available to record signal {signal.id}'s lifecycle "
            f"transitions, so it is not submitted. Requirement 14.5 requires the order "
            f"state to be persisted before it is reported, and an order placed with no "
            f"way to record that it was placed is the one order this platform must never "
            f"send.",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        )

    layer = idempotency_layer or get_idempotency_layer()
    key = idempotency_key_for(signal)

    # The log's genesis row, before anything else. Task 10.1 left every write to
    # order_lifecycle_transitions here, including this one; written outside the lock so a
    # signal that never gets submitted still has a history that says when it was
    # generated. Idempotent, because the log is append-only.
    await anchor_generated_transition(client, signal)

    # Task 14.2's first frame, and the earliest point at which one is honest: the row
    # exists (task 10.1 wrote it) and the log now says when it was generated. Requirement
    # 14.5's "persist before reporting", read literally. Contained - see that section.
    await publish_signal_generated(signal)

    # The operation's own view of the signal, published back out so the exception handlers
    # below can see how far it actually got.
    state: Dict[str, Signal] = {"signal": signal}

    async def route_through_risk_and_execution() -> Dict[str, Any]:
        """The at-most-once body. Everything inside runs under the Idempotency_Key's lock.

        Returns a JSON-serialisable dict because the layer caches it (``store_result``
        json-encodes it), and because that cached dict is what a later duplicate attempt
        receives back instead of running this again.
        """
        current = state["signal"]

        # GENERATED -> PENDING. Requirement 16.3's internal-only pre-submission
        # sub-states (risk-check-pending, approved-pending-submission) both report as
        # PENDING, so this single transition covers the whole pre-submission window
        # rather than inventing states the vocabulary does not have.
        current = await apply_order_lifecycle_state(
            client,
            current,
            OrderLifecycleState.PENDING,
            reason="accepted for submission; risk re-validation pending",
        )
        state["signal"] = current

        # Requirement 11.2 - risk validation, before any order may be submitted.
        verdict = await _call_risk_engine(risk_engine, current)
        if not verdict.approved:
            # Requirement 11.3 - persist the rejected outcome, submit nothing.
            current = await apply_order_lifecycle_state(
                client,
                current,
                OrderLifecycleState.REJECTED,
                reason=f"risk validation refused: {verdict.reason or 'no reason given'}",
            )
            state["signal"] = current
            return _submission_result(current, reason=verdict.reason)

        quantity = (
            verdict.adjusted_quantity
            if verdict.adjusted_quantity is not None
            else current.quantity
        )
        if (
            verdict.adjusted_quantity is not None
            and current.quantity is not None
            and verdict.adjusted_quantity != current.quantity
        ):
            logger.info(
                "Signal %s: risk validation reduced the requested quantity from %s to %s.",
                current.id,
                current.quantity,
                verdict.adjusted_quantity,
            )

        # Requirement 11.2 - order validation/execution. No second placement path.
        outcome = await _call_execution_engine(
            execution_engine, current, quantity=quantity
        )
        reached = resolve_execution_state(outcome)

        if outcome.indeterminate:
            # The order may be live and nothing here can tell. Requirement 19.2's sweep is
            # the only thing that can resolve it, and it can only do so from a
            # non-terminal state - so the signal is HELD, loudly, and no marker is written
            # here: marking is the sweep's own clause and the sweep is what will have
            # attempted the exchange lookup that justifies one.
            logger.warning(
                "Signal %s's submission is IN DOUBT: %s. It is held at %s under "
                "idempotency key %s and MUST be resolved by the crash-recovery sweep "
                "(Requirement 19.2), which queries the venue by that key. It is NOT "
                "recorded as FAILED, because FAILED is terminal and would strand a live "
                "order permanently.",
                current.id,
                outcome.reason or "no reason given",
                reached.value,
                signal.idempotency_key,
            )

        current = await apply_order_lifecycle_state(
            client,
            current,
            reached,
            reason=_execution_reason(outcome, reached),
            order_id=outcome.order_id,
            execution_id=outcome.execution_id,
            # The one justified opt-in. A report of "accepted, and filled" means the order
            # reached the exchange AND filled, so PENDING -> SUBMITTED -> EXECUTED records
            # two things that both happened. A refusal or a failure is always a single hop,
            # so the walk is never enabled for a state the signal did not actually pass
            # through.
            through_intermediate_states=outcome.accepted and not outcome.refused,
        )
        state["signal"] = current
        return _submission_result(current, reason=outcome.reason)

    try:
        result = await layer.execute_with_idempotency(
            tenant_id=signal.user_id,
            client_order_id=key,
            operation=route_through_risk_and_execution,
            # Requirement 19.1's "enforced for the entirety of that Signal's lifecycle".
            # Passed per call; every other caller of this layer keeps the 1-hour default.
            result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
        )
    except DuplicateOrderError:
        # Requirement 19.1 holding, not a failure. Also where 005b's contract lands: a
        # 23505 on uq_signals_idempotency_key is translated into this exception by
        # _insert_signal_row, and this branch is the "return the signal's current
        # Order_Lifecycle_State" half of that contract.
        current = await load_current_order_lifecycle_state(client, state["signal"])
        logger.info(
            "Signal %s is already guarded by idempotency key %s; reporting its current "
            "state %s rather than submitting again.",
            signal.id,
            key,
            current.value,
        )
        # A state READ BACK, not one this call caused - another attempt owns the key and
        # wrote it. That is a resync, so it travels as a SNAPSHOT rather than as a
        # transition this page should append to a signal's history.
        await publish_signal_snapshot(
            state["signal"].with_order_lifecycle_state(current),
            reason=f"already guarded by idempotency key {key}",
        )
        return current
    except RuntimeError as exc:
        # The layer's fail-closed posture in production: Redis unreachable. Requirement
        # 19.5 - withheld, never submitted unguarded.
        return await _withhold_pending_idempotency_store(client, state["signal"], exc)

    reported = normalise_lifecycle_state(_pick(result, "order_lifecycle_state"))
    if reported is not None:
        return reported
    # A cached result the layer could not shape back into a state (an older payload, a
    # truncated cache entry). The row is authoritative, so read it.
    recovered = await load_current_order_lifecycle_state(client, state["signal"])
    # Read back rather than caused - a SNAPSHOT, for the same reason as the duplicate
    # branch above.
    await publish_signal_snapshot(
        state["signal"].with_order_lifecycle_state(recovered),
        reason="reporting the persisted state; the cached submission result was unreadable",
    )
    return recovered


def _submission_result(signal: Signal, *, reason: Optional[str] = None) -> Dict[str, Any]:
    """The at-most-once operation's return value, and therefore its cached result.

    Small and JSON-only on purpose: the idempotency layer json-encodes it, and a later
    duplicate attempt gets this dict back instead of re-running the submission - so it
    must carry everything the caller needs to answer "what happened to this signal"
    without another database read.
    """
    return {
        "signal_id": signal.id,
        "order_lifecycle_state": signal.order_lifecycle_state.value,
        "order_id": signal.order_id,
        "execution_id": signal.execution_id,
        "idempotency_key": signal.idempotency_key,
        "reason": reason,
    }


def _execution_reason(outcome: ExecutionOutcome, reached: OrderLifecycleState) -> str:
    """A one-line reason for the transition log. Names the seam and what it reported."""
    seam = (outcome.detail or {}).get("seam") or "the execution component"
    if outcome.refused:
        return f"order validation/execution refused via {seam}: {outcome.reason or 'no reason given'}"
    if not outcome.accepted:
        if outcome.indeterminate:
            # Written only if the signal is somehow NOT already at PENDING; the normal case
            # writes nothing at all, because reached == the state it is already in.
            return (
                f"order submission is IN DOUBT via {seam} - the venue may hold a live "
                f"order and did not answer: {outcome.reason or 'no reason given'}. Held "
                f"for the crash-recovery sweep (Requirement 19.2); not resubmitted"
            )
        return f"order submission failed via {seam}: {outcome.reason or 'no reason given'}"
    detail = outcome.reason or "accepted"
    if outcome.filled:
        detail = f"{detail}; filled {outcome.filled} of {outcome.quantity}"
    return f"order submitted via {seam} -> {reached.value}: {detail}"


async def _withhold_pending_idempotency_store(
    sb: Any, signal: Signal, exc: BaseException
) -> OrderLifecycleState:
    """Requirement 19.5's withhold: hold at ``PENDING``, submit nothing, retry later.

    Reads the PERSISTED state first, and writes nothing if it has already moved past
    ``PENDING``. That is not defensive padding - it is the case where the idempotency
    store failed while STORING the result of a submission that had already reached the
    exchange, and writing ``PENDING`` over ``SUBMITTED`` there would be exactly the
    "overwritten with an earlier state" Requirement 19.3 forbids, on a signal that has a
    live order behind it.
    """
    persisted = await load_current_order_lifecycle_state(sb, signal)
    reason = f"idempotency store unavailable: {exc}"

    if persisted is OrderLifecycleState.GENERATED:
        current = await apply_order_lifecycle_state(
            sb, signal.with_order_lifecycle_state(persisted), OrderLifecycleState.PENDING,
            reason=reason,
        )
        logger.warning(
            "Signal %s was NOT submitted: %s. It is held at PENDING and must be retried "
            "under the same idempotency key (%s) once the store recovers.",
            signal.id,
            reason,
            signal.idempotency_key,
        )
        return current.order_lifecycle_state

    logger.warning(
        "Signal %s met an unavailable idempotency store (%s) but is already persisted at "
        "%s, so its state is left alone rather than moved back to PENDING (Requirement "
        "19.3). It must be resolved by the crash-recovery path, not resubmitted.",
        signal.id,
        exc,
        persisted.value,
    )
    # Nothing was written, and the state this call is reporting is one it OBSERVED rather
    # than caused - so it travels as a SNAPSHOT, not as a transition. See the task 14.2
    # section header.
    await publish_signal_snapshot(
        signal.with_order_lifecycle_state(persisted), reason=reason
    )
    return persisted


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 14.2 - ANNOUNCING A TRANSITION ON signal.{deployment_id}
#
#  Spec: trading-lifecycle-integration. design.md -> "WebSocket design"
#  ("Sequencing, dedup, ordering, backpressure"). Requirements 18.1, 18.4,
#  23.3, 23.6.
#
#  WHAT THIS SECTION OWNS, AND WHERE THE REST OF IT LIVES
#  -----------------------------------------------------
#  Only the PRODUCER. The channel family, its three frame types and its
#  ownership lookup are task 14.1's (``ws_channels.SIGNAL_FAMILY``,
#  ``core/websocket_auth``), the ``seq`` counter and the frame builder are
#  ``ws_channels.next_signal_sequence`` / ``ws_channels.signal_frame``, and the
#  transport is ``WebSocketManager.broadcast_signal_event``. Nothing here
#  composes a channel name, assigns a sequence number, projects a signal or
#  decides who may receive a frame.
#
#  A PUBLISH FAILURE MUST NEVER FAIL A PERSISTED TRANSITION
#  ------------------------------------------------------
#  The load-bearing rule of this section, and the reason every function in it
#  returns a count instead of raising. The record is the truth; a realtime frame
#  is observability. A browser that is not connected, a manager that was never
#  started, a send that fails mid-flight - none of those may turn a transition
#  this module has already GATED, WRITTEN and AUDITED into a failed submission.
#  So every exception is contained here, at the publish seam, and reported as a
#  warning naming the signal and the frame that was lost.
#
#  The recovery from a lost frame already exists and is not this module's:
#  Requirement 18.6 / 23.6 have a reconnecting page request a SNAPSHOT rather
#  than assume it missed nothing, and ``GET /api/signal-trace/signals`` (task
#  13.1) reads the persisted row. A frame that never arrived costs the page one
#  resync, not a wrong state.
#
#  WHERE THE THREE FRAME TYPES ARE PUBLISHED FROM, AND WHY THERE
#  ------------------------------------------------------------
#  * GENERATED - from :func:`submit_signal`, immediately after
#    :func:`anchor_generated_transition`. That is the first moment the signal is
#    both persisted (task 10.1 wrote the row) and anchored in the transition log,
#    which is Requirement 14.5's "persist before reporting" read literally.
#
#  * STATUS_CHANGED - from inside :func:`apply_order_lifecycle_state`, once per
#    HOP, after that hop's audit row. That is the single write path for every
#    transition in this module (task 10.2's own claim), so putting the publish
#    there is what makes "every transition is announced" structural rather than
#    a list of call sites somebody has to keep complete. A multi-hop route
#    (``PENDING -> SUBMITTED -> EXECUTED``) publishes TWO frames because two
#    transitions were written, and their content keys
#    (``signal_id:SUBMITTED``, ``signal_id:EXECUTED``) differ - which is exactly
#    what Requirement 18.4's dedup key is for.
#
#  * SNAPSHOT - from the paths that REPORT a state this call did not cause: the
#    ``DuplicateOrderError`` branch (another attempt owns the key; the state was
#    read back), the Requirement 19.5 withhold when the row has already moved
#    past ``PENDING`` (nothing was written), and a cached result the idempotency
#    layer could not shape back into a state (the row was re-read). None of
#    those is history the page should append; each is current state the page
#    should resync to, which is the distinction the SNAPSHOT frame type exists
#    to carry (Requirements 18.6, 23.6).
#
#  NOTHING IS PUBLISHED BEFORE THE WRITE, ANYWHERE
#  ----------------------------------------------
#  Requirement 14.5. Every call below is downstream of a completed persist, and
#  the publish inside ``apply_order_lifecycle_state`` sits after step 3 (audit)
#  rather than between steps 2 and 3, so a frame can never describe a transition
#  whose audit row failed to write.
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


async def publish_signal_frame(
    signal: Signal,
    event: Any,
    *,
    reason: Optional[str] = None,
    previous_state: Any = None,
    manager: Any = None,
) -> int:
    """Announce ``signal`` on ``signal.{deployment_id}``. Never raises. Returns the reach.

    ``event`` is a :class:`ws_channels.SignalEvent` (or its value). The frame is built
    by ``ws_channels.signal_frame``, which stamps Requirement 23.6's per-channel ``seq``
    and Requirement 18.4's content-level dedup key; this function chooses neither.

    ``manager`` defaults to the process's ``WebSocketManager``. Injectable for a test,
    and for a worker that holds its own.

    Returns
        The number of connections the frame reached - 0 when nobody was listening, when
        the signal names no deployment, or when publishing failed. A caller must not
        read 0 as a failure of the transition: see this section's header.
    """
    if signal is None or not signal.deployment_id:
        # A signal with no deployment has no channel. Not an error: a paper/preview
        # signal minted outside a deployment is a legitimate record, it simply has no
        # page watching one deployment's stream.
        return 0

    try:
        from backend_app.backend.ws_channels import signal_frame

        frame = signal_frame(
            event,
            signal,
            reason=reason,
            previous_state=previous_state,
        )
    except Exception as exc:  # noqa: BLE001 - a frame that could not be built is not a
        # failed transition. Loud, because an unbuildable frame is a producer bug.
        logger.warning(
            "Signal %s reached %s but no %s frame could be built for "
            "signal.%s (%s); the persisted record stands and a reconnecting page will "
            "resync from it.",
            getattr(signal, "id", None),
            signal.order_lifecycle_state.value,
            getattr(event, "value", event),
            signal.deployment_id,
            exc,
        )
        return 0

    try:
        if manager is None:
            from backend_app.backend.websocket_manager import get_websocket_manager

            manager = get_websocket_manager()

        # ``owner_id`` is the signal's own ``user_id``, which is the deployment's owner
        # (``_deployment_facts`` projected it from that row). Passing it lets the
        # transport re-check the subscribed connection at send time; it can only ever
        # agree, which is why it is cheap enough to keep as a second line of defence.
        delivered = await manager.broadcast_signal_event(
            signal.deployment_id,
            frame["type"],
            frame,
            owner_id=signal.user_id,
        )
    except Exception as exc:  # noqa: BLE001 - CONTAINED. See this section's header.
        logger.warning(
            "Signal %s's %s frame could not be published on signal.%s (%s). The "
            "transition is persisted and audited; the frame is lost and a reconnecting "
            "page will resync through the snapshot it already requests (Requirement "
            "18.6).",
            signal.id,
            frame["type"],
            signal.deployment_id,
            exc,
        )
        return 0

    return int(delivered or 0)


async def publish_signal_generated(signal: Signal, *, manager: Any = None) -> int:
    """Announce that a Signal row now exists (``SignalEvent.GENERATED``)."""
    from backend_app.backend.ws_channels import SignalEvent

    return await publish_signal_frame(
        signal, SignalEvent.GENERATED, reason="signal generated", manager=manager
    )


async def publish_signal_status_changed(
    signal: Signal,
    *,
    previous_state: Any = None,
    reason: Optional[str] = None,
    manager: Any = None,
) -> int:
    """Announce one PERSISTED Order_Lifecycle_State transition (``STATUS_CHANGED``).

    A separate frame type from :func:`publish_signal_generated` because it is a separate
    fact - a page that heard only "generated" would render a decision as though its
    order were still pending (task 14.1's own reasoning for the vocabulary).
    """
    from backend_app.backend.ws_channels import SignalEvent

    return await publish_signal_frame(
        signal,
        SignalEvent.STATUS_CHANGED,
        reason=reason,
        previous_state=previous_state,
        manager=manager,
    )


async def publish_signal_snapshot(
    signal: Signal, *, reason: Optional[str] = None, manager: Any = None
) -> int:
    """Announce CURRENT state, for a state this call observed rather than caused.

    Requirements 18.6 and 23.6. A reconnecting page requests a snapshot rather than
    assuming it missed nothing, and a replayed snapshot is not new history - naming it
    distinctly is what lets the page's reducer resync instead of appending. The same
    frame type carries the three "read the row back" paths in :func:`submit_signal`.
    """
    from backend_app.backend.ws_channels import SignalEvent

    return await publish_signal_frame(
        signal, SignalEvent.SNAPSHOT, reason=reason, manager=manager
    )


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 10.3 - the Live_Runtime's ACTION-node output, wired into this module
#
#  Spec: trading-lifecycle-integration. design.md -> "New module:
#  signal_service.py" ("...and the wiring between the Live_Runtime's
#  ACTION-node output and the risk/execution path"). Requirements 14.3, 14.5,
#  14.6, 14.7, 14.8, 15.5.
#
#  WHAT THIS SECTION OWNS
#  ----------------------
#  The three things 10.1 and 10.2 each explicitly deferred:
#
#    * WHICH ACTION-node evaluation calls generate_signal / submit_signal, and
#      the two gates that stand in front of that call - the closure-READY gate
#      (Requirement 14.3) and the risk validation of the candidate.
#    * Stale/gapped-feed suspension and automatic resume (Requirement 14.6).
#    * Per-event error containment (Requirement 14.7): one event's failure
#      aborts that event's signal generation and nothing else. This is where
#      SignalPersistenceError is recorded against the deployment and processing
#      continues to the next event (Requirements 14.7, 14.8, 15.5).
#
#  WHY IT IS A CLASS AND NOT A FUNCTION
#  ------------------------------------
#  Two of the three things above are STATEFUL per deployment and cannot be
#  decided from one event in isolation:
#
#    * Suspension is a transition, not a predicate. "Suspend generation, mark
#      the deployment's live-data health state, resume once the feed returns to
#      LIVE" needs to know whether it was already suspended, or every stale
#      event would re-write the deployment row and every fresh one would claim
#      a recovery that did not happen.
#    * The risk and execution components are per-deployment objects (10.2's
#      header says so), so something has to hold them for the life of the
#      deployment. A module-level singleton would be one risk configuration for
#      every deployment in the process, which is Requirement 11.1's binding
#      ("one risk configuration, one execution configuration" per Deployment)
#      broken by construction.
#
#  So :class:`LiveSignalPath` is one object per running Deployment, held by the
#  Live_Runtime, and every method on it is about one market event.
#
#  THE CLOSURE GATE IS FAIL-CLOSED, AND "UNDETERMINED" IS NOT "READY"
#  -----------------------------------------------------------------
#  Requirement 14.3: a Signal is generated "only if every upstream node in the
#  emitting action's closure is ready". :func:`closure_readiness` answers with
#  three values, not two - ``True``, ``False`` and ``None`` for "no evidence
#  either way" - and ``None`` refuses. That is the only defensible reading: the
#  requirement is a permission conditioned on a fact, so an absent fact is an
#  absent permission. Collapsing ``None`` into ``True`` would generate signals
#  from an evaluation nobody can show was ready, and collapsing it into
#  ``False`` (silently, without saying why) would make a wiring mistake look
#  like a quiet strategy.
#
#  The strongest evidence is the runtime's own: ``DAGEngine.execute_plan``
#  already enforces this gate internally - "an intent is present only when its
#  ACTION node and every node in that node's upstream closure hold READY" - and
#  it publishes the per-node verdict on its ``PlanRuntimeState``. So when a
#  plan and that state are available, the closure is walked with the plan's own
#  ``predecessors`` and checked against the state's own labels. Nothing about
#  readiness is re-derived here, and the ``READY`` label itself is imported from
#  ``model_readiness`` rather than spelled again.
#
#  THE FEED GATE IS feed_state.py's THRESHOLD, NOT A SECOND ONE
#  -----------------------------------------------------------
#  Requirement 14.6 says "beyond the platform's existing staleness threshold",
#  and the platform has exactly one: ``feed_state.py``, where ``LIVE`` is
#  ``age < 1.5 x the expected bar interval`` and ``STALE`` is ``>= 3 x``. This
#  section defines no threshold, no timer and no second age measurement - it
#  calls ``evaluate_feed_state`` and generates only while the verdict is
#  ``LIVE``. Everything else it can report (``DELAYED``, ``STALE``,
#  ``DISCONNECTED``, ``INSUFFICIENT_DATA``) suspends generation, which is what
#  "shall not generate a Signal from stale or gapped market data" means for
#  each of them: a gapped feed reads ``DELAYED`` or ``INSUFFICIENT_DATA``, and a
#  disconnected one cannot deliver the event that would qualify at all.
#
#  A feed that has never been evaluated also refuses. That is the same
#  disposition ``feed_state`` itself takes ("a feed nobody has measured is
#  never reported LIVE"), carried through to the decision it exists to inform.
#
#  WHERE THE HEALTH STATE AND THE FAILURES ARE RECORDED, AND WHY THERE
#  -----------------------------------------------------------------
#  ``public.strategy_deployments.error_message`` (003_signal_trace_restoration
#  section 3). It is the column this platform already uses to say why a
#  deployment is not doing what its status claims - Requirement 11.4's startup
#  failure reason lands there too - and Requirements 14.6 and 14.7 ask for
#  exactly that: "mark the Deployment's live-data health state accordingly" and
#  "record the failure against the Deployment". No migration in this spec adds
#  a live-data-health column and task 10.3 has no migration bullet, so
#  inventing one here would be inventing schema.
#
#  Two consequences, both deliberate:
#
#    * Every message this section writes there is PREFIXED
#      (:data:`DEPLOYMENT_HEALTH_PREFIX`), so an operator can tell a live-data
#      suspension from a startup refusal at a glance.
#    * A resume CLEARS the column only when this process is the one that set
#      it. The alternative - clearing unconditionally - would erase a startup
#      failure reason, or another worker's message, the first time a feed
#      recovered.
#
#  WHAT THIS SECTION DOES NOT OWN
#  ------------------------------
#  * Publishing GENERATED / STATUS_CHANGED / SNAPSHOT frames on
#    ``signal.{deployment_id}`` - task 14.2. Nothing is published from this
#    section: every frame a routed event produces is published by the two seams
#    that task wired inside ``submit_signal`` and
#    ``apply_order_lifecycle_state``, so a path that generates and submits a
#    signal announces it exactly once per persisted fact rather than once here
#    and once there. :meth:`SignalPathOutcome.to_dict` remains this section's
#    OWN report to its caller - the Live_Runtime's per-event outcome - and is
#    not a frame.
#  * The crash-recovery sweep (query the exchange by Idempotency_Key on
#    restart) - Requirement 19.2, task 12. This section contains a failure and
#    moves on; it does not reconcile one.
#  * Node evaluation itself. ``DAGEngine.execute_plan`` evaluates, this routes.
#    :meth:`LiveSignalPath.record_evaluation_failure` is the seam the runtime
#    reports a node evaluation error through, and it is the whole of
#    Requirement 14.7's "record the failure against the Deployment without
#    generating a Signal".
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


from backend_app.backend.feed_state import (  # noqa: E402
    FeedState,
    FeedStateReport,
    evaluate_feed_state,
    observe_feed,
)

# The one spelling of "this node's value at the current bar is trustworthy".
# ``dag_engine`` imports it from here too (``NODE_READY as READY``), so the runtime that
# writes the label and the gate that reads it cannot drift. Imported from
# ``model_readiness`` rather than from ``dag_engine`` on purpose: this module must stay
# importable without pandas, numpy and the executor stack.
from backend_app.backend.model_readiness import NODE_READY  # noqa: E402

#: The one feed state a Signal may be generated in. Requirement 14.6, and the boundary is
#: ``feed_state.py``'s (``LIVE`` is strictly ``age < 1.5 x interval``), not a second one.
FEED_GATE_STATE: FeedState = FeedState.LIVE

#: ``observation_source`` for a reading the Live_Runtime measured itself rather than
#: reading off the connection monitor. Distinct from ``feed_state.OBSERVATION_SUPPLIED``'s
#: generic label so a report says WHICH caller measured it.
OBSERVATION_SUPPLIED_LOCAL = "live_runtime"

#: Every message this section writes to ``strategy_deployments.error_message`` starts with
#: this, so a live-data or per-event note is distinguishable from Requirement 11.4's
#: startup failure reason, and so a resume can tell whether it is clearing its own note.
DEPLOYMENT_HEALTH_PREFIX = "LIVE_RUNTIME"

#: The deployment column the live-data health state and the per-event failures are
#: recorded on. Named once so the two writers cannot disagree.
DEPLOYMENT_HEALTH_COLUMN = "error_message"


# ══════════════════════════════════════════════════════════════════════════
# WHAT ONE EVENT'S ROUTING ENDED IN
# ══════════════════════════════════════════════════════════════════════════

#: The signal was generated, persisted and submitted. ``order_lifecycle_state`` says how
#: far it got - which may be ``REJECTED`` or ``FAILED``, both of which are outcomes a
#: submitted signal legitimately reaches (Requirement 11.3).
OUTCOME_SUBMITTED = "SUBMITTED"

#: Generation is suspended because the feed is not ``LIVE`` (Requirement 14.6).
OUTCOME_FEED_SUSPENDED = "FEED_SUSPENDED"

#: The emitting action's upstream closure is not (or cannot be shown to be) READY
#: (Requirement 14.3).
OUTCOME_CLOSURE_NOT_READY = "CLOSURE_NOT_READY"

#: Risk validation refused the candidate before any signal row existed
#: (Requirement 14.3). No signal id is consumed and nothing is persisted.
OUTCOME_RISK_REFUSED = "RISK_REFUSED"

#: The node output does not describe an actionable, attributable, sized signal. A HOLD
#: lands here, and it is not a failure.
OUTCOME_GENERATION_REFUSED = "GENERATION_REFUSED"

#: The signal could not be persisted, so it was NOT reported and NOT routed
#: (Requirement 15.5). Recorded against the deployment; the next event still runs.
OUTCOME_NOT_PERSISTED = "NOT_PERSISTED"

#: Node-state update, indicator evaluation, ML/DL evaluation or logic/action evaluation
#: raised for this event (Requirement 14.7). No signal for this event; later events run.
OUTCOME_NODE_EVALUATION_FAILED = "NODE_EVALUATION_FAILED"

#: Anything else this event raised, contained rather than propagated (Requirement 14.7).
OUTCOME_CONTAINED_ERROR = "CONTAINED_ERROR"

#: The outcomes in which no signal row exists. Published so a caller does not have to
#: re-derive "did this produce a Signal" from the status string.
OUTCOMES_WITHOUT_SIGNAL: Tuple[str, ...] = (
    OUTCOME_FEED_SUSPENDED,
    OUTCOME_CLOSURE_NOT_READY,
    OUTCOME_RISK_REFUSED,
    OUTCOME_GENERATION_REFUSED,
    OUTCOME_NOT_PERSISTED,
    OUTCOME_NODE_EVALUATION_FAILED,
)


@dataclass(frozen=True)
class SignalPathOutcome:
    """What routing one ACTION-node output ended in. Returned, never raised.

    Requirement 14.7 asks that a failure on one event not stop the next one, which means
    the per-event seam cannot signal by exception - a raise would have to be caught by the
    event loop, and an event loop that catches everything cannot tell a contained failure
    from a bug. So every path through :meth:`LiveSignalPath.on_action_output` returns one
    of these, and the caller decides what to log, publish or count.

    ``signal`` is present only when a row was persisted for it: :func:`generate_signal`
    returns a ``Signal`` only in that case, so ``outcome.signal is not None`` is exactly
    "this decision is on the record" (Requirements 14.5, 15.5).
    """

    status: str
    reason: Optional[str] = None
    signal: Optional[Signal] = None
    order_lifecycle_state: Optional[OrderLifecycleState] = None
    code: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def generated(self) -> bool:
        """Whether a persisted Signal exists for this event."""
        return self.signal is not None

    @property
    def submitted(self) -> bool:
        return self.status == OUTCOME_SUBMITTED

    def to_dict(self) -> Dict[str, Any]:
        """The wire form. Task 14.2's frames render from here, not from a row."""
        return {
            "status": self.status,
            "reason": self.reason,
            "code": self.code,
            "signal_id": self.signal.id if self.signal is not None else None,
            "order_lifecycle_state": (
                self.order_lifecycle_state.value
                if self.order_lifecycle_state is not None
                else None
            ),
            "signal": self.signal.to_public_dict() if self.signal is not None else None,
            "detail": dict(self.detail or {}),
        }


# ══════════════════════════════════════════════════════════════════════════
# THE CLOSURE-READY GATE (Requirement 14.3)
# ══════════════════════════════════════════════════════════════════════════

#: How the readiness verdict was reached. Carried on the verdict so a refusal can say what
#: it looked at, which is the difference between "your indicator is warming" and "this
#: runtime told me nothing".
CLOSURE_SOURCE_PLAN_STATE = "plan_runtime_state"
CLOSURE_SOURCE_REPORTED_FLAG = "node_output.closure_ready"
CLOSURE_SOURCE_NODE_READINGS = "node_output.node_closure"
CLOSURE_SOURCE_NONE = "no_evidence"


@dataclass(frozen=True)
class ClosureReadiness:
    """Whether every upstream node in the emitting action's closure is READY.

    ``ready`` is deliberately tri-state. ``None`` means no source said anything about
    readiness, and it refuses - see this section's header on why "undetermined" cannot be
    read as "ready" for a gate that stands in front of a real order.
    """

    ready: Optional[bool]
    source: str
    action_node_id: Optional[str] = None
    node_states: Dict[str, str] = field(default_factory=dict)
    not_ready: Tuple[str, ...] = ()

    @property
    def determined(self) -> bool:
        return self.ready is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ready": self.ready,
            "source": self.source,
            "action_node_id": self.action_node_id,
            "closure_size": len(self.node_states),
            "not_ready": list(self.not_ready),
        }


def _plan_upstream_closure(plan: Any, action_node_id: str) -> Optional[Tuple[str, ...]]:
    """Every node transitively upstream of ``action_node_id``, or ``None`` if unaskable.

    The walk is over the plan's OWN ``predecessors``, which is the same accessor
    ``DAGEngine._upstream_closure`` uses for the same requirement. Duck-typed, because
    this module must not import the compiled-plan stack to ask a graph question.
    """
    predecessors = getattr(plan, "predecessors", None)
    if not callable(predecessors):
        return None
    closure: List[str] = []
    seen = {str(action_node_id)}
    try:
        stack = [str(node) for node in (predecessors(action_node_id) or ())]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            closure.append(current)
            stack.extend(str(node) for node in (predecessors(current) or ()))
    except Exception as exc:  # noqa: BLE001 - an unaskable plan is not a ready closure
        logger.warning(
            "Could not walk the upstream closure of action node %s: %s",
            action_node_id,
            exc,
        )
        return None
    return tuple(closure)


def closure_readiness(
    node_output: Any = None,
    *,
    plan: Any = None,
    runtime_state: Any = None,
    action_node_id: Optional[str] = None,
) -> ClosureReadiness:
    """Requirement 14.3's readiness fact about one ACTION node's closure.

    Evidence is consulted in order of authority and the first usable answer wins:

    1. ``plan`` + ``runtime_state``. The runtime's own per-node verdict, checked over the
       closure the plan itself reports. This is the only source that can be WRONG only if
       the runtime is wrong, and it is what a plan-bound Live_Runtime always has: the
       ACTION node and every node upstream of it must hold :data:`NODE_READY`.
    2. ``closure_ready`` on the node output. An emitter that already ran the gate and is
       reporting its verdict (``DAGEngine.execute_plan`` enforces it internally, so an
       intent it returned is one whose closure was ready).
    3. ``node_closure`` on the node output, WHEN every reading states its own readiness.
       A closure of bare indicator values states none, and is not evidence - a number is
       not a readiness claim.

    Returns
        A :class:`ClosureReadiness` whose ``ready`` is ``True``, ``False``, or ``None``
        for "nothing said". ``None`` is a refusal at the call site, not a pass.
    """
    action_id = action_node_id or (
        (_source_nodes(node_output) or (None,))[0] if node_output is not None else None
    )

    if runtime_state is not None and action_id:
        states = getattr(runtime_state, "node_states", None)
        if isinstance(states, Mapping):
            closure = _plan_upstream_closure(plan, action_id) if plan is not None else None
            if closure is not None:
                observed: Dict[str, str] = {}
                not_ready: List[str] = []
                for node_id in (str(action_id),) + tuple(closure):
                    label = str(states.get(node_id, "")) or "UNREPORTED"
                    observed[node_id] = label
                    if label != NODE_READY:
                        not_ready.append(node_id)
                return ClosureReadiness(
                    ready=not not_ready,
                    source=CLOSURE_SOURCE_PLAN_STATE,
                    action_node_id=str(action_id),
                    node_states=observed,
                    not_ready=tuple(not_ready),
                )

    reported = _flag(
        _pick(node_output, "closure_ready", "all_upstream_ready", "upstream_ready")
    )
    if reported is not None:
        return ClosureReadiness(
            ready=reported,
            source=CLOSURE_SOURCE_REPORTED_FLAG,
            action_node_id=str(action_id) if action_id else None,
        )

    readings = _closure_readings(node_output)
    if readings:
        stated: Dict[str, str] = {}
        not_ready = []
        for node_id, reading in readings.items():
            if not isinstance(reading, Mapping):
                continue
            verdict = _flag(reading.get("ready"))
            if verdict is None:
                label = _text(reading.get("status"))
                verdict = None if label is None else (label.upper() == NODE_READY)
            if verdict is None:
                continue
            stated[node_id] = NODE_READY if verdict else "NOT_READY"
            if not verdict:
                not_ready.append(node_id)
        if len(stated) == len(readings) and stated:
            return ClosureReadiness(
                ready=not not_ready,
                source=CLOSURE_SOURCE_NODE_READINGS,
                action_node_id=str(action_id) if action_id else None,
                node_states=stated,
                not_ready=tuple(not_ready),
            )

    return ClosureReadiness(
        ready=None,
        source=CLOSURE_SOURCE_NONE,
        action_node_id=str(action_id) if action_id else None,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE ACTION-NODE OUTPUT, PROJECTED ONTO WHAT TASK 10.1 READS
# ══════════════════════════════════════════════════════════════════════════

#: ``quantity_type`` values that make ``quantity`` a number of BASE UNITS - the only shape
#: ``public.signals.quantity`` can hold as a quantity. Read from ``block_specs``' own
#: vocabulary (``QUANTITY_TYPES``); every other member sizes against something this module
#: does not hold (equity, free balance, the open position, a quote notional) and therefore
#: travels as Requirement 15.2's "sizing intention" instead of as a fabricated unit count.
BASE_QUANTITY_TYPES: Tuple[str, ...] = ("base_amount",)


def action_node_output(
    intent: Any,
    *,
    plan: Any = None,
    runtime_state: Any = None,
    market_context: Optional[Mapping[str, Any]] = None,
    risk_validation: Optional[Mapping[str, Any]] = None,
    ml_inference: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """One ACTION node's evaluation, in the named-key shape :func:`mint_signal` reads.

    The adapter between the two vocabularies the runtime actually produces and the one
    projection task 10.1 accepts. Both inputs are handled by the same named reads, so
    neither the engine nor this module has to adopt the other's type:

    ``dag_engine.TradeIntent``
        The compiled-plan path's output. Its ``side`` is ``"buy"``/``"sell"`` for an entry
        block and **``None`` for an exit block** - ``action_close_position``'s side is the
        inverse of the open position and the engine refuses to guess it. So an exit intent
        is spelled ``CLOSE`` here rather than being left sideless: ``CLOSE`` is a decision
        ``public.signals.decision`` already carries, it is actionable, and
        :func:`mint_signal` then leaves ``side`` NULL - which is the honest record of "the
        position decides the side", and is exactly what 10.1's ``side`` field documents.
    ``dag_event_loop.Signal``
        The streaming loop's own emission (``action``/``strength``/``trigger_node``).
        Already readable by 10.1 unchanged; passing it through here adds the closure
        verdict and the market context without changing how it is read.

    ``quantity`` is carried as a quantity ONLY when the author sized in base units. Every
    other ``quantity_type`` (percent of equity, percent of free balance, quote notional,
    percent of position) is a rule for computing a size against state this module does not
    hold, so it travels as ``sizing_intention`` with its type named. Writing a percentage
    into a units column would be a wrong number in an audit record about real money.

    Nothing is defaulted and nothing is inferred: an intent that states no size at all
    produces neither key, and :func:`mint_signal` refuses it (``SIGNAL_SIZING_UNSPECIFIED``)
    rather than persisting a decision whose size nobody can reconstruct.
    """
    node_id = _text(_pick(intent, "node_id", "trigger_node", "action_node_id"))

    decision = _normalise_decision(_pick(intent, "decision", "action", "side"))
    if decision is None:
        # An exit block states no side by design. ``order_intent`` is the descriptor's own
        # word for what the block is for ("entry"/"exit"), so an exit with no side is a
        # CLOSE - not an unrecognised decision, and not a guessed BUY or SELL.
        order_intent = (_text(_pick(intent, "order_intent", "intent")) or "").lower()
        if order_intent == "exit":
            decision = "CLOSE"

    output: Dict[str, Any] = {
        "decision": decision,
        "symbol": _text(_pick(intent, "symbol")),
        "timeframe": _text(_pick(intent, "timeframe")),
        "source_node_ids": [node_id] if node_id else [],
    }

    strength = _number(_pick(intent, "strength", "signal", "confidence"))
    if strength is not None:
        output["strength"] = strength

    quantity_type = _text(_pick(intent, "quantity_type"))
    quantity = _number(_pick(intent, "quantity", "requested_quantity", "size"))
    if quantity is not None:
        if quantity_type is None or quantity_type in BASE_QUANTITY_TYPES:
            output["quantity"] = quantity
        else:
            output["sizing_intention"] = {
                "quantity_type": quantity_type,
                "quantity": quantity,
                "basis": "declared_by_action_node",
            }
    elif quantity_type is not None:
        output["sizing_intention"] = {
            "quantity_type": quantity_type,
            "basis": "declared_by_action_node",
        }

    context: Dict[str, Any] = {
        "bar_time": _text(_pick(intent, "bar", "bar_time", "timestamp")),
        "order_type": _text(_pick(intent, "order_type")),
        "order_intent": _text(_pick(intent, "order_intent", "intent")),
        "reduce_only": bool(_pick(intent, "reduce_only") or False),
        "declared_price": _number(_pick(intent, "price")),
        "declared_trigger_price": _number(_pick(intent, "trigger_price")),
        "declared_limit_price": _number(_pick(intent, "limit_price")),
    }
    context = {k: v for k, v in context.items() if v is not None and v is not False}
    if market_context:
        context.update({str(k): v for k, v in market_context.items()})
    output["market_context"] = context

    verdict = closure_readiness(
        intent, plan=plan, runtime_state=runtime_state, action_node_id=node_id
    )
    if verdict.ready is not None:
        output["closure_ready"] = verdict.ready
    if verdict.node_states:
        output["node_closure"] = dict(verdict.node_states)
    else:
        existing = _closure_readings(intent)
        if existing:
            output["node_closure"] = existing

    if risk_validation is not None:
        output["risk_validation"] = dict(risk_validation)
    if ml_inference is not None:
        output["ml_inference"] = dict(ml_inference)

    return {k: v for k, v in output.items() if v is not None}


def _as_output_mapping(
    node_output: Any,
    *,
    plan: Any = None,
    runtime_state: Any = None,
    market_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """``node_output`` as a mapping this section can add a risk verdict to.

    A mapping is taken as-is (copied, so the caller's dict is never mutated); anything
    else - a ``TradeIntent``, a ``dag_event_loop.Signal`` - goes through
    :func:`action_node_output`. Named reads only, in both branches: nothing iterates the
    object, which is what keeps this module's credential containment structural.
    """
    if isinstance(node_output, Mapping):
        projected = dict(node_output)
        if market_context:
            merged = dict(_dict_of(projected.get("market_context")))
            merged.update({str(k): v for k, v in market_context.items()})
            projected["market_context"] = merged
        return projected
    return action_node_output(
        node_output,
        plan=plan,
        runtime_state=runtime_state,
        market_context=market_context,
    )


def _risk_validation_record(
    verdict: RiskVerdict, *, at: str, requested_quantity: Optional[float]
) -> Dict[str, Any]:
    """The risk verdict, in the shape ``_risk_facts`` reads (Requirement 15.2).

    The component's own report is the base - it is where a ``capital``, an ``exposure`` or
    a list of ``checks`` lives if the component published one - and the four things this
    path is authoritative about are overlaid on top, so a component that happens to use
    one of those key names cannot overwrite the verdict.

    ``position_size`` is the size risk validation ACTUALLY approved: the adjusted quantity
    when it reduced one, the requested quantity otherwise. It is not the requested
    quantity dressed up as an approval - the requested one stays on the signal's own
    ``quantity`` field, and the two differing is the record of a reduction.
    """
    record: Dict[str, Any] = dict(_dict_of((verdict.detail or {}).get("report")))
    record.update(
        {
            "passed": bool(verdict.approved),
            "reason": verdict.reason,
            "position_size": (
                verdict.adjusted_quantity
                if verdict.adjusted_quantity is not None
                else requested_quantity
            ),
            "evaluated_at": at,
        }
    )
    seam = (verdict.detail or {}).get("seam")
    if seam:
        record["seam"] = seam
    return {k: v for k, v in record.items() if v is not None}


# ══════════════════════════════════════════════════════════════════════════
# RECORDING AGAINST THE DEPLOYMENT (Requirements 14.6, 14.7, 15.5)
# ══════════════════════════════════════════════════════════════════════════


async def record_deployment_failure(
    sb: Any,
    deployment: Any,
    message: str,
    *,
    code: Optional[str] = None,
    at: Optional[datetime] = None,
) -> bool:
    """Write one note against a deployment's ``error_message``. Never raises.

    Requirement 14.7's "record the failure against the Deployment" and Requirement 14.6's
    "mark the Deployment's live-data health state accordingly", both onto the column this
    platform already uses for "why is this deployment not doing what its status says" -
    see this section's header on why there and not on a new column.

    Never raises, and that is the point rather than a convenience: this is called on the
    failure path of a per-event handler, and an exception from the recorder would abort
    the very containment it is part of. A recorder that cannot write says so in the log and
    returns ``False``; the event is still contained and the next event still runs.

    Scoped by ``user_id`` as well as ``id``, matching :func:`_update_signal_row`: the
    explicit filter is the application-layer half of Requirement 20.1 and is what keeps
    the write safe if it is ever executed through a client RLS does not constrain.
    """
    facts = _deployment_facts(deployment)
    deployment_id = facts["deployment_id"]
    user_id = facts["user_id"]
    if sb is None or not deployment_id or not user_id:
        logger.warning(
            "Could not record against deployment %s (user %s): %s%s",
            deployment_id or "?",
            user_id or "?",
            f"[{code}] " if code else "",
            message,
        )
        return False

    instant = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    note = f"{DEPLOYMENT_HEALTH_PREFIX}:{code or 'FAILURE'} {instant.isoformat()} {message}"

    try:
        result = await _execute(
            sb.table("strategy_deployments")
            .update({DEPLOYMENT_HEALTH_COLUMN: note})
            .eq("id", deployment_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - the recorder is never the reason an event dies
        logger.error(
            "Could not record against deployment %s: %s (the note was: %s)",
            deployment_id,
            exc,
            note,
        )
        return False

    error = getattr(result, "error", None)
    if error is not None:
        logger.error(
            "Could not record against deployment %s: %s (the note was: %s)",
            deployment_id,
            error,
            note,
        )
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
# THE PER-DEPLOYMENT SIGNAL PATH
# ══════════════════════════════════════════════════════════════════════════


class LiveSignalPath:
    """One running Deployment's route from an ACTION-node output to a submitted order.

    Held by the Live_Runtime for the life of the deployment (see this section's header on
    why it is stateful), and driven one market event at a time:

      1. :meth:`observe_feed_state` - measure the feed, once per event, through
         ``feed_state.py``'s own classifier.
      2. :meth:`apply_feed_state` - suspend or resume generation on a TRANSITION, marking
         the deployment's live-data health state (Requirement 14.6).
      3. :meth:`on_action_output` - per triggered ACTION node: the closure gate, risk
         validation, ``generate_signal``, ``submit_signal`` (Requirements 14.3, 14.5,
         15.5).
      4. :meth:`record_evaluation_failure` - the seam a node evaluation error is reported
         through, so this event is aborted and the next one is not (Requirement 14.7).

    ``risk_engine`` and ``execution_engine`` are required at CONSTRUCTION, not per call,
    and ``None`` is refused here rather than at the first event. 10.2's header explains
    why they have no default (a defaulted validator that approves when absent would be the
    second order-placement path Requirement 11.2 forbids); checking at construction
    additionally means a mis-wired deployment fails when it is wired, not silently at the
    first trade opportunity hours later.
    """

    def __init__(
        self,
        deployment: Any,
        *,
        risk_engine: Any,
        execution_engine: Any,
        sb: Any = None,
        user: Any = None,
        idempotency_layer: Any = None,
        plan: Any = None,
        timeframe: Optional[str] = None,
        warmup_bars: Optional[int] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        facts = _deployment_facts(deployment)
        if risk_engine is None:
            raise SignalSubmissionRefused(
                "SIGNAL_RISK_ENGINE_MISSING",
                f"Deployment {facts['deployment_id']} cannot run a live signal path "
                f"without a risk validation component. Requirement 11.2 routes every "
                f"generated Signal through risk validation before any order may be "
                f"submitted, so an absent validator is a refusal, never an approval.",
                {"deployment_id": facts["deployment_id"]},
            )
        if execution_engine is None:
            raise SignalSubmissionRefused(
                "SIGNAL_EXECUTION_ENGINE_MISSING",
                f"Deployment {facts['deployment_id']} cannot run a live signal path "
                f"without the platform's order validation/execution component. "
                f"Requirement 11.2 forbids a second order-placement path that bypasses "
                f"it.",
                {"deployment_id": facts["deployment_id"]},
            )

        self.deployment = deployment
        self.deployment_id = facts["deployment_id"]
        self.user_id = facts["user_id"]
        self.mode = facts["mode"]
        self.risk_engine = risk_engine
        self.execution_engine = execution_engine
        self.sb = sb
        self.user = user
        self.idempotency_layer = idempotency_layer
        self.plan = plan
        self.timeframe = timeframe or facts["timeframe"]
        self.warmup_bars = (
            int(warmup_bars)
            if warmup_bars is not None
            else _int_or_none(_pick(plan, "warmup_bars"))
        )
        self._clock = clock

        #: The last feed verdict :meth:`apply_feed_state` acted on. ``None`` until one has
        #: been applied, and that is a REFUSAL rather than a pass - a feed nobody measured
        #: is never treated as ``LIVE`` (Requirement 14.6).
        self.feed_report: Optional[FeedStateReport] = None
        self._suspended: bool = False
        self._suspension_reason: Optional[str] = None
        #: Whether the note currently on the deployment's health column was written by this
        #: object. A resume clears only its own note - see this section's header.
        self._health_note_is_ours: bool = False

        # Counters, so a deployment can answer "how often did the feed hold me back" and
        # "how many events failed" without a log scrape. Requirement 14.7's containment is
        # only trustworthy if the containments are countable.
        self.counters: Dict[str, int] = {
            "action_outputs": 0,
            "signals_generated": 0,
            "signals_submitted": 0,
            "suppressed_feed": 0,
            "suppressed_closure": 0,
            "refused_risk": 0,
            "refused_generation": 0,
            "not_persisted": 0,
            "evaluation_failures": 0,
            "contained_errors": 0,
            "feed_suspensions": 0,
            "feed_resumes": 0,
        }

    # ── the instant ──────────────────────────────────────────────────────

    def _now(self) -> datetime:
        """The instant this path measures against, tz-aware UTC.

        Injectable for the same reason ``DAGEventLoop``'s clock is: a test must be able to
        *state* that a feed is four intervals old rather than sleep until it is.
        """
        if self._clock is not None:
            moment = self._clock()
            if moment.tzinfo is None:
                return moment.replace(tzinfo=timezone.utc)
            return moment.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    # ── the feed gate (Requirement 14.6) ────────────────────────────────

    @property
    def suspended(self) -> bool:
        """Whether signal generation is currently suspended for this deployment."""
        return self._suspended

    @property
    def suspension_reason(self) -> Optional[str]:
        return self._suspension_reason

    def observe_feed_state(
        self,
        *,
        symbol: Optional[str] = None,
        age_seconds: Optional[float] = None,
        connected: Optional[bool] = None,
        available_bars: Optional[int] = None,
        last_event_at: Optional[str] = None,
        last_event_time: Any = None,
    ) -> FeedStateReport:
        """Classify this deployment's feed. Delegated to ``feed_state.py``, never re-derived.

        The measurement can come from the caller - the Live_Runtime holds the loop that
        received the events, so it knows the newest bar's time and the window's length
        better than anything else does - or, when the caller supplies neither an age nor a
        transport state, from the platform's existing connection monitor
        (``feed_state.observe_feed``). Either way the CLASSIFICATION is
        ``evaluate_feed_state``'s: no threshold, no timer and no second idea of "stale"
        appears in this module.

        ``last_event_time`` is a convenience for the common case: a datetime, from which
        the age against :meth:`_now` is computed here so the caller does not have to.
        """
        observed_symbol = symbol or _deployment_facts(self.deployment)["symbol"]

        if age_seconds is None and last_event_time is not None:
            age_seconds = _age_seconds_between(last_event_time, self._now())
            if last_event_at is None:
                last_event_at = _text(last_event_time)

        source = OBSERVATION_SUPPLIED_LOCAL
        if age_seconds is None and connected is None:
            observation = observe_feed(observed_symbol or "")
            connected = observation.connected
            age_seconds = observation.age_seconds
            last_event_at = last_event_at or observation.last_event_at
            source = observation.source

        return evaluate_feed_state(
            timeframe=self.timeframe,
            connected=connected,
            age_seconds=age_seconds,
            available_bars=available_bars,
            warmup_bars=self.warmup_bars,
            last_event_at=last_event_at,
            observation_source=source,
            observed_at=self._now().isoformat(),
            deployment_mode=self.mode,
            detail={"deployment_id": self.deployment_id, "symbol": observed_symbol},
        )

    async def apply_feed_state(self, report: FeedStateReport) -> bool:
        """Suspend or resume generation from ``report``. Returns whether it may proceed.

        Requirement 14.6, and the whole of it: generation is suspended while the feed is
        anything other than :data:`FEED_GATE_STATE`, the deployment's live-data health
        state is marked when that happens, and generation resumes AUTOMATICALLY - no
        operator action, no restart - the first time the feed classifies ``LIVE`` again.

        The deployment row is written only on a TRANSITION. A per-event write would mean
        one UPDATE per bar for the whole duration of an outage, on a column an operator
        reads, with no new information in any of them after the first.
        """
        live = report.state is FEED_GATE_STATE
        client = await _resolve_client(self.sb, self.user, self.deployment)

        if not live and not self._suspended:
            self._suspended = True
            self._suspension_reason = report.display
            self.counters["feed_suspensions"] += 1
            logger.warning(
                "Deployment %s: signal generation SUSPENDED - feed state is %s (%s). %s "
                "Generation resumes automatically once the feed classifies %s again.",
                self.deployment_id,
                report.state.value,
                report.reason,
                report.display,
                FEED_GATE_STATE.value,
            )
            self._health_note_is_ours = await record_deployment_failure(
                client,
                self.deployment,
                f"signal generation suspended: feed state {report.state.value} "
                f"({report.reason}). {report.display}",
                code="FEED_NOT_LIVE",
                at=self._now(),
            )
        elif live and self._suspended:
            self._suspended = False
            self._suspension_reason = None
            self.counters["feed_resumes"] += 1
            logger.info(
                "Deployment %s: signal generation RESUMED - feed state is %s again (%s).",
                self.deployment_id,
                report.state.value,
                report.display,
            )
            if self._health_note_is_ours:
                # Only our own note is cleared. Clearing unconditionally would erase a
                # startup failure reason (Requirement 11.4) or another worker's message
                # the first time a feed recovered.
                await _clear_deployment_health_note(client, self.deployment)
                self._health_note_is_ours = False

        self.feed_report = report
        return live

    # ── the per-event route ─────────────────────────────────────────────

    async def on_action_output(
        self,
        node_output: Any,
        *,
        plan: Any = None,
        runtime_state: Any = None,
        action_node_id: Optional[str] = None,
        market_context: Optional[Mapping[str, Any]] = None,
        feed: Optional[FeedStateReport] = None,
        now: Optional[datetime] = None,
    ) -> SignalPathOutcome:
        """Route one triggered ACTION node's output. Returns an outcome, never raises.

        THE ORDER OF THE GATES IS THE REQUIREMENT
            feed, then closure, then risk, then generate, then submit. Each one is placed
            where it is because of what it costs to be wrong at that point:

            * **feed** first (Requirement 14.6). A stale feed makes every later reading
              suspect, including the risk component's own view of the market, and the whole
              evaluation is void rather than merely unsized.
            * **closure** second (Requirement 14.3). Cheap, local, and the fact that
              decides whether this decision exists at all.
            * **risk** third (Requirement 14.3's "and the risk validation passes"). It is
              the last thing that can refuse a candidate BEFORE an identifier is minted
              and a row is written, and a refused candidate should leave no signal row -
              its refusal belongs on the deployment, not on a signal.
            * **generate** fourth (Requirements 14.5, 15.5). Persist, then report.
              ``generate_signal`` returns a ``Signal`` only when a row exists.
            * **submit** last (Requirement 11.2). Task 10.2's function, unchanged.

        WHY EVERY EXIT IS A RETURN
            Requirement 14.7: "a node evaluation error on one event SHALL NOT halt the
            Live_Runtime's processing of later events". An exception escaping here would
            put that guarantee in the event loop's ``except`` clause, where a contained
            failure and a genuine bug are indistinguishable. So this method contains, and
            the caller reads the outcome.

        ``SignalPersistenceError`` specifically is recorded against the deployment and
        returned as :data:`OUTCOME_NOT_PERSISTED` - the signal is NOT reported and NOT
        routed onwards (Requirement 15.5), and the next event still runs (14.7, 14.8).
        """
        self.counters["action_outputs"] += 1
        instant = now or self._now()
        effective_plan = plan if plan is not None else self.plan

        try:
            # 1. THE FEED (Requirement 14.6).
            if feed is not None:
                may_generate = await self.apply_feed_state(feed)
            elif self.feed_report is None:
                # Nothing has ever measured this deployment's feed, so nothing can say the
                # event was not stale or gapped. ``feed_state`` fails closed for exactly
                # this ("a feed nobody has measured is never reported LIVE") and so does
                # the decision it informs.
                self.counters["suppressed_feed"] += 1
                return SignalPathOutcome(
                    status=OUTCOME_FEED_SUSPENDED,
                    code="FEED_STATE_UNMEASURED",
                    reason=(
                        "no feed state has been measured for this deployment, so this "
                        "event cannot be shown to have come from a live feed; no Signal "
                        "is generated (Requirement 14.6)"
                    ),
                    detail={"deployment_id": self.deployment_id},
                )
            else:
                may_generate = not self._suspended

            if not may_generate:
                self.counters["suppressed_feed"] += 1
                report = self.feed_report
                return SignalPathOutcome(
                    status=OUTCOME_FEED_SUSPENDED,
                    code="FEED_NOT_LIVE",
                    reason=(
                        f"signal generation is suspended: feed state is "
                        f"{report.state.value if report else 'unknown'}, not "
                        f"{FEED_GATE_STATE.value}"
                    ),
                    detail={
                        "deployment_id": self.deployment_id,
                        "feed_state": report.to_dict() if report else None,
                    },
                )

            projected = _as_output_mapping(
                node_output,
                plan=effective_plan,
                runtime_state=runtime_state,
                market_context=market_context,
            )

            # 2. THE CLOSURE (Requirement 14.3).
            verdict = closure_readiness(
                projected if isinstance(node_output, Mapping) else node_output,
                plan=effective_plan,
                runtime_state=runtime_state,
                action_node_id=action_node_id
                or (_source_nodes(projected) or (None,))[0],
            )
            if verdict.ready is not True:
                self.counters["suppressed_closure"] += 1
                return SignalPathOutcome(
                    status=OUTCOME_CLOSURE_NOT_READY,
                    code=(
                        "CLOSURE_NOT_READY"
                        if verdict.ready is False
                        else "CLOSURE_READINESS_UNDETERMINED"
                    ),
                    reason=(
                        f"not every upstream node in action node "
                        f"{verdict.action_node_id or '?'}'s closure is {NODE_READY} "
                        f"({', '.join(verdict.not_ready) or 'readiness was not reported'}), "
                        f"so no Signal is generated (Requirement 14.3)"
                    ),
                    detail={
                        "deployment_id": self.deployment_id,
                        "closure": verdict.to_dict(),
                    },
                )
            projected["closure_ready"] = True
            if verdict.node_states and not projected.get("node_closure"):
                projected["node_closure"] = dict(verdict.node_states)

            # 3. RISK VALIDATION, on the candidate, before any id is minted.
            #    The candidate is minted here - purely, no I/O - because the risk
            #    component is handed a Signal and because minting is what turns a node
            #    output into one. It is NOT persisted: `generate_signal` below persists,
            #    and it is given this same id so one decision consumes one identifier
            #    rather than two.
            try:
                candidate = mint_signal(self.deployment, projected, now=instant)
            except SignalGenerationRefused as refusal:
                self.counters["refused_generation"] += 1
                logger.info(
                    "Deployment %s: no Signal for this event - %s (%s)",
                    self.deployment_id,
                    refusal.message,
                    refusal.code,
                )
                return SignalPathOutcome(
                    status=OUTCOME_GENERATION_REFUSED,
                    code=refusal.code,
                    reason=refusal.message,
                    detail={"deployment_id": self.deployment_id, **refusal.details},
                )

            risk_verdict = await _call_risk_engine(self.risk_engine, candidate)
            risk_record = _risk_validation_record(
                risk_verdict, at=instant.isoformat(), requested_quantity=candidate.quantity
            )
            if not risk_verdict.approved:
                self.counters["refused_risk"] += 1
                reason = (
                    f"risk validation refused the candidate: "
                    f"{risk_verdict.reason or 'no reason given'}"
                )
                logger.info("Deployment %s: %s", self.deployment_id, reason)
                # Requirement 14.3 - no Signal is generated. The refusal belongs on the
                # deployment's own record, not on a signal row that would claim a decision
                # this platform never took.
                await record_deployment_failure(
                    await _resolve_client(self.sb, self.user, self.deployment),
                    self.deployment,
                    reason,
                    code="RISK_REFUSED_CANDIDATE",
                    at=instant,
                )
                return SignalPathOutcome(
                    status=OUTCOME_RISK_REFUSED,
                    code="RISK_REFUSED_CANDIDATE",
                    reason=reason,
                    detail={
                        "deployment_id": self.deployment_id,
                        "risk_validation": risk_record,
                    },
                )

            projected["risk_validation"] = risk_record

            # 4. GENERATE: mint, persist, return - only then does a Signal exist
            #    (Requirements 14.5, 15.2, 15.5).
            signal = await generate_signal(
                self.deployment,
                projected,
                sb=self.sb,
                user=self.user,
                now=instant,
                signal_id=candidate.id,
            )
            self.counters["signals_generated"] += 1

            # 5. SUBMIT: task 10.2's function, called with the two components this path
            #    holds. Requirement 11.2's one route to a venue.
            state = await submit_signal(
                signal,
                risk_engine=self.risk_engine,
                execution_engine=self.execution_engine,
                sb=self.sb,
                user=self.user,
                idempotency_layer=self.idempotency_layer,
            )
            self.counters["signals_submitted"] += 1
            return SignalPathOutcome(
                status=OUTCOME_SUBMITTED,
                signal=signal.with_order_lifecycle_state(state),
                order_lifecycle_state=state,
                reason=f"submitted; reached {state.value}",
                detail={
                    "deployment_id": self.deployment_id,
                    "idempotency_key": signal.idempotency_key,
                },
            )

        except SignalPersistenceError as failure:
            # Requirement 15.5 and 14.8. The signal is not reported and not routed; the
            # failure is recorded through the platform's existing error-handling path so
            # the missed Signal can be investigated, and the next event still runs.
            self.counters["not_persisted"] += 1
            logger.error(
                "Deployment %s: a signal was NOT persisted and therefore NOT routed - %s",
                self.deployment_id,
                failure.message,
            )
            await record_deployment_failure(
                await _resolve_client(self.sb, self.user, self.deployment),
                self.deployment,
                failure.message,
                code=failure.code,
                at=instant,
            )
            return SignalPathOutcome(
                status=OUTCOME_NOT_PERSISTED,
                code=failure.code,
                reason=failure.message,
                detail={"deployment_id": self.deployment_id, **failure.details},
            )
        except SignalRejected as refusal:
            # A classified refusal from anywhere further down: a missing or unusable
            # component (SignalSubmissionRefused), an illegal transition. Contained for the
            # same reason as everything else here, and recorded, because a wiring problem
            # that silently produces no trades is the worst of the three outcomes.
            self.counters["contained_errors"] += 1
            logger.error(
                "Deployment %s: signal path refused this event - %s (%s)",
                self.deployment_id,
                refusal.message,
                refusal.code,
            )
            await record_deployment_failure(
                await _resolve_client(self.sb, self.user, self.deployment),
                self.deployment,
                refusal.message,
                code=refusal.code,
                at=instant,
            )
            return SignalPathOutcome(
                status=OUTCOME_CONTAINED_ERROR,
                code=refusal.code,
                reason=refusal.message,
                detail={"deployment_id": self.deployment_id, **refusal.details},
            )
        except Exception as exc:  # noqa: BLE001 - Requirement 14.7's containment, literally
            self.counters["contained_errors"] += 1
            logger.exception(
                "Deployment %s: this event's signal generation was aborted by an "
                "unexpected error; later events continue (Requirement 14.7).",
                self.deployment_id,
            )
            await record_deployment_failure(
                await _resolve_client(self.sb, self.user, self.deployment),
                self.deployment,
                f"signal generation aborted for one event: {exc}",
                code="SIGNAL_PATH_ERROR",
                at=instant,
            )
            return SignalPathOutcome(
                status=OUTCOME_CONTAINED_ERROR,
                code="SIGNAL_PATH_ERROR",
                reason=str(exc),
                detail={
                    "deployment_id": self.deployment_id,
                    "error": type(exc).__name__,
                },
            )

    async def on_action_outputs(
        self,
        node_outputs: Iterable[Any],
        *,
        plan: Any = None,
        runtime_state: Any = None,
        market_context: Optional[Mapping[str, Any]] = None,
        feed: Optional[FeedStateReport] = None,
        now: Optional[datetime] = None,
    ) -> List[SignalPathOutcome]:
        """Route every triggered ACTION node of ONE event, in order, each one contained.

        A plan may hold several ACTION nodes and one bar can trigger more than one of them.
        Each is routed independently and one failing does not skip the rest: Requirement
        14.7's containment is per event, and losing the second action of a bar because the
        first met a database error would be a narrower guarantee than the requirement's.
        """
        outcomes: List[SignalPathOutcome] = []
        applied = feed
        for index, node_output in enumerate(node_outputs):
            outcomes.append(
                await self.on_action_output(
                    node_output,
                    plan=plan,
                    runtime_state=runtime_state,
                    market_context=market_context,
                    # The feed verdict is applied ONCE per event, not once per action: the
                    # second application would be a no-op on the state machine but would
                    # count a suspension twice and log a resume that already happened.
                    feed=applied if index == 0 else None,
                    now=now,
                )
            )
        return outcomes

    async def record_evaluation_failure(
        self,
        error: BaseException,
        *,
        stage: str = "node evaluation",
        symbol: Optional[str] = None,
        event_time: Any = None,
        now: Optional[datetime] = None,
    ) -> SignalPathOutcome:
        """Requirement 14.7's other half: an evaluation that raised before any output.

        Node-state update, indicator evaluation, ML/DL evaluation and logic/action
        evaluation all happen inside ``DAGEngine.execute_plan``, which raises rather than
        returning a verdict - deliberately, because an intent whose numbers cannot be
        checked must not be sent. So the Live_Runtime catches it and reports it here: the
        failure is recorded against the Deployment, no Signal is generated for this event,
        and the runtime goes on to the next one.
        """
        self.counters["evaluation_failures"] += 1
        message = f"{stage} failed for one market event: {error}"
        logger.error(
            "Deployment %s: %s. No Signal for this event; later events continue "
            "(Requirement 14.7).",
            self.deployment_id,
            message,
        )
        await record_deployment_failure(
            await _resolve_client(self.sb, self.user, self.deployment),
            self.deployment,
            message,
            code="NODE_EVALUATION_FAILED",
            at=now or self._now(),
        )
        return SignalPathOutcome(
            status=OUTCOME_NODE_EVALUATION_FAILED,
            code="NODE_EVALUATION_FAILED",
            reason=message,
            detail={
                "deployment_id": self.deployment_id,
                "stage": stage,
                "symbol": symbol,
                "event_time": _text(event_time),
                "error": type(error).__name__,
            },
        )

    # ── observability ───────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """What this path has done and what is currently holding it back.

        Carries no credential and no exchange identity for the same structural reason
        :meth:`Signal.to_public_dict` does not: the only account-naming value anywhere on
        this object is the deployment's own internal reference, and that is not published
        here at all.
        """
        return {
            "deployment_id": self.deployment_id,
            "mode": self.mode,
            "timeframe": self.timeframe,
            "warmup_bars": self.warmup_bars,
            "suspended": self._suspended,
            "suspension_reason": self._suspension_reason,
            "feed_state": self.feed_report.to_dict() if self.feed_report else None,
            "counters": dict(self.counters),
        }


def _int_or_none(value: Any) -> Optional[int]:
    """``value`` as an int, or ``None``. Never raises."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _age_seconds_between(moment: Any, now: datetime) -> Optional[float]:
    """Seconds from ``moment`` to ``now``, or ``None`` when it cannot be measured.

    Timezone-naive input is read as UTC, which is what every timestamp on this path
    already is (``market_data_contract`` normalises to naive UTC and ``generated_at`` is
    ISO-8601 UTC). A negative age - an event stamped in the future - comes back ``None``
    rather than clamped to zero: ``feed_state.classify_age`` treats an unmeasurable age as
    ``STALE``, and a clock problem must not be able to certify a feed as fresh.
    """
    if moment is None:
        return None
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(moment, datetime):
        # A pandas Timestamp, which is a datetime subclass, lands above. Anything else
        # (a numpy datetime64, an int epoch) is not guessed at.
        to_pydatetime = getattr(moment, "to_pydatetime", None)
        if not callable(to_pydatetime):
            return None
        try:
            moment = to_pydatetime()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(moment, datetime):
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age = (now - moment.astimezone(timezone.utc)).total_seconds()
    return age if age >= 0 else None


async def _clear_deployment_health_note(sb: Any, deployment: Any) -> bool:
    """Clear the deployment's health column. Only ever called for our own note."""
    facts = _deployment_facts(deployment)
    if sb is None or not facts["deployment_id"] or not facts["user_id"]:
        return False
    try:
        result = await _execute(
            sb.table("strategy_deployments")
            .update({DEPLOYMENT_HEALTH_COLUMN: None})
            .eq("id", facts["deployment_id"])
            .eq("user_id", facts["user_id"])
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - a resume is never failed by its own bookkeeping
        logger.warning(
            "Could not clear deployment %s's live-data health note: %s",
            facts["deployment_id"],
            exc,
        )
        return False
    return getattr(result, "error", None) is None


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 12 - the crash-recovery sweep (Requirement 19.2)
#
#  Spec: trading-lifecycle-integration. Requirement 19.2, exercised by the
#  regression suite Requirement 19.4 mandates (tests/crash_recovery/).
#
#  WHY THIS SECTION EXISTS AT ALL
#  ------------------------------
#  Task 10.2's header names it as the one thing it deliberately does not own:
#  "the crash-recovery sweep itself (query the exchange by Idempotency_Key on
#  restart, bounded to 3 attempts in 30s) - Requirement 19.2, task 12". It is
#  also the other half of the disposition that same header defends: submit_signal
#  writes SUBMITTED only AFTER the execution call returns, which leaves a signal
#  at PENDING with a possibly-live order when a worker dies mid-call, and it
#  accepts that cost precisely BECAUSE that state is recoverable. This is the
#  code that recovers it. Without it, "a false PENDING costs a lookup" is a claim
#  with nothing behind it.
#
#  WHAT REQUIREMENT 19.2 ASKS FOR, CLAUSE BY CLAUSE, AND WHERE EACH ONE LANDS
#  -------------------------------------------------------------------------
#    "upon restart and BEFORE taking any further action on that Signal"
#        :func:`recover_signal` is a precondition, not a repair job run
#        afterwards. It returns :attr:`RecoveryOutcome.resubmission_allowed`,
#        and that flag - not the caller's judgement - is what says whether
#        submit_signal may run. :func:`recover_in_flight_signals` is the sweep
#        shape for the plural case (every non-terminal signal of a deployment
#        this worker is resuming).
#
#    "query the exchange for the order matching that Signal's Idempotency_Key
#     wherever the exchange supports lookup by client order identifier"
#        The key is ``idempotency_key_for(signal)`` - the SAME derivation the
#        submission used, by construction rather than by convention, which is
#        the whole point of it being a pure function of ``signal.id``. The
#        lookup seam is duck-typed (:func:`_exchange_lookup_seam`) for the same
#        reason the risk and execution seams are: the live object here is a
#        per-deployment exchange client, not a module singleton.
#
#    "SHALL treat the persisted Order_Lifecycle_State as authoritative when the
#     exchange is unreachable or does not support such a lookup"
#        Nothing is written in either case. The persisted state is read once, at
#        the top, through ``_persisted_state_for_recovery`` (the row, never this
#        process's memory - a process that just restarted has none), and it is
#        what the outcome reports.
#
#    "bound this exchange-state check to a maximum of 3 attempts within 30
#     seconds total"
#        Both bounds, both enforced, and the SECOND one is the one that is easy
#        to get wrong: the budget is re-checked before every attempt and before
#        every wait, and a wait is shortened to whatever is left rather than
#        allowed to overrun it. So three slow lookups cannot become 90 seconds
#        of a restarting worker refusing to do anything else.
#
#    "SHALL NOT resubmit an order for a Signal whose Idempotency_Key was already
#     used"
#        ``resubmission_allowed`` is True in exactly ONE case: the lookup
#        succeeded and definitively reported NO order for this key. Every other
#        path - an order found, an unreachable exchange, a lookup the venue does
#        not support, an exhausted budget - is False. "We could not find out" is
#        never "it is safe to send another order".
#
#    "IF the exchange-state check exhausts its attempts without a definitive
#     answer, THEN mark that Signal's Order_Lifecycle_State as requiring manual
#     reconciliation rather than resubmitting the order"
#        :func:`mark_manual_reconciliation_required`, and read back with
#        :func:`manual_reconciliation_marked`.
#
#  THE SWEEP READS THE RECORD OR REPORTS NOTHING
#  --------------------------------------------
#  THIS SUPERSEDES the original reliance on load_current_order_lifecycle_state's
#  fallback. That function, when the row cannot be read, returns the CALLER's
#  in-memory signal.order_lifecycle_state. For its two original callers (both
#  inside submit_signal, both holding a Signal this process itself just persisted)
#  that is defensible and is left exactly as it was. For this sweep it is not, and
#  recover_signal's own docstring already said why: "a sweep that cannot read that
#  state has no authority to report on this signal at all". It enforced that for a
#  missing CLIENT and not for a failed READ, and a database or queue restart
#  produces the second far more often than the first. The in-memory value a
#  restart-era caller holds is the GENERATED the object was minted with - the
#  "earlier, superseded state" Requirement 19.3 names by name - and two things
#  followed from believing it:
#
#    * the sweep re-derived the route from GENERATED and walked
#      GENERATED -> PENDING -> SUBMITTED over a record already at PENDING,
#      appending a SECOND "GENERATED -> PENDING" row so the history stopped being
#      one chain (Requirement 19.4's assertion (c));
#    * _no_definitive_answer computed "could this have a live order" from that
#      GENERATED and reported requires_manual_reconciliation=False for a signal
#      with a live order at the venue, defeating Requirement 19.2's marking clause.
#
#  So the sweep reads the state through ``_persisted_state_for_recovery``, which has
#  TWO persisted sources and no third:
#
#    1. public.signals.order_lifecycle_state, read STRICTLY - result.error is now
#       inspected (a refused SELECT answers with an error rather than raising, and
#       reading that as "no row" is how it used to reach the fallback), a missing
#       row is a failed read, and unreconcilable legacy columns are a failed read.
#    2. order_lifecycle_transitions' newest to_state - the same fact, written by
#       apply_order_lifecycle_state immediately after the column, in the same call.
#
#  Neither is process memory, which is the property that matters. The residual cost
#  of source 2 is stated rather than hidden: the log can LAG the column by one hop,
#  because the column is written first and Requirement 16.7 explicitly tolerates an
#  unavailable audit store. So a sweep that falls back to the log can act on a state
#  one hop behind - and it can only ever move FORWARD from there (lifecycle_path
#  finds no backward route), which is why that cost is a redundant write and not a
#  Requirement 19.3 violation, except in the narrow case where the column is ahead,
#  the log lagged, AND the row is unreadable at the same moment. That combination is
#  logged as an error when it happens.
#
#  WHEN NEITHER SOURCE ANSWERS, THE STATE IS UNKNOWN - AND UNKNOWN IS NOT GENERATED
#  ------------------------------------------------------------------------------
#  An unknown state is never terminal (not knowing is not finishing), so the
#  exchange is still queried - the query has no side effects and Requirement 19.2
#  mandates it - and then:
#
#    * the venue could not answer either: _no_definitive_answer, with
#      could_have_live_order TRUE, because an unreadable record rules out KNOWING,
#      not a live order. The signal is marked (or the marker's own failure is
#      logged), nothing is written, nothing is resubmitted.
#    * the venue DID answer: refuse, with SIGNAL_STATE_NOT_READABLE. There is no
#      from_state to gate a transition against and no verdict that would not be
#      this process's memory. Raised rather than returned so no caller can read it
#      as a verdict; recover_in_flight_signals contains it per signal.
#
#  HOW A SIGNAL IS "MARKED AS REQUIRING MANUAL RECONCILIATION" WITHOUT A TENTH
#  STATE - THE ONE INTERPRETATION IN THIS SECTION WORTH ARGUING WITH
#  --------------------------------------------------------------------------
#  Requirement 16.1 fixes the Order_Lifecycle_State vocabulary at exactly 9
#  values and migration 005b's chk_signals_order_lifecycle_state enforces that
#  list in the database. There is no NEEDS_RECONCILIATION among them, and adding
#  one would be inventing a vocabulary value and a migration that no task in this
#  plan authorises. Nor may the state be MOVED: the signal's true state is
#  unknown, so writing anything over it would be either a fabricated fact or
#  exactly the "overwritten with an earlier state" Requirement 19.3 forbids.
#
#  So the mark is a RECORD ABOUT the state rather than a value OF it: one row in
#  ``public.signal_events`` (which 002_signal_trace.sql already provides, with
#  ``event_type VARCHAR(50)`` and ``event_data JSONB``, and which design.md
#  settles as authoritative), event_type MANUAL_RECONCILIATION_REQUIRED, carrying
#  the Idempotency_Key, the state the row is stuck at, how many attempts were
#  made and why they were not definitive. The state column keeps saying what was
#  last known to be true, and the event says nobody may act on it automatically.
#  That is the strongest reading available without inventing schema, and it is
#  stated here rather than buried so a reviewer can disagree with it in one place.
#
#  Two consequences, both deliberate:
#    * The marker is IDEMPOTENT. A worker that restarts twice, or three workers
#      resuming the same deployment, must not append three markers for one
#      signal - so an existing marker is looked for first, exactly as
#      ``anchor_generated_transition`` does for the genesis row.
#    * A FAILURE TO WRITE THE MARKER DOES NOT FAIL THE SWEEP. The guarantee that
#      matters most in Requirement 19.2 is "rather than resubmitting the order",
#      and that one is carried by the returned outcome, in memory, with no
#      database dependency at all. Losing the marker loses an operator's
#      to-do item; raising here would instead leave the caller with no verdict,
#      and a caller with no verdict is a caller that might resubmit.
#
#  A LOOKUP THE VENUE DOES NOT SUPPORT IS ALSO MARKED, WHICH THE REQUIREMENT
#  DOES NOT LITERALLY SAY
#  ------------------------------------------------------------------------
#  Read literally, the manual-reconciliation clause is conditioned on EXHAUSTING
#  attempts, and a venue with no client-order-id lookup exhausts nothing - there
#  was never an attempt to make. But the resulting situation is identical and
#  worse: a signal sits at PENDING or SUBMITTED, an order may or may not be live
#  behind it, resubmission is forbidden, and no automated path can ever resolve
#  it. Leaving that unmarked would strand it silently. So the marker is written
#  whenever the answer was not definitive AND the signal could still have a live
#  order (its persisted state is past GENERATED and not terminal) - which covers
#  the requirement's case and this one, and covers neither a signal that never
#  reached submission nor one that already finished.
#
#  WHAT THIS SECTION DOES NOT DO
#  -----------------------------
#  * It does not submit, cancel, amend or resubmit anything. It reads the
#    exchange and reconciles the record forward. Every write it makes goes
#    through ``apply_order_lifecycle_state`` - the same gate-then-write-then-audit
#    path as every other transition - so a recovery cannot write a state the
#    transition table forbids, and every state it does write appears in the
#    Requirement 16.7 history with a reason naming the sweep.
#  * It never moves a state BACKWARDS. A stale exchange read that reports an
#    order as open when the row already says EXECUTED produces no write at all
#    (``lifecycle_path`` returns no route), which is Requirement 19.3's
#    "SHALL NOT be overwritten with an earlier state" holding structurally.
#  * It publishes no frame OF ITS OWN, and it does not decide which signals a
#    restarting worker should sweep - that is the Live_Runtime's own startup,
#    which hands this function the rows it read. Since task 14.2 hung the
#    STATUS_CHANGED publish off ``apply_order_lifecycle_state`` (the one write
#    path), a recovery that DOES write a state announces it there, on the same
#    channel and with the same reason the history row carries - which is the
#    point of there being one write path rather than two.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


from backend_app.backend.order_lifecycle_state import is_terminal  # noqa: E402

#: Requirement 19.2's hard ceiling on the exchange-state check.
RECOVERY_MAX_ATTEMPTS = 3

#: Requirement 19.2's other hard ceiling. Wall-clock seconds for the WHOLE check,
#: including the waits between attempts, not per attempt.
RECOVERY_TOTAL_BUDGET_SECONDS = 30.0

#: The wait between attempts. Chosen so three attempts spread across a transient
#: outage (worst case ~10s of waiting) instead of firing three times in a
#: millisecond and calling the venue unreachable - while still leaving the
#: 30-second budget most of its room for the lookups themselves.
RECOVERY_RETRY_DELAY_SECONDS = 5.0

#: ``public.signal_events``, from 002_signal_trace.sql. The manual-reconciliation
#: marker's home - see this section's header on why the marker is an event rather
#: than a tenth Order_Lifecycle_State.
SIGNAL_EVENTS_TABLE = "signal_events"

#: The one ``signal_events.event_type`` this section writes.
MANUAL_RECONCILIATION_EVENT = "MANUAL_RECONCILIATION_REQUIRED"

#: The recovery statuses. Published as constants so a caller (and a test) names
#: the outcome instead of matching a message.
#:
#: Nothing to recover: the signal is already in a terminal Order_Lifecycle_State.
RECOVERY_TERMINAL = "TERMINAL"
#: The exchange definitively reported no order for this Idempotency_Key. The key
#: was never used, so submission may proceed under it.
RECOVERY_NO_ORDER_AT_EXCHANGE = "NO_ORDER_AT_EXCHANGE"
#: An order exists and the persisted state already matched it. Nothing written.
RECOVERY_ALREADY_CONSISTENT = "ALREADY_CONSISTENT"
#: An order exists and the persisted state was moved forward to match it.
RECOVERY_RECONCILED = "RECONCILED"
#: The venue offers no lookup by client order identifier, so the persisted state
#: stands as authoritative (Requirement 19.2) and nothing is resubmitted.
RECOVERY_PERSISTED_STATE_AUTHORITATIVE = "PERSISTED_STATE_AUTHORITATIVE"
#: The bounded check ended without a definitive answer. The signal is marked and
#: left alone.
RECOVERY_MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"

#: The lookup seams this sweep knows how to call, most specific first. A venue
#: object exposing none of them does not support lookup by client order id, which
#: Requirement 19.2 names explicitly as a case to handle rather than an error.
EXCHANGE_LOOKUP_SEAMS: Tuple[str, ...] = (
    "fetch_order_by_client_order_id",
    "fetch_order_by_idempotency_key",
    "find_order_by_client_order_id",
    "get_order_by_client_order_id",
)


@dataclass(frozen=True)
class RecoveryOutcome:
    """What the crash-recovery sweep established about one signal.

    Returned, never raised, for the same reason :class:`SignalPathOutcome` is: a
    restarting worker asks about many signals and one unanswerable question must not
    abort the rest of the restart.

    ``resubmission_allowed`` is the field with teeth. It is ``True`` only when the
    exchange was asked and definitively answered that no order exists for this
    signal's Idempotency_Key. A caller that ignores it and submits anyway is the
    duplicate-order bug Requirement 19.2 exists to prevent, which is why the flag is
    stated here rather than left to be inferred from ``status``.
    """

    status: str
    #: The signal's Order_Lifecycle_State when the sweep finished - reconciled where
    #: the exchange had something to say, and the persisted value otherwise. ``None``
    #: when the record could not be read at all: this field is the sweep reporting what
    #: the DATABASE says, so there is nothing honest to put here when the database did
    #: not answer, and substituting the caller's own in-memory value is the mistake
    #: Requirement 19.3 names ("re-deriving it from an earlier, superseded state").
    order_lifecycle_state: Optional[OrderLifecycleState]
    resubmission_allowed: bool
    #: How many exchange lookups were actually made. ``0`` when no lookup was
    #: possible or none was needed.
    attempts: int = 0
    #: Whether this signal now needs a human. Independent of ``status`` so a caller
    #: can page on it without knowing which non-definitive path produced it.
    requires_manual_reconciliation: bool = False
    #: Whether the marker row was actually written (or already existed). ``False``
    #: with ``requires_manual_reconciliation`` ``True`` means the mark could not be
    #: recorded - the refusal to resubmit still holds; the operator's to-do item was
    #: lost, and it is logged as an error.
    marked: bool = False
    #: The state the exchange reported, where it reported one.
    exchange_state: Optional[OrderLifecycleState] = None
    #: The exchange's own order identifier, where one exists.
    exchange_order_id: Optional[str] = None
    reason: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def order_exists_at_exchange(self) -> bool:
        """Whether the sweep positively saw an order for this Idempotency_Key."""
        return self.exchange_state is not None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "order_lifecycle_state": (
                self.order_lifecycle_state.value
                if self.order_lifecycle_state is not None
                else None
            ),
            "resubmission_allowed": self.resubmission_allowed,
            "attempts": self.attempts,
            "requires_manual_reconciliation": self.requires_manual_reconciliation,
            "marked": self.marked,
            "exchange_state": (
                self.exchange_state.value if self.exchange_state is not None else None
            ),
            "exchange_order_id": self.exchange_order_id,
            "reason": self.reason,
            "detail": dict(self.detail or {}),
        }


def _exchange_lookup_seam(exchange: Any) -> Optional[Callable[[str], Any]]:
    """The venue's lookup-by-client-order-id seam, or ``None`` when it has none.

    Requirement 19.2 conditions the query on the venue supporting lookup by client
    order identifier ("wherever the exchange supports"), so the absence of a seam is
    a supported answer and not a wiring error - unlike the risk and execution seams,
    where an absent seam is a refusal because submitting without them is forbidden.

    An explicit ``supports_client_order_id_lookup`` attribute, where the object has
    one, is honoured even if a seam is present: a client that knows the venue cannot
    answer this question is more authoritative than the presence of a method.
    """
    if exchange is None:
        return None
    supports = getattr(exchange, "supports_client_order_id_lookup", None)
    if supports is not None and not bool(supports):
        return None
    for name in EXCHANGE_LOOKUP_SEAMS:
        seam = getattr(exchange, name, None)
        if callable(seam):
            return seam
    return None


def _exchange_reported_state(report: Any, signal: Signal) -> OrderLifecycleState:
    """The Order_Lifecycle_State an exchange order report describes.

    Reconciled through ``order_lifecycle_state``'s own ``ORDER_STATE_MAP`` where the
    venue's word belongs to that vocabulary, and through the fill arithmetic
    otherwise - the same two steps, in the same order, that
    :func:`resolve_execution_state` already applies to an execution report, reused
    rather than re-derived so a lookup and a submission cannot disagree about the
    same order.

    A report this function cannot read AT ALL still means an order exists - the venue
    returned a record for this Idempotency_Key - so the floor is ``SUBMITTED``, never
    ``FAILED``. Reading "I found your order but cannot parse it" as a failure would
    be the one mistake that strands a live order in a terminal state.
    """
    raw = normalise_source_value(
        _pick(report, "order_state", "status", "state", "order_status")
    )
    order_state: Optional[str] = None
    if raw is not None:
        try:
            if map_order_state(raw) is not None:
                order_state = raw
        except UnknownSourceStatus:
            order_state = None

    filled = _number(_pick(report, "filled", "filled_quantity", "executed_quantity"))
    quantity = _number(
        _pick(report, "quantity", "amount", "size", "requested_quantity")
    )
    return resolve_execution_state(
        ExecutionOutcome(
            accepted=True,
            refused=False,
            order_state=order_state,
            filled=filled,
            quantity=quantity if quantity is not None else signal.quantity,
        )
    )


def _exchange_order_id(report: Any) -> Optional[str]:
    """The venue's own identifier for the order it reported."""
    return _text(_pick(report, "order_id", "id", "exchange_order_id"))


async def latest_persisted_transition_state(
    sb: Any, signal: Signal
) -> Optional[OrderLifecycleState]:
    """The last ``to_state`` in ``signal``'s append-only transition history, or ``None``.

    The SECOND persisted record of the same fact, and the sweep's only fallback when the
    canonical column cannot be read (see :func:`_persisted_state_for_recovery`). It is a
    read of the database, not of this process - which is the whole property that matters
    here - because ``apply_order_lifecycle_state`` writes the column first and appends this
    row immediately afterwards, in the same call.

    Ordered by ``occurred_at`` and resolved with the returned order as the tie-breaker, so
    the newest row wins whether or not the client honours the ordering hint. Returns
    ``None`` for an absent log, an unreadable one, and a signal with no rows.
    """
    if sb is None or not _transitions_table_available():
        return None
    try:
        result = await _execute(
            sb.table(ORDER_LIFECYCLE_TRANSITIONS_TABLE)
            .select("to_state,occurred_at")
            .eq("signal_id", signal.id)
            .eq("user_id", signal.user_id)
            .order("occurred_at")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        if is_missing_transitions_table_error(exc):
            remember_transitions_table_absent()
            warn_transitions_table_absent(str(exc))
        else:
            logger.warning(
                "Could not read signal %s's transition history to establish its persisted "
                "Order_Lifecycle_State (%s).",
                signal.id,
                exc,
            )
        return None
    if getattr(result, "error", None) is not None:
        logger.warning(
            "Signal %s's transition history could not be read (%s), so it cannot stand in "
            "for the unreadable canonical column.",
            signal.id,
            getattr(result, "error", None),
        )
        return None

    data = getattr(result, "data", None)
    rows = data if isinstance(data, list) else ([data] if data else [])
    latest: Optional[OrderLifecycleState] = None
    latest_at: Optional[str] = None
    for row in rows:
        state = normalise_lifecycle_state(_pick(row, "to_state"))
        if state is None:
            continue
        occurred_at = _text(_pick(row, "occurred_at")) or ""
        if latest_at is None or occurred_at >= latest_at:
            latest, latest_at = state, occurred_at
    return latest


async def _persisted_state_for_recovery(
    sb: Any, signal: Signal
) -> Optional[OrderLifecycleState]:
    """``signal``'s persisted Order_Lifecycle_State for the sweep, or ``None`` if unknown.

    Two persisted sources, tried in order, and NO third one:

      1. ``public.signals.order_lifecycle_state`` - the canonical column, read strictly
         (:func:`load_current_order_lifecycle_state` with ``strict=True``), so a raised
         read, a response carrying ``result.error``, a missing row and unreconcilable
         legacy columns all report nothing rather than something.
      2. ``order_lifecycle_transitions``' newest ``to_state`` - the same fact, written by
         the same call immediately after the column.

    ``None`` when neither can be read. What is NOT here is the third source this used to
    reach for: ``signal.order_lifecycle_state``, this process's own memory. See the TASK 12
    header, "THE SWEEP READS THE RECORD OR REPORTS NOTHING".
    """
    persisted = await load_current_order_lifecycle_state(sb, signal, strict=True)
    if persisted is not None:
        return persisted

    from_log = await latest_persisted_transition_state(sb, signal)
    if from_log is not None:
        logger.warning(
            "Signal %s's canonical Order_Lifecycle_State could not be read; using the "
            "newest row of its persisted transition history (%s) instead. Still the "
            "database's answer, never this process's.",
            signal.id,
            from_log.value,
        )
        return from_log

    logger.error(
        "Signal %s's persisted Order_Lifecycle_State could not be read from the row OR "
        "from its transition history. This sweep has no state to reason from and will not "
        "substitute its own memory for one.",
        signal.id,
    )
    return None


def _state_word(state: Optional[OrderLifecycleState]) -> str:
    """``state``'s canonical word, or ``"unreadable"`` when there is no state to name."""
    return state.value if state is not None else "unreadable"


async def manual_reconciliation_marked(sb: Any, signal: Signal) -> Optional[bool]:
    """Whether ``signal`` already carries a manual-reconciliation marker.

    ``None`` for "could not tell" - an unreadable answer here must not be reported as
    "no marker", because the caller uses this to avoid appending a second one.
    """
    if sb is None:
        return None
    try:
        result = await _execute(
            sb.table(SIGNAL_EVENTS_TABLE)
            .select("id")
            .eq("signal_id", signal.id)
            .eq("event_type", MANUAL_RECONCILIATION_EVENT)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not check whether signal %s is already marked for manual "
            "reconciliation (%s).",
            signal.id,
            exc,
        )
        return None
    if getattr(result, "error", None) is not None:
        return None
    return bool(getattr(result, "data", None))


async def mark_manual_reconciliation_required(
    sb: Any,
    signal: Signal,
    *,
    reason: str,
    attempts: int,
    persisted: Optional[OrderLifecycleState],
    at: Optional[str] = None,
) -> bool:
    """Record that ``signal`` cannot be resolved automatically. Requirement 19.2.

    One row in ``public.signal_events``, idempotent, carrying the Idempotency_Key, the
    state the signal is stuck at, the number of attempts made and why they were not
    definitive. See this section's header for why the marker is an event rather than a
    tenth Order_Lifecycle_State, and why a failure to write it is logged rather than
    raised.

    ``persisted`` may be ``None``: a sweep that could not read the record is exactly a
    sweep whose signal needs a human. The marker then says so rather than naming a state
    nobody established.

    Returns
        ``True`` when a marker exists after this call (written now or already there),
        ``False`` when it could not be recorded.
    """
    already = await manual_reconciliation_marked(sb, signal)
    if already:
        return True
    if sb is None:
        logger.error(
            "Signal %s requires manual reconciliation (%s) but there is no database "
            "client to record the marker on. It will NOT be resubmitted.",
            signal.id,
            reason,
        )
        return False

    payload = {
        "signal_id": signal.id,
        "user_id": signal.user_id,
        "event_type": MANUAL_RECONCILIATION_EVENT,
        "event_data": _jsonable(
            {
                "idempotency_key": signal.idempotency_key,
                "order_lifecycle_state": persisted.value if persisted is not None else None,
                "attempts": int(attempts),
                "max_attempts": RECOVERY_MAX_ATTEMPTS,
                "budget_seconds": RECOVERY_TOTAL_BUDGET_SECONDS,
                "reason": reason,
                "deployment_id": signal.deployment_id,
                "requires_manual_reconciliation": True,
            }
        ),
        "event_timestamp": at or datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = await _execute(sb.table(SIGNAL_EVENTS_TABLE).insert(payload).execute())
    except Exception as exc:  # noqa: BLE001 - never fails the sweep, see the header
        logger.error(
            "Signal %s requires manual reconciliation (%s) but the marker could not be "
            "recorded in public.%s: %s. The signal is still NOT being resubmitted; its "
            "Order_Lifecycle_State remains %s.",
            signal.id,
            reason,
            SIGNAL_EVENTS_TABLE,
            exc,
            _state_word(persisted),
        )
        return False

    error = getattr(result, "error", None)
    if error is not None:
        logger.error(
            "Signal %s requires manual reconciliation (%s) but public.%s refused the "
            "marker: %s. The signal is still NOT being resubmitted.",
            signal.id,
            reason,
            SIGNAL_EVENTS_TABLE,
            error,
        )
        return False

    logger.warning(
        "Signal %s is marked as requiring manual reconciliation: %s. Its "
        "Order_Lifecycle_State stays %s (Requirement 19.2 forbids resubmitting under "
        "idempotency key %s, and forbids claiming a state the exchange did not "
        "confirm).",
        signal.id,
        reason,
        _state_word(persisted),
        signal.idempotency_key,
    )
    return True


async def recover_signal(
    signal: Signal,
    *,
    exchange: Any,
    sb: Any = None,
    user: Any = None,
    max_attempts: int = RECOVERY_MAX_ATTEMPTS,
    budget_seconds: float = RECOVERY_TOTAL_BUDGET_SECONDS,
    retry_delay_seconds: float = RECOVERY_RETRY_DELAY_SECONDS,
    sleep: Optional[Callable[[float], Any]] = None,
    monotonic: Optional[Callable[[], float]] = None,
) -> RecoveryOutcome:
    """Establish what really happened to ``signal``, before anything else acts on it.

    Requirement 19.2, and the precondition Requirement 19.4's regression suite is
    written against. Called by a worker (or an API process, or a runtime whose feed or
    exchange connection dropped) on restart, for every signal it was carrying that has
    not reached a terminal state.

    THE SHAPE, IN ORDER
        1. Read the PERSISTED state (:func:`_persisted_state_for_recovery`). The row - or
           failing that, the newest row of the persisted transition history - and NEVER
           this process's memory: a process that just restarted has no memory of this
           signal, and Requirement 19.3's "resume from its last persisted
           Order_Lifecycle_State without re-deriving it from an earlier, superseded state"
           is exactly this read. When neither can be read, the state is UNKNOWN, and this
           function reports no state rather than inventing one.
        2. A terminal state ends it. Nothing is in flight and nothing may be resent. An
           UNKNOWN state is never treated as terminal - not knowing is not finishing.
        3. Ask the exchange for the order matching ``idempotency_key_for(signal)`` -
           the same key the submission used - bounded to ``max_attempts`` attempts
           within ``budget_seconds`` in total.
        4. Reconcile FORWARD only, through ``apply_order_lifecycle_state`` (gate, then
           write, then audit) so a recovery cannot write an illegal transition and
           every state it writes is in the Requirement 16.7 history.
        5. Where the answer was not definitive: write nothing, mark the signal, and
           refuse resubmission. An UNKNOWN persisted state cannot rule out a live order,
           so it is marked like any other signal that could have one.
        6. Where the answer WAS definitive but the state is UNKNOWN: refuse. There is
           nothing to reconcile the answer against, and no verdict may be reported.

    Args:
        signal: The signal to recover. Only its identity, owner and Idempotency_Key are
            trusted; its in-memory ``order_lifecycle_state`` is not.
        exchange: The venue client. Duck-typed - see :func:`_exchange_lookup_seam`. A
            client with no lookup seam is Requirement 19.2's "does not support such a
            lookup" and is handled, not refused.
        sb: An RLS-scoped PostgREST client. Built from ``user``'s token when absent.
        user: The owning user, for the client only.
        max_attempts / budget_seconds / retry_delay_seconds: Requirement 19.2's bounds.
            Arguments rather than constants so a test can pin the boundary exactly;
            the defaults ARE the requirement's numbers.
        sleep / monotonic: The wait and the clock. Injectable for the same reason -
            default to ``asyncio.sleep`` and ``time.monotonic``.

    Returns:
        A :class:`RecoveryOutcome`. Check ``resubmission_allowed`` before submitting.
        ``order_lifecycle_state`` is ``None`` when the record could not be read.

    Raises:
        SignalSubmissionRefused: No database client is available, so the persisted
            state cannot be read - and a sweep that cannot read the record must not
            report a verdict about it.
        SignalPersistenceError: ``SIGNAL_STATE_NOT_READABLE`` - the exchange answered but
            the persisted state could not be read from the row or from the transition
            history, so the answer cannot be reconciled or recorded. Also raised
            (``SIGNAL_STATE_NOT_PERSISTED``) when a reconciling write is refused.
            :func:`recover_in_flight_signals` contains both per signal.
    """
    client = await _resolve_client(sb, user, None)
    if client is None:
        raise SignalSubmissionRefused(
            "SIGNAL_NO_PERSISTENCE_CLIENT",
            f"No database client is available to read signal {signal.id}'s persisted "
            f"Order_Lifecycle_State, so its crash recovery cannot run. Requirement 19.2 "
            f"treats the persisted state as authoritative when the exchange cannot "
            f"answer; a sweep that cannot read that state has no authority to report on "
            f"this signal at all, and reporting one anyway is how a live order gets "
            f"duplicated.",
            {"signal_id": signal.id, "deployment_id": signal.deployment_id},
        )

    wait = sleep if sleep is not None else asyncio.sleep
    clock = monotonic if monotonic is not None else time.monotonic
    key = idempotency_key_for(signal)

    # The record, or nothing. NEVER this process's memory - see the section header,
    # "THE SWEEP READS THE RECORD OR REPORTS NOTHING".
    persisted = await _persisted_state_for_recovery(client, signal)
    current = signal if persisted is None else signal.with_order_lifecycle_state(persisted)

    if persisted is not None and is_terminal(persisted):
        logger.info(
            "Signal %s is already at the terminal state %s; nothing to recover and "
            "nothing to resubmit.",
            signal.id,
            persisted.value,
        )
        return RecoveryOutcome(
            status=RECOVERY_TERMINAL,
            order_lifecycle_state=persisted,
            resubmission_allowed=False,
            reason=f"already terminal at {persisted.value}",
            detail={"signal_id": signal.id, "idempotency_key": key},
        )

    lookup = _exchange_lookup_seam(exchange)
    if lookup is None:
        return await _no_definitive_answer(
            client,
            current,
            persisted,
            status=RECOVERY_PERSISTED_STATE_AUTHORITATIVE,
            attempts=0,
            reason=(
                f"the exchange client ({type(exchange).__name__}) does not support "
                f"lookup by client order identifier, so the persisted "
                f"Order_Lifecycle_State ({_state_word(persisted)}) is authoritative "
                f"(Requirement 19.2)"
            ),
            detail={"signal_id": signal.id, "idempotency_key": key},
        )

    # ── the bounded check (Requirement 19.2: 3 attempts, 30 seconds total) ──
    started = clock()
    attempts = 0
    failures: List[str] = []
    report: Any = None
    definitive = False

    while attempts < max_attempts:
        elapsed = clock() - started
        if attempts and elapsed >= budget_seconds:
            failures.append(
                f"the {budget_seconds:.0f}s budget was exhausted after {attempts} "
                f"attempt(s)"
            )
            break
        attempts += 1
        try:
            report = await _maybe_await(lookup(key))
            definitive = True
            break
        except Exception as exc:  # noqa: BLE001 - an unreachable venue is the case
            failures.append(f"attempt {attempts}: {exc}")
            logger.warning(
                "Crash recovery for signal %s: exchange lookup by idempotency key %s "
                "failed on attempt %d of %d (%s).",
                signal.id,
                key,
                attempts,
                max_attempts,
                exc,
            )
            if attempts >= max_attempts:
                break
            remaining = budget_seconds - (clock() - started)
            if remaining <= 0:
                failures.append(f"the {budget_seconds:.0f}s budget was exhausted")
                break
            # Never overrun the budget waiting to use it.
            await _maybe_await(wait(min(retry_delay_seconds, remaining)))

    if not definitive:
        return await _no_definitive_answer(
            client,
            current,
            persisted,
            status=RECOVERY_MANUAL_RECONCILIATION_REQUIRED,
            attempts=attempts,
            reason=(
                f"the exchange-state check for idempotency key {key} ended without a "
                f"definitive answer after {attempts} attempt(s) within "
                f"{budget_seconds:.0f}s: {'; '.join(failures) or 'no reason reported'}"
            ),
            detail={
                "signal_id": signal.id,
                "idempotency_key": key,
                "failures": list(failures),
            },
        )

    if persisted is None:
        # The venue ANSWERED, and there is no readable record to answer against. Every
        # remaining branch either writes a transition (which needs a from_state nobody
        # could read) or reports a state as the sweep's verdict (which would be this
        # process's memory). recover_signal's own contract - "a sweep that cannot read
        # that state has no authority to report on this signal at all" - is enforced for a
        # missing CLIENT, and this is the same fact arriving one step later, so it gets the
        # same answer. Raised, not returned, so no caller can mistake it for a verdict;
        # recover_in_flight_signals contains it per signal.
        raise SignalPersistenceError(
            f"The exchange answered about signal {signal.id} under idempotency key {key}, "
            f"but its persisted Order_Lifecycle_State could not be read - not from "
            f"public.signals and not from its transition history - so there is nothing to "
            f"reconcile the answer against and nowhere to record it. No order was "
            f"resubmitted and nothing was written. Requirement 19.3 forbids re-deriving "
            f"the state from an earlier, superseded value, which is the only other thing "
            f"this sweep could do.",
            {
                "signal_id": signal.id,
                "idempotency_key": key,
                "attempts": attempts,
                "deployment_id": signal.deployment_id,
            },
            code="SIGNAL_STATE_NOT_READABLE",
        )

    if report is None or report is False:
        # Definitive: the venue has no order under this key. This is the ONE path on
        # which resubmission is permitted, and it is permitted because the key was
        # demonstrably never used - not because nothing went wrong.
        logger.info(
            "Crash recovery for signal %s: the exchange reports no order under "
            "idempotency key %s after %d attempt(s). Its persisted state %s stands and "
            "submission may proceed under the same key.",
            signal.id,
            key,
            attempts,
            persisted.value,
        )
        return RecoveryOutcome(
            status=RECOVERY_NO_ORDER_AT_EXCHANGE,
            order_lifecycle_state=persisted,
            resubmission_allowed=True,
            attempts=attempts,
            reason=f"no order exists at the exchange under {key}",
            detail={"signal_id": signal.id, "idempotency_key": key},
        )

    reported = _exchange_reported_state(report, current)
    exchange_order_id = _exchange_order_id(report)
    route = lifecycle_path(persisted, reported)

    if not route:
        # Either the record already says what the exchange says, or the exchange's
        # answer is BEHIND the record - a stale read. Neither is a write: Requirement
        # 19.3 forbids overwriting an in-flight signal's state with an earlier one, and
        # this is where that holds structurally rather than by a comparison someone
        # remembered to write.
        if reported is not persisted:
            logger.warning(
                "Crash recovery for signal %s: the exchange reports %s but the record "
                "already says %s, which is not behind it. Nothing is written - a "
                "persisted state is never moved backwards (Requirement 19.3).",
                signal.id,
                reported.value,
                persisted.value,
            )
        return RecoveryOutcome(
            status=RECOVERY_ALREADY_CONSISTENT,
            order_lifecycle_state=persisted,
            resubmission_allowed=False,
            attempts=attempts,
            exchange_state=reported,
            exchange_order_id=exchange_order_id,
            reason=(
                f"an order exists at the exchange under {key}; the persisted state "
                f"{persisted.value} needs no forward transition"
            ),
            detail={"signal_id": signal.id, "idempotency_key": key},
        )

    recovered = await apply_order_lifecycle_state(
        client,
        current,
        reported,
        reason=(
            f"crash recovery: the exchange reports the order under {key} as "
            f"{reported.value}"
        ),
        order_id=exchange_order_id,
        # The exchange reporting a FILL means the order both reached the venue and
        # filled - both happened, and PENDING -> EXECUTED is not an edge. Same
        # justification submit_signal uses for the same opt-in, and it is enabled only
        # for a route the table itself found.
        through_intermediate_states=len(route) > 1,
    )

    logger.info(
        "Crash recovery for signal %s: reconciled %s -> %s from the exchange's own "
        "record of idempotency key %s. No order was resubmitted.",
        signal.id,
        persisted.value,
        recovered.order_lifecycle_state.value,
        key,
    )
    return RecoveryOutcome(
        status=RECOVERY_RECONCILED,
        order_lifecycle_state=recovered.order_lifecycle_state,
        resubmission_allowed=False,
        attempts=attempts,
        exchange_state=reported,
        exchange_order_id=exchange_order_id,
        reason=(
            f"reconciled {persisted.value} -> {recovered.order_lifecycle_state.value} "
            f"from the exchange's record of {key}"
        ),
        detail={
            "signal_id": signal.id,
            "idempotency_key": key,
            "route": [state.value for state in route],
        },
    )


async def _no_definitive_answer(
    sb: Any,
    signal: Signal,
    persisted: Optional[OrderLifecycleState],
    *,
    status: str,
    attempts: int,
    reason: str,
    detail: Dict[str, Any],
) -> RecoveryOutcome:
    """The shared tail of every path that could not establish the truth.

    Writes nothing to the signal's state (there is nothing confirmed to write, and
    Requirement 19.2 says the persisted value is authoritative here), refuses
    resubmission, and marks the signal for a human where an order could still be live
    behind it - see this section's header on why "past GENERATED and not terminal" is
    the condition for the marker.

    ``persisted is None`` - THE RECORD ITSELF COULD NOT BE READ - COUNTS AS "COULD BE LIVE"
        An unreadable record does not rule out a live order; it rules out KNOWING. The
        condition below is therefore "not demonstrably harmless" rather than "demonstrably
        harmful". Deriving it from this process's in-memory ``GENERATED`` instead - which is
        what happened before - reported ``requires_manual_reconciliation=False`` for a
        signal with a live order at the venue, defeating the one clause of Requirement 19.2
        that exists for this exact situation.
    """
    could_have_live_order = persisted is None or (
        persisted is not OrderLifecycleState.GENERATED and not is_terminal(persisted)
    )
    marked = False
    if could_have_live_order:
        marked = await mark_manual_reconciliation_required(
            sb, signal, reason=reason, attempts=attempts, persisted=persisted
        )
    else:
        logger.info(
            "Crash recovery for signal %s: %s. It is at %s, so no order can be live "
            "behind it and no manual-reconciliation marker is written.",
            signal.id,
            reason,
            _state_word(persisted),
        )

    return RecoveryOutcome(
        status=status,
        order_lifecycle_state=persisted,
        resubmission_allowed=False,
        attempts=attempts,
        requires_manual_reconciliation=could_have_live_order,
        marked=marked,
        reason=reason,
        detail=dict(detail),
    )


async def recover_in_flight_signals(
    signals: Iterable[Signal],
    *,
    exchange: Any,
    sb: Any = None,
    user: Any = None,
    **kwargs: Any,
) -> Dict[str, RecoveryOutcome]:
    """Run :func:`recover_signal` over every signal a restarting worker was carrying.

    Requirement 19.2's "upon restart and before taking any further action", in the
    plural shape a restart actually has. Keyed by signal id so the caller can decide
    per signal whether submission may proceed.

    One signal's recovery never aborts another's, AND THAT IS ENFORCED FOR ANY FAILURE
        A classified refusal (:class:`SignalRejected` and its subclasses) is the expected
        shape and is recorded as that signal's outcome. But an UNCLASSIFIED exception -
        a driver error from a restarting database, a client that raises something this
        module has never seen - used to escape and abort the whole restart on the first
        signal that met one, which is the opposite of what this docstring promised. So
        every exception is contained, per signal, and each becomes a
        ``MANUAL_RECONCILIATION_REQUIRED`` outcome with ``resubmission_allowed=False``.

        ``BaseException`` is deliberately NOT caught: a ``KeyboardInterrupt``, a
        ``SystemExit`` or a cancelled task is the process being told to stop, and
        continuing to sweep the remaining signals would be ignoring that.

        The outcome's ``order_lifecycle_state`` is ``None``, not the caller's in-memory
        value: this sweep established nothing about this signal, and reporting the
        superseded state the object happens to be carrying is what Requirement 19.3 forbids.

    A restart that gives up on the whole deployment because one row could not be read is a
    worse failure than a restart that resumes the rest and marks the one.
    """
    outcomes: Dict[str, RecoveryOutcome] = {}
    for signal in signals:
        try:
            outcomes[signal.id] = await recover_signal(
                signal, exchange=exchange, sb=sb, user=user, **kwargs
            )
        except Exception as exc:  # noqa: BLE001 - contained per signal, never swallowed
            code = getattr(exc, "code", None) or type(exc).__name__
            message = getattr(exc, "message", None) or str(exc)
            logger.error(
                "Crash recovery could not run for signal %s (%s: %s). It is NOT "
                "resubmitted, and the rest of this sweep continues.",
                signal.id,
                code,
                message,
            )
            outcomes[signal.id] = RecoveryOutcome(
                status=RECOVERY_MANUAL_RECONCILIATION_REQUIRED,
                order_lifecycle_state=None,
                resubmission_allowed=False,
                requires_manual_reconciliation=True,
                marked=False,
                reason=f"{code}: {message}",
                detail={"signal_id": signal.id},
            )
    return outcomes


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 13.1 - the signal-trace list: filters, projection, one page
#
#  Spec: trading-lifecycle-integration. design.md -> "New router:
#  signal_trace.py" and the API table's GET /api/signal-trace/signals row.
#  Requirements 17.1, 17.2, 17.5, 17.7, 20.1, 21.3.
#
#  WHY THIS LIVES HERE AND NOT IN THE ROUTER
#  -----------------------------------------
#  Two of the three things this endpoint has to get right are not HTTP
#  concerns:
#
#    * The SHAPE. Signal.to_public_dict() is the one shape that reaches the
#      frontend (its own docstring says so: "the signal-trace router (task 13)
#      and the SIGNAL_FAMILY channel (task 14) both render from here rather
#      than from a row"). A persisted row is not that shape, so something has
#      to turn one back into a Signal - and that something belongs beside the
#      record whose shape it reconstructs, not in a router, because task 14's
#      channel needs the identical projection.
#
#    * The 005b DEGRADATION. Whether public.signals carries
#      order_lifecycle_state is a database fact, probed by
#      signal_lifecycle_columns_supported, and the fallback is Requirement
#      16.2's SIGNALS_STATUS_MAP. A router asking "does this column exist"
#      would be a second place that has to know.
#
#  The router keeps what is genuinely HTTP: authentication, the rate limit,
#  parameter parsing and bounds, and the response/error envelope.
#
#  AND-ACROSS-CATEGORIES / OR-WITHIN-CATEGORY, CONCRETELY (Requirement 17.2)
#  ------------------------------------------------------------------------
#  Each filter category is one predicate on the query:
#
#      one value   -> column = value          (PostgREST eq)
#      many values -> column IN (v1, v2, ...) (PostgREST in.)
#
#  and PostgREST ANDs the predicates it is given. So
#  ?symbol=BTC/USDT&symbol=ETH/USDT&side=BUY reads
#  "(symbol = BTC/USDT OR symbol = ETH/USDT) AND decision = BUY", which is
#  Requirement 17.2's "included only if it matches at least one selected value
#  in every active category" without any set arithmetic in Python. An absent
#  category adds no predicate, so it constrains nothing - which is the
#  difference Requirement 17.7 asks the empty state to distinguish, reported
#  as `filters_active` on the response.
#
#  WHERE `side` ACTUALLY LIVES
#  ---------------------------
#  public.signals HAS NO `side` COLUMN. 002/003 give it `decision`
#  VARCHAR(10) NOT NULL carrying BUY/SELL/EXIT/CLOSE, and 005b's own header
#  says so in as many words when it accounts for Requirement 21.3's index
#  list: "21.3's index list for signals is served by 002/003 for strategy_id,
#  deployment_id, symbol, decision (side) and generated_at". So the `side`
#  filter is applied to `decision`, served by idx_signals_decision, and no
#  migration is needed for it. (Signal.side is the same word for an ENTRY and
#  is None for an EXIT/CLOSE whose closing side the DAG did not state, which
#  is why the FILTER cannot be a JSONB read of market_info.side: it would
#  silently drop every exit.)
#
#  005b IS APPLIED BY HAND, SO ITS ABSENCE DEGRADES RATHER THAN 500s
#  ----------------------------------------------------------------
#  Same disposition as generate_signal's write path, for the same reason.
#  When order_lifecycle_state is absent:
#
#    * it is dropped from the SELECT projection, so PostgREST does not 400;
#    * a requested order_lifecycle_state filter is translated onto the legacy
#      `status` column through the INVERSE of Requirement 16.2's
#      SIGNALS_STATUS_MAP (see LEGACY_STATUS_SPELLINGS) - so the filter stays
#      a database predicate and pagination stays correct, rather than becoming
#      a Python post-filter that would make Requirement 17.5's page
#      boundaries lie;
#    * each row's reported state is reconciled by
#      reconcile_row_lifecycle_state, the same mapping;
#    * a warning NAMES 005b_signal_lifecycle_and_idempotency.sql, and the
#      response carries `degraded` so the page can say so rather than
#      pretending the list is complete.
#
#  THREE OF THE NINE CANONICAL STATES HAVE NO LEGACY SPELLING AT ALL.
#  SIGNALS_STATUS_MAP maps seven legacy words onto six canonical values;
#  GENERATED, PARTIALLY_EXECUTED and CLOSED are not among them, because the
#  legacy vocabulary never modelled them. Filtering for one of those without
#  005b applied therefore cannot be answered from `status`, and the honest
#  answer is an empty page plus a named reason
#  (`unsupported_lifecycle_states`) - not every signal, and not a 500.
#
#  PAGINATION WITHOUT CLAIMING A TOTAL WE DID NOT COUNT (Requirement 17.5)
#  ----------------------------------------------------------------------
#  The page is fetched at limit+1 rows and sliced to limit. The extra row is
#  never returned; its existence IS `has_more`. That is one round trip, needs
#  no COUNT over a table that grows without bound, and gives the page the one
#  fact Requirement 17.5 actually requires - "controls that allow the user to
#  reach every signal beyond the first page". `total` is reported as a LOWER
#  BOUND and says so (`total_is_exact: false`), because the alternative is a
#  number this endpoint did not count.
#
#  OWNERSHIP (Requirements 20.1, 20.2)
#  -----------------------------------
#  Two layers, both present, neither standing in for the other: the client is
#  the caller's own request-scoped one (so RLS applies), and list_signals adds
#  an explicit .eq("user_id", user_id). A filter naming another user's
#  strategy, version or deployment is not an error - it simply matches no row,
#  which is byte-for-byte the response an identifier that does not exist
#  produces. That identity is what Requirement 20.2 asks for on a list, and
#  it holds structurally: this code never looks the named resource up, so it
#  has nothing to reveal about whether it exists.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


from backend_app.backend.order_lifecycle_state import (  # noqa: E402
    ORDER_LIFECYCLE_STATE_VALUES,
    SIGNALS_STATUS_MAP,
)

#: The inverse of Requirement 16.2's ``SIGNALS_STATUS_MAP``: for each canonical state,
#: every legacy ``signals.status`` spelling that maps onto it. Derived from that table
#: rather than transcribed, so the two cannot drift. ``CANCELLED`` has two spellings
#: (``cancelled`` and ``expired``); ``GENERATED``, ``PARTIALLY_EXECUTED`` and ``CLOSED``
#: have none, and their absence here is the fact
#: :data:`LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING` reports.
LEGACY_STATUS_SPELLINGS: Dict[OrderLifecycleState, Tuple[str, ...]] = {}
for _legacy_word, _canonical_state in SIGNALS_STATUS_MAP.items():
    LEGACY_STATUS_SPELLINGS[_canonical_state] = LEGACY_STATUS_SPELLINGS.get(
        _canonical_state, ()
    ) + (_legacy_word,)
del _legacy_word, _canonical_state

#: The canonical states the legacy ``status`` vocabulary cannot express, so a filter on
#: one of them is unanswerable until 005b is applied.
LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING: Tuple[OrderLifecycleState, ...] = tuple(
    state for state in OrderLifecycleState if state not in LEGACY_STATUS_SPELLINGS
)

#: The keys ``Signal._market_info()`` adds on top of the market context, so a row read back
#: can be split into the two again instead of the attribution leaking into
#: ``market_context``. Kept beside that method's own key list by this comment; a test pins
#: the round trip.
_MARKET_INFO_ATTRIBUTION_KEYS: Tuple[str, ...] = (
    "signal_type",
    "side",
    "mode",
    "strategy_version_id",
    "exchange_account_id",
    "source_node_ids",
    "closure_ready",
    "sizing_intention",
)


def _filter_values(value: Any) -> Tuple[str, ...]:
    """One filter category's selected values, de-duplicated, order preserved.

    Accepts a single value, a sequence of them, or ``None``. Blank strings are dropped -
    an empty query parameter (``?symbol=``) is how an HTML form spells "this category is
    not active", and treating it as ``symbol = ''`` would filter every signal away.
    ``()`` therefore means "category not active", which is distinct from a category whose
    values match nothing.
    """
    if value is None:
        return ()
    candidates = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    seen: Dict[str, None] = {}
    for candidate in candidates:
        text = _text(candidate)
        if text is not None:
            seen.setdefault(text, None)
    return tuple(seen)


def _apply_column_filter(query: Any, column: str, value: Any) -> Any:
    """One filter category as one predicate: ``eq`` for a single value, ``in_`` for many.

    Returns ``query`` untouched when the category is not active, so an absent filter adds
    no predicate at all (rather than a vacuous one that a query planner would still have
    to consider).
    """
    values = _filter_values(value)
    if not values:
        return query
    if len(values) == 1:
        return query.eq(column, values[0])
    return query.in_(column, list(values))


def _matches_column_filter(actual: Any, value: Any) -> bool:
    """The in-process equivalent of :func:`_apply_column_filter`, for the local fallback.

    OR within the category, and "not active" matches everything - the same two rules, so
    the fallback cannot answer a filter differently from the database.
    """
    values = _filter_values(value)
    if not values:
        return True
    return _text(actual) in values


def resolve_lifecycle_state_filter(
    value: Any,
) -> Tuple[Tuple[OrderLifecycleState, ...], Tuple[str, ...]]:
    """A requested ``order_lifecycle_state`` filter, validated. ``(states, unrecognised)``.

    Each value is normalised through ``order_lifecycle_state.normalise_lifecycle_state``,
    so any case and either separator resolves. A value outside the 9 is returned in
    ``unrecognised`` rather than dropped: the router refuses the request with 400 naming
    it, because silently ignoring an unrecognised filter would return MORE signals than
    the caller asked for, which on a trading audit page is the wrong way to be wrong.
    """
    states: Dict[OrderLifecycleState, None] = {}
    unrecognised: Dict[str, None] = {}
    for raw in _filter_values(value):
        state = normalise_lifecycle_state(raw)
        if state is None:
            unrecognised.setdefault(raw, None)
        else:
            states.setdefault(state, None)
    return tuple(states), tuple(unrecognised)


def resolve_environment_filter(
    value: Any,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """A requested ``environment`` filter, validated. ``(environments, unrecognised)``.

    ONE MORE OF THE SAME (task 29.3, Requirements 23.4, 23.6)
        Declared, resolved and applied exactly as Requirement 17.2's other filter categories
        are: a list of values, ``()`` for "category not active", OR within the category
        through :func:`_apply_column_filter`, and AND against every other category. So
        ``?environment=PAPER&environment=LIVE`` collects into both, and no environment
        filter at all leaves the default of Requirement 23.6 - the caller's own signals
        across all environments - exactly as it was.

    THE VOCABULARY IS THE PLATFORM'S ONE RESOLVER, WITH CASE FOLDED FIRST
        Resolution goes through ``execution_environment.parse_execution_environment``, so
        the three words are not re-spelled here. Case and surrounding whitespace ARE folded
        before it is called, which that function deliberately does not do for the live-order
        guard - and the difference is safe in exactly one direction: this value becomes a
        SELECT predicate. It cannot promote a simulated intent to a real order, and a
        bookmarked or hand-typed ``?environment=paper`` is a URL a user legitimately sends
        (the same allowance ``normalise_export_format`` already makes for ``"CSV"``).

    A value that still does not resolve is returned in ``unrecognised`` rather than dropped;
    the callers raise :class:`SignalEnvironmentRefused` for it.
    """
    resolved: Dict[str, None] = {}
    unrecognised: Dict[str, None] = {}
    for raw in _filter_values(value):
        member = parse_execution_environment(str(raw).strip().upper())
        if member is None:
            unrecognised.setdefault(raw, None)
        else:
            resolved.setdefault(member.value, None)
    return tuple(resolved), tuple(unrecognised)


def environment_counts(items: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """How many of ``items`` belong to each Execution_Environment. Requirement 23.4.

    Requirement 23.4 forbids a total, an aggregate or a chart series that mixes ``PAPER``
    with ``LIVE`` "without an explicit environment label". This is that label, as a fact on
    the envelope: every count is keyed by the environment it counts, so the page can render
    one series per environment without deriving the grouping from the rows itself and
    without a mixed figure being the only figure available.

    A row whose environment is unknown - a pre-010 database, where the column does not
    exist - is counted under :data:`UNLABELLED_ENVIRONMENT` rather than being assumed to be
    ``LIVE`` or quietly dropped from the total.

    Ordered ``BACKTEST``, ``PAPER``, ``LIVE``, then ``UNLABELLED``: the check-constraint
    order, so the series order does not depend on which signal sorted first.
    """
    counts: Dict[str, int] = {}
    for item in items:
        label = _text(_pick(item, "environment")) or UNLABELLED_ENVIRONMENT
        counts[label] = counts.get(label, 0) + 1

    order = [member.value for member in EXECUTION_ENVIRONMENTS] + [UNLABELLED_ENVIRONMENT]
    ranked = sorted(counts, key=lambda label: (order.index(label) if label in order else len(order), label))
    return {label: counts[label] for label in ranked}


def _environment_degradation(
    with_environment: bool, requested: Tuple[str, ...] = ()
) -> Optional[Dict[str, Any]]:
    """The read path's 010 degradation block. ``None`` when the migration is applied.

    WHAT A PRE-010 DATABASE DOES WITH AN ``?environment=`` FILTER, AND WHY (task 29.3)
        The column does not exist, so the predicate cannot be sent. Three answers were
        available and two of them are lies:

        * the unfiltered list - it would present ``PAPER`` and ``LIVE`` signals as though
          they had been filtered to one environment, which is precisely Requirement 23.4's
          prohibition on an unlabelled mix, and MORE rows than were asked for;
        * an empty list with nothing said - indistinguishable from "you have no PAPER
          signals", which is a fabricated fact (there may be many; the column that would
          identify them is missing);
        * an empty list that SAYS the filter could not be applied, naming the migration.

        The third is what happens. It is also the disposition this module already takes for
        the one other unanswerable filter it has - an ``order_lifecycle_state`` the legacy
        vocabulary cannot express returns :func:`_empty_signal_trace_page` rather than the
        unfiltered list, because "returning the unfiltered list would be worse than
        returning none: it would answer a question nobody asked". Same reasoning, same
        answer, one more time.

    WHY THIS IS NOT A REFUSAL, EITHER
        The overall disposition to a missing COLUMN in this module is degrade-with-a-warning:
        an absent column costs an audit FIELD, not the record and not the endpoint. So an
        unfiltered list on a pre-010 database still answers every signal it always did -
        with ``count_by_environment`` reporting them all as ``UNLABELLED`` and this block
        saying why. Only the *filtered* request goes empty, and only because the question it
        asks cannot be answered from the schema in front of it.
    """
    if with_environment:
        return None
    return {
        "migration": SIGNAL_ENVIRONMENT_MIGRATION,
        "reason": (
            "public.signals does not carry the environment column, so no signal on this page "
            "can be attributed to an Execution_Environment (Requirement 23.1) and a PAPER "
            "signal is not distinguishable from a LIVE one by any column."
        ),
        "environment_filter": list(requested),
        # With no filter requested there is nothing to answer, so this is trivially true and
        # the page is the full, honestly-unlabelled list. With one requested it is false and
        # the page is empty FOR THAT REASON - which is what makes the emptiness readable
        # rather than a claim about how many signals the caller has.
        "environment_filter_answered": not requested,
    }


def legacy_status_filter_for(
    states: Iterable[OrderLifecycleState],
) -> Tuple[Tuple[str, ...], Tuple[OrderLifecycleState, ...]]:
    """The legacy ``status`` values equivalent to ``states``. ``(spellings, unsupported)``.

    Requirement 16.2's mapping, run backwards, for a database without 005b.
    ``unsupported`` names the requested states the legacy vocabulary cannot express
    (:data:`LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING`) so the caller can say which part of
    its filter went unanswered instead of quietly returning a shorter list.
    """
    spellings: Dict[str, None] = {}
    unsupported: Tuple[OrderLifecycleState, ...] = ()
    for state in states:
        words = LEGACY_STATUS_SPELLINGS.get(state)
        if not words:
            unsupported += (state,)
            continue
        for word in words:
            spellings.setdefault(word, None)
    return tuple(spellings), unsupported


def signal_from_row(row: Mapping[str, Any]) -> Signal:
    """A persisted ``public.signals`` row back into the :class:`Signal` it was written from.

    The exact inverse of :meth:`Signal.to_row` plus :meth:`Signal._market_info`, so the
    trace surfaces render through :meth:`Signal.to_public_dict` - one shape reaching the
    frontend, whether the signal came from this process's own mint or from a row written
    by a worker that has since exited.

    THE STATE IS RECONCILED, NOT READ (Requirement 16.2)
        ``order_lifecycle_state`` when 005b's column carries one, and otherwise
        :func:`reconcile_row_lifecycle_state`'s mapping from the legacy ``status`` /
        ``order_status`` columns. A row nothing can be reconciled from reports
        ``GENERATED`` - the state a signal is in when no source has said anything about it -
        which is what :func:`resolve_order_lifecycle_state` returns for exactly that case.

    WHAT DOES NOT SURVIVE THE ROUND TRIP, HONESTLY
        ``risk_validation["checks"]`` and ``risk_validation["blocked"]``: neither has a
        column, and ``to_row`` never persisted them. ``blocked`` is recoverable from
        ``risk_passed`` and is not re-derived here, because "not passed" and "blocked" are
        the same fact only when a verdict was recorded at all.
    """
    market_info = _dict_of(_pick(row, "market_info"))
    decision = _normalise_decision(_pick(row, "decision")) or _text(_pick(row, "decision")) or ""

    signal_type = _text(_pick(market_info, "signal_type"))
    if signal_type is None:
        signal_type = "ENTRY" if decision in ENTRY_DECISIONS else "EXIT"

    side = _normalise_decision(_pick(market_info, "side"))
    if side is None and decision in ENTRY_DECISIONS:
        side = decision
    if side is not None and side not in ENTRY_DECISIONS:
        side = None

    raw_nodes = _pick(market_info, "source_node_ids")
    source_node_ids = tuple(
        text
        for text in (
            _text(node)
            for node in (raw_nodes if isinstance(raw_nodes, (list, tuple)) else [])
        )
        if text is not None
    )

    risk = {
        "passed": _flag(_pick(row, "risk_passed")),
        "reason": _text(_pick(row, "risk_reason")),
        "position_size": _number(_pick(row, "position_size")),
        "capital": _number(_pick(row, "capital")),
        "exposure": _number(_pick(row, "exposure")),
        "expected_loss": _number(_pick(row, "expected_loss")),
        "expected_reward": _number(_pick(row, "expected_reward")),
        "drawdown_check": _flag(_pick(row, "drawdown_check")),
        "evaluated_at": _text(_pick(row, "risk_evaluated_at")),
    }

    sizing = _pick(market_info, "sizing_intention")
    ml_info = _pick(row, "ml_info")
    state = reconcile_row_lifecycle_state(row) or OrderLifecycleState.GENERATED

    return Signal(
        id=_text(_pick(row, "id")) or "",
        user_id=_text(_pick(row, "user_id")) or "",
        strategy_id=_text(_pick(row, "strategy_id")) or "",
        strategy_version=_text(_pick(row, "strategy_version")) or "",
        symbol=_text(_pick(row, "symbol")) or "",
        generated_at=_text(_pick(row, "generated_at")) or "",
        decision=decision,
        signal_type=signal_type,
        side=side,
        strategy_version_id=_text(_pick(market_info, "strategy_version_id")),
        deployment_id=_text(_pick(row, "deployment_id")),
        exchange_account_id=_text(_pick(market_info, "exchange_account_id")),
        venue=_text(_pick(row, "exchange_id")),
        timeframe=_text(_pick(row, "timeframe")),
        worker_id=_text(_pick(row, "worker_id")),
        mode=_text(_pick(market_info, "mode")),
        quantity=_number(_pick(row, "quantity")),
        sizing_intention=_dict_of(sizing) or None if sizing is not None else None,
        source_node_ids=source_node_ids,
        node_closure=_dict_of(_pick(row, "indicators")),
        closure_ready=_flag(_pick(market_info, "closure_ready")),
        risk_validation={k: v for k, v in risk.items() if v is not None},
        ml_inference=_dict_of(ml_info) or None if ml_info is not None else None,
        market_context={
            key: value
            for key, value in market_info.items()
            if key not in _MARKET_INFO_ATTRIBUTION_KEYS
        },
        order_lifecycle_state=state,
        order_id=_text(_pick(row, "order_id")),
        execution_id=_text(_pick(row, "trade_id")),
    )


def _execution_outcome_of(row: Mapping[str, Any], state: OrderLifecycleState) -> Dict[str, Any]:
    """The order/execution facts Requirement 17.3 wants per row, from the row's own columns.

    Alongside :meth:`Signal.to_public_dict` rather than inside it, because these are not
    facts about the DECISION - they are what the venue did afterwards, they live in columns
    the frozen record has no field for, and the ``SIGNAL_FAMILY`` frame carries them
    separately too.

    ``failure_reason`` is reported only for a state that actually failed. ``risk_reason``
    is the only reason column ``public.signals`` has, so it is the source; on a
    ``REJECTED`` signal that is precisely the right text, and on ``FAILED``/``CANCELLED``
    it is the nearest thing persisted rather than a fabricated one.
    """
    failed = state in (
        OrderLifecycleState.FAILED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.CANCELLED,
    )
    return {
        "order_id": _text(_pick(row, "order_id")),
        "execution_id": _text(_pick(row, "trade_id")),
        "execution_price": _number(_pick(row, "average_price")),
        "filled_quantity": _number(_pick(row, "filled")),
        "remaining_quantity": _number(_pick(row, "remaining")),
        "fees": _number(_pick(row, "fees")),
        "slippage": _number(_pick(row, "slippage")),
        "latency_ms": _number(_pick(row, "latency_ms")),
        "pnl": _number(_pick(row, "pnl")),
        "realized_pnl": _number(_pick(row, "realized_pnl")),
        "failure_reason": _text(_pick(row, "risk_reason")) if failed else None,
        "order_updated_at": _text(_pick(row, "order_updated_at")),
        "executed_at": _text(_pick(row, "executed_at")),
    }


def signal_trace_item(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One row as one list entry: the public shape, plus the execution outcome.

    ``**to_public_dict()`` and not a hand-written subset, so a field added to the record
    appears here without this function being edited, and so no field this record does not
    have can appear (Requirements 15.3, 15.4, 20.3 - containment holds on the wire because
    there is nothing to omit).

    ``environment`` AND ``paper_session_id`` ARE ON THE ITEM, NOT ON THE RECORD (task 29.3)
        Both are columns of the ROW rather than fields of :class:`Signal` - a signal record
        is minted before it is attributed to a store, and ``Signal`` is frozen with a closed
        field set that 010 does not change. They are read here by name, exactly as
        :func:`_execution_outcome_of`'s columns are, for the same reason: they are facts the
        row carries and the record has no field for.

        Both keys are ALWAYS present, ``None`` on a pre-010 database. A reader therefore
        branches on a VALUE and never on whether a key exists, which is what lets one
        frontend render a labelled series per environment (Requirement 23.4) against either
        schema.
    """
    signal = signal_from_row(row)
    item = signal.to_public_dict()
    item["execution"] = _execution_outcome_of(row, signal.order_lifecycle_state)
    item["environment"] = _text(_pick(row, "environment"))
    item["paper_session_id"] = _text(_pick(row, "paper_session_id"))
    return item


async def build_signal_trace_page(
    service: "SignalService",
    user: Any,
    *,
    strategy_id: Optional[Any] = None,
    strategy_version: Optional[Any] = None,
    deployment_id: Optional[Any] = None,
    symbol: Optional[Any] = None,
    side: Optional[Any] = None,
    order_lifecycle_state: Optional[Any] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = SIGNAL_TRACE_PAGE_SIZE,
    offset: int = 0,
    exchange_id: Optional[Any] = None,
    worker_id: Optional[Any] = None,
    decision: Optional[Any] = None,
    status: Optional[Any] = None,
    ml_type: Optional[str] = None,
    search: Optional[str] = None,
    environment: Optional[Any] = None,
) -> Dict[str, Any]:
    """One page of ``GET /api/signal-trace/signals``. Requirements 17.1, 17.2, 17.5, 17.7.

    Returns
        ``{signals, limit, offset, count, total, total_is_exact, has_more, next_offset,
        filters_active, active_filters, lifecycle_state_source, degraded,
        environment_source, environment_filter, count_by_environment,
        environment_degraded}``.

        ``filters_active`` is Requirement 17.7's empty-state discriminator: an empty
        ``signals`` with ``filters_active=false`` means "no signals exist yet", and with
        ``filters_active=true`` means "none match the current filter". The page does not
        have to infer it from the query string it sent.

        The last four are task 29.3's, and they are all about labelling rather than
        filtering: ``count_by_environment`` is Requirement 23.4's explicit environment label
        on the page's own aggregate, ``environment_source`` says whether the label came from
        010's column at all, ``environment_filter`` echoes the resolved filter, and
        ``environment_degraded`` is non-null exactly when 010 has not been applied (see
        :func:`_environment_degradation` for what the filter does then, and why).

    Raises
        :class:`OrderLifecycleRejected` (400) when an ``order_lifecycle_state`` value is
        outside the canonical 9. Never silently ignored - see
        :func:`resolve_lifecycle_state_filter`.

        :class:`SignalEnvironmentRefused` (400) when an ``environment`` value is outside
        ``BACKTEST``/``PAPER``/``LIVE``, for the same reason and with the same disposition.
    """
    limit = max(1, min(int(limit), SIGNAL_TRACE_PAGE_SIZE))
    offset = max(0, int(offset))

    environments, unrecognised_environments = resolve_environment_filter(environment)
    if unrecognised_environments:
        raise SignalEnvironmentRefused(unrecognised_environments)

    states, unrecognised = resolve_lifecycle_state_filter(order_lifecycle_state)
    if unrecognised:
        raise OrderLifecycleRejected(
            "ORDER_LIFECYCLE_STATE_UNRECOGNISED",
            f"{list(unrecognised)} is not an Order_Lifecycle_State. The filter accepts "
            f"{list(ORDER_LIFECYCLE_STATE_VALUES)}; an unrecognised value is refused "
            "rather than ignored, because ignoring it would return signals the caller "
            "did not ask for.",
            {
                "unrecognised": list(unrecognised),
                "recognised_states": list(ORDER_LIFECYCLE_STATE_VALUES),
            },
            http_status=400,
        )

    sb = await service._get_supabase(user)
    with_canonical = await signal_lifecycle_columns_supported(sb) if sb is not None else False
    # The 010 verdict, asked once and cached, so the page can say where its environment
    # labels came from and can refuse to pretend an unanswerable filter was answered.
    with_environment = (
        await signal_environment_columns_supported(sb) if sb is not None else False
    )

    if environments and not with_environment:
        # The filter cannot be sent as a predicate, and neither of the two silent answers is
        # honest - see :func:`_environment_degradation`. An EMPTY page that says so.
        return _empty_signal_trace_page(
            limit=limit,
            offset=offset,
            filters_active=True,
            active_filters=("environment",),
            lifecycle_state_source="canonical" if with_canonical else "legacy_status_map",
            with_environment=False,
            environments=environments,
        )

    # 010's ``environment`` / ``paper_session_id`` are already in SIGNAL_TRACE_COLUMNS
    # (task 29.1). They are NOT stripped here: :meth:`SignalService.list_signals` asks the
    # 010 probe and strips them for a database without that migration, so the decision is
    # made once, at the statement, rather than twice with two chances to disagree.
    columns = SIGNAL_TRACE_COLUMNS
    canonical_filter: Tuple[str, ...] = ()
    legacy_state_filter: Tuple[str, ...] = ()
    unsupported: Tuple[OrderLifecycleState, ...] = ()

    if with_canonical:
        columns = "order_lifecycle_state," + SIGNAL_TRACE_COLUMNS
        canonical_filter = tuple(state.value for state in states)
    elif states:
        legacy_state_filter, unsupported = legacy_status_filter_for(states)
        warn_signal_lifecycle_columns_absent(
            "an order_lifecycle_state filter was requested on the signal-trace list, so it "
            f"is being answered from the legacy status column via SIGNALS_STATUS_MAP "
            f"({list(legacy_state_filter)})"
            + (
                f"; {[s.value for s in unsupported]} has no legacy spelling and cannot be "
                "answered at all"
                if unsupported
                else ""
            )
        )
        if not legacy_state_filter:
            # Every requested state is one the legacy vocabulary cannot express. Returning
            # the unfiltered list would be worse than returning none: it would answer a
            # question nobody asked.
            return _empty_signal_trace_page(
                limit=limit,
                offset=offset,
                filters_active=True,
                active_filters=("order_lifecycle_state",),
                lifecycle_state_source="legacy_status_map",
                unsupported=unsupported,
                with_environment=with_environment,
                environments=environments,
            )

    # `side` is filtered on `decision`; see this section's header. Combined with an
    # explicit `decision` filter (which the pre-spec page still sends) by intersection,
    # because both name the same column and Requirement 17.2 ANDs distinct categories.
    side_values = _filter_values(side)
    decision_values = _filter_values(decision)
    if side_values and decision_values:
        decision_filter: Tuple[str, ...] = tuple(
            value for value in decision_values if value in side_values
        )
        if not decision_filter:
            return _empty_signal_trace_page(
                limit=limit,
                offset=offset,
                filters_active=True,
                active_filters=("side", "decision"),
                lifecycle_state_source="canonical" if with_canonical else "legacy_status_map",
                unsupported=unsupported,
                with_environment=with_environment,
                environments=environments,
            )
    else:
        decision_filter = side_values or decision_values

    status_filter = _filter_values(status) or legacy_state_filter

    active_filters = tuple(
        name
        for name, values in (
            ("strategy_id", _filter_values(strategy_id)),
            ("strategy_version", _filter_values(strategy_version)),
            ("deployment_id", _filter_values(deployment_id)),
            ("symbol", _filter_values(symbol)),
            ("side", decision_filter),
            ("order_lifecycle_state", canonical_filter or legacy_state_filter),
            ("exchange_id", _filter_values(exchange_id)),
            ("worker_id", _filter_values(worker_id)),
            ("status", _filter_values(status)),
            ("date_from", _filter_values(date_from)),
            ("date_to", _filter_values(date_to)),
            ("ml_type", _filter_values(ml_type)),
            ("search", _filter_values(search)),
            ("environment", environments),
        )
        if values
    )

    # limit+1: the extra row is never returned, its existence IS has_more.
    rows = await service.list_signals(
        user,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        exchange_id=exchange_id,
        symbol=symbol,
        worker_id=worker_id,
        deployment_id=deployment_id,
        decision=decision_filter or None,
        status=status_filter or None,
        order_lifecycle_state=canonical_filter or None,
        ml_type=ml_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        limit=limit + 1,
        offset=offset,
        columns=columns,
        environment=environments or None,
    )
    rows = list(rows or [])
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [signal_trace_item(row) for row in page]

    return {
        "signals": items,
        "limit": limit,
        "offset": offset,
        "count": len(page),
        # A LOWER BOUND, and it says so. No COUNT was issued.
        "total": offset + len(page) + (1 if has_more else 0),
        "total_is_exact": False,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
        "filters_active": bool(active_filters),
        "active_filters": list(active_filters),
        "lifecycle_state_source": "canonical" if with_canonical else "legacy_status_map",
        "degraded": None
        if with_canonical
        else {
            "migration": SIGNAL_LIFECYCLE_MIGRATION,
            "reason": (
                "public.signals does not carry order_lifecycle_state, so the reported "
                "state is reconciled from the legacy status column through "
                "SIGNALS_STATUS_MAP (Requirement 16.2)."
            ),
            "unsupported_lifecycle_states": [state.value for state in unsupported],
        },
        # ── task 29.3: the environment label, and where it came from ──────────
        "environment_source": ENVIRONMENT_SOURCE_COLUMN
        if with_environment
        else ENVIRONMENT_SOURCE_UNAVAILABLE,
        "environment_filter": list(environments),
        "count_by_environment": environment_counts(items),
        "environment_degraded": _environment_degradation(with_environment, environments),
    }


def _empty_signal_trace_page(
    *,
    limit: int,
    offset: int,
    filters_active: bool,
    active_filters: Tuple[str, ...],
    lifecycle_state_source: str,
    unsupported: Tuple[OrderLifecycleState, ...] = (),
    with_environment: bool = False,
    environments: Tuple[str, ...] = (),
) -> Dict[str, Any]:
    """A page that is empty because the filter cannot match, without querying for it.

    Same envelope as :func:`build_signal_trace_page`, so Requirement 17.7's empty state
    reads one shape whether the emptiness came from the database or from the filter being
    unanswerable. ``count_by_environment`` is ``{}`` here for the same reason ``signals`` is
    ``[]``: there is nothing to count, which is not the same statement as "you have none in
    that environment" - ``environment_degraded`` is what distinguishes the two.
    """
    return {
        "signals": [],
        "limit": limit,
        "offset": offset,
        "count": 0,
        "total": offset,
        "total_is_exact": False,
        "has_more": False,
        "next_offset": None,
        "filters_active": filters_active,
        "active_filters": list(active_filters),
        "lifecycle_state_source": lifecycle_state_source,
        "degraded": None
        if lifecycle_state_source == "canonical"
        else {
            "migration": SIGNAL_LIFECYCLE_MIGRATION,
            "reason": (
                "public.signals does not carry order_lifecycle_state, so the reported "
                "state is reconciled from the legacy status column through "
                "SIGNALS_STATUS_MAP (Requirement 16.2)."
            ),
            "unsupported_lifecycle_states": [state.value for state in unsupported],
        },
        "environment_source": ENVIRONMENT_SOURCE_COLUMN
        if with_environment
        else ENVIRONMENT_SOURCE_UNAVAILABLE,
        "environment_filter": list(environments),
        "count_by_environment": {},
        "environment_degraded": _environment_degradation(with_environment, environments),
    }


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 13.2 - the signal-trace DETAIL: one signal's full trace
#
#  Spec: trading-lifecycle-integration. design.md's API table:
#  "GET /api/signal-trace/signals/{signal_id} | One signal's full trace |
#  DAG node trace + ML inference + risk validation + execution outcome, read
#  from signal_trace_engine joined with the persisted signals row."
#  Requirements 17.6, 16.7, 20.1, 20.2, 20.3.
#
#  WHAT REQUIREMENT 17.6 ASKS FOR, AND WHERE EACH PART COMES FROM
#  --------------------------------------------------------------
#  Requirement 17.6 names exactly four things. Each has a persisted source and
#  an in-memory one, and this section reports WHICH it used rather than
#  blending them:
#
#    17.6 asks for        signal_trace_engine        public signals row
#    ------------------------------------------------------------------------
#    DAG node trace       SignalTraceRecord          indicators (node_closure,
#                         .node_traces               keyed by node id) +
#                                                    market_info.source_node_ids
#    ML inference         .ml_trace                  ml_info
#    risk validation      .risk_trace                risk_* columns
#    execution outcome    .execution_trace           order_*/trade/fill columns
#
#  THE ROW IS THE JOIN'S DRIVING SIDE, AND THAT IS AN OWNERSHIP DECISION
#  --------------------------------------------------------------------
#  signal_trace_engine is a PROCESS-LOCAL, in-memory store with a one-hour
#  retention window (SignalTraceEngine(retention_seconds=3600)) and NO owner
#  column on SignalTraceRecord: it is keyed by trace_id and indexed by
#  strategy_id, and it never knew which user a trace belongs to. So it is read
#  ONLY AFTER the owner-scoped SELECT on public.signals has returned a row -
#  exactly the disposition strategy_operations._signal_provenance already takes
#  for the same store, and for the same reason. The ownership decision stays
#  where RLS plus the explicit user_id predicate already make it (Requirements
#  20.1, 20.2); nothing about this endpoint's answer is derived from a store
#  that cannot enforce it.
#
#  A consequence worth stating: a signal older than the retention window, or
#  one generated by a worker process that has since exited, has NO engine
#  record. That is the COMMON case for an audit page, not an error, so the
#  trace degrades to the row's own persisted trace and says so through
#  `source` on each section. An empty detail view for a signal that really
#  exists would be the wrong way to be wrong.
#
#  REQUIREMENT 16.7's HISTORY IS READ FROM THE LOG, NOT RECONSTRUCTED
#  -----------------------------------------------------------------
#  design.md settles the 002-vs-003 disagreement by making the transition
#  history a PERSISTED store, "not an in-memory reconstruction". So
#  `lifecycle_transitions` is a chronological read of
#  order_lifecycle_transitions ordered by occurred_at - the index
#  idx_olt_signal_time exists for exactly this query - and NOT a derivation
#  from timestamp columns. The pre-spec `timeline` key (which IS such a
#  derivation) is retained beside it, unchanged, because SignalTrace.jsx
#  renders it today and task 19 is what re-points the page; the two are not
#  merged, because one is an audit record and the other is a reconstruction and
#  a reader is entitled to know which it is looking at.
#
#  005b's ABSENCE DEGRADES, EXACTLY AS ON THE LIST (task 13.1's contract)
#  ---------------------------------------------------------------------
#  Same three-part disposition, never a 500:
#
#    * the row is read with SELECT * rather than a named projection, so a
#      database without 005b cannot 400 on a column that does not exist -
#      unlike the list, the detail has nothing to FILTER on, so it needs no
#      projection and can afford the wildcard;
#    * the reported state comes from reconcile_row_lifecycle_state either way,
#      so the legacy status column answers when the canonical one is absent;
#    * `lifecycle_state_source` and `degraded` say which happened, the warning
#      names 005b_signal_lifecycle_and_idempotency.sql, and
#      `degraded.unrepresentable_lifecycle_states` names the three canonical
#      states the legacy vocabulary cannot express at all (task 13.1's finding:
#      GENERATED, PARTIALLY_EXECUTED, CLOSED). On the LIST that fact makes a
#      filter unanswerable; HERE it makes a reported state possibly coarser
#      than the truth - a signal that is really GENERATED reads as PENDING off
#      status='pending' - and saying so is the only honest option, because the
#      alternative is a page that presents a reconciled guess as a fact.
#
#    Section 2's table degrades independently and the same way: an absent
#    order_lifecycle_transitions means `lifecycle_transitions.available` is
#    false with a named migration, not an error.
#
#  CREDENTIAL CONTAINMENT ON A STORE WITH A FREE-FORM FIELD (Req 20.3)
#  ------------------------------------------------------------------
#  On the list, containment is structural: Signal has a closed field set, so
#  to_public_dict cannot leak. That argument does NOT carry over to the engine
#  record, which has two genuinely open fields - SignalTraceRecord.metadata
#  (Dict[str, Any], written by whatever called start_trace) and NodeIO.metadata.
#  Neither is projected here. Every other field is read by NAME, so this
#  section's projection is closed in the same way the record's is, and a field
#  added to SignalTraceRecord later does not silently appear on the wire.
#  (RiskValidationTrace, MLInferenceTrace and ExecutionTrace ARE projected
#  whole, through _dict_of, because all three are closed dataclasses of
#  scalars with no free-form member - and Requirement 17.6 asks for their
#  detail, so a hand-picked subset would just be a second list to keep in sync.)
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


#: How many of a strategy's recent engine traces to scan for one signal's record. The
#: store retains an hour and is indexed by strategy_id, not by signal_id, so the lookup is
#: a bounded scan of that index. Bounded rather than unbounded for the reason
#: strategy_operations.SIGNAL_TRACE_RECORD_LIMIT is: an audit read must not put the whole
#: retention window of a busy strategy on the heap to answer a question about one signal.
SIGNAL_TRACE_RECORD_SCAN_LIMIT = 500

#: Where a section of the trace was read from. Reported per section so a reader can tell
#: the DAG's own observability record from the row it was persisted into.
TRACE_SOURCE_ENGINE = "signal_trace_engine"
TRACE_SOURCE_ROW = "signals_row"


# ══════════════════════════════════════════════════════════════════════════
#
#  BC-6 (vyomquant-ui-redesign task 12.6) - STAGE 9, THE POSITION CHANGE
#
#  Requirements 9.1, 9.2, 19.1, 19.2. design.md §10.1's stage table, §16's
#  BC-6 row.
#
#  THE GAP. Requirement 9.1 names nine stages. Eight had a backing record and
#  the ninth - "position update" - had none: `PUT /signals/{id}/execution`
#  accepts `trade_id`, `pnl` and `realized_pnl`, which is a P&L OUTCOME, not a
#  position TRANSITION. So the page's last stage rendered permanently
#  not-available.
#
#  WHY A SIXTH DERIVED EVENT RATHER THAN A NEW STORE. This timeline is a
#  DERIVATION from the persisted `public.signals` row (see the docstring
#  below); it is not an append log. Extending the derivation buys three things
#  a parallel write would not:
#
#    * EXACTLY-ONCE FOR FREE. A derivation from a column cannot duplicate, so
#      a repeated `PUT .../execution` - which overwrites `executed_at` - still
#      yields exactly one POSITION_UPDATED. An append-on-write design would
#      need its own idempotency guard on a production execution path.
#    * NO NEW FAILURE MODE ON THE EXECUTION WRITE. `update_execution` gains no
#      second statement that could fail after the first one committed.
#    * NO RETENTION BOUND. `signal_trace_engine` is in-memory with
#      `retention_seconds=3600`, which is why design.md §10.1 has stage 4
#      degrade after an hour. Deriving stage 9 from the ROW instead means it
#      lasts as long as the signal does.
#
#  WHAT IT MAY AND MAY NOT CLAIM. The row records the position CHANGE - the
#  instrument, the direction the signal decided, the quantity that filled and
#  the price it filled at. It records NO absolute holding: nothing in this
#  domain reports the position a signal left behind. `position_size` is the
#  risk-approved INTENDED size from `update_risk_decision`, not an outcome, and
#  is deliberately not read here. So `resulting_position` is reported as
#  not-available WITH ITS REASON rather than reconstructed from the change. An
#  event asserting a holding nobody reported would be worse than a missing
#  stage (Requirement 19.2).
#
# ══════════════════════════════════════════════════════════════════════════

#: The sixth ``timeline`` event type. Named as a constant so the router, the service and
#: the tests spell it in exactly one place - the discipline `MANUAL_RECONCILIATION_EVENT`
#: already gets. There is no enum to extend and no collision to avoid: the other five names
#: were inline string literals in :func:`signal_event_timeline` and remain byte-identical,
#: and ``public.signal_events.event_type`` (a SEPARATE, persisted vocabulary whose only
#: member this repository writes is ``MANUAL_RECONCILIATION_REQUIRED``) is untouched.
POSITION_UPDATED_EVENT = "POSITION_UPDATED"

#: Every ``timeline`` event type this function can emit, in derivation order. Declared so a
#: consumer (and design.md §10.1's stage table) can name the vocabulary instead of
#: re-deriving it from the source. The first five are the pre-spec five, in their pre-spec
#: order; BC-6 appends the sixth and reorders nothing.
SIGNAL_TIMELINE_EVENTS = (
    "SIGNAL_GENERATED",
    "RISK_EVALUATED",
    "ORDER_CREATED",
    "EXCHANGE_RESPONSE",
    "EXECUTED",
    POSITION_UPDATED_EVENT,
)

#: The members of ``POSITION_UPDATED.data`` that describe the transition itself. Any one of
#: them the row did not report is named in ``not_available`` rather than defaulted, because
#: a zero fill and an unreported fill are different facts about a trader's position.
POSITION_TRANSITION_FIELDS = ("symbol", "direction", "quantity_delta", "average_price")

#: The absolute holding the change produced. ALWAYS ``None`` on every current database, and
#: always named in ``not_available``: no column, no request field and no other record in
#: this domain carries it. Kept as a present key so a consumer destructures a declared
#: absence instead of a missing one (Requirement 19.2).
POSITION_RESULTING_FIELD = "resulting_position"

#: Why ``resulting_position`` is not available. One sentence, on the event, so the page
#: renders the server's own reason rather than a hardcoded frontend string.
POSITION_RESULTING_UNREPORTED_REASON = (
    "The execution update reports the position CHANGE only - instrument, direction, filled "
    "quantity and fill price. No record in the signal-trace domain carries the absolute "
    "position a signal left behind, so it is reported as not available rather than "
    "reconstructed from the change."
)


def _position_updated_event(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """BC-6's ``POSITION_UPDATED``, or ``None`` when no execution was recorded.

    THE GATE IS ``executed_at``, WHICH IS ``EXECUTED``'S OWN GATE
        Deliberately the same column, so the two events are structurally paired: this event
        exists if and only if ``EXECUTED`` does. That is what makes design.md §10.1's
        "exactly one POSITION_UPDATED after EXECUTED" and "none for a signal that never
        executed" properties of the shape rather than of a code path - and it is why the
        event cannot be emitted speculatively, since ``executed_at`` is written only by
        :meth:`SignalService.update_execution`, at the moment the execution is recorded.
    """
    executed_at = row.get("executed_at")
    if not executed_at:
        return None

    # Only what the row actually reports. `filled` and `average_price` are the order
    # update's own record of what moved and at what price; `decision` is the direction the
    # signal committed to. Nothing here is defaulted - `.get` returning None means "not
    # reported", and that is carried through to `not_available` below.
    data: Dict[str, Any] = {
        "symbol": row.get("symbol"),
        "direction": row.get("decision"),
        "quantity_delta": row.get("filled"),
        "average_price": row.get("average_price"),
        # Repeated from EXECUTED rather than moved off it: Requirement 19.1 forbids changing
        # an existing event's payload, and a stage-9 reader should not have to join to
        # stage 8 to know which trade the change belongs to.
        "trade_id": row.get("trade_id"),
        "realized_pnl": row.get("realized_pnl"),
        POSITION_RESULTING_FIELD: None,
    }

    unavailable = [field for field in POSITION_TRANSITION_FIELDS if data.get(field) is None]
    # The absolute holding is unavailable on every current database, so it is appended
    # unconditionally rather than tested for.
    unavailable.append(POSITION_RESULTING_FIELD)

    data["not_available"] = unavailable
    data["not_available_reason"] = POSITION_RESULTING_UNREPORTED_REASON
    return {
        "event": POSITION_UPDATED_EVENT,
        "timestamp": executed_at,
        "data": data,
    }


def signal_event_timeline(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The pre-spec event timeline for one ``public.signals`` row, chronologically.

    Lifted out of :meth:`SignalService.get_signal_timeline` unchanged in behaviour, so the
    detail view can build it from a row it already holds. A DERIVATION from timestamp
    columns, not an audit record - :func:`load_lifecycle_transitions` is the audit record
    (Requirement 16.7) and the two are reported separately for that reason.

    BC-6 (task 12.6) appends a sixth event type, ``POSITION_UPDATED``, for Requirement
    9.1's stage 9. Purely additive: the five pre-spec events keep their names, their
    gates, their order and their ``data`` payloads byte-for-byte, and the new event is
    appended AFTER ``EXECUTED`` carrying ``EXECUTED``'s own timestamp - the sort below is
    stable, so equal timestamps keep insertion order and stage 9 can never sort ahead of
    stage 8. See :func:`_position_updated_event`.
    """
    timeline: List[Dict[str, Any]] = [
        {
            "event": "SIGNAL_GENERATED",
            "timestamp": row.get("generated_at"),
            "data": {
                "decision": row.get("decision"),
                "indicators": row.get("indicators"),
                "market_info": row.get("market_info"),
            },
        }
    ]

    if row.get("risk_evaluated_at"):
        timeline.append(
            {
                "event": "RISK_EVALUATED",
                "timestamp": row.get("risk_evaluated_at"),
                "data": {
                    "risk_passed": row.get("risk_passed"),
                    "risk_reason": row.get("risk_reason"),
                    "position_size": row.get("position_size"),
                },
            }
        )

    if row.get("order_id"):
        timeline.append(
            {
                "event": "ORDER_CREATED",
                "timestamp": row.get("order_updated_at") or row.get("risk_evaluated_at"),
                "data": {"order_id": row.get("order_id"), "quantity": row.get("quantity")},
            }
        )

    if row.get("exchange_order_id"):
        timeline.append(
            {
                "event": "EXCHANGE_RESPONSE",
                "timestamp": row.get("order_updated_at"),
                "data": {
                    "exchange_order_id": row.get("exchange_order_id"),
                    "order_status": row.get("order_status"),
                },
            }
        )

    if row.get("executed_at"):
        timeline.append(
            {
                "event": "EXECUTED",
                "timestamp": row.get("executed_at"),
                "data": {"trade_id": row.get("trade_id"), "pnl": row.get("pnl")},
            }
        )

    # BC-6's stage 9, appended immediately after stage 8 and gated on the same column, so
    # `EXECUTED` and `POSITION_UPDATED` are present or absent together.
    position_updated = _position_updated_event(row)
    if position_updated is not None:
        timeline.append(position_updated)

    # STABLE, so the append order above survives equal timestamps. `POSITION_UPDATED`
    # carries `executed_at` - the same value `EXECUTED` carries - and Python's sort is
    # stable, which is what keeps stage 9 after stage 8 without inventing a later
    # timestamp for it.
    timeline.sort(key=lambda event: event.get("timestamp") or "")
    return timeline


def _dag_node_entry(
    *,
    node_id: str,
    source: str,
    node_type: Optional[str] = None,
    node_label: Optional[str] = None,
    status: Optional[str] = None,
    execution_ms: Optional[float] = None,
    error: Optional[str] = None,
    cache_hit: Optional[bool] = None,
    retry_count: Optional[int] = None,
    inputs: Optional[List[Dict[str, Any]]] = None,
    outputs: Optional[List[Dict[str, Any]]] = None,
    reading: Any = None,
    is_source_node: bool = False,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
) -> Dict[str, Any]:
    """One node of the DAG node trace, in the ONE shape both sources render into.

    The engine's record and the row's persisted ``node_closure`` describe the same node
    with different amounts of detail - timing and per-port I/O only the live engine saw,
    the evaluated ``reading`` only the row kept. Emitting one key set with the absent half
    as ``None``/``[]`` means the page renders one component either way and can tell which
    it got from ``source``, instead of branching on two response shapes.
    """
    return {
        "node_id": node_id,
        "source": source,
        "node_type": node_type,
        "node_label": node_label,
        "status": status,
        "execution_ms": execution_ms,
        "error": error,
        "cache_hit": cache_hit,
        "retry_count": retry_count,
        "inputs": inputs or [],
        "outputs": outputs or [],
        "reading": reading,
        "is_source_node": is_source_node,
        "started_at": started_at,
        "ended_at": ended_at,
    }


def _node_io(io: Any) -> Dict[str, Any]:
    """One ``signal_trace_engine.NodeIO`` as four named keys.

    ``metadata`` is deliberately NOT among them: it is a free-form ``Dict[str, Any]``
    filled by whatever recorded the port, and this section's containment argument is that
    every field on the wire is read by name (see the header). ``value`` goes through
    ``_jsonable``, which is where the depth/width/length bounds and the
    ``Decimal``/``datetime``/NaN handling already live.
    """
    return {
        "key": _text(_pick(io, "key")),
        "value": _jsonable(_pick(io, "value")),
        "dtype": _text(_pick(io, "dtype")),
        "shape": _jsonable(_pick(io, "shape")),
    }


def _dag_nodes_from_record(record: Any) -> List[Dict[str, Any]]:
    """The engine record's ``node_traces``, projected. Requirement 17.6's DAG node trace."""
    nodes: List[Dict[str, Any]] = []
    for trace in getattr(record, "node_traces", None) or ():
        node_id = _text(_pick(trace, "node_id"))
        if node_id is None:
            continue
        nodes.append(
            _dag_node_entry(
                node_id=node_id,
                source=TRACE_SOURCE_ENGINE,
                # NodeType and ValidationResult members; _text unwraps an Enum to its
                # wire string, and a datetime to ISO-8601 (which is what the two
                # timestamps below rely on).
                node_type=_text(_pick(trace, "node_type")),
                node_label=_text(_pick(trace, "node_label")),
                status=_text(_pick(trace, "status")),
                execution_ms=_number(_pick(trace, "execution_ms")),
                error=_text(_pick(trace, "error_message")),
                cache_hit=_flag(_pick(trace, "cache_hit")),
                retry_count=int(_number(_pick(trace, "retry_count")) or 0),
                inputs=[_node_io(io) for io in (getattr(trace, "inputs", None) or ())],
                outputs=[_node_io(io) for io in (getattr(trace, "outputs", None) or ())],
                started_at=_text(_pick(trace, "start_time")),
                ended_at=_text(_pick(trace, "end_time")),
            )
        )
    return nodes


def _dag_nodes_from_row(row: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The persisted closure as a DAG node trace, for a signal with no engine record.

    ``signals.indicators`` holds :attr:`Signal.node_closure` - "the evaluated state of
    every upstream node in the emitting action's closure, keyed by node id" - and
    ``market_info.source_node_ids`` names the ACTION node(s) that emitted the decision. So
    the row does carry a node-level trace of the evaluation; what it does not carry is
    per-node timing or per-port I/O, which is why those keys come back ``None`` rather
    than being invented.
    """
    closure = _dict_of(_pick(row, "indicators"))
    market_info = _dict_of(_pick(row, "market_info"))
    raw_sources = _pick(market_info, "source_node_ids")
    source_ids = {
        text
        for text in (
            _text(node)
            for node in (raw_sources if isinstance(raw_sources, (list, tuple)) else ())
        )
        if text is not None
    }

    nodes = [
        _dag_node_entry(
            node_id=str(node_id),
            source=TRACE_SOURCE_ROW,
            reading=reading,
            is_source_node=str(node_id) in source_ids,
        )
        for node_id, reading in closure.items()
    ]
    # An emitting ACTION node whose own reading was not part of the closure still belongs
    # in the trace: it is the node the decision came FROM.
    for node_id in sorted(source_ids - set(closure)):
        nodes.append(
            _dag_node_entry(node_id=node_id, source=TRACE_SOURCE_ROW, is_source_node=True)
        )
    return nodes


def _dag_node_section(record: Any, row: Mapping[str, Any]) -> Dict[str, Any]:
    """Requirement 17.6's DAG node trace, from the engine where it has one."""
    nodes = _dag_nodes_from_record(record) if record is not None else []
    if nodes:
        return {"source": TRACE_SOURCE_ENGINE, "nodes": nodes}
    return {"source": TRACE_SOURCE_ROW, "nodes": _dag_nodes_from_row(row)}


def _ml_inference_section(record: Any, signal: Signal) -> Dict[str, Any]:
    """Requirement 17.6's ML inference detail, "where applicable".

    ``applicable`` is the tri-state Requirement 17.6's parenthetical asks for: an ML
    section is not MISSING for a rule-based strategy version, it does not apply, and a
    page that cannot tell those apart shows an empty ML panel for a strategy that has no
    ML node. ``Signal.ml_inference`` is ``None`` for exactly that case (its own docstring:
    "which is a fact about the strategy, not a missing value").
    """
    ml_trace = getattr(record, "ml_trace", None) if record is not None else None
    if ml_trace is not None:
        return {
            "source": TRACE_SOURCE_ENGINE,
            "applicable": True,
            "detail": _dict_of(ml_trace),
        }
    if signal.ml_inference:
        return {
            "source": TRACE_SOURCE_ROW,
            "applicable": True,
            "detail": dict(signal.ml_inference),
        }
    return {"source": TRACE_SOURCE_ROW, "applicable": False, "detail": None}


def _risk_validation_section(record: Any, signal: Signal) -> Dict[str, Any]:
    """Requirement 17.6's risk validation detail.

    The engine's ``RiskValidationTrace`` carries the checks performed, the limits they were
    measured against and the block reason; the row carries the verdict and its numbers
    (``signal_from_row``'s own docstring notes ``checks`` and ``blocked`` have no column).
    So when both exist the engine's detail is reported and the row's verdict is merged
    UNDER it - the persisted verdict cannot be overwritten by an in-memory one, and the
    fields only the engine saw are not lost.
    """
    risk_trace = getattr(record, "risk_trace", None) if record is not None else None
    persisted = dict(signal.risk_validation or {})
    if risk_trace is not None:
        detail = _dict_of(risk_trace)
        detail.update(persisted)
        return {"source": TRACE_SOURCE_ENGINE, "detail": detail}
    return {"source": TRACE_SOURCE_ROW, "detail": persisted}


def _execution_section(record: Any, row: Mapping[str, Any], state: OrderLifecycleState) -> Dict[str, Any]:
    """Requirement 17.6's execution outcome.

    ``outcome`` is task 13.1's ``_execution_outcome_of`` - the same projection the list
    renders, not a second one - because the row's columns are the authoritative record of
    what the venue did. The engine's ``ExecutionTrace`` is reported ALONGSIDE it as
    ``exchange_response``, never merged into it: it is what one process observed at
    submission time, and on a signal whose fills arrived later the two legitimately
    disagree. Labelling them is the honest way to show both.
    """
    exec_trace = getattr(record, "execution_trace", None) if record is not None else None
    return {
        "source": TRACE_SOURCE_ROW,
        "outcome": _execution_outcome_of(row, state),
        "exchange_response": _dict_of(exec_trace) if exec_trace is not None else None,
    }


async def load_signal_trace_record(signal_id: str, strategy_id: Optional[str]) -> Any:
    """``signal_trace_engine``'s record for ``signal_id``, or ``None``.

    READ ONLY AFTER OWNERSHIP IS SETTLED. ``SignalTraceRecord`` has no owner column and
    the store is indexed by ``strategy_id``, so this is called only with a ``strategy_id``
    that came off a row the owner-scoped SELECT already returned (Requirements 20.1, 20.2).
    Called with no ``strategy_id`` it reads nothing at all rather than scanning the
    process-global store, because that scan would be a read across every user's traces.

    Total, like ``strategy_operations._signal_provenance``: an engine that was never
    started, an unimportable module and a record shaped differently from the dataclass all
    report "no record". A trace detail must not 500 because an in-memory diagnostic could
    not be read - the persisted row is the answer either way.
    """
    if not strategy_id:
        return None
    try:
        from backend_app.backend.signal_trace_engine import trace_engine

        records = await trace_engine.get_recent_traces(
            strategy_id=strategy_id, limit=SIGNAL_TRACE_RECORD_SCAN_LIMIT
        )
    except Exception as exc:  # noqa: BLE001 - defensive; the store is in-process
        logger.info("Signal trace store unreadable for signal %s: %s", signal_id, exc)
        return None

    wanted = str(signal_id)
    for record in records or ():
        try:
            if str(getattr(record, "signal_id", "")) == wanted:
                return record
        except Exception:  # noqa: BLE001 - a malformed record is skipped, never fatal
            continue
    return None


async def load_lifecycle_transitions(
    sb: Any, *, signal_id: str, user_id: str
) -> Dict[str, Any]:
    """``signal_id``'s Order_Lifecycle_State transition history. Requirement 16.7.

    "Retrievable in chronological order by timestamp", ordered by ``occurred_at`` and
    served by 005b section 2's ``idx_olt_signal_time``. Owner-scoped twice, like every
    other read on this path: RLS on the caller's own client, plus an explicit ``user_id``
    predicate.

    Returns
        ``{available, transitions, degraded}``. ``available`` is ``False`` - never an
        error - when 005b section 2 has not been applied or the log could not be read,
        because Requirement 16.7 conditions the history on the audit store being available
        and a signal's detail view is still correct without it.
    """
    def unavailable(reason: str) -> Dict[str, Any]:
        """One shape for "no history", stated once, so all four reasons read the same."""
        return {
            "available": False,
            "transitions": [],
            "degraded": {"migration": SIGNAL_LIFECYCLE_MIGRATION, "reason": reason},
        }

    if sb is None:
        return unavailable("No database client was available to read the transition log.")
    if not _transitions_table_available():
        return unavailable(
            f"public.{ORDER_LIFECYCLE_TRANSITIONS_TABLE} is absent, so Requirement 16.7's "
            "transition history was never recorded for this signal."
        )

    try:
        result = await _execute(
            sb.table(ORDER_LIFECYCLE_TRANSITIONS_TABLE)
            .select("from_state,to_state,reason,occurred_at")
            .eq("signal_id", signal_id)
            .eq("user_id", user_id)
            .order("occurred_at")
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified, never swallowed blindly
        if is_missing_transitions_table_error(exc):
            remember_transitions_table_absent()
            warn_transitions_table_absent(str(exc))
            return unavailable(
                f"public.{ORDER_LIFECYCLE_TRANSITIONS_TABLE} is absent, so Requirement "
                "16.7's transition history was never recorded for this signal."
            )
        logger.warning(
            "Signal %s's transition history could not be read (%s); the trace detail is "
            "reported without it.",
            signal_id,
            exc,
        )
        return unavailable(f"The transition history could not be read: {exc}")

    error = getattr(result, "error", None)
    if error is not None:
        logger.warning(
            "Signal %s's transition history could not be read (%s); the trace detail is "
            "reported without it.",
            signal_id,
            error,
        )
        return unavailable(f"The transition history could not be read: {error}")

    data = getattr(result, "data", None)
    rows = data if isinstance(data, list) else ([data] if data else [])
    transitions = [
        {
            "from_state": _text(_pick(row, "from_state")),
            "to_state": _text(_pick(row, "to_state")),
            "reason": _text(_pick(row, "reason")),
            # _text unwraps a datetime to ISO-8601, so a driver that returns the column
            # as a datetime and one that returns it as text spell the instant identically.
            "occurred_at": _text(_pick(row, "occurred_at")),
        }
        for row in rows
    ]
    # Ordered here as well as in the query: Requirement 16.7's guarantee is about the
    # ANSWER, and a client that does not honour the ordering hint must not turn a
    # chronological history into an arbitrary one.
    transitions.sort(key=lambda t: t.get("occurred_at") or "")
    return {"available": True, "transitions": transitions, "degraded": None}


def _trace_identity(record: Any) -> Dict[str, Any]:
    """The engine record's own identity and timing. Every key present, ``None`` when absent.

    ``available`` is the discriminator: ``False`` means ``signal_trace_engine`` held no
    record for this signal - which for a signal older than that store's one-hour retention,
    or one generated by a worker process that has since exited, is the NORMAL case and not
    an error. The four sections below it still answer, from the row.

    Every key is emitted either way, so the page reads one shape and branches on
    ``available`` rather than on whether a key exists.
    """
    if record is None:
        return {
            "available": False,
            "trace_id": None,
            "status": None,
            "final_decision": None,
            "created_at": None,
            "started_at": None,
            "completed_at": None,
            "total_latency_ms": None,
            "errors": [],
        }
    return {
        "available": True,
        "trace_id": _text(_pick(record, "trace_id")),
        # A TraceStatus member; _text unwraps an Enum to its wire string.
        "status": _text(_pick(record, "status")),
        "final_decision": _text(_pick(record, "final_decision")),
        "created_at": _text(_pick(record, "created_at")),
        "started_at": _text(_pick(record, "started_at")),
        "completed_at": _text(_pick(record, "completed_at")),
        "total_latency_ms": _number(_pick(record, "total_latency_ms")),
        "errors": _jsonable(list(getattr(record, "errors", None) or ())),
    }


def _lifecycle_degradation(with_canonical: bool) -> Optional[Dict[str, Any]]:
    """The detail view's 005b degradation block. ``None`` when the migration is applied."""
    if with_canonical:
        return None
    return {
        "migration": SIGNAL_LIFECYCLE_MIGRATION,
        "reason": (
            "public.signals does not carry order_lifecycle_state, so the reported state is "
            "reconciled from the legacy status column through SIGNALS_STATUS_MAP "
            "(Requirement 16.2)."
        ),
        # On the list this makes a FILTER unanswerable; here it makes the reported state
        # possibly coarser than the truth, which the page has to be able to say.
        "unrepresentable_lifecycle_states": [
            state.value for state in LIFECYCLE_STATES_WITHOUT_LEGACY_SPELLING
        ],
    }


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 29.4 - the SUBSCRIBER-SAFE projection of one signal's detail
#
#  Spec: marketplace-subscriptions-paper-trading. Requirements 7.9, 23.2, 23.3
#  (and 19.7's disclosure rule, applied to a REST body).
#
#  WHO THIS IS ABOUT, CONCRETELY
#  -----------------------------
#  A subscriber buys a Listing and runs the creator's strategy in their own
#  Paper_Session. Every signal that session records is written with
#  `user_id` = THE SUBSCRIBER (they ran it) and `strategy_id` = THE CREATOR'S
#  strategy (they wrote it). So the caller who reads it is the owner of the
#  SIGNAL and not the owner of the STRATEGY - and the full trace detail of
#  that signal is a description of the creator's Protected_Logic: node ids,
#  per-node indicator readings, ML inference output and risk-rule internals.
#  Requirement 23.3 restricts exactly that reader to Requirement 23.2's field
#  list, and Requirement 7.9 is why: a subscriber may run the strategy and
#  may not see how it decides.
#
#  AN ALLOW-LIST, NEVER A DENY-LIST
#  -------------------------------
#  `SUBSCRIBER_SIGNAL_FIELDS` names what a non-owner MAY see, and
#  `project_subscriber_signal` emits those keys and nothing else. The
#  alternative - copying the owner's item and deleting the sensitive keys -
#  fails the moment a field is added anywhere upstream: `to_public_dict` gains
#  a member, `market_context` gains a key, `signal_trace_item` gains a column
#  (this very task added two), and a deny-list discloses every one of them
#  because nobody remembered to add it to the list. With an allow-list a new
#  field is withheld BY OMISSION, which is the safe direction to be forgotten
#  in.
#
#  THE KEY SET IS CONSTANT, WHICH IS WHAT REQUIREMENT 19.7 NEEDS
#  ------------------------------------------------------------
#  Every allow-listed key is present on every non-owner response, `None` where
#  the fact does not apply. So a subscriber cannot tell a WITHHELD field from
#  an ABSENT one, and - the part that matters - cannot infer anything about the
#  strategy from the SHAPE of the answer: an ML strategy and a rule-based one
#  produce byte-identical key sets, because neither carries an ML key at all.
#  The same is true of the envelope: `trace`, `lifecycle_transitions` and
#  `timeline` are omitted for every non-owner, always, rather than emptied for
#  some of them (an empty `dag_nodes` would still say "this strategy has a
#  DAG", and a present-but-empty `ml_inference` says whether an ML node ran).
#
#  THIS IS A READ RULE. IT NARROWS NOTHING THAT IS STORED
#  -----------------------------------------------------
#  Requirement 23.2's thirteen facts are RECORDED in full for every signal, and
#  `paper_session_service._record_signal` deliberately hands the evaluator's own
#  object to the recorder so that nothing is lost on the way in. The projection
#  happens HERE, at the moment of reading, so the owner's audit trail keeps
#  every field it always had and the restriction cannot destroy evidence.
#
#  WHERE THE ROLE COMES FROM (Requirement 21.1)
#  -------------------------------------------
#  `resolve_signal_viewer_role` compares the authenticated identity - the one
#  `get_current_user` resolved from the bearer token, server-side - with
#  `strategies.user_id` for the signal's own `strategy_id`, read through the
#  caller's own RLS-scoped client. There is no request field of any kind in
#  that decision: `routers/signal_trace.py` declares no `viewer_role`,
#  `is_owner` or `role` query parameter, so there is nothing for a caller to
#  claim. `build_signal_trace_detail`'s `viewer_role` argument exists for a
#  caller that has ALREADY resolved ownership server-side (and for the tests
#  that pin both branches); it is never fed from a request.
#
#  AND IT FAILS CLOSED
#  ------------------
#  No strategy row, a different `user_id`, an unreadable `strategies` table, an
#  unidentifiable caller: all four are NOT-the-owner, so an ownership question
#  that cannot be answered withholds rather than discloses. The single
#  exception is documented on `resolve_signal_viewer_role` and is not a
#  judgement call: with no database client at all there is no `strategies`
#  table in the process and the row can only have come from
#  `SignalService._local_signals`, which nothing but this process's own
#  `create_signal` writes.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


#: The two viewer roles this read path distinguishes. ``subscriber`` is Requirement 23.3's
#: word for it; it is the role of EVERY non-owner, not only of a paying one, because the
#: comparison the role is resolved from asks one question - "did you write this strategy?" -
#: and a reader who did not gets the restricted view whatever their reason for asking.
VIEWER_ROLE_OWNER = "owner"
VIEWER_ROLE_SUBSCRIBER = "subscriber"
VIEWER_ROLES: Tuple[str, ...] = (VIEWER_ROLE_OWNER, VIEWER_ROLE_SUBSCRIBER)

#: Where strategy ownership is decided. One row, one column, read by the caller's own client.
STRATEGY_OWNER_TABLE = "strategies"
STRATEGY_OWNER_COLUMN = "user_id"

#: Requirement 23.2's "signal source", as a CLOSED vocabulary rather than as a column.
#:
#: ``public.signals`` has two candidate "source" facts and neither can be handed to a
#: subscriber as it stands: ``market_info.source_node_ids`` is the emitting ACTION node's
#: identifier, which is Protected_Logic by definition (Requirement 23.3 excludes node-level
#: trace detail), and ``worker_id`` is the owner's process identifier - not logic, but a
#: fingerprint of their infrastructure and meaningless to the reader. What Requirement 23.2
#: asks for that IS safe and IS meaningful is which runtime produced the signal, so that is
#: what is reported, derived from the Execution_Environment and the deployment mode already
#: on the record.
SIGNAL_SOURCE_PAPER_SESSION = "PAPER_SESSION"
SIGNAL_SOURCE_LIVE_DEPLOYMENT = "LIVE_DEPLOYMENT"
SIGNAL_SOURCE_BACKTEST = "BACKTEST"
SIGNAL_SOURCE_UNATTRIBUTED = "UNATTRIBUTED"

#: Requirement 23.2's "safe reason where the signal was not executed", as a closed
#: vocabulary. NOT ``risk_reason``: that column is free text written by the risk engine and
#: routinely names the rule and the number it breached ("max drawdown 4.2% exceeded 4%"),
#: which is a risk-rule internal and is excluded by name. These five words say WHERE the
#: signal stopped and nothing about why the strategy would stop it.
SAFE_REASON_RISK_REFUSED = "REFUSED_BEFORE_SUBMISSION"
SAFE_REASON_REJECTED = "REJECTED"
SAFE_REASON_FAILED = "EXECUTION_FAILED"
SAFE_REASON_CANCELLED = "CANCELLED"
SAFE_REASON_NOT_SUBMITTED = "NOT_YET_SUBMITTED"

#: Requirement 23.2's field list, as the keys a non-owner's ``signal`` carries - and, by
#: Requirement 23.3 ("only the fields listed in Criterion 2"), as an UPPER bound as much as a
#: lower one. Fourteen facts, one key each.
#:
#: ``session_or_deployment_id`` is ONE key because Requirement 23.2 asks for one fact - "the
#: Paper_Session or deployment identifier AS APPLICABLE". ``environment`` says which of the
#: two it is, so nothing is lost by not spelling both: a ``PAPER`` signal has no
#: ``deployment_id`` (Requirement 23.2, and task 29.2 leaves the column null) and a ``LIVE``
#: one has no ``paper_session_id``.
#:
#: What is NOT here, and why each is missing rather than forgotten:
#:   * ``indicators`` / ``market_info`` / ``ml_info`` - omitted ENTIRELY (Requirement 23.3);
#:   * ``risk_reason``, ``drawdown_check``, ``exposure``, ``expected_loss``,
#:     ``expected_reward`` - the risk-rule internals, named in the design as excluded;
#:   * ``decision_metadata`` - the node closure and the risk verdict, i.e. the strategy;
#:   * ``worker_id``, ``venue``, ``exchange_account_id``, ``exchange_id``, ``timeframe`` -
#:     the owner's infrastructure and account attribution, which Requirement 23.2 does not
#:     list;
#:   * ``user_id`` - the reader already knows who they are, and on a subscriber's own signal
#:     it is themselves;
#:   * ``id`` - the caller supplied it in the path to ask this question, so echoing it adds
#:     nothing, and Criterion 2 does not list it.
SUBSCRIBER_SIGNAL_FIELDS: Tuple[str, ...] = (
    "strategy_id",
    "strategy_version",
    "session_or_deployment_id",
    "environment",
    "generated_at",
    "decision",
    "symbol",
    "side",
    "quantity",
    "price",
    "order_lifecycle_state",
    "order_id",
    "signal_source",
    "safe_reason",
)


def signal_source_of(item: Mapping[str, Any]) -> str:
    """Which runtime produced this signal, from the closed vocabulary above.

    The Execution_Environment first, because 010's column is the authoritative record of it
    (Requirement 23.1). Then the deployment ``mode`` the record has always carried, so a
    pre-010 row is still attributed rather than reported as unattributed. Neither reads a
    node identifier.
    """
    environment = parse_execution_environment(_text(_pick(item, "environment")) or "")
    if environment is ExecutionEnvironment.PAPER:
        return SIGNAL_SOURCE_PAPER_SESSION
    if environment is ExecutionEnvironment.LIVE:
        return SIGNAL_SOURCE_LIVE_DEPLOYMENT
    if environment is ExecutionEnvironment.BACKTEST:
        return SIGNAL_SOURCE_BACKTEST

    mode = (_text(_pick(item, "mode")) or "").strip().lower()
    if mode == "paper":
        return SIGNAL_SOURCE_PAPER_SESSION
    if mode == "live":
        return SIGNAL_SOURCE_LIVE_DEPLOYMENT
    if mode == "backtest":
        return SIGNAL_SOURCE_BACKTEST
    return SIGNAL_SOURCE_UNATTRIBUTED


def safe_reason_of(item: Mapping[str, Any]) -> Optional[str]:
    """Requirement 23.2's safe reason, or ``None`` for a signal that WAS executed.

    Derived from the Order_Lifecycle_State the row reports, plus the risk verdict as a
    BOOLEAN. ``risk_passed`` is a verdict and not a rule internal - it says that a check
    refused the signal, never which check or against what threshold - so the one place a
    reason could disclose a strategy (the free-text ``risk_reason``) is not read at all.
    """
    state = normalise_lifecycle_state(_text(_pick(item, "order_lifecycle_state")))
    if state is OrderLifecycleState.REJECTED:
        if _flag(_pick(item, "risk_passed")) is False:
            return SAFE_REASON_RISK_REFUSED
        return SAFE_REASON_REJECTED
    if state is OrderLifecycleState.FAILED:
        return SAFE_REASON_FAILED
    if state is OrderLifecycleState.CANCELLED:
        return SAFE_REASON_CANCELLED
    if state in (OrderLifecycleState.GENERATED, OrderLifecycleState.PENDING):
        return SAFE_REASON_NOT_SUBMITTED
    return None


def project_subscriber_signal(row: Mapping[str, Any]) -> Dict[str, Any]:
    """One ``public.signals`` row as the fourteen fields a non-owner may see.

    Built from :func:`signal_trace_item` - the OWNER's own projection - rather than from the
    row directly, so the two views cannot disagree about a fact they both report: the
    subscriber sees a strict SUBSET of what the owner sees, not a second rendering of the
    same columns that could drift from it.

    Every key in :data:`SUBSCRIBER_SIGNAL_FIELDS` is emitted, in that order, ``None`` where
    the fact does not apply - see this section's header on why the key set is constant.

    ``price`` is the execution price where the venue reported one and the decision price
    (``market_info.price``, read by that one name) otherwise, which is Requirement 23.2's
    "the price" for a signal that has not been filled yet. A price is market data, not
    strategy logic.
    """
    item = signal_trace_item(row)
    execution = item.get("execution") or {}
    market = (item.get("decision_metadata") or {}).get("market_context") or {}

    projected: Dict[str, Any] = {
        "strategy_id": _text(_pick(item, "strategy_id")),
        "strategy_version": _text(_pick(item, "strategy_version")),
        "session_or_deployment_id": _text(_pick(item, "paper_session_id"))
        or _text(_pick(item, "deployment_id")),
        "environment": _text(_pick(item, "environment")),
        "generated_at": _text(_pick(item, "generated_at")),
        "decision": _text(_pick(item, "decision")),
        "symbol": _text(_pick(item, "symbol")),
        "side": _text(_pick(item, "side")),
        "quantity": _number(_pick(item, "quantity")),
        "price": _number(_pick(execution, "execution_price")),
        "order_lifecycle_state": _text(_pick(item, "order_lifecycle_state")),
        "order_id": _text(_pick(item, "order_id")),
        "signal_source": signal_source_of(item),
        "safe_reason": safe_reason_of(row),
    }
    if projected["price"] is None:
        projected["price"] = _number(_pick(market, "price"))

    # Belt and braces, and cheap: the emitted key set IS the allow-list, so a key added to
    # the dict above without being declared cannot reach a subscriber, and a declared key
    # that the dict forgot is emitted as None rather than silently missing.
    return {name: projected.get(name) for name in SUBSCRIBER_SIGNAL_FIELDS}


async def resolve_signal_viewer_role(sb: Any, user: Any, row: Mapping[str, Any]) -> str:
    """``owner`` or ``subscriber`` for this caller and this signal. Decided server-side.

    ONE comparison: the authenticated identity against ``strategies.user_id`` for the
    signal's ``strategy_id``, read through the caller's own RLS-scoped client. No request
    field participates (Requirement 21.1), and no claim from the row participates either -
    ``signals.user_id`` says who RAN the strategy, which for a subscriber running a
    purchased Listing is themselves.

    FAILS CLOSED, IN FOUR OF FIVE CASES
        An unidentifiable caller, a signal with no ``strategy_id``, a ``strategies`` row that
        cannot be read, and a row whose ``user_id`` differs all resolve to
        :data:`VIEWER_ROLE_SUBSCRIBER`. A no-row answer is the NORMAL one for a subscriber:
        RLS on their own client does not admit the creator's ``strategies`` row, so the read
        succeeds and returns nothing.

    THE ONE EXEMPTION: NO DATABASE CLIENT AT ALL
        ``sb is None`` means this process resolved no client, so there is no ``strategies``
        table to consult, no RLS in force and no marketplace in play - and the row it is
        about cannot have come from Postgres either. It came from
        :attr:`SignalService._local_signals`, an in-process dict that only this process's own
        ``create_signal`` writes and whose read is already scoped to the caller's own
        ``user_id``. Withholding there would restrict an owner's own view of their own signal
        on the strength of a lookup that was never possible, so the pre-29.4 answer stands.
    """
    viewer_id = _text(_pick(user, "id"))
    strategy_id = _text(_pick(row, "strategy_id"))
    if viewer_id is None or strategy_id is None:
        return VIEWER_ROLE_SUBSCRIBER
    if sb is None:
        return VIEWER_ROLE_OWNER

    try:
        result = await _execute(
            sb.table(STRATEGY_OWNER_TABLE)
            .select(STRATEGY_OWNER_COLUMN)
            .eq("id", strategy_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - an unanswerable question withholds
        logger.warning(
            "Strategy ownership for signal on strategy %s could not be read (%s); the trace "
            "detail is served with the subscriber-safe projection (Requirement 23.3).",
            strategy_id,
            exc,
        )
        return VIEWER_ROLE_SUBSCRIBER

    error = getattr(result, "error", None)
    if error is not None:
        logger.warning(
            "Strategy ownership for signal on strategy %s could not be read (%s); the trace "
            "detail is served with the subscriber-safe projection (Requirement 23.3).",
            strategy_id,
            error,
        )
        return VIEWER_ROLE_SUBSCRIBER

    data = getattr(result, "data", None)
    rows = data if isinstance(data, list) else ([data] if data else [])
    if not rows:
        return VIEWER_ROLE_SUBSCRIBER
    owner_id = _text(_pick(rows[0], STRATEGY_OWNER_COLUMN))
    return VIEWER_ROLE_OWNER if owner_id is not None and owner_id == viewer_id else (
        VIEWER_ROLE_SUBSCRIBER
    )


def _subscriber_signal_trace_detail(
    row: Mapping[str, Any], *, with_canonical: bool
) -> Dict[str, Any]:
    """The whole answer a non-owner gets: the allow-list, and what it is.

    Four keys, the same four for every non-owner and every strategy.
    ``lifecycle_state_source`` and ``degraded`` are retained from the owner's envelope
    because they are facts about this DATABASE (whether 005b is applied), identical for every
    reader and every strategy, and the reported execution status is coarser without 005b -
    which a reader of an execution status is entitled to know. ``trace``,
    ``lifecycle_transitions`` and ``timeline`` are absent: the first two describe the
    strategy's own evaluation, and the third embeds ``indicators``, ``market_info`` and
    ``risk_reason`` in its ``data`` members.
    """
    return {
        "signal": project_subscriber_signal(row),
        "viewer_role": VIEWER_ROLE_SUBSCRIBER,
        "lifecycle_state_source": "canonical" if with_canonical else "legacy_status_map",
        "degraded": _lifecycle_degradation(with_canonical),
    }


async def build_signal_trace_detail(
    service: "SignalService",
    user: Any,
    signal_id: str,
    *,
    environment: Optional[Any] = None,
    viewer_role: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """``GET /api/signal-trace/signals/{signal_id}``. Requirements 17.6, 16.7, 20.1-20.3.

    Returns
        ``None`` when no row owned by ``user`` has this identifier - which the router
        renders as the single 404 that Requirement 20.2 requires be identical for a
        non-owner and for an identifier that never existed. Nothing about the answer
        depends on which of the two happened, because the owner-scoped SELECT returns no
        row in both cases and this function never looks the identifier up again.

        Otherwise ``{signal, trace, lifecycle_transitions, timeline,
        lifecycle_state_source, degraded}``:

        * ``signal`` - task 13.1's ``signal_trace_item``, unchanged, so the detail view and
          the list agree about every field of the same signal;
        * ``trace`` - Requirement 17.6's four sections, each labelled with the source it
          was read from;
        * ``lifecycle_transitions`` - Requirement 16.7's persisted history, chronological;
        * ``timeline`` - the pre-spec derived event list SignalTrace.jsx renders today.

        A NON-OWNER gets a different, SMALLER answer - four keys, the fourteen fields of
        Requirement 23.2 among them - see :func:`_subscriber_signal_trace_detail` and the
        task 29.4 header above. The owner's five-plus-one keys are untouched by that
        addition: an owner's view is exactly what it was.

    ``environment`` is task 29.3's filter, applied as a predicate on the read (see
    :meth:`SignalService.get_signal`). A signal that exists but is not in one of the named
    environments answers as an unknown identifier does.

    ``viewer_role`` is for a caller that has ALREADY resolved ownership server-side; ``None``
    - what the router passes, because the router has no business taking a role from a request
    - resolves it here through :func:`resolve_signal_viewer_role`.

    Raises
        :class:`SignalEnvironmentRefused` (400) for an environment value outside the three.
        :class:`SignalEnvironmentFilterUnanswerable` (404) when the filter cannot be applied
        because migration 010 is absent - before the row is read, so the answer is the same
        for every identifier and every caller.
    """
    environments, unrecognised_environments = resolve_environment_filter(environment)
    if unrecognised_environments:
        raise SignalEnvironmentRefused(unrecognised_environments)

    sb = await service._get_supabase(user)
    if environments and not await signal_environment_columns_supported(sb):
        warn_signal_environment_columns_absent(
            f"an environment filter {list(environments)} was requested on the trace detail "
            f"for signal {signal_id}, and there is no environment column to answer it with"
        )
        raise SignalEnvironmentFilterUnanswerable(environments)

    # The predicate is added to the read ONLY when a filter is active, so an unfiltered
    # detail read is the same statement, with the same two ownership predicates, that it has
    # always been.
    row = (
        await service.get_signal(user, signal_id, environment=list(environments))
        if environments
        else await service.get_signal(user, signal_id)
    )
    if not row:
        return None

    with_canonical = await signal_lifecycle_columns_supported(sb) if sb is not None else False

    role = (
        await resolve_signal_viewer_role(sb, user, row)
        if viewer_role is None
        else str(viewer_role)
    )
    if role != VIEWER_ROLE_OWNER:
        # Requirements 7.9, 23.3: not the strategy's author, so not its internals. Nothing
        # below this line runs - the engine record is not even read, because a trace store
        # this caller may not see is not worth a lookup.
        return _subscriber_signal_trace_detail(row, with_canonical=with_canonical)

    if not with_canonical:
        warn_signal_lifecycle_columns_absent(
            f"the trace detail for signal {signal_id} is reporting an Order_Lifecycle_State "
            "reconciled from the legacy status column via SIGNALS_STATUS_MAP"
        )

    signal = signal_from_row(row)
    record = await load_signal_trace_record(signal_id, _text(_pick(row, "strategy_id")))

    return {
        "signal": signal_trace_item(row),
        "trace": {
            **_trace_identity(record),
            "dag_nodes": _dag_node_section(record, row),
            "ml_inference": _ml_inference_section(record, signal),
            "risk_validation": _risk_validation_section(record, signal),
            "execution": _execution_section(record, row, signal.order_lifecycle_state),
        },
        "lifecycle_transitions": await load_lifecycle_transitions(
            sb, signal_id=signal_id, user_id=signal.user_id or str(_pick(row, "user_id") or "")
        ),
        "timeline": signal_event_timeline(row),
        "lifecycle_state_source": "canonical" if with_canonical else "legacy_status_map",
        "degraded": _lifecycle_degradation(with_canonical),
    }


# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#
#  TASK 13.3 - the signal-trace EXPORT: the filtered list as a file
#
#  Spec: trading-lifecycle-integration. design.md's API table:
#  "GET /api/signal-trace/signals/export | CSV/JSON export | New, matches the
#  export call SignalTrace.jsx already makes." Requirement 17.1 (and, through
#  the filters it reuses, 17.2, 17.3, 20.1, 20.2, 20.3).
#
#  WHAT THIS IS: THE LIST, WALKED TO THE END, RENDERED AS A FILE
#  ------------------------------------------------------------
#  The export is not a second query. It calls build_signal_trace_page - task
#  13.1's list - page by page and renders what comes back. Three consequences,
#  all of them the point:
#
#    * ONE FILTER IMPLEMENTATION. Requirement 17.2's AND-across-categories /
#      OR-within-category semantics, the `side`-on-`decision` mapping, the 005b
#      legacy-status translation and the 400 on an unrecognised
#      Order_Lifecycle_State are the list's, not a second copy that could
#      answer the same query differently. An export of a filtered view is
#      therefore exactly the rows that view shows.
#    * ONE OWNERSHIP IMPLEMENTATION. Every read is the list's own owner-scoped
#      read (RLS on the caller's client plus the explicit user_id predicate), so
#      a non-owner exporting another user's strategy_id gets the same empty file
#      a nonexistent strategy_id produces (Requirements 20.1, 20.2). There is no
#      export-specific query that could have forgotten the predicate.
#    * ONE ROW SHAPE. Each entry is signal_trace_item - the same projection the
#      list and the detail render - so Requirement 20.3's containment holds here
#      for the reason it holds there: the record has no credential field.
#
#  WHY IT WALKS EVERY PAGE (Requirement 17.1)
#  ------------------------------------------
#  Requirement 17.1: the page "SHALL NOT omit a signal that was actually
#  generated". A user with 400 signals who clicks Export CSV must not receive
#  the 100 that happen to be on screen. So this walks has_more/next_offset to
#  the end, bounded by SIGNAL_TRACE_EXPORT_MAX_ROWS, and reports `truncated`
#  when the bound stopped it - a partial file that says it is partial, rather
#  than a partial file that looks complete. (The pre-spec export took ONE page
#  of at most 1000 raw rows and said nothing about the rest.)
#
#  A signal is emitted at most once even though the walk issues several reads:
#  ids are de-duplicated across pages, because offset pagination over a list
#  sorted by generated_at DESC shifts when a new signal is inserted mid-walk,
#  and the same row can legitimately land on two consecutive pages. Losing the
#  tail row of a page to a concurrent insert is possible and NOT fixable with
#  offsets; duplicating one is, so it is fixed.
#
#  CSV IS A FLAT PROJECTION, AND SAYS WHICH FIELDS IT DROPPED
#  ---------------------------------------------------------
#  CSV has one value per cell. The item's nested members (decision_metadata
#  with its node closure and risk verdict, sizing_intention) have no honest flat
#  spelling, so the CSV columns are the scalar audit fields Requirement 17.3
#  names - and the JSON export carries the whole item, unflattened. The CSV
#  header is a NAMED constant (SIGNAL_TRACE_EXPORT_COLUMNS), not
#  `signals[0].keys()`: deriving the header from the first row means an export
#  whose columns depend on which signal sorted first, and a row missing a key
#  raises mid-write (the pre-spec export had exactly this shape).
#
#  FORMULA INJECTION IS NEUTRALISED, WITHOUT MANGLING NUMBERS
#  ---------------------------------------------------------
#  A spreadsheet treats a cell beginning with = + - @ or a control character as
#  a formula, so a `failure_reason` echoed from a venue is a script-injection
#  vector in a file the user opens locally. Text cells beginning with one of
#  those are prefixed with an apostrophe (the standard defusing). NUMERIC cells
#  are formatted separately and never prefixed - otherwise every negative pnl
#  would arrive as text and a spreadsheet could not sum a column.
#
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════


#: The two formats ``SignalTrace.jsx`` asks for, and the only two accepted.
SIGNAL_TRACE_EXPORT_FORMATS: Tuple[str, ...] = ("json", "csv")

#: Content-Type per format. ``charset=utf-8`` on the CSV because the export can carry a
#: symbol or a failure reason outside ASCII and a spreadsheet that guesses gets it wrong.
SIGNAL_TRACE_EXPORT_MEDIA_TYPES: Dict[str, str] = {
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
}

#: The download filenames. Exactly what ``SignalTrace.jsx`` names the blob it saves, so
#: the Content-Disposition and the page's own ``a.download`` agree.
SIGNAL_TRACE_EXPORT_FILENAMES: Dict[str, str] = {
    "json": "signal_trace.json",
    "csv": "signal_trace.csv",
}

#: The CSV projection: ``(column header, path into the item)``. A NAMED, ORDERED constant
#: rather than a derivation from the first row - see this section's header. Every field
#: Requirement 17.3 lists per signal is here (generation time, strategy, symbol, side,
#: quantity, Order_Lifecycle_State, order id, execution id, execution price, filled,
#: remaining, fees, failure reason), plus the attribution an auditor needs to tell two
#: deployments of the same strategy apart.
#:
#: ``user_id`` is deliberately absent: every row in the file belongs to the caller, so the
#: column would be the same value on every line. ``idempotency_key`` is absent for a
#: different reason - it is derived (``"signal:" + id``), so it is the signal id twice.
SIGNAL_TRACE_EXPORT_COLUMNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("signal_id", ("id",)),
    ("generated_at", ("generated_at",)),
    ("strategy_id", ("strategy_id",)),
    ("strategy_version", ("strategy_version",)),
    # Task 29.3, and Requirement 23.4 read at the row level: every exported line carries
    # its own Execution_Environment, so a spreadsheet cannot total a PAPER pnl and a LIVE
    # one together without an environment column to group or filter by. ``paper_session_id``
    # sits beside ``deployment_id`` because for a PAPER signal it is the applicable
    # identifier and ``deployment_id`` is null (Requirement 23.2).
    ("environment", ("environment",)),
    ("deployment_id", ("deployment_id",)),
    ("paper_session_id", ("paper_session_id",)),
    ("exchange_account_id", ("exchange_account_id",)),
    ("venue", ("venue",)),
    ("mode", ("mode",)),
    ("symbol", ("symbol",)),
    ("timeframe", ("timeframe",)),
    ("worker_id", ("worker_id",)),
    ("decision", ("decision",)),
    ("signal_type", ("signal_type",)),
    ("side", ("side",)),
    ("quantity", ("quantity",)),
    ("order_lifecycle_state", ("order_lifecycle_state",)),
    ("order_id", ("order_id",)),
    ("execution_id", ("execution_id",)),
    ("execution_price", ("execution", "execution_price")),
    ("filled_quantity", ("execution", "filled_quantity")),
    ("remaining_quantity", ("execution", "remaining_quantity")),
    ("fees", ("execution", "fees")),
    ("slippage", ("execution", "slippage")),
    ("latency_ms", ("execution", "latency_ms")),
    ("pnl", ("execution", "pnl")),
    ("realized_pnl", ("execution", "realized_pnl")),
    ("failure_reason", ("execution", "failure_reason")),
    ("order_updated_at", ("execution", "order_updated_at")),
    ("executed_at", ("execution", "executed_at")),
)

#: Leading characters a spreadsheet reads as the start of a formula. Includes the two
#: control characters, because a cell beginning with a tab or a carriage return is
#: re-parsed after the whitespace is trimmed.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def normalise_export_format(value: Any) -> str:
    """The requested format, lowercased and validated. Defaults to ``json`` when absent.

    Raises
        :class:`SignalExportRefused` (400) for anything outside
        :data:`SIGNAL_TRACE_EXPORT_FORMATS`. Never falls back to JSON silently - see that
        class's docstring.
    """
    text = _text(value)
    if text is None:
        return "json"
    lowered = text.lower()
    if lowered not in SIGNAL_TRACE_EXPORT_FORMATS:
        raise SignalExportRefused(
            "SIGNAL_EXPORT_FORMAT_UNSUPPORTED",
            f"{text!r} is not an export format. Supported: "
            f"{list(SIGNAL_TRACE_EXPORT_FORMATS)}.",
            {
                "requested": text,
                "supported_formats": list(SIGNAL_TRACE_EXPORT_FORMATS),
            },
        )
    return lowered


def _export_cell(value: Any) -> str:
    """One item field as one CSV cell.

    ``None`` becomes the empty cell - not the string "None", which a spreadsheet would
    show as data. Booleans become ``true``/``false`` (JSON's spelling, so the CSV and the
    JSON export of the same signal read alike). Numbers are formatted WITHOUT the
    formula-injection guard, so a negative pnl stays a number a spreadsheet can sum; every
    other value is stringified and guarded.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        # repr-free formatting: 0.25 stays "0.25", 1e-9 does not become "1E-9".
        text = format(value, "f") if isinstance(value, Decimal) else str(value)
        return text
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(_jsonable(value), default=str, sort_keys=True)
    else:
        text = _text(value) or str(value)
    return f"'{text}" if text.startswith(_CSV_FORMULA_LEAD) else text


def signal_trace_export_row(item: Mapping[str, Any]) -> List[str]:
    """One :func:`signal_trace_item` as one CSV line, in :data:`SIGNAL_TRACE_EXPORT_COLUMNS`
    order.

    Reads each column through its declared path, so a field the item does not carry is an
    empty cell rather than a ``KeyError`` mid-file.
    """
    cells: List[str] = []
    for _header, path in SIGNAL_TRACE_EXPORT_COLUMNS:
        value: Any = item
        for key in path:
            value = _pick(value, key) if value is not None else None
        cells.append(_export_cell(value))
    return cells


def render_signal_trace_csv(items: Iterable[Mapping[str, Any]]) -> str:
    """The CSV body: the declared header, then one line per signal.

    The header is written even for an empty export, so a user who exported a filter that
    matches nothing gets a valid file with column names rather than a zero-byte download
    they cannot tell from a failed request.

    ``lineterminator="\\r\\n"`` is RFC 4180 and is what ``csv``'s own default is; it is
    stated rather than inherited because ``StringIO`` is being written on a platform whose
    ``newline`` handling would otherwise be in play.
    """
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([header for header, _path in SIGNAL_TRACE_EXPORT_COLUMNS])
    for item in items:
        writer.writerow(signal_trace_export_row(item))
    return output.getvalue()


def render_signal_trace_json(envelope: Mapping[str, Any]) -> str:
    """The JSON body: the whole envelope, indented, ``default=str`` for anything exotic.

    Indented because this file is downloaded and read by a human as often as it is parsed;
    ``SignalTrace.jsx`` re-serialises it with ``JSON.stringify(data, null, 2)`` anyway.

    ``_jsonable`` IS DELIBERATELY NOT APPLIED HERE, AND THAT IS A REQUIREMENT 17.1 POINT
        ``_jsonable`` exists to bound what goes into a JSONB COLUMN: it truncates a list
        at ``_MAX_JSON_ITEMS`` (256) and appends a ``"[truncated: N items]"`` marker. Run
        over this envelope it would silently drop every signal after the 256th from the
        export - the exact omission Requirement 17.1 forbids, disguised as a rendering
        detail. The items need no such pass anyway: they come from ``signal_trace_item``,
        whose nested values were already made JSON-safe on the way out of the row, and
        ``default=str`` covers anything left.
    """
    return json.dumps(dict(envelope), default=str, indent=2, sort_keys=False)


async def build_signal_trace_export(
    service: "SignalService",
    user: Any,
    *,
    format: str = "json",
    strategy_id: Optional[Any] = None,
    strategy_version: Optional[Any] = None,
    deployment_id: Optional[Any] = None,
    symbol: Optional[Any] = None,
    side: Optional[Any] = None,
    order_lifecycle_state: Optional[Any] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    exchange_id: Optional[Any] = None,
    worker_id: Optional[Any] = None,
    decision: Optional[Any] = None,
    status: Optional[Any] = None,
    ml_type: Optional[str] = None,
    search: Optional[str] = None,
    environment: Optional[Any] = None,
    max_rows: int = SIGNAL_TRACE_EXPORT_MAX_ROWS,
) -> Dict[str, Any]:
    """``GET /api/signal-trace/signals/export``. Requirement 17.1.

    Every filter parameter is task 13.1's, spelled the same way and passed through
    unchanged, so the export of a filtered view is that view (see this section's header).
    There is no ``limit``/``offset``: the export's unit is the whole filtered result, and
    the walk is internal.

    Returns
        ``{format, media_type, filename, content, row_count, truncated, max_rows,
        exported_at, filters_active, active_filters, lifecycle_state_source, degraded,
        signals}``.

        ``content`` is the ready-to-serve body; the router adds the headers and does no
        rendering of its own. ``signals`` is the same list ``content`` was rendered from,
        for a caller (and a test) that wants the items rather than the file - the router
        does not put it on the wire.

        ``truncated`` is ``True`` only when :data:`SIGNAL_TRACE_EXPORT_MAX_ROWS` (or a
        smaller ``max_rows``) stopped the walk before the end of the filtered list, which
        is the one case where Requirement 17.1's "SHALL NOT omit" is not fully honoured -
        so it is stated on the envelope and on the response headers instead of being left
        for the user to discover.

    Raises
        :class:`SignalExportRefused` (400) for an unsupported ``format``.
        :class:`OrderLifecycleRejected` (400) for an unrecognised
        ``order_lifecycle_state`` - raised by the list, not re-implemented here.
    """
    resolved_format = normalise_export_format(format)
    ceiling = max(1, min(int(max_rows), SIGNAL_TRACE_EXPORT_MAX_ROWS))

    items: List[Dict[str, Any]] = []
    seen: Dict[str, None] = {}
    offset = 0
    truncated = False
    first_page: Optional[Dict[str, Any]] = None

    while True:
        page = await build_signal_trace_page(
            service,
            user,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            deployment_id=deployment_id,
            symbol=symbol,
            side=side,
            order_lifecycle_state=order_lifecycle_state,
            date_from=date_from,
            date_to=date_to,
            limit=SIGNAL_TRACE_PAGE_SIZE,
            offset=offset,
            exchange_id=exchange_id,
            worker_id=worker_id,
            decision=decision,
            status=status,
            ml_type=ml_type,
            search=search,
            environment=environment,
        )
        if first_page is None:
            first_page = page

        for item in page["signals"]:
            signal_id = _text(_pick(item, "id"))
            if signal_id is not None:
                if signal_id in seen:
                    # The same row on two pages: a concurrent insert shifted the offsets.
                    continue
                seen[signal_id] = None
            items.append(item)

        if len(items) >= ceiling:
            truncated = len(items) > ceiling or bool(page["has_more"])
            del items[ceiling:]
            break
        if not page["has_more"]:
            break
        offset = page["next_offset"] or offset + SIGNAL_TRACE_PAGE_SIZE

    envelope: Dict[str, Any] = {
        "format": resolved_format,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(items),
        "truncated": truncated,
        "max_rows": ceiling,
        "filters_active": first_page["filters_active"],
        "active_filters": list(first_page["active_filters"]),
        "lifecycle_state_source": first_page["lifecycle_state_source"],
        "degraded": first_page["degraded"],
        # ── task 29.3 ──────────────────────────────────────────────────────────
        # The three the list reports, carried onto the file. ``count_by_environment``
        # is recomputed over the WHOLE export rather than copied from the first page:
        # the first page's counts describe 100 signals and this file describes all of
        # them, and a per-environment figure that silently covered only the first page
        # would be exactly the unlabelled-mix Requirement 23.4 forbids, one level up.
        "environment_source": first_page["environment_source"],
        "environment_filter": list(first_page["environment_filter"]),
        "count_by_environment": environment_counts(items),
        "environment_degraded": first_page["environment_degraded"],
        "signals": items,
    }

    content = (
        render_signal_trace_csv(items)
        if resolved_format == "csv"
        else render_signal_trace_json(envelope)
    )

    return {
        **envelope,
        "media_type": SIGNAL_TRACE_EXPORT_MEDIA_TYPES[resolved_format],
        "filename": SIGNAL_TRACE_EXPORT_FILENAMES[resolved_format],
        "content": content,
    }
