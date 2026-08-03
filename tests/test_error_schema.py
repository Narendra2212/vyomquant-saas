"""
tests/test_error_schema.py

Unit tests for the unified APIErrorResponse schema and global exception handlers.
Covers:
  - create_api_error_response() with string, dict, and list inputs
  - Global HTTPException handler (via FastAPI test client)
  - Global RequestValidationError handler (422 normalisation)
  - Existing rich dict detail from orders.py-style handlers
  - HTTP status codes are unchanged by normalisation
"""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend_app.core.schemas import (
    APIErrorResponse,
    create_api_error_response,
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Unit tests for create_api_error_response()
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateApiErrorResponse:
    """create_api_error_response() must produce a canonical dict regardless of input."""

    def _validate_shape(self, result: dict, expected_status: int):
        """Assert the canonical fields are present and correctly typed."""
        assert isinstance(result["error"], str)
        assert isinstance(result["message"], str)
        # detail can be either a string (backward compat), dict (structured payload), or list (validation errors)
        assert isinstance(result["detail"], (str, dict, list)), "detail must be string, dict, or list"
        assert result["status_code"] == expected_status
        assert "timestamp" in result
        assert isinstance(result["timestamp"], str)

    def test_string_detail_400(self):
        r = create_api_error_response(400, "No valid fields provided.")
        self._validate_shape(r, 400)
        assert r["error"] == "BAD_REQUEST"
        assert r["message"] == "No valid fields provided."
        assert r["details"] is None
        assert r["solution"] is None

    def test_string_detail_401(self):
        r = create_api_error_response(401, "Missing authenticated Supabase token.")
        self._validate_shape(r, 401)
        assert r["error"] == "UNAUTHORIZED"

    def test_string_detail_403(self):
        r = create_api_error_response(403, "Forbidden")
        self._validate_shape(r, 403)
        assert r["error"] == "FORBIDDEN"

    def test_string_detail_404(self):
        r = create_api_error_response(404, "Strategy not found.")
        self._validate_shape(r, 404)
        assert r["error"] == "NOT_FOUND"

    def test_string_detail_500(self):
        r = create_api_error_response(500, "Internal server error")
        self._validate_shape(r, 500)
        assert r["error"] == "INTERNAL_SERVER_ERROR"

    def test_string_detail_503(self):
        r = create_api_error_response(503, "Failed to publish backtest job to queue")
        self._validate_shape(r, 503)
        assert r["error"] == "SERVICE_UNAVAILABLE"

    def test_dict_detail_rich_orders_style(self):
        """Mirrors the richest existing error format from orders.py."""
        detail = {
            "error": "DIRECT_EXECUTION_BLOCKED",
            "message": "Direct execution is not allowed. Use strategy deployment.",
            "allowed_path": "Strategy → DAG → BotRunner → UnifiedExecutionEngine",
            "blocked_path": "UI → API → Direct Execution",
            "solution": "Deploy a strategy via the strategy DAG system.",
        }
        r = create_api_error_response(403, detail)
        self._validate_shape(r, 403)
        assert r["error"] == "DIRECT_EXECUTION_BLOCKED"
        assert r["message"] == "Direct execution is not allowed. Use strategy deployment."
        assert r["solution"] == "Deploy a strategy via the strategy DAG system."
        # Extra fields moved to details
        assert r["details"]["allowed_path"] == "Strategy → DAG → BotRunner → UnifiedExecutionEngine"
        assert r["details"]["blocked_path"] == "UI → API → Direct Execution"

    def test_dict_detail_partial_portfolio_style(self):
        """Mirrors portfolio.py-style partial dict (no solution)."""
        detail = {
            "error": "PORTFOLIO_FETCH_FAILED",
            "message": "Cannot fetch portfolio data (cache unavailable): timeout",
        }
        r = create_api_error_response(503, detail)
        self._validate_shape(r, 503)
        assert r["error"] == "PORTFOLIO_FETCH_FAILED"
        assert r["solution"] is None

    def test_dict_detail_with_extra_numeric_fields(self):
        """Mirrors portfolio stale cache dict with numeric extra fields."""
        detail = {
            "error": "PORTFOLIO_CACHE_STALE",
            "message": "Portfolio data is stale (1.5s old).",
            "cache_age_seconds": 1.5,
            "max_acceptable_seconds": 1.0,
        }
        r = create_api_error_response(503, detail)
        self._validate_shape(r, 503)
        assert r["error"] == "PORTFOLIO_CACHE_STALE"
        assert r["details"]["cache_age_seconds"] == 1.5
        assert r["details"]["max_acceptable_seconds"] == 1.0

    def test_list_detail_validation_errors(self):
        """Pydantic RequestValidationError produces a list of error dicts."""
        errors = [
            {"loc": ["body", "amount"], "msg": "value is not a valid float", "type": "type_error.float"},
            {"loc": ["body", "symbol"], "msg": "field required", "type": "value_error.missing"},
        ]
        r = create_api_error_response(422, errors)
        self._validate_shape(r, 422)
        assert r["error"] == "VALIDATION_ERROR"
        assert r["message"] == "Input validation failed."
        assert r["details"] == errors

    def test_path_is_preserved(self):
        r = create_api_error_response(404, "not found", path="/api/orders/123")
        assert r["path"] == "/api/orders/123"

    def test_none_path_is_preserved(self):
        r = create_api_error_response(400, "bad request")
        assert r["path"] is None

    def test_empty_string_detail(self):
        r = create_api_error_response(400, "")
        assert r["message"] == ""
        assert r["error"] == "BAD_REQUEST"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Integration tests via FastAPI TestClient
#    Build a minimal app that mirrors the global handler registration in main.py
# ──────────────────────────────────────────────────────────────────────────────

def _build_test_app() -> FastAPI:
    """Construct a miniature FastAPI app that replicates the global handler setup."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.exceptions import RequestValidationError
    from backend_app.core.schemas import create_api_error_response

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request, exc):
        body = create_api_error_response(exc.status_code, exc.detail, str(request.url.path))
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request, exc):
        body = create_api_error_response(422, exc.errors(), str(request.url.path))
        return JSONResponse(status_code=422, content=body)

    class Item(BaseModel):
        amount: float
        symbol: str

    @app.get("/string-error")
    def string_error():
        raise HTTPException(status_code=400, detail="No valid fields provided.")

    @app.get("/dict-error")
    def dict_error():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "DIRECT_EXECUTION_BLOCKED",
                "message": "Direct execution not allowed.",
                "solution": "Use strategy DAG.",
                "allowed_path": "DAG → BotRunner",
            },
        )

    @app.get("/not-found")
    def not_found():
        raise HTTPException(status_code=404, detail="Strategy not found.")

    @app.get("/auth-error")
    def auth_error():
        raise HTTPException(status_code=401, detail="Missing authenticated Supabase token.")

    @app.post("/validation-target")
    def validation_target(item: Item):
        return {"ok": True}

    return app


@pytest.fixture(scope="module")
def test_client():
    return TestClient(_build_test_app(), raise_server_exceptions=False)


class TestGlobalHandlers:
    def test_string_400_has_canonical_shape(self, test_client):
        r = test_client.get("/string-error")
        assert r.status_code == 400
        data = r.json()
        assert data["error"] == "BAD_REQUEST"
        assert data["message"] == "No valid fields provided."
        assert data["detail"] == "No valid fields provided."
        assert data["status_code"] == 400
        assert "timestamp" in data

    def test_string_404_has_canonical_shape(self, test_client):
        r = test_client.get("/not-found")
        assert r.status_code == 404
        data = r.json()
        assert data["error"] == "NOT_FOUND"
        assert data["message"] == "Strategy not found."

    def test_string_401_has_canonical_shape(self, test_client):
        r = test_client.get("/auth-error")
        assert r.status_code == 401
        data = r.json()
        assert data["error"] == "UNAUTHORIZED"
        assert data["message"] == "Missing authenticated Supabase token."

    def test_dict_403_rich_shape_preserved(self, test_client):
        r = test_client.get("/dict-error")
        assert r.status_code == 403
        data = r.json()
        assert data["error"] == "DIRECT_EXECUTION_BLOCKED"
        assert data["message"] == "Direct execution not allowed."
        assert data["solution"] == "Use strategy DAG."
        assert data["details"]["allowed_path"] == "DAG → BotRunner"
        assert data["status_code"] == 403

    def test_validation_error_422_has_canonical_shape(self, test_client):
        # Missing required fields triggers RequestValidationError
        r = test_client.post("/validation-target", json={})
        assert r.status_code == 422
        data = r.json()
        assert data["error"] == "VALIDATION_ERROR"
        assert data["message"] == "Input validation failed."
        assert isinstance(data["details"], list)
        assert len(data["details"]) > 0
        # Confirm the loc/msg/type fields are in details (not lost)
        first = data["details"][0]
        assert "msg" in first
        assert "type" in first

    def test_path_is_included(self, test_client):
        r = test_client.get("/not-found")
        data = r.json()
        assert data["path"] == "/not-found"

    def test_status_codes_are_unchanged(self, test_client):
        """HTTP status codes must be unchanged — only body shape changes."""
        assert test_client.get("/string-error").status_code == 400
        assert test_client.get("/dict-error").status_code == 403
        assert test_client.get("/not-found").status_code == 404
        assert test_client.get("/auth-error").status_code == 401
        assert test_client.post("/validation-target", json={}).status_code == 422

    def test_backward_compat_detail_string(self, test_client):
        """Frontend legacy reads of .data.detail must still work."""
        r = test_client.get("/string-error")
        data = r.json()
        # Frontend reads error.response.data.detail
        assert isinstance(data["detail"], str)
        assert data["detail"] == data["message"]

    def test_backward_compat_message(self, test_client):
        """Frontend reads of .data.message must still work."""
        r = test_client.get("/dict-error")
        data = r.json()
        assert isinstance(data["message"], str)
        assert len(data["message"]) > 0
