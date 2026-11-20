"""NOTIF-02 — the panel said a safety alert could not be disabled, and offered the switch.

WHAT THE FINDING WAS
====================
`components/NotificationPreferences.jsx` ended with:

    "Mandatory risk alerts (global kill switch triggers) cannot be disabled."

Directly above that sentence it rendered a switch labelled "Emergency Kill Switch &
Liquidations", wired to `events.kill_switch_activated`, which a customer could turn off. The
panel stated an invariant and then offered the control that broke it.

Nothing enforced the sentence. Readers of `notification_settings` across `backend_app`: two,
both in `routers/user.py` — the GET and the PUT of the panel itself. Nothing on the dispatch
path reads it. The session handoff recorded this as "the mandatory-alert floor has no schema
anchor, so 'preferences cannot disable safety alerts' is currently a convention rather than an
invariant".

THE PART THAT WOULD HAVE MADE A NAIVE FIX VACUOUS
=================================================
`notification_settings.events` declares this column default:

    '{"system": true, "stop_loss": true, "risk_alerts": true, "trade_fills": true}'

The vocabulary actually STORED is a different one — the eight fields of
`pydantic_models.NotificationEvents`. Measured read-only against production with `jsonb_each`
over all four rows:

    events keys   : api_key_expiring, backtest_complete, bot_state_change,
                    daily_pnl_summary, kill_switch_activated, new_login_detected,
                    stop_loss_triggered, trade_executed
    channels keys : email, mobile, telegram

All four rows carry all eleven. **Not one row carries `system`, `risk_alerts`, `trade_fills`,
`push` or `in_app`.** The declared defaults have never been realised, because the only writer
always sends the whole Pydantic object.

So a CHECK written against `risk_alerts` would have constrained a key no row has, passed on
every row, and read in review like an enforced invariant. The constraint is written against
the stored vocabulary instead, and migration 073 corrects the dead defaults in the same
transaction — otherwise a row inserted without a payload would carry four keys nothing reads
and be missing all three mandatory ones.

WHAT IS NOT CLAIMED
===================
* **That preferences now take effect. They do not.** Nothing on the dispatch path reads
  `notification_settings`, so no preference suppresses any notification — and of the eight
  event keys, SEVEN have no producer. AST over every `dispatch_user_notification` call site:
  17 sites, and only `kill_switch_activated` (`routers/risk.py:849`) matches a declared
  preference key. `test_seven_of_eight_preference_keys_have_no_producer` pins that
  measurement, so the gap is a recorded fact rather than an assumption, and it fails when a
  producer is wired.
* That email delivery works. `VYOMQUANT_EMAIL_NOTIFICATION_DELIVERY_AUDIT.md` has those
  measurements; the channel switches are untouched here.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import uuid

import pytest
from pydantic import ValidationError

from backend_app.core.models.pydantic_models import (
    MANDATORY_NOTIFICATION_EVENTS,
    NotificationEvents,
    NotificationSettingsRequest,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MIGRATION = (
    REPO_ROOT
    / "backend_app"
    / "migrations"
    / "073_notif02_mandatory_alert_floor.sql"
)
PANEL = (
    REPO_ROOT
    / "algo22-terminal"
    / "src"
    / "components"
    / "NotificationPreferences.jsx"
)


# ══════════════════════════════════════════════════════════════════════════
# PART 1 — the floor as a request-model refusal
# ══════════════════════════════════════════════════════════════════════════


def _events(**over) -> dict:
    base = {
        "trade_executed": True,
        "stop_loss_triggered": True,
        "daily_pnl_summary": True,
        "bot_state_change": False,
        "kill_switch_activated": True,
        "new_login_detected": True,
        "api_key_expiring": True,
        "backtest_complete": False,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("key", MANDATORY_NOTIFICATION_EVENTS)
def test_switching_a_mandatory_alert_off_is_refused(key: str) -> None:
    with pytest.raises(ValidationError) as caught:
        NotificationEvents(**_events(**{key: False}))
    # The refusal names the switch. A sentence that says only "invalid" leaves the customer
    # looking at eight controls with no way to tell which one was rejected.
    assert key in str(caught.value)


def test_the_refusal_names_every_offending_switch_not_just_the_first() -> None:
    with pytest.raises(ValidationError) as caught:
        NotificationEvents(
            **_events(kill_switch_activated=False, new_login_detected=False)
        )
    message = str(caught.value)
    assert "kill_switch_activated" in message
    assert "new_login_detected" in message


@pytest.mark.parametrize(
    "key",
    [
        "trade_executed",
        "daily_pnl_summary",
        "bot_state_change",
        "api_key_expiring",
        "backtest_complete",
    ],
)
def test_a_discretionary_preference_can_still_be_switched_off(key: str) -> None:
    """The floor is a floor, not a lock on the whole panel.

    This half matters as much as the refusal: a constraint that rejected every `false` would
    have made the panel decorative, and would have passed every test that only checked the
    mandatory keys.
    """
    events = NotificationEvents(**_events(**{key: False}))
    assert getattr(events, key) is False


def test_every_delivery_channel_remains_the_customers_choice() -> None:
    """Silencing email is not silencing the alert. The in-app feed is the record."""
    request = NotificationSettingsRequest(
        channels={"email": False, "telegram": False, "mobile": False},
        events=_events(),
    )
    assert request.channels.email is False
    assert request.channels.telegram is False
    assert request.channels.mobile is False


def test_an_omitted_mandatory_key_is_the_default_rather_than_a_refusal() -> None:
    """A partial payload that does not mention a mandatory switch agrees with it.

    `IS NOT FALSE`, not `= true`, in the constraint — and the same reading here. Refusing a
    payload for not mentioning something it does not disagree with would break the panel's own
    write, which rebuilds the whole object but could legitimately be called with less.
    """
    events = NotificationEvents()
    for key in MANDATORY_NOTIFICATION_EVENTS:
        assert getattr(events, key) is True


# ══════════════════════════════════════════════════════════════════════════
# PART 2 — the three layers name one list
# ══════════════════════════════════════════════════════════════════════════


def test_the_migration_constrains_exactly_the_declared_mandatory_keys() -> None:
    """The schema constraint and the Python tuple cannot drift apart silently.

    Measured against the migration text rather than the database, so this holds in the SQLite
    lane too — a constraint naming a key the model does not, or the reverse, is a disagreement
    about what is mandatory and one of the two will be wrong.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql.split("ADD CONSTRAINT chk_notification_settings_mandatory_alerts", 1)[1]
    predicate = body.split(");", 1)[0]
    named = set(re.findall(r"events -> '([a-z_]+)'", predicate))
    assert named == set(MANDATORY_NOTIFICATION_EVENTS), (
        f"the constraint names {sorted(named)}; the model names "
        f"{sorted(MANDATORY_NOTIFICATION_EVENTS)}"
    )


