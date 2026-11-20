"""Which ROLE does the application connect as? That decides whether RLS applies at all.

PHASE 11B / WORKSTREAM 1B — read-only, and it does NOT print secrets.

WHY THIS MATTERS
----------------
The residue sweep measured role attributes on the staging cluster:

    postgres       super=True   bypassrls=True
    authenticated  super=False  bypassrls=False
    anon           super=False  bypassrls=False
    service_role   super=False  bypassrls=True     <-- bypasses RLS

`rolbypassrls` is checked against the CURRENT role. So:

  * a session running as `authenticated` or `anon` IS subject to RLS -- those roles are
    non-superuser, non-BYPASSRLS, and do not own the 89 public tables;
  * a session running as `service_role` is NOT subject to RLS, on any table, regardless
    of how many policies exist;
  * a session running as `postgres` is not subject to RLS either.

The tenant-isolation suites adopt `authenticated`/`anon` with `SET LOCAL ROLE`, so their
assertions do exercise policies. But that only transfers to production if production also
reaches the database as a non-BYPASSRLS role. If production connects as `service_role` or
as the table owner, the 195 policies are inert in production and the suites are measuring
a path production never takes.

SECRET HANDLING
---------------
This reads `.env`-style files and settings modules. It prints ONLY the username, host
shape, port and database name parsed out of a connection string, and the NAMES of the
variables it found. Passwords, keys and tokens are never printed, and values are reported
as a length plus a SHA-256 prefix so two configs can be compared without disclosure.

STRICTLY READ-ONLY: reads files, parses, prints. No database connection at all.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys
from urllib.parse import urlparse

REPO = pathlib.Path(__file__).resolve().parents[1]

ENV_FILES = [REPO / ".env", REPO / ".env.example", REPO / ".env.staging.example"]
DB_VAR_RE = re.compile(
    r"^(?P<key>[A-Z0-9_]*(?:DATABASE|DB|POSTGRES|PG|SUPABASE)[A-Z0-9_]*)\s*=\s*"
    r"(?P<value>.*)$")

SENSITIVE = ("KEY", "SECRET", "PASSWORD", "TOKEN", "JWT", "ANON", "SERVICE_ROLE")


def _fingerprint(value: str) -> str:
    if not value:
        return "(empty)"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"len={len(value)} sha256:{digest}"


def _describe_dsn(value: str) -> str:
    """Username / host / port / database only. Password is never reproduced."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return f"(unparsable) {_fingerprint(value)}"
    if not parsed.scheme:
        return f"(not a URL) {_fingerprint(value)}"
    user = parsed.username or "(none)"
    host = parsed.hostname or "(none)"
    port = parsed.port
    db = (parsed.path or "").lstrip("/") or "(none)"
    pw = "present" if parsed.password else "absent"
    return (f"scheme={parsed.scheme} user={user!r} host={host} port={port} "
            f"db={db!r} password={pw}")


def main() -> int:
    print("=" * 78)
    print("WHICH ROLE DOES THE APPLICATION CONNECT AS?")
    print("=" * 78)
    print("(usernames, hosts and database names only -- no secret values are printed)\n")

    for path in ENV_FILES:
        print(f"-- {path.name} " + "-" * (70 - len(path.name)))
        if not path.exists():
            print("   absent\n")
            continue
        found = 0
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            m = DB_VAR_RE.match(line)
            if not m:
                continue
            found += 1
            key = m.group("key")
            value = m.group("value").strip().strip('"').strip("'")
            if any(tok in key for tok in SENSITIVE):
                print(f"   {key:34} {_fingerprint(value)}  [redacted by name]")
            elif "://" in value:
                print(f"   {key:34} {_describe_dsn(value)}")
            else:
                print(f"   {key:34} {value!r}" if len(value) < 60
                      else f"   {key:34} {_fingerprint(value)}")
        if not found:
            print("   no database-related variables")
        print()

    print("-- how the backend builds its engine " + "-" * 41)
    hits: list[tuple[str, int, str]] = []
    for pattern in ("create_engine", "create_async_engine", "DATABASE_URL",
                    "SUPABASE_SERVICE_ROLE", "service_role", "postgresql+",
                    "SUPABASE_URL"):
        for path in sorted((REPO / "backend_app").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern in line and not line.strip().startswith("#"):
                    rel = str(path.relative_to(REPO)).replace("\\", "/")
                    hits.append((rel, i, line.strip()[:116]))
    seen = set()
    count = 0
    for rel, line, text in hits:
        key = (rel, line)
        if key in seen:
            continue
        seen.add(key)
        count += 1
        if count > 45:
            continue
        print(f"   {rel}:{line}")
        print(f"       {text}")
    print(f"\n   total distinct call sites: {len(seen)}"
          f"{' (first 45 shown)' if len(seen) > 45 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
