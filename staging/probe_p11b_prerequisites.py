"""Phase 11B Stage 1-2 — sandbox execution prerequisites. READ-ONLY. No venue contact.

Answers, for binance / bybit / okx, whether the things a sandbox certification run needs
actually exist here. Every check is local: env vars, local service reachability, and
ccxt's own tables. **No exchange endpoint is contacted** — the only network sockets opened
are to localhost (Redis) and the local PostgreSQL DSN already recorded in
`staging/pg_dsn.txt` by earlier phases.

The OKX market-load defect is reproduced OFFLINE by feeding `parse_market` the shape of
instrument that breaks it, rather than by asking OKX for one. That makes it deterministic
and keeps this probe free of venue traffic.

Nothing is installed, obtained, or created. A missing prerequisite is reported, not fixed.
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VENUES = ["binance", "bybit", "okx"]
report: dict = {}


def section(title: str) -> None:
    print()
    print("=" * 86)
    print(title)
    print("=" * 86)


# ── 1. CREDENTIALS ──────────────────────────────────────────────────────────────────
section("1. SANDBOX CREDENTIALS (names and presence only — no value is read or printed)")

cred: dict = {}
for v in VENUES:
    up = v.upper()
    names = [f"AERORA_SANDBOX_{up}_API_KEY", f"AERORA_SANDBOX_{up}_SECRET",
             f"AERORA_SANDBOX_{up}_PASSPHRASE"]
    present = {n: bool(os.environ.get(n)) for n in names}
    # OKX requires a passphrase; the other two do not.
    required = names[:3] if v == "okx" else names[:2]
    cred[v] = {
        "expected_names": names,
        "present": present,
        "required_present": all(present[n] for n in required),
    }
    status = "AVAILABLE" if cred[v]["required_present"] else "MISSING"
    print(f"  {v:<9} {status}")
    for n in names:
        opt = "" if n in required else "  (optional for this venue)"
        print(f"      {'SET  ' if present[n] else 'unset'} {n}{opt}")

keys_file = ROOT / "exchange_keys.json"
vault_entries = None
if keys_file.exists():
    body = "\n".join(
        ln for ln in keys_file.read_text(encoding="utf-8", errors="replace").splitlines()
        if not ln.lstrip().startswith("#")
    ).strip()
    try:
        parsed = json.loads(body) if body else {}
        vault_entries = len(parsed) if isinstance(parsed, (dict, list)) else "?"
    except Exception:
        vault_entries = "unparseable"
print(f"\n  exchange_keys.json        : "
      f"{'present, ' + str(vault_entries) + ' entries' if keys_file.exists() else 'absent'}")
print(f"  MASTER_ENCRYPTION_KEYS    : {'SET' if os.environ.get('MASTER_ENCRYPTION_KEYS') else 'unset'}"
      f"   (APIKeyVault refuses to construct without it)")
report["credentials"] = {
    "per_venue": cred,
    "exchange_keys_json_entries": vault_entries,
    "master_encryption_keys_set": bool(os.environ.get("MASTER_ENCRYPTION_KEYS")),
    "any_sandbox_credential": any(c["required_present"] for c in cred.values()),
}

# ── 2. REDIS (kill switch) ──────────────────────────────────────────────────────────
section("2. REDIS — required because the kill switch FAILS CLOSED when unreadable")

redis_url = os.environ.get("REDIS_URL") or ""
print(f"  REDIS_URL env             : {redis_url or '(unset)'}")


def port_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


redis_up = port_open("127.0.0.1", 6379)
print(f"  127.0.0.1:6379 reachable  : {redis_up}")
if not redis_up:
    print("  CONSEQUENCE: `global_safety.is_active()` engages its latch and returns True")
    print("  on RedisUnavailable (KILL-01 fail-closed), so `enforce_kill_switch()` at the")
    print("  venue boundary REFUSES every order. No sandbox order can be placed.")
report["redis"] = {"env_url": redis_url, "localhost_6379_reachable": redis_up}

# ── 3. TEST DATABASE ────────────────────────────────────────────────────────────────
section("3. TEST DATABASE — execution_records / positions are written on the order path")

test_dsn = os.environ.get("AERORA_TEST_DATABASE_URL") or ""
db_url = os.environ.get("DATABASE_URL") or ""
dsn_file = ROOT / "staging" / "pg_dsn.txt"
recorded = dsn_file.read_text(encoding="utf-8").strip() if dsn_file.exists() else ""
print(f"  AERORA_TEST_DATABASE_URL  : {'SET' if test_dsn else '(unset)'}")
print(f"  DATABASE_URL              : {'SET' if db_url else '(unset)'}")
print(f"  staging/pg_dsn.txt        : {'present' if recorded else 'absent'}")

pg_up = False
if recorded:
    # Host/port only — no connection is attempted with credentials, and the DSN is not
    # printed because it may contain one.
    try:
        tail = recorded.split("@", 1)[1] if "@" in recorded else recorded
        hostport = tail.split("/", 1)[0]
        host, _, port = hostport.partition(":")
        pg_up = port_open(host or "127.0.0.1", int(port or 5432))
        print(f"  recorded host reachable   : {pg_up}")
    except Exception as exc:  # noqa: BLE001
        print(f"  recorded DSN unparseable  : {type(exc).__name__}")
report["database"] = {
    "aerora_test_database_url_set": bool(test_dsn),
    "database_url_set": bool(db_url),
    "pg_dsn_file_present": bool(recorded),
    "recorded_host_reachable": pg_up,
}

# ── 4. SANDBOX ENDPOINT CONFIGURATION ──────────────────────────────────────────────
section("4. SANDBOX ENDPOINT CONFIGURATION (ccxt tables only — no venue contact)")

import ccxt  # noqa: E402

from backend_app.backend.exchange_executor import (SANDBOX_MECHANISMS,  # noqa: E402
                                                   ExchangeExecutorFactory,
                                                   SandboxMechanism,
                                                   verify_sandbox_endpoint)

print(f"  ccxt installed            : {ccxt.__version__}")
print(f"  allowlist                 : {ExchangeExecutorFactory.SUPPORTED_EXCHANGES}")
endpoints: dict = {}
for v in VENUES:
    declared = SANDBOX_MECHANISMS.get(v)
    mech = declared[0].value if declared else "UNDECLARED"
    # Arm sandbox exactly as CCXTExchangeExecutor.connect does, then run the real verifier.
    try:
        client = getattr(ccxt, v)({"sandbox": True, "options": {"sandbox": True}})
        try:
            res = verify_sandbox_endpoint(client, v)
            ok, detail = res.verified, res.detail
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"construction raised {type(exc).__name__}: {exc}"
    endpoints[v] = {"declared_mechanism": mech, "verifier_passes": ok, "detail": detail}
    print(f"  {v:<9} mechanism={mech:<12} verifier_passes={ok}")
    print(f"      {detail[:92]}")
report["endpoints"] = endpoints

# ── 5. OKX MARKET LOADING — offline reproduction ────────────────────────────────────
section("5. OKX MARKET LOADING ON THE PINNED CCXT — reproduced OFFLINE")

okx_detail: dict = {"ccxt_version": ccxt.__version__}
try:
    okx = ccxt.okx()
    # The shape `connection_engine.py:196-250` documents: a pre-open SPOT instrument whose
    # baseCcy / quoteCcy / settleCcy / uly are all empty strings. `safe_currency_code('')`
    # yields None, and parse_market then computes `base + '/' + quote`.
    pre_open = {
        "instType": "SPOT", "instId": "CT-USDT", "baseCcy": "", "quoteCcy": "",
        "settleCcy": "", "ctValCcy": "", "uly": "", "state": "preopen",
        "tickSz": "0.001", "lotSz": "1", "minSz": "1",
    }
    try:
        okx.parse_market(pre_open)
        okx_detail["parse_market_raises"] = False
        okx_detail["note"] = "the documented pre-open shape parsed without error"
        print("  parse_market on the pre-open SPOT shape : NO ERROR")
        print("  -> the defect does NOT reproduce on this input; treat the OKX blocker as")
        print("     unconfirmed by this probe and re-measure against the live venue.")
    except Exception as exc:  # noqa: BLE001
        okx_detail["parse_market_raises"] = True
        okx_detail["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  parse_market on the pre-open SPOT shape : RAISES")
        print(f"      {type(exc).__name__}: {exc}")
        print("  -> CONFIRMED STILL BROKEN on the pinned ccxt. `load_markets()` iterates")
        print("     every instrument, so one such row fails the whole load, and")
        print("     ExchangeNormalizer then has no precision or minimum data for OKX.")
except Exception as exc:  # noqa: BLE001
    okx_detail["probe_error"] = f"{type(exc).__name__}: {exc}"
    print(f"  probe could not run: {exc}")

# Corroborate against the Phase 10F measured connectivity artifact, if present.
art = ROOT / "staging" / "exchange_connectivity_results.json"
if art.exists():
    try:
        data = json.loads(art.read_text(encoding="utf-8"))
        ev = ((data.get("per_exchange") or {}).get("okx") or {}).get("evidence") or {}
        conn = ev.get("PUBLIC_CONNECTIVITY_VERIFIED") or {}
        okx_detail["phase10f_connectivity"] = {
            "attained": conn.get("attained"),
            "failure_class": conn.get("failure_class"),
            "detail": (conn.get("detail") or "")[:160],
        }
        print(f"\n  Phase 10F live measurement (historical, from the artifact):")
        print(f"      attained={conn.get('attained')} class={conn.get('failure_class')}")
        print(f"      {(conn.get('detail') or '')[:120]}")
    except Exception:  # noqa: BLE001
        pass
report["okx_market_loading"] = okx_detail

# ── 6. SCENARIO SUITE DISCOVERABILITY ───────────────────────────────────────────────
section("6. SANDBOX SCENARIO SUITE")

suite = ROOT / "tests" / "exchange_sandbox"
files = sorted(p.name for p in suite.glob("*.py")) if suite.exists() else []
print(f"  tests/exchange_sandbox/   : {'present' if suite.exists() else 'ABSENT'}")
for f in files:
    print(f"      {f}")
print(f"  collection gate           : AERORA_EXCHANGE_SANDBOX_CERT "
      f"({'SET' if os.environ.get('AERORA_EXCHANGE_SANDBOX_CERT') else 'unset'})")
print(f"  venue selector            : AERORA_SANDBOX_VENUE "
      f"({os.environ.get('AERORA_SANDBOX_VENUE') or 'unset -> defaults to binance'})")
report["suite"] = {
    "present": suite.exists(),
    "files": files,
    "gate_set": bool(os.environ.get("AERORA_EXCHANGE_SANDBOX_CERT")),
}

# ── 7. VERDICT ──────────────────────────────────────────────────────────────────────
section("7. PREREQUISITE VERDICT PER VENUE")

blockers_common = []
if not report["redis"]["localhost_6379_reachable"]:
    blockers_common.append("no reachable Redis -> kill switch fails closed, refusing all orders")
if not report["database"]["recorded_host_reachable"]:
    blockers_common.append("no reachable test database -> execution records cannot be written")

final: dict = {}
for v in VENUES:
    bl = list(blockers_common)
    if not cred[v]["required_present"]:
        bl.insert(0, "no sandbox credential provided")
    if not endpoints[v]["verifier_passes"]:
        bl.append("sandbox endpoint verification does not pass")
    if v == "okx" and okx_detail.get("parse_market_raises"):
        bl.append("load_markets broken on the pinned ccxt (precision/minimums unavailable)")
    final[v] = {
        "classification": "BLOCKED" if bl else "READY TO ATTEMPT",
        "blockers": bl,
        "offline_mechanism_verified": endpoints[v]["verifier_passes"],
    }
    print(f"  {v:<9} {final[v]['classification']}")
    for b in bl:
        print(f"      BLOCKER: {b}")

report["verdict"] = final
out = ROOT / "staging" / "p11b_prerequisites.json"
out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
print(f"\nartifact -> {out.relative_to(ROOT)}")
print("\nNo venue was contacted. No credential was created. Nothing was installed.")
