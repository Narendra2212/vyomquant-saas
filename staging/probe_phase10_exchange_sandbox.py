"""Phase 10 / Workstream E — Stage 1. Exchange sandbox availability probe. READ-ONLY, OFFLINE.

WHAT THIS MEASURES, AND WHY IT NEEDS NO NETWORK AND NO CREDENTIAL
----------------------------------------------------------------
Whether a venue can be sandbox-certified at all is decided by three facts, and all three
are answerable from the installed ccxt's own tables without sending a single packet:

  1. Does the venue have a sandbox AT ALL, as far as this pinned ccxt is concerned?
     Either `urls['test']` exists, or the venue ships a `set_sandbox_mode` override that
     switches by some other means (OKX uses a header; Bitget uses an options flag).

  2. Does `ConnectionEngine`'s mechanism route to it?
     `connection_engine.py:156-164` calls `exchange.set_sandbox_mode(True)` inside a
     `try/except` that only WARNS on failure. That call is pure local bookkeeping, so the
     before/after comparison below measures the real market-data code path exactly.

  3. Does `CCXTExchangeExecutor`'s mechanism route to it?
     `exchange_executor.py:864-868` never calls `set_sandbox_mode`. It passes
     `config["sandbox"] = True` AND `config["options"] = {"sandbox": True}` to the
     CONSTRUCTOR. ccxt 4.3.92's `Exchange.__init__` contains:

         is_sandbox = self.safe_bool_2(self.options, 'sandbox', 'testnet', False)
         if is_sandbox:
             self.set_sandbox_mode(is_sandbox)

     Note `self.options` — so the TOP-LEVEL `config["sandbox"]` key is NOT what arms it;
     the `options` dict is. The two lines therefore do different things, and only one of
     them matters. This probe separates them so the report can state which.

Question 3 is the one that decides money, because the ORDER path is the one that spends it.
Each mechanism is measured on its own so a venue can be classified as: routes by URL,
routes by header, routes by nothing (silently production), or refuses to construct.

NO ORDER IS PLACED. NO CREDENTIAL IS USED. NO REQUEST IS SENT. Every ccxt client below is
constructed with empty credentials and interrogated only for its URL table, header table
and `has` capability map, then closed. The async clients are driven through an event loop
solely so they can be `close()`d, which is what ccxt requires to release the aiohttp
session its constructor allocates.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ccxt as ccxt_sync  # noqa: E402
import ccxt.async_support as ccxt_async  # noqa: E402

# The order-execution allowlist, read from the source of truth rather than retyped.
from backend_app.backend.exchange_executor import ExchangeExecutorFactory  # noqa: E402

SUPPORTED = list(ExchangeExecutorFactory.SUPPORTED_EXCHANGES)

# Every venue named in a backend registry but NOT on the order allowlist, so the facade
# surface is measured rather than assumed.
NAMED_ELSEWHERE = ["gateio", "mexc", "htx", "bitfinex"]

# The ccxt unified capabilities an order-lifecycle certification actually depends on.
CAPABILITIES = [
    "createOrder", "cancelOrder", "fetchOrder", "fetchOpenOrders", "fetchClosedOrders",
    "fetchMyTrades", "fetchBalance", "fetchTradingFees", "createStopOrder",
    "createStopLimitOrder", "createPostOnlyOrder", "createReduceOnlyOrder",
    "fetchOHLCV", "fetchTicker", "watchOrders",
]


def _api(exchange) -> str:
    """A comparable rendering of the REST base URL (which may be a nested dict)."""
    return json.dumps((exchange.urls or {}).get("api"), sort_keys=True, default=str)


def _probe_static(exchange_id: str) -> dict:
    """URL tables, capability map and the two sandbox mechanisms. Sync ccxt, no network."""
    row: dict = {"exchange": exchange_id}

    cls = getattr(ccxt_sync, exchange_id, None)
    if cls is None:
        row["in_ccxt"] = False
        return row
    row["in_ccxt"] = True

    plain = cls({})
    live_url = _api(plain)
    live_headers = dict(plain.headers or {})
    test_entry = (plain.urls or {}).get("test")

    row["declares_urls_test"] = test_entry is not None
    row["live_api"] = live_url
    row["test_api"] = json.dumps(test_entry, sort_keys=True, default=str) if test_entry else None
    row["test_api_differs_from_live"] = bool(test_entry) and row["test_api"] != live_url
    row["has_own_set_sandbox_mode"] = "set_sandbox_mode" in cls.__dict__
    row["has"] = {cap: plain.has.get(cap) for cap in CAPABILITIES}
    row["requires_passphrase"] = bool((plain.requiredCredentials or {}).get("password"))

    # ── MECHANISM 2: what ConnectionEngine does (explicit set_sandbox_mode) ──────────
    m2 = cls({})
    try:
        m2.set_sandbox_mode(True)
        row["m2_set_sandbox_mode"] = "accepted"
        row["m2_url_changed"] = _api(m2) != live_url
        row["m2_headers_added"] = sorted(
            set((m2.headers or {})) - set(live_headers)
        )
        row["m2_sandboxMode_option"] = (m2.options or {}).get("sandboxMode", "<absent>")
    except Exception as exc:  # noqa: BLE001
        row["m2_set_sandbox_mode"] = f"{type(exc).__name__}: {exc}"
        row["m2_url_changed"] = False
        row["m2_headers_added"] = []
        row["m2_sandboxMode_option"] = "<not reached>"

    # ── Is the TOP-LEVEL config key alone enough? (the executor's first line) ────────
    try:
        top_only = cls({"sandbox": True})
        row["top_level_sandbox_key_url_changed"] = _api(top_only) != live_url
        row["top_level_sandbox_key_headers_added"] = sorted(
            set((top_only.headers or {})) - set(live_headers)
        )
        row["top_level_sandbox_key_sandboxMode_option"] = (
            (top_only.options or {}).get("sandboxMode", "<absent>")
        )
        row["top_level_sandbox_key_construct"] = "accepted"
    except Exception as exc:  # noqa: BLE001
        row["top_level_sandbox_key_construct"] = f"{type(exc).__name__}: {exc}"
        row["top_level_sandbox_key_url_changed"] = False

    # ── Is the OPTIONS key enough? (the executor's second line) ─────────────────────
    try:
        opt_only = cls({"options": {"sandbox": True}})
        row["options_sandbox_key_url_changed"] = _api(opt_only) != live_url
        row["options_sandbox_key_headers_added"] = sorted(
            set((opt_only.headers or {})) - set(live_headers)
        )
        row["options_sandbox_key_sandboxMode_option"] = (
            (opt_only.options or {}).get("sandboxMode", "<absent>")
        )
        row["options_sandbox_key_construct"] = "accepted"
    except Exception as exc:  # noqa: BLE001
        row["options_sandbox_key_construct"] = f"{type(exc).__name__}: {exc}"
        row["options_sandbox_key_url_changed"] = False

    return row


async def _probe_executor_mechanism(exchange_id: str) -> dict:
    """MECHANISM 3 — construct the client EXACTLY as CCXTExchangeExecutor.connect does.

    Mirrors `exchange_executor.py:856-872` line for line: the same config dict, the same
    `getattr(ccxt, exchange_id)` on the ASYNC module, the same single constructor call.
    The only differences are that the credentials are empty strings and that nothing is
    awaited against the venue.

    `connect()` builds the client at

        self._exchange = exchange_class(config)

    which sits BEFORE the `try:` that wraps `fetch_balance()`. So a constructor raise is
    not classified by `_handle_ccxt_error` and propagates raw out of `connect()`. Whether
    it raises is measured here rather than argued.
    """
    row: dict = {"exchange": exchange_id}
    cls = getattr(ccxt_async, exchange_id, None)
    if cls is None:
        row["in_ccxt_async"] = False
        return row
    row["in_ccxt_async"] = True

    live = cls({"apiKey": "", "secret": "", "enableRateLimit": True})
    try:
        live_url = _api(live)
        live_headers = dict(live.headers or {})
        row["live_api"] = live_url

        # Verbatim from exchange_executor.connect's sandbox branch.
        sandbox_config = {
            "apiKey": "",
            "secret": "",
            "enableRateLimit": True,
            "sandbox": True,
            "options": {"sandbox": True},
        }
        sandboxed = None
        try:
            sandboxed = cls(sandbox_config)
        except Exception as exc:  # noqa: BLE001
            row["constructor"] = f"RAISES {type(exc).__name__}: {exc}"
            row["executor_sandbox_api"] = None
            row["routes_away_from_production"] = False
            row["routing_mechanism"] = "none — connect() raises"
            return row

        try:
            row["constructor"] = "accepted"
            row["executor_sandbox_api"] = _api(sandboxed)
            url_changed = row["executor_sandbox_api"] != live_url
            headers_added = sorted(set((sandboxed.headers or {})) - set(live_headers))
            row["url_changed"] = url_changed
            row["headers_added"] = headers_added
            row["sandboxMode_option"] = (sandboxed.options or {}).get(
                "sandboxMode", "<absent>"
            )
            row["routes_away_from_production"] = bool(url_changed or headers_added)
            if url_changed:
                row["routing_mechanism"] = "base URL swapped to urls['test']"
            elif headers_added:
                row["routing_mechanism"] = f"header(s) {headers_added}"
            else:
                row["routing_mechanism"] = (
                    "NONE — options['sandboxMode'] set but no URL and no header change"
                )
        finally:
            await sandboxed.close()
    finally:
        await live.close()
    return row


def _credential_inventory() -> dict:
    """Is there ANY sandbox credential available? Reports presence only, never values."""
    out: dict = {}

    keys_file = ROOT / "exchange_keys.json"
    if keys_file.exists():
        text = keys_file.read_text(encoding="utf-8", errors="replace")
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
        ).strip()
        try:
            parsed = json.loads(body) if body else {}
            out["exchange_keys.json"] = {
                "exists": True,
                "entry_count": len(parsed) if isinstance(parsed, (dict, list)) else "?",
                "key_names": sorted(parsed)[:20] if isinstance(parsed, dict) else [],
            }
        except Exception as exc:  # noqa: BLE001
            out["exchange_keys.json"] = {"exists": True, "parse_error": str(exc)}
    else:
        out["exchange_keys.json"] = {"exists": False}

    # Env var NAMES only. No value is read into this report.
    tokens = ("EXCHANGE", "BINANCE", "BYBIT", "COINBASE", "KRAKEN", "OKX", "KUCOIN",
              "BITGET", "TESTNET", "SANDBOX", "MASTER_ENCRYPTION")
    out["env_var_names_present"] = sorted(
        k for k in os.environ if any(t in k.upper() for t in tokens)
    )
    out["MASTER_ENCRYPTION_KEYS_set"] = bool(os.environ.get("MASTER_ENCRYPTION_KEYS"))
    out["vault_reachable_without_it"] = False
    return out


def main() -> None:
    print("=" * 78)
    print("PHASE 10 / WORKSTREAM E - STAGE 1: EXCHANGE SANDBOX AVAILABILITY")
    print("read-only | offline | no credential | no order | live trading OFF")
    print("=" * 78)
    print(f"ccxt version (pinned) : {ccxt_sync.__version__}")
    print(f"order-path allowlist  : {SUPPORTED}")
    print(f"named elsewhere only  : {NAMED_ELSEWHERE}")
    print()

    print("-" * 78)
    print("A. DOES A SANDBOX EXIST, AND DOES set_sandbox_mode REACH IT?")
    print("   (mechanism 2 - what ConnectionEngine / market data uses)")
    print("-" * 78)
    rows = {}
    for eid in SUPPORTED + NAMED_ELSEWHERE:
        row = _probe_static(eid)
        rows[eid] = row
        tag = "ORDER-PATH" if eid in SUPPORTED else "facade    "
        if not row.get("in_ccxt"):
            print(f"{eid:<9} [{tag}] NOT PRESENT IN CCXT")
            continue
        print(f"{eid:<9} [{tag}] urls['test']={row['declares_urls_test']!s:<5} "
              f"own_override={row['has_own_set_sandbox_mode']!s:<5} "
              f"passphrase={row['requires_passphrase']}")
        print(f"{'':<9}   set_sandbox_mode(True) -> {row['m2_set_sandbox_mode']}")
        print(f"{'':<9}   url_changed={row['m2_url_changed']} "
              f"headers_added={row['m2_headers_added']} "
              f"sandboxMode={row['m2_sandboxMode_option']}")
    print()

    print("-" * 78)
    print("B. WHICH CONFIG KEY ACTUALLY ARMS SANDBOX MODE IN ccxt 4.3.92?")
    print("   Exchange.__init__ reads safe_bool_2(self.OPTIONS, 'sandbox', 'testnet')")
    print("   so the executor's two lines are NOT equivalent. Measured separately:")
    print("-" * 78)
    for eid in SUPPORTED:
        r = rows[eid]
        if not r.get("in_ccxt"):
            continue
        print(f"{eid:<9} config['sandbox']=True        -> construct="
              f"{r['top_level_sandbox_key_construct']:<40} "
              f"url_changed={r['top_level_sandbox_key_url_changed']}")
        print(f"{'':<9} config['options']['sandbox']  -> construct="
              f"{r['options_sandbox_key_construct']:<40} "
              f"url_changed={r['options_sandbox_key_url_changed']}")
    print()

    print("-" * 78)
    print("C. THE ORDER PATH'S OWN MECHANISM, VERBATIM")
    print("   CCXTExchangeExecutor.connect(sandbox=True) - where do ORDERS go?")
    print("-" * 78)

    async def _run_all():
        return [await _probe_executor_mechanism(e) for e in SUPPORTED]

    exec_rows = asyncio.run(_run_all())
    for row in exec_rows:
        eid = row["exchange"]
        if not row.get("in_ccxt_async"):
            print(f"{eid:<9} NOT PRESENT IN ccxt.async_support")
            continue
        if row["constructor"].startswith("RAISES"):
            print(f"{eid:<9} *** connect() RAISES - sandbox unusable ***")
            print(f"{'':<9}   {row['constructor']}")
            print(f"{'':<9}   raised from `exchange_class(config)`, which sits OUTSIDE")
            print(f"{'':<9}   connect()'s try/except, so it is not classified by")
            print(f"{'':<9}   _handle_ccxt_error and propagates raw to the caller.")
            continue
        verdict = ("routes away from production"
                   if row["routes_away_from_production"]
                   else "*** STAYS ON PRODUCTION ***")
        print(f"{eid:<9} {verdict}  via: {row['routing_mechanism']}")
        print(f"{'':<9}   live    : {row['live_api'][:92]}")
        print(f"{'':<9}   sandbox : {str(row['executor_sandbox_api'])[:92]}")
    print()

    print("-" * 78)
    print("D. CREDENTIAL INVENTORY (names and presence only - no values)")
    print("-" * 78)
    inv = _credential_inventory()
    print(json.dumps(inv, indent=2, default=str))
    print()

    print("-" * 78)
    print("E. SUMMARY - CERTIFIABILITY OF THE ORDER PATH")
    print("-" * 78)
    certifiable, raises, silent_prod = [], [], []
    for r in exec_rows:
        if not r.get("in_ccxt_async"):
            continue
        if r["constructor"].startswith("RAISES"):
            raises.append(r["exchange"])
        elif r["routes_away_from_production"]:
            certifiable.append(r["exchange"])
        else:
            silent_prod.append(r["exchange"])

    print(f"sandbox reachable on the ORDER path      : {certifiable or 'NONE'}")
    print(f"connect(sandbox=True) RAISES NotSupported : {raises or 'none'}")
    print(f"flag accepted but STILL PRODUCTION        : {silent_prod or 'none'}")
    print(f"credentials available for a real run      : "
          f"{inv['env_var_names_present'] or 'NONE'}")
    print()
    print("CONCLUSION: a sandbox end-to-end order lifecycle is executable only for the")
    print("venues in the first list, AND ONLY once a non-production credential exists.")
    print(f"Credentials present now: "
          f"{'NONE - no sandbox run is possible in this environment' if not inv['env_var_names_present'] else 'see list above'}")

    out_path = ROOT / "staging" / "phase10_exchange_sandbox_matrix.json"
    out_path.write_text(
        json.dumps(
            {
                "ccxt_version": ccxt_sync.__version__,
                "order_path_allowlist": SUPPORTED,
                "named_elsewhere_only": NAMED_ELSEWHERE,
                "per_venue_static": rows,
                "executor_sandbox_mechanism": exec_rows,
                "credential_inventory": inv,
                "summary": {
                    "order_path_sandbox_reachable": certifiable,
                    "connect_raises_notsupported": raises,
                    "flag_accepted_but_still_production": silent_prod,
                    "credentials_available": bool(inv["env_var_names_present"]),
                },
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nmachine-readable matrix -> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
