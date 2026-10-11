# PHASE 11B — SANDBOX EXECUTION PREREQUISITES

**Scope:** Binance, Bybit, OKX on the pinned CCXT.
**Date:** 2026-10-10
**Live trading:** OFF. No venue contacted. No credential used or invented. No order. No
install. No migration. Nothing committed.
**Probe:** `staging/probe_p11b_prerequisites.py` → [`p11b_prereq.txt`](p11b_prereq.txt) ·
[`p11b_prerequisites.json`](p11b_prerequisites.json)

---

## 1. Verdict

```
binance   BLOCKED   — no sandbox credential; no reachable Redis
bybit     BLOCKED   — no sandbox credential; no reachable Redis
okx       BLOCKED   — no sandbox credential; no reachable Redis;
                      load_markets broken on the pinned CCXT
```

No sandbox scenario was executed, and none could be. **No scenario execution is claimed.**

---

## 2. Baseline

| | |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`) |
| Staged / unstaged / total paths | **22 / 98 / 250** |
| CCXT pinned (`requirements-base.txt:95`) | `ccxt[async]==4.3.92` |
| CCXT installed | **4.3.92** — matches the pin |
| Reset / stash / clean / revert / commit | **none** |

### 2.1 Concurrent change found during this workstream

`backend_app/core/exchange_certification.py` and `backend_app/routers/support.py` were
**unmodified** at the Workstream J baseline and are now both ` M`. Their owners fixed
**EXCH-04** and **EXCH-05** while this workstream was running — see §6. Not touched here.

---

## 3. Prerequisite detail

### 3.1 Sandbox credentials — MISSING for all three

Presence checked by name only; no value was read, logged, or written anywhere.

| Venue | `..._API_KEY` | `..._SECRET` | `..._PASSPHRASE` | Required set present |
|---|---|---|---|---|
| binance | unset | unset | unset *(optional)* | **No** |
| bybit | unset | unset | unset *(optional)* | **No** |
| okx | unset | unset | unset *(**required**)* | **No** |

Supporting facts:

- `exchange_keys.json` — present, **0 entries** (explicit placeholder).
- `MASTER_ENCRYPTION_KEYS` — **unset**. `APIKeyVault.__init__` raises
  `EnvironmentError("Missing master encryption keys.")` without it, so the vault route to
  credentials is also unavailable.

**No credential was invented and none was requested from any exchange.** This is the
binding blocker and it is unchanged since Phase 7.

### 3.2 Funded sandbox accounts — UNKNOWN, and unknowable from here

Cannot be determined without a credential. Scenarios 2, 5a and 11 require a non-zero
sandbox balance. Recorded as a prerequisite, not as a pass or a failure.

### 3.3 Sandbox endpoint configuration — VERIFIED OFFLINE for all three

Each client was armed exactly as `CCXTExchangeExecutor.connect()` does and then passed
through the real `verify_sandbox_endpoint`. CCXT tables only; no venue contact.

| Venue | Declared mechanism | Verifier passes | Evidence |
|---|---|---|---|
| binance | `url_swap` | **Yes** | base URL replaced by declared `urls['test']`; production retained in `urls['apiBackup']` |
| bybit | `url_swap` | **Yes** | same |
| okx | `header` | **Yes** | demo selected by `x-simulated-trading`; shared production host, so an unchanged URL is correct here |

This is the one prerequisite that is satisfied. It establishes that **if** a credential
existed, the client would demonstrably address a sandbox endpoint — and would be refused
if it did not.

### 3.4 Redis — NOT REACHABLE, and this is a hard blocker

| | |
|---|---|
| `REDIS_URL` | **unset** |
| `127.0.0.1:6379` reachable | **False** |

This blocks order placement by design, not by accident. `enforce_kill_switch()` is the
last statement before `create_order` (`exchange_executor.py:1506`), and
`global_safety.is_active()` engages its local latch and returns **True** on
`RedisUnavailable` (KILL-01 fail-closed). So with no Redis, **every order is refused** —
correct behaviour, and an absolute precondition for running scenarios 3 through 12.

An isolated test Redis is required. **Not installed here** — no infrastructure was added.

### 3.5 Test database — REACHABLE

| | |
|---|---|
| `AERORA_TEST_DATABASE_URL` | unset |
| `DATABASE_URL` | unset |
| `staging/pg_dsn.txt` | present; **host reachable** |

A local PostgreSQL recorded by earlier phases is reachable. Host/port reachability only
was tested; no connection was made with credentials and the DSN is not reproduced here
because it may contain one.

**Not confirmed:** whether that database carries the schema the order path needs
(`execution_records`, `positions`, `fee_records`) at the required migration head. Schema
verification would need `AERORA_TEST_DATABASE_URL` to be set deliberately — `tests/conftest.py`
blanks `DATABASE_URL` otherwise, by design, so that provisioning a schema cannot silently
re-point the whole suite at a real server. **No migration was applied.**

### 3.6 Documented way to run the scenarios — PRESENT

| | |
|---|---|
| Suite | `tests/exchange_sandbox/` — `conftest.py`, `harness.py`, `test_sandbox_order_lifecycle.py`, `test_sandbox_routing_contract.py` |
| Collection gate | `AERORA_EXCHANGE_SANDBOX_CERT=1` — **unset** by default |
| Venue selector | `AERORA_SANDBOX_VENUE` — unset, defaults to `binance` |
| Operator instructions | `tests/exchange_sandbox/README.md` |

---

## 4. OKX market loading — CONFIRMED STILL BROKEN

Reproduced **offline**, deterministically, with no venue contact: `okx.parse_market` was
handed the pre-open SPOT instrument shape that `connection_engine.py:196-250` documents —
`instType: SPOT`, `instId: CT-USDT`, with `baseCcy` / `quoteCcy` / `settleCcy` / `uly` all
empty strings.

```
parse_market(pre-open SPOT shape) -> RAISES
    TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'
