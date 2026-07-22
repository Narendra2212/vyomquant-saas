"""

  ALGO22  FASTAPI MASTER SERVER                                          
  Wires every backend engine into a production-grade async HTTP + WS API  
                                                                          
   SYSTEM FREEZE PROTOCOL ACTIVE                                          
  All order execution is BLOCKED pending safety fixes                     
  See: core/safety_config.py for details                                  
                                                                          
  ENGINES:                                                                
  A  SecurityVault     AES-256 key vault + Supabase auth                 
  B  TelemetryEngine   QuestDB time-series R/W                           
  C  RiskManager       Institutional circuit breaker                     
  D  ConnectionEngine  CCXT.pro exchange connector                       
  E  DataEngine        WebSocket market + private data streams           
  F  OrderEngine       CCXT order execution + retry                      
  G  DataProcessor     Numba tickcandle builder                         
  H  StrategyEngine    Blueprint evaluator (safe eval)                   
  I  BacktestEngine    VectorBT + ML backtest runner                     
  J  XGBoostBlock      ML model training                                 
  K  BotRunner         Master execution orchestrator                     
  L  FleetManager      Multi-user bot lifecycle manager                  
  M  AlertEngine       Discord/Telegram webhook dispatcher               
                                                                          
  RUN:  uvicorn main:app --host 0.0.0.0 --port 8000 --reload              

"""


import logging
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from typing import Optional

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
#  Global exception handler 
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from backend_app.api_ws.ws_routes import ws_router
# Parallel DAG engine
from backend_app.backend.dag_engine_parallel import \
    router as parallel_dag_router
# Event-driven DAG engine
from backend_app.backend.dag_event_loop import router as event_dag_router
# Risk-integrated DAG engine
from backend_app.backend.dag_risk_integration import router as risk_dag_router
# Market data validation
from backend_app.backend.market_data_validation import \
    router as validation_router
from backend_app.backend.observability.sentry_config import initialize_sentry
from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
from backend_app.backend.pnl_engine import PnLEngine
# Portfolio management system
from backend_app.backend.portfolio_management import \
    router as portfolio_mgmt_router
from backend_app.backend.startup_recovery import run_startup_recovery
# State persistence system
from backend_app.backend.state_persistence import router as persistence_router
from backend_app.backend.telemetry_engine import check_questdb
from backend_app.backend.ws_event_stream import ws_streamer
from backend_app.core.cache import redis_manager
#  Import config for startup validation 
from backend_app.core.config import print_config_status, settings
# ── Runtime Services ────────────────────────────────────────────────
from backend_app.core.consistency_checker import (PositionConsistencyChecker,
                                                  get_consistency_checker)
# Database imports — ALL SQLAlchemy models must be imported here so that
# Base.metadata.create_all() registers their tables at startup.
from backend_app.core.database import Base, SessionLocal, engine
# Safety feature flags
from backend_app.core.feature_flags import ExecutionContext
from backend_app.core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS_TOTAL
from backend_app.core.models.execution_record import \
    ExecutionRecordModel  # noqa: F401
# Critical safety tables — must be registered before create_all()
from backend_app.core.models.reconciliation import \
    ReconciliationMismatchModel  # noqa: F401
from backend_app.core.position_model import PositionModel  # noqa: F401
from backend_app.core.rate_limit import limiter
from backend_app.core.reconciliation_scheduler import (
    start_reconciliation_scheduler, stop_reconciliation_scheduler)
#  SYSTEM FREEZE: Import safety config FIRST to block all execution
# This must be imported before any engine that could execute trades
from backend_app.core.safety_config import ExecutionFlags, SafetyMonitor
from backend_app.core.safety_monitor import log_blocked_execution
from backend_app.core.security_vault import SecurityVault
from backend_app.core.state import app_state
#  Router imports 
from backend_app.routers import (admin, analytics, auth, billing, exchange,
                                 library, market, metrics, orders, portfolio,
                                 risk, security, strategies, support, user)
# DAG task queue
from backend_app.routers.dag_tasks import router as dag_tasks_router

# Initialize Telemetry
sentry_dsn = os.environ.get("SENTRY_DSN")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )
    print("Telemetry: Sentry initialized.")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' ws: wss: http: https:; "
            "frame-ancestors 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.time() - start_time
            HTTP_REQUESTS_TOTAL.labels(
                method=request.method,
                endpoint=request.url.path,
                status_code=status_code
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                endpoint=request.url.path
            ).observe(duration)


