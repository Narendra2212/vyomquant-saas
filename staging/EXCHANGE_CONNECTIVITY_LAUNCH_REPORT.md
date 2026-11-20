# 100–110 EXCHANGE CONNECTIVITY — LAUNCH WORKSTREAM REPORT

**Phase 10 · Workstream F · Phases 1–7 of the brief**
**Revision:** `074b772ab16cece62cbfca0f2b78497be2ae56d5` (`main`), working tree dirty — §9.2
**Date:** 2026-10-10
**Live trading:** OFF throughout. No order submitted. No production credential used.
**Companions:** [`EXCHANGE_ADAPTER_INVENTORY.md`](EXCHANGE_ADAPTER_INVENTORY.md) ·
[`EXCHANGE_SANDBOX_CERTIFICATION.md`](EXCHANGE_SANDBOX_CERTIFICATION.md)

---

## 1. Verdict

```
LAUNCH TARGET:            100-110 venues with VERIFIED connectivity
ELIGIBLE CANDIDATES:      103 distinct venues   (ccxt 4.3.92, aliases excluded)
PUBLIC CONNECTIVITY:       72 verified by live public endpoint response
MARKET DATA VERIFIED:      69 verified by live ticker / order book / OHLCV
SHORTFALL TO 100:          28

TARGET MET:               NO
TARGET REACHABLE AS STATED: NO — the ceiling is below 100. See §5.

ORDER ROUTING:              6 venues (bitget REMOVED — EXCH-01)
SANDBOX VERIFIED:           0
TRADING CERTIFIED:          0
LIVE TRADING AUTHORIZED:    0
```

**The headline, stated plainly.** 72 of 103 venues answered a real public request from this
host. That is genuine, per-venue, evidence-backed connectivity — not a constructor check
and not a registry listing. It is also 28 short of the target, and the gap does not close
with more effort on this stack: **the verifiable ceiling is 97 on the pinned CCXT and 99
on the newest CCXT**, both below the 100 floor.

That is a dependency-and-ecosystem fact, not a reinterpretation of the requirement. The
"100–110" figure matches CCXT's own marketing claim and the raw exchange-ID count; it does
not match the number of venues whose public endpoints actually respond. §5 gives the
arithmetic.

**What did not happen, and will not be claimed.** No exchange reached
`AUTHENTICATED_ACCESS_VERIFIED`, `SANDBOX_VERIFIED`, `TRADING_CERTIFIED` or
`LIVE_TRADING_AUTHORIZED`. Connectivity was not allowed to imply any of them, by
construction — §4.

---

## 2. Phase 1 — Baseline

### 2.1 Repository state, preserved

| | |
|---|---|
| Branch / revision | `main` @ `074b772a` |
| Uncommitted paths at start | 234, from several workstreams in flight |
| Reset / stash / clean / revert | **none** |
| Shared files modified by this workstream | **none** — §9.3 |

### 2.2 Pinned dependency

`requirements-base.txt:95` → `ccxt[async]==4.3.92`; installed version confirmed 4.3.92.
Latest available is **4.5.85** — roughly two minor versions and ~190 patch releases on.
That gap turned out to matter (§5.3).

### 2.3 Conflicting registries found

Five surfaces named exchanges and disagreed:

| Surface | Count | Verdict |
|---|---|---|
| `ExchangeExecutorFactory.SUPPORTED_EXCHANGES` | 7 | authoritative for order routing only |
| `exchange_rate_limiter.ExchangeType` | 10 | module has **zero importers** |
| `exchange_certification` registry | 8 | **wrong** for coinbase/kucoin sandbox — EXCH-04 |
| `exchange_connection_schema` | 6 | **wrong** for coinbase/kucoin sandbox |
| `routers/exchange.py::/supported` | ~110 | conflates "ccxt knows it" with "tradable" — EXCH-10 |

Baseline test state and the full execution-safety findings were established in the prior
workstream and are carried forward in §7 rather than re-derived.

---

