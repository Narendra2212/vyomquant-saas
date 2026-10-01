"""merge the referrals and rls lineages into one head

Revision ID: merge_heads
Revises: ('6f1b3d9c8a7e', 'implement_rls_policies')
Create Date: 2026-08-20 00:00:00.000000

WHY THIS REVISION EXISTS
------------------------
``d97ffff9c3bb`` (execution_schema_rebuild) forked. Two revisions name it as
their ``down_revision`` and neither knows about the other::

    4ef23035a692  (baseline)
      |
      d97ffff9c3bb  (execution_schema_rebuild)
        |-- e88f9911b5a2  (consolidate_full_schema)
        |     |-- 6f1b3d9c8a7e  (add_referrals)          <- head 1
        |
        '-- add_foreign_keys
              |-- implement_rls_policies                 <- head 2

With two heads, ``alembic upgrade head`` does not pick one or run both - it
refuses outright with "Multiple head revisions are present for given argument
'head'". So the revision history was not merely untidy; it was unrunnable by
its own primary command, in either direction, for anyone who tried.

This revision joins the two lineages and does nothing else. ``upgrade()`` and
``downgrade()`` are empty on purpose: a merge exists to make the graph
single-headed, and any DDL placed here would execute against a database whose
state neither branch described.

WHY A MERGE AND NOT A DELETION
------------------------------
Deleting one of the two heads would have made the graph single-headed too, and
it would have been wrong. Both lineages descend from ``d97ffff9c3bb``, which is
the revision the production database is actually stamped at, so both are
unapplied-but-reachable future work rather than dead files. Dropping either
would discard a revision the stamp still points below.

WHAT THIS CHANGES IN PRODUCTION: NOTHING
----------------------------------------
Alembic is not applied in this deployment. Production's ``alembic_version``
holds exactly one row, ``d97ffff9c3bb``, and no workflow under
``.github/workflows/``, no Dockerfile and no script in ``scripts/`` or any
``*.sh`` runs ``alembic upgrade``. The live schema is maintained by the numbered
SQL set in ``backend_app/migrations/`` (001..018) and ``migrations/``, applied by
hand through ``scripts/apply_migrations.py``.

This file therefore repairs the revision graph and moves no database. It is
also NOT a licence to run ``alembic upgrade head`` now: ``e88f9911b5a2`` still
begins with ``op.create_table('library_strategies')`` and
``public.library_strategies`` already exists in production, so that revision
would abort on a ``duplicate_table`` the moment it ran. The three tables it was
supposed to create are declared instead by
``backend_app/migrations/018_copilot_and_waitlist_tables.sql``, which is applied
and which is idempotent.

``tests/test_schema_table_reference_drift.py::TestAlembicDagHasOneHead`` asserts
the single head, that this revision joins exactly the two recorded heads, and
that both of its functions stay empty.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'merge_heads'
down_revision: Union[str, Sequence[str], None] = ('6f1b3d9c8a7e', 'implement_rls_policies')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op. See the module docstring: this revision only joins two lineages."""


def downgrade() -> None:
    """No-op. Splitting back into two heads is what this revision undoes."""
