#!/usr/bin/env python3
"""
scripts/attack_database_integrity_destruction.py

DESTRUCTIVE DATABASE INTEGRITY & CONCURRENCY ATTACK SUITE

Aggressively attacks:
  1. Transaction atomicity & rollback boundaries (no partial financial state)
  2. Concurrent claim & row locking race conditions (exactly 1 claim per execution)
  3. Idempotent insertion collision destruction (concurrent duplicate inserts)
  4. Worker crash recovery & active task slot leakage prevention
  5. Strict tenant isolation across all repositories and query layers
  6. Financial position lifecycle & PnL calculation integrity
  7. Session lifecycle & connection pool recovery under failure
"""

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List
from uuid import UUID, uuid4

# Setup test environment
sys.path.insert(0, os.getcwd())
os.environ.setdefault("ENV", "test")
os.environ.setdefault("VYOMQUANT_MODE", "safe")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_integrity.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend_app.core.database_pool import Base
from backend_app.core.models.execution_record import (
    ExecutionRecordModel,
    ExecutionRecordRepository,
    ExecutionRecordCreate,
    ExecutionStatus,
    ExecutionSide,
)
from backend_app.core.models.dag_task import (
    DAGTaskModel,
    DAGTaskRepository,
    DAGTaskCreate,
    DAGConfig,
    TaskStatus,
)
from backend_app.core.models.reconciliation import (
    ReconciliationMismatchModel,
    ReconciliationMismatchRepository,
    ReconciliationMismatchCreate,
    MismatchSeverity,
    MismatchStatus,
)
from backend_app.core.position_model import (
    PositionModel,
    PositionRepository,
    PositionCreate,
    PositionUpdate,
    PositionSide,
    PositionStatus,
    PositionStateMachine,
    InvalidStateTransition,
    NegativePositionSize,
    PositionCalculator,
)
from backend_app.backend.fee_engine import (
    FeeRecordModel,
    FeeEngine,
    FeeType,
    FeeStatus,
)
from backend_app.core.cache.redis_manager import MockRedisClient
from sqlalchemy.pool import StaticPool


from sqlalchemy import text


