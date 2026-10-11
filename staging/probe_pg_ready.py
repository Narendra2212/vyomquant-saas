"""Is the local PostgreSQL reachable, and is it the database the suites need?

Run before a PostgreSQL-lane regression so a lane that cannot measure anything is not
launched and then reported.

READ-ONLY. It issues `SELECT`s and nothing else: it never starts, stops or restarts a
server, never writes or repoints `staging/pg_dsn.txt`, and applies no migrations.


WHY THIS NOW DEFERS TO THE SHARED CONTRACT  (Phase 10, Workstream J)
====================================================================
Workstream I recorded this script as the last inconsistency in the PostgreSQL tooling: it
"reads the DSN without classifying it [...] so it can report PG_READY=True against a
cluster the shared contract would call PARTIAL". Reading the code found that, and three
more things, of which the first is worse than what was reported:

1. `PG_READY=True` WAS PRINTED UNCONDITIONALLY. The table loop was::

       present = cur.fetchone()[0] > 0
       print(f"TABLE {schema}.{table}={'PRESENT' if present else 'ABSENT'}")

   `present` was computed, printed, and then DISCARDED. Nothing gated the final
   `print("PG_READY=True")`. So a cluster missing ten of the eleven tables this probe
   checks still reported ready, provided `paper_order_allowed_transitions` happened to
   exist -- because that one table was queried directly afterwards and an exception was
   the only thing that could stop the script. "Ready" meant "the process did not crash".

2. A SECOND DEFINITION OF READINESS. The hand-maintained list was disjoint from
   `bootstrap_pg.SCHEMA_MARKERS`, so "provisioned" meant one thing to `bootstrap_pg.py`
   and `probe_pg_find.py` and another thing here. Workstreams H and I exist because two
   definitions of a safety property means the weaker one is the real one.

3. AN UNREACHABLE SERVER PRODUCED A TRACEBACK, not a verdict. `psycopg2.connect` was
   unguarded, so "the database is down" -- the single most likely reason to run this --
   exited with a stack trace instead of a readable answer.

4. IT BYPASSED THE SHARED DSN RESOLVER. It read `staging/pg_dsn.txt` directly, so
   `AERORA_TEST_DATABASE_URL` did not win, even though that variable is what
   `tests/conftest.py` requires before a test process may talk to a real server and what
   `staging/dsn.py` documents as taking precedence. The probe and the suite it gates could
   therefore examine different databases.

WHAT IT DOES NOW
----------------
* The DSN comes from `staging/dsn.py::resolve_dsn()` -- the authoritative resolver, which
  also announces which source won.
* Classification comes from `bootstrap_pg._default_probe` and `TargetState.classification`
  -- the same UNREACHABLE / EMPTY / PARTIAL / PROVISIONED ladder the DSN write contract
  uses, with the same `SCHEMA_MARKERS` and the same `MIN_PROVISIONED_TABLES`. No marker
  list is redefined here.
* `PG_READY=True` requires the shared classification to be **PROVISIONED**, and the
  verdict is now derived from the measurements rather than from surviving to the last line.

WHY THE LANE TABLE LIST SURVIVES, AND IS NOT THE GATE
-----------------------------------------------------
The obvious change -- delete the hand-written list and use `SCHEMA_MARKERS` -- would have
LOST coverage. `SCHEMA_MARKERS` is about the provisioned test database in general
(`profiles`, `execution_records`, `fee_records`, `referral_payouts`,
`referral_obligations`, `safety_control_audit`, `alembic_version`) and contains NO
paper-trading table. The eleven relations below are what the paper-trading lane
specifically needs, which is the reason this probe exists.

So they are kept as an ADDITIONAL REQUIREMENT layered on top of the shared classification,
not as a competing definition of "provisioned". `PG_READY=True` needs both: the shared
contract says PROVISIONED, and the lane's own relations are present. Either one failing is
reported separately, so it is always clear which gate refused.

Usage:
    python staging/probe_pg_ready.py
    python staging/probe_pg_ready.py --data-dir pgdata_r1   # also assert directory identity

Exit codes:  0 ready;  1 not ready.

Regression: tests/test_p10j_probe_pg_ready_uses_shared_classification.py
"""
# No `from __future__ import annotations`: consistent with `bootstrap_pg.py`, which
# documents why postponed annotations are a trap for modules loaded by file path.
import argparse
import importlib.util
import pathlib
import sys

STAGING = pathlib.Path(__file__).resolve().parent
ROOT = STAGING.parent

#: Relations the PAPER-TRADING LANE needs, over and above the shared provisioned-database
#: markers. NOT a definition of "provisioned" -- see the module docstring. `(schema, name)`
#: because `auth.users` is not in `public`.
LANE_RELATIONS = (
    ("public", "paper_accounts"),
    ("public", "paper_orders"),
    ("public", "paper_fills"),
    ("public", "paper_balance_events"),
    ("public", "paper_positions"),
    ("public", "paper_order_allowed_transitions"),
    ("public", "library_strategies"),
    ("public", "strategies"),
    ("public", "library_ratings"),
    ("public", "positions"),
    ("auth", "users"),
)


