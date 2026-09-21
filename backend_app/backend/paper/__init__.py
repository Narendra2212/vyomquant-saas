"""Paper trading package.

The server-side paper trading domain: the order state machine, the accounting engine,
the deterministic simulator and the persistence layer that back ``/api/paper/*``.

Modules
-------
``paper_order_state.py``
    ``PaperOrderState`` (the six Paper_Order_State values), ``PAPER_ORDER_TRANSITIONS``
    (the nine permitted transitions of Requirement 16.2), ``TERMINAL``,
    ``can_transition`` and ``LEGACY_STATUS_FOR_STATE`` - the single mapping onto the
    retained ``PaperOrderStatus`` spellings.

Purity contract
---------------
The state-machine, accounting and validation modules in this package take values and
return values. No database handle, no HTTP client, no FastAPI import, no ``random``
draw. That is what makes them usable from the request path, the session worker and a
property test alike.

Following the convention of ``backend_app/backend/__init__.py``, no runtime imports are
performed here. Import directly from the submodules:

    from backend_app.backend.paper.paper_order_state import PaperOrderState

``COLUMN_CONTRACT`` (task 11.6)
-------------------------------
The paper half of the per-handler column manifest. Its purpose, shape and the two assertions
it enables are documented in ``backend_app/backend/marketplace/__init__.py::COLUMN_CONTRACT`` -
this mapping follows the identical structure so ``tests/test_marketplace_paper_schema_contract``
can iterate both with one code path.

Each entry names, for one paper-trading handler, every ``(table, column)`` pair it reads or
writes. Every table listed here is one of the eleven ``paper_*`` tables created by
``backend_app/migrations/009_paper_trading.sql`` (or the additive ``signals`` columns created
by ``010_signal_environment.sql``), sourced column-for-column from ``design.md`` -> "Data
Models".

``paper_repository`` (task 23.1) is the one module in this package that issues statements - the
accounting, state-machine and replay modules are pure - so it is the entry assertion 2 (the
``.select`` subset check) binds to. Its ``.select`` projections are declared as module-level
string constants (``ACCOUNT_SELECT``, ``POSITION_SELECT``, ``ORDER_SELECT``, ``TRADE_SELECT``,
``FILL_SELECT``, ``BALANCE_EVENT_SELECT``, ``EQUITY_SNAPSHOT_SELECT``, ``METRICS_SELECT``,
``SESSION_FEED_SELECT``, ``SESSION_CONFIG_SELECT``, ``EVENT_SELECT``, ``MARKET_EVENT_SELECT``,
``PROBE_SELECT``) precisely so
``tests/test_marketplace_paper_schema_contract._select_literals_in_module`` can resolve them,
and ``tests/test_marketplace_paper_schema_contract._MODULES_WITH_SELECTS`` maps that module onto
the ``paper_repository`` key below. Assertion 1 (the migration creates every named column)
remains the live guard for the other three entries.
"""

from typing import Dict, Mapping

__all__ = ["COLUMN_CONTRACT"]


# The order lifecycle - ``paper_orders`` and its ``paper_fills`` children (design.md ->
# "paper_orders", "paper_fills"). All created by 009.
_PAPER_ORDER_HANDLER = {
    "module": "backend_app.backend.paper.paper_order_state",
    "tables": {
        "paper_orders": frozenset(
            {
                "id",
                "session_id",
                "account_id",
                "user_id",
                "symbol",
                "side",
                "order_type",
                "quantity",
                "limit_price",
                "reference_price",
                "filled_quantity",
                "avg_fill_price",
                "fee_minor",
                "slippage_minor",
                "order_state",
                "legacy_status",
                "rejection_reason",
                "idempotency_key",
                "signal_id",
                "fingerprint",
            }
        ),
        "paper_fills": frozenset(
            {
                "id",
                "order_id",
                "session_id",
                "user_id",
                "fill_event_id",
                "quantity",
                "price",
                "fee_minor",
                "slippage_minor",
                "market_event_id",
                "filled_at",
            }
        ),
    },
    "preexisting": frozenset(),
}

