"""
Unit test to prove the None keys guard in market.py works correctly.
This directly tests the mechanism that was failing in test_market_ticker.
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from backend_app.routers.market import _get_data_engine


class MockVault:
    """Mock vault that returns None - simulating the test environment failure"""
    def load_decrypted_keys(self, user_id, exchange_id, access_token=None):
        return None


class MockVaultIncomplete:
    """Mock vault that returns incomplete dict - another failure mode"""
    def load_decrypted_keys(self, user_id, exchange_id, access_token=None):
        return {"api_key": "test"}  # Missing secret_key


def test_none_keys_guard_catches_and_fallback():
    """
    Test that when vault returns None, the ValueError is raised and caught,
    causing fallback to public connection.
    """
    async def run_test():
        user = {"id": "test-user", "access_token": "test-token"}
        vault = MockVault()
        exchange_id = "binance"
        
        # This should NOT raise TypeError anymore
        # It should catch the ValueError and fall back to public connection
        try:
            de = await _get_data_engine(user, vault, exchange_id)
            # If we get here, the fallback worked
            assert de is not None
            print("✓ None keys guard worked - fallback to public connection succeeded")
        except TypeError as e:
            pytest.fail(f"TypeError should have been caught by guard: {e}")
    
    asyncio.run(run_test())


def test_incomplete_dict_guard_catches_and_fallback():
    """
    Test that when vault returns incomplete dict, the ValueError is raised and caught.
    """
    async def run_test():
        user = {"id": "test-user", "access_token": "test-token"}
        vault = MockVaultIncomplete()
        exchange_id = "binance"
        
        # This should NOT raise TypeError from missing dict keys
        try:
            de = await _get_data_engine(user, vault, exchange_id)
            # If we get here, the fallback worked
            assert de is not None
            print("✓ Incomplete dict guard worked - fallback to public connection succeeded")
        except (TypeError, KeyError) as e:
            pytest.fail(f"TypeError/KeyError should have been caught by guard: {e}")
    
    asyncio.run(run_test())


if __name__ == "__main__":
    print("Testing None keys guard...")
    test_none_keys_guard_catches_and_fallback()
    
    print("Testing incomplete dict guard...")
    test_incomplete_dict_guard_catches_and_fallback()
    
    print("All mechanism tests passed!")
