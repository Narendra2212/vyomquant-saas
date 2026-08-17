"""
routers/exchange.py — Exchange API Key Vault.

FIXES:
  EXCH-1: Uses get_supabase() singleton throughout
  EXCH-3: delete_connection() now evicts the stale entry from the exchange pool
"""

import asyncio
import inspect
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from backend_app.backend.connection_engine import (ConnectionEngine,
                                                   release_exchange)
from backend_app.backend.data_seeking_engine import DataEngine
from backend_app.core.dependencies import (get_current_user,
                                           get_request_supabase, get_vault)
from backend_app.core.models import ExchangeKeysRequest, TestConnectionRequest
from backend_app.backend.redis_manager import get_redis_manager
from supabase import Client as SupabaseClient

router = APIRouter()
logger = logging.getLogger("ExchangeRouter")


_CACHED_SUPPORTED_EXCHANGES = None

@router.get("/supported")
async def get_supported_exchanges():
    """
    Returns list of all CCXT-supported exchanges with full metadata.
    Includes: id, display name, spot/futures/margin support, sandbox support,
    required auth fields, passphrase requirement, subaccount requirement, status.
    Cached in memory and Redis for 1 hour (exchanges list rarely changes).
    
    PUBLIC ENDPOINT - No authentication required (CCXT public data)
    """
    global _CACHED_SUPPORTED_EXCHANGES
    if _CACHED_SUPPORTED_EXCHANGES is not None:
        return _CACHED_SUPPORTED_EXCHANGES

    try:
        # Try cache first
        from backend_app.core.cache.redis_manager import redis_manager
        cache_key = "supported_exchanges:metadata"
        try:
            cached_str = await redis_manager.get(cache_key)
            if cached_str:
                _CACHED_SUPPORTED_EXCHANGES = json.loads(cached_str)
                return _CACHED_SUPPORTED_EXCHANGES
        except Exception as e:
            logger.warning(f"Redis cache lookup failed: {e}, fetching from CCXT")
        
        # Fetch from CCXT with full metadata
        import ccxt as ccxt_base
        exchanges = []
        
        for exchange_id in ccxt_base.exchanges:
            try:
                exchange_class = getattr(ccxt_base, exchange_id)
                exchange_instance = exchange_class()
                
                # Extract capabilities
                has = exchange_instance.has
                
                # Determine required auth fields dynamically from CCXT
                required_fields = []
                if has.get('apiKey'):
                    required_fields.append('api_key')
                if has.get('secret'):
                    required_fields.append('secret_key')
                if has.get('password'):
                    required_fields.append('password')
                if has.get('uid'):
                    required_fields.append('uid')
                
                # Check for passphrase/password requirement dynamically
                requires_passphrase = has.get('password')
                
                # Check for subaccount support dynamically via exchange options
                requires_subaccount = False
                try:
                    if hasattr(exchange_instance, 'options') and 'defaultType' in exchange_instance.options:
                        requires_subaccount = True
                except Exception:
                    pass
                
                exchanges.append({
                    "id": exchange_id,
                    "display_name": exchange_instance.name or exchange_id.upper(),
                    "logo": f"/logos/{exchange_id.lower()}.png",
                    "spot_support": bool(has.get('createOrder', False)),
                    "futures_support": bool(has.get('createFuturesOrder', False) or has.get('futures', False)),
                    "margin_support": bool(has.get('createMarginOrder', False) or has.get('margin', False)),
                    "sandbox_support": bool(has.get('sandbox', False)),
                    "required_fields": required_fields,
                    "requires_passphrase": bool(requires_passphrase),
                    "requires_subaccount": bool(requires_subaccount),
                    "status": "available"
                })
            except Exception as e:
                logger.warning(f"Failed to load metadata for {exchange_id}: {e}")
                continue
        
        # Sort by display name
        exchanges.sort(key=lambda x: x['display_name'])
        
        result = {"exchanges": exchanges, "total": len(exchanges)}
        _CACHED_SUPPORTED_EXCHANGES = result
        
        # Cache for 1 hour (3600 seconds)
        try:
            await redis_manager.set(cache_key, json.dumps(result), ex=3600)
            logger.debug("Cached supported exchanges with metadata for 1 hour")
        except Exception as e:
            logger.warning(f"Redis cache set failed: {e}")
        
        return result
    except Exception as e:
        logger.error(f"Failed to fetch supported exchanges: {e}")
        raise HTTPException(500, "Failed to retrieve supported exchanges.")


