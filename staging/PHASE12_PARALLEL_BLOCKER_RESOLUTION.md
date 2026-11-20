# Phase 12 — Parallel Blocker Resolution, Implementation and Verification

**0 source files modified · 0 test files modified · 0 commits · 0 pushes · 0 staged entries.**
**15 read-only evidence files written, plus this report — all new, all under `staging/`.**

Every implementation item in this phase (D7-1, W-6, W-3, D6-A, D6-B) remains **unauthorized**,
so no patch was applied. All work that could be carried to completion without a grant was
carried to completion.

Precisely: **grants on record covering any Phase 12 item = 0.** Grants *do* exist elsewhere in
the programme — `AP-W1D-3` and `D1`–`D4` are GRANTED for the drift-classification work — but
`AP-W1D-3` states *"No other files are authorized"*, and no Phase 12 target falls inside its
scope. §2.1 corrects the looser "Total granted: 0" phrasing earlier passes used, including
mine.

**Net new this pass, in order of value:**

1. **The repository baseline changed under the phase.** The owner committed and pushed
   `ecd23a9e` mid-phase, absorbing the 22 previously-staged entries. Drift was detected,
   scope-expanding work was stopped, the difference was investigated read-only, and it is
   **benign** — nothing was lost or overwritten. All prior preservation invariants are now
   obsolete and are formally retired in §1.
2. **W-6 moved from argued to MEASURED.** A probe exercised the live classification ladder:
   **3 of 8 cases misclassify**. Two of the three are new findings. One prior escalation of my
   own is **corrected downward** — the `retryable` inversion is real but **latent**, because
   no production control flow reads the flag (§5.4).
3. **The D6-C evidence request was itself defective and would have returned an error, not
   evidence.** Two independent defects, both now measured: a type mismatch that makes the
   query fail to plan, and the count-semantics defect carried forward since D8. Corrected
   query in §7.3, with the defect demonstrated on temp tables.
4. **TENANT-01 measured: executable `FORCE ROW LEVEL SECURITY` statements = 0**, against 108
   executable `ENABLE` statements. `tenant_db_context.py` is fully implemented and has **zero
   consumers**. §7.1 records why enabling FORCE before wiring it would be harmful.
5. **184 tests executed across the execution core at the new baseline: 179 passed, 5 skipped,
   0 failed.** The 5 skips are the named real-Redis gap and are not reported as passes.

**Live trading OFF / NOT CERTIFIED. Paper track 53%, unrevised this pass.**
**W-1b · W-3 · W-4/W-5 · W-6 · TENANT-01 · QUOTA-01 all remain OPEN.**

State vocabulary used strictly below: **OBSERVED** (read from source) · **MEASURED** (a probe
executed and produced a number this pass) · **TESTED** (a test executed this pass) ·
**APPROVED** (an authoritative grant exists) · **IMPLEMENTED** (code changed) ·
**DEPLOYED** (running in a deployed environment).

---

## 1. Baseline — a mid-phase change of baseline, investigated before anything was touched

### 1.1 What happened

Phase 12 opened its recon against the D11 baseline and found it gone. Per the standing
instruction, scope-expanding work stopped and the difference was investigated read-only
before any file was touched.

| Property | D11 baseline | **Phase 12, now** | Status |
|---|---|---|---|
| `HEAD` | `074b772ab16cece6…` | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** | **CHANGED** |
| Branch | `main` | `main` | same |
| `HEAD...origin/main` | `0 0` | **`0 / 0`** (ahead / behind) | pushed |
| Staged entries | 22, digest `2e994018…7152` | **0** | **absorbed by the commit** |
| Working-tree entries | 262 | **16 → 39 during the phase** | absorbed by the commit, then grown by concurrent work (§2.3) |
| **Files differing from `HEAD`** | — | **2, and only 2, throughout** | **neither one this phase's** |
| Worktrees · unmerged paths | 1 · 0 | 1 · 0 | same |
| `074b772a` reachable from `HEAD` | — | **True** | **nothing discarded** |

The working-tree count is a moving figure and is **not** a preservation invariant: it rose from
16 to 39 during the phase as other sessions wrote their own artifacts (§2.3) and as this phase
added its 16 files. The stable invariant is the row above it — **exactly 2 files differ from
`HEAD` at entry and at exit, and neither belongs to this phase** (§10.2).

The commit is `ecd23a9e` "launch phase 1", 3,723 files, `Narendra2212 <nt3441449@gmail.com>`,
2026-10-11 15:13:47 +0530.

### 1.2 Why this is benign, and what was checked to establish that

- `git merge-base --is-ancestor 074b772a HEAD` returns **true**. The old baseline is an
  ancestor, so the commit is additive history, not a rewrite. No reset, rebase or force-push
  occurred.
- The 22 staged entries were not discarded — they were **committed**. The D5 watchdog repair
  and the D11 bind probe are now tracked rather than pending.
- All 16 files this programme has touched or depended upon are **clean at `HEAD`** (§10.2).

No destructive command was run at any point in this phase. No stash, no revert, no checkout
over a modified file, no staging command.

### 1.3 Retired invariants — do not reinstate

The following are **obsolete** and must not be re-asserted by a later phase:

- `HEAD = 074b772a…`
- *22 staged entries*
- *staged digest `2e994018…7152`*
- *262 working-tree entries*

The current invariant is in §10.

### 1.4 The commit is NOT authorization

