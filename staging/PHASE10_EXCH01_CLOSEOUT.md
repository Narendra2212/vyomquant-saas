# PHASE 10 — EXCH-01 SANDBOX VERIFICATION CLOSEOUT

**Workstream J.** Review and close the remaining software-level EXCH-01 items following
[`PHASE10_EXCH01_SANDBOX_ENDPOINT_REVIEW.md`](PHASE10_EXCH01_SANDBOX_ENDPOINT_REVIEW.md).
**Date:** 2026-10-10
**Live trading:** OFF. No exchange call, no credential, no order, no commit, no push.
**Full repository suite:** not run, per instruction.

---

## 1. Baseline and ownership boundaries

Re-established by inspection. The previous report's Git state and test counts were **not**
assumed.

| | |
|---|---|
| HEAD | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Branch | `main` |
| Staged paths (index vs HEAD) | **22** |
| Unstaged paths (worktree vs index) | **98** |
| Total porcelain paths | **247** |

### 1.1 In-scope file states, staged and unstaged separately

| Path | Index | Worktree | Numstat vs HEAD |
|---|---|---|---|
| `backend_app/backend/exchange_executor.py` | clean | **M** | **+1188 / −55**, all unstaged |
| `backend_app/core/exchange_capability_registry.py` | — | untracked | new (Workstream F/G) |
| `tests/test_exch01_sandbox_endpoint_verification.py` | — | untracked | new (previous workstream) |
| `tests/test_exch01_bitget_routing_is_blocked.py` | — | untracked | new (Workstream G) |
| `tests/exchange_sandbox/` | — | untracked | new (Workstream E) |
| `staging/probe_exch01_sandbox_endpoints.py` | — | untracked | new |
| `backend_app/core/exchange_certification.py` | clean | clean | **unmodified** |

Nothing in scope is staged. The executor's entire +1188/−55 is unstaged working-tree work
from earlier phases plus Workstreams G and the endpoint review.

### 1.2 Concurrent ownership — verified, not assumed

| Path | Owner | Baseline | Final | Touched by Workstream J? |
|---|---|---|---|---|
| `backend_app/backend/connection_engine.py` | **Workstream C** | +188/−18 | **+188/−18** | **No** |
| `backend_app/routers/exchange.py` | **Workstream C** | staged +268/−15, unstaged +154/−14 | **identical** | **No** |
| `backend_app/backend/exchange_executor.py` | mixed | +1188/−55 | **+1188/−55** | **No** |
| `pytest.ini`, `tests/conftest.py` | other | ` M` | ` M` | **No** |
| `requirements-base.txt`, `exchange_certification.py`, `routers/support.py` | other | clean | **clean** | **No** |

Byte-for-byte identical numstats before and after confirm Workstream C's work is preserved.
CCXT was not upgraded.

**Workstream J changed exactly one source file**, documentation-only — §6.

---

## 2. Final diff summary

| File | Change | Nature |
|---|---|---|
| `tests/exchange_sandbox/test_sandbox_routing_contract.py` | module docstring + one test docstring | **documentation only** — no assertion, no logic, no skip/xfail |
| `staging/PHASE10_EXCH01_CLOSEOUT.md` | new | this report |
| `staging/p10j_run1.txt`, `p10j_run2.txt`, `p10j_run3.txt` | new | run logs |

No production code was modified in this workstream. The endpoint-verification
implementation was reviewed as-is.

---

## 3. Endpoint-verification safety review

Reviewed by code inspection of the current working-tree state plus the 28 deterministic
tests. Element locations confirmed present in `exchange_executor.py`:

| Element | Line |
|---|---|
| `class SandboxMechanism` | 799 |
| `SANDBOX_MECHANISMS` | 819 |
| `class SandboxVerificationFailed` / `SandboxVerification` | 829 |
| `verify_sandbox_endpoint` | 878 |
| `assert_sandbox_supported` | 1001 |
| `sandbox_verified` property | 1115 |
| `sandbox_evidence` property | 1126 |
| `assert_sandbox_supported(self.exchange_id)` in `connect()` | 1297 |
| `verify_sandbox_endpoint(self._exchange, …)` in `connect()` | 1334 |
| `_close_unverified_client` | 1336 |
| `"VERIFIED SANDBOX mode"` log | 1344 |

