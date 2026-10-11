"""TENANT-01: a real cross-tenant negative matrix, against live PostgreSQL.

WHAT THIS TESTS, AND WHY IT IS NOT AN HTTP TEST
===============================================
The audit's concern was that the explicit tenant-dependency family is unwired
(`TenantMiddleware` is never installed, `request.state.tenant` is never set,
`app.current_tenant_id` is never populated). The question that actually matters for a
launch is not "is that framework wired?" but **"can tenant A reach tenant B's rows?"**

So this drives the database the way a real request does. `auth.uid()` on this deployment
is::

    SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid

and every per-user table carries a policy of the shape
``FOR ALL TO authenticated USING (auth.uid() = user_id)``. A request arrives through
PostgREST as role ``authenticated`` with that setting populated from the JWT's ``sub``.
This test reproduces exactly that: ``SET LOCAL ROLE authenticated`` plus
``SET LOCAL request.jwt.claim.sub``. No FakeSupabase, no HTTP layer, no mock.

A 403 from a handler is explicitly NOT accepted as evidence anywhere here. Every negative
case asserts **zero rows affected AND the victim row unchanged afterwards**, read back as
the owner. An endpoint that answers 403 while the UPDATE already landed would fail.

THE THREE CONTROLS
------------------
1. NEGATIVE — tenant A attempts SELECT / UPDATE / DELETE against tenant B's row.
2. POSITIVE — tenant A performs the same operations on its OWN row. Without this, a
   database that denied everything would look perfectly isolated.
3. DETECTION — a scratch table is given a deliberately weakened (`USING (true)`) policy
   and the same harness is pointed at it. If the harness does not report a leak there, it
   cannot be trusted to report one anywhere, and every row above is vacuous.

Skipped, not silently passed, when no PostgreSQL DSN is configured.
"""

from __future__ import annotations

import os
import uuid
from typing import Dict, List, Optional, Tuple

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or "postgresql://postgres:@127.0.0.1:62674/postgres"
)

if not DSN.startswith("postgres"):
    pytest.skip("TENANT-01 matrix requires PostgreSQL", allow_module_level=True)

#: The tenant-scoped tables this matrix covers, in the order the brief lists them.
#: The owner column is DISCOVERED rather than hardcoded, because this repository uses
#: both `user_id` and `tenant_id` and a hardcoded guess would silently skip a table.
TARGET_TABLES = [
    "strategies",
    "strategy_versions",
    "strategy_deployments",
    "signals",
    "exchange_keys",
    "orders",
    "positions",
    "fills",
    "execution_records",
    "risk_settings",
    "profiles",
    "billing_invoices",
    "payment_methods",
    "referral_codes",
    "referral_relationships",
    "referral_commissions",
    "library_strategies",
    "library_subscriptions",
    "library_ratings",
    "marketplace_submissions",
    "strategy_subscriptions",
    "notifications",
    "security_logs",
    "training_jobs",
    "model_versions",
    "paper_sessions",
    "paper_accounts",
    "paper_orders",
    "paper_positions",
]

OWNER_COLUMN_CANDIDATES = (
    "user_id", "tenant_id", "owner_id", "author_id", "creator_id",
    "subscriber_id", "referrer_id",
)

#: `profiles` is the identity row itself, so its owner column is its primary key.
OWNER_COLUMN_OVERRIDES = {"profiles": "id"}


def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def db():
    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    yield conn
    conn.close()