# Production execution engine
try:
    from backend_app.backend.execution_router import router as execution_router
except ImportError:
    execution_router = None


#  Logging setup 
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s"
)
logger = logging.getLogger("Algo22")




sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))



env_mode = os.getenv("AERORA_MODE", "safe").lower()
if env_mode == "live":
    ExecutionFlags.enable_live_trading()
elif env_mode == "paper":
    ExecutionFlags.enable_paper_trading()
else:
    SafetyMonitor.assert_safe_mode()  # Crash if not explicitly valid safe state

try:
    from asgi_correlation_id import CorrelationIdMiddleware
except ImportError:
    CorrelationIdMiddleware = None

# Shared runtime service status — populated during lifespan startup, read by /health/services
_runtime_service_status: dict = {}


# ── Lifespan: boot + graceful shutdown ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Sentry early
    initialize_sentry(
        dsn=os.getenv("SENTRY_DSN"),
        environment=os.getenv("AERORA_MODE", "production"),
        release="2.4.0"
    )
    # Print configuration status at startup
    print_config_status()
    missing_settings = settings.validate_required()
    if missing_settings:
        logger.critical(f"FATAL: Missing required environment variables: {', '.join(missing_settings)}")
        raise RuntimeError(f"FATAL: Missing required environment variables: {', '.join(missing_settings)}")
    
    logger.info(" ALGO22 server starting up...")
    
    # 
    # STARTUP RECOVERY: Resume system after crash/restart
    # 
    try:
        recovery_stats = await run_startup_recovery()
        logger.info(
            f" Startup recovery complete: "
            f"recovered={recovery_stats.get('stuck_tasks_recovered', 0)} "
            f"requeued={recovery_stats.get('pending_tasks_requeued', 0)}"
        )
    except Exception as e:
        logger.warning(f" Startup recovery failed: {e}")
    
    service_status = {"redis": "unknown", "questdb": "unknown", "alert": "unknown", "fleet": "unknown", "supabase": "unknown"}
    
    # Check Supabase
    try:
        vault = SecurityVault()
        if vault.is_available():
            service_status["supabase"] = "connected"
            logger.info(" Supabase: Connected")
        else:
            service_status["supabase"] = "fallback"
            logger.warning(" Supabase: Fallback mode (dev tokens active)")
    except Exception as e:
        service_status["supabase"] = "fallback"
        logger.warning(f" Supabase: Fallback mode ({e})")
    
    # Check QuestDB
    try:
        if check_questdb():
            service_status["questdb"] = "connected"
        else:
            service_status["questdb"] = "fallback"
    except Exception as e:
        service_status["questdb"] = "fallback"
        logger.warning(f" QuestDB check failed: {e}")

    # Engine: Redis Cache
    try:
        await redis_manager.connect()
        service_status["redis"] = "connected" if redis_manager.pool else "fallback"
        logger.info(f" Redis Cache: {service_status['redis']}")
    except Exception as e:
        service_status["redis"] = "failed"
        logger.warning(f" Redis Cache connection failed: {e}")

    # Engine B: QuestDB connection pool
    try:
        await app_state.telemetry.connect()
        service_status["questdb"] = "connected"
        logger.info(" TelemetryEngine connected to QuestDB")
    except Exception as e:
        service_status["questdb"] = "failed"
        logger.warning(f" TelemetryEngine (QuestDB) connection failed: {e}")

    # Engine M: Alert Engine (Discord/Telegram)
    try:
        await app_state.alert.send_info("ALGO22 server started successfully.")
        service_status["alert"] = "armed"
        logger.info(" AlertEngine armed")
    except Exception as e:
        service_status["alert"] = "mock/fallback"
        logger.warning(f" AlertEngine not armed (using mock): {e}")

    # Engine L: FleetManager online
    try:
        max_bots = getattr(app_state.fleet, 'MAX_SYSTEM_BOTS', 'unknown')
        service_status["fleet"] = "online"
        logger.info(f" FleetManager online  capacity: {max_bots} bots")
    except Exception as e:
        service_status["fleet"] = "mock/fallback"
        logger.warning(f" FleetManager using mock: {e}")

    # ── Runtime Service: ConsistencyChecker ──────────────────────────
    _consistency_checker: Optional[PositionConsistencyChecker] = None
    try:
        _consistency_checker = get_consistency_checker()
        await _consistency_checker.start()
        service_status["consistency_checker"] = "ACTIVE"
        logger.info(" ConsistencyChecker: ACTIVE (interval=10s, kill-switch armed)")
    except Exception as e:
        service_status["consistency_checker"] = "INACTIVE"
        logger.warning(f" ConsistencyChecker: INACTIVE — {e}")

    # ── Runtime Service: OrderWatchdog ───────────────────────────────
    _order_watchdog: Optional[OrderWatchdog] = None
    _watchdog_db = None
    try:
        _watchdog_db = SessionLocal()
        _watchdog_cfg = WatchdogConfig(
            check_interval_seconds=30,
            stale_threshold_seconds=60,
            alert_threshold_seconds=300,
        )
        _order_watchdog = OrderWatchdog(_watchdog_db, _watchdog_cfg)
        await _order_watchdog.start()
        service_status["order_watchdog"] = "ACTIVE"
        logger.info(" OrderWatchdog: ACTIVE (interval=30s, stale=60s, alert=300s)")
    except Exception as e:
        service_status["order_watchdog"] = "INACTIVE"
        logger.warning(f" OrderWatchdog: INACTIVE — {e}")

    # ── Runtime Service: PnLEngine ───────────────────────────────────
    _pnl_db = None
    _pnl_engine: Optional[PnLEngine] = None
    try:
        _pnl_db = SessionLocal()
        _pnl_engine = PnLEngine(_pnl_db)
        # Warm-up: verify Decimal precision calculation is functional
        from decimal import Decimal
        _test = _pnl_engine.calculate_sharpe_ratio([Decimal("0.01"), Decimal("0.02")])
        service_status["pnl_engine"] = "ACTIVE"
        logger.info(" PnLEngine: ACTIVE (Decimal precision verified, Sharpe/Drawdown ready)")
    except Exception as e:
        service_status["pnl_engine"] = "INACTIVE"
        logger.warning(f" PnLEngine: INACTIVE — {e}")

    # ── Runtime Service: ReconciliationScheduler ─────────────────────────────
    try:
        await start_reconciliation_scheduler()
        service_status["reconciliation_scheduler"] = "ACTIVE"
        logger.info(" ReconciliationScheduler: ACTIVE (poll=30s, kill-switch armed)")
    except Exception as e:
        service_status["reconciliation_scheduler"] = "INACTIVE"
        logger.warning(f" ReconciliationScheduler: INACTIVE — {e}")

    logger.info(f" Service Status: {service_status}")

    # Start WebSocket Streamer
    try:
        await ws_streamer.start()
        logger.info(" WebSocket Streamer: ACTIVE")
    except Exception as e:
        logger.warning(f" WebSocket Streamer start failed: {e}")

    # Publish to module-level dict so /health/services can read it without request context
    _runtime_service_status.update(service_status)

    yield  #  Server is live and accepting requests here

    #  Graceful shutdown 
    logger.info(" Shutdown initiated  terminating all bots...")

    # Runtime Service: ConsistencyChecker — stop background check loop
    try:
        if _consistency_checker is not None:
            await _consistency_checker.stop()
            logger.info(" ConsistencyChecker: stopped")
    except Exception as e:
        logger.warning(f" ConsistencyChecker shutdown error: {e}")

    # Runtime Service: OrderWatchdog — cancel background monitoring task
    try:
        if _order_watchdog is not None:
            await _order_watchdog.stop()
            logger.info(" OrderWatchdog: stopped")
        if _watchdog_db is not None:
            _watchdog_db.close()
    except Exception as e:
        logger.warning(f" OrderWatchdog shutdown error: {e}")

    # Runtime Service: PnLEngine — close DB session
    try:
        if _pnl_db is not None:
            _pnl_db.close()
            logger.info(" PnLEngine: session closed")
    except Exception as e:
        logger.warning(f" PnLEngine shutdown error: {e}")

    # Runtime Service: ReconciliationScheduler — stop background poll loop
    try:
        await stop_reconciliation_scheduler()
        logger.info(" ReconciliationScheduler: stopped")
    except Exception as e:
        logger.warning(f" ReconciliationScheduler shutdown error: {e}")

    # Engine L  stop all running BotRunner tasks safely
    try:
        await app_state.fleet.shutdown_all()
    except Exception as e:
        logger.warning(f" Fleet shutdown error: {e}")

    # Engine B  close QuestDB connection pool
    try:
        await app_state.telemetry.disconnect()
    except Exception as e:
        logger.warning(f" Telemetry disconnect error: {e}")

    # Disconnect from Redis
    try:
        await redis_manager.disconnect()
    except Exception as e:
        logger.warning(f" Redis disconnect error: {e}")

    # Stop WebSocket Streamer
    try:
        await ws_streamer.stop()
    except Exception as e:
        logger.warning(f" WebSocket Streamer shutdown error: {e}")

    logger.info(" All engines stopped. Server offline.")


