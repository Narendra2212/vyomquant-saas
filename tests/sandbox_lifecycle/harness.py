"""
The deterministic sandbox harness. Requirement 26, task 21.1.

Requirement 26.4 mandates ONE end-to-end test that drives ONE fixture strategy through
the whole chain this specification wires together:

    save -> appears on the Strategies_Page listing
         -> ``POST .../backtests/execute`` completes and persists a
            ``strategy_backtests`` row
         -> ``POST .../versions/{v}/deploy`` with ``mode=paper`` succeeds
         -> at least one Signal is generated and reaches a terminal
            ``Order_Lifecycle_State`` distinguishable from a real fill
         -> that signal appears with a full trace on ``GET /api/signal-trace/signals``

and then asserts, structurally, that **no real exchange order-placement call was made**
(call count == 0 on the real order-placement seam).

The chain is one narrative, so the world it runs in lives here once and
``test_deterministic_sandbox.py`` supplies only the steps and the assertions.

WHAT IS REAL IN HERE AND WHAT IS A DOUBLE
-----------------------------------------
Real, exercised as shipped, over the real ``backend_app.main.app``:

* the FastAPI routing and every request model's validation;
* ``POST /api/strategies`` (the save), ``GET /api/strategies`` (the listing),
  ``POST /api/strategy-operations/strategies/{id}/versions`` (the immutable version),
  ``POST .../backtests/execute`` (task 6.1),
  ``POST /api/strategies/{id}/versions/{v}/deploy`` (the binding),
  ``GET /api/signal-trace/signals`` and ``GET /api/signal-trace/signals/{id}``
  (tasks 13.1, 13.2);
* the block registry, the canonical DAG schema, the single compiler, ``CompiledPlan``,
  ``plan_to_engine_graph``, the ``DAGEngine``, the VectorBT ``BacktestEngine``, the
  ``RiskEngine`` and the portfolio simulator - the backtest really runs;
* every deployment gate in ``deployment_binding.evaluate_binding``;
* ``signal_service``'s ``LiveSignalPath``, ``mint_signal``, ``generate_signal``,
  ``submit_signal``, ``apply_order_lifecycle_state`` and the
  ``order_lifecycle_state`` transition table and gate;
* the REAL ``DistributedIdempotencyLayer``, including its atomic Lua check-and-set and
  its owner-token release.

Doubles, and there are exactly four, each because this environment has no instance of
the thing it stands for:

1. :class:`SandboxDatabase` - the PostgREST client. There is no PostgreSQL here, so the
   rows live in memory. It honours the predicates the production code sends (``eq``,
   ``neq``, ``in_``, ``is_``, ``gte``, ``lte``, ``not_.is_``, ``order``, ``range``,
   ``limit``) and it enforces the one database-level guarantee this suite leans on -
   005b's ``uq_signals_idempotency_key``.
2. :class:`RecordingRedis` (imported from ``tests/crash_recovery/harness.py``, not
   re-written) - the Redis store the real idempotency layer locks in.
3. :class:`SeededSyntheticFeed` - Requirement 26.1's "fixed, seeded ... synthetic data
   source rather than a live market feed". It is the only market data in the suite: the
   backtest reads it through the real ``DataEngine``, and the live event the signal path
   evaluates is one of its own bars.
4. :class:`SandboxExchangeClient` - Requirement 26.4's "mocked/sandboxed exchange
   client", and the object the zero-call assertion is measured on.

Nothing under test is mocked. In particular the risk validation component and the
idempotency layer are NOT stubbed out: the risk component is
``tests/crash_recovery/harness.RecordingRiskEngine`` (the same double the Requirement
19.4 suite established) and the idempotency layer is the shipped one.

THE ORDER-PLACEMENT SEAM, AND WHY THE COUNTER IS FALSIFIABLE
------------------------------------------------------------
The platform's real order-placement seam is
``exchange_executor.CCXTExchangeExecutor.place_order``, whose one venue call is
``self._exchange.create_order(**params)`` - a CCXT client method.
:class:`SandboxExchangeClient` therefore records **every** CCXT order-placement method
name plus the platform's own wrapper names, and :attr:`SandboxExchangeClient.placements`
is what "call count == 0" is read off.

A counter that nothing ever touches would make that assertion unfalsifiable, so
:class:`SandboxExecutionComponent` genuinely holds the client and genuinely places an
order when its mode is ``live`` - the test suite exercises that branch as a positive
control before asserting the sandbox's zero.

DETERMINISM (Requirement 26.1)
------------------------------
Every number the fixture strategy sees is a pure function of :data:`SANDBOX_SEED`:
:class:`SeededSyntheticFeed` builds its bars from ``random.Random(SANDBOX_SEED)`` and a
closed-form oscillation, with a fixed first-bar timestamp. No wall clock and no global
random state reaches an asserted value - :class:`SandboxClock` is the one clock, and the
signal path is driven through its injectable ``clock`` argument.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

# The doubles the Requirement 19.4 suite already established. Imported rather than
# re-written: a second recording Redis or a second approving risk component would be a
# second thing to keep in step with the layer and the seam they stand in for.
from tests.crash_recovery.harness import (  # noqa: F401 - re-exported on purpose
    FakeClock as SandboxClock,
    RecordingRedis,
    RecordingRiskEngine,
)

# ══════════════════════════════════════════════════════════════════════════
# THE FIXTURE'S FIXED FACTS
# ══════════════════════════════════════════════════════════════════════════

#: The one seed. Requirement 26.1's "fixed, seeded" source, and the reason two runs of
#: this suite see byte-identical bars.
SANDBOX_SEED = 20_240_501

#: The fixture strategy's market. It is the DATA block's own ``symbol``/``timeframe``
#: (SB-06) - nothing in this harness substitutes a market anywhere else, and the deploy
#: gate resolves both from the version's persisted plan.
SANDBOX_SYMBOL = "BTC/USDT"
SANDBOX_TIMEFRAME = "1h"
SANDBOX_MARKET_TYPE = "spot"

#: The venue the seeded asset universe lists this market on, and therefore the venue the
#: sandbox exchange account names. A venue is a deployment fact, never a saved one.
SANDBOX_VENUE = "binance"

#: How many bars the synthetic feed holds. ``BacktestRuntime.run_backtest`` refuses a
#: window under 50 bars, and the EMA(20) needs its warmup, so 400 leaves the strategy a
#: long tradeable stretch while keeping the suite fast.
SANDBOX_BARS = 400

#: The first bar's open time, fixed so the persisted window is reproducible.
SANDBOX_FIRST_BAR = datetime(2024, 1, 1, tzinfo=timezone.utc)

#: The owner. Every row this package seeds belongs to this user.
SANDBOX_USER: Dict[str, Any] = {
    "id": "5f2b7c94-3d61-4a08-9f7e-1b0c8d4e2a11",
    "email": "sandbox-owner@example.com",
    "role": "authenticated",
    "access_token": "token-sandbox-owner",
}

# ══════════════════════════════════════════════════════════════════════════
# THE SECOND TENANT (task 22.1, Requirement 20.4)
# ══════════════════════════════════════════════════════════════════════════
#
# Requirement 20.4's cross-tenant suite needs one more authenticated identity and a set of
# resources it does NOT own. Both live here rather than in a second harness: the world, the
# database double and the mounted-app client fixture are already exactly what that suite
# needs, and a second copy of them would be a second thing to keep in step with the seams
# ``conftest.py`` patches.
#
# This user is authenticated and legitimate - it is not an attacker with a forged token.
# Requirement 20.2 is about what an ordinary, logged-in stranger can learn from an
# identifier, so the only thing that distinguishes it from the owner is that it owns
# nothing.

#: The non-owning authenticated caller. Requirement 20.4's "non-owning authenticated user".
STRANGER_USER: Dict[str, Any] = {
    "id": "0c9a5e13-7b42-4d8f-a6e1-3f5b9d2c4a76",
    "email": "cross-tenant-stranger@example.com",
    "role": "authenticated",
    "access_token": "token-cross-tenant-stranger",
}

# The five resources the owner owns, and the five identifiers that name nothing at all.
#
# FIXED LITERALS, AND ALL THE SAME LENGTH, ON PURPOSE
#     Fixed so the suite is reproducible (a minted id would put a different string in every
#     run's assertion message), and all 36-character UUIDs so that two responses which
#     differ only by the identifier the caller itself supplied still agree on
#     ``Content-Length``. If the "owned" ids and the "missing" ids had different lengths,
#     every echoed-identifier response would differ by a header for a reason that has
#     nothing to do with existence.
OWNED_STRATEGY_ID = "b1d4c8e0-1111-4a00-8000-000000000001"
OWNED_VERSION_ID = "b1d4c8e0-1111-4a00-8000-000000000002"
OWNED_BACKTEST_ID = "b1d4c8e0-1111-4a00-8000-000000000003"
OWNED_DEPLOYMENT_ID = "b1d4c8e0-1111-4a00-8000-000000000004"
OWNED_SIGNAL_ID = "b1d4c8e0-1111-4a00-8000-000000000005"

#: ``strategy_versions.version`` - a LABEL, not the row's id. It is what
#: ``.../versions/{version}/deploy`` and ``_load_deployable_version`` resolve on, so the
#: deploy and preflight probes name this and not :data:`OWNED_VERSION_ID`.
OWNED_VERSION_LABEL = "v1"

#: Identifiers that belong to nobody, because no row carries them. Same length as their
#: owned counterparts (see above).
MISSING_STRATEGY_ID = "f7a30b62-2222-4a00-8000-000000000001"
MISSING_VERSION_ID = "f7a30b62-2222-4a00-8000-000000000002"
MISSING_BACKTEST_ID = "f7a30b62-2222-4a00-8000-000000000003"
MISSING_DEPLOYMENT_ID = "f7a30b62-2222-4a00-8000-000000000004"
MISSING_SIGNAL_ID = "f7a30b62-2222-4a00-8000-000000000005"

#: A row in the owner's own vault. Named by id and nothing else: the binding is a
#: reference, and no credential appears anywhere in this harness (Requirements 13.9, 21.7).
SANDBOX_EXCHANGE_ACCOUNT_ID = "9c1e4d7a-8b23-4f56-91a0-2e7d5c3b6f84"
SANDBOX_RISK_CONFIG_ID = "1a2b3c4d-5e6f-4708-8192-a3b4c5d6e7f8"


# ══════════════════════════════════════════════════════════════════════════
# 1. THE SEEDED SYNTHETIC FEED (Requirement 26.1)
# ══════════════════════════════════════════════════════════════════════════


class SeededSyntheticFeed:
    """A market that is a pure function of a seed. The only data source in this suite.

    Shaped as the one method the platform's historical read actually calls:
    ``DataEngine.fetch_historical_ohlcv`` pages through ``exchange.fetch_ohlcv(symbol,
    timeframe, since=..., limit=...)`` and nothing else, so that is what this exposes.
    ``fetch_ohlcv``'s pagination contract is honoured exactly (``since`` is inclusive,
    fewer rows than ``limit`` means the series is exhausted), because the real pager
    loops until it is told to stop.

    THE SHAPE OF THE SERIES, AND WHY IT IS NOT A PURE RANDOM WALK
        The fixture strategy is ``ohlcv_feed -> ema(20) -> gt(vs constant) -> buy``, so a
        series that only drifts would leave the comparator on one side of the constant for
        the whole window and the backtest would have nothing to simulate. The closes
        therefore oscillate around :attr:`base` with a fixed period, plus a seeded jitter -
        deterministic, and it crosses the constant many times.
    """

    #: The comparator's constant is set to this, so the gate flips rather than latching.
    base: float = 100.0

    def __init__(
        self,
        *,
        seed: int = SANDBOX_SEED,
        bars: int = SANDBOX_BARS,
        first_bar: datetime = SANDBOX_FIRST_BAR,
        interval: timedelta = timedelta(hours=1),
    ):
        self.seed = int(seed)
        self.first_bar = first_bar
        self.interval = interval
        self.fetch_calls: List[Dict[str, Any]] = []
        self.bars: List[List[float]] = self._build(int(bars))

    # ── construction ─────────────────────────────────────────────────────
    def _build(self, count: int) -> List[List[float]]:
        rng = random.Random(self.seed)
        start_ms = int(self.first_bar.timestamp() * 1000)
        step_ms = int(self.interval.total_seconds() * 1000)
        rows: List[List[float]] = []
        previous_close = self.base
        for index in range(count):
            # Closed-form oscillation, so the gate flips on a fixed schedule, plus a
            # seeded jitter so the series is not artificially smooth. Both are functions
            # of the seed and the index alone.
            swing = 9.0 * math.sin(index / 8.0)
            jitter = rng.uniform(-0.75, 0.75)
            close = round(self.base + swing + jitter, 8)
            open_ = round(previous_close, 8)
            high = round(max(open_, close) + abs(jitter) / 2.0, 8)
            low = round(min(open_, close) - abs(jitter) / 2.0, 8)
            volume = round(50.0 + rng.uniform(0.0, 10.0), 8)
            rows.append([start_ms + index * step_ms, open_, high, low, close, volume])
            previous_close = close
        return rows

    # ── the one exchange method the historical read uses ─────────────────
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = SANDBOX_TIMEFRAME,
        since: Optional[int] = None,
        limit: int = 500,
        **_kwargs: Any,
    ) -> List[List[float]]:
        self.fetch_calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        rows = self.bars if since is None else [r for r in self.bars if r[0] >= since]
        return [list(row) for row in rows[: int(limit)]]

    # ── the live half: one bar, as a market context ──────────────────────
    def bar(self, index: int) -> Dict[str, Any]:
        """One bar as the market-context mapping an ACTION-node output carries."""
        timestamp, open_, high, low, close, volume = self.bars[index]
        moment = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        return {
            "symbol": SANDBOX_SYMBOL,
            "timeframe": SANDBOX_TIMEFRAME,
            "bar": moment.isoformat(),
            "bar_time": moment.isoformat(),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "price": close,
            "volume": volume,
        }

    @property
    def last_bar_time(self) -> datetime:
        return datetime.fromtimestamp(self.bars[-1][0] / 1000, tz=timezone.utc)

    def digest(self) -> str:
        """A stable fingerprint of the whole series, for a determinism assertion."""
        import hashlib
        import json

        return hashlib.sha256(json.dumps(self.bars, sort_keys=True).encode()).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# 2. THE SANDBOXED EXCHANGE CLIENT (Requirement 26.3, 26.4)
# ══════════════════════════════════════════════════════════════════════════

#: Every method name that places an order at a venue, on either side of the platform's
#: order-placement seam:
#:
#: * ``create_order`` and its typed variants are CCXT's own, and ``create_order`` is
#:   literally the call ``exchange_executor.CCXTExchangeExecutor.place_order`` makes;
#: * ``edit_order`` replaces a live order, which is a placement;
#: * ``place_order`` / ``submit_order_idempotent`` are the platform's own wrappers
#:   (``exchange_executor``, ``distributed_execution.idempotent_exchange_submission``),
#:   named here so a component that reaches for the wrapper instead of the raw client is
#:   still counted.
#:
#: Requirement 26.4's assertion is a count over ALL of them, not over one, because "no
#: real exchange order-placement call" is a claim about the seam and not about one spelling
#: of it.
ORDER_PLACEMENT_SEAMS: Tuple[str, ...] = (
    "create_order",
    "create_market_order",
    "create_limit_order",
    "create_market_buy_order",
    "create_market_sell_order",
    "create_post_only_order",
    "edit_order",
    "place_order",
    "submit_order_idempotent",
)


class SandboxExchangeClient:
    """The venue that must never be asked to place an order, and that counts being asked.

    Every name in :data:`ORDER_PLACEMENT_SEAMS` is a real coroutine on this object that
    RECORDS the attempt and returns a plausible order. It does not raise: an exception
    would make the zero-call assertion pass for the wrong reason (the call happened and
    blew up) and would hide the positive control that proves the counter works at all.

    The read-only methods are here too, and are counted separately, because a sandbox is
    allowed to look at a venue - Requirement 26.3 forbids routing a Signal to an
    order-placement call, not reading a price.
    """

    def __init__(self) -> None:
        #: Every order-placement attempt, in order. THIS is what Requirement 26.4's
        #: "call count == 0" is measured on.
        self.placements: List[Dict[str, Any]] = []
        #: Read-only venue traffic, counted apart so it can never be mistaken for a
        #: placement.
        self.reads: List[Dict[str, Any]] = []
        self._order_seq = 0
        for name in ORDER_PLACEMENT_SEAMS:
            setattr(self, name, self._placement_seam(name))

    def _placement_seam(self, name: str):
        async def seam(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            self._order_seq += 1
            order_id = f"SANDBOX-VENUE-{self._order_seq}"
            self.placements.append(
                {"seam": name, "args": args, "kwargs": kwargs, "order_id": order_id}
            )
            return {
                "id": order_id,
                "clientOrderId": kwargs.get("client_order_id")
                or kwargs.get("clientOrderId"),
                "symbol": kwargs.get("symbol"),
                "side": kwargs.get("side"),
                "amount": kwargs.get("amount") or kwargs.get("quantity"),
                "status": "open",
                "filled": 0.0,
            }

        seam.__name__ = name
        return seam

    async def fetch_order(self, order_id: str, symbol: Optional[str] = None) -> Dict[str, Any]:
        self.reads.append({"seam": "fetch_order", "order_id": order_id})
        for placement in self.placements:
            if placement["order_id"] == order_id:
                return {"id": order_id, "status": "open", "filled": 0.0}
        return {}

    async def fetch_balance(self) -> Dict[str, Any]:
        self.reads.append({"seam": "fetch_balance"})
        return {"total": {"USDT": 100_000.0}, "free": {"USDT": 100_000.0}}

    # ── observation ──────────────────────────────────────────────────────
    @property
    def order_placement_calls(self) -> int:
        """The number Requirement 26.4 requires to be zero for a sandbox deployment."""
        return len(self.placements)

    def placements_by_seam(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for placement in self.placements:
            counts[placement["seam"]] = counts.get(placement["seam"], 0) + 1
        return counts


# ══════════════════════════════════════════════════════════════════════════
# 3. THE SANDBOX'S EXECUTION COMPONENT
# ══════════════════════════════════════════════════════════════════════════
#
# WHY THIS OBJECT EXISTS AT ALL, STATED PLAINLY
# ---------------------------------------------
# Requirement 27.1 puts "Paper Trading as a distinct execution mode" explicitly OUT OF
# SCOPE for this specification, and no task in this plan implements it. So there is no
# shipped component that decides what a `mode='paper'` deployment does with an approved
# Signal, and this suite must supply one. It is the sandbox's execution component, not a
# stub of a production one, and its whole contract is the two things Requirement 26.3
# demands of a non-production mode:
#
#   (a) it does not route the Signal to a real exchange order-placement call, and
#   (b) it drives the Signal to a TERMINAL Order_Lifecycle_State that is distinguishable
#       from the state a real fill produces.
#
# ON THE STATE IT REPORTS, AND WHY IT IS `REJECTED`
# ------------------------------------------------
# The canonical vocabulary's four terminal states are CLOSED, FAILED, CANCELLED and
# REJECTED (`order_lifecycle_state.TERMINAL_STATES`). A real fill lands on EXECUTED and
# then CLOSED, so (b) rules CLOSED out and leaves three. Of those, `ExecutionOutcome`
# documents exactly one as "the execution/guard component REJECTED the signal. **No order
# was placed**" - a refusal - and that sentence is (a) verbatim. FAILED would claim the
# venue answered and refused; CANCELLED would claim something cancelled an order that was
# never offered. So a refusal is reported, `resolve_execution_state` maps it to REJECTED,
# and the reason names the sandbox and the mode so the Signal_Trace_Page can say WHY the
# state is not a fill as well as THAT it is not.
#
# A NOTE WORTH CARRYING FORWARD (not a defect in this specification's code)
# ------------------------------------------------------------------------
# `ExecutionOutcome`'s docstring says `accepted` means "the order reached the exchange (or
# its sandbox)", and `exchange_simulator.PaperTradingExchange` already simulates fills.
# A future Paper Trading mode (Requirement 27) that reports a simulated FILL would land on
# EXECUTED -> CLOSED, which is precisely the state a real fill produces - so Requirement
# 26.3's "distinguishable" clause would then need either an additive tenth state or an
# explicit reliance on `signals.mode`. This suite asserts `mode` on the trace item as well
# as the state, so whichever way that is resolved, the distinction is already recorded.


class SandboxExecutionComponent:
    """The order validation/execution component a sandbox deployment is wired to.

    Holds the venue client, which is the point: the zero-call assertion is only worth
    making about a component that COULD have placed an order. When ``mode`` is ``live``
    this really does place one at :class:`SandboxExchangeClient` - that branch is the
    positive control that proves the counter is not vacuous - and when it is anything else
    the client is not touched.

    Exposes ``submit_order``, one of the seams ``signal_service._call_execution_engine``
    already knows how to call, so nothing about the submission path is special-cased for
    the sandbox.
    """

    def __init__(self, exchange: SandboxExchangeClient, *, mode: str = "paper"):
        self.exchange = exchange
        self.mode = str(mode)
        self.calls: List[str] = []

    async def submit_order(self, signal: Any) -> Any:
        from backend_app.backend.signal_service import ExecutionOutcome

        self.calls.append(signal.id)

        if self.mode == "live":
            # The real seam. Reached only by the positive-control test.
            order = await self.exchange.create_order(
                symbol=signal.symbol,
                type="market",
                side=(signal.side or signal.decision or "buy").lower(),
                amount=signal.quantity,
                client_order_id=signal.idempotency_key,
            )
            return ExecutionOutcome(
                accepted=True,
                order_id=order["id"],
                order_state=order["status"],
                filled=order["filled"],
                quantity=signal.quantity,
                reason="placed at the venue",
                detail={"seam": "sandbox execution component (live)", "mode": self.mode},
            )

        return ExecutionOutcome(
            accepted=False,
            refused=True,
            reason=(
                f"sandbox deployment: this deployment's mode is '{self.mode}', which is "
                f"not 'live', so the Signal was NOT routed to a real exchange "
                f"order-placement call and no order exists at any venue "
                f"(Requirement 26.3)"
            ),
            quantity=signal.quantity,
            detail={
                "seam": "sandbox execution component",
                "mode": self.mode,
                "routed_to_exchange": False,
                "order_placement_calls": self.exchange.order_placement_calls,
            },
        )


# ══════════════════════════════════════════════════════════════════════════
# 4. THE DATABASE DOUBLE
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data: Any = None, error: Any = None):
        self.data = data
        self.error = error


class SandboxQuery:
    """One chained PostgREST query, recorded in full and then actually applied.

    Applied, not merely recorded: several of the claims this suite makes are claims about
    which rows a filter selects (the listing excludes nothing it should carry, the trace
    list finds the signal by deployment), and a recording-only double would let a wrong
    predicate pass.

    ``execute()`` returns a coroutine. Every caller in the production code either awaits
    it unconditionally (``routers/strategies.py``) or awaits it through
    ``inspect.isawaitable`` (everything else), so one awaitable shape serves both.
    """

    def __init__(self, db: "SandboxDatabase", table: str):
        self.db = db
        self.table = table
        self.verb = "select"
        self.columns = "*"
        self.payload: Any = None
        self.filters: List[Tuple[str, str, Any]] = []
        self.row_limit: Optional[int] = None
        self.row_range: Optional[Tuple[int, int]] = None
        self.order_by: Optional[Tuple[str, bool]] = None
        self._negate = False

    # ── verbs ────────────────────────────────────────────────────────────
    def select(self, columns: str = "*", *_a: Any, **_k: Any) -> "SandboxQuery":
        self.verb = "select"
        self.columns = columns
        return self

    def insert(self, payload: Any, *_a: Any, **_k: Any) -> "SandboxQuery":
        self.verb = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any, *_a: Any, **_k: Any) -> "SandboxQuery":
        self.verb = "update"
        self.payload = payload
        return self

    def upsert(self, payload: Any, *_a: Any, **_k: Any) -> "SandboxQuery":
        self.verb = "upsert"
        self.payload = payload
        return self

    def delete(self, *_a: Any, **_k: Any) -> "SandboxQuery":
        self.verb = "delete"
        return self

    # ── predicates ───────────────────────────────────────────────────────
    def eq(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("neq", column, value))
        return self

    def in_(self, column: str, values: Any) -> "SandboxQuery":
        self.filters.append(("in", column, list(values)))
        return self

    def gte(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("gte", column, value))
        return self

    def lte(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("lte", column, value))
        return self

    def gt(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("gt", column, value))
        return self

    def lt(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("lt", column, value))
        return self

    def is_(self, column: str, value: Any) -> "SandboxQuery":
        self.filters.append(("is_not" if self._negate else "is", column, value))
        self._negate = False
        return self

    def ilike(self, column: str, pattern: str) -> "SandboxQuery":
        self.filters.append(("ilike", column, pattern))
        return self

    def like(self, column: str, pattern: str) -> "SandboxQuery":
        self.filters.append(("ilike", column, pattern))
        return self

    def or_(self, expression: str) -> "SandboxQuery":
        # PostgREST's `or=(a.eq.x,b.eq.y)`. Recorded and then ignored as a filter: no
        # assertion in this suite rests on an `or_` narrowing a result, and applying it
        # by parsing PostgREST's grammar here would be a second query planner to get
        # wrong. A caller that relies on it therefore sees a SUPERSET, never a subset -
        # which cannot make a "the row is present" assertion pass falsely.
        self.filters.append(("or", "*", expression))
        return self

    @property
    def not_(self) -> "SandboxQuery":
        self._negate = True
        return self

    # ── shaping ──────────────────────────────────────────────────────────
    def order(self, column: str, desc: bool = False, **_k: Any) -> "SandboxQuery":
        self.order_by = (column, bool(desc))
        return self

    def limit(self, count: int, **_k: Any) -> "SandboxQuery":
        self.row_limit = int(count)
        return self

    def range(self, start: int, end: int, **_k: Any) -> "SandboxQuery":
        self.row_range = (int(start), int(end))
        return self

    def single(self) -> "SandboxQuery":
        self.row_limit = 1
        return self

    def maybe_single(self) -> "SandboxQuery":
        self.row_limit = 1
        return self

    # ── terminal ─────────────────────────────────────────────────────────
    def execute(self):
        return self._run()

    async def _run(self) -> _Result:
        return self.db.run(self)

    # ── row matching ─────────────────────────────────────────────────────
    def matches(self, row: Mapping[str, Any]) -> bool:
        for kind, column, value in self.filters:
            if kind == "or":
                continue
            actual = row.get(column)
            if kind == "eq" and str(actual) != str(value):
                return False
            if kind == "neq" and str(actual) == str(value):
                return False
            if kind == "in" and actual not in value and str(actual) not in [str(v) for v in value]:
                return False
            if kind == "gte" and (actual or "") < value:
                return False
            if kind == "lte" and (actual or "") > value:
                return False
            if kind == "gt" and not ((actual or "") > value):
                return False
            if kind == "lt" and not ((actual or "") < value):
                return False
            if kind == "is" and actual is not None:
                return False
            if kind == "is_not" and actual is None:
                return False
            if kind == "ilike":
                needle = str(value).replace("%", "").lower()
                if needle and needle not in str(actual or "").lower():
                    return False
        return True

    def shaped(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected = [dict(row) for row in rows if self.matches(row)]
        if self.order_by is not None:
            column, desc = self.order_by
            selected.sort(key=lambda r: str(r.get(column) or ""), reverse=desc)
        if self.row_range is not None:
            start, end = self.row_range
            selected = selected[start : end + 1]
        if self.row_limit is not None:
            selected = selected[: self.row_limit]
        return selected


class SandboxDatabase:
    """The rows, and the one database-level guarantee this suite leans on.

    ``uq_signals_idempotency_key`` (migration 005b) is enforced, because
    ``signal_service`` translates its ``23505`` into ``DuplicateOrderError`` and the
    "at most one order per signal" half of this chain is meaningless without it.

    Every call is recorded, so a claim about which table was written - and with which
    verb - is a measurement. ``order_lifecycle_transitions`` in particular is append-only
    (Requirement 16.7), and the only way to test "no UPDATE and no DELETE reaches it" is
    to notice one.
    """

    def __init__(self) -> None:
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.calls: List[Dict[str, Any]] = []

    # ── the client surface ───────────────────────────────────────────────
    def table(self, name: str) -> SandboxQuery:
        return SandboxQuery(self, name)

    def from_(self, name: str) -> SandboxQuery:  # pragma: no cover - alias
        return self.table(name)

    # ── reading the world back ───────────────────────────────────────────
    def rows(self, table: str) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.tables.get(table, [])]

    def row(self, table: str, row_id: Any) -> Optional[Dict[str, Any]]:
        for row in self.tables.get(table, []):
            if str(row.get("id")) == str(row_id):
                return dict(row)
        return None

    def seed(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        self.tables.setdefault(table, []).extend(dict(row) for row in rows)

    def verbs_on(self, table: str) -> set:
        return {call["verb"] for call in self.calls if call["table"] == table}

    # ── the engine ───────────────────────────────────────────────────────
    def run(self, query: SandboxQuery) -> _Result:
        self.calls.append(
            {
                "table": query.table,
                "verb": query.verb,
                "columns": query.columns,
                "payload": query.payload,
                "filters": list(query.filters),
            }
        )
        rows = self.tables.setdefault(query.table, [])

        if query.verb == "select":
            return _Result(data=query.shaped(rows))

        if query.verb in ("insert", "upsert"):
            payloads = (
                query.payload if isinstance(query.payload, list) else [query.payload or {}]
            )
            written: List[Dict[str, Any]] = []
            for raw in payloads:
                payload = dict(raw)
                payload.setdefault("id", str(uuid4()))
                if query.table == "signals":
                    # 003: signals.status is NOT NULL DEFAULT 'pending'.
                    payload.setdefault("status", "pending")
                    # 005b section 1: the partial unique index on idempotency_key.
                    key = payload.get("idempotency_key")
                    if key is not None and any(
                        r.get("idempotency_key") == key for r in rows
                    ):
                        raise Exception(
                            "23505 duplicate key value violates unique constraint "
                            '"uq_signals_idempotency_key"'
                        )
                rows.append(payload)
                written.append(dict(payload))
            return _Result(data=written)

        if query.verb == "update":
            touched: List[Dict[str, Any]] = []
            for row in rows:
                if query.matches(row):
                    row.update(query.payload or {})
                    touched.append(dict(row))
            return _Result(data=touched)

        if query.verb == "delete":
            kept = [row for row in rows if not query.matches(row)]
            removed = [dict(row) for row in rows if query.matches(row)]
            self.tables[query.table] = kept
            # PostgREST answers a DELETE with the rows it deleted (supabase-py sends
            # ``Prefer: return=representation`` by default), so ``data`` is EMPTY when the
            # predicate matched nothing. This used to answer ``[{"deleted": n}]``, which is
            # truthy even for ``n == 0`` - a double that reports "something happened" to a
            # caller whose filter matched no row cannot be used to test a "report what was
            # actually deleted" claim, which is what task 22.1's third finding is about.
            return _Result(data=removed)

        return _Result(data=[])  # pragma: no cover


# ══════════════════════════════════════════════════════════════════════════
# 5. THE FIXTURE STRATEGY'S GRAPH
# ══════════════════════════════════════════════════════════════════════════


def sandbox_graph(registry: Any) -> Any:
    """``ohlcv_feed -> ema(20) -> {gt, lt}(vs constant) -> {buy, sell} market actions``.

    The smallest graph the real validator and the real compiler both accept, built from
    the registry's own published descriptors rather than from invented block ids - so the
    save reaches persistence instead of stopping at ``UNRESOLVED_BLOCK``, and the artifacts
    the rest of the chain reads are the ones production would really write.

    Node ids are FIXED LITERALS, never minted. ``dag_hash`` is a function of them, so a
    minted id would make the version's identity differ between two runs of this suite and
    Requirement 26.1's determinism claim would be untestable.

    No ML node, deliberately: Requirement 26.1 asks for a strategy whose behaviour does
    not depend on real-time randomness, and a model artifact would put the version in
    ``TRAINING`` rather than ``READY`` and so make it undeployable here for a reason that
    has nothing to do with this chain.

    WHY THERE IS A SELL SIDE AS WELL AS A BUY SIDE
        The obvious minimal graph is buy-only, and it CANNOT be backtested by this
        platform. ``BacktestRuntime`` derives VectorBT's exits from a NEGATIVE signal
        (``exits = signals < 0``), and ``BacktestEngine`` then computes
        ``position = entries.cumsum() - exits.cumsum()`` and admits an entry only where
        ``position == 0``. ``cumsum`` includes the current bar, so on the very first entry
        bar the position already reads 1 and the entry is filtered out - a buy-only strategy
        therefore yields "Strategy generated 0 signals" and a 422, however many bars its
        gate is true on. That is a defect in ``backtesting_engine.py`` (the position state
        wants a one-bar shift), it predates this specification, and fixing it would move
        every backtest number in the repository including
        ``tests/golden/dag_runtime_plan_golden.json`` - so it is reported rather than
        changed here, and this fixture declares both sides so the chain has something to
        simulate.
    """
    from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph

    data = NodeSpec(
        id="sbx_data",
        block_id="ohlcv_feed",
        category=registry["ohlcv_feed"].category,
        params={
            "symbol": SANDBOX_SYMBOL,
            "timeframe": SANDBOX_TIMEFRAME,
            "market_type": SANDBOX_MARKET_TYPE,
            "mode": "streaming",
        },
    )
    ema = NodeSpec(
        id="sbx_ema",
        block_id="ema",
        category=registry["ema"].category,
        params={"window": 20, "source": "close"},
    )
    floor = NodeSpec(
        id="sbx_floor",
        block_id="constant",
        category=registry["constant"].category,
        params={"value": SeededSyntheticFeed.base},
    )
    above = NodeSpec(
        id="sbx_above", block_id="gt", category=registry["gt"].category, params={}
    )
    below = NodeSpec(
        id="sbx_below", block_id="lt", category=registry["lt"].category, params={}
    )
    buy = NodeSpec(
        id="sbx_buy",
        block_id="action_buy_market",
        category=registry["action_buy_market"].category,
        params={"quantity_type": "percent_of_equity", "quantity": 0.25},
    )
    sell = NodeSpec(
        id="sbx_sell",
        block_id="action_sell_market",
        category=registry["action_sell_market"].category,
        params={"quantity_type": "percent_of_equity", "quantity": 0.25},
    )
    return StrategyGraph(
        name="Deterministic Sandbox Fixture",
        nodes=[data, ema, floor, above, below, buy, sell],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", above.id, "left"),
            EdgeSpec.create(floor.id, "value", above.id, "right"),
            EdgeSpec.create(ema.id, "value", below.id, "left"),
            EdgeSpec.create(floor.id, "value", below.id, "right"),
            EdgeSpec.create(above.id, "out", buy.id, "signal"),
            EdgeSpec.create(below.id, "out", sell.id, "signal"),
        ],
    )


def sandbox_action_output(feed: SeededSyntheticFeed, *, bar_index: int = -1) -> Dict[str, Any]:
    """One triggered ACTION-node evaluation, projected onto the keys task 10.1 reads.

    Shaped exactly as ``tests/test_task_10_3_live_runtime_signal_path.py``'s
    ``action_output`` is - that file owns the adapter from the runtime's own
    ``TradeIntent`` / ``Signal`` vocabularies onto these keys, and duplicating the
    adaptation here would be testing this suite's copy of it. What matters to Requirement
    26 is that the reading comes from the SEEDED FEED and from nowhere else: the price,
    the bar and the market context are all :class:`SeededSyntheticFeed`'s.
    """
    bar = feed.bar(bar_index)
    return {
        "decision": "BUY",
        "side": "BUY",
        "symbol": SANDBOX_SYMBOL,
        "timeframe": SANDBOX_TIMEFRAME,
        "quantity": 0.25,
        "price": bar["close"],
        "source_node_ids": ["sbx_buy"],
        "closure_ready": True,
        "market_context": bar,
        "risk_validation": {"passed": True, "reason": "within limits"},
    }


def all_nodes_ready(plan: Any, *, bars: int) -> Any:
    """A real :class:`dag_engine.PlanRuntimeState` with every node of ``plan`` READY.

    The real class and its real ``mark`` (which refuses a label outside
    ``RUNTIME_STATES``), not a stand-in with a dict on it: Requirement 14.3's closure gate
    reads this object, and a fake one could report a readiness vocabulary the engine does
    not have.
    """
    # ``READY`` is re-exported by ``dag_engine`` from ``model_readiness`` (task 8.6), which
    # is the one place the label is spelled.
    from backend_app.backend.dag_engine import READY, PlanRuntimeState

    state = PlanRuntimeState()
    state.reset_for(plan, bars, {node_id: 0 for node_id in plan.node_index})
    for node_id in plan.node_index:
        state.mark(node_id, READY)
    return state


# ══════════════════════════════════════════════════════════════════════════
# 6. THE WORLD
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class SandboxWorld:
    """Everything the chain acts on: the rows, the store, the feed, the venue, the clock.

    Built by the ``sandbox`` fixture in ``conftest.py``, which also patches the four
    request-scoped-client seams at the modules that own them and points the real
    idempotency layer's Redis handle at :attr:`redis`.
    """

    clock: SandboxClock
    db: SandboxDatabase
    redis: RecordingRedis
    feed: SeededSyntheticFeed
    exchange: SandboxExchangeClient
    user: Dict[str, Any] = field(default_factory=lambda: dict(SANDBOX_USER))

    #: Filled in as the chain runs, so a later step can name what an earlier one produced.
    strategy_id: Optional[str] = None
    version_id: Optional[str] = None
    version_label: Optional[str] = None
    backtest_id: Optional[str] = None
    deployment_id: Optional[str] = None

    # ── the owner's own rows the deploy gate reads ───────────────────────
    def seed_owner_rows(self) -> None:
        """The vault entry and the risk configuration the binding will name.

        Both are REFERENCES. ``exchange_keys`` carries an id and a venue and no
        credential of any kind - the keys are resolved by the execution process, never by
        a request or a response (Requirements 13.9, 21.7), and this suite has no key to
        seed even if it wanted one.

        A ``mode='paper'`` binding is allowed to name an exchange account: Requirement
        13.6 makes one MANDATORY for ``live`` and says nothing about forbidding one
        otherwise. It is named here because the account is what resolves the venue, and
        ``signals.exchange_id`` is ``VARCHAR(50) NOT NULL`` in migration 003 - a Signal
        must say which venue it belongs to.
        """
        self.db.seed(
            "exchange_keys",
            [
                {
                    "id": SANDBOX_EXCHANGE_ACCOUNT_ID,
                    "user_id": self.user["id"],
                    "exchange_id": SANDBOX_VENUE,
                    "label": "sandbox account",
                    "is_active": True,
                }
            ],
        )
        self.db.seed(
            "risk_settings",
            [
                {
                    "id": SANDBOX_RISK_CONFIG_ID,
                    "user_id": self.user["id"],
                    "max_drawdown_pct": 0.2,
                    "risk_per_trade_pct": 0.01,
                }
            ],
        )

    # ── the owner's five resources, for the cross-tenant suite (task 22.1) ─
    def seed_cross_tenant_rows(self) -> None:
        """One strategy, one version, one backtest, one deployment, one signal - all the owner's.

        Requirement 20.4's suite needs a resource of every kind this specification covers
        that **demonstrably exists** and **demonstrably is not the caller's**, so that a
        refusal aimed at it can be compared with a refusal aimed at nothing. The rows are
        written straight into :class:`SandboxDatabase` rather than driven through the API:
        the subject of that suite is what a *stranger* is told about them, and the owner's
        own save/backtest/deploy chain is already Requirement 26.4's suite next door.

        Every row carries ``user_id`` = this world's owner. That column is what the
        endpoints' ownership predicates filter on, and :class:`SandboxQuery` applies those
        predicates, so an endpoint whose ``.eq("user_id", ...)`` went missing selects the
        row here and the suite notices.

        The columns are the ones the endpoints under test actually read - the FK graph of
        ``001_strategy_architecture.sql``/``003``/``005b`` as those handlers traverse it, not
        a full column list. In particular ``strategy_versions.version`` is the label the
        deploy and preflight paths resolve on, ``strategy_backtests.version_id`` is the
        immutable version the result is traceable to, and ``signals.exchange_id`` is
        ``VARCHAR(50) NOT NULL`` in migration 003 so a signal must name its venue.
        """
        owner = self.user["id"]
        moment = SANDBOX_FIRST_BAR.isoformat()

        self.db.seed(
            "strategies",
            [
                {
                    "id": OWNED_STRATEGY_ID,
                    "user_id": owner,
                    "name": "The owner's only strategy",
                    "description": "Seeded by task 22.1's cross-tenant suite.",
                    "symbol": SANDBOX_SYMBOL,
                    "timeframe": SANDBOX_TIMEFRAME,
                    "status": "running",
                    "is_active": True,
                    "version": 1,
                    "buy_logic": {"_nodes": [], "_edges": []},
                    "created_at": moment,
                    "updated_at": moment,
                }
            ],
        )
        self.db.seed(
            "strategy_versions",
            [
                {
                    "id": OWNED_VERSION_ID,
                    "strategy_id": OWNED_STRATEGY_ID,
                    "user_id": owner,
                    "version": OWNED_VERSION_LABEL,
                    "is_current": True,
                    "lifecycle_state": "READY",
                    "validation_state": "VALID",
                    "dag_hash": "0" * 64,
                    "graph_json": {"nodes": [], "edges": []},
                    "compiled_plan": {"nodes": [], "edges": []},
                    "created_at": moment,
                    "updated_at": moment,
                }
            ],
        )
        self.db.seed(
            "strategy_backtests",
            [
                {
                    "id": OWNED_BACKTEST_ID,
                    "strategy_id": OWNED_STRATEGY_ID,
                    "user_id": owner,
                    "version": OWNED_VERSION_LABEL,
                    "version_id": OWNED_VERSION_ID,
                    "status": "completed",
                    "dataset": "seeded",
                    "start_date": moment,
                    "end_date": moment,
                    "initial_capital": 10_000.0,
                    # ``get_backtest_report`` reads these two with ``[]`` rather than
                    # ``.get``, so a row without them is a 500 for its own owner. Seeded so
                    # the owner-side control on that endpoint is a real 200.
                    "commission": 0.001,
                    "slippage": 0.0005,
                    "total_return_pct": 1.25,
                    "created_at": moment,
                    "completed_at": moment,
                }
            ],
        )
        self.db.seed(
            "strategy_deployments",
            [
                {
                    "id": OWNED_DEPLOYMENT_ID,
                    "strategy_id": OWNED_STRATEGY_ID,
                    "user_id": owner,
                    "version_id": OWNED_VERSION_ID,
                    "version": OWNED_VERSION_LABEL,
                    "status": "running",
                    "binding_state": "RUNNING",
                    "mode": "paper",
                    "environment": "paper",
                    "exchange_id": SANDBOX_VENUE,
                    "exchange_account_id": SANDBOX_EXCHANGE_ACCOUNT_ID,
                    "risk_config_id": SANDBOX_RISK_CONFIG_ID,
                    "created_at": moment,
                    "updated_at": moment,
                }
            ],
        )
        self.db.seed(
            "signals",
            [
                {
                    "id": OWNED_SIGNAL_ID,
                    "user_id": owner,
                    "strategy_id": OWNED_STRATEGY_ID,
                    "strategy_version": OWNED_VERSION_LABEL,
                    "version_id": OWNED_VERSION_ID,
                    "deployment_id": OWNED_DEPLOYMENT_ID,
                    "exchange_id": SANDBOX_VENUE,
                    "symbol": SANDBOX_SYMBOL,
                    "timeframe": SANDBOX_TIMEFRAME,
                    "decision": "BUY",
                    "side": "BUY",
                    "quantity": 0.25,
                    "price": SeededSyntheticFeed.base,
                    "status": "pending",
                    "order_lifecycle_state": "GENERATED",
                    "idempotency_key": "cross-tenant-seeded-signal",
                    "generated_at": moment,
                    "created_at": moment,
                }
            ],
        )

        # So a test that reads the world back through the world (rather than through the
        # API) names the same rows the stranger will be probing.
        self.strategy_id = OWNED_STRATEGY_ID
        self.version_id = OWNED_VERSION_ID
        self.version_label = OWNED_VERSION_LABEL
        self.backtest_id = OWNED_BACKTEST_ID
        self.deployment_id = OWNED_DEPLOYMENT_ID

    # ── reading the world back ───────────────────────────────────────────
    def strategy_row(self) -> Optional[Dict[str, Any]]:
        return self.db.row("strategies", self.strategy_id)

    def version_row(self) -> Optional[Dict[str, Any]]:
        return self.db.row("strategy_versions", self.version_id)

    def backtest_rows(self) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.db.rows("strategy_backtests")
            if str(row.get("strategy_id")) == str(self.strategy_id)
        ]

    def deployment_row(self) -> Optional[Dict[str, Any]]:
        return self.db.row("strategy_deployments", self.deployment_id)

    def signal_rows(self) -> List[Dict[str, Any]]:
        return self.db.rows("signals")

    def transitions(self, signal_id: str) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.db.rows("order_lifecycle_transitions")
            if str(row.get("signal_id")) == str(signal_id)
        ]

    # ── the live signal path, wired to this world ────────────────────────
    def signal_path(self, *, mode: Optional[str] = None) -> Tuple[Any, Any, Any]:
        """``(LiveSignalPath, risk component, execution component)`` for this deployment.

        The path is the shipped ``signal_service.LiveSignalPath``, holding the shipped
        ``DistributedIdempotencyLayer``; only the venue client and the market reading are
        this suite's.
        """
        from backend_app.backend.signal_service import LiveSignalPath
        from backend_app.core.distributed_idempotency import DistributedIdempotencyLayer

        row = self.deployment_row() or {}
        effective_mode = mode or row.get("mode") or "paper"
        risk = RecordingRiskEngine()
        execution = SandboxExecutionComponent(self.exchange, mode=effective_mode)
        plan = self.compiled_plan()
        path = LiveSignalPath(
            row,
            risk_engine=risk,
            execution_engine=execution,
            sb=self.db,
            user=self.user,
            idempotency_layer=DistributedIdempotencyLayer(),
            plan=plan,
            timeframe=SANDBOX_TIMEFRAME,
            clock=lambda: self.feed.last_bar_time,
        )
        return path, risk, execution

    def compiled_plan(self) -> Any:
        """The version's OWN persisted plan, read back through the shipped loader.

        ``strategy_compiler.load_plan`` is what the backtester and the live runtime both
        use (Requirement 22.3), so the plan the closure gate consults here is the same
        artifact the backtest executed - not a recompilation that could differ.
        """
        from backend_app.backend.strategy_compiler import load_plan

        return load_plan(self.version_row() or {}).plan
