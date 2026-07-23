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

    return {
        "found": found,
        "issues": issues,
        "valid": len(issues) == 0
    }


def is_docker_available() -> bool:
    """Check if docker CLI and daemon are responsive."""
    code, stdout, stderr = run_cmd(["docker", "info"], timeout=10)
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
    """Build, run, and probe Docker container with detailed timing and cleanup."""
    runtime_res = {
        "build_status": "SKIPPED",
        "container_status": "SKIPPED",
        "health_endpoint": False,
        "health_live_endpoint": False,
        "health_ready_endpoint": False,
        "logs": "",
        "error": "",
        "timings": {}
    }

    container_name = "test-trading-backend-val"
    t_start_total = time.time()

    try:
        # 1. Docker Build
        t0 = time.time()
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Starting Docker build for image {image_tag}...")
        build_env = {"DOCKER_BUILDKIT": "1"}
        code, stdout, stderr = run_cmd(
            ["docker", "build", "-t", image_tag, "-f", "Dockerfile", "."],
            timeout=180,
            env=build_env
        )
        t_build = time.time() - t0
        runtime_res["timings"]["docker_build"] = round(t_build, 2)
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Docker build finished in {t_build:.2f}s (exit_code={code})")

        if code != 0:
            runtime_res["build_status"] = "FAIL"
            runtime_res["error"] = f"Docker build failed:\n{stderr}"
            return runtime_res
        runtime_res["build_status"] = "PASS"

        # Cleanup existing container instance if present
        run_cmd(["docker", "rm", "-f", container_name], timeout=15)

        # 2. Docker Run
        t0 = time.time()
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Starting container {container_name} on port 8000...")
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
        t_run = time.time() - t0
        runtime_res["timings"]["docker_run"] = round(t_run, 2)
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Container start finished in {t_run:.2f}s (exit_code={code})")

        if code != 0:
            runtime_res["container_status"] = "FAIL"
            runtime_res["error"] = f"Container start failed:\n{stderr}"
            return runtime_res

        runtime_res["container_status"] = "PASS"

        # 3. Health Probe Polling Loop
        t0 = time.time()
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Waiting up to 20s for container health endpoints...")
        health_ok, live_ok, ready_ok = False, False, False

        while time.time() - t0 < 20:
            if not live_ok:
                live_ok, _, _ = probe_endpoint("http://localhost:8000/health/live")
            if not ready_ok:
                ready_ok, _, _ = probe_endpoint("http://localhost:8000/health/ready")
            if not health_ok:
                health_ok, _, _ = probe_endpoint("http://localhost:8000/health")

            if live_ok and ready_ok and health_ok:
                break
            time.sleep(1)

        t_health = time.time() - t0
        runtime_res["timings"]["health_polling"] = round(t_health, 2)
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Health polling finished in {t_health:.2f}s (live={live_ok}, ready={ready_ok}, health={health_ok})")

        runtime_res["health_live_endpoint"] = live_ok
        runtime_res["health_ready_endpoint"] = ready_ok
        runtime_res["health_endpoint"] = health_ok

        # 4. Capture Container Logs
        _, logs_stdout, logs_stderr = run_cmd(["docker", "logs", "--tail", "100", container_name], timeout=15)
        container_logs = (logs_stdout + "\n" + logs_stderr).strip()
        runtime_res["logs"] = container_logs

        if not (live_ok and ready_ok and health_ok):
            timestamp = time.strftime('%H:%M:%S')
            print(f"\n[{timestamp}] [docker_val] ❌ Health probes failed. Printing container logs:")
            print("==================== CONTAINER LOGS ====================")
            print(container_logs)
            print("========================================================")

    finally:
        # 5. Guaranteed Container Cleanup
        t0 = time.time()
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Starting container cleanup ({container_name})...")
        run_cmd(["docker", "rm", "-f", container_name], timeout=15)
        t_clean = time.time() - t0
        runtime_res["timings"]["cleanup"] = round(t_clean, 2)
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [docker_val] Container cleanup finished in {t_clean:.2f}s")

    t_total = time.time() - t_start_total
    runtime_res["timings"]["total_runtime"] = round(t_total, 2)
    timestamp = time.strftime('%H:%M:%S')
    print(f"[{timestamp}] [docker_val] Total runtime_res validation time: {t_total:.2f}s")

    return runtime_res


def main():
    t_main_start = time.time()
    print("=== AUTOMATED DOCKER VALIDATION ===")
    res = {
        "status": "PASS",
        "static_validation": validate_dockerfiles(),
        "docker_available": is_docker_available(),
        "runtime": {}
    }

    print(f"Static Dockerfile check: {'PASS' if res['static_validation']['valid'] else 'FAIL'}")
    if res['static_validation']['issues']:
        for issue in res['static_validation']['issues']:
            print(f"  [!] {issue}")

    if res["docker_available"]:
        print("Docker Daemon: CONNECTED. Running container build & probe validation...")
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
        print("Docker Daemon: NOT AVAILABLE locally. Static validation complete.")
        if not res['static_validation']['valid']:
            res["status"] = "FAIL"

    total_elapsed = round(time.time() - t_main_start, 2)
    res["total_execution_time_seconds"] = total_elapsed
    print(f"Total Docker Validation Elapsed Time: {total_elapsed}s")

    os.makedirs("reports", exist_ok=True)
    with open("reports/docker_validation_results.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
