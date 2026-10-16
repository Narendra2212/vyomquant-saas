"""
backend/paper/paper_reconciliation.py - PAPER-05's repair half: compare stored state to replay.

PAPER-05 names two gaps in ``paper_simulator.apply_fill``'s write path: "seven separate PostgREST
writes with no transaction, and no repair." The first half is a deliberate, documented tradeoff,
not an oversight -- ``apply_fill``'s own docstring ("THE WRITES, AND THE ORDERING THE MISSING
TRANSACTION FORCES") explains at length why the writes are ordered so a crash before the account
UPDATE can be RESUMED, why a crash after it is reported as :class:`~paper_simulator.
PaperConcurrencyExhausted` with ``partial_write=True`` rather than silently retried, and why "a
single transaction is not achievable over this transport." That exception detail reaches the
HTTP caller of the one request that hit it, and nothing else: there is no durable record of which
account a partial write left inconsistent, so a caller who did not act on that one response (a
dropped connection, a client that logged and moved on) leaves it undiscoverable. That is the
second half, and the one this module closes.

``paper_replay.replay`` already rebuilds a session's final state from its append-only event and
order log, independent of whatever the live rows currently hold -- it was built for Requirement
15.4's audit trail, writes nothing, and is unused by anything outside its own module and tests
today. This module is the other half ``Reconstruction.as_dict``'s own docstring anticipates ("a
reconstruction and a projection of the stored rows... compare with a bare =="): a projection of
the LIVE rows, built through the exact same ``paper_repository`` reads every other caller uses
(so it is subject to the same ownership predicates), in the shape that replay's reconstruction can
be compared against field for field.

THE SHAPE OF THE COMPARISON
----------------------------
Mirrors ``tests/test_paper_replay.py``'s own ``_stored`` / ``_reconstructed`` pair exactly -- the
one place this project already asserts a replay reproduces a recorded session, which is the
contract a reconciliation tool has to share or it would flag a mismatch nothing is actually wrong
about. ``series_index`` and the per-row ``stale`` flag are excluded for the same reason that test
excludes them: they are two different measurements under one name, not a persisted fact to agree
on (see :func:`project_reconstruction`).

WHAT A CLEAN RESULT MEANS, AND WHAT A DIRTY ONE DOES NOT
---------------------------------------------------------
``clean=True`` means the live rows and the replayed reconstruction agree on every compared
subject -- the apply_fill write sequence for this session completed exactly as the log says it
should have, whether or not any individual write happened to retry along the way. ``clean=False``
names which subjects disagree and how, which is the starting point for an operator's repair, not
a repair in itself: this module writes nothing, exactly as ``replay`` does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import paper_repository as repo
from . import paper_replay as replay

__all__ = [
    "ReconciliationReport",
    "reconcile_session",
    "project_stored_state",
    "project_reconstruction",
]

#: The subjects compared, in the order a report lists them -- the same order
#: ``test_a_replay_reproduces_the_recorded_session_exactly`` asserts them in.
SUBJECTS: Tuple[str, ...] = (
    "balances",
    "realized_pnl",
    "orders",
    "fills",
    "positions",
    "closed_trades",
    "equity_series",
)


def _decimal(value: Any) -> Decimal:
    """One ``NUMERIC`` column as an exact ``Decimal``. Never a ``float``."""
    return Decimal(str(value))


def _optional_decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else _decimal(value)


def _instant(value: Any) -> Optional[datetime]:
    """One ``TIMESTAMPTZ`` column as a tz-aware UTC ``datetime``.

    The reading ``paper_repository._instant`` writes, mirrored here rather than imported: the
    module-private helper is this file's own small, read-direction counterpart of a
    write-direction one that lives in ``paper_repository``, exactly as
    ``tests/test_paper_replay.py`` keeps its own copy for the same reason.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _position_rank(row: Mapping[str, Any]) -> Tuple[int, str, int]:
    """How recent a ``paper_positions`` row is - an OPEN row outranks every closed one."""
    return (
        0 if row.get("closed_at") else 1,
        str(row.get("closed_at") or ""),
        int(row.get("version") or 1),
    )