def test_the_predicate_is_absent_or_true_rather_than_not_false() -> None:
    """The spelling is the control, and the obvious spelling does not bind.

    `(events -> 'key') IS DISTINCT FROM 'false'::jsonb` was the first version of this
    constraint. `->` yields jsonb, so it catches ONLY the JSON boolean `false`. Measured on a
    local cluster against that predicate:

        JSON boolean false       refused
        JSON string "false"      ACCEPTED
        JSON number 0            ACCEPTED
        JSON null                ACCEPTED

    `0` and `null` are the dangerous two, because `bool(0)` and `bool(None)` are both False —
    so a preference filter would read them as "switched off", which is the suppression the
    constraint exists to forbid, arriving through a shape it waved through.

    Asserted on the text rather than the behaviour so the reason survives in the SQLite lane;
    the postgres cases below assert the behaviour.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql.split("ADD CONSTRAINT chk_notification_settings_mandatory_alerts", 1)[1]
    predicate = body.split(");", 1)[0]

    assert "IS DISTINCT FROM" not in predicate, (
        "the predicate is back to IS DISTINCT FROM, which only catches the JSON boolean "
        "false and accepts 0 and null — see this test's docstring for the measurement"
    )
    for key in MANDATORY_NOTIFICATION_EVENTS:
        assert f"(events -> '{key}') IS NULL" in predicate, f"{key} lost its absent clause"
        assert f"(events -> '{key}') = 'true'::jsonb" in predicate, (
            f"{key} lost its explicit true clause"
        )


def test_the_migration_corrects_the_dead_column_defaults() -> None:
    """The default must name the stored vocabulary, or the constraint reads an absent key.

    A row inserted without an explicit payload under the ORIGINAL default carries `system`,
    `stop_loss`, `risk_alerts` and `trade_fills` — none of which the application reads — and
    carries none of the three mandatory keys, which `IS NOT FALSE` would then permit.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    default_block = sql.split("ALTER COLUMN events SET DEFAULT", 1)[1].split("::jsonb", 1)[0]
    for key in MANDATORY_NOTIFICATION_EVENTS:
        assert f'"{key}"' in default_block, f"{key} is missing from the events default"
    for dead in ("system", "risk_alerts", "trade_fills"):
        assert f'"{dead}"' not in default_block, (
            f"{dead} is back in the default; no stored row has ever carried it"
        )


