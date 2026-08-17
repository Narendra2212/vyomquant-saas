"""
DB-CRITICAL-001 Regression Test: SQL Injection Prevention

Tests that user_id validation prevents SQL injection attacks in QuestDB queries.

This test verifies the fix for the critical SQL injection vulnerability.
"""

import pytest
import re
from backend_app.routers.portfolio import _safe_uid as portfolio_safe_uid
from backend_app.backend.dashboard_aggregation_service import DashboardAggregationService


def test_safe_uid_valid_uuid():
    """
    DB-CRITICAL-001: Verify that valid UUIDs pass validation.
    """
    valid_uids = [
        "123e4567-e89b-12d3-a456-426614174000",
        "abc123-def-456-ghi-789012345678",
        "user_123",
        "test-user-2024",
        "valid_id_12345"
    ]
    
    for uid in valid_uids:
        result = portfolio_safe_uid(uid)
        assert result == uid, f"Valid UID {uid} should pass validation"
        print(f"✓ Valid UID passed: {uid}")


def test_safe_uid_sql_injection_prevention():
    """
    DB-CRITICAL-001: Verify that SQL injection attempts are blocked.
    """
    injection_attempts = [
        "'; DROP TABLE users; --",
        "1' OR '1'='1",
        "admin'--",
        "user' UNION SELECT * FROM passwords--",
        "'; EXEC xp_cmdshell('dir'); --",
        "1'; SELECT * FROM sensitive_data--",
        "user' OR 1=1#",
        "admin'/*comment*/OR/*comment*/1=1",
        "'; INSERT INTO users VALUES ('hacker', 'password'); --",
        "user' OR '1'='1'--"
    ]
    
    for injection in injection_attempts:
        with pytest.raises(ValueError, match="Unsafe user_id"):
            portfolio_safe_uid(injection)
        print(f"✓ SQL injection blocked: {injection}")


def test_safe_uid_length_validation():
    """
    DB-CRITICAL-001: Verify that overly long UIDs are rejected.
    """
    long_uid = "a" * 129  # Exceeds 128 character limit
    
    with pytest.raises(ValueError, match="exceeds maximum length"):
        portfolio_safe_uid(long_uid)
    
    print("✓ Length validation blocks overly long UIDs")


def test_safe_uid_special_characters():
    """
    DB-CRITICAL-001: Verify that special characters are rejected.
    """
    invalid_uids = [
        "user@example.com",  # @ symbol
        "user#123",         # # symbol
        "user$hack",        # $ symbol
        "user%20name",      # % symbol
        "user&admin",       # & symbol
        "user|pipe",        # | symbol
        "user\\backslash",  # backslash
        "user/forward",     # forward slash
        "user<angle>",      # angle brackets
        "user[bracket]",    # brackets
        "user{brace}",      # braces
        "user`backtick`",   # backtick
        "user\"quote\"",    # double quote
        "user space",       # space
        "user\ttab",        # tab
        "user\nnewline",    # newline
    ]
    
    for uid in invalid_uids:
        with pytest.raises(ValueError, match="Unsafe user_id"):
            portfolio_safe_uid(uid)
        print(f"✓ Special character blocked: {uid}")


def test_safe_uid_dangerous_keywords():
    """
    DB-CRITICAL-001: Verify that SQL keywords are blocked.
    """
    keyword_attempts = [
        "userSELECT",
        "userINSERT",
        "userUPDATE",
        "userDELETE",
        "userDROP",
        "userEXEC",
        "userUNION",
        "userWHERE",
        "userJOIN",
        "userCREATE"
    ]
    
    for uid in keyword_attempts:
        with pytest.raises(ValueError, match="potentially dangerous pattern"):
            portfolio_safe_uid(uid)
        print(f"✓ SQL keyword blocked: {uid}")


def test_dashboard_safe_uid_validation():
    """
    DB-CRITICAL-001: Verify DashboardAggregationService also has enhanced validation.
    """
    service = DashboardAggregationService()
    
    # Test valid UID
    valid_uid = "123e4567-e89b-12d3-a456-426614174000"
    result = service._safe_uid(valid_uid)
    assert result == valid_uid
    
    # Test injection attempt
    injection = "'; DROP TABLE users; --"
    with pytest.raises(ValueError, match="Unsafe user_id"):
        service._safe_uid(injection)
    
    print("✓ DashboardAggregationService _safe_uid validation works correctly")


def test_safe_uid_case_sensitivity():
    """
    DB-CRITICAL-001: Verify that dangerous patterns are blocked regardless of case.
    """
    case_variations = [
        "userSELECT",
        "userselect",
        "userSelect",
        "userSeLeCt",
        "userUNION",
        "userunion",
        "userUnion"
    ]
    
    for uid in case_variations:
        with pytest.raises(ValueError, match="potentially dangerous pattern"):
            portfolio_safe_uid(uid)
        print(f"✓ Case-insensitive pattern blocking: {uid}")


def test_safe_uid_unicode_validation():
    """
    DB-CRITICAL-001: Verify that unicode characters are properly handled.
    """
    # Valid unicode (within allowed pattern)
    valid_unicode = "user_αβγ"  # Greek letters should fail pattern match
    
    with pytest.raises(ValueError, match="Unsafe user_id"):
        portfolio_safe_uid(valid_unicode)
    
    print("✓ Unicode characters outside allowed pattern are rejected")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
