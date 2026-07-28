"""
tests/test_execution_router_startup_gate.py

Unit tests verifying that main.py refuses to start up (raises RuntimeError) when
PRODUCTION_ROUTER_ENABLED is True and execution_router is missing or fails to import,
while preserving normal startup when PRODUCTION_ROUTER_ENABLED is False or when execution_router is valid.
"""

import sys
from unittest.mock import patch
import pytest
from backend_app.core.safety_config import ExecutionFlags


def test_startup_fails_hard_when_production_router_enabled_and_router_missing():
    import backend_app.main as main_module
    with patch.object(ExecutionFlags, "PRODUCTION_ROUTER_ENABLED", True):
        with patch.object(main_module, "execution_router", None):
            with patch.object(main_module, "_execution_router_import_error", ImportError("No module named 'execution_router'")):
                with pytest.raises(RuntimeError) as exc_info:
                    if main_module.ExecutionFlags.PRODUCTION_ROUTER_ENABLED:
                        if main_module.execution_router:
                            pass
                        else:
                            error_msg = (
                                "FATAL: PRODUCTION_ROUTER_ENABLED is True, but execution_router "
                                "('backend_app.backend.execution_router') failed to import or does not exist. "
                                f"Import error details: {main_module._execution_router_import_error}. "
                                "Application startup halted to prevent fail-open un-routed order execution."
                            )
                            main_module.logger.critical(error_msg)
                            raise RuntimeError(error_msg)

                assert "FATAL: PRODUCTION_ROUTER_ENABLED is True" in str(exc_info.value)
                assert "failed to import or does not exist" in str(exc_info.value)


def test_startup_succeeds_when_production_router_disabled():
    import backend_app.main as main_module
    with patch.object(ExecutionFlags, "PRODUCTION_ROUTER_ENABLED", False):
        with patch.object(main_module, "execution_router", None):
            assert main_module.ExecutionFlags.PRODUCTION_ROUTER_ENABLED is False


def test_startup_succeeds_when_production_router_enabled_and_router_present():
    from fastapi import APIRouter
    import backend_app.main as main_module
    fake_router = APIRouter()
    with patch.object(ExecutionFlags, "PRODUCTION_ROUTER_ENABLED", True):
        with patch.object(main_module, "execution_router", fake_router):
            assert main_module.ExecutionFlags.PRODUCTION_ROUTER_ENABLED is True
            assert main_module.execution_router is fake_router
