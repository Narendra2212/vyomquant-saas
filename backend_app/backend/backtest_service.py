"""
backend/backtest_service.py — Backtest Service

PHASE 5: Complete Backtest History and Reporting

Every strategy owns complete backtest history.
Store forever.
Display comprehensive backtest reports.

Provides:
- Backtest execution and storage
- Backtest history retrieval
- Detailed backtest reporting
- Backtest comparison
- Backtest metrics calculation
"""

import inspect
import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from backend_app.core.dependencies import create_request_supabase_async

logger = logging.getLogger("BacktestService")


# ══════════════════════════════════════════════════════════════════════════
#  ``strategy_backtests.executed_bar_count`` AVAILABILITY
#  (marketplace-subscriptions-paper-trading task 12.1, Requirements 3.8, 3.4, 25.1)
#
#  Nothing in this repository ever recorded how many bars a backtest actually
#  ran over: ``backtesting_engine.py`` line 189 checks ``len(price_data) < 50``
#  and throws the number away, and ``BacktestRuntime.run_backtest`` knows
#  ``len(ohlcv_data)`` and did not persist it. Requirement 3.8 needs that number
#  on the row, because the Evidence_Validator must reject a Backtest_Condition
#  whose bar count is null, absent or below 50 - and it must reject rather than
#  infer, so a NULL is a failed criterion and never a guess derived from the
#  equity curve's length.
#
#  The column is created by ``006_backtest_evidence_columns.sql``, which - like
#  004/004e/005a before it - is applied BY HAND: ``.github/workflows/03-deploy.yml``
#  has no migration step, so this code can reach production before the DDL does.
#  Naming an absent column in an UPDATE payload is PostgreSQL ``42703`` /
#  PostgREST ``PGRST204``, which would turn "the bar count was not recorded" into
#  "the whole backtest result was lost" - every metric, on a run that had already
#  finished computing them.
#
#  So availability is DETECTED, exactly the way ``strategy_service``,
#  ``deployment_binding`` and ``strategy_archive`` detect theirs:
#
#    1. Once per process, a read-only ``SELECT executed_bar_count ... LIMIT 1``
#       through the caller's own RLS-scoped client, cached in this module.
#    2. A definitive "absent" answer degrades the write: the bar count is dropped
#       from ``update_data``, a warning NAMING the migration is logged, and every
#       other metric is still persisted.
#    3. An inconclusive probe (network, auth, timeout) decides and caches nothing,
#       and resolves to "supported" so a real failure surfaces at the UPDATE
#       instead of being pre-emptively downgraded.
#    4. If the UPDATE itself then reports a missing column, that is newer
#       information than the probe had: it is remembered, and the write is retried
#       once without the bar count so the results still land.
#    5. The negative verdict expires after
#       :data:`EXECUTED_BAR_COUNT_RECHECK_SECONDS`, so applying 006 to a running
#       fleet takes effect without a redeploy. A positive one is kept for the life
#       of the process - 006 is additive and drops nothing.
#
#  Net effect: applying 006 late costs the bar count and a loud warning; it never
#  costs a completed backtest's metrics.
# ══════════════════════════════════════════════════════════════════════════

#: The one column task 12.1 starts writing.
EXECUTED_BAR_COUNT_COLUMN = "executed_bar_count"

#: The migration named in the degradation warning, so an operator is never left guessing.
BACKTEST_EVIDENCE_MIGRATION = "backend_app/migrations/006_backtest_evidence_columns.sql"

#: How long an "``executed_bar_count`` is absent" verdict is trusted before it is re-probed.
#: Same value and same reason as ``deployment_binding.BINDING_COLUMN_RECHECK_SECONDS``.
EXECUTED_BAR_COUNT_RECHECK_SECONDS = 300.0

#: PostgreSQL's ``undefined_column`` and PostgREST's schema-cache equivalents. ``PGRST205``
#: and ``42P01`` (a missing *table*) are deliberately absent: ``strategy_backtests`` is
#: created by migration 001, and a missing table there is a real problem, not something to
#: degrade around.
_MISSING_COLUMN_CODES = ("42703", "undefined_column", "pgrst204")

_executed_bar_count_supported: Optional[bool] = None
_executed_bar_count_checked_at: float = 0.0


def reset_executed_bar_count_support() -> None:
    """Forget the cached 006 verdict. For tests, and for an operator who just applied it."""
    global _executed_bar_count_supported, _executed_bar_count_checked_at
    _executed_bar_count_supported = None
    _executed_bar_count_checked_at = 0.0


