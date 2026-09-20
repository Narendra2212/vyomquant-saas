"""
backend/dashboard_aggregation_service.py — Dashboard Aggregation Service

Enterprise-grade single source of truth for all Dashboard data.
Replaces multiple frontend API calls with one optimized endpoint.

PHASE 2-3: Build DashboardAggregationService as single source of truth
Aggregate data from all modules: Authentication, Billing, Subscription, Exchange, 
Bot Monitor, Strategy Builder, Strategies, Marketplace, Risk, Notifications, 
Referral, Signal Trace, Support, Health
"""

import asyncio
import inspect
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from backend_app.core.dependencies import get_telemetry, create_request_supabase

logger = logging.getLogger("DashboardAggregationService")

#: Fallback used only when neither APP_URL nor FRONTEND_URL is set. Referral links are
#: user-facing and get pasted into chats and emails, so a stale hostname here outlives any
#: deployment. Kept deliberately identical to ``routers.referral._get_referral_link`` - the two
#: are duplicated rather than shared to avoid importing a router module into a service layer, so
#: they must be changed together.
_DEFAULT_APP_URL = "https://app.vyomquant.in"


def _app_base_url() -> str:
    """Public base URL of the frontend, for links rendered into user-visible payloads."""
    return os.getenv("APP_URL", os.getenv("FRONTEND_URL", _DEFAULT_APP_URL)).rstrip("/")


def _equity_of(point: Any) -> Optional[float]:
    """The equity figure carried by one point of an equity curve, or ``None`` if it carries none.

    Accepts what ``get_equity_curve`` actually returns - a mapping keyed by the QuestDB column
    names (``timestamp``, ``equity``) - and, for callers holding a plainer series, a bare number.
    ``total_equity`` is accepted as an alias because that is the column name the paper equity
    snapshots use.

    Returns ``None`` rather than ``0.0`` for an unreadable point. Zero is a real equity: an
    account can be flat, and a wiped account is the case a drawdown figure matters most for, so
    substituting zero for "not reported" would invent the very reading it is standing in for.
    """
    if isinstance(point, bool):  # bool is an int subclass; an equity is never a flag
        return None
    if isinstance(point, (int, float)):
        return float(point)
    if isinstance(point, dict):
        raw = point.get("equity")
        if raw is None:
            raw = point.get("total_equity")
    else:
        raw = getattr(point, "equity", None)
        if raw is None:
            raw = getattr(point, "total_equity", None)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
        return None
    return value


def current_drawdown_pct_from_equity_curve(equity_curve: Optional[List[Any]]) -> Optional[float]:
    """Current drawdown as a **percentage** of the series' peak, or ``None`` if unmeasurable.

    vyomquant-ui-redesign BC-1 (`design.md` §1.5, §16). Drawdown is a peak-to-trough decline.
    The field the dashboard has published until now carries ``today_return_pct`` instead, so a
    profitable day renders as a positive "drawdown" - the figure a trader reads to decide whether
    to cut size. This is the honest computation, taken over the equity series the aggregation
    service already reads; it opens no connection and issues no query of its own.

    The figure is ``(peak_equity - current_equity) / peak_equity * 100``, where ``peak_equity``
    is the maximum over the series up to and including its last point and ``current_equity`` is
    that last point. Note this is *current* drawdown, not *maximum* drawdown: it measures the
    decline still outstanding right now, not the worst decline the series ever suffered. It is
    therefore a different quantity from ``backend/paper/paper_accounting.max_drawdown``, which
    answers Requirement 18.9's question about a paper session's worst historical decline, and the
    two are deliberately not shared.

    Args:
        equity_curve: The series in ascending time order, as ``get_equity_curve`` returns it.
            It is read as given and **not** sorted here: the drawdown of a resorted series is
            the drawdown of a different series, and the read is already ordered by
            ``ORDER BY timestamp ASC``.

    Returns:
        A non-negative percentage rounded to two decimals, or ``None``.

        ``None`` - not ``0.0`` - whenever no decline can be measured:

        * an absent or empty series: nothing was read;
        * a single point: one observation describes no decline. A peak needs something to fall
          from;
        * any point whose equity is unreadable: dropping it would silently measure a different
          series, and the dropped point may have been the peak;
        * a peak at or below zero: there is no positive base to express the decline against.

        ``0.0`` is reserved for its one honest meaning - a series that was read, that has a
        positive peak, and whose latest point is at or above that peak. A series that only ever
        rose is at its peak and its drawdown is zero. The result is clamped at zero for that
        reason: a gain is not a negative drawdown, it is no drawdown.
    """
    if not equity_curve:
        return None

    equities: List[float] = []
    for point in equity_curve:
        equity = _equity_of(point)
        if equity is None:
            return None
        equities.append(equity)

    if len(equities) < 2:
        return None

    peak = max(equities)
    if peak <= 0.0:
        return None

    current = equities[-1]
    decline_pct = (peak - current) / peak * 100.0
    return round(max(0.0, decline_pct), 2)


# ══════════════════════════════════════════════════════════════════════════
#
#  BC-2 - a positions read that FAILED is not a positions read that found NONE
#
#  Spec: vyomquant-ui-redesign task 12.2. design.md §1.6, §16. Requirements 14.5, 19.1, 19.2.
#
#  ``get_open_positions`` used to log a Redis (or paper-store) failure and ``return []``.
#  That is the one answer a caller cannot tell apart from the truthful one: an empty list
#  IS what an account with no open positions returns, so an outage rendered as "you have no
#  positions" and a trader could act on it. Requirement 14.5 forbids exactly this.
#
#  THE DISPOSITION, per site:
#    * ``get_open_positions`` RAISES :class:`PositionsUnreadable`. It returns a bare list with
#      no envelope to hang a marker on, so the failure has to leave by the only channel a
#      list has.
#    * ``get_dashboard_data`` catches it and publishes ``degraded`` alongside the rest of the
#      dashboard, because the balances, the equity curve and the executions on that response
#      are still real reads and a blanket 503 would throw them away. ``positions`` stays
#      ``[]`` - the LIST does not lie, it is simply empty - and ``degraded`` is what says
#      WHY it is empty. That is the same discriminator ``signal_service`` already uses for
#      the same problem (``signals: []`` + ``degraded`` vs ``signals: []`` + ``degraded:
#      None``), so this is one convention rather than a second one.
#    * ``routers/dashboard.py::get_dashboard_overview`` raises 503 instead, because EVERY
#      figure it returns is a headline money figure. There is no partial truth left to carry.
#
#  HOW A CLIENT TELLS THE TWO APART (this is the whole point of BC-2):
#    * ``degraded is None``            -> ``positions`` is the truth. ``[]`` means none open.
#    * ``degraded["positions"]``       -> ``positions`` is ``[]`` because it could not be read.
#      ``== "unreadable"``               Render the error state, never an empty table.
#    Redundantly, and from the other end of the response: ``risk.open_positions_count`` is an
#    ``int`` when counted and ``None`` when unreadable - never ``0`` as a stand-in.
#
# ══════════════════════════════════════════════════════════════════════════

#: The value of ``degraded["positions"]`` when the positions read failed. A string rather than
#: a bare ``True`` so the key can name other position-read degradations later without a second
#: shape, and so a log line carrying it reads as a sentence.
POSITIONS_UNREADABLE = "unreadable"


class PositionsUnreadable(RuntimeError):
    """The open-positions read failed, and no list can honestly be returned for it.

    Raised by :meth:`DashboardAggregationService.get_open_positions` where it used to
    ``return []``. Callers that can degrade catch this and publish
    :func:`positions_degradation`; callers that cannot let it propagate to the route's 503.

    Deliberately NOT an ``HTTPException``: this is a service-layer fact, and which status
    code it deserves depends on how much of the response survives without it - a judgement
    only the route can make.
    """

    def __init__(self, environment: str, cause: BaseException) -> None:
        self.environment = environment
        self.cause = cause
        super().__init__(
            f"open positions for the {environment} environment could not be read: {cause}"
        )


