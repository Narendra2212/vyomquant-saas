#!/usr/bin/env python3
"""
scripts/docker_validation.py

Automated Fast Static Docker Validation Engine for CI/CD Pipelines
Performs fast (<1s) comprehensive pre-deployment verification:
 1. Docker daemon & CLI connectivity check
 2. Dockerfile syntax & structural instruction audit:
    - FROM directive (base image specification)
    - HEALTHCHECK directive (container health probe definition)
    - USER directive (non-root security requirement)
    - COPY / ADD source path & file existence check
    - CMD / ENTRYPOINT syntax & executable script existence check
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def log_ts(msg: str):
    """Helper to log message with timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_cmd(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
    """Execute shell command with timeout and return returncode, stdout, stderr."""
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


def is_docker_available() -> Tuple[bool, str]:
    """Check if docker CLI and daemon are responsive."""
    t0 = time.time()
    log_ts("[docker_val] Checking Docker CLI & Daemon connectivity...")
    code, stdout, stderr = run_cmd(["docker", "info"], timeout=10)
    elapsed = round(time.time() - t0, 3)

    if code == 0:
        log_ts(f"[docker_val] Docker Daemon CONNECTED ({elapsed}s)")
        return True, "Docker daemon connected"
    else:
        log_ts(f"[docker_val] Docker Daemon NOT AVAILABLE ({elapsed}s)")
        return False, f"Docker daemon unavailable: {stderr.strip()}"


def validate_single_dockerfile(filepath: str) -> Tuple[bool, List[str]]:
    """
    Thorough static analysis of a Dockerfile:
    - FROM directive present
    - HEALTHCHECK directive present
    - USER directive present (non-root enforcement)
    - COPY/ADD source files exist in repository context
    - CMD/ENTRYPOINT valid and referenced scripts exist
    """
    issues = []
    if not os.path.exists(filepath):
        return False, [f"Missing required Dockerfile: {filepath}"]

    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    content = "".join(lines)

    # 1. FROM Directive Check
    if not re.search(r"^\s*FROM\s+\S+", content, re.MULTILINE | re.IGNORECASE):
        issues.append(f"{filepath}: Missing FROM directive (base image specification)")

    # 2. HEALTHCHECK Directive Check
    if not re.search(r"^\s*HEALTHCHECK\s+", content, re.MULTILINE | re.IGNORECASE):
        issues.append(f"{filepath}: Missing HEALTHCHECK directive")

    # 3. USER Directive Check (Non-root requirement)
    user_matches = re.findall(r"^\s*USER\s+(\S+)", content, re.MULTILINE | re.IGNORECASE)
    if not user_matches:
        issues.append(f"{filepath}: Missing USER instruction (container running as root)")
    else:
        last_user = user_matches[-1].lower()
        if last_user in ("root", "0"):
            issues.append(f"{filepath}: Container configured to run as root ('USER {last_user}')")

    # 4. COPY / ADD Source File Existence Verification
    for line_num, line in enumerate(lines, 1):
        clean_line = line.strip()
        if clean_line.startswith("#") or not clean_line:
            continue

        # Match COPY [--chown=...] [--from=...] src... dest
        copy_match = re.match(r"^\s*(?:COPY|ADD)\s+(?:--[a-zA-Z0-9_-]+=\S+\s+)*(.*)$", clean_line, re.IGNORECASE)
        if copy_match:
            parts = copy_match.group(1).split()
            # If line includes flags like --from=builder, skip checking build stage artifacts
            if any(p.startswith("--from=") for p in clean_line.split()):
                continue
            if len(parts) >= 2:
                sources = parts[:-1]
                for src in sources:
                    # Strip leading slashes for relative path check in workspace
                    relative_src = src.lstrip("./").lstrip("/")
                    if not relative_src:
                        continue
                    # Ignore wildcards or build args
                    if "*" in relative_src or "$" in relative_src:
                        continue
                    if not os.path.exists(relative_src):
                        issues.append(f"{filepath}:{line_num}: COPY source path does not exist: '{src}'")

    # 5. CMD / ENTRYPOINT Script Existence Check
    cmd_match = re.search(r"^\s*(?:CMD|ENTRYPOINT)\s+\[?\s*[\"']?([^\"'\],]+)", content, re.MULTILINE | re.IGNORECASE)
    if cmd_match:
        script = cmd_match.group(1).strip("./").strip("/")
        if script.endswith(".sh") or script.endswith(".py"):
            if not os.path.exists(script):
                issues.append(f"{filepath}: CMD/ENTRYPOINT references non-existent script: '{script}'")

    return len(issues) == 0, issues


def audit_all_dockerfiles() -> Dict:
    """Audit all target Dockerfiles in repository."""
    t0 = time.time()
    log_ts("[docker_val] ENTER: Static Dockerfile Structure & Directive Audit")
    
    target_dockerfiles = ["Dockerfile", "Dockerfile.backend", "Dockerfile.tee", "Dockerfile.websocket"]
    all_issues = []
    audited = []

    for df in target_dockerfiles:
        if os.path.exists(df):
            audited.append(df)
            valid, issues = validate_single_dockerfile(df)
            if not valid:
                all_issues.extend(issues)
        else:
            all_issues.append(f"Missing required Dockerfile: {df}")

    elapsed = round(time.time() - t0, 3)
    log_ts(f"[docker_val] EXIT: Static Dockerfile Audit completed in {elapsed}s (valid={len(all_issues)==0})")

    return {
        "audited_dockerfiles": audited,
        "issues": all_issues,
        "valid": len(all_issues) == 0,
        "elapsed_seconds": elapsed
    }


def main():
    t_main_start = time.time()
    log_ts("=== AUTOMATED FAST DOCKER VALIDATION (CI/CD PRE-FLIGHT) ===")

    # 1. Audit Dockerfiles
    static_audit = audit_all_dockerfiles()

    # 2. Check Docker CLI & Daemon Connectivity
    docker_ok, docker_msg = is_docker_available()

    total_elapsed = round(time.time() - t_main_start, 3)

    res = {
        "status": "PASS" if static_audit["valid"] else "FAIL",
        "docker_available": docker_ok,
        "docker_message": docker_msg,
        "static_validation": static_audit,
        "total_execution_time_seconds": total_elapsed
    }

    print("\n--- DOCKER VALIDATION SUMMARY ---")
    print(f"Static Dockerfile Inspection: {'PASS' if static_audit['valid'] else 'FAIL'}")
    print(f"Docker Daemon Connectivity: {'PASS' if docker_ok else 'WARN (Daemon unavailable)'}")
    
    if static_audit["issues"]:
        print("\nDetected Dockerfile Issues:")
        for issue in static_audit["issues"]:
            print(f"  [!] {issue}")

    log_ts(f"=== TOTAL FAST DOCKER VALIDATION TIME: {total_elapsed}s ===")

    os.makedirs("reports", exist_ok=True)
    with open("reports/docker_validation_results.json", "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