# The accounting engine - the account, positions, balance events, trades, equity snapshots and
# metrics it reads and writes (design.md -> "paper_accounts", "paper_positions",
# "paper_balance_events", "paper_trades", "paper_equity_snapshots", "paper_metrics").
_PAPER_ACCOUNTING_HANDLER = {
    "module": "backend_app.backend.paper.paper_accounting",
    "tables": {
        "paper_accounts": frozenset(
            {
                "id",
                "user_id",
                "session_id",
                "currency",
                "initial_capital",
                "available_balance",
                "locked_balance",
                "realized_pnl",
                "total_equity",
                "version",
                "last_price_at",
                "stale",
            }
        ),
        "paper_positions": frozenset(
            {
                "id",
                "session_id",
                "account_id",
                "user_id",
                "symbol",
                "side",
                "size",
                "entry_price",
                "current_price",
                "unrealized_pnl",
                "price_at",
                "opened_at",
                "closed_at",
                "version",
            }
        ),
        "paper_balance_events": frozenset(
            {
                "session_id",
                "account_id",
                "user_id",
                "cause",
                "available_delta",
                "locked_delta",
                "realized_delta",
                "available_after",
                "locked_after",
                "realized_after",
                "fill_id",
                "occurred_at",
            }
        ),
        "paper_trades": frozenset(
            {
                "session_id",
                "account_id",
                "user_id",
                "symbol",
                "side",
                "quantity",
                "entry_price",
                "exit_price",
                "realized_pnl",
                "fee_minor",
                "opened_at",
                "closed_at",
            }
        ),
        "paper_equity_snapshots": frozenset(
            {
                "session_id",
                "user_id",
                "series_index",
                "total_equity",
                "available_balance",
                "locked_balance",
                "position_market_value",
                "stale",
                "cause",
                "taken_at",
            }
        ),
        "paper_metrics": frozenset(
            {
                "session_id",
                "user_id",
                "total_return_pct",
                "realized_pnl",
                "unrealized_pnl",
                "max_drawdown_amount",
                "max_drawdown_fraction",
                "win_rate",
                "closed_trade_count",
                "order_count",
                "fill_count",
                "computed_at",
            }
        ),
    },
    "preexisting": frozenset(),
}

# The session lifecycle and the event/market-event streams it owns (design.md ->
# "paper_sessions", "paper_events", "paper_market_events"), plus the additive ``signals``
# columns 010 adds for the paper execution environment.
_PAPER_SESSION_HANDLER = {
    "module": "backend_app.backend.paper.paper_replay",
    "tables": {
        "paper_sessions": frozenset(
            {
                "id",
                "user_id",
                "listing_id",
                "source_strategy_id",
                "version_id",
                "environment",
                "session_state",
                "exchange_id",
                "symbol",
                "timeframe",
                "initial_capital_minor",
                "currency",
                "config",
                "market_data_source",
                "feed_state",
                "feed_transport",
                "event_sequence",
                "started_at",
                "paused_at",
                "stopped_at",
            }
        ),
        "paper_events": frozenset(
            {
                "session_id",
                "user_id",
                "sequence",
                "event_id",
                "event_type",
                "schema_version",
                "payload",
                "emitted_at",
            }
        ),
        "paper_market_events": frozenset(
            {
                "session_id",
                "user_id",
                "sequence",
                "source_event_id",
                "symbol",
                "timeframe",
                "event_timestamp",
                "payload",
                "received_at",
                "latency_ms",
            }
        ),
        "signals": frozenset({"environment", "paper_session_id"}),
    },
    # ``signals`` is pre-existing; 010 only adds ``environment`` and ``paper_session_id``. The
    # whole table is marked pre-existing so the two additive columns are covered without the
    # migration set being expected to create ``signals`` itself.
    "preexisting": frozenset({"signals"}),
}


