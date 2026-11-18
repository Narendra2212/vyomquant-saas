"""NOTIF-01 — a notification must actually persist, and an event must notify once.

WHAT THE FINDING WAS
====================
`public.notifications` held **zero rows in production**. Two independent causes, both
measured rather than inferred:

**(a) The payload could not insert.** `create_notification` sent
``"id": f"notif_{uuid4().hex[:12]}"`` into a column declared
``id uuid NOT NULL DEFAULT gen_random_uuid()``. PostgreSQL rejects that with
SQLSTATE **22P02**, `invalid input syntax for type uuid` — every time, for every
notification, on both databases. :func:`test_the_old_payload_could_not_insert` pins
that so the regression cannot return quietly.

**(b) Almost no caller asked for persistence.** `create_notification` writes to
Supabase only inside ``if supabase:``, and `dispatch_user_notification` defaulted
``supabase=None`` and forwarded it. AST over every call site in ``backend_app``:
**1 of 19 passed a client.** The other 18 — every billing event, both
exchange-connection failures, both paper-trading events, all three risk events, all
three strategy-lifecycle events, both security events — wrote to a process-local
dict and nowhere else.

The sharpest case was ``routers/risk.py``: all three call sites passed
``ws_manager`` but not ``supabase``, so a risk-limit breach was broadcast to a
connected socket and never stored. A customer not watching that tab at that instant
never learned their limit was breached.

**AND THE DEDUPE WAS A DICT.** The dispatcher's docstring claimed "skips duplicate
events with the same idempotency key". The mechanism was
``routers/notifications._idempotency_index``, which dies on restart (seven ECS
services were replaced during the 2026-11-16 credential repair), cannot see another
task's entries, and evicts at 5,000 — so a redelivered event produced a second
notification in all three situations. The key was already written durably into
``metadata``; only the check was in memory.

WHAT THIS FILE MEASURES
=======================
* **Real PostgreSQL.** Uniqueness, CHECK refusals and SQLSTATEs are database
  behaviour; under SQLite none of them exist, which is why this file is
  PostgreSQL-gated and named in a PostgreSQL CI lane.
* **The exact column set the writer sends**, so a rename breaks this test rather
  than silently re-breaking the ledger.
* **The real exception against the real predicate.** The database's SQLSTATE is fed
  to ``_is_duplicate_notification``; if the two drifted apart, a correctly
  de-duplicated redelivery would be logged as a lost notification.
* **An independent connection as the oracle** for "it persisted".
* No network, no provider credential, no live order. Fixture identities only.

WHAT IS NOT CLAIMED
===================
* **No retry.** There is no outbox and no retry worker. A notification that fails
  to persist for a reason other than a duplicate is still lost — what changed is
  that an actionable severity now says so at ERROR instead of DEBUG. A retry
  mechanism needs a scheduler owner and a backoff policy and is separate work.
* **No preference filtering.** ``notification_settings`` is read by nothing on the
  dispatch path, so no preference suppresses any alert today.
  :func:`test_the_mandatory_alert_set_is_pinned` exists so that whoever wires
  filtering has to confront the mandatory floor rather than discover it.
* **Trade-execution and order-failure events are still not wired at all.** The
  execution engine and bot runner are synchronous and dispatch nothing.
"""
from __future__ import annotations

import os
import uuid

import pytest

# ── explicit PostgreSQL requirement ──────────────────────────────────────────
#
# Same gate as tests/test_money06_*.py and tests/test_bill07_*.py, with no fallback
# to a local DSN file. A SKIP here is NOT a pass — it means the notification ledger
# is unverified. This file is named in the "Run every remaining PostgreSQL-gated
# test file" lane in .github/workflows/01-pr-check.yml; the guard
# tests/test_every_postgres_gated_file_runs_in_a_postgres_lane.py enforces that.
_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")
pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the notification ledger is unverified."
    ),
)

#: Every column `create_notification` sends, as data, so a rename fails loudly.
#: `id` is deliberately ABSENT: the column generates it, and sending a non-uuid
#: string there is cause (a) above.
WRITER_COLUMNS = (
    "user_id", "type", "category", "severity", "title", "message",
    "strategy_id", "exchange", "metadata", "read", "created_at",
    "idempotency_key",
)