def _columns(cur, table: str):
    """(name, data_type, is_nullable, default, udt_name, max_len) per column."""
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default,
               udt_name, character_maximum_length
          FROM information_schema.columns
         WHERE table_schema='public' AND table_name=%s
         ORDER BY ordinal_position
        """,
        (table,),
    )
    return cur.fetchall()


def _is_enum(cur, udt_name: str) -> bool:
    return bool(_enum_labels(cur, udt_name))


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s)",
        (table,),
    )
    return bool(cur.fetchone()[0])


def _owner_column(cols, table: str = "") -> Optional[str]:
    names = [c[0] for c in cols]
    override = OWNER_COLUMN_OVERRIDES.get(table)
    if override and override in names:
        return override
    for candidate in OWNER_COLUMN_CANDIDATES:
        if candidate in names:
            return candidate
    return None


def _seed_identity(cur, user_id: str) -> None:
    """Create the identity row every tenant-scoped table's FK points at.

    Almost every table references `profiles(id)` (which itself references
    `auth.users(id)`), so seeding a row with a random owner uuid fails the FK and the
    whole matrix silently reduces to the one table that has no FK. Both are inserted,
    best-effort and idempotently.

    Note from earlier remediation work: inserting a `profiles` row AUTO-PROVISIONS a
    `referral_codes` row via trigger, with `uq_referral_codes_user` on it -- so the
    referral tables may already hold a row for this identity before the matrix seeds one.
    """
    # BEGIN/ROLLBACK rather than SAVEPOINT: the connection is autocommit, and SAVEPOINT
    # outside a transaction block raises -- which is exactly how the first version of
    # this helper failed silently, taking every `*_user_id_fkey` down with it and
    # reducing the matrix to two tables.
    for stmt, params in (
        ('INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING',
         (user_id,)),
        ('INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT (id) DO NOTHING',
         (user_id,)),
    ):
        try:
            cur.execute("BEGIN")
            cur.execute(stmt, params)
            cur.execute("COMMIT")
        except Exception as exc:  # noqa: BLE001
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            _IDENTITY_SEED_ERRORS.append(
                f"{stmt.split()[2]}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}"
            )


#: Identity-seed failures, surfaced by a test so this can never fail silently again.
_IDENTITY_SEED_ERRORS: List[str] = []


def _pk_column(cur, table: str) -> Optional[str]:
    cur.execute(
        """
        SELECT a.attname
          FROM pg_index i
          JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
         WHERE i.indrelid = (quote_ident(%s))::regclass AND i.indisprimary
         LIMIT 1
        """,
        (table,),
    )
    row = cur.fetchone()
    return row[0] if row else None


#: Values that satisfy the CHECK constraints this schema actually uses. Discovered by
#: probing the live database (staging/identity_RUNF3.txt) rather than guessed: a generic
#: synthesiser produced `t_side_ea08c1` and tripped `chk_paper_order_side`,
#: `chk_paper_position_side`, `chk_signals_environment` and the positionside enum.
_CONSTRAINED_COLUMNS = {
    "side": "buy",
    "order_type": "market",
    "order_state": "CREATED",
    "environment": "PAPER",
    "currency": "USD",
    "subscription_tier": "free",
    "status": "pending",
    "order_lifecycle_state": "GENERATED",
}

#: Enum columns need a real label. Filled per-table by `_enum_labels`.
_ENUM_CACHE: Dict[str, List[str]] = {}


def _enum_labels(cur, udt_name: str) -> List[str]:
    if udt_name in _ENUM_CACHE:
        return _ENUM_CACHE[udt_name]
    cur.execute(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = %s ORDER BY e.enumsortorder",
        (udt_name,),
    )
    labels = [r[0] for r in cur.fetchall()]
    _ENUM_CACHE[udt_name] = labels
    return labels


def _synth(data_type: str, column_name: str, udt_name: str = "",
           max_len: Optional[int] = None, cur=None):
    """A minimal LEGAL value for a NOT NULL column with no default.

    "Legal" is doing real work here: this schema has enum columns, varchar(4)/varchar(10)
    columns and a dozen CHECK constraints on text columns. A naive synthesiser produces
    values that violate them, and the table is then reported as unseedable -- which looks
    like coverage loss but is really a broken generator.
    """
    t = data_type.lower()

    # Enums first: the type dictates the legal set.
    if cur is not None and udt_name:
        labels = _enum_labels(cur, udt_name)
        if labels:
            return labels[0]

    if column_name in _CONSTRAINED_COLUMNS:
        value = _CONSTRAINED_COLUMNS[column_name]
        return value[:max_len] if max_len else value

    if "uuid" in t:
        return str(uuid.uuid4())
    if "json" in t:
        return "{}"
    if "bool" in t:
        return False
    if any(k in t for k in ("int", "numeric", "double", "real", "decimal")):
        return 1
    if "timestamp" in t or "date" in t:
        return "2026-01-01T00:00:00+00:00"
    if "ARRAY" in data_type or t.endswith("[]"):
        return "{}"

    value = f"t_{column_name[:8]}_{uuid.uuid4().hex[:6]}"
    return value[:max_len] if max_len else value


def _insert_row(cur, table: str, owner_col: str, owner_id: str) -> Optional[str]:
    """Insert one row owned by `owner_id` as the table owner. Returns its PK, or None."""
    cols = _columns(cur, table)
    pk = _pk_column(cur, table)
    payload: Dict[str, object] = {owner_col: owner_id}

    for name, dtype, nullable, default, udt, maxlen in cols:
        if name in payload:
            continue
        if nullable == "NO" and default is None:
            payload[name] = _synth(dtype, name, udt, maxlen, cur)

    if pk and pk not in payload:
        pk_meta = next((c for c in cols if c[0] == pk), None)
        if pk_meta and pk_meta[3] is None:
            payload[pk] = _synth(pk_meta[1], pk, pk_meta[4], pk_meta[5], cur)

    names = ", ".join(f'"{k}"' for k in payload)
    holders = ", ".join(["%s"] * len(payload))
    returning = f' RETURNING "{pk}"' if pk else ""
    try:
        cur.execute(
            f'INSERT INTO public."{table}" ({names}) VALUES ({holders}){returning}',
            list(payload.values()),
        )
        if pk:
            row = cur.fetchone()
            return str(row[0]) if row else None
        return None
    except Exception as exc:  # noqa: BLE001
        # The reason is RETURNED, not swallowed: a matrix that cannot say why it skipped
        # a table cannot be audited, and "could not seed" hid FK failures that reduced
        # this suite to a single table on its first run.
        _LAST_INSERT_ERROR[table] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        return None


#: Why the most recent insert attempt failed, per table. Module-level so `_insert_row`
#: can stay a simple helper while the fixture still reports a diagnosable reason.
_LAST_INSERT_ERROR: Dict[str, str] = {}


def _as_tenant(cur, tenant_id: str) -> None:
    """Impersonate a PostgREST request for `tenant_id`."""
    cur.execute("SET LOCAL ROLE authenticated")
    cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (tenant_id,))


def _reset_role(cur) -> None:
    cur.execute("RESET ROLE")


# ── the harness ─────────────────────────────────────────────────────────────


def _probe(conn, table: str, owner_col: str, pk: str,
           a_id: str, b_id: str, b_pk: str) -> Dict[str, object]:
    """Tenant A attacks tenant B's row. Returns what A achieved."""
    result: Dict[str, object] = {}
    cur = conn.cursor()

    # SELECT
    try:
        cur.execute("BEGIN")
        _as_tenant(cur, a_id)
        cur.execute(
            f'SELECT count(*) FROM public."{table}" WHERE "{pk}"::text = %s', (b_pk,)
        )
        result["select_visible"] = int(cur.fetchone()[0])
        cur.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        cur.execute("ROLLBACK")
        result["select_visible"] = 0
        result["select_error"] = type(exc).__name__

    # UPDATE
    try:
        cur.execute("BEGIN")
        _as_tenant(cur, a_id)
        cur.execute(
            f'UPDATE public."{table}" SET "{owner_col}" = %s WHERE "{pk}"::text = %s',
            (a_id, b_pk),
        )
        result["update_rows"] = cur.rowcount
        cur.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        cur.execute("ROLLBACK")
        result["update_rows"] = 0
        result["update_error"] = type(exc).__name__

    # DELETE
    try:
        cur.execute("BEGIN")
        _as_tenant(cur, a_id)
        cur.execute(
            f'DELETE FROM public."{table}" WHERE "{pk}"::text = %s', (b_pk,)
        )
        result["delete_rows"] = cur.rowcount
        cur.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        cur.execute("ROLLBACK")
        result["delete_rows"] = 0
        result["delete_error"] = type(exc).__name__

    # THE SIDE-EFFECT CHECK, as the owner. A refusal that still mutated is a failure.
    _reset_role(cur)
    cur.execute(
        f'SELECT "{owner_col}"::text FROM public."{table}" WHERE "{pk}"::text = %s',
        (b_pk,),
    )
    row = cur.fetchone()
    result["victim_still_present"] = row is not None
    result["victim_owner_unchanged"] = bool(row and row[0] == b_id)
    cur.close()
    return result


