"""PAPER-01: route a ``BotRunner`` paper fill into the CANONICAL paper ledger.

THE DEFECT THIS CLOSES
======================
The platform had two paper engines sharing one word, and only one of them wrote anything
a trader can read:

* **The canonical ledger** -- ``paper_accounts`` / ``paper_orders`` / ``paper_fills`` /
  ``paper_positions`` / ``paper_balance_events`` / ``paper_trades`` /
  ``paper_equity_snapshots``. Arithmetic in the pure :mod:`paper_accounting`, writes in
  :mod:`paper_repository`. It is what ``GET /api/paper/account``, ``/positions``,
  ``/orders``, ``/trades`` and ``/summary`` serve, and therefore what ``PaperTrading.jsx``
  and the paper dashboard show.
* **``BotRunner`` paper mode** -- ``ExecutionEngine._execute_trade_internal``'s PAPER
  branch, which mutated ``self.cash`` / ``self.positions`` / ``self._marks`` in process
  against a synthetic ``{"total_equity": 10000.0}`` and returned a ``paper_<hex12>`` dict.

``backend_app/backend/master_executor.py`` had **no import edge** to
``backend_app/backend/paper/`` at all. So a trader who deployed a strategy in paper mode
got real simulated executions that their paper account did not show: no order row, no
fill row, no position row, no cash movement, no equity snapshot, and nothing that survived
a process restart.

WHY THE DEFAULT ACCOUNT AND NOT A PAPER_SESSION
-----------------------------------------------
There are two persisted paper paths. The session path
(``paper_simulator.submit_intent`` / ``apply_fill``) requires a ``paper_sessions`` row and
a ``feed_state = 'HEALTHY'`` market feed, and **a BotRunner bot has neither** -- which is
exactly what the comment in ``master_executor`` named as the blocker ("the missing piece
is a ``session_id``, which a BotRunner bot does not have").

The default-account path (``PaperTradingService.place_order``) needs no ``session_id``:
``013_paper_default_account_children.sql`` relaxed ``session_id`` to NULLABLE on all seven
child tables for precisely this case, and added
``uq_paper_order_idem_default`` -- a partial unique index on
``(account_id, idempotency_key) WHERE session_id IS NULL`` -- so the default account still
de-duplicates. Verified present in the live database.

It is also the right account on the merits: the default account is the one the six
``/api/paper/*`` endpoints a trader already uses report on, so writing a bot's fills there
is what makes the dashboard agree with the bot.

**No second accounting implementation is introduced.** This module computes no money. It
assembles arguments, calls ``PaperTradingService.place_order``, and reads the resulting
rows back. Every figure it returns was produced by ``paper_accounting`` and persisted by
``paper_repository``.

WHY A DETERMINISTIC IDEMPOTENCY KEY IS MANDATORY HERE
-----------------------------------------------------
The **order-level** ``idempotency_key`` is what stops a retried tick from placing a second
paper order: a bot that retried under a fresh key would place a second order and move the
money twice, and only ``uq_paper_order_idem_default`` can refuse that.

When this module was written, the order-level key was also the *only* guard available,
because ``paper_trading_service._execute_fill`` minted its ``fill_event_id`` as
``f"exec_paper_{uuid.uuid4().hex[:12]}"`` -- random -- so ``uq_paper_fill_event`` could
never recognise a repeated fill on the default-account path. **That has since been fixed**
(PAPER-02's phase): the id is now ``f"exec_paper_{order_id}"``, deterministic, so the
fill-level index guards the fill as well. The order-level key is still mandatory - it
guards a different thing, the creation of a second ORDER, which the fill-level index cannot
see.

So the key is derived from the gateway's own ``execution_id``, which
``ExecutionEngine.execute_with_idempotency`` already generates deterministically per
``(tenant, strategy, symbol, side, time bucket)``. Same tick, same key, one order --
arbitrated by the database index rather than by anything in this process.

WHY A PERSISTENCE FAILURE REFUSES THE FILL
------------------------------------------
:meth:`BotPaperLedger.record_fill` raises :class:`BotPaperLedgerUnavailable` rather than
returning a simulated fill. Reporting a fill that was not recorded **is** PAPER-01, so a
ledger that cannot be written must not produce a successful execution. The cost of
refusing is a paper bot that does not trade while the store is down; the cost of the
alternative is the defect this module exists to close, reintroduced as a fallback.

Regression: tests/test_paper01_botrunner_fills_reach_the_ledger_postgres.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger("BotPaperLedger")

#: Prefix on every ``paper_orders.idempotency_key`` this path writes, so a key written by a
#: bot is distinguishable from one supplied by a client on ``POST /api/paper/orders``.
IDEMPOTENCY_PREFIX = "botrunner"

#: ``chk_paper_order_idem_len`` admits 1..128 characters. The key is truncated to fit rather
#: than being allowed to fail the insert, and the truncation keeps the PREFIX and the TAIL of
#: the execution id -- the tail is where its entropy is.
MAX_IDEMPOTENCY_KEY = 128

#: A bot's sell may not exceed the open LONG it is closing. **This preserves the pre-fix
#: BotRunner semantics exactly, and it is here because routing bots onto the canonical path
#: would otherwise change them silently.**
#:
#: MEASURED, not assumed (``staging/probe_paper01_sell_semantics.py``):
#:
#:   * a sell with no position at all is ACCEPTED and FILLED by the canonical path, opening
#:     ``side=SHORT size=0.01 entry=49975``;
#:   * a sell of 0.03 against an open long of 0.01 REVERSES THROUGH ZERO -- the long is
#:     closed in its own row with a ``paper_trades`` row, and ``SHORT size=0.02`` is opened.
#:
#: Both are correct for the canonical engine, which has an explicit SHORT side and a
#: documented reversal. Neither is what a BotRunner bot did before: its paper branch read
#:
#:     if symbol not in self.positions:
#:         success, message = False, f"No position found for {symbol}"
#:
#: and its buy branch calls ``open_position(..., side='long')``, so the bot path is
#: long-only by construction. Letting the fix hand every paper bot the ability to go short
#: -- including bots running spot strategies that have never been evaluated short -- would
#: be a material behaviour change nobody reviewed, introduced as a side effect of a
#: persistence fix.
#:
#: This is a policy on the INTENT and computes no money, so it is not a second accounting
#: implementation: it decides whether to call the canonical path at all, and the canonical
#: path remains the only thing that moves a balance.
#:
#: A deployment that genuinely wants short paper bots turns this off deliberately, which is
#: the review this guard exists to require.
REDUCE_ONLY_SELLS = True


class BotPaperLedgerError(Exception):
    """Base for this module's refusals."""


