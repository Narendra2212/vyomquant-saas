"""
routers/library.py — Strategy Library / Marketplace API

Implements the complete V1 Strategy Library backend as specified in
STRATEGY_LIBRARY_DESIGN.md.

Endpoints:
  GET    /api/library              — Browse catalogue (paginated, filterable)
  GET    /api/library/me           — My published strategies
  GET    /api/library/{id}         — Strategy detail
  POST   /api/library              — Publish strategy
  DELETE /api/library/{id}         — Unpublish strategy (soft delete)
  POST   /api/library/{id}/clone   — Clone into user's workspace
  POST   /api/library/{id}/rate    — Submit / update rating
  PATCH  /api/admin/library/{id}   — Admin moderation
  GET    /api/admin/library/pending — Admin pending queue

Security:
  - All write endpoints require JWT via get_current_user()
  - Admin endpoints require get_admin_user() (app_metadata.role == "admin")
  - Ownership validated at API layer before DB writes
  - Service-role client used only for cross-user DAG reads (clone flow)
  - Input validated via Pydantic models
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
# The two analytics reads report a rating average. `Decimal` is the exact type the
# `NUMERIC(3,2)` column stores, so the weighted mean is computed in it rather than in binary
# floating point. No money value passes through it — every amount in this module is an integer
# number of Minor_Units (Requirement 10.3).
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional
from uuid import UUID

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError, validator

from backend_app.core.auth_middleware import decode_token_local
from backend_app.core.dependencies import (bearer_scheme, get_admin_user,
                                           get_current_user)
from backend_app.core.subscription_dependencies import (
    check_feature_optional,
    check_marketplace_publish_quota,
    require_marketplace_access,
    require_marketplace_publish,
)
from backend_app.core.rate_limit import limiter
from backend_app.core.rate_limit_keys import caller_or_address, source_address
from backend_app.backend.marketplace import eligibility_gate as _eligibility_gate
from backend_app.backend.marketplace import submission_service as _submission_service
from backend_app.backend.marketplace import pricing_evaluator as _pricing_evaluator
from backend_app.backend.marketplace import aliases as _aliases
from backend_app.backend.marketplace import listing_projection as _listing_projection
from backend_app.backend.marketplace import media as _media
from backend_app.backend.marketplace import money as _money
from backend_app.backend.marketplace import entitlement_resolver as _entitlement_resolver
from backend_app.backend.marketplace import checkout_service as _checkout_service
from backend_app.backend.marketplace import library_entries as _library_entries
from backend_app.backend.marketplace import settlement_service as _settlement_service
from backend_app.backend.marketplace import (
    subscription_reinstatement as _subscription_reinstatement,
)
from backend_app.backend.marketplace.errors import (
    MARKETPLACE_CLONING_DISABLED,
    MARKETPLACE_ELIGIBILITY_FAILED,
    MARKETPLACE_ELIGIBILITY_UNEVALUABLE,
    MARKETPLACE_PRICE_EVIDENCE_MISSING,
    MARKETPLACE_PRICE_OUT_OF_RANGE,
    MARKETPLACE_READ_FAILED,
    MARKETPLACE_REJECTION_REASON_REQUIRED,
    MARKETPLACE_SUBMISSION_NOT_FOUND,
    MARKETPLACE_USE_SUBMISSION_ACTIONS,
    NOT_FOUND,
    MarketplaceError,
)
from backend_app.core.audit_trail import (
    StrategyAuditAction,
    get_strategy_audit_logger,
)
from backend_app.backend.marketplace.submission_state import (
    MODERATION_STATUS_FOR_STATE,
    SubmissionState,
    is_open,
    normalise_submission_state,
)
# The one enum -> `library_subscriptions.status` mapping. `subscriber_analytics` compares against
# it rather than against the literal "active", so the spelling lives in one place
# (Requirement 30.2) and a future rename cannot leave this handler silently matching nothing.
from backend_app.backend.marketplace.subscription_state import (
    STATUS_TEXT_FOR_STATE,
    SubscriptionState,
)
from supabase import create_client

# The concrete PostgREST error the supabase client raises for a failed table operation. It is
# imported here so the clone_count increment and the verified-clone marker can narrow their
# `except` to exactly this class (Requirement 30.5 — no broad `except` on a correctness path).
# postgrest is a hard dependency of supabase, so this import is always satisfiable; the guard is
# defensive against a future packaging change and never widens the catch to bare Exception.
try:
    from postgrest.exceptions import APIError as _PostgrestAPIError
except Exception:  # pragma: no cover - postgrest is a supabase dependency; present in practice
    class _PostgrestAPIError(Exception):
        """Fallback so the narrowed excepts still name a concrete, non-bare exception type."""

from backend_app.api_ws.ws_manager import manager as ws_manager
from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Route registration order (design §5 — route shadowing)
#
# FastAPI matches routes in registration order, so a parameterised route such as
# GET /{library_id} or GET /creator/{creator_id} declared before a literal one
# (GET /favorites, GET /creator/analytics, …) permanently shadows it. Every route
# whose path has no path parameter is therefore registered on `literal_router`,
# and at the bottom of this module those routes are spliced in front of the
# parameterised routes on `router`. Paths, handlers and dependencies are
# unchanged — only the order in which FastAPI sees them.
# ─────────────────────────────────────────────────────────────────────────────

literal_router = APIRouter()
_CACHED_CATEGORIES = None

# ─────────────────────────────────────────────────────────────────────────────
# Service-role Supabase client (cross-user reads, metric updates)
# Bypasses RLS — used only for tightly scoped operations documented below.
# ─────────────────────────────────────────────────────────────────────────────

_service_client = None


def _get_service_client():
    """Returns a cached Supabase service-role client."""
    global _service_client
    if _service_client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            if os.getenv("DEV_MODE", "false").lower() == "true":
                return None
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
            )
        try:
            _service_client = create_client(url, key)
        except Exception as e:
            if os.getenv("DEV_MODE", "false").lower() == "true":
                return None
            raise
    return _service_client


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas (derived from STRATEGY_LIBRARY_DESIGN.md §8)
# ─────────────────────────────────────────────────────────────────────────────

class PublishStrategyRequest(BaseModel):
    strategy_id: UUID
    description: Optional[str] = Field(None, max_length=2000)
    category: str = Field(
        ...,
        description="One of: mean_reversion, trend_following, market_making, arbitrage, momentum, ml_hybrid, other",
    )
    difficulty: str = Field(
        ..., description="One of: beginner, intermediate, advanced, pro"
    )
    tags: List[str] = Field(default_factory=list)
    price: Optional[float] = Field(None, ge=0, description="Monthly subscription price in USD. Null for free strategies.")
    currency: str = Field("USD", description="Currency for pricing: USD or INR")
    subscription_tier: str = Field("free", description="Subscription tier: free, pro, elite")
    cover_image: Optional[str] = Field(None, max_length=500, description="URL to strategy cover image")

    @validator("category")
    def validate_category(cls, v):
        valid = {
            "mean_reversion", "trend_following", "market_making",
            "arbitrage", "momentum", "ml_hybrid", "other",
        }
        if v not in valid:
            raise ValueError(f"Invalid category. Must be one of: {sorted(valid)}")
        return v

    @validator("difficulty")
    def validate_difficulty(cls, v):
        valid = {"beginner", "intermediate", "advanced", "pro"}
        if v not in valid:
            raise ValueError(f"Invalid difficulty. Must be one of: {sorted(valid)}")
        return v

    @validator("tags", each_item=True)
    def validate_tag(cls, v):
        v = v.lower().strip()
        if len(v) > 30 or not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"Invalid tag: '{v}'. Use alphanumeric, hyphens, underscores only."
            )
        return v

    @validator("currency")
    def validate_currency(cls, v):
        valid = {"USD", "INR"}
        if v not in valid:
            raise ValueError(f"Invalid currency. Must be one of: {sorted(valid)}")
        return v

    @validator("subscription_tier")
    def validate_subscription_tier(cls, v):
        valid = {"free", "starter", "pro", "enterprise"}
        if v not in valid:
            raise ValueError(f"Invalid subscription tier. Must be one of: {sorted(valid)}")
        return v


class SubmitRatingRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    review_text: Optional[str] = Field(None, max_length=500)


class AdminModerateRequest(BaseModel):
    moderation_status: str = Field(
        ..., description="One of: pending, approved, rejected, featured"
    )
    is_featured: Optional[bool] = None
    moderation_notes: Optional[str] = Field(None, max_length=1000)

    @validator("moderation_status")
    def validate_moderation_status(cls, v):
        valid = {"pending", "approved", "rejected", "featured"}
        if v not in valid:
            raise ValueError(f"Invalid moderation_status. Must be one of: {sorted(valid)}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_uuid(value: str, field_name: str = "id") -> str:
    """Validate UUID string to prevent SQL injection via path params."""
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID format for {field_name}.",
        )


def _build_service_client():
    """Returns a service-role client; raises 500 if env is not configured."""
    try:
        return _get_service_client()
    except RuntimeError as exc:
        logger.error(f"Service client unavailable: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error. Please contact support.",
        )


def _current_submission_state_for_listing(svc, source_strategy_id):
    """The current ``SubmissionState`` of the listing's Submission, or ``None``.

    Task 14.5 narrows ``admin_moderate_strategy`` so it cannot move the effective
    lifecycle behind the Submission state machine's back. That gate needs the listing's
    "current submission state", which is the state of the ``marketplace_submissions`` row
    for the listing's ``source_strategy_id`` (the column is ``submission_state``). When
    more than one Submission exists for the strategy (a rejected attempt plus a fresh
    one), the meaningful one for lifecycle protection is the *open* Submission — the row
    occupying the one-open-Submission-per-strategy slot; failing that, the most recently
    created row.

    Returns ``None`` — meaning "no lifecycle to protect, take the legacy path" — when:

    * the listing carries no ``source_strategy_id``;
    * no ``marketplace_submissions`` row exists for it (a legacy pre-marketplace listing);
    * the submissions table cannot be read here.

    A read failure resolves to ``None`` deliberately: this is a *narrowing* guard on an
    already-authorised admin route, not the enforcement. The database trigger
    ``trg_submission_projects_moderation_status`` and ``trg_submission_transition_guard``
    remain the arbiters of the lifecycle, so degrading to the legacy path on an unreadable
    submissions table never lets a caller move a Submission illegally — it only declines to
    add the extra refusal when the state that would justify it is unknown.
    """
    if not source_strategy_id:
        return None
    try:
        resp = (
            svc.table("marketplace_submissions")
            .select("submission_state, created_at")
            .eq("source_strategy_id", str(source_strategy_id))
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 - narrowing guard, never the enforcement
        logger.warning(f"admin_moderate submission-state lookup skipped: {exc}")
        return None

    rows = resp.data or []
    if not rows:
        return None

    open_rows = [
        r for r in rows if is_open(r.get("submission_state"))
    ]
    if open_rows:
        return normalise_submission_state(open_rows[0].get("submission_state"))

    most_recent = max(rows, key=lambda r: str(r.get("created_at") or ""))
    return normalise_submission_state(most_recent.get("submission_state"))


def _sample_equity_curve(equity_curve: Optional[list], max_points: int = 500) -> Optional[list]:
    """Downsample equity curve to max_points for storage efficiency."""
    if not equity_curve:
        return None
    if len(equity_curve) <= max_points:
        return equity_curve
    step = len(equity_curve) // max_points
    return equity_curve[::step][:max_points]


def _recompute_avg_rating(library_id: str) -> None:
    """
    Recompute and persist avg_rating and rating_count for a library strategy.
    Runs synchronously (called in background via fire-and-forget pattern).
    Failures are logged but do not surface to the caller.
    """
    try:
        svc = _get_service_client()
        resp = (
            svc.table("library_ratings")
            .select("rating")
            .eq("library_id", library_id)
            .not_.is_("rating", "null")
            .execute()
        )
        rows = resp.data or []
        rating_count = len(rows)
        avg_rating = (
            round(sum(r["rating"] for r in rows) / rating_count, 2)
            if rating_count > 0
            else None
        )
        svc.table("library_strategies").update(
            {"avg_rating": avg_rating, "rating_count": rating_count}
        ).eq("id", library_id).execute()
    except Exception as exc:
        logger.error(f"Failed to recompute avg_rating for {library_id}: {exc}")


def _get_author_alias(user_id: str) -> str:
    """One reviewer's display alias, for the single caller that legitimately reads one row.

    THE ONLY REMAINING CALLER IS ``get_strategy_reviews`` (task 16.2)
    ---------------------------------------------------------------
    Every Listing read — the catalogue, search, detail, creator, comparison, recommendation and
    favourites paths — now resolves aliases through :func:`_page_alias_map`, one batched
    ``profiles`` read per response (Requirement 27.1). This helper survives for
    ``get_strategy_reviews``, which is **not** a Listing read and **not** a catalogue path: it
    aliases the *reviewers* on ``library_ratings`` rows, not the creator on a
    ``library_strategies`` row, so ``listing_projection.project_listing`` has nothing to say
    about it and Requirement 27.1's fixed-round-trip rule (which is about "a catalogue page of
    at most 50 Listings") does not bind it.

    Do not call this from a Listing read. A per-row alias fetch on a catalogue path is the N+1
    that made a 50-Listing page cost 51 round trips; ``_page_alias_map`` is the read those paths
    use.

    ``email`` is no longer selected. The old projection was ``"display_name, email"`` with a
    ``resp.data.get("email", "Anonymous")`` fallback, which put a reviewer's email address into
    a public response body whenever their ``display_name`` was null. A column that is never read
    cannot be disclosed by a later edit, so the read asks for the alias and nothing else
    (Requirement 6.5's rule applied to the one identity this helper resolves).
    """
    try:
        svc = _get_service_client()
        resp = (
            svc.table("profiles")
            .select("display_name")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if resp.data:
            return resp.data.get("display_name") or "Anonymous"
    except Exception:
        pass
    return "Anonymous"


def _page_alias_map(rows, svc):
    """The batched creator-alias map for a page of ``library_strategies`` rows.

    One round trip for the whole page (Requirement 27.1), replacing the per-row
    ``_get_author_alias`` call that made a 50-Listing page 51 round trips. ``resolve_aliases``
    raises ``MARKETPLACE_READ_FAILED`` itself when the read does not complete, so a page of
    fabricated creators can never be rendered (Requirements 1.5, 28.5). An id it cannot
    resolve is simply absent from the map; ``project_listing`` then refuses that row, which the
    per-row projection at the call site turns into ``MARKETPLACE_READ_FAILED``.

    The ``project_listing`` call itself stays at each call site (never hidden in a helper) so
    the allow-list projection is visible to the AST guard in ``tests/test_listing_projection.py``.
    """
    author_ids = [
        r.get("author_id")
        for r in (rows or [])
        if isinstance(r, dict) and r.get("author_id")
    ]
    return _aliases.resolve_aliases(author_ids, svc)


def check_deployment_permission(user_id: str, library_id: str) -> dict:
    """Whether this caller may deploy this Listing's strategy, or a refusal to guess.

    Returns:
        dict: {
            "has_permission": bool,
            "granted_via": str,  # "ownership" or "subscription"
            "subscription_id": Optional[str],
            "expires_at": Optional[str]
        }

    A DENIAL IS AN ANSWER, SO IT MAY NOT BE FABRICATED (Requirements 1.5, 1.7, 28.3, 30.5)
    --------------------------------------------------------------------------------------
    Both reads used to sit in ``except Exception: logger.warning(...)`` blocks that fell
    through to ``{"has_permission": False, "reason": "No valid subscription or ownership"}``.
    A caller therefore could not tell "you are not entitled" — a fact — from "we could not
    find out" — an outage. That is the same substitution a zero-filled earnings body makes:
    the response states something the Persistence_Layer never said. Both ``except`` blocks now
    RE-RAISE ``MARKETPLACE_READ_FAILED``; the driver's message (which carries the sqlstate and
    a column name) is logged for an operator and never put in the body (Requirement 22.9).

    A read that COMPLETED and found nothing is still a denial, and still answers one: an absent
    Listing, a Listing owned by somebody else and a caller with no live Subscription are all
    answered exactly as before, so this function has not become an existence oracle and
    Requirement 21.4's indistinguishability is untouched. Only the did-not-complete case moved.

    WHY ``.single()`` IS GONE
    ------------------------
    ``.single()`` raises when it matches zero rows, so "no such Listing" and "the read failed"
    arrived at the same ``except`` and could not be told apart — which is *why* the swallow was
    there. ``.limit(1)`` reports zero rows as an empty ``data`` list and reserves the exception
    for a read that genuinely did not complete, so each outcome can now have its own answer.

    A ``data`` of ``None`` is the third case: a response object the handler cannot read. It is
    refused rather than treated as "no rows", because reading it as absence would report an
    owner as a stranger.
    """
    svc = _get_service_client()
    if not svc:
        # No handle, so neither read can be attempted. There is nothing to report, and a
        # `has_permission: False` here would be a denial nothing was checked for.
        logger.error("deployment permission check: no service client available")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    user_uid = _safe_uuid(user_id, "user_id")
    lib_uid = _safe_uuid(library_id, "library_id")

    # Check 1: User owns the strategy (author)
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("author_id")
            .eq("id", lib_uid)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("deployment permission check (ownership) read failed: %s", exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    listing_rows = lib_resp.data if lib_resp is not None else None
    if listing_rows is None:
        logger.error("deployment permission check (ownership) read unreadable")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if listing_rows and listing_rows[0].get("author_id") == user_uid:
        return {
            "has_permission": True,
            "granted_via": "ownership",
            "subscription_id": None,
            "expires_at": None
        }

    # Check 2: User has active subscription
    try:
        sub_resp = (
            svc.table("library_subscriptions")
            .select("id, status, expires_at")
            .eq("library_id", lib_uid)
            .eq("user_id", user_uid)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error("deployment permission check (subscription) read failed: %s", exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    subscription_rows = sub_resp.data if sub_resp is not None else None
    if subscription_rows is None:
        logger.error("deployment permission check (subscription) read unreadable")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if subscription_rows:
        subscription = subscription_rows[0]
        # Check if subscription is not expired.
        #
        # A NULL expiry falls through to the "no valid subscription" answer below. It is NOT
        # a perpetual entitlement — the `else: # No expiry date means perpetual subscription`
        # branch that used to sit here granted permanent access to a monthly Listing, and
        # task 18.2 removes it now that both halves of its removal hold:
        #
        #   * `checkout_service._pending_payload` OMITS `expires_at` rather than writing
        #     NULL into it (task 18.1), and asserts it stayed omitted — so the write that
        #     used to seed a null-expiry row no longer exists.
        #   * migration 008's `chk_ls_active_has_period` — `status <> 'active' OR
        #     (period_start IS NOT NULL AND period_expiry IS NOT NULL)` — makes an ACTIVE
        #     row without a period *unrepresentable*, and `trg_lib_subs_period_mirror`
        #     mirrors the period onto `expires_at`. So a NULL expiry on an ACTIVE row is not
        #     merely unwritten by this codebase, it is refused by the database.
        #
        # A NULL expiry on a non-ACTIVE row is not entitling either, and this query filters
        # `status = 'active'` anyway. Requirement 11.7 is the rule being followed: entitlement
        # is decided by the period, irrespective of the stored status value.
        #
        # `entitlement_resolver.resolve` — the gate POST /{library_id}/deploy actually admits
        # on since task 17.3 — already reads a null `period_expiry` as non-entitling (property
        # P-11, `tests/test_marketplace_deployment_subscriber_safe.py`
        # ::test_a_null_period_expiry_is_not_a_perpetual_subscription). This function is now
        # reachable only from the advisory `GET /{library_id}/deploy/check` read, which grants
        # nothing, and it agrees with the resolver on this point rather than contradicting it.
        expires_at = subscription.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(str(expires_at).replace('Z', '+00:00'))
            except (TypeError, ValueError) as exc:
                # The row was read, but the value it carries cannot be interpreted, so whether
                # the period is live is UNKNOWN. Reporting "no permission" would state a fact
                # this read does not support; reporting permission would grant one. Neither is
                # answered — the read is refused (Requirements 1.7, 28.5).
                logger.error(
                    "deployment permission check: unreadable subscription expiry: %s", exc
                )
                raise MarketplaceError(MARKETPLACE_READ_FAILED)
            if expiry > datetime.now(timezone.utc):
                return {
                    "has_permission": True,
                    "granted_via": "subscription",
                    "subscription_id": subscription["id"],
                    "expires_at": expires_at
                }

    return {"has_permission": False, "reason": "No valid subscription or ownership"}


# ─────────────────────────────────────────────────────────────────────────────
# DELETED: grant_deployment_permission (task 19.3)
#
# This router carried a second `grant_deployment_permission` alongside
# `backend_app/backend/marketplace/settlement_service.grant_deployment_permission`. Requirement
# 30.2 admits one, and the authoritative one is the service's — for a reason that is a privilege
# defect and not a style preference:
#
#   * the router's copy wrote `"expires_at": None` unconditionally and took no expiry parameter,
#     so it *could not* express a Subscription_Period. `check_deployment_permission` used to read
#     a null expiry as perpetual access, so every grant it wrote was a permanent entitlement to a
#     monthly Listing.
#   * the service's copy takes `expires_at` as a REQUIRED keyword and refuses `None` outright, so
#     the defect is unrepresentable there rather than merely absent. It runs inside the settlement
#     transaction, beside the Settlement_Record and the period write, so a grant cannot exist
#     without the payment that bought it.
#
# The only caller in the codebase was `renew_subscription`, which this same task rewrote as
# checkout-only — the grant was one of the three statements Requirement 11.16 ordered deleted.
# Nothing else in `backend_app/**` or `tests/**` referenced this function
# (`tests/test_settlement_service.py` imports the service's, not this one), so it is removed
# rather than repointed.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/featured — Featured strategies
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_cards_with_user_context(cards, svc, user_id):
    """Fold the authenticated caller's ``user_has_cloned``/``user_rating`` onto projected cards.

    Public-view enrichment on top of the one public projection, exactly as
    ``get_library_detail`` does it: the projected card is the public view, and the caller's own
    context is added only when a row for that caller is read (Requirement 28.5 — an omitted
    field and a null field differ, so a read that does not complete leaves the fields off rather
    than zeroing them). Each card carries ``listing_id`` (never ``id``), so the enrichment maps
    are keyed by the Listing id and looked up by ``card["listing_id"]``.
    """
    if not user_id or not cards:
        return
    library_ids = [c["listing_id"] for c in cards if c.get("listing_id")]
    if not library_ids:
        return
    try:
        clones_resp = (
            svc.table("library_strategies")
            .select("id, source_library_id")
            .eq("author_id", user_id)
            .in_("source_library_id", library_ids)
            .execute()
        )
        cloned_map = {r["source_library_id"]: r["id"] for r in (clones_resp.data or [])}

        rating_resp = (
            svc.table("library_ratings")
            .select("library_id, rating")
            .eq("user_id", user_id)
            .in_("library_id", library_ids)
            .execute()
        )
        rating_map = {r["library_id"]: r["rating"] for r in (rating_resp.data or [])}
    except Exception as exc:
        # Enrichment is additive public context; a failure to read it omits the fields rather
        # than fabricating them, and does not fail the (already-projected) catalogue read.
        logger.warning(f"Failed to enrich cards with user context: {exc}")
        return

    for card in cards:
        listing_id = card.get("listing_id")
        card["user_has_cloned"] = listing_id in cloned_map
        card["user_rating"] = rating_map.get(listing_id)
        if card["user_has_cloned"]:
            card["cloned_strategy_id"] = cloned_map[listing_id]


def _user_id_from_credentials(credentials):
    """The caller's user id from a bearer token, or ``None`` when unauthenticated/invalid."""
    if not credentials:
        return None
    try:
        payload = decode_token_local(credentials.credentials)
        return payload.get("sub")
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# THE TWO STACKED LIMITS ON EVERY CATALOGUE, SEARCH AND DETAIL ROUTE (Req 6.7)
#
# Requirement 6.7 asks for two windows at once: at most 120 requests per 60 seconds per
# AUTHENTICATED CALLER, and at most 60 per 60 seconds per SOURCE ADDRESS. One decorator cannot
# say that, because a limit has exactly one key function. Two stacked decorators can, and
# `slowapi` supports it: `Limiter.limit` files a limit under `f"{func.__module__}.{func.
# __name__}"`, `functools.wraps` keeps both names stable through the first wrapper, so the
# second decorator appends to the SAME registry entry — and `__evaluate_limits` walks that
# entry calling each limit's OWN `key_func`. Both buckets are therefore checked on every
# request and the stricter one decides. `tests/test_task_33_2_rate_limit_keys.py` proves this
# against the installed version rather than trusting the reading.
#
# An unauthenticated caller keys BOTH limits by address (`caller_or_address` falls back to the
# same string `source_address` returns), so the effective cap is the 60/60s address window
# Requirement 6.7 names for exactly that caller. The 120 and the 60 still occupy different
# storage keys, because `limits` folds the amount and the window into the key it hits.
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/featured")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("60/60second", key_func=source_address)
async def get_featured_strategies(
    request: Request,
    limit: int = Query(3, ge=1, le=10),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Returns featured strategies (is_featured=True), through the one public projection."""
    svc = _build_service_client()

    # Check cache (10 minute TTL for featured). The cache holds already-PROJECTED items, so a
    # cache hit and a cache miss return the identical projected structure — the caller cannot
    # tell which path served the page. Only the caller-specific enrichment is applied on top.
    cache_key = f"library:featured:{limit}"
    try:
        cached_data = await redis_manager.get(cache_key)
        if cached_data:
            response_data = json.loads(cached_data)
            user_id = _user_id_from_credentials(credentials)
            _enrich_cards_with_user_context(
                response_data.get("items", []), svc, user_id
            )
            return response_data
    except Exception as e:
        logger.warning(f"Redis cache read failed for featured: {e}")

    if not svc:
        return {"items": []}

    try:
        resp = (
            svc.table("library_strategies")
            .select(_listing_projection.LISTING_SELECT)
            .eq("is_active", True)
            .eq("is_featured", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is the structured MARKETPLACE_READ_FAILED, never a
        # zero-filled 200 (Requirements 1.5, 1.7; design.md "Never-swallow rule").
        logger.error(f"Featured strategies DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data or []

    # Batched alias read (one round trip for the page) + per-row projection through the one
    # public serialiser. A row that cannot be projected fails the read (see the detail path).
    alias_by_author_id = _page_alias_map(rows, svc)
    items = []
    for row in rows:
        author_id = row.get("author_id") if isinstance(row, dict) else None
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            items.append(_listing_projection.project_listing(row, None, creator_alias))
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Featured projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    response_data = {"items": items}

    # Save the PROJECTED items to cache, so a subsequent cache hit returns the same shape.
    try:
        await redis_manager.set(cache_key, json.dumps({"items": items}), ex=600)
    except Exception as e:
        logger.warning(f"Redis cache write failed for featured: {e}")

    # Enrich with the caller's own context if authenticated (after cache save).
    _enrich_cards_with_user_context(
        items, svc, _user_id_from_credentials(credentials)
    )

    return response_data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/trending — Trending strategies
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/trending")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("60/60second", key_func=source_address)
async def get_trending_strategies(
    request: Request,
    limit: int = Query(10, ge=1, le=20),
):
    """Returns trending strategies (sorted by clone_count + rating)."""
    svc = _build_service_client()
    
    if not svc:
        return {"items": [], "total": 0}
    
    try:
        resp = (
            svc.table("library_strategies")
            .select(_listing_projection.LISTING_SELECT)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("clone_count", desc=True)
            .order("avg_rating", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never a zero-filled 200
        # (Requirements 1.5, 1.7).
        logger.error(f"Trending strategies DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data or []
    alias_by_author_id = _page_alias_map(rows, svc)
    items = []
    for row in rows:
        author_id = row.get("author_id") if isinstance(row, dict) else None
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            items.append(_listing_projection.project_listing(row, None, creator_alias))
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Trending projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/categories — Available categories
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/categories")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("60/60second", key_func=source_address)
async def get_categories(request: Request):
    """Returns available strategy categories with counts.

    AN EMPTY CATEGORY LIST IS A CLAIM, SO IT MAY NOT BE FABRICATED (Requirements 1.5, 1.7)
    -------------------------------------------------------------------------------------
    ``except Exception: return {"categories": []}`` answered a failed read with an empty 200,
    which a catalogue page renders as "this marketplace has nothing in it". That is a figure
    the Persistence_Layer never produced — each entry carries a ``count``, so the empty list is
    a statement that every count is zero. A read that did not complete now raises
    ``MARKETPLACE_READ_FAILED``; the driver's message is logged and never put in the body
    (Requirement 22.9).

    The Redis miss above is a different thing and is still swallowed deliberately: a cache that
    cannot be read costs a round trip, and the read under it is still performed, so no answer
    depends on it.
    """
    global _CACHED_CATEGORIES
    if _CACHED_CATEGORIES is not None:
        return _CACHED_CATEGORIES

    cache_key = "library:categories"
    try:
        cached = await redis_manager.get(cache_key)
        if cached:
            _CACHED_CATEGORIES = json.loads(cached)
            return _CACHED_CATEGORIES
    except Exception as e:
        logger.warning(f"Redis cache read failed for categories: {e}")

    svc = _build_service_client()

    if not svc:
        logger.error("categories: no service client available")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    try:
        resp = (
            svc.table("library_strategies")
            .select("category")
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .execute()
        )
    except Exception as exc:
        logger.error(f"Categories DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    items = resp.data if resp is not None else None
    if items is None:
        # A response object carrying no readable ``data``. Treating it as "no categories" would
        # cache an empty catalogue for ten minutes (Requirements 1.5, 28.5).
        logger.error("categories read unreadable")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    category_counts = {}
    for item in items:
        cat = item.get("category")
        if cat:
            category_counts[cat] = category_counts.get(cat, 0) + 1
    
    categories = [
        {"name": cat, "count": count}
        for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
    ]
    
    result = {"categories": categories}
    _CACHED_CATEGORIES = result
    try:
        await redis_manager.set(cache_key, json.dumps(result), ex=600)
    except Exception as e:
        logger.warning(f"Redis cache write failed for categories: {e}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/creator/{creator_id} — Creator profile
# ─────────────────────────────────────────────────────────────────────────────

# `/creator/{creator_id}` is not a row of its own in `design.md`'s table, but it is a catalogue
# read by every other measure — it serves `project_listing`ed rows to the whole internet, and
# `tests/test_marketplace_error_surface.py` enumerates it beside `browse_library` and
# `get_library_detail` — so Requirement 6.7's "every catalogue, search and detail endpoint"
# reaches it and it carries the same pair.
@router.get("/creator/{creator_id}")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("60/60second", key_func=source_address)
async def get_creator_profile(request: Request, creator_id: str):
    """Returns creator profile with published strategies and stats."""
    creator_uid = _safe_uuid(creator_id, "creator_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(status_code=503, detail="Service unavailable")
    
    # Fetch creator's published strategies through the explicit allow-list column list. This
    # endpoint serves one creator's catalogue to the whole internet — the `.eq("author_id",
    # creator_uid)` filter comes from the PATH, not the authenticated caller, so it is a public
    # read that must project (design.md § "One implementation, shared by every path").
    try:
        strat_resp = (
            svc.table("library_strategies")
            .select(_listing_projection.LISTING_SELECT)
            .eq("author_id", creator_uid)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("published_at", desc=True)
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never a fabricated 500 or a
        # zero-filled body (Requirements 1.5, 1.7).
        logger.error(f"Creator profile DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = strat_resp.data or []

    # Batched alias read (one round trip). Every row shares the one creator, so the map has at
    # most one entry; the projection still receives its `creator_alias` from that map, never a
    # per-row profile fetch.
    alias_by_author_id = _page_alias_map(rows, svc)
    strategies = []
    for row in rows:
        author_id = row.get("author_id") if isinstance(row, dict) else None
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            strategies.append(
                _listing_projection.project_listing(row, None, creator_alias)
            )
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Creator profile projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # Creator stats from the projected public fields only. `subscriber_count` and `avg_rating`
    # are public (Requirement 6.2); a card omits `avg_rating` when the rating infrastructure
    # holds no value (Requirement 6.9), so those cards are excluded from the average rather than
    # counted as zero.
    total_subscribers = sum(s.get("subscriber_count") or 0 for s in strategies)
    avg_rating = 0.0
    if strategies:
        ratings = [s["avg_rating"] for s in strategies if s.get("avg_rating") is not None]
        if ratings:
            avg_rating = round(sum(ratings) / len(ratings), 2)

    # The creator's own display alias — read from the projected cards, so there is one
    # representation of the creator and it is a display alias, never author_id. Every card
    # shares the one creator, so the first card's `creator_alias` is that alias; a creator with
    # no projectable listings has none to show.
    alias = strategies[0].get("creator_alias") if strategies else None

    return {
        "creator_id": creator_uid,
        "author_alias": alias,
        "total_strategies": len(strategies),
        "total_subscribers": total_subscribers,
        "avg_rating": avg_rating,
        "strategies": strategies,
    }


#: The key pattern every cached `browse_library` page is stored under. Named once so the
#: writer (`browse_library`) and the invalidator below cannot drift apart.
_BROWSE_CACHE_KEY_PATTERN = "library:browse:*"


async def _invalidate_browse_cache(context: str) -> None:
    """Drop every cached `library:browse:*` page.

    `browse_library` serves a cache hit for up to 300 seconds without consulting the
    Persistence_Layer, so any write that changes which Listings the public catalogue must
    contain has to drop those pages in the same request. Requirement 4 Criterion 7 — a Listing
    whose Submission is not PUBLISHED is excluded from *every* public catalogue response — is
    not satisfied by a handler that flips `is_active` and leaves the catalogue serving the
    Listing from cache until the TTL lapses.

    `SCAN` is preferred over `KEYS` (the latter is O(keyspace) and blocks the server), and it is
    consumed with `async for`: `redis.asyncio`'s `scan_iter` returns an *async* iterator, so a
    synchronous `for` over it raises `TypeError` and deletes nothing. A client that has no
    `scan_iter` (the DEV_MODE in-memory double) falls back to the manager's `keys` proxy.

    A cache that cannot be reached is logged and swallowed: the authoritative write has already
    committed, and failing the request would report a successful state change as a failure.
    """
    try:
        client = await redis_manager.get_client()
        scan_iter = getattr(client, "scan_iter", None) if client is not None else None
        if scan_iter is not None:
            async for key in scan_iter(match=_BROWSE_CACHE_KEY_PATTERN):
                await client.delete(key)
            return
        for key in await redis_manager.keys(_BROWSE_CACHE_KEY_PATTERN):
            await redis_manager.delete(key)
    except Exception as exc:
        logger.warning(f"Redis browse-cache invalidation failed ({context}): {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library — Browse catalogue
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("60/60second", key_func=source_address)
async def browse_library(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    sort: str = Query("clones", description="clones|rating|sharpe|return|newest|featured"),
    category: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    has_ml: Optional[bool] = Query(None),
    min_sharpe: Optional[float] = Query(None),
    min_return: Optional[float] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
    q: Optional[str] = Query(None, description="Search by name or description"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """
    Browse the public strategy catalogue. Auth is optional; if provided,
    enriches each card with user_has_cloned and user_rating.
    """
    svc = _build_service_client()
    
    # Check cache - Use shorter TTL for browse queries (5 minutes) to keep data fresh
    cache_key = f"library:browse:{page}:{limit}:{sort}:{category}:{difficulty}:{has_ml}:{min_sharpe}:{min_return}:{tags}:{q}"
    try:
        cached_data = await redis_manager.get(cache_key)
        if cached_data:
            response_data = json.loads(cached_data)
            # The cache holds already-PROJECTED items, so a cache hit returns the identical
            # projected structure a cache miss builds — only the caller's own context is folded
            # on top (Requirement 6.7's public projection is one view for every path).
            _enrich_cards_with_user_context(
                response_data.get("items", []),
                svc,
                _user_id_from_credentials(credentials),
            )
            return response_data
    except Exception as e:
        logger.warning(f"Redis cache read failed: {e}")

    # --- Build query ---
    if not svc:
        return {"items": [], "total": 0, "page": page, "limit": limit}

    query = (
        svc.table("library_strategies")
        .select(_listing_projection.LISTING_SELECT)
        .eq("is_active", True)
        .in_("moderation_status", ["approved", "featured"])
    )

    if category:
        query = query.eq("category", category)
    if difficulty:
        query = query.eq("difficulty", difficulty)
    if has_ml is not None:
        query = query.eq("has_ml_model", has_ml)
    if min_sharpe is not None:
        query = query.gte("backtest_sharpe_ratio", min_sharpe)
    if min_return is not None:
        query = query.gte("backtest_total_return_pct", min_return)
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            query = query.contains("tags", tag_list)
    if q:
        # Simple ilike search on name and description
        query = query.or_(f"name.ilike.%{q}%,description.ilike.%{q}%")

    # --- Sort ---
    sort_map = {
        "clones": ("clone_count", False),
        "rating": ("avg_rating", False),
        "sharpe": ("backtest_sharpe_ratio", False),
        "return": ("backtest_total_return_pct", False),
        "newest": ("published_at", False),
        "featured": ("is_featured", False),
    }
    sort_col, sort_asc = sort_map.get(sort, ("clone_count", False))
    query = query.order(sort_col, desc=not sort_asc)

    try:
        all_result = query.execute()
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never a zero-filled 200 on
        # the cache-miss DB path (Requirements 1.5, 1.7; design.md "Never-swallow rule").
        logger.error(f"Library browse DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    all_rows = all_result.data or []
    count = len(all_rows)
    offset = (page - 1) * limit
    paginated = all_rows[offset : offset + limit]

    # Batched alias read for the page (one round trip), then per-row projection through the one
    # public serialiser. A row that cannot be projected fails the read (mirrors the detail path).
    alias_by_author_id = _page_alias_map(paginated, svc)
    items = []
    for row in paginated:
        author_id = row.get("author_id") if isinstance(row, dict) else None
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            items.append(_listing_projection.project_listing(row, None, creator_alias))
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Browse projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    response_data = {
        "items": items,
        "total": count,
        "page": page,
        "limit": limit,
        "pages": (count + limit - 1) // limit if count > 0 else 1,
    }

    # Save the PROJECTED, caller-agnostic items to cache so a cache hit returns the same shape.
    try:
        await redis_manager.set(cache_key, json.dumps(response_data), ex=300)
    except Exception as e:
        logger.warning(f"Redis cache write failed: {e}")

    # Enrich with the caller's own context if authenticated (after cache save, so the cached
    # payload stays caller-agnostic).
    _enrich_cards_with_user_context(
        items, svc, _user_id_from_credentials(credentials)
    )

    return response_data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/me — My published strategies (task 16.2)
#
# WHY THIS HANDLER DOES NOT CALL `project_listing`, AND WHY IT STILL BUILDS ITS ROWS
#   `design.md` lists "`my_library`'s non-owner view" among the paths that must project.
#   `my_library` has no non-owner view: its single `library_strategies` chain is filtered
#   `.eq("author_id", user_id)` where `user_id` derives from the `Depends(get_current_user)`
#   identity, so every row it can return belongs to the caller. Requirement 6 governs what a
#   Listing discloses to a NON-owner (6.1 "return a Listing to a non-owner", 6.4's denied
#   columns), and `moderation_status` and `is_active` are exactly the fields an owner needs on
#   their own management list and a non-owner may not have. Running the public projection here
#   would strip the owner's own moderation state from the owner — a functional regression
#   dressed up as a security fix — so the public allow-list is deliberately not applied.
#
#   What DOES apply is the structural half of Requirement 6.1: the response is built, never
#   serialised from the driver's rows. `_MY_LIBRARY_FIELDS` is a frozen tuple and the entry is
#   assembled from it, so a column a future migration adds to `library_strategies` reaches this
#   response only if someone adds it here — the same default-closed property `project_listing`
#   gives the public paths, without borrowing the public field set.
#
#   No alias read at all, batched or otherwise (Requirement 27.1): every row shares the one
#   author and that author is the caller, so there is no creator to resolve. Round trips: 1.
# ─────────────────────────────────────────────────────────────────────────────

#: The explicit column list `GET /api/library/me` requests. Unchanged from the pre-task-16
#: query, so the filter, ordering and response contract are identical.
_MY_LIBRARY_SELECT = (
    "id, name, moderation_status, clone_count, avg_rating, rating_count, "
    "is_active, published_at"
)

#: The owner-view fields the response carries, in the order it carries them. Frozen, so the
#: response's key set is decided here and not by whatever columns the row happens to hold.
_MY_LIBRARY_FIELDS = (
    "id",
    "name",
    "moderation_status",
    "clone_count",
    "avg_rating",
    "rating_count",
    "is_active",
    "published_at",
)


def _owned_library_entry(row):
    """One `GET /api/library/me` entry, built field by field from the frozen list.

    The owner's own view, so it keeps `moderation_status` and `is_active`; it is still built
    rather than copied, so an unnamed column cannot ride along (see the block comment above).
    """
    return {field: row.get(field) for field in _MY_LIBRARY_FIELDS}


@literal_router.get("/me")
@limiter.limit("120/60second", key_func=caller_or_address)
async def my_library(request: Request, user: dict = Depends(get_current_user)):
    """Returns all library entries authored by the authenticated user.

    A read that did not complete answers the stable ``MARKETPLACE_READ_FAILED``, not the
    ``HTTPException(500, "Failed to fetch your library entries.")`` that used to sit here
    (Requirement 1.5). The old shape was a 5xx — so no figure was fabricated — but it carried
    no machine-readable code and the legacy envelope, so a client could only branch on a
    sentence, and the body carried the internal ``path`` member Requirement 22.9 excludes.

    The success body is unchanged: ``{"items": [...], "total": n}`` over ``_MY_LIBRARY_FIELDS``.
    """
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    try:
        resp = (
            svc.table("library_strategies")
            .select(_MY_LIBRARY_SELECT)
            .eq("author_id", user_id)
            .order("published_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"my_library DB error for user {user_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data if resp is not None else None
    if rows is None:
        # A response object carrying no readable ``data`` did not complete in a way this handler
        # can read. Reporting it as an empty library would tell an owner their strategies are
        # gone (Requirements 1.5, 1.7, 28.5).
        logger.error("my_library read unreadable for user %s", user_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    items = [
        _owned_library_entry(row)
        for row in rows
        if isinstance(row, dict)
    ]
    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/my-strategies — the Strategies_Page's combined owned-and-
# subscribed list (task 17.4; Requirements 12.1, 12.2, 12.3, 12.4, 12.6, 27.2)
#
# THREE ROUND TRIPS, INDEPENDENT OF THE ENTRY COUNT (Requirement 27.2)
#   1. `strategies`           the caller's own, active rows
#   2. `library_subscriptions` the caller's Subscriptions, with the Listing and its
#                              submission state arriving as embedded PostgREST resources in
#                              the SAME request — `library_strategies!inner(…)`
#   3. `paper_sessions`        the caller's RUNNING sessions, counted per strategy in Python
#
# No read is issued per entry. In particular `entitlement_resolver.resolve` is NOT called
# here: it is a per-listing decision (one embedded read, plus a version read when it would
# otherwise entitle), so calling it once per entry would make the round-trip count grow with
# the entry count — exactly what Requirement 27.2 forbids. `library_entries` re-states the
# resolver's decision ORDERING over the rows round trip 2 already carries, in the resolver's
# own `EntitlementReason` vocabulary and through its own wire-code mapping, so the list and
# the admission point cannot disagree in meaning (property P-16). The resolver still runs at
# the admission point; `allowed_actions` is an affordance list, never an authorisation —
# Requirement 12.7's server-side 403 is each restricted route's own.
#
# WHY EVERY DERIVATION LIVES IN `marketplace/library_entries.py`
#   `ownership`, `subscription` and `allowed_actions` are decided by a pure module that
#   imports no FastAPI and performs no I/O, so Requirement 12.4's "a SUBSCRIBED entry never
#   offers these thirteen actions" is a property a test can assert exhaustively without a
#   database. This handler owns the three queries and the error surface, nothing else.
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/my-strategies")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("120/60second")
async def my_strategies(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """The caller's owned strategies and every Listing they hold a Subscription to, in one list.

    Each entry carries a server-derived ``ownership`` label (``OWNED`` or ``SUBSCRIBED``), so
    the page never infers it client-side (Requirement 12.2); a ``SUBSCRIBED`` entry additionally
    carries ``subscription: {state, period_expiry, renewal_state}`` (Requirement 12.6) and a
    server-computed ``allowed_actions`` restricted to the eight Requirement 12.3 permits and
    never naming any of the thirteen Requirement 12.4 forbids.

    A read that does not complete is never answered with a zero-filled or partial 200
    (Requirements 1.5, 1.7): rounds 1 and 2 raise ``MARKETPLACE_READ_FAILED``. Round 3 is the
    one exception, and deliberately so — the running-session count is a decoration, not an
    entitlement input, so when it fails the figure is OMITTED from every entry and reported
    unavailable rather than substituted with a zero the server did not measure
    (Requirement 28.5).
    """
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    now = datetime.now(timezone.utc)

    # ── Round trip 1: the caller's own, non-archived strategies ──────────
    # `archived_at IS NULL` is 005a's active-rows predicate and its partial index
    # (idx_strategies_archived_at). The column list is explicit — no `select("*")`, and no
    # Protected_Logic column, even though these rows are the caller's own.
    try:
        owned_resp = (
            svc.table("strategies")
            .select(_library_entries.OWNED_STRATEGY_SELECT)
            .eq("user_id", user_id)
            .is_(_library_entries.ARCHIVED_AT_COLUMN, "null")
            .order("updated_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error("my_strategies owned-read failed for user %s: %s", user_id, exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # ── Round trip 2: the caller's Subscriptions + embedded Listing ──────
    # The embed is filtered to this caller server-side; a Subscription belonging to another
    # user is simply not in the result set, so it is indistinguishable from absent and can
    # never reach this response (Requirement 21.1).
    try:
        subs_resp = (
            svc.table("library_subscriptions")
            .select(_library_entries.SUBSCRIPTION_SELECT)
            .eq("user_id", user_id)
            .order("period_expiry", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error("my_strategies subscription-read failed for user %s: %s", user_id, exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # ── Round trip 3: the caller's RUNNING Paper_Sessions ────────────────
    session_rows = None
    try:
        sessions_resp = (
            svc.table("paper_sessions")
            .select(_library_entries.RUNNING_PAPER_SESSION_SELECT)
            .eq("user_id", user_id)
            .eq("session_state", _library_entries.RUNNING_SESSION_STATE)
            .execute()
        )
        session_rows = getattr(sessions_resp, "data", None) or []
    except Exception as exc:
        # Reported as unavailable, never as zero (Requirement 28.5). `session_rows` stays
        # None, which makes `build_my_strategies_entries` omit the key from every entry.
        logger.error(
            "my_strategies running-session read failed for user %s; the running-session "
            "count is reported unavailable rather than as zero: %s",
            user_id,
            exc,
        )

    entries = _library_entries.build_my_strategies_entries(
        caller_id=user_id,
        owned_rows=getattr(owned_resp, "data", None) or [],
        subscription_rows=getattr(subs_resp, "data", None) or [],
        session_rows=session_rows,
        now=now,
    )

    owned_total = sum(
        1
        for entry in entries
        if entry["ownership"] == _library_entries.OWNERSHIP_OWNED
    )
    return {
        "items": entries,
        "total": len(entries),
        "owned_total": owned_total,
        "subscribed_total": len(entries) - owned_total,
        # Requirement 28.5: the client is told the figure is unavailable rather than being
        # handed a zero it cannot tell apart from a real count.
        "running_paper_sessions_available": session_rows is not None,
        "as_of": now.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/{library_id} — Strategy detail (task 16.1, root-cause fix 4)
#
# THREE ROUND TRIPS FOR THE LISTING ITSELF (design.md → "Round trips per screen")
#   1. `library_strategies` through `listing_projection.LISTING_SELECT` — the explicit
#      allow-list column list that replaces `select("*")`
#   2. `marketplace_backtest_evidence` — the IMMUTABLE per-condition copy, reached in ONE
#      request through an embedded `marketplace_submissions!inner` filter, so the condition
#      summaries cost one read rather than one plus a submission lookup
#   3. the batched creator-alias read (`aliases.resolve_aliases`) — never the per-row
#      `_get_author_alias` N+1
#   plus the two `library_ratings` enrichment reads that already existed.
#
# WHY A `.single()` MISS IS NOT A READ FAILURE
#   PostgREST answers a `.single()` that matched no row with `PGRST116` rather than an empty
#   body, and supabase-py raises that as an `APIError`. Treating every exception as
#   MARKETPLACE_READ_FAILED would answer an unknown Listing with a 503, so the miss is
#   separated from the failure by `_is_single_row_miss` — one predicate, used by both reads
#   below, and the ONLY thing that produces the shared NOT_FOUND shape.
# ─────────────────────────────────────────────────────────────────────────────

#: PostgREST's code for "a `.single()` read did not match exactly one row". It is a MISS, not a
#: Persistence_Layer failure: the read completed and the answer is "no such row". Requirement
#: 6.10 needs the two told apart, because a Listing that is absent and a Listing that is not
#: PUBLISHED must both answer NOT_FOUND while a failed read must answer MARKETPLACE_READ_FAILED.
_PGRST_SINGLE_ROW_MISS = "PGRST116"


def _is_single_row_miss(exc: Exception) -> bool:
    """True when ``exc`` is PostgREST's "zero rows for a `.single()`" answer.

    The code is read from the driver's structured ``code`` attribute first — that is the field
    postgrest-py populates — and the string form is consulted only as a fallback, so a client
    version that carries the code somewhere else still classifies correctly rather than turning
    every unknown Listing into a 503. Nothing else about the exception is inspected, and the
    exception's text never reaches a response body (Requirement 22.9).
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.strip().upper() == _PGRST_SINGLE_ROW_MISS:
        return True
    return _PGRST_SINGLE_ROW_MISS in str(exc)


#: The per-condition projection the detail read asks of the immutable evidence copy: the
#: condition ordinal and the seven outcome metrics `listing_projection.CONDITION_OUTCOME_METRICS`
#: names, and NOTHING else. No `dataset`, `start_date`, `end_date`, `dag_hash`,
#: `dataset_checksum`, `engine_version`, `executed_bar_count`, `version_id` or
#: `source_backtest_id` is selected — a column that is never read cannot be projected by a
#: later edit (Requirement 6.3). The embedded `marketplace_submissions!inner(...)` is the FK
#: join that makes "the evidence of this Listing's PUBLISHED Submission" one round trip; its
#: two columns are the filter, never response content — `project_listing` assigns only the
#: metrics above into `condition_summaries`.
_DETAIL_EVIDENCE_SELECT = (
    "condition_index, total_return_pct, sharpe_ratio, sortino_ratio, "
    "max_drawdown_pct, win_rate_pct, profit_factor, total_trades, "
    "marketplace_submissions!inner(listing_id, submission_state)"
)


def _published_condition_evidence(lib_id: str, svc):
    """The Listing's per-condition Backtest_Evidence rows, in condition order.

    Read from ``marketplace_backtest_evidence`` — the immutable copy taken at submission time
    (Requirement 3.9) — and never from ``strategy_backtests``, so the figures shown cannot be
    changed by a later re-run of the source backtest, and no ``blueprint``, ``dag_hash`` or
    ``dataset_checksum`` is even fetched.

    Scoped to the Submission that is ``PUBLISHED``, so an older REJECTED or SUPERSEDED
    Submission for the same Listing contributes no condition to a public response.

    A read that does not complete raises ``MARKETPLACE_READ_FAILED`` rather than returning an
    empty list: an empty list is a truthful "this Submission has no evidence rows" that a
    caller cannot tell from "the read broke", and substituting one for the other is what
    Requirements 1.5 and 28.5 forbid.
    """
    try:
        resp = (
            svc.table("marketplace_backtest_evidence")
            .select(_DETAIL_EVIDENCE_SELECT)
            .eq("marketplace_submissions.listing_id", lib_id)
            .eq(
                "marketplace_submissions.submission_state",
                SubmissionState.PUBLISHED.value,
            )
            .order("condition_index")
            .execute()
        )
    except Exception as exc:
        logger.error(f"get_library_detail evidence read error for {lib_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = getattr(resp, "data", None)
    if rows is None:
        # Not "no evidence" — PostgREST answers a list for that. The read produced no rows
        # object at all, so it did not complete in a way this handler can interpret.
        logger.error(f"get_library_detail evidence read unreadable for {lib_id}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)
    return rows


@router.get("/{library_id}")
# `design.md`'s table gives the detail route 120/60s per caller and 60/60s per address, which
# DISAGREES with the 100/minute that has sat here since BE-CRITICAL-007. The table is followed —
# both of its limits are declared — and the 100/minute is left in place rather than deleted,
# because removing a limit is the one thing this task may not do. It is subsumed: 60 per 60s is
# stricter than 100 per minute on the same key, so it never decides a request. It is retained,
# not relied on.
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("60/60second", key_func=source_address)
@limiter.limit("100/minute")  # BE-CRITICAL-007 FIX: Add rate limiting
async def get_library_detail(
    library_id: str,
    request: Request,
    user: dict = Depends(get_current_user),  # BE-CRITICAL-007 FIX: Require authentication
):
    """Returns the public view of one Listing: allow-listed metadata, outcome figures, the
    per-condition summaries from the immutable evidence copy, and public rating enrichment.

    Nothing here serialises the row. `listing_projection.project_listing` builds a fresh dict
    by explicit assignment per allow-listed field and closes with a runtime
    `assert set(out) <= PUBLIC_LISTING_FIELDS`, so a column a future migration adds to
    `library_strategies` reaches no caller by default (Requirements 6.1, 6.2, 6.4).

    A Listing whose Submission is not PUBLISHED answers a non-owner with the SAME status and
    SAME body as an identifier that names no row at all — the shared NOT_FOUND shape, disclosing
    neither existence, owner nor Submission_State (Requirement 6.10). The Listing's own owner
    still reads their own Listing in any state, through an owner-scoped read that can serve
    nobody else's row.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    caller_id = _safe_uuid(user["id"], "user_id") if user and user.get("id") else None
    svc = _build_service_client()

    # Fetch the library entry through the explicit allow-list column list (task 16.1). The
    # public filter is the PUBLISHED-visible set per the 007 projection: is_active True and a
    # moderation_status the state machine only ever projects for a PUBLISHED Submission. A
    # Listing whose Submission is not PUBLISHED — and a truly absent id — both make the
    # filtered `.single()` miss, and both take the ONE not-found path below, so a non-owner
    # cannot tell them apart (Requirement 6.10; the P-50 indistinguishability of task 14.9).
    row = None
    try:
        resp = (
            svc.table("library_strategies")
            .select(_listing_projection.LISTING_SELECT)
            .eq("id", lib_id)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .single()
            .execute()
        )
        row = getattr(resp, "data", None)
    except Exception as exc:
        # A read that did not complete is never a zero-filled or fabricated success body
        # (Requirements 1.5, 1.7); it is the structured MARKETPLACE_READ_FAILED, not a stack
        # trace. A `.single()` that matched nothing is not such a failure — it is the miss the
        # not-found path below answers, identically for both of its causes.
        if not _is_single_row_miss(exc):
            logger.error(f"get_library_detail DB error for {lib_id}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    served_publicly = row is not None

    if row is None and caller_id:
        # The owner's own Listing, in whatever state it is. Owner-scoped in the query itself:
        # `author_id` is constrained to the authenticated caller, so this read can never serve
        # another creator's row and adds no way to probe for one — a non-owner falls straight
        # through to the same not-found answer.
        try:
            own_resp = (
                svc.table("library_strategies")
                .select(_listing_projection.LISTING_SELECT)
                .eq("id", lib_id)
                .eq("author_id", caller_id)
                .single()
                .execute()
            )
            row = getattr(own_resp, "data", None)
        except Exception as exc:
            if not _is_single_row_miss(exc):
                logger.error(f"get_library_detail owner read error for {lib_id}: {exc}")
                raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if not row:
        # ONE not-found path, one shape, for every cause: no such id, a Listing whose
        # Submission is not PUBLISHED, and another creator's unpublished Listing all answer
        # with the shared NOT_FOUND code and its generic message. No field of the
        # Listing_Projection is returned and the body names no listing, owner or state
        # (Requirements 6.10, 21.4).
        raise MarketplaceError(NOT_FOUND)

    # Round trip 2 — the per-condition summaries, read from the immutable evidence copy. Only
    # a publicly served Listing has a PUBLISHED Submission to read evidence for; for the
    # owner's own non-published Listing the evidence is NOT read, and `None` (rather than an
    # empty list) tells the projection so: `condition_count` then comes from the row's stored
    # column instead of being restated as a 0 that no read measured (Requirement 28.5).
    evidence_summaries = (
        _published_condition_evidence(lib_id, svc) if served_publicly else None
    )

    # Resolve the creator's display alias server-side, batched through the one alias read
    # (Requirement 6.5). It is a display alias only — never author_id, email or any
    # authentication identity. `resolve_aliases` omits an id it cannot resolve rather than
    # fabricating "Anonymous"/email (Requirement 28.5), so a missing alias leaves the row
    # unprojectable and `project_listing` refuses it below.
    author_id = row.get("author_id") if isinstance(row, dict) else None
    creator_alias = None
    if author_id:
        alias_by_author_id = _aliases.resolve_aliases([author_id], svc)
        creator_alias = alias_by_author_id.get(author_id)

    # Recent ratings (5 most recent with a non-null rating). This is public outcome data, not
    # Protected_Logic, so it may accompany the projected Listing. It is gathered into a local
    # here and folded into the projection's output at the single return below.
    try:
        ratings_resp = (
            svc.table("library_ratings")
            .select("rating, review_text, created_at")
            .eq("library_id", lib_id)
            .not_.is_("rating", "null")
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        recent_ratings = ratings_resp.data or []
    except Exception as exc:
        logger.warning(f"Failed to fetch recent ratings for {lib_id}: {exc}")
        recent_ratings = []

    # User context enrichment. An omitted field and a null field mean different things
    # (Requirement 28.5): when the caller's rating row cannot be read the fields are OMITTED
    # rather than set to a value the infrastructure did not record — `user_rating = None` would
    # claim the caller rated this Listing and left the score blank. They are folded into the
    # response below only when the read succeeded and returned a row.
    #
    # The catch is narrowed to the driver's own `APIError` (design.md → "Never-swallow rule":
    # `get_library_detail`'s `except Exception: pass` becomes a narrowed catch). Anything else
    # — a programming error in this block, say — propagates instead of being absorbed.
    user_context = {}
    if caller_id:
        try:
            ur = (
                svc.table("library_ratings")
                .select("is_verified_clone, rating")
                .eq("library_id", lib_id)
                .eq("user_id", caller_id)
                .single()
                .execute()
            )
            if ur.data:
                user_context["user_has_cloned"] = ur.data.get("is_verified_clone", False)
                user_context["user_rating"] = ur.data.get("rating")
        except _PostgrestAPIError as exc:
            if _is_single_row_miss(exc):
                # The caller has no rating row for this Listing. Not a failure: there is
                # nothing to report, so both fields stay absent.
                logger.debug(
                    f"no caller rating row for {lib_id}; omitting user_rating"
                )
            else:
                # The read did not complete. Omit both fields rather than substituting None,
                # and log at error — a silent enrichment failure is what task 16.1 removes.
                logger.error(
                    f"Failed to read caller rating for {lib_id}: {exc}; "
                    "omitting user_rating and user_has_cloned"
                )

    # Build the public view by explicit allow-list and fold in the allow-listed public
    # enrichment in one dict. `project_listing` starts from an empty dict and assigns one
    # allow-listed field at a time, so no DENIED_LISTING_COLUMNS value (source_strategy_id,
    # moderation_notes, moderated_by/at, evaluation_score, deployment_requirements,
    # version_history, equity_curve_snapshot, the risk_* columns, has_ml_model, author_id) can
    # reach a non-owner (Requirements 6.1–6.5). The per-condition summaries come from the
    # immutable evidence rows read above — `label` plus outcome metrics only, no dataset, no
    # window, no checksum, no graph fingerprint (Requirement 6.3) — and `None` there means
    # "not read", leaving `condition_count` to the row's stored column.
    # The returned value is the projection's dict augmented only with public fields — never the
    # raw row (the AST guard in tests/test_listing_projection.py).
    try:
        detail = {
            **_listing_projection.project_listing(
                row, evidence_summaries, creator_alias
            ),
            "recent_ratings": recent_ratings,
            **user_context,
        }
    except (ValueError, _money.MoneyError) as exc:
        # A blank/owner-equal alias or a malformed price_minor makes this one row
        # unserialisable. That is a read that could not produce an honest body, so it becomes
        # MARKETPLACE_READ_FAILED rather than a 500 stack trace or a fabricated figure.
        logger.error(f"get_library_detail projection error for {lib_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return detail


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library — Publish a strategy
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.post("", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
async def publish_strategy(
    request: Request,
    payload: PublishStrategyRequest,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_publish),
    _quota=Depends(check_marketplace_publish_quota),
):
    """
    Publish a strategy to the Library. The strategy must:
    1. Belong to the authenticated user
    2. Have a stored backtest_result
    3. Not already be actively published
    """
    user_id = _safe_uuid(user["id"], "user_id")
    strategy_id = _safe_uuid(str(payload.strategy_id), "strategy_id")

    # 0. The ONE write-time gate on `library_strategies.cover_image` (Requirement 22.7). An
    #    allow-list of parsed origins, applied HERE - before any database access, as
    #    Requirement 22.2 requires - so an unvetted reference never reaches the insert payload
    #    below. Nothing server-side ever dereferences the value; see
    #    `marketplace/media.py`'s docstring for why that makes a write-time check sufficient.
    #    Refusal is the catalogue's 422 MARKETPLACE_COVER_REFERENCE_REJECTED.
    cover_image = _media.validate_cover_reference(payload.cover_image)

    svc = _build_service_client()

    # 1. Verify strategy exists and belongs to user
    try:
        strat_resp = (
            svc.table("strategies")
            .select("id, name, user_id, symbol, timeframe, exchange_id, "
                    "buy_logic, sell_logic, risk, indicators, ml_model_path, backtest_result")
            .eq("id", strategy_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"publish_strategy strategy lookup error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up strategy.",
        )

    if not strat_resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found.",
        )

    strategy = strat_resp.data

    # Ownership check
    if strategy["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your strategy.",
        )

    # Backtest result check
    if not strategy.get("backtest_result"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run a backtest before publishing. Strategy has no stored backtest result.",
        )

    # 2. Prevent duplicate publication
    try:
        dup_resp = (
            svc.table("library_strategies")
            .select("id")
            .eq("source_strategy_id", strategy_id)
            .eq("is_active", True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"publish_strategy duplicate check error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check for existing publication.",
        )

    if dup_resp.data:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy is already published. Unpublish the existing entry first.",
        )

    # 3. Extract performance metrics from backtest_result
    br = strategy.get("backtest_result") or {}
    if isinstance(br, str):
        try:
            br = json.loads(br)
        except Exception:
            br = {}

    # 4. Determine node_count and has_ml_model from buy/sell logic
    buy_logic = strategy.get("buy_logic") or {}
    if isinstance(buy_logic, str):
        try:
            buy_logic = json.loads(buy_logic)
        except Exception:
            buy_logic = {}

    nodes = buy_logic.get("nodes", [])
    node_count = len(nodes)
    has_ml_model = bool(strategy.get("ml_model_path"))

    # Validate DAG has at least one action node
    action_nodes = [n for n in nodes if n.get("type") == "action"]
    if not action_nodes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Strategy DAG has no action nodes. Add a buy/sell action before publishing.",
        )

    # 5. Equity curve snapshot (max 500 points)
    equity_curve_raw = br.get("equity_curve") or br.get("equity_curve_data")
    equity_curve_snapshot = _sample_equity_curve(equity_curve_raw)

    # 6. Build library_strategies row
    now_ts = datetime.now(timezone.utc).isoformat()

    # The heuristic evaluation score, the free/paid score gate and the
    # 29.99 / 49.99 / 99.99 / 199.99 suggested-price points were removed here
    # (task 15.1, Requirement 8.11). Pricing is no longer decided on the publish
    # path: the Price_Range is computed by ``pricing_evaluator`` and enforced by the
    # POST /submissions/{id}/price endpoint, which is the single enforcement point
    # (Requirements 8.8–8.10). ``evaluation_score`` remains a column but is written
    # NULL here rather than as a number that no longer decides anything, and it stays
    # in ``listing_projection.DENIED_LISTING_COLUMNS`` so it never reaches a non-owner.

    insert_payload = {
        "author_id": user_id,
        "source_strategy_id": strategy_id,
        "name": strategy["name"],
        "description": payload.description,
        "category": payload.category,
        "difficulty": payload.difficulty,
        "tags": [t for t in payload.tags if t],
        "symbol": strategy.get("symbol", ""),
        "timeframe": strategy.get("timeframe", ""),
        "exchange_id": strategy.get("exchange_id", ""),
        "node_count": node_count,
        "has_ml_model": has_ml_model,
        # Backtest metrics
        "backtest_total_return_pct": br.get("total_return_pct") or br.get("total_return"),
        "backtest_sharpe_ratio": br.get("sharpe_ratio"),
        "backtest_max_drawdown_pct": br.get("max_drawdown_pct") or br.get("max_drawdown"),
        "backtest_win_rate_pct": br.get("win_rate_pct") or br.get("win_rate"),
        "backtest_profit_factor": br.get("profit_factor"),
        "backtest_total_trades": br.get("total_trades"),
        "backtest_start_date": br.get("start_date"),
        "backtest_end_date": br.get("end_date"),
        "backtest_initial_capital": br.get("initial_capital"),
        "equity_curve_snapshot": equity_curve_snapshot,
        # Risk params
        "risk_stop_loss_pct": (strategy.get("risk") or {}).get("stop_loss_pct"),
        "risk_take_profit_pct": (strategy.get("risk") or {}).get("take_profit_pct"),
        "risk_max_position_size": (strategy.get("risk") or {}).get("max_position_size"),
        "risk_max_drawdown_pct": (strategy.get("risk") or {}).get("max_drawdown_pct"),
        # Marketplace pricing — no suggested price is computed on publish; the
        # authoritative price is set through POST /submissions/{id}/price (task 15.1).
        "price": payload.price,
        "currency": payload.currency,
        "subscription_tier": payload.subscription_tier,
        # The value validated at step 0, not `payload.cover_image` — reading the payload again
        # here is how a gate gets bypassed by a later edit (Requirement 22.7).
        "cover_image": cover_image,
        "evaluation_score": None,
        "verification_status": "unverified",
        "subscriber_count": 0,
        # Defaults
        "clone_count": 0,
        "rating_count": 0,
        "moderation_status": "pending",
        "is_active": True,
        "is_featured": False,
        "published_at": now_ts,
        "updated_at": now_ts,
    }

    try:
        insert_resp = svc.table("library_strategies").insert(insert_payload).execute()
    except Exception as exc:
        logger.error(f"publish_strategy insert error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to publish strategy. Please try again.",
        )

    if not insert_resp.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Publish operation returned no data.",
        )

    # 7. Invalidate cache
    try:
        r_client = await redis_manager.get_client()
        if r_client:
            for key in r_client.scan_iter("library:browse:*"):
                await r_client.delete(key)
    except Exception as e:
        logger.warning(f"Redis cache invalidation failed: {e}")

    library_id = insert_resp.data[0]["id"]
    logger.info(f"Strategy published: library_id={library_id} by user={user_id}")

    # Broadcast marketplace event
    try:
        await ws_manager.broadcast_marketplace("strategy_published", {
            "library_id": library_id,
            "author_id": user_id,
            "name": payload.name,
            "category": payload.category,
            "published_at": now_ts,
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast marketplace event: {e}")

    return {
        "library_id": library_id,
        "moderation_status": "pending",
        "message": "Strategy submitted for review. It will appear publicly once approved.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/library/{library_id} — Unpublish (soft delete)
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{library_id}", status_code=status.HTTP_200_OK)
async def unpublish_strategy(
    library_id: str,
    user: dict = Depends(get_current_user),
):
    """Soft-unpublishes a strategy. Existing clones are unaffected."""
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # Verify ownership
    try:
        resp = (
            svc.table("library_strategies")
            .select("id, author_id, is_active")
            .eq("id", lib_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"unpublish_strategy lookup error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up library entry.",
        )

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library entry not found.",
        )

    entry = resp.data
    if entry["author_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the author of this strategy.",
        )

    if not entry["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy is already unpublished.",
        )

    # Soft delete
    try:
        svc.table("library_strategies").update(
            {"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", lib_id).execute()
    except Exception as exc:
        logger.error(f"unpublish_strategy update error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unpublish strategy.",
        )

    # Drop the cached catalogue pages. Unpublishing removes the Listing from the browsable pool
    # (Requirement 4 Criterion 7), and `browse_library` answers from a 300-second cache without
    # reading the Persistence_Layer — so without this the unpublished Listing keeps being served
    # to the public catalogue for up to five minutes after the row says it is gone. `publish`,
    # `clone` and `rate` already invalidate here; `unpublish` was the one state change that did
    # not, which is the defect `tests/test_marketplace_pipeline.py::test_13` reproduces.
    await _invalidate_browse_cache(f"unpublish library_id={lib_id}")

    logger.info(f"Strategy unpublished: library_id={lib_id} by user={user_id}")
    return {"message": "Strategy unpublished. Existing clones are unaffected."}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/clone — Clone strategy
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{library_id}/clone", status_code=status.HTTP_201_CREATED)
@limiter.limit("20/60second", key_func=caller_or_address)
@limiter.limit("20/minute")
async def clone_strategy(
    request: Request,
    library_id: str,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_access),
):
    """
    Clones a published library strategy into the authenticated user's
    personal strategy workspace.

    Cloning copies the author's Protected_Logic (``buy_logic``, ``sell_logic``, ``risk``,
    ``indicators``, ``ml_model_path``) into a row the caller owns, so it runs only with the
    owner's explicit consent AND an entitling Subscription. The gate order below is the order
    Requirements 7.2/7.3/7.4 fix, and each refusal happens before anything is written.

    Transaction flow:
    1. Validate library entry is approved and active
    2. Block self-clone (409)
    3. Owner consent: ``source_cloning_enabled`` false → 403 MARKETPLACE_CLONING_DISABLED,
       no ``strategies`` row, ``clone_count`` unchanged, one MARKETPLACE_ACCESS_REFUSED
       Audit_Log entry
    4. Entitlement: ``entitlement_resolver.resolve`` must be entitling (reason SUBSCRIBED on
       this path); a failed read is 503 MARKETPLACE_READ_FAILED, never a 403
    5. Idempotency: return existing clone if already cloned
    6. Fetch source strategy DAG via service role
    7. Insert new strategies row owned by calling user
    8. Increment clone_count
    9. Create / update library_ratings row with is_verified_clone=TRUE
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # 1. Fetch library entry
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("id, author_id, source_strategy_id, name, is_active, moderation_status, clone_count, source_cloning_enabled")
            .eq("id", lib_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"clone_strategy library lookup error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up library entry.",
        )

    if not lib_resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library strategy not found.",
        )

    lib_entry = lib_resp.data

    if not lib_entry["is_active"] or lib_entry["moderation_status"] not in ("approved", "featured"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not available for cloning.",
        )

    # 2. Block self-clone (existing behaviour, unchanged — 409). The owner clones nothing: the
    #    long-standing answer to "clone your own listing" is a 409, and it is independent of the
    #    consent flag below (a flag that governs *other* users copying the logic). Keeping it
    #    ahead of the cloning-disabled gate is what preserves that 409 for the owner, whose own
    #    entitlement would be OWNED/entitling anyway.
    if lib_entry["author_id"] == user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot clone your own published strategy.",
        )

    # 3. Owner-consent gate for non-owner cloners (Requirements 7.3, 7.4, 7.12; design.md
    #    clone_strategy redesign step 2). This runs before any `strategies` row is created and
    #    before clone_count is touched: if the owner has not enabled cloning, the operation is
    #    refused 403 MARKETPLACE_CLONING_DISABLED, nothing is written to `strategies`,
    #    clone_count is left unchanged, and one MARKETPLACE_ACCESS_REFUSED Audit_Log entry is
    #    written. The audit record carries no Protected_Logic — only the listing id, the actor
    #    and the reason.
    if not lib_entry.get("source_cloning_enabled"):
        try:
            await get_strategy_audit_logger().log(
                StrategyAuditAction.MARKETPLACE_ACCESS_REFUSED,
                actor_id=str(user_id),
                resource_type="strategy",
                resource_id=str(lib_id),
                reason=MARKETPLACE_CLONING_DISABLED,
                metadata={"operation": "clone", "listing_id": str(lib_id)},
            )
        except Exception as audit_exc:  # never-swallow logger already swallows; belt-and-braces
            logger.error(f"clone_strategy cloning-disabled audit error: {audit_exc}")
        raise MarketplaceError(MARKETPLACE_CLONING_DISABLED)

    # 4. Entitlement gate (Requirement 7.2; design.md clone_strategy redesign step 3). A
    #    non-owner cloner MUST hold an entitling Subscription — the single admission decision
    #    is the Entitlement_Resolver. A non-entitling result is refused with the resolver's own
    #    wire code (MARKETPLACE_NOT_SUBSCRIBED / MARKETPLACE_SUBSCRIPTION_EXPIRED /
    #    MARKETPLACE_STRATEGY_UNAVAILABLE / MARKETPLACE_OPERATION_NOT_PERMITTED). The owner case
    #    never reaches here (step 3 returned 409), so the entitling reason on this path is
    #    SUBSCRIBED.
    #
    #    A read that DID NOT COMPLETE is not a refusal. `EntitlementReadFailed` is caught here
    #    and re-raised as MARKETPLACE_READ_FAILED (503), never folded into the 403 branch
    #    below: answering "you are not subscribed" because a Persistence_Layer read broke would
    #    tell a paying subscriber something false about their own subscription (Requirements
    #    1.5, 1.7, 30.5). The outcome stays defined — 503, retryable — and, because this runs
    #    before step 5, nothing is written to `strategies` and clone_count is untouched.
    try:
        entitlement = await _entitlement_resolver.resolve(
            {"id": user_id},
            lib_id,
            svc,
            datetime.now(timezone.utc),
        )
    except _entitlement_resolver.EntitlementReadFailed as exc:
        logger.error(f"clone_strategy entitlement read failed for library_id={lib_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if not entitlement.entitling:
        raise MarketplaceError(entitlement.wire_code)

    # 5. Idempotency: check if user already cloned this library entry
    try:
        existing_clone = (
            svc.table("strategies")
            .select("id")
            .eq("source_library_id", lib_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:
        logger.error(f"clone_strategy idempotency check error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check for existing clones.",
        )

    if existing_clone.data:
        existing_id = existing_clone.data[0]["id"]
        return {
            "new_strategy_id": existing_id,
            "message": "You have already cloned this strategy. Returning existing clone.",
        }

    # 6. Fetch source strategy DAG (service-role, cross-user read)
    source_id = lib_entry["source_strategy_id"]
    try:
        src_resp = (
            svc.table("strategies")
            .select("name, symbol, timeframe, exchange_id, buy_logic, sell_logic, risk, indicators, ml_model_path")
            .eq("id", source_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"clone_strategy source fetch error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch source strategy data.",
        )

    if not src_resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source strategy no longer exists.",
        )

    source = src_resp.data
    now_ts = datetime.now(timezone.utc).isoformat()

    # SECURITY: Validate ML/DL strategies have trained models before cloning
    # Extract nodes from buy_logic if present
    nodes = []
    if isinstance(source.get("buy_logic"), dict):
        nodes = source["buy_logic"].get("_nodes", [])
    
    # Check for ML/DL nodes
    ml_nodes = [n for n in nodes if n.get("type", "").lower() in ["ml", "dl"]]
    
    if ml_nodes and not source.get("ml_model_path"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "ML_MODEL_MISSING",
                "message": "Source strategy contains ML/DL nodes but no trained model reference. "
                         "Cannot clone untrained ML strategy."
            }
        )

    # 7. Insert clone into strategies table
    clone_payload = {
        "user_id": user_id,
        "name": f"[Clone] {lib_entry['name']}",
        "symbol": source.get("symbol", ""),
        "timeframe": source.get("timeframe", ""),
        "exchange_id": source.get("exchange_id", ""),
        "buy_logic": source.get("buy_logic"),
        "sell_logic": source.get("sell_logic"),
        "risk": source.get("risk"),
        "indicators": source.get("indicators"),
        "ml_model_path": source.get("ml_model_path"),
        "source_library_id": lib_id,
        "status": "stopped",
        "created_at": now_ts,
        "updated_at": now_ts,
    }

    try:
        clone_resp = svc.table("strategies").insert(clone_payload).execute()
    except Exception as exc:
        logger.error(f"clone_strategy insert error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create strategy clone.",
        )

    if not clone_resp.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Clone operation returned no data.",
        )

    new_strategy_id = clone_resp.data[0]["id"]

    # 8. Increment clone_count. Non-critical — the clone already succeeded — so a failure is
    #    logged and swallowed rather than failing the request. The catch is narrowed to the
    #    concrete PostgREST APIError (Requirement 30.5): a broad `except` here would mask a
    #    programming error such as a bad column name behind a "clone succeeded" response.
    try:
        new_count = (lib_entry.get("clone_count") or 0) + 1
        svc.table("library_strategies").update(
            {"clone_count": new_count, "updated_at": now_ts}
        ).eq("id", lib_id).execute()
    except _PostgrestAPIError as exc:
        logger.error(f"clone_strategy clone_count increment error: {exc}")

    # 9. Create verified-clone marker in library_ratings (upsert). Non-critical, same
    #    narrowing to the concrete PostgREST APIError (Requirement 30.5).
    try:
        svc.table("library_ratings").upsert(
            {
                "library_id": lib_id,
                "user_id": user_id,
                "is_verified_clone": True,
                "created_at": now_ts,
                "updated_at": now_ts,
            },
            on_conflict="library_id,user_id",
        ).execute()
    except _PostgrestAPIError as exc:
        logger.error(f"clone_strategy verified-clone rating marker error: {exc}")

    logger.info(
        f"Strategy cloned: library_id={lib_id} -> new_strategy_id={new_strategy_id} by user={user_id}"
    )

    # 8. Invalidate cache
    try:
        r_client = await redis_manager.get_client()
        if r_client:
            for key in r_client.scan_iter("library:browse:*"):
                await r_client.delete(key)
    except Exception as e:
        logger.warning(f"Redis cache invalidation failed: {e}")

    return {
        "new_strategy_id": new_strategy_id,
        "message": "Strategy cloned into your builder. Ready to customise.",
    }


# The owner's consent switch for the clone gate above — PATCH
# /api/library/{library_id}/settings {source_cloning_enabled} — is declared on
# `literal_router` further down this module, immediately ahead of the other
# literal-router routes that carry a path parameter. See the section header there for why
# the registration order matters.


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/rate — Submit / update rating
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{library_id}/rate", status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def rate_strategy(
    request: Request,
    library_id: str,
    payload: SubmitRatingRequest,
    user: dict = Depends(get_current_user),
):
    """
    Submit or update a rating for a library strategy.
    Only users who have cloned the strategy (is_verified_clone=TRUE) may rate.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # Verify the strategy is active and approved
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("id, is_active, moderation_status")
            .eq("id", lib_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"rate_strategy library lookup error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up library entry.",
        )

    if not lib_resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library strategy not found.",
        )

    lib_entry = lib_resp.data
    if not lib_entry["is_active"] or lib_entry["moderation_status"] not in ("approved", "featured"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not available for rating.",
        )

    # Verify user has cloned this strategy
    try:
        clone_check = (
            svc.table("library_ratings")
            .select("id, is_verified_clone")
            .eq("library_id", lib_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"rate_strategy clone check error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check clone status.",
        )

    if not clone_check.data or not clone_check.data.get("is_verified_clone"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must clone this strategy before you can rate it.",
        )

    now_ts = datetime.now(timezone.utc).isoformat()

    # Upsert rating
    try:
        svc.table("library_ratings").upsert(
            {
                "library_id": lib_id,
                "user_id": user_id,
                "rating": payload.rating,
                "review_text": payload.review_text,
                "is_verified_clone": True,
                "updated_at": now_ts,
            },
            on_conflict="library_id,user_id",
        ).execute()
    except Exception as exc:
        logger.error(f"rate_strategy upsert error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit rating.",
        )

    # Recompute avg_rating (synchronous for now; background worker in Sprint 2A.3)
    _recompute_avg_rating(lib_id)

    logger.info(f"Rating submitted: library_id={lib_id} rating={payload.rating} by user={user_id}")

    # Broadcast marketplace event
    try:
        await ws_manager.broadcast_marketplace("rating_submitted", {
            "library_id": lib_id,
            "user_id": user_id,
            "rating": payload.rating,
            "updated_at": now_ts,
        })
    except Exception as e:
        logger.warning(f"Failed to broadcast marketplace event: {e}")

    # Invalidate cache
    try:
        r_client = await redis_manager.get_client()
        if r_client:
            for key in r_client.scan_iter("library:browse:*"):
                await r_client.delete(key)
    except Exception as e:
        logger.warning(f"Redis cache invalidation failed: {e}")

    return {
        "library_id": lib_id,
        "rating": payload.rating,
        "review_text": payload.review_text,
        "message": "Rating submitted.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/admin/library/{library_id} — Admin moderation
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/admin/{library_id}", status_code=status.HTTP_200_OK)
async def admin_moderate_strategy(
    library_id: str,
    body: AdminModerateRequest,
    admin: dict = Depends(get_admin_user),
):
    """
    Admin-only: Approve, reject, feature, or hide a library strategy.
    Requires app_metadata.role == "admin".
    """
    lib_id = _safe_uuid(library_id, "library_id")
    admin_id = _safe_uuid(admin["id"], "admin_id")
    svc = _build_service_client()

    # Verify entry exists
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("id, moderation_status, source_strategy_id")
            .eq("id", lib_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"admin_moderate lookup error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to look up library entry.",
        )

    if not lib_resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library strategy not found.",
        )

    # This route is RETAINED only for `is_featured`/`moderation_notes`; the Submission
    # state machine and its six admin actions are the only path that moves the effective
    # lifecycle (Task 14.5, Requirements 4.2, 4.12). So a `moderation_status` is accepted
    # ONLY when it agrees with `MODERATION_STATUS_FOR_STATE[current submission state]` for
    # the listing's open Submission; a disagreeing value would change the lifecycle behind
    # the state machine's back and is refused with 409. A legacy pre-marketplace listing
    # with no Submission row has no lifecycle to protect, so the legacy moderation path is
    # left intact for it.
    current_submission_state = _current_submission_state_for_listing(
        svc, lib_resp.data.get("source_strategy_id")
    )
    if current_submission_state is not None:
        allowed_status = MODERATION_STATUS_FOR_STATE.get(current_submission_state)
        if body.moderation_status != allowed_status:
            raise MarketplaceError(MARKETPLACE_USE_SUBMISSION_ACTIONS)

    now_ts = datetime.now(timezone.utc).isoformat()
    update_payload = {
        "moderation_status": body.moderation_status,
        "moderated_by": admin_id,
        "moderated_at": now_ts,
        "updated_at": now_ts,
    }
    if body.moderation_notes is not None:
        update_payload["moderation_notes"] = body.moderation_notes
    if body.is_featured is not None:
        update_payload["is_featured"] = body.is_featured
        # If featuring, auto-approve if not already
        if body.is_featured and body.moderation_status not in ("approved", "featured"):
            update_payload["moderation_status"] = "featured"
    # Rejected or hidden strategies become inactive
    if body.moderation_status in ("rejected",):
        update_payload["is_active"] = False

    try:
        svc.table("library_strategies").update(update_payload).eq("id", lib_id).execute()
    except Exception as exc:
        logger.error(f"admin_moderate update error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update moderation status.",
        )

    logger.info(
        f"Admin moderation: library_id={lib_id} status={body.moderation_status} by admin={admin_id}"
    )

    return {
        "library_id": lib_id,
        "moderation_status": body.moderation_status,
        "message": f"Strategy moderation status updated to '{body.moderation_status}'.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/admin/library/pending — Admin pending queue
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/admin/pending", status_code=status.HTTP_200_OK)
async def admin_pending_strategies(
    admin: dict = Depends(get_admin_user),
):
    """
    Admin-only: Returns all strategies awaiting moderation, ordered oldest-first.

    A read that did not complete answers the stable ``MARKETPLACE_READ_FAILED`` rather than the
    ``HTTPException(500, "Failed to fetch pending moderation queue.")`` that used to sit here: a
    5xx, so no figure was fabricated, but with no machine-readable code for a reviewer's client
    to branch on and with the legacy envelope's internal ``path`` member (Requirements 1.5,
    22.9). The success body — ``{"items": [...], "total": n}`` with the batched
    ``author_alias`` — is unchanged.
    """
    svc = _build_service_client()

    try:
        resp = (
            svc.table("library_strategies")
            .select(
                "id, name, author_id, category, difficulty, moderation_status, "
                "published_at, clone_count, is_featured"
            )
            .eq("moderation_status", "pending")
            .order("published_at", desc=False)
            .execute()
        )
    except Exception as exc:
        logger.error(f"admin_pending_strategies error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # One batched alias read for the whole queue (Requirement 27.1), not one profile read per
    # pending row. This is an ADMIN review surface, not a catalogue read: the reviewer needs
    # `moderation_status` and `author_id`, which Requirement 6.4 denies to a non-owner, so the
    # public `project_listing` allow-list is deliberately not applied here — only the N+1 is
    # removed. An id the batched read cannot resolve is absent from the map and its
    # `author_alias` is therefore OMITTED rather than filled with "Anonymous" or the author's
    # email address, which is the substitution Requirement 28.5 forbids.
    rows = resp.data if resp is not None else None
    if rows is None:
        # No readable ``data``: an empty queue here tells an Admin_Reviewer there is nothing to
        # review, which is the fabricated answer Requirements 1.5 and 1.7 forbid.
        logger.error("admin_pending_strategies read unreadable")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)
    alias_by_author_id = _page_alias_map(rows, svc)
    items = []
    for row in rows:
        item = dict(row)
        alias = alias_by_author_id.get(item.get("author_id"))
        if alias is not None:
            item["author_alias"] = alias
        items.append(item)

    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/checkout — Create checkout session for subscription
# ─────────────────────────────────────────────────────────────────────────────

class MarketplaceCheckoutRequest(BaseModel):
    currency: str = Field("USD", pattern="^(USD|INR)$")

@router.post("/{library_id}/checkout")
@limiter.limit("5/60second", key_func=caller_or_address)
async def create_marketplace_checkout(
    request: Request,
    library_id: str,
    body: MarketplaceCheckoutRequest,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_access),
):
    """Create a payment checkout session for a Marketplace Subscription (task 18.2).

    The service owns the transaction; this handler owns HTTP. That division is the whole of
    this rewrite: every one of the four defects the previous body carried lived in work the
    router had no business doing.

    * ``strat = resp.data[0]`` on a ``.single()`` response. ``.single()`` sets ``resp.data`` to
      a **dict**, so ``dict[0]`` raised ``KeyError: 0`` — and the statement sat outside the
      ``try``, so FastAPI turned it into a bare 500. **This endpoint had never completed for
      any Listing.** There is no driver response indexed here any more:
      :func:`checkout_service._rows` normalises the ``.single()`` dict, the ``{"data": …}``
      envelope and the bare list into one shape, so there is no index to get wrong.
    * ``int(float(strat.get("price", 0)) * 100)`` and its ``amount_paise`` twin charged 1998
      for a ``$19.99`` Listing. Both are gone. The amount is
      ``money.amount_for_listing(price_minor)`` — the stored integer, unchanged — and no
      ``float`` appears on this path (Requirements 9.2, 10.3).
    * ``select("*")`` on ``library_strategies``. The service reads
      ``checkout_service.CHECKOUT_LISTING_SELECT``, an explicit column list with the
      Submission_State embedded, so a column a future migration adds is not fetched by
      default (Requirement 6.1). This was the last offender in this file.
    * ``svc.table("library_subscriptions").delete().eq("id", sub_id)`` inside a bare
      ``except: pass``. That destroyed the only record that a payment attempt had been made,
      which is exactly what Requirement 9.4 forbids. The service ``UPDATE``s the row to
      ``PAYMENT_FAILED`` with a ``failure_cause`` and a ``failed_at`` instead, and never
      deletes it. No delete and no bare ``except`` remains here.

    The provider credentials and the ``stripe`` / ``razorpay`` imports moved into the service
    behind ``billing._validate_keys`` (task 18.1), so this handler imports neither SDK and
    reads no ``STRIPE_SECRET_KEY`` — least of all with the ``"sk_test_dummy"`` default that
    used to let a production path open a test-mode session.

    There is deliberately **no** ``except`` in this handler. Every outcome the service defines
    leaves it as a :class:`MarketplaceError`, whose registered handler in ``main.py`` renders
    the catalogue's status and the ``{"error": {code, message, details}, "request_id"}``
    envelope: 400 ``MARKETPLACE_OWN_LISTING``, 409 ``MARKETPLACE_LISTING_NOT_PURCHASABLE``,
    404 ``NOT_FOUND``, 503 ``MARKETPLACE_READ_FAILED``, 502
    ``MARKETPLACE_CHECKOUT_UNAVAILABLE`` (which is also the 30-second-deadline answer,
    Requirement 9.13). Catching any of those to re-shape it would be the swallow Requirement
    30.5 forbids, and there is nothing else to catch: ``_safe_uuid`` has already rejected a
    malformed id and the ``currency`` pattern has already rejected an unsupported code.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    # `_build_service_client` raises its own 500 when the environment is misconfigured, and
    # returns None only under DEV_MODE. There is no `if not svc: 503` guard here because the
    # service's own read-failure path already answers a *truthful* 503 MARKETPLACE_READ_FAILED
    # with the structured envelope for an unusable handle, rather than the bare
    # `HTTPException(503, "Service unavailable")` string this handler used to raise.
    svc = _build_service_client()

    # The caller identity handed to the service is the server-side, UUID-validated one — never
    # a value from the body or the query string (Requirements 7.7, 21.1).
    result = await _checkout_service.create_checkout(
        caller={"id": user_id},
        listing_id=lib_id,
        currency=body.currency,
        supabase=svc,
    )

    # The response body. Every money value is an ``int`` of Minor_Units straight off
    # ``CheckoutResult`` — nothing is re-derived, re-scaled or formatted here, so there is no
    # second place for the amount to disagree with what the provider was asked to charge.
    response = {
        "subscription_id": result.subscription_id,
        "library_id": result.listing_id,
        "provider": result.provider,
        "provider_reference": result.provider_reference,
        "amount_minor": result.amount_minor,
        "currency": result.currency,
    }
    if result.checkout_url:
        # Stripe: the browser follows this. `StrategyMarketplace.jsx` reads `checkout_url`.
        response["checkout_url"] = result.checkout_url
    if result.provider == "razorpay":
        # Razorpay: the browser opens the checkout with the order id and the publishable key
        # id, which travels on `extra` below. `order_id` is the key that page already reads.
        response["order_id"] = result.provider_reference
    # `extra` carries only the provider-specific, non-secret members the service chose to
    # surface (Razorpay's `razorpay_key` and `amount`); Stripe's is empty.
    response.update(dict(result.extra))
    return response


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/subscribe — Subscribe to strategy
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{library_id}/subscribe", status_code=status.HTTP_200_OK)
async def get_subscription_status(
    library_id: str,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_access),
):
    """
    Get current subscription status for a marketplace strategy.
    
    Use POST /api/library/{library_id}/checkout to create a payment session.
    The billing webhook will automatically activate the subscription on successful payment.
    
    This endpoint is read-only and returns the current subscription status.
    Manual activation without payment verification is not permitted.

    A read that did not complete answers the stable ``MARKETPLACE_READ_FAILED`` rather than the
    ``HTTPException(500, "Failed to check subscription status.")`` that used to sit here — a
    5xx with no machine-readable code and the legacy envelope (Requirements 1.5, 22.9). A
    response carrying no readable ``data`` is refused for a second reason: the branch below
    reads an empty result as ``"status": "not_subscribed"``, so an unreadable response would
    tell a paying subscriber they hold no subscription (Requirements 1.7, 28.5). A read that
    COMPLETED and matched no row still answers ``not_subscribed``, exactly as before.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    # Check for existing subscription
    try:
        sub_resp = (
            svc.table("library_subscriptions")
            .select("*")
            .eq("library_id", lib_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:
        logger.error(f"Subscription lookup error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    subscription_rows = sub_resp.data if sub_resp is not None else None
    if subscription_rows is None:
        logger.error("subscription status read unreadable for user %s", user_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if not subscription_rows:
        return {
            "status": "not_subscribed",
            "library_id": lib_id,
            "message": "No subscription found. Complete checkout at /api/library/{library_id}/checkout"
        }

    subscription = subscription_rows[0]
    current_status = subscription.get("status", "unknown")
    
    return {
        "status": current_status,
        "subscription_id": subscription.get("id"),
        "library_id": lib_id,
        "started_at": subscription.get("started_at"),
        "expires_at": subscription.get("expires_at"),
        "message": f"Subscription status: {current_status}"
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/{library_id}/deploy/check — Check deployment permission
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{library_id}/deploy/check")
async def check_deployment_permission_endpoint(
    library_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Check if the authenticated user has permission to deploy this marketplace strategy.
    Returns permission status and details.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    
    permission = check_deployment_permission(user_id, lib_id)
    
    return {
        "library_id": lib_id,
        "user_id": user_id,
        **permission
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/deploy — Deploy marketplace strategy
# ─────────────────────────────────────────────────────────────────────────────


class DeployMarketplaceStrategyRequest(BaseModel):
    """The subscriber-supplied body for ``POST /api/library/{id}/deploy`` (task 17.3).

    The subscriber controls only the *runtime* of the deployment — the symbol, the timeframe,
    the capital committed and the session options. It never supplies, and can never smuggle in,
    any part of the owner's Protected_Logic: there is no ``strategy_definition``, ``graph``,
    ``compiled_plan``, ``buy_logic``, ``sell_logic``, ``risk``, ``indicators``, ``ml_model_path``,
    ``version_id``, ``owner_id``, ``tenant_id`` or ``subscription_id`` field here (Requirements
    7.1, 7.6, 7.8). ``model_config = extra="forbid"`` makes any such field a 422 — see
    :func:`_parse_deploy_request`, which produces the 422 by hand so it names the offending
    field(s) but **echoes no supplied value** (Requirement 7.6; a value echoed back into an
    error body is exactly the leakage this design forbids).

    Only ``symbol`` and ``timeframe`` may cross the boundary; ``capital`` and ``session_options``
    are accepted but not the concern of this task's persistence — the owner's version already
    carries the executable artifact, and the deployment row binds to it rather than copying it.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Optional[str] = Field(
        None, max_length=50, description="The market symbol to run against."
    )
    timeframe: Optional[str] = Field(
        None, max_length=20, description="The candle timeframe to run against."
    )
    capital: Optional[float] = Field(
        None, ge=0, description="The capital the subscriber commits to this deployment."
    )
    session_options: Optional[dict] = Field(
        None, description="Opaque per-session runtime options."
    )