def executed_bar_count_support_state() -> Optional[bool]:
    """The cached verdict: ``True``, ``False`` or ``None`` for "not yet determined"."""
    return _executed_bar_count_supported


def _remember_executed_bar_count_support(supported: bool) -> None:
    global _executed_bar_count_supported, _executed_bar_count_checked_at
    _executed_bar_count_supported = supported
    _executed_bar_count_checked_at = time.monotonic()


def remember_executed_bar_count_absent() -> None:
    """Record that 006 is not applied, so the next write degrades without re-probing.

    Public because the write path learns this the hard way: a process that cached a
    positive verdict and then meets ``42703`` at the UPDATE has newer information than the
    probe did. The negative verdict still expires after
    :data:`EXECUTED_BAR_COUNT_RECHECK_SECONDS`.
    """
    _remember_executed_bar_count_support(False)


def _cached_executed_bar_count_support() -> Optional[bool]:
    if _executed_bar_count_supported is None:
        return None
    if _executed_bar_count_supported:
        return True
    if (
        time.monotonic() - _executed_bar_count_checked_at
        >= EXECUTED_BAR_COUNT_RECHECK_SECONDS
    ):
        return None
    return False


def is_missing_executed_bar_count_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says the bar-count column does not exist.

    Narrow on purpose, for the same reason its three siblings are: anything this returns
    ``False`` for is re-raised, because the one outcome worse than a results write that
    fails loudly is one that fails silently and leaves a finished backtest stuck at
    ``running``.
    """
    text = str(exc).lower()
    if not text:
        return False
    if "pgrst205" in text or "42p01" in text:  # a missing TABLE, not a missing column
        return False
    if any(code in text for code in _MISSING_COLUMN_CODES):
        return True
    if EXECUTED_BAR_COUNT_COLUMN not in text:
        return False
    return any(
        phrase in text
        for phrase in ("does not exist", "schema cache", "could not find", "unknown column")
    )


def warn_executed_bar_count_absent(detail: str) -> None:
    """The degradation warning. Names the file, and says exactly what is not stored."""
    logger.warning(
        "strategy_backtests is missing the %s column. Apply %s, then restart or wait "
        "%.0fs for the re-probe. Until then the executed bar count is NOT persisted, so "
        "Requirement 3.8 is not met and the Evidence_Validator's bar-count criterion will "
        "fail for every backtest produced meanwhile. Every other result metric was still "
        "written. Detail: %s",
        EXECUTED_BAR_COUNT_COLUMN,
        BACKTEST_EVIDENCE_MIGRATION,
        EXECUTED_BAR_COUNT_RECHECK_SECONDS,
        detail,
    )


async def _execute(query: Any) -> Any:
    return await query if inspect.isawaitable(query) else query


async def executed_bar_count_supported(sb: Any) -> bool:
    """Whether ``strategy_backtests`` carries 006's ``executed_bar_count``.

    Read-only and cached: one ``SELECT executed_bar_count ... LIMIT 1`` per process through
    the caller's own RLS-scoped client, so the probe sees what the write will see. An
    *indeterminate* answer resolves to ``True`` so the write is attempted and any real
    failure surfaces at the UPDATE - the same disposition
    ``deployment_binding.binding_columns_supported`` takes.
    """
    cached = _cached_executed_bar_count_support()
    if cached is not None:
        return cached
    if sb is None:
        return False

    try:
        result = await _execute(
            sb.table("strategy_backtests")
            .select(EXECUTED_BAR_COUNT_COLUMN)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
        if is_missing_executed_bar_count_error(exc):
            _remember_executed_bar_count_support(False)
            warn_executed_bar_count_absent(str(exc))
            return False
        logger.warning(
            "The strategy_backtests.%s probe was inconclusive (%s); attempting the write "
            "and letting a real error surface.",
            EXECUTED_BAR_COUNT_COLUMN,
            exc,
        )
        return True

    # PostgREST clients that report errors on the response rather than by raising.
    error = getattr(result, "error", None)
    if error is not None and is_missing_executed_bar_count_error(Exception(str(error))):
        _remember_executed_bar_count_support(False)
        warn_executed_bar_count_absent(str(error))
        return False

    _remember_executed_bar_count_support(True)
    return True


def coerce_executed_bar_count(value: Any) -> Optional[int]:
    """``value`` as a non-negative ``int``, or ``None`` when it cannot be one.

    ``chk_sb_executed_bar_count`` allows ``NULL`` or ``>= 0``, so a negative or
    unparseable count is dropped rather than sent to be rejected by the CHECK - the caller
    is recording a fact about a run that already completed, and a bad count must not cost
    the metrics. ``bool`` is excluded because ``True`` is not a bar count.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


