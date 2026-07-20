"""
routers/exchange.py — Exchange API Key Vault.

FIXES:
  EXCH-1: Uses get_supabase() singleton throughout
  EXCH-3: delete_connection() now evicts the stale entry from the exchange pool
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from supabase import Client as SupabaseClient

from backend_app.core.models import ExchangeKeysRequest, TestConnectionRequest
from backend_app.core.dependencies import get_current_user, get_vault, get_request_supabase, DEV_MODE

from backend_app.backend.connection_engine import ConnectionEngine, release_exchange
from backend_app.backend.data_seeking_engine import DataEngine

router = APIRouter()
logger = logging.getLogger("ExchangeRouter")


@router.post("/keys")
async def store_keys(
    body: ExchangeKeysRequest,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    # Test connection first
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
        logger.warning(f"Onboarding connection test failed for user={user['id']} exchange={body.exchange_id}: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Exchange connection verification failed: {str(e)}"
        )
    finally:
        if bridge:
            await bridge.disconnect()

    try:
        vault.store_exchange_keys(
            user_id=user["id"],
            exchange_id=body.exchange_id,
            raw_api_key=body.api_key,
            raw_secret=body.secret_key,
            raw_password=body.password,
        )
        return {
            "status": "ok",
            "message": f"Keys encrypted, verified, and stored for {body.exchange_id.upper()}",
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Key storage failed for {user['id']}: {e}")
        raise HTTPException(500, "Failed to store keys in vault.")


@router.get("/")
async def list_exchanges(
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    try:
        resp = (
            supabase.table("exchange_keys")
            .select("exchange_id, encrypted_api_key, created_at")
            .eq("user_id", user["id"])
            .execute()
        )

        return [
            {
                "exchange_id": row["exchange_id"],
                "masked_key": row["exchange_id"].upper()[:3] + "•" * 24,
                "connected_at": row["created_at"],
                "status": "CONNECTED",
            }
            for row in resp.data
        ]
    except Exception as e:
        logger.error(f"List exchanges failed: {e}")
        raise HTTPException(500, "Failed to retrieve exchange connections.")


@router.delete("/{exchange_id}")
async def delete_connection(
    exchange_id: str,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    try:
        supabase.table("exchange_keys").delete().eq("user_id", user["id"]).eq(
            "exchange_id", exchange_id.lower()
        ).execute()

        # EXCH-3: Evict from the exchange pool so the stale socket is closed
        await release_exchange(user["id"], exchange_id.lower())

        return {"status": "ok", "message": f"{exchange_id.upper()} disconnected."}
    except Exception as e:
        raise HTTPException(500, f"Failed to disconnect: {e}")


@router.post("/test")
async def test_connection(
    body: TestConnectionRequest,
    user: dict = Depends(get_current_user),
    vault=Depends(get_vault),
):
    """
    One-time connection test — deliberately uses a fresh connection (not the pool)
    so it validates the currently stored keys against the live exchange.
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
        return {
            "status": "CONNECTED",
            "exchange": body.exchange_id.upper(),
            "usdt_balance": round(total_usdt, 2),
            "message": "Connection verified successfully.",
        }
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.warning(f"Connection test failed for {user['id']}: {e}")
        raise HTTPException(400, f"Connection failed: {str(e)}")
    finally:
        if bridge:
            await bridge.disconnect()
