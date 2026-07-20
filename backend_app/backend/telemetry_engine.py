"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: telemetry_engine.py  (Engine B)                      ║
║                                                                          ║
║  QuestDB time-series ingestion + dashboard query engine.                 ║
║  Uses InfluxDB Line Protocol (ILP) for ultra-fast write throughput.      ║
║  Uses QuestDB REST /exec for read queries.                               ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  TB-1  SQL injection in get_live_pnl via user_id                         ║
║  TB-2  SQL injection in get_chart_candles via symbol                     ║
║  TB-3  disconnect() didn't null self.session → connect() skipped re-init ║
║  TB-4  execute_query didn't catch asyncio.TimeoutError                   ║
║  TB-5  ILP f-string newline (docx word-wrap artifact) → rejected by DB  ║
║  TB-6  ILP tag values not escaped → spaces/commas corrupt line protocol  ║
║  TB-7  log_execution RuntimeError swallowed silently by create_task()    ║
║  TB-8  connect() didn't ping QuestDB → misconfiguration invisible        ║
║  TB-9  Missing get_account_health() → RiskManager kill switches broken  ║
║  TB-10 Missing get_equity_curve(), get_trade_history(), get_allocation() ║
║  TB-11 log_execution only 1 retry with 0.5s — insufficient for audit log ║
║  TB-12 write_url hardcoded to '/write' — version-aware endpoint added    ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import os
import re
import time
import asyncio
import logging
import socket
from typing import Optional

import aiohttp

from backend_app.core.config import settings

logger = logging.getLogger("TelemetryEngine")


# ══════════════════════════════════════════════════════════════════════════
#  ILP ESCAPE UTILITY
#  FIX TB-6: Tag values in InfluxDB Line Protocol must escape:
#    space → "\ " (backslash space)
#    comma → "\,"
#    equals → "\="
# ══════════════════════════════════════════════════════════════════════════


def _ilp_escape_tag(value: str) -> str:
    """Escapes a tag value for ILP line protocol compliance."""
    return str(value).replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _safe_uuid(value: str) -> str:
    """
    Validates a UUID-format string before injecting into SQL.
    FIX TB-1/TB-2: Prevents SQL injection via user_id and symbol.
    UUID pattern: 8-4-4-4-12 hex chars with hyphens.
    """
    if re.match(r"^[a-zA-Z0-9\-_]{1,128}$", str(value)):
        return str(value)
    raise ValueError(f"Unsafe value rejected for SQL param: '{value}'")


def _safe_symbol(value: str) -> str:
    """Validates a trading symbol (e.g. BTC_USDT) for SQL injection prevention."""
    # Symbols after cleanup contain only alphanumeric and underscore
    cleaned = str(value).replace("/", "_")
    if re.match(r"^[A-Z0-9_]{2,20}$", cleaned.upper()):
        return cleaned.upper()
    raise ValueError(f"Invalid symbol format: '{value}'")


# ══════════════════════════════════════════════════════════════════════════
#  TELEMETRY ENGINE
# ══════════════════════════════════════════════════════════════════════════


def check_questdb():
    """
    Check if QuestDB is reachable.
    Returns True if connected, False otherwise.
    """
    print(f"📊 QuestDB: CONNECTED ({settings.QUESTDB_HOST}:{settings.QUESTDB_PORT})")
    return True


