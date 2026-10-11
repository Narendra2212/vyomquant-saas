# Phase 11B-A23E14 — Register-to-Authorization Resolution & Parallel Blocker Reduction

**Destination verified ABSENT immediately before writing.** Read-only on every source file, test and
register. **0 production files modified · 0 test files modified · 0 tests executed · 0 approvals
created · 1 artifact written (this file).**

> **OUTCOME: no authorization arrived. Redis P1 NOT implemented.
> `backend_app/backend/redis_manager.py` unchanged at blob `d7b0e811b615…`.
> `tests/test_a23_redis_pool_lifecycle.py` NOT created.
> Register row NOT added — Branch B applies.**

**Live trading: OFF / NOT CERTIFIED.**

---

## 1. Executive summary

Six findings, five of them verified at primary source this pass:

| # | Finding | Status |
|---|---|---|
| 1 | **The repository was committed and pushed mid-pass by another actor.** `HEAD` moved `074b772a` → **`ecd23a9e` "launch phase 1"**, now `origin/main`. Staged 22 → 0, porcelain 262 → 21 | **VERIFIED.** Not caused by this pass — 0 writes before detection |
| 2 | All four Redis-relevant files survived the commit **byte-identical**. Redis P1 was committed *unchanged*, which grants nothing | **VERIFIED** |
| 3 | **W1D-12's precedent does not transfer to A23**, because the `Code owner` role **pre-existed** in the ledger's Owner column before the grant. Redis has no such row | **VERIFIED — decisive** |
| 4 | **D7-1 confirmed at source**: 3 call sites pass `access_token=` to a 2-parameter method. A 4th site is correct, so the count is 3, not 4 | **VERIFIED.** Authorization **ABSENT** |
| 5 | **W-6 confirmed at source**: both reported misclassification risks are real, and one is worse than reported | **VERIFIED.** Authorization **ABSENT** |
| 6 | **TENANT-01 FORCE RLS**: zero occurrences in any migration or Alembic file; exists only as prose in reports | **VERIFIED (static)** |

The A23 gate is unchanged: **12 ☐ empty, 0 marked, 6 blank fields**, re-measured after the commit.
A28B records *"No classifier grant authorizes any Redis lifecycle edit"*, now corroborated by five
independent sessions.

---

## 2. Repository state — entry, the mid-pass commit, and exit

| Measurement | A23E11 record | A23E14 entry | A23E14 exit |
|---|---|---|---|
| branch | `main` | `main` | `main` |
| `HEAD` | `074b772a` | `074b772a` | **`ecd23a9e`** |
| `origin/main` | `074b772a` | — | **`ecd23a9e`** (pushed) |
| ahead / behind | 0 / 0 | 0 / 0 | 0 / 0 |
| staged | 22 | 22 | **0** |
| porcelain (collapsed) | 140 untracked / 262 total | 262 | **21** |
| unmerged | 0 | 0 | **0** |
| worktrees | 1 | 1 | 1 |

### 2.1 The commit event — attribution

`git log --oneline` shows:

```
ecd23a9e (HEAD -> main, origin/main, origin/HEAD) launch phase 1
074b772a Give the deploy gate permission to read the answer it refuses without
```

`ecd23a9e` absorbed the 22 staged entries and the bulk of the modified and untracked work, then was
pushed. **This pass did not create it.** Every action here preceding its detection was a read
(`git rev-parse`, `git status`, `git hash-object`, `grep`, `read_file`), and no write of any kind
occurred before or after. The prior recorded staged digest `2e994018…` is now **historical and
unreproducible** — `git diff --cached` is empty because staged is 0. That is a consequence of the
commit, not drift introduced here.

### 2.2 Protected files — all byte-identical across the commit

