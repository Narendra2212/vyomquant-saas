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
from typing import List, Optional
from uuid import UUID

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator

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
from supabase import create_client
from backend_app.api_ws.ws_manager import manager as ws_manager
from backend_app.core.cache.redis_manager import redis_manager

logger = logging.getLogger(__name__)

router = APIRouter()
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
    """Fetches the author's display alias from the profiles table."""
    try:
        svc = _get_service_client()
        resp = (
            svc.table("profiles")
            .select("display_name, email")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if resp.data:
            return resp.data.get("display_name") or resp.data.get("email", "Anonymous")
    except Exception:
        pass
    return "Anonymous"


def check_deployment_permission(user_id: str, library_id: str) -> dict:
    """
    Check if user has permission to deploy a marketplace strategy.
    
    Returns:
        dict: {
            "has_permission": bool,
            "granted_via": str,  # "ownership" or "subscription"
            "subscription_id": Optional[str],
            "expires_at": Optional[str]
        }
    """
    svc = _get_service_client()
    if not svc:
        return {"has_permission": False, "reason": "Service unavailable"}
    
    user_uid = _safe_uuid(user_id, "user_id")
    lib_uid = _safe_uuid(library_id, "library_id")
    
    # Check 1: User owns the strategy (author)
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("author_id")
            .eq("id", lib_uid)
            .single()
            .execute()
        )
        if lib_resp.data and lib_resp.data["author_id"] == user_uid:
            return {
                "has_permission": True,
                "granted_via": "ownership",
                "subscription_id": None,
                "expires_at": None
            }
    except Exception as exc:
        logger.warning(f"Deployment permission check (ownership) failed: {exc}")
    
    # Check 2: User has active subscription
    try:
        sub_resp = (
            svc.table("library_subscriptions")
            .select("id, status, expires_at")
            .eq("library_id", lib_uid)
            .eq("user_id", user_uid)
            .eq("status", "active")
            .single()
            .execute()
        )
        if sub_resp.data:
            # Check if subscription is not expired
            expires_at = sub_resp.data.get("expires_at")
            if expires_at:
                expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if expiry > datetime.now(timezone.utc):
                    return {
                        "has_permission": True,
                        "granted_via": "subscription",
                        "subscription_id": sub_resp.data["id"],
                        "expires_at": expires_at
                    }
            else:
                # No expiry date means perpetual subscription
                return {
                    "has_permission": True,
                    "granted_via": "subscription",
                    "subscription_id": sub_resp.data["id"],
                    "expires_at": None
                }
    except Exception as exc:
        logger.warning(f"Deployment permission check (subscription) failed: {exc}")
    
    return {"has_permission": False, "reason": "No valid subscription or ownership"}


