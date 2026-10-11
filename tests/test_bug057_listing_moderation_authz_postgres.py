"""BUG-057 regression: a creator cannot move their own listing's moderation lifecycle.

THE DEFECT
----------
`library_strategies` carried a blanket `GRANT INSERT, UPDATE` to `authenticated`, an
UPDATE policy of `USING (author_id = auth.uid())` with **no `WITH CHECK`**, and -- unlike
`library_subscriptions`, which has `trg_subscription_transition_guard` -- **no
transition-guard trigger of any kind**. A creator could therefore issue a direct
PostgREST UPDATE against their own row and set::

    moderation_status = 'approved'
    is_active         = true
    is_featured       = true

bypassing `admin_moderate_strategy` (`routers/library.py:2670`, `Depends(get_admin_user)`)
and the Submission state machine, which that handler's own comment calls "the only path
that moves the effective lifecycle". Reproduced before the fix in
`staging/self_approve_recheck.log`.

`anon` additionally held INSERT/UPDATE/DELETE on the same table (inert, because every
policy is `TO authenticated`, but one `CREATE POLICY ... TO anon` away from being live).

THE FIX
-------
`backend_app/migrations/023_remediate_privilege_escalation.sql`:

* the blanket table-wide write grant is removed from `authenticated` and the
  creator-editable columns are granted back individually, so the 13 moderation and
  derived columns are unwritable by a browser role;
* the UPDATE policy gains `WITH CHECK (author_id = auth.uid())`, so an author cannot
  re-assign their row to a different author;
* `anon` loses INSERT/UPDATE/DELETE.

WHY `WITH CHECK` ALONE WOULD NOT HAVE BEEN ENOUGH, which is what these tests pin
----------------------------------------------------------------------------------
The creator legitimately **is** the author, so `WITH CHECK (author_id = auth.uid())`
passes for a self-approval and the write proceeds. Row-level security cannot express
"the new row's moderation_status must equal the old row's". Column privileges are the
mechanism PostgreSQL provides for restricting *which columns* a role may write, so that
is the control actually doing the work here -- and
:func:`test_creator_cannot_self_approve_via_direct_update` fails if it is removed, even
if the policy is left intact.

WHAT THESE TESTS DELIBERATELY ALSO ASSERT
-----------------------------------------
A fix that locked the whole table would satisfy every denial test and break the product.
:func:`test_creator_can_still_edit_their_own_listing_content` and
:func:`test_service_role_can_still_moderate` are therefore not optional extras -- they
are the half that proves the control is *scoped* rather than merely *restrictive*.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: this defect and its fix live in column "
        "privileges, RLS policies and role switching, none of which SQLite has. Start "
        "one with staging/bootstrap_pg.py."
    ),
)

sa = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

#: Columns the platform owns. A creator must not be able to write any of them.
#: Duplicated from migration 023 deliberately: if someone widens the grant there, this
#: list keeps asserting the reviewed contract and the disagreement is the signal.
PROTECTED_COLUMNS = (
    "moderation_status",
    "is_active",
    "is_featured",
    "moderated_by",
    "moderated_at",
    "moderation_notes",
    "published_at",
    "verification_status",
    "evaluation_score",
    "avg_rating",
    "rating_count",
    "clone_count",
    "subscriber_count",
)

#: Columns the creator owns. Pricing is included on purpose -- it is a creator decision
#: in this product, and a fix that removed it would be too broad.
CREATOR_EDITABLE_COLUMNS = (
    "name",
    "description",
    "category",
    "tags",
    "price_minor",
    "currency",
    "cover_image",
    "source_cloning_enabled",
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_listing(conn, *, moderation_status="pending", is_active=False):
    """A creator plus one of their listings in a pre-moderation state. Returns ids."""
    author = str(uuid.uuid4())
    conn.execute(
        text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
        {"id": author, "email": f"bug057-{author[:8]}@example.invalid"},
    )
    conn.execute(text("INSERT INTO public.profiles (id) VALUES (:id)"), {"id": author})
    listing_id = conn.execute(
        text(
            "INSERT INTO public.library_strategies "
            "(author_id, source_strategy_id, name, description, category, symbol, "
            " timeframe, exchange_id, moderation_status, is_active, is_featured, "
            " price_minor, currency) "
            "VALUES (:author, :src, 'bug057-listing', 'desc', 'trend', 'BTC/USDT', "
            "        '1h', 'binance', :mod, :active, false, 2999, 'USD') "
            "RETURNING id"
        ),
        {
            "author": author,
            "src": str(uuid.uuid4()),
            "mod": moderation_status,
            "active": is_active,
        },
    ).scalar()
    return author, listing_id


def _as_creator(conn, author_id):
    """Adopt the creator's identity exactly as PostgREST would for a direct request."""
    conn.execute(
        text("SELECT set_config('request.jwt.claim.sub', :sub, true)"),
        {"sub": str(author_id)},
    )
    conn.execute(text("SET LOCAL ROLE authenticated"))