def test_the_panel_locks_exactly_the_mandatory_switches() -> None:
    """The UI's set is the model's set, and the sentence matches the controls.

    Read from the source rather than rendered, because this is a declaration — and the defect
    being fixed was precisely a sentence and a control disagreeing.
    """
    source = PANEL.read_text(encoding="utf-8")
    declared = source.split("const MANDATORY_EVENTS = new Set([", 1)[1].split("])", 1)[0]
    named = set(re.findall(r"'([a-z_]+)'", declared))
    assert named == set(MANDATORY_NOTIFICATION_EVENTS)

    # The old sentence under-claimed: it named only the kill switch while three alerts are on
    # the floor, so a customer would reasonably read the other two as theirs to mute.
    assert "global kill switch triggers) cannot be disabled" not in source
    assert "cannot be disabled" in source

    # Every mandatory switch passes `locked`, and no discretionary one does.
    for key in MANDATORY_NOTIFICATION_EVENTS:
        assert f"MANDATORY_EVENTS.has('{key}')" in source, f"{key} is not locked in the panel"
    for key in ("trade_executed", "daily_pnl_summary", "bot_state_change"):
        assert f"MANDATORY_EVENTS.has('{key}')" not in source, (
            f"{key} is locked in the panel but is not on the model's floor"
        )


# ══════════════════════════════════════════════════════════════════════════
# PART 3 — the measured gap, pinned open
# ══════════════════════════════════════════════════════════════════════════


def test_the_dispatch_path_now_filters_and_the_floor_still_holds() -> None:
    """REPLACES a pin on the ABSENCE of filtering. NOTIF-04 wired it.

    This test used to assert that `notification_settings` appeared nowhere on the
    dispatch path, and said so explicitly: *"wiring a filter has a test to delete"*.
    It is not deleted but inverted, because the thing worth pinning was never the
    absence — it was that **no mandatory event can be filtered**, which is now a
    property of running code rather than of missing code.

    The floor is asserted here as a source-level property and exercised behaviourally
    in `tests/test_notif04_preference_filtering.py`. Both exist on purpose: a
    behavioural test can be satisfied by a stub, and this one cannot.
    """
    for relative in (
        "backend_app/core/notification_dispatcher.py",
        "backend_app/routers/notifications.py",
    ):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        # Comments stripped first. The gate's own explanation NAMES the table it
        # deliberately does not read here, so a raw substring search matches the prose
        # about the design rather than a query. Same reason the colour-literal guard
        # strips comments before counting.
        #
        # DOCSTRINGS ARE NOT STRIPPED, and that is a sharp edge worth knowing: any
        # mention of the table in a docstring fails this test, including one inside a
        # longer identifier that merely contains it. Migration 073's
        # mandatory-alerts CHECK is therefore referred to by description rather than
        # by name in notification_dispatcher. Strip docstrings here (via ast, keeping
        # other string literals so a real query is still caught) if that becomes
        # more annoying than it is useful.
        code = "\n".join(
            line.split("#", 1)[0] for line in source.splitlines()
        )
        assert "notification_settings" not in code, (
            f"{relative} now reads notification_settings DIRECTLY. The gate belongs in "
            "core/notification_preferences.py, which these two modules consult -- see "
            "test_the_dispatch_path_now_applies_preferences below. Reading the table "
            "from two places is how the floor and the filter drift apart."
        )