#  FastAPI app 
app = FastAPI(
    title="ALGO22 Quantitative Trading API",
    description="Industrial-grade algorithmic trading backend  Aerora Dynamics",
    version="2.4.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PrometheusMiddleware)
if CorrelationIdMiddleware:
    app.add_middleware(CorrelationIdMiddleware)

# Create database tables (skip if database not available)
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    logger.warning(f"Database not available at startup: {e}")
    logger.warning("Backend will start without database connection")

#  CORS — origins are env-var driven; localhost is NOT hardcoded to prevent leakage.
_cors_origins = os.environ.get("CORS_ORIGINS", "")
_allowed_origins = [
    origin.strip()
    for origin in _cors_origins.split(",")
    if origin.strip()
]
_production_origins = [
    "https://algo22.io",
    "https://app.algo22.io",
]
_allowed_origins = list(set(_allowed_origins + _production_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#  Mount REST routers 
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(exchange.router, prefix="/api/exchanges", tags=["Exchange Vault"])
app.include_router(library.router, prefix="/api/library", tags=["Strategy Library"])
app.include_router(market.router, prefix="/api/market", tags=["Market Data"])
app.include_router(orders.router, prefix="/api/orders", tags=["Order Execution"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["Strategies"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(user.router, prefix="/api", tags=["User"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin / God Mode"])
app.include_router(risk.router, prefix="/api/risk", tags=["Risk Management"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(support.router, prefix="/api/support", tags=["Support"])
app.include_router(metrics.router, tags=["Metrics"])
#  Mount Event-Driven DAG router 
app.include_router(event_dag_router)
#  Mount Parallel DAG router 
app.include_router(parallel_dag_router)
#  Mount Risk-Integrated DAG router 
app.include_router(risk_dag_router)
#  Mount Production Execution router 
# 
# HARD STOP SAFETY CHECK (STEP 1)
# 
if ExecutionFlags.PRODUCTION_ROUTER_ENABLED:
    if execution_router:
        app.include_router(execution_router, prefix="/api/execution", tags=["Execution"])
        logger.info(" Production execution router ENABLED (verify safety before production)")
    else:
        logger.info(" PRODUCTION_ROUTER_ENABLED is True, but execution_router module does not exist. Skipping.")
else:
    logger.warning(" PRODUCTION EXECUTION ROUTER DISABLED (STEP 1 safety lockdown)")
    logger.warning("   Routes under /api/execution are BLOCKED pending safety review")
    logger.warning("   See transformation plan Phase 1 - Only /api/orders is active")
    
    # Log blocked attempts at startup
    log_blocked_execution(
        source="main.py",
        context=ExecutionContext.PRODUCTION_ROUTER.value,
        details={
            "reason": "PRODUCTION_ROUTER_ENABLED is False - unsafe path blocked pending safety review",
            "router_prefix": "/api/execution",
            "blocked_routes": "All /api/execution/* endpoints",
            "safe_alternative": "/api/orders/* (verified safe with idempotency)"
        }
    )
#  Mount Portfolio Management router 
app.include_router(portfolio_mgmt_router)
#  Mount Market Data Validation router 
app.include_router(validation_router)
#  Mount State Persistence router 
app.include_router(persistence_router)
#  Mount DAG Task Queue router 
app.include_router(dag_tasks_router)
#  Mount WebSocket router 
app.include_router(ws_router)





@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch all unhandled exceptions and return structured error response"""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "detail": "Internal server error"}
    )


#  Health probe (Kubernetes / load balancer) 
@app.get("/health", tags=["System"])
async def health():
    """Comprehensive health check with external service status"""
    service_status = {
        "redis": "unknown",
        "questdb": "unknown",
        "supabase": "unknown",
        "fleet": "unknown",
    }
    
    # Check Redis
    try:
        if redis_manager.pool:
            await redis_manager.pool.ping()
            service_status["redis"] = "connected"
        else:
            service_status["redis"] = "fallback/disconnected"
    except Exception as e:
        service_status["redis"] = f"error: {str(e)[:50]}"
    
    # Check QuestDB/Telemetry
    try:
        await app_state.telemetry.ping()
        service_status["questdb"] = "connected"
    except Exception:
        service_status["questdb"] = "fallback/mock"
    
    # Check FleetManager
    try:
        fleet_status = app_state.fleet.get_status()
        service_status["fleet"] = "online"
        service_status["active_bots"] = fleet_status.get("active_bots_count", 0)
        service_status["capacity"] = fleet_status.get("usage_pct", 0)
    except Exception:
        service_status["fleet"] = "mock/fallback"
        service_status["active_bots"] = 0
        service_status["capacity"] = 0
    
    # Check Supabase
    try:
        from backend_app.core.dependencies import get_supabase
        sb = get_supabase()
        service_status["supabase"] = "connected" if sb else "disconnected"
    except Exception as e:
        service_status["supabase"] = f"failed: {str(e)[:50]}"
    
    # Overall status - ok if at least core functions work
    all_ok = service_status.get("supabase") == "connected" and service_status.get("questdb") != "failed"
    
    return {
        "status": "ok" if all_ok else "degraded",
        "mode": "DEV_MODE" if os.environ.get("DEV_MODE", "").lower() == "true" else "production",
        "services": service_status,
    }


#  Runtime Services health endpoint 
@app.get("/health/services", tags=["System"])
async def health_services():
    """Per-service ACTIVE/INACTIVE status for ConsistencyChecker, OrderWatchdog, PnLEngine."""
    runtime = _runtime_service_status

    def _classify(v: str) -> str:
        if v == "ACTIVE":
            return "ACTIVE"
        if v in ("INACTIVE", "unknown", ""):
            return "INACTIVE"
        return "FAILED"

    services = {
        "consistency_checker":       _classify(runtime.get("consistency_checker", "")),
        "order_watchdog":            _classify(runtime.get("order_watchdog", "")),
        "pnl_engine":                _classify(runtime.get("pnl_engine", "")),
        "reconciliation_scheduler": _classify(runtime.get("reconciliation_scheduler", "")),
    }
    active_count   = sum(1 for s in services.values() if s == "ACTIVE")
    inactive_count = sum(1 for s in services.values() if s == "INACTIVE")
    failed_count   = sum(1 for s in services.values() if s == "FAILED")

    overall = "ok" if failed_count == 0 and inactive_count == 0 else (
              "degraded" if failed_count == 0 else "failed")

    return {
        "status": overall,
        "summary": {
            "active":   active_count,
            "inactive": inactive_count,
            "failed":   failed_count,
        },
        "services": services,
        "raw": runtime,
    }


#  Liveness probe (Docker HEALTHCHECK / K8s liveness) 
@app.get("/health/live", tags=["System"])
async def health_live():
    """Minimal liveness probe — returns 200 if the process is up."""
    return {"status": "alive"}


#  Stats endpoint 
@app.get("/api/stats", tags=["Stats"])
async def get_stats():
    """Get user trading statistics (mock data in DEV_MODE)"""
    
    
    # In production, fetch from database
    # For now, return empty structure
    return {
        "total_trades": 0,
        "total_pnl": 0.0,
        "win_rate": 0.0,
        "active_bots": 0,
        "total_strategies": 0,
        "daily_pnl": 0.0,
        "weekly_pnl": 0.0,
        "monthly_pnl": 0.0,
    }


# NOTE: Auth routes (/api/auth/register, /api/auth/login) are registered via
# app.include_router(auth.router, prefix="/api/auth") above.
# Do NOT add @app.post("/api/auth/...") handlers here — they create duplicate
# route registrations that cause undefined routing behaviour.
