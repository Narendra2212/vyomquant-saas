# PHASE 10 — EXCH-01 SANDBOX ENDPOINT VERIFICATION

**Objective:** establish sandbox status from verified endpoint configuration rather than
from a flag or a log message.
**Date:** 2026-10-10
**Live trading:** OFF throughout. No order submitted. No credential used. **No network
call to any exchange venue.** Nothing committed, pushed or deployed.

---

## 1. Baseline and ownership boundaries

| | |
|---|---|
| Branch | `main` |
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Changed paths at baseline | **244** |
| Reset / stash / clean / revert | **none** |

### 1.1 Concurrent work in shared connection logic

| Path | Baseline | Owner | Overlap with this task? |
|---|---|---|---|
| `backend_app/backend/exchange_executor.py` | ` M` **+793 / −52** | mixed — earlier phases, plus Workstream G (bitget routing, mine) | **Yes** — `connect()` is here |
| `backend_app/backend/connection_engine.py` | ` M` **+188 / −18** | **Workstream C** (QUOTA-01 / CONN-01) | **No** — see below |
| `backend_app/routers/exchange.py` | `MM` | Workstream C | No |
| `backend_app/core/exchange_capability_registry.py` | `??` | Workstream F/G (mine) | No change needed |

**Workstream C's connection work was checked for conflict before editing.** Filtering
their `connection_engine.py` diff for `sandbox|testnet|set_sandbox|urls[` returns exactly
one added line — a constructor-signature passthrough of the existing `testnet` argument.
Their changes are pool lifecycle, credential-keyed pooling and shutdown drain. **No
overlap with sandbox endpoint resolution.**

`connection_engine.py` was therefore **not modified by this workstream**. It is the
market-data path, Workstream C owns it, and it already calls `set_sandbox_mode()`
explicitly inside a `try/except` that only warns — which is appropriate for market data
and is not the defect. Changing it would have been both an ownership conflict and an
unnecessary risk to public market-data access.

**Verified no one else was editing the sandbox block.** Filtering the executor diff for
`config["sandbox"]|SANDBOX mode|set_sandbox_mode` returned only comment lines from my own
Workstream G. The `connect()` sandbox statements sat at their committed state.

---

## 2. Root cause and affected connection paths

### 2.1 Root cause

`CCXTExchangeExecutor.connect()` established sandbox status by **setting a flag and
logging it**:

```python
if self.sandbox:
    config["sandbox"] = True
    config["options"] = {"sandbox": True}
    logger.info(f"Connecting to {self.exchange_id} in SANDBOX mode")
```

Nothing checked where the resulting client would actually send. `place_order` then logged
`sandbox={self.sandbox}` on every order. `self.sandbox` is the caller's **request**; it was
being reported as **evidence**.

For bitget the log was false: ccxt's bitget `set_sandbox_mode` sets only
`options['sandboxMode']` and bitget publishes no `urls['test']`, so the client remained on
the production API.

### 2.2 The trap in the obvious fix

Checking that `set_sandbox_mode()` did not raise **would not have caught bitget** — it
does not raise. Measured (`staging/p10exch01_endpoints.txt`):

```
bitget   urls['test'] declared : False
         urls['apiBackup'] set : False      <- the swap never happened
         headers added         : []
         options['sandboxMode']: True       <- the only thing that changed
         base URL              : UNCHANGED
```

**Absence of an exception is not evidence of routing.** The verifier therefore requires
positive endpoint evidence.

### 2.3 Affected path

Only one: `CCXTExchangeExecutor.connect()`, the order path. `ConnectionEngine.connect()`
(market data) already calls `set_sandbox_mode()` explicitly and is owned elsewhere;
untouched.

---

## 3. Venue-specific endpoint verification approach

### 3.1 Why no network call is needed

ccxt 4.3.92's `Exchange.set_sandbox_mode` leaves the proof on the client:

```python
if enabled:
    if 'test' in self.urls:
        self.urls['apiBackup'] = self.urls['api']   # where it came FROM
        self.urls['api']       = self.urls['test']  # where it is going
    else:
        raise NotSupported(...)
```

