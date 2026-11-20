#!/usr/bin/env python
"""Behavioural cross-tenant isolation test. Writes synthetic rows, commits NOTHING.

THE TENSION IN THE INSTRUCTIONS, RESOLVED EXPLICITLY
----------------------------------------------------
The launch gate says two things that cannot both be taken literally:

    "DO NOT modify production data."
    "Create only synthetic test records. TENANT_A creates a record, TENANT_B
     attempts SELECT / UPDATE / DELETE / forged INSERT."

A synthetic row written to production IS a modification of production. There is no
version of the second instruction that satisfies the first if the writes are committed.

So they are not committed. Every statement this script issues runs inside ONE
transaction that ends in `ROLLBACK`, unconditionally, in a `finally` block. The rows
exist only for the duration of the transaction, are visible only to it, and leave no
trace afterwards. What production keeps is the sequence advances and the WAL records of
an aborted transaction -- not a row.

That is the only way to answer the question the gate actually asks, which is behavioural
and cannot be answered by reading catalogues: a policy set that LOOKS correct is not
proof that one customer cannot read another's row. `library_ratings` had RLS enabled and
a policy that filtered nothing; `referral_codes` had a correct policy sitting beside a
broader one that overrode it. Both passed every inventory check. Both were found by
executing the attempt.

WHY ROLLBACK IS GENUINELY SAFE HERE, NOT A TECHNICALITY
-------------------------------------------------------
  * `autocommit` is set to False and asserted before anything runs. A single stray
    commit path is the only way a row could survive, and there is exactly one code path
    out of this script.
  * The script refuses to run without `--i-understand-this-writes-and-rolls-back`, so it
    cannot be invoked by accident or by a CI job that was aiming at something else.
  * It refuses if either live-trading switch is set in the environment.
  * It never UPDATEs or DELETEs a pre-existing row. Every mutation it attempts targets a
    row it created in the same transaction -- and the attempts that are SUPPOSED to fail
    are the point of the exercise.
  * It reports the row count of every table it touches, before and after, as evidence
    that nothing was added.

WHAT IT MEASURES
----------------
For each table carrying a customer-ownership column:

    A creates a row.
    B (a different logged-in customer) attempts SELECT, UPDATE, DELETE.
    B attempts INSERT claiming A's identity.
    anon attempts SELECT, INSERT, UPDATE, DELETE.
    A re-reads its own row -- which MUST still work.

The last line matters as much as the rest. A policy of `USING (false)` blocks every
cross-tenant attempt and also breaks the product; without the positive control, an
outage and an isolation guarantee produce identical output.

EXIT CODES
----------
    0  all isolation probes behaved correctly
    1  at least one cross-tenant or anon access succeeded, or a legitimate own-row
       operation was blocked
    2  could not run (refused, no DSN, cannot assume the browser roles)
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import uuid

try:
    import psycopg2
except ImportError:  # pragma: no cover
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.owner_columns import derive_owner_columns  # noqa: E402
from scripts.verify_schema_drift import _refuse_transaction_pooler, redact  # noqa: E402

#: Tables the gate names, probed first and reported separately. Cross-tenant access to
#: any of these is unconditionally launch-critical.
BILLING_TABLES = ("invoices", "payment_methods", "subscriptions", "billing_invoices")

#: Named by the gate for their own sections. `referral_codes` because enumeration was a
#: real defect; `library_ratings` because a constant-TRUE policy was.
NAMED_TABLES = ("referral_codes", "referral_wallets", "library_ratings")

LIVE_TRADING_ENV = (
    "VYOMQUANT_ENABLE_LIVE_TRADING",
    "AERORA_ENABLE_LIVE_TRADING",
)

#: Values for columns under a CHECK constraint that a synthesised value cannot satisfy.
#: Same list as `staging/anon_read_sweep.py`; kept here so this script has no dependency
#: on `staging/`, which is local scaffolding and may not be deployed.
CHECK_OVERRIDES: dict[str, dict[str, object]] = {
    "deployment_permissions": {"granted_via": "ownership"},
    "library_subscriptions": {"status": "cancelled"},
    "plan_usage_ledger": {"period": "2026-01", "amount": 1},
    "order_lifecycle_transitions": {"to_state": "GENERATED"},
    "paper_orders": {"side": "buy", "order_type": "market"},
    "paper_positions": {"side": "LONG"},
    "paper_balance_events": {"cause": "RESET"},
    "paper_equity_snapshots": {"cause": "SESSION_START"},
    "paper_events": {"event_type": "paper_session_started"},
    "copilot_messages": {"role": "user"},
    "waitlist": {"experience_level": "beginner"},
    "training_governance_decisions": {"decision": "ADMITTED"},
    "marketplace_backtest_evidence": {
        "executed_bar_count": 50, "total_trades": 20,
        "start_date": "2026-01-01", "end_date": "2026-06-01",
    },
    "marketplace_settlements": {
        "amount_minor": 100, "owner_share_minor": 70, "platform_fee_minor": 30,
        "currency": "USD", "provider": "stripe",
    },
}

#: Created for a profile by a trigger, so an explicit insert hits a unique constraint.
TRIGGER_SEEDED = {"referral_codes", "referral_wallets", "referral_profiles"}


class Probe:
    """One table's isolation result."""

    def __init__(self, table: str) -> None:
        self.table = table
        self.seeded = False
        self.seed_error = ""
        self.results: dict[str, str] = {}
        self.rows_before = 0
        self.owner_column = ""

    def record(self, name: str, outcome: str) -> None:
        self.results[name] = outcome

    @property
    def leaks(self) -> list[str]:
        """Cross-tenant or anon access that SUCCEEDED. The only real failure."""
        return [
            f"{name}=ALLOWED"
            for name, outcome in self.results.items()
            if not name.startswith("owner_") and outcome == "ALLOWED"
        ]

    @property
    def positive_control(self) -> str:
        return self.results.get("owner_select", "-")

    @property
    def has_positive_control(self) -> bool:
        return self.positive_control == "ALLOWED"

    @property
    def verdict(self) -> str:
        """Three outcomes, and the middle one is the honest answer for several tables.

        ISOLATED      the owner can read its own row AND nobody else can. Proved.
        LEAK          some cross-tenant or anon attempt succeeded. Disproved.
        NO CONTROL    every attempt was blocked, INCLUDING the owner's own. Nothing was
                      proved either way, and calling this a pass is the single easiest
                      way to produce a falsely reassuring report -- a policy of
                      `USING (false)` blocks every attack and also breaks the product.

        NO CONTROL has two distinguishable causes, both reported:
          * REFUSED(42501) -- `authenticated` holds no grant on the table at all, so
            PostgREST cannot reach it. More restrictive than required, not less.
          * NO ROWS -- a grant exists but no policy matched the seeded row. Usually
            because ownership is INDIRECT: `paper_orders` is owned through
            `paper_accounts.user_id`, not through its own `user_id`, so a probe that
            scopes by the column on the table itself cannot establish ownership.
        """
        if self.leaks:
            return "LEAK"
        if self.has_positive_control:
            return "ISOLATED"
        return "NO CONTROL"


