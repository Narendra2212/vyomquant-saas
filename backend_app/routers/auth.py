"""
Authentication Router - Production + Fallback

Handles user signup and signin using Supabase when available.
Falls back to dev tokens if Supabase not configured.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from backend_app.core.dependencies import get_current_user, get_supabase
from backend_app.core.rate_limit import limiter
from backend_app.core.security_vault import SecurityVault
from supabase import Client as SupabaseClient

router = APIRouter()

# In-memory user store for fallback mode
_fallback_users = {}


# ----------------------------------
# REQUEST MODELS
# ----------------------------------
class GoogleAuthRequest(BaseModel):
    id_token: str




# ----------------------------------
# SIGNOUT
# ----------------------------------
@router.post("/signout")
def signout(token: str):
    """
    Sign out user by revoking their session.
    """
    vault = SecurityVault()
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
def google_auth(data: GoogleAuthRequest):
    """
    Authenticate user using Google OAuth token.
    """
    vault = SecurityVault()
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


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    username: str
    phone_number: str | None = None
    referral_code: str | None = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

@router.post("/register", status_code=201)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_data: UserCreate,
    supabase: SupabaseClient = Depends(get_supabase)
):
    try:
        from backend_app.core.security_vault import SecurityVault
        vault = SecurityVault()
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
                return {"access_token": login_res.session.access_token, "token_type": "bearer"}
            else:
                return {"access_token": "email_verification_pending", "message": "Failed to auto-login"}
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

