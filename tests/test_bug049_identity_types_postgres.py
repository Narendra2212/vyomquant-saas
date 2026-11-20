"""BUG-049 regression: customer-identity columns are uuid and declare their owner.

THE DEFECT
----------
`user_id` was declared three ways across this schema -- `uuid` on 43 columns,
`character varying(36)` on `invoices`/`payment_methods`/`subscriptions`, and `text` on
nine more including `exchange_keys`. A foreign key cannot span two types, so twelve
columns declared no reference to the customer whose data they held.

Impact was integrity and retention rather than confidentiality: RLS still isolated those
tables because their policies cast both sides. What it allowed was a row naming a
customer who does not exist, and -- on `exchange_keys` -- **encrypted exchange API
credentials outliving the account that owned them**, because with no foreign key there is
no `ON DELETE` to cascade.

WHAT THESE TESTS ASSERT, AND WHY EACH ONE IS SEPARATE
-----------------------------------------------------
  1. every class A/B identity column is `uuid`                      -- the type defect
  2. every one of the twelve declares a foreign key to `profiles`   -- the missing link
  3. the ON DELETE action is the one that was CHOSEN, per table     -- not a default
  4. credentials CASCADE                                            -- the security case
  5. financial records do NOT cascade                               -- the retention case
  6. the constraint actually bites: an orphan insert is rejected    -- behaviour, not DDL
  7. deleting a customer removes their credentials                  -- behaviour
  8. deleting a customer with an invoice is REFUSED                 -- behaviour
  9. RLS still isolates the converted tables                        -- no regression
 10. the policies survived the conversion                           -- none went missing

Tests 6, 7 and 8 are the ones that matter most. Asserting a constraint EXISTS in the
catalogue is asserting that DDL ran. Asserting that an orphan insert is rejected, that a
cascade fires, and that a restrict refuses, is asserting that the database behaves the
way the launch report claims.
"""

from __future__ import annotations

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason=(
        "needs a real PostgreSQL instance: SQLite has no uuid type, no referential "
        "ON DELETE actions worth testing here, and no RLS. Start one with "
        "staging/bootstrap_pg.py."
    ),
)

sa = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text  # noqa: E402

CANONICAL_TYPE = "uuid"

#: The twelve columns alembic `d4e5f6a7b8c9` converted, with the ON DELETE chosen for
#: each and the reason it was chosen. Duplicated from the migration deliberately: if
#: someone changes the migration, this test keeps asserting the reviewed contract and the
#: disagreement is the signal.
EXPECTED = {
    # Operational per-customer data -- goes when the customer goes.
    "exchange_connections": ("user_id", "CASCADE"),
    "exchange_keys": ("user_id", "CASCADE"),
    "notification_settings": ("user_id", "CASCADE"),
    "referral_profiles": ("user_id", "CASCADE"),
    "risk_settings": ("user_id", "CASCADE"),
    "strategy_limits": ("user_id", "CASCADE"),
    # Financial and audit records -- must survive customer deletion.
    "billing_invoices": ("user_id", "RESTRICT"),
    "invoices": ("user_id", "RESTRICT"),
    "payment_methods": ("user_id", "RESTRICT"),
    "subscriptions": ("user_id", "RESTRICT"),
    "risk_settings_audit": ("user_id", "RESTRICT"),
    "security_logs": ("user_id", "RESTRICT"),
}

#: Credential-bearing. An orphaned row here is a live API key with no owner.
CREDENTIAL_TABLES = ("exchange_keys", "exchange_connections")

#: Must survive customer deletion for audit and tax reasons.
FINANCIAL_TABLES = ("invoices", "billing_invoices", "subscriptions", "payment_methods")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    try:
        yield eng
    finally:
        eng.dispose()


def _fk_for(conn, table: str, column: str):
    return conn.execute(
        text(
            "SELECT con.conname, "
            "       CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' "
            "            WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE' "
            "            WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' END, "
            "       fc.relname "
            "FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
            "JOIN pg_class fc ON fc.oid = con.confrelid "
            "CROSS JOIN LATERAL unnest(con.conkey) AS k(attnum) "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid "
            "                     AND att.attnum = k.attnum "
            "WHERE con.contype = 'f' AND c.relname = :t AND att.attname = :c"
        ),
        {"t": table, "c": column},
    ).first()


# ── 1. the type defect ───────────────────────────────────────────────────────


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_the_identity_column_is_uuid(engine, table):
    column, _on_delete = EXPECTED[table]
    with engine.begin() as conn:
        dtype = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        ).scalar()
    assert dtype == CANONICAL_TYPE, (
        f"public.{table}.{column} is {dtype!r}, not {CANONICAL_TYPE}. A foreign key "
        f"cannot span two types, so this column can declare no reference to the "
        f"customer whose data it holds. That is BUG-049."
    )