# ══════════════════════════════════════════════════════════════════════════
#  THE WRITER'S COLUMN → ENGINE-KEY MAPPING
#  (production-launch-hardening task 7.8, Requirements 1.7, 1.8, 2.7, 2.8, 3.4)
#
#  ``update_backtest_results`` used to read ``results.get("total_return", 0)``,
#  ``.get("win_rate", 0)``, ``.get("max_drawdown", 0)`` and
#  ``.get("final_capital", 0)``. ``backtesting_engine.run_backtest_async`` emits
#  NONE of those four names - it emits ``total_return_pct``, ``win_rate_pct``,
#  ``max_drawdown_pct`` and ``final_equity`` - so each of those four lookups
#  missed on every run and the writer persisted its own default into a column
#  that is then rendered to a trader as a measured result.
#
#  TWO RULES, AND BOTH OF THEM ARE THE POINT OF THIS TABLE.
#
#  1. WHERE A COLUMN NAME DIFFERS FROM THE KEY THAT PRODUCES IT, THE MAPPING IS
#     DECLARED HERE. It is not implied by a matching string in a dict literal,
#     because a matching string is exactly what was missing and nothing about a
#     ``results.get("win_rate")`` sitting next to ``"win_rate":`` says whether
#     the producer agreed. The four cross-spellings are greppable, reviewable
#     and asserted by ``tests/test_backtest_key_contract.py``, whose
#     ``READ_KEYS`` mirrors the right-hand column of this table.
#
#  2. NO NUMERIC DEFAULT. ``dict.get(key, 0)`` is a silent, type-preserving
#     translation of "absent" into "zero": the column comes back a plausible
#     number rather than an obviously missing one, which is why these four were
#     *wrong* rather than *missing* for as long as they were. A key the payload
#     genuinely does not carry now writes SQL ``NULL`` - the same rule wave 1
#     established - and every one of these columns is nullable in
#     ``001_strategy_architecture.sql``, whose ``DEFAULT 0`` applies to the
#     INSERT that ``create_backtest`` performs and not to this UPDATE.
#
#  MAP, DO NOT MIGRATE. Requirement 2.8 also permits renaming the columns to the
#  engine's spelling. That is a migration against a table that already holds
#  rows; this is reversible by reverting one commit, and a migration is not.
#
#  NOT IN THIS TABLE, deliberately:
#    * ``status`` and ``completed_at`` - written by this method, not read from
#      ``results``.
#    * ``executed_bar_count`` - a separate argument with its own availability
#      handling (see the block above); ``None`` there means "leave the column
#      alone", not "write NULL".
# ══════════════════════════════════════════════════════════════════════════

#: ``strategy_backtests`` column → the key in ``results`` that produces it. Read with no
#: default, so an absent key writes SQL ``NULL`` instead of a fabricated ``0``.
#: The four marked ``<-`` are the cross-spellings task 7.8 repointed.
RESULT_COLUMN_SOURCE_KEYS: Dict[str, str] = {
    "total_return":           "total_return_pct",       # <- was .get("total_return", 0)
    "total_return_pct":       "total_return_pct",
    "win_rate":               "win_rate_pct",           # <- was .get("win_rate", 0)
    "max_drawdown":           "max_drawdown_pct",       # <- was .get("max_drawdown", 0)
    "sharpe_ratio":           "sharpe_ratio",
    "sortino_ratio":          "sortino_ratio",
    "profit_factor":          "profit_factor",
    "total_trades":           "total_trades",
    "winning_trades":         "winning_trades",
    "losing_trades":          "losing_trades",
    "execution_time_seconds": "execution_time_seconds",
    "final_capital":          "final_equity",           # <- was .get("final_capital", 0)
}

#: The JSONB columns, kept separate because their default is ``[]`` and stays ``[]``.
#: An empty array is not the failure mode rule 2 above is about: it renders as an empty
#: chart or an empty trade table, not as a number a trader reads as measured. Narrowing
#: them to ``NULL`` would change what lands in a persisted column beyond what task 7.8
#: declares, so it is not done here.
RESULT_JSON_COLUMN_SOURCE_KEYS: Dict[str, str] = {
    "equity_curve":    "equity_curve",
    "monthly_returns": "monthly_returns",
    "daily_returns":   "daily_returns",
    "trades":          "trades",
}