def _load(module_name, filename):
    """`staging/` is not a package, so siblings are loaded by path."""
    spec = importlib.util.spec_from_file_location(module_name, STAGING / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def lane_facts(dsn, relations=LANE_RELATIONS, timeout=10):
    """The lane-specific measurements the shared classification does not cover.

    READ-ONLY (`SELECT` only) and NON-RAISING, mirroring `bootstrap_pg._default_probe`'s
    contract so that "the database is down" is a result rather than a traceback.

    Returns a dict with `present` (`{(schema, name): bool}`), `transition_rows`
    (`int | None`), `server_version` (`str | None`) and `error` (`str | None`).
    """
    out = {"present": {}, "transition_rows": None, "server_version": None, "error": None}
    try:
        import psycopg2
    except Exception as e:  # noqa: BLE001
        out["error"] = f"psycopg2 unavailable: {e}"
        return out

    conn = None
    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        cur = conn.cursor()

        # Retained so the `SERVER=` output line keeps its original meaning.
        cur.execute("SELECT version()")
        out["server_version"] = cur.fetchone()[0].split(",")[0]

        for schema, table in relations:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (schema, table),
            )
            out["present"][(schema, table)] = cur.fetchone()[0] > 0

        # Guarded. This used to be an unconditional `SELECT count(*)` against the table,
        # so an absent transition table raised UndefinedTable and the script died here
        # rather than reporting it.
        if out["present"].get(("public", "paper_order_allowed_transitions")):
            cur.execute("SELECT count(*) FROM public.paper_order_allowed_transitions")
            out["transition_rows"] = int(cur.fetchone()[0])
        return out
    except Exception as e:  # noqa: BLE001
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        out["error"] = f"{type(e).__name__}: {first}"
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def evaluate(state, lane_present, intended_data_dir=None, same_directory=None):
    """The verdict, as a pure function of the measurements.

    Separated from I/O so the decision is testable without a database. Returns
    `(ready: bool, reasons: list[str])`; `reasons` is empty exactly when ready.
    """
    reasons = []

    if state.classification != "PROVISIONED":
        detail = state.error if not state.reachable else (
            f"public_tables={state.public_tables}"
            + (f" missing_markers={','.join(state.missing_markers)}"
               if state.missing_markers else "")
        )
        reasons.append(
            f"shared classification is {state.classification}, not PROVISIONED ({detail})"
        )

    missing_lane = sorted(f"{s}.{t}" for (s, t), ok in lane_present.items() if not ok)
    if missing_lane:
        reasons.append(f"lane relations absent: {', '.join(missing_lane)}")
    elif not lane_present:
        reasons.append("lane relations could not be checked")

    if intended_data_dir is not None:
        matches = same_directory(state.data_directory, intended_data_dir)
        if not matches:
            reasons.append(
                f"server reports data_directory={state.data_directory!r}, "
                f"which is not the requested {str(intended_data_dir)!r}"
            )

    return (not reasons), reasons


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="probe_pg_ready.py",
        description="Report whether the configured PostgreSQL is ready for the PG lane. "
                    "Read-only; starts nothing and never writes the DSN.",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Also require that the server reports THIS data directory. A bare name "
             "resolves under staging/.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    bp = _load("_pgready_bootstrap_pg", "bootstrap_pg.py")
    dsn_mod = _load("_pgready_dsn", "dsn.py")

    # The authoritative resolver: AERORA_TEST_DATABASE_URL beats staging/pg_dsn.txt, and it
    # announces which one won. Exits with instructions if neither is available.
    dsn = dsn_mod.resolve_dsn()

    port = dsn.rsplit(":", 1)[-1].split("/")[0]
    print(f"DSN_PORT={port}")

    state = bp._default_probe(dsn)
    facts = lane_facts(dsn)

    # `SERVER=`, the `TABLE ...=PRESENT|ABSENT` lines and `ALLOWED_TRANSITION_ROWS=` keep
    # their original spelling so previously captured runs (staging/probe_PGR.txt) remain
    # comparable. The CLASSIFICATION / PUBLIC_TABLES / DATA_DIRECTORY / MISSING_MARKERS /
    # NOT_READY lines are additions.
    print(f"SERVER={facts['server_version'] or 'unavailable'}")
    print(f"CLASSIFICATION={state.classification}")
    print(f"PUBLIC_TABLES={state.public_tables}")
    print(f"DATA_DIRECTORY={state.data_directory}")
    print(f"MISSING_MARKERS={','.join(state.missing_markers) or 'none'}")
    if not state.reachable:
        print(f"ERROR={state.error}")

    lane_present = facts["present"]
    for schema, table in LANE_RELATIONS:
        got = lane_present.get((schema, table))
        label = "PRESENT" if got else ("ABSENT" if got is not None else "UNKNOWN")
        print(f"TABLE {schema}.{table}={label}")
    rows = facts["transition_rows"]
    print(f"ALLOWED_TRANSITION_ROWS={rows if rows is not None else 'n/a'}")
    if facts["error"]:
        print(f"LANE_ERROR={facts['error']}")

    intended = None
    if args.data_dir is not None:
        intended = bp.resolve_data_dir(explicit=args.data_dir, env={})
        print(f"REQUESTED_DATA_DIRECTORY={intended}")

    ready, reasons = evaluate(
        state, lane_present, intended_data_dir=intended,
        same_directory=bp._same_directory,
    )

    for reason in reasons:
        print(f"NOT_READY={reason}")
    print(f"PG_READY={ready}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