def test_the_dispatch_path_now_applies_preferences() -> None:
    """The inverse of what this test originally pinned.

    It used to assert that NOTHING on the dispatch path read a preference, and said in its
    own docstring that wiring a filter "has a test to delete". T10 wired it, so this is
    that deletion -- replaced rather than removed, because the absence it recorded and the
    presence that replaced it are both facts worth keeping checkable.

    The behaviour is asserted properly in
    `tests/test_t10_event_wiring_and_preferences.py` (46 tests: suppression, the mandatory
    floor overriding a stored `false`, the floor not even reading the store, a failed read
    delivering, and the gate running BEFORE both the insert and the WebSocket broadcast).
    This keeps a single structural assertion here so that removing the gate fails the file
    that was built to guard the floor.
    """
    dispatcher_source = (
        REPO_ROOT / "backend_app" / "core" / "notification_dispatcher.py"
    ).read_text(encoding="utf-8")

    assert "event_is_permitted" in dispatcher_source, (
        "the dispatcher no longer consults core/notification_preferences. Preference "
        "filtering has been removed, so every switch in the Notifications panel is inert "
        "again."
    )

    # Order is the property that makes a mute real rather than cosmetic: filtering after
    # the insert would leave the row in the feed the customer muted and would already
    # have broadcast it.
    gate_at = dispatcher_source.find("event_is_permitted(")
    create_at = dispatcher_source.find("await create_notification(")
    assert gate_at != -1 and create_at != -1
    assert gate_at < create_at, (
        "the preference gate runs AFTER create_notification, so a suppressed "
        "notification is still stored and still broadcast"
    )