A client that genuinely swapped carries `apiBackup` alongside `api` and `test`. Comparing
those three proves the swap happened **and** that it landed on the venue's own declared
sandbox endpoint — strictly stronger than "the URL changed to something", and decidable
entirely offline, from the client alone, with no reference client.

### 3.2 Measured mechanisms — the venues genuinely differ

Probe: `staging/probe_exch01_sandbox_endpoints.py` →
[`p10exch01_endpoints.txt`](p10exch01_endpoints.txt) ·
[`exch01_sandbox_endpoint_evidence.json`](exch01_sandbox_endpoint_evidence.json)

| Venue | Mechanism | Evidence on the client |
|---|---|---|
| binance | `URL_SWAP` | `apiBackup` set; `api == test`; → `testnet.binancefuture.com` |
| bybit | `URL_SWAP` | `apiBackup` set; `api == test`; → `api-testnet.{hostname}` |
| okx | `HEADER` | URL **unchanged**; header `x-simulated-trading` added |
| coinbase | `UNSUPPORTED` | constructor raises `NotSupported`; no `urls['test']` |
| kraken | `UNSUPPORTED` | constructor raises `NotSupported` |
| kucoin | `UNSUPPORTED` | constructor raises `NotSupported` |
| bitget | **none** | armed, changed nothing — the EXCH-01 condition |

**OKX is why a URL-only rule would be wrong.** Demo trading shares the production host and
is selected by a header, so "URL unchanged" is the *correct* outcome there. A naive URL
comparison would reject a genuinely-sandboxed OKX client.

**A generic "something must have changed" rule would be wrong in the other direction** — it
would accept any future venue that happens to mutate an unrelated attribute.

So the mechanism is **declared per venue** in `SANDBOX_MECHANISMS` and verified against
that declaration. The table is derived from measurement, **not** from ccxt's `has` map and
not from any capability registry — two registries were wrong about exactly this (EXCH-04:
coinbase and kucoin declared `sandbox=True`, and both raise).

### 3.3 Fail-closed behaviour

Every path refuses; none proceeds on incomplete evidence.

| Condition | Outcome |
|---|---|
| Venue absent from `SANDBOX_MECHANISMS` (`UNKNOWN`) | **refuse** — a new venue must be measured and added deliberately |
| Mechanism `UNSUPPORTED` | **refuse before a client is built** |
| `URL_SWAP` but no `apiBackup` | **refuse** — the swap never ran |
| `URL_SWAP` but `api != test` | **refuse** — endpoint identity ambiguous |
| `URL_SWAP` but `api == apiBackup` | **refuse** — landed back on production |
| `HEADER` but header absent | **refuse** |
| `HEADER` present but value not `1`/`true` | **refuse** — `x-simulated-trading: 0` is a live connection wearing a sandbox label |
| Constructor raises during sandbox init | **refuse**, classified as `SandboxVerificationFailed` |
| A fourth mechanism added without a rule | **refuse** — no fall-through to success |

On refusal the client is **closed and its reference dropped** before the raise. An
abandoned client leaks its aiohttp session and keeps a decrypted credential reachable —
the same `Unclosed client session` leak `connection_engine`'s CE-3 note documents.

### 3.4 Preventing misrepresentation

`self.sandbox` (request) and `sandbox_verified` (evidence) are now separate:

```python
@property
def sandbox_verified(self) -> bool:
    v = self._sandbox_verification
    return bool(v and v.verified)
```

`_sandbox_verification` is `None` until `connect()` succeeds, so an executor that has not
connected reports `False`. Nothing derives sandbox certification from `self.sandbox`.

`SandboxVerificationFailed` subclasses `ExchangeError` with
`error_code="SANDBOX_UNVERIFIED"` and `retryable=False` — existing `except ExchangeError`
handling on the connect path treats it as terminal, which it is: retrying does not change
which endpoint a venue publishes.

