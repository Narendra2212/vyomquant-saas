#!/usr/bin/env python3
"""SEC-01 pre-flight: confirm every precondition WITHOUT touching anything.

Read-only. Prints no secret material: only the DSN components that are not the
password, so the operator can confirm which database they are about to rotate.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "rot", HERE / "rotate_database_password.py"
)
rot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rot)


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'XX '}] {label}" + (f"  {detail}" if detail else ""))
    return ok


def main() -> int:
    print("SEC-01 PRE-FLIGHT (read-only, changes nothing)\n")
    all_ok = True

    out = subprocess.run(["aws", "sts", "get-caller-identity", "--output", "json"],
                         capture_output=True, text=True)
    ident = json.loads(out.stdout) if out.returncode == 0 else {}
    all_ok &= check("AWS credentials resolve", out.returncode == 0,
                    ident.get("Arn", out.stderr.strip()[:120]))

    try:
        dsn, container = rot.read_secret()
        all_ok &= check(f"Secrets Manager: {rot.SECRET_ID} readable", True)
        print(f"         stored as: "
              f"{'a bare string' if container == 'raw' else f'JSON key {container!r}'}")
        print(f"         {rot.describe(dsn)}")
        # The project ref is in the USERNAME for a Supavisor pooler DSN
        # (postgres.<ref>@aws-N-<region>.pooler.supabase.com), NOT in the hostname --
        # the hostname is the shared regional pooler and is the same for every project.
        from urllib.parse import urlsplit
        user = urlsplit(dsn).username or ""
        ref = user.split(".", 1)[1] if "." in user else "?"
        print(f"\n         -> Supabase project ref: {ref}")
        print(f"         -> Dashboard: https://supabase.com/dashboard/project/"
              f"{ref}/settings/database")
        print(f"         -> NOTE: port {urlsplit(dsn).port} is the Supavisor pooler, not "
              f"the direct database port. Rotating the project's database password "
              f"rotates this credential too; the pooler user shares it.")
    except SystemExit:
        all_ok &= check(f"Secrets Manager: {rot.SECRET_ID} readable", False)

    out = rot.aws("ecs", "describe-services", "--cluster", rot.CLUSTER,
                  "--services", rot.SERVICE, "--query",
                  "services[0].[status,runningCount,desiredCount,"
                  "deploymentConfiguration.minimumHealthyPercent,"
                  "deploymentConfiguration.maximumPercent,"
                  "deploymentConfiguration.deploymentCircuitBreaker.rollback]",
                  "--output", "json")
    if out.returncode == 0:
        status, run, des, minp, maxp, rollback = json.loads(out.stdout)
        all_ok &= check("ECS service reachable", status == "ACTIVE",
                        f"{status} running={run}/{des}")
        all_ok &= check("Rolling deploy never passes through zero tasks",
                        minp == 100, f"minimumHealthyPercent={minp} maximumPercent={maxp}")
        all_ok &= check("Circuit breaker will auto-rollback a bad password",
                        bool(rollback), f"rollback={rollback}")
    else:
        all_ok &= check("ECS service reachable", False, out.stderr.strip()[:120])

    try:
        import psycopg2  # noqa: F401
        all_ok &= check("psycopg2 present (so --verify can prove the credential)", True)
    except ImportError:
        check("psycopg2 present (so --verify can prove the credential)", False,
              "optional; ECS health check is the real proof")

    print()
    if all_ok:
        print("ALL PRECONDITIONS MET. The rotation is safe to run.")
        print("Nothing above modified anything.")
    else:
        print("ONE OR MORE PRECONDITIONS FAILED -- do not start the rotation.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