The commit was authored by the repository owner and includes this programme's prior work.
That is **not** a grant, and it is not read as one. Authorization is not inferred from
authorship, from inclusion in a commit, from passing tests, from a report title, or from
silence. The commit's own contents confirm the absence: the committed copy of
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` still records *"no authorization arrived …
Grants on record for any A23 item: 0"* and still carries `☐ GRANTED ☐ REFUSED` with **both
boxes unchecked**.

### 1.5 Environment, read rather than assumed

`pytest.ini`: `testpaths = tests`, `addopts = -v --tb=short`,
`pythonpath = . aerora_quant_backend_updated_final1 backend`, `norecursedirs` excludes
`tests/perf`. Interpreter `.venv\Scripts\python.exe` 3.12.10, pytest 9.1.1, ccxt 4.3.92
(pinned and installed). No `pyproject.toml`, `setup.cfg`, `.flake8`, `mypy.ini`, `tox.ini`
or `.pre-commit-config.yaml` exists. `.github/workflows/01-pr-check.yml:43-47` — **flake8 is
the blocking check**; `black` and `isort` are `continue-on-error: true` and have never passed.

PostgreSQL test cluster **UP**: `python staging/bootstrap_pg.py verify` →
`PROVISIONED public_tables=89 data_directory=…/staging/pgdata_r1`, DSN
`127.0.0.1:51805/postgres`.

---

## 2. Decision intake — zero grants for any Phase 12 item

### 2.1 A correction to how earlier passes, including mine, stated this

Earlier phases summarized the authorization position as *"Total granted: 0"*, quoting
`PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md` §7.1. **That phrasing is too broad and this
report does not repeat it.** Grants *do* exist in this programme. They are recorded at primary
source in `staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md:19-26`:

| Decision | State | Issued by |
|---|---|---|
| `AP-W1D-3` — authorize the four-file scope in W1D-11 §5.1. **"No other files are authorized."** | **GRANTED** | Code owner |
| `D1` ambiguous collisions are hard failures with distinct labels | **GRANTED** | Code owner |
| `D2` reordered identical rows classify as unchanged | **GRANTED** | Code owner |
| `D3` Option 1 — one shared classifier, three renderers, two reviewable changes | **GRANTED** | Code owner |
| `D4` classifier goes in `scripts/verify_provisioning.py` | **GRANTED** | Code owner |
| `D5` preserve `check_drift()`'s three-tier severity routing exactly | **CONDITIONALLY GRANTED — condition NOT satisfied** (production-audit stakeholder sign-off, never given) | Code owner |

**Tally at source: 5 GRANTED, 1 CONDITIONALLY GRANTED, 0 REJECTED.**

The accurate statement is therefore narrower and stronger: **`AP-W1D-3` is explicitly
scope-limited — "No other files are authorized" — and no Phase 12 target falls inside it.**
That grant covers the drift-classification work in `scripts/verify_provisioning.py`, which is
why `tests/test_drift_classification.py` (+143/−0) sits in the working tree as another
workstream's authorized output (§10.2). It confers nothing on the execution core, the vault,
the classifier ladder, the routers, Redis or RLS.

### 2.2 Phase 12 items, each checked individually

A sweep of every artifact created since D10 found **no decision on any Phase 12 item**.

| Artifact | Bearing on a Phase 12 item |
|---|---|
| `PHASE11B_A28_LEDGER_GATE_DELTA.md` | none — records "D-A27-1 **NOT AUTHORIZED**" |
| `PHASE11B_A28B_CI_EVIDENCE_AND_LEDGER_ROW_VERIFICATION.md` | none |
| `PHASE11B_A29_TEST6_AND_DECISION_REQUESTS.md` | none — contains *requests* |
| `PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | none — both decision boxes unchecked |
| `PHASE11B_A23E12_PARALLEL_BLOCKER_RESOLUTION.md` **(new, §2.3)** | none — findings only |
| `PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md` **(new, §2.3)** | none — "no authorization arrived" |

`PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md:43` still holds for the A23/Phase-12 item set:
every `GRANTED` token in those files is either the string `NOT GRANTED` or a *request*.

**Grants on record for any Phase 12 implementation item: 0.** No channel exists in this
workspace through which to obtain one. This is the binding constraint on the phase and the
reason five of six workstreams end in a specification rather than a patch.

### 2.3 Concurrent workstream output that appeared during this phase

Four artifacts appeared in `staging/` mid-phase that this phase did not create. They were read
read-only and **left exactly as found** — not edited, moved, absorbed or overwritten.

| Artifact | What it is | Effect on this report |
|---|---|---|
| `PHASE11B_A23E14_REGISTER_AUTHORIZATION_AND_BLOCKER_REDUCTION.md` | A fifth independent session's pass over the same ground | **Corroborates.** Records the same commit event (`074b772a` → `ecd23a9e`, staged 22 → 0) and the same conclusion: *"no authorization arrived"*, **"Granted 0 · Conditionally granted 0 · Refused 0 · Unresolved 8"**, with D7-1, W-6, D6-C and W-3/W-4/W-5/D7-2 all **ABSENT / NOT GRANTED** |
| `PHASE11B_A23E12_PARALLEL_BLOCKER_RESOLUTION.md` | A different document from this one despite the similar name (A23E12 ≠ PHASE12) | **Corroborates, and sharpens D7-1** — see §4.4 |
| `p12e12_w6_probe_run.txt` | Another session's execution of **this phase's own probe script**, `staging/p12_w6_classifier_probe.py`, under system Python 3.12 rather than `.venv` | **Independent reproduction** — see §5.7 |
| `p11b_a23e16_baseline.py` | Another workstream's baseline script | Not executed by this phase. Out of scope. |

A23E14 independently notes it performed **0 writes before detecting the commit**, matching this
phase's own sequence. Two sessions detecting the same drift and stopping is the preservation
discipline working as intended.

---

## 3. Workstream A — D6-D, deployment evidence

**Status: BLOCKED, unchanged. No deployed environment is reachable from here.**

The question is whether `app_state.vault` is a real `APIKeyVault` or a `MockEngine` in each
deployed environment. It is answerable **only** from deployment evidence, for a measured
reason: `MockEngine.__getattr__` (`backend_app/core/state.py:51-55`) answers *any* attribute
access with a no-op, so `hasattr`-style introspection cannot distinguish a mock from the real
object. The type must be read from the boot log, not probed.

**The two emitters that decide it** (OBSERVED, re-verified at the new `HEAD`):

| Emitter | Location | Enclosing |
|---|---|---|
| `Failed to import APIKeyVault` | `core/state.py:63-64` | `safe_import` |
| `APIKeyVault initialization failed, using mock` | `core/state.py:108-109` | `_safe_init` |

**Why it matters on the live order path** (OBSERVED): `master_executor.py:127`
`self.vault = global_state.vault` → `:252` `load_decrypted_keys(...)` → `:255-257`
`keys["api_key"]`. If the vault is a mock, the subscript raises **`TypeError` on the live
order path**, not at boot.

