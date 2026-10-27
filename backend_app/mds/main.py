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

# PAPER-02. A durable, one-shot-readable last price, alongside the pub/sub this module always
# had. Before this, a caller that was not an ACTIVE subscriber of ``mds:data:*`` (pub/sub has no
# memory - a publish with no listener is simply gone) had no way to ask "what did this symbol
# last trade at", so a manual paper market order had nothing to fill at but a caller-supplied or
# hardcoded price. ``backend/paper_trading_service.py::get_fresh_market_price`` is the reader;
# this is the one writer, so the key format is spelled identically in both files and the two are
# asserted equal by a test, the same pattern this module's own TRANSPORT_* constants document
# above - importing the backend's paper package into this standalone microservice would couple
# the two deployments.
#
# The TTL is what makes a stream that silently died read as "no fresh price" instead of serving
# a stale one forever: Requirement 18.15 (see paper_trading_service.py) says a stale figure must
# be labelled, never substituted silently. 150s comfortably covers one quiet interval of either
# delivery path below (a `watch_ohlcv` candle close on a 1m timeframe, or the 10s REST poll) while
# still expiring an actually-dead stream well before a human would call it "fresh".
LAST_PRICE_TTL_SECONDS = 150

# WS-03. How many CONSECUTIVE websocket failures end the websocket attempt and fall through
# to the REST path below.
#
# WHY A CEILING EXISTS AT ALL. The websocket loop retried every five seconds forever and
# nothing counted. When ccxt 4.3.92 turned out to be incompatible with the pinned aiohttp,
# EVERY watch_ohlcv call raised `'aiohttp._websocket.reader_c.WebSocketReader' object has no
# attribute 'parse_frame'`, and the measured result was 34 error events in three minutes,
# per subscription, indefinitely -- a permanent fault retried at a fixed rate, billing
# CloudWatch for the privilege. The service was scaled to zero to stop it.
#
# WHY IT FALLS BACK TO REST RATHER THAN GIVING UP, which is the part that matters for the
# product. This module already has a designed fallback: `NotSupported` breaks out of the
# websocket loop into REST polling, and every payload carries a `transport` field precisely
# so a subscriber can tell which path it is reading. A websocket that has failed a dozen
# times in a row is, operationally, a venue where websockets do not work -- the same
# condition `NotSupported` names, learned by observation instead of by declaration. Had this
# ceiling existed, WS-03 would have been a DEGRADED feed (REST candles, correctly labelled)
# instead of a total outage in which paper sessions received nothing at all.
#
# 12 at the five-second backoff below is one minute of trying: long enough to ride out a
# venue restart or a transient network fault, short enough that a permanent incompatibility
# costs a minute rather than a day. A SUCCESSFUL candle resets the count, so a long-lived
# stream that sees occasional errors never accumulates its way into the fallback.
WS_CONSECUTIVE_FAILURE_CEILING = 12

# And the same question for the REST path, where there is nothing left to fall back to, so
# the stream STOPS instead. A symbol that will never poll successfully -- delisted, or a typo
# in a subscribe command -- otherwise retries for the life of the container. Stopping drops
# the stream from `active_streams`, which makes it re-subscribable: the next subscribe for
# that pair starts a fresh task rather than being refused because a dead entry is still
# sitting in the dict.
REST_CONSECUTIVE_FAILURE_CEILING = 30


