# PHASE 11B-C — ORDER INTEGRITY AND SANDBOX READINESS

**Date:** 2026-10-10
**Live trading:** OFF. WebSocket listener **not started**. No venue contacted, no
credential, no order, no CCXT upgrade, no infrastructure, nothing committed.

---

## 1. Baseline and ownership

| | |
|---|---|
| Branch / HEAD | `main` @ `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Staged / unstaged / total | **22 / 100 / 252** |
| CCXT pin and installed | `ccxt[async]==4.3.92` / **4.3.92** — match |
| Reset / stash / clean / revert / commit | **none** |

### 1.1 Concurrent work found, and how it was separated

Two files in Stage 2's scope were **unmodified** at the Phase 11B baseline and are now
modified by other workstreams. Overlap was checked by hunk range **before** editing:

| File | Concurrent change | Hunks | Overlaps my target? |
|---|---|---|---|
| `exchange_websocket_listener.py` | +36 / −13 | one, at **504–540** (`_fallback_poll`) | **No** — my targets are `_handle_fill_event` (~297) and `_update_order_in_db` (~362) |
| `transactional_execution_manager.py` | +67 / −2 | **1017**, **1059–1085** | **No** — `handle_fill` is ~1146; no `handle_fill`/`fill_data`/`fill_id` lines in their diff |

Also now modified by others, and **untouched here**: `exchange_certification.py` (+16/−4,
the EXCH-04 fix), `routers/exchange.py`, `connection_engine.py`, `exchange_executor.py`.
Phase 11A added four `staging/PHASE11A_*` artifacts — **not edited**, per instruction.

After my change the listener reads +235/−33, so **+199/−20 is attributable to this
workstream**, and the concurrent hunk survives intact (shifted 504 → 683 by insertions
above it).

---

## 2. Findings confirmed versus unconfirmed

### 2.1 CONFIRMED — fill quantity semantics

**The question Stage 2 requires answering: cumulative or incremental?** Established two
independent ways, offline.

**Source evidence** — ccxt 4.3.92 `Exchange.safe_order` *derives* the order-level figure
by summing the trade-level ones:

```python
filled = '0'
for i in range(len(trades)):
    tradeAmount = self.safe_string(trades[i], 'amount')
    if parseFilled and (tradeAmount is not None):
        filled = Precise.string_add(filled, tradeAmount)
