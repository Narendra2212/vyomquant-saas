import asyncio
from decimal import Decimal, ROUND_HALF_UP
import datetime
from typing import Any
import sys
import os

# Add path so we can import core
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend_app.core.execution_engine import ExecutionEngine, Position
from backend_app.core.risk_manager import RiskManager

passes = []
failures = []
md = []

def q(val: Any) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.00000001"))

def record_result(test_name: str, expected: Any, actual: Any, detail: str = ""):
    if type(expected) in (Decimal, float, int) and type(expected) is not bool:
        passed = q(expected) == q(actual)
    else:
        passed = str(expected) == str(actual)
        
    if passed:
        passes.append(test_name)
        msg = f"- ✅ **PASS**: {test_name} (Expected: {expected}, Actual: {actual}) {detail}"
        print(msg)
        md.append(msg)
    else:
        failures.append(test_name)
        msg = f"- ❌ **FAIL**: {test_name} (Expected: {expected}, Actual: {actual}) {detail}"
        print(msg)
        md.append(msg)

def create_engine(fee_rate=0.001, slippage=0.0000, initial_equity=10000000.0):
    engine = ExecutionEngine(
        fee_rate=fee_rate,
        slippage=slippage,
        portfolio_state={'total_equity': initial_equity}
    )
    # Override apply_slippage for deterministic testing
    engine.apply_slippage = lambda price, side: q(price)
    return engine

def test_1_single_buy():
    md.append("\n### Test 1: Single BUY")
    engine = create_engine(fee_rate=0.001)
    # Buy 1 BTC at 50,000
    success, msg = engine.open_position("BTC", Decimal("50000"), Decimal("1"))
    if not success:
        record_result("T1_BUY_SUCCESS", True, False, msg)
        return
    
    pos = engine.positions.get("BTC")
    record_result("T1_EXPECTED_QTY", 1, pos.size)
    record_result("T1_EXPECTED_PRICE", 50000, pos.entry_price)
    # Fee = 50000 * 0.001 = 50
    record_result("T1_EXPECTED_EQUITY", 9999950, engine.current_equity)

def test_2_buy_sell():
    md.append("\n### Test 2: BUY → SELL")
    engine = create_engine(fee_rate=0.001)
    engine.open_position("BTC", Decimal("50000"), Decimal("1")) # Fee 50
    pnl, msg = engine.close_position("BTC", Decimal("60000"), Decimal("1")) # Gross +10000, Fee 60
    # Net PNL = 10000 - 110 = 9890
    record_result("T2_REALIZED_PNL", 9890, pnl)
    record_result("T2_QTY_AFTER", 0, len(engine.positions))
    record_result("T2_EXPECTED_EQUITY", 10009890, engine.current_equity)

def test_3_partial_sell():
    md.append("\n### Test 3: Partial SELL")
    engine = create_engine(fee_rate=0.001)
    engine.open_position("BTC", Decimal("50000"), Decimal("2")) # Fee 100
    
    # Sell 1 BTC at 60000 using handle_partial_fill
    # handle_partial_fill deducts fee from equity and returns PNL in closed_pnl
    fee_sell = Decimal("60000") * Decimal("1") * Decimal("0.001") # 60
    res = engine.handle_partial_fill("BTC", Decimal("1"), Decimal("60000"), Decimal("1"), "sell", fee_sell)
    
    pos = engine.positions.get("BTC")
    record_result("T3_EXPECTED_QTY", 1, pos.size)
    record_result("T3_AVG_PRICE_UNCHANGED", 50000, pos.entry_price)
    
    # Original equity 10,000,000
    # Buy fee: 100 -> eq=9,999,900
    # Sell gross PNL = 1 * (60000 - 50000) = +10000
    # Sell fee = 60 -> net PNL = 9940
    # New equity = 9,999,900 + 9940 = 10,009,840
    record_result("T3_EXPECTED_EQUITY", 10009840, engine.current_equity)

def test_4_multiple_buy_averaging():
    md.append("\n### Test 4: Multiple BUY averaging")
    engine = create_engine(fee_rate=0.0)
    engine.handle_partial_fill("BTC", Decimal("1"), Decimal("50000"), Decimal("1"), "buy", Decimal("0"))
    engine.handle_partial_fill("BTC", Decimal("1"), Decimal("40000"), Decimal("1"), "buy", Decimal("0"))
    
    pos = engine.positions.get("BTC")
    record_result("T4_EXPECTED_QTY", 2, pos.size)
    record_result("T4_AVG_PRICE", 45000, pos.entry_price)

def test_5_multiple_sell_averaging():
    md.append("\n### Test 5: Multiple SELL averaging")
    engine = create_engine(fee_rate=0.0)
    engine.handle_partial_fill("BTC", Decimal("2"), Decimal("50000"), Decimal("2"), "buy", Decimal("0"))
    
    # Sell 1 at 60k
    engine.handle_partial_fill("BTC", Decimal("1"), Decimal("60000"), Decimal("1"), "sell", Decimal("0"))
    # Equity = 100k + 10k = 110k
    
    # Sell 1 at 40k
    engine.handle_partial_fill("BTC", Decimal("1"), Decimal("40000"), Decimal("1"), "sell", Decimal("0"))
    # Equity = 110k - 10k = 100k
    
    record_result("T5_EXPECTED_QTY", 0, len(engine.positions))
    record_result("T5_EXPECTED_EQUITY", 10000000, engine.current_equity)

