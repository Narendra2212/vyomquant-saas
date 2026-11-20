"""Cross-tenant isolation, established by executing queries against real PostgreSQL.

WHY THIS FILE EXISTS. Reading `pg_policies` tells you a policy is defined. It does not
tell you whether the policy binds for the role the application connects as, whether the
predicate matches the rows it was meant to match, or whether a write that should be
refused is actually refused. Those are properties of PostgreSQL's evaluation, and the
only way to learn them is to attempt the access and look at what comes back. So every
assertion here follows a real INSERT, SELECT, UPDATE or DELETE issued under an adopted
identity.

THE THREE ACCESS PATHS, WHICH BEHAVE DIFFERENTLY. The application reaches the database
three ways, and a claim about "tenant isolation" is meaningless without saying which:

  1. PostgREST with the anon key plus the end user's JWT (`get_request_supabase` in
     core/dependencies.py). Resolves to role `authenticated` with `auth.uid()`
     populated. Most routers use this. RLS is the enforcement mechanism here, and this
     is the path the `auth.uid()` policy family protects.
  2. PostgREST with the service-role key. `service_role` carries BYPASSRLS, so RLS is
     not a control at all; application-level ownership checks are. `routers/library.py`
     is wholly on this path.
  3. SQLAlchemy over DATABASE_URL. Every DSN in the repository carries username
     `postgres`, which owns every table. A table owner is exempt from its own policies
     unless FORCE ROW LEVEL SECURITY is set, and nothing in the repository sets FORCE.
     So RLS is also not a control on this path.

All three are asserted below, including the two where isolation does NOT come from the
database, because a launch report that records only the reassuring path would be wrong.

SIX TENANTS, NOT TWO. Two tenants cannot distinguish "A is isolated from B" from "A sees
only the first row" or "the policy happens to match one id". Six, checked pairwise, make
the shape of any leak visible.

GATED on AERORA_TEST_DATABASE_URL, the repository's existing opt-in (tests/conftest.py
lines 37-41). Unset, this skips; the SQLite lane cannot run it because RLS is a
PostgreSQL mechanism.
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
        "AERORA_TEST_DATABASE_URL is unset or not PostgreSQL. Row level security is a "
        "PostgreSQL mechanism; there is nothing to execute without a server."
    ),
)

#: Six tenants. Fixed rather than random so a failure names the same ids every run.
TENANTS = [
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
]

#: Tables reached on path 1 (PostgREST as `authenticated`), with the column that owns the
#: row and a factory for the rest of the required NOT NULL columns. Chosen because each
#: carries a real `auth.uid()` policy in the provisioned schema and `authenticated` holds
#: table privileges on it.
AUTH_UID_TABLES = {
    "strategies": lambda t, tag: {
        "user_id": t, "name": f"strat-{tag}", "symbol": "BTC/USDT",
    },
    "notifications": lambda t, tag: {
        "user_id": t, "title": f"title-{tag}", "message": f"msg-{tag}",
    },
    "paper_accounts": lambda t, tag: {
        "user_id": t, "currency": "USDT", "initial_capital": 10000,
        "available_balance": 10000, "locked_balance": 0, "total_equity": 10000,
    },
}

#: Tables carrying the `app.current_tenant_id` policy family from
#: alembic/versions/implement_rls_policies_20260817.py.
#:
#: `orders` still has the 5-column blob shape `(id, tenant_id, data, created_at,
#: updated_at)`. `positions` DOES NOT any more: alembic `b2c3d4e5f6a7` reshaped it onto
#: `core/position_model.PositionModel`, so it needs the full canonical column set. Both
#: are kept here because both carry the GUC policy and that is what these tests measure;
#: the difference is only in how a row is seeded.
GUC_TABLES = ("orders", "positions")

#: A minimal valid row per GUC table, since the two shapes no longer agree.
def _guc_row(table: str, row_id: str, tenant: str) -> dict:
    if table == "positions":
        return {
            "position_id": row_id, "tenant_id": tenant,
            "strategy_id": "rls-probe", "symbol": "BTC/USDT",
            "side": "LONG", "size": "1", "avg_entry_price": "1",
            "unrealized_pnl": "0.0", "realized_pnl": "0.0", "status": "OPEN",
        }
    return {"id": row_id, "tenant_id": tenant}


#: The primary key column, which also differs now.
_GUC_PK = {"orders": "id", "positions": "position_id"}

TENANT_GUC = "app.current_tenant_id"
CLAIM_GUC = "request.jwt.claim.sub"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def seeded(engine):
    """Six tenants, each owning one row in every table under test.

    Seeded as the owner, which is the only role that can write rows for someone else --
    that asymmetry is itself one of the things under test further down.

    Rows are tagged with a per-run marker so a failed run cannot poison the next one, and
    everything tagged is removed at teardown.
    """
    run = uuid.uuid4().hex[:8]
    ids: dict[tuple[str, str], str] = {}

    with engine.begin() as conn:
        for tenant in TENANTS:
            conn.execute(
                text(
                    "INSERT INTO auth.users (id, email) VALUES (:id, :email) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant, "email": f"{run}-{tenant[:8]}@example.test"},
            )
            conn.execute(
                text(
                    "INSERT INTO public.profiles (id) VALUES (:id) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant},
            )

        for table, factory in AUTH_UID_TABLES.items():
            for tenant in TENANTS:
                payload = factory(tenant, f"{run}-{tenant[:8]}")
                cols = ", ".join(payload)
                binds = ", ".join(f":{c}" for c in payload)
                row_id = conn.execute(
                    text(f"INSERT INTO public.{table} ({cols}) VALUES ({binds}) RETURNING id"),
                    payload,
                ).scalar()
                ids[(table, tenant)] = str(row_id)

        for table in GUC_TABLES:
            for tenant in TENANTS:
                row_id = f"{run}-{tenant[:8]}"
                payload = _guc_row(table, row_id, tenant)
                cols = ", ".join(payload)
                binds = ", ".join(f":{c}" for c in payload)
                conn.execute(
                    text(f"INSERT INTO public.{table} ({cols}) VALUES ({binds})"),
                    payload,
                )
                ids[(table, tenant)] = row_id

    yield {"run": run, "ids": ids}

    with engine.begin() as conn:
        for table in list(AUTH_UID_TABLES) + list(GUC_TABLES):
            pk = _GUC_PK.get(table, "id")
            for tenant in TENANTS:
                key = (table, tenant)
                if key in ids:
                    conn.execute(
                        text(f"DELETE FROM public.{table} WHERE {pk} = :id"),
                        {"id": ids[key]},
                    )
        for tenant in TENANTS:
            conn.execute(text("DELETE FROM public.profiles WHERE id = :id"), {"id": tenant})
            conn.execute(text("DELETE FROM auth.users WHERE id = :id"), {"id": tenant})


def _as_authenticated(conn, tenant: str | None):
    """Adopt the identity PostgREST would present for `tenant`.

    `set_config(..., true)` and `SET LOCAL ROLE` are both transaction-scoped, so the
    caller's `begin()` block is the whole lifetime of the identity and nothing leaks into
    the next test through the connection pool. The claim is bound as a parameter rather
    than interpolated -- `SET` takes no bind parameters, which is exactly why
    `core/tenant_db_context.py` uses `set_config` too.
    """
    if tenant is not None:
        conn.execute(
            text("SELECT set_config(:name, :val, true)"),
            {"name": CLAIM_GUC, "val": tenant},
        )
    conn.execute(text("SET LOCAL ROLE authenticated"))


def _set_tenant_guc(conn, tenant: str | None):
    conn.execute(
        text("SELECT set_config(:name, :val, true)"),
        {"name": TENANT_GUC, "val": tenant if tenant is not None else ""},
    )


# ── path 1: PostgREST as `authenticated`, the auth.uid() policy family ────────


@pytest.mark.parametrize("table", sorted(AUTH_UID_TABLES))
def test_authenticated_sees_only_its_own_row(engine, seeded, table):
    """The baseline claim: one identity, one row, out of six that exist."""
    for tenant in TENANTS:
        with engine.begin() as conn:
            _as_authenticated(conn, tenant)
            rows = conn.execute(
                text(f"SELECT user_id::text FROM public.{table}")
            ).scalars().all()
        assert rows, (
            f"{table}: tenant {tenant} sees NO rows, including its own. That is a "
            f"missing GRANT or an over-broad policy, not isolation."
        )
        assert set(rows) == {tenant}, (
            f"{table}: tenant {tenant} can see rows belonging to "
            f"{sorted(set(rows) - {tenant})}"
        )


@pytest.mark.parametrize("table", sorted(AUTH_UID_TABLES))
def test_authenticated_cannot_read_another_tenants_row_by_primary_key(engine, seeded, table):
    """Asking for a specific foreign row by id, which is the realistic attack.

    A policy that filters a list but not a keyed lookup would pass the test above and
    fail this one.
    """
    ids = seeded["ids"]
    for victim in TENANTS:
        for attacker in TENANTS:
            if attacker == victim:
                continue
            with engine.begin() as conn:
                _as_authenticated(conn, attacker)
                found = conn.execute(
                    text(f"SELECT count(*) FROM public.{table} WHERE id = :id"),
                    {"id": ids[(table, victim)]},
                ).scalar()
            assert found == 0, (
                f"{table}: tenant {attacker} read tenant {victim}'s row "
                f"{ids[(table, victim)]} by primary key"
            )


@pytest.mark.parametrize("table", sorted(AUTH_UID_TABLES))
def test_authenticated_cannot_update_another_tenants_row(engine, seeded, table):
    """Two acceptable outcomes, mirroring test_authenticated_cannot_delete_another_tenants_row.

    A cross-tenant UPDATE can be stopped either by the row level policy -- the statement
    runs and affects zero rows -- or by table privilege, where PostgreSQL refuses it with
    `permission denied` before any policy is consulted. Both leave the row intact.

    `paper_accounts` takes the second path: `043_paper_trading_service_role_only_write.sql`
    revoked `authenticated`'s own prior UPDATE grant on the paper tables (009's original
    grant, which is why the DELETE test's own docstring could cite it as still current for
    UPDATE at the time) once a session's own lifecycle no longer needed a direct client
    write. The privilege denial is the designed, stronger control there, not a gap.
    """
    ids = seeded["ids"]
    victim, attacker = TENANTS[0], TENANTS[1]

    with engine.begin() as conn:
        _as_authenticated(conn, attacker)
        try:
            result = conn.execute(
                text(f"UPDATE public.{table} SET user_id = :me WHERE id = :id"),
                {"me": attacker, "id": ids[(table, victim)]},
            )
        except Exception as exc:  # noqa: BLE001 - the refusal class is the finding
            message = str(exc).lower()
            assert "permission denied" in message or "policy" in message, (
                f"{table}: cross-tenant UPDATE failed, but not by privilege or policy: "
                f"{exc}"
            )
        else:
            assert result.rowcount == 0, (
                f"{table}: tenant {attacker} updated tenant {victim}'s row "
                f"({result.rowcount} row(s) affected)"
            )

    # And the victim's row is unchanged, checked as the owner so RLS cannot hide a
    # successful write from the verification query.
    with engine.begin() as conn:
        still = conn.execute(
            text(f"SELECT user_id::text FROM public.{table} WHERE id = :id"),
            {"id": ids[(table, victim)]},
        ).scalar()
    assert still == victim, f"{table}: ownership of {ids[(table, victim)]} changed"


@pytest.mark.parametrize("table", sorted(AUTH_UID_TABLES))
def test_authenticated_cannot_delete_another_tenants_row(engine, seeded, table):
    """Two acceptable outcomes, and the difference is worth recording.

    A cross-tenant DELETE can be stopped either by the row level policy -- the statement
    runs and affects zero rows -- or by table privilege, where PostgreSQL refuses it with
    `permission denied` before any policy is consulted. Both leave the row intact, so
    both are isolation, but they are different controls and a report should not call one
    the other.

    `paper_accounts` takes the second path on purpose. `009_paper_trading.sql` grants
    `authenticated` only SELECT, INSERT and UPDATE on the paper tables, and says why: a
    session is removed by the account cascade, never by a client. So the absence of the
    privilege is the designed control there and is stronger than a policy, since it
    forecloses the operation entirely rather than filtering its rows.

    What this test actually requires, in both cases, is that the victim's row survives.
    """
    ids = seeded["ids"]
    victim, attacker = TENANTS[2], TENANTS[3]
    mechanism: str

    with engine.begin() as conn:
        _as_authenticated(conn, attacker)
        try:
            result = conn.execute(
                text(f"DELETE FROM public.{table} WHERE id = :id"),
                {"id": ids[(table, victim)]},
            )
        except Exception as exc:  # noqa: BLE001 - the refusal class is the finding
            message = str(exc).lower()
            assert "permission denied" in message or "policy" in message, (
                f"{table}: cross-tenant DELETE failed, but not by privilege or policy: "
                f"{exc}"
            )
            mechanism = "refused by table privilege"
        else:
            assert result.rowcount == 0, (
                f"{table}: tenant {attacker} deleted {result.rowcount} of tenant "
                f"{victim}'s row(s)"
            )
            mechanism = "permitted but filtered to zero rows by policy"

    # Checked as the owner, so a successful delete cannot be hidden from the
    # verification query by the same policy that was supposed to prevent it.
    with engine.begin() as conn:
        survives = conn.execute(
            text(f"SELECT count(*) FROM public.{table} WHERE id = :id"),
            {"id": ids[(table, victim)]},
        ).scalar()
    assert survives == 1, (
        f"{table}: {ids[(table, victim)]} was deleted despite being {mechanism}"
    )


@pytest.mark.parametrize("table", sorted(AUTH_UID_TABLES))
def test_authenticated_cannot_insert_a_row_owned_by_another_tenant(engine, seeded, table):
    """WITH CHECK, not USING. Forging ownership on write is the mirror of reading
    someone else's row, and a policy with USING but no WITH CHECK would allow it."""
    attacker, victim = TENANTS[4], TENANTS[5]
    payload = AUTH_UID_TABLES[table](victim, f"{seeded['run']}-forged")
    cols = ", ".join(payload)
    binds = ", ".join(f":{c}" for c in payload)

    with engine.begin() as conn:
        _as_authenticated(conn, attacker)
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                text(f"INSERT INTO public.{table} ({cols}) VALUES ({binds})"), payload
            )
    message = str(excinfo.value).lower()
    assert "policy" in message or "permission" in message, (
        f"{table}: insert of a row owned by {victim} was refused, but not by a policy: "
        f"{excinfo.value}"
    )


