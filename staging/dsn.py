"""One place that answers "which database should I check?".

Every probe in this directory needs the answer and several of them used to hardcode
`(REPO / "staging" / "pg_dsn.txt").read_text()`. That file is a LOCAL artefact --
`.gitignore` excludes it -- so any probe reading it unconditionally could not run in CI,
which is where these checks are worth the most. The `provisioning-and-isolation` job in
`.github/workflows/01-pr-check.yml` sets `AERORA_TEST_DATABASE_URL` and has no such file.

Order of precedence, and the reason for it:

  1. ``AERORA_TEST_DATABASE_URL`` -- explicit beats implicit, and it is the variable
     `tests/conftest.py` already requires before it will let a test process talk to a
     real server, so a probe and a test run against the same database by default.
  2. ``staging/pg_dsn.txt`` -- written by `staging/bootstrap_pg.py start`, which is how a
     local server comes into existence. Convenient, and only convenient.
  3. exit with instructions. Deliberately not a silent default to localhost:5432: a probe
     that reports "0 leaked" against whatever happens to be listening on 5432 is worse
     than one that refuses to run.
"""

from __future__ import annotations

import os
import pathlib
import sys
import urllib.parse

_LOCAL_DSN_FILE = pathlib.Path(__file__).resolve().parent / "pg_dsn.txt"


def redact(dsn: str) -> str:
    """A DSN safe to print: scheme, host, port, database. Never userinfo.

    Duplicated from `scripts/verify_schema_drift.py` rather than imported, because
    `staging/` must not depend on `scripts/` import paths resolving -- these probes run
    with the script directory as `sys.path[0]` and nothing else guaranteed.
    """
    try:
        parsed = urllib.parse.urlsplit(dsn)
    except ValueError:
        return "<unparseable DSN, withheld>"
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}{parsed.path or ''}"


def resolve_dsn(announce: bool = True) -> str:
    """The database to check, and -- out loud -- which source said so.

    ANNOUNCING THE SOURCE IS NOT COSMETIC.
    A stale `AERORA_TEST_DATABASE_URL` inherited from a parent shell silently beat the
    DSN file during this work and pointed a probe at a server that no longer existed.
    The connection was refused, so that instance was merely confusing. The dangerous
    version of the same mistake is an env var that points at a DIFFERENT LIVE DATABASE:
    every probe would run, pass, and report on a database nobody intended to check.

    An exit-0 "all green" against the wrong database is the worst possible output of
    this tooling, so the target is printed, redacted, every time -- and the source is
    named, because "which of the two won" is the part that is otherwise invisible.
    """
    env = os.environ.get("AERORA_TEST_DATABASE_URL")
    if env and env.strip():
        dsn = env.strip()
        if announce:
            print(f"[dsn] AERORA_TEST_DATABASE_URL -> {redact(dsn)}", file=sys.stderr)
        return dsn

    if _LOCAL_DSN_FILE.exists():
        text = _LOCAL_DSN_FILE.read_text(encoding="utf-8").strip()
        if text:
            if announce:
                print(f"[dsn] staging/pg_dsn.txt -> {redact(text)}", file=sys.stderr)
            return text

    # The data directory is named EXPLICITLY. This used to print the bare
    # `python staging/bootstrap_pg.py start`, which took `bootstrap_pg`'s default
    # `pgdata` -- a different cluster from the provisioned `pgdata_r1` on this machine.
    # Since Phase 10 / Workstream H that bare form fails closed rather than silently
    # creating an empty cluster, so printing it here would hand the reader a command that
    # exits 2. Phase 10 / Workstream I.
    sys.exit(
        "no database to check.\n"
        "  Set AERORA_TEST_DATABASE_URL, or start the provisioned local server with\n"
        "      python staging/bootstrap_pg.py start --data-dir pgdata_r1 "
        "--expect-provisioned\n"
        "  which writes staging/pg_dsn.txt.\n"
        "  If it is already running but on a new port, locate it with\n"
        "      python staging/probe_pg_find.py"
    )
