"""Which columns identify the customer that owns a row -- derived, not hand-written.

WHY THIS MODULE EXISTS
----------------------
Every exposure check in this project asks the same question first: "does this table
carry a column that makes a row belong to one specific customer?" Until now the answer
came from a hand-written tuple of column names, repeated in four places. That list has
been wrong twice, and both times it caused a table to go unexamined:

  * `creator_id` was missing, so `creator_analytics` -- creator_id plus total_earnings --
    was not checked on the first sweep. RLS was off and GRANT ALL was intact.
  * `purchaser_id` was missing, so `marketplace_settlements`'
    `settlements_purchaser_read` policy was reported as "not recognisably an ownership
    test" when it is exactly that. A false positive this time, but the same root cause:
    a list a human maintains is a list a human forgets.

The schema already knows the answer. A column that REFERENCES `profiles(id)` or
`auth.users(id)` is, by construction, a column naming a customer. So the list is derived
from `pg_constraint` and the hand-written names are kept only as a FLOOR -- for columns
that hold a customer identity without a declared foreign key, which this schema has
plenty of (`invoices.user_id` is `varchar(36)` with no FK at all).

Union of the two, never just one. Derivation alone would miss the unconstrained columns;
the hand-written list alone misses whatever nobody thought of.
"""

from __future__ import annotations

#: The floor. Columns known to identify a customer even where no foreign key declares
#: it. Kept because this schema has per-customer columns with no FK -- the billing
#: tables' `user_id` is `varchar(36)` referencing nothing -- and those are precisely the
#: ones a derivation cannot see.
#:
#: Add to this freely. It can only widen what gets checked, never narrow it.
BASELINE_OWNER_COLUMNS: tuple[str, ...] = (
    "user_id",
    "tenant_id",
    "profile_id",
    "owner_id",
    "author_id",
    "customer_id",
    "subscriber_id",
    "referrer_id",
    "referred_id",
    "created_by",
    "creator_id",
    "seller_id",
    "purchaser_id",
    "account_id",
    # Found by the derivation below, which is the mechanism working as intended: these
    # three have declared foreign keys to a customer identity and no human had listed
    # them.
    "referred_by_user_id",
    "reviewed_by",
    "top_creator_id",
)

#: `profiles` is per-customer by construction -- one row per customer, keyed by the
#: customer's own id -- and no column name on it says so. Named explicitly because every
#: check that asks "does this table carry an ownership column?" would otherwise answer no
#: for the single most sensitive table in the schema.
ALWAYS_PER_CUSTOMER_TABLES: tuple[str, ...] = ("profiles",)

#: Excluded from the derived set. `profiles.id` references `auth.users(id)`, so the
#: derivation legitimately reports `id` -- but adding a bare `id` to a GLOBAL list of
#: ownership column names makes every table in the schema match, because nearly all of
#: them have an `id`. Verified that `profiles` is the ONLY table whose `id` carries such
#: a foreign key, so excluding the name costs nothing and `ALWAYS_PER_CUSTOMER_TABLES`
#: covers the one real case.
_DERIVATION_EXCLUSIONS: frozenset[str] = frozenset({"id"})

#: Tables whose identity columns reference a customer. Used to decide whether a foreign
#: key makes its column an ownership column.
_IDENTITY_TABLES = ("profiles", "users")

_DERIVE_SQL = """
SELECT DISTINCT att.attname
FROM pg_constraint con
JOIN pg_class child         ON child.oid = con.conrelid
JOIN pg_namespace child_ns  ON child_ns.oid = child.relnamespace
JOIN pg_class parent        ON parent.oid = con.confrelid
CROSS JOIN LATERAL unnest(con.conkey) AS k(attnum)
JOIN pg_attribute att       ON att.attrelid = con.conrelid AND att.attnum = k.attnum
WHERE con.contype = 'f'
  AND child_ns.nspname = 'public'
  AND parent.relname = ANY(%s)
ORDER BY 1
"""


def derive_owner_columns(conn) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (effective, derived_only).

    `effective` is the union of the baseline and everything the schema's foreign keys
    reveal -- the list every exposure check should actually use.

    `derived_only` is what the derivation found that the baseline did not mention. It is
    returned separately so a caller can REPORT it: a column appearing there means the
    hand-written floor is out of date, and saying so is how the list stops silently
    rotting.
    """
    with conn.cursor() as cur:
        cur.execute(_DERIVE_SQL, (list(_IDENTITY_TABLES),))
        derived = {r[0] for r in cur.fetchall()} - _DERIVATION_EXCLUSIONS

    effective = tuple(sorted(set(BASELINE_OWNER_COLUMNS) | derived))
    derived_only = tuple(sorted(derived - set(BASELINE_OWNER_COLUMNS)))
    return effective, derived_only
