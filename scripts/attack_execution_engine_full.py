"""
HOSTILE EXECUTION ENGINE ATTACK SUITE — 15 Adversarial Scenarios
Uses $1M equity so the 10% guardrail = $100k, above all test positions.
"""
import sys, threading, io
from decimal import Decimal, getcontext

# Force UTF-8 output to avoid cp1252 issues with emoji in execution_engine prints
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

getcontext().prec = 28

sys.path.insert(0, r"c:\aerora_quant_backend_updated_final1")

from backend_app.core.execution_engine import ExecutionEngine, Position

PASS = 0
FAIL = 0

def result(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  <<< {detail}")


def fresh_engine(equity=Decimal("1000000")):
    """$1M default so 10% guardrail = $100k — all positions well under limit."""
    return ExecutionEngine(
        fee_rate=0.001,
        slippage=0.0,
        portfolio_state={"total_equity": equity, "available_balance": equity}
    )


# ---------------------------------------------------------------------------
# 01 open_position equity deduction == entry fee only
# ---------------------------------------------------------------------------
def test_01_open_position_equity_deduction():
    eng = fresh_engine()
    start = eng.current_equity
    ok, msg = eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="long")
    fee = Decimal("50000") * Decimal("0.001")
    expected = start - fee
    result(
        "01 open_position equity deduction == entry fee only",
        ok and eng.current_equity == expected,
        f"ok={ok} eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 02 close_position zero-PnL round-trip equity conservation
# ---------------------------------------------------------------------------
def test_02_close_position_equity_conservation():
    eng = fresh_engine()
    start = eng.current_equity
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="long")
    eng.close_position("BTC/USDT", Decimal("50000"), Decimal("1"))
    entry_fee = Decimal("50000") * Decimal("0.001")
    exit_fee  = Decimal("50000") * Decimal("0.001")
    expected  = start - entry_fee - exit_fee
    result(
        "02 close_position zero-PnL round-trip equity conservation",
        abs(eng.current_equity - expected) < Decimal("0.00001"),
        f"eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 03 close_position profitable long exact equity
# ---------------------------------------------------------------------------
def test_03_close_position_profitable_trade_equity():
    eng = fresh_engine()
    start = eng.current_equity   # 1_000_000
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="long")
    eng.close_position("BTC/USDT", Decimal("55000"), Decimal("1"))
    # entry_fee = 50, exit_fee = 55, gross_pnl = 5000, net_pnl = 4895
    # equity_after_open  = 999950
    # equity_after_close = 999950 + 4895 + 50 = 1004895
    expected = start + Decimal("5000") - Decimal("55")   # = 1004945 ← verify formula
    # Code formula: equity += net_pnl + entry_fee = (gross-entry-exit)+entry = gross-exit
    # => equity += gross_pnl - exit_fee = 5000 - 55 = 4945
    # => equity = (start - entry_fee) + 4945 = start - 50 + 4945 = start + 4895
    expected = start + Decimal("4895")
    result(
        "03 close_position profitable long exact equity",
        abs(eng.current_equity - expected) < Decimal("0.00001"),
        f"eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 04 short profitable PnL sign
# ---------------------------------------------------------------------------
def test_04_short_pnl_sign():
    eng = fresh_engine()
    start = eng.current_equity
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="short")
    eng.close_position("BTC/USDT", Decimal("45000"), Decimal("1"))
    # gross_pnl = 5000, entry_fee=50, exit_fee=45
    # net_pnl = 5000-50-45 = 4905
    # equity_after_open = start - 50
    # equity_after_close = (start-50) + 4905 + 50 = start + 4905
    expected = start + Decimal("4905")
    result(
        "04 short profitable PnL sign correctness",
        abs(eng.current_equity - expected) < Decimal("0.00001"),
        f"eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 05 short losing trade equity
# ---------------------------------------------------------------------------
def test_05_short_losing_trade_equity():
    eng = fresh_engine()
    start = eng.current_equity
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="short")
    eng.close_position("BTC/USDT", Decimal("55000"), Decimal("1"))
    # gross_pnl = -5000, entry_fee=50, exit_fee=55
    # net_pnl = -5000-50-55 = -5105
    # equity_after_open = start - 50
    # equity_after_close = (start-50) + (-5105) + 50 = start - 5105
    expected = start - Decimal("5105")
    result(
        "05 short losing trade equity correct",
        abs(eng.current_equity - expected) < Decimal("0.00001"),
        f"eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 06 partial then full close equity conservation
# ---------------------------------------------------------------------------
def test_06_partial_then_full_close_equity():
    eng = fresh_engine()
    start = eng.current_equity
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("2"), side="long")
    eng.close_position("BTC/USDT", Decimal("50000"), Decimal("1"))  # partial
    eng.close_position("BTC/USDT", Decimal("50000"), Decimal("1"))  # remaining
    # open fee: 50000*2*0.001 = 100
    # partial close fee: 50000*1*0.001 = 50
    # full close fee: 50000*1*0.001 = 50
    # all zero PnL → total fees = 200
    expected = start - Decimal("200")
    result(
        "06 partial then full close equity conservation",
        abs(eng.current_equity - expected) < Decimal("0.001"),
        f"eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 07 handle_partial_fill open: equity deducted by fee only
# ---------------------------------------------------------------------------
def test_07_partial_fill_open_equity():
    eng = fresh_engine()
    start = eng.current_equity
    fee = Decimal("0.05")
    eng.handle_partial_fill("ETH/USDT", Decimal("1"), Decimal("3000"), Decimal("2"), "buy", fee)
    result(
        "07 partial_fill open: equity deducted by fee only",
        abs(eng.current_equity - (start - fee)) < Decimal("0.00001"),
        f"eq={eng.current_equity} expected={start-fee}"
    )
    result(
        "07b partial_fill open: position created",
        "ETH/USDT" in eng.positions and eng.positions["ETH/USDT"].size == Decimal("1"),
        f"positions={eng.positions}"
    )


# ---------------------------------------------------------------------------
# 08 handle_partial_fill position flip: equity + size
# ---------------------------------------------------------------------------
def test_08_partial_fill_position_flip():
    eng = fresh_engine()
    eng.handle_partial_fill("BTC/USDT", Decimal("2"), Decimal("50000"), Decimal("2"), "buy", Decimal("100"))
    start_equity = eng.current_equity
    eng.handle_partial_fill("BTC/USDT", Decimal("3"), Decimal("55000"), Decimal("3"), "sell", Decimal("165"))
    has_short = "BTC/USDT" in eng.positions and eng.positions["BTC/USDT"].side == "short"
    short_size_ok = has_short and eng.positions["BTC/USDT"].size == Decimal("1")
    result("08a partial_fill flip: short position created", has_short, f"pos={eng.positions}")
    result("08b partial_fill flip: short size == 1", short_size_ok,
           f"size={eng.positions['BTC/USDT'].size if 'BTC/USDT' in eng.positions else 'MISSING'}")
    # gross_pnl closing long 2 @ 55000 (opened @ 50000) = (55000-50000)*2 = 10000
    # net = 10000 - 165 = 9835
    expected_equity = start_equity + Decimal("9835")
    result(
        "08c partial_fill flip: equity after flip correct",
        abs(eng.current_equity - expected_equity) < Decimal("0.01"),
        f"eq={eng.current_equity} expected={expected_equity}"
    )


# ---------------------------------------------------------------------------
# 09 is_complete boundary
# ---------------------------------------------------------------------------
def test_09_partial_fill_is_complete_boundary():
    eng = fresh_engine()
    res = eng.handle_partial_fill("BTC/USDT", Decimal("0.5"), Decimal("50000"), Decimal("1"), "buy", Decimal("25"))
    result("09a is_complete=False when filled < total", res["is_complete"] is False, f"res={res}")
    res2 = eng.handle_partial_fill("BTC/USDT", Decimal("0.5"), Decimal("50000"), Decimal("0.5"), "buy", Decimal("25"))
    result("09b is_complete=True when filled == total", res2["is_complete"] is True, f"res={res2}")


# ---------------------------------------------------------------------------
# 10 sell without position: no equity corruption
# ---------------------------------------------------------------------------
def test_10_sell_without_position():
    eng = fresh_engine()
    start = eng.current_equity
    pnl, msg = eng.close_position("FAKE/USDT", Decimal("100"), Decimal("1"))
    result(
        "10 sell without position: no equity change, pnl=0",
        eng.current_equity == start and pnl == Decimal("0"),
        f"eq={eng.current_equity} pnl={pnl}"
    )


# ---------------------------------------------------------------------------
# 11 verify_position_consistency: side mismatch
# ---------------------------------------------------------------------------
def test_11_position_consistency_side_mismatch():
    eng = fresh_engine()
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="long")
    ok, msg = eng.verify_position_consistency(
        "BTC/USDT",
        {"size": Decimal("1"), "side": "short", "entry_price": Decimal("50000")}
    )
    result(
        "11 consistency: side mismatch detected",
        not ok and "Side mismatch" in msg,
        f"ok={ok} msg={msg}"
    )


