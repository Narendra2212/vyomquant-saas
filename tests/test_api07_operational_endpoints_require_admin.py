"""API-07: operational/observability endpoints were public with no auth at all.

/metrics, /health/services, and /api/system/{readiness,health,dependencies,workers,
exchanges} leaked internal service names, dependency topology, worker counts, and
exchange credential status to any unauthenticated caller. This is a reconnaissance
surface, not a safety control -- it cannot cause a trade or move money -- so it is
P3/Low, but it still has no business being public.

Fix: gate the 6 `/api/system/*` + `/health/services` endpoints behind
`Depends(get_admin_user)` in backend_app/main.py. Two endpoints are deliberately left
untouched:
 - `/api/system/trading-capabilities` has its own docstring explaining it must be
   reachable before login (pre-auth UI needs to know what the backend supports).
 - `/health`, `/health/live`, `/health/ready` are the load-balancer / Kubernetes
   health-check targets (confirmed against deploy-archive/k8s/ingress.yaml's
   `healthcheck-path: /health` and healthcheck.sh's `curl .../health/live`) --
   gating these would break orchestration, not just "leak" anything.
 - `/metrics` (backend_app/routers/metrics.py) is the Prometheus scrape endpoint.
   Restricting it is a genuinely infra-level action (VPC/ALB network policy or a
   scrape bearer-token shared with the Prometheus server) that cannot be verified
   or safely implemented from this sandbox, so it is explicitly NOT touched here
   and the finding stays IN_PROGRESS for that reason.
"""
from fastapi.testclient import TestClient

from backend_app.main import app
from backend_app.core.dependencies import get_admin_user

client = TestClient(app)

ADMIN_GATED_PATHS = [
    "/health/services",
    "/api/system/readiness",
    "/api/system/health",
    "/api/system/dependencies",
    "/api/system/workers",
    "/api/system/exchanges",
]

DELIBERATELY_PUBLIC_PATHS = [
    "/api/system/trading-capabilities",
    "/health",
    "/health/live",
    "/health/ready",
]


def _as_admin():
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "app_metadata": {"role": "admin"},
    }


def _clear_overrides():
    app.dependency_overrides.clear()


def test_each_gated_endpoint_rejects_an_unauthenticated_caller():
    for path in ADMIN_GATED_PATHS:
        res = client.get(path)
        assert res.status_code in (401, 403), (
            f"{path} must reject an unauthenticated caller now, got {res.status_code}"
        )


def test_each_gated_endpoint_accepts_an_admin_caller():
    _as_admin()
    try:
        for path in ADMIN_GATED_PATHS:
            res = client.get(path)
            assert res.status_code == 200, (
                f"{path} must still work for an admin, got {res.status_code}: {res.text}"
            )
    finally:
        _clear_overrides()


def test_each_gated_endpoint_rejects_a_non_admin_authenticated_caller():
    from backend_app.core.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: {
        "id": "trader-1", "app_metadata": {"role": "authenticated"},
    }
    try:
        for path in ADMIN_GATED_PATHS:
            res = client.get(path)
            assert res.status_code == 403, (
                f"{path} must reject a non-admin authenticated caller, got {res.status_code}"
            )
    finally:
        _clear_overrides()


def test_deliberately_public_endpoints_still_require_no_auth():
    for path in DELIBERATELY_PUBLIC_PATHS:
        res = client.get(path)
        assert res.status_code in (200, 503), (
            f"{path} is deliberately public (pre-login UI or LB/k8s health check target) "
            f"and must not have been gated; got {res.status_code}"
        )


def test_metrics_is_explicitly_not_gated_by_this_fix():
    """Documents the deferred scope: /metrics stays public here.

    Restricting Prometheus scraping is a network/infra decision (VPC-only ingress or a
    scrape token shared with the Prometheus server config), not something this backend
    process can safely decide on its own -- gating it in-app risks breaking the real
    scraper with no way to verify a fix from this sandbox. Tracked as the remaining
    action on this finding rather than silently dropped.
    """
    res = client.get("/metrics")
    assert res.status_code == 200, (
        "/metrics is intentionally left public by this change; if this starts "
        "failing because something else gated it, update the API-07 ledger entry "
        "to reflect that the infra-level action was completed"
    )
