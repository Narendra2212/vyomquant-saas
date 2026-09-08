"""
backend/paper_trading_service.py - the Paper_Trading facade, on durable storage.

Spec: marketplace-subscriptions-paper-trading tasks 23.2 and 23.3. ``design.md`` -> "Paper
trading: persistence migration strategy". Requirements 16.8, 16.10, 16.11, 17.1, 17.2, 17.12,
18.1-18.8, 18.15, 13.6, 28.1, 28.3.

WHAT THIS MODULE IS NOW
-----------------------
The same facade, with the storage replaced. Every public method keeps its name, its argument
order and its return shape, and none of them holds a figure in process memory any more:

======================================  =====================================================
was                                     is
======================================  =====================================================
``self._accounts[uid]``                 ``paper_accounts`` where ``session_id IS NULL``
``self._positions[uid][symbol]``        ``paper_positions`` rows keyed ``(account_id, symbol)``
``self._orders[order_id]``              ``paper_orders`` rows
``self._trades[uid]`` list              ``paper_fills`` (every fill) + ``paper_trades`` (closed)
``self._idempotency_cache[key]``        ``paper_orders.idempotency_key`` + its unique indexes
``self._user_locks[uid]`` asyncio.Lock  the ``version`` optimistic protocol on the account row
``self._recalculate_account``           ``paper_accounting.recalculate`` (pure)
======================================  =====================================================

The module-level ``_paper_service_instance`` singleton and :func:`get_paper_trading_service`
stay, because ``routers/risk.py``, ``routers/paper_trading.py`` and
``backend/dashboard_aggregation_service.py`` all import the second by name.

THE DEFAULT ACCOUNT
-------------------
``paper_accounts`` where ``session_id IS NULL`` - the account the six existing ``/api/paper/*``
endpoints have always served. ``uq_paper_account_default`` keeps it single, and every read and
write below reaches it through ``paper/paper_repository.py``, which is the one module that
issues a statement against a ``paper_*`` table. Session-scoped accounts (Requirement 17.6) are
not this module's business; they are reached through ``/api/paper/sessions/*``.

WHY THE FIVE READS ARE STILL SYNCHRONOUS
---------------------------------------
``routers/risk.py`` calls ``get_performance_summary(uid)``, ``get_positions(uid)`` and
``get_or_create_account(uid)`` **synchronously from inside ``async def`` handlers** (lines
304, 309 and 357), and ``backend/dashboard_aggregation_service.py`` does the same in four
places. Making them ``await``-only would break all six call sites at once. So they keep
synchronous signatures and issue their statements through the synchronous ``supabase-py``
client, which is exactly why ``paper_repository`` is synchronous throughout. Only
``place_order``, ``cancel_order`` and ``check_limit_orders`` are ``async``, as they already
were.

REFUSAL, NOT DEGRADATION (Requirements 17.2, 28.3)
--------------------------------------------------
There is no in-memory fallback anywhere in this file. If ``paper_accounts`` does not exist,
``paper_repository.require_persistence`` raises the 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` that
names ``009_paper_trading.sql`` and this module lets it through untouched. If a statement DID
NOT COMPLETE, ``PaperPersistenceError`` becomes 503 ``PAPER_READ_FAILED`` - a different code,
because naming a migration that is in fact applied would send an operator to a file that
changes nothing. A balance served from process memory after this change would be the
fabricated figure Requirement 28.3 forbids, and Requirement 17.2's survive-a-restart guarantee
would read as true while being false.

WHERE THE ARITHMETIC LIVES
--------------------------
``paper/paper_accounting.py``, entirely. This module computes no balance, no equity, no PnL
and no position value: it reads rows, builds the engine's value objects, calls
``apply_fill`` / ``recalculate`` / ``lock`` / ``unlock`` / ``invariants_hold``, and writes what
comes back. The SHORT convention ``size * (2 * entry_price - price)`` that used to live in
``_recalculate_account`` is now ``paper_accounting.position_value``, unchanged.

Two consequences are deliberate improvements the engine brings with it, both of which change
figures the old implementation got wrong rather than figures it got right:

* **Zero tolerance.** ``verify_accounting_invariants`` compared with ``Decimal("0.05")``. It
  now asks ``paper_accounting.invariants_hold``, which is exact equality (Requirement 18.3).
* **A short close credits cash.** The old ``_execute_fill`` moved no cash at all when a short
  was closed, which the ``0.05`` tolerance was wide enough to hide. Cash is now the residual
  of the position-value change, so the equity identity holds for every one of the five fill
  cases by construction (Requirements 18.6, 18.7).

A fully closed position persists at ``size = 0`` with ``closed_at`` set (Requirement 18.5)
instead of being deleted from a dict, so its history survives; the open reads filter
``closed_at IS NULL``, which is what the deletion used to express structurally.

THE ADDITIVE RESPONSE FIELDS (task 23.3)
----------------------------------------
Five, and only five, on top of what each body already carried:

* ``execution_environment: "PAPER"`` (Requirement 13.6) and ``is_simulated: true``
  (Requirement 28.1) on every body, so no consumer can mistake a simulated figure for a live
  one.
* ``session_id: null`` on every default-account body - the honest name for "this is not a
  Paper_Session".
* ``stale`` and ``last_price_at`` (Requirement 18.15) beside every figure derived from a
  market price: ``unrealized_pnl``, a position's market value, ``total_equity`` and
  ``current_price``. ``stale`` is ``true`` when a figure was computed from the last validated
  price recorded on the position rather than from a price supplied with this call, which is
  the honest reading of a read path that has no live feed attached to it yet (task 24.x adds
  one). No price is ever synthesised, interpolated or defaulted to zero.

No existing key is removed, renamed, retyped or re-meaninged.
``tests/test_paper_api_shape_compatibility.py`` and
``tests/regression/test_baseline_unchanged.py`` are what hold that claim.

WHAT IS PERSISTED AND WHAT IS NOT
---------------------------------
``strategy_id`` and ``deployment_id`` are reported as ``null`` on every order and fill,
because ``paper_orders`` has no column for either - it has ``signal_id``, which is a different
thing - and this file will not invent a value it cannot store. That is exactly what the
pre-change baseline recorded for them (``"strategy_id": "null"``), so no response type
changes; what changes is that a caller which passes ``strategy_id`` into ``place_order`` no
longer sees it echoed back. It is still evaluated: the strategy-level risk limits below read
the argument. Recorded in the task report as a gap that needs an additive column, not papered
over here.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from backend_app.backend.paper import paper_accounting as accounting
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.errors import (
    PAPER_CONCURRENCY_CONFLICT,
    PAPER_INVARIANT_VIOLATION,
    PaperError,
    paper_persistence_unavailable,
    paper_read_failed,
)
from backend_app.backend.paper.paper_accounting import Account, AccountingConfig, Position
from backend_app.backend.paper.paper_order_state import PaperOrderState

logger = logging.getLogger("PaperTradingService")


# ══════════════════════════════════════════════════════════════════════════
# THE RETAINED ORDER STATUS VOCABULARY (Requirement 17.12)
# ══════════════════════════════════════════════════════════════════════════


class PaperOrderStatus(str, Enum):
    """The five spellings ``GET /api/paper/orders?status=…`` has always filtered on.

    **Retained, not replaced.** ``paper_orders.order_state`` stores the six canonical
    Paper_Order_State values and ``paper_orders.legacy_status`` carries the value from
    ``paper_order_state.LEGACY_STATUS_FOR_STATE``, which is written by the repository from the
    state and never passed to it. ``ACCEPTED`` and ``PARTIALLY_FILLED`` both map to ``OPEN``
    and ``CREATED`` maps to ``NEW``, so ``?status=OPEN`` keeps its exact meaning - an order
    live on the book, whether untouched or partly filled.
    """

    NEW = "NEW"
    OPEN = "OPEN"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


#: Requirement 13.6: every paper body says which Execution_Environment produced it.
PAPER_EXECUTION_ENVIRONMENT = "PAPER"

#: The two constants the account body has always carried. Neither is a measurement and neither
#: has a column: they are literals this endpoint returned before the repoint and returns after
#: it, kept so no consumer loses a field (Requirement 17.12).
ACCOUNT_NAME = "VyomQuant Paper Trading Account"
ACCOUNT_STATUS = "active"

#: The reference prices the existing service falls back to when a caller names no price. Kept
#: verbatim - changing them would change what an order fills at - and used only on the path
#: where the caller supplied none. Task 24.x replaces this with the measured feed; until then
#: it is a *configured* reference, and ``paper_market_feed`` is what will make it a measurement.
REFERENCE_PRICES: Mapping[str, Decimal] = {
    "BTC-USDT": Decimal("65000.00"),
    "BTC-USD": Decimal("65000.00"),
    "ETH-USDT": Decimal("3500.00"),
    "ETH-USD": Decimal("3500.00"),
    "SOL-USDT": Decimal("150.00"),
    "SOL-USD": Decimal("150.00"),
}

#: What an unlisted symbol falls back to, as before.
REFERENCE_PRICE_DEFAULT = Decimal("100.00")

#: Decimal places for money (USD minor units), for a price and for a quantity. The first is
#: what every balance the old implementation reported was quantized to (``Decimal("0.01")``),
#: and the second is what it quantized a fill price and a weighted-average entry to
#: (``Decimal("0.00000001")``). Retained so the repoint does not move a reported figure.
MONEY_EXPONENT = 2
PRICE_PRECISION = 8
QUANTITY_PRECISION = 8


class PaperTradingService:
    """The Paper_Trading facade. Holds configuration; holds no figure.

    Every method takes the identity it operates on and reaches the Persistence_Layer through
    ``paper_repository``. The only mutable state on an instance is the configuration passed to
    ``__init__`` and the optional bound Persistence_Layer handle, which exists so a worker, a
    test or the regression capture harness can name the client it wants used.
    """

    def __init__(
        self,
        default_capital: float = 100_000.0,
        default_fee_rate: float = 0.001,
        default_slippage: float = 0.0005,
        supabase: Any = None,
    ):
        # ``str`` first, deliberately: ``Decimal(0.001)`` is not one thousandth, while
        # ``Decimal("0.001")`` is. These three arrive as ``float`` from the existing callers'
        # signatures, and this is the one boundary where that transport value becomes an exact
        # decimal. Nothing downstream ever sees a ``float`` - ``paper_accounting.to_decimal``
        # refuses one outright (Requirement 18.1).
        self.default_capital = Decimal(str(default_capital))
        self.default_fee_rate = Decimal(str(default_fee_rate))
        #: Kept as the ``float`` it always was, because it is part of this class's published
        #: surface; the exact value used in arithmetic is ``self.config.slippage_rate``.
        self.default_slippage = default_slippage

        #: The frozen accounting configuration. One rounding mode, one cost basis, one set of
        #: scales, applied to every computation (Requirements 18.2, 18.8).
        self.config = AccountingConfig(
            fee_rate=self.default_fee_rate,
            slippage_rate=Decimal(str(default_slippage)),
            rounding_mode=ROUND_HALF_EVEN,
            cost_basis=accounting.WEIGHTED_AVERAGE,
            price_precision=PRICE_PRECISION,
            quantity_precision=QUANTITY_PRECISION,
            minor_unit_exponent=MONEY_EXPONENT,
            market_type=None,
        )

        self._supabase = supabase

    # ══════════════════════════════════════════════════════════════════
    # THE PERSISTENCE_LAYER HANDLE
    # ══════════════════════════════════════════════════════════════════

    def bind_persistence(self, supabase: Any) -> None:
        """Use ``supabase`` for every statement from now on. ``None`` unbinds.

        The seam a worker, a test and ``tests/regression/capture_baseline.py`` use to name the
        Persistence_Layer they want. It is not a cache and holds no row: only the handle.
        """
        self._supabase = supabase

    def persistence_client(self, access_token: Optional[str] = None) -> Any:
        """:meth:`_client` under a public name, for a caller outside this class.

        The Paper_Session routes (task 28.1) issue their statements through
        ``paper/paper_session_service`` and ``paper/paper_repository`` rather than through this
        service, but they must reach the SAME handle by the same three-source rule - the bound
        one first, then a client carrying the caller's JWT so RLS resolves ``auth.uid()``, then
        the anon singleton - or a test that bound a Persistence_Layer here would find the session
        routes talking to a different one, and a request-scoped read would silently lose its
        row-level-security scope. Resolving it twice was the alternative, and two resolutions can
        disagree about which of the three sources applies.

        Raises:
            PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when no handle can be obtained.
        """
        return self._client(access_token)

    def _client(self, access_token: Optional[str] = None) -> Any:
        """The RLS-scoped Persistence_Layer handle for this call.

        Three sources, in descending order of authority:

        1. the handle bound by :meth:`bind_persistence`;
        2. a per-request client carrying the caller's JWT, so ``auth.uid()`` resolves and the
           row-level-security policies on the ``paper_*`` tables apply to the identity that
           made the request - which is why the routers pass ``user["access_token"]``;
        3. the module-level anon singleton, for the internal callers that hold no token
           (``routers/risk.py`` and ``dashboard_aggregation_service``, until task 23.5 hands
           one down).

        Raises:
            PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when no handle can be obtained at
                all. With no handle there is no storage, and the alternative would be to
                proceed as though there were - which is the memory fallback this whole task
                exists to remove (Requirements 17.2, 28.3).
        """
        if self._supabase is not None:
            return self._supabase

        client = None
        if access_token:
            try:
                from backend_app.core.dependencies import create_request_supabase

                client = create_request_supabase(str(access_token))
            except Exception as exc:  # noqa: BLE001 - falls through to the anon singleton
                logger.warning(
                    "A request-scoped Supabase client could not be created for a paper read; "
                    "falling back to the module client. Detail: %s",
                    exc,
                )
        if client is None:
            try:
                from backend_app.core.dependencies import get_supabase

                client = get_supabase()
            except Exception as exc:  # noqa: BLE001 - classified as an outage below
                logger.error("No Supabase handle is available for paper trading: %s", exc)
                client = None

        if client is None:
            raise paper_persistence_unavailable(repo.PAPER_TRADING_MIGRATION_FILE)
        return client

    # ══════════════════════════════════════════════════════════════════
    # FAILURE TRANSLATION
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _read_failed(exc: BaseException, what: str) -> PaperError:
        """``PaperPersistenceError`` -> 503 ``PAPER_READ_FAILED``, with the detail logged.

        The statement did not complete, so there is no answer - and "no answer" must not be
        served as an empty list or a zero balance. The driver's message goes to the log and
        never into ``details``, which ``redact_details`` would scrub anyway.
        """
        logger.error("A paper %s did not complete: %s", what, exc)
        return paper_read_failed()

    @staticmethod
    def _conflict(exc: repo.PaperConcurrencyConflict) -> PaperError:
        """A version- or state-guarded write that matched no row -> 409.

        Requirement 16.10's answer to a lost race. The caller learns it lost instead of being
        told the write succeeded.
        """
        logger.warning("A paper write lost its optimistic guard: %s", exc)
        return PaperError(PAPER_CONCURRENCY_CONFLICT)

    # ══════════════════════════════════════════════════════════════════
    # SMALL HELPERS
    # ══════════════════════════════════════════════════════════════════

    def _get_timestamp(self) -> str:
        """The current instant in UTC as an ISO-8601 string. Retained name and shape."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _exact(value: Any, what: str) -> Decimal:
        """A transport value as an exact ``Decimal``.

        A ``float`` arrives from the HTTP boundary (``PaperOrderRequest.quantity`` is a
        ``float`` field and cannot change without changing the request schema), and this is
        the single place it becomes exact - through ``str``, so ``0.07`` becomes
        ``Decimal("0.07")`` and not the binary approximation. Everything downstream sees only
        ``Decimal`` and ``paper_accounting.to_decimal`` refuses a ``float`` outright
        (Requirement 18.1).
        """
        if isinstance(value, Decimal):
            return value
        if isinstance(value, bool) or value is None:
            raise ValueError(f"{what} must be a number, got {value!r}")
        return Decimal(str(value))

    def _minor(self, amount: Decimal, column: str) -> int:
        """A money amount as exact integer Minor_Units.

        The amount is quantized to the currency's minor unit first, so the integer is exact
        rather than rounded: a fee is a whole number of minor units or it is not a fee
        (Requirement 18.2).
        """
        quantized = self.config.money(amount)
        scaled = quantized.scaleb(MONEY_EXPONENT)
        if scaled != scaled.to_integral_value():  # pragma: no cover - defensive
            raise ValueError(f"{column} is not an exact number of minor units: {amount}")
        return int(scaled)

    @staticmethod
    def _fingerprint(
        user_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Optional[Decimal],
    ) -> str:
        """A stable digest of the intent, for ``paper_orders.fingerprint``.

        ``NOT NULL`` on the column, and what task 25.3 compares when the same idempotency key
        arrives twice: the same key with a *different* intent is a conflict, not a duplicate
        (Requirement 16.11). Deterministic - a hash of the canonical intent - so it is
        reproducible across processes and carries no clock and no random draw.
        """
        canonical = "|".join(
            [
                user_id,
                symbol,
                side,
                order_type,
                str(quantity),
                "" if price is None else str(price),
            ]
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _text(value: Any) -> Optional[str]:
        return None if value is None else str(value)

    @staticmethod
    def _clean_symbol(symbol: str) -> str:
        return str(symbol).strip().upper().replace("/", "-")

    # ══════════════════════════════════════════════════════════════════
    # ROWS -> THE ACCOUNTING ENGINE'S VALUE OBJECTS
    # ══════════════════════════════════════════════════════════════════

    def _account_of(self, row: Mapping[str, Any]) -> Account:
        """A ``paper_accounts`` row as an :class:`Account`.

        ``total_equity`` is carried across as stored so that
        :meth:`verify_accounting_invariants` can *check* it against the sum. Nothing here
        recomputes it to store it (Requirement 18.3).
        """
        return Account(
            available_balance=accounting.to_decimal(
                row.get("available_balance"), "available_balance"
            ),
            locked_balance=accounting.to_decimal(row.get("locked_balance"), "locked_balance"),
            realized_pnl=accounting.to_decimal(row.get("realized_pnl"), "realized_pnl"),
            total_equity=accounting.to_decimal(row.get("total_equity"), "total_equity"),
            currency=str(row.get("currency") or repo.DEFAULT_CURRENCY),
        )

    def _positions_of(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Tuple[Dict[str, Position], Dict[str, Mapping[str, Any]]]:
        """``paper_positions`` rows as ``{symbol: Position}``, plus the rows by symbol.

        The rows are kept beside the value objects because the response body reports the row's
        own ``created_at``, ``updated_at`` and ``price_at``, which the engine's value object
        has no place for and must not invent.
        """
        positions: Dict[str, Position] = {}
        by_symbol: Dict[str, Mapping[str, Any]] = {}
        for row in rows:
            symbol = str(row.get("symbol"))
            if symbol in positions and not positions[symbol].is_closed:
                # An OPEN row already stands for this symbol. ``uq_paper_position_open`` allows
                # at most one, while closed rows accumulate as history, so a later closed row
                # must not displace the open one - the fill path decides what to do from
                # whichever row it finds here.
                continue
            positions[symbol] = Position(
                symbol=symbol,
                side=str(row.get("side")),
                size=accounting.to_decimal(row.get("size"), "size"),
                entry_price=accounting.to_decimal(row.get("entry_price"), "entry_price"),
                current_price=(
                    None
                    if row.get("current_price") is None
                    else accounting.to_decimal(row.get("current_price"), "current_price")
                ),
                unrealized_pnl=(
                    None
                    if row.get("unrealized_pnl") is None
                    else accounting.to_decimal(row.get("unrealized_pnl"), "unrealized_pnl")
                ),
                price_at=row.get("price_at"),
                opened_at=row.get("opened_at"),
                closed_at=row.get("closed_at"),
            )
            by_symbol[symbol] = row
        return positions, by_symbol

    # ══════════════════════════════════════════════════════════════════
    # VALUATION (Requirements 18.3, 18.8, 18.15)
    # ══════════════════════════════════════════════════════════════════

    def _figures(
        self,
        account: Account,
        positions: Mapping[str, Position],
        prices: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Revalue the book and report what is price-derived, and whether it is stale.

        The replacement for ``_recalculate_account``, and it writes nothing: the arithmetic is
        ``paper_accounting.recalculate`` and the prices are **arguments**. The old
        implementation fell back to the position's own ``current_price`` and reported the
        result as current, which is what let a pre-disconnection price be presented as a fresh
        one.

        ``stale`` is ``true`` when any open position had to be valued at the last validated
        price recorded on its row rather than at a price supplied with this call (Requirement
        18.15), and ``last_price_at`` is the newest such timestamp. A book with no open
        position has no price-derived figure at all, so it is not stale.

        Returns:
            ``{"positions", "total_equity", "unrealized_pnl", "position_market_value",
            "stale", "last_price_at"}``.
        """
        supplied: Dict[str, Decimal] = {
            str(symbol): self._exact(price, f"price for {symbol}")
            for symbol, price in dict(prices or {}).items()
            if price is not None
        }
        merged: Dict[str, Decimal] = dict(
            accounting.last_validated_prices(positions.values())
        )
        merged.update(supplied)

        open_symbols = [p.symbol for p in positions.values() if p.is_open]
        stale = any(symbol not in supplied for symbol in open_symbols)
        last_price_at = accounting.latest_price_at(positions.values())

        try:
            valuation = accounting.recalculate(account, positions, merged, self.config)
        except accounting.StalePrice as exc:
            # No validated price exists for an open position at all - not even a recorded one.
            # Requirement 18.15 forbids synthesising, interpolating or zeroing it, so the
            # price-derived figures are reported exactly as they were last persisted and the
            # answer says so. Unreachable for a row this service wrote: every fill records the
            # price it filled at.
            logger.warning(
                "A paper position has no validated price, so the equity figures are reported "
                "as last persisted and marked stale. Detail: %s",
                exc,
            )
            persisted_unrealized = sum(
                (p.unrealized_pnl for p in positions.values() if p.unrealized_pnl is not None),
                Decimal("0"),
            )
            return {
                "positions": positions,
                "total_equity": account.total_equity,
                "unrealized_pnl": persisted_unrealized,
                "position_market_value": account.total_equity - account.cash,
                "stale": True,
                "last_price_at": self._text(last_price_at),
            }

        return {
            "positions": valuation.positions,
            "total_equity": valuation.account.total_equity,
            "unrealized_pnl": valuation.unrealized_pnl,
            "position_market_value": valuation.position_market_value,
            "stale": stale,
            "last_price_at": self._text(last_price_at),
        }

    # ══════════════════════════════════════════════════════════════════
    # THE RESPONSE BODIES - EVERY EXISTING KEY, PLUS THE FIVE ADDITIVE ONES
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _paper_provenance(stale: Any = None, last_price_at: Any = None) -> Dict[str, Any]:
        """The additive fields of task 23.3, and nothing else.

        ``execution_environment`` and ``is_simulated`` on every body (Requirements 13.6, 28.1);
        ``session_id: null`` because the default account is not a Paper_Session; ``stale`` and
        ``last_price_at`` **only** where the body carries a figure derived from a market price
        (Requirement 18.15) - an order's quantity and limit price are recorded facts, not
        market-derived ones, so they get neither.
        """
        provenance: Dict[str, Any] = {
            "execution_environment": PAPER_EXECUTION_ENVIRONMENT,
            "is_simulated": True,
            "session_id": None,
        }
        if stale is not None:
            provenance["stale"] = bool(stale)
            provenance["last_price_at"] = last_price_at
        return provenance

    def _account_body(
        self,
        row: Mapping[str, Any],
        figures: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """The ``/api/paper/account`` body: the thirteen keys it has always carried.

        The stored money columns are reported **as stored**, unchanged, because that is what
        makes "what the row says is what the endpoint says" checkable. ``unrealized_pnl`` and
        ``total_equity`` are the computed figures - the first has no column at all, and the
        second is recomputed rather than trusted so that Requirement 18.3's identity is what
        the reader sees.
        """
        body = {
            "account_id": str(row.get("id")),
            "user_id": str(row.get("user_id")),
            "name": ACCOUNT_NAME,
            "currency": str(row.get("currency") or repo.DEFAULT_CURRENCY),
            "initial_capital": str(row.get("initial_capital")),
            "available_balance": str(row.get("available_balance")),
            "locked_balance": str(row.get("locked_balance")),
            "total_equity": str(figures["total_equity"]),
            "realized_pnl": str(row.get("realized_pnl")),
            "unrealized_pnl": str(figures["unrealized_pnl"]),
            "status": ACCOUNT_STATUS,
            "created_at": str(row.get("created_at")),
            "updated_at": str(row.get("updated_at")),
        }
        body.update(
            self._paper_provenance(figures["stale"], figures["last_price_at"])
        )
        return body

    def _position_body(
        self,
        row: Mapping[str, Any],
        position: Position,
        stale: bool,
    ) -> Dict[str, Any]:
        """One element of ``/api/paper/positions``: the eight keys it has always carried.

        ``side`` is lower-cased back to ``'long'`` / ``'short'``, which is the spelling the
        response has always used and what ``Portfolio.jsx`` and
        ``dashboard_aggregation_service`` read; ``paper_positions.side`` stores ``LONG`` /
        ``SHORT`` because ``chk_paper_position_side`` says so.

        ``current_price`` and ``unrealized_pnl`` are ``null`` in the one case where the row
        carries no validated price - an absent price stays absent (Requirement 18.15). No path
        in this service produces such a row: a position exists only as the result of a fill,
        and a fill records the price it filled at.
        """
        body = {
            "symbol": position.symbol,
            "side": str(position.side).lower(),
            "size": str(position.size),
            "entry_price": str(position.entry_price),
            "current_price": (
                None if position.current_price is None else str(position.current_price)
            ),
            "unrealized_pnl": (
                None if position.unrealized_pnl is None else str(position.unrealized_pnl)
            ),
            "created_at": str(row.get("created_at")),
            "updated_at": str(row.get("updated_at")),
        }
        body.update(self._paper_provenance(stale, self._text(row.get("price_at"))))
        return body

    def _order_body(
        self,
        row: Mapping[str, Any],
        fills: Sequence[Mapping[str, Any]] = (),
    ) -> Dict[str, Any]:
        """One element of ``/api/paper/orders``: the keys it has always carried.

        ``fee`` and ``execution_id`` appear **only** on an order that has at least one fill,
        which is exactly the union the pre-change baseline recorded: a filled order carried
        them and a resting or cancelled one did not. Adding them unconditionally would add two
        keys to a variant that never had them.

        ``price`` is the average fill price once there is one, and the limit price before that
        - the same value this field has always held. ``strategy_id`` and ``deployment_id`` are
        ``null`` because ``paper_orders`` has no column for either; see the module docstring.
        """
        price = (
            row.get("avg_fill_price")
            if row.get("avg_fill_price") is not None
            else row.get("limit_price")
            if row.get("limit_price") is not None
            else row.get("reference_price")
        )
        body: Dict[str, Any] = {
            "order_id": str(row.get("id")),
            "user_id": str(row.get("user_id")),
            "strategy_id": None,
            "deployment_id": None,
            "symbol": str(row.get("symbol")),
            "side": str(row.get("side")),
            "order_type": str(row.get("order_type")),
            "quantity": str(row.get("quantity")),
            "price": None if price is None else str(price),
            "filled_quantity": str(row.get("filled_quantity")),
            "status": str(row.get("legacy_status")),
            "created_at": str(row.get("created_at")),
            "updated_at": str(row.get("updated_at")),
        }
        if fills:
            fee_minor = sum(int(fill.get("fee_minor") or 0) for fill in fills)
            body["fee"] = str(self.config.money(Decimal(fee_minor).scaleb(-MONEY_EXPONENT)))
            body["execution_id"] = str(fills[0].get("fill_event_id"))
        body.update(self._paper_provenance())
        return body

    def _trade_body(
        self,
        fill: Mapping[str, Any],
        order: Mapping[str, Any],
        realized_pnl: Decimal,
    ) -> Dict[str, Any]:
        """One element of ``/api/paper/trades``: the fill history, key for key.

        This endpoint has always served **fills** - the in-memory ``self._trades[uid]`` list
        held one record per fill, appended by ``_execute_fill`` - so it is served from
        ``paper_fills``, joined to its order for the symbol and side those columns live on and
        to the balance ledger for the realized PnL that fill moved. ``paper_trades`` answers
        the *other* question, closed round-trips, and is what ``/summary``'s closed-trade
        figures count.
        """
        body = {
            "execution_id": str(fill.get("fill_event_id")),
            "order_id": str(fill.get("order_id")),
            "user_id": str(fill.get("user_id")),
            "strategy_id": None,
            "deployment_id": None,
            "symbol": str(order.get("symbol")),
            "side": str(order.get("side")),
            "quantity": str(fill.get("quantity")),
            "price": str(fill.get("price")),
            "fee": str(
                self.config.money(
                    Decimal(int(fill.get("fee_minor") or 0)).scaleb(-MONEY_EXPONENT)
                )
            ),
            "realized_pnl": str(realized_pnl),
            "status": PaperOrderStatus.FILLED.value,
            "executed_at": str(fill.get("filled_at")),
        }
        body.update(self._paper_provenance())
        return body

    # ══════════════════════════════════════════════════════════════════
    # THE READS - SYNCHRONOUS, FOR THE REASON IN THE MODULE DOCSTRING
    # ══════════════════════════════════════════════════════════════════

    def _account_and_positions(
        self,
        sb: Any,
        uid: str,
        initial_capital: Optional[Decimal] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """The default account row and its OPEN positions. Two statements.

        ``get_or_create_account`` creates the account when it is absent, which is the
        behaviour every caller of this facade already relies on - ``routers/risk.py`` and
        ``dashboard_aggregation_service`` both call ``get_or_create_account`` for a user who
        may never have traded.
        """
        account = repo.get_or_create_account(
            sb,
            uid,
            repo.DEFAULT_CURRENCY,
            None,
            initial_capital=(
                self.default_capital if initial_capital is None else initial_capital
            ),
        )
        positions = repo.get_positions(sb, uid, account_id=account["id"])
        return account, positions

    def get_or_create_account(
        self,
        user_id: str,
        initial_capital: Optional[float] = None,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The user's virtual paper account, created at ``initial_capital`` if it has none.

        Synchronous, and called synchronously from ``async def`` handlers in
        ``routers/risk.py`` (line 357) and ``routers/paper_trading.py``.

        Raises:
            PaperError: 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied, or 503
                ``PAPER_READ_FAILED`` when a statement did not complete. Never a default
                balance (Requirements 17.2, 28.3).
        """
        uid = str(user_id)
        sb = self._client(access_token)
        capital = None if initial_capital is None else self._exact(initial_capital, "capital")
        try:
            row, position_rows = self._account_and_positions(sb, uid, capital)
            positions, _ = self._positions_of(position_rows)
            return self._account_body(row, self._figures(self._account_of(row), positions))
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "account read") from exc

    def get_positions(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's OPEN paper positions, with their unrealized PnL.

        A fully closed position persists at ``size = 0`` with ``closed_at`` set rather than
        being deleted (Requirement 18.5), and the read filters ``closed_at IS NULL`` in the
        statement - which is what the old ``del user_positions[symbol]`` expressed
        structurally. So this returns exactly the set the dictionary used to hold, and
        ``risk.py``'s ``open_pos_count`` keeps reporting the figure it reports today.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            row, position_rows = self._account_and_positions(sb, uid)
            positions, by_symbol = self._positions_of(position_rows)
            figures = self._figures(self._account_of(row), positions)
            revalued = figures["positions"]
            return [
                self._position_body(
                    by_symbol[symbol], revalued[symbol], bool(figures["stale"])
                )
                for symbol in by_symbol
                if revalued[symbol].is_open
            ]
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "positions read") from exc

    def get_orders(
        self,
        user_id: str,
        status: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's paper orders, newest first, optionally filtered by legacy status.

        ``status`` is applied as a predicate on ``paper_orders.legacy_status`` inside the
        statement, so ``?status=OPEN`` returns exactly the orders it returns today:
        ``ACCEPTED`` and ``PARTIALLY_FILLED`` both carry ``OPEN`` there, written by the
        repository from ``LEGACY_STATUS_FOR_STATE`` (Requirement 17.12).
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=self.default_capital
            )
            rows = repo.get_orders(
                sb,
                uid,
                account_id=account["id"],
                legacy_status=None if not status else str(status).upper(),
            )
            fills_by_order = self._fills_by_order(repo.get_fills(sb, uid))
            return [
                self._order_body(row, fills_by_order.get(str(row.get("id")), []))
                for row in rows
            ]
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "orders read") from exc

    @staticmethod
    def _fills_by_order(
        fills: Sequence[Mapping[str, Any]]
    ) -> Dict[str, List[Mapping[str, Any]]]:
        """``{order_id: [fill, …]}``, newest fill first within each order."""
        grouped: Dict[str, List[Mapping[str, Any]]] = {}
        for fill in fills:
            grouped.setdefault(str(fill.get("order_id")), []).append(fill)
        return grouped

    def get_trades(
        self,
        user_id: str,
        limit: int = 50,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """The user's fill history, most recent first, at most ``limit`` records.

        Three statements: the fills, the orders they belong to (for ``symbol`` and ``side``,
        which live on the order) and the ``FILL`` balance events (for the realized PnL each
        fill moved). The realized figure is read from the ledger rather than recomputed,
        because the ledger is where it was recorded when the money moved.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            fills = repo.get_fills(sb, uid, limit=None if limit is None else int(limit))
            if not fills:
                return []
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=self.default_capital
            )
            orders = {
                str(row.get("id")): row
                for row in repo.get_orders(sb, uid, account_id=account["id"])
            }
            realized = self._realized_by_fill(
                repo.get_balance_events(sb, uid, account_id=account["id"], cause="FILL")
            )
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "trades read") from exc

        trades: List[Dict[str, Any]] = []
        for fill in fills:
            order = orders.get(str(fill.get("order_id")))
            if order is None:
                # ``paper_fills.order_id`` is a NOT NULL foreign key, so this is unreachable
                # for a consistent database. Reporting the fill without a symbol or a side
                # would be inventing two values, and dropping it silently would lose a record,
                # so it is dropped loudly.
                logger.error(
                    "A paper fill references an order that is not readable for this identity; "
                    "it is omitted from the fill history. fill=%s order=%s",
                    fill.get("id"),
                    fill.get("order_id"),
                )
                continue
            trades.append(
                self._trade_body(fill, order, realized.get(str(fill.get("id")), Decimal("0")))
            )
        return trades

    def _realized_by_fill(
        self, events: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Decimal]:
        """``{fill_id: realized_delta}`` from the ``FILL`` balance events.

        The per-fill realized PnL, as recorded when the money moved. A fill that realized
        nothing has an event carrying ``realized_delta = 0``, so an *absent* entry means the
        ledger is incomplete rather than that the fill realized nothing - which is why the
        caller logs it rather than treating the two as the same thing.
        """
        realized: Dict[str, Decimal] = {}
        for event in events:
            fill_id = event.get("fill_id")
            if fill_id is None:
                continue
            realized[str(fill_id)] = accounting.to_decimal(
                event.get("realized_delta"), "realized_delta"
            )
        return realized

    def get_performance_summary(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The paper performance summary, key for key as ``/api/paper/summary`` returns it.

        ``total_trades`` counts fills, and ``closed_trades`` / ``winning_trades`` /
        ``losing_trades`` count **realizing fills** - a fill whose ledger event moved
        ``realized_pnl`` - which is exactly what the old implementation counted (``[t for t in
        trades if Decimal(t["realized_pnl"]) != 0]``). Counting ``paper_trades`` rows instead
        would silently drop every partial close from the win rate.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            row, position_rows = self._account_and_positions(sb, uid)
            positions, _ = self._positions_of(position_rows)
            figures = self._figures(self._account_of(row), positions)
            fills = repo.get_fills(sb, uid)
            realized = self._realized_by_fill(
                repo.get_balance_events(sb, uid, account_id=row["id"], cause="FILL")
            )
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "summary read") from exc

        account_body = self._account_body(row, figures)
        initial_capital = accounting.to_decimal(row.get("initial_capital"), "initial_capital")
        total_equity = accounting.to_decimal(figures["total_equity"], "total_equity")
        total_pnl = total_equity - initial_capital
        roi_pct = (
            total_pnl / initial_capital * Decimal("100")
            if initial_capital > 0
            else Decimal("0")
        )

        realizing = [
            realized[str(fill.get("id"))]
            for fill in fills
            if str(fill.get("id")) in realized
            and realized[str(fill.get("id"))] != Decimal("0")
        ]
        winning = [amount for amount in realizing if amount > Decimal("0")]
        losing = [amount for amount in realizing if amount < Decimal("0")]

        win_rate = (len(winning) / len(realizing) * 100) if realizing else 0.0
        gross_profit = sum(winning, Decimal("0"))
        gross_loss = abs(sum(losing, Decimal("0")))
        profit_factor = (
            float(gross_profit / gross_loss)
            if gross_loss > 0
            else (float(gross_profit) if gross_profit > 0 else 1.0)
        )

        summary = {
            "account": account_body,
            "total_pnl": str(self.config.money(total_pnl)),
            "roi_pct": float(self.config.money(roi_pct)),
            "total_trades": len(fills),
            "closed_trades": len(realizing),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "open_positions_count": len(
                [p for p in positions.values() if p.is_open]
            ),
        }
        summary.update(
            self._paper_provenance(figures["stale"], figures["last_price_at"])
        )
        return summary

    def verify_accounting_invariants(
        self,
        user_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> bool:
        """Whether every Requirement 18.3 / 18.4 / 18.5 invariant holds, to **zero tolerance**.

        The old implementation compared ``total_equity`` against the sum with a
        ``Decimal("0.05")`` tolerance, which was wide enough to hide the short-close path that
        credited no cash. ``paper_accounting.invariants_hold`` is exact equality, and the
        figures it checks are the persisted ones: the stored ``total_equity`` against the
        stored balances and the stored positions at their last validated prices.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        try:
            row, position_rows = self._account_and_positions(sb, uid)
            positions, _ = self._positions_of(position_rows)
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "invariant read") from exc

        account = self._account_of(row)
        prices = accounting.last_validated_prices(positions.values())
        try:
            invariant = accounting.violated_invariant(
                account, positions, prices, self.config
            )
        except accounting.StalePrice as exc:
            # A missing measurement is not a breached invariant (Requirement 18.15). Saying
            # "the ledger is broken" because a price is absent would be the wrong answer.
            logger.warning(
                "The paper accounting invariants could not be checked because a validated "
                "price is absent: %s",
                exc,
            )
            return True
        if invariant is not None:
            logger.error(
                "A paper accounting invariant does not hold for account %s: %s",
                row.get("id"),
                invariant,
            )
        return invariant is None

    # ══════════════════════════════════════════════════════════════════
    # RESET (Requirement 17.15)
    # ══════════════════════════════════════════════════════════════════

    def reset_account(
        self,
        user_id: str,
        capital: Optional[float] = None,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reset the account to ``capital``, close every position and cancel every open order.

        Synchronous, as it always was. **History is not deleted**: the filled orders, the fills,
        the closed round-trips and the balance ledger all stay readable, which is what lets
        ``GET /api/paper/orders?status=FILLED`` keep answering after a reset exactly as it does
        today. A position is closed by persisting ``size = 0`` with ``closed_at`` set
        (Requirement 18.5), and the reset itself is recorded as a ``RESET`` balance event so the
        ledger explains the jump instead of a balance simply changing.
        """
        uid = str(user_id)
        sb = self._client(access_token)
        cap = self.default_capital if capital is None else self._exact(capital, "capital")
        occurred_at = self._get_timestamp()

        try:
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=cap
            )
            account_id = str(account["id"])

            for order in repo.get_orders(
                sb,
                uid,
                account_id=account_id,
                legacy_status=PaperOrderStatus.OPEN.value,
            ):
                repo.update_order(
                    sb,
                    user_id=uid,
                    order_id=order["id"],
                    order_state=PaperOrderState.CANCELLED,
                    expected_state=PaperOrderState(order["order_state"]),
                )

            for row in repo.get_positions(sb, uid, account_id=account_id):
                repo.upsert_position(
                    sb,
                    account_id=account_id,
                    user_id=uid,
                    symbol=row["symbol"],
                    side=row["side"],
                    size=Decimal("0"),
                    entry_price=accounting.to_decimal(row["entry_price"], "entry_price"),
                    opened_at=row.get("opened_at") or occurred_at,
                    current_price=row.get("current_price"),
                    unrealized_pnl=Decimal("0"),
                    price_at=row.get("price_at"),
                    closed_at=occurred_at,
                )

            before_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
            before = self._account_of(before_row)
            updated = repo.bump_version(
                sb,
                user_id=uid,
                account_id=account_id,
                expected_version=before_row["version"],
                payload={
                    "initial_capital": cap,
                    "available_balance": cap,
                    "locked_balance": Decimal("0"),
                    "realized_pnl": Decimal("0"),
                    "total_equity": cap,
                    "last_price_at": None,
                    "stale": False,
                },
            )
            repo.insert_balance_event(
                sb,
                account_id=account_id,
                user_id=uid,
                cause="RESET",
                available_delta=cap - before.available_balance,
                locked_delta=-before.locked_balance,
                realized_delta=-before.realized_pnl,
                available_after=cap,
                locked_after=Decimal("0"),
                realized_after=Decimal("0"),
                occurred_at=occurred_at,
            )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "account reset") from exc

        # No open position survives a reset, so nothing in the body is price-derived.
        return self._account_body(
            updated, self._figures(self._account_of(updated), {})
        )

    # ══════════════════════════════════════════════════════════════════
    # PLACING AN ORDER (Requirements 16.6, 16.8, 16.11)
    # ══════════════════════════════════════════════════════════════════

    async def place_order(
        self,
        user_id: str,
        symbol: str,
        side: str,
        order_type: str = "market",
        quantity: float = 0.01,
        price: Optional[float] = None,
        strategy_id: Optional[str] = None,
        deployment_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place a paper market or limit order. Same argument order, same return shape.

        IDEMPOTENCY IS THE DATABASE'S NOW (Requirement 16.8)
        ---------------------------------------------------
        ``self._idempotency_cache`` is gone. The key is persisted on
        ``paper_orders.idempotency_key`` and arbitrated by ``uq_paper_order_idem_default`` -
        the partial unique index on ``(account_id, idempotency_key) WHERE session_id IS NULL``
        that ``013_paper_default_account_children.sql`` adds, because SQL treats two NULL
        ``session_id`` values as distinct and ``uq_paper_order_idem`` alone would silently stop
        de-duplicating the default account's orders. A retry re-reads the recorded order and
        returns it; a concurrent first request that loses the index race is answered the same
        way, by reading the winner.

        PER-USER SERIALISATION IS THE VERSION GUARD NOW (Requirement 16.10)
        ------------------------------------------------------------------
        ``self._user_locks`` is gone with it. It only ever serialised one process, so two
        workers could both move the same balance; the ``paper_accounts.version`` predicate
        makes the loser's UPDATE match no row and surface as 409
        ``PAPER_CONCURRENCY_CONFLICT`` instead of a silently overwritten balance.
        """
        uid = str(user_id)
        sb = self._client(access_token)

        clean_sym = self._clean_symbol(symbol)
        clean_side = str(side).strip().lower()
        clean_type = str(order_type).strip().lower()
        qty_dec = self._exact(quantity, "quantity")

        if qty_dec <= Decimal("0"):
            raise ValueError("Quantity must be greater than 0")
        if clean_side not in ("buy", "sell"):
            raise ValueError("Side must be 'buy' or 'sell'")
        if clean_type not in ("market", "limit"):
            raise ValueError("Order type must be 'market' or 'limit'")

        try:
            account_row, position_rows = self._account_and_positions(sb, uid)
            account_id = str(account_row["id"])

            if idempotency_key:
                recorded = repo.probe_idempotency_key(
                    sb,
                    user_id=uid,
                    idempotency_key=idempotency_key,
                    account_id=account_id,
                )
                if recorded is not None:
                    logger.info(
                        "[PAPER IDEMPOTENCY] An order is already recorded under this key; "
                        "returning it without placing a second one. order=%s",
                        recorded.get("id"),
                    )
                    return self._order_body(
                        recorded, repo.get_fills(sb, uid, order_id=recorded["id"])
                    )

            positions, _ = self._positions_of(position_rows)
            open_positions = {
                symbol_key: position
                for symbol_key, position in positions.items()
                if position.is_open
            }

            # ── CANONICAL RISK EVALUATION ──
            # Unchanged in every threshold and every message; only the figures it reads are
            # persisted ones now.
            from backend_app.routers.risk import (
                get_user_risk_settings_store,
                is_user_kill_switched,
                record_risk_violation,
            )

            if is_user_kill_switched(uid):
                record_risk_violation(
                    uid,
                    "KILL_SWITCH_ACTIVE",
                    "Trading halted: Emergency Risk Kill Switch is active",
                )
                raise ValueError("Trading halted: Risk Kill Switch is active")

            user_risk = get_user_risk_settings_store(uid)
            max_daily_loss = self._exact(
                user_risk.get("max_daily_loss", 50000.0), "max_daily_loss"
            )
            max_positions = int(user_risk.get("max_positions", 20))

            realized_pnl = accounting.to_decimal(
                account_row.get("realized_pnl"), "realized_pnl"
            )
            realized_loss = -min(Decimal("0"), realized_pnl)
            if realized_loss >= max_daily_loss:
                record_risk_violation(
                    uid,
                    "MAX_DAILY_LOSS_EXCEEDED",
                    f"Daily loss ${realized_loss:.2f} reached limit of ${max_daily_loss:.2f}",
                )
                raise ValueError(
                    f"Risk limit exceeded: Max Daily Loss limit reached "
                    f"(${realized_loss:.2f} >= ${max_daily_loss:.2f})"
                )

            cur_positions_count = len(open_positions)
            if clean_sym not in open_positions and cur_positions_count >= max_positions:
                record_risk_violation(
                    uid,
                    "MAX_POSITIONS_EXCEEDED",
                    f"Open positions ({cur_positions_count}) reached limit of {max_positions}",
                )
                raise ValueError(
                    f"Risk limit exceeded: Max open positions reached "
                    f"({cur_positions_count} >= {max_positions})"
                )

            avail_bal = accounting.to_decimal(
                account_row.get("available_balance"), "available_balance"
            )

            # Resolve the execution price. A caller-supplied price wins; otherwise the retained
            # reference table answers, which is what makes this path usable while task 24.x's
            # measured feed does not exist yet.
            if price is not None and self._exact(price, "price") > Decimal("0"):
                exec_price = self.config.price(self._exact(price, "price"))
            else:
                exec_price = self.config.price(
                    REFERENCE_PRICES.get(clean_sym, REFERENCE_PRICE_DEFAULT)
                )

            # Strategy-Level Limits Enforcement - thresholds and messages unchanged.
            from backend_app.routers.risk import _user_strategy_limits

            user_strat_limits = _user_strategy_limits.get(uid, {})
            if strategy_id and strategy_id in user_strat_limits:
                strat_limit = user_strat_limits[strategy_id]
                allowed_syms = [
                    self._clean_symbol(s)
                    for s in strat_limit.get("allowed_symbols", [])
                    if s
                ]
                if allowed_syms and clean_sym not in allowed_syms:
                    record_risk_violation(
                        uid,
                        "SYMBOL_RESTRICTED",
                        f"Symbol {clean_sym} not in allowed symbols list for strategy "
                        f"{strategy_id}",
                    )
                    raise ValueError(
                        f"Risk limit exceeded: Symbol {clean_sym} is not allowed for strategy "
                        f"{strategy_id}"
                    )

                max_pos_size = self._exact(
                    strat_limit.get("max_position_size", 1000000.0), "max_position_size"
                )
                prop_notional = qty_dec * exec_price
                if prop_notional > max_pos_size:
                    record_risk_violation(
                        uid,
                        "MAX_POSITION_SIZE_EXCEEDED",
                        f"Order notional ${prop_notional:.2f} exceeds strategy limit "
                        f"${max_pos_size:.2f}",
                    )
                    raise ValueError(
                        f"Risk limit exceeded: Order size ${prop_notional:.2f} exceeds maximum "
                        f"strategy limit ${max_pos_size:.2f}"
                    )

            if clean_type == "limit":
                estimated_cost = self.config.money(
                    qty_dec * exec_price * (Decimal("1") + self.default_fee_rate)
                )
                if clean_side == "buy" and avail_bal < estimated_cost:
                    raise ValueError(
                        f"Insufficient virtual balance (${avail_bal:.2f}) for limit order "
                        f"estimated cost (${estimated_cost:.2f})"
                    )
                return self._place_resting_limit_order(
                    sb,
                    uid,
                    account_id=account_id,
                    symbol=clean_sym,
                    side=clean_side,
                    quantity=qty_dec,
                    limit_price=exec_price,
                    estimated_cost=estimated_cost,
                    idempotency_key=idempotency_key,
                )

            # Market order -> accepted and filled in the same call, as before.
            order_row, already_recorded = self._insert_and_accept(
                sb,
                uid,
                account_id=account_id,
                symbol=clean_sym,
                side=clean_side,
                order_type="market",
                quantity=qty_dec,
                limit_price=None,
                reference_price=exec_price,
                idempotency_key=idempotency_key,
            )
            if already_recorded:
                return self._order_body(
                    order_row, repo.get_fills(sb, uid, order_id=order_row["id"])
                )
            return await self._execute_fill(
                sb, uid, order_row, quantity=qty_dec, price=exec_price
            )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except accounting.InvariantViolation as exc:
            logger.error("A paper fill was refused by an invariant: %s", exc)
            raise PaperError(
                PAPER_INVARIANT_VIOLATION, details={"invariant": exc.invariant}
            ) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "order placement") from exc

    def _insert_and_accept(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Optional[Decimal],
        reference_price: Optional[Decimal],
        idempotency_key: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Insert one order at ``CREATED`` and move it to ``ACCEPTED``.

        Two statements, because ``CREATED`` is the only origin the transition guard admits and
        ``CREATED -> FILLED`` is not one of Requirement 16.2's transitions. The order is on the
        book at ``ACCEPTED`` - ``legacy_status = 'OPEN'`` - which is the state
        ``?status=OPEN`` has always meant.

        Returns:
            ``(order_row, already_recorded)``. ``already_recorded`` is ``True`` when the insert
            lost the idempotency-index race, in which case the row is the order that **won** and
            the caller returns it untouched instead of accepting or filling it a second time
            (Requirement 16.8).
        """
        created, already_recorded = self._insert_order_or_recorded(
            sb,
            uid,
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            reference_price=reference_price,
            idempotency_key=idempotency_key,
        )
        if already_recorded:
            return created, True
        return (
            repo.update_order(
                sb,
                user_id=uid,
                order_id=created["id"],
                order_state=PaperOrderState.ACCEPTED,
                expected_state=PaperOrderState.CREATED,
            ),
            False,
        )

    def _insert_order_or_recorded(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Optional[Decimal],
        reference_price: Optional[Decimal],
        idempotency_key: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        """Insert the order, or return the one an idempotency key already holds.

        Requirement 16.8, in the case the probe in :meth:`place_order` cannot cover: two first
        requests carrying the same key can both read no order and both insert, and
        ``uq_paper_order_idem_default`` refuses one of them. That refusal is the guarantee
        working, so the loser re-reads and answers with the winner rather than raising - which is
        what makes a retried intent produce exactly one order.

        A conflict on an order with **no** idempotency key is re-raised: without a key there is
        no recorded order to answer with, and inventing one would be worse than the conflict.
        """
        try:
            return (
                repo.insert_order(
                    sb,
                    account_id=account_id,
                    user_id=uid,
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    quantity=quantity,
                    fingerprint=self._fingerprint(
                        uid,
                        symbol,
                        side,
                        order_type,
                        quantity,
                        limit_price or reference_price,
                    ),
                    order_state=PaperOrderState.CREATED,
                    limit_price=limit_price,
                    reference_price=reference_price,
                    idempotency_key=idempotency_key,
                ),
                False,
            )
        except repo.PaperConcurrencyConflict:
            if not idempotency_key:
                raise
            recorded = repo.probe_idempotency_key(
                sb,
                user_id=uid,
                idempotency_key=idempotency_key,
                account_id=account_id,
            )
            if recorded is None:
                raise
            logger.info(
                "[PAPER IDEMPOTENCY] A concurrent request recorded this key first; answering "
                "with its order. order=%s",
                recorded.get("id"),
            )
            return recorded, True

    def _place_resting_limit_order(
        self,
        sb: Any,
        uid: str,
        *,
        account_id: str,
        symbol: str,
        side: str,
        quantity: Decimal,
        limit_price: Decimal,
        estimated_cost: Decimal,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        """Record a resting limit order and lock a buy's funds. Returns the order body.

        The order is inserted **before** the money moves, so a duplicate key is refused by the
        index before anything is locked, and a lock that then fails leaves the order at
        ``CREATED`` - not on the book - rather than accepted with no reservation behind it.
        That ordering is what stands in for the single transaction PostgREST cannot give
        (Requirement 24.6's limitation, recorded in ``paper_repository``).

        Only a **buy** locks funds, which is the existing behaviour: a sell that opens a short
        is charged at fill time. ``paper_accounting.lock`` refuses a lock that would drive
        ``available_balance`` below zero (Requirement 18.4) instead of detecting it afterwards.
        """
        created, already_recorded = self._insert_order_or_recorded(
            sb,
            uid,
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_type="limit",
            quantity=quantity,
            limit_price=limit_price,
            reference_price=limit_price,
            idempotency_key=idempotency_key,
        )
        if already_recorded:
            # A concurrent request already recorded this key. Its order is on the book with its
            # own reservation behind it, so nothing is locked a second time.
            return self._order_body(
                created, repo.get_fills(sb, uid, order_id=created["id"])
            )

        if side == "buy" and estimated_cost > Decimal("0"):
            occurred_at = self._get_timestamp()
            before_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
            before = self._account_of(before_row)
            locked = accounting.lock(before, estimated_cost, self.config)
            repo.bump_version(
                sb,
                user_id=uid,
                account_id=account_id,
                expected_version=before_row["version"],
                payload={
                    "available_balance": locked.available_balance,
                    "locked_balance": locked.locked_balance,
                    # A lock moves cash between two columns of the same account, so the sum -
                    # and therefore ``total_equity`` - is unchanged. It is written from the
                    # stored value rather than recomputed, because nothing about the book moved.
                    "total_equity": before.total_equity,
                },
            )
            repo.insert_balance_event(
                sb,
                account_id=account_id,
                user_id=uid,
                cause="ORDER_LOCK",
                available_delta=locked.available_balance - before.available_balance,
                locked_delta=locked.locked_balance - before.locked_balance,
                realized_delta=Decimal("0"),
                available_after=locked.available_balance,
                locked_after=locked.locked_balance,
                realized_after=locked.realized_pnl,
                occurred_at=occurred_at,
            )

        accepted = repo.update_order(
            sb,
            user_id=uid,
            order_id=created["id"],
            order_state=PaperOrderState.ACCEPTED,
            expected_state=PaperOrderState.CREATED,
        )
        return self._order_body(accepted)

    # ══════════════════════════════════════════════════════════════════
    # THE FILL (Requirements 18.5, 18.6, 18.7, 18.8, 18.14)
    # ══════════════════════════════════════════════════════════════════

    async def _execute_fill(
        self,
        sb: Any,
        uid: str,
        order_row: Mapping[str, Any],
        *,
        quantity: Decimal,
        price: Decimal,
        release_from_locked: Decimal = Decimal("0"),
    ) -> Dict[str, Any]:
        """Fill one order, move the money, and persist all of it. Returns the order body.

        The arithmetic is ``paper_accounting.apply_fill`` and nothing here duplicates it: this
        method reads the account and the positions, applies the retained adverse-slippage and
        fee model to get the fill price and the fee, hands both to the engine, **asserts the
        invariants before writing anything** (Requirement 18.14) and then writes what came
        back:

        1. ``paper_fills`` - the fill, with its exact integer Minor_Units fee and slippage.
        2. ``paper_positions`` - the resulting position; two statements for a reversal, so the
           closed leg keeps its own row and the reopened side gets a fresh one.
        3. ``paper_accounts`` - the new balances, guarded by the ``version`` that was read.
        4. ``paper_balance_events`` - the ledger row carrying this fill's three deltas, which
           is where the per-fill realized PnL lives.
        5. ``paper_trades`` - only when a position reached exactly zero (Requirement 18.10).
        6. ``paper_orders`` - the order moved to ``FILLED``, with its filled quantity, average
           fill price, fee and slippage.

        These are six statements and PostgREST offers no transaction to put them in, which
        ``paper_repository`` records as a limitation of the transport rather than glossing over.
        The assert runs first, so an arithmetic refusal writes nothing at all; the database's own
        guards (``uq_paper_fill_event``, ``chk_paper_order_fill_bound``,
        ``chk_paper_balances_non_negative``, ``uq_paper_position_open``) and the version
        predicate are what make a partial sequence detectable rather than silent.
        """
        account_id = str(order_row["account_id"])
        symbol = str(order_row["symbol"])
        side = str(order_row["side"])

        account_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
        account = self._account_of(account_row)
        position_rows = repo.get_positions(
            sb, uid, account_id=account_id, include_closed=True
        )
        positions, _ = self._positions_of(position_rows)
        existing = positions.get(symbol)

        # The retained fill model: adverse slippage only - a buy slips up, a sell slips down -
        # and a fee as a fraction of the filled notional. Task 24.x/25.x own the model itself;
        # this is the one the figures users see today were produced by.
        fill_qty = self.config.qty(quantity)
        reference = self.config.price(price)
        drift = self.config.slippage_rate if side == "buy" else -self.config.slippage_rate
        fill_price = self.config.price(reference * (Decimal("1") + drift))
        fee = self.config.money(fill_qty * fill_price * self.config.fee_rate)
        slippage_cost = self.config.money(abs(fill_price - reference) * fill_qty)
        filled_at = self._get_timestamp()

        # A release can never exceed what is actually locked; ``apply_fill`` would refuse the
        # fill outright (Requirement 18.4), and the estimate was computed at the limit price
        # while the lock was quantized when it was taken.
        release = min(
            self.config.money(release_from_locked), account.locked_balance
        )

        result = accounting.apply_fill(
            account,
            positions,
            {"symbol": symbol, "side": side},
            quantity=fill_qty,
            price=fill_price,
            fee=fee,
            config=self.config,
            filled_at=filled_at,
            release_from_locked=release,
        )
        # Requirement 18.14: the invariants are asserted on the computed state BEFORE the first
        # statement, so a breach leaves the stored rows untouched rather than needing a rollback
        # PostgREST cannot give.
        accounting.assert_invariants(
            result.account,
            result.positions,
            accounting.last_validated_prices(result.positions.values()),
            self.config,
        )

        fill_row = repo.insert_fill(
            sb,
            order_id=order_row["id"],
            user_id=uid,
            fill_event_id=f"exec_paper_{uuid.uuid4().hex[:12]}",
            quantity=fill_qty,
            price=fill_price,
            fee_minor=self._minor(fee, "fee_minor"),
            slippage_minor=self._minor(slippage_cost, "slippage_minor"),
            filled_at=filled_at,
        )

        new_position = result.position
        if (
            existing is not None
            and existing.is_open
            and existing.side != new_position.side
        ):
            # A reversal through zero. The old side is persisted closed in its own row - its
            # history is not overwritten by the side that replaced it - and the new side is
            # then inserted, which the released partial unique index allows.
            repo.upsert_position(
                sb,
                account_id=account_id,
                user_id=uid,
                symbol=symbol,
                side=existing.side,
                size=Decimal("0"),
                entry_price=existing.entry_price,
                opened_at=existing.opened_at or filled_at,
                current_price=fill_price,
                unrealized_pnl=Decimal("0"),
                price_at=filled_at,
                closed_at=filled_at,
            )
        repo.upsert_position(
            sb,
            account_id=account_id,
            user_id=uid,
            symbol=symbol,
            side=new_position.side,
            size=new_position.size,
            entry_price=new_position.entry_price,
            opened_at=new_position.opened_at or filled_at,
            current_price=new_position.current_price,
            unrealized_pnl=new_position.unrealized_pnl,
            price_at=filled_at,
            closed_at=new_position.closed_at,
        )

        repo.bump_version(
            sb,
            user_id=uid,
            account_id=account_id,
            expected_version=account_row["version"],
            payload={
                "available_balance": result.account.available_balance,
                "locked_balance": result.account.locked_balance,
                "realized_pnl": result.account.realized_pnl,
                "total_equity": result.account.total_equity,
                "last_price_at": filled_at,
                "stale": False,
            },
        )
        repo.insert_balance_event(
            sb,
            account_id=account_id,
            user_id=uid,
            cause="FILL",
            available_delta=result.available_delta,
            locked_delta=result.locked_delta,
            realized_delta=result.realized_delta,
            available_after=result.account.available_balance,
            locked_after=result.account.locked_balance,
            realized_after=result.account.realized_pnl,
            fill_id=fill_row["id"],
            occurred_at=filled_at,
        )

        if result.closed_trade is not None:
            closed = result.closed_trade
            repo.insert_trade(
                sb,
                account_id=account_id,
                user_id=uid,
                symbol=closed.symbol,
                side=closed.side,
                quantity=closed.quantity,
                entry_price=closed.entry_price,
                exit_price=closed.exit_price,
                realized_pnl=closed.realized_pnl,
                fee_minor=self._minor(closed.fee, "fee_minor"),
                opened_at=closed.opened_at or filled_at,
                closed_at=closed.closed_at or filled_at,
            )

        filled_order = repo.update_order(
            sb,
            user_id=uid,
            order_id=order_row["id"],
            order_state=PaperOrderState.FILLED,
            expected_state=PaperOrderState(order_row["order_state"]),
            filled_quantity=fill_qty,
            avg_fill_price=fill_price,
            fee_minor=self._minor(fee, "fee_minor"),
            slippage_minor=self._minor(slippage_cost, "slippage_minor"),
        )

        logger.info(
            "[PAPER_TRADE] tenant=%s user=%s order=%s exec=%s sym=%s side=%s qty=%s px=%s "
            "fee=%s pnl=%s",
            uid,
            uid,
            filled_order.get("id"),
            fill_row.get("fill_event_id"),
            symbol,
            side.upper(),
            fill_qty,
            fill_price,
            fee,
            result.realized_pnl,
        )
        return self._order_body(filled_order, [fill_row])

    # ══════════════════════════════════════════════════════════════════
    # CANCELLING (Requirement 16.2, 18.4)
    # ══════════════════════════════════════════════════════════════════

    async def cancel_order(
        self,
        user_id: str,
        order_id: str,
        *,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel a resting paper limit order and release the capital it locked.

        Cancelling an already cancelled order is still an idempotent no-op, cancelling a filled
        one still raises ``ValueError("Cannot cancel an already filled order")``, and the
        transition itself is guarded by the state the order was read in - so two concurrent
        cancels do not both release the funds.

        **One behaviour is deliberately different.** Another tenant's order is now *not found*
        rather than ``PermissionError``: ``user_id`` is a predicate on the read, so the row is
        never fetched, and answering "forbidden" for an order that exists while answering "not
        found" for one that does not is itself the existence disclosure Requirement 21.4 closes.

        THIS CANCELS DEFAULT-ACCOUNT ORDERS ONLY
        ---------------------------------------
        The read is narrowed to ``session_id IS NULL`` (``read_order(..., session_id=None)``),
        which is the same default-account scope every other retained ``/api/paper/*`` endpoint
        answers from - :meth:`get_orders`, :meth:`get_positions`, :meth:`check_limit_orders` and
        :meth:`reset_account` all narrow to that one book, and Requirement 17.12 is that the
        retained endpoints keep those semantics.

        A Paper_Session's order is therefore *not found* here, exactly as an unknown id is: same
        status, same code, same message, and ``details`` carrying only the identifier the caller
        supplied. Scoped by ``(user_id, order_id)`` alone this path would have moved a session's
        order to ``CANCELLED`` and released capital on the SESSION's account while writing no
        ``paper_events`` row for it - bypassing the state machine
        ``backend/paper/paper_session_service.py`` owns and leaving a hole in the session's audit
        trail (Requirements 17.7, 17.15, 19.2). Session orders are cancelled by the session's own
        reset, which takes the transition and writes the event.
        """
        uid = str(user_id)
        sb = self._client(access_token)

        try:
            order = repo.read_order(sb, uid, order_id, session_id=None)
            if order is None:
                raise ValueError(f"Order {order_id} not found")

            status = str(order.get("legacy_status"))
            if status == PaperOrderStatus.CANCELLED.value:
                return self._order_body(
                    order, repo.get_fills(sb, uid, order_id=order["id"])
                )
            if status == PaperOrderStatus.FILLED.value:
                raise ValueError("Cannot cancel an already filled order")
            if status != PaperOrderStatus.OPEN.value:
                raise ValueError(f"Cannot cancel order in status '{status}'")

            account_id = str(order.get("account_id"))
            if (
                str(order.get("side")) == "buy"
                and str(order.get("order_type")) == "limit"
                and order.get("limit_price") is not None
            ):
                occurred_at = self._get_timestamp()
                before_row = repo.lock_account_for_update(sb, uid, account_id=account_id)
                before = self._account_of(before_row)
                reserved = self.config.money(
                    accounting.to_decimal(order["quantity"], "quantity")
                    * accounting.to_decimal(order["limit_price"], "limit_price")
                    * (Decimal("1") + self.default_fee_rate)
                )
                # Never more than is actually locked. The old implementation credited the
                # estimate and floored ``locked_balance`` at zero, which could return more cash
                # than was ever reserved; ``unlock`` refuses to overdraw the locked column
                # (Requirement 18.4).
                release = min(reserved, before.locked_balance)
                unlocked = accounting.unlock(before, release, self.config)
                repo.bump_version(
                    sb,
                    user_id=uid,
                    account_id=account_id,
                    expected_version=before_row["version"],
                    payload={
                        "available_balance": unlocked.available_balance,
                        "locked_balance": unlocked.locked_balance,
                        "total_equity": before.total_equity,
                    },
                )
                repo.insert_balance_event(
                    sb,
                    account_id=account_id,
                    user_id=uid,
                    cause="ORDER_UNLOCK",
                    available_delta=unlocked.available_balance - before.available_balance,
                    locked_delta=unlocked.locked_balance - before.locked_balance,
                    realized_delta=Decimal("0"),
                    available_after=unlocked.available_balance,
                    locked_after=unlocked.locked_balance,
                    realized_after=unlocked.realized_pnl,
                    occurred_at=occurred_at,
                )

            cancelled = repo.update_order(
                sb,
                user_id=uid,
                order_id=order["id"],
                order_state=PaperOrderState.CANCELLED,
                expected_state=PaperOrderState(order["order_state"]),
            )
            return self._order_body(
                cancelled, repo.get_fills(sb, uid, order_id=cancelled["id"])
            )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except accounting.InvariantViolation as exc:
            logger.error("A paper cancellation was refused by an invariant: %s", exc)
            raise PaperError(
                PAPER_INVARIANT_VIOLATION, details={"invariant": exc.invariant}
            ) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "order cancellation") from exc

    # ══════════════════════════════════════════════════════════════════
    # THE LIMIT MATCHING ENGINE
    # ══════════════════════════════════════════════════════════════════

    async def check_limit_orders(
        self,
        symbol: str,
        current_price: Decimal,
        user_id: Optional[str] = None,
        *,
        access_token: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fill the identity's resting limit orders in ``symbol`` that ``current_price`` triggers.

        The fill conditions are unchanged: a buy fills when the price is at or below its limit,
        a sell when it is at or above, and the fill runs through the same
        :meth:`_execute_fill`. A buy's reservation is released and then spent, so the locked
        capital is not spent twice.

        ``user_id`` IS REQUIRED, AND THAT IS THE ONE SIGNATURE CHANGE
        -----------------------------------------------------------
        The in-memory matcher walked ``self._orders.values()`` - every tenant's orders in one
        process dictionary. There is no such read here and there must not be: ``user_id`` is a
        predicate on every statement, row-level security is scoped to one identity, and a
        cross-tenant scan would need an unscoped read of ``paper_orders`` that
        ``paper_repository`` deliberately does not offer (Requirements 21.2, 21.5). So the
        identity is now an argument, and omitting it is refused rather than silently answering
        "nothing matched" - which would look like a working matcher that never fills anything.

        Nothing in the application called this method; ``tests/test_paper_trading_lifecycle.py``
        was its only caller. The per-session matcher tasks 26.x/27.x add is what drives fills
        for a Paper_Session.
        """
        if user_id is None:
            raise ValueError(
                "check_limit_orders requires the user_id whose resting orders are to be "
                "matched: paper orders are persisted per tenant and every read carries user_id "
                "as a predicate, so there is no cross-tenant scan to fall back on"
            )

        uid = str(user_id)
        sb = self._client(access_token)
        clean_sym = self._clean_symbol(symbol)
        cur_px = self.config.price(self._exact(current_price, "current_price"))
        filled_orders: List[Dict[str, Any]] = []

        try:
            account = repo.get_or_create_account(
                sb, uid, repo.DEFAULT_CURRENCY, None, initial_capital=self.default_capital
            )
            resting = repo.get_orders(
                sb,
                uid,
                account_id=account["id"],
                legacy_status=PaperOrderStatus.OPEN.value,
                symbol=clean_sym,
            )

            for order in resting:
                if str(order.get("order_type")) != "limit":
                    continue
                if order.get("limit_price") is None:
                    logger.error(
                        "A resting paper limit order carries no limit price and cannot be "
                        "matched; it is left on the book. order=%s",
                        order.get("id"),
                    )
                    continue

                order_side = str(order.get("side"))
                limit_px = accounting.to_decimal(order["limit_price"], "limit_price")
                qty = accounting.to_decimal(order["quantity"], "quantity")

                should_fill = (order_side == "buy" and cur_px <= limit_px) or (
                    order_side == "sell" and cur_px >= limit_px
                )
                if not should_fill:
                    continue

                reserved = (
                    self.config.money(
                        qty * limit_px * (Decimal("1") + self.default_fee_rate)
                    )
                    if order_side == "buy"
                    else Decimal("0")
                )
                filled_orders.append(
                    await self._execute_fill(
                        sb,
                        uid,
                        order,
                        quantity=qty,
                        price=cur_px,
                        release_from_locked=reserved,
                    )
                )
        except repo.PaperConcurrencyConflict as exc:
            raise self._conflict(exc) from exc
        except accounting.InvariantViolation as exc:
            logger.error("A paper limit fill was refused by an invariant: %s", exc)
            raise PaperError(
                PAPER_INVARIANT_VIOLATION, details={"invariant": exc.invariant}
            ) from exc
        except repo.PaperPersistenceError as exc:
            raise self._read_failed(exc, "limit order matching") from exc

        return filled_orders


# ══════════════════════════════════════════════════════════════════════════
# THE SINGLETON
# ══════════════════════════════════════════════════════════════════════════
#
# Retained exactly as it was. ``routers/risk.py``, ``routers/paper_trading.py`` and
# ``backend/dashboard_aggregation_service.py`` all import ``get_paper_trading_service`` by name,
# and the instance now holds configuration only - no balance, no position, no order - so one
# process-wide instance is a configuration holder rather than a store.

_paper_service_instance: Optional[PaperTradingService] = None


def get_paper_trading_service() -> PaperTradingService:
    global _paper_service_instance
    if _paper_service_instance is None:
        _paper_service_instance = PaperTradingService()
    return _paper_service_instance
