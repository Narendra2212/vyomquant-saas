#!/usr/bin/env python3
"""
scripts/post_deployment_validation.py

Automated Post-Deployment Verification Suite
Probes deployed environment endpoints:
 1. GET /health
 2. GET /health/live
 3. GET /health/ready
 4. GET /metrics
 5. GET /api/stats
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Dict, Tuple


def probe_endpoint(url: str, timeout: int = 5) -> Tuple[bool, int, str]:
    """Execute HTTP GET request against endpoint."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PostDeploymentVal/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
            return resp.status == 200, resp.status, body
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode('utf-8')
        except Exception:
            err_body = str(e)
        return False, e.code, err_body
    except Exception as e:
        return False, 0, str(e)


def run_post_deployment_checks(base_url: str) -> Dict:
    """Execute post-deployment validation checks."""
    results = {
        "base_url": base_url,
        "status": "PASS",
        "endpoints": {},
        "summary": {}
    }

    target_endpoints = [
        ("/health/live", "Liveness Probe"),
        ("/health/ready", "Readiness Probe"),
        ("/health", "Health Status"),
        ("/metrics", "Prometheus Metrics"),
        ("/api/stats", "API Stats")
    ]

    all_passed = True

    for path, name in target_endpoints:
        url = f"{base_url.rstrip('/')}{path}"
        ok, status_code, body = probe_endpoint(url)
        results["endpoints"][path] = {
            "name": name,
            "url": url,
            "status_code": status_code,
            "success": ok,
            "snippet": body[:200] if body else ""
        }

        # /health/live, /health/ready, /health, /metrics must return 200
        if not ok:
            all_passed = False

    results["status"] = "PASS" if all_passed else "FAIL"
    return results


def main():
    base_url = "http://localhost:8000"
    for arg in sys.argv[1:]:
        if arg.startswith("--base-url="):
            base_url = arg.split("=")[1]

    print("==========================================================")
    print(f"   VYOMQUANT AUTOMATED POST-DEPLOYMENT VALIDATION         ")
    print(f"   Target Base URL: {base_url}")
    print("==========================================================")

    res = run_post_deployment_checks(base_url)

    for path, ep in res["endpoints"].items():
        symbol = "✅" if ep["success"] else "❌"
        print(f"{symbol} {ep['name']} ({path}): HTTP {ep['status_code']}")

    print(f"\nFinal Post-Deployment Status: {res['status']}")

    os.makedirs("reports", exist_ok=True)
    with open("reports/post_deployment_validation_summary.json", "w") as f:
        json.dump(res, f, indent=2)

    if res["status"] == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
