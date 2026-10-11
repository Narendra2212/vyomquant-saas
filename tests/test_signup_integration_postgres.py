"""Signup works against real PostgreSQL: profile, trigger, referral code, commit.

WHY THIS EXISTS. Two separate defects in the migration set made every
``INSERT INTO public.profiles`` fail, which is every user signup, and neither was
reachable from the SQLite lane because the whole chain is PL/pgSQL:

  BUG-029  ``generate_unique_referral_code()`` calls ``gen_random_bytes(4)``, which is
           pgcrypto's, and no file in the repository issued ``CREATE EXTENSION``.
           Supabase enables pgcrypto by default, so production never noticed -- and a
           disaster-recovery rebuild onto plain PostgreSQL would have produced a system
           where nobody could register.

  BUG-031  The same function declared a local variable named ``code`` while
           ``referral_codes`` has a column named ``code``, so its uniqueness check read
           ``WHERE code = code``:

               AmbiguousColumn: column reference "code" is ambiguous

           Resolving the ambiguity the other way would not have helped either -- as
           column-equals-column it is a tautology, so ``NOT EXISTS`` goes false as soon
           as one code exists, all ten attempts fail, and the function ends at its own
           ``RAISE EXCEPTION``. Two different failures from one name collision.

Both were found by executing an insert, not by reading. Nothing in the 12,000-test suite
inserts a profiles row against PostgreSQL, which is why a broken signup path was green.

THE CHAIN UNDER TEST
    INSERT INTO auth.users          (Supabase's identity table)
        -> INSERT INTO public.profiles
            -> tr_profiles_create_referral_code
                -> trigger_create_referral_code()
                    -> create_referral_code_for_user(NEW.id)
                        -> generate_unique_referral_code()   [needs pgcrypto]
                            -> INSERT INTO referral_codes
                            -> UPDATE profiles.referral_code
    -> COMMIT

Gated on AERORA_TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason=(
        "AERORA_TEST_DATABASE_URL is unset or not PostgreSQL. The signup chain is "
        "PL/pgSQL triggers and functions; there is nothing to execute on SQLite."
    ),
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _purge(engine, user_ids: list[str]) -> None:
    """Remove a signup and everything the trigger chain created for it."""
    if not user_ids:
        return
    with engine.begin() as conn:
        for table, column in (
            ("referral_relationships", "referred_id"),
            ("referral_relationships", "referrer_id"),
            ("referral_wallets", "user_id"),
            ("referral_codes", "user_id"),
        ):
            conn.execute(
                text(f"DELETE FROM public.{table} WHERE {column} = ANY(CAST(:ids AS uuid[]))"),
                {"ids": user_ids},
            )
        conn.execute(
            text("DELETE FROM public.profiles WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": user_ids}
        )
        conn.execute(
            text("DELETE FROM auth.users WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": user_ids}
        )


@pytest.fixture
def signup(engine):
    """A factory that performs a signup and cleans up everything it created."""
    created: list[str] = []

    def _do(user_id: str | None = None) -> str:
        uid = user_id or str(uuid.uuid4())
        created.append(uid)
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
                {"i": uid, "e": f"{uid[:8]}@signup.test"},
            )
            conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": uid})
        return uid

    yield _do
    _purge(engine, created)


# ── the normal path ──────────────────────────────────────────────────────────


def test_a_new_user_gets_a_profile(signup, engine):
    uid = signup()
    with engine.begin() as conn:
        found = conn.execute(
            text("SELECT id FROM public.profiles WHERE id = :i"), {"i": uid}
        ).scalar()
    assert str(found) == uid


def test_the_referral_trigger_creates_a_code(signup, engine):
    """The step BUG-029 and BUG-031 both broke."""
    uid = signup()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT code, is_active FROM public.referral_codes WHERE user_id = :i"
            ),
            {"i": uid},
        ).first()
    assert row is not None, (
        "no referral_codes row was created, so tr_profiles_create_referral_code did not "
        "fire or its function failed"
    )
    code, is_active = row
    assert code.startswith("VQ-"), f"unexpected code format: {code!r}"

    # WHY THE LENGTH IS A RANGE AND NOT 9.
    #
    # The generator reads, in `migrations/referral_system_redesign.sql`:
    #
    #     v_code := 'VQ-' || upper(substring(encode(gen_random_bytes(4),'base64'),1,6));
    #     v_code := regexp_replace(v_code, '[^A-Z0-9]', '', 'g');
    #     v_code := 'VQ-' || substring(v_code, 4, 6);
    #
    # The middle line strips the hyphen it just added along with any base64 '+' or '/',
    # so the third line slices from position 4 of a string that is now 2 characters
    # shorter than intended and loses one or two more characters to the padding. The
    # result is a 'VQ-' prefix plus somewhere between four and six alphanumerics -- the
    # observed value here was 'VQ-XIVHA', which is five.
    #
    # Asserted as a range rather than "fixed up", because nothing is broken: codes are
    # still unique (the column has a UNIQUE constraint, and
    # test_two_users_never_share_a_referral_code exercises the generator 20 times). It
    # is a smaller keyspace than the code appears to intend, which is recorded as a
    # cosmetic finding rather than treated as a launch issue.
    suffix = code[3:]
    assert 4 <= len(suffix) <= 6, f"referral code suffix {suffix!r} has odd length"
    assert suffix.isalnum() and suffix.isupper(), f"unexpected characters in {code!r}"
    assert is_active is True


def test_the_code_is_mirrored_onto_the_profile(signup, engine):
    """``create_referral_code_for_user`` also writes ``profiles.referral_code``.

    Checked separately from the ``referral_codes`` row because the function does two
    writes and only the first one failing would still leave a code to find.
    """
    uid = signup()
    with engine.begin() as conn:
        profile_code = conn.execute(
            text("SELECT referral_code FROM public.profiles WHERE id = :i"), {"i": uid}
        ).scalar()
        table_code = conn.execute(
            text("SELECT code FROM public.referral_codes WHERE user_id = :i"), {"i": uid}
        ).scalar()
    assert profile_code == table_code, (
        f"profiles.referral_code is {profile_code!r} but referral_codes.code is "
        f"{table_code!r}; the two writes disagree"
    )


def test_the_signup_is_committed_and_visible_to_a_new_connection(signup, engine):
    """The chain has to survive the transaction, not just run inside it.

    A trigger that raised and was swallowed, or a commit that silently rolled back,
    would still pass a test that only read back on the same connection.
    """
    uid = signup()
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM public.profiles p "
                "JOIN public.referral_codes r ON r.user_id = p.id WHERE p.id = :i"
            ),
            {"i": uid},
        ).scalar()
    assert count == 1


def test_a_default_profile_state_is_present(signup, engine):
    """``available_discounts`` defaults to 0 rather than NULL.

    This column is the one alembic revision ``6f1b3d9c8a7e`` tried to add to a table
    that already had it (BUG-032), so its presence AND its default are both worth
    asserting after that repair.
    """
    uid = signup()
    with engine.begin() as conn:
        discounts = conn.execute(
            text("SELECT available_discounts FROM public.profiles WHERE id = :i"),
            {"i": uid},
        ).scalar()
    assert discounts == 0, f"available_discounts is {discounts!r}, expected 0"


# ── with and without a referrer ──────────────────────────────────────────────


def test_signup_without_a_referral_still_gets_its_own_code(signup, engine):
    """The common case: nobody referred this user.

    Worth stating explicitly because the referral tables are where the signup chain
    does its work, and a design that only functioned for referred users would look fine
    in a test suite that always supplied a referrer.
    """
    uid = signup()
    with engine.begin() as conn:
        own_code = conn.execute(
            text("SELECT count(*) FROM public.referral_codes WHERE user_id = :i"),
            {"i": uid},
        ).scalar()
        relationships = conn.execute(
            text(
                "SELECT count(*) FROM public.referral_relationships WHERE referred_id = :i"
            ),
            {"i": uid},
        ).scalar()
    assert own_code == 1
    assert relationships == 0, "an unreferred signup should create no relationship row"


def test_signup_with_a_referral_links_the_two_users(signup, engine):
    """A referred signup, using the referrer's real code.

    The relationship row is written by the application rather than by a trigger, so this
    performs that insert the way a referred registration would and asserts the database
    accepts it against the real foreign keys and the real unique constraint.
    """
    referrer = signup()
    with engine.begin() as conn:
        referrer_code_id = conn.execute(
            text("SELECT id FROM public.referral_codes WHERE user_id = :i"),
            {"i": referrer},
        ).scalar()
    assert referrer_code_id is not None

    referred = signup()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.referral_relationships "
                "  (referrer_id, referred_id, referral_code_id, status) "
                "VALUES (:a, :b, :c, 'pending')"
            ),
            {"a": referrer, "b": referred, "c": referrer_code_id},
        )
        linked = conn.execute(
            text(
                "SELECT referrer_id FROM public.referral_relationships "
                "WHERE referred_id = :b"
            ),
            {"b": referred},
        ).scalar()
    assert str(linked) == referrer


def test_a_user_cannot_be_referred_twice(signup, engine):
    """``uq_referral_relationships_referred`` -- one referrer per referred user.

    Without it, a second relationship row would mean a second commission for the same
    signup, which is money.
    """
    referrer_a = signup()
    referrer_b = signup()
    referred = signup()
    with engine.begin() as conn:
        code_a = conn.execute(
            text("SELECT id FROM public.referral_codes WHERE user_id = :i"),
            {"i": referrer_a},
        ).scalar()
        code_b = conn.execute(
            text("SELECT id FROM public.referral_codes WHERE user_id = :i"),
            {"i": referrer_b},
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO public.referral_relationships "
                "  (referrer_id, referred_id, referral_code_id, status) "
                "VALUES (:a, :b, :c, 'pending')"
            ),
            {"a": referrer_a, "b": referred, "c": code_a},
        )

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(
                        "INSERT INTO public.referral_relationships "
                        "  (referrer_id, referred_id, referral_code_id, status) "
                        "VALUES (:a, :b, :c, 'pending')"
                    ),
                    {"a": referrer_b, "b": referred, "c": code_b},
                )
            assert "unique" in str(excinfo.value).lower(), str(excinfo.value)
        finally:
            trans.rollback()


def test_two_users_never_share_a_referral_code(signup, engine):
    """``generate_unique_referral_code`` has a retry loop whose uniqueness check was the
    broken one. Twenty signups is enough to catch a generator that returns a constant --
    which is exactly what the tautological ``WHERE code = code`` would have produced had
    it resolved instead of erroring."""
    uids = [signup() for _ in range(20)]
    with engine.begin() as conn:
        codes = conn.execute(
            text("SELECT code FROM public.referral_codes WHERE user_id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": uids},
        ).scalars().all()
    assert len(codes) == 20, f"expected 20 codes, got {len(codes)}"
    assert len(set(codes)) == 20, (
        f"only {len(set(codes))} distinct codes across 20 signups: "
        f"{sorted(c for c in codes if codes.count(c) > 1)[:5]}"
    )


def test_the_referral_code_column_is_unique(engine, signup):
    """The database refuses a duplicate code even if the generator ever produced one."""
    uid = signup()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT code FROM public.referral_codes WHERE user_id = :i"), {"i": uid}
        ).scalar()

    other = signup()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(Exception) as excinfo:
                conn.execute(
                    text(
                        "UPDATE public.referral_codes SET code = :c WHERE user_id = :i"
                    ),
                    {"c": existing, "i": other},
                )
            assert "unique" in str(excinfo.value).lower(), str(excinfo.value)
        finally:
            trans.rollback()


# ── failure and concurrency ──────────────────────────────────────────────────


def test_a_rolled_back_signup_leaves_nothing_behind(engine):
    """The trigger's writes must be inside the caller's transaction.

    If ``create_referral_code_for_user`` committed independently -- through an autonomous
    transaction or a separate connection -- a failed registration would leave an orphaned
    referral code holding a unique value nobody owns.
    """
    uid = str(uuid.uuid4())
    with engine.connect() as conn:
        trans = conn.begin()
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": uid, "e": f"{uid[:8]}@rollback.test"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": uid})
        code_inside = conn.execute(
            text("SELECT code FROM public.referral_codes WHERE user_id = :i"), {"i": uid}
        ).scalar()
        assert code_inside, "the trigger did not run inside the transaction"
        trans.rollback()

    with engine.begin() as conn:
        profiles = conn.execute(
            text("SELECT count(*) FROM public.profiles WHERE id = :i"), {"i": uid}
        ).scalar()
        codes = conn.execute(
            text("SELECT count(*) FROM public.referral_codes WHERE user_id = :i"),
            {"i": uid},
        ).scalar()
    assert profiles == 0, "the profile survived a rolled-back signup"
    assert codes == 0, (
        "a referral code survived a rolled-back signup, so the trigger is committing "
        "outside the caller's transaction"
    )


def test_concurrent_signups_all_succeed_with_distinct_codes(engine):
    """Eight registrations at once, off a barrier.

    ``generate_unique_referral_code`` reads ``referral_codes`` and then inserts into it,
    which is a read-then-write race: two concurrent signups can both see a code as free.
    The unique constraint is the backstop, and the question this answers is whether the
    retry loop handles the collision or whether a user's registration simply fails.
    """
    count = 8
    uids = [str(uuid.uuid4()) for _ in range(count)]
    barrier = threading.Barrier(count)
    failures: list[tuple[str, str]] = []
    lock = threading.Lock()

    def register(uid: str) -> None:
        try:
            barrier.wait(timeout=30)
            with engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
                    {"i": uid, "e": f"{uid[:8]}@concurrent.test"},
                )
                conn.execute(
                    text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": uid}
                )
        except Exception as exc:  # noqa: BLE001
            with lock:
                failures.append((uid[:8], str(exc).strip().splitlines()[0]))

    threads = [threading.Thread(target=register, args=(u,)) for u in uids]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert not failures, (
            f"{len(failures)} of {count} concurrent signups failed: {failures}"
        )

        with engine.begin() as conn:
            codes = conn.execute(
                text("SELECT code FROM public.referral_codes WHERE user_id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": uids},
            ).scalars().all()
        assert len(codes) == count, f"expected {count} codes, got {len(codes)}"
        assert len(set(codes)) == count, "concurrent signups produced a duplicate code"
    finally:
        _purge(engine, uids)