## 3. Phase 2 — The connectivity inventory

Built from the installed dependency. Nothing hardcoded.
Probe: `staging/probe_ccxt_universe.py` → [`p10f_ccxt_universe.txt`](p10f_ccxt_universe.txt) ·
[`ccxt_universe.json`](ccxt_universe.json)

| Measure | Count | Note |
|---|---|---|
| Raw ids in `ccxt.exchanges` | **110** | not 110 distinct venues |
| Constructible | 110 | zero constructor failures |
| Class-sharing alias groups | 0 | |
| `describe()['id']` mismatches | 0 | |
| Self-declared aliases (`alias: True`) | **7** | `bitbay`, `bitcoincom`, `bitpanda`, `coinbaseadvanced`, `gateio`, `hitbtc3`, `huobi` |
| Distinct venues | 103 | |
| **Eligible candidates** | **103** | distinct, constructible, non-alias, ≥1 public market-data method |

Counting the 7 aliases would have made the target easier to hit by listing the same venue
twice — `gateio`/`gate` and `huobi`/`htx` are each one exchange. They are excluded, and
`test_aliases_are_excluded_from_eligible_candidates` enforces it.

Declared capability across the eligible set — **library declarations, not verification**:
103 declare `createOrder`, 39 declare a sandbox URL, 63 are WebSocket-capable.

One correction to the prior report: **Delta Exchange does exist in CCXT** (`delta`). It is
absent from *this codebase*, which is a different statement. Zerodha/Kite is in neither.

---

## 4. Phase 3 — Architecture

New module: `backend_app/core/exchange_capability_registry.py`. A shared CCXT connection
layer was already present (`ConnectionEngine`, `CCXTExchangeExecutor`); what was missing
was a truthful capability model over it.

### 4.1 The eight states, and why they cannot be inferred

Every rung carries its own `StateEvidence` — method, detail, evidence class, failure
class, timestamp, artifact path. `has_state()` deliberately does **not** consult lower
rungs and conclude a higher one.

| # | State | Established by | Count now |
|---|---|---|---|
| 1 | `DISCOVERED` | id present in installed ccxt | 110 |
| 2 | `CLIENT_INITIALIZED` | class instantiates — **no network call** | 110 |
| 3 | `PUBLIC_CONNECTIVITY_VERIFIED` | venue answered a real public request | **72** |
| 4 | `MARKET_DATA_VERIFIED` | ticker / order book / OHLCV returned usable data | **69** |
| 5 | `AUTHENTICATED_ACCESS_VERIFIED` | authorized credentials accepted | 0 |
| 6 | `SANDBOX_VERIFIED` | sandbox reached **and** routing proven off production | 0 |
| 7 | `TRADING_CERTIFIED` | order lifecycle + safety suite passed | 0 |
| 8 | `LIVE_TRADING_AUTHORIZED` | production safety and release gates | 0 |

`LIVE_TRADING_AUTHORIZED` is unreachable from inside the module — nothing in it can set
that rung, and `is_tradable()` requires it. So no amount of connectivity verification can
make a venue tradable. `test_full_connectivity_still_does_not_authorize_trading` grants
rungs 1–7 to a synthetic record and asserts it is still not tradable.

Incoherent states are **surfaced, not auto-corrected**: `inconsistencies()` reports a rung
held while a prerequisite carries no evidence, because that means the ingest changed, not
that the venue is fine.

### 4.2 Precise capability flags

24 tri-state fields replace the single ambiguous `supported` boolean, split into public
market data / private account / execution / transport. **`None` means unverified and is
never coerced to `False`** — "we have not checked" and "it does not work" drive different
decisions, and a dashboard rendering both as a red cross lies about one of them.

The flag pair that matters most:

- `sandbox_url_declared` — a `urls['test']` lookup.
- `sandbox_routing_proven` — a *measurement* of whether arming sandbox mode actually left
  production.

They disagree for bitget, which is exactly why one field would have been dangerous.
Inventory never sets the second one, and a test enforces that.

