"""
Distributed Execution API Router

REST API endpoints for the distributed execution system.
Provides job submission, monitoring, and management capabilities.

Author: Principal Distributed Trading Systems Architect
"""

import asyncio
import logging
from decimal import Decimal
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

from backend_app.core.dependencies import get_current_user
from backend_app.backend.distributed_execution import (
    ExecutionOrchestrator, JobStatus, JobPriority, execution_orchestrator,
    job_persistence
)

router = APIRouter()
logger = logging.getLogger("distributed_execution_api")


# Pydantic Models
class ExecutionRequest(BaseModel):
    """Request model for job submission."""
    tenant_id: str = Field(..., description="Tenant identifier")
    strategy_id: str = Field(..., description="Strategy identifier")
    bot_id: str = Field(..., description="Bot identifier")
    signal_id: str = Field(..., description="Signal identifier")
    exchange: str = Field(..., description="Exchange name")
    symbol: str = Field(..., description="Trading symbol")
    side: str = Field(..., description="Order side: buy/sell")
    order_type: str = Field(..., description="Order type: market/limit")
    quantity: str = Field(..., description="Order quantity")
    price: Optional[str] = Field(None, description="Order price (for limit orders)")
    priority: str = Field("normal", description="Job priority: low/normal/high/critical")
    idempotency_key: Optional[str] = Field(None, description="Idempotency key")


class JobStatusResponse(BaseModel):
    """Response model for job status."""
    job_id: str
    status: str
    worker_id: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    order_id: Optional[str]
    execution_price: Optional[str]
    executed_quantity: Optional[str]
    fees: Optional[str]
    error: Optional[str]
    retry_count: int


class SystemStatusResponse(BaseModel):
    """Response model for system status."""
    running: bool
    workers: Dict[str, Any]
    queue_depth: int
    stats: Dict[str, Any]
    job_statistics: Dict[str, Any]
    uptime: float
    timestamp: str


# Helper Functions
def parse_priority(priority_str: str) -> JobPriority:
    """Parse priority string to enum."""
    priority_map = {
        "low": JobPriority.LOW,
        "normal": JobPriority.NORMAL,
        "high": JobPriority.HIGH,
        "critical": JobPriority.CRITICAL
    }
    return priority_map.get(priority_str.lower(), JobPriority.NORMAL)


# API Endpoints
@router.post("/submit", response_model=Dict[str, str])
async def submit_execution_job(
    request: ExecutionRequest,
    user: dict = Depends(get_current_user)
):
    """
    Submit execution job to distributed queue.
    
    This endpoint replaces direct order execution with queue-based
    distributed processing for institutional-grade reliability.
    """
    try:
        # Validate tenant access
        if request.tenant_id != user["tenant_id"]:
            raise HTTPException(
                status_code=403,
                detail="Access denied: tenant mismatch"
            )
        
        # Parse quantity and price
        quantity = Decimal(request.quantity)
        price = Decimal(request.price) if request.price else None
        
        # Parse priority
        priority = parse_priority(request.priority)
        
        # Submit job to orchestrator
        job_id = await execution_orchestrator.submit_execution_job(
            tenant_id=request.tenant_id,
            strategy_id=request.strategy_id,
            bot_id=request.bot_id,
            signal_id=request.signal_id,
            exchange=request.exchange,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=quantity,
            price=price,
            priority=priority,
            idempotency_key=request.idempotency_key
        )
        
        return {
            "job_id": job_id,
            "status": "submitted",
            "message": "Job submitted to distributed execution queue"
        }
        
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid input: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Failed to submit execution job: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to submit execution job"
        )


@router.get("/job/{job_id}/status", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    user: dict = Depends(get_current_user)
):
    """Get status of a specific execution job."""
    try:
        status = await execution_orchestrator.get_job_status(job_id)
        if not status:
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )
        
        # Verify tenant access
        job = await job_persistence.load_job(job_id)
        if job and job.tenant_id != user["tenant_id"]:
            raise HTTPException(
                status_code=403,
                detail="Access denied: tenant mismatch"
            )
        
        return JobStatusResponse(**status)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job status: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get job status"
        )


@router.get("/jobs", response_model=List[Dict[str, Any]])
async def get_tenant_jobs(
    status: Optional[str] = Query(None, description="Filter by job status"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    user: dict = Depends(get_current_user)
):
    """Get jobs for the current tenant."""
    try:
        # Parse status filter
        job_status = None
        if status:
            try:
                job_status = JobStatus(status)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status}"
                )
        
        # Get jobs
        jobs = await execution_orchestrator.get_tenant_jobs(
            tenant_id=user["tenant_id"],
            status=job_status,
            limit=limit,
            offset=offset
        )
        
        return jobs
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get tenant jobs: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get tenant jobs"
        )


@router.get("/system/status", response_model=SystemStatusResponse)
async def get_system_status(user: dict = Depends(get_current_user)):
    """Get distributed execution system status (admin only)."""
    try:
        # Check admin permissions
        if user.get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail="Admin access required"
            )
        
        status = await execution_orchestrator.get_system_status()
        return SystemStatusResponse(**status)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get system status: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get system status"
        )


@router.post("/job/{job_id}/replay", response_model=Dict[str, str])
async def replay_job(
    job_id: str,
    user: dict = Depends(get_current_user)
):
    """Replay a failed job."""
    try:
        # Verify tenant access
        job = await job_persistence.load_job(job_id)
        if not job:
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )
        
        if job.tenant_id != user["tenant_id"]:
            raise HTTPException(
                status_code=403,
                detail="Access denied: tenant mismatch"
            )
        
        # Check if job can be replayed
        if job.status not in [JobStatus.FAILED, JobStatus.DEAD_LETTER]:
            raise HTTPException(
                status_code=400,
                detail="Job cannot be replayed"
            )
        
        # Replay job
        new_job_id = await execution_orchestrator.replay_job(job_id)
        if not new_job_id:
            raise HTTPException(
                status_code=500,
                detail="Failed to replay job"
            )
        
        return {
            "original_job_id": job_id,
            "new_job_id": new_job_id,
            "status": "replayed",
            "message": "Job replayed successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to replay job: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to replay job"
        )


@router.get("/statistics")
async def get_job_statistics(
    tenant_id: Optional[str] = Query(None, description="Filter by tenant (admin only)"),
    user: dict = Depends(get_current_user)
):
    """Get job statistics."""
    try:
        # Verify tenant access
        target_tenant_id = user["tenant_id"]
        if tenant_id and user.get("role") != "admin":
            raise HTTPException(
                status_code=403,
                detail="Admin access required for tenant filtering"
            )
        
        if tenant_id and user.get("role") == "admin":
            target_tenant_id = tenant_id
        
        stats = await job_persistence.get_job_statistics(target_tenant_id)
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job statistics: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get job statistics"
        )


@router.get("/audit/{job_id}")
async def get_job_audit_trail(
    job_id: str,
    user: dict = Depends(get_current_user)
):
    """Get audit trail for a job."""
    try:
        # Verify tenant access
        job = await job_persistence.load_job(job_id)
        if not job:
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )
        
        if job.tenant_id != user["tenant_id"]:
            raise HTTPException(
                status_code=403,
                detail="Access denied: tenant mismatch"
            )
        
        # Get audit trail
        audit_trail = await job_persistence.get_job_audit_trail(job_id)
        
        return {
            "job_id": job_id,
            "audit_trail": audit_trail
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get audit trail: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get audit trail"
        )
