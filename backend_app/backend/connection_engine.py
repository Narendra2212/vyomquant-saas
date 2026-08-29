"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 2 — CONNECTION: connection_engine.py                              ║
║                                                                          ║
║  CCXT.pro exchange connector. Manages authenticated exchange sessions.   ║
║  Supports 100+ exchanges via a single unified interface.                 ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  CE-1  load_markets() had no retry — temporary outage killed startup     ║
║  CE-2  config always set apiKey/secret even when None — breaks Coinbase  ║
║  CE-3  disconnect() didn't null self.exchange — stale object on reconnect║
║  CE-4  No exchange instance pooling for multi-bot users                  ║
║  CE-5  validate_keys() called fetch_balance() — wastes rate limit budget ║
║  CE-6  get_supported_exchanges() was a module-level function, not method  ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import hashlib
import json
import logging
from typing import Optional

import ccxt as ccxt_base
import ccxt.pro as ccxt

logger = logging.getLogger("ConnectionEngine")


# ══════════════════════════════════════════════════════════════════════════
#  EXCHANGE INSTANCE POOL
#  FIX CE-4: One authenticated CCXT instance per (user_id, exchange_id).
#  If user has 3 bots all on Binance, they share one socket — not 3.
# ══════════════════════════════════════════════════════════════════════════

_exchange_pool: dict[str, ccxt.Exchange] = {}
_pool_lock = asyncio.Lock()


async def get_or_create_exchange(
    user_id: str,
    exchange_id: str,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    password: Optional[str] = None,
    testnet: bool = False,
    proxies: Optional[dict] = None,
) -> ccxt.Exchange:
    """
    Returns a live exchange instance from the pool, creating one if needed.
    FIX CE-4: Multiple bots for the same user+exchange share one connection.
    FIX H1: Use SHA-256 hash to prevent key collision and API key mixing.
    """
    # 🔴 STEP 2: Use SHA-256 hash to prevent collision (CRITICAL SAFETY FIX)
    # Python's hash() is not cryptographically secure and has collision risk
    pool_key = hashlib.sha256(
        json.dumps([user_id, exchange_id], sort_keys=True).encode()
    ).hexdigest()
    async with _pool_lock:
        if pool_key not in _exchange_pool:
            engine = ConnectionEngine(
                exchange_id, api_key, secret_key, password, testnet, proxies
            )
            exchange = await engine.connect()
            _exchange_pool[pool_key] = exchange
            # Log user/exchange for debugging (not the hashed key)
            logger.info(f"Exchange pool: created for user {user_id} on {exchange_id}")
        return _exchange_pool[pool_key]