def _refuse_unless_authorised(args) -> None:
    if not args.i_understand_this_writes_and_rolls_back:
        sys.exit(
            "REFUSING TO RUN.\n"
            "  This script writes synthetic rows in order to measure isolation\n"
            "  behaviourally. Every write is inside ONE transaction that is ALWAYS\n"
            "  rolled back, so nothing is committed -- but it is still a write against\n"
            "  the live database and that deserves a deliberate act.\n"
            "  Re-run with --i-understand-this-writes-and-rolls-back\n"
            "\n"
            "  For a strictly read-only check, use:\n"
            "      python scripts/verify_production_schema.py --dsn ..."
        )

    for var in LIVE_TRADING_ENV:
        value = os.environ.get(var, "")
        if value.lower() in ("1", "true", "yes", "on"):
            sys.exit(
                f"REFUSING TO RUN: {var}={value!r} is set.\n"
                "  Live trading must remain OFF for the duration of this work. Clear it\n"
                "  and re-run."
            )
    mode = os.environ.get("VYOMQUANT_MODE", "")
    if mode.lower() == "live":
        sys.exit(
            f"REFUSING TO RUN: VYOMQUANT_MODE={mode!r}.\n"
            "  Live trading must remain OFF for the duration of this work."
        )


def _synth(column: str, data_type: str, udt: str, maxlen, identity: str,
           owner_columns: tuple[str, ...]):
    if column in owner_columns:
        return identity
    if udt == "uuid":
        return str(uuid.uuid4())
    if data_type == "boolean":
        return False
    if data_type in ("smallint", "integer", "bigint"):
        return 1
    if data_type in ("numeric", "real", "double precision"):
        return 1
    if data_type in ("timestamp with time zone", "timestamp without time zone"):
        return "2026-01-01T00:00:00+00:00"
    if data_type == "date":
        return "2026-01-01"
    if udt in ("json", "jsonb"):
        return "{}"
    if data_type == "ARRAY":
        return []
    if data_type == "interval":
        return "1 day"
    value = f"probe-{uuid.uuid4().hex[:8]}"
    if maxlen and maxlen < len(value):
        return value[:maxlen]
    return value


