#!/usr/bin/env python3
"""SEC-01 step 3: rotate MASTER_ENCRYPTION_KEYS, published in this repo's history.

This is the delicate one. The key encrypts customer exchange API credentials, so a
mistake does not fail loudly -- it makes ciphertext permanently undecryptable.

WHY A PREPEND IS SAFE, AND WHY ONLY FOR ONE OF THE TWO VAULTS
-------------------------------------------------------------
``backend_app/backend/api_key_vault.py:96-99`` builds a ``MultiFernet`` from the
comma-separated list: THE FIRST KEY ENCRYPTS, EVERY KEY DECRYPTS. Prepending a new
key therefore leaves all existing ciphertext readable. This is the vault that holds
the live rows.

``backend_app/core/credential_vault.py:285`` does something different and unsafe
for rotation: it takes ``[...][0]`` -- only the FIRST key -- and runs it through
PBKDF2 to derive a single ``Fernet``. Prepending there silently orphans everything
it encrypted. It has NO production callers (its only references are its own module,
tests, and ``test_strategy_dag_architecture.py``, which treats importing it as a
violation) and it stores to Redis rather than Postgres, so it holds no durable data
today. ``--plan`` re-checks that claim every run rather than trusting this comment.

THE ORDER IS LOAD-BEARING AND THIS SCRIPT ENFORCES IT
-----------------------------------------------------
    1. --prepend    new key becomes primary in Secrets Manager. Nothing uses it yet.
    2. --redeploy   tasks relaunch holding BOTH keys.
    3. --reencrypt  rewrite existing rows under the new primary.
    4. --verify     prove every row decrypts under the NEW PRIMARY ALONE.
    5. --retire     drop the old key, then --redeploy once more.

Step 3 before step 2 would be an outage: the running task would still hold only the
old key and could not read rows written under the new one. ``--reencrypt`` therefore
REFUSES unless it can prove a deployment completed after the secret was staged.

Step 5 before step 4 would be permanent data loss. ``--retire`` REFUSES unless every
row already decrypts under the primary alone.

Re-encryption uses ``MultiFernet.rotate()``, the library's own primitive, which
re-encrypts a token under the primary key without this script ever handling the
plaintext.

USAGE
-----
    python scripts/rotate_master_encryption_keys.py --plan
    python scripts/rotate_master_encryption_keys.py --prepend
    python scripts/rotate_master_encryption_keys.py --redeploy
    python scripts/rotate_master_encryption_keys.py --reencrypt
    python scripts/rotate_master_encryption_keys.py --verify
    python scripts/rotate_master_encryption_keys.py --retire
    python scripts/rotate_master_encryption_keys.py --redeploy
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time

SECRET_ID = "vyomquant/production/master_encryption_keys"
DSN_SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"
SERVICE = "vyomquant-api-service-cjema2sl"

TABLE = "exchange_keys"
CIPHER_COLUMNS = ("encrypted_api_key", "encrypted_secret_key", "encrypted_password")
IDENTITY_COLUMNS = ("user_id", "exchange_id")


def aws(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *args, "--region", REGION],
                          capture_output=True, text=True, input=stdin)


def fp(value: str) -> str:
    return hashlib.sha256(b"mek-rotation-fp" + value.encode()).hexdigest()[:12]


def read_secret(secret_id: str) -> str | None:
    out = aws("secretsmanager", "get-secret-value", "--secret-id", secret_id,
              "--query", "SecretString", "--output", "text")
    return out.stdout.strip() if out.returncode == 0 else None


def key_list() -> list[str]:
    raw = read_secret(SECRET_ID)
    if raw is None:
        print(f"cannot read {SECRET_ID}", file=sys.stderr)
        sys.exit(2)
    return [k.strip() for k in raw.split(",") if k.strip()]


def put_keys(keys: list[str]) -> str:
    payload = ",".join(keys)
    out = aws("secretsmanager", "put-secret-value", "--secret-id", SECRET_ID,
              "--secret-string", "fileb:///dev/stdin", stdin=payload)
    if out.returncode != 0:
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
            fh.write(payload)
            tmp = fh.name
        try:
            out = aws("secretsmanager", "put-secret-value", "--secret-id", SECRET_ID,
                      "--secret-string", f"file://{tmp}")
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if out.returncode != 0:
        print(f"put-secret-value failed: {out.stderr.strip()[:300]}", file=sys.stderr)
        sys.exit(1)
    return json.loads(out.stdout or "{}").get("VersionId", "(unknown)")


def connect():
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is required for the database half", file=sys.stderr)
        sys.exit(2)
    dsn = read_secret(DSN_SECRET_ID)
    if not dsn:
        print(f"cannot read {DSN_SECRET_ID}", file=sys.stderr)
        sys.exit(2)
    return psycopg2.connect(dsn, connect_timeout=30)


def ciphers(keys: list[str]):
    from cryptography.fernet import Fernet, MultiFernet
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys]), Fernet(keys[0].encode())
    except Exception as exc:  # noqa: BLE001
        print(f"one or more keys are not valid Fernet keys: {type(exc).__name__}",
              file=sys.stderr)
        sys.exit(2)


def fetch_rows(conn) -> list[tuple]:
    cur = conn.cursor()
    cols = ", ".join(IDENTITY_COLUMNS + CIPHER_COLUMNS)
    cur.execute(f"SELECT {cols} FROM {TABLE}")  # noqa: S608 - fixed identifiers
    return cur.fetchall()


def classify(rows: list[tuple], multi, primary) -> dict[str, int]:
    """How many ciphertext values decrypt under the primary alone vs only the set."""
    from cryptography.fernet import InvalidToken
    counts = {"under_primary": 0, "under_older_key": 0, "undecryptable": 0, "null": 0}
    for row in rows:
        for value in row[len(IDENTITY_COLUMNS):]:
            if value is None:
                counts["null"] += 1
                continue
            try:
                primary.decrypt(value.encode())
                counts["under_primary"] += 1
                continue
            except InvalidToken:
                pass
            try:
                multi.decrypt(value.encode())
                counts["under_older_key"] += 1
            except InvalidToken:
                counts["undecryptable"] += 1
    return counts


def deployment_is_newer_than_secret() -> tuple[bool, str]:
    """Has a deployment completed since the current secret version was created?"""
    out = aws("secretsmanager", "list-secret-version-ids", "--secret-id", SECRET_ID,
              "--query", "Versions[?contains(VersionStages, 'AWSCURRENT')].CreatedDate",
              "--output", "json")
    if out.returncode != 0:
        return False, "could not read secret version dates"
    try:
        staged = dt.datetime.fromisoformat(json.loads(out.stdout)[0].replace("Z", "+00:00"))
    except (ValueError, IndexError, KeyError):
        return False, "could not parse the secret version date"

    out = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", SERVICE,
              "--query", "services[0].deployments[0].[createdAt,rolloutState]",
              "--output", "json")
    if out.returncode != 0:
        return False, "could not read the service deployment"
    created_raw, state = json.loads(out.stdout)
    deployed = dt.datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))

    if state != "COMPLETED":
        return False, f"the latest deployment is {state}, not COMPLETED"
    if deployed <= staged:
        return False, (f"the latest deployment ({deployed.isoformat()}) started BEFORE "
                       f"the current secret version ({staged.isoformat()}), so the "
                       f"running tasks do not hold the new key yet")
    return True, (f"deployment {deployed.isoformat()} is after secret version "
                  f"{staged.isoformat()}")


def cmd_plan() -> int:
    keys = key_list()
    multi, primary = ciphers(keys)
    print("MASTER_ENCRYPTION_KEYS ROTATION PLAN (read-only)\n")
    print(f"  secret        : {SECRET_ID}")
    print(f"  keys present  : {len(keys)}")
    for i, k in enumerate(keys):
        role = "PRIMARY (encrypts)" if i == 0 else "legacy (decrypt only)"
        print(f"      [{i}] fingerprint={fp(k)}  {role}")

    raw = read_secret(SECRET_ID) or ""
    out = subprocess.run(["git", "log", "--all", "--format=%H", f"-S{raw}"],
                         capture_output=True, text=True, errors="replace")
    print(f"  whole value in git history : "
          f"{'YES -- this is why we are rotating' if out.stdout.strip() else 'no'}")

    conn = connect()
    try:
        rows = fetch_rows(conn)
        counts = classify(rows, multi, primary)
    finally:
        conn.close()
    print(f"\n  {TABLE} rows : {len(rows)}")
    print(f"      ciphertext values under the PRIMARY key : {counts['under_primary']}")
    print(f"      ciphertext values under an OLDER key    : {counts['under_older_key']}")
    print(f"      NULL (optional fields)                  : {counts['null']}")
    print(f"      UNDECRYPTABLE under any key             : {counts['undecryptable']}")
    if counts["undecryptable"]:
        print("\n  *** ALREADY-LOST CIPHERTEXT PRESENT ***")
        print(f"      {counts['undecryptable']} value(s) decrypt under no key this")
        print("      deployment holds. They are genuine Fernet tokens, so they were")
        print("      encrypted by a real key that no longer exists in Secrets Manager.")
        print("      Investigated separately (see FINDING VAULT-01): no key recoverable")
        print("      from git history opens them either, across 265 candidates, so they")
        print("      are unrecoverable rather than merely misplaced.")
        print("\n      CONSEQUENCES, BOTH DIRECTIONS:")
        print("       * Those credentials cannot be read by the platform. Any live")
        print("         trading for their owners already fails in _decrypt. Their owners")
        print("         must re-enter their exchange keys.")
        print("       * Rotation is therefore RISK-FREE for them: there is no readable")
        print("         ciphertext left to preserve, so nothing can be made worse.")
        print("\n      To proceed deliberately rather than by accident, pass")
        print("      --accept-unrecoverable to --reencrypt and --retire. Without it")
        print("      they refuse, because the same symptom could mean a key that is")
        print("      merely missing from THIS environment and still recoverable.")

    print("\n  SECOND VAULT CHECK (core/credential_vault.py takes only the FIRST key")
    print("  through PBKDF2, so a prepend would orphan anything it encrypted):")
    grep = subprocess.run(
        ["git", "grep", "-l", "-E", r"get_credential_vault|CredentialVault\(", "HEAD",
         "--", "backend_app"],
        capture_output=True, text=True, errors="replace")
    callers = [ln for ln in grep.stdout.splitlines()
               if "credential_vault.py" not in ln]
    if callers:
        print("      *** CALLERS FOUND in backend_app -- a prepend may orphan data:")
        for c in callers:
            print(f"          {c}")
        print("      Resolve this before prepending.")
        return 2
    print("      none in backend_app outside the module itself. Prepend is safe.")

    print("\n  NEXT: --prepend, then --redeploy, then --reencrypt, --verify, --retire")
    return 0


def cmd_prepend() -> int:
    from cryptography.fernet import Fernet
    keys = key_list()
    new = Fernet.generate_key().decode()
    if new in keys:
        print("generated key already present; aborting", file=sys.stderr)
        return 2
    updated = [new] + keys
    print(f"current keys : {len(keys)}  (primary fingerprint {fp(keys[0])})")
    print(f"new primary  : fingerprint {fp(new)}  (generated in-process, never printed)")
    version = put_keys(updated)
    print(f"\nSTAGED version {version}: {len(updated)} keys, newest first.")
    print("Every existing ciphertext remains decryptable -- MultiFernet tries all keys.")
    print("\nNOTHING IS USING IT YET. Next:")
    print("  python scripts/rotate_master_encryption_keys.py --redeploy")
    return 0


def cmd_redeploy() -> int:
    print("forcing a new deployment so tasks re-resolve the key list...")
    out = aws("ecs", "update-service", "--cluster", CLUSTER, "--service", SERVICE,
              "--force-new-deployment")
    if out.returncode != 0:
        print(f"update-service failed: {out.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    deadline = time.time() + 900
    last = None
    while time.time() < deadline:
        out = aws("ecs", "describe-services", "--cluster", CLUSTER,
                  "--services", SERVICE, "--query",
                  "services[0].deployments[0].[rolloutState,runningCount,desiredCount,"
                  "rolloutStateReason]", "--output", "json")
        if out.returncode == 0:
            try:
                state, running, desired, reason = json.loads(out.stdout)
            except (ValueError, TypeError):
                state = running = desired = reason = None
            line = f"   {state}  running={running}/{desired}  {reason}"
            if line != last:
                print(line, flush=True)
                last = line
            if state == "COMPLETED":
                print("\nDEPLOYMENT COMPLETED.")
                return 0
            if state == "FAILED":
                print("\nDEPLOYMENT FAILED and ECS rolled back; old task still serves.",
                      file=sys.stderr)
                return 1
        time.sleep(15)
    print("timed out; check the ECS console", file=sys.stderr)
    return 1


def cmd_reencrypt(accept_unrecoverable: bool = False) -> int:
    ok, why = deployment_is_newer_than_secret()
    print(f"ordering guard: {why}")
    if not ok:
        print("\nREFUSING TO RE-ENCRYPT. Rows written under the new primary would be "
              "unreadable by the running tasks, which is an outage.\n"
              "Run --redeploy first.", file=sys.stderr)
        return 2

    keys = key_list()
    if len(keys) < 2:
        print("\nonly one key present; nothing to rotate from. Run --prepend first.",
              file=sys.stderr)
        return 2
    multi, primary = ciphers(keys)

    conn = connect()
    try:
        rows = fetch_rows(conn)
        before = classify(rows, multi, primary)
        print(f"\n{len(rows)} row(s); {before['under_older_key']} value(s) under an "
              f"older key, {before['under_primary']} already under the primary.")
        if before["undecryptable"] and not accept_unrecoverable:
            print(f"\nSTOP: {before['undecryptable']} undecryptable value(s). Run --plan "
                  f"for the diagnosis. If they are known-unrecoverable, re-run with "
                  f"--accept-unrecoverable to proceed deliberately.", file=sys.stderr)
            return 2
        if before["undecryptable"]:
            print(f"proceeding past {before['undecryptable']} known-unrecoverable "
                  f"value(s); they are left exactly as they are, not touched.")
        if not before["under_older_key"]:
            print("Nothing to re-encrypt -- no value sits under an older key.")
            return 0

        cur = conn.cursor()
        rotated = 0
        for row in rows:
            identity = row[:len(IDENTITY_COLUMNS)]
            values = row[len(IDENTITY_COLUMNS):]
            sets, params = [], []
            for col, value in zip(CIPHER_COLUMNS, values):
                if value is None:
                    continue
                # MultiFernet.rotate re-encrypts under the PRIMARY key without
                # exposing the plaintext to this process.
                sets.append(f"{col} = %s")
                params.append(multi.rotate(value.encode()).decode())
            if not sets:
                continue
            where = " AND ".join(f"{c} = %s" for c in IDENTITY_COLUMNS)
            cur.execute(
                f"UPDATE {TABLE} SET {', '.join(sets)} WHERE {where}",  # noqa: S608
                params + list(identity),
            )
            rotated += 1
        conn.commit()
        print(f"re-encrypted {rotated} row(s) under the new primary key.")

        after = classify(fetch_rows(conn), multi, primary)
        print(f"\nafter: under primary={after['under_primary']}  "
              f"under older key={after['under_older_key']}  "
              f"undecryptable={after['undecryptable']}")
        if after["under_older_key"]:
            print("NOT FULLY ROTATED -- do not --retire.", file=sys.stderr)
            return 1
        if after["undecryptable"] and not accept_unrecoverable:
            print("undecryptable values remain -- do not --retire.", file=sys.stderr)
            return 1
    except Exception:
        conn.rollback()
        print("rolled back; nothing was changed", file=sys.stderr)
        raise
    finally:
        conn.close()

    print("\nALL ROWS NOW UNDER THE NEW PRIMARY. Next: --verify, then --retire.")
    return 0


def cmd_verify() -> int:
    keys = key_list()
    multi, primary = ciphers(keys)
    conn = connect()
    try:
        rows = fetch_rows(conn)
        counts = classify(rows, multi, primary)
    finally:
        conn.close()
    print(f"keys present : {len(keys)}  (primary fingerprint {fp(keys[0])})")
    print(f"{TABLE} rows : {len(rows)}")
    print(f"   under PRIMARY alone : {counts['under_primary']}")
    print(f"   under an older key  : {counts['under_older_key']}")
    print(f"   NULL                : {counts['null']}")
    print(f"   undecryptable       : {counts['undecryptable']}")

    raw = read_secret(SECRET_ID) or ""
    out = subprocess.run(["git", "log", "--all", "--format=%H", f"-S{raw}"],
                         capture_output=True, text=True, errors="replace")
    leaked = bool(out.stdout.strip())
    print(f"current value in git history : {'YES' if leaked else 'no'}")

    print()
    if counts["under_older_key"]:
        print("NOT SAFE TO RETIRE -- run --reencrypt first.", file=sys.stderr)
        return 1
    if leaked:
        print("NOT DONE: the current key list still appears in git history, so the "
              "rotation has not actually changed anything an attacker holds.",
              file=sys.stderr)
        return 1
    if counts["undecryptable"]:
        print("ROTATED, with a caveat that is NOT this rotation's fault:")
        print(f"  {counts['undecryptable']} value(s) were already encrypted under a lost")
        print("  key before this rotation began and remain unreadable. See VAULT-01.")
        print("  Retiring the old key is safe for them (nothing readable is lost), but")
        print("  their owners must re-enter their exchange credentials.")
        return 0
    print("ROTATED. Every value decrypts under the primary key alone, and the current")
    print("key list does not appear in git history.")
    return 0


def cmd_retire(accept_unrecoverable: bool = False) -> int:
    keys = key_list()
    if len(keys) < 2:
        print("only one key present; nothing to retire.")
        return 0
    multi, primary = ciphers(keys)
    conn = connect()
    try:
        counts = classify(fetch_rows(conn), multi, primary)
    finally:
        conn.close()
    if counts["under_older_key"]:
        print(f"REFUSING: {counts['under_older_key']} value(s) still decrypt only under "
              f"an older key. Retiring it would make them permanently unreadable. "
              f"Run --reencrypt first.", file=sys.stderr)
        return 2
    if counts["undecryptable"] and not accept_unrecoverable:
        print(f"REFUSING: {counts['undecryptable']} undecryptable value(s) present. "
              f"Retiring cannot make them worse, but confirm they are known-"
              f"unrecoverable (see --plan) and re-run with --accept-unrecoverable.",
              file=sys.stderr)
        return 2

    print(f"retiring {len(keys) - 1} old key(s); keeping primary {fp(keys[0])}")
    version = put_keys([keys[0]])
    print(f"\nSTAGED version {version}: a single key.")
    print("Finish with:")
    print("  python scripts/rotate_master_encryption_keys.py --redeploy")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--prepend", action="store_true")
    g.add_argument("--redeploy", action="store_true")
    g.add_argument("--reencrypt", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--retire", action="store_true")
    p.add_argument("--accept-unrecoverable", action="store_true",
                   help="proceed past ciphertext that no key opens. Requires reading "
                        "--plan first: the same symptom can mean a key that is merely "
                        "absent from this environment and still recoverable elsewhere.")
    a = p.parse_args()
    if a.plan:
        return cmd_plan()
    if a.prepend:
        return cmd_prepend()
    if a.redeploy:
        return cmd_redeploy()
    if a.reencrypt:
        return cmd_reencrypt(a.accept_unrecoverable)
    if a.verify:
        return cmd_verify()
    if a.retire:
        return cmd_retire(a.accept_unrecoverable)
    return 2


if __name__ == "__main__":
    sys.exit(main())
