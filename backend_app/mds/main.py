import asyncio
import json
import logging

import ccxt.pro as ccxtpro
from aiohttp import web

from backend_app.core.cache import redis_manager

logger = logging.getLogger("MDS")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

active_streams = {} # (exchange_id, symbol) -> asyncio.Task
exchange_instances = {}

# The `transport` field every published payload carries, naming WHICH of this module's two
# delivery paths produced it. Added by marketplace-subscriptions-paper-trading task 24.2.
#
# The watch_ohlcv -> fetch_ohlcv fallback on NotSupported below already existed and is unchanged;
# what is new is that a subscriber can now TELL which one it is reading, which is what
# Requirement 14.6 ("SHALL record the fallback in the session's feed state") needs and what a
# subscriber previously had no way to know - both paths publish to the same Redis channel with an
# otherwise identical payload.
#
# The values are the ones `paper_market_feed.FEED_TRANSPORTS` reads. They are spelled as literals
# here rather than imported from the backend package because this module is a standalone
# microservice process (`mds/main.py`, its own container and health port) and importing the
# backend's paper package into it would couple the two deployments; the pair is asserted equal by
# tests/test_paper_market_feed.py so a rename cannot silently split them.
TRANSPORT_WEBSOCKET = "WEBSOCKET"
TRANSPORT_REST = "REST"

def get_exchange_instance(exchange_id: str):
    if exchange_id not in exchange_instances:
        ex_class = getattr(ccxtpro, exchange_id)
        exchange_instances[exchange_id] = ex_class({'enableRateLimit': True})
    return exchange_instances[exchange_id]

async def broadcast_ohlcv(exchange_id, symbol):
    exchange = get_exchange_instance(exchange_id)
    timeframe = '1m'
    channel = f"mds:data:{exchange_id}:{symbol}"
    
    logger.info(f"Starting websocket stream for {exchange_id} {symbol}")
    
    while True:
        try:
            # Watch OHLCV (Websocket)
            candles = await exchange.watch_ohlcv(symbol, timeframe)
            if candles:
                latest = candles[-1]
                payload = {
                    "exchange": exchange_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": latest[0],
                    "open": latest[1],
                    "high": latest[2],
                    "low": latest[3],
                    "close": latest[4],
                    "volume": latest[5],
                    "transport": TRANSPORT_WEBSOCKET
                }
                await redis_manager.redis.publish(channel, json.dumps(payload))
        except Exception as e:
            if "NotSupported" in str(type(e)):
                logger.warning(f"WebSockets not supported for {exchange_id} {symbol}. Falling back to REST polling.")
                break
            logger.error(f"Error watching {exchange_id} {symbol}: {e}")
            await asyncio.sleep(5)

    # REST Fallback
    logger.info(f"Starting REST polling loop for {exchange_id} {symbol}")
    while True:
        try:
            candles = await exchange.fetch_ohlcv(symbol, timeframe, limit=2)
            if candles:
                latest = candles[-1]
                payload = {
                    "exchange": exchange_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": latest[0],
                    "open": latest[1],
                    "high": latest[2],
                    "low": latest[3],
                    "close": latest[4],
                    "volume": latest[5],
                    "transport": TRANSPORT_REST
                }
                await redis_manager.redis.publish(channel, json.dumps(payload))
            await asyncio.sleep(10) # Poll every 10s to avoid rate limit
        except Exception as e:
            logger.error(f"Error polling REST {exchange_id} {symbol}: {e}")
            await asyncio.sleep(5)

async def handle_commands():
    pubsub = redis_manager.redis.pubsub()
    await pubsub.subscribe("mds:commands")
    logger.info("Listening to mds:commands...")
    
    async for message in pubsub.listen():
        if message["type"] == "message":
            try:
                data = json.loads(message["data"])
                action = data.get("action")
                exchange_id = data.get("exchange", "binance").lower()
                symbol = data.get("symbol")
                
                if not symbol:
                    continue
                    
                key = (exchange_id, symbol)
                
                if action == "subscribe":
                    if key not in active_streams:
                        task = asyncio.create_task(broadcast_ohlcv(exchange_id, symbol))
                        active_streams[key] = task
                elif action == "unsubscribe":
                    # Memory optimization / cleanup is omitted for this implementation
                    pass
            except Exception as e:
                logger.error(f"Error parsing mds command: {e}")

async def health_check(request):
    return web.json_response({"status": "ok", "service": "mds", "active_streams": len(active_streams)})

async def start_health_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8081)  # nosec: B104 - microservice internal health endpoint
    await site.start()
    logger.info("Health server listening on port 8081")

async def main():
    await redis_manager.connect()
    logger.info("Market Data Service (MDS) starting...")
    
    health_task = asyncio.create_task(start_health_server())
    cmd_task = asyncio.create_task(handle_commands())
    
    await asyncio.gather(health_task, cmd_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("MDS shut down gracefully.")