```

**Demonstration** — three trades of 1.0 / 1.0 / 0.5 parse to `filled == 2.5`,
`average == 180.0`, `remaining == 0.5`.

```
trade['amount']  -> INCREMENTAL (that trade only, own `id`)
order['filled']  -> CUMULATIVE  (running total)
```

Evidence: `staging/probe_p11bc_fill_semantics.py` →
[`p11bc_fill_semantics.txt`](p11bc_fill_semantics.txt)

*Method note, because it affected the conclusion:* my first fixture produced `filled=0.0`
and the probe correctly printed **"NOT ESTABLISHED — do NOT modify behaviour."** The cause
was mine: ccxt's `tradesAreParsed` gate requires both `info` and `id` on the first trade,
so my unified-shaped trades were re-parsed as Binance-native and yielded nothing. Fixed
the fixture; the demonstration then agreed with the source. **I did not proceed on the
source reading alone while my own probe said otherwise.**

### 2.2 CONFIRMED — five defects in the fill path

| # | Defect | Consequence |
|---|---|---|
| 1 | No-trades fallback synthesised a fill from the **cumulative** `filled` under the **order's** id | A second such event re-books everything already booked; dedup on trade id cannot distinguish a new partial fill from a replay |
| 2 | `trade.get("id", "unknown")` | Every id-less fill booked under the literal `"unknown"` — distinct fills collide on one identity |
| 3 | `price` defaulted to `0` | A real quantity booked at a price of zero |
| 4 | `filled_size = str(event.filled) if event.filled else "0"` | An **absent** figure read as zero, **erasing** a recorded cumulative fill. Same for `avg_price` |
| 5 | No monotonicity guard | A stale out-of-order event could **shrink** a cumulative fill |

### 2.3 CONFIRMED — DUP-01: our client order id does not reach two venues

Established by reading ccxt's per-venue source. Evidence:
`staging/probe_p11bc_client_order_id.py` →
[`p11bc_client_order_id.txt`](p11bc_client_order_id.txt) / `.json`, which records the
matched source lines so the verdict is auditable.

| Venue | Native field | Our `execution_id` arrives? |
|---|---|---|
| binance | `newClientOrderId` | **Yes** |
| bybit | `orderLinkId` | **Yes** |
| okx | `clOrdId` | **Yes** |
| kucoin | `clientOid` | **Yes** |
| **coinbase** | `client_order_id` | **NO — overwritten** |
| **kraken** | `userref` | **NO — not handled** |

Coinbase builds its own: `'client_order_id': id + '-' + self.uuid()`, discarding ours.
Kraken mentions neither `clientOrderId` nor `userref` in any `create*`/`edit*` method, so
our parameter is passed through verbatim and the venue does not read it.

**Consequence:** on coinbase and kraken a retry is a **brand-new order** at the venue.
Nothing there will refuse it. Both are on the six-venue routing allowlist.

*Method note:* my first probe reported **binance** as untranslated. That was a false
negative — binance assigns through a *variable* key
(`request[clientOrderIdRequest] = clientOrderId`), which my literal-subscript regex
missed. I verified binance directly before reporting, corrected the detection, and then
re-verified coinbase by hand when it flipped. **Reporting binance as broken would have
been a serious error.**

**Bonus finding:** binance *invents* a client order id when none is supplied
(`brokerId + self.uuid22()`). A wire-level observer would see an id present and wrongly
conclude the order was protected. It is fresh per attempt, so it provides no idempotency.

### 2.4 CONFIRMED — OKX `load_markets` still broken

Reproduced offline, deterministically, with no venue contact: `okx.parse_market` on the
documented pre-open SPOT shape (`baseCcy`/`quoteCcy` empty) raises

```
TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'
```

`safe_currency_code("")` yields `None`, then `parse_market` computes `base + '/' + quote`.
`load_markets()` iterates every instrument, so one such row fails the whole load and
`ExchangeNormalizer` has no precision, tick-size or minimum data for OKX. Corroborated by
the independent Phase 10F live measurement (`LIBRARY_DEFECT`).

### 2.5 UNCONFIRMED — and deliberately left so

| Item | Why it cannot be confirmed here |
|---|---|
| Whether any venue **actually** echoes our id back and refuses a second order carrying it | Venue behaviour. Only a real sandbox order settles it — **scenario 8**. Not claimed |
| Whether real venues report fills as `trades[]` or cumulative-only in practice | Needs a live sandbox stream. The handler is now correct for **both** shapes; which arrives is unverified |
| Whether the persistence layer actually deduplicates on `fill_id` | `TransactionalExecutionManager.handle_fill` is substituted in the tests; its real uniqueness constraint is owned by the ledger |
| Whether only pre-open instruments break OKX parsing | Would need the live OKX instrument list — a venue call, forbidden here |

---

## 3. Files changed, and why

| File | Change | Why |
|---|---|---|
| `backend_app/backend/exchange_websocket_listener.py` | **+199 / −20** | The two methods Stage 2 names. Scope limited to them plus two imports (`hashlib`, `InvalidOperation`) |
| `tests/test_fill01_partial_fill_accounting.py` | **new**, 27 tests | Stage 2's six required cases |
| `tests/test_dup01_client_order_id_mapping.py` | **new**, 12 tests | Stage 3 offline mapping contract |
| `staging/probe_p11bc_*.py`, `p11bc_*.txt/.json` | new | Evidence artifacts |

### 3.1 What the fix does

**`_handle_fill_event`**
- The ambiguous cumulative-only event now books **no fill** and logs a reconciliation
  warning. It is **refused, not converted**: turning a cumulative figure into a delta
  needs to know what is already booked, and that is not in the event. Reading it back
  here would be a read-then-write race against the very events this handler processes
  concurrently. The cumulative total is still persisted by `_update_order_in_db`, which is
  where a cumulative number belongs.
- A fill with no usable amount **or price** is refused rather than booked at zero.
- `_stable_trade_id` prefers the venue's own id; when absent it derives a deterministic
  digest over `(order_id, timestamp, amount, price)` — stable across replays, distinct
  across fills. It returns `None` rather than inventing an identity, and the caller then
  refuses. **The order id is never used as a trade id** (asserted directly).
- One unusable trade no longer discards the usable ones in the same event.

**`_update_order_in_db`**
- Cumulative figures only ever move **forward**; a stale smaller total is ignored and
  logged.
- An **absent** figure no longer overwrites a recorded one. Absent and zero are different
  facts; a genuine zero is still honoured.
- `remaining` and `average` move **with** `filled` or not at all, so the row cannot end up
  mixing a new total with a stale remainder.
- `status` is **always** applied, even when quantities are stale — otherwise an order
  cancelled at the venue would stay `open` locally purely because its fill total had not
  advanced.

Transaction boundaries, the fee/accounting payload shape, and `handle_fill`'s contract are
unchanged. The listener is **not** started.

### 3.2 Honest limitation of the trade-id derivation

Two fills of identical size at an identical price within the same millisecond produce the
same digest and collapse into one. That is stated in the code and tested. Collapsing is
the safe direction: under-booking is detectable by reconciliation against the venue,
whereas double-booking inflates a position with no record that it happened.

---

## 4. Before / after test results

All offline. No venue, no credential, no order.

| Run | Command | Exit | Result |
|---|---|---|---|
| FILL-01 focused | `pytest tests/test_fill01_partial_fill_accounting.py -q --timeout=120` | `0` | **27 passed**, 0 failed, 0 skipped |
| DUP-01 focused | `pytest tests/test_dup01_client_order_id_mapping.py -q --timeout=120` | `0` | **12 passed**, 0 failed, 0 skipped |
| Regression | 9 suites, below, `--timeout=240` | `0` | **182 passed**, 0 failed, 0 skipped, 0 errors |

```powershell
python -m pytest tests/test_fill01_partial_fill_accounting.py `
  tests/test_dup01_client_order_id_mapping.py tests/test_exchange_websocket.py `
  tests/test_execution_paths_that_claim_an_exchange.py `
  tests/test_order_normalization_and_venue_idempotency.py `
  tests/test_idempotent_submission_fails_closed.py `
  tests/test_exch01_sandbox_endpoint_verification.py `
  tests/test_exch01_bitget_routing_is_blocked.py `
  tests/test_trade04_live_order_gate_at_venue_boundary.py `
  -q --no-header --tb=short --timeout=240