### 3.5 What was deliberately not changed

- The live-trading authorization gate (TRADE-04) and kill-switch re-read (KILL-03) are
  untouched and still run before `create_order`.
- The bitget routing block is untouched; it runs at construction, so bitget never reaches
  sandbox verification.
- The allowlist was not broadened. `SANDBOX_MECHANISMS` is scoped to the six approved
  venues and a mechanism entry confers no routing permission.
- `config["sandbox"]` was **kept** alongside `config["options"]`. The top-level key is
  inert (ccxt reads the flag from `options`), but deleting the "redundant" options line
  would silently route every sandbox order to production. Both retained, with the
  asymmetry documented at the site.

---

## 4. Implementation and test evidence

### 4.1 Files changed

| File | Change | Attributable lines |
|---|---|---|
| `backend_app/backend/exchange_executor.py` | `SandboxMechanism`, `SANDBOX_MECHANISMS`, `SandboxVerificationFailed`, `SandboxVerification`, `verify_sandbox_endpoint`, `assert_sandbox_supported`, `_close_unverified_client`, `sandbox_verified` / `sandbox_evidence` properties, `connect()` rewiring, `import json` + `Tuple` | **+395 / −3** on top of the pre-existing +793/−52 |
| `tests/test_exch01_sandbox_endpoint_verification.py` | **new** — 28 tests | +~430 |
| `staging/probe_exch01_sandbox_endpoints.py` | **new** — offline evidence probe | +~165 |
| `staging/exch01_sandbox_endpoint_evidence.json`, `staging/p10exch01_*.txt` | **new** — artifacts and logs | — |
| `staging/PHASE10_EXCH01_SANDBOX_ENDPOINT_REVIEW.md` | **new** — this report | — |

Changed-path count 244 → 246. **No other file was modified.**

### 4.2 Test commands and results

```powershell
# Focused
python -m pytest tests/test_exch01_sandbox_endpoint_verification.py `
  -q --no-header --tb=short --timeout=120
```
**28 passed · 0 failed · 0 skipped · 0 collection errors** —
[`p10exch01_focused.txt`](p10exch01_focused.txt)

```powershell
# Directly affected connection and sandbox-gate tests
python -m pytest tests/test_exch01_sandbox_endpoint_verification.py `
  tests/test_exch01_bitget_routing_is_blocked.py tests/test_exchange_safety_fix.py `
  tests/test_conn01_rotation_evicts_pooled_client.py `
  tests/test_conn01_pool_lifecycle_and_shutdown.py `
  tests/test_exchange_executor_cache_is_per_tenant.py `
  tests/test_connection_engine_deterministic_error_regression.py `
  tests/test_trade04_live_order_gate_at_venue_boundary.py `
  tests/test_exchange_capability_registry.py `
  -q --no-header --tb=short --timeout=180
