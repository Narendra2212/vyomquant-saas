#!/usr/bin/env python3
"""INF-03: register and create the worker ECS services, modelled on the one that works.

WHAT THIS IS FOR
================
INF-03: "Market-data service and background workers are not in the committed production
deployment." Production runs exactly ONE ECS service, `vyomquant-api-service-cjema2sl`.
The three worker families exist but hold only INACTIVE revisions, and nothing runs them.

WHY THE INACTIVE REVISIONS CANNOT SIMPLY BE RE-REGISTERED
=========================================================
Measured: `vyomquant-worker-backtest-production:1`, `-command-production:1` and
`-dag-production:1` all name the image `vyomquant-api:latest` -- a BARE repository
reference, which Docker resolves against Docker Hub rather than ECR, and the ECR
repository holds 305 images and NO `latest` tag. They also use a different role
generation (`VyomQuantEcsTaskExecutionRole-production`) from the revision actually serving
traffic (`ecsTaskExecutionRole`). That set is an abandoned naming generation which could
never have pulled an image.

So this takes the TEMPLATE from the revision that demonstrably works -- `vyomquant-api`'s
current ACTIVE revision, with its full ECR URI at a real commit tag, its roles, its log
group, its env and its secrets -- and takes ONLY the `command` from the INACTIVE worker
revisions. That is the one thing those revisions got right and the one thing the template
does not have.

ROUTE (B) OF TWO, AND IT IS THE SHORTCUT
========================================
INF-03 records two routes. (A) grant the deploy identity `s3:CreateBucket` +
`dynamodb:CreateTable`, run `scripts/bootstrap_terraform_state.py --apply`, and let
terraform own the result -- slower, and the only route that leaves the infrastructure
described by the repository. (B) is this script: it works with permissions already held
(`ecs:RegisterTaskDefinition` and `ecs:CreateService` are both PERMITTED, measured) and
leaves services terraform does not know about, which a later `terraform apply` would try
to create again unless they are `terraform import`ed first.

(A) IS PREFERRED. This exists so that (B) is a deliberate, recorded shortcut with its
consequence stated, rather than an undocumented sequence of console clicks.

SAFETY
======
`--plan` is the default and calls nothing that mutates. `--apply` is required.
It REFUSES when:
  * the template revision cannot be read, or names an image tag absent from ECR
  * a service of that name already exists (it will not modify a running service)
  * `--desired-count` is greater than 1 without `--i-understand-fan-out`, because the
    command and DAG workers consume shared queues and two replicas of an unelected
    consumer is a different system from one

    python scripts/deploy_worker_services.py --plan
    python scripts/deploy_worker_services.py --worker command --apply
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"

#: The revision to copy everything except the command from. Resolved live rather than
#: pinned, because pinning a revision number here would rot on the next deploy.
TEMPLATE_FAMILY = "vyomquant-api"

#: The container name in the template whose settings are the application's. The template
#: also carries a `questdb` sidecar, which a worker must NOT inherit -- it is a
#: co-located metrics store for the API, and three more copies of it is three more
#: things to run for no reader.
TEMPLATE_CONTAINER = "vyomquant-api"

#: worker key -> (family of the INACTIVE revision to take the command from, service name)
WORKERS = {
    "backtest": ("vyomquant-worker-backtest-production:1",
                 "vyomquant-worker-backtest-service"),
    "command": ("vyomquant-worker-command-production:1",
                "vyomquant-worker-command-service"),
    "dag": ("vyomquant-worker-dag-production:1",
            "vyomquant-worker-dag-service"),
}


def aws(*args: str) -> tuple:
    p = subprocess.run(["aws", *args, "--region", REGION],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def describe(task_def: str):
    code, out, err = aws("ecs", "describe-task-definition", "--task-definition",
                         task_def, "--output", "json")
    if code != 0:
        return None, (err.strip().splitlines() or ["unknown"])[-1][:200]
    return json.loads(out)["taskDefinition"], None


def ecr_tag_exists(image: str) -> tuple:
    """Is the template's image tag actually in ECR? The INACTIVE revisions failed here."""
    if "/" not in image or ":" not in image:
        return False, f"{image!r} is not a registry-qualified tag"
    repo = image.split("/")[-1].split(":")[0]
    tag = image.rsplit(":", 1)[-1]
    code, _, err = aws("ecr", "describe-images", "--repository-name", repo,
                       "--image-ids", f"imageTag={tag}", "--output", "json")
    if code != 0:
        return False, f"{repo}:{tag} not found in ECR ({(err or '').strip()[:90]})"
    return True, f"{repo}:{tag} present in ECR"


def service_exists(name: str) -> bool:
    code, out, _ = aws("ecs", "describe-services", "--cluster", CLUSTER,
                       "--services", name, "--output", "json")
    if code != 0:
        return False
    services = json.loads(out).get("services", [])
    return any(s.get("status") in ("ACTIVE", "DRAINING") for s in services)


