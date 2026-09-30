"""tests/test_badhost_path_poisoning_regression.py

A Host header cannot change the path this service makes decisions on.

THE DEFECT (CVE-2026-48710, "BadHost", GHSA-86qp-5c8j-p5mr)
-----------------------------------------------------------
``starlette 0.41.0`` - the version ``fastapi 0.115.3`` pins via
``starlette<0.42.0,>=0.40.0`` - builds ``request.url`` by joining the Host header to the path
and RE-PARSING the result. So a request carrying::

    Host: app.vyomquant.in/health

makes ``request.url.path`` return ``/health/api/strategies`` for a request to
``/api/strategies``. ``scope["path"]`` is taken from the request line and is unaffected.

WHAT THAT REACHED IN THIS TREE
------------------------------
* ``main.py`` labelled every Prometheus metric with ``request.url.path``, so one client could
  mint unbounded label cardinality, and three exception handlers reflected the poisoned value
  back in the response body. Both were live.
* ``core/tenant_middleware.py`` skipped AUTHENTICATION when the path started with one of
  ``skip_paths`` (``/health``, ``/docs``, ``/api/auth/login``...). A Host of ``x/health`` makes
  that ``startswith`` match for ANY route. That middleware is **not currently installed on the
  app**, so this was latent rather than exploitable - and it is fixed anyway, because leaving it
  would be a loaded gun for whoever mounts it next.

WHY NOT TrustedHostMiddleware
-----------------------------
It was considered and rejected. The ALB health check addresses the task by IP, so a Host
allow-list would answer 400, the target would fail its health check and ECS would kill the
task. ``scope["path"]`` needs no allow-list to keep in sync with reality. The real remedy is
``starlette >= 1.0``, which cannot land until ``fastapi`` is upgraded (task 13.12*).
"""

import io
import os
import sys

import pytest
from starlette.datastructures import URL

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: Every file that reads a request path to decide or record something.
PATH_READERS = [
    "backend_app/main.py",
    "backend_app/core/tenant_middleware.py",
    "backend_app/core/subscription_middleware.py",
    "backend_app/core/rate_limit_middleware.py",
]

REAL_PATH = "/api/strategies"


def scope_for(host):
    return {
        "type": "http",
        "scheme": "https",
        "path": REAL_PATH,
        "query_string": b"",
        "headers": [(b"host", host.encode())],
        "server": ("10.0.0.1", 8000),
    }


def executable(rel):
    """Source with ``#`` comments stripped, so the explanatory notes cannot satisfy a scan."""
    text = io.open(os.path.join(REPO, rel), encoding="utf-8").read()
    return chr(10).join([line.split("#")[0] for line in text.split(chr(10))])


class TestThePremiseTheLibraryIsStillVulnerable:
    """If these fail, the pinned starlette was upgraded and the workaround can be revisited."""

    def test_url_path_is_poisonable_by_the_host_header(self):
        poisoned = URL(scope=scope_for("app.vyomquant.in/health")).path

        assert poisoned == "/health" + REAL_PATH, (
            "starlette no longer rebuilds url.path from the Host header (got %r). If it was "
            "upgraded to >= 1.0, re-read the note above main.py exception handlers." % poisoned
        )

    def test_the_poisoned_path_would_match_an_auth_skip_prefix(self):
        """Why this mattered: a startswith over skip_paths matches for ANY route."""
        poisoned = URL(scope=scope_for("anything/health")).path

        assert poisoned.startswith("/health")

    def test_scope_path_is_not_poisonable(self):
        for host in ("app.vyomquant.in", "x/health", "evil.com/api/auth/login"):
            assert scope_for(host)["path"] == REAL_PATH


class TestNoPathDecisionReadsThePoisonableValue:
    @pytest.mark.parametrize("rel", PATH_READERS)
    def test_url_path_is_not_read(self, rel):
        body = executable(rel)

        assert "request.url.path" not in body, (
            "%s still reads request.url.path, which starlette 0.41.0 rebuilds from the Host "
            "header. Use request.scope[\x27path\x27]." % rel
        )

    @pytest.mark.parametrize("rel", PATH_READERS)
    def test_it_reads_the_scope_path_instead(self, rel):
        body = executable(rel)

        assert "scope[" in body, (
            "%s no longer reads a request path at all; expected request.scope[\x27path\x27]" % rel
        )


class TestTheAuthSkipCannotBeReachedByAHeader:
    def test_a_poisoned_host_does_not_match_a_skip_path(self):
        """The decision itself, exercised rather than grepped.

        Mirrors ``TenantMiddleware.dispatch``: the same prefixes, the same startswith, fed the
        same poisoned request - and now reading the value the fix reads.
        """
        from backend_app.core.tenant_middleware import TenantMiddleware

        skip_paths = TenantMiddleware(app=None).skip_paths
        scope = scope_for("anything/health")

        poisonable = URL(scope=scope).path
        authoritative = scope["path"]

        assert any(poisonable.startswith(p) for p in skip_paths), (
            "the poisoned path no longer matches any skip prefix, so this test proves nothing"
        )
        assert not any(authoritative.startswith(p) for p in skip_paths), (
            "scope[path] matched a skip prefix for %s - auth would still be skippable"
            % REAL_PATH
        )
