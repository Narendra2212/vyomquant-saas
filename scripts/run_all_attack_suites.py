import subprocess
import sys
import time
import os

scripts = [
    ("API Boundary Destruction", "scripts/attack_api_boundary_destruction.py"),
    ("Extreme Financial Concurrency", "scripts/attack_extreme_financial_concurrency.py"),
    ("Contention & Transaction Destruction", "scripts/attack_contention_transaction_destruction.py"),
    ("End-to-End Financial Destruction", "scripts/attack_end_to_end_financial_destruction.py"),
    ("Resource Saturation Destruction", "scripts/attack_resource_saturation_destruction.py"),
    ("Real Multiprocess Chaos", "scripts/attack_real_multiprocess_chaos.py"),
    ("Backend P0/P1 Destruction", "scripts/attack_backend_p0_p1_destruction.py"),
    ("Crash Recovery Destruction", "scripts/attack_crash_recovery_destruction.py"),
    ("Frontend/Backend Integration", "scripts/attack_frontend_backend_integration.py"),
    ("Internal Billing Full", "scripts/attack_internal_billing_full.py"),
    ("Unknown States Full", "scripts/attack_unknown_states_full.py"),
    ("Order Lifecycle Full", "scripts/attack_order_lifecycle_full.py"),
    ("Cross Boundary Full", "scripts/attack_cross_boundary_full.py"),
    ("Money Control v2", "scripts/attack_money_control_v2.py"),
    ("Runtime Soak & Process Destruction", "scripts/attack_runtime_soak_process_destruction.py"),
    ("Database Integrity & Concurrency", "scripts/attack_database_integrity_destruction.py"),
]

print("=" * 70)
print("RUNNING COMPLETE 16-SUITE REGRESSION BATTERY")
print("=" * 70)

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"

all_passed = True
for name, script_path in scripts:
    t0 = time.time()
    res = subprocess.run(
        [sys.executable, "-X", "utf8", script_path],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    dur = time.time() - t0
    status = "PASS" if res.returncode == 0 else "FAIL"
    print(f"[{status}] {name:<42} ({dur:.2f}s)")
    if res.returncode != 0:
        all_passed = False
        stdout_lines = (res.stdout or "").splitlines()
        stderr_lines = (res.stderr or "").splitlines()
        if stdout_lines:
            print("  Stdout (last 10):")
            print("  " + "\n  ".join(stdout_lines[-10:]))
        if stderr_lines:
            print("  Stderr (last 10):")
            print("  " + "\n  ".join(stderr_lines[-10:]))

print("=" * 70)
print(f"OVERALL RESULT: {'ALL PASS' if all_passed else 'FAILURES DETECTED'}")
print("=" * 70)
sys.exit(0 if all_passed else 1)
