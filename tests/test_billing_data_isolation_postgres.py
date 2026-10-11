"""Billing tables must not expose one customer's financial records to another.

WHAT THIS GUARDS, AND WHY IT IS NOT A POLICY-READING TEST. Before this pass,
`migrations/006_reconcile_production_database.sql` PART 11 created correct owner-access
policies on `invoices` and `subscriptions` and never enabled row level security on either,
so PostgreSQL never consulted them. `payment_methods` had no policy at all. With
Supabase's default `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated,
service_role` in place, the measured result was 22 distinct exposures, including:

    anon          can read ALL rows from invoices / payment_methods / subscriptions
    authenticated can read another user's invoice by primary key
    authenticated can UPDATE another user's invoice
    authenticated can DELETE another user's stored payment method
    authenticated can INSERT a row owned by another user

`anon` is the key that ships in the browser bundle, so that first line means every visitor
could read every customer's billing history and stored card metadata.

Reading `pg_policies` would not have found any of it: the policies were present and
correctly written. The flag that made them inert, and the privileges that made writes
possible, are separate facts. So this file asserts on the outcome of real statements
issued under the roles PostgREST resolves requests to, never on the presence of a policy.

THE ROLES. `core/dependencies.py:242-281` builds the request client with
`apiKey: SUPABASE_ANON_KEY` plus the end user's JWT, which PostgREST resolves to
`authenticated` with `auth.uid()` populated. Without a token the same anon key resolves to
`anon`. `service_role` is the backend's key and is BYPASSRLS by design -- asserted here
too, because the webhook and entitlement paths depend on it and a change that broke it
would be a silent billing outage.

Gated on AERORA_TEST_DATABASE_URL, the repository's existing opt-in. RLS is a PostgreSQL
mechanism and there is nothing to execute on SQLite.
"""

from __future__ import annotations

import os
import uuid

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not TEST_DATABASE_URL.startswith("postgresql"),
    reason=(
        "AERORA_TEST_DATABASE_URL is unset or not PostgreSQL. Row level security and "
        "table privileges are PostgreSQL mechanisms."
    ),
)

USER_A = "a0a0a0a0-1111-4111-8111-a0a0a0a0a0a0"
USER_B = "b0b0b0b0-2222-4222-8222-b0b0b0b0b0b0"

BILLING_TABLES = ("invoices", "payment_methods", "subscriptions")

#: Every column on these three is NOT NULL with no default, so a seed row cannot omit
#: any. The camelCase names (amtUSD, priceINR, autoRenew) are the real column names and
#: have to be quoted.
def _row(table: str, owner: str, tag: str) -> dict:
    if table == "invoices":
        return {
            "id": f"inv-{tag}", "user_id": owner, "date": "2026-10-06",
            "amtUSD": 49.0, "amtINR": 4100.0, "status": "paid",
            "created_at": "2026-10-06 00:00:00",
        }
    if table == "payment_methods":
        return {
            "id": f"pm-{tag}", "user_id": owner,
            "payment_method_id": f"pm_stripe_{tag}", "brand": "visa",
            "last4": "4242", "expiry_month": 12, "expiry_year": 2030,
            "is_default": True, "created_at": "2026-10-06",
        }
    return {
        "id": f"sub-{tag}", "user_id": owner, "plan_id": "pro", "name": "Pro",
        "priceUSD": 49.0, "priceINR": 4100.0, "autoRenew": True,
        "created_at": "2026-10-06 00:00:00", "updated_at": "2026-10-06 00:00:00",
    }


def _prefix(table: str) -> str:
    return {"invoices": "inv", "payment_methods": "pm", "subscriptions": "sub"}[table]


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def seeded(engine):
    """One billing row per table per user, written as the owner.

    Seeded as `postgres`, which owns these tables and is exempt from their policies while
    FORCE ROW LEVEL SECURITY is off. That exemption is what lets the fixture write a row
    on another user's behalf, and is itself asserted further down because the API depends
    on it.
    """
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as conn:
        for owner in (USER_A, USER_B):
            conn.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:i, :e) "
                     "ON CONFLICT (id) DO NOTHING"),
                {"i": owner, "e": f"{owner[:8]}-{tag}@example.test"},
            )
            conn.execute(
                text("INSERT INTO public.profiles (id) VALUES (:i) "
                     "ON CONFLICT (id) DO NOTHING"),
                {"i": owner},
            )
        for table in BILLING_TABLES:
            for owner, who in ((USER_A, "a"), (USER_B, "b")):
                row = _row(table, owner, f"{tag}-{who}")
                cols = ", ".join(f'"{c}"' for c in row)
                binds = ", ".join(f":{c}" for c in row)
                conn.execute(
                    text(f"INSERT INTO public.{table} ({cols}) VALUES ({binds})"), row
                )

    yield tag

    with engine.begin() as conn:
        for table in BILLING_TABLES:
            conn.execute(
                text(f"DELETE FROM public.{table} WHERE user_id IN (:a, :b)"),
                {"a": USER_A, "b": USER_B},
            )
        for owner in (USER_A, USER_B):
            conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": owner})
            conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": owner})