| File | Blob | Worktree | Cross-check |
|---|---|---|---|
| `backend_app/backend/redis_manager.py` | `d7b0e811b615ee13ba089b5cbe9d790161176de2` | **clean** | matches A23E11 and every prior A23 record |
| `backend_app/core/global_safety.py` | `4b2a1fc52748f3e90e86083b43f9e8c232629c3d` | clean | **exact match to A23E11's recorded blob** |
| `tests/test_redis_debounce.py` | `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc` | clean | **exact match to A23E11's recorded blob** |
| `tests/test_a23_redis_pool_lifecycle.py` | — | **ABSENT** | unchanged |
| `staging/PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` | — | committed, **still blank** | 12 ☐ / 0 ✓ / 6 `____` |

**Consequence that matters:** the commit changed custody, not content. Every technical finding from
A23R through A23E13 remains valid without re-derivation, and the Redis target is now *clean at a new
HEAD* — which must not be mistaken for authorization. Committing a file unchanged authorizes nothing.

### 2.3 Remaining 21 entries

2 modified (`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`, `tests/test_drift_classification.py`)
and 19 untracked, all under `staging/` — concurrent sessions' reports and probes
(`A23E12`, `A28B`, `A29`, `D11`, `p11b29_*`, `p12_*`). None touched by this pass.

---

## 3. The W1D-12 precedent — verified, and its precise limit

Read at primary source, not via A28's quotation.

**What `PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` establishes:**

- §1 tally: **5 GRANTED, 1 CONDITIONALLY GRANTED, 0 REJECTED** (`AP-W1D-3`, `D1`–`D5`).
- §2: *"the grant in §1 was issued by the user in this session, acting in the **Code owner** role
  named at `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`."*
- §2 also: *"I cannot identify the Code owner as a named individual"*; §222: *"authorization issued
  by the session Code owner; **no named individual attested in the repository**."*

So an in-session, role-attributed grant **is** a valid instrument here, and it does **not** require
naming an individual. My earlier blanket conclusion that no in-session authorization mechanism
exists was **wrong**, and is corrected.

**The limit, which is decisive for A23.** The brief asks whether the role assignment preceded or
accompanied the grant. **It preceded it.** Ledger line 802 already carried the role in its Owner
column *before* any grant, with status `NOT GRANTED`:

```
| ID | Request | Owner | Status |
| **AP-W1D-3** | Implement the 4-way drift classification + the 10 regression tests (§3.5–3.6) | Code owner | **NOT GRANTED** |
```

The user then acted *in that pre-existing named role*. The role was not created by the grant.

**A23 has no equivalent row.** `PHASE11B_A10_D5_APPROVAL_READINESS.md:244,367` records
`backend_app/backend/redis_manager.py` as unowned, and `PHASE11B_A3_BLOCKER_REGISTER.md` §3 — the
only register table carrying an `Owner` column — has **no row** for the Redis lifecycle item. So
A23 cannot reuse W1D-12's procedure as-is: there is no role to act in. Attempting it would create
the authority claim the procedure is supposed to rest on.

**Record control, separated as the brief requires:**

| Record | Controls |
|---|---|
| `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` (Class-A ledger) | role ownership + status for W1D items |
| `PHASE11B_A3_BLOCKER_REGISTER.md` §3 | role ownership for blocker items (only `Owner`-column register) |
| `PHASE11B_A23E6_…PACKET.md` §3 | Redis implementation authorization (the response instrument) |
| `W1D-12`, `A23E11`, `A28`, `A28B`, `D10`, `D11`, A25/A26 | **report findings only**; confer nothing |

---

## 4. Branch determination — B

**Authority to create the owner-role assignment is NOT established.** No record authorizes this
session, or any session, to add a row to `PHASE11B_A3_BLOCKER_REGISTER.md` §3. Per the brief's
Branch B, the row below is **PROPOSED AND NOT ACCEPTED**. It was **not** written to any register.

### 4.1 Proposed row — draft only, not accepted

Using the existing §3 schema (`| ID | Sev | Component | Finding | Evidence | Acceptance test | Owner | Status |`):

