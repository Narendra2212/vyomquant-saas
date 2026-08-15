import sys
import os

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from backend_app.core.execution_engine import ExecutionEngine
    print("ExecutionEngine successfully imported.")
    
    # Run test
    from backend_app.validate_financials import (
        test_1_buy_order, test_2_sell_order, test_3_short_order,
        test_4_cover_order, test_5_multiple_buys_wap, test_6_fractional_shares,
        test_7_crypto_satoshi, test_8_unrealized_pnl, test_9_realized_pnl,
        test_10_large_portfolio, passes, failures
    )
    
    test_1_buy_order()
    test_2_sell_order()
    test_3_short_order()
    test_4_cover_order()
    test_5_multiple_buys_wap()
    test_6_fractional_shares()
    test_7_crypto_satoshi()
    test_8_unrealized_pnl()
    test_9_realized_pnl()
    test_10_large_portfolio()
    
    print(f"\nFINANCIAL CORRECTNESS SUMMARY: {len(passes)} PASSES, {len(failures)} FAILURES")
    assert len(failures) == 0, f"Found {len(failures)} failures!"
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
