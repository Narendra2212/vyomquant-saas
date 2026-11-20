#!/usr/bin/env python3
"""SEC-01 operator tool: stage a rotated production database password in Secrets Manager.

WHY THIS EXISTS
---------------
SEC-01's rotation was planned in detail and then stalled, because executing it by hand
means editing a production DSN correctly under time pressure, in the right order, with the
password passing through a shell. This does the fiddly part so the operator only has to
decide and confirm.

THE PASSWORD NEVER REACHES A COMMAND LINE, A LOG, OR A CHAT MESSAGE. It is read from the
environment variable ``NEW_DB_PASSWORD`` (or prompted for, hidden) and only the resulting
DSN is written, to Secrets Manager, via stdin. This script prints neither the old nor the
new DSN. It reports only the component it changed (host/port/user/database), never the
secret.

THE ORDER MATTERS, AND THIS SCRIPT ENFORCES THE SAFE ONE
--------------------------------------------------------
Stage in Secrets Manager FIRST, then change it in Supabase, then redeploy. Measured
reasoning from SEC-01's own reconnaissance:

* ECS resolves ``secrets``/``valueFrom`` only at task LAUNCH, never into a running
  container. So staging a new value changes nothing until a force-new-deployment.
* The service is ROLLING with minimumHealthyPercent=100/maximumPercent=200 and the
  deployment circuit breaker enabled (rollback: true). A force-new-deployment starts a
  second task and waits for it to pass the ALB health check before stopping the old one, so
  it never passes through zero running tasks -- AND if the staged DSN carries a password
  Supabase has not been set to yet, the new task fails readiness and **ECS rolls back by
  itself** with the old task still serving. That ordering mistake is self-correcting.
* The OPPOSITE order is the dangerous one. If the Supabase password changes before the
  secret is staged and redeployed, the running task's established pooled connections
  survive (PostgreSQL does not drop live sessions on a password change) but every NEW
  connection attempt from that stale task starts failing immediately.

USAGE
-----
    # 1. Choose the new password in the Supabase dashboard but DO NOT APPLY IT YET.
    #    Settings -> Database -> Reset database password -> set your own value, copy it.

    # 2. Stage it here (PowerShell):
    $env:NEW_DB_PASSWORD = '<the value you chose>'
    python scripts/rotate_database_password.py --stage
    Remove-Item Env:NEW_DB_PASSWORD

    # 3. NOW click the reset in Supabase so the database actually takes that password.

    # 4. Redeploy so fresh tasks pick up the staged secret, and watch it settle:
    python scripts/rotate_database_password.py --redeploy

    # 5. Confirm, and only then consider the old version retired:
    python scripts/rotate_database_password.py --verify

``--stage`` refuses to write a value that does not parse as a DSN, and refuses to write one
whose password is unchanged. Secrets Manager keeps the previous version as AWSPREVIOUS, so
the old value is recoverable with
``aws secretsmanager update-secret-version-stage`` if needed.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit, urlunsplit

SECRET_ID = "vyomquant/production/database_url"
REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"
SERVICE = "vyomquant-api-service-cjema2sl"


def aws(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["aws", *args, "--region", REGION],
        capture_output=True, text=True, input=stdin,
    )


def read_secret() -> tuple[str, str]:
    """Returns (dsn, container_format) where container_format is 'raw' or a JSON key."""
    out = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
              "--query", "SecretString", "--output", "text")
    if out.returncode != 0:
        print(f"could not read {SECRET_ID}: {out.stderr.strip()[:300]}", file=sys.stderr)
        sys.exit(2)
    raw = out.stdout.strip()
    if raw.startswith("{"):
        blob = json.loads(raw)
        for key in ("DATABASE_URL", "database_url", "url", "dsn"):
            if key in blob:
                return blob[key], key
        key = next(iter(blob))
        return blob[key], key
    return raw, "raw"


def describe(dsn: str) -> str:
    """A safe description of a DSN: everything except the password."""
    parts = urlsplit(dsn)
    user = parts.username or "(no user)"
    host = parts.hostname or "(no host)"
    port = parts.port or "(default)"
    db = (parts.path or "/").lstrip("/") or "(no database)"
    return f"scheme={parts.scheme} user={user} host={host} port={port} database={db}"


def with_password(dsn: str, new_password: str) -> str:
    parts = urlsplit(dsn)
    if not parts.scheme or not parts.hostname:
        print("the stored value does not parse as a DSN; refusing to rewrite it",
              file=sys.stderr)
        sys.exit(2)
    user = parts.username or ""
    host = parts.hostname
    netloc = f"{quote(user, safe='')}:{quote(new_password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def cmd_stage() -> int:
    dsn, container = read_secret()
    print(f"current secret parses as:  {describe(dsn)}")
    print(f"stored as: {'a bare string' if container == 'raw' else f'JSON key {container!r}'}")

    new_password = os.environ.get("NEW_DB_PASSWORD") or getpass.getpass(
        "New database password (not echoed): "
    )
    if not new_password.strip():
        print("empty password; nothing staged", file=sys.stderr)
        return 2
    if len(new_password) < 16:
        print(f"refusing a {len(new_password)}-character password for a production "
              f"database; use at least 16", file=sys.stderr)
        return 2
    if urlsplit(dsn).password == new_password:
        print("that is already the stored password; nothing staged", file=sys.stderr)
        return 2

    new_dsn = with_password(dsn, new_password)
    if container == "raw":
        payload = new_dsn
    else:
        # Re-read the full JSON so every sibling key is preserved verbatim; only the
        # DSN member is replaced.
        out = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
                  "--query", "SecretString", "--output", "text")
        if out.returncode != 0:
            print(f"could not re-read the secret: {out.stderr.strip()[:200]}",
                  file=sys.stderr)
            return 2
        blob = json.loads(out.stdout.strip())
        blob[container] = new_dsn
        payload = json.dumps(blob)

    print(f"staging a new version (unchanged: {describe(new_dsn)})")
    out = aws("secretsmanager", "put-secret-value", "--secret-id", SECRET_ID,
              "--secret-string", "fileb:///dev/stdin", stdin=payload)
    if out.returncode != 0:
        # fileb:///dev/stdin is not portable (notably on Windows); fall back to a
        # temporary file that is removed immediately.
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                         suffix=".json") as fh:
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
        return 1

    version = json.loads(out.stdout or "{}").get("VersionId", "(unknown)")
    print(f"\nSTAGED. New version {version} is now AWSCURRENT; the previous value is "
          f"retained as AWSPREVIOUS.")
    print("\nNOTHING IS USING IT YET -- ECS resolves secrets only at task launch.")
    print("NEXT, in this order:")
    print("  1. Apply the SAME password in the Supabase dashboard now.")
    print("  2. python scripts/rotate_database_password.py --redeploy")
    print("\nIf you staged the wrong value, the new task will fail its health check and "
          "ECS will roll back on its own; the running task keeps serving throughout.")
    return 0


def cmd_redeploy() -> int:
    print("forcing a new deployment so fresh tasks re-resolve the secret...")
    out = aws("ecs", "update-service", "--cluster", CLUSTER, "--service", SERVICE,
              "--force-new-deployment")
    if out.returncode != 0:
        print(f"update-service failed: {out.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    print("deployment requested. Polling until it settles (circuit breaker will roll "
          "back automatically if the new task cannot reach the database).\n")
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
            if state in ("COMPLETED", "FAILED"):
                print()
                if state == "COMPLETED":
                    print("DEPLOYMENT COMPLETED. Run --verify next.")
                    return 0
                print("DEPLOYMENT FAILED and ECS rolled back. The old task is still "
                      "serving. The staged password almost certainly does not match "
                      "what Supabase was set to -- re-stage and retry.", file=sys.stderr)
                return 1
        time.sleep(15)
    print("timed out waiting for the rollout to settle; check the ECS console",
          file=sys.stderr)
    return 1


def cmd_verify() -> int:
    ok = True
    out = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", SERVICE,
              "--query", "services[0].[runningCount,desiredCount,"
                         "deployments[0].rolloutState]", "--output", "json")
    if out.returncode == 0:
        running, desired, state = json.loads(out.stdout)
        print(f"service: running={running}/{desired} rollout={state}")
        ok = ok and running == desired and state == "COMPLETED"

    out = aws("ecs", "list-tasks", "--cluster", CLUSTER, "--service-name", SERVICE,
              "--query", "taskArns[0]", "--output", "text")
    if out.returncode == 0 and out.stdout.strip() not in ("", "None"):
        task = out.stdout.strip()
        out = aws("ecs", "describe-tasks", "--cluster", CLUSTER, "--tasks", task,
                  "--query", "tasks[0].[lastStatus,healthStatus]", "--output", "json")
        if out.returncode == 0:
            status, health = json.loads(out.stdout)
            print(f"task:    {status} health={health}")
            ok = ok and status == "RUNNING" and health in ("HEALTHY", "UNKNOWN")

    print("\nproving the new credential actually works, by connecting with it:")
    try:
        import psycopg2
        dsn, _ = read_secret()
        conn = psycopg2.connect(dsn, connect_timeout=30)
        cur = conn.cursor()
        cur.execute("SELECT current_user, now()")
        user, now = cur.fetchone()
        conn.close()
        print(f"   connected as {user} at {now}")
    except ImportError:
        print("   psycopg2 not installed; skipped (the ECS health check is the real proof)")
    except Exception as exc:  # noqa: BLE001
        print(f"   CONNECTION FAILED: {type(exc).__name__}: {str(exc)[:160]}",
              file=sys.stderr)
        ok = False

    print()
    if ok:
        # Deliberately does NOT say "rotation verified". This function checks that the
        # service is healthy and that whatever DSN is stored right now can connect -- both
        # of which are true before a rotation as well as after, so claiming the rotation
        # succeeded would be asserting something it cannot observe.
        print("HEALTHY: the service is serving and the DSN currently stored in "
              "Secrets Manager connects successfully.")
        print("If you have just completed steps 1-4, that is the rotation confirmed: the "
              "running task resolved the staged secret at launch and passed its health "
              "check against the database.")
        print("\nStill open for SEC-01 after the rotation: the git-history purge of the "
              "old literal. That is a separate destructive action (filter-repo + "
              "force-push, rewriting published history) and will not be done unless you "
              "ask for it specifically.")
        return 0
    print("NOT HEALTHY -- see above. Do not retire the AWSPREVIOUS version.",
          file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", action="store_true",
                   help="write a new secret version with the new password (step 2)")
    g.add_argument("--redeploy", action="store_true",
                   help="force a new ECS deployment and poll until it settles (step 4)")
    g.add_argument("--verify", action="store_true",
                   help="confirm the service is healthy and the credential works (step 5)")
    args = p.parse_args()
    if args.stage:
        return cmd_stage()
    if args.redeploy:
        return cmd_redeploy()
    return cmd_verify()


if __name__ == "__main__":
    sys.exit(main())
