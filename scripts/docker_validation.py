#!/usr/bin/env python3
"""
scripts/docker_validation.py

Automated Docker Container Build & Runtime Verification Script
Performs:
 1. Dockerfile & Dockerfile.backend syntax & structure validation
 2. Docker daemon connectivity check
 3. Image build test (`docker build`)
 4. Container launch & startup polling
 5. GET /health, GET /health/live, GET /health/ready probe validation
 6. Container stop & cleanup
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, List, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def log_ts(msg: str):
    """Helper to log message with ISO/H:M:S timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_cmd(cmd: List[str], timeout: int = 300, env: dict = None) -> Tuple[int, str, str]:
    """Execute shell command with timeout and return returncode, stdout, stderr."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=full_env
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


def validate_dockerfiles() -> Dict:
    """Perform static inspection of Dockerfiles."""
    t0 = time.time()
    log_ts("[docker_val] ENTER: Dockerfile Validation")
    issues = []
    dockerfiles = ["Dockerfile", "Dockerfile.backend", "Dockerfile.tee", "Dockerfile.websocket"]
    found = []

    for df in dockerfiles:
        if not os.path.exists(df):
            issues.append(f"Missing Dockerfile: {df}")
            continue
        found.append(df)
        with open(df, "r", encoding="utf-8") as f:
            content = f.read()

        if "FROM" not in content:
            issues.append(f"{df}: Missing FROM directive")
        if "HEALTHCHECK" not in content:
            issues.append(f"{df}: Missing HEALTHCHECK directive")
        if "USER" not in content or "USER root" in content:
            issues.append(f"{df}: Container running as root or missing USER instruction")

    elapsed = round(time.time() - t0, 3)
    log_ts(f"[docker_val] EXIT: Dockerfile Validation completed in {elapsed}s (valid={len(issues)==0})")

    return {
        "found": found,
        "issues": issues,
        "valid": len(issues) == 0,
        "elapsed_seconds": elapsed
    }


def is_docker_available() -> bool:
    """Check if docker CLI and daemon are responsive."""
    t0 = time.time()
    log_ts("[docker_val] ENTER: Docker Daemon Connectivity Check")
    code, stdout, stderr = run_cmd(["docker", "info"], timeout=10)
    elapsed = round(time.time() - t0, 3)
    log_ts(f"[docker_val] EXIT: Docker Daemon Check completed in {elapsed}s (available={code==0})")
    return code == 0


def probe_endpoint(url: str, timeout: int = 5) -> Tuple[bool, int, str]:
    """HTTP GET probe helper."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "DockerValidation/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            return resp.status == 200, resp.status, body
    except urllib.error.HTTPError as e:
        return False, e.code, str(e)
    except Exception as e:
        return False, 0, str(e)


