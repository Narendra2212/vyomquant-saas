#!/usr/bin/env python3
"""
scripts/ecs_deploy_and_diagnose.py

Automated ECS Fargate Deployment, Failure Analysis & Self-Healing Engine
Features:
 1. Task definition registration & container image updating
 2. ECS service update & stability monitoring
 3. Automatic Failure Analysis:
    - Task events & stopped task reasons
    - Container exit codes
    - CloudWatch log extraction & Python traceback harvesting
 4. Automatic Self-Healing:
    - Identifies recoverable failures (transient timeouts)
    - Automatically retries deployment once
    - Prevents endless retry loops
 5. Artifact generation for CI/CD pipeline
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Tuple


AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
ECS_CLUSTER = os.getenv("ECS_CLUSTER", "vyomquant-cluster")
ECS_SERVICE = os.getenv("ECS_SERVICE", "vyomquant-api-service-cjema2sl")
TASK_FAMILY = os.getenv("TASK_FAMILY", "vyomquant-api")
LOG_GROUP = os.getenv("LOG_GROUP", "/ecs/vyomquant-api")
IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")
ECR_REGISTRY = os.getenv("ECR_REGISTRY", "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com")


def run_aws_cmd(cmd: List[str], timeout: int = 60) -> Tuple[int, str, str]:
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


def register_task_definition(image_uri: str) -> Tuple[bool, str]:
    """Register updated task definition with new container image."""
    task_def_file = "ecs-task-definition-full.json"
    if not os.path.exists(task_def_file):
        return False, f"File {task_def_file} not found"

    try:
        with open(task_def_file, "r", encoding="utf-8") as f:
            td = json.load(f)

        # Update container image
        if td.get("containerDefinitions"):
            td["containerDefinitions"][0]["image"] = image_uri

        # Strip fields not accepted by register-task-definition
        for key in ['taskDefinitionArn', 'revision', 'status', 'requiresAttributes',
                    'compatibilities', 'registeredAt', 'registeredBy', 'deregisteredAt']:
            td.pop(key, None)

        temp_td_file = "task-def-rendered.json"
        with open(temp_td_file, "w", encoding="utf-8") as f:
            json.dump(td, f, indent=2)

        code, stdout, stderr = run_aws_cmd([
            "ecs", "register-task-definition",
            "--cli-input-json", f"file://{temp_td_file}"
        ])

        if code != 0:
            return False, f"Task def registration failed: {stderr}"

        parsed = json.loads(stdout)
        arn = parsed.get("taskDefinition", {}).get("taskDefinitionArn", "")
        return True, arn
    except Exception as e:
        return False, f"Exception during task def registration: {e}"


def update_ecs_service(task_def_arn: str) -> Tuple[bool, str]:
    """Trigger ECS service update with forced new deployment."""
    code, stdout, stderr = run_aws_cmd([
        "ecs", "update-service",
        "--cluster", ECS_CLUSTER,
        "--service", ECS_SERVICE,
        "--task-definition", task_def_arn,
        "--force-new-deployment"
    ])
    if code != 0:
        return False, stderr
    return True, stdout


def wait_for_service_stable(timeout_seconds: int = 300) -> bool:
    """Poll ECS service stability."""
    print(f"[ecs_deploy] Waiting up to {timeout_seconds}s for service {ECS_SERVICE} stability...")
    code, stdout, stderr = run_aws_cmd([
        "ecs", "wait", "services-stable",
        "--cluster", ECS_CLUSTER,
        "--services", ECS_SERVICE
    ], timeout=timeout_seconds)
    return code == 0


def collect_diagnostics() -> Dict:
    """Harvest ECS events, stopped task reasons, CloudWatch logs, and Python tracebacks."""
    print("[ecs_deploy] Collecting diagnostic data...")
    diag = {
        "timestamp": time.time(),
        "service_events": [],
        "stopped_tasks": [],
        "cloudwatch_logs": [],
        "tracebacks": [],
        "recoverable": False
    }

    # 1. Fetch Service Events
    code, stdout, stderr = run_aws_cmd([
        "ecs", "describe-services",
        "--cluster", ECS_CLUSTER,
        "--services", ECS_SERVICE
    ])
    if code == 0:
        try:
            svc_data = json.loads(stdout)
            events = svc_data.get("services", [{}])[0].get("events", [])
            diag["service_events"] = events[:15]
        except Exception:
            pass

    # 2. Fetch Stopped Tasks
    code, stdout, stderr = run_aws_cmd([
        "ecs", "list-tasks",
        "--cluster", ECS_CLUSTER,
        "--desired-status", "STOPPED"
    ])
    if code == 0:
        try:
            task_arns = json.loads(stdout).get("taskArns", [])[:3]
            if task_arns:
                code_t, stdout_t, stderr_t = run_aws_cmd([
                    "ecs", "describe-tasks",
                    "--cluster", ECS_CLUSTER,
                    "--tasks"
                ] + task_arns)
                if code_t == 0:
                    tasks_info = json.loads(stdout_t).get("tasks", [])
                    for t in tasks_info:
                        containers = t.get("containers", [{}])
                        diag["stopped_tasks"].append({
                            "taskArn": t.get("taskArn"),
                            "stoppedReason": t.get("stoppedReason"),
                            "stopCode": t.get("stopCode"),
                            "exitCode": containers[0].get("exitCode") if containers else None,
                            "containerReason": containers[0].get("reason") if containers else None
                        })
        except Exception:
            pass

    # 3. Fetch CloudWatch Logs
    code, stdout, stderr = run_aws_cmd([
        "logs", "describe-log-streams",
        "--log-group-name", LOG_GROUP,
        "--order-by", "LastEventTime",
        "--descending",
        "--max-items", "1"
    ])
    if code == 0:
        try:
            streams = json.loads(stdout).get("logStreams", [])
            if streams:
                stream_name = streams[0].get("logStreamName")
                code_l, stdout_l, stderr_l = run_aws_cmd([
                    "logs", "get-log-events",
                    "--log-group-name", LOG_GROUP,
                    "--log-stream-name", stream_name,
                    "--limit", "100"
                ])
                if code_l == 0:
                    log_events = json.loads(stdout_l).get("events", [])
                    messages = [e.get("message", "") for e in log_events]
                    diag["cloudwatch_logs"] = messages

                    # Harvest tracebacks
                    full_log_str = "\n".join(messages)
                    tb_matches = re.findall(r"(Traceback \(most recent call last\):.*?(?:\r?\n[^\s].*|\Z))", full_log_str, re.DOTALL)
                    diag["tracebacks"] = tb_matches
        except Exception:
            pass

    # Evaluate recoverable status: transient timeouts or rate limits
    recoverable_keywords = ["ResourceInitializationError", "API rate limit exceeded", "Task failed to start", "Timeout waiting"]
    full_diag_text = json.dumps(diag)
    diag["recoverable"] = any(kw in full_diag_text for kw in recoverable_keywords)

    return diag


def execute_deployment(image_uri: str, retry_count: int = 0) -> bool:
    """Execute ECS deployment flow with 1-time self-healing retry."""
    print(f"=== DEPLOYING TO ECS (Attempt {retry_count + 1}) ===")
    print(f"Target Image: {image_uri}")

    reg_ok, arn_or_err = register_task_definition(image_uri)
    if not reg_ok:
        print(f"❌ Task definition registration failed: {arn_or_err}")
        return False
    print(f"✅ Registered Task Definition ARN: {arn_or_err}")

    upd_ok, upd_res = update_ecs_service(arn_or_err)
    if not upd_ok:
        print(f"❌ Service update failed: {upd_res}")
        return False
    print("✅ ECS Service update initiated.")

    is_stable = wait_for_service_stable(timeout_seconds=240)
    if is_stable:
        print("✅ ECS DEPLOYMENT COMPLETED & STABLE!")
        return True

    print("⚠️  ECS DEPLOYMENT TIMED OUT OR UNSTABLE!")
    diagnostics = collect_diagnostics()

    os.makedirs("reports", exist_ok=True)
    with open("reports/failure_analysis.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2)

    with open("reports/cloudwatch_logs.log", "w", encoding="utf-8") as f:
        f.write("\n".join(diagnostics.get("cloudwatch_logs", [])))

    if retry_count == 0 and diagnostics.get("recoverable", False):
        print("\n🔄 SELF-HEALING ACTIVATED: Failure is recoverable. Retrying deployment once...")
        time.sleep(15)
        return execute_deployment(image_uri, retry_count=1)

    print("❌ DEPLOYMENT FAILED AFTER DIAGNOSTIC EVALUATION.")
    return False


def main():
    image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"
    success = execute_deployment(image_uri)
    if not success:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
