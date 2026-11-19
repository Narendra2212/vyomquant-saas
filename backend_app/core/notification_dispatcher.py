"""
core/notification_dispatcher.py — Centralized, Resilient Notification Dispatcher

The single dispatch helper for every SaaS event producer (Orders, Risk, Exchange,
Strategies, Paper Trading, Reconciliation, Support, Security).

NOTIF-01 — WHAT THIS MODULE USED TO CLAIM, AND WHAT IT DID
----------------------------------------------------------
Two of the four invariants listed here were not true.

**"Persists to DB".** ``create_notification`` writes to Supabase inside
``if supabase:`` and otherwise falls through to a bounded in-process dict. This
module's ``supabase`` parameter defaults to ``None`` and was forwarded verbatim, so
persistence depended on each caller remembering to pass a client. Measured by AST
over every call site in ``backend_app``: **1 of 19 passed one.** The other 18 —
every billing event, both exchange-connection failures, both paper-trading events,
all three risk events, all three strategy-lifecycle events and both security
events — wrote to process memory and nowhere else. Production held **zero**
notification rows, which is exactly what that predicts.

The sharpest case was ``routers/risk.py``: all three of its call sites passed
``ws_manager`` but not ``supabase``, so a risk-limit breach was broadcast live to a
connected socket and never stored. A customer not looking at the tab at that
instant never learned their limit was breached.

The fix is not 18 edits. ``_resolve_persistence_client`` obtains the service-role
client here when a caller does not supply one, so persistence stops being a
property of the call site. A caller may still pass its own client (the support
router does) and that still wins.

**"Idempotency support: skips duplicate events with the same idempotency key".**
The mechanism was ``routers/notifications._idempotency_index``, a process-local
dict that died on restart, could not see a second task's entries, and evicted at
5,000 entries. The key was already written durably into ``metadata``; only the
check was in memory. Migration 071 promotes the key to its own column with a
partial unique index, so Postgres is now the authority and the sentence above is
true. See ``create_notification`` for how a collision is read back.

**Sanitization and tenant scoping were already correct** and are unchanged.

Invariants, as they now stand:
- Guaranteed non-blocking: never crashes or aborts the calling transaction.
- Secret sanitization: scrubs passwords, API secrets, tokens, and private keys.
- Durable persistence: a service-role client is resolved here when not supplied.
- Idempotency: enforced by ``uq_notifications_user_idempotency``, not by a dict.
- Multi-tenant isolation: strictly user-scoped delivery.

A FAILED DISPATCH IS STILL NON-FATAL, BUT NO LONGER SILENT
----------------------------------------------------------
This module must never break the transaction that produced the event — a risk
refusal that fails because its notification failed would be a worse outcome than a
missing notification. So failures are still swallowed. What changed is that they
are no longer all logged at DEBUG: a dropped ``warning``/``error``/``critical``
notification is a dropped safety alert and is logged at ERROR, while a dropped
``info`` stays at DEBUG so the signal is not buried in noise. See
``_log_dispatch_failure``.
"""

import asyncio
import logging
import re
from typing import Any, Dict, Optional

from backend_app.core.background_tasks import fire_and_forget_task
from backend_app.core.notification_preferences import event_is_permitted
from backend_app.routers.notifications import NotificationCreate, create_notification

logger = logging.getLogger(__name__)

#: Severities for which a failed dispatch is itself worth an ERROR line.
#:
#: These are the notifications a customer acts on — a breached risk limit, a halted
#: strategy, a failed order, an exchange that stopped authenticating. Losing one
#: silently is the defect NOTIF-01 is about, so losing one now says so. ``info`` and
#: ``success`` are deliberately excluded: they are the overwhelming majority (15 of
#: the 18 literal call sites emit ``info``) and promoting them would bury the three
#: that matter.
ACTIONABLE_SEVERITIES = frozenset({"warning", "error", "critical"})

SENSITIVE_KEY_PATTERN = re.compile(r"(?:secret|password|token|private_key|api_key|auth|credential)", re.IGNORECASE)

