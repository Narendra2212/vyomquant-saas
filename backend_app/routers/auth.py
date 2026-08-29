# -*- coding: utf-8 -*-
"""
Authentication Router - Production + Fallback

SECURITY: Fallback mode removed for production safety.
All authentication must go through Supabase.
"""
from typing import Optional
import logging
import os
import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from backend_app.core.dependencies import get_current_user, get_supabase
from backend_app.core.rate_limit import limiter
from backend_app.core.supabase_connection import SupabaseConnection
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)

router = APIRouter()

# Fallback mode removed for production safety
# _fallback_users removed to prevent authentication bypass


# ----------------------------------
# REQUEST MODELS
# ----------------------------------
class GoogleAuthRequest(BaseModel):
    id_token: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    username: str
    phone_number: Optional[str] = None
    referral_code: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


# ----------------------------------
# SIGNOUT
# ----------------------------------
@router.post("/signout")
def signout(
    token: str,
    current_user: dict = Depends(get_current_user)  # BE-CRITICAL-001 FIX: Require authentication
):
    """
    Sign out user by revoking their session.
    
    BE-CRITICAL-001 FIX: Now requires authentication to prevent unauthorized signout.
    SECURITY: No fallback mode - must use Supabase authentication.
    """
    vault = SupabaseConnection()
    client = vault.get_client()
    
    if not client:
        raise HTTPException(
            status_code=503,
            detail="Supabase authentication required. Configure SUPABASE_URL and SUPABASE_ANON_KEY."
        )
    
    try:
        client.auth.sign_out()
        return {"message": "Signed out successfully"}
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Signout failed: {str(e)}"
        )


# ----------------------------------
# GOOGLE OAUTH
# ----------------------------------
@router.post("/google")
@limiter.limit("10/minute")  # BE-CRITICAL-002 FIX: Add rate limiting to prevent account enumeration
def google_auth(request: Request, data: GoogleAuthRequest):
    """
    Authenticate user using Google OAuth token.
    """
    vault = SupabaseConnection()
    client = vault.get_client()
    
    if not client:
        raise HTTPException(
            status_code=503,
            detail="Supabase authentication required. Configure SUPABASE_URL and SUPABASE_ANON_KEY."
        )
    
    try:
        # Verify Google ID token with Supabase
        res = client.auth.sign_in_with_id_token({
            "provider": "google",
            "id_token": data.id_token,
            "access_token": data.id_token
        })
        
        return {
            "access_token": res.session.access_token,
            "token_type": "bearer",
            "user": res.user
        }
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Google authentication failed: {str(e)}"
        )
# ----------------------------------
# CURRENT USER PROFILE
# ----------------------------------
@router.get("/me")
def get_current_user_profile(user: dict = Depends(get_current_user)):
    """
    Get current authenticated user's profile.
    Fast (<50ms) - uses JWT token claims, no Supabase network call.
    """
    logger.debug(f"User profile fetched: {user.get('email')}")
    return {
        "id": user.get("sub") or user.get("id"),
        "email": user.get("email"),
        "role": user.get("role") or user.get("app_metadata", {}).get("role", "user")
    }


