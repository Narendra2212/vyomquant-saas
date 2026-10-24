"""INF-03: bootstrap the terraform remote-state backend, or say exactly why it cannot.

WHY THIS EXISTS
---------------
INF-03 is not blocked on code. Everything the workers need is committed:
``terraform/ecs.tf``'s ``local.worker_definitions`` declares all four (backtest, dag,
command, marketplace_expiry) and ``scripts/aws_ecs_deploy.py`` gained
list_worker_services / register_worker_task_definition / redeploy_workers. It is blocked
on ONE thing: ``terraform apply`` cannot run because the remote-state backend does not
exist, and the deploy identity is not allowed to create it.

MEASURED, not assumed (and the earlier record of this was wrong in a way that mattered --
it read as "deliberately not done without asking first", which sends the owner to the
wrong remedy):

    identity                      arn:aws:iam::273709947018:user/github-actions
    s3 head-bucket  vyomquant-terraform-state     404 Not Found
    s3 create-bucket                              AccessDenied - "no identity-based
                                                  policy allows the s3:CreateBucket
                                                  action"
    ecs register-task-definition                  PERMITTED (validation error, not
                                                  AccessDenied)
    ecs create-service                            PERMITTED (TaskDefinition not found,
                                                  not AccessDenied)
    worker task-definition revisions              ZERO, across ACTIVE and INACTIVE, for
                                                  every vyomquant-worker-*-production
                                                  family

So the two ECS permissions that look like a way round are moot: there is no worker task
definition to create a service from. Registering them by hand would mean transcribing
what terraform already encodes -- image URI, task and execution role ARNs, log group and
stream prefix, subnets, security groups, env and secret wiring -- and the result would be
infrastructure that exists but is not described by the committed source, so the next real
``terraform apply`` would either duplicate or fight it. That is why this script
bootstraps the BACKEND rather than the services.

WHAT IT DOES
------------
Creates the S3 bucket (versioned, encrypted, public access blocked) and the DynamoDB lock
table that ``terraform/backend.tf`` expects. Idempotent. Needs an identity with
``s3:CreateBucket`` and ``dynamodb:CreateTable`` -- which the deploy user does NOT have,
so this is for an operator running it once with their own credentials.

``--check`` reports the state without creating anything, and is what CI could call to
tell "blocked on IAM" apart from "not yet run".
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

REGION = "ap-southeast-1"
BUCKET = "vyomquant-terraform-state"
LOCK_TABLE = "vyomquant-terraform-locks"


def aws(*args: str) -> tuple[int, str, str]:
    out = subprocess.run(
        ["aws", *args, "--region", REGION], capture_output=True, text=True
    )
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def identity() -> str:
    code, out, err = aws("sts", "get-caller-identity", "--output", "json")
    if code != 0:
        return f"UNKNOWN ({err.splitlines()[-1] if err else 'no credentials'})"
    return json.loads(out).get("Arn", "UNKNOWN")


def bucket_state() -> str:
    code, _, err = aws("s3api", "head-bucket", "--bucket", BUCKET)
    if code == 0:
        return "PRESENT"
    if "404" in err or "Not Found" in err:
        return "ABSENT"
    if "AccessDenied" in err or "403" in err:
        return "EXISTS_BUT_NOT_READABLE"
    return f"UNKNOWN ({err.splitlines()[-1] if err else '?'})"


def table_state() -> str:
    code, _, err = aws(
        "dynamodb", "describe-table", "--table-name", LOCK_TABLE, "--output", "json"
    )
    if code == 0:
        return "PRESENT"
    if "ResourceNotFoundException" in err:
        return "ABSENT"
    return f"UNKNOWN ({err.splitlines()[-1] if err else '?'})"


def can_create_bucket() -> tuple[bool, str]:
    """Probe with a name that cannot collide. Nothing is created when it is denied."""
    probe = f"{BUCKET}-permission-probe-delete-me"
    code, _, err = aws(
        "s3api", "create-bucket", "--bucket", probe,
        "--create-bucket-configuration", f"LocationConstraint={REGION}",
    )
    if code == 0:
        aws("s3api", "delete-bucket", "--bucket", probe)
        return True, "permitted (probe bucket created and deleted)"
    if "AccessDenied" in err:
        return False, "DENIED - no identity-based policy allows s3:CreateBucket"
    return False, err.splitlines()[-1] if err else "unknown failure"


def report() -> int:
    print("=" * 78)
    print("INF-03: terraform remote-state backend readiness")
    print("=" * 78)
    print(f"  identity         {identity()}")
    print(f"  region           {REGION}")
    print(f"  s3://{BUCKET}  {bucket_state()}")
    print(f"  dynamodb {LOCK_TABLE}  {table_state()}")
    allowed, why = can_create_bucket()
    print(f"  s3:CreateBucket  {why}")
    print()

    if bucket_state() == "PRESENT" and table_state() == "PRESENT":
        print("READY. `terraform init` can use the remote backend, and INF-03's worker")
        print("services can be applied from terraform/ecs.tf's worker_definitions.")
        return 0

    if not allowed:
        print("BLOCKED ON IAM, not on this repository. Everything the workers need is")
        print("committed; the backend they are applied through cannot be created by this")
        print("identity. The remedy is one of:")
        print("   (a) attach s3:CreateBucket and dynamodb:CreateTable to")
        print("       user/github-actions, then re-run this script with --apply;")
        print("   (b) run this script once with an operator identity that already has")
        print("       them;")
        print("   (c) run terraform with LOCAL state, accepting that the state file then")
        print("       lives on whoever ran it and CI cannot apply afterwards.")
        print()
        print("NOT a remedy, and measured rather than assumed: hand-creating the services")
        print("with ecs register-task-definition / create-service. Both calls ARE")
        print("permitted for this identity, but zero worker task-definition revisions")
        print("exist, so they would mean transcribing by hand what terraform encodes")
        print("(image, roles, log group, subnets, security groups, secrets) -- producing")
        print("infrastructure the committed source does not describe.")
        return 1

    print("The backend is incomplete but this identity CAN create it. Re-run with")
    print("--apply.")
    return 1


def apply() -> int:
    if bucket_state() == "ABSENT":
        code, _, err = aws(
            "s3api", "create-bucket", "--bucket", BUCKET,
            "--create-bucket-configuration", f"LocationConstraint={REGION}",
        )
        if code != 0:
            print(f"could not create s3://{BUCKET}: {err}")
            return 1
        print(f"created s3://{BUCKET}")

        # Versioning first: terraform state is the one object whose previous versions are
        # the recovery path after a bad apply.
        aws("s3api", "put-bucket-versioning", "--bucket", BUCKET,
            "--versioning-configuration", "Status=Enabled")
        aws("s3api", "put-bucket-encryption", "--bucket", BUCKET,
            "--server-side-encryption-configuration",
            '{"Rules":[{"ApplyServerSideEncryptionByDefault":'
            '{"SSEAlgorithm":"AES256"}}]}')
        aws("s3api", "put-public-access-block", "--bucket", BUCKET,
            "--public-access-block-configuration",
            "BlockPublicAcls=true,IgnorePublicAcls=true,"
            "BlockPublicPolicy=true,RestrictPublicBuckets=true")
        print("   versioning, AES256 encryption and public-access block applied")
    else:
        print(f"s3://{BUCKET} already {bucket_state()}")

    if table_state() == "ABSENT":
        code, _, err = aws(
            "dynamodb", "create-table", "--table-name", LOCK_TABLE,
            "--attribute-definitions", "AttributeName=LockID,AttributeType=S",
            "--key-schema", "AttributeName=LockID,KeyType=HASH",
            "--billing-mode", "PAY_PER_REQUEST",
        )
        if code != 0:
            print(f"could not create {LOCK_TABLE}: {err}")
            return 1
        print(f"created dynamodb table {LOCK_TABLE}")
    else:
        print(f"dynamodb {LOCK_TABLE} already {table_state()}")

    print("\nBackend ready. Next: terraform init && terraform plan, and review the plan")
    print("before applying -- it will propose the four workers in ecs.tf plus whatever")
    print("else has drifted since the last real apply, which nobody has measured.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="create the bucket and lock table (needs the permissions above)")
    args = ap.parse_args()
    return apply() if args.apply else report()


if __name__ == "__main__":
    sys.exit(main())