def build_task_definition(template: dict, command: list, family: str) -> dict:
    """The template's container, with the worker's command and no sidecar."""
    container = next(
        (dict(c) for c in template["containerDefinitions"]
         if c["name"] == TEMPLATE_CONTAINER), None)
    if container is None:
        raise SystemExit(
            f"the template has no container named {TEMPLATE_CONTAINER!r}; its containers "
            f"are {[c['name'] for c in template['containerDefinitions']]}"
        )
    container["name"] = family
    container["command"] = command
    # A worker serves no traffic. Port mappings on a worker invite someone to point a
    # load balancer at it.
    container.pop("portMappings", None)
    container.pop("healthCheck", None)
    env = [e for e in container.get("environment", [])
           if e.get("name") not in ("PORT", "WORKERS")]
    container["environment"] = env

    return {
        "family": family,
        "networkMode": template.get("networkMode", "awsvpc"),
        "requiresCompatibilities": template.get("requiresCompatibilities", ["FARGATE"]),
        "cpu": "256",
        "memory": "512",
        "executionRoleArn": template["executionRoleArn"],
        "taskRoleArn": template.get("taskRoleArn", template["executionRoleArn"]),
        "containerDefinitions": [container],
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--worker", choices=sorted(WORKERS) + ["all"], default="all")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="register and create")
    p.add_argument("--desired-count", type=int, default=1)
    p.add_argument("--i-understand-fan-out", action="store_true",
                   help="required for --desired-count > 1; see the module docstring")
    args = p.parse_args()
    apply = bool(args.apply)

    if args.desired_count > 1 and not args.i_understand_fan_out:
        print("REFUSED: --desired-count > 1 without --i-understand-fan-out. The command "
              "and DAG workers consume shared queues and have no leader election, so two "
              "replicas is a different system from one -- the same condition WATCH-01 "
              "records for OrderWatchdog.", file=sys.stderr)
        return 2

    print("=" * 78)
    print("INF-03: worker service deployment (route B -- direct register + create)")
    print("=" * 78)

    template, err = describe(TEMPLATE_FAMILY)
    if template is None:
        print(f"REFUSED: cannot read the template {TEMPLATE_FAMILY}: {err}")
        return 2
    container = next((c for c in template["containerDefinitions"]
                      if c["name"] == TEMPLATE_CONTAINER), {})
    image = container.get("image", "")
    print(f"template       : {TEMPLATE_FAMILY}:{template['revision']}")
    print(f"image          : {image}")
    ok, detail = ecr_tag_exists(image)
    print(f"image in ECR   : {ok}  ({detail})")
    if not ok:
        print("\nREFUSED: the template's own image tag is not in ECR, so a task built "
              "from it could not pull. This is the exact defect that left the "
              "-production revisions INACTIVE.")
        return 2
    print(f"roles          : exec={template['executionRoleArn'].split('/')[-1]} "
          f"task={template.get('taskRoleArn','').split('/')[-1]}")
    print(f"secrets        : {len(container.get('secrets', []))} entries inherited")
    print(f"env            : {len(container.get('environment', []))} entries inherited")

    net = (aws("ecs", "describe-services", "--cluster", CLUSTER, "--services",
               "vyomquant-api-service-cjema2sl", "--output", "json"))
    subnets, groups = [], []
    if net[0] == 0:
        svc = json.loads(net[1])["services"][0]
        cfg = (svc.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {}
        subnets, groups = cfg.get("subnets", []), cfg.get("securityGroups", [])
    print(f"network        : {len(subnets)} subnet(s), {len(groups)} security group(s) "
          f"copied from the running api service")
    if not subnets or not groups:
        print("\nREFUSED: could not read the api service's network configuration, and a "
              "Fargate service cannot be created without one. Guessing a subnet would "
              "put a worker in the wrong place.")
        return 2

    targets = sorted(WORKERS) if args.worker == "all" else [args.worker]
    failures = 0
    for key in targets:
        source_td, service_name = WORKERS[key]
        print("\n" + "-" * 78)
        print(f"worker: {key}")
        print("-" * 78)
        src, err = describe(source_td)
        if src is None:
            print(f"   REFUSED: cannot read {source_td}: {err}")
            failures += 1
            continue
        src_container = (src.get("containerDefinitions") or [{}])[0]
        command = src_container.get("command")
        if not command:
            print(f"   REFUSED: {source_td} declares no command, which is the one thing "
                  f"this script needs from it.")
            failures += 1
            continue
        print(f"   command taken from {source_td}: {command}")
        print(f"   its image was {src_container.get('image')!r} "
              f"(ignored -- that is why it never ran)")

        family = f"vyomquant-worker-{key}"
        definition = build_task_definition(template, command, family)
        print(f"   would register : {family} cpu={definition['cpu']} "
              f"memory={definition['memory']}")
        print(f"   would create   : {service_name} desiredCount={args.desired_count}")

        if service_exists(service_name):
            print(f"   REFUSED: service {service_name} already exists. This script does "
                  f"not modify a running service.")
            failures += 1
            continue

        if not apply:
            continue

        code, out, err = aws(
            "ecs", "register-task-definition", "--output", "json",
            "--cli-input-json", json.dumps(definition))
        if code != 0:
            print(f"   register FAILED: {(err or '').strip()[:220]}")
            failures += 1
            continue
        registered = json.loads(out)["taskDefinition"]
        arn = registered["taskDefinitionArn"]
        print(f"   registered {arn.split('/')[-1]}")

        code, out, err = aws(
            "ecs", "create-service", "--cluster", CLUSTER,
            "--service-name", service_name,
            "--task-definition", arn,
            "--desired-count", str(args.desired_count),
            "--launch-type", "FARGATE",
            "--network-configuration",
            json.dumps({"awsvpcConfiguration": {
                "subnets": subnets, "securityGroups": groups,
                "assignPublicIp": "ENABLED"}}),
            "--output", "json",
        )
        if code != 0:
            print(f"   create-service FAILED: {(err or '').strip()[:220]}")
            failures += 1
            continue
        print(f"   created service {service_name}")

    print("\n" + "=" * 78)
    if not apply:
        print("--plan only. Nothing was registered and no service was created.")
        print("Re-run with --apply, and read the two routes in the module docstring "
              "first: this is route B, the shortcut.")
    print(f"{failures} refusal(s)/failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
