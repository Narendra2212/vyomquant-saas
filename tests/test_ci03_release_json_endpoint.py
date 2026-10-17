"""CI-03: GET /release.json is the backend counterpart to the frontend's own
dist/release.json stamp (06-frontend-deploy.yml), so a FE-build-SHA == BE-image-SHA
contract check can actually be made. Before this, the UI exposed its build commit and
the API exposed nothing comparable.

ADDITIONALLY: GET /api/release.json. The real production CloudFront distribution only
proxies /api/*, /ws/*, /health*, /metrics* and /openapi.json to the ALB -- a bare
/release.json falls through to the default S3 behaviour and returns the FRONTEND's own
static file, never reaching this backend endpoint. The /api/ alias is what
06-frontend-deploy.yml's version-match comparison step actually fetches; this file
pins that BOTH paths serve the identical shape, so a future change to one cannot drift
from the other unnoticed."""

import pytest
from fastapi.testclient import TestClient

from backend_app.main import app

client = TestClient(app)


def test_release_json_reports_the_baked_in_sha_and_build_time(monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc1234567")
    monkeypatch.setenv("BUILT_AT", "2026-10-16T00:00:00Z")

    res = client.get("/release.json")

    assert res.status_code == 200
    body = res.json()
    assert body["sha"] == "abc1234567"
    assert body["built_at"] == "2026-10-16T00:00:00Z"


def test_release_json_defaults_to_unknown_rather_than_erroring(monkeypatch):
    """A `docker build` run with no --build-arg (e.g. a local build outside this
    pipeline's CI) must not crash the endpoint -- it must honestly report 'unknown',
    matching the Dockerfile ARG's own default."""
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("BUILT_AT", raising=False)

    res = client.get("/release.json")

    assert res.status_code == 200
    body = res.json()
    assert body["sha"] == "unknown"
    assert body["built_at"] == "unknown"


def test_release_json_is_unauthenticated():
    """Must be reachable the same way the frontend's own release.json is -- no login,
    so a deploy-verification script can compare both without credentials."""
    res = client.get("/release.json")
    assert res.status_code == 200


def test_api_release_json_exists_and_matches_the_bare_path(monkeypatch):
    """The real CloudFront distribution does not proxy a bare /release.json to the ALB
    (only /api/*, /ws/*, /health*, /metrics*, /openapi.json) -- a request to the public
    domain's /release.json returns the frontend's own static file, never this endpoint.
    /api/release.json is the one 06-frontend-deploy.yml's comparison step can actually
    reach, and it must report the exact same {sha, built_at} the bare path does."""
    monkeypatch.setenv("GIT_SHA", "def4567890")
    monkeypatch.setenv("BUILT_AT", "2026-10-17T00:00:00Z")

    bare = client.get("/release.json")
    api = client.get("/api/release.json")

    assert api.status_code == 200
    assert api.json() == bare.json() == {
        "sha": "def4567890",
        "built_at": "2026-10-17T00:00:00Z",
    }


def test_api_release_json_is_unauthenticated():
    res = client.get("/api/release.json")
    assert res.status_code == 200
