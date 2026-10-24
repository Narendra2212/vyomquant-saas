"""RECON-01: the platform does not invent an execution it cannot attribute.

THE DECISION
============
RECON-01's remaining_action named exactly one open item, and called it an owner's policy
decision rather than engineering:

    "should the platform create an execution_records row for a venue order it cannot
     attribute to any strategy (_handle_ghost_order's current behaviour --
     strategy_id='reconciled', price=None, status EXECUTING, no risk check)?"

The answer is NO. This file is that answer made executable, which is the difference
between a decision and a note: the previous state of the entry would have been "finished
as-is" on the strength of the service never being started, and the fabricating code would
have sat there waiting for whoever started it. A decision recorded only in a ledger is not
enforced by anything.

WHY NO
======
``execution_records`` is the strategy-execution ledger. A row in it means "this platform
executed this". For a venue order the platform cannot explain, that is the one claim it is
not entitled to make, and every field of the row it used to write was false:

  strategy_id="reconciled"  names no strategy; anything grouping exposure or performance
                            by strategy gained a phantom one
  price=None                on an EXECUTING record, leaving P&L and risk without their
                            one required input
  status=EXECUTING          asserts this platform is executing it; it is not
  (no risk check)           so the row contributed to exposure having never been
                            authorised against a limit

A reconciler detects divergence between two systems of record. It does not resolve that
divergence by editing one of them to agree with the other.

WHAT "NO" MUST NOT MEAN
=======================
Ignoring it. An unattributable OPEN order is real exposure on a real account, and the two
plausible causes -- a manual trade in the exchange UI, or this platform's own order whose
database write failed after the venue accepted it -- are indistinguishable from inside the
reconciler. The second is dangerous. So the refusal is paired with a loud report, and
these tests pin both halves: nothing is written, AND nothing is swallowed.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock

import pytest

from backend_app.backend import exchange_reconciliation as er
from backend_app.backend.exchange_reconciliation import (
    ExchangeReconciliationService, ReconciliationAction)

TENANT = "11111111-2222-3333-4444-555555555555"


def _venue_order(order_id="V-ORPHAN"):
    return {
        "order_id": order_id,
        "symbol": "BTC/USDT",
        "side": "buy",
        "size": 0.5,
        "filled_size": 0.1,
        "avg_price": 65000.0,
        "remaining_size": 0.4,
        "status": "open",
    }


def _service():
    """A service whose database would RAISE if anything tried to write through it.

    The strongest available statement of "writes nothing": not "we counted the rows and
    they did not change", but "any attempt to add or commit is an error". A test that
    counted rows could pass against code that wrote and then rolled back, which would
    still have taken a lock and still have been wrong about what it is entitled to do.
    """
    db = MagicMock()
    db.add.side_effect = AssertionError(
        "db.add() was called: the reconciler tried to create a row for a venue order it "
        "cannot attribute to a strategy. RECON-01's answer is NO."
    )
    db.commit.side_effect = AssertionError(
        "db.commit() was called on the unattributed-order path; nothing should be written."
    )
    return ExchangeReconciliationService(db_session=db, exchange_client=MagicMock())


def _report(svc=None):
    svc = svc or _service()
    return asyncio.run(svc._report_unattributed_order(TENANT, _venue_order()))


# ══════════════════════════════════════════════════════════════════════════════
#  1. NOTHING IS WRITTEN
# ══════════════════════════════════════════════════════════════════════════════


def test_no_row_is_created_for_an_unattributable_venue_order():
    result = _report()
    assert result.action == ReconciliationAction.REPORT_UNATTRIBUTED_ORDER


def test_the_insert_action_no_longer_exists_in_the_vocabulary():
    """Removed rather than left unused. An enum member naming a forbidden write is an
    invitation to emit it again, and a reviewer reading the enum would reasonably assume
    something does."""
    names = {a.name for a in ReconciliationAction}
    assert "INSERT_GHOST_ORDER" not in names, (
        "the action that named the fabricating write is still in the vocabulary"
    )
    assert "REPORT_UNATTRIBUTED_ORDER" in names


def test_no_code_path_in_the_module_fabricates_a_reconciled_strategy_id():
    """Via ast, not a substring search.

    The module and this file both DISCUSS ``strategy_id="reconciled"`` at length in order
    to explain why it is gone, so a text search finds the explanation and calls it the
    offence. What matters is whether any executable expression passes that value as a
    ``strategy_id``, which is a question about the syntax tree.
    """
    import ast

    tree = ast.parse(inspect.getsource(er))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "strategy_id":
            continue
        if isinstance(node.value, ast.Constant) and node.value.value == "reconciled":
            offenders.append(getattr(node.value, "lineno", "?"))
    assert not offenders, (
        f"strategy_id='reconciled' is still passed as a keyword at line(s) {offenders} "
        f"-- an execution attributed to a strategy that does not exist"
    )


def test_no_execution_record_model_is_constructed_in_this_module_at_all():
    """The broader property, and the one that survives a rename of the literal: this
    module must not build an execution record. Updating existing rows is its job;
    creating them is the execution path's."""
    import ast

    tree = ast.parse(inspect.getsource(er))
    constructions = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "ExecutionRecordModel"
    ]
    assert not constructions, (
        f"ExecutionRecordModel(...) is constructed at line(s) {constructions}. A "
        f"reconciler detects divergence between two systems of record; it does not "
        f"resolve it by creating rows in one of them."
    )