```
**166 passed · 0 failed · 0 skipped · 0 collection errors** · 99.03s —
[`p10exch01_affected.txt`](p10exch01_affected.txt)

Workstream C's connection-lifecycle suites are included and pass, so their concurrent work
is intact.

The broad regression suite was **not** rerun, per instruction. No issue arose that required it.

### 4.3 Coverage against the nine required cases

| # | Required | Test |
|---|---|---|
| 1 | Sandbox resolves to a known sandbox endpoint | `test_url_swap_to_the_declared_sandbox_endpoint_is_verified`, `test_header_mechanism_is_verified_even_though_the_url_is_unchanged` |
| 2 | Switch ineffective, production remains | `test_an_ineffective_switch_that_leaves_production_configured_is_refused`, `test_a_swap_that_lands_back_on_production_is_refused`, `test_a_missing_header_is_refused_for_a_header_venue`, `test_a_header_set_to_a_non_demo_value_is_refused` |
| 3 | Unknown or ambiguous configuration | `test_an_unmeasured_venue_is_refused_rather_than_guessed`, `test_an_endpoint_that_is_neither_production_nor_the_declared_sandbox_is_refused`, `test_a_venue_declaring_no_test_url_is_refused_for_a_url_swap_mechanism`, `test_nested_dict_endpoints_compare_order_independently` |
| 4 | Sandbox initialization exception | `test_a_constructor_raise_during_sandbox_init_is_classified_not_leaked` |
| 5 | Unsupported sandbox implementation | `test_a_venue_with_no_sandbox_is_refused_before_a_client_is_built` (×3), `test_an_unsupported_venue_refuses_even_when_handed_a_plausible_client` |
| 6 | Unverified cannot be marked sandbox-verified | `test_a_fresh_executor_is_not_sandbox_verified`, `test_a_refused_sandbox_connection_leaves_the_executor_unverified_and_closed`, `test_a_verified_sandbox_connection_reports_its_evidence`, `test_live_mode_is_never_reported_as_sandbox_verified` |
| 7 | Live-trading authorization gate preserved | `test_place_order_still_requires_a_live_authorization`, `test_capability_registry_still_authorizes_no_venue_for_trading` |
| 8 | Six approved venues, no allowlist expansion | `test_the_approved_allowlist_is_unchanged`, `test_the_mechanism_table_covers_the_approved_venues_and_adds_none`, `test_every_approved_venue_has_a_declared_mechanism`, `test_live_connections_for_approved_venues_are_unaffected` |
| 9 | Bitget block regression | `test_bitget_is_still_blocked_at_construction`, `test_bitget_is_absent_from_the_allowlist_and_the_mechanism_table`, `test_bitgets_endpoint_shape_would_be_refused_by_the_verifier` |

**These are unit tests over deterministic fake clients. They are not venue sandbox
certification.** No venue was contacted and no credential used; passing them certifies
nothing about any exchange. The file states this in its header.

### 4.4 Ownership-boundary verification

A case-insensitive scan for `sandbox_verified` initially flagged four hits in
`exchange_capability_registry.py` and `exchange_certification.py`. **Both are false
positives** — the pre-existing `ConnectivityState.SANDBOX_VERIFIED` member and
`CertificationLevel.LEVEL_3_SANDBOX_VERIFIED`. A case-sensitive scan for only this
workstream's new identifiers (`verify_sandbox_endpoint`, `SandboxVerificationFailed`,
`SANDBOX_MECHANISMS`, `assert_sandbox_supported`, `SandboxMechanism`,
`_close_unverified_client`) across `connection_engine.py`, `routers/exchange.py`, the
capability registry, `exchange_certification.py`, `routers/support.py`, `pytest.ini`,
`tests/conftest.py` and `requirements-base.txt` returns **no matches**.

`exchange_certification.py`, `routers/support.py` and `requirements-base.txt` show no git
modification at all. `routers/exchange.py`, `pytest.ini` and `tests/conftest.py` carry
only their baseline modifications. Every pre-existing marker in the executor
(`TRADE-04`, `KILL-03`, `TRADE-07`, `_executor_cache_key`, `BELOW_MIN_NOTIONAL`, and the
Workstream G routing block) is still present.

---

## 5. Supported venues that remain unverified

**No venue is sandbox-certified. The count is 0, unchanged.**

| Venue | Code-level mechanism | Venue sandbox certification | Why |
|---|---|---|---|
| binance | **verified** (`URL_SWAP`) | **NOT CERTIFIED** | no testnet credential; no venue round trip |
| bybit | **verified** (`URL_SWAP`) | **NOT CERTIFIED** | same |
| okx | **verified** (`HEADER`) | **NOT CERTIFIED** | same, **plus** `load_markets` is broken for OKX on ccxt 4.3.92 |
| coinbase | **UNSUPPORTED** | **STRUCTURALLY UNCERTIFIABLE** | no sandbox exists on this ccxt |
| kraken | **UNSUPPORTED** | **STRUCTURALLY UNCERTIFIABLE** | same |
| kucoin | **UNSUPPORTED** | **STRUCTURALLY UNCERTIFIABLE** | same |
| bitget | n/a | **BLOCKED FROM ROUTING** | EXCH-01; refused at construction |

"Code-level mechanism verified" means: the platform can now **prove**, offline, whether a
constructed client left production, and refuses when it cannot. It does **not** mean any
order has been observed at a venue sandbox.

---

## 6. Blockers and unsupported configurations

| | |
|---|---|
| **No sandbox credential exists** | The binding blocker for certification. Unchanged across Phases 7–10. Without one, scenarios 1–12 in `EXCHANGE_SANDBOX_CERTIFICATION.md` stay unexecuted. |
| **Three of six approved venues have no sandbox at all** | coinbase, kraken, kucoin. Not a defect and not fixable here — ccxt 4.3.92 publishes no `urls['test']` for them. Sandbox certification for these three is impossible on this stack. |
| **OKX market load is broken on the pinned ccxt** | Documented at `connection_engine.py:196-250`. Its sandbox mechanism verifies, but precision data may be unobtainable until the CCXT upgrade (measured as fixed in 4.5.85). |
| **The ccxt-level bitget reproduction remains red, correctly** | `tests/exchange_sandbox/test_sandbox_routing_contract.py` is still **3 failed / 8 passed**, unchanged. Those three measure **ccxt's** behaviour and the registry/product-copy claims, not the platform connect path. ccxt's bitget still changes nothing when armed — an upstream fact this change does not and should not alter. **Documentation inaccuracy to correct:** that test's docstring says "Fixing it turns this test green", which was written assuming a routing-level fix. The platform-level defect is now closed while the test stays red. Reported rather than edited, to avoid bundling an unrelated change. |
| **`SANDBOX_MECHANISMS` needs re-measuring on any ccxt upgrade** | The table is pinned to measured 4.3.92 behaviour. An upgrade could change a venue's mechanism. Re-run `staging/probe_exch01_sandbox_endpoints.py`. Fail-closed in the meantime: an unmeasured venue is refused. |

---

## 7. Remaining certification requirements

Before any venue can be called sandbox-certified:

1. **A testnet/demo credential** for binance, bybit or okx, supplied under the
   `AERORA_SANDBOX_<VENUE>_*` names the harness requires.
2. **A funded sandbox account** — balance, fill and fee scenarios need non-zero assets.
3. **A reachable Redis** — the kill switch is re-read at the venue boundary and fails
   closed when unreadable, so no order can be placed without it.
4. **Execution of the 12-scenario suite** in `tests/exchange_sandbox/`, with scenario 8
   (duplicate `clientOrderId`) run first: it is the only check on the platform's sole
   protection against a retry becoming a second real order.
5. **A release-gate decision** to grant `LIVE_TRADING_AUTHORIZED`, which no verification
   run can set.

---

## 8. Decision

**The code-level defect is FIXED.**

Sandbox status is now established by positive, venue-aware endpoint evidence read from the
client's own `urls` and `headers` — `apiBackup`/`api`/`test` agreement for URL-swap venues,
a value-checked `x-simulated-trading` header for OKX — and is refused when the mechanism is
unknown, unsupported, or the evidence is absent or ambiguous. A refused client is closed and
dropped before the raise. `self.sandbox` (request) and `sandbox_verified` (evidence) are
separate, so an unverified or unconnected executor cannot be represented as
sandbox-certified. The log line that previously announced SANDBOX mode now runs only after
verification succeeds, and names the mechanism that was proven.

**Venues that remain uncertified: all seven.** binance, bybit and okx have a verified
mechanism but no venue evidence. coinbase, kraken and kucoin have no sandbox on ccxt 4.3.92
and are structurally uncertifiable on this stack. bitget remains blocked from routing.

**Evidence still required for sandbox certification:** a non-production credential, a funded
sandbox account, a reachable Redis, and an actual venue round trip through the 12-scenario
suite. None of that was available here, none was simulated, and none is claimed.

Live trading remains OFF. `registry.tradable()` returns an empty list. No trading
authorization was granted. Nothing was committed, pushed or deployed.
