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


import asyncio
from datetime import datetime, timezone
import logging
import os
import re
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
# Asset universe cache (strategy-builder task 7.1) — warmed at startup, refreshed on a
# schedule, so no request ever calls load_markets()
from backend_app.backend.asset_universe import (
    start_asset_universe_refresher, stop_asset_universe_refresher)
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
from backend_app.backend.paper_limit_order_sweeper import (
    PaperLimitOrderSweeper, get_paper_limit_order_sweeper)
# Database imports. The SQLAlchemy model imports below this line are kept for the
# modules that actually use them (routers, Alembic's own `env.py` target_metadata) -
# API-05 removed only the `Base.metadata.create_all(bind=engine)` DDL call this comment
# used to justify; see that removal's own note, further down, for why.
from backend_app.core.database import SessionLocal
from backend_app.core.http_metrics import \
    MarketplacePaperHttpMetricsMiddleware
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
from backend_app.core.creator_payout_scheduler import (
    start_creator_payout_scheduler, stop_creator_payout_scheduler)
from backend_app.core.runtime_env import (deployment_environment,
                                          local_state_is_acceptable)
#  SYSTEM FREEZE: Import safety config FIRST to block all execution
# This must be imported before any engine that could execute trades
from backend_app.core.safety_config import ExecutionFlags, SafetyMonitor
from backend_app.core.supabase_connection import SupabaseConnection
from backend_app.core.state import app_state
from backend_app.core.dependencies import get_admin_user
#  Router imports 
from backend_app.routers import (admin, analytics, auth, billing, copilot, dashboard,
                                 exchange, health, health_websocket, legal, library, market, metrics,
                                 notifications, orders, paper_trading, portfolio, referral, risk, security,
                                 signal_trace, signals, strategies, strategy_operations, support, user)
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
            "https://vyomquant.in https://*.vyomquant.in "
            "wss://vyomquant.in wss://*.vyomquant.in "
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
            "https://vyomquant.in https://*.vyomquant.in "
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
                endpoint=request.scope["path"],
                status_code=status_code
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                endpoint=request.scope["path"]
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

# LOG-01: core/observability.py's RedactingLogFilter only ever protected the thirteen
# marketplace/paper logger names in GUARDED_LOGGER_NAMES -- billing, exchange,
# execution and auth loggers carried no secret redaction at all. Attaching the same
# filter to the handler basicConfig just installed on the root logger covers every
# OTHER logger too, since a Handler's filters run for every record that propagates to
# it regardless of which logger originated it (unlike a Logger's own filters, which
# apply only to that logger's own records).
from backend_app.core.observability import install_root_log_redaction

install_root_log_redaction()




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

# BILL-01: handle of the plan-expiry sweep task started in lifespan
_plan_expiry_task = None
#: PAPER-04's periodic reconciliation sweep. Cancelled on shutdown like `_plan_expiry_task`.
_paper_reconcile_task = None
# RT-08: handle of the resume-after-restart task started in lifespan
_resume_task = None


