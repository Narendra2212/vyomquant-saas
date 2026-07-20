"""
Transactional Execution Manager

Institutional-grade transactional execution manager for atomic execution flow.

This module provides:
- Full transactional execution flow
- Order persistence atomicity
- Fill persistence atomicity
- Retry persistence atomicity
- Replay persistence atomicity
- Transactional rollback
- Transactional retry safety
- Replay-safe rollback
- Failover-safe persistence

Author: Principal Institutional Execution Consistency Engineer
"""

import asyncio
import logging
import json
import uuid
import hashlib
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("TransactionalExecutionManager")


# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTION STATE
# ═══════════════════════════════════════════════════════════════════════════

class TransactionState(Enum):
    """Transaction lifecycle states."""
    PENDING = "pending"
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    REPLAYING = "replaying"
    REPLAYED = "replayed"


# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTION METADATA
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TransactionMetadata:
    """Transaction metadata for replay safety."""
    transaction_id: str
    tenant_id: UUID
    user_id: Optional[UUID]
    strategy_id: str
    
    # Timing
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Replay safety
    is_replay_safe: bool = True
    is_idempotent: bool = True
    is_deterministic: bool = True
    
    # Checkpoint
    checkpoint_id: Optional[str] = None
    checkpoint_data: Optional[Dict[str, Any]] = None
    
    # Rollback
    rollback_id: Optional[str] = None
    rollback_data: Optional[Dict[str, Any]] = None
    
    # Validation
    checksum: str = ""
    version: str = "1.0"


# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTION OPERATION
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TransactionOperation:
    """Single operation within a transaction."""
    operation_id: str
    operation_type: str  # insert, update, delete, execute
    table_name: str
    data: Dict[str, Any]
    
    # Pre-state
    pre_state: Optional[Dict[str, Any]] = None
    
    # Post-state
    post_state: Optional[Dict[str, Any]] = None
    
    # Idempotency
    idempotency_key: Optional[str] = None
    is_idempotent: bool = True
    
    # Execution
    executed: bool = False
    executed_at: Optional[datetime] = None
    execution_result: Optional[Dict[str, Any]] = None


# ═══════════════════════════════════════════════════════════════════════════
# REPLAY-SAFE TRANSACTION
# ═══════════════════════════════════════════════════════════════════════════

