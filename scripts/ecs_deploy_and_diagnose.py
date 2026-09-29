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

FALSE-NEGATIVE ROLLOUT DETECTION FIX (2026-09-29)
-------------------------------------------------
Two consecutive pipeline runs reported "DEPLOYMENT FAILED + ROLLBACK FAILED" while
every ECS task in both rollouts actually reached healthStatus=HEALTHY with exitCode=0
and failedTasks=0. Forensics on service vyomquant-api-service-cjema2sl showed two
independent defects:

  1. WAIT WINDOW BELOW THE SERVICE'S PHYSICAL FLOOR.
     An uncontested rollout on this service takes ~315s wall clock:
       ~85-110s  Fargate ENI provisioning + pull of both container images
        60s      healthCheckGracePeriodSeconds
        30s      ALB healthy threshold (2 checks x 15s interval)
       remainder minimumHealthyPercent=100 forces new-task-healthy BEFORE the old
                 task drains, and the target group carries
                 deregistration_delay.timeout_seconds=300 plus stopTimeout=30.
     DEPLOY_WAIT_SECONDS was 300 (AWS CLI killed at 330s) and
     ROLLBACK_WAIT_SECONDS was 240 (killed at 270s) - i.e. the rollback wait could
     never succeed, and the deploy wait had ~15s of margin. Both are now 600s.

  2. NO DEPLOYMENT IDENTITY, SO CONCURRENT ROLLOUTS LOOKED LIKE FAILURES.
     `aws ecs wait services-stable` only observes the service as a whole. When two
     pipeline runs deployed 99 seconds apart, each update-service reset the other's
     rollout, so neither run could ever observe steady state and both fired a
     needless rollback that stomped the other. Replaced the blind waiter with
     wait_for_rollout(), which polls describe-services and tracks the specific
     deployment id this run created. It now distinguishes COMPLETED / FAILED
     (circuit breaker tripped) / SUPERSEDED (another deployment took over) /
     TIMEOUT, returns as soon as the outcome is known instead of burning the whole
     window, and SKIPS the rollback on SUPERSEDED so a concurrent - possibly newer -
     deployment is never clobbered.

  Supporting changes:
   * wait_for_idle_service() refuses to stomp a rollout that is already in flight.
   * register_task_definition() now bases the new revision on the revision the
     SERVICE IS ACTUALLY RUNNING, not on `describe-task-definition <family>` which
     returns the newest ACTIVE revision of the family and can therefore inherit an
     unvalidated revision registered moments earlier by a concurrent run.
   * CI: 02-build.yml and 03-deploy.yml now carry `concurrency` groups so two
     deployments can no longer overlap on the same ECS service.
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
#
# MEASURED FLOOR for vyomquant-api-service-cjema2sl is ~315s for a completely
# uncontested rollout (deployment created -> "has reached a steady state"):
#   ~85-110s Fargate ENI provisioning + pull of both container images
#     60s    healthCheckGracePeriodSeconds on the service
#     30s    ALB healthy threshold (HealthyThresholdCount=2 x 15s interval)
#   +        minimumHealthyPercent=100 serialises new-healthy-then-drain-old, and
#            the target group's deregistration_delay.timeout_seconds is 300s.
# Anything at or below ~350s produces false "unstable" verdicts on healthy rollouts.
DEPLOY_WAIT_SECONDS = int(os.getenv("DEPLOY_WAIT_SECONDS", "600"))
ROLLBACK_WAIT_SECONDS = int(os.getenv("ROLLBACK_WAIT_SECONDS", "600"))

# Interval between describe-services polls while watching a rollout.
ROLLOUT_POLL_INTERVAL_SECONDS = int(os.getenv("ROLLOUT_POLL_INTERVAL_SECONDS", "15"))

# How long to wait for a pre-existing, still-running rollout to settle before this
# run touches the service. Prevents one pipeline run from resetting another's
# in-flight deployment (the exact failure mode seen on 2026-09-29).
PRE_DEPLOY_IDLE_WAIT_SECONDS = int(os.getenv("PRE_DEPLOY_IDLE_WAIT_SECONDS", "600"))

# Rollout outcome states returned by wait_for_rollout().
ROLLOUT_COMPLETED = "COMPLETED"    # our deployment reached steady state
ROLLOUT_FAILED = "FAILED"          # ECS deployment circuit breaker tripped
ROLLOUT_SUPERSEDED = "SUPERSEDED"  # another deployment became PRIMARY
ROLLOUT_TIMEOUT = "TIMEOUT"        # still in progress when the window closed
ROLLOUT_IN_PROGRESS = "IN_PROGRESS"


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


