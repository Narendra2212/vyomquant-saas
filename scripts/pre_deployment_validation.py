#!/usr/bin/env python3
"""
scripts/pre_deployment_validation.py

Unified Pre-Deployment Validation Engine
Runs all automated validation checks before triggering deployment:
 1. Python syntax & AST import audit
 2. Dependency reconciliation audit
 3. Backend FastAPI instantiation & startup simulation
 4. Docker container build & endpoint probe check
 5. AWS infrastructure & task definition validation

If ANY check fails, halts deployment immediately.
"""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def run_python_script(script_path: str, args: list = None) -> tuple:
    """Helper to execute external script and capture output."""
    cmd = [sys.executable, script_path] + (args or [])
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return 1, "", str(e)


def test_fastapi_startup() -> tuple:
    """Test importing backend_app.main and instantiating app."""
    try:
        sys.path.insert(0, os.getcwd())
        os.environ.setdefault("ENV", "test")
        os.environ.setdefault("AERORA_MODE", "safe")
        os.environ.setdefault("DEV_MODE", "true")
        os.environ.setdefault("SUPABASE_URL", "https://dummy.supabase.co")
        os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "dummy-service-role-key")
        os.environ.setdefault("SUPABASE_ANON_KEY", "dummy-anon-key")
        os.environ.setdefault("SUPABASE_JWT_SECRET", "dummy-supabase-jwt-secret-dev")
        os.environ.setdefault("MASTER_ENCRYPTION_KEYS", "dummy-key-1,dummy-key-2")
        os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
        
        main_module = importlib.import_module("backend_app.main")
        app = getattr(main_module, "app", None)
        if app is None:
            return False, "FastAPI app instance not found in backend_app.main"
        
        return True, f"FastAPI app instance verified: title={app.title}, version={app.version}"
    except Exception as e:
        return False, f"FastAPI startup simulation failed: {e}"


def main():
    print("==========================================================")
    print("      VYOMQUANT AUTOMATED PRE-DEPLOYMENT VALIDATION       ")
    print("==========================================================")

    os.makedirs("reports", exist_ok=True)
    validation_results = {
        "status": "PASS",
        "steps": {}
    }

    # Step 1: Import AST Audit
    print("\n[Step 1/5] Running Automated Import & AST Audit...")
    code, stdout, stderr = run_python_script("scripts/import_audit.py", ["backend_app"])
    print(stdout)
    validation_results["steps"]["import_audit"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "output": stdout,
        "error": stderr
    }

    # Step 2: Dependency Audit
    print("\n[Step 2/5] Running Dependency Reconciliation Audit...")
    code, stdout, stderr = run_python_script("scripts/dependency_audit.py")
    print(stdout)
    validation_results["steps"]["dependency_audit"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "output": stdout,
        "error": stderr
    }

    # Step 3: FastAPI Startup Check
    print("\n[Step 3/5] Simulating FastAPI Startup & Router Instantiation...")
    startup_ok, startup_msg = test_fastapi_startup()
    print(f"Startup Status: {'PASS' if startup_ok else 'FAIL'}")
    print(f"Details: {startup_msg}")
    validation_results["steps"]["fastapi_startup"] = {
        "status": "PASS" if startup_ok else "FAIL",
        "message": startup_msg
    }

    # Step 4: Docker Validation
    print("\n[Step 4/5] Running Docker Container Validation...")
    code, stdout, stderr = run_python_script("scripts/docker_validation.py")
    print(stdout)
    validation_results["steps"]["docker_validation"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "output": stdout,
        "error": stderr
    }

    # Step 5: AWS Validation
    print("\n[Step 5/5] Running AWS Task Definition & Infrastructure Validation...")
    code, stdout, stderr = run_python_script("scripts/aws_validation.py")
    print(stdout)
    validation_results["steps"]["aws_validation"] = {
        "status": "PASS" if code == 0 else "FAIL",
        "output": stdout,
        "error": stderr
    }

    # Final Evaluation
    all_passed = all(step["status"] == "PASS" for step in validation_results["steps"].values())
    validation_results["status"] = "PASS" if all_passed else "FAIL"

    print("\n==========================================================")
    print(f" PRE-DEPLOYMENT VALIDATION FINAL RESULT: {validation_results['status']}")
    print("==========================================================")

    with open("reports/pre_deployment_validation_summary.json", "w", encoding="utf-8") as f:
        json.dump(validation_results, f, indent=2)

    if validation_results["status"] == "FAIL":
        print("\n[X] PRE-DEPLOYMENT VALIDATION FAILED! ABORTING DEPLOYMENT.")
        sys.exit(1)

    print("\n[OK] ALL PRE-DEPLOYMENT CHECKS PASSED! PROCEEDING TO DEPLOYMENT.")
    sys.exit(0)


if __name__ == "__main__":
    main()