# ── Lifespan: boot + graceful shutdown ──────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Sentry early
    #
    # ENV-06: `environment` is the DEPLOYMENT environment and `trading_mode` is the trading
    # mode. This call used to read `environment=get_vyomquant_mode("production")` — a
    # trading mode supplying Sentry's environment tag — so with the documented production
    # setting `VYOMQUANT_MODE=paper` every production event was filed under an environment
    # called `paper`, mixed in with developer machines, and no `production` environment
    # existed for an alert rule to select. Same substitution as API-03 (`core/rate_limit`)
    # and the `redis_manager` fix, on the reporting side.
    #
    # Both dimensions are now recorded independently: `deployment_environment()` reads
    # ENV/ENVIRONMENT and never a trading mode, and the trading mode is attached as its own
    # global tag. `"unknown"` rather than a plausible-looking default, because a process
    # that has not declared where it runs should be visibly unlabelled.
    # Regression: tests/test_api05_documentation_exposure_matrix.py
    initialize_sentry(
        dsn=os.getenv("SENTRY_DSN"),
        environment=deployment_environment() or "unknown",
        release="2.4.0",
        trading_mode=get_vyomquant_mode("safe"),
    )
    # Print configuration status at startup
    print_config_status()
    missing_settings = settings.validate_required()
    if missing_settings:
        logger.critical(f"FATAL: Missing required environment variables: {', '.join(missing_settings)}")
        raise RuntimeError(f"FATAL: Missing required environment variables: {', '.join(missing_settings)}")
    
    # ══════════════════════════════════════════════════════════════════════
    #  JWT SECRET STRENGTH  (BUG-012) — fails the boot, not just the log
    # ══════════════════════════════════════════════════════════════════════
    # `validate_required` above only checks that JWT_SECRET is PRESENT. Its strength
    # used to be two `logger.warning` calls that the application started straight
    # through, so the 24-byte secret this deployment actually had shipped with nothing
    # but a line in the boot log.
    #
    # Placed here, immediately after the required-variables check and before any
    # service is initialised or any port is bound, so a deployment with an unfit secret
    # never accepts a request. The raised message names every problem and no part of the
    # secret.
    # Regression: tests/test_jwt_secret_is_production_grade.py
    settings.assert_jwt_secret_is_production_grade()

    # ══════════════════════════════════════════════════════════════════════
    #  CREDENTIALS ARE CONFIGURED, NOT MERELY PRESENT  (ENV-03)
    # ══════════════════════════════════════════════════════════════════════
    # `validate_required` above is spelled `if not v`, and
    # `bool("YOUR_SUPABASE_SERVICE_ROLE_KEY")` is True — so the placeholders committed in
    # `backend_app/.env` satisfied it completely. This is the check that tells a template
    # value from a credential: MISSING, EMPTY and PLACEHOLDER are all refused for a
    # deployed environment, and a local run with `ENV=development` or `DEV_MODE=true` keeps
    # its documented behaviour (reported by `print_config_status` above, not fatal).
    #
    # Placed immediately after the JWT check and before any service is initialised or any
    # port is bound, so a misconfigured deployment never answers a request. The raised
    # message names every unfit variable and no part of any value.
    # Regression: tests/test_env03_placeholder_secrets_are_invalid.py
    settings.assert_credentials_are_production_grade()

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

    # ══════════════════════════════════════════════════════════════════════
    #  THE MASTER KEY CAN STILL READ WHAT IT PROTECTS  (VAULT-01)
    # ══════════════════════════════════════════════════════════════════════
    # A master encryption key was once replaced without re-encrypting the credentials
    # it protected, and nothing detected it -- not a health check, not a test, not an
    # alarm. The four affected ciphertext values are permanently unrecoverable; the
    # cost was nil only because live trading is hard-gated off and two rows were
    # affected. The same event after launch silently breaks every funded exchange
    # connection, and the first symptom would be a ValueError from
    # APIKeyVault._decrypt at order time.
    #
    # Placed here, after the configuration checks and before any service is
    # initialised or any port is bound, so a deployment whose key cannot read the
    # vault never accepts a request. That is the entire point: it makes a wrong key a
    # FAILED DEPLOYMENT -- ECS keeps the previous task definition, which has the key
    # that works -- instead of a data disaster discovered at first order.
    #
    # Unlike the checks above this one, it reaches the database. So it refuses the
    # boot on exactly one verdict, KEY_MISMATCH, where rows were read and EVERY
    # in-scope value failed. An unreadable sample (UNDETERMINED) and a partial
    # failure (DEGRADED) are logged loudly and allowed through, because refusing to
    # start on a dependency blip or on one poisoned row would be a self-inflicted
    # outage. See the module docstring for why each verdict behaves as it does.
    # Regression: tests/test_vault01_master_key_decryptability_check.py
    from backend_app.core.credential_key_integrity import (
        assert_master_key_can_read_stored_credentials,
    )
    assert_master_key_can_read_stored_credentials()

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
        supabase_conn = SupabaseConnection()
        if supabase_conn.is_configured:
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
        logger.info(" ConsistencyChecker: started (interval=10s) - NOT a working safety net yet (RT-18)")
    except Exception as e:
        service_status["consistency_checker"] = "INACTIVE"
        logger.warning(f" ConsistencyChecker: INACTIVE — {e}")

    # ── Runtime Service: PaperLimitOrderSweeper (PAPER-07) ───────────
    _paper_limit_sweeper: Optional[PaperLimitOrderSweeper] = None
    try:
        _paper_limit_sweeper = get_paper_limit_order_sweeper()
        await _paper_limit_sweeper.start()
        service_status["paper_limit_order_sweeper"] = "ACTIVE"
        logger.info(" PaperLimitOrderSweeper: started (interval=30s)")
    except Exception as e:
        service_status["paper_limit_order_sweeper"] = "INACTIVE"
        logger.warning(f" PaperLimitOrderSweeper: INACTIVE — {e}")

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
        logger.info(" ReconciliationScheduler: started (poll=30s) - NOT a working safety net yet (RT-18)")
    except Exception as e:
        service_status["reconciliation_scheduler"] = "INACTIVE"
        logger.warning(f" ReconciliationScheduler: INACTIVE — {e}")

    # ── PAPER-04: mark paper sessions whose loop died as ABANDONED ───────────
    #
    # A ONE-SHOT PASS AT BOOT, not a loop, which is why it belongs here and does not
    # repeat PAPER-04's own recorded defect. The session LOOPS still run in this process
    # and still die on redeploy -- that half needs the worker service INF-03 covers, and
    # this is not a substitute for it. What this fixes is that the death used to be
    # INVISIBLE: the row sat at RUNNING forever, counted against Requirement 27.4's
    # concurrency cap, and read as live to every client while no figure it reported would
    # ever advance again.
    #
    # Safe to run from every gunicorn worker concurrently, which matters because
    # startup.sh runs `--workers ${WORKERS:-2}`: the population is heartbeat-STALE rows
    # rather than RUNNING rows, so a session another worker is healthily running is never
    # eligible, and the write is a guarded UPDATE so a race is a no-op rather than a
    # double write. Needs no leader election for that reason.
    #
    # Awaited rather than spawned: it is bounded (one indexed read plus one UPDATE per
    # orphan, and RUNNING is capped at 20 per user), and a startup that reported ACTIVE
    # before reconciling would serve its first requests with sessions still lying about
    # their state.
    try:
        from backend_app.backend.paper.paper_session_service import (
            reconcile_orphaned_sessions, run_reconciliation_loop)
        from backend_app.core.dependencies import get_service_supabase

        _paper_recon = await reconcile_orphaned_sessions(get_service_supabase())
        service_status["paper_session_reconciliation"] = (
            "ACTIVE" if not _paper_recon.get("errors") else "DEGRADED"
        )
        logger.info(
            " PaperSessionReconciliation: %s examined, %s abandoned, %s raced, %s error(s)",
            _paper_recon.get("examined"),
            _paper_recon.get("abandoned"),
            _paper_recon.get("raced"),
            _paper_recon.get("errors"),
        )

        # ── AND THEN KEEP SWEEPING (PAPER-04) ───────────────────────────────
        # The boot pass above covered the redeploy case by accident: `--graceful-timeout
        # 30` means a redeploy produces a boot soon after, so the sweep ran then. It did
        # NOT cover a death that produces no boot -- a task killed by an escaped
        # exception, a feed handle that never reconnects, a gunicorn worker that survives
        # while the task inside it does not. Nothing re-ran the sweep, so those orphans
        # sat at RUNNING indefinitely, counting against Requirement 27.4's cap and
        # reading as live: the condition PAPER-04 describes, left in place by the half of
        # it that shipped.
        #
        # Spawned rather than awaited, unlike the boot pass: this one never finishes.
        # Deliberately runs in EVERY worker with no leader election -- the sweep selects
        # heartbeat-stale rows and writes through a guarded UPDATE, so it is already
        # idempotent, and one worker's dead event loop must not stop the others.
        global _paper_reconcile_task
        _paper_reconcile_task = asyncio.create_task(
            run_reconciliation_loop(get_service_supabase)
        )
    except Exception as e:
        # `reconcile_orphaned_sessions` does not raise; this catches a failure to RESOLVE
        # it or the Persistence_Layer handle. Either way a boot must not fail because the
        # thing that reports damage could not run.
        service_status["paper_session_reconciliation"] = "INACTIVE"
        logger.warning(f" PaperSessionReconciliation: INACTIVE — {e}")

    # ── MKT-06: CreatorPayoutScheduler — weekly marketplace creator payouts ──
    try:
        await start_creator_payout_scheduler()
        service_status["creator_payout_scheduler"] = "ACTIVE"
        logger.info(
            " CreatorPayoutScheduler: started (poll=1h, sweep interval=7d) — "
            "creates payout rows only, never moves money; settlement is manual "
            "and admin-only (PUT /api/library/creator-payouts/{id}/settle)"
        )
    except Exception as e:
        service_status["creator_payout_scheduler"] = "INACTIVE"
        logger.warning(f" CreatorPayoutScheduler: INACTIVE — {e}")

    # ── BILL-01: expire lapsed one-time (Razorpay) plan purchases ───────────
    global _plan_expiry_task
    try:
        from backend_app.core.dependencies import get_service_supabase
        from backend_app.core.plan_expiry import run_plan_expiry_loop
        _plan_expiry_task = asyncio.create_task(run_plan_expiry_loop(get_service_supabase))
        service_status["plan_expiry_sweep"] = "ACTIVE"
    except Exception as e:
        service_status["plan_expiry_sweep"] = "INACTIVE"
        logger.warning(f" PlanExpirySweep: INACTIVE — {e}")

    # ── RT-08: resume deployments the database says are running ────────────
    # Runs in the background so a slow venue cannot delay readiness. Live deployments are
    # only resumed when the operator opted in (RESUME_LIVE_DEPLOYMENTS); otherwise their
    # rows are marked failed.
    #
    # RT-23: this used to also skip when USE_TEE was set, deferring resumption to a
    # separate runtime (backend_app/tee/main.py). That module is deleted -- it crashed on
    # import and real AWS evidence confirmed it was never actually deployed -- so the
    # branch this guarded is now permanently unreachable rather than conditionally
    # skipped. No production behaviour changes: USE_TEE was never set to "true" anywhere
    # in this deployment (nothing could have consumed it), so this path already ran
    # unconditionally in practice.
    global _resume_task
    try:
        if getattr(app_state, "fleet", None):
            from backend_app.backend.deployment_runtime import \
                resume_running_deployments
            _resume_task = asyncio.create_task(resume_running_deployments(app_state.fleet))
            service_status["deployment_resume"] = "ACTIVE"
        else:
            service_status["deployment_resume"] = "INACTIVE"
    except Exception as e:
        service_status["deployment_resume"] = "INACTIVE"
        logger.warning(f" DeploymentResume: INACTIVE — {e}")

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

    # ── Runtime Service: AssetUniverseRefresher ───────────────────────────
    # strategy-builder task 7.1 / Requirements 11.5, 25.5. `start()` only creates the
    # task, so a slow or unreachable exchange delays no boot; the first refresh IS the
    # startup warm and it runs off the request path. Until it lands, asset discovery
    # answers 503 ASSET_UNIVERSE_UNAVAILABLE rather than a substitute symbol list.
    try:
        await start_asset_universe_refresher()
        service_status["asset_universe_refresher"] = "ACTIVE"
        logger.info(" AssetUniverseRefresher: ACTIVE (TTL=6h, refresh=3h)")
    except Exception as e:
        service_status["asset_universe_refresher"] = "INACTIVE"
        logger.warning(f" AssetUniverseRefresher: INACTIVE — {e}")

    logger.info(f" Service Status: {service_status}")

    # Start WebSocket Streamer
    try:
        await ws_streamer.start()
        logger.info(" WebSocket Streamer: ACTIVE")
    except Exception as e:
        logger.warning(f" WebSocket Streamer start failed: {e}")

    # Publish to module-level dict so /health/services can read it without request context
    _runtime_service_status.update(service_status)
    # OBS-01: stamp the snapshot so /health/services can report its age rather than
    # presenting boot-time state as live state.
    _runtime_service_status["_captured_at"] = datetime.now(timezone.utc).isoformat()

    yield  #  Server is live and accepting requests here

    #  Graceful shutdown 
    logger.info(" Shutdown initiated  terminating all bots...")

    # ══════════════════════════════════════════════════════════════════════
    #  PAPER-04: RECORD THE DEATH OF THE SESSIONS THIS PROCESS WAS HOSTING
    # ══════════════════════════════════════════════════════════════════════
    # FIRST in the shutdown sequence, before any engine is stopped and while the database
    # handle still works. Every paper session loop in this process is about to end, and
    # this is the only moment anything knows WHICH sessions those are: the row says
    # RUNNING, but `gunicorn --workers ${WORKERS:-2}` means "a RUNNING row" and "a row
    # this process is running" are different sets, and draining the first would abandon
    # another worker's live sessions.
    #
    # Without this, a redeploy left the row claiming RUNNING until the staleness window
    # plus a sweep interval had passed -- up to 1200s of a session reading as live after
    # its loop had stopped, which is PAPER-04's "die silently" exactly. With it, the row
    # is accurate at the moment of the redeploy and carries a reason that names it.
    #
    # The periodic sweep stays the backstop for every death that gets NO clean shutdown:
    # SIGKILL, OOM, a lost task. This is the prompt path, not the only one.
    #
    # Never raises: a shutdown must not fail because the thing recording damage could not
    # run, and a session left at RUNNING is still visible to the sweep.
    try:
        from backend_app.routers.paper_trading import drain_sessions_hosted_here

        _paper_drain = await drain_sessions_hosted_here(
            "the web process hosting this session's loop shut down (redeploy or worker "
            "restart). No bar was processed after this point; the session did not end on "
            "its own terms and nothing resumed it."
        )
        if _paper_drain.get("examined"):
            logger.info(
                " PaperSessionDrain: %s hosted, %s abandoned, %s already moved on, "
                "%s error(s)",
                _paper_drain.get("examined"), _paper_drain.get("abandoned"),
                _paper_drain.get("raced"), _paper_drain.get("errors"),
            )
    except Exception as e:
        logger.warning(f" PaperSessionDrain shutdown error: {e}")

    # PAPER-04: stop the periodic reconciliation sweep
    if _paper_reconcile_task is not None:
        _paper_reconcile_task.cancel()
        try:
            await _paper_reconcile_task
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning(f" PaperSessionReconciliation shutdown error: {e}")

    # Runtime Service: ConsistencyChecker — stop background check loop
    try:
        if _consistency_checker is not None:
            await _consistency_checker.stop()
            logger.info(" ConsistencyChecker: stopped")
    except Exception as e:
        logger.warning(f" ConsistencyChecker shutdown error: {e}")

    # Runtime Service: PaperLimitOrderSweeper — stop background sweep loop
    try:
        if _paper_limit_sweeper is not None:
            await _paper_limit_sweeper.stop()
            logger.info(" PaperLimitOrderSweeper: stopped")
    except Exception as e:
        logger.warning(f" PaperLimitOrderSweeper shutdown error: {e}")

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

    # MKT-06: CreatorPayoutScheduler — stop background sweep loop
    try:
        await stop_creator_payout_scheduler()
        logger.info(" CreatorPayoutScheduler: stopped")
    except Exception as e:
        logger.warning(f" CreatorPayoutScheduler shutdown error: {e}")

    # BILL-01: stop the plan-expiry sweep
    try:
        if _plan_expiry_task is not None:
            _plan_expiry_task.cancel()
            try:
                await _plan_expiry_task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        logger.warning(f" PlanExpirySweep shutdown error: {e}")

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

    # Runtime Service: AssetUniverseRefresher — stop the scheduled market refresh
    try:
        await stop_asset_universe_refresher()
        logger.info(" AssetUniverseRefresher: stopped")
    except Exception as e:
        logger.warning(f" AssetUniverseRefresher shutdown error: {e}")

    # Engine L  stop all running BotRunner tasks safely
    try:
        await app_state.fleet.shutdown_all()
    except Exception as e:
        logger.warning(f" Fleet shutdown error: {e}")

    # CONN-01: drain the exchange connection pool.
    # ------------------------------------------------------------------------------------
    # Every block above stops a service that OPENED venue connections; none of them closed
    # one. `backend/connection_engine.py::_exchange_pool` holds one authenticated ccxt client
    # per (user, venue) pair, each with a live aiohttp session and a plaintext credential in
    # memory, and nothing in this lifespan reached it — so every pooled client survived until
    # the process died, which is where the "Unclosed client session" / "ccxt requires you to
    # release all resources" noise at shutdown came from.
    #
    # Placed AFTER `fleet.shutdown_all()` deliberately: the bots are the main consumers of
    # these connections, so closing the sockets before their tasks have stopped would pull the
    # transport out from under an in-flight call. Placed BEFORE the Redis disconnect because a
    # close may still want to record telemetry.
    #
    # Failures are logged, never raised: shutdown must continue to the remaining engines.
    # Regression: tests/test_conn01_pool_lifecycle_and_shutdown.py
    try:
        from backend_app.backend.connection_engine import (close_all_exchanges,
                                                           exchange_pool_size)
        pooled = exchange_pool_size()
        closed = await close_all_exchanges()
        logger.info(f" Exchange pool: closed {closed} of {pooled} pooled venue client(s)")
    except Exception as e:
        logger.warning(f" Exchange pool drain error: {e}")

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


