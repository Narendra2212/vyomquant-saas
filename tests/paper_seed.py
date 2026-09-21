"""
tests/paper_seed.py - seeding helpers for the persisted paper account.

Not a test module (pytest collects ``test_*.py``): a support module for the suites that used to
build paper state by assigning to ``PaperTradingService._accounts``, ``._positions`` and
``._trades``.

WHY IT EXISTS
-------------
As of marketplace-subscriptions-paper-trading task 23.2 the paper service holds no balance, no
position, no order and no fill in process memory. Every figure lives in the ``paper_*`` tables
and is reached through ``backend/paper/paper_repository.py``, and an absent ``paper_accounts`` is
answered with 503 ``PAPER_PERSISTENCE_UNAVAILABLE`` rather than with a remembered figure
(Requirements 17.2, 28.3). A test that wants a known account therefore has to **write** one,
and writing it through the repository is what makes the test's premise the same premise
production runs under.

The Persistence_Layer double is ``tests/test_paper_repository.FakeSupabase``, which enforces the
five unique indexes ``009_paper_trading.sql`` declares. There is deliberately only one double in
this repository: a second would be a second set of assumptions about the database.

WHAT A "SEEDED FILL" IS MADE OF
-------------------------------
``GET /api/paper/trades`` and ``dashboard_aggregation_service``'s today-PnL sum read the fill
history, which is ``paper_fills`` joined to its order (for the symbol and side, which live on the
order) and to the ``cause = 'FILL'`` balance event (for the realized PnL that fill moved - the
only place a **per-fill** realized figure is persisted, because ``paper_trades`` carries one row
per closed round-trip and a partial close writes none). So :func:`seed_fill` writes all three,
in that order, exactly as the fill path does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper.paper_order_state import PaperOrderState

#: ``paper_positions.side`` is ``LONG`` / ``SHORT`` (``chk_paper_position_side``); the response
#: body lower-cases it back to the ``long`` / ``short`` every consumer has always read.
_SIDE_FOR_TEXT = {"long": "LONG", "short": "SHORT", "LONG": "LONG", "SHORT": "SHORT"}


def bind_paper_persistence(service: Any) -> Any:
    """Bind a fresh in-memory Persistence_Layer to ``service`` and return it."""
    from tests.test_paper_repository import FakeSupabase

    repo.reset_persistence_probe()
    double = FakeSupabase()
    service.bind_persistence(double)
    return double


def release_paper_persistence(service: Any) -> None:
    """Unbind, so no later test inherits these rows or this migration verdict."""
    service.bind_persistence(None)
    repo.reset_persistence_probe()


def _minor(amount: Any) -> int:
    """A money amount as exact integer Minor_Units (USD cents)."""
    scaled = Decimal(str(amount)).scaleb(2)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount!r} is not a whole number of minor units")
    return int(scaled)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def account_id_for(service: Any, user_id: str) -> str:
    """The user's default paper account id, creating the account if it has none."""
    return service.get_or_create_account(str(user_id))["account_id"]


def seed_account(
    service: Any,
    user_id: str,
    *,
    available_balance: Optional[str] = None,
    locked_balance: Optional[str] = None,
    realized_pnl: Optional[str] = None,
    total_equity: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the named money columns onto the user's default account, under the version guard.

    Only the columns named are written; the rest of the row is left as it is. ``total_equity`` is
    accepted because the column is persisted, but every read recomputes it from
    ``available + locked + position_market_value`` (Requirement 18.3), so passing it changes what
    is stored and not what is reported.
    """
    supabase = service._supabase
    uid = str(user_id)
    account_id = account_id_for(service, uid)
    locked = repo.lock_account_for_update(supabase, uid, account_id=account_id)

    payload: Dict[str, Any] = {}
    if available_balance is not None:
        payload["available_balance"] = available_balance
    if locked_balance is not None:
        payload["locked_balance"] = locked_balance
    if realized_pnl is not None:
        payload["realized_pnl"] = realized_pnl
    if total_equity is not None:
        payload["total_equity"] = total_equity
    if not payload:
        return locked

    return repo.bump_version(
        supabase,
        user_id=uid,
        account_id=account_id,
        expected_version=locked["version"],
        payload=payload,
    )


def seed_position(
    service: Any,
    user_id: str,
    *,
    symbol: str,
    side: str,
    size: str,
    entry_price: str,
    current_price: Optional[str] = None,
    unrealized_pnl: Optional[str] = None,
    opened_at: Optional[str] = None,
    price_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Write one OPEN position for the user's default account.

    ``uq_paper_position_open`` allows one open row per ``(account_id, symbol)``, and
    :func:`repo.upsert_position` updates the open row when there is one - so calling this twice
    for a symbol restates that position rather than creating a second one, which is what the
    index guarantees in production.
    """
    supabase = service._supabase
    uid = str(user_id)
    instant = opened_at or _now()
    return repo.upsert_position(
        supabase,
        account_id=account_id_for(service, uid),
        user_id=uid,
        symbol=symbol,
        side=_SIDE_FOR_TEXT[str(side)],
        size=size,
        entry_price=entry_price,
        opened_at=instant,
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
        price_at=price_at or instant,
    )


def seed_fill(
    service: Any,
    user_id: str,
    *,
    symbol: str,
    side: str,
    quantity: str,
    price: str,
    fee: str = "0",
    realized_pnl: str = "0",
    executed_at: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one filled order, its fill, and the ledger event carrying its realized PnL.

    ``execution_id`` becomes ``paper_fills.fill_event_id``, which is what the fill history
    reports as ``execution_id`` - and what ``uq_paper_fill_event`` de-duplicates on, so a repeated
    one is refused rather than applied twice (Requirements 16.9, 18.13).
    """
    supabase = service._supabase
    uid = str(user_id)
    account_id = account_id_for(service, uid)
    instant = executed_at or _now()
    event_id = execution_id or f"exec_paper_seed_{symbol}_{instant}"

    created = repo.insert_order(
        supabase,
        account_id=account_id,
        user_id=uid,
        symbol=symbol,
        side=side,
        order_type="market",
        quantity=quantity,
        reference_price=price,
        fingerprint=f"seed-{event_id}",
        order_state=PaperOrderState.CREATED,
    )
    repo.update_order(
        supabase,
        user_id=uid,
        order_id=created["id"],
        order_state=PaperOrderState.ACCEPTED,
        expected_state=PaperOrderState.CREATED,
    )
    repo.update_order(
        supabase,
        user_id=uid,
        order_id=created["id"],
        order_state=PaperOrderState.FILLED,
        expected_state=PaperOrderState.ACCEPTED,
        filled_quantity=quantity,
        avg_fill_price=price,
        fee_minor=_minor(fee),
    )
    fill = repo.insert_fill(
        supabase,
        order_id=created["id"],
        user_id=uid,
        fill_event_id=event_id,
        quantity=quantity,
        price=price,
        fee_minor=_minor(fee),
        slippage_minor=0,
        filled_at=instant,
    )

    account = repo.read_account(supabase, uid)
    repo.insert_balance_event(
        supabase,
        account_id=account_id,
        user_id=uid,
        cause="FILL",
        available_delta="0",
        locked_delta="0",
        realized_delta=realized_pnl,
        available_after=account["available_balance"],
        locked_after=account["locked_balance"],
        realized_after=account["realized_pnl"],
        fill_id=fill["id"],
        occurred_at=instant,
    )
    return fill
