"""LEGAL-01: record and report acceptance of the published legal documents.

WHAT THIS REPLACES
==================
Nothing. There was no server-side record of legal acceptance at all. The signup
page rendered a checkbox, refused to submit without it, and threw the result
away: ``agreedToPolicies`` was React state that gated a local form submit and was
never sent to Supabase Auth, to this API, or to any table. With 182 existing
accounts, the platform could not say which terms any of them had agreed to.

WHY THE SERVER DOES NOT TRUST THE CLIENT'S VERSION STRING
=========================================================
``POST /api/legal/accept`` takes a version and a document list, and validates
BOTH against this deployment's own constants (``core/legal_documents.py``) before
writing anything. A server that recorded whatever version the client sent would
reproduce the original weakness in a more durable form: a row asserting the user
agreed to text that was never published.

A stale browser bundle therefore gets a 409 naming the current version, not a
recorded acceptance of an old one.

WHY WRITES GO THROUGH HERE AND NOT STRAIGHT FROM THE BROWSER
============================================================
``052_legal_acceptance.sql`` grants the browser SELECT on its own rows and
nothing else. An acceptance the accepting party can write unaided is worth
nothing as evidence. This router writes over the service role, which bypasses
RLS, after validating. The table is additionally append-only by trigger, so even
a bug in this file cannot rewrite history.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.legal_documents import (
    LEGAL_EFFECTIVE_DATE,
    LEGAL_VERSION,
    REQUIRED_DOCUMENTS,
    LegalAcceptanceRefused,
    is_acceptance_current,
    validate_documents,
    validate_version,
)
from backend_app.core.rate_limit import limiter

logger = logging.getLogger("LegalRouter")

router = APIRouter()

TABLE = "legal_acceptances"

#: Truncation bound for the stored user agent. A header is attacker-controlled
#: and unbounded; the column is TEXT, so without a limit a single request could
#: store an arbitrarily large value.
_MAX_USER_AGENT = 512


class AcceptanceRequest(BaseModel):
    """What the client asserts it displayed and the user accepted."""

    version: str = Field(
        ...,
        description="Version identity of the documents shown, e.g. '2026-10-23'. "
                    "Validated against the server's own published version.",
        max_length=32,
    )
    documents: List[str] = Field(
        ...,
        description="Document ids the acceptance covered. Must be exactly the "
                    "required set; partial acceptance is refused, not recorded.",
        max_length=16,
    )
    context: str = Field(
        "signup",
        description="'signup' for the initial acceptance, 're_acceptance' when "
                    "prompted after a version change.",
        max_length=32,
    )


def _client_ip(request: Request) -> Optional[str]:
    """The caller's address as the load balancer reports it.

    Takes the FIRST entry of X-Forwarded-For, which is the client as the edge saw
    it. The header is client-spendable, so this is evidentiary context rather
    than proof of origin, and it is nullable precisely so a stripped or absent
    value is recorded as unknown instead of as something invented.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:64]
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return str(host)[:64] if host else None


@router.get("/current")
async def get_current_documents() -> Dict[str, Any]:
    """The version this deployment publishes, and what must be accepted.

    Unauthenticated: a visitor deciding whether to sign up is entitled to know
    which version they are being asked to agree to, and the version is not a
    secret. The frontend also reads it to detect that its own bundle is stale.
    """
    return {
        "version": LEGAL_VERSION,
        "effective_date": LEGAL_EFFECTIVE_DATE,
        "required_documents": sorted(REQUIRED_DOCUMENTS),
    }


