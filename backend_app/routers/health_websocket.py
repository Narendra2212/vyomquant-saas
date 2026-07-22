"""
routers/health_websocket.py — WebSocket Health Check Endpoint (STEP 8.7)

PHASE 8: MONITORING + ALERTING + FAILSAFE INFRASTRUCTURE
STEP 8.7: WebSocket Monitor - Data Stream Reliability

Purpose:
  - Provide /health/websockets endpoint for WebSocket connection status
  - Check for stale connections (> 10 seconds)
  - Return appropriate status codes for alerting

Usage:
    GET /health/websockets - Returns WebSocket connection status
"""

import asyncio
import logging

from fastapi import APIRouter, Response

from backend_app.backend.websocket_monitor import get_websocket_monitor

logger = logging.getLogger("WebSocketHealth")

router = APIRouter()


@router.get("/health/websockets")
async def websocket_health_check():
    """
    STEP 8.7: WebSocket connection health check.
    
    Returns status of all monitored WebSocket connections.
    Returns 503 if any connections are stale (> 10 seconds).
    
    Response:
        {
            "timestamp": 1234567890,
            "summary": {
                "total_connections": 5,
                "connected": 4,
                "stale": 1,
                "disconnected": 0,
                "healthy_percentage": 80.0
            },
            "connections": {
                "binance_btc": {
                    "state": "stale",
                    "is_stale": true,
                    "seconds_since_last_message": 15.2,
                    ...
                },
                ...
            },
            "stale_detected": true
        }
    """
    try:
        monitor = get_websocket_monitor()
        summary = monitor.get_summary()
        all_status = monitor.get_all_status()
        
        # Check if any connections are stale
        stale_count = summary.get("stale", 0)
        
        result = {
            "timestamp": asyncio.get_event_loop().time(),
            "summary": summary,
            "connections": all_status,
            "stale_detected": stale_count > 0,
        }
        
        if stale_count > 0:
            logger.warning(
                f"STEP 8.7: WebSocket health check - {stale_count} stale connections detected"
            )
            return Response(
                content=str(result),
                status_code=503,
                media_type="application/json"
            )
        
        return result
        
    except Exception as e:
        logger.error(f"STEP 8.7: WebSocket health check failed: {e}")
        return Response(
            content=str({
                "error": str(e),
                "timestamp": asyncio.get_event_loop().time(),
            }),
            status_code=500,
            media_type="application/json"
        )
