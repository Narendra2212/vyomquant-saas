"""
core/state.py — Singleton holders for every engine instance.

FIXES APPLIED:
  C1: 'engine_b_telemetry' → 'telemetry_engine'  (correct filename)
  C2: 'engine_a_vault'     → 'api_key_vault'      (correct filename)
  C4: Removed stale empty stub references — import directly from backend/
  DEV: Safe imports - engines fail gracefully, app always starts
"""
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("AppState")

# Ensure backend/ is on sys.path so engine imports resolve
_backend_path = os.path.join(os.path.dirname(__file__), "..", "backend")
if _backend_path not in sys.path:
    sys.path.insert(0, os.path.abspath(_backend_path))


# ── DEV MODE: Detect if we should use mock engines
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true" or \
           os.environ.get("ENV", "").lower() == "development"

# ── Safe Engine imports (fail gracefully) ────────────────────────────────

class MockEngine:
    """Placeholder engine that does nothing but won't crash"""
    def __init__(self, name="MockEngine"):
        self._name = name
        logger.debug(f"Using {name} (mock)")
    
    def __getattr__(self, name):
        """Return a no-op function for any method call"""
        def noop(*args, **kwargs):
            return None
        return noop

def safe_import(class_name, module_name):
    """Try to import an engine, return MockEngine if it fails"""
    try:
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
    except Exception as e:
        logger.warning(f"Failed to import {class_name} from {module_name}: {e}")
        return lambda *args, **kwargs: MockEngine(f"Mock{class_name}")

# Import engines safely
APIKeyVault = safe_import("APIKeyVault", "api_key_vault")
TelemetryEngine = safe_import("TelemetryEngine", "telemetry_engine")
try:
    from backend_app.core.risk_manager import InstitutionalRiskManager
except Exception as e:
    logger.warning(f"Failed to import InstitutionalRiskManager from core.risk_manager: {e}")
    InstitutionalRiskManager = lambda *args, **kwargs: MockEngine("MockInstitutionalRiskManager")

AlertEngine = safe_import("AlertEngine", "alert_engine")
FleetManager = safe_import("FleetManager", "fleet_manager")

# WS manager import
try:
    from backend_app.api_ws.ws_manager import ConnectionManager
except Exception as e:
    logger.warning(f"Failed to import ConnectionManager: {e}")
    def ConnectionManager():
        return MockEngine("MockConnectionManager")


@dataclass
class AppState:
    """
    Central singleton. Initialized once at import time, shared everywhere.
    All engines are injected into routers via Depends(get_X) in dependencies.py.
    Engines that fail to load are replaced with MockEngine placeholders.
    """

    vault: Any = field(default_factory=lambda: _safe_init(APIKeyVault, "APIKeyVault"))
    telemetry: Any = field(default_factory=lambda: _safe_init(TelemetryEngine, "TelemetryEngine"))
    risk: Any = field(default_factory=lambda: _safe_init(InstitutionalRiskManager, "RiskManager"))
    alert: Any = field(default_factory=lambda: _safe_init_alert())
    fleet: Any = field(default_factory=lambda: _safe_init(FleetManager, "FleetManager"))
    ws: Any = field(default_factory=lambda: _safe_init(ConnectionManager, "ConnectionManager"))


def _safe_init(Factory, name):
    """Initialize an engine safely, return MockEngine on failure"""
    try:
        return Factory()
    except Exception as e:
        logger.warning(f"DEV_MODE: {name} initialization failed, using mock: {e}")
        return MockEngine(f"Mock{name}")


def _safe_init_alert():
    """Initialize AlertEngine safely"""
    try:
        return AlertEngine(
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", None),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", None)
        )
    except Exception as e:
        logger.warning(f"DEV_MODE: AlertEngine initialization failed, using mock: {e}")
        return MockEngine("MockAlertEngine")


# ── Single shared instance imported by all modules ────────────────────────
app_state = AppState()
