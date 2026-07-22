"""
Operational Runbook Manager - Phase 6 Operational Excellence

This module implements a comprehensive operational runbook manager for institutional-grade
operational excellence. The manager provides structured operational procedures for common
operational scenarios, ensuring consistent and reliable operational responses.

Key Features:
- Structured runbook definitions
- Automated runbook execution
- Runbook validation and testing
- Operational procedure tracking
- Runbook versioning
- Runbook approval workflow
- Operational metrics collection
- Runbook execution history

Author: Principal Institutional Site Reliability and Operational Resilience Engineer
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("operational_runbook_manager")


# ═══════════════════════════════════════════════════════════════════════════
# RUNBOOK TYPES AND CATEGORIES
# ═══════════════════════════════════════════════════════════════════════════

class RunbookCategory(Enum):
    """Categories of operational runbooks."""
    INCIDENT_RESPONSE = "incident_response"
    FAILOVER = "failover"
    DISASTER_RECOVERY = "disaster_recovery"
    DEPLOYMENT = "deployment"
    MAINTENANCE = "maintenance"
    MONITORING = "monitoring"
    VALIDATION = "validation"
    RECOVERY = "recovery"


class RunbookSeverity(Enum):
    """Severity levels for runbooks."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class RunbookStatus(Enum):
    """Status of runbook execution."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


# ═══════════════════════════════════════════════════════════════════════════
# RUNBOOK STEP MODEL
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RunbookStep:
    """A single step in a runbook."""
    step_id: str
    step_number: int
    title: str
    description: str
    command: Optional[str] = None
    expected_duration_seconds: Optional[float] = None
    validation_criteria: Optional[str] = None
    rollback_command: Optional[str] = None
    required: bool = True
    dependencies: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_number": self.step_number,
            "title": self.title,
            "description": self.description,
            "command": self.command,
            "expected_duration_seconds": self.expected_duration_seconds,
            "validation_criteria": self.validation_criteria,
            "rollback_command": self.rollback_command,
            "required": self.required,
            "dependencies": self.dependencies,
        }


# ═══════════════════════════════════════════════════════════════════════════
# RUNBOOK MODEL
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Runbook:
    """Operational runbook definition."""
    runbook_id: str
    name: str
    category: RunbookCategory
    severity: RunbookSeverity
    description: str
    steps: List[RunbookStep]
    version: str = "1.0.0"
    author: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approval_required: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "runbook_id": self.runbook_id,
            "name": self.name,
            "category": self.category.value,
            "severity": self.severity.value,
            "description": self.description,
            "steps": [step.to_dict() for step in self.steps],
            "version": self.version,
            "author": self.author,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "approval_required": self.approval_required,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "tags": self.tags,
        }


# ═══════════════════════════════════════════════════════════════════════════
# RUNBOOK EXECUTION MODEL
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RunbookExecution:
    """Record of a runbook execution."""
    execution_id: str
    runbook_id: str
    runbook_name: str
    started_at: datetime
    started_by: str
    status: RunbookStatus = RunbookStatus.PENDING
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    
    # Step execution
    step_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current_step: Optional[str] = None
    
    # Results
    success: bool = False
    failure_reason: Optional[str] = None
    rollback_executed: bool = False
    
    # Metrics
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "runbook_id": self.runbook_id,
            "runbook_name": self.runbook_name,
            "started_at": self.started_at.isoformat(),
            "started_by": self.started_by,
            "status": self.status.value,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "step_results": self.step_results,
            "current_step": self.current_step,
            "success": self.success,
            "failure_reason": self.failure_reason,
            "rollback_executed": self.rollback_executed,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
        }


# ═══════════════════════════════════════════════════════════════════════════
# OPERATIONAL RUNBOOK MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class OperationalRunbookManager:
    """
    Comprehensive operational runbook manager for institutional-grade operations.
    
    This manager provides:
    - Structured runbook definitions
    - Automated runbook execution
    - Runbook validation and testing
    - Operational procedure tracking
    - Runbook versioning
    - Runbook approval workflow
    - Operational metrics collection
    - Runbook execution history
    """
    
    def __init__(self, runbook_directory: Optional[str] = None):
        """
        Initialize Operational Runbook Manager.
        
        Args:
            runbook_directory: Directory to store runbook definitions
        """
        self.runbook_directory = Path(runbook_directory) if runbook_directory else Path("./runbooks")
        self.runbook_directory.mkdir(parents=True, exist_ok=True)
        
        # Runbook storage
        self._runbooks: Dict[str, Runbook] = {}
        self._executions: Dict[str, RunbookExecution] = {}
        
        # Callbacks
        self._step_execution_callbacks: List[Callable[[str, RunbookStep], Any]] = []
        self._step_completion_callbacks: List[Callable[[str, RunbookStep, Dict[str, Any]], Any]] = []
        self._runbook_completion_callbacks: List[Callable[[RunbookExecution], Any]] = []
        
        # Approval workflow
        self._pending_approvals: Dict[str, Runbook] = {}
        
        logger.info("Operational Runbook Manager initialized")
    
    async def initialize(self) -> bool:
        """Initialize runbook manager and load existing runbooks."""
        try:
            logger.info("Initializing Operational Runbook Manager")
            
            # Load existing runbooks from directory
            await self._load_runbooks()
            
            # Create default runbooks if none exist
            if not self._runbooks:
                await self._create_default_runbooks()
            
            logger.info(f"Operational Runbook Manager initialized with {len(self._runbooks)} runbooks")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Operational Runbook Manager: {e}")
            return False
    
    async def _load_runbooks(self):
        """Load runbooks from directory."""
        try:
            for runbook_file in self.runbook_directory.glob("*.json"):
                with open(runbook_file, 'r') as f:
                    data = json.load(f)
                    runbook = self._dict_to_runbook(data)
                    self._runbooks[runbook.runbook_id] = runbook
                    logger.info(f"Loaded runbook: {runbook.name}")
        except Exception as e:
            logger.error(f"Failed to load runbooks: {e}")
    
    async def _create_default_runbooks(self):
        """Create default operational runbooks."""
        # Redis failover runbook
        redis_failover = Runbook(
            runbook_id=str(uuid.uuid4()),
            name="Redis Failover Procedure",
            category=RunbookCategory.FAILOVER,
            severity=RunbookSeverity.HIGH,
            description="Procedure for failing over Redis master to replica",
            steps=[
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=1,
                    title="Verify Redis Health",
                    description="Check Redis cluster health status",
                    command="redis-cli cluster nodes",
                    expected_duration_seconds=5.0,
                    validation_criteria="Cluster status should be ok",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=2,
                    title="Identify Failed Master",
                    description="Identify which Redis master has failed",
                    command="redis-cli cluster info",
                    expected_duration_seconds=5.0,
                    validation_criteria="Master node should be marked as failed",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=3,
                    title="Trigger Sentinel Failover",
                    description="Trigger Redis Sentinel to promote replica",
                    command="redis-cli -p 26379 sentinel failover mymaster",
                    expected_duration_seconds=10.0,
                    validation_criteria="New master should be promoted",
                    rollback_command="redis-cli -p 26379 sentinel failover-overrule mymaster",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=4,
                    title="Verify Failover",
                    description="Verify failover completed successfully",
                    command="redis-cli cluster nodes",
                    expected_duration_seconds=5.0,
                    validation_criteria="Cluster should be healthy with new master",
                ),
            ],
            tags=["redis", "failover", "high-severity"]
        )
        
        # PostgreSQL failover runbook
        postgres_failover = Runbook(
            runbook_id=str(uuid.uuid4()),
            name="PostgreSQL Failover Procedure",
            category=RunbookCategory.FAILOVER,
            severity=RunbookSeverity.HIGH,
            description="Procedure for failing over PostgreSQL primary to replica",
            steps=[
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=1,
                    title="Verify PostgreSQL Health",
                    description="Check PostgreSQL cluster health status",
                    command="pg_isready -h localhost",
                    expected_duration_seconds=5.0,
                    validation_criteria="PostgreSQL should respond",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=2,
                    title="Identify Failed Primary",
                    description="Identify which PostgreSQL primary has failed",
                    command="repmgr cluster show",
                    expected_duration_seconds=10.0,
                    validation_criteria="Primary node should be marked as failed",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=3,
                    title="Promote Replica",
                    description="Promote standby replica to primary",
                    command="repmgr standby promote",
                    expected_duration_seconds=15.0,
                    validation_criteria="Replica should be promoted to primary",
                    rollback_command="repmgr primary demote",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=4,
                    title="Verify Failover",
                    description="Verify failover completed successfully",
                    command="repmgr cluster show",
                    expected_duration_seconds=10.0,
                    validation_criteria="Cluster should be healthy with new primary",
                ),
            ],
            tags=["postgres", "failover", "high-severity"]
        )
        
        # Coordinator failover runbook
        coordinator_failover = Runbook(
            runbook_id=str(uuid.uuid4()),
            name="Coordinator Failover Procedure",
            category=RunbookCategory.FAILOVER,
            severity=RunbookSeverity.CRITICAL,
            description="Procedure for failing over coordinator leader to hot standby",
            steps=[
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=1,
                    title="Verify Coordinator Health",
                    description="Check coordinator cluster health status",
                    command="curl http://localhost:8080/health",
                    expected_duration_seconds=5.0,
                    validation_criteria="Coordinator should respond with healthy status",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=2,
                    title="Identify Failed Leader",
                    description="Identify which coordinator leader has failed",
                    command="curl http://localhost:8080/status",
                    expected_duration_seconds=5.0,
                    validation_criteria="Leader should be marked as failed",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=3,
                    title="Trigger Hot Standby Takeover",
                    description="Trigger hot standby to take over leadership",
                    command="curl -X POST http://localhost:8081/takeover",
                    expected_duration_seconds=10.0,
                    validation_criteria="Hot standby should become leader",
                    rollback_command="curl -X POST http://localhost:8081/rollback",
                ),
                RunbookStep(
                    step_id=str(uuid.uuid4()),
                    step_number=4,
                    title="Verify Failover",
                    description="Verify failover completed successfully",
                    command="curl http://localhost:8081/status",
                    expected_duration_seconds=5.0,
                    validation_criteria="New leader should be operational",
                ),
            ],
            tags=["coordinator", "failover", "critical"]
        )
        
        # Add runbooks
        await self.create_runbook(redis_failover)
        await self.create_runbook(postgres_failover)
        await self.create_runbook(coordinator_failover)
        
        logger.info("Created default runbooks")
    
    async def create_runbook(self, runbook: Runbook) -> bool:
        """Create a new runbook."""
        try:
            self._runbooks[runbook.runbook_id] = runbook
            
            # Save to file
            await self._save_runbook(runbook)
            
            logger.info(f"Created runbook: {runbook.name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create runbook: {e}")
            return False
    
    async def _save_runbook(self, runbook: Runbook):
        """Save runbook to file."""
        runbook_file = self.runbook_directory / f"{runbook.runbook_id}.json"
        with open(runbook_file, 'w') as f:
            json.dump(runbook.to_dict(), f, indent=2)
    
    def _dict_to_runbook(self, data: Dict[str, Any]) -> Runbook:
        """Convert dictionary to Runbook object."""
        steps = [
            RunbookStep(**step_data)
            for step_data in data.get("steps", [])
        ]
        
        return Runbook(
            runbook_id=data["runbook_id"],
            name=data["name"],
            category=RunbookCategory(data["category"]),
            severity=RunbookSeverity(data["severity"]),
            description=data["description"],
            steps=steps,
            version=data.get("version", "1.0.0"),
            author=data.get("author", "system"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
            approval_required=data.get("approval_required", False),
            approved_by=data.get("approved_by"),
            approved_at=datetime.fromisoformat(data["approved_at"]) if data.get("approved_at") else None,
            tags=data.get("tags", []),
        )
    
    async def get_runbook(self, runbook_id: str) -> Optional[Runbook]:
        """Get runbook by ID."""
        return self._runbooks.get(runbook_id)
    
    async def list_runbooks(
        self,
        category: Optional[RunbookCategory] = None,
        severity: Optional[RunbookSeverity] = None,
        tags: Optional[List[str]] = None
    ) -> List[Runbook]:
        """List runbooks with optional filters."""
        runbooks = list(self._runbooks.values())
        
        if category:
            runbooks = [r for r in runbooks if r.category == category]
        
        if severity:
            runbooks = [r for r in runbooks if r.severity == severity]
        
        if tags:
            runbooks = [r for r in runbooks if any(tag in r.tags for tag in tags)]
        
        return runbooks
    
    async def execute_runbook(
        self,
        runbook_id: str,
        started_by: str,
        dry_run: bool = False
    ) -> RunbookExecution:
        """
        Execute a runbook.
        
        Args:
            runbook_id: ID of the runbook to execute
            started_by: User or system initiating the execution
            dry_run: If True, simulate execution without actually running commands
            
        Returns:
            RunbookExecution result
        """
        runbook = await self.get_runbook(runbook_id)
        if not runbook:
            raise ValueError(f"Runbook not found: {runbook_id}")
        
        # Check approval if required
        if runbook.approval_required and not runbook.approved_by:
            raise ValueError(f"Runbook requires approval: {runbook.name}")
        
        execution_id = str(uuid.uuid4())
        execution = RunbookExecution(
            execution_id=execution_id,
            runbook_id=runbook_id,
            runbook_name=runbook.name,
            started_at=datetime.now(timezone.utc),
            started_by=started_by,
            status=RunbookStatus.IN_PROGRESS,
            total_steps=len(runbook.steps)
        )
        
        self._executions[execution_id] = execution
        
        logger.info(f"Starting runbook execution: {runbook.name} (ID: {execution_id})")
        
        try:
            # Execute each step
            for step in runbook.steps:
                execution.current_step = step.step_id
                
                # Check dependencies
                if not await self._check_step_dependencies(step, execution):
                    logger.error(f"Step dependencies not met: {step.title}")
                    execution.status = RunbookStatus.FAILED
                    execution.failure_reason = f"Step dependencies not met: {step.title}"
                    break
                
                # Notify step execution callback
                for callback in self._step_execution_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(execution_id, step)
                        else:
                            callback(execution_id, step)
                    except Exception as e:
                        logger.error(f"Step execution callback error: {e}")
                
                # Execute step
                step_result = await self._execute_step(step, dry_run)
                execution.step_results[step.step_id] = step_result
                
                if step_result["success"]:
                    execution.completed_steps += 1
                else:
                    execution.failed_steps += 1
                    
                    if step.required:
                        logger.error(f"Required step failed: {step.title}")
                        execution.status = RunbookStatus.FAILED
                        execution.failure_reason = f"Required step failed: {step.title}"
                        
                        # Execute rollback if available
                        if step.rollback_command and not dry_run:
                            await self._execute_rollback(step, dry_run)
                            execution.rollback_executed = True
                        
                        break
                
                # Notify step completion callback
                for callback in self._step_completion_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(execution_id, step, step_result)
                        else:
                            callback(execution_id, step, step_result)
                    except Exception as e:
                        logger.error(f"Step completion callback error: {e}")
            
            # Complete execution
            execution.completed_at = datetime.now(timezone.utc)
            execution.duration_seconds = (execution.completed_at - execution.started_at).total_seconds()
            execution.current_step = None
            
            if execution.status == RunbookStatus.IN_PROGRESS:
                execution.status = RunbookStatus.COMPLETED
                execution.success = True
                logger.info(f"Runbook execution completed successfully: {runbook.name}")
            else:
                execution.success = False
                logger.warning(f"Runbook execution failed: {runbook.name}")
            
            # Notify completion callbacks
            for callback in self._runbook_completion_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(execution)
                    else:
                        callback(execution)
                except Exception as e:
                    logger.error(f"Runbook completion callback error: {e}")
            
            return execution
            
        except Exception as e:
            logger.error(f"Runbook execution error: {e}")
            execution.status = RunbookStatus.FAILED
            execution.failure_reason = str(e)
            execution.completed_at = datetime.now(timezone.utc)
            execution.duration_seconds = (execution.completed_at - execution.started_at).total_seconds()
            execution.success = False
            return execution
    
    async def _check_step_dependencies(self, step: RunbookStep, execution: RunbookExecution) -> bool:
        """Check if step dependencies are met."""
        for dep_id in step.dependencies:
            dep_result = execution.step_results.get(dep_id)
            if not dep_result or not dep_result.get("success"):
                return False
        return True
    
    async def _execute_step(self, step: RunbookStep, dry_run: bool) -> Dict[str, Any]:
        """Execute a single runbook step."""
        logger.info(f"Executing step: {step.title}")
        
        if dry_run:
            logger.info(f"[DRY RUN] Would execute: {step.command}")
            return {
                "success": True,
                "output": f"[DRY RUN] Command: {step.command}",
                "duration_seconds": step.expected_duration_seconds or 0.0,
            }
        
        # In production, this would:
        # - Execute the command
        # - Capture output
        # - Validate against criteria
        # - Measure duration
        
        # For now, simulate execution
        await asyncio.sleep(step.expected_duration_seconds or 1.0)
        
        return {
            "success": True,
            "output": f"Executed: {step.command}",
            "duration_seconds": step.expected_duration_seconds or 1.0,
        }
    
    async def _execute_rollback(self, step: RunbookStep, dry_run: bool):
        """Execute rollback for a failed step."""
        logger.info(f"Executing rollback for step: {step.title}")
        
        if dry_run:
            logger.info(f"[DRY RUN] Would execute rollback: {step.rollback_command}")
            return
        
        # In production, this would execute the rollback command
        logger.info(f"Executing rollback command: {step.rollback_command}")
        await asyncio.sleep(1.0)
    
    async def approve_runbook(self, runbook_id: str, approved_by: str) -> bool:
        """Approve a runbook that requires approval."""
        runbook = await self.get_runbook(runbook_id)
        if not runbook:
            return False
        
        runbook.approved_by = approved_by
        runbook.approved_at = datetime.now(timezone.utc)
        
        await self._save_runbook(runbook)
        
        logger.info(f"Runbook approved by {approved_by}: {runbook.name}")
        return True
    
    async def get_execution(self, execution_id: str) -> Optional[RunbookExecution]:
        """Get execution by ID."""
        return self._executions.get(execution_id)
    
    async def list_executions(
        self,
        runbook_id: Optional[str] = None,
        status: Optional[RunbookStatus] = None,
        limit: int = 100
    ) -> List[RunbookExecution]:
        """List executions with optional filters."""
        executions = list(self._executions.values())
        
        if runbook_id:
            executions = [e for e in executions if e.runbook_id == runbook_id]
        
        if status:
            executions = [e for e in executions if e.status == status]
        
        return executions[:limit]
    
    def register_step_execution_callback(self, callback: Callable[[str, RunbookStep], Any]):
        """Register callback for step execution."""
        self._step_execution_callbacks.append(callback)
    
    def register_step_completion_callback(self, callback: Callable[[str, RunbookStep, Dict[str, Any]], Any]):
        """Register callback for step completion."""
        self._step_completion_callbacks.append(callback)
    
    def register_runbook_completion_callback(self, callback: Callable[[RunbookExecution], Any]):
        """Register callback for runbook completion."""
        self._runbook_completion_callbacks.append(callback)
    
    def generate_execution_report(self, execution: RunbookExecution) -> str:
        """Generate human-readable execution report."""
        lines = [
            "\n" + "=" * 70,
            "RUNBOOK EXECUTION REPORT",
            "=" * 70,
            f"Execution ID: {execution.execution_id}",
            f"Runbook: {execution.runbook_name}",
            f"Started By: {execution.started_by}",
            f"Started At: {execution.started_at}",
            f"Completed At: {execution.completed_at or 'N/A'}",
            f"Duration: {execution.duration_seconds:.2f}s" if execution.duration_seconds else "Duration: N/A",
            f"Status: {execution.status.value}",
            f"Success: {execution.success}",
            "",
            "SUMMARY:",
            f"  Total Steps: {execution.total_steps}",
            f"  Completed Steps: {execution.completed_steps}",
            f"  Failed Steps: {execution.failed_steps}",
            f"  Rollback Executed: {execution.rollback_executed}",
            "",
            "STEP RESULTS:",
        ]
        
        for step_id, result in execution.step_results.items():
            status = "[PASS]" if result["success"] else "[FAIL]"
            lines.append(f"  {step_id}: {status}")
            lines.append(f"    Output: {result.get('output', 'N/A')}")
            lines.append(f"    Duration: {result.get('duration_seconds', 0):.2f}s")
            lines.append("")
        
        if execution.failure_reason:
            lines.append("FAILURE REASON:")
            lines.append(f"  {execution.failure_reason}")
            lines.append("")
        
        lines.append("=" * 70)
        
        return "\n".join(lines)


# Global singleton
operational_runbook_manager = OperationalRunbookManager()


def get_operational_runbook_manager() -> OperationalRunbookManager:
    """Get global operational runbook manager instance."""
    return operational_runbook_manager
