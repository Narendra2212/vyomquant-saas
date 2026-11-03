"""
routers/security.py — Security-related endpoints.

SEC-24 adds the three MFA backup-code endpoints `pages/TwoFA.jsx` calls for
lost-2FA-device recovery; see `backend/mfa_backup_codes.py` for the mechanism
and why it exists. Nothing else lived here before — the module docstring that
used to call this "a minimal stub" is gone because it no longer is one.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend_app.backend.mfa_backup_codes import (
    BackupCodeRateLimited,
    generate_and_store_backup_codes,
    recover_account_with_backup_code,
)
from backend_app.core.dependencies import get_current_user, get_service_supabase, require_aal2
from backend_app.core.rate_limit import limiter

logger = logging.getLogger("SecurityRouter")

router = APIRouter()


def _require_service_client():
    service_client = get_service_supabase()
    if not service_client:
        raise HTTPException(
            status_code=503,
            detail="Supabase service-role credentials required for MFA backup codes.",
        )
    return service_client


def _subject_id(user: dict) -> str:
    return user.get("sub") or user.get("id") or ""


class BackupCodeVerifyRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=64)


@router.post("/mfa/backup-codes/generate")
def generate_backup_codes_endpoint(user: dict = Depends(require_aal2)):
    """Issue a fresh set of ten backup codes, invalidating any earlier set.

    Requires AAL2 — a completed MFA challenge — which `pages/TwoFA.jsx` reaches
    right after a successful `supabase.auth.mfa.verify`, whether that is the
    trader's first enrolment or a later "Manage MFA" re-verification. The
    plaintext codes are returned exactly once; this backend never logs or
    persists them, only their SHA-256 hashes.
    """
    service_client = _require_service_client()
    codes = generate_and_store_backup_codes(service_client, _subject_id(user))
    return {"codes": codes, "count": len(codes)}


@router.post("/mfa/backup-codes/verify")
@limiter.limit("10/minute")
def verify_backup_code_endpoint(
    request: Request,
    body: BackupCodeVerifyRequest,
    user: dict = Depends(get_current_user),
):
    """Consume one backup code to recover a lost 2FA device.

    Deliberately `get_current_user`, not `require_aal2`: this is the one path
    a trader who has already failed the real MFA challenge (no device, no
    other session) can still reach — they hold only an AAL1 session, which is
    exactly what made `handleResetFactor` unreachable for them before this
    existed. On a valid code every TOTP factor on the account is removed via
    Supabase's admin MFA API, which signs the account out of every session —
    including this request's own. The trader signs in again and is walked
    through fresh enrolment by `pages/TwoFA.jsx`'s existing "no verified
    factor" path, untouched by this feature.

    Rate-limited twice over: `slowapi`'s 10/minute here is per source address,
    and `mfa_backup_codes.verify_and_consume_backup_code` separately locks the
    ACCOUNT out after five failures regardless of which address they came
    from.
    """
    service_client = _require_service_client()
    try:
        result = recover_account_with_backup_code(service_client, _subject_id(user), body.code)
    except BackupCodeRateLimited as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    if not result.success:
        raise HTTPException(status_code=401, detail="Invalid or already-used backup code.")

    return {"success": True, "unenrolled_factors": list(result.unenrolled_factor_ids)}
