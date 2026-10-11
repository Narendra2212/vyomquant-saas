"""
routers/referral.py — Referral System API

Complete referral system implementation:
- Automatic referral code generation
- Referral relationship tracking
- Commission calculation
- Wallet management
- Payout system

SECURITY FEATURES:
- RLS policies on all tables
- Ownership validation on every request
- Rate limiting on sensitive endpoints
- Self-referral prevention
- Duplicate referral prevention
- Input validation and sanitization
- Comprehensive audit logging

PRINCIPAL ARCHITECT: Referral System Redesign
DATE: 2026-08-01
"""

import asyncio
import inspect
import logging
import os
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.runtime_env import local_fallbacks_are_acceptable
from supabase import Client as SupabaseClient

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger("ReferralRouter")

_service_client = None


def _get_service_client():
    """A cached Supabase service-role client, as `routers/library.py:146` defines it.

    Used by ONE endpoint in this module -- ``POST /referral/validate`` -- and the reason
    is a privilege decision, not convenience.

    Validating a code means reading a row that belongs to somebody else: the referrer's.
    Doing that through the caller's own JWT requires an RLS policy broad enough to let
    any logged-in customer read other customers' `referral_codes` rows, and
    ``006_reconcile_production_database.sql`` supplied exactly that --
    ``USING (user_id = auth.uid() OR is_active = true)``. Since every row is active, the
    predicate reduces to TRUE and a single unfiltered select returns every referral code
    in the business together with the `user_id` that owns it.

    Measured with ``staging/anon_read_sweep.py authenticated``, which seeds a code owned
    by customer A and reads it as customer B:

        referral_codes      authenticated saw 1 of 1 row(s)

    So the privilege moves to the server: the policy is narrowed to own-rows-only (see
    ``migrations/referral_system_redesign.sql``), the lookup runs as `service_role`, and
    the response still discloses only what it disclosed before -- a verdict plus the
    referrer id FOR A CODE THE CALLER ALREADY SUPPLIED. Knowing one code's owner is the
    referral feature working; enumerating all of them is not.

    Like ``routers/library.py``, the degraded ``None`` return is gated on
    ``runtime_env.local_fallbacks_are_acceptable()`` rather than on ``DEV_MODE`` alone, so
    a declared non-local ``ENV`` cannot be downgraded to local semantics by a leftover
    flag (RUNTIME-01). Regression:
    tests/test_environment_precedence_is_centralised.py
    """
    global _service_client
    if _service_client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            if local_fallbacks_are_acceptable():
                return None
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
            )
        from supabase import create_client

        _service_client = create_client(url, key)
    return _service_client


# ═══════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════


class ReferralProfileResponse(BaseModel):
    """Referral profile information"""
    referral_code: str
    referral_link: str
    total_referrals: int
    active_referrals: int
    pending_earnings: float
    approved_earnings: float
    paid_earnings: float
    lifetime_earnings: float


class ReferralStatsResponse(BaseModel):
    """Referral statistics summary"""
    referral_code: str
    referral_link: str
    total_referrals: int
    active_referrals: int
    pending_earnings: float
    approved_earnings: float
    paid_earnings: float
    lifetime_earnings: float
    commission_history: list
    payout_history: list


class CommissionRecord(BaseModel):
    """Single commission record"""
    id: str
    referred_user_id: str
    payment_id: str
    subscription_tier: str
    payment_amount_usd: float
    commission_amount_usd: float
    status: str
    created_at: str
    paid_at: Optional[str] = None
    reversal_reason: Optional[str] = None


class PayoutRecord(BaseModel):
    """Single payout record"""
    id: str
    amount_usd: float
    status: str
    payment_method: Optional[str] = None
    created_at: str
    approved_at: Optional[str] = None
    paid_at: Optional[str] = None
    rejection_reason: Optional[str] = None


