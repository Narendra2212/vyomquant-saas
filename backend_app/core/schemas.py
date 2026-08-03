# core/schemas.py

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class SubscriptionTier(str, Enum):
    FREE = "free"
    PRO = "pro_999"
    ELITE = "elite_1999"


class CheckoutRequest(BaseModel):
    tier: SubscriptionTier
    currency: str = "INR"  # "INR" triggers Razorpay, "USD" triggers Stripe
    is_addon: bool = False  # Set to true if buying the 199 INR ML strategy addon


class UserLimits(BaseModel):
    tier: SubscriptionTier
    deployed_bots_count: int
    ml_strategies_built: int
    allowed_deployments: int
    allowed_ml_builds: int


class APIErrorResponse(BaseModel):
    """
    Standardized, uniform API error response schema for all endpoints.
    Strict superset of rich error formats across all routers.
    """
    error: str = Field(..., description="Short upper_snake_case error code (e.g. 'NOT_FOUND', 'INVALID_QUANTITY')")
    message: str = Field(..., description="Human-readable error message")
    detail: Any = Field(..., description="Backward-compatible detail field matching message or details payload")
    status_code: int = Field(..., description="HTTP status code (e.g. 400, 401, 403, 404, 429, 500)")
    timestamp: str = Field(..., description="ISO-8601 UTC timestamp")
    path: Optional[str] = Field(None, description="Request path")
    details: Optional[Any] = Field(None, description="Additional context or validation details")
    solution: Optional[str] = Field(None, description="Actionable advice for error resolution")


def _sanitize_validation_errors(errors: list) -> list:
    """
    Strip non-JSON-serializable objects from Pydantic error dicts.
    Pydantic v2 includes the raw exception object in ctx['error'], which
    cannot be serialized by JSONResponse.  Convert it to its string repr.
    """
    sanitized = []
    for err in errors:
        if not isinstance(err, dict):
            sanitized.append(str(err))
            continue
        clean = {}
        for k, v in err.items():
            if k == "ctx" and isinstance(v, dict):
                clean[k] = {
                    ck: str(cv) if isinstance(cv, Exception) else cv
                    for ck, cv in v.items()
                }
            elif isinstance(v, Exception):
                clean[k] = str(v)
            else:
                clean[k] = v
        sanitized.append(clean)
    return sanitized


def create_api_error_response(
    status_code: int,
    detail_or_msg: Any,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Constructs a uniform error response dict matching APIErrorResponse schema.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    
    error_code = f"HTTP_{status_code}_ERROR"
    message = "An error occurred."
    details = None
    solution = None
    detail_field = detail_or_msg  # Separate variable for the detail field

    if isinstance(detail_or_msg, dict):
        error_code = detail_or_msg.get("error") or detail_or_msg.get("error_code") or f"HTTP_{status_code}_ERROR"
        message = detail_or_msg.get("message") or detail_or_msg.get("detail") or "An error occurred."
        solution = detail_or_msg.get("solution")
        reserved_keys = {"error", "error_code", "message", "detail", "solution", "status_code", "timestamp", "path"}
        extra = {k: v for k, v in detail_or_msg.items() if k not in reserved_keys}
        if extra:
            details = extra
        elif "details" in detail_or_msg:
            details = detail_or_msg["details"]
    elif isinstance(detail_or_msg, list):
        error_code = "VALIDATION_ERROR"
        message = "Input validation failed."
        sanitized = _sanitize_validation_errors(detail_or_msg)
        details = sanitized
        detail_field = sanitized  # Use sanitized version for detail field
    elif isinstance(detail_or_msg, str):
        message = detail_or_msg
        if status_code == 400:
            error_code = "BAD_REQUEST"
        elif status_code == 401:
            error_code = "UNAUTHORIZED"
        elif status_code == 403:
            error_code = "FORBIDDEN"
        elif status_code == 404:
            error_code = "NOT_FOUND"
        elif status_code == 422:
            error_code = "UNPROCESSABLE_ENTITY"
        elif status_code == 429:
            error_code = "RATE_LIMIT_EXCEEDED"
        elif status_code == 500:
            error_code = "INTERNAL_SERVER_ERROR"
        elif status_code == 503:
            error_code = "SERVICE_UNAVAILABLE"
    else:
        message = str(detail_or_msg)

    if not isinstance(message, str):
        message = str(message)

    return {
        "error": str(error_code),
        "message": message,
        "detail": detail_field,
        "status_code": status_code,
        "timestamp": now_iso,
        "path": path,
        "details": details,
        "solution": solution,
    }