**Evidence still required, per environment (staging, production):** the vault's concrete type,
plus whether each of the two emitters above fired at boot. Five fields. Request unchanged
from D11 §3.2 and still **NOT SUPPLIED**.

---

## 4. Workstream B — D7-1, the vault signature mismatch

**Status: specification machine-verified at the new baseline. Patch NOT applied — unauthorized.**

### 4.1 Re-verified at `HEAD = ecd23a9e` (MEASURED, `staging/p12_bindprobe.txt`)

Real signature, `backend_app/backend/api_key_vault.py:177`:

```python
def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict
```

An AST sweep of all call sites, each bound against that signature with
`inspect.signature(...).bind(...)`: **7 call sites · 4 bind · 3 raise `TypeError`.**

| Call site (AST call-node line) | `access_token=` literal | Result |
|---|---|---|
| `backend_app/routers/orders.py:789` | `:792` | **TypeError** |
| `backend_app/routers/orders.py:1095` | `:1098` | **TypeError** |
| `backend_app/routers/market.py:50` | `:53` | **TypeError** |
| `backend_app/routers/exchange.py:993` | — | binds |
| `backend_app/api_ws/ws_routes.py:814` | — | binds |
| `backend_app/backend/master_executor.py:252` | — | binds |
| `backend_app/backend/portfolio_cache_updater.py:160` | — | binds |

### 4.2 Why this defect survived every previous test pass

This is the load-bearing observation and it governs the test design.
`tests/test_market_none_keys_fix.py:13` declares its double as:

```python
def load_decrypted_keys(self, user_id, exchange_id, access_token=None):
```

**The double matches the broken calls; the real class does not.** Both tests in that file
passed again this pass (§9) — and they would pass with the defect present, because they never
touch the real signature. A third test double would reproduce the blindness, so the verified
specification uses `inspect.signature(...).bind(...)` plus AST extraction and **never
instantiates the vault**.

### 4.3 The patch, specified and ready

Delete the `access_token=user.get("access_token"),` argument at `orders.py:792`,
`orders.py:1098` and `market.py:53`. Three single-line deletions, no signature change, no new
parameter.

**Preserve** the guard at `market.py:56` — `if not keys or not isinstance(keys, dict):` — which
is the fix D3 landed and must not be disturbed.

**Lint impact:** flake8 baseline for `orders.py` + `market.py` is **224 pre-existing findings**
(130 × E501, 84 × W293, …). **None falls on a deletion line**, so the blocking CI check is
unaffected either way.

**Authorization: ABSENT.** No grant for these three lines exists in any authoritative record.

### 4.4 Runtime consequence per site — adopted from a concurrent workstream

`PHASE11B_A23E12_PARALLEL_BLOCKER_RESOLUTION.md:196-197` characterises the three sites more
precisely than any prior pass of mine, by checking what guards each call. Its finding is
adopted here with attribution, because it changes the priority of the three deletions:

| # | Site | Guarded? | Runtime consequence |
|---|---|---|---|
| 1 | `orders.py:789-793`, `get_open_orders` (`GET /api/orders/open`) | **NO** — first statement of the body | `TypeError` propagates to FastAPI → **unhandled 500 on every request** |
| 2 | `orders.py:1095-1099`, trade history | `except Exception` at `:1110` | logs WARNING, `return []` → **client is shown "no history"** |
| 3 | `market.py:50-54`, `_get_data_engine` | `except Exception` at `:67` | **silently falls back to an unauthenticated public `ConnectionEngine`** for `get_candles`, `get_orderbook`, `get_ticker`, `get_funding_rate` |

Site 3 is the one that matters most and it is the least visible. The guard at `market.py:56`
that D3 added is doing its job — it prevents a crash — but what it falls back *to* is an
unauthenticated public connection, so authenticated market data silently degrades rather than
failing. Site 1, by contrast, is a loud 500 that would be noticed immediately.

This does not change the patch (three line deletions, §4.3). It changes the argument for
authorizing it: site 2 shows customers wrong data and site 3 silently drops authentication,
neither of which surfaces as an error anywhere.

---

## 5. Workstream C — W-6, the error classifier: MEASURED

**Status: OBSERVED → MEASURED. 3 of 8 cases misclassify. Fix NOT applied — unauthorized.**

### 5.1 Method, and why it differs from every prior pass

Prior phases read the ladder and reasoned about it. This pass **executed** it.
`staging/p12_w6_classifier_probe.py` builds an instance with
`object.__new__(CCXTExchangeExecutor)` — the same pattern
`tests/test_trade04_live_order_gate_at_venue_boundary.py` uses — and feeds eight realistic
exception messages through the real `_handle_ccxt_error`.

### 5.2 Result (`staging/p12_w6_probe.txt`, EXIT=0)

| Case | Actual class | `error_code` | `retryable` | Verdict |
|---|---|---|---|---|
| genuine invalid symbol | `InvalidSymbolError` | `INVALID_SYMBOL` | False | ok |
| **TIMEOUT whose message carries the request URL** | **`InvalidSymbolError`** | **`INVALID_SYMBOL`** | **False** | **MISCLASSIFIED** → want `NetworkError` / True |
| TIMEOUT, no symbol text | `NetworkError` | `NETWORK_ERROR` | True | ok |
| **network failure carrying a market path** | **`InvalidSymbolError`** | **`INVALID_SYMBOL`** | **False** | **MISCLASSIFIED** → want `NetworkError` / True |
| genuine authentication failure | `AuthenticationError` | `AUTH_ERROR` | False | ok |
| **unrelated message containing `"key"`** | **`AuthenticationError`** | **`AUTH_ERROR`** | **False** | **MISCLASSIFIED** → want `ExchangeError` |
| insufficient funds | `InsufficientFundsError` | `INSUFFICIENT_FUNDS` | False | ok |
| rate limit | `RateLimitError` | `RATE_LIMIT` | True | ok |

**cases: 8 · misclassified: 3**

### 5.3 The cause, exactly located

`backend_app/backend/exchange_executor.py`, `def _handle_ccxt_error` (**span 1225-1257**).
An ordered substring ladder over `error_str = redacted.lower()`:

```
1240   if "insufficient" in error_str or "balance" in error_str:
1243   if "rate limit" in error_str or "too many requests" in error_str:
1246   if "symbol" in error_str or "market" in error_str:          <-- shadows
1249   if "network" in error_str or "timeout" in error_str or "connection" in error_str:
1252   if "authentication" in error_str or "apikey" in error_str or "key" in error_str:
1256   retryable = any(word in error_str for word in ["timeout","network","temporarily","busy"])
1257   return ExchangeError(redacted, retryable=retryable)
```

Two findings here are **new this pass**:

- **`"market"` at 1246 also shadows.** It is a common path segment at real venues — okx
  publishes `/api/v5/market/ticker`, bybit `/v5/market/*`. Any network failure whose message
  carries the request path is classified as an invalid symbol. The `"symbol"` half had been
  noted before; the `"market"` half had not.
- **`"key"` at 1252 is a three-character substring with no word boundary.** The probe's
  `"monkey patch failed"` matches on the `key` inside `monkey`. The consequence is the
  expensive one: a wholly unrelated failure is reported as `AUTH_ERROR`, which **sends an
  operator to rotate credentials** that were never the problem.

Error classes and their fixed codes: `exchange_executor.py:285-313`
(`InsufficientFundsError` 285-288, `RateLimitError` 291-295, `InvalidSymbolError` 298-301,
`NetworkError` 304-307, `AuthenticationError` 310-313).

### 5.4 Correction to a prior escalation of my own

D10 escalated W-6 to "a proven `retryable` inversion", which overstated the impact. The
inversion is proven; its **liveness is not**. Measured repo-wide this pass, every reader of
`.retryable` is:

| Reader | Nature |
|---|---|
| `exchange_executor.py:1687` `f"retryable={error.retryable}"` | **log f-string only**, inside `place_order` |
| `tests/exchange_sandbox/test_sandbox_order_lifecycle.py:587, :601` | tests — and 16/16 skip without credentials (measured in D11) |
| `tests/test_exch01_sandbox_endpoint_verification.py:123` | a different class (`SANDBOX_UNVERIFIED`) |

**No production control flow branches on `retryable` anywhere in the repository.** So the
accurate statement is: the flag is computed wrongly and logged wrongly, and the defect becomes
live the moment a retry policy is wired to it — which is precisely what W-3/W-4/W-5 would do.
It is a latent P1 that W-3 would promote to a live P0, not a live P0 today.

Telemetry severity is likewise **unaffected**: `exchange_telemetry.py:520` branches only on
`["INSUFFICIENT_FUNDS", "POSITION_LIMIT"]`, and all three misclassifications land in the same
`"medium"` bucket a correct classification would.

### 5.5 Blast radius, and why it is wider than previously recorded

`_handle_ccxt_error` has **five** call sites, not one. Earlier passes discussed only
`place_order`.

| Caller | Call line | Span |
|---|---|---|
| `connect` | 1367 | 1271-1369 |
| `place_order` | 1682 | 1400-1699 |
| `cancel_order` | 1719 | 1701-1730 |
| `get_order_status` | 1757 | 1732-1766 |
| `get_balance` | 1938 | 1918-1940 |

So the corrupted `error_code` reaches order placement, cancellation, status polling, balance
reads and connection establishment alike.

### 5.6 The fix, specified

Reorder so the specific precedes the generic, and bound the weak tokens:

1. Move the network test (currently 1249) **above** the symbol/market test (currently 1246).
   A transport failure is a transport failure regardless of which URL it was carrying.
2. Replace the bare `"key"` token at 1252 with word-boundary matching on
   `api key` / `apikey` / `api-key` / `invalid key` / `signature`, so `monkey` cannot match.
3. Leave the redaction at 1236 exactly where it is. It must continue to run **before** any
   classification or storage, per the STEP 6.10 docstring, and the D3 redaction guarantee
   depends on it.

**Not applied. Authorization: ABSENT.** Coverage for this ladder in the default lane is
**zero** (measured in D11 — the sandbox file skips 16/16 without credentials), so a fix would
have to ship with new tests; the probe in `staging/p12_w6_classifier_probe.py` is the
executable basis for them.

### 5.7 Independent reproduction of this measurement

`staging/p12e12_w6_probe_run.txt` — an artifact this phase did not create (§2.3) — is another
session's execution of **this phase's own probe script**, run under the system Python 3.12
rather than `.venv`. Its result table is **identical**: the same 8 cases, the same 3
misclassifications, the same `error_code` and `retryable` values, `cases: 8 misclassified: 3`.

That is worth recording for two reasons. The measurement is reproducible on a different
interpreter, so it is not an artefact of this environment. And it was obtained by a session
that reached the same conclusion independently — `PHASE11B_A23E12…:31` records W-6 as
*"CONFIRMED, with a correction to the previously reported trigger"*, which is the same
correction §5.3 makes about `"market"` and `"key"`.

Two independent sessions, two interpreters, one number. W-6's status as MEASURED does not rest
on a single run.

---

## 6. Workstream D — QUOTA-01, real-Redis verification

**Status: TESTED except for the real-Redis lane, which remains unreachable.**

### 6.1 Result at the new baseline (TESTED, §9)

All four `test_quota01_*.py` files ran. **No failures.** 5 tests skipped, all in the
real-Redis lane:

| Skipped test | File |
|---|---|
| `test_the_slot_lock_is_mutually_exclusive_against_real_redis` | `test_quota01_exchange_slot_is_atomic.py` |
| `test_the_guard_admits_exactly_the_limit_against_real_redis` | `test_quota01_exchange_slot_is_atomic.py` |
| `test_concurrent_reservations_are_atomic_against_real_redis` | `test_quota01_metered_reservation_is_hard.py` |
| `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis` | `test_quota01_metered_reservation_is_hard.py` |
| `test_production_zero_allowance_writes_no_meter_against_real_redis` | `test_quota01_metered_reservation_is_hard.py` |

### 6.2 The gate, as the gate itself reports it

Recorded verbatim from `tests/real_redis_gate.py` via `gate_summary()`
(`staging/p12_final_audit.txt`):

```
real-Redis gate: state=skip (AERORA_TEST_REDIS_URL=unset, remote_allowed=False)
```

> `AERORA_TEST_REDIS_URL` is not set. Real-Redis coverage is opt-in: the default suite runs on
> the in-process substrate, and the logic tests beside these cover it. Set
> `AERORA_TEST_REDIS_URL` to a DEDICATED test instance or database index to enable it.
> **A skip here is NOT a pass.**