# ── 1. the privilege contract, read off the live catalogue ───────────────────


@pytest.mark.parametrize("column", PROTECTED_COLUMNS)
def test_browser_roles_cannot_write_protected_columns(engine, column):
    """No browser role holds INSERT or UPDATE on a platform-owned column."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT grantee, privilege_type FROM information_schema.column_privileges "
                " WHERE table_schema='public' AND table_name='library_strategies' "
                "   AND column_name=:c AND grantee IN ('anon','authenticated') "
                "   AND privilege_type IN ('INSERT','UPDATE')"
            ),
            {"c": column},
        ).fetchall()
    assert rows == [], (
        f"library_strategies.{column} is platform-owned, but a browser role still holds "
        f"{rows}. A creator could write it directly and bypass admin_moderate_strategy. "
        f"This is BUG-057."
    )


@pytest.mark.parametrize("column", CREATOR_EDITABLE_COLUMNS)
def test_creator_retains_write_on_their_own_content_columns(engine, column):
    """The fix must be scoped, not a blanket lock. A creator still owns their content."""
    with engine.begin() as conn:
        can_write = conn.execute(
            text(
                "SELECT has_column_privilege('authenticated', "
                "       'public.library_strategies', :c, 'UPDATE')"
            ),
            {"c": column},
        ).scalar()
    assert can_write is True, (
        f"authenticated lost UPDATE on library_strategies.{column}. That column is "
        f"creator-editable in this product; the remediation was too broad."
    )


def test_update_policy_has_a_with_check(engine):
    """Without WITH CHECK an author could re-assign their row to another author."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT pg_get_expr(p.polwithcheck, p.polrelid) "
                "  FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                " WHERE c.relname='library_strategies' AND p.polcmd='w'"
            )
        ).first()
    assert row is not None, "the library_strategies UPDATE policy is missing entirely"
    assert row[0] is not None and "auth.uid()" in row[0], (
        f"the library_strategies UPDATE policy has WITH CHECK={row[0]!r}; it must "
        f"constrain author_id to auth.uid()."
    )


def test_anon_holds_no_write_privilege_on_listings(engine):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                " WHERE table_schema='public' AND table_name='library_strategies' "
                "   AND grantee='anon' AND privilege_type IN ('INSERT','UPDATE','DELETE')"
            )
        ).fetchall()
    assert rows == [], f"anon still holds {rows} on library_strategies"


# ── 2. behaviour: the attack is refused ──────────────────────────────────────