### 3.1 Required confirmations

| Requirement | Verdict | Evidence |
|---|---|---|
| Unknown mechanism fails closed | **Confirmed** | venue absent from `SANDBOX_MECHANISMS` → `SandboxVerificationFailed`; `test_an_unmeasured_venue_is_refused_rather_than_guessed` |
| Unsupported mechanism fails closed | **Confirmed** | `assert_sandbox_supported` refuses **before** a client is built, at line 1297; 3 parametrised tests |
| URL-swap requires positive endpoint identity | **Confirmed** | three conjunctive conditions — `apiBackup` present, `api == test`, `api != apiBackup`. "Something changed" is insufficient; `test_an_endpoint_that_is_neither_production_nor_the_declared_sandbox_is_refused` |
| Header venues validate the header **value** | **Confirmed** | presence alone rejected; value must be `1`/`true`. `x-simulated-trading: 0` refused — `test_a_header_set_to_a_non_demo_value_is_refused` |
| Unverified client closed and dereferenced before the failure propagates | **Confirmed** | `_close_unverified_client()` awaited, sets `_exchange = None`, then `raise`. Asserted by `created[0].closed == 1` **and** `ex._exchange is None` |
| Request state not confused with evidence | **Confirmed** | `self.sandbox` (request) vs `sandbox_verified` (evidence) are separate; `_sandbox_verification` defaults `None` so an unconnected executor reports `False` |
| Success logged only after verification | **Confirmed** | the old unconditional `logger.info("… in SANDBOX mode")` is gone from live code — its only remaining occurrence (line 748) is inside a `#` comment documenting the replaced defect. The live log at 1344 runs after `verify_sandbox_endpoint` returns and names the proven mechanism |
| Bitget blocked before reaching the connection path | **Confirmed** | `assert_exchange_routable` is the first statement of `BaseExchangeExecutor.__init__`, so bitget never reaches `connect()` |
| Live-order authorization intact | **Confirmed** | `require_live_authorization` (2 sites) and `enforce_kill_switch` (4 sites) present; `test_place_order_still_requires_a_live_authorization` drives a *verified sandbox* client and the order is still refused |
| No allowlist or trading capability broadened | **Confirmed** | §8 |

### 3.2 A design point worth recording

`SANDBOX_MECHANISMS` is derived from measurement
(`staging/probe_exch01_sandbox_endpoints.py`), **not** from ccxt's `has` map and **not**
from any capability registry. That is deliberate: two registries in this repository were
wrong about precisely this field — `exchange_certification.py` declares coinbase and
kucoin `sandbox=True` and both raise `NotSupported` (EXCH-04, still open, see §5).
Sourcing the mechanism table from either would have reproduced the defect inside the fix.

The table is also pinned to ccxt 4.3.92 behaviour. **Any CCXT upgrade requires re-running
the probe**, because a venue's mechanism could change. Fail-closed in the interim: an
unmeasured venue is refused.

---

## 4. Focused test commands and actual outcomes

Real exit codes captured. No run produced zero executed tests, and no startup failure was
treated as a pass.

### Run 1 — sandbox endpoint verification

```powershell
python -m pytest tests/test_exch01_sandbox_endpoint_verification.py `
  -q --no-header --tb=short --timeout=120
