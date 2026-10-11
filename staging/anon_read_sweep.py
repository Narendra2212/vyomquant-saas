"""Behavioural sweep: seed a row as the owner, then adopt `anon` and try to read it.

Every other check in this tree reasons about `pg_policies` and
`information_schema.role_table_grants`. This one does not reason at all -- it inserts a
real row, executes `SET LOCAL ROLE anon; SELECT count(*) FROM <table>`, and reports the
number. Everything is done inside a savepoint that is always rolled back, so the
database is unchanged.

Why that is a different question from the inventory checks
---------------------------------------------------------
62 policies in this schema are granted to role `public`, which INCLUDES `anon`. None of
them name `anon` explicitly, so a query filtering on `roles = '{anon}'` reports zero
anon-reachable policies and looks reassuring. The predicates are of the form
`auth.uid() = user_id`, and for `anon` there is no JWT, so `auth.uid()` is NULL and the
comparison is NULL rather than true -- zero rows. That is the conclusion this sweep is
meant to confirm or refute by measurement.

WHY THE SEEDING STEP EXISTS
---------------------------
An earlier revision of this file only counted rows that happened to be present. 44 of
the 49 tables were empty, so `anon` read zero rows from them and the sweep reported no
leak -- having proven nothing at all about those 44. An empty table is silent, not safe.
Seeding first is what turns the sweep into evidence.

Values are synthesised per column type for every NOT NULL column without a default.
Where a foreign key cannot be satisfied the table is reported as NOT PROVEN rather than
counted as a pass, because that distinction is the whole point.

BOTH ROLES
----------
    python staging/anon_read_sweep.py                 # anon, no JWT at all
    python staging/anon_read_sweep.py authenticated   # a logged-in OTHER customer

The second form is the one that matters more. `anon` holds SELECT on 49 tables;
`authenticated` holds it on 79, and a logged-in customer supplies a real `auth.uid()`,
so the policy predicates evaluate to true for *their* rows rather than to NULL for
everything. The sweep seeds a row owned by customer A and then reads it as customer B,
which is cross-tenant isolation measured directly.

Exit 0 -> the role read zero rows from every table where a row could be seeded.
Exit 1 -> the role read at least one row it should not have.
"""

from __future__ import annotations

import sys
import uuid

import psycopg2
import psycopg2.extensions

from dsn import resolve_dsn

GRANTED = """
SELECT DISTINCT g.table_name
FROM information_schema.role_table_grants g
JOIN pg_class c ON c.relname = g.table_name
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
WHERE g.table_schema = 'public'
  AND g.grantee = %s
  AND g.privilege_type = 'SELECT'
  AND c.relkind = 'r'
ORDER BY 1
"""

REQUIRED_COLUMNS = """
SELECT column_name, data_type, udt_name, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = %s
  AND is_nullable = 'NO'
  AND column_default IS NULL
  AND is_generated = 'NEVER'
ORDER BY ordinal_position
"""

# Tables that are public product information by design. `anon` reading these is the
# intended behaviour -- a pricing page and a state-machine lookup are not customer data.
# Listed explicitly so that "expected" is a decision in this file and not a surprise.
INTENTIONALLY_PUBLIC = {
    "plans",
    "plan_features",
    "plan_limits",
    "plan_prices",
    "plan_migration_mapping",
    "marketplace_submission_allowed_transitions",
    "marketplace_subscription_allowed_transitions",
    "paper_order_allowed_transitions",
    "paper_session_allowed_transitions",
    "block_registry_snapshots",
}

#: Columns that identify the owning customer. When a table has one, it is seeded with
#: the probe identity so that a leak would be a leak of a real, attributable row.
OWNER_COLUMNS = (
    "user_id", "tenant_id", "profile_id", "owner_id", "author_id",
    "customer_id", "subscriber_id", "referrer_id", "created_by",
    "creator_id", "seller_id", "account_id",
)


