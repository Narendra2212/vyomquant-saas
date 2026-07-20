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

import logging
import os
import json
from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, validator
from supabase import create_client

from backend_app.core.dependencies import (
    get_current_user,
    get_admin_user,
    create_request_supabase,
    bearer_scheme,
)
from backend_app.core.rate_limit import limiter
from fastapi.security import HTTPAuthorizationCredentials
from fastapi import Request
import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
try:
    redis_client = redis.from_url(REDIS_URL)
except Exception:
    redis_client = None

logger = logging.getLogger(__name__)

router = APIRouter()

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
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set."
            )
        _service_client = create_client(url, key)
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
    
    # Check cache
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
                        from backend_app.core.auth_middleware import decode_token_local
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
    query = (
        svc.table("library_strategies")
        .select(
            "id, name, author_id, category, difficulty, tags, symbol, timeframe, "
            "node_count, has_ml_model, backtest_sharpe_ratio, backtest_total_return_pct, "
            "backtest_max_drawdown_pct, backtest_win_rate_pct, backtest_total_trades, "
            "clone_count, avg_rating, rating_count, is_featured, published_at"
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
        logger.error(f"Library browse DB error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch library. Please try again.",
        )

    all_items = all_result.data or []
    count = len(all_items)
    offset = (page - 1) * limit
    paginated = all_items[offset : offset + limit]

    # --- Enrich with user context if authenticated ---
    user_id = None
    if credentials:
        try:
            from backend_app.core.auth_middleware import decode_token_local
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
    if redis_client:
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
                item.pop("user_has_cloned", None)
                item.pop("user_rating", None)
                item.pop("cloned_strategy_id", None)
            
            redis_client.setex(cache_key, 300, json.dumps(cache_payload))
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
            from backend_app.core.auth_middleware import decode_token_local
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
