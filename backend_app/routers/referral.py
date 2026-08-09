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

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend_app.core.dependencies import get_current_user, get_request_supabase
from supabase import Client as SupabaseClient

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger("ReferralRouter")

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
    app_url = os.getenv("APP_URL", os.getenv("FRONTEND_URL", "https://vyomquant.com")).rstrip("/")
    return f"{app_url}/signup?ref={referral_code}"


def _ensure_referral_code_exists(user_id: str, supabase: SupabaseClient) -> str:
    """
    Ensure user has a referral code, create if missing.
    Returns the referral code.
    """
    # Check if referral code exists
    code_resp = supabase.table("referral_codes").select("code").eq("user_id", user_id).execute()
    
    if code_resp.data and len(code_resp.data) > 0:
        return code_resp.data[0]["code"]
    
    # Generate new code using database function
    try:
        result = supabase.rpc("create_referral_code_for_user", {"user_uuid": user_id}).execute()
        
        # Fetch the newly created code
        code_resp = supabase.table("referral_codes").select("code").eq("user_id", user_id).execute()
        if code_resp.data and len(code_resp.data) > 0:
            return code_resp.data[0]["code"]
        
        # Fallback: generate code manually
        import secrets
        code = f"VQ-{secrets.token_urlsafe(6).upper()[:6]}"
        supabase.table("referral_codes").insert({"user_id": user_id, "code": code}).execute()
        return code
        
    except Exception as e:
        logger.error(f"Failed to create referral code for user {user_id}: {e}")
        # Fallback to simple code
        import secrets
        code = f"VQ-{secrets.token_urlsafe(6).upper()[:6]}"
        supabase.table("referral_codes").insert({"user_id": user_id, "code": code}).execute()
        return code


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
        
        # Ensure referral code exists
        referral_code = _ensure_referral_code_exists(user_id, supabase)
        
        # Get referral wallet
        wallet_resp = supabase.table("referral_wallets").select("*").eq("user_id", user_id).execute()
        wallet = wallet_resp.data[0] if wallet_resp.data else {
            "pending_balance_usd": 0,
            "approved_balance_usd": 0,
            "paid_balance_usd": 0,
            "lifetime_earnings_usd": 0
        }
        
        # Get referral relationships count
        relationships_resp = supabase.table("referral_relationships").select("status").eq("referrer_id", user_id).execute()
        relationships = relationships_resp.data or []
        
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

        # Ensure referral code exists
        referral_code = _ensure_referral_code_exists(user_id, supabase)
        
        # Get referral wallet
        wallet_resp = supabase.table("referral_wallets").select("*").eq("user_id", user_id).execute()
        wallet = wallet_resp.data[0] if wallet_resp.data else {
            "pending_balance_usd": 0,
            "approved_balance_usd": 0,
            "paid_balance_usd": 0,
            "lifetime_earnings_usd": 0
        }
        
        # Get referral relationships count
        relationships_resp = supabase.table("referral_relationships").select("status").eq("referrer_id", user_id).execute()
        relationships = relationships_resp.data or []
        
        total_referrals = len(relationships)
        active_referrals = sum(1 for r in relationships if r.get("status") == "active")
        
        # Get commission history (last 20)
        commissions_resp = supabase.table("referral_commissions").select("*").eq("referrer_id", user_id).order("created_at", desc=True).limit(20).execute()
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
            for c in commissions_resp.data or []
        ]
        
        # Get payout history (last 20)
        payouts_resp = supabase.table("referral_payouts").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
        payout_history = [
            PayoutRecord(
                id=p["id"],
                amount_usd=float(p["amount_usd"]),
                status=p["status"],
                payment_method=p.get("payment_method"),
                created_at=p["created_at"],
                approved_at=p.get("approved_at"),
                paid_at=p.get("paid_at"),
                rejection_reason=p.get("rejection_reason")
            )
            for p in payouts_resp.data or []
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
        import inspect
        
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
                payment_method=p.get("payment_method"),
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
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    try:
        # Look up referral code
        code_resp = supabase.table("referral_codes").select("user_id", "is_active").eq("code", request_body.referral_code.upper()).execute()
        
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
        logger.error(f"Error validating referral code {request.referral_code}: {e}")
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
    Validates sufficient balance and creates pending payout record.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")
    
    try:
        user_id = user["id"]
        
        # Get user's wallet
        wallet_resp = supabase.table("referral_wallets").select("*").eq("user_id", user_id).execute()
        if not wallet_resp.data or len(wallet_resp.data) == 0:
            raise HTTPException(status_code=404, detail="Referral wallet not found")
        
        wallet = wallet_resp.data[0]
        approved_balance = float(wallet.get("approved_balance_usd", 0))
        
        # Validate sufficient balance
        if payout_request.amount_usd > approved_balance:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient approved balance. Available: ${approved_balance}, Requested: ${payout_request.amount_usd}"
            )
        
        # Validate minimum payout amount (e.g., $10)
        if payout_request.amount_usd < 10:
            raise HTTPException(
                status_code=400,
                detail="Minimum payout amount is $10"
            )
        
        # Create payout record
        payout_resp = supabase.table("referral_payouts").insert({
            "user_id": user_id,
            "amount_usd": payout_request.amount_usd,
            "status": "pending",
            "payment_method": payout_request.payment_method,
            "payment_details": payout_request.payment_details
        }).execute()
        
        if not payout_resp.data or len(payout_resp.data) == 0:
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