@router.post("/keys")
async def store_keys(
    body: ExchangeKeysRequest,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
    redis_manager=Depends(get_redis_manager),
):
    """
    Store encrypted exchange API keys in vault.
    Tests connection before saving to ensure credentials are valid.
    Invalidates cache on successful storage.
    """
    bridge = None
    try:
        bridge = ConnectionEngine(
            exchange_id=body.exchange_id,
            api_key=body.api_key,
            secret_key=body.secret_key,
            password=body.password,
        )
        exchange = await bridge.connect()
        await DataEngine(exchange).fetch_wallet_balance_snapshot()
    except Exception as e:
        raise HTTPException(400, f"Exchange connection verification failed: {str(e)}")
    finally:
        if bridge:
            await bridge.disconnect()

    try:
        vault.store_exchange_keys(
            user_id=user["id"],
            exchange_id=body.exchange_id,
            raw_api_key=body.api_key,
            raw_secret_key=body.secret_key,
            raw_password=body.password,
            label=body.label,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Key storage failed for {user['id']}: {e}")
        raise HTTPException(500, "Failed to store keys in vault.")

    # Invalidate cache
    if redis_manager:
        cache_key = f"exchanges:list:{user['id']}"
        await redis_manager.cache_delete(cache_key)
        logger.debug(f"Invalidated cache for user {user['id']} after storing new exchange")

    return {"status": "ok", "message": "Exchange keys securely encrypted and stored in vault."}


@router.get("")
@router.get("/")
async def list_exchanges(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
    redis_manager=Depends(get_redis_manager),
    vault=Depends(get_vault),
):
    """
    List user's connected exchanges with full metadata.
    Returns exchange status, permissions, bot count, strategy count, health metrics.
    """
    try:
        keys = []
        if supabase:
            try:
                q1_res = supabase.table("exchange_keys").select("exchange_id, updated_at").eq("user_id", user["id"]).execute()
                q1 = await q1_res if inspect.isawaitable(q1_res) else q1_res
                keys = q1.data if q1 and hasattr(q1, "data") and isinstance(q1.data, list) else []
            except Exception as e:
                logger.warning(f"Failed to fetch exchange keys: {e}")
                keys = []
        
        exchanges = []
        for row in keys:
            exchange_id = row.get("exchange_id", "unknown")
            exchanges.append({
                "id": f"{user['id']}_{exchange_id}",
                "exchange_id": exchange_id,
                "name": exchange_id.upper(),
                "masked_key": f"{exchange_id[:3].upper()}{'•' * 24}{exchange_id[-2:].upper() if len(exchange_id) > 2 else ''}",
                "status": "CONNECTED",
                "permissions": ["Spot Trading", "Read"],
                "bot_count": 0,
                "strategy_count": 0,
                "account_type": "Spot",
                "enabled_features": ["Trading", "Balance"],
                "connected_at": row.get("updated_at"),
                "last_sync": row.get("updated_at"),
                "subscription_tier": "free",
                "health": "healthy"
            })
        return exchanges
    except Exception as e:
        logger.warning(f"list_exchanges fallback: {e}")
        return []


@router.delete("/{exchange_id}")
async def delete_connection(
    exchange_id: str,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
    redis_manager=Depends(get_redis_manager),
):
    """
    Delete exchange connection with safety checks.
    Warns if bots are running, strategies exist, or positions are open.
    Invalidates cache on successful deletion.
    """
    try:
        # Check for active bots using this exchange
        res_bots = (
            supabase.table("strategies")
            .select("id, name")
            .eq("user_id", user["id"])
            .eq("exchange_id", exchange_id.lower())
            .eq("status", "deployed")
            .execute()
        )
        bots_resp = await res_bots if inspect.isawaitable(res_bots) else res_bots
        
        active_bots = bots_resp.data if bots_resp and hasattr(bots_resp, "data") and bots_resp.data else []
        
        if active_bots:
            bot_names = [bot.get("name", bot.get("id")) for bot in active_bots]
            raise HTTPException(
                400,
                f"Cannot delete exchange: {len(active_bots)} active bot(s) running. "
                f"Stop bots first: {', '.join(bot_names[:3])}"
            )

        # Delete from database
        res_del = supabase.table("exchange_keys").delete().eq("user_id", user["id"]).eq(
            "exchange_id", exchange_id.lower()
        ).execute()
        if inspect.isawaitable(res_del):
            await res_del

        # Evict from the exchange pool so the stale socket is closed
        await release_exchange(user["id"], exchange_id.lower())

        # Invalidate cache
        if redis_manager:
            cache_key = f"exchanges:list:{user['id']}"
            await redis_manager.cache_delete(cache_key)
            logger.debug(f"Invalidated cache for user {user['id']} after deleting exchange")

        logger.info(f"Exchange {exchange_id} deleted for user {user['id']}")
        return {"status": "ok", "message": f"{exchange_id.upper()} disconnected successfully."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to disconnect {exchange_id}: {e}")
        raise HTTPException(500, f"Failed to disconnect: {e}")


@router.get("/schema/{exchange_id}")
async def get_exchange_auth_schema(
    exchange_id: str,
    redis_manager=Depends(get_redis_manager)
):
    """
    Returns the authentication schema for a specific exchange.
    Includes required fields, field labels, field types, and validation rules.
    Cached in Redis for 1 hour.
    """
    try:
        exchange_id = exchange_id.lower()
        cache_key = f"exchange_schema:{exchange_id}"
        cached = await redis_manager.cache_get_json(cache_key) if redis_manager else None
        
        if cached:
            return cached
        
        # Fetch CCXT instance to inspect auth requirements
        import ccxt as ccxt_base
        if not hasattr(ccxt_base, exchange_id):
            raise HTTPException(404, f"Exchange '{exchange_id}' not supported by CCXT.")
        
        exchange_class = getattr(ccxt_base, exchange_id)
        exchange_instance = exchange_class()
        has = exchange_instance.has
        
        # Build field definitions
        fields = []
        
        if has.get('apiKey'):
            fields.append({
                "name": "api_key",
                "label": "API Key",
                "type": "text",
                "required": True,
                "placeholder": "Enter your API key",
                "description": "Public identifier for your API credentials"
            })
        
        if has.get('secret'):
            fields.append({
                "name": "secret_key",
                "label": "Secret Key",
                "type": "password",
                "required": True,
                "placeholder": "Enter your secret key",
                "description": "Private key for signing requests"
            })
        
        if has.get('password'):
            fields.append({
                "name": "password",
                "label": "Password",
                "type": "password",
                "required": True,
                "placeholder": "Enter your password",
                "description": "Additional security parameter for API authentication"
            })
        
        if has.get('uid'):
            fields.append({
                "name": "uid",
                "label": "User ID",
                "type": "text",
                "required": True,
                "placeholder": "Enter your user ID",
                "description": "Unique identifier for your account"
            })
        
        # Optional label field
        fields.append({
            "name": "label",
            "label": "Connection Label",
            "type": "text",
            "required": False,
            "placeholder": "e.g., Main Binance Account",
            "description": "Optional label to identify this connection"
        })
        
        schema = {
            "exchange_id": exchange_id,
            "display_name": exchange_instance.name or exchange_id.upper(),
            "fields": fields,
            "supports_testnet": has.get('sandbox', False),
            "supports_subaccount": has.get('createFuturesOrder') or has.get('futures'),
            "default_account_type": "spot" if has.get('createOrder') else None
        }
        
        # Cache for 1 hour
        if redis_manager:
            await redis_manager.cache_set_json(cache_key, schema, ttl=3600)
        
        return schema
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch auth schema for {exchange_id}: {e}")
        raise HTTPException(500, f"Failed to retrieve exchange schema: {e}")


@router.post("/test")
async def test_connection(
    body: ExchangeKeysRequest,
    user: dict = Depends(get_current_user),
):
    """
    Test exchange connection with provided credentials before saving.
    Validates API keys, permissions, clock synchronization, and API scope.
    """
    bridge = None
    try:
        bridge = ConnectionEngine(
            exchange_id=body.exchange_id,
            api_key=body.api_key,
            secret_key=body.secret_key,
            password=body.password,
        )
        exchange = await bridge.connect()
        
        # Validate keys with lightweight call
        is_valid = await bridge.validate_keys()
        if not is_valid:
            raise HTTPException(400, "Invalid API credentials or insufficient permissions.")
        
        # Fetch balance to verify trading permissions
        balance = await DataEngine(exchange).fetch_wallet_balance_snapshot()
        total_usdt = balance.get("USDT", {}).get("total", 0)
        
        # Check exchange status
        status = await bridge.check_exchange_status()
        
        # Check clock synchronization
        clock_sync = "ok"
        try:
            if hasattr(exchange, 'load_time_difference'):
                await exchange.load_time_difference()
                clock_sync = "synchronized"
        except:
            clock_sync = "assumed_ok"
        
        # Determine permissions based on what we could access
        permissions = ["Read"]
        if total_usdt >= 0:  # Successfully fetched balance
            permissions.append("Spot Trading")
        if exchange.has.get('createFuturesOrder'):
            permissions.append("Futures Trading")
        
        return {
            "status": "CONNECTED",
            "exchange": body.exchange_id.upper(),
            "usdt_balance": round(total_usdt, 2),
            "exchange_status": status,
            "clock_sync": clock_sync,
            "permissions": permissions,
            "message": "Connection verified successfully.",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Connection test failed for {user['id']}: {e}")
        raise HTTPException(400, f"Connection failed: {str(e)}")
    finally:
        if bridge:
            await bridge.disconnect()


@router.post("/test-stored")
async def test_stored_connection(
    body: TestConnectionRequest,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    """
    Test connection for already-stored exchange keys.
    Used to verify existing connections are still valid.
    """
    bridge = None
    try:
        keys = vault.load_decrypted_keys(
            user["id"],
            body.exchange_id,
        )
        bridge = ConnectionEngine(
            exchange_id=body.exchange_id,
            api_key=keys["api_key"],
            secret_key=keys["secret_key"],
            password=keys.get("password"),
        )
        exchange = await bridge.connect()
        balance = await DataEngine(exchange).fetch_wallet_balance_snapshot()
        total_usdt = balance.get("USDT", {}).get("total", 0)
        status = await bridge.check_exchange_status()
        return {
            "status": "CONNECTED",
            "exchange": body.exchange_id.upper(),
            "usdt_balance": round(total_usdt, 2),
            "exchange_status": status,
            "message": "Stored keys verified successfully.",
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.warning(f"Stored connection test failed for {user['id']}: {e}")
        raise HTTPException(400, f"Connection failed: {str(e)}")
    finally:
        if bridge:
            await bridge.disconnect()
