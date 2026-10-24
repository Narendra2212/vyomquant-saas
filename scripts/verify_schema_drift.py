#!/usr/bin/env python
"""Compare a live database's schema against the provisioning baseline, and fail on drift.

WHAT THIS IS FOR
----------------
`scripts/verify_provisioning.py` proves that provisioning an EMPTY database from this
repository is reproducible. It says nothing about a database that already exists and has
been changed by some other means.

That gap is not hypothetical here. Supabase lets a schema be edited through its dashboard,
and such an edit leaves no migration in this tree -- so the repository and the database can
disagree while every migration-based check stays green. The 22 billing exposures were the
mirror image of the same problem: migrations declared correct RLS policies and the
`ENABLE ROW LEVEL SECURITY` statement was simply absent, so what the files said and what
the database did were two different things.

This script asks the only question that closes that gap:

    does the schema in front of me match what this repository produces?

HOW TO USE IT
-------------
    # 1. produce (or refresh) the baseline from a clean provision
    python scripts/verify_provisioning.py

    # 2. compare any database against that baseline
    python scripts/verify_schema_drift.py --dsn postgresql://...

    # or against an explicit snapshot file
    python scripts/verify_schema_drift.py --dsn ... --baseline staging/some_snapshot.json

Exit 0 -> no drift in any compared dimension.
Exit 1 -> drift found; every differing row is printed, with which side it is on.
Exit 2 -> could not run (no baseline, no DSN, connection refused).

WHAT IS COMPARED, AND WHAT IS DELIBERATELY NOT
----------------------------------------------
The 16 dimensions `verify_provisioning.py` snapshots: tables, columns, primary keys,
foreign keys, unique and check constraints, indexes, RLS flags, policies, privileges,
extensions, functions, triggers, sequences, enum types, `alembic_version` and the
provisioner's own `schema_migrations` rows.

`recorded_migrations` is compared but reported as a WARNING rather than a failure, because
a database legitimately ahead or behind on *bookkeeping* is a deployment-state question,
not a schema-shape question, and conflating the two makes the signal useless. Everything
else is a hard failure.

Row DATA is never read. This compares shape only, so it is safe to point at production.
The connection is opened read-only.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import urllib.parse

try:
    import psycopg2
except ImportError:  # pragma: no cover - dependency is declared in requirements
    sys.exit("psycopg2 is required: pip install psycopg2-binary")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.verify_provisioning import (  # noqa: E402
    COMPARISON_KEY,
    SNAPSHOT_QUERIES,
    WHOLE_ROW,
    _norm,
    classify_dimension,
)

#: Compared, but a difference here is reported and does not fail the run. See the
#: module docstring for why bookkeeping is held to a different standard than shape.
ADVISORY_DIMENSIONS = frozenset({"recorded_migrations", "alembic_version"})

DEFAULT_BASELINE = ROOT / "staging" / "provisioning_snapshot.json"


def redact(dsn: str) -> str:
    """A DSN safe to print. Never returns anything that could be a credential.

    `dsn.split("@")[-1]` is not sufficient on its own: a DSN with no `@` is returned
    whole, and a DSN with a password containing `@` would leak its tail. This keeps the
    scheme, host, port and database and discards everything else, so the output is still
    useful for telling two databases apart without carrying a secret into a log or a
    launch report.
    """
    try:
        parsed = urllib.parse.urlsplit(dsn)
    except ValueError:
        return "<unparseable DSN, withheld>"
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{database}"


def assert_read_only(conn, dsn: str) -> None:
    """Prove the session cannot write before issuing a single query against production.

    `conn.set_session(readonly=True)` sets `default_transaction_read_only`, which the
    SERVER then enforces -- a write is rejected with 25006 `read_only_sql_transaction`
    regardless of what this script does next. That is the guarantee worth having, and it
    is worth CHECKING rather than assuming: a connection pooler can swallow session
    settings, and if that happened the script would carry on believing itself harmless.

    So the setting is read back from the server. If it is not `on`, this aborts before
    touching anything.
    """
    with conn.cursor() as cur:
        cur.execute("SHOW default_transaction_read_only")
        setting = cur.fetchone()[0]
    if setting != "on":
        sys.exit(
            "REFUSING TO PROCEED: this session is not read-only.\n"
            f"  default_transaction_read_only = {setting!r}, expected 'on'.\n"
            f"  database = {redact(dsn)}\n"
            "  A connection pooler may be discarding session settings. Connect to the\n"
            "  direct port (5432), not Supabase's transaction pooler (6543)."
        )


def _refuse_transaction_pooler(dsn: str) -> None:
    """Supabase's port 6543 is the transaction-mode pooler. Refuse it.

    Same refusal `scripts/provision_database.py` makes, for a related reason: session
    state does not survive transaction pooling, so `default_transaction_read_only` --
    the entire read-only guarantee above -- cannot be relied upon there.
    """
    try:
        port = urllib.parse.urlsplit(dsn).port
    except ValueError:
        port = None
    if port == 6543:
        sys.exit(
            "REFUSING TO PROCEED: port 6543 is Supabase's transaction-mode pooler.\n"
            "  Session settings do not survive it, so this script cannot guarantee a\n"
            "  read-only session. Use the direct connection on port 5432."
        )


def live_snapshot(dsn: str) -> dict[str, list]:
    """The same 16 queries `verify_provisioning.py` uses, against `dsn`, read-only.

    Imported rather than copied. A second copy of these queries would drift from the
    first, and a drift checker that drifts is worse than none.
    """
    _refuse_transaction_pooler(dsn)

    out: dict[str, list] = {}
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    assert_read_only(conn, dsn)
    try:
        with conn.cursor() as cur:
            for name, sql in SNAPSHOT_QUERIES.items():
                try:
                    cur.execute(sql)
                    out[name] = [tuple(_norm(v) for v in row) for row in cur.fetchall()]
                except psycopg2.Error as exc:
                    # A dimension that cannot be read is itself a finding, recorded in
                    # band rather than swallowed.
                    out[name] = [("QUERY FAILED", str(exc).splitlines()[0])]
    finally:
        conn.close()
    return out


def _server_version(dsn: str) -> str:
    """The server's own identity, recorded in the output.

    Asked separately and read from the server rather than inferred from the DSN or the
    driver, because "which PostgreSQL is this" is part of the evidence: the local
    verification ran on 16.2 and production reports 17.x, and a drift report that does
    not say which is being compared is incomplete.
    """
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_setting('server_version')")
            full, short = cur.fetchone()
    finally:
        conn.close()
    return f"{short} ({full.split(' on ')[0]})"


def _as_tuples(rows: list) -> list[tuple]:
    """JSON turns tuples into lists; normalise both sides before comparing."""
    return [tuple(r) if isinstance(r, list) else r for r in rows]


#: How many rows of each kind are enumerated before the report truncates. Unchanged.
ROW_CAP = 12


def classify_all(baseline: dict, live: dict) -> dict:
    """Classify every dimension once, so the summary table and the detail cannot disagree.

    Previously `compare()` and `main()`'s summary loop each decided drift independently --
    `compare()` by whole-tuple membership, the summary by list equality. For rows that
    were identical but reordered the summary printed `DRIFT` while the detail printed
    `0 missing, 0 unexpected` and named nothing. One classification feeding both removes
    that disagreement by construction.
    """
    out = {}
    for key in SNAPSHOT_QUERIES:
        result = classify_dimension(
            key,
            _as_tuples(baseline.get(key, [])),
            _as_tuples(live.get(key, [])),
        )
        # Advisory routing is this caller's policy, not the classifier's.
        result.advisory = key in ADVISORY_DIMENSIONS
        out[key] = result
    return out


def render(result) -> list[str]:
    """Human-readable lines for one classified dimension. Empty when there is nothing.

    The two row labels, the `ROW_CAP` of 12 and the `... and N more` convention are
    unchanged from the previous implementation. What is new is that a MODIFIED object is
    named ONCE with the attributes that changed, instead of appearing twice -- once as
    missing and once as unexpected -- with nothing linking the halves.
    """
    if result.verdict == "same":
        return []

    if result.verdict == "inconclusive":
        # No classification was possible, so there are no buckets to print. The note is
        # the finding.
        return [f"{result.dimension}: NOT CLASSIFIED"] + [f"    {n}" for n in result.notes]

    counts = [
        f"{result.unchanged} unchanged",
        f"{len(result.modified)} modified",
        f"{len(result.missing)} missing",
        f"{len(result.added)} added",
    ]
    if result.duplicated:
        counts.append(f"{len(result.duplicated)} duplicated")
    if result.ambiguous:
        counts.append(f"{len(result.ambiguous)} ambiguous")
    lines = [f"{result.dimension}: " + ", ".join(counts)]

    # Ambiguity first: it qualifies everything else reported for those keys.
    for item in result.ambiguous[:ROW_CAP]:
        lines.append(
            f"    AMBIGUOUS KEY {item['key']}: baseline={item['baseline']}, "
            f"live={item['live']} -- not classified; compared as whole rows"
        )
    if len(result.ambiguous) > ROW_CAP:
        lines.append(f"    ... and {len(result.ambiguous) - ROW_CAP} more ambiguous")

    for item in result.modified[:ROW_CAP]:
        lines.append(f"    MODIFIED {item['key']}")
        for change in item["changes"]:
            lines.append(
                f"        attr[{change['index']}] (col {change['column_index']}): "
                f"{change['was']!r} -> {change['now']!r}"
            )
    if len(result.modified) > ROW_CAP:
        lines.append(f"    ... and {len(result.modified) - ROW_CAP} more modified")

    for row in result.missing[:ROW_CAP]:
        lines.append(f"    MISSING from the database : {row}")
    if len(result.missing) > ROW_CAP:
        lines.append(f"    ... and {len(result.missing) - ROW_CAP} more missing")

    for row in result.added[:ROW_CAP]:
        lines.append(f"    NOT IN this repository    : {row}")
    if len(result.added) > ROW_CAP:
        lines.append(f"    ... and {len(result.added) - ROW_CAP} more unexpected")

    for item in result.duplicated[:ROW_CAP]:
        lines.append(
            f"    DUPLICATED {item['subject']}: baseline={item['baseline']}, "
            f"live={item['live']}"
        )
    if len(result.duplicated) > ROW_CAP:
        lines.append(f"    ... and {len(result.duplicated) - ROW_CAP} more duplicated")

    return lines


def compare(baseline: dict, live: dict) -> tuple[list[str], list[str]]:
    """Return (hard failures, advisories), each a list of human-readable lines.

    Signature and routing unchanged. The diff underneath is now keyed and multiset-aware
    (W1C-02, AP-W1D-3): an attribute change on an object present on both sides reports as
    one MODIFIED entry rather than as a removal plus an addition, and a difference can no
    longer be reported without naming its subject.
    """
    failures: list[str] = []
    advisories: list[str] = []

    for key, result in classify_all(baseline, live).items():
        lines = render(result)
        if not lines:
            continue
        (advisories if result.advisory else failures).extend(lines)

    return failures, advisories


def main() -> int:
    ap = argparse.ArgumentParser(description="Fail if a live schema has drifted from the "
                                             "provisioning baseline.")
    ap.add_argument("--dsn", help="database to check (defaults to staging/pg_dsn.txt)")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                    help="snapshot JSON from scripts/verify_provisioning.py")
    ap.add_argument(
        "--write-baseline", metavar="PATH",
        help="write this database's snapshot to PATH in baseline format and exit 0, "
             "comparing nothing. For REFRESHING a stale baseline from a database that "
             "was just provisioned from repository source -- which is the only kind of "
             "database whose shape is allowed to define the baseline. Pointing this at "
             "production would enshrine production's drift as the expectation, which is "
             "the opposite of what this script is for.",
    )
    args = ap.parse_args()

    baseline_path = pathlib.Path(args.baseline)
    if not baseline_path.exists() and not args.write_baseline:
        print(f"no baseline at {baseline_path}")
        print("produce one first:  python scripts/verify_provisioning.py")
        return 2

    dsn = args.dsn
    if not dsn:
        dsn_file = ROOT / "staging" / "pg_dsn.txt"
        if not dsn_file.exists():
            print("no --dsn given and staging/pg_dsn.txt does not exist")
            return 2
        dsn = dsn_file.read_text(encoding="utf-8").strip()

    # ── refresh mode ────────────────────────────────────────────────────────────
    # A stale baseline and a missing migration produce the same report, and they have
    # opposite fixes -- which is why this writes to a path the caller names rather than
    # over the committed baseline, and why CI uploads the result as an artifact for a
    # human to compare and commit instead of self-healing the check that guards it.
    if args.write_baseline:
        try:
            snapshot = live_snapshot(dsn)
            version = _server_version(dsn)
        except psycopg2.Error as exc:
            print(f"could not read the database: {str(exc).splitlines()[0]}")
            print(f"  database = {redact(dsn)}")
            return 2
        out = pathlib.Path(args.write_baseline)
        out.parent.mkdir(parents=True, exist_ok=True)
        # indent=1, sort_keys=True matches what verify_provisioning.py writes, so a
        # refreshed file diffs against the committed one line-for-line.
        out.write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
        print("=" * 78)
        print("SCHEMA SNAPSHOT WRITTEN (no comparison performed)")
        print("=" * 78)
        print(f"database : {redact(dsn)}")
        print(f"server   : {version}")
        print(f"written  : {out}")
        for key in sorted(snapshot):
            print(f"   {key:<32} {len(snapshot[key])}")
        print("\nCompare against the committed baseline before replacing it. Drift means")
        print("EITHER a stale baseline OR a missing migration, and only a human can say")
        print("which -- this file is evidence for that decision, not the decision.")
        return 0

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    try:
        live = live_snapshot(dsn)
        server_version = _server_version(dsn)
    except psycopg2.Error as exc:
        print(f"could not read the database: {str(exc).splitlines()[0]}")
        print(f"  database = {redact(dsn)}")
        return 2

    print("=" * 78)
    print("SCHEMA DRIFT CHECK")
    print("=" * 78)
    print(f"baseline : {baseline_path}")
    print(f"database : {redact(dsn)}")
    print(f"read-only: yes -- default_transaction_read_only verified 'on' before reading")
    print(f"server   : {server_version}")
    print(f"timestamp: {timestamp}")
    print("-" * 78)

    # One classification drives both the summary table and the detail sections, so the
    # marker printed here cannot contradict what is reported below.
    classified = classify_all(baseline, live)

    failures: list[str] = []
    advisories: list[str] = []
    for result in classified.values():
        lines = render(result)
        if not lines:
            continue
        (advisories if result.advisory else failures).extend(lines)

    for key in SNAPSHOT_QUERIES:
        result = classified[key]
        expected = _as_tuples(baseline.get(key, []))
        actual = _as_tuples(live.get(key, []))
        mark = {"same": "same ", "drift": "DRIFT", "inconclusive": "?????"}[result.verdict]
        print(f"  {mark} {key:<30} baseline={len(expected):<5} live={len(actual)}")

    if advisories:
        print("-" * 78)
        print("ADVISORY -- deployment bookkeeping, not schema shape")
        for line in advisories:
            print(f"  {line}")

    if failures:
        print("-" * 78)
        print("DRIFT")
        for line in failures:
            print(f"  {line}")
        print("=" * 78)
        print("RESULT: DRIFTED")
        print()
        print("  The schema in front of this script is not the schema this repository")
        print("  produces. Either a change was made outside the migration set -- the")
        print("  Supabase dashboard is the usual route -- or the baseline is stale.")
        print("  Decide which, because the two have opposite fixes: write the missing")
        print("  migration, or re-run scripts/verify_provisioning.py.")
        print("=" * 78)
        return 1

    print("=" * 78)
    print("RESULT: NO DRIFT")
    print()
    print("  Every compared dimension matches the baseline produced by provisioning")
    print("  an empty database from this repository.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
