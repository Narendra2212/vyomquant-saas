"""
tests/test_paper_noise_and_authored_quantity.py

Two active production issues from the Sentry review:

PAPER-QTY-01 (VYOMQUANT-API-PRODUCTION-13, 54 events in an hour): a paper session discarded
every signal whose action node STATED a quantity. The quantity is an author-typed number
stored in the version's JSON, so it arrives as a float, and ``paper_signal`` refuses floats
(Requirement 18.1). The author's decimal is now recovered exactly AT THE PLAN BOUNDARY
(``paper_session_runtime``), so ``paper_signal`` keeps refusing every other float - as
``test_task_27_session_service::test_a_float_quantity_is_refused_rather_than_converted`` pins.

PAPER-01-TOL (VYOMQUANT-API-PRODUCTION-D, 126 FATAL events/day): the paper reconciliation
check compared the ledger's cent-rounded total_equity with this process's unrounded
recomputation using exact ``!=``, so every fill logged a "disagreement" of under a cent.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend_app.backend.paper_session_runtime import authored_decimal as _authored_decimal
from backend_app.core.execution_engine import _beyond_ledger_precision


@pytest.mark.parametrize("typed, exact", [
    (0.0001, Decimal("0.0001")),
    (1e-07, Decimal("1E-7")),
    (0.1, Decimal("0.1")),
    (2.5, Decimal("2.5")),
    (1.0, Decimal("1.0")),
])
def test_an_authored_float_becomes_the_decimal_the_author_typed(typed, exact):
    got = _authored_decimal(typed)
    assert isinstance(got, Decimal) and got == exact


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_float_is_left_for_the_engine_to_refuse(bad):
    got = _authored_decimal(bad)
    assert isinstance(got, float)


@pytest.mark.parametrize("other", [True, None, "0.5", 3, Decimal("0.5")])
def test_non_floats_pass_through_unchanged(other):
    assert _authored_decimal(other) is other


def test_the_session_boundary_still_refuses_a_float():
    """The conversion lives at the plan boundary only; paper_signal is unchanged."""
    from backend_app.backend.paper import paper_session_service as service

    assert not hasattr(service, "_authored_decimal")


def test_the_paper_signal_accepts_the_converted_quantity():
    from backend_app.backend.paper import paper_accounting as accounting

    assert accounting.to_decimal(_authored_decimal(0.0001), "q") == Decimal("0.0001")
    with pytest.raises(accounting.PaperAccountingError):
        accounting.to_decimal(0.0001, "q")     # the raw float is still refused


@pytest.mark.parametrize("recomputed, recorded, beyond", [
    (Decimal("99999.8947064863105"), Decimal("99999.89"), False),   # the production case
    (Decimal("100000.011100250"), Decimal("100000.01"), False),
    (Decimal("99999.865962"), Decimal("99999.87"), False),
    (Decimal("100000.05"), Decimal("100000.01"), True),             # a real divergence
    (Decimal("99990.00"), Decimal("100000.00"), True),
    (Decimal("100000.30"), Decimal("100000"), True),                # never looser than a cent
    (Decimal("100000.004"), Decimal("100000"), False),
    (Decimal("1"), "not-a-number", True),                           # unreadable = disagreement
    (Decimal("NaN"), Decimal("1"), True),
])
def test_reconciliation_compares_at_the_ledgers_precision(recomputed, recorded, beyond):
    assert _beyond_ledger_precision(recomputed, recorded) is beyond


# ── WS-NOISE-01 ───────────────────────────────────────────────────────────────────────


def test_client_disconnects_are_not_errors():
    from enum import IntEnum

    from backend_app.backend.ws_event_stream import _is_client_disconnect

    class CloseCode(IntEnum):
        NO_STATUS_RCVD = 1005
        ABNORMAL_CLOSURE = 1006
        INTERNAL_ERROR = 1011

    class WebSocketDisconnect(Exception):
        def __init__(self, code):
            self.code = code

    # The exact shape Sentry recorded: "(<CloseCode.NO_STATUS_RCVD: 1005>, '')".
    assert _is_client_disconnect(RuntimeError(CloseCode.NO_STATUS_RCVD, "")) is True
    assert _is_client_disconnect(RuntimeError(CloseCode.ABNORMAL_CLOSURE, "")) is True
    assert _is_client_disconnect(WebSocketDisconnect(1001)) is True
    # A server-side close or any other exception stays an error.
    assert _is_client_disconnect(RuntimeError(CloseCode.INTERNAL_ERROR, "")) is False
    assert _is_client_disconnect(ValueError("bad json")) is False


def test_the_plan_boundary_converts_the_intents_authored_quantity():
    """The adapter reads `quantity`/`limit_price` off the plan intent through authored_decimal."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "backend_app" / "backend"
           / "paper_session_runtime.py").read_text(encoding="utf-8")
    assert "signal[field] = authored_decimal(value)" in src