def test_no_identity_column_anywhere_has_a_divergent_type(engine):
    """The CLASS, not the twelve instances.

    A thirteenth table added later with a `text user_id` would pass every parametrised
    test above by not being in the list. This sweeps the whole schema.
    """
    with engine.begin() as conn:
        divergent = conn.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "  AND column_name IN ('user_id', 'tenant_id') "
                "  AND data_type <> 'uuid' "
                "ORDER BY table_name"
            )
        ).fetchall()
    assert divergent == [], (
        "these customer/tenant identity columns are not uuid, so they cannot reference "
        f"profiles(id): {[(r[0], r[1], r[2]) for r in divergent]}"
    )


# ── 2 & 3. the foreign key, and the action that was chosen ───────────────────


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_the_identity_column_declares_its_owner(engine, table):
    column, _on_delete = EXPECTED[table]
    with engine.begin() as conn:
        fk = _fk_for(conn, table, column)
    assert fk is not None, (
        f"public.{table}.{column} has no foreign key. Nothing stops a row naming a "
        f"customer who does not exist, and nothing happens to the row when that "
        f"customer is deleted."
    )
    assert fk[2] == "profiles", (
        f"public.{table}.{column} references {fk[2]!r}, expected 'profiles'"
    )


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_the_on_delete_action_is_the_one_that_was_chosen(engine, table):
    """ON DELETE is a decision, not a default, and this is where the decision lives.

    `CASCADE` on a financial table would destroy invoices. `RESTRICT` on a credential
    table would leave the credential in place and merely make deletion fail. Getting
    either backwards is worse than having no constraint, so the expected action is
    asserted per table rather than "some action exists".
    """
    column, expected_action = EXPECTED[table]
    with engine.begin() as conn:
        fk = _fk_for(conn, table, column)
    assert fk is not None, f"public.{table}.{column} has no foreign key"
    assert fk[1] == expected_action, (
        f"public.{table}.{column} is ON DELETE {fk[1]}, expected {expected_action}. "
        f"{'A financial record must survive customer deletion.' if table in FINANCIAL_TABLES else ''}"
        f"{'A credential must NOT survive customer deletion.' if table in CREDENTIAL_TABLES else ''}"
    )


# ── 6, 7, 8. behaviour, not DDL ──────────────────────────────────────────────


@pytest.fixture
def customer(engine):
    """A real customer, removed at teardown. Committed, because ON DELETE behaviour
    cannot be observed inside a transaction that is rolled back regardless.
    """
    ident = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": ident, "e": f"bug049-{ident[:8]}@example.invalid"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": ident})

    yield ident

    with engine.begin() as conn:
        # Order matters: RESTRICT tables must be emptied before the profile can go.
        for table in EXPECTED:
            conn.execute(
                text(f"DELETE FROM public.{table} WHERE user_id = :i"), {"i": ident}
            )
        conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": ident})
        conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": ident})


def test_a_row_naming_a_nonexistent_customer_is_rejected(engine, customer):
    """The constraint has to BITE. Asserting it exists in `pg_constraint` asserts that
    DDL ran; this asserts the database refuses the row the defect allowed.
    """
    ghost = str(uuid.uuid4())
    with engine.begin() as conn:
        with pytest.raises(Exception) as caught:
            conn.execute(
                text(
                    "INSERT INTO public.exchange_keys "
                    "(user_id, exchange_id, encrypted_api_key, encrypted_secret_key) "
                    "VALUES (:u, 'binance', 'enc', 'enc')"
                ),
                {"u": ghost},
            )
    assert "foreign key" in str(caught.value).lower(), (
        f"inserting an exchange credential for a customer that does not exist was not "
        f"rejected by a foreign key: {caught.value}"
    )


def test_deleting_a_customer_removes_their_exchange_credentials(engine):
    """The security case for CASCADE, measured.

    An encrypted exchange API key whose owner no longer exists is a credential nobody
    can audit, rotate or revoke through the product. It must not survive the account.
    """
    ident = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": ident, "e": f"bug049-casc-{ident[:8]}@example.invalid"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": ident})
        conn.execute(
            text(
                "INSERT INTO public.exchange_keys "
                "(user_id, exchange_id, encrypted_api_key, encrypted_secret_key) "
                "VALUES (:u, 'binance', 'enc', 'enc')"
            ),
            {"u": ident},
        )

    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT count(*) FROM public.exchange_keys WHERE user_id = :u"),
            {"u": ident},
        ).scalar()
    assert before == 1, "the credential was not seeded, so this proves nothing"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": ident})

    with engine.begin() as conn:
        after = conn.execute(
            text("SELECT count(*) FROM public.exchange_keys WHERE user_id = :u"),
            {"u": ident},
        ).scalar()
        conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": ident})

    assert after == 0, (
        f"{after} exchange credential(s) survived the deletion of their owner. An "
        f"orphaned encrypted API key cannot be audited, rotated or revoked through the "
        f"product."
    )


