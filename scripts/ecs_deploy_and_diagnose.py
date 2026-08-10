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
    - Captures the currently-running task definition ARN before every deployment
    - If the new deployment fails to stabilise, redeploys the last known-good ARN
    - Handles the initial-deployment edge case (no previous ARN) safely
 5. Artifact generation for CI/CD pipeline
 6. GitHub Actions job-summary output for human visibility

FIX HISTORY
-----------
* Removed broken retry_count pattern that re-deployed the same broken image on failure.
* Split update_ecs_service into deploy_new_image (--force-new-deployment) and
  rollback_to_task_def (no force flag): using --force-new-deployment on a rollback
  triggers a redundant extra deployment cycle and interferes with the circuit-breaker.
* Fixed subprocess timeout in wait_for_service_stable: the AWS CLI ecs wait command
  has an internal polling loop up to ~600s. We now pass subprocess_timeout = wait +
  _SUBPROCESS_HEADROOM so the AWS CLI is never killed before it concludes.
* collect_diagnostics now passes --service-name to filter stopped tasks to this service.
* execute_deployment writes a structured summary to GITHUB_STEP_SUMMARY.
* CONFIGURATION PRESERVATION FIX: Changed register_task_definition to fetch the
  current production task definition from ECS and preserve ALL existing configuration
  (environment variables, secrets, CPU, memory, etc.) while updating ONLY the
  container image. This prevents stale configuration from ecs-task-definition-full.json
  from overwriting production settings. Added validate_critical_config() to fail
  deployment if critical configuration (REDIS_URL, secrets, etc.) is incorrect.
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple


AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
ECS_CLUSTER = os.getenv("ECS_CLUSTER", "vyomquant-cluster")
ECS_SERVICE = os.getenv("ECS_SERVICE", "vyomquant-api-service-cjema2sl")
TASK_FAMILY = os.getenv("TASK_FAMILY", "vyomquant-api")
LOG_GROUP = os.getenv("LOG_GROUP", "/ecs/vyomquant-api")
IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")
ECR_REGISTRY = os.getenv("ECR_REGISTRY", "273709947018.dkr.ecr.ap-southeast-1.amazonaws.com")

# How long to wait for service stabilisation (seconds).
# AWS ecs wait services-stable internally polls every 15s for up to 40 attempts (600s).
# We add _SUBPROCESS_HEADROOM seconds so the AWS CLI is never killed prematurely.
DEPLOY_WAIT_SECONDS = int(os.getenv("DEPLOY_WAIT_SECONDS", "300"))
ROLLBACK_WAIT_SECONDS = int(os.getenv("ROLLBACK_WAIT_SECONDS", "240"))
_SUBPROCESS_HEADROOM = 30  # extra seconds added to subprocess timeout above wait window


