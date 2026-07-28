"""
tests/test_database_pool_math.py — Database Connection Pool Math & Capacity Validation

Validates worst-case database connection counts against Supabase Pooler (PgBouncer/Supavisor) limits.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend_app.core.database_pool import validate_pool_capacity, POOL_SIZE, MAX_OVERFLOW


def test_default_pool_capacity_validation():
    """Verify that default POOL_SIZE and MAX_OVERFLOW fit safely under 200 Supabase pooler limit."""
    status = validate_pool_capacity(
        max_api_replicas=10,
        workers_per_replica=2,
        pool_size=5,
        max_overflow=3,
        background_worker_processes=6,
        worker_pool_size=2,
        worker_max_overflow=1,
        supabase_pooler_limit=200,
    )
    
    assert status["is_safe"] is True, "Default pool configuration must stay below Supabase 200 limit"
    assert status["total_api_conn"] == 160, "10 replicas * 2 workers * (5+3) = 160 API connections"
    assert status["total_worker_conn"] == 18, "6 workers * (2+1) = 18 worker connections"
    assert status["total_max_connections"] == 178, "Total connections must equal 178"
    assert status["headroom"] == 22, "Reserved headroom must equal 22 connections"
    assert status["safety_margin_pct"] >= 10.0, "Safety margin must be at least 10%"
    print(f"[PASS] Default pool capacity validated: {status['total_max_connections']}/{status['supabase_pooler_limit']} (Headroom: {status['headroom']}, Margin: {status['safety_margin_pct']}%)")


def test_legacy_pool_capacity_would_exceed():
    """Verify that old POOL_SIZE=20, MAX_OVERFLOW=10 would fail safety check."""
    status = validate_pool_capacity(
        max_api_replicas=10,
        workers_per_replica=2,
        pool_size=20,
        max_overflow=10,
        background_worker_processes=6,
        worker_pool_size=5,
        worker_max_overflow=2,
        supabase_pooler_limit=200,
    )
    
    assert status["is_safe"] is False, "Legacy 20+10 pool size should fail safety validation for 10 replicas"
    assert status["total_max_connections"] == 642, "Legacy configuration results in 642 connections"
    print(f"[PASS] Legacy pool size correctly caught as unsafe ({status['total_max_connections']} > {status['supabase_pooler_limit']})")


def test_pro_tier_headroom():
    """Verify margin under Supabase Pro tier (500 limit)."""
    status = validate_pool_capacity(
        max_api_replicas=10,
        workers_per_replica=2,
        pool_size=5,
        max_overflow=3,
        background_worker_processes=6,
        worker_pool_size=2,
        worker_max_overflow=1,
        supabase_pooler_limit=500,
    )
    
    assert status["is_safe"] is True
    assert status["safety_margin_pct"] > 60.0, "Pro tier margin should exceed 60%"
    print(f"[PASS] Pro tier headroom validated: {status['total_max_connections']}/500 (Margin: {status['safety_margin_pct']}%)")


if __name__ == "__main__":
    test_default_pool_capacity_validation()
    test_legacy_pool_capacity_would_exceed()
    test_pro_tier_headroom()
    print("\nALL POOL MATH TESTS PASSED!")
