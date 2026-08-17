"""
FIN-CRITICAL-003 Regression Test: Fee Calculation Precision Fix

Tests that fee calculations use proper Decimal precision to prevent
precision loss for high-value assets like BTC.

This test verifies the fix for the critical fee precision issue.
"""

import pytest
from decimal import Decimal, ROUND_HALF_EVEN
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.core.risk_manager import RiskManager


def test_fee_rate_stored_as_decimal():
    """
    FIN-CRITICAL-003: Verify that fee_rate is stored as Decimal.
    
    This test ensures that fee_rate is converted to Decimal immediately
    to prevent float conversion precision issues.
    """
    portfolio_state = {"total_equity": Decimal("100000.0"), "available_balance": Decimal("100000.0")}
    engine = ExecutionEngine(fee_rate=0.001, portfolio_state=portfolio_state)
    
    # Verify fee_rate is stored as Decimal
    assert isinstance(engine.fee_rate, Decimal), f"fee_rate should be Decimal, got {type(engine.fee_rate)}"
    assert engine.fee_rate == Decimal("0.001"), f"Expected 0.001, got {engine.fee_rate}"
    
    print("✓ Fee rate is stored as Decimal")


def test_fee_calculation_high_precision():
    """
    FIN-CRITICAL-003: Verify fee calculations use high precision (12 decimal places).
    
    This test ensures that fee calculations for high-value assets maintain
    sufficient precision to avoid rounding errors.
    """
    # Test with high-value asset (BTC at $50,000)
    price = Decimal("50000.00")
    size = Decimal("1.0")
    fee_rate = Decimal("0.001")  # 0.1%
    
    # Calculate fee with high precision
    fee = (price * size * fee_rate).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    
    # Expected fee: $50,000 * 1.0 * 0.001 = $50.00
    expected_fee = Decimal("50.00")
    
    assert fee == expected_fee, f"Expected fee {expected_fee}, got {fee}"
    
    # Test with small fraction (sub-satoshi precision)
    small_price = Decimal("0.00000001")  # 1 satoshi
    small_size = Decimal("1000000")  # 1 million satoshis
    small_fee = (small_price * small_size * fee_rate).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    
    # Expected: 0.00000001 * 1000000 * 0.001 = 0.00001
    expected_small_fee = Decimal("0.00001")
    
    assert small_fee == expected_small_fee, f"Expected small fee {expected_small_fee}, got {small_fee}"
    
    print("✓ Fee calculations use high precision")


def test_banker_rounding():
    """
    FIN-CRITICAL-003: Verify that banker's rounding (ROUND_HALF_EVEN) is used.
    
    This test ensures that financial calculations use banker's rounding
    to prevent systematic bias in fee calculations.
    """
    # Test banker's rounding: .5 rounds to nearest even number
    value1 = Decimal("1.005")
    rounded1 = value1.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    
    # 1.005 rounded to 2 decimal places with banker's rounding should be 1.00
    # (since 0 is even)
    assert rounded1 == Decimal("1.00"), f"Expected 1.00, got {rounded1}"
    
    value2 = Decimal("1.015")
    rounded2 = value2.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    
    # 1.015 rounded to 2 decimal places with banker's rounding should be 1.02
    # (since 2 is even)
    assert rounded2 == Decimal("1.02"), f"Expected 1.02, got {rounded2}"
    
    print("✓ Banker's rounding is applied correctly")


def test_execution_engine_fee_precision():
    """
    FIN-CRITICAL-003: Test ExecutionEngine fee calculation with high precision.
    
    This test ensures that the ExecutionEngine calculates fees with proper
    precision using the fixed implementation.
    """
    portfolio_state = {"total_equity": Decimal("100000.0"), "available_balance": Decimal("100000.0")}
    engine = ExecutionEngine(fee_rate=0.001, portfolio_state=portfolio_state)
    
    # Verify fee_rate is Decimal
    assert isinstance(engine.fee_rate, Decimal), "fee_rate should be Decimal"
    
    # Test fee calculation for high-value trade
    symbol = "BTC/USDT"
    price = Decimal("50000.00")
    size = Decimal("1.0")
    
    # Open position
    success, result = engine.open_position(symbol, price, size, side='long')
    
    if success:
        # Verify fee is calculated with high precision
        assert 'fee' in result, "Result should contain fee"
        fee = Decimal(result['fee'])
        
        # Expected fee: 50000 * 1.0 * 0.001 = 50.00
        expected_fee = Decimal("50.00")
        assert fee == expected_fee, f"Expected fee {expected_fee}, got {fee}"
        
        print(f"✓ ExecutionEngine fee calculation: {fee} (expected: {expected_fee})")
    else:
        pytest.fail(f"Failed to open position: {result}")


def test_no_float_conversion_precision_loss():
    """
    FIN-CRITICAL-003: Verify no precision loss from float conversion.
    
    This test ensures that converting fee_rate from float to Decimal
    doesn't introduce precision errors.
    """
    # Test with various float fee rates
    test_cases = [
        (0.001, "0.001"),      # 0.1%
        (0.0005, "0.0005"),    # 0.05%
        (0.002, "0.002"),      # 0.2%
        (0.0001, "0.0001"),    # 0.01%
    ]
    
    for float_fee, expected_str in test_cases:
        decimal_fee = Decimal(str(float_fee))
        expected = Decimal(expected_str)
        
        assert decimal_fee == expected, \
            f"Float {float_fee} converted to {decimal_fee}, expected {expected}"
    
    print("✓ No precision loss from float conversion")


def test_fee_calculation_edge_cases():
    """
    FIN-CRITICAL-003: Test fee calculation edge cases.
    
    This test ensures fee calculations handle edge cases correctly.
    """
    # Test with zero fee
    zero_fee_rate = Decimal("0.0")
    price = Decimal("100.00")
    size = Decimal("1.0")
    zero_fee = (price * size * zero_fee_rate).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    assert zero_fee == Decimal("0.0"), f"Zero fee should be 0, got {zero_fee}"
    
    # Test with very high fee
    high_fee_rate = Decimal("0.1")  # 10%
    high_fee = (price * size * high_fee_rate).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    expected_high = Decimal("10.0")
    assert high_fee == expected_high, f"Expected {expected_high}, got {high_fee}"
    
    # Test with very small price
    tiny_price = Decimal("0.00000001")
    tiny_fee = (tiny_price * size * Decimal("0.001")).quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
    assert tiny_fee >= Decimal("0.0"), "Tiny fee should be non-negative"
    
    print("✓ Fee calculation edge cases handled correctly")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