def _interactive_docs_allowed() -> bool:
    """Whether this process may serve ``/docs`` and ``/redoc``.

    WHAT WAS WRONG. Both URL helpers decided with::

        os.getenv("ENV", get_vyomquant_mode("development")).lower() in ("production", "prod", "live")

    -- a **trading mode** supplying the default for a **deployment environment**. That is
    the API-03 defect, which ``core/rate_limit.py`` was fixed for and this was not. With
    ``ENV`` unset and the documented production setting ``VYOMQUANT_MODE=paper``, the
    expression evaluated ``"paper" in ("production", "prod", "live")`` -> ``False``, so the
    interactive docs were served. And the mirror image was just as wrong in the other
    direction: ``VYOMQUANT_MODE=live`` made ``is_prod`` true and hid the docs on a machine
    that had declared no environment at all. The docs followed what the system was allowed
    to do with money.

    WHAT IT DOES NOW. ``runtime_env.local_state_is_acceptable()`` -- the single precedence
    rule (RUNTIME-01), which reads ``ENV`` / ``ENVIRONMENT`` / ``DEV_MODE`` and never a
    trading mode. Docs are served only where that returns ``True``: a declared
    development/test environment, or a silent one that says ``DEV_MODE=true``.

    This is never looser than the old behaviour, case by case:

        ENV=development                  served  ->  served      (unchanged)
        ENV=production                   hidden  ->  hidden       (unchanged)
        ENV=production + DEV_MODE=true   hidden  ->  hidden       (unchanged)
        ENV=staging                      SERVED  ->  hidden       (stricter)
        ENV unset + VYOMQUANT_MODE=paper SERVED  ->  hidden       (stricter)
        ENV unset, no DEV_MODE           SERVED  ->  hidden       (stricter)
        ENV unset + DEV_MODE=true        served  ->  served       (unchanged)

    ``ENABLE_DOCS`` remains an explicit operator override in both directions and is
    consulted first, because an operator who names the behaviour outranks an inference.

    Regression: tests/test_environment_precedence_is_centralised.py
    """
    return local_state_is_acceptable()


