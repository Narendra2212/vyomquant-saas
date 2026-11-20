"""resolve the three competing declarations of `positions` onto one canonical shape

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-06 04:00:00.000000

THE DEFECT
----------
Three different declarations of ``public.positions`` existed, and the one the database
actually had was the one almost nothing used:

  1. ``alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py`` -- the BLOB shape:
     ``(id varchar(36) PK, tenant_id, data json, created_at, updated_at)``. This is what
     was provisioned.
  2. ``core/position_model.py:361`` -- the RICH shape: ``position_id`` primary key plus
     ``strategy_id``, ``exchange_id``, ``symbol``, ``side``, ``size``,
     ``avg_entry_price``, ``unrealized_pnl``, ``realized_pnl``, ``status``, ``closed_at``.
  3. ``backend/state_service.py:333`` -- a THIRD shape, nested inside a function, with
     ``user_id``, ``quantity``, ``available_quantity``, ``locked_quantity``,
     ``entry_price``, ``mark_price``, ``liquidation_price``.

So any query through the rich model failed outright:

    ProgrammingError: column positions.position_id does not exist

Measured by executing one, not inferred. ``tests/test_orm_models_match_provisioned_schema.py``
carried this as a strict xfail.

WHY THE RICH SHAPE IS THE CANONICAL ONE
---------------------------------------
Not because it is nicer, but because it is the one the application is built on. Six
modules use it, and together they are the entire position lifecycle:

    backend/position_engine.py      open / update / reduce / flip / close
    backend/pnl_engine.py           realised and unrealised PnL
    backend/fee_engine.py           tenant lookup from a position
    backend/metrics_system.py       per-tenant PnL aggregation
    backend/event_router.py         position close on fill events
    core/consistency_checker.py     RECONCILIATION against the venue

The blob shape has no reachable writer at all: the only code that writes
``positions.data`` is ``backend/transactional_execution_manager.py``, which is
constructed in exactly one place -- ``backend/exchange_websocket_listener.py:343`` -- and
nothing starts that listener (``tests/test_reconciliation_worker_claims_match_behaviour.py``
asserts that). Declaration 3 is nested inside a conditional block in a module that
maintains its own async session factory.

So this revision reshapes the table to match ``core/position_model.py``, and that model
becomes the single source of truth. Declaration 3 is left in place and reported as a
follow-up: it is unreachable from the provisioned schema either way, and deleting code
inside another subsystem's conditional is not this migration's business.

WHY IT REFUSES RATHER THAN CONVERTS WHEN BLOB ROWS EXIST
--------------------------------------------------------
``data`` is free-form JSON. Mapping it onto ``symbol``/``side``/``size``/
``avg_entry_price`` means guessing which keys mean what, and a wrong guess here produces
a position of the wrong size or the wrong direction -- a financial record that is
confidently incorrect. That is worse than a failed migration.

So the precheck counts rows with a non-null ``data`` payload, and if it finds any it
raises with the count and a sample, having altered nothing. An empty table -- which is
what the unreachable writer implies, and what a fresh provision gives -- converts
cleanly. This is the same discipline as ``a1b2c3d4e5f6``: migrations may fail loudly,
they may not guess about money.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: The rich shape, as `core/position_model.py` declares it. Spelled out rather than
#: imported from the model: a migration that reads the current ORM would silently change
#: meaning the next time someone edits the model, which is how declarations drift apart
#: in the first place.
_COLUMNS = (
    ("strategy_id", "varchar", False, "''"),
    ("exchange_id", "varchar", True, None),
    ("symbol", "varchar", False, "''"),
    ("size", "varchar", False, "'0'"),
    ("avg_entry_price", "varchar", False, "'0'"),
    ("unrealized_pnl", "varchar", False, "'0.0'"),
    ("realized_pnl", "varchar", False, "'0.0'"),
    ("closed_at", "timestamptz", True, None),
)


def _validate_table_name(table_name: str) -> str:
    """Refuse anything that is not a bare SQL identifier. Raises; never sanitises.

    Named to match `backend/transactional_execution_manager.py:37` deliberately:
    `tests/test_bandit_sast_gate_is_green.py` treats a call to `_validate_table_name` as
    the one way an interpolated expression may reach a statement, and that test caught
    this file. The gate is right -- column names and type names below are interpolated
    because DDL identifiers cannot be bound parameters, and "they come from a module
    constant" is an argument, not a constraint. This makes it a constraint.
    """
    s = str(table_name)
    if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
        return s
    raise ValueError(f"not a SQL identifier: {s!r}")


def _has_column(conn, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='positions' "
                "  AND column_name=:c"
            ),
            {"c": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        print("positions_canonical_shape: non-PostgreSQL dialect, nothing to do")
        return

    if _has_column(conn, "position_id"):
        print("positions_canonical_shape: position_id already present; nothing to do")
        return

    # ── precheck: refuse to guess about existing blob rows ───────────────────
    if _has_column(conn, "data"):
        blob_rows = conn.execute(
            sa.text(
                "SELECT count(*) FROM public.positions "
                "WHERE data IS NOT NULL AND data::text NOT IN ('null', '{}', '')"
            )
        ).scalar()
        if blob_rows:
            sample = conn.execute(
                sa.text(
                    "SELECT id, data::text FROM public.positions "
                    "WHERE data IS NOT NULL AND data::text NOT IN ('null','{}','') "
                    "LIMIT 3"
                )
            ).fetchall()
            raise RuntimeError(
                f"public.positions holds {blob_rows} row(s) with a JSON `data` payload. "
                f"This revision reshapes the table to the columns core/position_model.py "
                f"declares, and mapping free-form JSON onto symbol/side/size/"
                f"avg_entry_price would mean guessing which keys mean what -- a wrong "
                f"guess writes a position with the wrong size or the wrong direction. "
                f"NOTHING HAS BEEN ALTERED. Sample: {sample!r}. Map these rows "
                f"explicitly, or confirm they are disposable and clear them, then re-run."
            )

    # ── the two enum types the model needs ──────────────────────────────────
    # `create_type=False` on the model means it expects them to exist already.
    op.execute(
        "DO $$ BEGIN "
        "  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'positionside') THEN "
        "    CREATE TYPE positionside AS ENUM ('LONG', 'SHORT'); "
        "  END IF; "
        "  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'positionstatus') THEN "
        "    CREATE TYPE positionstatus AS ENUM "
        "      ('OPEN', 'PARTIAL', 'CLOSED', 'LIQUIDATED'); "
        "  END IF; "
        "END $$;"
    )

    # ── position_id, backfilled from the existing identifier ────────────────
    # `id` and `position_id` are the same thing under two names, so an existing row keeps
    # its identity rather than being given a new one.
    op.execute("ALTER TABLE public.positions ADD COLUMN position_id varchar")
    op.execute("UPDATE public.positions SET position_id = id WHERE position_id IS NULL")
    op.execute("ALTER TABLE public.positions ALTER COLUMN position_id SET NOT NULL")

    # ── swap the primary key ────────────────────────────────────────────────
    # The FK added by a1b2c3d4e5f6 is on `tenant_id` and is unaffected. Nothing
    # references `positions.id`, checked via pg_constraint before writing this.
    op.execute(
        "DO $$ "
        "DECLARE pk_name text; "
        "BEGIN "
        "  SELECT c.conname INTO pk_name FROM pg_constraint c "
        "    JOIN pg_class t ON t.oid = c.conrelid "
        "    JOIN pg_namespace n ON n.oid = t.relnamespace "
        "   WHERE n.nspname='public' AND t.relname='positions' AND c.contype='p'; "
        "  IF pk_name IS NOT NULL THEN "
        "    EXECUTE format('ALTER TABLE public.positions DROP CONSTRAINT %I', pk_name); "
        "  END IF; "
        "END $$;"
    )
    op.execute(
        "ALTER TABLE public.positions "
        "ADD CONSTRAINT positions_pkey PRIMARY KEY (position_id)"
    )

    # `id` stays, nullable, as the legacy name. Dropping it would break the blob-shaped
    # writer if it is ever reached, and an unused nullable column costs nothing.
    op.execute("ALTER TABLE public.positions ALTER COLUMN id DROP NOT NULL")

    # ── the remaining columns ───────────────────────────────────────────────
    for raw_name, raw_type, nullable, default in _COLUMNS:
        if _has_column(conn, raw_name):
            continue
        # Both go through the validator, not just the column name: `sqltype` is
        # interpolated into the same statement and is equally an identifier.
        name = _validate_table_name(raw_name)
        sqltype = _validate_table_name(raw_type)
        op.execute(f"ALTER TABLE public.positions ADD COLUMN {name} {sqltype}")
        if not nullable:
            # Defaulted, then NOT NULL, so an existing row does not block the change.
            #
            # `default` is a VALUE (`''`, `'0'`, `'0.0'`), not an identifier, so it must
            # not be interpolated at all -- it is bound. The previous version of this
            # line spliced it into the statement text, which is the habit the SAST gate
            # exists to break even when the value happens to come from a constant.
            op.execute(
                sa.text(
                    f"UPDATE public.positions SET {name} = :v WHERE {name} IS NULL"  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
                ).bindparams(v=default.strip("'"))
            )
            op.execute(
                f"ALTER TABLE public.positions ALTER COLUMN {name} SET NOT NULL"
            )

    if not _has_column(conn, "side"):
        op.execute("ALTER TABLE public.positions ADD COLUMN side positionside")
        op.execute("UPDATE public.positions SET side = 'LONG' WHERE side IS NULL")
        op.execute("ALTER TABLE public.positions ALTER COLUMN side SET NOT NULL")

    if not _has_column(conn, "status"):
        op.execute("ALTER TABLE public.positions ADD COLUMN status positionstatus")
        op.execute("UPDATE public.positions SET status = 'OPEN' WHERE status IS NULL")
        op.execute("ALTER TABLE public.positions ALTER COLUMN status SET NOT NULL")

    # created_at / updated_at exist from the blob shape but as `timestamp without time
    # zone`; the model declares them timezone-aware, and a naive timestamp on a financial
    # record is an ambiguity nobody resolves correctly later.
    for column in ("created_at", "updated_at"):
        current = conn.execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='positions' "
                "  AND column_name=:c"
            ),
            {"c": column},
        ).scalar()
        if current == "timestamp without time zone":
            op.execute(
                f"ALTER TABLE public.positions ALTER COLUMN {column} "
                f"TYPE timestamptz USING {column} AT TIME ZONE 'UTC'"
            )
        op.execute(
            f"ALTER TABLE public.positions ALTER COLUMN {column} SET DEFAULT now()"
        )
        op.execute(
            f"UPDATE public.positions SET {column} = now() WHERE {column} IS NULL"  # nosec B608 - identifiers only, each passed through _validate_table_name (^[A-Za-z0-9_]{1,64}$) or taken from a module constant; values are bound params
        )
        op.execute(f"ALTER TABLE public.positions ALTER COLUMN {column} SET NOT NULL")

    # ── tenant_id is NOT NULL in the model, and was nullable in the blob shape ──
    #
    # It matters more here than it looks. `tenant_id` is the tenant isolation column:
    # every RLS policy on this table compares against it, and a NULL compares equal to
    # nothing, so a row with a null tenant is a position no tenant can see and no policy
    # can protect -- invisible to its owner and to any cross-tenant check written to
    # catch it. It also carries `fk_positions_tenant_id` as of a1b2c3d4e5f6, which NULL
    # satisfies trivially.
    #
    # Refused rather than defaulted if any null row exists: inventing an owner for a
    # financial record is exactly the kind of guess this revision's precheck exists to
    # prevent.
    null_tenants = conn.execute(
        sa.text("SELECT count(*) FROM public.positions WHERE tenant_id IS NULL")
    ).scalar()
    if null_tenants:
        raise RuntimeError(
            f"public.positions holds {null_tenants} row(s) with a NULL tenant_id. Such a "
            f"row is invisible to every RLS policy on the table and belongs to no "
            f"account. Assign them an owner or remove them, then re-run; this migration "
            f"will not pick one."
        )
    op.execute("ALTER TABLE public.positions ALTER COLUMN tenant_id SET NOT NULL")

    # ── the indexes the model declares ──────────────────────────────────────
    for column in ("tenant_id", "strategy_id", "exchange_id"):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_positions_{column} "
            f"ON public.positions ({column})"
        )

    print("positions_canonical_shape: positions now matches core/position_model.py")


def downgrade() -> None:
    """Back to the blob shape.

    `id` was kept throughout, so restoring the old primary key needs no backfill. The
    rich columns are dropped; any data in them is lost, which is why this direction is
    only sensible on a database where the rich shape was never used.
    """
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return

    op.execute("UPDATE public.positions SET id = position_id WHERE id IS NULL")
    op.execute("ALTER TABLE public.positions DROP CONSTRAINT IF EXISTS positions_pkey")
    op.execute("ALTER TABLE public.positions ALTER COLUMN id SET NOT NULL")
    op.execute(
        "ALTER TABLE public.positions ADD CONSTRAINT positions_pkey PRIMARY KEY (id)"
    )
    for raw_name, *_rest in _COLUMNS:
        name = _validate_table_name(raw_name)
        op.execute(f"ALTER TABLE public.positions DROP COLUMN IF EXISTS {name}")
    op.execute("ALTER TABLE public.positions DROP COLUMN IF EXISTS side")
    op.execute("ALTER TABLE public.positions DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE public.positions DROP COLUMN IF EXISTS position_id")
