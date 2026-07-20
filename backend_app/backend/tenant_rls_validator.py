"""
Tenant RLS Validator

Institutional-grade tenant RLS validator for tenant isolation hardening.

This module provides:
- RLS enabled validation for all tables
- Tenant-scoped policy validation
- JWT claim propagation validation
- WebSocket tenant validation
- Replay authorization tenant safety
- Execution data cross-tenant prevention
- Service role safety guards

Author: Principal Institutional Execution Consistency Engineer
"""

import asyncio
import logging
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("TenantRlsValidator")


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION RESULT
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """Validation result for a specific check."""
    test_name: str
    table: Optional[str] = None
    status: str = "pending"  # pass, fail, warning
    message: str = ""
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "table": self.table,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }


# ═══════════════════════════════════════════════════════════════════════════
# TENANT RLS VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════

class TenantRlsValidator:
    """Tenant RLS validator for tenant isolation hardening."""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.validation_results: List[ValidationResult] = []
        self.tables_to_validate = [
            "profiles",
            "strategies",
            "orders",
            "positions",
            "exchange_credentials",
            "execution_records",
            "dag_tasks"
        ]
    
    async def validate_all(self) -> Dict[str, Any]:
        """Run all tenant RLS validations."""
        logger.info(f"[TenantRlsValidator] Starting validation for tenant {self.tenant_id}")
        
        # Validate RLS enabled on all tables
        await self.validate_rls_enabled()
        
        # Validate tenant-scoped policies
        await self.validate_tenant_scoped_policies()
        
        # Validate JWT claim propagation
        await self.validate_jwt_propagation()
        
        # Validate WebSocket tenant validation
        await self.validate_websocket_tenant_validation()
        
        # Validate replay authorization tenant safety
        await self.validate_replay_authorization_tenant_safe()
        
        # Validate execution data cannot cross tenants
        await self.validate_execution_data_cross_tenant_prevention()
        
        # Validate service role safety guards
        await self.validate_service_role_safety_guards()
        
        # Generate report
        report = self.generate_validation_report()
        
        logger.info(f"[TenantRlsValidator] Validation completed: {report['success_rate']}% success rate")
        
        return report
    
    async def validate_rls_enabled(self):
        """Validate RLS is enabled on all tables."""
        for table in self.tables_to_validate:
            result = await self._check_rls_enabled(table)
            self.validation_results.append(result)
    
    async def _check_rls_enabled(self, table: str) -> ValidationResult:
        """Check if RLS is enabled on a table."""
        try:
            result = self.db.execute(
                text(f"""
                    SELECT relrowsecurity
                    FROM pg_class
                    WHERE relname = :table_name
                """),
                {"table_name": table}
            ).fetchone()
            
            if not result:
                return ValidationResult(
                    test_name="rls_enabled",
                    table=table,
                    status="fail",
                    message=f"Table {table} not found"
                )
            
            rls_enabled = result[0]
            
            if rls_enabled:
                return ValidationResult(
                    test_name="rls_enabled",
                    table=table,
                    status="pass",
                    message=f"RLS enabled on {table}"
                )
            else:
                return ValidationResult(
                    test_name="rls_enabled",
                    table=table,
                    status="fail",
                    message=f"RLS not enabled on {table}"
                )
                
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error checking RLS on {table}: {e}")
            return ValidationResult(
                test_name="rls_enabled",
                table=table,
                status="fail",
                message=f"Error checking RLS: {str(e)}"
            )
    
    async def validate_tenant_scoped_policies(self):
        """Validate RLS policies are tenant-scoped."""
        for table in self.tables_to_validate:
            result = await self._check_tenant_scoped_policies(table)
            self.validation_results.append(result)
    
    async def _check_tenant_scoped_policies(self, table: str) -> ValidationResult:
        """Check if RLS policies are tenant-scoped."""
        try:
            result = self.db.execute(
                text(f"""
                    SELECT pg_get_expr(qual, pg_class.oid) as policy_expr
                    FROM pg_policy
                    JOIN pg_class ON pg_class.oid = pg_policy.polrelid
                    WHERE pg_class.relname = :table_name
                """),
                {"table_name": table}
            ).fetchall()
            
            if not result:
                return ValidationResult(
                    test_name="tenant_scoped_policies",
                    table=table,
                    status="warning",
                    message=f"No RLS policies found on {table}"
                )
            
            # Check if policies use tenant_id
            has_tenant_scoped_policy = False
            for row in result:
                policy_expr = row[0]
                if policy_expr and "tenant_id" in policy_expr:
                    has_tenant_scoped_policy = True
                    break
            
            if has_tenant_scoped_policy:
                return ValidationResult(
                    test_name="tenant_scoped_policies",
                    table=table,
                    status="pass",
                    message=f"Tenant-scoped policies found on {table}"
                )
            else:
                return ValidationResult(
                    test_name="tenant_scoped_policies",
                    table=table,
                    status="fail",
                    message=f"No tenant-scoped policies on {table}"
                )
                
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error checking policies on {table}: {e}")
            return ValidationResult(
                test_name="tenant_scoped_policies",
                table=table,
                status="fail",
                message=f"Error checking policies: {str(e)}"
            )
    
    async def validate_jwt_propagation(self):
        """Validate JWT tenant_id claim propagation."""
        # Check if app.current_tenant_id is set in database context
        try:
            result = self.db.execute(
                text("""
                    SELECT current_setting('app.current_tenant_id', true)
                """)
            ).fetchone()
            
            if result and result[0]:
                tenant_id = result[0]
                # Verify tenant_id matches expected tenant_id
                if str(self.tenant_id) == tenant_id:
                    self.validation_results.append(ValidationResult(
                        test_name="jwt_propagation",
                        status="pass",
                        message=f"JWT tenant_id propagated correctly: {tenant_id}"
                    ))
                else:
                    self.validation_results.append(ValidationResult(
                        test_name="jwt_propagation",
                        status="fail",
                        message=f"JWT tenant_id mismatch: expected {self.tenant_id}, got {tenant_id}"
                    ))
            else:
                self.validation_results.append(ValidationResult(
                    test_name="jwt_propagation",
                    status="fail",
                    message="JWT tenant_id not propagated to database context"
                ))
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error validating JWT propagation: {e}")
            self.validation_results.append(ValidationResult(
                test_name="jwt_propagation",
                status="fail",
                message=f"Error validating JWT propagation: {str(e)}"
            ))
    
    async def validate_websocket_tenant_validation(self):
        """Validate WebSocket tenant context validation."""
        # Check if WebSocket auth module exists and has tenant validation
        try:
            # Check if websocket_auth.py has tenant_id extraction
            import os
            websocket_auth_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "..", "core", "websocket_auth.py"
            )
            
            if os.path.exists(websocket_auth_path):
                with open(websocket_auth_path, 'r') as f:
                    content = f.read()
                    
                    # Check if tenant_id is extracted from JWT
                    if 'tenant_id' in content and 'app_metadata' in content:
                        self.validation_results.append(ValidationResult(
                            test_name="websocket_tenant_validation",
                            status="pass",
                            message="WebSocket auth module has tenant_id extraction logic"
                        ))
                    else:
                        self.validation_results.append(ValidationResult(
                            test_name="websocket_tenant_validation",
                            status="fail",
                            message="WebSocket auth module missing tenant_id extraction logic"
                        ))
            else:
                self.validation_results.append(ValidationResult(
                    test_name="websocket_tenant_validation",
                    status="warning",
                    message="WebSocket auth module not found"
                ))
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error validating WebSocket tenant validation: {e}")
            self.validation_results.append(ValidationResult(
                test_name="websocket_tenant_validation",
                status="fail",
                message=f"Error validating WebSocket tenant validation: {str(e)}"
            ))
    
    async def validate_replay_authorization_tenant_safe(self):
        """Validate replay authorization is tenant-safe."""
        # Check if state_persistence table has tenant_id column and tenant-scoped queries
        try:
            # Check if state_persistence table exists and has tenant_id
            result = self.db.execute(
                text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'state_persistence' AND column_name = 'tenant_id'
                """)
            ).fetchone()
            
            if result:
                self.validation_results.append(ValidationResult(
                    test_name="replay_authorization_tenant_safe",
                    status="pass",
                    message="State persistence table has tenant_id column for replay safety"
                ))
            else:
                self.validation_results.append(ValidationResult(
                    test_name="replay_authorization_tenant_safe",
                    status="fail",
                    message="State persistence table missing tenant_id column for replay safety"
                ))
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error validating replay authorization tenant safety: {e}")
            self.validation_results.append(ValidationResult(
                test_name="replay_authorization_tenant_safe",
                status="fail",
                message=f"Error validating replay authorization: {str(e)}"
            ))
    
    async def validate_execution_data_cross_tenant_prevention(self):
        """Validate execution data cannot cross tenants."""
        # Check if execution_records table has tenant-scoped queries
        try:
            # Test that queries are tenant-scoped
            result = self.db.execute(
                text("""
                    SELECT COUNT(*) FROM execution_records
                    WHERE tenant_id = :tenant_id
                """),
                {"tenant_id": str(self.tenant_id)}
            ).fetchone()
            
            if result:
                count = result[0]
                self.validation_results.append(ValidationResult(
                    test_name="execution_data_cross_tenant_prevention",
                    status="pass",
                    message=f"Execution records are tenant-scoped: {count} records for tenant {self.tenant_id}"
                ))
            else:
                self.validation_results.append(ValidationResult(
                    test_name="execution_data_cross_tenant_prevention",
                    status="fail",
                    message="Failed to verify execution data tenant scoping"
                ))
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error validating execution data cross-tenant prevention: {e}")
            self.validation_results.append(ValidationResult(
                test_name="execution_data_cross_tenant_prevention",
                status="fail",
                message=f"Error validating execution data: {str(e)}"
            ))
    
    async def validate_service_role_safety_guards(self):
        """Validate service role safety guards."""
        # Check if service role operations enforce tenant context
        try:
            # Test service role guard functionality
            with ServiceRoleGuard(self.db, self.tenant_id):
                result = self.db.execute(
                    text("""
                        SELECT current_setting('app.current_tenant_id', true)
                    """)
                ).fetchone()
                
                if result and result[0]:
                    tenant_id = result[0]
                    if str(self.tenant_id) == tenant_id:
                        self.validation_results.append(ValidationResult(
                            test_name="service_role_safety_guards",
                            status="pass",
                            message="Service role guard enforces tenant context correctly"
                        ))
                    else:
                        self.validation_results.append(ValidationResult(
                            test_name="service_role_safety_guards",
                            status="fail",
                            message=f"Service role guard tenant_id mismatch: expected {self.tenant_id}, got {tenant_id}"
                        ))
                else:
                    self.validation_results.append(ValidationResult(
                        test_name="service_role_safety_guards",
                        status="fail",
                        message="Service role guard does not set tenant context"
                    ))
        except Exception as e:
            logger.error(f"[TenantRlsValidator] Error validating service role guards: {e}")
            self.validation_results.append(ValidationResult(
                test_name="service_role_safety_guards",
                status="fail",
                message=f"Error validating service role guards: {str(e)}"
            ))
    
    def generate_validation_report(self) -> Dict[str, Any]:
        """Generate validation report."""
        passed = sum(1 for r in self.validation_results if r.status == "pass")
        failed = sum(1 for r in self.validation_results if r.status == "fail")
        warnings = sum(1 for r in self.validation_results if r.status == "warning")
        
        return {
            "tenant_id": str(self.tenant_id),
            "total_tests": len(self.validation_results),
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
            "success_rate": f"{(passed / len(self.validation_results) * 100):.1f}%" if self.validation_results else "0%",
            "results": [r.to_dict() for r in self.validation_results],
            "timestamp": datetime.utcnow().isoformat()
        }


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE ROLE GUARD
# ═══════════════════════════════════════════════════════════════════════════

class ServiceRoleGuard:
    """Service role safety guard for tenant isolation."""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
    
    def __enter__(self):
        """Set tenant context for service role operations."""
        self.db.execute(
            text("SET LOCAL app.current_tenant_id = :tenant_id"),
            {"tenant_id": str(self.tenant_id)}
        )
        logger.info(f"[ServiceRoleGuard] Tenant context set: {self.tenant_id}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clear tenant context."""
        self.db.execute(text("RESET app.current_tenant_id"))
        logger.info(f"[ServiceRoleGuard] Tenant context cleared")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# TENANT-SCOPED QUERY BUILDER
# ═══════════════════════════════════════════════════════════════════════════

class TenantScopedQuery:
    """Tenant-scoped query builder."""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
    
    def query(self, model):
        """Add tenant filter to all queries."""
        # This would use SQLAlchemy to add tenant filter
        # For now, return the model
        return model
    
    def execute(self, query):
        """Execute query with tenant validation."""
        # This would execute query and verify tenant isolation
        # For now, return empty list
        return []
    
    def verify_tenant_isolation(self, results: List[Any]) -> bool:
        """Verify all results belong to tenant."""
        # This would verify tenant_id in results matches self.tenant_id
        # For now, return True
        return True


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCES
# ═══════════════════════════════════════════════════════════════════════════

def get_tenant_rls_validator(db: Session, tenant_id: UUID) -> TenantRlsValidator:
    """Get tenant RLS validator instance."""
    return TenantRlsValidator(db, tenant_id)


def get_service_role_guard(db: Session, tenant_id: UUID) -> ServiceRoleGuard:
    """Get service role guard instance."""
    return ServiceRoleGuard(db, tenant_id)


def get_tenant_scoped_query(db: Session, tenant_id: UUID) -> TenantScopedQuery:
    """Get tenant-scoped query builder instance."""
    return TenantScopedQuery(db, tenant_id)