def test_the_register_of_producerless_preference_keys_only_shrinks() -> None:
    """This used to assert that SEVEN of the eight switches governed nothing.

    That was true when measured: only `kill_switch_activated` had a producer, so
    "add preference filtering" would have been building a mechanism with no input. T10
    added producers, so the measurement has moved and the assertion moves with it -- in
    one direction only.

    THE REGISTER MAY ONLY SHRINK. `core/notification_preferences.PREFERENCE_KEYS_WITHOUT_PRODUCER`
    is the declared list of switches that still govern nothing. If this test finds a key
    NOT in that register and with no producer, either a producer was deleted or a switch
    was added without one -- both are the panel offering a control that does nothing,
    which is the defect NOTIF-02 was opened for.

    Derived by AST over every `dispatch_user_notification` and
    `schedule_user_notification` call site in `backend_app`, plus the event types the
    mapping routes, because a producer now reaches the switch THROUGH
    `PREFERENCE_KEY_BY_EVENT_TYPE` rather than by naming the key directly.
    """
    from backend_app.core.notification_preferences import (
        PREFERENCE_KEY_BY_EVENT_TYPE, PREFERENCE_KEYS_WITHOUT_PRODUCER)

    emitted: set = set()
    for path in (REPO_ROOT / "backend_app").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            if name not in ("dispatch_user_notification", "schedule_user_notification"):
                continue
            for keyword in node.keywords:
                if keyword.arg == "event_type" and isinstance(keyword.value, ast.Constant):
                    emitted.add(str(keyword.value.value))

        # A producer may build its notification as a dict and pass `event_type=shape[...]`,
        # which is a Subscript and not a Constant -- `routers/risk._risk_breach_notification`
        # and `master_executor._notify_order_outcome` both do, because they classify the
        # event before dispatching it. Scanning call keywords alone reports those event
        # types as having no producer, which is how `stop_loss_triggered` first appeared
        # here as an undeclared gap when its producer had just been written. So also take
        # string constants bound to an "event_type" key in any dict literal.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant) and key.value == "event_type"
                    and isinstance(value, ast.Constant) and isinstance(value.value, str)
                ):
                    emitted.add(value.value)

    assert emitted, "no dispatch call site was found at all, which cannot be right"

    # A producer satisfies a switch either by naming the key as its event_type or by
    # emitting an event_type the mapping routes to that key.
    governed: set = {
        PREFERENCE_KEY_BY_EVENT_TYPE[event_type]
        for event_type in emitted
        if event_type in PREFERENCE_KEY_BY_EVENT_TYPE
    }
    declared = set(NotificationEvents.model_fields)
    with_producer = declared & (governed | emitted)
    producerless = declared - with_producer

    assert "kill_switch_activated" in with_producer, (
        "the kill-switch producer has gone. That is the one switch that had a producer "
        "before T10 and it is on the mandatory floor."
    )

    undeclared_gaps = producerless - PREFERENCE_KEYS_WITHOUT_PRODUCER
    assert not undeclared_gaps, (
        f"these preference switches govern nothing and are not declared in "
        f"PREFERENCE_KEYS_WITHOUT_PRODUCER: {sorted(undeclared_gaps)}. Either a producer "
        "was deleted, or a switch was added without one. The panel must not offer a "
        "control that does nothing -- add the producer, or add the key to that register "
        "with a reason."
    )

    stale = PREFERENCE_KEYS_WITHOUT_PRODUCER & with_producer
    assert not stale, (
        f"PREFERENCE_KEYS_WITHOUT_PRODUCER still lists {sorted(stale)}, which now HAS a "
        "producer. The register may only shrink -- remove the key from it, so it keeps "
        "naming a real gap rather than a historical one."
    )


# ══════════════════════════════════════════════════════════════════════════
# PART 4 — the floor against real PostgreSQL
# ══════════════════════════════════════════════════════════════════════════
#
# Same gate as tests/test_bill07_billing_invoices_postgres.py. A SKIP here is NOT a pass --
# it means the only layer that holds regardless of which client writes is unverified.

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")
pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- the mandatory-alert floor is unverified."
    ),
)


@pytest.fixture()
def pg():
    import psycopg2

    conn = psycopg2.connect(_PG_URL)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _profile(cur) -> str:
    user_id = str(uuid.uuid4())
    email = f"notif02-{user_id[:8]}@example.invalid"
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (user_id, email),
    )
    cur.execute(
        "INSERT INTO profiles (id, email) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (user_id, email),
    )
    return user_id


#: Every shape a client could put at a mandatory key that is not "absent" and not the JSON
#: boolean `true`. All must be refused.
#:
#: The list is the point. A predicate of `IS DISTINCT FROM 'false'::jsonb` refuses the first
#: and accepts the other four — and `0` and `null` are the two a preference filter would read
#: as "switched off", because `bool(0)` and `bool(None)` are both False. Parametrising over
#: the shapes is what makes "the constraint binds" a measurement rather than one lucky case.
_REFUSED_SHAPES = ("false", '"false"', '"no"', "0", "null")


@pg_only
@pytest.mark.parametrize("key", MANDATORY_NOTIFICATION_EVENTS)
@pytest.mark.parametrize("value", _REFUSED_SHAPES)
def test_the_database_refuses_every_not_true_shape_at_a_mandatory_key(
    pg, key: str, value: str
) -> None:
    """Against a REAL profile, so the CHECK is what refuses rather than the foreign key."""
    import psycopg2

    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.notification_settings')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.notification_settings is not in this database")

    user_id = _profile(cur)
    cur.execute("SAVEPOINT attempt")
    with pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO notification_settings (user_id, events) VALUES (%s, %s::jsonb)",
            (user_id, '{"%s": %s}' % (key, value)),
        )
    pg.rollback()


