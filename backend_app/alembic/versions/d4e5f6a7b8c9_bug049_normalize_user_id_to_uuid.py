"""BUG-049: normalise customer-identity columns to uuid and declare their foreign keys

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-06 09:30:00.000000

THE DEFECT
----------
`user_id` is declared three ways across this schema:

    uuid                     43 columns -- matching profiles.id and auth.users.id
    character varying(36)    invoices, payment_methods, subscriptions
    text                     billing_invoices, exchange_connections, exchange_keys,
                             notification_settings, referral_profiles, risk_settings,
                             risk_settings_audit, security_logs, strategy_limits

A foreign key cannot span two types, so **none of those 12 columns declares a reference
to the customer whose data it holds.** This is BUG-033's unfixed sibling: alembic
`a1b2c3d4e5f6` normalised `tenant_id` to `uuid` and all 7 of its blocked foreign keys
then landed. `user_id` was not part of that change.

WHAT IS AND IS NOT AT RISK
--------------------------
NOT confidentiality. Every one of these tables is still isolated by RLS, because its
policies cast both sides -- `(auth.uid())::text = (user_id)::text` -- and that is
measured rather than assumed: `scripts/verify_production_security.py` reports `invoices`
and `subscriptions` as ISOLATED, meaning the owner reads its own row and neither another
customer nor `anon` can.

What IS at risk is integrity and retention:

  * a row can name a customer who does not exist;
  * deleting a customer does not cascade, because there is no ON DELETE to cascade -- the
    rows are simply orphaned. On `exchange_keys` that means **encrypted exchange API
    credentials outliving the account that owned them**, which is the worst of the twelve
    and the reason this is not purely housekeeping.

WHY ONLY TWELVE COLUMNS, AND WHY THESE
--------------------------------------
`scripts/bug049_enumerate.py` examines 81 identity-like columns and CLASSIFIES each one
before anything is altered, writing `BUG049_IDENTITY_MATRIX.json`. Only classes A
(canonical customer identity) and B (tenant identity) are candidates. The classifier
exists because "it is text, convert it" is wrong:

  * class C is an EXTERNAL PROVIDER identifier. Converting `provider_reference` to a uuid
    would destroy the only link to Razorpay or Stripe. None of the columns in scope
    turned out to be class C -- the guard did not need to fire, which is worth saying
    plainly rather than claiming a finding.
  * class D is a reference to a non-customer table. `paper_orders.account_id` is a uuid
    that references `paper_accounts`, not a customer; four such columns are excluded.
  * class E is a legacy identifier whose values are NOT UUID-shaped. Conversion is
    BLOCKED for those, not forced.

ON DELETE IS CHOSEN PER TABLE, DELIBERATELY
-------------------------------------------
    CASCADE   exchange_connections, exchange_keys, notification_settings,
              referral_profiles, risk_settings, strategy_limits
              Operational per-customer data. `exchange_keys` and `exchange_connections`
              are the security-driven cases: an orphaned row is a live credential with no
              owner, so it must go when the customer does.

    RESTRICT  billing_invoices, invoices, payment_methods, subscriptions,
              risk_settings_audit, security_logs
              Financial and audit records that must SURVIVE customer deletion for audit
              and tax reasons. CASCADE would destroy them. SET NULL is impossible --
              every one of these columns is NOT NULL -- so RESTRICT is used, which forces
              whoever builds an account-deletion path to handle these rows explicitly
              instead of silently discarding financial history.

              Verified before choosing it: there is **no account-deletion path in
              `backend_app/`** today (no `DELETE FROM profiles`, no `delete_user`), so
              RESTRICT cannot break an existing flow. It constrains a flow that does not
              exist yet, which is the right time to constrain it.

IT REFUSES RATHER THAN GUESSES
------------------------------
Before altering anything, every column is inventoried for values that are not
UUID-shaped. If any are found the migration RAISES with the table, the column, the count
and a sample, having altered nothing.

This matters more than usual here. On the database this was developed against, all 12
tables are EMPTY, so the local inventory is vacuously clean and proves nothing about
production -- `BUG049_IDENTITY_MATRIX.json` records that caveat explicitly. The precheck
runs again wherever this revision is applied, which is the only place the question can
actually be answered.

Silently casting a customer identity would reassign the data it owns. Silently dropping
one would orphan it. Neither is acceptable for a table holding invoices or API keys, so
the migration stops and asks.

POLICIES ARE DROPPED AND RESTORED, NOT REWRITTEN
------------------------------------------------
PostgreSQL refuses `ALTER COLUMN ... TYPE` on a column referenced by an RLS policy. The
policies are captured from `pg_policies`, dropped, and recreated verbatim afterwards --
the same technique `a1b2c3d4e5f6` uses, and for the same reason: a predicate stored as
`(auth.uid())::text = (user_id)::text` remains correct once the column is uuid, because
the cast is explicit. Rewriting the predicates by hand would be the risky version.

The count is asserted afterwards. A column retyped with its policy missing is a table
whose row filtering silently disappeared, which is worse than a failed migration.

NO FINANCIAL COLUMN IS DROPPED. No data is deleted. The only changes are the column
type, the foreign key, and the temporary absence of policies inside the transaction.
"""
from typing import Sequence, Union