class BotPaperLedgerUnavailable(BotPaperLedgerError):
    """The canonical paper ledger could not record the fill.

    Raised rather than degrading to an in-process simulation. See the module docstring: a
    fill reported but not recorded is the defect, not the fallback.
    """


class BotPaperSellExceedsPosition(BotPaperLedgerError):
    """A bot's sell is larger than the open long it would close.

    The pre-fix refusal, preserved. See :data:`REDUCE_ONLY_SELLS` for why it is enforced
    here rather than left to the canonical path, which would open or reverse into a short.
    """


@dataclass(frozen=True)
class LedgerFill:
    """What the canonical ledger recorded, read back from it.

    Every figure here came out of ``paper_accounting`` and was persisted by
    ``paper_repository``; nothing on this object was computed in this module.
    """

    order_id: str
    symbol: str
    side: str
    size: Decimal
    executed_price: Decimal
    fee: Decimal
    available_balance: Decimal
    locked_balance: Decimal
    realized_pnl: Decimal
    total_equity: Decimal
    account_id: str
    #: ``{ledger symbol: paper_positions row}``, open positions only.
    positions: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: True when the order was already recorded under this key and nothing moved.
    duplicate: bool = False

    @property
    def cash(self) -> Decimal:
        """``available_balance + locked_balance`` -- the cash side of the equity identity."""
        return self.available_balance + self.locked_balance


def idempotency_key_for(execution_id: Any) -> str:
    """The ``paper_orders.idempotency_key`` a bot fill carries, derived from the tick.

    Deterministic on purpose -- see the module docstring. Bounded to
    :data:`MAX_IDEMPOTENCY_KEY` by keeping the prefix and the tail, because the tail of an
    ``execution_id`` is the part that distinguishes one tick from another.
    """
    raw = str(execution_id or "").strip()
    if not raw:
        raise BotPaperLedgerError(
            "a bot paper fill needs the gateway's execution_id: it is what makes the "
            "idempotency key deterministic, and without one a retried tick would place a "
            "second paper order and move the money twice"
        )
    key = f"{IDEMPOTENCY_PREFIX}:{raw}"
    if len(key) <= MAX_IDEMPOTENCY_KEY:
        return key
    keep = MAX_IDEMPOTENCY_KEY - len(IDEMPOTENCY_PREFIX) - 1
    return f"{IDEMPOTENCY_PREFIX}:{raw[-keep:]}"


