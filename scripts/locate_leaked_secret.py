#!/usr/bin/env python3
"""Locate every commit/path exposing each of the four matched live secrets.

Also classifies severity, because one of the four (the Supabase ANON key) is
designed to be public and must not be reported as an incident alongside the
others -- inflating a real incident with a false positive makes the real part
easier to dismiss.

Prints paths, commits, dates. Never prints secret material.
"""
from __future__ import annotations

import re
import subprocess
import sys
from urllib.parse import urlsplit

REGION = "ap-southeast-1"

TARGETS = [
    ("vyomquant/production/master_encryption_keys", "whole",
     "CRITICAL",
     "Fernet key(s) encrypting tenant exchange API keys in the credential vault. "
     "With this plus any database copy, every customer's exchange API keys decrypt."),
    ("vyomquant/production/jwt_secret", "whole",
     "CRITICAL",
     "Signs/verifies the application's own JWTs. Anyone holding it can mint a token "
     "for any user, including an admin role, with no password."),
    ("vyomquant/production/database_url", "password",
     "CRITICAL",
     "Direct connection as the `postgres` role to the production database."),
    ("vyomquant/production/supabase_anon_key", "whole",
     "NOT AN INCIDENT",
     "Supabase anon keys are PUBLIC BY DESIGN -- shipped in the browser bundle and "
     "stored as the VITE_SUPABASE_ANON_KEY CI secret precisely so it can be. Its "
     "safety rests on RLS, not on secrecy. Being in git is expected."),
]


def fetch(secret_id: str) -> str | None:
    out = subprocess.run(
        ["aws", "secretsmanager", "get-secret-value", "--secret-id", secret_id,
         "--region", REGION, "--query", "SecretString", "--output", "text"],
        capture_output=True, text=True,
    )
    return out.stdout.strip() if out.returncode == 0 else None


def atom(value: str, kind: str) -> str | None:
    if kind == "whole":
        return value
    try:
        return urlsplit(value).password
    except ValueError:
        m = re.match(r"[a-z+]+://[^:/@]+:([^@]+)@", value)
        return m.group(1) if m else None


def locate(needle: str) -> tuple[list[tuple[str, str, str]], set[str], bool]:
    out = subprocess.run(
        ["git", "log", "--all", "--format=%H|%ad|%s", "--date=short",
         f"-S{needle}", "--name-only"],
        capture_output=True, text=True, errors="replace",
    )
    commits: list[tuple[str, str, str]] = []
    paths: set[str] = set()
    cur = None
    for ln in out.stdout.splitlines():
        if not ln.strip():
            continue
        if ln.count("|") >= 2 and len(ln.split("|", 2)[0]) == 40:
            sha, date, subj = ln.split("|", 2)
            commits.append((sha, date, subj))
            cur = sha
        elif cur:
            paths.add(ln.strip())
    head = subprocess.run(["git", "grep", "-l", "--fixed-strings", needle, "HEAD"],
                          capture_output=True, text=True, errors="replace")
    return commits, paths, bool(head.stdout.strip())


def main() -> int:
    print("SEC-01: EXPOSURE MAP")
    print("=" * 78)
    print("Repository is PUBLIC. Anyone can clone and read all of this history.\n")

    for secret_id, kind, severity, why in TARGETS:
        val = fetch(secret_id)
        if not val:
            print(f"\n{secret_id}: could not read, skipped")
            continue
        needle = atom(val, kind)
        if not needle:
            print(f"\n{secret_id}: no {kind} to match")
            continue

        commits, paths, in_head = locate(needle)
        print("\n" + "=" * 78)
        print(f"{severity}   {secret_id}   ({kind})")
        print("=" * 78)
        print(f"  WHY IT MATTERS: {why}")
        print(f"  commits touching it : {len(commits)}")
        print(f"  files that held it  : {len(paths)}")
        print(f"  present at HEAD     : {'YES -- STILL IN THE TREE' if in_head else 'no'}")
        if commits:
            first = min(commits, key=lambda c: c[1])
            print(f"  first published     : {first[1]}  ({first[0][:10]})")
        for p in sorted(paths)[:12]:
            print(f"      - {p}")
        if len(paths) > 12:
            print(f"      ... and {len(paths) - 12} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