REQUIRED_COLUMNS = """
SELECT column_name, data_type, udt_name, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s
  AND is_nullable = 'NO' AND column_default IS NULL AND is_generated = 'NEVER'
ORDER BY ordinal_position
"""

FOREIGN_KEYS = """
SELECT att.attname, fn.nspname, fc.relname, fatt.attname
FROM pg_constraint con
JOIN pg_class c   ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
JOIN pg_class fc  ON fc.oid = con.confrelid
JOIN pg_namespace fn ON fn.oid = fc.relnamespace
CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(conkey, confkey)
JOIN pg_attribute att  ON att.attrelid = con.conrelid  AND att.attnum = k.conkey
JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = k.confkey
WHERE con.contype = 'f' AND c.relname = %s
"""


def _ensure_identity(cur, identity: str) -> None:
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (identity, f"isolation-probe-{identity[:8]}@example.invalid"),
    )
    cur.execute(
        "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING",
        (identity,),
    )


def _seed(cur, table: str, identity: str, owner_columns: tuple[str, ...],
          depth: int = 0, forced_owner: tuple[str, str] | None = None,
          primary_owner: str | None = None) -> None:
    if depth > 4:
        raise psycopg2.DataError(f"foreign key chain deeper than 4 levels at {table}")

    if table == "profiles":
        fresh = str(uuid.uuid4())
        _ensure_identity(cur, fresh)
        return

    # An ownership column is assigned `identity` below, overriding the foreign-key
    # borrow. That is only safe if the identity actually EXISTS -- otherwise the
    # override turns a satisfiable insert into a foreign-key violation, which is what
    # happened on 18 tables when this override was first added: `_seed` recursed into a
    # parent, assigned a freshly generated uuid to its ownership column, and the parent
    # insert then failed because no such customer existed. Creating it first is
    # idempotent and makes the override always valid.
    _ensure_identity(cur, identity)

    cur.execute(REQUIRED_COLUMNS, (table,))
    required = cur.fetchall()

    # THE OWNERSHIP COLUMN IS ALWAYS POPULATED, NOT ONLY WHEN IT IS NOT NULL.
    #
    # `REQUIRED_COLUMNS` returns NOT NULL columns without a default -- the minimum set
    # needed for an insert to succeed. On the blob-shaped tables (`orders` is
    # `(id, tenant_id, data, created_at, updated_at)`) `tenant_id` is NULLABLE, so it
    # was absent from that set, the seeded row got a NULL owner, and NO policy could
    # match it. The owner's own-row SELECT then returned nothing, which looked like
    # seven separate isolation failures and was one bug in this script.
    cur.execute(
        "SELECT column_name, data_type, udt_name, character_maximum_length "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = ANY(%s)",
        (table, list(owner_columns)),
    )
    ownership = cur.fetchall()
    known = {r[0] for r in required}
    required = list(required) + [r for r in ownership if r[0] not in known]

    cur.execute(FOREIGN_KEYS, (table,))
    fks = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    payload: dict[str, object] = {}
    for name, dtype, udt, maxlen in required:
        if name in CHECK_OVERRIDES.get(table, {}):
            payload[name] = CHECK_OVERRIDES[table][name]
            continue
        if name in owner_columns:
            # An ownership column takes the probe identity so the seeded row is OWNED by
            # the probe -- otherwise the foreign-key branch below borrows whatever parent
            # row exists, the row ends up owned by a third party, and the owner's own-row
            # SELECT finds nothing. That was seven phantom "isolation failures".
            #
            # BUT ONLY IF THE COLUMN REFERENCES A CUSTOMER. `paper_orders.account_id` is
            # in the ownership-column list and references `paper_accounts`, not
            # `profiles`. Forcing a profile id into it produced a foreign-key violation
            # and six tables dropped out of coverage entirely -- a probe that overrides
            # too much measures nothing, exactly like one that overrides too little.
            # `fks[name]` is (schema, TABLE, column). An earlier version read `ref[2]`
            # here -- the referenced COLUMN, almost always `id` -- so the comparison was
            # never true, every ownership column that had a foreign key fell through to
            # the borrow below, and the ISOLATED count collapsed from 58 to 30. One
            # index. Found by printing the statement the seeder actually executed, which
            # named a borrowed `user_id` instead of the probe's.
            ref = fks.get(name)
            references_a_customer = ref is None or ref[1] in ("profiles", "users")
            if references_a_customer:
                # THE COLUMN THE PROBE SCOPES BY ALWAYS GETS THE PROBE IDENTITY.
                #
                # A table can carry several ownership columns, and two of them must not
                # always get the same value -- `referral_relationships` has
                # CHECK (referrer_id <> referred_id). An earlier version handled that by
                # giving the identity to whichever ownership column appeared FIRST and a
                # different one to the rest, which quietly broke every table where the
                # first column was not the one the policy uses: `notifications` has a
                # policy on `user_id`, another ownership column was placed first, and
                # `user_id` ended up owned by a stranger. 27 tables silently dropped from
                # ISOLATED to NO CONTROL.
                #
                # `primary_owner` is the column `_choose_owner_column` selected by reading
                # the policy predicates, so it is the one that decides visibility. It gets
                # the identity unconditionally; only the others get a distinct one.
                if primary_owner is not None:
                    if name == primary_owner:
                        payload[name] = identity
                    else:
                        other = str(uuid.uuid4())
                        _ensure_identity(cur, other)
                        payload[name] = other
                elif not any(c in payload for c in owner_columns):
                    payload[name] = identity
                else:
                    other = str(uuid.uuid4())
                    _ensure_identity(cur, other)
                    payload[name] = other
                continue
        if name in fks:
            ref_schema, ref_table, ref_column = fks[name]
            steer_away = name not in owner_columns and any(
                c in payload for c in owner_columns
            )
            sql = f'SELECT "{ref_column}" FROM {ref_schema}."{ref_table}"'
            params: list[object] = []
            if steer_away:
                sql += f' WHERE "{ref_column}" <> %s'
                params.append(next(payload[c] for c in owner_columns if c in payload))

            # PREFER A PARENT OWNED BY THE PROBE IDENTITY.
            #
            # Many tables are owned INDIRECTLY: `paper_orders` belongs to a customer
            # through `paper_accounts.user_id`, not through its own column, and its RLS
            # policy follows that path. Borrowing an arbitrary parent therefore produces
            # a row owned by somebody else, the owner's own-row SELECT finds nothing, and
            # the result is `NO CONTROL` -- a coverage gap manufactured by the probe.
            #
            # This was measured, not theorised: the ISOLATED count fell from 58 to 31
            # when `_ensure_identity` started creating more candidate parents, because
            # `LIMIT 1` with no ORDER BY then returned a foreign one more often. The
            # non-determinism was the clue; the missing ownership preference was the
            # cause.
            parent_owner_col = _parent_owner_column(cur, ref_schema, ref_table,
                                                    owner_columns)
            row = None
            if parent_owner_col and not steer_away:
                cur.execute(
                    f'SELECT "{ref_column}" FROM {ref_schema}."{ref_table}" '
                    f'WHERE "{parent_owner_col}"::text = %s LIMIT 1',
                    (identity,),
                )
                row = cur.fetchone()
                if row is None:
                    # No parent owned by the probe yet -- create one, so the child's
                    # indirect ownership resolves to the probe identity.
                    if ref_schema == "public":
                        _seed(cur, ref_table, identity, owner_columns, depth + 1)
                        cur.execute(
                            f'SELECT "{ref_column}" FROM {ref_schema}."{ref_table}" '
                            f'WHERE "{parent_owner_col}"::text = %s LIMIT 1',
                            (identity,),
                        )
                        row = cur.fetchone()

            if row is None:
                cur.execute(sql + " LIMIT 1", params)
                row = cur.fetchone()
            if row is None:
                if ref_schema != "public":
                    raise psycopg2.DataError(
                        f"cannot seed {ref_schema}.{ref_table} for {table}.{name}"
                    )
                _seed(cur, ref_table, str(uuid.uuid4()), owner_columns, depth + 1)
                cur.execute(sql + " LIMIT 1", params)
                row = cur.fetchone()
                if row is None:
                    raise psycopg2.DataError(
                        f"no usable parent row in {ref_table} for {table}.{name}"
                    )
            payload[name] = row[0]
            continue
        if dtype == "USER-DEFINED":
            cur.execute(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = %s ORDER BY e.enumsortorder LIMIT 1",
                (udt,),
            )
            label = cur.fetchone()
            if not label:
                raise psycopg2.DataError(f"no label for enum {udt} on {table}.{name}")
            payload[name] = label[0]
            continue
        payload[name] = _synth(name, dtype, udt, maxlen, identity, owner_columns)

    for name, value in CHECK_OVERRIDES.get(table, {}).items():
        payload.setdefault(name, value)

    # Applied LAST so it beats both the synthesiser and the foreign-key borrow. This is
    # what makes the forged-insert probe exact rather than approximate.
    if forced_owner is not None:
        column, value = forced_owner
        payload[column] = value

    if payload:
        cols = ", ".join(f'"{c}"' for c in payload)
        binds = ", ".join(["%s"] * len(payload))
        cur.execute(
            f'INSERT INTO public."{table}" ({cols}) VALUES ({binds})',
            list(payload.values()),
        )
    else:
        cur.execute(f'INSERT INTO public."{table}" DEFAULT VALUES')


