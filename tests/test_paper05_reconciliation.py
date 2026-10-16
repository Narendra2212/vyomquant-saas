"""
tests/test_paper05_reconciliation.py - PAPER-05, the repair half.

PAPER-05: "A paper fill is seven separate PostgREST writes with no transaction and no repair."
``paper_simulator.apply_fill``'s own docstring ("THE WRITES, AND THE ORDERING THE MISSING
TRANSACTION FORCES") already documents why the writes are deliberately resumable rather than
wrapped in one transaction, and why a crash past the point of no return is reported as
``PaperConcurrencyExhausted(partial_write=True)`` -- that is a considered tradeoff, not the gap.

The gap is that the exception detail naming a partial write reaches only the one HTTP caller of
the one request that hit it, with no durable record left behind, so nobody can find an affected
session afterward. ``backend/paper/paper_reconciliation.py`` closes it: it compares a session's
live rows against ``paper_replay.replay``'s independent reconstruction from the append-only event
and order log, the same contract ``tests/test_paper_replay.py`` already proves a healthy session
satisfies exactly. This file proves:

1. A session driven entirely through the production loop reconciles clean -- no mismatch, on any
   subject -- using the SAME real session the existing replay tests already trust
   (``tests/test_paper_replay.py::_run_the_fixture_session``).
2. ``project_stored_state`` produces byte-identical output to that other test file's own
   ``_stored`` projection of the identical rows, so this module's reading of the live tables is not
   a second, drifting opinion about what they mean.
3. A tampered balance, a tampered order and a tampered position are each caught, and named under
   the right subject -- this is the whole point: an inconsistency a partial write could leave
   behind must be visible rather than silently believed.
4. Unknown and other-tenant sessions answer ``None``, identically, exactly as ``replay`` itself
   does -- the disclosure contract Requirements 21.2/21.5 ask of every session read.
5. Reconciling writes nothing. It is an audit, and must not be able to alter what it audits.
"""

from __future__ import annotations

import copy
from decimal import Decimal

from backend_app.backend.paper import paper_reconciliation as recon
from backend_app.backend.paper import paper_replay as replay
from tests.test_paper_replay import (
    OTHER_USER,
    SESSION,
    USER,
    _pinned_feed_clock,  # noqa: F401 -- pytest fixture, used by name via the parameter below
    _run_the_fixture_session,
    _stored,
)


def test_a_clean_session_reconciles_with_no_mismatch(_pinned_feed_clock) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)

    report = recon.reconcile_session(supabase, USER, SESSION)

    assert report is not None
    assert report.clean, (
        f"a session driven entirely through the production loop should reconcile clean; "
        f"mismatches: {report.mismatches}"
    )
    assert report.mismatches == ()
    assert report.events_replayed > 0


def test_project_stored_state_matches_the_replay_tests_own_projection(_pinned_feed_clock) -> None:
    """This module's reading of the live tables is not a second opinion about what they mean.

    ``tests/test_paper_replay.py::_stored`` is the projection the existing replay-fidelity test
    already trusts. Rebuilding the same fixture's rows through this module's
    ``project_stored_state`` -- which reads through ``paper_repository`` rather than off
    ``FakeSupabase``'s in-memory lists directly -- must produce exactly the same dictionary, field
    for field, or the two files disagree about what "the stored state" means.
    """
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)

    result = replay.replay(supabase, USER, SESSION)
    assert result is not None
    reference = _stored(supabase)
    mine = recon.project_stored_state(supabase, USER, SESSION, currency=result.currency)

    assert mine == reference, (
        "paper_reconciliation.project_stored_state disagrees with test_paper_replay's own "
        "_stored projection of the identical rows"
    )


def test_a_tampered_balance_is_caught(_pinned_feed_clock) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    account = supabase.accounts[0]
    account["available_balance"] = str(Decimal(str(account["available_balance"])) + Decimal("1"))

    report = recon.reconcile_session(supabase, USER, SESSION)

    assert report is not None
    assert not report.clean
    assert "balances" in report.mismatches
    assert report.stored["balances"] != report.reconstructed["balances"]


def test_a_tampered_order_is_caught(_pinned_feed_clock) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    assert supabase.orders, "the fixture should have recorded at least one order"
    order = supabase.orders[0]
    order["filled_quantity"] = str(Decimal(str(order["filled_quantity"])) + Decimal("1000"))

    report = recon.reconcile_session(supabase, USER, SESSION)

    assert report is not None
    assert not report.clean
    assert "orders" in report.mismatches


def test_a_tampered_position_is_caught(_pinned_feed_clock) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    assert supabase.positions, "the fixture should have recorded at least one position"
    position = supabase.positions[0]
    position["size"] = str(Decimal(str(position["size"])) + Decimal("1000"))

    report = recon.reconcile_session(supabase, USER, SESSION)

    assert report is not None
    assert not report.clean
    assert "positions" in report.mismatches


def test_an_unknown_or_other_tenants_session_answers_none(_pinned_feed_clock) -> None:
    """One answer for "no such session" and "not yours", exactly as ``replay`` itself gives.

    ``reconcile_session`` calls ``replay.replay`` first and returns its ``None`` unchanged rather
    than resolving ownership a second time, so it cannot disclose more than ``replay`` already
    does not.
    """
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    unknown_id = "44444444-4444-4444-8444-444444444444"

    assert recon.reconcile_session(supabase, OTHER_USER, SESSION) is None
    assert recon.reconcile_session(supabase, OTHER_USER, unknown_id) is None
    assert recon.reconcile_session(supabase, USER, unknown_id) is None


def test_reconciling_writes_nothing(_pinned_feed_clock) -> None:
    supabase, _config, _account_id = _run_the_fixture_session(_pinned_feed_clock)
    names = (
        "accounts",
        "orders",
        "fills",
        "positions",
        "trades",
        "equity_snapshots",
        "balance_events",
        "market_events",
        "events",
        "sessions",
    )
    before = copy.deepcopy({name: getattr(supabase, name) for name in names})

    report = recon.reconcile_session(supabase, USER, SESSION)

    assert report is not None
    after = {name: getattr(supabase, name) for name in names}
    assert after == before, "reconcile_session must not write anything -- it is an audit"