#: The set of fields a subscriber may send to the deploy endpoint. Any other key is a 422 that
#: names the key(s) but never their value (Requirement 7.6). It is exactly Requirement 7.5's
#: list of execution parameters — symbol, timeframe, capital, session options — and nothing else:
#: not a strategy definition, graph or compiled plan, and not a version, owner, tenant or
#: subscription identifier, because every one of those is resolved server-side from the
#: authenticated session and the Listing (Requirement 7.7).
_DEPLOY_ALLOWED_FIELDS = frozenset(
    {"symbol", "timeframe", "capital", "session_options"}
)

#: The stable code the deploy endpoint answers an unaccepted or malformed field with. Not a
#: member of the shared marketplace catalogue: ``design.md`` -> "The error code catalogue" has no
#: 422 entry, and Requirement 7.6 asks for "an error indicating that an unaccepted field was
#: supplied", which is a request-shape refusal rather than a marketplace-domain one. Written once
#: here so the response body, the Audit_Log ``reason`` and any future test all read the same
#: spelling.
DEPLOY_REQUEST_INVALID = "DEPLOY_REQUEST_INVALID"


async def _audit_marketplace_refusal(
    *,
    actor_id,
    listing_id,
    operation: str,
    reason: str,
    extra: Optional[dict] = None,
) -> None:
    """Record one ``MARKETPLACE_ACCESS_REFUSED`` Audit_Log entry for a refused operation.

    Requirement 7.12: a refusal under Criterion 1, 3, 6, 8 or 10 is recorded within 5 seconds
    with the authenticated caller identity, the Listing identifier, the attempted operation, the
    refusal reason code and a UTC timestamp — and with **no** Protected_Logic in the entry. The
    metadata this builds carries only the operation name, the Listing id and, where relevant,
    the *names* of the offending request fields; never a field's value, never a graph, node,
    indicator, threshold or model path.

    The audit write never converts a refusal into a different outcome. ``get_strategy_audit_logger``
    already swallows its own failures; the ``except`` here is belt-and-braces so that a logger
    that starts raising cannot turn a 403/422 into a 500, which would tell the caller something
    false about why they were refused.
    """
    metadata = {"operation": operation, "listing_id": str(listing_id)}
    if extra:
        metadata.update(extra)
    try:
        await get_strategy_audit_logger().log(
            StrategyAuditAction.MARKETPLACE_ACCESS_REFUSED,
            actor_id=str(actor_id),
            resource_type="strategy",
            resource_id=str(listing_id),
            reason=reason,
            metadata=metadata,
        )
    except Exception as audit_exc:  # never-swallow logger already swallows; belt-and-braces
        logger.error(f"marketplace refusal audit error ({operation}/{reason}): {audit_exc}")