def test_creator_cannot_self_approve_via_direct_update(engine):
    """THE defect. A creator attempts the exact UPDATE reproduced pre-fix.

    Asserted two ways on purpose, because they fail differently: the statement should
    raise (insufficient privilege), and -- whatever it raises -- the row must still be
    unapproved when read back as a privileged reader.
    """
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            author, listing_id = _seed_listing(outer)
            _as_creator(outer, author)

            with pytest.raises(sa.exc.ProgrammingError) as exc:
                outer.execute(
                    text(
                        "UPDATE public.library_strategies "
                        "   SET moderation_status='approved', is_active=true, "
                        "       is_featured=true "
                        " WHERE id = :id"
                    ),
                    {"id": listing_id},
                )
            assert "permission denied" in str(exc.value).lower(), (
                f"expected a privilege refusal, got: {exc.value}"
            )
        finally:
            sp.rollback()

    # Re-seed and verify the state is unchanged after a refused attempt. A separate
    # transaction, because the aborted one above cannot be read from.
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            author, listing_id = _seed_listing(outer)
            inner = outer.begin_nested()
            try:
                _as_creator(outer, author)
                outer.execute(
                    text(
                        "UPDATE public.library_strategies SET moderation_status='approved' "
                        " WHERE id = :id"
                    ),
                    {"id": listing_id},
                )
            except Exception:
                inner.rollback()
            else:
                inner.commit()
            outer.execute(text("RESET ROLE"))

            state = outer.execute(
                text(
                    "SELECT moderation_status, is_active, is_featured "
                    "  FROM public.library_strategies WHERE id = :id"
                ),
                {"id": listing_id},
            ).first()
            assert state == ("pending", False, False), (
                f"after a refused self-approval the listing reads {state}; it must still "
                f"be ('pending', False, False)."
            )
        finally:
            sp.rollback()


@pytest.mark.parametrize(
    "column,value",
    [
        ("moderation_status", "'approved'"),
        ("is_active", "true"),
        ("is_featured", "true"),
        ("avg_rating", "5.0"),
        ("subscriber_count", "10000"),
        ("clone_count", "9999"),
        ("verification_status", "'verified'"),
        ("published_at", "now()"),
        ("moderated_by", "gen_random_uuid()"),
    ],
)
def test_creator_cannot_write_any_single_protected_column(engine, column, value):
    """Each protected column refused individually.

    The social-proof columns (`avg_rating`, `subscriber_count`, `clone_count`) are in
    this list because a creator who can write `avg_rating = 5` is committing the same
    category of fraud as self-approval, and they were writable for the same reason.
    """
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            author, listing_id = _seed_listing(outer)
            _as_creator(outer, author)
            with pytest.raises(sa.exc.ProgrammingError) as exc:
                outer.execute(
                    text(
                        f"UPDATE public.library_strategies SET {column} = {value} "
                        f" WHERE id = :id"
                    ),
                    {"id": listing_id},
                )
            assert "permission denied" in str(exc.value).lower()
        finally:
            sp.rollback()


def test_creator_can_still_edit_their_own_listing_content(engine):
    """The product must still work. A creator edits name, description and price."""
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            author, listing_id = _seed_listing(outer)
            _as_creator(outer, author)
            result = outer.execute(
                text(
                    "UPDATE public.library_strategies "
                    "   SET name='renamed-by-creator', description='new desc', "
                    "       price_minor = 4999 "
                    " WHERE id = :id"
                ),
                {"id": listing_id},
            )
            assert result.rowcount == 1, (
                "a creator could not edit their own listing content — the remediation "
                "was too broad."
            )
            outer.execute(text("RESET ROLE"))
            row = outer.execute(
                text(
                    "SELECT name, description, price_minor "
                    "  FROM public.library_strategies WHERE id=:id"
                ),
                {"id": listing_id},
            ).first()
            assert row == ("renamed-by-creator", "new desc", 4999)
        finally:
            sp.rollback()


def test_creator_cannot_reassign_their_listing_to_another_author(engine):
    """What the new WITH CHECK buys, independent of the column grants."""
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            author, listing_id = _seed_listing(outer)
            victim = str(uuid.uuid4())
            outer.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:id, :e)"),
                {"id": victim, "e": f"bug057-victim-{victim[:8]}@example.invalid"},
            )
            outer.execute(text("INSERT INTO public.profiles (id) VALUES (:id)"), {"id": victim})

            _as_creator(outer, author)
            with pytest.raises(sa.exc.DBAPIError) as exc:
                outer.execute(
                    text(
                        "UPDATE public.library_strategies SET author_id = :victim "
                        " WHERE id = :id"
                    ),
                    {"victim": victim, "id": listing_id},
                )
            msg = str(exc.value).lower()
            assert "row-level security" in msg or "violates" in msg, (
                f"expected the WITH CHECK to refuse the re-assignment, got: {exc.value}"
            )
        finally:
            sp.rollback()


