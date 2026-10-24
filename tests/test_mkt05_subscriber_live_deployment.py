"""MKT-05 -- a subscriber may deploy live, and only for the period they paid for.

WHAT THIS FILE GUARDS
----------------------
The feature is "a marketplace subscriber can deploy the listing they bought to LIVE
trading, bounded by their subscribed period". The capability is the easy half. The hard
half is the bound, and it has three independent enforcement points that must agree:

  1. ``marketplace/entitlement_resolver.py`` refuses a NEW deploy past expiry (already
     existed, already tested elsewhere);
  2. ``master_executor.evaluate_authorization_window`` refuses an ORDER past expiry, on
     the order path, every tick -- the only one that does not depend on a background
     worker being alive;
  3. ``marketplace/expiry_sweep.py`` asks a running live deployment to stop and flatten
     (tested in ``tests/test_expiry_sweep.py``).

The tests here concentrate on (2) and on the two vocabulary agreements that make the
whole thing hold together, because those are where a wrong answer costs real money rather
than returning a wrong status code.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend_app.backend.master_executor import (_coerce_authorized_until,
                                                 evaluate_authorization_window)

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_004E = _ROOT / "backend_app" / "migrations" / "004e_deployment_bindings.sql"
_MIGRATION_056 = (
    _ROOT / "backend_app" / "migrations"
    / "056_mkt05_subscriber_live_authorization_window.sql"
)

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ══════════════════════════════════════════════════════════════════════════════
#  The authorisation window: the control that does not depend on a worker
# ══════════════════════════════════════════════════════════════════════════════


def test_an_unbounded_deployment_is_never_refused():
    """A first-party deployment is entitled by ownership and has no period, and so is
    every deployment that predates MKT-05. ``None`` must be a complete no-op, or this
    change would stop every bot on the platform."""
    assert evaluate_authorization_window(None, now=NOW) is None


def test_a_future_window_admits_the_order():
    assert evaluate_authorization_window(NOW + timedelta(seconds=1), now=NOW) is None


def test_a_past_window_refuses_the_order():
    reason = evaluate_authorization_window(NOW - timedelta(seconds=1), now=NOW)
    assert reason is not None
    assert "subscription period ended" in reason


def test_the_boundary_instant_itself_refuses():
    """``>=``, not ``>``, and this is the agreement that matters.

    ``marketplace/entitlement_resolver.py`` treats ``now >= period_expiry`` as EXPIRED --
    ``tests/test_entitlement_resolver.py::test_now_equal_to_period_expiry_is_expired``
    and ``tests/property/test_entitlement_expiry_boundary.py`` pin it to the microsecond.
    If this function used ``>`` it would admit one order at exactly the instant the
    resolver would refuse a new deployment for, which is two parts of the platform
    disagreeing about when a paid period ends.
    """
    assert evaluate_authorization_window(NOW, now=NOW) is not None


def test_the_refusal_names_both_instants():
    """An operator reading this line needs to know when it ended and when we noticed,
    without going to the database."""
    expiry = NOW - timedelta(hours=3)
    reason = evaluate_authorization_window(expiry, now=NOW)
    assert expiry.isoformat() in reason
    assert NOW.isoformat() in reason


@pytest.mark.parametrize("microseconds", [1, 1000, 999_999])
def test_a_window_that_closed_microseconds_ago_still_refuses(microseconds):
    """No grace period, deliberately. A grace period on somebody else's money is a
    decision for the product, not a rounding artefact of an implementation."""
    assert evaluate_authorization_window(
        NOW - timedelta(microseconds=microseconds), now=NOW
    ) is not None


# ══════════════════════════════════════════════════════════════════════════════
#  Reading the window off a blueprint: an unreadable value is not "no limit"
# ══════════════════════════════════════════════════════════════════════════════


def test_none_is_unbounded():
    assert _coerce_authorized_until(None) is None


def test_an_aware_datetime_passes_through():
    assert _coerce_authorized_until(NOW) == NOW


def test_a_naive_datetime_is_read_as_utc():
    """Every instant in this codebase is stored UTC. Reading a naive one as UTC is what
    keeps the comparison in ``evaluate_authorization_window`` from raising on a
    naive/aware mismatch -- which would crash the tick loop and with it the refusal."""
    naive = datetime(2026, 6, 1, 12, 0, 0)
    assert _coerce_authorized_until(naive) == NOW


@pytest.mark.parametrize(
    "text", ["2026-06-01T12:00:00+00:00", "2026-06-01T12:00:00Z"]
)
def test_an_iso_string_is_read(text):
    """A deployment resumed after a restart rebuilds its blueprint from the row, where
    the ``TIMESTAMPTZ`` arrives as whatever PostgREST serialised it to."""
    assert _coerce_authorized_until(text) == NOW


def test_an_empty_string_is_unbounded():
    assert _coerce_authorized_until("") is None
    assert _coerce_authorized_until("   ") is None


@pytest.mark.parametrize("bad", ["not-a-date", "2026-13-45T99:99:99", "soon"])
def test_an_unparseable_value_refuses_rather_than_reading_as_no_limit(bad):
    """THE POINT OF THIS WHOLE HELPER.

    Returning ``None`` for a value that was present but unreadable would silently convert
    a bounded live deployment into an unbounded one -- a live bot trading somebody's money
    with no end date, which is the exact outcome this feature exists to prevent. Refusing
    to start is strictly better than starting without the bound.
    """
    with pytest.raises(RuntimeError) as exc:
        _coerce_authorized_until(bad)
    assert "no expiry" in str(exc.value)


@pytest.mark.parametrize("bad", [123, 12.5, [], {}, object()])
def test_an_unsupported_type_also_refuses(bad):
    with pytest.raises(RuntimeError):
        _coerce_authorized_until(bad)


# ══════════════════════════════════════════════════════════════════════════════
#  Vocabulary agreements -- parsed from the files, so neither side can drift alone
# ══════════════════════════════════════════════════════════════════════════════


def _chk_sd_mode_vocabulary() -> set:
    text = _MIGRATION_004E.read_text(encoding="utf-8")
    m = re.search(r"ADD CONSTRAINT chk_sd_mode\s*CHECK\s*\(\s*mode\s+IN\s*\((.*?)\)",
                  text, re.S | re.I)
    assert m, "could not find chk_sd_mode in 004e; if it moved, repoint this test"
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


def test_the_endpoint_accepts_exactly_the_modes_the_database_admits():
    """``_DEPLOY_MODES`` and ``chk_sd_mode`` must name the same set.

    Accepting a mode the CHECK refuses turns a 422 into a ``23514`` surfacing as a 500.
    Refusing one the CHECK admits silently removes a capability. Both sides are read from
    their own source here, so changing either alone fails this test.
    """
    from backend_app.routers.library import _DEPLOY_MODES

    assert set(_DEPLOY_MODES) == _chk_sd_mode_vocabulary()


def test_the_expiry_sweeps_two_branches_cover_the_whole_vocabulary():
    """The sweep matches live and paper by EQUALITY, one branch each. That partition is
    total only while the vocabulary is exactly those two values.

    If someone adds a third mode, the sweep would silently stop handling it -- a live-ish
    deployment nothing asks to stop when its period ends. This test is what makes that a
    failure with a pointer rather than a gap.
    """
    from backend_app.backend.marketplace.expiry_sweep import (
        LIVE_DEPLOYMENT_MODE, PAPER_DEPLOYMENT_MODE)

    assert {LIVE_DEPLOYMENT_MODE, PAPER_DEPLOYMENT_MODE} == _chk_sd_mode_vocabulary()


def test_the_sweep_does_not_declare_a_live_deployment_stopped():
    """A live row goes to 'stopping', never straight to 'stopped'. Asserted on the
    constants rather than only through the behavioural test in test_expiry_sweep.py, so
    the distinction is pinned even if that fixture is rewritten."""
    from backend_app.backend.marketplace.expiry_sweep import (
        STOPPED_DEPLOYMENT_STATUS, STOPPING_DEPLOYMENT_STATUS)

    assert STOPPING_DEPLOYMENT_STATUS == "stopping"
    assert STOPPED_DEPLOYMENT_STATUS != STOPPING_DEPLOYMENT_STATUS


# ══════════════════════════════════════════════════════════════════════════════
#  The database refuses an unbounded live marketplace deployment
# ══════════════════════════════════════════════════════════════════════════════


def test_056_bounds_a_live_marketplace_deployment():
    """The CHECK is the backstop for every caller, including ones not written yet.

    A source-level check, deliberately: the behavioural proof lives in the migration's own
    self-exercise, which inserts an unbounded live marketplace row against a real server
    and requires ``check_violation``. This asserts the constraint is still declared and
    still names all three columns, so deleting any part of it fails here.
    """
    text = _MIGRATION_056.read_text(encoding="utf-8")
    m = re.search(
        r"ADD CONSTRAINT chk_sd_marketplace_live_is_bounded CHECK \((.*?)\n\s*\);",
        text, re.S)
    assert m, "chk_sd_marketplace_live_is_bounded is no longer declared in 056"
    predicate = m.group(1)
    for column in ("mode", "marketplace_listing_id", "authorized_until",
                   "subscription_id"):
        assert column in predicate, (
            f"{column} is no longer part of chk_sd_marketplace_live_is_bounded, so a live "
            "marketplace deployment could be created without it"
        )


def test_056_self_test_cannot_skip():
    """MKT-03's lesson, and the first version of 056 learned it the hard way.

    056 originally self-tested by INSERTing an unbounded live marketplace deployment and
    requiring ``check_violation``. Against the real production database that SKIPPED
    rather than proving anything: ``strategy_deployments`` there has four NOT NULL columns
    with no default (``strategy_id``, ``user_id``, ``version_id``, ``version``), three of
    them carrying foreign keys, so the fixture tripped ``not_null_violation`` and the
    handler -- correctly, so the migration could still apply -- skipped.

    A self-test that skips reports success either way. That is exactly what let migration
    050 sit un-appliable to production for weeks.

    So 056 now reads the INSTALLED constraint out of ``pg_constraint`` and evaluates it
    against a truth table as a plain SELECT. This test pins the two properties that make
    that version trustworthy: it needs no fixture row, and it covers the cases that must
    be ADMITTED as well as the ones that must be refused.
    """
    text = _MIGRATION_056.read_text(encoding="utf-8")

    assert "pg_get_constraintdef" in text, (
        "056's self-test must read the INSTALLED constraint rather than assume the file's "
        "own text is what the database has"
    )
    assert "INSERT INTO public.strategy_deployments" not in text, (
        "056's self-test must not depend on inserting a strategy_deployments row: that "
        "needs a user, a profile, a strategy and a version to satisfy four NOT NULL "
        "columns and three foreign keys, and every link in that chain is another way for "
        "the test to skip instead of proving something"
    )
    assert "RAISE EXCEPTION" in text and "self-test FAILED" in text, (
        "056's self-test must fail the migration when the bound is wrong, not warn"
    )

    # It must cover the admit cases, not only the refusals. A constraint that wrongly
    # refused a first-party live deployment would be a serious regression that a
    # refusal-only test would never notice.
    for admitted in ("FIRST-PARTY live deployment", "paper marketplace deployment"):
        assert admitted in text, (
            f"056's truth table must include the {admitted!r} case, which must be ADMITTED"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  The runtime carries the window, and a restart cannot extend it
# ══════════════════════════════════════════════════════════════════════════════


def test_the_blueprint_always_answers_whether_it_is_bounded():
    """``authorized_until`` is written unconditionally, including as ``None``, so the
    question is answered explicitly rather than by the absence of a key -- which a
    ``.get`` elsewhere could then read as "unbounded" for the wrong reason."""
    from backend_app.backend.deployment_runtime import build_runtime_blueprint

    version_row = {
        "id": "ver-1",
        "version": "1.0",
        "blueprint": {"nodes": [{"id": "n1"}], "edges": []},
    }
    blueprint = build_runtime_blueprint(
        version_row,
        deployment_id="dep-1",
        strategy_id="strat-1",
        symbol="BTC/USDT",
        timeframe="1h",
        exchange_id="binance",
        mode="paper",
    )
    assert "authorized_until" in blueprint
    assert blueprint["authorized_until"] is None

    bounded = build_runtime_blueprint(
        version_row,
        deployment_id="dep-2",
        strategy_id="strat-1",
        symbol="BTC/USDT",
        timeframe="1h",
        exchange_id="binance",
        mode="live",
        authorized_until=NOW,
    )
    assert bounded["authorized_until"] == NOW


def test_resume_reads_the_window_off_the_row():
    """A restart must not extend a paid period. ``rebuild_for_resume`` takes the instant
    from the row, so a deployment whose window already closed comes back as a bot that
    refuses its first order rather than one that trades until a sweep notices."""
    import inspect

    from backend_app.backend import deployment_runtime

    source = inspect.getsource(deployment_runtime.rebuild_for_resume)
    assert 'authorized_until=row.get("authorized_until")' in source, (
        "rebuild_for_resume must carry authorized_until from the deployment row, or a "
        "resumed live marketplace deployment would come back unbounded"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  EXCH-03: a deployment must be able to name an exchange account
# ══════════════════════════════════════════════════════════════════════════════

_MIGRATION_058 = (
    _ROOT / "backend_app" / "migrations"
    / "058_exch03_exchange_keys_stable_handle.sql"
)


def test_058_gives_exchange_keys_a_single_column_handle():
    """EXCH-03. ``load_owned_exchange_account`` resolves a deployment's
    ``exchange_account_id`` with ``.eq("id", ...)``, and ``exchange_keys`` had no ``id``
    column at all -- eleven columns, primary key the composite
    ``(user_id, exchange_id)``, measured against production. So that query raised
    ``42703``, the resolver's own ``except/continue`` swallowed it as "table
    unavailable", and EVERY live deployment answered 404 -- first-party as well as
    marketplace.

    The migration adds the handle. This asserts it is still declared and still unique,
    because without uniqueness a handle does not identify an account.
    """
    text = _MIGRATION_058.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS id" in text, (
        "058 must add the id handle; without it no live deployment can name an account"
    )
    assert "uq_exchange_keys_id" in text, (
        "058 must make the handle unique, or it does not identify one account"
    )
    assert "SET NOT NULL" in text, (
        "a NULL handle is a row no deployment can name, so the column must be NOT NULL"
    )


def test_058_does_not_replace_the_primary_key():
    """The composite ``(user_id, exchange_id)`` stays the primary key, deliberately.

    It is the real constraint -- one credential per user per venue -- and it is what
    ``api_key_vault.load_decrypted_keys(user_id, exchange_id)`` resolves credentials by.
    Replacing the primary key of a table holding encrypted customer credentials to add a
    lookup handle would be a far larger risk than adding a column beside it.
    """
    text = _MIGRATION_058.read_text(encoding="utf-8")

    # Comment lines are stripped before any of this is judged. The file's header
    # DISCUSSES the primary key at length -- explaining why it is left alone -- and a
    # scan that read those sentences as statements would be checking prose.
    statements = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("--")
    ).upper()

    assert "DROP CONSTRAINT" not in statements, (
        "058 must not drop a constraint: the composite primary key is what "
        "api_key_vault.load_decrypted_keys resolves credentials by"
    )
    for forbidden in ("ADD PRIMARY KEY", "EXCHANGE_KEYS_PKEY"):
        assert forbidden not in statements, (
            f"058 must not touch the primary key ({forbidden.lower()} found in a "
            "statement). The handle is added beside it, not instead of it."
        )


def test_058_accepts_both_live_shapes():
    """The from-source shape and production's shape differ, and both must pass.

    ``migrations/003_create_exchange_keys_table.sql`` declares ``id TEXT PRIMARY KEY``,
    so a database provisioned from repository source already has a handle -- as text, and
    as the primary key. Production's table was never created by that migration and had no
    handle at all, so 058 adds one as ``uuid``.

    An earlier draft asserted ``data_type='uuid'`` in its postflight, which would have
    failed on every CI run. The postflight must accept either.
    """
    text = _MIGRATION_058.read_text(encoding="utf-8")
    assert "'uuid', 'text', 'character varying'" in text, (
        "058's postflight must accept a text handle as well as a uuid one, or it fails on "
        "every database provisioned from repository source (003 declares id TEXT)"
    )


def test_the_resolver_still_looks_up_by_the_handle():
    """Pins the dependency from the other end.

    If ``load_owned_exchange_account`` is ever changed to resolve by something other than
    ``id``, 058's whole reason for existing goes with it -- and this test, rather than a
    404 in production, is where that should surface.
    """
    import inspect

    from backend_app.backend import deployment_binding

    source = inspect.getsource(deployment_binding.load_owned_exchange_account)
    assert '.eq("id", exchange_account_id)' in source, (
        "load_owned_exchange_account no longer resolves by `id`. If that is deliberate, "
        "058's handle may be unnecessary -- but check first, because this is the lookup "
        "that answered 404 for every live deployment before the handle existed."
    )
    assert "exchange_keys" in inspect.getsource(deployment_binding).split(
        "EXCHANGE_ACCOUNT_TABLES"
    )[1][:200], "exchange_keys must remain one of the candidate account tables"
