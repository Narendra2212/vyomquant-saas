import asyncio
import json
import logging
import os

from aiohttp import web

# Import legacy engine components to execute locally within this process
from backend_app.core.cache import redis_manager
from backend_app.core.fleet_manager import get_fleet_manager
from supabase import create_client

logger = logging.getLogger("TEE")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

STREAM_KEY = "command_queue"
GROUP_NAME = "tee_group"
CONSUMER_NAME = f"tee_worker_{os.getpid()}"
LOCK_TTL_SECONDS = 15

# active_locks keeps track of strategy_id -> (user_id, symbol)
active_locks = {}

# Supabase Client for Reconciliation
_supabase_client = None
def get_supabase():
    global _supabase_client
    if _supabase_client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if url and key:
            _supabase_client = create_client(url, key)
    return _supabase_client

async def init_redis_group():
    """Ensure the consumer group exists."""
    try:
        await redis_manager.redis.xgroup_create(STREAM_KEY, GROUP_NAME, mkstream=True)
        logger.info(f"Created consumer group {GROUP_NAME} on {STREAM_KEY}")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group {GROUP_NAME} already exists.")
        else:
            logger.error(f"Error creating group: {e}")

async def try_acquire_lock(strategy_id: str) -> bool:
    lock_key = f"tee:lock:{strategy_id}"
    # SETNX logic
    acquired = await redis_manager.redis.set(lock_key, CONSUMER_NAME, nx=True, ex=LOCK_TTL_SECONDS)
    return bool(acquired)

async def release_lock(strategy_id: str):
    lock_key = f"tee:lock:{strategy_id}"
    await redis_manager.redis.delete(lock_key)

async def process_start_bot(payload: dict):
    user_id = payload.get("user_id")
    symbol = payload.get("symbol")
    blueprint = payload.get("blueprint")
    
    if not all([user_id, symbol, blueprint]):
        logger.error(f"Invalid start_bot payload: {payload}")
        return False
        
    strategy_id = blueprint.get("id")
    if not strategy_id:
        logger.error("No strategy ID in blueprint")
        return False
    
    # SECURITY: Validate ML/DL strategies have trained models before TEE deployment
    nodes = []
    if isinstance(blueprint.get("buy_logic"), dict):
        nodes = blueprint["buy_logic"].get("_nodes", [])
    elif isinstance(blueprint.get("nodes"), list):
        nodes = blueprint["nodes"]
    
    # Check for ML/DL nodes
    ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]
    
    if ml_nodes and not blueprint.get("ml_model_path"):
        logger.error(f"TEE deployment blocked: Strategy {strategy_id} contains ML/DL nodes but no trained model reference")
        return False
        
    # Attempt Leader Election for this strategy
    acquired = await try_acquire_lock(strategy_id)
    if not acquired:
        logger.info(f"Skipping deployment for {strategy_id}, lock held by another TEE instance.")
        return True # Acknowledge anyway, it's being handled
        
    logger.info(f"Starting bot for user {user_id} on {symbol} (Strategy: {strategy_id})")
    fleet = get_fleet_manager()
    success, msg = await fleet.start_bot(user_id, symbol, blueprint)
    if success:
        logger.info(f"Bot started successfully: {msg}")
        active_locks[strategy_id] = (user_id, symbol)
    else:
        logger.error(f"Failed to start bot: {msg}")
        await release_lock(strategy_id)
    return success

async def process_stop_bot(payload: dict):
    user_id = payload.get("user_id")
    symbol = payload.get("symbol")
    # If called from API stop_bot, we don't have strategy_id in payload currently.
    # Wait, the API stop_bot only passes user_id and symbol.
    # Let's find strategy_id from active_locks
    
    if not all([user_id, symbol]):
        logger.error(f"Invalid stop_bot payload: {payload}")
        return False
        
    logger.info(f"Stopping bot for user {user_id} on {symbol}")
    fleet = get_fleet_manager()
    await fleet.stop_bot(user_id, symbol)
    
    # Release locks matching this user and symbol
    to_remove = []
    for s_id, (u_id, sym) in active_locks.items():
        if u_id == user_id and sym == symbol:
            to_remove.append(s_id)
            await release_lock(s_id)
            
    for s_id in to_remove:
        active_locks.pop(s_id, None)
        
    logger.info("Bot stopped successfully")
    return True