@pytest.mark.parametrize("table", sorted(AUTH_UID_TABLES))
def test_no_jwt_claim_means_no_rows(engine, seeded, table):
    """`auth.uid()` is NULL without a claim, and `NULL = user_id` is NULL, not true.

    This is the property that makes an unauthenticated PostgREST request safe on these
    tables, and it has to be checked rather than assumed: a policy written as
    `USING (true)` would pass every other test in this file and fail only here.
    """
    with engine.begin() as conn:
        _as_authenticated(conn, None)
        conn.execute(text("SELECT set_config(:n, '', true)"), {"n": CLAIM_GUC})
        count = conn.execute(text(f"SELECT count(*) FROM public.{table}")).scalar()
    assert count == 0, (
        f"{table}: {count} row(s) visible with no JWT claim installed"
    )


def test_all_six_tenants_are_mutually_isolated(engine, seeded):
    """Pairwise over every tenant and every auth.uid() table at once.

    Thirty ordered pairs per table. The per-table tests above would catch a total
    failure; this catches a policy that isolates most pairs and not one.
    """
    leaks: list[str] = []
    for table in sorted(AUTH_UID_TABLES):
        for attacker in TENANTS:
            with engine.begin() as conn:
                _as_authenticated(conn, attacker)
                visible = set(
                    conn.execute(
                        text(f"SELECT DISTINCT user_id::text FROM public.{table}")
                    ).scalars().all()
                )
            for other in visible - {attacker}:
                leaks.append(f"{table}: {attacker} -> {other}")
    assert not leaks, "cross-tenant visibility:\n  " + "\n  ".join(leaks)