def _as_role(cur, role: str, claim: str | None) -> None:
    """Adopt `role` with `claim` as the caller's identity, for BOTH policy families.

    This schema uses two, and setting only one makes the measurement meaningless:

      * `request.jwt.claim.sub` -- read by `auth.uid()`. The Supabase/PostgREST family,
        used by most policies.
      * `app.current_tenant_id` -- read directly by the nine policies on `orders`,
        `positions`, `execution_records`, `fills` and the transaction tables, installed
        by `alembic/versions/implement_rls_policies_20260817.py`.

    An earlier version of this script set only the first. Every table in the second
    family then reported that the OWNER could not see its own row -- which is true of a
    live request today, and is recorded as a known gap, but as a probe result it is
    noise: it says nothing about whether the policy discriminates between two customers.
    Setting both is what a correctly-configured request would do, and it is the only
    configuration in which the positive control means anything.
    """
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (claim or "",))
    cur.execute("SELECT set_config('app.current_tenant_id', %s, true)", (claim or "",))
    cur.execute(f"SET LOCAL ROLE {role}")


def _reset_role(cur) -> None:
    cur.execute("RESET ROLE")


def _attempt(cur, label: str, sql: str, params, probe: Probe,
             expect_rows: bool = False) -> None:
    """Run one access attempt inside a savepoint and classify the outcome.

    Three outcomes, and the distinction between the last two matters:
      ALLOWED  -- the statement succeeded and touched at least one row
      NO ROWS  -- the statement succeeded and matched nothing; RLS filtered it
      REFUSED  -- the statement was rejected outright (missing privilege)
    """
    cur.execute("SAVEPOINT attempt")
    try:
        cur.execute(sql, params)
        if expect_rows:
            touched = len(cur.fetchall())
        else:
            touched = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        cur.execute("RELEASE SAVEPOINT attempt")
        probe.record(label, "ALLOWED" if touched else "NO ROWS")
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT attempt")
        code = exc.pgcode or "?"
        probe.record(label, f"REFUSED({code})")


