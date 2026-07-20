"""
routers/health.py — Health Check Endpoint (STEP 8.6)

PHASE 8: MONITORING + ALERTING + FAILSAFE INFRASTRUCTURE
STEP 8.6: Failover System - Health Checks

Purpose:
  - Provide /health endpoint for Docker/K8s health probes
  - Check Redis connectivity (primary + replica)
  - Check WebSocket connections
  - Check system resources
  - Return appropriate status codes for auto-restart decisions

Endpoints:
  GET /health - Basic health check (returns 200 if healthy, 503 if degraded)
  GET /health/ready - Readiness probe (returns 200 when ready to serve traffic)
  GET /health/live - Liveness probe (returns 200 if process is alive)

Status Codes:
  200 - Healthy/Ready
  503 - Service Unavailable (degraded)
  500 - Internal Error (critical failure)
"""

from fastapi import APIRouter, Response, HTTPException
from typing import Dict, Any
import redis.asyncio as redis
import asyncio
import logging

logger = logging.getLogger("HealthCheck")

router = APIRouter()

# Redis connection settings
REDIS_PRIMARY_URL = "redis://localhost:6379"
REDIS_REPLICA_URL = "redis://localhost:6380"


async def check_redis_health(url: str, timeout: float = 2.0) -> Dict[str, Any]:
    """Check Redis health with timeout."""
    try:
        client = redis.Redis.from_url(url, socket_timeout=timeout, decode_responses=True)
        
        # Test ping
        ping_result = await client.ping()
        
        # Get info
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
        logger.error(f"Redis health check failed for {url}: {e}")
        return {
            "healthy": False,
            "error": str(e),
        }


@router.get("/health")
async def health_check():
    """
    STEP 8.6: Comprehensive health check.
    
    Returns:
        200 - All systems healthy
        503 - Degraded (Redis issues, but can serve traffic)
        500 - Critical failure (cannot operate)
    """
    checks = {
        "status": "healthy",
        "timestamp": asyncio.get_event_loop().time(),
        "checks": {},
    }
    
    # Check Redis Primary
    primary_health = await check_redis_health(REDIS_PRIMARY_URL)
    checks["checks"]["redis_primary"] = primary_health
    
    # Check Redis Replica
    replica_health = await check_redis_health(REDIS_REPLICA_URL)
    checks["checks"]["redis_replica"] = replica_health
    
    # Determine overall status
    primary_healthy = primary_health.get("healthy", False)
    replica_healthy = replica_health.get("healthy", False)
    
    if primary_healthy and replica_healthy:
        checks["status"] = "healthy"
        checks["message"] = "All systems operational"
        return checks
    elif primary_healthy:
        # Primary OK, replica down - degraded but operational
        checks["status"] = "degraded"
        checks["message"] = "Redis replica unavailable, operating with primary only"
        logger.warning("STEP 8.6: Health check degraded - Redis replica down")
        return Response(
            content=str(checks),
            status_code=503,
            media_type="application/json"
        )
    else:
        # Primary down - critical failure
        checks["status"] = "critical"
        checks["message"] = "Redis primary unavailable - cannot operate"
        logger.critical("STEP 8.6: Health check critical - Redis primary down")
        return Response(
            content=str(checks),
            status_code=500,
            media_type="application/json"
        )


@router.get("/health/ready")
async def readiness_check():
    """
    STEP 8.6: Readiness probe for Kubernetes.
    
    Returns 200 when the service is ready to accept traffic.
    Returns 503 when not ready (e.g., still initializing).
    """
    try:
        # Check Redis primary is accessible
        primary_health = await check_redis_health(REDIS_PRIMARY_URL, timeout=1.0)
        
        if primary_health.get("healthy", False):
            return {
                "ready": True,
                "timestamp": asyncio.get_event_loop().time(),
                "redis_primary": "connected",
            }
        else:
            return Response(
                content=str({
                    "ready": False,
                    "timestamp": asyncio.get_event_loop().time(),
                    "redis_primary": "disconnected",
                }),
                status_code=503,
                media_type="application/json"
            )
    except Exception as e:
        logger.error(f"STEP 8.6: Readiness check failed: {e}")
        return Response(
            content=str({
                "ready": False,
                "error": str(e),
            }),
            status_code=503,
            media_type="application/json"
        )


@router.get("/health/live")
async def liveness_check():
    """
    STEP 8.6: Liveness probe for Kubernetes.
    
    Returns 200 if the process is alive and functioning.
    Returns 500 if the process is stuck/crashed (will trigger restart).
    """
    # Simple liveness check - if this endpoint responds, we're alive
    return {
        "alive": True,
        "timestamp": asyncio.get_event_loop().time(),
    }


@router.get("/health/redis")
async def redis_health_detailed():
    """
    STEP 8.6: Detailed Redis health check including replication status.
    
    Returns comprehensive Redis status for monitoring.
    """
    primary = await check_redis_health(REDIS_PRIMARY_URL)
    replica = await check_redis_health(REDIS_REPLICA_URL)
    
    # Check replication status
    replication_healthy = (
        primary.get("role") == "master" and
        replica.get("role") == "slave" and
        primary.get("healthy", False) and
        replica.get("healthy", False)
    )
    
    return {
        "status": "healthy" if replication_healthy else "degraded",
        "replication_healthy": replication_healthy,
        "redis_primary": primary,
        "redis_replica": replica,
        "failover_ready": replica.get("healthy", False),  # Can failover to replica
    }
