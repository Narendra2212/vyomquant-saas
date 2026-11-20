#!/usr/bin/env python3
"""SEC-01 step 2: rotate JWT_SECRET, which is published in this repository's history.

WHY THIS IS SAFER THAN THE DATABASE ROTATION
--------------------------------------------
``JWT_SECRET`` has no external counterpart. The database password had to be typed
into the Supabase dashboard as well as staged here, which is why that rotation
needs a human holding the value. Nothing outside this account needs to know
``JWT_SECRET``, so this script GENERATES IT IN-PROCESS and writes it straight to
Secrets Manager. The value is never printed, never prompted for, never pasted,
and never placed on a command line.

WHAT ROTATING IT BREAKS, MEASURED RATHER THAN ASSUMED
-----------------------------------------------------
It does NOT log users out. User sessions are Supabase-issued and verified with
``SUPABASE_JWT_SECRET``, which is a DIFFERENT secret and is NOT exposed (verified
by ``scripts/audit_secrets_in_git_history.py``).

``JWT_SECRET`` signs tokens the platform mints itself -- the model-download token
at ``backend_app/routers/strategy_operations.py:5704`` is the concrete case. Those
are short-lived. Rotating invalidates any outstanding ones, so a download link
someone is holding stops working and has to be re-issued. That is the whole blast
radius.

THE APPLICATION REFUSES TO BOOT ON A WEAK VALUE
-----------------------------------------------
``backend_app/main.py:320`` calls ``Settings.assert_jwt_secret_is_production_grade()``
during startup, which raises unless the secret clears every rule in
``Settings.jwt_secret_problems``. This script imports THE APPLICATION'S OWN
constants and applies the same rules before staging, so a value that would fail
the boot cannot be written in the first place. Importing them rather than
restating them means this script cannot drift from the rule it is enforcing.

USAGE
-----
    python scripts/rotate_jwt_secret.py --plan        # read-only, shows what will happen
    python scripts/rotate_jwt_secret.py --stage       # generate + write a new version
    python scripts/rotate_jwt_secret.py --redeploy    # roll tasks onto it
    python scripts/rotate_jwt_secret.py --verify      # confirm healthy + not in history
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time

SECRET_ID = "vyomquant/production/jwt_secret"
REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"
SERVICE = "vyomquant-api-service-cjema2sl"

# Generated length in bytes. RFC 7518 s3.2 requires >=32 for HS256; token_hex(32)
# yields 64 hex characters, i.e. 64 UTF-8 bytes, comfortably above the floor the
# application enforces.
TOKEN_BYTES = 32


def _app_rules() -> tuple[int, tuple]:
    """The application's OWN thresholds, imported so this cannot drift from them."""
    sys.path.insert(0, os.getcwd())
    os.environ.setdefault("ENV", "production")
    try:
        from backend_app.core.config import Settings  # noqa: PLC0415

        return Settings.MIN_JWT_SECRET_BYTES, Settings.WEAK_JWT_SECRET_PATTERNS
    except Exception as exc:  # noqa: BLE001
        print(f"could not import the application's JWT rules ({type(exc).__name__}: "
              f"{str(exc)[:120]}).", file=sys.stderr)
        print("Refusing to stage a secret against rules this script invented itself.",
              file=sys.stderr)
        sys.exit(2)


def _problems(secret: str, min_bytes: int, weak: tuple) -> list[str]:
    """Mirror of Settings.jwt_secret_problems, using the imported thresholds."""
    out = []
    n = len(secret.encode("utf-8"))
    if n < min_bytes:
        out.append(f"{n} bytes; needs at least {min_bytes}")
    lowered = secret.lower()
    matched = [p for p in weak if p in lowered]
    if matched:
        # The matching pattern is NOT named: doing so would write part of the
        # secret into this output, which is the SEC-11 mistake.
        out.append(f"matches {len(matched)} placeholder pattern(s)")
    if len(set(secret)) < 8:
        out.append(f"only {len(set(secret))} distinct characters")
    return out


def generate_acceptable() -> str:
    """A value that passes the application's own boot check. Never printed."""
    min_bytes, weak = _app_rules()
    for _ in range(200):
        candidate = secrets.token_hex(TOKEN_BYTES)
        if not _problems(candidate, min_bytes, weak):
            return candidate
    print("could not generate a value passing the application's rules in 200 tries",
          file=sys.stderr)
    sys.exit(2)


def aws(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *args, "--region", REGION],
                          capture_output=True, text=True, input=stdin)


def read_current() -> str | None:
    out = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
              "--query", "SecretString", "--output", "text")
    return out.stdout.strip() if out.returncode == 0 else None


def fingerprint(value: str) -> str:
    """A salted digest, so two runs can be compared without revealing anything."""
    return hashlib.sha256(b"jwt-rotation-fingerprint" + value.encode()).hexdigest()[:12]


def in_git_history(value: str) -> bool:
    out = subprocess.run(["git", "log", "--all", "--format=%H", f"-S{value}"],
                         capture_output=True, text=True, errors="replace")
    return bool(out.stdout.strip())