async def process_message(msg_id, message_data):
    try:
        action = message_data.get(b"action", b"").decode("utf-8")
        raw_payload = message_data.get(b"payload", b"{}").decode("utf-8")
        payload = json.loads(raw_payload)
        
        logger.info(f"Processing message {msg_id}: action={action}")
        
        if action == "start_bot":
            await process_start_bot(payload)
        elif action == "stop_bot":
            await process_stop_bot(payload)
        else:
            logger.warning(f"Unknown action {action}")
            
        # Acknowledge the message
        await redis_manager.redis.xack(STREAM_KEY, GROUP_NAME, msg_id)
        logger.info(f"Acknowledged message {msg_id}")
    except Exception as e:
        logger.error(f"Error processing message {msg_id}: {e}", exc_info=True)

async def process_pel():
    """Recover unacknowledged messages from PEL."""
    logger.info("Checking PEL for unacknowledged messages...")
    try:
        messages = await redis_manager.redis.xreadgroup(
            GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: "0-0"}, count=100
        )
        for stream, msgs in messages:
            for msg_id, msg_data in msgs:
                logger.info(f"Recovering message {msg_id} from PEL")
                await process_message(msg_id, msg_data)
    except Exception as e:
        logger.error(f"Error processing PEL: {e}")

async def heartbeat_loop():
    """Renews locks for actively running strategies."""
    while True:
        try:
            to_remove = []
            for strategy_id, (user_id, symbol) in list(active_locks.items()):
                lock_key = f"tee:lock:{strategy_id}"
                # Renew lock
                success = await redis_manager.redis.expire(lock_key, LOCK_TTL_SECONDS)
                if not success:
                    logger.warning(f"Lost lock for {strategy_id}! Forcing stop.")
                    fleet = get_fleet_manager()
                    await fleet.stop_bot(user_id, symbol)
                    to_remove.append(strategy_id)
                    
            for s_id in to_remove:
                active_locks.pop(s_id, None)
        except Exception as e:
            logger.error(f"Error in heartbeat loop: {e}")
        await asyncio.sleep(5)

async def reconciler_loop():
    """Detects orphaned strategies left by crashed TEE instances and adopts them."""
    while True:
        try:
            client = get_supabase()
            if not client:
                await asyncio.sleep(10)
                continue
                
            # Query all running strategies
            resp = client.table("strategies").select("*").eq("status", "running").execute()
            running_strats = resp.data or []
            
            for strat in running_strats:
                s_id = strat["id"]
                if s_id not in active_locks:
                    # Attempt to adopt orphaned strategy
                    acquired = await try_acquire_lock(s_id)
                    if acquired:
                        logger.info(f"Adopting orphaned strategy: {s_id}")
                        payload = {
                            "user_id": strat["user_id"],
                            "symbol": strat.get("symbol", "BTC/USDT"),
                            "blueprint": strat
                        }
                        await process_start_bot(payload)
                        
        except Exception as e:
            logger.error(f"Error in reconciler loop: {e}")
        await asyncio.sleep(10)

async def consume_stream():
    """Main consumer loop."""
    await redis_manager.connect()
    await init_redis_group()
    await process_pel()
    
    logger.info(f"Starting consumer loop for {CONSUMER_NAME}...")
    while True:
        try:
            messages = await redis_manager.redis.xreadgroup(
                GROUP_NAME, CONSUMER_NAME, {STREAM_KEY: ">"}, count=10, block=5000
            )
            for stream, msgs in messages:
                for msg_id, msg_data in msgs:
                    await process_message(msg_id, msg_data)
        except asyncio.CancelledError:
            logger.info("Consumer loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in consumer loop: {e}")
            await asyncio.sleep(5)

# --- Health Check Server ---
async def health_check(request):
    return web.json_response({"status": "ok", "service": "tee", "active_strategies": len(active_locks)})

async def start_health_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("Health server listening on port 8080")

async def shutdown():
    logger.info("Graceful shutdown: Releasing locks...")
    for s_id in list(active_locks.keys()):
        await release_lock(s_id)

async def main():
    logger.info("Trading Execution Engine (TEE) starting...")
    health_task = asyncio.create_task(start_health_server())
    consumer_task = asyncio.create_task(consume_stream())
    heartbeat_task = asyncio.create_task(heartbeat_loop())
    reconciler_task = asyncio.create_task(reconciler_loop())
    
    await asyncio.gather(health_task, consumer_task, heartbeat_task, reconciler_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        asyncio.run(shutdown())
        logger.info("TEE shut down gracefully.")