async def _parse_deploy_request(
    request: Request,
    *,
    actor_id,
    listing_id,
) -> DeployMarketplaceStrategyRequest:
    """Parse the deploy body, rejecting any unknown field with a value-free 422.

    Why this is parsed by hand rather than through a FastAPI body parameter: FastAPI's default
    ``RequestValidationError`` serialiser includes the rejected ``input`` value in the 422 body.
    For a normal payload that is a convenience; for this endpoint it is a Protected_Logic leak —
    a subscriber who sends ``buy_logic={...}`` would see it echoed straight back. So the raw
    body is read here, its extra keys are detected against :data:`_DEPLOY_ALLOWED_FIELDS`, and
    the refusal is a 422 that names the offending field(s) and carries **no value at all**
    (Requirement 7.6). A well-formed body is then validated through the Pydantic model, whose
    own ``extra="forbid"`` is the second line of defence.

    Every refusal here also writes one ``MARKETPLACE_ACCESS_REFUSED`` Audit_Log entry
    (Requirement 7.12's Criterion 6 half) carrying the field *names* and no value, which is why
    ``actor_id`` and ``listing_id`` are passed in: the entry is worthless without the caller and
    the Listing it was attempted against.
    """
    try:
        raw = await request.json()
    except Exception:
        await _audit_marketplace_refusal(
            actor_id=actor_id,
            listing_id=listing_id,
            operation="deploy",
            reason=DEPLOY_REQUEST_INVALID,
            extra={"malformed_body": True},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": DEPLOY_REQUEST_INVALID,
                "message": "Request body must be a JSON object.",
            },
        )

    # An absent body (empty POST) is a valid, all-optional request.
    if raw is None:
        raw = {}

    if not isinstance(raw, dict):
        await _audit_marketplace_refusal(
            actor_id=actor_id,
            listing_id=listing_id,
            operation="deploy",
            reason=DEPLOY_REQUEST_INVALID,
            extra={"malformed_body": True},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": DEPLOY_REQUEST_INVALID,
                "message": "Request body must be a JSON object.",
            },
        )

    unexpected = sorted(k for k in raw.keys() if k not in _DEPLOY_ALLOWED_FIELDS)
    if unexpected:
        # Name the fields, never their values (Requirement 7.6).
        await _audit_marketplace_refusal(
            actor_id=actor_id,
            listing_id=listing_id,
            operation="deploy",
            reason=DEPLOY_REQUEST_INVALID,
            extra={"unexpected_fields": unexpected},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": DEPLOY_REQUEST_INVALID,
                "message": "Unexpected field(s) in deploy request.",
                "unexpected_fields": unexpected,
            },
        )

    try:
        return DeployMarketplaceStrategyRequest(**raw)
    except ValidationError as exc:
        # Surface only the field locations, not the offending values (Requirement 7.6).
        fields = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
        await _audit_marketplace_refusal(
            actor_id=actor_id,
            listing_id=listing_id,
            operation="deploy",
            reason=DEPLOY_REQUEST_INVALID,
            extra={"invalid_fields": fields},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": DEPLOY_REQUEST_INVALID,
                "message": "Invalid deploy request field(s).",
                "invalid_fields": fields,
            },
        )