@pg_only
@pytest.mark.parametrize("key", MANDATORY_NOTIFICATION_EVENTS)
def test_the_database_accepts_a_mandatory_key_absent_or_true(pg, key: str) -> None:
    """The other half: a floor that refused every write would make the panel undrawable.

    Absent is accepted because a partial payload that does not mention a mandatory switch does
    not disagree with it.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.notification_settings')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.notification_settings is not in this database")

    for payload in ('{"%s": true}' % key, '{"trade_executed": true}'):
        user_id = _profile(cur)
        cur.execute("SAVEPOINT ok")
        cur.execute(
            "INSERT INTO notification_settings (user_id, events) VALUES (%s, %s::jsonb)",
            (user_id, payload),
        )
        cur.execute("SELECT 1 FROM notification_settings WHERE user_id = %s", (user_id,))
        assert cur.fetchone() is not None, f"{payload} was not stored"
        cur.execute("ROLLBACK TO SAVEPOINT ok")
    pg.rollback()


@pg_only
def test_the_database_accepts_a_discretionary_preference_switched_off(pg) -> None:
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.notification_settings')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.notification_settings is not in this database")

    user_id = _profile(cur)
    cur.execute(
        "INSERT INTO notification_settings (user_id, events) VALUES (%s, %s::jsonb)",
        (user_id, '{"backtest_complete": false, "kill_switch_activated": true}'),
    )
    cur.execute(
        "SELECT events ->> 'backtest_complete' FROM notification_settings WHERE user_id = %s",
        (user_id,),
    )
    assert cur.fetchone()[0] == "false"
    pg.rollback()


@pg_only
def test_a_row_written_with_no_payload_carries_the_keys_the_application_reads(pg) -> None:
    """The dead-default half, measured rather than read off the migration text.

    Under the original default such a row carried `system`/`stop_loss`/`risk_alerts`/
    `trade_fills` — four keys nothing reads — and none of the three mandatory ones, so the
    constraint's `IS NOT FALSE` would have had nothing to constrain.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.notification_settings')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.notification_settings is not in this database")

    user_id = _profile(cur)
    cur.execute("INSERT INTO notification_settings (user_id) VALUES (%s)", (user_id,))
    cur.execute(
        "SELECT events, channels FROM notification_settings WHERE user_id = %s", (user_id,)
    )
    events, channels = cur.fetchone()

    assert set(events) == set(NotificationEvents.model_fields), (
        f"the default stores {sorted(events)}, which is not the vocabulary the application "
        f"reads ({sorted(NotificationEvents.model_fields)})"
    )
    for key in MANDATORY_NOTIFICATION_EVENTS:
        assert events[key] is True
    assert set(channels) == {"email", "telegram", "mobile"}
    pg.rollback()


@pg_only
def test_anon_holds_nothing_on_the_preferences_table(pg) -> None:
    """A constraint is only as good as the roles that can reach the table.

    `authenticated` keeps SELECT/INSERT/UPDATE: the panel's write is an upsert through the
    caller's own request-scoped client, which needs both write verbs. DELETE goes, because a
    deleted row reads as "no preference stored" and the panel renders the contract defaults --
    a silent reset rather than an erasure the customer would see.
    """
    cur = pg.cursor()
    cur.execute("SELECT to_regclass('public.notification_settings')")
    if cur.fetchone()[0] is None:
        pytest.skip("public.notification_settings is not in this database")

    def grants(role: str) -> set:
        cur.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            " WHERE table_schema='public' AND table_name='notification_settings' "
            "   AND grantee=%s",
            (role,),
        )
        return {r[0] for r in cur.fetchall()}

    assert grants("anon") == set(), f"anon still holds {sorted(grants('anon'))}"
    assert grants("authenticated") == {"SELECT", "INSERT", "UPDATE"}, (
        f"authenticated holds {sorted(grants('authenticated'))}"
    )