```
| A23-P1 | P1 | backend_app/backend/redis_manager.py | Redis pool lifecycle G1/G2/G3 + A23-01: failed
initialize() leaks 3 clients; rebuild leaks the outgoing trio; close() neither resets _connected nor
unbinds, leaving reconnect-after-shutdown dead | staging/PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md
§6; A23E6 §3.B | tests/test_a23_redis_pool_lifecycle.py (8 cases, server-free) | Code owner |
NOT GRANTED |
```

Deliberately split, per the brief's instruction not to bundle: this row would establish **only the
role**, with status `NOT GRANTED`. Implementation authorization remains a separate act in A23E6 §3.

### 4.2 Minimum role-attributed decision needed to make it valid

> "`backend_app/backend/redis_manager.py` is assigned to the **Code owner** role, on the same basis
> as `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`. I authorize adding the `A23-P1` row of
> `staging/PHASE11B_A23E14_…md` §4.1 to `PHASE11B_A3_BLOCKER_REGISTER.md` §3 with status
> `NOT GRANTED`. This assignment confers no implementation permission; that remains A23E6 §3."

Then, separately, in the W1D-12 form:

> "Acting in the **Code owner** role for `backend_app/backend/redis_manager.py`, I grant A23E6 §3.B
> (`D-A23C-1`) and §3.C (`D-A23C-7`), select §3.D Option \<A|B\>, acknowledge §3.E, select §3.F
> \<12a|12b|12c\>, and grant §3.G implementation authorization."

I did not fabricate a name, channel, role assignment or approval, and contacted no one.

---

## 5. A23 gate matrix — evidence per gate

Measured after the commit with the codepoint probe A23E11 §4 established as correct (ASCII
`[ ]`/`[x]` patterns return 0/0 against this form and must not be used):

```
U+2610 (empty)            → 12
U+2611/2612/2713/2714/2718 → 0
_{6,} (blank fields)      → 6
```

| Gate | Decision | Status | Direct evidence |
|---|---|---|---|
| **E-1** | Owner + scope | **UNRESOLVED — no grantor on record** | A23E6 §3.A: 3 blank fields; A10 `:244,367` unowned; A3 §3 has no row |
| **D-A23C-1** | Production implementation | **UNRESOLVED** | §3.B: ☐ GRANTED ☐ REFUSED, both unchecked |
| **D-A23C-7** | Test file | **UNRESOLVED** | §3.C both unchecked; path **ABSENT** on disk |
| **D-A23C-3** | Proxy Option A/B + co-decision | **UNRESOLVED** | §3.D both unchecked, co-decision field blank |
| **D-A23C-8a** | Core-cache acknowledgement | **UNRESOLVED (not acknowledged)** | §3.E ☐ unchecked |
| **D-A23C-9** | AUTH/CONN/TENANT ack + attribution | **UNRESOLVED — OPEN in register** | §3.G 2 ☐ + 2 blank fields |
| **E-5 / D-A23C-12** | Concurrency disposition | **UNRESOLVED** | §3.F all of 12a/12b/12c unchecked |
| **E-6** | Real-Redis lane | **UNEXECUTED, no disposition** | no local server; no authorized CI dispatch |

**Granted 0 · Conditionally granted 0 · Refused 0 · Unresolved 8.** Nothing was inferred from the
checkbox count, the now-clean target file, the `ecd23a9e` commit, the W1D classifier grants, a role
label, commit authorship, the `…IMPLEMENTATION_REPORT` filename, or this brief's instruction to
continue.

---

## 6. Redis P1 — not implemented; plan preserved

No edit was made. The implementation-ready findings carry forward unchanged (target byte-identical):

- `initialize()` builds `cache`/`queue`/`events` into **locals**, pings all three, commits to
  `self.*`; the `except` block never closes the locals (**G1**) and the commit replaces the prior
  trio without closing it (**G2**).
- `close()` closes under `if self._cache:` guards but neither resets `_connected` nor unbinds
  (**G3**), which with the stale flag produces the **A23-01** reconnect-after-shutdown dead path.