# ── path 2: the service-role key bypasses RLS entirely ───────────────────────


def test_service_role_sees_every_tenant(engine, seeded):
    """Not a defect -- a documented property with a consequence worth stating.

    `service_role` is BYPASSRLS, so on every code path that uses the service-role key
    (`routers/library.py` wholesale, the billing webhook, the TEE reconciler) the
    database applies no tenant filtering whatsoever and the Python-level ownership checks
    are the ONLY isolation control. Asserting it here means the claim is measured rather
    than assumed, and it fails loudly if someone later believes RLS is a second layer on
    that path.
    """
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE service_role"))
        seen = set(
            conn.execute(text("SELECT DISTINCT user_id::text FROM public.strategies"))
            .scalars().all()
        )
    assert set(TENANTS).issubset(seen), (
        "service_role could not see all six tenants; the BYPASSRLS assumption in "
        "docs/saas-architecture/tenant-isolation-matrix.md may no longer hold"
    )


# ── path 3: SQLAlchemy connects as the table owner ───────────────────────────


def test_table_owner_is_exempt_from_its_own_policies(engine, seeded):
    """The reason RLS protects nothing on the SQLAlchemy path.

    Every DATABASE_URL in the repository carries username `postgres`, which owns these
    tables, and `implement_rls_policies_20260817.py` issues ENABLE ROW LEVEL SECURITY
    rather than FORCE. PostgreSQL exempts a table's owner from its policies unless FORCE
    is set, so the owner sees all six tenants' rows.

    Two consequences follow, and both belong in a launch assessment. The reassuring one:
    enabling these policies cannot break the application while it connects as the owner.
    The unreassuring one: tenant isolation on this path rests entirely on application
    `WHERE tenant_id = ...` clauses, with no database-level backstop.
    """
    with engine.begin() as conn:
        role = conn.execute(text("SELECT current_user")).scalar()
        seen = set(
            conn.execute(text("SELECT DISTINCT user_id::text FROM public.strategies"))
            .scalars().all()
        )
    assert set(TENANTS).issubset(seen), (
        f"connected as {role!r} and could NOT see all six tenants. If this fails, the "
        f"connecting role is no longer the owner, FORCE ROW LEVEL SECURITY has been "
        f"enabled, or both -- and the SQLAlchemy paths that read these tables will be "
        f"returning empty results in production."
    )