def get_current_task_definition(task_def_ref: Optional[str] = None) -> Tuple[bool, Dict]:
    """Fetch a task definition from ECS.

    ``task_def_ref`` should normally be the revision ARN the SERVICE is currently
    running (from describe-services). Passing the bare family name falls back to
    "newest ACTIVE revision of the family", which is NOT the same thing: a
    concurrent pipeline run may have registered a newer, never-validated revision
    seconds earlier, and we would silently inherit it as our baseline.
    """
    ref = task_def_ref or TASK_FAMILY
    code, stdout, stderr = run_aws_cmd([
        "ecs", "describe-task-definition",
        "--task-definition", ref
    ])
    if code != 0:
        print(f"[ecs_deploy] describe-task-definition failed for '{ref}': {stderr.strip()}")
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

    container = None
    for c in task_def.get("containerDefinitions", []):
        if c.get("name") in ("vyomquant-api", "api"):
            container = c
            break
    if not container:
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


def register_task_definition(image_uri: str, base_task_def_arn: str = "") -> Tuple[bool, str]:
    """Register updated task definition with new container image.

    This function fetches the current production task definition and preserves ALL
    existing configuration, changing ONLY the container image. This prevents stale
    configuration from ecs-task-definition-full.json from overwriting production
    settings.

    ``base_task_def_arn`` is the revision the service is currently running. Basing
    the new revision on it - rather than on the newest ACTIVE revision of the family -
    guarantees we never inherit configuration from a revision that was registered by
    a concurrent run and never reached steady state.
    """
    # Step 1: Fetch the task definition we are going to build on top of.
    if base_task_def_arn:
        print(f"[ecs_deploy] Basing new revision on the running revision: {base_task_def_arn}")
    else:
        print(
            f"[ecs_deploy] No running revision supplied; falling back to the newest "
            f"ACTIVE revision of family '{TASK_FAMILY}'."
        )
    success, current_td = get_current_task_definition(base_task_def_arn or None)
    if not success:
        return False, "Failed to fetch current task definition from ECS"

    # Step 2: Validate critical configuration before proceeding
    valid, validation_msg = validate_critical_config(current_td)
    if not valid:
        return False, f"Deployment safety validation failed: {validation_msg}"

    print(f"[ecs_deploy] ✅ Critical configuration validation passed: {validation_msg}")

    # Step 3: Update ONLY the container image
    if current_td.get("containerDefinitions"):
        for container in current_td["containerDefinitions"]:
            if container.get("name") in ("vyomquant-api", "api"):
                container["image"] = image_uri
                break

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


def describe_service() -> Tuple[bool, Dict]:
    """Fetch the live ECS service description."""
    code, stdout, _stderr = run_aws_cmd([
        "ecs", "describe-services",
        "--cluster", ECS_CLUSTER,
        "--services", ECS_SERVICE,
    ])
    if code != 0:
        return False, {}
    try:
        services = json.loads(stdout).get("services", [])
        if not services:
            return False, {}
        return True, services[0]
    except Exception as exc:
        print(f"[ecs_deploy] Exception parsing describe-services: {exc}")
        return False, {}


def _primary_deployment(service: Dict) -> Dict:
    """Return the PRIMARY deployment of a service description, or {}."""
    for dep in service.get("deployments", []):
        if dep.get("status") == "PRIMARY":
            return dep
    return {}


def extract_deployment_id(update_service_stdout: str) -> str:
    """Pull the PRIMARY deployment id out of an `ecs update-service` response.

    This is the identity of the rollout THIS run created. Without it we can only
    observe the service as a whole and cannot tell "my deployment is still running"
    apart from "somebody else's deployment replaced mine".

    Returns "" when the id cannot be determined; callers then fall back to
    watching whatever deployment is PRIMARY.
    """
    try:
        service = json.loads(update_service_stdout).get("service", {})
    except Exception:
        return ""
    return _primary_deployment(service).get("id", "")


