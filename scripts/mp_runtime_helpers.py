"""
scripts/mp_runtime_helpers.py — Multiprocessing worker helpers for runtime destruction tests.
"""

import os
import sys
import time
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core.models.execution_record import (
    ExecutionRecordRepository,
    ExecutionStatus,
)


def mp_execution_claim_worker(db_path, execution_id, tenant_id, worker_id, results_queue):
    """Real independent OS process attempting to claim the same execution."""
    try:
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30.0, "check_same_thread": False})
        Session = sessionmaker(bind=engine)
        db = Session()
        repo = ExecutionRecordRepository(db)
        
        claimed, record = repo.claim_execution(
            execution_id=execution_id,
            tenant_id=tenant_id
        )
        db.close()
        results_queue.put({
            "worker_id": worker_id,
            "claimed": claimed,
            "error": None
        })
    except Exception as e:
        results_queue.put({
            "worker_id": worker_id,
            "claimed": False,
            "error": str(e)
        })


def mp_wallet_debit_worker(db_path, user_id, amount_dec_str, worker_id, results_queue):
    """Real independent OS process executing atomic optimistic wallet debits."""
    try:
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30.0, "check_same_thread": False})
        with engine.begin() as conn:
            res = conn.execute(
                text("UPDATE test_wallets SET balance = balance - :amt WHERE user_id = :uid AND balance >= :amt"),
                {"amt": float(amount_dec_str), "uid": user_id}
            )
            success = (res.rowcount == 1)
        results_queue.put({"worker_id": worker_id, "success": success, "error": None})
    except Exception as e:
        results_queue.put({"worker_id": worker_id, "success": False, "error": str(e)})


def mp_killable_financial_worker(db_path, execution_id, tenant_id, progress_queue):
    """Worker designed to be killed mid-operation."""
    try:
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30.0, "check_same_thread": False})
        Session = sessionmaker(bind=engine)
        db = Session()
        repo = ExecutionRecordRepository(db)
        
        # Step 1: Claim
        claimed, record = repo.claim_execution(
            execution_id=execution_id,
            tenant_id=tenant_id
        )
        if claimed:
            progress_queue.put("CLAIMED")
        else:
            progress_queue.put("FAILED_TO_CLAIM")
            db.close()
            return
        
        # Simulating heavy work / external call
        time.sleep(2.0)
        
        # Step 2: Complete
        repo.update_status(
            execution_id=execution_id,
            tenant_id=tenant_id,
            status=ExecutionStatus.COMPLETED
        )
        db.close()
    except Exception as e:
        progress_queue.put(f"ERROR: {e}")