import re

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID_REGEX = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

#: (table, column, ON DELETE). Spelled out rather than derived from the database at
#: runtime: a migration that discovers its own scope would do something different on
#: every database it meets, and the whole point of this one is that its scope was
#: reviewed in `BUG049_IDENTITY_MATRIX.json` before it was written.
_TARGETS: tuple[tuple[str, str, str], ...] = (
    # Operational per-customer data -- goes when the customer goes.
    ("exchange_connections", "user_id", "CASCADE"),
    ("exchange_keys", "user_id", "CASCADE"),
    ("notification_settings", "user_id", "CASCADE"),
    ("referral_profiles", "user_id", "CASCADE"),
    ("risk_settings", "user_id", "CASCADE"),
    ("strategy_limits", "user_id", "CASCADE"),
    # Financial and audit records -- must survive customer deletion.
    ("billing_invoices", "user_id", "RESTRICT"),
    ("invoices", "user_id", "RESTRICT"),
    ("payment_methods", "user_id", "RESTRICT"),
    ("subscriptions", "user_id", "RESTRICT"),
    ("risk_settings_audit", "user_id", "RESTRICT"),
    ("security_logs", "user_id", "RESTRICT"),
)

_FK_PREFIX = "fk_bug049_"


def _validate_table_name(table_name: str) -> str:
    """Refuse anything that is not a bare SQL identifier. Raises; never sanitises.

    Named to match `backend/transactional_execution_manager.py:37` deliberately:
    `tests/test_bandit_sast_gate_is_green.py` treats a call to `_validate_table_name` as
    the one way an interpolated expression may reach a statement. Table, column and
    policy names cannot be bound parameters, and the policy names below arrive from a
    query against `pg_policies`.
    """
    s = str(table_name)
    if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
        return s
    raise ValueError(f"not a SQL identifier: {s!r}")


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:t"
            ),
            {"t": table},
        ).scalar()
    )


def _column_type(conn, table: str, column: str) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
        ),
        {"t": table, "c": column},
    ).scalar()


def _refuse_if_unconvertible(conn, raw_table: str, raw_column: str) -> int:
    """Count non-UUID values and RAISE if there are any. Returns the row count checked.

    The return value is used to distinguish "checked N values and they are all fine"
    from "the table was empty so nothing was checked" -- a distinction the local run
    could not make, because every one of these tables is empty there.
    """
    table = _validate_table_name(raw_table)
    column = _validate_table_name(raw_column)

    total = conn.execute(
        sa.text(f'SELECT count(*) FROM public."{table}" WHERE "{column}" IS NOT NULL')  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
    ).scalar()

    bad = conn.execute(
        sa.text(
            f'SELECT count(*) FROM public."{table}" '  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
            f'WHERE "{column}" IS NOT NULL AND "{column}"::text !~* :re'
        ),
        {"re": UUID_REGEX},
    ).scalar()

    if not bad:
        return int(total or 0)

    sample = conn.execute(
        sa.text(
            f'SELECT DISTINCT "{column}"::text FROM public."{table}" '  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
            f'WHERE "{column}" IS NOT NULL AND "{column}"::text !~* :re LIMIT 5'
        ),
        {"re": UUID_REGEX},
    ).scalars().all()

    raise RuntimeError(
        f"BUG-049 refuses to convert public.{table}.{column}: {bad} of {total} non-null "
        f"value(s) are not UUID-shaped. Sample: {sample!r}. NOTHING HAS BEEN ALTERED.\n"
        f"Decide what those rows are -- a legacy identifier, a service account, a test "
        f"fixture, or a defect -- and reconcile them first. This migration will not "
        f"guess, because silently casting a customer identity reassigns the data it "
        f"owns and silently dropping one orphans it."
    )


