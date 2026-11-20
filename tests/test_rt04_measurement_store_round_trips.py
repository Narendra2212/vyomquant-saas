"""RT-04 -- the measurement store round-trips, and its honest absence still refuses.

THIS FILE DOES NOT CLOSE RT-04. See ``backend/market_data_measurement_store.py``'s own
module docstring: Requirement 14.4's correctness floor needs >= 99.99% completeness over
>= 10,000 bars of a REAL feed, which is roughly a week of continuous measurement and cannot
be produced here. What this proves is narrower and real: that IF an operator runs that
measurement and records it, the store gets it back byte-for-byte, and that nothing here
invents a measurement nobody made.

WHAT THIS FILE PROVES (fake Supabase double; no network, no real exchange, no real feed)
    * ``record_measurement`` -> ``latest_measurements`` round-trips a ``SourceMeasurement``
      through JSON exactly, including nested ``LatencySummary`` fields;
    * writing twice for the same source returns only the NEWEST row, never both;
    * an unknown source is refused before any write (``ValueError``), not silently recorded;
    * nothing recorded yet is an empty dict, not an exception and not a fabricated pair;
    * a store that cannot be READ (a real error) raises ``MeasurementStoreUnavailable``
      rather than being indistinguishable from "nothing recorded".

NOT SHOWN: a real feed measurement; the correctness floor's own arithmetic (covered by
``tests/test_market_data_latency*.py``); the 10,000-bar sample-size requirement, which no
unit test can exercise without literally running that long.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend_app.backend.market_data_latency import (SOURCE_A, SOURCE_B,
                                                      LatencySummary,
                                                      SourceMeasurement)
from backend_app.backend.market_data_measurement_store import (
    MeasurementStoreUnavailable, latest_measurements, record_measurement)


class _Query:
    def __init__(self, rows, op="select"):
        self._rows, self._op, self._payload = rows, op, None
        self._filters = []
        self._order_desc = False

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def in_(self, col, vals):
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, _col, desc=False):
        self._order_desc = desc
        return self

    def execute(self):
        if self._op == "insert":
            row = dict(self._payload)
            row["recorded_at"] = f"2026-01-01T00:00:{len(self._rows):02d}Z"
            self._rows.append(row)
            return SimpleNamespace(data=[row])
        hit = [r for r in self._rows if all(f(r) for f in self._filters)]
        hit.sort(key=lambda r: r["recorded_at"], reverse=self._order_desc)
        return SimpleNamespace(data=[dict(r) for r in hit])


class FakeSB:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "market_data_source_measurements", name
        return _Query(self.rows)


def _measurement(source=SOURCE_A, *, received_bars=10499, expected_bars=10500):
    return SourceMeasurement(
        source=source, has_validation_layer=False, measured=True,
        symbols=("BTC/USDT",), timeframes=("1m",), window_seconds=604800.0,
        events_observed=10500,
        end_to_end=LatencySummary.from_samples([10.0, 12.0, 15.0, 20.0, 9.0]),
        expected_bars=expected_bars, received_bars=received_bars,
        monotonic_violations=0, duplicates_delivered=0, invalid_ohlc_delivered=0,
        synthetic_candles_delivered=0, uptime_fraction=0.9999, error_count=0,
        silent_stall_episodes=0, notes=["measured against the real public feed"],
    )


@pytest.mark.asyncio
class TestRoundTrip:
    async def test_record_then_read_is_byte_identical(self):
        sb = FakeSB()
        m = _measurement()
        await record_measurement(m, recorded_by="operator@example.com", client_factory=lambda: sb)
        got = await latest_measurements(client_factory=lambda: sb)
        assert got[SOURCE_A].to_dict() == m.to_dict()

    async def test_both_candidates_round_trip_independently(self):
        sb = FakeSB()
        a, b = _measurement(SOURCE_A), _measurement(SOURCE_B, received_bars=10500)
        await record_measurement(a, recorded_by="op", client_factory=lambda: sb)
        await record_measurement(b, recorded_by="op", client_factory=lambda: sb)
        got = await latest_measurements(client_factory=lambda: sb)
        assert set(got) == {SOURCE_A, SOURCE_B}
        assert got[SOURCE_A].completeness != got[SOURCE_B].completeness

    async def test_only_the_newest_row_per_source_is_returned(self):
        sb = FakeSB()
        stale = _measurement(received_bars=1000)  # would fail the floor
        fresh = _measurement(received_bars=10500)  # passes it
        await record_measurement(stale, recorded_by="op", client_factory=lambda: sb)
        await record_measurement(fresh, recorded_by="op", client_factory=lambda: sb)
        got = await latest_measurements(client_factory=lambda: sb)
        assert got[SOURCE_A].received_bars == 10500, (
            "an older, worse measurement must not shadow a newer one"
        )

    async def test_an_unknown_source_is_refused_before_any_write(self):
        sb = FakeSB()
        bad = _measurement(source="not_a_real_candidate")
        with pytest.raises(ValueError):
            await record_measurement(bad, recorded_by="op", client_factory=lambda: sb)
        assert sb.rows == [], "a refused measurement must leave no row behind"

    async def test_nothing_recorded_is_an_empty_dict_not_an_exception(self):
        sb = FakeSB()
        got = await latest_measurements(client_factory=lambda: sb)
        assert got == {}

    async def test_an_unreadable_store_raises_rather_than_reading_as_unmeasured(self):
        class _Boom:
            def table(self, _name):
                raise RuntimeError("connection refused")

        with pytest.raises(MeasurementStoreUnavailable):
            await latest_measurements(client_factory=_Boom)
