# EXCHANGE SANDBOX CERTIFICATION

**Phase 10 · Workstream E · Steps 3–5**
**Revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`), working tree dirty — see §6.2
**Date:** 2026-10-10
**Companion:** [`EXCHANGE_ADAPTER_INVENTORY.md`](EXCHANGE_ADAPTER_INVENTORY.md) (Steps 1–2)

---

## 1. Verdict

```
EXCHANGE SANDBOX CERTIFICATION:  NOT ACHIEVED — NO SANDBOX CREDENTIAL EXISTS
VENUES CERTIFIABLE IN PRINCIPLE:  binance, bybit, okx          (3 of 7, MEASURED)
VENUES NOT CERTIFIABLE AT ALL:    coinbase, kraken, kucoin     (no sandbox on this ccxt)
VENUES UNSAFE TODAY:              bitget                        (EXCH-01, P0-class)
SANDBOX ORDERS PLACED:            0
LIVE TRADING:                     OFF throughout
```

**The single sentence version.** Of seven exchanges this platform will accept an order
for, three have a sandbox that the order path can actually reach, three have none and
raise when asked, and one accepts the sandbox flag and sends the order to production
anyway. No credential exists for any of them, so zero sandbox orders were placed and the
twelve-scenario lifecycle remains unexecuted.

**What changed in Phase 10 despite that.** Phase 7 and 9 recorded sandbox E2E as
`BLOCKED — NO SAFE SANDBOX CREDENTIAL` and stopped there. That was correct but incomplete:
it treated the blockage as uniform across venues. It is not. The blockage is
credential-shaped for three venues, *structural* for three more, and for bitget there is
no blockage at all — the path is open and wrong. That distinction was not previously
measured, and finding EXCH-01 was reachable without a credential the whole time.

---

## 2. Sandbox test plan

### 2.1 Standing constraints

Taken from the brief and from `production-launch-hardening/tasks.md:1022`
(*"Live-order testing against a real exchange is out of bounds"*), and enforced
mechanically rather than by instruction — see §2.3.

| Never | Enforced by |
|---|---|
| Production exchange API keys | `conftest.py::_assert_no_production_credentials`, autouse |
| Live trading enabled | Nothing in the suite touches `VYOMQUANT_MODE` / `VYOMQUANT_ENABLE_LIVE_TRADING` |
| Real orders | `harness.SANDBOX_ROUTABLE` + the pre-order routing proof |
| Real customer funds | Sandbox accounts only; orders sized at the venue's published minimum |
| Secrets in logs or reports | Only env var **names** are ever printed or asserted on |
| Assuming every venue has an equivalent sandbox | §2.2 is explicitly per-venue |

### 2.2 Per-venue applicability

| Scenario | binance | bybit | okx | coinbase | kraken | kucoin | bitget |
|---|---|---|---|---|---|---|---|
| 1 Auth / account identity | ✔ | ✔ | ✔ | ✖ no sandbox | ✖ | ✖ | ⛔ unsafe |
| 2 Balance retrieval | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 3 Order placement | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 4 Open-order / status | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 5a Full fill | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 5b **Partial** fill | ⚠ not inducible | ⚠ | ⚠ | ✖ | ✖ | ✖ | ⛔ |
| 6a Cancellation | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 6b Double cancel | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 6c Cancel racing a fill | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 7 Timeout after submission | ⚠ consequence only | ⚠ | ⚠ | ✖ | ✖ | ✖ | ⛔ |
| 8 Duplicate client order id | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 9a Rate limit | ⚠ hard to provoke | ⚠ | ⚠ | ✖ | ✖ | ✖ | ⛔ |
| 9b Transient / terminal error | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 10 Reconnect / restart | ✔ | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 11 Fee and fill accounting | ✔ input only | ✔ | ✔ | ✖ | ✖ | ✖ | ⛔ |
| 12 Internal vs exchange recon | ⚠ venue half only | ⚠ | ⚠ | ✖ | ✖ | ✖ | ⛔ |

✔ executable · ⚠ executable with a stated limitation · ✖ venue has no sandbox ·
⛔ excluded on safety grounds (EXCH-01)

Three scenarios are **partially answerable by design**, and the limitation is recorded in
the test body rather than glossed:

- **5b partial fill** — cannot be forced deterministically on a thin sandbox book. Needs
  either enough resting depth to cross part of a large limit order, or a counter-order
  from a second sandbox account. It is the premise behind the non-accumulating
  `order.filled_size = str(event.filled)` assignment, so it matters.
- **7 timeout after submission** — a genuine socket timeout cannot be induced against a
  third party on demand. The *consequence* is reproduced (the response is discarded) and
  what gets certified is the recovery handle, not the timeout handling.
- **12 reconciliation** — there is no internal state to compare against, because no
  detector runs (EXCH-09). Only the venue-side half is certifiable: that the reads a
  reconciler would need exist and agree with each other.

### 2.3 How the harness is prevented from reaching production

Four independent mechanisms. The fourth exists because the first three are all one
editing mistake away from failing silently, and EXCH-01 is proof that this class of
mistake is real.

1. **Collection gate** — `AERORA_EXCHANGE_SANDBOX_CERT=1`, else the whole directory skips.
   Follows the precedent `pytest.ini` records for `tests/property/conftest.py`.
2. **Credential naming** — credentials must arrive as `AERORA_SANDBOX_<VENUE>_API_KEY` /
   `_SECRET` / `_PASSPHRASE`. The mandatory `SANDBOX` token means an operator cannot
   satisfy the harness with production keys they already hold.
3. **Production-credential refusal** — an autouse fixture fails the run if any of 19
   production-shaped names is set in the process. No value is read; only presence.
4. **Pre-order routing proof** — `_assert_not_production` builds a pristine reference
   client of the same class and compares the **connected** client's base URL and header
   set against it, immediately after `connect()` and before any order. If the client has
   not demonstrably moved off production, the test fails rather than trading. This is the
   one that catches a venue like bitget.

`SANDBOX_ROUTABLE = {binance, bybit, okx}` is derived from measurement
(`staging/probe_phase10_exchange_sandbox.py`), **not** from any registry in the codebase —
because two of those registries are wrong (EXCH-04).

### 2.4 The gates are satisfied, not bypassed

This is the difference between a certification and a bypass, so it is stated plainly.

`place_order` carries three controls before `create_order`: the anti-bypass validation
token, the TRADE-04 signed `LiveOrderAuthorization`, and a kill-switch re-read. The
harness does not reach past any of them. It **stands in for the execution gateway**:

- the token is minted with `global_safety.generate_validation_token` — the same function
  the execution engine calls;
- the authorisation is minted with the public `live_execution_gate.authorize_live_order`,
  which refuses unless every member of `REQUIRED_CHECKS` is named;
- `enforce_kill_switch()` runs untouched, and because `is_active()` fails **closed** on
  `RedisUnavailable`, a sandbox run additionally requires a reachable Redis. Recorded as a
  precondition in §2.5 rather than worked around.

Minting an authorisation is therefore an assertion the harness makes and is accountable
for. `_GATE_CHECKS` enumerates all thirteen check names with honest accounting of which
are substantive here (`kill_switch`, `precision`, `idempotency`) and which are vacuous
because there is no HTTP actor, tenant row, strategy or ledger in the loop. Listing them
in full rather than spreading a set means a new member of `REQUIRED_CHECKS` makes the
harness **fail to mint** rather than silently continue asserting a check nobody performed.

Platform-mode gating is certified separately by
`tests/test_trade04_live_order_gate_at_venue_boundary.py`, which this workstream did not
touch.

### 2.5 Preconditions for executing the plan

All five are currently unmet:

| # | Precondition | Status |
|---|---|---|
| 1 | Testnet/demo API keys for binance, bybit (and okx for wave 2) | ✖ **none exist** |
| 2 | Those sandbox accounts funded with testnet assets | ✖ |
| 3 | `MASTER_ENCRYPTION_KEYS` set, if testing via the vault rather than env | ✖ |
| 4 | A reachable Redis, or the kill switch fails closed and refuses every order | ✖ in this environment |
| 5 | OKX only: `load_markets` working, i.e. the ccxt 4.3.92 `parse_market` defect resolved | ✖ documented as broken |

---

## 3. Execution evidence

### 3.1 What was actually run

| Artefact | What it is | Result |
|---|---|---|
| [`p10e_sandbox_probe.txt`](p10e_sandbox_probe.txt) | Sandbox availability probe, offline | **completed** — 11 venues measured |
| [`phase10_exchange_sandbox_matrix.json`](phase10_exchange_sandbox_matrix.json) | Same, machine-readable | written |
| [`p10e_routing_contract.txt`](p10e_routing_contract.txt) | Certification suite, gate ON | **3 failed, 8 passed, 16 skipped** |
| [`p10e_default_lane_skip.txt`](p10e_default_lane_skip.txt) | Same suite, gate OFF | **27 skipped, 0 failed** |

The last row is the proof that this workstream did not red anyone else's lane.

### 3.2 The eight passes — safe alternatives, real evidence

These are evidence class **MEASURED**. They are not sandbox certification and are not
labelled as such; they answer the layer below — *when this platform says "sandbox", where
does the order actually go?*

| Test | Establishes |
|---|---|
| `test_the_sandbox_gate_is_explicit` | The opt-in exists and is named, so a skipped run is self-explaining |
| `test_top_level_sandbox_config_key_is_dead_code` | `config["sandbox"]` arms nothing; `config["options"]["sandbox"]` arms everything |
| `test_routable_venue_leaves_production_when_sandbox_is_armed[binance]` | Base URL → `urls['test']` |
| `…[bybit]` | Base URL → `urls['test']` |
| `…[okx]` | URL unchanged **and that is correct** — header `x-simulated-trading` |
| `test_venue_without_a_sandbox_raises_from_the_constructor[coinbase]` | `NotSupported`, from outside the `try` |
| `…[kraken]` | `NotSupported` |
| `…[kucoin]` | `NotSupported` |

### 3.3 The three failures — reproductions, red by design

A reproduction that passes is not a reproduction. Each is written to assert the
**required** behaviour, so fixing the defect turns the test green with no edit to the test.

```
FAILED test_bitget_sandbox_flag_is_inert_and_orders_reach_production
  EXCH-01 REPRODUCED: bitget accepted sandbox mode and changed nothing —
  url_changed=False, headers_added=[], options['sandboxMode']=True.

