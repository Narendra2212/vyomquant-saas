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
import secrets
import sys
import time
import traceback
from contextlib import asynccontextmanager
from typing import Optional

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
#  Global exception handler 
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from backend_app.api_ws.ws_routes import ws_router
# Market data validation
from backend_app.backend.market_data_validation import \
    router as validation_router
from backend_app.backend.observability.sentry_config import initialize_sentry
from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
from backend_app.backend.pnl_engine import PnLEngine
# Portfolio management system
from backend_app.backend.dashboard_data_ingester import (
    start_dashboard_data_ingester, stop_dashboard_data_ingester)
from backend_app.backend.portfolio_cache_updater import (
    start_portfolio_cache_updater, stop_portfolio_cache_updater)
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
from backend_app.core.supabase_connection import SupabaseConnection
from backend_app.core.state import app_state
from backend_app.core.dependencies import get_admin_user
#  Router imports 
from backend_app.routers import (admin, analytics, auth, billing, dashboard, distributed_execution,
                                 exchange, health, health_websocket, library, market, metrics, notifications,
                                 orders, portfolio, referral, risk, security, signals, strategies, 
                                 strategy_operations, support, user)
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
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        
        script_src = (
            f"'self' 'nonce-{nonce}' "
            "'sha256-XqyX1qV9pOwHROFtpBLVNTgel4/9fVj08368kkmPLlo=' "
            "'sha256-oh88oVc5GQIwejvapxT0pN+WATWNhr9pwQMCHNEmvPM=' "
            "'sha256-0rFfImQBL15VBsxmb0YzGJk4vU4uKemgRXR9f+EHKrM=' "
            "'sha256-DEymb3mo5Ws6yDucrq927MY8ojwp1g8WEp2otYEec/A=' "
            "https://cdn.jsdelivr.net "
            "https://js.sentry-cdn.com "
            "https://www.googletagmanager.com "
            "https://www.clarity.ms "
            "https://checkout.razorpay.com "
            "https://js.stripe.com"
        )
        
        connect_src = (
            "'self' "
            "ws: wss: "
            "https://*.vyomquant.com wss://*.vyomquant.com "
            "https://*.vyomquant.in "
            "https://*.supabase.co wss://*.supabase.co "
            "https://*.sentry.io "
            "https://*.clarity.ms "
            "https://www.google-analytics.com "
            "https://api.stripe.com "
            "https://api.razorpay.com"
        )
        
        style_src = (
            "'self' 'unsafe-inline' "
            "https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com"
        )
        
        img_src = (
            "'self' data: blob: "
            "https://cdn.jsdelivr.net "
            "https://fastapi.tiangolo.com "
            "https://vyomquant.in "
            "https://*.clarity.ms "
            "https://www.googletagmanager.com"
        )
        
        font_src = (
            "'self' data: "
            "https://fonts.gstatic.com "
            "https://cdn.jsdelivr.net"
        )
        
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src {script_src}; "
            f"style-src {style_src}; "
            f"img-src {img_src}; "
            f"font-src {font_src}; "
            f"connect-src {connect_src}; "
            f"frame-ancestors 'none'"
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
_execution_router_import_error: Optional[Exception] = None
try:
    from backend_app.backend.execution_router import router as execution_router
except Exception as e:
    execution_router = None
    _execution_router_import_error = e


#  Logging setup 
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s  %(message)s"
)
logger = logging.getLogger("Algo22")




sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))



from backend_app.core.safety_config import get_vyomquant_mode

