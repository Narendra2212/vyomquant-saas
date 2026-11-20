"""REF-ATTR-01: the one rule for attributing a new account to the referrer whose code it used.

Found 2026-11-19 in the final launch certification: the app hands out ``/signup?ref=CODE``,
but the signup page never read ``ref`` and signed up with ``supabase.auth.signUp`` directly,
and the ``handle_new_user`` trigger knows nothing of referrals. The only attribution code was
inside ``POST /api/auth/register``, which the UI never calls -- so no link the referral
program issued could ever credit anyone. This module holds that rule once; the register
endpoint and the new ``POST /api/referral/claim`` (called by the app after a ``ref`` signup)
both use it.

Rules, each one an abuse case:
* the code must exist and be active;
* no self-referral;
* an account is attributed at most once (the first claim wins);
* a claim is accepted only within ``CLAIM_WINDOW`` of the account's creation, so an
  established user cannot later attach themselves to whichever referrer pays best.

Writes go through a SERVICE-ROLE client (``referral_relationships`` and
``profiles.referred_by_user_id`` are not customer-writable), and the user id is always the
authenticated caller's, never one from the request body.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: How long after signup a referral may still be claimed. Signup -> email confirmation ->
#: first sign-in normally takes minutes; a week covers a late confirmation without leaving
#: the door open indefinitely.
CLAIM_WINDOW = timedelta(days=7)

ATTRIBUTED = "attributed"
ALREADY_ATTRIBUTED = "already_attributed"
INVALID_CODE = "invalid_code"
SELF_REFERRAL = "self_referral"
WINDOW_CLOSED = "window_closed"


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def attribute_referral(
    admin_client: Any,
    user_id: str,
    code: str,
    *,
    account_created_at: Any = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Attribute ``user_id`` to the owner of ``code``. Returns ``{"status", ...}``; never raises
    for a business refusal (invalid code, self-referral, ...), only for a failed read/write."""
    ref_code = str(code or "").upper().strip()
    if not ref_code or not user_id:
        return {"status": INVALID_CODE}

    created = _parse_ts(account_created_at)
    current = now or datetime.now(timezone.utc)
    if created is not None and current - created > CLAIM_WINDOW:
        return {"status": WINDOW_CLOSED}

    found = (
        admin_client.table("referral_codes")
        .select("id,user_id,is_active")
        .eq("code", ref_code)
        .execute()
    )
    rows = [r for r in (getattr(found, "data", None) or []) if r.get("is_active") is not False]
    if not rows:
        return {"status": INVALID_CODE}
    referrer_id, code_id = str(rows[0]["user_id"]), rows[0]["id"]
    if referrer_id == str(user_id):
        logger.warning("REF-ATTR-01: self-referral blocked for %s", user_id)
        return {"status": SELF_REFERRAL}

    existing = (
        admin_client.table("referral_relationships")
        .select("id,referrer_id")
        .eq("referred_id", str(user_id))
        .execute()
    )
    if getattr(existing, "data", None):
        return {"status": ALREADY_ATTRIBUTED}

    try:
        admin_client.table("referral_relationships").insert(
            {
                "referrer_id": referrer_id,
                "referred_id": str(user_id),
                "referral_code_id": code_id,
                "status": "pending",
            }
        ).execute()
    except Exception as exc:  # uq_referral_relationships_referred: a concurrent claim won
        if "23505" in str(exc) or "duplicate key" in str(exc).lower():
            return {"status": ALREADY_ATTRIBUTED}
        raise
    admin_client.table("profiles").update({"referred_by_user_id": referrer_id}).eq(
        "id", str(user_id)
    ).execute()
    logger.info("REF-ATTR-01: %s referred %s with %s", referrer_id, user_id, ref_code)
    return {"status": ATTRIBUTED, "referrer_id": referrer_id}