def project_stored_state(
    supabase: Any, user_id: Any, session_id: Any, *, currency: Any
) -> Dict[str, Any]:
    """The live rows, in the shape :func:`project_reconstruction` can be compared against.

    Every read goes through ``paper_repository``, carrying ``user_id`` as a predicate exactly as
    ``paper_replay.replay`` does, so this projection is never a wider view of the account than the
    caller it is built for is already entitled to.

    Args:
        supabase: the caller's RLS-scoped Persistence_Layer handle.
        user_id: the identity every read is scoped to.
        session_id: the Paper_Session whose rows are projected.
        currency: the session's configured currency (``Reconstruction.currency``), needed because
            ``read_account`` keys the account row by it as well as by ``session_id``.
    """
    account = repo.read_account(supabase, user_id, currency=currency, session_id=session_id)
    balances = (
        {
            "available_balance": _decimal(account["available_balance"]),
            "locked_balance": _decimal(account["locked_balance"]),
            "total_equity": _decimal(account["total_equity"]),
        }
        if account is not None
        else {}
    )
    realized_pnl = _decimal(account["realized_pnl"]) if account is not None else Decimal("0")

    orders = {
        str(row["id"]): {
            "symbol": str(row["symbol"]),
            "side": str(row["side"]),
            "order_type": str(row["order_type"]),
            "quantity": _decimal(row["quantity"]),
            "limit_price": _optional_decimal(row.get("limit_price")),
            "reference_price": _optional_decimal(row.get("reference_price")),
            "filled_quantity": _decimal(row["filled_quantity"]),
            "avg_fill_price": _optional_decimal(row.get("avg_fill_price")),
            "fee_minor": int(row.get("fee_minor") or 0),
            "slippage_minor": int(row.get("slippage_minor") or 0),
            "order_state": str(row["order_state"]),
            "rejection_reason": (
                None if row.get("rejection_reason") is None else str(row["rejection_reason"])
            ),
        }
        for row in repo.get_orders(supabase, user_id, session_id=session_id)
    }

    fills = {
        (str(row["order_id"]), str(row["fill_event_id"])): {
            "quantity": _decimal(row["quantity"]),
            "price": _decimal(row["price"]),
            "fee_minor": int(row["fee_minor"]),
            "slippage_minor": int(row["slippage_minor"]),
            "filled_at": _instant(row["filled_at"]),
        }
        for row in repo.get_fills(supabase, user_id, session_id=session_id)
    }

    by_symbol: Dict[str, Dict[str, Any]] = {}
    for row in repo.get_positions(supabase, user_id, session_id=session_id, include_closed=True):
        symbol = str(row["symbol"])
        current = by_symbol.get(symbol)
        if current is None or _position_rank(row) > _position_rank(current):
            by_symbol[symbol] = dict(row)
    positions = {
        symbol: {
            "side": str(row["side"]),
            "size": _decimal(row["size"]),
            "entry_price": _decimal(row["entry_price"]),
            "current_price": _optional_decimal(row.get("current_price")),
            "unrealized_pnl": _optional_decimal(row.get("unrealized_pnl")),
            "price_at": _instant(row.get("price_at")),
            "opened_at": _instant(row.get("opened_at")),
            "closed_at": _instant(row.get("closed_at")),
        }
        for symbol, row in by_symbol.items()
    }

    closed_trades = [
        {
            "symbol": str(row["symbol"]),
            "side": str(row["side"]),
            "quantity": _decimal(row["quantity"]),
            "entry_price": _decimal(row["entry_price"]),
            "exit_price": _decimal(row["exit_price"]),
            "realized_pnl": _decimal(row["realized_pnl"]),
        }
        for row in repo.get_trades(supabase, user_id, session_id=session_id)
    ]

    equity_series = [
        {
            "cause": str(row["cause"]),
            "total_equity": _decimal(row["total_equity"]),
            "available_balance": _decimal(row["available_balance"]),
            "locked_balance": _decimal(row["locked_balance"]),
            "position_market_value": _decimal(row["position_market_value"]),
            "taken_at": _instant(row["taken_at"]),
        }
        for row in repo.get_equity_snapshots(supabase, user_id, session_id=session_id)
    ]

    return {
        "balances": balances,
        "realized_pnl": realized_pnl,
        "orders": orders,
        "fills": fills,
        "positions": positions,
        "closed_trades": closed_trades,
        "equity_series": equity_series,
    }


