import os
import sys
from decimal import Decimal
import random

sys.path.insert(0, '.')

print("=" * 80)
print("DEEP HOSTILE ADVERSARIAL ATTACK: POSITION REVERSALS, SLIPPAGE, FRACTIONALS")
print("=" * 80)

from backend_app.core.execution_engine import ExecutionEngine, Position

# ======================================================================
# ATTACK SUITE 1: BUG-EE-08 (Short Entry Slippage Direction & Configs)
# ======================================================================
print("\n[ATTACK SUITE 1]: Attacking Slippage Directions & Edge Configurations...")
ee = ExecutionEngine()
ee.current_equity = Decimal("1000000.0")
ee.risk_manager.max_position_pct = 0.99
ee.risk_manager.max_daily_loss = 0.99
ee.risk_manager.max_open_trades = 100

# 1. Long Entry: Must slip upwards (worse for buyer -> execution > 100.0)
ee.slippage = 0.005 # 0.5%
success_long, msg_long = ee.open_position("SOL/USDT", price=Decimal("100.0"), size=Decimal("1.0"), side="long")
assert success_long is True
pos_long = ee.positions.pop("SOL/USDT")
assert pos_long.entry_price > Decimal("100.0"), f"Expected long entry > 100.0, got {pos_long.entry_price}"
print(f"  Long Entry Price: {pos_long.entry_price} (Adverse up: PASS)")

# 2. Short Entry: Must slip downwards (worse for short seller -> execution < 100.0)
success_short, msg_short = ee.open_position("SOL/USDT", price=Decimal("100.0"), size=Decimal("1.0"), side="short")
assert success_short is True
pos_short = ee.positions.pop("SOL/USDT")
assert pos_short.entry_price < Decimal("100.0"), f"Expected short entry < 100.0, got {pos_short.entry_price}"
print(f"  Short Entry Price: {pos_short.entry_price} (Adverse down: PASS)")
print("  --> BUG-EE-08 (Directional Entry Slippage on Long vs Short): RUNTIME_PROVEN PASS")

# 3. Slippage = 0 configuration: Must yield exact price
ee.slippage = 0.0
price_zero_slip = ee.apply_slippage(Decimal("123.456789"), "buy")
assert price_zero_slip == Decimal("123.456789"), f"Expected exact price with slippage 0, got {price_zero_slip}"
print("  Slippage = 0: PASS")

# 4. Slippage = None configuration: Must safely fallback to default without crashing
ee.slippage = None
price_none_slip = ee.apply_slippage(Decimal("100.0"), "buy")
assert price_none_slip > Decimal("100.0")
print("  Slippage = None: PASS")

# 5. Negative slippage configuration: Must take absolute value and remain adverse
ee.slippage = -0.002
price_neg_slip_buy = ee.apply_slippage(Decimal("100.0"), "buy")
assert price_neg_slip_buy > Decimal("100.0")
price_neg_slip_sell = ee.apply_slippage(Decimal("100.0"), "sell")
assert price_neg_slip_sell < Decimal("100.0")
print("  Negative Slippage Config Handled Adversely: PASS")

# ======================================================================
# ATTACK SUITE 2: High-Precision Fractional Position Reversals (8 Decimals)
# ======================================================================
print("\n[ATTACK SUITE 2]: Testing 8-Decimal Fractional Position Reversals...")
ee_frac = ExecutionEngine()
ee_frac.current_equity = Decimal("1000000.0")

# Step 1: Open Long 1.00000000 BTC @ $50,000.00
print("  Step 1: Open Long 1.00000000 BTC @ $50,000.00")
ee_frac.positions["BTC/USDT"] = Position("BTC/USDT", Decimal("50000.0"), Decimal("1.00000000"), "long")

# Step 2: Micro-oversell: Sell 1.00000001 BTC @ $60,000.00 (Flip to Short 0.00000001 BTC)
print("  Step 2: Sell 1.00000001 BTC @ $60,000.00 (Flips to Short 0.00000001 BTC)...")
res1 = ee_frac.handle_partial_fill(
    symbol="BTC/USDT",
    filled_size=Decimal("1.00000001"),
    fill_price=Decimal("60000.0"),
    total_order_size=Decimal("1.00000001"),
    side="sell",
    fee=Decimal("6.00000006")
)
pos1 = ee_frac.positions.get("BTC/USDT")
assert pos1 is not None, "Position must exist after micro-reversal"
assert pos1.side == "short", f"Expected short, got {pos1.side}"
assert pos1.size == Decimal("0.00000001"), f"Expected size 0.00000001, got {pos1.size}"
assert pos1.entry_price == Decimal("60000.0")
print(f"  Flipped Short Position: {pos1.size} BTC @ {pos1.entry_price} (PASS)")

# Step 3: Massive Buy Reversal: Buy 2.00000000 BTC @ $55,000.00 (Flips to Long 1.99999999 BTC)
print("  Step 3: Buy 2.00000000 BTC @ $55,000.00 (Flips to Long 1.99999999 BTC)...")
res2 = ee_frac.handle_partial_fill(
    symbol="BTC/USDT",
    filled_size=Decimal("2.00000000"),
    fill_price=Decimal("55000.0"),
    total_order_size=Decimal("2.00000000"),
    side="buy",
    fee=Decimal("11.0")
)
pos2 = ee_frac.positions.get("BTC/USDT")
assert pos2 is not None, "Position must exist after buy reversal"
assert pos2.side == "long", f"Expected long, got {pos2.side}"
assert pos2.size == Decimal("1.99999999"), f"Expected size 1.99999999, got {pos2.size}"
assert pos2.entry_price == Decimal("55000.0")
print(f"  Flipped Long Position: {pos2.size} BTC @ {pos2.entry_price} (PASS)")

# Step 4: Exact Close: Sell 1.99999999 BTC @ $58,000.00 (Position goes to 0 / None)
print("  Step 4: Sell exact 1.99999999 BTC @ $58,000.00 (Position closes cleanly)...")
res3 = ee_frac.handle_partial_fill(
    symbol="BTC/USDT",
    filled_size=Decimal("1.99999999"),
    fill_price=Decimal("58000.0"),
    total_order_size=Decimal("1.99999999"),
    side="sell",
    fee=Decimal("11.59999994")
)
assert "BTC/USDT" not in ee_frac.positions, "Position must be deleted after exact full close"
print("  Position cleanly removed on exact close: PASS")

print("\n" + "=" * 80)
print("ALL DEEP HOSTILE ADVERSARIAL ATTACKS PASSED WITH REAL RUNTIME PROOF!")
print("=" * 80)