def cmd_plan() -> int:
    cur = read_current()
    if cur is None:
        print(f"cannot read {SECRET_ID}", file=sys.stderr)
        return 2
    min_bytes, weak = _app_rules()
    print("JWT_SECRET ROTATION PLAN (read-only)\n")
    print(f"  secret                 : {SECRET_ID}")
    print(f"  current length         : {len(cur)} characters "
          f"({len(cur.encode())} bytes)")
    print(f"  current fingerprint    : {fingerprint(cur)}")
    print(f"  currently in history   : "
          f"{'YES -- this is why we are rotating' if in_git_history(cur) else 'no'}")
    print(f"  app floor (imported)   : {min_bytes} bytes, "
          f"{len(weak)} placeholder patterns")
    problems = _problems(cur, min_bytes, weak)
    print(f"  current value is       : "
          f"{'production-grade' if not problems else 'NOT production-grade: ' + '; '.join(problems)}")
    print(f"\n  will generate          : {TOKEN_BYTES * 2} hex characters, in-process")
    print("  the new value is never printed, prompted for, or placed on a command line")
    print("\n  AFTER STAGING, NOTHING CHANGES until --redeploy: ECS resolves secrets")
    print("  only at task launch.")
    print("\n  BLAST RADIUS: outstanding platform-minted tokens (e.g. model-download")
    print("  links) stop working. User logins are UNAFFECTED -- they are verified with")
    print("  SUPABASE_JWT_SECRET, a different secret, which is not exposed.")
    return 0


def cmd_stage() -> int:
    cur = read_current()
    if cur is None:
        print(f"cannot read {SECRET_ID}", file=sys.stderr)
        return 2

    new = generate_acceptable()
    if new == cur:
        print("generated value equals the current one; aborting", file=sys.stderr)
        return 2

    print(f"current fingerprint : {fingerprint(cur)}")
    print(f"new fingerprint     : {fingerprint(new)}")
    print(f"new length          : {len(new)} characters ({len(new.encode())} bytes)")

    out = aws("secretsmanager", "put-secret-value", "--secret-id", SECRET_ID,
              "--secret-string", "fileb:///dev/stdin", stdin=new)
    if out.returncode != 0:
        # fileb:///dev/stdin is not portable on Windows; fall back to a temp file
        # that is deleted immediately.
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
            fh.write(new)
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
    print(f"\nSTAGED. Version {version} is AWSCURRENT; the previous value is AWSPREVIOUS.")
    print("\nNOTHING IS USING IT YET. Next:")
    print("  python scripts/rotate_jwt_secret.py --redeploy")
    return 0


def cmd_redeploy() -> int:
    print("forcing a new deployment so fresh tasks re-resolve the secret...")
    out = aws("ecs", "update-service", "--cluster", CLUSTER, "--service", SERVICE,
              "--force-new-deployment")
    if out.returncode != 0:
        print(f"update-service failed: {out.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    print("deployment requested; polling until it settles.\n")
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
                print("\nDEPLOYMENT COMPLETED. Run --verify next.")
                return 0
            if state == "FAILED":
                print("\nDEPLOYMENT FAILED and ECS rolled back; the old task still "
                      "serves. A weak secret would fail assert_jwt_secret_is_"
                      "production_grade() at boot -- but --stage applies those same "
                      "rules, so look at the task logs for a different cause.",
                      file=sys.stderr)
                return 1
        time.sleep(15)
    print("timed out waiting for the rollout; check the ECS console", file=sys.stderr)
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

    cur = read_current()
    if cur is None:
        print("cannot read the secret", file=sys.stderr)
        return 2

    leaked = in_git_history(cur)
    print(f"current fingerprint      : {fingerprint(cur)}")
    print(f"present in git history   : {'YES' if leaked else 'no'}")
    ok = ok and not leaked

    min_bytes, weak = _app_rules()
    problems = _problems(cur, min_bytes, weak)
    print(f"production-grade         : "
          f"{'yes' if not problems else 'NO: ' + '; '.join(problems)}")
    ok = ok and not problems

    print()
    if ok:
        print("ROTATED. The stored JWT_SECRET is production-grade, the service is")
        print("serving on it, and the value does not appear anywhere in git history.")
        print("\nThe OLD value is still in history and always will be until the purge.")
        print("That is now harmless: it no longer signs or verifies anything.")
        return 0
    print("NOT CONFIRMED -- see above.", file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="read-only; show what will happen")
    g.add_argument("--stage", action="store_true", help="generate and write a new version")
    g.add_argument("--redeploy", action="store_true", help="roll tasks onto the new value")
    g.add_argument("--verify", action="store_true", help="confirm healthy and not leaked")
    a = p.parse_args()
    if a.plan:
        return cmd_plan()
    if a.stage:
        return cmd_stage()
    if a.redeploy:
        return cmd_redeploy()
    return cmd_verify()


if __name__ == "__main__":
    sys.exit(main())
