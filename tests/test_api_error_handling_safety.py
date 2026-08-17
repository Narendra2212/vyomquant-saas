"""
P1-BACKEND-CRITICAL-001 Regression Test: API Error Handling Safety

Tests that API error handling provides consistent, safe responses and does not
leak sensitive information or return incorrect status codes.

This test verifies the safety of API error handling mechanisms.
"""

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock


def test_api_error_response_standardization():
    """
    P1-BACKEND-CRITICAL-001: Verify that API error responses are standardized.
    
    This test ensures that all error responses follow the APIErrorResponse schema
    and provide consistent error information.
    """
    from backend_app.core.schemas import create_api_error_response
    
    # Test basic string error
    response = create_api_error_response(
        status_code=404,
        detail_or_msg="Resource not found",
        path="/api/test"
    )
    
    assert response["error"] == "NOT_FOUND"
    assert response["message"] == "Resource not found"
    assert response["status_code"] == 404
    assert response["path"] == "/api/test"
    assert "timestamp" in response
    
    print("✓ API error responses are standardized")


def test_validation_error_sanitization():
    """
    P1-BACKEND-CRITICAL-002: Verify that validation errors are sanitized.
    
    This test ensures that Pydantic validation errors are sanitized to remove
    non-serializable objects and prevent information leakage.
    """
    from backend_app.core.schemas import _sanitize_validation_errors
    
    # Test sanitization of error with exception in context
    errors = [
        {
            "loc": ["body", "price"],
            "msg": "value is not a valid float",
            "type": "value_error.float",
            "ctx": {"error": ValueError("test error")}
        }
    ]
    
    sanitized = _sanitize_validation_errors(errors)
    
    assert len(sanitized) == 1
    assert isinstance(sanitized[0]["ctx"]["error"], str)
    assert not isinstance(sanitized[0]["ctx"]["error"], ValueError)
    
    print("✓ Validation errors are sanitized")


def test_error_code_mapping():
    """
    P1-BACKEND-CRITICAL-003: Verify that HTTP status codes map to correct error codes.
    
    This test ensures that status codes are mapped to appropriate error codes
    for client consumption.
    """
    from backend_app.core.schemas import create_api_error_response
    
    # Test various status codes
    test_cases = [
        (400, "BAD_REQUEST"),
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (422, "UNPROCESSABLE_ENTITY"),
        (429, "RATE_LIMIT_EXCEEDED"),
        (500, "INTERNAL_SERVER_ERROR"),
        (503, "SERVICE_UNAVAILABLE"),
    ]
    
    for status_code, expected_code in test_cases:
        response = create_api_error_response(
            status_code=status_code,
            detail_or_msg="Test error"
        )
        assert response["error"] == expected_code, f"Status {status_code} should map to {expected_code}"
    
    print("✓ Error code mapping is correct")


def test_dict_error_response_handling():
    """
    P1-BACKEND-CRITICAL-004: Verify that dict error responses are handled correctly.
    
    This test ensures that rich error responses in dict format are properly
    parsed and included in the standardized response.
    """
    from backend_app.core.schemas import create_api_error_response
    
    # Test dict error with custom fields
    dict_error = {
        "error": "CUSTOM_ERROR",
        "message": "Custom error message",
        "solution": "Check your input",
        "extra_field": "extra_value"
    }
    
    response = create_api_error_response(
        status_code=400,
        detail_or_msg=dict_error,
        path="/api/test"
    )
    
    assert response["error"] == "CUSTOM_ERROR"
    assert response["message"] == "Custom error message"
    assert response["solution"] == "Check your input"
    assert response["details"]["extra_field"] == "extra_value"
    
    print("✓ Dict error responses are handled correctly")


def test_list_validation_error_handling():
    """
    P1-BACKEND-CRITICAL-005: Verify that list validation errors are handled correctly.
    
    This test ensures that Pydantic validation error lists are properly
    sanitized and included in the error response.
    """
    from backend_app.core.schemas import create_api_error_response
    
    # Test list of validation errors
    validation_errors = [
        {
            "loc": ["body", "price"],
            "msg": "value is not a valid float",
            "type": "value_error.float"
        },
        {
            "loc": ["body", "quantity"],
            "msg": "ensure this value is greater than 0",
            "type": "value_error.number.not_gt"
        }
    ]
    
    response = create_api_error_response(
        status_code=422,
        detail_or_msg=validation_errors,
        path="/api/test"
    )
    
    assert response["error"] == "VALIDATION_ERROR"
    assert response["message"] == "Input validation failed."
    assert len(response["details"]) == 2
    assert "detail" in response
    
    print("✓ List validation errors are handled correctly")


def test_timestamp_inclusion():
    """
    P1-BACKEND-CRITICAL-006: Verify that timestamps are included in error responses.
    
    This test ensures that all error responses include ISO-8601 timestamps
    for debugging and monitoring purposes.
    """
    from backend_app.core.schemas import create_api_error_response
    
    response = create_api_error_response(
        status_code=500,
        detail_or_msg="Internal error",
        path="/api/test"
    )
    
    assert "timestamp" in response
    assert isinstance(response["timestamp"], str)
    
    # Verify it's a valid ISO-8601 timestamp
    try:
        datetime.fromisoformat(response["timestamp"])
    except ValueError:
        pytest.fail("Timestamp is not in valid ISO-8601 format")
    
    print("✓ Timestamps are included in error responses")


def test_path_inclusion():
    """
    P1-BACKEND-CRITICAL-007: Verify that request paths are included in error responses.
    
    This test ensures that error responses include the request path for
    debugging and monitoring purposes.
    """
    from backend_app.core.schemas import create_api_error_response
    
    response = create_api_error_response(
        status_code=404,
        detail_or_msg="Not found",
        path="/api/orders/123"
    )
    
    assert response["path"] == "/api/orders/123"
    
    print("✓ Request paths are included in error responses")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
