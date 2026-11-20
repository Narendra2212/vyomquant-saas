#!/usr/bin/env python3
"""VAULT-01: mark the exchange credentials that no configured master key can open.

WHY THIS EXISTS
===============
VAULT-01's part (1): "Their existing rows should be deleted or flagged rather than left
to raise, because the current failure mode is an exception from _decrypt at use time
rather than a clear 'reconnect your exchange' state the UI can act on."

FLAGGED, NOT DELETED, and the choice matters. Deleting is simplest and the rows carry no
recoverable value, but it destroys the only record that the user ever connected that
venue -- so the account silently looks like it never had a credential, and nobody can
later tell an unreadable row from one that never existed. Flagging keeps the audit trail,
gives the dashboard a specific negative to render, and stays reversible. Deletion remains
available afterwards and is then an informed choice rather than the only option.

WHAT IT WRITES, AND WHAT IT REFUSES TO WRITE
--------------------------------------------
For a row whose ciphertext opens under NO configured key:

    validation_status = 'unreadable'   <- a known negative; credential_validity() returns
                                         False, venue_status() reads invalid_credentials
    can_trade         = false          <- it demonstrably cannot place an order
    last_validated_at = now()          <- when this was established

It does NOT touch is_active. That column means "the user enabled this key", and the user
did; we lost the ability to read it. Overwriting it would blame the user for our failure.

It NEVER writes an affirmative. A row that decrypts is left exactly as it was --
validation_status and can_trade untouched, which for an unchecked row means NULL, the
unknown. Being able to DECRYPT a credential says nothing about whether the VENUE accepts
it, and writing can_trade=true here would fabricate a validation nobody performed. That
is the distinction dashboard_aggregation_service.credential_validity is built around, and
this script stays on the right side of it.

SAFETY
------
``--plan`` is the default and writes nothing. ``--apply`` requires the explicit flag.
Only ever narrows: it sets a negative on rows that already fail, so it cannot make a
working credential unusable. Re-runnable; a row already marked is reported, not rewritten.
No key material, no plaintext and no ciphertext is printed.

    python scripts/quarantine_undecryptable_exchange_keys.py --plan
    python scripts/quarantine_undecryptable_exchange_keys.py --apply
"""
from __future__ import annotations

import argparse
import subprocess
import sys

SECRET_ID = "vyomquant/production/master_encryption_keys"
DSN_SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"

TABLE = "exchange_keys"
CIPHER_COLUMNS = ("encrypted_api_key", "encrypted_secret_key", "encrypted_password")
IDENTITY_COLUMNS = ("user_id", "exchange_id")

# Imported from the module that owns it so the spelling cannot drift from the reader's.
sys.path.insert(0, __file__.rsplit("scripts", 1)[0])
try:
    from backend_app.core.credential_key_integrity import UNREADABLE_STATUS
except Exception:  # noqa: BLE001 - keep the script runnable standalone
    UNREADABLE_STATUS = "unreadable"


def aws(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *args, "--region", REGION],
                          capture_output=True, text=True)


def read_secret(secret_id: str) -> str | None:
    out = aws("secretsmanager", "get-secret-value", "--secret-id", secret_id,
              "--query", "SecretString", "--output", "text")
    return out.stdout.strip() if out.returncode == 0 else None


def connect():
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required", file=sys.stderr)
        sys.exit(2)
    dsn = read_secret(DSN_SECRET_ID)
    if not dsn:
        print(f"cannot read {DSN_SECRET_ID}", file=sys.stderr)
        sys.exit(2)
    return psycopg2.connect(dsn, connect_timeout=30)