# ── the app.current_tenant_id policy family ──────────────────────────────────


@pytest.mark.parametrize("table", GUC_TABLES)
def test_guc_policy_denies_a_non_owner_when_the_setting_is_absent(engine, seeded, table):
    """The latent failure mode in `implement_rls_policies_20260817.py`.

    Its policies compare `tenant_id` to `current_setting('app.current_tenant_id', true)`,
    and nothing on any request path sets it -- `core/tenant_db_context.py` provides the
    mechanism, ADR-0006 records its adoption as not started, and its only importers are
    tests. With the setting absent the comparison is NULL rather than true, so a role
    that is subject to RLS matches zero rows.

    Here the claim is cleared as well, so the `*_owner_access` policy on these tables
    cannot satisfy the query instead and the GUC policy is measured on its own.
    """
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config(:n, '', true)"), {"n": CLAIM_GUC})
        _set_tenant_guc(conn, None)
        conn.execute(text("SET LOCAL ROLE authenticated"))
        count = conn.execute(text(f"SELECT count(*) FROM public.{table}")).scalar()
    assert count == 0, (
        f"{table}: a non-owner role saw {count} row(s) with no tenant context set"
    )


@pytest.mark.parametrize("table", GUC_TABLES)
def test_guc_policy_isolates_correctly_once_the_setting_is_installed(engine, seeded, table):
    """The other half: the policies are sound, they are simply never supplied an input.

    Worth proving because it decides what the remediation is. If the predicate were
    wrong, the policies would need rewriting; since it is right, adopting
    `tenant_db_context` on the request path is sufficient, and that is a materially
    smaller change to recommend.
    """
    for tenant in TENANTS:
        with engine.begin() as conn:
            conn.execute(text("SELECT set_config(:n, '', true)"), {"n": CLAIM_GUC})
            _set_tenant_guc(conn, tenant)
            conn.execute(text("SET LOCAL ROLE authenticated"))
            seen = set(
                conn.execute(
                    text(f"SELECT DISTINCT tenant_id::text FROM public.{table}")
                ).scalars().all()
            )
        assert seen == {tenant}, (
            f"{table}: with app.current_tenant_id={tenant} the visible tenants were "
            f"{sorted(seen)}"
        )