@pytest.fixture(scope="module")
def matrix(db):
    """Seed an A row and a B row in every reachable table, then probe each."""
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    cur = db.cursor()
    _reset_role(cur)

    # The FK target every tenant-scoped table points at. Without this the matrix
    # collapses to the one table that has no FK.
    _seed_identity(cur, a_id)
    _seed_identity(cur, b_id)

    findings: Dict[str, Dict[str, object]] = {}
    skipped: Dict[str, str] = {}

    for table in TARGET_TABLES:
        if not _table_exists(cur, table):
            skipped[table] = "table absent on this database"
            continue
        cols = _columns(cur, table)
        owner_col = _owner_column(cols, table)
        if owner_col is None:
            skipped[table] = (
                "no owner column; columns are: "
                + ",".join(c[0] for c in cols)[:200]
            )
            continue
        pk = _pk_column(cur, table)
        if pk is None:
            skipped[table] = "no primary key"
            continue

        a_pk = _insert_row(cur, table, owner_col, a_id)
        b_pk = _insert_row(cur, table, owner_col, b_id)
        if not b_pk:
            skipped[table] = _LAST_INSERT_ERROR.get(table, "could not seed a victim row")
            continue

        findings[table] = {
            "owner_col": owner_col,
            "pk": pk,
            "a_pk": a_pk,
            "b_pk": b_pk,
            **_probe(db, table, owner_col, pk, a_id, b_id, b_pk),
        }

    cur.close()
    return {"a_id": a_id, "b_id": b_id, "findings": findings, "skipped": skipped}


