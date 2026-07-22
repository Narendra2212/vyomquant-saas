"""
core/quota_errors.py — Structured Quota Violation Errors.

Hard enforcement errors with structured data for monitoring and alerting.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class QuotaViolationDetails:
    """Detailed information about quota violation."""
    tenant_id: str
    resource_type: str
    limit: int
    current: int
    attempted: int
    plan: str
    timestamp: datetime
    request_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "resource_type": self.resource_type,
            "limit": self.limit,
            "current": self.current,
            "attempted": self.attempted,
            "plan": self.plan,
            "timestamp": self.timestamp.isoformat(),
            "request_id": self.request_id,
            "context": self.context,
        }


class QuotaViolationError(Exception):
    """
    Base exception for quota violations.
    
    Includes structured data for logging, monitoring, and client responses.
    """
    
    def __init__(
        self,
        message: str,
        details: QuotaViolationDetails,
        error_code: str = "QUOTA_EXCEEDED"
    ):
        super().__init__(message)
        self.details = details
        self.error_code = error_code
        self.violation_timestamp = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "error": self.error_code,
            "message": str(self),
            "details": self.details.to_dict(),
            "timestamp": self.violation_timestamp.isoformat(),
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), default=str)


class DAGQuotaExceededError(QuotaViolationError):
    """Quota exceeded for DAG operations."""
    
    def __init__(self, details: QuotaViolationDetails):
        super().__init__(
            message=f"DAG quota exceeded: {details.resource_type} "
                    f"(limit: {details.limit}, current: {details.current}, "
                    f"attempted: {details.attempted})",
            details=details,
            error_code="DAG_QUOTA_EXCEEDED"
        )


class ExecutionQuotaExceededError(QuotaViolationError):
    """Quota exceeded for execution operations."""
    
    def __init__(self, details: QuotaViolationDetails):
        super().__init__(
            message=f"Execution quota exceeded: {details.resource_type} "
                    f"(limit: {details.limit}, current: {details.current}, "
                    f"attempted: {details.attempted})",
            details=details,
            error_code="EXECUTION_QUOTA_EXCEEDED"
        )


class PortfolioQuotaExceededError(QuotaViolationError):
    """Quota exceeded for portfolio operations."""
    
    def __init__(self, details: QuotaViolationDetails):
        super().__init__(
            message=f"Portfolio quota exceeded: {details.resource_type} "
                    f"(limit: {details.limit}, current: {details.current}, "
                    f"attempted: {details.attempted})",
            details=details,
            error_code="PORTFOLIO_QUOTA_EXCEEDED"
        )


class RateLimitExceededError(QuotaViolationError):
    """Rate limit exceeded."""
    
    def __init__(
        self,
        details: QuotaViolationDetails,
        window: str,
        retry_after: int
    ):
        super().__init__(
            message=f"Rate limit exceeded: {details.resource_type} "
                    f"(limit: {details.limit} per {window}, current: {details.current})",
            details=details,
            error_code="RATE_LIMIT_EXCEEDED"
        )
        self.window = window
        self.retry_after = retry_after
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["details"]["window"] = self.window
        base["details"]["retry_after_seconds"] = self.retry_after
        return base


class CapitalQuotaExceededError(QuotaViolationError):
    """Capital allocation quota exceeded."""
    
    def __init__(
        self,
        details: QuotaViolationDetails,
        requested_capital: float,
        available_capital: float
    ):
        super().__init__(
            message=f"Capital quota exceeded: requested ${requested_capital:,.2f}, "
                    f"available ${available_capital:,.2f}, "
                    f"plan limit ${details.limit:,.2f}",
            details=details,
            error_code="CAPITAL_QUOTA_EXCEEDED"
        )
        self.requested_capital = requested_capital
        self.available_capital = available_capital
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["details"]["requested_capital"] = self.requested_capital
        base["details"]["available_capital"] = self.available_capital
        return base


class ConcurrentOperationQuotaError(QuotaViolationError):
    """Too many concurrent operations."""
    
    def __init__(self, details: QuotaViolationDetails, operation_type: str):
        super().__init__(
            message=f"Concurrent operation limit exceeded: {operation_type} "
                    f"(limit: {details.limit}, active: {details.current})",
            details=details,
            error_code="CONCURRENT_OPERATION_LIMIT"
        )
        self.operation_type = operation_type


class DailyLimitExceededError(QuotaViolationError):
    """Daily operation limit exceeded."""
    
    def __init__(
        self,
        details: QuotaViolationDetails,
        limit_type: str,
        reset_time: datetime
    ):
        super().__init__(
            message=f"Daily {limit_type} limit exceeded: "
                    f"(limit: {details.limit}, used: {details.current}). "
                    f"Resets at {reset_time.isoformat()}",
            details=details,
            error_code="DAILY_LIMIT_EXCEEDED"
        )
        self.limit_type = limit_type
        self.reset_time = reset_time
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["details"]["limit_type"] = self.limit_type
        base["details"]["reset_time"] = self.reset_time.isoformat()
        return base


class StorageQuotaExceededError(QuotaViolationError):
    """Storage quota exceeded."""
    
    def __init__(
        self,
        details: QuotaViolationDetails,
        used_mb: float,
        requested_mb: float
    ):
        super().__init__(
            message=f"Storage quota exceeded: used {used_mb:.1f}MB, "
                    f"requested {requested_mb:.1f}MB, "
                    f"limit {details.limit}MB",
            details=details,
            error_code="STORAGE_QUOTA_EXCEEDED"
        )
        self.used_mb = used_mb
        self.requested_mb = requested_mb
    
    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["details"]["used_mb"] = self.used_mb
        base["details"]["requested_mb"] = self.requested_mb
        return base