# ── RLS enabled with a predicate of TRUE, which is not row security ──────────
#
# `library_ratings` is the one table in the schema where `relrowsecurity` was true and
# the SELECT policy was `USING (true)`. Those two together read exactly like RLS being
# switched off, except that every inventory query reporting on `relrowsecurity` calls the
# table secured -- which is worse than an unguarded table, because it looks guarded.
#
# The table is dual purpose, and that is what makes the blanket policy wrong:
#   * `rating IS NOT NULL` rows are marketplace reviews. Public by design.
#   * `rating IS NULL` rows are FAVOURITES, written by POST /api/library/{id}/favorite
#     and read back by GET /api/library/favorites, which
#     `tests/property/test_tenant_isolation_matrix.py` classifies OWNER_SCOPED_LIST.
#
# These tests exercise the distinction as `authenticated` against real rows, not by
# reading `pg_policies`. The policy they measure is installed by
# `migrations/hardening/999_privilege_hardening.sql` section 3b.


@pytest.fixture(scope="module")
def ratings_seed(engine, seeded):
    """One listing, plus a review and a favourite owned by each of two tenants.

    Seeded as the owner for the same reason as `seeded`: the owner is the only role that
    can write a row on someone else's behalf.
    """
    run = seeded["run"]
    owner, other = TENANTS[0], TENANTS[1]
    listing = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.library_strategies "
                "(id, author_id, source_strategy_id, name, category, symbol, "
                " timeframe, exchange_id) "
                "VALUES (:id, :author, :src, :name, 'trend', 'BTC/USDT', '1h', 'binance')"
            ),
            {
                "id": listing,
                "author": owner,
                "src": str(uuid.uuid4()),
                "name": f"rls-ratings-{run}",
            },
        )
        # A review from `owner`, and a favourite from `other`. Only one row per
        # (library_id, user_id) is allowed by `library_ratings_user_library_idx`, so the
        # two roles cannot each hold both kinds on the same listing.
        conn.execute(
            text(
                "INSERT INTO public.library_ratings (library_id, user_id, rating, review_text) "
                "VALUES (:lib, :u, 5, :txt)"
            ),
            {"lib": listing, "u": owner, "txt": f"review-{run}"},
        )
        conn.execute(
            text(
                "INSERT INTO public.library_ratings (library_id, user_id, rating) "
                "VALUES (:lib, :u, NULL)"
            ),
            {"lib": listing, "u": other},
        )

    yield {"listing": listing, "reviewer": owner, "favouriter": other}

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM public.library_ratings WHERE library_id = :lib"),
            {"lib": listing},
        )
        conn.execute(
            text("DELETE FROM public.library_strategies WHERE id = :lib"), {"lib": listing}
        )