# ---------------------------------------------------------------------------
# 12 verify_position_consistency: price drift boundary
# ---------------------------------------------------------------------------
def test_12_position_consistency_price_drift():
    eng = fresh_engine()
    eng.open_position("BTC/USDT", Decimal("50000"), Decimal("1"), side="long")

    boundary_price = Decimal("50000") * Decimal("1.001")
    ok, _ = eng.verify_position_consistency(
        "BTC/USDT",
        {"size": Decimal("1"), "side": "long", "entry_price": boundary_price}
    )
    result("12a consistency: at 0.1% tolerance boundary accepted", ok)

    over_price = Decimal("50000") * Decimal("1.0011")
    ok2, msg2 = eng.verify_position_consistency(
        "BTC/USDT",
        {"size": Decimal("1"), "side": "long", "entry_price": over_price}
    )
    result("12b consistency: 0.11% drift rejected", not ok2, f"ok={ok2} msg={msg2}")


# ---------------------------------------------------------------------------
# 13 N round-trips equity conservation
# ---------------------------------------------------------------------------
def test_13_n_roundtrip_equity_conservation():
    n = 10
    price = Decimal("50000")
    size = Decimal("0.1")
    fee_per_leg = price * size * Decimal("0.001")
    eng = fresh_engine()
    start = eng.current_equity
    for i in range(n):
        eng.open_position(f"SYM{i}/USDT", price, size, side="long")
        eng.close_position(f"SYM{i}/USDT", price, size)
    expected = start - (n * 2 * fee_per_leg)
    result(
        f"13 {n} round-trips equity conservation",
        abs(eng.current_equity - expected) < Decimal("0.001"),
        f"eq={eng.current_equity} expected={expected}"
    )


