"""Find the live provisioned PostgreSQL across candidate ports and record its DSN -- safely.

`pgserver` allocates a NEW random port on every start, so after a restart
`staging/pg_dsn.txt` names a port nothing is listening on. This script answers "where did
it go?" by probing candidates and, when it finds the intended cluster, recording the DSN.

It never starts, stops or restarts a server. Candidate discovery is entirely read-only:
it reads the current DSN file, reads `postmaster.pid` inside the intended data directory,
and tries any ports given on the command line. Reading a pid file is not starting a
database.


WHY THIS NO LONGER WRITES THE DSN ITSELF  (Phase 10, Workstream I)
==================================================================
It used to end with::

    if n and n > 50:
        dsn_file.write_text(dsn, encoding="utf-8")

which was the *only* guarded DSN writer in the tree at the time, and that guard was real
-- it is why this script never caused the Workstream H incident. But it was a SECOND,
WEAKER implementation of DSN safety sitting next to `bootstrap_pg.write_dsn_if_safe`, and
it was missing three of that helper's four checks:

  * it never asked the server `current_setting('data_directory')`, so a DIFFERENT
    provisioned cluster answering on a recycled port would have been accepted as "the"
    instance and recorded. `pgserver` hands out random high ports and this repository now
    carries two clusters, so that is a reachable mistake, not a theoretical one;
  * it had no downgrade guard, so it could replace a better-provisioned target;
  * its "provisioned" test was a bare `> 50` table count with no schema markers, so a
    cluster with 60 unrelated tables qualified.

Two definitions of "safe to record" is one too many: whichever is weaker sets the real
guarantee. So discovery stays here and the WRITE DECISION moves wholesale to
`write_dsn_if_safe`, which connects, confirms the server is serving the requested data
directory, applies the shared classification and marker rules, refuses an unauthorised
downgrade, and leaves the file byte-for-byte untouched on any refusal.

The `> 50` threshold is not lost: it survives as `bootstrap_pg.MIN_PROVISIONED_TABLES`,
which is where this script's rule was adopted from in the first place.

Usage:
    python staging/probe_pg_find.py                      # find pgdata_r1, record if valid
    python staging/probe_pg_find.py --dry-run            # report only, never write
    python staging/probe_pg_find.py --data-dir pgdata     # look for a different cluster
    python staging/probe_pg_find.py --port 51805 --port 49323
    PG_ALLOW_DSN_DOWNGRADE=1 python staging/probe_pg_find.py --data-dir pgdata

Regression: tests/test_p10i_probe_pg_find_uses_shared_dsn_contract.py
"""
# No `from __future__ import annotations`: this module is loaded by path in its tests, and
# the sibling `bootstrap_pg` documents why postponed annotations are a trap there.
import argparse
import importlib.util
import pathlib
import sys
from typing import List, Optional, Sequence

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGING = ROOT / "staging"
DSN_FILE = STAGING / "pg_dsn.txt"

#: The cluster this script exists to locate. Explicit, for the same reason Workstream H
#: made `bootstrap_pg`'s selection explicit: the name must not be implied by a default
#: that happens to be wrong on this machine.
DEFAULT_INTENDED_DIR = "pgdata_r1"

#: Ports seen in earlier bootstrap attempts, kept so the script still has something to try
#: on a machine where the pid file is gone. Superseded in practice by `_port_from_pid_file`
#: below, which is authoritative when the server is running.
HISTORICAL_PORTS = (64780, 60076, 56595, 51805, 49323)