def test_6_fee_deduction():
    md.append("\n### Test 6: Fee deduction")
    engine = create_engine(fee_rate=0.0025) # 0.25% fee
    engine.open_position("BTC", Decimal("10000"), Decimal("1"))
    # Fee = 25
    record_result("T6_FEE_DEDUCTION_ENTRY", 9999975, engine.current_equity)
    pnl, msg = engine.close_position("BTC", Decimal("10000"), Decimal("1"))
    # Gross 0. Exit fee 25. Net PNL -50
    record_result("T6_FEE_DEDUCTION_EXIT", 9999950, engine.current_equity)

def test_7_slippage_impact():
    md.append("\n### Test 7: Slippage impact")
    engine = create_engine(fee_rate=0.0)
    # Enable exactly 0.01 (1%) slippage
    engine.apply_slippage = lambda price, side: (price * Decimal("1.01")).quantize(Decimal("0.00000001"))
    
    engine.open_position("BTC", Decimal("50000"), Decimal("1"))
    pos = engine.positions.get("BTC")
    record_result("T7_SLIPPAGE_PRICE", 50500, pos.entry_price)

def test_8_unrealized_pnl():
    md.append("\n### Test 8: Unrealized PnL")
    engine = create_engine(fee_rate=0.0)
    engine.open_position("BTC", Decimal("50000"), Decimal("1"))
    pos = engine.positions.get("BTC")
    upnl = pos.unrealized_pnl(Decimal("65000"))
    record_result("T8_UNREALIZED_PNL", 15000, upnl)

def test_9_realized_pnl():
    md.append("\n### Test 9: Realized PnL")
    engine = create_engine(fee_rate=0.001)
    engine.open_position("ETH", Decimal("3000"), Decimal("10")) # 30000 * 0.001 = 30 fee
    pnl, _ = engine.close_position("ETH", Decimal("4000"), Decimal("10")) # Gross 10000, Exit Fee 40, Total Fee 70 -> Net 9930
    record_result("T9_REALIZED_PNL", 9930, pnl)

def test_10_portfolio_equity():
    md.append("\n### Test 10: Portfolio equity calculation")
    engine = create_engine(initial_equity=10000000.0, fee_rate=0.0)
    # Gain 200, lose 100
    engine.open_position("SOL", Decimal("100"), Decimal("1"))
    engine.close_position("SOL", Decimal("300"), Decimal("1")) # +200
    
    engine.open_position("ADA", Decimal("100"), Decimal("1"))
    engine.close_position("ADA", Decimal("0"), Decimal("1")) # -100
    
    record_result("T10_EQUITY", 10000100, engine.current_equity)

def test_11_drawdown_calculation():
    md.append("\n### Test 11: Drawdown calculation")
    engine = create_engine(initial_equity=100000.0, fee_rate=0.0)
    # Peak is 100,000. We lose 15,000. Current is 85,000. Drawdown is 15%. Max drawdown limit is 10%.
    engine.risk_manager.update_equity(-15000)
    
    allowed, reason = engine.risk_manager.can_trade()
    record_result("T11_DRAWDOWN_REASON", True, "MAX DRAWDOWN HIT" in reason)
    record_result("T11_DRAWDOWN_BLOCKED", False, allowed)

async def test_12_concurrent_updates():
    md.append("\n### Test 12: Concurrent position updates")
    # Python dict operations are somewhat atomic but let's test async concurrent fills
    engine = create_engine(fee_rate=0.0)
    
    async def fill_order(qty):
        engine.handle_partial_fill("BTC", Decimal(str(qty)), Decimal("50000"), Decimal("100"), "buy", Decimal("0"))
        
    # 100 concurrent partial fills of 1 BTC
    tasks = [fill_order(1) for _ in range(100)]
    await asyncio.gather(*tasks)
    
    pos = engine.positions.get("BTC")
    record_result("T12_CONCURRENT_QTY", 100, pos.size)
    record_result("T12_CONCURRENT_PRICE", 50000, pos.entry_price)

async def run_all():
    print("Starting Financial Correctness tests...")
    test_1_single_buy()
    test_2_buy_sell()
    test_3_partial_sell()
    test_4_multiple_buy_averaging()
    test_5_multiple_sell_averaging()
    test_6_fee_deduction()
    test_7_slippage_impact()
    test_8_unrealized_pnl()
    test_9_realized_pnl()
    test_10_portfolio_equity()
    test_11_drawdown_calculation()
    await test_12_concurrent_updates()
    
    verdict = "FINANCIALLY_CORRECT" if len(failures) == 0 else "FINANCIAL_MISMATCH_FOUND"
    
    doc = f"""# FINANCIAL CORRECTNESS CERTIFICATION

**Timestamp:** {datetime.datetime.utcnow().isoformat()}

## Summary
- **Tests Passed:** {len(passes)}
- **Tests Failed:** {len(failures)}

## Results
{chr(10).join(md)}

## Final Verdict: {verdict}
"""
    with open("financial_correctness_certification.md", "w", encoding="utf-8") as f:
        f.write(doc)
    
    print(f"\nCompleted. Verdict: {verdict}. Passes: {len(passes)}, Fails: {len(failures)}")

if __name__ == "__main__":
    asyncio.run(run_all())