def build_multi():
    from cryptography.fernet import Fernet, MultiFernet

    raw = read_secret(SECRET_ID)
    if not raw:
        print(f"cannot read {SECRET_ID}", file=sys.stderr)
        sys.exit(2)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys]), len(keys)
    except Exception as exc:  # noqa: BLE001
        print(f"MASTER_ENCRYPTION_KEYS is not valid key material: {type(exc).__name__}",
              file=sys.stderr)
        sys.exit(2)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true",
                   help="report what would be marked and write nothing (default)")
    g.add_argument("--apply", action="store_true",
                   help="write the quarantine state")
    args = p.parse_args()
    apply = bool(args.apply)

    multi, key_count = build_multi()
    conn = connect()
    cur = conn.cursor()

    has_columns = True
    try:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND column_name IN "
            "('validation_status', 'can_trade', 'last_validated_at')", (TABLE,))
        has_columns = len(cur.fetchall()) == 3
    except Exception:  # noqa: BLE001
        has_columns = False

    if not has_columns:
        print(f"{TABLE} does not carry validation_status / can_trade / "
              f"last_validated_at. Apply backend_app/migrations/"
              f"056_vault01_credential_validity_state.sql first.", file=sys.stderr)
        return 2

    cols = ", ".join(IDENTITY_COLUMNS + CIPHER_COLUMNS + ("validation_status",))
    cur.execute(f"SELECT {cols} FROM {TABLE}")  # noqa: S608 - fixed identifiers
    rows = cur.fetchall()

    print(f"master keys configured : {key_count}")
    print(f"{TABLE} rows           : {len(rows)}\n")

    to_mark: list[tuple] = []
    already: list[tuple] = []
    readable = 0
    for row in rows:
        identity = row[:len(IDENTITY_COLUMNS)]
        values = row[len(IDENTITY_COLUMNS):len(IDENTITY_COLUMNS) + len(CIPHER_COLUMNS)]
        status = (row[-1] or "").strip().lower()
        present = [v for v in values if v]
        if not present:
            continue
        opened = 0
        for v in present:
            try:
                multi.decrypt(v.encode() if isinstance(v, str) else v)
                opened += 1
            except Exception:  # noqa: BLE001
                pass
        label = "/".join(str(x)[:8] for x in identity)
        if opened == len(present):
            readable += 1
            print(f"  {label:24} all {len(present)} value(s) decrypt - left untouched")
        elif status == UNREADABLE_STATUS:
            already.append(identity)
            print(f"  {label:24} {len(present) - opened}/{len(present)} undecryptable "
                  f"- already quarantined")
        else:
            to_mark.append(identity)
            verb = "marking" if apply else "WOULD MARK"
            print(f"  {label:24} {len(present) - opened}/{len(present)} undecryptable "
                  f"- {verb} '{UNREADABLE_STATUS}'")

    print(f"\nreadable rows            : {readable}")
    print(f"already quarantined      : {len(already)}")
    print(f"to quarantine            : {len(to_mark)}")

    if not to_mark:
        print("\nnothing to do.")
        conn.close()
        return 0

    if not apply:
        print("\n--plan only; nothing written. Re-run with --apply to write.")
        conn.close()
        return 0

    where = " AND ".join(f"{c} = %s" for c in IDENTITY_COLUMNS)
    for identity in to_mark:
        cur.execute(
            f"UPDATE {TABLE} SET validation_status = %s, can_trade = FALSE, "  # noqa: S608
            f"last_validated_at = now() WHERE {where}",
            (UNREADABLE_STATUS, *identity),
        )
    conn.commit()
    print(f"\nmarked {len(to_mark)} row(s) '{UNREADABLE_STATUS}'.")

    # Prove it landed, rather than trusting the UPDATE's own word for it.
    cur.execute(
        f"SELECT count(*) FROM {TABLE} WHERE validation_status = %s",  # noqa: S608
        (UNREADABLE_STATUS,))
    print(f"rows now quarantined     : {cur.fetchone()[0]}")
    cur.execute(
        f"SELECT count(*) FROM {TABLE} WHERE validation_status = %s "  # noqa: S608
        f"AND can_trade IS NOT FALSE", (UNREADABLE_STATUS,))
    stragglers = cur.fetchone()[0]
    print(f"quarantined but can_trade not false : {stragglers}")
    conn.close()
    return 1 if stragglers else 0


if __name__ == "__main__":
    sys.exit(main())