def _get_docs_url():
    enable_docs = os.getenv("ENABLE_DOCS")
    if enable_docs is not None:
        return "/docs" if enable_docs.lower() in ("true", "1", "yes") else None
    return "/docs" if _interactive_docs_allowed() else None

def _get_redoc_url():
    enable_docs = os.getenv("ENABLE_DOCS")
    if enable_docs is not None:
        return "/redoc" if enable_docs.lower() in ("true", "1", "yes") else None
    return "/redoc" if _interactive_docs_allowed() else None

def _get_openapi_url():
    """``/openapi.json``: the schema follows the pages it feeds (API-05).

    THE POLICY, STATED. There are two distinct surfaces here and they were being governed
    by two different rules by accident rather than by decision:

      * ``/docs`` and ``/redoc`` are the INTERACTIVE, browser-executable consoles. Served
        only where ``runtime_env.local_state_is_acceptable()`` is true (ENV-05).
      * ``/openapi.json`` is the MACHINE-READABLE SCHEMA those consoles render from. It
        enumerates every path, method, parameter and model in the application.

    Hiding the console while publishing the schema discloses exactly the surface the hiding
    withholds, so the schema now follows the same rule. ``ENABLE_OPENAPI`` remains an
    explicit operator override in BOTH directions and is consulted first, which is the
    escape hatch for a deployed environment that genuinely has a schema consumer.

    DOCUMENTATION IS NOT DISABLED EVERYWHERE. In a declared development or test
    environment — and on any machine that says ``DEV_MODE=true`` — all three URLs are
    served exactly as before. What changes is production and staging, and only by default.

    WHY THIS WAS DEFERRED BEFORE, AND WHAT THE EVIDENCE NOW SAYS. The earlier pass left it
    alone because unmounting the schema can break a generated client, a contract test or an
    API-gateway import. Measured since:

      * NOTHING in this repository fetches ``/openapi.json`` over HTTP. The frontend
        mentions it only in comments (``algo22-terminal/.env.production``, two test
        docstrings); no CI workflow references it; and ``tests/test_route_contract.py``
        reads ``app.openapi()`` IN PROCESS, so it is unaffected by this URL.
      * CloudFront distribution EEOXECPHQ8SR0 does carry an explicit ``/openapi.json`` cache
        behaviour (``infra/rollback/cloudfront-...-pre-domain.json``,
        ``scripts/update_cloudfront_policies.py``, ``infra/dns.md``), and
        ``core/tenant_middleware.py`` and ``core/subscription_middleware.py`` both list the
        path as public. Those are ROUTING and AUTH facts, and they predate the ENV-05
        change: they exist because ``/docs`` used to be served in production and had to be
        reachable unauthenticated for the page to work. Once the console became local-only
        the schema's publication lost its reason.

    CONSEQUENCE, SPELLED OUT. In production ``/openapi.json`` now answers 404, and the
    CloudFront behaviour for it becomes a no-op forwarding to a 404. Neither the CDN
    behaviour nor the middleware allowlists are removed here — deleting a deployed cache
    behaviour is an infrastructure change, and a routed path that answers 404 is harmless.
    ``ENABLE_OPENAPI=true`` restores the previous behaviour with one variable.

    Regression: tests/test_api05_documentation_exposure_matrix.py
    """
    enable_openapi = os.getenv("ENABLE_OPENAPI")
    if enable_openapi is not None:
        return "/openapi.json" if enable_openapi.lower() in ("true", "1", "yes") else None
    return "/openapi.json" if _interactive_docs_allowed() else None

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
# A refused request answers the catalogue's MARKETPLACE_RATE_LIMITED (429) through the one
# structured envelope, instead of slowapi's ``{"error": "Rate limit exceeded: 120 per 1
# minute"}`` — a sentence with no machine-readable code that prints the limit back at the
# caller (Requirements 6.11, 22.4, 22.9). slowapi's own ``_rate_limit_exceeded_handler`` is
# superseded and its import removed, so there is one 429 shape in the process rather than two.
from backend_app.core.rate_limit_keys import marketplace_rate_limit_handler
app.add_exception_handler(RateLimitExceeded, marketplace_rate_limit_handler)

