"""
backend/paper/paper_repository.py - the one module that reads and writes the paper_* tables.

Spec: marketplace-subscriptions-paper-trading task 23.1. ``design.md`` -> "Paper trading:
persistence migration strategy". Requirements 17.1, 17.2, 17.11, 21.2, 21.5, 24.10, 28.3.

Every ``.table("paper_...")`` call in the backend lives here, so the storage decision has one
place to live: one set of explicit column projections, one migration probe, one optimistic
concurrency protocol, one tenant predicate. ``paper_trading_service`` (task 23.2),
``paper_simulator`` (task 25.x) and the six existing ``/api/paper/*`` endpoints reach the
Persistence_Layer through the functions below and through nothing else.

Exposes
-------
PAPER_TRADING_MIGRATION            the file an operator applies, for log lines
PAPER_TRADING_MIGRATION_FILE       the bare name that travels in an error's ``details``
PAPER_DEFAULT_ACCOUNT_MIGRATION    013, the file that lets the default account own child rows
PAPER_DEFAULT_ACCOUNT_MIGRATION_FILE   its bare name, for the same ``details`` slot
ACCOUNTS_TABLE ... METRICS_TABLE   the eight table names, spelled once each
ACCOUNT_SELECT ... METRICS_SELECT  the explicit projections; never ``select("*")``
PaperRepositoryError               base class for every refusal this module makes
PaperPersistenceError              a statement that DID NOT COMPLETE (never "no rows")
PaperConcurrencyConflict           an optimistic UPDATE that matched no row
PaperDuplicateFill                 ``uq_paper_fill_event`` already holds this fill
paper_persistence_supported(sb)    the cached migration probe
require_persistence(sb)            the probe, as a refusal: 503 PAPER_PERSISTENCE_UNAVAILABLE
remember_persistence_absent()      record a verdict a statement proved after the probe
get_or_create_account(...)         the default account (``session_id IS NULL``) or a session's
get_positions / get_orders / get_trades / get_fills / get_balance_events
get_equity_snapshots / get_metrics
lock_account_for_update(...)       the account row plus the ``version`` to write against
bump_version(...)                  the guarded UPDATE that is the other half of that lock
insert_order / update_order / probe_idempotency_key / insert_fill / upsert_position
insert_balance_event / insert_trade / insert_equity_snapshot
insert_metrics(...)                  the ``paper_metrics`` write (task 27.4, Requirement 17.8);
                                     every figure NULLABLE, so an absent one is absent not zero
read_session / update_session_feed   the Paper_Session's feed columns (Requirements 14.3, 14.5, 14.6)
insert_session(...)                  the session INSERT at ``CREATED`` (task 27.1, Requirement 17.9)
count_running_sessions(...)          the per-user concurrency cap, one round trip (Requirement 27.4)
transition_session_state(...)        the guarded ``session_state`` UPDATE (task 27.3, Req 17.7)
SESSION_STATES                       ``chk_paper_session_state`` verbatim - the four of Req 17.7
read_session_config(...)             the frozen ``paper_sessions.config`` (Requirement 16.12)
session_config_payload(config)       that column's ONE write path - and there is no update path
read_session_lifecycle(...)          the stop/reset projection: the recorded initial capital in
                                     Minor_Units, its currency and the frozen config, in ONE read
                                     (task 27.4, Requirements 17.8, 17.15)
list_sessions / read_session_summary the CLIENT projection - what ``GET /api/paper/sessions`` and
                                     ``GET /api/paper/sessions/{id}`` serve, caller-scoped IN the
                                     statement (task 28.1, Requirements 17.3, 21.5)
insert_market_event / get_market_events / next_market_event_sequence
                                     the append-only market-data log, replay input of Req 15.5
insert_session_event / next_session_event_sequence
                                     one append-only Paper_Channel record (task 24.4's paper_error)
read_session_events                  the Paper_Channel REPLAY read, capped at
                                     SESSION_EVENT_REPLAY_CAP rows (task 26.4, Requirement 19.8)
read_session_owner                   the pre-emit ownership oracle (task 26.5, Requirement 21.7)
on_session_write / clear_session_write_listeners
                                     the paper_sessions write notification the 5-second owner
                                     cache is invalidated from
PaperDuplicateMarketEvent            ``uq_paper_market_event`` already holds this event identity
PaperDuplicateSessionEvent           ``uq_paper_event_id`` / ``uq_paper_event_seq`` already holds it

THE DEFAULT ACCOUNT
-------------------
``paper_accounts`` where ``session_id IS NULL``. That is the account
``GET /api/paper/account``, ``/positions``, ``/orders``, ``/trades``, ``/summary`` and
``POST /api/paper/account/reset`` serve, and it is what ``routers/risk.py``,
``Portfolio.jsx`` and ``TradeHistory.jsx`` read today. ``uq_paper_account_default`` - the
PARTIAL unique index on ``(user_id, currency) WHERE session_id IS NULL`` - is what keeps it
single, and :func:`get_or_create_account` is written so that a concurrent create loses to it
and then reads the winner rather than raising: the uniqueness is the database's, not this
module's optimism about ordering. A session-scoped account carries a non-null ``session_id``
and is kept single per currency by ``uq_paper_account_session``.

**Its child rows carry ``session_id IS NULL`` too.** 009 declared ``session_id NOT NULL`` on
``paper_orders``, ``paper_fills``, ``paper_positions``, ``paper_balance_events``,
``paper_trades``, ``paper_equity_snapshots`` and ``paper_metrics``, which left the default
account with nowhere to persist an order, a fill, a position, a trade or an equity point - a
child row needed a ``paper_sessions`` row, and ``paper_sessions`` requires nine NOT NULL columns
an account that is not running a strategy has no honest value for.
``013_paper_default_account_children.sql`` relaxes those seven columns and reconciles what had
rested on them: ``uq_paper_order_idem_default`` (because SQL treats two NULL ``session_id``
values as distinct, so ``uq_paper_order_idem`` alone would silently stop de-duplicating) and
``paper_append_only_guard()`` (whose cascade exemption resolved no parent for a NULL session and
would have let default-account history be erased). ``session_id`` is consequently OPTIONAL on
every read and write here, ``None`` meaning the default account. A synthetic "default session"
row was rejected: its nine placeholder values would be the fabricated data Requirement 28.3
forbids, and ``paper_sessions`` would come to mean two different things. That is a deliberate,
documented divergence from Requirement 17.11 read as an unconditional NOT NULL, resolved in
favour of Requirement 17.12's retained endpoints; the ``user_id`` half of 17.11 is untouched and
still NOT NULL, still the RLS predicate, and still a predicate on every statement below.

REFUSAL, NOT DEGRADATION (Requirements 17.2, 28.3)
--------------------------------------------------
Every entry point calls :func:`require_persistence` first. If ``paper_accounts`` does not
exist, it raises the 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` that names
``009_paper_trading.sql``, and there is no second branch. This module holds no dictionary, no
cache of balances and no default figure to serve in place of a row: a balance served from
process memory after this change is exactly the fabricated figure Requirement 28.3 forbids,
and Requirement 17.2's survive-a-restart guarantee would read as true while being false. That
is a deliberate departure from the degrade-with-a-warning convention
``strategy_service.canonical_columns_supported`` and
``signal_service.signal_lifecycle_columns_supported`` establish for their *columns*: a missing
column there costs an audit field, whereas a missing table here costs the balance itself.

A READ THAT DID NOT COMPLETE IS NOT A READ THAT FOUND NOTHING
------------------------------------------------------------
The reading ``settlement_service`` establishes, applied here. ``None`` and ``[]`` are answers,
returned only when the statement completed and the result set was empty. A driver exception, or
a PostgREST response carrying an ``error`` envelope, raises :class:`PaperPersistenceError`.
Answering "no positions" for a broken read would show a trader a flat book they do not have,
and answering "no such order" would let a second order be placed for an idempotency key that
already has one.

WHAT SUBSTITUTES FOR ``SELECT ... FOR UPDATE``
---------------------------------------------
There is none in PostgREST: it speaks HTTP, one statement per request, so a row lock could not
outlive the round trip that took it. ``paper_accounts.version`` and ``paper_positions.version``
carry the optimistic protocol instead, the same one
``settlement_service._apply_transition`` uses on ``library_subscriptions.status``:

  1. :func:`lock_account_for_update` reads the row and returns it, ``version`` included.
  2. The caller computes the new balances from THAT image.
  3. :func:`bump_version` issues ``UPDATE ... WHERE id = :id AND user_id = :uid AND
     version = :read_version``, setting ``version = read_version + 1``.
  4. A concurrent writer that already moved the row makes step 3 match **zero** rows, which
     raises :class:`PaperConcurrencyConflict` rather than being read as success.

So a lost update surfaces as a conflict the caller retries (task 25.6's three attempts, then
409 ``PAPER_CONCURRENCY_CONFLICT``), which is the outcome the ``FOR UPDATE`` in ``design.md``
was there to produce. What is genuinely NOT reproduced is multi-statement atomicity: PostgREST
gives no transaction spanning several requests, so "the fill, the position, the balance event
and the equity snapshot commit together" (Requirement 24.6) holds only per statement here. The
guards that make a partial sequence detectable rather than silent are the database's own -
``chk_paper_balances_non_negative``, ``chk_paper_order_fill_bound``, ``uq_paper_fill_event``,
``uq_paper_position_open`` - and the version predicate above. This is recorded as a limitation
of the transport, not as a claim that it is equivalent.

EVERY READ AND WRITE IS SCOPED BY ``user_id`` AS A PREDICATE (Requirements 21.2, 21.5)
-------------------------------------------------------------------------------------
``user_id`` appears in the ``WHERE`` clause of every SELECT and every UPDATE below, and in the
payload of every INSERT. Another user's account is therefore never *fetched*, not merely never
returned: a handler bug, an exception log or a guessed identifier cannot surface a row the
statement did not ask for. Row-level security is the second guard, not the only one, and the
caller passes the identity it resolved from the authenticated server-side session - this module
performs no authorisation of its own and is not a place to add one.

MONEY AND QUANTITY
------------------
``NUMERIC(28,10)`` columns are written as decimal **strings** and read back as whatever the
driver returns, unconverted. :func:`_numeric` routes every such value through
``paper_accounting.to_decimal``, which refuses a ``float`` outright (Requirement 18.1) - a
binary float has already lost exactness by the time it reaches storage, and ``str(0.07)``
would persist a value the caller never had. The ``*_minor`` columns are ``BIGINT``
Minor_Units and go through :func:`_minor_units`, which admits only exact integers: a fee is a
whole number of minor units or it is not a fee.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* Arithmetic. ``paper_accounting`` owns every balance, equity, PnL, drawdown and win-rate
  computation, and this module never recomputes one to store it. ``total_equity`` is written
  from the value the caller was handed, so the zero-tolerance identity of Requirement 18.3
  stays a property of one implementation.
* The order state machine. ``paper_order_state`` owns the six values, the nine transitions and
  ``LEGACY_STATUS_FOR_STATE``; :func:`insert_order` derives ``legacy_status`` from that mapping
  rather than accepting it, so ``paper_orders.order_state`` and ``paper_orders.legacy_status``
  cannot disagree and ``GET /api/paper/orders?status=OPEN`` keeps its exact meaning.
* Transition *validation* on an update, the fill model, the fee and slippage rates, the
  idempotency-fingerprint comparison, and every HTTP status other than the one 503 above.
  Those belong to ``paper_simulator`` (tasks 25.3-25.6), which owns the retry loop.
* The session lifecycle itself. Task 24.x added the three SESSION-scoped tables to this module -
  ``paper_sessions``' three feed columns, ``paper_market_events`` and one ``paper_events`` insert -
  because ``paper_market_feed`` needs them and because a second module issuing statements against
  ``paper_*`` would be a second set of assumptions about the schema. Task 25.2 added the
  ``config`` READ (:func:`read_session_config`) and that column's one serialiser
  (:func:`session_config_payload`) for the same reason. Task 27.1/27.3 added the session INSERT
  (:func:`insert_session`), the concurrency-cap count (:func:`count_running_sessions`) and the
  guarded state UPDATE (:func:`transition_session_state`) on the same principle. What is still NOT
  here is the session STATE MACHINE ITSELF - which operation is permitted from which state, and
  what a refusal tells the caller: that is ``paper_session_service``'s, exactly as
  ``paper_order_state`` owns the order machine. This module owns the column VOCABULARY
  (:data:`SESSION_STATES`, ``chk_paper_session_state`` verbatim) and nothing above it. There is
  deliberately no ``update_session_config``: ``config`` is written once, in the session INSERT, and
  ``trg_paper_session_config_immutable`` refuses any UPDATE that changes it (Requirement 16.12).
* ``asyncio``. Every function here is synchronous and issues its statements through the
  synchronous ``supabase-py`` client, because ``routers/risk.py`` calls
  ``get_performance_summary``, ``get_positions`` and ``get_or_create_account`` synchronously
  from inside ``async def`` handlers (``design.md``: "That keeps ``risk.py`` unchanged"). The
  async write paths call these functions directly, exactly as ``settlement_service.settle``
  calls its own synchronous statement helpers.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.metrics import guarded_collector
from backend_app.backend.paper.errors import paper_persistence_unavailable
from backend_app.backend.paper.paper_accounting import to_decimal
from backend_app.backend.paper.paper_events import PAPER_EVENT_TYPES
from backend_app.backend.paper.paper_order_state import (
    LEGACY_STATUS_FOR_STATE,
    PaperOrderState,
    can_transition,
)

logger = logging.getLogger("PaperRepository")


# ══════════════════════════════════════════════════════════════════════════
# THE MIGRATION, AND THE EIGHT TABLES
# ══════════════════════════════════════════════════════════════════════════

#: The file an operator applies. Used in log lines, where a repository path is useful.
PAPER_TRADING_MIGRATION = "backend_app/migrations/009_paper_trading.sql"

#: The additive migration that lets the DEFAULT ACCOUNT own child rows, by relaxing
#: ``session_id`` to NULLABLE on the seven accounting child tables and reconciling the guarantees
#: that had rested on it (the companion ``uq_paper_order_idem_default`` and the append-only
#: guard's parent resolution). A database carrying 009 but not this file accepts every
#: session-scoped write and refuses every DEFAULT-ACCOUNT write with ``23502
#: not_null_violation`` on ``session_id`` - so :func:`_execute` classifies that one SQLSTATE and
#: answers the 503 naming THIS file rather than letting a constraint violation reach a trader as
#: an unexplained failure. Applied BY HAND, like 009.
PAPER_DEFAULT_ACCOUNT_MIGRATION = (
    "backend_app/migrations/013_paper_default_account_children.sql"
)

#: The bare name of that file, for ``details["migration"]``. Bare for the reason
#: :data:`PAPER_TRADING_MIGRATION_FILE` is bare: ``FORBIDDEN_BODY_SUBSTRINGS`` lists
#: ``"backend_app"``, so a path would be redacted to nothing.
PAPER_DEFAULT_ACCOUNT_MIGRATION_FILE = "013_paper_default_account_children.sql"

#: The bare file name, and the value that travels in ``details["migration"]``.
#:
#: It is the basename and not :data:`PAPER_TRADING_MIGRATION` because
#: ``marketplace.errors.FORBIDDEN_BODY_SUBSTRINGS`` lists ``"backend_app"``: the full path would
#: be replaced by the redaction placeholder, and the one refusal that exists to tell an operator
#: what to apply would name nothing. ``errors.paper_persistence_unavailable``'s docstring asks
#: for the bare name for exactly this reason.
PAPER_TRADING_MIGRATION_FILE = "009_paper_trading.sql"

#: The eight tables this module touches, each spelled once. A literal repeated across statements
#: is how a typo becomes a PostgREST ``PGRST205`` on a balance read.
ACCOUNTS_TABLE = "paper_accounts"
ORDERS_TABLE = "paper_orders"
FILLS_TABLE = "paper_fills"
POSITIONS_TABLE = "paper_positions"
BALANCE_EVENTS_TABLE = "paper_balance_events"
TRADES_TABLE = "paper_trades"
EQUITY_SNAPSHOTS_TABLE = "paper_equity_snapshots"
METRICS_TABLE = "paper_metrics"

#: The three SESSION-scoped tables, added by task 24.x for the market feed. They are NOT part of
#: the "eight accounting tables" above and are listed separately for one reason: unlike those
#: eight, none of them has a DEFAULT-ACCOUNT form. 009 declares ``paper_events.session_id`` and
#: ``paper_market_events.session_id`` NOT NULL, and 013 deliberately did not relax them - they are
#: the Paper_Channel and market-data logs OF a Paper_Session and have no meaning without one. So
#: every function below requires a ``session_id``, and there is no ``session_id IS NULL`` branch to
#: get wrong.
SESSIONS_TABLE = "paper_sessions"
EVENTS_TABLE = "paper_events"
MARKET_EVENTS_TABLE = "paper_market_events"

#: ``paper_balance_events.cause`` - ``chk_paper_balance_event_cause``, verbatim.
BALANCE_EVENT_CAUSES: Tuple[str, ...] = (
    "ORDER_LOCK",
    "ORDER_UNLOCK",
    "FILL",
    "FEE",
    "RESET",
)

#: ``paper_equity_snapshots.cause`` - ``chk_paper_equity_cause``, verbatim. These are also the
#: five points Requirement 18.11 requires a snapshot at.
EQUITY_SNAPSHOT_CAUSES: Tuple[str, ...] = (
    "SESSION_START",
    "FILL",
    "FEE",
    "REVALUATION",
    "SESSION_STOP",
)

#: ``paper_events.event_type`` - ``chk_paper_event_type``, verbatim and in 009's order. The sixteen
#: Paper_Channel event types of ``design.md`` -> "paper/paper_events.py and the Paper_Channel".
#:
#: **Task 26.1 moved the definition, not the meaning.** This tuple used to be spelled out here
#: because :func:`insert_session_event` owns the column. It is now the tuple
#: ``paper_events.PAPER_EVENT_TYPES`` derives from :class:`~paper_events.PaperEvent` - the SAME
#: OBJECT, re-exported under the name every caller already used, so nothing that imported it from
#: here breaks and there is exactly one list of sixteen values in the codebase. The values and their
#: order are unchanged: the audit that accompanied 26.1 found this tuple and both spellings of
#: ``chk_paper_event_type`` already in agreement.
#:
#: ``paper_events`` imports NOTHING from this module at module scope (it reaches
#: :func:`allocate_session_event_sequence` and :func:`insert_session_event` through function-local
#: imports), so this import direction carries no cycle. The CHECK constraint remains the guarantee.
#:
#: The import itself is at the top of the module with the others; this comment stays where the tuple
#: was so a reader looking for the vocabulary is told where it went and why.
_PAPER_EVENT_TYPES_DEFINED_IN = "backend_app.backend.paper.paper_events"

#: ``paper_positions.side`` - ``chk_paper_position_side``. Direction is an explicit side value,
#: never a negative size (Requirement 18.5).
POSITION_SIDES: Tuple[str, ...] = ("LONG", "SHORT")

#: ``paper_orders.side`` and ``paper_orders.order_type`` - ``chk_paper_order_side`` and
#: ``chk_paper_order_type``, verbatim and lower-case as the constraints spell them.
ORDER_SIDES: Tuple[str, ...] = ("buy", "sell")
ORDER_TYPES: Tuple[str, ...] = ("market", "limit")

#: ``chk_paper_order_idem_len``: 1 to 128 characters. Validated here as well as by the database
#: because this module owns the column; ``paper_simulator.submit_intent`` validates earlier still
#: so the caller-facing error is its own rather than a constraint violation (task 25.3).
IDEMPOTENCY_KEY_MAX_CHARS = 128

#: The default currency of the account the six existing endpoints serve. ``paper_accounts`` has
#: no default for the column, and the existing in-memory account dict carries ``"USD"``.
DEFAULT_CURRENCY = "USD"

#: The starting capital used when a caller does not name one. The same figure
#: ``PaperTradingService.__init__``'s ``default_capital`` parameter defaults to today, so
#: repointing that service at this module (task 23.2) does not change what a new account opens
#: with. It is a configured default, not a measurement: a caller that knows the capital passes
#: it, and task 23.2 passes its own ``self.default_capital``.
DEFAULT_INITIAL_CAPITAL = Decimal("100000")

#: How long a *negative* probe verdict is trusted before it is re-taken. 009 is applied BY HAND,
#: so a process that started before the operator applied it recovers on its own inside this
#: window instead of needing a restart. A *positive* verdict is cached for the life of the
#: process, which is the "once per process" of the task. Same convention, and the same window, as
#: ``signal_service.SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS``.
PAPER_PERSISTENCE_RECHECK_SECONDS = 300.0

#: PostgreSQL's ``undefined_table`` and PostgREST's schema-cache equivalent. A missing *column*
#: code is deliberately absent: 009 creates every one of these tables whole, so a table that
#: exists with a column missing is a reconciled-by-hand database, and the shape assertions inside
#: 009 are what report that - not a silent degrade here.
_MISSING_TABLE_CODES: Tuple[str, ...] = ("42p01", "pgrst205", "undefined_table")

#: The phrases a driver or PostgREST uses alongside those codes.
_MISSING_TABLE_PHRASES: Tuple[str, ...] = (
    "does not exist",
    "schema cache",
    "could not find the table",
    "unknown table",
)

#: What a driver says for ``23505``. Matched on the SQLSTATE or on the constraint name, because
#: which of the two a driver surfaces depends on the driver.
_UNIQUE_VIOLATION_CODES: Tuple[str, ...] = ("23505", "unique_violation", "duplicate key")

#: PostgreSQL's ``not_null_violation`` and PostgREST's spelling of it.
_NOT_NULL_VIOLATION_CODES: Tuple[str, ...] = ("23502", "not_null_violation", "null value in column")

#: PostgreSQL's transaction-rollback class 40: ``serialization_failure`` (``40001``) and
#: ``deadlock_detected`` (``40P01``). Both mean the same thing to a caller - "your statement was
#: aborted because another transaction got there first; run it again" - which is exactly the
#: condition Requirement 16.10 retries. ``40003`` (``statement_completion_unknown``) is
#: deliberately ABSENT: a statement whose completion is unknown must not be retried, because a
#: retry could apply a money movement twice.
_SERIALIZATION_FAILURE_CODES: Tuple[str, ...] = (
    "40001",
    "40p01",
    "serialization_failure",
    "deadlock_detected",
    "could not serialize access",
)


# ══════════════════════════════════════════════════════════════════════════
# THE EXPLICIT PROJECTIONS (Requirement 24.9, 24.10)
# ══════════════════════════════════════════════════════════════════════════
#
# Every column is named. No ``select("*")``: a projection that names its columns is the thing
# ``tests/test_marketplace_paper_schema_contract.py`` can hold against the migration-defined
# schema, and it is what turns a mistyped column into a red suite rather than a PostgreSQL 42703
# on a balance read. Each constant is a module-level string so that test's AST walk resolves it
# (it reads ``.select(NAME)`` by looking the name up among module-level string assignments), and
# every column below is registered in ``backend_app/backend/paper/__init__.py::COLUMN_CONTRACT``
# under the ``paper_repository`` entry.

#: ``paper_accounts``, whole. ``version`` is the optimistic guard; ``last_price_at`` and
#: ``stale`` are what Requirement 18.15 reports alongside a price-derived figure;
#: ``created_at``/``updated_at`` are in the shape the existing ``/api/paper/account`` returns.
ACCOUNT_SELECT = (
    "id,user_id,session_id,currency,initial_capital,available_balance,locked_balance,"
    "realized_pnl,total_equity,version,last_price_at,stale,created_at,updated_at"
)

#: ``paper_positions``, whole. ``closed_at`` distinguishes a closed position - which persists at
#: ``size = 0`` rather than being deleted (Requirement 18.5) - from an open one.
POSITION_SELECT = (
    "id,session_id,account_id,user_id,symbol,side,size,entry_price,current_price,"
    "unrealized_pnl,price_at,opened_at,closed_at,version,created_at,updated_at"
)

#: ``paper_orders``, whole. ``legacy_status`` is what ``?status=OPEN`` filters on;
#: ``fingerprint`` and ``idempotency_key`` are what task 25.3's probe compares.
ORDER_SELECT = (
    "id,session_id,account_id,user_id,symbol,side,order_type,quantity,limit_price,"
    "reference_price,filled_quantity,avg_fill_price,fee_minor,slippage_minor,order_state,"
    "legacy_status,rejection_reason,idempotency_key,signal_id,fingerprint,created_at,updated_at"
)

#: ``paper_fills``, whole. Added by task 23.2, which needs the fills back out again: the
#: ``self._trades[uid]`` list the existing service kept in memory held one record **per fill**
#: (``_execute_fill`` appended one every time), and that list is what ``GET /api/paper/trades``,
#: ``TradeHistory.jsx`` and ``dashboard_aggregation_service``'s today-PnL sum read. So the fill
#: history is a READ of this table and not only a write, and ``design.md``'s migration table says
#: exactly that: ``self._trades[uid]`` -> ``paper_fills`` (every fill) + ``paper_trades`` (closed
#: round-trips). ``paper_fills`` carries no ``account_id`` - it reaches the account through its
#: order - so a default-account read is scoped by ``user_id`` and ``session_id IS NULL``.
FILL_SELECT = (
    "id,order_id,session_id,user_id,fill_event_id,quantity,price,fee_minor,slippage_minor,"
    "market_event_id,filled_at,created_at,updated_at"
)

#: ``paper_balance_events``, whole. Added by task 23.2 for one reason: ``realized_delta`` is the
#: **per-fill** realized PnL, and it is the only place that figure is persisted.
#: ``paper_trades.realized_pnl`` is per closed round-trip (Requirement 18.10), so a PARTIAL close
#: has no row there while it does move ``realized_pnl`` on the account - and the existing
#: ``/api/paper/trades`` reports the realized PnL of every realizing fill, partial ones included.
#: Reading it from the ledger keeps that figure a persisted measurement instead of the zero a
#: trades-only join would have to report (Requirement 28.3).
BALANCE_EVENT_SELECT = (
    "id,session_id,account_id,user_id,cause,available_delta,locked_delta,realized_delta,"
    "available_after,locked_after,realized_after,fill_id,occurred_at,created_at,updated_at"
)

#: ``paper_trades``, whole - the closed round-trips ``GET /api/paper/trades`` serves.
TRADE_SELECT = (
    "id,session_id,account_id,user_id,symbol,side,quantity,entry_price,exit_price,"
    "realized_pnl,fee_minor,opened_at,closed_at,created_at,updated_at"
)

#: ``paper_equity_snapshots``, whole - the series Requirement 18.9's drawdown is computed from
#: and Requirement 18.11's equity curve is drawn from.
EQUITY_SNAPSHOT_SELECT = (
    "id,session_id,user_id,series_index,total_equity,available_balance,locked_balance,"
    "position_market_value,stale,cause,taken_at,created_at,updated_at"
)

#: ``paper_metrics``, whole.
METRICS_SELECT = (
    "id,session_id,user_id,total_return_pct,realized_pnl,unrealized_pnl,"
    "max_drawdown_amount,max_drawdown_fraction,win_rate,closed_trade_count,order_count,"
    "fill_count,computed_at,created_at,updated_at"
)

#: ``paper_sessions``, the feed-bearing projection. Not the whole row: the columns the market feed
#: and the simulator's feed gate read, plus the identity ones that scope them.
#: ``market_data_source`` is Requirement 14.3's recorded source identity; ``feed_state`` and
#: ``feed_transport`` are Requirements 14.5, 14.6 and 18.15's health record; ``session_state`` is
#: read so a caller can assert the session stayed ``CREATED`` when :func:`open_feed` refused
#: (Requirement 14.4). ``event_sequence`` is 009's per-session Paper_Channel counter.
SESSION_FEED_SELECT = (
    "id,user_id,session_state,exchange_id,symbol,timeframe,market_data_source,"
    "feed_state,feed_transport,event_sequence,created_at,updated_at"
)

#: ``paper_sessions``, the CONFIG-bearing projection (task 25.2). Deliberately a second, narrow
#: projection rather than a widening of :data:`SESSION_FEED_SELECT`: the feed path transitions on
#: every accepted candle and has no use for the frozen configuration, so carrying that payload
#: there would put it on a per-event statement. ``currency`` travels with ``config`` because
#: ``config["minor_unit_exponent"]`` has no meaning apart from the account's currency, and
#: ``session_state`` because a caller reading the configuration is about to act on the session and
#: is entitled to see whether it is still ``RUNNING`` (Requirement 17.7).
SESSION_CONFIG_SELECT = (
    "id,user_id,session_state,symbol,currency,config,created_at,updated_at"
)

#: ``paper_sessions``, the LIFECYCLE projection (task 27.4). A third narrow projection, for the
#: same reason :data:`SESSION_CONFIG_SELECT` is a second one: the stop and reset paths need three
#: things none of the other two carries together - the RECORDED initial simulated capital in
#: Minor_Units (Requirement 17.15's "the recorded initial simulated capital", which is the exact
#: integer the balances return to), the currency that integer is denominated in, and the frozen
#: ``config`` whose rounding mode and precisions the closing figures are computed under.
#:
#: ``session_state`` travels because the caller is about to gate on it, and ``started_at`` /
#: ``stopped_at`` because a stop reports when the session ran. It does NOT carry the three feed
#: columns: the stop path releases the subscription through the handle it was given, not by reading
#: a transport out of a row.
SESSION_LIFECYCLE_SELECT = (
    "id,user_id,session_state,symbol,timeframe,exchange_id,currency,"
    "initial_capital_minor,config,started_at,stopped_at,created_at,updated_at"
)

#: ``paper_sessions``, the LIST projection (task 28.1). What ``GET /api/paper/sessions`` and
#: ``GET /api/paper/sessions/{id}`` serve, and a fourth narrow projection for the same reason the
#: other three are narrow: it is the only one a CLIENT reads, so what it omits matters as much as
#: what it carries.
#:
#: It carries :data:`SESSION_FEED_SELECT`'s columns - Requirement 17.3's ``feed_state``,
#: ``market_data_source`` and ``event_sequence`` among them, which task 28.1 names explicitly - plus
#: the recorded capital, its currency, the Listing the caller themselves named, and the three
#: lifecycle timestamps a client needs to render when the session ran.
#:
#: It deliberately omits ``config``, ``version_id`` and ``source_strategy_id``. ``config`` is the
#: frozen session configuration and has no place in a list response; the other two are values the
#: ENTITLEMENT DECISION produced server-side (Requirement 21.1) and are internal identifiers a
#: response must not return (Requirement 22.9). ``environment`` is omitted because
#: ``chk_paper_session_environment`` pins it to ``'PAPER'`` - a column with one possible value is
#: not information, and the route states the environment on the envelope instead.
SESSION_LIST_SELECT = (
    "id,user_id,session_state,listing_id,exchange_id,symbol,timeframe,currency,"
    "initial_capital_minor,market_data_source,feed_state,feed_transport,event_sequence,"
    "started_at,paused_at,stopped_at,created_at,updated_at"
)

#: How many sessions one list read returns at most, and the ceiling a caller's ``limit`` is clamped
#: to. Requirement 27.4 caps CONCURRENT sessions at 20, but a user's HISTORY is unbounded - every
#: stopped session stays readable (Requirement 17.8) - so the read needs a ceiling of its own.
SESSION_LIST_CAP = 200

#: ``paper_sessions``, the OWNERSHIP projection (task 26.5). Two columns, and they are the only two
#: :func:`read_session_owner` is allowed to see: it answers Requirement 21.7's "re-derive the owning
#: user of the subscribed Paper_Session before emitting each event", and a pre-emit check that read
#: the symbol, the state or the configuration would be carrying session data through a path whose
#: entire output is one identity comparison.
SESSION_OWNER_SELECT = "id,user_id"

#: ``paper_events``, whole - the append-only Paper_Channel log. Written here only for the
#: ``paper_error`` record Requirement 14.5 requires on a dropped feed; the general Paper_Channel
#: writer is task 26.x and must route through :func:`insert_session_event` rather than adding a
#: second statement against this table.
EVENT_SELECT = (
    "id,session_id,user_id,sequence,event_id,event_type,schema_version,payload,"
    "emitted_at,created_at,updated_at"
)

#: ``paper_market_events``, whole - the append-only market-data log, and the replay input of
#: Requirements 15.4 and 15.5. Every column is read because ``paper_replay`` reconstructs a session
#: from these rows: ``payload`` carries the prices, ``event_timestamp`` the market instant,
#: ``sequence`` the order, ``source_event_id`` the identity ``uq_paper_market_event`` de-duplicates
#: on, and ``latency_ms`` the delivery measurement of Requirement 14.10.
MARKET_EVENT_SELECT = (
    "id,session_id,user_id,sequence,source_event_id,symbol,timeframe,event_timestamp,"
    "payload,received_at,latency_ms,created_at,updated_at"
)

#: The probe's projection. One column, because the probe asks whether the RELATION exists and
#: nothing else; asking for more would make a reconciled-by-hand column the reason every paper
#: endpoint answers 503.
PROBE_SELECT = "id"


# ══════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ══════════════════════════════════════════════════════════════════════════


class PaperRepositoryError(Exception):
    """Base class for every refusal this module makes."""


class PaperPersistenceError(PaperRepositoryError):
    """A statement DID NOT COMPLETE.

    The exact counterpart of ``settlement_service.SettlementPersistenceError``, and it exists for
    the same reason: it is not the same answer as "the result set was empty". A broken balance
    read must never be reported as a zero balance, and a broken order read must never be reported
    as "no order under that idempotency key", because the second one would place a duplicate order.

    Deliberately **not** a ``PaperError``, so this module raises one exception type for one
    condition and the HTTP surface stays the service layer's decision. The catalogue code that
    condition maps onto is ``PAPER_READ_FAILED`` (503), raised through
    ``paper.errors.paper_read_failed``: the paper counterpart of ``MARKETPLACE_READ_FAILED``, added
    because ``PaperError`` correctly refuses a ``MARKETPLACE_*`` code and
    ``PAPER_PERSISTENCE_UNAVAILABLE`` is specifically the *unapplied migration* - answering that
    for a transient driver failure would name a migration that is in fact applied and send an
    operator to the wrong place. Task 23.2 performs the mapping; the code it maps onto now exists,
    so it is no longer choosing between a wrong code and no code.
    """


class PaperConcurrencyConflict(PaperRepositoryError):
    """A version-guarded UPDATE matched no row: another writer moved it first.

    The optimistic equivalent of losing a ``SELECT ... FOR UPDATE`` race. ``paper_simulator``
    retries three times and then answers 409 ``PAPER_CONCURRENCY_CONFLICT`` (Requirement 16.10,
    task 25.6); this module does not retry, because a retry needs the caller's recomputed payload
    and only the caller has it.
    """

    def __init__(self, message: str, *, table: str, row_id: Optional[str] = None) -> None:
        self.table = table
        self.row_id = row_id
        super().__init__(message)


class PaperDuplicateFill(PaperRepositoryError):
    """``uq_paper_fill_event`` already holds ``(order_id, fill_event_id)``.

    Requirement 16.9 / 18.13: applying a fill event twice must change nothing. This is the
    database saying the first application already happened, so the caller's second attempt is a
    no-op rather than an error to surface (task 25.4's second guard). It is raised rather than
    swallowed because only the caller knows whether it is inside a fill it must abandon.
    """

    def __init__(self, order_id: str, fill_event_id: str) -> None:
        self.order_id = order_id
        self.fill_event_id = fill_event_id
        super().__init__(
            f"fill event {fill_event_id!r} is already recorded on order {order_id!r}; "
            "the first application stands and this one changes nothing"
        )


class PaperDuplicateMarketEvent(PaperRepositoryError):
    """``uq_paper_market_event`` already holds ``(session_id, source_event_id)``.

    Requirement 14.7: a market-data event whose identity this session has already processed is
    discarded. The in-process LRU in ``paper_market_feed`` is a **cache** in front of this, not the
    arbiter - a cache miss (a restarted worker, an evicted entry, a second worker on the same
    session) still has to be safe, and this is what makes it safe. The insert is refused by the
    database, the caller treats it as the no-op Requirement 14.7 asks for, and no second row is
    written.

    Raised rather than swallowed for the reason :class:`PaperDuplicateFill` is: only the caller
    knows whether it must also refrain from advancing its sequence, and swallowing it here would
    let a caller believe a row it does not have was written.
    """

    def __init__(self, session_id: str, source_event_id: str) -> None:
        self.session_id = session_id
        self.source_event_id = source_event_id
        super().__init__(
            f"market event {source_event_id!r} is already recorded for session "
            f"{session_id!r}; the first record stands and this one changes nothing"
        )


class PaperDuplicateSessionEvent(PaperRepositoryError):
    """``uq_paper_event_id`` or ``uq_paper_event_seq`` already holds this Paper_Channel record.

    Requirement 19.8's at-most-once delivery, enforced by the database. ``event_id`` collides when
    the same act is emitted twice; ``sequence`` collides when two writers minted the same position
    in the log. Both mean "this record is already in the log", and both are a no-op for the caller
    rather than a failure of the act being described - an audit line that could not be written must
    not roll back the feed transition it describes, and the log line for it is the visible gap.
    """

    def __init__(self, session_id: str, event_id: str, constraint: str) -> None:
        self.session_id = session_id
        self.event_id = event_id
        self.constraint = constraint
        super().__init__(
            f"paper event {event_id!r} for session {session_id!r} is already recorded "
            f"({constraint}); the first record stands and this one changes nothing"
        )


# ══════════════════════════════════════════════════════════════════════════
# THE MIGRATION PROBE (Requirements 17.1, 17.2, 28.3)
# ══════════════════════════════════════════════════════════════════════════

#: ``None`` = not yet probed. ``True`` = present, cached for the life of the process. ``False`` =
#: absent, re-taken after :data:`PAPER_PERSISTENCE_RECHECK_SECONDS`.
_paper_persistence_supported: Optional[bool] = None
_paper_persistence_checked_at: float = 0.0


def _remember_persistence(supported: bool) -> None:
    global _paper_persistence_supported, _paper_persistence_checked_at
    _paper_persistence_supported = supported
    _paper_persistence_checked_at = time.monotonic()


def remember_persistence_absent() -> None:
    """Record that 009 is not applied, so the next call refuses without re-probing.

    Public for the reason ``signal_service.remember_signal_lifecycle_columns_absent`` is: a
    process that cached a positive verdict and then met ``42P01`` on a real statement has newer
    information than the probe did.
    """
    _remember_persistence(False)


def reset_persistence_probe() -> None:
    """Forget the cached verdict.

    Exists for the test suite, which drives the probe against several doubles in one process, and
    for an operator-facing reload. It changes no stored data.
    """
    global _paper_persistence_supported, _paper_persistence_checked_at
    _paper_persistence_supported = None
    _paper_persistence_checked_at = 0.0


def _cached_persistence_verdict() -> Optional[bool]:
    if _paper_persistence_supported is None:
        return None
    if _paper_persistence_supported:
        return True
    if time.monotonic() - _paper_persistence_checked_at >= PAPER_PERSISTENCE_RECHECK_SECONDS:
        return None
    return False


def is_missing_paper_table_error(exc: BaseException) -> bool:
    """True only when ``exc`` definitively says a ``paper_*`` relation does not exist.

    Narrow on purpose. Anything this returns ``False`` for is re-raised as a
    :class:`PaperPersistenceError`, because the one outcome worse than a paper endpoint that
    fails loudly is a paper endpoint that answers 503 for a network blip and sends an operator
    looking for a migration that is already applied.
    """
    text = str(exc).lower()
    if not text:
        return False
    if any(code in text for code in _MISSING_TABLE_CODES):
        return True
    if not any(table in text for table in (ACCOUNTS_TABLE, "paper_")):
        return False
    return any(phrase in text for phrase in _MISSING_TABLE_PHRASES)


def is_unapplied_default_account_migration_error(exc: BaseException) -> bool:
    """True only when ``exc`` is a ``23502`` on a ``paper_*`` ``session_id`` column.

    That is the single, unambiguous signature of a database carrying 009 but not 013: the seven
    accounting child tables still declare ``session_id NOT NULL``, so every session-scoped write
    succeeds and every DEFAULT-ACCOUNT write - which writes ``session_id: None`` - is refused.

    Narrow on purpose, in both directions. It must name ``session_id`` and it must name a
    ``paper_`` relation, because a ``23502`` on any other column is a caller that omitted a
    required value and answering "apply a migration" for that would send an operator to a file
    that changes nothing. Anything this returns ``False`` for stays a
    :class:`PaperPersistenceError`.
    """
    text = str(exc).lower()
    code = str(getattr(exc, "pgcode", "") or "").lower()
    if code not in ("23502", "not_null_violation") and not any(
        marker in text for marker in _NOT_NULL_VIOLATION_CODES
    ):
        return False
    return "session_id" in text and "paper_" in text


def is_serialization_failure(exc: BaseException) -> bool:
    """True only when ``exc`` is PostgreSQL's ``40001`` or ``40P01``.

    The other half of what task 25.6 retries. :class:`PaperConcurrencyConflict` covers the
    optimistic case this transport actually produces - a version-guarded UPDATE that matched no
    row - and this covers the case a *real* ``SERIALIZABLE`` transaction produces if this code is
    ever moved behind a database function: the transaction was aborted by the server and the
    correct response is to run it again.

    Narrow on purpose, in the same way :func:`is_missing_paper_table_error` is. A retry is only
    safe where the statement definitively did **not** apply, and class 40 is the only class that
    says so; ``40003 statement_completion_unknown`` is therefore excluded, because retrying a
    money movement whose outcome is unknown is how a fill gets applied twice.

    Anything this returns ``False`` for is a :class:`PaperPersistenceError` the caller must
    surface rather than repeat.
    """
    text = str(exc).lower()
    code = str(getattr(exc, "pgcode", "") or "").lower()
    if code in ("40001", "40p01", "serialization_failure", "deadlock_detected"):
        return True
    if not text:
        return False
    return any(marker in text for marker in _SERIALIZATION_FAILURE_CODES)


def _is_unique_violation(exc: BaseException, *constraints: str) -> bool:
    """Whether ``exc`` is a ``23505``, and when ``constraints`` are named, one of them produced it.

    Both spellings a driver may surface are consulted - the SQLSTATE on ``pgcode`` and the text of
    the message - because which one arrives depends on the driver, and PostgREST surfaces neither
    as an attribute.
    """
    text = str(exc).lower()
    code = str(getattr(exc, "pgcode", "") or "").lower()
    if code not in ("23505", "unique_violation") and not any(
        marker in text for marker in _UNIQUE_VIOLATION_CODES
    ):
        return False
    if not constraints:
        return True
    if any(name.lower() in text for name in constraints):
        return True
    # A driver that reports only the SQLSTATE names no constraint at all. On a statement whose
    # only unique index is the one being asked about, a bare 23505 IS that index; a message that
    # does name a constraint and does not name this one is somebody else's violation.
    return "constraint" not in text


def paper_persistence_supported(supabase: Any) -> bool:
    """Whether ``paper_accounts`` exists, cached.

    One ``SELECT id FROM paper_accounts LIMIT 1`` per process, issued through the caller's own
    RLS-scoped client so the probe sees exactly what the read and the write will see - the
    convention ``strategy_service.canonical_columns_supported`` and
    ``signal_service.signal_lifecycle_columns_supported`` already establish. Read-only.

    An *inconclusive* probe answers ``True``: the statement the caller was going to issue is
    attempted, and any real failure surfaces from it as a :class:`PaperPersistenceError` rather
    than being pre-emptively relabelled as an unapplied migration. That is the same disposition
    those two functions take, and it is safe here for the reason it is safe there - the
    pessimistic answer is a refusal, never a fabricated figure.

    A ``None`` client answers ``False``: with no handle there is no storage, and the alternative
    would be to proceed as though there were.
    """
    cached = _cached_persistence_verdict()
    if cached is not None:
        return cached
    if supabase is None:
        return False

    try:
        response = (
            supabase.table(ACCOUNTS_TABLE).select(PROBE_SELECT).limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001 - classified below, never swallowed blindly
        if is_missing_paper_table_error(exc):
            _remember_persistence(False)
            warn_persistence_absent(str(exc))
            return False
        logger.warning(
            "The paper persistence probe was inconclusive (%s); attempting the statement and "
            "letting a real failure surface rather than reporting an unapplied migration.",
            exc,
        )
        return True

    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error and is_missing_paper_table_error(Exception(str(error))):
        _remember_persistence(False)
        warn_persistence_absent(str(error))
        return False

    _remember_persistence(True)
    return True


def warn_persistence_absent(detail: str) -> None:
    """The one log line for an unapplied 009. Names the file and what is not in force."""
    logger.warning(
        "public.%s does not exist, so paper trading has no durable storage. Apply %s, then "
        "restart or wait %.0fs for the re-probe. Until then every /api/paper/* endpoint answers "
        "503 PAPER_PERSISTENCE_UNAVAILABLE and NOTHING is served from process memory: a balance "
        "read from memory would be a fabricated figure (Requirement 28.3) and the "
        "survive-a-restart guarantee of Requirement 17.2 would read as true while being false. "
        "Detail: %s",
        ACCOUNTS_TABLE,
        PAPER_TRADING_MIGRATION,
        PAPER_PERSISTENCE_RECHECK_SECONDS,
        detail,
    )


def warn_default_account_migration_absent(detail: str) -> None:
    """The one log line for an applied 009 with an unapplied 013. Names the file and the scope."""
    logger.warning(
        "A paper_* session_id column is still NOT NULL, so the default account "
        "(session_id IS NULL) cannot own an order, fill, position, balance event, trade, equity "
        "snapshot or metric. Apply %s. Session-scoped paper trading is unaffected and keeps "
        "working; only the six existing /api/paper/* endpoints' account refuses, with 503 "
        "PAPER_PERSISTENCE_UNAVAILABLE naming that file. NOTHING is served from process memory in "
        "the meantime: a balance read from memory would be a fabricated figure (Requirement "
        "28.3). Detail: %s",
        PAPER_DEFAULT_ACCOUNT_MIGRATION,
        detail,
    )


def require_persistence(supabase: Any) -> None:
    """Raise the 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` unless ``paper_accounts`` exists.

    Called first by every public function in this module, so no statement is issued against a
    relation that is not there and no caller can reach a code path that improvises a figure.

    Raises:
        PaperError: code ``PAPER_PERSISTENCE_UNAVAILABLE``, HTTP 503, carrying
            ``details["migration"] = "009_paper_trading.sql"``. The registered structured-error
            handler renders it; nothing here catches it.
    """
    if not paper_persistence_supported(supabase):
        raise paper_persistence_unavailable(PAPER_TRADING_MIGRATION_FILE)


# ══════════════════════════════════════════════════════════════════════════
# THE RESPONSE AND VALUE HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _rows(response: Any, what: str) -> List[Dict[str, Any]]:
    """The rows of a PostgREST response, or ``[]``. Raises when the response signals an error.

    The same reading ``settlement_service._rows`` uses: rows may arrive on ``.data``, under a
    ``["data"]`` key, as the single mapping ``.single()`` returns, or as a bare list in a
    Persistence_Layer double.

    A response carrying a non-empty ``error`` is a statement that DID NOT COMPLETE and raises
    :class:`PaperPersistenceError`. Reading it as "no rows" is what would turn a failed insert
    into a silent success and a failed balance read into a zero balance.
    """
    error = getattr(response, "error", None)
    if error is None and isinstance(response, Mapping):
        error = response.get("error")
    if error:
        raise PaperPersistenceError(f"the {what} returned an error: {error}")

    if response is None:
        return []
    if isinstance(response, Mapping):
        data = response.get("data", response) if "data" in response else response
    else:
        data = getattr(response, "data", response)
    if data is None:
        return []
    if isinstance(data, Mapping):
        return [dict(data)]
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return [dict(row) for row in data if isinstance(row, Mapping)]
    return []


# ══════════════════════════════════════════════════════════════════════════
# INSTRUMENTATION (Requirements 26.6, 27.1, 27.2, 27.6)
# ══════════════════════════════════════════════════════════════════════════


def _record_statement(what: str, measured_from: float, *, failed: bool = False) -> None:
    """One ``paper.db.*`` sample for the statement that started at ``measured_from``.

    ``perf_counter`` rather than ``time.time``: this is an interval, and a wall-clock correction
    mid-statement must not turn into a negative duration.

    ``guarded_collector()`` yields ``None`` when the collector is unavailable and this returns
    without recording: instrumentation must never be what fails a statement this module was asked
    to run, so the statement is still issued and its rows still returned.
    """
    collector = guarded_collector()
    if collector is None:
        return
    collector.record_db_statement(
        "paper",
        what,
        duration_ms=(time.perf_counter() - measured_from) * 1000.0,
        failed=failed,
    )


def _execute(query: Any, what: str) -> List[Dict[str, Any]]:
    """Run one statement and return its rows, converting any failure into a defined outcome.

    Every statement in this module goes through here, so there is one place where "the statement
    raised" and "the statement completed carrying an error" become the same
    :class:`PaperPersistenceError` - and one place that classifies a missing relation, so a
    process whose cached verdict is stale learns from the statement instead of from the next
    probe.

    IT IS ALSO THE ONE PLACE A PAPER ROUND TRIP IS COUNTED (task 33.5, Requirement 26.6)
    -----------------------------------------------------------------------------------
    ``paper.db.round_trips``, ``paper.db.latency_ms`` and ``paper.db.errors``, filed under
    ``what`` as the ``operation`` label. Here and nowhere else, for the reason this function
    exists at all: a second counting point would have to be kept in step with this one, and the
    figure Requirements 27.1/27.2 are about is "statements the database saw", which is exactly
    what passes through here. ``what`` is a closed vocabulary - every call site passes an
    f-string over this module's own table constants - so the label set is bounded.

    A failed statement is counted as a round trip too: it cost the trip. See
    :meth:`MetricsCollector.record_db_statement`.
    """
    _measured_from = time.perf_counter()
    try:
        response = query.execute()
    except PaperRepositoryError:
        _record_statement(what, _measured_from, failed=True)
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as a defined outcome, never swallowed
        _record_statement(what, _measured_from, failed=True)
        if is_missing_paper_table_error(exc):
            remember_persistence_absent()
            warn_persistence_absent(str(exc))
            raise paper_persistence_unavailable(PAPER_TRADING_MIGRATION_FILE) from exc
        if is_unapplied_default_account_migration_error(exc):
            # 009 is applied - the relation exists, the statement reached it - but 013 is not, so
            # the default account still cannot own this row. The probe's verdict is NOT flipped:
            # every session-scoped path works in this database, and caching "absent" would refuse
            # them too. This is the one condition where naming a DIFFERENT migration is the
            # correct answer, which is why it is classified here rather than folded into the
            # probe.
            warn_default_account_migration_absent(str(exc))
            raise paper_persistence_unavailable(
                PAPER_DEFAULT_ACCOUNT_MIGRATION_FILE, {"scope": "default_account"}
            ) from exc
        raise PaperPersistenceError(f"the {what} did not complete: {exc}") from exc
    try:
        rows = _rows(response, what)
    except Exception:
        # A response carrying an error envelope: the statement was issued and did not complete.
        # Counted as a round trip AND as an error, then re-raised unchanged.
        _record_statement(what, _measured_from, failed=True)
        raise
    _record_statement(what, _measured_from)
    return rows


def _one(rows: List[Dict[str, Any]], what: str) -> Dict[str, Any]:
    """The single row a write is expected to return.

    A write that returned nothing is a write this module cannot vouch for: PostgREST returns the
    inserted or updated row, so an empty result means the statement did not do what was asked.
    Returning a synthesised copy of the payload instead would report a row that may not exist.
    """
    if not rows:
        raise PaperPersistenceError(
            f"the {what} completed but returned no row, so the write cannot be confirmed"
        )
    return rows[0]


def _require_text(value: Any, what: str) -> str:
    """``value`` as non-empty text, or ``ValueError``.

    A blank ``user_id`` is a programming error at the call site and not an outcome to serve:
    without it the tenant predicate would match on ``''`` and the scope of the read would be
    whatever the row-level-security policy alone decided.

    An ``Enum`` member is read as its ``value``. ``str(PaperOrderState.FILLED)`` is
    ``'PaperOrderState.FILLED'`` even for a ``(str, Enum)`` member, so stringifying one would
    write - or query for - a value no column holds, silently.
    """
    if value is None:
        raise ValueError(f"{what} is required")
    if isinstance(value, Enum):
        value = value.value
    text = str(value).strip()
    if not text:
        raise ValueError(f"{what} is required")
    return text


def _optional_text(value: Any) -> Optional[str]:
    """``value`` as text, or ``None`` when it is ``None`` or blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _numeric(value: Any, column: str) -> str:
    """A ``NUMERIC(28,10)`` value, as the decimal string that column is written with.

    Routed through ``paper_accounting.to_decimal``, which admits ``Decimal``, ``int`` and exact
    decimal strings and refuses a ``float`` outright (Requirement 18.1): ``str(0.07)`` would
    persist a value the caller never held, and there is no way to tell afterwards that it did.
    """
    return str(to_decimal(value, column))


def _optional_numeric(value: Any, column: str) -> Optional[str]:
    """:func:`_numeric`, but ``None`` passes through.

    Used for the columns that are genuinely absent until a price has been validated -
    ``current_price``, ``unrealized_pnl``, ``avg_fill_price``, ``reference_price``. An absent
    price stays absent; Requirement 18.15 forbids replacing it with a zero.
    """
    return None if value is None else _numeric(value, column)


def _exact_int(value: Any, column: str) -> int:
    """A ``BIGINT`` or ``INTEGER`` value, as an exact ``int``.

    A ``float`` is refused for the reason :func:`_numeric` refuses one, and a ``Decimal`` carrying
    a fraction is refused rather than rounded: rounding a version counter or a row count would
    make the value stored differ from the value the caller reasoned about.
    """
    if isinstance(value, bool):
        raise ValueError(f"{column} must be an integer, got a bool")
    if isinstance(value, int):
        return value
    amount = to_decimal(value, column)
    if amount != amount.to_integral_value():
        raise ValueError(
            f"{column} must be an exact integer, got {amount}"
        )
    return int(amount)


def _minor_units(value: Any, column: str) -> int:
    """A ``BIGINT`` Minor_Units value, as an exact ``int``.

    A fee or a slippage cost is a whole number of minor units. A fractional one is refused rather
    than rounded: rounding here would move money by an amount no caller asked for, and no ledger
    row would record the difference.
    """
    try:
        return _exact_int(value, column)
    except ValueError as exc:
        raise ValueError(
            f"{column} must be an exact integer number of minor units ({exc}); a fractional "
            "minor unit is not a representable amount of money"
        ) from exc


def _instant(value: Any, what: str) -> str:
    """A ``TIMESTAMPTZ`` value, as the UTC ISO-8601 string the column is written with.

    A naive ``datetime`` is read as UTC, because that is what the column stores and because
    ``009``'s shape assertions refuse ``timestamp without time zone`` precisely so no instant is
    silently reinterpreted as local time. A string is passed through unchanged - a caller that
    read a timestamp out of a row and is writing it back must not have it reformatted, which is
    what makes the persistence round trip of task 23.6 exact.
    """
    if value is None:
        raise ValueError(f"{what} is required")
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    if not text:
        raise ValueError(f"{what} is required")
    return text


def _optional_instant(value: Any, what: str) -> Optional[str]:
    """:func:`_instant`, but ``None`` passes through - ``closed_at`` on an open position."""
    return None if value is None else _instant(value, what)


def _utc_now() -> str:
    """The current instant, in UTC, as an ISO-8601 string.

    The only clock read in this module, and it is read for exactly one thing: the ``updated_at``
    of a version-guarded UPDATE, where the value must change even when every other column is
    unchanged. No balance, price, quantity or business timestamp is taken from here - those are
    handed in, which is what makes a Paper_Session replayable (Requirement 15.4).
    """
    return datetime.now(timezone.utc).isoformat()


def _one_of(value: Any, permitted: Sequence[str], column: str) -> str:
    """``value`` as text, refused unless it is one of ``permitted``.

    These are the values the ``chk_*`` constraints in 009 enumerate. Checking them here means the
    caller-facing failure names the column and the permitted set, rather than being a ``23514``
    the error handler has to redact; the constraint remains the guarantee.
    """
    text = _require_text(value, column)
    if text not in permitted:
        raise ValueError(
            f"{column} must be one of {tuple(permitted)}, got {text!r}"
        )
    return text


# ══════════════════════════════════════════════════════════════════════════
# THE ACCOUNT (Requirement 17.1) - AND WHAT KEEPS THE DEFAULT ONE SINGLE
# ══════════════════════════════════════════════════════════════════════════


def _account_query(supabase: Any, *, user_id: str, currency: str, session_id: Optional[str]) -> Any:
    """The one read that locates an account, default or session-scoped.

    ``user_id`` is a predicate, not a filter applied afterwards (Requirement 21.5). The
    default account is located by ``session_id IS NULL`` - spelled ``.is_("session_id", "null")``,
    which is how PostgREST expresses SQL's ``IS NULL`` and the spelling
    ``marketplace/expiry_sweep.py`` and ``routers/library.py`` already use. ``.eq("session_id",
    None)`` would render as ``session_id=eq.None`` and match nothing, which would silently create
    a second default account on every call and make ``uq_paper_account_default`` the only thing
    standing between a user and two balances.
    """
    query = (
        supabase.table(ACCOUNTS_TABLE)
        .select(ACCOUNT_SELECT)
        .eq("user_id", user_id)
        .eq("currency", currency)
    )
    if session_id is None:
        return query.is_("session_id", "null")
    return query.eq("session_id", session_id)


def read_account(
    supabase: Any,
    user_id: Any,
    currency: Any = DEFAULT_CURRENCY,
    session_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """The account row, or ``None`` when this user has no such account.

    ``None`` is an answer and means the read completed and matched nothing.
    :class:`PaperPersistenceError` is the other outcome, and it is not the same one.

    Args:
        supabase: the caller's RLS-scoped Persistence_Layer handle.
        user_id: the identity resolved from the authenticated server-side session.
        currency: the account currency; ``'USD'`` is the default account's.
        session_id: ``None`` for the default account, a Paper_Session id for a session's.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    cur = _require_text(currency, "currency").upper()
    sid = _optional_text(session_id)

    rows = _execute(
        _account_query(supabase, user_id=uid, currency=cur, session_id=sid),
        f"{ACCOUNTS_TABLE} read",
    )
    if not rows:
        return None
    # The predicates are restated in the process. A Persistence_Layer double, or a future filter
    # change that lost one of them, would otherwise let a session account be served where the
    # default account was asked for - and a session's isolated balances are not the balances
    # /api/paper/account reports (Requirement 17.6).
    for row in rows:
        if str(row.get("user_id")) != uid:
            continue
        row_session = _optional_text(row.get("session_id"))
        if row_session == sid:
            return row
    return None


#: The "do not narrow by ``session_id`` at all" marker for :func:`read_order`.
#:
#: It exists because ``None`` already MEANS something there - ``session_id IS NULL``, the default
#: account, exactly as it does in :func:`get_fills`, :func:`get_equity_snapshots` and
#: :func:`probe_idempotency_key` - so ``None`` cannot also stand for "any scope". A caller that
#: already holds the order's scope (``paper_simulator``'s fill path, which read the account it is
#: settling against in the same breath) omits the argument; a caller answering a request that is
#: defined as being about ONE book passes that book. Module-private and never exported: the scope
#: is a decision each call site has to make, not one a caller can be handed.
_ANY_ORDER_SCOPE = object()


def read_order(
    supabase: Any,
    user_id: Any,
    order_id: Any,
    *,
    session_id: Any = _ANY_ORDER_SCOPE,
) -> Optional[Dict[str, Any]]:
    """One ``paper_orders`` row this user owns, or ``None``.

    ``user_id`` is a predicate alongside ``id``, so another tenant's order is **not fetched**
    rather than fetched and then hidden - which is also why the caller can only report it as not
    found. That is the single cross-tenant shape Requirement 21.4 asks for, and it is a change
    from the ``PermissionError`` the in-memory service raised: answering "forbidden" for an order
    that exists and "not found" for one that does not is itself the disclosure.

    ``session_id`` NARROWS THE SCOPE, AND ``None`` IS THE DEFAULT ACCOUNT
    --------------------------------------------------------------------
    Omitted, the read is by ``(user_id, id)`` and finds the user's order whatever book it belongs
    to - which is what ``paper_simulator``'s fill path wants, because it has already located the
    account it is settling against and is re-reading that order fresh in place of a ``FOR UPDATE``.

    Passed ``None``, the read is narrowed to ``session_id IS NULL``: the DEFAULT ACCOUNT, and
    therefore the only order set the retained ``/api/paper/*`` endpoints are about
    (Requirement 17.12). Spelled ``.is_("session_id", "null")`` - the spelling
    :func:`read_account`, :func:`get_fills` and :func:`probe_idempotency_key` use - because
    ``.eq("session_id", None)`` renders as ``session_id=eq.None`` and matches nothing, which would
    report every default-account order as absent. Passed a session id, the read is narrowed to
    that Paper_Session.

    A narrowed read answers ``None`` for an order that exists in ANOTHER scope, and that is the
    point: it is the same answer an unknown id gets, for the same reason the cross-tenant case
    gives that answer. A caller told "this order is real but not yours to act on here" has been
    told the order is real.

    ``None`` means the read completed and matched nothing. :class:`PaperPersistenceError` is the
    other outcome, and a caller must not read it as "no such order" - for a cancel that would
    report a resting order as absent while it is still on the book.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    oid = _require_text(order_id, "order_id")
    scoped = session_id is not _ANY_ORDER_SCOPE
    sid = _optional_text(session_id) if scoped else None

    query = (
        supabase.table(ORDERS_TABLE)
        .select(ORDER_SELECT)
        .eq("id", oid)
        .eq("user_id", uid)
    )
    if scoped:
        query = query.is_("session_id", "null") if sid is None else query.eq("session_id", sid)

    rows = _execute(query.limit(1), f"{ORDERS_TABLE} read")
    # The predicates are restated in the process for the reason :func:`probe_idempotency_key`
    # restates them: a lost filter must not let a session's order be served where a
    # default-account one was asked for, because the caller acts on what it is handed.
    for row in rows:
        if str(row.get("user_id")) != uid or str(row.get("id")) != oid:
            continue
        if scoped and _optional_text(row.get("session_id")) != sid:
            continue
        return row
    return None


def get_or_create_account(
    supabase: Any,
    user_id: Any,
    currency: Any = DEFAULT_CURRENCY,
    session_id: Any = None,
    *,
    initial_capital: Any = None,
) -> Dict[str, Any]:
    """The user's account for ``(currency, session_id)``, created at ``initial_capital`` if absent.

    THE DEFAULT ACCOUNT IS ``session_id IS NULL``
    ---------------------------------------------
    With ``session_id=None`` this is the account the six existing ``/api/paper/*`` endpoints
    serve, and ``uq_paper_account_default`` - PARTIAL unique on ``(user_id, currency) WHERE
    session_id IS NULL`` - is what keeps it single. With a ``session_id`` it is that session's
    isolated balance set (Requirement 17.6), kept single per currency by
    ``uq_paper_account_session``.

    WHY THE CREATE PATH LOSES GRACEFULLY
    ------------------------------------
    Read-then-insert is not atomic over HTTP, so two concurrent first requests for the same user
    both read nothing and both insert. One of them violates the unique index. That is the correct
    outcome - the index is the guarantee - and this function answers it by re-reading and
    returning the row that won, so the caller receives the one account rather than a ``23505``.
    If the re-read then finds nothing, the insert failed for some other reason and
    :class:`PaperPersistenceError` is raised: a second insert attempt could create the duplicate
    the index just prevented.

    ``total_equity`` at creation is ``initial_capital`` because ``available_balance`` is
    ``initial_capital``, ``locked_balance`` is zero and there are no positions - so the
    zero-tolerance identity of Requirement 18.3 holds by construction. It is computed from the
    row's own columns, not assumed.

    Args:
        initial_capital: the starting balance, used only on the create path. ``None`` takes
            :data:`DEFAULT_INITIAL_CAPITAL`, which is the figure ``PaperTradingService`` already
            defaults to; a caller that knows the capital passes it. A ``float`` is refused
            (Requirement 18.1).

    Raises:
        PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied.
        PaperPersistenceError: a statement did not complete.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    cur = _require_text(currency, "currency").upper()
    sid = _optional_text(session_id)

    existing = read_account(supabase, uid, cur, sid)
    if existing is not None:
        return existing

    capital = _numeric(
        DEFAULT_INITIAL_CAPITAL if initial_capital is None else initial_capital,
        "initial_capital",
    )
    payload: Dict[str, Any] = {
        "user_id": uid,
        "session_id": sid,
        "currency": cur,
        "initial_capital": capital,
        "available_balance": capital,
        "locked_balance": _numeric(0, "locked_balance"),
        "realized_pnl": _numeric(0, "realized_pnl"),
        # available + locked + position_market_value, with no positions and nothing locked.
        "total_equity": capital,
        "version": 1,
        "stale": False,
    }

    try:
        rows = _execute(
            supabase.table(ACCOUNTS_TABLE).insert(payload),
            f"{ACCOUNTS_TABLE} insert",
        )
    except PaperPersistenceError as exc:
        if not _is_unique_violation(
            exc.__cause__ or exc, "uq_paper_account_default", "uq_paper_account_session"
        ):
            raise
        winner = read_account(supabase, uid, cur, sid)
        if winner is None:
            raise PaperPersistenceError(
                f"the {ACCOUNTS_TABLE} insert for user {uid} was refused as a duplicate but no "
                "account is readable, so the account state is unknown; nothing is inserted a "
                "second time because that is what the unique index just prevented"
            ) from exc
        return winner

    return _one(rows, f"{ACCOUNTS_TABLE} insert")


def lock_account_for_update(
    supabase: Any,
    user_id: Any,
    *,
    account_id: Any = None,
    currency: Any = DEFAULT_CURRENCY,
    session_id: Any = None,
) -> Dict[str, Any]:
    """The account row to compute the next balances from, ``version`` included.

    WHAT THIS SUBSTITUTES FOR
    -------------------------
    ``design.md`` and task 25.3 write ``SELECT ... FROM paper_accounts WHERE session_id = :id FOR
    UPDATE``. **There is no ``FOR UPDATE`` in PostgREST**: it speaks one HTTP statement per
    request, so a row lock taken by this read could not survive until the caller's UPDATE. What is
    reproduced instead is the *outcome* a ``FOR UPDATE`` was there for - a lost update surfaces as
    a conflict rather than as a silently overwritten balance - by the optimistic protocol
    ``settlement_service._apply_transition`` already uses on this codebase's other money path:

        account = lock_account_for_update(sb, uid, account_id=aid)   # carries version = N
        ... compute the new balances from THAT image ...
        bump_version(sb, user_id=uid, account_id=aid, expected_version=N, payload={...})

    :func:`bump_version` carries ``.eq("version", N)``, so a writer that moved the row in between
    makes it match zero rows and raises :class:`PaperConcurrencyConflict`. The name is kept
    because it is the name the design and tasks 25.3-25.6 use for this step; the mechanism is
    documented here so nobody reads the name as a promise of a held lock.

    What this does NOT give is serialisation *across* several statements. The fill, the position
    upsert, the balance event and the equity snapshot of task 25.4 are four requests, and
    PostgREST offers no transaction to put them in. Their guards are the database's own -
    ``uq_paper_fill_event``, ``chk_paper_order_fill_bound``, ``chk_paper_balances_non_negative``,
    ``uq_paper_position_open`` - plus this version predicate. Requirement 24.6's single
    transaction is not achievable over this transport and would need a database function
    (``rpc``) to hold; that is recorded rather than glossed.

    Args:
        account_id: the account to read. When ``None``, the account is located by
            ``(user_id, currency, session_id)`` the way :func:`read_account` locates it.

    Raises:
        PaperPersistenceError: the read did not complete, or no such account exists - because a
            caller asking to lock an account has one, and answering ``None`` would let it compute
            new balances against nothing.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    aid = _optional_text(account_id)

    if aid is None:
        account = read_account(supabase, uid, currency, session_id)
    else:
        rows = _execute(
            supabase.table(ACCOUNTS_TABLE)
            .select(ACCOUNT_SELECT)
            .eq("id", aid)
            .eq("user_id", uid)
            .limit(1),
            f"{ACCOUNTS_TABLE} read",
        )
        account = rows[0] if rows else None

    if account is None:
        raise PaperPersistenceError(
            "no paper account is readable for this identity and scope, so there is no balance to "
            "compute against; nothing was locked and nothing was written"
        )
    return account


def bump_version(
    supabase: Any,
    *,
    user_id: Any,
    account_id: Any,
    expected_version: Any,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply ``payload`` to the account, guarded by the ``version`` that was read.

    The other half of :func:`lock_account_for_update`. ``version`` is set to
    ``expected_version + 1`` in the same statement that requires it to still be
    ``expected_version``, so two concurrent writers cannot both succeed and the loser learns that
    it lost.

    ``payload`` is written as given; this function computes no balance. The money columns it may
    carry - ``available_balance``, ``locked_balance``, ``realized_pnl``, ``total_equity`` - are
    passed through :func:`_numeric`, so a ``float`` balance is refused here rather than persisted.

    Raises:
        PaperConcurrencyConflict: the UPDATE matched no row. Either another writer moved it, or
            the account does not belong to this identity - both are refusals, and the caller
            retries (task 25.6) rather than being told the write succeeded.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    aid = _require_text(account_id, "account_id")
    version = _exact_int(expected_version, "version")

    update: Dict[str, Any] = {}
    for column, value in dict(payload or {}).items():
        if column in ("available_balance", "locked_balance", "realized_pnl", "total_equity",
                      "initial_capital"):
            update[column] = _numeric(value, column)
        elif column == "last_price_at":
            update[column] = _optional_instant(value, column)
        elif column in ("id", "user_id", "session_id", "currency", "version", "created_at"):
            raise ValueError(
                f"{column} is not writable through bump_version: the identity, the scope and the "
                "version counter are what the guard is made of"
            )
        else:
            update[column] = value
    update["version"] = version + 1
    update["updated_at"] = _utc_now()

    rows = _execute(
        supabase.table(ACCOUNTS_TABLE)
        .update(update)
        .eq("id", aid)
        .eq("user_id", uid)
        .eq("version", version),
        f"{ACCOUNTS_TABLE} update",
    )
    if not rows:
        raise PaperConcurrencyConflict(
            f"the paper account update matched no row at version {version}; another writer moved "
            "it while this change was in flight, so nothing was written",
            table=ACCOUNTS_TABLE,
            row_id=aid,
        )
    return rows[0]


# ══════════════════════════════════════════════════════════════════════════
# THE READS (Requirements 17.1, 17.2, 21.5)
# ══════════════════════════════════════════════════════════════════════════
#
# WHY THE CHILD READS ARE SCOPED BY ``account_id`` AND NOT BY ``session_id``
# -------------------------------------------------------------------------
# ``design.md`` keys positions as ``paper_positions rows keyed (account_id, symbol)``, and
# ``uq_paper_position_open`` is UNIQUE ``(account_id, symbol) WHERE closed_at IS NULL`` - so the
# account, not the session, is the identity a position hangs from. Scoping by ``account_id``
# therefore serves the default account (``session_id IS NULL``) and a session account with one
# code path, and it is the predicate the unique index already assumes. ``session_id`` remains
# available as an additional narrowing predicate for the session views of task 27.x.
#
# ``user_id`` is on every read regardless, as a predicate. That is Requirement 21.5 read
# literally: another tenant's rows are never fetched, so they cannot be leaked by a handler bug,
# an exception log or a guessed identifier.


def _scoped(
    query: Any,
    *,
    user_id: str,
    account_id: Optional[str],
    session_id: Optional[str],
) -> Any:
    """Apply the tenant predicate and the optional account / session narrowing to ``query``."""
    scoped = query.eq("user_id", user_id)
    if account_id is not None:
        scoped = scoped.eq("account_id", account_id)
    if session_id is not None:
        scoped = scoped.eq("session_id", session_id)
    return scoped


def get_positions(
    supabase: Any,
    user_id: Any,
    *,
    account_id: Any = None,
    session_id: Any = None,
    symbol: Any = None,
    include_closed: bool = False,
) -> List[Dict[str, Any]]:
    """The user's paper positions, open only unless ``include_closed``.

    A fully closed position persists with ``size = 0`` and ``closed_at`` set rather than being
    deleted (Requirement 18.5), which is the change from the in-memory ``del
    user_positions[symbol]`` the existing service performs. ``include_closed=False`` filters
    ``closed_at IS NULL`` **in the statement**, so the default read returns exactly the set the
    old dictionary held - which is what keeps ``GET /api/paper/positions`` and
    ``risk.py``'s ``open_pos_count`` reporting the same figures they report today.

    Returns:
        The rows, newest-opened first. ``[]`` means the read completed and there are none.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")

    query = _scoped(
        supabase.table(POSITIONS_TABLE).select(POSITION_SELECT),
        user_id=uid,
        account_id=_optional_text(account_id),
        session_id=_optional_text(session_id),
    )
    if symbol is not None:
        query = query.eq("symbol", _require_text(symbol, "symbol"))
    if not include_closed:
        query = query.is_("closed_at", "null")

    rows = _execute(
        query.order("opened_at", desc=True), f"{POSITIONS_TABLE} read"
    )
    return [row for row in rows if str(row.get("user_id")) == uid]


def get_orders(
    supabase: Any,
    user_id: Any,
    *,
    account_id: Any = None,
    session_id: Any = None,
    legacy_status: Any = None,
    order_state: Any = None,
    symbol: Any = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The user's paper orders, newest first.

    ``legacy_status`` is the retained ``PaperOrderStatus`` spelling that
    ``GET /api/paper/orders?status=OPEN`` filters on, and it is applied as a predicate on
    ``paper_orders.legacy_status`` rather than by inspecting ``order_state`` here. That keeps the
    meaning of ``OPEN`` in exactly one place - ``paper_order_state.LEGACY_STATUS_FOR_STATE``,
    which :func:`insert_order` writes the column from - so ``ACCEPTED`` and ``PARTIALLY_FILLED``
    both answer ``OPEN`` without this function knowing that they do (Requirement 17.12).

    ``order_state`` filters on the canonical six-value column, for callers that want the new
    spelling. Both may be given; both are predicates.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")

    query = _scoped(
        supabase.table(ORDERS_TABLE).select(ORDER_SELECT),
        user_id=uid,
        account_id=_optional_text(account_id),
        session_id=_optional_text(session_id),
    )
    if legacy_status is not None:
        query = query.eq("legacy_status", _require_text(legacy_status, "legacy_status").upper())
    if order_state is not None:
        # Through ``PaperOrderState``, not through :func:`_one_of`: that helper stringifies its
        # argument, and ``str(PaperOrderState.FILLED)`` is ``'PaperOrderState.FILLED'`` for a
        # ``(str, Enum)`` member - so a caller passing the enum would have queried for a value no
        # row holds and silently received nothing. The enum is the authority for the value set.
        query = query.eq("order_state", PaperOrderState(order_state).value)
    if symbol is not None:
        query = query.eq("symbol", _require_text(symbol, "symbol"))
    query = query.order("created_at", desc=True)
    if limit is not None:
        query = query.limit(_exact_int(limit, "limit"))

    rows = _execute(query, f"{ORDERS_TABLE} read")
    return [row for row in rows if str(row.get("user_id")) == uid]


def get_trades(
    supabase: Any,
    user_id: Any,
    *,
    account_id: Any = None,
    session_id: Any = None,
    symbol: Any = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The user's closed round-trips, most recently closed first.

    ``paper_trades`` holds one row per position that reached size zero (Requirement 18.10's
    "closed" trade), which is what ``GET /api/paper/trades`` and ``TradeHistory.jsx`` read. Every
    individual fill lives in ``paper_fills``; the two are different sets and this is the closed
    one.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")

    query = _scoped(
        supabase.table(TRADES_TABLE).select(TRADE_SELECT),
        user_id=uid,
        account_id=_optional_text(account_id),
        session_id=_optional_text(session_id),
    )
    if symbol is not None:
        query = query.eq("symbol", _require_text(symbol, "symbol"))
    query = query.order("closed_at", desc=True)
    if limit is not None:
        query = query.limit(_exact_int(limit, "limit"))

    rows = _execute(query, f"{TRADES_TABLE} read")
    return [row for row in rows if str(row.get("user_id")) == uid]


def get_fills(
    supabase: Any,
    user_id: Any,
    *,
    session_id: Any = None,
    order_id: Any = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The user's fills, most recently filled first.

    One row per fill, which is the set the existing ``GET /api/paper/trades`` serves: the
    in-memory ``self._trades[uid]`` list held one record for every fill, not one per closed
    round-trip, and ``TradeHistory.jsx`` and ``dashboard_aggregation_service``'s today-PnL sum both
    read that list. ``paper_trades`` is the *other* set - the closed round-trips of Requirement
    18.10 - and :func:`get_trades` serves it; the two are different questions and this is the
    per-fill one.

    ``session_id=None`` READS THE DEFAULT ACCOUNT
    ---------------------------------------------
    ``paper_fills`` carries no ``account_id`` (009 gives it ``order_id``, ``session_id`` and
    ``user_id``), so unlike :func:`get_orders` and :func:`get_trades` there is no account column to
    narrow by, and ``None`` here means ``session_id IS NULL`` - the default account - exactly as it
    does in :func:`get_equity_snapshots`, which has the same column set and the same reason.
    Spelled ``.is_("session_id", "null")``, because ``.eq("session_id", None)`` renders as
    ``session_id=eq.None`` and would report an empty fill history for an account that has one.

    Returns:
        The rows, newest ``filled_at`` first. ``[]`` means the read completed and there are none.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _optional_text(session_id)

    query = (
        supabase.table(FILLS_TABLE).select(FILL_SELECT).eq("user_id", uid)
    )
    query = query.is_("session_id", "null") if sid is None else query.eq("session_id", sid)
    if order_id is not None:
        query = query.eq("order_id", _require_text(order_id, "order_id"))
    query = query.order("filled_at", desc=True)
    if limit is not None:
        query = query.limit(_exact_int(limit, "limit"))

    rows = _execute(query, f"{FILLS_TABLE} read")
    return [
        row
        for row in rows
        if str(row.get("user_id")) == uid
        and _optional_text(row.get("session_id")) == sid
    ]


def get_balance_events(
    supabase: Any,
    user_id: Any,
    *,
    account_id: Any = None,
    session_id: Any = None,
    cause: Any = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The user's balance ledger, most recent first.

    The append-only record of every balance movement, and the only persisted home of the
    **per-fill** realized PnL: ``realized_delta`` on the ``cause = 'FILL'`` event carrying that
    fill's ``fill_id``. ``paper_trades.realized_pnl`` cannot answer that question, because a
    partial close moves ``realized_pnl`` on the account and writes no ``paper_trades`` row
    (Requirement 18.10 closes a trade at size zero) - so a trades-only join would have to report
    zero realized PnL for a fill that realized a figure the account already carries, which is the
    fabricated figure Requirement 28.3 forbids.

    ``cause``, when given, is applied as a predicate and must be one of
    :data:`BALANCE_EVENT_CAUSES`. ``account_id`` narrows to one book, the way :func:`get_trades`
    does; ``user_id`` is a predicate regardless (Requirement 21.5).
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")

    query = _scoped(
        supabase.table(BALANCE_EVENTS_TABLE).select(BALANCE_EVENT_SELECT),
        user_id=uid,
        account_id=_optional_text(account_id),
        session_id=_optional_text(session_id),
    )
    if cause is not None:
        query = query.eq("cause", _one_of(cause, BALANCE_EVENT_CAUSES, "cause"))
    query = query.order("occurred_at", desc=True)
    if limit is not None:
        query = query.limit(_exact_int(limit, "limit"))

    rows = _execute(query, f"{BALANCE_EVENTS_TABLE} read")
    return [row for row in rows if str(row.get("user_id")) == uid]


def get_equity_snapshots(
    supabase: Any,
    user_id: Any,
    *,
    session_id: Any = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """A session's equity series, in non-decreasing order.

    ``ORDER BY series_index, taken_at ASC`` - ``idx_paper_equity``'s own order, and it is
    **ascending on purpose**. ``paper_accounting.max_drawdown`` refuses a series handed to it out
    of timestamp order rather than sorting it, because the drawdown of a resorted series is not
    the drawdown of the series that was read; reading it descending here and letting the caller
    reverse it would put that decision in two places. ``series_index`` leads because a reset
    begins a new series (Requirement 17.15) while keeping the old snapshots readable.

    ``paper_equity_snapshots`` carries no ``account_id``, so this read is scoped by ``user_id``
    and ``session_id`` - which are the two columns 009 gives it.

    ``session_id=None`` reads the DEFAULT ACCOUNT's series, spelled ``.is_("session_id", "null")``
    - the same spelling :func:`read_account` uses, and for the same reason: ``.eq("session_id",
    None)`` renders as ``session_id=eq.None`` and would match nothing, which would report an empty
    equity curve for an account that has one. It is not a read of "every series this user has":
    the predicate is on the column, so a default-account read never returns a session's snapshots
    and a session read never returns the default account's.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _optional_text(session_id)

    query = (
        supabase.table(EQUITY_SNAPSHOTS_TABLE)
        .select(EQUITY_SNAPSHOT_SELECT)
        .eq("user_id", uid)
    )
    query = (
        query.is_("session_id", "null") if sid is None else query.eq("session_id", sid)
    )
    query = query.order("series_index", desc=False).order("taken_at", desc=False)
    if limit is not None:
        query = query.limit(_exact_int(limit, "limit"))

    rows = _execute(query, f"{EQUITY_SNAPSHOTS_TABLE} read")
    return [
        row
        for row in rows
        if str(row.get("user_id")) == uid
        and _optional_text(row.get("session_id")) == sid
    ]


def get_metrics(
    supabase: Any,
    user_id: Any,
    *,
    session_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """The session's most recently computed ``paper_metrics`` row, or ``None``.

    009 places no unique index on ``paper_metrics.session_id``, so a session may hold several
    computed rows; the newest by ``computed_at`` is the current one and that is what this returns.
    ``None`` means the read completed and no metrics have been computed yet - which is a different
    statement from "the metrics are zero", and Requirement 28.5 is why it is not flattened into
    one: an absent win rate is reported absent, never as a zero presented as a measurement.

    ``session_id=None`` reads the DEFAULT ACCOUNT's metrics row, by ``session_id IS NULL``.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _optional_text(session_id)

    query = (
        supabase.table(METRICS_TABLE).select(METRICS_SELECT).eq("user_id", uid)
    )
    query = (
        query.is_("session_id", "null") if sid is None else query.eq("session_id", sid)
    )

    rows = _execute(
        query.order("computed_at", desc=True).limit(1), f"{METRICS_TABLE} read"
    )
    for row in rows:
        if str(row.get("user_id")) == uid and _optional_text(row.get("session_id")) == sid:
            return row
    return None


# ══════════════════════════════════════════════════════════════════════════
# THE WRITES THE SIMULATOR CALLS (Requirements 16.x, 18.x)
# ══════════════════════════════════════════════════════════════════════════
#
# WHY EVERY WRITE TAKES ``session_id`` AND WHY IT IS OPTIONAL
# ----------------------------------------------------------
# ``paper_orders``, ``paper_fills``, ``paper_positions``, ``paper_balance_events``,
# ``paper_trades``, ``paper_equity_snapshots`` and ``paper_metrics`` each declared
# ``session_id UUID NOT NULL REFERENCES paper_sessions(id)`` in 009, while
# ``paper_accounts.session_id`` was nullable - and that nullable column IS the default account.
# So the default account had nowhere to persist an order, a fill, a position, a trade or an
# equity point: a child row needed a ``paper_sessions`` row, and ``paper_sessions`` requires
# nine NOT NULL columns a default account has no honest value for.
#
# ``013_paper_default_account_children.sql`` relaxes those seven columns to NULLABLE, so a row
# belonging to the default account carries ``session_id IS NULL`` exactly as its account does.
# ``session_id`` is therefore OPTIONAL on every write below, defaulting to ``None``:
#
#   * ``None``  -> the default account. The row is written with ``session_id: None``.
#   * a value   -> that Paper_Session, unchanged from before.
#
# A fabricated session id is still never invented here; ``None`` is the honest representation of
# "this row belongs to no Paper_Session", which is what the default account is.
#
# WHAT 013 RECONCILED SO THE RELAXATION COSTS NOTHING
# --------------------------------------------------
# ``uq_paper_order_idem`` is UNIQUE ``(session_id, idempotency_key)``, and SQL treats two NULLs
# as DISTINCT - so with a NULL ``session_id`` it stops de-duplicating altogether and a retried
# default-account intent would insert a second order (the exact failure Requirement 16.8 exists
# to prevent, silently). 013 adds ``uq_paper_order_idem_default``, a PARTIAL unique index on
# ``(account_id, idempotency_key) WHERE session_id IS NULL AND idempotency_key IS NOT NULL``:
# the account is the book a default-account key is unique within, ``account_id`` is NOT NULL, and
# ``uq_paper_account_default`` makes it resolve to one default account per ``(user_id,
# currency)``. :func:`probe_idempotency_key` therefore probes by ``account_id`` for the default
# account and by ``session_id`` for a session, matching whichever index arbitrates.
#
# 013 also replaced ``paper_append_only_guard()``: its cascade exemption asked whether
# ``OLD.session_id`` still named a ``paper_sessions`` row, and nothing equals NULL, so a
# default-account fill, trade, balance event or equity snapshot would have been silently
# removable. It now resolves a NULL-session row's parent through ``paper_orders`` or
# ``paper_accounts`` instead. Neither of those is this module's business, but both are why it is
# safe for this module to write ``session_id: None``.
#
# THE TENANT SCOPE IS UNCHANGED. ``user_id`` is NOT NULL on all seven tables, is still the
# row-level-security predicate, and is still a ``WHERE`` predicate on every statement below.
# Relaxing ``session_id`` relaxed nothing else (Requirements 21.2, 21.5).


def probe_idempotency_key(
    supabase: Any,
    *,
    user_id: Any,
    idempotency_key: Any,
    session_id: Any = None,
    account_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """The order already recorded under ``idempotency_key`` in this scope, or ``None``.

    THE SCOPE IS THE SESSION, OR - FOR THE DEFAULT ACCOUNT - THE ACCOUNT
    -------------------------------------------------------------------
    ``uq_paper_order_idem`` is UNIQUE ``(session_id, idempotency_key)``, so for a session order at
    most one row can match and the scope of a key is one Paper_Session - two sessions may reuse a
    key without colliding (Requirement 16.11).

    With ``session_id=None`` the order belongs to the default account, which is not a
    Paper_Session, and ``uq_paper_order_idem`` cannot arbitrate those rows at all: SQL treats two
    NULL ``session_id`` values as distinct. ``uq_paper_order_idem_default`` -
    ``(account_id, idempotency_key) WHERE session_id IS NULL AND idempotency_key IS NOT NULL``,
    added by ``013_paper_default_account_children.sql`` - is what does, so this probe filters
    ``account_id`` and ``session_id IS NULL`` in that case. ``account_id`` is therefore REQUIRED
    when ``session_id`` is absent: probing by ``user_id`` alone would match a key recorded against
    the user's account in a different currency, which is a different book with a different
    balance, and returning that order would answer a duplicate for a request that has none.

    ``None`` means the read completed and no order exists under that key. It is emphatically not
    what a failed read returns: answering "no order" for a broken read is what would place a
    second order for a request the caller believes it already made, which is the exact failure
    Requirement 16.8's idempotency exists to prevent.

    This function does **not** compare fingerprints. ``paper_simulator.submit_intent`` compares
    ``fingerprint`` and raises ``PAPER_IDEMPOTENCY_CONFLICT`` on a mismatch (task 25.3); the
    repository returns the row and stays out of the decision.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _optional_text(session_id)
    aid = _optional_text(account_id)
    key = _validate_idempotency_key(idempotency_key)
    if key is None:
        raise ValueError("idempotency_key is required to probe for a duplicate order")
    if sid is None and aid is None:
        raise ValueError(
            "account_id is required to probe a default-account idempotency key: the account is "
            "the scope uq_paper_order_idem_default arbitrates, and a probe scoped only by user_id "
            "would answer with an order belonging to a different currency's account"
        )

    query = (
        supabase.table(ORDERS_TABLE)
        .select(ORDER_SELECT)
        .eq("user_id", uid)
        .eq("idempotency_key", key)
    )
    if sid is None:
        query = query.eq("account_id", aid).is_("session_id", "null")
    else:
        query = query.eq("session_id", sid)

    rows = _execute(query.limit(1), f"{ORDERS_TABLE} idempotency read")
    # The predicates are restated in the process for the reason :func:`read_account` restates
    # them: a Persistence_Layer double, or a lost filter, must not let a session order be served
    # where a default-account one was asked for - a session's orders are not the orders
    # ``GET /api/paper/orders`` reports (Requirement 17.6).
    for row in rows:
        if str(row.get("user_id")) != uid:
            continue
        if _optional_text(row.get("session_id")) != sid:
            continue
        if sid is None and str(row.get("account_id")) != aid:
            continue
        return row
    return None


def _validate_idempotency_key(value: Any) -> Optional[str]:
    """``value`` as a 1-to-128-character key, or ``None`` when absent.

    ``chk_paper_order_idem_len`` says the same thing. It is checked here so the failure names the
    length rule, and checked earlier still in ``paper_simulator.submit_intent`` so the
    caller-facing error is the simulator's - the constraint is the backstop, not the error
    surface (task 25.3).
    """
    if value is None:
        return None
    key = str(value).strip()
    if not key:
        return None
    if len(key) > IDEMPOTENCY_KEY_MAX_CHARS:
        raise ValueError(
            f"idempotency_key must be 1 to {IDEMPOTENCY_KEY_MAX_CHARS} characters, got "
            f"{len(key)}"
        )
    return key


def insert_order(
    supabase: Any,
    *,
    account_id: Any,
    user_id: Any,
    symbol: Any,
    side: Any,
    order_type: Any,
    quantity: Any,
    fingerprint: Any,
    session_id: Any = None,
    order_state: Any = PaperOrderState.CREATED,
    limit_price: Any = None,
    reference_price: Any = None,
    filled_quantity: Any = 0,
    avg_fill_price: Any = None,
    fee_minor: Any = 0,
    slippage_minor: Any = 0,
    rejection_reason: Any = None,
    idempotency_key: Any = None,
    signal_id: Any = None,
) -> Dict[str, Any]:
    """Insert one ``paper_orders`` row and return it.

    ``legacy_status`` IS DERIVED, NEVER PASSED
    -----------------------------------------
    It is read from ``paper_order_state.LEGACY_STATUS_FOR_STATE[order_state]``, so
    ``paper_orders.order_state`` and ``paper_orders.legacy_status`` cannot disagree about the same
    order. That is what keeps ``GET /api/paper/orders?status=OPEN`` returning exactly the orders it
    returns today: ``ACCEPTED`` and ``PARTIALLY_FILLED`` both map to ``OPEN``, ``CREATED`` to
    ``NEW`` (Requirement 17.12). Accepting the column as an argument would allow an order that is
    ``FILLED`` and ``OPEN`` at once.

    Every order is inserted at its starting state - ``CREATED`` by default, which is where task
    25.3's pipeline begins and the only state the transition guard admits as an origin. Moving it
    onward is an UPDATE the simulator issues, validated against
    ``paper_order_allowed_transitions`` by ``trg_paper_order_transition_guard``; this function
    does not transition anything.

    ``session_id`` IS OPTIONAL
    -------------------------
    ``None`` - the default is - writes the order to the default account, carrying
    ``session_id: None`` exactly as that account's own row does. A value writes it to that
    Paper_Session. Either way ``account_id`` names the book the order is placed against, and
    ``user_id`` is on the row.

    Raises:
        PaperConcurrencyConflict: ``uq_paper_order_idem`` (a session order) or
            ``uq_paper_order_idem_default`` (a default-account order) already holds this key. A
            concurrent first request won, and the caller's retry re-probes and finds that order
            rather than inserting a second one (Requirement 16.8).
        PaperPersistenceError: the insert did not complete.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _optional_text(session_id)
    aid = _require_text(account_id, "account_id")
    state = PaperOrderState(order_state)
    key = _validate_idempotency_key(idempotency_key)

    payload: Dict[str, Any] = {
        "session_id": sid,
        "account_id": aid,
        "user_id": uid,
        "symbol": _require_text(symbol, "symbol"),
        "side": _one_of(side, ORDER_SIDES, "side"),
        "order_type": _one_of(order_type, ORDER_TYPES, "order_type"),
        "quantity": _numeric(quantity, "quantity"),
        "limit_price": _optional_numeric(limit_price, "limit_price"),
        "reference_price": _optional_numeric(reference_price, "reference_price"),
        "filled_quantity": _numeric(filled_quantity, "filled_quantity"),
        "avg_fill_price": _optional_numeric(avg_fill_price, "avg_fill_price"),
        "fee_minor": _minor_units(fee_minor, "fee_minor"),
        "slippage_minor": _minor_units(slippage_minor, "slippage_minor"),
        "order_state": state.value,
        "legacy_status": LEGACY_STATUS_FOR_STATE[state],
        "rejection_reason": _optional_text(rejection_reason),
        "idempotency_key": key,
        "signal_id": _optional_text(signal_id),
        "fingerprint": _require_text(fingerprint, "fingerprint"),
    }

    try:
        rows = _execute(
            supabase.table(ORDERS_TABLE).insert(payload), f"{ORDERS_TABLE} insert"
        )
    except PaperPersistenceError as exc:
        # Both index names, because which one refused depends on whether this is a session order
        # or a default-account one. ``uq_paper_order_idem_default`` is checked FIRST: it is the
        # longer name and contains the shorter one, so testing the shorter one first would report
        # the wrong index for a default-account duplicate.
        if key is not None and _is_unique_violation(
            exc.__cause__ or exc, "uq_paper_order_idem_default", "uq_paper_order_idem"
        ):
            raise PaperConcurrencyConflict(
                "an order is already recorded under this idempotency key for this "
                + ("account" if sid is None else "session")
                + ", so no second order was inserted",
                table=ORDERS_TABLE,
            ) from exc
        raise
    return _one(rows, f"{ORDERS_TABLE} insert")


def update_order(
    supabase: Any,
    *,
    user_id: Any,
    order_id: Any,
    order_state: Any,
    expected_state: Any = None,
    filled_quantity: Any = None,
    avg_fill_price: Any = None,
    fee_minor: Any = None,
    slippage_minor: Any = None,
    rejection_reason: Any = None,
) -> Dict[str, Any]:
    """Move one order to ``order_state``, guarded by the state it is expected to be in.

    The other half of :func:`insert_order`, and the reason task 23.2 needed it: an order does not
    stay where it was inserted. A market order is inserted ``CREATED``, accepted, and filled; a
    resting limit order is inserted ``CREATED``, accepted, and later filled or cancelled. Each of
    those is an UPDATE, and without one the retained ``GET /api/paper/orders?status=OPEN`` could
    never stop returning a filled order.

    THE GUARD, AND WHY IT IS THE STATE RATHER THAN A VERSION
    -------------------------------------------------------
    ``paper_orders`` carries no ``version`` column - 009 gives that to ``paper_accounts`` and
    ``paper_positions`` - and it does not need one here, because the state machine is itself the
    guard: ``expected_state`` is applied as ``.eq("order_state", expected)``, so a concurrent
    writer that already moved the order makes this UPDATE match **zero** rows and raise
    :class:`PaperConcurrencyConflict` instead of overwriting a state it never read. That is the
    same disposition :func:`bump_version` takes, expressed in the column that carries the meaning.

    ``LEGACY_STATUS`` IS DERIVED, NEVER PASSED - the reason :func:`insert_order` derives it.
    ``paper_orders.order_state`` and ``paper_orders.legacy_status`` therefore cannot disagree after
    an update either, which is what keeps ``?status=OPEN`` meaning "live on the book" for an order
    that has moved (Requirement 17.12).

    THE TRANSITION IS CHECKED HERE AS WELL AS BY THE DATABASE
    --------------------------------------------------------
    ``can_transition`` is consulted when ``expected_state`` is given, so an illegal transition is
    refused before a statement is issued and the failure names the transition it refused
    (Requirement 16.4) instead of arriving as a trigger's exception.
    ``trg_paper_order_transition_guard`` remains the guarantee.

    Args:
        expected_state: the state the caller read. ``None`` issues the update unguarded, which is
            for a caller that has no prior read to guard with; the transition check is skipped
            with it, because there is no origin to check against.
        filled_quantity, avg_fill_price, fee_minor, slippage_minor, rejection_reason: written only
            when not ``None``. ``None`` leaves the stored value alone rather than nulling it - a
            state transition that erased an order's fill record would lose money history.

    Raises:
        ValueError: ``expected_state -> order_state`` is not one of Requirement 16.2's transitions.
        PaperConcurrencyConflict: the UPDATE matched no row - another writer moved the order first,
            or it does not belong to this identity.
        PaperPersistenceError: the statement did not complete.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    oid = _require_text(order_id, "order_id")
    target = PaperOrderState(order_state)
    origin = None if expected_state is None else PaperOrderState(expected_state)

    if origin is not None and not can_transition(origin, target):
        raise ValueError(
            f"{origin.value} -> {target.value} is not a permitted paper order transition "
            "(Requirement 16.2); nothing was written"
        )

    update: Dict[str, Any] = {
        "order_state": target.value,
        "legacy_status": LEGACY_STATUS_FOR_STATE[target],
        "updated_at": _utc_now(),
    }
    if filled_quantity is not None:
        update["filled_quantity"] = _numeric(filled_quantity, "filled_quantity")
    if avg_fill_price is not None:
        update["avg_fill_price"] = _numeric(avg_fill_price, "avg_fill_price")
    if fee_minor is not None:
        update["fee_minor"] = _minor_units(fee_minor, "fee_minor")
    if slippage_minor is not None:
        update["slippage_minor"] = _minor_units(slippage_minor, "slippage_minor")
    if rejection_reason is not None:
        update["rejection_reason"] = _require_text(rejection_reason, "rejection_reason")

    query = (
        supabase.table(ORDERS_TABLE).update(update).eq("id", oid).eq("user_id", uid)
    )
    if origin is not None:
        query = query.eq("order_state", origin.value)

    rows = _execute(query, f"{ORDERS_TABLE} update")
    if not rows:
        raise PaperConcurrencyConflict(
            f"the paper order update to {target.value} matched no row"
            + (f" at {origin.value}" if origin is not None else "")
            + "; another writer moved it while this change was in flight, so nothing was written",
            table=ORDERS_TABLE,
            row_id=oid,
        )
    return rows[0]


def insert_fill(
    supabase: Any,
    *,
    order_id: Any,
    user_id: Any,
    fill_event_id: Any,
    quantity: Any,
    price: Any,
    fee_minor: Any,
    slippage_minor: Any,
    filled_at: Any,
    session_id: Any = None,
    market_event_id: Any = None,
) -> Dict[str, Any]:
    """Insert one ``paper_fills`` row and return it. Append-only.

    ``uq_paper_fill_event`` is UNIQUE ``(order_id, fill_event_id)``, which is what makes a repeated
    fill event a no-op rather than a second movement of money (Requirements 16.9, 18.13). A
    duplicate raises :class:`PaperDuplicateFill`, so the caller can commit and return the order
    unchanged - task 25.4's second guard - instead of applying the fill twice.

    ``fee_minor`` and ``slippage_minor`` are exact integer Minor_Units. They are recorded on the
    fill, not recomputed from a rate, because Requirement 18.7 makes the equity fall equal to the
    sum of *the fees recorded on the fills* - so the recorded value is the authority.

    ``paper_fills`` carries no ``account_id``; it reaches the account through its order. That is
    009's shape and this function does not add a column to it.

    ``session_id`` is optional and ``None`` means the default account, matching the order this
    fill belongs to. ``uq_paper_fill_event`` keys on ``(order_id, fill_event_id)`` and ``order_id``
    is NOT NULL, so the duplicate-fill guarantee is identical either way - it never mentions
    ``session_id``.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    oid = _require_text(order_id, "order_id")
    event_id = _require_text(fill_event_id, "fill_event_id")

    payload: Dict[str, Any] = {
        "order_id": oid,
        "session_id": _optional_text(session_id),
        "user_id": uid,
        "fill_event_id": event_id,
        "quantity": _numeric(quantity, "quantity"),
        "price": _numeric(price, "price"),
        "fee_minor": _minor_units(fee_minor, "fee_minor"),
        "slippage_minor": _minor_units(slippage_minor, "slippage_minor"),
        "market_event_id": _optional_text(market_event_id),
        "filled_at": _instant(filled_at, "filled_at"),
    }

    try:
        rows = _execute(
            supabase.table(FILLS_TABLE).insert(payload), f"{FILLS_TABLE} insert"
        )
    except PaperPersistenceError as exc:
        if _is_unique_violation(exc.__cause__ or exc, "uq_paper_fill_event"):
            raise PaperDuplicateFill(oid, event_id) from exc
        raise
    return _one(rows, f"{FILLS_TABLE} insert")


def upsert_position(
    supabase: Any,
    *,
    account_id: Any,
    user_id: Any,
    symbol: Any,
    side: Any,
    size: Any,
    entry_price: Any,
    opened_at: Any,
    session_id: Any = None,
    current_price: Any = None,
    unrealized_pnl: Any = None,
    price_at: Any = None,
    closed_at: Any = None,
) -> Dict[str, Any]:
    """Write the account's position in ``symbol``: update the open row, or insert one.

    ``uq_paper_position_open`` is UNIQUE ``(account_id, symbol) WHERE closed_at IS NULL``, so an
    account holds at most one OPEN position per symbol while closed rows accumulate as history.
    This function reads that open row and updates it when there is one, and inserts otherwise -
    which is what "upsert" means here. PostgREST offers ``upsert`` only against a unique index it
    can name in an ``ON CONFLICT``, and a **partial** index cannot be named there, so the
    read-then-write is not a convenience: it is what the partial index leaves available. The
    ``version`` predicate below is what makes the race detectable.

    A FULLY CLOSED POSITION IS ``size = 0`` WITH ``closed_at`` SET
    -------------------------------------------------------------
    Never a deleted row. Requirement 18.5 wants exactly zero, and the existing service's
    ``del user_positions[symbol]`` at a ``0.00000001`` tolerance loses the history as well as the
    precision. Passing ``closed_at`` here is what releases the partial unique index so the symbol
    can be opened again, with the closed row still readable.

    ``session_id`` is optional and ``None`` means the default account. The partial unique index
    keys on ``account_id`` and not on ``session_id``, so the one-open-position-per-symbol rule is
    exactly as strong for the default account as for a session's - which is also why the read
    below narrows by ``account_id`` and never needed ``session_id`` to find the open row.

    Raises:
        PaperConcurrencyConflict: the open row moved between the read and the update.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _optional_text(session_id)
    aid = _require_text(account_id, "account_id")
    sym = _require_text(symbol, "symbol")

    values: Dict[str, Any] = {
        "side": _one_of(side, POSITION_SIDES, "side"),
        "size": _numeric(size, "size"),
        "entry_price": _numeric(entry_price, "entry_price"),
        "current_price": _optional_numeric(current_price, "current_price"),
        "unrealized_pnl": _optional_numeric(unrealized_pnl, "unrealized_pnl"),
        "price_at": _optional_instant(price_at, "price_at"),
        "closed_at": _optional_instant(closed_at, "closed_at"),
    }

    open_rows = _execute(
        supabase.table(POSITIONS_TABLE)
        .select(POSITION_SELECT)
        .eq("user_id", uid)
        .eq("account_id", aid)
        .eq("symbol", sym)
        .is_("closed_at", "null")
        .limit(1),
        f"{POSITIONS_TABLE} read",
    )

    if open_rows:
        current = open_rows[0]
        version = _exact_int(current.get("version", 1), "version")
        update = dict(values)
        update["version"] = version + 1
        update["updated_at"] = _utc_now()
        rows = _execute(
            supabase.table(POSITIONS_TABLE)
            .update(update)
            .eq("id", _require_text(current.get("id"), "position id"))
            .eq("user_id", uid)
            .eq("version", version),
            f"{POSITIONS_TABLE} update",
        )
        if not rows:
            raise PaperConcurrencyConflict(
                f"the paper position update for {sym} matched no row at version {version}; "
                "another writer moved it while this change was in flight",
                table=POSITIONS_TABLE,
                row_id=_optional_text(current.get("id")),
            )
        return rows[0]

    payload: Dict[str, Any] = dict(values)
    payload.update(
        {
            "session_id": sid,
            "account_id": aid,
            "user_id": uid,
            "symbol": sym,
            "opened_at": _instant(opened_at, "opened_at"),
        }
    )
    try:
        rows = _execute(
            supabase.table(POSITIONS_TABLE).insert(payload), f"{POSITIONS_TABLE} insert"
        )
    except PaperPersistenceError as exc:
        if _is_unique_violation(exc.__cause__ or exc, "uq_paper_position_open"):
            raise PaperConcurrencyConflict(
                f"an open paper position in {sym} was created for this account while this one was "
                "in flight, so nothing was inserted",
                table=POSITIONS_TABLE,
            ) from exc
        raise
    return _one(rows, f"{POSITIONS_TABLE} insert")


def insert_balance_event(
    supabase: Any,
    *,
    account_id: Any,
    user_id: Any,
    cause: Any,
    available_delta: Any,
    locked_delta: Any,
    realized_delta: Any,
    available_after: Any,
    locked_after: Any,
    realized_after: Any,
    occurred_at: Any,
    session_id: Any = None,
    fill_id: Any = None,
) -> Dict[str, Any]:
    """Append one ``paper_balance_events`` row and return it.

    The ledger of every balance movement: the three deltas and the three resulting values, so a
    balance can be reconstructed from the events and compared against the account row rather than
    being taken on trust. ``cause`` is one of :data:`BALANCE_EVENT_CAUSES`, which is
    ``chk_paper_balance_event_cause`` verbatim.

    Append-only: ``paper_append_only_guard`` refuses every UPDATE on this table and refuses a
    DELETE while the row's parent still exists - the session for a session row, and, since
    ``013_paper_default_account_children.sql``, the ``paper_accounts`` row for a default-account
    one. So this module offers no update or delete for it. That is deliberate, not an omission.

    ``session_id`` is optional and ``None`` means the default account.
    """
    require_persistence(supabase)
    payload: Dict[str, Any] = {
        "session_id": _optional_text(session_id),
        "account_id": _require_text(account_id, "account_id"),
        "user_id": _require_text(user_id, "user_id"),
        "cause": _one_of(cause, BALANCE_EVENT_CAUSES, "cause"),
        "available_delta": _numeric(available_delta, "available_delta"),
        "locked_delta": _numeric(locked_delta, "locked_delta"),
        "realized_delta": _numeric(realized_delta, "realized_delta"),
        "available_after": _numeric(available_after, "available_after"),
        "locked_after": _numeric(locked_after, "locked_after"),
        "realized_after": _numeric(realized_after, "realized_after"),
        "fill_id": _optional_text(fill_id),
        "occurred_at": _instant(occurred_at, "occurred_at"),
    }
    rows = _execute(
        supabase.table(BALANCE_EVENTS_TABLE).insert(payload),
        f"{BALANCE_EVENTS_TABLE} insert",
    )
    return _one(rows, f"{BALANCE_EVENTS_TABLE} insert")


def insert_trade(
    supabase: Any,
    *,
    account_id: Any,
    user_id: Any,
    symbol: Any,
    side: Any,
    quantity: Any,
    entry_price: Any,
    exit_price: Any,
    realized_pnl: Any,
    fee_minor: Any,
    opened_at: Any,
    closed_at: Any,
    session_id: Any = None,
) -> Dict[str, Any]:
    """Append one closed round-trip to ``paper_trades`` and return it.

    Written when a position reaches size zero (Requirement 18.10), which is what makes
    ``win_rate`` countable and what ``GET /api/paper/trades`` serves. Append-only, for the reason
    :func:`insert_balance_event` is.

    ``side`` is passed through as given: 009 declares **no** check constraint on
    ``paper_trades.side``, unlike ``chk_paper_position_side``, so validating it against ``LONG`` /
    ``SHORT`` here would impose a rule the schema does not hold and would refuse a value a
    correctly-migrated database accepts. The caller writes the side of the position that closed.

    ``session_id`` is optional and ``None`` means the default account, whose closed round-trips are
    what ``GET /api/paper/trades`` and ``TradeHistory.jsx`` read today.
    """
    require_persistence(supabase)
    payload: Dict[str, Any] = {
        "session_id": _optional_text(session_id),
        "account_id": _require_text(account_id, "account_id"),
        "user_id": _require_text(user_id, "user_id"),
        "symbol": _require_text(symbol, "symbol"),
        "side": _require_text(side, "side"),
        "quantity": _numeric(quantity, "quantity"),
        "entry_price": _numeric(entry_price, "entry_price"),
        "exit_price": _numeric(exit_price, "exit_price"),
        "realized_pnl": _numeric(realized_pnl, "realized_pnl"),
        "fee_minor": _minor_units(fee_minor, "fee_minor"),
        "opened_at": _instant(opened_at, "opened_at"),
        "closed_at": _instant(closed_at, "closed_at"),
    }
    rows = _execute(
        supabase.table(TRADES_TABLE).insert(payload), f"{TRADES_TABLE} insert"
    )
    return _one(rows, f"{TRADES_TABLE} insert")


def insert_equity_snapshot(
    supabase: Any,
    *,
    user_id: Any,
    total_equity: Any,
    available_balance: Any,
    locked_balance: Any,
    position_market_value: Any,
    cause: Any,
    taken_at: Any,
    session_id: Any = None,
    series_index: Any = 0,
    stale: bool = False,
) -> Dict[str, Any]:
    """Append one point to the session's equity curve and return it.

    Requirement 18.11 names the five points a snapshot is taken at, and
    :data:`EQUITY_SNAPSHOT_CAUSES` - ``chk_paper_equity_cause`` verbatim - is that list. The
    curve the Paper_Trading_UI draws is these rows, read back by
    :func:`get_equity_snapshots`, and the maximum drawdown of Requirement 18.9 is computed from
    them in order; neither is derived from live event state.

    The four money columns are written as the caller computed them, with no recomputation here.
    ``total_equity`` is ``available + locked + position_market_value`` by
    ``paper_accounting.total_equity``, and recomputing it in this module would be a second
    implementation of the one identity Requirement 18.3 holds to zero tolerance.

    ``stale`` records that a figure was derived from a price that is no longer current
    (Requirement 18.15). It is a fact about the snapshot, so it is stored on it rather than being
    inferred later from a timestamp comparison.

    ``series_index`` distinguishes the series a reset began (Requirement 17.15) from the one
    before it, which is why the pre-reset snapshots stay readable instead of being deleted.

    ``session_id`` is optional and ``None`` means the default account. ``paper_equity_snapshots``
    carries no ``account_id`` - 009 gives it only ``user_id`` and ``session_id`` - so a
    default-account series is identified by ``(user_id, session_id IS NULL, series_index)``. That
    means the default accounts of two CURRENCIES of one user share a series namespace: this module
    stores what it is given and does not invent a discriminator, and the limitation is recorded
    here rather than papered over. A caller holding default accounts in more than one currency must
    keep their series apart with ``series_index``.
    """
    require_persistence(supabase)
    payload: Dict[str, Any] = {
        "session_id": _optional_text(session_id),
        "user_id": _require_text(user_id, "user_id"),
        "series_index": _exact_int(series_index, "series_index"),
        "total_equity": _numeric(total_equity, "total_equity"),
        "available_balance": _numeric(available_balance, "available_balance"),
        "locked_balance": _numeric(locked_balance, "locked_balance"),
        "position_market_value": _numeric(position_market_value, "position_market_value"),
        "stale": bool(stale),
        "cause": _one_of(cause, EQUITY_SNAPSHOT_CAUSES, "cause"),
        "taken_at": _instant(taken_at, "taken_at"),
    }
    rows = _execute(
        supabase.table(EQUITY_SNAPSHOTS_TABLE).insert(payload),
        f"{EQUITY_SNAPSHOTS_TABLE} insert",
    )
    return _one(rows, f"{EQUITY_SNAPSHOTS_TABLE} insert")


def insert_metrics(
    supabase: Any,
    *,
    user_id: Any,
    computed_at: Any,
    session_id: Any = None,
    total_return_pct: Any = None,
    realized_pnl: Any = None,
    unrealized_pnl: Any = None,
    max_drawdown_amount: Any = None,
    max_drawdown_fraction: Any = None,
    win_rate: Any = None,
    closed_trade_count: Any = None,
    order_count: Any = None,
    fill_count: Any = None,
) -> Dict[str, Any]:
    """Append one computed ``paper_metrics`` row and return it. The write half of :func:`get_metrics`.

    Added by task 27.4, which is the first path that has metrics to persist: Requirement 17.8
    requires a stopped session's METRICS committed alongside its orders, positions, balances,
    trades and equity curve, and until now this module could only read the table.

    APPEND, NOT UPDATE, EVEN THOUGH THE TABLE IS MUTABLE
    ---------------------------------------------------
    009 grants ``UPDATE`` on ``paper_metrics`` and places no unique index on ``session_id``, so a
    session may hold several computed rows and :func:`get_metrics` returns the newest by
    ``computed_at``. This function therefore inserts rather than upserting: a metrics row is a
    MEASUREMENT taken at an instant, and overwriting the one a running session computed with the
    one its stop computed would destroy the only record of what the session read like while it ran.
    The append also means a repeated stop attempt cannot lose a figure - it adds one.

    EVERY FIGURE IS OPTIONAL, AND ``None`` MEANS ABSENT RATHER THAN ZERO
    -------------------------------------------------------------------
    All twelve measurement columns are NULLABLE in 009 and that is load-bearing, not lax:
    ``win_rate`` is absent while the closed-trade count is zero (Requirement 18.10) and
    ``max_drawdown_fraction`` is absent when the peak it would be a fraction of is not positive
    (Requirement 18.9). ``None`` is written as ``NULL`` here rather than coerced to ``0``, because
    a zero in either column is a measurement the session did not make (Requirements 28.3, 28.5).
    ``chk_paper_win_rate`` and ``chk_paper_drawdown_fraction`` both admit ``NULL`` for exactly this.

    ``total_equity``, ``position_market_value``, ``margin_usage``, ``stale`` and ``price_at`` are
    deliberately NOT arguments: 009's ``paper_metrics`` has no column for any of them, so a caller
    holding a :class:`~paper_accounting.SessionMetrics` passes the twelve this table stores and
    carries the rest on the Paper_Channel frame. Writing a column the table does not have would be
    the ``42703`` the column contract exists to catch.

    ``session_id`` is optional and ``None`` means the default account, for the reason every other
    write here takes it that way.
    """
    require_persistence(supabase)
    payload: Dict[str, Any] = {
        "session_id": _optional_text(session_id),
        "user_id": _require_text(user_id, "user_id"),
        "total_return_pct": _optional_numeric(total_return_pct, "total_return_pct"),
        "realized_pnl": _optional_numeric(realized_pnl, "realized_pnl"),
        "unrealized_pnl": _optional_numeric(unrealized_pnl, "unrealized_pnl"),
        "max_drawdown_amount": _optional_numeric(
            max_drawdown_amount, "max_drawdown_amount"
        ),
        "max_drawdown_fraction": _optional_numeric(
            max_drawdown_fraction, "max_drawdown_fraction"
        ),
        "win_rate": _optional_numeric(win_rate, "win_rate"),
        "closed_trade_count": (
            None
            if closed_trade_count is None
            else _exact_int(closed_trade_count, "closed_trade_count")
        ),
        "order_count": (
            None if order_count is None else _exact_int(order_count, "order_count")
        ),
        "fill_count": (
            None if fill_count is None else _exact_int(fill_count, "fill_count")
        ),
        "computed_at": _instant(computed_at, "computed_at"),
    }
    rows = _execute(
        supabase.table(METRICS_TABLE).insert(payload), f"{METRICS_TABLE} insert"
    )
    return _one(rows, f"{METRICS_TABLE} insert")


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION AND ITS TWO LOGS (Requirements 14.3, 14.5, 14.6, 14.7, 15.5)
# ══════════════════════════════════════════════════════════════════════════
#
# Added by task 24.x, for ``paper_market_feed``. Three tables, all session-scoped, none with a
# default-account form (see :data:`SESSIONS_TABLE`). Everything the eight accounting functions above
# hold to holds here too: ``user_id`` is a predicate on every statement, no ``select("*")``, a
# statement that did not complete raises rather than answering "no rows", and no value is
# recomputed on the way in.


def _jsonb(value: Any, column: str) -> Dict[str, Any]:
    """A ``JSONB`` payload, with every value checked for representability. Never a ``float``.

    ``paper_market_events.payload`` is the replay input of Requirement 15.5, and
    ``paper_events.payload`` is what the Paper_Channel replays. Both therefore have to survive a
    round trip **exactly**, and a JSON number does not: ``json`` renders a ``float`` at
    ``repr`` precision and a driver reading it back produces a binary float, so a close price
    stored as a JSON number is a price the replay cannot reproduce. Requirement 18.1 forbids the
    arithmetic that would follow.

    So: a ``Decimal`` is converted to its **exact decimal string** and stored as a string, and a
    ``float`` is refused outright rather than converted - by the time a ``float`` reaches here the
    exactness is already gone and ``str(0.07)`` would persist a value the caller never had. Ints,
    bools, strings, ``None`` and nested mappings and sequences of those pass through.
    """
    def _convert(item: Any, where: str) -> Any:
        if isinstance(item, bool) or item is None or isinstance(item, (str, int)):
            return item
        if isinstance(item, float):
            raise ValueError(
                f"{where} is a float ({item!r}); a JSONB payload that a replay must reproduce "
                f"exactly cannot carry one (Requirement 18.1). Pass a Decimal or an exact "
                f"decimal string."
            )
        if isinstance(item, Decimal):
            return str(to_decimal(item, where))
        if isinstance(item, datetime):
            return _instant(item, where)
        if isinstance(item, Enum):
            return _convert(item.value, where)
        if isinstance(item, Mapping):
            return {str(key): _convert(val, f"{where}.{key}") for key, val in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            return [_convert(val, f"{where}[{index}]") for index, val in enumerate(item)]
        raise ValueError(
            f"{where} is a {type(item).__name__}, which has no JSON representation this "
            f"module will guess at"
        )

    if not isinstance(value, Mapping):
        raise ValueError(f"{column} must be a mapping, got {type(value).__name__}")
    return {str(key): _convert(val, f"{column}.{key}") for key, val in value.items()}


def _sequence_value(value: Any, column: str) -> int:
    """A log ``sequence`` value, refused below 1.

    ``chk_paper_market_event_sequence`` and ``chk_paper_event_sequence`` are both
    ``CHECK (sequence >= 1)``, so a log starts at 1 and not at 0. Checked here so the
    caller-facing failure names the column rather than arriving as a ``23514`` the error handler
    has to redact; the constraint remains the guarantee.
    """
    number = _exact_int(value, column)
    if number < 1:
        raise ValueError(
            f"{column} must be at least 1 - both paper log tables declare "
            f"CHECK (sequence >= 1), so a log starts at 1 and not at 0 - got {number}"
        )
    return number


# ──────────────────────────────────────────────────────────────────────────
# ``paper_sessions`` WRITE NOTIFICATIONS (task 26.5)
# ──────────────────────────────────────────────────────────────────────────
#
# Task 26.5 caches ``paper_sessions.user_id`` for five seconds per session and requires that cache
# to be "invalidated on any ``paper_sessions`` write". This module is the only one in the package
# that issues statements, so the notification belongs here rather than at each caller: a cache
# invalidated by whoever remembered to call the invalidator is a cache that goes stale on the write
# somebody forgot about, and the value it holds is an authorisation decision.
#
# A listener list rather than a direct import of ``paper_channel``: ``paper_channel`` imports this
# module at module scope, so an import back would be circular, and a repository that knew about a
# WebSocket registry would be the wrong direction anyway.

#: Callables invoked with the session id after a statement that MAY have changed a
#: ``paper_sessions`` row. Registered by :func:`paper_channel.install_session_write_invalidation`.
_SESSION_WRITE_LISTENERS: List[Any] = []


def on_session_write(listener: Any) -> None:
    """Register ``listener(session_id)`` to run after every ``paper_sessions`` write.

    Idempotent by identity, so a module that registers on import can be re-imported without
    stacking listeners.
    """
    if listener not in _SESSION_WRITE_LISTENERS:
        _SESSION_WRITE_LISTENERS.append(listener)


def clear_session_write_listeners() -> None:
    """Forget every listener. For tests, which must not leak one into the next module."""
    _SESSION_WRITE_LISTENERS.clear()


def _note_session_write(session_id: Optional[str]) -> None:
    """Tell every listener that ``session_id``'s row may have moved.

    A listener that raises is swallowed and logged: a cache invalidation is a best-effort
    notification, and a write that has already succeeded must not be reported as a failure because
    something downstream of it could not update a dictionary. The consequence of a swallowed
    failure is bounded and stated: the cached owner keeps its five-second life, which is the
    staleness the design already accepts.
    """
    for listener in list(_SESSION_WRITE_LISTENERS):
        try:
            listener(session_id)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning(
                "[Paper] a paper_sessions write listener failed for session %r (%s); the cached "
                "owner keeps its five-second life",
                session_id,
                exc,
            )


def read_session(supabase: Any, user_id: Any, session_id: Any) -> Optional[Dict[str, Any]]:
    """One ``paper_sessions`` row this user owns, or ``None``.

    ``None`` means the read completed and matched nothing - this user has no session under that
    id, which is the same answer for "no such session" and "another user's session" **by design**:
    ``user_id`` is a predicate, so another tenant's session is never fetched rather than merely
    never returned (Requirements 21.2, 21.5).

    The projection is :data:`SESSION_FEED_SELECT`, which is what the feed and the simulator's feed
    gate need. It is deliberately not the whole row: ``config`` is the frozen session configuration
    and reading it on every feed transition would carry the Protected_Logic-adjacent payload through
    a path that has no use for it.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .select(SESSION_FEED_SELECT)
        .eq("user_id", uid)
        .eq("id", sid),
        f"{SESSIONS_TABLE} read",
    )
    for row in rows:
        # The predicates are restated in the process, as every read in this module does: a
        # Persistence_Layer double, or a lost filter, must not be able to hand back a session
        # belonging to someone else.
        if str(row.get("user_id")) == uid and str(row.get("id")) == sid:
            return row
    return None


def list_sessions(
    supabase: Any,
    user_id: Any,
    *,
    session_state: Any = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The caller's own Paper_Sessions, newest first, capped. Task 28.1.

    ``user_id`` is a PREDICATE ON THE STATEMENT, which is the whole of Requirement 21.5: another
    tenant's session is never fetched rather than fetched and filtered out afterwards. There is no
    post-retrieval filter here that could be removed to change that - the in-process restatement
    below is a second check on the same predicate, not the only one.

    ``session_state``, when supplied, is checked against :data:`SESSION_STATES` -
    ``chk_paper_session_state`` verbatim - so an unrecognised label is a named ``ValueError`` here
    rather than a statement that quietly matches nothing.

    ``limit`` defaults to and is CLAMPED to :data:`SESSION_LIST_CAP`, in the statement and again in
    the process, for the reason :func:`read_session_events` gives: the statement's limit keeps the
    rows off the wire and the in-process truncation keeps the cap true if a transport or a double
    ignores it.

    ``[]`` means the read completed and this user owns no matching session. A read that did not
    complete raises: answering "you have no sessions" for a failed statement would tell a user their
    history is gone.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    state = None if session_state is None else _one_of(
        session_state, SESSION_STATES, "session_state"
    )

    cap = SESSION_LIST_CAP if limit is None else _exact_int(limit, "limit")
    if cap < 1:
        raise ValueError(f"limit must be at least 1, got {cap}")
    cap = min(cap, SESSION_LIST_CAP)

    query = (
        supabase.table(SESSIONS_TABLE).select(SESSION_LIST_SELECT).eq("user_id", uid)
    )
    if state is not None:
        query = query.eq("session_state", state)
    rows = _execute(
        query.order("created_at", desc=True).limit(cap),
        f"{SESSIONS_TABLE} list read",
    )

    matched: List[Dict[str, Any]] = []
    for row in rows:
        # The predicates restated in the process, as every read in this module does: a
        # Persistence_Layer double, or a lost filter, must not be able to hand back another
        # user's session.
        if str(row.get("user_id")) != uid:
            continue
        if state is not None and str(row.get("session_state")) != state:
            continue
        matched.append(row)
    return matched[:cap]


def read_session_summary(
    supabase: Any, user_id: Any, session_id: Any
) -> Optional[Dict[str, Any]]:
    """The CLIENT projection of one session this user owns, or ``None``. Task 28.1.

    :data:`SESSION_LIST_SELECT` for a single id - the fourth narrow projection's other half.
    :func:`list_sessions` serves ``GET /api/paper/sessions`` from that constant and this serves
    ``GET /api/paper/sessions/{id}`` from the same one, which is what makes the list element and the
    detail body the same shape rather than two shapes a client has to reconcile. Requirement 17.3's
    ``feed_state``, ``market_data_source`` and ``event_sequence`` travel on it, and ``config``,
    ``version_id`` and ``source_strategy_id`` deliberately do not - see that constant for why.

    Deliberately NOT a widening of :func:`read_session`: that projection is what the feed and the
    simulator's feed gate read on every accepted candle, and carrying the recorded capital and the
    lifecycle timestamps there would put columns no per-event path uses on a per-event statement.

    ``None`` means the read completed and matched nothing - the same answer for "no such session"
    and "another user's session" (Requirements 21.2, 21.5, 21.4), because ``user_id`` is a
    PREDICATE and another tenant's row is never fetched.

    Raises:
        PaperPersistenceError: the statement DID NOT COMPLETE. Never reported as "no session":
            answering 404 for a failed read would tell a caller their session is gone.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .select(SESSION_LIST_SELECT)
        .eq("user_id", uid)
        .eq("id", sid),
        f"{SESSIONS_TABLE} summary read",
    )
    for row in rows:
        # The predicates restated in the process, as every read here does: a Persistence_Layer
        # double, or a lost filter, must not be able to hand back another user's session.
        if str(row.get("user_id")) == uid and str(row.get("id")) == sid:
            return row
    return None


def read_session_owner(supabase: Any, session_id: Any) -> Optional[str]:
    """The user id recorded on ``paper_sessions.id = session_id``, or ``None``.

    Task 26.5. Requirement 21.7's "re-derive the owning user of the subscribed Paper_Session from
    the Persistence_Layer before emitting each event".

    **Why this is the one read in this module with no ``user_id`` predicate, and why that is not a
    weakening.** Every other read here scopes on ``user_id`` so another tenant's row is never
    fetched. This one cannot: its whole purpose is to detect that the recorded owner is *not* the
    identity the subscription was authorised under, and a statement filtered by that identity can
    only ever return the row when they already agree. Scoped, the comparison would be a tautology
    and Requirement 21.7 would be unimplemented.

    What keeps that safe is what the value is used for. It is compared, and the only outcomes are
    "keep delivering to a subscription that was already authorised" and "stop delivering and close
    it". The owner id is never returned to a client, never logged at info level and never placed in
    a frame - :data:`SESSION_OWNER_SELECT` is two columns so there is nothing else to leak - so the
    check can only ADD refusals to the ones ``authorize_channel_subscription`` already made
    (Requirement 21.4 is untouched: this function is never reached by a subscriber who was not
    already admitted).

    ``None`` means "no owner could be established", which is a REFUSAL at the call site and not an
    allow: the session may not exist, may have been deleted, or - under a non-service client whose
    owner-scoped RLS policy hides it - may simply be invisible. All three fail closed, which is the
    same direction ``websocket_auth._resolve_owned_channel_owner`` fails in for the same question.
    """
    require_persistence(supabase)
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .select(SESSION_OWNER_SELECT)
        .eq("id", sid)
        .limit(1),
        f"{SESSIONS_TABLE} owner read",
    )
    for row in rows:
        if str(row.get("id")) != sid:
            continue
        owner = row.get("user_id")
        text = "" if owner is None else str(owner).strip()
        return text or None
    return None


#: Requirement 19.8's replay ceiling, as ``design.md`` states it: at most this many rows per replay
#: request. ``paper_events`` retains everything, so a client that has been away long enough is
#: served the oldest 5000 it is missing and told there is more
#: (:class:`SessionEventPage.truncated`) rather than being handed an unbounded result set that
#: would have to be buffered in one frame.
SESSION_EVENT_REPLAY_CAP = 5000


def read_session_events(
    supabase: Any,
    user_id: Any,
    session_id: Any,
    *,
    after_sequence: Any = 0,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The session's Paper_Channel log above ``after_sequence``, oldest first, capped.

    Task 26.4's replay read (Requirements 19.8, 19.9). ``paper_events`` IS the buffer: it retains
    every event, so "at least the most recent 1000 events for at least 5 minutes" is met with
    margin and survives a restart, which an in-memory ring would not.

    ``sequence > after_sequence ORDER BY sequence ASC``, which is what
    ``idx_paper_events (session_id, sequence ASC)`` indexes, so the rows arrive in the order the
    client has to apply them.

    ``limit`` defaults to :data:`SESSION_EVENT_REPLAY_CAP` and is CLAMPED to it - a caller cannot
    ask for more, and the returned list is truncated in the process as well as limited in the
    statement. Both, deliberately: the statement's ``limit`` is what keeps the row set off the wire,
    and the in-process truncation is what keeps the cap true if a transport or a double ignores it.

    ``user_id`` is a predicate, as it is on every read in this module, so another tenant's session
    log is never fetched rather than merely never returned (Requirements 21.2, 21.5). ``[]`` means
    the read completed and there is nothing above ``after_sequence``; a read that did not complete
    raises, because answering "no history" for a broken statement would let a client believe it is
    up to date.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")
    floor = _exact_int(after_sequence, "after_sequence")
    if floor < 0:
        raise ValueError(
            f"after_sequence must be at least 0 - a Paper_Channel sequence starts at 1, so 0 is "
            f"'replay everything' and a negative value is an unrecoverable gap the caller must "
            f"report as HISTORY_INCOMPLETE rather than read past (Requirement 19.9) - got {floor}"
        )

    cap = SESSION_EVENT_REPLAY_CAP if limit is None else _exact_int(limit, "limit")
    if cap < 1:
        raise ValueError(f"limit must be at least 1, got {cap}")
    cap = min(cap, SESSION_EVENT_REPLAY_CAP)

    rows = _execute(
        supabase.table(EVENTS_TABLE)
        .select(EVENT_SELECT)
        .eq("user_id", uid)
        .eq("session_id", sid)
        .gt("sequence", floor)
        .order("sequence", desc=False)
        .limit(cap),
        f"{EVENTS_TABLE} replay read",
    )

    matched: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("user_id")) != uid or str(row.get("session_id")) != sid:
            continue
        try:
            if _exact_int(row.get("sequence"), "sequence") <= floor:
                continue
        except ValueError:
            continue
        matched.append(row)

    matched.sort(key=lambda row: _exact_int(row.get("sequence"), "sequence"))
    return matched[:cap]


def read_session_config(
    supabase: Any, user_id: Any, session_id: Any
) -> Optional[Dict[str, Any]]:
    """The frozen ``paper_sessions.config`` of one session this user owns, or ``None``.

    ``None`` means the read completed and matched nothing - no session of that id belongs to this
    user, which is deliberately the same answer for "no such session" and "another user's
    session" (Requirements 21.2, 21.5), exactly as :func:`read_session` gives it.

    A separate function and a separate projection (:data:`SESSION_CONFIG_SELECT`) rather than a
    widening of :func:`read_session`, because the two reads serve paths that need different
    things: the feed transitions on every accepted market event and has no use for the
    configuration, while the simulator reads the configuration once and does not care about the
    transport. Carrying ``config`` through the feed path would put the whole frozen payload on a
    statement issued per candle.

    ``currency`` travels with it because ``config["minor_unit_exponent"]`` is only meaningful
    against the account's currency, and a caller that has one without the other cannot check that
    they still agree.

    Raises:
        PaperPersistenceError: The statement DID NOT COMPLETE. Never reported as "no config": a
            session with no readable configuration must not trade, and a caller that read ``{}``
            would apply a zero fee rate to real recorded fills (Requirements 16.12, 28.3).
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .select(SESSION_CONFIG_SELECT)
        .eq("user_id", uid)
        .eq("id", sid),
        f"{SESSIONS_TABLE} config read",
    )
    for row in rows:
        # The predicates restated in the process, as every read here does.
        if str(row.get("user_id")) == uid and str(row.get("id")) == sid:
            return row
    return None


def read_session_lifecycle(
    supabase: Any, user_id: Any, session_id: Any
) -> Optional[Dict[str, Any]]:
    """The stop/reset projection of one session this user owns, or ``None``. Task 27.4.

    :data:`SESSION_LIFECYCLE_SELECT` in one statement: the recorded ``initial_capital_minor``, the
    ``currency`` it is denominated in, the frozen ``config``, and the state the caller is about to
    gate on. One read rather than three, because the stop and reset paths need all of them before
    they issue their first write and a path that read them separately could act on a session whose
    state moved between two of the reads.

    ``None`` means the read completed and matched nothing - no session of that id belongs to this
    user, deliberately the same answer for "no such session" and "another user's session"
    (Requirements 21.2, 21.5), exactly as :func:`read_session` and :func:`read_session_config` give
    it. ``user_id`` is a PREDICATE, so another tenant's row is never fetched.

    Raises:
        PaperPersistenceError: the statement DID NOT COMPLETE. Never reported as "no session": a
            stop that read ``None`` because a statement failed would release a subscription it had
            not persisted the finals for.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .select(SESSION_LIFECYCLE_SELECT)
        .eq("user_id", uid)
        .eq("id", sid),
        f"{SESSIONS_TABLE} lifecycle read",
    )
    for row in rows:
        # The predicates restated in the process, as every read here does.
        if str(row.get("user_id")) == uid and str(row.get("id")) == sid:
            return row
    return None


def session_config_payload(config: Any) -> Dict[str, Any]:
    """One ``paper_sessions.config`` value, checked and ready for the session INSERT.

    THE ONLY WRITE PATH FOR ``config``, AND THERE IS NO UPDATE PATH
    --------------------------------------------------------------
    Requirement 16.12 captures the session configuration at start and keeps it unchanged for the
    session's lifetime. Three things hold that, and this function is the second:

    1. ``paper_simulator.SessionConfig`` is frozen, so an in-memory copy cannot drift.
    2. This module exposes this serialiser and :func:`read_session_config`, and **no** function
       that updates ``config``. :func:`update_session_feed` - the only UPDATE against
       ``paper_sessions`` anywhere in the backend - writes three named feed columns and
       ``updated_at``, and cannot be asked to carry a fourth: an argument it does not have is not
       a payload key it can set.
    3. ``trg_paper_session_config_immutable`` (``009_paper_trading.sql`` section 14d) is a
       ``BEFORE UPDATE ... FOR EACH ROW`` trigger on ``paper_sessions`` raising ``23514`` when
       ``NEW.config IS DISTINCT FROM OLD.config``. That is the guarantee, because it holds against
       a direct ``psql`` session and a future handler as well as against this module.

    So a mid-session fee change is unrepresentable rather than discouraged.

    The payload goes through :func:`_jsonb`, which refuses a ``float`` outright and renders a
    ``Decimal`` as its exact decimal string - the reason every rate and quantity in the
    configuration is a string in storage: a JSON number cannot be read back exactly, and a fee
    rate the session cannot reproduce makes every fill it priced unreproducible with it
    (Requirements 18.1, 15.4).

    Args:
        config: A ``paper_simulator.SessionConfig``, or the mapping its ``to_jsonb`` produces.
            Accepted both ways so the session-start service of task 26.x can hand over the value
            object it built, while a test or a migration-time fixture can hand over the payload.

    Returns:
        The mapping to place under ``"config"`` in the ``paper_sessions`` INSERT.

    Raises:
        ValueError: If ``config`` is neither a mapping nor an object with ``to_jsonb``, or carries
            a value with no exact JSON representation.
    """
    if hasattr(config, "to_jsonb"):
        payload = config.to_jsonb()
    else:
        payload = config
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"{SESSIONS_TABLE}.config must be a SessionConfig or the mapping its to_jsonb() "
            f"produces, got {type(config).__name__}"
        )
    if not payload:
        raise ValueError(
            f"{SESSIONS_TABLE}.config must not be empty; the column is NOT NULL and a session "
            "with no recorded configuration cannot apply a fee, a slippage or a precision "
            "(Requirement 16.12)"
        )
    return _jsonb(payload, f"{SESSIONS_TABLE}.config")


def update_session_feed(
    supabase: Any,
    *,
    user_id: Any,
    session_id: Any,
    feed_state: Any = None,
    feed_transport: Any = None,
    market_data_source: Any = None,
) -> Dict[str, Any]:
    """Write the session's feed columns and return the updated row.

    The three columns Requirement 14 records on a Paper_Session and nothing else:

    * ``market_data_source`` - the identity ``choose_market_data_source`` selected
      (Requirement 14.3). Written once, at :func:`~paper_market_feed.open_feed`.
    * ``feed_transport`` - ``WEBSOCKET``, ``REST`` or ``UNKNOWN``, observed from the ``transport``
      field ``mds/main.py``'s payload carries (Requirement 14.6).
    * ``feed_state`` - the health record Requirements 14.5, 14.6 and 18.15 read.

    ``paper_sessions`` is **not** append-only - ``trg_paper_events_append_only`` covers the two logs,
    not the session row - so this is a plain guarded UPDATE. It carries no ``version`` predicate,
    because these three columns have a single writer (the session's own feed loop) and a version
    guard whose conflict nobody could resolve would turn a feed transition into an error. The
    ``user_id`` and ``id`` predicates are what keep it the caller's own row.

    An argument left ``None`` is **not written**, so a caller that is recording only a state
    transition does not blank the transport it observed a moment ago. A call that names nothing is a
    programming error and is refused rather than issuing an UPDATE with an empty payload.

    Raises:
        PaperConcurrencyConflict: the statement matched no row - the session does not exist, or
            does not belong to this user. Reported as a conflict rather than answered with
            ``None`` because the caller believed it had a session and now has evidence it does not.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    values: Dict[str, Any] = {}
    if feed_state is not None:
        values["feed_state"] = _require_text(feed_state, "feed_state")
    if feed_transport is not None:
        values["feed_transport"] = _require_text(feed_transport, "feed_transport")
    if market_data_source is not None:
        values["market_data_source"] = _require_text(
            market_data_source, "market_data_source"
        )
    if not values:
        raise ValueError(
            "update_session_feed was asked to write nothing; name at least one of "
            "feed_state, feed_transport or market_data_source"
        )
    values["updated_at"] = _utc_now()

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .update(values)
        .eq("id", sid)
        .eq("user_id", uid),
        f"{SESSIONS_TABLE} feed update",
    )
    if not rows:
        raise PaperConcurrencyConflict(
            f"the {SESSIONS_TABLE} feed update matched no row for session {sid!r}; the "
            f"session does not exist or is not this user's",
            table=SESSIONS_TABLE,
            row_id=sid,
        )
    _note_session_write(sid)
    return rows[0]


def next_market_event_sequence(supabase: Any, user_id: Any, session_id: Any) -> int:
    """The sequence the session's next market event takes: ``max(sequence) + 1``, or 1.

    Read rather than counted in process, because a session that resumes after a restart has to
    continue its log rather than start a second one at 1 - and ``uq_paper_market_event`` would not
    catch that, since it keys on ``source_event_id`` and a genuinely new candle carries a new one.
    ``chk_paper_market_event_sequence`` is ``>= 1``, so an empty log answers 1 and not 0.

    Two writers on one session can read the same number here. That is a real race and it is
    resolved by the database, not by this read: ``idx_paper_market_events`` is not unique on
    ``sequence``, so the second write does not fail - which means the ordering guarantee this
    number carries is only as strong as the single-writer-per-session property the session worker
    holds. Recorded as a limitation rather than papered over: the *identity* guarantee
    (``uq_paper_market_event``) is the database's and holds regardless.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(MARKET_EVENTS_TABLE)
        .select(MARKET_EVENT_SELECT)
        .eq("user_id", uid)
        .eq("session_id", sid)
        .order("sequence", desc=True)
        .limit(1),
        f"{MARKET_EVENTS_TABLE} sequence read",
    )
    highest = 0
    for row in rows:
        if str(row.get("user_id")) != uid or str(row.get("session_id")) != sid:
            continue
        try:
            highest = max(highest, _exact_int(row.get("sequence"), "sequence"))
        except ValueError:
            continue
    return highest + 1


def insert_market_event(
    supabase: Any,
    *,
    session_id: Any,
    user_id: Any,
    sequence: Any,
    source_event_id: Any,
    symbol: Any,
    timeframe: Any,
    event_timestamp: Any,
    payload: Any,
    received_at: Any = None,
    latency_ms: Any = None,
) -> Dict[str, Any]:
    """Append one validated market-data event to ``paper_market_events``. Append-only.

    This row is what makes ``paper_replay.replay`` possible: Requirement 15.5 asks the session to
    "record the market-data event sequence sufficient to reproduce its order and accounting
    history", and ``payload`` plus ``event_timestamp`` plus ``sequence`` is that record.

    ``uq_paper_market_event`` is UNIQUE ``(session_id, source_event_id)``, and a violation raises
    :class:`PaperDuplicateMarketEvent` so the caller can treat the second arrival as the no-op
    Requirement 14.7 asks for. That constraint - not the caller's LRU - is the durable arbiter.

    ``payload`` goes through :func:`_jsonb`, which stores a ``Decimal`` as its exact decimal string
    and refuses a ``float``: a close price rendered as a JSON number could not be read back exactly,
    and a replay priced from an inexact close is not a replay (Requirements 15.4, 18.1).

    ``latency_ms`` is Requirement 14.10's delivery measurement, ``NUMERIC(10,3)``. ``None`` when it
    could not be measured, which is different from zero and is stored as different.

    ``received_at`` defaults to 009's ``NOW()`` when not passed; a caller that measured the
    arrival instant passes it so the stored figure is the measurement rather than the insert time.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")
    event_id = _require_text(source_event_id, "source_event_id")

    row: Dict[str, Any] = {
        "session_id": sid,
        "user_id": uid,
        "sequence": _sequence_value(sequence, "sequence"),
        "source_event_id": event_id,
        "symbol": _require_text(symbol, "symbol"),
        "timeframe": _require_text(timeframe, "timeframe"),
        "event_timestamp": _instant(event_timestamp, "event_timestamp"),
        "payload": _jsonb(payload, "payload"),
        "latency_ms": _optional_numeric(latency_ms, "latency_ms"),
    }
    if received_at is not None:
        row["received_at"] = _instant(received_at, "received_at")

    try:
        rows = _execute(
            supabase.table(MARKET_EVENTS_TABLE).insert(row),
            f"{MARKET_EVENTS_TABLE} insert",
        )
    except PaperPersistenceError as exc:
        if _is_unique_violation(exc.__cause__ or exc, "uq_paper_market_event"):
            raise PaperDuplicateMarketEvent(sid, event_id) from exc
        raise
    return _one(rows, f"{MARKET_EVENTS_TABLE} insert")


def get_market_events(
    supabase: Any,
    user_id: Any,
    session_id: Any,
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The session's market-data log, oldest first. The replay read (Requirements 15.4, 15.5).

    Ordered by ``sequence`` ascending, which is what ``idx_paper_market_events`` indexes, so the
    rows arrive in the order the session processed them and ``paper_replay`` can drive its ledger
    from them directly.

    ``[]`` means the read completed and the session has recorded no market event yet.
    :class:`PaperPersistenceError` is the other outcome and is not the same one - answering "no
    events" for a broken read would let a replay report a session that never traded.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    query = (
        supabase.table(MARKET_EVENTS_TABLE)
        .select(MARKET_EVENT_SELECT)
        .eq("user_id", uid)
        .eq("session_id", sid)
        .order("sequence", desc=False)
    )
    if limit is not None:
        query = query.limit(_exact_int(limit, "limit"))

    rows = _execute(query, f"{MARKET_EVENTS_TABLE} read")
    return [
        row
        for row in rows
        if str(row.get("user_id")) == uid and str(row.get("session_id")) == sid
    ]


#: How many times :func:`allocate_session_event_sequence` attempts its compare-and-swap before
#: reporting the contention. Three, the figure ``paper_simulator.RETRY_ATTEMPTS`` uses for the same
#: kind of loss. No sleep and no jitter between attempts: a randomised delay would be a ``random``
#: draw inside ``backend_app/backend/paper/`` (forbidden outright by Requirement 18.1 and
#: ``tests/test_paper_no_random.py``), and a re-read of one integer costs one request.
SEQUENCE_ALLOCATION_ATTEMPTS = 3


def allocate_session_event_sequence(
    supabase: Any,
    user_id: Any,
    session_id: Any,
    *,
    attempts: int = SEQUENCE_ALLOCATION_ATTEMPTS,
) -> int:
    """Advance ``paper_sessions.event_sequence`` by one and return the number allocated.

    Task 26.2's allocator. ``design.md`` writes it as

        UPDATE paper_sessions SET event_sequence = event_sequence + 1 WHERE id = :id
        RETURNING event_sequence

    inside the emitting transaction, with the row lock making the counter contiguous across
    instances. **PostgREST has none of the three pieces that needs** - no ``BEGIN``, no
    ``FOR UPDATE``, and no server-side expression in a ``PATCH`` (a column is set to a LITERAL, so
    ``event_sequence + 1`` is not expressible; ``Prefer: return=representation`` gives back the
    updated row, which is the *reading* half of ``RETURNING`` and not the arithmetic half). The same
    condition :func:`lock_account_for_update` records for the money path.

    So this is the same optimistic protocol, applied to a counter instead of a balance:

      1. read ``event_sequence`` -> N, with ``id`` and ``user_id`` as predicates
      2. ``UPDATE ... SET event_sequence = N + 1 WHERE id = :id AND user_id = :uid AND
         event_sequence = N`` - the read value is a PREDICATE, so a writer that moved the row in
         between makes the statement match zero rows
      3. zero rows -> re-read and retry. A loser never reuses the number it read, which is what
         keeps two allocations from returning the same value.

    **The gap this does not close.** The allocation and the ``paper_events`` INSERT that consumes it
    are two requests with no transaction around them, so a process that dies between them burns a
    number and leaves a HOLE in the session's sequence. What cannot happen is two events at one
    sequence: ``uq_paper_event_seq UNIQUE (session_id, sequence)`` refuses the second INSERT and
    :func:`insert_session_event` raises :class:`PaperDuplicateSessionEvent`, which the caller
    retries with a fresh allocation. Closing the gap needs the allocation and the INSERT inside one
    database function (``rpc``); that is a deployment change and is recorded rather than glossed.

    ``event_sequence`` absent or ``NULL`` is read as 0, so the first allocation is 1 -
    ``chk_paper_event_sequence`` is ``>= 1`` and a log starts at 1.

    Raises:
        PaperPersistenceError: no session is readable under that id for this identity. The same
            answer for "no such session" and "another user's session", because ``user_id`` is a
            predicate and another tenant's row is never fetched.
        PaperConcurrencyConflict: the swap lost ``attempts`` times. Nothing is written and no number
            is returned, so a caller emits nothing rather than emitting at a sequence it does not
            own.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")
    total = _exact_int(attempts, "attempts")
    if total < 1:
        raise ValueError(f"attempts must be at least 1, got {total}")

    for _attempt in range(total):
        rows = _execute(
            supabase.table(SESSIONS_TABLE)
            .select(SESSION_FEED_SELECT)
            .eq("id", sid)
            .eq("user_id", uid)
            .limit(1),
            f"{SESSIONS_TABLE} event_sequence read",
        )
        row = rows[0] if rows else None
        if row is None:
            raise PaperPersistenceError(
                f"no {SESSIONS_TABLE} row is readable for session {sid!r} under this identity, so "
                f"no Paper_Channel sequence can be allocated; nothing was written"
            )

        current = row.get("event_sequence")
        held = 0 if current is None else _exact_int(current, "event_sequence")
        allocated = held + 1

        updated = _execute(
            supabase.table(SESSIONS_TABLE)
            .update({"event_sequence": allocated, "updated_at": _utc_now()})
            .eq("id", sid)
            .eq("user_id", uid)
            .eq("event_sequence", held),
            f"{SESSIONS_TABLE} event_sequence allocation",
        )
        if updated:
            _note_session_write(sid)
            return allocated

    raise PaperConcurrencyConflict(
        f"the {SESSIONS_TABLE} Paper_Channel sequence allocation for session {sid!r} lost its "
        f"compare-and-swap {total} times; no sequence was allocated and nothing was written",
        table=SESSIONS_TABLE,
        row_id=sid,
    )


def next_session_event_sequence(supabase: Any, user_id: Any, session_id: Any) -> int:
    """The sequence the session's next Paper_Channel event takes: ``max(sequence) + 1``, or 1.

    The ``paper_events`` counterpart of :func:`next_market_event_sequence`, and it exists for the
    same reason. Here the race the docstring there records is **not** silent:
    ``uq_paper_event_seq`` is UNIQUE ``(session_id, sequence)``, so a second writer that read the
    same number is refused by the database and :func:`insert_session_event` raises
    :class:`PaperDuplicateSessionEvent`.

    **Task 26.2 relationship.** This function reads the LOG (``max(paper_events.sequence) + 1``);
    :func:`allocate_session_event_sequence` advances the COUNTER
    (``paper_sessions.event_sequence``) and is what the Paper_Channel emitter uses. Both are kept:
    task 24.4's feed writer calls this one and its ``paper_error`` record stands on its own, while
    the general emitter needs an allocation that a concurrent writer cannot duplicate. The two agree
    whenever every event of a session went through the allocator, and where they disagree the
    counter is the authority - the log can only be behind it, never ahead, because a number is
    allocated before it is written.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")

    rows = _execute(
        supabase.table(EVENTS_TABLE)
        .select(EVENT_SELECT)
        .eq("user_id", uid)
        .eq("session_id", sid)
        .order("sequence", desc=True)
        .limit(1),
        f"{EVENTS_TABLE} sequence read",
    )
    highest = 0
    for row in rows:
        if str(row.get("user_id")) != uid or str(row.get("session_id")) != sid:
            continue
        try:
            highest = max(highest, _exact_int(row.get("sequence"), "sequence"))
        except ValueError:
            continue
    return highest + 1


def insert_session_event(
    supabase: Any,
    *,
    session_id: Any,
    user_id: Any,
    sequence: Any,
    event_id: Any,
    event_type: Any,
    schema_version: Any,
    payload: Any,
    emitted_at: Any,
) -> Dict[str, Any]:
    """Append one Paper_Channel record to ``paper_events``. Append-only.

    Added by task 24.4 for the one record Requirement 14.5 names - a ``paper_error`` carrying
    ``code: 'FEED_DISCONNECTED'`` on a dropped market-data connection. It is written as a general
    ``paper_events`` insert rather than as a feed-specific one because ``paper_events`` has one
    shape and one set of constraints, and task 26.x's Paper_Channel writer must reach this table
    through this function rather than issuing a second statement against it.

    ``event_type`` is validated against ``chk_paper_event_type``'s sixteen values here, so a typo
    is a named failure at the call site rather than a ``23514``.

    Raises:
        PaperDuplicateSessionEvent: ``uq_paper_event_id`` or ``uq_paper_event_seq`` already holds
            this record. A no-op for the caller: the log line is already there.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")
    eid = _require_text(event_id, "event_id")

    row: Dict[str, Any] = {
        "session_id": sid,
        "user_id": uid,
        "sequence": _sequence_value(sequence, "sequence"),
        "event_id": eid,
        "event_type": _one_of(event_type, PAPER_EVENT_TYPES, "event_type"),
        "schema_version": _require_text(schema_version, "schema_version"),
        "payload": _jsonb(payload, "payload"),
        "emitted_at": _instant(emitted_at, "emitted_at"),
    }

    try:
        rows = _execute(
            supabase.table(EVENTS_TABLE).insert(row), f"{EVENTS_TABLE} insert"
        )
    except PaperPersistenceError as exc:
        cause = exc.__cause__ or exc
        if _is_unique_violation(cause):
            text = str(cause).lower()
            if "uq_paper_event_seq" in text:
                constraint = "uq_paper_event_seq"
            elif "uq_paper_event_id" in text:
                constraint = "uq_paper_event_id"
            else:
                # A driver that surfaces only the SQLSTATE names neither index. Both mean the same
                # thing here - the record is already in the log - so the outcome is the same and
                # the constraint is reported as unnamed rather than guessed at.
                constraint = "23505 (the driver named no constraint)"
            raise PaperDuplicateSessionEvent(sid, eid, constraint) from exc
        raise
    return _one(rows, f"{EVENTS_TABLE} insert")


# ══════════════════════════════════════════════════════════════════════════
# THE SESSION ROW ITSELF (task 27.1, 27.3 - Requirements 17.6, 17.7, 17.9, 27.4)
# ══════════════════════════════════════════════════════════════════════════
#
# Until task 27.x the session row was a PREMISE here: the feed transitioned three of its columns
# and the simulator read its ``config``, but nothing in the backend created one and nothing moved
# ``session_state``. The three functions below close that, and they are here rather than in
# ``paper_session_service`` for the reason stated at the top of this module: every statement against
# a ``paper_*`` table lives in one place, so there is one set of assumptions about the schema, one
# explicit projection list and one tenant predicate.
#
# What is deliberately NOT here is the session STATE MACHINE - which operation is permitted from
# which state, and what a refusal says. That is ``paper_session_service``'s, exactly as
# ``paper_order_state`` owns the order machine: this module validates the column VOCABULARY
# (:data:`SESSION_STATES`, which is ``chk_paper_session_state`` verbatim) and issues a guarded
# statement, and it refuses to be the place a business rule is spelled a second time.


#: ``paper_sessions.session_state`` - ``chk_paper_session_state``, verbatim and in 009's order.
#: These are also the four states of Requirement 17.7. ``paper_session_service`` imports THIS tuple
#: rather than re-listing them, so the column vocabulary and the state machine cannot drift.
SESSION_STATES: Tuple[str, ...] = ("CREATED", "RUNNING", "PAUSED", "STOPPED")

#: ``paper_sessions.session_state`` at creation. 009 declares it as the column DEFAULT; it is
#: spelled here as well because :func:`insert_session` writes the column explicitly - a payload
#: that relied on the default would create a session whose state depended on whether the migration
#: had been reconciled by hand.
SESSION_STATE_CREATED = "CREATED"

#: The one state the per-user concurrency cap of Requirement 27.4 counts, and the state
#: ``idx_paper_sessions_running`` is PARTIAL on.
SESSION_STATE_RUNNING = "RUNNING"

#: ``paper_sessions.environment`` - pinned by ``chk_paper_session_environment``. Written
#: explicitly for the same reason :data:`SESSION_STATE_CREATED` is, and there is no parameter to
#: override it: a paper session cannot be relabelled into a live one (Requirement 13's
#: Execution_Environment boundary).
SESSION_ENVIRONMENT = "PAPER"

#: ``paper_sessions.feed_state`` before :func:`~paper_market_feed.open_feed` has selected a source.
#: 009's column default, spelled here for the same reason.
SESSION_FEED_STATE_PENDING = "PENDING"

#: The projection the per-user concurrency cap reads. ONE column, because the cap needs a count and
#: nothing else: a projection that carried the symbol or the configuration of every running session
#: would move a payload across the wire to answer "how many". Scoped by ``user_id`` and
#: ``session_state``, which is exactly ``idx_paper_sessions_running``'s ``(user_id) WHERE
#: session_state = 'RUNNING'``, so the answer is one index scan and one round trip.
SESSION_COUNT_SELECT = "id"


def insert_session(
    supabase: Any,
    *,
    user_id: Any,
    source_strategy_id: Any,
    version_id: Any,
    exchange_id: Any,
    symbol: Any,
    timeframe: Any,
    initial_capital_minor: Any,
    currency: Any,
    config: Any,
    market_data_source: Any,
    listing_id: Any = None,
) -> Dict[str, Any]:
    """Create one Paper_Session at ``CREATED`` and return the row.

    The FIRST of the three writes task 27.1 calls "one transaction", and the only place a
    ``paper_sessions`` row is created. Every validation Requirement 17.4 lists has already passed
    by the time this is called - that ordering is ``paper_session_service.start_session``'s and is
    the whole of Requirement 17.13's "creates no Paper_Session"; this function is deliberately
    incapable of enforcing it, because a check here would be a second copy of a decision that has
    to be made before anything is created.

    ``session_state`` is ``CREATED`` and ``event_sequence`` is ``0``, both written explicitly
    rather than left to 009's column defaults: a session that started life at ``RUNNING`` because a
    hand-reconciled table carried a different default would make Requirement 14.4's "the session
    stays ``CREATED`` when the feed refuses" unobservable. ``environment`` is
    :data:`SESSION_ENVIRONMENT` with no parameter to change it.

    ``config`` goes through :func:`session_config_payload`, which is the column's ONE write path
    (Requirement 16.12) - and there is no update path, here or anywhere.

    ``initial_capital_minor`` is a ``BIGINT`` of Minor_Units and goes through
    :func:`_minor_units`, so a capital that is not a whole number of minor units is refused before
    ``chk_paper_capital`` sees it. The account's ``initial_capital`` is a ``NUMERIC`` major-unit
    figure and is the CALLER's to compute from this and the currency's exponent: converting here
    would be arithmetic, and this module performs none.

    ``listing_id`` is ``None`` for an owned strategy, which is what 009's nullable
    ``listing_id ... ON DELETE SET NULL`` means.

    Raises:
        PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied.
        PaperPersistenceError: the statement did not complete.
        ValueError: a required identifier is absent, or ``config`` is not a session configuration.
    """
    require_persistence(supabase)
    payload: Dict[str, Any] = {
        "user_id": _require_text(user_id, "user_id"),
        "listing_id": _optional_text(listing_id),
        "source_strategy_id": _require_text(source_strategy_id, "source_strategy_id"),
        "version_id": _require_text(version_id, "version_id"),
        "environment": SESSION_ENVIRONMENT,
        "session_state": SESSION_STATE_CREATED,
        "exchange_id": _require_text(exchange_id, "exchange_id"),
        "symbol": _require_text(symbol, "symbol"),
        "timeframe": _require_text(timeframe, "timeframe"),
        "initial_capital_minor": _minor_units(
            initial_capital_minor, "initial_capital_minor"
        ),
        "currency": _require_text(currency, "currency").upper(),
        "config": session_config_payload(config),
        "market_data_source": _require_text(market_data_source, "market_data_source"),
        "feed_state": SESSION_FEED_STATE_PENDING,
        "feed_transport": None,
        "event_sequence": 0,
    }
    rows = _execute(
        supabase.table(SESSIONS_TABLE).insert(payload),
        f"{SESSIONS_TABLE} insert",
    )
    row = _one(rows, f"{SESSIONS_TABLE} insert")
    _note_session_write(_optional_text(row.get("id")))
    return row


def count_running_sessions(supabase: Any, user_id: Any) -> int:
    """How many sessions this user has ``RUNNING``, in one round trip.

    Requirement 27.4's per-user concurrent cap. The predicates are ``user_id`` and
    ``session_state = 'RUNNING'``, which is precisely ``idx_paper_sessions_running`` - the PARTIAL
    index 009 declares for this question - so the whole of the user's session history is not
    scanned to answer it.

    The rows are counted in the process rather than through PostgREST's ``count`` parameter for one
    reason: a ``count`` answer arrives in a response HEADER, and a header that is absent (a proxy
    that stripped it, a client version that does not surface it) reads as ``None``, which a caller
    comparing against a cap would have to treat as either zero or as an error. Counting a
    single-column projection cannot be ambiguous, and the cap is 20 rows at its widest.

    The ``user_id`` and ``session_state`` predicates are restated over the returned rows, as every
    read in this module does: a Persistence_Layer double, or a lost filter, must not be able to
    make another tenant's running session count against this caller's cap - or, worse, not count
    against theirs.

    Raises:
        PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied.
        PaperPersistenceError: the read did not complete. Never reported as zero: admitting a
            session because the cap could not be read is exactly the "a read that did not complete
            is not a read that found nothing" failure this module exists to prevent.
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .select(SESSION_COUNT_SELECT)
        .eq("user_id", uid)
        .eq("session_state", SESSION_STATE_RUNNING),
        f"{SESSIONS_TABLE} running count",
    )
    return len(rows)


def transition_session_state(
    supabase: Any,
    *,
    user_id: Any,
    session_id: Any,
    from_state: Any,
    to_state: Any,
    started_at: Any = None,
    paused_at: Any = None,
    stopped_at: Any = None,
) -> Dict[str, Any]:
    """Move one session from ``from_state`` to ``to_state``, guarded on the state it was read at.

    The UPDATE carries THREE predicates - ``id``, ``user_id`` and ``session_state = from_state`` -
    and the third is what makes this the same optimistic protocol
    :func:`lock_account_for_update` / :func:`bump_version` use on a balance. There is no
    ``SELECT ... FOR UPDATE`` over PostgREST, so the state the service read is carried into the
    statement as a predicate: a concurrent operation that already moved the session makes this match
    **zero** rows and raise, rather than overwriting a state the caller never saw.

    ``from_state`` and ``to_state`` are checked against :data:`SESSION_STATES` -
    ``chk_paper_session_state`` verbatim - so a typo is a named failure here rather than a ``23514``
    from PostgreSQL. Whether the PAIR is a permitted edge is **not** checked here: that is
    ``paper_session_allowed_transitions`` + ``trg_paper_session_guard``'s in the database and
    ``paper_session_service``'s before it writes, and a third copy in this module would be a
    business rule in the statement layer.

    The three lifecycle timestamps are written only when named, following
    :func:`update_session_feed`: an operation recording ``stopped_at`` must not blank the
    ``started_at`` the session has carried since it began.

    Raises:
        PaperConcurrencyConflict: the statement matched no row. Exactly three causes, and they are
            deliberately one answer: no such session, not this user's session, or its state moved
            between the read and this write. The caller re-reads and decides; it must not treat any
            of the three as "the transition happened".
    """
    require_persistence(supabase)
    uid = _require_text(user_id, "user_id")
    sid = _require_text(session_id, "session_id")
    current = _one_of(from_state, SESSION_STATES, "from_state")
    target = _one_of(to_state, SESSION_STATES, "to_state")

    values: Dict[str, Any] = {"session_state": target, "updated_at": _utc_now()}
    if started_at is not None:
        values["started_at"] = _instant(started_at, "started_at")
    if paused_at is not None:
        values["paused_at"] = _instant(paused_at, "paused_at")
    if stopped_at is not None:
        values["stopped_at"] = _instant(stopped_at, "stopped_at")

    rows = _execute(
        supabase.table(SESSIONS_TABLE)
        .update(values)
        .eq("id", sid)
        .eq("user_id", uid)
        .eq("session_state", current),
        f"{SESSIONS_TABLE} state transition",
    )
    if not rows:
        raise PaperConcurrencyConflict(
            f"the {SESSIONS_TABLE} transition {current} -> {target} matched no row for session "
            f"{sid!r}; the session does not exist, is not this user's, or is no longer in "
            f"{current}",
            table=SESSIONS_TABLE,
            row_id=sid,
        )
    _note_session_write(sid)
    return rows[0]


__all__ = [
    "ACCOUNTS_TABLE",
    "ACCOUNT_SELECT",
    "BALANCE_EVENTS_TABLE",
    "BALANCE_EVENT_CAUSES",
    "BALANCE_EVENT_SELECT",
    "FILL_SELECT",
    "DEFAULT_CURRENCY",
    "PAPER_DEFAULT_ACCOUNT_MIGRATION",
    "PAPER_DEFAULT_ACCOUNT_MIGRATION_FILE",
    "DEFAULT_INITIAL_CAPITAL",
    "EQUITY_SNAPSHOTS_TABLE",
    "EQUITY_SNAPSHOT_CAUSES",
    "EQUITY_SNAPSHOT_SELECT",
    "EVENTS_TABLE",
    "EVENT_SELECT",
    "FILLS_TABLE",
    "IDEMPOTENCY_KEY_MAX_CHARS",
    "MARKET_EVENTS_TABLE",
    "MARKET_EVENT_SELECT",
    "METRICS_SELECT",
    "METRICS_TABLE",
    "ORDERS_TABLE",
    "ORDER_SELECT",
    "ORDER_SIDES",
    "ORDER_TYPES",
    "PAPER_EVENT_TYPES",
    "PAPER_PERSISTENCE_RECHECK_SECONDS",
    "PAPER_TRADING_MIGRATION",
    "PAPER_TRADING_MIGRATION_FILE",
    "SEQUENCE_ALLOCATION_ATTEMPTS",
    "POSITIONS_TABLE",
    "POSITION_SELECT",
    "POSITION_SIDES",
    "PROBE_SELECT",
    "SESSIONS_TABLE",
    "SESSION_CONFIG_SELECT",
    "SESSION_COUNT_SELECT",
    "SESSION_LIFECYCLE_SELECT",
    "SESSION_ENVIRONMENT",
    "SESSION_EVENT_REPLAY_CAP",
    "SESSION_FEED_SELECT",
    "SESSION_FEED_STATE_PENDING",
    "SESSION_LIST_CAP",
    "SESSION_LIST_SELECT",
    "SESSION_OWNER_SELECT",
    "SESSION_STATES",
    "SESSION_STATE_CREATED",
    "SESSION_STATE_RUNNING",
    "PaperConcurrencyConflict",
    "PaperDuplicateFill",
    "PaperDuplicateMarketEvent",
    "PaperDuplicateSessionEvent",
    "PaperPersistenceError",
    "PaperRepositoryError",
    "TRADES_TABLE",
    "TRADE_SELECT",
    "allocate_session_event_sequence",
    "bump_version",
    "clear_session_write_listeners",
    "count_running_sessions",
    "get_balance_events",
    "get_equity_snapshots",
    "get_fills",
    "get_market_events",
    "get_metrics",
    "get_or_create_account",
    "get_orders",
    "get_positions",
    "get_trades",
    "insert_balance_event",
    "insert_equity_snapshot",
    "insert_fill",
    "insert_market_event",
    "insert_metrics",
    "insert_order",
    "insert_session",
    "insert_session_event",
    "insert_trade",
    "is_missing_paper_table_error",
    "is_unapplied_default_account_migration_error",
    "list_sessions",
    "lock_account_for_update",
    "next_market_event_sequence",
    "next_session_event_sequence",
    "on_session_write",
    "paper_persistence_supported",
    "probe_idempotency_key",
    "read_account",
    "read_order",
    "read_session",
    "read_session_config",
    "read_session_events",
    "read_session_lifecycle",
    "read_session_owner",
    "read_session_summary",
    "remember_persistence_absent",
    "require_persistence",
    "reset_persistence_probe",
    "transition_session_state",
    "update_order",
    "update_session_feed",
    "upsert_position",
    "warn_default_account_migration_absent",
    "warn_persistence_absent",
]