def _owner_version_label(svc, version_id: str) -> Optional[str]:
    """The owner's human-readable version label (``strategy_versions.version``), or ``None``.

    ``strategy_deployments.version`` is ``VARCHAR(20) NOT NULL`` and holds the label
    (``"v1.2"``), not the id — a 36-character UUID neither fits it nor means the same thing.
    The Entitlement_Resolver deliberately does not carry the label: :class:`Entitlement`
    carries "the identifiers the caller needs and no more", and a display label is not an
    identifier. So it is read here, in the one place that needs it.

    The projection names ``id`` and ``version`` only. ``strategy_versions`` also holds
    ``blueprint`` and ``execution_graph`` — the two Protected_Logic columns on that table — and
    neither is selected, so nothing this read returns can carry the owner's logic
    (Requirement 7.1). The pair is bound by the ``marketplace_deployment`` entry of
    ``backend_app.backend.marketplace.COLUMN_CONTRACT``.

    Returns ``None`` when the version row has vanished between the admission decision and this
    read — the caller answers that as ``MARKETPLACE_STRATEGY_UNAVAILABLE`` (409), the same
    answer the resolver gives for an unresolvable artifact.

    Raises:
        MarketplaceError: ``MARKETPLACE_READ_FAILED`` (503) when the read did not complete. A
            broken read is not "the version is gone": answering 409 would tell the subscriber
            something false about the Listing (Requirements 1.5, 1.7, 30.5).
    """
    try:
        resp = (
            svc.table("strategy_versions")
            .select("id, version")
            .eq("id", version_id)
            .execute()
        )
    except Exception as exc:
        logger.error(f"deploy_strategy owner version read failed for {version_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = getattr(resp, "data", None) or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return None
    label = rows[0].get("version")
    return str(label) if label is not None else None


@router.post("/{library_id}/deploy")
@limiter.limit("10/60second", key_func=caller_or_address)
async def deploy_marketplace_strategy(
    request: Request,
    library_id: str,
    user: dict = Depends(get_current_user),
):
    """Deploy a marketplace Listing the caller is entitled to, without copying its logic.

    The subscriber-safe path (task 17.3, Requirements 7.1, 7.5, 7.6, 7.8, 11.10).

    Order, and each step's refusal:

    1. **Body shape.** Only ``symbol``, ``timeframe``, ``capital`` and ``session_options`` are
       accepted. Anything else — a strategy definition, graph, compiled plan, version, owner,
       tenant or subscription identifier — is a 422 naming the field(s) and echoing **no**
       supplied value (Requirement 7.6). Nothing is created and no read is issued.
    2. **Admission.** ``entitlement_resolver.resolve`` is the *single* decision, replacing the
       old ``check_deployment_permission``-only gate. Non-entitling → the resolver's own wire
       code (Requirements 7.10, 7.11, 11.10). ``EntitlementReadFailed`` → 503
       ``MARKETPLACE_READ_FAILED``, never a refusal.
    3. **The write.** A ``strategy_deployments`` row bound to the OWNER's ``strategy_id`` and
       ``version_id``, with ``marketplace_listing_id`` recorded and ``user_id`` = the
       SUBSCRIBER. It does **not** create a ``strategies`` row carrying the owner's
       ``buy_logic`` / ``sell_logic`` / ``risk`` / ``indicators`` / ``ml_model_path``, which is
       what it used to do: the subscriber EXECUTES the owner's immutable version server-side and
       never holds a copy of it (Requirements 7.1, 7.5).

    Both refusal paths write one ``MARKETPLACE_ACCESS_REFUSED`` Audit_Log entry carrying the
    caller, the Listing, the operation and the reason code, and no Protected_Logic
    (Requirement 7.12).
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # ── The subscriber controls only symbol/timeframe/capital/session options. Any other
    #    field — a strategy definition, graph, compiled plan, version identifier, owner
    #    identifier, tenant identifier or subscription identifier — is a 422 that echoes no
    #    supplied value (Requirement 7.6), audited under Requirement 7.12. Parsed before any
    #    admission read, so a malformed body costs no round trip and starts nothing. ──
    deploy_request = await _parse_deploy_request(
        request, actor_id=user_id, listing_id=lib_id
    )

    # ── The single admission decision (Requirements 7.5, 7.7, 11.10, property P-16). It
    #    replaces the old check_deployment_permission-only gate entirely — there is no second
    #    check here, and in particular no re-reading of library_subscriptions.status: the
    #    resolver reads the current Subscription_State inside this request, compares `now`
    #    against `period_expiry` itself (so an expiry that the housekeeping sweep has not swept
    #    still refuses), and is the same decision that gates a Paper_Session start.
    #
    #    Requirement 11.10 — "a subscription that ceases to entitle blocks new deployments" — is
    #    therefore satisfied by the resolver's own verdict rather than by a rule restated here.
    #
    #    A read that DID NOT COMPLETE is not a refusal. `EntitlementReadFailed` becomes
    #    MARKETPLACE_READ_FAILED (503), never a 403: answering "you are not subscribed" because
    #    a Persistence_Layer read broke would tell a paying subscriber something false about
    #    their own subscription (Requirements 1.5, 1.7, 30.5). Nothing has been written at this
    #    point, so the 503 is cleanly retryable. ──
    try:
        entitlement = await _entitlement_resolver.resolve(
            {"id": user_id},
            lib_id,
            svc,
            datetime.now(timezone.utc),
        )
    except _entitlement_resolver.EntitlementReadFailed as exc:
        logger.error(
            f"deploy_strategy entitlement read failed for library_id={lib_id}: {exc}"
        )
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if not entitlement.entitling:
        # The resolver's own wire code: MARKETPLACE_NOT_SUBSCRIBED /
        # MARKETPLACE_SUBSCRIPTION_EXPIRED (403, distinct codes per Requirement 7.10) /
        # MARKETPLACE_STRATEGY_UNAVAILABLE (409, Requirement 7.11) /
        # MARKETPLACE_OPERATION_NOT_PERMITTED (403). Nothing is created, and the Subscription's
        # own state and period are untouched.
        await _audit_marketplace_refusal(
            actor_id=user_id,
            listing_id=lib_id,
            operation="deploy",
            reason=entitlement.wire_code,
            extra={"entitlement_reason": entitlement.reason.value},
        )
        raise MarketplaceError(entitlement.wire_code)

    # An entitling result always carries the owner's current live version id and the backing
    # strategy id (resolve() resolves the current version as part of its LISTING_UNAVAILABLE
    # detection). Absence here would be a resolver contract violation, not a caller error.
    owner_version_id = entitlement.version_id
    owner_strategy_id = entitlement.source_strategy_id
    if not owner_version_id or not owner_strategy_id:
        logger.error(
            "deploy_strategy: entitling result for listing %s carried no owner version/strategy",
            lib_id,
        )
        raise MarketplaceError(_entitlement_resolver.WIRE_CODE_FOR_REASON[
            _entitlement_resolver.EntitlementReason.LISTING_UNAVAILABLE
        ])

    # The owner's version LABEL for the NOT NULL VARCHAR(20) `version` column. See
    # `_owner_version_label` for why the resolver does not carry it and why the projection names
    # no Protected_Logic column. A vanished version between the decision and here is the same
    # 409 the resolver gives for an unresolvable artifact (Requirement 7.11); a broken read is
    # the 503 raised inside the helper.
    owner_version_label = _owner_version_label(svc, owner_version_id)
    if owner_version_label is None:
        raise MarketplaceError(_entitlement_resolver.WIRE_CODE_FOR_REASON[
            _entitlement_resolver.EntitlementReason.LISTING_UNAVAILABLE
        ])

    now_ts = datetime.now(timezone.utc).isoformat()

    # ── The subscriber-safe deployment row (Requirements 7.1, 7.5).
    #
    #    It binds to the OWNER's `strategy_id` and `version_id` and records `user_id` = the
    #    SUBSCRIBER, who is the row's RLS owner. The executable artifact is fetched server-side
    #    from that version at run time; it is never copied into a row the subscriber owns. No
    #    `buy_logic`, `sell_logic`, `risk`, `indicators` or `ml_model_path` is read or written
    #    anywhere on this path — the `strategies` INSERT that carried all five into a
    #    subscriber-owned row is the vulnerability this task removes, and its absence here is
    #    the fix.
    #
    #    `marketplace_listing_id` is the additive marketplace-sourced deployment source that
    #    `.kiro/specs/trading-lifecycle-integration/` Requirement 27.3 reserved and 27.4
    #    required be addable without disturbing an existing column. It is created by
    #    `backend_app/migrations/011_marketplace_deployment_source.sql` (nullable UUID, no FK)
    #    and is what distinguishes this row from a first-party deployment: without it, a
    #    deployment pointing at a strategy its `user_id` does not own is indistinguishable from
    #    a cross-user row written in error, and there is nothing to join a deployment back to
    #    the Listing — and so to the Subscription that entitled it — for revocation or audit.
    #
    #    `environment` and the deployment `mode` 004e adds are left at the paper vocabulary:
    #    'paper' is the value that reaches no real order router, and `mode` is not written at
    #    all so its NOT NULL DEFAULT 'paper' applies — writing a column 004e may not have
    #    created in a given environment is the PGRST204 the schema contract exists to prevent.
    #
    #    `timeframe` and `session_options` are accepted from the subscriber (Requirement 7.5)
    #    and are deliberately not persisted on this row: `strategy_deployments` has no column
    #    for either, and per-session runtime options belong to `paper_sessions`
    #    (009_paper_trading.sql), whose start path is task 27.1. Inventing a column here would
    #    be schema this task does not own. ──
    deployment_payload = {
        "user_id": user_id,
        "strategy_id": owner_strategy_id,
        "version_id": owner_version_id,
        "version": owner_version_label,
        "marketplace_listing_id": lib_id,
        "environment": "paper",
        "status": "deploying",
        "created_at": now_ts,
        "updated_at": now_ts,
    }
    if deploy_request.symbol is not None:
        deployment_payload["exchange_symbol"] = deploy_request.symbol
    if deploy_request.capital is not None:
        deployment_payload["initial_capital"] = deploy_request.capital

    try:
        deploy_resp = (
            svc.table("strategy_deployments").insert(deployment_payload).execute()
        )
    except Exception as exc:
        # A PGRST204 naming `marketplace_listing_id` means 011 has not been applied to this
        # database. The refusal names the file rather than retrying without the column: a
        # deployment recorded with no Listing linkage would read as a first-party deployment of
        # another user's version, which is a worse outcome than an outage.
        logger.error(
            f"deploy_strategy deployment insert error (if it names "
            f"marketplace_listing_id, apply "
            f"backend_app/migrations/011_marketplace_deployment_source.sql): {exc}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create marketplace deployment.",
        )

    if not getattr(deploy_resp, "data", None):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deploy operation returned no data.",
        )

    deployment_id = deploy_resp.data[0]["id"]

    # The response carries the deployment handle, the Listing it came from and how access was
    # granted. It carries no `strategy_id`, no `version_id` and no part of the owner's
    # definition: those are server-side identifiers for an artifact the subscriber executes and
    # may not read (Requirement 7.1).
    return {
        "deployment_id": deployment_id,
        "library_id": lib_id,
        "granted_via": entitlement.reason.value,
        "message": "Marketplace deployment created against the owner's version.",
    }

@router.post("/subscriptions/{sub_id}/cancel")
@limiter.limit("10/60second", key_func=caller_or_address)
async def cancel_subscription(
    request: Request, sub_id: str, user: dict = Depends(get_current_user)
):
    """Cancel renewal. Entitlement runs to the unchanged current expiry (task 19.3, Req 11.9).

    Requirement 11.9 says three things about a cancellation, and this handler now does all three
    and nothing more: no further renewal is performed for the Subscription, entitlement is
    **retained until the unchanged current expiry**, and the state moves to ``CANCELLED`` with the
    cancellation instant recorded in UTC.

    **What was deleted.** The immediate revoke::

        svc.table("deployment_permissions").update(
            {"is_active": False, "revoked_at": until}
        ).eq("subscription_id", sub_uid).execute()

    That ended access the moment the purchaser cancelled, for a period they had already paid for -
    the opposite of "retain entitlement until the unchanged current expiry". Access now lapses
    when the period does: ``settlement_service.grant_deployment_permission`` stamped the row's
    ``expires_at`` with the Subscription_Period expiry when the payment settled, the expiry sweep
    of Requirement 11.8 moves the Subscription to ``EXPIRED`` at that instant, and
    ``entitlement_resolver`` decides entitlement from the period rather than from the stored
    status (Requirement 11.7). Nothing here has to revoke anything for access to end on time.

    No period column is touched: ``expires_at``, ``period_start`` and ``period_expiry`` are absent
    from the update payload, which is what "the *unchanged* current expiry" means. And no row is
    deleted - the Subscription is retained permanently (Requirement 11.11).
    """
    sub_uid = _safe_uuid(sub_id, "subscription_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    # Verify subscription exists and belongs to user
    try:
        res = svc.table("library_subscriptions").select("*").eq("id", sub_uid).eq("user_id", user_id).execute()
        if not res.data:
            raise HTTPException(404, f"Subscription '{sub_id}' not found.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Cancel subscription lookup error: {exc}")
        raise HTTPException(500, "Failed to lookup subscription")
    
    until = datetime.now(timezone.utc).isoformat()
    
    try:
        # Only cancel if subscription is active (idempotent)
        update_resp = svc.table("library_subscriptions").update({
            "status": "cancelled",
            "cancelled_at": until
        }).eq("id", sub_uid).eq("status", "active").execute()
        
        if not update_resp.data:
            # Idempotency: subscription already cancelled or invalid state
            logger.info(f"Subscription {sub_id} already cancelled or invalid state - idempotent no-op")
            return {
                "status": "already_cancelled",
                "subscription_id": sub_id,
                "message": "Subscription is already cancelled"
            }
    except Exception as exc:
        logger.error(f"Cancel subscription update error: {exc}")
        raise HTTPException(500, "Failed to cancel subscription")
    
    # NO deployment-permission revoke here. See the docstring: the paid period is honoured to its
    # unchanged expiry (Requirement 11.9), and the grant already carries that expiry in
    # `deployment_permissions.expires_at` (written by `settlement_service`), so access lapses with
    # the period rather than with the cancellation.

    # Decrement subscriber_count on library_strategies
    try:
        sub = res.data[0]
        lib_id = sub.get("library_id")
        if lib_id:
            lib_resp = svc.table("library_strategies").select("subscriber_count").eq("id", lib_id).execute()
            if lib_resp.data:
                current = lib_resp.data[0].get("subscriber_count", 0)
                svc.table("library_strategies").update({
                    "subscriber_count": max(0, current - 1),
                    "updated_at": until
                }).eq("id", lib_id).execute()
    except Exception as exc:
        logger.warning(f"Failed to decrement subscriber_count: {exc}")
    
    return {"status": "cancelled", "subscription_id": sub_id, "cancelled_at": until}


@router.post("/subscriptions/{sub_id}/renew")
@limiter.limit("5/60second", key_func=caller_or_address)
async def renew_subscription(
    request: Request, sub_id: str, user: dict = Depends(get_current_user)
):
    """Create a payment session for the next Subscription_Period. Changes no state (task 19.3).

    **THE DEFECT THIS REPLACES.** The previous body renewed a Subscription by writing it::

        svc.table("library_subscriptions").update({
            "status": "active", "cancelled_at": None, "expires_at": None
        }).eq("id", sub_uid).in_("status", ["cancelled", "expired"]).execute()
        ...
        grant_deployment_permission(user_id, lib_id, "subscription", sub_uid)
        ...  # and then incremented subscriber_count

    No provider was contacted, no amount was ever computed, and no Settlement_Record was written.
    Worse than free: ``expires_at`` was set to **NULL**, and ``check_deployment_permission``
    carried a branch reading a null expiry as a *perpetual* subscription. So one unpriced POST by
    anybody who had ever subscribed once and cancelled turned a lapsed monthly Subscription into
    permanent access, plus a ``deployment_permissions`` row to execute the owner's strategy with.
    Requirement 11.16 orders that path removed; Requirements 11.6 and 11.14 require a payment
    confirmed through the Billing_Integration and recorded as a Settlement_Record before **every**
    transition into ``ACTIVE``.

    **WHAT IT DOES NOW.** It returns a provider session for the renewal amount and nothing else.
    All three deleted statements are gone, and so is the whole idea of this endpoint writing:
    ``checkout_service.create_renewal_checkout`` issues no statement against
    ``library_subscriptions`` at all - not the status, not the period, not ``cancelled_at``, not
    even a ``provider_reference`` (writing that would overwrite the reference of the payment that
    bought the *current* period, which is what a later refund is correlated by). The transition
    into ``ACTIVE`` and the new expiry happen in exactly one place, on a confirmed payment:
    ``settlement_service.settle``, inside the same transaction as the Settlement_Record that
    ``trg_subscription_transition_guard`` refuses the activation without.

    An already-``ACTIVE`` Subscription may renew: ``settlement_service._apply_transition`` treats
    ``active -> active`` as a renewal in place and extends from the stored ``period_expiry``,
    never from ``now``, so renewing early buys the next month rather than shortening this one
    (Requirement 11.5).

    The one provider path is reused rather than duplicated - the same
    ``default_provider_session_factory``, the same 30-second deadline, and the same
    ``_settlement_metadata``, so the **existing** webhook activates this payment and no second
    Billing_Integration exists (Requirement 9.1). There is deliberately no ``except`` here: every
    outcome the service defines is a ``MarketplaceError`` that ``main.py``'s registered handler
    renders as the catalogue's status and envelope (404 ``NOT_FOUND``, 409
    ``MARKETPLACE_LISTING_NOT_PURCHASABLE``, 502 ``MARKETPLACE_CHECKOUT_UNAVAILABLE``, 503
    ``MARKETPLACE_READ_FAILED``).
    """
    sub_uid = _safe_uuid(sub_id, "subscription_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # The caller identity is the server-side, UUID-validated one, and it travels into the service
    # as a *predicate on the read* — so another purchaser's Subscription id cannot be renewed
    # here, and is answered with the same NOT_FOUND body as one that does not exist (Req 21.4).
    result = await _checkout_service.create_renewal_checkout(
        caller={"id": user_id},
        subscription_id=sub_uid,
        supabase=svc,
    )

    # The same body shape POST /{library_id}/checkout returns, for the same reason: every money
    # value is an ``int`` of Minor_Units straight off ``CheckoutResult``, re-derived nowhere.
    response = {
        "status": "renewal_pending_payment",
        "subscription_id": result.subscription_id,
        "library_id": result.listing_id,
        "provider": result.provider,
        "provider_reference": result.provider_reference,
        "amount_minor": result.amount_minor,
        "currency": result.currency,
    }
    if result.checkout_url:
        response["checkout_url"] = result.checkout_url
    if result.provider == "razorpay":
        response["order_id"] = result.provider_reference
    response.update(dict(result.extra))
    return response

# ─────────────────────────────────────────────────────────────────────────────
# The two analytics reads (task 21.1 — design.md "Root-cause fixes" #3)
#
# Both handlers previously assembled a figure rather than reading one. What was
# deleted, and why each deletion is not cosmetic:
#
#   * `.select("id, name, clone_count, monthly_price, rating_average")` —
#     `monthly_price` and `rating_average` exist on no table in the applied
#     migration set (the real columns are `price` and `avg_rating`). The query
#     failed with PostgreSQL 42703 on EVERY call, so this endpoint had never
#     completed for any creator. That is the identical condition the header of
#     `migrations/007_add_marketplace_pricing_columns.sql` records for
#     `/trending` and `/featured` (Requirements 1.3, 1.4).
#   * `mrr = sum(clone_count * float(monthly_price))` and
#     `round(mrr * 0.90, 2)` — `clone_count` counts CLONES, not payments, so the
#     product was not an amount anybody had ever been charged; and the
#     arithmetic was binary floating point on money (Requirements 10.3, 10.6).
#   * `except Exception: return {…every figure 0.0…}` — the bare except swallowed
#     the 42703 above and answered HTTP 200 with a fabricated zero. A creator who
#     had earned nothing and a creator whose read broke saw the same number
#     (Requirements 1.5, 1.7, 28.2, 30.5).
#   * `payout_schedule: "Monthly auto-transfer (Stripe Connect)"` — no payout
#     mechanism exists in this system. Requirement 28 forbids presenting one.
#   * `monthly_spend_usd = round(sum(float(price_paid)), 2)` on the subscriber
#     side — the same float-money defect, one table over.
#
# What replaces them: every figure is read, every money value is an integer
# number of Minor_Units, every total is per currency, and a read that did not
# complete raises `MARKETPLACE_READ_FAILED` with no numeric payload at all.
# ─────────────────────────────────────────────────────────────────────────────

#: The owner's own Listing rows, as `creator_analytics` reads them. Explicit, and every column is
#: one the migration set creates — `subscriber_count`, `avg_rating`, `rating_count` and
#: `moderation_status` from `001_create_library_strategies.sql`. `moderation_status` is read to
#: COUNT the published Listings and is never returned: it is in
#: `listing_projection.DENIED_LISTING_COLUMNS`, and that deny-list is about the response, not the
#: query. `price`, `price_minor`, `clone_count` and `name` are deliberately absent — no figure here
#: is derived from a price or a counter (Requirement 10.6), so selecting them would only create the
#: opportunity.
_CREATOR_ANALYTICS_LISTING_SELECT = "id,subscriber_count,avg_rating,rating_count,moderation_status"

#: The caller's own Subscription rows, as `subscriber_analytics` reads them. `price_minor` replaces
#: the legacy `price_paid NUMERIC` the float arithmetic read: the integer is the amount that was
#: actually charged (Requirement 8.12), and `price_paid` is mirrored from it, never the reverse.
#: `period_start`/`period_expiry` are 008's authoritative period columns; `started_at`/`expires_at`
#: are retained and mirrored from them, and both pairs are returned because existing readers
#: (`check_deployment_permission`, the Strategies page) read the legacy pair (Requirement 25).
_SUBSCRIBER_ANALYTICS_SELECT = (
    "id,library_id,subscription_tier,price_minor,currency,status,"
    "started_at,expires_at,period_start,period_expiry"
)

#: `library_strategies.moderation_status` for a PUBLISHED Submission, resolved through the one
#: shared mapping rather than spelled as a literal at this call site (Requirement 4.12).
_PUBLISHED_MODERATION_STATUS = MODERATION_STATUS_FOR_STATE[SubmissionState.PUBLISHED]


def _earnings_entry(totals: _settlement_service.EarningsTotals) -> dict:
    """One currency's earnings, as the response carries it: integer Minor_Units, labelled.

    Every value is an ``int`` and the currency travels WITH the figures rather than being implied
    by a field name such as ``total_earnings_usd`` — which is what the deleted body did, and which
    was a lie for an INR Listing. Requirement 10.7 forbids one blended total, so the response
    carries one entry per currency and no grand total exists to be misread.

    No major-unit conversion happens here. ``money.to_major`` returns a ``Decimal``, FastAPI would
    serialise it through ``float``, and a money value that has passed through binary floating point
    is exactly what Requirement 10.3 forbids. Formatting belongs to the client, which is told the
    currency and can read its minor-unit exponent from the same ISO 4217 table.
    """
    return {
        "currency": totals.currency,
        "owner_earnings_minor": totals.owner_total_minor,
        "platform_fee_minor": totals.platform_total_minor,
        "gross_minor": totals.gross_total_minor,
        "settlement_count": totals.settlement_count,
        "reversal_count": totals.reversal_count,
    }


@literal_router.get("/creator/analytics")
@limiter.limit("60/60second", key_func=caller_or_address)
async def creator_analytics(request: Request, user: dict = Depends(get_current_user)):
    """The owner's own earnings and catalogue figures, every one of them read from persisted data.

    OWNER-SCOPED, BY PREDICATE (Requirements 21.4, 22.1)
    ---------------------------------------------------
    The identity comes from the authenticated server-side session and is never accepted from the
    request: there is no ``creator_id`` path or query parameter to guess at. It travels as a
    predicate on both reads — ``.eq("author_id", user_id)`` on the Listings and
    ``.eq("owner_id", user_id)`` inside ``settlement_service.creator_earnings`` — so another
    creator's rows are never fetched, not merely never returned.

    TWO ROUND TRIPS, FIXED (Requirement 27.2)
    -----------------------------------------
    One for the owner's Listing rows and one for the owner's ledger, independent of how many
    Listings or Settlement_Records either holds. No per-row read, so the count of Listings cannot
    turn this endpoint into an N+1.

    EVERY EARNINGS FIGURE IS A LEDGER SUM (Requirements 10.6, 10.7)
    --------------------------------------------------------------
    ``creator_earnings`` returns the exact integer sum of ``owner_share_minor`` over non-reversal
    Settlement_Records minus the sum over reversal Settlement_Records, per currency. Nothing here
    multiplies a counter by a price, and nothing converts or combines currencies. That is the same
    quantity ``tests/property/test_settlement_ledger.py::test_p5_reported_totals_equal_ledger_sums``
    states as a property, so the two cannot disagree about what an owner is owed.

    THE RATING IS OMITTED WHEN NOBODY HAS RATED (Requirements 6.9, 28.5)
    -------------------------------------------------------------------
    ``rating_count == 0`` means the rating infrastructure holds no values for this creator, so
    ``avg_rating`` and ``rating_count`` are left out of the response entirely. The deleted body
    answered ``rating_average: 0.0``, which reads as "rated, and badly". An omitted field and a
    null field mean different things, and neither means zero.

    THERE IS NO ``except`` THAT RETURNS (Requirements 1.5, 1.7, 30.5)
    ----------------------------------------------------------------
    A read that did not complete raises ``MARKETPLACE_READ_FAILED`` — 503, one stable code, no
    numeric field of any kind. The driver's detail is logged, where an operator can see it, and
    never put in the body (Requirement 22.9). ``tests/test_marketplace_error_surface.py`` asserts
    that for a connection failure, a query timeout, an undefined column and a permission denial.
    """
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    if not svc:
        # DEV_MODE with no Supabase configured. There is nothing to read, so there is no figure to
        # report — and a zero-filled body here would be the same fabrication as the one above.
        logger.error("creator analytics: no service client available for user %s", user_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # ── Round trip 1: the owner's own Listing rows ────────────────────────
    try:
        resp = (
            svc.table("library_strategies")
            .select(_CREATOR_ANALYTICS_LISTING_SELECT)
            .eq("author_id", user_id)
            .execute()
        )
    except Exception as exc:
        # Narrowed by outcome, not by type, and it RE-RAISES: the driver's message (which carries
        # the sqlstate and a column name) is logged and the caller gets the stable code.
        logger.error("creator analytics listing read failed for %s: %s", user_id, exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data if resp is not None else None
    if rows is None:
        # A response object carrying no ``data`` did not complete in a way this handler can read.
        # Treating it as "no Listings" would report a published creator as having none.
        logger.error("creator analytics listing read unreadable for %s", user_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    published_count = 0
    subscriber_total = 0
    rating_count_total = 0
    rating_weighted_sum = Decimal(0)
    for row in rows:
        if str(row.get("moderation_status") or "") == _PUBLISHED_MODERATION_STATUS:
            published_count += 1
        subscriber_total += int(row.get("subscriber_count") or 0)

        # The creator-level rating is the RATING-COUNT-WEIGHTED mean of the per-Listing averages,
        # not the mean of the averages: a Listing rated once would otherwise carry the same weight
        # as one rated four hundred times. ``Decimal`` because a rating is a NUMERIC(3,2) in the
        # database and the exact value is what was persisted; it never touches a money path.
        listing_rating_count = int(row.get("rating_count") or 0)
        listing_avg_rating = row.get("avg_rating")
        if listing_rating_count > 0 and listing_avg_rating is not None:
            rating_count_total += listing_rating_count
            rating_weighted_sum += Decimal(str(listing_avg_rating)) * listing_rating_count

    # ── Round trip 2: the owner's ledger, per currency, in Minor_Units ────
    try:
        earnings = _settlement_service.creator_earnings(svc, owner_id=user_id)
    except _settlement_service.SettlementPersistenceError as exc:
        logger.error("creator analytics earnings read failed for %s: %s", user_id, exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    response = {
        "creator_id": user_id,
        "listings_count": len(rows),
        "published_strategies_count": published_count,
        # The persisted counter, named after the column it is read from. Not "active_subscribers":
        # this column counts Subscriptions ever taken on the owner's Listings, and calling it
        # "active" would be a claim the read does not support (Requirement 28.2).
        "subscriber_count": subscriber_total,
        # Sorted so the response is stable across two identical reads; ``earnings`` is a mapping
        # keyed by currency and a mapping's order is not a contract.
        "earnings": [_earnings_entry(earnings[code]) for code in sorted(earnings)],
    }

    if rating_count_total > 0:
        # ROUND_HALF_UP and two places, matching the NUMERIC(3,2) the column stores, so the
        # reported average is representable in the same precision as its inputs.
        response["avg_rating"] = float(
            (rating_weighted_sum / rating_count_total).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        response["rating_count"] = rating_count_total

    return response


@literal_router.get("/subscriber/analytics")
@limiter.limit("60/60second", key_func=caller_or_address)
async def subscriber_analytics(request: Request, user: dict = Depends(get_current_user)):
    """The caller's own Subscriptions, and what they are committed to paying, per currency.

    The same three corrections as ``creator_analytics``, on the Subscription side:

    * ``round(sum(float(price_paid)), 2)`` is deleted. ``price_minor`` is the integer amount that
      was actually charged (Requirement 8.12); the legacy ``price_paid NUMERIC`` is mirrored from
      it and is never read back as the amount, because doing so reintroduces the inexactness
      ``price_minor`` was added to remove.
    * The per-currency commitment replaces ``monthly_spend_usd``. A caller with an INR Subscription
      was previously shown its rupees added to their dollars under a ``_usd`` field name
      (Requirement 10.7 forbids the combination; nothing here converts).
    * ``except Exception: raise HTTPException(500, …)`` is replaced by the stable
      ``MARKETPLACE_READ_FAILED``. The old shape was a 5xx — so not a fabricated figure — but it
      carried no machine-readable code, so a client could only branch on a sentence
      (Requirement 1.5).

    An ``ACTIVE`` Subscription whose ``price_minor`` is ``NULL`` (a row written before 008) is
    counted in ``unpriced_active_subscriptions`` rather than contributing a zero to a currency
    total. Dropping it silently would understate the commitment; substituting a zero would state a
    price nobody agreed to (Requirement 28.5).
    """
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    if not svc:
        logger.error("subscriber analytics: no service client available for user %s", user_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # One round trip, scoped to the caller by predicate: `user_id` is the authenticated,
    # UUID-validated identity, so no other purchaser's Subscription is ever fetched.
    try:
        resp = (
            svc.table("library_subscriptions")
            .select(_SUBSCRIBER_ANALYTICS_SELECT)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as exc:
        logger.error("subscriber analytics read failed for %s: %s", user_id, exc)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    subscriptions = resp.data if resp is not None else None
    if subscriptions is None:
        logger.error("subscriber analytics read unreadable for %s", user_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    active_status = STATUS_TEXT_FOR_STATE[SubscriptionState.ACTIVE]
    active = [row for row in subscriptions if str(row.get("status") or "") == active_status]

    committed: dict = {}
    unpriced_active = 0
    for row in active:
        amount_minor = row.get("price_minor")
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            # No integer amount recorded for this period. Counted, never guessed.
            unpriced_active += 1
            continue
        code = str(row.get("currency") or "").strip().upper()
        if not code:
            unpriced_active += 1
            continue
        bucket = committed.setdefault(code, {"amount_minor": 0, "subscription_count": 0})
        bucket["amount_minor"] += amount_minor
        bucket["subscription_count"] += 1

    return {
        "subscriber_id": user_id,
        "active_subscriptions_count": len(active),
        "total_subscriptions_count": len(subscriptions),
        "unpriced_active_subscriptions": unpriced_active,
        "active_commitment": [
            {"currency": code, **committed[code]} for code in sorted(committed)
        ],
        "subscriptions": subscriptions,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/{library_id}/reviews — Get strategy reviews
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{library_id}/reviews")
async def get_strategy_reviews(
    library_id: str,
    limit: int = Query(10, ge=1, le=50),
):
    """Returns reviews for a specific strategy.

    ``{"reviews": [], "total": 0}`` IS A FIGURE, NOT A FALLBACK (Requirements 1.5, 1.7, 28.2)
    ----------------------------------------------------------------------------------------
    The two ``return {"reviews": [], "total": 0}`` statements this handler used to answer a
    failed read with put a ``total`` of ``0`` on the wire — a count of reviews nobody counted,
    rendered on a Listing page as "no reviews yet" for a strategy that may have hundreds. A
    read that did not complete now raises ``MARKETPLACE_READ_FAILED``: 503, one stable code, no
    numeric payload, and the driver's detail logged rather than sent (Requirement 22.9).

    A read that COMPLETED and matched nothing still answers ``{"reviews": [], "total": 0}`` —
    that zero was measured.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    svc = _build_service_client()

    if not svc:
        logger.error("reviews: no service client available for listing %s", lib_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    try:
        resp = (
            svc.table("library_ratings")
            .select("rating, review_text, created_at, user_id")
            .eq("library_id", lib_id)
            .not_.is_("review_text", None)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        logger.error(f"Reviews DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    reviews = resp.data if resp is not None else None
    if reviews is None:
        logger.error("reviews read unreadable for listing %s", lib_id)
        raise MarketplaceError(MARKETPLACE_READ_FAILED)
    
    # Anonymize user IDs
    for review in reviews:
        review["user_alias"] = _get_author_alias(review.get("user_id", ""))
        review.pop("user_id", None)
    
    return {"reviews": reviews, "total": len(reviews)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/recommendations — Get personalized recommendations
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/recommendations")
async def get_recommendations(
    user: dict = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=20),
):
    """
    Returns personalized strategy recommendations based on:
    - User's subscribed categories
    - Trending strategies
    - High-rated strategies
    """
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        return {"recommendations": [], "total": 0}
    
    try:
        # Get user's subscription history to infer preferences
        sub_resp = (
            svc.table("library_subscriptions")
            .select("library_id")
            .eq("user_id", user_id)
            .execute()
        )
        
        subscribed_ids = [s["library_id"] for s in (sub_resp.data or [])]
        
        # Get categories of subscribed strategies
        preferred_categories = set()
        if subscribed_ids:
            cat_resp = (
                svc.table("library_strategies")
                .select("category")
                .in_("id", subscribed_ids)
                .execute()
            )
            for s in (cat_resp.data or []):
                preferred_categories.add(s.get("category"))
        
        # Query strategies matching preferred categories or trending, through the explicit
        # allow-list column list (task 16.2).
        if preferred_categories:
            resp = (
                svc.table("library_strategies")
                .select(_listing_projection.LISTING_SELECT)
                .eq("is_active", True)
                .in_("moderation_status", ["approved", "featured"])
                .in_("category", list(preferred_categories))
                .order("avg_rating", desc=True)
                .limit(limit)
                .execute()
            )
        else:
            # No history, return trending
            resp = (
                svc.table("library_strategies")
                .select(_listing_projection.LISTING_SELECT)
                .eq("is_active", True)
                .in_("moderation_status", ["approved", "featured"])
                .order("clone_count", desc=True)
                .limit(limit)
                .execute()
            )
    except MarketplaceError:
        # The subscription/category preamble reads may already raise a structured error; let it
        # surface unchanged rather than swallowing it into a zero-filled body.
        raise
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never a zero-filled 200
        # (Requirements 1.5, 1.7).
        logger.error(f"Recommendations DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data or []

    # Filter out already-subscribed strategies on the raw rows (by `id`), before projection —
    # the projected card carries `listing_id`, never `id`.
    candidate_rows = [
        r for r in rows if isinstance(r, dict) and r.get("id") not in subscribed_ids
    ]

    # Batched alias read + per-row projection through the one public serialiser.
    alias_by_author_id = _page_alias_map(candidate_rows, svc)
    recommendations = []
    for row in candidate_rows:
        author_id = row.get("author_id")
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            recommendations.append(
                _listing_projection.project_listing(row, None, creator_alias)
            )
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Recommendations projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return {"recommendations": recommendations[:limit], "total": len(recommendations)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/compare — Compare multiple strategies
# ─────────────────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    library_ids: List[str] = Field(..., min_items=2, max_items=5)

@literal_router.post("/compare")
async def compare_strategies(
    payload: CompareRequest,
    user: dict = Depends(get_current_user),
):
    """Compare multiple marketplace strategies side by side."""
    lib_ids = [_safe_uuid(lid, f"library_id_{i}") for i, lid in enumerate(payload.library_ids)]
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    try:
        resp = (
            svc.table("library_strategies")
            .select(_listing_projection.LISTING_SELECT)
            .in_("id", lib_ids)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never a bare 500
        # (Requirements 1.5, 1.7).
        logger.error(f"Compare strategies DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data or []

    # Batched alias read + per-row projection through the one public serialiser.
    alias_by_author_id = _page_alias_map(rows, svc)
    strategies = []
    for row in rows:
        author_id = row.get("author_id") if isinstance(row, dict) else None
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            strategies.append(
                _listing_projection.project_listing(row, None, creator_alias)
            )
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Compare projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return {"strategies": strategies, "total": len(strategies)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/favorite — Favorite a strategy
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{library_id}/favorite")
async def favorite_strategy(
    library_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Mark a strategy as favorite.
    Uses library_ratings table with rating=null to track favorites.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    now_ts = datetime.now(timezone.utc).isoformat()
    
    try:
        # Upsert favorite marker
        svc.table("library_ratings").upsert(
            {
                "library_id": lib_id,
                "user_id": user_id,
                "rating": None,  # Null means favorite without rating
                "review_text": None,
                "is_verified_clone": False,
                "created_at": now_ts,
                "updated_at": now_ts,
            },
            on_conflict="library_id,user_id",
        ).execute()
    except Exception as exc:
        logger.error(f"Favorite strategy error: {exc}")
        raise HTTPException(500, "Failed to favorite strategy")
    
    return {"status": "favorited", "library_id": lib_id}


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /api/library/{library_id}/favorite — Unfavorite a strategy
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/{library_id}/favorite")
async def unfavorite_strategy(
    library_id: str,
    user: dict = Depends(get_current_user),
):
    """Remove a strategy from favorites."""
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    try:
        # Delete favorite marker (only if rating is null - pure favorite)
        svc.table("library_ratings").delete().eq("library_id", lib_id).eq("user_id", user_id).is_("rating", None).execute()
    except Exception as exc:
        logger.error(f"Unfavorite strategy error: {exc}")
        raise HTTPException(500, "Failed to unfavorite strategy")
    
    return {"status": "unfavorited", "library_id": lib_id}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/favorites — Get user's favorites
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/favorites")
async def get_user_favorites(
    user: dict = Depends(get_current_user),
    limit: int = Query(20, ge=1, le=50),
):
    """Returns the user's favorited strategies."""
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        return {"favorites": [], "total": 0}
    
    try:
        # Get favorite library_ids
        fav_resp = (
            svc.table("library_ratings")
            .select("library_id")
            .eq("user_id", user_id)
            .is_("rating", None)
            .execute()
        )

        fav_ids = [f["library_id"] for f in (fav_resp.data or [])]

        if not fav_ids:
            # A genuinely empty favourites set — the read completed and returned nothing. This
            # is not a swallowed failure; the empty list is the honest answer.
            return {"favorites": [], "total": 0}

        # Fetch strategy details through the explicit allow-list column list (task 16.2).
        resp = (
            svc.table("library_strategies")
            .select(_listing_projection.LISTING_SELECT)
            .in_("id", fav_ids)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never a zero-filled 200
        # (Requirements 1.5, 1.7).
        logger.error(f"Favorites DB error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data or []

    # Batched alias read + per-row projection through the one public serialiser.
    alias_by_author_id = _page_alias_map(rows, svc)
    items = []
    for row in rows:
        author_id = row.get("author_id") if isinstance(row, dict) else None
        creator_alias = alias_by_author_id.get(author_id) if author_id else None
        try:
            items.append(_listing_projection.project_listing(row, None, creator_alias))
        except (ValueError, _money.MoneyError) as exc:
            logger.error(f"Favorites projection error for {row.get('id')}: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return {"favorites": items, "total": len(items)}


# ═════════════════════════════════════════════════════════════════════════════
# Marketplace submission + admin review routes (task 14.4)
#
# Requirements 2.12, 4.8, 4.9, 4.10, 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 5.9, 5.10,
# 22.1, 22.2, 22.4, 22.10.
#
# Every route below is declared on `literal_router` (task 13.1's literal-segment
# router) so its literal first segment — `/submissions`, `/admin/submissions` —
# is matched before the parameterised `@router.get("/{library_id}")`. The splice
# `router.routes[:0] = literal_router.routes` at the foot of this module puts them
# in front of the parameterised routes at include time.
#
# The pure marketplace modules (`eligibility_gate`, `submission_service`) import
# no FastAPI and receive the Supabase handle by injection; this route layer is the
# FastAPI boundary that owns HTTP. A pure module signals failure with a code string
# (`SubmissionServiceError.code`) or a verdict; this layer maps those onto
# `MarketplaceError`, whose registered exception handler (main.py) renders the
# `{"error": {code, message, details}, "request_id"}` body and the catalogue's HTTP
# status. No status code or public sentence is spelled here — the catalogue is the
# one authority.
# ═════════════════════════════════════════════════════════════════════════════

#: A service code that names the same failure under a different spelling than the
#: error catalogue does gets mapped here before it reaches ``MarketplaceError`` —
#: otherwise the ``StructuredError`` constructor would reject the unknown code. The
#: only such case is the rejection-reason rule: the service raises
#: ``MARKETPLACE_REASON_REQUIRED`` (its own constant) for the catalogue's
#: ``MARKETPLACE_REJECTION_REASON_REQUIRED`` (Requirements 4.8, 5.10).
_SERVICE_CODE_TO_CATALOGUE = {
    "MARKETPLACE_REASON_REQUIRED": MARKETPLACE_REJECTION_REASON_REQUIRED,
}


def _raise_from_service_error(exc: "_submission_service.SubmissionServiceError"):
    """Re-raise a ``SubmissionServiceError`` as the matching ``MarketplaceError``.

    The catalogue (``errors.py``) is the single authority for the HTTP status and the
    public sentence per code, so this maps by ``code`` and lets ``MarketplaceError``
    resolve the rest. ``details`` is forwarded unchanged — it already carries only the
    caller's own values (the current/rejected state on a transition refusal, the
    ``source_strategy_id`` on an already-open conflict) and never a database string,
    query or internal path. ``message`` is deliberately NOT forwarded so the catalogue's
    scrubbed sentence is used rather than the service's internal wording.
    """
    catalogue_code = _SERVICE_CODE_TO_CATALOGUE.get(exc.code, exc.code)
    raise MarketplaceError(catalogue_code, details=exc.details or {}) from exc


class SubmissionCreateRequest(BaseModel):
    """The owner's publication request: a strategy and its 3..10 Backtest_Evidence refs.

    The Eligibility_Gate ignores every eligibility/validation/score/status value in the
    body (Requirement 2.1); the only fields that matter are which strategy and which
    backtest runs. ``backtest_ids`` is bounded 3..10 here to match Requirement 3.1's
    Backtest_Condition count — the gate re-checks it server-side regardless.
    """

    strategy_id: UUID
    backtest_ids: List[UUID] = Field(..., min_items=3, max_items=10)


class AdminActionRequest(BaseModel):
    """The optional body for an admin action. Only ``reject`` reads ``reason``."""

    reason: Optional[str] = Field(None, max_length=2000)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/submissions — create a Submission (owner)
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.post("/submissions", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/60second", key_func=caller_or_address)
@limiter.limit("10/60second")
async def create_submission_route(
    request: Request,
    payload: SubmissionCreateRequest,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_publish),
):
    """Evaluate eligibility and, on admission, create the Submission (Requirements 2.12, 2.13).

    ``require_marketplace_publish`` is the STRATEGY_SHARING / publish entitlement gate: a
    caller lacking it is refused 403 before any read (Requirement 2.9), which is why the
    gate itself does not re-check it. Rate-limited 10/60s (design § Security design).

    The verdict drives the outcome:
      * admitted            → read the admitted backtest rows + the Listing, then
                              ``submission_service.create_submission`` (Requirement 2.12).
      * not admitted, not
        unevaluable         → 422 ``MARKETPLACE_ELIGIBILITY_FAILED`` carrying EVERY failed
                              criterion code (Requirement 2.10), no Submission row.
      * unevaluable         → 503 ``MARKETPLACE_ELIGIBILITY_UNEVALUABLE`` (Requirement 2.13),
                              distinct by code from a criteria failure, no Submission row.
    """
    user_id = _safe_uuid(user["id"], "user_id")
    strategy_id = _safe_uuid(str(payload.strategy_id), "strategy_id")
    backtest_ids = [_safe_uuid(str(b), "backtest_id") for b in payload.backtest_ids]
    svc = _build_service_client()

    caller = {"id": user_id, "tenant_id": user.get("tenant_id")}

    # ── The Eligibility_Gate decides; this layer only maps the verdict ──
    verdict = await _eligibility_gate.evaluate(caller, strategy_id, backtest_ids, svc)

    if verdict.unevaluable:
        # A read did not complete: eligibility is *unknown*, a 503, not a 422 "not eligible".
        raise MarketplaceError(MARKETPLACE_ELIGIBILITY_UNEVALUABLE)

    if not verdict.admitted:
        # Requirement 2.10: one response naming EVERY failed criterion, not the first.
        failures = [o.code for o in verdict.failed_outcomes]
        raise MarketplaceError(
            MARKETPLACE_ELIGIBILITY_FAILED,
            details={"failures": failures},
        )

    # ── Admitted: gather what create_submission persists, then create it ──
    # The Listing the Submission references is the owner's existing library_strategies
    # row for this source strategy. The evidence rows are re-read owner-scoped with the
    # exact columns the immutable evidence copy needs (submission_service._EVIDENCE_SOURCE_SELECT),
    # so the copy carries every parameter and metric and no blueprint/logic column.
    try:
        listing_resp = (
            svc.table("library_strategies")
            .select("id")
            .eq("source_strategy_id", strategy_id)
            .limit(1)
            .execute()
        )
        listing_rows = listing_resp.data or []
        listing_id = listing_rows[0]["id"] if listing_rows else None

        evidence_resp = (
            svc.table("strategy_backtests")
            .select(_submission_service._EVIDENCE_SOURCE_SELECT)
            .in_("id", backtest_ids)
            .eq("user_id", user_id)
            .execute()
        )
        backtest_rows = evidence_resp.data or []
    except Exception as exc:
        logger.error(f"create_submission_route evidence read error: {exc}")
        # A read that did not complete after admission is the same unknown-eligibility
        # condition Requirement 2.13 answers with UNEVALUABLE — no Submission is created.
        raise MarketplaceError(MARKETPLACE_ELIGIBILITY_UNEVALUABLE)

    try:
        submission = await _submission_service.create_submission(
            caller=caller,
            listing_id=listing_id,
            source_strategy_id=strategy_id,
            version_id=verdict.version_id,
            backtest_rows=backtest_rows,
            eligibility_outcomes=verdict.outcomes,
            evaluator_version=verdict.evaluator_version,
            supabase=svc,
        )
    except _submission_service.SubmissionServiceError as exc:
        _raise_from_service_error(exc)

    # Requirement 2.12: return the created Submission identifier to the owner.
    return {
        "submission_id": submission.get("id"),
        "submission_state": submission.get("submission_state"),
        "message": "Submission created and advanced to SUBMITTED.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/admin/submissions — list (LITERAL path; declared before the
# parameterised submission routes so the literal-first ordering Task 13.1 asserts
# via tests/test_library_route_resolution.py holds across the whole router table)
# ─────────────────────────────────────────────────────────────────────────────

#: Requirement 5.2's pagination contract: page size 1..100, default 25, clamped to 100.
_ADMIN_PAGE_SIZE_DEFAULT = 25
_ADMIN_PAGE_SIZE_MAX = 100


@literal_router.get("/admin/submissions")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("120/60second")
async def admin_list_submissions(
    request: Request,
    state: Optional[List[str]] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(_ADMIN_PAGE_SIZE_DEFAULT, ge=1),
    admin: dict = Depends(get_admin_user),
):
    """List Submissions for an Admin_Reviewer (Requirements 5.1, 5.2).

    Filterable by one or more Submission_State values (repeat ``?state=`` per value); when
    none is supplied ALL states are returned. Ordered ``submitted_at DESC, id DESC`` — the
    exact key of ``idx_submissions_state`` (007). ``page_size`` is clamped to 100 when a
    larger value is requested rather than rejected, and ``total`` — the count of matching
    Submissions — is returned with each page. ``get_admin_user`` is the only authority; no
    role, flag or capability is read from the client (Requirement 5.1).
    """
    effective_page_size = min(page_size, _ADMIN_PAGE_SIZE_MAX)
    offset = (page - 1) * effective_page_size
    svc = _build_service_client()

    try:
        query = (
            svc.table("marketplace_submissions")
            .select(
                "id, listing_id, source_strategy_id, owner_id, version_id, "
                "submission_state, evaluator_version, submitted_at, reviewed_at, "
                "published_at, created_at, updated_at",
                count="exact",
            )
        )
        # None => all states (Requirement 5.2). An explicit, non-empty list filters.
        requested_states = [s for s in (state or []) if s]
        if requested_states:
            query = query.in_("submission_state", requested_states)

        resp = (
            query
            .order("submitted_at", desc=True)
            .order("id", desc=True)
            .range(offset, offset + effective_page_size - 1)
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never
        # MARKETPLACE_SUBMISSION_NOT_FOUND. The 404 that used to be raised here told an
        # Admin_Reviewer the queue holds nothing — a claim about the queue's contents drawn from
        # a query that never returned, and one that reads identically to a genuinely empty
        # queue (Requirements 1.5, 1.7).
        logger.error(f"admin_list_submissions read error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    items = resp.data if resp is not None else None
    if items is None:
        logger.error("admin_list_submissions read unreadable")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)
    total = getattr(resp, "count", None)
    if total is None:
        total = len(items)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": effective_page_size,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/library/{library_id}/settings — Owner toggles source_cloning_enabled
#
# The owner's consent switch for the `clone_strategy` gate (Requirements 7.2, 22.4, 30.5;
# design.md clone_strategy redesign — "The owner toggles it through PATCH
# /api/library/{library_id}/settings {source_cloning_enabled}, owner-scoped, rate-limited,
# audited"; design.md endpoint table — "owner | as above | 20/60s | idempotent |
# single-column update").
#
# WHY IT IS DECLARED ON `literal_router`
# --------------------------------------
# `router.routes[:0] = literal_router.routes` at the bottom of this module splices every
# `literal_router` route in front of every `router` route, so a route declared here can never
# be swallowed by a parameterised route on `router` — including `GET/DELETE /{library_id}`
# and any future `/{library_id}/…` catch-all. The literal `settings` segment therefore stays
# reachable by construction rather than by luck of decorator ordering.
#
# WHY IT SITS AT THIS EXACT POINT IN THE FILE
# -------------------------------------------
# `literal_router` is ordered: every path-parameter-free route first, then the routes that
# carry a path parameter (the `/submissions/{submission_id}` family below). Task 13.1's
# structural guard — `tests/test_library_route_resolution.py::
# test_literal_routes_precede_parameterised_routes_in_the_table` — asserts that in the spliced
# table every literal path precedes every parameterised one. Declaring this route here, at the
# head of `literal_router`'s parameterised tail, keeps that invariant exactly as it is while
# still putting the route ahead of everything on `router`.
#
# Owner-scoped with no existence oracle: a non-owner (or an absent listing) gets the identical
# 404 an unknown listing gets, so the endpoint never leaks whether the listing exists
# (design.md endpoint table — "other owner's → 404 identical"). The change is recorded in one
# audit entry that carries no Protected_Logic (only the boolean and the listing id).
# ─────────────────────────────────────────────────────────────────────────────


class LibrarySettingsRequest(BaseModel):
    """The single owner-controlled setting this endpoint updates."""

    source_cloning_enabled: bool = Field(
        ..., description="Whether non-owner subscribers may clone this listing's strategy."
    )


@literal_router.patch("/{library_id}/settings", status_code=status.HTTP_200_OK)
@limiter.limit("20/60second", key_func=caller_or_address)
@limiter.limit("20/60second")
async def update_library_settings(
    request: Request,
    library_id: str,
    payload: LibrarySettingsRequest,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_access),
):
    """Owner-scoped toggle of ``source_cloning_enabled`` on a Listing."""
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # Read the listing and its owner. A read that did not complete is the structured
    # MARKETPLACE_READ_FAILED, never a fabricated success (Requirements 1.5, 1.7).
    try:
        resp = (
            svc.table("library_strategies")
            .select("id, author_id, source_cloning_enabled")
            .eq("id", lib_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"update_library_settings lookup error for {lib_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    entry = getattr(resp, "data", None)
    # Absent listing OR another owner's listing → the SAME 404, so ownership is enforced
    # without an existence oracle (design.md endpoint table — "other owner's → 404 identical").
    if not entry or str(entry.get("author_id")) != str(user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library strategy not found.",
        )

    before_value = bool(entry.get("source_cloning_enabled"))
    after_value = bool(payload.source_cloning_enabled)
    now_ts = datetime.now(timezone.utc).isoformat()

    # Single-column update (idempotent — writing the same value is a no-op the client cannot
    # tell from a change).
    try:
        svc.table("library_strategies").update(
            {"source_cloning_enabled": after_value, "updated_at": now_ts}
        ).eq("id", lib_id).execute()
    except Exception as exc:
        logger.error(f"update_library_settings update error for {lib_id}: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # Audit the owner's configuration change. No dedicated settings action exists in
    # StrategyAuditAction; MARKETPLACE_ADMIN_ACTION is the closest fit — the owner acting on
    # their own listing's configuration — and the before/after values are recorded in
    # metadata. The record carries no Protected_Logic, only the boolean and the listing id.
    try:
        await get_strategy_audit_logger().log(
            StrategyAuditAction.MARKETPLACE_ADMIN_ACTION,
            actor_id=str(user_id),
            resource_type="strategy",
            resource_id=str(lib_id),
            reason="library_settings_updated",
            before=str(before_value),
            after=str(after_value),
            metadata={
                "operation": "update_settings",
                "listing_id": str(lib_id),
                "source_cloning_enabled": after_value,
            },
        )
    except Exception as audit_exc:
        logger.error(f"update_library_settings audit error for {lib_id}: {audit_exc}")

    logger.info(
        f"Library settings updated: library_id={lib_id} source_cloning_enabled={after_value} "
        f"by user={user_id}"
    )

    return {
        "library_id": str(lib_id),
        "source_cloning_enabled": after_value,
        "message": "Listing settings updated.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/submissions/{submission_id} — own Submission only
# (parameterised paths follow; every literal-path route is declared above)
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.get("/submissions/{submission_id}")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("120/60second")
async def get_own_submission(
    request: Request,
    submission_id: str,
    user: dict = Depends(get_current_user),
):
    """Return the caller's own Submission; another owner's is answered as unknown.

    The read is scoped to the caller's ``owner_id`` in the query itself. When it returns
    no row — whether the Submission is absent OR belongs to another owner — the same
    404 ``MARKETPLACE_SUBMISSION_NOT_FOUND`` body is returned and NO second, unscoped read
    is performed, so the response cannot be used as an existence oracle for another owner's
    Submission (Requirement 21.4, design § ownership indistinguishability). No blueprint or
    logic field is selected.
    """
    sub_id = _safe_uuid(submission_id, "submission_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    try:
        resp = (
            svc.table("marketplace_submissions")
            .select(
                "id, listing_id, source_strategy_id, owner_id, version_id, "
                "submission_state, eligibility_outcomes, evaluator_version, "
                "rejection_reason, reviewed_by, submitted_at, reviewed_at, "
                "published_at, created_at, updated_at"
            )
            .eq("id", sub_id)
            .eq("owner_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        # A read that did not complete is MARKETPLACE_READ_FAILED, never the 404 below. The two
        # answers mean opposite things: the 404 states that no Submission with this identifier
        # belongs to the caller, which a query that never returned cannot establish
        # (Requirements 1.5, 1.7).
        logger.error(f"get_own_submission read error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    rows = resp.data if resp is not None else None
    if rows is None:
        logger.error("get_own_submission read unreadable")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)
    if not rows:
        # Absent OR another owner's — identical answer, no second read (Requirement 21.4). This
        # branch is reached only for a read that COMPLETED, so the 404 remains the single shape
        # for both of its causes and no cross-tenant probe has become distinguishable.
        raise MarketplaceError(_submission_service.MARKETPLACE_SUBMISSION_NOT_FOUND)

    return {"submission": rows[0]}


# ─────────────────────────────────────────────────────────────────────────────
# Server-side pricing enforcement (task 15.1, Requirements 8.8–8.12, 8.14, 8.15,
# 22.2, 22.4).
#
# Two POST routes on the caller's OWN Submission. Both are declared on
# `literal_router` so their literal first segment `/submissions` is matched before
# the parameterised `@router.get("/{library_id}")` — the splice
# `router.routes[:0] = literal_router.routes` at the foot of this module keeps the
# literal-first ordering tests/test_library_route_resolution.py asserts.
#
# Ownership is scoped exactly as `get_own_submission` (task 14.4): the Submission is
# read with `.eq("owner_id", user_id)` in the query itself, and a foreign or absent
# Submission is answered with the same MARKETPLACE_SUBMISSION_NOT_FOUND body with no
# second, unscoped read — so neither route can be used as an existence oracle for
# another owner's Submission (Requirement 21.4).
# ─────────────────────────────────────────────────────────────────────────────

#: The `marketplace_backtest_evidence` columns `pricing_evaluator` reads (its
#: `PRICING_INPUTS` and `DIGEST_INPUTS`, on their canonical spellings — the immutable
#: evidence copy uses `max_drawdown_pct`, `win_rate_pct` and `source_backtest_id`, so no
#: alias is needed). Bound by the `_PRICING_HANDLER` entry of
#: `backend_app.backend.marketplace.COLUMN_CONTRACT`.
_PRICE_EVIDENCE_SELECT = (
    "source_backtest_id, condition_index, "
    "total_return_pct, sharpe_ratio, sortino_ratio, max_drawdown_pct, "
    "win_rate_pct, profit_factor, total_trades, start_date, end_date, "
    "dataset_checksum, dag_hash, executed_bar_count, final_capital"
)


async def _load_own_submission_for_pricing(sub_id: str, user_id: str, svc):
    """Read the caller's own Submission, or answer as unknown (Requirement 21.4).

    Owner-scoped in the query itself; an absent OR foreign Submission raises the same
    ``MARKETPLACE_SUBMISSION_NOT_FOUND`` with no second, unscoped read. Returns the
    Submission row (its ``id`` and ``listing_id``).
    """
    try:
        resp = (
            svc.table("marketplace_submissions")
            .select("id, listing_id, owner_id")
            .eq("id", sub_id)
            .eq("owner_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.error(f"pricing submission read error: {exc}")
        raise MarketplaceError(MARKETPLACE_SUBMISSION_NOT_FOUND)

    rows = resp.data or []
    if not rows:
        # Absent OR another owner's — identical answer, no second read (Requirement 21.4).
        raise MarketplaceError(MARKETPLACE_SUBMISSION_NOT_FOUND)
    return rows[0]


def _load_pricing_conditions(sub_id: str, user_id: str, svc):
    """Read the immutable Backtest_Evidence rows for the Submission, owner-scoped.

    The conditions are the ``marketplace_backtest_evidence`` copy (Requirement 3.9) — the
    figures ``pricing_evaluator`` reads — ordered by ``condition_index`` for a stable read.
    A read failure is a Persistence_Layer failure, not a missing-evidence condition, so it
    surfaces as ``MARKETPLACE_READ_FAILED`` rather than as a fabricated empty range.
    """
    try:
        resp = (
            svc.table("marketplace_backtest_evidence")
            .select(_PRICE_EVIDENCE_SELECT)
            .eq("submission_id", sub_id)
            .eq("owner_id", user_id)
            .order("condition_index")
            .execute()
        )
    except Exception as exc:
        logger.error(f"pricing evidence read error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)
    return resp.data or []


def _price_range_or_error(conditions, currency: str):
    """Compute the Price_Range, mapping the evaluator's refusals to the catalogue.

    ``MissingEvidenceInputs`` → 422 ``MARKETPLACE_PRICE_EVIDENCE_MISSING`` carrying the
    sorted ``details["missing"]`` list (Requirement 8.14). ``UnsupportedCurrency`` and
    ``InvalidEvidenceInput`` are the same class of unusable evidence/currency and surface as
    the same 422 with the missing/currency detail, never as a 500.
    """
    try:
        return _pricing_evaluator.price_range(conditions, currency)
    except _pricing_evaluator.MissingEvidenceInputs as exc:
        raise MarketplaceError(
            MARKETPLACE_PRICE_EVIDENCE_MISSING,
            details={"missing": list(exc.names)},
        )
    except _pricing_evaluator.UnsupportedCurrency:
        raise MarketplaceError(
            MARKETPLACE_PRICE_EVIDENCE_MISSING,
            details={"missing": [], "currency": currency},
        )
    except _pricing_evaluator.InvalidEvidenceInput:
        raise MarketplaceError(
            MARKETPLACE_PRICE_EVIDENCE_MISSING,
            details={"missing": []},
        )


class PriceRangeRequest(BaseModel):
    """Body for POST /submissions/{id}/price-range — the currency to evaluate in."""

    currency: str = Field(..., min_length=3, max_length=8)


class SetPriceRequest(BaseModel):
    """Body for POST /submissions/{id}/price — the price to set and its currency."""

    price_minor: int = Field(..., ge=1, le=_pricing_evaluator.ABSOLUTE_MAX_MINOR)
    currency: str = Field(..., min_length=3, max_length=8)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/submissions/{submission_id}/price-range — compute + persist
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.post("/submissions/{submission_id}/price-range")
@limiter.limit("30/60second", key_func=caller_or_address)
@limiter.limit("30/60second")
async def submission_price_range(
    request: Request,
    submission_id: str,
    payload: PriceRangeRequest,
    user: dict = Depends(get_current_user),
):
    """Compute the Price_Range for the caller's Submission and persist the evaluation.

    Rate-limited 30/60s per authenticated caller (Requirement 8.15); a rate-limited request
    is refused by the limiter before this body runs, so it computes nothing and persists
    nothing. On success the ``Price_Range`` is computed from the immutable Backtest_Evidence
    (``pricing_evaluator.price_range``), a ``marketplace_price_evaluations`` row is persisted
    carrying the ``inputs_digest``, ``evaluator_version`` and a timestamp, and the
    minimum/recommended/maximum minor triple is returned (Requirements 8.1, 8.8, 8.12).

    Ownership is scoped to the caller; a foreign or absent Submission is answered as unknown
    (Requirement 21.4). Missing pricing inputs surface as 422
    ``MARKETPLACE_PRICE_EVIDENCE_MISSING`` and persist nothing (Requirement 8.14).
    """
    sub_id = _safe_uuid(submission_id, "submission_id")
    user_id = _safe_uuid(user["id"], "user_id")
    currency = payload.currency
    svc = _build_service_client()

    await _load_own_submission_for_pricing(sub_id, user_id, svc)
    conditions = _load_pricing_conditions(sub_id, user_id, svc)

    price_range = _price_range_or_error(conditions, currency)
    digest = _pricing_evaluator.inputs_digest(conditions)

    now_ts = datetime.now(timezone.utc).isoformat()
    try:
        svc.table("marketplace_price_evaluations").insert(
            {
                "submission_id": sub_id,
                "owner_id": user_id,
                "currency": currency,
                "inputs_digest": digest,
                "minimum_price_minor": price_range.minimum_price_minor,
                "recommended_price_minor": price_range.recommended_price_minor,
                "maximum_price_minor": price_range.maximum_price_minor,
                "evaluator_version": _pricing_evaluator.EVALUATOR_VERSION,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
        ).execute()
    except Exception as exc:
        logger.error(f"price-range persist error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return {
        "submission_id": sub_id,
        "currency": currency,
        "minimum_price_minor": price_range.minimum_price_minor,
        "recommended_price_minor": price_range.recommended_price_minor,
        "maximum_price_minor": price_range.maximum_price_minor,
        "evaluator_version": _pricing_evaluator.EVALUATOR_VERSION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/submissions/{submission_id}/price — the single enforcement point
# ─────────────────────────────────────────────────────────────────────────────

@literal_router.post("/submissions/{submission_id}/price")
@limiter.limit("30/60second", key_func=caller_or_address)
@limiter.limit("30/60second")
async def submission_set_price(
    request: Request,
    submission_id: str,
    payload: SetPriceRequest,
    user: dict = Depends(get_current_user),
):
    """Set the Listing price for the caller's Submission — the SINGLE enforcement point.

    The persisted evaluation for ``(submission_id, currency, inputs_digest)`` is looked up;
    when the current evidence digest does not match a persisted row it is recomputed and
    persisted (Requirement 8.8). ``price_minor`` is accepted only when
    ``minimum <= price_minor <= maximum``; otherwise 400 ``MARKETPLACE_PRICE_OUT_OF_RANGE``
    is raised carrying the permitted range and NO Listing price change is made
    (Requirements 8.9, 8.10). On accept the Submission's Listing has its ``price_minor`` set
    (the section-7 trigger mirrors ``price`` from it — Requirement 8.12).

    Ownership is scoped to the caller; a foreign or absent Submission is answered as unknown
    (Requirement 21.4).
    """
    sub_id = _safe_uuid(submission_id, "submission_id")
    user_id = _safe_uuid(user["id"], "user_id")
    currency = payload.currency
    price_minor = payload.price_minor
    svc = _build_service_client()

    submission = await _load_own_submission_for_pricing(sub_id, user_id, svc)
    listing_id = submission.get("listing_id")

    # The digest of the CURRENT evidence — the thing the persisted evaluation must match.
    conditions = _load_pricing_conditions(sub_id, user_id, svc)
    digest = _pricing_evaluator.inputs_digest(conditions)

    # Look up the most recent evaluation for exactly (submission_id, currency, digest).
    persisted = None
    try:
        eval_resp = (
            svc.table("marketplace_price_evaluations")
            .select(
                "minimum_price_minor, recommended_price_minor, maximum_price_minor, "
                "inputs_digest, currency"
            )
            .eq("submission_id", sub_id)
            .eq("currency", currency)
            .eq("inputs_digest", digest)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        eval_rows = eval_resp.data or []
        persisted = eval_rows[0] if eval_rows else None
    except Exception as exc:
        logger.error(f"set_price evaluation lookup error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    if persisted is not None:
        minimum = int(persisted["minimum_price_minor"])
        maximum = int(persisted["maximum_price_minor"])
    else:
        # The digest did not match a persisted row: recompute from the current evidence and
        # persist it, so the range the price is checked against is provably the range of the
        # evidence now on the table (Requirement 8.8).
        price_range = _price_range_or_error(conditions, currency)
        minimum = price_range.minimum_price_minor
        maximum = price_range.maximum_price_minor
        now_ts = datetime.now(timezone.utc).isoformat()
        try:
            svc.table("marketplace_price_evaluations").insert(
                {
                    "submission_id": sub_id,
                    "owner_id": user_id,
                    "currency": currency,
                    "inputs_digest": digest,
                    "minimum_price_minor": price_range.minimum_price_minor,
                    "recommended_price_minor": price_range.recommended_price_minor,
                    "maximum_price_minor": price_range.maximum_price_minor,
                    "evaluator_version": _pricing_evaluator.EVALUATOR_VERSION,
                    "created_at": now_ts,
                    "updated_at": now_ts,
                }
            ).execute()
        except Exception as exc:
            logger.error(f"set_price recompute persist error: {exc}")
            raise MarketplaceError(MARKETPLACE_READ_FAILED)

    # The one accept/reject decision. On reject NOTHING is written (Requirement 8.10).
    if not (minimum <= price_minor <= maximum):
        raise MarketplaceError(
            MARKETPLACE_PRICE_OUT_OF_RANGE,
            details={
                "minimum_minor": minimum,
                "maximum_minor": maximum,
                "currency": currency,
            },
        )

    # Accepted: set the Listing's price_minor; the section-7 trigger mirrors price from it.
    if listing_id is None:
        # A Submission with no Listing has nothing to price; treat it as unknown rather than
        # inventing a target (Requirement 21.4 — same indistinguishable answer).
        raise MarketplaceError(MARKETPLACE_SUBMISSION_NOT_FOUND)

    try:
        svc.table("library_strategies").update(
            {
                "price_minor": price_minor,
                "currency": currency,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", listing_id).execute()
    except Exception as exc:
        logger.error(f"set_price listing update error: {exc}")
        raise MarketplaceError(MARKETPLACE_READ_FAILED)

    return {
        "submission_id": sub_id,
        "listing_id": listing_id,
        "price_minor": price_minor,
        "currency": currency,
        "minimum_minor": minimum,
        "maximum_minor": maximum,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Admin review surface — the parameterised admin routes (Requirement 5)
# Every route carries get_admin_user; the literal `/admin/submissions` list is
# declared above with the other literal-path routes.
# ─────────────────────────────────────────────────────────────────────────────


@literal_router.get("/admin/submissions/{submission_id}")
@limiter.limit("120/60second", key_func=caller_or_address)
@limiter.limit("120/60second")
async def admin_submission_detail(
    request: Request,
    submission_id: str,
    admin: dict = Depends(get_admin_user),
):
    """The Admin_Reviewer detail for one Submission (Requirements 5.3, 5.8).

    Delegates wholly to ``submission_service.admin_detail``, which reads the immutable
    ``marketplace_backtest_evidence`` copy — never a blueprint or logic column — so no
    Protected_Logic can be exposed here (Requirement 5.8). A missing Submission surfaces as
    the service's ``MARKETPLACE_SUBMISSION_NOT_FOUND``. Admin authorisation is global by
    design and carried solely by ``get_admin_user``.
    """
    sub_id = _safe_uuid(submission_id, "submission_id")
    svc = _build_service_client()
    try:
        detail = await _maybe_await(
            _submission_service.admin_detail(sub_id, supabase=svc)
        )
    except _submission_service.SubmissionServiceError as exc:
        _raise_from_service_error(exc)
    return detail


async def _apply_admin_action_route(
    submission_id: str,
    action: "_submission_service.SubmissionAction",
    reason: Optional[str],
    admin: dict,
):
    """Shared body for the five admin action routes (Requirement 5.4).

    Each route resolves to exactly one ``SubmissionAction`` and calls
    ``submission_service.apply_admin_action``, which reads the Submission ``FOR UPDATE``,
    checks ``can_transition`` against the state read inside the transaction, validates the
    reason for ``reject``, writes the transition and audits it atomically (Requirements 5.6,
    5.9, 5.10, 5.11). Every failure path is a ``SubmissionServiceError`` this layer maps to
    the catalogue: an unknown Submission → 404, an illegal edge → 409 naming the rejected
    transition, a missing reason → 422, an unrecorded action → 500. Authorisation is
    ``get_admin_user`` only (Requirements 5.1, 5.7).
    """
    sub_id = _safe_uuid(submission_id, "submission_id")
    admin_id = _safe_uuid(admin["id"], "admin_id")
    svc = _build_service_client()
    try:
        result = await _submission_service.apply_admin_action(
            {"id": admin_id},
            sub_id,
            action,
            reason,
            supabase=svc,
        )
    except _submission_service.SubmissionServiceError as exc:
        _raise_from_service_error(exc)
    return {
        "submission_id": result.get("id"),
        "submission_state": result.get("submission_state"),
    }


@literal_router.post("/admin/submissions/{submission_id}/approve")
@limiter.limit("60/60second", key_func=caller_or_address)
@limiter.limit("60/60second")
async def admin_approve_submission(
    request: Request,
    submission_id: str,
    admin: dict = Depends(get_admin_user),
):
    """``SUBMITTED → UNDER_REVIEW → APPROVED`` (or ``UNDER_REVIEW → APPROVED``)."""
    return await _apply_admin_action_route(
        submission_id, _submission_service.SubmissionAction.APPROVE, None, admin
    )


@literal_router.post("/admin/submissions/{submission_id}/reject")
@limiter.limit("60/60second", key_func=caller_or_address)
@limiter.limit("60/60second")
async def admin_reject_submission(
    request: Request,
    submission_id: str,
    body: AdminActionRequest,
    admin: dict = Depends(get_admin_user),
):
    """``→ REJECTED`` with a trimmed 1..2000-char reason (Requirements 5.5, 5.10)."""
    return await _apply_admin_action_route(
        submission_id, _submission_service.SubmissionAction.REJECT, body.reason, admin
    )


@literal_router.post("/admin/submissions/{submission_id}/publish")
@limiter.limit("60/60second", key_func=caller_or_address)
@limiter.limit("60/60second")
async def admin_publish_submission(
    request: Request,
    submission_id: str,
    admin: dict = Depends(get_admin_user),
):
    """``APPROVED → PUBLISHED`` or ``SUSPENDED → PUBLISHED``."""
    return await _apply_admin_action_route(
        submission_id, _submission_service.SubmissionAction.PUBLISH, None, admin
    )


@literal_router.post("/admin/submissions/{submission_id}/suspend")
@limiter.limit("60/60second", key_func=caller_or_address)
@limiter.limit("60/60second")
async def admin_suspend_submission(
    request: Request,
    submission_id: str,
    admin: dict = Depends(get_admin_user),
):
    """``PUBLISHED → SUSPENDED``."""
    return await _apply_admin_action_route(
        submission_id, _submission_service.SubmissionAction.SUSPEND, None, admin
    )


@literal_router.post("/admin/submissions/{submission_id}/unpublish")
@limiter.limit("60/60second", key_func=caller_or_address)
@limiter.limit("60/60second")
async def admin_unpublish_submission(
    request: Request,
    submission_id: str,
    admin: dict = Depends(get_admin_user),
):
    """``PUBLISHED → UNPUBLISHED`` or ``SUSPENDED → UNPUBLISHED``."""
    return await _apply_admin_action_route(
        submission_id, _submission_service.SubmissionAction.UNPUBLISH, None, admin
    )


class AdminReinstateRequest(BaseModel):
    """The body for an administrative reinstatement. The reason is REQUIRED (Req 11.12).

    Unlike ``AdminActionRequest``, whose ``reason`` is optional because only ``reject`` reads
    it, this model makes the reason mandatory at the wire boundary: a reinstatement with no
    recorded reason is refused with 422 before the handler runs, and
    ``subscription_reinstatement.validate_reason`` refuses a whitespace-only one before any
    read. Two gates, one rule, and neither of them writes anything on refusal.
    """

    reason: str = Field(..., min_length=1, max_length=2000)


@literal_router.post("/admin/subscriptions/{subscription_id}/reinstate")
@limiter.limit("60/60second", key_func=caller_or_address)
@limiter.limit("60/60second")
async def admin_reinstate_subscription(
    request: Request,
    subscription_id: str,
    body: AdminReinstateRequest,
    admin: dict = Depends(get_admin_user),
):
    """Lift an administrative hold: `SUSPENDED → ACTIVE`, no charge, period unchanged (Req 11.17).

    **The defect this closes.** A `SUSPENDED` Subscription had no settlement-free route back to
    `ACTIVE`. Requirement 11.2 permits `SUSPENDED → ACTIVE` and 008 seeds that edge, but
    Requirement 11.6 does not list `SUSPENDED` among the sources that require a payment — the
    edge existed with no stated payment rule, and the implementation took the stricter reading,
    so the only route was a checkout. A purchaser suspended mid-month for an investigation that
    then cleared them had to buy the month they had already paid for.

    Requirement 11.17 resolves the contradiction: a suspension is a **hold, not a refund**, so
    lifting it restores the REMAINING period without a charge. `period_start`, `period_expiry`
    and the `started_at` / `expires_at` mirrors are not written; an already-elapsed period
    therefore hands back nothing, because `entitlement_resolver` compares `now` with
    `period_expiry` on every call (Requirement 11.7).

    Authorisation is `get_admin_user` and nothing else — the same dependency the five submission
    actions carry, so a non-admin gets the identical 403 before any read (Requirements 5.1,
    22.10). No role, capability or identifier is taken from the client; the acting identity on
    the Audit_Log entry is the session's (Requirement 21.1).

    Everything else is `subscription_reinstatement.reinstate`'s: the reason rule, the
    paid-period probe (the application half of the database guard's reinstatement arm), the
    one-column UPDATE, the `cause='admin_reinstatement'` history row and the audit entry that
    the transition is conditioned on (Requirements 11.12, 11.14). A refusal maps onto the error
    catalogue by code, exactly as the submission actions' refusals do.
    """
    sub_uid = _safe_uuid(subscription_id, "subscription_id")
    admin_id = _safe_uuid(admin["id"], "admin_id")
    svc = _build_service_client()
    try:
        result = await _subscription_reinstatement.reinstate(
            {"id": admin_id},
            sub_uid,
            body.reason,
            supabase=svc,
        )
    except _subscription_reinstatement.ReinstatementError as exc:
        _raise_from_service_error(exc)
    return {
        "subscription_id": result.subscription_id,
        "subscription_state": result.to_status,
        "prior_state": result.from_status,
        "period_start": result.period_start.isoformat() if result.period_start else None,
        "period_expiry": result.period_expiry.isoformat() if result.period_expiry else None,
        "cause": result.cause,
        "settlement_written": False,
    }


async def _maybe_await(value):
    """Await ``value`` if it is awaitable, else return it.

    ``submission_service.admin_detail`` is a plain (synchronous) function today, but calling
    it through this helper keeps the route correct if it ever becomes ``async`` — the route
    contract (an awaited detail) does not change.
    """
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Final registration order — literal-segment routes first (design §5)
#
# `main.py` includes `library.router`, and FastAPI resolves a request against
# `router.routes` in order. Splicing the literal router's routes in front of the
# parameterised ones makes GET /creator/analytics, GET /recommendations and
# GET /favorites reachable instead of being swallowed by GET /creator/{creator_id}
# and GET /{library_id}. The route objects themselves are untouched, so paths,
# handlers, dependencies and rate limits are exactly as declared above.
# ─────────────────────────────────────────────────────────────────────────────

router.routes[:0] = literal_router.routes
