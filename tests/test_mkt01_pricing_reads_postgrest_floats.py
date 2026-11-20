"""MKT-01: pricing evidence read through PostgREST arrives as floats and was refused.

``marketplace_backtest_evidence``'s metric columns are ``numeric(20,8)``. PostgREST renders a
``numeric`` as a JSON number and the HTTP client parses a fractional one into a binary ``float``.
``pricing_evaluator`` refuses a ``float`` by design (Requirement 8.13), so in production
``POST /api/library/submissions/{id}/price-range`` and ``/price`` answered 422
``MARKETPLACE_PRICE_EVIDENCE_MISSING`` for EVERY submission: nothing could be priced, so nothing
could be published. CI did not see it for the reason PAPER-08 was not seen: its fakes (and its
Postgres driver) return ``Decimal`` for ``numeric``, which is not what the deployed client does.

This drives the loader the way production does -- floats in, as they come off the wire -- and
holds the evaluator and the digest to the same answer they give for the exact decimals.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from backend_app.backend.marketplace import pricing_evaluator as pe
from backend_app.routers import library as lib


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=self._rows)


class _Svc:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _Query(self._rows)


def _wire_row(index: int) -> dict:
    """One evidence row exactly as the deployed PostgREST client hands it back."""
    return {
        "source_backtest_id": f"00000000-0000-0000-0000-00000000000{index}",
        "condition_index": index,
        "total_return_pct": 18.4521 + index,  # float: numeric(20,8) on the wire
        "sharpe_ratio": 1.4367,
        "sortino_ratio": 1.9012,
        "max_drawdown_pct": 6.4690,
        "win_rate_pct": 58.3333,
        "profit_factor": 1.3459,
        "total_trades": 94,
        "start_date": "2025-01-01",
        "end_date": "2025-06-30",
        "dataset_checksum": "a" * 64,
        "dag_hash": "b" * 64,
        "executed_bar_count": 4000,
        "final_capital": 11845.21,  # float
    }


def _loaded(rows):
    return lib._load_pricing_conditions("sub", "owner", _Svc(rows))


def test_float_evidence_is_priced_not_refused() -> None:
    rows = [_wire_row(i) for i in (1, 2, 3)]

    result = lib._price_range_or_error(_loaded(rows), "USD")

    assert result is not None
    assert result.minimum_price_minor <= result.recommended_price_minor <= result.maximum_price_minor


def test_the_digest_of_float_evidence_is_computed_and_stable() -> None:
    rows = [_wire_row(i) for i in (1, 2, 3)]

    first = pe.inputs_digest(_loaded(rows))
    second = pe.inputs_digest(_loaded(list(reversed(rows))))

    assert first == second and len(first) == 64


def test_a_float_is_converted_to_the_decimal_it_was_parsed_from() -> None:
    loaded = _loaded([_wire_row(1)])[0]

    assert loaded["sharpe_ratio"] == Decimal("1.4367")
    assert isinstance(loaded["sharpe_ratio"], Decimal)
    assert loaded["total_trades"] == 94 and isinstance(loaded["total_trades"], int)
    assert loaded["start_date"] == "2025-01-01"
