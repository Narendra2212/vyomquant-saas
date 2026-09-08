"""
backend/paper/paper_simulator.py - the two simulator guards, and the frozen session config.

Spec: marketplace-subscriptions-paper-trading tasks 25.1 and 25.2. ``design.md`` ->
"``paper/paper_simulator.py``". Requirements 13.11, 15.6, 16.5, 16.12, 17.5, 18.1, 18.2, 28.3.

This file is deliberately only the *first two* pieces of task 25. ``submit_intent`` (25.3),
``apply_fill`` (25.4), the fill model (25.5) and the retry loop (25.6) are separate dispatches
and are NOT here. What is here is everything that has to exist **before** anything calls a
simulator at all:

Exposes
-------
FORBIDDEN_SIMULATORS            the one ``(module, qualname)`` pair no Paper_Session may use
FORBIDDEN_SIMULATOR_EVIDENCE    the three lines that make it forbidden, verified by reading it
SIMULATOR_MODULE / _QUALNAME / SIMULATOR_NAME   this module's own simulator identity
PaperSimulatorMisconfigured     500 ``PAPER_SIMULATOR_MISCONFIGURED`` (Requirement 13.11)
simulator_identity(candidate)   ``(candidate.__module__, candidate.__qualname__)``, no import
assert_paper_simulator(cand)    guard 1: refuse the forbidden simulator
audit_simulator_misconfigured   Requirement 13.11's Audit_Log half, awaited by the start path

MarketMetadata                  the three figures Requirement 16.5 compares against
PaperMarketMetadataUnavailable  409 ``PAPER_START_REFUSED``, validation ``MARKET_METADATA_*``
PRECISION_MODE_DECIMAL_PLACES / PRECISION_MODE_TICK_SIZE
market_metadata(entry, ...)     one exchange market entry -> :class:`MarketMetadata`
resolve_market_metadata(...)    the same, looked up by symbol in an exchange market map

SessionConfig                   ``paper_sessions.config``, as a frozen value object
CONFIG_KEYS                     the fifteen keys, in ``design.md``'s order
SCHEMA_VERSION                  ``"paper.v1"``
freeze_session_config(...)      build it once, at session start
session_config_from_jsonb(...)  read a stored one back exactly

The second guard is not code in this module at all: it is
``tests/test_paper_no_random.py``, which AST-walks every module under
``backend_app/backend/paper/`` and fails on ``import random``, ``from random import ...``, any
``numpy.random`` attribute access and any import of ``backend_app.backend.exchange_simulator``.
A guard that lives in a test rather than in an ``if`` is the right shape here: the condition it
checks ("no paper module can reach a randomised value") is a property of the whole package, and
no runtime branch can observe that.

GUARD 1: WHY THE COMPARISON IS A TUPLE OF STRINGS
-------------------------------------------------
``assert_paper_simulator`` compares ``(candidate.__module__, candidate.__qualname__)`` against
:data:`FORBIDDEN_SIMULATORS`. It does **not** do
``isinstance(candidate, exchange_simulator.PaperTradingExchange)``, and it does not import that
module to build the comparison, because importing it would execute its module body - whose line
49 is ``import random`` - and put a randomness source in the import graph of the one module that
exists to keep randomness out. A guard whose own operation defeats the guarantee it asserts is
not a guard. Two strings compared to two strings needs nothing loaded.

The cost of that choice, stated rather than left to be discovered: a **subclass** of
``PaperTradingExchange`` carries its own ``__qualname__`` and is therefore NOT matched. That is
accepted, because the alternative is the import above. A subclass would also have to be written
deliberately, inside this repository, to route around a named refusal - and it would still be
caught by ``tests/test_paper_no_random.py``, which fails on *any* import of
``backend_app.backend.exchange_simulator`` from the paper package regardless of what is done
with it. The two halves of task 25.1 cover each other's gap; neither covers it alone.

WHAT MAKES THAT SIMULATOR FORBIDDEN (Requirement 13.11)
-------------------------------------------------------
Read out of ``backend_app/backend/exchange_simulator.py`` and re-verified against the file at
the time this module was written, not copied from the spec:

* line 49   ``import random``
* line 319  ``noise = random.gauss(0, base_price * self._price_volatility)``
* line 369  ``if random.random() > self.fill_probability:``
* line 753  ``change = random.gauss(0, base_price * self._price_volatility)``

So its prices are drawn from a Gaussian around a base price and its fills are decided by a
coin weighted with ``fill_probability``. A Paper_Session running on it would report a track
record of a market that never traded (Requirements 14.9, 16.12, 28.3).

The module is **not** modified and **not** deleted. Requirement 13.8 confines it to internal
staging self-tests, which is a statement about which paths reach it, not about whether it
exists - and deleting it would break the staging self-tests that legitimately use it while
proving nothing about the request path. It is kept out, not removed.

GUARD 2's SUBJECT: WHY THERE IS NO ``random`` HERE EITHER
--------------------------------------------------------
Nothing in this module draws, seeds or consults a random source, and nothing it imports does on
its behalf. Partial fills in task 25.5 are capped by a deterministic participation rate rather
than by a probability, and slippage is a recorded rate applied in the adverse direction - both
are functions of the frozen config below and of a validated market event, so replaying the same
event sequence produces the same fills (Requirement 15.4).

THE FROZEN CONFIGURATION (Requirement 16.12)
--------------------------------------------
``paper_sessions.config JSONB`` is written **once**, in the INSERT that creates the session, and
never updated. Three separate things make that true rather than intended:

1. :class:`SessionConfig` is a frozen dataclass, so an object handed to the simulator cannot be
   edited in place mid-session.
2. ``paper_repository`` exposes a config **reader** and a config **serialiser** and no config
   updater. ``update_session_feed`` - the only UPDATE against ``paper_sessions`` in the
   codebase - writes three named feed columns and cannot be made to carry a fourth.
3. ``trg_paper_session_config_immutable`` in ``009_paper_trading.sql`` (section 14d) is a
   ``BEFORE UPDATE ... FOR EACH ROW`` trigger that raises ``23514`` when
   ``NEW.config IS DISTINCT FROM OLD.config``. That one is the guarantee: it holds against a
   direct ``psql`` session, a future handler and a mistake, not only against this module. It was
   read and verified rather than assumed, so no migration 015 was needed for task 25.2.

A mid-session fee change is therefore unrepresentable, which is what Requirement 16.12 asks for
- "captured at session start and SHALL remain unchanged for the session's lifetime" - rather
than discouraged by convention.

WHY EVERY NUMBER IN THAT JSONB IS A STRING
------------------------------------------
``fee_rate``, ``slippage_rate``, ``participation_rate`` and ``max_order_quantity`` are exact
``Decimal`` in memory and exact decimal **strings** in the JSONB, for the reason
``paper_repository._jsonb`` already documents: ``json`` renders a float at ``repr`` precision and
a driver reads it back as a binary float, so a fee rate stored as a JSON *number* is a rate the
session cannot reproduce - and Requirement 18.1 forbids the arithmetic that would follow. The
three precisions and the minor-unit exponent are plain ``int`` because a count of decimal places
is an integer and JSON represents integers exactly.

REFUSAL, NOT A DEFAULT, WHEN THE MARKET METADATA IS ABSENT
----------------------------------------------------------
``price_precision``, ``quantity_precision`` and ``max_order_quantity`` are read from the
exchange market metadata for the session's symbol, so Requirement 16.5's precision checks
compare against a recorded value. If that metadata cannot be read - no market map, no entry for
the symbol, or an entry that states no precision or no maximum - :func:`market_metadata` raises
:class:`PaperMarketMetadataUnavailable` and the session does not start. Nothing is defaulted to
``2`` and ``8``.

A defaulted precision would be the fabricated figure Requirement 28.3 forbids, and it would
fail in the direction that costs something: too *loose* a precision admits an order the venue
would reject, and the resulting fills, balances and reported returns would be of a market that
does not exist. This codebase has already made the same call twice - ``paper_repository``
answers 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` rather than serving a remembered balance, and
``asset_universe`` answers 503 rather than serving a hardcoded symbol list - and this is the
third instance of that one decision, not a new policy.

The code the refusal carries is ``PAPER_START_REFUSED`` with
``details["validation"] = "MARKET_METADATA_UNAVAILABLE"``. That is the existing catalogue entry
for exactly this shape: Requirement 17.4 lists "the symbol against the session's exchange market
metadata" among the start validations, and Requirement 17.13 requires a failed start validation
to return an error naming the validation that refused, creating no session. No new error code
was added, because the catalogue already answers this.

WHERE THE MARKET MAP COMES FROM
-------------------------------
``asset_universe`` is the platform's market-metadata reader and the only one:
``load_exchange_markets(exchange_id)`` returns one venue's CCXT market map through
``connection_engine.ConnectionEngine.connect()`` (which owns the retrying ``load_markets``), and
refuses the ``DEV_MODE`` mock market map outright - the same refusal Requirement 14.8 requires of
the feed. :func:`resolve_market_metadata` takes that map as a **value**, and reads
``precision.price``, ``precision.amount`` and ``limits.amount.max`` out of the entry for the
session's symbol, exactly as ``asset_universe._asset_from_market`` reads four figures out of the
same shape. This module opens no exchange connection of its own and holds no cache: it stays a
pure value-to-value module, which is the purity contract
``backend_app/backend/paper/__init__.py`` states for this package and the reason a Paper_Session
can never reach an exchange trading endpoint through it (Requirement 13.2).

WHAT IS DELIBERATELY NOT HERE
-----------------------------
* The feed gate. ``paper_market_feed.admit_execution(session_row)`` /
  :class:`~paper_market_feed.ExecutionAdmission` / ``FeedNotHealthy`` /
  ``TRADEABLE_FEED_STATES`` already exist and are what task 25.4 must consult before pricing a
  fill. It is not duplicated, wrapped or re-implemented here; tasks 25.1 and 25.2 only need to
  know it is there.
* Every statement. ``paper_repository`` owns the ``paper_sessions`` read and the config
  serialisation (``read_session_config``, ``session_config_payload``); this module produces and
  consumes values.
* Every arithmetic operation on money. ``paper_accounting`` owns it, and
  :meth:`SessionConfig.accounting` hands it the subset of these keys it reads.
* The session INSERT that first writes ``config``. That is the session-start service of task
  26.x, which must route its ``config`` value through
  ``paper_repository.session_config_payload`` rather than building a payload of its own.
* ``market_type``. ``design.md``'s frozen config lists exactly the fifteen keys of
  :data:`CONFIG_KEYS` and ``market_type`` is not among them, so it is not written here even
  though :func:`market_metadata` resolves it and ``AccountingConfig`` would read it. The
  consequence is stated plainly rather than papered over: with no recorded market type,
  ``AccountingConfig.supports_margin`` is ``False`` and a session reports **no** margin figure.
  For a ``spot`` session that is exactly Requirement 18.12; for a ``swap`` or ``future`` session
  it is a gap, and closing it means adding a sixteenth key with the spec's agreement.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Iterable as _AbcIterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from backend_app.backend.asset_universe import SUPPORTED_MARKET_TYPES
from backend_app.backend.marketplace.money import (
    UnsupportedCurrency,
    minor_unit_exponent,
)
from backend_app.backend.metrics import guarded_collector
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.errors import (
    PAPER_CONCURRENCY_CONFLICT,
    PAPER_IDEMPOTENCY_CONFLICT,
    PAPER_INSUFFICIENT_FUNDS,
    PAPER_INVARIANT_VIOLATION,
    PAPER_ORDER_INVALID,
    PAPER_OVER_FILL,
    PAPER_SIMULATOR_MISCONFIGURED,
    PAPER_START_REFUSED,
    PaperError,
)
from backend_app.backend.paper.paper_accounting import (
    COST_BASES,
    DEFAULT_ROUNDING_MODE,
    ROUNDING_MODES,
    WEIGHTED_AVERAGE,
    Account,
    AccountingConfig,
    FillResult,
    InvariantViolation,
    Position,
    assert_invariants,
    last_validated_prices,
    required_funds,
    to_decimal,
)
from backend_app.backend.paper.paper_accounting import apply_fill as accounting_apply_fill
from backend_app.backend.paper.paper_accounting import lock as accounting_lock
from backend_app.backend.paper.paper_market_feed import (
    ExecutionAdmission,
    admit_execution,
    canonical_number,
)
from backend_app.backend.paper.paper_order_state import (
    PaperOrderState,
    can_transition,
    is_terminal,
)
from backend_app.backend.paper.paper_repository import ORDER_SIDES, ORDER_TYPES

logger = logging.getLogger("PaperSimulator")


# ══════════════════════════════════════════════════════════════════════════
# GUARD 1 - THE FORBIDDEN SIMULATOR (Requirements 13.8, 13.11, 15.6)
# ══════════════════════════════════════════════════════════════════════════

#: The ``(module, qualname)`` pairs no Paper_Session may execute against. A set of **string
#: pairs**, never of classes, so resolving it imports nothing (see the module docstring).
FORBIDDEN_SIMULATORS: FrozenSet[Tuple[str, str]] = frozenset(
    {("backend_app.backend.exchange_simulator", "PaperTradingExchange")}
)

#: Why that pair is forbidden, as ``(line, source)`` read out of the file itself. Carried as data
#: so the refusal message can name the evidence, and so a test can hold these lines against the
#: real file: a line number that has drifted makes the message wrong, and a wrong citation in a
#: refusal is worse than none. Verified against ``exchange_simulator.py`` when this module was
#: written - ``import random`` on 49, ``random.gauss`` on 319 and 753, ``random.random()`` on 369.
FORBIDDEN_SIMULATOR_EVIDENCE: Tuple[Tuple[int, str], ...] = (
    (49, "import random"),
    (319, "noise = random.gauss(0, base_price * self._price_volatility)"),
    (369, "if random.random() > self.fill_probability:"),
    (753, "change = random.gauss(0, base_price * self._price_volatility)"),
)

#: The Paper_Simulator this package owns. The class itself arrives with task 25.3; its identity
#: is named here because ``config["simulator"]`` records it at session start (task 25.2), and
#: because :func:`assert_paper_simulator` has to have something to admit.
SIMULATOR_MODULE = __name__
SIMULATOR_QUALNAME = "PaperSimulator"
SIMULATOR_NAME = f"{SIMULATOR_MODULE}.{SIMULATOR_QUALNAME}"

#: ``StrategyAuditAction`` member name for Requirement 13.11's recorded refusal. Resolved by name
#: at call time - the convention ``paper_market_feed.AUDIT_ACTION_REFUSED_MOCK_INTERFACE`` and
#: ``marketplace/settlement_service._write_audit`` already follow - so this module does not pull
#: ``core.audit_trail`` and its Redis handle into every import of the paper package.
AUDIT_ACTION_SIMULATOR_MISCONFIGURED = "PAPER_SIMULATOR_MISCONFIGURED"


class PaperSimulatorMisconfigured(PaperError):
    """500 ``PAPER_SIMULATOR_MISCONFIGURED`` - the resolved simulator is the forbidden one.

    Requirement 13.11: a Paper_Session that resolves its simulator to
    ``exchange_simulator.PaperTradingExchange`` refuses to start, reports a
    simulator-misconfiguration condition, and records the refusal reason in the Audit_Log.

    The status is **pinned**. ``PAPER_SIMULATOR_MISCONFIGURED`` is absent from
    ``ALLOWED_HTTP_STATUS_FOR_CODE``, so ``StructuredError.__init__`` refuses any status other
    than the catalogue's 500 - the same pinning ``PAPER_READ_FAILED`` and
    ``PAPER_MARKET_DATA_UNAVAILABLE`` have, and for the same reason: no call site can turn
    "this server is wired to a random-number generator" into a 200.

    500 rather than 4xx because nothing the caller sent caused it. The caller asked for a paper
    session; the server is misconfigured. ``details`` carries the resolved ``module`` and
    ``qualname`` and the three lines of :data:`FORBIDDEN_SIMULATOR_EVIDENCE` that earn the
    refusal - our own module paths and our own source lines, no identifier belonging to a
    tenant and no credential. The public sentence stays the catalogue's.
    """

    def __init__(
        self,
        module: Any,
        qualname: Any,
        *,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.module = str(module)
        self.qualname = str(qualname)
        merged: Dict[str, Any] = {
            "module": self.module,
            "qualname": self.qualname,
            "evidence": [f"line {line}: {source}" for line, source in FORBIDDEN_SIMULATOR_EVIDENCE],
        }
        if details:
            merged.update(dict(details))
        super().__init__(
            PAPER_SIMULATOR_MISCONFIGURED,
            message=(
                f"{self.module}.{self.qualname} draws its prices from random.gauss "
                f"(lines 319 and 753) and decides its fills with "
                f"random.random() > self.fill_probability (line 369); it is a staging "
                f"self-test harness and no Paper_Session may execute against it "
                f"(Requirements 13.8, 13.11)"
            ),
            details=merged,
        )


def simulator_identity(candidate: Any) -> Tuple[str, str]:
    """``(candidate.__module__, candidate.__qualname__)`` - the identity the guard compares.

    Accepts a class, a function or an instance. For an instance neither attribute is defined on
    the object, so the identity is read off ``type(candidate)``: a caller that resolved a
    simulator by constructing it must be checked on what it constructed.

    Raises:
        PaperSimulatorMisconfigured: If ``candidate`` has no resolvable module and qualname at
            all. That is not a "probably fine" case: the whole guard is an identity comparison,
            so a candidate with no identity is a candidate that cannot be checked, and admitting
            it would make the refusal below decorative.
    """
    module = getattr(candidate, "__module__", None)
    qualname = getattr(candidate, "__qualname__", None)
    if module is None or qualname is None:
        holder = type(candidate)
        module = module if module is not None else getattr(holder, "__module__", None)
        qualname = qualname if qualname is not None else getattr(holder, "__qualname__", None)
    if not module or not qualname:
        raise PaperSimulatorMisconfigured(
            module or "<unknown>",
            qualname or "<unknown>",
            details={"reason": "UNIDENTIFIABLE_SIMULATOR"},
        )
    return (str(module), str(qualname))


def assert_paper_simulator(candidate: Any) -> Tuple[str, str]:
    """Refuse :data:`FORBIDDEN_SIMULATORS`; return the admitted identity.

    The first guard of task 25.1, and the one that has to run before anything calls a simulator
    - which is why :func:`freeze_session_config` calls it: a forbidden simulator cannot even
    reach a frozen session config, let alone an order.

    Returns:
        The ``(module, qualname)`` pair, so a caller can record it in
        ``config["simulator"]`` without deriving it a second time.

    Raises:
        PaperSimulatorMisconfigured: 500, when the identity is in
            :data:`FORBIDDEN_SIMULATORS` or cannot be resolved at all.

    The Audit_Log half of Requirement 13.11 is :func:`audit_simulator_misconfigured`, which is a
    coroutine and therefore cannot be awaited from here. This function stays synchronous
    deliberately: it must be callable from ``freeze_session_config``, from a module-level
    assertion and from a property test, none of which has an event loop to hand. It logs the
    refusal at error level unconditionally, so Requirement 26.5's "log an error for every failure
    on a correctness-critical path" holds even where no caller awaits the audit record.
    """
    identity = simulator_identity(candidate)
    if identity in FORBIDDEN_SIMULATORS:
        logger.error(
            "[paper-simulator] refusing %s.%s: its prices are random.gauss (lines 319, 753) "
            "and its fills are random.random() > self.fill_probability (line 369) "
            "(Requirement 13.11)",
            identity[0],
            identity[1],
        )
        raise PaperSimulatorMisconfigured(identity[0], identity[1])
    return identity


async def audit_simulator_misconfigured(
    identity: Tuple[str, str],
    *,
    actor_id: Any,
    session_id: Any = None,
) -> None:
    """Write Requirement 13.11's ``PAPER_SIMULATOR_MISCONFIGURED`` Audit_Log record.

    Through the existing ``core.audit_trail.StrategyAuditLogger``, which already carries the enum
    member - no second audit facility and no new member. Imported at call time, exactly as
    ``paper_market_feed._audit_mock_interface_refusal`` does, for the same reason.

    ``log`` rather than ``record_or_raise``: the session does not start whether or not the record
    lands, and an audit-storage outage must not turn "we refused to run on a coin flip" into an
    unexplained 500. A failed write is logged at warning level by the audit logger, so the gap is
    visible where the record would have been, and :func:`assert_paper_simulator` has already
    logged the refusal at error level.

    Never raises. The caller is on a refusal path and has an error to raise already.
    """
    try:
        from backend_app.core.audit_trail import (
            StrategyAuditAction,
            get_strategy_audit_logger,
        )
    except Exception as exc:  # noqa: BLE001 - a defined outcome, and already logged at error
        logger.warning(
            "[paper-simulator] the audit facility is unavailable, so the "
            "PAPER_SIMULATOR_MISCONFIGURED record for session %s was not written: %s",
            session_id,
            exc,
        )
        return

    action = getattr(StrategyAuditAction, AUDIT_ACTION_SIMULATOR_MISCONFIGURED, None)
    if action is None:  # pragma: no cover - the member exists; this is the rename guard
        logger.warning(
            "[paper-simulator] core.audit_trail.StrategyAuditAction has no %s member, so the "
            "Requirement 13.11 refusal record for session %s was not written",
            AUDIT_ACTION_SIMULATOR_MISCONFIGURED,
            session_id,
        )
        return

    module, qualname = identity
    try:
        await get_strategy_audit_logger().log(
            action,
            actor_id=str(actor_id),
            resource_type="paper_session",
            resource_id=str(session_id) if session_id is not None else "unstarted",
            reason=(
                "paper session start refused: the resolved simulator draws prices and fills "
                "from random (Requirement 13.11)"
            ),
            metadata={
                "module": module,
                "qualname": qualname,
                "evidence": [
                    f"line {line}: {source}" for line, source in FORBIDDEN_SIMULATOR_EVIDENCE
                ],
            },
        )
    except Exception as exc:  # noqa: BLE001 - logged, then swallowed for the stated reason
        logger.error(
            "[paper-simulator] the PAPER_SIMULATOR_MISCONFIGURED record for session %s could "
            "not be written: %s. The refusal itself stands (Requirement 26.5).",
            session_id,
            exc,
        )


# ══════════════════════════════════════════════════════════════════════════
# THE EXCHANGE MARKET METADATA (Requirements 16.5, 17.4, 17.13, 28.3)
# ══════════════════════════════════════════════════════════════════════════

#: How to read a CCXT ``precision`` figure, when the caller knows. CCXT expresses precision
#: either as a count of decimal places or as a tick size, and which one it means is a property of
#: the *exchange* (``exchange.precisionMode``) rather than of the market entry - so the entry
#: alone is ambiguous for the value ``1``, which is both "one decimal place" and "integer tick".
#: A caller that has the exchange to hand passes the mode and the reading is exact; a caller that
#: does not gets the documented heuristic in :func:`_decimal_places`, which refuses the one
#: genuinely ambiguous value rather than picking a side.
PRECISION_MODE_DECIMAL_PLACES = "DECIMAL_PLACES"
PRECISION_MODE_TICK_SIZE = "TICK_SIZE"
PRECISION_MODES: Tuple[str, ...] = (
    PRECISION_MODE_DECIMAL_PLACES,
    PRECISION_MODE_TICK_SIZE,
)

#: The most decimal places this module will record for a price or a quantity. ``NUMERIC(28,10)``
#: is what ``paper_orders.quantity`` and ``paper_fills.price`` are stored in, so a precision above
#: 10 could not be persisted without being silently rounded by the column - which would make the
#: recorded precision a claim the storage does not keep. Refused instead.
MAX_RECORDED_PRECISION = 10


class PaperMarketMetadataUnavailable(PaperError):
    """409 ``PAPER_START_REFUSED`` - the symbol's exchange market metadata could not be read.

    Requirement 17.4 lists "the symbol against the session's exchange market metadata" among the
    validations a start must complete *before* creating anything, and Requirement 17.13 requires
    a failed start validation to name the validation that refused and to create no session, no
    order and no balance. So this is that catalogue code, with the failing check in
    ``details["validation"]`` - not a new code, because the catalogue already answers this shape.

    409 is the code's default and the right one here: nothing about the request was malformed
    (422) and nothing about the caller was unauthorised (403). The venue's metadata was not
    readable at the moment they asked, which is a state they could not have known.

    ``details`` carries ``validation``, the ``symbol`` and the ``exchange_id`` the caller
    themselves supplied, and ``missing`` naming which figure was absent. No default is
    substituted for any of the three figures (Requirement 28.3).
    """

    #: ``details["validation"]`` values, one per way the metadata can fail to answer. Named
    #: constants rather than inline strings so a caller can branch on them and a test can assert
    #: on them without matching a sentence.
    NO_MARKET_MAP = "MARKET_METADATA_UNAVAILABLE"
    SYMBOL_NOT_LISTED = "SYMBOL_NOT_LISTED"
    FIGURE_NOT_STATED = "MARKET_METADATA_INCOMPLETE"
    MARKET_TYPE_UNSUPPORTED = "MARKET_TYPE_UNSUPPORTED"

    def __init__(
        self,
        validation: str,
        *,
        symbol: Any = None,
        exchange_id: Any = None,
        missing: Any = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.validation = str(validation)
        merged: Dict[str, Any] = {"validation": self.validation}
        if symbol is not None:
            merged["symbol"] = str(symbol)
        if exchange_id is not None:
            merged["exchange_id"] = str(exchange_id)
        if missing is not None:
            merged["missing"] = str(missing)
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_START_REFUSED, details=merged)


@dataclass(frozen=True)
class MarketMetadata:
    """The figures Requirement 16.5's validation compares an order against.

    Frozen, and produced only by :func:`market_metadata`, so the three figures a session records
    are the three figures the venue stated. There is no constructor path that supplies a default
    for any of them.

    Attributes:
        exchange_id: The venue the figures were read from. Recorded because a symbol's tick size
            and maximum differ between venues, and a record that blended two would describe a
            market that trades nowhere - the same rule ``asset_universe.precision_source``
            follows.
        symbol: The canonical symbol, as the venue spells it.
        market_type: ``spot``, ``swap`` or ``future`` - ``asset_universe``'s
            :data:`SUPPORTED_MARKET_TYPES`, imported rather than re-listed.
        price_precision: Decimal places a limit price may carry (Requirement 16.5).
        quantity_precision: Decimal places a quantity may carry (Requirements 16.5, 18.2).
        max_order_quantity: The venue's ``limits.amount.max``, exact.
    """

    exchange_id: str
    symbol: str
    market_type: str
    price_precision: int
    quantity_precision: int
    max_order_quantity: Decimal


def _decimal_places(
    value: Any,
    *,
    what: str,
    symbol: Any,
    exchange_id: Any,
    precision_mode: Optional[str] = None,
) -> int:
    """One CCXT ``precision`` figure as a count of decimal places. Never guessed.

    ``precision_mode`` resolves CCXT's two conventions exactly when the caller knows which one
    the venue uses. Without it:

    * ``0`` -> ``0`` places (integer-only), under either convention.
    * ``0 < value < 1`` -> a tick size; the count is the tick's decimal exponent, so ``0.01`` is
      2 and ``1e-8`` is 8.
    * ``value > 1`` and integral -> a count of decimal places, taken as it is.
    * ``value == 1`` -> **refused**. Under ``DECIMAL_PLACES`` it means one place; under
      ``TICK_SIZE`` it means none. Guessing "one place" would record a precision one place looser
      than the venue's, which admits an order the venue rejects and prices fills against a market
      that does not exist. Guessing "no places" would refuse orders the venue accepts. So neither
      is guessed: the caller passes ``precision_mode``, or the session does not start.
    * anything else (negative, non-finite, non-integral above 1) -> refused.

    A ``float`` is read through ``Decimal(repr(value))`` rather than ``Decimal(value)``. CCXT
    reports these figures as JSON numbers, so a ``float`` is what arrives; ``repr`` is the
    shortest text that round-trips, which makes ``0.01`` exactly one hundredth instead of the
    binary value near it. ``paper_accounting.to_decimal`` refuses a ``float`` outright and is
    right to - by the time a *money* value is a float its exactness is gone - but a tick size is
    a short decimal constant published by the venue, and reading it through ``repr`` recovers it
    exactly. The two readings are different because the two situations are.

    Raises:
        PaperMarketMetadataUnavailable: For an absent, unreadable or ambiguous figure.
    """
    def _refuse(missing: str) -> "PaperMarketMetadataUnavailable":
        return PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.FIGURE_NOT_STATED,
            symbol=symbol,
            exchange_id=exchange_id,
            missing=missing,
        )

    if value is None or isinstance(value, bool):
        raise _refuse(what)

    try:
        if isinstance(value, float):
            number = Decimal(repr(value))
        elif isinstance(value, Decimal):
            number = value
        elif isinstance(value, (int, str)):
            number = Decimal(str(value).strip())
        else:
            raise _refuse(what)
    except (InvalidOperation, ValueError) as exc:
        raise _refuse(what) from exc

    if not number.is_finite() or number < 0:
        raise _refuse(what)

    if precision_mode is not None:
        mode = str(precision_mode).strip().upper()
        if mode not in PRECISION_MODES:
            raise _refuse(f"{what}.precision_mode")
        if mode == PRECISION_MODE_DECIMAL_PLACES:
            if number != number.to_integral_value():
                raise _refuse(what)
            places = int(number)
        else:
            places = _places_of_tick(number, refuse=_refuse, what=what)
    elif number == 0:
        places = 0
    elif number < 1:
        places = _places_of_tick(number, refuse=_refuse, what=what)
    elif number == 1:
        # The one genuinely ambiguous value. Refused rather than resolved by guess.
        raise PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.FIGURE_NOT_STATED,
            symbol=symbol,
            exchange_id=exchange_id,
            missing=f"{what} (1 is ambiguous: pass precision_mode)",
        )
    else:
        if number != number.to_integral_value():
            raise _refuse(what)
        places = int(number)

    if places > MAX_RECORDED_PRECISION:
        raise _refuse(f"{what} ({places} places exceeds NUMERIC(28,10))")
    return places


def _places_of_tick(tick: Decimal, *, refuse: Any, what: str) -> int:
    """The decimal places a tick size implies: ``0.01`` -> 2, ``1E-8`` -> 8, ``5`` -> 0."""
    if tick <= 0:
        raise refuse(what)
    exponent = tick.normalize().as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - non-finite is refused above
        raise refuse(what)
    return max(0, -exponent)


def _positive_decimal(
    value: Any,
    *,
    what: str,
    symbol: Any,
    exchange_id: Any,
) -> Decimal:
    """A venue limit as an exact positive ``Decimal``. Absent means refuse, never a default.

    Read through ``Decimal(repr(value))`` for a ``float``, for the reason
    :func:`_decimal_places` documents: CCXT publishes these as JSON numbers and ``repr`` is the
    shortest round-tripping text, so ``100.0`` is a hundred rather than the binary value near it.
    """
    refusal = PaperMarketMetadataUnavailable(
        PaperMarketMetadataUnavailable.FIGURE_NOT_STATED,
        symbol=symbol,
        exchange_id=exchange_id,
        missing=what,
    )
    if value is None or isinstance(value, bool):
        raise refusal
    try:
        if isinstance(value, float):
            number = Decimal(repr(value))
        elif isinstance(value, Decimal):
            number = value
        elif isinstance(value, (int, str)):
            number = Decimal(str(value).strip())
        else:
            raise refusal
    except (InvalidOperation, ValueError) as exc:
        raise refusal from exc
    if not number.is_finite() or number <= 0:
        raise refusal
    return number


def market_metadata(
    entry: Any,
    *,
    exchange_id: Any,
    symbol: Any = None,
    precision_mode: Optional[str] = None,
) -> MarketMetadata:
    """One exchange market entry as :class:`MarketMetadata`, or a refusal.

    ``entry`` is one value of the CCXT market map - the shape
    ``connection_engine.ConnectionEngine.connect()`` produces and
    ``asset_universe._asset_from_market`` already reads. Three figures are taken out of it:
    ``precision.price``, ``precision.amount`` and ``limits.amount.max``.

    Raises:
        PaperMarketMetadataUnavailable: When ``entry`` is not a mapping, names no symbol, states
            a market type outside ``asset_universe.SUPPORTED_MARKET_TYPES``, or states any of the
            three figures as absent, non-numeric, non-positive or ambiguous. Every one of those
            is a refusal and none of them is a default (Requirement 28.3).
    """
    if not isinstance(entry, Mapping):
        raise PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.SYMBOL_NOT_LISTED,
            symbol=symbol,
            exchange_id=exchange_id,
            missing="market",
        )

    resolved_symbol = entry.get("symbol") or symbol
    if not resolved_symbol:
        raise PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.SYMBOL_NOT_LISTED,
            symbol=symbol,
            exchange_id=exchange_id,
            missing="symbol",
        )
    resolved_symbol = str(resolved_symbol)

    market_type = entry.get("type")
    if not market_type:
        # Some adapters express the type only through flags - the same fallback
        # ``asset_universe._asset_from_market`` applies, so one market map reads the same way
        # through both readers.
        for flag in SUPPORTED_MARKET_TYPES:
            if entry.get(flag) is True:
                market_type = flag
                break
    market_type = str(market_type).strip().lower() if market_type else ""
    if market_type not in SUPPORTED_MARKET_TYPES:
        raise PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.MARKET_TYPE_UNSUPPORTED,
            symbol=resolved_symbol,
            exchange_id=exchange_id,
            missing=f"type ({market_type or 'unstated'})",
        )

    precision = entry.get("precision") or {}
    limits = entry.get("limits") or {}
    amount_limits = limits.get("amount") if isinstance(limits, Mapping) else None
    amount_limits = amount_limits if isinstance(amount_limits, Mapping) else {}
    if not isinstance(precision, Mapping):
        precision = {}

    return MarketMetadata(
        exchange_id=str(exchange_id),
        symbol=resolved_symbol,
        market_type=market_type,
        price_precision=_decimal_places(
            precision.get("price"),
            what="precision.price",
            symbol=resolved_symbol,
            exchange_id=exchange_id,
            precision_mode=precision_mode,
        ),
        quantity_precision=_decimal_places(
            precision.get("amount"),
            what="precision.amount",
            symbol=resolved_symbol,
            exchange_id=exchange_id,
            precision_mode=precision_mode,
        ),
        max_order_quantity=_positive_decimal(
            amount_limits.get("max"),
            what="limits.amount.max",
            symbol=resolved_symbol,
            exchange_id=exchange_id,
        ),
    )


def resolve_market_metadata(
    markets: Any,
    *,
    exchange_id: Any,
    symbol: Any,
    precision_mode: Optional[str] = None,
) -> MarketMetadata:
    """Look ``symbol`` up in ``markets`` and read its metadata, or refuse.

    ``markets`` is one venue's market map, obtained through the platform's existing reader
    ``asset_universe.load_exchange_markets(exchange_id)`` - which goes through
    ``connection_engine`` and already refuses the ``DEV_MODE`` mock market map. This function
    opens no connection and consults no cache of its own.

    Raises:
        PaperMarketMetadataUnavailable: 409 ``PAPER_START_REFUSED``, when ``markets`` is not a
            non-empty mapping (``NO_MARKET_MAP``), when it lists no such symbol
            (``SYMBOL_NOT_LISTED``), or when the entry it lists is incomplete
            (``MARKET_METADATA_INCOMPLETE``). An empty map is a refusal and not "no markets":
            a venue whose metadata did not load has stated nothing, and starting a session on
            nothing is what Requirement 28.3 forbids.
    """
    if not isinstance(markets, Mapping) or not markets:
        raise PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.NO_MARKET_MAP,
            symbol=symbol,
            exchange_id=exchange_id,
            missing="markets",
        )
    key = str(symbol)
    entry = markets.get(key)
    if entry is None:
        raise PaperMarketMetadataUnavailable(
            PaperMarketMetadataUnavailable.SYMBOL_NOT_LISTED,
            symbol=symbol,
            exchange_id=exchange_id,
            missing="symbol",
        )
    return market_metadata(
        entry,
        exchange_id=exchange_id,
        symbol=key,
        precision_mode=precision_mode,
    )


# ══════════════════════════════════════════════════════════════════════════
# THE FROZEN SESSION CONFIGURATION (Requirements 16.12, 17.5, 18.1, 18.2)
# ══════════════════════════════════════════════════════════════════════════

#: ``config["schema_version"]``. Recorded so a stored config can be read back by the code that
#: understands it, rather than by whatever the reader happens to be. Same literal as
#: ``paper_market_feed.PAPER_EVENT_SCHEMA_VERSION``, and deliberately the same value: the paper
#: domain versions its stored shapes together.
SCHEMA_VERSION = "paper.v1"

#: The fifteen keys, in ``design.md``'s order. Spelled once, so the serialiser, the reader and
#: the test that holds them against the design all read one list.
CONFIG_KEYS: Tuple[str, ...] = (
    "fee_rate",
    "slippage_rate",
    "participation_rate",
    "rounding_mode",
    "cost_basis",
    "price_precision",
    "quantity_precision",
    "minor_unit_exponent",
    "max_order_quantity",
    "supported_order_types",
    "supported_sides",
    "validated_symbols",
    "market_data_source",
    "simulator",
    "schema_version",
)

#: The four keys whose value is an exact decimal **string** in the JSONB, for the reason
#: ``paper_repository._jsonb`` documents: a JSON number cannot be read back exactly.
DECIMAL_CONFIG_KEYS: Tuple[str, ...] = (
    "fee_rate",
    "slippage_rate",
    "participation_rate",
    "max_order_quantity",
)

#: ``config["supported_order_types"]`` and ``config["supported_sides"]``, from
#: ``paper_repository.ORDER_TYPES`` and ``ORDER_SIDES`` - which are ``chk_paper_order_type`` and
#: ``chk_paper_order_side`` verbatim. Imported rather than re-listed: a session that recorded a
#: supported type the column refuses would accept an order the INSERT then rejects.
SUPPORTED_ORDER_TYPES: Tuple[str, ...] = tuple(ORDER_TYPES)
SUPPORTED_SIDES: Tuple[str, ...] = tuple(ORDER_SIDES)

#: The rates a session records when its caller names none. Configured defaults, not measurements
#: - and ``design.md``'s frozen-config example is where these three figures come from. They are
#: defaults for the *rate*, which is a policy choice the platform is entitled to make; they are
#: emphatically not defaults for a precision or a maximum, which are facts about a venue and are
#: refused when absent (see :func:`market_metadata`).
DEFAULT_FEE_RATE = Decimal("0.0010")
DEFAULT_SLIPPAGE_RATE = Decimal("0.0005")
DEFAULT_PARTICIPATION_RATE = Decimal("0.10")

_ZERO = Decimal("0")
_ONE = Decimal("1")


class InvalidSessionConfig(ValueError):
    """A session configuration this module will not build or will not read back.

    A ``ValueError`` and not a :class:`PaperError`, following ``paper_accounting`` and
    ``paper_order_state``: these modules stay importable on their own and the HTTP surface is the
    service layer's decision. The refusals that ARE caller-facing here carry a catalogue code -
    :class:`PaperSimulatorMisconfigured` and :class:`PaperMarketMetadataUnavailable` - because
    each answers a specific requirement about what a caller is told.
    """


@dataclass(frozen=True)
class SessionConfig:
    """``paper_sessions.config``, as a value. Written once at start, never updated.

    Frozen for the reason the column is frozen: Requirement 16.12 captures the configuration at
    session start and keeps it for the session's lifetime, so an in-memory copy that could be
    edited would be a second, mutable version of a value the database refuses to change.

    Every field is validated at construction, so a configuration that cannot be applied fails
    where it is built rather than halfway through a fill.
    """

    fee_rate: Decimal
    slippage_rate: Decimal
    participation_rate: Decimal
    rounding_mode: str
    cost_basis: str
    price_precision: int
    quantity_precision: int
    minor_unit_exponent: int
    max_order_quantity: Decimal
    supported_order_types: Tuple[str, ...]
    supported_sides: Tuple[str, ...]
    validated_symbols: Tuple[str, ...]
    market_data_source: str
    simulator: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, lower, upper in (
            ("fee_rate", _ZERO, _ONE),
            ("slippage_rate", _ZERO, _ONE),
            ("participation_rate", _ZERO, _ONE),
        ):
            value = _exact(getattr(self, name), name)
            if not (lower <= value <= upper):
                raise InvalidSessionConfig(
                    f"{name} must be a fraction between 0 and 1 inclusive, got {value}; a rate "
                    f"outside that range is not a rate"
                )
            object.__setattr__(self, name, value)

        quantity = _exact(self.max_order_quantity, "max_order_quantity")
        if quantity <= _ZERO:
            raise InvalidSessionConfig(
                f"max_order_quantity must be greater than zero, got {quantity}"
            )
        object.__setattr__(self, "max_order_quantity", quantity)

        if self.rounding_mode not in ROUNDING_MODES:
            raise InvalidSessionConfig(
                f"unsupported rounding_mode {self.rounding_mode!r}; supported: "
                f"{sorted(ROUNDING_MODES)}. Requirement 18.2 requires ONE recorded mode applied "
                f"to every computation, so an unimplemented one is refused."
            )
        if self.cost_basis not in COST_BASES:
            raise InvalidSessionConfig(
                f"unsupported cost_basis {self.cost_basis!r}; supported: {sorted(COST_BASES)}. "
                f"Requirement 18.8 requires the recorded convention to be the applied one."
            )

        for name in ("price_precision", "quantity_precision", "minor_unit_exponent"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidSessionConfig(
                    f"{name} must be a non-negative int number of decimal places, got {value!r}"
                )
            if value > MAX_RECORDED_PRECISION:
                raise InvalidSessionConfig(
                    f"{name} of {value} exceeds the {MAX_RECORDED_PRECISION} decimal places "
                    f"NUMERIC(28,10) stores, so the recorded precision would not be the kept one"
                )
            object.__setattr__(self, name, int(value))

        object.__setattr__(
            self, "supported_order_types", _vocabulary(
                self.supported_order_types, "supported_order_types", SUPPORTED_ORDER_TYPES
            )
        )
        object.__setattr__(
            self, "supported_sides", _vocabulary(
                self.supported_sides, "supported_sides", SUPPORTED_SIDES
            )
        )

        symbols = _texts(self.validated_symbols, "validated_symbols")
        if not symbols:
            raise InvalidSessionConfig(
                "validated_symbols must name at least one symbol; a session whose validated "
                "symbol set is empty can accept no order at all (Requirement 16.5)"
            )
        object.__setattr__(self, "validated_symbols", symbols)

        for name in ("market_data_source", "simulator", "schema_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidSessionConfig(f"{name} must be non-empty text, got {value!r}")
            object.__setattr__(self, name, value.strip())

        if self.schema_version != SCHEMA_VERSION:
            raise InvalidSessionConfig(
                f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if self.simulator in _forbidden_simulator_names():
            # The same refusal ``assert_paper_simulator`` makes, restated at the one other place
            # a forbidden simulator could enter a session: a config assembled from a stored
            # payload rather than from a resolved class.
            module, _, qualname = self.simulator.rpartition(".")
            raise PaperSimulatorMisconfigured(module, qualname)

    # -- serialisation ----------------------------------------------------

    def to_jsonb(self) -> Dict[str, Any]:
        """The exact payload ``paper_sessions.config`` stores, in :data:`CONFIG_KEYS` order.

        The four rate and quantity values are decimal **strings**; the three precisions and the
        minor-unit exponent are ``int``; the three vocabularies are lists. Nothing in the result
        is a ``float``, which is what makes the round trip exact (Requirement 18.1) and what
        ``paper_repository.session_config_payload`` then re-checks before the write.
        """
        return {
            "fee_rate": str(self.fee_rate),
            "slippage_rate": str(self.slippage_rate),
            "participation_rate": str(self.participation_rate),
            "rounding_mode": self.rounding_mode,
            "cost_basis": self.cost_basis,
            "price_precision": self.price_precision,
            "quantity_precision": self.quantity_precision,
            "minor_unit_exponent": self.minor_unit_exponent,
            "max_order_quantity": str(self.max_order_quantity),
            "supported_order_types": list(self.supported_order_types),
            "supported_sides": list(self.supported_sides),
            "validated_symbols": list(self.validated_symbols),
            "market_data_source": self.market_data_source,
            "simulator": self.simulator,
            "schema_version": self.schema_version,
        }

    def accounting(self, *, market_type: Optional[str] = None) -> AccountingConfig:
        """The :class:`~paper_accounting.AccountingConfig` this configuration implies.

        Built from this object rather than from the stored JSONB so the simulator and the
        accounting engine cannot disagree about a rate. ``market_type`` is accepted because
        ``AccountingConfig`` reads it for Requirement 18.12's margin decision and
        :data:`CONFIG_KEYS` does not record it (see the module docstring's closing note); left
        ``None``, no margin figure is reported, which is the correct answer for a ``spot``
        session and a stated gap for a margined one.
        """
        return AccountingConfig(
            fee_rate=self.fee_rate,
            slippage_rate=self.slippage_rate,
            rounding_mode=self.rounding_mode,
            cost_basis=self.cost_basis,
            price_precision=self.price_precision,
            quantity_precision=self.quantity_precision,
            minor_unit_exponent=self.minor_unit_exponent,
            market_type=market_type,
        )


def _forbidden_simulator_names() -> FrozenSet[str]:
    """:data:`FORBIDDEN_SIMULATORS` as dotted names, for comparison against a stored string."""
    return frozenset(f"{module}.{qualname}" for module, qualname in FORBIDDEN_SIMULATORS)


def _exact(value: Any, what: str) -> Decimal:
    """``paper_accounting.to_decimal``, with this module's exception type.

    ``to_decimal`` is the one place a ``float`` is refused (Requirement 18.1) and it is reused
    rather than reimplemented; only the exception is translated, so a bad config reads as a bad
    config rather than as an accounting failure.
    """
    try:
        return to_decimal(value, what)
    except Exception as exc:  # noqa: BLE001 - re-raised immediately as InvalidSessionConfig
        raise InvalidSessionConfig(str(exc)) from exc


def _texts(values: Any, what: str) -> Tuple[str, ...]:
    """A sequence of non-empty strings, order preserved, duplicates refused."""
    if isinstance(values, (str, bytes)) or not isinstance(values, _AbcIterable):
        raise InvalidSessionConfig(
            f"{what} must be a sequence of strings, got {type(values).__name__}"
        )
    result: List[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise InvalidSessionConfig(f"{what} must contain non-empty strings, got {item!r}")
        text = item.strip()
        if text in result:
            raise InvalidSessionConfig(f"{what} lists {text!r} twice")
        result.append(text)
    return tuple(result)


def _vocabulary(values: Any, what: str, permitted: Tuple[str, ...]) -> Tuple[str, ...]:
    """:func:`_texts`, restricted to ``permitted`` and required to be non-empty."""
    texts = _texts(values, what)
    if not texts:
        raise InvalidSessionConfig(f"{what} must name at least one value")
    unknown = [text for text in texts if text not in permitted]
    if unknown:
        raise InvalidSessionConfig(
            f"{what} names {unknown!r}, which the Persistence_Layer's CHECK constraint refuses; "
            f"permitted: {list(permitted)}"
        )
    return texts


def freeze_session_config(
    *,
    metadata: MarketMetadata,
    currency: Any,
    market_data_source: Any,
    fee_rate: Any = DEFAULT_FEE_RATE,
    slippage_rate: Any = DEFAULT_SLIPPAGE_RATE,
    participation_rate: Any = DEFAULT_PARTICIPATION_RATE,
    rounding_mode: str = DEFAULT_ROUNDING_MODE,
    cost_basis: str = WEIGHTED_AVERAGE,
    validated_symbols: Optional[Sequence[str]] = None,
    simulator: Any = None,
) -> SessionConfig:
    """Build the one configuration a Paper_Session runs on, at start (Requirement 16.12).

    Args:
        metadata: The session symbol's exchange market metadata, from
            :func:`resolve_market_metadata`. This is what makes ``price_precision``,
            ``quantity_precision`` and ``max_order_quantity`` recorded values rather than
            guesses, and it is a **required** argument for exactly that reason: there is no
            call shape in which they are defaulted.
        currency: The Paper_Account's currency. ``minor_unit_exponent`` is read from
            ``marketplace.money.minor_unit_exponent`` - the platform's one persisted exponent
            table - rather than assumed to be 2.
        market_data_source: The identity ``choose_market_data_source`` selected, recorded per
            Requirement 14.3. Passed in because ``paper_market_feed`` owns the selection.
        simulator: The resolved simulator, checked by :func:`assert_paper_simulator` before its
            identity is recorded, so a forbidden simulator cannot reach a frozen config. ``None``
            records :data:`SIMULATOR_NAME`, this package's own simulator.
        validated_symbols: The session's validated symbol set. Defaults to exactly
            ``(metadata.symbol,)`` - the one symbol whose metadata was actually read. A symbol
            in this set whose precision was never read would make Requirement 16.5's check
            compare against another market's figures, so every member is required to be a
            symbol the caller has validated; passing a set that omits ``metadata.symbol`` is
            refused.

    Returns:
        A frozen :class:`SessionConfig`. Serialise it with :meth:`SessionConfig.to_jsonb` and
        write it in the session INSERT through ``paper_repository.session_config_payload``.

    Raises:
        InvalidSessionConfig: For a rate outside ``[0, 1]``, an unsupported rounding mode or cost
            basis, an unsupported currency, or a ``validated_symbols`` set that does not contain
            the symbol whose metadata was read.
        PaperSimulatorMisconfigured: 500, when ``simulator`` resolves to
            :data:`FORBIDDEN_SIMULATORS` (Requirement 13.11).
    """
    if not isinstance(metadata, MarketMetadata):
        raise InvalidSessionConfig(
            "metadata must be a MarketMetadata read from the exchange market metadata; a "
            "session's precisions are never defaulted (Requirements 16.5, 28.3)"
        )

    try:
        exponent = minor_unit_exponent(currency)
    except UnsupportedCurrency as exc:
        raise InvalidSessionConfig(
            f"{exc}. The minor-unit exponent is read from the platform's persisted table, not "
            f"assumed to be 2, because every quantized money value in the session depends on it "
            f"(Requirement 18.2)."
        ) from exc

    if simulator is None:
        recorded_simulator = SIMULATOR_NAME
    else:
        module, qualname = assert_paper_simulator(simulator)
        recorded_simulator = f"{module}.{qualname}"

    if validated_symbols is None:
        symbols: Tuple[str, ...] = (metadata.symbol,)
    else:
        symbols = _texts(validated_symbols, "validated_symbols")
        if metadata.symbol not in symbols:
            raise InvalidSessionConfig(
                f"validated_symbols {list(symbols)} does not contain {metadata.symbol!r}, the "
                f"symbol whose market metadata these precisions were read from; Requirement "
                f"16.5's checks would then compare against another market's figures"
            )

    return SessionConfig(
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        participation_rate=participation_rate,
        rounding_mode=rounding_mode,
        cost_basis=cost_basis,
        price_precision=metadata.price_precision,
        quantity_precision=metadata.quantity_precision,
        minor_unit_exponent=exponent,
        max_order_quantity=metadata.max_order_quantity,
        supported_order_types=SUPPORTED_ORDER_TYPES,
        supported_sides=SUPPORTED_SIDES,
        validated_symbols=symbols,
        market_data_source=market_data_source,
        simulator=recorded_simulator,
        schema_version=SCHEMA_VERSION,
    )


def session_config_from_jsonb(payload: Any) -> SessionConfig:
    """Read a stored ``paper_sessions.config`` back as a :class:`SessionConfig`.

    The inverse of :meth:`SessionConfig.to_jsonb`, and exact: every decimal was stored as a
    string, so ``Decimal(text)`` recovers the value that was written.

    Nothing is defaulted. A payload missing a key is refused rather than completed, because a
    session whose recorded configuration is incomplete must not trade on a filled-in one - the
    same disposition ``AccountingConfig.from_session_config`` already takes.

    Raises:
        InvalidSessionConfig: For a non-mapping payload, an absent key, or a value the
            constructor refuses.
        PaperSimulatorMisconfigured: 500, when the stored ``simulator`` names
            :data:`FORBIDDEN_SIMULATORS`.
    """
    if not isinstance(payload, Mapping):
        raise InvalidSessionConfig(
            f"paper_sessions.config must be a mapping, got {type(payload).__name__}"
        )
    missing = [key for key in CONFIG_KEYS if payload.get(key) is None]
    if missing:
        raise InvalidSessionConfig(
            f"paper_sessions.config is missing {missing}; a session's recorded configuration is "
            f"read as it was written or not at all (Requirements 16.12, 28.3)"
        )
    return SessionConfig(
        fee_rate=payload["fee_rate"],
        slippage_rate=payload["slippage_rate"],
        participation_rate=payload["participation_rate"],
        rounding_mode=str(payload["rounding_mode"]),
        cost_basis=str(payload["cost_basis"]),
        price_precision=payload["price_precision"],
        quantity_precision=payload["quantity_precision"],
        minor_unit_exponent=payload["minor_unit_exponent"],
        max_order_quantity=payload["max_order_quantity"],
        supported_order_types=tuple(payload["supported_order_types"]),
        supported_sides=tuple(payload["supported_sides"]),
        validated_symbols=tuple(payload["validated_symbols"]),
        market_data_source=str(payload["market_data_source"]),
        simulator=str(payload["simulator"]),
        schema_version=str(payload["schema_version"]),
    )


__all__ = [
    "AUDIT_ACTION_SIMULATOR_MISCONFIGURED",
    "CONFIG_KEYS",
    "DECIMAL_CONFIG_KEYS",
    "DEFAULT_FEE_RATE",
    "DEFAULT_PARTICIPATION_RATE",
    "DEFAULT_SLIPPAGE_RATE",
    "FORBIDDEN_SIMULATORS",
    "FORBIDDEN_SIMULATOR_EVIDENCE",
    "InvalidSessionConfig",
    "MAX_RECORDED_PRECISION",
    "MarketMetadata",
    "PRECISION_MODES",
    "PRECISION_MODE_DECIMAL_PLACES",
    "PRECISION_MODE_TICK_SIZE",
    "PaperMarketMetadataUnavailable",
    "PaperSimulatorMisconfigured",
    "SCHEMA_VERSION",
    "SIMULATOR_MODULE",
    "SIMULATOR_NAME",
    "SIMULATOR_QUALNAME",
    "SUPPORTED_ORDER_TYPES",
    "SUPPORTED_SIDES",
    "SessionConfig",
    "assert_paper_simulator",
    "audit_simulator_misconfigured",
    "freeze_session_config",
    "market_metadata",
    "resolve_market_metadata",
    "session_config_from_jsonb",
    "simulator_identity",
]

# ══════════════════════════════════════════════════════════════════════════
# TASKS 25.3 - 25.6: THE ORDER LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════
#
# WHAT "ONE TRANSACTION" MEANS OVER THIS TRANSPORT - READ THIS FIRST
# -----------------------------------------------------------------
# ``design.md`` and tasks 25.3/25.4 are written against a SQL transaction with
# ``SELECT ... FOR UPDATE``. **This deployment has neither.** The Persistence_Layer is reached
# through PostgREST, which speaks one HTTP statement per request:
#
# * There is no ``FOR UPDATE``. A row lock taken by a read could not survive until the
#   caller's UPDATE, because the read and the UPDATE are two requests.
# * There is no ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``. The eight statements a fill writes are
#   eight requests and nothing can put them in one unit.
#
# ``paper_repository.lock_account_for_update`` records that already, and this module reproduces
# the *outcome* the transaction was there for rather than claiming the transaction:
#
# ============================  =================================================================
# ``FOR UPDATE`` on the account version-guarded optimistic UPDATE. ``lock_account_for_update``
#                               reads ``version = N``; every write goes through
#                               ``bump_version(expected_version=N)``, which carries
#                               ``.eq("version", N)`` and matches zero rows if another writer
#                               moved it. A lost update surfaces as a conflict.
# ``FOR UPDATE`` on the order   the state machine is the guard. ``update_order(expected_state=S)``
#                               carries ``.eq("order_state", S)``, so a concurrent writer that
#                               already moved the order makes the UPDATE match zero rows.
# ``ROLLBACK`` on a violation   the ``ASSERT accounting.invariants_hold`` runs on the COMPUTED
#                               state **before the first statement is issued**, so an invariant
#                               breach writes nothing at all rather than needing a rollback that
#                               does not exist. This is strictly stronger than a late assert and
#                               strictly weaker than a transaction: see the next paragraph.
# ``ROLLBACK`` on a late error  **not available.** Once ``bump_version`` has moved the money, a
#                               failure in a later statement leaves a partial write. This module
#                               does not pretend otherwise: it refuses to retry past that point
#                               and reports ``details["partial_write"] = True`` with the phase
#                               that failed, so the gap is visible in the response and in the log
#                               rather than being silently retried into a double movement.
# ============================  =================================================================
#
# **The residual gap, stated plainly.** Requirement 24.6's single transaction is not achievable
# over this transport. What is achievable, and what is implemented, is: (a) nothing is written
# until every arithmetic check has passed; (b) every write that can be guarded is guarded, so a
# race is detected rather than silently applied; (c) the one write ordering that admits a safe
# resume is used, and the resume is implemented (see :func:`apply_fill`'s "RESUME" note); and
# (d) any failure past the money movement is reported as a partial write and never retried.
# Closing the gap completely requires moving ``submit_intent`` and ``apply_fill`` into a database
# function (``rpc``) so that PostgreSQL holds the transaction. That is a schema and deployment
# change outside these four tasks, and it is recorded here rather than glossed over.
#
# WHERE THE MONEY ARITHMETIC IS
# -----------------------------
# ``paper_accounting``. Not one balance, fee, position size or equity figure is computed in this
# section; every one of them comes back from ``paper_accounting.apply_fill``,
# ``paper_accounting.lock`` or ``paper_accounting.required_funds``. The *prices* are this
# module's - that is the fill model of task 25.5 - and they are derived from a validated market
# event and the frozen config, never drawn, interpolated or extrapolated (Requirement 14.9).
#
# WHERE THE STATEMENTS ARE
# ------------------------
# ``paper_repository``. This section issues none of its own: every read and every write goes
# through a repository function, so ``user_id`` is a predicate on all of them and the projections
# stay the ones the schema-contract test checks (Requirements 21.2, 21.5, 24.9).


_T = TypeVar("_T")

#: Requirement 16.10: "SHALL retry the application at most 3 times". Read as three attempts in
#: total, which is how ``design.md``'s ``FOR attempt IN 1..3`` spells it.
RETRY_ATTEMPTS = 3

#: The delay before each retry, in seconds, in order. Two entries because three attempts have two
#: waits; the last entry is the cap.
#:
#: **Jitter-free, for the reason ``paper_market_feed.BACKOFF_SECONDS`` documents.** Requirement
#: 15.4 requires a session replayed against the same recorded events to produce the same order
#: states and fills. A randomised delay would move the retry points between the run and the
#: replay, and it would be a ``random`` draw inside ``backend_app/backend/paper/``, which
#: Requirement 18.1 and ``tests/test_paper_no_random.py`` forbid outright. Bounded instead: three
#: attempts, at most 0.15 s of waiting in total, so a contended account fails fast rather than
#: holding a request open.
RETRY_BACKOFF_SECONDS: Tuple[Decimal, ...] = (Decimal("0.05"), Decimal("0.10"))


def retry_backoff_seconds(attempt: int) -> Decimal:
    """The delay before the attempt after ``attempt``. 1-based; capped at the last entry.

    ``retry_backoff_seconds(1)`` is the wait between attempts 1 and 2. Exact and deterministic:
    two runs of the same session wait the same sequence.
    """
    index = int(attempt)
    if index < 1:
        raise ValueError(f"attempt is 1-based; got {attempt!r}")
    return RETRY_BACKOFF_SECONDS[min(index, len(RETRY_BACKOFF_SECONDS)) - 1]


class PaperConcurrencyExhausted(PaperError):
    """409 ``PAPER_CONCURRENCY_CONFLICT`` - the bounded retries were used up.

    Requirement 16.10's final clause: after at most three attempts the caller is told that the
    application conflicted rather than being told it succeeded. The status is **pinned** -
    ``PAPER_CONCURRENCY_CONFLICT`` is absent from ``ALLOWED_HTTP_STATUS_FOR_CODE``, so
    ``StructuredError.__init__`` refuses any status other than the catalogue's 409.

    ``details`` carries ``operation`` (which of the two write paths gave up), ``attempts``, and -
    when it applies - ``phase`` and ``partial_write``. ``partial_write`` is the honest half: over
    PostgREST there is no ``ROLLBACK``, so a conflict *after* the account balance moved leaves
    rows written that a transaction would have undone. Reporting it is what makes it findable;
    retrying it would double-apply the money.
    """

    def __init__(
        self,
        operation: str,
        attempts: int,
        *,
        phase: Optional[str] = None,
        partial_write: bool = False,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.operation = str(operation)
        self.attempts = int(attempts)
        self.phase = None if phase is None else str(phase)
        self.partial_write = bool(partial_write)
        merged: Dict[str, Any] = {
            "operation": self.operation,
            "attempts": self.attempts,
        }
        if self.phase is not None:
            merged["phase"] = self.phase
        if self.partial_write:
            merged["partial_write"] = True
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_CONCURRENCY_CONFLICT, details=merged)


class PaperIdempotencyConflict(PaperError):
    """409 ``PAPER_IDEMPOTENCY_CONFLICT`` - the key was reused with different parameters.

    Requirement 16.15: no order is created, the previously created order is left unchanged, and
    the error says the reuse conflicted. ``details`` carries the key's own ``fingerprint`` and the
    ``recorded_fingerprint`` it did not match - both are SHA-256 digests of the caller's own order
    parameters, so neither discloses anything the caller does not already hold - plus the
    ``order_id`` of the order that stands.
    """

    def __init__(
        self,
        idempotency_key: Any,
        *,
        fingerprint: Any,
        recorded_fingerprint: Any,
        order_id: Any = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged: Dict[str, Any] = {
            "fingerprint": str(fingerprint),
            "recorded_fingerprint": str(recorded_fingerprint),
        }
        if order_id is not None:
            merged["order_id"] = str(order_id)
        if details:
            merged.update(dict(details))
        self.idempotency_key = str(idempotency_key)
        super().__init__(PAPER_IDEMPOTENCY_CONFLICT, details=merged)


class PaperOverFill(PaperError):
    """409 ``PAPER_OVER_FILL`` - the fill would exceed the order's quantity.

    Requirement 16.7: the fill event is rejected and the cumulative filled quantity, the order's
    state, the position, the balance and the realized PnL are all left unchanged. Raised **before
    the first statement**, so "unchanged" is a fact about what was issued rather than about what
    was undone.

    ``details`` carries the three quantities that make the arithmetic checkable - ``ordered``,
    ``filled`` and ``requested`` - as exact decimal strings.
    """

    def __init__(
        self,
        order_id: Any,
        *,
        ordered: Any,
        filled: Any,
        requested: Any,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged: Dict[str, Any] = {
            "order_id": str(order_id),
            "ordered": str(ordered),
            "filled": str(filled),
            "requested": str(requested),
        }
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_OVER_FILL, details=merged)


class PaperInvariantViolation(PaperError):
    """500 ``PAPER_INVARIANT_VIOLATION`` - an accounting invariant did not hold.

    Requirement 18.14: the operation is rejected, the stored balances, positions, realized PnL and
    equity series are unchanged, and the error **names** the violated invariant in
    ``details["invariant"]``. The name comes from ``paper_accounting.InvariantViolation``, which is
    the only producer of one, so the reported name and the failed check cannot disagree.

    "Unchanged" holds here by construction rather than by rollback: the assert runs on the computed
    state before any statement is issued (see the section header).
    """

    def __init__(
        self,
        invariant: Any,
        *,
        phase: Optional[str] = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.invariant = str(invariant)
        merged: Dict[str, Any] = {"invariant": self.invariant}
        if phase is not None:
            merged["phase"] = str(phase)
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_INVARIANT_VIOLATION, details=merged)


class PaperOrderInvalid(PaperError):
    """400 ``PAPER_ORDER_INVALID`` - the request could not be turned into an order at all.

    Distinct from the eight *persisted* rejections of Requirement 16.5, and deliberately so.
    Requirement 16.5's rejections are order **outcomes**: an order row exists, it is ``REJECTED``,
    and it names the failed check. This is a **request** refusal, for the two things that cannot
    become an order row: an idempotency key outside 1-128 characters (which the column's
    ``chk_paper_order_idem_len`` would refuse, and task 25.3 requires to be refused here so the
    constraint stays a backstop) and a numeric field that is not an exact decimal
    (Requirement 18.1 - a ``float`` quantity has already lost exactness upstream).

    ``details["validation"]`` names which. :func:`order_invalid` builds the same error for a
    persisted Requirement 16.5 rejection, for a caller that wants to *raise* rather than return
    the rejected order.
    """

    def __init__(
        self,
        validation: Any,
        *,
        order_id: Any = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.validation = str(validation)
        merged: Dict[str, Any] = {"validation": self.validation}
        if order_id is not None:
            merged["order_id"] = str(order_id)
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_ORDER_INVALID, details=merged)


class PaperInsufficientFunds(PaperError):
    """400 ``PAPER_INSUFFICIENT_FUNDS`` - required funds exceed the available balance.

    Requirement 16.6's caller-facing form, for a caller that wants to raise rather than return the
    persisted ``REJECTED`` order. Nothing was locked, which is a fact about
    :func:`submit_intent`'s ordering: the funds check runs before the lock, not after it.
    """

    def __init__(
        self,
        *,
        required: Any,
        available: Any,
        order_id: Any = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        merged: Dict[str, Any] = {"required": str(required), "available": str(available)}
        if order_id is not None:
            merged["order_id"] = str(order_id)
        if details:
            merged.update(dict(details))
        super().__init__(PAPER_INSUFFICIENT_FUNDS, details=merged)


# ══════════════════════════════════════════════════════════════════════════
# INSTRUMENTATION (Requirements 26.6, 27.6)
# ══════════════════════════════════════════════════════════════════════════


#: The five ``paper.order.*`` / ``paper.fill.*`` recorders below all reach the collector through
#: :func:`backend_app.backend.metrics.guarded_collector`, which yields ``None`` when it is
#: unavailable. Each of them then records nothing and returns: instrumentation must never be what
#: loses a fill, so the order is still submitted and the fill still applied.


def _record_order_submitted(duration_ms: float) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_order_submitted(duration_ms)


def _record_fill_applied(duration_ms: float) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_fill_applied(duration_ms)


def _record_order_rejected(reason: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_order_rejected(reason)


def _record_order_retry(operation: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_order_retry(operation)


def _record_order_concurrency_conflict(operation: str) -> None:
    collector = guarded_collector()
    if collector is not None:
        collector.record_paper_order_concurrency_conflict(operation)


def _is_retryable(exc: BaseException) -> bool:
    """Whether ``exc`` is one of the two conditions Requirement 16.10 retries.

    Exactly two: a version-guarded UPDATE that matched no row
    (:class:`paper_repository.PaperConcurrencyConflict` - the stale-version case this transport
    actually produces), and PostgreSQL's transaction-rollback class 40
    (``paper_repository.is_serialization_failure`` - the ``SerializationFailure`` case a real
    ``SERIALIZABLE`` transaction would produce). Everything else is re-raised: a retry is only
    safe where the statement definitively did not apply.
    """
    if isinstance(exc, repo.PaperConcurrencyConflict):
        return True
    if isinstance(exc, repo.PaperPersistenceError):
        return repo.is_serialization_failure(exc.__cause__ or exc)
    return False


async def with_retries(
    operation: str,
    attempt: Callable[[int], Awaitable[_T]],
    *,
    attempts: int = RETRY_ATTEMPTS,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> _T:
    """Run ``attempt`` up to ``attempts`` times, then raise :class:`PaperConcurrencyExhausted`.

    Task 25.6, written **once** and shared by :func:`submit_intent` and :func:`apply_fill` - which
    is the whole point of the task: two copies of a retry policy are two policies, and the one
    that gets edited is never the one that is running.

    ``attempt`` is called with the 1-based attempt number, so a caller that wants to log or record
    which try it is on can. It must be a coroutine function taking that number, and it must be
    safe to call again after a retryable failure - which is what the write orderings in
    :func:`submit_intent` and :func:`apply_fill` are arranged to make true.

    Args:
        operation: What is being retried, for ``details["operation"]`` and the log line.
        attempt: The coroutine to run.
        attempts: How many times. Requirement 16.10's three.
        sleep: The awaitable delay, for tests. Defaults to :func:`asyncio.sleep` - **never**
            ``time.sleep``, which would block the event loop and stall every other request on
            this worker for the duration.
        details: Extra ``details`` members for the final error - the session and order the caller
            was working on, so a 409 is diagnosable.

    Raises:
        PaperConcurrencyExhausted: 409, after ``attempts`` retryable failures.
        Anything else ``attempt`` raises, unchanged and on the first occurrence.
    """
    limit = int(attempts)
    if limit < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts!r}")
    waiter = sleep if sleep is not None else asyncio.sleep
    last: Optional[BaseException] = None

    for number in range(1, limit + 1):
        try:
            return await attempt(number)
        except BaseException as exc:  # noqa: BLE001 - re-raised unless it is one of the two
            if not _is_retryable(exc):
                raise
            last = exc
            # `paper.order.retries` (Requirement 26.6). Counted here, at the ONE shared retry
            # policy, so the figure covers every write path that uses it and there is no second
            # counting point to keep in step - the same argument this function's docstring makes
            # about the policy itself.
            _record_order_retry(operation)
            logger.warning(
                "[paper-simulator] %s attempt %d of %d conflicted (%s); %s",
                operation,
                number,
                limit,
                exc,
                "retrying" if number < limit else "giving up",
            )
        if number < limit:
            # ``float`` for ``asyncio.sleep``, which is the one place a float appears on this
            # path and is not a money or price computation - the same exception
            # ``paper_market_feed.FeedHandle.reconnect`` makes, for the same reason.
            await waiter(float(retry_backoff_seconds(number)))

    # `paper.order.concurrency_conflicts`: the retries were used up and the caller is told the
    # application conflicted. Distinct from `paper.order.retries` above, which counts each
    # conflicting attempt - one give-up costs `limit` retries, and an operator needs both figures
    # to tell a contended account from a broken one.
    _record_order_concurrency_conflict(operation)
    raise PaperConcurrencyExhausted(operation, limit, details=details) from last


# ══════════════════════════════════════════════════════════════════════════
# THE ORDER INTENT, ITS FINGERPRINT AND ITS STATIC VALIDATION (Requirement 16.5)
# ══════════════════════════════════════════════════════════════════════════

#: ``paper_orders.idempotency_key`` length bound. ``paper_repository``'s own constant, imported
#: rather than restated, so this module and ``chk_paper_order_idem_len`` cannot disagree about
#: 128.
IDEMPOTENCY_KEY_MAX_CHARS = repo.IDEMPOTENCY_KEY_MAX_CHARS

#: The default time-in-force. ``paper_orders`` has **no** ``time_in_force`` column - 009 does not
#: declare one - so this value is not persisted; it exists because ``design.md``'s fingerprint
#: includes it, and a resting limit order's time-in-force is part of "identical order parameters"
#: even where the column is absent. The consequence is stated rather than hidden: two intents that
#: differ only in time-in-force produce different fingerprints and therefore an idempotency
#: conflict, but the accepted order does not record which one it was. Recording it needs a
#: migration 015 column and is not one of these four tasks.
DEFAULT_TIME_IN_FORCE = "GTC"

#: The fingerprint's fields, in ``design.md``'s order:
#: ``sha256(symbol|side|order_type|quantity|limit_price|time_in_force)``.
FINGERPRINT_FIELDS: Tuple[str, ...] = (
    "symbol",
    "side",
    "order_type",
    "quantity",
    "limit_price",
    "time_in_force",
)

#: The eight ``rejection_reason`` values Requirement 16.5 enumerates, in the order
#: ``design.md``'s ``first_of(...)`` evaluates them. Order is contractual: an intent that fails
#: two checks reports the **first** one, so the reason a caller reads is reproducible rather than
#: dependent on how the conditions happen to be arranged.
REJECTION_QUANTITY_NOT_POSITIVE = "QUANTITY_NOT_POSITIVE"
REJECTION_QUANTITY_ABOVE_MAX = "QUANTITY_ABOVE_MAX"
REJECTION_QUANTITY_PRECISION = "QUANTITY_PRECISION"
REJECTION_SYMBOL_NOT_VALIDATED = "SYMBOL_NOT_VALIDATED"
REJECTION_ORDER_TYPE_UNSUPPORTED = "ORDER_TYPE_UNSUPPORTED"
REJECTION_SIDE_UNSUPPORTED = "SIDE_UNSUPPORTED"
REJECTION_LIMIT_PRICE_NOT_POSITIVE = "LIMIT_PRICE_NOT_POSITIVE"
REJECTION_LIMIT_PRICE_PRECISION = "LIMIT_PRICE_PRECISION"

STATIC_REJECTION_REASONS: Tuple[str, ...] = (
    REJECTION_QUANTITY_NOT_POSITIVE,
    REJECTION_QUANTITY_ABOVE_MAX,
    REJECTION_QUANTITY_PRECISION,
    REJECTION_SYMBOL_NOT_VALIDATED,
    REJECTION_ORDER_TYPE_UNSUPPORTED,
    REJECTION_SIDE_UNSUPPORTED,
    REJECTION_LIMIT_PRICE_NOT_POSITIVE,
    REJECTION_LIMIT_PRICE_PRECISION,
)

#: The two rejections that are **not** static: they depend on state outside the intent.
#: ``NO_VALIDATED_PRICE`` is Requirement 14.9 - no validated Paper_Market_Data price exists for
#: the symbol, and one is never synthesised. ``INSUFFICIENT_FUNDS`` is Requirement 16.6.
REJECTION_NO_VALIDATED_PRICE = "NO_VALIDATED_PRICE"
REJECTION_INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"

#: Every reason ``paper_orders.rejection_reason`` may carry from this module. 009 declares no
#: CHECK constraint on the column, so this tuple - not the database - is the enumeration, which is
#: why it is spelled once and asserted against by the tests.
REJECTION_REASONS: Tuple[str, ...] = STATIC_REJECTION_REASONS + (
    REJECTION_NO_VALIDATED_PRICE,
    REJECTION_INSUFFICIENT_FUNDS,
)


class InvalidOrderIntent(ValueError):
    """An order intent that cannot be read as one at all.

    A ``ValueError`` rather than a :class:`PaperError`, following :class:`InvalidSessionConfig`:
    the value objects in this module stay usable without an HTTP surface.
    :func:`submit_intent` translates it into :class:`PaperOrderInvalid` (400) at the boundary, so
    a caller still gets a catalogue code.

    This is emphatically **not** where Requirement 16.5's rejections live. A zero quantity, a
    quantity above the maximum, an unsupported side - all of those are admissible *intents* that
    produce a persisted ``REJECTED`` order, so :class:`OrderIntent` accepts them without comment.
    What is refused here is a value that is not an exact decimal at all (Requirement 18.1) or a
    field that is absent.
    """


@dataclass(frozen=True)
class OrderIntent:
    """One order intent, as a value. Coerced, not validated.

    Frozen, so the intent a rejection was computed from is the intent that was fingerprinted.

    Every numeric field is an exact :class:`~decimal.Decimal` and a ``float`` is refused rather
    than coerced (``paper_accounting.to_decimal``, Requirement 18.1). Nothing else is checked
    here: Requirement 16.5's eight checks compare the intent against the **session's** frozen
    configuration, which this object does not carry, and they produce a persisted ``REJECTED``
    order rather than an exception - so they live in :func:`static_rejection_reason`.

    Attributes:
        symbol: The market symbol, as the session's ``validated_symbols`` spells it.
        side: ``'buy'`` or ``'sell'`` - lower-cased here because ``chk_paper_order_side`` spells
            them lower-case, and a caller sending ``'BUY'`` means the same order.
        order_type: ``'market'`` or ``'limit'``, lower-cased for the same reason.
        quantity: The ordered quantity. May be zero or negative - that is a rejection, not a
            malformed intent.
        limit_price: The limit price, or ``None`` for a market order.
        time_in_force: Part of the fingerprint; see :data:`DEFAULT_TIME_IN_FORCE`.
        idempotency_key: 1 to 128 characters, or ``None``. Validated by
            :func:`validate_idempotency_key` at :func:`submit_intent`'s boundary, before it
            reaches the database.
        signal_id: The Signal Trace identifier this intent came from, when it came from one.
    """

    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Optional[Decimal] = None
    time_in_force: str = DEFAULT_TIME_IN_FORCE
    idempotency_key: Optional[str] = None
    signal_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("symbol", "side", "order_type", "time_in_force"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise InvalidOrderIntent(f"intent.{name} must be non-empty text, got {value!r}")
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "side", self.side.strip().lower())
        object.__setattr__(self, "order_type", self.order_type.strip().lower())
        object.__setattr__(self, "time_in_force", self.time_in_force.strip().upper())

        try:
            object.__setattr__(self, "quantity", to_decimal(self.quantity, "intent.quantity"))
            if self.limit_price is not None:
                object.__setattr__(
                    self, "limit_price", to_decimal(self.limit_price, "intent.limit_price")
                )
        except Exception as exc:  # noqa: BLE001 - re-raised immediately as InvalidOrderIntent
            raise InvalidOrderIntent(str(exc)) from exc

        key = self.idempotency_key
        object.__setattr__(
            self, "idempotency_key", None if key is None else str(key).strip() or None
        )
        signal = self.signal_id
        object.__setattr__(self, "signal_id", None if signal is None else str(signal).strip() or None)

    @classmethod
    def from_mapping(cls, payload: Any) -> "OrderIntent":
        """Read an intent out of the mapping a handler or a signal converter produced.

        ``order_type`` defaults to ``'market'``, matching ``paper_replay.ReferenceLedger.submit``
        and the existing ``/api/paper/order`` body. Everything else is required.

        Raises:
            InvalidOrderIntent: For a non-mapping payload, an absent required key, or a value the
                constructor refuses.
        """
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise InvalidOrderIntent(
                f"an order intent must be a mapping or an OrderIntent, got "
                f"{type(payload).__name__}"
            )
        for key in ("symbol", "side", "quantity"):
            if payload.get(key) is None:
                raise InvalidOrderIntent(f"the order intent carries no {key!r}")
        return cls(
            symbol=payload["symbol"],
            side=payload["side"],
            order_type=payload.get("order_type") or "market",
            quantity=payload["quantity"],
            limit_price=payload.get("limit_price"),
            time_in_force=payload.get("time_in_force") or DEFAULT_TIME_IN_FORCE,
            idempotency_key=payload.get("idempotency_key"),
            signal_id=payload.get("signal_id"),
        )


def validate_idempotency_key(value: Any) -> Optional[str]:
    """``value`` as a 1-to-128-character key, or ``None`` when absent. Refuses before the database.

    Task 25.3: "the idempotency key is validated as 1-128 characters **before** it reaches the
    database, so ``chk_paper_order_idem_len`` is a backstop rather than the error surface". So an
    over-long key is a :class:`PaperOrderInvalid` (400) naming the length rule, raised before any
    statement, rather than a ``23514`` surfacing from an INSERT as an unexplained failure.

    A ``None`` or blank key is ``None`` - absent, which the nullable column permits and which
    means "this request is not idempotent". Blank is treated as absent rather than refused for the
    reason ``paper_repository._validate_idempotency_key`` treats it so: a client sending an empty
    header field meant to send nothing.

    Raises:
        PaperOrderInvalid: 400, ``details["validation"] = "IDEMPOTENCY_KEY_LENGTH"``.
    """
    if value is None:
        return None
    key = str(value).strip()
    if not key:
        return None
    if len(key) > IDEMPOTENCY_KEY_MAX_CHARS:
        raise PaperOrderInvalid(
            "IDEMPOTENCY_KEY_LENGTH",
            details={
                "max_characters": IDEMPOTENCY_KEY_MAX_CHARS,
                "characters": len(key),
            },
        )
    return key


def order_fingerprint(intent: Any) -> str:
    """``sha256(symbol|side|order_type|quantity|limit_price|time_in_force)``, hex.

    ``design.md``'s fingerprint, which is what turns Requirement 16.8's "identical order
    parameters" from a judgement into a computed fact - and what Requirement 16.15's conflict is
    detected by.

    THE TWO DECIMALS ARE CANONICALISED
    ----------------------------------
    Through ``paper_market_feed.canonical_number``, which renders ``format(value.normalize(),
    'f')``: ``1``, ``1.0``, ``1.00`` and ``1E+0`` all become ``'1'``. Without that, a client that
    sent ``quantity: "1.0"`` on the first request and ``quantity: "1"`` on the retry would get a
    409 for repeating the *same* order, which is precisely the failure idempotency exists to
    prevent. A genuinely different quantity - ``1.005`` against ``1.0`` - renders differently and
    still conflicts.

    An absent ``limit_price`` contributes the empty string, so a market order and a limit order at
    price nothing are not the same fingerprint (they cannot both exist: a limit order without a
    price is not representable in :class:`OrderIntent`'s consumers).

    The digest is over UTF-8 bytes of the pipe-joined fields, and ``|`` cannot appear in a symbol,
    a side, an order type, a canonical decimal or an upper-cased time-in-force, so the join is
    unambiguous without escaping.
    """
    resolved = OrderIntent.from_mapping(intent)
    parts = [
        resolved.symbol,
        resolved.side,
        resolved.order_type,
        canonical_number(resolved.quantity),
        "" if resolved.limit_price is None else canonical_number(resolved.limit_price),
        resolved.time_in_force,
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def decimal_places(value: Decimal) -> int:
    """How many decimal places ``value`` carries, ignoring trailing zeros.

    ``design.md``'s ``decimals(x)``. ``normalize()`` first, so ``Decimal('1.500')`` is **one**
    decimal place rather than three: a client that padded its quantity has not exceeded the
    symbol's precision, and rejecting it would refuse an order the venue accepts.
    ``Decimal('100')`` normalizes to ``1E+2`` and answers zero.
    """
    exponent = to_decimal(value, "value").normalize().as_tuple().exponent
    return -int(exponent) if isinstance(exponent, int) and exponent < 0 else 0


def static_rejection_reason(intent: Any, config: SessionConfig) -> Optional[str]:
    """The first of Requirement 16.5's eight checks that ``intent`` fails, or ``None``.

    ``design.md``'s ``first_of(...)``, in its order and with its comparisons. Every figure
    compared against comes from the session's **frozen** configuration - ``max_order_quantity``,
    ``quantity_precision`` and ``price_precision`` read from the exchange market metadata at start
    (task 25.2), ``validated_symbols``, ``supported_order_types`` and ``supported_sides`` recorded
    beside them - so the comparison is against a recorded value rather than a guess
    (Requirements 16.5, 28.3).

    Pure: it issues no statement and touches no balance, which is what makes Requirement 16.5's
    "SHALL make no change to the Paper_Account's balances or positions" true of the *check* as
    well as of the outcome.
    """
    resolved = OrderIntent.from_mapping(intent)

    if resolved.quantity <= _ZERO:
        return REJECTION_QUANTITY_NOT_POSITIVE
    if resolved.quantity > config.max_order_quantity:
        return REJECTION_QUANTITY_ABOVE_MAX
    if decimal_places(resolved.quantity) > config.quantity_precision:
        return REJECTION_QUANTITY_PRECISION
    if resolved.symbol not in config.validated_symbols:
        return REJECTION_SYMBOL_NOT_VALIDATED
    if resolved.order_type not in config.supported_order_types:
        return REJECTION_ORDER_TYPE_UNSUPPORTED
    if resolved.side not in config.supported_sides:
        return REJECTION_SIDE_UNSUPPORTED
    if resolved.limit_price is not None:
        if resolved.limit_price <= _ZERO:
            return REJECTION_LIMIT_PRICE_NOT_POSITIVE
        if decimal_places(resolved.limit_price) > config.price_precision:
            return REJECTION_LIMIT_PRICE_PRECISION
    return None


#: The ``paper_orders`` CHECK constraints an intent's own values must satisfy before a row can
#: exist at all, as ``(constraint name, predicate)``. Read out of ``009_paper_trading.sql`` lines
#: 728-731 and verified against the file.
#:
#: WHY THIS EXISTS - AND THE REQUIREMENT 16.5 GAP IT RECORDS
#: --------------------------------------------------------
#: Requirement 16.5 asks for a **persisted** ``REJECTED`` order carrying the failed check's name,
#: for all eight of its checks. Four of those eight describe an intent whose values the column
#: constraints **refuse**, so no such row can exist:
#:
#: * ``QUANTITY_NOT_POSITIVE``     -> ``chk_paper_order_quantity CHECK (quantity > 0)``
#: * ``LIMIT_PRICE_NOT_POSITIVE``  -> ``chk_paper_order_limit_price CHECK (limit_price IS NULL OR
#:                                    limit_price > 0)``
#: * ``ORDER_TYPE_UNSUPPORTED``    -> ``chk_paper_order_type CHECK (order_type IN
#:                                    ('market','limit'))`` - but only when the value is outside
#:                                    that pair. A session whose recorded
#:                                    ``supported_order_types`` is *narrower* than the column's
#:                                    rejects a representable value, and that order IS persisted.
#: * ``SIDE_UNSUPPORTED``          -> ``chk_paper_order_side CHECK (side IN ('buy','sell'))``, with
#:                                    the same narrowing case.
#:
#: **The gap, stated rather than papered over.** For those four, this module answers 400
#: ``PAPER_ORDER_INVALID`` with ``details["validation"]`` carrying the *same* reason name and
#: ``details["blocked_by"]`` naming the constraint, and writes **no** order row. The reason a
#: caller reads is therefore the reason Requirement 16.5 names; what is missing is the row.
#:
#: Closing it properly would mean relaxing those CHECK constraints so a rejected order can carry
#: an out-of-range value - which is a *weakening* of a control that currently keeps a nonsense
#: order out of the table entirely, and is not an additive migration. That trade is the spec's to
#: make, not this module's, so the constraints stand and the divergence is reported here, in
#: ``details``, and in the tests.
ORDER_COLUMN_CONSTRAINTS: Tuple[Tuple[str, str], ...] = (
    ("chk_paper_order_quantity", "quantity > 0"),
    ("chk_paper_order_limit_price", "limit_price IS NULL OR limit_price > 0"),
    ("chk_paper_order_type", "order_type IN ('market', 'limit')"),
    ("chk_paper_order_side", "side IN ('buy', 'sell')"),
)


def unrepresentable_order_constraint(intent: Any) -> Optional[str]:
    """The ``paper_orders`` CHECK constraint ``intent``'s own values violate, or ``None``.

    See :data:`ORDER_COLUMN_CONSTRAINTS` for what this is for and for the Requirement 16.5 gap it
    records. Checked in this module rather than left to the database for the reason task 25.3 gives
    for the idempotency key length: a constraint should be the backstop, not the error surface -
    and here it also matters that the Persistence_Layer **double** does not model these four
    CHECKs, so a rejection path that relied on them would pass in the suite and fail against
    PostgreSQL.
    """
    resolved = OrderIntent.from_mapping(intent)
    if resolved.quantity <= _ZERO:
        return ORDER_COLUMN_CONSTRAINTS[0][0]
    if resolved.limit_price is not None and resolved.limit_price <= _ZERO:
        return ORDER_COLUMN_CONSTRAINTS[1][0]
    if resolved.order_type not in ORDER_TYPES:
        return ORDER_COLUMN_CONSTRAINTS[2][0]
    if resolved.side not in ORDER_SIDES:
        return ORDER_COLUMN_CONSTRAINTS[3][0]
    return None


def order_invalid(reason: Any, *, order_id: Any = None) -> PaperOrderInvalid:
    """The 400 a caller raises for a persisted Requirement 16.5 rejection.

    :func:`submit_intent` **returns** the rejected order rather than raising, because Requirement
    16.5 requires the order to exist and to carry its reason - an exception would leave no order.
    A handler that would rather answer 400 than 200-with-a-rejected-order builds the error from
    here, so the code and the ``details`` shape are written once.
    """
    return PaperOrderInvalid(reason, order_id=order_id)


# ══════════════════════════════════════════════════════════════════════════
# TASK 25.5 - THE DETERMINISTIC FILL MODEL (Requirements 16.12, 16.13, 16.14, 18.1)
# ══════════════════════════════════════════════════════════════════════════
#
# There is no ``random.random()`` comparison anywhere in this section, and no ``random`` import
# anywhere in this package - ``tests/test_paper_no_random.py`` AST-walks the whole of
# ``backend_app/backend/paper/`` and fails on one. Partial filling is a **deterministic
# participation cap** (:func:`fillable_quantity`) and slippage is a **recorded rate applied in the
# adverse direction** (:func:`market_fill_price`), both functions of the frozen session config and
# of a validated market event. Replaying the same event sequence therefore produces the same fills
# (Requirement 15.4), which a fill probability could never do.
#
# Every price this section produces is derived from a price present in the session's
# ``paper_market_events`` by the frozen config's slippage transform, or is the caller's own limit
# price. None is drawn, interpolated between two events or extrapolated past the last one
# (Requirements 14.9, 28.3).

#: Which side of the book a market order crosses, and therefore which field is the reference when
#: the selected source supplies a quote rather than only a candle. A buy lifts the **ask**; a sell
#: hits the **bid**. A mapping rather than a conditional so the two cases are one readable table.
REFERENCE_FIELD_FOR_SIDE: Mapping[str, str] = {"buy": "ask", "sell": "bid"}

#: The fields :func:`limit_fill_triggered` consults, per side, in order. A candle answers on
#: ``low`` / ``high``; a tick source that publishes no candle answers on ``last``; ``close`` is the
#: last resort and is what ``paper_market_feed.MarketEvent`` always carries.
TRIGGER_FIELDS_FOR_SIDE: Mapping[str, Tuple[str, ...]] = {
    "buy": ("low", "last", "close"),
    "sell": ("high", "last", "close"),
}


def _event_decimal(event: Any, name: str) -> Optional[Decimal]:
    """One numeric field off a market event, as an exact ``Decimal``, or ``None`` when absent.

    Accepts a ``paper_market_feed.MarketEvent`` (attributes), the mapping stored in
    ``paper_market_events.payload`` (decimal **strings**, which is how that column keeps a price
    exact - see ``paper_repository._jsonb``), or any mapping in that shape. A field that is absent
    or ``None`` answers ``None``: "the source did not supply it" is a different fact from "it is
    zero", and Requirement 18.15 forbids treating them as the same.

    A ``float`` is refused rather than converted, by ``paper_accounting.to_decimal``. That is the
    right outcome even for a market price: a fill priced from a binary float is a fill at a price
    the exchange never published, and every figure derived from it inherits the error
    (Requirement 18.1).
    """
    if event is None:
        return None
    if isinstance(event, Mapping):
        if name in event:
            raw = event[name]
        else:
            payload = event.get("payload")
            raw = payload.get(name) if isinstance(payload, Mapping) else None
    else:
        raw = getattr(event, name, None)
        if raw is None:
            payload = getattr(event, "payload", None)
            raw = payload.get(name) if isinstance(payload, Mapping) else None
    if raw is None:
        return None
    return to_decimal(raw, f"market event {name}")


def _event_text(event: Any, name: str) -> Optional[str]:
    """One text field off a market event, or ``None``. Same two shapes as :func:`_event_decimal`."""
    if event is None:
        return None
    raw = event.get(name) if isinstance(event, Mapping) else getattr(event, name, None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def reference_price(event: Any, side: Any) -> Optional[Decimal]:
    """The reference a market order is priced from, or ``None`` when the event supplies none.

    Task 25.5 and ``design.md``: "the ``ask`` / ``bid`` of the latest validated event when the
    selected source supplies them, otherwise its ``close``". Both halves matter. A source that
    publishes a two-sided quote states what a buy would actually pay, so using it is more faithful
    than using the mid; a source that publishes only candles states the close, and inventing a
    spread around it would be a fabricated figure (Requirement 28.3).

    ``None`` is a real answer and the caller must handle it: :func:`submit_intent` rejects the
    order with ``NO_VALIDATED_PRICE`` rather than substituting anything (Requirement 14.9). A
    non-positive reference also answers ``None`` - a price at or below zero is not a price, and
    valuing a fill at it would put a negative notional into the ledger.

    Args:
        event: The latest **validated** event. Validation is
            ``paper_market_feed.next_validated_event``'s job; this function does not re-validate,
            and an unvalidated event must not reach it.
        side: ``'buy'`` or ``'sell'``, which selects the ask or the bid.
    """
    if event is None:
        return None
    quote_field = REFERENCE_FIELD_FOR_SIDE.get(str(side).strip().lower())
    candidates: List[Optional[Decimal]] = []
    if quote_field is not None:
        candidates.append(_event_decimal(event, quote_field))
    candidates.append(_event_decimal(event, "close"))
    for candidate in candidates:
        if candidate is not None and candidate > _ZERO:
            return candidate
    return None


def market_fill_price(reference: Any, side: Any, config: SessionConfig) -> Decimal:
    """``reference × (1 ± slippage_rate)``, adverse direction only.

    A buy slips **up** and a sell slips **down**, which is the convention
    ``paper_trading_service._execute_fill`` already applies (``drift = slippage_rate if side ==
    'buy' else -slippage_rate``) and which every figure the Paper_Trading_UI shows today was
    produced by. It is matched rather than reinvented.

    Adverse only, and deliberately: a favourable slip would be a price better than the market
    published, which is an invented price and would inflate a reported track record
    (Requirements 14.9, 28.3). ``slippage_rate`` comes from the frozen config, so it is the rate
    the session recorded at start and cannot have changed mid-session (Requirement 16.12).

    Quantized to the session's ``price_precision`` with the session's recorded rounding mode, so
    the price written to ``paper_fills.price`` is the price the arithmetic used
    (Requirement 18.2). Exact decimal throughout; a ``float`` reference is refused.
    """
    exact = to_decimal(reference, "reference price")
    if exact <= _ZERO:
        raise InvalidOrderIntent(
            f"a market order's reference price must be strictly positive, got {exact}; a "
            "reference is never synthesised or defaulted (Requirement 14.9)"
        )
    normalised = str(side).strip().lower()
    if normalised not in REFERENCE_FIELD_FOR_SIDE:
        raise InvalidOrderIntent(f"order side must be 'buy' or 'sell', got {side!r}")
    drift = config.slippage_rate if normalised == "buy" else -config.slippage_rate
    accounting = config.accounting()
    return accounting.price(exact * (_ONE + drift))


def slippage_amount(
    quantity: Any, fill_price: Any, reference: Any, config: SessionConfig
) -> Decimal:
    """``|fill_price - reference| × quantity`` at the Minor_Units scale.

    ``design.md``'s ``slip_amt <- quantize(quantity * ABS(price - order.reference_price),
    minor_units)`` - the *cost* the slippage imposed, which is what ``paper_fills.slippage_minor``
    records as an exact integer Minor_Units value (Requirement 16.12). Zero for a resting limit
    order, because it fills at exactly its limit and its reference **is** its limit.
    """
    accounting = config.accounting()
    qty = accounting.qty(quantity)
    price = accounting.price(fill_price)
    ref = accounting.price(reference)
    return accounting.money(abs(price - ref) * qty)


def fee_amount(quantity: Any, fill_price: Any, config: SessionConfig) -> Decimal:
    """``quantity × fill_price × fee_rate`` at the Minor_Units scale.

    ``design.md``'s ``fee <- quantize(quantity * price * config.fee_rate, minor_units)``.
    ``fee_rate`` is the frozen config's, so the fee charged is the fee the session recorded
    (Requirement 16.12), and the amount is quantized once at the end rather than per factor so it
    does not depend on how the product was grouped (Requirement 18.2).
    """
    accounting = config.accounting()
    return accounting.money(
        accounting.qty(quantity) * accounting.price(fill_price) * config.fee_rate
    )


def limit_fill_triggered(order: Any, event: Any) -> bool:
    """Whether ``event`` is the first at which a resting limit order fills.

    Task 25.5 and ``design.md``: ``buy: low <= limit`` (candle) or ``last <= limit`` (tick);
    ``sell: high >= limit`` or ``last >= limit``. The candle field is preferred because it is the
    stronger statement - a candle whose low reached the limit means the market traded there,
    whether or not it closed there - and ``close`` is the last resort because
    ``paper_market_feed.MarketEvent`` always carries one.

    ``False`` when the order carries no limit price, when the event supplies none of the three
    fields, or when the side is not one of the two. None of those is an error: it means this event
    is not the one that fills this order, and the order keeps resting.
    """
    limit = _event_decimal(order, "limit_price")
    if limit is None or limit <= _ZERO:
        return False
    side = str(_event_text(order, "side") or "").lower()
    fields = TRIGGER_FIELDS_FOR_SIDE.get(side)
    if fields is None:
        return False
    for name in fields:
        candidate = _event_decimal(event, name)
        if candidate is None:
            continue
        return candidate <= limit if side == "buy" else candidate >= limit
    return False


def limit_fill_price(order: Any, config: SessionConfig) -> Decimal:
    """A resting limit order's fill price: **exactly** its limit.

    No slippage in either direction. An adverse slip on a resting order would fill it worse than
    the price it was resting at, which the venue would not do; a favourable one would fill it
    better, which is an improvement nobody offered - "an improvement on a resting limit is an
    invented price" (Requirements 14.9, 28.3). So the limit, quantized to the session's price
    precision, and nothing else.
    """
    limit = _event_decimal(order, "limit_price")
    if limit is None or limit <= _ZERO:
        raise InvalidOrderIntent(
            "a resting limit order fills at exactly its limit price, and this order carries none"
        )
    return config.accounting().price(limit)


def remaining_quantity(order: Any, config: SessionConfig) -> Decimal:
    """``quantity - filled_quantity`` at the session's quantity precision, floored at zero.

    Floored rather than allowed negative: a negative remainder would mean the order is already
    over-filled, which ``chk_paper_order_fill_bound`` forbids and which
    :func:`apply_fill`'s over-fill guard refuses. Reporting zero here makes
    :func:`fillable_quantity` answer zero, so a caller cannot accidentally submit a negative fill
    on top of it.
    """
    accounting = config.accounting()
    ordered = accounting.qty(_event_decimal(order, "quantity") or _ZERO)
    filled = accounting.qty(_event_decimal(order, "filled_quantity") or _ZERO)
    remainder = accounting.qty(ordered - filled)
    return remainder if remainder > _ZERO else _ZERO


def fillable_quantity(order: Any, event: Any, config: SessionConfig) -> Decimal:
    """``MIN(remaining, quantize(event.volume × participation_rate))`` - a **cap**, not a coin.

    ``design.md``'s function, one for one, and the whole of task 25.5's partial-fill rule:

    * The event reports **no volume** (absent, ``None``, or not positive) -> the whole remaining
      quantity. A source that states no volume has stated nothing about liquidity, and inventing a
      figure to cap against would be the fabricated measurement Requirement 28.3 forbids.
    * Otherwise -> the participation cap, quantized to the session's ``quantity_precision``, and
      the remaining quantity, whichever is smaller.

    This is deterministic in the strongest sense: the same order against the same event under the
    same frozen config yields the same quantity every time, which is what makes a replay
    byte-identical (Requirement 15.4). It is emphatically **not** a fill probability - the
    forbidden simulator's ``random.random() > self.fill_probability`` (line 369) is exactly what
    this replaces, and a probability would make the same recorded event sequence produce a
    different track record on every run.

    Returns ``Decimal('0')`` when nothing remains, which is a caller's signal not to fill.
    """
    remaining = remaining_quantity(order, config)
    if remaining <= _ZERO:
        return _ZERO
    volume = _event_decimal(event, "volume")
    if volume is None or volume <= _ZERO or config.participation_rate <= _ZERO:
        return remaining
    cap = config.accounting().qty(volume * config.participation_rate)
    return cap if cap < remaining else remaining


# ══════════════════════════════════════════════════════════════════════════
# ROW -> VALUE (the only place a persisted row becomes an accounting value)
# ══════════════════════════════════════════════════════════════════════════


def _instant_of(value: Any) -> Optional[datetime]:
    """A ``TIMESTAMPTZ`` value as a tz-aware UTC ``datetime``, or ``None``.

    ``paper_repository._instant`` writes ISO-8601 with an explicit offset, so reading it back with
    ``datetime.fromisoformat`` recovers the instant exactly. A trailing ``Z`` - which some drivers
    and PostgREST both emit - is translated, because ``fromisoformat`` on this interpreter's
    minimum version does not accept it. A naive value is assumed UTC and made explicit, so
    ``paper_accounting.latest_price_at``'s ``max()`` never compares a naive instant with an aware
    one and raises.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def account_of(row: Mapping[str, Any]) -> Account:
    """One ``paper_accounts`` row as a :class:`~paper_accounting.Account`.

    Every money column is read through ``to_decimal``, which refuses a ``float``: the columns are
    ``NUMERIC(28,10)`` and a driver that handed back a float has already lost the exactness the
    column kept (Requirement 18.1).
    """
    return Account(
        available_balance=to_decimal(row["available_balance"], "available_balance"),
        locked_balance=to_decimal(row.get("locked_balance") or 0, "locked_balance"),
        realized_pnl=to_decimal(row.get("realized_pnl") or 0, "realized_pnl"),
        total_equity=to_decimal(row.get("total_equity") or 0, "total_equity"),
        currency=str(row.get("currency") or "USD"),
    )


def position_of(row: Mapping[str, Any]) -> Position:
    """One ``paper_positions`` row as a :class:`~paper_accounting.Position`.

    ``current_price``, ``unrealized_pnl`` and ``price_at`` stay ``None`` when the column is
    ``NULL``. That is Requirement 18.15 read literally: a position that has not been revalued has
    no price, and substituting a zero would put an invented valuation into the equity identity.
    """
    return Position(
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        size=to_decimal(row["size"], "size"),
        entry_price=to_decimal(row["entry_price"], "entry_price"),
        current_price=(
            None if row.get("current_price") is None
            else to_decimal(row["current_price"], "current_price")
        ),
        unrealized_pnl=(
            None if row.get("unrealized_pnl") is None
            else to_decimal(row["unrealized_pnl"], "unrealized_pnl")
        ),
        price_at=_instant_of(row.get("price_at")),
        opened_at=_instant_of(row.get("opened_at")),
        closed_at=_instant_of(row.get("closed_at")),
    )


def positions_of(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Position]:
    """``{symbol: Position}`` from ``paper_positions`` rows, **closed rows included**.

    Closed rows are kept because a closed position is ``size = 0`` with ``closed_at`` set and is
    never deleted (Requirement 18.5), and ``paper_accounting.apply_fill`` needs the open one to
    tell an add from a close. When a symbol has both an open row and closed history, the **open**
    row wins: ``uq_paper_position_open`` guarantees there is at most one.
    """
    resolved: Dict[str, Position] = {}
    for row in rows:
        position = position_of(row)
        existing = resolved.get(position.symbol)
        if existing is None or (position.is_open and not existing.is_open):
            resolved[position.symbol] = position
    return resolved


def _minor_units(amount: Any, config: SessionConfig, column: str) -> int:
    """A money amount as the exact integer Minor_Units the ``*_minor`` column stores.

    ``amount.scaleb(minor_unit_exponent)`` and an exactness check, not a ``round()``: a value that
    is not a whole number of minor units at the session's recorded exponent is refused rather than
    rounded, because the rounding would silently change a recorded fee (Requirements 16.12, 18.2).
    Every caller here has already quantized through ``AccountingConfig.money``, so a refusal means
    the exponent and the quantization disagree - a bug, not a fact about the money.
    """
    scaled = to_decimal(amount, column).scaleb(config.minor_unit_exponent)
    if scaled != scaled.to_integral_value():
        raise InvalidOrderIntent(
            f"{column} of {amount} is not a whole number of minor units at exponent "
            f"{config.minor_unit_exponent}; it is not rounded here, because rounding a recorded "
            f"fee or slippage would change the figure the session reports (Requirement 18.2)"
        )
    return int(scaled)


# ══════════════════════════════════════════════════════════════════════════
# TASK 25.4 - ``apply_fill``, THE SINGLE WRITE PATH FOR EVERY FILL
# ══════════════════════════════════════════════════════════════════════════

#: :attr:`FillOutcome.outcome` values. Three, and no fourth: every call either applies the fill or
#: returns unchanged for one of the two named reasons.
FILL_APPLIED = "APPLIED"
FILL_TERMINAL = "TERMINAL"
FILL_DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class FillOutcome:
    """What :func:`apply_fill` produces: what happened, and every row it wrote.

    ``outcome`` is :data:`FILL_APPLIED`, :data:`FILL_TERMINAL` or :data:`FILL_DUPLICATE`. The two
    no-op outcomes carry the order **unchanged** and every other field ``None``, which is
    Requirements 16.3 and 16.9 read literally - a terminal order and a repeated ``fill_event_id``
    change nothing, and this type has no way to report a change they did not make.

    ``admission`` is the :class:`~paper_market_feed.ExecutionAdmission` the feed gate minted. It is
    present on an applied fill and on nothing else, so "the gate was consulted before this fill"
    is readable off the result rather than taken on trust.
    """

    outcome: str
    order: Mapping[str, Any]
    fill: Optional[Mapping[str, Any]] = None
    position: Optional[Mapping[str, Any]] = None
    closed_position: Optional[Mapping[str, Any]] = None
    account: Optional[Mapping[str, Any]] = None
    balance_event: Optional[Mapping[str, Any]] = None
    trade: Optional[Mapping[str, Any]] = None
    snapshot: Optional[Mapping[str, Any]] = None
    admission: Optional[ExecutionAdmission] = None
    result: Optional[FillResult] = None
    fill_price: Optional[Decimal] = None
    fee: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    attempts: int = 1

    @property
    def applied(self) -> bool:
        """Whether this call moved anything."""
        return self.outcome == FILL_APPLIED


def _fill_event_totals(
    fills: Sequence[Mapping[str, Any]], config: SessionConfig
) -> Tuple[Decimal, Decimal, int, int]:
    """``(filled_quantity, notional, fee_minor, slippage_minor)`` recomputed from the fill rows.

    Recomputed rather than read off ``paper_orders.filled_quantity``, deliberately: P-20's oracle
    is the sum over ``paper_fills``, and a drift between the column and the rows is a failure. If
    this module derived the column from itself, that drift would be undetectable - so every write
    of ``filled_quantity``, ``avg_fill_price``, ``fee_minor`` and ``slippage_minor`` on an order
    comes from here, and the column is a projection of the rows rather than a second record.
    """
    accounting = config.accounting()
    quantity = _ZERO
    notional = _ZERO
    fee_minor = 0
    slippage_minor = 0
    for row in fills:
        row_qty = accounting.qty(row["quantity"])
        quantity += row_qty
        notional += row_qty * accounting.price(row["price"])
        fee_minor += int(row.get("fee_minor") or 0)
        slippage_minor += int(row.get("slippage_minor") or 0)
    return (accounting.qty(quantity), notional, fee_minor, slippage_minor)


async def apply_fill(
    supabase: Any,
    session: Mapping[str, Any],
    order: Mapping[str, Any],
    *,
    config: SessionConfig,
    quantity: Any,
    price: Any,
    fill_event_id: Any,
    filled_at: datetime,
    reference: Any = None,
    market_event_id: Any = None,
    release_from_locked: Any = None,
    series_index: Any = 0,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> FillOutcome:
    """Apply one fill. The single write path for every fill in the system (task 25.4).

    THE FEED GATE (Requirement 14.5's simulator half - the part task 24.4 left outstanding)
    --------------------------------------------------------------------------------------
    Every attempt re-reads the ``paper_sessions`` row through
    ``paper_repository.read_session`` - inside the attempt, not once outside it - and passes it to
    ``paper_market_feed.admit_execution``, which returns an
    :class:`~paper_market_feed.ExecutionAdmission` or raises ``FeedNotHealthy``.
    ``TRADEABLE_FEED_STATES`` is exactly ``("HEALTHY",)``, so **no fill is applied while
    ``session.feed_state != 'HEALTHY'``**. The token is required on the write path: the writes
    below are reached only through a local that holds one, which is why the token is a token and
    not a boolean - a boolean return can be forgotten, and the omission would look like a pass.

    THE GUARDS, IN ORDER
    --------------------
    1. **Terminal order** -> returns unchanged, :data:`FILL_TERMINAL`. Requirement 16.3.
    2. **Duplicate ``(order_id, fill_event_id)``** -> returns unchanged, :data:`FILL_DUPLICATE`.
       Requirements 16.9, 18.13. ``uq_paper_fill_event`` is the durable arbiter; this read is the
       one that lets the answer be "unchanged" rather than a ``23505``.
    3. **The feed gate**, as above. It sits after the two no-op guards on purpose: Requirements
       16.3 and 16.9 say a terminal order and a repeated event change nothing *whatever else is
       true*, and a gate placed ahead of them would turn a no-op into a refusal.
    4. **Over-fill** -> :class:`PaperOverFill` (409), raised before any statement, so the
       cumulative filled quantity, the state, the position, the balance and the realized PnL are
       untouched because nothing was issued. Requirement 16.7.
    5. **The transition** -> :class:`PaperOrderInvalid` (400) with
       ``details["validation"] = "ILLEGAL_TRANSITION"`` and ``details["from"]`` /
       ``details["to"]`` naming the rejected pair, when the state read inside the attempt does not
       permit the target this fill implies (Requirements 16.2, 16.4). Also before any statement -
       see the comment at the guard for why that placement is the whole point of it.
    6. **``ASSERT accounting.invariants_hold``** -> :class:`PaperInvariantViolation` (500) naming
       the invariant, also before any statement. Requirement 18.14.

    THE WRITES, AND THE ORDERING THE MISSING TRANSACTION FORCES
    ----------------------------------------------------------
    ``paper_fills`` -> ``paper_accounts`` (``version = version + 1``) -> ``paper_balance_events``
    -> ``paper_positions`` -> ``paper_orders`` -> ``paper_trades`` (only when a position reached
    size zero) -> ``paper_equity_snapshots`` (``cause='FILL'``).

    That order is chosen, not inherited. See the section header for what PostgREST does not offer;
    the two consequences that shaped this list:

    * **The fill row goes first, because it is the idempotency anchor.** ``uq_paper_fill_event``
      is what makes a repeated event a no-op, and a fill row written before the money moves can be
      *resumed*; money moved before the fill row exists could be applied twice.
    * **RESUME.** A fill row whose ``id`` appears on no ``paper_balance_events`` row is a previous
      attempt that died between the fill insert and the ledger. Guard 2 distinguishes that from a
      genuinely applied duplicate and **resumes** rather than skipping, so the retry finishes the
      movement instead of losing it. This is the one recovery the write ordering admits.
    * **Past the account UPDATE there is no recovery, and none is faked.** A failure after
      ``bump_version`` has moved the money leaves a partial write that a transaction would have
      rolled back. It is reported as :class:`PaperConcurrencyExhausted` with
      ``details["partial_write"] = True`` and the phase that failed, and it is **not** retried,
      because a retry would double-apply. Requirement 24.6's single transaction is not achievable
      over this transport.

    Args:
        supabase: The caller's RLS-scoped Persistence_Layer handle.
        session: The ``paper_sessions`` row. ``id`` and ``user_id`` are read from it; the
            ``feed_state`` that decides is re-read inside each attempt.
        order: The ``paper_orders`` row being filled. Re-read inside each attempt, so a stale
            image cannot decide a guard.
        config: The session's frozen configuration.
        quantity: The filled quantity, strictly positive and at or below what remains.
        price: The fill price. Produced by :func:`market_fill_price` or :func:`limit_fill_price`;
            never synthesised here.
        fill_event_id: The event identity ``uq_paper_fill_event`` de-duplicates on. Deterministic
            for a replay - see :func:`market_fill_event_id` and :func:`resting_fill_event_id`.
        filled_at: The fill instant. **Passed in, never read from a clock**: every timestamp this
            module writes comes from the market event or from the caller, which is what makes a
            replay byte-identical (Requirement 15.4).
        reference: The reference price the slippage is measured against, for
            ``paper_fills.slippage_minor``. Defaults to the order's recorded ``reference_price``,
            and to ``price`` when the order has none - which records zero slippage rather than
            inventing a reference.
        market_event_id: The identity of the ``paper_market_events`` row this fill was priced from,
            when there is one. Recorded so a fill's price provenance is a stored fact and not only
            a derivable one (P-55).

            **The identity is the event's ``source_event_id``, not the row's primary key**, and
            both callers pass exactly that: :func:`submit_intent` passes the ``latest_event``'s and
            :func:`check_resting_orders` the triggering event's. Three reasons, and the third is
            the decisive one:

            * ``uq_paper_market_event`` is UNIQUE ``(session_id, source_event_id)`` and every
              ``paper_fills`` row carries ``session_id``, so ``(session_id, market_event_id)``
              resolves to exactly one event row. Nothing is unreachable from it that the primary
              key would reach.
            * ``paper_market_events.id`` is a ``UUID`` generated by the database and
              ``paper_fills.market_event_id`` is a nullable ``TEXT`` with no foreign key (009
              section 6), which is the shape of a recorded identity rather than of a key
              reference; ``source_event_id`` is itself ``TEXT NOT NULL``. And the primary key is
              not available on every path that has an event: ``next_validated_event`` returns a
              :class:`~paper_market_feed.MarketEvent` (which carries ``source_event_id`` and no
              row id), and on its duplicate branch there is no inserted row at all.
            * **Replay reproduces it.** ``paper_replay._flat_event`` deliberately does not copy the
              row's ``id`` into the event it reconstructs, because a replay that supplied a
              database-generated key would produce a fill differing from the recorded one in that
              column alone. It does copy ``source_event_id``, so the column this path writes is one
              a replay can write identically - which is what Requirements 15.4 and 15.5 ask of
              every value a fill records.

            ``None`` is a real answer and is stored as SQL ``NULL``: a market order priced from an
            explicit ``reference`` rather than from an event names no event, and inventing one
            would be a fabricated provenance. That is the only path that leaves the column empty -
            the session loop (``paper_session_service.step_session``) passes the validated event to
            both fill paths.
        release_from_locked: Cash this fill consumes out of ``locked_balance``. ``None`` derives it
            for a limit order from :func:`required_funds` at the limit price for the filled
            quantity, clamped to what is actually locked; a market order locks nothing, so it is
            zero.
        series_index: The equity series this snapshot belongs to (Requirement 17.15).
        sleep: The retry delay, for tests. See :func:`with_retries`.

    Returns:
        A :class:`FillOutcome`.

    Raises:
        PaperOverFill, PaperInvariantViolation, PaperConcurrencyExhausted: as above.
        paper_market_feed.FeedNotHealthy: the session's feed state is not ``HEALTHY``.
        PaperError: ``PAPER_PERSISTENCE_UNAVAILABLE`` when 009 is unapplied.
    """
    user_id = _require_session_text(session, "user_id")
    session_id = _require_session_text(session, "id")
    order_id = _require_session_text(order, "id")
    account_id = _require_session_text(order, "account_id")
    event_id = str(fill_event_id).strip()
    if not event_id:
        raise InvalidOrderIntent(
            "a fill needs a fill_event_id: it is the identity uq_paper_fill_event de-duplicates "
            "on, and without one a repeated event cannot be recognised (Requirement 16.9)"
        )
    if not isinstance(filled_at, datetime):
        raise InvalidOrderIntent(
            "filled_at must be a datetime supplied by the caller; this module reads no clock, "
            "because a clock read would make a replay diverge (Requirement 15.4)"
        )

    accounting = config.accounting()
    fill_qty = accounting.qty(quantity)
    fill_price = accounting.price(price)
    if fill_qty <= _ZERO:
        raise InvalidOrderIntent(f"fill quantity must be strictly positive, got {fill_qty}")
    if fill_price <= _ZERO:
        raise InvalidOrderIntent(f"fill price must be strictly positive, got {fill_price}")

    async def _attempt(number: int) -> FillOutcome:
        # ── the FOR UPDATE substitutes: the account image with its version, and a fresh order ──
        account_row = repo.lock_account_for_update(supabase, user_id, account_id=account_id)
        order_row = repo.read_order(supabase, user_id, order_id)
        if order_row is None:
            raise InvalidOrderIntent(
                f"no paper order {order_id} is readable for this identity, so there is nothing "
                "to fill; nothing was written"
            )

        state = PaperOrderState(str(order_row["order_state"]))

        # ── guard 1: a terminal order changes nothing (Requirement 16.3) ──
        if is_terminal(state):
            logger.info(
                "[paper-simulator] fill %s ignored: order %s is %s, which is terminal "
                "(Requirement 16.3)",
                event_id,
                order_id,
                state.value,
            )
            return FillOutcome(FILL_TERMINAL, order=order_row, attempts=number)

        # ── guard 2: a repeated fill_event_id changes nothing (Requirements 16.9, 18.13) ──
        fill_rows = repo.get_fills(
            supabase, user_id, session_id=session_id, order_id=order_id
        )
        existing_fill = next(
            (row for row in fill_rows if str(row.get("fill_event_id")) == event_id), None
        )
        resumed_fill: Optional[Mapping[str, Any]] = None
        if existing_fill is not None:
            ledger = repo.get_balance_events(
                supabase, user_id, account_id=account_id, session_id=session_id, cause="FILL"
            )
            settled = any(
                str(row.get("fill_id") or "") == str(existing_fill["id"]) for row in ledger
            )
            if settled:
                logger.info(
                    "[paper-simulator] fill %s on order %s is a duplicate; nothing changed "
                    "(Requirements 16.9, 18.13)",
                    event_id,
                    order_id,
                )
                return FillOutcome(FILL_DUPLICATE, order=order_row, attempts=number)
            # The resume of the module's write ordering: the fill row landed, the money did not.
            resumed_fill = existing_fill
            logger.warning(
                "[paper-simulator] resuming fill %s on order %s: the fill row exists but no "
                "balance event carries it, so a previous attempt stopped between the two. The "
                "money has not moved, so finishing it cannot move it twice.",
                event_id,
                order_id,
            )

        # ── the feed gate (Requirement 14.5), on the row read inside THIS attempt ──
        session_row = repo.read_session(supabase, user_id, session_id)
        if session_row is None:
            raise InvalidOrderIntent(
                f"no paper session {session_id} is readable for this identity, so the feed state "
                "that decides whether a fill may be applied cannot be read; nothing was written"
            )
        admission = admit_execution(session_row)

        # ── guard 3: over-fill (Requirement 16.7), before any statement ──
        prior_quantity, _, prior_fee_minor, prior_slippage_minor = _fill_event_totals(
            [row for row in fill_rows if row is not resumed_fill], config
        )
        ordered = accounting.qty(order_row["quantity"])
        filled_quantity = accounting.qty(prior_quantity + fill_qty)
        if filled_quantity > ordered:
            raise PaperOverFill(
                order_id,
                ordered=ordered,
                filled=prior_quantity,
                requested=fill_qty,
                details={"session_id": session_id, "fill_event_id": event_id},
            )

        # ── guard 4: the transition itself (Requirements 16.2, 16.4), before any statement ──
        #
        # The target is decided here rather than at write 5, and the reason is not tidiness. Write
        # 5's ``update_order`` validates ``state -> target`` against
        # ``paper_order_allowed_transitions`` and refuses an illegal one - but by then the fill row,
        # the account balance, the ledger row and the position have all been written, and this
        # transport has no ROLLBACK to undo them. The refusal then satisfied Requirement 16.4's
        # "leave the stored state unchanged" while leaving exactly the partial write Requirement
        # 16.10 forbids. Checked here, nothing has been issued, so "unchanged" is a fact about the
        # whole account and not only about the order's state column.
        #
        # ``CREATED`` is the one origin this can refuse in practice: guard 1 has already answered
        # for the three terminal states, and ``ACCEPTED`` and ``PARTIALLY_FILLED`` both admit both
        # fill targets. No production caller reaches here with a ``CREATED`` order - a market
        # order's fill runs after its accept, and ``check_resting_orders`` reads
        # ``legacy_status='OPEN'`` - but ``apply_fill`` is this module's single public write path for
        # a fill, and a public write path does not get to assume its callers.
        target = (
            PaperOrderState.FILLED
            if filled_quantity == ordered
            else PaperOrderState.PARTIALLY_FILLED
        )
        if not can_transition(state, target):
            logger.error(
                "[paper-simulator] fill %s on order %s refused: %s -> %s is not one of "
                "Requirement 16.2's permitted transitions. Nothing was written.",
                event_id,
                order_id,
                state.value,
                target.value,
            )
            raise PaperOrderInvalid(
                "ILLEGAL_TRANSITION",
                order_id=order_id,
                details={
                    "session_id": session_id,
                    "fill_event_id": event_id,
                    "from": state.value,
                    "to": target.value,
                    "persisted": False,
                },
            )

        # ── the arithmetic, and the closing assert, both before the first write ──
        account = account_of(account_row)
        position_rows = repo.get_positions(
            supabase, user_id, account_id=account_id, include_closed=True
        )
        positions = positions_of(position_rows)
        symbol = str(order_row["symbol"])
        side = str(order_row["side"])
        existing_position = positions.get(symbol)

        reference_for_slippage = accounting.price(
            reference
            if reference is not None
            else (order_row.get("reference_price") or fill_price)
        )
        fee = fee_amount(fill_qty, fill_price, config)
        slippage = slippage_amount(fill_qty, fill_price, reference_for_slippage, config)

        release = _release_for_fill(
            order_row,
            account,
            fill_qty,
            config,
            release_from_locked=release_from_locked,
        )

        try:
            result = accounting_apply_fill(
                account,
                positions,
                {"symbol": symbol, "side": side},
                quantity=fill_qty,
                price=fill_price,
                fee=fee,
                config=accounting,
                filled_at=filled_at,
                release_from_locked=release,
            )
            prices = last_validated_prices(result.positions.values())
            prices[symbol] = fill_price
            assert_invariants(result.account, result.positions, prices, accounting)
        except InvariantViolation as exc:
            # Requirement 18.14. Nothing has been issued at this point, so "the stored balances,
            # positions, realized PnL and equity series remain unchanged" is a fact about what was
            # never written rather than about what was rolled back.
            logger.error(
                "[paper-simulator] fill %s on order %s refused by the %s invariant: %s",
                event_id,
                order_id,
                exc.invariant,
                exc,
            )
            raise PaperInvariantViolation(
                exc.invariant,
                phase="apply_fill",
                details={"order_id": order_id, "session_id": session_id},
            ) from exc

        # ── write 1: the fill row, the idempotency anchor (skipped on a resume) ──
        if resumed_fill is None:
            try:
                fill_row = repo.insert_fill(
                    supabase,
                    order_id=order_id,
                    user_id=user_id,
                    session_id=session_id,
                    fill_event_id=event_id,
                    quantity=fill_qty,
                    price=fill_price,
                    fee_minor=_minor_units(fee, config, "fee_minor"),
                    slippage_minor=_minor_units(slippage, config, "slippage_minor"),
                    market_event_id=market_event_id,
                    filled_at=filled_at,
                )
            except repo.PaperDuplicateFill:
                # ``uq_paper_fill_event`` refused: a concurrent writer inserted this event between
                # guard 2 and here. That writer owns the movement, so this call changes nothing -
                # which is exactly Requirement 16.9's outcome, reached through the index rather
                # than through the read.
                logger.info(
                    "[paper-simulator] fill %s on order %s was inserted concurrently; this call "
                    "changed nothing (Requirement 16.9)",
                    event_id,
                    order_id,
                )
                return FillOutcome(FILL_DUPLICATE, order=order_row, attempts=number)
        else:
            fill_row = resumed_fill

        # ── write 2: the money. Past this point nothing is retried (see the docstring). ──
        account_after = repo.bump_version(
            supabase,
            user_id=user_id,
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

        with _NoRetryPastTheMoney("apply_fill", order_id, session_id):
            # write 3: the ledger row, which is also what marks this fill as settled for the
            # resume above. It carries ``fill_id``, so the marker and the money are tied together.
            balance_event = repo.insert_balance_event(
                supabase,
                account_id=account_id,
                user_id=user_id,
                session_id=session_id,
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

            # write 4: the position. A reversal through zero writes the closed leg in its own row
            # first, so its history is not overwritten by the side that replaced it - the same two
            # statements ``paper_trading_service._execute_fill`` issues, and for the same reason.
            closed_row: Optional[Mapping[str, Any]] = None
            new_position = result.position
            if (
                existing_position is not None
                and existing_position.is_open
                and existing_position.side != new_position.side
            ):
                closed_row = repo.upsert_position(
                    supabase,
                    account_id=account_id,
                    user_id=user_id,
                    session_id=session_id,
                    symbol=symbol,
                    side=existing_position.side,
                    size=_ZERO,
                    entry_price=existing_position.entry_price,
                    opened_at=existing_position.opened_at or filled_at,
                    current_price=fill_price,
                    unrealized_pnl=_ZERO,
                    price_at=filled_at,
                    closed_at=filled_at,
                )
            position_row = repo.upsert_position(
                supabase,
                account_id=account_id,
                user_id=user_id,
                session_id=session_id,
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

            # write 5: the order state. ``target`` was decided by guard 4 - PARTIALLY_FILLED or
            # FILLED according to whether the cumulative filled quantity equals the ordered
            # quantity (Requirements 16.13, 16.14) - with every derived column recomputed from the
            # fill ROWS rather than incremented. ``update_order``'s own transition check and
            # ``trg_paper_order_transition_guard`` remain the durable guarantees; guard 4 is what
            # makes the refusal arrive before any statement.
            _, notional, _, _ = _fill_event_totals(
                [row for row in fill_rows if row is not resumed_fill] + [dict(fill_row)], config
            )
            order_after = repo.update_order(
                supabase,
                user_id=user_id,
                order_id=order_id,
                order_state=target,
                expected_state=state,
                filled_quantity=filled_quantity,
                avg_fill_price=accounting.price(notional / filled_quantity),
                fee_minor=prior_fee_minor + _minor_units(fee, config, "fee_minor"),
                slippage_minor=(
                    prior_slippage_minor + _minor_units(slippage, config, "slippage_minor")
                ),
            )

            # write 6: the closed round-trip, only when a position reached size zero
            # (Requirement 18.10).
            trade_row: Optional[Mapping[str, Any]] = None
            if result.closed_trade is not None:
                closed = result.closed_trade
                trade_row = repo.insert_trade(
                    supabase,
                    account_id=account_id,
                    user_id=user_id,
                    session_id=session_id,
                    symbol=closed.symbol,
                    side=closed.side,
                    quantity=closed.quantity,
                    entry_price=closed.entry_price,
                    exit_price=closed.exit_price,
                    realized_pnl=closed.realized_pnl,
                    fee_minor=_minor_units(closed.fee, config, "fee_minor"),
                    opened_at=closed.opened_at or filled_at,
                    closed_at=closed.closed_at or filled_at,
                )

            # write 7: the equity point (Requirement 18.11). One per APPLIED fill and none for a
            # repeated fill_event_id, because guard 2 returns before reaching here
            # (Requirement 18.13).
            snapshot_row = repo.insert_equity_snapshot(
                supabase,
                user_id=user_id,
                session_id=session_id,
                series_index=series_index,
                total_equity=result.account.total_equity,
                available_balance=result.account.available_balance,
                locked_balance=result.account.locked_balance,
                position_market_value=result.position_market_value,
                stale=False,
                cause="FILL",
                taken_at=filled_at,
            )

        logger.info(
            "[PAPER_FILL] user=%s session=%s order=%s event=%s sym=%s side=%s qty=%s px=%s "
            "fee=%s slip=%s state=%s feed=%s",
            user_id,
            session_id,
            order_id,
            event_id,
            symbol,
            side,
            fill_qty,
            fill_price,
            fee,
            slippage,
            target.value,
            admission.feed_state,
        )
        return FillOutcome(
            FILL_APPLIED,
            order=order_after,
            fill=fill_row,
            position=position_row,
            closed_position=closed_row,
            account=account_after,
            balance_event=balance_event,
            trade=trade_row,
            snapshot=snapshot_row,
            admission=admission,
            result=result,
            fill_price=fill_price,
            fee=fee,
            slippage=slippage,
            filled_quantity=filled_quantity,
            attempts=number,
        )

    # `paper.fill.apply_latency_ms` (Requirements 26.6, 27.6). Measured around the whole
    # application including its retries, because that is the delay the session actually waited.
    # `perf_counter`: an interval, so a wall-clock correction must not make it negative.
    _measured_from = time.perf_counter()
    try:
        return await with_retries(
            "apply_fill",
            _attempt,
            sleep=sleep,
            details={
                "order_id": order_id,
                "session_id": session_id,
                "fill_event_id": event_id,
            },
        )
    finally:
        _record_fill_applied((time.perf_counter() - _measured_from) * 1000.0)


def _require_session_text(row: Any, key: str) -> str:
    """One required identifier off a row, as non-empty text.

    Refused rather than defaulted: every statement below is scoped by ``user_id`` and by the
    session or account it belongs to, and a blank scope would widen a predicate rather than narrow
    it (Requirement 21.5).
    """
    value = row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)
    text = "" if value is None else str(value).strip()
    if not text:
        raise InvalidOrderIntent(
            f"the row carries no {key!r}; every paper statement is scoped by the owning user and "
            "session, so a blank identifier is refused rather than treated as 'any'"
        )
    return text


def _release_for_fill(
    order_row: Mapping[str, Any],
    account: Account,
    fill_qty: Decimal,
    config: SessionConfig,
    *,
    release_from_locked: Any = None,
) -> Decimal:
    """How much of ``locked_balance`` this fill consumes.

    A market order locks nothing at acceptance, so it releases nothing. A limit order locked
    ``required_funds`` for its **whole** quantity, so a partial fill releases
    ``required_funds`` for the filled portion at the limit price.

    Clamped to what is actually locked, for two reasons that both matter: the lock was quantized
    once at the money scale while the releases are quantized per fill, so the parts need not sum to
    the whole; and ``paper_accounting.apply_fill`` refuses a release that would drive
    ``locked_balance`` below zero (Requirement 18.4) rather than letting it, so an unclamped
    estimate would turn a rounding remainder into a refused fill.
    """
    accounting = config.accounting()
    if release_from_locked is not None:
        explicit = accounting.money(release_from_locked)
        return explicit if explicit < account.locked_balance else account.locked_balance
    if str(order_row.get("order_type") or "").strip().lower() != "limit":
        return _ZERO
    limit = _event_decimal(order_row, "limit_price")
    if limit is None or limit <= _ZERO:
        return _ZERO
    needed = required_funds({"quantity": fill_qty}, limit, accounting)
    return needed if needed < account.locked_balance else account.locked_balance


class _NoRetryPastTheMoney:
    """Turn a retryable failure into a **non**-retryable partial-write report.

    Wraps the statements that run **after** ``bump_version`` has moved the account balance. Up to
    that point a conflict means nothing applied and :func:`with_retries` may run the attempt again;
    past it, the money has moved, and re-running the attempt would move it a second time. So a
    conflict here is re-raised as :class:`PaperConcurrencyExhausted` - which
    :func:`with_retries` does not catch - carrying ``partial_write: True``.

    This is the honest shape of the missing ``ROLLBACK``. A transaction would have undone the
    account UPDATE; PostgREST cannot, so the incomplete state is **reported** instead of being
    hidden behind a retry. See the section header.
    """

    def __init__(self, operation: str, order_id: str, session_id: str) -> None:
        self.operation = operation
        self.order_id = order_id
        self.session_id = session_id

    def __enter__(self) -> "_NoRetryPastTheMoney":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if exc is None or not _is_retryable(exc):
            return False
        logger.error(
            "[paper-simulator] %s wrote the account balance for order %s and then failed (%s). "
            "PostgREST offers no ROLLBACK, so this is a PARTIAL WRITE and it is NOT retried - a "
            "retry would apply the money twice. Requirement 24.6's single transaction is not "
            "achievable over this transport.",
            self.operation,
            self.order_id,
            exc,
        )
        raise PaperConcurrencyExhausted(
            self.operation,
            1,
            phase="after_account_update",
            partial_write=True,
            details={"order_id": self.order_id, "session_id": self.session_id},
        ) from exc


def market_fill_event_id(order_id: Any) -> str:
    """The ``fill_event_id`` a market order's single fill carries.

    Deterministic and derived from the order alone, because a market order fills exactly once, on
    acceptance. That makes it replay-safe without a clock or a counter: replaying the session
    produces the same identity, ``uq_paper_fill_event`` recognises it, and the second application
    is the no-op Requirement 18.13 requires - rather than a second fill under a fresh ``uuid4``.
    """
    return f"market-{_require_session_text({'id': order_id}, 'id')}"


def resting_fill_event_id(order_id: Any, event: Any) -> str:
    """The ``fill_event_id`` a resting limit order's fill against ``event`` carries.

    ``sha256(order_id|event identity)``, hex, truncated to 32 characters and prefixed. Derived from
    the order and the **event** together, so:

    * the same order filled by the same event twice is one fill (Requirements 16.9, 18.13), which
      is what makes :func:`check_resting_orders` safe to run again on a replayed event stream; and
    * successive partial fills of one order against **different** events are different fills, so
      ``PARTIALLY_FILLED -> PARTIALLY_FILLED`` is representable (Requirement 16.2).

    The event identity is its ``source_event_id`` - which is what ``uq_paper_market_event``
    de-duplicates on and is therefore already the canonical identity of a validated event. A
    ``sequence`` or an ``event_timestamp`` is accepted as a fallback for a caller working from a
    stored row projection; an event with none of the three is refused rather than filled under a
    fabricated identity.
    """
    identity = (
        _event_text(event, "source_event_id")
        or _event_text(event, "sequence")
        or _event_text(event, "event_timestamp")
    )
    if identity is None:
        raise InvalidOrderIntent(
            "a resting-order fill needs the event's identity (source_event_id, sequence or "
            "event_timestamp) to build a deterministic fill_event_id; without one a replay would "
            "fill the same order twice (Requirements 16.9, 18.13)"
        )
    digest = hashlib.sha256(
        f"{_require_session_text({'id': order_id}, 'id')}|{identity}".encode("utf-8")
    ).hexdigest()
    return f"limit-{digest[:32]}"


# ══════════════════════════════════════════════════════════════════════════
# TASK 25.3 - ``submit_intent``
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SubmitOutcome:
    """What :func:`submit_intent` produces.

    ``order`` is always the persisted row, whatever happened: a returned duplicate, a persisted
    ``REJECTED`` order, or a fresh ``ACCEPTED`` one. Requirement 16.5 requires a rejection to
    **exist** as an order carrying its reason, so this type reports rejections as orders rather
    than as exceptions; a caller that would rather answer 4xx builds the error with
    :func:`order_invalid` or :class:`PaperInsufficientFunds`.
    """

    order: Mapping[str, Any]
    duplicate: bool = False
    rejection_reason: Optional[str] = None
    fingerprint: Optional[str] = None
    reference_price: Optional[Decimal] = None
    required_funds: Optional[Decimal] = None
    locked: Optional[Decimal] = None
    balance_event: Optional[Mapping[str, Any]] = None
    account: Optional[Mapping[str, Any]] = None
    fill: Optional[FillOutcome] = None
    attempts: int = 1

    @property
    def rejected(self) -> bool:
        """Whether the persisted order is ``REJECTED``."""
        return self.rejection_reason is not None

    @property
    def accepted(self) -> bool:
        """Whether a new order was accepted by this call."""
        return not self.duplicate and self.rejection_reason is None


async def submit_intent(
    supabase: Any,
    session: Mapping[str, Any],
    intent: Any,
    *,
    config: SessionConfig,
    account_id: Any,
    latest_event: Any = None,
    reference: Any = None,
    filled_at: Optional[datetime] = None,
    series_index: Any = 0,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> SubmitOutcome:
    """Turn one order intent into a persisted paper order (task 25.3).

    The five steps, in ``design.md``'s order, all against the account image read at the top of the
    attempt - which is this transport's substitute for ``SELECT ... FOR UPDATE`` (see the section
    header):

    1. **The idempotency probe.** An order already recorded under this key whose ``fingerprint``
       matches is returned **unchanged**: no second order, no balance movement (Requirement 16.8).
       A mismatch raises :class:`PaperIdempotencyConflict` (409) and leaves the recorded order
       alone (Requirement 16.15). The probe is ``paper_repository.probe_idempotency_key``, which
       reads inside the same scope every other statement here uses.
    2. **Static validation.** The first failing check of :func:`static_rejection_reason` produces a
       persisted order at ``REJECTED``, **from** ``CREATED``, carrying one of the eight names
       Requirement 16.5 enumerates. Balances and positions are untouched - two statements are
       issued, both against ``paper_orders``, and none against ``paper_accounts`` or
       ``paper_positions``.
    3. **The reference price.** A limit order's is its limit; a market order's is
       :func:`reference_price` of the latest validated event. When there is none the order is
       persisted ``REJECTED`` with ``NO_VALIDATED_PRICE`` and **nothing is synthesised,
       interpolated or extrapolated** (Requirement 14.9).
    4. **The funds check**, against the ``available_balance`` read in this attempt, using
       ``paper_accounting.required_funds`` (notional + fee allowance + slippage allowance,
       Requirement 16.6). Failure persists ``REJECTED`` with ``INSUFFICIENT_FUNDS`` and **locks
       nothing** - the lock is step 5, so "locked nothing" is a fact about the ordering.
    5. **Accept.** Insert at ``CREATED``, transition to ``ACCEPTED``, and for a **limit** order
       ``paper_accounting.lock(account, required)`` moving ``available -> locked``, written through
       ``bump_version`` under the version read in step 0 and recorded as an ``ORDER_LOCK`` balance
       event.

    THE FUNDS CHECK APPLIES TO BOTH SIDES, AND THAT IS A DELIBERATE READING
    ---------------------------------------------------------------------
    Requirement 16.6 and task 25.3 step 4 both say "IF an order's required funds exceed the
    Paper_Account's available balance" with **no** side qualifier, and step 5 says "for a limit
    order" lock the required funds - also with no side qualifier. Both are implemented as written.
    ``design.md``'s pseudocode narrows the check to ``intent.side = 'buy'``, and the retained
    ``paper_trading_service`` does the same (line 1320: ``if clean_side == "buy" and avail_bal <
    estimated_cost``). Those two readings differ, and the difference is stated rather than
    resolved silently: **on this path a sell also needs the available balance to cover its
    required funds**, where on the retained path it does not.

    The reason for choosing the requirement's wording over the pseudocode's is that they cannot
    both be had: locking ``required`` for a limit **sell** whose required funds exceed
    ``available_balance`` makes ``paper_accounting.lock`` raise ``InvariantViolation``
    (Requirement 18.4), so a buy-only funds check with an any-side lock is not implementable. The
    retained path is untouched and keeps its behaviour; if the spec means the narrower reading, the
    fix is one condition here plus narrowing the lock to buys, and it needs the spec's agreement.

    A MARKET ORDER'S FILL HAPPENS AFTER THE ACCEPT
    ----------------------------------------------
    Task 25.3: "a market order's ``apply_fill`` happens **after** the commit, so an accepted order
    is durable before it is filled". So :func:`apply_fill` is called *after* the accept path has
    returned, not inside it, and it runs its own attempt with its own guards and its own feed gate.
    If the fill then fails, the order stands at ``ACCEPTED`` - which is the durability the task
    asks for, and is why the failure is raised rather than swallowed.

    A market order also consults the feed gate **before** it is accepted, on the session row read
    in this attempt. A market order fills on acceptance, so accepting one while
    ``feed_state != 'HEALTHY'`` would create an order that cannot fill and would price it from a
    pre-disconnection reference (Requirement 14.5). A **limit** order is accepted regardless and
    simply rests: that is the difference ``paper_market_feed.admit_execution``'s docstring names
    ("refuse the market order and leave the resting limit order unfilled").

    Args:
        supabase: The caller's RLS-scoped Persistence_Layer handle.
        session: The ``paper_sessions`` row; ``id`` and ``user_id`` are read from it.
        intent: An :class:`OrderIntent` or the mapping :meth:`OrderIntent.from_mapping` reads.
        config: The session's frozen configuration (task 25.2).
        account_id: The session's Paper_Account.
        latest_event: The latest **validated** market event, for a market order's reference price.
        reference: An explicit reference price, overriding ``latest_event``. Must itself have come
            from a validated event; this function does not check its provenance and does not invent
            one when both are absent - it rejects with ``NO_VALIDATED_PRICE``.
        filled_at: The instant a market order's fill is recorded at. Required for a market order;
            this module reads no clock. Defaults to the event's ``event_timestamp`` when
            ``latest_event`` supplies one.
        series_index: The equity series a market fill's snapshot belongs to.
        sleep: The retry delay, for tests.

    Raises:
        PaperIdempotencyConflict, PaperOrderInvalid, PaperConcurrencyExhausted: as above.
        paper_market_feed.FeedNotHealthy: a market order on a feed that is not ``HEALTHY``.
    """
    resolved = OrderIntent.from_mapping(intent)
    user_id = _require_session_text(session, "user_id")
    session_id = _require_session_text(session, "id")
    aid = _require_session_text({"account_id": account_id}, "account_id")
    # Before the database, so ``chk_paper_order_idem_len`` stays a backstop (task 25.3).
    idempotency_key = validate_idempotency_key(resolved.idempotency_key)
    fingerprint = order_fingerprint(resolved)
    accounting = config.accounting()

    async def _reject(
        reason: str,
        *,
        attempt: int,
        reference_price_value: Optional[Decimal] = None,
        required: Optional[Decimal] = None,
    ) -> SubmitOutcome:
        """Persist the order at ``REJECTED`` **from** ``CREATED``, and change nothing else.

        Two statements, both on ``paper_orders``: the INSERT at ``CREATED`` and the UPDATE to
        ``REJECTED`` guarded by ``expected_state=CREATED``. Requirement 16.5's "SHALL make no
        change to the Paper_Account's balances or positions" therefore holds because no statement
        against either table is issued - not because one was issued and undone.

        Where the intent's own values violate a ``paper_orders`` CHECK constraint the row cannot
        exist at all, and this raises :class:`PaperOrderInvalid` (400) carrying the same reason
        instead. :func:`unrepresentable_order_constraint` documents which four of Requirement
        16.5's eight checks that applies to, and why the constraints were not relaxed.
        """
        blocked_by = unrepresentable_order_constraint(resolved)
        if blocked_by is not None:
            logger.warning(
                "[paper-simulator] order refused for session %s: %s. No REJECTED order row was "
                "written because %s refuses the intent's own values, so Requirement 16.5's "
                "persisted rejection is not representable for this check.",
                session_id,
                reason,
                blocked_by,
            )
            # Counted under the same reason as a persisted rejection: the intent WAS refused, and
            # a refusal rate that excluded the four unrepresentable checks would understate it.
            _record_order_rejected(reason)
            raise PaperOrderInvalid(
                reason,
                details={
                    "session_id": session_id,
                    "blocked_by": blocked_by,
                    "persisted": False,
                },
            )
        created = repo.insert_order(
            supabase,
            account_id=aid,
            user_id=user_id,
            session_id=session_id,
            symbol=resolved.symbol,
            side=resolved.side,
            order_type=resolved.order_type,
            quantity=resolved.quantity,
            limit_price=resolved.limit_price,
            reference_price=reference_price_value,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            signal_id=resolved.signal_id,
            order_state=PaperOrderState.CREATED,
        )
        rejected = repo.update_order(
            supabase,
            user_id=user_id,
            order_id=created["id"],
            order_state=PaperOrderState.REJECTED,
            expected_state=PaperOrderState.CREATED,
            rejection_reason=reason,
        )
        logger.info(
            "[paper-simulator] order rejected for session %s: %s (order %s)",
            session_id,
            reason,
            created["id"],
        )
        # `paper.order.rejected{reason}` (Requirement 26.6). Counted here, at the ONE place a
        # rejection is persisted, so the metric and `paper_orders.rejection_reason` cannot
        # disagree about how many rejections there were or what they were for. `reason` is one of
        # the closed vocabulary Requirement 16.5 enumerates.
        _record_order_rejected(reason)
        return SubmitOutcome(
            order=rejected,
            rejection_reason=reason,
            fingerprint=fingerprint,
            reference_price=reference_price_value,
            required_funds=required,
            attempts=attempt,
        )

    async def _attempt(number: int) -> SubmitOutcome:
        # ── step 0: the account image, with the version every later write is guarded by ──
        account_row = repo.lock_account_for_update(supabase, user_id, account_id=aid)

        # ── step 1: idempotency, in the same scope (Requirements 16.8, 16.15) ──
        if idempotency_key is not None:
            existing = repo.probe_idempotency_key(
                supabase,
                user_id=user_id,
                idempotency_key=idempotency_key,
                session_id=session_id,
                account_id=aid,
            )
            if existing is not None:
                recorded = str(existing.get("fingerprint") or "")
                if recorded != fingerprint:
                    logger.warning(
                        "[paper-simulator] idempotency key reused with different parameters on "
                        "session %s; order %s is unchanged (Requirement 16.15)",
                        session_id,
                        existing.get("id"),
                    )
                    raise PaperIdempotencyConflict(
                        idempotency_key,
                        fingerprint=fingerprint,
                        recorded_fingerprint=recorded,
                        order_id=existing.get("id"),
                        details={"session_id": session_id},
                    )
                return SubmitOutcome(
                    order=existing,
                    duplicate=True,
                    rejection_reason=_optional_reason(existing),
                    fingerprint=fingerprint,
                    attempts=number,
                )

        # ── step 2: static validation -> a persisted REJECTED order (Requirement 16.5) ──
        failure = static_rejection_reason(resolved, config)
        if failure is not None:
            return await _reject(failure, attempt=number)

        # ── step 3: the reference price. Never synthesised (Requirement 14.9). ──
        if resolved.limit_price is not None:
            ref = accounting.price(resolved.limit_price)
        elif reference is not None:
            ref = accounting.price(reference)
        else:
            derived = reference_price(latest_event, resolved.side)
            ref = None if derived is None else accounting.price(derived)
        if ref is None or ref <= _ZERO:
            return await _reject(REJECTION_NO_VALIDATED_PRICE, attempt=number)

        # ── step 4: funds, on the balance read in THIS attempt (Requirement 16.6) ──
        account = account_of(account_row)
        required = required_funds(resolved, ref, accounting)
        if required > account.available_balance:
            return await _reject(
                REJECTION_INSUFFICIENT_FUNDS,
                attempt=number,
                reference_price_value=ref,
                required=required,
            )

        # ── the feed gate, for a market order only (see the docstring) ──
        if resolved.order_type == "market":
            session_row = repo.read_session(supabase, user_id, session_id)
            if session_row is None:
                raise InvalidOrderIntent(
                    f"no paper session {session_id} is readable for this identity, so the feed "
                    "state that decides whether a market order may be accepted cannot be read; "
                    "nothing was written"
                )
            admit_execution(session_row)

        # ── step 5: accept ──
        created = repo.insert_order(
            supabase,
            account_id=aid,
            user_id=user_id,
            session_id=session_id,
            symbol=resolved.symbol,
            side=resolved.side,
            order_type=resolved.order_type,
            quantity=resolved.quantity,
            limit_price=resolved.limit_price,
            reference_price=ref,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            signal_id=resolved.signal_id,
            order_state=PaperOrderState.CREATED,
        )
        accepted = repo.update_order(
            supabase,
            user_id=user_id,
            order_id=created["id"],
            order_state=PaperOrderState.ACCEPTED,
            expected_state=PaperOrderState.CREATED,
        )

        locked_amount: Optional[Decimal] = None
        account_after: Optional[Mapping[str, Any]] = None
        balance_event: Optional[Mapping[str, Any]] = None
        if resolved.order_type == "limit":
            locked_amount, account_after, balance_event = await _lock_for_order(
                supabase,
                user_id=user_id,
                session_id=session_id,
                account_id=aid,
                order_id=str(accepted["id"]),
                required=required,
                config=config,
                occurred_at=filled_at or _event_instant(latest_event),
                sleep=sleep,
            )

        logger.info(
            "[paper-simulator] order %s accepted on session %s (%s %s %s @ ref %s, locked %s)",
            accepted["id"],
            session_id,
            resolved.side,
            resolved.quantity,
            resolved.symbol,
            ref,
            locked_amount if locked_amount is not None else "none",
        )
        return SubmitOutcome(
            order=accepted,
            fingerprint=fingerprint,
            reference_price=ref,
            required_funds=required,
            locked=locked_amount,
            account=account_after,
            balance_event=balance_event,
            attempts=number,
        )

    # `paper.order.submit_latency_ms` (Requirements 26.6, 27.6): the submission proper, from
    # intent to persisted order, and NOT the market fill that follows it - that is
    # `paper.fill.apply_latency_ms`, measured inside `apply_fill`. Two figures rather than one,
    # because they answer different questions and folding them together would make an accepted
    # limit order and a filled market order indistinguishable on the dashboard.
    _submit_measured_from = time.perf_counter()
    try:
        outcome = await with_retries(
            "submit_intent",
            _attempt,
            sleep=sleep,
            details={"session_id": session_id, "fingerprint": fingerprint},
        )
    finally:
        _record_order_submitted(
            (time.perf_counter() - _submit_measured_from) * 1000.0
        )

    # A market order's fill runs AFTER the accept path has returned, so the accepted order is
    # durable before it is filled (task 25.3). ``apply_fill`` opens its own attempt, re-reads the
    # session row and consults the feed gate again.
    if outcome.accepted and resolved.order_type == "market":
        instant = filled_at or _event_instant(latest_event)
        if instant is None:
            raise InvalidOrderIntent(
                "a market order's fill needs an instant: pass filled_at, or a latest_event "
                "carrying event_timestamp. This module reads no clock, because a clock read would "
                "make a replay diverge (Requirement 15.4)."
            )
        reference_value = outcome.reference_price
        fill = await apply_fill(
            supabase,
            session,
            outcome.order,
            config=config,
            quantity=resolved.quantity,
            price=market_fill_price(reference_value, resolved.side, config),
            fill_event_id=market_fill_event_id(outcome.order["id"]),
            filled_at=instant,
            reference=reference_value,
            # The event's OWN identity, not the ``paper_market_events`` primary key - see
            # ``apply_fill``'s ``market_event_id`` argument for why that is the identity this
            # column holds. ``None`` only when the caller priced the order from an explicit
            # ``reference`` rather than from an event, which is the one case with no event to name.
            market_event_id=_event_text(latest_event, "source_event_id"),
            series_index=series_index,
            sleep=sleep,
        )
        return SubmitOutcome(
            order=fill.order,
            fingerprint=outcome.fingerprint,
            reference_price=outcome.reference_price,
            required_funds=outcome.required_funds,
            locked=outcome.locked,
            account=fill.account or outcome.account,
            balance_event=fill.balance_event or outcome.balance_event,
            fill=fill,
            attempts=outcome.attempts,
        )
    return outcome


def _optional_reason(order_row: Mapping[str, Any]) -> Optional[str]:
    """The ``rejection_reason`` of a returned duplicate, or ``None``.

    A duplicate is returned "with its current Paper_Order_State" (Requirement 16.8), and that state
    may well be ``REJECTED`` - the first request's order was persisted rejected and the retry gets
    that same order back. Reporting the reason keeps :attr:`SubmitOutcome.rejected` honest for it.
    """
    if str(order_row.get("order_state") or "") != PaperOrderState.REJECTED.value:
        return None
    reason = order_row.get("rejection_reason")
    return None if reason is None else str(reason)


def _event_instant(event: Any) -> Optional[datetime]:
    """The market instant of a validated event, or ``None``.

    ``event_timestamp`` - the instant the candle opened, which
    ``paper_market_feed.next_validated_event`` recorded - and never ``received_at``: the processing
    time is a property of this worker, so a replay on another machine would write a different one
    and Requirement 15.4's byte-identical replay would fail.
    """
    if event is None:
        return None
    value = (
        event.get("event_timestamp")
        if isinstance(event, Mapping)
        else getattr(event, "event_timestamp", None)
    )
    return _instant_of(value)


async def _lock_for_order(
    supabase: Any,
    *,
    user_id: str,
    session_id: str,
    account_id: str,
    order_id: str,
    required: Decimal,
    config: SessionConfig,
    occurred_at: Optional[datetime],
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> Tuple[Decimal, Mapping[str, Any], Mapping[str, Any]]:
    """Move ``required`` from ``available_balance`` to ``locked_balance`` for a resting order.

    ``paper_accounting.lock`` does the arithmetic and refuses a lock that would drive
    ``available_balance`` below zero (Requirement 18.4) **before** the move, so a refused lock
    leaves the in-memory account untouched and no statement is issued. This function writes the
    result under the version it just read and appends the ``ORDER_LOCK`` ledger row.

    ``total_equity`` is not written, and that is not an omission: a lock moves cash between two
    columns whose **sum** is one term of Requirement 18.3's identity, so the identity is unchanged
    by construction and rewriting the stored total would be a second computation of a figure that
    did not move.

    WHY THIS HAS ITS OWN RETRY LOOP
    -------------------------------
    It runs *after* the order is durable at ``ACCEPTED``. Retrying the enclosing
    :func:`submit_intent` attempt would re-run the idempotency probe, find that order, and return
    it as a duplicate - leaving an accepted limit order with no funds locked. So the lock re-reads
    the account and retries **in place**, through the same shared :func:`with_retries`. If all
    three attempts conflict, :class:`PaperConcurrencyExhausted` names ``phase="ORDER_LOCK"`` and
    ``partial_write=True``: the order stands at ``ACCEPTED`` with nothing locked, which is visible
    in the response rather than silent, and which ``paper_accounting.apply_fill`` will refuse
    later if the funds are then not there (Requirement 18.4).
    """
    accounting = config.accounting()

    async def _attempt(number: int) -> Tuple[Decimal, Mapping[str, Any], Mapping[str, Any]]:
        account_row = repo.lock_account_for_update(supabase, user_id, account_id=account_id)
        account = account_of(account_row)
        try:
            locked = accounting_lock(account, required, accounting)
        except InvariantViolation as exc:
            logger.error(
                "[paper-simulator] the ORDER_LOCK for order %s was refused by the %s invariant: "
                "%s",
                order_id,
                exc.invariant,
                exc,
            )
            raise PaperInvariantViolation(
                exc.invariant,
                phase="ORDER_LOCK",
                details={"order_id": order_id, "session_id": session_id},
            ) from exc

        amount = accounting.money(required)
        account_after = repo.bump_version(
            supabase,
            user_id=user_id,
            account_id=account_id,
            expected_version=account_row["version"],
            payload={
                "available_balance": locked.available_balance,
                "locked_balance": locked.locked_balance,
            },
        )
        with _NoRetryPastTheMoney("submit_intent.lock", order_id, session_id):
            balance_event = repo.insert_balance_event(
                supabase,
                account_id=account_id,
                user_id=user_id,
                session_id=session_id,
                cause="ORDER_LOCK",
                available_delta=-amount,
                locked_delta=amount,
                realized_delta=_ZERO,
                available_after=locked.available_balance,
                locked_after=locked.locked_balance,
                realized_after=locked.realized_pnl,
                occurred_at=occurred_at or _instant_of(account_row.get("updated_at")),
            )
        return (amount, account_after, balance_event)

    try:
        return await with_retries(
            "submit_intent.lock",
            _attempt,
            sleep=sleep,
            details={"order_id": order_id, "session_id": session_id},
        )
    except PaperConcurrencyExhausted as exc:
        raise PaperConcurrencyExhausted(
            "submit_intent.lock",
            exc.attempts,
            phase="ORDER_LOCK",
            partial_write=True,
            details={
                "order_id": order_id,
                "session_id": session_id,
                "note": (
                    "the order is durable at ACCEPTED and no funds were locked; PostgREST offers "
                    "no ROLLBACK for the two statements that already committed"
                ),
            },
        ) from exc


# ══════════════════════════════════════════════════════════════════════════
# TASK 25.5 - ``check_resting_orders``: THE ONE CALLER FOR LIMIT ORDERS
# ══════════════════════════════════════════════════════════════════════════


async def check_resting_orders(
    supabase: Any,
    session: Mapping[str, Any],
    event: Any,
    *,
    config: SessionConfig,
    account_id: Any,
    series_index: Any = 0,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> List[FillOutcome]:
    """Fill every resting limit order this validated event triggers. Routes through :func:`apply_fill`.

    Task 25.5: "``check_resting_orders(session, event)`` is the one caller for limit orders and
    routes through ``apply_fill``; it does not write a fill itself." That is literal - this function
    issues exactly one statement of its own, the read that finds the resting orders. Every fill,
    every balance movement, every position write, every state transition and every equity point
    comes from :func:`apply_fill`, which means the four guards, the feed gate, the invariant assert
    and the retry policy apply to a resting fill exactly as they do to a market fill. There is no
    second write path.

    WHICH ORDERS
    ------------
    ``legacy_status='OPEN'`` on the session's account, which is exactly ``ACCEPTED`` and
    ``PARTIALLY_FILLED`` by ``paper_order_state.LEGACY_STATUS_FOR_STATE`` - the two states a
    resting order can be in, and the two Requirement 16.2 admits a fill from. Filtered in process
    to ``order_type == 'limit'`` and to the event's own symbol, then ordered oldest-first so two
    orders competing for one event's volume are served in the order they were accepted rather than
    in whatever order the read returned.

    HOW MUCH
    --------
    :func:`fillable_quantity` - the deterministic participation cap - and never a probability. The
    cap is computed per order against the **same** event, so an event whose volume is small does
    not fill every resting order in full; it is not decremented across orders, because the design's
    cap is per order and inventing a shared budget would be a rule the spec does not state. That is
    recorded as a known simplification rather than hidden.

    AT WHAT PRICE
    -------------
    Exactly the limit (:func:`limit_fill_price`). No slippage in either direction, so
    ``slippage_minor`` on a resting fill is zero - the reference passed to :func:`apply_fill` is the
    limit itself.

    REPLAY SAFETY
    -------------
    The ``fill_event_id`` is :func:`resting_fill_event_id`, derived from the order and the event's
    own identity. Running this function again on the same event - a replay, a redelivery, a second
    worker - produces the same identity, so ``uq_paper_fill_event`` makes the second run the no-op
    Requirements 16.9 and 18.13 require.

    Returns:
        One :class:`FillOutcome` per order this event triggered, in the order they were applied.
        ``[]`` when nothing triggered, which is the common case for a candle.
    """
    user_id = _require_session_text(session, "user_id")
    session_id = _require_session_text(session, "id")
    aid = _require_session_text({"account_id": account_id}, "account_id")
    instant = _event_instant(event)
    if instant is None:
        raise InvalidOrderIntent(
            "a resting-order fill needs the event's own event_timestamp; this module reads no "
            "clock, because a clock read would make a replay diverge (Requirement 15.4)"
        )
    symbol = _event_text(event, "symbol")

    resting = repo.get_orders(
        supabase,
        user_id,
        account_id=aid,
        session_id=session_id,
        legacy_status="OPEN",
    )
    candidates = [
        row
        for row in resting
        if str(row.get("order_type") or "").strip().lower() == "limit"
        and (symbol is None or str(row.get("symbol")) == symbol)
        and limit_fill_triggered(row, event)
    ]
    candidates.sort(key=lambda row: str(row.get("created_at") or ""))

    outcomes: List[FillOutcome] = []
    for row in candidates:
        quantity = fillable_quantity(row, event, config)
        if quantity <= _ZERO:
            continue
        price = limit_fill_price(row, config)
        outcomes.append(
            await apply_fill(
                supabase,
                session,
                row,
                config=config,
                quantity=quantity,
                price=price,
                fill_event_id=resting_fill_event_id(row["id"], event),
                filled_at=instant,
                reference=price,
                # The same identity the ``fill_event_id`` above is derived from, so the fill's
                # provenance and its de-duplication key name the same event.
                market_event_id=_event_text(event, "source_event_id"),
                series_index=series_index,
                sleep=sleep,
            )
        )
    return outcomes


__all__ += [
    # 25.6 - the shared retry path
    "RETRY_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS",
    "retry_backoff_seconds",
    "with_retries",
    # the catalogue-coded refusals
    "PaperConcurrencyExhausted",
    "PaperIdempotencyConflict",
    "PaperInsufficientFunds",
    "PaperInvariantViolation",
    "PaperOrderInvalid",
    "PaperOverFill",
    "order_invalid",
    # 25.3 - the intent, its fingerprint, its validation
    "DEFAULT_TIME_IN_FORCE",
    "FINGERPRINT_FIELDS",
    "IDEMPOTENCY_KEY_MAX_CHARS",
    "InvalidOrderIntent",
    "OrderIntent",
    "REJECTION_INSUFFICIENT_FUNDS",
    "REJECTION_NO_VALIDATED_PRICE",
    "REJECTION_REASONS",
    "ORDER_COLUMN_CONSTRAINTS",
    "STATIC_REJECTION_REASONS",
    "SubmitOutcome",
    "decimal_places",
    "order_fingerprint",
    "unrepresentable_order_constraint",
    "static_rejection_reason",
    "submit_intent",
    "validate_idempotency_key",
    # 25.4 - the single write path
    "FILL_APPLIED",
    "FILL_DUPLICATE",
    "FILL_TERMINAL",
    "FillOutcome",
    "account_of",
    "apply_fill",
    "position_of",
    "positions_of",
    # 25.5 - the deterministic fill model
    "REFERENCE_FIELD_FOR_SIDE",
    "TRIGGER_FIELDS_FOR_SIDE",
    "check_resting_orders",
    "fee_amount",
    "fillable_quantity",
    "limit_fill_price",
    "limit_fill_triggered",
    "market_fill_event_id",
    "market_fill_price",
    "reference_price",
    "remaining_quantity",
    "resting_fill_event_id",
    "slippage_amount",
]