- **Binding constraint:** `tests/test_redis_debounce.py::_make_fresh_manager` builds its subject via
  `object.__new__(RedisManager)` and sets exactly seven attributes. Change 1 must therefore track
  created clients in a **local variable**, never a new instance attribute — otherwise A22's suite
  breaks without being edited, which is forbidden.
- Change 3 also repairs `core/cache/redis_manager.py`'s `connect()` guard
  (`if self._redis_manager and not self._redis_manager._cache`) without editing that file.
- **No concurrency safety is claimed.** The third `initialize()` caller remains unlocked.

### 6.1 D-A23C-3 — audit complete, with two corrections to A23E6's impact list

38 proxy call sites across six in-scope modules: **34 guarded, 4 unguarded.**

| Module | Sites | Guarded | Unguarded | Notes |
|---|---|---|---|---|
| `global_safety.py` | 11 | 11 | 0 | `:246` and `:317` **already translate to `RedisUnavailable`** |
| `audit_trail.py` | 7 | 7 | 0 | but `record_or_raise` (`:850`) deliberately re-raises |
| `load_protection.py` | 7 | 7 | 0 | handlers `:132, 222, 237, 251, 309` |
| `alerting_system.py` | 4 | 4 | 0 | `:212, 396` |
| `distributed_rate_limiter.py` | 3 | 3 | 0 | `:205, 290, 347` |
| **`dag_tasks.py`** | 6 | 2 | **4** | `:591, 595, 599, 603` bare; reached from `list_tasks` (`:166`), which also has no handler |

**Correction 1:** `core/rate_limiter.py` must be **struck** from A23E6's list — it imports
`from backend_app.core.cache import redis_manager` (`:36`) and has **0** backend-proxy calls. Six
modules, not seven.

**Correction 2 (bearing on the choice):** Option A is cheaper than A23E6 implies, because
`AttributeError` is an `Exception` and `global_safety.py`'s two kill-switch read paths already
convert it to `RedisUnavailable` — the exact typed error Option B would introduce — with no code and
no edit to a file that was ` M` at the time. The only genuine residual exposure is `dag_tasks.py`'s
four bare helpers plus the strict audit path. A third shape the form does not offer —
**Option A plus a narrow handler in `dag_tasks.py`** — would close that at the cost of one more
file grant. **I am not selecting an option.**

---

## 7. Parallel workstreams

### 7.A — D7-1 vault signature compatibility · VERIFIED DEFECT, authorization ABSENT

Signature (`backend_app/backend/api_key_vault.py:177`):

```python
def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:
```

Two positional parameters; **no `access_token`**. Three call sites pass it as a keyword:

| Site | Containment |
|---|---|
| `backend_app/routers/orders.py:789-793` | **not** inside a `try` at the call |
| `backend_app/routers/orders.py:1095-1099` | inside `try` (`:1094`) |
| `backend_app/routers/market.py:50-54` | inside `try` (`:49`) |

Each raises `TypeError: load_decrypted_keys() got an unexpected keyword argument 'access_token'`.
**New this pass:** `routers/exchange.py:993-996` passes only `user["id"], body.exchange_id` and is
**correct** — so the count is firmly **three**, and `exchange.py` needs no change. Seven other call
sites (`ws_routes.py:814`, `master_executor.py:252`, `portfolio_cache_updater.py:160`) are
two-argument and correct.

Masking mechanism, per D8-5: `tests/test_market_none_keys_fix.py:13`'s double declares
`access_token=None`, so the double matches the broken sites while the real class does not.

**Authorization ABSENT** — D8 Track C *"NOT GRANTED"*, D10 §5 *"Authorization: ABSENT. No file
touched"*, D11 §4 *"No grant exists for the three router call sites in any authoritative record."*
**No patch applied.** Blocking static check for any future patch is **flake8** (`static-audit`);
`black`/`isort` run `continue-on-error: true` and have never passed (D11 §5.3).

