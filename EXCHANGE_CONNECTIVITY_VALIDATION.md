# EXCHANGE CONNECTIVITY VALIDATION
## Sprint 1F — Phase 1 Certification
**Aerora Quant Platform**
**Validation Date:** 2026-07-18T14:39:01Z → 14:40:10Z UTC
**Validator:** Sprint 1F Automated Sandbox Validation Suite
**Python:** Portable 3.12.3 | **CCXT:** 4.4.89

---

## Objective

Verify that the Aerora Quant platform can establish authenticated and public connections to all three target exchange sandboxes using CCXT, and that network paths, latency, and error handling are production-ready.

---

## Exchange Configuration

| Parameter | Binance Testnet | Bybit Testnet | OKX Demo |
|---|---|---|---|
| Sandbox URL | `https://testnet.binance.vision` | `https://api-testnet.bybit.com` | `https://www.okx.com` |
| WebSocket URL | `wss://testnet.binance.vision/ws` | `wss://stream-testnet.bybit.com/v5/public/spot` | `wss://ws.okx.com:8443/ws/v5/public` |
| CCXT ID | `binance` | `bybit` | `okx` |
| Sandbox Mode | `set_sandbox_mode(True)` | `set_sandbox_mode(True)` | `x-simulated-trading: 1` |
| Default Symbol | `BTC/USDT` | `BTC/USDT` | `BTC-USDT` |

---

## Credential Status

| Exchange | API Key Loaded | Key Status | Auth Test | Blocker |
|---|---|---|---|---|
| Binance | ✅ Yes | ❌ DUMMY | ❌ FAIL | Supabase vault contains `dummy_api_key` |
| Bybit | ✅ Yes | ❌ DUMMY | ❌ FAIL | Supabase vault contains `dummy_api_key` |
| OKX | ✅ Yes | ❌ DUMMY | ❌ FAIL | Supabase vault contains `dummy_api_key` |

> **Root Cause:** The `exchange_connections` table in Supabase is pre-populated with placeholder credentials. Real testnet API keys have not been provisioned.

---

## Runtime Connectivity Results

### Binance Testnet

```
Execution window: 2026-07-18T14:39:01Z → 14:39:06Z
```

| Test | Result | Value |
|---|---|---|
| TCP Handshake | ✅ PASS | 266.5 ms |
| TLS Handshake | ✅ PASS | 159.2 ms |
| WebSocket Host Reachable | ✅ PASS | 32.7 ms |
| Markets Loaded | ✅ PASS | 2,149 symbols in 1,719.4 ms |
| BTC/USDT Ticker | ✅ PASS | last=64,161.54 bid=64,161.54 ask=64,161.55 (243.6 ms) |
| Auth Test | ❌ FAIL | `{"code":-2014,"msg":"API-key format invalid."}` |
| Balance Retrieval | ❌ BLOCKED | Requires valid API key |
| Position Retrieval | ❌ BLOCKED | Requires valid API key |

**Classification: `PARTIAL`**

---

### Bybit Testnet

```
Execution window: 2026-07-18T14:39:06Z → 14:39:11Z
```

| Test | Result | Value |
|---|---|---|
| TCP Handshake | ✅ PASS | 168.4 ms |
| TLS Handshake | ✅ PASS | 94.3 ms |
| WebSocket Host Reachable | ✅ PASS | 49.6 ms |
| Markets Loaded | ✅ PASS | 3,471 symbols in 2,454.2 ms |
| BTC/USDT Ticker | ✅ PASS | last=64,028.40 bid=64,028.20 ask=64,028.40 (200.8 ms) |
| Auth Test | ❌ FAIL | `{"retCode":10003,"retMsg":"API key is invalid."}` |
| Balance Retrieval | ❌ BLOCKED | Requires valid API key |
| Position Retrieval | ❌ BLOCKED | Requires valid API key |

**Classification: `PARTIAL`**

---

### OKX Demo

```
Execution window: 2026-07-18T14:39:11Z → 14:40:10Z
```

| Test | Result | Value |
|---|---|---|
| TCP Handshake | ❌ FAIL | Connection timeout (www.okx.com blocked from this network) |
| TLS Handshake | ❌ FAIL | Timeout |
| WebSocket Host Reachable | ✅ PASS | ws.okx.com:443 → 33.2 ms |
| Markets Loaded | ❌ FAIL | `GET /api/v5/public/instruments?instType=SWAP` timeout |
| Ticker | ❌ FAIL | Blocked |
| Auth Test | ❌ FAIL | Network unreachable |

> **Root Cause:** OKX REST API host (`www.okx.com`) is blocked/inaccessible from the current network environment. OKX WebSocket host (`ws.okx.com`) IS reachable. This is a network egress restriction, not a code defect.

**Classification: `FAILED`** *(Network blocked — not a code defect)*

---

## Summary Matrix

| Exchange | Network | Public API | WS Reachable | Auth | Markets | Classification |
|---|---|---|---|---|---|---|
| Binance Testnet | ✅ | ✅ | ✅ | ❌ Dummy key | 2,149 | **PARTIAL** |
| Bybit Testnet | ✅ | ✅ | ✅ | ❌ Dummy key | 3,471 | **PARTIAL** |
| OKX Demo | ❌ Blocked | ❌ | ✅ (WS only) | ❌ | 0 | **FAILED** |

---

## Reconnect Behavior (Code Verification)

`ConnectionEngine` (file: `backend_app/backend/connection_engine.py`) verified to contain:

| Feature | Status |
|---|---|
| `max_retries` parameter | ✅ Present |
| Exponential backoff (`2**attempt`) | ✅ Present |
| Disconnect cleanup (`self.exchange = None`) | ✅ Present |
| `set_sandbox_mode(True)` invocation | ✅ Present |
| `_exchange_pool` connection pooling | ✅ Present |

---

## Errors Documented

| Exchange | Error Code | Error Message | Category |
|---|---|---|---|
| Binance | -2014 | API-key format invalid | INVALID_API_KEY |
| Bybit | 10003 | API key is invalid | INVALID_API_KEY |
| OKX | N/A | Connection timeout to www.okx.com | NETWORK_BLOCKED |

---

## Certification

| Requirement | Status |
|---|---|
| Public market data reachable (Binance, Bybit) | ✅ PASS |
| WebSocket hosts reachable (all 3) | ✅ PASS (partial OKX) |
| Authenticated connection | ❌ BLOCKED — dummy API keys |
| Balance retrieval | ❌ BLOCKED |
| Position retrieval | ❌ BLOCKED |

**Phase 1 Verdict: `PARTIAL`**
**Blocker:** Real testnet API keys must be provisioned before authenticated validation can proceed.