#: Values for columns under a CHECK constraint that a synthesised value cannot satisfy.
#: Each one is a domain fact, so it is written down rather than guessed at. A table still
#: missing an entry is reported NOT PROVEN, never silently passed.
CHECK_OVERRIDES = {
    # CHECK (granted_via = ANY (ARRAY['subscription','ownership']))
    "deployment_permissions": {"granted_via": "ownership"},
    # CHECK (status <> 'active' OR the period columns are set)
    "library_subscriptions": {"status": "cancelled"},
    # CHECK (period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$') and CHECK (amount >= 1)
    "plan_usage_ledger": {"period": "2026-01", "amount": 1},
    # CHECK (to_state = ANY (ARRAY['GENERATED',...,'REJECTED']))
    "order_lifecycle_transitions": {"to_state": "GENERATED"},
    # The paper-trading tables. These matter most for a paper-only launch, so being
    # able to seed them is the difference between measuring isolation and assuming it.
    "paper_orders": {"side": "buy", "order_type": "market"},
    # `paper_fills` needs no entry: its own CHECKs are `price > 0` and `quantity > 0`,
    # which the synthesised numeric 1 satisfies. Its `paper_orders` parent is seeded
    # through the foreign-key walk and picks up that table's overrides there. Listing
    # `side` here instead would try to set a column `paper_fills` does not have.
    "paper_positions": {"side": "LONG"},
    "paper_balance_events": {"cause": "RESET"},
    "paper_equity_snapshots": {"cause": "SESSION_START"},
    "paper_events": {"event_type": "paper_session_started"},
    "copilot_messages": {"role": "user"},
    "waitlist": {"experience_level": "beginner"},
    "training_governance_decisions": {"decision": "ADMITTED"},
    # CHECK (executed_bar_count >= 50), CHECK (total_trades >= 20) and
    # CHECK ((end_date - start_date) + 1 >= 90)
    "marketplace_backtest_evidence": {
        "executed_bar_count": 50, "total_trades": 20,
        "start_date": "2026-01-01", "end_date": "2026-06-01",
    },
    # CHECK (owner_share_minor + platform_fee_minor = amount_minor),
    # CHECK (currency = ANY (ARRAY['USD','INR'])) and
    # CHECK (provider = ANY (ARRAY['stripe','razorpay']))
    "marketplace_settlements": {
        "amount_minor": 100, "owner_share_minor": 70, "platform_fee_minor": 30,
        "currency": "USD", "provider": "stripe",
    },
    # Phase 9 migration 029. The MONEY-06 durable owed-commission ledger. Its NOT NULL
    # columns without a default are provider, provider_payment_id, referred_id,
    # payment_amount, currency and reason_code. Only `currency` needs an override: it is
    # under CHECK (currency ~ '^[A-Z]{3}$'), which the synthesised `probe-xxxxxxxx` string
    # cannot satisfy. The others are fine -- provider/provider_payment_id/reason_code only
    # need `btrim(...) <> ''` (any non-empty probe string passes), payment_amount needs
    # `> 0` (the synthesised numeric 1 passes), and referred_id is a bare uuid with no FK.
    # Added in Phase 10 after this table dropped out of coverage as NOT PROVEN -- a real
    # gap introduced by adding the table in Phase 9 without its seed override, which
    # test_every_readable_table_was_actually_exercised correctly refused to pass.
    "referral_obligations": {"currency": "USD"},
}

#: Tables whose rows are created for a profile by a trigger, so an explicit insert hits
#: a unique constraint. The row is already there, which is all the sweep needs.
TRIGGER_SEEDED = {"referral_codes", "referral_wallets", "referral_profiles"}

ENUM_LABELS = """
SELECT e.enumlabel
FROM pg_enum e
JOIN pg_type t ON t.oid = e.enumtypid
WHERE t.typname = %s
ORDER BY e.enumsortorder
LIMIT 1
"""

