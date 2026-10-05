"""
FIN-CRITICAL-002 Regression Test: Position Delta Race Condition Fix

Tests that position delta calculations are protected against race conditions
using row-level locking (SELECT FOR UPDATE) to prevent position drift.

This test verifies the fix for the critical race condition in position updates.

THE TWO SERVER-BACKED TESTS IN THIS FILE SKIP unless ``DATABASE_URL`` names a PostgreSQL
server; the two arithmetic tests below them still run everywhere.

``SELECT ... FOR UPDATE`` is the property under test and SQLite has no row locks -- its
parser rejects the clause (``near "FOR": syntax error``). The ``unit-tests`` job in
``.github/workflows/01-pr-check.yml`` has no ``DATABASE_URL`` by design, so
``SessionLocal()`` there is the ``sqlite:///./algo22.db`` fallback and
``test_position_update_row_level_locking`` failed on every run.

The guard is applied per test rather than as a module-level ``pytestmark`` because
``test_fill_delta_calculation_correctness`` and ``test_position_update_idempotency`` need
no database and must keep running in the SQLite lane. This is the same shape
``tests/test_strategy_lifecycle_concurrency_postgres.py`` uses for the same reason.

See ``tests/test_sqlite_lane_is_greenable.py``, which asserts both that this guard exists
and that this file is named in the ``database-tests`` job, so skipping here cannot quietly
become running nowhere.
"""

import os

import pytest
import threading
import time
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session
from backend_app.core.database import SessionLocal
from backend_app.backend.order_watchdog import OrderWatchdog
from backend_app.core.models.execution_record import ExecutionRecordModel, ExecutionStatus

requires_postgres = pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith(("postgresql", "postgres://")),
    reason=(
        "needs a real PostgreSQL through DATABASE_URL for SELECT ... FOR UPDATE; "
        "provided by the database-tests job in .github/workflows/01-pr-check.yml, "
        "skipped in the SQLite lane"
    ),
)


@requires_postgres
def test_position_update_row_level_locking():
    """
    FIN-CRITICAL-002: Verify that position updates use row-level locking.
    
    This test ensures that concurrent position updates are protected by
    SELECT FOR UPDATE to prevent race conditions and position drift.
    """
    # THE STATEMENT IS IMPORTED FROM PRODUCTION, NOT RETYPED HERE.
    #
    # This test used to carry its own copy of the SQL, which is how it came to assert a
    # statement that cannot run. The first execution of the `database-tests` job reported
    # `UndefinedColumn: column "symbol" does not exist`, and it was right: `positions` is
    # declared `id, tenant_id, data (JSON), created_at, updated_at`, and the symbol lives
    # inside `data`. `order_watchdog.py` had the identical statement at two call sites, each
    # wrapped in an `except` that logged a warning and carried on without the lock -- so the
    # "FIN-CRITICAL-002 FIX" row-level lock was never acquired on PostgreSQL.
    #
    # Calling the production helper means a future edit to that statement is tested here by
    # construction, and a copy cannot silently diverge from it again.
    # See tests/test_positions_table_column_agreement.py.
    from backend_app.backend.order_watchdog import (_position_lock_sql,
                                                    _watchdog_session_dialect)

    session = SessionLocal()
    try:
        session.execute(
            text(_position_lock_sql(_watchdog_session_dialect(session))),
            {"tenant_id": "test_tenant", "symbol": "BTC/USDT"}
        )

        # The query must execute without error (even if no rows are found). That is what
        # proves both the FOR UPDATE syntax and the column expression are valid against this
        # server. It does NOT prove a row was locked -- `positions` may be empty here, and
        # `SELECT ... FOR UPDATE` matching zero rows succeeds while locking nothing.
        print("✓ Row-level locking (SELECT FOR UPDATE) is supported")

    finally:
        session.rollback()
        session.close()