# The Paper_Repository (task 23.1) - the single module that reads and writes the paper_* tables.
# This entry is the union of every column its seven ``.select`` projections name and every column
# its insert and update payloads carry, across the eight accounting tables. It overlaps the three
# entries above by design: those describe what the pure modules' *values* correspond to, this one
# describes what one module's *statements* touch, and assertion 2 is bound to this one because
# this is the module that issues them.
#
# ``created_at`` and ``updated_at`` appear here and not above because the projections read them:
# the existing ``/api/paper/account``, ``/positions``, ``/orders`` and ``/trades`` response bodies
# carry both, so dropping them from the read would remove a field an existing consumer sees
# (Requirement 17.12).
#
# ``paper_sessions``, ``paper_events`` and ``paper_market_events`` were absent until task 24.x and
# are present now, because ``paper_market_feed`` reaches them through this module: the session's
# three feed columns (Requirements 14.3, 14.5, 14.6), the append-only market-data log that is the
# replay input of Requirement 15.5, and the one ``paper_error`` Paper_Channel record Requirement
# 14.5 requires on a dropped feed. They are listed with exactly the columns the new statements
# name - not the whole of ``paper_sessions``, because ``config`` is not read on the feed path.
_PAPER_REPOSITORY_HANDLER = {
    "module": "backend_app.backend.paper.paper_repository",
    "tables": {
        "paper_accounts": frozenset(
            {
                "id",
                "user_id",
                "session_id",
                "currency",
                "initial_capital",
                "available_balance",
                "locked_balance",
                "realized_pnl",
                "total_equity",
                "version",
                "last_price_at",
                "stale",
                "created_at",
                "updated_at",
            }
        ),
        "paper_positions": frozenset(
            {
                "id",
                "session_id",
                "account_id",
                "user_id",
                "symbol",
                "side",
                "size",
                "entry_price",
                "current_price",
                "unrealized_pnl",
                "price_at",
                "opened_at",
                "closed_at",
                "version",
                "created_at",
                "updated_at",
            }
        ),
        "paper_orders": frozenset(
            {
                "id",
                "session_id",
                "account_id",
                "user_id",
                "symbol",
                "side",
                "order_type",
                "quantity",
                "limit_price",
                "reference_price",
                "filled_quantity",
                "avg_fill_price",
                "fee_minor",
                "slippage_minor",
                "order_state",
                "legacy_status",
                "rejection_reason",
                "idempotency_key",
                "signal_id",
                "fingerprint",
                "created_at",
                "updated_at",
            }
        ),
        # Read and written. Task 23.1 wrote it only; task 23.2 reads it back through
        # ``FILL_SELECT``, because the ``self._trades[uid]`` list the existing paper service kept
        # in memory held one record per FILL - which is what ``GET /api/paper/trades``,
        # ``TradeHistory.jsx`` and ``dashboard_aggregation_service`` read - and ``design.md``'s
        # migration table maps that list onto ``paper_fills`` + ``paper_trades``. ``id``,
        # ``created_at`` and ``updated_at`` are here because the projection reads them.
        "paper_fills": frozenset(
            {
                "id",
                "created_at",
                "updated_at",
                "order_id",
                "session_id",
                "user_id",
                "fill_event_id",
                "quantity",
                "price",
                "fee_minor",
                "slippage_minor",
                "market_event_id",
                "filled_at",
            }
        ),
        # Append-only - ``paper_append_only_guard`` refuses every UPDATE - and read by task 23.2
        # through ``BALANCE_EVENT_SELECT``: ``realized_delta`` on a ``cause = 'FILL'`` event is the
        # only persisted home of a fill's realized PnL, which the retained
        # ``GET /api/paper/trades`` reports for every realizing fill including a partial close
        # (``paper_trades`` carries only closed round-trips, Requirement 18.10).
        "paper_balance_events": frozenset(
            {
                "id",
                "created_at",
                "updated_at",
                "session_id",
                "account_id",
                "user_id",
                "cause",
                "available_delta",
                "locked_delta",
                "realized_delta",
                "available_after",
                "locked_after",
                "realized_after",
                "fill_id",
                "occurred_at",
            }
        ),
        "paper_trades": frozenset(
            {
                "id",
                "session_id",
                "account_id",
                "user_id",
                "symbol",
                "side",
                "quantity",
                "entry_price",
                "exit_price",
                "realized_pnl",
                "fee_minor",
                "opened_at",
                "closed_at",
                "created_at",
                "updated_at",
            }
        ),
        "paper_equity_snapshots": frozenset(
            {
                "id",
                "session_id",
                "user_id",
                "series_index",
                "total_equity",
                "available_balance",
                "locked_balance",
                "position_market_value",
                "stale",
                "cause",
                "taken_at",
                "created_at",
                "updated_at",
            }
        ),
        # Read only. ``paper_metrics`` is computed and written by the metrics path of task 27.x;
        # this module serves the newest row per session.
        "paper_metrics": frozenset(
            {
                "id",
                "session_id",
                "user_id",
                "total_return_pct",
                "realized_pnl",
                "unrealized_pnl",
                "max_drawdown_amount",
                "max_drawdown_fraction",
                "win_rate",
                "closed_trade_count",
                "order_count",
                "fill_count",
                "computed_at",
                "created_at",
                "updated_at",
            }
        ),
        # Task 24.x. Read through ``SESSION_FEED_SELECT`` and written by
        # ``update_session_feed``, which touches exactly three columns - ``market_data_source``
        # (Requirement 14.3), ``feed_transport`` (14.6) and ``feed_state`` (14.5, 18.15) - plus
        # ``updated_at``.
        #
        # Task 25.2 adds ``config`` and ``currency``, read through the second, narrow
        # ``SESSION_CONFIG_SELECT`` projection: ``config`` is the frozen session configuration of
        # Requirement 16.12 and ``currency`` is what its ``minor_unit_exponent`` is an exponent
        # *of*. They are a separate projection rather than a widening of the feed one because the
        # feed transitions per candle and has no use for either.
        #
        # Task 27.1/27.3 adds the rest of the row, because the session INSERT
        # (``insert_session``), the concurrency-cap count (``count_running_sessions``) and the
        # guarded state UPDATE (``transition_session_state``) now touch them: ``listing_id``,
        # ``source_strategy_id``, ``version_id``, ``environment`` and ``initial_capital_minor``
        # travel in the INSERT payload, and ``started_at`` / ``paused_at`` / ``stopped_at`` are
        # written by the transition that earns each of them. The whole of ``paper_sessions`` is
        # therefore named here now - and it is named because statements touch it, not to be
        # complete: a column no statement touches would make this manifest a wish rather than a
        # record.
        "paper_sessions": frozenset(
            {
                "id",
                "user_id",
                "listing_id",
                "source_strategy_id",
                "version_id",
                "environment",
                "session_state",
                "exchange_id",
                "symbol",
                "timeframe",
                "initial_capital_minor",
                "currency",
                "config",
                "market_data_source",
                "feed_state",
                "feed_transport",
                "event_sequence",
                "started_at",
                "paused_at",
                "stopped_at",
                "created_at",
                "updated_at",
            }
        ),
        # Task 24.4. Written for the one ``paper_error`` record Requirement 14.5 names on a dropped
        # market-data connection, and read only to find the next sequence. The general
        # Paper_Channel writer is task 26.x and routes through the same function.
        "paper_events": frozenset(
            {
                "id",
                "session_id",
                "user_id",
                "sequence",
                "event_id",
                "event_type",
                "schema_version",
                "payload",
                "emitted_at",
                "created_at",
                "updated_at",
            }
        ),
        # Task 24.3. The append-only market-data log: every accepted event is written here with its
        # sequence, ``source_event_id`` and payload, and ``paper_replay`` reads them back in
        # ``sequence`` order. That read is what makes Requirement 15.5's "sufficient to reproduce
        # its order and accounting history" a fact about stored rows.
        "paper_market_events": frozenset(
            {
                "id",
                "session_id",
                "user_id",
                "sequence",
                "source_event_id",
                "symbol",
                "timeframe",
                "event_timestamp",
                "payload",
                "received_at",
                "latency_ms",
                "created_at",
                "updated_at",
            }
        ),
    },
    "preexisting": frozenset(),
}


#: The public paper manifest. Keyed by a short handler name; the test iterates it alongside the
#: marketplace manifest with one code path.
COLUMN_CONTRACT: Dict[str, Mapping[str, object]] = {
    "paper_order_lifecycle": _PAPER_ORDER_HANDLER,
    "paper_accounting": _PAPER_ACCOUNTING_HANDLER,
    "paper_session_lifecycle": _PAPER_SESSION_HANDLER,
    "paper_repository": _PAPER_REPOSITORY_HANDLER,
}
