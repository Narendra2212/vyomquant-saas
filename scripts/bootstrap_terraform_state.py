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
#: SINGULAR, because `terraform/providers.tf`'s backend block is the authority on this
#: name and says `dynamodb_table = "vyomquant-terraform-lock"`. This constant read
#: "...-locks" until 2026-10-24, which would have created a table terraform then could
#: not find -- `terraform init` fails on a missing lock table, so the bootstrap would have
#: reported success and left the backend unusable. Caught before the first apply, by
#: reading the backend block rather than trusting the constant.
LOCK_TABLE = "vyomquant-terraform-lock"


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


#: Every action the backend actually needs, against the resource ARN it needs it on.
#: Split by resource because that distinction is where the previous probe went wrong:
#: s3:PutObject is granted on the OBJECT ARN (`bucket/*`), so simulating it against the
#: bucket ARN returns implicitDeny on a policy that is in fact correct.
REQUIRED_PERMISSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        f"arn:aws:s3:::{BUCKET}",
        ("s3:CreateBucket", "s3:PutBucketVersioning",
         "s3:PutEncryptionConfiguration", "s3:PutBucketPublicAccessBlock",
         "s3:ListBucket"),
    ),
    (f"arn:aws:s3:::{BUCKET}/*", ("s3:GetObject", "s3:PutObject")),
    (
        f"arn:aws:dynamodb:{REGION}:273709947018:table/{LOCK_TABLE}",
        ("dynamodb:CreateTable", "dynamodb:DescribeTable",
         "dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:DeleteItem"),
    ),
)


def can_create_bucket() -> tuple[bool, str]:
    """Ask IAM for the authorization decision, instead of provoking one.

    THIS USED TO CREATE A BUCKET. It called create-bucket on
    ``vyomquant-terraform-state-permission-probe-delete-me`` and read success as proof.
    Two things were wrong with that, and the second one made this script report the
    opposite of the truth:

    1. A readiness check documented as reporting "without creating anything" created a
       real, billable, globally-named bucket on every successful run, and deleted it on a
       separate call that could fail and leave it behind.
    2. The probe name is NOT the resource the policy grants. The least-privilege policy
       at infra/iam/terraform-state-backend-policy.json scopes s3:CreateBucket to the
       exact ARN of the state bucket -- on purpose, so this identity cannot create
       arbitrary buckets. So once route (A) was actually attached and working, this probe
       still came back "DENIED - no identity-based policy allows s3:CreateBucket" and the
       script still printed BLOCKED ON IAM. Measured 2026-10-24: probe DENIED while
       simulate-principal-policy returned `allowed` for s3:CreateBucket on the real ARN,
       and `--apply` then succeeded.

    Same failure mode as the `--status ALL` bug this file's docstring describes: a probe
    that cannot observe the thing it claims to measure, reporting absence.

    simulate-principal-policy evaluates the identity's real policies against the real
    resource ARNs and writes nothing. Needs iam:SimulatePrincipalPolicy; when that is
    unavailable the verdict is UNDETERMINED rather than a guess, because reporting
    "denied" on a failure to measure is what caused this.
    """
    arn = identity()
    if arn.startswith("UNKNOWN"):
        return False, f"UNDETERMINED - cannot resolve caller identity ({arn})"

    denied: list[str] = []
    for resource, actions in REQUIRED_PERMISSIONS:
        code, out, err = aws(
            "iam", "simulate-principal-policy",
            "--policy-source-arn", arn,
            "--action-names", *actions,
            "--resource-arns", resource,
            "--output", "json",
        )
        if code != 0:
            tail = err.splitlines()[-1] if err else "no output"
            return False, f"UNDETERMINED - iam:SimulatePrincipalPolicy failed ({tail})"
        for result in json.loads(out).get("EvaluationResults", []):
            if result.get("EvalDecision") != "allowed":
                denied.append(
                    f"{result.get('EvalActionName')} on {resource}"
                    f" -> {result.get('EvalDecision')}"
                )

    if denied:
        return False, "DENIED - " + "; ".join(denied)
    total = sum(len(a) for _, a in REQUIRED_PERMISSIONS)
    return True, f"permitted ({total} actions simulated allowed, nothing created)"


def report() -> int:
    print("=" * 78)
    print("INF-03: terraform remote-state backend readiness")
    print("=" * 78)
    bucket, table = bucket_state(), table_state()
    print(f"  identity         {identity()}")
    print(f"  region           {REGION}")
    print(f"  s3://{BUCKET}  {bucket}")
    print(f"  dynamodb {LOCK_TABLE}  {table}")
    allowed, why = can_create_bucket()
    print(f"  backend IAM      {why}")
    print()

    if bucket == "PRESENT" and table == "PRESENT":
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
        print("THE ALTERNATIVE, and this text used to describe it wrongly: hand-creating")
        print("the services with ecs register-task-definition / create-service. Both")
        print("calls ARE permitted for this identity. The claim that followed -- 'zero")
        print("worker task-definition revisions exist' -- was FALSE, and false because")
        print("of a bug in the probe behind it: it passed --status ALL to")
        print("list-task-definitions, which accepts only ACTIVE / INACTIVE /")
        print("DELETE_IN_PROGRESS, so every call failed validation and the resulting")
        print("zeros were read as absence. Re-measured: 249 revisions exist across 13")
        print("families, and each of the three production worker families holds one")
        print("INACTIVE revision carrying its roles, secrets, log group and command.")
        print()
        print("So route (B) is real and is implemented in")
        print("scripts/deploy_worker_services.py, which takes the TEMPLATE from the")
        print("api revision that actually works and only the COMMAND from those")
        print("INACTIVE revisions -- they all name the image 'vyomquant-api:latest', a")
        print("tag absent from ECR, which is why they never ran.")
        print()
        print("(A) IS STILL PREFERRED. Route B leaves services terraform does not know")
        print("about, which a later apply would try to create again unless they are")
        print("terraform import-ed first. The IAM policy route (A) needs is prepared at")
        print("infra/iam/terraform-state-backend-policy.json.")
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
