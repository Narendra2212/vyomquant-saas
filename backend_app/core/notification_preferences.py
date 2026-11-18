"""
core/notification_preferences.py — The preference filter on the dispatch path

T10. NOTHING READ A PREFERENCE, SO NO PREFERENCE SUPPRESSED ANYTHING
--------------------------------------------------------------------
`notification_settings` had exactly two readers across `backend_app`, both in
`routers/user.py` — the GET and the PUT of the preferences panel itself. Measured by
grep at the time this module was written, and recorded as an asserted gap by
`tests/test_notif02_mandatory_alert_floor.test_nothing_on_the_dispatch_path_reads_a_preference`
and by `test_notif01_durable_dedupe_postgres.test_no_preference_filtering_exists_on_the_dispatch_path`.
Both of those tests instructed whoever wired a filter to replace them with assertions
on the filter; this module is that filter and they now assert it.

So the panel stored eight switches and the platform honoured none of them. A customer
who turned "Trade Executions" off still received every fill.

────────────────────────────────────────────────────────────────────────────────
1. THE STORED VOCABULARY IS NOT THE ONE THE COLUMN DEFAULT DECLARES
────────────────────────────────────────────────────────────────────────────────
This is the measurement that decides whether the filter does anything at all, and it
is the same trap migration 073 documents for its CHECK constraint.

`notification_settings.events` declares this column default:

    '{"system": true, "stop_loss": true, "risk_alerts": true, "trade_fills": true}'

NOT ONE production row carries any of those four keys. Migration 073 measured all
four rows read-only with `jsonb_each` and found the eight fields of
`core/models/pydantic_models.NotificationEvents` on every one of them:

    api_key_expiring, backtest_complete, bot_state_change, daily_pnl_summary,
    kill_switch_activated, new_login_detected, stop_loss_triggered, trade_executed

The reason is that the only writer is the panel's PUT, which always sends the whole
`NotificationSettingsRequest` object, so the default is reachable only by an INSERT
that omits the column — and nothing does that.

A filter keyed on `trade_fills` or `risk_alerts` would therefore have read a key no row
has, found nothing, and delivered everything — a mechanism that looks enforced in
review and suppresses nothing in production. The mapping below is keyed on the stored
eight, not on the declared four.

────────────────────────────────────────────────────────────────────────────────
2. HOW EACH event_type WAS MAPPED ONTO A PREFERENCE KEY
────────────────────────────────────────────────────────────────────────────────
A preference key is a promise the panel makes in words. The mapping is derived from
those words — `components/NotificationPreferences.jsx` renders a label and a
description for every switch — plus `design/notificationPolicy.js`, which already
records which backend `event_type` belongs to which trader-facing concern. Nothing is
mapped on a name resemblance alone.

    trade_executed
        Panel: "Trade Executions" / "Real-time order fill alerts and position entries".
        Governs `trade_executed` (a live fill) and `paper_order_placed` (a simulated
        one). Both are a fill reaching the customer, which is what the switch names.

    stop_loss_triggered                                              [MANDATORY FLOOR]
        Panel: "Stop Loss & Margin Alerts" / "Stop loss execution and drawdown limit
        notifications". The description names drawdown limits explicitly, so this key
        governs `stop_loss_triggered` AND `risk_limit_breached` — the refusals
        `routers/risk.record_risk_violation` records. It is on the floor, so the mapping
        changes nothing about delivery; it is written down so a later reader does not
        conclude a risk breach is governed by a switch that could suppress it.

    kill_switch_activated                                            [MANDATORY FLOOR]
        Panel: "Emergency Kill Switch & Liquidations" / "Venue liquidation proximity and
        emergency halt activations". Governs `kill_switch_activated` (`routers/risk.py`,
        the one `severity="critical"` dispatch in the tree) and its
        `kill_switch_recovered` counterpart.

    new_login_detected                                               [MANDATORY FLOOR]
        Panel: "Security & Login Events" / "New login detections and authentication
        state alterations". Governs `new_login_detected` and `profile_updated` —
        `routers/user.py:83,102` dispatch the latter with `category="security"`, and a
        changed username or telegram id is an authentication-state alteration.

    bot_state_change
        Panel: "Strategy Lifecycle State Changes" / "Strategy start, pause, runtime
        errors, and halts". Governs `strategy_created`, `strategy_deployed` and
        `strategy_stopped` — the transitions the description enumerates.

    daily_pnl_summary, api_key_expiring, backtest_complete
        MAPPED TO NOTHING, because nothing produces them. Derived by AST over every
        `dispatch_user_notification` call site in `backend_app`: no site emits a daily
        PnL summary, an API-key expiry warning, or a backtest completion.
        `design/notificationPolicy.js` records why the last of those three has no
        server event — `GET /api/strategies/backtest/{job_id}` is polled and the
        Backtester page raises `backtest_complete` client-side. Listed here with empty
        coverage so "this switch governs nothing yet" is a statement in the code rather
        than something a reader has to re-derive.

────────────────────────────────────────────────────────────────────────────────
3. WHAT IS DELIBERATELY NOT GOVERNED BY ANY SWITCH
────────────────────────────────────────────────────────────────────────────────
An event with no entry in the map is DELIVERED. That is the default-open direction and
each exclusion below is a decision, not an oversight:

  * `order_failed` — the panel's only trading-executions switch is for *fills*
    ("Real-time order fill alerts and position entries"). A submission that did not
    fill is not a fill, and a customer who muted fill confirmations has not asked to
    stop hearing that an order was rejected. Not governed.
  * `deployment_failed` — `design/notificationPolicy.js` keeps `DEPLOYMENT_FAILED` as
    its own allowlist key, separate from `DEPLOYMENT_SUCCEEDED` and `STRATEGY_STOPPED`.
    A deployment that never started is a failed request, not a lifecycle transition, so
    `bot_state_change` does not cover it.
  * `exchange_connected` / `exchange_disconnected` — `api_key_expiring` is about a key
    nearing expiry, which is not the same event as a venue that stopped
    authenticating. `notificationPolicy.js` escalates `exchange_disconnected` to
    `error` on the frontend "because a disconnected exchange means live strategies have
    stopped trading", so it is not something `api_key_expiring` may mute.
  * billing (`subscription_updated`, `payment_succeeded`, `subscription_cancelled`,
    `cancellation_reversed`), `support_ticket_*`, `risk_settings_updated`,
    `paper_account_reset` — the panel offers no switch in these areas at all. A filter
    cannot honour a preference that does not exist.

────────────────────────────────────────────────────────────────────────────────
4. THE MANDATORY FLOOR IS ENFORCED HERE TOO, NOT ONLY IN THE COLUMN
────────────────────────────────────────────────────────────────────────────────
`chk_notification_settings_mandatory_alerts` (migration 073) stops a mandatory switch
being STORED as off. That is the control for the write path. It is not the control for
this one: a row predating the constraint, a value in a shape the CHECK admits, or a
future edit to the mapping could all put a mandatory event behind a false preference.
So :func:`event_is_permitted` answers True for a mandatory event before it reads
anything at all — the read is skipped entirely rather than performed and overridden,
because a floor that depends on a successful database read is not a floor.

────────────────────────────────────────────────────────────────────────────────
5. ONLY AN UNAMBIGUOUS "OFF" SUPPRESSES
────────────────────────────────────────────────────────────────────────────────
Migration 073's own probe records what shapes reach this column: the JSON boolean
`false`, the strings `"false"` / `"no"`, the number `0`, and JSON `null` are all
storable at a DISCRETIONARY key (the CHECK only constrains the three mandatory ones,
and `authenticated` retains INSERT and UPDATE, so a hand-rolled PostgREST request can
write any of them). `bool(0)` and `bool(None)` are both False in Python, so a naive
truthiness test would read a number and a null as consent to suppress.

They are not consent. Suppression requires the value to be exactly the boolean `False`
— the one shape the request model can actually produce. Everything else, junk
included, delivers. Same sentence 073 uses for the floor: an unreadable value at a
preference key is not consent.

────────────────────────────────────────────────────────────────────────────────
6. A FAILED READ DELIVERS, AND SAYS SO AT ERROR
────────────────────────────────────────────────────────────────────────────────
Every failure path in :func:`event_is_permitted` returns True. A preference store that
cannot be read is not a customer asking for silence, and the failure mode of "drop the
notification" is the NOTIF-01 defect happening again through a new door. An unreadable
store is logged at ERROR because a filter that silently stops filtering is a defect;
"no client configured" is logged at DEBUG because that is the ordinary shape of a local
or test environment and is not evidence of anything.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, Mapping, Optional

from backend_app.core.models.pydantic_models import MANDATORY_NOTIFICATION_EVENTS

logger = logging.getLogger(__name__)

#: ``event_type`` passed to ``dispatch_user_notification`` -> the ``events`` key in
#: ``notification_settings`` that governs it. Section 2 of this module's docstring gives
#: the evidence for every entry; section 3 gives the evidence for every absence.
#:
#: An ``event_type`` that is NOT a key here is delivered unconditionally. That is the
#: safe direction and the one the panel supports: it offers eight switches, and an event
#: no switch names cannot be suppressed by a preference the customer was never shown.
PREFERENCE_KEY_BY_EVENT_TYPE: Mapping[str, str] = {
    # ── Fills ────────────────────────────────────────────────────────────────
    "trade_executed": "trade_executed",
    "paper_order_placed": "trade_executed",
    # ── Loss limits and risk refusals (floor) ────────────────────────────────
    "stop_loss_triggered": "stop_loss_triggered",
    "risk_limit_breached": "stop_loss_triggered",
    # ── Emergency halt (floor) ───────────────────────────────────────────────
    "kill_switch_activated": "kill_switch_activated",
    "kill_switch_recovered": "kill_switch_activated",
    # ── Account / authentication (floor) ─────────────────────────────────────
    "new_login_detected": "new_login_detected",
    "profile_updated": "new_login_detected",
    # ── Strategy lifecycle transitions ───────────────────────────────────────
    "strategy_created": "bot_state_change",
    "strategy_deployed": "bot_state_change",
    "strategy_stopped": "bot_state_change",
}

#: Preference keys that exist in the panel and the model but that no producer emits.
#: Kept as data so "this switch governs nothing yet" is checkable rather than an
#: impression, and so a later producer has an obvious place to register itself.
PREFERENCE_KEYS_WITHOUT_PRODUCER: frozenset = frozenset(
    {"daily_pnl_summary", "api_key_expiring", "backtest_complete"}
)

#: Every ``event_type`` that must be delivered whatever the stored preference says.
#:
#: DERIVED, not restated: an event type is on the floor exactly when the preference key
#: governing it is one of ``MANDATORY_NOTIFICATION_EVENTS``. Spelling the set out again
#: would let the two drift, and the drift would present as a mandatory alert quietly
#: becoming suppressible. ``tests/test_t10_event_wiring_and_preferences.py`` asserts the
#: derivation against the model's tuple and against migration 073's predicate.
MANDATORY_EVENT_TYPES: frozenset = frozenset(
    event_type
    for event_type, key in PREFERENCE_KEY_BY_EVENT_TYPE.items()
    if key in MANDATORY_NOTIFICATION_EVENTS
)


def preference_key_for(event_type: str) -> Optional[str]:
    """The ``events`` key governing ``event_type``, or ``None`` when none does."""
    return PREFERENCE_KEY_BY_EVENT_TYPE.get(str(event_type))


def is_mandatory_event(event_type: str) -> bool:
    """Whether no preference may suppress ``event_type`` (migration 073's floor)."""
    return str(event_type) in MANDATORY_EVENT_TYPES


def _is_explicitly_off(value: Any) -> bool:
    """Whether ``value`` is an unambiguous "switch this off".

    ``True`` for the boolean ``False`` and nothing else. See section 5 of the module
    docstring: ``0``, ``None``, ``"false"`` and anything else storable at this key are
    NOT read as consent, because a reader cannot tell a customer's intent from them and
    the failure direction of guessing wrong is a dropped safety alert.

    ``value is False`` rather than ``value == False``: ``0 == False`` is true in Python,
    and ``0`` is one of the shapes migration 073 measured arriving at this column.
    """
    return value is False


async def _stored_events(user_id: str, client: Any) -> Optional[Dict[str, Any]]:
    """This user's stored ``events`` object, or ``None`` when it cannot be read.

    ``None`` means "no answer" and is deliberately not distinguished from "no row": both
    lead the caller to deliver. The two ARE distinguished in the logs, because a row that
    does not exist is normal and a read that failed is not.

    Follows the read shape ``routers/user.get_notif_settings`` already uses — the same
    ``inspect.isawaitable`` dance — because the Supabase client is sync in some
    deployments and async in others and this module must work with whichever one the
    dispatcher resolved.
    """
    try:
        res = (
            client.table("notification_settings")
            .select("events")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        result = await res if inspect.isawaitable(res) else res
    except Exception as exc:  # noqa: BLE001 - classified by the caller, never propagated
        logger.error(
            "[NOTIF_PREFS] could not read notification preferences for user %s "
            "(%s: %s). The notification will be DELIVERED: an unreadable preference "
            "store is not a customer asking for silence.",
            user_id, type(exc).__name__, exc,
        )
        return None

    rows = getattr(result, "data", None) or []
    if not rows:
        # No row is the ordinary state of an account that has never opened the
        # preferences panel. Every switch is therefore at its model default, and the
        # caller delivers.
        logger.debug("[NOTIF_PREFS] user %s has no stored preferences", user_id)
        return None

    events = rows[0].get("events")
    if not isinstance(events, dict):
        logger.error(
            "[NOTIF_PREFS] user %s has a notification_settings row whose `events` is "
            "%s, not an object. The notification will be DELIVERED.",
            user_id, type(events).__name__,
        )
        return None
    return events


async def event_is_permitted(
    user_id: str, event_type: str, client: Optional[Any] = None
) -> bool:
    """Whether this user's stored preferences allow ``event_type`` to be delivered.

    Total: never raises, and returns ``True`` on every uncertainty. ``False`` is
    returned only when a discretionary preference key for this event is stored as the
    boolean ``False``.

    Order matters and is the point of the function:

    1. **The floor first.** A mandatory event returns ``True`` before any read happens,
       so no store failure, no stale row and no future mapping edit can suppress one.
    2. **No governing key** -> deliver. The panel never offered a switch for it.
    3. **No client** -> deliver, at DEBUG. An environment with no service-role key
       cannot read preferences and never could; this is local and test, not a fault.
    4. **The stored value**, read once, suppressing only on an unambiguous ``False``.
    """
    event_type = str(event_type)

    if is_mandatory_event(event_type):
        return True

    key = preference_key_for(event_type)
    if key is None:
        return True

    if client is None:
        logger.debug(
            "[NOTIF_PREFS] no persistence client; '%s' for user %s is delivered "
            "without consulting preferences",
            event_type, user_id,
        )
        return True

    events = await _stored_events(user_id, client)
    if events is None:
        return True

    if key not in events:
        # A partial payload that never mentions the switch does not disagree with it.
        # Same clause migration 073's predicate carries for an absent mandatory key.
        return True

    if _is_explicitly_off(events[key]):
        logger.debug(
            "[NOTIF_PREFS] suppressing '%s' for user %s: preference '%s' is off",
            event_type, user_id, key,
        )
        return False

    return True