@requires_postgres
def test_concurrent_position_update_safety():
    """
    FIN-CRITICAL-002: Test that concurrent position updates are safe.

    This test simulates concurrent reconciliation workers processing the same
    order to verify that row-level locking prevents duplicate position updates.

    THIS TEST ASSERTED NOTHING UNTIL THE ASSERTIONS AT THE BOTTOM WERE ADDED. It
    collected every exception into ``errors``, printed a count, and ended. On SQLite all
    five workers failed with ``near "FOR": syntax error`` and the test still reported
    success -- it would have passed against a database with no ``positions`` table, no
    row locks and no concurrency control of any kind. The two assertions below are what
    make the run meaningful, and ``@requires_postgres`` is what stops them from failing
    in the SQLite lane where the statement cannot be parsed at all.

    WHAT THIS ESTABLISHES AND WHAT IT DOES NOT. ``FOR UPDATE`` without ``NOWAIT`` or
    ``SKIP LOCKED`` blocks rather than erroring, and there is a single lock target, so a
    correct server serialises all five workers and none of them fails: ``errors`` empty
    and five results is the right expectation, and a deadlock or a rejected lock request
    now reds the test instead of being counted as "handled". What it does not establish
    is that any row was actually locked -- ``positions`` may be empty in the provisioned
    schema, and ``SELECT ... FOR UPDATE`` matching zero rows succeeds while locking
    nothing. Proving mutual exclusion needs a seeded row and an interleaving check; that
    is a larger test than this one and is not claimed here.
    """
    results = []
    errors = []
    
    def concurrent_position_update(worker_id):
        """Simulate a concurrent position update."""
        session = SessionLocal()
        try:
            # Begin transaction
            session.begin()
            
            # Acquire row-level lock, using production's own statement rather than a copy
            # of it -- see test_position_update_row_level_locking above for why.
            from backend_app.backend.order_watchdog import (
                _position_lock_sql, _watchdog_session_dialect)

            session.execute(
                text(_position_lock_sql(_watchdog_session_dialect(session))),
                {"tenant_id": "test_tenant", "symbol": "BTC/USDT"}
            )
            
            # Simulate position update operation
            time.sleep(0.05)  # Small delay to increase chance of race condition
            
            results.append(worker_id)
            
            # Commit transaction
            session.commit()
            
        except Exception as e:
            errors.append((worker_id, str(e)))
            session.rollback()
        finally:
            session.close()
    
    # Run concurrent position updates
    threads = []
    for i in range(5):
        thread = threading.Thread(target=concurrent_position_update, args=(i,))
        threads.append(thread)
        thread.start()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
    
    # Verify no deadlocks or lock acquisition failures. `FOR UPDATE` without NOWAIT or
    # SKIP LOCKED blocks instead of erroring and there is one lock target, so a correct
    # server serialises all five workers and none of them raises.
    assert errors == [], (
        f"concurrent FOR UPDATE position updates should all succeed by blocking, but "
        f"{len(errors)} of 5 workers failed: {errors}"
    )
    assert sorted(results) == [0, 1, 2, 3, 4], (
        f"all 5 workers should have acquired the lock and committed; got {sorted(results)}"
    )


def test_fill_delta_calculation_correctness():
    """
    FIN-CRITICAL-002: Verify fill delta calculation is correct.
    
    This test ensures that fill delta is calculated correctly as the
    difference between new and previous filled sizes.
    """
    from decimal import Decimal
    
    # Test case 1: First fill
    prev_filled = Decimal("0")
    new_filled = Decimal("1.5")
    delta = max(Decimal("0"), new_filled - prev_filled)
    assert delta == Decimal("1.5"), f"Expected delta 1.5, got {delta}"
    
    # Test case 2: Partial fill
    prev_filled = Decimal("1.5")
    new_filled = Decimal("2.0")
    delta = max(Decimal("0"), new_filled - prev_filled)
    assert delta == Decimal("0.5"), f"Expected delta 0.5, got {delta}"
    
    # Test case 3: Full fill
    prev_filled = Decimal("2.0")
    new_filled = Decimal("2.0")
    delta = max(Decimal("0"), new_filled - prev_filled)
    assert delta == Decimal("0"), f"Expected delta 0, got {delta}"
    
    # Test case 4: No regression (fill size decreased - should not happen but defensive)
    prev_filled = Decimal("2.0")
    new_filled = Decimal("1.5")
    delta = max(Decimal("0"), new_filled - prev_filled)
    assert delta == Decimal("0"), f"Expected delta 0 for decreased fill, got {delta}"
    
    print("✓ Fill delta calculation is correct")


def test_position_update_idempotency():
    """
    FIN-CRITICAL-002: Test that position updates are idempotent.
    
    This test ensures that applying the same fill delta multiple times
    does not cause position drift when proper locking is in place.
    """
    # This test would require database setup with actual position records
    # For now, we test the logic conceptually
    
    from decimal import Decimal
    
    # Simulate position state
    position_size = Decimal("10.0")
    
    # Apply same fill delta twice (should not happen with proper idempotency)
    fill_delta = Decimal("1.0")
    
    # First application
    position_size += fill_delta
    assert position_size == Decimal("11.0"), f"Expected 11.0, got {position_size}"
    
    # Second application (would cause drift without idempotency protection)
    # With proper locking and delta calculation, this should not occur
    position_size += fill_delta
    assert position_size == Decimal("12.0"), f"Expected 12.0, got {position_size}"
    
    print("✓ Position update idempotency logic verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