FOREIGN_KEYS = """
SELECT att.attname          AS column_name,
       fn.nspname           AS ref_schema,
       fc.relname           AS ref_table,
       fatt.attname         AS ref_column
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


def _enum_first(cur, udt: str):
    cur.execute(ENUM_LABELS, (udt,))
    row = cur.fetchone()
    return row[0] if row else None


def _synth(column: str, data_type: str, udt: str, maxlen, identity: str):
    """A value of the right type for `column`, preferring the probe identity."""
    if column in OWNER_COLUMNS:
        # uuid columns take the identity directly; text ones take its string form.
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
    # text / varchar / char / enum / everything else. Enums will be rejected and the
    # table reported as NOT PROVEN, which is the honest outcome.
    value = f"probe-{uuid.uuid4().hex[:8]}"
    if maxlen and maxlen < len(value):
        return value[:maxlen]
    return value


def _seed(cur, table: str, identity: str, depth: int):
    """Insert one row into `table`, creating parent rows for its foreign keys first.

    Returns nothing; the row is whatever the synthesised payload produced. Depth is
    bounded because the schema contains self-referencing chains (`strategy_versions` ->
    `strategies` -> `profiles`) and an unbounded walk would not terminate on a cycle.
    """
    if depth > 4:
        raise psycopg2.DataError(f"foreign key chain deeper than 4 levels at {table}")

    if table == "profiles":
        # `profiles.id` references `auth.users`, so a profile cannot be synthesised the
        # generic way -- the identity has to exist in the auth schema first. Handled here
        # rather than through the FK walk because `auth` is not seedable generically.
        fresh = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO auth.users (id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (fresh, f"anon-sweep-{fresh[:8]}@example.test"),
        )
        cur.execute("INSERT INTO public.profiles (id) VALUES (%s)", (fresh,))
        return

    cur.execute(REQUIRED_COLUMNS, (table,))
    required = cur.fetchall()

    cur.execute(FOREIGN_KEYS, (table,))
    fks = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    payload: dict[str, object] = {}
    for name, dtype, udt, maxlen in required:
        if name in CHECK_OVERRIDES.get(table, {}):
            payload[name] = CHECK_OVERRIDES[table][name]
            continue
        if name in fks:
            ref_schema, ref_table, ref_column = fks[name]

            # An OWNER column that is ALSO a foreign key takes the probe identity, not
            # "any parent row".
            #
            # `OWNER_COLUMNS` above says the owning column "is seeded with the probe
            # identity so that a leak would be a leak of a real, attributable row", and
            # `_synth` does exactly that -- but `_synth` is only reached for columns that
            # are NOT foreign keys, and this branch runs first. So for every table whose
            # owner column carries an FK, the stated intent did not hold: the row was
            # owned by whichever profile `LIMIT 1` happened to return.
            #
            # That misattribution also produced a false NOT PROVEN. `risk_settings` has
            # `user_id` UNIQUE and an FK to `profiles(id)`, and most profiles on a
            # provisioned database already have a `risk_settings` row, so "any parent row"
            # collided with `risk_settings_user_id_key` and the table dropped out of
            # coverage entirely -- which `test_every_readable_table_was_actually_exercised`
            # correctly refused to accept. The probe identity is created fresh by `main()`
            # for every table, so it owns no child row and cannot collide.
            #
            # Guarded by an existence check: an owner column may reference something other
            # than `profiles`/`auth.users` (the two tables the identity is created in), and
            # there the identity is not a valid parent. Falls back to the original walk.
            if name in OWNER_COLUMNS:
                cur.execute(
                    f'SELECT "{ref_column}" FROM {ref_schema}."{ref_table}" '
                    f'WHERE "{ref_column}"::text = %s LIMIT 1',
                    (identity,),
                )
                owned = cur.fetchone()
                if owned is not None:
                    payload[name] = owned[0]
                    continue

            # `referral_relationships` has CHECK (referrer_id <> referred_id), and both
            # columns point at `profiles`. Taking "any parent row" for both would give
            # the same id twice, so a non-owner FK column is steered away from the owner.
            steer_away = name not in OWNER_COLUMNS and any(
                c in payload for c in OWNER_COLUMNS
            )
            sql = f'SELECT "{ref_column}" FROM {ref_schema}."{ref_table}"'
            params: list[object] = []
            if steer_away:
                sql += f' WHERE "{ref_column}"::text <> %s'
                params.append(str(next(payload[c] for c in OWNER_COLUMNS if c in payload)))
            cur.execute(sql + " LIMIT 1", params)
            row = cur.fetchone()
            if row is None:
                if ref_schema != "public":
                    raise psycopg2.DataError(
                        f"cannot seed {ref_schema}.{ref_table} for {table}.{name}"
                    )
                _seed(cur, ref_table, str(uuid.uuid4()), depth + 1)
                cur.execute(sql + " LIMIT 1", params)
                row = cur.fetchone()
                if row is None:
                    raise psycopg2.DataError(
                        f"no usable parent row in {ref_table} for {table}.{name}"
                    )
            payload[name] = row[0]
            continue
        if dtype == "USER-DEFINED":
            label = _enum_first(cur, udt)
            if label is None:
                raise psycopg2.DataError(f"no label for enum {udt} on {table}.{name}")
            payload[name] = label
            continue
        payload[name] = _synth(name, dtype, udt, maxlen, identity)

    # Overrides may name a nullable column, which would not appear in `required`.
    for name, value in CHECK_OVERRIDES.get(table, {}).items():
        payload.setdefault(name, value)

    if payload:
        cols = ", ".join(f'"{c}"' for c in payload)
        binds = ", ".join(["%s"] * len(payload))
        cur.execute(
            f'INSERT INTO public."{table}" ({cols}) VALUES ({binds})',
            list(payload.values()),
        )
    else:
        cur.execute(f'INSERT INTO public."{table}" DEFAULT VALUES')


def main() -> int:
    role = sys.argv[1] if len(sys.argv) > 1 else "anon"
    if role not in ("anon", "authenticated"):
        sys.exit("usage: anon_read_sweep.py [anon|authenticated]")

    conn = psycopg2.connect(resolve_dsn())
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)

    # Customer A owns the seeded row. Customer B is the reader. For the `anon` sweep
    # there is no reader identity at all, which is the point of that variant.
    identity = str(uuid.uuid4())
    reader = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(GRANTED, (role,))
        tables = [r[0] for r in cur.fetchall()]

    leaked: list[tuple[str, int, int]] = []
    blocked: list[tuple[str, int]] = []
    expected: list[tuple[str, int]] = []
    unproven: list[tuple[str, str]] = []

    for table in tables:
        with conn.cursor() as cur:
            cur.execute(REQUIRED_COLUMNS, (table,))
            required = cur.fetchall()

            cur.execute("SAVEPOINT s")
            try:
                # The owning identity must exist before a row can reference it.
                cur.execute(
                    "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (identity, f"anon-sweep-{identity[:8]}@example.test"),
                )
                cur.execute(
                    "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING",
                    (identity,),
                )

                if table == "profiles" or table in TRIGGER_SEEDED:
                    # The profile insert above already created the row, directly or
                    # through the trigger that provisions a referral code and wallet.
                    seeded = 1
                else:
                    _seed(cur, table, identity, depth=0)
                    seeded = 1

                cur.execute(f'SELECT count(*) FROM public."{table}"')
                owner_count = cur.fetchone()[0]

                if role == "authenticated":
                    # A second, fully registered customer -- the realistic attacker. The
                    # claim is what PostgREST installs from a verified JWT, and
                    # `auth.uid()` reads it, so this is the same input a real request has.
                    cur.execute(
                        "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (reader, f"sweep-reader-{reader[:8]}@example.test"),
                    )
                    cur.execute(
                        "INSERT INTO public.profiles (id) VALUES (%s) "
                        "ON CONFLICT DO NOTHING",
                        (reader,),
                    )
                    cur.execute(
                        "SELECT set_config('request.jwt.claim.sub', %s, true)", (reader,)
                    )
                else:
                    cur.execute("SELECT set_config('request.jwt.claim.sub', '', true)")
                cur.execute(f"SET LOCAL ROLE {role}")
                cur.execute(f'SELECT count(*) FROM public."{table}"')
                anon_count = cur.fetchone()[0]

                if role == "authenticated":
                    # The reader's own profile row is legitimately visible, and for
                    # `profiles` it is the only row the count can contain beyond the
                    # seeded one. Subtracting it keeps the measurement about the OTHER
                    # customer's data rather than about the reader's own.
                    if table in ("profiles", "referral_codes", "referral_wallets",
                                 "referral_profiles"):
                        cur.execute(
                            f'SELECT count(*) FROM public."{table}" '
                            f'WHERE user_id = %s' if table != "profiles"
                            else 'SELECT count(*) FROM public.profiles WHERE id = %s',
                            (reader,),
                        )
                        anon_count -= cur.fetchone()[0]
            except psycopg2.Error as exc:
                cur.execute("ROLLBACK TO SAVEPOINT s")
                unproven.append((table, str(exc).splitlines()[0][:110]))
                continue
            cur.execute("ROLLBACK TO SAVEPOINT s")

        assert seeded
        if table in INTENTIONALLY_PUBLIC:
            expected.append((table, anon_count))
        elif anon_count > 0:
            leaked.append((table, anon_count, owner_count))
        else:
            blocked.append((table, owner_count))

    conn.close()

    who = "anon (no JWT)" if role == "anon" else "a DIFFERENT logged-in customer"
    print("=" * 78)
    print(f"BEHAVIOURAL READ SWEEP as {role.upper()} -- seed a row, adopt the role, count")
    print("=" * 78)
    print(f"{len(tables)} table(s) in public grant SELECT to {role}.")
    print(f"Reader identity: {who}.")
    print("Every insert and read happens inside a savepoint that is rolled back.")
    print("-" * 78)
    print(f"A. LEAKED -- a seeded row owned by another customer was readable by {role}")
    print("-" * 78)
    if leaked:
        for table, a, o in leaked:
            print(f"  {table:<48} {role} saw {a} of {o} row(s)")
    else:
        print("  (none)")
    print("-" * 78)
    print(f"B. BLOCKED -- row present, {role} saw zero ({len(blocked)} table(s))")
    print("-" * 78)
    for table, o in blocked:
        print(f"  {table:<48} 0 of {o} row(s)")
    print("-" * 78)
    print(f"C. INTENTIONALLY PUBLIC ({len(expected)} table(s))")
    print("-" * 78)
    for table, a in expected:
        print(f"  {table:<48} {role} saw {a} row(s) -- product information")
    print("-" * 78)
    print(f"D. NOT PROVEN -- no row could be seeded ({len(unproven)} table(s))")
    print("-" * 78)
    for table, msg in unproven:
        print(f"  {table:<34} {msg}")
    print("-" * 78)
    print(
        f"SUMMARY: {len(leaked)} leaked, {len(blocked)} proven blocked, "
        f"{len(expected)} intentionally public, {len(unproven)} not proven"
    )
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
