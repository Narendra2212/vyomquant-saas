import asyncio
import sys
import os
import importlib
import subprocess
import time
from decimal import Decimal
import uuid
import ccxt.async_support as ccxt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "aerora_quant_backend_updated_final1"))

from backend.master_executor import BotRunner
from core.execution_engine import ExecutionEngine
from core.risk_manager import RiskManager

async def test_1():
    try:
        rm = RiskManager(initial_equity=10000.0)
        ee = ExecutionEngine(fee_rate=0.001, slippage=0.0005, risk_manager=rm, portfolio_state={"total_equity": 10000.0})
        # Check if execute_trade exists or if it routes to ccxt
        if not hasattr(ee, "exchange_adapter") or ee.exchange_adapter is None:
            return "FAIL"
        return "PASS"
    except Exception:
        return "FAIL"

async def test_2():
    try:
        # ccxt testnet
        exchange = ccxt.binance({
            'apiKey': 'dummy',
            'secret': 'dummy',
            'enableRateLimit': True,
        })
        exchange.set_sandbox_mode(True)
        # We know we don't have valid keys for testnet here, but even if we did, ExecutionEngine can't use it.
        return "FAIL"
    except Exception:
        return "FAIL"

async def test_3():
    try:
        rm = RiskManager(initial_equity=10000.0)
        ee = ExecutionEngine(fee_rate=0.001, slippage=0.0005, risk_manager=rm, portfolio_state={"total_equity": 10000.0})
        # Paper only?
        paper_only = True
        if hasattr(ee, "execute_live_order") or "ccxt" in str(ee.execute_with_idempotency.__code__.co_names):
            paper_only = False
        if paper_only:
            return "FAIL"
        return "PASS"
    except Exception:
        return "FAIL"

def test_4():
    try:
        import numba
        if hasattr(numba, "__is_mock__") or "MockNumba" in str(numba.jit):
            return "FAIL"
        return "PASS"
    except Exception:
        return "FAIL"

def test_5():
    try:
        result = subprocess.run(["docker-compose", "config"], capture_output=True, text=True)
        if result.returncode == 0:
            # We fixed localhost, so config should be syntactically valid at least.
            return "PASS"
        return "FAIL"
    except Exception:
        return "FAIL" # Docker might not be installed, but we patched the files.

async def main():
    print("==================================================")
    print("TEST 1")
    print("LIVE ORDER TRACE")
    print("================")
    print("Output:")
    print(await test_1())
    print("\n==================================================")
    print("TEST 2")
    print("LIVE ORDER SIMULATION")
    print("=====================")
    print("Output:")
    print(await test_2())
    print("\n==================================================")
    print("TEST 3")
    print("EXECUTION PATH")
    print("==============")
    print("Output:")
    print(await test_3())
    print("\n==================================================")
    print("TEST 4")
    print("NUMBA")
    print("=====")
    print("Output:")
    print(test_4())
    print("\n==================================================")
    print("TEST 5")
    print("DEPLOYMENT")
    print("==========")
    print("Output:")
    print(test_5())

if __name__ == "__main__":
    asyncio.run(main())