class BacktestService:
    """
    Central service for all backtest operations.
    
    Every backtest is stored permanently with strategy.
    Complete history tracking and reporting.
    """
    
    def __init__(self):
        pass
    
    def _generate_dataset_checksum(self, dataset: str, start_date: str, end_date: str) -> str:
        """Generate a checksum for dataset reproducibility tracking."""
        import hashlib
        checksum_string = f"{dataset}:{start_date}:{end_date}"
        return hashlib.sha256(checksum_string.encode()).hexdigest()[:16]
    
    async def _get_supabase(self, user: dict):
        """Get Supabase client for user."""
        res = create_request_supabase_async(user.get("access_token"))
        return await res if inspect.isawaitable(res) else res
    
    async def create_backtest(
        self,
        user: dict,
        strategy_id: str,
        version: int,
        blueprint: dict,
        dataset: str,
        start_date: str,
        end_date: str,
        initial_capital: float,
        commission: float,
        slippage: float,
        version_id: Optional[str] = None,
    ) -> Dict:
        """
        Create a new backtest record.
        
        Args:
            user: User dict
            strategy_id: Strategy ID
            version: Version label recorded on the row (``strategy_backtests.version``)
            blueprint: Strategy blueprint
            dataset: Dataset used
            start_date: Backtest start date
            end_date: Backtest end date
            initial_capital: Initial capital
            commission: Commission rate
            slippage: Slippage rate
            version_id: The ``strategy_versions.id`` this backtest ran. Keyword-only in
                practice and last in the signature, so no existing positional caller
                changes shape.

        ``version_id`` (trading-lifecycle-integration task 6.1): ``BacktestRuntime.
        run_backtest`` has always called this method with ``version_id=…`` while the
        signature did not accept it, so every canonical backtest raised ``TypeError``
        before it reached the simulator. ``strategy_backtests.version_id`` is
        ``NOT NULL REFERENCES strategy_versions(id)`` in ``001_strategy_architecture.sql``,
        so the row could not have been written without it either. It is now accepted and
        persisted, which is what makes the backtest result traceable to the immutable
        version that produced it (Requirement 10.1). It is omitted from the payload when
        ``None`` so a caller that has no version to name gets the database's own NOT NULL
        complaint rather than an explicit ``NULL`` that hides which caller sent it.

        Returns:
            Backtest record
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        backtest_id = str(uuid4())
        
        backtest_data = {
            "id": backtest_id,
            "strategy_id": strategy_id,
            "user_id": user["id"],
            # ``strategy_backtests.version`` is VARCHAR(20) NOT NULL
            # (001_strategy_architecture.sql), and the caller's own label is what makes the
            # row say which version ran. This used to be a hardcoded ``1``, which discarded
            # the argument and recorded every backtest against "version 1".
            "version": version,
            "blueprint": blueprint,
            "dataset": dataset,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "commission": commission,
            "slippage": slippage,
            "status": "running",
            "created_at": datetime.now(timezone.utc).isoformat(),
            # Reproducibility tracking
            "engine_version": "1.0.0",
            "schema_version": blueprint.get("schema_version", "2.0"),
            # ``blueprint`` is ``ExecutionGraph.to_dict()`` when the canonical runtime calls
            # this (``BacktestRuntime.run_backtest``), and that document carries the graph's
            # identity hash on its ``metadata``, not at the top level - so this column, whose
            # only purpose is reproducibility tracking, was written NULL on every canonical
            # backtest. Both shapes are read now, in the order of specificity, so a caller
            # that really does put ``dag_hash`` at the top level still wins.
            # Found by the Requirement 26.4 end-to-end sandbox suite
            # (``tests/sandbox_lifecycle/``).
            "dag_hash": blueprint.get("dag_hash")
            or (blueprint.get("metadata") or {}).get("dag_hash"),
            "dataset_checksum": self._generate_dataset_checksum(dataset, start_date, end_date)
        }

        if version_id is not None:
            backtest_data["version_id"] = str(version_id)

        if sb is None:
            logger.info(f"[DEV_MODE] Skipping Supabase backtest insertion for {backtest_id}")
            return backtest_data
        
        query_res = sb.table("strategy_backtests").insert(backtest_data).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        logger.info(f"Created backtest {backtest_id} for strategy {strategy_id}")
        
        return result.data[0] if result.data else backtest_data
    
    async def update_backtest_results(
        self,
        user: dict,
        backtest_id: str,
        results: Dict,
        executed_bar_count: Optional[int] = None,
    ) -> Dict:
        """
        Update backtest with execution results.

        Args:
            user: User dict
            backtest_id: Backtest ID
            results: Backtest results dictionary
            executed_bar_count: How many OHLCV bars the simulation actually ran over.
                Keyword-only in practice and last in the signature, so no existing
                caller changes shape. ``None`` - the default, and what the HTTP
                ``PUT /api/backtests/{id}/results`` handler still sends - leaves the
                column untouched rather than writing an explicit ``NULL``, so a
                partial update cannot erase a count an earlier write recorded.

        ``executed_bar_count`` (task 12.1, Requirements 3.8, 3.4, 25.1): the number of
        bars a backtest ran over was computed and discarded everywhere it appeared -
        ``backtesting_engine.py`` line 189 tests ``len(price_data) < 50``,
        ``BacktestRuntime.run_backtest`` knows ``len(ohlcv_data)`` - so no row could
        satisfy Requirement 3.8's "non-null executed bar count of at least 50" and
        every marketplace submission would have failed its bar-count criterion on
        evidence that existed but was never written down. It is recorded here, next to
        the metrics it describes, in the same UPDATE. The 50-bar guard and the 20-trade
        significance warning in ``backtesting_engine.py`` are untouched: this method
        records the number, it does not re-judge it.

        The write degrades when ``006_backtest_evidence_columns.sql`` has not been
        applied - see this module's availability block. A results write must never be
        lost to a column that a hand-applied migration has not created yet.

        KEY MAPPING (task 7.8, Requirements 1.7, 1.8, 2.7, 2.8)
            Which key in ``results`` feeds which column is declared in
            :data:`RESULT_COLUMN_SOURCE_KEYS` and :data:`RESULT_JSON_COLUMN_SOURCE_KEYS`,
            including the four columns whose name differs from the engine key that
            produces them. Nothing here supplies a numeric default, so a metric the
            payload does not carry is written as SQL ``NULL`` rather than as a ``0`` that
            reads like a measurement.

        Returns:
            The updated backtest record, or ``{}`` when no row belonging to this user
            carries ``backtest_id``.

        OWNERSHIP (Requirement 20.1)
            The UPDATE is scoped by ``user_id`` as well as by ``id``. It used to filter on
            ``id`` alone, which made this both a cross-tenant WRITE - a non-owner's metrics
            landed on the owner's row - and an existence oracle, because the response
            echoed the row it had just overwritten. ``get_backtest``, ``list_backtests``,
            ``get_backtest_history``, ``compare_backtests`` and ``delete_backtest`` all
            already carried the ``user_id`` predicate; this method was the one that did
            not.

            A caller that owns no such row therefore matches nothing and gets ``{}`` -
            exactly what a ``backtest_id`` that names no row at all returns, so the two
            cases are indistinguishable from here up (Requirement 20.2). The HTTP layer
            turns that single empty answer into one 404 for both.
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        # Every metric column is read through the declared mapping above (task 7.8), so
        # the column ↔ producing-key correspondence lives in one reviewable table rather
        # than in eighteen string literals that happen - or, for four of them, happen not
        # - to match. No ``.get(key, 0)`` appears here: an absent key writes SQL NULL.
        update_data: Dict[str, Any] = {
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        for column, source_key in RESULT_COLUMN_SOURCE_KEYS.items():
            update_data[column] = results.get(source_key)
        for column, source_key in RESULT_JSON_COLUMN_SOURCE_KEYS.items():
            update_data[column] = results.get(source_key, [])

        bar_count = coerce_executed_bar_count(executed_bar_count)

        if sb is None:
            # DEV_MODE echoes what would have been written, including the bar count: there
            # is no database to probe, and a caller inspecting this payload is entitled to
            # see the value it supplied.
            if bar_count is not None:
                update_data[EXECUTED_BAR_COUNT_COLUMN] = bar_count
            logger.info(f"[DEV_MODE] Skipping Supabase backtest update for {backtest_id}")
            return update_data

        if bar_count is not None and await executed_bar_count_supported(sb):
            update_data[EXECUTED_BAR_COUNT_COLUMN] = bar_count

        async def _write(payload: Dict) -> Any:
            query_res = (sb.table("strategy_backtests")
                     .update(payload)
                     .eq("id", backtest_id)
                     .eq("user_id", user["id"])
                     .execute())
            return await query_res if inspect.isawaitable(query_res) else query_res

        try:
            result = await _write(update_data)
        except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
            if EXECUTED_BAR_COUNT_COLUMN not in update_data:
                raise
            if not is_missing_executed_bar_count_error(exc):
                raise
            # The probe said the column was there (or was inconclusive) and the write says
            # otherwise. The write is the newer information: remember it, and land the
            # metrics without the bar count rather than losing a finished run.
            remember_executed_bar_count_absent()
            warn_executed_bar_count_absent(str(exc))
            update_data.pop(EXECUTED_BAR_COUNT_COLUMN, None)
            result = await _write(update_data)
        else:
            # Clients that report errors on the response instead of raising.
            error = getattr(result, "error", None)
            if (
                error is not None
                and EXECUTED_BAR_COUNT_COLUMN in update_data
                and is_missing_executed_bar_count_error(Exception(str(error)))
            ):
                remember_executed_bar_count_absent()
                warn_executed_bar_count_absent(str(error))
                update_data.pop(EXECUTED_BAR_COUNT_COLUMN, None)
                result = await _write(update_data)

        if not result.data:
            # Nothing matched: either no such backtest, or it is not this user's. Logged
            # as one sentence on purpose - the two are the same event to this method, and
            # the caller is told the same thing about both.
            logger.info(
                "No backtest %s belonging to user %s; results not written",
                backtest_id,
                user["id"],
            )
            return {}

        logger.info(f"Updated backtest {backtest_id} with results")

        return result.data[0]
    
    async def get_backtest(
        self,
        user: dict,
        backtest_id: str
    ) -> Optional[Dict]:
        """
        Get complete backtest data.
        
        Args:
            user: User dict
            backtest_id: Backtest ID
            
        Returns:
            Backtest record with results
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if sb is None:
            return None
        
        query_res = sb.table("strategy_backtests").select("*").eq("id", backtest_id).eq("user_id", user["id"]).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        if not result.data:
            return None
        
        return result.data[0]
    
    async def list_backtests(
        self,
        user: dict,
        strategy_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict]:
        """
        List backtests for user or strategy.
        
        Args:
            user: User dict
            strategy_id: Optional strategy filter
            limit: Maximum number of results
            
        Returns:
            List of backtest records
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if sb is None:
            return []
        
        query = sb.table("strategy_backtests").select("*").eq("user_id", user["id"])
        
        if strategy_id:
            query = query.eq("strategy_id", strategy_id)
        
        query_res = query.order("created_at", desc=True).limit(limit).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        return result.data or []
    
    async def get_backtest_history(
        self,
        user: dict,
        strategy_id: str
    ) -> List[Dict]:
        """
        Get complete backtest history for a strategy.
        
        Every strategy owns complete backtest history.
        Store forever.
        
        Args:
            user: User dict
            strategy_id: Strategy ID
            
        Returns:
            All backtests for strategy
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        if sb is None:
            return []
        
        query_res = (sb.table("strategy_backtests")
                 .select("*")
                 .eq("strategy_id", strategy_id)
                 .eq("user_id", user["id"])
                 .order("created_at", desc=True)
                 .execute())
        result = await query_res if inspect.isawaitable(query_res) else query_res
        
        return result.data or []
    
    async def compare_backtests(
        self,
        user: dict,
        backtest_ids: List[str]
    ) -> Dict:
        """
        Compare multiple backtests.
        
        Args:
            user: User dict
            backtest_ids: List of backtest IDs to compare
            
        Returns:
            Comparison results
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res
        
        backtests = []
        if sb:
            for backtest_id in backtest_ids:
                query_res = sb.table("strategy_backtests").select("*").eq("id", backtest_id).eq("user_id", user["id"]).execute()
                result = await query_res if inspect.isawaitable(query_res) else query_res
                if result.data:
                    backtests.append(result.data[0])
        
        # Generate comparison
        comparison = {
            "backtest_ids": backtest_ids,
            "backtests": backtests,
            "comparison": {
                "total_return": {b["id"]: b.get("total_return_pct", 0) for b in backtests},
                "win_rate": {b["id"]: b.get("win_rate", 0) for b in backtests},
                "max_drawdown": {b["id"]: b.get("max_drawdown", 0) for b in backtests},
                "sharpe_ratio": {b["id"]: b.get("sharpe_ratio", 0) for b in backtests},
                "sortino_ratio": {b["id"]: b.get("sortino_ratio", 0) for b in backtests},
                "profit_factor": {b["id"]: b.get("profit_factor", 0) for b in backtests},
                "total_trades": {b["id"]: b.get("total_trades", 0) for b in backtests}
            }
        }
        
        return comparison
    
    async def delete_backtest(
        self,
        user: dict,
        backtest_id: str
    ) -> bool:
        """
        Delete a backtest.

        Args:
            user: User dict
            backtest_id: Backtest ID

        Returns:
            ``True`` when a row belonging to this user was actually deleted, ``False`` when
            the ownership-scoped DELETE matched nothing - because no such backtest exists,
            or because it is not this user's.

        WHY THIS RETURN VALUE AND THE HTTP RESPONSE DELIBERATELY DIFFER
            This method used to ``return True`` unconditionally, below the DELETE and
            without looking at it, so it reported success for a row it had not touched. The
            ownership predicate (``.eq("user_id", ...)``) was already here and correct; what
            was wrong was the report. It is truthful now, because an in-process caller
            asking "was it deleted?" is entitled to the answer, and a false ``True`` is how
            a "deleted" that deleted nothing reaches an audit log or a cache invalidation.

            The HTTP layer does NOT forward this value. ``DELETE /api/backtests/{id}``
            answers ``{"success": true}`` for every caller, and must keep doing so:
            Requirement 20.2 requires "not yours" and "not there" to be indistinguishable,
            and Requirement 20.1 is satisfied by the predicate on the statement, not by the
            wording of the reply. Truthfulness in-process and silence over the wire are two
            different obligations, and this is the one place they point in opposite
            directions - see the handler in ``routers/strategy_operations.py``.
        """
        sb_res = self._get_supabase(user)
        sb = await sb_res if inspect.isawaitable(sb_res) else sb_res

        if sb is None:
            logger.info(f"[DEV_MODE] Skipping Supabase backtest deletion for {backtest_id}")
            return False

        query_res = sb.table("strategy_backtests").delete().eq("id", backtest_id).eq("user_id", user["id"]).execute()
        result = await query_res if inspect.isawaitable(query_res) else query_res

        # PostgREST returns the deleted rows, so an empty ``data`` means the predicate -
        # ``id`` AND ``user_id`` - matched nothing.
        deleted = bool(getattr(result, "data", None))

        if deleted:
            logger.info(f"Deleted backtest {backtest_id}")
        else:
            logger.info(
                "No backtest %s belonging to user %s; nothing deleted",
                backtest_id,
                user["id"],
            )

        return deleted
    
    async def get_backtest_report(
        self,
        user: dict,
        backtest_id: str
    ) -> Dict:
        """
        Generate comprehensive backtest report.

        Args:
            user: User dict
            backtest_id: Backtest ID

        Returns:
            Complete backtest report with all metrics
        """
        backtest = await self.get_backtest(user, backtest_id)
        if not backtest:
            return {}
        
        report = {
            "backtest_id": backtest_id,
            "strategy_id": backtest["strategy_id"],
            "version": backtest["version"],
            "status": backtest["status"],
            "created_at": backtest["created_at"],
            "completed_at": backtest.get("completed_at"),
            
            # Parameters
            "parameters": {
                "dataset": backtest["dataset"],
                "start_date": backtest["start_date"],
                "end_date": backtest["end_date"],
                "initial_capital": backtest["initial_capital"],
                "commission": backtest["commission"],
                "slippage": backtest["slippage"]
            },
            
            # Performance Metrics
            "performance": {
                "total_return": backtest.get("total_return", 0),
                "total_return_pct": backtest.get("total_return_pct", 0),
                "final_capital": backtest.get("final_capital", 0),
                "win_rate": backtest.get("win_rate", 0),
                "max_drawdown": backtest.get("max_drawdown", 0),
                "sharpe_ratio": backtest.get("sharpe_ratio", 0),
                "sortino_ratio": backtest.get("sortino_ratio", 0),
                "profit_factor": backtest.get("profit_factor", 0)
            },
            
            # Trade Metrics
            "trades": {
                "total_trades": backtest.get("total_trades", 0),
                "winning_trades": backtest.get("winning_trades", 0),
                "losing_trades": backtest.get("losing_trades", 0)
            },
            
            # Execution
            "execution": {
                "execution_time_seconds": backtest.get("execution_time_seconds", 0)
            },
            
            # Data
            "equity_curve": backtest.get("equity_curve", []),
            "monthly_returns": backtest.get("monthly_returns", []),
            "daily_returns": backtest.get("daily_returns", [])
        }
        
        return report
    
    async def validate_historical_data(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str
    ) -> Dict:
        """
        Validate historical data availability and quality before backtest.
        
        Args:
            symbol: Trading pair (e.g., BTC/USDT)
            timeframe: Timeframe (e.g., 1h, 4h, 1d)
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
            
        Returns:
            Validation result with issues if any
        """
        from backend_app.backend.data_seeking_engine import DataEngine
        import pandas as pd
        from datetime import datetime
        
        validation_result = {
            "valid": True,
            "issues": [],
            "warnings": [],
            "data_info": {}
        }
        
        try:
            # Initialize data engine
            data_engine = DataEngine()
            
            # Fetch sample data to validate
            df = await data_engine.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date
            )
            
            if df is None or df.empty:
                validation_result["valid"] = False
                validation_result["issues"].append({
                    "code": "NO_DATA_AVAILABLE",
                    "message": f"No historical data available for {symbol} {timeframe} between {start_date} and {end_date}",
                    "severity": "critical"
                })
                return validation_result
            
            # Basic data quality checks
            validation_result["data_info"] = {
                "total_candles": len(df),
                "date_range": f"{df.index[0]} to {df.index[-1]}",
                "columns": list(df.columns)
            }
            
            # Check for missing candles (gaps)
            if len(df) > 1:
                time_diffs = df.index.to_series().diff()
                expected_diff = pd.Timedelta(minutes=self._timeframe_to_minutes(timeframe))
                gaps = time_diffs[time_diffs > expected_diff * 1.5]  # Allow some tolerance
                
                if len(gaps) > 0:
                    validation_result["warnings"].append({
                        "code": "DATA_GAPS_DETECTED",
                        "message": f"Found {len(gaps)} potential data gaps in the time series",
                        "severity": "warning",
                        "gap_count": len(gaps)
                    })
            
            # Check for duplicate timestamps
            duplicates = df.index.duplicated()
            if duplicates.any():
                validation_result["issues"].append({
                    "code": "DUPLICATE_TIMESTAMPS",
                    "message": f"Found {duplicates.sum()} duplicate timestamps in the data",
                    "severity": "error",
                    "duplicate_count": int(duplicates.sum())
                })
                validation_result["valid"] = False
            
            # Check for invalid OHLCV values
            invalid_values = 0
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in df.columns:
                    invalid_count = (df[col] <= 0).sum() if col != 'volume' else (df[col] < 0).sum()
                    if invalid_count > 0:
                        invalid_values += invalid_count
            
            if invalid_values > 0:
                validation_result["issues"].append({
                    "code": "INVALID_OHLCV_VALUES",
                    "message": f"Found {invalid_values} invalid OHLCV values (zero or negative)",
                    "severity": "error",
                    "invalid_count": invalid_values
                })
                validation_result["valid"] = False
            
            # Check timestamp ordering
            if not df.index.is_monotonic_increasing:
                validation_result["issues"].append({
                    "code": "TIMESTAMP_ORDERING",
                    "message": "Timestamps are not in chronological order",
                    "severity": "error"
                })
                validation_result["valid"] = False
            
            # Check minimum warmup period (at least 100 candles)
            if len(df) < 100:
                validation_result["issues"].append({
                    "code": "INSUFFICIENT_WARMUP",
                    "message": f"Insufficient data for warmup period (need at least 100 candles, got {len(df)})",
                    "severity": "error",
                    "candle_count": len(df)
                })
                validation_result["valid"] = False
            
            # Check high/low consistency
            if 'high' in df.columns and 'low' in df.columns:
                inconsistent = df[df['high'] < df['low']]
                if len(inconsistent) > 0:
                    validation_result["issues"].append({
                        "code": "OHLCV_CONSISTENCY",
                        "message": f"Found {len(inconsistent)} candles where high < low",
                        "severity": "error",
                        "inconsistent_count": len(inconsistent)
                    })
                    validation_result["valid"] = False
            
        except Exception as e:
            logger.error(f"Error validating historical data: {e}")
            validation_result["valid"] = False
            validation_result["issues"].append({
                "code": "VALIDATION_ERROR",
                "message": f"Error during data validation: {str(e)}",
                "severity": "critical"
            })
        
        return validation_result
    
    def _timeframe_to_minutes(self, timeframe: str) -> int:
        """Convert timeframe string to minutes."""
        timeframe_map = {
            '1m': 1,
            '5m': 5,
            '15m': 15,
            '30m': 30,
            '1h': 60,
            '4h': 240,
            '1d': 1440,
            '1w': 10080
        }
        return timeframe_map.get(timeframe, 60)


# Singleton instance
_backtest_service = None

async def get_backtest_service() -> BacktestService:
    """Get singleton BacktestService instance."""
    global _backtest_service
    if _backtest_service is None:
        _backtest_service = BacktestService()
    return _backtest_service