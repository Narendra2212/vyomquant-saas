"""Bring up a real PostgreSQL for staging verification and print its DSN.

This is STAGING infrastructure, deliberately separate from anything production.
It uses `pgserver`, which ships actual PostgreSQL server binaries, so the schema,
constraints and RLS behaviour verified against it are PostgreSQL's real behaviour
and not a SQLite approximation.

Usage:
    python staging/bootstrap_pg.py start     # start and print DSN
    python staging/bootstrap_pg.py stop      # shut down
    python staging/bootstrap_pg.py dsn       # print DSN only
    python staging/bootstrap_pg.py verify    # report on the CURRENT DSN, change nothing

    # selecting a cluster (equivalent):
    PG_DATA_DIR=pgdata_r1 python staging/bootstrap_pg.py start
    python staging/bootstrap_pg.py start --data-dir pgdata_r1

    # for a workflow that REQUIRES the provisioned schema:
    python staging/bootstrap_pg.py start --data-dir pgdata_r1 --expect-provisioned


WHY THIS FILE IS MORE CAREFUL THAN IT LOOKS  (Phase 10, Workstream H)
=====================================================================
It used to select the data directory like this::

    DATA_DIR = pathlib.Path(__file__).resolve().parent / os.environ.get("PG_DATA_DIR", "pgdata")

and then, in `start()`, write the resulting DSN to `staging/pg_dsn.txt` unconditionally.
Two things made that unsafe, and together they cost about five hours of shared
test-database downtime and a window in which every PostgreSQL-gated suite was pointed at
an empty database.

1. THE DEFAULT NAMED A DIFFERENT CLUSTER FROM THE PROVISIONED ONE, SILENTLY.
   The correct directory was carried in each caller's environment rather than being a
   property of the cluster, and FOUR names are in circulation across the repository:

       staging/start_pg.ps1                 PG_DATA_DIR=pgdata_r1
       staging/pg_keepalive_r1.py           PG_DATA_DIR=pgdata_r1
       DATABASE_PROVISIONING_RUNBOOK.md     PG_DATA_DIR=pgdata_z1
       PAPER_ONLY_LAUNCH_READINESS.md       PG_DATA_DIR=pgdata_z1
       RLS_TENANT_ISOLATION_VERIFICATION.md PG_DATA_DIR=pgdata_z1
       BILLING_SECURITY_VERIFICATION.md     PG_DATA_DIR=pgdata_local
       FINAL_LAUNCH_READINESS_REPORT.md     PG_DATA_DIR=pgdata_local
       (unset)                              -> "pgdata"

   Meanwhile the command printed by this module's own docstring, by `staging/dsn.py`, and
   by the skip message of roughly eight PG-gated test files is the BARE
   `python staging/bootstrap_pg.py start` -- which took the default. On a machine whose
   provisioned cluster is `pgdata_r1`, that silently `initdb`s a brand-new empty cluster
   in `staging/pgdata` and starts it.

2. THE SHARED DSN WAS REWRITTEN WITH NO CHECK ON WHAT IT NOW POINTED AT.
   `pgserver` allocates a new random port on every start, so the DSN file must be
   rewritten -- but it was rewritten before anything confirmed the server was reachable,
   let alone that it held the schema the suites need. The observed result was
   `staging/pg_dsn.txt` pointing at a cluster with `public tables = 0`, where every
   PG-gated test skips or fails for reasons that have nothing to do with the code under
   test. A green or all-skipped run against the wrong database is the worst output this
   tooling can produce, which is the same argument `staging/dsn.py` already makes for
   announcing its source.

WHAT IT DOES NOW
----------------
* Selection is EXPLICIT, and ambiguity FAILS CLOSED. `PG_DATA_DIR` or `--data-dir` is
  used as given. With neither set, the directory is inferred only when inference cannot
  be wrong -- no existing cluster, or the only existing cluster already IS the default.
  If some other cluster exists, it refuses and lists the candidates, because that is
  exactly the situation in which a silent default picks the wrong one.
* The DSN is written LAST, and only after the started server has been connected to and
  asked `current_setting('data_directory')` -- so the DSN provably serves the directory
  that was requested, rather than whatever else happened to answer on that port.
* The DSN is never DOWNGRADED. If the file currently points at a provisioned cluster and
  the new target is empty or unreachable, the write is refused and the existing file is
  left byte-for-byte alone. `PG_ALLOW_DSN_DOWNGRADE=1` is the deliberate override, for
  the from-scratch provisioning rehearsal this directory is also used for.
* `--expect-provisioned` turns "the schema markers must be present" into a hard
  precondition for workflows that need the provisioned database.
* Nothing here stops a server, deletes a data directory, or runs `initdb` against an
  existing one. `stop` is unchanged and still explicit.

The validating-write rule is not new in spirit: `staging/probe_pg_find.py` already
refuses to rewrite the DSN unless the candidate reports more than 50 public tables. This
puts the same discipline on the path that actually creates the file.

Regression: tests/test_p10h_bootstrap_pg_selection_is_explicit.py
"""
# NOTE: deliberately NO `from __future__ import annotations`.
#
# This module is loaded BY PATH (`importlib.util.spec_from_file_location`) by
# `staging/pg_keepalive_r1.py`, by its test, and by anything else that needs it from a
# directory which is not a package. With postponed annotations every field annotation is a
# string, and `dataclasses` resolves those through `sys.modules[cls.__module__].__dict__`
# while building `TargetState`. A caller that execs the module without first registering it
# in `sys.modules` therefore gets:
#
#     AttributeError: 'NoneType' object has no attribute '__dict__'
#
# ...from inside dataclasses, which is a baffling error for "I imported your file". Real
# annotations cost nothing here (every form used below evaluates at runtime on the
# supported Python) and make the module importable by path with no ceremony. Found by
# loading it exactly that way.
import argparse
import dataclasses
import os
import pathlib
import sys
from typing import Callable, Iterable, Optional, Sequence

