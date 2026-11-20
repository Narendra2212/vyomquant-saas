"""tests/test_notif04_preference_filtering.py — NOTIF-04 / T10

T10 asked for notification preference filtering. The measurement that preceded this
(`docs/audits/T10_NOTIFICATION_VOCABULARY_FINDING.md`) found that the obvious
implementation would have been worse than none:

    8   preference keys the panel offers (NotificationEvents)
    15  literal event types the tree dispatches
    1   appeared in both  (kill_switch_activated)

A filter keyed on the 8 preference fields consults a preference for one event type and
passes the other fourteen through ungoverned -- while looking, in review and in the UI,
like preferences work. The panel stops being VISIBLY inert and starts being INVISIBLY
inert, which is harder to find.

So the mapping is declared (`EVENT_PREFERENCE_MAP`) and this file is what stops it
rotting. Three obligations, in descending order of how much damage their absence does:

1. **The mandatory floor holds at the dispatch boundary.** `kill_switch_activated`,
   `stop_loss_triggered` and `new_login_detected` must reach the user whatever the
   stored row says. Migration 073's CHECK refuses to STORE `false` for them, but a
   constraint added later than the rows it governs cannot vouch for rows written
   before it, so the floor is enforced twice.
2. **Nothing is suppressed by accident.** A preference read that fails, an absent row,
   an unmapped event and a malformed value all resolve to "deliver". A silent drop is
   invisible from both ends -- the producer thinks it notified, the user never knew.
3. **The vocabularies cannot drift apart again.** Every event type dispatched anywhere
   in `backend_app` must appear in the map, or this file fails.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from backend_app.core import notification_dispatcher as nd
from backend_app.core.notification_preferences import event_is_permitted
from backend_app.core.models.pydantic_models import MANDATORY_NOTIFICATION_EVENTS

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend_app"

USER = "11111111-1111-4111-8111-111111111111"


# ───────────────────────── doubles ─────────────────────────


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    def __init__(self, client, table):
        self._c, self._t = client, table

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        self._c.reads.append(self._t)
        if self._c.raise_on_read:
            raise RuntimeError("preference store unreachable")
        return _Result(self._c.rows)


class _Client:
    """Only answers the preference read; anything else is a programming error here."""

    def __init__(self, events: Optional[Dict[str, Any]] = None, *, no_row=False,
                 raise_on_read=False):
        self.rows = [] if no_row else [{"events": events if events is not None else {}}]
        self.reads: List[str] = []
        self.raise_on_read = raise_on_read

    def table(self, name):
        return _Query(self, name)


def _allows(event_type: str, client) -> bool:
    """The real gate, not a copy of it.

    This used to call `notification_dispatcher._preference_allows`, which NOTIF-04
    superseded with `notification_preferences.event_is_permitted` and left behind. The
    dead copy never imported `inspect`, so every read raised `NameError` into its own
    fail-open handler and the four suppression cases below could not have passed
    against it. Pointing at the function `dispatch_user_notification` actually calls is
    what makes them mean something.
    """
    return asyncio.run(event_is_permitted(USER, event_type, client))


# ───────── 1. the mandatory floor, enforced at dispatch ─────────


@pytest.mark.parametrize("event", sorted(MANDATORY_NOTIFICATION_EVENTS))
def test_a_mandatory_event_is_delivered_even_when_the_row_says_false(event: str) -> None:
    """The floor migration 073 enforces at the column, enforced again at the boundary.

    073's CHECK refuses to STORE `false` for these. It was added after rows already
    existed, so it cannot speak for those rows -- and a row that predates a constraint
    is exactly the one that would mute an emergency halt.
    """
    client = _Client({event: False})
    assert _allows(event, client) is True, (
        f"{event} is a mandatory safety alert and was suppressed by a stored preference"
    )


@pytest.mark.parametrize("event", sorted(MANDATORY_NOTIFICATION_EVENTS))
def test_a_mandatory_event_does_not_even_read_the_preference(event: str) -> None:
    """Not consulted at all, so no read failure or malformed row can reach a verdict."""
    client = _Client({event: False})
    _allows(event, client)
    assert client.reads == [], (
        f"{event} consulted notification_settings; a mandatory alert must not depend on "
        "a read that can fail"
    )


def test_kill_switch_recovered_rides_with_the_mandatory_event() -> None:
    """It maps to `kill_switch_activated`, which is mandatory, so it is not filterable.

    Deliberate: a customer who is told the halt fired must also be told it cleared, or
    they are left believing trading is still stopped.
    """
    assert nd.EVENT_PREFERENCE_MAP["kill_switch_recovered"] == "kill_switch_activated"
    assert _allows("kill_switch_recovered", _Client({"kill_switch_activated": False})) is True


# ───────── 2. governed events are actually governed ─────────


def test_a_false_preference_suppresses_a_governed_event() -> None:
    """The feature itself. Without this the map is decoration."""
    assert _allows("trade_executed", _Client({"trade_executed": False})) is False


def test_a_true_preference_delivers_a_governed_event() -> None:
    assert _allows("trade_executed", _Client({"trade_executed": True})) is True


def test_an_event_is_governed_by_its_MAPPED_key_not_its_own_name() -> None:
    """`paper_order_placed` has no preference of its own; `trade_executed` governs it.

    This is the half a name-match filter could not do, and the reason the map exists.
    """
    assert nd.EVENT_PREFERENCE_MAP["paper_order_placed"] == "trade_executed"
    assert _allows("paper_order_placed", _Client({"trade_executed": False})) is False
    assert _allows("paper_order_placed", _Client({"trade_executed": True})) is True


def test_an_ungoverned_event_is_delivered_whatever_the_row_says() -> None:
    """Billing facts are not opt-out, and `None` records that as a decision."""
    assert nd.EVENT_PREFERENCE_MAP["payment_succeeded"] is None
    client = _Client({"trade_executed": False, "payment_succeeded": False})
    assert _allows("payment_succeeded", client) is True
    assert client.reads == [], "an ungoverned event should not read preferences at all"


# ───────── 3. nothing is suppressed by accident ─────────


def test_a_failed_preference_read_delivers() -> None:
    """Losing an alert because a settings row could not be read is the worse outcome."""
    assert _allows("trade_executed", _Client({"trade_executed": False},
                                             raise_on_read=True)) is True


def test_an_absent_settings_row_delivers() -> None:
    """`NotificationEvents` defaults are on, so a user who never opened the panel
    gets default behaviour rather than silence."""
    assert _allows("trade_executed", _Client(no_row=True)) is True


def test_a_key_missing_from_the_stored_events_delivers() -> None:
    assert _allows("trade_executed", _Client({"daily_pnl_summary": False})) is True


def test_no_client_delivers() -> None:
    """DEV_MODE and the test suite have no store; that must not mute everything."""
    assert _allows("trade_executed", None) is True


@pytest.mark.parametrize("bad", [0, None, "no", "", [], {}, "False", "false"])
def test_an_ambiguous_value_is_not_read_as_false(bad) -> None:
    """NOTIF-02 measured this: `bool(0)` and `bool(None)` are False in Python, so a
    malformed row would silently mute an alert. Only an explicit false suppresses.

    `"false"` MOVED HERE from the suppressing case, and that is a correction to this
    file rather than a change in behaviour. The string never suppressed in production:
    `dispatch_user_notification` consults `event_is_permitted`, whose
    `_is_explicitly_off` is `value is False` and documents `"false"` as one of the
    shapes it deliberately will not read as consent.
    `test_t10_event_wiring_and_preferences.test_only_an_unambiguous_false_suppresses`
    has pinned that, green, with `"false"` in this same list. The old expectation
    belonged to the superseded copy of the gate, which matched `value == "false"` --
    so these two files asserted opposite things about the same string, and the one
    describing running code is this one.
    """
    assert _allows("trade_executed", _Client({"trade_executed": bad})) is True, (
        f"{bad!r} was read as 'switched off'"
    )


def test_the_only_spelling_that_suppresses() -> None:
    """The JSON boolean `false`, and nothing else. See the case above for why."""
    assert _allows("trade_executed", _Client({"trade_executed": False})) is False


# ───────── 4. the vocabularies cannot drift apart again ─────────


def _dispatched_event_types() -> set:
    """Every literal `event_type=`/`type=` passed to a dispatch call in backend_app."""
    found = set()
    for path in BACKEND.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in {"dispatch_user_notification", "create_notification"}:
                continue
            for kw in node.keywords:
                if kw.arg in ("event_type", "type") and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        found.add(kw.value.value)
    return found


def test_every_dispatched_event_type_is_declared_in_the_map() -> None:
    """The drift guard. An undeclared event type falls through as 'deliver', which is
    safe but silently ungoverned -- and silently ungoverned is how the 8 preference
    keys and the 15 dispatched types diverged in the first place."""
    dispatched = _dispatched_event_types()
    assert dispatched, "the scanner found no dispatched event types; it has stopped working"
    missing = sorted(dispatched - set(nd.EVENT_PREFERENCE_MAP))
    assert not missing, (
        "these event types are dispatched but not declared in EVENT_PREFERENCE_MAP:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd each with the preference that governs it, or None if it is not "
          "preference-governed. None is a real answer; omission is not."
    )


def test_every_mapped_preference_key_is_a_real_NotificationEvents_field() -> None:
    """A typo'd key would read as 'absent from the row' and deliver forever -- a filter
    that silently never filters."""
    from backend_app.core.models.pydantic_models import NotificationEvents

    fields = set(NotificationEvents.model_fields)
    bad = sorted(
        {v for v in nd.EVENT_PREFERENCE_MAP.values() if v is not None} - fields
    )
    assert not bad, (
        f"EVENT_PREFERENCE_MAP names preference keys that NotificationEvents does not "
        f"declare: {bad}. They would never match a stored row."
    )


def test_every_mandatory_event_is_in_the_map_and_maps_to_itself_or_a_mandatory_key() -> None:
    for event in MANDATORY_NOTIFICATION_EVENTS:
        assert event in nd.EVENT_PREFERENCE_MAP, f"{event} is not declared"
        assert nd.EVENT_PREFERENCE_MAP[event] in MANDATORY_NOTIFICATION_EVENTS


def test_the_map_covers_every_notification_events_field() -> None:
    """The other direction: a preference the panel shows that governs NOTHING is a
    switch the customer can toggle with no effect, which is what T10 started as."""
    from backend_app.core.models.pydantic_models import NotificationEvents

    governed = {v for v in nd.EVENT_PREFERENCE_MAP.values() if v is not None}
    orphaned = sorted(set(NotificationEvents.model_fields) - governed)
    assert not orphaned, (
        "the preferences panel offers these switches, but no dispatched event type maps "
        f"to them, so toggling them does nothing: {orphaned}\n"
        "Either map an event type to each, or remove them from the panel."
    )


# ───────── 5. the suppression happens before anything is written ─────────


def test_a_suppressed_event_persists_nothing_and_returns_none(monkeypatch) -> None:
    """A filter that suppressed AFTER the insert would leave the row and only hide the
    socket frame -- the user would still see it on next page load."""
    created: List[Any] = []

    async def _never(*a, **k):
        created.append((a, k))
        return "should-not-happen"

    async def _client(_supplied=None):
        return _Client({"trade_executed": False})

    monkeypatch.setattr(nd, "create_notification", _never)
    monkeypatch.setattr(nd, "_resolve_persistence_client", _client)

    out = asyncio.run(
        nd.dispatch_user_notification(
            user_id=USER, event_type="trade_executed", category="trading",
            title="t", message="m",
        )
    )
    assert out is None
    assert created == [], "a suppressed notification was still persisted"


def test_an_allowed_event_still_reaches_create_notification(monkeypatch) -> None:
    """The guard must not break the path it sits on."""
    created: List[Any] = []

    async def _ok(notif, **k):
        created.append(notif)
        return "notif-id"

    async def _client(_supplied=None):
        return _Client({"trade_executed": True})

    monkeypatch.setattr(nd, "create_notification", _ok)
    monkeypatch.setattr(nd, "_resolve_persistence_client", _client)

    out = asyncio.run(
        nd.dispatch_user_notification(
            user_id=USER, event_type="trade_executed", category="trading",
            title="t", message="m",
        )
    )
    assert out == "notif-id"
    assert len(created) == 1


def test_a_mandatory_event_is_dispatched_even_with_a_broken_store(monkeypatch) -> None:
    """End to end: the emergency halt gets through when preferences cannot be read."""
    created: List[Any] = []

    async def _ok(notif, **k):
        created.append(notif)
        return "notif-id"

    async def _client(_supplied=None):
        return _Client({"kill_switch_activated": False}, raise_on_read=True)

    monkeypatch.setattr(nd, "create_notification", _ok)
    monkeypatch.setattr(nd, "_resolve_persistence_client", _client)

    out = asyncio.run(
        nd.dispatch_user_notification(
            user_id=USER, event_type="kill_switch_activated", category="risk",
            title="halt", message="m", severity="critical",
        )
    )
    assert out == "notif-id"
    assert len(created) == 1
