"""PAPER-08 -- a paper balance read from the deployed database is exact, not a float.

Production: ``GET /api/paper/account``, ``/summary`` and ``/positions`` answered 500 for a brand-new
user with ``InvalidQuantity: available_balance must be an exact decimal value, not a float
(100000.0)``. PostgREST renders ``numeric`` as a JSON number and the HTTP client parses it to a
float; the accounting engine refuses floats. The existing suites read through a driver that returns
``Decimal`` for ``numeric``, so none of them saw it.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend_app.backend.paper import paper_accounting as accounting
from backend_app.backend.paper import paper_repository as repo


class _Query:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return SimpleNamespace(data=self._data, error=None)


def test_a_float_balance_off_the_wire_becomes_the_exact_decimal():
    (row,) = repo._execute(_Query([{"available_balance": 100000.0, "version": 3}]), "accounts read")
    assert row["available_balance"] == Decimal("100000.0")
    assert isinstance(row["available_balance"], Decimal)
    # The engine accepts it - this is the call that raised in production.
    assert accounting.to_decimal(row["available_balance"], "available_balance") == Decimal("100000")


def test_a_float_keeps_the_digits_it_was_stored_with():
    (row,) = repo._execute(_Query([{"price": 0.1, "quantity": 1234.56789012}]), "x")
    assert row["price"] == Decimal("0.1"), "0.1 must not become 0.1000000000000000055..."
    assert row["quantity"] == Decimal("1234.56789012")


def test_integers_strings_and_text_pass_through_untouched():
    (row,) = repo._execute(
        _Query([{"version": 7, "currency": "USD", "id": "abc", "closed_at": None, "ok": True}]), "x"
    )
    assert row == {"version": 7, "currency": "USD", "id": "abc", "closed_at": None, "ok": True}
    assert row["ok"] is True and isinstance(row["version"], int)


def test_a_single_mapping_response_is_normalised_too():
    (row,) = repo._rows(SimpleNamespace(data={"total_equity": 99999.5}, error=None), "x")
    assert row["total_equity"] == Decimal("99999.5")


def test_a_non_finite_float_is_still_refused_by_the_engine():
    (row,) = repo._execute(_Query([{"available_balance": float("nan")}]), "x")
    with pytest.raises(accounting.InvalidQuantity):
        accounting.to_decimal(row["available_balance"], "available_balance")


def test_jsonb_payloads_are_left_as_stored():
    payload = {"price": "101.25"}
    (row,) = repo._execute(_Query([{"payload": payload}]), "x")
    assert row["payload"] == payload
