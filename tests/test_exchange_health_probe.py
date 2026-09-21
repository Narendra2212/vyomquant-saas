"""
tests/test_exchange_health_probe.py - production-launch-hardening task 1, CLUSTER A.

Requirements 1.2, 1.3 (exploration, sections 1-2) and 3.3 (preservation, section 3).
`design.md` §Hypothesized Root Cause (wave 1).

WHAT THIS FILE IS
-----------------
Sections 1 and 2 are **bug condition exploration tests** and are EXPECTED TO FAIL against the
current tree (`F`). The failure is the deliverable.

Section 3 is **preservation**, written here rather than in a separate file because task 2 asks for
it beside these assertions: it pins what `F` already gets right, so the fix cannot buy honesty by
making every credential look broken. Section 3 PASSES on `F` and must keep passing on `F'`. Every
test there is named `test_preserved_*`.

THE DEFECT  (`get_exchange_data` :615-625)
------------------------------------------
The loop over `exchange_keys` rows writes a health report for each one without probing anything::

    exchange_health.append({
        "exchange_id": exchange_id,
        "status": "connected",     # <- literal
        "latency_ms": 35,          # <- literal
        "last_sync": conn.get("updated_at"),
    })

`status` and `latency_ms` are answers to questions nobody asked. `35` is not a rounded estimate or
a cached measurement; it is the same integer for every exchange, every user and every request. A
key that was revoked at the venue an hour ago reports `connected` with 35 ms of latency.

And one line further down::

    "can_trade": len(connected_exchanges) > 0

`can_trade` answers *"does a row exist in `exchange_keys`?"*, not *"can this account place an
order?"*. Those come apart the moment a key is revoked, expires, loses its trade permission, or was
saved but never validated. The row is still there, so `F` says yes. Note also that the select at
:597 asks only for `exchange_id, updated_at` - the validity of the credential is never even read,
so this function could not answer the real question with the data it fetches.

THE HONEST MODEL, IN THE SAME CLASS
-----------------------------------
`get_health_status` (:1456-1486) does this correctly and is the convention the expectations below
are taken from, not a preference invented by this spec:

* `latency_ms = None` and `latency_status = "unavailable"` until something is actually measured;
* it reads the measurement from `exchange_health:{user_id}:*` in Redis - a figure a probe wrote;
* it classifies **only** when it has one: `< 150` optimal, `< 500` normal, else degraded.

So "unprobed" already has a spelling in this file. `get_exchange_data` just does not use it.
"""

import pytest
from unittest.mock import AsyncMock, patch

from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService


#: The hardcoded latency at :622. Named so an assertion reads as "the fabrication", not "35".
FABRICATED_LATENCY_MS = 35

#: `get_health_status`' thresholds (:1471-1476), which section 3 pins and the fix must adopt.
OPTIMAL_CEILING_MS = 150
NORMAL_CEILING_MS = 500


def _router_import_error():
    """Why `backend_app.routers.risk` cannot be imported here, or `None` if it can.

    `get_health_status` imports `is_user_kill_switched` from the risk router inside its body, so
    section 3's classification tests need it. Probed rather than assumed, as
    `test_realized_pnl_projection.py` and `test_positions_degraded_projection.py` both do, so a
    run that could not import is reported as skipped rather than as passing.
    """
    try:
        import backend_app.routers.risk  # noqa: F401
    except Exception as exc:  # the reason is the useful part, so it is carried into the skip
        return f"{type(exc).__name__}: {exc}"
    return None


_ROUTER_IMPORT_ERROR = _router_import_error()

requires_routers = pytest.mark.skipif(
    _ROUTER_IMPORT_ERROR is not None,
    reason="backend_app.routers.risk is not importable here: " + str(_ROUTER_IMPORT_ERROR),
)


@pytest.fixture
def mock_user():
    return {
        "id": "test_user_wave1_health",
        "email": "trader@vyomquant.com",
        "access_token": "valid_jwt_token",
    }


@pytest.fixture
def dashboard_service():
    return DashboardAggregationService()