# ---------------------------------------------------------------------------
# 14 handle_partial_fill average price accumulation
# ---------------------------------------------------------------------------
def test_14_partial_fill_avg_price():
    eng = fresh_engine()
    eng.handle_partial_fill("BTC/USDT", Decimal("1"), Decimal("50000"), Decimal("2"), "buy", Decimal("50"))
    eng.handle_partial_fill("BTC/USDT", Decimal("1"), Decimal("60000"), Decimal("2"), "buy", Decimal("60"))
    pos = eng.positions.get("BTC/USDT")
    expected_avg = Decimal("55000")
    result(
        "14 partial_fill avg price == 55000",
        pos is not None and abs(pos.entry_price - expected_avg) < Decimal("0.01"),
        f"avg={pos.entry_price if pos else 'N/A'}"
    )
    result(
        "14b partial_fill accumulated size == 2",
        pos is not None and pos.size == Decimal("2"),
        f"size={pos.size if pos else 'N/A'}"
    )


# ---------------------------------------------------------------------------
# 15 Concurrent open_position thread safety
# BUG PROBE: Does the guardrail properly enforce max_open_trades=5 under
# concurrency, or does a race allow more than 5 to slip through?
# EXPECTED: exactly max_open_trades=5 succeed, rest are blocked.
# ---------------------------------------------------------------------------
def test_15_concurrent_open_race():
    eng = fresh_engine(equity=Decimal("100000000"))  # $100M so 10% = $10M
    results_list = []
    lock = threading.Lock()

    def open_worker(sym):
        ok, msg = eng.open_position(sym, Decimal("100"), Decimal("0.01"), side="long")
        with lock:
            results_list.append((sym, ok, msg))

    threads = [threading.Thread(target=open_worker, args=(f"SYM{i}/USDT",)) for i in range(50)]
    for t in threads: t.start()
    for t in threads: t.join()

    opened = [r for r in results_list if r[1]]
    max_trades = eng.risk_manager.max_open_trades  # = 5

    # INVARIANT: number of opened positions must not EXCEED the guardrail limit
    # (race could let >5 slip through if check-then-act is not atomic)
    result(
        f"15 concurrent open: guardrail enforced (opened={len(opened)} <= max={max_trades})",
        len(opened) <= max_trades,
        f"opened={len(opened)} max_allowed={max_trades} — RACE BYPASS if > max"
    )
    # INVARIANT: actual positions dict must also not exceed max_trades
    actual_positions = len(eng.positions)
    result(
        f"15b concurrent open: positions dict == opened count ({actual_positions})",
        actual_positions == len(opened),
        f"positions_dict={actual_positions} opened_count={len(opened)}"
    )
    result("15c concurrent open: equity > 0", eng.current_equity > Decimal("0"), f"eq={eng.current_equity}")


# ---------------------------------------------------------------------------
print("=" * 72)
print("HOSTILE EXECUTION ENGINE ATTACK SUITE - 15 Scenarios")
print("=" * 72)

test_01_open_position_equity_deduction()
test_02_close_position_equity_conservation()
test_03_close_position_profitable_trade_equity()
test_04_short_pnl_sign()
test_05_short_losing_trade_equity()
test_06_partial_then_full_close_equity()
test_07_partial_fill_open_equity()
test_08_partial_fill_position_flip()
test_09_partial_fill_is_complete_boundary()
test_10_sell_without_position()
test_11_position_consistency_side_mismatch()
test_12_position_consistency_price_drift()
test_13_n_roundtrip_equity_conservation()
test_14_partial_fill_avg_price()
test_15_concurrent_open_race()

print("=" * 72)
print(f"RESULT: {PASS} PASS / {FAIL} FAIL out of {PASS+FAIL}")
if FAIL > 0:
    print("DEFECTS FOUND - DO NOT CERTIFY")
    sys.exit(1)
else:
    print("All passed (SOURCE_VERIFIED + LOGIC_REPRODUCED)")
    sys.exit(0)