```
`EXIT=0` · **28 passed · 0 failed · 0 skipped · 0 collection errors** —
[`p10j_run1.txt`](p10j_run1.txt)

### Run 2 — bitget routing and live-order safety

```powershell
python -m pytest tests/test_exch01_bitget_routing_is_blocked.py `
  tests/test_trade04_live_order_gate_at_venue_boundary.py `
  tests/test_kill03_authoritative_global_halt.py `
  tests/test_exchange_executor_cache_is_per_tenant.py `
  tests/test_exchange_capability_registry.py `
  -q --no-header --tb=short --timeout=180
```
`EXIT=0` · **123 passed · 0 failed · 6 skipped · 0 collection errors** —
[`p10j_run2.txt`](p10j_run2.txt)

The 6 skips are the `realaudit`-marked PostgreSQL tests in
`test_kill03_authoritative_global_halt.py`, gated on `AERORA_TEST_DATABASE_URL` which is
not set here. Pre-existing gate, unrelated to EXCH-01, and `pytest.ini` documents that a
skip there is not a pass.

### Run 3 — CCXT sandbox contract, separately

```powershell
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'
python -m pytest tests/exchange_sandbox/test_sandbox_routing_contract.py `
  -q --no-header --tb=no -rf --timeout=180
```
`EXIT=1` · **8 passed · 3 failed · 0 skipped · 0 collection errors** —
[`p10j_run3.txt`](p10j_run3.txt)

Identical to the previous report's outcome, and the same three test identities. The
documentation correction changed no behaviour.

**Totals across the three runs: 159 passed · 3 failed (all expected, §5) · 6 skipped ·
0 collection errors.**

---

## 5. Analysis of the three CCXT contract failures

Each was traced to its assertion and its owner. **None is an open platform-level defect.**

### 5.1 `test_bitget_sandbox_flag_is_inert_and_orders_reach_production`

```python
m = measure("bitget")
assert m["raised"] is not None or m["url_changed"] or m["headers_added"]
```

`measure()` constructs a `ccxt.async_support.bitget` client and diffs its URL and header
tables against an unarmed one. **No platform code is involved** — the file does not import
`CCXTExchangeExecutor` and never calls `connect()`.

| | |
|---|---|
| Subject | **upstream ccxt 4.3.92** |
| Why red | `ccxt.bitget.set_sandbox_mode` sets only `options['sandboxMode']`; bitget publishes no `urls['test']`, so nothing observable changes |
| What would turn it green | an upstream change only — ccxt calling `super()`, or bitget publishing a testnet URL. Neither is in this repository's gift |
| Owner | **upstream / dependency triage** |
| Platform exposure | **closed.** Bitget is withheld from routing and refused at construction; `connect()` refuses any venue it cannot prove left production |

**This is the assertion whose documentation was wrong** — see §6.

### 5.2 `test_certification_registry_sandbox_claims_match_measured_reality`

```python
assert not disagreements
```

Compares `exchange_certification.py`'s hand-written `sandbox` booleans against measured
ccxt behaviour. Two disagree: coinbase and kucoin declare `sandbox=True` and both raise
`NotSupported`. Kraken is the control — it declares `sandbox=False` correctly, proving the
field *can* be right.

| | |
|---|---|
| Subject | `backend_app/core/exchange_certification.py` |
| Finding | **EXCH-04, open** |
| Risk | `validate_deployment_preflight` gates live deployment on these records |
| Owner | execution/venue-boundary owner, with coordinator sign-off (shared gate) |
| In scope here? | **No** — explicitly excluded; the file is unmodified |

### 5.3 `test_customer_facing_faq_sandbox_claims_match_measured_reality`

```python
assert not overclaimed
```

The published FAQ in `routers/support.py` advertises "Coinbase (Sandbox)" and
"KuCoin (Sandbox)"; both raise. Kraken is correctly excluded by that copy and correctly not
counted by the matcher.

| | |
|---|---|
| Subject | `backend_app/routers/support.py` (product copy) |
| Finding | **EXCH-05, open** |
| Owner | product/content |
| In scope here? | **No** — explicitly excluded; the file is unmodified |

### 5.4 Summary

| Test | Subject | Platform defect? | Owner |
|---|---|---|---|
| bitget inert | upstream ccxt | **No** — platform half closed | dependency triage |
| registry claims | `exchange_certification.py` | No — registry accuracy | venue-boundary owner |
| FAQ claims | `routers/support.py` | No — product copy | product |

All three were **preserved**. None was skipped, xfailed, relaxed, or converted to a pass.

---

## 6. Documentation-only correction

### 6.1 What was wrong

Two inaccuracies, both conflating upstream library behaviour with the platform connect path:

1. **Module docstring** — *"That is a property of the pinned ccxt's URL tables and of
   twelve lines in `backend_app/backend/exchange_executor.py`, and it is fully measurable
   here."* The file measures **only** ccxt; it never touches the executor. And those twelve
   lines no longer exist — they are now the verifier.

2. **Module docstring** — *"They are written to fail while the defects are open and to pass
   once they are fixed."* True for EXCH-04 and EXCH-05. **False for EXCH-01**: the platform
   defect is closed and the test is still red, because it measures ccxt.

3. **Test docstring** — *"Fixing it turns this test green; no change to this file is
   needed."* Wrong for the same reason. The docstring also described `connect()` logging
   "Connecting to bitget in SANDBOX mode" in the **present tense**, which is no longer true.

Left uncorrected, a reader would reasonably conclude that EXCH-01 is still open on the
platform side. That is the specific harm this correction removes.

### 6.2 What was changed

- Added a **SCOPE** section to the module docstring stating that every assertion
  constructs a ccxt client and that no platform code is exercised, and pointing to
  `tests/test_exch01_sandbox_endpoint_verification.py` for the platform half.
- Replaced "to pass once they are fixed" with a per-finding breakdown of **what would
  actually turn each green** and who owns it.
- Rewrote the bitget test docstring: subject stated as ccxt rather than the platform, the
  historical `connect()` behaviour moved to past tense, the prior claim explicitly
  retracted and corrected, and the three reasons the platform exposure is closed
  enumerated.

### 6.3 Proof it was documentation-only

- All ten assertions in the file remain present and verbatim, including the three red ones
  at lines 281, 329 and 372.
- Run 3 outcome is **identical** before and after: `EXIT=1`, 3 failed / 8 passed, same three
  test identities.
- No `skip`, `xfail`, assertion relaxation, dependency change or registry change.

---

## 7. Bitget remains blocked — confirmed

Verified by execution:

```
allowlist      : ['binance', 'bybit', 'coinbase', 'kraken', 'okx', 'kucoin']
allowlist count: 6
blocked        : ['bitget']
routable       : ['binance', 'bybit', 'coinbase', 'kraken', 'okx', 'kucoin']
```

Four independent mechanisms, unchanged by this workstream:

1. absent from `ExchangeExecutorFactory.SUPPORTED_EXCHANGES`;
2. `assert_exchange_routable` refuses at the factory;
3. `assert_exchange_routable` refuses in `BaseExchangeExecutor.__init__` — first statement,
   ahead of credential retention, so direct construction cannot bypass it;
4. absent from `SANDBOX_MECHANISMS`, so even if routing were restored, sandbox verification
   would refuse it as unmeasured.

Regression: `test_exch01_bitget_routing_is_blocked.py` (22 tests) and the three bitget
tests in `test_exch01_sandbox_endpoint_verification.py`. All pass.

---

## 8. Allowlist and trading authorization not broadened — confirmed

```
allowlist count : 6          (unchanged)
mechanisms      : {binance: url_swap, bybit: url_swap, okx: header,
                   coinbase: unsupported, kraken: unsupported, kucoin: unsupported}