class DatabaseIntegrityDestructionTester:
    def __init__(self):
        # Create a dedicated test SQLite engine with WAL mode and 30s timeout for concurrent transactions
        self.db_file = os.path.abspath("test_database_integrity.db")
        if os.path.exists(self.db_file):
            try:
                os.remove(self.db_file)
            except Exception:
                pass
        self.engine = create_engine(
            f"sqlite:///{self.db_file}",
            connect_args={"check_same_thread": False, "timeout": 30.0}
        )
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.commit()
        Base.metadata.create_all(self.engine)
        self.SessionFactory = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)
        self.passed_tests = 0
        self.failed_tests = 0

    def get_session(self):
        return self.SessionFactory()

    def record_pass(self, test_name: str, detail: str = ""):
        self.passed_tests += 1
        msg = f"  [PASS] {test_name}"
        if detail:
            msg += f" - {detail}"
        print(msg)

    def record_fail(self, test_name: str, reason: str):
        self.failed_tests += 1
        print(f"  [FAIL] {test_name} - {reason}")

    # =========================================================================
    # ATTACK 1: TRANSACTION ATOMICITY & ROLLBACK INTEGRITY
    # =========================================================================
    def test_transaction_rollback_and_caller_control(self):
        print("\n--- ATTACK 1: Transaction Rollback & Caller-Controlled Atomicity ---")
        session = self.get_session()
        tenant_id = uuid4()
        
        repo = DAGTaskRepository(session)
        pos_repo = PositionRepository(session)
        
        # Test 1.1: Multi-entity transaction rolled back on exception after flush
        try:
            # Caller begins atomic transaction
            task = repo.create(
                DAGTaskCreate(
                    dag_config=DAGConfig(nodes=[{"id": "n1"}], edges=[]),
                    priority=5
                ),
                tenant_id=tenant_id,
                auto_commit=False
            )
            
            pos = pos_repo.create(
                PositionCreate(
                    tenant_id=tenant_id,
                    strategy_id="strat_1",
                    symbol="BTCUSDT",
                    side=PositionSide.LONG,
                    size="1.5",
                    avg_entry_price="50000.0",
                    unrealized_pnl="0",
                    realized_pnl="0",
                    status=PositionStatus.OPEN
                ),
                auto_commit=False
            )
            
            # Simulate unexpected failure before commit
            raise ValueError("Simulated unexpected financial service exception")
            
        except ValueError:
            session.rollback()
        
        # Verify ZERO rows were persisted after rollback
        task_in_db = repo.get_by_id(task.task_id, tenant_id)
        pos_in_db = pos_repo.get_by_id(pos.position_id, tenant_id)
        
        if task_in_db is None and pos_in_db is None:
            self.record_pass("Transaction Rollback Atomicity", "All staged mutations reverted completely on failure")
        else:
            self.record_fail("Transaction Rollback Atomicity", f"Partial rows survived rollback: task={task_in_db}, pos={pos_in_db}")

        # Test 1.2: Successful atomic commit
        try:
            task = repo.create(
                DAGTaskCreate(
                    dag_config=DAGConfig(nodes=[{"id": "n2"}], edges=[]),
                    priority=3
                ),
                tenant_id=tenant_id,
                auto_commit=False
            )
            pos = pos_repo.create(
                PositionCreate(
                    tenant_id=tenant_id,
                    strategy_id="strat_2",
                    symbol="ETHUSDT",
                    side=PositionSide.LONG,
                    size="10.0",
                    avg_entry_price="3000.0",
                    unrealized_pnl="0",
                    realized_pnl="0",
                    status=PositionStatus.OPEN
                ),
                auto_commit=False
            )
            session.commit()
            
            task_verified = repo.get_by_id(task.task_id, tenant_id)
            pos_verified = pos_repo.get_by_id(pos.position_id, tenant_id)
            if task_verified is not None and pos_verified is not None:
                self.record_pass("Transaction Commit Atomicity", "All composite mutations committed atomically")
            else:
                self.record_fail("Transaction Commit Atomicity", "Entities missing after commit")
        except Exception as e:
            self.record_fail("Transaction Commit Atomicity", str(e))
        finally:
            session.close()

    # =========================================================================
    # ATTACK 2: CONCURRENT CLAIM DESTRUCTION (NO DOUBLE CLAIMS)
    # =========================================================================
    def test_concurrent_execution_claims(self):
        print("\n--- ATTACK 2: Concurrent Claim Destruction (Pessimistic/Optimistic Locking) ---")
        tenant_id = uuid4()
        
        # Setup 1 pending execution record
        setup_session = self.get_session()
        exec_repo = ExecutionRecordRepository(setup_session)
        created = exec_repo.create(
            ExecutionRecordCreate(
                execution_id=f"exec_race_{uuid4().hex[:8]}",
                tenant_id=tenant_id,
                strategy_id="race_strat",
                symbol="SOLUSDT",
                side=ExecutionSide.BUY,
                size="100",
                price="150.0",
                status=ExecutionStatus.PENDING
            )
        )
        target_exec_id = created.execution_id
        setup_session.close()
        
        # Attack with 20 parallel threads all attempting to claim the exact same execution
        import concurrent.futures
        
        claim_results = []
        def attempt_claim(worker_idx: int):
            worker_session = self.get_session()
            repo = ExecutionRecordRepository(worker_session)
            try:
                success, record = repo.claim_execution(target_exec_id, tenant_id)
                return (worker_idx, success, record is not None)
            finally:
                worker_session.close()
                
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(attempt_claim, i) for i in range(20)]
            for f in concurrent.futures.as_completed(futures):
                claim_results.append(f.result())
                
        successful_claims = [r for r in claim_results if r[1] is True]
        failed_claims = [r for r in claim_results if r[1] is False]
        
        if len(successful_claims) == 1 and len(failed_claims) == 19:
            self.record_pass(
                "20-Worker Concurrent Claim Race",
                f"EXACTLY 1 worker claimed ({successful_claims[0][0]}), 19 workers rejected cleanly"
            )
        else:
            self.record_fail(
                "20-Worker Concurrent Claim Race",
                f"Expected 1 claim, got {len(successful_claims)} successful claims!"
            )
            
        # Verify DB state is EXECUTING
        verify_session = self.get_session()
        v_repo = ExecutionRecordRepository(verify_session)
        final_record = v_repo.get_by_id(target_exec_id, tenant_id)
        if final_record and final_record.status == ExecutionStatus.EXECUTING:
            self.record_pass("Claim Final DB State", "Record status correctly persisted as EXECUTING")
        else:
            self.record_fail("Claim Final DB State", f"Status is {final_record.status if final_record else None}")
        verify_session.close()

    # =========================================================================
    # ATTACK 3: IDEMPOTENT INSERTION COLLISION DESTRUCTION
    # =========================================================================
    def test_concurrent_idempotent_insertions(self):
        print("\n--- ATTACK 3: Idempotent Insertion Concurrency Collisions ---")
        tenant_id = uuid4()
        now = datetime.utcnow()
        
        # Test 3.1: 10 concurrent calls to check_idempotent_execution for the same parameters
        import concurrent.futures
        
        results = []
        def concurrent_idempotent_call(worker_idx: int):
            session = self.get_session()
            repo = ExecutionRecordRepository(session)
            try:
                exec_id, action, res = repo.check_idempotent_execution(
                    tenant_id=tenant_id,
                    strategy_id="strat_idem",
                    symbol="BTCUSDT",
                    timestamp=now,
                    side=ExecutionSide.BUY,
                    qty=1.0,
                    price=60000.0,
                    execution_interval_minutes=5
                )
                return (worker_idx, exec_id, action)
            finally:
                session.close()
                
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(concurrent_idempotent_call, i) for i in range(10)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())
                
        execute_actions = [r for r in results if r[2] == "execute" or r[2] == "allow_retry"]
        skip_actions = [r for r in results if "skip" in r[2]]
        
        # Total responses must be 10, with zero uncaught IntegrityError exceptions
        if len(results) == 10 and len(execute_actions) >= 1:
            self.record_pass(
                "Concurrent Idempotent Execution Check",
                f"10/10 completed without crash: {len(execute_actions)} execute, {len(skip_actions)} skip/retry"
            )
        else:
            self.record_fail("Concurrent Idempotent Execution Check", f"Failed: total={len(results)}")

        # Test 3.2: Concurrent Reconciliation Mismatch record_mismatch calls
        mismatch_results = []
        def concurrent_mismatch_record(worker_idx: int):
            session = self.get_session()
            repo = ReconciliationMismatchRepository(session)
            try:
                rec = repo.record_mismatch(
                    ReconciliationMismatchCreate(
                        tenant_id=tenant_id,
                        execution_id="exec_mismatch_1",
                        symbol="ETHUSDT",
                        side="buy",
                        field="size",
                        local_value="10.0",
                        exchange_value="8.0",
                        severity=MismatchSeverity.CRITICAL,
                        detected_at=now
                    )
                )
                return (worker_idx, rec.mismatch_id, rec.id)
            finally:
                session.close()
                
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(concurrent_mismatch_record, i) for i in range(10)]
            for f in concurrent.futures.as_completed(futures):
                mismatch_results.append(f.result())
                
        mismatch_ids = set(r[1] for r in mismatch_results)
        row_ids = set(r[2] for r in mismatch_results)
        
        # Exactly 1 DB row should exist and all 10 workers get the same mismatch_id and row id
        if len(mismatch_ids) == 1 and len(row_ids) == 1:
            self.record_pass(
                "Concurrent Reconciliation Mismatch Upsert",
                f"10/10 threads returned identical record (id={list(row_ids)[0]}), zero duplicate rows"
            )
        else:
            self.record_fail("Concurrent Reconciliation Mismatch Upsert", f"Divergent rows created: {row_ids}")

    # =========================================================================
    # ATTACK 4: WORKER RECOVERY & ACTIVE TASK SLOT LEAK PREVENTION
    # =========================================================================
    async def test_task_recovery_slot_leak(self):
        print("\n--- ATTACK 4: Worker Crash Recovery & Active Slot Cleanup ---")
        from backend_app.backend.task_recovery import TaskRecoveryService
        from backend_app.core.dag_task_queue import TaskQueueKeyBuilder
        from backend_app.core import cache
        
        # Inject MockRedisClient to test slot accounting
        mock_redis = MockRedisClient()
        cache.redis_manager = mock_redis
        
        tenant_id = str(uuid4())
        task_id = str(uuid4())
        
        # Simulate active task in Redis active set
        active_key = TaskQueueKeyBuilder.active_tasks(tenant_id)
        await mock_redis.sadd(active_key, task_id)
        active_count_before = await mock_redis.scard(active_key)
        
        # Create stuck task in DB
        session = self.get_session()
        stuck_task = DAGTaskModel(
            task_id=task_id,
            tenant_id=tenant_id,
            status=TaskStatus.RUNNING,
            priority=5,
            dag_config={"nodes": []},
            retry_count=0,
            max_retries=3,
            last_heartbeat=datetime.utcnow() - timedelta(seconds=120),
            started_at=datetime.utcnow() - timedelta(seconds=120)
        )
        session.add(stuck_task)
        session.commit()
        
        # Run recovery service on task
        recovery_svc = TaskRecoveryService(
            stale_threshold_seconds=30.0,
            enable_auto_retry=True,
            redis_client=mock_redis
        )
        # Patch SessionLocal inside task_recovery to use test engine
        import backend_app.backend.task_recovery
        backend_app.backend.task_recovery.SessionLocal = self.SessionFactory
        
        recovered_ok = await recovery_svc.recover_task(stuck_task, action="retry")
        
        # Verify task was retried
        active_count_after = await mock_redis.scard(active_key)
        session.refresh(stuck_task)
        
        if recovered_ok and active_count_after == 0 and stuck_task.status == "PENDING" and stuck_task.retry_count == 1:
            self.record_pass(
                "Task Recovery Slot Leak Prevention",
                "Task moved to PENDING, retry_count=1, active slot safely removed (count 1 -> 0)"
            )
        else:
            self.record_fail(
                "Task Recovery Slot Leak Prevention",
                f"Active count after={active_count_after}, status={stuck_task.status}, retries={stuck_task.retry_count}"
            )
        session.close()

    # =========================================================================
    # ATTACK 5: TENANT ISOLATION DESTRUCTION
    # =========================================================================
    def test_strict_tenant_isolation(self):
        print("\n--- ATTACK 5: Cross-Tenant Database Isolation ---")
        tenant_a = uuid4()
        tenant_b = uuid4()
        
        session = self.get_session()
        dag_repo = DAGTaskRepository(session)
        pos_repo = PositionRepository(session)
        exec_repo = ExecutionRecordRepository(session)
        
        # Create entities for Tenant A
        t_a_task = dag_repo.create(
            DAGTaskCreate(dag_config=DAGConfig(nodes=[], edges=[]), priority=5),
            tenant_id=tenant_a
        )
        t_a_pos = pos_repo.create(
            PositionCreate(
                tenant_id=tenant_a,
                strategy_id="strat_a",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                size="2.0",
                avg_entry_price="50000.0",
                unrealized_pnl="0",
                realized_pnl="0",
                status=PositionStatus.OPEN
            )
        )
        t_a_exec = exec_repo.create(
            ExecutionRecordCreate(
                execution_id=f"exec_a_{uuid4().hex[:8]}",
                tenant_id=tenant_a,
                strategy_id="strat_a",
                symbol="BTCUSDT",
                side=ExecutionSide.BUY,
                size="2.0",
                price="50000.0",
                status=ExecutionStatus.PENDING
            )
        )
        
        # Attack 5.1: Tenant B attempting to read Tenant A's task
        cross_task = dag_repo.get_by_id(t_a_task.task_id, tenant_b)
        if cross_task is None:
            self.record_pass("Tenant Isolation (DAG Task)", "Tenant B cannot query Tenant A task")
        else:
            self.record_fail("Tenant Isolation (DAG Task)", "Tenant B retrieved Tenant A task!")
            
        # Attack 5.2: Tenant B attempting to read Tenant A's position
        cross_pos = pos_repo.get_by_id(t_a_pos.position_id, tenant_b)
        if cross_pos is None:
            self.record_pass("Tenant Isolation (Position)", "Tenant B cannot query Tenant A position")
        else:
            self.record_fail("Tenant Isolation (Position)", "Tenant B retrieved Tenant A position!")
            
        # Attack 5.3: Tenant B attempting to claim Tenant A's execution
        claimed, rec = exec_repo.claim_execution(t_a_exec.execution_id, tenant_b)
        if claimed is False and rec is None:
            self.record_pass("Tenant Isolation (Execution Claim)", "Tenant B cannot claim Tenant A execution")
        else:
            self.record_fail("Tenant Isolation (Execution Claim)", "Tenant B successfully claimed Tenant A execution!")
            
        # Attack 5.4: Tenant B list queries returning only Tenant B items
        t_b_tasks = dag_repo.get_by_tenant(tenant_b)
        t_b_positions = pos_repo.list_open_positions(tenant_b)
        if len(t_b_tasks) == 0 and len(t_b_positions) == 0:
            self.record_pass("Tenant Isolation (List Scoping)", "Tenant B list queries returned 0 results (no leakage)")
        else:
            self.record_fail("Tenant Isolation (List Scoping)", f"Leaked items in Tenant B list queries: tasks={len(t_b_tasks)}, pos={len(t_b_positions)}")
            
        session.close()

    # =========================================================================
    # ATTACK 6: POSITION LIFECYCLE & FINANCIAL CALCULATIONS
    # =========================================================================
    def test_position_lifecycle_and_financial_integrity(self):
        print("\n--- ATTACK 6: Position State Machine & PnL Integrity ---")
        
        # Test 6.1: Valid state transitions
        try:
            PositionStateMachine.validate_transition(
                from_state=PositionStatus.OPEN,
                to_state=PositionStatus.PARTIAL,
                current_size=Decimal("2.0"),
                new_size=Decimal("1.0"),
                original_size=Decimal("2.0")
            )
            PositionStateMachine.validate_transition(
                from_state=PositionStatus.PARTIAL,
                to_state=PositionStatus.CLOSED,
                current_size=Decimal("1.0"),
                new_size=Decimal("0.0"),
                original_size=Decimal("2.0")
            )
            self.record_pass("Position State Machine (Valid)", "OPEN -> PARTIAL -> CLOSED transitions valid")
        except Exception as e:
            self.record_fail("Position State Machine (Valid)", str(e))
            
        # Test 6.2: Invalid state transition (CLOSED -> OPEN)
        try:
            PositionStateMachine.validate_transition(
                from_state=PositionStatus.CLOSED,
                to_state=PositionStatus.OPEN,
                current_size=Decimal("0"),
                new_size=Decimal("1.0"),
                original_size=Decimal("2.0")
            )
            self.record_fail("Position State Machine (Invalid)", "Failed to reject CLOSED -> OPEN transition")
        except InvalidStateTransition:
            self.record_pass("Position State Machine (Invalid)", "Correctly rejected CLOSED -> OPEN transition")
            
        # Test 6.3: Negative position size rejection
        try:
            PositionStateMachine.validate_transition(
                from_state=PositionStatus.OPEN,
                to_state=PositionStatus.PARTIAL,
                current_size=Decimal("2.0"),
                new_size=Decimal("-0.5"),
                original_size=Decimal("2.0")
            )
            self.record_fail("Negative Position Size Guard", "Failed to reject negative position size")
        except NegativePositionSize:
            self.record_pass("Negative Position Size Guard", "Correctly rejected negative position size (-0.5)")

        # Test 6.4: Financial PnL calculation correctness
        realized_long = PositionCalculator.calculate_realized_pnl(
            exit_size=Decimal("2.0"),
            exit_price=Decimal("55000.0"),
            avg_entry_price=Decimal("50000.0"),
            side=PositionSide.LONG
        )
        if realized_long == Decimal("10000.00000000"):
            self.record_pass("Realized PnL Calculation (Long)", f"Profit calculation exact: {realized_long}")
        else:
            self.record_fail("Realized PnL Calculation (Long)", f"Expected 10000.00000000, got {realized_long}")

    # =========================================================================
    # ATTACK 7: FEE MODEL & NULLABILITY CONSTRAINTS
    # =========================================================================
    async def test_fee_model_and_funding_fee(self):
        print("\n--- ATTACK 7: Fee Model & Funding Fee Nullability ---")
        session = self.get_session()
        tenant_id = uuid4()
        
        # Create position to anchor funding fee
        pos_repo = PositionRepository(session)
        pos = pos_repo.create(
            PositionCreate(
                tenant_id=tenant_id,
                strategy_id="strat_funding",
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                size="1.0",
                avg_entry_price="50000.0",
                unrealized_pnl="0",
                realized_pnl="0",
                status=PositionStatus.OPEN
            )
        )
        
        fee_engine = FeeEngine(session)
        
        try:
            # Funding fee has NO execution_id (execution_id=None)
            funding_record = await fee_engine.record_funding_fee(
                position_id=pos.position_id,
                symbol="BTCUSDT",
                fee_amount=Decimal("15.50"),
                fee_asset="USDT",
                exchange_id="binance",
                funding_period="2026-08-15-0800"
            )
            
            if funding_record and funding_record.fee_type == FeeType.FUNDING and funding_record.execution_id is None:
                self.record_pass(
                    "Funding Fee Null Execution Constraint",
                    f"Successfully persisted funding fee with execution_id=None (fee_id={funding_record.fee_id})"
                )
            else:
                self.record_fail("Funding Fee Null Execution Constraint", "Funding fee failed to persist properly")
        except Exception as e:
            self.record_fail("Funding Fee Null Execution Constraint", f"Raised integrity exception: {e}")
        finally:
            session.close()

    async def run_all(self):
        print("======================================================================")
        print("HOSTILE DATABASE INTEGRITY & CONCURRENCY DESTRUCTION SUITE")
        print("======================================================================")
        start_time = time.time()
        
        self.test_transaction_rollback_and_caller_control()
        self.test_concurrent_execution_claims()
        self.test_concurrent_idempotent_insertions()
        await self.test_task_recovery_slot_leak()
        self.test_strict_tenant_isolation()
        self.test_position_lifecycle_and_financial_integrity()
        await self.test_fee_model_and_funding_fee()
        
        duration = time.time() - start_time
        print("\n======================================================================")
        print(f"DATABASE INTEGRITY SUITE COMPLETE: {self.passed_tests} PASSED, {self.failed_tests} FAILED ({duration:.2f}s)")
        print("======================================================================")
        
        if self.failed_tests > 0:
            sys.exit(1)


if __name__ == "__main__":
    tester = DatabaseIntegrityDestructionTester()
    asyncio.run(tester.run_all())
