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
import urllib.request
import urllib.error
from typing import Dict, List, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def run_cmd(cmd: List[str], timeout: int = 300) -> Tuple[int, str, str]:
    """Execute shell command with timeout and return stdout, stderr."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout
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
    """Build, run, and probe Docker container."""
    runtime_res = {
        "build_status": "SKIPPED",
        "container_status": "SKIPPED",
        "health_endpoint": False,
        "health_live_endpoint": False,
        "health_ready_endpoint": False,
        "logs": "",
        "error": ""
    }

    print(f"[docker_val] Building Docker image {image_tag}...")
    code, stdout, stderr = run_cmd(["docker", "build", "-t", image_tag, "-f", "Dockerfile", "."])
    if code != 0:
        runtime_res["build_status"] = "FAIL"
        runtime_res["error"] = f"Docker build failed:\n{stderr}"
        return runtime_res
    runtime_res["build_status"] = "PASS"

    container_name = "test-trading-backend-val"
    run_cmd(["docker", "rm", "-f", container_name])

    print(f"[docker_val] Starting container {container_name} on port 8000...")
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
    ])

    if code != 0:
        runtime_res["container_status"] = "FAIL"
        runtime_res["error"] = f"Container start failed:\n{stderr}"
        return runtime_res

    runtime_res["container_status"] = "PASS"

    print("[docker_val] Waiting up to 30s for container health endpoints...")
    start_time = time.time()
    health_ok, live_ok, ready_ok = False, False, False

    while time.time() - start_time < 30:
        if not live_ok:
            live_ok, _, _ = probe_endpoint("http://localhost:8000/health/live")
        if not ready_ok:
            ready_ok, _, _ = probe_endpoint("http://localhost:8000/health/ready")
        if not health_ok:
            health_ok, _, _ = probe_endpoint("http://localhost:8000/health")

        if live_ok and ready_ok and health_ok:
            break
        time.sleep(2)

    runtime_res["health_live_endpoint"] = live_ok
    runtime_res["health_ready_endpoint"] = ready_ok
    runtime_res["health_endpoint"] = health_ok

    _, logs_stdout, logs_stderr = run_cmd(["docker", "logs", "--tail", "100", container_name])
    runtime_res["logs"] = logs_stdout + "\n" + logs_stderr

    run_cmd(["docker", "rm", "-f", container_name])

    return runtime_res


def main():
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

    os.makedirs("reports", exist_ok=True)
    with open("reports/docker_validation_results.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