def grant_deployment_permission(user_id: str, library_id: str, granted_via: str, subscription_id: str = None) -> str:
    """
    Grant deployment permission to a user for a marketplace strategy.
    
    Returns:
        str: deployment_permission_id
    """
    svc = _get_service_client()
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    user_uid = _safe_uuid(user_id, "user_id")
    lib_uid = _safe_uuid(library_id, "library_id")
    
    now_ts = datetime.now(timezone.utc).isoformat()
    
    permission_payload = {
        "user_id": user_uid,
        "library_id": lib_uid,
        "granted_via": granted_via,
        "subscription_id": subscription_id,
        "is_active": True,
        "granted_at": now_ts,
        "expires_at": None,
    }
    
    try:
        resp = svc.table("deployment_permissions").insert(permission_payload).execute()
        if resp.data:
            return resp.data[0]["id"]
    except Exception as exc:
        logger.error(f"Failed to grant deployment permission: {exc}")
        raise HTTPException(500, "Failed to grant deployment permission")
    
    raise HTTPException(500, "Failed to grant deployment permission")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/featured — Featured strategies
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/featured")
async def get_featured_strategies(
    limit: int = Query(3, ge=1, le=10),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """Returns featured strategies (is_featured=True)."""
    svc = _build_service_client()
    
    # Check cache (10 minute TTL for featured)
    cache_key = f"library:featured:{limit}"
    try:
        cached_data = await redis_manager.get(cache_key)
        if cached_data:
            response_data = json.loads(cached_data)
            
            # Enrichment if authenticated
            user_id = None
            if credentials:
                try:
                    payload = decode_token_local(credentials.credentials)
                    user_id = payload.get("sub")
                except Exception:
                    pass
            
            if user_id:
                items = response_data.get("items", [])
                library_ids = [item["id"] for item in items]
                if library_ids:
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
                        
                        for item in items:
                            item["user_has_cloned"] = item["id"] in cloned_map
                            item["user_rating"] = rating_map.get(item["id"])
                            if item["user_has_cloned"]:
                                item["cloned_strategy_id"] = cloned_map[item["id"]]
                    except Exception as exc:
                        logger.warning(f"Failed to enrich featured with user context: {exc}")
            
            return response_data
    except Exception as e:
        logger.warning(f"Redis cache read failed for featured: {e}")
    
    if not svc:
        return {"items": []}
    
    try:
        resp = (
            svc.table("library_strategies")
            .select(
                "id, name, author_id, category, difficulty, tags, symbol, timeframe, "
                "node_count, has_ml_model, backtest_sharpe_ratio, backtest_total_return_pct, "
                "backtest_max_drawdown_pct, backtest_win_rate_pct, backtest_total_trades, "
                "clone_count, avg_rating, rating_count, is_featured, published_at, "
                "price, currency, subscription_tier, subscriber_count"
            )
            .eq("is_active", True)
            .eq("is_featured", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"Featured strategies DB error: {exc}")
        return {"items": []}
    
    items = resp.data or []
    
    # Add author aliases
    for item in items:
        item["author_alias"] = _get_author_alias(item.get("author_id", ""))
        item.pop("author_id", None)
    
    response_data = {"items": items}
    
    # Save to cache
    try:
        cache_payload = {
            "items": items
        }
        await redis_manager.set(cache_key, json.dumps(cache_payload), ex=600)
    except Exception as e:
        logger.warning(f"Redis cache write failed for featured: {e}")
    
    # Enrich with user context if authenticated (after cache save)
    user_id = None
    if credentials:
        try:
            payload = decode_token_local(credentials.credentials)
            user_id = payload.get("sub")
        except Exception:
            pass
    
    if user_id:
        try:
            library_ids = [item["id"] for item in items]
            if library_ids:
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
                
                for item in items:
                    item["user_has_cloned"] = item["id"] in cloned_map
                    item["user_rating"] = rating_map.get(item["id"])
                    if item["user_has_cloned"]:
                        item["cloned_strategy_id"] = cloned_map[item["id"]]
        except Exception as exc:
            logger.warning(f"Failed to enrich featured with user context: {exc}")
    
    return response_data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/trending — Trending strategies
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/trending")
async def get_trending_strategies(
    limit: int = Query(10, ge=1, le=20),
):
    """Returns trending strategies (sorted by clone_count + rating)."""
    svc = _build_service_client()
    
    if not svc:
        return {"items": [], "total": 0}
    
    try:
        resp = (
            svc.table("library_strategies")
            .select(
                "id, name, author_id, category, difficulty, tags, symbol, timeframe, "
                "node_count, has_ml_model, backtest_sharpe_ratio, backtest_total_return_pct, "
                "backtest_max_drawdown_pct, backtest_win_rate_pct, backtest_total_trades, "
                "clone_count, avg_rating, rating_count, is_featured, price, currency, "
                "subscription_tier, cover_image, evaluation_score, subscriber_count, published_at"
            )
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("clone_count", desc=True)
            .order("avg_rating", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"Trending strategies DB error: {exc}")
        return {"items": [], "total": 0}
    
    items = resp.data or []
    author_alias_cache = {}
    
    def _get_alias(author_id: str) -> str:
        if author_id not in author_alias_cache:
            author_alias_cache[author_id] = _get_author_alias(author_id)
        return author_alias_cache[author_id]
    
    for item in items:
        item["author_alias"] = _get_alias(item.get("author_id", ""))
        item.pop("author_id", None)
    
    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/categories — Available categories
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/categories")
async def get_categories():
    """Returns available strategy categories with counts."""
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
        return {"categories": []}
    
    try:
        resp = (
            svc.table("library_strategies")
            .select("category")
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .execute()
        )
    except Exception as exc:
        logger.warning(f"Categories DB error: {exc}")
        return {"categories": []}
    
    items = resp.data or []
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

@router.get("/creator/{creator_id}")
async def get_creator_profile(creator_id: str):
    """Returns creator profile with published strategies and stats."""
    creator_uid = _safe_uuid(creator_id, "creator_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(status_code=503, detail="Service unavailable")
    
    # Fetch creator's strategies
    try:
        strat_resp = (
            svc.table("library_strategies")
            .select(
                "id, name, category, difficulty, clone_count, avg_rating, rating_count, "
                "is_featured, price, subscription_tier, evaluation_score, subscriber_count, published_at"
            )
            .eq("author_id", creator_uid)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("published_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"Creator profile DB error: {exc}")
        raise HTTPException(status_code=500, detail="Failed to fetch creator profile")
    
    strategies = strat_resp.data or []
    
    # Calculate creator stats
    total_subscribers = sum(s.get("subscriber_count", 0) for s in strategies)
    total_clones = sum(s.get("clone_count", 0) for s in strategies)
    avg_rating = 0.0
    if strategies:
        ratings = [s.get("avg_rating") for s in strategies if s.get("avg_rating") is not None]
        if ratings:
            avg_rating = round(sum(ratings) / len(ratings), 2)
    
    # Get creator alias
    alias = _get_author_alias(creator_uid)
    
    return {
        "creator_id": creator_uid,
        "author_alias": alias,
        "total_strategies": len(strategies),
        "total_subscribers": total_subscribers,
        "total_clones": total_clones,
        "avg_rating": avg_rating,
        "strategies": strategies,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library — Browse catalogue
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
async def browse_library(
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
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                response_data = json.loads(cached_data)
                
                # --- Enrichment (if authenticated) ---
                user_id = None
                if credentials:
                    try:
                        payload = decode_token_local(credentials.credentials)
                        user_id = payload.get("sub")
                    except Exception:
                        pass
                
                if user_id:
                    strategies = response_data.get("strategies", [])
                    strat_ids = [s["id"] for s in strategies]
                    
                    if strat_ids:
                        enrich_resp = (
                            svc.table("library_strategies")
                            .select("id, source_strategy_id")
                            .eq("author_id", user_id)
                            .in_("source_library_id", strat_ids)
                            .execute()
                        )
                        cloned_map = {r["source_library_id"]: r["source_strategy_id"] for r in (enrich_resp.data or [])}
                        
                        rating_resp = (
                            svc.table("library_ratings")
                            .select("library_id, rating")
                            .eq("user_id", user_id)
                            .in_("library_id", strat_ids)
                            .execute()
                        )
                        rating_map = {r["library_id"]: r["rating"] for r in (rating_resp.data or [])}
                        
                        for s in strategies:
                            s["user_has_cloned"] = s["id"] in cloned_map
                            s["user_rating"] = rating_map.get(s["id"])
                            if s["user_has_cloned"]:
                                s["cloned_strategy_id"] = cloned_map[s["id"]]
                                
                return response_data
        except Exception as e:
            logger.warning(f"Redis cache read failed: {e}")

    # --- Build query ---
    if not svc:
        return {"items": [], "total": 0, "page": page, "limit": limit}

    query = (
        svc.table("library_strategies")
        .select(
            "id, name, author_id, category, difficulty, tags, symbol, timeframe, "
            "node_count, has_ml_model, backtest_sharpe_ratio, backtest_total_return_pct, "
            "backtest_max_drawdown_pct, backtest_win_rate_pct, backtest_total_trades, "
            "clone_count, avg_rating, rating_count, is_featured, published_at, "
            "price, currency, subscription_tier, subscriber_count"
        )
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
        logger.warning(f"Library browse DB error (returning empty fallback): {exc}")
        return {"items": [], "total": 0, "page": page, "limit": limit}

    all_items = all_result.data or []
    count = len(all_items)
    offset = (page - 1) * limit
    paginated = all_items[offset : offset + limit]

    # --- Enrich with user context if authenticated ---
    user_id = None
    if credentials:
        try:
            payload = decode_token_local(credentials.credentials)
            user_id = payload.get("sub")
        except Exception:
            pass

    cloned_map = {}
    rating_map = {}

    if user_id:
        try:
            library_ids = [item["id"] for item in paginated]
            if library_ids:
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
            logger.warning(f"Failed to enrich browse with user context: {exc}")

    # --- Build response ---
    author_alias_cache = {}

    def _get_alias(author_id: str) -> str:
        if author_id not in author_alias_cache:
            author_alias_cache[author_id] = _get_author_alias(author_id)
        return author_alias_cache[author_id]

    items = []
    for item in paginated:
        card = dict(item)
        card["author_alias"] = _get_alias(card.get("author_id", ""))
        card.pop("author_id", None)
        if user_id:
            card["user_has_cloned"] = item["id"] in cloned_map
            card["user_rating"] = rating_map.get(item["id"])
            if card["user_has_cloned"]:
                card["cloned_strategy_id"] = cloned_map[item["id"]]
        items.append(card)

    response_data = {
        "items": items,
        "total": count,
        "page": page,
        "limit": limit,
        "pages": (count + limit - 1) // limit if count > 0 else 1
    }

    # Save to cache
    try:
        cache_payload = {
            "items": items,
            "total": count,
            "page": page,
            "limit": limit,
            "pages": response_data["pages"]
        }
        # Strip auth-sensitive keys for generic cache
        for item in cache_payload["items"]:
            if isinstance(item, dict):
                item.pop("user_has_cloned", None)
                item.pop("user_rating", None)
                item.pop("cloned_strategy_id", None)
        
        await redis_manager.set(cache_key, json.dumps(cache_payload), ex=300)
    except Exception as e:
        logger.warning(f"Redis cache write failed: {e}")

    return response_data


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/me — My published strategies
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/me")
async def my_library(user: dict = Depends(get_current_user)):
    """Returns all library entries authored by the authenticated user."""
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    try:
        resp = (
            svc.table("library_strategies")
            .select(
                "id, name, moderation_status, clone_count, avg_rating, rating_count, is_active, published_at"
            )
            .eq("author_id", user_id)
            .order("published_at", desc=True)
            .execute()
        )
    except Exception as exc:
        logger.error(f"my_library DB error for user {user_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch your library entries.",
        )

    items = resp.data or []
    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/{library_id} — Strategy detail
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{library_id}")
async def get_library_detail(
    library_id: str,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
):
    """
    Returns full strategy detail including equity curve snapshot and
    recent ratings. Auth is optional; enriches user_has_cloned / user_rating
    if authenticated.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    svc = _build_service_client()

    # Fetch the library entry
    try:
        resp = (
            svc.table("library_strategies")
            .select("*")
            .eq("id", lib_id)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"get_library_detail DB error for {lib_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch strategy detail.",
        )

    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strategy not found or not publicly available.",
        )

    detail = dict(resp.data)
    detail["author_alias"] = _get_author_alias(detail.get("author_id", ""))
    detail.pop("author_id", None)

    # Recent ratings (5 most recent with a non-null rating)
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
        detail["recent_ratings"] = ratings_resp.data or []
    except Exception as exc:
        logger.warning(f"Failed to fetch recent ratings for {lib_id}: {exc}")
        detail["recent_ratings"] = []

    # User context enrichment
    user_id = None
    if credentials:
        try:
            payload = decode_token_local(credentials.credentials)
            user_id = payload.get("sub")
        except Exception:
            pass

    detail["user_has_cloned"] = False
    detail["user_rating"] = None

    if user_id:
        try:
            ur = (
                svc.table("library_ratings")
                .select("is_verified_clone, rating")
                .eq("library_id", lib_id)
                .eq("user_id", user_id)
                .single()
                .execute()
            )
            if ur.data:
                detail["user_has_cloned"] = ur.data.get("is_verified_clone", False)
                detail["user_rating"] = ur.data.get("rating")
        except Exception:
            pass

    return detail


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library — Publish a strategy
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
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
    
    # Evaluator: Compute evaluation score based on backtest metrics
    sharpe = float(br.get("sharpe_ratio") or 0)
    return_pct = float(br.get("total_return_pct") or br.get("total_return") or 0)
    max_dd = float(br.get("max_drawdown_pct") or br.get("max_drawdown") or 100)
    win_rate = float(br.get("win_rate_pct") or br.get("win_rate") or 0)
    profit_factor = float(br.get("profit_factor") or 0)
    
    # Evaluation score calculation (0-100)
    # Weighted: Sharpe (40%), Return (25%), Win Rate (20%), Profit Factor (15%)
    eval_score = 0.0
    if sharpe > 0:
        eval_score += min(sharpe * 10, 40)  # Max 40 points for Sharpe >= 4
    if return_pct > 0:
        eval_score += min(return_pct / 5, 25)  # Max 25 points for Return >= 125%
    eval_score += min(win_rate * 0.2, 20)  # Max 20 points for Win Rate >= 100%
    if profit_factor > 0:
        eval_score += min(profit_factor * 3, 15)  # Max 15 points for Profit Factor >= 5
    eval_score = round(eval_score, 2)
    
    # Pricing validation: Free strategies must have evaluation_score >= 50
    # Paid strategies must have evaluation_score >= 70
    min_score_for_free = 50
    min_score_for_paid = 70
    
    if payload.subscription_tier == "free" and eval_score < min_score_for_free:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Strategy evaluation score ({eval_score}) below minimum ({min_score_for_free}) for free publication. Improve backtest performance."
        )
    
    if payload.subscription_tier in ("pro", "elite") and eval_score < min_score_for_paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Strategy evaluation score ({eval_score}) below minimum ({min_score_for_paid}) for paid publication. Improve backtest performance."
        )
    
    # Price suggestion based on evaluation score
    suggested_price = None
    if payload.price is None and payload.subscription_tier != "free":
        # Auto-suggest price based on score
        if eval_score >= 85:
            suggested_price = 199.99  # Elite tier
        elif eval_score >= 75:
            suggested_price = 99.99   # High pro
        elif eval_score >= 70:
            suggested_price = 49.99   # Standard pro
        else:
            suggested_price = 29.99   # Entry pro
    
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
        # Marketplace pricing
        "price": payload.price if payload.price is not None else suggested_price,
        "currency": payload.currency,
        "subscription_tier": payload.subscription_tier,
        "cover_image": payload.cover_image,
        "evaluation_score": eval_score,
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
    if redis_client:
        try:
            for key in redis_client.scan_iter("library:browse:*"):
                redis_client.delete(key)
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
        "evaluation_score": eval_score,
        "suggested_price": suggested_price,
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

    logger.info(f"Strategy unpublished: library_id={lib_id} by user={user_id}")
    return {"message": "Strategy unpublished. Existing clones are unaffected."}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/clone — Clone strategy
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{library_id}/clone", status_code=status.HTTP_201_CREATED)
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

    Transaction flow:
    1. Validate library entry is approved and active
    2. Block self-clone
    3. Idempotency: return existing clone if already cloned
    4. Fetch source strategy DAG via service role
    5. Insert new strategies row owned by calling user
    6. Increment clone_count
    7. Create / update library_ratings row with is_verified_clone=TRUE
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()

    # 1. Fetch library entry
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("id, author_id, source_strategy_id, name, is_active, moderation_status, clone_count")
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

    # 2. Block self-clone
    if lib_entry["author_id"] == user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot clone your own published strategy.",
        )

    # 3. Idempotency: check if user already cloned this library entry
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

    # 4. Fetch source strategy DAG (service-role, cross-user read)
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

    # 5. Insert clone into strategies table
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

    # 6. Increment clone_count
    try:
        new_count = (lib_entry.get("clone_count") or 0) + 1
        svc.table("library_strategies").update(
            {"clone_count": new_count, "updated_at": now_ts}
        ).eq("id", lib_id).execute()
    except Exception as exc:
        # Non-critical — clone already succeeded, log and continue
        logger.error(f"clone_strategy clone_count increment error: {exc}")

    # 7. Create verified-clone marker in library_ratings (upsert)
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
    except Exception as exc:
        # Non-critical — log and continue
        logger.error(f"clone_strategy verified-clone rating marker error: {exc}")

    logger.info(
        f"Strategy cloned: library_id={lib_id} -> new_strategy_id={new_strategy_id} by user={user_id}"
    )

    # 8. Invalidate cache
    if redis_client:
        try:
            for key in redis_client.scan_iter("library:browse:*"):
                redis_client.delete(key)
        except Exception as e:
            logger.warning(f"Redis cache invalidation failed: {e}")

    return {
        "new_strategy_id": new_strategy_id,
        "message": "Strategy cloned into your builder. Ready to customise.",
    }


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
    if redis_client:
        try:
            for key in redis_client.scan_iter("library:browse:*"):
                redis_client.delete(key)
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
            .select("id, moderation_status")
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

@router.get("/admin/pending", status_code=status.HTTP_200_OK)
async def admin_pending_strategies(
    admin: dict = Depends(get_admin_user),
):
    """
    Admin-only: Returns all strategies awaiting moderation, ordered oldest-first.
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch pending moderation queue.",
        )

    items = resp.data or []
    for item in items:
        item["author_alias"] = _get_author_alias(item.get("author_id", ""))

    return {"items": items, "total": len(items)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/{library_id}/checkout — Create checkout session for subscription
# ─────────────────────────────────────────────────────────────────────────────

class MarketplaceCheckoutRequest(BaseModel):
    currency: str = Field("USD", pattern="^(USD|INR)$")

@router.post("/{library_id}/checkout")
async def create_marketplace_checkout(
    library_id: str,
    body: MarketplaceCheckoutRequest,
    user: dict = Depends(get_current_user),
    _feature=Depends(require_marketplace_access),
):
    """Creates a payment checkout session for marketplace strategy subscription."""
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    # Fetch strategy details
    try:
        resp = (
            svc.table("library_strategies")
            .select("*")
            .eq("id", lib_id)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"Checkout strategy lookup error: {exc}")
        raise HTTPException(500, "Failed to fetch strategy details")
    
    if not resp.data:
        raise HTTPException(404, "Strategy not found or not available for subscription")
    
    strat = resp.data[0]
    
    # Check if strategy has a price
    if not strat.get("price"):
        raise HTTPException(400, "This strategy is free. Use the clone endpoint instead.")
    
    # Check if already subscribed
    try:
        existing = (
            svc.table("library_subscriptions")
            .select("*")
            .eq("library_id", lib_id)
            .eq("user_id", user_id)
            .eq("status", "active")
            .execute()
        )
        if existing.data:
            raise HTTPException(400, "You are already subscribed to this strategy")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"Subscription check error: {exc}")
    
    # Create pending subscription record
    now_ts = datetime.now(timezone.utc).isoformat()
    sub_id = str(uuid.uuid4())
    
    try:
        sub_resp = (
            svc.table("library_subscriptions")
            .insert({
                "id": sub_id,
                "library_id": lib_id,
                "user_id": user_id,
                "subscription_tier": strat.get("subscription_tier", "standard"),
                "price_paid": strat.get("price"),
                "currency": strat.get("currency", "USD"),
                "status": "pending",
                "started_at": now_ts,
                "expires_at": None,
            })
            .execute()
        )
    except Exception as exc:
        logger.error(f"Pending subscription creation error: {exc}")
        raise HTTPException(500, "Failed to create pending subscription")
    
    # Create checkout session using billing system
    try:
        if body.currency == "USD":
            import stripe
            stripe_key = os.environ.get("STRIPE_SECRET_KEY", "sk_test_dummy")
            stripe.api_key = stripe_key
            
            amount_cents = int(float(strat.get("price", 0)) * 100)
            
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[
                    {
                        "price_data": {
                            "currency": "usd",
                            "product_data": {
                                "name": f"Strategy Subscription: {strat.get('name', 'Unknown')}",
                                "description": f"Monthly subscription to marketplace strategy",
                            },
                            "unit_amount": amount_cents,
                        },
                        "quantity": 1,
                    }
                ],
                mode="payment",
                success_url=f"{os.environ.get('FRONTEND_URL', 'http://localhost:3000')}/marketplace?session_id={{CHECKOUT_SESSION_ID}}&sub_id={sub_id}",
                cancel_url=f"{os.environ.get('FRONTEND_URL', 'http://localhost:3000')}/marketplace",
                metadata={
                    "user_id": user_id,
                    "subscription_id": sub_id,
                    "library_id": lib_id,
                    "item_key": f"marketplace_{lib_id}",
                },
            )
            
            return {
                "checkout_url": session.url,
                "subscription_id": sub_id,
                "provider": "stripe"
            }
            
        elif body.currency == "INR":
            import razorpay
            razorpay_key = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_dummy")
            razorpay_secret = os.environ.get("RAZORPAY_KEY_SECRET")
            
            client = razorpay.Client(auth=(razorpay_key, razorpay_secret))
            
            amount_paise = int(float(strat.get("price", 0)) * 100)
            
            order = client.order.create({
                "amount": amount_paise,
                "currency": "INR",
                "receipt": f"marketplace_sub_{sub_id}",
                "notes": {
                    "user_id": user_id,
                    "subscription_id": sub_id,
                    "library_id": str(lib_id),
                    "item": f"marketplace_{lib_id}",  # Razorpay webhook expects "item" key
                },
            })
            
            return {
                "order_id": order["id"],
                "razorpay_key": razorpay_key,
                "amount": amount_paise,
                "currency": "INR",
                "subscription_id": sub_id,
                "provider": "razorpay"
            }
        else:
            raise HTTPException(400, "Invalid currency")
            
    except Exception as exc:
        logger.error(f"Checkout session creation error: {exc}")
        # Clean up pending subscription
        try:
            svc.table("library_subscriptions").delete().eq("id", sub_id).execute()
        except:
            pass
        raise HTTPException(500, "Failed to create checkout session")


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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check subscription status.",
        )

    if not sub_resp.data:
        return {
            "status": "not_subscribed",
            "library_id": lib_id,
            "message": "No subscription found. Complete checkout at /api/library/{library_id}/checkout"
        }

    subscription = sub_resp.data[0]
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

@router.post("/{library_id}/deploy")
async def deploy_marketplace_strategy(
    library_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Deploy a marketplace strategy after verifying subscription or ownership.
    This endpoint clones the strategy and starts deployment.
    """
    lib_id = _safe_uuid(library_id, "library_id")
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    # Check deployment permission
    permission = check_deployment_permission(user_id, lib_id)
    if not permission.get("has_permission"):
        raise HTTPException(
            status_code=403,
            detail=f"Deployment not authorized: {permission.get('reason', 'Unknown reason')}"
        )
    
    # Clone the strategy (reuse clone logic)
    try:
        lib_resp = (
            svc.table("library_strategies")
            .select("id, author_id, source_strategy_id, name, is_active, moderation_status")
            .eq("id", lib_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"deploy_strategy library lookup error: {exc}")
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
    source_id = lib_entry["source_strategy_id"]
    
    # Fetch source strategy
    try:
        src_resp = (
            svc.table("strategies")
            .select("name, symbol, timeframe, exchange_id, buy_logic, sell_logic, risk, indicators, ml_model_path")
            .eq("id", source_id)
            .single()
            .execute()
        )
    except Exception as exc:
        logger.error(f"deploy_strategy source fetch error: {exc}")
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

    # SECURITY: Validate ML/DL strategies have trained models before deployment
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
                         "Cannot deploy untrained ML strategy."
            }
        )

    # Insert clone into strategies table
    clone_payload = {
        "user_id": user_id,
        "name": f"[Deployed] {lib_entry['name']}",
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
        logger.error(f"deploy_strategy insert error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create strategy clone for deployment.",
        )

    if not clone_resp.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Deploy operation returned no data.",
        )

    new_strategy_id = clone_resp.data[0]["id"]

    return {
        "strategy_id": new_strategy_id,
        "library_id": lib_id,
        "granted_via": permission.get("granted_via"),
        "message": "Strategy cloned and ready for deployment. Use the deployment API to start live trading.",
    }

@router.post("/subscriptions/{sub_id}/cancel")
async def cancel_subscription(sub_id: str, user: dict = Depends(get_current_user)):
    """Cancel marketplace strategy subscription."""
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
    
    # Revoke deployment permission
    try:
        svc.table("deployment_permissions").update({
            "is_active": False,
            "revoked_at": until
        }).eq("subscription_id", sub_uid).execute()
    except Exception as exc:
        logger.warning(f"Failed to revoke deployment permission on cancel: {exc}")
    
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
async def renew_subscription(sub_id: str, user: dict = Depends(get_current_user)):
    """Renew marketplace strategy subscription."""
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
        logger.error(f"Renew subscription lookup error: {exc}")
        raise HTTPException(500, "Failed to lookup subscription")
    
    now = datetime.now(timezone.utc).isoformat()
    
    try:
        # Only renew if subscription is cancelled or expired (idempotent)
        update_resp = svc.table("library_subscriptions").update({
            "status": "active",
            "cancelled_at": None,
            "expires_at": None
        }).eq("id", sub_uid).in_("status", ["cancelled", "expired"]).execute()
        
        if not update_resp.data:
            # Idempotency: subscription already active or invalid state
            logger.info(f"Subscription {sub_id} already active or invalid state - idempotent no-op")
            return {
                "status": "already_active",
                "subscription_id": sub_id,
                "message": "Subscription is already active"
            }
    except Exception as exc:
        logger.error(f"Renew subscription update error: {exc}")
        raise HTTPException(500, "Failed to renew subscription")
    
    # Re-fetch subscription to get library_id after update
    try:
        sub_resp = svc.table("library_subscriptions").select("*").eq("id", sub_uid).eq("user_id", user_id).execute()
        if not sub_resp.data:
            raise HTTPException(404, f"Subscription '{sub_id}' not found after renew.")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Renew subscription post-update lookup error: {exc}")
        raise HTTPException(500, "Failed to lookup subscription after renew")
    
    # Re-grant deployment permission
    try:
        sub = sub_resp.data[0]
        lib_id = sub.get("library_id")
        if lib_id:
            grant_deployment_permission(user_id, lib_id, "subscription", sub_uid)
    except Exception as exc:
        logger.warning(f"Failed to re-grant deployment permission on renew: {exc}")
    
    # Increment subscriber_count on library_strategies
    try:
        sub = sub_resp.data[0]
        lib_id = sub.get("library_id")
        if lib_id:
            lib_resp = svc.table("library_strategies").select("subscriber_count").eq("id", lib_id).execute()
            if lib_resp.data:
                current = lib_resp.data[0].get("subscriber_count", 0)
                svc.table("library_strategies").update({
                    "subscriber_count": current + 1,
                    "updated_at": now
                }).eq("id", lib_id).execute()
    except Exception as exc:
        logger.warning(f"Failed to increment subscriber_count: {exc}")
    
    return {"status": "renewed", "subscription_id": sub_id, "renewed_at": now}

@router.get("/creator/analytics")
async def creator_analytics(user: dict = Depends(get_current_user)):
    """Creator analytics dashboard derived from actual user publications and subscriber data."""
    svc = _build_service_client()
    try:
        resp = (
            svc.table("library_strategies")
            .select("id, name, clone_count, monthly_price, rating_average")
            .eq("author_id", user["id"])
            .execute()
        )
        strats = resp.data or []
        total_subs = sum(s.get("clone_count", 0) for s in strats)
        mrr = sum(s.get("clone_count", 0) * float(s.get("monthly_price") or 0.0) for s in strats)
        creator_mrr = round(mrr * 0.90, 2)
        platform_fee = round(mrr * 0.10, 2)
        
        ratings = [float(s.get("rating_average")) for s in strats if s.get("rating_average") is not None]
        avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0
        
        return {
            "creator_id": user["id"],
            "total_earnings_usd": creator_mrr,
            "monthly_recurring_revenue": creator_mrr,
            "platform_fee_paid": platform_fee,
            "active_subscribers": total_subs,
            "published_strategies_count": len(strats),
            "rating_average": avg_rating,
            "payout_schedule": "Monthly auto-transfer (Stripe Connect)"
        }
    except Exception as exc:
        logger.error(f"Error fetching creator analytics for user {user['id']}: {exc}")
        return {
            "creator_id": user["id"],
            "total_earnings_usd": 0.0,
            "monthly_recurring_revenue": 0.0,
            "platform_fee_paid": 0.0,
            "active_subscribers": 0,
            "published_strategies_count": 0,
            "rating_average": 0.0,
            "payout_schedule": "Monthly auto-transfer (Stripe Connect)"
        }

@router.get("/subscriber/analytics")
async def subscriber_analytics(user: dict = Depends(get_current_user)):
    """
    Subscriber analytics: what strategies has this user subscribed to?
    Queries library_subscriptions (the table populated by subscribe_to_strategy).
    """
    user_id = _safe_uuid(user["id"], "user_id")
    svc = _build_service_client()
    
    if not svc:
        raise HTTPException(503, "Service unavailable")
    
    try:
        # Query the user's marketplace subscriptions
        res = svc.table("library_subscriptions").select(
            "id, library_id, subscription_tier, price_paid, currency, status, started_at, expires_at"
        ).eq("user_id", user_id).execute()
        subscriptions = res.data or []

        active_subs = [s for s in subscriptions if s.get("status") == "active"]
        monthly_spend = round(sum(float(s.get("price_paid") or 0.0) for s in active_subs), 2)

        return {
            "subscriber_id": user_id,
            "active_subscriptions_count": len(active_subs),
            "total_subscriptions_count": len(subscriptions),
            "monthly_spend_usd": monthly_spend,
            "subscriptions": subscriptions,
        }
    except Exception as exc:
        logger.error(f"Error fetching subscriber analytics for user {user_id}: {exc}")
        raise HTTPException(500, "Failed to fetch subscriber analytics")


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/{library_id}/reviews — Get strategy reviews
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{library_id}/reviews")
async def get_strategy_reviews(
    library_id: str,
    limit: int = Query(10, ge=1, le=50),
):
    """Returns reviews for a specific strategy."""
    lib_id = _safe_uuid(library_id, "library_id")
    svc = _build_service_client()
    
    if not svc:
        return {"reviews": [], "total": 0}
    
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
        logger.warning(f"Reviews DB error: {exc}")
        return {"reviews": [], "total": 0}
    
    reviews = resp.data or []
    
    # Anonymize user IDs
    for review in reviews:
        review["user_alias"] = _get_author_alias(review.get("user_id", ""))
        review.pop("user_id", None)
    
    return {"reviews": reviews, "total": len(reviews)}


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/library/recommendations — Get personalized recommendations
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/recommendations")
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
        
        # Query strategies matching preferred categories or trending
        if preferred_categories:
            resp = (
                svc.table("library_strategies")
                .select(
                    "id, name, author_id, category, difficulty, tags, "
                    "backtest_sharpe_ratio, backtest_total_return_pct, clone_count, "
                    "avg_rating, rating_count, price, subscription_tier, cover_image, "
                    "evaluation_score, subscriber_count"
                )
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
                .select(
                    "id, name, author_id, category, difficulty, tags, "
                    "backtest_sharpe_ratio, backtest_total_return_pct, clone_count, "
                    "avg_rating, rating_count, price, subscription_tier, cover_image, "
                    "evaluation_score, subscriber_count"
                )
                .eq("is_active", True)
                .in_("moderation_status", ["approved", "featured"])
                .order("clone_count", desc=True)
                .limit(limit)
                .execute()
            )
    except Exception as exc:
        logger.warning(f"Recommendations DB error: {exc}")
        return {"recommendations": [], "total": 0}
    
    items = resp.data or []
    
    # Filter out already subscribed strategies
    recommendations = [s for s in items if s["id"] not in subscribed_ids]
    
    for item in recommendations:
        item["author_alias"] = _get_author_alias(item.get("author_id", ""))
        item.pop("author_id", None)
    
    return {"recommendations": recommendations[:limit], "total": len(recommendations)}


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/library/compare — Compare multiple strategies
# ─────────────────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    library_ids: List[str] = Field(..., min_items=2, max_items=5)

@router.post("/compare")
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
            .select(
                "id, name, author_id, category, difficulty, tags, symbol, timeframe, "
                "node_count, has_ml_model, backtest_sharpe_ratio, backtest_total_return_pct, "
                "backtest_max_drawdown_pct, backtest_win_rate_pct, backtest_total_trades, "
                "backtest_profit_factor, clone_count, avg_rating, rating_count, "
                "price, subscription_tier, evaluation_score, subscriber_count, published_at"
            )
            .in_("id", lib_ids)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .execute()
        )
    except Exception as exc:
        logger.error(f"Compare strategies DB error: {exc}")
        raise HTTPException(500, "Failed to fetch strategies for comparison")
    
    strategies = resp.data or []
    
    for s in strategies:
        s["author_alias"] = _get_author_alias(s.get("author_id", ""))
        s.pop("author_id", None)
    
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

@router.get("/favorites")
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
            return {"favorites": [], "total": 0}
        
        # Fetch strategy details
        resp = (
            svc.table("library_strategies")
            .select(
                "id, name, author_id, category, difficulty, tags, "
                "backtest_sharpe_ratio, backtest_total_return_pct, clone_count, "
                "avg_rating, rating_count, price, subscription_tier, cover_image, "
                "evaluation_score, subscriber_count, published_at"
            )
            .in_("id", fav_ids)
            .eq("is_active", True)
            .in_("moderation_status", ["approved", "featured"])
            .order("published_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"Favorites DB error: {exc}")
        return {"favorites": [], "total": 0}
    
    items = resp.data or []
    
    for item in items:
        item["author_alias"] = _get_author_alias(item.get("author_id", ""))
        item.pop("author_id", None)
    
    return {"favorites": items, "total": len(items)}
