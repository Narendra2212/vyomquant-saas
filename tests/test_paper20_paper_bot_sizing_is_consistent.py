"""PAPER-20: a paper bot traded once and then had every entry rejected.

Production deployment 6547c7b2 (2026-11-11): the engine started on a synthetic $10,000, sized
the first BUY at ~$1,000, then reconciled to the real paper account (~$100k) on its first fill
and sized every later BUY at ~$10,000 - 19 of them rejected by the Free plan's $1,000 per-trade
limit. Fix (owner's decision): seed the engine from the stored paper account at boot, and reduce
a paper ENTRY to the plan's per-trade limit instead of rejecting it. Live is unchanged.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

from backend_app.backend import master_executor as me
from backend_app.backend.paper.bot_paper_ledger import BotPaperLedger, LedgerFill


def test_an_entry_within_the_limit_is_untouched():
    assert me.clamp_paper_entry_to_plan_limit(Decimal("0.013"), Decimal("74000"), 1000) == (Decimal("0.013"), None)


def test_an_entry_above_the_limit_is_reduced_and_never_exceeds_it():
    qty, note = me.clamp_paper_entry_to_plan_limit(Decimal("0.134435"), Decimal("74380.01"), 1000)
    assert qty == Decimal("0.013444")
    assert qty * Decimal("74380.01") <= 1000
    assert "reduced from 0.134435 to 0.013444" in note


def test_nothing_left_at_this_precision_is_skipped_not_rounded_up():
    qty, note = me.clamp_paper_entry_to_plan_limit(Decimal("5"), Decimal("2000"), 1000)
    assert qty is None and "nothing remains" in note


def test_no_limit_means_no_change():
    assert me.clamp_paper_entry_to_plan_limit(Decimal("1"), Decimal("1"), None) == (Decimal("1"), None)


def test_the_clamp_runs_for_paper_entries_only():
    src = inspect.getsource(me)
    assert "if self.paper_trading and not is_reduce:" in src


def test_the_paper_engine_is_seeded_from_the_ledger_at_boot():
    src = inspect.getsource(me.BotRunner._recover_live_state) if hasattr(me, "BotRunner") else inspect.getsource(me)
    assert "await self._seed_paper_engine_from_ledger(paper_ledger)" in src


def test_snapshot_reads_back_with_no_order():
    ledger = BotPaperLedger("u")
    seen = {}

    def _rb(order, sym, side, key):
        seen.update(order=order, sym=sym)
        return LedgerFill("", sym, "", Decimal(0), Decimal(0), Decimal(0), Decimal("99000"),
                          Decimal("1000"), Decimal(0), Decimal("100000"), "acc")

    ledger._read_back = _rb
    ledger.ledger_symbol = lambda s: s.replace("/", "-")
    snap = ledger.snapshot("BTC/USDT")
    assert seen == {"order": {}, "sym": "BTC-USDT"} and snap.cash == Decimal("100000")