**These 5 skips are not reported as passes.** The gate module is well-built and distinguishes
three states deliberately: opt-in unset → SKIP, opt-in set but unreachable → **FAIL**, opt-in
set and reachable → RUN. That design means a broken CI Redis cannot masquerade as green.

### 6.3 Why the lane was not enabled

Measured: `AERORA_TEST_REDIS_URL` unset · `redis-server` not on `PATH` · 0 matching services ·
**port 6379 CLOSED**. Starting a Redis server is an infrastructure change and is
**unauthorized**, so it was not done. No skip was converted into a pass.

### 6.4 One obsolete caveat retired

D11 §7.4 qualified its QUOTA-01 result by noting that `backend_app/routers/exchange.py` — the
single call site of `exchange_connection_slot_guard` (defined
`core/subscription_dependencies.py:1036`, called `routers/exchange.py:428`) — was *staged,
uncommitted work*, so the result rested on an unisolated tree. **That caveat is now obsolete:**
the file is committed at `ecd23a9e` and clean at `HEAD` (blob
`4b40973568edfa4dc24a8416819f872eaf18d54c`). The QUOTA-01 result no longer carries an
isolation qualifier.

---

## 7. Workstream E — TENANT-01 and D6-C, tenant isolation

### 7.1 RLS, measured with its scope stated — and a warning that must not be acted on prematurely

**A discrepancy in this programme's own numbers, resolved.** Earlier passes recorded
`ENABLE ROW LEVEL SECURITY = 83`. A recursive count this pass returned **120**. A count
without a stated scope is not evidence, so the measurement was redone per tree
(`staging/p12_rls_scope.py` → `p12_rls_scope.txt`). The repository carries several duplicate
source trees, which is the whole of the difference:

| Tree | `ENABLE` | `FORCE` |
|---|---|---|
| `backend_app/` | 61 | **1** |
| `migrations/` | 43 | 0 |
| `archived_migrations/` | 7 | 0 |
| `aerora_quant_platform/` | 5 | 0 |
| `rls_migration.sql/` | 4 | 0 |
| **TOTAL** (70 `*.sql` files, duplicates included) | **120** | **1** |

Of the 120 `ENABLE` occurrences, **108 are executable and 12 sit inside comments**. The two
canonical trees are `migrations/` (43) and `backend_app/migrations/` (61).

**The load-bearing figure is robust under every scope: executable
`FORCE ROW LEVEL SECURITY` statements = 0.** The single `FORCE` occurrence anywhere in the
repository is a **comment**, at `backend_app/migrations/026_fee_records_row_level_security.sql:75`:

```sql
-- `FORCE ROW LEVEL SECURITY` is set. So `ENABLE` closes the PostgREST surface — where the
```

This confirms D10's finding at the new baseline, and is independently corroborated by two
concurrent workstreams (§2.3): A23E14 §28 *"zero occurrences in any migration or Alembic
file; exists only as prose in reports"* and A23E12 §33 *"appears in **zero** SQL statements
repository-wide"*.

The earlier `83` is not reproducible at any scope measured here and should be considered
superseded by the table above rather than averaged with it.

`backend_app/core/tenant_db_context.py` is **fully implemented and correct in its details**:
it uses `set_config(name, value, true)` rather than `SET LOCAL` so it survives pgbouncer
`pool_mode=transaction`; it refuses to run when no transaction is open; it checks the dialect
rather than catching a failure; `TENANT_SETTING_NAME = "app.current_tenant_id"`.

It also has **zero consumers**. Nothing in `core/dependencies.py`, `main.py`, or any router
calls it. The only GUC write anywhere in the product is an inline
`text("SET LOCAL app.current_tenant_id = :tenant_id")` at
`transactional_execution_manager.py:225`.

> **RECORD THIS PROMINENTLY. `FORCE ROW LEVEL SECURITY` MUST NOT BE ENABLED BEFORE
> `tenant_db_context` IS WIRED.** The module's own docstring states the consequence: forcing
> RLS before the GUC is set on each connection *"converts reads on `orders`, `positions` and
> `fills` into silently empty result sets."* Silently empty — not an error. A well-intentioned
> hardening change that enables FORCE first would produce a product that looks healthy and
> shows every customer an empty portfolio.

The correct order is: wire `tenant_db_context` into the request/session lifecycle → prove the
GUC is set on every connection that reads a tenant-scoped table → *then* consider FORCE.

### 7.2 TENANT-01 remains OPEN

83 tables have RLS enabled, which the 149 passing isolation tests (§9) exercise. But enablement
without FORCE means a table owner or a `BYPASSRLS` role reads across tenants, and the
application-level GUC that the policies depend on is written in exactly one place that is not
the request path. **Status unchanged: OPEN.**

### 7.3 D6-C — the evidence request was itself defective

The query carried from D8 → D9 (Packet 2) → D10 → D11 has **two independent defects**. Both
were measured this pass against the real schema. Neither had been detected before, because
the query had never been run anywhere.

**Defect 1 — it does not plan. It would have returned an error, not evidence.**

Measured column types on the provisioned cluster (`staging/p12_d6c_types.txt`):

| Column | Type |
|---|---|
| `public.exchange_keys.user_id` | **uuid** |
| `public.exchange_keys.exchange_id` | text |
| `public.execution_records.tenant_id` | **uuid** |
| `public.execution_records.user_id` | uuid |

The query joins `k.user_id = e.tenant_id::text`, i.e. **`uuid = text`**:

```
ERROR  D9 Packet 2            UndefinedFunction: operator does not exist: uuid = text
```

The cast was written against the type *declared* in
`migrations/003_create_exchange_keys_table.sql:14` (`user_id TEXT NOT NULL`). The identity
hardening migration `migrations/hardening/998_bug049_identity_types.sql` converts those
columns to `uuid` (BUG-049: *"customer-identity columns are uuid and declare their owner"*).
The declared type and the deployed type differ, and the evidence request trusted the declared
one. **Had the DB owner run it, they would have replied with a syntax error.**