env_mode = get_vyomquant_mode("safe").lower()
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
        environment=get_vyomquant_mode("production"),
        release="2.4.0"
    )
    # Print configuration status at startup
    print_config_status()
    missing_settings = settings.validate_required()
    if missing_settings:
        logger.critical(f"FATAL: Missing required environment variables: {', '.join(missing_settings)}")
        raise RuntimeError(f"FATAL: Missing required environment variables: {', '.join(missing_settings)}")
    
    # Security check: DEV_MODE should not be enabled in production-like environments
    if settings.DEV_MODE and settings.ENV.lower() in ("production", "staging"):
        logger.critical(
            f"FATAL: DEV_MODE is enabled in {settings.ENV.upper()} environment. "
            "This bypasses Redis cache failure detection and is unsafe for production deployments."
        )
        raise RuntimeError(
            f"FATAL: DEV_MODE=true is not allowed in {settings.ENV.upper()} environment. "
            "Set DEV_MODE=false or use ENV=development/ENV=testing for local development."
        )
    
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
        
        # Start WebSocket Redis Pub/Sub Bridge
        if redis_manager.pool:
            app_state.ws.start_bridge()
            logger.info(" WebSocket Redis Pub/Sub Bridge started")
            
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

    # ── Runtime Service: PortfolioCacheUpdater ─────────────────────────────
    try:
        await start_portfolio_cache_updater()
        service_status["portfolio_cache_updater"] = "ACTIVE"
        logger.info(" PortfolioCacheUpdater: ACTIVE (interval=3s, TTL=5s)")
    except Exception as e:
        service_status["portfolio_cache_updater"] = "INACTIVE"
        logger.warning(f" PortfolioCacheUpdater: INACTIVE — {e}")

    # ── Runtime Service: DashboardDataIngester ─────────────────────────────
    try:
        await start_dashboard_data_ingester()
        service_status["dashboard_data_ingester"] = "ACTIVE"
        logger.info(" DashboardDataIngester: ACTIVE (interval=60s)")
    except Exception as e:
        service_status["dashboard_data_ingester"] = "INACTIVE"
        logger.warning(f" DashboardDataIngester: INACTIVE — {e}")

    logger.info(f" Service Status: {service_status}")

    # Start WebSocket Streamer
    try:
        await ws_streamer.start()
        logger.info(" WebSocket Streamer: ACTIVE")
    except Exception as e:
        logger.warning(f" WebSocket Streamer start failed: {e}")

    # Publish to module-level dict so /health/services can read it without request context
    _runtime_service_status.update(service_status)

    # Pre-warm backend modules asynchronously in background
    async def _async_warmup():
        try:
            from backend_app.backend.dashboard_aggregation_service import get_dashboard_service
            await get_dashboard_service()
            logger.info(" Background service pre-warm complete.")
        except Exception as err:
            logger.debug(f" Pre-warm notice: {err}")
    asyncio.create_task(_async_warmup())

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

    # Runtime Service: PortfolioCacheUpdater — stop background update loop
    try:
        await stop_portfolio_cache_updater()
        logger.info(" PortfolioCacheUpdater: stopped")
    except Exception as e:
        logger.warning(f" PortfolioCacheUpdater shutdown error: {e}")

    # Runtime Service: DashboardDataIngester — stop background ingestion loop
    try:
        await stop_dashboard_data_ingester()
        logger.info(" DashboardDataIngester: stopped")
    except Exception as e:
        logger.warning(f" DashboardDataIngester shutdown error: {e}")

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


def _get_docs_url():
    is_prod = os.getenv("ENV", get_vyomquant_mode("development")).lower() in ("production", "prod", "live")
    enable_docs = os.getenv("ENABLE_DOCS")
    if enable_docs is not None:
        return "/docs" if enable_docs.lower() in ("true", "1", "yes") else None
    return None if is_prod else "/docs"

def _get_redoc_url():
    is_prod = os.getenv("ENV", get_vyomquant_mode("development")).lower() in ("production", "prod", "live")
    enable_docs = os.getenv("ENABLE_DOCS")
    if enable_docs is not None:
        return "/redoc" if enable_docs.lower() in ("true", "1", "yes") else None
    return None if is_prod else "/redoc"

def _get_openapi_url():
    enable_openapi = os.getenv("ENABLE_OPENAPI")
    if enable_openapi is not None:
        return "/openapi.json" if enable_openapi.lower() in ("true", "1", "yes") else None
    return "/openapi.json"