# Marketplace and paper-trading structured errors (marketplace-subscriptions-paper-trading
# task 5.5). One handler serves MarketplaceError and PaperError alike — it is registered
# against their shared base, and Starlette resolves a handler by walking the exception's
# __mro__, so it is found before the catch-all ``Exception`` handler below. The body is
# ``{"error": {code, message, details}, "request_id"}`` and carries no stack trace, database
# error string, query, internal path or foreign identifier (Requirements 22.9, 26.1).
from backend_app.backend.marketplace.errors import register_structured_error_handlers
register_structured_error_handlers(app)

app.add_middleware(SlowAPIMiddleware)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(PrometheusMiddleware)
# marketplace-subscriptions-paper-trading task 33.5 (Requirements 26.6, 27.6): the per-route
# latency and error-rate metrics for the endpoints that spec introduced or modified, recorded onto
# the one `backend/metrics.py` collector that `routers/metrics.py` exposes. Additive - it records
# `marketplace.http.*` and `paper.http.*` for `/api/library/*` and `/api/paper/*` and leaves every
# other request, and every figure `PrometheusMiddleware` above already records, untouched.
app.add_middleware(MarketplacePaperHttpMetricsMiddleware)
if CorrelationIdMiddleware:
    # ── THE CLIENT'S OWN REQUEST ID IS HONOURED, NOT DISCARDED ──────────────
    #
    # `apiClient` mints `req_<uuid4>` for every request, sends it as `X-Request-ID`, and
    # `design/errorCopy.readSupportRef` prints it on the error panel as the support reference a
    # trader is asked to quote. The middleware's DEFAULT validator is `is_valid_uuid4`, which
    # rejects that value outright — the `req_` prefix is not a UUID — logs
    #
    #     Generated new request ID (…), since request header value failed validation
    #
    # and replaces it with a freshly generated hex id. Every log record for that request, and the
    # `request_id` in every structured error body, then carried the SERVER's id while the trader's
    # screen showed the browser's. So the one identifier a customer could quote matched nothing on
    # the server, and a support report had to be correlated by guessing over timestamps. That
    # applied to every endpoint in the application, not to one.
    #
    # The validator below accepts the format this application's own client produces and nothing
    # looser. It stays strict on purpose: an unvalidated inbound header is a log-injection seam —
    # a caller could write arbitrary text, including forged ids and newlines, into the log line
    # that identifies its own request. A bare uuid4 is accepted too, so curl, the mobile client and
    # an upstream proxy that already sets a correlation id all keep working, and anything else is
    # still replaced by a generated id exactly as before.
    _CLIENT_REQUEST_ID = re.compile(
        r"\A(?:req_)?[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
        r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\Z"
    )

    def _is_acceptable_request_id(value: str) -> bool:
        """Whether an inbound ``X-Request-ID`` may be adopted as this request's identifier."""
        return bool(value) and _CLIENT_REQUEST_ID.match(value) is not None

    app.add_middleware(CorrelationIdMiddleware, validator=_is_acceptable_request_id)

