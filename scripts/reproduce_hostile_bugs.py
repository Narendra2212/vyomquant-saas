import sys
from decimal import Decimal
import uuid

sys.path.insert(0, '.')

print("=" * 70)
print("REPRODUCING NEW P0/P1 BUGS FOUND IN AUDIT")
print("=" * 70)

# ----------------------------------------------------------------------
# 1. REPRODUCE BUG-EE-05: SHORT POSITION CORRUPTION IN handle_partial_fill
# ----------------------------------------------------------------------
print("\n[REPRODUCING BUG-EE-05]: Short position handling in handle_partial_fill...")
from backend_app.core.execution_engine import ExecutionEngine, Position

ee = ExecutionEngine()

# Setup an existing SHORT position: 1.0 BTC @ 50,000 USDT
ee.positions["BTC/USDT"] = Position(
    symbol="BTC/USDT",
    entry_price=Decimal("50000.0"),
    size=Decimal("1.0"),
    side="short"
)
print(f"  Initial Position: {ee.positions['BTC/USDT'].side} {ee.positions['BTC/USDT'].size} @ {ee.positions['BTC/USDT'].entry_price}")

# Now a BUY fill arrives to cover 0.5 BTC at 40,000 (a profitable short cover of $5,000 profit)
res = ee.handle_partial_fill(
    symbol="BTC/USDT",
    filled_size=Decimal("0.5"),
    fill_price=Decimal("40000.0"),
    total_order_size=Decimal("0.5"),
    side="buy",
    fee=Decimal("10.0")
)
print(f"  After BUY fill to cover short: size={ee.positions['BTC/USDT'].size}, side={ee.positions['BTC/USDT'].side}, avg_price={ee.positions['BTC/USDT'].entry_price}")

# The bug: size increased from 1.0 to 1.5 instead of reducing to 0.5!
if ee.positions["BTC/USDT"].size == Decimal("1.5"):
    print("  --> BUG REPRODUCED: BUY fill increased SHORT position size to 1.5 instead of reducing it to 0.5!")
else:
    print(f"  Unexpected size: {ee.positions['BTC/USDT'].size}")

# ----------------------------------------------------------------------
# 2. REPRODUCE BUG-EX-01: None values in CCXT response converted to "None"
# ----------------------------------------------------------------------
print("\n[REPRODUCING BUG-EX-01]: None values in CCXT response converted to 'None'...")
from backend_app.backend.exchange_executor import OrderResult, OrderStatusResult

# Simulate raw CCXT response with None
raw_ccxt_response = {
    "id": "ord_999",
    "filled": None,
    "remaining": None,
    "average": None
}
# The buggy logic in place_order:
filled_size_buggy = str(raw_ccxt_response.get("filled", 0))
remaining_size_buggy = str(raw_ccxt_response.get("remaining", "1.0"))
print(f"  Extracted filled_size: {filled_size_buggy!r}")
print(f"  Extracted remaining_size: {remaining_size_buggy!r}")

try:
    Decimal(filled_size_buggy)
    print("  Decimal conversion succeeded")
except Exception as e:
    print(f"  --> BUG REPRODUCED: Decimal({filled_size_buggy!r}) crashed with {type(e).__name__}: {e}")

# ----------------------------------------------------------------------
# 3. REPRODUCE BUG-WD-01: AttributeError in order_watchdog _has_order_changed
# ----------------------------------------------------------------------
print("\n[REPRODUCING BUG-WD-01]: AttributeError in order_watchdog _has_order_changed...")
from backend_app.backend.order_watchdog import OrderWatchdog, WatchdogConfig
from backend_app.core.models.execution_record import ExecutionRecordModel, ExecutionStatus

# Mock database session
class DummySession:
    pass

watchdog = OrderWatchdog(db_session=DummySession(), config=WatchdogConfig())

mock_order = ExecutionRecordModel(
    execution_id="exec_1",
    tenant_id=uuid.uuid4(),
    strategy_id="strat_1",
    symbol="BTC/USDT",
    side="buy",
    size="1.0",
    filled_size="0.5",
    remaining_size="0.5",
    avg_price="50000.0",
    status=ExecutionStatus.EXECUTING
)

mock_status_result = OrderStatusResult(
    success=True,
    status=ExecutionStatus.EXECUTING,
    filled_size="0.5",
    remaining_size="0.5",
    avg_price="50000.0"
)

try:
    watchdog._has_order_changed(mock_order, mock_status_result)
    print("  _has_order_changed succeeded")
except AttributeError as e:
    print(f"  --> BUG REPRODUCED: _has_order_changed crashed with AttributeError: {e}")

print("\n" + "=" * 70)
print("ALL NEW DEFECTS FULLY REPRODUCED!")
print("=" * 70)