```

**No pre-existing failure was inherited in these suites, and none was introduced.** No
assertion was weakened and nothing was skipped to obtain a pass.

### 4.1 Evidence categories, kept separate

| Category | What exists |
|---|---|
| **Offline unit evidence** | 27 FILL-01 + 12 DUP-01 + 143 regression. The handler logic and the ccxt source contract |
| **Mock exchange evidence** | The substituted `handle_fill` / DB session in the FILL-01 tests. Proves the handler's output, **not** the ledger's dedup |
| **Real venue sandbox evidence** | **NONE.** 0 of 12 scenarios executed. No venue contacted in this workstream |

---

## 5. Remaining venue-specific blockers

| Blocker | Venues | Status |
|---|---|---|
| No sandbox credential | binance, bybit, okx | **BLOCKED** — unchanged |
| No reachable Redis (kill switch fails closed, refusing all orders) | all | **BLOCKED** — unchanged |
| `load_markets` broken on pinned CCXT | **okx** | **BLOCKED** — §6 |
| Our client order id never reaches the venue | **coinbase, kraken** | **DUP-01, reported** — §7 |
| Scenario 8 unexecuted, so duplicate refusal unverified | binance, bybit, okx | **BLOCKED** on credential |
| Scenario 5b not inducible on a thin sandbox book | all | Needs crossable depth or a second funded account |

---

## 6. Does the OKX issue require an authorized dependency change?

**Yes. It cannot be safely fixed at application level.**

Three candidate guards were considered and all are rejected on the brief's own terms:

1. **Override `okx.parse_market`** to return `None` for malformed instruments — patches a
   pinned dependency's internals at runtime, and `parse_markets` is not contracted to
   accept `None`.
2. **Pre-filter in `fetch_markets`** — also an override of pinned-dependency internals.
3. **Skip instruments that fail to parse** — this is exactly the forbidden "silently skip
   arbitrary instruments". Establishing that *only* non-tradable pre-open instruments fail
   would require the live OKX instrument list, i.e. a venue call, also forbidden.

And inventing fallback precision or minimum values is forbidden — correctly, since
`place_order` enforces `BELOW_PRECISION` / `BELOW_MIN_QUANTITY` / `BELOW_MIN_NOTIONAL`
from exactly that data, so a fabricated figure would produce venue rejections or
oversized orders.

### Minimal upgrade proposal

| | |
|---|---|
| Change | `requirements-base.txt:95` — `ccxt[async]==4.3.92` → `4.5.85` |
| Measured effect | OKX loads **4,501 markets**; also recovers `gemini` (`staging/ccxt_upgrade_delta.json`) |
| Blast radius | **Large** — every venue path |
| Required compatibility tests | ① re-run `staging/probe_exch01_sandbox_endpoints.py` and update `SANDBOX_MECHANISMS` if any mechanism changed; ② re-run `staging/probe_p11bc_client_order_id.py` — **DUP-01's verdicts are version-specific** and a venue's mapping could change in either direction; ③ `tests/test_exch01_sandbox_endpoint_verification.py`; ④ `tests/test_dup01_client_order_id_mapping.py`; ⑤ `tests/exchange_sandbox/test_sandbox_routing_contract.py`; ⑥ `scripts/verify_exchange_connectivity.py`; ⑦ the full regression suite |
| Note | The upgrade also corrects a stale in-tree claim: `connection_engine.py:196-250` says the OKX fix is absent from 4.4.1 and 4.4.50. It **is present** in 4.5.85 |
| Remaining blocker after upgrade | Credential and Redis. OKX would still not be certified |

**OKX sandbox execution remains BLOCKED.**

---

## 7. Blocker reached — DUP-01 requires separate authorization

Stage 3's rule is: *"If a venue's duplicate-ID behavior is unknown, fail closed and
document the unresolved venue-specific requirement."*

For coinbase and kraken the behaviour is not unknown — it is **known to be absent**. But
the only correct fail-closed response is to stop routing orders to a venue that cannot
enforce client-order-id uniqueness, and that would **change routing allowlist behaviour**,
which this workstream is explicitly forbidden from doing.

**So I stopped and am reporting it**, per the boundary rule. Options for the owner, smallest first:

1. **Document and accept**, on the basis that live trading is OFF and no venue is
   authorized — the gap is real but currently unreachable. *(Status quo; costs nothing,
   defers the risk.)*
2. **Refuse retries for venues without venue-side idempotency** — narrower than removing
   them from routing: the venue stays routable for first submissions, and only the
   retry/ambiguous-recovery path refuses. Needs a change in `place_order` or the recovery
   path, both shared code.
3. **Remove coinbase and kraken from the routing allowlist** until mapping exists. Largest
   blast radius; changes the approved six.

I recommend **(2)**, because it targets precisely the unsafe action rather than the venue,
and `OrderWatchdog` already never resubmits — so the change would mostly be making an
existing conservative behaviour explicit and enforced. It still needs authorization and
its own focused tests.

---

## 8. Recommended next action, smallest safe scope

1. **Provision an isolated test Redis.** Cheapest, unblocks the most, and no order can be
   placed without it.
2. **Authorize the DUP-01 decision** (§7). It is a money-path gap on two allowlisted venues.
3. **Then** obtain binance + bybit testnet credentials and run **scenario 8 first**.
4. **Separately** authorize the CCXT upgrade for OKX, with the seven compatibility tests
   in §6.

Do **not** start the WebSocket listener yet: FILL-01 is fixed, but EXCH-12's four
reconnect defects (fixed 1 s retry, unused `symbols` argument, task exits permanently on a
pool failure, `start_listening` never sets `_running`) and its unauthenticated
`watch_orders` call are untouched and out of scope here.

---

## 9. Assessment

**What is now safer.** The fill path can no longer double-book a position. The specific
mechanism — booking a cumulative quantity through the incremental path under a
non-unique identity — is removed, and the ambiguous event that caused it is refused
rather than guessed at. Fills can no longer be booked at a price of zero, cannot collide
on the literal identity `"unknown"`, cannot be erased by an event that simply omits a
figure, and cannot be shrunk by a stale out-of-order event. Replay produces identical
identities, which is the precondition the ledger's dedup needs. 39 new offline tests, 182
passing in regression.

**What remains unverified.** Everything about actual venue behaviour. No venue was
contacted; 0 of 12 scenarios executed. Whether real venues deliver `trades[]` or
cumulative-only events is unknown — the handler is now correct for both, which is the
point. Whether any venue echoes our client order id back and refuses a duplicate is
unknown, and **scenario 8 is not satisfied by anything in this workstream.** Whether the
ledger deduplicates on `fill_id` is a separate concern with a separate owner.

**What requires separate authorization.** The CCXT upgrade for OKX (§6). The DUP-01
decision for coinbase and kraken (§7). An isolated Redis and sandbox credentials. Starting
the WebSocket listener, which needs EXCH-12 fixed first.

```
TRADING LIVE — OFF / NOT CERTIFIED
```

No venue is certified. No paper-trading or live-trading GO decision follows from this
workstream. The shared launch-gate register and readiness scorecard were not edited.