# API-05: `Base.metadata.create_all(bind=engine)` used to run here, unconditionally, at
# IMPORT time. Removed. Two reasons, not one:
#
# 1. This is a THIRD schema-management mechanism alongside the hand-written numbered SQL
#    migrations (`backend_app/migrations/*.sql`, which declare RLS policies, triggers,
#    check constraints and explicit grants this ORM layer knows nothing about) and
#    Alembic (`backend_app/alembic/`, whose own `env.py` already targets this same
#    `Base.metadata`). `create_all` only creates a table that does not already exist, so
#    in any environment where the real migrations have not yet been applied - a fresh
#    database, a developer machine, a broken deploy - it would silently materialise
#    whatever a SQLAlchemy model class here declares: no RLS, no triggers, no grants, no
#    check constraints. A caller that later checks "does this table exist" would get a
#    truthful yes for a table missing every safety property the real migration gives it.
# 2. It ran at IMPORT, inside `main.py`'s own module body - every process that imports
#    this module (the web server, a worker, a test collection run) attempted a
#    schema-mutating DDL statement against whatever `DATABASE_URL` it happened to
#    resolve, merely by being imported. A test suite or a short-lived worker has no
#    business issuing DDL as a side effect of `import backend_app.main`.
#
# The migrations are the only source of truth for schema now; nothing here replaces
# that, because nothing here should.

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
app.include_router(paper_trading.router, prefix="/api/paper", tags=["Paper Trading"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["Strategies"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(dashboard.router, prefix="/api", tags=["Dashboard Aggregation"])
app.include_router(strategy_operations.router, prefix="/api", tags=["Strategy Operations"])
app.include_router(user.router, prefix="/api", tags=["User"])
app.include_router(referral.router, prefix="/api", tags=["Referral"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["Notifications"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin / God Mode"])
app.include_router(risk.router, prefix="/api/risk", tags=["Risk Management"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
#  RT-11: distributed_execution.router WAS mounted here. Removed: `execution_orchestrator`
#  is never initialized or started anywhere in this codebase (confirmed by
#  tests/test_execution_paths_that_claim_an_exchange.py's own measurement --
#  `exchange_gateways == {}` and `worker_pool is None` always), so every submission already
#  refused with 400 before this fix. That refusal closed the dangerous failure mode (a job
#  silently accepted and never processed), but left a fully-documented, "institutional-grade
#  reliability" HTTP surface mounted for a queue nothing drains -- confusing to any caller
#  and, per this finding, worth removing rather than leaving reachable-but-useless. No
#  frontend caller exists (grepped algo22-terminal/src/). The router module and the
#  `backend/distributed_execution/` package are intentionally left in place, not deleted:
#  several tests import them directly by path to pin the orchestrator's unstarted state,
#  and `backend/distributed_execution/exchange_reconciliation_engine.py`'s reconciliation
#  loops are a separate, not-yet-evaluated concern this change does not touch.
app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(health_websocket.router, prefix="/health", tags=["Health"])
app.include_router(signals.router, prefix="/api/signals", tags=["Signals"])
app.include_router(signal_trace.router, prefix="/api/signal-trace", tags=["Signal Trace"])
app.include_router(copilot.router, prefix="/api/v1/copilot", tags=["AI Copilot"])
app.include_router(support.router, prefix="/api/support", tags=["Support"])
# LEGAL-01: records which version of the Terms/Privacy/Risk/Refund documents each
# user accepted. Before this existed the signup checkbox was client-side state
# that was never persisted, so the platform could not answer that question for
# any of its 182 accounts.
app.include_router(legal.router, prefix="/api/legal", tags=["Legal"])
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
    
    # NO BLOCKED-EXECUTION EVENT IS RECORDED HERE, AND THAT IS THE FIX.
    #
    # This used to call log_blocked_execution(...) on every process start. That function
    # exists to record an execution that was ATTEMPTED and stopped - which is exactly how
    # dag_worker._run_dag_with_heartbeat and dag_event_loop._emit_signal use it, each
    # recording a real call that a feature flag refused.
    #
    # Nothing is attempted here. A router simply was not mounted. Recording it as a blocked
    # execution put a phantom event into safety_monitor._blocked_events on every container
    # start, persisted it to Redis under the retention window, and inflated what
    # safety_monitor.get_statistics() reports as total_blocked_events - adding a
    # production_router entry to blocked_by_context that no caller caused. Anyone auditing
    # blocked executions would be reading an event that never happened.
    #
    # It also emitted logger.critical on every start for a permanent, deliberate setting.
    # CRITICAL is the channel that is supposed to mean someone should look now; spending it
    # on a configuration that has not changed since STEP 1 is how a CRITICAL stops being
    # read. The three warnings above already state the router is disabled, which is the
    # accurate record: a fact about configuration, logged as one.
    logger.warning(
        "   Blocked routes: %s/* | Safe alternative: /api/orders/* "
        "(verified safe with idempotency)",
        "/api/execution",
    )
#  Mount Internal Administrative routers (Protected by Admin Auth)
app.include_router(portfolio_mgmt_router, prefix="/api/internal/portfolio-mgmt", dependencies=[Depends(get_admin_user)], tags=["Internal Portfolio Mgmt"])
app.include_router(validation_router, prefix="/api/internal/market-validation", dependencies=[Depends(get_admin_user)], tags=["Internal Market Validation"])
app.include_router(persistence_router, prefix="/api/internal/persistence", dependencies=[Depends(get_admin_user)], tags=["Internal Persistence"])
#  Mount DAG Task Queue router 
app.include_router(dag_tasks_router)
#  Mount WebSocket router 
app.include_router(ws_router)





# CVE-2026-48710 (BadHost), and why every path below reads scope[path] rather than
# request.url.path.
#
# starlette 0.41.0 - the version fastapi 0.115.3 pins, via starlette<0.42.0,>=0.40.0 - builds
# request.url by joining the Host header to the path and RE-PARSING the result. A Host of
# app.vyomquant.in/health therefore makes request.url.path return /health/<real path>.
# Verified against the pinned version, not assumed.
#
# Consequences that were live here: the Prometheus endpoint label became attacker-controlled
# (unbounded label cardinality from one client), and these error bodies reflected the poisoned
# value back to the caller. The more dangerous shape - core/tenant_middleware.py skipping
# AUTHENTICATION on a startswith over skip_paths - is NOT currently installed on this app, but
# it is fixed too, because leaving it would be a loaded gun for whoever mounts it next.
#
# TrustedHostMiddleware was considered and NOT added: the ALB health check addresses the task
# by IP, so a Host allow-list would answer 400, the target would fail its health check and ECS
# would kill the task. scope[path] comes from the request line, cannot be influenced by any
# header, and needs no allow-list to maintain. The real remedy is starlette >= 1.0, which
# cannot land until fastapi is upgraded - recorded as task 13.12*.
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Global HTTPException handler — normalizes every raise HTTPException(...) across all
    routers into the canonical APIErrorResponse shape regardless of whether the original
    detail is a plain string, a rich dict, or a list.

    ``exc.headers`` is forwarded (strategy-builder task 7.1). It was previously discarded,
    which silently dropped headers the HTTP specification requires an error to carry —
    ``Retry-After`` on a 503 and ``WWW-Authenticate`` on a 401 among them. The body shape
    is unchanged; only the headers a raiser explicitly attached are now preserved.
    """
    from backend_app.core.schemas import create_api_error_response
    body = create_api_error_response(
        status_code=exc.status_code,
        detail_or_msg=exc.detail,
        path=str(request.scope["path"]),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=getattr(exc, "headers", None) or None,
    )


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
        path=str(request.scope["path"]),
    )
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions and return a normalized 500 error response.

    API-04. This used to write the exception type, message and full traceback to stdout
    via three separate bare ``print()``/``traceback.print_exc()`` calls -- bypassing the
    application's own logging configuration entirely (no level, no handler routing, no
    structured format for whatever ingests this process's logs) and, in many deployments,
    landing in the SAME raw, unredacted stream any other stdout write would. Routed
    through this module's own ``logger`` instead: ``logger.exception`` logs at ERROR and
    attaches the current exception's traceback the same way ``traceback.format_exc()``
    did, through the one path every other error in this codebase already reports through.
    The response body is unchanged -- it already said only "Internal server error" and
    never echoed ``exc`` to the caller.
    """
    logger.exception(
        "Unhandled exception on %s: %s: %s",
        request.scope.get("path"),
        type(exc).__name__,
        exc,
    )
    from backend_app.core.schemas import create_api_error_response
    body = create_api_error_response(
        status_code=500,
        detail_or_msg="Internal server error",
        path=str(request.scope["path"]),
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
    
    # Overall status.
    #
    # OBS-01: this considered only supabase and questdb, so REDIS BEING DOWN STILL
    # REPORTED "ok" — and Redis is not a cache here, it backs the rate limiter (API-03)
    # and the distributed execution locks. The redis probe above was already running and
    # its result was simply discarded.
    #
    # Redis is only counted when this environment actually requires it, using the same
    # detector as the rate limiter and the readiness gate, so a developer with no Redis
    # does not see a permanently degraded dashboard.
    from backend_app.core.runtime_env import local_state_is_acceptable

    _redis_required = not local_state_is_acceptable()
    _redis_state = str(service_status.get("redis", ""))
    _redis_ok = (not _redis_required) or _redis_state == "connected"

    all_ok = (
        service_status.get("supabase") in ("connected", "fallback")
        and not str(service_status.get("supabase", "")).startswith("failed")
        and service_status.get("questdb") != "failed"
        and _redis_ok
    )
    service_status["redis_required"] = _redis_required
    
    return {
        "status": "ok" if all_ok else "degraded",
        "mode": "DEV_MODE" if os.environ.get("DEV_MODE", "").lower() == "true" else "production",
        "services": service_status,
    }


#  Runtime Services health endpoint
# API-07: per-service internal state (ConsistencyChecker/OrderWatchdog/PnLEngine) is
# operational detail, not the liveness/readiness signal a load balancer needs -- confirmed
# via deploy-archive/k8s/ingress.yaml (ALB health check is /health) and healthcheck.sh
# (Docker's own check is /health/live), neither of which names this path. Gated behind
# get_admin_user rather than left public.
@app.get("/health/services", tags=["System"], dependencies=[Depends(get_admin_user)])
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
    # RT-18: these two start and loop but do no work today (the checker iterates an empty
    # list of positions; the scheduler reads a table nothing writes). Reporting ACTIVE, with a
    # claim of an armed halt, told operators a reconciliation safety net existed. Until RT-16
    # implements real venue reconciliation they are reported for what they are.
    for _noop in ("consistency_checker", "reconciliation_scheduler"):
        if services[_noop] == "ACTIVE":
            services[_noop] = "NOT_IMPLEMENTED"

    active_count   = sum(1 for s in services.values() if s == "ACTIVE")
    inactive_count = sum(1 for s in services.values() if s == "INACTIVE")
    failed_count   = sum(1 for s in services.values() if s == "FAILED")
    not_implemented_count = sum(1 for s in services.values() if s == "NOT_IMPLEMENTED")

    overall = "ok" if failed_count == 0 and inactive_count == 0 and not_implemented_count == 0 else (
              "degraded" if failed_count == 0 else "failed")

    # OBS-01: this snapshot is written ONCE, during lifespan startup, and never
    # refreshed. A service that died an hour ago still reports ACTIVE, so the answer is
    # not "are these services running" but "did these services start" — and those are
    # different questions that were being conflated.
    #
    # Refreshing it properly means each service publishing a heartbeat, which is what
    # `WorkerBase.report_health` now does for workers after OBS-04 (it was writing
    # nowhere at all). These four are not WorkerBase subclasses, so the honest fix
    # available here is to STOP PRESENTING A BOOT SNAPSHOT AS LIVE STATE: the age is
    # reported, and `as_of` says plainly what the reading is.
    _age_seconds = None
    _captured = _runtime_service_status.get("_captured_at")
    if _captured:
        try:
            _age_seconds = round(
                (datetime.now(timezone.utc)
                 - datetime.fromisoformat(_captured)).total_seconds(), 1
            )
        except Exception:  # noqa: BLE001
            _age_seconds = None

    return {
        "status": overall,
        "summary": {
            "active":   active_count,
            "inactive": inactive_count,
            "failed":   failed_count,
            "not_implemented": not_implemented_count,
        },
        "services": services,
        "raw": {k: v for k, v in runtime.items() if not k.startswith("_")},
        # The honesty fields. A consumer that treats `services` as live state is wrong,
        # and now has the information to know it.
        "as_of": "process_startup",
        "captured_at": _captured,
        "age_seconds": _age_seconds,
        "note": (
            "These values are a BOOT-TIME snapshot, not live state: they are written "
            "once during startup and never refreshed, so a service that has since died "
            "still reads ACTIVE. Use /health/ready for a traffic decision and "
            "worker:health:* (see WorkerBase.report_health) for live worker liveness."
        ),
    }


#  Liveness probe (Docker HEALTHCHECK / K8s liveness) 
@app.get("/health/live", tags=["System"])
async def health_live():
    """Minimal liveness probe — returns 200 if the process is up."""
    return {"status": "alive"}


#  Readiness probe (Docker HEALTHCHECK / K8s readiness) 
@app.get("/health/ready", tags=["System"])
async def health_ready():
    """OBS-01: readiness now MEASURES its required dependencies.

    This returned `{"status": "ready"}` unconditionally -- it checked nothing -- so an
    orchestrator would route production traffic to a pod with no database and no Redis.

    Required: the database (every authenticated request resolves identity and RLS through
    it) and Redis outside a declared local environment (it backs the rate limiter per
    API-03 and the distributed execution locks). Telemetry is NOT required: it degrades to
    a mock by design, and making readiness depend on an optional service turns a metrics
    outage into a total outage.

    **503 when not ready.** A 200 carrying a sad body is invisible to every load balancer,
    which is the whole reason the old version was harmless-looking and useless.

    Liveness (`/health/live`) stays dependency-free: a pod whose database is down is not a
    broken pod, and restarting it does not fix the database.
    """
    from backend_app.core.readiness import evaluate_readiness

    report = await evaluate_readiness()
    return JSONResponse(status_code=report.http_status, content=report.to_dict())


# CI-03: release provenance -- the backend counterpart to 06-frontend-deploy.yml's
# dist/release.json. That workflow already stamps the deployed UI with the commit it was
# built from; this reads back the SAME shape ({"sha", "built_at"}) for the backend image,
# baked in at build time by Dockerfile's GIT_SHA/BUILT_AT ARGs (set from 02-build.yml's own
# IMAGE_TAG -- the commit SHA already used to tag the image in ECR). This is deliberately
# the SHA of the running IMAGE, not of whatever source tree happens to be checked out --
# that is the comparison a "does the deployed frontend match the deployed backend" check
# actually needs. "unknown" (the Dockerfile ARG's own default) means the image was built
# without passing --build-arg, e.g. a local `docker build` outside this CI pipeline, not a
# broken endpoint.
#
# Registered at BOTH /release.json and /api/release.json. The real production CloudFront
# distribution (confirmed against the live account, not just terraform source) only proxies
# /api/*, /ws/*, /health*, /metrics* and /openapi.json to the ALB; every other path -- this
# includes a bare /release.json -- falls through to CloudFront's default behaviour, which
# serves the FRONTEND's own static dist/release.json from S3. So on the real public domain,
# GET https://<domain>/release.json can only ever return the frontend's own stamp -- the
# backend's endpoint at that exact path is unreachable through the real routing, silently
# defeating the FE/BE version-match comparison this pair of endpoints exists for. The /api/
# alias is what 06-frontend-deploy.yml's new comparison step (added alongside this) actually
# fetches; the bare /release.json stays for direct-to-backend callers (local dev, a health
# dashboard hitting the ALB/task directly) where no such collision exists.
def _release_info() -> dict:
    return {
        "sha": os.environ.get("GIT_SHA", "unknown"),
        "built_at": os.environ.get("BUILT_AT", "unknown"),
    }


@app.get("/release.json", tags=["System"])
async def release_info():
    return _release_info()


@app.get("/api/release.json", tags=["System"])
async def release_info_api():
    return _release_info()


@app.get("/api/system/trading-capabilities", tags=["System"])
async def system_trading_capabilities():
    """FE-03 / RT-15: tell the UI whether LIVE execution is available on this platform.

    ``live_trading_available`` is true only when the platform is in live mode AND live
    trading is explicitly enabled - the same two conditions the order gate enforces
    (``core.live_execution_gate.assert_platform_live_trading_enabled``). The gate also
    applies a per-tenant allowlist, which is deliberately not reported here: this is a
    public, unauthenticated probe and says nothing about any account. The UI uses it to
    avoid offering Live when the server would refuse it; the server remains the authority.
    """
    from backend_app.core.safety_config import (get_live_trading_confirmation,
                                                get_vyomquant_mode)

    mode = str(get_vyomquant_mode("safe") or "").strip().lower()
    enabled = str(get_live_trading_confirmation("false") or "").strip().lower() == "true"
    live_mode = mode in ("live", "live_trading")
    return {
        "mode": mode if mode in ("safe", "paper", "live", "live_trading") else "safe",
        "live_trading_available": bool(live_mode and enabled),
    }


@app.get("/api/system/readiness", tags=["System"], dependencies=[Depends(get_admin_user)])
async def system_readiness():
    """System readiness check — confirms critical dependencies are operational.

    OBS-01: this returned the literal string "HEALTHY" for database, redis, risk_engine,
    execution_engine and reconciliation, and `"status": "READY"` always. Nothing was
    probed. It now reports the same measured dependency set as `/health/ready` and
    carries the same 503.

    CLAIM-02: `certified_exchanges` below is NOT a measured fact either -- it is
    `exchange_certification.py`'s own count of venues it marks LEVEL_5, and that file's
    levels were themselves hardcoded overclaims until this finding corrected them (see
    its module docstring). This count is included for backwards compatibility with
    whatever reads this endpoint today; as of the correction it reads 0, honestly,
    because no venue has ever had a sandbox or live order placed against it
    (staging/EXCHANGE_SANDBOX_CERTIFICATION.md, Phase 10, 2026-10-10).

    API-07: this is a diagnostic duplicate of `/health/ready` (the actual LB/K8s gate,
    confirmed still public -- deploy-archive/k8s/ingress.yaml names /health as the ALB
    target, not this path) carrying extra operational detail. Gated behind get_admin_user.
    """
    from backend_app.core.exchange_certification import \
        get_exchange_certification_registry
    from backend_app.core.readiness import evaluate_readiness

    report = await evaluate_readiness()
    body = report.to_dict()

    try:
        registry = get_exchange_certification_registry()
        body["certified_exchanges"] = (
            registry.get_certification_matrix()["total_certified_level_5"]
        )
    except Exception as exc:  # noqa: BLE001
        # Informational only; it must not change the readiness verdict.
        body["certified_exchanges"] = None
        body["certified_exchanges_error"] = type(exc).__name__

    body["mode"] = (
        "DEV_MODE" if os.environ.get("DEV_MODE", "").lower() == "true" else "production"
    )
    body["timestamp"] = datetime.now(timezone.utc).isoformat()
    return JSONResponse(status_code=report.http_status, content=body)


@app.get("/api/system/health", tags=["System"], dependencies=[Depends(get_admin_user)])
async def system_health():
    """Comprehensive system health across runtime workers and dependencies.

    API-07: gated behind get_admin_user -- this is `health()`'s own, more detailed
    report, not the LB/K8s liveness target (that is the bare `/health`, still public).
    """
    return await health()


@app.get("/api/system/dependencies", tags=["System"], dependencies=[Depends(get_admin_user)])
async def system_dependencies():
    """Dependency health, measured.

    OBS-01: every value here was a hardcoded literal — `"HEALTHY"` for all five, plus
    fabricated detail (`"circuit_breaker": "ARMED"`, `"idempotency_guard": "ACTIVE"`)
    that was never read from anything. `risk_engine`, `execution_engine` and
    `reconciliation_scheduler` are in-process components rather than things we talk to, so
    reporting them as reachable dependencies was category error on top of fabrication;
    worker/service state is at `/health/services`.

    Always 200: this is a diagnostic, not a gate. `/health/ready` is the gate.
    """
    from backend_app.core.readiness import evaluate_readiness

    report = await evaluate_readiness()
    return {
        "dependencies": report.to_dict()["dependencies"],
        "ready": report.ready,
        "blocking": list(report.blocking),
        "checked_at": report.checked_at,
        "note": (
            "In-process components (risk engine, execution gateway, reconciliation "
            "scheduler) are reported at /health/services, not here: they are not "
            "external dependencies and cannot be probed by reachability."
        ),
    }


@app.get("/api/system/workers", tags=["System"], dependencies=[Depends(get_admin_user)])
async def system_workers():
    """Background workers operational status. API-07: gated behind get_admin_user."""
    return await health_services()


@app.get("/api/system/exchanges", tags=["System"], dependencies=[Depends(get_admin_user)])
async def system_exchanges():
    """Certified exchanges status matrix. API-07: gated behind get_admin_user."""
    from backend_app.core.exchange_certification import get_exchange_certification_registry
    return get_exchange_certification_registry().get_certification_matrix()


#  Stats endpoint
@app.get("/api/stats", tags=["Stats"])
async def get_stats():
    """Platform-wide trading statistics. Public and unauthenticated BY DESIGN -- this is
    not any one account's data (see ``algo22-terminal/src/design/pageFields.js``'s
    documentation of ``pages/Profile.jsx``'s *Automation Account Context* card, which
    already labels these four figures as the platform's aggregate, not the caller's).

    API-03. This used to call the PER-USER dashboard aggregator with a sentinel
    ``user={"id": "public"}`` identity that owns nothing -- most figures came back as
    that non-existent account's empty state, ``total_pnl`` was actually today's P&L
    under a mislabelled key, and a failed read answered 200 with every figure forced to
    0, indistinguishable from a genuinely idle platform. See
    ``backend_app.backend.platform_stats`` for the real, service-role aggregation this
    now calls instead, and why a failure here is a 503, not a false "all zero".
    """
    from backend_app.backend.platform_stats import (
        PlatformStatsUnavailable,
        get_platform_stats,
    )
    from backend_app.core.dependencies import get_service_supabase

    try:
        return await get_platform_stats(get_service_supabase())
    except PlatformStatsUnavailable as exc:
        logger.error(f"Platform stats unavailable: {exc}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "PLATFORM_STATS_UNAVAILABLE",
                "message": "Platform-wide statistics could not be computed.",
            },
        )


# NOTE: Auth routes (/api/auth/register, /api/auth/login) are registered via
# app.include_router(auth.router, prefix="/api/auth") above.
# Do NOT add @app.post("/api/auth/...") handlers here — they create duplicate
# route registrations that cause undefined routing behaviour.
