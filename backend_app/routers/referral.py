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
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, validator

from backend_app.core.dependencies import get_admin_user, get_current_user, get_request_supabase, get_service_supabase
from backend_app.core.rate_limit import limiter
from backend_app.core.runtime_env import local_fallbacks_are_acceptable
from supabase import Client as SupabaseClient

def _rpc_error_code(exc: Exception) -> str:
    """The SQLSTATE a failed ``.rpc()`` call raised, or ``""`` when none is readable.

    REF-01's two RPCs (``create_referral_payout_request``, ``settle_referral_payout``,
    migration ``049_ref01_payout_atomic.sql``) each raise ``RAISE EXCEPTION ...
    USING ERRCODE = 'RFxxx'``. ``postgrest.exceptions.APIError`` carries that SQLSTATE on
    its own ``.code`` attribute (PostgREST relays the Postgres error code verbatim); a
    double that does not wrap postgrest's exception type is read by falling back to
    ``str(exc)``, the same two-step reading ``paper_repository._is_unique_violation``
    already uses for the same reason -- which attribute a driver surfaces differs by
    driver, and PostgREST itself surfaces neither as a guaranteed attribute on every
    double used in tests.
    """
    code = str(getattr(exc, "code", "") or "").strip()
    if code:
        return code
    text = str(exc)
    for candidate in ("RF001", "RF002", "RF003", "RF004"):
        if candidate in text:
            return candidate
    return ""