def _load_bootstrap():
    """`staging/` is not a package, so the sibling module is loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "_probe_find_bootstrap_pg", STAGING / "bootstrap_pg.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _dsn_for_port(port):
    return f"postgresql://postgres:@127.0.0.1:{port}/postgres"


def _port_from_pid_file(data_dir):
    """The port a running postmaster recorded, read from `postmaster.pid` line 4.

    Authoritative when the server is up, and self-maintaining -- unlike a hardcoded list
    somebody has to remember to update. Returns None if the file is absent or malformed.
    READ ONLY: this does not tell us the server is alive, only what port it claimed; the
    connection attempt is what decides that.
    """
    pid_file = pathlib.Path(data_dir) / "postmaster.pid"
    try:
        lines = pid_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) < 4:
        return None
    port = lines[3].strip()
    return int(port) if port.isdigit() else None


def candidate_dsns(
    data_dir,
    dsn_file=None,
    extra_ports=(),
    historical_ports=HISTORICAL_PORTS,
):
    """Ordered, de-duplicated candidates. Most-likely first, and nothing is started.

    Order: explicit `--port` values, then the port the intended cluster's own pid file
    claims, then whatever the DSN file currently says, then the historical list.
    """
    dsn_file = DSN_FILE if dsn_file is None else dsn_file
    out = []

    def add(dsn):
        if dsn and dsn not in out:
            out.append(dsn)

    for port in extra_ports:
        add(_dsn_for_port(port))

    pid_port = _port_from_pid_file(data_dir)
    if pid_port is not None:
        add(_dsn_for_port(pid_port))

    if dsn_file.exists():
        try:
            add(dsn_file.read_text(encoding="utf-8").strip())
        except OSError:
            pass

    for port in historical_ports:
        add(_dsn_for_port(port))

    return out


def find_and_record(
    intended_dir_name=DEFAULT_INTENDED_DIR,
    extra_ports=(),
    dry_run=False,
    expect_provisioned=True,
    dsn_file=None,
    staging_dir=None,
    bootstrap=None,
    probe=None,
    env=None,
):
    """Probe candidates, report each, and hand the winner to `write_dsn_if_safe`.

    Returns a process exit code: 0 recorded (or would record, under --dry-run), 1 nothing
    suitable found, 3 a candidate was found but the shared helper refused to record it.

    The write decision is NOT made here. This function selects a candidate and the shared
    helper adjudicates it, so there is exactly one definition of "safe to record".
    """
    bp = _load_bootstrap() if bootstrap is None else bootstrap
    dsn_file = (bp.DSN_FILE if dsn_file is None else dsn_file)
    staging_dir = (bp.STAGING_DIR if staging_dir is None else staging_dir)
    probe = bp._default_probe if probe is None else probe

    intended = bp.resolve_data_dir(explicit=intended_dir_name, env={},
                                   staging_dir=staging_dir)

    print("=" * 78)
    print("PROBE: locating the provisioned PostgreSQL (no server is started)")
    print("=" * 78)
    print(f"intended data directory : {intended}")
    print(f"DSN file                : {dsn_file}")
    print(f"mode                    : {'DRY RUN (never writes)' if dry_run else 'record if valid'}")

    candidates = candidate_dsns(intended, dsn_file=dsn_file, extra_ports=extra_ports)
    print(f"candidates              : {len(candidates)}")
    print("-" * 78)

    winner = None
    for dsn in candidates:
        state = probe(dsn)
        port = dsn.rsplit(":", 1)[-1].split("/")[0]
        if not state.reachable:
            print(f"  port {port:<6} UNREACHABLE   {state.error}")
            continue

        serves_intended = bp._same_directory(state.data_directory, intended)
        note = "" if serves_intended else "  <- serves a DIFFERENT data directory"
        print(f"  port {port:<6} {state.classification:<12} "
              f"public_tables={state.public_tables} "
              f"data_directory={state.data_directory}{note}")
        if state.missing_markers:
            print(f"                  missing markers: {', '.join(state.missing_markers)}")

        if winner is None and serves_intended and (
            state.classification == "PROVISIONED" or not expect_provisioned
        ):
            winner = dsn

    print("-" * 78)
    if winner is None:
        print("NO SUITABLE CANDIDATE.")
        print("  Nothing was recorded and the existing DSN is untouched.")
        print("  Every reachable candidate either served a different data directory or "
              "did not carry the provisioned schema.")
        print(f"  To bring the intended cluster up:")
        print(f"      python staging/bootstrap_pg.py start "
              f"--data-dir {intended_dir_name} --expect-provisioned")
        return 1

    if dry_run:
        print(f"DRY RUN: would record {winner}")
        print("  Nothing was written.")
        return 0

    try:
        state = bp.write_dsn_if_safe(
            winner,
            intended_data_dir=intended,
            dsn_file=dsn_file,
            probe=probe,
            env=env,
            expect_provisioned=expect_provisioned,
        )
    except bp.DsnWriteRefused as e:
        print("REFUSED by the shared DSN safety contract:")
        print(f"  {e}")
        return 3

    print(f"RECORDED {winner}")
    print(f"  {state.describe()}")
    return 0


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="probe_pg_find.py",
        description="Locate the provisioned staging PostgreSQL and record its DSN safely.",
    )
    parser.add_argument(
        "--data-dir", default=DEFAULT_INTENDED_DIR,
        help=f"Cluster to look for (default: {DEFAULT_INTENDED_DIR}). A bare name "
             f"resolves under staging/.",
    )
    parser.add_argument(
        "--port", type=int, action="append", default=[], dest="ports",
        help="Additional port to try first. Repeatable.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report findings and never write the DSN file.",
    )
    parser.add_argument(
        "--any-provisioning", action="store_true",
        help="Accept a reachable cluster serving the intended directory even without the "
             "provisioned schema markers. The shared downgrade guard still applies.",
    )
    return parser


def main(argv=None):
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    return find_and_record(
        intended_dir_name=args.data_dir,
        extra_ports=tuple(args.ports),
        dry_run=args.dry_run,
        expect_provisioned=not args.any_provisioning,
    )


if __name__ == "__main__":
    raise SystemExit(main())