#: The severity vocabulary `chk_notifications_severity` admits. Derived by AST from
#: the call sites (backend emits info/warning/critical) unioned with what the
#: frontend styles (error/warning/success/info) — see migration 071's header for
#: why the two are not reconciled here.
PERMITTED_SEVERITIES = ("info", "success", "warning", "error", "critical")

#: Alerts a user preference must never be able to switch off.
#:
#: Nothing filters on preferences today, so this set currently guards nothing at
#: runtime — which is exactly why it is pinned here. The brief requires that
#: "user preferences cannot disable mandatory system safety alerts", and the
#: cheapest way for that to be violated is for someone to add preference filtering
#: without knowing a floor was intended.
MANDATORY_EVENT_TYPES = frozenset({
    "risk_limit_breached",
    "kill_switch_activated",
    "emergency_halt",
    "margin_call",
    "stop_loss_triggered",
    "exchange_auth_failed",
})


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


@pytest.fixture
def notified_user():
    """A profile that can own a notification.

    `notifications.user_id` is uuid with an FK to `auth.users`, and `profiles`
    requires `auth.users` too, so both legs are created and removed children-first.
    """
    uid = str(uuid.uuid4())
    conn = _conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
    cur.execute("INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (uid,))
    try:
        yield uid
    finally:
        cur.execute("DELETE FROM public.notifications WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM public.profiles WHERE id = %s", (uid,))
        cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
        conn.close()


def _insert(cur, user_id, *, key=None, severity="info", event="risk_limit_breached"):
    """Insert exactly what create_notification sends — no `id`."""
    cur.execute(
        """
        INSERT INTO public.notifications
            (user_id, type, category, severity, title, message, strategy_id,
             exchange, metadata, read, created_at, idempotency_key)
        VALUES (%s, %s, 'risk', %s, 'Daily loss limit',
                'Your daily loss limit was breached.', NULL, 'binance',
                '{}'::jsonb, false, now(), %s)
        RETURNING id
        """,
        (user_id, event, severity, key),
    )
    return cur.fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# The ledger can be written at all
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheLedgerAcceptsWhatTheWriterSends:
    def test_the_old_payload_could_not_insert(self, notified_user):
        """Pins cause (a): a `notif_`-prefixed id against a uuid column.

        This is the regression test for the actual defect. If someone reintroduces
        a generated string id into the INSERT payload, this fails.
        """
        import psycopg2

        conn = _conn()
        cur = conn.cursor()
        with pytest.raises(psycopg2.Error) as caught:
            cur.execute(
                "INSERT INTO public.notifications (id, user_id, type, category,"
                " severity, title, message)"
                " VALUES (%s, %s, 'order_filled', 'trade', 'info', 't', 'm')",
                (f"notif_{uuid.uuid4().hex[:12]}", notified_user),
            )
        conn.close()
        assert caught.value.pgcode == "22P02", (
            f"expected 22P02 (invalid input syntax for type uuid), got "
            f"{caught.value.pgcode}. The point of this test is that the id column "
            "is a uuid and will not take a prefixed string."
        )

    def test_every_column_the_writer_sends_exists(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='notifications'"
        )
        present = {r[0] for r in cur.fetchall()}
        conn.close()
        missing = [c for c in WRITER_COLUMNS if c not in present]
        assert not missing, (
            f"notifications is missing {missing}, which create_notification sends. "
            "PostgREST rejects the whole statement on one undefined column."
        )

    def test_the_payload_persists_and_the_database_generates_the_id(self, notified_user):
        conn = _conn()
        cur = conn.cursor()
        new_id = _insert(cur, notified_user, key=f"evt_{uuid.uuid4().hex[:16]}")
        conn.close()

        # Independent connection: "persisted" must mean committed and visible.
        oracle = _conn()
        ocur = oracle.cursor()
        ocur.execute(
            "SELECT id, severity, category FROM public.notifications WHERE user_id = %s",
            (notified_user,),
        )
        rows = ocur.fetchall()
        oracle.close()

        assert len(rows) == 1, f"expected one persisted notification, got {len(rows)}"
        assert str(rows[0][0]) == str(new_id)
        assert len(str(new_id)) == 36, (
            f"id {new_id!r} is not a generated uuid -- the column default did not run"
        )


# ═════════════════════════════════════════════════════════════════════════════
# One event, one notification
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestOneEventNotifiesOnce:
    def test_a_redelivered_event_is_refused(self, notified_user):
        """The property the in-process dict could not provide across a restart."""
        import psycopg2

        key = f"evt_{uuid.uuid4().hex[:16]}"
        conn = _conn()
        cur = conn.cursor()
        _insert(cur, notified_user, key=key)
        with pytest.raises(psycopg2.IntegrityError) as caught:
            _insert(cur, notified_user, key=key)
        conn.close()
        assert caught.value.pgcode == "23505", (
            f"the duplicate was refused with {caught.value.pgcode}, not 23505. "
            "_is_duplicate_notification matches 23505 to tell 'already delivered' "
            "from a real write failure."
        )

    def test_the_first_delivery_is_the_one_that_remains(self, notified_user):
        """The read-back the handler performs must find exactly one row."""
        key = f"evt_{uuid.uuid4().hex[:16]}"
        conn = _conn(autocommit=False)
        cur = conn.cursor()
        first = _insert(cur, notified_user, key=key)
        conn.commit()
        try:
            _insert(cur, notified_user, key=key)
            conn.commit()
        except Exception:
            conn.rollback()
        cur.execute(
            "SELECT id FROM public.notifications WHERE user_id=%s AND idempotency_key=%s",
            (notified_user, key),
        )
        rows = cur.fetchall()
        conn.close()
        assert len(rows) == 1
        assert str(rows[0][0]) == str(first), (
            "the surviving notification must be the FIRST delivery -- that is the "
            "one that actually happened"
        )

    def test_the_same_key_for_a_different_user_is_allowed(self, notified_user):
        """Uniqueness is per user. Two customers may be told about one platform event."""
        other = str(uuid.uuid4())
        key = f"evt_{uuid.uuid4().hex[:16]}"
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT DO NOTHING", (other,))
        cur.execute("INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING", (other,))
        try:
            _insert(cur, notified_user, key=key)
            _insert(cur, other, key=key)
            cur.execute(
                "SELECT count(*) FROM public.notifications WHERE idempotency_key = %s",
                (key,),
            )
            assert cur.fetchone()[0] == 2
        finally:
            cur.execute("DELETE FROM public.notifications WHERE user_id = %s", (other,))
            cur.execute("DELETE FROM public.profiles WHERE id = %s", (other,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (other,))
            conn.close()

    def test_notifications_without_a_key_never_collide(self, notified_user):
        """Most notifications carry no key. They must not de-duplicate each other.

        The index is partial for exactly this reason: NULL is "this is not a
        de-duplicated event", not a shared identity.
        """
        conn = _conn()
        cur = conn.cursor()
        for _ in range(3):
            _insert(cur, notified_user, key=None)
        cur.execute(
            "SELECT count(*) FROM public.notifications"
            " WHERE user_id=%s AND idempotency_key IS NULL",
            (notified_user,),
        )
        n = cur.fetchone()[0]
        conn.close()
        assert n == 3, f"expected 3 independent notifications, got {n}"

    def test_a_blank_key_is_refused(self, notified_user):
        """A blank string is a real value identifying nothing.

        Were it allowed, the FIRST event that supplied one would block every later
        blank-keyed event for that user.
        """
        import psycopg2

        conn = _conn()
        cur = conn.cursor()
        with pytest.raises(psycopg2.IntegrityError) as caught:
            _insert(cur, notified_user, key="   ")
        conn.close()
        assert caught.value.pgcode == "23514"

    def test_the_dedupe_is_index_backed(self):
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public'"
            " AND tablename='notifications'"
            " AND indexname='uq_notifications_user_idempotency'"
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None, (
            "uq_notifications_user_idempotency is absent, so duplicate suppression "
            "has no authority outside a single process's memory."
        )
        assert "user_id" in row[0] and "idempotency_key" in row[0]
        assert "WHERE" in row[0].upper(), (
            "the index must be PARTIAL -- a total unique index would be a different "
            "guarantee and the NULL case is the common one"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Severity is a vocabulary, and it admits every real alert
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestSeverityIsBounded:
    @pytest.mark.parametrize("severity", PERMITTED_SEVERITIES)
    def test_every_emitted_severity_is_admitted(self, notified_user, severity):
        """A constraint that refused a real severity would drop the alert.

        `critical` matters most: it is what the risk kill-switch emits, and it is
        the single most important notification the platform sends.
        """
        conn = _conn()
        cur = conn.cursor()
        try:
            _insert(cur, notified_user, severity=severity)
        finally:
            conn.close()

    def test_an_unknown_severity_is_refused(self, notified_user):
        import psycopg2

        conn = _conn()
        cur = conn.cursor()
        with pytest.raises(psycopg2.IntegrityError) as caught:
            _insert(cur, notified_user, severity="catastrophic")
        conn.close()
        assert caught.value.pgcode == "23514", (
            "an unrecognised severity must be refused: the notification surface "
            "has no branch to render it, so it would display as an unstyled row"
        )


# ═════════════════════════════════════════════════════════════════════════════
# The handler's predicate agrees with the database
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheHandlerRecognisesTheCollision:
    def test_is_duplicate_notification_matches_a_real_violation(self, notified_user):
        """Connect the database's behaviour to the branch that depends on it.

        If the index raised a code the predicate did not match, a correctly
        de-duplicated redelivery would take the failure path and be reported as a
        notification the customer never received.
        """
        import psycopg2

        from backend_app.routers.notifications import _is_duplicate_notification

        key = f"evt_{uuid.uuid4().hex[:16]}"
        conn = _conn()
        cur = conn.cursor()
        _insert(cur, notified_user, key=key)
        try:
            _insert(cur, notified_user, key=key)
            pytest.fail("the duplicate was accepted")
        except psycopg2.IntegrityError as exc:
            assert _is_duplicate_notification(exc), (
                f"_is_duplicate_notification did not recognise pgcode={exc.pgcode!r}"
            )
        finally:
            conn.close()

    def test_it_does_not_swallow_other_failures(self):
        """Narrow on purpose: a real error misread as 'already delivered' is a
        notification lost with nothing logged."""
        from backend_app.routers.notifications import _is_duplicate_notification

        for other in (
            Exception("connection refused"),
            Exception("permission denied for table notifications"),
            Exception('invalid input syntax for type uuid: "notif_abc"'),
            Exception("23514 check constraint violated"),
        ):
            assert not _is_duplicate_notification(other), (
                f"{other!r} was treated as a duplicate; it must reach the failure branch"
            )


# ═════════════════════════════════════════════════════════════════════════════
# Durability is the dispatcher's property, not each caller's
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestDurabilityDoesNotDependOnTheCallSite:
    def test_the_dispatcher_resolves_a_client_when_none_is_supplied(self):
        """The fix for cause (b), asserted on the dispatcher rather than 18 callers.

        Pinning the behaviour here is what stops the next producer from silently
        re-acquiring the defect by omitting a keyword argument.
        """
        import asyncio

        from backend_app.core import notification_dispatcher as nd

        sentinel = object()
        calls = []

        async def fake_service_client():
            calls.append("resolved")
            return sentinel

        # Patch the import target the resolver reaches for.
        import backend_app.core.dependencies as deps

        original = getattr(deps, "get_service_supabase_async")
        deps.get_service_supabase_async = fake_service_client
        try:
            got = asyncio.run(nd._resolve_persistence_client(None))
        finally:
            deps.get_service_supabase_async = original

        assert got is sentinel, (
            "the dispatcher did not resolve a service client, so a call site that "
            "omits `supabase=` persists nothing -- the NOTIF-01 defect"
        )
        assert calls == ["resolved"]

    def test_a_supplied_client_still_wins(self):
        """routers/support.py passes its own request-scoped client; that must stand."""
        import asyncio

        from backend_app.core import notification_dispatcher as nd

        supplied = object()
        got = asyncio.run(nd._resolve_persistence_client(supplied))
        assert got is supplied

    def test_an_unconfigured_environment_degrades_instead_of_failing(self):
        """No service key is the ordinary local/test shape and must not raise."""
        import asyncio

        from backend_app.core import notification_dispatcher as nd
        import backend_app.core.dependencies as deps

        async def unavailable():
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")

        original = getattr(deps, "get_service_supabase_async")
        deps.get_service_supabase_async = unavailable
        try:
            got = asyncio.run(nd._resolve_persistence_client(None))
        finally:
            deps.get_service_supabase_async = original
        assert got is None, (
            "an unresolvable client must return None so the caller falls back to "
            "the in-memory store, exactly as before this change"
        )


# ═════════════════════════════════════════════════════════════════════════════
# The mandatory floor, pinned because nothing enforces it yet
# ═════════════════════════════════════════════════════════════════════════════
@pg_only
class TestTheMandatoryAlertFloor:
    def test_every_mandatory_event_survives_preference_filtering(self):
        """The floor, asserted DIRECTLY -- which is what this test's predecessor asked for.

        It used to assert that nothing on the dispatch path read `notification_settings`,
        and its failure message named the exact follow-up: *"confirm that every event in
        MANDATORY_EVENT_TYPES is still delivered regardless of preference, then update
        this test to assert the floor directly rather than its absence."*

        NOTIF-04 introduced the filtering, and that instruction caught a real defect in
        it: the first version keyed the floor on `MANDATORY_NOTIFICATION_EVENTS`, which
        has THREE members, while this file has pinned SIX since before any filter
        existed. Four of the six -- `emergency_halt`, `risk_limit_breached`,
        `margin_call`, `exchange_auth_failed` -- have no preference switch and no
        producer, so they were passing only because they were unmapped. That is luck,
        not a floor, and it would have become a live suppressed risk alert the day
        somebody wrote the producer.
        """
        from backend_app.core import notification_dispatcher as nd

        missing = sorted(MANDATORY_EVENT_TYPES - nd.SAFETY_CRITICAL_EVENT_TYPES)
        assert not missing, (
            f"these mandatory event types are not in the dispatcher's safety floor: "
            f"{missing}. A stored preference -- or a row predating migration 073's "
            "CHECK -- could suppress them."
        )

    def test_the_floor_is_consulted_before_anything_is_persisted(self):
        """A filter applied after the insert would leave the row and hide only the socket
        frame, so the user would still see the notification on the next page load."""
        import inspect as _inspect

        from backend_app.core import notification_dispatcher as nd

        src = _inspect.getsource(nd.dispatch_user_notification)
        gate = src.find("_preference_allows")
        write = src.find("create_notification(")
        assert gate != -1, "dispatch_user_notification no longer consults preferences"
        assert write != -1, "dispatch_user_notification no longer persists"
        assert gate < write, (
            "the preference gate runs AFTER the write, so a suppressed notification is "
            "still stored"
        )

    def test_the_mandatory_alert_set_is_pinned(self):
        """The floor itself, as data, so the intent survives this file."""
        assert "kill_switch_activated" in MANDATORY_EVENT_TYPES
        assert "risk_limit_breached" in MANDATORY_EVENT_TYPES
        assert len(MANDATORY_EVENT_TYPES) >= 6

    def test_a_critical_alert_can_be_stored(self, notified_user):
        """The end of the chain for the alert that matters most."""
        conn = _conn()
        cur = conn.cursor()
        _insert(cur, notified_user, severity="critical", event="kill_switch_activated")
        cur.execute(
            "SELECT severity, type FROM public.notifications WHERE user_id = %s",
            (notified_user,),
        )
        row = cur.fetchone()
        conn.close()
        assert row == ("critical", "kill_switch_activated")
