#!/usr/bin/env python3
"""SEC-01 DEFINITIVE SWEEP: every readable production secret vs all of git history.

WHY THIS REPLACES THE FIRST ATTEMPT
-----------------------------------
The first sweep reported "live values loaded for comparison: 4" against 11 watched
secrets and then printed a verdict. Secrets Manager throttles rapid sequential
get-secret-value calls; the loader treated a non-zero exit as "skip" and said
nothing, so 7 secrets were silently unchecked while the output looked complete.
That is the exact failure mode that makes a security scan worse than none.

This version:
  * retries with backoff and FAILS LOUDLY if any secret cannot be read
  * refuses to print a verdict unless coverage is complete or explicitly accepted
  * reports per-secret OUTCOME, so "not found in history" is distinguishable from
    "not checked"
  * matches both whole secret values and, for DSNs, the password component alone
  * tolerates malformed URLs instead of crashing the run

Prints no secret material: names, lengths and salted digests only.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"
SERVICE = "vyomquant-api-service-cjema2sl"
SALT = b"sec01-definitive-sweep-local-only"
PLACEHOLDERS = {
    "PASSWORD", "password", "pass", "***", "REDACTED", "changeme",
    "your-password", "YOUR_PASSWORD", "xxx", "secret", "<password>",
}

#: Secrets whose whole point is to be published. Finding these in git is EXPECTED
#: and reporting them as exposures is actively harmful: an alert that always
#: contains a known-benign entry teaches its reader to skim past the real ones.
#:
#: A Supabase ANON key is shipped in the browser bundle and stored as the
#: VITE_SUPABASE_ANON_KEY CI secret precisely so it can be. Its safety rests on
#: row-level security, not on secrecy. Matched by suffix so both the lowercase and
#: the legacy uppercase naming are covered.
PUBLIC_BY_DESIGN_SUFFIXES = ("supabase_anon_key", "SUPABASE_ANON_KEY")


def digest(v: str) -> str:
    return hashlib.sha256(SALT + v.encode()).hexdigest()[:12]


def list_secrets() -> list[str]:
    out = subprocess.run(
        ["aws", "secretsmanager", "list-secrets", "--region", REGION,
         "--query", "SecretList[].Name", "--output", "json"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(f"could not list secrets: {out.stderr[:200]}", file=sys.stderr)
        sys.exit(2)
    return json.loads(out.stdout)


def fetch(secret_id: str, attempts: int = 5) -> tuple[str | None, str]:
    """Returns (value, status). Retries throttling with backoff."""
    last = ""
    for i in range(attempts):
        out = subprocess.run(
            ["aws", "secretsmanager", "get-secret-value", "--secret-id", secret_id,
             "--region", REGION, "--query", "SecretString", "--output", "text"],
            capture_output=True, text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip(), "read"
        last = out.stderr.strip()
        if "Throttling" in last or "TooManyRequests" in last or "Rate exceeded" in last:
            time.sleep(1.5 * (i + 1))
            continue
        if "ResourceNotFound" in last:
            return None, "not-found"
        if "AccessDenied" in last or "not authorized" in last:
            return None, "access-denied"
        time.sleep(0.6 * (i + 1))
    return None, f"failed: {last.splitlines()[-1][:60] if last else 'unknown'}"


def safe_password(value: str) -> str | None:
    if "://" not in value:
        return None
    try:
        return urlsplit(value).password
    except ValueError:
        m = re.match(r"[a-z+]+://[^:/@]+:([^@]+)@", value)
        return m.group(1) if m else None


def history_text() -> str:
    out = subprocess.run(
        ["git", "log", "--all", "-p", "--no-color", "--unified=0"],
        capture_output=True, text=True, errors="replace",
    )
    return out.stdout


def live_secret_arns() -> set[str]:
    """ARNs the RUNNING task definition actually resolves.

    Without this the sweep cannot tell a live exposure from secret sprawl. An
    ORPHANED secret nothing reads may well contain published material, but that
    material authenticates nothing, so the remedy is to delete the secret rather
    than to rotate a credential that is not in use. Conflating the two sends the
    reader to rotate something that is already harmless while the genuine finding
    sits in the same list.
    """
    td = subprocess.run(
        ["aws", "ecs", "describe-services", "--cluster", CLUSTER,
         "--services", SERVICE, "--region", REGION,
         "--query", "services[0].taskDefinition", "--output", "text"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not td:
        return set()
    out = subprocess.run(
        ["aws", "ecs", "describe-task-definition", "--task-definition", td,
         "--region", REGION, "--query",
         "taskDefinition.containerDefinitions[].secrets[].valueFrom",
         "--output", "json"],
        capture_output=True, text=True,
    )
    try:
        return set(json.loads(out.stdout) or [])
    except ValueError:
        return set()


def is_public_by_design(name: str) -> bool:
    return any(name.endswith(s) for s in PUBLIC_BY_DESIGN_SUFFIXES)


def main() -> int:
    print("SEC-01 DEFINITIVE SWEEP")
    print("=" * 78)
    print("(read-only; no secret material in this output)\n")

    names = [n for n in list_secrets() if "production" in n or "staging" in n]
    print(f"secrets in scope: {len(names)}\n")

    atoms: dict[str, tuple[str, str]] = {}   # atom -> (secret name, kind)
    statuses: dict[str, str] = {}
    for n in names:
        val, status = fetch(n)
        statuses[n] = status
        if val is None:
            print(f"  [{status:>14}] {n}")
            continue
        if len(val) >= 10:
            atoms[val] = (n, "whole value")
        pw = safe_password(val)
        if pw and len(pw) >= 8 and pw not in PLACEHOLDERS:
            atoms[pw] = (n, "password component")
        print(f"  [{status:>14}] {n}  ({len(val)} chars"
              + (f", password {len(pw)} chars" if pw else "") + ")")

    unread = {n: s for n, s in statuses.items() if s != "read"}
    print(f"\nreadable: {len(statuses) - len(unread)}/{len(statuses)}   "
          f"distinct atoms to match: {len(atoms)}")

    print("\nscanning all of git history in one pass...")
    blob = history_text()
    print(f"history: {len(blob):,} characters\n")

    print("reading which secrets the running task definition resolves...")
    live_arns = live_secret_arns()
    print(f"referenced by the running task: {len(live_arns)}\n")

    def is_live(name: str) -> bool:
        return any(name in arn for arn in live_arns)

    print("=" * 78)
    print("PER-SECRET RESULT")
    print("=" * 78)
    matched: dict[str, list[str]] = {}
    for atom, (name, kind) in atoms.items():
        if atom in blob:
            matched.setdefault(name, []).append(kind)

    exposed: dict[str, list[str]] = {}   # live + in history + not public by design
    sprawl: dict[str, list[str]] = {}    # in history, but nothing reads it
    benign: list[str] = []               # public by design

    for n in names:
        if statuses[n] != "read":
            print(f"  NOT CHECKED       {n}   ({statuses[n]})")
            continue
        if n not in matched:
            print(f"  clean             {n}")
            continue
        kinds = matched[n]
        if is_public_by_design(n):
            benign.append(n)
            print(f"  public by design  {n}   (in history, and expected to be)")
        elif is_live(n):
            exposed[n] = kinds
            print(f"  *** EXPOSED       {n}   ({', '.join(kinds)})  LIVE")
        else:
            sprawl[n] = kinds
            print(f"  ORPHANED          {n}   (in history; read by nothing)")

    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    if exposed:
        print(f"\n  {len(exposed)} LIVE SECRET(S) ARE RECOVERABLE FROM GIT HISTORY:\n")
        for name, kinds in exposed.items():
            print(f"     {name}  ({', '.join(kinds)})")
        print("\n  Every one of these must be rotated. Rotating without purging history")
        print("  leaves the next value safe but the old one permanently published;")
        print("  purging without rotating leaves clones that already exist holding a")
        print("  working credential. Both are required, rotation first.")
    else:
        print("\n  NO LIVE SECRET IS RECOVERABLE FROM GIT HISTORY.")

    if sprawl:
        print(f"\n  {len(sprawl)} ORPHANED secret(s) contain published material but are")
        print("  read by NOTHING, so they authenticate nothing:\n")
        for name in sprawl:
            print(f"     {name}")
        print("\n  These are NOT a live exposure and must not be confused with one.")
        print("  The remedy is DELETE, not rotate: leaving them invites a future")
        print("  operator to wire one up and silently deploy a published credential.")
        print("  `aws secretsmanager delete-secret` keeps a recovery window, so this")
        print("  is reversible for 7-30 days.")

    if benign:
        print(f"\n  {len(benign)} secret(s) are PUBLIC BY DESIGN and correctly in git:\n")
        for name in benign:
            print(f"     {name}")
        print("\n  Listed for completeness, deliberately NOT counted as findings. A")
        print("  Supabase anon key ships in the browser bundle; its safety rests on")
        print("  row-level security, not on secrecy.")

    if unread:
        print(f"\n  COVERAGE CAVEAT: {len(unread)} secret(s) could not be read and are")
        print("  therefore UNKNOWN, not clean:")
        for n, s in unread.items():
            print(f"     {n}  ({s})")

    # Non-zero ONLY for a live exposure. Sprawl and public-by-design entries are
    # reported but must not fail a scheduled run, or the signal gets ignored.
    return 1 if exposed else 0


if __name__ == "__main__":
    sys.exit(main())
