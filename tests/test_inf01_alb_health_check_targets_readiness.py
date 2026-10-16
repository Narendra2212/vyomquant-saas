"""INF-01: the ALB health check for the API target group must be able to fail.

terraform/alb.tf's aws_lb_target_group "api" health check used to point at /health.
backend_app/main.py's health() handler always answers that path with HTTP 200 -- its
"status": "degraded" (Redis/Supabase/QuestDB down) lives only in the JSON body, which an
ALB health check (a bare status-code matcher) never reads. A target with no reachable
database or Redis still passed every check and kept receiving production traffic.

/health/ready (backend_app/core/readiness.py's evaluate_readiness(), added under OBS-01)
actually measures the database and Redis and returns a real 503 when either is down,
which the existing matcher = "200" does correctly read as unhealthy.

No terraform binary is available in this sandbox to parse the resource properly, so this
checks the raw text directly -- precisely, not by substring ("/health" is itself a
substring of "/health/ready", so a naive "path is not /health" check would be satisfied
by the very string this guards against regressing to only by accident).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALB_TF = REPO / "terraform" / "alb.tf"


def _api_target_group_block() -> str:
    text = ALB_TF.read_text(encoding="utf-8")
    match = re.search(
        r'resource\s+"aws_lb_target_group"\s+"api"\s*\{(.*?)\n\}\n',
        text,
        re.DOTALL,
    )
    assert match, (
        'could not find resource "aws_lb_target_group" "api" in terraform/alb.tf -- '
        "the regex needs updating to match a renamed/restructured resource, not the "
        "finding this test guards."
    )
    return match.group(1)


def test_api_target_group_health_check_targets_readiness():
    block = _api_target_group_block()

    path_match = re.search(r'path\s*=\s*"([^"]+)"', block)
    assert path_match, 'aws_lb_target_group "api" has no health_check.path set'
    assert path_match.group(1) == "/health/ready", (
        f"expected the API target group's health check to point at /health/ready, got "
        f"{path_match.group(1)!r} instead. A path that always answers 200 regardless of "
        f"dependency state (like /health) defeats the purpose of a health check (INF-01)."
    )

    matcher_match = re.search(r'matcher\s*=\s*"([^"]+)"', block)
    assert matcher_match, 'aws_lb_target_group "api" has no health_check.matcher set'
    assert matcher_match.group(1) == "200", (
        "the matcher must stay a plain '200': /health/ready answers 200 when ready and "
        "503 when not (core/readiness.py), and a matcher widened to accept 503 too would "
        "silently defeat the fix this test guards."
    )


def test_api_target_group_health_check_timeout_fits_the_probe_budget():
    """core/readiness.py's PROBE_TIMEOUT_SECONDS is 3.0s per dependency; the ALB's own
    health-check timeout must stay comfortably above that or a slow-but-healthy dependency
    probe gets reported as a connection failure rather than a real readiness answer."""
    block = _api_target_group_block()

    timeout_match = re.search(r"timeout\s*=\s*(\d+)", block)
    assert timeout_match, 'aws_lb_target_group "api" has no health_check.timeout set'
    assert int(timeout_match.group(1)) >= 5, (
        "health_check.timeout dropped below 5s, too close to readiness.py's own "
        "3.0s-per-dependency PROBE_TIMEOUT_SECONDS to leave any margin."
    )
