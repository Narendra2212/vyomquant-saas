# -*- coding: utf-8 -*-
"""
Authentication Router - Production + Fallback
"""
from typing import Optional
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from backend_app.core.dependencies import get_current_user, get_supabase
from backend_app.core.rate_limit import limiter
from backend_app.core.supabase_connection import SupabaseConnection
from supabase import Client as SupabaseClient

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory user store for fallback mode
_fallback_users = {}


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
    """
    vault = SupabaseConnection()
    client = vault.get_client()
    
    if client:
        try:
            client.auth.sign_out()
            return {"message": "Signed out successfully"}
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Signout failed: {str(e)}"
            )
    else:
        # Fallback: Nothing to do
        return {"message": "Signed out (fallback mode)"}


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
    print(" USER PROFILE FETCHED:", user.get("email"))
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
    try:
        from backend_app.core.supabase_connection import SupabaseConnection
        vault = SupabaseConnection()
        admin_client = vault.get_client()
        
        if admin_client:
            # Use admin client to auto-confirm user
            res = admin_client.auth.admin.create_user({
                "email": user_data.email,
                "password": user_data.password,
                "email_confirm": True,
                "user_metadata": {
                    "username": user_data.username,
                    "phone": user_data.phone_number,
                }
            })
            
            # Immediately login to get a session token
            login_res = supabase.auth.sign_in_with_password({
                "email": user_data.email,
                "password": user_data.password
            })
            if login_res.session:
                user_id = login_res.user.id
                access_token = login_res.session.access_token
            else:
                user_id = res.user.id
                access_token = "email_verification_pending"

            # Apply referral logic using new referral system
            if getattr(user_data, "referral_code", None):
                ref_code = user_data.referral_code.upper().strip()
                try:
                    # Validate referral code using new referral_codes table
                    referral_code_res = admin_client.table("referral_codes").select("user_id", "id").eq("code", ref_code).execute()
                    
                    if referral_code_res.data and len(referral_code_res.data) > 0:
                        referrer_data = referral_code_res.data[0]
                        referrer_id = referrer_data["user_id"]
                        referral_code_id = referrer_data["id"]
                        
                        # Prevent self-referral
                        if referrer_id == user_id:
                            logger.warning(f"Self-referral attempt blocked for user {user_id}")
                        else:
                            # Check if user already has a referrer
                            existing_referral = admin_client.table("referral_relationships").select("id").eq("referred_id", user_id).execute()
                            
                            if not existing_referral.data or len(existing_referral.data) == 0:
                                # Create referral relationship using new schema
                                admin_client.table("referral_relationships").insert({
                                    "referrer_id": referrer_id,
                                    "referred_id": user_id,
                                    "referral_code_id": referral_code_id,
                                    "status": "pending"
                                }).execute()
                                
                                # Update profiles table for backward compatibility
                                admin_client.table("profiles").update({
                                    "referred_by_user_id": referrer_id
                                }).eq("id", user_id).execute()
                                
                                logger.info(f"Referral relationship created: {referrer_id} referred {user_id} using code {ref_code}")
                            else:
                                logger.warning(f"User {user_id} already has a referrer, ignoring duplicate referral")
                    else:
                        logger.warning(f"Invalid referral code: {ref_code}")
                        
                except Exception as ref_err:
                    logger.error(f"Failed to process referral code {ref_code}: {ref_err}")

            if login_res.session:
                return {"access_token": access_token, "token_type": "bearer"}
            else:
                return {"access_token": access_token, "message": "Failed to auto-login"}
        else:
            # Fallback to standard anon signup
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
            
            user_id = res.user.id if res.user else None

            # Apply referral logic if possible (requires admin_client which might not be available here, 
            # but we can try using the standard client if RLS permits, else we skip or log)
            if user_id and getattr(user_data, "referral_code", None):
                logger.warning(f"Referral code {user_data.referral_code} ignored during anon signup due to missing admin_client")

            if res.session:
                return {"access_token": res.session.access_token, "token_type": "bearer"}
            else:
                return {
                    "access_token": "email_verification_pending",
                    "message": "Check email",
                }

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