def _decimal(value: Any, default: str = "0") -> Decimal:
    """A persisted ``NUMERIC`` as a ``Decimal``. ``None`` becomes ``default``.

    ``str()`` first because a PostgREST client may hand back a ``float`` for a numeric
    column, and ``Decimal(0.07)`` is not seven hundredths.
    """
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class BotPaperLedger:
    """Records one bot's paper fills on its owner's DEFAULT paper account.

    One instance per bot, built by ``BotRunner._recover_live_state`` on the paper branch
    and handed to the ``ExecutionEngine`` as its ``paper_ledger``. Holds the identity and
    the service handle; holds no figure and no position.
    """

    def __init__(
        self,
        user_id: Any,
        *,
        service: Any = None,
        supabase: Any = None,
        access_token: Optional[str] = None,
    ) -> None:
        self.user_id = str(user_id)
        self._access_token = access_token
        self._supabase = supabase
        self._service = service

    # ── collaborators, resolved late ──────────────────────────────────────

    def service(self) -> Any:
        """The canonical ``PaperTradingService``.

        Imported inside the method rather than at module scope: ``master_executor`` builds
        this object during a bot's boot, and an import-time edge from the engine's module
        graph into the paper package would widen what ``ExecutionEngine`` pulls in for every
        caller, paper or not.
        """
        if self._service is None:
            from backend_app.backend.paper_trading_service import (
                get_paper_trading_service,
            )

            self._service = get_paper_trading_service()
        if self._supabase is not None:
            # Name the handle for every statement this service issues from now on, so a
            # test or a worker that bound one is not quietly talking to the anon singleton.
            try:
                self._service.bind_persistence(self._supabase)
            except Exception as exc:  # noqa: BLE001
                raise BotPaperLedgerUnavailable(
                    f"the paper service would not accept the supplied persistence handle: {exc}"
                ) from exc
        return self._service

    def _client(self) -> Any:
        if self._supabase is not None:
            return self._supabase
        return self.service().persistence_client(self._access_token)

    def ledger_symbol(self, symbol: Any) -> str:
        """The symbol as the ledger spells it.

        ``PaperTradingService._clean_symbol`` is the authority (``BTC/USDT`` -> ``BTC-USDT``)
        and is called rather than re-implemented, so a bot's rows carry the same symbol the
        ``/api/paper/*`` endpoints return and a position opened by the UI and added to by a
        bot are one row rather than two.
        """
        return self.service()._clean_symbol(symbol)

    # ── the one operation ────────────────────────────────────────────────

    async def record_fill(
        self,
        *,
        symbol: Any,
        side: Any,
        size: Any,
        price: Any,
        execution_id: Any,
    ) -> LedgerFill:
        """Place and fill one paper order on the canonical ledger, and read it back.

        Raises:
            BotPaperLedgerUnavailable: the ledger refused or could not be reached. The
                caller must treat the execution as failed; see the module docstring.
        """
        service = self.service()
        key = idempotency_key_for(execution_id)
        ledger_symbol = self.ledger_symbol(symbol)
        clean_side = str(side).strip().lower()
        if clean_side in ("long", "buy"):
            clean_side = "buy"
        elif clean_side in ("short", "sell"):
            clean_side = "sell"
        else:
            raise BotPaperLedgerError(
                f"a paper fill side must resolve to buy or sell, got {side!r}"
            )

        if clean_side == "sell" and REDUCE_ONLY_SELLS:
            self._assert_reduce_only(ledger_symbol, size)

        try:
            order = await service.place_order(
                user_id=self.user_id,
                symbol=ledger_symbol,
                side=clean_side,
                order_type="market",
                quantity=size,
                price=price,
                idempotency_key=key,
                access_token=self._access_token,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as a defined refusal
            # A ValueError here is a REFUSAL by the canonical path - insufficient funds, no
            # position to sell, a risk limit, the kill switch. It is not an outage, but it is
            # still "this fill did not happen", which is the only thing the caller can act
            # on. Both are reported as one refusal carrying the reason.
            raise BotPaperLedgerUnavailable(
                f"the canonical paper ledger refused the fill for "
                f"{ledger_symbol} {clean_side} {size}: {exc}"
            ) from exc

        return self._read_back(order, ledger_symbol, clean_side, key)

    def open_long_size(self, ledger_symbol: str) -> Decimal:
        """The open LONG quantity this account holds in ``ledger_symbol``, from the ledger.

        Read from ``paper_positions`` -- the source of truth -- rather than from any
        in-process figure, so the guard cannot be defeated by a stale copy. A SHORT row
        returns zero: there is no long to reduce, which is the answer the guard needs.
        """
        from backend_app.backend.paper import paper_repository as repo

        sb = self._client()
        try:
            account_row = repo.read_account(sb, self.user_id)
            if account_row is None:
                return Decimal("0")
            rows = repo.get_positions(
                sb,
                self.user_id,
                account_id=str(account_row["id"]),
                symbol=ledger_symbol,
            )
        except Exception as exc:  # noqa: BLE001
            raise BotPaperLedgerUnavailable(
                f"the open position for {ledger_symbol} could not be read, so a sell "
                f"cannot be checked against it: {exc}"
            ) from exc

        total = Decimal("0")
        for row in rows:
            if str(row.get("side") or "").strip().upper() != "LONG":
                continue
            if row.get("closed_at") is not None:
                continue
            total += _decimal(row.get("size"))
        return total

    def _assert_reduce_only(self, ledger_symbol: str, size: Any) -> None:
        """Refuse a sell larger than the open long. See :data:`REDUCE_ONLY_SELLS`."""
        requested = _decimal(size)
        held = self.open_long_size(ledger_symbol)
        if held <= 0:
            raise BotPaperSellExceedsPosition(
                f"No position found for {ledger_symbol}: this bot holds no open long, and "
                f"a bot's sell may not open or reverse into a short. Nothing was sent to "
                f"the paper ledger."
            )
        if requested > held:
            raise BotPaperSellExceedsPosition(
                f"A sell of {requested} {ledger_symbol} exceeds the open long of {held}. "
                f"Reducing is permitted; reversing through zero into a short is not. "
                f"Nothing was sent to the paper ledger."
            )

    def _read_back(
        self,
        order: Mapping[str, Any],
        ledger_symbol: str,
        clean_side: str,
        key: str,
    ) -> LedgerFill:
        """Read the persisted account and positions, so the returned figures are stored ones.

        The order body ``place_order`` returns is the API shape, and it deliberately omits
        balances. The authoritative figures are on ``paper_accounts``, so they are read from
        there rather than derived from the order -- which is also what makes the caller's
        reconciliation a copy of persisted state and not a parallel computation.
        """
        from backend_app.backend.paper import paper_repository as repo

        sb = self._client()
        try:
            account_row = repo.read_account(sb, self.user_id)
            if account_row is None:
                raise BotPaperLedgerUnavailable(
                    "the paper order was placed but the default paper account cannot be "
                    "read back, so the fill cannot be confirmed as recorded"
                )
            account_id = str(account_row["id"])
            position_rows = repo.get_positions(sb, self.user_id, account_id=account_id)
        except BotPaperLedgerUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BotPaperLedgerUnavailable(
                f"the paper fill was written but could not be read back: {exc}"
            ) from exc

        positions: Dict[str, Mapping[str, Any]] = {
            str(row.get("symbol")): row for row in position_rows
        }

        fee = _decimal(order.get("fee"))
        executed = _decimal(order.get("price"))
        filled = _decimal(order.get("filled_quantity"))

        return LedgerFill(
            order_id=str(order.get("order_id") or order.get("id") or ""),
            symbol=ledger_symbol,
            side=clean_side,
            size=filled,
            executed_price=executed,
            fee=fee,
            available_balance=_decimal(account_row.get("available_balance")),
            locked_balance=_decimal(account_row.get("locked_balance")),
            realized_pnl=_decimal(account_row.get("realized_pnl")),
            total_equity=_decimal(account_row.get("total_equity")),
            account_id=account_id,
            positions=positions,
            # ``place_order`` returns the RECORDED order on an idempotency hit, so a
            # duplicate is visible as an order that already carries its fill.
            duplicate=bool(order.get("_idempotent_replay", False)),
        )


__all__ = [
    "IDEMPOTENCY_PREFIX",
    "MAX_IDEMPOTENCY_KEY",
    "REDUCE_ONLY_SELLS",
    "BotPaperLedger",
    "BotPaperLedgerError",
    "BotPaperLedgerUnavailable",
    "BotPaperSellExceedsPosition",
    "LedgerFill",
    "idempotency_key_for",
]