**Defect 2 — the count semantics, as flagged since D8 but never demonstrated.**
`exchange_keys` is `UNIQUE (user_id, exchange_id)`
(`003_create_exchange_keys_table.sql:23`), so a tenant holding keys on two venues yields two
join rows and is counted twice. Demonstrated on temp tables with no real data — fixture:
`tenant-A` holds keys on 2 venues, `tenant-B` holds none; truth is 2 / 1 / 1:

```
ORIGINAL  -> {'tenants_with_keys': 2, 'tenants_without_keys': 1}        <- 3 "tenants" out of 2
CORRECTED -> {'distinct_tenants': 2, 'tenants_with_keys': 1, 'tenants_without_keys': 1}
```

**The corrected query.** `EXISTS` fixes the counting; dropping the cast entirely fixes the
type error and keeps `idx_exchange_keys_user_id` usable (a `::text` cast on both sides also
plans, but defeats the index):

```sql
-- D6-C, corrected. Counts only: three integers, no identifiers.
-- Verified to plan and execute against the real schema (vacuous on the test cluster).
SELECT count(*) AS distinct_tenants,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM public.exchange_keys k
                          WHERE k.user_id = e.tenant_id)
       ) AS tenants_with_keys,
       count(*) FILTER (
           WHERE NOT EXISTS (SELECT 1 FROM public.exchange_keys k
                              WHERE k.user_id = e.tenant_id)
       ) AS tenants_without_keys
  FROM (SELECT DISTINCT tenant_id FROM public.execution_records) e;
```

**Caveat, carried forward and still load-bearing:** only **`tenants_without_keys = 0`** is the
load-bearing condition. It is **necessary but not sufficient** — see the claim-divergence half
below.

**Not run for its result, deliberately.** The only reachable database is the provisioned test
cluster, which has no real connected accounts; it returns `(0, 0, 0)`, which is vacuous and is
reported as vacuous, not as `tenants_without_keys = 0`. What was established here is that the
corrected query **plans and executes against the real schema**, so the DB owner receives a
query that runs.

### 7.4 The claim-divergence half of D6-C is unmeasurable from application tables

`core/dependencies.py:316`:

```python
tenant_id = payload.get("tenant_id") or payload.get("app_metadata", {}).get("tenant_id") or user_id
```

`exchange_keys.user_id` is written from `user["id"]` at `routers/exchange.py:429-431`. When the
JWT carries a `tenant_id` claim that differs from `user_id`, execution rows and credential rows
key on different values. **No query over application tables can detect this**, because the
divergence lives in the token, not the database. It needs auth/platform evidence. Unchanged.

**D6-C therefore still requires two separate owners:** the DB owner (the §7.3 query) and the
auth/platform owner (the claim-divergence count). Both **NOT SUPPLIED**.

---

## 8. Workstream F — W-3, W-4/W-5, D7-2 preparation

**Status: all prepared, none authorized, none implemented.**

| Item | Target | Prepared position |
|---|---|---|
| **W-3** | `order_watchdog.py:370`, `:712`, `event_router.py:135` | Coalesce, **never swap**, the venue order id. Migration `030_execution_venue_order_id.sql` has a postflight that **aborts if an FK is added to `venue_order_id`** — the column is a venue-side identifier, not a foreign key, and the postflight exists to stop someone making it one. |
| **W-4 before W-5** | `resolve_stale_execution` | There is **no exit rule from `UNKNOWN`**. The CAS is pinned to `upper(status::text) = 'EXECUTING'`, so an `UNKNOWN` row is unreachable by it. W-5 must not be attempted before W-4 defines the exit rule, or rows will accumulate in a terminal-by-accident state. |
| **D7-2** | `portfolio_cache_updater.py:151` | Imports `backend_app.backend.key_manager`, which **does not exist**. Reached only for registered users; the `ImportError` is swallowed at `:134`. The cache is therefore **silently nonfunctional while `/health/services` reports ACTIVE** — a monitoring falsehood, which is the part worth fixing first. |
| **A-6** | `.env.example` | The `ALERT_*` variable names have **no consumer** in the codebase. Either wire them or delete them; as they stand they imply alerting that does not exist. |

W-6 (§5) is a prerequisite for W-3 in substance: wiring a retry policy to `error.retryable`
while 3-in-8 classifications are wrong would convert a latent defect into a live one.

---

## 9. Test commands, outcomes, skips, limitations

### 9.1 Execution core, at the new baseline

```powershell
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging/pg_dsn.txt -Raw).Trim()
.venv\Scripts\python.exe -m pytest -p no:randomly -rs `
  tests/test_d5_watchdog_adjudication_repair.py `
  tests/test_trade07_stale_execution_recovery_postgres.py `
  tests/test_dup02_ambiguous_submission_is_not_retried.py `
  tests/regression/test_baseline_unchanged.py `
  tests/test_market_none_keys_fix.py `
  tests/test_quota01_enforcement_is_wired.py `
  tests/test_quota01_exchange_slot_is_atomic.py `
  tests/test_quota01_hard_enforcer_is_deprecated_in_code.py `
  tests/test_quota01_metered_reservation_is_hard.py
```

**Collected 184 · 179 passed · 5 skipped · 0 failed · EXIT=0 · 340.17s.**
Full log: `staging/p12_exec.txt`.

Notable individual results:

- `test_persist_is_durable_on_postgresql` **PASSED** — the D5 repair is verified on real
  PostgreSQL, not SQLite. This test skips without a cluster; it did not skip.
- `test_baseline_unchanged[live_order_path]` and `[credential_vault]` **PASSED** — the
  regression baselines, including the `live_order_path.json` re-recorded in D3, are intact at
  the new `HEAD`.
- All 5 skips are the real-Redis lane of §6.1. **Reported as a gap, not as passes.**
- `test_market_none_keys_fix.py` — both tests passed, **and would also pass with the D7-1
  defect present**, for the reason in §4.2. Their passing is not evidence about D7-1.

### 9.2 Tenant isolation and credential vault

```powershell
$env:AERORA_TEST_DATABASE_URL = (Get-Content staging/pg_dsn.txt -Raw).Trim()
.venv\Scripts\python.exe -m pytest `
  tests/test_tenant_db_context.py tests/test_tenant_db_context_postgres.py `
  tests/test_tenant01_cross_tenant_matrix_postgres.py `
  tests/test_tenant01_credential_vault_isolation.py `
  tests/test_tenant02_fee_records_rls_postgres.py `
  tests/test_cross_tenant_read_sweep_postgres.py `
  tests/test_tenant_isolation_rls_postgres.py `
  tests/test_tenant_referential_integrity_postgres.py `
  tests/test_exchange_executor_cache_is_per_tenant.py `
  tests/test_exchange_credential_log_redaction.py
```