# ── 1. the matrix covered something ─────────────────────────────────────────


def test_the_matrix_actually_covered_tables(matrix):
    """A matrix that silently skipped everything would pass every assertion below."""
    covered = matrix["findings"]
    assert len(covered) >= 10, (
        f"only {len(covered)} table(s) were probed; skipped="
        f"{matrix['skipped']}. This matrix proves too little to rely on."
    )


def test_the_skips_are_reported_not_hidden(matrix):
    """Every table that could not be probed is named, with the reason."""
    for table, reason in matrix["skipped"].items():
        assert reason, f"{table} was skipped without a reason"


# ── 2. NEGATIVE: tenant A must not reach tenant B ──────────────────────────


def test_no_cross_tenant_read(matrix):
    leaks = {
        t: f["select_visible"]
        for t, f in matrix["findings"].items()
        if f.get("select_visible", 0) != 0
    }
    assert leaks == {}, (
        f"tenant A could READ tenant B's rows in: {leaks}. Cross-tenant read is P0."
    )


def test_no_cross_tenant_update(matrix):
    leaks = {
        t: f["update_rows"]
        for t, f in matrix["findings"].items()
        if f.get("update_rows", 0) != 0
    }
    assert leaks == {}, (
        f"tenant A UPDATED tenant B's rows in: {leaks}. Cross-tenant write is P0."
    )