def _as(conn, role: str, claim: str | None = None):
    """Adopt the identity PostgREST would present. Transaction-scoped."""
    conn.execute(
        text("SELECT set_config('request.jwt.claim.sub', :v, true)"),
        {"v": claim or ""},
    )
    conn.execute(text(f"SET LOCAL ROLE {role}"))


def _refused(exc: Exception) -> str:
    low = str(exc).lower()
    if "permission denied" in low:
        return "privilege"
    if "row-level security" in low or "row level security" in low:
        return "policy"
    return "other"


# ── anon: the browser-held key must reach none of it ─────────────────────────


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_anon_cannot_read_any_billing_row(engine, seeded, table):
    """The highest-severity case. `anon` is public; it must see nothing here."""
    with engine.begin() as conn:
        _as(conn, "anon")
        try:
            rows = conn.execute(text(f"SELECT user_id::text FROM public.{table}")).fetchall()
        except Exception as exc:  # noqa: BLE001
            assert _refused(exc) in ("privilege", "policy"), str(exc)
            return
    assert rows == [], (
        f"{table}: the anon key -- which ships in the browser bundle -- read "
        f"{len(rows)} billing row(s)"
    )


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_anon_cannot_write_billing_rows(engine, seeded, table):
    """Reading someone's invoices is a disclosure; deleting them is destruction."""
    victim = f"{_prefix(table)}-{seeded}-b"
    with engine.begin() as conn:
        _as(conn, "anon")
        try:
            result = conn.execute(
                text(f"DELETE FROM public.{table} WHERE id = :i"), {"i": victim}
            )
        except Exception as exc:  # noqa: BLE001
            assert _refused(exc) in ("privilege", "policy"), str(exc)
            return
        assert result.rowcount == 0, f"{table}: anon deleted {result.rowcount} row(s)"


