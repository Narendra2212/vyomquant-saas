# Phase 11B / A23E17 — Executed Evidence, W-6 Measurement, and Parallel Blocker Status

**Date:** 2026-10-11 · Continues `PHASE11B_A23E11_AUTHORIZATION_DELTA.md`.
**Destination note:** the brief assigned `staging/PHASE11B_A23E12_PARALLEL_BLOCKER_RESOLUTION.md`.
That path **was already written by a concurrent session at 18:43:15 (24,322 B)**, as were `A23E13`
(19:30) and `A23E14` (18:47). Overwriting it would have destroyed another session's evidence, so this
report takes a new path. **No prior report was modified.**

> **OUTCOME: 0 implementations authorized, 0 production files changed, 0 test files created.**
> **5 test/probe executions performed — 4 of them not previously run anywhere in this repository.**
> **Live trading: OFF / NOT CERTIFIED.**

This report is deliberately **narrow**. A23E12/E13/E14 already cover the governance re-derivation,
the D7-1 specification and the TENANT-01 static scan. Restating them would add volume, not evidence.
What follows is what those reports do **not** contain: executed measurements (§3), one previously
unreported classifier defect (§4), and the pooled-connection analysis (§5).

---

## 1. Repository state — custody changed mid-engagement, content did not

**The repository owner committed and pushed everything.** Detected on this pass's first read, before
any write.