**1 failed · 149 passed · 97.78s · EXIT=1.** Log: `staging/p12_tenant.txt`.

The single failure is **pre-existing and already dispositioned in D3 §5.7**. It is a stale
assertion, not a defect and not a leak:

```
test_exchange_credential_log_redaction.py::
  TestExchangeExecutorConnectFailureDoesNotLeakTheSecretInTheLog::
  test_a_connect_failure_whose_exception_embeds_the_secret_is_redacted

assert 'Failed to connect to binance' in
  'CRITICAL … exchange_executor.py:1337 EXCH-01: refused a sandbox connection to binance
   that could not be proven to have left production.'
```

The EXCH-01 sandbox-routing guard (landed in Phase 10 Workstream G/J) short-circuits before
the connect attempt the assertion expects. **No secret leaked** — the log line is the guard's
refusal. The test asserts on a message that the guard legitimately pre-empts.

### 9.3 Probes executed this pass

| Probe | Output | Result |
|---|---|---|
| `staging/p12_w6_classifier_probe.py` | `p12_w6_probe.txt` | 8 cases, **3 misclassified**, EXIT=0 |
| `staging/p11bd11_d71_bind_probe.py` | `p12_bindprobe.txt` | 7 call sites, 4 bind, **3 TypeError** |
| `staging/p12_w6_lines.py` | `p12_w6_lines.txt` | ladder span **1225-1257**, **5** callers, 1 log-only `retryable` reader |
| `staging/p12_d6c_sql_syntax_check.py` | `p12_d6c_sql.txt` | **both original forms fail to plan**; count defect demonstrated on temp tables |
| `staging/p12_d6c_types.py` | `p12_d6c_types.txt` | types measured (**both `uuid`**); 2 of 4 formulations plan |
| `staging/p12_rls_scope.py` | `p12_rls_scope.txt` | **executable FORCE RLS = 0**; ENABLE 120 total / 108 executable, per-tree breakdown |
| `staging/p12_final_audit.py` | `p12_final_audit.txt` | preservation audit, **0 drift**, 16/16 tracked files clean |

All 15 evidence files and the two test logs are new files under `staging/`. None overwrites a
prior report or evidence file; each prior-phase report named in this document was read, not
written. Two of the scripts above were corrected mid-phase after producing a wrong result
(§10.1 for `p12_final_audit.py`; `p12_w6_lines.py` first died on a cp1252 encoding error), and
both corrections are recorded in the scripts' own docstrings rather than hidden.

### 9.4 Limitations of this pass, stated plainly

- **No deployed environment was reached.** D6-D is unanswerable from here (§3).
- **No real Redis.** 5 tests remain unexecuted (§6.3).
- **No production database.** The D6-C counts remain vacuous; only the query's validity was
  established (§7.3).
- **No authorization channel.** Five workstreams end in a verified specification rather than
  a patch (§2).
- **Four corrections to this programme's own prior claims** are recorded rather than quietly
  fixed: the W-6 liveness overstatement (§5.4), the "Total granted: 0" overreach (§2.1), the
  unreproducible `ENABLE = 83` figure (§7.1), and this phase's own false drift report (§10.1).
- **The repository was under concurrent modification throughout.** Porcelain moved 16 → 39.
  That is normal here and was handled by re-measuring rather than assuming, but it means any
  count in this report other than "2 files differ from `HEAD`" should be re-measured, not
  quoted, by a later phase.

---

## 10. Preservation audit, and before/after blocker status

### 10.1 A correction to this phase's own audit script

The first draft of `staging/p12_final_audit.py` compared the tracked files against hash
strings carried forward in prose from earlier phases, using a hash function written fresh in
that script. It reported **`drifted=13/13`**. That was an artefact of the comparison, **not a
finding**: a hash function cannot be compared against values a different function produced,
and one of the 13 paths was wrong as well (`core/event_router.py`; the file is at
`backend/event_router.py`, which the script reported as MISSING).

It is recorded here rather than silently corrected, because a false drift report in this
programme would trigger exactly the stop-and-investigate response that §1 describes, and the
next phase should know this one produced a false positive and why.

The audit was rebuilt on two authoritative mechanisms: `git diff HEAD --name-only` for
non-drift, which needs no hash at all, and `git hash-object` for per-file digests, which any
later phase can reproduce with one command.

### 10.2 Final preservation audit (`staging/p12_final_audit.txt`, EXIT=0)