def last_price_key(exchange_id: str, symbol: str) -> str:
    """The key :func:`broadcast_ohlcv` writes and ``get_fresh_market_price`` reads. Spelled
    identically in both files on purpose - see the comment above ``LAST_PRICE_TTL_SECONDS``."""
    return f"mds:last_price:{str(exchange_id).strip().lower()}:{str(symbol).strip()}"


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

    consecutive_failures = 0
    while True:
        try:
            # Watch OHLCV (Websocket)
            candles = await exchange.watch_ohlcv(symbol, timeframe)
            consecutive_failures = 0
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
                await redis_manager.redis.setex(
                    last_price_key(exchange_id, symbol),
                    LAST_PRICE_TTL_SECONDS,
                    json.dumps({
                        "price": latest[4], "timestamp": latest[0],
                        "transport": TRANSPORT_WEBSOCKET,
                    }),
                )
        except Exception as e:
            if "NotSupported" in str(type(e)):
                logger.warning(f"WebSockets not supported for {exchange_id} {symbol}. Falling back to REST polling.")
                break
            consecutive_failures += 1
            logger.error(
                f"Error watching {exchange_id} {symbol} "
                f"({consecutive_failures}/{WS_CONSECUTIVE_FAILURE_CEILING} consecutive): {e}"
            )
            if consecutive_failures >= WS_CONSECUTIVE_FAILURE_CEILING:
                # Reported at CRITICAL because this is the line that would have named WS-03
                # within a minute of the first deployment, instead of it being found by
                # driving the service and reading a repeating ERROR by hand.
                logger.critical(
                    f"Websocket stream for {exchange_id} {symbol} failed "
                    f"{consecutive_failures} times consecutively; abandoning the websocket "
                    f"transport and falling back to REST polling. Last error: {e}"
                )
                break
            await asyncio.sleep(5)

    # REST Fallback
    logger.info(f"Starting REST polling loop for {exchange_id} {symbol}")
    consecutive_failures = 0
    while True:
        try:
            candles = await exchange.fetch_ohlcv(symbol, timeframe, limit=2)
            consecutive_failures = 0
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
                await redis_manager.redis.setex(
                    last_price_key(exchange_id, symbol),
                    LAST_PRICE_TTL_SECONDS,
                    json.dumps({
                        "price": latest[4], "timestamp": latest[0],
                        "transport": TRANSPORT_REST,
                    }),
                )
            await asyncio.sleep(10) # Poll every 10s to avoid rate limit
        except Exception as e:
            consecutive_failures += 1
            logger.error(
                f"Error polling REST {exchange_id} {symbol} "
                f"({consecutive_failures}/{REST_CONSECUTIVE_FAILURE_CEILING} consecutive): {e}"
            )
            if consecutive_failures >= REST_CONSECUTIVE_FAILURE_CEILING:
                logger.critical(
                    f"REST polling for {exchange_id} {symbol} failed {consecutive_failures} "
                    f"times consecutively and there is no further fallback; STOPPING this "
                    f"stream. It can be re-subscribed. Last error: {e}"
                )
                return
            await asyncio.sleep(5)

def _forget_stream(key, task) -> None:
    """Drop a finished stream's entry, but only if it is still the one registered.

    The identity check matters: between a task ending and this callback running, a
    re-subscribe may already have put a NEW task under the same key, and popping blindly
    would deregister the live stream and leave it running untracked -- unstoppable by
    unsubscribe and invisible to the health count.
    """
    if active_streams.get(key) is task:
        active_streams.pop(key, None)


async def _cancel_stream(key, exchange_id: str, symbol: str) -> None:
    """Stop a stream and deregister it. Safe to call for a pair that is not streaming.

    The cancellation is AWAITED. Returning as soon as `cancel()` is called would report the
    stream stopped while its next `watch_ohlcv` was still in flight, so an unsubscribe
    immediately followed by a subscribe could end up with two tasks on one symbol.
    `CancelledError` is raised into the task at its next suspension point and is not caught
    by its `except Exception` handlers -- it derives from BaseException -- so the loops above
    unwind rather than treating the cancel as one more stream error to retry.
    """
    task = active_streams.pop(key, None)
    if task is None:
        logger.info(f"Unsubscribe for {exchange_id} {symbol}: no active stream.")
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001 - the stream's own failure, not ours
        logger.warning(
            f"Stream for {exchange_id} {symbol} raised while being cancelled: {e}"
        )
    logger.info(f"Stopped streaming {exchange_id} {symbol}.")


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
                    # `existing and not existing.done()` rather than `key not in
                    # active_streams`. A finished task used to stay in the dict forever,
                    # and this check only asked whether the KEY was present, so a stream
                    # that had stopped could never be restarted: every subsequent subscribe
                    # for that pair matched the stale entry and did nothing, silently.
                    existing = active_streams.get(key)
                    if existing is not None and not existing.done():
                        logger.info(
                            f"Already streaming {exchange_id} {symbol}; subscribe ignored."
                        )
                    else:
                        task = asyncio.create_task(broadcast_ohlcv(exchange_id, symbol))
                        active_streams[key] = task
                        # Self-cleaning: whichever way the task ends -- the REST ceiling,
                        # a cancel, or an unexpected raise -- its entry leaves the dict, so
                        # `active_streams` means "streams that are actually running" and
                        # the health endpoint's count stops drifting upward.
                        task.add_done_callback(
                            lambda t, k=key: _forget_stream(k, t)
                        )
                elif action == "unsubscribe":
                    # THIS USED TO BE `pass`, under the comment "Memory optimization /
                    # cleanup is omitted for this implementation". It is not a memory
                    # optimization. The task kept running after its last subscriber left,
                    # and a task whose stream was FAILING kept retrying every five seconds
                    # for the life of the container -- measured during WS-03: the probe
                    # published an unsubscribe in a `finally` and the error loop carried on
                    # regardless, 34 events in the next three minutes. An unsubscribe that
                    # does not stop the stream is not an unsubscribe.
                    await _cancel_stream(key, exchange_id, symbol)
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