| Measurement | A23E11 | A23E17 entry = exit |
|---|---|---|
| `HEAD` | `074b772a…` | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| commit | — | `launch phase 1` · `Narendra2212 <nt3441449@gmail.com>` · 2026-10-11 15:13:47 +0530 |
| `origin/main` | `074b772a` | **`ecd23a9e`** — pushed, 0/0 ahead/behind |
| staged | 22 | **0** |
| unstaged | 102 | **2** |
| untracked (`-uall` files) | 3,601 | **7** |
| canonical staged digest | `2e994018…7152` | **`01ba4719c80b…546b`** = SHA-256 of a lone newline, i.e. **empty staged diff** |
| isolated `write-tree` | `6f40856c…` | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`**, 0 objects written |

Scale: **3,723 files changed, 1,803,165 insertions, 1,524 deletions.** The 22 staged entries, the
102 modified files and ~3,500 untracked files — including every `staging/` report — were absorbed.

**Nothing was lost.** 5,705 tracked files, 153 `staging/*.md`, 512 test files all present on disk.
The prior invariants (`2e994018…`, `6f40856c…`) are now **historical and unreproducible by
construction** — not drift, and not a preservation failure. Any session still comparing against them
will mis-read this as catastrophic loss; it is a commit.

**Protected files — byte-identical across the commit, worktree *and* committed tree:**

| File | Blob (both worktree and `HEAD:`) | State |
|---|---|---|
| `backend_app/backend/redis_manager.py` | `d7b0e811b615ee13ba089b5cbe9d790161176de2` | **clean** |
| `backend_app/core/global_safety.py` | `4b2a1fc52748f3e90e86083b43f9e8c232629c3d` | clean |
| `backend_app/core/cache/redis_manager.py` | `6c8a3692c35056d0b7ea25d7ce04dfcf1db06a11` | clean |
| `tests/test_redis_debounce.py` | `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc` | clean |
| `tests/test_a23_redis_pool_lifecycle.py` | — | **ABSENT, not in the commit** |

So every source finding from A23R onward carries forward without re-derivation. **Committing a file
unchanged authorizes nothing** — concurring with A23E14 §2.2.

One in-flight item preserved untouched: `tests/test_drift_classification.py` (` M`, +143 lines),
another workstream's live work.

---

## 2. Authorization — 0 grants across all eight workstreams

| Workstream | Implementation authorization | Source |
|---|---|---|
| Redis A23 P1 (G1/G2/G3) | **ABSENT** — A23E6 §3 re-measured: **12 ☐ empty, 0 marked, 6 blank fields** | A23E6, unchanged |
| D7-1 vault signature | **ABSENT** | D11 §4; A23E13 |
| W-6 classifier | **ABSENT** | D10 §7; A23E13 §170-173 |
| QUOTA-01 | n/a (no change proposed) — gate closure blocked on real Redis | §3.3 |
| TENANT-01 / D6-C | **ABSENT**; both owner evidence packets "NOT SUPPLIED" | D10 §178 |
| W-3, W-4/W-5, D7-2 | **ABSENT** | D10 §7.1 |

Two new artifacts scanned (`A28B` 28,580 B, `A29` 18,689 B) plus `D11`: no Redis or W-6 grant.
D11's four `✔` marks are **preservation-audit ticks, not decision boxes** — a probe keyed on
checkmark glyphs will misread them. A29's three bare `GRANTED` tokens all resolve to `AP-W1D-3`
(the unrelated W1D classifier item) or to its own disclaimer at `:334`.

### 2.1 The W1D-12 precedent and its limit — adopting A23E14's correction

A23E14 §3 establishes something that **corrects my own earlier framing** in A23E and A23E11. I had
reported that no in-session authorization mechanism exists. That was wrong:
`PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` §2 records a valid grant *"issued by the user in this
session, acting in the **Code owner** role"*, while §222 simultaneously states *"no named individual
attested in the repository."* **A role-attributed in-session grant is a valid instrument here, and
naming an individual is not required.**

The limit is what blocks A23: the `Code owner` role **pre-existed** in the ledger's Owner column at
`PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802` *before* the grant was issued. The Redis lifecycle
item has no such row — `PHASE11B_A10_D5_APPROVAL_READINESS.md:244,367` records the file as unowned,
and `A3_BLOCKER_REGISTER.md` §3, the only register with an Owner column, has no Redis row.

**This narrows the external ask materially, and it is the single most useful finding of this pass.**
The minimum action is *not* "identify a named person" (A23E11 §7 item 1 overstated it). It is:
**add an Owner row for the Redis lifecycle item to a register, then issue the grant in that role** —
exactly the W1D-12 procedure. That is cheaper and already has working precedent in this repository.

### 2.2 A named actor now exists, and is still not a grant

`ecd23a9e` is authored and pushed by `Narendra2212 <nt3441449@gmail.com>`, who demonstrably holds
write and push authority over `main`. This is the first *named* actor with demonstrated repository
authority in the whole engagement, and it identifies a plausible delivery route.

It is **not** authorization, for a reason stated in the instrument itself: A23E6 §A reads
*"Not to be completed from commit authorship or inferred responsibility."* The commit also touched
`redis_manager.py` in no way. **Recorded as a delivery-route candidate only.**

---

## 3. Executed evidence (the new contribution)

All runs: `.venv\Scripts\python.exe` (Python 3.12.10, pytest 9.1.1), `-p no:cacheprovider`, no
network, no venue, no credentials, no database, no Redis server started.

### 3.1 W-6 classifier — FIRST EXECUTION IN THIS REPOSITORY

A concurrent session left a read-only probe, `staging/p12_w6_classifier_probe.py`, but **never ran
it**: A23E13 §170-173 only notes the pattern is available and that authorization is absent, and the
probe is `print`-only with no recorded output. Prior W-6 findings were therefore **static traces**.

```
python staging/p12_w6_classifier_probe.py        # EXIT=0 → staging/p12e12_w6_probe_run.txt
```

**Measured: `cases: 8   misclassified: 3`.**

| Case | Actual | `error_code` | retryable | Verdict |
|---|---|---|---|---|
| genuine invalid symbol | `InvalidSymbolError` | `INVALID_SYMBOL` | False | ok |
| **timeout whose message carries `?symbol=BTCUSDT`** | `InvalidSymbolError` | `INVALID_SYMBOL` | **False** | **MISCLASSIFIED** (want `NetworkError`) |
| timeout, no symbol text | `NetworkError` | `NETWORK_ERROR` | True | ok |
| **network failure carrying `/api/v5/market/ticker`** | `InvalidSymbolError` | `INVALID_SYMBOL` | **False** | **MISCLASSIFIED** |
| genuine authentication failure | `AuthenticationError` | `AUTH_ERROR` | False | ok |
| **`"monkey patch failed"`** | `AuthenticationError` | `AUTH_ERROR` | False | **MISCLASSIFIED** (want `ExchangeError`) |
| insufficient funds | `InsufficientFundsError` | `INSUFFICIENT_FUNDS` | False | ok |
| rate limit | `RateLimitError` | `RATE_LIMIT` | True | ok |

The predicted 3/8 is now **measured 3/8**, exact agreement. W-6 moves OBSERVED → **TESTED**.
Implementation remains **unauthorized**.

### 3.2 D7-1 — independently reproduced at the new HEAD

```
python staging/p11bd11_d71_bind_probe.py         # EXIT=0 → staging/p12e12_d71_bindprobe_rerun.txt
```

`load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict` ·
**7 call sites · 4 bind · 3 `TypeError: got an unexpected keyword argument 'access_token'`** at
`routers/orders.py:789`, `routers/orders.py:1095`, `routers/market.py:50` (AST call-node lines; the
`access_token=` literals sit at `:792`, `:1098`, `:53`). Four sites clear:
`routers/exchange.py:993`, `api_ws/ws_routes.py:814`, `backend/master_executor.py:252`,
`backend/portfolio_cache_updater.py:160`. Matches D11 §5.1 exactly across the commit.

### 3.3 QUOTA-01 — 43 passed / 5 skipped, and the 5 skips located exactly

```
pytest tests/test_quota01_enforcement_is_wired.py tests/test_quota01_exchange_slot_is_atomic.py \
       tests/test_quota01_metered_reservation_is_hard.py -q --tb=line -rfs
# EXIT=0 → staging/p12e12_quota.txt
```

**`43 passed, 5 skipped, 8 warnings in 25.14s`**

The five real-Redis tests the brief asks about, with exact locations:

| # | Location |
|---|---|
| 1-2 | `tests/test_quota01_exchange_slot_is_atomic.py:641`, `:700` |
| 3-5 | `tests/test_quota01_metered_reservation_is_hard.py:230`, `:414`, `:490` |

All five share one gate, quoted verbatim: *"`AERORA_TEST_REDIS_URL` is not set. Real-Redis coverage
is opt-in… Set `AERORA_TEST_REDIS_URL` to a DEDICATED test instance or database index to enable it.
**A skip here is NOT a pass.**"*

**QUOTA-01 is TESTED on the in-process substrate; real-Redis coverage is UNEXECUTED.**
The gate is **not** closed and the launch register is **not** changed.

### 3.4 Redis debounce — the strict XFAIL contract is intact

```
pytest tests/test_redis_debounce.py -q --tb=line -rfsxX
# EXIT=0 → staging/p12e12_redis_debounce.txt
```

**`18 passed, 2 xfailed in 1.46s`** — `collected 20 items`, pattern `.........x.........x`

`test_r6_d_desired_a_runtime_failure_should_be_recoverable` is **XFAIL under both `[asyncio]` and
`[trio]`**. Its reason restates the R6 defect: *"`_connected` is written only by `initialize()`, so a
failed command leaves it True and `get_redis_manager()`'s fast path returns the broken manager for
the rest of the process… When a fix lands this becomes an XPASS, which pytest reports as a failure
under `strict=True`."* **The P2 tripwire is preserved and must stay XFAIL until P2 is authorized.**

Note for the record: earlier phases cited "9 passed / 1 xfailed". The file is anyio-parametrized, so
the current 18/2 is the same 10 tests across two backends, not a change in coverage.

### 3.5 Prior closures re-verified post-commit

```
pytest tests/test_a20_quota_refusal_semantics.py tests/test_p11b_a8_collection_integrity.py \
       tests/test_api03_rate_limit_storage_is_fail_closed.py \
       tests/test_kill03_authoritative_global_halt.py -q --tb=line -rfs
# EXIT=0 → staging/p12e12_regress.txt
```

**`84 passed, 7 skipped, 45 warnings in 12.62s`** — the A20 quota-refusal fix, the P11B-A8
collection-integrity guard (incl. its negative control) and the API-03 order-dependence fix all
survived the commit. 5 of the 7 skips are PostgreSQL-gated kill-switch audit tests
(`test_kill03_authoritative_global_halt.py:1334, :1375, :1401, :1431, :1452`), each reading
*"A SKIP here is NOT a pass -- the durable audit trail is unverified."*

### 3.6 Environment limits — measured, not assumed

| Resource | Probe | Result |
|---|---|---|
| Redis | TCP 127.0.0.1:6379 | **closed** |
| Redis env | `REDIS*` / `AERORA_TEST_REDIS_URL` | **0 variables set** |
| Docker | `Get-Command docker` | **absent** — cannot provision one even if authorized |
| PostgreSQL | TCP 127.0.0.1:5432 | **closed** (`staging/pg_dsn.txt` exists; **not opened, not connected**) |

**E-6 is unexecutable on this host as a matter of fact, not policy.** No Redis was started, stopped
or reconfigured. No exchange was contacted. No database connection was opened.

---

## 4. W-6: a third shadowing defect, not previously reported

The classifier is `_handle_ccxt_error` at **`backend_app/backend/exchange_executor.py:1225-1257`**,
on `BaseExchangeExecutor` (**not** `CCXTExchangeExecutor`, which begins at `:1264`). It is a pure
case-insensitive substring ladder over `str(error)`; **no `isinstance` check and no ccxt exception
type is referenced anywhere in it**, so a genuine `ccxt.RequestTimeout` and a `RuntimeError` with
the same text are indistinguishable.

Ladder order is the whole defect — first match wins:

| Order | Line | Substrings | Result |
|---|---|---|---|
| 1 | `:1240` | `insufficient`, **`balance`** | `InsufficientFundsError` |
| 2 | `:1243` | `rate limit`, `too many requests` | `RateLimitError` |
| 3 | `:1246` | **`symbol`**, **`market`** | `InvalidSymbolError` |
| 4 | `:1249` | `network`, `timeout`, `connection` | `NetworkError` |
| 5 | `:1252` | `authentication`, `apikey`, **`key`** | `AuthenticationError` |

**NEW — a third false positive, same defect class, not in any prior report.** `balance` at `:1240`
is the **topmost** branch, so any failure text containing it — including a timeout or 5xx on the
balance endpoint, e.g. `"binance RequestTimeout: GET /api/v3/account balance timed out"` — becomes
`InsufficientFundsError(retryable=False)`. `backend_app/backend/exchange_telemetry.py:520` then
escalates that `error_code` to alert severity `"high"`. This belongs in the regression table as a
sixth row.

**Correction to the framing in prior reports.** Reports describe these classes as driving retry
behaviour. They do not. Nothing in `backend_app` reads `ExchangeError.retryable`: `place_order` logs
it once at `:1687` and discards it, `cancel_order`/`get_order_status` drop `error_code` entirely, and
`connection_engine.py:336-425` runs its own **type-based** retry loop that never calls this
classifier. The real blast radius is the **persisted `OrderResult(status="rejected", error_code=…)`**
plus telemetry severity. A timeout — the single most *indeterminate* venue outcome, where the order
may well have reached the book — is recorded as a *determinate* rejection.

Three stale citations worth fixing when W-6 is authorized:
`docs/history/SANDBOX_FAILURE_INJECTION.md:170` cites `:L592` (**stale by ~630 lines**); `:83` quotes
the auth branch **omitting the `or "key"` disjunct** that causes the defect; `:111-113` claims
invalid-symbol means "order rejected before reaching exchange", but the classifier only runs inside
`except Exception` *after* `create_order` was attempted (`:1682`).

**Coverage today: zero in the default lane.** The only two tests that call the classifier
(`tests/exchange_sandbox/test_sandbox_order_lifecycle.py:556`, `:590`) assert correct-case behaviour
only, so they pass identically before and after any fix, and both are skipped by
`tests/exchange_sandbox/conftest.py:57-76` unless `AERORA_EXCHANGE_SANDBOX_CERT=1`.

### 4.1 Regression suite — designed, NOT created (no W-6 grant)

Six server-free cases, constructible via `object.__new__(CCXTExchangeExecutor)` with the three
credential attributes set to `""`: genuine invalid symbol stays terminal · timeout carrying
`symbol=` stays `NETWORK_ERROR`/retryable · genuine auth failure stays `AUTH_ERROR` ·
`"monkey patch failed"` must **not** be `AUTH_ERROR` · `/market/` in a network path stays
`NETWORK_ERROR` · **balance-endpoint timeout must not be `INSUFFICIENT_FUNDS`**. Preferred fix shape:
check structured ccxt types first, fall back to the ladder with word-boundary matching and the
network branch **above** the symbol branch.

---

## 5. TENANT-01 / D6-C

### 5.1 The chain, and the link that does not exist

```
Bearer JWT → get_current_user              backend_app/core/dependencies.py:300
   tenant_id = payload["tenant_id"] or app_metadata["tenant_id"] or sub        :318
Depends(get_db) → db_pool.get_session()    backend_app/core/database_pool.py:385-392
   → handler SQL with application-level WHERE
```

**Nothing between authentication and the SQL sets `app.current_tenant_id`.** `get_db` (`:385-392`)
and `get_db_context` (`:395-401`) create, yield, close. The only three pool event listeners
(`:233-236` → `:252-293`) record statistics exclusively. So the nine RLS policies have nothing to
compare against and isolation rests entirely on application `WHERE` clauses.

RLS is defined in `backend_app/alembic/versions/implement_rls_policies_20260817.py`: nine tables,
one `FOR ALL` policy each, all comparing `current_setting('app.current_tenant_id', true)`.

**Two findings that weaken every "RLS is enabled" claim:**

1. **`FORCE ROW LEVEL SECURITY` appears in zero migrations** across all four trees. PostgreSQL
   exempts a table owner from its own policies without it, and the committed non-secret DSN default
   is role **`postgres`** (`database_pool.py:151-155`) — the owner of these tables. **The policies
   are inert on the SQLAlchemy path regardless of the GUC.**
2. **Every `ENABLE` and every `CREATE POLICY` in that migration is wrapped in
   `try/except Exception` with a `print` warning** (`:46-51` and each policy block). `upgrade()`
   reports success even if zero policies were created. From source alone the posture is
   **"attempted", not "achieved"** — any "enabled" claim requires a live catalogue read.

**Ordering constraint:** enabling `FORCE` *before* per-request context is wired converts owner-path
reads on `orders`/`positions`/`fills` into **silently empty result sets**, because
`current_setting(name, true)` returns NULL and the comparison yields NULL. `tenant_db_context.py`
is the missing half and is **unwired** — `get_tenant_db` (`:243-285`) and `tenant_db_dependency`
(`:287-299`) have no callers.

### 5.2 Pooled-connection reuse — the precise answer

Not today, and the guard is incidental. `Session.close()` returns the connection with SQLAlchemy's
default `pool_reset_on_return="rollback"`; no `DISCARD ALL`, no `checkin` GUC reset. A `ROLLBACK`
destroys anything set with `SET LOCAL` or `set_config(..., true)` — which is why the three existing
call sites cannot leak. A value set with plain `SET` or `set_config(..., false)` is **session-scoped,
survives `ROLLBACK`, and would be inherited by the next request**. `pool_pre_ping=True` only issues
`SELECT 1`; `pool_recycle=3600` only retires by age. Neither resets GUCs. Compounding it, Supabase
DSNs get `pgbouncer=true&pool_mode=transaction` (`:175-178`) with 5432→6543 rewriting (`:159-160`).

**Current failure mode is "policies are inert", not "tenant bleed". The bleed appears the moment
someone wires tenant context with `is_local=false`.**

Two traps for whoever implements this: `database_pool.py` defines `get_db`/`get_db_context`
**twice** (`:385`/`:395` and `:591`/`:606`) and the later definition wins at import time; and
`transactional_execution_manager.py:224-236` issues `SET LOCAL` *before* `self.db.begin()`, working
only via SQLAlchemy's implicit transaction plus a swallowed "already begun" error — correct by
accident, and its `except Exception` + `logger.debug` silences a genuine PostgreSQL failure.

### 5.3 A test that cannot fail

`tests/test_tenant01_cross_tenant_matrix_postgres.py:689-707`
(`test_no_table_has_force_row_level_security_so_the_owner_bypasses_rls`) asserts only
`assert isinstance(forced, list)` — explicitly "Informational, and deliberately not a failure".
**Any report citing it as pinning the no-FORCE fact overstates it.** The real assertion is
`test_no_table_relies_solely_on_the_inert_tenant_setting` at `:647-686`, against a frozen five-table
set (`dag_tasks`, `idempotency_keys`, `transaction_records`, `transaction_checkpoints`,
`reconciliation_mismatches`).

### 5.4 D6-C — the count-query correction is already done

The brief asks for `DISTINCT`/`EXISTS`. **A concurrent session already implemented it** in
`staging/p12_d6c_sql_syntax_check.py` as `CORRECTED_SQL`, and it is verified: recorded output
`staging/p12_d6c_sql.txt:16-21` shows the original `LEFT JOIN` form returning
`{'tenants_with_keys': 2, 'tenants_without_keys': 1}` against a truth of
`distinct_tenants=2, with_keys=1, without_keys=1`, while the `EXISTS` form returns `{2, 1, 1}`.
Cause: `exchange_keys` is UNIQUE `(user_id, exchange_id)`, so a tenant with keys on two venues
produced two join rows. **`tenants_without_keys` is unaffected by the defect, which is exactly why
it is the load-bearing count.** I **did not re-run** this — it would require a database connection
that is not authorized. Recorded as already-corrected, verified by the concurrent session's output.

D6-C's second half remains **unmeasurable from application tables**: the `tenant_id`-vs-`sub`
claim-divergence count depends on `dependencies.py:318` permitting `tenant_id != sub`, and requires
the auth/platform owner. Claiming otherwise from application tables would be false.

---

## 6. W-3, W-4/W-5, D7-2 — carried forward, unchanged

No authorization, and no new evidence this pass. Preserved invariants, recorded so they are not lost:
W-3's `coalesce-never-swap`; migration 030's no-FK rule; W-4/W-5's `UNKNOWN` exit semantics remain
**undefined** and the CAS is `EXECUTING`-only — **no exit rule was invented**. Deferred to the
sessions holding them (`A23E13`, `D10` §7).

---

## 7. Consolidated governance request — copy-ready, A23E6 unaltered

Correct vocabulary: the form offers **Option A / Option B**. "Option I"/"Option III" appear **zero
times** anywhere in this repository and must not be used — a reply in those terms maps onto no
artifact.

> **Prerequisite (new, per §2.1).** Add an Owner row for the Redis lifecycle item to a register —
> e.g. `A3_BLOCKER_REGISTER.md` §3 — so the grant can be issued *in a pre-existing role*, as
> `W1D-12` was. **Naming an individual is not required**; `W1D-12` §222 is explicit that none was.
>
> Then complete `staging/PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` §3:
>
> - **§A / E-1** — assignee of `backend_app/backend/redis_manager.py` + precise scope. Leave the
>   "additional files" field blank: `global_safety.py` and `core/cache/redis_manager.py` belong to
>   other workstreams.
> - **§B / D-A23C-1** — ☐ GRANTED / ☐ REFUSED for exactly the three changes: (1) close every client
>   a failed `initialize()` created while **preserving the original failure**; (2) on re-init publish
>   the new trio **before** closing the outgoing one, each closed exactly once; (3) make `close()`
>   repeatable — close, reset `_connected`, unbind `_cache`/`_queue`/`_events`.
> - **§C / D-A23C-7** — ☐ GRANTED / ☐ REFUSED for `tests/test_a23_redis_pool_lifecycle.py`, and
>   reserve that path. `tests/test_redis_debounce.py` not to be modified.
> - **§D / D-A23C-3** — ☐ **Option A** (proxy raises `AttributeError` post-shutdown; document the
>   gap) or ☐ **Option B** (typed error; ~17 call sites; **requires the `core/global_safety.py`
>   co-decision at `:149`**, and that file is dirty under another workstream, so Option B cannot be
>   executed under a one-file grant). **Hard prerequisite** — it governs what the 11 kill-switch
>   sites (`global_safety.py:244, 255, 257, 266, 275, 284, 315, 334, 351, 370, 384`) observe.
>   Omitting the unbind is not on offer: A23-01 shows both halves are load-bearing.
> - **§E / D-A23C-8a** — ☐ ACKNOWLEDGED by the `core/cache/redis_manager.py` owner that change 3
>   alters `close()`'s postcondition, consumed at `:619` via `main.py:607`. No edit to that file.
> - **§F / E-5 / D-A23C-12** — exactly one of ☐ 12a (accept + document; keeps the one-file scope) /
>   ☐ 12b (lock the third caller at `core/cache/redis_manager.py:625` — needs a separate grant) /
>   ☐ 12c (defer P1).
> - **§G / D-A23C-9** — ☐ ACKNOWLEDGED for the 5 `Depends(get_redis_manager)` handlers in
>   `routers/exchange.py` (`MM`, not edited); name the decision-maker; ☐ implementation
>   authorization.
> - **E-6** — either authorize a real-Redis lane **or** record that P1 lands on server-free coverage
>   alone. Note §3.6: this host has no Redis and no Docker, so the lane needs CI or a provisioned
>   service regardless.
>
> Separately, if W-6 / D7-1 / TENANT-01 work is wanted, each needs its own grant; D7-1's is a
> three-line deletion with a machine-verified specification already sitting in `D11` §5.4.

**No approval was created, implied, or signed. A23E6 was not modified. No owner was contacted — no
documented channel exists, and `Narendra2212` is recorded as a route candidate only (§2.2).**

---

## 8. Workstream status — before / after this pass

State vocabulary kept strictly separate; **no state was upgraded because another was achieved.**

| Workstream | Before | After | Change |
|---|---|---|---|
| **Redis A23 P1** | OBSERVED | OBSERVED | none — unauthorized |
| **Redis R6 tripwire** | — | **TESTED** | `test_r6_d` XFAIL intact, 18 passed/2 xfailed (§3.4) |
| **W-6 classifier** | OBSERVED | **TESTED** | 3/8 misclassified **measured**; third defect found (§3.1, §4) |
| **D7-1 vault** | OBSERVED (D11) | **OBSERVED, reproduced post-commit** | 7/4/3 confirmed (§3.2) |
| **QUOTA-01** | TESTED (D11, old HEAD) | **TESTED at new HEAD** | 43 passed/5 skipped; 5 skips located (§3.3) |
| **TENANT-01** | OBSERVED (static) | **OBSERVED + pooled-reuse analysed** | §5.1-5.3; owner-bypass is decisive |
| **D6-C** | correction proposed | **correction verified already applied** | §5.4 |
| **W-3 / W-4-5 / D7-2** | OBSERVED | OBSERVED | none — unauthorized |
| **Prior closures** | TESTED | **re-TESTED post-commit** | 84 passed/7 skipped (§3.5) |

**IMPLEMENTED: nothing. DEPLOYED: nothing. APPROVED: nothing.**
Genuine blocker *reduction* this pass is evidentiary: W-6 moved from inferred to measured, and
QUOTA-01's residual gap is now five exactly-located tests behind one named environment variable.

---

## 9. Preservation audit

| Check | Result |
|---|---|
| `HEAD` | `ecd23a9e…` at entry and exit — **unchanged by this pass** |
| branch / divergence / worktrees / unmerged | `main` / 0-0 / 1 / 0 — unchanged |
| staged | **0** at entry and exit — nothing staged |
| Production files modified | **0** |
| Test files modified or created | **0** |
| Four protected blobs | all unchanged (§1) |
| `tests/test_a23_redis_pool_lifecycle.py` | **still ABSENT** |
| Other workstreams' work | `tests/test_drift_classification.py` ` M` **untouched**; all 19 concurrent `staging/` artifacts untouched; `PHASE11B_A23E12_…` **not overwritten** |
| `stage`/`commit`/`push`/`reset`/`clean`/`stash`/`checkout`/branch change | **none used** |
| Redis / exchange / database / network / CI / deploy | **none** — no server started, no venue contacted, no DB connection, no DSN opened |
| Credentials | `.env`, `exchange_keys.json`, `staging/pg_dsn.txt` — existence checked only; **never opened, no value read or echoed** |

**Files written — 6, all new, all under `staging/`, all untracked:**
`PHASE11B_A23E17_EXECUTED_EVIDENCE_AND_W6_MEASUREMENT.md` (this report) ·
`p12e12_w6_probe_run.txt` · `p12e12_d71_bindprobe_rerun.txt` · `p12e12_quota.txt` ·
`p12e12_redis_debounce.txt` · `p12e12_regress.txt`. No credentials, tokens, DSNs or tenant data in
any of them.

---

## 10. Highest-leverage next actions, in dependency order

1. **Add a register Owner row for the Redis lifecycle item, then grant in that role** (§2.1). One
   edit plus one in-session statement, with working precedent in `W1D-12`. Unblocks §A-§G and the
   whole P1 ladder. **Cheapest real decision available.**
2. **Answer §D as Option A or Option B** — it gates P1 regardless of §B, and Option B is not
   executable under a one-file grant while `global_safety.py` is dirty.
3. **Grant D7-1** — three line deletions, specification machine-verified in `D11` §5.4, cannot
   introduce a flake8 finding (adds no lines), isolated (no unstaged diff on either file).
4. **Grant W-6** — now backed by measured evidence, and the suite is server-free and
   credential-free. Include the balance-shadowing row from §4.
5. **Provision a real-Redis lane** (CI or a service — not this host, §3.6) to execute QUOTA-01's
   five located tests. **Until then QUOTA-01 cannot close**, regardless of local green.
6. **TENANT-01:** decide the `FORCE RLS` + per-request-context ordering (§5.1). Wiring context
   **must precede** `FORCE`, or owner-path reads silently return empty. Needs an approved test
   database; none is reachable here.
7. **Reduce report duplication.** Five sessions independently re-derived the same unchanged
   governance state this phase, and one assigned destination was already occupied. The constraint is
   a single missing register row, not missing analysis.

**Risks not fixed, each with its exact requirement:** Redis G1/G2/G3 pool leak and
reconnect-after-shutdown (needs §B + §D); W-6 three-way misclassification persisting indeterminate
timeouts as determinate rejections (needs W-6 grant); D7-1 three guaranteed `TypeError`s on live
order and market paths (needs D7-1 grant); TENANT-01 inert RLS under an owner role with no `FORCE`
(needs an owner decision plus an approved test DB); QUOTA-01 real-Redis atomicity (needs E-6 or a
CI lane); W-4/W-5 undefined `UNKNOWN` exit semantics (needs an owner rule — deliberately not
invented here).

**Live trading: OFF / NOT CERTIFIED.** No launch gate was changed, and none was satisfied.

---

## 11. ADDENDUM — the frontend deploy is not security-gated, and it published

> ### CORRECTION, issued immediately after §11 was first written
>
> **§11 originally claimed this was a "new finding" and "the most consequential item in this report."
> That was wrong, and I am correcting it rather than leaving it to stand.** Two concurrent sessions
> had already recorded it, independently and earlier:
>
> - **`PHASE11B_A23E16_SCOPE_CI_AND_BLOCKER_REDUCTION.md:173-176`** — *"The frontend deployed from an
>   untested commit, and nothing stopped it… It has no dependency on the security gate and no test
>   gate."* Ranked **#2, HIGH** in its blocker table (`:373`).
> - **`PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md` §4.4** — *"Gap 1 — `06 Frontend Deploy` has no
>   gate whatsoever"*, ranked **#2, CRITICAL** (`:398`), already carrying a decision ID:
>   **`D-P15-4`** (`:313-316`).
>
> **Consequences for anyone acting on this report.** Use the existing ID **`D-P15-4`**; do not open a
> parallel decision. My §11.5 "Required decision (new, no owner identified)" is **superseded** by it.
> `PHASE15`'s formulation is also better than mine: it specifies verifying **`head_sha`** so an older
> green run for a *different* SHA cannot satisfy the gate — a hole my two options did not close.
>
> What §11 below does still contribute, and all it contributes: independent verification at source of
> the gate asymmetry (§11.2), the enumeration of what the job actually published (§11.3), and the
> explicit scope limits on what the security failures do and do not imply (§11.4). Treat it as
> corroboration of `A23E16`/`PHASE15`, not as a discovery.
>
> **One detail worth carrying forward, from `PHASE15:238` and `A23E16:162`:** `03 Deploy`'s refusal
> came from the step *"Require the security scan to have passed for this commit"*, implemented as
> **`require_security_scan.py`, SHA-pinned**. That is the P11-B08 deploy gate earlier phases prepared
> a patch for and flagged the SHA trap on. **It exists, it is SHA-correct, and it worked.** P11-B08
> should be re-read as closed-and-functioning on the evidence of run `38130273012`.

Added after §1-§10 on a follow-up pass. `A23E15` correctly reports that `05 Security` fails on
`ecd23a9e` and that `03 Deploy` *"correctly refused and the ECS deploy was skipped"* — but that
accounts for only one of the two deploy paths this commit triggered.

### 11.1 What the push actually ran

`gh run list` for `ecd23a9e` ("launch phase 1", push to `main`, 2026-10-11T09:48Z):

| Workflow | Run | Conclusion |
|---|---|---|
| `02 Build` | `38129699471` | success |
| **`05 Security`** | `38129699454` | **FAILURE** |
| **`06 Frontend Deploy`** | `38129699497` | **SUCCESS — 1m24s** |
| `07 Desktop Build (native, unsigned)` | `38129699516` | success |
| `03 Deploy` | `38130273012` | **failure** (13 s, `workflow_run`) |
| `05 Security` (scheduled, later) | `38132697285` | failure |

`gh run view 38129699497` confirms a single completed job: **`Build & Deploy Frontend to S3 +
CloudFront`** (job `114441282889`), concluded `✓`.

### 11.2 The gate asymmetry, verified in the workflow sources

| | `03 Deploy` (backend/ECS) | `06 Frontend Deploy` (S3/CloudFront) |
|---|---|---|
| Trigger | `workflow_run:` on `["02 Build"]`, `types: [completed]` (`03-deploy.yml:4-7`) | **`on: push`**, `branches: [main, master]`, `paths: ['algo22-terminal/**', …]` (`06-frontend-deploy.yml:3-10`) |
| Conclusion guard | `if: workflow_run.conclusion == 'success'` (`:73`) | **none** |
| Job dependency | `needs: pre-deployment-validation` (`:130`) | **none** |
| `needs:` occurrences | ≥1 | **0** |
| `workflow_run` occurrences | 1 | **0** |
| Outcome on `ecd23a9e` | **refused** (13 s) | **deployed** |

So the backend deploy sits behind a build gate plus a pre-deployment validation job and correctly
refused. **The frontend deploy is reached directly from `push` with no gate of any kind** — not on
`05 Security`, not on `02 Build`, not on `01 PR Check`. Because the commit touched
`algo22-terminal/**` (the `path` filter matched), it fired and completed.

### 11.3 What it published

The job's own steps, read from `06-frontend-deploy.yml`, are not a dry run:

- three `aws s3 sync dist/ s3://vyomquant-frontend/ --delete` passes (hashed assets, unhashed
  assets, HTML), each with `--delete`, so stale objects were **pruned**;
- `aws cloudfront create-invalidation --distribution-id EEOXECPHQ8SR0 --paths "/*"` followed by
  `aws cloudfront wait invalidation-completed` — so the edge was flushed and the job **blocked until
  the new content was live**;
- a final verification step listing bucket contents and the distribution status.

**Production frontend assets for `ecd23a9e` are live on S3 + CloudFront.** The workflow's own
comment on its last step confirms the ordering is deliberate: *"It runs last, after the S3 sync and
the invalidation, so the assets are already live when it fires: a failure reports a broken realtime
layer, it does not roll the frontend back."*

### 11.4 Assessment

This is a **gate bypass by topology, not by override.** Nobody skipped a check; the frontend path
never had one. The build-time steps in `06` are genuinely strict about *bundle* hygiene — they fail
the deploy if the service-role key, `localhost:8000`, or a placeholder installer appears in `dist/`,
and the final step blocks on a real WebSocket upgrade probe. None of that is repository-wide
security scanning, and none of it consults `05 Security`.

Scope limits I am keeping strict:

- **What is established:** the frontend of a commit whose security workflow failed was published to
  production, and the path that did it has no security gate. Both halves verified at source and in
  the run list.
- **What is NOT established:** that the 5 CRITICAL Trivy findings (in `terraform/`, per `A23E15`)
  or the Bandit SAST failure are *exploitable through the frontend bundle*. Trivy IaC findings in
  `terraform/` concern infrastructure definitions, not the SPA. I did not open the security run's
  logs this pass, and I am not claiming a live vulnerability in the deployed assets.
- **Unmeasured:** whether the deployed bundle differs materially from the previous one, and whether
  the backend ALB/ECS still serves the prior image — likely yes, since `03 Deploy` refused, which
  means **frontend and backend are now at different commits**. That skew is worth a deliberate
  check by whoever owns the release.

This belongs in the launch-gate register as its own item. It is **not** a Redis, W-6, D7-1 or
TENANT-01 issue, and it is not covered by any of the eight tracked workstreams.

### 11.5 Required decision (new, no owner identified)

> **Gate `06 Frontend Deploy` on the security workflow, or record an explicit accepted-risk
> decision that frontend publication is intentionally ungated.**
>
> Two mechanical options, both one-file changes to `.github/workflows/06-frontend-deploy.yml`:
> **(i)** convert it to `workflow_run` on `["05 Security"]` (and/or `["02 Build"]`) with an
> `if: …conclusion == 'success'` guard, matching `03-deploy.yml:5-7,73`; or **(ii)** keep the `push`
> trigger and add a job that queries the `05 Security` conclusion for `github.sha` and fails closed.
>
> Note the precedent trap documented in the P11-B08 audit: a gate keyed on the deploying SHA will
> **refuse every deploy** if it consults a workflow that only runs on `pull_request`. `05 Security`
> *does* run on `push` (run `38129699454` proves it), so option (i) is viable here — but
> `01 PR Check` is **not**, since `A23E15` establishes it is `pull_request`-only and therefore never
> ran for `ecd23a9e`.

**Owner: UNIDENTIFIED.** `.github/workflows/` has no owner row in any register, and there is still
no `CODEOWNERS`. No change was made to any workflow file on this pass.

### 11.6 Preservation re-confirmed after the addendum

`HEAD` `ecd23a9e…` · staged **0** · `redis_manager.py` `d7b0e811…` clean ·
`tests/test_a23_redis_pool_lifecycle.py` ABSENT · tracked modifications still exactly the two
pre-existing concurrent-session files. **No workflow, production or test file was edited. No CI run
was dispatched, cancelled or re-run — `gh run list` and `gh run view` are read-only.** No AWS
credential was used, no bucket or distribution was touched by me.

---

## 12. P11-B08 (deploy security gate) — VERIFIED CLOSED AND FUNCTIONING

Verified at primary source this pass, not taken from `PHASE15`/`A23E16`. This closes a loop opened
at the very start of this engagement, when P11-B08 was audited and a patch was *prepared but not
applied* because the gate's owner was Stream E — and when the audit flagged a specific trap.

**The gate exists.** `scripts/require_security_scan.py` is present, and
`.github/workflows/03-deploy.yml:90` invokes it as the step *"Require the security scan to have
passed for this commit"*, with the required `actions: read` permission documented at `:33-38`.

**It is SHA-pinned to the deploying commit** (`require_security_scan.py:58`):

```python
data = _api("/repos/%s/actions/runs?head_sha=%s&per_page=100" % (repo, sha), token)
```

filtered to `r.get("name") == WORKFLOW_NAME` (`:60`), with `sha` read from `DEPLOY_SHA` (`:65`). It
takes the **newest** matching run by `run_started_at` (`:106`) and returns 0 **only** on
`conclusion == "success"` (`:114-117`).

**It fails closed on every other path** — missing `GITHUB_TOKEN`/`GITHUB_REPOSITORY`/`DEPLOY_SHA`
(`:68-72`, *"Refusing rather than assuming the scan passed"*), GitHub API HTTPError (`:81-82`,
*"refusing rather than guessing"*), no run for the commit (`:100-103`, *"Nobody scanned this commit,
which is not evidence that it is safe"*), `cancelled`/`skipped` (`:118-121`, *"nothing was
verified"*), and any other conclusion (`:122`). The one bypass, `ALLOW_UNSCANNED`, is explicit and
self-logging (`:99`, *"This override is recorded here on purpose"*).

**It avoids the trap the original P11-B08 audit identified.** That audit warned that a gate keyed on
the deploying SHA would **refuse every deploy** if it consulted `01 PR Check`, because that workflow
runs on `pull_request` and records against the PR head SHA. This gate consults **`05 Security`**,
which runs on `push` — proven by run `38129699454` existing for `ecd23a9e`, a direct push. The
implementer chose the one workflow that makes a SHA-pinned gate viable.

**It demonstrably worked.** Run `38130273012` (`03 Deploy`, `workflow_run`, 13 s) failed at precisely
this step, and the ECS Fargate deploy job was **skipped** — the backend was not deployed from a
commit whose security scan was red.

**Status: P11-B08 is CLOSED and FUNCTIONING.** Evidence is threefold and independent: the script's
source, the workflow step that calls it, and a live run in which it refused. This is the one launch
blocker this pass can report as genuinely reduced to closed — and it was closed by another
workstream, not here.

**Scope limit:** this covers the **backend/ECS** path only. It says nothing about
`06 Frontend Deploy`, which has no gate at all (§11) and is tracked as **`D-P15-4`**. The two
findings are complementary: the backend gate is the working pattern that `D-P15-4` proposes copying.
