#!/usr/bin/env python3
"""SEC-01 step 4: delete the orphaned uppercase Secrets Manager entries.

WHAT THESE ARE
--------------
Six `/vyomquant/production/UPPERCASE` secrets (note the leading slash) that no
longer correspond to anything. The deployment reads eleven
`vyomquant/production/lowercase` secrets instead. Two of the six held the OLD
published values for master_encryption_keys and jwt_secret; three MIRROR current
live values; one holds a third, stale database password.

WHY DELETE RATHER THAN ROTATE
-----------------------------
The two holding published values authenticate nothing now that the secrets ECS
actually reads have been rotated, so there is no credential to rotate. The three
mirroring live values are the more dangerous class: a duplicate copy of a live
high-privilege secret, in a location no deployment reads and no rotation
procedure updates. The next rotation leaves them silently stale, and an operator
who later wires one up deploys a credential the team believes was retired. More
copies of a live secret is strictly more attack surface for no benefit.

SAFETY
------
* Re-verifies, immediately before each delete, that the secret is absent from the
  RUNNING task definition. A check done minutes earlier is not a check.
* Uses a 30-day recovery window, so every delete is reversible with
  `aws secretsmanager restore-secret`.
* --dry-run by default. Deleting requires --apply.
* Refuses to touch anything whose name does not start with the orphan prefix.

USAGE
-----
    python scripts/delete_orphaned_secrets.py            # dry run
    python scripts/delete_orphaned_secrets.py --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"
SERVICE = "vyomquant-api-service-cjema2sl"
RECOVERY_DAYS = 30

#: The leading slash is the whole point: it is what distinguishes these from the
#: live secrets. A guard below refuses to delete anything without it, so a typo
#: cannot target a live secret.
ORPHAN_PREFIX = "/vyomquant/"

TARGETS = [
    "/vyomquant/production/MASTER_ENCRYPTION_KEYS",
    "/vyomquant/production/JWT_SECRET",
    "/vyomquant/production/DATABASE_URL",
    "/vyomquant/production/SUPABASE_JWT_SECRET",
    "/vyomquant/production/SUPABASE_SERVICE_ROLE_KEY",
    "/vyomquant/production/SUPABASE_ANON_KEY",
]


def aws(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *a, "--region", REGION],
                          capture_output=True, text=True)


def running_task_secret_arns() -> set[str]:
    td = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", SERVICE,
             "--query", "services[0].taskDefinition", "--output", "text").stdout.strip()
    if not td:
        print("could not read the running task definition; refusing to proceed",
              file=sys.stderr)
        sys.exit(2)
    out = aws("ecs", "describe-task-definition", "--task-definition", td,
              "--query", "taskDefinition.containerDefinitions[].secrets[].valueFrom",
              "--output", "json")
    try:
        return set(json.loads(out.stdout) or [])
    except ValueError:
        print("could not parse the task definition secrets; refusing to proceed",
              file=sys.stderr)
        sys.exit(2)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="actually delete. Without this, nothing is changed.")
    args = p.parse_args()

    live = running_task_secret_arns()
    print(f"running task definition references {len(live)} secret ARN(s)\n")
    if not live:
        print("the running task references NO secrets, which cannot be right; "
              "refusing to proceed", file=sys.stderr)
        return 2

    print("=" * 74)
    print("DRY RUN - nothing will change" if not args.apply else "APPLYING DELETIONS")
    print("=" * 74)

    deleted, skipped = 0, 0
    for name in TARGETS:
        if not name.startswith(ORPHAN_PREFIX):
            print(f"  REFUSED  {name}  (does not start with {ORPHAN_PREFIX!r})")
            skipped += 1
            continue

        exists = aws("secretsmanager", "describe-secret", "--secret-id", name).returncode == 0
        if not exists:
            print(f"  absent   {name}  (already gone)")
            continue

        # Re-verify NOW, not from an earlier pass.
        if any(name in arn for arn in live):
            print(f"  REFUSED  {name}  *** REFERENCED BY THE RUNNING TASK ***")
            skipped += 1
            continue

        if not args.apply:
            print(f"  would delete  {name}")
            deleted += 1
            continue

        out = aws("secretsmanager", "delete-secret", "--secret-id", name,
                  "--recovery-window-in-days", str(RECOVERY_DAYS))
        if out.returncode != 0:
            print(f"  FAILED   {name}: {out.stderr.strip()[:140]}")
            skipped += 1
            continue
        try:
            when = json.loads(out.stdout).get("DeletionDate", "?")
        except ValueError:
            when = "?"
        print(f"  deleted  {name}  (recoverable until {when})")
        deleted += 1

    print("\n" + "=" * 74)
    if args.apply:
        print(f"deleted {deleted}, skipped {skipped}")
        print(f"\nEvery deletion is reversible for {RECOVERY_DAYS} days:")
        print("  aws secretsmanager restore-secret --secret-id <name> "
              f"--region {REGION}")
    else:
        print(f"{deleted} would be deleted, {skipped} refused. Re-run with --apply.")
    return 1 if skipped else 0


if __name__ == "__main__":
    sys.exit(main())