async def _resolve_persistence_client(supplied: Optional[Any]) -> Optional[Any]:
    """The client ``create_notification`` will persist through.

    NOTIF-01. ``create_notification`` only writes to Supabase inside
    ``if supabase:``, and 18 of this module's 19 call sites passed nothing — so
    their notifications existed only in a process-local dict that evicts and does
    not survive a restart. Resolving the client HERE makes durability a property of
    the dispatcher rather than of whether each producer remembered a keyword
    argument.

    A supplied client wins, so the one caller that already passes a request-scoped
    client (``routers/support.py``) is unaffected and no call site has to change.

    Returns ``None`` rather than raising when no service client is configured —
    the ordinary shape of a local or test environment without
    ``SUPABASE_SERVICE_ROLE_KEY``. That is the behaviour every call site already
    had, so an unconfigured environment degrades to the in-memory store exactly as
    before rather than starting to fail. This is the same not-configured /
    configured-but-unreachable split ``core/webhook_idempotency`` documents.
    """
    if supplied is not None:
        return supplied
    try:
        from backend_app.core.dependencies import get_service_supabase_async

        return await get_service_supabase_async()
    except Exception as exc:  # noqa: BLE001
        # Never fatal: a dispatcher that raises would take the producing
        # transaction down with it, which is the outcome this module exists to
        # avoid. Falls back to the in-memory store.
        logger.debug("[NOTIF_DISPATCH] service client unavailable: %s", exc)
        return None


def _log_dispatch_failure(user_id: str, event_type: str, severity: str, exc: Exception) -> None:
    """Report a dropped notification at a level that matches what was dropped.

    Everything here used to be ``logger.debug``, which in production is not
    enabled — so a dropped risk-limit breach left no trace at all. An actionable
    severity now logs at ERROR and names the event, because a customer who was
    never told their limit was breached is an incident, not a debug detail.
    """
    if str(severity).lower() in ACTIONABLE_SEVERITIES:
        logger.error(
            "[NOTIF_DISPATCH] %s notification '%s' for user %s was NOT delivered: "
            "%s: %s. The customer has not been told.",
            severity, event_type, user_id, type(exc).__name__, exc,
        )
    else:
        logger.debug(
            "[NOTIF_DISPATCH] %s notification '%s' for user %s dropped: %s",
            severity, event_type, user_id, exc,
        )