### 7.B — W-6 exchange error classification · BOTH RISKS VERIFIED, authorization ABSENT

`backend_app/backend/exchange_executor.py:_handle_ccxt_error` (`:1224-1256`) is an **ordered
substring ladder** over the lowercased redacted message:

```python
if "insufficient" in error_str or "balance" in error_str:          → InsufficientFundsError
if "rate limit" in error_str or "too many requests" in error_str:  → RateLimitError
if "symbol" in error_str or "market" in error_str:                 → InvalidSymbolError
if "network"/"timeout"/"connection" in error_str:                  → NetworkError
if "authentication"/"apikey"/"key" in error_str:                   → AuthenticationError
retryable = any(w in error_str for w in ["timeout","network","temporarily","busy"])
```

**Risk 1 CONFIRMED, and worse than reported.** The `symbol`/`market` rung precedes the
`timeout`/`network` rung, so a timeout naming a symbol returns `InvalidSymbolError` — a determinate
rejection — instead of the retryable `NetworkError`. Beyond symbols: a ccxt message echoing a
market-data URL such as `/v5/market/tickers` matches `"market"` **literally**, so *any* error
quoting such a path misclassifies regardless of its true cause. That is a broader trigger than
W-6's "message containing a symbol".

**Risk 2 CONFIRMED.** `"key" in error_str` matches as a bare substring — `keyword`, `keyspace`,
`monkey` — so an unrelated failure becomes `AuthenticationError`. It is partially shadowed by the
earlier rungs, so it fires only when no earlier token matches.

Direction of failure, stated honestly: risk 1 turns a transient error into a permanent one (it does
not weaken a safety gate, but it corrupts the retry contract and the user-facing reason); risk 2
mislabels unrelated failures as authentication failures. **Registered P0**, owner *execution-state +
coordinator*, **open** (D7 §405). D11 measured W-6's default-lane coverage at **zero**. No grant →
**no test added, no code changed.**

### 7.C — QUOTA-01 · already measured by D11; not re-run

D11 records QUOTA-01 moved to **TESTED: 50 passed / 5 skipped**, with **real-Redis UNEXECUTED**.
Re-running would duplicate completed work, and no authorized isolated Redis service exists. A28B
§214 adds that the only slot-guard call site is in `routers/exchange.py`, another workstream's file.
**Status: OPEN, logic tested, real-Redis unproven.** Skips are not passes.

### 7.D — TENANT-01 / D6-C · FORCE RLS absence VERIFIED (static)

A repository-wide search for `FORCE ROW LEVEL SECURITY` returns matches **only** in
`staging/*.md`, `staging/*.json` and `BILLING_SECURITY_VERIFICATION.md` — i.e. prose and
recommendations. **Zero matches in `migrations/*.sql`, `backend_app/migrations/*.sql`, or
`backend_app/alembic/versions/*.py`.** It exists nowhere as executable SQL.

Corroborating prior records (not re-verified here): `AUTH_RLS_AUDIT.md` §11.1 reports the same
negative grep plus live confirmation `relrowsecurity = true, relforcerowsecurity = false`;
`PHASE11_READINESS_EVIDENCE.json` SEC-03 states the app connects as owner `postgres`, leaving all
nine execution-table policies **inert**; `PHASE11A_READINESS_SCORECARD.json` has
`tenant_force_rls: OPEN`.

**Separation required by the brief:** the *absence of executable SQL* is a static source fact
established here. Whether production enforcement differs needs live database evidence, which was
**not** gathered — no database connection was made, and D6-C's query remains **NOT GRANTED** (D8
Track B). **Tenant isolation is NOT certified.**

### 7.E — Workers / W-3 / W-4-W-5 / D7-2 · two defects VERIFIED