FAILED test_certification_registry_sandbox_claims_match_measured_reality
  EXCH-04 REPRODUCED:
    coinbase: registry says sandbox=True, measured False
              (raised=NotSupported: coinbase does not have a sandbox URL)
    kucoin:   registry says sandbox=True, measured False
              (raised=NotSupported: kucoin does not have a sandbox URL)

FAILED test_customer_facing_faq_sandbox_claims_match_measured_reality
  EXCH-05 REPRODUCED: the customer-facing FAQ advertises sandbox support for
  ['coinbase', 'kucoin'], and arming sandbox mode for each raises ccxt NotSupported.
```

A note on the third, because the first draft of that test was wrong in a way worth
recording: a plain substring match reported Kraken as an overclaim. The FAQ answer ends
*"Kraken runs on production endpoints exclusively"*, which is accurate. The matcher now
requires a venue name followed by a parenthetical label — the form this copy uses to
advertise support — so Kraken is correctly not counted. **The copy gets Kraken right; it
gets Coinbase and KuCoin wrong.**

### 3.4 The sixteen skips — missing evidence, itemised

Every skip names the exact env vars an operator must set, and says in terms that a skip is
not a pass:

```
NO SANDBOX CREDENTIAL for binance. This is MISSING EVIDENCE, not a pass. To produce it,
set: ['AERORA_SANDBOX_BINANCE_API_KEY', 'AERORA_SANDBOX_BINANCE_SECRET',
'AERORA_SANDBOX_BINANCE_PASSPHRASE'] (testnet/demo keys only).
```

`test_05b_partial_fill_accumulation` skips for a second, independent reason that a
credential would not fix — see §4.2.

### 3.5 What was NOT run, and will not be claimed

No order, of any size, in any mode, against any venue. No authenticated request to any
exchange. No credential read, decrypted, or invented. The `TRADING PAPER: NOT CLASSIFIED`
and `TRADING LIVE: OFF / NOT CERTIFIED` verdicts carried since Phase 7 stand unchanged,
and this workstream adds no evidence that would move either.

---

## 4. Unsupported and untested scenarios

### 4.1 Structurally unsupported — no credential would help

| Scenario | Venues | Why | Safe alternative run |
|---|---|---|---|
| Entire 12-scenario lifecycle | coinbase, kraken, kucoin | No sandbox exists in ccxt 4.3.92. `connect(sandbox=True)` raises | ✔ §3.2 — the raise is pinned as the measured reason |
| Entire lifecycle | bitget | Excluded on safety grounds, not capability. The flag is accepted and routes to production | ✔ §3.3 — EXCH-01 reproduction |
| Order types beyond market/limit | all seven | `"stop_market"`, `"stop_limit"`, `"take_profit_*"` are not ccxt unified types. OCO / post-only / reduce-only / `timeInForce` are absent from the code | ✖ needs a venue to confirm rejection |
| Per-venue rate-limit conformance | all seven | The configured per-exchange limits live in a module with zero importers; the live limiter is flat 5 req/s | ✖ |
| 429 / `Retry-After` handling | all seven | Neither exists on the live path | ✖ |

### 4.2 Untested but testable — blocked only on a credential

| Scenario | Blocker | Extra condition |
|---|---|---|
| 1 Auth / account identity | credential | — |
| 2 Balance retrieval | credential | funded sandbox account |
| 3 Order placement | credential | reachable Redis (kill switch fails closed) |
| 4 Open-order / status, incl. `clientOrderId` lookup | credential | — |
| 5a Full fill | credential | funded account |
| 6a/6b/6c Cancellation and races | credential | — |
| 7 Timeout after submission | credential | reproduces the consequence, not the cause |
| **8 Duplicate client order id** | credential | **the highest-value unrun test — see below** |
| 9b Terminal error classification | credential | — |
| 10 Reconnect / restart recovery | credential | — |
| 11 Fee input from the venue | credential | funded account |
| 12 Venue-side reconciliation reads | credential | — |

**Scenario 8 is the one to run first.** The platform's only protection against a retry
becoming a second real order is that `execution_id` is forwarded as `clientOrderId` and
that the *venue* enforces uniqueness on it. ccxt does not normalise this —
`newClientOrderId` on Binance, `orderLinkId` on Bybit, `clientOrderId` on OKX — and nothing
in this repository verifies the mapping or that the venue echoed the id back. If a venue
silently ignores the field, every retry path above this layer doubles a position. One
sandbox credential settles it.

### 4.3 Not answerable by any sandbox

| Question | Why a sandbox cannot answer it | Finding |
|---|---|---|
| Does the platform *record* venue-reported fees? | `handle_fill` has no fee field; the listener drops `fee.cost`. The venue will report a fee correctly and the platform will discard it | EXCH-08 |
| Does restart recovery rediscover resting orders? | `_recover_live_state` re-queries balance only. A sandbox can prove the venue *would* answer `fetch_open_orders`; nothing calls it | EXCH-07 |
| Does internal-vs-exchange reconciliation run? | The only scheduled reconciler reads a DB table no running detector writes | EXCH-09 |
| Are partial fills accumulated correctly across a missed event? | No sequence number or version check exists to test against | §2.6 of the inventory |
| Do fills arrive by WebSocket as documented? | The listener is never started | EXCH-12 |

These are platform-side gaps. They need code owners, not credentials.

---

## 5. Step 4 — defect handoff

Reported for coordination. **No shared execution, risk-control, tenant-isolation,
connection-pool, ledger or migration code was modified by this workstream**, and
TEST-05 and the retry/idempotency test files were not touched.

### EXCH-01 · P0-class · bitget · sandbox routing

**"Sandbox mode" is accepted, logged, and sends orders to production.**

*Reproduce:* `AERORA_EXCHANGE_SANDBOX_CERT=1 pytest
tests/exchange_sandbox/test_sandbox_routing_contract.py::test_bitget_sandbox_flag_is_inert_and_orders_reach_production`
→ fails. Also §B/§C of `staging/p10e_sandbox_probe.txt`.

*Code path:* `exchange_executor.py:864-868` sets `config["options"] = {"sandbox": True}`;
ccxt's constructor calls `set_sandbox_mode(True)`; `ccxt.bitget.set_sandbox_mode` overrides
the base and **does not call `super()`**:

```python
def set_sandbox_mode(self, enabled):
    self.options['sandboxMode'] = enabled