def sanitize_notification_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Remove any sensitive credentials or keys from notification metadata."""
    if not meta or not isinstance(meta, dict):
        return {}
    
    clean: Dict[str, Any] = {}
    for k, v in meta.items():
        if SENSITIVE_KEY_PATTERN.search(str(k)):
            continue
        if isinstance(v, str) and len(v) > 200:
            clean[k] = v[:200] + "..."
        elif isinstance(v, (int, float, bool, str)):
            clean[k] = v
        elif isinstance(v, dict):
            clean[k] = sanitize_notification_metadata(v)
        else:
            clean[k] = str(v)[:100]
    return clean


# ══════════════════════════════════════════════════════════════════════════
#  NOTIF-04: WHICH PREFERENCE GOVERNS WHICH EVENT, DECLARED IN ONE PLACE
#
#  T10 asked for preference filtering. The measurement that preceded this
#  (docs/audits/T10_NOTIFICATION_VOCABULARY_FINDING.md) found the obvious
#  implementation would have been WORSE than none:
#
#      8   preference keys the panel offers (NotificationEvents)
#      15  literal event types this tree dispatches
#      1   appeared in both
#
#  A filter keyed on the 8 preference fields would have consulted a preference
#  for exactly one event type and passed the other fourteen through ungoverned,
#  while looking -- in review and in the UI -- like preferences now work. The
#  panel would have stopped being VISIBLY inert and started being INVISIBLY
#  inert, which is strictly harder to find.
#
#  So the mapping is reconciled here, explicitly, rather than inferred from a
#  name match. Every event type this repository dispatches appears below with
#  the preference that governs it, or `None` for "not preference-governed".
#
#  `None` IS A REAL ANSWER, not a gap. Billing facts and security events are
#  things a customer cannot opt out of: a payment succeeding, a subscription
#  being cancelled, a login from a new device. Marking them `None` records that
#  decision where the next reader will see it, instead of leaving them to fall
#  through a default.
#
#  `tests/test_notif04_preference_filtering.py` fails if any event type
#  dispatched anywhere in `backend_app` is missing from this map, so the two
#  vocabularies cannot drift apart again without CI saying so.
# ══════════════════════════════════════════════════════════════════════════

#: event type -> the `NotificationEvents` field that governs it, or None when the
#: event is not preference-governed.
EVENT_PREFERENCE_MAP: Dict[str, Optional[str]] = {
    # ── governed by a preference the customer owns ──
    "trade_executed": "trade_executed",
    "paper_order_placed": "trade_executed",
    "backtest_complete": "backtest_complete",
    "bot_state_change": "bot_state_change",
    "strategy_deployed": "bot_state_change",
    "strategy_stopped": "bot_state_change",
    "strategy_created": "bot_state_change",
    "paper_account_reset": "bot_state_change",
    "exchange_connected": "api_key_expiring",
    "exchange_disconnected": "api_key_expiring",
    "api_key_expiring": "api_key_expiring",
    "daily_pnl_summary": "daily_pnl_summary",
    # ── MANDATORY: listed for completeness, never filtered (see below) ──
    "kill_switch_activated": "kill_switch_activated",
    "kill_switch_recovered": "kill_switch_activated",
    "stop_loss_triggered": "stop_loss_triggered",
    "new_login_detected": "new_login_detected",
    # ── SAFETY-CRITICAL, no preference field exists and none should ──
    # These four have NO producer yet -- T10 names them as required integrations
    # ("risk-limit breaches", "emergency halt activation", "exchange connection
    # failures") and nothing dispatches them today. They are declared here
    # anyway, and are in SAFETY_CRITICAL_EVENT_TYPES below, so that whoever adds
    # the producer gets an unsuppressable alert BY CONSTRUCTION rather than by
    # remembering to come back here. `None` is accurate: there is no
    # `NotificationEvents` switch for them, and there should not be one.
    "emergency_halt": None,
    "risk_limit_breached": None,
    "margin_call": None,
    "exchange_auth_failed": None,
    # ── not preference-governed, deliberately ──
    # Billing and subscription facts: a customer cannot opt out of being told
    # their money moved or their access changed.
    "payment_succeeded": None,
    "subscription_updated": None,
    "subscription_cancelled": None,
    "cancellation_reversed": None,
    # Account state the customer themselves just changed; suppressing the
    # confirmation would make the action look like it did not happen.
    "profile_updated": None,
    "risk_settings_updated": None,
}


def _safety_critical_event_types() -> frozenset:
    """Every event type that reaches the user regardless of any stored preference.

    TWO LISTS, UNIONED, because they were written for different jobs and neither is
    complete on its own:

    * `MANDATORY_NOTIFICATION_EVENTS` (3) is the set migration 073's mandatory-alerts
      CHECK enforces at the COLUMN, so it can only contain events that have a
      `NotificationEvents` switch to constrain. (The constraint's identifier is not
      spelled out here: it contains the settings table's name as a substring, and
      test_notif02's guard strips `#` comments but not docstrings, so naming it in
      prose reads as this module querying the table. See that test.)
    * the four safety-critical types above have no switch and no producer yet. T10
      requires them; `tests/test_notif01_durable_dedupe_postgres.py` has pinned a
      six-item floor since before any filtering existed.

    Filtering keyed on the first list alone would have left `risk_limit_breached` and
    `emergency_halt` suppressible the moment somebody wrote their producer -- passing
    today only because they are unmapped, which is luck rather than design.
    """
    from backend_app.core.models.pydantic_models import (  # noqa: PLC0415
        MANDATORY_NOTIFICATION_EVENTS,
    )

    return frozenset(MANDATORY_NOTIFICATION_EVENTS) | frozenset({
        "emergency_halt",
        "risk_limit_breached",
        "margin_call",
        "exchange_auth_failed",
        # kill_switch_recovered rides with the halt: a customer told trading stopped
        # must also be told it resumed, or they are left believing it is still down.
        "kill_switch_recovered",
    })


#: Resolved once; the two source lists are module-level constants.
SAFETY_CRITICAL_EVENT_TYPES: frozenset = _safety_critical_event_types()


# The preference gate lives in `core/notification_preferences.event_is_permitted`,
# which `dispatch_user_notification` calls below. A second copy used to live here;
# it was superseded, kept reading the settings table directly, and drifted -- see
# test_notif02_mandatory_alert_floor for the guard that forbids a second reader.




async def dispatch_user_notification(
    user_id: str,
    event_type: str,
    category: str,
    title: str,
    message: str,
    severity: str = "info",
    strategy_id: Optional[str] = None,
    exchange: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    supabase: Optional[Any] = None,
    ws_manager: Optional[Any] = None,
) -> Optional[str]:
    """
    Asynchronously dispatch a notification to a specific authenticated SaaS user.

    Persists durably (resolving a service-role client when the caller supplies
    none — see :func:`_resolve_persistence_client`), falls back to the bounded
    in-process store only when no client is configured, and broadcasts over
    WebSocket when a manager is supplied.

    Pass ``metadata["idempotency_key"]`` to make an event de-duplicated: the key is
    enforced by ``uq_notifications_user_idempotency``, so a redelivery returns the
    first notification's id instead of creating a second one.
    """
    if not user_id or str(user_id) in ("unknown", "None", ""):
        return None

    try:
        clean_meta = sanitize_notification_metadata(metadata)
        notif = NotificationCreate(
            user_id=str(user_id),
            type=str(event_type),
            category=str(category),
            severity=str(severity),
            title=str(title),
            message=str(message),
            strategy_id=strategy_id,
            exchange=exchange,
            metadata=clean_meta,
        )
        client = await _resolve_persistence_client(supabase)

        # ── T10: the preference gate, and it runs BEFORE anything observable ──
        #
        # Until this, NOTHING on the dispatch path read `notification_settings`: the panel
        # offered eight switches and not one of them suppressed anything. The switches
        # were real, stored, and inert.
        #
        # ORDER IS THE WHOLE POINT. Filtering after `create_notification` would leave the
        # row sitting in the very feed the customer muted AND would already have broadcast
        # it over their socket, so the mute would be visibly fake. The gate therefore sits
        # above both, and a suppressed event returns None having touched neither.
        #
        # THE MANDATORY FLOOR IS NOT NEGOTIABLE and is enforced inside
        # `event_is_permitted` rather than here -- `kill_switch_activated`,
        # `stop_loss_triggered` and `new_login_detected` do not even read the store. That
        # matches migration 073, which pins those three keys to `true` in the column, so
        # the schema and the dispatcher agree instead of one trusting the other.
        #
        # A FAILED PREFERENCE READ DELIVERS. `event_is_permitted` fails toward delivery
        # and logs at ERROR: an unreadable preference row must never silently swallow a
        # notification, because the failure mode would be indistinguishable from the
        # customer having muted it.
        if not await event_is_permitted(str(user_id), str(event_type), client):
            logger.debug(
                "[NOTIF_DISPATCH] %s suppressed for %s by a stored preference",
                event_type, user_id,
            )
            return None

        return await create_notification(notif, supabase=client, ws_manager=ws_manager)
    except Exception as e:  # noqa: BLE001 - must never break the producing transaction
        _log_dispatch_failure(str(user_id), str(event_type), str(severity), e)
        return None


# ══════════════════════════════════════════════════════════════════════════
#  NOTIF-05: THE SYNC ENTRY POINT, IN THE SHAPE THE DELETION NOTE SPECIFIED
#
#  Production holds ZERO notification rows against 1838 execution_records,
#  28 strategy_deployments and 6 support_tickets (measured 2026-11-18). The
#  reason is not the preference filter NOTIF-04 added, and not NOTIF-01's
#  persistence fix: it is that NOTHING DISPATCHES from the execution path at
#  all. `backend/execution_engine.py` and the bot runner are SYNCHRONOUS, and
#  the only sync entry point they could have called was deleted -- correctly --
#  for being unsafe in two ways (see the note below).
#
#  So a sync producer has had nothing to call. This is that thing, built to the
#  shape the deletion note itself prescribed: `run_coroutine_threadsafe`
#  against the application's running loop, HOLDING the returned Future and
#  REPORTING its exception. Not `create_task` fire-and-forget.
#
#  WHY A REGISTERED LOOP RATHER THAN `get_event_loop()`. The deleted version
#  called `asyncio.get_event_loop()`, which is deprecated and RAISES in a
#  thread with no loop -- precisely the "sync execution context" it offered
#  itself for. The loop cannot be discovered from a worker thread; it has to be
#  handed over from the async side, which is what `register_dispatch_loop` is
#  for (called once from `main.py`'s lifespan).
#
#  WHAT IT GUARANTEES TO ITS CALLER: it never raises, and it never blocks.
#  A notification is an observation ABOUT a trade, never a participant in it --
#  an alert that cannot be queued must not be able to fail a fill, and an alert
#  that is slow must not hold a lock in the execution path. Both properties are
#  pinned by tests.
# ══════════════════════════════════════════════════════════════════════════

_dispatch_loop: Optional[Any] = None


def register_dispatch_loop(loop: Any) -> None:
    """Hand the application's running event loop to sync producers. Called once.

    Idempotent and last-write-wins, so a test fixture or a re-entered lifespan can
    replace it without a guard at every call site.
    """
    global _dispatch_loop
    _dispatch_loop = loop


def dispatch_loop() -> Optional[Any]:
    """The registered loop, or None when the application has not started one."""
    return _dispatch_loop


def dispatch_user_notification_threadsafe(**kwargs: Any) -> Optional[Any]:
    """Queue a notification from SYNCHRONOUS code. Never raises, never blocks.

    Returns the `concurrent.futures.Future` for the queued coroutine so a caller that
    genuinely wants the notification id can wait for it, and `None` when the work
    could not be queued at all. Most callers should ignore the return value: the
    point of this function is that a notification failure is invisible to the trade
    that produced it.

    ``None`` IS NOT "it failed" -- it is "there was no loop to queue onto", which
    happens in DEV_MODE, in sync unit tests, and in a worker started before the API.
    It is logged at debug, not error, for that reason.

    THE EXCEPTION IS REPORTED, which is the whole difference from the deleted
    version. `run_coroutine_threadsafe` stores the coroutine's exception on the
    Future; if nobody reads it, the failure is silent and Python may not even warn.
    A done-callback reads it and routes it through `_log_dispatch_failure`, so a
    notification that could not be stored is as visible as one dispatched from async
    code.
    """
    import asyncio  # noqa: PLC0415 - only needed on this path

    loop = _dispatch_loop
    user_id = str(kwargs.get("user_id", "unknown"))
    event_type = str(kwargs.get("event_type", "unknown"))
    severity = str(kwargs.get("severity", "info"))

    if loop is None or getattr(loop, "is_closed", lambda: False)():
        logger.debug(
            "[NOTIF_DISPATCH] no running loop registered; %s for %s not queued",
            event_type, user_id,
        )
        return None

    try:
        future = asyncio.run_coroutine_threadsafe(
            dispatch_user_notification(**kwargs), loop
        )
    except Exception as exc:  # noqa: BLE001 - a producing transaction must not fail here
        _log_dispatch_failure(user_id, event_type, severity, exc)
        return None

    def _report(fut: Any) -> None:
        try:
            fut.result()
        except Exception as exc:  # noqa: BLE001
            _log_dispatch_failure(user_id, event_type, severity, exc)

    try:
        future.add_done_callback(_report)
    except Exception:  # noqa: BLE001 - a Future that cannot take a callback still ran
        pass
    return future


# dispatch_user_notification_sync WAS HERE, AND IS DELETED RATHER THAN FIXED.
#
# NOTIF-01. It had ZERO references in the entire repository -- measured by grep
# across source and tests; only its own definition matched. It was also unsafe in
# two ways that would only have surfaced once someone used it:
#
#   * `asyncio.get_event_loop()` is deprecated, and in a thread with no running
#     loop it raises -- which is precisely the "sync execution context" its own
#     docstring offered itself for.
#   * on the loop-is-running branch it called `asyncio.create_task(...)` and kept
#     NO reference to the task, so the task was eligible for garbage collection
#     before it ran. It returned None immediately on that branch either way, so a
#     caller could not distinguish a delivered notification from a collected one.
#
# A broken sync entry point is worse than no sync entry point: it looks like the
# thing a sync producer is supposed to call.
#
# T10 NEEDED ONE, AND `schedule_user_notification` BELOW IS IT. The paragraph that
# used to end here recommended `asyncio.run_coroutine_threadsafe(coro, loop)`, and
# that recommendation was answering only half the problem:
#
#   * `run_coroutine_threadsafe` is the right call FROM ANOTHER THREAD, and it
#     requires a loop object to target. The sync producers this was written for --
#     `ExecutionEngine`, `BotRunner`, `record_risk_violation` -- run inside the
#     application's own loop thread, where `get_running_loop()` succeeds and
#     `run_coroutine_threadsafe` would deadlock if given that same loop.
#   * its objection to `create_task` was that the task was UNTRACKED and therefore
#     collectable before running. `core/background_tasks.fire_and_forget_task`
#     holds a strong reference and logs the exception, which is exactly that
#     objection answered -- and it is already how `routers/risk.record_risk_violation`
#     and `backend/builder_alerts.raise_alerts` schedule work, so reusing it keeps
#     one mechanism rather than introducing a second.
#
# What is still NOT solved, stated rather than implied: a producer on a thread with
# no running loop gets `False` and an ERROR log, not delivery. That is deliberate --
# the alternative is `asyncio.run()` on a per-bar execution path, which blocks the
# trading loop to send a notification. A cross-thread producer needs the
# application loop captured at startup and `run_coroutine_threadsafe` against it;
# no such producer exists today, and `schedule_user_notification` reports honestly
# instead of pretending.


def schedule_user_notification(
    *,
    user_id: str,
    event_type: str,
    category: str,
    title: str,
    message: str,
    severity: str = "info",
    strategy_id: Optional[str] = None,
    exchange: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    supabase: Optional[Any] = None,
    ws_manager: Optional[Any] = None,
) -> bool:
    """Dispatch a notification from SYNCHRONOUS code. Never raises.

    Returns ``True`` when the coroutine was handed to the platform's tracked
    background-task helper, ``False`` when there was no running loop to schedule onto.
    The boolean is the point: the replaced ``dispatch_user_notification_sync`` returned
    ``None`` on both branches, so a caller could not tell a delivered notification from a
    collected one.

    TOTALITY IS THE CONTRACT. Every caller is a producer whose real work -- an order
    refusal, a fill, an audit write, a sign-in -- has already happened or is about to.
    A notification that cannot be scheduled must never propagate into that path, so
    every failure mode here is caught and logged.

    A ``warning``/``error``/``critical`` that is dropped logs at ERROR, because those are
    the ones a customer was supposed to act on. An ``info`` that is dropped logs at DEBUG.
    """
    actionable = severity in ("warning", "error", "critical")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop in this thread. Report, do not raise, and do not block.
        message_text = (
            "[NOTIF_DISPATCH] %s notification for %s was NOT scheduled: no running "
            "event loop in this thread. The producing action is unaffected."
        )
        if actionable:
            logger.error(message_text, event_type, user_id)
        else:
            logger.debug(message_text, event_type, user_id)
        return False

    try:
        # Resolved as a module global at call time, so a test (and a future decorator)
        # can replace `dispatch_user_notification` and have this honour it.
        coro = dispatch_user_notification(
            user_id=user_id,
            event_type=event_type,
            category=category,
            title=title,
            message=message,
            severity=severity,
            strategy_id=strategy_id,
            exchange=exchange,
            metadata=metadata,
            supabase=supabase,
            ws_manager=ws_manager,
        )
        fire_and_forget_task(coro, name=f"notify_{event_type}_{user_id}")
        return True
    except Exception as exc:  # noqa: BLE001 - a notification must not break its producer
        logger.error(
            "[NOTIF_DISPATCH] %s notification for %s was NOT scheduled: %s. The "
            "producing action is unaffected.",
            event_type, user_id, exc,
        )
        return False