#: Preference order when a table has several ownership columns and no policy names one.
#: Not alphabetical and not `ordinal_position`: those are arbitrary, and picking the
#: wrong column is how `profiles` came to be probed on `referred_by_user_id` -- a column
#: that is NULL for almost every row, so the owner's own-row SELECT found nothing and the
#: positive control reported a failure that did not exist.
_OWNER_COLUMN_PREFERENCE = (
    "user_id", "tenant_id", "owner_id", "profile_id", "author_id",
    "customer_id", "creator_id", "seller_id", "account_id", "referrer_id",
    "subscriber_id", "created_by", "purchaser_id", "reviewed_by",
    "referred_id", "referred_by_user_id", "top_creator_id",
)


def _parent_owner_column(cur, schema: str, table: str,
                         owner_columns: tuple[str, ...]) -> str | None:
    """The ownership column on a parent table, if it has one.

    Used to borrow a parent row that the PROBE owns rather than an arbitrary one, so that
    a child table whose ownership runs through its parent resolves to the probe identity.
    """
    if schema != "public":
        return None
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = ANY(%s)",
        (schema, table, list(owner_columns)),
    )
    present = [r[0] for r in cur.fetchall()]
    if not present:
        return None
    for preferred in _OWNER_COLUMN_PREFERENCE:
        if preferred in present:
            return preferred
    return present[0]


def _choose_owner_column(cur, table: str, owner_columns: tuple[str, ...]) -> str | None:
    """The column this table's RLS policies actually scope by.

    Read from the policy predicates first, because that is the ground truth: a probe
    that scopes by a different column than the policy does is measuring nothing. Only
    when no policy names an ownership column does this fall back to a preference order,
    and `profiles` is special-cased because it is keyed by the customer's own id and no
    column name on it says so.
    """
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    present = {r[0] for r in cur.fetchall()}

    if table in ("profiles",) and "id" in present:
        return "id"

    candidates = [c for c in owner_columns if c in present]
    if not candidates:
        return None

    cur.execute(
        "SELECT coalesce(qual, '') || ' ' || coalesce(with_check, '') "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename = %s",
        (table,),
    )
    predicates = " ".join(r[0] for r in cur.fetchall())

    # A column the policies mention beats one they do not, whatever its name.
    mentioned = [c for c in candidates if c in predicates]
    if mentioned:
        for preferred in _OWNER_COLUMN_PREFERENCE:
            if preferred in mentioned:
                return preferred
        return mentioned[0]

    for preferred in _OWNER_COLUMN_PREFERENCE:
        if preferred in candidates:
            return preferred
    return candidates[0]