def _capture_policies(conn, tables: set[str]) -> list[dict]:
    rows = conn.execute(
        sa.text(
            "SELECT tablename, policyname, permissive, roles, cmd, qual, with_check "
            "FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = ANY(:tables) "
            "ORDER BY tablename, policyname"
        ),
        {"tables": list(tables)},
    ).mappings().all()
    return [dict(r) for r in rows]


def _drop_policies(captured: list[dict]) -> None:
    for p in captured:
        policy = _validate_table_name(p["policyname"])
        table = _validate_table_name(p["tablename"])
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON public.{table}')


_CAST_PLACEHOLDER = "\x00ALREADY_CAST\x00"


def _cast_column_to_text(predicate: str, column: str) -> str:
    """Rewrite bare references to `column` as `(column)::text`.

    WHY THIS IS NEEDED, AND WHY IT IS THE MINIMAL CHANGE
    ----------------------------------------------------
    `a1b2c3d4e5f6` restored policies verbatim and that was correct there, because every
    predicate it moved was already double-cast: `(tenant_id)::text = current_setting(...)`.
    Here they are not. `billing_invoices` carries

        USING ((auth.uid())::text = user_id)

    -- the LEFT side cast, the column bare. That is valid while `user_id` is `text`. The
    moment the column becomes `uuid`, restoring it verbatim fails with

        operator does not exist: text = uuid

    which is exactly what happened on the first attempt, and alembic's transactional DDL
    rolled the whole revision back -- 188 policies intact, column still text, revision
    unmoved. Failing loudly and leaving the database recoverable is the behaviour that
    was wanted; the fix is to stop producing an invalid statement.

    Casting the COLUMN to text restores the ORIGINAL comparison exactly: a text
    comparison between `auth.uid()::text` and the column's text form. Nothing about the
    policy's meaning changes, and the result is the same double-cast shape that
    `invoices`, `payment_methods` and `subscriptions` already use today -- so this makes
    the twelve consistent rather than inventing a thirteenth convention.

    The alternative was rewriting to `auth.uid() = user_id`, a native uuid comparison.
    That is arguably BETTER -- uuid equality normalises case, so a stored uppercase id
    would start matching its owner, where the text comparison never did -- but it is a
    behaviour CHANGE, and a migration whose job is a type conversion is not the place to
    silently alter who can see a row. Recorded in POST_LAUNCH_IMPROVEMENTS instead.

    Existing `(column)::text` occurrences are protected before the substitution, so a
    double-cast predicate is left alone rather than becoming `((col)::text)::text`.
    """
    protected = predicate.replace(f"({column})::text", _CAST_PLACEHOLDER)
    # Word boundary on both sides, and not preceded by `.` so `t.user_id` in a
    # correlated subquery is not mangled into `t.(user_id)::text`.
    rewritten = re.sub(
        rf"(?<![\w.\"]){re.escape(column)}(?![\w\"])",
        f"({column})::text",
        protected,
    )
    return rewritten.replace(_CAST_PLACEHOLDER, f"({column})::text")


def _policy_sql(p: dict, qual: str | None, with_check: str | None) -> str:
    policy = _validate_table_name(p["policyname"])
    table = _validate_table_name(p["tablename"])
    cmd = _validate_table_name(p["cmd"])
    roles = ", ".join(
        "public" if r == "public" else f'"{_validate_table_name(r)}"'
        for r in p["roles"]
    )
    kind = "PERMISSIVE" if p["permissive"] == "PERMISSIVE" else "RESTRICTIVE"
    sql = (
        f'CREATE POLICY "{policy}" ON public.{table} '
        f'AS {kind} FOR {cmd} TO {roles}'
    )
    # `qual` and `with_check` are PostgreSQL's own rendering of a predicate -- arbitrary
    # SQL by nature and not reducible to an identifier. The source is `pg_policies` on
    # this same database, never request input. Concatenated rather than interpolated so
    # each site is visible to the SAST gate and to a reader.
    if qual:
        sql = sql + " USING (" + qual + ")"
    if with_check:
        sql = sql + " WITH CHECK (" + with_check + ")"
    return sql


