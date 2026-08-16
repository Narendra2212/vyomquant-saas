"""
Sandbox Execution Runner - Exchange Validation

This module runs sandbox execution validation for exchange integration testing
in the strict algo trading platform.

Author: Principal Institutional Validation Engineer
"""

import ast
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger("sandbox_execution_runner")


class SandboxExecutionRunner:
    """
    Sandbox execution runner for exchange validation.
    
    Runs sandbox execution validation for exchange integration testing.
    """
    
    def __init__(self):
        """Initialize sandbox execution runner."""
        self.redis = redis_manager
        self.sandbox_mode = True
    
    async def run_sandbox_execution(
        self,
        order_data: Dict[str, Any],
        exchange_client: Any,
        tenant_id: str
    ) -> Dict[str, Any]:
        """
        Run sandbox execution validation.
        
        Args:
            order_data: Order data
            exchange_client: Exchange client
            tenant_id: Tenant ID
            
        Returns:
            Sandbox execution result
        """
        logger.info(f"Running sandbox execution for tenant: {tenant_id}")
        
        try:
            # Validate order data
            validation_result = await self._validate_order_data(order_data)
            
            if not validation_result["valid"]:
                return {
                    "success": False,
                    "error": "order_validation_failed",
                    "details": validation_result["errors"]
                }
            
            # Simulate sandbox execution
            sandbox_result = await self._simulate_sandbox_execution(order_data, exchange_client)
            
            # Validate sandbox result
            result_validation = await self._validate_sandbox_result(sandbox_result)
            
            # Store sandbox execution record
            await self._store_sandbox_record(tenant_id, order_data, sandbox_result)
            
            return {
                "success": True,
                "sandbox_result": sandbox_result,
                "validation": result_validation,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            logger.error(f"Sandbox execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    async def _validate_order_data(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate order data."""
        errors = []
        
        required_fields = ["symbol", "side", "quantity", "price", "order_type"]
        
        for field in required_fields:
            if field not in order_data:
                errors.append(f"Missing required field: {field}")
        
        if order_data.get("quantity", 0) <= 0:
            errors.append("Quantity must be positive")
        
        if order_data.get("price", 0) <= 0:
            errors.append("Price must be positive")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    
    async def _simulate_sandbox_execution(self, order_data: Dict[str, Any], exchange_client: Any) -> Dict[str, Any]:
        """Simulate sandbox execution."""
        # In sandbox mode, simulate execution without actual exchange call
        return {
            "order_id": f"sandbox_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "status": "filled",
            "filled_quantity": order_data.get("quantity", 0),
            "filled_price": order_data.get("price", 0),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    async def _validate_sandbox_result(self, sandbox_result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate sandbox result."""
        errors = []
        
        if "order_id" not in sandbox_result:
            errors.append("Missing order_id in sandbox result")
        
        if "status" not in sandbox_result:
            errors.append("Missing status in sandbox result")
        
        if sandbox_result.get("filled_quantity", 0) <= 0:
            errors.append("Filled quantity must be positive")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    
    async def _store_sandbox_record(
        self,
        tenant_id: str,
        order_data: Dict[str, Any],
        sandbox_result: Dict[str, Any]
    ) -> bool:
        """Store sandbox execution record."""
        try:
            key = f"sandbox_execution:{tenant_id}:{sandbox_result.get('order_id', '')}"
            record = {
                "order_data": order_data,
                "sandbox_result": sandbox_result,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await self.redis.setex(key, 86400, str(record))
            return True
            
        except Exception as e:
            logger.error(f"Failed to store sandbox record: {e}")
            return False
    
    async def get_sandbox_history(self, tenant_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get sandbox execution history."""
        try:
            pattern = f"sandbox_execution:{tenant_id}:*"
            keys = await self.redis.keys(pattern)
            
            history = []
            for key in keys[:limit]:
                record = await self.redis.get(key)
                if record:
                    history.append(ast.literal_eval(record))
            
            return history
            
        except Exception as e:
            logger.error(f"Failed to get sandbox history: {e}")
            return []


# Global instance
_sandbox_execution_runner: SandboxExecutionRunner = None


def get_sandbox_execution_runner() -> SandboxExecutionRunner:
    """Get or create sandbox execution runner instance."""
    global _sandbox_execution_runner
    if _sandbox_execution_runner is None:
        _sandbox_execution_runner = SandboxExecutionRunner()
    return _sandbox_execution_runner
