"""normalize tenant_id (and dag_tasks.task_id) to uuid so the foreign keys can exist

Revision ID: a1b2c3d4e5f6
Revises: merge_heads
Create Date: 2026-10-06 03:00:00.000000

WHAT THIS FIXES
---------------
BUG-033. The schema typed tenant ownership two different ways, and the split ran straight
through the financial tables:

    varchar(36)  orders, positions, fills, dag_tasks, idempotency_keys,
                 transaction_records, transaction_checkpoints, transaction_rollbacks
    uuid         execution_records, reconciliation_mismatches
    uuid         profiles.id  -- the identity every tenant_id is supposed to reference

`profiles.id` mirrors Supabase `auth.users.id` and is not ours to retype, so the varchar
side is the side that was wrong. PostgreSQL has no equality operator between `uuid` and
`character varying`, which is why five of the seven constraints in `add_foreign_keys`
could not be created:

    fk_fills_tenant_id         fills.tenant_id      varchar(36) -> profiles.id uuid
    fk_orders_tenant_id        orders.tenant_id     varchar(36) -> profiles.id uuid
    fk_positions_tenant_id     positions.tenant_id  varchar(36) -> profiles.id uuid
    fk_dag_tasks_tenant_id     dag_tasks.tenant_id  varchar(36) -> profiles.id uuid
    fk_execution_records_task_id
                               execution_records.task_id uuid -> dag_tasks.task_id varchar(36)

So there was no referential integrity on tenant ownership for any of those tables: a row
could name a profile that never existed or had been deleted, and nothing would object.

`dag_tasks.task_id` is converted for the same reason -- it is the primary key
`execution_records.task_id` (already uuid) points at, and that is the fifth constraint.

WHY THE APPLICATION DOES NOT NOTICE
-----------------------------------
The ORM columns move from `String(36)` to `sa.Uuid(as_uuid=False)`, which renders as a
native `uuid` on PostgreSQL and `CHAR(32)` on SQLite, and hands Python back a plain `str`
in both cases. Measured on both dialects before writing this, inserting
'3f2b8c14-7d6e-4a19-9b05-2c8e1f4a6d73' and reading it back:

    returned : '3f2b8c14-7d6e-4a19-9b05-2c8e1f4a6d73'  (type str)
    identical: True
    WHERE tenant_id = <hyphenated> finds row: True

That property is load bearing rather than incidental. `routers/dag_tasks.py:149` compares
`task.tenant_id != tenant["id"]` with no `str()` on either side; had the column begun
returning `uuid.UUID` objects, that comparison could never match and the endpoint would
have answered 403 to the task's rightful owner. `as_uuid=True` would have done exactly
that, which is why it is not used here. The SQLite lane is unaffected for the same reason.

WHY THE CAST IS CHECKED BEFORE IT IS PERFORMED
----------------------------------------------
`ALTER COLUMN ... TYPE uuid USING tenant_id::uuid` fails on the first value that is not a
UUID, and the failure arrives as `invalid input syntax for type uuid: "..."` naming one
row. On a table with real data that is the wrong way round: the operator needs to know
how many rows are affected and which ones, before anything is altered.

So each column is scanned first, and if any value will not cast, the migration raises
with the table, the column, the count and a sample -- and alters nothing. The database is
left exactly as it was, which is the recoverable state. There is no "clean it up
silently" branch, because deciding what a non-UUID tenant id means is not a migration's
call: it could be a test fixture, a legacy identifier, or a bug worth investigating.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'merge_heads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (table, column). Every one is varchar(36) before this revision.
_TENANT_COLUMNS = (
    ("orders", "tenant_id"),
    ("positions", "tenant_id"),
    ("fills", "tenant_id"),
    ("dag_tasks", "tenant_id"),
    ("idempotency_keys", "tenant_id"),
    ("transaction_records", "tenant_id"),
    ("transaction_checkpoints", "tenant_id"),
    ("transaction_rollbacks", "tenant_id"),
)

#: Converted so `execution_records.task_id` (uuid) can reference it.
_ID_COLUMNS = (
    ("dag_tasks", "task_id"),
)


def _column_type(conn, table: str, column: str) -> str | None:
    return conn.execute(
        sa.text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).scalar()


def _capture_policies(conn, tables: set[str]) -> list[dict]:
    """Every row policy on ``tables``, in a form that can be recreated verbatim.

    PostgreSQL refuses ``ALTER COLUMN ... TYPE`` on a column any policy references:

        FeatureNotSupported: cannot alter type of a column used in a policy definition
        DETAIL: policy orders_tenant_isolation_policy on table orders
                depends on column "tenant_id"

    So the policies have to come off and go back on. They are read out of
    ``pg_policies`` rather than hardcoded by name, because which ones exist depends on
    how far through the migration set a given database is -- a fresh provision has only
    the ``*_tenant_isolation_policy`` set from ``implement_rls_policies`` at this point,
    while a database that has also had ``006_reconcile_production_database.sql`` applied
    carries the ``*_owner_access`` and ``*_service_role`` pairs as well. Discovering them
    means this revision is correct on both, and on whatever someone adds later.
    """
    if not tables:
        return []
    rows = conn.execute(
        sa.text(
            "SELECT tablename, policyname, permissive, roles, cmd, "
            "       coalesce(qual, '') AS qual, coalesce(with_check, '') AS with_check "
            "FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = ANY(:tables) "
            "ORDER BY tablename, policyname"
        ),
        {"tables": list(tables)},
    ).mappings().all()
    return [dict(r) for r in rows]


def _validate_table_name(table_name: str) -> str:
    """Refuse anything that is not a bare SQL identifier. Raises; never sanitises.

    Named to match `backend/transactional_execution_manager.py:37` deliberately:
    `tests/test_bandit_sast_gate_is_green.py` treats a call to `_validate_table_name` as
    the one way an interpolated expression may reach a statement, and that test caught
    this file. The gate is right. Table, column and policy names cannot be bound
    parameters, so they are interpolated -- and the names below arrive from a query
    against `pg_policies`, which makes "it comes from a constant" not even true here.
    """
    s = str(table_name)
    if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
        return s
    raise ValueError(f"not a SQL identifier: {s!r}")


def _drop_policies(captured: list[dict]) -> None:
    for p in captured:
        policy = _validate_table_name(p["policyname"])
        table = _validate_table_name(p["tablename"])
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON public.{table}')


def _restore_policies(captured: list[dict]) -> None:
    """Recreate each captured policy against the retyped column.

    The predicates are reinstated as PostgreSQL rendered them, which is what makes this
    safe: a policy comparing ``tenant_id`` to a text GUC was stored as
    ``(tenant_id)::text = current_setting(...)`` and that expression is still correct
    once the column is uuid, because the cast is explicit in the predicate. Rewriting
    the predicates by hand would be the risky version of this.
    """
    for p in captured:
        policy = _validate_table_name(p["policyname"])
        table = _validate_table_name(p["tablename"])
        roles = ", ".join(
            "public" if r == "public" else f'"{_validate_table_name(r)}"'
            for r in p["roles"]
        )
        kind = "PERMISSIVE" if p["permissive"] == "PERMISSIVE" else "RESTRICTIVE"
        # `cmd` is one of SELECT/INSERT/UPDATE/DELETE/ALL as PostgreSQL reports it, and
        # it is an identifier, so it goes through the validator too.
        cmd = _validate_table_name(p["cmd"])
        sql = (
            f'CREATE POLICY "{policy}" ON public.{table} '
            f'AS {kind} FOR {cmd} TO {roles}'
        )
        # `qual` and `with_check` are PostgreSQL's own rendering of a predicate --
        # arbitrary SQL by nature, and not reducible to an identifier. They are
        # reinstated verbatim because that is the only faithful restoration, and the
        # source is `pg_policies` on this same database rather than any request input.
        # Kept out of the f-strings above so each concatenation site is visible.
        if p["qual"]:
            sql = sql + " USING (" + p["qual"] + ")"
        if p["with_check"]:
            sql = sql + " WITH CHECK (" + p["with_check"] + ")"
        op.execute(sql)
        print(f"normalize_tenant_id: restored policy {policy} on {table}")


#: The UUID shape, as a bound parameter rather than a spliced literal.
UUID_REGEX = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _refuse_if_uncastable(conn, raw_table: str, raw_column: str) -> None:
    """Raise, naming the rows, if any value in this column is not a UUID.

    Uses a regex rather than a trial cast because a failed cast inside the transaction
    would abort it, and the point is to report BEFORE changing anything.
    """
    table = _validate_table_name(raw_table)
    column = _validate_table_name(raw_column)
    bad_count = conn.execute(
        sa.text(
            f"SELECT count(*) FROM public.{table} "  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
            f"WHERE {column} IS NOT NULL AND {column} !~* :re"
        ),
        {"re": UUID_REGEX},
    ).scalar()
    if not bad_count:
        return

    sample = conn.execute(
        sa.text(
            f"SELECT DISTINCT {column} FROM public.{table} "  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
            f"WHERE {column} IS NOT NULL AND {column} !~* :re "
            f"LIMIT 5"
        ),
        {"re": UUID_REGEX},
    ).scalars().all()
    raise RuntimeError(
        f"Cannot convert {table}.{column} to uuid: {bad_count} row(s) hold a value that "
        f"is not a UUID. Sample: {sample!r}. NOTHING HAS BEEN ALTERED. Decide what those "
        f"rows mean -- legacy identifier, test fixture, or defect -- and either correct "
        f"or remove them, then re-run this revision. This migration will not guess, "
        f"because silently discarding a tenant id would silently reassign the data it "
        f"owns."
    )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        # SQLite has no native uuid and no ALTER COLUMN TYPE. The generic `sa.Uuid`
        # column renders as CHAR(32) there and the lane creates its tables from the
        # models rather than by altering them, so there is nothing to do.
        print("normalize_tenant_id: non-PostgreSQL dialect, nothing to alter")
        return

    targets = list(_TENANT_COLUMNS) + list(_ID_COLUMNS)

    # Pass 1: look at everything before touching anything, so a refusal leaves the
    # schema untouched rather than half-converted.
    pending = []
    for table, column in targets:
        current = _column_type(conn, table, column)
        if current is None:
            print(f"normalize_tenant_id: {table}.{column} absent; skipping")
            continue
        if current == "uuid":
            print(f"normalize_tenant_id: {table}.{column} is already uuid; skipping")
            continue
        _refuse_if_uncastable(conn, table, column)
        pending.append((table, column))

    # Pass 2: convert. No savepoints and no try/except -- a failure here SHOULD abort
    # the revision. A half-typed schema is worse than an unconverted one, and alembic
    # rolling the whole revision back is the correct outcome.
    #
    # `pending` is empty on a re-run, or on a database where every column is already
    # uuid. That is NOT a reason to return early: pass 3 still has to verify the
    # constraints, which is the thing this revision exists to deliver.
    if not pending:
        print("normalize_tenant_id: all columns already uuid; checking constraints")

    # Row policies referencing these columns block the ALTER, so they come off and go
    # back on. Captured first, for every affected table, so the restore is from observed
    # state rather than from an assumption about which policies exist.
    captured = _capture_policies(conn, {t for t, _c in pending})
    if captured:
        print(f"normalize_tenant_id: temporarily dropping {len(captured)} policy/policies")
        _drop_policies(captured)

    for raw_table, raw_column in pending:
        table = _validate_table_name(raw_table)
        column = _validate_table_name(raw_column)
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ALTER COLUMN {column} TYPE uuid USING {column}::uuid"
        )
        print(f"normalize_tenant_id: {table}.{column} -> uuid")

    if captured:
        _restore_policies(captured)

    if pending:
        print(f"normalize_tenant_id: converted {len(pending)} column(s)")

    # The policies MUST all be back. A column retyped with its policy missing is a table
    # whose row filtering silently disappeared, which is worse than a failed migration.
    if captured:
        restored = conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = ANY(:tables)"
            ),
            {"tables": list({p["tablename"] for p in captured})},
        ).scalar()
        if restored != len(captured):
            raise RuntimeError(
                f"captured {len(captured)} policy/policies before retyping and only "
                f"{restored} are present now. Refusing to continue: these tables would "
                f"be left with row filtering removed."
            )
        print(f"normalize_tenant_id: all {restored} policy/policies verified present")

    # Pass 3: the constraints the type conflict was blocking.
    #
    # This is the point of the whole revision. Until now there was no referential
    # integrity on tenant ownership for orders, positions, fills or dag_tasks -- a row
    # could name a profile that had never existed or had been deleted, and the database
    # would not object.
    #
    # Strict, like pass 2. If a constraint is refused now, the cause is orphaned data
    # rather than a type conflict, and the operator needs to see it: the failure names
    # the constraint, and alembic rolls the revision back so nothing is half-applied.
    # `ON DELETE CASCADE` is kept from the original declarations -- deleting a profile
    # should take its execution rows with it rather than orphan them.
    _create_blocked_constraints(conn)


#: The five `add_foreign_keys` could never create, plus the two it does -- re-checked
#: here so a database that passed through that revision while it was still best-effort
#: ends up with all seven. Literal SQL, for the reason given in add_foreign_keys.
_BLOCKED_CONSTRAINTS = (
    ("fk_fills_tenant_id", "fills",
     "ALTER TABLE fills ADD CONSTRAINT fk_fills_tenant_id "
     "FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE"),
    ("fk_orders_tenant_id", "orders",
     "ALTER TABLE orders ADD CONSTRAINT fk_orders_tenant_id "
     "FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE"),
    ("fk_positions_tenant_id", "positions",
     "ALTER TABLE positions ADD CONSTRAINT fk_positions_tenant_id "
     "FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE"),
    ("fk_dag_tasks_tenant_id", "dag_tasks",
     "ALTER TABLE dag_tasks ADD CONSTRAINT fk_dag_tasks_tenant_id "
     "FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE"),
    ("fk_execution_records_task_id", "execution_records",
     "ALTER TABLE execution_records ADD CONSTRAINT fk_execution_records_task_id "
     "FOREIGN KEY (task_id) REFERENCES dag_tasks(task_id) ON DELETE SET NULL"),
    ("fk_execution_records_order_id", "execution_records",
     "ALTER TABLE execution_records ADD CONSTRAINT fk_execution_records_order_id "
     "FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE SET NULL"),
    ("fk_execution_records_tenant_id", "execution_records",
     "ALTER TABLE execution_records ADD CONSTRAINT fk_execution_records_tenant_id "
     "FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE"),
)


def _create_blocked_constraints(conn) -> None:
    created, present = 0, 0
    for name, table, add_sql in _BLOCKED_CONSTRAINTS:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE c.conname = :n AND t.relname = :t"
            ),
            {"n": name, "t": table},
        ).scalar()
        if exists:
            present += 1
            continue
        op.execute(add_sql)
        print(f"normalize_tenant_id: constraint {name} created")
        created += 1

    total = len(_BLOCKED_CONSTRAINTS)
    print(
        f"normalize_tenant_id: {created} constraint(s) created, {present} already "
        f"present, {created + present}/{total} in place"
    )
    if created + present != total:
        raise RuntimeError(
            f"only {created + present} of {total} foreign keys are in place after "
            f"normalisation; refusing to report success"
        )


def downgrade() -> None:
    """Back to varchar(36).

    `uuid::text` always produces the canonical hyphenated 36-character form, so the type
    change itself is lossless and needs no precheck.

    THE CONSTRAINTS COME OFF FIRST, AND THAT ORDER IS NOT OPTIONAL. A foreign key ties
    the two column types together, so retyping either side while the constraint exists is
    refused -- the downgrade would fail halfway through, having dropped nothing and
    converted some columns. The five this revision created are dropped before anything is
    retyped. The two that belong to `add_foreign_keys` are also dropped here, because
    after this runs their types no longer match either; `add_foreign_keys.downgrade()`
    drops them again harmlessly with IF EXISTS.
    """
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    for raw_name, raw_table, _add_sql in _BLOCKED_CONSTRAINTS:
        name = _validate_table_name(raw_name)
        table = _validate_table_name(raw_table)
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")

    for raw_table, raw_column in list(_ID_COLUMNS) + list(_TENANT_COLUMNS):
        if _column_type(conn, raw_table, raw_column) != "uuid":
            continue
        table = _validate_table_name(raw_table)
        column = _validate_table_name(raw_column)
        op.execute(
            f"ALTER TABLE public.{table} "
            f"ALTER COLUMN {column} TYPE varchar(36) USING {column}::text"
        )