```

No URL swap, no header, and bitget publishes no `urls['test']`. Then
`exchange_executor.py:869` logs `"Connecting to bitget in SANDBOX mode"` and `place_order`
logs `sandbox=True` on every order. The log says sandbox; the socket says production.

*Measured:* `url_changed=False`, `headers_added=[]`, `options['sandboxMode']=True`.

*Risk:* A trader who ticks "Testnet / Sandbox Mode" on a bitget connection gets **real
orders against real balances**, with every log line asserting otherwise. This is distinct
from EXCH-02 in the way that matters: coinbase, kraken and kucoin *refuse*, and a refusal
cannot lose anyone's money.

*Mitigating today:* bitget has no connection-form schema entry, so the UI may not currently
offer it — but it **is** on the order allowlist and `get_exchange_executor` will build it.

*Recommended fix (not applied — shared code):* in `CCXTExchangeExecutor.connect`, after
construction, assert the client actually left production (base URL changed **or** a
simulated-trading header was added) and raise otherwise. That fixes the whole class, not
just bitget, and it is the same check `_assert_not_production` already implements in the
harness. Interim: remove `bitget` from `SUPPORTED_EXCHANGES`.

*Owner:* execution/venue-boundary owner. **Launch impact: blocks any launch that exposes
bitget.**

### EXCH-02 · P1 · coinbase, kraken, kucoin · sandbox routing

**`connect(sandbox=True)` raises `NotSupported` from outside the `try`.**

*Reproduce:* `…::test_venue_without_a_sandbox_raises_from_the_constructor` (passes — it
pins the behaviour).

*Code path:* `exchange_executor.py:871` — `self._exchange = exchange_class(config)` sits
**before** the `try:` wrapping `fetch_balance()`, so the raise never passes through
`_handle_ccxt_error`. Callers written to catch this platform's `ExchangeError` will not
catch a raw ccxt `NotSupported`.

*Risk:* Low money risk, real correctness risk. These three venues cannot be
sandbox-certified at all, and the failure mode is an unclassified exception rather than a
clear "this venue has no sandbox" refusal.

*Recommended fix:* move construction inside the `try`, and classify `NotSupported` as a
terminal `ExchangeError` with a specific code. *Owner:* same as EXCH-01.

### EXCH-03 · P3 · all · sandbox routing

**`config["sandbox"] = True` is dead code.** ccxt reads the flag from `self.options`, so
only `config["options"] = {"sandbox": True}` arms it. Pinned by
`…::test_top_level_sandbox_config_key_is_dead_code`. The pair reads as belt-and-braces and
is not — deleting the "redundant" second line silently routes every sandbox order to
production. A previous analysis of this code reached the opposite conclusion by reading the
first line alone. *Fix:* delete the dead key, or replace both with an explicit
`set_sandbox_mode(True)` call matching `ConnectionEngine`.

### EXCH-04 · P1 · certification registry

**The registry claims sandboxes and order types that do not exist, and gates live
deployment on those claims.**

*Reproduce:* `…::test_certification_registry_sandbox_claims_match_measured_reality` → fails.

*Measured disagreements:* coinbase `sandbox=True` (actually raises); kucoin `sandbox=True`
(actually raises). Kraken is the control — it declares `sandbox=False` with the limitation
"No public CCXT sandbox endpoint", which is correct, proving the field *can* be right and
that these two are errors rather than a convention.

*Additionally, unmeasured by that test but read in the code:* `post_only=True` and
`reduce_only=True` on six of seven records, while neither is sent to any venue anywhere in
`backend_app`. And OKX is `LEVEL_5_PRODUCTION_READY` + `HEALTHY` while
`connection_engine.py:196-250` documents its `load_markets` as broken on the pinned ccxt.

*Risk:* `validate_deployment_preflight` (`exchange_certification.py:310+`) refuses live
deployment unless `is_production_certified`, and these hand-written constants are what it
reads. A capability matrix that is wrong in the permissive direction is a gate that passes
things it should stop.

*Recommended fix:* derive `sandbox` from `set_sandbox_mode` reachability and the order-type
booleans from what `place_order` actually sends, or mark the records as unverified metadata
and stop gating on them. *Owner:* execution/venue-boundary owner, with coordinator sign-off
since preflight is a shared gate.

### EXCH-05 · P2 · product copy

**The customer-facing FAQ advertises two sandboxes that raise `NotSupported`.**
`routers/support.py:142-146` names "Coinbase (Sandbox)" and "KuCoin (Sandbox)".
*Reproduce:* `…::test_customer_facing_faq_sandbox_claims_match_measured_reality` → fails.
Same class as `FINAL_PRE_LAUNCH_TEST_REPORT.md:230`'s "110 exchanges" misrepresentation
risk. *Owner:* product/content, with the same owner as EXCH-04 supplying the true list.

### EXCH-06 · P3 · bitget · registry coverage

Tradable via the allowlist, absent from both the certification registry and the connection
schema, so `/api/exchanges/supported` reports `certification_level: 0` for a venue the
executor will happily build. Resolve together with EXCH-01 — the likely correct answer is
to remove it from the allowlist.

### EXCH-07 · P1 · all · restart recovery

`master_executor._recover_live_state` re-queries **balance only** on restart and never
calls `fetch_open_orders`. No account-level "what is actually open at the venue" sweep
exists anywhere. After a process restart, a resting order the platform has no record of
stays invisible until the 15-minute `OrderWatchdog` stale window happens to pick up a row
that *does* exist. Scenario 10 certifies that the venue makes recovery possible; nothing
performs it. *Owner:* execution/recovery owner.

### EXCH-08 · P1 · all · fee accounting

Venue-reported `commission` / `commissionAsset` are parsed nowhere.
`TransactionalExecutionManager.handle_fill` has no fee field; the WebSocket listener
extracts `fee.cost` and `fee.currency` into `FillEvent` and then drops both when building
`fill_data` (`exchange_websocket_listener.py:344-351`). What reaches PnL is a flat
`fee_rate=0.001`, identical for every venue and both sides of the book. The well-built
`fee_engine` that handles fee currency correctly has no production caller. *Owner:*
ledger/accounting owner — **this workstream must not touch it.**

### EXCH-09 · P1 · all · reconciliation

The only scheduled reconciler (`core/reconciliation_scheduler.py`, `main.py:456`) polls a
`reconciliation_mismatches` table and escalates by severity. **No running detector writes
to that table.** Of the three detectors that exist, one is never started, one is referenced
only through a class name that does not exist in its module, and one has every data source
stubbed to `[]` with hardcoded `["tenant_1","tenant_2"]`. No balance reconciliation and no
fill reconciliation run anywhere. *Owner:* reconciliation owner.

### EXCH-10 · P2 · product copy

`/api/exchanges/supported` iterates all of `ccxt_base.exchanges` (~100) and reports
`spot_support` / `futures_support` / `sandbox_support` from each class's `has` map, while 7
can receive an order and 3 have a usable sandbox. Already raised at
`FINAL_PRE_LAUNCH_TEST_REPORT.md:230`; restated here with the sandbox dimension measured.

### EXCH-11 · P2 · all · balance

`master_executor.py:264-280` substitutes `{"USDT": {"total": 10000.0}}` when
`self.ccxt_bridge.api_key` is falsy, and replaces a zero quote balance with
`Decimal("10000.0")`. That fabricated equity feeds position sizing. *Owner:* execution
owner.

### EXCH-12 · P2 · all · fills

`exchange_websocket_listener.py` describes itself as "WebSocket = primary source of truth"
and is never started — `main.py` does not reference it. Fills arrive by polling instead.
Its reconnect logic also has four independent defects (fixed 1 s retry with no backoff or
cap; unused `symbols` argument so no explicit resubscribe set; a `get_or_create_exchange`
failure permanently exits the task; `start_listening` never sets `_running`), and it calls
`get_or_create_exchange` with **no credentials** for `watch_orders`, which is a private
channel. *Owner:* execution/streaming owner.

---

## 6. Workstream report

### 6.1 Deliverables

| Brief item | Where |
|---|---|
| Exchange adapter inventory | [`EXCHANGE_ADAPTER_INVENTORY.md`](EXCHANGE_ADAPTER_INVENTORY.md) §1 |
| Per-exchange capability matrix | same, §2 (12 sub-matrices) |
| Sandbox test plan | this document §2 |
| Actual sandbox execution evidence | this document §3 + four run logs |
| Unsupported and untested scenario list | this document §4 |
| Launch-scope recommendation | `EXCHANGE_ADAPTER_INVENTORY.md` §4 |
| Workstream report and revision reference | this document §6 |

### 6.2 Files created — all new, nothing shared was edited

```
staging/probe_phase10_exchange_sandbox.py        offline sandbox availability probe
staging/p10e_sandbox_probe.txt                   its run log
staging/phase10_exchange_sandbox_matrix.json     machine-readable matrix
staging/p10e_routing_contract.txt                certification suite, gate ON
staging/p10e_default_lane_skip.txt               same suite, gate OFF (27 skipped)
staging/EXCHANGE_ADAPTER_INVENTORY.md            Steps 1-2
staging/EXCHANGE_SANDBOX_CERTIFICATION.md        Steps 3-5 (this file)
tests/exchange_sandbox/__init__.py
tests/exchange_sandbox/conftest.py               collection gate + credential guard
tests/exchange_sandbox/harness.py                constants, routing helpers
tests/exchange_sandbox/test_sandbox_routing_contract.py    11 offline tests
tests/exchange_sandbox/test_sandbox_order_lifecycle.py     16 gated venue tests
tests/exchange_sandbox/README.md                 operator instructions
```

Naming follows the established Phase 6–9 convention in `staging/`: `probe_phase<N>_*.py`
for read-only probes, `p<N><workstream>_*.txt` for run logs, `SCREAMING_SNAKE.md` for
audit documents.

### 6.3 Ownership boundaries respected

| Not to be edited | Edited? |
|---|---|
| Shared order execution / risk-control implementation | **No** |
| TEST-05 and retry/idempotency test files | **No** |
| Tenant isolation, quotas, connection-pool, financial ledger | **No** |
| Shared migrations, global audit artifacts | **No** |
| `pytest.ini` (shared) | **No** — gating done via a local `conftest.py` instead |

Every defect in §5 whose fix touches shared code is reported with a recommended fix and an
owner, and left unapplied. The ~80 in-flight modifications from other workstreams in the
working tree were preserved untouched.

### 6.4 Correction to the prior record

`staging/TRADING_SAFETY_AUDIT.md:558-574` and
`staging/FULL_REGRESSION_FAILURE_DISPOSITION.md:198-204` disposition **ENV-01** (the
unmocked live-Binance call in `test_connection_pooling_safety`) as "reported, not fixed".
**That disposition is stale.** The test has been rewritten with an in-process
`_ControlledVenueClient` installed by monkeypatching `connection_engine.ccxt`, and the
assertions are now stronger than the originals — they read back the credentials each
pooled client actually carries rather than comparing object identity alone. No test in
`tests/` now makes a real network call to an exchange.

Flagged, not edited: `TRADING_SAFETY_AUDIT.md` is a shared audit artifact and updating it
needs coordinator approval.

Separately, several older documents in `audit/` assert sandbox verification that no run log
supports — `ALGO_TRADING_REAL_APPLICATION_FINAL.md:14` ("CCXT Testnet │ VERIFIED"),
`BACKEND_UNVERIFIED_ITEMS.md:11` ("CCXT adapters tested in sandbox/paper mode"),
`BACKEND_FIX_LOG.md:9` ("Verified sandbox order execution"). These are contradicted by
this workstream's measurements and by the later, more rigorous
`audit/CROSS_BOUNDARY_REMAINING_UNVERIFIED.md:34-49`, which marks every exchange→DB row
`EXCHANGE_SANDBOX_RUNTIME NOT VERIFIED`. Recommend retracting the three claims; not done
here, same reason.

### 6.5 Running it

```powershell
# Offline — produces all evidence in §3.2/§3.3, no credential needed
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'
python -m pytest tests/exchange_sandbox/ -q -rs

