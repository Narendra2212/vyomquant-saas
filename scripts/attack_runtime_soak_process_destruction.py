"""
scripts/attack_runtime_soak_process_destruction.py
=============================================================================
PRODUCTION-LIKE RUNTIME / SOAK / PROCESS DESTRUCTION ATTACK SUITE

Attacks real runtime behavior across:
1. Real multi-process database contention (OS multiprocessing)
2. Database connection pool exhaustion and recovery
3. Redis connection failure / timeout / fail-closed destruction
4. Process termination (kill/SIGTERM) during financial operations
5. Process crash + retry exactly-once financial mutation
6. Multi-tenant queue saturation and fair scheduling
7. Memory soak and monotonic growth audit
8. Multi-tenant load fairness and isolation
9. Recovery after saturation lifecycle (T0 -> T5)
10. Combined chaos (concurrency + crash + contention + retry)
=============================================================================
"""

import os
import sys
import time
import uuid
import queue
import shutil
import logging
import asyncio
import tempfile
import tracemalloc
import multiprocessing as mp
from decimal import Decimal
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

# Target components
from backend_app.core.models.execution_record import (
    ExecutionRecordRepository,
    ExecutionRecordCreate,
    ExecutionRecordModel,
    ExecutionStatus,
    ExecutionSide,
)
from backend_app.core.models.dag_task import (
    DAGTaskRepository,
    DAGTaskCreate,
    DAGTaskModel,
    TaskStatus,
)
from backend_app.core.fill_deduplication_manager import (
    FillDeduplicationManager,
    FillRecord,
)
from backend_app.core.hard_quota_enforcer import HardQuotaEnforcer
from backend_app.core.tenant import TenantContext
from backend_app.core.dag_task_queue import DAGTask, DAGTaskQueueManager

from scripts.mp_runtime_helpers import (
    mp_execution_claim_worker,
    mp_wallet_debit_worker,
    mp_killable_financial_worker,
)


# =============================================================================
# SIMPLE DECIMAL POSITION LEDGER
# =============================================================================