class CreatePayoutRequest(BaseModel):
    """Request to create a payout"""
    amount_usd: float = Field(..., gt=0)
    payment_method: str = Field(..., pattern="^(bank_transfer|paypal|upi)$")
    payment_details: dict = Field(..., description="Payment method specific details")

    @validator("amount_usd", pre=True)
    def validate_amount(cls, v):
        if isinstance(v, bool) or not isinstance(v, (int, float, Decimal, str)):
            raise ValueError("Amount must be a number")
        try:
            d = Decimal(str(v))
            if d.is_nan() or d.is_infinite() or d <= 0:
                raise ValueError("Amount must be a positive finite number")
        except Exception:
            raise ValueError("Amount must be a positive finite number")
        return float(d)


class ValidateReferralCodeRequest(BaseModel):
    """Request to validate a referral code"""
    referral_code: str = Field(..., min_length=5, max_length=20)


class ValidateReferralCodeResponse(BaseModel):
    """Response for referral code validation"""
    valid: bool
    referrer_id: Optional[str] = None
    message: str


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════


def _get_referral_link(referral_code: str) -> str:
    """Generate referral link from referral code"""
    app_url = os.getenv("APP_URL", os.getenv("FRONTEND_URL", "https://app.vyomquant.in")).rstrip("/")
    return f"{app_url}/signup?ref={referral_code}"


async def _ensure_referral_code_exists(user_id: str, supabase: SupabaseClient) -> str:
    """
    Ensure user has a referral code, create if missing.
    Returns the referral code.
    """
    # Check if referral code exists
    try:
        res = supabase.table("referral_codes").select("code").eq("user_id", user_id).execute()
        code_resp = await res if inspect.isawaitable(res) else res

        if code_resp and hasattr(code_resp, "data") and code_resp.data and len(code_resp.data) > 0:
            return code_resp.data[0]["code"]

        # Generate new code using database function
        try:
            rpc_res = supabase.rpc("create_referral_code_for_user", {"user_uuid": user_id}).execute()
            if inspect.isawaitable(rpc_res):
                await rpc_res

            # Fetch the newly created code
            res2 = supabase.table("referral_codes").select("code").eq("user_id", user_id).execute()
            code_resp = await res2 if inspect.isawaitable(res2) else res2
            if code_resp and hasattr(code_resp, "data") and code_resp.data and len(code_resp.data) > 0:
                return code_resp.data[0]["code"]

            # Fallback: generate code manually
            import secrets
            code = f"VQ-{secrets.token_urlsafe(6).upper()[:6]}"
            res3 = supabase.table("referral_codes").insert({"user_id": user_id, "code": code}).execute()
            if inspect.isawaitable(res3):
                await res3
            return code

        except Exception as e:
            logger.error(f"Failed to create referral code for user {user_id}: {e}")
            # Fallback to simple code
            import secrets
            code = f"VQ-{secrets.token_urlsafe(6).upper()[:6]}"
            res4 = supabase.table("referral_codes").insert({"user_id": user_id, "code": code}).execute()
            if inspect.isawaitable(res4):
                await res4
            return code
    except Exception as e:
        # If referral_codes table doesn't exist, return a placeholder
        if "PGRST205" in str(e) or "Could not find the table" in str(e):
            logger.warning(f"Referral tables not found, returning placeholder code for user {user_id[:8]}...")
            return "NOT-AVAILABLE"
        else:
            raise