tradable count  : 0
```

- `SANDBOX_MECHANISMS` is an **exact set match** to the six approved venues. A mechanism
  entry is not a routing permission, and
  `test_the_mechanism_table_covers_the_approved_venues_and_adds_none` fails on an addition
  in either table.
- `registry.tradable()` is **empty**. `LIVE_TRADING_AUTHORIZED` cannot be set by any
  verification run.
- `test_connectivity_cannot_widen_the_routing_allowlist` still holds: 103 venues declare
  `createOrder`, 6 may be routed to, 0 are authorized.

---

## 9. Remaining venue-certification requirements

**Endpoint configuration verification proves none of the following**, and no such claim is
made here: venue connectivity, successful fills, fee accuracy, or safe order execution. It
proves one thing — that a constructed client demonstrably addresses a sandbox endpoint, or
is refused.

Prerequisites for any future sandbox-certification work, all unmet:

1. Approved **testnet/demo credentials** (`AERORA_SANDBOX_<VENUE>_*`).
2. **Funded sandbox accounts** for balance, fill and fee scenarios.
3. **Reachable Redis** — the kill switch is re-read at the venue boundary and fails closed.
4. **Execution of the 12 sandbox scenarios** in `tests/exchange_sandbox/`.
5. **Explicit review of duplicate-order and retry behaviour** — scenario 8 (`clientOrderId`
   uniqueness) is the only check on the platform's sole protection against a retry becoming
   a second real order, and ccxt does not normalise that field across venues.
6. A **separate release-gate decision** for `LIVE_TRADING_AUTHORIZED`.

Venue status:

| Venue | Mechanism (code level) | Venue certification |
|---|---|---|
| binance, bybit | verified `URL_SWAP` | **NOT CERTIFIED** |
| okx | verified `HEADER` | **NOT CERTIFIED** — plus `load_markets` broken on pinned ccxt |
| coinbase, kraken, kucoin | `UNSUPPORTED` | **STRUCTURALLY UNCERTIFIABLE** on ccxt 4.3.92 |
| bitget | n/a | **BLOCKED FROM ROUTING** |

---

## 10. Acceptance recommendation

### ACCEPT the software-level fix.

The definition of done is met:

- **The final diff supports it.** The endpoint verification is confined to
  `exchange_executor.py`; Workstream C's `connection_engine.py` and `routers/exchange.py`
  are byte-identical to baseline; `requirements-base.txt`, `exchange_certification.py` and
  `routers/support.py` are unmodified; CCXT was not upgraded.
- **The focused tests support it.** 159 passed, 0 unexpected failures, 0 collection errors
  across three bounded runs with real exit codes. All ten safety confirmations in §3.1 hold
  under both inspection and deterministic test.
- **The misleading regression documentation is corrected**, documentation-only, with every
  assertion preserved and the test outcome provably unchanged.
- **Unresolved upstream behaviour is recorded.** The bitget contract test stays red by
  design against ccxt 4.3.92, with the reason, the owner, and the fact that platform
  exposure is closed. EXCH-04 and EXCH-05 remain open with named owners and were correctly
  left out of scope.

### Residual risks accepted with this recommendation

| Risk | Disposition |
|---|---|
| `SANDBOX_MECHANISMS` is pinned to measured ccxt 4.3.92 behaviour | Re-run `staging/probe_exch01_sandbox_endpoints.py` on any upgrade. Fail-closed meanwhile |
| EXCH-04 open — the certification registry gates live deployment on two wrong `sandbox` booleans | Separate owner; reproduction retained and red |
| EXCH-05 open — published FAQ advertises two non-existent sandboxes | Product owner; reproduction retained and red |
| Three of six approved venues can never be sandbox-certified on this ccxt | Not a defect; recorded. Any paper/sandbox launch claim must exclude coinbase, kraken, kucoin |
| OKX `load_markets` broken on pinned ccxt | Mechanism verifies, precision data may not load. Tracked with the upgrade |
| No venue has sandbox or trading certification | Unchanged and correct. §9 |

### Explicitly not recommended and not granted

Sandbox certification for any venue. Live-trading authorization for any venue. Any claim
that endpoint verification establishes venue connectivity, fills, fee accuracy, or safe
order execution.

Live trading remains OFF. `registry.tradable()` is empty. Nothing was committed, pushed or
deployed.