STAGING_DIR = pathlib.Path(__file__).resolve().parent
DSN_FILE = STAGING_DIR / "pg_dsn.txt"

#: The documented default, kept so a fresh checkout and CI keep working unchanged. It is
#: used ONLY when choosing it cannot be a mistake -- see `resolve_data_dir`.
DEFAULT_DATA_DIR_NAME = "pgdata"

#: A directory is a PostgreSQL cluster if and only if it carries PG_VERSION. Used instead
#: of a name pattern so a cluster called something unexpected is still found, and a stray
#: empty directory called `pgdata_notes` is not mistaken for one.
CLUSTER_SENTINEL = "PG_VERSION"

#: Tables whose presence distinguishes the PROVISIONED test database from a cluster that
#: merely exists. Drawn from what the Phase 9/10 PG-gated suites actually require, and a
#: superset of the two markers `staging/probe_pg_find.py` checks.
SCHEMA_MARKERS: tuple[str, ...] = (
    "profiles",
    "execution_records",
    "fee_records",
    "referral_payouts",
    "referral_obligations",
    "safety_control_audit",
    "alembic_version",
)

#: `probe_pg_find.py`'s existing threshold for "this is the provisioned instance".
MIN_PROVISIONED_TABLES = 50

# Ordered worst -> best. A write that lowers this rank is a downgrade.
_RANK = {"UNREACHABLE": 0, "EMPTY": 1, "PARTIAL": 2, "PROVISIONED": 3}


class DataDirError(RuntimeError):
    """Selection could not be made safely. Carries the operator-facing explanation."""


class DsnWriteRefused(RuntimeError):
    """The DSN was NOT written, and the existing file is untouched."""


@dataclasses.dataclass(frozen=True)
class TargetState:
    """What a DSN actually points at. `data_directory` comes from the server itself."""

    dsn: str
    reachable: bool
    public_tables: int = 0
    data_directory: Optional[str] = None
    missing_markers: tuple[str, ...] = ()
    error: Optional[str] = None

    @property
    def classification(self) -> str:
        if not self.reachable:
            return "UNREACHABLE"
        if self.public_tables == 0:
            return "EMPTY"
        if self.missing_markers or self.public_tables < MIN_PROVISIONED_TABLES:
            return "PARTIAL"
        return "PROVISIONED"

    @property
    def rank(self) -> int:
        return _RANK[self.classification]

    def describe(self) -> str:
        if not self.reachable:
            return f"{self.classification} ({self.error or 'no connection'})"
        bits = [f"{self.classification}", f"public_tables={self.public_tables}"]
        if self.data_directory:
            bits.append(f"data_directory={self.data_directory}")
        if self.missing_markers:
            bits.append(f"missing={','.join(self.missing_markers)}")
        return "  ".join(bits)


# ─────────────────────────────────────────────────────────────────────────────
#  Selection
# ─────────────────────────────────────────────────────────────────────────────

def list_existing_clusters(staging_dir: pathlib.Path) -> list[str]:
    """Names of directories under `staging_dir` that really are PostgreSQL clusters."""
    if not staging_dir.is_dir():
        return []
    return sorted(
        p.name for p in staging_dir.iterdir()
        if p.is_dir() and (p / CLUSTER_SENTINEL).is_file()
    )