def classify_rollout(service: Dict, deployment_id: str = "") -> Tuple[str, str]:
    """Classify the current rollout state of the service.

    Returns (state, human_readable_detail) where state is one of the ROLLOUT_*
    constants (never ROLLOUT_TIMEOUT - that is decided by the caller's clock).

    Steady state matches the semantics of `aws ecs wait services-stable`
    (exactly one deployment remaining and runningCount == desiredCount) but is
    additionally scoped to the deployment id we are responsible for.
    """
    deployments = service.get("deployments", [])
    primary = _primary_deployment(service)
    desired = service.get("desiredCount", 0)
    running = service.get("runningCount", 0)
    pending = service.get("pendingCount", 0)

    # Another run's update-service replaced our rollout.
    if deployment_id and primary and primary.get("id") != deployment_id:
        return (
            ROLLOUT_SUPERSEDED,
            f"deployment {deployment_id} was replaced as PRIMARY by "
            f"{primary.get('id')} (task def "
            f"{primary.get('taskDefinition', '?').rsplit('/', 1)[-1]})",
        )

    watched = primary
    if deployment_id:
        for dep in deployments:
            if dep.get("id") == deployment_id:
                watched = dep
                break

    rollout_state = watched.get("rolloutState", "")
    failed_tasks = watched.get("failedTasks", 0)

    # ECS deployment circuit breaker tripped - no point waiting out the window.
    if rollout_state == "FAILED":
        return (
            ROLLOUT_FAILED,
            f"circuit breaker tripped: {watched.get('rolloutStateReason', 'no reason given')} "
            f"(failedTasks={failed_tasks})",
        )

    at_capacity = desired == running and pending == 0
    only_deployment = len(deployments) == 1
    if rollout_state == "COMPLETED" and at_capacity and only_deployment:
        return ROLLOUT_COMPLETED, watched.get("rolloutStateReason", "deployment completed")

    blockers = []
    if rollout_state != "COMPLETED":
        blockers.append(f"rolloutState={rollout_state or 'UNKNOWN'}")
    if not at_capacity:
        blockers.append(f"running={running}/{desired} pending={pending}")
    if not only_deployment:
        draining = [
            d.get("taskDefinition", "?").rsplit("/", 1)[-1]
            for d in deployments
            if d.get("status") != "PRIMARY"
        ]
        blockers.append(f"{len(deployments) - 1} older deployment(s) still draining: {draining}")
    if failed_tasks:
        blockers.append(f"failedTasks={failed_tasks}")
    return ROLLOUT_IN_PROGRESS, "; ".join(blockers)


def wait_for_rollout(wait_seconds: int, deployment_id: str = "") -> str:
    """Poll describe-services until the watched rollout resolves or time runs out.

    Replaces `aws ecs wait services-stable`, which (a) is capped at 40 x 15s = 600s
    internally, (b) had to be killed by a subprocess timeout, and (c) cannot tell
    whose deployment it is observing. Returns a ROLLOUT_* constant.
    """
    label = deployment_id or "PRIMARY deployment"
    print(
        f"[ecs_deploy] Watching {label} on {ECS_SERVICE} for up to {wait_seconds}s "
        f"(poll every {ROLLOUT_POLL_INTERVAL_SECONDS}s)..."
    )
    started = time.monotonic()
    deadline = started + wait_seconds
    last_detail = ""
    consecutive_describe_failures = 0

    while True:
        ok, service = describe_service()
        if ok:
            consecutive_describe_failures = 0
            state, detail = classify_rollout(service, deployment_id)
            elapsed = int(time.monotonic() - started)
            if state != ROLLOUT_IN_PROGRESS:
                print(f"[ecs_deploy] [{elapsed}s] rollout {state}: {detail}")
                return state
            if detail != last_detail:
                print(f"[ecs_deploy] [{elapsed}s] in progress - {detail}")
                last_detail = detail
        else:
            consecutive_describe_failures += 1
            print(
                f"[ecs_deploy] describe-services failed "
                f"({consecutive_describe_failures} in a row); retrying."
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                f"[ecs_deploy] Window of {wait_seconds}s closed with the rollout still "
                f"in progress. Last observed: {last_detail or 'unknown'}"
            )
            return ROLLOUT_TIMEOUT
        time.sleep(min(ROLLOUT_POLL_INTERVAL_SECONDS, remaining))


def wait_for_service_stable(wait_seconds: int, deployment_id: str = "") -> bool:
    """Backwards-compatible boolean wrapper around wait_for_rollout()."""
    return wait_for_rollout(wait_seconds, deployment_id) == ROLLOUT_COMPLETED