# ── authenticated: own rows yes, anyone else's no ────────────────────────────


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_authenticated_never_sees_another_users_billing_row(engine, seeded, table):
    """Both directions, and by primary key rather than by listing.

    A keyed lookup is the realistic attack: the attacker already has an id, from a URL, a
    webhook payload or a guess. A policy that filters a list but not a keyed read would
    pass a naive test and fail here.
    """
    for reader, victim_owner, victim_suffix in (
        (USER_A, USER_B, "b"),
        (USER_B, USER_A, "a"),
    ):
        victim_id = f"{_prefix(table)}-{seeded}-{victim_suffix}"
        with engine.begin() as conn:
            _as(conn, "authenticated", reader)
            try:
                rows = conn.execute(
                    text(f"SELECT user_id::text FROM public.{table} WHERE id = :i"),
                    {"i": victim_id},
                ).fetchall()
            except Exception as exc:  # noqa: BLE001
                assert _refused(exc) in ("privilege", "policy"), str(exc)
                continue
        assert rows == [], (
            f"{table}: {reader[:8]} read {victim_owner[:8]}'s row {victim_id} "
            f"-> {rows}"
        )


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_authenticated_cannot_modify_another_users_billing_row(engine, seeded, table):
    """UPDATE, DELETE and forged-ownership INSERT, all three.

    Each is checked for "refused OR affected zero rows", because privilege and policy
    produce different shapes and both are acceptable outcomes; what is not acceptable is a
    row changing hands.
    """
    victim_id = f"{_prefix(table)}-{seeded}-b"

    statements = [
        (f"UPDATE public.{table} SET user_id = :me WHERE id = :i",
         {"me": USER_A, "i": victim_id}),
        (f"DELETE FROM public.{table} WHERE id = :i", {"i": victim_id}),
    ]
    forged = _row(table, USER_B, f"{seeded}-forged")
    cols = ", ".join(f'"{c}"' for c in forged)
    binds = ", ".join(f":{c}" for c in forged)
    statements.append((f"INSERT INTO public.{table} ({cols}) VALUES ({binds})", forged))

    for sql, params in statements:
        with engine.begin() as conn:
            _as(conn, "authenticated", USER_A)
            try:
                result = conn.execute(text(sql), params)
            except Exception as exc:  # noqa: BLE001
                assert _refused(exc) in ("privilege", "policy"), (
                    f"{table}: {sql.split()[0]} failed for an unexpected reason: {exc}"
                )
                continue
            assert result.rowcount == 0, (
                f"{table}: {sql.split()[0]} by {USER_A[:8]} affected "
                f"{result.rowcount} row(s) of another user's data"
            )

    # The victim's row is intact, verified as the owner so a successful write cannot be
    # concealed from the verification query by the same control that should have stopped it.
    with engine.begin() as conn:
        # `::text` because `user_id` is `uuid` since alembic `d4e5f6a7b8c9` (BUG-049),
        # and psycopg2 renders a uuid column as a `uuid.UUID` object. Comparing that to
        # the string in USER_B fails on type, not on ownership -- the assertion's meaning
        # is unchanged and it is deliberately not relaxed, only made to compare like with
        # like. The cast is in SQL rather than `str()` in Python so the comparison is
        # explicit at the point the value is read.
        owner = conn.execute(
            text(f"SELECT user_id::text FROM public.{table} WHERE id = :i"),
            {"i": victim_id},
        ).scalar()
    assert owner == USER_B, f"{table}: {victim_id} no longer belongs to user B"


def test_a_user_can_still_read_their_own_invoices(engine, seeded):
    """The control has to let the product work, or it is just an outage.

    `invoices` and `subscriptions` keep SELECT for `authenticated`, filtered to the
    owner's rows by 006's policy. If this ever fails, the hardening has gone too far.
    """
    with engine.begin() as conn:
        _as(conn, "authenticated", USER_A)
        owners = conn.execute(text("SELECT user_id::text FROM public.invoices")).scalars().all()
    assert owners, "user A cannot read their own invoices; the policy is too restrictive"
    assert set(owners) == {USER_A}, f"user A sees other owners: {sorted(set(owners))}"


# ── service_role and the owner: the paths the product depends on ─────────────


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_service_role_retains_full_access(engine, seeded, table):
    """The webhook and entitlement paths run under the service-role key.

    `service_role` is BYPASSRLS, so this is not a policy result -- it is a statement that
    the backend's own credential was not caught by the revokes above. Narrowing it would
    be a silent billing outage rather than a security improvement.
    """
    with engine.begin() as conn:
        _as(conn, "service_role")
        owners = set(
            conn.execute(text(f"SELECT user_id::text FROM public.{table}")).scalars().all()
        )
    assert {USER_A, USER_B} <= owners, (
        f"{table}: service_role sees {sorted(owners)}; the backend can no longer read "
        f"billing state"
    )


def test_the_api_path_for_payment_methods_still_works(engine, seeded):
    """`payment_methods` is served over SQLAlchemy as the table owner.

    `routers/billing.py` reads it at lines 1598 and 2576 through `get_db`, filtering by
    `user_id` in Python, and every DSN in the repository connects as `postgres`, which
    owns the table. Because 006 uses ENABLE and not FORCE ROW LEVEL SECURITY, the owner
    stays exempt and those endpoints are unaffected by the revokes -- which is the whole
    reason FORCE was not used. This asserts that explicitly, so a later change to FORCE
    fails here instead of in production.
    """
    with engine.begin() as conn:
        role = conn.execute(text("SELECT current_user")).scalar()
        rows = conn.execute(
            text("SELECT user_id::text FROM public.payment_methods WHERE user_id = :u"),
            {"u": USER_A},
        ).fetchall()
    assert rows, (
        f"connected as {role!r} and could not read user A's payment methods. If FORCE "
        f"ROW LEVEL SECURITY was enabled on payment_methods, GET /billing/payment-methods "
        f"now returns nothing."
    )