| Check | Result |
|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| Branch · ahead/behind `origin/main` | `main` · **0 / 0** |
| `074b772a` reachable from `HEAD` | **True** — no history was discarded |
| Worktrees · unmerged paths | 1 · **0** |
| **Staged entries** | **0** — nothing was staged by this phase |
| Working-tree entries | 16 at entry → **39 at exit** (concurrent work + this phase's 16 files) |
| **Files differing from `HEAD`** | **2** — unchanged at entry and exit |
| **…of which in the 16-file tracked set** | **0** |
| Non-staging working-tree entries | **1** |
| Tracked files missing | **0 / 16** |

The 2 files differing from `HEAD` are `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (+2/−1)
and `tests/test_drift_classification.py` (+143/−0). **Neither was touched by this phase.**
`test_drift_classification.py` is the output of the `AP-W1D-3` authorized drift-classification
workstream (§2.1); it was read, never written, and is left exactly as found. The same applies
to the four concurrent artifacts in §2.3.

Everything this phase wrote is a new file under `staging/`: 15 evidence files
(`p12_*`) plus this report. **No existing file in the repository was modified, moved or
deleted.** `staging/p12e12_w6_probe_run.txt` is *not* among them — it is another session's run
of this phase's probe script (§5.7) and is left as found.

Blob ids of the tracked set, all matching `HEAD` — reproducible with `git hash-object <path>`:

```
74a0059411467442b6aaa3d11d0d89af4d00fe65  backend_app/backend/api_key_vault.py
df4fd42a151015124ae7eb3743733107106c413e  backend_app/backend/exchange_executor.py
a67092359cb74ad372a9337d3fd523e42f6ba06c  backend_app/backend/order_watchdog.py
a3849d4acd7afcf4e53054fc85b16c786776eada  backend_app/main.py
f167f0e8c239511c1970144d04e5ba3bb05c1da5  backend_app/core/state.py
a62c65416376e18d00685975d0d8c5a6f50160a5  backend_app/core/models/execution_record.py
a87786f2ea641a972226fbd6d41b626dd65d2f03  tests/test_d5_watchdog_adjudication_repair.py
ff2d34bf030a3086d2af63729c025bbef87ca045  backend_app/routers/orders.py
16f0377cb9f6c85ef0fb21ce3df37940bd27628e  backend_app/routers/market.py
a74d76448a15150f703f05d0e43b83fd8470007b  backend_app/backend/portfolio_cache_updater.py
8c83aaef2d0daaed19f263b8f2b46adc1bf00475  backend_app/backend/event_router.py
5f8774bc184588919faf2f2a4b7faca71ac7181c  backend_app/core/dependencies.py
4a14a0916b5093c2b74838071463d95b3c5fe77c  tests/test_market_none_keys_fix.py
dcc4db123f2cf27ebebdd672dd3f1a566f38eaac  backend_app/core/subscription_dependencies.py
4b40973568edfa4dc24a8416819f872eaf18d54c  backend_app/routers/exchange.py
21c46835e225f904c362d2dfdee9af9aeaaf3d75  backend_app/core/tenant_db_context.py
```

**No commit, no push, no staging, no destructive operation was performed in this phase.**

### 10.3 Before/after blocker status

Statuses change **only** where this pass produced evidence that supports the change.

| Blocker | Before (D11) | **After (Phase 12)** | What changed, and on what evidence |
|---|---|---|---|
| **W-6** classifier | OBSERVED | **OBSERVED + MEASURED** | A probe executed the ladder: 3/8 misclassify (`p12_w6_probe.txt`). 2 of 3 findings new. Blast radius corrected 1 → **5** callers. Liveness corrected **downward** (§5.4). Still **OPEN**, unauthorized. |
| **QUOTA-01** | TESTED (50p/5s, isolation caveat) | **TESTED, caveat retired** | Re-ran at the new baseline: 0 failures, same 5 named skips. D11's "staged work" isolation qualifier is **obsolete** — `routers/exchange.py` is committed (§6.4). Real-Redis lane still unexecuted → **OPEN**. |
| **D6-C** | Query prepared, not run | **Query CORRECTED; still unanswered** | Measured that the prepared query **fails to plan** (`uuid = text`) and **miscounts**. Corrected query verified to plan (§7.3). Evidence still **NOT SUPPLIED** → **OPEN**. |
| **TENANT-01** | OPEN, FORCE RLS = 0 | **OPEN** (confirmed, quantified, scope stated) | **Executable FORCE RLS = 0** re-measured with per-tree scope; the one occurrence is a comment. The prior `ENABLE = 83` figure is **not reproducible** and is superseded by 120 total / **108 executable** (§7.1). `tenant_db_context.py` read in full: implemented, **0 consumers**. 149 isolation tests pass. **No status change** — passing tests are not FORCE RLS. |
| **D7-1** vault signature | Spec machine-verified | **Spec re-verified + impact sharpened** | 7/4/3 reproduced at the new `HEAD`; both bind assertions execute. Per-site runtime consequence now known (§4.4): one **unhandled 500**, one **shows "no history"**, one **silently drops to an unauthenticated connection**. No grant → **OPEN**, not implemented. |
| **D6-D** vault type | BLOCKED | **BLOCKED** | No deployed access. Emitters re-verified only. No change. |
| **D6-A / D6-B** | NOT DECIDED | **NOT DECIDED** | No decision arrived. No change. |
| **W-1b** executor integration | OPEN | **OPEN** | No change. |
| **W-3** venue id | OPEN, prepared | **OPEN, prepared** | No change. W-6 identified as a substantive prerequisite (§8). |
| **W-4 / W-5** stale recovery | OPEN, ordered | **OPEN, ordered** | No change. |
| **D7-2** key_manager | OPEN | **OPEN** | No change. Monitoring falsehood highlighted as the first thing to fix (§8). |
| **Live trading** | **OFF / NOT CERTIFIED** | **OFF / NOT CERTIFIED** | Unchanged and not a candidate for change. |
| **Paper track** | 53% | **53%, unrevised** | Not examined this pass. |

**Net: 0 blockers closed — none could be, with 0 grants. 4 blockers advanced in evidence
quality (W-6 argued → measured and independently reproduced; QUOTA-01 caveat retired; D6-C
query repaired; D7-1 impact characterised per site). 1 defect found in the programme's own
evidence request (D6-C, which would have errored). 4 prior claims corrected (§9.4).**

**Why 0 closed is the correct outcome rather than a failure of effort:** every remaining
blocker is gated on something outside this workspace — an owner decision (D7-1, W-6, W-3,
D6-A, D6-B), a deployed environment (D6-D), a production database (D6-C), or an
infrastructure change (QUOTA-01 real-Redis). The work available without those was done.

### 10.4 Next actions, in value order

1. **Deliver the §7.3 corrected D6-C query to the DB owner.** The version that has been in
   circulation since D8 would have returned an error. This is the cheapest blocker to retire
   and it has been blocked on a broken query, not on a reluctant owner.
2. **Deliver the §3 D6-D request to the platform owner.** Five fields, two environments. It
   gates W-1b and it cannot be answered from inside the repository.
3. **Obtain a grant, or a refusal, for D7-1 (§4.3).** Three single-line deletions, verified,
   with no lint impact. It is the smallest authorized-work decision outstanding and the
   defect is a live `TypeError` on three router paths.
4. **Do not enable FORCE RLS** until `tenant_db_context` is wired (§7.1). This is the one item
   on the list where acting without the full picture would make the product worse.
5. **Treat W-6 as a prerequisite for W-3**, not a parallel track (§8).

---

*Phase 12 closed. 0 source files modified · 0 test files modified · 0 commits · 0 pushes ·
0 staged entries. Live trading remains OFF and NOT CERTIFIED.*