def test_no_code_path_mints_a_synthetic_execution_id():
    source = inspect.getsource(er)
    assert 'f"exec_ghost_' not in source and "f'exec_ghost_" not in source, (
        "an exec_ghost_* execution_id is still being minted; a fabricated id is how a "
        "reader ends up believing a row exists"
    )


def test_the_state_machine_is_not_seeded_for_a_nonexistent_execution():
    """The old code also wrote OrderState.SUBMITTED into the in-memory machine under the
    fabricated key, so the machine held a state for an execution that did not exist."""
    svc = _service()
    before = dict(getattr(svc.state_machine, "_current_states", {}))
    _report(svc)
    after = dict(getattr(svc.state_machine, "_current_states", {}))
    assert after == before, (
        f"the state machine gained {set(after) - set(before)}: a state for an execution "
        f"that has no row"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  2. NOTHING IS SWALLOWED EITHER
# ══════════════════════════════════════════════════════════════════════════════
#  Refusing to write must not become refusing to notice. This is real exposure on a
#  real account.


def test_the_order_is_reported_at_error_level(caplog):
    with caplog.at_level("INFO"):
        _report()
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors, (
        "an unattributable OPEN order at the venue was not reported at error level. "
        "Declining to write a row is not the same as declining to notice."
    )


def test_the_report_carries_every_field_the_venue_gave(caplog):
    """The venue's own identifiers and the order's shape, so an operator can find it at
    the exchange without the platform having a row for it.

    The TENANT id is deliberately not asserted in full: the platform's log scrubber
    redacts UUID tails (observed: ``tenant=11111111-2222-3333-[redacted]``), which is
    correct and is not this test's business to defeat. The leading segment is checked
    instead, which is enough to identify the account in combination with the venue order
    id.
    """
    with caplog.at_level("ERROR"):
        _report()
    text = " ".join(r.getMessage() for r in caplog.records)
    for fragment in ("V-ORPHAN", "BTC/USDT", "buy", TENANT.split("-")[0]):
        assert fragment in text, f"the report does not name {fragment!r}"


def test_the_report_says_that_nothing_was_written(caplog):
    """An operator reading it must not be left wondering whether the platform took the
    order over."""
    with caplog.at_level("ERROR"):
        _report()
    text = " ".join(r.getMessage() for r in caplog.records).upper()
    assert "NO ROW" in text


def test_the_result_is_returned_as_a_discrepancy_not_dropped():
    result = _report()
    assert result.discrepancy, (
        "the result carries no discrepancy text, so a caller aggregating reconciliation "
        "findings has nothing to show"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  3. THE ABSENT EXECUTION IS REPRESENTED AS ABSENT
# ══════════════════════════════════════════════════════════════════════════════


def test_the_result_has_no_execution_id_because_there_is_no_execution():
    assert _report().execution_id is None


def test_the_result_carries_the_venue_id_in_the_venue_field():
    result = _report()
    assert result.exchange_order_id == "V-ORPHAN"


def test_a_missing_execution_id_is_never_stamped_as_synced():
    """`_update_sync_timestamp` receives the results' execution ids. A None would become
    `execution_id = ANY(ARRAY[NULL])` -- matching nothing, while still committing a
    transaction derived from an absence."""
    db = MagicMock()
    svc = ExchangeReconciliationService(db_session=db, exchange_client=MagicMock())
    svc._update_sync_timestamp([None, None])
    assert not db.execute.called, "a sync timestamp was issued for no rows"
    assert not db.commit.called


def test_a_real_execution_id_is_still_stamped():
    """Positive control: the filter must not disable the stamping it guards."""
    db = MagicMock()
    svc = ExchangeReconciliationService(db_session=db, exchange_client=MagicMock())
    svc._update_sync_timestamp([None, "exec-real"])
    assert db.execute.called, "the surviving id was not stamped"
    args = db.execute.call_args[0]
    assert args[1]["execution_ids"] == ["exec-real"]


# ══════════════════════════════════════════════════════════════════════════════
#  4. NEGATIVE CONTROL
# ══════════════════════════════════════════════════════════════════════════════


def test_negative_control_the_write_guard_would_actually_fire():
    """These tests assert "nothing was written" against a database double whose add and
    commit raise. If that double were inert, every assertion above would pass for free.
    Proves the trap is armed."""
    svc = _service()
    with pytest.raises(AssertionError, match="db.add\\(\\) was called"):
        svc.db.add(object())
    with pytest.raises(AssertionError, match="db.commit\\(\\) was called"):
        svc.db.commit()


def test_negative_control_the_source_scan_can_find_a_literal():
    """The two source scans look for strings that are now absent. Confirm the same
    technique finds a string that IS present, so "absent" means absent rather than
    "inspect.getsource returned nothing useful"."""
    source = inspect.getsource(er)
    assert "REPORT_UNATTRIBUTED_ORDER" in source
    assert "_report_unattributed_order" in source
