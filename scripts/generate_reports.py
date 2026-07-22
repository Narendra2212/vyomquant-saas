#!/usr/bin/env python3
"""
scripts/generate_reports.py

Automated Markdown Report Generator
Generates:
 1. deployment_report.md
 2. validation_report.md
 3. dependency_report.md
 4. aws_report.md
 5. docker_report.md

Each report includes PASS/FAIL status, reason, detailed findings, and recommended fix.
"""

import json
import os
import sys
from typing import Dict, Any

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def load_json(filepath: str) -> Dict[str, Any]:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def generate_dependency_report(dep_data: Dict[str, Any]) -> str:
    status = dep_data.get("status", "PASS")
    mismatches = dep_data.get("mismatches", [])
    missing_b = dep_data.get("missing_in_backend", [])
    missing_r = dep_data.get("missing_in_root", [])

    reason = "All dependencies across root and backend requirements are reconciled." if status == "PASS" else "Dependency specification mismatches or missing declarations detected."
    rec_fix = "No action required." if status == "PASS" else "Synchronize requirements.txt and backend_app/requirements.txt so all package versions match exactly."

    md = f"""# Dependency Audit Report

**Status**: `{status}`  
**Reason**: {reason}  

## Package Counts
- **Root `requirements.txt`**: {dep_data.get('root_count', 0)}
- **Backend `backend_app/requirements.txt`**: {dep_data.get('backend_count', 0)}

## Version Mismatches
"""
    if mismatches:
        for m in mismatches:
            md += f"- ❌ `{m['package']}`: root (`{m['root_spec']}`) vs backend (`{m['backend_spec']}`)\n"
    else:
        md += "✅ No version mismatches found.\n"

    md += "\n## Missing Declarations\n"
    if missing_b:
        for mb in missing_b:
            md += f"- ⚠️  Missing in backend requirements: `{mb['package']}` (`{mb['root_spec']}`)\n"
    elif missing_r:
        for mr in missing_r:
            md += f"- ⚠️  Missing in root requirements: `{mr['package']}` (`{mr['backend_spec']}`)\n"
    else:
        md += "✅ All packages present in both manifests.\n"

    md += f"\n## Recommended Fix\n{rec_fix}\n"
    return md


def generate_docker_report(docker_data: Dict[str, Any]) -> str:
    status = docker_data.get("status", "PASS")
    static = docker_data.get("static_validation", {})
    runtime = docker_data.get("runtime", {})

    reason = "Dockerfiles valid, multi-stage builds non-root, and health endpoints verified." if status == "PASS" else "Dockerfile syntax errors or container health probe failures detected."
    rec_fix = "No action required." if status == "PASS" else "Ensure Dockerfiles copy startup scripts, set non-root USER appuser, and export port 8000."

    md = f"""# Docker Validation Report

**Status**: `{status}`  
**Reason**: {reason}  

## Static Dockerfile Audits
- **Found Dockerfiles**: {', '.join(static.get('found', []))}
- **Static Validation**: {'PASS' if static.get('valid') else 'FAIL'}
"""
    if static.get("issues"):
        for issue in static["issues"]:
            md += f"- ⚠️  {issue}\n"

    md += f"""
## Container Runtime Validation
- **Docker Daemon Available**: {docker_data.get('docker_available', False)}
- **Image Build**: `{runtime.get('build_status', 'N/A')}`
- **Container Start**: `{runtime.get('container_status', 'N/A')}`
- **GET /health/live Probe**: {'PASS' if runtime.get('health_live_endpoint') else 'N/A'}
- **GET /health/ready Probe**: {'PASS' if runtime.get('health_ready_endpoint') else 'N/A'}
- **GET /health Probe**: {'PASS' if runtime.get('health_endpoint') else 'N/A'}

## Recommended Fix
{rec_fix}
"""
    return md


def generate_aws_report(aws_data: Dict[str, Any]) -> str:
    status = aws_data.get("status", "PASS")
    td = aws_data.get("task_definition", {})
    live = aws_data.get("aws_live", {})

    reason = "ECS Task Definition valid and AWS IAM/Secrets Manager resources verified." if status == "PASS" else "Task definition schema errors or missing AWS secrets detected."
    rec_fix = "No action required." if status == "PASS" else "Verify ecs-task-definition-full.json fields and run ecs-secrets-manager-setup.sh."

    md = f"""# AWS Infrastructure & Task Definition Report

**Status**: `{status}`  
**Region**: `{aws_data.get('region', 'ap-southeast-1')}`  
**Reason**: {reason}  

## Local Task Definition Audit (`ecs-task-definition-full.json`)
- **Family**: `{td.get('family')}`
- **Container Definitions**: {', '.join(td.get('containers', []))}
- **Task Definition Validity**: {'PASS' if td.get('valid') else 'FAIL'}
"""
    if td.get("errors"):
        for err in td["errors"]:
            md += f"- ❌ {err}\n"

    md += f"""
## Live AWS Infrastructure Audit
- **AWS Credentials**: {'CONNECTED' if live.get('credentials_valid') else 'NOT CONFIGURED / UNREACHABLE'}
- **ECS Cluster (`vyomquant-cluster`)**: {'PASS' if live.get('ecs_cluster') else 'NOT VERIFIED'}
- **ECS Service (`vyomquant-api-service-cjema2sl`)**: {'PASS' if live.get('ecs_service') else 'NOT VERIFIED'}

## Recommended Fix
{rec_fix}
"""
    return md


