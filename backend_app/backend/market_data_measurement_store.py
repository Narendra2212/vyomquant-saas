"""backend/market_data_measurement_store.py -- RT-04: where a REAL measurement would live.

THE DEFECT THIS DOES NOT CLOSE
    ``routers/paper_trading._recorded_measurements()`` returns ``None``, unconditionally,
    because Requirement 14.4's correctness floor (``market_data_latency.CorrectnessFloor``)
    has never been applied to a real measurement of this platform's two candidate feeds.
    Every ``POST /api/paper/sessions`` call resolves ``select_market_data_source(None)`` to
    ``FLOOR_ADMITTED_NOTHING`` and refuses before creating anything.

    That cannot be fixed by writing code. The floor requires >= 99.99% completeness over
    >= 10,000 bars (``CorrectnessFloor.min_completeness_sample_bars``) -- at a 1-minute
    timeframe, roughly seven days of continuous measurement against the real feed, because
    27 bars of 27 received is evidence of nothing at that resolution (see that property's
    own docstring). No chat session, however long, runs for seven wall-clock days inside one
    turn, and inventing a measurement that looks plausible is exactly the failure mode
    ``paper_market_feed.measurement_for`` was written to refuse -- "substituting a plausible
    measurement here would defeat the entire mechanism from the one place nothing downstream
    could detect it."

WHAT THIS MODULE IS
    Somewhere real for an operator's measurement to land, once they run one. Before this
    existed, ``evidence/`` held only ``reports/market_data_latency_decision.md`` -- prose
    for a human, not a value this function could read. This is the missing other half: a
    persisted row, read by the one function that was always going to read it.

    ``record_measurement`` writes one ``SourceMeasurement`` (see
    ``scripts/record_market_data_measurement.py`` for the operator-facing CLI around it).
    ``latest_measurements`` reads the newest row per candidate and reconstructs it via
    ``SourceMeasurement.from_dict`` -- the exact inverse of what was written, so there is one
    schema (the dataclass) rather than two that could drift.

    Both run under the SERVICE role (migration 039 grants no browser role anything on this
    table): this is platform infrastructure, not user data, and a tenant's own JWT has no
    legitimate reason to write or read a measurement of the feed itself.

Regression: tests/test_rt04_measurement_store_round_trips.py
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, Optional

from backend_app.backend.market_data_latency import SOURCE_A, SOURCE_B, SourceMeasurement

logger = logging.getLogger("MarketDataMeasurementStore")

_KNOWN_SOURCES = (SOURCE_A, SOURCE_B)


async def _run(query: Any) -> Any:
    result = query.execute()
    return await result if inspect.isawaitable(result) else result


class MeasurementStoreUnavailable(RuntimeError):
    """The store could not be read or written. Never silently treated as "unmeasured"."""


async def record_measurement(
    measurement: SourceMeasurement,
    *,
    recorded_by: str,
    client_factory: Optional[Callable[[], Any]] = None,
) -> None:
    """Persist one candidate's measurement. Raises rather than swallowing a write failure --
    an operator who believes they recorded a measurement and silently did not is worse off
    than one who sees the error.
    """
    if measurement.source not in _KNOWN_SOURCES:
        raise ValueError(
            f"{measurement.source!r} is not a known candidate; expected one of "
            f"{_KNOWN_SOURCES}"
        )
    sb = client_factory() if client_factory else _service_client()
    await _run(
        sb.table("market_data_source_measurements").insert({
            "source": measurement.source,
            "measured": bool(measurement.measured),
            "window_seconds": measurement.window_seconds,
            "recorded_by": str(recorded_by),
            "payload": measurement.to_dict(),
        })
    )
    logger.warning(
        "RT-04: recorded a %s measurement for %s (measured=%s, window=%ss) by %s",
        "MEASURED" if measurement.measured else "UNMEASURED",
        measurement.source, measurement.measured, measurement.window_seconds, recorded_by,
    )


async def latest_measurements(
    *, client_factory: Optional[Callable[[], Any]] = None,
) -> Dict[str, SourceMeasurement]:
    """The newest recorded measurement per candidate, or an empty dict.

    Empty -- not an exception -- when nothing has ever been recorded: that is the honest,
    expected state until an operator runs the real measurement, and
    ``paper_market_feed.measurement_for`` already treats a missing entry as
    ``unmeasured(...)``, which correctly fails the floor. An unreadable STORE (a real error,
    not "nothing recorded yet") raises, because that case must not be indistinguishable from
    "measured nothing, correctly refused".
    """
    sb = client_factory() if client_factory else _service_client()
    try:
        res = await _run(
            sb.table("market_data_source_measurements")
            .select("source, payload, recorded_at")
            .in_("source", list(_KNOWN_SOURCES))
            .order("recorded_at", desc=True)
        )
    except Exception as exc:  # noqa: BLE001
        raise MeasurementStoreUnavailable(str(exc)) from exc

    rows = list(getattr(res, "data", None) or [])
    out: Dict[str, SourceMeasurement] = {}
    for row in rows:
        source = row.get("source")
        if source in out or source not in _KNOWN_SOURCES:
            continue  # rows are newest-first; the first one seen per source is the latest
        try:
            out[source] = SourceMeasurement.from_dict(row["payload"])
        except Exception as exc:  # noqa: BLE001
            logger.error("RT-04: stored measurement for %s is unreadable: %s", source, exc)
    return out


def _service_client() -> Any:
    from backend_app.core.dependencies import get_service_supabase

    return get_service_supabase()
