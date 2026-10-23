#!/usr/bin/env python3
"""
scripts/aws_validation.py

Automated AWS Infrastructure & Resource Validation Script
Validates:
 1. AWS Credentials & Identity (STS caller identity)
 2. IAM Roles (ecsTaskExecutionRole)
 3. ECR Repositories (vyomquant-api, vyomquant-tee, vyomquant-mds)
 4. AWS Secrets Manager Secret Paths
 5. ECS Task Definition Schema (ecs-task-definition-full.json)
 6. ECS Cluster (vyomquant-cluster) & Service (vyomquant-api-service-cjema2sl)
 7. CloudWatch Log Groups (/ecs/vyomquant-api)
"""

import json
import os
import subprocess
import sys
from typing import Dict, List, Tuple


#: SEC-01: this used to be a HARDCODED list of six `/vyomquant/production/UPPERCASE`
#: paths. Every one of them was wrong in the way that matters most for a deployment
#: gate: the running task definition references eleven `vyomquant/production/lowercase`
#: secrets and NONE of the six. So this check verified the existence of secrets the
#: application does not read, while never checking a single one it does -- it would have
#: passed with `vyomquant/production/database_url` deleted.
#:
#: The list is now DERIVED from the task definition's own `secrets[].valueFrom`, which
#: is the file this script already validates two functions below. A hardcoded list can
#: drift from the deployment; a derived one cannot.
def required_secrets(filepath: str = "ecs-task-definition-full.json") -> List[str]:
    """Secret names the task definition actually resolves, read from it directly."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            td = json.load(fh)
    except Exception:
        return []
    names = []
    for container in td.get("containerDefinitions", []) or []:
        for entry in container.get("secrets", []) or []:
            arn = entry.get("valueFrom", "")
            if not arn:
                continue
            # arn:aws:secretsmanager:<region>:<acct>:secret:<name>[-<suffix>]
            marker = ":secret:"
            name = arn.split(marker, 1)[1] if marker in arn else arn
            names.append(name)
    return sorted(set(names))

REQUIRED_ECR_REPOS = [
    "vyomquant-api",
    "vyomquant-tee",
    "vyomquant-mds"
]

AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
ECS_CLUSTER = "vyomquant-cluster"
ECS_SERVICE = "vyomquant-api-service-cjema2sl"
LOG_GROUP = "/ecs/vyomquant-api"


def run_aws_cmd(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    """Execute AWS CLI command."""
    full_cmd = ["aws"] + cmd + ["--region", AWS_REGION, "--output", "json"]
    try:
        proc = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


def validate_task_definition_file(filepath: str = "ecs-task-definition-full.json") -> Dict:
    """Validate local task definition file structure and ARNs."""
    res = {"valid": True, "errors": [], "family": "", "containers": []}
    if not os.path.exists(filepath):
        res["valid"] = False
        res["errors"].append(f"Task definition file {filepath} not found.")
        return res

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            td = json.load(f)

        res["family"] = td.get("family", "")
        containers = td.get("containerDefinitions", [])
        res["containers"] = [c.get("name") for c in containers]

        if not td.get("executionRoleArn"):
            res["errors"].append("Missing executionRoleArn in task definition.")
        if not td.get("taskRoleArn"):
            res["errors"].append("Missing taskRoleArn in task definition.")
        if not containers:
            res["errors"].append("No containerDefinitions defined.")
        else:
            main_c = containers[0]
            if not main_c.get("image"):
                res["errors"].append("Container image missing.")
            if not main_c.get("secrets"):
                res["errors"].append("No secrets defined in task definition.")

    except Exception as e:
        res["valid"] = False
        res["errors"].append(f"JSON Parse error in {filepath}: {e}")

    if res["errors"]:
        res["valid"] = False

    return res


def validate_aws_live() -> Dict:
    """Validate live AWS resources if credentials are available."""
    report = {
        "credentials_valid": False,
        "identity": {},
        "ecr_repos": {},
        "secrets": {},
        "ecs_cluster": False,
        "ecs_service": False,
        "log_group": False,
        "errors": []
    }

    # 1. Identity Check
    code, stdout, stderr = run_aws_cmd(["sts", "get-caller-identity"])
    if code != 0:
        report["errors"].append(f"AWS STS check failed: {stderr.strip()}")
        return report

    report["credentials_valid"] = True
    try:
        report["identity"] = json.loads(stdout)
    except Exception:
        pass

    # 2. ECR Repository Validation
    for repo in REQUIRED_ECR_REPOS:
        code, stdout, stderr = run_aws_cmd(["ecr", "describe-repositories", "--repository-names", repo])
        report["ecr_repos"][repo] = (code == 0)
        if code != 0:
            report["errors"].append(f"ECR repo missing or inaccessible: {repo}")

    # 3. Secrets Manager Validation
    #
    # SEC-01: `describe-secret` succeeding is NOT sufficient and that gap was real.
    # `vyomquant/production/supabase_key` describes fine -- Terraform created it -- but
    # has ZERO versions, so `get-secret-value` returns ResourceNotFound and a task
    # consuming it would fail at launch. A gate that only describes cannot see that.
    # So this checks the secret both EXISTS and HAS A RETRIEVABLE VERSION, without ever
    # reading the value itself.
    secrets_to_check = required_secrets()
    if not secrets_to_check:
        report["errors"].append(
            "Could not derive any required secret from the task definition; refusing "
            "to report the secret check as passing on an empty list."
        )
    for sec in secrets_to_check:
        code, stdout, stderr = run_aws_cmd(
            ["secretsmanager", "describe-secret", "--secret-id", sec])
        if code != 0:
            report["secrets"][sec] = False
            report["errors"].append(f"Secrets Manager secret missing: {sec}")
            continue
        vcode, vout, verr = run_aws_cmd(
            ["secretsmanager", "list-secret-version-ids", "--secret-id", sec])
        has_version = False
        if vcode == 0:
            try:
                has_version = bool(json.loads(vout).get("Versions"))
            except Exception:
                has_version = False
        report["secrets"][sec] = has_version
        if not has_version:
            report["errors"].append(
                f"Secrets Manager secret exists but has NO retrievable version, so a "
                f"task consuming it will fail at launch: {sec}"
            )

    # 4. ECS Cluster Validation
    code, stdout, stderr = run_aws_cmd(["ecs", "describe-clusters", "--clusters", ECS_CLUSTER])
    if code == 0:
        report["ecs_cluster"] = True
    else:
        report["errors"].append(f"ECS Cluster {ECS_CLUSTER} not found.")

    # 5. ECS Service Validation
    code, stdout, stderr = run_aws_cmd(["ecs", "describe-services", "--cluster", ECS_CLUSTER, "--services", ECS_SERVICE])
    if code == 0:
        report["ecs_service"] = True
    else:
        report["errors"].append(f"ECS Service {ECS_SERVICE} not found in cluster {ECS_CLUSTER}.")

    # 6. CloudWatch Log Group Validation
    code, stdout, stderr = run_aws_cmd(["logs", "describe-log-groups", "--log-group-name-prefix", LOG_GROUP])
    if code == 0:
        report["log_group"] = True
    else:
        report["errors"].append(f"CloudWatch log group {LOG_GROUP} check failed.")

    return report


def main():
    strict_mode = "--strict" in sys.argv
    print(f"=== AUTOMATED AWS RESOURCE VALIDATION (Region: {AWS_REGION}) ===")

    td_res = validate_task_definition_file()
    print(f"Task Definition File ({td_res.get('family')}): {'PASS' if td_res['valid'] else 'FAIL'}")
    if td_res["errors"]:
        for err in td_res["errors"]:
            print(f"  ❌ {err}")

    aws_res = validate_aws_live()
    print(f"AWS CLI & Credentials: {'CONNECTED' if aws_res['credentials_valid'] else 'NOT CONFIGURED / UNREACHABLE'}")

    if aws_res["credentials_valid"]:
        print(f"  Account: {aws_res['identity'].get('Account')}")
        print(f"  Arn:     {aws_res['identity'].get('Arn')}")

        all_ecr_ok = all(aws_res["ecr_repos"].values())
        print(f"ECR Repositories: {'ALL PASS' if all_ecr_ok else 'SOME MISSING'}")

        all_sec_ok = all(aws_res["secrets"].values())
        print(f"Secrets Manager: {'ALL PASS' if all_sec_ok else 'SOME MISSING'}")

        print(f"ECS Cluster ({ECS_CLUSTER}): {'PASS' if aws_res['ecs_cluster'] else 'FAIL'}")
        print(f"ECS Service ({ECS_SERVICE}): {'PASS' if aws_res['ecs_service'] else 'FAIL'}")
    else:
        print("Note: Live AWS calls skipped due to missing or unconfigured AWS credentials.")

    status = "PASS"
    if not td_res["valid"]:
        status = "FAIL"

    # SEC-01: A MISSING REQUIRED SECRET NOW FAILS THE GATE WITHOUT --strict.
    #
    # Previously only `--strict` could fail on this, and the deploy workflow does not
    # pass it (.github/workflows/03-deploy.yml:144 runs the engine with no flags). So
    # "Secrets Manager: SOME MISSING" printed and the gate passed anyway -- a
    # pre-deployment check that reported the exact condition that breaks a task launch
    # and then waved it through.
    #
    # Scoped deliberately: this only fails when credentials WERE valid, i.e. the check
    # actually ran. A credential-less run (a PR check, a laptop) skips the live calls
    # entirely and must not be failed for something it could not look at -- that would
    # make the gate unrunnable outside deployment, which is how `--strict` ended up
    # never being passed in the first place.
    if aws_res["credentials_valid"]:
        missing = [name for name, ok in aws_res["secrets"].items() if not ok]
        if missing or not aws_res["secrets"]:
            status = "FAIL"

    if strict_mode and (not aws_res["credentials_valid"] or aws_res["errors"]):
        status = "FAIL"

    os.makedirs("reports", exist_ok=True)
    report_data = {
        "status": status,
        "region": AWS_REGION,
        "task_definition": td_res,
        "aws_live": aws_res
    }

    with open("reports/aws_validation_results.json", "w") as f:
        json.dump(report_data, f, indent=2)

    if status == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