---

## 5. Phases 4 — Verification, and the shortfall

Runner: `scripts/verify_exchange_connectivity.py`
Logs: [`p10f_pass1.txt`](p10f_pass1.txt) · [`p10f_pass2.txt`](p10f_pass2.txt) ·
[`p10f_pass3.txt`](p10f_pass3.txt)
Authoritative tally: [`p10f_tally.txt`](p10f_tally.txt) ·
[`connectivity_tally.json`](connectivity_tally.json)
Per-venue artifact: [`exchange_connectivity_results.json`](exchange_connectivity_results.json)

### 5.1 Safety properties of the runner

Four, and the second is structural rather than procedural:

1. **No credentials.** Empty config on every client; the runner refuses to start if any of
   17 production-shaped credential names is set in the environment.
2. **No order can be submitted.** All 40+ private and mutating ccxt methods are replaced
   on every client with a function that raises `_ForbiddenCall`. A call to `create_order`
   from inside this runner raises and fails the venue. This is not a review rule.
3. **Bounded.** Per-call timeout, per-venue wall budget, global concurrency cap, at most
   one retry — and only for a classified-transient failure, so a rate-limited or
   geo-blocked venue is never hammered.
4. **Read-only.** Exactly four venue calls: `load_markets`, `fetch_ticker`,
   `fetch_order_book`, `fetch_ohlcv`.

Venue-specific symbol formats are handled by reading each venue's **own** market map and
selecting deterministically by liquidity preference, rather than constructing a string —
which is what makes one runner work across `BTC/USDT`, `XBT/EUR` and `BTC-PERP`.

### 5.2 Result, and the attribution that keeps it honest

| | Count |
|---|---|
| Eligible candidates | 103 |
| **`PUBLIC_CONNECTIVITY_VERIFIED`** | **72** |
| **`MARKET_DATA_VERIFIED`** | **69** |
| Not verified | 31 |

The 31 split three ways, and the split is the point:

| Cause | Count | Venues |
|---|---|---|
| **HOST-CAUSED** — facts about this network, not the venue | **12** | ascendex, bitmex, bitrue, cex, coinlist, idex, kuna, lykke, novadax, oceanex, poloniexfutures, whitebit |
| **GENUINE** — venue or pinned library | **6** | alpaca, coinmetro, gemini, hyperliquid, okx, timex |
| **AMBIGUOUS** — timeout / 5xx, could be either | **13** | ace, bitmart, btcalpha, coinmate, currencycom, mercado, okcoin, onetrading, oxfun, probit, tradeogre, vertex, zonda |

**The host-caused 12 are proven, not assumed.** The runner's egress preflight found:

- a **DNS sinkhole** at `139.5.241.93` serving unrelated registrable domains, confirmed by
  a TLS handshake in which `ascendex.com` presented a certificate that does not verify for
  its own hostname — definitive impersonation;
- **9 hostnames returning NXDOMAIN**, including `api.kuna.io`, `hft-apiv2.lykke.com`,
  `trade-api.coinlist.co`, `api-matic.idex.io`, `api.oceanex.pro`.

Meanwhile the control hosts (`api.binance.com`, `api.kraken.com`, `www.bitstamp.net`)
resolved to correct, distinct CDN addresses and passed. So the filtering is selective, and
`EGRESS_FILTERED` is a separate failure class from `VENUE_UNAVAILABLE` precisely so a
corporate egress policy is never reported as a dead exchange.

Two corrections I made to my own method along the way, because both would have skewed the
number:

- My first sinkhole heuristic flagged any shared IP, which wrongly condemned
  `api.kraken.com`/`futures.kraken.com` — same company, one legitimate Cloudflare address —
  and then declared its own control group compromised. Fixed by grouping on registrable
  domain and requiring a TLS proof. **Overstating interception would have been as
  dishonest as understating it.**
- 7 venues first failed as `CancelledError`, which was my own budget cancellation leaking
  through, not a venue fact. Re-run with lower concurrency and longer timeouts, 6 of them
  passed. They are in the 72.