```

`safe_currency_code("")` yields `None`, and `parse_market` then computes
`base + '/' + quote`. `load_markets()` iterates every instrument, so **one such row fails
the entire load** — and `ExchangeNormalizer` consequently has no precision, tick-size or
minimum-notional data for OKX.

Corroborated by the independent Phase 10F live measurement in
`staging/exchange_connectivity_results.json`:

```
okx  PUBLIC_CONNECTIVITY_VERIFIED  attained=False  class=LIBRARY_DEFECT
     TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'
```

**Consequence for certification:** OKX cannot pass scenario 1
(`load_markets` must return the symbol universe) or any scenario that depends on venue
precision and minimums — which is every order-placing scenario, because `place_order`
enforces `BELOW_PRECISION` / `BELOW_MIN_QUANTITY` / `BELOW_MIN_NOTIONAL` from that data.

This is a **dependency defect, not a platform defect.** It was measured as fixed in CCXT
4.5.85 (`staging/ccxt_upgrade_delta.json`: OKX loads 4,501 markets). **CCXT was not
upgraded in this workstream** — out of scope without approval.

---

## 5. Prerequisite summary

| Prerequisite | binance | bybit | okx |
|---|---|---|---|
| Sandbox credential | **MISSING** | **MISSING** | **MISSING** |
| Funded sandbox account | unknown | unknown | unknown |
| Sandbox endpoint config | **OK** | **OK** | **OK** |
| Isolated Redis | **MISSING** | **MISSING** | **MISSING** |
| Reachable test DB | OK (schema unconfirmed) | OK | OK |
| Market data loadable | unverified | unverified | **BROKEN** |
| Documented run path | OK | OK | OK |

---

## 6. Concurrent fixes observed (not made here)

Both cite this workstream's measurement artifacts, which is the reproductions working as
intended.

**EXCH-04 — `exchange_certification.py`.** `coinbase` and `kucoin` changed from
`sandbox=True` to `sandbox=False`, with `known_limitations` naming the measured
`NotSupported` behaviour on ccxt 4.3.92 and citing
`tests/exchange_sandbox/test_sandbox_routing_contract.py` and
`staging/EXCHANGE_SANDBOX_CERTIFICATION.md`.

**EXCH-05 — `routers/support.py`.** The FAQ answer now reads "Binance (Testnet), Bybit
(Testnet), and OKX (Demo Mode)"; Coinbase and KuCoin were removed from the advertised
sandbox list.

Measured effect: `test_certification_registry_sandbox_claims_match_measured_reality` and
`test_customer_facing_faq_sandbox_claims_match_measured_reality` **now pass**. The contract
suite moved from 3 failed / 8 passed to **1 failed / 10 passed** — see
[`PHASE11B_SANDBOX_SCENARIO_MATRIX.md`](PHASE11B_SANDBOX_SCENARIO_MATRIX.md) §5.

Neither file was modified by this workstream.

---

## 7. What is required to unblock, in dependency order

1. **An isolated test Redis.** Cheapest and unblocks the most. Without it every order is
   refused regardless of credentials. Needs infrastructure approval.
2. **Sandbox credentials** for at least binance and bybit, supplied under
   `AERORA_SANDBOX_<VENUE>_*`. Needs authorization; must be testnet/demo only.
3. **Funded sandbox accounts** for those venues.
4. **Schema confirmation** on the test database at the required migration head, with
   `AERORA_TEST_DATABASE_URL` set deliberately.
5. **For OKX only:** the CCXT upgrade (measured as fixing it in 4.5.85), through dependency
   triage with its own regression run. OKX cannot be certified before this.

Items 1–4 unblock binance and bybit. OKX additionally requires item 5.

---

## 8. Classification

| Venue | Highest state attained | Sandbox execution |
|---|---|---|
| **binance** | **OFFLINE MECHANISM VERIFIED** | **BLOCKED** — credential, Redis |
| **bybit** | **OFFLINE MECHANISM VERIFIED** | **BLOCKED** — credential, Redis |
| **okx** | **OFFLINE MECHANISM VERIFIED** | **BLOCKED** — credential, Redis, `load_markets` |

No venue reached SANDBOX CONNECTIVITY VERIFIED, SANDBOX ORDER FLOW VERIFIED or
SANDBOX RECONCILIATION VERIFIED.

```
TRADING LIVE — OFF / NOT CERTIFIED
```

No paper-trading or live-trading GO decision follows from this workstream.
