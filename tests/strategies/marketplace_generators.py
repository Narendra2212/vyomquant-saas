"""Shared Hypothesis generators for the marketplace half of the property suite.

Feature: marketplace-subscriptions-paper-trading
Task 2.1 — _Requirements: 29.3_
Design reference: ``design.md § Testing Strategy → Property-based testing configuration``.

Exported generators
-------------------
``minor_amounts()``
    Integer Minor_Unit amounts over ``0 … 99_999_999_999`` with the boundaries
    ``0, 1, 99, 100, 101`` and the maximum explicitly in the pool.
``utc_instants()``
    Timezone-aware UTC instants with 31 Jan, 28/29 Feb, 30 Apr, 30 Nov and
    31 Dec of both leap and non-leap years explicitly in the pool.
``backtest_conditions()``
    One ``strategy_backtests`` row as a ``dict``, keyed by the real column names
    of the reconciled table (``backend_app/migrations/001_strategy_architecture.sql``
    line 118 plus the nine additive columns of migration ``006`` — task 11.1).
``evidence_sets()``
    A list of 3…10 ``backtest_conditions()`` rows that is either admissible under
    ``evidence_validator`` (one ``version_id``, pairwise-distinct windows, pairwise
    different ``dataset_checksum``) or deliberately defective.
``listing_rows()``
    One ``library_strategies`` row as a ``dict``, populating **every** member of
    ``listing_projection.DENIED_LISTING_COLUMNS`` with a non-null value.
``protected_logic_strategies()``
    ``(strategy_row, version_row, backtest_rows)`` whose node ids, indicator
    names, parameter names, threshold values and model path segments are all
    generated strings of at least three characters.
``tenant_pairs()``
    Two distinct caller identities plus a set of never-created identifiers, for
    the cross-tenant matrix (P-41 … P-46).

Row shapes are plain ``dict`` objects keyed by database column name, because that
is what the Supabase client hands the modules under test. Money and ratio values
are ``Decimal`` — never ``float`` — and dates are ``datetime.date`` so that
``(end_date - start_date).days`` is exact integer arithmetic
(Requirements 8.13, 10.3, 3.5).

DENIED_LISTING_COLUMNS coupling (read this before editing task 8.1)
------------------------------------------------------------------
``listing_rows()`` must populate every member of
``backend_app.backend.marketplace.listing_projection.DENIED_LISTING_COLUMNS``,
which task 8.1 creates. Until that module exists this file falls back to
``FALLBACK_DENIED_LISTING_COLUMNS`` below — transcribed verbatim from
``design.md § marketplace/listing_projection.py``.

The resolution is lazy and per-call (``denied_listing_columns()``), so the moment
task 8.1 lands, this generator follows the production constant rather than the
copy. Task 8.1 must therefore either keep its constant equal to the fallback or
update the fallback in the same change; ``assert_denied_columns_in_sync()`` is
provided so ``tests/test_listing_projection.py`` (task 8.3) can fail loudly if the
two ever disagree. A column that is in the production set but not in
``_DENIED_COLUMN_VALUES`` still gets a non-null generated value from
``_generic_denied_value()``, so an added denied column can never silently arrive
as ``None`` and make the allow-list property vacuous.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

__all__ = [
    "MAX_AMOUNT_MINOR",
    "MINOR_AMOUNT_BOUNDARIES",
    "CURRENCIES",
    "DATASETS",
    "LEAP_YEARS",
    "NON_LEAP_YEARS",
    "CALENDAR_BOUNDARY_MONTH_DAYS",
    "FALLBACK_DENIED_LISTING_COLUMNS",
    "MIN_CONDITIONS",
    "MAX_CONDITIONS",
    "MIN_WINDOW_DAYS",
    "MIN_TRADES",
    "MIN_BARS",
    "denied_listing_columns",
    "assert_denied_columns_in_sync",
    "identifiers",
    "tokens",
    "minor_amounts",
    "utc_instants",
    "backtest_conditions",
    "evidence_sets",
    "listing_rows",
    "protected_logic_strategies",
    "tenant_pairs",
    "CONDITION_DEFECTS",
    "EVIDENCE_SET_DEFECTS",
    "TENANT_RESOURCE_KINDS",
    "ProtectedLogicFixture",
    "TenantPair",
]


# ---------------------------------------------------------------------------
# Constants mirrored from the design (kept in one place per module)
# ---------------------------------------------------------------------------

#: ``money.MAX_AMOUNT_MINOR`` (design.md § marketplace/money.py, task 4.1).
MAX_AMOUNT_MINOR = 99_999_999_999

#: The boundaries Requirement 29.3 / task 2.1 require in the pool explicitly.
MINOR_AMOUNT_BOUNDARIES: Tuple[int, ...] = (0, 1, 99, 100, 101, MAX_AMOUNT_MINOR)

#: The two currencies with a declared Minor_Unit exponent (design § money.py).
CURRENCIES: Tuple[str, ...] = ("USD", "INR")

#: Dataset identifiers used by ``strategy_backtests.dataset``. A small pool makes
#: dataset collisions (and therefore the window-overlap branch of ``distinct``)
#: reachable, which a free-text generator would make vanishingly unlikely.
DATASETS: Tuple[str, ...] = (
    "BTC/USDT-1h",
    "ETH/USDT-1h",
    "BTC/USDT-4h",
    "SOL/USDT-15m",
    "AAPL-1d",
    "NIFTY50-1d",
)

#: ``evidence_validator.THRESHOLDS`` (design § marketplace/evidence_validator.py).
#: Duplicated here only so the generators can straddle each boundary; the
#: production constants remain the single source of truth for the assertions.
MIN_CONDITIONS = 3
MAX_CONDITIONS = 10
MIN_WINDOW_DAYS = 90
MIN_TRADES = 20
MIN_BARS = 50

LEAP_YEARS: Tuple[int, ...] = (2000, 2004, 2020, 2024, 2028, 2032)
#: 2100 is deliberately present: divisible by 100, not by 400, therefore *not* a
#: leap year, which is where a naive ``year % 4`` implementation breaks.
NON_LEAP_YEARS: Tuple[int, ...] = (2019, 2021, 2022, 2023, 2025, 2026, 2100)

#: 31 Jan, 28 Feb, 29 Feb, 30 Apr, 30 Nov, 31 Dec — the month-length edges a
#: calendar-month roll has to clamp (Requirement 11.4, P-12).
CALENDAR_BOUNDARY_MONTH_DAYS: Tuple[Tuple[int, int], ...] = (
    (1, 31),
    (2, 28),
    (2, 29),
    (4, 30),
    (11, 30),
    (12, 31),
)

#: Clock times worth hitting exactly: midnight, noon, the last microsecond of a
#: day, and the two second/microsecond edges either side of it.
_BOUNDARY_TIMES: Tuple[time, ...] = (
    time(0, 0, 0, 0),
    time(0, 0, 0, 1),
    time(12, 0, 0, 0),
    time(23, 59, 59, 0),
    time(23, 59, 59, 999_999),
)

#: Transcribed verbatim from ``design.md § marketplace/listing_projection.py``.
#: See the module docstring for the coupling contract with task 8.1.
FALLBACK_DENIED_LISTING_COLUMNS = frozenset(
    {
        "author_id",
        "source_strategy_id",
        "moderated_by",
        "moderation_notes",
        "moderated_at",
        "deployment_requirements",
        "version_history",
        "evaluation_score",
        "equity_curve_snapshot",
        "has_ml_model",
        "node_count",
        "risk_stop_loss_pct",
        "risk_take_profit_pct",
        "risk_max_position_size",
        "risk_max_drawdown_pct",
        "is_active",
        "moderation_status",
        "updated_at",
    }
)


def denied_listing_columns() -> frozenset:
    """Return the denied-column set, preferring the production constant.

    Resolved lazily on every call so that the generators follow
    ``listing_projection.DENIED_LISTING_COLUMNS`` as soon as task 8.1 lands,
    without this module importing production code at collection time.
    """
    try:  # pragma: no cover - the branch taken depends on task 8.1 landing
        from backend_app.backend.marketplace import listing_projection  # type: ignore
    except Exception:
        return FALLBACK_DENIED_LISTING_COLUMNS
    produced = getattr(listing_projection, "DENIED_LISTING_COLUMNS", None)
    if not produced:
        return FALLBACK_DENIED_LISTING_COLUMNS
    return frozenset(produced)


def assert_denied_columns_in_sync() -> None:
    """Fail if the production denied set and the fallback have drifted apart.

    Called by ``tests/test_listing_projection.py`` (task 8.3). It is a no-op
    while ``listing_projection`` does not exist yet.
    """
    try:  # pragma: no cover - see denied_listing_columns()
        from backend_app.backend.marketplace import listing_projection  # type: ignore
    except Exception:
        return
    produced = getattr(listing_projection, "DENIED_LISTING_COLUMNS", None)
    if produced is None:
        return
    produced = frozenset(produced)
    missing = FALLBACK_DENIED_LISTING_COLUMNS - produced
    extra = produced - FALLBACK_DENIED_LISTING_COLUMNS
    assert not missing and not extra, (
        "listing_projection.DENIED_LISTING_COLUMNS and "
        "tests/strategies/marketplace_generators.FALLBACK_DENIED_LISTING_COLUMNS "
        f"disagree: only in the fallback={sorted(missing)}, "
        f"only in production={sorted(extra)}. Update both in the same change."
    )


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------


def _resolve(draw, given: Any, fallback: SearchStrategy):
    """Accept a concrete value, a strategy or ``None`` for a generator argument."""
    if given is None:
        return draw(fallback)
    if isinstance(given, SearchStrategy):
        return draw(given)
    return given


def identifiers() -> SearchStrategy:
    """UUID identifiers as text, the form every router and table uses."""
    return st.uuids().map(str)


def tokens(min_size: int = 3, max_size: int = 16) -> SearchStrategy:
    """Identifier-like strings of at least ``min_size`` characters.

    The three-character floor is the same one
    ``listing_projection.protected_logic_tokens`` applies, so a token this
    generator produces is never discarded by the containment assertion
    (design § Mechanical Protected_Logic containment).
    """
    assert min_size >= 3, "the Protected_Logic token floor is three characters"
    return st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
        min_size=min_size,
        max_size=max_size,
    )


def _hex_tokens(size: int = 32) -> SearchStrategy:
    return st.text(alphabet="0123456789abcdef", min_size=size, max_size=size)


def _money(min_value: str, max_value: str, places: int = 2) -> SearchStrategy:
    return st.decimals(
        min_value=Decimal(min_value),
        max_value=Decimal(max_value),
        places=places,
        allow_nan=False,
        allow_infinity=False,
    )


# ---------------------------------------------------------------------------
# minor_amounts
# ---------------------------------------------------------------------------


def minor_amounts(
    min_value: int = 0, max_value: int = MAX_AMOUNT_MINOR
) -> SearchStrategy:
    """Integer Minor_Unit amounts, boundaries explicitly in the pool.

    ``0, 1, 99, 100, 101`` and ``MAX_AMOUNT_MINOR`` are drawn from a
    ``sampled_from`` branch, so the 90/10 split's rounding edges are hit on
    nearly every run rather than left to chance over an 11-digit range
    (task 2.1, Requirement 29.3).
    """
    assert 0 <= min_value <= max_value <= MAX_AMOUNT_MINOR
    branches: List[SearchStrategy] = [
        st.integers(min_value=min_value, max_value=max_value)
    ]
    pool = [b for b in MINOR_AMOUNT_BOUNDARIES if min_value <= b <= max_value]
    if pool:
        branches.append(st.sampled_from(pool))
    return st.one_of(*branches)


# ---------------------------------------------------------------------------
# utc_instants
# ---------------------------------------------------------------------------


def _boundary_dates(min_year: int, max_year: int) -> List[date]:
    out: List[date] = []
    for year in sorted(set(LEAP_YEARS + NON_LEAP_YEARS)):
        if not (min_year <= year <= max_year):
            continue
        for month, day in CALENDAR_BOUNDARY_MONTH_DAYS:
            if day > calendar.monthrange(year, month)[1]:
                continue  # 29 Feb in a non-leap year
            out.append(date(year, month, day))
        # The day before and after each boundary keeps "one day off" bugs visible.
        out.append(date(year, 3, 1))
        out.append(date(year, 12, 1))
    return out


def utc_instants(min_year: int = 2000, max_year: int = 2100) -> SearchStrategy:
    """Timezone-aware UTC instants, month-length boundaries in the pool.

    The pool carries 31 Jan, 28 Feb, 29 Feb, 30 Apr, 30 Nov and 31 Dec of both
    leap and non-leap years (including 2100, which is divisible by 100 and not by
    400), combined with clock-time edges, so ``add_one_calendar_month`` is
    exercised on every clamping case (Requirement 11.4, P-12).
    """
    pool = _boundary_dates(min_year, max_year)
    branches: List[SearchStrategy] = [
        st.datetimes(
            min_value=datetime(min_year, 1, 1),
            max_value=datetime(max_year, 12, 31, 23, 59, 59, 999_999),
            timezones=st.just(timezone.utc),
        )
    ]
    if pool:
        clock = st.one_of(st.sampled_from(_BOUNDARY_TIMES), st.times())
        branches.append(
            st.builds(
                lambda day, clock_time: datetime.combine(
                    day, clock_time, tzinfo=timezone.utc
                ),
                st.sampled_from(pool),
                clock,
            )
        )
    return st.one_of(*branches)


# ---------------------------------------------------------------------------
# backtest_conditions
# ---------------------------------------------------------------------------

#: The ways one condition row can fail ``evidence_validator``'s per-condition
#: criteria. Named so a shrunk counterexample says which criterion it targets.
CONDITION_DEFECTS: Tuple[str, ...] = (
    "not_completed",
    "no_completed_at",
    "error_message",
    "null_parameter",
    "short_window",
    "too_few_trades",
    "null_bar_count",
    "too_few_bars",
    "null_version_id",
    "null_metric",
    "foreign_owner",
)

_NULLABLE_PARAMETERS: Tuple[str, ...] = (
    "dataset",
    "start_date",
    "end_date",
    "initial_capital",
    "commission",
    "slippage",
    "dataset_checksum",
    "dag_hash",
)

_METRIC_COLUMNS: Tuple[str, ...] = (
    "total_return_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "total_trades",
    "final_capital",
)


@st.composite
def backtest_conditions(  # noqa: C901 - one flat row builder, deliberately explicit
    draw,
    *,
    owner_id: Any = None,
    strategy_id: Any = None,
    version_id: Any = None,
    dataset: Any = None,
    start_date: Any = None,
    window_days: Any = None,
    dataset_checksum: Any = None,
    satisfying: Optional[bool] = True,
    defects: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """One ``strategy_backtests`` row, keyed by column name.

    ``satisfying=True`` (the default) produces a row that passes every
    per-condition criterion in ``evidence_validator.validate``: ``status
    'completed'``, a non-null ``completed_at``, an empty ``error_message``, all
    eight reproducibility parameters non-null, a window of at least
    ``MIN_WINDOW_DAYS`` inclusive calendar days, ``total_trades >= MIN_TRADES``,
    ``executed_bar_count >= MIN_BARS`` and a non-null ``version_id``.

    ``satisfying=False`` injects at least one defect from ``CONDITION_DEFECTS``;
    ``satisfying=None`` draws the choice. ``defects`` pins the defect set
    explicitly, which is how an error-condition property targets one criterion.

    Money and ratio columns are ``Decimal``; ``start_date`` / ``end_date`` are
    ``datetime.date``; ``completed_at`` is a timezone-aware UTC ``datetime``.
    """
    if defects is not None:
        chosen = tuple(defects)
        for defect in chosen:
            assert defect in CONDITION_DEFECTS, f"unknown defect {defect!r}"
    else:
        if satisfying is None:
            satisfying = draw(st.booleans())
        if satisfying:
            chosen = ()
        else:
            chosen = tuple(
                draw(
                    st.lists(
                        st.sampled_from(CONDITION_DEFECTS),
                        min_size=1,
                        max_size=3,
                        unique=True,
                    )
                )
            )

    resolved_owner = _resolve(draw, owner_id, identifiers())
    resolved_strategy = _resolve(draw, strategy_id, identifiers())
    resolved_version = _resolve(draw, version_id, identifiers())
    resolved_dataset = _resolve(draw, dataset, st.sampled_from(DATASETS))

    span = _resolve(
        draw,
        window_days,
        st.integers(min_value=MIN_WINDOW_DAYS, max_value=400),
    )
    if "short_window" in chosen:
        span = draw(st.integers(min_value=1, max_value=MIN_WINDOW_DAYS - 1))
    begins = _resolve(
        draw,
        start_date,
        st.dates(min_value=date(2015, 1, 1), max_value=date(2030, 6, 30)),
    )
    ends = begins + timedelta(days=span - 1)

    trades = draw(st.integers(min_value=MIN_TRADES, max_value=5_000))
    if "too_few_trades" in chosen:
        trades = draw(st.integers(min_value=0, max_value=MIN_TRADES - 1))

    bars = draw(st.integers(min_value=MIN_BARS, max_value=200_000))
    if "too_few_bars" in chosen:
        bars = draw(st.integers(min_value=0, max_value=MIN_BARS - 1))
    if "null_bar_count" in chosen:
        bars = None

    row: Dict[str, Any] = {
        "id": draw(identifiers()),
        "user_id": resolved_owner,
        "strategy_id": resolved_strategy,
        "version_id": resolved_version,
        "version": f"v{draw(st.integers(min_value=1, max_value=40))}",
        "status": "completed",
        "error_message": None,
        "completed_at": datetime.combine(ends, time(0, 0), tzinfo=timezone.utc)
        + timedelta(minutes=draw(st.integers(min_value=1, max_value=2_000))),
        "created_at": datetime.combine(begins, time(0, 0), tzinfo=timezone.utc),
        "dataset": resolved_dataset,
        "start_date": begins,
        "end_date": ends,
        "initial_capital": draw(_money("1000.00", "10000000.00")),
        "commission": draw(_money("0.000000", "0.010000", places=6)),
        "slippage": draw(_money("0.000000", "0.010000", places=6)),
        "dataset_checksum": _resolve(draw, dataset_checksum, _hex_tokens(64)),
        "dag_hash": draw(_hex_tokens(64)),
        "engine_version": draw(st.sampled_from(("1.0.0", "1.1.0", "2.0.0", "2.3.1"))),
        "schema_version": draw(st.sampled_from(("2.0", "2.1"))),
        "executed_bar_count": bars,
        "total_trades": trades,
        "total_return_pct": draw(_money("-99.9999", "999.9999", places=4)),
        "sharpe_ratio": draw(_money("-9.9999", "9.9999", places=4)),
        "sortino_ratio": draw(_money("-9.9999", "9.9999", places=4)),
        "max_drawdown": draw(_money("0.0000", "99.9999", places=4)),
        "win_rate": draw(_money("0.0000", "1.0000", places=4)),
        "profit_factor": draw(_money("0.0000", "9.9999", places=4)),
        "final_capital": draw(_money("0.00", "50000000.00")),
        "blueprint": {"nodes": [], "edges": []},
    }

    if "not_completed" in chosen:
        row["status"] = draw(st.sampled_from(("pending", "running", "failed")))
    if "no_completed_at" in chosen:
        row["completed_at"] = None
    if "error_message" in chosen:
        row["error_message"] = draw(tokens(min_size=5, max_size=40))
    if "null_parameter" in chosen:
        row[draw(st.sampled_from(_NULLABLE_PARAMETERS))] = None
    if "null_version_id" in chosen:
        row["version_id"] = None
    if "null_metric" in chosen:
        row[draw(st.sampled_from(_METRIC_COLUMNS))] = None
    if "foreign_owner" in chosen:
        row[draw(st.sampled_from(("user_id", "strategy_id")))] = draw(identifiers())

    return row


# ---------------------------------------------------------------------------
# evidence_sets
# ---------------------------------------------------------------------------

#: The ways a *set* of conditions fails admission, independently of whether each
#: individual row is well formed.
EVIDENCE_SET_DEFECTS: Tuple[str, ...] = (
    "too_few_conditions",
    "too_many_conditions",
    "duplicate_checksum",
    "overlapping_windows",
    "multiple_versions",
    "null_version",
    "defective_condition",
)


@st.composite
def evidence_sets(  # noqa: C901 - the defect branches are the point of the generator
    draw,
    *,
    owner_id: Any = None,
    strategy_id: Any = None,
    version_id: Any = None,
    min_size: int = MIN_CONDITIONS,
    max_size: int = MAX_CONDITIONS,
    admissible: Optional[bool] = True,
    defects: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """A Backtest_Evidence set: a list of ``backtest_conditions()`` rows.

    ``admissible=True`` produces a set that satisfies every criterion in
    ``evidence_validator.validate``: 3…10 rows, one shared non-null
    ``version_id``, one owner and strategy, pairwise different
    ``dataset_checksum`` values, and pairwise-distinct windows — laid out
    end-to-end with a gap, so ``intersection_days`` is zero and the pairwise
    distinctness holds whatever ``dataset`` each row drew.

    ``admissible=False`` injects at least one defect from
    ``EVIDENCE_SET_DEFECTS``; ``admissible=None`` draws the choice.
    """
    if defects is not None:
        chosen = tuple(defects)
        for defect in chosen:
            assert defect in EVIDENCE_SET_DEFECTS, f"unknown defect {defect!r}"
    else:
        if admissible is None:
            admissible = draw(st.booleans())
        if admissible:
            chosen = ()
        else:
            chosen = tuple(
                draw(
                    st.lists(
                        st.sampled_from(EVIDENCE_SET_DEFECTS),
                        min_size=1,
                        max_size=2,
                        unique=True,
                    )
                )
            )

    low = max(min_size, MIN_CONDITIONS)
    high = min(max_size, MAX_CONDITIONS)
    assert low <= high, "min_size/max_size do not intersect 3…10"
    size = draw(st.integers(min_value=low, max_value=high))
    if "too_few_conditions" in chosen:
        size = draw(st.integers(min_value=0, max_value=MIN_CONDITIONS - 1))
    if "too_many_conditions" in chosen:
        size = draw(st.integers(min_value=MAX_CONDITIONS + 1, max_value=14))

    resolved_owner = _resolve(draw, owner_id, identifiers())
    resolved_strategy = _resolve(draw, strategy_id, identifiers())
    resolved_version = _resolve(draw, version_id, identifiers())

    # Distinct 64-hex checksums, one per condition, drawn as a unique list so a
    # collision cannot make an "admissible" set fail EV_CHECKSUMS by accident.
    checksums: List[str] = []
    if size:
        checksums = draw(
            st.lists(_hex_tokens(64), min_size=size, max_size=size, unique=True)
        )

    cursor = draw(st.dates(min_value=date(2012, 1, 1), max_value=date(2016, 1, 1)))
    rows: List[Dict[str, Any]] = []
    for index in range(size):
        span = draw(st.integers(min_value=MIN_WINDOW_DAYS, max_value=250))
        rows.append(
            draw(
                backtest_conditions(
                    owner_id=resolved_owner,
                    strategy_id=resolved_strategy,
                    version_id=resolved_version,
                    start_date=cursor,
                    window_days=span,
                    dataset_checksum=checksums[index],
                    satisfying=True,
                )
            )
        )
        gap = draw(st.integers(min_value=1, max_value=30))
        cursor = cursor + timedelta(days=span - 1 + gap)

    if not rows:
        return rows

    if "duplicate_checksum" in chosen and len(rows) >= 2:
        first, second = draw(
            st.lists(
                st.integers(min_value=0, max_value=len(rows) - 1),
                min_size=2,
                max_size=2,
                unique=True,
            )
        )
        rows[second]["dataset_checksum"] = rows[first]["dataset_checksum"]

    if "overlapping_windows" in chosen and len(rows) >= 2:
        first, second = draw(
            st.lists(
                st.integers(min_value=0, max_value=len(rows) - 1),
                min_size=2,
                max_size=2,
                unique=True,
            )
        )
        # Same dataset and an identical window: intersection == window, so
        # intersection * 4 <= min(window) is false and the pair is not distinct.
        rows[second]["dataset"] = rows[first]["dataset"]
        rows[second]["start_date"] = rows[first]["start_date"]
        rows[second]["end_date"] = rows[first]["end_date"]

    if "multiple_versions" in chosen and len(rows) >= 2:
        index = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        rows[index]["version_id"] = draw(identifiers())

    if "null_version" in chosen:
        index = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        rows[index]["version_id"] = None

    if "defective_condition" in chosen:
        index = draw(st.integers(min_value=0, max_value=len(rows) - 1))
        replacement = draw(
            backtest_conditions(
                owner_id=resolved_owner,
                strategy_id=resolved_strategy,
                version_id=rows[index]["version_id"],
                start_date=rows[index]["start_date"],
                dataset_checksum=rows[index]["dataset_checksum"],
                satisfying=False,
            )
        )
        rows[index] = replacement

    return rows


# ---------------------------------------------------------------------------
# listing_rows
# ---------------------------------------------------------------------------

_CATEGORIES: Tuple[str, ...] = (
    "mean_reversion",
    "trend_following",
    "market_making",
    "arbitrage",
    "momentum",
    "ml_hybrid",
    "other",
)
_DIFFICULTIES: Tuple[str, ...] = ("beginner", "intermediate", "advanced", "pro")
_MODERATION_STATUSES: Tuple[str, ...] = (
    "pending",
    "approved",
    "rejected",
    "featured",
)
_TIMEFRAMES: Tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
_MARKET_TYPES: Tuple[str, ...] = ("spot", "futures", "margin")


def _generic_denied_value(draw, column: str) -> Any:
    """A non-null, obviously traceable value for an unrecognised denied column.

    A denied column added by a later migration must never arrive as ``None``,
    or ``test_projection_output_is_allow_listed`` would pass vacuously for it.
    """
    return f"denied-{column}-{draw(tokens())}"


def _denied_value(draw, column: str) -> Any:
    """A non-null value of a plausible type for each known denied column."""
    if column in ("author_id", "source_strategy_id", "moderated_by"):
        return draw(identifiers())
    if column == "moderation_notes":
        return draw(tokens(min_size=5, max_size=60))
    if column in ("moderated_at", "updated_at"):
        return draw(utc_instants(2020, 2030)).isoformat()
    if column == "deployment_requirements":
        return {
            "exchange": draw(tokens()),
            "min_capital": str(draw(_money("100.00", "100000.00"))),
        }
    if column == "version_history":
        return [
            {"version": f"v{n}", "note": draw(tokens())}
            for n in range(1, draw(st.integers(min_value=1, max_value=3)) + 1)
        ]
    if column == "evaluation_score":
        return draw(st.integers(min_value=0, max_value=100))
    if column == "equity_curve_snapshot":
        return [
            {"t": n, "equity": str(draw(_money("1000.00", "200000.00")))}
            for n in range(draw(st.integers(min_value=1, max_value=4)))
        ]
    if column == "has_ml_model":
        return draw(st.booleans())
    if column == "node_count":
        return draw(st.integers(min_value=1, max_value=400))
    if column in (
        "risk_stop_loss_pct",
        "risk_take_profit_pct",
        "risk_max_position_size",
        "risk_max_drawdown_pct",
    ):
        return draw(_money("0.0001", "99.9999", places=4))
    if column == "is_active":
        return draw(st.booleans())
    if column == "moderation_status":
        return draw(st.sampled_from(_MODERATION_STATUSES))
    return _generic_denied_value(draw, column)


@st.composite
def listing_rows(
    draw,
    *,
    currency: Any = None,
    rating_count: Any = None,
    denied_columns: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """One ``library_strategies`` row with every denied column populated.

    Every member of ``denied_listing_columns()`` is present with a **non-null**
    value, which is what makes
    ``test_projection_output_is_allow_listed`` (task 8.3) meaningful: the row
    carries everything ``project_listing`` must refuse to emit.

    ``rating_count`` can be pinned to ``0`` to reach Requirement 6.9's
    omit-rather-than-zero branch; left free, the pool includes ``0`` and a null
    ``avg_rating``.
    """
    resolved_ratings = _resolve(
        draw,
        rating_count,
        st.one_of(st.just(0), st.integers(min_value=1, max_value=5_000)),
    )
    # A null avg_rating is drawable at any rating_count, so Requirement 6.9's
    # "omit rather than emit zero" branch is reachable from both directions.
    avg_rating: Optional[Decimal] = draw(st.one_of(st.none(), _money("1.00", "5.00")))

    price_minor = draw(minor_amounts(1, 100_000_000))
    row: Dict[str, Any] = {
        # Columns the public projection reads (design § LISTING_SELECT).
        "id": draw(identifiers()),
        "name": draw(tokens(min_size=3, max_size=40)),
        "description": draw(st.one_of(st.none(), tokens(min_size=5, max_size=90))),
        "category": draw(st.sampled_from(_CATEGORIES)),
        "difficulty": draw(st.sampled_from(_DIFFICULTIES)),
        "tags": draw(st.lists(tokens(), min_size=0, max_size=5, unique=True)),
        "symbol": draw(st.sampled_from(("BTC/USDT", "ETH/USDT", "AAPL", "INFY"))),
        "timeframe": draw(st.sampled_from(_TIMEFRAMES)),
        "supported_timeframes": draw(
            st.lists(st.sampled_from(_TIMEFRAMES), min_size=1, max_size=4, unique=True)
        ),
        "exchange_id": draw(st.sampled_from(("binance", "bybit", "zerodha"))),
        "market_type": draw(st.sampled_from(_MARKET_TYPES)),
        "price_minor": price_minor,
        "price": (Decimal(price_minor) / Decimal(100)).quantize(Decimal("0.01")),
        "currency": _resolve(draw, currency, st.sampled_from(CURRENCIES)),
        "subscriber_count": draw(st.integers(min_value=0, max_value=100_000)),
        "clone_count": draw(st.integers(min_value=0, max_value=100_000)),
        "condition_count": draw(
            st.integers(min_value=MIN_CONDITIONS, max_value=MAX_CONDITIONS)
        ),
        "avg_rating": avg_rating,
        "rating_count": resolved_ratings,
        "published_at": draw(utc_instants(2020, 2030)).isoformat(),
        "verification_status": draw(
            st.sampled_from(("unverified", "verified", "community"))
        ),
        "source_cloning_enabled": draw(st.booleans()),
        "is_featured": draw(st.booleans()),
        # The public catalogue's cached metric columns.
        "backtest_total_return_pct": draw(_money("-99.9999", "999.9999", places=4)),
        "backtest_sharpe_ratio": draw(_money("-9.9999", "9.9999", places=4)),
        "backtest_max_drawdown_pct": draw(_money("0.0000", "99.9999", places=4)),
        "backtest_win_rate_pct": draw(_money("0.0000", "100.0000", places=4)),
        "backtest_profit_factor": draw(_money("0.0000", "9.9999", places=4)),
        "backtest_total_trades": draw(st.integers(min_value=0, max_value=10_000)),
    }

    denied = (
        frozenset(denied_columns)
        if denied_columns is not None
        else denied_listing_columns()
    )
    for column in sorted(denied):
        value = _denied_value(draw, column)
        # Booleans are the one type where a drawn False would still be non-null
        # but visually indistinguishable from "absent" in a failure report; keep
        # them as drawn, and assert non-null for everything.
        assert value is not None, f"denied column {column} must be non-null"
        row[column] = value

    return row


# ---------------------------------------------------------------------------
# protected_logic_strategies
# ---------------------------------------------------------------------------


class ProtectedLogicFixture(NamedTuple):
    """The three row groups ``protected_logic_tokens`` takes as arguments."""

    strategy_row: Dict[str, Any]
    version_row: Dict[str, Any]
    backtest_rows: List[Dict[str, Any]]


@st.composite
def protected_logic_strategies(
    draw,
    *,
    owner_id: Any = None,
    node_count: Any = None,
    backtest_count: Any = None,
) -> ProtectedLogicFixture:
    """A strategy whose Protected_Logic is made of traceable generated tokens.

    Node ids, indicator names, parameter names, threshold values and model path
    segments are all generated strings of at least three characters, so nothing
    real is excluded by ``protected_logic_tokens``'s ``len(text) >= 3`` floor and
    every token is a substring the containment assertion can look for
    (design § Mechanical Protected_Logic containment, P-47, P-48).

    Returns ``(strategy_row, version_row, backtest_rows)`` — exactly the argument
    list of ``listing_projection.protected_logic_tokens``.
    """
    resolved_owner = _resolve(draw, owner_id, identifiers())
    strategy_id = draw(identifiers())
    version_id = draw(identifiers())

    nodes_wanted = _resolve(draw, node_count, st.integers(min_value=2, max_value=6))
    tests_wanted = _resolve(draw, backtest_count, st.integers(min_value=1, max_value=3))

    node_ids = draw(
        st.lists(
            tokens(min_size=4, max_size=14),
            min_size=nodes_wanted,
            max_size=nodes_wanted,
            unique=True,
        )
    )
    indicator_names = draw(
        st.lists(
            tokens(min_size=4, max_size=14),
            min_size=nodes_wanted,
            max_size=nodes_wanted,
            unique=True,
        )
    )
    parameter_names = draw(
        st.lists(
            tokens(min_size=3, max_size=12),
            min_size=nodes_wanted,
            max_size=nodes_wanted,
            unique=True,
        )
    )
    # Threshold values are generated *strings* of at least three characters, so
    # they are tokens in their own right rather than ordinary short numbers.
    thresholds = draw(
        st.lists(
            st.from_regex(r"[1-9][0-9]{2,5}", fullmatch=True),
            min_size=nodes_wanted,
            max_size=nodes_wanted,
            unique=True,
        )
    )
    path_segments = draw(
        st.lists(tokens(min_size=4, max_size=12), min_size=3, max_size=3, unique=True)
    )

    def _condition(index: int) -> Dict[str, Any]:
        return {
            "node_id": node_ids[index],
            "indicator": indicator_names[index],
            "params": {parameter_names[index]: thresholds[index]},
            "operator": draw(st.sampled_from((">", "<", ">=", "<=", "cross_above"))),
            "threshold": thresholds[index],
        }

    buy_logic = [_condition(i) for i in range(nodes_wanted)]
    sell_logic = [_condition(i) for i in range(nodes_wanted)]

    strategy_row: Dict[str, Any] = {
        "id": strategy_id,
        "user_id": resolved_owner,
        "name": draw(tokens(min_size=4, max_size=20)),
        "buy_logic": buy_logic,
        "sell_logic": sell_logic,
        "indicators": [
            {
                "name": indicator_names[i],
                "node_id": node_ids[i],
                "params": {parameter_names[i]: thresholds[i]},
            }
            for i in range(nodes_wanted)
        ],
        "risk": {
            parameter_names[0]: thresholds[0],
            "stop_loss": thresholds[-1],
            "sizing_rule": draw(tokens(min_size=4, max_size=12)),
        },
        "ml_model_path": "models/" + "/".join(path_segments) + ".pkl",
    }

    graph_json = {
        "nodes": [
            {
                "id": node_ids[i],
                "type": indicator_names[i],
                "config": {parameter_names[i]: thresholds[i]},
            }
            for i in range(nodes_wanted)
        ],
        "edges": [
            {"from": node_ids[i], "to": node_ids[i + 1]}
            for i in range(nodes_wanted - 1)
        ],
    }
    version_row: Dict[str, Any] = {
        "id": version_id,
        "strategy_id": strategy_id,
        "version": f"v{draw(st.integers(min_value=1, max_value=20))}",
        "is_draft": False,
        "blueprint": graph_json,
        "graph_json": graph_json,
        "execution_graph": {
            "execution_order": list(node_ids),
            "plan": {node_ids[i]: indicator_names[i] for i in range(nodes_wanted)},
        },
    }

    backtest_rows: List[Dict[str, Any]] = []
    for _ in range(tests_wanted):
        backtest_rows.append(
            {
                "id": draw(identifiers()),
                "strategy_id": strategy_id,
                "user_id": resolved_owner,
                "version_id": version_id,
                "blueprint": graph_json,
                "dag_hash": draw(_hex_tokens(64)),
                "dataset_checksum": draw(_hex_tokens(64)),
            }
        )

    return ProtectedLogicFixture(strategy_row, version_row, backtest_rows)


# ---------------------------------------------------------------------------
# tenant_pairs
# ---------------------------------------------------------------------------

#: Resource kinds the cross-tenant matrix addresses by identifier. One owned id
#: per kind per identity, plus one never-created id per kind for the
#: indistinguishability oracle (P-43).
TENANT_RESOURCE_KINDS: Tuple[str, ...] = (
    "strategy_id",
    "backtest_id",
    "submission_id",
    "listing_id",
    "subscription_id",
    "settlement_id",
    "paper_session_id",
    "paper_order_id",
    "signal_id",
)


class TenantPair(NamedTuple):
    """Two distinct callers plus identifiers that were never created.

    ``u1``, ``u2`` carry the caller-identity shape
    ``core/websocket_auth.py`` and ``core/dependencies.py`` produce:
    ``{"id", "email", "tenant_id", "access_token", "role"}``, plus ``owned_ids``
    mapping each ``TENANT_RESOURCE_KINDS`` entry to an identifier that identity
    owns. ``absent_ids`` maps the same kinds to identifiers nobody owns, which is
    the oracle P-43 compares a foreign-resource response against.
    """

    u1: Dict[str, Any]
    u2: Dict[str, Any]
    absent_ids: Dict[str, str]


@st.composite
def tenant_pairs(
    draw,
    *,
    separate_tenant_ids: Optional[bool] = None,
    role: str = "authenticated",
) -> TenantPair:
    """Two distinct caller identities for the cross-tenant matrix.

    ``u1['id'] != u2['id']`` and ``u1['tenant_id'] != u2['tenant_id']`` always
    hold, and no identifier is shared between the two identities or with
    ``absent_ids``.

    ``separate_tenant_ids=False`` makes ``tenant_id == id``, the default
    ``tenant_middleware`` produces when a token carries no explicit tenant claim;
    ``True`` gives each identity a distinct tenant claim; ``None`` draws it.
    """
    if separate_tenant_ids is None:
        separate_tenant_ids = draw(st.booleans())

    # One unique pool for every identifier in the fixture, so a collision cannot
    # make a cross-tenant assertion pass for the wrong reason.
    wanted = 4 + 3 * len(TENANT_RESOURCE_KINDS)
    pool = [
        str(value)
        for value in draw(
            st.lists(st.uuids(), min_size=wanted, max_size=wanted, unique=True)
        )
    ]

    first_id, second_id, first_tenant, second_tenant = pool[:4]
    rest = pool[4:]
    owned_first = {
        kind: rest[index] for index, kind in enumerate(TENANT_RESOURCE_KINDS)
    }
    offset = len(TENANT_RESOURCE_KINDS)
    owned_second = {
        kind: rest[offset + index] for index, kind in enumerate(TENANT_RESOURCE_KINDS)
    }
    offset *= 2
    absent = {
        kind: rest[offset + index] for index, kind in enumerate(TENANT_RESOURCE_KINDS)
    }

    def _identity(
        user_id: str, tenant_id: str, owned: Dict[str, str]
    ) -> Dict[str, Any]:
        return {
            "id": user_id,
            "email": f"{user_id}@example.com",
            "tenant_id": tenant_id if separate_tenant_ids else user_id,
            "access_token": f"token-{user_id}",
            "role": role,
            "owned_ids": owned,
        }

    return TenantPair(
        _identity(first_id, first_tenant, owned_first),
        _identity(second_id, second_tenant, owned_second),
        absent,
    )