router = APIRouter()
# REF-02: this used to be its own `Limiter(key_func=get_remote_address)` -- a SEPARATE
# instance from every other router's, with two compounding defects. (1) Behind
# CloudFront/ALB the socket peer `get_remote_address` reads is the shared edge address,
# not the caller's own -- every caller landed in the SAME bucket, so the limit was
# either shared by everyone (one heavy caller exhausts it for all) or trivially useless.
# (2) A bare `Limiter(...)` with no `storage_uri` defaults to slowapi's own in-memory
# store, which is per-POD: in a multi-replica deployment the effective limit becomes the
# configured one times the replica count. The shared `core.rate_limit.limiter` (SEC-10's
# `trusted_client_address`, resolved storage backend) is what every other router in this
# codebase already uses for exactly this reason.
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
    """Response for referral code validation.

    REF-02: ``referrer_id`` (the referrer's raw Supabase user UUID) used to be returned
    here. Nothing in the frontend reads it -- grepped algo22-terminal/src/: the only
    reference was this field's own name in a JSDoc type comment -- so returning it
    disclosed another user's identifier for no caller-facing purpose. ``valid`` and
    ``message`` are the whole contract an unauthenticated-in-practice caller (see
    ``validate_referral_code``'s own docstring) needs to decide whether to submit a code.
    """
    valid: bool
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

    REF-02: ``supabase`` stays in the signature because ``get_request_supabase`` rejects
    a request with NO Authorization header at all -- but it builds its client from
    whatever bearer-token string was supplied, without ever verifying that string is a
    genuinely valid, signed Supabase JWT (``create_request_supabase_async`` does no
    signature or expiry check), and this handler never actually issues a query through
    that client to have Supabase itself reject a bad one. In effect, any non-empty
    Authorization header value passes, with no real identity verification -- this
    endpoint is unauthenticated in substance, matching the finding's own title, even
    though a bare header-presence check remains. Real verification would mean switching
    to a dependency that decodes and checks the JWT itself (e.g. ``get_current_user``),
    which was not attempted here: this fix is scoped to the rate-limiter key and the
    UUID disclosure the remediation note names, not a rewrite of this endpoint's
    authentication.
    """
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

    REF-01 (redesign): the debit of ``referral_wallets.approved_balance_usd`` and the
    ``referral_payouts`` INSERT used to be two separate PostgREST statements, with a
    best-effort compensating re-credit on failure (which could itself fail). They are
    now one call to ``public.create_referral_payout_request`` --
    ``049_ref01_payout_atomic.sql`` -- a single ``SECURITY DEFINER`` Postgres function
    that performs both inside one transaction. If the INSERT fails for any reason, the
    debit the SAME function body already issued rolls back with it: there is no window
    in which the money is out of the wallet and nowhere else, so the compensating
    re-credit this handler used to run is not needed and is not here any more.
    """
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection unavailable")

    from backend_app.core.cache import redis_manager
    user_id = user["id"]
    payout_lock_key = f"lock:referral_payout:{user_id}"
    lock_acquired = False

    try:
        # Acquire short distributed lock (30s). This is a latency optimisation, not a
        # correctness backstop any more: the real correctness guarantee is the
        # `SELECT ... FOR UPDATE` row lock inside create_referral_payout_request, held
        # for that function's whole duration, which a SEPARATE PostgREST statement
        # could never hold. Failing CLOSED on a lock-acquisition error (REF-01) still
        # matters for the reason stated below: it costs a confusing-but-safe "please
        # wait" on a Redis outage rather than ever risking a fail-open race, even though
        # the race it would otherwise guard against is now also closed at the database.
        try:
            lock_acquired = await redis_manager.set(payout_lock_key, "1", nx=True, ex=30)
        except Exception as lock_err:
            logger.warning(f"Redis lock attempt failed for payout {user_id}: {lock_err}")
            lock_acquired = False

        if not lock_acquired:
            raise HTTPException(
                status_code=409,
                detail="A payout request is currently being processed. Please wait."
            )

        # Validate minimum payout amount (e.g., $10). A product policy, not a database
        # invariant, so it stays here rather than in the RPC.
        if payout_request.amount_usd < 10:
            raise HTTPException(
                status_code=400,
                detail="Minimum payout amount is $10"
            )

        try:
            rpc_resp = supabase.rpc("create_referral_payout_request", {
                "p_user_id": user_id,
                "p_amount_usd": payout_request.amount_usd,
                "p_payout_method": payout_request.payment_method,
                "p_payout_details": payout_request.payment_details or {},
            }).execute()
            if inspect.isawaitable(rpc_resp):
                rpc_resp = await rpc_resp
        except Exception as rpc_err:  # noqa: BLE001 — classified by SQLSTATE below
            code = _rpc_error_code(rpc_err)
            if code == "RF001":
                raise HTTPException(
                    status_code=400,
                    detail="Insufficient approved balance for this payout.",
                ) from rpc_err
            if code == "RF002":
                raise HTTPException(
                    status_code=404, detail="Referral wallet not found"
                ) from rpc_err
            if code == "RF003":
                raise HTTPException(
                    status_code=400, detail="Invalid payout request"
                ) from rpc_err
            logger.error(f"Error creating payout request for user {user_id}: {rpc_err}")
            raise HTTPException(
                status_code=500, detail="Failed to create payout request"
            ) from rpc_err

        payout = getattr(rpc_resp, "data", None)
        if not payout:
            logger.error(
                "create_referral_payout_request for user %s returned no row", user_id
            )
            raise HTTPException(status_code=500, detail="Failed to create payout request")

        logger.info(f"Payout request created for user {user_id}, amount: ${payout_request.amount_usd}")

        return {
            "status": "success",
            "payout_id": payout["id"],
            "amount_usd": payout_request.amount_usd,
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


class SettlePayoutRequest(BaseModel):
    """REF-01: the pending -> completed/rejected settlement step."""
    status: str = Field(..., pattern="^(completed|rejected)$")
    reference: Optional[str] = Field(
        None, max_length=200,
        description="For 'completed': the external transfer reference (bank/UPI/PayPal "
                    "transaction id) proving the operator actually sent the money."
    )
    reason: Optional[str] = Field(
        None, max_length=500,
        description="For 'rejected': why (e.g. invalid payout details, failed KYC)."
    )


@router.put("/referral/payouts/{payout_id}/settle")
@limiter.limit("30/minute")
async def settle_payout(
    request: Request,
    payout_id: str,
    body: SettlePayoutRequest,
    admin: dict = Depends(get_admin_user),
    sb: SupabaseClient = Depends(get_service_supabase),
):
    """Operator-only: settle a pending/processing payout as completed or rejected.

    REF-01: this is the "pending -> completed" step the finding's own title names as
    unimplemented -- before this, a `referral_payouts` row could be created and then
    never transition out of 'pending' through any code path in this file.

    WHAT THIS DOES NOT DO. There is no outbound payment-rail integration anywhere in
    this codebase for referral payouts -- Stripe/Razorpay are wired for INCOMING
    subscription payments only. 'completed' therefore records that a human operator
    performed the transfer manually (bank transfer, UPI, PayPal) and is attesting to
    it with `reference`; this endpoint never itself moves money. 'rejected' re-credits
    the wallet balance this payout's own request had already debited -- the balance
    was taken out of the user's "pending payout" bucket at request time
    (`create_payout_request`'s own debit), and a rejection must give it back, or the
    money simply vanishes.

    Runs under the service-role client (not the admin's own JWT): `referral_payouts`
    and `referral_wallets` both carry an owner-only RLS policy
    (`user_id = auth.uid()`), so an admin's own token cannot read or write a payout
    or wallet belonging to the user it is settling -- exactly the SEC-08 pattern this
    codebase already uses for platform actions on another tenant's rows, applied only
    AFTER `get_admin_user`'s own check has passed.

    REF-01 (redesign): the status flip and the rejection re-credit used to be two
    separate PostgREST statements, so a rejection whose re-credit UPDATE failed left
    the payout 'rejected' with the money unaccounted for -- logged CRITICAL, but not
    prevented. Both are now one call to ``public.settle_referral_payout``
    (``049_ref01_payout_atomic.sql``): if the re-credit cannot complete (no wallet row),
    the whole function raises and the status flip rolls back with it, so the payout
    stays 'pending' -- re-settleable once the wallet is reconciled -- rather than
    becoming a lie about where the money went.
    """
    if sb is None:
        raise HTTPException(status_code=503, detail="Database connection unavailable")

    try:
        rpc_resp = sb.rpc("settle_referral_payout", {
            "p_payout_id": payout_id,
            "p_status": body.status,
            "p_reference": body.reference,
            "p_reason": body.reason,
        }).execute()
        if inspect.isawaitable(rpc_resp):
            rpc_resp = await rpc_resp
    except Exception as rpc_err:  # noqa: BLE001 — classified by SQLSTATE below
        code = _rpc_error_code(rpc_err)
        if code == "RF002":
            # Either "no such payout" or "the rejection could not be re-credited
            # because no wallet row exists for its user" -- both are a 404-shaped
            # failure to the caller, and both leave the payout untouched (still
            # 'pending' in the second case, since the whole function rolled back).
            raise HTTPException(
                status_code=404,
                detail="Payout request not found, or could not be re-credited",
            ) from rpc_err
        if code == "RF003":
            raise HTTPException(status_code=400, detail=str(rpc_err)) from rpc_err
        if code == "RF004":
            raise HTTPException(
                status_code=409,
                detail=f"Payout {payout_id} is already settled and cannot be settled "
                       "again.",
            ) from rpc_err
        logger.error(f"Error settling payout {payout_id}: {rpc_err}")
        raise HTTPException(status_code=500, detail="Failed to settle payout") from rpc_err

    payout = getattr(rpc_resp, "data", None)
    if not payout:
        raise HTTPException(status_code=500, detail="Failed to settle payout")

    logger.info(
        "Payout %s settled as %s by admin %s", payout_id, body.status, admin.get("id"),
    )
    return {
        "status": "success",
        "payout_id": payout_id,
        "settled_as": body.status,
    }
