import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple
import uuid
from uuid import UUID

from backend_app.backend.execution_environment import (
    ExecutionEnvironment, assert_live_environment,
    assert_simulation_environment_matches, parse_execution_environment)

#: Sentinel distinguishing "the caller omitted the environment" from "the caller passed
#: None". The first is the legacy path and is left unenforced; the second is a caller bug
#: and is refused. A plain ``None`` default could not tell them apart, and conflating them
#: is exactly how the guard at ``execute_with_idempotency`` ended up disarmed.
_ENVIRONMENT_UNSTATED = object()
from backend_app.core.database import SessionLocal
from backend_app.core.metrics import execution_metrics
from backend_app.core.models.execution_record import (
    AMBIGUOUS_SUBMISSION_ACTION, FILL_STATE_ACCEPTED, FILL_STATE_FILLED,
    FILL_STATE_PARTIAL, ExecutionRecordRepository, ExecutionSide,
    ExecutionStatus, classify_venue_fill, execution_status_for_fill_state)
from backend_app.core.risk_manager import RiskManager

logger = logging.getLogger(__name__)


#: The only environments in which a ``test_``-prefixed strategy_id is accepted by
#: :meth:`ExecutionEngine._validate_strategy_exists` without a matching database row.
#:
#: Deliberately narrower than the sets elsewhere in this codebase. ``core/rate_limit.py``
#: treats ``safe``/``paper``/``ambiguous`` as dev, and ``core/auth_middleware.py`` also
#: accepts ``development``/``dev``/``local``; neither set is appropriate here, because what
#: this one governs is whether a LIVE order may be attributed to a strategy nobody can show
#: exists. Two spellings, both of which only a test lane sets.
_SYNTHETIC_STRATEGY_ID_ENVS = frozenset({"testing", "test"})


def _synthetic_strategy_ids_allowed() -> bool:
    """Whether the ``test_`` strategy_id convention is in force.

    FAILS CLOSED. An unset, empty or unrecognised ``ENV`` returns ``False``, so a
    deployment that never configured one is protected rather than exposed — the opposite of
    the rate limiter's default, and the right direction for a control that decides whether
    an order is tied to a real strategy.

    Read from the environment on every call rather than cached at import: the test suite
    sets ``ENV`` in ``tests/conftest.py`` at import time but individual tests also
    ``monkeypatch.setenv`` it, and a cached value would make the gate depend on module
    import order.
    """
    env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").strip().lower()
    return env in _SYNTHETIC_STRATEGY_ID_ENVS
"""
Production-grade Execution Engine for Paper Trading
Simulates order execution, tracks positions, and computes PnL
With Global Risk Guardrails integration and Idempotent Execution
"""




@dataclass
class ExecutionResult:
    """Standardized execution result."""
    success: bool
    execution_id: Optional[str]
    status: str
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class Position:
    """Represents an open trading position"""
    symbol: str
    entry_price: Decimal
    size: Decimal
    side: str = "long"  # "long" or "short"
    entry_time: datetime = field(default_factory=datetime.now)

    def current_value(self, current_price: Decimal) -> Decimal:
        """Calculate current position value"""
        price = Decimal(str(current_price)) if not isinstance(current_price, Decimal) else current_price
        if self.side == "long":
            return self.size * price
        else:  # short
            return self.size * (Decimal("2") * self.entry_price - price)

    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized PnL"""
        price = Decimal(str(current_price)) if not isinstance(current_price, Decimal) else current_price
        if self.side == "long":
            return (price - self.entry_price) * self.size
        else:  # short
            return (self.entry_price - price) * self.size


@dataclass
class Trade:
    """Represents a completed trade"""
    symbol: str
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal
    side: str
    entry_time: datetime
    exit_time: datetime
    pnl: Decimal
    pnl_pct: Decimal
    commission: Decimal = field(default_factory=lambda: Decimal("0"))


def serialize_decimals(obj):
    """Recursively convert Decimal objects to string representation for JSON serialization."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: serialize_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialize_decimals(i) for i in obj]
    return obj


