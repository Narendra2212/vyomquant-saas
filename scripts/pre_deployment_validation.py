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
import pprint
import subprocess
import sys
import traceback
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
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
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
    print("\nENTER STEP 1")
    print("[Step 1/5] Running Automated Import & AST Audit...")
    try:
        ret_step1 = run_python_script("scripts/import_audit.py", ["backend_app"])
        code, stdout, stderr = ret_step1
        step1_result = "PASS" if code == 0 else "FAIL"
        print(f"STEP 1 RESULT = {step1_result}")
        print(f"STEP 1 RETURN VALUE =\n{repr(ret_step1)}")
    except Exception:
        traceback.print_exc()
        raise
    print(stdout)
    validation_results["steps"]["import_audit"] = {
        "status": step1_result,
        "output": stdout,
        "error": stderr
    }

    # Step 2: Dependency Audit
    print("\nENTER STEP 2")
    print("[Step 2/5] Running Dependency Reconciliation Audit...")
    try:
        ret_step2 = run_python_script("scripts/dependency_audit.py")
        code, stdout, stderr = ret_step2
        step2_result = "PASS" if code == 0 else "FAIL"
        print(f"STEP 2 RESULT = {step2_result}")
        print(f"STEP 2 RETURN VALUE =\n{repr(ret_step2)}")
    except Exception:
        traceback.print_exc()
        raise
    print(stdout)
    validation_results["steps"]["dependency_audit"] = {
        "status": step2_result,
        "output": stdout,
        "error": stderr
    }

    # Step 3: FastAPI Startup Check
    print("\nENTER STEP 3")
    print("[Step 3/5] Simulating FastAPI Startup & Router Instantiation...")
    try:
        ret_step3 = test_fastapi_startup()
        startup_ok, startup_msg = ret_step3
        step3_result = "PASS" if startup_ok else "FAIL"
        print(f"STEP 3 RESULT = {step3_result}")
        print(f"STEP 3 RETURN VALUE =\n{repr(ret_step3)}")
    except Exception:
        traceback.print_exc()
        raise
    print(f"Startup Status: {step3_result}")
    print(f"Details: {startup_msg}")
    validation_results["steps"]["fastapi_startup"] = {
        "status": step3_result,
        "message": startup_msg
    }

    # Step 4: Docker Validation
    print("\nENTER STEP 4")
    print("[Step 4/5] Running Docker Container Validation...")
    try:
        ret_step4 = run_python_script("scripts/docker_validation.py")
        code, stdout, stderr = ret_step4
        step4_result = "PASS" if code == 0 else "FAIL"
        print(f"STEP 4 RESULT = {step4_result}")
        print(f"STEP 4 RETURN VALUE =\n{repr(ret_step4)}")
    except Exception:
        traceback.print_exc()
        raise
    print(stdout)
    validation_results["steps"]["docker_validation"] = {
        "status": step4_result,
        "output": stdout,
        "error": stderr
    }

    # Step 5: AWS Validation
    print("\nENTER STEP 5")
    print("[Step 5/5] Running AWS Task Definition & Infrastructure Validation...")
    try:
        ret_step5 = run_python_script("scripts/aws_validation.py")
        code, stdout, stderr = ret_step5
        step5_result = "PASS" if code == 0 else "FAIL"
        print(f"STEP 5 RESULT = {step5_result}")
        print(f"STEP 5 RETURN VALUE =\n{repr(ret_step5)}")
    except Exception:
        traceback.print_exc()
        raise
    print(stdout)
    validation_results["steps"]["aws_validation"] = {
        "status": step5_result,
        "output": stdout,
        "error": stderr
    }

    # Immediately before the final PASS/FAIL calculation print: validation_results using pprint.pprint()
    print("\nVALIDATION RESULTS BEFORE CALCULATION:")
    pprint.pprint(validation_results)

    # Final Evaluation
    all_passed = all(step["status"] == "PASS" for step in validation_results["steps"].values())
    validation_results["status"] = "PASS" if all_passed else "FAIL"

    print("\n==========================================================")
    print(f" PRE-DEPLOYMENT VALIDATION FINAL RESULT: {validation_results['status']}")
    print("==========================================================")

    with open("reports/pre_deployment_validation_summary.json", "w", encoding="utf-8") as f:
        json.dump(validation_results, f, indent=2)

    if validation_results["status"] == "FAIL":
        print("\nWHY EXITING")
        print(f"all_passed={all_passed}")
        print("validation_results=")
        pprint.pprint(validation_results)
        print("\n[X] PRE-DEPLOYMENT VALIDATION FAILED! ABORTING DEPLOYMENT.")
        sys.exit(1)

    print("\n[OK] ALL PRE-DEPLOYMENT CHECKS PASSED! PROCEEDING TO DEPLOYMENT.")
    sys.exit(0)


if __name__ == "__main__":
    main()