def test_a_review_is_readable_by_another_customer(engine, ratings_seed):
    """The half that must keep working. A listing page shows its reviews to everyone.

    If this fails the policy is too tight and the marketplace lost a feature, which is a
    regression in the opposite direction and just as much a defect.
    """
    with engine.begin() as conn:
        _as_authenticated(conn, ratings_seed["favouriter"])
        rows = conn.execute(
            text(
                "SELECT user_id::text FROM public.library_ratings "
                "WHERE library_id = :lib AND rating IS NOT NULL"
            ),
            {"lib": ratings_seed["listing"]},
        ).scalars().all()
    assert ratings_seed["reviewer"] in rows, (
        "a published review is not visible to another customer; "
        "library_ratings_select_reviews_public is too restrictive"
    )


def test_a_favourite_is_not_readable_by_another_customer(engine, ratings_seed):
    """The leak itself. A NULL-rating row is a private favourite.

    Under the old `USING (true)` policy this returned the other customer's row and the
    assertion below failed.
    """
    with engine.begin() as conn:
        _as_authenticated(conn, ratings_seed["reviewer"])
        rows = conn.execute(
            text(
                "SELECT user_id::text FROM public.library_ratings "
                "WHERE library_id = :lib AND rating IS NULL"
            ),
            {"lib": ratings_seed["listing"]},
        ).scalars().all()
    assert rows == [], (
        f"one customer can read another's private favourites: saw {rows}. "
        f"library_ratings rows with a NULL rating are owner-scoped"
    )


