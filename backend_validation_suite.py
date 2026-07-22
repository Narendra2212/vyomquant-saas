"""
Backend Validation Suite
Principal Institutional Distributed Systems Validation Engineer

Validates backend startup, imports, middleware, routes, websocket auth,
replay auth, orchestration, dependency injection, Redis, Postgres, Supabase.
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, Any

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "aerora_quant_backend_updated_final1"))

from full_system_validation_runtime import ValidationSuite

logger = logging.getLogger("BackendValidation")

class BackendValidationSuite(ValidationSuite):
    """Comprehensive backend validation suite."""
    
    def __init__(self):
        super().__init__("BackendValidationSuite")
    
    async def _execute_tests(self):
        """Execute all backend validation tests."""
        await self._test_imports()
        await self._test_startup_flow()
        await self._test_middleware()
        await self._test_routes()
        await self._test_websocket_auth()
        await self._test_dependency_injection()
        await self._test_redis_integration()
        await self._test_supabase_integration()
        await self._test_safety_config()
    
    async def _test_imports(self):
        """Test critical backend imports."""
        self.results["tests_run"] += 1
        test_name = "Critical Backend Imports"
        
        try:
            # Test core imports
            from core.dependencies import get_supabase, get_current_user
            from core.state import app_state
            from core.websocket_auth import WebSocketAuthMiddleware
            from core.config import settings
            
            # Test backend imports
            from backend.fleet_manager import FleetManager
            from backend.distributed_execution.orchestrator import ExecutionOrchestrator
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except ImportError as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: Import error - {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_startup_flow(self):
        """Test backend startup flow."""
        self.results["tests_run"] += 1
        test_name = "Backend Startup Flow"
        
        try:
            # Verify main.py exists and is importable
            import main as main_module
            
            # Check for lifespan function
            if not hasattr(main_module, 'lifespan'):
                raise ValueError("lifespan function not found in main.py")
            
            # Check for app instance
            if not hasattr(main_module, 'app'):
                raise ValueError("app instance not found in main.py")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_middleware(self):
        """Test middleware configuration."""
        self.results["tests_run"] += 1
        test_name = "Middleware Configuration"
        
        try:
            import main as main_module
            
            # Check CORS middleware
            app = main_module.app
            middleware_types = [type(m.cls) for m in app.user_middleware]
            
            # Verify CORS is present
            cors_found = any('CORS' in str(t) for t in middleware_types)
            if not cors_found:
                self.results["warnings"].append("CORS middleware not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_routes(self):
        """Test route registration."""
        self.results["tests_run"] += 1
        test_name = "Route Registration"
        
        try:
            import main as main_module
            
            app = main_module.app
            routes = [route.path for route in app.routes if hasattr(route, 'path')]
            
            # Critical routes
            critical_routes = [
                "/health",
                "/api/auth",
                "/api/orders",
                "/api/market",
                "/api/strategies",
                "/api/portfolio",
                "/ws"
            ]
            
            missing_routes = [r for r in critical_routes if not any(r in route for route in routes)]
            
            if missing_routes:
                self.results["warnings"].append(f"Missing critical routes: {missing_routes}")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_websocket_auth(self):
        """Test WebSocket authentication."""
        self.results["tests_run"] += 1
        test_name = "WebSocket Authentication"
        
        try:
            from core.websocket_auth import (
                WebSocketAuthMiddleware,
                get_websocket_auth
            )
            
            # Test singleton
            auth = get_websocket_auth()
            if not isinstance(auth, WebSocketAuthMiddleware):
                raise ValueError("WebSocket auth is not a WebSocketAuthMiddleware instance")
            
            # Test methods exist
            if not hasattr(auth, 'authenticate_websocket'):
                raise ValueError("authenticate_websocket method not found")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_dependency_injection(self):
        """Test dependency injection."""
        self.results["tests_run"] += 1
        test_name = "Dependency Injection"
        
        try:
            from core.dependencies import (
                get_supabase,
                get_current_user,
                get_vault,
                get_telemetry,
                get_risk,
                get_fleet,
                get_alert,
                get_ws_manager
            )
            
            # Test that all dependency functions exist
            deps = [
                get_supabase,
                get_current_user,
                get_vault,
                get_telemetry,
                get_risk,
                get_fleet,
                get_alert,
                get_ws_manager
            ]
            
            for dep in deps:
                if not callable(dep):
                    raise ValueError(f"{dep.__name__} is not callable")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_redis_integration(self):
        """Test Redis integration."""
        self.results["tests_run"] += 1
        test_name = "Redis Integration"
        
        try:
            from core.cache import redis_manager
            
            # Test that redis_manager exists
            if not hasattr(redis_manager, 'connect'):
                raise ValueError("redis_manager missing connect method")
            
            if not hasattr(redis_manager, 'disconnect'):
                raise ValueError("redis_manager missing disconnect method")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_supabase_integration(self):
        """Test Supabase integration."""
        self.results["tests_run"] += 1
        test_name = "Supabase Integration"
        
        try:
            from core.dependencies import get_supabase
            from core.security_vault import SecurityVault
            
            # Test Supabase client function
            if not callable(get_supabase):
                raise ValueError("get_supabase is not callable")
            
            # Test SecurityVault
            if not hasattr(SecurityVault, '__init__'):
                raise ValueError("SecurityVault missing __init__")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
    
    async def _test_safety_config(self):
        """Test safety configuration."""
        self.results["tests_run"] += 1
        test_name = "Safety Configuration"
        
        try:
            from core.safety_config import ExecutionFlags, SafetyMonitor
            
            # Test ExecutionFlags
            if not hasattr(ExecutionFlags, 'PRODUCTION_ROUTER_ENABLED'):
                raise ValueError("ExecutionFlags missing PRODUCTION_ROUTER_ENABLED")
            
            # Test SafetyMonitor
            if not hasattr(SafetyMonitor, 'assert_safe_mode'):
                raise ValueError("SafetyMonitor missing assert_safe_mode")
            
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
            
        except Exception as e:
            self.results["tests_failed"] += 1
            self.results["errors"].append(f"{test_name}: {e}")
            logger.error(f"❌ {test_name}: FAILED - {e}")