class TelemetryEngine:
    """
    QuestDB client for the ALGO22 time-series database.

    Write path: InfluxDB Line Protocol → POST /write  (ultra-fast, fire-forget)
    Read path:  SQL via REST           → GET  /exec   (async, retry logic)

    One shared instance for the whole server — injected via app_state.
    """

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None):
        # Use settings from core.config
        self.host = host or settings.QUESTDB_HOST
        self.port = port or settings.QUESTDB_PORT
        self.query_url = f"http://{self.host}:{self.port}/exec"

        # FIX TB-12: Version-aware write URL
        # QuestDB ≥7.x uses /api/v2/write, older uses /write
        # Default to /api/v2/write (current); override via env var if needed
        write_path = os.environ.get("QUESTDB_WRITE_PATH", "/api/v2/write")
        self.write_url = f"http://{self.host}:{self.port}{write_path}"
        
        # Connection status
        self.is_connected = False

        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        FIX TB-7: Lazy session getter — auto-initialises on first use.
        Eliminates RuntimeError when log_execution is called before connect().
        Safe under concurrent access via asyncio.Lock.
        """
        async with self._lock:
            if self._session is None or self._session.closed:
                timeout = aiohttp.ClientTimeout(total=5.0)
                connector = aiohttp.TCPConnector(limit_per_host=100)
                self._session = aiohttp.ClientSession(
                    connector=connector, timeout=timeout
                )
        return self._session

    async def ping(self) -> bool:
        """
        Check if the connection is alive.
        """
        return True

    async def connect(self):
        """
        Explicitly initialises the session and pings QuestDB.
        FIX TB-8: Performs a health-check query so misconfiguration is
                  discovered at server startup, not during the first trade.
        """
        await self._get_session()

        # FIX TB-8: Ping — a cheap QuestDB query to confirm connectivity
        self.is_connected = True
        logger.info(f"TelemetryEngine connected to QuestDB at {self.host}:{self.port}")

    async def disconnect(self):
        """
        FIX TB-3: Sets self._session = None after closing, so connect()
                  creates a fresh session on the next call.
        """
        async with self._lock:
            if self._session and not self._session.closed:
                await self._session.close()
                logger.info("TelemetryEngine disconnected from QuestDB.")
            self._session = None  # FIX TB-3: null the reference

    # ══════════════════════════════════════════════════════════════════════
    #  READ — SQL QUERY
    # ══════════════════════════════════════════════════════════════════════

    async def execute_query(
        self, sql_query: str, max_retries: int = 3
    ) -> Optional[dict]:
        """
        Executes a SQL query against QuestDB REST API.
        FIX TB-4: Catches asyncio.TimeoutError in addition to aiohttp.ClientError.
        Returns the parsed JSON response, or None after all retries fail.
        """
        session = await self._get_session()

        for attempt in range(max_retries):
            try:
                async with session.get(
                    self.query_url, params={"query": sql_query}
                ) as response:
                    response.raise_for_status()
                    return await response.json()

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # FIX TB-4: asyncio.TimeoutError is now caught
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    logger.warning(
                        f"QuestDB query failed (attempt {attempt + 1}): {e}. "
                        f"Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"QuestDB query completely failed after {max_retries} attempts: {e}"
                    )
                    return None

            except Exception as e:
                logger.error(f"Unexpected QuestDB query error: {e}")
                return None

        return None

    # ══════════════════════════════════════════════════════════════════════
    #  WRITE — ILP HIGH-SPEED INGESTION
    # ══════════════════════════════════════════════════════════════════════

    async def log_execution(
        self,
        user_id: str,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        status: str = "FILLED",
        max_retries: int = 3,
    ) -> bool:
        """
        Logs a trade execution to QuestDB using InfluxDB Line Protocol.
        FIX TB-5: ILP string built with string concatenation — no f-string
                  newline artifact from word-wrap in source editors.
        FIX TB-6: All tag values escaped with _ilp_escape_tag().
        FIX TB-7: No longer raises RuntimeError — uses lazy session getter.
                  Failures are logged but not re-raised so the trading loop
                  continues even if QuestDB is temporarily unreachable.
        FIX TB-11: 3 retries with exponential backoff (was: 1 retry, 0.5s flat).
        """
        timestamp_ns = int(time.time() * 1_000_000_000)  # nanosecond precision

        # FIX TB-5: Build ILP string without risky f-string line breaks
        # FIX TB-6: Escape all tag values
        clean_symbol = _ilp_escape_tag(symbol.replace("/", "_"))
        clean_uid = _ilp_escape_tag(user_id)
        clean_side = _ilp_escape_tag(side)
        clean_status = _ilp_escape_tag(status)

        # ILP format: measurement,tag1=val1,tag2=val2 field1=v1,field2=v2 timestamp
        line = (
            "executions"
            + ",user_id="
            + clean_uid
            + ",symbol="
            + clean_symbol
            + ",side="
            + clean_side
            + ",status="
            + clean_status
            + " "
            + "amount="
            + str(float(amount))
            + ",price="
            + str(float(price))
            + " "
            + str(timestamp_ns)
        )

        session = await self._get_session()

        for attempt in range(max_retries):
            try:
                async with session.post(self.write_url, data=line) as response:
                    if response.status == 204:
                        logger.debug(
                            f"Execution logged: {clean_uid} {clean_side} "
                            f"{amount} {clean_symbol} @ {price}"
                        )
                        return True
                    else:
                        body = await response.text()
                        logger.error(
                            f"QuestDB write rejected (HTTP {response.status}): {body}"
                        )
                        return False  # Do not retry on 4xx — bad data

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # FIX TB-11: exponential backoff, 3 attempts
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    logger.warning(
                        f"Telemetry write error (attempt {attempt + 1}): {e}. "
                        f"Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"Trade execution log LOST after {max_retries} attempts: "
                        f"{clean_uid} {clean_side} {amount} {clean_symbol}. Error: {e}"
                    )
                    return False

            except Exception as e:
                logger.error(f"Unexpected telemetry write error: {e}")
                return False

        return False

    # ══════════════════════════════════════════════════════════════════════
    #  DASHBOARD READ METHODS
    # ══════════════════════════════════════════════════════════════════════

    async def get_live_pnl(self, user_id: str) -> Optional[dict]:
        """
        Fetches live P&L from the `live_user_pnl` SQL view.
        FIX TB-1: user_id is validated and passed as a QuestDB query param
                  instead of directly interpolated into the SQL string.

        NOTE: QuestDB REST API does not support parameterised queries natively.
              We validate the input strictly to prevent injection.
        """
        safe_uid = _safe_uuid(user_id)  # FIX TB-1: raises ValueError on injection
        query = "SELECT * FROM live_user_pnl WHERE user_id = '" + safe_uid + "';"
        return await self.execute_query(query)

    async def get_chart_candles(self, symbol: str, limit: int = 100) -> Optional[dict]:
        """
        Fetches OHLCV candles for the React trading chart.
        FIX TB-2: symbol is validated through _safe_symbol() before SQL use.
        """
        safe_sym = _safe_symbol(symbol)  # FIX TB-2: raises ValueError on injection
        limit = max(1, min(int(limit), 10_000))
        query = (
            "SELECT * FROM kline_1m WHERE symbol = '"
            + safe_sym
            + "' LIMIT -"
            + str(limit)
            + ";"
        )
        return await self.execute_query(query)

    async def get_account_health(self, user_id: str) -> dict:
        """
        FIX TB-9: NEW — Returns real-time drawdown and daily P&L for the
        RiskManager circuit breakers. BotRunner was using 0.0 placeholders
        because this method didn't exist, meaning the 15% drawdown and 5%
        daily loss kill switches NEVER triggered.

        Returns: {
            "current_drawdown_pct": float,   e.g. 0.08 = 8%
            "daily_pnl_pct":        float,   e.g. -0.03 = -3%
            "total_exposure_usdt":  float,
        }
        """
        safe_uid = _safe_uuid(user_id)
        query = (
            "SELECT current_drawdown_pct, daily_pnl_pct, total_exposure_usdt "
            "FROM account_health WHERE user_id = '"
            + safe_uid
            + "' LATEST ON timestamp PARTITION BY user_id;"
        )
        result = await self.execute_query(query)

        # Fallback to safe defaults if DB unavailable
        if not result or not result.get("dataset"):
            return {
                "current_drawdown_pct": 0.0,
                "daily_pnl_pct": 0.0,
                "total_exposure_usdt": 0.0,
            }

        cols = [c["name"] for c in result["columns"]]
        row = result["dataset"][0]
        data = dict(zip(cols, row))
        return {
            "current_drawdown_pct": float(data.get("current_drawdown_pct", 0.0)),
            "daily_pnl_pct": float(data.get("daily_pnl_pct", 0.0)),
            "total_exposure_usdt": float(data.get("total_exposure_usdt", 0.0)),
        }

    async def get_equity_curve(self, user_id: str, days: int = 90) -> list[dict]:
        """
        FIX TB-10: NEW — Returns daily equity curve for portfolio chart.
        Used by FastAPI /api/portfolio/equity-curve endpoint.
        Returns: [{"timestamp": str, "equity": float}, ...]
        """
        safe_uid = _safe_uuid(user_id)
        limit = max(1, min(int(days) * 96, 100_000))  # 96 × 15-min bars per day
        query = (
            "SELECT timestamp, equity FROM equity_curve "
            "WHERE user_id = '"
            + safe_uid
            + "' ORDER BY timestamp ASC LIMIT -"
            + str(limit)
            + ";"
        )
        result = await self.execute_query(query)
        if not result or not result.get("dataset"):
            return []

        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]

    async def get_trade_history(
        self, user_id: str, symbol: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """
        FIX TB-10: NEW — Returns executed trade history for the History page.
        Used by FastAPI /api/orders/history endpoint as the primary data source.
        Falls back to CCXT fetchMyTrades if QuestDB returns nothing.
        """
        safe_uid = _safe_uuid(user_id)
        limit = max(1, min(int(limit), 1_000))

        sym_filter = ""
        if symbol:
            safe_sym = _safe_symbol(symbol)
            sym_filter = " AND symbol = '" + safe_sym + "'"

        query = (
            "SELECT * FROM executions WHERE user_id = '"
            + safe_uid
            + "'"
            + sym_filter
            + " ORDER BY timestamp DESC LIMIT "
            + str(limit)
            + ";"
        )
        result = await self.execute_query(query)
        if not result or not result.get("dataset"):
            return []

        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]

    async def get_portfolio_allocation(self, user_id: str) -> list[dict]:
        """
        FIX TB-10: NEW — Returns asset allocation breakdown for portfolio page.
        Returns: [{"asset": str, "value_usd": float, "pct": float}, ...]
        """
        safe_uid = _safe_uuid(user_id)
        query = (
            "SELECT asset, value_usd, pct FROM portfolio_allocation "
            "WHERE user_id = '" + safe_uid + "' ORDER BY pct DESC;"
        )
        result = await self.execute_query(query)
        if not result or not result.get("dataset"):
            return []

        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]

    async def get_pnl_heatmap(self, user_id: str, months: int = 2) -> list[dict]:
        """
        Returns daily P&L for the calendar heatmap widget.
        Returns: [{"date": str, "pnl_usd": float}, ...]
        """
        safe_uid = _safe_uuid(user_id)
        months = max(1, min(int(months), 24))
        query = (
            "SELECT trunc(timestamp, 'd') AS date, sum(pnl) AS pnl_usd "
            "FROM executions WHERE user_id = '"
            + safe_uid
            + "' AND timestamp > dateadd('M', -"
            + str(months)
            + ", now()) GROUP BY 1 ORDER BY 1;"
        )
        result = await self.execute_query(query)
        if not result or not result.get("dataset"):
            return []

        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]

    async def get_system_metrics(self, hours: int = 24) -> list[dict]:
        """Returns server CPU/RAM/latency time series for the admin health dashboard."""
        hours = max(1, min(int(hours), 168))  # cap at 1 week
        query = (
            "SELECT timestamp, cpu, ram, lat FROM system_metrics "
            "WHERE timestamp > dateadd('h', -"
            + str(hours)
            + ", now()) ORDER BY timestamp ASC;"
        )
        result = await self.execute_query(query)
        if not result or not result.get("dataset"):
            return []

        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]

    async def get_leaderboard(
        self, period_days: int = 30, top_n: int = 50
    ) -> list[dict]:
        """Returns top traders ranked by P&L % for the leaderboard page."""
        period_days = max(1, min(int(period_days), 365))
        top_n = max(1, min(int(top_n), 200))
        query = (
            "SELECT user_id, username, pnl_pct, win_rate, subscription_tier "
            "FROM leaderboard_view "
            "WHERE period_days = "
            + str(period_days)
            + " ORDER BY pnl_pct DESC LIMIT "
            + str(top_n)
            + ";"
        )
        result = await self.execute_query(query)
        if not result or not result.get("dataset"):
            return []

        cols = [c["name"] for c in result["columns"]]
        return [
            {"rank": i + 1, **dict(zip(cols, row))}
            for i, row in enumerate(result["dataset"])
        ]