def project_reconstruction(result: "replay.Reconstruction") -> Dict[str, Any]:
    """``result``, narrowed to exactly what :func:`project_stored_state` can be compared against.

    ``series_index`` and the per-row ``stale`` flag are dropped, for the reason
    ``tests/test_paper_replay.py::_reconstructed`` drops them: the persisted ``series_index`` is
    the session's equity-series ordinal (Requirement 17.15's reset restarts it), while the
    reconstruction's is the point's ordinal within the series it built -- two different
    measurements under one name, not a fact the live row and the replay could disagree about. The
    reconstruction's ``SESSION_START`` point has no persisted counterpart either (``start_session``
    writes the opening snapshot the fixture account already carries, so the series the loop wrote
    begins at its first fill or revaluation) and is dropped for the same reason.
    """
    return {
        "balances": dict(result.balances),
        "realized_pnl": result.realized_pnl,
        "orders": {
            order.order_id: {
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": order.quantity,
                "limit_price": order.limit_price,
                "reference_price": order.reference_price,
                "filled_quantity": order.filled_quantity,
                "avg_fill_price": order.avg_fill_price,
                "fee_minor": order.fee_minor,
                "slippage_minor": order.slippage_minor,
                "order_state": order.order_state,
                "rejection_reason": order.rejection_reason,
            }
            for order in result.orders
        },
        "fills": {
            (fill.order_id, fill.fill_event_id): {
                "quantity": fill.quantity,
                "price": fill.price,
                "fee_minor": fill.fee_minor,
                "slippage_minor": fill.slippage_minor,
                "filled_at": fill.filled_at,
            }
            for fill in result.fills
        },
        "positions": {
            symbol: {
                "side": position["side"],
                "size": position["size"],
                "entry_price": position["entry_price"],
                "current_price": position["current_price"],
                "unrealized_pnl": position["unrealized_pnl"],
                "price_at": position["price_at"],
                "opened_at": position["opened_at"],
                "closed_at": position["closed_at"],
            }
            for symbol, position in result.positions.items()
        },
        "closed_trades": [
            {
                "symbol": trade["symbol"],
                "side": trade["side"],
                "quantity": trade["quantity"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "realized_pnl": trade["realized_pnl"],
            }
            for trade in result.closed_trades
        ],
        "equity_series": [
            {
                "cause": point["cause"],
                "total_equity": point["total_equity"],
                "available_balance": point["available_balance"],
                "locked_balance": point["locked_balance"],
                "position_market_value": point["position_market_value"],
                "taken_at": point["taken_at"],
            }
            for point in result.equity_series
            if point["cause"] != replay.SESSION_START
        ],
    }


@dataclass(frozen=True)
class ReconciliationReport:
    """The outcome of comparing one session's live rows against its replayed reconstruction.

    ``clean`` is the whole answer an automated sweep needs. ``mismatches`` names which of
    :data:`SUBJECTS` disagreed, in the order they were compared, for a log line or an alert.
    ``stored`` and ``reconstructed`` are the full projections, kept for an operator who opens the
    report to see exactly what diverged and by how much -- this module draws no conclusion about
    which side is "right"; replay is the independent account, not an automatic override.
    """

    session_id: str
    user_id: str
    clean: bool
    mismatches: Tuple[str, ...]
    stored: Dict[str, Any] = field(repr=False)
    reconstructed: Dict[str, Any] = field(repr=False)
    events_replayed: int = 0
    events_unverified: int = 0
    stale: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "clean": self.clean,
            "mismatches": list(self.mismatches),
            "stored": _json_safe_projection(self.stored),
            "reconstructed": _json_safe_projection(self.reconstructed),
            "events_replayed": self.events_replayed,
            "events_unverified": self.events_unverified,
            "stale": self.stale,
        }


def _json_safe_projection(projection: Mapping[str, Any]) -> Dict[str, Any]:
    """``project_stored_state`` / ``project_reconstruction``'s output, with JSON-safe keys.

    Only ``fills`` needs it: its key is ``(order_id, fill_event_id)``, a tuple, because that pair
    is literally what ``uq_paper_fill_event`` de-duplicates on and the comparison in
    :func:`reconcile_session` wants that as one hashable identity. A JSON object key must be a
    string, so this is applied only when a report leaves Python (:meth:`as_dict`) -- the
    comparison itself, and every test file comparing two projections with a bare ``==``, keeps the
    tuple form unchanged.
    """
    out = dict(projection)
    out["fills"] = {
        f"{order_id}:{fill_event_id}": fill
        for (order_id, fill_event_id), fill in projection["fills"].items()
    }
    return out


def reconcile_session(supabase: Any, user_id: Any, session_id: Any) -> Optional[ReconciliationReport]:
    """Compare one Paper_Session's live rows against its replayed reconstruction.

    Returns ``None`` for exactly the two cases ``paper_replay.replay`` already answers ``None``
    for -- no such session, or a session belonging to another user -- deliberately indistinguishable
    from each other for the same disclosure reason ``replay`` gives.

    Raises:
        paper_replay.ReplayRefused: a read did not complete, or the recorded log could not be
            replayed at all. The caller sees the same refusal ``replay`` would raise; this
            function draws no stronger conclusion from a read it could not complete than ``replay``
            does.
    """
    result = replay.replay(supabase, user_id, session_id)
    if result is None:
        return None

    stored = project_stored_state(supabase, user_id, session_id, currency=result.currency)
    reconstructed = project_reconstruction(result)

    mismatches = tuple(
        subject for subject in SUBJECTS if stored[subject] != reconstructed[subject]
    )

    return ReconciliationReport(
        session_id=str(session_id),
        user_id=str(user_id),
        clean=not mismatches,
        mismatches=mismatches,
        stored=stored,
        reconstructed=reconstructed,
        events_replayed=result.events_replayed,
        events_unverified=result.events_unverified,
        stale=result.stale,
    )