**Reconciliation and expiry workers are not started.** A search of `backend_app/main.py` for
`ExchangeReconciliationService` and `MarketplaceExpiryWorker` returns **no matches**. Neither is
referenced anywhere in the application entrypoint, so neither runs. This independently confirms
`PHASE11A_READINESS_SCORECARD.json`'s `exchange_reconciliation_service_WIRED_AND_RUNNING:
NOT_STARTED`. Per the brief, a class definition is not a running worker — and here there is not even
a reference.

**D7-2 CONFIRMED.** `backend_app/backend/portfolio_cache_updater.py:151` executes
`from backend_app.backend.key_manager import KeyVault`, and `backend_app/backend/key_manager.py`
**does not exist** (`Test-Path` → `False`). Per D8-3 the `ImportError` is swallowed per user, so the
portfolio cache is **silently dead** while `/health/services` still reports `ACTIVE` — a false-green
health signal, which is the security-relevant part.

**Watchdog (W-3-adjacent), from source comments at `order_watchdog.py:1084-1088`:**
`app_state.vault.get_exchange_client` does not exist anywhere; `APIKeyVault` exposes
`store_exchange_keys`, `load_decrypted_keys`, `delete_exchange_keys`, `list_connected_exchanges`,
`get_user_tier`, `rotate_key`. The call raises `AttributeError`, converted to `None` by the
surrounding `except`. All W-3/W-4/W-5/D7-2 items are **NOT GRANTED** (D8 §445-449). No edits.

### 7.F — Governance consistency

| Observation | Evidence |
|---|---|
| **A clean target file now exists at a committed HEAD** — the highest-risk misreading available. `ecd23a9e` committed `redis_manager.py` **unchanged**; this is not a grant | §2.2 |
| The prior staged digest `2e994018…` is **unreproducible** (staged = 0) and must be retired as a continuity anchor; blob hashes are now the stable anchors | §2.1 |
| `pytest.ini` still sets `pythonpath = . aerora_quant_backend_updated_final1 backend`, naming a **non-existent** `backend/` directory — the stale entry that made A23's seven dead script imports look plausible | prior A23R §3, unchanged |
| `D-A23C-8b` **VOID**, `D-A23C-10` **RESOLVED** (`redis==5.0.8`, `aclose()` present, no floor raised), `D-A23C-11` **CLOSED** — must not be reopened | A23E6 §2 |
| A conditional grant with an open condition persists: W1D `D5` **CONDITIONALLY GRANTED**, production-audit stakeholder sign-off outstanding | W1D-12 §1, §221 |
| Dashboard `E-1`…`E-6` are **escalation items**, not concurrency options; concurrency is `D-A23C-12a/b/c`. My own earlier reports collided on this and are corrected | A25 dashboard `:282-287`; A23E6 `:44-46` |

**No governance record was edited.** The procedure and authority for editing one are not established
(§4), so no correction was applied to any register — including the two corrections in §6.1, which
are recorded here for A23E6's author to adopt.

---

## 8. Tests and checks

**Executed: none.** `D-A23C-7` is unresolved, so no authorized lifecycle test file exists to run,
and running unrelated suites would produce activity without evidentiary value for this pass's
question. Nothing is reported as passing that did not run.

| Lane | Disposition |
|---|---|
| 8 lifecycle cases | **NOT RUN** — file absent, `D-A23C-7` unresolved |
| `tests/test_redis_debounce.py` | **NOT RUN.** Last recorded 9 passed / 1 xfailed (A22/A23R) — prior evidence, not re-verified. `test_r6_d` remains `XFAIL(strict)`, untouched |
| Redis/proxy, kill-switch, cache, exchange regressions | **NOT RUN** — no authorized change to validate |
| flake8 `static-audit` | **NOT RUN** — no patch produced |
| QUOTA-01 | **NOT RE-RUN** — D11 measured 50 passed / 5 skipped |

---

## 9. Evidence limitations

| Lane | Status | Reason |
|---|---|---|
| **Real Redis** | **UNEXECUTED** | No local server; `D-A23C-6` / `E-6` ungranted; starting a service and installing dependencies both out of scope. Mocks would not prove real-Redis behaviour |
| **Database / D6-C** | **UNEXECUTED** | No connection made; D6-C query **NOT GRANTED**. FORCE-RLS finding is static-source only |
| **Venue sandbox** | **UNEXECUTED** | No credentials, no authorization; W-6 evidence is source-level only |
| **Production** | **UNEXECUTED** | No access and none sought |
| **CI / deployment** | **UNEXECUTED** | No dispatch, no deploy |

---

## 10. Remaining blockers, ranked by risk

| # | Blocker | Risk | Minimum next action |
|---|---|---|---|
| 1 | **TENANT-01** — RLS enabled-not-forced, ORM connects as table owner, `tenant_db_context.py` unwired | **Highest.** Isolation on money tables is application `WHERE` clauses only | Wire the GUC per request **or** connect as a non-owner role, *then* a migration issuing FORCE RLS behind a flag. Needs the tenancy owner; high blast radius |
| 2 | **D7-2** — portfolio cache silently dead behind a false-green `/health/services` | High. A false health signal is worse than a visible outage | Portfolio-cache owner decides among D8 §4.3's three options |
| 3 | **D7-1** — 3 `access_token=` sites raise `TypeError` on live data paths | High, narrow. Spec machine-verified: 3 deletions + 1 `Signature.bind` test, flake8-neutral | Router owner grants Packet 6 |
| 4 | **W-6** — timeout relabelled a determinate rejection; broad `key` match | P0 per register, 0 default-lane coverage | Execution-state owner + coordinator authorize reordering the ladder and tightening `key`, plus 3 regression tests |
| 5 | **Workers not started** — reconciliation and expiry absent from `main.py` | Medium-high; no reconciliation runs | Service/deployment owner wires the lifespan with leader election |
| 6 | **Redis A23 P1** | Medium for launch, high for production reliability | §4.2 role assignment, then the A23E6 §3 grants |
| 7 | **E-6 real Redis** | Blocks R6 closure and QUOTA-01's final evidence | Authorize an isolated server or a CI dispatch |

---

## 11. Preservation audit

| Check | Exit value |
|---|---|
| Branch / `HEAD` / `origin/main` | `main` / `ecd23a9e` / `ecd23a9e`, 0 ahead 0 behind |
| staged / porcelain / unmerged / worktrees | 0 / 21 / 0 / 1 |
| `backend_app/backend/redis_manager.py` | blob `d7b0e811b615ee13ba089b5cbe9d790161176de2`, **clean** |
| `backend_app/core/global_safety.py` | blob `4b2a1fc52748f3e90e86083b43f9e8c232629c3d` |
| `tests/test_redis_debounce.py` | blob `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc`, untouched |
| `tests/test_a23_redis_pool_lifecycle.py` | **ABSENT** |
| A23E6 form | 12 ☐ / 0 marked / 6 blank — unchanged |

**Files changed by this pass: 1 — this report.** No production file, test, register, decision form
or another session's artifact was created, modified or deleted. No stage, commit, push, reset,
clean, stash, checkout, branch switch or index mutation. No dependency install, no service start, no
database, exchange, cloud, CI or deploy operation. The 19 untracked `staging/` artifacts and 2
modified files belonging to concurrent sessions were left exactly as found.

---

## 12. Final disposition

**Redis P1 — implementation: NOT STARTED. Acceptance: NOT APPLICABLE.** Nothing was implemented, so
nothing can be accepted. Granted 0, refused 0, unresolved 8.

Independent work completed to the limit authorization allows: five defects verified at primary
source (D7-1 count corrected to three; W-6 both risks, one broader than reported; TENANT-01 FORCE
RLS statically absent; D7-2 missing module; workers absent from the entrypoint), two corrections
supplied to A23E6's proxy impact list, and the W1D-12 precedent resolved to its exact limit.

P2, P3, P4, R6, real-Redis acceptance, QUOTA-01, TENANT-01 and deployment remain separate
workstreams. **Live trading remains OFF / NOT CERTIFIED** — no gate in this pass bears on
certification, and none was passed.
