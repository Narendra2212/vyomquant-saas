"""The pgcrypto preflight must FAIL when pgcrypto is absent, not just pass when present.

WHY THIS FILE IS MANDATORY. `009_paper_trading.sql` already carried a pgcrypto
precondition, and it could not fail for its stated reason. It tested for
``gen_random_uuid()``, which has been in **core** PostgreSQL since version 13, so on any
modern server the check passed whether or not the extension existed. A guard that cannot
fail is read as coverage and provides none -- and what it was supposed to be protecting
was reached a different way:

``migrations/referral_system_redesign.sql`` defines ``generate_unique_referral_code()``
around ``gen_random_bytes(4)``, which IS pgcrypto's, and
``trigger_create_referral_code`` on ``public.profiles`` calls it. So without pgcrypto
every ``INSERT INTO public.profiles`` fails:

    UndefinedFunction: function gen_random_bytes(integer) does not exist
    CONTEXT: PL/pgSQL function generate_unique_referral_code() line 10
             SQL statement "SELECT create_referral_code_for_user(NEW.id)"

A profiles row is created for every user, so that is signup broken outright -- BUG-029.
Production never saw it because Supabase enables pgcrypto by default, which is exactly
why the dependency stayed invisible and why a disaster-recovery rebuild onto plain
PostgreSQL would not have produced a working system.

So both states are exercised here, as the remediation brief requires:

    State A  dependency satisfied  -> the preflight passes
    State B  dependency absent     -> the preflight RAISES, naming the consequence

State B is produced by dropping the function the preflight tests for, running the
preflight's own SQL, and restoring it. Dropping a function is reversible and scoped; the
alternative -- uninstalling an extension -- is not available on every server and would
not reproduce the condition on the `pgserver` build used for verification, which ships no
pgcrypto at all and is served by a documented stand-in from
`scripts/provision_database.py --create-substrate`.

Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason="AERORA_TEST_DATABASE_URL is unset or not PostgreSQL.",
)

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATION_009 = REPO / "backend_app" / "migrations" / "009_paper_trading.sql"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _preflight_sql() -> str:
    """The `gen_random_bytes` precondition, lifted from 009 rather than retyped.

    Extracted from the migration so the test cannot drift from the thing it is testing.
    If someone deletes the check, this stops finding it and the extraction assertion
    below fails -- which is the correct outcome, because the guard would be gone.
    """
    source = MIGRATION_009.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"(IF NOT EXISTS \(\s*SELECT 1 FROM pg_proc p.*?proname = 'gen_random_bytes'.*?END IF;)",
        source,
        re.S,
    )
    assert match, (
        "009_paper_trading.sql no longer contains a gen_random_bytes precondition. "
        "That check is what stops a database without pgcrypto from accepting the whole "
        "provisioning run and then failing every user signup (BUG-029)."
    )
    return f"DO $$\nBEGIN\n{match.group(1)}\nEND $$;"


# ── the dependency itself ────────────────────────────────────────────────────


def test_gen_random_bytes_is_available_on_the_provisioned_database(engine):
    """State A, at the level that matters: the function resolves and returns bytes."""
    with engine.begin() as conn:
        value = conn.execute(text("SELECT gen_random_bytes(4)")).scalar()
    assert value is not None
    assert len(bytes(value)) == 4, f"gen_random_bytes(4) returned {len(bytes(value))} bytes"


def test_the_preflight_passes_while_the_dependency_is_present(engine):
    """State A. Must not raise."""
    with engine.begin() as conn:
        conn.execute(text(_preflight_sql()))


# ── State B: the dependency removed ─────────────────────────────────────────


def test_the_preflight_fails_when_gen_random_bytes_is_absent(engine):
    """State B -- the half that was missing, and the whole point of this file.

    The function is dropped, the preflight is run, and it must raise with a message that
    names pgcrypto and says what breaks. Then it is restored.

    Restoration is in a `finally` and is verified afterwards by
    `test_gen_random_bytes_is_restored_after_the_absence_test`, because a test that left
    the verification database unable to accept a signup would poison every later run.
    """
    with engine.begin() as conn:
        definition = conn.execute(
            text(
                "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.proname = 'gen_random_bytes' LIMIT 1"
            )
        ).scalar()
    assert definition, "cannot run State B: gen_random_bytes has no definition to restore"

    try:
        with engine.begin() as conn:
            conn.execute(text("DROP FUNCTION IF EXISTS public.gen_random_bytes(integer)"))

        with engine.begin() as conn:
            still_there = conn.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'gen_random_bytes'")
            ).scalar()
        if still_there:
            pytest.skip(
                "gen_random_bytes survives the drop, so it comes from a real pgcrypto "
                "extension in a schema this test will not uninstall. State B cannot be "
                "produced non-destructively here."
            )

        with engine.begin() as conn:
            with pytest.raises(Exception) as excinfo:
                conn.execute(text(_preflight_sql()))

        message = str(excinfo.value)
        assert "pgcrypto" in message, (
            f"the preflight failed but did not name pgcrypto, so an operator would not "
            f"know what to install: {message}"
        )
        assert "gen_random_bytes" in message, message
        assert "signup" in message.lower() or "profiles" in message.lower(), (
            f"the preflight's message does not say what breaks. A diagnostic that names "
            f"a missing function without naming the consequence is how BUG-029 survived: "
            f"{message}"
        )
    finally:
        with engine.begin() as conn:
            conn.execute(text(definition))


def test_gen_random_bytes_is_restored_after_the_absence_test(engine):
    """Ordering-dependent on purpose, and harmless if it runs first.

    State B drops a function the rest of the database needs. This asserts the restore
    actually happened, so a failure in that test's cleanup surfaces here rather than as
    an unrelated signup failure later in the session.
    """
    with engine.begin() as conn:
        value = conn.execute(text("SELECT gen_random_bytes(4)")).scalar()
    assert value is not None and len(bytes(value)) == 4


# ── the consequence, end to end ─────────────────────────────────────────────


def test_a_profiles_insert_is_what_actually_depends_on_this(engine):
    """Ties the preflight to the thing it protects.

    The preflight is a proxy. This is the real requirement: a profiles row can be
    created, which means the referral trigger ran, which means `gen_random_bytes` was
    callable. If this passes and the preflight tests fail, the preflight is wrong; if
    this fails, signup is broken regardless of what the preflight says.
    """
    import uuid

    tid = str(uuid.uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
                {"i": tid, "e": f"{tid[:8]}@example.test"},
            )
            conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": tid})
            code = conn.execute(
                text("SELECT code FROM public.referral_codes WHERE user_id = :i"),
                {"i": tid},
            ).scalar()
        assert code, (
            "the profiles insert succeeded but no referral code was created, so "
            "trigger_create_referral_code did not fire"
        )
        assert code.startswith("VQ-"), f"unexpected referral code format: {code!r}"
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM public.referral_codes WHERE user_id = :i"), {"i": tid}
            )
            conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": tid})
            conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": tid})