def _restore_policies(conn, captured: list[dict], columns_by_table: dict[str, str]
                      ) -> list[str]:
    """Recreate each captured policy. Returns the names that needed a cast rewrite.

    Verbatim is ATTEMPTED FIRST and preferred. The rewrite is applied only when
    PostgreSQL itself rejects the verbatim form with `undefined_function` (42883) --
    which is the server telling us the column type moved underneath the predicate. That
    makes the fallback evidence-driven rather than speculative: a predicate that still
    type-checks is never touched.
    """
    rewritten: list[str] = []

    for p in captured:
        column = columns_by_table.get(p["tablename"])
        verbatim = _policy_sql(p, p["qual"], p["with_check"])

        savepoint = conn.begin_nested()
        try:
            conn.execute(sa.text(verbatim))
            savepoint.commit()
            continue
        except sa.exc.ProgrammingError as exc:
            savepoint.rollback()
            if getattr(exc.orig, "pgcode", None) != "42883" or column is None:
                raise

        qual = _cast_column_to_text(p["qual"], column) if p["qual"] else p["qual"]
        check = (
            _cast_column_to_text(p["with_check"], column) if p["with_check"]
            else p["with_check"]
        )
        conn.execute(sa.text(_policy_sql(p, qual, check)))
        rewritten.append(f"{p['tablename']}.{p['policyname']}")

    return rewritten


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        # SQLite has no roles, no policies and no uuid type. The ORM lane runs there and
        # this revision has nothing to say to it.
        return

    present = [
        (t, c, on_delete) for t, c, on_delete in _TARGETS
        if _table_exists(conn, t)
    ]
    if not present:
        print("bug049: none of the target tables exist; nothing to do")
        return

    # ── PHASE 1: inventory everything BEFORE altering anything ───────────────
    #
    # Deliberately a separate pass. If the eleventh table holds an unconvertible value,
    # that must be discovered before the first ten have been rewritten -- otherwise the
    # migration fails half-applied, and on a money table that is the worst outcome
    # available.
    checked: dict[str, int] = {}
    for table, column, _on_delete in present:
        if _column_type(conn, table, column) == "uuid":
            continue
        checked[f"{table}.{column}"] = _refuse_if_unconvertible(conn, table, column)

    if checked:
        proven = {k: v for k, v in checked.items() if v > 0}
        empty = [k for k, v in checked.items() if v == 0]
        print(f"bug049: inventory clean for {len(checked)} column(s)")
        if proven:
            print(f"bug049:   convertibility PROVEN against real values: {proven}")
        if empty:
            print(f"bug049:   tables EMPTY, so nothing was proven for: {empty}")

    # ── PHASE 2: drop the policies that block ALTER COLUMN TYPE ──────────────
    affected = {t for t, _c, _d in present}
    captured = _capture_policies(conn, affected)
    if captured:
        print(f"bug049: temporarily dropping {len(captured)} policy/policies")
        _drop_policies(captured)

    # ── PHASE 3: convert the types ───────────────────────────────────────────
    converted = 0
    for raw_table, raw_column, _on_delete in present:
        if _column_type(conn, raw_table, raw_column) == "uuid":
            continue
        table = _validate_table_name(raw_table)
        column = _validate_table_name(raw_column)
        op.execute(
            f'ALTER TABLE public."{table}" '
            f'ALTER COLUMN "{column}" TYPE uuid USING "{column}"::uuid'
        )
        converted += 1
        print(f"bug049: public.{table}.{column} -> uuid")

    # ── PHASE 4: restore the policies, and ASSERT they are all back ──────────
    if captured:
        columns_by_table = {t: c for t, c, _d in present}
        rewritten = _restore_policies(conn, captured, columns_by_table)
        if rewritten:
            print(f"bug049: {len(rewritten)} policy predicate(s) needed the column cast "
                  f"to text because the verbatim form no longer type-checked: "
                  f"{rewritten}")
        restored = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname='public' AND tablename = ANY(:tables)"
            ),
            {"tables": list({p["tablename"] for p in captured})},
        ).scalar()
        if restored != len(captured):
            raise RuntimeError(
                f"bug049: captured {len(captured)} policies and restored {restored}. "
                f"A retyped column with its policy missing is a table whose row "
                f"filtering silently disappeared. Refusing to continue."
            )
        print(f"bug049: restored {len(captured)} policy/policies")

    # ── PHASE 5: declare the foreign keys, with the chosen ON DELETE ─────────
    #
    # Added only where absent, so a re-run is a no-op. `profiles(id)` rather than
    # `auth.users(id)`: `profiles` is this schema's own customer table, it is what every
    # other customer foreign key in the tree references, and it already cascades from
    # `auth.users`.
    for raw_table, raw_column, on_delete in present:
        table = _validate_table_name(raw_table)
        column = _validate_table_name(raw_column)
        action = _validate_table_name(on_delete.replace(" ", "_"))
        name = _validate_table_name(f"{_FK_PREFIX}{table}_{column}")

        existing = conn.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint con "
                "JOIN pg_class c ON c.oid = con.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname='public' "
                "CROSS JOIN LATERAL unnest(con.conkey) AS k(attnum) "
                "JOIN pg_attribute att ON att.attrelid = con.conrelid "
                "                     AND att.attnum = k.attnum "
                "WHERE con.contype='f' AND c.relname=:t AND att.attname=:c"
            ),
            {"t": table, "c": column},
        ).scalar()
        if existing:
            continue

        # Orphans would make the constraint un-addable. Report them rather than letting
        # PostgreSQL's own error be the only explanation, and rather than deleting them.
        orphans = conn.execute(
            sa.text(
                f'SELECT count(*) FROM public."{table}" t '  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
                f'WHERE t."{column}" IS NOT NULL AND NOT EXISTS '
                f'      (SELECT 1 FROM public.profiles p WHERE p.id = t."{column}")'
            )
        ).scalar()
        if orphans:
            sample = conn.execute(
                sa.text(
                    f'SELECT DISTINCT t."{column}"::text FROM public."{table}" t '  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
                    f'WHERE t."{column}" IS NOT NULL AND NOT EXISTS '
                    f'      (SELECT 1 FROM public.profiles p WHERE p.id = t."{column}") '
                    f'LIMIT 5'
                )
            ).scalars().all()
            raise RuntimeError(
                f"bug049: cannot add a foreign key on public.{table}.{column}: "
                f"{orphans} row(s) name a customer that does not exist in "
                f"public.profiles. Sample: {sample!r}. The type conversion HAS been "
                f"applied; the foreign key has not. These orphans are exactly the defect "
                f"this migration exists to prevent, and deleting them automatically "
                f"would destroy customer records. Reconcile them, then re-run."
            )

        op.execute(
            f'ALTER TABLE public."{table}" '
            f'ADD CONSTRAINT "{name}" FOREIGN KEY ("{column}") '
            f'REFERENCES public.profiles(id) ON DELETE {on_delete}'
        )
        print(f"bug049: {name} -> profiles(id) ON DELETE {on_delete}")

    print(f"bug049: converted {converted} column(s), declared foreign keys on "
          f"{len(present)}")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    present = [(t, c, d) for t, c, d in _TARGETS if _table_exists(conn, t)]

    # Drop the foreign keys first -- they are this revision's own, named with a prefix,
    # so nothing pre-existing is touched.
    for raw_table, raw_column, _on_delete in present:
        table = _validate_table_name(raw_table)
        column = _validate_table_name(raw_column)
        name = _validate_table_name(f"{_FK_PREFIX}{table}_{column}")
        op.execute(f'ALTER TABLE public."{table}" DROP CONSTRAINT IF EXISTS "{name}"')

    # Then put the types back. `text` for the nine that were text, `varchar(36)` for the
    # three billing tables -- restoring the wrong one of the two would be a silent
    # schema change dressed up as a rollback.
    varchar36 = {"invoices", "payment_methods", "subscriptions"}

    affected = {t for t, _c, _d in present}
    captured = _capture_policies(conn, affected)
    if captured:
        _drop_policies(captured)

    for raw_table, raw_column, _on_delete in present:
        if _column_type(conn, raw_table, raw_column) != "uuid":
            continue
        table = _validate_table_name(raw_table)
        column = _validate_table_name(raw_column)
        target = "varchar(36)" if raw_table in varchar36 else "text"
        op.execute(
            f'ALTER TABLE public."{table}" '
            f'ALTER COLUMN "{column}" TYPE {target} USING "{column}"::text'
        )

    if captured:
        # Going back to text, the double-cast predicates this revision produced remain
        # valid -- `(user_id)::text` type-checks whether the column is uuid or text -- so
        # verbatim restoration succeeds here and the rewrite path is not exercised.
        _restore_policies(conn, captured, {t: c for t, c, _d in present})