def wait_for_idle_service(max_wait: int = PRE_DEPLOY_IDLE_WAIT_SECONDS) -> Tuple[bool, str]:
    """Ensure no rollout is already in flight before this run touches the service.

    Calling update-service while another deployment is mid-rollout resets that
    rollout. When two pipeline runs overlap, both end up unable to reach steady
    state and both fire a rollback that stomps the other. Rather than racing, we
    wait for the in-flight rollout to settle.

    Returns (safe_to_proceed, detail).
    """
    ok, service = describe_service()
    if not ok:
        # Never block a deployment purely because a read failed; the deploy path
        # itself will surface a hard error if the service is genuinely unreachable.
        return True, "could not read service state; proceeding"

    state, detail = classify_rollout(service)
    if state in (ROLLOUT_COMPLETED, ROLLOUT_FAILED):
        return True, f"service idle ({state})"

    primary_id = _primary_deployment(service).get("id", "")
    print(
        f"⏳ A rollout is already in flight on {ECS_SERVICE} ({detail}). "
        f"Waiting up to {max_wait}s for it to settle rather than resetting it."
    )
    outcome = wait_for_rollout(max_wait, primary_id)
    if outcome in (ROLLOUT_COMPLETED, ROLLOUT_FAILED, ROLLOUT_SUPERSEDED):
        return True, f"pre-existing rollout resolved as {outcome}"
    return False, (
        f"a pre-existing rollout ({primary_id or 'PRIMARY'}) was still in progress after "
        f"{max_wait}s. Refusing to reset another deployment mid-flight."
    )


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
    SUPERSEDED           A concurrent deployment took ownership of the service;
                         rollback deliberately skipped so we do not clobber it.

    Returns True only on SUCCESS. All other outcomes return False.
    """
    print("=" * 60)
    print("=== STARTING ECS FARGATE DEPLOYMENT ===")
    print(f"Target Image : {image_uri}")
    print(f"Cluster      : {ECS_CLUSTER}")
    print(f"Service      : {ECS_SERVICE}")
    print(f"Deploy window: {DEPLOY_WAIT_SECONDS}s   Rollback window: {ROLLBACK_WAIT_SECONDS}s")
    print("=" * 60)

    # Step 0: Never reset a rollout that another run already has in flight.
    idle_ok, idle_detail = wait_for_idle_service()
    if not idle_ok:
        print(f"❌ Aborting before any change was made: {idle_detail}")
        _write_github_step_summary([
            "## ❌ ECS Deployment — ABORTED (concurrent rollout in flight)",
            f"**Reason:** {idle_detail}",
            "No task definition was registered and the service was **not** modified.",
            "Re-run this workflow once the in-flight deployment has settled.",
        ])
        return False
    print(f"[ecs_deploy] Pre-flight check: {idle_detail}")

    # Step 1: Capture currently-running task definition ARN before deploying.
    previous_task_def_arn = get_current_task_definition_arn()
    if previous_task_def_arn:
        print(f"📌 Previous known-good task definition ARN: {previous_task_def_arn}")
    else:
        print("📌 No existing task definition found (initial service deployment).")

    # Step 2: Register the new task definition, based on the RUNNING revision.
    reg_ok, arn_or_err = register_task_definition(image_uri, previous_task_def_arn)
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
    deployment_id = extract_deployment_id(upd_res)
    if deployment_id:
        print(f"✅ ECS service update initiated — deployment id {deployment_id}")
    else:
        print(
            "✅ ECS service update initiated with new task definition "
            "(deployment id unavailable; watching whichever deployment is PRIMARY)."
        )

    # Step 4: Monitor OUR deployment for stabilisation.
    outcome = wait_for_rollout(DEPLOY_WAIT_SECONDS, deployment_id)
    if outcome == ROLLOUT_COMPLETED:
        print("✅ ECS DEPLOYMENT COMPLETED & STABLE!")
        _write_github_step_summary([
            "## ✅ ECS Deployment — SUCCESS",
            f"**New task definition:** `{new_task_def_arn}`",
            f"**Image:** `{image_uri}`",
            f"**Deployment id:** `{deployment_id or 'n/a'}`",
        ])
        return True

    # Step 5: Deployment did not complete — collect diagnostics, then decide.
    if outcome == ROLLOUT_FAILED:
        print("⚠️  ECS DEPLOYMENT FAILED (circuit breaker) — starting diagnostics and rollback.")
    elif outcome == ROLLOUT_SUPERSEDED:
        print("⚠️  ECS DEPLOYMENT SUPERSEDED by a concurrent deployment — collecting diagnostics.")
    else:
        print("⚠️  ECS DEPLOYMENT TIMED OUT OR UNSTABLE — starting diagnostics and rollback.")

    diagnostics = collect_diagnostics()
    diagnostics["previous_task_def"] = previous_task_def_arn
    diagnostics["new_task_def"] = new_task_def_arn
    diagnostics["deployment_id"] = deployment_id
    diagnostics["rollout_outcome"] = outcome
    diagnostics["deploy_wait_seconds"] = DEPLOY_WAIT_SECONDS

    if outcome == ROLLOUT_SUPERSEDED:
        # Another run owns the service now, and it may be deploying NEWER code.
        # Rolling back here would clobber it and restart the whole fight. This is
        # exactly what turned two healthy rollouts into two "ROLLBACK FAILED"
        # reports on 2026-09-29.
        print(
            "⏭️  Rollback deliberately SKIPPED: another deployment now owns this service. "
            "Rolling back would clobber a concurrent (possibly newer) deployment.\n"
            "    Nothing was reverted. Re-run this workflow if this commit must win."
        )
        diagnostics["rollback_status"] = "SKIPPED_SUPERSEDED"
        _write_github_step_summary([
            "## ⚠️ ECS Deployment — SUPERSEDED (no rollback performed)",
            f"**Our task definition:** `{new_task_def_arn}`",
            f"**Our deployment id:** `{deployment_id or 'n/a'}`",
            "Another deployment took over this ECS service while our rollout was in "
            "flight, so our rollout never reached steady state.",
            "**No rollback was performed** — reverting would clobber the concurrent "
            "deployment. The service is being managed by the other run.",
            "**Action:** confirm the winning deployment carries the intended commit, "
            "then re-run this workflow if this commit must be deployed.",
        ])

    elif not previous_task_def_arn:
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
            rb_deployment_id = extract_deployment_id(rb_res)
            print(
                f"✅ Rollback update accepted (deployment id "
                f"{rb_deployment_id or 'n/a'}) — waiting for service to stabilise "
                f"on the previous version..."
            )
            rb_outcome = wait_for_rollout(ROLLBACK_WAIT_SECONDS, rb_deployment_id)
            diagnostics["rollback_outcome"] = rb_outcome
            diagnostics["rollback_deployment_id"] = rb_deployment_id
            diagnostics["rollback_wait_seconds"] = ROLLBACK_WAIT_SECONDS
            if rb_outcome == ROLLOUT_COMPLETED:
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
            elif rb_outcome == ROLLOUT_SUPERSEDED:
                print(
                    "⏭️  Rollback was superseded by another deployment — the service is "
                    "now owned by a concurrent run. Not intervening further."
                )
                diagnostics["rollback_status"] = "SKIPPED_SUPERSEDED"
                _write_github_step_summary([
                    "## ⚠️ ECS Deployment — FAILED, ROLLBACK SUPERSEDED",
                    f"**Failed new task definition:** `{new_task_def_arn}`",
                    f"**Rollback target:** `{previous_task_def_arn}`",
                    "A concurrent deployment took over the service during our rollback. "
                    "No further action was taken to avoid clobbering it.",
                    "**Action:** verify which task definition the service settled on.",
                ])
            else:
                print(
                    f"❌ CRITICAL: AUTOMATIC ROLLBACK DID NOT COMPLETE "
                    f"(outcome={rb_outcome})!"
                )
                diagnostics["rollback_status"] = "FAILED"
                _write_github_step_summary([
                    "## 🚨 ECS Deployment — FAILED + ROLLBACK UNSTABLE",
                    f"**Failed new task definition:** `{new_task_def_arn}`",
                    f"**Rollback target:** `{previous_task_def_arn}`",
                    f"**Rollback outcome:** `{rb_outcome}` after {ROLLBACK_WAIT_SECONDS}s.",
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
        f"\n📋 Deployment outcome: rollout={diagnostics.get('rollout_outcome', 'N/A')} "
        f"rollback_status={diagnostics.get('rollback_status', 'N/A')}"
    )
    print("❌ DEPLOYMENT FAILED — see reports/ for full diagnostics.")
    return False


def main() -> None:
    image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"
    success = execute_deployment(image_uri)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