# Availability probe on its own
python staging/probe_phase10_exchange_sandbox.py

# The live lifecycle, once testnet keys exist (see tests/exchange_sandbox/README.md)
$env:AERORA_SANDBOX_VENUE='binance'
$env:AERORA_SANDBOX_BINANCE_API_KEY='<testnet key>'
$env:AERORA_SANDBOX_BINANCE_SECRET='<testnet secret>'
python -m pytest tests/exchange_sandbox/test_sandbox_order_lifecycle.py -q
```

Default lane is unaffected: without the gate the directory reports 27 skipped, 0 failed
([`p10e_default_lane_skip.txt`](p10e_default_lane_skip.txt)).

### 6.6 What the coordinator is being asked for

1. **The mandatory launch exchange list** — not defined anywhere in this repository
   (`EXCHANGE_ADAPTER_INVENTORY.md` §4.1). Three questions there need answers before the
   §4.2 recommendation becomes a plan.
2. **A decision on bitget (EXCH-01)** — independent of launch scope. The path is open and
   wrong today.
3. **An owner for EXCH-04** — the preflight gate reads the registry, so correcting it
   touches a shared control.
4. **Approval to update `TRADING_SAFETY_AUDIT.md`** for the stale ENV-01 disposition, and
   to retract the three unsupported `audit/` sandbox claims (§6.4).
5. **A sandbox credential**, if venue-observed certification is wanted in Phase 10. Without
   one, scenarios 1–12 stay unexecuted and `TRADING PAPER` stays NOT CLASSIFIED — which is
   the same conclusion Phase 7 reached, now with the per-venue structure measured.