def test_another_creator_cannot_touch_someone_elses_listing(engine):
    """Cross-creator isolation is unchanged by the remediation."""
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            _author_a, listing_a = _seed_listing(outer)
            author_b, _listing_b = _seed_listing(outer)

            _as_creator(outer, author_b)
            result = outer.execute(
                text(
                    "UPDATE public.library_strategies SET name='hijacked' WHERE id=:id"
                ),
                {"id": listing_a},
            )
            assert result.rowcount == 0, (
                "creator B updated creator A's listing — tenant isolation is broken."
            )
        finally:
            sp.rollback()


# ── 3. behaviour: the legitimate moderation path still works ─────────────────


def test_service_role_can_still_moderate(engine):
    """`admin_moderate_strategy` writes through a SERVICE-ROLE client.

    If this fails the remediation has broken moderation itself, which would be a worse
    defect than the one it fixed.
    """
    with engine.begin() as outer:
        sp = outer.begin_nested()
        try:
            _author, listing_id = _seed_listing(outer)
            outer.execute(text("SET LOCAL ROLE service_role"))
            result = outer.execute(
                text(
                    "UPDATE public.library_strategies "
                    "   SET moderation_status='approved', is_active=true, "
                    "       is_featured=true, moderated_at=now() "
                    " WHERE id = :id"
                ),
                {"id": listing_id},
            )
            assert result.rowcount == 1, (
                "service_role could not moderate a listing — migration 023 over-revoked "
                "and has broken the admin moderation path."
            )
            outer.execute(text("RESET ROLE"))
            state = outer.execute(
                text(
                    "SELECT moderation_status, is_active, is_featured "
                    "  FROM public.library_strategies WHERE id=:id"
                ),
                {"id": listing_id},
            ).first()
            assert state == ("approved", True, True)
        finally:
            sp.rollback()


def test_service_role_retains_execute_on_increment_ml_addon(engine):
    """RLS-01's revoke must not have cut off the legitimate billing webhook caller."""
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
                "    ON n.oid = p.pronamespace "
                " WHERE n.nspname='public' AND p.proname='increment_ml_addon'"
            )
        ).scalar()
        if not exists:
            pytest.skip("increment_ml_addon is not installed on this database")
        assert conn.execute(
            text(
                "SELECT has_function_privilege('service_role', "
                "       'public.increment_ml_addon(uuid)', 'EXECUTE')"
            )
        ).scalar() is True, (
            "service_role lost EXECUTE on increment_ml_addon; routers/billing.py:1053 "
            "would start failing."
        )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_browser_roles_cannot_execute_increment_ml_addon(engine, role):
    """RLS-01. A SECURITY DEFINER function taking a caller-supplied user id.

    Pre-fix ACL was ``{=X/postgres, anon=X/postgres, authenticated=X/postgres, ...}`` --
    ``=X`` being PUBLIC. Any valid browser JWT could have granted a paid add-on to any
    account via ``POST /rest/v1/rpc/increment_ml_addon``.
    """
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
                "    ON n.oid = p.pronamespace "
                " WHERE n.nspname='public' AND p.proname='increment_ml_addon'"
            )
        ).scalar()
        if not exists:
            pytest.skip("increment_ml_addon is not installed on this database")
        can = conn.execute(
            text(
                "SELECT has_function_privilege(:role, "
                "       'public.increment_ml_addon(uuid)', 'EXECUTE')"
            ),
            {"role": role},
        ).scalar()
    assert can is False, (
        f"{role} can still EXECUTE increment_ml_addon, a SECURITY DEFINER function that "
        f"credits a paid add-on to an arbitrary caller-supplied user id. This is RLS-01."
    )