# ═══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/referral/profile")
@limiter.limit("60/minute")
async def get_referral_profile(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Get user's referral profile information.
    Returns referral code, link, and basic statistics.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    try:
        user_id = user["id"]

        # Check if referral tables exist (fallback if migration not run)
        try:
            # Test if referral_codes table exists
            t_res = supabase.table("referral_codes").select("code").limit(1).execute()
            if inspect.isawaitable(t_res):
                await t_res
        except Exception as table_check_error:
            # If referral tables don't exist, return safe empty response
            if "PGRST205" in str(table_check_error) or "Could not find the table" in str(table_check_error):
                logger.warning(f"Referral tables not found in database, returning empty profile for user {user_id[:8]}...")
                return ReferralProfileResponse(
                    referral_code="NOT-AVAILABLE",
                    referral_link=_get_referral_link("NOT-AVAILABLE"),
                    total_referrals=0,
                    active_referrals=0,
                    pending_earnings=0.0,
                    approved_earnings=0.0,
                    paid_earnings=0.0,
                    lifetime_earnings=0.0
                )
            else:
                # Different error, re-raise
                raise

        # Ensure referral code exists
        referral_code = await _ensure_referral_code_exists(user_id, supabase)
        
        # Parallelize independent queries
        async def _fetch_wallet():
            res = supabase.table("referral_wallets").select("*").eq("user_id", user_id).execute()
            return await res if inspect.isawaitable(res) else res

        async def _fetch_relationships():
            res = supabase.table("referral_relationships").select("status").eq("referrer_id", user_id).execute()
            return await res if inspect.isawaitable(res) else res

        wallet_resp, relationships_resp = await asyncio.gather(
            _fetch_wallet(),
            _fetch_relationships(),
            return_exceptions=True
        )

        wallet = (wallet_resp.data[0] if wallet_resp and hasattr(wallet_resp, "data") and wallet_resp.data else {
            "pending_balance_usd": 0,
            "approved_balance_usd": 0,
            "paid_balance_usd": 0,
            "lifetime_earnings_usd": 0
        }) if not isinstance(wallet_resp, Exception) else {
            "pending_balance_usd": 0,
            "approved_balance_usd": 0,
            "paid_balance_usd": 0,
            "lifetime_earnings_usd": 0
        }
        
        relationships = (relationships_resp.data or [] if relationships_resp and hasattr(relationships_resp, "data") else []) if not isinstance(relationships_resp, Exception) else []
        
        total_referrals = len(relationships)
        active_referrals = sum(1 for r in relationships if r.get("status") == "active")
        
        logger.info(f"Referral profile fetched for user {user_id}")
        
        return ReferralProfileResponse(
            referral_code=referral_code,
            referral_link=_get_referral_link(referral_code),
            total_referrals=total_referrals,
            active_referrals=active_referrals,
            pending_earnings=float(wallet.get("pending_balance_usd", 0)),
            approved_earnings=float(wallet.get("approved_balance_usd", 0)),
            paid_earnings=float(wallet.get("paid_balance_usd", 0)),
            lifetime_earnings=float(wallet.get("lifetime_earnings_usd", 0))
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching referral profile for user {user['id']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch referral profile")


@router.get("/referral/stats")
@limiter.limit("60/minute")
async def get_referral_stats(
    request: Request,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Get comprehensive referral statistics.
    Includes commission history and payout history.
    """
    if not supabase:
        # DEV_MODE fallback — same pattern as user.py endpoints (return safe empty stub)
        return {
            "status": "active",
            "referral_code": "DEV-OFFLINE",
            "referral_link": _get_referral_link("DEV-OFFLINE"),
            "total_referrals": 0,
            "active_referrals": 0,
            "pending_earnings": 0.0,
            "approved_earnings": 0.0,
            "paid_earnings": 0.0,
            "lifetime_earnings": 0.0,
            "commission_history": [],
            "payout_history": [],
            "available_discounts": 0,
        }

    try:
        user_id = user["id"]

        # Check if referral tables exist (fallback if migration not run)
        try:
            # Test if referral_codes table exists
            t_res = supabase.table("referral_codes").select("code").limit(1).execute()
            if inspect.isawaitable(t_res):
                await t_res
        except Exception as table_check_error:
            # If referral tables don't exist, return safe empty response
            if "PGRST205" in str(table_check_error) or "Could not find the table" in str(table_check_error):
                logger.warning(f"Referral tables not found in database, returning empty stats for user {user_id[:8]}...")
                return {
                    "status": "active",
                    "referral_code": "NOT-AVAILABLE",
                    "referral_link": _get_referral_link("NOT-AVAILABLE"),
                    "total_referrals": 0,
                    "active_referrals": 0,
                    "pending_earnings": 0.0,
                    "approved_earnings": 0.0,
                    "paid_earnings": 0.0,
                    "lifetime_earnings": 0.0,
                    "commission_history": [],
                    "payout_history": [],
                }
            else:
                # Different error, re-raise
                raise

        # If we get here, tables exist - proceed with normal logic
        # Ensure referral code exists
        referral_code = await _ensure_referral_code_exists(user_id, supabase)

        # Parallelize independent queries using asyncio.gather
        async def _fetch_wallet():
            res = supabase.table("referral_wallets").select("*").eq("user_id", user_id).execute()
            return await res if inspect.isawaitable(res) else res

        async def _fetch_relationships():
            res = supabase.table("referral_relationships").select("status").eq("referrer_id", user_id).execute()
            return await res if inspect.isawaitable(res) else res

        async def _fetch_commissions():
            res = supabase.table("referral_commissions").select("*").eq("referrer_id", user_id).order("created_at", desc=True).limit(20).execute()
            return await res if inspect.isawaitable(res) else res

        async def _fetch_payouts():
            res = supabase.table("referral_payouts").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
            return await res if inspect.isawaitable(res) else res

        wallet_resp, relationships_resp, commissions_resp, payouts_resp = await asyncio.gather(
            _fetch_wallet(),
            _fetch_relationships(),
            _fetch_commissions(),
            _fetch_payouts(),
            return_exceptions=True
        )

        wallet = (wallet_resp.data[0] if wallet_resp and hasattr(wallet_resp, "data") and wallet_resp.data else {
            "pending_balance_usd": 0,
            "approved_balance_usd": 0,
            "paid_balance_usd": 0,
            "lifetime_earnings_usd": 0
        }) if not isinstance(wallet_resp, Exception) else {
            "pending_balance_usd": 0,
            "approved_balance_usd": 0,
            "paid_balance_usd": 0,
            "lifetime_earnings_usd": 0
        }

        relationships = (relationships_resp.data or [] if relationships_resp and hasattr(relationships_resp, "data") else []) if not isinstance(relationships_resp, Exception) else []

        total_referrals = len(relationships)
        active_referrals = sum(1 for r in relationships if r.get("status") == "active")

        commission_raw = (commissions_resp.data or [] if commissions_resp and hasattr(commissions_resp, "data") else []) if not isinstance(commissions_resp, Exception) else []
        commission_history = [
            CommissionRecord(
                id=c["id"],
                referred_user_id=c["referred_id"],
                payment_id=c["payment_id"],
                subscription_tier=c["subscription_tier"],
                payment_amount_usd=float(c["payment_amount_usd"]),
                commission_amount_usd=float(c["commission_amount_usd"]),
                status=c["status"],
                created_at=c["created_at"],
                paid_at=c.get("paid_at"),
                reversal_reason=c.get("reversal_reason")
            )
            for c in commission_raw
        ]

        payout_raw = (payouts_resp.data or [] if payouts_resp and hasattr(payouts_resp, "data") else []) if not isinstance(payouts_resp, Exception) else []
        payout_history = [
            PayoutRecord(
                id=p["id"],
                amount_usd=float(p["amount_usd"]),
                status=p["status"],
                # Phase 10: the provisioned column is `payout_method`; prefer it and fall
                # back to `payment_method` for any legacy row written under the other
                # migration's shape. The response field stays `payment_method`.
                payment_method=p.get("payout_method") or p.get("payment_method"),
                created_at=p["created_at"],
                approved_at=p.get("approved_at"),
                paid_at=p.get("paid_at"),
                rejection_reason=p.get("rejection_reason")
            )
            for p in payout_raw
        ]

        return ReferralStatsResponse(
            referral_code=referral_code,
            referral_link=_get_referral_link(referral_code),
            total_referrals=total_referrals,
            active_referrals=active_referrals,
            pending_earnings=float(wallet.get("pending_balance_usd", 0)),
            approved_earnings=float(wallet.get("approved_balance_usd", 0)),
            paid_earnings=float(wallet.get("paid_balance_usd", 0)),
            lifetime_earnings=float(wallet.get("lifetime_earnings_usd", 0)),
            commission_history=commission_history,
            payout_history=payout_history
        )
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        
        # Safe diagnostic logging - no sensitive data
        exc_type = type(e).__name__
        exc_module = type(e).__module__
        exc_message = str(e)
        
        # Get caller info
        frame = inspect.currentframe()
        caller_filename = frame.f_back.f_code.co_filename if frame.f_back else "unknown"
        caller_lineno = frame.f_back.f_lineno if frame.f_back else 0
        
        # Log comprehensive diagnostic info
        logger.error(
            f"[REFERRAL_STATS_ENDPOINT] Exception details: "
            f"endpoint=/api/referral/stats, "
            f"exception_type={exc_type}, "
            f"exception_module={exc_module}, "
            f"exception_message={exc_message}, "
            f"caller_file={caller_filename}, "
            f"caller_line={caller_lineno}, "
            f"user_id_truncated={user['id'][:8] if user.get('id') else 'missing'}..."
        )
        
        # Log full traceback for debugging
        logger.error(f"[REFERRAL_STATS_ENDPOINT] Full traceback:\n{traceback.format_exc()}")
        
        raise HTTPException(status_code=500, detail="Failed to fetch referral stats")


@router.get("/referral/commissions")
async def get_commission_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, pattern="^(pending|approved|paid|reversed)$"),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Get paginated commission history.
    Supports filtering by status.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    try:
        user_id = user["id"]
        
        query = supabase.table("referral_commissions").select("*").eq("referrer_id", user_id)
        
        if status:
            query = query.eq("status", status)
        
        resp = query.order("created_at", desc=True).limit(limit).offset(offset).execute()
        
        commissions = [
            CommissionRecord(
                id=c["id"],
                referred_user_id=c["referred_id"],
                payment_id=c["payment_id"],
                subscription_tier=c["subscription_tier"],
                payment_amount_usd=float(c["payment_amount_usd"]),
                commission_amount_usd=float(c["commission_amount_usd"]),
                status=c["status"],
                created_at=c["created_at"],
                paid_at=c.get("paid_at"),
                reversal_reason=c.get("reversal_reason")
            )
            for c in resp.data or []
        ]
        
        return {
            "commissions": commissions,
            "count": len(commissions),
            "limit": limit,
            "offset": offset
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching commission history for user {user['id']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch commission history")


@router.get("/referral/payouts")
async def get_payout_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, pattern="^(pending|approved|processing|paid|failed|rejected)$"),
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Get paginated payout history.
    Supports filtering by status.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    try:
        user_id = user["id"]
        
        query = supabase.table("referral_payouts").select("*").eq("user_id", user_id)
        
        if status:
            query = query.eq("status", status)
        
        resp = query.order("created_at", desc=True).limit(limit).offset(offset).execute()
        
        payouts = [
            PayoutRecord(
                id=p["id"],
                amount_usd=float(p["amount_usd"]),
                status=p["status"],
                # Phase 10: prefer the provisioned `payout_method` column; fall back to
                # `payment_method` for legacy rows. Response field stays `payment_method`.
                payment_method=p.get("payout_method") or p.get("payment_method"),
                created_at=p["created_at"],
                approved_at=p.get("approved_at"),
                paid_at=p.get("paid_at"),
                rejection_reason=p.get("rejection_reason")
            )
            for p in resp.data or []
        ]
        
        return {
            "payouts": payouts,
            "count": len(payouts),
            "limit": limit,
            "offset": offset
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching payout history for user {user['id']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch payout history")


@router.post("/referral/validate")
@limiter.limit("30/minute")
async def validate_referral_code(
    request: Request,
    request_body: ValidateReferralCodeRequest,
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Validate a referral code before signup.
    Returns referrer information if valid.
    """
    # `supabase` stays in the signature: `get_request_supabase` is what rejects an
    # unauthenticated caller with 401, so removing it would make this endpoint public.
    # The LOOKUP, however, deliberately does not use it -- see `_get_service_client`.
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")

    svc = _get_service_client()
    if not svc:
        raise HTTPException(status_code=503, detail="Database connection unavailable")

    try:
        # Look up referral code. Runs as `service_role` so that the RLS policy on
        # `referral_codes` can stay own-rows-only; the filter here is the whole
        # authorisation story -- one exact code, supplied by the caller.
        code_resp = svc.table("referral_codes").select("user_id", "is_active").eq("code", request_body.referral_code.upper()).execute()
        
        if not code_resp.data or len(code_resp.data) == 0:
            return ValidateReferralCodeResponse(
                valid=False,
                message="Invalid referral code"
            )
        
        referrer = code_resp.data[0]
        
        if not referrer.get("is_active"):
            return ValidateReferralCodeResponse(
                valid=False,
                message="Referral code is inactive"
            )
        
        return ValidateReferralCodeResponse(
            valid=True,
            referrer_id=referrer["user_id"],
            message="Valid referral code"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        # `request` is the FastAPI Request; the body is `request_body`. This line used to
        # read `request.referral_code`, so the handler itself raised AttributeError and
        # replaced the real error with a misleading one. The code is not logged at all
        # now -- a referral code is a credential of sorts, and it belongs in the response
        # to its owner rather than in a shared log.
        logger.error("Error validating referral code: %s", e)
        raise HTTPException(status_code=500, detail="Failed to validate referral code")


@router.post("/referral/payouts")
@limiter.limit("10/minute")
async def create_payout_request(
    request: Request,
    payout_request: CreatePayoutRequest,
    user: dict = Depends(get_current_user),
    supabase: SupabaseClient = Depends(get_request_supabase),
):
    """
    Create a payout request.
    Validates sufficient balance under distributed lock and deducts approved balance.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    from backend_app.core.cache import redis_manager
    user_id = user["id"]
    payout_lock_key = f"lock:referral_payout:{user_id}"
    lock_acquired = False
    
    try:
        # Acquire short distributed lock (30s) to prevent concurrent double-spend
        try:
            lock_acquired = await redis_manager.set(payout_lock_key, "1", nx=True, ex=30)
        except Exception as lock_err:
            logger.warning(f"Redis lock attempt failed for payout {user_id}: {lock_err}")
            lock_acquired = True  # Proceed if Redis is unavailable, fallback to DB check
        
        if not lock_acquired:
            raise HTTPException(
                status_code=409,
                detail="A payout request is currently being processed. Please wait."
            )
        
        # Get user's wallet
        wallet_resp = supabase.table("referral_wallets").select("*").eq("user_id", user_id).execute()
        if inspect.isawaitable(wallet_resp):
            wallet_resp = await wallet_resp
        
        if not wallet_resp.data or len(wallet_resp.data) == 0:
            raise HTTPException(status_code=404, detail="Referral wallet not found")
        
        wallet = wallet_resp.data[0]
        approved_balance = float(wallet.get("approved_balance_usd", 0))
        
        # Validate minimum payout amount (e.g., $10)
        if payout_request.amount_usd < 10:
            raise HTTPException(
                status_code=400,
                detail="Minimum payout amount is $10"
            )
        
        # Validate sufficient balance
        if payout_request.amount_usd > approved_balance:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient approved balance. Available: ${approved_balance:.2f}, Requested: ${payout_request.amount_usd:.2f}"
            )
        
        # Deduct approved balance immediately from wallet with optimistic concurrency guard
        new_approved_balance = max(0.0, approved_balance - payout_request.amount_usd)
        update_wallet_resp = supabase.table("referral_wallets").update({
            "approved_balance_usd": new_approved_balance,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("user_id", user_id).gte("approved_balance_usd", payout_request.amount_usd).execute()
        if inspect.isawaitable(update_wallet_resp):
            update_wallet_resp = await update_wallet_resp
        
        if not update_wallet_resp or not getattr(update_wallet_resp, "data", None):
            raise HTTPException(
                status_code=409,
                detail="Concurrent wallet modification detected. Payout request aborted."
            )
        
        # Create payout record.
        #
        # SCHEMA CONTRACT (Phase 10 fix): the provisioned `referral_payouts` table
        # (migrations/006_reconcile_production_database.sql, and the live DB) names its
        # columns `payout_method` (VARCHAR(50) NOT NULL) and `payout_details` (JSONB). This
        # insert previously wrote `payment_method` / `payment_details`, which are NOT columns
        # of the provisioned table -- so on the real schema it wrote a non-existent column
        # and left the NOT NULL `payout_method` unset, failing the insert and 500-ing the
        # payout request after the wallet had already been debited (then rolled back below).
        # The API's own field stays `payment_method` (CreatePayoutRequest / PayoutRecord) so
        # the client contract is unchanged; only the DB column names are corrected. A second
        # migration, `migrations/referral_system_redesign.sql`, declares the OTHER shape
        # (`payment_method` nullable), but `CREATE TABLE IF NOT EXISTS` means the `006`
        # definition is the one materialised on this database, and migration 027 builds on
        # that same table -- so `payout_method` is the authoritative column name.
        # THE DEBIT ABOVE IS NOW UNCOMPENSATED UNTIL A PAYOUT ROW EXISTS.
        #
        # Phase 10 / WS-D. The compensating re-credit used to live inside
        # `if not payout_resp.data`, i.e. it covered ONE failure mode: an insert that
        # came back empty. An insert that RAISES -- PostgREST error, RLS denial, CHECK or
        # FK violation, timeout, Supabase 5xx -- produces no `payout_resp` at all and
        # jumped straight to this handler's `except Exception`, which only logged. The
        # referrer's approved balance stayed reduced with NO payout row of any kind, and
        # nothing reconciles that: there is no referral audit table and no settlement job
        # (see the settlement gap reported in the same workstream).
        #
        # Measured, not inferred: tests/test_referral_payout_debit_is_compensated_on_
        # insert_failure.py drives this handler with a stub whose payout insert raises.
        # Before this change the balance went 500.00 -> 450.00 with zero payout rows,
        # while the returns-empty control compensated correctly -- so the two paths
        # genuinely differed.
        #
        # Both failure modes now run the SAME compensation, and a compensation that
        # itself fails is logged CRITICAL with the figures needed to repair it by hand,
        # because that is the one case where money really is unaccounted for and it must
        # not be swallowed into a generic 500.
        async def _compensate_debit(cause: str) -> None:
            """Restore the pre-debit approved balance. Safe to call at most once."""
            try:
                rollback_resp = supabase.table("referral_wallets").update({
                    "approved_balance_usd": approved_balance
                }).eq("user_id", user_id).execute()
                if inspect.isawaitable(rollback_resp):
                    rollback_resp = await rollback_resp
                if not getattr(rollback_resp, "data", None):
                    raise RuntimeError("compensating update matched no wallet row")
                logger.warning(
                    "Referral payout insert failed (%s) for user %s; approved balance "
                    "restored to %.2f",
                    cause, user_id, approved_balance,
                )
            except Exception as comp_err:  # noqa: BLE001
                # Do not re-raise: the caller must still answer 500 for the payout. This
                # log IS the recovery record.
                logger.critical(
                    "REFERRAL PAYOUT DEBIT NOT COMPENSATED. user=%s amount=%.2f "
                    "balance_before=%.2f balance_after_debit=%.2f payout_row=none "
                    "insert_failure=%s compensation_failure=%s — this balance is "
                    "unaccounted for and needs manual reconciliation.",
                    user_id, payout_request.amount_usd, approved_balance,
                    new_approved_balance, cause, comp_err,
                )

        try:
            payout_resp = supabase.table("referral_payouts").insert({
                "user_id": user_id,
                "amount_usd": payout_request.amount_usd,
                "status": "pending",
                "payout_method": payout_request.payment_method,
                "payout_details": payout_request.payment_details,
            }).execute()
            if inspect.isawaitable(payout_resp):
                payout_resp = await payout_resp
        except Exception as insert_err:  # noqa: BLE001
            await _compensate_debit(f"{type(insert_err).__name__}: {insert_err}")
            raise HTTPException(
                status_code=500, detail="Failed to create payout request"
            ) from insert_err

        if not payout_resp.data or len(payout_resp.data) == 0:
            await _compensate_debit("insert returned no rows")
            raise HTTPException(status_code=500, detail="Failed to create payout request")
        
        logger.info(f"Payout request created for user {user_id}, amount: ${payout_request.amount_usd}")
        
        return {
            "status": "success",
            "payout_id": payout_resp.data[0]["id"],
            "amount_usd": payout_request.amount_usd,
            "status": "pending"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating payout request for user {user['id']}: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payout request")
    finally:
        if lock_acquired:
            try:
                await redis_manager.delete(payout_lock_key)
            except Exception:
                pass