def run_docker_runtime_validation(image_tag: str = "trading-platform:test") -> Dict:
    """Build, run, and probe Docker container with detailed timing and diagnostics."""
    runtime_res = {
        "build_status": "SKIPPED",
        "container_status": "SKIPPED",
        "health_endpoint": False,
        "health_live_endpoint": False,
        "health_ready_endpoint": False,
        "logs": "",
        "error": "",
        "exit_code": None,
        "inspect_health": "",
        "timings": {}
    }

    container_name = "test-trading-backend-val"
    t_start_total = time.time()

    try:
        # Stage 1: Docker Build
        t0 = time.time()
        log_ts(f"[docker_val] ENTER: Stage 1 - Docker Build for image {image_tag}")
        build_env = {"DOCKER_BUILDKIT": "1"}
        code, stdout, stderr = run_cmd(
            ["docker", "build", "-t", image_tag, "-f", "Dockerfile", "."],
            timeout=180,
            env=build_env
        )
        t_build = round(time.time() - t0, 2)
        runtime_res["timings"]["docker_build"] = t_build
        log_ts(f"[docker_val] EXIT: Stage 1 - Docker Build completed in {t_build}s (exit_code={code})")

        if code != 0:
            runtime_res["build_status"] = "FAIL"
            runtime_res["error"] = f"Docker build failed:\n{stderr}"
            return runtime_res
        runtime_res["build_status"] = "PASS"

        # Cleanup existing container instance if present
        run_cmd(["docker", "rm", "-f", container_name], timeout=15)

        # Stage 2: Docker Run
        t0 = time.time()
        log_ts(f"[docker_val] ENTER: Stage 2 - Docker Run container {container_name} on port 8000")
        code, stdout, stderr = run_cmd([
            "docker", "run", "-d",
            "--name", container_name,
            "-p", "8000:8000",
            "-e", "ENV=test",
            "-e", "AERORA_MODE=safe",
            "-e", "DEV_MODE=true",
            "-e", "SUPABASE_URL=https://dummy.supabase.co",
            "-e", "SUPABASE_SERVICE_ROLE_KEY=dummy-key",
            "-e", "SUPABASE_ANON_KEY=dummy-key",
            "-e", "SUPABASE_JWT_SECRET=dummy-supabase-jwt-secret-dev",
            "-e", "MASTER_ENCRYPTION_KEYS=dummy-encryption-key-32-bytes-ok!",
            "-e", "DATABASE_URL=sqlite:///./test.db",
            image_tag
        ], timeout=30)
        t_run = round(time.time() - t0, 2)
        runtime_res["timings"]["docker_run"] = t_run
        log_ts(f"[docker_val] EXIT: Stage 2 - Docker Run completed in {t_run}s (exit_code={code})")

        if code != 0:
            runtime_res["container_status"] = "FAIL"
            runtime_res["error"] = f"Container start failed:\n{stderr}"
            return runtime_res

        runtime_res["container_status"] = "PASS"

        # Stage 3: Health & Readiness Probe Polling Loops
        t0 = time.time()
        log_ts(f"[docker_val] ENTER: Stage 3 - Health & Readiness probe polling loop (max 20s)")
        health_ok, live_ok, ready_ok = False, False, False

        while time.time() - t0 < 20:
            if not live_ok:
                live_ok, status_live, _ = probe_endpoint("http://localhost:8000/health/live")
                log_ts(f"  [probe] GET /health/live -> ok={live_ok} (status={status_live})")
            if not ready_ok:
                ready_ok, status_ready, _ = probe_endpoint("http://localhost:8000/health/ready")
                log_ts(f"  [probe] GET /health/ready -> ok={ready_ok} (status={status_ready})")
            if not health_ok:
                health_ok, status_h, _ = probe_endpoint("http://localhost:8000/health")
                log_ts(f"  [probe] GET /health -> ok={health_ok} (status={status_h})")

            if live_ok and ready_ok and health_ok:
                log_ts(f"  [probe] All endpoints responding HTTP 200 OK after {round(time.time() - t0, 2)}s")
                break
            time.sleep(1)

        t_health = round(time.time() - t0, 2)
        runtime_res["timings"]["health_polling"] = t_health
        log_ts(f"[docker_val] EXIT: Stage 3 - Health polling completed in {t_health}s (live={live_ok}, ready={ready_ok}, health={health_ok})")

        runtime_res["health_live_endpoint"] = live_ok
        runtime_res["health_ready_endpoint"] = ready_ok
        runtime_res["health_endpoint"] = health_ok

        # Stage 4: Collect Docker Logs & Container Exit Code & Inspect Health
        t0 = time.time()
        log_ts(f"[docker_val] ENTER: Stage 4 - Collecting Container Diagnostics & Logs (last 200 lines)")
        
        _, exit_code_out, _ = run_cmd(["docker", "inspect", "--format", "{{.State.ExitCode}}", container_name], timeout=10)
        exit_code_str = exit_code_out.strip()
        runtime_res["exit_code"] = exit_code_str
        log_ts(f"  [inspect] Container Exit Code: {exit_code_str}")

        _, inspect_health_out, _ = run_cmd(["docker", "inspect", "--format", "{{json .State.Health}}", container_name], timeout=10)
        inspect_health_str = inspect_health_out.strip()
        runtime_res["inspect_health"] = inspect_health_str
        log_ts(f"  [inspect] Docker Health Status: {inspect_health_str}")

        _, logs_stdout, logs_stderr = run_cmd(["docker", "logs", "--tail", "200", container_name], timeout=15)
        t_logs = round(time.time() - t0, 2)
        runtime_res["timings"]["docker_logs"] = t_logs
        container_logs = (logs_stdout + "\n" + logs_stderr).strip()
        runtime_res["logs"] = container_logs
        log_ts(f"[docker_val] EXIT: Stage 4 - Diagnostics & Logs collected in {t_logs}s")

        if not (live_ok and ready_ok and health_ok):
            log_ts("❌ Health probes failed! Container diagnostics summary:")
            print(f"Container Exit Code: {exit_code_str}")
            print(f"Docker Inspect Health: {inspect_health_str}")
            print("==================== CONTAINER LOGS (LAST 200 LINES) ====================")
            print(container_logs)
            print("=========================================================================")

    finally:
        # Stage 5: Container Cleanup
        t0 = time.time()
        log_ts(f"[docker_val] ENTER: Stage 5 - Container Cleanup ({container_name})")
        run_cmd(["docker", "rm", "-f", container_name], timeout=15)
        t_clean = round(time.time() - t0, 2)
        runtime_res["timings"]["cleanup"] = t_clean
        log_ts(f"[docker_val] EXIT: Stage 5 - Container Cleanup completed in {t_clean}s")

    t_total = round(time.time() - t_start_total, 2)
    runtime_res["timings"]["total_runtime"] = t_total
    log_ts(f"[docker_val] TOTAL Runtime Validation Time: {t_total}s")

    return runtime_res