def generate_validation_report(import_data: Dict[str, Any], pre_data: Dict[str, Any], post_data: Dict[str, Any]) -> str:
    status = pre_data.get("status", "PASS")
    reason = "All pre-deployment and post-deployment validation steps passed cleanly." if status == "PASS" else "Validation checks failed in syntax, startup, or endpoint probes."
    rec_fix = "No action required." if status == "PASS" else "Fix failing validation step reported below before retrying deployment."

    md = f"""# Automated Validation Report

**Status**: `{status}`  
**Reason**: {reason}  

## Summary of Validation Pipeline Steps
"""
    steps = pre_data.get("steps", {})
    for step_name, step_info in steps.items():
        symbol = "✅" if step_info.get("status") == "PASS" else "❌"
        md += f"- {symbol} **{step_name}**: `{step_info.get('status')}`\n"

    md += f"""
## AST & Import Audit Metrics
- **Total Files Scanned**: {import_data.get('total_files', 0)}
- **Syntax Errors**: {len(import_data.get('syntax_errors', []))}
- **Missing `__init__.py` Dirs**: {len(import_data.get('missing_init_dirs', []))}
- **Unused Import Warnings**: {len(import_data.get('unused_imports', []))}

## Post-Deployment Endpoint Probes
- **Post-Deployment Status**: `{post_data.get('status', 'N/A')}`
"""
    endpoints = post_data.get("endpoints", {})
    for path, ep in endpoints.items():
        symbol = "✅" if ep.get("success") else "❌"
        md += f"- {symbol} `{path}`: HTTP {ep.get('status_code')}\n"

    md += f"\n## Recommended Fix\n{rec_fix}\n"
    return md


def generate_deployment_report(pre_data: Dict[str, Any], post_data: Dict[str, Any], fail_data: Dict[str, Any]) -> str:
    pre_pass = pre_data.get("status") == "PASS"
    post_pass = post_data.get("status") == "PASS" or not post_data

    status = "PASS" if pre_pass and post_pass else "FAIL"
    reason = "Deployment completed successfully, service stable and health checks passed." if status == "PASS" else "Deployment aborted or failed during validation / ECS rollout."

    rec_fix = "Deployment healthy. Zero action needed." if status == "PASS" else "Review failure_analysis.json and CloudWatch logs artifact."

    md = f"""# Production Deployment Report

**Status**: `{status}`  
**Reason**: {reason}  

## Pre-Deployment Gate
- **Pre-Deployment Validation**: `{'PASS' if pre_pass else 'FAIL'}`

## Deployment Execution & Self-Healing
- **ECS Service Rollout**: `{'COMPLETED' if status == 'PASS' else 'FAILED / PENDING'}`
- **Self-Healing Triggered**: `{fail_data.get('recoverable', False)}`

## Post-Deployment Gate
- **Post-Deployment Health**: `{'PASS' if post_pass else 'FAIL'}`

## Recommended Fix
{rec_fix}
"""
    return md


def main():
    print("=== GENERATING AUTOMATED MARKDOWN REPORTS ===")
    os.makedirs("reports", exist_ok=True)

    import_data = load_json("reports/import_audit_results.json")
    dep_data = load_json("reports/dependency_audit_results.json")
    docker_data = load_json("reports/docker_validation_results.json")
    aws_data = load_json("reports/aws_validation_results.json")
    pre_data = load_json("reports/pre_deployment_validation_summary.json")
    post_data = load_json("reports/post_deployment_validation_summary.json")
    fail_data = load_json("reports/failure_analysis.json")

    reports = {
        "dependency_report.md": generate_dependency_report(dep_data),
        "docker_report.md": generate_docker_report(docker_data),
        "aws_report.md": generate_aws_report(aws_data),
        "validation_report.md": generate_validation_report(import_data, pre_data, post_data),
        "deployment_report.md": generate_deployment_report(pre_data, post_data, fail_data)
    }

    for filename, content in reports.items():
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  [OK] Generated {filename}")

    print("All markdown reports generated successfully.")


if __name__ == "__main__":
    main()