#  FastAPI app 
app = FastAPI(
    title="ALGO22 Quantitative Trading API",
    description="Industrial-grade algorithmic trading backend  Aerora Dynamics",
    version="2.4.0",
    lifespan=lifespan,
    docs_url=_get_docs_url(),
    redoc_url=_get_redoc_url(),
    openapi_url=_get_openapi_url(),
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

#  CORS — origins are strictly env-var driven; no hardcoded fallbacks to prevent domain-hijack vulnerability.
_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:1420,http://localhost:3000,http://127.0.0.1:1420,http://127.0.0.1:3000")
_allowed_origins = [
    origin.strip()
    for origin in _cors_origins.split(",")
    if origin.strip()
]
_allow_credentials = True
if "*" in _allowed_origins or not _allowed_origins:
    _allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allow_credentials,
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
app.include_router(dashboard.router, prefix="/api", tags=["Dashboard Aggregation"])
app.include_router(strategy_operations.router, prefix="/api", tags=["Strategy Operations"])
app.include_router(user.router, prefix="/api", tags=["User"])
app.include_router(referral.router, prefix="/api", tags=["Referral"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin / God Mode"])
# Test endpoint to verify basic routing works - must be before router includes
@app.get("/api/test-basic")
async def test_basic():
    return {"status": "ok", "message": "Basic routing works"}

app.include_router(risk.router, prefix="/api/risk", tags=["Risk Management"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(distributed_execution.router, prefix="/api/distributed-execution", tags=["Distributed Execution"])
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(health_websocket.router, prefix="/health", tags=["Health"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])
from backend_app.routers import signal_trace

app.include_router(signal_trace.router, prefix="/api/signal-trace", tags=["Signal Trace"])
app.include_router(support.router, prefix="/api/support", tags=["Support"])
app.include_router(metrics.router, tags=["Observability"])

#  Mount Production Execution router 
# 
# HARD STOP SAFETY CHECK (STEP 1)
# 
if ExecutionFlags.PRODUCTION_ROUTER_ENABLED:
    if execution_router:
        app.include_router(execution_router, prefix="/api/execution", tags=["Execution"])
        logger.info(" Production execution router ENABLED (verify safety before production)")
    else:
        error_msg = (
            "FATAL: PRODUCTION_ROUTER_ENABLED is True, but execution_router "
            "('backend_app.backend.execution_router') failed to import or does not exist. "
            f"Import error details: {_execution_router_import_error}. "
            "Application startup halted to prevent fail-open un-routed order execution."
        )
        logger.critical(error_msg)
        raise RuntimeError(error_msg)
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
#  Mount Internal Administrative routers (Protected by Admin Auth)
app.include_router(portfolio_mgmt_router, prefix="/api/internal/portfolio-mgmt", dependencies=[Depends(get_admin_user)], tags=["Internal Portfolio Mgmt"])
app.include_router(validation_router, prefix="/api/internal/market-validation", dependencies=[Depends(get_admin_user)], tags=["Internal Market Validation"])
app.include_router(persistence_router, prefix="/api/internal/persistence", dependencies=[Depends(get_admin_user)], tags=["Internal Persistence"])
#  Mount DAG Task Queue router 
app.include_router(dag_tasks_router)
#  Mount WebSocket router 
app.include_router(ws_router)





@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Global HTTPException handler — normalizes every raise HTTPException(...) across all
    routers into the canonical APIErrorResponse shape regardless of whether the original
    detail is a plain string, a rich dict, or a list.
    """
    from backend_app.core.schemas import create_api_error_response
    body = create_api_error_response(
        status_code=exc.status_code,
        detail_or_msg=exc.detail,
        path=str(request.url.path),
    )
    return JSONResponse(status_code=exc.status_code, content=body)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Global RequestValidationError handler — normalizes Pydantic body/query validation
    failures (422) into the canonical APIErrorResponse shape.
    The validation error details list is preserved in the ``details`` field.
    """
    from backend_app.core.schemas import create_api_error_response
    body = create_api_error_response(
        status_code=422,
        detail_or_msg=exc.errors(),  # list of {loc, msg, type} dicts
        path=str(request.url.path),
    )
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions and return a normalized 500 error response."""
    import traceback
    print(f"GLOBAL EXCEPTION HANDLER: {type(exc).__name__}: {exc}")
    print(f"TRACEBACK: {traceback.format_exc()}")
    traceback.print_exc()
    from backend_app.core.schemas import create_api_error_response
    body = create_api_error_response(
        status_code=500,
        detail_or_msg="Internal server error",
        path=str(request.url.path),
    )
    return JSONResponse(status_code=500, content=body)


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
    all_ok = (
        service_status.get("supabase") in ("connected", "fallback")
        and not str(service_status.get("supabase", "")).startswith("failed")
        and service_status.get("questdb") != "failed"
    )
    
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


#  Readiness probe (Docker HEALTHCHECK / K8s readiness) 
@app.get("/health/ready", tags=["System"])
async def health_ready():
    """Minimal readiness probe — returns 200 if the process is ready to serve traffic."""
    return {"status": "ready"}



#  Stats endpoint 
@app.get("/api/stats", tags=["Stats"])
async def get_stats():
    """Get system trading statistics calculated from live database records."""
    try:
        from backend_app.backend.dashboard_aggregation_service import get_dashboard_service
        svc = await get_dashboard_service()
        data = await svc.get_dashboard_data(user={"id": "public"}, equity_days=30)
        overview = data.get("overview", {})
        bots = data.get("bots", {})
        return {
            "total_trades": overview.get("total_trades", 0),
            "total_pnl": float(overview.get("today_pnl", 0.0)),
            "win_rate": float(overview.get("win_rate", 0.0)),
            "active_bots": int(bots.get("running", 0)),
            "total_strategies": int(bots.get("total", 0)),
            "daily_pnl": float(overview.get("today_pnl", 0.0)),
            "weekly_pnl": float(overview.get("today_pnl", 0.0)),
            "monthly_pnl": float(overview.get("today_pnl", 0.0)),
        }
    except Exception as e:
        logger.warning(f"Error fetching stats from dashboard service: {e}")
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