# ──────────────────────────────────────────────────────────────────────────
# The `exchange_keys` double
# ──────────────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """A PostgREST chain that records its projection and answers with the rows it was given.

    `_execute_sb_query` calls `.execute()` on a thread (`:449-456`), so `execute` is sync - the
    same shape `tests/test_paper_repository.FakeSupabase` presents.

    It returns the FULL row rather than the requested projection on purpose. `F` selects only
    `exchange_id, updated_at`, so if this double projected faithfully the validity columns would
    be invisible and the test could not tell whether the defect is "the data was not fetched" or
    "the data was fetched and ignored". Handing over everything means a `can_trade` of `True`
    cannot be excused by the row not carrying the answer. `selects` records what was actually
    asked for, which is the evidence for the first of those two.
    """

    def __init__(self, rows, selects):
        self.rows = rows
        self.selects = selects

    def select(self, columns, **kwargs):
        self.selects.append(columns)
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def execute(self):
        return _Result([dict(row) for row in self.rows])


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.selects = []

    def table(self, name):
        if name != "exchange_keys":
            return _Query([], self.selects)
        return _Query(self.rows, self.selects)


#: A credential saved but never validated, and never health-probed. The row exists; nothing about
#: it has been checked. `last_validated_at` is null and there is no `exchange_health:*` key.
UNPROBED_KEY = {
    "exchange_id": "binance",
    "updated_at": "2024-05-17T09:30:00+00:00",
    "is_active": True,
    "validation_status": "unvalidated",
    "last_validated_at": None,
    "can_trade": None,
}

#: A credential revoked at the venue. The row is still in `exchange_keys` - revocation happens on
#: the exchange, not in this table - which is exactly why row existence cannot stand in for
#: tradability.
REVOKED_KEY = {
    "exchange_id": "bybit",
    "updated_at": "2024-05-10T11:00:00+00:00",
    "is_active": False,
    "validation_status": "revoked",
    "last_validated_at": "2024-05-10T11:00:00+00:00",
    "can_trade": False,
}

#: A credential that was validated, is reachable, and carries trade permission. Section 3's
#: subject: this one genuinely is connected and genuinely can trade.
VALID_KEY = {
    "exchange_id": "binance",
    "updated_at": "2024-05-17T09:30:00+00:00",
    "is_active": True,
    "validation_status": "valid",
    "last_validated_at": "2024-05-17T09:29:00+00:00",
    "can_trade": True,
}


def _measured_latency(latency_ms):
    """Redis holding one real probe result, as `get_health_status` :1461-1470 reads it.

    The key shape is `exchange_health:{user_id}:{exchange}` and the value is the JSON a probe
    wrote. This is a *measurement*: the whole distinction sections 1-2 are about.
    """
    import json

    return (
        patch(
            "backend_app.core.cache.redis_manager.redis_manager.keys",
            new_callable=AsyncMock,
            return_value=["exchange_health:test_user_wave1_health:binance"],
        ),
        patch(
            "backend_app.core.cache.redis_manager.redis_manager.get",
            new_callable=AsyncMock,
            return_value=json.dumps({"latency_ms": latency_ms, "status": "connected"}),
        ),
    )


def _no_probe_results():
    """Redis holding no `exchange_health:*` keys - nothing has ever been probed for this user."""
    return patch(
        "backend_app.core.cache.redis_manager.redis_manager.keys",
        new_callable=AsyncMock,
        return_value=[],
    )


def _exchange(row, data):
    """The health entry for `row`'s exchange in an `get_exchange_data` response."""
    matches = [e for e in data["exchanges"] if e["exchange_id"] == row["exchange_id"]]
    assert matches, f"{row['exchange_id']} is missing from {data['exchanges']}"
    return matches[0]


# ══════════════════════════════════════════════════════════════════════════
# 1. AN UNPROBED KEY  (`get_exchange_data` :615-625, Requirement 1.2)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_unprobed_exchange_reports_unknown_status_and_no_latency(
    mock_user, dashboard_service
):
    """Nothing has been probed, so there is no status and no latency. `"unknown"` and `None`.

    COUNTEREXAMPLE OBSERVED ON `F` (dashboard_aggregation_service.py:618-623)::

        {"exchange_id": "binance", "status": "connected", "latency_ms": 35,
         "last_sync": "2024-05-17T09:30:00+00:00"}

    No probe ran. Redis holds no `exchange_health:*` key for this user. The credential has never
    been validated (`validation_status: "unvalidated"`, `last_validated_at: null`). `F` reports a
    live connection with 35 ms of latency because the loop body is two literals.

    `"unknown"` and `None` are `get_health_status`' own spelling for the same condition
    (`latency_ms = None`, `"unavailable"`, :1461-1462) - the expectation is this file's existing
    convention, not a new one.
    """
    sb = _FakeSupabase([UNPROBED_KEY])

    with _no_probe_results():
        data = await dashboard_service.get_exchange_data(mock_user, sb=sb)

    entry = _exchange(UNPROBED_KEY, data)

    assert entry["status"] == "unknown", (
        f"no probe has run for this exchange, so its status is unknown. `F` reports "
        f"{entry['status']!r} - the literal at :620."
    )
    assert entry["latency_ms"] is None, (
        f"no latency has been measured, so there is none to report. `F` reports "
        f"{entry['latency_ms']!r} - the literal at :621, identical for every exchange, every "
        f"user and every request."
    )