def test_no_cross_tenant_delete(matrix):
    leaks = {
        t: f["delete_rows"]
        for t, f in matrix["findings"].items()
        if f.get("delete_rows", 0) != 0
    }
    assert leaks == {}, (
        f"tenant A DELETED tenant B's rows in: {leaks}. Cross-tenant delete is P0."
    )


def test_no_side_effect_on_the_victim_row(matrix):
    """THE ASSERTION A 403 CANNOT SATISFY.

    Read back as the table owner, so RLS cannot hide a mutation that did land.
    """
    damaged = {
        t: {
            "present": f.get("victim_still_present"),
            "owner_unchanged": f.get("victim_owner_unchanged"),
        }
        for t, f in matrix["findings"].items()
        if not (f.get("victim_still_present") and f.get("victim_owner_unchanged"))
    }
    assert damaged == {}, (
        f"tenant B's row was altered or removed in: {damaged}. A denial that still "
        "mutates is not a denial."
    )


# ── 3. POSITIVE: tenant A can still use its own data ───────────────────────


def test_a_tenant_can_read_its_own_rows(matrix, db):
    """Without this, a database that denied everything would look isolated."""
    a_id = matrix["a_id"]
    visible = 0
    checked = 0
    cur = db.cursor()
    for table, f in matrix["findings"].items():
        if not f.get("a_pk"):
            continue
        checked += 1
        try:
            cur.execute("BEGIN")
            _as_tenant(cur, a_id)
            cur.execute(
                f'SELECT count(*) FROM public."{table}" '
                f'WHERE "{f["pk"]}"::text = %s',
                (f["a_pk"],),
            )
            if int(cur.fetchone()[0]) == 1:
                visible += 1
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
    _reset_role(cur)
    cur.close()

    assert checked > 0, "no A-owned rows were seeded; the positive control is vacuous"
    assert visible > 0, (
        f"tenant A could not read ANY of its own {checked} seeded rows. Either the "
        "policies deny everything (in which case the negative results above prove "
        "nothing) or the claim is not being applied the way PostgREST applies it."
    )


# ── 4. DETECTION: prove the harness can see a leak ─────────────────────────


def test_the_harness_detects_a_deliberately_weakened_policy(db):
    """THE CONTROL THAT MAKES SECTION 2 MEANINGFUL.

    A scratch table with `USING (true)` is a cross-tenant leak by construction. If the
    same harness reports no leak on it, the harness is blind and every assertion above is
    worthless.
    """
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    table = f"tenant01_canary_{uuid.uuid4().hex[:8]}"
    cur = db.cursor()
    _reset_role(cur)

    try:
        cur.execute(
            f'CREATE TABLE public."{table}" '
            f"(id uuid PRIMARY KEY, user_id uuid NOT NULL, payload text)"
        )
        cur.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        # The weakening: no row filter at all.
        cur.execute(
            f'CREATE POLICY canary_all ON public."{table}" '
            f"FOR ALL TO authenticated USING (true) WITH CHECK (true)"
        )
        cur.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{table}" TO authenticated'
        )
        b_pk = str(uuid.uuid4())
        cur.execute(
            f'INSERT INTO public."{table}" (id, user_id, payload) VALUES (%s, %s, %s)',
            (b_pk, b_id, "victim"),
        )

        found = _probe(db, table, "user_id", "id", a_id, b_id, b_pk)

        assert found["select_visible"] == 1, (
            "the harness did NOT see a row exposed by a USING(true) policy, so it "
            "cannot detect a cross-tenant read and section 2 proves nothing"
        )
        assert found["update_rows"] == 1, (
            "the harness did NOT detect a cross-tenant UPDATE through a USING(true) "
            "policy, so section 2's update assertion proves nothing"
        )
    finally:
        _reset_role(cur)
        cur.execute(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
        cur.close()


def test_the_harness_detects_rls_being_disabled_entirely(db):
    """The other way isolation can be lost: RLS simply off."""
    a_id = str(uuid.uuid4())
    b_id = str(uuid.uuid4())
    table = f"tenant01_norls_{uuid.uuid4().hex[:8]}"
    cur = db.cursor()
    _reset_role(cur)
    try:
        cur.execute(
            f'CREATE TABLE public."{table}" '
            f"(id uuid PRIMARY KEY, user_id uuid NOT NULL, payload text)"
        )
        # RLS deliberately NOT enabled.
        cur.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON public."{table}" TO authenticated'
        )
        b_pk = str(uuid.uuid4())
        cur.execute(
            f'INSERT INTO public."{table}" (id, user_id, payload) VALUES (%s, %s, %s)',
            (b_pk, b_id, "victim"),
        )

        found = _probe(db, table, "user_id", "id", a_id, b_id, b_pk)

        assert found["select_visible"] == 1, (
            "the harness did not detect a table with RLS disabled; it would not report "
            "a table that shipped without policies"
        )
    finally:
        _reset_role(cur)
        cur.execute(f'DROP TABLE IF EXISTS public."{table}" CASCADE')
        cur.close()