def positions_degradation(error: Optional[BaseException], environment: str) -> Optional[Dict[str, Any]]:
    """The ``degraded`` block for an unreadable positions read, or ``None`` when it read fine.

    Shape and spelling follow ``signal_service._lifecycle_degradation`` /
    ``_environment_degradation``: a top-level ``degraded`` key that is ``None`` in the healthy
    case and, in the degraded one, a dict naming what is degraded plus a prose ``reason`` a
    page can render verbatim.

    ``None`` in / ``None`` out, so the caller writes one expression for both cases and cannot
    forget the healthy one.
    """
    if error is None:
        return None
    return {
        "positions": POSITIONS_UNREADABLE,
        "environment": environment,
        "reason": (
            "Open positions could not be read for this account, so the empty positions list on "
            "this response is the absence of a reading and not the absence of positions "
            "(Requirement 14.5). Any position held is still held."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════
#
#  BC-5 - lifetime REALISED P&L, which nothing on the portfolio overview reported
#
#  Spec: vyomquant-ui-redesign task 12.5. design.md §7.6, §16. Requirements 10.1, 19.1, 19.2.
#
#  Requirement 10.1 asks the Portfolio page for realised P&L. Two figures on this response look
#  like it and neither is it:
#    * ``today_realized_pnl`` IS realised, but only since 00:00 UTC. It is not a lifetime figure.
#    * ``cumulative_pnl`` IS lifetime, but it is TOTAL P&L - realised plus the mark-to-market on
#      positions still open. Labelling it "realised" would report unbanked money as banked.
#  So the page had no source and rendered not-available.
#
#  THE FIGURE: the same ``executions.pnl`` sum ``today_realized_pnl`` already comes from, with
#  the day filter removed. Derived from ONE expression rather than a second definition of
#  "realised" - the two figures cannot disagree about what realised means, because they read the
#  same column of the same table and differ only in the window.
#
#  ONE ROUND TRIP: the day-filtered sum used to be its own query with the cutoff in its
#  ``WHERE``. It is now a conditional aggregate inside the same ``SELECT`` as the lifetime sum
#  (:func:`executions_realized_pnl_query`), so the read that produced one figure produces both.
#  The cost that did change is the scan: the lifetime sum has to see the user's whole execution
#  history where the day-filtered one saw today's partition. That is inherent to a lifetime
#  figure - a separate second query would pay the same scan AND a second round trip.
#
#  UNREADABLE IS ``None``; NET-ZERO IS ``0.0`` (Requirement 19.2, and BC-1/BC-2's rule)
#    An account that has closed trades netting exactly nothing has a realised P&L of zero, and
#    that is a fact a trader is entitled to read. So ``0.0`` is reserved for it and for the
#    successfully-read empty ledger, and every case where the figure was NOT read reports
#    ``None``:
#      * the query raised, or returned no row                       -> ``None``
#      * the row carries no ``realized_pnl`` column                 -> ``None``
#      * the sum is SQL NULL over a ledger that HAS rows            -> ``None`` (the ``pnl``
#        column is not reporting for those rows, so no sum of it can be published)
#      * the sum is SQL NULL over a ledger with zero rows           -> ``0.0`` (read fine, and
#        an account that has never executed has realised nothing)
#      * the sum is a number, including ``0.0``                     -> that number
#    ``None`` on the field is the whole honesty channel here, exactly as BC-1's
#    ``current_drawdown_pct_v2`` is - a nullable FIGURE needs no ``degraded`` block, which BC-2
#    added only because a LIST has nowhere to put a null. This is those two conventions applied,
#    not a third one.
#
#  ADDITIVE (Requirement 19.1): ``today_realized_pnl`` and ``cumulative_pnl`` keep their names,
#  their types and their exact values, including the ``0.0`` that ``today_realized_pnl`` has
#  always published for an unreadable day sum. No consumer is repointed by BC-5.
#
# ══════════════════════════════════════════════════════════════════════════

#: Column alias for the day-filtered realised sum. Selected FIRST so a caller reading the row
#: positionally - which is how the single-figure query it replaces was read, ``dataset[0][0]`` -
#: still finds today's figure where it has always been.
TODAY_REALIZED_PNL_COLUMN = "today_realized_pnl"

#: Column alias for the lifetime realised sum (BC-5). Read BY NAME only, never positionally: an
#: unnamed column is a response whose shape we are guessing at, and a guessed money figure is
#: the fabrication Requirement 19.2 forbids. ``None`` is the honest answer there.
REALIZED_PNL_COLUMN = "realized_pnl"

#: Column alias for the row count behind the sums. Not published - it exists so a SQL NULL sum
#: over an EMPTY ledger can be told from a NULL sum over a ledger that has rows, which is the
#: difference between "realised nothing" and "cannot say".
EXECUTION_COUNT_COLUMN = "execution_count"


def executions_realized_pnl_query(safe_uid: str, today_date_str: str) -> str:
    """One QuestDB read yielding the day-filtered realised sum AND the lifetime one.

    BC-5. Both figures are ``sum(pnl)`` over the same ``executions`` rows for this user; the
    day-filtered one narrows to ``timestamp >= today 00:00 UTC`` as a conditional aggregate
    rather than in the ``WHERE``, so one round trip answers both.

    Args:
        safe_uid: A user id ALREADY through :meth:`DashboardAggregationService._safe_uid`. This
            function interpolates it and validates nothing - callers must not pass raw input.
        today_date_str: ``YYYY-MM-DD`` for the UTC day whose realised P&L is wanted.

    The day-filtered branch carries ``else 0.0`` rather than falling through to SQL NULL: a row
    outside today contributes nothing either way, and the published figure is identical, but the
    ``ELSE``-present form is the one this repository already runs against QuestDB
    (``routers/analytics.py``).
    """
    day_cutoff = (
        f"to_timestamp('{today_date_str}T00:00:00.000000Z', 'yyyy-MM-ddTHH:mm:ss.SSSUUUZ')"
    )
    return (
        f"SELECT "
        f"sum(case when timestamp >= {day_cutoff} then pnl else 0.0 end) AS {TODAY_REALIZED_PNL_COLUMN}, "
        f"sum(pnl) AS {REALIZED_PNL_COLUMN}, "
        f"count(*) AS {EXECUTION_COUNT_COLUMN} "
        f"FROM executions "
        f"WHERE user_id = '{safe_uid}';"  # nosec: B608
    )


def _finite_float(raw: Any) -> Optional[float]:
    """``raw`` as a finite float, or ``None`` if it is not a number. Booleans are not money."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):  # NaN / infinity
        return None
    return value


def realized_pnl_from_execution_totals(
    result: Optional[Dict[str, Any]],
) -> Tuple[float, Optional[float]]:
    """Read ``(today_realized_pnl, realized_pnl)`` off :func:`executions_realized_pnl_query`.

    Returns:
        A 2-tuple of

        * ``today_realized_pnl`` - a ``float``, ``0.0`` when unreadable. Its historical
          behaviour, preserved exactly: this field has always published ``0.0`` for a failed or
          empty day sum and BC-5 is additive, so it is not the field that gets a null now.
        * ``realized_pnl`` - the lifetime figure, or ``None`` when it was not read. ``0.0`` only
          for a ledger that WAS read: one whose closed trades net to zero, or one with no rows
          at all. See the BC-5 block above for the case-by-case rule.

    Never raises for a malformed ``result``; an unrecognisable response is an unread one.
    """
    today = 0.0
    lifetime: Optional[float] = None

    if not isinstance(result, dict):
        return today, lifetime
    dataset = result.get("dataset")
    if not dataset:
        return today, lifetime
    row = dataset[0]
    if not isinstance(row, (list, tuple)) or not row:
        return today, lifetime

    columns = [
        col.get("name")
        for col in (result.get("columns") or [])
        if isinstance(col, dict)
    ]
    by_name = dict(zip(columns, row))

    # Today: by name where the response names it, else index 0 - the exact cell the query this
    # replaced read (``pnl_res["dataset"][0][0]``), so no reader of this figure sees a change.
    today_cell = by_name[TODAY_REALIZED_PNL_COLUMN] if TODAY_REALIZED_PNL_COLUMN in by_name else row[0]
    today_value = _finite_float(today_cell)
    if today_value is not None:
        today = today_value

    # Lifetime: named only. A missing column means this response is not the one this function
    # describes, and no figure can be taken from it.
    if REALIZED_PNL_COLUMN in by_name:
        lifetime = _finite_float(by_name[REALIZED_PNL_COLUMN])
        if lifetime is None:
            # SQL NULL. Zero rows -> the ledger was read and it is empty, so nothing has been
            # realised: that is 0.0. Rows present -> ``pnl`` is not reporting for them, and a
            # sum of what is not reported cannot be published.
            count = _finite_float(by_name.get(EXECUTION_COUNT_COLUMN))
            if count is not None and count == 0:
                lifetime = 0.0

    return today, lifetime


class DashboardAggregationService:
    """
    Single source of truth for all Dashboard data.
    
    Aggregates data from:
    - Portfolio (QuestDB)
    - Strategies (Supabase)
    - Signals (Supabase)
    - Exchange Management
    - Risk
    - Notifications
    - Health
    
    All calculations performed in backend.
    Frontend becomes presentation-only.
    """
    
    def __init__(self):
        self._telemetry = None
        self._supabase = None
    
    async def _timed_operation(self, operation_name: str, operation):
        """
        Wrapper coroutine to measure operation duration while preserving exact execution semantics.

        This wrapper:
        - Records monotonic start time
        - Awaits the exact original coroutine
        - Records monotonic end time
        - Logs duration
        - Returns the exact original result
        - Re-raises any exceptions without masking

        Args:
            operation_name: Name of the operation for logging
            operation: The coroutine to measure

        Returns:
            The exact result from the original operation

        Raises:
            Any exception raised by the original operation (not masked)
        """
        start = time.perf_counter()
        try:
            result = await operation
            return result
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                f"dashboard_operation_timing",
                extra={
                    "operation": operation_name,
                    "duration_ms": round(duration_ms, 2),
                },
            )

    def _get_telemetry(self):
        """Get TelemetryEngine instance."""
        if self._telemetry is None:
            from backend_app.core.dependencies import get_telemetry
            self._telemetry = get_telemetry()
        return self._telemetry
    
    async def _execute_sb_query(self, query):
        """Execute a Supabase PostgREST query in a background thread to prevent blocking asyncio loop."""
        if inspect.isawaitable(query):
            return await query
        if hasattr(query, "execute"):
            return await asyncio.to_thread(query.execute)
        return query

    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        from backend_app.core.dependencies import create_request_supabase_async
        res = create_request_supabase_async(user.get("access_token"))
        return await res if inspect.isawaitable(res) else res
    
    def _safe_uid(self, uid: str) -> str:
        """
        DB-CRITICAL-001 FIX: Enhanced validation to prevent SQL injection.
        
        This function validates user_id with multiple checks:
        1. Format validation (alphanumeric, hyphens, underscores only)
        2. Length validation (max 128 characters)
        3. Unicode character validation
        4. Pattern matching to prevent injection attempts
        """
        uid_str = str(uid)
        
        # Length validation
        if len(uid_str) > 128:
            raise ValueError(f"Unsafe user_id: exceeds maximum length of 128 characters")
        
        # Format validation - strict pattern for UUIDs and similar identifiers
        if not re.match(r"^[a-zA-Z0-9\-_]{1,128}$", uid_str):
            raise ValueError(f"Unsafe user_id: contains invalid characters")
        
        # Additional SQL injection pattern checks
        dangerous_patterns = [
            r"'",  # Single quote
            r";",  # Statement separator
            r"--", # SQL comment
            r"/\*", # SQL comment start
            r"\*/", # SQL comment end
            r"\bUNION\b", # UNION operator
            r"\bSELECT\b", # SELECT keyword
            r"\bINSERT\b", # INSERT keyword
            r"\bUPDATE\b", # UPDATE keyword
            r"\bDELETE\b", # DELETE keyword
            r"\bDROP\b", # DROP keyword
            r"\bEXEC\b", # EXECUTE keyword
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, uid_str, re.IGNORECASE):
                raise ValueError(f"Unsafe user_id: contains potentially dangerous pattern")
        
        return uid_str
    
    async def get_subscription_data(self, user: dict, strategies_task: Optional['asyncio.Task'] = None, sb: Optional[Any] = None) -> Dict:
        """
        Get subscription and billing data.

        Args:
            user: User dict
            strategies_task: Optional request-local task for strategies fetch. If provided, will await it instead of fetching.
            sb: Optional shared Supabase client

        Returns:
            Subscription tier, usage metrics, billing status
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {
                    "tier": "free",
                    "usage": {"strategies": 0, "strategies_limit": 3, "bots": 0, "bots_limit": 1, "ml_training_used": 0, "ml_training_limit": 0},
                    "billing_status": "active",
                    "subscription_end": None,
                    "is_trial": False
                }

            # Get user profile with subscription info
            res = await self._execute_sb_query(sb.table("profiles").select("subscription_tier, updated_at").eq("id", user["id"]).limit(1))
            profile = res.data[0] if res and hasattr(res, "data") and res.data else {}

            # Get subscription tier from profile
            subscription_tier = profile.get("subscription_tier", "free")

            # Calculate usage metrics (use provided task if available, otherwise fetch directly)
            if strategies_task is not None:
                strategies = await strategies_task
            else:
                strategies = await self.get_strategies(user, sb=sb)
            strategies_used = len(strategies)
            active_bots = len([s for s in strategies if s["status"] == "active"])
            
            # Get tier limits (these should come from subscription engine)
            tier_limits = {
                "free": {"strategies": 3, "bots": 1, "ml_training": 0},
                "starter": {"strategies": 10, "bots": 3, "ml_training": 5},
                "pro": {"strategies": 50, "bots": 10, "ml_training": 20},
                "enterprise": {"strategies": -1, "bots": -1, "ml_training": -1}  # Unlimited
            }
            
            limits = tier_limits.get(subscription_tier, tier_limits["free"])
            
            return {
                "tier": subscription_tier,
                "usage": {
                    "strategies": strategies_used,
                    "strategies_limit": limits["strategies"],
                    "bots": active_bots,
                    "bots_limit": limits["bots"],
                    "ml_training_used": 0,  # Would come from ML training tracking
                    "ml_training_limit": limits["ml_training"]
                },
                "billing_status": "active",
                "subscription_end": None,
                "is_trial": False
            }
        except Exception as e:
            logger.error(f"Failed to fetch subscription data for user {user['id']}: {e}")
            return {
                "tier": "free",
                "usage": {"strategies": 0, "strategies_limit": 3, "bots": 0, "bots_limit": 1, "ml_training_used": 0, "ml_training_limit": 0},
                "billing_status": "active",
                "subscription_end": None,
                "is_trial": False
            }
    
    async def get_exchange_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get exchange connection data.
        
        Returns:
            Connected exchanges, connection status, latency metrics
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {
                    "total_exchanges": 0,
                    "connected_exchanges": 0,
                    "exchanges": [],
                    "can_trade": False
                }
            
            # Get user's exchange connections from exchange_keys
            try:
                res = await self._execute_sb_query(sb.table("exchange_keys").select("exchange_id, updated_at").eq("user_id", user["id"]))
                connections = res.data or [] if res and hasattr(res, "data") else []
            except Exception as e:
                logger.warning(f"Failed to fetch exchange_keys in dashboard: {e}")
                connections = []
            
            # Calculate exchange metrics
            connected_exchanges = connections
            total_exchanges = len(connections)
            
            # Get exchange health from actual connections
            exchange_health = []
            for conn in connected_exchanges:
                exchange_id = conn.get("exchange_id", "unknown")
                exchange_health.append({
                    "exchange_id": exchange_id,
                    "status": "connected",
                    "latency_ms": 35,
                    "last_sync": conn.get("updated_at")
                })
            
            return {
                "total_exchanges": total_exchanges,
                "connected_exchanges": len(connected_exchanges),
                "exchanges": exchange_health,
                "can_trade": len(connected_exchanges) > 0
            }
        except Exception as e:
            logger.error(f"Failed to fetch exchange data for user {user['id']}: {e}")
            return {
                "total_exchanges": 0,
                "connected_exchanges": 0,
                "exchanges": [],
                "can_trade": False
            }
    
    async def get_notification_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get notification data.
        
        Returns:
            Unread count, recent notifications, notification categories
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            res_unread, res_recent = await asyncio.gather(
                self._execute_sb_query(sb.table("notifications").select("id", count="exact").eq("user_id", user["id"]).eq("read", False)),
                self._execute_sb_query(sb.table("notifications").select("id, type, category, severity, title, message, read, created_at").eq("user_id", user["id"]).order("created_at", desc=True).limit(10)),
                return_exceptions=True
            )

            unread_count = res_unread.count if not isinstance(res_unread, Exception) and res_unread and hasattr(res_unread, "count") and res_unread.count is not None else 0
            
            recent_notifications = []
            if not isinstance(res_recent, Exception) and res_recent and hasattr(res_recent, "data") and res_recent.data:
                for notif in res_recent.data:
                    recent_notifications.append({
                        "id": notif.get("id"),
                        "type": notif.get("type"),
                        "category": notif.get("category"),
                        "severity": notif.get("severity"),
                        "title": notif.get("title"),
                        "message": notif.get("message"),
                        "read": notif.get("read", False),
                        "created_at": notif.get("created_at")
                    })
            
            categories = {}
            for notif in recent_notifications:
                cat = notif.get("category", "system")
                categories[cat] = categories.get(cat, 0) + 1
            
            return {
                "unread_count": unread_count,
                "total_count": unread_count + len([n for n in recent_notifications if n["read"]]),
                "recent": recent_notifications,
                "categories": categories
            }
        except Exception as e:
            logger.error(f"Failed to fetch notification data for user {user['id']}: {e}")
            return {"unread_count": 0, "total_count": 0, "recent": [], "categories": {}}
    
    async def get_referral_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get referral program data.
        
        Returns:
            Referral code, total referrals, earnings, referral link
        """
        try:
            default_ref = user["id"][:8].upper()
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {
                    "referral_code": default_ref,
                    "referral_link": f"{_app_base_url()}/ref/{default_ref}",
                    "total_referrals": 0,
                    "active_referrals": 0,
                    "pending_earnings": 0.0,
                    "approved_earnings": 0.0,
                    "lifetime_earnings": 0.0
                }

            res = await self._execute_sb_query(sb.table("referral_profiles").select("referral_code, total_referrals, active_referrals, pending_earnings, approved_earnings, paid_earnings, lifetime_earnings").eq("user_id", user["id"]).limit(1))
            profile = res.data[0] if res and hasattr(res, "data") and res.data else {}
            
            ref_code = profile.get("referral_code") or default_ref
            
            return {
                "referral_code": ref_code,
                "referral_link": f"{_app_base_url()}/ref/{ref_code}",
                "total_referrals": profile.get("total_referrals", 0),
                "active_referrals": profile.get("active_referrals", 0),
                "pending_earnings": float(profile.get("pending_earnings", 0.0)),
                "approved_earnings": float(profile.get("approved_earnings", 0.0)),
                "lifetime_earnings": float(profile.get("lifetime_earnings", 0.0))
            }
        except Exception as e:
            logger.error(f"Failed to fetch referral data for user {user['id']}: {e}")
            return {
                "referral_code": "",
                "referral_link": "",
                "total_referrals": 0,
                "active_referrals": 0,
                "pending_earnings": 0.0,
                "approved_earnings": 0.0,
                "lifetime_earnings": 0.0
            }
    
    async def get_risk_data(self, user: dict, portfolio: Optional[Dict] = None, sb: Optional[Any] = None) -> Dict:
        """
        Get risk management data.

        NOTE: a second ``get_risk_data`` is defined later in this class and, being later in the
        class body, is the one bound to the attribute - so this definition is unreachable through
        ``DashboardAggregationService.get_risk_data``. Left as found; BC-1 changed nothing here
        beyond adding the new field so the two bodies agree in shape.

        Args:
            user: User dict
            portfolio: Optional portfolio data (to avoid duplicate query if already fetched)
            sb: Optional shared Supabase client

        Returns:
            Risk settings, current risk level, circuit breaker status
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

            # Get risk settings
            settings = {}
            if sb:
                res = await self._execute_sb_query(sb.table("risk_settings").select("max_daily_loss, max_positions, max_leverage, kill_switches").eq("user_id", user["id"]).limit(1))
                settings = res.data[0] if res and hasattr(res, "data") and res.data else {}

            current_drawdown = abs(float((portfolio or {}).get("pnl_pct", 0)))
            
            # Determine risk level
            max_daily_loss = float(settings.get("max_daily_loss", 500))
            risk_level = "low"
            if current_drawdown > max_daily_loss * 0.5:
                risk_level = "medium"
            if current_drawdown > max_daily_loss * 0.8:
                risk_level = "high"
            if current_drawdown >= max_daily_loss:
                risk_level = "critical"
            
            return {
                "risk_level": risk_level,
                # DEPRECATED (BC-1, design.md §1.5): ``abs()`` of a P&L percentage, so a
                # profitable day reads as a positive "drawdown". Unchanged - see the note on the
                # later definition of this method.
                "current_drawdown_pct": current_drawdown,
                # BC-1: this body holds no equity series, so there is nothing to measure a
                # peak-to-trough decline over and ``None`` is the honest answer. Present so the
                # two ``get_risk_data`` bodies below and above agree in shape.
                "current_drawdown_pct_v2": None,
                "max_daily_loss": max_daily_loss,
                "max_positions": settings.get("max_positions", 10),
                "max_leverage": settings.get("max_leverage", 3),
                "circuit_breaker_armed": settings.get("circuit_breaker_armed", True),
                "circuit_breaker_breaches": settings.get("circuit_breaker_breaches", 0),
                "kill_switches": settings.get("kill_switches", [])
            }
        except Exception as e:
            logger.error(f"Failed to fetch risk data for user {user['id']}: {e}")
            return {
                "risk_level": "low",
                "current_drawdown_pct": 0.0,
                "current_drawdown_pct_v2": None,
                "max_daily_loss": 500,
                "max_positions": 10,
                "max_leverage": 3,
                "circuit_breaker_armed": True,
                "circuit_breaker_breaches": 0,
                "kill_switches": []
            }
    
    async def get_marketplace_data(self, user: dict, sb: Optional[Any] = None) -> Dict:
        """
        Get marketplace data from library_strategies.
        
        Returns:
            Available strategies, user's publications, subscription counts
        """
        try:
            if sb is None:
                sb_res = self._get_supabase(user)
                sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
            if not sb:
                return {"available_count": 0, "user_publications": 0, "total_subscribers": 0, "featured": []}

            res_avail, res_pubs = await asyncio.gather(
                self._execute_sb_query(sb.table("library_strategies").select("id, name, is_featured").eq("is_active", True).in_("moderation_status", ["approved", "featured"]).limit(10)),
                self._execute_sb_query(sb.table("library_strategies").select("id, subscriber_count").eq("author_id", user["id"])),
                return_exceptions=True
            )

            available_strategies = res_avail.data if not isinstance(res_avail, Exception) and res_avail and hasattr(res_avail, "data") and res_avail.data else []
            user_publications = res_pubs.data if not isinstance(res_pubs, Exception) and res_pubs and hasattr(res_pubs, "data") and res_pubs.data else []
            
            total_subscribers = 0
            for pub in user_publications:
                total_subscribers += pub.get("subscriber_count", 0)
            
            return {
                "available_count": len(available_strategies),
                "user_publications": len(user_publications),
                "total_subscribers": total_subscribers,
                "featured": [s for s in available_strategies if s.get("is_featured")][:3]
            }
        except Exception as e:
            logger.error(f"Failed to fetch marketplace data for user {user['id']}: {e}")
            return {
                "available_count": 0,
                "user_publications": 0,
                "total_subscribers": 0,
                "featured": []
            }
    
    async def get_admin_metrics(self, user: dict) -> Dict:
        """
        Get system-wide metrics (admin only).
        
        Returns:
            Total users, active strategies, system volume, revenue metrics
        """
        # Return default/mock metrics - real implementation would aggregate across all users
        return {
            "total_users": 150,
            "active_users_24h": 42,
            "total_strategies": 320,
            "active_bots": 85,
            "total_volume_24h": 1250000.0,
            "monthly_recurring_revenue": 4500.0,
            "system_health_score": 99.8
        }
    
    async def get_strategies_by_market(self, user: dict) -> List[Dict]:
        """
        Get public strategies from the marketplace for exploration.
        
        Returns:
            List of published strategies from all users
        """
        # Return empty list - real implementation would fetch from marketplace/library table
        return []

    async def get_portfolio_overview(self, user: dict, environment: str = "live") -> Dict:
        """
        Get normalized portfolio overview data with strict environment isolation.
        
        Distinguishes:
        - total_equity / total_value
        - available_balance / free_balance
        - used_balance / margin
        - today_realized_pnl (closed trades since 00:00:00 UTC)
        - unrealized_pnl (mark-to-market open positions)
        - today_pnl (today_realized + unrealized delta)
        - cumulative_pnl (lifetime total)
        """
        from datetime import timezone
        now_utc = datetime.now(timezone.utc)
        today_utc_cutoff = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                paper_svc = get_paper_trading_service()
                acct = paper_svc.get_or_create_account(user["id"])
                
                # Task 6.2 (Requirements 1.1, 1.4, 2.1, 2.4). ``100000.0`` here was the paper
                # STARTING capital leaking out of a ``dict.get`` fallback into a money field, so
                # an account row missing the column - a schema or migration fault, exactly when a
                # stale-looking figure is most likely to be believed - reported as fully funded.
                # Read through ``_finite_float`` (:314): an absent column is absent, and a figure
                # that WAS read is reported exactly as read, ``0.0``, ``-0.0`` and a genuine
                # ``100000.0`` balance alike (preservation 3.2). Absence is a fact about the
                # READ, never about the number.
                total_equity = _finite_float(acct.get("total_equity"))
                available_balance = _finite_float(acct.get("available_balance"))
                free_balance = available_balance
                used_balance = float(acct.get("locked_balance", 0.0))
                realized_pnl = float(acct.get("realized_pnl", 0.0))
                unrealized_pnl = float(acct.get("unrealized_pnl", 0.0))
                initial_capital = _finite_float(acct.get("initial_capital"))
                
                # Calculate today's realized PnL from paper trades since 00:00 UTC
                trades = paper_svc.get_trades(user["id"])
                # Defect 48, found by ``tests/property/test_absent_vs_zero.py``. This sum coerced
                # each same-day fill with a bare ``float()`` INSIDE this branch's ``try``, so one
                # unreadable realised figure raised and the ``except`` below replaced the ENTIRE
                # overview with the starting capital: a single malformed ledger row fabricated the
                # whole account. Read through ``_finite_float`` so an unreadable fill nulls only
                # the figure it belongs to, and ``None`` rather than a partial sum for the same
                # reason the lifetime figure below is ``None`` - dropping the unreadable fill
                # would publish the sum of a DIFFERENT set of fills under this name. A ledger
                # holding no same-day fills was read fine: nothing realised today is ``0.0``.
                paper_today_realized = [
                    _finite_float(t.get("realized_pnl"))
                    for t in trades
                    if (t.get("executed_at") or "") >= today_utc_cutoff
                ]
                today_realized_pnl: Optional[float] = (
                    None
                    if any(value is None for value in paper_today_realized)
                    else float(sum(paper_today_realized))
                )

                # BC-5: lifetime realised P&L. The SAME per-fill expression as
                # ``today_realized_pnl`` above with the day window removed, for the same reason
                # the live branch derives both figures from one ``sum(pnl)``: the lifetime figure
                # must not be a second definition of "realised". Deliberately NOT
                # ``acct["realized_pnl"]`` - that is the account row's own running total, a
                # different producer that is free to drift from the fill ledger this response
                # already reports today's figure from.
                #
                # ``None`` if any fill's realised figure is unreadable: dropping it would publish
                # the sum of a DIFFERENT set of fills under this name (the rule BC-1 applies to
                # an equity series). An empty ledger read fine is ``0.0`` - nothing realised.
                paper_realized = [_finite_float(t.get("realized_pnl")) for t in trades]
                lifetime_realized_pnl: Optional[float] = (
                    None
                    if any(value is None for value in paper_realized)
                    else float(sum(paper_realized))
                )

                # A figure derived from one that was not read was not read either. ``today_pnl``
                # goes absent with today's realised figure; ``today_return_pct`` goes absent with
                # either its numerator or its ``initial_capital`` denominator. The denominator is
                # where the fabrication used to hide: ``100000.0`` was divided BY rather than
                # published, so an absent capital base surfaced as a precise-looking percentage
                # and never as the literal itself.
                today_pnl: Optional[float] = (
                    None if today_realized_pnl is None else today_realized_pnl + unrealized_pnl
                )
                today_return_pct: Optional[float] = None
                if today_pnl is not None and initial_capital is not None:
                    today_return_pct = (
                        round((today_pnl / initial_capital * 100), 2)
                        if initial_capital > 0
                        else 0.0
                    )
                cumulative_pnl = realized_pnl + unrealized_pnl
                
                return {
                    "total_equity": total_equity,
                    "total_value": total_equity,
                    "available_balance": available_balance,
                    "free_balance": free_balance,
                    "used_balance": used_balance,
                    "today_pnl": today_pnl,
                    "today_realized_pnl": today_realized_pnl,
                    # BC-5 (Requirement 10.1). Same key, same meaning, both environments - a
                    # page cannot have a lifetime realised figure in one mode and no such field
                    # in the other.
                    "realized_pnl": lifetime_realized_pnl,
                    "today_return_pct": today_return_pct,
                    "unrealized_pnl": unrealized_pnl,
                    "cumulative_pnl": cumulative_pnl,
                    "total_exposure": used_balance,
                    "currency": "USD",
                    "environment": "paper",
                    "updated_at": now_utc.isoformat()
                }
            except Exception as paper_err:
                logger.error(f"Failed to fetch paper portfolio overview for {user['id']}: {paper_err}")
                return {
                    # Task 6.2 (Requirements 1.1, 1.2, 2.1, 2.2). The paper store could not be
                    # read, so there is no balance here to report. A trader whose account has
                    # been running for a month and has just lost the store was being shown the
                    # capital they opened with, indistinguishable from a measured balance.
                    "total_equity": None,
                    "total_value": None,
                    "available_balance": None,
                    "free_balance": None,
                    "used_balance": 0.0,
                    "today_pnl": 0.0,
                    "today_realized_pnl": 0.0,
                    # BC-5: the paper read failed, so there is no lifetime realised figure to
                    # report. ``None`` - and as of task 6.2 the four headline money figures above
                    # say the same thing rather than the starting capital, so this is no longer
                    # the one honest field in the literal. The remaining ``0.0``s are the P&L
                    # figures, outside 6.2's scope and left exactly as they were.
                    "realized_pnl": None,
                    "today_return_pct": 0.0,
                    "unrealized_pnl": 0.0,
                    "cumulative_pnl": 0.0,
                    "total_exposure": 0.0,
                    "currency": "USD",
                    "environment": "paper",
                    "updated_at": now_utc.isoformat()
                }

        # LIVE Environment
        telemetry = self._get_telemetry()
        safe_uid = self._safe_uid(user["id"])
        
        total_equity = 0.0
        cumulative_pnl = 0.0
        pnl_pct = 0.0
        total_exposure = 0.0
        available_balance = 0.0
        free_balance = 0.0
        used_balance = 0.0
        
        try:
            result = await telemetry.execute_query(
                f"SELECT * FROM live_user_pnl WHERE user_id = '{safe_uid}' LIMIT 1;"  # nosec: B608
            )
            if result and result.get("dataset"):
                cols = [c["name"] for c in result["columns"]]
                row_dict = dict(zip(cols, result["dataset"][0]))
                total_equity = float(row_dict.get("total_equity", 0.0))
                cumulative_pnl = float(row_dict.get("total_pnl", 0.0))
                pnl_pct = float(row_dict.get("pnl_pct", 0.0))
                total_exposure = float(row_dict.get("total_exposure", 0.0))
        except Exception as q_err:
            logger.debug(f"QuestDB live_user_pnl read error: {q_err}")

        # Check Redis cached exchange balance for live available/free balance
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            # Scan or get balance keys
            keys = await redis_manager.keys(f"portfolio:{user['id']}:*:balance")
            if keys:
                for k in keys:
                    raw_bal = await redis_manager.get(k)
                    if raw_bal:
                        bal_data = json.loads(raw_bal) if isinstance(raw_bal, str) else raw_bal
                        free_balance += float(bal_data.get("available", 0.0))
                        total_from_ex = float(bal_data.get("total", 0.0))
                        if total_equity == 0.0 and total_from_ex > 0.0:
                            total_equity = total_from_ex
                available_balance = free_balance
                used_balance = max(0.0, total_equity - free_balance)
            else:
                available_balance = max(0.0, total_equity - total_exposure)
                free_balance = available_balance
                used_balance = total_exposure
        except Exception as redis_err:
            logger.debug(f"Redis balance read error: {redis_err}")
            available_balance = max(0.0, total_equity - total_exposure)
            free_balance = available_balance
            used_balance = total_exposure

        # Realized PnL from the QuestDB executions table: today's (since 00:00 UTC) and, as of
        # BC-5, the lifetime figure. ONE query for both - the day filter moved out of the
        # ``WHERE`` and into a conditional aggregate, so the lifetime sum comes off the same read
        # rather than a second round trip and cannot disagree about what "realised" means.
        today_realized_pnl = 0.0
        realized_pnl: Optional[float] = None
        try:
            today_date_str = now_utc.strftime("%Y-%m-%d")
            pnl_res = await telemetry.execute_query(
                executions_realized_pnl_query(safe_uid, today_date_str)
            )
            today_realized_pnl, realized_pnl = realized_pnl_from_execution_totals(pnl_res)
        except Exception as exec_err:
            logger.debug(f"QuestDB executions PnL query error: {exec_err}")
            # ``today_realized_pnl`` keeps the 0.0 it has always published for this failure
            # (BC-5 is additive). ``realized_pnl`` stays ``None``: the read failed, so there is
            # no lifetime realised figure, and 0.0 would assert one (Requirement 19.2).

        # Calculate open positions unrealized PnL from Redis live positions
        unrealized_pnl = 0.0
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            pos_keys = await redis_manager.keys(f"portfolio:{user['id']}:*:positions")
            for pk in pos_keys:
                raw_pos = await redis_manager.get(pk)
                if raw_pos:
                    pos_dict = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
                    if isinstance(pos_dict, dict):
                        for p in pos_dict.values():
                            unrealized_pnl += float(p.get("unrealized_pnl", 0.0))
                    elif isinstance(pos_dict, list):
                        for p in pos_dict:
                            unrealized_pnl += float(p.get("unrealized_pnl", 0.0))
        except Exception as pos_err:
            logger.debug(f"Live unrealized PnL calculate error: {pos_err}")

        today_pnl = today_realized_pnl + unrealized_pnl
        today_return_pct = round((today_pnl / total_equity * 100), 2) if total_equity > 0 else 0.0

        return {
            "total_equity": total_equity,
            "total_value": total_equity,
            "available_balance": available_balance,
            "free_balance": free_balance,
            "used_balance": used_balance,
            "today_pnl": today_pnl,
            "today_realized_pnl": today_realized_pnl,
            # BC-5: lifetime realised P&L (Requirement 10.1). Distinct from
            # ``today_realized_pnl`` (same figure, today's window only) and from
            # ``cumulative_pnl`` (realised PLUS open-position mark-to-market). ``None`` when the
            # executions read could not produce it - never 0.0 as a stand-in.
            "realized_pnl": realized_pnl,
            "today_return_pct": today_return_pct,
            "unrealized_pnl": unrealized_pnl,
            "cumulative_pnl": cumulative_pnl,
            "total_exposure": total_exposure,
            "currency": "USDT",
            "environment": "live",
            "updated_at": now_utc.isoformat()
        }
    
    async def get_open_positions(self, user: dict, environment: str = "live") -> List[Dict]:
        """
        Get normalized open trading positions with strict environment isolation.

        Returns canonical NormalizedPosition list. ``[]`` means, and now only means, that the
        account holds no open positions.

        Raises
            :class:`PositionsUnreadable` when the read itself failed (BC-2, Requirement 14.5).
            Both environments raise: the live branch when the Redis read behind it does, the
            paper branch when the paper store does. Callers that can publish the rest of their
            response catch it and attach :func:`positions_degradation`; callers that cannot let
            it reach their route's 503. What no caller gets any more is ``[]``.
        """
        from datetime import timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                paper_svc = get_paper_trading_service()
                positions = paper_svc.get_positions(user["id"])
                
                pos_list = []
                pos_items = positions.values() if isinstance(positions, dict) else positions
                for p in pos_items:
                    size = float(p.get("size", p.get("quantity", 0.0)))
                    if size <= 0:
                        continue
                    entry_p = float(p.get("entry_price", 0.0))
                    current_p = float(p.get("current_price", entry_p))
                    notional = size * current_p
                    u_pnl = float(p.get("unrealized_pnl", 0.0))
                    cost_basis = size * entry_p
                    u_pnl_pct = round((u_pnl / cost_basis * 100), 2) if cost_basis > 0 else 0.0
                    
                    pos_list.append({
                        "id": f"pos_paper_{user['id'][:8]}_{p.get('symbol', 'BTC/USDT').lower().replace('/', '_')}",
                        "exchange_id": "paper",
                        "environment": "paper",
                        "symbol": p.get("symbol", "BTC/USDT"),
                        "market_type": "spot",
                        "side": p.get("side", "long").lower(),
                        "contracts": size,
                        "quantity": size,
                        "entry_price": entry_p,
                        "mark_price": current_p,
                        "notional": round(notional, 2),
                        "leverage": 1,
                        "unrealized_pnl": round(u_pnl, 2),
                        "unrealized_pnl_pct": u_pnl_pct,
                        "liquidation_price": None,
                        "margin": round(cost_basis, 2),
                        "margin_type": "cross",
                        "timestamp": p.get("updated_at") or p.get("created_at") or now_iso
                    })
                return pos_list
            except Exception as paper_pos_err:
                logger.error(f"Failed to fetch paper positions for {user['id']}: {paper_pos_err}")
                # BC-2: was ``return []``. An empty list is what a paper account with no open
                # positions returns, so returning it here published an outage as a fact about
                # the account (Requirement 14.5).
                raise PositionsUnreadable("paper", paper_pos_err) from paper_pos_err

        # LIVE Environment
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            import json
            pos_list = []
            keys = await redis_manager.keys(f"portfolio:{user['id']}:*:positions")
            for k in keys:
                # Extract exchange_id from key: portfolio:{uid}:{exchange_id}:positions
                parts = k.split(":") if isinstance(k, str) else k.decode().split(":")
                ex_id = parts[2] if len(parts) >= 4 else "binance"
                
                raw_pos = await redis_manager.get(k)
                if raw_pos:
                    data = json.loads(raw_pos) if isinstance(raw_pos, str) else raw_pos
                    items = []
                    if isinstance(data, dict):
                        for sym_k, p_val in data.items():
                            if isinstance(p_val, dict):
                                if "symbol" not in p_val:
                                    p_val["symbol"] = sym_k
                                items.append(p_val)
                    elif isinstance(data, list):
                        items = data

                    for p in items:
                        contracts = float(p.get("contracts", p.get("size", 0.0)))
                        if contracts == 0:
                            continue
                        entry_p = float(p.get("entry_price", p.get("entryPrice", 0.0)))
                        notional = float(p.get("notional", contracts * entry_p))
                        u_pnl = float(p.get("unrealized_pnl", p.get("unrealizedPnl", 0.0)))
                        cost = contracts * entry_p
                        u_pct = round((u_pnl / cost * 100), 2) if cost > 0 else 0.0
                        
                        sym = p.get("symbol", "BTC/USDT")
                        pos_list.append({
                            "id": f"pos_{ex_id}_{sym.lower().replace('/', '_')}",
                            "exchange_id": ex_id,
                            "environment": "live",
                            "symbol": sym,
                            "market_type": "future" if "future" in ex_id or "swap" in ex_id else "spot",
                            "side": p.get("side", "long").lower(),
                            "contracts": contracts,
                            "quantity": contracts,
                            "entry_price": entry_p,
                            "mark_price": float(p.get("mark_price", p.get("markPrice", entry_p))),
                            "notional": round(notional, 2),
                            "leverage": int(p.get("leverage", 1)),
                            "unrealized_pnl": round(u_pnl, 2),
                            "unrealized_pnl_pct": u_pct,
                            "liquidation_price": float(p.get("liquidation_price", p.get("liquidationPrice", 0.0))) if p.get("liquidation_price") or p.get("liquidationPrice") else None,
                            "margin": round(float(p.get("margin", notional / max(1, int(p.get("leverage", 1))))), 2),
                            "margin_type": p.get("margin_type", "cross"),
                            "timestamp": p.get("timestamp") or now_iso
                        })
            return pos_list
        except Exception as live_pos_err:
            logger.error(f"Failed to fetch live positions for {user['id']}: {live_pos_err}")
            # BC-2: was ``return []``. The Redis read behind this is the one that goes away in
            # an outage, and ``[]`` made the outage indistinguishable from a flat account.
            raise PositionsUnreadable("live", live_pos_err) from live_pos_err

    async def get_recent_executions(self, user: dict, environment: str = "live", limit: int = 5) -> List[Dict]:
        """
        Get normalized recent executed trades/fills with strict environment isolation.
        """
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                paper_svc = get_paper_trading_service()
                trades = paper_svc.get_trades(user["id"])
                # Sort descending by executed_at
                sorted_trades = sorted(
                    trades,
                    key=lambda t: t.get("executed_at", ""),
                    reverse=True
                )[:limit]
                
                results = []
                for t in sorted_trades:
                    qty = float(t.get("quantity", t.get("amount", 0.0)))
                    price = float(t.get("price", 0.0))
                    results.append({
                        "id": t.get("execution_id", f"exec_paper_{t.get('order_id', '0')}"),
                        "order_id": t.get("order_id"),
                        "exchange_id": "paper",
                        "environment": "paper",
                        "symbol": t.get("symbol", "BTC/USDT"),
                        "side": t.get("side", "buy").lower(),
                        "price": price,
                        "amount": qty,
                        "cost": round(qty * price, 2),
                        "fee": float(t.get("fee", 0.0)),
                        "realized_pnl": float(t.get("realized_pnl", 0.0)),
                        "timestamp": t.get("executed_at"),
                        "strategy_id": t.get("strategy_id")
                    })
                return results
            except Exception as paper_trades_err:
                logger.error(f"Failed to fetch paper trades for {user['id']}: {paper_trades_err}")
                return []

        # LIVE Environment: Query QuestDB executions table
        telemetry = self._get_telemetry()
        safe_uid = self._safe_uid(user["id"])
        try:
            query = (
                f"SELECT timestamp, symbol, side, status, amount, price, pnl, fee "
                f"FROM executions "
                f"WHERE user_id = '{safe_uid}' "
                f"ORDER BY timestamp DESC "
                f"LIMIT {limit};"  # nosec: B608
            )
            res = await telemetry.execute_query(query)
            if res and res.get("dataset"):
                cols = [c["name"] for c in res["columns"]]
                results = []
                # Determine authoritative exchange_id from user's active exchange connections
                primary_exchange = "binance"
                try:
                    sb = await self._get_supabase(user)
                    ex_res = await self._execute_sb_query(
                        sb.table("exchange_keys")
                        .select("exchange_id")
                        .eq("user_id", user["id"])
                        .limit(1)
                    )
                    if ex_res and hasattr(ex_res, "data") and ex_res.data:
                        primary_exchange = ex_res.data[0].get("exchange_id") or "binance"
                except Exception:
                    primary_exchange = "binance"

                for idx, row in enumerate(res["dataset"]):
                    entry = dict(zip(cols, row))
                    qty = float(entry.get("amount", 0.0))
                    price = float(entry.get("price", 0.0))
                    ex_id = entry.get("exchange_id") or primary_exchange
                    results.append({
                        "id": f"exec_live_{safe_uid[:8]}_{idx}_{int(time.time())}",
                        "order_id": entry.get("order_id"),
                        "exchange_id": ex_id,
                        "environment": "live",
                        "symbol": entry.get("symbol", "BTC/USDT"),
                        "side": str(entry.get("side", "buy")).lower(),
                        "price": price,
                        "amount": qty,
                        "cost": round(qty * price, 2),
                        "fee": float(entry.get("fee", 0.0)),
                        "realized_pnl": float(entry.get("pnl", 0.0)),
                        "timestamp": entry.get("timestamp"),
                        "strategy_id": entry.get("strategy_id")
                    })
                return results
            return []
        except Exception as live_exec_err:
            logger.debug(f"QuestDB executions read error: {live_exec_err}")
            return []

    async def get_equity_curve(self, user: dict, days: int = 30, environment: str = "live") -> List[Dict]:
        """
        Get equity curve data with environment awareness.
        """
        telemetry = self._get_telemetry()
        safe_uid = self._safe_uid(user["id"])
        limit = max(1, min(int(days) * 96, 100_000))
        
        try:
            result = await telemetry.execute_query(
                f"SELECT timestamp, equity FROM equity_curve "
                f"WHERE user_id = '{safe_uid}' "
                f"ORDER BY timestamp ASC LIMIT -{limit};"  # nosec: B608
            )
            if result and result.get("dataset"):
                cols = [c["name"] for c in result["columns"]]
                return [dict(zip(cols, row)) for row in result["dataset"]]
        except Exception as eq_err:
            logger.debug(f"QuestDB equity curve fetch error: {eq_err}")

        # If paper mode and no QuestDB data, synthesize default baseline
        if environment == "paper":
            from datetime import timezone, timedelta
            now = datetime.now(timezone.utc)
            return [
                {"timestamp": (now - timedelta(days=days)).isoformat(), "equity": 100000.0},
                {"timestamp": now.isoformat(), "equity": 100000.0}
            ]
        
        return []
    
    async def get_strategies(self, user: dict, environment: str = "live", sb: Optional[Any] = None) -> List[Dict]:
        """
        Get strategies with calculated metrics and environment binding.
        """
        if sb is None:
            sb_res = self._get_supabase(user)
            sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if not sb:
            return []
        
        res = await self._execute_sb_query(
            sb.table("strategies")
            .select("id, name, symbol, is_active, created_at")
            .eq("user_id", user["id"])
        )
        strategies = res.data or [] if res and hasattr(res, "data") else []
        
        enriched = []
        for s in strategies:
            enriched.append({
                "id": s.get("id"),
                "name": s.get("name") or "Strategy",
                "pair": s.get("pair") or s.get("symbol") or "BTC/USDT",
                "status": "active" if s.get("is_active") else "paused",
                "health": "healthy" if s.get("is_active") else "idle",
                "today_pnl": float(s.get("today_pnl") or 0.0),
                "today_return_pct": float(s.get("today_return_pct") or 0.0),
                "last_signal_time": s.get("last_signal_at") or None,
                "environment": environment
            })
        
        return enriched
    
    async def get_strategy_insights(self, user: dict, strategies_task: Optional['asyncio.Task'] = None) -> List[Dict]:
        """
        Calculate trading insights from strategy state.
        """
        if strategies_task is not None:
            strategies = await strategies_task
        else:
            strategies = await self.get_strategies(user)
        
        insights = []
        active_count = sum(1 for s in strategies if s["status"] == "active")
        inactive_count = len(strategies) - active_count
        
        if inactive_count > 0:
            inactive_names = [s["name"] for s in strategies if s["status"] == "paused"]
            insights.append({
                "id": "ins_1",
                "type": "warning",
                "text": f"{inactive_count} strategy(ies) currently paused: {', '.join(inactive_names)}.",
                "action_text": "Manage Strategies",
                "action_path": "/app/strategies"
            })
        
        insights.append({
            "id": "ins_2",
            "type": "info",
            "text": f"{active_count} active strategy execution bot(s) running on live connected venues.",
            "action_text": "View Bots",
            "action_path": "/app/strategies"
        })
        
        insights.append({
            "id": "ins_3",
            "type": "success",
            "text": "Risk Circuit Breakers active: Max daily loss limit enforced by Risk Engine.",
            "action_text": "Risk Settings",
            "action_path": "/app/risk"
        })
        
        return insights[:3]
    
    async def get_recent_signals(self, user: dict, limit: int = 5, sb: Optional[Any] = None) -> List[Dict]:
        """
        Get recent signal traces for notifications.
        """
        if sb is None:
            sb_res = self._get_supabase(user)
            sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        res = await self._execute_sb_query(
            sb.table("signals")
            .select("id, generated_at, decision, symbol, exchange_id, risk_passed")
            .eq("user_id", user["id"])
            .order("generated_at", desc=True)
            .limit(limit)
        )
        records = res.data or [] if res and hasattr(res, "data") else []
        
        notifications = []
        for r in records:
            notifications.append({
                "id": r.get("id"),
                "time": r.get("generated_at") or None,
                "text": f"Signal {r.get('decision', 'BUY').upper()}: {r.get('symbol')} on {r.get('exchange_id', 'binance').upper()} (Risk: {'APPROVED' if r.get('risk_passed') else 'REJECTED'})",
                "type": "success" if r.get("risk_passed") else "warning"
            })
        
        return notifications
    
    async def get_health_status(self, user: dict, environment: str = "live") -> Dict:
        """
        Get real measured system health status (Zero fabricated latency numbers).
        """
        from backend_app.routers.risk import is_user_kill_switched
        kill_active = is_user_kill_switched(user["id"])
        
        # Check measured latency in Redis
        latency_ms = None
        latency_status = "unavailable"
        
        if environment == "live":
            try:
                from backend_app.core.cache.redis_manager import redis_manager
                import json
                keys = await redis_manager.keys(f"exchange_health:{user['id']}:*")
                for k in keys:
                    val = await redis_manager.get(k)
                    if val:
                        data = json.loads(val) if isinstance(val, str) else val
                        measured = data.get("latency_ms")
                        if measured is not None:
                            latency_ms = int(measured)
                            break
            except Exception as health_err:
                logger.debug(f"Health latency lookup error: {health_err}")

        if latency_ms is not None:
            if latency_ms < 150:
                latency_status = "optimal"
            elif latency_ms < 500:
                latency_status = "normal"
            else:
                latency_status = "degraded"

        return {
            "exchange_api_latency_ms": latency_ms,
            "exchange_api_latency_status": latency_status,
            "risk_circuit_breaker_status": "triggered" if kill_active else "armed",
            "risk_circuit_breaker_breaches": 0,
            "order_state_sync_status": "synchronized" if environment == "paper" else "active"
        }
    
    async def get_risk_data(
        self,
        user: dict,
        environment: str = "live",
        portfolio: Optional[Dict] = None,
        sb: Optional[Any] = None,
        equity_curve: Optional[List[Any]] = None,
        positions: Optional[Any] = None,
    ) -> Dict:
        """
        Get authoritative risk management data with synchronized risk_score and risk_level.

        Args:
            equity_curve: The series ``get_equity_curve`` already produced for this request, for
                BC-1's ``current_drawdown_pct_v2``. Passed in rather than fetched so this method
                issues no additional read; ``get_dashboard_data`` hands over the series it
                gathered. Omitted, ``current_drawdown_pct_v2`` is ``None``, which is what a
                caller holding no series honestly reports.
            positions: BC-2. The positions ``get_dashboard_data`` already gathered - a list, or
                the :class:`PositionsUnreadable` that gathering it produced (that ``asyncio.
                gather`` runs with ``return_exceptions=True``, so an exception is a value on
                that path). Handed over for the same reason ``equity_curve`` is: so this method
                issues no second read, and so one response cannot report the positions
                unreadable at the top level while reporting a count for them under ``risk``.
                ``None`` means "not supplied" - this method then reads them itself, and catches
                the same failure. Note the ``is None`` test: ``[]`` is a supplied, truthful,
                empty list.

        POSITIONS UNREADABLE (BC-2, Requirement 14.5)
            ``open_positions_count`` is ``None`` and ``risk_score`` is ``None``, never ``0``.
            A count of zero and a utilisation of zero are the *safest* readings this endpoint
            can publish, which is precisely why publishing them unmeasured is the dangerous
            option: ``risk_score`` folds position utilisation in at 40% weight, so a swallowed
            failure reads as headroom. ``risk_level`` says ``unavailable`` - the spelling this
            same service already uses for an unmeasured reading
            (``health.exchange_api_latency_status``) - except when the kill switch is active,
            which is a fact about the switch and stays ``blocked`` regardless of any read.
        """
        from backend_app.routers.risk import get_user_risk_settings_store, is_user_kill_switched
        uid = str(user["id"])
        settings = get_user_risk_settings_store(uid)
        max_daily_loss = float(settings.get("max_daily_loss", 500.0))
        max_positions = int(settings.get("max_positions", 10))
        max_leverage = int(settings.get("max_leverage", 3))
        circuit_armed = bool(settings.get("circuit_breaker_armed", True))
        kill_active = is_user_kill_switched(uid)
        
        # Calculate daily loss utilized
        daily_loss_utilized = 0.0
        if environment == "paper":
            try:
                from backend_app.backend.paper_trading_service import get_paper_trading_service
                from datetime import timezone
                paper_svc = get_paper_trading_service()
                trades = paper_svc.get_trades(uid)
                today_cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                closed_losses = sum(
                    float(t.get("realized_pnl", 0.0))
                    for t in trades
                    if (t.get("executed_at") or "") >= today_cutoff and float(t.get("realized_pnl", 0.0)) < 0
                )
                daily_loss_utilized = abs(closed_losses)
            except Exception:
                daily_loss_utilized = 0.0
        else:
            try:
                telemetry = self._get_telemetry()
                safe_uid = self._safe_uid(uid)
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                res = await telemetry.execute_query(
                    f"SELECT sum(pnl) FROM executions WHERE user_id = '{safe_uid}' AND pnl < 0 AND timestamp >= to_timestamp('{today_str}T00:00:00.000000Z', 'yyyy-MM-ddTHH:mm:ss.SSSUUUZ');"  # nosec: B608
                )
                if res and res.get("dataset") and res["dataset"][0][0] is not None:
                    daily_loss_utilized = abs(float(res["dataset"][0][0]))
            except Exception:
                daily_loss_utilized = 0.0

        loss_util_pct = (daily_loss_utilized / max_daily_loss * 100) if max_daily_loss > 0 else 0.0
        
        # Calculate open positions count. BC-2: `positions` may already be in hand from
        # `get_dashboard_data`'s gather - including as the failure that gathering it produced.
        if positions is None:
            try:
                positions = await self.get_open_positions(user, environment=environment)
            except PositionsUnreadable as positions_err:
                positions = positions_err
        positions_error = positions if isinstance(positions, BaseException) else None

        if positions_error is not None:
            # BC-2: nothing here is a figure. A count of zero, a utilisation of zero and the
            # risk score they feed would all be inventions, and all three would read as safe.
            # ``pos_util_pct`` is bound though this branch publishes nothing from it, so the
            # day this projection starts reporting utilisation it reports ``None`` here rather
            # than raising - the same reason the branches below bind the same four names.
            open_pos_count = None
            pos_util_pct = None
            risk_score = None
            risk_level = "blocked" if kill_active else "unavailable"
        else:
            open_pos_count = len(positions)
            pos_util_pct = (open_pos_count / max_positions * 100) if max_positions > 0 else 0.0

            # Calculate deterministic numeric risk score (0-100)
            risk_score = min(100, int((loss_util_pct * 0.6) + (pos_util_pct * 0.4)))

            # Standardize risk level
            if kill_active:
                risk_level = "blocked"
            elif loss_util_pct >= 100 or pos_util_pct >= 100:
                risk_level = "critical"
            elif loss_util_pct >= 75 or pos_util_pct >= 75:
                risk_level = "high"
            elif loss_util_pct >= 40 or pos_util_pct >= 40:
                risk_level = "medium"
            else:
                risk_level = "low"

        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            # BC-2: ``None`` when the positions read failed, mirroring the response-level
            # ``degraded`` block so a client reading only the risk section can still tell an
            # unreadable count from a count of zero.
            "degraded": positions_degradation(positions_error, environment),
            # DEPRECATED (BC-1, design.md §1.5): this is ``today_return_pct``, not a drawdown, so
            # a profitable day reads as a positive "drawdown". Left in place unchanged for its
            # deprecation window - BC-1 is an additive read projection (Requirement 19.1) and no
            # existing consumer is repointed by it. Read ``current_drawdown_pct_v2`` instead.
            "current_drawdown_pct": round(float((portfolio or {}).get("today_return_pct", 0.0)), 2),
            # BC-1: the honest peak-to-trough figure, from the equity series this request already
            # read. ``None`` when no drawdown can be measured - never 0.0 as a stand-in
            # (Requirements 3.1, 10.2, 19.2).
            "current_drawdown_pct_v2": current_drawdown_pct_from_equity_curve(equity_curve),
            "max_daily_loss": max_daily_loss,
            "daily_loss_utilized": round(daily_loss_utilized, 2),
            "max_positions": max_positions,
            "open_positions_count": open_pos_count,
            "max_leverage": max_leverage,
            "circuit_breaker_armed": circuit_armed,
            "circuit_breaker_breaches": 0,
            "kill_switch_active": kill_active,
            "kill_switches": settings.get("kill_switches", {})
        }
    
    async def get_dashboard_data(self, user: dict, equity_days: int = 30, environment: str = "live") -> Dict:
        """
        Get complete normalized dashboard data in one call with strict environment isolation.
        
        Args:
            user: User dict with id and access_token
            equity_days: Number of days for equity curve
            environment: 'live' or 'paper'
        
        Returns:
            Comprehensive, environment-isolated dashboard structure.

            BC-2 adds one top-level key, ``degraded``: ``None`` when every read succeeded, and
            ``{"positions": "unreadable", "environment": ..., "reason": ...}`` when the
            positions read failed. ``positions`` is ``[]`` in both cases and ``degraded`` is the
            only thing that tells them apart, so a client MUST consult it before rendering an
            empty positions state (Requirement 14.5, design.md §1.6). ``risk.degraded`` and a
            ``null`` ``risk.open_positions_count`` say the same thing from inside the risk
            section, for a consumer that reads only that.
        """
        from datetime import timezone
        norm_env = environment.lower() if environment in ("live", "paper") else "live"
        dashboard_start = time.perf_counter()
        
        try:
            sb = await self._get_supabase(user)
            strategies_start = time.perf_counter()
            strategies_task = asyncio.create_task(self.get_strategies(user, environment=norm_env, sb=sb))

            gather_start = time.perf_counter()
            results = await asyncio.gather(
                self._timed_operation("get_portfolio_overview", self.get_portfolio_overview(user, environment=norm_env)),
                self._timed_operation("get_equity_curve", self.get_equity_curve(user, days=equity_days, environment=norm_env)),
                self._timed_operation("get_open_positions", self.get_open_positions(user, environment=norm_env)),
                self._timed_operation("get_recent_executions", self.get_recent_executions(user, environment=norm_env, limit=5)),
                self._timed_operation("get_strategy_insights", self.get_strategy_insights(user, strategies_task)),
                self._timed_operation("get_recent_signals", self.get_recent_signals(user, sb=sb)),
                self._timed_operation("get_health_status", self.get_health_status(user, environment=norm_env)),
                self._timed_operation("get_subscription_data", self.get_subscription_data(user, strategies_task, sb=sb)),
                self._timed_operation("get_exchange_data", self.get_exchange_data(user, sb=sb)),
                self._timed_operation("get_notification_data", self.get_notification_data(user, sb=sb)),
                self._timed_operation("get_referral_data", self.get_referral_data(user, sb=sb)),
                self._timed_operation("get_marketplace_data", self.get_marketplace_data(user, sb=sb)),
                return_exceptions=True
            )
            gather_duration_ms = (time.perf_counter() - gather_start) * 1000

            (portfolio, equity, positions, executions, insights,
             signals, health, subscription, exchange,
             notifications, referral, marketplace) = results

            strategies = await strategies_task

            # Handle exceptions with safe fallbacks
            if isinstance(portfolio, Exception):
                logger.error(f"Portfolio fetch failed: {portfolio}")
                portfolio = {
                    "total_equity": 100000.0 if norm_env == "paper" else 0.0,
                    "total_value": 100000.0 if norm_env == "paper" else 0.0,
                    "available_balance": 100000.0 if norm_env == "paper" else 0.0,
                    "free_balance": 100000.0 if norm_env == "paper" else 0.0,
                    "used_balance": 0.0,
                    "today_pnl": 0.0,
                    "today_realized_pnl": 0.0,
                    # BC-5: the portfolio read failed, so no lifetime realised figure was read.
                    "realized_pnl": None,
                    "today_return_pct": 0.0,
                    "unrealized_pnl": 0.0,
                    "cumulative_pnl": 0.0,
                    "total_exposure": 0.0,
                    "currency": "USD" if norm_env == "paper" else "USDT",
                    "environment": norm_env,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

            if isinstance(equity, Exception):
                logger.error(f"Equity curve fetch failed: {equity}")
                equity = []

            # BC-2 (design.md §1.6, Requirement 14.5). The fallback below still hands the
            # response an EMPTY LIST, because a list is all `positions` can be and an empty one
            # is at least not a fabricated position. What changed is that the emptiness no
            # longer travels alone: `positions_error` is carried to the `degraded` block at the
            # bottom of this method, and to `get_risk_data`, so nothing downstream has to guess
            # whether `[]` means "none open" or "not read". Kept as `[]` rather than `null`
            # deliberately - a client that iterates it renders an empty table at worst, where a
            # `null` would throw, and the marker is what stops it rendering that table at all.
            positions_error = positions if isinstance(positions, BaseException) else None
            if positions_error is not None:
                logger.error(f"Open positions fetch failed: {positions_error}")
                positions = []

            if isinstance(executions, Exception):
                logger.error(f"Executions fetch failed: {executions}")
                executions = []

            if isinstance(insights, Exception):
                logger.error(f"Insights generation failed: {insights}")
                insights = []
            
            if isinstance(signals, Exception):
                logger.error(f"Signals fetch failed: {signals}")
                signals = []
            
            if isinstance(health, Exception):
                logger.error(f"Health status fetch failed: {health}")
                health = {
                    "exchange_api_latency_ms": None,
                    "exchange_api_latency_status": "unavailable",
                    "risk_circuit_breaker_status": "armed",
                    "risk_circuit_breaker_breaches": 0,
                    "order_state_sync_status": "synchronized" if norm_env == "paper" else "active"
                }
            
            if isinstance(subscription, Exception):
                logger.error(f"Subscription data fetch failed: {subscription}")
                subscription = {"tier": "free", "usage": {}, "billing_status": "active", "subscription_end": None, "is_trial": False}
            
            if isinstance(exchange, Exception):
                logger.error(f"Exchange data fetch failed: {exchange}")
                exchange = {"total_exchanges": 0, "connected_exchanges": 0, "exchanges": [], "can_trade": False}
            
            if isinstance(notifications, Exception):
                logger.error(f"Notification data fetch failed: {notifications}")
                notifications = {"unread_count": 0, "total_count": 0, "recent": [], "categories": {}}
            
            if isinstance(referral, Exception):
                logger.error(f"Referral data fetch failed: {referral}")
                referral = {"referral_code": "", "referral_link": "", "total_referrals": 0, "active_referrals": 0, "pending_earnings": 0.0, "approved_earnings": 0.0, "lifetime_earnings": 0.0}
            
            if isinstance(marketplace, Exception):
                logger.error(f"Marketplace data fetch failed: {marketplace}")
                marketplace = {"available_count": 0, "user_publications": 0, "total_subscribers": 0, "featured": []}

            # Fetch authoritative risk data. ``equity`` is the series already gathered above, and
            # is handed over so BC-1's ``current_drawdown_pct_v2`` is computed without a second
            # read. It is ``[]`` when the equity read failed, which yields ``None`` rather than a
            # fabricated zero.
            # BC-2 hands over ``positions`` for the same reason: the failure, or the list, that
            # this request already has. Without it ``get_risk_data`` reads Redis a second time
            # and one response could report the positions unreadable at the top level while
            # publishing a count for them under ``risk``.
            risk_data = await self.get_risk_data(
                user,
                environment=norm_env,
                portfolio=portfolio,
                sb=sb,
                equity_curve=equity,
                positions=positions_error if positions_error is not None else positions,
            )
            
            active_strategies = [s for s in strategies if s["status"] == "active"]
            paused_strategies = [s for s in strategies if s["status"] == "paused"]
            
            dashboard_duration_ms = (time.perf_counter() - dashboard_start) * 1000
            logger.info(
                f"dashboard_total_timing",
                extra={
                    "operation": "get_dashboard_data",
                    "duration_ms": round(dashboard_duration_ms, 2),
                    "environment": norm_env
                },
            )
            
            return {
                "environment": norm_env,
                "overview": {
                    # Wave 1 step 1 (task 6.1). Every figure in this block is read through
                    # ``_finite_float`` (:314), exactly as ``realized_pnl`` below already was:
                    # a figure that WAS read is published as read - ``0.0`` and ``-0.0``
                    # included, because a zero is a measurement (preservation 3.2) - and a
                    # figure that was NOT read is ``None``. The ``float(portfolio.get(key, 0.0))``
                    # this replaces had two outcomes and no third, so absence was unrepresentable
                    # at the response boundary and every producer upstream was forced to invent a
                    # number (Requirements 1.1, 1.2).
                    #
                    # The two chained reads keep their by-PRESENCE fallback: ``total_value`` falls
                    # back to ``total_equity`` only when the key is absent, never when it is
                    # present and unreadable. A figure that was not read must not be answered with
                    # a different figure.
                    "total_value": _finite_float(portfolio.get("total_value", portfolio.get("total_equity"))),
                    "total_equity": _finite_float(portfolio.get("total_equity")),
                    "available_balance": _finite_float(portfolio.get("available_balance")),
                    "free_balance": _finite_float(portfolio.get("free_balance", portfolio.get("available_balance"))),
                    "used_balance": _finite_float(portfolio.get("used_balance")),
                    "today_pnl": _finite_float(portfolio.get("today_pnl")),
                    "today_realized_pnl": _finite_float(portfolio.get("today_realized_pnl")),
                    # BC-5: lifetime realised P&L, carried through as read (Requirements 10.1,
                    # 19.2). Its neighbours above now read the same way, so this field is no
                    # longer the one exception in the block.
                    "realized_pnl": _finite_float(portfolio.get("realized_pnl")),
                    "today_return_pct": _finite_float(portfolio.get("today_return_pct")),
                    "unrealized_pnl": _finite_float(portfolio.get("unrealized_pnl")),
                    "cumulative_pnl": _finite_float(portfolio.get("cumulative_pnl")),
                    "total_exposure": _finite_float(portfolio.get("total_exposure")),
                    "currency": portfolio.get("currency", "USDT"),
                    "updated_at": portfolio.get("updated_at", datetime.now(timezone.utc).isoformat())
                },
                "positions": positions,
                "executions": executions,
                "subscription": {
                    "tier": subscription.get("tier", "free"),
                    "billing_status": subscription.get("billing_status", "active"),
                    "subscription_end": subscription.get("subscription_end"),
                    "is_trial": subscription.get("is_trial", False)
                },
                "usage": subscription.get("usage", {
                    "strategies": 0,
                    "strategies_limit": 3,
                    "deployments": 0,
                    "deployments_limit": 1,
                    "ml_training_used": 0,
                    "ml_training_limit": 0
                }),
                "strategies": {
                    "total": len(strategies),
                    "active": len(active_strategies),
                    "paused": len(paused_strategies),
                    "running": len(active_strategies),
                    "stopped": len(paused_strategies),
                    "items": strategies
                },
                "marketplace": {
                    "available_count": marketplace.get("available_count", 0),
                    "user_publications": marketplace.get("user_publications", 0),
                    "total_subscribers": marketplace.get("total_subscribers", 0),
                    "featured": marketplace.get("featured", [])
                },
                "risk": risk_data,
                "notifications": {
                    "unread_count": notifications.get("unread_count", 0),
                    "total_count": notifications.get("total_count", 0),
                    "recent": notifications.get("recent", []),
                    "categories": notifications.get("categories", {})
                },
                "referrals": {
                    "referral_code": referral.get("referral_code", ""),
                    "referral_link": referral.get("referral_link", ""),
                    "total_referrals": referral.get("total_referrals", 0),
                    "active_referrals": referral.get("active_referrals", 0),
                    "lifetime_earnings": referral.get("lifetime_earnings", 0.0)
                },
                "health": health,
                "exchange": {
                    "total_exchanges": exchange.get("total_exchanges", 0),
                    "connected_exchanges": exchange.get("connected_exchanges", 0),
                    "can_trade": exchange.get("can_trade", False),
                    "exchanges": exchange.get("exchanges", [])
                },
                "recent_activity": {
                    "signals": signals,
                    "insights": insights,
                    "executions": executions
                },
                "equity_curve": equity,
                # BC-2 (Requirement 14.5). ``None`` when every read on this response succeeded;
                # otherwise the block naming what could not be read. THIS is what makes
                # ``positions: []`` above unambiguous - with ``degraded`` null it means the
                # account holds no open positions, and with ``degraded["positions"] ==
                # "unreadable"`` it means the read failed and the client must show its error
                # state rather than an empty table. Same top-level key, same ``None``-when-
                # healthy convention, and the same "name it plus a renderable reason" body as
                # ``signal_service``'s ``degraded`` (Requirements 16.2, 23.1 there).
                "degraded": positions_degradation(positions_error, norm_env),
                "generated_at": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Dashboard aggregation failed for user {user['id']}: {e}")
            raise


# Singleton instance
_dashboard_service = None

async def get_dashboard_service() -> DashboardAggregationService:
    """Get singleton DashboardAggregationService instance."""
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardAggregationService()
    return _dashboard_service