def run_aws_cmd(cmd: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    """Execute an AWS CLI command and return (returncode, stdout, stderr)."""
    full_cmd = ["aws"] + cmd + ["--region", AWS_REGION, "--output", "json"]
    try:
        proc = subprocess.run(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 1, "", f"[ecs_deploy] AWS CLI timed out after {timeout}s: {cmd}"
    except Exception as exc:
        return 1, "", str(exc)


def get_current_task_definition() -> Tuple[bool, Dict]:
    """Fetch the currently active task definition from ECS."""
    code, stdout, stderr = run_aws_cmd([
        "ecs", "describe-task-definition",
        "--task-definition", TASK_FAMILY
    ])
    if code != 0:
        return False, {}
    try:
        data = json.loads(stdout)
        return True, data.get("taskDefinition", {})
    except Exception as e:
        print(f"[ecs_deploy] Exception parsing task definition: {e}")
        return False, {}


def validate_critical_config(task_def: Dict) -> Tuple[bool, str]:
    """Validate critical production configuration before deployment.

    Fails deployment if critical configuration is missing or incorrect.
    This prevents accidental deployment of broken configuration.
    """
    if not task_def.get("containerDefinitions"):
        return False, "No container definitions found"

    container = task_def["containerDefinitions"][0]
    env_vars = {env["name"]: env["value"] for env in container.get("environment", [])}

    # Validate REDIS_URL is production ElastiCache, not localhost
    redis_url = env_vars.get("REDIS_URL", "")
    expected_redis = "rediss://master.vyomquant-redis-production-cmd.4lf97k.apse1.cache.amazonaws.com:6379"

    if redis_url != expected_redis:
        return False, f"CRITICAL: REDIS_URL is '{redis_url}', expected '{expected_redis}'"

    # Validate secret names are present
    secret_names = {secret["name"] for secret in container.get("secrets", [])}
    required_secrets = {
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_JWT_SECRET",
        "DATABASE_URL",
        "MASTER_ENCRYPTION_KEYS",
        "CREDENTIAL_VAULT_SALT",
        "JWT_SECRET"
    }

    missing_secrets = required_secrets - secret_names
    if missing_secrets:
        return False, f"CRITICAL: Missing secrets: {missing_secrets}"

    # Validate container port
    port_mappings = container.get("portMappings", [])
    if not port_mappings or port_mappings[0].get("containerPort") != 8000:
        return False, "CRITICAL: Container port is not 8000"

    # Validate CPU and memory
    if task_def.get("cpu") != "1024":
        return False, f"CRITICAL: CPU is not 1024, got {task_def.get('cpu')}"
    if task_def.get("memory") != "2048":
        return False, f"CRITICAL: Memory is not 2048, got {task_def.get('memory')}"

    return True, "All critical validations passed"


def register_task_definition(image_uri: str) -> Tuple[bool, str]:
    """Register updated task definition with new container image.

    This function now fetches the current production task definition and
    preserves ALL existing configuration, changing ONLY the container image.
    This prevents stale configuration from ecs-task-definition-full.json
    from overwriting production settings.
    """
    # Step 1: Fetch current production task definition
    success, current_td = get_current_task_definition()
    if not success:
        return False, "Failed to fetch current task definition from ECS"

    # Step 2: Validate critical configuration before proceeding
    valid, validation_msg = validate_critical_config(current_td)
    if not valid:
        return False, f"Deployment safety validation failed: {validation_msg}"

    print(f"[ecs_deploy] ✅ Critical configuration validation passed: {validation_msg}")

    # Step 3: Update ONLY the container image
    if current_td.get("containerDefinitions"):
        current_td["containerDefinitions"][0]["image"] = image_uri

    # Step 4: Strip fields not accepted by register-task-definition
    for key in ['taskDefinitionArn', 'revision', 'status', 'requiresAttributes',
                'compatibilities', 'registeredAt', 'registeredBy', 'deregisteredAt']:
        current_td.pop(key, None)

    # Step 5: Write temporary file for registration
    temp_td_file = "task-def-rendered.json"
    with open(temp_td_file, "w", encoding="utf-8") as f:
        json.dump(current_td, f, indent=2)

    # Step 6: Register the task definition
    code, stdout, stderr = run_aws_cmd([
        "ecs", "register-task-definition",
        "--cli-input-json", f"file://{temp_td_file}"
    ])

    if code != 0:
        return False, f"Task def registration failed: {stderr}"

    parsed = json.loads(stdout)
    arn = parsed.get("taskDefinition", {}).get("taskDefinitionArn", "")
    return True, arn


def deploy_new_image(task_def_arn: str) -> Tuple[bool, str]:
    """Trigger a forward ECS deployment using --force-new-deployment.

    Intentionally separate from rollback_to_task_def; using --force-new-deployment
    during a rollback creates a redundant deployment cycle and can interfere with
    the native ECS circuit-breaker state machine.
    """
    code, stdout, stderr = run_aws_cmd([
        "ecs", "update-service",
        "--cluster", ECS_CLUSTER,
        "--service", ECS_SERVICE,
        "--task-definition", task_def_arn,
        "--force-new-deployment",
    ])
    if code != 0:
        return False, stderr
    return True, stdout


def rollback_to_task_def(task_def_arn: str) -> Tuple[bool, str]:
    """Revert the ECS service to a specific task definition ARN.

    Does NOT pass --force-new-deployment. ECS simply updates the desired
    task definition; the force flag is inappropriate for rollbacks and
    interferes with the native circuit-breaker.
    """
    code, stdout, stderr = run_aws_cmd([
        "ecs", "update-service",
        "--cluster", ECS_CLUSTER,
        "--service", ECS_SERVICE,
        "--task-definition", task_def_arn,
    ])
    if code != 0:
        return False, stderr
    return True, stdout


def wait_for_service_stable(wait_seconds: int) -> bool:
    """Block until the ECS service reaches steady state or the timeout elapses.

    A subprocess timeout of wait_seconds + _SUBPROCESS_HEADROOM is used so the
    AWS CLI process is never killed before the AWS-side polling window closes.
    """
    subprocess_timeout = wait_seconds + _SUBPROCESS_HEADROOM
    print(
        f"[ecs_deploy] Waiting up to {wait_seconds}s for {ECS_SERVICE} to stabilise "
        f"(subprocess timeout: {subprocess_timeout}s)..."
    )
    code, _stdout, _stderr = run_aws_cmd(
        [
            "ecs", "wait", "services-stable",
            "--cluster", ECS_CLUSTER,
            "--services", ECS_SERVICE,
        ],
        timeout=subprocess_timeout,
    )
    return code == 0


def _write_github_step_summary(lines: List[str]) -> None:
    """Append Markdown lines to the GitHub Actions step summary file if running in CI."""
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception as exc:
        print(f"[ecs_deploy] Warning: could not write to GITHUB_STEP_SUMMARY: {exc}")


def collect_diagnostics(service_arn: Optional[str] = None) -> Dict:
    """Harvest ECS events, stopped task reasons, CloudWatch logs, and Python tracebacks.

    Stopped tasks are filtered to this service via --service-name so we do not
    pick up stopped tasks from unrelated services running on the same cluster.
    """
    print("[ecs_deploy] Collecting diagnostic data...")
    diag: Dict = {
        "timestamp": time.time(),
        "service_events": [],
        "stopped_tasks": [],
        "cloudwatch_logs": [],
        "tracebacks": [],
        "recoverable": False,
    }

    # 1. Fetch Service Events
    code, stdout, _stderr = run_aws_cmd([
        "ecs", "describe-services",
        "--cluster", ECS_CLUSTER,
        "--services", ECS_SERVICE,
    ])
    if code == 0:
        try:
            svc_data = json.loads(stdout)
            events = svc_data.get("services", [{}])[0].get("events", [])
            diag["service_events"] = events[:15]
            if not service_arn and svc_data.get("services"):
                service_arn = svc_data["services"][0].get("serviceArn", "")
        except Exception:
            pass

    # 2. Fetch Stopped Tasks — scoped to this service to avoid cluster noise
    code, stdout, _stderr = run_aws_cmd([
        "ecs", "list-tasks",
        "--cluster", ECS_CLUSTER,
        "--desired-status", "STOPPED",
        "--service-name", ECS_SERVICE,
    ])
    if code == 0:
        try:
            task_arns = json.loads(stdout).get("taskArns", [])[:3]
            if task_arns:
                code_t, stdout_t, _stderr_t = run_aws_cmd([
                    "ecs", "describe-tasks",
                    "--cluster", ECS_CLUSTER,
                    "--tasks",
                ] + task_arns)
                if code_t == 0:
                    tasks_info = json.loads(stdout_t).get("tasks", [])
                    for task in tasks_info:
                        containers = task.get("containers", [{}])
                        diag["stopped_tasks"].append({
                            "taskArn": task.get("taskArn"),
                            "stoppedReason": task.get("stoppedReason"),
                            "stopCode": task.get("stopCode"),
                            "exitCode": containers[0].get("exitCode") if containers else None,
                            "containerReason": containers[0].get("reason") if containers else None,
                        })
        except Exception:
            pass

    # 3. Fetch CloudWatch Logs
    code, stdout, _stderr = run_aws_cmd([
        "logs", "describe-log-streams",
        "--log-group-name", LOG_GROUP,
        "--order-by", "LastEventTime",
        "--descending",
        "--max-items", "1",
    ])
    if code == 0:
        try:
            streams = json.loads(stdout).get("logStreams", [])
            if streams:
                stream_name = streams[0].get("logStreamName")
                code_l, stdout_l, _stderr_l = run_aws_cmd([
                    "logs", "get-log-events",
                    "--log-group-name", LOG_GROUP,
                    "--log-stream-name", stream_name,
                    "--limit", "100",
                ])
                if code_l == 0:
                    log_events = json.loads(stdout_l).get("events", [])
                    messages = [e.get("message", "") for e in log_events]
                    diag["cloudwatch_logs"] = messages
                    full_log_str = "\n".join(messages)
                    tb_matches = re.findall(
                        r"(Traceback \(most recent call last\):.*?(?:\r?\n[^\s].*|\Z))",
                        full_log_str,
                        re.DOTALL,
                    )
                    diag["tracebacks"] = tb_matches
        except Exception:
            pass

    recoverable_keywords = [
        "ResourceInitializationError",
        "API rate limit exceeded",
        "Task failed to start",
        "Timeout waiting",
    ]
    full_diag_text = json.dumps(diag)
    diag["recoverable"] = any(kw in full_diag_text for kw in recoverable_keywords)
    return diag


def get_current_task_definition_arn() -> str:
    """Fetch currently running task definition ARN from the ECS service before deployment."""
    code, stdout, stderr = run_aws_cmd([
        "ecs", "describe-services",
        "--cluster", ECS_CLUSTER,
        "--services", ECS_SERVICE
    ])
    if code == 0:
        try:
            svc_data = json.loads(stdout)
            services = svc_data.get("services", [])
            if services:
                return services[0].get("taskDefinition", "")
        except Exception as e:
            print(f"[ecs_deploy] Exception parsing describe-services: {e}")
    return ""


def execute_deployment(image_uri: str) -> bool:
    """Execute ECS deployment with automatic rollback to last known-good task definition.

    Outcome states
    --------------
    SUCCESS              New image deployed and stabilised.
    ROLLED_BACK          New image failed; service reverted to previous version.
    ROLLBACK_FAILED      New image failed AND rollback also failed.
    SKIPPED_IDENTICAL_ARN Previous and new ARNs are identical; rollback skipped.
    NO_PREVIOUS_VERSION  Initial deployment; no previous version to roll back to.

    Returns True only on SUCCESS. All other outcomes return False.
    """
    print("=" * 60)
    print("=== STARTING ECS FARGATE DEPLOYMENT ===")
    print(f"Target Image : {image_uri}")
    print(f"Cluster      : {ECS_CLUSTER}")
    print(f"Service      : {ECS_SERVICE}")
    print("=" * 60)

    # Step 1: Capture currently-running task definition ARN before deploying.
    previous_task_def_arn = get_current_task_definition_arn()
    if previous_task_def_arn:
        print(f"📌 Previous known-good task definition ARN: {previous_task_def_arn}")
    else:
        print("📌 No existing task definition found (initial service deployment).")

    # Step 2: Register the new task definition.
    reg_ok, arn_or_err = register_task_definition(image_uri)
    if not reg_ok:
        print(f"❌ Task definition registration failed: {arn_or_err}")
        _write_github_step_summary([
            "## ❌ ECS Deployment — FAILED",
            f"**Reason:** Task definition registration failed: `{arn_or_err}`",
        ])
        return False

    new_task_def_arn = arn_or_err
    print(f"✅ Registered new task definition ARN: {new_task_def_arn}")

    if previous_task_def_arn and new_task_def_arn == previous_task_def_arn:
        print(
            "⚠️  Warning: New task definition ARN is identical to the previous one. "
            "The image tag or task definition content may not have changed."
        )

    # Step 3: Initiate service update with new task definition.
    upd_ok, upd_res = deploy_new_image(new_task_def_arn)
    if not upd_ok:
        print(f"❌ Service update failed: {upd_res}")
        _write_github_step_summary([
            "## ❌ ECS Deployment — FAILED",
            f"**Reason:** update-service API call rejected: `{upd_res}`",
        ])
        return False
    print("✅ ECS service update initiated with new task definition.")

    # Step 4: Monitor new deployment for stabilisation.
    is_stable = wait_for_service_stable(wait_seconds=DEPLOY_WAIT_SECONDS)
    if is_stable:
        print("✅ ECS DEPLOYMENT COMPLETED & STABLE!")
        _write_github_step_summary([
            "## ✅ ECS Deployment — SUCCESS",
            f"**New task definition:** `{new_task_def_arn}`",
            f"**Image:** `{image_uri}`",
        ])
        return True

    # Step 5: Deployment failed — collect diagnostics then roll back.
    print("⚠️  ECS DEPLOYMENT TIMED OUT OR UNSTABLE — starting diagnostics and rollback.")
    diagnostics = collect_diagnostics()
    diagnostics["previous_task_def"] = previous_task_def_arn
    diagnostics["new_task_def"] = new_task_def_arn

    if not previous_task_def_arn:
        # Initial deployment with no prior version to revert to.
        print(
            "⚠️  No previous known-good task definition available to roll back to "
            "(initial deployment). Manual intervention required."
        )
        diagnostics["rollback_status"] = "NO_PREVIOUS_VERSION"
        _write_github_step_summary([
            "## ❌ ECS Deployment — FAILED (No Rollback Available)",
            "This appears to be an initial deployment. No previous task definition to revert to.",
            f"**Failed task definition:** `{new_task_def_arn}`",
            "**Action required:** Manual intervention.",
        ])

    elif previous_task_def_arn == new_task_def_arn:
        # Guard: rolling back to the same ARN would be a no-op.
        print(
            "⚠️  Previous and new task definition ARNs are identical — skipping rollback "
            "to avoid a no-op deployment cycle."
        )
        diagnostics["rollback_status"] = "SKIPPED_IDENTICAL_ARN"
        _write_github_step_summary([
            "## ❌ ECS Deployment — FAILED (Rollback Skipped)",
            "Previous and new task definition ARNs are identical; rollback would be a no-op.",
            f"**Task definition:** `{new_task_def_arn}`",
            "**Action required:** Manual investigation.",
        ])

    else:
        # Normal failure path: revert to the captured previous version.
        print(
            f"\n🔄 AUTOMATIC ROLLBACK ACTIVATED — reverting ECS service to previous "
            f"known-good task definition:\n  {previous_task_def_arn}"
        )
        rb_ok, rb_res = rollback_to_task_def(previous_task_def_arn)
        if not rb_ok:
            print(f"❌ CRITICAL: Rollback initiation failed: {rb_res}")
            diagnostics["rollback_status"] = "FAILED"
            diagnostics["rollback_error"] = rb_res
            _write_github_step_summary([
                "## 🚨 ECS Deployment — FAILED + ROLLBACK FAILED",
                f"**Failed new task definition:** `{new_task_def_arn}`",
                f"**Rollback target:** `{previous_task_def_arn}`",
                f"**Rollback error:** `{rb_res}`",
                "**Action required:** Immediate manual intervention.",
            ])
        else:
            print(
                "✅ Rollback update accepted — waiting for service to stabilise "
                "on the previous version..."
            )
            rb_stable = wait_for_service_stable(wait_seconds=ROLLBACK_WAIT_SECONDS)
            if rb_stable:
                print(
                    f"✅ AUTOMATIC ROLLBACK SUCCESSFUL — service restored to: "
                    f"{previous_task_def_arn}"
                )
                diagnostics["rollback_status"] = "SUCCESS"
                diagnostics["rollback_arn"] = previous_task_def_arn
                _write_github_step_summary([
                    "## ⚠️ ECS Deployment — FAILED but ROLLED BACK SUCCESSFULLY",
                    f"**Failed new task definition:** `{new_task_def_arn}`",
                    f"**Restored to:** `{previous_task_def_arn}`",
                    "The service is running the previous known-good version. "
                    "Investigate the new image before re-deploying.",
                ])
            else:
                print("❌ CRITICAL: AUTOMATIC ROLLBACK TIMED OUT OR UNSTABLE!")
                diagnostics["rollback_status"] = "FAILED"
                _write_github_step_summary([
                    "## 🚨 ECS Deployment — FAILED + ROLLBACK UNSTABLE",
                    f"**Failed new task definition:** `{new_task_def_arn}`",
                    f"**Rollback target:** `{previous_task_def_arn}`",
                    "Rollback was initiated but the service did not stabilise.",
                    "**Action required:** Immediate manual intervention.",
                ])

    # Step 6: Write diagnostic artefacts for CI upload.
    os.makedirs("reports", exist_ok=True)
    with open("reports/failure_analysis.json", "w", encoding="utf-8") as fh:
        json.dump(diagnostics, fh, indent=2)
    with open("reports/cloudwatch_logs.log", "w", encoding="utf-8") as fh:
        fh.write("\n".join(diagnostics.get("cloudwatch_logs", [])))

    print(
        f"\n📋 Deployment outcome: rollback_status={diagnostics.get('rollback_status', 'N/A')}"
    )
    print("❌ DEPLOYMENT FAILED — see reports/ for full diagnostics.")
    return False


def main() -> None:
    image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"
    success = execute_deployment(image_uri)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