### 5.3 Why the target is unreachable as stated

Probe: `staging/probe_ccxt_upgrade_delta.py` → [`p10f_ccxt_upgrade_delta.txt`](p10f_ccxt_upgrade_delta.txt) ·
[`ccxt_upgrade_delta.json`](ccxt_upgrade_delta.json)
Run in a throwaway venv under `staging/.ccxt_upgrade_probe/`. The project `.venv` and
`requirements-base.txt` were **not** modified — this measures an upgrade, it does not
perform one.

```
                                    pinned 4.3.92      latest 4.5.85
raw exchange ids                           110                104
self-declared aliases                        7                  0
ELIGIBLE CANDIDATES                        103                104

verified now                                72                  —
+ host-caused (clean network)               12                 12
+ ambiguous (optimistic: all pass)          13                 13
---------------------------------------------------------------------
CEILING                                     97                 99
TARGET FLOOR                                100                100
```

Two findings here, both load-bearing:

**The CCXT universe is shrinking, not growing.** 4.5.85 ships **104** exchange ids, down
from 110. `coinmetro` and `timex` were removed upstream entirely. So "100–110" is at the
very top of what the library offers, with no headroom.

**An upgrade recovers 2 of the 5 library defects.** `okx` now loads **4,501 markets** and
`gemini` 345 — both previously failed inside ccxt while parsing a venue response. This
also **corrects the record in this repository**: `connection_engine.py:196-250` states
that the OKX `parse_market` fix is absent from 4.4.1 and 4.4.50. It is present in 4.5.85.
`hyperliquid` still times out; `coinmetro` and `timex` no longer exist.

Even granting every ambiguous venue, **the ceiling is 99 against a floor of 100.** The
requirement as literally stated cannot be satisfied by this library. §8 gives the options.

---

## 6. Phase 6 — Product and API truthfulness

New router: `backend_app/routers/exchange_capabilities.py`. Additive —
`routers/exchange.py` is being modified by another workstream and was left alone.

Six independent facts replace one boolean, because they are six different questions:

| Endpoint | Answers |
|---|---|
| `GET /api/exchanges/capabilities` | all 103 venues, six facts each |
| `GET /api/exchanges/capabilities/{id}` | one venue |
| `GET /api/exchanges/connectivity/summary` | the launch dashboard |
| `GET /api/exchanges/connectivity/launch-list` | verified / connectivity-only / not verified |
| `GET /api/exchanges/connectivity/states` | the ladder, documented for consumers |

Verified against the real artifact — [`p10f_capability_api.txt`](p10f_capability_api.txt):

```
venues reported                           : 103
venues claiming trading_authorized        :   0
venues the platform can route an order to :   6  [binance, bybit, coinbase, kraken, kucoin, okx]
venues safety-blocked                     :   1  [bitget]
venues claiming SANDBOX_VERIFIED          :   0
venues with MARKET_DATA_VERIFIED          :  69
sandbox routing UNVERIFIED (null, not false): 103
registry_inconsistencies                  :  []
ALL TRUTHFULNESS ASSERTIONS PASSED
```

The dashboard reports the verified count **together with** the shortfall attribution, so a
reader cannot mistake 12 egress-filtered venues for 12 broken exchanges.

---

## 7. Phase 5 — Execution safety

**Applied here**, both contained and additive:

| Action | Where |
|---|---|
| **Bitget removed from tradable routing** (EXCH-01) | `SAFETY_BLOCKED` in the registry; `order_routing_allowlisted()` now returns 6, verified in §6 |
| Safety-blocked is distinguishable from merely unlisted | a block carries its reason; tested |
| Fail-closed trading | `is_tradable()` requires rung 8, which no verification can set |
| Connectivity cannot widen routing | `test_declared_order_capability_is_not_a_trading_claim` fails if the allowlist is broadened to match CCXT's 103 `createOrder` declarations |

