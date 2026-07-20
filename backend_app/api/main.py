"""
╔══════════════════════════════════════════════════════════════════════════╗
║  ALGO22 — FASTAPI MASTER SERVER                                          ║
║  Wires every backend engine into a production-grade async HTTP + WS API  ║
║                                                                          ║
║  ENGINES:                                                                ║
║  A  SecurityVault    — AES-256 key vault + Supabase auth                 ║
║  B  TelemetryEngine  — QuestDB time-series R/W                           ║
║  C  RiskManager      — Institutional circuit breaker                     ║
║  D  ConnectionEngine — CCXT.pro exchange connector                       ║
║  E  DataEngine       — WebSocket market + private data streams           ║
║  F  OrderEngine      — CCXT order execution + retry                      ║
║  G  DataProcessor    — Numba tick→candle builder                         ║
║  H  StrategyEngine   — Blueprint evaluator (safe eval)                   ║
║  I  BacktestEngine   — VectorBT + ML backtest runner                     ║
║  J  XGBoostBlock     — ML model training                                 ║
║  K  BotRunner        — Master execution orchestrator                     ║
║  L  FleetManager     — Multi-user bot lifecycle manager                  ║
║  M  AlertEngine      — Discord/Telegram webhook dispatcher               ║
║                                                                          ║
║  RUN:  uvicorn main:app --host 0.0.0.0 --port 8000 --reload             ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
import sys
import os

# Force stdout/stderr to be UTF-8 with fallback replacement to prevent Unicode crashes on Windows
if sys.platform.startswith('win'):
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

# ── Ensure project root is in path for imports ────────────────────────────
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# ── Router imports ─────────────────────────────────────────────────────────
from backend_app.routers import (
    auth,
    exchange,
    market,
    orders,
    strategies,
    portfolio,
    risk,
    user,
    admin,
    billing,
    security,
    analytics,
    support,
)
from backend_app.api_ws.ws_routes import ws_router
from backend_app.core.state import app_state

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s — %(message)s"
)
logger = logging.getLogger("Algo22")


# ── DEV MODE: Detect development mode ─────────────────────────────────────
import os
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true" or \
           os.environ.get("ENV", "").lower() == "development"


# ── Lifespan: boot + graceful shutdown ─────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:  Initialize TelemetryEngine (QuestDB), AlertEngine, FleetManager.
    Shutdown: Gracefully stop all BotRunner tasks via FleetManager.shutdown_all().
    """
    logger.info(f"🚀 ALGO22 server starting up... (DEV_MODE={DEV_MODE})")

    # Safety config unfreezing based on AERORA_MODE
    from backend_app.core.safety_config import ExecutionFlags
    aerora_mode = os.getenv("AERORA_MODE", "safe").lower()
    logger.info(f"🔒 Safety configuration check: AERORA_MODE={aerora_mode}")
    if aerora_mode == "paper":
        ExecutionFlags.enable_paper_trading()
    elif aerora_mode == "live":
        ExecutionFlags.enable_live_trading()
    else:
        logger.warning("🚨 Running in SAFE mode (read-only). Orders will be BLOCKED.")

    # Engine B — QuestDB connection pool
    try:
        await app_state.telemetry.connect()
        logger.info("📊 TelemetryEngine connected to QuestDB")
    except Exception as e:
        logger.error(f"Failed to connect to QuestDB: {e}")
        raise

    # Engine M — Alert Engine (Discord/Telegram)
    logger.info("🔔 AlertEngine armed")

    # Engine L — FleetManager online (safe even if mock)
    try:
        max_bots = getattr(app_state.fleet, 'MAX_SYSTEM_BOTS', 10)
        logger.info(f"🤖 FleetManager online — capacity: {max_bots} bots")
    except Exception as e:
        logger.warning(f"FleetManager status check failed: {e}")

    # Send startup notification
    try:
        await app_state.alert.send_info("ALGO22 server started successfully.")
    except Exception as e:
        logger.error(f"Failed to send startup alert: {e}")

    logger.info("✅ Server startup complete. Ready for requests.")
    yield  # ← Server is live and accepting requests here

    # ── Graceful shutdown ──────────────────────────────────────────────────
    logger.info("🛑 Shutdown initiated — terminating all bots...")

    # Engine L — stop all running BotRunner tasks safely
    try:
        await app_state.fleet.shutdown_all()
    except Exception as e:
        logger.warning(f"Fleet shutdown error (ignored): {e}")

    # Engine B — close QuestDB connection pool
    try:
        await app_state.telemetry.disconnect()
    except Exception as e:
        logger.warning(f"QuestDB disconnect error (ignored): {e}")

    logger.info("✅ All engines stopped. Server offline.")


# ── FastAPI app ────────────────────────────────────────────────────────────
app = FastAPI(
    title="ALGO22 Quantitative Trading API",
    description="Industrial-grade algorithmic trading backend — Aerora Dynamics",
    version="2.4.0",
    lifespan=lifespan,
)

# ── CORS — origins are env-var driven; localhost is NOT hardcoded to prevent leakage. ─
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

# ── Mount REST routers ─────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(exchange.router, prefix="/api/exchanges", tags=["Exchange Vault"])
app.include_router(market.router, prefix="/api/market", tags=["Market Data"])
app.include_router(orders.router, prefix="/api/orders", tags=["Order Execution"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["Strategies"])
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["Portfolio"])
app.include_router(risk.router, prefix="/api/risk", tags=["Risk Manager"])
app.include_router(user.router, prefix="/api", tags=["User"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin / God Mode"])
app.include_router(billing.router, prefix="/api/billing", tags=["Billing"])
app.include_router(security.router, prefix="/api/security", tags=["Security"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(support.router, prefix="/api/support", tags=["Support"])

# ── Mount WebSocket router ─────────────────────────────────────────────────
app.include_router(ws_router)


# ── Health probe (Kubernetes / load balancer) ──────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    fleet_status = app_state.fleet.get_status()
    return {
        "status": "ok",
        "active_bots": fleet_status["active_bots_count"],
        "capacity": fleet_status["usage_pct"],
        "questdb": "connected",
    }


# ── E2E Test Auth Validation Endpoints ─────────────────────────────────────
from backend_app.core.dependencies import get_current_user



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

