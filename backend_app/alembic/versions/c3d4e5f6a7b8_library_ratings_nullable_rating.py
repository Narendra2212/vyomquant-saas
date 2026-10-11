"""library_ratings.rating is nullable, because two of its three writers omit it

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-06 06:30:00.000000

THE DEFECT
----------
``public.library_ratings`` is a dual-purpose table. ``backend_app/routers/library.py``
writes it from three endpoints, and only one of them supplies a rating:

  1. ``POST /api/library/{id}/favorite`` (line 4159) sends ``"rating": None``.
     The docstring at line 4146 says so in as many words: "Uses library_ratings table
     with rating=null to track favorites."
  2. ``POST /api/library/{id}/clone`` step 9 (line 2484) sends a verified-clone marker
     with **no** ``rating`` key at all.
  3. ``POST /api/library/{id}/rate`` (line 2614) sends an actual 1..5 rating.

The provisioned column was ``rating smallint NOT NULL`` with no default, so (1) and (2)
were rejected by the database. Measured, not inferred -- ``staging/library_ratings_probe.py``
attempts all three inserts against the real schema and records:

    rating column: is_nullable=NO  default=(none)
      REJECTED  POST /{id}/favorite  (rating = NULL)
                23502 null value in column "rating" ... violates not-null constraint
      REJECTED  POST /{id}/clone     (rating key absent)
                23502 null value in column "rating" ... violates not-null constraint
      ACCEPTED  POST /{id}/rate      (rating = 5)

The two failures present differently, and both are bad:

  * Favouriting raised, was caught by ``except Exception`` at line 4172, and returned
    ``HTTPException(500, "Failed to favorite strategy")``. Every call, for every user.
    ``GET /api/library/favorites`` consequently always returned an empty list.
  * The clone marker's insert is wrapped in ``except _PostgrestAPIError`` and logged as
    non-critical, so cloning SUCCEEDED while silently never recording that it happened.
    ``is_verified_clone`` was therefore false for every row in the table, permanently.

WHY NEITHER TEST SUITE CAUGHT IT
--------------------------------
``tests/test_marketplace_pipeline.py`` asserts the verified-clone marker is written, and
passes. It writes into ``patched_db.stores["library_ratings"]``, a Python list. A list
does not enforce NOT NULL. The same applies to the favourites coverage in
``tests/property/test_tenant_isolation_matrix.py``, which classifies
``GET /api/library/favorites`` as an owner-scoped list without ever reaching Postgres.
This is the exact failure mode that motivated provisioning a real database.

THE FIX, AND WHY IT IS PAIRED WITH AN RLS CHANGE
------------------------------------------------
``rating`` becomes nullable: a review has a rating, a favourite and a clone marker do
not, and three independent places in the tree already document NULL as the marker.

Dropping the constraint on its own would have OPENED a tenant leak rather than closed
one. ``library_ratings`` carried ``POLICY library_ratings_select_authenticated FOR
SELECT TO authenticated USING (true)`` -- row level security ENABLED, predicate constant
TRUE, so no row filter at all. While every row was a public review that was tolerable.
The moment NULL-rating rows can exist, that policy publishes one customer's private
favourites list to every other logged-in customer, and the browser anon key in
``algo22-terminal/src/supabase.js`` reaches PostgREST directly, so it is reachable and
not theoretical.

So ``migrations/hardening/999_privilege_hardening.sql`` section 3b replaces that policy
with ``USING (rating IS NOT NULL OR user_id = auth.uid())`` -- reviews stay public,
everything else is owner-only -- and its third backstop now refuses to finish
provisioning if any per-customer table has a constant-TRUE policy. The two changes are
only correct together; neither should be applied alone.

A CHECK constraint keeps the range honest now that NULL is admissible: a non-null rating
must still be 1..5, which the original ``smallint NOT NULL`` never actually enforced.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RANGE_CHECK = "library_ratings_rating_range"


def _table_exists(conn) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='library_ratings'"
            )
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn):
        # The table lives in archived_migrations/root_migrations/, which the provisioner
        # runs. If it is genuinely absent this revision has nothing to say, and saying
        # nothing is correct -- failing here would block an otherwise valid deployment.
        return

    op.alter_column(
        "library_ratings",
        "rating",
        existing_type=sa.SmallInteger(),
        nullable=True,
        schema="public",
    )

    # Added only if absent so a re-run is a no-op. This one IS conditional on purpose:
    # the constraint is a declarative end state, not a data change, and there is nothing
    # to guess about.
    exists = conn.execute(
        sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": _RANGE_CHECK}
    ).scalar()
    if not exists:
        op.create_check_constraint(
            _RANGE_CHECK,
            "library_ratings",
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            schema="public",
        )


def downgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn):
        return

    op.drop_constraint(_RANGE_CHECK, "library_ratings", schema="public", type_="check")

    # Restoring NOT NULL would destroy every favourite and every verified-clone marker,
    # which are exactly the rows this revision made possible. Refuse instead, with the
    # count, and leave the database as it is. A downgrade that deletes customer data to
    # satisfy a constraint is not a downgrade.
    orphans = conn.execute(
        sa.text("SELECT count(*) FROM public.library_ratings WHERE rating IS NULL")
    ).scalar()
    if orphans:
        raise RuntimeError(
            f"refusing to downgrade: {orphans} library_ratings row(s) have a NULL "
            "rating. Those are favourites and verified-clone markers, and restoring "
            "NOT NULL requires deleting or inventing a rating for every one of them. "
            "Decide what should happen to them explicitly, then re-run."
        )

    op.alter_column(
        "library_ratings",
        "rating",
        existing_type=sa.SmallInteger(),
        nullable=False,
        schema="public",
    )