**Reported, deliberately not applied.** The remaining Phase 5 items land in shared
execution, ledger and reconciliation code that other workstreams are actively modifying,
and the brief says to implement in small reviewable changes rather than a sweeping
rewrite. Each is documented with reproduction, code path, risk and recommended owner in
[`EXCHANGE_SANDBOX_CERTIFICATION.md` §5](EXCHANGE_SANDBOX_CERTIFICATION.md):

EXCH-02 (sandbox raise outside the `try`) · EXCH-03 (dead sandbox config key) ·
EXCH-04 (certification registry gates deployment on wrong claims) · EXCH-05 (FAQ
overclaim) · EXCH-06 (bitget absent from registry) · EXCH-07 (restart recovery never
re-queries open orders) · EXCH-08 (venue fees parsed nowhere; flat 0.001 estimate) ·
EXCH-09 (reconciliation escalates from a table no detector writes) · EXCH-10
(`/supported` advertises ~110) · EXCH-11 (fabricated \$10,000 balance feeds position
sizing) · EXCH-12 (WebSocket fill listener never started).

Unsupported order-type mappings, per-venue `clientOrderId` idempotency, partial-fill
accumulation and timeout ambiguity are all documented in the companion reports; four of
the six declared order types are not CCXT unified types and most venues will reject them.

---

## 8. Launch-scope options

The 100-verified-venue target is not reachable on CCXT. Three honest routes, for the
coordinator to choose between — this is a scope decision, not a technical one:

**A. Restate the metric (recommended).** Launch on *market-data connectivity across
~95–99 venues*, which is achievable, evidence-backed, and genuinely useful — traders can
chart, screen and backtest. Requires: running the verifier from an unfiltered network, and
the CCXT upgrade. Advertise the verified number, whatever it turns out to be, from
`/connectivity/summary`.

**B. Keep "100–110" as the candidate count.** 103 eligible candidates on 4.3.92, 104 on
4.5.85 — both inside the band. This is defensible *only* if the copy says "integrated via
CCXT" and never "verified" or "supported", and if the dashboard shows the verified subset.
Close to the current misleading claim, so it needs careful wording.

**C. Supplement CCXT.** Add bespoke adapters for venues CCXT cannot verify. Contradicts
the brief's "use CCXT to avoid unnecessary bespoke adapters" and buys ~3 venues for
disproportionate cost and risk. Not recommended.

Under all three: order routing stays at 6 venues and trading authorization stays at 0
until the sandbox certification in the companion report is actually executed.

### 8.1 Prerequisites to re-measure

1. **An unfiltered network vantage point.** Up to 25 venues (12 proven + 13 ambiguous) may
   pass from a clean egress path. This is the single highest-leverage action.
2. **CCXT 4.3.92 → 4.5.85**, through the repository's dependency-triage process with a
   full regression run. Recovers okx and gemini; okx's long-standing `load_markets`
   breakage is fixed, which also unblocks OKX precision data.
3. Re-run `scripts/verify_exchange_connectivity.py` and re-derive the launch list.

---

## 9. Phase 7 — Tests, and ownership

### 9.1 Test results, actual

| Suite | Result |
|---|---|
| `tests/test_exchange_capability_registry.py` | **22 passed** |
| `tests/exchange_sandbox/` (gate OFF — default lane) | **27 skipped, 0 failed** |
| `tests/exchange_sandbox/` (gate ON) | 8 passed, 3 failed *(reproductions, by design)*, 16 skipped |
| Global collection | 14,283 tests collected, no errors |

The 22 new tests are credential-free and network-free. They cover the ladder's
non-inference rule, fail-closed trading authorization, the bitget safety block,
duplicate/alias/invalid ids, tri-state capability flags, the declared-vs-verified
boundary, and that offline inventory makes no network claim. No assertion was weakened and
no failing test was skipped to obtain a green result.

### 9.2 Files created — all new