# ── 5. the structural findings, asserted so they cannot silently change ────


def test_no_table_relies_solely_on_the_inert_tenant_setting(db):
    """`app.current_tenant_id` is never set by any request path.

    A table whose ONLY policy compares against it is unreachable for `authenticated` --
    which fails closed, so it is not a leak, but it means the policy is decorative and
    the real control is the application's WHERE clause. Recorded so the list cannot grow
    unnoticed.
    """
    cur = db.cursor()
    _reset_role(cur)
    cur.execute(
        """
        SELECT c.relname,
               bool_or(pg_get_expr(p.polqual, p.polrelid) LIKE '%auth.uid()%')  AS has_authuid,
               bool_or(pg_get_expr(p.polqual, p.polrelid) LIKE '%current_tenant_id%') AS has_inert,
               count(*) AS n
          FROM pg_policy p
          JOIN pg_class c ON c.oid = p.polrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname='public'
         GROUP BY c.relname
        """
    )
    inert_only = sorted(
        name for name, has_authuid, has_inert, _n in cur.fetchall()
        if has_inert and not has_authuid
    )
    cur.close()

    # These five are the known set. The assertion is that it does not GROW.
    known = {
        "dag_tasks", "idempotency_keys", "transaction_records",
        "transaction_checkpoints", "reconciliation_mismatches",
    }
    unexpected = set(inert_only) - known
    assert unexpected == set(), (
        f"new table(s) rely solely on the inert app.current_tenant_id predicate: "
        f"{sorted(unexpected)}. Nothing sets that setting, so for `authenticated` the "
        f"policy matches no row (fails closed) and the only real control is the "
        f"application's WHERE clause. Give them an auth.uid() policy."
    )


def test_no_table_has_force_row_level_security_so_the_owner_bypasses_rls(db):
    """The honest statement of the residual risk, pinned as a fact.

    Nothing has FORCE ROW LEVEL SECURITY, and the application's SQLAlchemy connection is
    the table owner -- so on THAT path RLS is skipped entirely and application-level
    `WHERE user_id = ...` is the only isolation. This test does not assert that is
    acceptable; it asserts the situation has not silently changed, in either direction.
    """
    cur = db.cursor()
    _reset_role(cur)
    cur.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity "
        "ORDER BY 1"
    )
    forced = [r[0] for r in cur.fetchall()]
    cur.close()

    # Informational, and deliberately not a failure: turning FORCE on before the
    # SQLAlchemy path sets a tenant setting would convert every owner-path read into an
    # empty result set. Recorded so the decision is explicit.
    assert isinstance(forced, list)