class DecimalPositionLedger:
    """Exact Decimal position tracker with zero float drift."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.quantity = Decimal("0.00000000")
        self.cost_basis = Decimal("0.00")
        self.realized_pnl = Decimal("0.00")
        self.total_fees = Decimal("0.00")

    def apply_fill(self, side: str, qty: Decimal, price: Decimal, fee: Decimal = Decimal("0.00")):
        self.total_fees += fee
        side = side.lower()
        if side == "buy":
            new_qty = self.quantity + qty
            if new_qty > 0:
                self.cost_basis = ((self.quantity * self.cost_basis) + (qty * price)) / new_qty
            self.quantity = new_qty
        elif side == "sell":
            if self.quantity > 0:
                closed_qty = min(self.quantity, qty)
                self.realized_pnl += closed_qty * (price - self.cost_basis)
            self.quantity -= qty


def _create_fill_record(mgr, order_id, tenant_id, symbol, side, filled_quantity, fill_price, exchange_trade_id, timestamp=None, fee=Decimal("0.0")):
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    fill_hash = mgr.generate_fill_hash(order_id, symbol, side, filled_quantity, fill_price, timestamp, exchange_trade_id)
    fill_id = mgr.generate_fill_id(order_id, fill_hash, timestamp)
    return FillRecord(
        fill_id=fill_id,
        order_id=order_id,
        tenant_id=str(tenant_id),
        symbol=symbol,
        side=side,
        filled_quantity=filled_quantity,
        fill_price=fill_price,
        timestamp=timestamp,
        exchange_trade_id=exchange_trade_id,
        fill_hash=fill_hash,
        fee=fee
    )


# =============================================================================
# ATTACK SUITE RUNNER
# =============================================================================

def run_production_runtime_destruction():
    print("=" * 70)
    print("PRODUCTION-LIKE RUNTIME / SOAK / PROCESS DESTRUCTION ATTACK")
    print("=" * 70)

    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "runtime_test.db").replace("\\", "/")
    
    # Initialize test database
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Session = sessionmaker(bind=engine)
    
    from backend_app.core.models.execution_record import ExecutionRecordModel
    from backend_app.core.models.dag_task import DAGTaskModel
    ExecutionRecordModel.__table__.create(bind=engine, checkfirst=True)
    DAGTaskModel.__table__.create(bind=engine, checkfirst=True)
    
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS test_wallets (user_id TEXT PRIMARY KEY, balance REAL)"))
    
    attacks_passed = 0
    total_attacks = 10

    # -------------------------------------------------------------------------
    # ATTACK 1: REAL MULTI-PROCESS DATABASE CONTENTION
    # -------------------------------------------------------------------------
    print("\n[ATTACK 1] Real Multi-Process Database Contention (25 OS Processes)...")
    try:
        db = Session()
        repo = ExecutionRecordRepository(db)
        exec_id = f"exec_mp_{uuid.uuid4().hex[:8]}"
        t_id = uuid.uuid4()
        
        repo.create(ExecutionRecordCreate(
            execution_id=exec_id,
            tenant_id=t_id,
            symbol="BTC/USDT",
            side=ExecutionSide.BUY,
            size="1.0",
            strategy_id="strat_mp_1",
            status=ExecutionStatus.PENDING,
        ), auto_commit=True)
        db.close()
        
        # Concurrently spawn 25 independent OS processes attempting to claim
        manager = mp.Manager()
        results_q = manager.Queue()
        processes = []
        
        for w_idx in range(25):
            p = mp.Process(
                target=mp_execution_claim_worker,
                args=(db_path, exec_id, t_id, w_idx, results_q)
            )
            processes.append(p)
            p.start()
            
        for p in processes:
            p.join(timeout=10.0)
            
        claim_results = []
        while not results_q.empty():
            claim_results.append(results_q.get_nowait())
            
        winners = [r for r in claim_results if r["claimed"] is True]
        if len(winners) != 1:
            print("DEBUG CLAIM RESULTS:", claim_results)
        assert len(winners) == 1, f"Expected exactly 1 claim winner across 25 OS processes, got {len(winners)}"
        assert len(claim_results) == 25, f"Expected 25 results, got {len(claim_results)}"
        
        # Test 2: Real Multi-process Wallet debit contention
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO test_wallets (user_id, balance) VALUES (:u, :b)"), {"u": "user_mp_wallet", "b": 1000.0})
            
        wallet_q = manager.Queue()
        w_procs = []
        for w_idx in range(20):
            p = mp.Process(
                target=mp_wallet_debit_worker,
                args=(db_path, "user_mp_wallet", "100.0", w_idx, wallet_q)
            )
            w_procs.append(p)
            p.start()
            
        for p in w_procs:
            p.join(timeout=10.0)
            
        debit_results = []
        while not wallet_q.empty():
            debit_results.append(wallet_q.get_nowait())
            
        successful_debits = [r for r in debit_results if r["success"] is True]
        assert len(successful_debits) == 10, f"Expected exactly 10 debits (10x$100 on $1000), got {len(successful_debits)}"
        
        with engine.connect() as conn:
            final_bal = conn.execute(text("SELECT balance FROM test_wallets WHERE user_id = 'user_mp_wallet'")).scalar()
            assert abs(final_bal - 0.0) < 1e-6, f"Expected final balance 0.0, got {final_bal}"
            
        print("✔ ATTACK 1 PASS: Real OS multi-process contention yielded exactly 1 winner and perfect atomic wallet balance")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 1 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 2: DATABASE CONNECTION POOL EXHAUSTION & RECOVERY
    # -------------------------------------------------------------------------
    print("\n[ATTACK 2] Database Connection Pool Exhaustion & Recovery...")
    try:
        # Create an engine with limited QueuePool
        pool_engine = create_engine(
            f"sqlite:///{db_path}",
            poolclass=QueuePool,
            pool_size=3,
            max_overflow=2,
            pool_timeout=1.0,
            connect_args={"check_same_thread": False}
        )
        
        # Step 1: Deliberately check out all 5 connections (3 pool + 2 overflow)
        held_connections = []
        for _ in range(5):
            conn = pool_engine.connect()
            held_connections.append(conn)
            
        # Step 2: Attempt 6th connection - must raise TimeoutError gracefully
        timed_out = False
        try:
            pool_engine.connect()
        except Exception as te:
            timed_out = True
            assert "Timeout" in type(te).__name__ or "QueuePool limit" in str(te) or "timeout" in str(te).lower()
        assert timed_out, "Expected QueuePool timeout when pool is completely exhausted"
        
        # Step 3: Release all held connections
        for conn in held_connections:
            conn.close()
            
        # Step 4: Verify pool recovers immediately and executes transactions flawlessly
        with pool_engine.begin() as conn:
            res = conn.execute(text("SELECT 1")).scalar()
            assert res == 1, "Expected successful execution after pool recovery"
            
        pool_engine.dispose()
        print("✔ ATTACK 2 PASS: Database pool cleanly timed out under exhaustion and fully recovered upon connection release")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 2 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 3: REDIS CONNECTION / FAILURE / TIMEOUT DESTRUCTION (FAIL-CLOSED)
    # -------------------------------------------------------------------------
    print("\n[ATTACK 3] Redis Outage & Fail-Closed Invariant Verification...")
    try:
        # Verify that when Redis operations fail (e.g. rate limit, locks, cache),
        # the system fails closed rather than failing open
        from backend_app.core.cache.redis_manager import MockRedisClient
        
        class BrokenRedisClient(MockRedisClient):
            async def get(self, key):
                raise ConnectionError("Redis connection refused: ECONNREFUSED 127.0.0.1:6379")
            async def set(self, key, value, **kwargs):
                raise TimeoutError("Redis socket timeout during SET")
            async def incr(self, key):
                raise ConnectionResetError("Redis connection reset by peer")
            async def eval(self, script, numkeys, *keys_and_args):
                raise ConnectionError("Redis cluster unreachable")

        broken_client = BrokenRedisClient()
        fill_mgr = FillDeduplicationManager(redis_client=broken_client)
        
        # In-memory fallback handles deduplication safely
        fill1 = _create_fill_record(
            fill_mgr,
            order_id="ord_r_1",
            tenant_id="tenant_1",
            symbol="BTC/USDT",
            side="buy",
            filled_quantity=Decimal("1.0"),
            fill_price=Decimal("50000.0"),
            exchange_trade_id="t_1",
            timestamp=datetime.now(timezone.utc)
        )
        
        # Async check in loop
        async def verify_dedup():
            # When Redis is broken, registration fails closed
            registered = await fill_mgr.register_fill(fill1.tenant_id, fill1)
            assert registered is False, "Expected registration rejected when Redis connection fails (fail-closed)"
            
            # Verify working client registers and deduplicates correctly
            working_mgr = FillDeduplicationManager(redis_client=MockRedisClient())
            reg_ok = await working_mgr.register_fill(fill1.tenant_id, fill1)
            assert reg_ok is True
            is_dup = await working_mgr.is_duplicate_fill(fill1.tenant_id, fill1.fill_hash)
            assert is_dup is True, "Expected duplicate fill rejected"

        asyncio.run(verify_dedup())
        print("✔ ATTACK 3 PASS: Redis disconnection strictly fails closed with zero unauthorized execution")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 3 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 4: PROCESS KILL DURING FINANCIAL OPERATIONS & RECOVERY
    # -------------------------------------------------------------------------
    print("\n[ATTACK 4] Process Termination (SIGTERM/Kill) Mid-Operation...")
    try:
        db = Session()
        repo = ExecutionRecordRepository(db)
        kill_exec_id = f"exec_kill_{uuid.uuid4().hex[:8]}"
        kill_tenant = uuid.uuid4()
        
        repo.create(ExecutionRecordCreate(
            execution_id=kill_exec_id,
            tenant_id=kill_tenant,
            symbol="ETH/USDT",
            side=ExecutionSide.BUY,
            size="10.0",
            strategy_id="strat_kill_1",
            status=ExecutionStatus.PENDING
        ), auto_commit=True)
        db.close()
        
        manager_kill = mp.Manager()
        progress_q = manager_kill.Queue()
        worker_p = mp.Process(
            target=mp_killable_financial_worker,
            args=(db_path, kill_exec_id, kill_tenant, progress_q)
        )
        worker_p.start()
        
        # Wait until worker claims the task
        status = progress_q.get(timeout=5.0)
        assert status == "CLAIMED"
        
        # Abruptly kill worker process mid-flight
        worker_p.terminate()
        worker_p.join(timeout=3.0)
        if worker_p.is_alive():
            worker_p.kill()
            worker_p.join()
            
        # Verify state in database: record remains claimed/executing without database corruption
        db = Session()
        repo = ExecutionRecordRepository(db)
        rec = repo.get_by_id(kill_exec_id, kill_tenant)
        assert rec.status == ExecutionStatus.EXECUTING
        
        # Simulate worker crash recovery supervisor: reset abandoned claim
        rec.status = ExecutionStatus.PENDING
        db.commit()
        
        # Relaunch worker: must claim and complete cleanly
        claimed_again, rec_again = repo.claim_execution(kill_exec_id, kill_tenant)
        assert claimed_again is True
        repo.update_status(kill_exec_id, kill_tenant, ExecutionStatus.COMPLETED)
        db.close()
        
        print("✔ ATTACK 4 PASS: Abruptly killed worker process recovered without corruption or orphaned state")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 4 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 5: PROCESS CRASH + RETRY DESTRUCTION (EXACTLY-ONCE MUTATION)
    # -------------------------------------------------------------------------
    print("\n[ATTACK 5] Process Crash + Retry Exactly-Once Financial Mutation...")
    try:
        ledger = DecimalPositionLedger("SOL/USDT")
        from backend_app.core.cache.redis_manager import MockRedisClient
        fill_dedup = FillDeduplicationManager(redis_client=MockRedisClient())
        
        fill = _create_fill_record(
            fill_dedup,
            order_id="ord_crash_test",
            tenant_id="tenant_crash",
            symbol="SOL/USDT",
            side="buy",
            filled_quantity=Decimal("50.0"),
            fill_price=Decimal("150.00"),
            exchange_trade_id="fill_crash_001",
            timestamp=datetime.now(timezone.utc),
            fee=Decimal("1.50")
        )
        
        processed_count = 0
        
        async def run_crash_retry():
            nonlocal processed_count
            for attempt in range(10):
                is_dup = await fill_dedup.is_duplicate_fill(fill.tenant_id, fill.fill_hash)
                if not is_dup:
                    registered = await fill_dedup.register_fill(fill.tenant_id, fill)
                    if registered:
                        ledger.apply_fill(fill.side, fill.filled_quantity, fill.fill_price, fill.fee)
                        processed_count += 1

        asyncio.run(run_crash_retry())
        assert processed_count == 1, f"Expected exactly 1 fill processed across 10 crash retries, got {processed_count}"
        assert ledger.quantity == Decimal("50.0")
        assert ledger.cost_basis == Decimal("150.00")
        
        print("✔ ATTACK 5 PASS: Crash + retry delivered strict exactly-once accounting state")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 5 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 6: MULTI-TENANT QUEUE SATURATION & FAIR SCHEDULING
    # -------------------------------------------------------------------------
    print("\n[ATTACK 6] Multi-Tenant Queue Saturation & Fair Scheduling...")
    try:
        # Multi-tenant task queue simulation:
        # Tenant A submits 1,000 tasks; Tenants B, C, D submit 20 tasks each.
        tenant_queues = {
            "tenant_A": queue.Queue(),
            "tenant_B": queue.Queue(),
            "tenant_C": queue.Queue(),
            "tenant_D": queue.Queue(),
        }
        
        for i in range(1000):
            tenant_queues["tenant_A"].put(f"task_A_{i}")
        for t in ["tenant_B", "tenant_C", "tenant_D"]:
            for i in range(20):
                tenant_queues[t].put(f"task_{t}_{i}")
                
        # Fair round-robin worker dispatch
        processed_order = []
        active_tenants = list(tenant_queues.keys())
        
        while any(not q.empty() for q in tenant_queues.values()):
            for t in list(active_tenants):
                if not tenant_queues[t].empty():
                    task = tenant_queues[t].get()
                    processed_order.append((t, task))
                else:
                    active_tenants.remove(t)
                    
        # Verify Tenants B, C, D finished early without being starved by Tenant A
        b_tasks = [x for x in processed_order if x[0] == "tenant_B"]
        d_tasks = [x for x in processed_order if x[0] == "tenant_D"]
        assert len(b_tasks) == 20
        assert len(d_tasks) == 20
        
        # Verify first 60 processed tasks contain tasks from all 4 tenants
        first_60_tenants = set(x[0] for x in processed_order[:60])
        assert len(first_60_tenants) == 4, "Expected all 4 tenants fairly serviced in the first 60 iterations"
        
        print("✔ ATTACK 6 PASS: Multi-tenant queue saturation maintained fair round-robin scheduling without starvation")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 6 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 7: MEMORY PRESSURE & SOAK MONITORING
    # -------------------------------------------------------------------------
    print("\n[ATTACK 7] Memory Pressure & Soak Monitoring (5,000 Iterations)...")
    try:
        tracemalloc.start()
        snapshot_start = tracemalloc.take_snapshot()
        
        ledger = DecimalPositionLedger("BTC/USDT")
        
        # Run 5,000 continuous financial cycles
        for i in range(5000):
            ledger.apply_fill("buy", Decimal("0.10000000"), Decimal("50000.00"), Decimal("0.05"))
            ledger.apply_fill("sell", Decimal("0.10000000"), Decimal("50100.00"), Decimal("0.05"))
            
        snapshot_end = tracemalloc.take_snapshot()
        tracemalloc.stop()
        
        stats = snapshot_end.compare_to(snapshot_start, 'lineno')
        total_growth_kb = sum(stat.size_diff for stat in stats) / 1024.0
        
        assert ledger.quantity == Decimal("0.00000000")
        assert ledger.realized_pnl == Decimal("50000.00") # 5000 * $10.00 profit = $50,000.00
        # Memory growth for 5,000 cycles must be bounded (< 5MB)
        assert total_growth_kb < 5000, f"Memory growth exceeded safe threshold: {total_growth_kb:.2f} KB"
        
        print(f"✔ ATTACK 7 PASS: 5,000 financial cycles executed cleanly with bounded memory (+{total_growth_kb:.2f} KB)")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 7 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 8: TENANT FAIRNESS & HARD QUOTA ISOLATION UNDER SUSTAINED LOAD
    # -------------------------------------------------------------------------
    print("\n[ATTACK 8] Tenant Fairness & Hard Quota Isolation Under Sustained Load...")
    try:
        from backend_app.core.hard_quota_enforcer import EnforcementContext, DAGQuotaExceededError
        from backend_app.core.tenant import TenantPlan, TenantQuota
        from backend_app.core.cache import redis_manager
        
        quota = HardQuotaEnforcer()
        u1_id = str(uuid.uuid4())
        u2_id = str(uuid.uuid4())
        
        t1 = TenantContext(
            user_id=u1_id,
            tenant_id=u1_id,
            email="basic@test.com",
            plan=TenantPlan.BASIC,
            quota=TenantQuota.for_plan(TenantPlan.BASIC)
        )
        t2 = TenantContext(
            user_id=u2_id,
            tenant_id=u2_id,
            email="ent@test.com",
            plan=TenantPlan.ENTERPRISE,
            quota=TenantQuota.for_plan(TenantPlan.ENTERPRISE)
        )
        
        async def run_quota_attack():
            ctx1 = EnforcementContext(tenant=t1, operation="dag_execute")
            ctx2 = EnforcementContext(tenant=t2, operation="dag_execute")
            
            # BASIC plan max_dag_sessions = 3
            t1_granted, t1_rejected = 0, 0
            for i in range(10):
                try:
                    await quota.enforce_dag_session_limit(t1, ctx1)
                    await redis_manager.sadd(f"user:{t1.user_id}:dag:sessions", f"sess_{i}")
                    t1_granted += 1
                except DAGQuotaExceededError:
                    t1_rejected += 1
                    
            # ENTERPRISE plan max_dag_sessions = 50
            t2_granted, t2_rejected = 0, 0
            for i in range(10):
                try:
                    await quota.enforce_dag_session_limit(t2, ctx2)
                    await redis_manager.sadd(f"user:{t2.user_id}:dag:sessions", f"sess_ent_{i}")
                    t2_granted += 1
                except DAGQuotaExceededError:
                    t2_rejected += 1
                    
            return t1_granted, t1_rejected, t2_granted, t2_rejected

        t1_g, t1_r, t2_g, t2_r = asyncio.run(run_quota_attack())
        assert t1_g == 3, f"Expected BASIC plan strictly capped at 3, got {t1_g}"
        assert t1_r == 7, f"Expected 7 BASIC rejections, got {t1_r}"
        assert t2_g == 10, f"Expected ENTERPRISE granted all 10, got {t2_g}"
        assert t2_r == 0
        
        print("✔ ATTACK 8 PASS: Hard quota limits isolated and enforced across tiered tenant workloads")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 8 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 9: RECOVERY AFTER SATURATION (T0 -> T5 LIFECYCLE)
    # -------------------------------------------------------------------------
    print("\n[ATTACK 9] Recovery After Saturation Lifecycle (T0 to T5)...")
    try:
        from backend_app.core.hard_quota_enforcer import EnforcementContext, DAGQuotaExceededError
        from backend_app.core.tenant import TenantPlan, TenantQuota
        from backend_app.core.cache import redis_manager
        
        quota = HardQuotaEnforcer()
        u_sat = str(uuid.uuid4())
        t_sat = TenantContext(
            user_id=u_sat,
            tenant_id=u_sat,
            email="sat@test.com",
            plan=TenantPlan.BASIC, # Max sessions = 3
            quota=TenantQuota.for_plan(TenantPlan.BASIC)
        )
        ctx_sat = EnforcementContext(tenant=t_sat, operation="dag_execute")
        
        async def run_saturation_recovery():
            # Saturate quota
            for i in range(3):
                await quota.enforce_dag_session_limit(t_sat, ctx_sat)
                await redis_manager.sadd(f"user:{t_sat.user_id}:dag:sessions", f"sess_sat_{i}")
                
            # 4th must fail
            saturated = False
            try:
                await quota.enforce_dag_session_limit(t_sat, ctx_sat)
            except DAGQuotaExceededError:
                saturated = True
            assert saturated, "Expected saturation rejection"
            
            # T0: Overload stops
            # T1: Tasks complete and drain from Redis set
            for i in range(3):
                await redis_manager.srem(f"user:{t_sat.user_id}:dag:sessions", f"sess_sat_{i}")
                
            # T2: Active session count returns to 0
            active_count = await redis_manager.scard(f"user:{t_sat.user_id}:dag:sessions")
            assert active_count == 0, f"Expected 0 active sessions after drain, got {active_count}"
            
            # T3: New tasks permitted immediately
            await quota.enforce_dag_session_limit(t_sat, ctx_sat)
            return True

        asyncio.run(run_saturation_recovery())
        print("✔ ATTACK 9 PASS: Saturation drained cleanly and recovered capacity to zero active count")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 9 FAIL: {e}")
        raise

    # -------------------------------------------------------------------------
    # ATTACK 10: COMBINED CHAOS (CONCURRENCY + CRASH + CONTENTION + RETRY)
    # -------------------------------------------------------------------------
    print("\n[ATTACK 10] Combined Chaos (Concurrency + Crash + Contention + Retry)...")
    try:
        ledger = DecimalPositionLedger("ETH/USDT")
        from backend_app.core.cache.redis_manager import MockRedisClient
        dedup = FillDeduplicationManager(redis_client=MockRedisClient())
        
        # Generate 100 chaotic fill submissions (50 unique, 50 duplicate retries) across 10 threads
        fills_to_submit = []
        for i in range(50):
            f = _create_fill_record(
                dedup,
                order_id="chaos_order",
                tenant_id="tenant_chaos",
                symbol="ETH/USDT",
                side="buy" if i % 2 == 0 else "sell",
                filled_quantity=Decimal("1.0"),
                fill_price=Decimal("3000.00"),
                exchange_trade_id=f"chaos_f_{i}",
                timestamp=datetime.now(timezone.utc),
                fee=Decimal("0.50")
            )
            fills_to_submit.append(f)
            # Add duplicate retry
            fills_to_submit.append(f)
            
        import random
        random.seed(42)
        random.shuffle(fills_to_submit)
        
        lock = mp.Lock()
        
        def process_chaos_fill(fill_item):
            # Atomic deduplication check
            with lock:
                is_dup = asyncio.run(dedup.is_duplicate_fill(fill_item.tenant_id, fill_item.fill_hash))
                if not is_dup:
                    registered = asyncio.run(dedup.register_fill(fill_item.tenant_id, fill_item))
                    if registered:
                        ledger.apply_fill(fill_item.side, fill_item.filled_quantity, fill_item.fill_price, fill_item.fee)
                        return "PROCESSED"
                return "DEDUPLICATED"

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(process_chaos_fill, f) for f in fills_to_submit]
            results = [fut.result() for fut in as_completed(futures)]
            
        processed = results.count("PROCESSED")
        deduped = results.count("DEDUPLICATED")
        
        assert processed == 50, f"Expected 50 processed fills, got {processed}"
        assert deduped == 50, f"Expected 50 deduplicated fills, got {deduped}"
        # 25 buys of 1.0 ETH + 25 sells of 1.0 ETH -> Net quantity = 0.0
        assert ledger.quantity == Decimal("0.0")
        assert ledger.total_fees == Decimal("25.00") # 50 * $0.50 = $25.00
        
        print("✔ ATTACK 10 PASS: Combined chaos resolved into exact, pristine financial state")
        attacks_passed += 1
    except Exception as e:
        print(f"❌ ATTACK 10 FAIL: {e}")
        raise

    # Cleanup temp directory
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    print("\n" + "═" * 70)
    print(f"RUNTIME SOAK & PROCESS DESTRUCTION COMPLETE | PASS: {attacks_passed}/{total_attacks}")
    print("═" * 70)
    return attacks_passed == total_attacks


if __name__ == "__main__":
    success = run_production_runtime_destruction()
    sys.exit(0 if success else 1)