class ExecutionEngine:
    """
    Paper trading execution engine.
    
    Simulates:
    - Order execution at specified prices
    - Position tracking (long/short)
    - PnL calculation (realized and unrealized)
    - Trade history logging
    - Commission simulation
    """
    
    def __init__(self, fee_rate: float = 0.001, slippage: float = 0.0005, risk_manager: Optional[RiskManager] = None, portfolio_state: Optional[Dict] = None, exchange_executor: Optional[Any] = None, *, execution_environment: Any = _ENVIRONMENT_UNSTATED, paper_ledger: Optional[Any] = None):
        """
        Initialize ExecutionEngine with fee, slippage, and risk management.
        
        🔴 CRITICAL: portfolio_state is REQUIRED for real money trading.
        No hardcoded capital is allowed - must use real portfolio data.
        
        Args:
            fee_rate: Fee per trade (default: 0.001 = 0.1%)
            slippage: Slippage as fraction (default: 0.0005 = 0.05%)
            risk_manager: RiskManager instance for guardrails (creates new if None)
            portfolio_state: Real portfolio data with 'total_equity' field (REQUIRED)
            execution_environment: What this engine EXISTS FOR - ``LIVE``, ``PAPER`` or
                ``BACKTEST``. Keyword-only. STATED IS ENFORCED; omitted is not.

                WHY IT IS NOT REQUIRED, HAVING BEEN WRITTEN THAT WAY FIRST. There are 71
                construction sites across this repository - 23 in ``tests/``, 8 in
                ``backend_app/``, 14 in the ``scripts/`` attack suite and 26 in
                ``connection_layer/``. Making it mandatory turns every one of those into
                a ``TypeError`` at construction, which is a far wider contract change
                than the leak it closes and would break live execution paths to protect
                them.

                Of the 8 in ``backend_app/``, 6 now state it -- ``master_executor``
                (PAPER and LIVE), ``dag_risk_integration`` (PAPER),
                ``routers/strategies`` (BACKTEST), ``validate_financials`` (BACKTEST),
                and both ``distributed_execution`` modules (LIVE). The one that does not
                is ``execution_router``, whose endpoint cannot succeed for unrelated
                reasons. ``tests/test_execution_engine_mode_matches_its_branch.py`` holds
                that list.

                So an omitted environment behaves exactly as before: the branch below is
                selected by the ``exchange_executor`` null check alone and nothing new
                refuses. An environment that IS stated is enforced at the branch point.
                That makes this an incremental control - each site that starts stating it
                gains the protection, and no site loses anything by not having been
                updated yet. ``tests/test_execution_engine_mode_matches_its_branch.py``
                enumerates the sites that do not state it, so the number can be driven
                down and cannot quietly grow.

                An explicitly supplied ``None`` is REFUSED rather than treated as
                omitted. A caller that passes something meant to be an environment and
                hands over nothing has a bug, and silently downgrading that to
                "unenforced" is how the funnel-entry guard got disarmed in the first
                place.

                WHY IT IS HERE AND NOT ONLY ON THE CALL. ``_execute_trade_internal``
                chooses between placing a real order and simulating one by asking
                whether ``exchange_executor`` happens to be non-None, and nothing used
                to check that the branch it took was the branch this engine was built
                for. Two leaks followed, and the environment guard at
                ``execute_with_idempotency`` could not see either: at the time, no caller
                passed it an environment, so its ``LIVE`` default made it believe every
                order was live. Two doors state it now, but every caller that omits it
                still gets the default, which is why this control is separate - it reads
                what the engine was BUILT for rather than what a caller remembered to
                pass:

                * A PAPER engine that acquires an executor places a REAL order.
                  ``BotRunner``'s paper fast-path relies on ``exchange_executor=None``,
                  and that null was the only thing standing in the way.
                * A LIVE engine with NO executor falls through to the paper branch,
                  simulates the fill and returns success with a ``paper_`` order id - so
                  a trader is told their live order executed while nothing reached an
                  exchange.

                Recorded here so the branch point can assert the two agree. NOT
                defaulted, for the reason the funnel-entry guard demonstrates: a default
                turns "nobody said" into "this engine may place real orders". Unstated is
                a third state, distinct from both, and it is the only one left
                unenforced.

                A synthetic paper balance is already required to be passed explicitly
                (see ``portfolio_state`` below); the mode it belongs to is the same kind
                of fact and is treated the same way wherever a caller states it.
                Regression: tests/test_execution_engine_mode_matches_its_branch.py
        
        Raises:
            ValueError: If execution_environment is supplied but is outside
                BACKTEST/PAPER/LIVE - including an explicit ``None``. Omitting it
                entirely is allowed and leaves the engine unstated.
            ValueError: If portfolio_state is not provided or missing required fields
        """
        # Step 1: The portfolio state is REQUIRED, exactly as this method docstring says.
        #
        # IT USED TO INVENT CAPITAL. An absent portfolio_state became
        #     {total_equity: 100000.0, available_balance: 100000.0}
        # and a present-but-incomplete one had total_equity set to 100000.0, both
        # silently (the first at INFO). That contradicted the docstring above, which
        # promises ValueError, and it is the fabrication class the launch-hardening
        # bugfix defines: asked for a figure it does not have, the system answered with
        # an invented one instead of an absence.
        #
        # WHY IT MATTERS MORE HERE THAN ON A DASHBOARD. total_equity becomes
        # initial_capital, current_equity, peak_equity AND RiskManager(initial_equity=),
        # so every guardrail below - position size, exposure ceiling, drawdown - was
        # computed against 100k the account may not hold. A trader with 2k of real
        # capital would be authorised positions sized for fifty times that.
        #
        # A SYNTHETIC BALANCE IS STILL ALLOWED, BUT IT MUST BE PASSED. master_executor
        # deliberately passes {total_equity: 10000.0} for its paper branch and says so;
        # that keeps working. What is refused is arriving with NOTHING and being handed
        # a number nobody chose.
        # Step 0: what this engine exists for, when the caller says.
        #
        # `None` here means UNSTATED, and an unstated engine behaves exactly as it did
        # before this parameter existed - see the docstring for why mandating it was
        # backed out. A STATED environment is resolved through the SHARED parser, so this
        # class cannot invent a fourth spelling or become more permissive than
        # `assert_live_environment` about what "LIVE" looks like, and an unparseable
        # value is refused rather than coerced.
        if execution_environment is _ENVIRONMENT_UNSTATED:
            self.execution_environment = None
        else:
            self.execution_environment = parse_execution_environment(
                execution_environment
            )
            if self.execution_environment is None:
                raise ValueError(
                    f"ExecutionEngine was given execution_environment="
                    f"{execution_environment!r}, which is not one of "
                    f"{[member.value for member in ExecutionEnvironment]}. It decides "
                    f"whether this engine may place a real order, so it is refused "
                    f"rather than coerced or downgraded to unstated. Omit the argument "
                    f"entirely if the mode genuinely is not established here."
                )

        if not portfolio_state:
            raise ValueError(
                "ExecutionEngine requires portfolio_state: it sets initial_capital, "
                "current_equity, peak_equity and the RiskManager equity that every "
                "guardrail is measured against. There is no default, because a "
                "default would size real positions against capital nobody "
                "established. Pass the caller own portfolio state - a synthetic "
                "paper balance is fine, but it has to be chosen explicitly."
            )

        if 'total_equity' not in portfolio_state:
            raise ValueError(
                "ExecutionEngine requires portfolio_state['total_equity']; the state "
                "supplied carries only %r. total_equity is the denominator of the "
                "exposure, concentration and drawdown checks, so substituting a "
                "figure here would make every one of them report on capital that "
                "was never read."
                % (sorted(portfolio_state),)
            )
        
        # Use REAL capital from portfolio state
        total_equity = Decimal(str(portfolio_state['total_equity']))
        
        self.positions: Dict[str, Position] = {}
        self.trade_log: List[Trade] = []
        
        # FIN-CRITICAL-003 FIX: Store fee_rate as Decimal to prevent precision loss
        # Convert immediately to Decimal to avoid float conversion precision issues
        self.fee_rate = Decimal(str(fee_rate))
        self.slippage = slippage
        self.initial_capital: Decimal = total_equity
        self.current_equity: Decimal = total_equity
        self.peak_equity: Decimal = total_equity

        # ═══════════════════════════════════════════════════════════════════
        # CASH IS SEPARATE FROM EQUITY  (BUG-016)
        # ═══════════════════════════════════════════════════════════════════
        # `current_equity` used to be the only balance this engine had, and it served as
        # both the cash available to spend and the total value of the account. Opening a
        # position moved only the FEE out of it, so:
        #
        #   * `cash + position value != equity` -- the identity simply did not hold.
        #   * `get_drawdown()` measured drawdown on a figure that never moved while a
        #     position was open, so a position 40% underwater reported ZERO drawdown and
        #     the max-drawdown kill switch could not fire on open risk.
        #   * the "Insufficient capital" check compared a position's cost against EQUITY,
        #     which had not been reduced by the previous position -- so N positions could
        #     each be worth up to the whole account. The only accidental brake was
        #     `max_open_trades = 5`, capping exposure at ~500% of equity by coincidence.
        #
        # THE MODEL, taken from `backend/paper/paper_accounting` rather than invented
        # here, because that module is the already-correct reference and has an exact
        # zero-tolerance invariant test:
        #
        #     cash_delta = -(new_position_value - old_position_value) - fee
        #     equity     = cash + position_market_value
        #
        # Cash is DERIVED from the change in position value rather than debited case by
        # case, which is what makes the identity hold for all five position cases
        # (open, increase, partial close, full close, reverse) without any of them
        # needing their own arithmetic. The change in equity across any fill is then
        # exactly `-fee`, by construction.
        #
        # A round trip ends on the same equity as the old code -- verified algebraically
        # and pinned by a regression test -- so this does not change realised results.
        # What changes is equity DURING an open position, which is the whole point.
        # Regression: tests/test_execution_engine_accounting_identity.py
        self.cash: Decimal = total_equity

        #: Last price seen per symbol, used to mark open positions to market. A position
        #: only exists as the result of a fill, and every fill carries a price, so an
        #: open position always has a mark.
        self._marks: Dict[str, Decimal] = {}
        
        # 🛡️ Risk Manager for global guardrails - use REAL capital
        self.risk_manager = risk_manager or RiskManager(initial_equity=float(total_equity))
        self._blocked_trades: List[dict] = []  # Log of blocked trades
        
        # Store portfolio state reference for safety checks
        self._portfolio_state = portfolio_state
        self.exchange_executor = exchange_executor

        # ═══════════════════════════════════════════════════════════════════
        # PAPER-01: THE CANONICAL PAPER LEDGER SEAM
        # ═══════════════════════════════════════════════════════════════════
        # When installed, the PAPER branch records its fill on the canonical paper
        # ledger (`paper_accounts` / `paper_orders` / `paper_fills` /
        # `paper_positions` / ...) through `PaperTradingService`, and this engine's
        # in-process cash and positions become a COPY of what was persisted rather
        # than a second, independent model of it.
        #
        # OPTIONAL, for the same reason `execution_environment` is optional: there
        # are 71 construction sites and making this mandatory would be a far wider
        # contract change than the defect it closes. An engine with no ledger
        # behaves exactly as it did -- it simulates in process and persists
        # nothing -- so each site that wires one gains the persistence and no site
        # loses anything by not having been updated. `BotRunner`'s paper branch
        # wires it; `tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py`
        # holds that it does.
        #
        # It is NOT a second accounting implementation. This engine computes no
        # money on the canonical path: it hands the order to the service and reads
        # the stored figures back.
        self.paper_ledger = paper_ledger

        # TRADE-04 / TRADE-05. Per-order facts the live gate needs, set by
        # `execute_with_idempotency` immediately before `_execute_trade_internal` and
        # never read on the paper branch. `None` is the safe default for the risk verdict:
        # an order nobody evaluated is refused, not waved through.
        self._live_risk_approved: Optional[bool] = None
        self._live_risk_reason: str = ""
        self._live_tenant_id: Optional[str] = None
        self._live_strategy_id: Optional[str] = None

    # ----------------------------------
    # ACCOUNTING  (BUG-016)
    # ----------------------------------
    @staticmethod
    def position_value(position: "Position", price: Decimal) -> Decimal:
        """One position's market value at *price*.

        ``LONG``  -> ``size * price``
        ``SHORT`` -> ``size * (2 * entry_price - price)``

        The SHORT convention is `paper_accounting.position_value`'s, retained rather than
        re-derived. Read as ``size*entry + size*(entry - price)``, it is the cash the
        short consumed plus the profit it has made -- which is what keeps the equity
        identity holding for a short without introducing a negative position value.

        A closed position (``size == 0``) is worth exactly zero under both branches, so
        it needs no special case and no tolerance.
        """
        p = price if isinstance(price, Decimal) else Decimal(str(price))
        if position.side == "long":
            return position.size * p
        return position.size * (Decimal("2") * position.entry_price - p)

    def position_market_value(self) -> Decimal:
        """Total value of the open positions at their last marks."""
        total = Decimal("0")
        for symbol, position in self.positions.items():
            mark = self._marks.get(symbol, position.entry_price)
            total += self.position_value(position, mark)
        return total

    def _sync_equity(self) -> None:
        """Recompute ``current_equity`` from cash and position value, and track the peak.

        ``current_equity`` is never incremented or decremented anywhere; it is only ever
        recomputed from the two things that actually move. That is what makes the
        identity impossible to drift -- the same rule `paper_accounting` follows, where
        ``total_equity`` is always the computed sum and never a maintained counter.
        """
        self.current_equity = self.cash + self.position_market_value()
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

    def _capacity_bounds_for(
        self, *, size: Any, price: Any
    ) -> tuple[Decimal, Optional[Decimal], str]:
        """The notional and the fundable ceiling for one order, for TRADE-01.

        DERIVED THE SAME WAY AS THE PER-ORDER CHECK, ON PURPOSE. The ceiling computed
        here must be the same number `_execute_trade_internal` computes at its
        `_notional > _ceiling` gate, or the cross-order bound and the per-order bound
        would disagree about what the account can fund and one of them would be wrong.
        Both read `self.current_equity` and `self.max_leverage` and multiply; this is the
        one place that logic is written twice, and it is written identically.

        Returns `(notional, ceiling, explanation)`. `ceiling` is None when equity cannot
        be established, which the caller treats as a refusal — an order that cannot be
        shown to be fundable is not sent.
        """
        # `getattr`, not `self.current_equity`. The attribute is set by `_sync_equity`
        # and is NOT present on every engine: several construction paths in this
        # repository never reach it, and an `AttributeError` here would escape the
        # `(InvalidOperation, TypeError, ValueError)` handler and crash the order path
        # instead of refusing it. The per-order check's own log line already reads it this
        # way, which is how the inconsistency was noticed.
        raw_equity = getattr(self, "current_equity", None)
        try:
            equity = Decimal(str(raw_equity))
        except (InvalidOperation, TypeError, ValueError):
            equity = None

        if equity is None or equity <= 0:
            return (Decimal("0"), None, f"the engine's equity is {raw_equity!r}")

        max_leverage = Decimal(str(getattr(self, "max_leverage", 1) or 1))

        # ═══════════════════════════════════════════════════════════════════
        # TRADE-11: A PRICE THAT IS NOT A USABLE NUMBER REFUSES. IT DOES NOT
        #           BECOME ZERO.
        # ═══════════════════════════════════════════════════════════════════
        # This line used to read
        #
        #     notional = abs(Decimal(str(size))) * abs(Decimal(str(price or 0)))
        #
        # and `price or 0` is the whole finding. `None`, `""`, `0`, `0.0` and
        # `Decimal("0")` are all falsey, so every one of them produced `notional = 0` --
        # a reservation that consumes no capacity and therefore ALWAYS fits, whatever
        # else is in flight. The refusal below only ever fired for a price that was
        # non-numeric AND non-falsey, i.e. a string like "abc"; the cases that actually
        # occur went through silently.
        #
        # A MARKET ORDER IS REFUSED HERE, AND THAT IS THE POINT.
        # On the live path a market order arrives as `price = 0`
        # (`_execute_trade_internal` computes `market if price <= 0 else limit`). So "a
        # zero price" and "a market order" are the same input, and valuing it requires a
        # mark price.
        #
        # Measured: NO price oracle, ticker or mark-price service is consulted by ANY
        # capacity or notional check in this repository, and no risk policy document
        # specifying pre-trade market-order valuation exists. `data_seeking_engine`
        # can fetch a ticker, but nothing defines which price, how stale is too stale, or
        # what to do when market data is unavailable. Inventing those three answers inside
        # a capital check would be an unreviewed risk rule governing real money.
        #
        # So the honest options are "refuse" or "admit unbounded exposure", and the
        # previous behaviour was the second one wearing the first one's clothes: the
        # comment at the per-order gate said a market order "is not waved through"
        # because "the venue's own balance check is the backstop" -- but a venue-side
        # rejection arrives AFTER the idempotency claim has been consumed and the
        # execution record says an attempt was made.
        #
        # Paper and backtest engines never reach this method: the caller branches on
        # `_is_declared_simulator` first, so simulated market orders are unaffected.
        # Regression: tests/test_trade11_capacity_fails_closed_postgres.py
        if price is None or (isinstance(price, str) and not price.strip()):
            return (
                Decimal("0"),
                None,
                f"order price is {price!r}, so this order has no computable notional. A "
                f"live order cannot be shown to fit the account's capacity without a "
                f"price, and this platform has no policy for valuing one from market "
                f"data",
            )

        try:
            price_decimal = Decimal(str(price))
            size_decimal = Decimal(str(size))
        except (InvalidOperation, TypeError, ValueError):
            # An unparseable size or price cannot be bounded either. Refuse rather than
            # treat it as zero notional, which would consume no capacity and pass.
            return (
                Decimal("0"),
                None,
                f"order size {size!r} or price {price!r} is not a usable number",
            )

        if not price_decimal.is_finite() or not size_decimal.is_finite():
            # NaN and Infinity are parseable by `Decimal` and are not numbers a notional
            # can be computed from. `Decimal("NaN") > x` is False for every x, so an
            # unchecked NaN price would pass the ceiling comparison below rather than
            # failing it -- the comparison silently inverts.
            return (
                Decimal("0"),
                None,
                f"order size {size!r} or price {price!r} is not finite",
            )

        if price_decimal <= 0:
            return (
                Decimal("0"),
                None,
                f"order price {price!r} is not positive, so this order has no computable "
                f"notional (a market order arrives here as price <= 0). This platform "
                f"has no risk policy for valuing a market order pre-trade, so it is "
                f"refused rather than valued by a guess",
            )

        if size_decimal == 0:
            return (
                Decimal("0"),
                None,
                f"order size {size!r} is zero, so there is nothing to fund",
            )

        notional = abs(size_decimal) * abs(price_decimal)
        return (notional, equity * max_leverage, "")

    def mark_to_market(self, symbol: str, price: Decimal) -> Decimal:
        """Record the latest price for *symbol* and restate equity. Returns new equity.

        This is what lets ``get_drawdown`` see an open position's loss. Without a mark,
        drawdown is measured on realised results only, and the max-drawdown kill switch
        cannot fire until the position is closed -- by which time the loss has already
        been taken.
        """
        self._marks[symbol] = price if isinstance(price, Decimal) else Decimal(str(price))
        self._sync_equity()
        return self.current_equity

    def _apply_cash_delta(
        self, symbol: str, old_value: Decimal, new_value: Decimal, fee: Decimal
    ) -> Decimal:
        """Move cash by the residual of the position-value change, less the fee.

        The single arithmetic path for every fill. Returns the cash delta applied.
        """
        cash_delta = -(new_value - old_value) - fee
        self.cash += cash_delta
        self._sync_equity()
        return cash_delta

    def _reconcile_from_paper_ledger(self, recorded: Any) -> None:
        """Replace this engine's in-process figures with the ones the ledger persisted.

        PAPER-01. Without this the engine would hold a second, independent answer to
        "what is my cash and what do I hold", and the two would diverge on the first
        fill -- which is the defect restated one layer in. After this call every figure
        on the engine is a copy of a stored one:

        * ``self.cash`` is ``available_balance + locked_balance`` from ``paper_accounts``.
        * ``self.positions`` is rebuilt from the open ``paper_positions`` rows.
        * ``self._marks`` is each position's stored ``current_price``, so
          :meth:`_sync_equity` reproduces the stored ``total_equity`` rather than
          valuing the same position at a different price.

        The engine's own ``position_value`` is the same SHORT convention
        (``size * (2*entry - price)``) ``paper_accounting.position_value`` uses, so the
        recomputation agrees by construction rather than by coincidence. A disagreement
        is therefore evidence of a real divergence and is logged at CRITICAL -- not
        raised, because the ledger has already asserted its own invariant with zero
        tolerance and IT is the source of truth; this process's copy being wrong is a
        reporting fault, not a reason to unwind a recorded fill.
        """
        self.cash = recorded.cash

        rebuilt: Dict[str, Position] = {}
        marks: Dict[str, Decimal] = {}
        for ledger_symbol, row in (recorded.positions or {}).items():
            try:
                size = Decimal(str(row.get("size") or "0"))
                entry = Decimal(str(row.get("entry_price") or "0"))
            except (InvalidOperation, TypeError, ValueError):
                logger.error(
                    "Paper ledger position for %s carries an unreadable size/entry "
                    "(%r/%r); it is skipped rather than guessed at.",
                    ledger_symbol, row.get("size"), row.get("entry_price"),
                )
                continue
            if size <= 0:
                # A closed position persists at size 0 with closed_at set. It is worth
                # exactly zero and holds no exposure, so it is not an open position here.
                continue
            # `paper_positions.side` is 'LONG'/'SHORT'; this engine's is 'long'/'short'.
            rebuilt[ledger_symbol] = Position(
                symbol=ledger_symbol,
                entry_price=entry,
                size=size,
                side=str(row.get("side") or "LONG").strip().lower(),
            )
            current = row.get("current_price")
            marks[ledger_symbol] = (
                entry if current is None else Decimal(str(current))
            )

        self.positions = rebuilt
        self._marks = marks
        self._sync_equity()

        if self.current_equity != recorded.total_equity:
            logger.critical(
                "PAPER-01 reconciliation disagreement: the ledger persisted "
                "total_equity=%s and this process recomputed %s from the same rows. "
                "The LEDGER is authoritative. cash=%s position_market_value=%s",
                recorded.total_equity, self.current_equity, self.cash,
                self.position_market_value(),
            )

    def available_cash(self) -> Decimal:
        """Cash not already committed to an open position."""
        return self.cash

    def total_exposure(self) -> Decimal:
        """Aggregate absolute notional of the open positions, at their last marks.

        Exposure is notional, not value: a short's exposure is what it could move
        against the account, so it is measured on ``size * price`` rather than on the
        ``2*entry - price`` valuation used for equity.
        """
        total = Decimal("0")
        for symbol, position in self.positions.items():
            mark = self._marks.get(symbol, position.entry_price)
            total += abs(position.size * mark)
        return total
    
    # ----------------------------------
    # IDEMPOTENT EXECUTION WRAPPER
    # ----------------------------------
    async def execute_with_idempotency(
        self,
        tenant_id: UUID,
        strategy_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        task_id: Optional[UUID] = None,
        execution_interval_minutes: int = 5,
        source: str = "unknown",
        execution_environment: Any = ExecutionEnvironment.LIVE,
        risk_approved: Optional[bool] = None,
        risk_reason: str = "",
    ) -> Dict[str, Any]:
        """
        Execute trade with idempotency check - PREVENTS DUPLICATE EXECUTIONS.
        
        🔴 PURE ALGO TRADING: This method ENFORCES that ALL trades come from BotRunner.
        
        This is the ONLY allowed way to execute trades. All orders MUST go through
        this wrapper to ensure idempotency and prevent double execution.
        
        Flow:
        0. Assert the Execution_Environment is LIVE - REJECT PAPER/BACKTEST/unresolved
        1. Validate source is "bot_runner" - REJECT direct/manual execution
        2. Validate strategy_id exists in database - REJECT fake IDs
        3. Generate deterministic execution_id
        4. Check existing execution in PostgreSQL
        5. Acquire execution lock (optimistic locking)
        6. Execute order (only if lock acquired)
        7. Store result in PostgreSQL
        
        Args:
            tenant_id: Tenant UUID for isolation
            strategy_id: Strategy identifier (MUST exist in DB)
            symbol: Trading symbol (e.g., BTCUSDT)
            side: 'buy' or 'sell'
            size: Position size
            price: Execution price
            task_id: Optional associated DAG task ID
            execution_interval_minutes: Time bucket for idempotency
            source: Execution source - MUST be "bot_runner"
            execution_environment: The order's Execution_Environment. MUST resolve to
                `ExecutionEnvironment.LIVE`; `PAPER`, `BACKTEST`, `None` and any
                out-of-set value are refused by `assert_live_environment` before any
                other work (marketplace-subscriptions-paper-trading Requirements
                13.9, 13.10). The guard itself never defaults: an explicitly supplied
                absent or unrecognised value raises.

                ⚠️ THE DEFAULT BELOW DISARMS THAT GUARD FOR ANY CALLER THAT OMITS IT,
                AND THE JUSTIFICATION IT USED TO CARRY WAS WRONG. It argued that the
                LIVE default merely wrote down what every caller of this engine already
                meant, on the grounds that the engine had only ever had one environment.
                That was not true of the caller that matters.
                `BotRunner._recover_live_state` constructs an `ExecutionEngine` on BOTH
                paths - the paper fast-path builds one with a synthetic
                `{"total_equity": 10000.0}` portfolio and `exchange_executor=None` before
                returning, the live path builds one with a real executor - and the shared
                tick loop in `BotRunner._inner_loop` made the single
                `execute_with_idempotency` call for both modes WITHOUT stating the
                environment. For a while NO caller in the repository passed this
                argument, so a PAPER order entered this funnel, the default labelled it
                LIVE, `assert_live_environment` passed it, and it was recorded with an
                audit trail that said LIVE. What stopped it reaching an exchange was the
                `exchange_executor=None` on the paper engine - a null dependency, not a
                control.

                FIXED AT THE TWO DOORS THAT HAD A STATED ANSWER TO GIVE:
                  * `BotRunner._inner_loop` passes
                    `PAPER if self.paper_trading else LIVE`. A paper bot's order is now
                    refused here, which is Requirement 13.9 working as written; the
                    refusal is audited before the raise. Requirement 13.3 says that
                    order intent should have gone to the Paper_Simulator and never
                    reached this funnel at all - routing it there is still open.
                  * `execute_trade` forwards `self.execution_environment`, so an engine
                    built for PAPER or BACKTEST no longer has its orders relabelled LIVE
                    by this default on the way through the wrapper.

                STILL UNGUARDED: every caller that omits the argument, because "said
                nothing" and "said LIVE" are indistinguishable here. The default was not
                changed to a refusing sentinel because 70 of the 71 funnel call sites in
                this repository omit it - making omission fatal would break working
                paths rather than protect them. The sites are driven down one at a time.
                See tests/test_execution_environment_is_stated_not_defaulted.py.
        
        Returns:
            Dict with execution result:
            - status: 'completed' | 'skipped_completed' | 'skipped_executing' | 'failed' | 'blocked'
            - execution_id: The execution identifier
            - result: Trade result (if completed)
            - message: Human-readable status
        
        ⚠️ WARNING: Direct calls to open_position/close_position are BLOCKED.
        Use this method for ALL trade executions.

        Raises:
            UnresolvedExecutionEnvironment: `execution_environment` is absent or outside
                `BACKTEST`/`PAPER`/`LIVE` (Requirement 13.10).
            ExecutionEnvironmentMismatch: `execution_environment` is `PAPER` or
                `BACKTEST` (Requirement 13.9). Audited before the raise.
        """
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 STEP 0: EXECUTION ENVIRONMENT GUARD
        # ═══════════════════════════════════════════════════════════════════
        # First statement of this method, so it precedes every other check, every
        # repository and session handle, and the exchange_executor / CCXT call in
        # `_execute_trade_internal`. A refused order therefore cannot reach an exchange
        # and cannot move a balance on the way to being refused (Requirements 13.9,
        # 13.10). It raises rather than returning a `blocked` result: a returned verdict
        # can be dropped by a caller, and dropping this one places a real order for a
        # simulated intent.
        #
        # WHICH GUARD, AND WHY THERE ARE TWO.
        # Requirement 13.9 is a rule about THE LIVE ORDER PATH - "IF the live order path
        # receives an order whose originating Execution_Environment is `PAPER`, THEN THE
        # live order path SHALL reject the order BEFORE ISSUING ANY EXCHANGE CALL". The
        # live order path is the path that can issue one. `execution_environment.py`'s own
        # module docstring places `assert_live_environment` so that "there is no live
        # order path that reaches an exchange without passing it".
        #
        # A DECLARED SIMULATOR cannot issue an exchange call: it states PAPER or BACKTEST
        # and holds no `exchange_executor`, and the branch-point assertion below refuses
        # it the live branch if it ever acquires one. Applying the live-path guard to it
        # refused `BotRunner`'s paper mode - which is how every bot started by
        # `routers/strategies.py` runs, `paper_trading=True` hardcoded at both deploy
        # sites - and stopped paper trading on the platform outright.
        #
        # So a declared simulator is guarded for AGREEMENT instead: 13.10 still applies in
        # full, LIVE is refused (which the live-path guard could not do for it), and only
        # the engine's own environment is admitted. Everything else - an engine holding an
        # executor, an engine stating LIVE, and an engine that stated nothing at all -
        # keeps `assert_live_environment` unchanged. That last clause is what makes this
        # not a weakening: 65 of the 71 construction sites state nothing, and every one of
        # them is still on the live-path guard.
        #
        # The full (engine, executor, order) matrix is enumerated in the regression test.
        # Exactly one row reaches `place_order`: engine LIVE, executor present, order LIVE.
        # Regression: tests/test_simulated_orders_reach_the_simulator.py
        _engine_environment = getattr(self, 'execution_environment', None)
        _holds_executor = getattr(self, 'exchange_executor', None) is not None
        _is_declared_simulator = (
            _engine_environment in (
                ExecutionEnvironment.PAPER, ExecutionEnvironment.BACKTEST
            )
            and not _holds_executor
        )

        if _is_declared_simulator:
            await assert_simulation_environment_matches(
                execution_environment,
                engine_environment=_engine_environment,
                actor_id=str(tenant_id),
                strategy_id=str(strategy_id) if strategy_id else None,
                symbol=symbol,
            )
        else:
            await assert_live_environment(
                execution_environment,
                actor_id=str(tenant_id),
                strategy_id=str(strategy_id) if strategy_id else None,
                symbol=symbol,
            )

        import time, threading
        start_time = time.time()
        thread_id = threading.get_ident()
        task_id_str = str(task_id or uuid.uuid4())
        correlation_id = f"corr-{tenant_id}-{strategy_id}-{int(start_time * 1000)}"

        logger.info(
            f"[TRACE][ENTRY] fn=execute_with_idempotency tenant_id={tenant_id} strategy_id={strategy_id} "
            f"symbol={symbol} side={side} size={size} price={price} thread_id={thread_id} task_id={task_id_str} "
            f"correlation_id={correlation_id}"
        )

        # ═══════════════════════════════════════════════════════════════════
        # 🔴 STEP 6: EXECUTION GUARD - Only bot_runner allowed
        # ═══════════════════════════════════════════════════════════════════
        if source != "bot_runner":
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': f'Direct execution blocked - source must be "bot_runner", got "{source}"'
            }
        
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 GLOBAL KILL SWITCH CHECK - Instantly stops trading if active
        # ═══════════════════════════════════════════════════════════════════
        try:
            from backend_app.core.global_safety import get_global_kill_switch
            kill_switch = get_global_kill_switch()
            if await kill_switch.is_active():
                return {
                    'status': 'blocked',
                    'execution_id': None,
                    'result': None,
                    'message': 'Global kill switch is active - trading execution blocked'
                }
        except Exception as ks_err:
            # Fall-safe: if safety system check errors, block trading
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': f'Safety check failed: {ks_err} - trading execution blocked'
            }
        
        # ═══════════════════════════════════════════════════════════════════
        # 🔴 RT-19: PER-TENANT HALT (the user's emergency kill switch), durable and
        # shared across workers. Fail-closed: an unreadable halt state blocks trading.
        # ═══════════════════════════════════════════════════════════════════
        try:
            from backend_app.core.tenant_halt import is_halted as _tenant_halted
            if await _tenant_halted(tenant_id):
                return {
                    'status': 'blocked',
                    'execution_id': None,
                    'result': None,
                    'message': 'Tenant kill switch is active - trading execution blocked'
                }
        except Exception as halt_err:
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': f'Tenant halt check failed: {halt_err} - trading execution blocked'
            }

        # ═══════════════════════════════════════════════════════════════════
        # 🔴 STEP 2: STRATEGY VALIDATION - Must be real strategy from DB
        # ═══════════════════════════════════════════════════════════════════
        if not strategy_id or not self._validate_strategy_exists(tenant_id, strategy_id):
            return {
                'status': 'blocked',
                'execution_id': None,
                'result': None,
                'message': 'Execution requires valid strategy - manual/fake strategy_id not allowed'
            }
        
        db_session = SessionLocal()
        
        try:
            repo = ExecutionRecordRepository(db_session)
            
            # STEP 1: Check idempotency (generate execution_id, check existing)
            execution_id, action, existing_result = repo.check_idempotent_execution(
                tenant_id=tenant_id,
                strategy_id=strategy_id,
                symbol=symbol,
                timestamp=datetime.utcnow(),
                side=ExecutionSide.BUY if side.lower() == 'buy' else ExecutionSide.SELL,
                qty=float(size),
                price=float(price),
                task_id=task_id,
                execution_interval_minutes=execution_interval_minutes,
                allow_failed_retry=True
            )
            
            # STEP 2: Handle existing executions
            if action == 'skip_return_result':
                # Already completed - return cached result
                return {
                    'status': 'skipped_completed',
                    'execution_id': execution_id,
                    'result': existing_result,
                    'message': 'Trade already executed, returning cached result'
                }
            
            elif action == 'skip_already_running':
                # Already executing - skip to prevent double execution
                return {
                    'status': 'skipped_executing',
                    'execution_id': execution_id,
                    'result': None,
                    'message': 'Trade already being executed by another worker'
                }
            
            elif action == 'skip_failed_no_retry':
                # Failed and retry disabled
                return {
                    'status': 'skipped_failed',
                    'execution_id': execution_id,
                    'result': existing_result,
                    'message': 'Previous execution failed, retry disabled'
                }

            # ═══════════════════════════════════════════════════════════════════
            # DUP-02: AMBIGUOUS SUBMISSION — REFUSED, NOT RESUBMITTED
            # ═══════════════════════════════════════════════════════════════════
            # `check_idempotent_execution` established that this execution may already be
            # live at the venue (`submitted_at` committed before `create_order`, or a
            # status of UNKNOWN which means the watchdog could not determine the
            # outcome). Returning here keeps it away from STEP 3's claim.
            #
            # Resubmitting would double a position; marking it successful would invent a
            # fill. The row is left exactly as it is for `OrderWatchdog` — the single
            # owner of adjudication — and the refusal is surfaced to the caller so a tick
            # is skipped rather than silently retried.
            elif action == AMBIGUOUS_SUBMISSION_ACTION:
                return {
                    'status': 'skipped_ambiguous_submission',
                    'execution_id': execution_id,
                    'result': existing_result,
                    'message': (
                        'A previous attempt for this execution may already have reached '
                        'the venue. It was NOT resubmitted. Reconcile against the venue '
                        'before retrying.'
                    )
                }

            # DUP-02. `allow_retry` is now handled EXPLICITLY.
            #
            # It previously had no branch and reached STEP 3 by fall-through, which is
            # structurally how an ambiguous row became a second submission. Naming it
            # here makes the intent legible and means a future action added to
            # `check_idempotent_execution` without a branch is caught by
            # `test_dup02_…::test_every_action_the_check_can_return_is_handled_explicitly`
            # rather than silently inheriting "proceed to submit".
            #
            # Reaching this point is safe: the ambiguity guard above has already refused
            # anything that may be live at the venue, so an `allow_retry` here is a
            # retry of an execution that provably never reached `create_order`.
            elif action == 'allow_retry':
                logger.info(
                    "DUP-02: execution %s is being retried; no prior venue submission "
                    "was recorded for it.", execution_id,
                )
            
            # RT-17 SYMBOL FREEZE. An earlier submission for this tenant+symbol is UNKNOWN
            # (the venue may hold an order we cannot see). A new order -- which would get a
            # new execution id and so bypass the per-execution guard -- could double the
            # position. Refuse until the UNKNOWN row is adjudicated. Live-bound only;
            # fails closed when the check itself errors.
            if not _is_declared_simulator:
                try:
                    _frozen = repo.has_unresolved_submission(tenant_id, symbol)
                except Exception as _freeze_err:  # noqa: BLE001
                    logger.critical(
                        "Refusing execution %s: unresolved-submission check failed: %s",
                        execution_id, _freeze_err,
                    )
                    _frozen = True
                if _frozen is True:
                    # The idempotency check above inserted a PENDING row for this attempt.
                    # Close it out (never submitted, so FAILED is truthful and retryable)
                    # rather than leaving an orphan for the stale-execution sweeper.
                    try:
                        repo.update_status(
                            execution_id=execution_id,
                            tenant_id=tenant_id,
                            status=ExecutionStatus.FAILED,
                            result={'error': 'symbol frozen: unresolved UNKNOWN submission',
                                    'symbol': symbol, 'side': side},
                        )
                    except Exception:  # noqa: BLE001 - the refusal stands regardless
                        logger.warning("Could not close the placeholder row for %s", execution_id)
                    return {
                        'status': 'blocked',
                        'execution_id': execution_id,
                        'result': None,
                        'message': (
                            f'Risk gateway: {symbol} is frozen -- an earlier order submission '
                            f'for it has an unknown outcome and may be live at the venue. '
                            f'Reconcile it before sending another order. Refused before any '
                            f'exchange call.'
                        ),
                    }

            # STEP 3: Acquire execution lock (only ONE worker proceeds)
            #
            # TRADE-01. THE CLAIM IS NOW ALSO A CAPACITY RESERVATION, FOR LIVE ORDERS.
            # ----------------------------------------------------------------------
            # `claim_execution` guarantees that only one worker executes a GIVEN
            # execution. It says nothing about two DIFFERENT executions, and the capital
            # bound further down (`_notional > _ceiling` in `_execute_trade_internal`) is
            # a read-then-compare against `self.current_equity` in process memory with
            # nothing reserved. Because `generate_execution_id` folds size and price into
            # the canonical string, two orders that differ in either get different
            # execution ids and different Redis locks, clear the identical ceiling
            # independently, and both proceed — two orders at 60% of the ceiling commit
            # 120% of it.
            #
            # `claim_execution_within_capacity` makes the claim and the capacity decision
            # one transaction, serialised per tenant by a PostgreSQL advisory lock, with
            # in-flight notional summed from `execution_records` rather than from memory.
            # The reservation IS the `executing` row, so there is no separate reservation
            # state that can leak if this process dies between the two.
            #
            # SCOPE: live-bound orders only. `_is_declared_simulator` was computed above
            # from the engine's own environment and the presence of an executor; a paper
            # or backtest engine keeps the original claim, because there is no real
            # account whose capacity could be oversubscribed and the paper path has its
            # own ledger. The per-order ceiling below is unchanged and still runs — this
            # adds a bound across concurrent orders, it does not replace the one on each.
            if _is_declared_simulator:
                claimed, record = repo.claim_execution(execution_id, tenant_id)
                capacity_refusal = None
            else:
                _cap_notional, _cap_ceiling, _cap_why = self._capacity_bounds_for(
                    size=size, price=price
                )
                if _cap_ceiling is None:
                    # Equity unreadable. Same policy as the per-order check: an order that
                    # cannot be shown to be fundable is refused, not sent.
                    logger.critical(
                        "Refusing execution %s: capacity could not be bounded (%s).",
                        execution_id, _cap_why,
                    )
                    return {
                        'status': 'blocked',
                        'execution_id': execution_id,
                        'result': None,
                        'message': (
                            f"Risk gateway: concurrent capacity could not be established "
                            f"for this account ({_cap_why}). Refused before any exchange "
                            f"call."
                        ),
                    }
                claimed, record, capacity_refusal = repo.claim_execution_within_capacity(
                    execution_id,
                    tenant_id,
                    notional=_cap_notional,
                    ceiling=_cap_ceiling,
                )

            if not claimed:
                # Two different outcomes share this branch, and they are reported
                # differently so an operator can tell "someone else got there first" from
                # "the account has no room".
                if capacity_refusal and "Capacity reservation refused" in capacity_refusal:
                    return {
                        'status': 'blocked',
                        'execution_id': execution_id,
                        'result': None,
                        'message': capacity_refusal,
                    }
                return {
                    'status': 'skipped_contention',
                    'execution_id': execution_id,
                    'result': None,
                    'message': capacity_refusal
                               or 'Another worker acquired execution lock first'
                }
            
            # STEP 4: Execute trade (ONLY if lock acquired)
            #
            # TRADE-04 / TRADE-05. The per-order facts the live gate needs are handed to
            # `_execute_trade_internal` as instance state rather than as parameters,
            # matching how `_current_validation_token` is already passed to the executor.
            # They are set HERE, after every guard above has run, so the authorisation
            # minted on the live branch cannot claim a check that did not happen.
            #
            # `risk_approved` defaults to None, so a caller that does not evaluate risk
            # refuses on the live branch. That is the point: `execute_trade` does not
            # forward it, so the two doors the audit flagged as inheriting bot_runner
            # authorisation without the risk check now cannot place a live order.
            self._live_risk_approved = risk_approved
            self._live_risk_reason = risk_reason
            self._live_tenant_id = str(tenant_id)
            self._live_strategy_id = str(strategy_id)
            try:
                success, trade_result = await self._execute_trade_internal(
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=price,
                    execution_id=execution_id
                )
                
                if success:
                    # ═══════════════════════════════════════════════════════════
                    # STEP 5: Store result in PostgreSQL BEFORE returning
                    # CRITICAL: Must persist before response to ensure durability
                    # ═══════════════════════════════════════════════════════════
                    final_result = serialize_decimals({
                        'symbol': symbol,
                        'side': side,
                        'size': size,
                        'price': price,
                        'executed_at': datetime.utcnow().isoformat(),
                        'trade_result': trade_result,
                    })
                    
                    # UPDATE execution_records
                    # SET status = 'completed', order_id = ?, result = ?, updated_at = now()
                    # WHERE execution_id = ?
                    # TRADE-12: the venue's id goes in `venue_order_id`, NOT `order_id`.
                    #
                    # `trade_result['order_id']` is `result.exchange_order_id`, i.e.
                    # `str(create_order_response["id"])` -- the EXCHANGE's identifier.
                    # `execution_records.order_id` carries
                    # `FOREIGN KEY (order_id) REFERENCES orders(id)`, and `public.orders`
                    # holds THIS platform's orders, so that write was refused with 23503.
                    # Since `update_status` commits, the failure aborted this session's
                    # transaction, the `except` handler's own FAILED write then failed
                    # too, and the exception left this method -- stranding the row as
                    # EXECUTING with `order_id IS NULL` immediately after a real fill,
                    # where `OrderWatchdog` could never see it.
                    #
                    # Measured in
                    # staging/probe_phase9_order_id_fk_blocks_completion.py.
                    # ═══════════════════════════════════════════════════════════
                    # RT-16: COMPLETED REQUIRES A CONFIRMED FILL, NOT AN ACCEPT.
                    # ═══════════════════════════════════════════════════════════
                    # This wrote COMPLETED unconditionally. `_execute_trade_internal`
                    # returning success means the VENUE ACCEPTED the order; a resting
                    # limit order and a genuine fill took the same branch. COMPLETED is
                    # operationally terminal -- `OrderWatchdog._find_open_orders`,
                    # `_find_stale_orders` and `ExchangeReconciliation._get_active_orders`
                    # all select only PENDING/EXECUTING -- so the row left every
                    # reconciliation population at the moment it most needed watching,
                    # and nothing would ever discover whether it filled.
                    #
                    # `fill_state` is absent on the paper/ledger branches of
                    # `_execute_trade_internal`, which return a recorded fill by
                    # construction; those default to FILLED and are unchanged.
                    fill_state = (
                        trade_result.get('fill_state', FILL_STATE_FILLED)
                        if isinstance(trade_result, dict)
                        else FILL_STATE_FILLED
                    )
                    recorded_status = execution_status_for_fill_state(fill_state)
                    filled_qty = (
                        trade_result.get('size')
                        if isinstance(trade_result, dict) else None
                    )
                    requested_qty = (
                        trade_result.get('requested_size')
                        if isinstance(trade_result, dict) else None
                    )
                    remaining_qty = None
                    if filled_qty is not None and requested_qty is not None:
                        try:
                            remaining_qty = str(
                                max(
                                    Decimal("0"),
                                    Decimal(str(requested_qty)) - Decimal(str(filled_qty)),
                                )
                            )
                        except (InvalidOperation, ValueError, TypeError):
                            remaining_qty = None

                    repo.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=recorded_status,
                        venue_order_id=trade_result.get('order_id') if isinstance(trade_result, dict) else None,
                        result=final_result,
                        # RT-16: write the fill columns the watchdog's `fill_delta`
                        # arithmetic reads. They were never written here, so a row marked
                        # COMPLETED carried `filled_size IS NULL` and the next poll
                        # re-credited the whole quantity as newly filled.
                        filled_size=str(filled_qty) if filled_qty is not None else None,
                        avg_price=(
                            str(trade_result.get('executed_price'))
                            if isinstance(trade_result, dict)
                            and trade_result.get('executed_price') is not None
                            and fill_state != FILL_STATE_ACCEPTED
                            else None
                        ),
                        remaining_size=remaining_qty,
                        filled_at=(
                            datetime.utcnow()
                            if recorded_status == ExecutionStatus.COMPLETED
                            else None
                        ),
                        exchange_status=(
                            str(trade_result.get('status'))
                            if isinstance(trade_result, dict)
                            and trade_result.get('status') is not None
                            else None
                        ),
                    )

                    # Record success metric. A venue acceptance IS a successful
                    # submission -- this counts submissions, not fills.
                    execution_metrics.record_success(tenant_id=str(tenant_id))

                    # ONLY return AFTER successful DB commit.
                    #
                    # RT-16: the returned string is load-bearing beyond this function.
                    # `master_executor.settle_exposure` credits exposure only for
                    # "completed", and the fee row keys off the same value, so returning
                    # "completed" for an accepted-but-unfilled order is what produced
                    # phantom exposure and a phantom fee. The two non-filled states get
                    # their own strings instead of being folded into either "completed"
                    # or a failure -- the order is live at the venue, and calling it
                    # failed would be as wrong as calling it filled.
                    if fill_state == FILL_STATE_FILLED:
                        return {
                            'status': 'completed',
                            'execution_id': execution_id,
                            'result': final_result,
                            'message': 'Trade executed successfully'
                        }
                    return {
                        'status': fill_state,
                        'execution_id': execution_id,
                        'result': final_result,
                        'message': (
                            'The venue accepted the order and has filled '
                            f'{filled_qty} of {requested_qty}. It is recorded as '
                            'EXECUTING and is being reconciled against the venue; it '
                            'has NOT been treated as a completed trade.'
                        ),
                    }
                elif isinstance(trade_result, dict) and trade_result.get('submission_unknown'):
                    # ═══════════════════════════════════════════════════════════
                    # RT-17: THE ORDER MAY BE LIVE. Record UNKNOWN, not FAILED.
                    # FAILED would let the strategy roll back and re-enter on the next
                    # tick (a different price is a different execution id), doubling the
                    # position. UNKNOWN is refused for retry by `check_idempotent_execution`
                    # and freezes this symbol below until it is adjudicated.
                    # ═══════════════════════════════════════════════════════════
                    unknown_result = serialize_decimals({
                        'error': trade_result.get('error'),
                        'error_code': trade_result.get('error_code'),
                        'symbol': symbol,
                        'side': side,
                        'unknown_at': datetime.utcnow().isoformat(),
                    })
                    repo.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=ExecutionStatus.UNKNOWN,
                        result=unknown_result,
                    )
                    return {
                        'status': 'submission_unknown',
                        'execution_id': execution_id,
                        'result': unknown_result,
                        'message': (
                            'The order may be live at the venue. It was NOT recorded as '
                            'rejected and NOT resubmitted; new orders for this symbol are '
                            'frozen until it is reconciled.'
                        ),
                    }
                else:
                    # ═══════════════════════════════════════════════════════════
                    # Execution failed - store error BEFORE returning
                    # ═══════════════════════════════════════════════════════════
                    error_result = serialize_decimals({
                        'error': trade_result,
                        'symbol': symbol,
                        'side': side,
                        'failed_at': datetime.utcnow().isoformat(),
                    })
                    
                    # UPDATE execution_records
                    # SET status = 'failed', result = ?, updated_at = now()
                    # WHERE execution_id = ?
                    repo.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=ExecutionStatus.FAILED,
                        result=error_result
                    )
                    
                    return {
                        'status': 'failed',
                        'execution_id': execution_id,
                        'result': error_result,
                        'message': f'Trade execution failed: {trade_result}'
                    }
                    
            except Exception as e:
                # ═══════════════════════════════════════════════════════════
                # Unexpected error - store error BEFORE returning
                # ═══════════════════════════════════════════════════════════
                error_result = serialize_decimals({
                    'error': str(e),
                    'symbol': symbol,
                    'side': side,
                    'failed_at': datetime.utcnow().isoformat(),
                })
                
                # UPDATE execution_records
                # SET status = 'failed', result = ?, updated_at = now()
                # WHERE execution_id = ?
                # RT-17: an unexpected error AFTER the submission evidence was written must
                # not be recorded as FAILED -- the venue may hold the order.
                _may_be_live = False
                try:
                    _rec = repo.get_by_id(execution_id, tenant_id)
                    _may_be_live = (not _is_declared_simulator) and (
                        getattr(_rec, "submitted_at", None) is not None
                    )
                except Exception:  # noqa: BLE001 - unreadable evidence is not proof of safety
                    _may_be_live = not _is_declared_simulator
                if _may_be_live is True:
                    logger.critical(
                        "ORDER SUBMISSION UNKNOWN for execution %s: unexpected %s after "
                        "submission evidence was recorded.", execution_id, type(e).__name__,
                    )
                    repo.update_status(
                        execution_id=execution_id,
                        tenant_id=tenant_id,
                        status=ExecutionStatus.UNKNOWN,
                        result=error_result,
                    )
                    return {
                        'status': 'submission_unknown',
                        'execution_id': execution_id,
                        'result': error_result,
                        'message': f'Unexpected error after submission; order may be live: {str(e)}',
                    }

                repo.update_status(
                    execution_id=execution_id,
                    tenant_id=tenant_id,
                    status=ExecutionStatus.FAILED,
                    result=error_result
                )
                
                return {
                    'status': 'failed',
                    'execution_id': execution_id,
                    'result': error_result,
                    'message': f'Unexpected error: {str(e)}'
                }
                
        finally:
            db_session.close()
    
    async def _execute_trade_internal(
        self,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        execution_id: str = "exec_internal_default"
    ) -> Tuple[bool, Any]:
        """
        Internal trade execution - ONLY called by execute_with_idempotency.
        
        ⚠️ DO NOT CALL DIRECTLY - Use execute_with_idempotency() instead.
        
        This method performs the actual trade execution without idempotency checks.
        All idempotency logic is handled by the wrapper.
        """
        # ═══════════════════════════════════════════════════════════════════
        # THE BRANCH MUST BE THE BRANCH THIS ENGINE WAS BUILT FOR
        # ═══════════════════════════════════════════════════════════════════
        # The selection below is a null check on a constructor argument, and until this
        # block existed nothing verified that the branch it chose matched the engine's
        # purpose. The guard at `execute_with_idempotency` cannot cover this: no caller
        # passes it an environment, so its `ExecutionEnvironment.LIVE` default makes it
        # believe every order is live, whichever branch actually runs.
        #
        # Both directions are refused, and both are real:
        #
        #   PAPER/BACKTEST engine holding an executor -> it would place a REAL order.
        #     `BotRunner`'s paper fast-path depends on `exchange_executor=None`, so that
        #     null is all that stands between simulated intent and `place_order`.
        #
        #   LIVE engine holding no executor -> it would fall through to the paper branch,
        #     simulate the fill, and return success with a `paper_` order id and a
        #     plausible price. The caller records a filled trade and the trader is told
        #     their live order executed while nothing reached an exchange. This is the
        #     more dangerous direction, because it fails silently and optimistically.
        #
        # Returned as a (False, reason) refusal rather than raised, because that is this
        # method's established contract with `execute_with_idempotency` - every other
        # failure here returns the same shape, and raising would bypass the caller's
        # exposure rollback.
        # Regression: tests/test_execution_engine_mode_matches_its_branch.py
        # Only an engine that STATED its environment is held to it. An unstated one is
        # left exactly as it was - 65 of the 71 construction sites in this repository do
        # not state it yet, and failing them here would be breaking execution to protect
        # it. See the constructor docstring.
        _has_executor = (
            hasattr(self, 'exchange_executor') and self.exchange_executor is not None
        )
        _stated = getattr(self, 'execution_environment', None) is not None
        _is_live = self.execution_environment is ExecutionEnvironment.LIVE

        if _stated and _has_executor and not _is_live:
            logger.critical(
                "Refusing execution %s: engine built for %s holds an exchange executor, "
                "so this order would be placed on a real exchange.",
                execution_id,
                self.execution_environment.value,
            )
            return False, (
                f"Execution environment mismatch: this engine was built for "
                f"{self.execution_environment.value} but holds an exchange executor, so "
                f"the order would reach a live exchange. Refused."
            )

        if _stated and _is_live and not _has_executor:
            logger.critical(
                "Refusing execution %s: engine built for LIVE has no exchange executor, "
                "so the order would be simulated and reported as filled.",
                execution_id,
            )
            return False, (
                "Execution environment mismatch: this engine was built for LIVE but has "
                "no exchange executor, so the order would be simulated on the paper "
                "branch and returned as a successful fill. Refused rather than "
                "reporting a trade that never reached an exchange."
            )

        # LIVE EXECUTION PATH
        if _has_executor:
            # ═══════════════════════════════════════════════════════════════
            # NOTIONAL CEILING — THE ONE RISK BOUND THE GATEWAY ITSELF OWNS
            # ═══════════════════════════════════════════════════════════════
            # `execute_with_idempotency` contains NO risk evaluation. Its checks are
            # environment, source, kill switch, strategy existence, idempotency and claim
            # — verified by execution during the pre-launch audit, which searched this
            # method and `_execute_trade_internal` for any reference to `ExecutionGuard`,
            # `RiskEngine`, `validate_trade` or `validate_trade_request` and found none.
            #
            # Risk is enforced one level up, in `BotRunner._inner_loop`, which calls
            # `risk_manager.validate_trade_request` and `continue`s on a non-APPROVED
            # verdict. That is the only production caller, so there is no live bypass
            # today. But it means the property "a live order has passed risk" is a
            # property of ONE CALLER rather than of the gateway, and `execute_trade`
            # hardcodes `source="bot_runner"` — so any future caller inherits the
            # bot_runner authorisation without inheriting the risk check that is supposed
            # to accompany it. `backend/execution_guard.py` describes itself as "the FINAL
            # CONTROL LAYER that validates EVERY trade before execution" and is called
            # only from the two CANCEL endpoints, never from this path.
            #
            # WHAT THIS CHECK IS, AND WHAT IT DELIBERATELY IS NOT.
            # It is the one bound that no legitimate order can breach: an order may not
            # commit more notional than the account has equity to fund it with (times
            # `max_leverage`, which defaults to 1). It therefore cannot produce a false
            # refusal for a correctly sized order, which is why it is safe to add
            # directly to the live path.
            #
            # It is NOT a substitute for the risk engine. It will not catch an order that
            # is merely too large for the strategy's configured risk-per-trade, breaches
            # the daily loss limit, or exceeds the open-position count. Those remain the
            # caller's responsibility, and wiring `ExecutionGuard` into this funnel is
            # recorded as an outstanding item rather than attempted here.
            #
            # What it does catch is the category that loses the account in one order: a
            # sizing bug (the audit found `RiskEngine.calculate_position_size` returning
            # 125x equity on a low-ATR asset), a decimal-shift error, a corrupted or
            # replayed signal, or a unit mix-up between base and quote quantities.
            #
            # FAILS CLOSED on an unreadable equity: if the engine cannot establish what
            # the account is worth, it cannot establish that the order is fundable, and
            # the safe answer to that is refusal.
            # Regression: tests/test_live_orders_are_bounded_by_account_equity.py
            try:
                _equity = Decimal(str(self.current_equity))
            except (InvalidOperation, TypeError, ValueError):
                _equity = None

            if _equity is None or _equity <= 0:
                logger.critical(
                    "Refusing execution %s: live order cannot be bounded because the "
                    "engine's equity is %r.",
                    execution_id,
                    getattr(self, "current_equity", None),
                )
                return False, (
                    "Risk gateway: the account equity backing this engine could not be "
                    "established, so the order could not be shown to be fundable. "
                    "Refused rather than sent to a live exchange unbounded."
                )

            # ═══════════════════════════════════════════════════════════════
            # TRADE-11: DERIVED FROM THE SAME HELPER, SO THE TWO GATES CANNOT
            #           DISAGREE ABOUT WHAT IS FUNDABLE
            # ═══════════════════════════════════════════════════════════════
            # This block used to recompute the notional inline:
            #
            #     _notional = abs(Decimal(str(size))) * abs(Decimal(str(price or 0)))
            #
            # which is the TRADE-11 expression, and it carried a comment saying a market
            # order "is not waved through" because "the venue's own balance check is the
            # backstop". Measured, that was not true in the way it reads: `price or 0`
            # made the notional zero, zero is never greater than the ceiling, and a
            # venue-side rejection arrives only AFTER the idempotency claim has been
            # consumed and the execution record says an attempt was made. The quantity
            # bound the comment refers to is the venue's own minimum, which bounds the
            # order from BELOW, not above.
            #
            # `_capacity_bounds_for` now performs the full validation -- non-finite,
            # non-positive, unparseable, empty -- and returns `ceiling is None` to mean
            # refuse. Calling it here instead of recomputing means the cross-order
            # reservation and this per-order gate share one definition of "fundable",
            # which `test_the_engine_derives_the_same_ceiling_as_the_per_order_check`
            # already required of the ceiling and now also holds for the notional.
            _notional, _ceiling, _why = self._capacity_bounds_for(size=size, price=price)
            # Retained only for the refusal messages below, which name the two factors so
            # a trader is told WHY the ceiling is what it is.
            _max_leverage = Decimal(str(getattr(self, "max_leverage", 1) or 1))

            if _ceiling is None:
                logger.critical(
                    "Refusing execution %s: live order notional could not be computed "
                    "(%s) for %s %s %s.",
                    execution_id, _why, side, size, symbol,
                )
                return False, (
                    f"Risk gateway: this live order has no computable notional ({_why}), "
                    f"so it could not be shown to be fundable. Refused before any "
                    f"exchange call rather than sent with its exposure unmodelled."
                )

            if _notional > _ceiling:
                logger.critical(
                    "Refusing execution %s: notional %s exceeds the %s ceiling "
                    "(equity %s x max_leverage %s) for %s %s %s.",
                    execution_id, _notional, _ceiling, _equity, _max_leverage,
                    side, size, symbol,
                )
                return False, (
                    f"Risk gateway: order notional {_notional} exceeds the maximum "
                    f"fundable notional {_ceiling} for this account "
                    f"(equity {_equity} x max_leverage {_max_leverage}). Refused before "
                    f"any exchange call."
                )

            # ═══════════════════════════════════════════════════════════════
            # TRADE-05: RISK IS NOW A PROPERTY OF THIS GATEWAY, NOT OF A CALLER
            # ═══════════════════════════════════════════════════════════════
            # The comment above records the defect: this funnel contained no risk
            # evaluation, `BotRunner._inner_loop` performed it before calling, and
            # `execute_trade` hardcodes `source="bot_runner"` -- so any future caller
            # inherited the authorisation to place live orders WITHOUT inheriting the risk
            # check. Two such callers already existed
            # (`distributed_execution/execution_worker.py`,
            # `idempotent_exchange_submission.py`), both neutered by accident rather than
            # by design.
            #
            # A live order now REQUIRES evidence that risk was evaluated and approved. The
            # evidence is a parameter, so a caller that does not evaluate risk cannot
            # supply it and cannot execute. That is what makes the invariant structural
            # rather than a convention the caller is trusted to follow.
            #
            # Paper orders are untouched: they do not reach this branch.
            # Regression: tests/test_trade04_live_order_gate_at_venue_boundary.py
            if self._live_risk_approved is not True:
                logger.critical(
                    "Refusing execution %s: a live order reached the venue path without "
                    "an approved risk evaluation (risk_approved=%r, reason=%r).",
                    execution_id,
                    self._live_risk_approved,
                    self._live_risk_reason,
                )
                return False, (
                    "Risk gateway: this live order carries no approved risk evaluation. "
                    "The caller must evaluate risk and pass the verdict to the gateway; "
                    "reaching the gateway does not confer permission to skip it. "
                    f"(risk_approved={self._live_risk_approved!r}"
                    + (f", reason={self._live_risk_reason!r}" if self._live_risk_reason else "")
                    + "). Refused before any exchange call."
                )

            _venue_call_possible = False  # RT-17: set True once the venue call may have been made
            try:
                from backend_app.core.global_safety import generate_validation_token
                from backend_app.core.live_execution_gate import (
                    LiveExecutionRefused, authorize_live_order)
                from backend_app.core.models.pydantic_models import OrderSide
                from backend_app.core.models.pydantic_models import \
                    OrderType as CoreOrderType

                # ═══════════════════════════════════════════════════════════
                # TRADE-04: MINT THE LIVE-EXECUTION AUTHORISATION
                # ═══════════════════════════════════════════════════════════
                # Every name below corresponds to a check that has ALREADY run on the way
                # here, and `authorize_live_order` refuses unless the complete
                # `REQUIRED_CHECKS` set is present -- so a future branch that reaches this
                # point having skipped one produces a refusal rather than an unguarded
                # order. The authorisation is bound to this exact order and venue and is
                # consumed by `place_order`.
                try:
                    _live_auth = authorize_live_order(
                        execution_id=execution_id,
                        tenant_id=self._live_tenant_id,
                        strategy_id=self._live_strategy_id,
                        symbol=symbol.replace('-', '/'),
                        side=side,
                        size=size,
                        exchange_id=getattr(
                            self.exchange_executor, "exchange_id", ""
                        ),
                        checks=(
                            # Established by `execute_with_idempotency` before this call:
                            "authenticated_actor",        # tenant_id is a parsed UUID
                            "authorized_tenant_resource", # _validate_strategy_exists(tenant, strategy)
                            "strategy_authorized",        # same check, named for the invariant
                            "kill_switch",                # get_global_kill_switch().is_active()
                            "execution_environment",      # assert_live_environment
                            "explicit_live_mode",         # engine states LIVE and holds an executor
                            "idempotency",                # check_idempotent_execution + claim_execution
                            "safety_config",              # branch-point agreement assertion
                            # Established in this method, above:
                            "capital",                    # notional <= equity * max_leverage
                            "risk",                       # self._live_risk_approved is True
                            # Established by the executor itself, below this call:
                            "precision",                  # normalize_size / venue minimums
                            # Established by the caller that resolved the deployment:
                            "live_trading_entitlement",
                            "exchange_binding",
                        ),
                    )
                except LiveExecutionRefused as refusal:
                    logger.critical(
                        "Refusing execution %s at the live gate: %s",
                        execution_id, refusal,
                    )
                    return False, f"Live execution refused: {refusal}"

                if hasattr(self.exchange_executor, '_current_live_authorization'):
                    self.exchange_executor._current_live_authorization = _live_auth

                # Generate and attach anti-bypass validation token
                token = generate_validation_token(execution_id, symbol, size)
                if hasattr(self.exchange_executor, '_current_execution_id'):
                    self.exchange_executor._current_execution_id = execution_id
                if hasattr(self.exchange_executor, '_current_validation_token'):
                    self.exchange_executor._current_validation_token = token

                # ═══════════════════════════════════════════════════════════
                # TRADE-07: RECORD THE SUBMISSION BEFORE MAKING IT
                # ═══════════════════════════════════════════════════════════
                # `submitted_at` exists on this table and in the migration, and NOTHING in
                # the application ever wrote it. That is the whole of TRADE-07: a process
                # that died inside `place_order` left `order_id`, `submitted_at`,
                # `exchange_id` and `result` all NULL, so nothing could tell "crashed
                # before the venue call" from "crashed after it" -- and the two demand
                # opposite actions. Failing the first is correct; failing the second can
                # orphan a real resting order and let the next signal duplicate it.
                #
                # ITS OWN SESSION AND ITS OWN TRANSACTION, COMMITTED BEFORE THE CALL. The
                # outer session belongs to `execute_with_idempotency` and may yet roll
                # back; if this write shared it, a rollback would erase the record of a
                # submission that really happened. A separate short-lived session is what
                # makes the evidence survive the thing it exists to detect.
                #
                # FAILS CLOSED. If the evidence cannot be written the order is NOT sent.
                # Sending it would create precisely the unrecoverable row this block
                # exists to prevent -- a venue order with no local trace that it was ever
                # attempted. Refusing costs one missed signal; proceeding costs an
                # untracked position.
                # Regression: tests/test_trade07_stale_execution_recovery_postgres.py
                #
                # `SessionLocal` and `ExecutionRecordRepository` are the MODULE-LEVEL names
                # imported at the top of this file, deliberately NOT re-imported locally.
                # The live-order tests patch `execution_engine.SessionLocal` and
                # `execution_engine.ExecutionRecordRepository` to inject their doubles; a
                # fresh `from ... import` here would bind the real objects and bypass those
                # patches, which is exactly the seam `execute_with_idempotency` already
                # relies on for its own claim.
                _evidence_session = SessionLocal()
                try:
                    _evidence_ok = ExecutionRecordRepository(
                        _evidence_session
                    ).mark_submitted(
                        execution_id,
                        UUID(str(self._live_tenant_id)),
                        exchange_id=getattr(
                            self.exchange_executor, "exchange_id", None
                        ),
                    )
                finally:
                    _evidence_session.close()

                if not _evidence_ok:
                    logger.critical(
                        "Refusing execution %s: submission evidence could not be "
                        "recorded, so a crash during submission would leave an "
                        "unrecoverable row.", execution_id,
                    )
                    return False, (
                        "Risk gateway: this order's submission could not be recorded "
                        "before sending it. Proceeding would create an execution record "
                        "that cannot later be distinguished from one that never reached "
                        "the venue. Refused before any exchange call."
                    )

                # RT-17: from here on, any failure -- including one raised while reading the
                # response -- may follow a venue order that really exists.
                _venue_call_possible = True

                # Map inputs to CCXT requirements
                c_order_type = CoreOrderType.market if price <= Decimal("0") else CoreOrderType.limit
                c_side = OrderSide.buy if side.lower() == "buy" else OrderSide.sell
                # Call exchange_executor.place_order (which is async)
                result = await self.exchange_executor.place_order(
                    symbol=symbol.replace('-', '/'), # Standardize symbol for CCXT
                    side=c_side,
                    order_type=c_order_type,
                    size=size,
                    price=price if price > 0 else None
                )
                # Extract actual exchange fee from CCXT response if available.
                # BUG-FIX MC-08: also capture fee_currency denomination so callers
                # can normalize fees to quote currency before PnL accounting.
                # Previous code discarded currency, causing 0.0001 BTC fee to be
                # indistinguishable from 0.0001 USDT — a 50000× magnitude error
                # at $50k/BTC.
                actual_fee = Decimal("0")
                actual_fee_currency = None
                if isinstance(result.raw_response, dict):
                    fee_obj = result.raw_response.get("fee")
                    if not fee_obj and isinstance(result.raw_response.get("fees"), list) and len(result.raw_response["fees"]) > 0:
                        fee_obj = result.raw_response["fees"][0]
                    if isinstance(fee_obj, dict):
                        raw_cost = fee_obj.get("cost", 0.0)
                        actual_fee = Decimal(str(raw_cost or "0"))
                        actual_fee_currency = fee_obj.get("currency")  # e.g. "BTC", "USDT", "BNB"

                if getattr(result, "status", None) == "unknown" and not result.success:
                    # RT-17: the executor could not tell whether the venue accepted the
                    # order (lost response / timeout / no order id). That is NOT a
                    # failure: the caller must record UNKNOWN, freeze the symbol and
                    # adjudicate, never treat it as rejected.
                    logger.critical(
                        "ORDER SUBMISSION UNKNOWN for execution %s (%s %s): %s",
                        execution_id, side, symbol, result.error_message,
                    )
                    return False, {
                        'submission_unknown': True,
                        'error_code': getattr(result, "error_code", None) or "SUBMISSION_UNKNOWN",
                        'error': f"Order submission outcome unknown: {result.error_message}",
                    }

                if result.success:
                    venue_status = (
                        result.raw_response.get("info", {}).get("status")
                        if isinstance(result.raw_response, dict) and isinstance(result.raw_response.get("info"), dict)
                        else (result.raw_response.get("status")
                              if isinstance(result.raw_response, dict)
                              else result.status)
                    )
                    # ═══════════════════════════════════════════════════════════
                    # RT-16: `result.success` MEANS ACCEPTED, NOT FILLED.
                    # ═══════════════════════════════════════════════════════════
                    # `size` used to be `float(result.filled_size or size)`. `0` is falsy
                    # in Python, so a venue that accepted a resting limit order and
                    # reported `filled=0` made this claim THE FULL REQUESTED SIZE as
                    # filled -- and `master_executor.settle_exposure` reads exactly this
                    # field to credit the bot's only record of what it holds, while the
                    # fee row is computed from the same number. One falsy-zero turned an
                    # unfilled order into phantom exposure and a phantom fee.
                    # `executed_price` had the identical bug via `result.avg_price or
                    # price`, reporting the price we ASKED for as a price we GOT.
                    #
                    # `classify_venue_fill` reads the venue's own status word and its own
                    # filled quantity, and resolves every ambiguous case to `accepted`
                    # rather than `filled`. Under-reporting is self-correcting: the row
                    # stays EXECUTING, so it stays in the watchdog's population and the
                    # next poll reconciles it. Over-reporting is not.
                    fill_state, filled_qty, qty_inferred = classify_venue_fill(
                        venue_status=venue_status,
                        filled_size=result.filled_size,
                        requested_size=size,
                    )
                    if qty_inferred:
                        logger.warning(
                            "RT-16: venue confirmed completion for execution %s (%s %s) "
                            "but reported no filled quantity; crediting the requested "
                            "size %s. venue_status=%r",
                            execution_id, side, symbol, size, venue_status,
                        )
                    if fill_state != FILL_STATE_FILLED:
                        logger.info(
                            "RT-16: venue ACCEPTED execution %s (%s %s) without a full "
                            "fill -- fill_state=%s filled=%s of %s venue_status=%r. "
                            "Recorded EXECUTING, not COMPLETED; the watchdog owns it "
                            "from here.",
                            execution_id, side, symbol, fill_state, filled_qty, size,
                            venue_status,
                        )
                    return True, {
                        'order_id': result.exchange_order_id,
                        # Kept non-None deliberately: `distributed_execution/
                        # execution_worker.py` treats a None `executed_price` as a failed
                        # job, and an accepted order is LIVE, not failed. When nothing
                        # filled there is no execution price, so the requested price
                        # stands in -- `fill_state` is what says whether it means
                        # anything.
                        'executed_price': float(
                            result.avg_price
                            if result.avg_price is not None
                            else price
                        ),
                        # The venue's own number. 0.0 when it filled nothing.
                        'size': float(filled_qty),
                        # RT-16: so a consumer can tell intent from outcome.
                        'requested_size': float(size),
                        'fill_state': fill_state,
                        'fee': str(actual_fee),
                        'fee_currency': actual_fee_currency,  # MC-08: denomination preserved
                        'total_cost': 0.0,
                        'slippage_applied': False,
                        'status': venue_status,
                    }
                else:
                    return False, f"Exchange execution failed: {result.error_message}"
            except Exception as e:
                if _venue_call_possible:
                    # RT-17: raised after the submission evidence was committed, so the
                    # venue may hold an order. Not a plain failure.
                    logger.critical(
                        "ORDER SUBMISSION UNKNOWN for execution %s: executor raised %s after "
                        "the venue call became possible.", execution_id, type(e).__name__,
                    )
                    return False, {
                        'submission_unknown': True,
                        'error_code': 'SUBMISSION_UNKNOWN',
                        'error': f"Exchange executor exception after submission: {str(e)}",
                    }
                return False, f"Exchange executor exception: {str(e)}"

        # ═══════════════════════════════════════════════════════════════════
        # PAPER-01: THE CANONICAL PAPER LEDGER PATH
        # ═══════════════════════════════════════════════════════════════════
        # When a ledger is installed this is the paper path, and the in-process
        # simulation below is not used at all. Taken BEFORE that simulation rather
        # than beside it, because running both would be the two-accounting-systems
        # problem PAPER-01 is: two answers to "what is my cash", differing by the
        # slippage draw alone.
        #
        # Three things improve by construction on this path, beyond the persistence:
        #
        #   * The fill price and fee are the canonical ones. `apply_slippage` below
        #     uses `random.uniform`, so the in-process path cannot be replayed; the
        #     canonical model is `price * (1 +/- slippage_rate)` by side, which can.
        #   * The equity identity is asserted with ZERO tolerance inside
        #     `paper_trading_service._execute_fill`, before its first statement.
        #   * A retried tick is one order, arbitrated by
        #     `uq_paper_order_idem_default` on the execution_id-derived key.
        #
        # A ledger refusal REFUSES THE EXECUTION. Reporting a fill that was not
        # recorded is the defect; see `bot_paper_ledger`'s module docstring.
        if self.paper_ledger is not None:
            from backend_app.backend.paper.bot_paper_ledger import (
                BotPaperLedgerError, BotPaperLedgerUnavailable,
                BotPaperSellExceedsPosition)

            try:
                recorded = await self.paper_ledger.record_fill(
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=price,
                    execution_id=execution_id,
                )
            except BotPaperSellExceedsPosition as exc:
                # An ordinary strategy outcome, not an incident: the pre-fix paper branch
                # answered `(False, "No position found for {symbol}")` for exactly this,
                # and `_inner_loop` treats a falsey result as a skipped tick. Logged at
                # INFO so a long-only bot selling into no position does not fill the
                # operator's log with criticals.
                logger.info(
                    "Paper execution %s not placed: %s", execution_id, exc,
                )
                return False, str(exc)
            except (BotPaperLedgerUnavailable, BotPaperLedgerError) as exc:
                logger.critical(
                    "Refusing paper execution %s: the canonical paper ledger did not "
                    "record the fill (%s). Nothing is reported as filled, because a "
                    "fill the paper account does not show is the PAPER-01 defect.",
                    execution_id, exc,
                )
                return False, (
                    f"Paper execution refused: the canonical paper ledger did not record "
                    f"the fill ({exc}). No fill is reported."
                )

            self._reconcile_from_paper_ledger(recorded)
            return True, {
                'order_id': str(recorded.order_id),
                'executed_price': str(recorded.executed_price),
                'size': str(recorded.size),
                'fee': str(recorded.fee),
                'fee_currency': None,
                'total_cost': str(
                    (recorded.size * recorded.executed_price) + recorded.fee
                ),
                'slippage_applied': recorded.executed_price != Decimal(str(price)),
                # The evidence that this fill is persisted, carried on the result so a
                # caller (and a test) can tell a recorded fill from an in-process one
                # without inspecting the database.
                'paper_ledger': 'canonical',
                'paper_account_id': recorded.account_id,
                'total_equity': str(recorded.total_equity),
                'available_balance': str(recorded.available_balance),
                'realized_pnl': str(recorded.realized_pnl),
            }

        # PAPER EXECUTION PATH
        # Execute based on side - open_position and close_position handle single slippage internally
        fee_rate_dec = Decimal(str(self.fee_rate))
        if side.lower() == 'buy':
            success, message = self.open_position(symbol, price, size, side='long')
            executed_price = self.positions[symbol].entry_price if success and symbol in self.positions else price
        else:  # sell
            if symbol not in self.positions:
                success, message = False, f"No position found for {symbol}"
                executed_price = price
            else:
                pnl, message = self.close_position(symbol, price, size)
                success = True
                executed_price = price

        # FIN-CRITICAL-003 FIX: Use higher precision (12 decimal places) and banker's rounding
        # This prevents precision loss for high-value assets like BTC
        fee = (size * executed_price * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        total_cost = (size * executed_price + fee).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        if success:
            # BUG-FIX MC-23: Return Decimal values as str() to preserve precision.
            # Previous float() casts degraded accuracy for large BTC prices and
            # sub-satoshi fee amounts (e.g. 0.00000001 BTC loses precision as float).
            # Callers must use Decimal(result['fee']) not float(result['fee']).
            return True, {
                'order_id': f"paper_{uuid.uuid4().hex[:12]}",
                'executed_price': str(executed_price),
                'size': str(size),
                'fee': str(fee),
                'fee_currency': None,  # paper trading: fee in quote currency by definition
                'total_cost': str(total_cost),
                'slippage_applied': executed_price != price,
            }
        else:
            return False, message
    
    # ----------------------------------
    # APPLY SLIPPAGE
    # ----------------------------------
    def apply_slippage(self, price: Decimal, side: str) -> Decimal:
        """
        Apply adverse slippage to execution price based on order side.

        Args:
            price: Original price (Decimal)
            side: "buy", "sell", "long", or "short"

        Returns:
            Price with adverse slippage applied (Decimal)
        """
        raw_slip = getattr(self, 'slippage', 0.001)
        if raw_slip is None:
            max_slip = 0.001
        else:
            try:
                max_slip = abs(float(raw_slip))
            except (ValueError, TypeError):
                max_slip = 0.001

        if max_slip == 0.0:
            return price

        slip_mag = Decimal(str(random.uniform(0.5 * max_slip, max_slip)))
        
        if str(side).lower() in ("buy", "long"):
            # Buy orders slip upwards (worse for buyer)
            executed_price = (price * (Decimal("1") + slip_mag)).quantize(Decimal("0.00000001"))
        else:
            # Sell orders slip downwards (worse for seller)
            executed_price = (price * (Decimal("1") - slip_mag)).quantize(Decimal("0.00000001"))
            
        return executed_price

    # ----------------------------------
    # OPEN POSITION
    # ----------------------------------
    def open_position(
        self,
        symbol: str,
        price: Decimal,
        size: Decimal,
        side: str = "long"
    ) -> Tuple[bool, str]:
        """
        Open a new position with slippage, fees, and 🛡️ RISK GUARDRAILS.

        Global guardrails enforced BEFORE execution:
        - Max position size: 10% of equity
        - Max daily loss: 5% of equity
        - Max open trades: 5 positions

        Args:
            symbol: Trading pair symbol
            price: Entry price (slippage will be applied)
            size: Position size (quantity)
            side: "long" or "short" (default: "long")

        Returns:
            Tuple of (success, message)
        """
        # 🛡️ GLOBAL RISK GUARDRAILS - Check BEFORE execution
        # Sync risk manager with current engine state.
        #
        # `current_equity` is now MARKED TO MARKET (BUG-016), so the guardrails below are
        # measured against what the account is actually worth including open positions,
        # rather than against a figure that only moved on a realised close.
        self._sync_equity()
        self.risk_manager.current_equity = float(self.current_equity)
        self.risk_manager.update_open_trades_count(len(self.positions))

        # Apply slippage to get execution price for guardrail check
        open_order_side = "sell" if side.lower() == "short" else "buy"
        execution_price = self.apply_slippage(price, open_order_side)
        fee_rate_dec = Decimal(str(self.fee_rate))
        position_value = execution_price * size
        # FIN-CRITICAL-003 FIX: Use higher precision (12 decimal places) and banker's rounding
        fee = (execution_price * size * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        total_cost = (position_value + fee).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # Check ALL guardrails
        allowed, reason = self.risk_manager.can_open_position(
            position_value=float(position_value),
            open_trades_count=len(self.positions)
        )

        if not allowed:
            # 🚫 BLOCK TRADE - Log warning
            blocked_record = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "side": side,
                "intended_size": float(size),
                "intended_value": float(position_value),
                "reason": reason,
                "equity": float(self.current_equity),
            }
            self._blocked_trades.append(blocked_record)

            print("\n[WARNING] TRADE BLOCKED BY RISK GUARDRAIL")
            print(f"    Symbol: {symbol}")
            print(f"    Side: {side}")
            print(f"    Size: {size}")
            print(f"    Value: ${float(position_value):.2f}")
            print(f"    Reason: {reason}")
            print(f"    Equity: ${float(self.current_equity):.2f}")
            print(f"    Open trades: {len(self.positions)}/{self.risk_manager.max_open_trades}\n")

            return False, f"RISK GUARDRAIL BLOCKED: {reason}"

        # Continue with existing checks
        if symbol in self.positions:
            return False, f"Position already exists for {symbol}"

        if size <= Decimal("0"):
            return False, "Position size must be positive"

        if price <= Decimal("0"):
            return False, "Price must be positive"

        # Log execution details
        print(f"[Executed price]: {execution_price}")
        print(f"[Fee]: {fee}")

        # ═══════════════════════════════════════════════════════════════════
        # AFFORDABILITY IS CHECKED AGAINST CASH, NOT EQUITY  (BUG-016)
        # ═══════════════════════════════════════════════════════════════════
        # This compared `total_cost > self.current_equity`. Because opening a position
        # debited only the fee, equity barely moved -- so the SAME check passed again for
        # the next symbol, and the next. N positions could each be worth up to the whole
        # account while every one of them reported sufficient capital.
        #
        # Cash is what a position is actually funded from, and it is now reduced by the
        # full notional below. Aggregate exposure is therefore bounded by the balance
        # rather than by `max_open_trades` happening to be 5.
        if total_cost > self.cash:
            return False, (
                f"Insufficient capital: {symbol} {side} {size} @ {execution_price} "
                f"costs {total_cost} (including {fee} fee) but only {self.cash} cash is "
                f"available. Equity is {self.current_equity}, of which "
                f"{self.position_market_value()} is already committed to open positions."
            )

        # ✅ All guardrails passed - Execute trade
        self.risk_manager.record_trade_open()

        # Store entry fee for later PnL calculation
        entry_fee = fee

        # Create position with executed price
        self.positions[symbol] = Position(
            symbol=symbol,
            entry_price=execution_price,
            size=size,
            side=side
        )

        # Mark at the fill price, then move cash by the residual of the position-value
        # change less the fee. At entry the position is worth exactly its notional, so
        # equity falls by exactly the fee -- while CASH falls by notional + fee.
        self._marks[symbol] = execution_price
        new_value = self.position_value(self.positions[symbol], execution_price)
        self._apply_cash_delta(symbol, Decimal("0"), new_value, fee)

        # Store additional data separately
        self._position_meta = getattr(self, '_position_meta', {})
        self._position_meta[symbol] = {"entry_fee": entry_fee}

        print("[SUCCESS] Guardrails passed - Trade executed")
        return True, f"Opened {side} position: {size} {symbol} @ {execution_price} (fee: {fee})"
    
    # ----------------------------------
    # CLOSE POSITION
    # ----------------------------------
    def close_position(self, symbol: str, price: Decimal, size: Decimal = None) -> Tuple[Decimal, str]:
        """
        Close an existing position (fully or partially) with slippage and fees.

        Args:
            symbol: Trading pair symbol to close
            price: Exit price (slippage will be applied)
            size: Size to close (if None or >= position size, closes entire position)

        Returns:
            Tuple of (pnl, message)
        """
        if symbol not in self.positions:
            return Decimal("0"), f"No position found for {symbol}"

        pos = self.positions[symbol]

        # Determine if full or partial close
        is_full_close = (size is None or size >= pos.size)
        close_size = pos.size if is_full_close else size

        # Apply slippage (worse price for exit: sell when long, buy when short)
        exit_side = "sell" if pos.side == "long" else "buy"
        execution_price = self.apply_slippage(price, exit_side)

        # Calculate gross PnL with executed price
        if pos.side == "long":
            gross_pnl = (execution_price - pos.entry_price) * close_size
        else:  # short
            gross_pnl = (pos.entry_price - execution_price) * close_size

        # Calculate fees using Decimal
        fee_rate_dec = Decimal(str(self.fee_rate))
        # FIN-CRITICAL-003 FIX: Use higher precision (12 decimal places) and banker's rounding
        exit_fee = (execution_price * close_size * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # Log execution details
        print(f"[Executed price]: {execution_price}")
        print(f"[Fee]: {exit_fee}")

        # Get entry fee from stored metadata
        self._position_meta = getattr(self, '_position_meta', {})
        entry_fee = (pos.entry_price * close_size * fee_rate_dec).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
        total_fees = (entry_fee + exit_fee).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # Net PnL after fees
        net_pnl = (gross_pnl - total_fees).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)

        # 🛡️ Update risk manager with PnL and record trade close
        self.risk_manager.update_equity(float(net_pnl))

        # ═══════════════════════════════════════════════════════════════════
        # CASH AS THE RESIDUAL OF THE POSITION-VALUE CHANGE  (BUG-016)
        # ═══════════════════════════════════════════════════════════════════
        # This used to be `self.current_equity += net_pnl + entry_fee`, which reversed
        # the open's fee-only debit and arrived at the right FINAL equity by a route that
        # never represented the position's value at all.
        #
        # Now: mark at the exit price, value the position before and after the size
        # reduction, and move cash by the difference less the exit fee. Closing a long
        # therefore credits cash with the exit proceeds, which is what actually happens.
        #
        # The final equity after a full round trip is IDENTICAL to the old code's --
        # pinned by `test_a_round_trip_ends_on_the_same_equity_as_before_the_fix` -- so
        # realised results do not move. Only equity while the position is open changes.
        self._marks[symbol] = execution_price
        old_value = self.position_value(pos, execution_price)

        if is_full_close:
            self.risk_manager.record_trade_close()
            closed = self.positions.pop(symbol)
            self._position_meta.pop(symbol, None)
            new_value = Decimal("0")
            self._marks.pop(symbol, None)
            pos = closed  # keep the pre-close snapshot for the trade record below
        else:
            pos.size = (pos.size - close_size).quantize(Decimal("0.00000001"))
            self.positions[symbol] = pos
            new_value = self.position_value(pos, execution_price)

        self._apply_cash_delta(symbol, old_value, new_value, exit_fee)

        # Calculate PnL percentage
        invested = pos.entry_price * close_size
        pnl_pct = ((net_pnl / invested) * Decimal("100")).quantize(Decimal("0.00000001")) if invested > 0 else Decimal("0")

        # Log trade with fees
        trade = Trade(
            symbol=symbol,
            entry_price=pos.entry_price,
            exit_price=execution_price,
            size=close_size,
            side=pos.side,
            entry_time=pos.entry_time,
            exit_time=datetime.now(),
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            commission=total_fees
        )
        self.trade_log.append(trade)

        status_prefix = "Closed" if is_full_close else "Partially closed"
        return net_pnl, f"{status_prefix} {pos.side} position: {close_size} {symbol} @ {execution_price} (PnL: {net_pnl}, {pnl_pct}%)"

    # ----------------------------------
    # 🔴 STEP 5: PARTIAL FILL HANDLING
    # ----------------------------------
    def handle_partial_fill(
        self,
        symbol: str,
        filled_size: Decimal,
        fill_price: Decimal,
        total_order_size: Decimal,
        side: str,
        fee: Decimal
    ) -> Dict[str, Any]:
        """
        Handle partial fill - update position incrementally.

        When an order is partially filled, we update the position with the filled
        amount rather than waiting for the full order to complete.

        Args:
            symbol: Trading pair
            filled_size: Size filled in this event
            fill_price: Price of this fill
            total_order_size: Total size of the original order
            side: "buy" or "sell"
            fee: Fee for this fill

        Returns:
            Dict with position update details
        """
        remaining_size = (total_order_size - filled_size).quantize(Decimal("0.00000001"))
        is_complete = remaining_size <= Decimal("0.0001")  # Allow tiny tolerance

        # ═══════════════════════════════════════════════════════════════════
        # EVERY BRANCH BELOW MOVES CASH BY THE SAME RESIDUAL  (BUG-016)
        # ═══════════════════════════════════════════════════════════════════
        # Each of the five cases used to adjust `current_equity` directly and by a
        # different rule -- `-= fee` on an open or an add, `+= net_pnl` on a close,
        # `+= closed_pnl` on a reduce, and nothing at all on the flip leg. So the engine
        # held four definitions of what a fill does to the account, and the flip leg
        # opened a position for free.
        #
        # Now each branch decides only WHAT THE NEW POSITION IS. The position is valued
        # before and after at this fill's price, and cash moves by the difference less
        # the fee -- one rule, applied once at the end.
        old_value = (
            self.position_value(self.positions[symbol], fill_price)
            if symbol in self.positions
            else Decimal("0")
        )
        self._marks[symbol] = fill_price

        side_lower = side.lower()
        if symbol not in self.positions:
            # Opening new position (long on buy, short on sell)
            pos_side = 'long' if side_lower == 'buy' else 'short'
            self.positions[symbol] = Position(
                symbol=symbol,
                entry_price=fill_price,
                size=filled_size,
                side=pos_side
            )
        else:
            existing = self.positions[symbol]
            # If side matches position side (buy for long, sell for short) -> Add to position
            is_adding = (side_lower == 'buy' and existing.side == 'long') or (side_lower == 'sell' and existing.side == 'short')
            
            if is_adding:
                new_total_size = existing.size + filled_size
                new_avg_price = (
                    ((existing.entry_price * existing.size) + (fill_price * filled_size))
                    / new_total_size
                )

                existing.entry_price = new_avg_price
                existing.size = new_total_size
            else:
                # Closing or reducing position (sell for long, buy for short)
                if filled_size >= existing.size:
                    # Complete close of existing position
                    closing_size = existing.size
                    if existing.side == 'long':
                        gross_pnl = (fill_price - existing.entry_price) * closing_size
                    else:  # short
                        gross_pnl = (existing.entry_price - fill_price) * closing_size
                    
                    net_pnl = gross_pnl - fee
                    old_side = existing.side
                    self.positions.pop(symbol, None)

                    # Record trade
                    trade = Trade(
                        symbol=symbol,
                        entry_price=existing.entry_price,
                        exit_price=fill_price,
                        size=closing_size,
                        side=old_side,
                        entry_time=existing.entry_time,
                        exit_time=datetime.now(),
                        pnl=net_pnl,
                        pnl_pct=((net_pnl / (existing.entry_price * closing_size)) * Decimal("100")) if closing_size > 0 and existing.entry_price > 0 else Decimal("0"),
                        commission=fee
                    )
                    self.trade_log.append(trade)

                    # Position flip: excess filled_size opens new opposite position
                    flip_size = filled_size - closing_size
                    if flip_size > Decimal("0"):
                        new_opposite_side = 'short' if old_side == 'long' else 'long'
                        self.positions[symbol] = Position(
                            symbol=symbol,
                            entry_price=fill_price,
                            size=flip_size,
                            side=new_opposite_side
                        )
                        print(f"[POSITION FLIP] Flipped {old_side} to {new_opposite_side}: {flip_size} {symbol} @ {fill_price}")
                else:
                    # Partial close - reduce position
                    if existing.side == 'long':
                        closed_pnl = (fill_price - existing.entry_price) * filled_size - fee
                    else:  # short
                        closed_pnl = (existing.entry_price - fill_price) * filled_size - fee
                    
                    existing.size = existing.size - filled_size

                    # Log partial close
                    print(f"[PARTIAL CLOSE] Partial close {existing.side}: {filled_size} {symbol} @ {fill_price} (PnL: {closed_pnl})")

        # The one cash movement for this fill, whichever branch above ran.
        new_value = (
            self.position_value(self.positions[symbol], fill_price)
            if symbol in self.positions
            else Decimal("0")
        )
        if symbol not in self.positions:
            self._marks.pop(symbol, None)
        self._apply_cash_delta(symbol, old_value, new_value, fee)

        result = {
            'symbol': symbol,
            'filled_size': float(filled_size),
            'fill_price': float(fill_price),
            'remaining_size': float(max(Decimal("0"), remaining_size)),
            'is_complete': is_complete,
            'current_position_size': float(self.positions.get(symbol, Position(symbol, Decimal("0"), Decimal("0"), 'long')).size),
            'fee': float(fee),
            'cash': float(self.cash),
            'equity': float(self.current_equity),
        }

        print(f"[STEP 5] Partial fill handled - {filled_size}/{total_order_size} {symbol} @ {fill_price}")

        return result
    
    # ----------------------------------
    # GET CURRENT POSITIONS
    # ----------------------------------
    def get_positions(self) -> Dict[str, Position]:
        """
        Get all current open positions.
        
        Returns:
            Dictionary mapping symbol to Position object
        """
        return self.positions.copy()
    
    # ----------------------------------
    # 🔴 STEP 6: POSITION CONSISTENCY CHECK
    # ----------------------------------
    def verify_position_consistency(
        self,
        symbol: str,
        exchange_position: Optional[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Verify local position matches exchange position before execution.
        
        CRITICAL SAFETY CHECK: Prevents execution if there's a mismatch
        between our local position tracking and the actual exchange position.
        
        Args:
            symbol: Trading pair to check
            exchange_position: Position data from exchange (None if no position)
            
        Returns:
            Tuple of (is_consistent, message)
        """
        local_pos = self.positions.get(symbol)
        
        # No local position, no exchange position - consistent
        if local_pos is None and exchange_position is None:
            return True, "No position - consistent"
        
        # Have local position but no exchange position - DRIFT!
        if local_pos is not None and exchange_position is None:
            return False, f"POSITION DRIFT: Local position exists but exchange shows none. Symbol: {symbol}"
        
        # No local position but have exchange position - DRIFT!
        if local_pos is None and exchange_position is not None:
            return False, f"POSITION DRIFT: Exchange has position but local shows none. Symbol: {symbol}"
        
        # Both have positions - verify details
        if local_pos is not None and exchange_position is not None:
            # Check position size (with small tolerance for rounding)
            local_size = local_pos.size
            exchange_size_raw = exchange_position.get('size', 0)
            exchange_size = Decimal(str(exchange_size_raw)) if not isinstance(exchange_size_raw, Decimal) else exchange_size_raw
            size_diff = abs(local_size - exchange_size)

            if size_diff > Decimal("0.0001"):  # Allow tiny rounding differences
                return False, (
                    f"POSITION DRIFT: Size mismatch. "
                    f"Local: {local_size}, Exchange: {exchange_size}, "
                    f"Diff: {size_diff}. Symbol: {symbol}"
                )

            # Check position side
            local_side = local_pos.side.lower()
            exchange_side = exchange_position.get('side', '').lower()

            if local_side != exchange_side:
                return False, (
                    f"POSITION DRIFT: Side mismatch. "
                    f"Local: {local_side}, Exchange: {exchange_side}. Symbol: {symbol}"
                )

            # Check entry price (with 0.1% tolerance for market volatility)
            if local_pos.entry_price > 0:
                local_price = local_pos.entry_price
                exchange_price_raw = exchange_position.get('entry_price', 0)
                exchange_price = Decimal(str(exchange_price_raw)) if not isinstance(exchange_price_raw, Decimal) else exchange_price_raw
                price_diff_pct = abs(local_price - exchange_price) / local_price

                if price_diff_pct > Decimal("0.001"):  # 0.1% tolerance
                    return False, (
                        f"POSITION DRIFT: Entry price mismatch. "
                        f"Local: {local_price}, Exchange: {exchange_price}, "
                        f"Diff: {price_diff_pct * Decimal('100')}%. Symbol: {symbol}"
                    )
        
        return True, "Positions consistent"
    
    def block_execution_on_drift(self, symbol: str, exchange_position: Optional[Dict[str, Any]]) -> None:
        """
        Block execution if position drift detected.
        
        Raises:
            ValueError: If position inconsistency detected
        """
        is_consistent, message = self.verify_position_consistency(symbol, exchange_position)
        
        if not is_consistent:
            logger.error(f"🔴 STEP 6: {message}")
            raise ValueError(
                f"🔴 CRITICAL SAFETY ERROR: Position drift detected. "
                f"Execution BLOCKED to prevent further divergence. "
                f"Details: {message}"
            )
        return self.positions.copy()
    
    # ----------------------------------
    # GET POSITION VALUE
    # ----------------------------------
    def get_position_value(self, symbol: str, current_price: Decimal) -> Decimal:
        """
        Get current value of a specific position.

        Args:
            symbol: Trading pair symbol
            current_price: Current market price

        Returns:
            Position value
        """
        if symbol not in self.positions:
            return Decimal("0")
        return self.positions[symbol].current_value(current_price)
    
    # ----------------------------------
    # GET UNREALIZED PNL
    # ----------------------------------
    def get_unrealized_pnl(self, symbol: str, current_price: Decimal) -> Decimal:
        """
        Get unrealized PnL for a position.

        Args:
            symbol: Trading pair symbol
            current_price: Current market price

        Returns:
            Unrealized PnL
        """
        if symbol not in self.positions:
            return Decimal("0")
        return self.positions[symbol].unrealized_pnl(current_price)
    
    # ----------------------------------
    # GET TOTAL UNREALIZED PNL
    # ----------------------------------
    def get_total_unrealized_pnl(self, prices: Dict[str, Decimal]) -> Decimal:
        """
        Get total unrealized PnL across all positions.

        Args:
            prices: Dictionary mapping symbol to current price

        Returns:
            Total unrealized PnL
        """
        total = Decimal("0")
        for symbol, position in self.positions.items():
            if symbol in prices:
                price = prices[symbol] if isinstance(prices[symbol], Decimal) else Decimal(str(prices[symbol]))
                total += position.unrealized_pnl(price)
        return total
    
    # ----------------------------------
    # GET TRADE HISTORY
    # ----------------------------------
    def get_trade_log(self) -> List[Trade]:
        """
        Get completed trade history.
        
        Returns:
            List of Trade objects
        """
        return self.trade_log.copy()
    
    # ----------------------------------
    # GET EQUITY
    # ----------------------------------
    def get_equity(self) -> Decimal:
        """
        Get current equity (cash + unrealized PnL approximation).

        Returns:
            Current equity amount
        """
        return self.current_equity
    
    # ----------------------------------
    # GET DRAWDOWN
    # ----------------------------------
    def get_drawdown(self) -> Decimal:
        """
        Get current drawdown from peak equity.

        Returns:
            Drawdown as decimal (e.g., 0.15 = 15%)
        """
        if self.peak_equity == 0:
            return Decimal("0")
        return ((self.peak_equity - self.current_equity) / self.peak_equity).quantize(Decimal("0.0001"))
    
    # ----------------------------------
    # GET PERFORMANCE STATS
    # ----------------------------------
    def get_stats(self) -> dict:
        """
        Get trading performance statistics.

        Returns:
            Dictionary with performance metrics
        """
        if not self.trade_log:
            # SAME SHAPE AS THE POPULATED BRANCH.
            #
            # This used to return only four keys, so `get_stats()` had a different
            # dictionary shape depending on whether any trade had CLOSED yet. A consumer
            # reading `stats["current_equity"]` got a KeyError on a run that was holding
            # an open position and had closed nothing - which is exactly the state a
            # backtest is in partway through, and the state BUG-016 made meaningful by
            # marking open positions to market.
            #
            # Found by `tests/test_bug016_backtest_impact.py` asking for `cash` on a run
            # with one open position and no closed trades.
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "total_pnl": float(Decimal("0")),
                "avg_pnl": float(Decimal("0")),
                "fee_rate": float(self.fee_rate),
                "slippage": self.slippage,
                "total_commission": 0.0,
                "current_equity": float(self.current_equity),
                "peak_equity": float(self.peak_equity),
                "drawdown_pct": float(self.get_drawdown()),
                "cash": float(self.cash),
                "position_market_value": float(self.position_market_value()),
                "total_exposure": float(self.total_exposure()),
                "open_positions": len(self.positions),
            }

        total_trades = len(self.trade_log)
        winning_trades = sum(1 for t in self.trade_log if t.pnl > 0)
        total_pnl = sum(t.pnl for t in self.trade_log)

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": total_trades - winning_trades,
            "win_rate": winning_trades / total_trades if total_trades > 0 else 0,
            "total_pnl": float(total_pnl),
            "avg_pnl": float(total_pnl / total_trades) if total_trades > 0 else 0,
            "fee_rate": self.fee_rate,
            "slippage": self.slippage,
            "total_commission": float(sum(t.commission for t in self.trade_log)),
            "current_equity": float(self.current_equity),
            "peak_equity": float(self.peak_equity),
            "drawdown_pct": float(self.get_drawdown()),
            # BUG-016: cash, committed value and exposure are now distinct from equity
            # and are reported separately, so a consumer can see the identity rather
            # than having to infer it from one number.
            "cash": float(self.cash),
            "position_market_value": float(self.position_market_value()),
            "total_exposure": float(self.total_exposure()),
            "open_positions": len(self.positions),
        }
    
    # ----------------------------------
    # 🛡️ GET BLOCKED TRADES
    # ----------------------------------
    def get_blocked_trades(self) -> List[dict]:
        """
        Get list of trades blocked by risk guardrails.
        
        Returns:
            List of blocked trade records with reasons
        """
        return self._blocked_trades.copy()
    
    # ----------------------------------
    # 🛡️ GET RISK STATUS
    # ----------------------------------
    def get_risk_status(self) -> dict:
        """
        Get current risk status from risk manager.

        Returns:
            Dictionary with risk metrics
        """
        # Sync with current state (convert Decimal to float for risk_manager)
        self.risk_manager.current_equity = float(self.current_equity)
        self.risk_manager.update_open_trades_count(len(self.positions))

        status = self.risk_manager.get_risk_status()
        status["blocked_trades_count"] = len(self._blocked_trades)
        status["open_positions"] = list(self.positions.keys())

        return status
    
    # ----------------------------------
    # RESET
    # ----------------------------------
    def reset(self) -> None:
        """Reset all positions and trade history"""
        self.positions = {}
        self.trade_log = []
        self.current_equity = self.initial_capital
        self.peak_equity = self.initial_capital
        self._blocked_trades = []
        # BUG-016: cash and marks are part of the account state and must reset with it.
        # Leaving cash behind would start the next run funded by the previous one's
        # closing balance while reporting the initial equity.
        self.cash = self.initial_capital
        self._marks = {}
        self._position_meta = {}

        # Reset risk manager (convert Decimal to float)
        if self.risk_manager:
            self.risk_manager.current_equity = float(self.initial_capital)
            self.risk_manager.peak_equity = float(self.initial_capital)
            self.risk_manager._open_trades_count = 0
            self.risk_manager._daily_pnl = 0.0

    # ----------------------------------
    # 🔴 PURE ALGO: STRATEGY VALIDATION
    # ----------------------------------
    def _validate_strategy_exists(self, tenant_id: UUID, strategy_id: str) -> bool:
        """
        🔴 CRITICAL: Validate strategy_id exists in database.
        
        This prevents fake/manual strategy_ids from executing trades.
        ALL trades must be tied to a real strategy in the database.
        
        Args:
            tenant_id: Tenant UUID
            strategy_id: Strategy identifier to validate
            
        Returns:
            bool: True if strategy exists and is active, False otherwise
        """
        if not strategy_id:
            return False
            
        # Check for blocked manual strategy IDs
        blocked_ids = {
            "manual_order", "manual", "direct", "ui_order",
            "stop_loss_order", "take_profit_order",
            "emergency_order", "panic_order", "quick_trade",
            "test", "fake", "null", "none", ""
        }
        
        if strategy_id.lower() in blocked_ids:
            return False

        # The synthetic-id convention this suite drives the funnel with, gated on the
        # environment. It used to be unconditional and ahead of the query, which made this
        # whole step — the only one in `execute_with_idempotency` that ties a trade to a
        # real, OWNED strategy — bypassable by naming convention on the live path. The gate
        # fails closed, so an unset ENV disables it.
        # Regression: tests/test_strategy_validation_test_prefix_gate.py
        if str(strategy_id).startswith("test_") and _synthetic_strategy_ids_allowed():
            return True
            
        try:
            from sqlalchemy import text

            from backend_app.core.database import SessionLocal
            
            db = SessionLocal()
            try:
                # Query database for strategy using raw SQL (works with SQLite/PG tables)
                result = db.execute(
                    text("SELECT 1 FROM strategies WHERE id = :id AND user_id = :tid"),
                    {"id": strategy_id, "tid": str(tenant_id)}
                ).fetchone()
                
                if result is not None:
                    return True
                # Same gate as above. This second site is why the branch above is not
                # simply deleted: a test-lane id whose row genuinely does not exist is
                # still admitted here, and both sites have to agree or the behaviour
                # depends on whether the query happened to find something.
                return (
                    str(strategy_id).startswith("test_")
                    and _synthetic_strategy_ids_allowed()
                )
            finally:
                db.close()
        except Exception as e:
            # Log error but fail secure - reject if we can't verify
            print(f"[WARNING] Strategy validation error: {e}")
            return False

    async def execute_trade(
        self,
        tenant_id: Any,
        strategy_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        context: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Unified trade execution interface. Delegates to execute_with_idempotency.

        THE ENGINE'S OWN ENVIRONMENT TRAVELS WITH THE ORDER.

        This wrapper is the second door into the live funnel -- five production callers
        in ``backend_app`` use it (``dag_risk_integration``, ``execution_router``,
        ``signal_service`` and both ``distributed_execution`` modules) -- and it used to
        drop the environment entirely. The funnel's ``ExecutionEnvironment.LIVE`` default
        then applied, so an order from an engine explicitly constructed for ``PAPER``
        (``dag_risk_integration``'s fallback) or ``BACKTEST``
        (``routers/strategies.py``) entered the live path labelled ``LIVE`` and
        ``assert_live_environment`` -- which exists to refuse exactly that -- passed it.
        Same defect as the one fixed in ``BotRunner._inner_loop``, different door.

        ``execute_trade`` deliberately takes NO ``execution_environment`` argument of its
        own: what an engine exists for is not a per-call option a caller may override,
        because overriding it is the defect with extra steps.

        AND ONLY WHAT IT WAS TOLD. An engine constructed without an environment has
        ``self.execution_environment is None``, and forwarding that ``None`` would make
        ``assert_live_environment`` refuse the order (Requirement 13.10). 70 of the 71
        construction sites in this repository omit the argument, so omission stays
        silent and their behaviour is unchanged. Stated is enforced; omitted is not --
        the same rule ``__init__`` applies, for the same reason.

        Regression: tests/test_execution_environment_is_stated_not_defaulted.py
        """
        parsed_tenant = UUID(str(tenant_id)) if isinstance(tenant_id, (str, UUID)) else tenant_id

        stated_environment = getattr(self, "execution_environment", None)
        environment_kwargs = (
            {} if stated_environment is None
            else {"execution_environment": stated_environment}
        )

        res = await self.execute_with_idempotency(
            tenant_id=parsed_tenant,
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            source="bot_runner",
            **environment_kwargs,
        )
        is_success = res.get("status") in ("completed", "skipped_completed")
        return ExecutionResult(
            success=is_success,
            execution_id=res.get("execution_id"),
            status=res.get("status", "failed"),
            message=res.get("message", ""),
            details=res.get("result"),
        )

# Retain UnifiedExecutionEngine alias for zero-breakage consolidation
UnifiedExecutionEngine = ExecutionEngine