def resolve_data_dir(
    explicit: Optional[str] = None,
    env: Optional[dict] = None,
    staging_dir: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """Decide which data directory to use, or raise `DataDirError` rather than guess.

    Precedence: `--data-dir` > `PG_DATA_DIR` > safe inference. The first two are taken as
    given -- an operator who names a directory means it. Inference is permitted only when
    it cannot select the wrong cluster:

      * no cluster exists yet            -> the documented default, nothing to confuse;
      * the only cluster IS the default  -> the default, same answer either way;
      * any other cluster exists         -> REFUSE and list them.

    The third case is the one that caused the incident: `pgdata_r1` existed and held the
    provisioned schema, and the bare default created and selected `pgdata` instead.
    """
    env = os.environ if env is None else env
    staging_dir = STAGING_DIR if staging_dir is None else staging_dir

    raw = explicit if explicit is not None else env.get("PG_DATA_DIR")
    source = "--data-dir" if explicit is not None else "PG_DATA_DIR"

    if raw is not None and str(raw).strip():
        return _validate_explicit(str(raw).strip(), source, staging_dir)

    if raw is not None and not str(raw).strip():
        # Set but blank is a configuration error, not a request for the default: it is
        # what an unset shell variable expands to in a script that meant to set one.
        raise DataDirError(
            f"{source} is set but empty.\n"
            f"  An empty value is almost always an unset variable expanded by a script, "
            f"so it is refused rather than silently treated as the default.\n"
            f"  Name a directory explicitly, e.g. {source}="
            f"{_suggest(staging_dir)}"
        )

    existing = list_existing_clusters(staging_dir)
    if not existing or existing == [DEFAULT_DATA_DIR_NAME]:
        chosen = staging_dir / DEFAULT_DATA_DIR_NAME
        print(
            f"[bootstrap_pg] PG_DATA_DIR unset; using the default {DEFAULT_DATA_DIR_NAME!r} "
            f"(existing clusters: {existing or 'none'})",
            file=sys.stderr,
        )
        return chosen

    raise DataDirError(
        "refusing to guess which PostgreSQL data directory to use.\n"
        f"  PG_DATA_DIR is unset, so this would have defaulted to "
        f"{DEFAULT_DATA_DIR_NAME!r} -- but these clusters already exist in "
        f"{staging_dir}:\n"
        + "".join(f"      {name}\n" for name in existing)
        + "  Defaulting here is how a provisioned cluster gets bypassed in favour of an "
        "empty one, which then becomes the target of every PG-gated test.\n"
        f"  Choose explicitly:\n"
        f"      PG_DATA_DIR={_suggest(staging_dir)} python staging/bootstrap_pg.py start\n"
        f"      python staging/bootstrap_pg.py start --data-dir {_suggest(staging_dir)}"
    )


def _suggest(staging_dir: pathlib.Path) -> str:
    existing = [n for n in list_existing_clusters(staging_dir) if n != DEFAULT_DATA_DIR_NAME]
    return existing[0] if existing else DEFAULT_DATA_DIR_NAME


def _validate_explicit(raw: str, source: str, staging_dir: pathlib.Path) -> pathlib.Path:
    """An explicit value is honoured, but must name one unambiguous location.

    An absolute path is allowed -- a cluster may legitimately live outside `staging/`. A
    relative path that climbs out with `..` is refused: it resolves differently depending
    on the caller's working directory, which is the ambiguity this workstream exists to
    remove.
    """
    candidate = pathlib.Path(raw)
    if candidate.is_absolute():
        return candidate.resolve()

    if ".." in candidate.parts:
        raise DataDirError(
            f"{source}={raw!r} is a relative path containing '..'.\n"
            f"  It would resolve differently depending on the caller's working "
            f"directory. Use a bare directory name (resolved under {staging_dir}) or an "
            f"absolute path."
        )

    return (staging_dir / candidate).resolve()


# ─────────────────────────────────────────────────────────────────────────────
#  Validation
# ─────────────────────────────────────────────────────────────────────────────

def _default_probe(dsn: str, timeout: int = 10) -> TargetState:
    """Connect and report. Never raises; an unreachable target is a RESULT, not an error."""
    try:
        import psycopg2
    except Exception as e:  # noqa: BLE001  - psycopg2 absent is itself an answer
        return TargetState(dsn=dsn, reachable=False, error=f"psycopg2 unavailable: {e}")

    conn = None
    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        cur = conn.cursor()
        cur.execute(
            "select count(*) from information_schema.tables where table_schema='public'"
        )
        ntables = int(cur.fetchone()[0])
        cur.execute("select current_setting('data_directory')")
        datadir = cur.fetchone()[0]
        missing = []
        for table in SCHEMA_MARKERS:
            cur.execute("select to_regclass(%s)", (f"public.{table}",))
            if cur.fetchone()[0] is None:
                missing.append(table)
        return TargetState(
            dsn=dsn,
            reachable=True,
            public_tables=ntables,
            data_directory=datadir,
            missing_markers=tuple(missing),
        )
    except Exception as e:  # noqa: BLE001
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return TargetState(dsn=dsn, reachable=False, error=f"{type(e).__name__}: {first}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _same_directory(reported: Optional[str], intended: pathlib.Path) -> bool:
    """Does the server's own `data_directory` match what we asked for?

    Compared case-insensitively with separators normalised, because PostgreSQL reports
    forward slashes on Windows while `pathlib` yields backslashes.
    """
    if not reported:
        return False
    try:
        a = pathlib.Path(reported).resolve()
        b = pathlib.Path(intended).resolve()
    except OSError:
        return False
    return str(a).replace("\\", "/").lower() == str(b).replace("\\", "/").lower()


def write_dsn_if_safe(
    new_dsn: str,
    intended_data_dir: pathlib.Path,
    dsn_file: Optional[pathlib.Path] = None,
    probe: Optional[Callable[[str], TargetState]] = None,
    env: Optional[dict] = None,
    expect_provisioned: bool = False,
) -> TargetState:
    """Validate `new_dsn`, then write it -- or raise `DsnWriteRefused` and write nothing.

    The checks, in the order they matter:

      1. The new target must be REACHABLE. A port that accepts a TCP connection is not
         evidence: during the incident the port was open while the postmaster was wedged
         in crash recovery and never completed a handshake.
      2. The new target must serve `intended_data_dir`, according to the SERVER
         (`current_setting('data_directory')`). This is what makes "I asked for
         pgdata_r1" and "the DSN I am recording points at pgdata_r1" the same statement.
      3. If `expect_provisioned`, the schema markers must all be present.
      4. The write must not DOWNGRADE the file: if it currently points at something
         better-provisioned than the new target, refuse. This is the direct guard against
         repointing the shared DSN at a fresh empty cluster.

    On every failure the existing file is left exactly as it was.
    """
    env = os.environ if env is None else env
    dsn_file = DSN_FILE if dsn_file is None else dsn_file
    probe = _default_probe if probe is None else probe

    new_state = probe(new_dsn)

    if not new_state.reachable:
        raise DsnWriteRefused(
            f"not recording the DSN: the server it names is not reachable.\n"
            f"  target: {new_state.describe()}\n"
            f"  staging/pg_dsn.txt left unchanged."
        )

    if not _same_directory(new_state.data_directory, intended_data_dir):
        raise DsnWriteRefused(
            "not recording the DSN: the reachable server is serving a DIFFERENT data "
            "directory than the one requested.\n"
            f"  requested: {intended_data_dir}\n"
            f"  answering: {new_state.data_directory}\n"
            "  Something else is listening on that port, or a stale server answered. "
            "staging/pg_dsn.txt left unchanged."
        )

    if expect_provisioned and new_state.classification != "PROVISIONED":
        raise DsnWriteRefused(
            "not recording the DSN: --expect-provisioned was requested but the cluster "
            "does not carry the provisioned schema.\n"
            f"  target: {new_state.describe()}\n"
            f"  missing markers: {', '.join(new_state.missing_markers) or '(none)'}\n"
            "  Provision it with scripts/provision_database.py, or drop "
            "--expect-provisioned if an empty cluster is genuinely intended.\n"
            "  staging/pg_dsn.txt left unchanged."
        )

    current_text = ""
    if dsn_file.exists():
        current_text = dsn_file.read_text(encoding="utf-8").strip()

    if current_text and current_text != new_dsn:
        current_state = probe(current_text)
        allow_downgrade = str(env.get("PG_ALLOW_DSN_DOWNGRADE", "")).strip().lower() in (
            "1", "true", "yes", "on",
        )
        if new_state.rank < current_state.rank and not allow_downgrade:
            raise DsnWriteRefused(
                "not recording the DSN: it would REPLACE a better-provisioned target.\n"
                f"  current:  {current_state.describe()}\n"
                f"  proposed: {new_state.describe()}\n"
                "  Every PG-gated suite reads this file, so repointing it at a less "
                "provisioned cluster makes those tests skip or fail for reasons "
                "unrelated to the code under test.\n"
                "  If a from-scratch rehearsal really is intended, say so explicitly:\n"
                "      PG_ALLOW_DSN_DOWNGRADE=1\n"
                "  staging/pg_dsn.txt left unchanged."
            )
        if new_state.rank < current_state.rank:
            print(
                f"[bootstrap_pg] PG_ALLOW_DSN_DOWNGRADE set; replacing "
                f"{current_state.classification} with {new_state.classification}",
                file=sys.stderr,
            )

    dsn_file.write_text(new_dsn, encoding="utf-8")
    return new_state


# ─────────────────────────────────────────────────────────────────────────────
#  Commands
# ─────────────────────────────────────────────────────────────────────────────

def _server(data_dir: pathlib.Path):
    import pgserver

    data_dir.mkdir(parents=True, exist_ok=True)
    return pgserver.get_server(str(data_dir), cleanup_mode=None)


def start(
    explicit_data_dir: Optional[str] = None,
    expect_provisioned: bool = False,
    server_factory: Optional[Callable[[pathlib.Path], object]] = None,
    probe: Optional[Callable[[str], TargetState]] = None,
    dsn_file: Optional[pathlib.Path] = None,
    env: Optional[dict] = None,
) -> str:
    """Start the selected cluster and record its DSN, in that order, both validated."""
    data_dir = resolve_data_dir(explicit=explicit_data_dir, env=env)
    factory = _server if server_factory is None else server_factory

    print(f"[bootstrap_pg] data directory: {data_dir}", file=sys.stderr)
    srv = factory(data_dir)
    dsn = srv.get_uri()

    state = write_dsn_if_safe(
        dsn,
        intended_data_dir=data_dir,
        dsn_file=dsn_file,
        probe=probe,
        env=env,
        expect_provisioned=expect_provisioned,
    )

    print("POSTGRES STARTED")
    print("DATA_DIR:", data_dir)
    print("DSN_FILE:", DSN_FILE if dsn_file is None else dsn_file)
    print("TARGET:", state.describe())
    return dsn


def stop(explicit_data_dir: Optional[str] = None, env: Optional[dict] = None) -> None:
    """Shut down the selected cluster. Selection is as explicit as for `start`."""
    data_dir = resolve_data_dir(explicit=explicit_data_dir, env=env)
    print(f"[bootstrap_pg] data directory: {data_dir}", file=sys.stderr)
    _server(data_dir).cleanup()
    print("POSTGRES STOPPED")


def verify(dsn_file: Optional[pathlib.Path] = None,
           probe: Optional[Callable[[str], TargetState]] = None) -> TargetState:
    """Report on whatever the DSN file currently names. Changes nothing.

    Exists so "is my test database the one I think it is?" has an answer that does not
    involve starting or restarting anything.
    """
    dsn_file = DSN_FILE if dsn_file is None else dsn_file
    probe = _default_probe if probe is None else probe
    if not dsn_file.exists():
        print(f"{dsn_file} does not exist; nothing to verify.")
        return TargetState(dsn="", reachable=False, error="no DSN file")
    text = dsn_file.read_text(encoding="utf-8").strip()
    state = probe(text)
    print(f"DSN_FILE: {dsn_file}")
    print(f"TARGET:   {state.describe()}")
    return state


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bootstrap_pg.py",
        description="Start/stop the staging PostgreSQL and record its DSN safely.",
    )
    parser.add_argument(
        "action", nargs="?", default="start",
        choices=("start", "stop", "dsn", "verify"),
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Cluster to use. A bare name resolves under staging/; an absolute path is "
             "used as given. Overrides PG_DATA_DIR.",
    )
    parser.add_argument(
        "--expect-provisioned", action="store_true",
        help="Fail unless the started cluster carries the provisioned schema markers.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    expect = args.expect_provisioned or str(
        os.environ.get("PG_EXPECT_PROVISIONED", "")
    ).strip().lower() in ("1", "true", "yes", "on")

    try:
        if args.action == "start":
            start(explicit_data_dir=args.data_dir, expect_provisioned=expect)
        elif args.action == "stop":
            stop(explicit_data_dir=args.data_dir)
        elif args.action == "dsn":
            if not DSN_FILE.exists():
                print(f"{DSN_FILE} does not exist; run 'start' first.", file=sys.stderr)
                return 1
            print(DSN_FILE.read_text(encoding="utf-8").strip())
        elif args.action == "verify":
            return 0 if verify().reachable else 1
    except DataDirError as e:
        print(f"bootstrap_pg: {e}", file=sys.stderr)
        return 2
    except DsnWriteRefused as e:
        print(f"bootstrap_pg: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