# ── the privilege posture itself ─────────────────────────────────────────────


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_anon_holds_no_privilege_on_billing_tables(engine, seeded, table):
    """Belt to the braces above.

    The behavioural tests would also pass if RLS alone were filtering anon to zero rows.
    Asserting the privilege is absent as well means a future `GRANT` that re-opens the
    table is caught even if a policy happens to mask it, and it is the difference between
    "anon sees nothing" and "anon cannot ask".
    """
    with engine.begin() as conn:
        privs = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema='public' AND table_name=:t AND grantee='anon'"
            ),
            {"t": table},
        ).scalars().all()
    assert privs == [], f"{table}: anon still holds {sorted(privs)}"


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_row_level_security_is_enabled_on_billing_tables(engine, seeded, table):
    """The flag 006 PART 11 forgot.

    Asserted directly because it is the single condition that made correct policies inert,
    and because a table re-created by a future migration would come back with it off.
    """
    with engine.begin() as conn:
        enabled = conn.execute(
            text(
                "SELECT c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=:t"
            ),
            {"t": table},
        ).scalar()
    assert enabled is True, (
        f"{table}: relrowsecurity is {enabled}; any policy on this table is inert"
    )


# ── privileges row level security cannot filter ──────────────────────────────
#
# These are not billing-specific, but they belong with the privilege assertions above
# rather than in a file about row policies, because they are the cases where a policy is
# irrelevant by construction.


@pytest.mark.parametrize("privilege", ["TRUNCATE", "REFERENCES", "TRIGGER"])
def test_no_browser_role_holds_a_privilege_rls_cannot_filter(engine, privilege):
    """TRUNCATE, REFERENCES and TRIGGER are table-level, so no policy applies.

    Supabase's model -- grant the browser roles DML and let RLS filter rows -- is sound
    for SELECT/INSERT/UPDATE/DELETE and does not extend to these three. PostgreSQL never
    consults a row policy for any of them.

    Measured before this was fixed, with RLS already enabled and correct owner policies
    in place on every table involved:

        profiles           anon  TRUNCATE CASCADE  -> SUCCEEDED
        orders             anon  TRUNCATE CASCADE  -> SUCCEEDED
        execution_records  anon  TRUNCATE          -> SUCCEEDED
        referral_wallets   anon  TRUNCATE          -> SUCCEEDED

    `profiles` is the identity table every other table references and CASCADE takes the
    referencing tables with it, so the first line is the whole database in one statement.

    Reachability, stated accurately: `anon` and `authenticated` are NOLOGIN, PostgREST has
    no TRUNCATE verb, and no function in `public` performs one, so there is no path to it
    through the documented client API today. This is a blast-radius control rather than a
    patched exploit -- it is what keeps a future SQL injection or a careless
    SECURITY INVOKER function from escalating into total data destruction.
    """
    with engine.begin() as conn:
        held = conn.execute(
            text(
                "SELECT table_name, grantee FROM information_schema.role_table_grants "
                "WHERE table_schema='public' AND grantee IN ('anon','authenticated') "
                "  AND privilege_type = :p ORDER BY table_name, grantee"
            ),
            {"p": privilege},
        ).fetchall()
    assert held == [], (
        f"{len(held)} table/role pair(s) still hold {privilege}, which no policy can "
        f"filter: {[f'{t} ({g})' for t, g in held[:10]]}"
        + (" ..." if len(held) > 10 else "")
    )


def test_anon_cannot_truncate_the_identity_table_even_with_cascade(engine):
    """The specific statement that emptied the database, executed and rolled back.

    The privilege assertion above is the general control; this is the proof that the
    general control actually stops the concrete attack. Kept separate because a future
    refactor could satisfy one and not the other -- for example by granting TRUNCATE
    through a role membership that `role_table_grants` does not attribute to `anon`
    directly.

    `profiles` was chosen because CASCADE from it reaches everything.
    """
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text("SET LOCAL ROLE anon"))
            with pytest.raises(Exception) as excinfo:
                conn.execute(text("TRUNCATE TABLE public.profiles CASCADE"))
            message = str(excinfo.value).lower()
            assert "permission denied" in message or "must be owner" in message, (
                f"TRUNCATE profiles CASCADE as anon was refused, but not by privilege: "
                f"{excinfo.value}"
            )
        finally:
            # Rolled back whatever happened, so a regression that lets the TRUNCATE
            # through does not also destroy the verification database.
            trans.rollback()