@router.get("/acceptance")
async def get_my_acceptance(
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
) -> Dict[str, Any]:
    """This user's acceptance history, and whether it covers the current version.

    ``needs_acceptance`` is what the frontend acts on. It is True for every
    account created before this mechanism existed, which is correct: none of them
    has a record, so none can be shown to have accepted anything.
    """
    user_id = str(user.get("id") or "")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHENTICATED", "message": "No user id on the token."},
        )

    if not supabase:
        # Deliberately NOT "needs_acceptance: false". A read that did not happen
        # must not be reported as "already accepted" -- that would silently skip
        # the prompt for everyone during a database outage.
        raise HTTPException(
            status_code=503,
            detail={
                "error": "LEGAL_ACCEPTANCE_UNAVAILABLE",
                "message": "Could not read acceptance records. Try again shortly.",
            },
        )

    try:
        response = (
            supabase.table(TABLE)
            .select("document_version, documents, accepted_at, context")
            .eq("user_id", user_id)
            .order("accepted_at", desc=True)
            .execute()
        )
        rows = response.data or []
    except Exception as exc:  # noqa: BLE001
        logger.error(f"legal acceptance read failed for {user_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "LEGAL_ACCEPTANCE_UNAVAILABLE",
                "message": "Could not read acceptance records. Try again shortly.",
            },
        ) from exc

    latest = rows[0]["document_version"] if rows else None
    return {
        "current_version": LEGAL_VERSION,
        "accepted_version": latest,
        "needs_acceptance": not is_acceptance_current(latest),
        "history": rows,
    }


@router.post("/accept")
@limiter.limit("10/minute")
async def accept_documents(
    request: Request,
    body: AcceptanceRequest,
    user: dict = Depends(get_current_user),
    supabase: Any = Depends(get_request_supabase),
) -> Dict[str, Any]:
    """Record that this user accepted the current documents.

    Idempotent. ``uq_legal_acceptances_user_version`` makes one acceptance per
    user per version, so a double click or a retried request is reported as
    success rather than as a conflict the user has to interpret.
    """
    user_id = str(user.get("id") or "")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"error": "UNAUTHENTICATED", "message": "No user id on the token."},
        )

    try:
        version = validate_version(body.version)
        documents = validate_documents(body.documents)
    except LegalAcceptanceRefused as refusal:
        # 409 rather than 400: the request is well-formed, but it refers to a
        # version this deployment does not publish -- almost always a browser on
        # a stale bundle. The response names the current version so the client
        # can tell the user to reload rather than retrying the same thing.
        status = 409 if refusal.code == "LEGAL_VERSION_UNKNOWN" else 400
        raise HTTPException(
            status_code=status,
            detail={
                "error": refusal.code,
                "message": refusal.message,
                "current_version": LEGAL_VERSION,
            },
        ) from refusal

    context = body.context if body.context in {"signup", "re_acceptance"} else "signup"

    if not supabase:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "LEGAL_ACCEPTANCE_NOT_RECORDED",
                "message": "Could not record your acceptance. Nothing was saved. "
                           "Try again shortly.",
            },
        )

    user_agent = request.headers.get("user-agent")
    row = {
        "user_id": user_id,
        "document_version": version,
        "documents": sorted(documents),
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "ip_address": _client_ip(request),
        "user_agent": user_agent[:_MAX_USER_AGENT] if user_agent else None,
        "context": context,
    }

    try:
        # on_conflict names the unique index, so a repeat accept is a no-op
        # rather than a 23505 this handler would have to interpret.
        supabase.table(TABLE).upsert(
            row, on_conflict="user_id,document_version", ignore_duplicates=True
        ).execute()
    except Exception as exc:  # noqa: BLE001
        # NEVER report an acceptance as recorded when the write failed. The whole
        # value of this record is that it is true.
        logger.error(f"legal acceptance write failed for {user_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "LEGAL_ACCEPTANCE_NOT_RECORDED",
                "message": "Could not record your acceptance. Nothing was saved. "
                           "Try again shortly.",
            },
        ) from exc

    logger.info(
        f"legal acceptance recorded: user={user_id} version={version} context={context}"
    )
    return {
        "recorded": True,
        "version": version,
        "documents": sorted(documents),
        "accepted_at": row["accepted_at"],
    }