def test_deleting_a_customer_who_has_an_invoice_is_refused(engine):
    """The retention case for RESTRICT, measured.

    CASCADE here would silently destroy financial history. RESTRICT forces whoever
    builds an account-deletion path to decide what happens to the invoices, rather than
    finding out afterwards that they are gone.
    """
    ident = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:i, :e)"),
            {"i": ident, "e": f"bug049-restr-{ident[:8]}@example.invalid"},
        )
        conn.execute(text("INSERT INTO public.profiles (id) VALUES (:i)"), {"i": ident})
        conn.execute(
            text(
                # `created_at` is NOT NULL with no default on this table, so it must be
                # supplied. `"amtUSD"` / `"amtINR"` are mixed case and need the quotes.
                "INSERT INTO public.invoices "
                "(id, user_id, date, \"amtUSD\", \"amtINR\", status, created_at) "
                "VALUES (:inv, :u, '2026-01-01', 19.99, 1660.0, 'paid', now())"
            ),
            {"inv": f"inv-{ident[:12]}", "u": ident},
        )

    with engine.begin() as conn:
        with pytest.raises(Exception) as caught:
            conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": ident})
    assert "foreign key" in str(caught.value).lower(), (
        f"deleting a customer who has an invoice was not refused: {caught.value}"
    )

    # The invoice is still there, which is the whole point.
    with engine.begin() as conn:
        surviving = conn.execute(
            text("SELECT count(*) FROM public.invoices WHERE user_id = :u"),
            {"u": ident},
        ).scalar()
    assert surviving == 1, "the invoice did not survive the refused deletion"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM public.invoices WHERE user_id = :u"), {"u": ident})
        conn.execute(text("DELETE FROM public.profiles WHERE id = :i"), {"i": ident})
        conn.execute(text("DELETE FROM auth.users WHERE id = :i"), {"i": ident})


# ── 9 & 10. the conversion did not cost anything ─────────────────────────────


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_row_level_security_is_still_enabled(engine, table):
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
        f"public.{table} lost row level security during the BUG-049 conversion. The "
        f"migration drops and restores policies in order to retype the column; a table "
        f"that came back without RLS is a table whose filtering silently disappeared."
    )


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_the_table_still_has_at_least_one_policy(engine, table):
    """The conversion drops every policy on these tables and recreates them. A table
    that came back with zero is fail-closed rather than leaky, but it is still a feature
    that stopped working, and silently.
    """
    with engine.begin() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname='public' AND tablename=:t"
            ),
            {"t": table},
        ).scalar()
    assert count > 0, (
        f"public.{table} has no RLS policy after the BUG-049 conversion. The migration "
        f"captured and restored them; this one did not come back."
    )


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_every_restored_policy_still_type_checks(engine, table):
    """The failure mode the first attempt at this migration hit, as a test.

    Restoring `USING ((auth.uid())::text = user_id)` verbatim against a now-`uuid` column
    raises `operator does not exist: text = uuid`. The migration handles it by casting
    the column in the predicate -- but a predicate that is merely PRESENT is not
    necessarily one PostgreSQL can evaluate, so each policy is forced to execute.

    `EXPLAIN` is enough: it plans the query, which requires resolving every operator in
    the policy predicate, without reading any row.
    """
    with engine.begin() as conn:
        conn.execute(text("SELECT set_config('request.jwt.claim.sub', :v, true)"),
                     {"v": str(uuid.uuid4())})
        conn.execute(text("SELECT set_config('app.current_tenant_id', :v, true)"),
                     {"v": str(uuid.uuid4())})
        conn.execute(text("SET LOCAL ROLE authenticated"))
        try:
            conn.execute(text(f'EXPLAIN SELECT 1 FROM public."{table}"'))
        except Exception as exc:  # noqa: BLE001 - the message is the finding
            if "permission denied" in str(exc).lower():
                pytest.skip(
                    f"`authenticated` holds no SELECT on {table}; its policy cannot be "
                    f"exercised from this role. Not a pass -- see "
                    f"RLS_TENANT_ISOLATION_VERIFICATION.md section 8.3."
                )
            pytest.fail(
                f"public.{table}'s restored RLS policy does not type-check: {exc}"
            )