```
backend_app/core/exchange_capability_registry.py     canonical registry, 8 states
backend_app/routers/exchange_capabilities.py         truthful capability API
scripts/verify_exchange_connectivity.py              public connectivity runner
tests/test_exchange_capability_registry.py           22 contract tests
staging/probe_ccxt_universe.py                       inventory enumeration
staging/probe_ccxt_upgrade_delta.py                  upgrade impact, isolated venv
staging/probe_capability_api.py                      API truthfulness checks
staging/show_connectivity_failures.py                failure triage
staging/tally_connectivity.py                        authoritative merged tally
staging/ccxt_universe.json                           inventory artifact
staging/exchange_connectivity_results.json           per-venue evidence
staging/connectivity_tally.json                      tally artifact
staging/ccxt_upgrade_delta.json                      upgrade artifact
staging/p10f_*.txt                                   run logs
staging/EXCHANGE_CONNECTIVITY_LAUNCH_REPORT.md       this report
```

### 9.3 Ownership boundaries respected

| Not to be edited | Edited? |
|---|---|
| `routers/exchange.py` (another workstream) | **No** |
| `exchange_executor.py`, `exchange_certification.py`, `routers/support.py` | **No** |
| `requirements-base.txt` / the project `.venv` | **No** — the upgrade was measured in a throwaway venv |
| `pytest.ini`, `tests/conftest.py` | **No** |
| Tenant isolation, quotas, ledger, migrations, shared audit artifacts | **No** |
| TEST-05 and retry/idempotency test files | **No** |

The 234 in-flight paths from other workstreams were preserved untouched.

---

## 10. Completion criteria

| Criterion | Status |
|---|---|
| Inventory identifies 100–110 eligible distinct candidates, **or** documents a genuine shortfall | **Met** — 103 eligible; shortfall documented with arithmetic (§5.3) |
| Claimed connectivity count backed by per-venue evidence | **Met** — 72, each with method, timestamp, detail in the artifact |
| Public connectivity and market-data capabilities reported accurately | **Met** — 72 / 69, tri-state flags, egress attribution |
| Execution capability and certification separate from connectivity | **Met** — 8-rung ladder, no inference, enforced by tests |
| Known unsafe sandbox routing remains blocked | **Met** — bitget removed; routing is 6 |
| Live trading remains OFF | **Met** — 0 authorized; unreachable from this module |
| Existing unrelated work preserved | **Met** — §9.3 |
| No production-readiness claim without evidence | **Met** — target reported as NOT met |

**Blockers to a 100-venue connectivity claim**, in order of leverage:

1. **Network egress filtering at the verification vantage point** — proven by DNS sinkhole
   and TLS interception. Caps the measurable number regardless of code.
2. **CCXT 4.3.92 is ~190 patch releases stale** — costs okx and gemini today.
3. **The ceiling is 99 < 100 even after both are fixed.** The target needs restating; §8.

**Blockers to any trading claim** are unchanged from the companion report: no sandbox
credential exists, so `SANDBOX_VERIFIED` and `TRADING_CERTIFIED` are 0 and
`TRADING PAPER` stays NOT CLASSIFIED.

---

## 11. For the coordinator

1. **Choose a launch metric** from §8. The literal "100–110 verified" is not achievable on
   CCXT; A or B both are, with different copy.
2. **Approve a CCXT bump to 4.5.85** through dependency triage. Recovers 2 venues and fixes
   the OKX `load_markets` breakage that has been documented in-tree for several phases.
3. **Provide an unfiltered network path** for the verifier, or accept that the measured
   number understates reality by up to 25 venues.
4. **Confirm bitget's removal** from `SUPPORTED_EXCHANGES` in `exchange_executor.py`. The
   registry blocks it; the executor's own list still contains it, and that list is shared
   code I did not edit.
5. **Assign owners** for EXCH-02 and EXCH-04 through EXCH-12.
6. **Approve correcting** `connection_engine.py`'s comment about the OKX fix, and the stale
   ENV-01 disposition in `TRADING_SAFETY_AUDIT.md`.