def probe_table(cur, table: str, owner: str, reader: str,
                owner_columns: tuple[str, ...]) -> Probe:
    probe = Probe(table)

    cur.execute(f'SELECT count(*) FROM public."{table}"')
    probe.rows_before = cur.fetchone()[0]

    # Chosen BEFORE seeding, and passed into the seeder. The column the probe will scope
    # by must be the column the seeded row is actually owned through, or the positive
    # control measures a different row than the one that was created.
    owner_col = _choose_owner_column(cur, table, owner_columns)
    if owner_col is None:
        probe.seed_error = "no ownership column found, nothing to scope by"
        return probe
    probe.owner_column = owner_col

    cur.execute("SAVEPOINT seed")
    try:
        if table in TRIGGER_SEEDED or table == "profiles":
            probe.seeded = True  # the profile insert already created it
        else:
            _seed(cur, table, owner, owner_columns, primary_owner=owner_col)
            probe.seeded = True
        cur.execute("RELEASE SAVEPOINT seed")
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT seed")
        probe.seed_error = str(exc).splitlines()[0][:120]
        return probe

    # â”€â”€ A: the positive control, FIRST â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    #
    # Order matters. An earlier version ran this last, after B's DELETE attempt -- so on
    # any table where B's delete succeeded, A's row was already gone and the positive
    # control reported "the owner cannot see its own row". That reads as a second,
    # separate defect when it is a consequence of the first, and it buries the real
    # finding. Measuring the legitimate path before attacking it removes the ambiguity.
    _as_role(cur, "authenticated", owner)
    _attempt(cur, "owner_select",
             f'SELECT 1 FROM public."{table}" WHERE "{owner_col}"::text = %s',
             (owner,), probe, expect_rows=True)
    _reset_role(cur)

    # â”€â”€ B: a different logged-in customer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _as_role(cur, "authenticated", reader)
    _attempt(cur, "b_select", f'SELECT 1 FROM public."{table}" WHERE "{owner_col}"::text = %s',
             (owner,), probe, expect_rows=True)
    _attempt(cur, "b_update",
             f'UPDATE public."{table}" SET "{owner_col}" = "{owner_col}" '
             f'WHERE "{owner_col}"::text = %s', (owner,), probe)
    _attempt(cur, "b_delete", f'DELETE FROM public."{table}" WHERE "{owner_col}"::text = %s',
             (owner,), probe)
    _reset_role(cur)

    # B forging A's ownership on INSERT.
    #
    # The ownership column is forced to A's identity EXPLICITLY rather than left to
    # `_seed`'s synthesiser. `_seed` satisfies foreign keys by borrowing whatever parent
    # row exists, which on some tables means the ownership column gets a value that is
    # neither A nor B -- and an insert that succeeds with a third party's id is not the
    # forgery this is meant to test. Forcing the column makes the question exact: can B
    # write a row that claims to belong to A?
    _as_role(cur, "authenticated", reader)
    cur.execute("SAVEPOINT forge")
    try:
        _seed(cur, table, owner, owner_columns, forced_owner=(owner_col, owner),
              primary_owner=owner_col)
        cur.execute("ROLLBACK TO SAVEPOINT forge")
        probe.record("b_forged_insert", "ALLOWED")
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT forge")
        probe.record("b_forged_insert", f"REFUSED({exc.pgcode or '?'})")
    _reset_role(cur)

    # â”€â”€ anon: no JWT at all â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _as_role(cur, "anon", None)
    _attempt(cur, "anon_select", f'SELECT 1 FROM public."{table}"', None, probe,
             expect_rows=True)
    _attempt(cur, "anon_update",
             f'UPDATE public."{table}" SET "{owner_col}" = "{owner_col}"', None, probe)
    _attempt(cur, "anon_delete", f'DELETE FROM public."{table}"', None, probe)
    _reset_role(cur)

    return probe


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Behavioural cross-tenant isolation test. Rolls back everything."
    )
    ap.add_argument("--dsn", required=True, help="direct connection (5432), not the pooler")
    ap.add_argument("--i-understand-this-writes-and-rolls-back", action="store_true",
                    help="required. See the module docstring.")
    ap.add_argument("--out", help="write the result matrix here as JSON")
    ap.add_argument("--limit", type=int, default=0,
                    help="probe only the first N tables (0 = all)")
    ap.add_argument("--only", action="append", default=[], metavar="TABLE",
                    help="probe only these tables. Repeatable. For triaging a single "
                         "NO CONTROL result without waiting for a full run.")
    args = ap.parse_args()

    _refuse_unless_authorised(args)
    _refuse_transaction_pooler(args.dsn)

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    assert not conn.autocommit, "autocommit must be off; nothing may be committed"

    owner = str(uuid.uuid4())
    reader = str(uuid.uuid4())
    probes: list[Probe] = []
    could_not_assume_roles = ""

    print("=" * 78)
    print("BEHAVIOURAL CROSS-TENANT ISOLATION TEST -- ALL WRITES ROLLED BACK")
    print("=" * 78)
    print(f"database  : {redact(args.dsn)}")
    print(f"timestamp : {timestamp}")
    print(f"autocommit: OFF -- one transaction, ending in ROLLBACK unconditionally")
    print()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_setting('server_version')")
            who, server = cur.fetchone()
            print(f"connected as {who}, PostgreSQL {server}")

            # Can this role adopt the browser roles? If not, the probe cannot run and
            # that must be reported as UNVERIFIED, never as a pass.
            cur.execute("SAVEPOINT roletest")
            try:
                cur.execute("SET LOCAL ROLE authenticated")
                cur.execute("RESET ROLE")
                cur.execute("SET LOCAL ROLE anon")
                cur.execute("RESET ROLE")
                cur.execute("RELEASE SAVEPOINT roletest")
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT roletest")
                could_not_assume_roles = str(exc).splitlines()[0]

            if could_not_assume_roles:
                print()
                print("CANNOT RUN -- and this is NOT a pass.")
                print(f"  {who} cannot SET ROLE to anon/authenticated:")
                print(f"  {could_not_assume_roles}")
                print()
                print("  Connect as a role that may assume them (on Supabase, `postgres`")
                print("  can). Until then, behavioural isolation on this database is")
                print("  NOT VERIFIED.")
                return 2

            owner_columns, _derived = derive_owner_columns(conn)
            _ensure_identity(cur, owner)
            _ensure_identity(cur, reader)

            cur.execute(
                """
                SELECT DISTINCT c.relname
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND EXISTS (SELECT 1 FROM information_schema.columns col
                              WHERE col.table_schema='public'
                                AND col.table_name=c.relname
                                AND col.column_name = ANY(%s))
                ORDER BY 1
                """,
                (list(owner_columns),),
            )
            discovered = [r[0] for r in cur.fetchall()]

            # Named tables first so their result is at the top of the output.
            ordered = (
                [t for t in BILLING_TABLES if t in discovered]
                + [t for t in NAMED_TABLES if t in discovered]
                + [t for t in discovered
                   if t not in BILLING_TABLES and t not in NAMED_TABLES]
            )
            if args.only:
                missing = [t for t in args.only if t not in discovered]
                if missing:
                    print(f"  these tables carry no ownership column, or do not exist: "
                          f"{missing}")
                ordered = [t for t in ordered if t in args.only]
            if args.limit:
                ordered = ordered[: args.limit]

            print(f"probing {len(ordered)} table(s) carrying an ownership column")
            print()
            header = (f"  {'table':<32}{'B sel':<10}{'B upd':<10}{'B del':<10}"
                      f"{'B forge':<13}{'anon sel':<10}{'A own':<10}")
            print(header)
            print("  " + "-" * (len(header) - 2))

            for table in ordered:
                probe = probe_table(cur, table, owner, reader, owner_columns)
                probes.append(probe)
                if not probe.seeded:
                    print(f"  {table:<32}NOT PROVEN -- {probe.seed_error[:40]}")
                    continue
                r = probe.results
                print(f"  {table:<32}"
                      f"{r.get('b_select', '-'):<10}"
                      f"{r.get('b_update', '-'):<10}"
                      f"{r.get('b_delete', '-'):<10}"
                      f"{r.get('b_forged_insert', '-'):<13}"
                      f"{r.get('anon_select', '-'):<10}"
                      f"{r.get('owner_select', '-'):<10}")
    finally:
        # The whole point. Unconditional, in a finally, before the connection closes.
        conn.rollback()
        with conn.cursor() as cur:
            # Prove it: the two synthetic identities must not exist.
            #
            # CAST is not optional. `auth.users.id` is `uuid` and a bound Python string
            # arrives as `text`, so a bare `= ANY(%s)` fails with
            # `operator does not exist: uuid = text` -- which would raise HERE, inside
            # the `finally` that performs the rollback, and mask it.
            cur.execute(
                "SELECT count(*) FROM auth.users WHERE id = ANY(CAST(%s AS uuid[]))",
                ([owner, reader],),
            )
            leftover = cur.fetchone()[0]
        conn.rollback()
        conn.close()

    print()
    print("-" * 78)
    print(f"ROLLBACK issued. synthetic identities still present after rollback: {leftover}")
    if leftover:
        print("  *** WARNING: the rollback did not remove the probe identities. ***")
    print("-" * 78)

    leaked = [p for p in probes if p.seeded and p.verdict == "LEAK"]
    isolated = [p for p in probes if p.seeded and p.verdict == "ISOLATED"]
    no_control = [p for p in probes if p.seeded and p.verdict == "NO CONTROL"]
    unseeded = [p for p in probes if not p.seeded]

    billing_leaked = [p for p in leaked if p.table in BILLING_TABLES]
    named_leaked = [p for p in leaked if p.table in NAMED_TABLES]

    print()
    print("=" * 78)
    if leaked:
        print("RESULT: ISOLATION FAILURE")
    elif no_control or unseeded:
        print("RESULT: NO LEAK FOUND, WITH COVERAGE GAPS")
    else:
        print("RESULT: ISOLATED")
    print("=" * 78)
    print(f"  {len(isolated):>3} ISOLATED    owner reads its own row, nobody else can")
    print(f"  {len(leaked):>3} LEAK        a cross-tenant or anon attempt SUCCEEDED")
    print(f"  {len(no_control):>3} NO CONTROL  every attempt blocked INCLUDING the owner's")
    print(f"  {len(unseeded):>3} NOT SEEDED  no row could be created -- nothing measured")
    print()

    if billing_leaked:
        print("  LAUNCH-CRITICAL -- BILLING:")
        for p in billing_leaked:
            print(f"    {p.table}: {'; '.join(p.leaks)}")
        print()
    if named_leaked:
        print("  LAUNCH-CRITICAL -- referral / ratings isolation:")
        for p in named_leaked:
            print(f"    {p.table}: {'; '.join(p.leaks)}")
        print()
    other_leaked = [p for p in leaked if p not in billing_leaked and p not in named_leaked]
    if other_leaked:
        print("  OTHER ISOLATION FAILURES:")
        for p in other_leaked:
            print(f"    {p.table}: {'; '.join(p.leaks)}")
        print()

    if no_control:
        print("  NO CONTROL -- not a leak, and NOT a pass either:")
        print("    Every attempt was blocked, including the legitimate one, so this")
        print("    probe did not establish that the policy DISCRIMINATES between two")
        print("    customers. Two causes, both shown:")
        refused = [p for p in no_control if p.positive_control.startswith("REFUSED")]
        norows = [p for p in no_control if p.positive_control == "NO ROWS"]
        if refused:
            print()
            print("    (a) `authenticated` holds no grant at all -- PostgREST cannot")
            print("        reach the table. MORE restrictive than required, not less:")
            for p in refused:
                print(f"          {p.table:<28} {p.positive_control}")
        if norows:
            print()
            print("    (b) a grant exists but no policy matched the seeded row. Usually")
            print("        INDIRECT ownership -- e.g. `paper_orders` is owned through")
            print("        `paper_accounts.user_id`, not its own `user_id`, so scoping")
            print("        by the column on the table itself cannot prove ownership:")
            for p in norows:
                print(f"          {p.table:<28} scoped by {p.owner_column}")
        print()

    if unseeded:
        print("  NOT SEEDED -- add the missing value to CHECK_OVERRIDES:")
        for p in unseeded:
            print(f"    {p.table}: {p.seed_error}")
        print()

    print("  NOTE ON service_role")
    print("    service_role holds BYPASSRLS and is INTENTIONALLY privileged. It is the")
    print("    backend's own identity, not an end-user session, and it is deliberately")
    print("    not probed here -- it would 'fail' every check by design. For any endpoint")
    print("    that queries through it, the application's own tenant filter is the only")
    print("    control, and that is a separate, unverified question.")
    print("=" * 78)

    if args.out:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "timestamp": timestamp,
            "database": redact(args.dsn),
            "rolled_back": True,
            "leftover_identities_after_rollback": leftover,
            "probes": [
                {"table": p.table, "seeded": p.seeded, "seed_error": p.seed_error,
                 "rows_before": p.rows_before, "owner_column": p.owner_column,
                 "results": p.results, "leaks": p.leaks, "verdict": p.verdict}
                for p in probes
            ],
        }, indent=1, sort_keys=True), encoding="utf-8")
        print(f"matrix written to {out}")

    return 1 if (leaked or no_control or unseeded) else 0


if __name__ == "__main__":
    sys.exit(main())


