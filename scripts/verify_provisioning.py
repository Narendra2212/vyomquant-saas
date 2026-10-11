"""Prove a database can be built from this repository, twice, from nothing.

WHAT THIS ANSWERS
-----------------
    EMPTY POSTGRES -> ONE COMMAND -> COMPLETE SCHEMA -> APPLICATION CAN START

The pre-launch audit established that nothing in this repository ever ran a migration --
not the Dockerfile, not startup.sh, not a Procfile, not a workflow, not a script -- so
every environment's schema was whatever had been applied by hand, and no two were
provably the same. ``scripts/provision_database.py`` closed the "can it be built" half.
This closes the "is it reproducible" half, which is a different question and the one a
disaster-recovery plan actually depends on.

WHY IT PROVISIONS TWICE
-----------------------
Once proves the schema can be created. Twice proves the process is idempotent, which is
what makes it safe to put in a deployment pipeline: a deploy that re-runs provisioning
must be a no-op rather than a failure or a duplicate-object error. The second run is
compared against the first on every dimension below, and any difference is a failure --
including a difference that would look harmless, like an extra index, because an
unexplained delta means the process is not deterministic.

WHAT IT COMPARES
----------------
Tables, columns and their types, primary keys, foreign keys, unique constraints, check
constraints, indexes, RLS flags, policies, privileges, extensions, functions, triggers,
sequences, enum types, and the migration bookkeeping. The point of comparing all of it
rather than a table count is that the defects this pass found were never missing tables --
they were a disabled RLS flag, a privilege nobody revoked, a column typed varchar instead
of uuid, and a constraint that silently failed to be created.

SAFETY
------
Creates and drops its OWN database on the target server, named with a fixed prefix, and
refuses to touch anything else. It will not run against the Supabase pooler port or a
non-local host. The administrative connection is made to ``postgres`` purely to issue
CREATE/DROP DATABASE.

USAGE
    python scripts/verify_provisioning.py --dsn postgresql://postgres:@127.0.0.1:5432/postgres
    python scripts/verify_provisioning.py            # reads staging/pg_dsn.txt
    python scripts/verify_provisioning.py --keep     # leave the database for inspection
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass, field

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Fixed prefix so this script can only ever drop a database it made.
DB_PREFIX = "aerora_provision_check"


# ── the snapshot ─────────────────────────────────────────────────────────────
#
# One query per dimension. Ordered deterministically so two snapshots of the same schema
# compare equal as plain Python objects, with no set/dict ordering in play.

SNAPSHOT_QUERIES: dict[str, str] = {
    "tables": """
        -- The OWNER is part of the comparison, not just the name. A table owned by a
        -- different role than the one this repository provisions is a table whose RLS
        -- exemption belongs to someone else: owners bypass their own policies unless
        -- FORCE is set, and this schema deliberately does not set FORCE. So an
        -- ownership difference is a security difference, and it is invisible if only
        -- `relname` is compared.
        SELECT c.relname, pg_get_userbyid(c.relowner)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY c.relname
    """,
    "columns": """
        SELECT table_name, column_name, data_type,
               coalesce(character_maximum_length::text, ''), is_nullable,
               coalesce(column_default, ''),
               -- Identity and generated status. Added for the production comparison:
               -- a column that is GENERATED ALWAYS AS IDENTITY in one database and a
               -- plain integer with a sequence default in another has the same name,
               -- type, nullability and (often) default, and behaves differently on
               -- INSERT. Comparing the first six fields would call them identical.
               is_identity, coalesce(identity_generation, ''), is_generated
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, column_name
    """,
    "primary_keys": """
        SELECT t.relname, c.conname,
               array_to_string(ARRAY(
                   SELECT a.attname FROM pg_attribute a
                   WHERE a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
                   ORDER BY a.attname
               ), ',')
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND c.contype = 'p'
        ORDER BY t.relname, c.conname
    """,
    "foreign_keys": """
        SELECT t.relname, c.conname, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND c.contype = 'f'
        ORDER BY t.relname, c.conname
    """,
    "unique_and_check_constraints": """
        SELECT t.relname, c.conname, c.contype::text, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND c.contype IN ('u', 'c')
        ORDER BY t.relname, c.conname
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes WHERE schemaname = 'public'
        ORDER BY tablename, indexname
    """,
    "row_level_security": """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY c.relname
    """,
    "policies": """
        SELECT tablename, policyname, permissive, array_to_string(roles, ','), cmd,
               coalesce(qual, ''), coalesce(with_check, '')
        FROM pg_policies WHERE schemaname = 'public'
        ORDER BY tablename, policyname
    """,
    "privileges": """
        SELECT table_name, grantee, privilege_type
        FROM information_schema.role_table_grants
        WHERE table_schema = 'public'
          AND grantee IN ('anon', 'authenticated', 'service_role')
        ORDER BY table_name, grantee, privilege_type
    """,
    "extensions": """
        SELECT extname FROM pg_extension ORDER BY extname
    """,
    "functions": """
        SELECT p.proname, pg_get_function_identity_arguments(p.oid), p.prosecdef
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        ORDER BY p.proname, pg_get_function_identity_arguments(p.oid)
    """,
    "triggers": """
        SELECT c.relname, t.tgname, p.proname
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE n.nspname = 'public' AND NOT t.tgisinternal
        ORDER BY c.relname, t.tgname
    """,
    "sequences": """
        SELECT sequence_name FROM information_schema.sequences
        WHERE sequence_schema = 'public' ORDER BY sequence_name
    """,
    "enum_types": """
        SELECT t.typname, array_to_string(ARRAY(
                   SELECT e.enumlabel FROM pg_enum e
                   WHERE e.enumtypid = t.oid ORDER BY e.enumsortorder
               ), ',')
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public' AND t.typtype = 'e'
        ORDER BY t.typname
    """,
    "alembic_version": "SELECT version_num FROM alembic_version ORDER BY version_num",
    "recorded_migrations": """
        SELECT filename, sha256, step FROM public.schema_migrations
        ORDER BY filename, step
    """,
}


def snapshot(dsn: str) -> dict[str, list]:
    out: dict[str, list] = {}
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for name, sql in SNAPSHOT_QUERIES.items():
            try:
                cur.execute(sql)
                out[name] = [list(map(_norm, row)) for row in cur.fetchall()]
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                out[name] = [["<QUERY FAILED>", type(exc).__name__, str(exc)[:160]]]
    return out


def _norm(value):
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


# ── comparison-key declaration and the shared classifier ─────────────────────
#
# PHASE 11B / W1C-02, authorized as AP-W1D-3 with decisions D1-D4
# (staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md).
#
# WHY THIS LIVES HERE. `scripts/verify_schema_drift.py` and
# `scripts/verify_production_schema.py` both import from this module; this module imports
# neither of them. Placing the classifier in `verify_schema_drift.py` instead would force
# an import of it from here for `diff_snapshots()`, closing a cycle. This is the only
# location from which all three callers can share one implementation (decision D4).
#
# WHAT IT FIXES. `compare()` previously tested WHOLE-TUPLE membership
# (`[r for r in expected if r not in actual]`), so it had no notion of an object's
# identity separate from its attributes. One policy whose WITH CHECK expression changed
# was reported as one row MISSING and one row UNEXPECTED -- claiming the same object was
# both absent from the database and foreign to the repository. 12 of the 16 dimensions
# have attribute columns and were exposed to that.
#
# WHAT IS NOT CLAIMED. Key uniqueness is UNVERIFIED for all 16 dimensions: the widths
# below rest on documented catalogue properties and, for `recorded_migrations`, on a
# PRIMARY KEY in the DDL this repository creates -- not on a measurement of any live
# catalogue. The classifier is therefore built to stay CORRECT when a declared key turns
# out not to be unique: see `_ambiguous` handling in `classify_dimension`.

#: Sentinel for "the whole row is the key". A distinct object rather than `None`, because
#: `dict.get()` returns `None` for an ABSENT key and the two cases must not be
#: confusable: "this dimension has no attribute columns" is a reviewed conclusion, while
#: "nobody has declared this dimension" is an omission. `None` would collapse them.
WHOLE_ROW = "whole-row"

#: How many LEADING columns of each dimension's SELECT list identify the object, as
#: opposed to describing it. Derived from each query's own `ORDER BY` together with the
#: uniqueness PostgreSQL enforces on the catalogue being read -- not from the column
#: names. `tests/test_snapshot_dimension_invariants.py` checks every entry against the
#: SQL, so a wrong width here fails rather than silently redefining identity.
COMPARISON_KEY: dict[str, object] = {
    # relname identifies the table; pg_get_userbyid(relowner) describes it. An ownership
    # change is a security change (owners bypass their own policies unless FORCE is set),
    # so it must read as a modification, not as a drop plus an add.
    "tables": 1,
    # (table_name, column_name) is unique in information_schema.columns. The other seven
    # -- type, length, nullability, default, identity, identity_generation, generated --
    # are attributes.
    "columns": 2,
    # (relname, conname): constraint names are unique per table.
    "primary_keys": 2,
    "foreign_keys": 2,
    "unique_and_check_constraints": 2,
    # (tablename, indexname): index names are unique within a schema.
    "indexes": 2,
    # relname identifies the table; relrowsecurity and relforcerowsecurity are its flags.
    "row_level_security": 1,
    # (tablename, policyname) is unique in pg_policies. permissive, roles, cmd, qual and
    # with_check are attributes -- this is the dimension whose misreport W1C-02 observed.
    "policies": 2,
    # table_name + grantee + privilege_type IS the grant. There is nothing left to
    # describe, so a difference can only be a real grant or revoke. Note that
    # information_schema.role_table_grants is one row per (grantor, grantee, table,
    # privilege, is_grantable) and this query projects only three of those with neither
    # DISTINCT nor GROUP BY, so two grantors of the same privilege produce two IDENTICAL
    # rows. That is why multiplicity is counted rather than collapsed.
    "privileges": WHOLE_ROW,
    "extensions": WHOLE_ROW,
    # (proname, identity_arguments) is the overload. prosecdef is an attribute -- this is
    # the dimension whose misreport W1C observed for 024's SECURITY DEFINER change.
    "functions": 2,
    # (relname, tgname): trigger names are unique per table. The function it calls is an
    # attribute.
    "triggers": 2,
    "sequences": WHOLE_ROW,
    # typname identifies the enum; its label list is an attribute.
    "enum_types": 1,
    "alembic_version": WHOLE_ROW,
    # filename is the PRIMARY KEY of public.schema_migrations
    # (scripts/provision_database.py:154). sha256 and step are attributes -- so a
    # migration file edited after being applied is a MODIFIED checksum, which is exactly
    # the condition provision_database.py:465-471 exits 4 on. The query's ORDER BY adds
    # `step` after `filename`, which is wider than the key and merely harmless: the
    # writer is `ON CONFLICT (filename) DO NOTHING`, so a file applied at two steps is
    # RECORDED once.
    "recorded_migrations": 1,
}


def _sort_key(value):
    """A total order over normalised snapshot values, safe across `None`.

    `_norm` preserves `None`, and in Python 3 `None < 'x'` raises `TypeError`. Four of
    `columns`' seven attribute columns are not `coalesce(...)`-guarded, so a NULL is
    reachable and a plain `sorted()` over these tuples would crash on real data rather
    than merely mis-order. Nulls sort first, then everything by its string form.
    """
    if isinstance(value, (tuple, list)):
        return tuple(_sort_key(v) for v in value)
    return (value is None, str(value))


@dataclass
class ClassifiedDimension:
    """The outcome of comparing one dimension. Pure data; no I/O, no formatting.

    `verdict` is one of:
      "same"          nothing to report
      "drift"         a real difference was found
      "inconclusive"  the dimension could not be classified at all -- the key is
                      undeclared or invalid, so there is nothing safe to group by

    INVARIANT, asserted by the tests: if `verdict != "same"` then at least one of
    `modified`, `missing`, `added`, `duplicated`, `ambiguous`, `notes` is non-empty. A
    nonzero drift can never be reported without naming a subject -- which is precisely
    what the previous implementation did when it printed "0 missing, 0 unexpected".
    """

    dimension: str
    key_spec: object
    verdict: str = "same"
    #: Set by the CALLER, not by the classifier. Each of the three callers has its own
    #: severity policy -- `compare()` has two tiers, `check_drift()` has three,
    #: `diff_snapshots()` has none -- so advisory routing is renderer concern. Keeping it
    #: out of the classifier is also what stops this module needing
    #: `verify_schema_drift.ADVISORY_DIMENSIONS`, which would close an import cycle.
    advisory: bool = False
    unchanged: int = 0
    #: [{"key": tuple, "changes": [{"index", "column_index", "was", "now"}]}]
    modified: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    added: list = field(default_factory=list)
    #: [{"subject": tuple, "baseline": int, "live": int}] -- identical rows, differing counts
    duplicated: list = field(default_factory=list)
    #: [{"key": tuple, "baseline": int, "live": int}] -- a key shared by >1 row on a side
    ambiguous: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def has_findings(self) -> bool:
        return bool(self.modified or self.missing or self.added
                    or self.duplicated or self.ambiguous or self.notes)


def _as_rows(rows) -> list[tuple]:
    """JSON turns tuples into lists; normalise before comparing or counting."""
    return [tuple(r) if isinstance(r, list) else r for r in rows]


def _multiset_compare(expected: list[tuple], actual: list[tuple], result) -> None:
    """Whole-row multiset difference. Never pairs two non-identical rows.

    Used for `WHOLE_ROW` dimensions and, under decision D1, as the fallback inside a key
    group whose key turned out to be ambiguous. Because it only ever matches rows that
    are equal in full, it cannot misreport two distinct objects as one modification --
    it simply declines to say "modified".
    """
    ce = collections.Counter(expected)
    ca = collections.Counter(actual)
    for row in sorted(set(ce) | set(ca), key=_sort_key):
        n, m = ce[row], ca[row]
        if n and not m:
            result.missing.extend([row] * n)
        elif m and not n:
            result.added.extend([row] * m)
        elif n == m:
            result.unchanged += n
        else:
            # Same row, different number of copies. Reported with both counts rather
            # than as a missing/added pair, because neither side lost or gained the
            # OBJECT -- only its multiplicity changed.
            result.duplicated.append({"subject": row, "baseline": n, "live": m})
            result.unchanged += min(n, m)


def classify_dimension(dimension: str, expected, actual) -> ClassifiedDimension:
    """Classify one dimension's two row sets. Pure data in, pure data out.

    Order-insensitive by construction (decision D2): rows are grouped and counted, so two
    snapshots with identical content in a different order classify as `same` rather than
    producing the previous implementation's subject-less hard failure.
    """
    key_spec = COMPARISON_KEY.get(dimension)
    result = ClassifiedDimension(dimension=dimension, key_spec=key_spec)
    expected = _as_rows(expected)
    actual = _as_rows(actual)

    # ── declaration faults: refuse to classify (decision D1's escalation) ────
    #
    # Falling back to a whole-row comparison here would produce a plausible-looking
    # result for a dimension nobody has decided the identity of, masking the omission
    # that the static guard exists to catch. So this is inconclusive, not lenient.
    if dimension not in COMPARISON_KEY:
        result.verdict = "inconclusive"
        result.notes.append("UNDECLARED DIMENSION -- no comparison key; not classified")
        return result

    if key_spec != WHOLE_ROW:
        width_ok = isinstance(key_spec, int) and not isinstance(key_spec, bool) and key_spec >= 1
        if width_ok:
            widest = max((len(r) for r in expected + actual), default=key_spec)
            width_ok = key_spec <= widest
        if not width_ok:
            result.verdict = "inconclusive"
            result.notes.append(f"INVALID KEY DECLARATION {key_spec!r} -- not classified")
            return result

    # ── whole-row dimensions: the row IS the key, so `modified` is meaningless ───
    if key_spec == WHOLE_ROW:
        _multiset_compare(expected, actual, result)
        result.verdict = "drift" if result.has_findings() else "same"
        return result

    # ── keyed dimensions ────────────────────────────────────────────────────────
    width = key_spec

    def grouped(rows):
        out: dict[tuple, list[tuple]] = {}
        for row in rows:
            out.setdefault(tuple(row[:width]), []).append(tuple(row[width:]))
        return out

    ge, ga = grouped(expected), grouped(actual)

    for key in sorted(set(ge) | set(ga), key=_sort_key):
        in_e, in_a = key in ge, key in ga
        rows_e = ge.get(key, [])
        rows_a = ga.get(key, [])

        if in_e and not in_a:
            result.missing.extend(sorted((key + a for a in rows_e), key=_sort_key))
            continue
        if in_a and not in_e:
            result.added.extend(sorted((key + a for a in rows_a), key=_sort_key))
            continue

        if len(rows_e) > 1 or len(rows_a) > 1:
            # AMBIGUOUS KEY. Two or more distinct rows share this key on at least one
            # side, so the declared width does not identify an object here. Pairing them
            # would reproduce the very defect this classifier exists to fix, from the
            # other direction: two distinct objects differenced into one false
            # "modified". Under decision D1 this is a hard failure with its own label,
            # and the group falls back to a whole-row multiset comparison, which cannot
            # pair non-identical rows.
            result.ambiguous.append(
                {"key": key, "baseline": len(rows_e), "live": len(rows_a)}
            )
            _multiset_compare(
                [key + a for a in rows_e], [key + a for a in rows_a], result
            )
            continue

        attrs_e, attrs_a = rows_e[0], rows_a[0]
        if attrs_e == attrs_a:
            result.unchanged += 1
            continue

        changes = [
            {"index": i, "column_index": width + i, "was": was, "now": now}
            for i, (was, now) in enumerate(zip(attrs_e, attrs_a))
            if was != now
        ]
        # A length difference between the two attribute tuples is not expected -- the
        # SELECT list is fixed per dimension -- but if it ever happened, zip() would hide
        # the tail. Record it rather than lose it.
        if len(attrs_e) != len(attrs_a):
            changes.append({
                "index": -1, "column_index": -1,
                "was": f"<{len(attrs_e)} attribute columns>",
                "now": f"<{len(attrs_a)} attribute columns>",
            })
        result.modified.append({"key": key, "changes": changes})

    result.verdict = "drift" if result.has_findings() else "same"
    return result


# ── orchestration ────────────────────────────────────────────────────────────


def _admin_connect(dsn: str):
    conn = psycopg2.connect(dsn)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def _swap_database(dsn: str, dbname: str) -> str:
    head, _, _tail = dsn.rpartition("/")
    return f"{head}/{dbname}"


def provision(dsn: str, label: str) -> tuple[int, str]:
    proc = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "provision_database.py"),
            "--dsn", dsn, "--apply", "--create-substrate",
        ],
        cwd=str(ROOT),
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log = ROOT / "staging" / f"verify_provisioning_{label}.log"
    log.parent.mkdir(exist_ok=True)
    log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return proc.returncode, str(log)


def diff_snapshots(first: dict, second: dict) -> list[str]:
    problems: list[str] = []
    for key in SNAPSHOT_QUERIES:
        a, b = first.get(key, []), second.get(key, [])
        if a == b:
            continue
        only_a = [r for r in a if r not in b]
        only_b = [r for r in b if r not in a]
        problems.append(
            f"{key}: {len(a)} -> {len(b)} row(s); "
            f"{len(only_a)} disappeared, {len(only_b)} appeared"
        )
        for row in only_a[:4]:
            problems.append(f"    only in run 1: {row}")
        for row in only_b[:4]:
            problems.append(f"    only in run 2: {row}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", help="admin DSN on the target server (defaults to staging/pg_dsn.txt)")
    ap.add_argument("--keep", action="store_true", help="do not drop the test database")
    ap.add_argument("--i-understand-this-is-remote", action="store_true")
    args = ap.parse_args()

    dsn = args.dsn or os.environ.get("PROVISION_DATABASE_URL")
    if not dsn:
        path = ROOT / "staging" / "pg_dsn.txt"
        if not path.exists():
            sys.exit("no --dsn given and staging/pg_dsn.txt is absent")
        dsn = path.read_text(encoding="utf-8").strip()

    if "6543" in dsn:
        sys.exit("REFUSED: port 6543 is the Supabase transaction pooler.")
    if not args.i_understand_this_is_remote and not (
        "127.0.0.1" in dsn or "localhost" in dsn
    ):
        sys.exit("REFUSED: DSN host is not local. This script CREATEs and DROPs a database.")

    dbname = f"{DB_PREFIX}_{os.getpid()}"
    target = _swap_database(dsn, dbname)

    print("=" * 78)
    print("PROVISIONING VERIFICATION — empty database, provisioned twice")
    print("=" * 78)
    print(f"server      : {dsn.rsplit('/', 1)[0]}")
    print(f"scratch db  : {dbname}")
    print()

    admin = _admin_connect(dsn)
    failures: list[str] = []
    try:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            cur.execute(f'CREATE DATABASE "{dbname}"')
        print(f"STEP 1 — created an EMPTY database {dbname}")

        with psycopg2.connect(target) as probe, probe.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'"
            )
            before = cur.fetchone()[0]
        print(f"         public tables before provisioning: {before}")
        if before != 0:
            failures.append(f"the scratch database was not empty ({before} tables)")

        print()
        print("STEP 2 — provision (run 1)")
        rc1, log1 = provision(target, "run1")
        print(f"         exit={rc1}  log={log1}")
        if rc1 != 0:
            failures.append(f"first provisioning run exited {rc1}; see {log1}")
            raise SystemExit(_report(failures, None))
        first = snapshot(target)
        print(f"         tables={len(first['tables'])} "
              f"fks={len(first['foreign_keys'])} policies={len(first['policies'])} "
              f"functions={len(first['functions'])} triggers={len(first['triggers'])}")

        print()
        print("STEP 3 — provision AGAIN on the same database (run 2, idempotency)")
        rc2, log2 = provision(target, "run2")
        print(f"         exit={rc2}  log={log2}")
        if rc2 != 0:
            failures.append(
                f"second provisioning run exited {rc2} — the process is NOT idempotent "
                f"and cannot be put in a deployment pipeline; see {log2}"
            )
        second = snapshot(target)
        print(f"         tables={len(second['tables'])} "
              f"fks={len(second['foreign_keys'])} policies={len(second['policies'])} "
              f"functions={len(second['functions'])} triggers={len(second['triggers'])}")

        print()
        print("STEP 4 — compare the two schemas across 16 dimensions")
        # `recorded_migrations` legitimately differs: the second run records nothing new
        # because every file's checksum already matches, but `applied_at` is excluded
        # from the snapshot so the rows themselves must be identical.
        problems = diff_snapshots(first, second)
        if problems:
            failures.extend(problems)
            for line in problems[:40]:
                print(f"  DIFF {line}")
        else:
            for key in SNAPSHOT_QUERIES:
                print(f"  same  {key:<30} {len(first[key])} row(s)")

        snap_path = ROOT / "staging" / "provisioning_snapshot.json"
        snap_path.write_text(json.dumps(first, indent=1, sort_keys=True), encoding="utf-8")
        print()
        print(f"schema snapshot written to {snap_path}")
        print("  (this is the baseline `scripts/verify_schema_drift.py` compares against)")

    finally:
        if args.keep:
            print(f"\n--keep: leaving {dbname} in place")
        else:
            try:
                with admin.cursor() as cur:
                    cur.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (dbname,),
                    )
                    cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
                print(f"\ndropped {dbname}")
            except Exception as exc:  # noqa: BLE001
                print(f"\ncould not drop {dbname}: {exc}")
        admin.close()

    return _report(failures, first)


def _report(failures: list[str], first: dict | None) -> int:
    print()
    print("=" * 78)
    if failures:
        print(f"RESULT: NOT REPRODUCIBLE — {len(failures)} problem(s)")
        for f in failures[:20]:
            print(f"  * {f}")
    else:
        print("RESULT: REPRODUCIBLE")
        print("  An empty PostgreSQL database was provisioned from this repository by a")
        print("  single command, twice, and the two schemas are identical across tables,")
        print("  columns, primary keys, foreign keys, unique and check constraints,")
        print("  indexes, RLS flags, policies, privileges, extensions, functions,")
        print("  triggers, sequences, enum types and migration bookkeeping.")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