@pytest.mark.asyncio
async def test_no_exchange_row_carries_the_hardcoded_latency(mock_user, dashboard_service):
    """Two unprobed exchanges, and `F` gives both the same invented number.

    COUNTEREXAMPLE OBSERVED ON `F`: `latency_ms: 35` on BOTH rows - `binance` and `bybit`,
    different venues, different continents, different round trips, one integer. That the figure
    does not vary across exchanges is the clearest evidence it was never measured, and it is why
    this is asserted across the whole list rather than on one entry.
    """
    sb = _FakeSupabase([UNPROBED_KEY, REVOKED_KEY])

    with _no_probe_results():
        data = await dashboard_service.get_exchange_data(mock_user, sb=sb)

    fabricated = [
        entry["exchange_id"]
        for entry in data["exchanges"]
        if entry.get("latency_ms") == FABRICATED_LATENCY_MS
    ]
    assert fabricated == [], (
        f"{FABRICATED_LATENCY_MS} ms was reported for {fabricated} without a probe having run"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. A REVOKED KEY  (`can_trade = len(connections) > 0`, :625, Requirement 1.3)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_revoked_key_cannot_trade(mock_user, dashboard_service):
    """The only credential on the account was revoked at the venue. `can_trade` is `False`.

    COUNTEREXAMPLE OBSERVED ON `F` (dashboard_aggregation_service.py:625)::

        {"total_exchanges": 1, "connected_exchanges": 1, "can_trade": true,
         "exchanges": [{"exchange_id": "bybit", "status": "connected", "latency_ms": 35, ...}]}

    `can_trade = len(connected_exchanges) > 0` answers "does a row exist in `exchange_keys`?".
    Revocation happens at the exchange, so the row survives it and `F` answers yes. The row this
    test supplies says `is_active: false`, `validation_status: "revoked"`, `can_trade: false`, and
    none of it is consulted - the select at :597 asks only for `exchange_id, updated_at`, so the
    function does not even fetch the columns that would answer the question it claims to answer.

    `can_trade: true` is the field a client gates its order UI on, so this is a defect that puts a
    trader in front of an enabled button for an account that cannot place an order.
    """
    sb = _FakeSupabase([REVOKED_KEY])

    with _no_probe_results():
        data = await dashboard_service.get_exchange_data(mock_user, sb=sb)

    assert data["can_trade"] is False, (
        f"the only credential is revoked, so nothing can be traded. `F` returns "
        f"{data['can_trade']!r} because :625 counts rows rather than asking whether any of them "
        f"still works."
    )
    assert _exchange(REVOKED_KEY, data)["status"] != "connected", (
        f"a revoked credential is not connected. `F` reports "
        f"{_exchange(REVOKED_KEY, data)['status']!r}."
    )


@pytest.mark.asyncio
async def test_an_unvalidated_key_cannot_trade(mock_user, dashboard_service):
    """Saved, never validated. Until something confirms it, the answer is not `True`.

    COUNTEREXAMPLE OBSERVED ON `F`: `can_trade: true` for a credential whose
    `last_validated_at` is `null`. Asserted separately from the revoked case because the two have
    different remedies - revocation is a *known* negative, unvalidated is an *unknown* - and a fix
    that only handled explicit revocation would leave the more common of the two wrong.
    """
    sb = _FakeSupabase([UNPROBED_KEY])

    with _no_probe_results():
        data = await dashboard_service.get_exchange_data(mock_user, sb=sb)

    assert data["can_trade"] is False, (
        f"nothing has validated this credential, so tradability is not established. `F` returns "
        f"{data['can_trade']!r}."
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. PRESERVATION  (Requirement 3.3, task 2) - these PASS on `F` and must keep passing
# ══════════════════════════════════════════════════════════════════════════
#
# Observation-first: each expectation below was observed on `F` and is pinned as observed. They
# are the other half of wave 1 - a fix that reported "unknown" for everything, or `can_trade:
# false` for a working account, would satisfy sections 1-2 and be a worse defect than the one it
# replaced.


@requires_routers
@pytest.mark.parametrize(
    "measured_ms, expected_status",
    [
        (12, "optimal"),    # well under the 150 ms ceiling
        (149, "optimal"),   # the last millisecond that is still optimal
        (150, "normal"),    # the boundary itself is NOT optimal (`< 150`, :1472)
        (300, "normal"),
        (499, "normal"),    # the last millisecond that is still normal
        (500, "degraded"),  # the boundary itself is NOT normal (`< 500`, :1474)
        (900, "degraded"),
    ],
)
@pytest.mark.asyncio
async def test_preserved_measured_latency_classification_thresholds(
    mock_user, dashboard_service, measured_ms, expected_status
):
    """A latency that WAS measured keeps classifying exactly as it does today.

    OBSERVED ON `F` (`get_health_status` :1471-1476) and pinned: `< 150` optimal, `< 500` normal,
    otherwise degraded, with the figure reported through unrounded. Both boundaries are included
    because `<` and `<=` are the same code until a test distinguishes them, and the fix to
    `get_exchange_data` is expected to adopt these thresholds - so what they are must be recorded
    before it does.
    """
    keys_patch, get_patch = _measured_latency(measured_ms)

    with keys_patch, get_patch:
        health = await dashboard_service.get_health_status(mock_user, environment="live")

    assert health["exchange_api_latency_ms"] == measured_ms, "a measurement is reported as read"
    assert health["exchange_api_latency_status"] == expected_status


@requires_routers
@pytest.mark.asyncio
async def test_preserved_unmeasured_latency_is_null_and_unavailable(
    mock_user, dashboard_service
):
    """The convention sections 1-2 ask `get_exchange_data` to adopt, pinned where it already holds.

    OBSERVED ON `F` (:1461-1462, :1479-1480): no probe result in Redis, so
    `exchange_api_latency_ms` is `None` and `exchange_api_latency_status` is `"unavailable"`. No
    `35`, no `"connected"`. This is the proof that the fixed-state expectation above is this
    codebase's own answer to "nothing was measured" and not an expectation imported by this spec.
    """
    with _no_probe_results():
        health = await dashboard_service.get_health_status(mock_user, environment="live")

    assert health["exchange_api_latency_ms"] is None
    assert health["exchange_api_latency_status"] == "unavailable"


@pytest.mark.asyncio
async def test_preserved_a_valid_reachable_credential_is_connected_and_can_trade(
    mock_user, dashboard_service
):
    """A validated, reachable, trade-permitted credential still reads `connected` / `can_trade`.

    OBSERVED ON `F` and pinned: `status: "connected"`, `can_trade: true`, `total_exchanges: 1`,
    `connected_exchanges: 1`, `last_sync` carried from the row's `updated_at`.

    `F` passes this *vacuously* - it reports `connected` for every row, so it cannot fail a test
    that asks for `connected` on a good one. That is precisely why this is preservation and not
    exploration: it has no power to detect the defect, and full power to detect a fix that
    over-corrects into reporting every exchange as unknown or untradable.
    """
    sb = _FakeSupabase([VALID_KEY])
    keys_patch, get_patch = _measured_latency(42)

    with keys_patch, get_patch:
        data = await dashboard_service.get_exchange_data(mock_user, sb=sb)

    entry = _exchange(VALID_KEY, data)
    assert entry["status"] == "connected"
    assert entry["last_sync"] == VALID_KEY["updated_at"]
    assert data["can_trade"] is True
    assert data["total_exchanges"] == 1
    assert data["connected_exchanges"] == 1


@pytest.mark.asyncio
async def test_preserved_an_account_with_no_credentials_reports_nothing_and_cannot_trade(
    mock_user, dashboard_service
):
    """No rows is a successful read of an empty set, and it already answers correctly.

    OBSERVED ON `F` and pinned: `{"total_exchanges": 0, "connected_exchanges": 0,
    "exchanges": [], "can_trade": false}`. The `can_trade: false` here is the one case
    `len(connections) > 0` gets right, and it must survive the fix - a genuine "no exchanges
    connected" is not the same finding as "unknown", and must not become one.
    """
    sb = _FakeSupabase([])

    with _no_probe_results():
        data = await dashboard_service.get_exchange_data(mock_user, sb=sb)

    assert data == {
        "total_exchanges": 0,
        "connected_exchanges": 0,
        "exchanges": [],
        "can_trade": False,
    }