# ----------------------------------
# REGISTER & LOGIN (Supabase)
# ----------------------------------
@router.post("/register", status_code=201)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_data: UserCreate,
    supabase: SupabaseClient = Depends(get_supabase)
):
    """
    Register a new user account.

    Phase 7B F-01 REMEDIATION:
    - email_confirm=True (service-role admin bypass) has been removed.
    - Registration now uses the standard anon client sign_up(), which honours
      the Supabase project's email-confirmation setting.
    - If email confirmation is required (project default), the user receives
      verification_required=True and must verify before gaining API access.
    - The 'email_verification_pending' fake-token placeholder (F-14) is removed;
      the response now uses a typed field so API clients cannot mistake it for a
      real JWT.

    Referral code processing still uses the admin client when available,
    but ONLY after the user record has been created via the standard flow.
    """
    try:
        # Phase 7B F-01: Use standard anon sign_up — respects Supabase email-confirmation setting.
        # Do NOT use admin.create_user(email_confirm=True) as that silently bypasses verification.
        res = supabase.auth.sign_up(
            {
                "email": user_data.email,
                "password": user_data.password,
                "options": {
                    "data": {
                        "username": user_data.username,
                        "phone": user_data.phone_number,
                    }
                },
            }
        )

        if not res.user:
            raise HTTPException(status_code=400, detail="Registration failed: no user returned from Supabase.")

        user_id = res.user.id

        # Process referral code using service-role admin client (post-creation, isolated)
        if user_id and getattr(user_data, "referral_code", None):
            try:
                from backend_app.core.supabase_connection import SupabaseConnection
                admin_client = SupabaseConnection().get_client()
                if admin_client:
                    ref_code = user_data.referral_code.upper().strip()
                    referral_code_res = admin_client.table("referral_codes").select("user_id", "id").eq("code", ref_code).execute()
                    if referral_code_res.data and len(referral_code_res.data) > 0:
                        referrer_data = referral_code_res.data[0]
                        referrer_id = referrer_data["user_id"]
                        referral_code_id = referrer_data["id"]
                        if referrer_id == user_id:
                            logger.warning(f"Self-referral attempt blocked for user {user_id}")
                        else:
                            existing_referral = admin_client.table("referral_relationships").select("id").eq("referred_id", user_id).execute()
                            if not existing_referral.data or len(existing_referral.data) == 0:
                                admin_client.table("referral_relationships").insert({
                                    "referrer_id": referrer_id,
                                    "referred_id": user_id,
                                    "referral_code_id": referral_code_id,
                                    "status": "pending"
                                }).execute()
                                admin_client.table("profiles").update({
                                    "referred_by_user_id": referrer_id
                                }).eq("id", user_id).execute()
                                logger.info(f"Referral relationship created: {referrer_id} referred {user_id} using code {ref_code}")
                            else:
                                logger.warning(f"User {user_id} already has a referrer, ignoring duplicate referral")
                    else:
                        logger.warning(f"Invalid referral code: {ref_code}")
                else:
                    logger.warning(f"Referral code {user_data.referral_code} ignored: admin client not available")
            except Exception as ref_err:
                logger.error(f"Failed to process referral code {user_data.referral_code}: {ref_err}")

        # If Supabase returned a session, the project has email confirmation disabled.
        if res.session:
            return {
                "access_token": res.session.access_token,
                "token_type": "bearer",
                "verification_required": False,
            }

        # Email confirmation is required — user must verify before gaining access.
        return {
            "token_type": "bearer",
            "verification_required": True,
            "message": "Registration successful. Please check your email to verify your account before signing in.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    credentials: UserLogin,
    supabase: SupabaseClient = Depends(get_supabase)
):
    try:
        res = supabase.auth.sign_in_with_password(
            {
                "email": credentials.email,
                "password": credentials.password,
            }
        )
        if res.session:
            return {"access_token": res.session.access_token, "token_type": "bearer", "user": res.user}
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


# ----------------------------------
# WEBSOCKET TICKET  (Phase 7B F-05)
# ----------------------------------

_WS_TICKET_TTL_SECONDS = 30
_WS_TICKET_REDIS_PREFIX = "ws_ticket:"


@router.post("/ws-ticket", status_code=200)
@limiter.limit("30/minute")
async def issue_ws_ticket(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """
    Phase 7B F-05 REMEDIATION: Issue a short-lived WebSocket authentication ticket.
    """
    try:
        from backend_app.core.cache import redis_manager

        ticket = secrets.token_urlsafe(32)
        redis_key = f"{_WS_TICKET_REDIS_PREFIX}{ticket}"

        user_id = current_user.get("id") or current_user.get("sub")
        if not user_id:
            raise HTTPException(status_code=400, detail="Cannot issue WS ticket: user identity missing.")

        if redis_manager.pool:
            await redis_manager.setex(redis_key, _WS_TICKET_TTL_SECONDS, user_id)
        else:
            logger.warning("[WS-Ticket] Redis unavailable; ticket verification fallback.")

        logger.info(f"[WS-Ticket] Issued ticket for user {user_id} (TTL={_WS_TICKET_TTL_SECONDS}s)")
        return {
            "ticket": ticket,
            "ttl_seconds": _WS_TICKET_TTL_SECONDS,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[WS-Ticket] Failed to issue ticket: {e}")
        raise HTTPException(status_code=500, detail="Failed to issue WebSocket ticket.")