class ReplaySafeTransaction:
    """Replay-safe transaction manager."""
    
    def __init__(self, db: Session, tenant_id: UUID, exchange_adapter=None):
        self.db = db
        self.tenant_id = tenant_id
        self.exchange_adapter = exchange_adapter
        self.transaction_id = str(uuid4())
        self.state = TransactionState.PENDING
        self.operations: List[TransactionOperation] = []
        self.metadata = TransactionMetadata(
            transaction_id=self.transaction_id,
            tenant_id=tenant_id,
            user_id=None,
            strategy_id="",
            created_at=datetime.utcnow()
        )
    
    async def begin(self):
        """Begin transaction."""
        try:
            self.db.execute(
                text("SET LOCAL app.current_tenant_id = :tenant_id"),
                {"tenant_id": str(self.tenant_id)}
            )
        except Exception as e:
            # Ignore sqlite operational error
            logger.debug(f"[Transaction] SET LOCAL not supported in current DB dialect: {e}")
        
        # Begin transaction
        try:
            self.db.begin()
        except Exception as e:
            if "already begun" not in str(e):
                logger.error(f"[Transaction] Failed to begin transaction: {e}")
                raise
        
        # Update state
        self.state = TransactionState.ACTIVE
        self.metadata.started_at = datetime.utcnow()
        
        # Create checkpoint
        await self._create_checkpoint()
        
        logger.info(f"[Transaction] {self.transaction_id} begun")
    
    async def commit(self):
        """Commit transaction."""
        # Validate operations
        await self._validate_operations()
        
        # Calculate checksum
        self.metadata.checksum = self._calculate_checksum()
        
        # Commit transaction
        self.db.commit()
        
        # Update state
        self.state = TransactionState.COMMITTED
        self.metadata.completed_at = datetime.utcnow()
        
        # Save transaction record
        await self._save_transaction_record()
        
        logger.info(f"[Transaction] {self.transaction_id} committed")
    
    async def rollback(self):
        """Rollback transaction."""
        # Rollback database transaction
        self.db.rollback()
        
        # Restore from checkpoint
        await self._restore_checkpoint()
        
        # Update state
        self.state = TransactionState.ROLLED_BACK
        self.metadata.completed_at = datetime.utcnow()
        
        # Save rollback record
        await self._save_rollback_record()
        
        logger.info(f"[Transaction] {self.transaction_id} rolled back")
    
    async def add_operation(
        self,
        operation_type: str,
        table_name: str,
        data: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ):
        """Add operation to transaction."""
        # Capture pre-state
        pre_state = await self._capture_pre_state(table_name, data)
        
        # Create operation
        operation = TransactionOperation(
            operation_id=str(uuid4()),
            operation_type=operation_type,
            table_name=table_name,
            data=data,
            pre_state=pre_state,
            idempotency_key=idempotency_key
        )
        
        # Add to operations
        self.operations.append(operation)
        
        logger.debug(f"[Transaction] Operation added: {operation.operation_id}")
    
    async def execute_operation(self, operation: TransactionOperation):
        """Execute single operation."""
        # Check idempotency
        if operation.idempotency_key:
            existing = await self._check_idempotency(operation.idempotency_key)
            if existing:
                logger.info(f"[Transaction] Idempotent operation skipped: {operation.operation_id}")
                operation.execution_result = existing
                operation.executed = True
                operation.executed_at = datetime.utcnow()
                return
        
        # Execute operation
        result = await self._execute_operation_impl(operation)
        
        # Capture post-state
        post_state = await self._capture_post_state(operation.table_name, operation.data)
        operation.post_state = post_state
        
        # Update operation
        operation.execution_result = result
        operation.executed = True
        operation.executed_at = datetime.utcnow()
        
        logger.debug(f"[Transaction] Operation executed: {operation.operation_id}")
    
    async def _create_checkpoint(self):
        """Create transaction checkpoint."""
        # Capture current state
        state_snapshot = await self._capture_state_snapshot()
        
        # Capture operations snapshot
        operations_snapshot = [op.__dict__ for op in self.operations]
        
        # Create checkpoint
        checkpoint = {
            "checkpoint_id": str(uuid4()),
            "transaction_id": self.transaction_id,
            "tenant_id": str(self.tenant_id),
            "state_snapshot": json.dumps(state_snapshot),
            "operations_snapshot": json.dumps(operations_snapshot),
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Calculate checksum
        checkpoint_data = json.dumps(checkpoint, sort_keys=True)
        checkpoint["checksum"] = hashlib.sha256(checkpoint_data.encode()).hexdigest()
        
        # Save checkpoint to database
        self.db.execute(
            text("""
                INSERT INTO transaction_checkpoints (checkpoint_id, transaction_id, tenant_id, state_snapshot, operations_snapshot, checksum, created_at)
                VALUES (:checkpoint_id, :transaction_id, :tenant_id, :state_snapshot, :operations_snapshot, :checksum, :created_at)
            """),
            checkpoint
        )
        
        # Update metadata
        self.metadata.checkpoint_id = checkpoint["checkpoint_id"]
        self.metadata.checkpoint_data = checkpoint
        
        logger.info(f"[Transaction] Checkpoint created: {checkpoint['checkpoint_id']}")
    
    async def _restore_checkpoint(self, checkpoint_id: Optional[str] = None):
        """Restore from checkpoint."""
        # Load checkpoint
        if checkpoint_id:
            result = self.db.execute(
                text("""
                    SELECT checkpoint_id, transaction_id, tenant_id, state_snapshot, operations_snapshot, checksum, created_at
                    FROM transaction_checkpoints
                    WHERE checkpoint_id = :checkpoint_id AND tenant_id = :tenant_id
                """),
                {"checkpoint_id": checkpoint_id, "tenant_id": str(self.tenant_id)}
            ).fetchone()
        else:
            result = self.db.execute(
                text("""
                    SELECT checkpoint_id, transaction_id, tenant_id, state_snapshot, operations_snapshot, checksum, created_at
                    FROM transaction_checkpoints
                    WHERE transaction_id = :transaction_id AND tenant_id = :tenant_id
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"transaction_id": self.transaction_id, "tenant_id": str(self.tenant_id)}
            ).fetchone()
        
        if not result:
            logger.error(f"[Transaction] Checkpoint not found")
            return False
        
        # Verify checkpoint integrity
        checkpoint_data = {
            "checkpoint_id": result[0],
            "transaction_id": result[1],
            "tenant_id": result[2],
            "state_snapshot": json.loads(result[3]),
            "operations_snapshot": json.loads(result[4]),
            "created_at": result[6].isoformat()
        }
        calculated_checksum = hashlib.sha256(json.dumps(checkpoint_data, sort_keys=True).encode()).hexdigest()
        
        if calculated_checksum != result[5]:
            logger.error(f"[Transaction] Checkpoint verification failed")
            return False
        
        # Restore operations
        self.operations = [
            TransactionOperation(**op) for op in checkpoint_data["operations_snapshot"]
        ]
        
        logger.info(f"[Transaction] Checkpoint restored: {result[0]}")
        return True
    
    async def _validate_operations(self):
        """Validate operations before commit."""
        # Check all operations executed
        for operation in self.operations:
            if not operation.executed:
                raise ValueError(f"Operation not executed: {operation.operation_id}")
        
        # Check idempotency
        for operation in self.operations:
            if operation.idempotency_key and not operation.is_idempotent:
                raise ValueError(f"Operation not idempotent: {operation.operation_id}")
        
        logger.info(f"[Transaction] Operations validated")
    
    def _calculate_checksum(self) -> str:
        """Calculate transaction checksum for integrity verification."""
        data = f"{self.transaction_id}:{json.dumps([op.__dict__ for op in self.operations], sort_keys=True, default=str)}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    async def _save_transaction_record(self):
        """Save transaction record."""
        self.db.execute(
            text("""
                INSERT INTO transaction_records (transaction_id, tenant_id, state, operations_count, checksum, created_at, started_at, completed_at)
                VALUES (:transaction_id, :tenant_id, :state, :operations_count, :checksum, :created_at, :started_at, :completed_at)
            """),
            {
                "transaction_id": self.transaction_id,
                "tenant_id": str(self.tenant_id),
                "state": self.state.value,
                "operations_count": len(self.operations),
                "checksum": self.metadata.checksum,
                "created_at": self.metadata.created_at,
                "started_at": self.metadata.started_at,
                "completed_at": self.metadata.completed_at
            }
        )
    
    async def _save_rollback_record(self):
        """Save rollback record."""
        self.db.execute(
            text("""
                INSERT INTO transaction_rollbacks (rollback_id, transaction_id, tenant_id, checkpoint_id, created_at)
                VALUES (:rollback_id, :transaction_id, :tenant_id, :checkpoint_id, :created_at)
            """),
            {
                "rollback_id": str(uuid4()),
                "transaction_id": self.transaction_id,
                "tenant_id": str(self.tenant_id),
                "checkpoint_id": self.metadata.checkpoint_id,
                "created_at": datetime.utcnow()
            }
        )
    
    async def _capture_pre_state(self, table_name: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Capture pre-state for operation."""
        try:
            # Extract primary key from data
            primary_key = data.get("id") or data.get("order_id") or data.get("execution_id")
            
            if primary_key:
                # Query current state for update/delete operations
                result = self.db.execute(
                    text(f"""
                        SELECT * FROM {table_name}
                        WHERE id = :primary_key AND tenant_id = :tenant_id
                    """),
                    {"primary_key": primary_key, "tenant_id": str(self.tenant_id)}
                ).fetchone()
                
                if result:
                    return dict(result._mapping)
            
            return None
        except Exception as e:
            logger.warning(f"[Transaction] Failed to capture pre-state: {e}")
            return None
    
    async def _capture_post_state(self, table_name: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Capture post-state for operation."""
        try:
            # Extract primary key from data
            primary_key = data.get("id") or data.get("order_id") or data.get("execution_id")
            
            if primary_key:
                # Query current state after operation
                result = self.db.execute(
                    text(f"""
                        SELECT * FROM {table_name}
                        WHERE id = :primary_key AND tenant_id = :tenant_id
                    """),
                    {"primary_key": primary_key, "tenant_id": str(self.tenant_id)}
                ).fetchone()
                
                if result:
                    return dict(result._mapping)
            
            return None
        except Exception as e:
            logger.warning(f"[Transaction] Failed to capture post-state: {e}")
            return None
    
    async def _capture_state_snapshot(self) -> Dict[str, Any]:
        """Capture current state snapshot."""
        try:
            # Capture relevant state for transaction checkpoint
            snapshot = {
                "tenant_id": str(self.tenant_id),
                "transaction_id": self.transaction_id,
                "timestamp": datetime.utcnow().isoformat(),
                "operations_count": len(self.operations)
            }
            
            # Capture operation states
            snapshot["operations"] = [
                {
                    "operation_id": op.operation_id,
                    "operation_type": op.operation_type,
                    "table_name": op.table_name,
                    "executed": op.executed
                }
                for op in self.operations
            ]
            
            return snapshot
        except Exception as e:
            logger.warning(f"[Transaction] Failed to capture state snapshot: {e}")
            return {
                "tenant_id": str(self.tenant_id),
                "transaction_id": self.transaction_id,
                "timestamp": datetime.utcnow().isoformat(),
                "error": str(e)
            }
    
    async def _check_idempotency(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Check if operation already executed."""
        result = self.db.execute(
            text("""
                SELECT result
                FROM idempotency_keys
                WHERE idempotency_key = :idempotency_key
                AND tenant_id = :tenant_id
                LIMIT 1
            """),
            {"idempotency_key": idempotency_key, "tenant_id": str(self.tenant_id)}
        ).fetchone()
        
        if result:
            return json.loads(result[0])
        
        return None
    
    async def _execute_operation_impl(self, operation: TransactionOperation) -> Dict[str, Any]:
        """Execute operation implementation."""
        # Execute operation based on type
        if operation.operation_type == "insert":
            record_id = operation.data.get("id") or str(uuid4())
            result = self.db.execute(
                text(f"""
                    INSERT INTO {operation.table_name}
                    (id, data, tenant_id, created_at)
                    VALUES (:id, :data, :tenant_id, :created_at)
                    RETURNING *
                """),
                {
                    "id": record_id,
                    "data": json.dumps(operation.data),
                    "tenant_id": str(self.tenant_id),
                    "created_at": datetime.utcnow()
                }
            ).fetchone()
            return {"operation_id": operation.operation_id, "status": "success", "result": dict(result._mapping) if result else {}}
        
        elif operation.operation_type == "update":
            # Extract primary key from data
            primary_key = operation.data.get("id") or operation.data.get("order_id") or operation.data.get("execution_id")
            if not primary_key:
                raise ValueError("Primary key required for update operation")
            
            result = self.db.execute(
                text(f"""
                    UPDATE {operation.table_name}
                    SET data = :data, updated_at = :updated_at
                    WHERE id = :primary_key AND tenant_id = :tenant_id
                    RETURNING *
                """),
                {
                    "data": json.dumps(operation.data),
                    "tenant_id": str(self.tenant_id),
                    "updated_at": datetime.utcnow(),
                    "primary_key": primary_key
                }
            ).fetchone()
            return {"operation_id": operation.operation_id, "status": "success", "result": dict(result._mapping) if result else {}}
        
        elif operation.operation_type == "delete":
            # Extract primary key from data
            primary_key = operation.data.get("id") or operation.data.get("order_id") or operation.data.get("execution_id")
            if not primary_key:
                raise ValueError("Primary key required for delete operation")
            
            result = self.db.execute(
                text(f"""
                    DELETE FROM {operation.table_name}
                    WHERE id = :primary_key AND tenant_id = :tenant_id
                    RETURNING *
                """),
                {
                    "tenant_id": str(self.tenant_id),
                    "primary_key": primary_key
                }
            ).fetchone()
            return {"operation_id": operation.operation_id, "status": "success", "result": dict(result._mapping) if result else {}}
        
        else:
            raise ValueError(f"Unknown operation type: {operation.operation_type}")


# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTIONAL EXECUTION MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class TransactionalExecutionManager:
    """Transactional execution manager for atomic execution flow."""
    
    def __init__(self, db: Session, tenant_id: UUID, exchange_adapter=None):
        self.db = db
        self.tenant_id = tenant_id
        self.exchange_adapter = exchange_adapter
        self.transaction = None
    
    async def reconcile_exchange_state(
        self,
        exchange_orders: List[Dict[str, Any]],
        exchange_positions: List[Dict[str, Any]],
        exchange_fills: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Reconcile missing orders, missing fills, and position drift natively inside
        the transactional manager engine.
        """
        transaction = ReplaySafeTransaction(self.db, self.tenant_id)
        self.transaction = transaction
        
        result = {
            "missing_orders_inserted": 0,
            "missing_fills_inserted": 0,
            "position_drift_adjustments": 0
        }
        
        try:
            await transaction.begin()
            
            # Local cache to track positions modified during this transaction
            pos_cache: Dict[str, float] = {}
            def get_local_qty(sym: str) -> float:
                if sym in pos_cache: return pos_cache[sym]
                row = self.db.execute(
                    text("SELECT json_extract(data, '$.quantity') FROM positions WHERE json_extract(data, '$.symbol') = :symbol AND tenant_id = :tid"),
                    {"symbol": sym, "tid": str(self.tenant_id)}
                ).fetchone()
                pos_cache[sym] = float(row[0]) if row and row[0] is not None else 0.0
                return pos_cache[sym]
                
            def set_local_qty(sym: str, qty: float):
                pos_cache[sym] = qty
            
            # 1. Reconcile Missing Orders & Partial Fill Divergence
            for ex_order in exchange_orders:
                order_id = ex_order.get("id")
                local_order_row = self.db.execute(
                    text("SELECT data FROM orders WHERE id = :order_id AND tenant_id = :tid"),
                    {"order_id": order_id, "tid": str(self.tenant_id)}
                ).fetchone()
                
                if not local_order_row:
                    # Missing Order -> Insert
                    order_data = ex_order.copy()
                    order_data["tenant_id"] = str(self.tenant_id)
                    await transaction.add_operation("insert", "orders", order_data, idempotency_key=f"recon_order_{order_id}")
                    result["missing_orders_inserted"] += 1
            
            # 2. Reconcile Missing Fills
            for ex_fill in exchange_fills:
                fill_id = ex_fill.get("fill_id")
                local_fill_row = self.db.execute(
                    text("SELECT data FROM fills WHERE id = :fill_id AND tenant_id = :tid"),
                    {"fill_id": fill_id, "tid": str(self.tenant_id)}
                ).fetchone()
                
                if not local_fill_row:
                    fill_data = ex_fill.copy()
                    fill_data["id"] = fill_id
                    fill_data["tenant_id"] = str(self.tenant_id)
                    
                    await transaction.add_operation("insert", "fills", fill_data, idempotency_key=f"recon_fill_{fill_id}")
                    result["missing_fills_inserted"] += 1
                    
                    # Update local position mathematically to reflect the missing fill
                    symbol = fill_data["symbol"]
                    qty = float(fill_data["quantity"])
                    if fill_data["side"] == "sell":
                        qty = -qty
                    
                    current_qty = get_local_qty(symbol)
                    new_qty = current_qty + qty
                    
                    pos_row_check = self.db.execute(
                        text("SELECT id FROM positions WHERE json_extract(data, '$.symbol') = :symbol AND tenant_id = :tid"),
                        {"symbol": symbol, "tid": str(self.tenant_id)}
                    ).fetchone()
                    
                    # Also need to know if we already inserted it THIS transaction
                    # We can use the pos_cache for qty, and another cache to know if we've inserted it
                    pos_exists = pos_row_check is not None or f"inserted_{symbol}" in pos_cache
                    
                    if pos_exists:
                        pos_data = {
                            "symbol": symbol,
                            "quantity": new_qty,
                            "tenant_id": str(self.tenant_id)
                        }
                        # When using 'update', the ReplaySafeTransaction does a generic update on the json
                        # Actually we need the id to update.
                        # It's much easier to just let drift handle it! BUT drift needs to always output an op if it was modified.
                    
                    # We will rely on Drift to do the actual UPDATE/INSERT for positions later,
                    # but we need to track it. Wait, if we don't insert here, drift will do it!
                    # So let's NOT insert position operation here, just update cache.
                    
                    # Wait, if we rely on Drift, we MUST make sure Drift runs even if local_qty == ex_qty!
                    # Actually, we can just say "if local_qty != ex_qty or local_qty was modified by fills"
                    set_local_qty(symbol, new_qty)
                    pos_cache[f"dirty_{symbol}"] = 1.0

            # 3. Reconcile Position Drift
            for ex_pos in exchange_positions:
                symbol = ex_pos.get("symbol")
                ex_qty = float(ex_pos.get("quantity", 0))
                
                local_qty = get_local_qty(symbol)
                is_dirty = f"dirty_{symbol}" in pos_cache
                
                if local_qty != ex_qty or is_dirty:
                    # Need to adjust position to match ex_qty (if ex_qty was the final truth),
                    # Wait, if local_qty (which includes fills) equals ex_qty, we STILL need to emit the insert/update!
                    # If local_qty != ex_qty, there is actual drift, so we force it to ex_qty.
                    final_qty = ex_qty if local_qty != ex_qty else local_qty
                    
                    local_pos_row = self.db.execute(
                        text("SELECT id, data FROM positions WHERE json_extract(data, '$.symbol') = :symbol AND tenant_id = :tid"),
                        {"symbol": symbol, "tid": str(self.tenant_id)}
                    ).fetchone()
                    
                    if local_pos_row:
                        pos_id, pos_json = local_pos_row
                        pos_data = json.loads(pos_json) if isinstance(pos_json, str) else pos_json
                        pos_data["quantity"] = final_qty
                        pos_data["tenant_id"] = str(self.tenant_id)
                        await transaction.add_operation("update", "positions", pos_data, idempotency_key=f"recon_drift_{symbol}")
                    else:
                        pos_data = ex_pos.copy()
                        pos_data["id"] = str(uuid.uuid4())
                        pos_data["tenant_id"] = str(self.tenant_id)
                        pos_data["quantity"] = final_qty
                        await transaction.add_operation("insert", "positions", pos_data, idempotency_key=f"recon_drift_{symbol}")
                    
                    if local_qty != ex_qty:
                        result["position_drift_adjustments"] += 1
                    set_local_qty(symbol, final_qty)
            
            # Execute
            for operation in transaction.operations:
                await transaction.execute_operation(operation)
                
            await transaction.commit()
            return result
            
        except Exception as e:
            try:
                await transaction.rollback()
            except Exception as rollback_err:
                logger.warning(f"Rollback failed after original error: {rollback_err}")
            logger.error(f"Exchange reconciliation failed: {e}")
            raise

    async def execute_order(
        self,
        order_params: Dict[str, Any],
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute order with atomic persistence."""
        # Validate tenant context
        if not self.tenant_id:
            raise ValueError("tenant_id required for execution")
        
        # Add tenant_id to order params
        order_params["tenant_id"] = str(self.tenant_id)
        
        # Create transaction
        transaction = ReplaySafeTransaction(self.db, self.tenant_id)
        self.transaction = transaction
        
        try:
            # Begin transaction
            await transaction.begin()
            
            # Check idempotency
            if idempotency_key:
                existing = await transaction._check_idempotency(idempotency_key)
                if existing:
                    logger.info(f"[AtomicExecution] Idempotent order skipped: {idempotency_key}")
                    await transaction.commit()
                    return existing
            
            # Validate order
            await self._validate_order(order_params)
            
            # Add order persistence operation
            await transaction.add_operation(
                operation_type="insert",
                table_name="orders",
                data=order_params,
                idempotency_key=idempotency_key
            )
            
            # Execute order on exchange (mock for now)
            exchange_result = await self._execute_on_exchange(order_params)
            
            # Add tenant_id to exchange result
            exchange_result["tenant_id"] = str(self.tenant_id)
            
            # Add fill persistence operation
            await transaction.add_operation(
                operation_type="insert",
                table_name="fills",
                data=exchange_result,
                idempotency_key=idempotency_key
            )
            
            # Execute all operations
            for operation in transaction.operations:
                await transaction.execute_operation(operation)
            
            # Commit transaction
            await transaction.commit()
            
            # Record idempotency
            if idempotency_key:
                await self._record_idempotency(idempotency_key, exchange_result)
            
            return exchange_result
            
        except Exception as e:
            # Rollback on failure
            try:
                await transaction.rollback()
            except Exception as rollback_err:
                logger.warning(f"Rollback failed after original error: {rollback_err}")
            logger.error(f"[AtomicExecution] Order execution failed: {e}")
            raise
    
    async def cancel_order(
        self,
        order_id: str,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute order cancellation with atomic persistence."""
        if not self.tenant_id:
            raise ValueError("tenant_id required for cancellation")
            
        transaction = ReplaySafeTransaction(self.db, self.tenant_id)
        self.transaction = transaction
        
        try:
            await transaction.begin()
            
            # Check idempotency
            if idempotency_key:
                existing = await transaction._check_idempotency(idempotency_key)
                if existing:
                    logger.info(f"[AtomicExecution] Idempotent cancellation skipped: {idempotency_key}")
                    await transaction.commit()
                    return existing
            
            # Query existing order
            existing_order_row = self.db.execute(
                text("SELECT data FROM orders WHERE id = :order_id AND tenant_id = :tenant_id"),
                {"order_id": order_id, "tenant_id": str(self.tenant_id)}
            ).fetchone()
            
            if not existing_order_row:
                raise ValueError(f"Order not found: {order_id}")
                
            order_data = json.loads(existing_order_row[0]) if isinstance(existing_order_row[0], str) else existing_order_row[0]
            if order_data.get("status") == "cancelled":
                raise ValueError("Order already cancelled")
            
            # Update order status
            order_data["status"] = "cancelled"
            order_data["id"] = order_id
            order_data["tenant_id"] = str(self.tenant_id)
            
            # Add order update operation
            await transaction.add_operation(
                operation_type="update",
                table_name="orders",
                data=order_data,
                idempotency_key=idempotency_key
            )
            
            # Execute on exchange (mock)
            exchange_result = await self._cancel_on_exchange(order_id)
            exchange_result["tenant_id"] = str(self.tenant_id)
            
            # Record the cancellation in fills (or as an execution record)
            await transaction.add_operation(
                operation_type="insert",
                table_name="fills",
                data=exchange_result,
                idempotency_key=idempotency_key
            )
            
            for operation in transaction.operations:
                await transaction.execute_operation(operation)
                
            await transaction.commit()
            
            if idempotency_key:
                await self._record_idempotency(idempotency_key, exchange_result)
                
            return exchange_result
            
        except Exception as e:
            try:
                await transaction.rollback()
            except Exception as rollback_err:
                logger.warning(f"Rollback failed after original error: {rollback_err}")
            logger.error(f"[AtomicExecution] Order cancellation failed: {e}")
            raise
    
    async def _cancel_on_exchange(self, order_id: str) -> Dict[str, Any]:
        """Cancel order on real exchange."""
        if not getattr(self, "exchange_adapter", None):
            raise ValueError("Live exchange adapter not configured. Mock execution paths have been eradicated.")
            
        exchange = self.exchange_adapter.get_exchange()
        
        # CCXT cancel_order
        result = await exchange.cancel_order(order_id)
        
        return {
            "order_id": order_id,
            "status": "cancelled",
            "cancel_result": result
        }

    async def _validate_order(self, order_params: Dict[str, Any]):
        """Validate order parameters."""
        # Validate required fields
        required_fields = ["symbol", "side", "quantity", "price"]
        for field in required_fields:
            if field not in order_params:
                raise ValueError(f"Missing required field: {field}")
        
        # Validate tenant_id matches
        if order_params.get("tenant_id") != str(self.tenant_id):
            raise ValueError(f"Tenant ID mismatch: expected {self.tenant_id}, got {order_params.get('tenant_id')}")
        
        # Validate quantity is positive
        if order_params["quantity"] <= 0:
            raise ValueError("Quantity must be positive")
        
        # Validate price is positive
        if order_params["price"] <= 0:
            raise ValueError("Price must be positive")
        
        # Validate side
        if order_params["side"] not in ["buy", "sell"]:
            raise ValueError("Side must be 'buy' or 'sell'")
        
        logger.debug(f"[TransactionalExecutionManager] Order validated for tenant {self.tenant_id}")
    
    async def _execute_on_exchange(self, order_params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute order on real exchange."""
        if not getattr(self, "exchange_adapter", None):
            raise ValueError("Live exchange adapter not configured. Mock execution paths have been eradicated.")
            
        exchange = self.exchange_adapter.get_exchange()
        
        symbol = order_params["symbol"]
        type_ = order_params["type"].lower()
        side = order_params["side"].lower()
        amount = order_params["quantity"]
        price = order_params.get("price")
        
        # CCXT create_order
        result = await exchange.create_order(symbol, type_, side, amount, price)
        
        return {
            "order_id": result.get("id"),
            "status": result.get("status", "open"),
            "filled_quantity": result.get("filled", 0),
            "execution_price": result.get("average", price)
        }
    
    async def handle_fill(
        self,
        fill_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle incoming fill from exchange."""
        if not self.tenant_id:
            raise ValueError("tenant_id required for fill handling")
            
        transaction = ReplaySafeTransaction(self.db, self.tenant_id)
        self.transaction = transaction
        
        try:
            await transaction.begin()
            
            fill_id = fill_data.get("fill_id") or str(uuid4())
            fill_data["id"] = fill_id
            
            # Check idempotency
            idempotency_key = f"fill_{fill_id}"
            existing = await transaction._check_idempotency(idempotency_key)
            if existing:
                logger.info(f"[AtomicExecution] Idempotent fill skipped: {fill_id}")
                await transaction.commit()
                return existing
                
            order_id = fill_data.get("order_id")
            if not order_id:
                raise ValueError("Fill missing order_id")
                
            # 1. Update Order
            order_row = self.db.execute(
                text("SELECT data FROM orders WHERE id = :order_id AND tenant_id = :tenant_id"),
                {"order_id": order_id, "tenant_id": str(self.tenant_id)}
            ).fetchone()
            
            if not order_row:
                raise ValueError(f"Order not found: {order_id}")
                
            order = json.loads(order_row[0]) if isinstance(order_row[0], str) else order_row[0]
            
            fill_qty = float(fill_data.get("quantity", 0))
            fill_price = float(fill_data.get("price", 0))
            
            current_filled = float(order.get("filled_quantity", 0))
            new_filled = current_filled + fill_qty
            total_qty = float(order.get("quantity", 0))
            remaining_qty = total_qty - new_filled
            
            order["filled_quantity"] = new_filled
            order["remaining_quantity"] = remaining_qty
            order["status"] = "filled" if remaining_qty <= 0 else "partial_filled"
            
            await transaction.add_operation(
                operation_type="update",
                table_name="orders",
                data=order,
                idempotency_key=idempotency_key
            )
            
            # 2. Update Position
            symbol = order.get("symbol") or fill_data.get("symbol")
            if not symbol:
                raise ValueError("Symbol required for position update")
                
            # We assume position id is the symbol for simplicity, or we query by symbol
            # Let's query by symbol directly in data
            position_row = self.db.execute(
                text("SELECT id, data FROM positions WHERE tenant_id = :tenant_id AND json_extract(data, '$.symbol') = :symbol"),
                {"tenant_id": str(self.tenant_id), "symbol": symbol}
            ).fetchone()
            
            if position_row:
                pos_id = position_row[0]
                pos_data = json.loads(position_row[1]) if isinstance(position_row[1], str) else position_row[1]
                pos_qty = float(pos_data.get("quantity", 0))
                pos_avg_price = float(pos_data.get("average_price", 0))
                
                new_pos_qty = pos_qty + (fill_qty if order.get("side") == "buy" else -fill_qty)
                
                # Weighted average price (simplified, only applying if quantity increases)
                if pos_qty == 0:
                    new_avg_price = fill_price
                elif (pos_qty > 0 and order.get("side") == "buy") or (pos_qty < 0 and order.get("side") == "sell"):
                    total_value = (abs(pos_qty) * pos_avg_price) + (fill_qty * fill_price)
                    new_avg_price = total_value / (abs(pos_qty) + fill_qty)
                else:
                    new_avg_price = pos_avg_price # Reducing position, avg price unchanged
                    
                pos_data["quantity"] = new_pos_qty
                pos_data["average_price"] = new_avg_price
                pos_data["id"] = pos_id
                
                await transaction.add_operation(
                    operation_type="update",
                    table_name="positions",
                    data=pos_data,
                    idempotency_key=idempotency_key
                )
            else:
                pos_id = str(uuid4())
                pos_data = {
                    "id": pos_id,
                    "symbol": symbol,
                    "quantity": fill_qty if order.get("side", "buy") == "buy" else -fill_qty,
                    "average_price": fill_price,
                    "tenant_id": str(self.tenant_id)
                }
                await transaction.add_operation(
                    operation_type="insert",
                    table_name="positions",
                    data=pos_data,
                    idempotency_key=idempotency_key
                )
            
            # 3. Persist Fill
            fill_data["tenant_id"] = str(self.tenant_id)
            await transaction.add_operation(
                operation_type="insert",
                table_name="fills",
                data=fill_data,
                idempotency_key=idempotency_key
            )
            
            for operation in transaction.operations:
                await transaction.execute_operation(operation)
                
            await transaction.commit()
            await self._record_idempotency(idempotency_key, {"status": "success", "fill_id": fill_id})
            
            return {"status": "success", "fill_id": fill_id}
            
        except Exception as e:
            try:
                await transaction.rollback()
            except Exception as rollback_err:
                logger.warning(f"Rollback failed after original error: {rollback_err}")
            logger.error(f"[AtomicExecution] Fill handling failed: {e}")
            raise

    async def reconcile_fills(self, exchange_fills: List[Dict[str, Any]]) -> Dict[str, int]:
        """Reconcile fills against exchange."""
        repaired = 0
        detected = 0
        
        for ex_fill in exchange_fills:
            fill_id = ex_fill.get("fill_id")
            if not fill_id:
                continue
                
            # Check if fill exists
            fill_exists = self.db.execute(
                text("SELECT 1 FROM fills WHERE id = :fill_id AND tenant_id = :tenant_id"),
                {"fill_id": fill_id, "tenant_id": str(self.tenant_id)}
            ).fetchone()
            
            if not fill_exists:
                detected += 1
                try:
                    await self.handle_fill(ex_fill)
                    repaired += 1
                except Exception as e:
                    logger.error(f"[Reconciliation] Failed to repair missing fill {fill_id}: {e}")
                    
        return {"detected": detected, "repaired": repaired}

    async def _record_idempotency(self, idempotency_key: str, result: Dict[str, Any]):
        """Record idempotent operation result."""
        try:
            self.db.execute(
                text("""
                    INSERT INTO idempotency_keys (idempotency_key, tenant_id, result, created_at)
                    VALUES (:idempotency_key, :tenant_id, :result, :created_at)
                    ON CONFLICT (idempotency_key, tenant_id) DO NOTHING
                """),
                {
                    "idempotency_key": idempotency_key,
                    "tenant_id": str(self.tenant_id),
                    "result": json.dumps(result),
                    "created_at": datetime.utcnow()
                }
            )
            self.db.commit()
            logger.debug(f"[TransactionalExecutionManager] Idempotency recorded: {idempotency_key}")
        except Exception as e:
            logger.error(f"[TransactionalExecutionManager] Failed to record idempotency: {e}")
            # Don't fail the transaction if idempotency recording fails
            pass


# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════

def get_transactional_execution_manager(db: Session, tenant_id: UUID, exchange_adapter=None) -> TransactionalExecutionManager:
    """Get transactional execution manager instance."""
    return TransactionalExecutionManager(db, tenant_id, exchange_adapter)
