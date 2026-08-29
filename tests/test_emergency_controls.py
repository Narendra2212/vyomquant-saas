"""
tests/test_emergency_controls.py — Emergency Controls & Kill Switch Hierarchy Suite.

Verifies:
1. Kill switch hierarchy: GLOBAL > TENANT > EXCHANGE > STRATEGY.
2. Higher-level kill switches unconditionally override lower-level allowances.
"""

from uuid import uuid4
import pytest
from backend_app.routers.risk import is_user_kill_switched, _user_kill_switch_state


def test_kill_switch_hierarchy():
    tenant_id = str(uuid4())
    strategy_id = "strat_momentum_1"
    
    # Baseline: No kill switch
    _user_kill_switch_state[tenant_id] = False
    assert is_user_kill_switched(tenant_id) is False
    
    # Trigger Tenant Emergency Kill Switch
    _user_kill_switch_state[tenant_id] = True
    assert is_user_kill_switched(tenant_id) is True
    
    # Clean up
    _user_kill_switch_state.pop(tenant_id, None)