def main():
    t_main_start = time.time()
    log_ts("=== AUTOMATED DOCKER VALIDATION ===")
    
    # 1. Static Dockerfile Inspection
    static_res = validate_dockerfiles()
    
    # 2. Docker Availability Check
    docker_avail = is_docker_available()
    
    res = {
        "status": "PASS",
        "static_validation": static_res,
        "docker_available": docker_avail,
        "runtime": {}
    }

    print(f"Static Dockerfile check: {'PASS' if static_res['valid'] else 'FAIL'}")
    if static_res['issues']:
        for issue in static_res['issues']:
            print(f"  [!] {issue}")

    if docker_avail:
        log_ts("Docker Daemon: CONNECTED. Proceeding to container build & probe validation...")
        res["runtime"] = run_docker_runtime_validation()
        print(f"Docker Build: {res['runtime']['build_status']}")
        print(f"Container Start: {res['runtime']['container_status']}")
        print(f"GET /health/live: {'PASS' if res['runtime']['health_live_endpoint'] else 'FAIL'}")
        print(f"GET /health/ready: {'PASS' if res['runtime']['health_ready_endpoint'] else 'FAIL'}")
        print(f"GET /health: {'PASS' if res['runtime']['health_endpoint'] else 'FAIL'}")

        if not (
            res['runtime']['build_status'] == 'PASS'
            and res['runtime']['container_status'] == 'PASS'
            and res['runtime']['health_live_endpoint']
            and res['runtime']['health_ready_endpoint']
            and res['runtime']['health_endpoint']
        ):
            res["status"] = "FAIL"
    else:
        log_ts("Docker Daemon: NOT AVAILABLE locally. Static validation complete.")
        if not static_res['valid']:
            res["status"] = "FAIL"

    total_elapsed = round(time.time() - t_main_start, 2)
    res["total_execution_time_seconds"] = total_elapsed
    log_ts(f"=== TOTAL DOCKER VALIDATION ELAPSED TIME: {total_elapsed}s ===")

    os.makedirs("reports", exist_ok=True)
    with open("reports/docker_validation_results.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