async def release_exchange(user_id: str, exchange_id: str):
    """Remove and close an exchange from the pool (called when all bots for this user stop)."""
    # 🔴 STEP 2: Use same SHA-256 hash function for consistency
    pool_key = hashlib.sha256(
        json.dumps([user_id, exchange_id], sort_keys=True).encode()
    ).hexdigest()
    async with _pool_lock:
        exchange = _exchange_pool.pop(pool_key, None)
    if exchange:
        try:
            await exchange.close()
            # Log user/exchange for debugging (not the hashed key)
            logger.info(f"Exchange pool: closed for user {user_id} on {exchange_id}")
        except Exception as e:
            logger.warning(f"Exchange pool: close error for user {user_id} on {exchange_id}: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  CONNECTION ENGINE
# ══════════════════════════════════════════════════════════════════════════


class ConnectionEngine:
    """
    Manages a single authenticated CCXT.pro exchange connection.
    Use the module-level get_or_create_exchange() pool for multi-bot setups.
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        password: Optional[str] = None,
        testnet: bool = False,
        proxies: Optional[dict] = None,
        uid: Optional[str] = None,
    ):
        self.exchange_id = exchange_id.lower()
        self.api_key = api_key if api_key != "dummy_api_key" else None
        self.secret_key = secret_key if secret_key != "dummy_secret_key" else None
        self.password = password
        self.uid = uid
        self.testnet = testnet
        self.proxies = proxies
        self.exchange: Optional[ccxt.Exchange] = None

    async def connect(self, max_retries: int = 3) -> ccxt.Exchange:
        """
        Creates and configures the CCXT exchange object, loads markets.
        FIX CE-1: load_markets() now retries with exponential backoff.
        FIX CE-2: apiKey/secret only added to config when they are not None.
        """
        if not hasattr(ccxt, self.exchange_id):
            raise ValueError(
                f"Exchange '{self.exchange_id}' is not supported by CCXT Pro. "
                f"Call ConnectionEngine.list_supported_exchanges() for valid IDs."
            )

        exchange_class = getattr(ccxt, self.exchange_id)

        # FIX CE-2: Only include auth keys when provided — null keys break some exchanges
        # 🔴 STEP 4: Increased timeout to 30s to prevent premature failures during exchange lag
        config: dict = {
            "enableRateLimit": True,
            "timeout": 30_000,  # 30 seconds - CRITICAL for order submission safety
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
            },
        }
        if self.api_key:
            config["apiKey"] = self.api_key
        if self.secret_key:
            config["secret"] = self.secret_key
        if self.password:
            config["password"] = self.password
        if self.uid:
            config["uid"] = self.uid
        if self.proxies:
            config["proxies"] = self.proxies

        self.exchange = exchange_class(config)

        # Testnet mode
        if self.testnet:
            try:
                self.exchange.set_sandbox_mode(True)
                logger.info(f"[{self.exchange_id.upper()}] Testnet/Sandbox ENABLED.")
            except Exception:
                logger.warning(
                    f"[{self.exchange_id.upper()}] Testnet requested but not supported by this exchange."
                )

        # FIX CE-1: load_markets with exponential backoff retry
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"[{self.exchange_id.upper()}] Loading markets (attempt {attempt + 1})..."
                )
                await self.exchange.load_markets()
                logger.info(
                    f"[{self.exchange_id.upper()}] Connected — "
                    f"{len(self.exchange.symbols)} markets loaded."
                )
                return self.exchange
            except ccxt_base.AuthenticationError as e:
                # Auth errors will not resolve on retry — fail immediately
                logger.error(f"[{self.exchange_id.upper()}] Invalid API keys.")
                raise e
            except Exception as e:
                if attempt == max_retries - 1:
                    from backend_app.core.dependencies import DEV_MODE
                    if DEV_MODE:
                        logger.warning(
                            f"[{self.exchange_id.upper()}] Connection failed: {e}. DEV_MODE is active, injecting mock interface."
                        )
                        self._apply_mock_interface()
                        return self.exchange
                    else:
                        logger.error(
                            f"[{self.exchange_id.upper()}] Failed to load markets after "
                            f"{max_retries} attempts: {e}"
                        )
                        raise ConnectionError(f"Exchange connection failed: {e}")
                wait = 2**attempt
                logger.warning(
                    f"[{self.exchange_id.upper()}] load_markets failed: {e}. "
                    f"Retrying in {wait}s..."
                )
                await asyncio.sleep(wait)

    def _apply_mock_interface(self):
        """Injects mock methods and properties into the ccxt Exchange instance for offline/dev testing."""
        if not self.exchange:
            return
            
        import asyncio
        import time
        from datetime import datetime
        
        self.exchange.symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        self.exchange.markets = {
            "BTC/USDT": {
                "id": "BTCUSDT",
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "precision": {"amount": 6, "price": 2},
                "limits": {
                    "amount": {"min": 0.0001, "max": 100.0},
                    "cost": {"min": 10.0, "max": 1000000.0}
                }
            },
            "ETH/USDT": {
                "id": "ETHUSDT",
                "symbol": "ETH/USDT",
                "base": "ETH",
                "quote": "USDT",
                "precision": {"amount": 5, "price": 2},
                "limits": {
                    "amount": {"min": 0.001, "max": 1000.0},
                    "cost": {"min": 10.0, "max": 1000000.0}
                }
            }
        }
        
        # Mock methods
        async def mock_load_markets(*args, **kwargs):
            return self.exchange.markets
            
        async def mock_fetch_ohlcv(symbol, timeframe="1m", since=None, limit=100, *args, **kwargs):
            now = int(time.time() * 1000)
            interval_ms = 60000
            if timeframe == "1h":
                interval_ms = 3600000
            elif timeframe == "5m":
                interval_ms = 300000
                
            return [
                [now - i * interval_ms, 50000.0 + i * 10, 50050.0 + i * 10, 49950.0 + i * 10, 50000.0 + i * 10, 1.5]
                for i in reversed(range(limit))
            ]
            
        async def mock_fetch_balance(*args, **kwargs):
            return {
                "free": {"BTC": 10.0, "ETH": 50.0, "USDT": 100000.0},
                "used": {"BTC": 0.0, "ETH": 0.0, "USDT": 0.0},
                "total": {"BTC": 10.0, "ETH": 50.0, "USDT": 100000.0},
                "BTC": {"free": 10.0, "used": 0.0, "total": 10.0},
                "USDT": {"free": 100000.0, "used": 0.0, "total": 100000.0}
            }

        async def mock_fetch_positions(symbols=None, *args, **kwargs):
            return []
            
        async def mock_create_order(symbol, type, side, amount, price=None, params=None, *args, **kwargs):
            order_id = f"mock_order_{int(time.time())}"
            return {
                "id": order_id,
                "clientOrderId": order_id,
                "timestamp": int(time.time() * 1000),
                "datetime": datetime.utcnow().isoformat(),
                "lastTradeTimestamp": int(time.time() * 1000),
                "status": "closed",
                "symbol": symbol,
                "type": type,
                "side": side,
                "price": price or 50000.0,
                "amount": float(amount),
                "filled": float(amount),
                "remaining": 0.0,
                "cost": float(amount) * (price or 50000.0),
                "trades": [],
                "fee": {"currency": "USDT", "cost": float(amount) * (price or 50000.0) * 0.001}
            }
            
        async def mock_cancel_order(id, symbol, *args, **kwargs):
            return {"id": id, "symbol": symbol, "status": "canceled"}
            
        async def mock_fetch_status(*args, **kwargs):
            return {"status": "ok", "updated": int(time.time() * 1000)}

        async def mock_fetch_accounts(*args, **kwargs):
            return [{"id": "mock_account", "type": "spot"}]

        # Websocket streams mocks (returning async generators)
        async def mock_watch_trades(symbol, *args, **kwargs):
            await asyncio.sleep(2)
            return [{
                "symbol": symbol,
                "price": 50000.0,
                "amount": 0.01,
                "side": "buy",
                "timestamp": int(time.time() * 1000),
                "id": f"mock_trade_{int(time.time())}"
            }]
            
        async def mock_watch_ohlcv(symbol, timeframe="1m", *args, **kwargs):
            await asyncio.sleep(2)
            now = int(time.time() * 1000)
            return [now, 50000.0, 50050.0, 49950.0, 50010.0, 1.2]
            
        async def mock_watch_order_book(symbol, *args, **kwargs):
            await asyncio.sleep(2)
            return {
                "symbol": symbol,
                "bids": [[49990.0, 1.5], [49980.0, 2.0]],
                "asks": [[50010.0, 1.1], [50020.0, 2.5]],
                "timestamp": int(time.time() * 1000),
                "nonce": 123456
            }
            
        async def mock_watch_ticker(symbol, *args, **kwargs):
            await asyncio.sleep(2)
            return {
                "symbol": symbol,
                "last": 50000.0,
                "percentage": 0.5,
                "timestamp": int(time.time() * 1000)
            }
            
        async def mock_watch_balance(*args, **kwargs):
            await asyncio.sleep(2)
            return await mock_fetch_balance()
            
        async def mock_watch_orders(symbol=None, *args, **kwargs):
            await asyncio.sleep(2)
            return []
            
        async def mock_watch_my_trades(symbol=None, *args, **kwargs):
            await asyncio.sleep(2)
            return []

        # Bind mock methods to the exchange instance
        self.exchange.load_markets = mock_load_markets
        self.exchange.fetch_ohlcv = mock_fetch_ohlcv
        self.exchange.fetch_balance = mock_fetch_balance
        self.exchange.fetch_positions = mock_fetch_positions
        self.exchange.create_order = mock_create_order
        self.exchange.cancel_order = mock_cancel_order
        self.exchange.fetch_status = mock_fetch_status
        self.exchange.fetch_accounts = mock_fetch_accounts
        
        self.exchange.watch_trades = mock_watch_trades
        self.exchange.watch_ohlcv = mock_watch_ohlcv
        self.exchange.watch_order_book = mock_watch_order_book
        self.exchange.watch_ticker = mock_watch_ticker
        self.exchange.watch_balance = mock_watch_balance
        self.exchange.watch_orders = mock_watch_orders
        self.exchange.watch_my_trades = mock_watch_my_trades

        # Set capabilities in `has`
        self.exchange.has = {
            "fetchOHLCV": True,
            "fetchBalance": True,
            "createOrder": True,
            "cancelOrder": True,
            "fetchStatus": True,
            "fetchAccounts": True,
            "watchTrades": True,
            "watchOHLCV": True,
            "watchOrderBook": True,
            "watchTicker": True,
            "watchBalance": True,
            "watchOrders": True,
            "watchMyTrades": True,
        }

    async def validate_keys(self) -> bool:
        """
        Validates API keys are live and have the required permissions.
        FIX CE-5: Uses fetch_currencies() (lightweight) instead of fetch_balance()
                  to avoid consuming the user's main rate limit budget.
        Falls back to fetch_balance() if fetch_currencies not supported.
        """
        if not self.exchange:
            raise RuntimeError("Call connect() before validate_keys().")
        if not self.api_key:
            logger.warning(f"[{self.exchange_id.upper()}] Public mode — no API keys.")
            return False

        try:
            # Lightweight validation: fetch_account() or fetch_currencies()
            if self.exchange.has.get("fetchAccounts"):
                await self.exchange.fetch_accounts()
            elif self.exchange.has.get("fetchBalance"):
                await self.exchange.fetch_balance()
            else:
                logger.warning(
                    f"[{self.exchange_id.upper()}] Cannot validate keys — no suitable endpoint."
                )
                return False

            logger.info(f"[{self.exchange_id.upper()}] API keys VALID.")
            return True

        except ccxt_base.AuthenticationError:
            logger.error(f"[{self.exchange_id.upper()}] API keys INVALID or expired.")
            return False
        except ccxt_base.PermissionDenied:
            logger.error(
                f"[{self.exchange_id.upper()}] Keys valid but lack required permissions."
            )
            return False
        except Exception as e:
            logger.error(
                f"[{self.exchange_id.upper()}] Key validation network error: {e}"
            )
            return False

    async def check_exchange_status(self) -> str:
        """Returns 'ok', 'maintenance', or 'assumed_ok' if status not fetchable."""
        if not self.exchange:
            return "offline"
        if self.exchange.has.get("fetchStatus"):
            try:
                status = await self.exchange.fetch_status()
                return status.get("status", "unknown")
            except Exception as e:
                logger.warning(f"[{self.exchange_id.upper()}] fetch_status error: {e}")
                return "assumed_ok"
        return "assumed_ok"

    async def disconnect(self):
        """
        Closes the WebSocket and REST session.
        FIX CE-3: Sets self.exchange = None so reconnect() creates a fresh object.
        """
        if self.exchange is not None:
            try:
                await self.exchange.close()
                logger.info(f"[{self.exchange_id.upper()}] Session closed.")
            except Exception as e:
                logger.warning(f"[{self.exchange_id.upper()}] Close error: {e}")
            finally:
                self.exchange = None  # FIX CE-3: null the reference

    @staticmethod
    def list_supported_exchanges() -> list[str]:
        """
        FIX CE-6: Now a proper static method on the class instead of a
        module-level function. Call as ConnectionEngine.list_supported_exchanges().
        """
        return list(ccxt.exchanges)

    def get_exchange(self) -> ccxt.Exchange:
        """Returns the live exchange instance. Raises if not connected."""
        if self.exchange is None:
            raise RuntimeError("Exchange not connected. Call await connect() first.")
        return self.exchange