def test_a_customer_can_read_its_own_favourite(engine, ratings_seed):
    """The complement, so the test above cannot pass by denying everyone.

    A policy of `USING (false)` would satisfy the previous assertion and break the
    feature; this is what distinguishes isolation from an outage.
    """
    with engine.begin() as conn:
        _as_authenticated(conn, ratings_seed["favouriter"])
        rows = conn.execute(
            text(
                "SELECT user_id::text FROM public.library_ratings "
                "WHERE library_id = :lib AND rating IS NULL"
            ),
            {"lib": ratings_seed["listing"]},
        ).scalars().all()
    assert rows == [ratings_seed["favouriter"]], (
        f"a customer cannot see its own favourite: saw {rows}"
    )


def test_no_per_customer_table_has_an_unconstrained_policy(engine):
    """The class, not the instance.

    `library_ratings` was found by sweeping for permissive policies whose predicate is
    the constant TRUE on tables carrying an ownership column -- the same sweep that
    `999_privilege_hardening.sql` section 4 runs as a provisioning gate. Asserting it
    here as well means a new table with a `USING (true)` policy fails a test run even if
    nobody re-provisions.

    `service_role` is excluded because it holds BYPASSRLS; a TRUE policy grants it
    nothing it does not already have, and the 51 such policies in this schema are
    deliberate.
    """
    owner_columns = [
        "user_id", "tenant_id", "profile_id", "owner_id", "author_id",
        "customer_id", "subscriber_id", "referrer_id", "created_by",
        "creator_id", "seller_id", "account_id",
    ]
    with engine.begin() as conn:
        offending = conn.execute(
            text(
                "SELECT p.tablename || '.' || p.policyname || ' (' || p.cmd || ')' "
                "FROM pg_policies p "
                "WHERE p.schemaname = 'public' "
                "  AND p.permissive = 'PERMISSIVE' "
                "  AND coalesce(p.qual, p.with_check) = 'true' "
                "  AND ('anon' = ANY(p.roles) OR 'authenticated' = ANY(p.roles) "
                "       OR 'public' = ANY(p.roles)) "
                "  AND EXISTS (SELECT 1 FROM information_schema.columns col "
                "              WHERE col.table_schema = 'public' "
                "                AND col.table_name = p.tablename "
                "                AND col.column_name = ANY(:cols)) "
                "ORDER BY 1"
            ),
            {"cols": owner_columns},
        ).scalars().all()
    assert offending == [], (
        "these policies carry a predicate of TRUE on a table with a per-customer "
        f"column, so they apply no row filter while still reporting "
        f"relrowsecurity = true: {offending}"
    )
