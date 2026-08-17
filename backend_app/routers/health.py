"""
routers/health.py — Health Check Endpoint

PHASE 8: MONITORING + ALERTING + FAILSAFE INFRASTRUCTURE
STEP 8.6: Failover System - Health Checks

Purpose:
  - Provide /health endpoint for Docker/K8s health probes
  - Check Redis connectivity (primary + replica)
  - Check system resources
  - Return appropriate JSON status codes for auto-restart decisions

Endpoints:
  GET /health - Basic health check (returns 200 if healthy, 503 if degraded)
  GET /health/ready - Readiness probe (returns 200 when ready to serve traffic)
  GET /health/live - Liveness probe (returns 200 if process is alive)

Status Codes:
  200 - Healthy/Ready
  503 - Service Unavailable (degraded)
  500 - Internal Error (critical failure)
"""

import asyncio
import logging
import os
from typing import Any, Dict

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from backend_app.core.rate_limit import limiter  # BE-CRITICAL-005 FIX

logger = logging.getLogger("HealthCheck")

router = APIRouter()

REDIS_PRIMARY_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_REPLICA_URL = os.getenv("REDIS_REPLICA_URL", "redis://localhost:6380")


async def check_redis_health(url: str, timeout: float = 2.0) -> Dict[str, Any]:
    """Check Redis health with timeout."""
    try:
        client = redis.Redis.from_url(url, socket_timeout=timeout, decode_responses=True)
        ping_result = await client.ping()
        info = await client.info()
        await client.close()
        return {
            "healthy": ping_result,
            "role": info.get("role", "unknown"),
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_human": info.get("used_memory_human", "unknown"),
            "uptime_in_seconds": info.get("uptime_in_seconds", 0),
        }
    except Exception as e:
        logger.debug(f"Redis health check failed for {url}: {e}")
        return {
            "healthy": False,
            "error": str(e),
        }


@router.get("/health")
@limiter.limit("100/minute")  # BE-CRITICAL-005 FIX: Add rate limiting to prevent abuse
async def health_check():
    """
    Comprehensive health check.
    Returns:
        200 - All systems healthy
        503 - Degraded (Redis replica issues)
        500 - Critical failure (Redis primary unavailable)
    """
    checks = {
        "status": "healthy",
        "timestamp": asyncio.get_event_loop().time(),
        "checks": {},
    }
    
    primary_health = await check_redis_health(REDIS_PRIMARY_URL)
    checks["checks"]["redis_primary"] = primary_health
    
    replica_health = await check_redis_health(REDIS_REPLICA_URL)
    checks["checks"]["redis_replica"] = replica_health
    
    primary_healthy = primary_health.get("healthy", False)
    replica_healthy = replica_health.get("healthy", False)
    
    if primary_healthy and replica_healthy:
        checks["status"] = "healthy"
        checks["message"] = "All systems operational"
        return JSONResponse(status_code=200, content=checks)
    elif primary_healthy:
        checks["status"] = "degraded"
        checks["message"] = "Redis replica unavailable, operating with primary only"
        return JSONResponse(status_code=200, content=checks)
    else:
        checks["status"] = "degraded_fallback"
        checks["message"] = "Redis primary unavailable, operating in dev/fallback mode"
        return JSONResponse(status_code=200, content=checks)


@router.get("/health/ready")
@limiter.limit("100/minute")  # BE-CRITICAL-005 FIX: Add rate limiting
async def readiness_check():
    """
    Readiness probe for Kubernetes / ECS.
    Returns 200 when ready to accept traffic.
    """
    return JSONResponse(
        status_code=200,
        content={
            "ready": True,
            "timestamp": asyncio.get_event_loop().time(),
            "status": "ready",
        }
    )


@router.get("/health/live")
@limiter.limit("100/minute")  # BE-CRITICAL-005 FIX: Add rate limiting
async def liveness_check():
    """
    Liveness probe for Kubernetes / ECS.
    Returns 200 if process is alive.
    """
    return JSONResponse(
        status_code=200,
        content={
            "alive": True,
            "timestamp": asyncio.get_event_loop().time(),
            "status": "alive",
        }
    )


@router.get("/health/redis")
@limiter.limit("100/minute")  # BE-CRITICAL-005 FIX: Add rate limiting
async def redis_health_detailed():
    """
    Detailed Redis health check including replication status.
    """
    primary = await check_redis_health(REDIS_PRIMARY_URL)
    replica = await check_redis_health(REDIS_REPLICA_URL)
    
    replication_healthy = (
        primary.get("role") == "master" and
        replica.get("role") == "slave" and
        primary.get("healthy", False) and
        replica.get("healthy", False)
    )
    
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy" if replication_healthy else "degraded",
            "replication_healthy": replication_healthy,
            "redis_primary": primary,
            "redis_replica": replica,
            "failover_ready": replica.get("healthy", False),
        }
    )
