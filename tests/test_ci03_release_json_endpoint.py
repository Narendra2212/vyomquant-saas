"""CI-03: GET /release.json is the backend counterpart to the frontend's own
dist/release.json stamp (06-frontend-deploy.yml), so a FE-build-SHA == BE-image-SHA
contract check can actually be made. Before this, the UI exposed its build commit and
the API exposed nothing comparable."""

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
