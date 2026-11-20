"""Phase 11B-C Stage 2 — are incoming trade quantities CUMULATIVE or INCREMENTAL?

The brief requires this be ESTABLISHED, not assumed, and permits no fix if it cannot be.
Establishing it decides whether `_handle_fill_event` is booking the wrong number.

Evidence sources, all local — no venue contact:

  1. ccxt's own unified type definitions (`ccxt.base.types.Order` / `Trade`).
  2. ccxt's `parse_order` / `safe_order` implementation, which COMPUTES `filled` from the
     trade list — the direction of that computation is the proof.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import re

import ccxt
# Explicit submodule imports: `ccxt.base` is shadowed by an attribute on the package, so
# `ccxt.base.exchange` resolves to the wrong object.
from ccxt.base.exchange import Exchange as CcxtExchange

ROOT = pathlib.Path(__file__).resolve().parents[1]
out: dict = {"ccxt_version": ccxt.__version__}

print("=" * 88)
print("CCXT FILL EVENT SEMANTICS — CUMULATIVE vs INCREMENTAL")
print("=" * 88)
print(f"ccxt {ccxt.__version__}\n")

# ── 1. Unified type definitions ─────────────────────────────────────────────────────
print("-" * 88)
print("1. UNIFIED TYPE DEFINITIONS (ccxt.base.types)")
print("-" * 88)
try:
    from ccxt.base import types as T

    for name in ("Order", "Trade"):
        cls = getattr(T, name, None)
        if cls is None:
            print(f"  {name}: not present")
            continue
        ann = getattr(cls, "__annotations__", {})
        keys = sorted(ann)
        print(f"  {name} fields: {keys}")
        out[f"{name}_fields"] = keys
except Exception as exc:  # noqa: BLE001
    print(f"  could not import types: {exc}")

# ── 2. safe_order — how ccxt DERIVES `filled` ───────────────────────────────────────
print()
print("-" * 88)
print("2. HOW ccxt DERIVES order['filled'] FROM THE TRADE LIST")
print("-" * 88)
print("  This is the decisive evidence: if `filled` is computed by SUMMING trade amounts,")
print("  then trade['amount'] is per-trade (INCREMENTAL) and order['filled'] is the")
print("  running total (CUMULATIVE). The two are not interchangeable.\n")

src = inspect.getsource(CcxtExchange.safe_order)
# The summing loop and the assignment back onto `filled`.
interesting = [
    ln.rstrip() for ln in src.splitlines()
    if re.search(r"filled|amount|cost|trades\[|totalCost|tradeAmount|sum", ln)
]
for ln in interesting[:46]:
    print("   " + ln.strip()[:100])
out["safe_order_excerpt_lines"] = len(interesting)

# ── 3. Verdict, demonstrated ────────────────────────────────────────────────────────
print()
print("-" * 88)
print("3. DEMONSTRATION — parse a synthetic order with three partial trades")
print("-" * 88)

ex = ccxt.binance()
raw = {
    "id": "ORDER-1",
    "symbol": "BTC/USDT",
    "side": "buy",
    "status": "open",
    "type": "limit",
    "amount": 3.0,
    # `filled` deliberately absent, so safe_order takes its parseFilled branch and
    # DERIVES it from the trade list — which is the behaviour under examination.
    "filled": None,
    "remaining": None,
    "cost": None,
    "price": None,
    "average": None,
    "timestamp": 1,
    "lastTradeTimestamp": 3,
    "fee": None,
    "fees": [],
    "trades": [
        {"id": "T1", "order": "ORDER-1", "amount": 1.0, "price": 100.0, "cost": 100.0,
         "symbol": "BTC/USDT", "side": "buy", "timestamp": 1, "fee": None, "info": {}, "type": "limit", "takerOrMaker": "taker"},
        {"id": "T2", "order": "ORDER-1", "amount": 1.0, "price": 200.0, "cost": 200.0,
         "symbol": "BTC/USDT", "side": "buy", "timestamp": 2, "fee": None, "info": {}, "type": "limit", "takerOrMaker": "taker"},
        {"id": "T3", "order": "ORDER-1", "amount": 0.5, "price": 300.0, "cost": 150.0,
         "symbol": "BTC/USDT", "side": "buy", "timestamp": 3, "fee": None, "info": {}, "type": "limit", "takerOrMaker": "taker"},
    ],
}
parsed = ex.safe_order(dict(raw))

trade_amounts = [t["amount"] for t in raw["trades"]]
print(f"  individual trade amounts      : {trade_amounts}  (sum = {sum(trade_amounts)})")
print(f"  parsed order['filled']        : {parsed.get('filled')}")
print(f"  parsed order['average']       : {parsed.get('average')}")
print(f"  parsed order['remaining']     : {parsed.get('remaining')}")

cumulative_confirmed = parsed.get("filled") == sum(trade_amounts)
out["trade_amounts"] = trade_amounts
out["parsed_filled"] = parsed.get("filled")
out["filled_equals_sum_of_trades"] = cumulative_confirmed

print()
if cumulative_confirmed:
    print("  ESTABLISHED:")
    print("    order['filled']    = SUM of trade amounts  -> CUMULATIVE")
    print("    trade['amount']    = that trade only       -> INCREMENTAL")
else:
    print("  NOT ESTABLISHED by this demonstration — do NOT modify behaviour.")

# ── 4. The two shapes the listener handles ──────────────────────────────────────────
print()
print("-" * 88)
print("4. THE TWO EVENT SHAPES `_handle_fill_event` HANDLES")
print("-" * 88)
print("  SHAPE A  raw_order_data['trades'] is populated")
print("           -> each entry's 'amount' is INCREMENTAL and carries its own unique 'id'")
print("           -> booking each as one fill is CORRECT")
print()
print("  SHAPE B  'trades' empty but 'lastTradeTimestamp' set; the listener synthesises")
print("           one trade as:")
print("               id     = raw_order_data['id']      <- the ORDER id, not a trade id")
print("               amount = raw_order_data['filled']  <- CUMULATIVE, not incremental")
print("           -> booking that as one fill mixes the two conventions:")
print("              * the quantity is the running total, so a second such event re-books")
print("                everything already booked;")
print("              * the id is the same every time, so dedup on trade id cannot")
print("                distinguish a NEW partial fill from a REPLAY of the previous one.")
print()
print("  CONCLUSION: SHAPE B cannot be converted into an incremental fill from the event")
print("  alone. The information needed (what has already been booked) is not in it.")

dest = ROOT / "staging" / "p11bc_fill_semantics.json"
dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(f"\nartifact -> {dest.relative_to(ROOT)}")
