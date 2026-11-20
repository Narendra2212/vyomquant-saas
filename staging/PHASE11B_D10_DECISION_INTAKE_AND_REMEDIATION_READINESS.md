# Phase 11B-D10 — Decision Intake, Credential-Safety Assessment, Remediation Readiness

**0 source/test files modified. Tests executed: NONE.** Phase 7 executed nothing because no
new authorization exists. **W-1b OPEN · TENANT-01 OPEN · QUOTA-01 OPEN · live trading OFF /
NOT CERTIFIED · paper track 53% unchanged and unrevised.**

No approval packet was duplicated — `staging/PHASE11B_D9_DECISION_PACKET.md` stands as the
single routing artifact. No authoritative register was edited.

---

## 1. Fresh baseline and concurrent drift

| Property | D9 | **Now** | Drift |
|---|---|---|---|
| Branch / `HEAD` | `main` / `074b772a…` | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **none** |
| `origin/main...HEAD` | `0 0` | `0 0` | none |
| Worktrees / unmerged | 1 / 0 | **1 / 0** | none |
| Staged entries / digest | 22 / `2e994018…7152` | **22 / matches** | **none** |
| Working tree | 262 | 262 (140 untracked) | none |

**Every fingerprint relevant to a D10 phase is unchanged**, so D8/D9 source findings are
reused rather than re-scanned:

| File | SHA-256 (16) |
|---|---|
| `backend_app/backend/api_key_vault.py` | `FE42F4F3E63BF417` |
| `backend_app/backend/exchange_executor.py` | `81ED5679BB93AFB0` |
| `backend_app/backend/order_watchdog.py` | `507F5C85A7984200` |
| `backend_app/main.py` | `DE1CCF9ADFFFBA0C` |
| `backend_app/core/state.py` | `6AC55227B378EE5E` |
| `backend_app/core/models/execution_record.py` | `DE6C95CE08AEF318` |
| `tests/test_d5_watchdog_adjudication_repair.py` | `AB918192B4801F13` |
| `backend_app/routers/orders.py` | `51F724F6202A90AD` |
| `backend_app/routers/market.py` | `8D43EC83A3C86D1A` |
| `backend_app/backend/portfolio_cache_updater.py` | `1BAB674F48375CE7` |
| `backend_app/backend/event_router.py` | `0F0F8D31523181BF` |
| `backend_app/core/dependencies.py` | `DFEE4C13A7FA7CB8` |
| `tests/test_market_none_keys_fix.py` | `FF2C145A9806FA91` |

Concurrent drift: three new staging artifacts (§2), no source drift on any D10 target.

---

## 2. Decision intake — three new artifacts, zero new decisions

Scanned only artifacts created after D9 (13:39:50). No repository-wide approval sweep.

| Artifact | Written | Hits on D10 items | Authority self-declared | Verdict |
|---|---|---|---|---|
| `PHASE11B_W1D14_D5_SIGNOFF_GATE_REPORT.md` | 13:44:51 | **0** | "**NO IMPLEMENTATION — SIGN-OFF REQUIRED**"; "no affirmative D5 production-audit sign-off anywhere … no file was edited" | not a decision |
| `PHASE11B_A26_DECISION_SHEET.md` | 13:44:52 | 1 | "**No approval is created here on anyone's behalf.**" | not a decision |
| `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` | 14:22:11 | **0** | "**THIS DOCUMENT IS NOT AN APPROVAL.** It was authored by the implementation session and carries no authority… No status was changed to `GRANTED`." | not a decision |

Assessed against the five intake criteria:

1. **Who decided** — nobody. All three are author-side artifacts.
2. **Record establishing authority** — none; A23E6 disclaims authority in its own first line.
3. **Decision/files covered** — none of my items. A26's single hit (line 240) is
   informational: it records that `OrderWatchdog` is already wired at `main.py:432` with
   30 s/60 s/300 s. That is a fact I established in D4, not a grant.
4. **Co-approvals** — n/a.
5. **Classification** — all three are **requests for more information / readiness
   statements**, not approvals, refusals or observational evidence.

**Carry-forward: every D9 blocker is unchanged.** A26 also restates paper track **53%**,
which this phase does not touch.

---

## 3. D6-D / D6-C evidence readiness

Source inspection confirms the D9 requests are still technically correct and still
minimal.

### 3.1 Credential identity path, traced end to end

```
JWT                     dependencies.py:315  user_id  = payload["sub"]
                        dependencies.py:316  tenant_id = payload.get("tenant_id")
                                              or payload["app_metadata"].get("tenant_id")
                                              or user_id           ← the divergence point
                        dependencies.py:323  user = {"id": user_id, "tenant_id": tenant_id, …}

write  exchange_keys    routers/exchange.py:429  store_exchange_keys(user_id=user["id"], …)
                                                 → TEXT column, NO FK,
                                                   RLS auth.uid()::text = user_id
read   vault            api_key_vault.py:177  load_decrypted_keys(user_id, exchange_id)
                        :179-180  _validate_id both  (^[a-zA-Z0-9\-_]{1,128}$)
                        :183-188  .eq("user_id", …)  ← SERVICE-ROLE client ⇒ RLS bypassed
                        :194-198  returns {api_key, secret_key, password}
construct executor      master_executor.py:296  get_exchange_executor(…, user_id=self.user_id)
                                                 ← vault's secret_key → factory's api_secret
retain                  exchange_executor.py:1081-1083  _api_key/_api_secret on the instance,
                                                 cached in _executors, never drained
record                  execution_records.tenant_id  uuid, FK → profiles(id)
```

### 3.2 D6-D — the two boot-log booleans are sufficient

| Question | Answered by |
|---|---|
| Is the object a real vault? | `type(app_state.vault).__name__` — the only reliable discriminator, because `MockEngine.__getattr__` (`state.py:51-55`) answers **any** name with a noop, so `hasattr` cannot distinguish |
| Did the **import** fall back? | `Failed to import APIKeyVault` — emitted by `safe_import` (`state.py:63-64`) |
| Did the **construction** fall back? | `APIKeyVault initialization failed, using mock` — emitted by `_safe_init` (`state.py:108-109`) |

Those are the **only two** silent-substitution emitters on this path, so the two booleans
cover both failure modes, and the type name settles the outcome regardless of cause.
**Sufficient — no additional observation needed.** Deployment state is **not** claimed from
local source.

### 3.3 D6-C — the SQL is correct, and the two requests answer different questions

- The join `k.user_id = e.tenant_id::text` is the right direction and the right cast:
  `tenant_id` is `uuid`, `exchange_keys.user_id` is `TEXT`. A `LEFT JOIN` from
  `SELECT DISTINCT tenant_id` with `FILTER (WHERE k.user_id IS NULL/NOT NULL)` counts
  tenants, not key rows — correct for the question "does every executing tenant have a
  credential row under its own id".
- **Count semantics caveat worth stating to the DB owner:** `exchange_keys` is unique on
  `(user_id, exchange_id)`, so a tenant with keys on two venues produces two join rows.
  `tenants_with_keys` therefore counts *matched join rows*, not distinct tenants, unless the
  `LEFT JOIN` is wrapped in a `DISTINCT` or an `EXISTS`. Only `tenants_without_keys = 0`
  is load-bearing for the mapping, and that count is unaffected.
- **The two requests are genuinely separate.** Missing credential rows (Packet 2) and JWT
  claim divergence (Packet 3) are different failures: a tenant can have a perfectly matching
  key row while the *token* that produced the execution carried a `tenant_id` claim
  differing from `sub` — in which case the rows match for the wrong reason. Claim divergence
  is **not in any application table**; it lives in the issuer or
  `auth.users.raw_app_meta_data`.
- **No identifier exposure:** all four values are integers. No `tenant_id`, `user_id`,
  email or token content is requested or returned.

**Not executed.** No database query was run; the only reachable server is the provisioned
test cluster with no real connected accounts, so the result would be vacuous.

### 3.4 Evidence checklist

| Claim | Established by source inspection | Needs deployed logs | Needs authorized DB / auth-platform |
|---|---|---|---|
| Two silent vault-substitution paths exist and `hasattr` cannot detect them | ✔ | — | — |
| Which vault object a deployment actually holds | — | **✔ (D6-D)** | — |
| `tenant_id` and `user_id` originate from the same claim *only when* no tenant claim is issued | ✔ (`dependencies.py:316`) | — | — |
| Whether any issued token carries a divergent tenant claim | — | — | **✔ (Packet 3, auth/platform)** |
| Whether every executing tenant has a credential row under its own id | — | — | **✔ (Packet 2, DB owner)** |
| The vault bypasses RLS (service-role client) | ✔ (`api_key_vault.py:80`) | — | — |

---

## 4. D6-A / D6-B / W-1b dependency matrix

Re-traced at this baseline; all findings unchanged from D6–D8.

### 4.1 D6-A — credential residency facts

| Fact | Evidence |
|---|---|
| Decrypted credentials would enter a long-lived API process | the watchdog runs in the API process (`main.py:421-437`), 30 s loop |
| Cached executors retain credential references | `exchange_executor.py:1081-1083` holds `_api_key`/`_api_secret`; `_executors` (`:2323`) keys on a credential digest |
| `release_exchange_executor` callers | **zero in `backend_app`** — only its own definition at `:2432`; it pops the dict without `await disconnect()` |
| Executor disconnection / shutdown drain | **not implemented.** `main.py` drains the *other* pool (`connection_engine.close_all_exchanges()`); nothing drains `_executors`. `master_executor.py:886-889` closes only the `ConnectionEngine` bridge |
| Mock-vault fallback at deployed startup | silent: `logger.warning` only, at both `state.py:63-64` and `:108-109`. A deployed boot *does* fail on **missing env vars** (`config.py:415-422` + `:496`), but not on an `ImportError`, a `create_client` failure, or a present-but-malformed Fernet key |

### 4.2 D6-B — connection lifecycle facts

| Fact | Evidence |
|---|---|
| Lookup contract | `_get_executor_for_order` is a plain `def` (`order_watchdog.py:1051`); D5 asserts the invariant in both directions |
| `connect()` / `disconnect()` called? | **never** on an executor anywhere in `backend_app`; every `.connect()` hit is `ConnectionEngine`, websockets, event publisher/listener or alembic |
| Network I/O in the loop today | `_refresh_order_status` → `get_order_status`; `_adjudicate_ambiguous_execution` → `find_order_by_client_order_id`. With `_exchange = None` the latter degrades to `success=False, status="error"` — fail-safe, useless |
| Retry / timeout / cancellation | `2 ** attempt` backoff, `max_retries=3`, **no per-call timeout**; shutdown is a bare `_watchdog_task.cancel()` (`:160-171`) |
| D5 tests affected by sync→async | exactly: `test_the_executor_lookup_is_synchronous_and_no_call_site_awaits_it`; `test_every_awaited_self_method_in_the_module_is_actually_a_coroutine`; and only if the persist helper is touched, `test_the_persist_helper_exists_is_synchronous_and_is_not_awaited`. The 15 behaviour tests inject the executor and are unaffected |

### 4.3 The four W-1b gates

| Gate | Decision-maker | Acceptance evidence | State |
|---|---|---|---|
| **1 · D6-D** | platform/deployment | vault type + 2 boot-log booleans, per environment | **NOT SUPPLIED** |
| **2 · D6-C** | DB owner **and** auth/platform | `tenants_without_keys = 0` **and** claim-divergence count `= 0` | **NOT SUPPLIED** |
| **3 · D6-A** | security/credential + coordinator | yes/no on residency; residency rule; named lifecycle owner; mock-fatal yes/no | **NOT DECIDED** |
| **4 · D6-B** | watchdog + execution-state | named connect owner; timeout/cancellation rule; sync-or-async; ack of the three tests | **NOT DECIDED** |

Gates 1–2 are observational and can be answered in parallel; 3–4 cannot be answered on
fact until 1–2 are in. **No gate may be substituted by a passing test.** Nothing was
weakened to make the integration appear complete.

---

## 5. D7-1 — patch readiness

**Authorization: ABSENT. No file touched.** Re-verified at this baseline:

| Item | Current |
|---|---|
| Real signature | `api_key_vault.py:177` — `def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict` · two positional params, no `access_token`, no `**kwargs` |
| Invalid kwarg | `routers/orders.py:792`, `routers/orders.py:1098`, `routers/market.py:53` — all three still `access_token=user.get("access_token"),` |
| Guard to preserve | `routers/market.py:56` — `if not keys or not isinstance(keys, dict):` **present** |
| Valid two-arg sites to preserve | `routers/exchange.py:993`, `api_ws/ws_routes.py:814`, `master_executor.py:252`, `portfolio_cache_updater.py:161` |
| Isolation | `orders.py`, `market.py`, `test_market_none_keys_fix.py` have **no unstaged diff** — cleanly isolatable |

### 5.1 The test design, chosen so it cannot reproduce the defect

The existing double is the reason the incompatibility survived:
`tests/test_market_none_keys_fix.py:13` declares
`load_decrypted_keys(self, user_id, exchange_id, access_token=None)` — **the double matches
the broken calls while the real class does not.** A new double would repeat that mistake.

The regression test therefore binds against the **real** callable and never instantiates a
vault:

```python
from inspect import signature
from backend_app.backend.api_key_vault import APIKeyVault

SIG = signature(APIKeyVault.load_decrypted_keys)

# Each tuple is (args, kwargs) as the call site actually passes them.
def test_every_vault_call_site_binds_against_the_real_signature():
    SIG.bind(None, "user-id", "binance")                    # the valid two-arg form
    with pytest.raises(TypeError):
        SIG.bind(None, "user-id", "binance", access_token="t")   # the defect
```

No network, no Supabase client, no credential — `signature()` reads the function object, so
the test runs without the three environment variables `APIKeyVault.__init__` requires.

**Scope discipline:** delete exactly three lines; add exactly this test; do **not** add
`access_token` to the vault signature (that would be a new credential contract).
Rollback: revert three lines, delete one test file.

---

## 6. D7-2 — design comparison, decision not taken

Re-confirmed: `portfolio_cache_updater.py:151` imports
`backend_app.backend.key_manager`, which **does not exist**; it is the only reference in the
tree. Reached via `start()` → `_update_loop()` → `_update_all_portfolios()` →
`_update_user_portfolio()` **only when a user is registered**; the `ImportError` is swallowed
by the per-user handler at `:134` (`"❌ Failed to update portfolio for {user_id}"`), so the
loop survives and **the portfolio cache is silently nonfunctional** while
`/health/services` still reports `ACTIVE` (a boot-time snapshot).

| Option | Initialization | Credential lifetime | Mock exposure | DI | Fits the existing contract? |
|---|---|---|---|---|---|
| **1 · `APIKeyVault()` direct** | raises `EnvironmentError`/`ConnectionError`/`ValueError` on bad config — **fails loud** | per call; dict consumed immediately | **none** — no `safe_import`/`_safe_init` wrapper | none; constructed in the loop | matches the current call shape (`load_decrypted_keys(user_id, exchange_id)`); but creates a **new** direct-construction site in a background process, which falls inside D6-A's scope |
| **2 · `get_vault()` / `app_state.vault`** | never raises — may be `MockEngine` | per call | **full** — returns `None` silently; `keys["api_key"]` then raises `TypeError` | the convention every working path uses (`master_executor.py:127`) | matches the call shape; inherits the D6-D exposure |
| **3 · `core.credential_vault.CredentialVault`** | `get_credential_vault()`; `RuntimeError` without `MASTER_ENCRYPTION_KEYS` | per call, async | none | factory | **does not fit** — `async def get_credential` with a different return shape; feeds no ccxt client today |

**Decision required from the portfolio-cache owner, with the credential owner on the
residency implication of option 1.** Not chosen here. No new construction path created, no
background-loop behaviour changed. Note again that this module was the only candidate
precedent for "a background loop constructing the vault directly", and it has never worked.

---

## 7. W-3, W-4/W-5, W-6, TENANT-01, QUOTA-01

### 7.1 W-3 — NOT GRANTED (two owners), targets re-confirmed

Line numbers re-measured rather than trusted:

| Site | Current line | Predicate |
|---|---|---|
| `order_watchdog._find_open_orders` | **`:370`** | `ExecutionRecordModel.order_id.isnot(None)` |
| `order_watchdog._find_stale_orders` | **`:712`** | `ExecutionRecordModel.order_id.isnot(None)` |
| `event_router._find_execution_by_order_id` | **`:135`** | `ExecutionRecordModel.order_id == order_id` |

Required behaviour: **coalesce, never swap.** `order_id` carries
`fk_execution_records_order_id` → `orders(id)`; `venue_order_id` deliberately carries **no
FK**, and migration `030_execution_venue_order_id.sql` contains **35** FK/raise-related
lines whose postflight aborts if an FK is ever added. No migration change, no column swap.

**W-3 stays separate from W-1b:** selecting a row does not establish that the venue can be
queried about it.

### 7.2 W-4 before W-5 — ordering preserved, no exit rule exists yet

`resolve_stale_execution`'s CAS is still pinned to `upper(status::text) = 'EXECUTING'`, and
`submission_may_have_reached_venue` still refuses `UNKNOWN` unconditionally — so **no
explicit `UNKNOWN` exit rule is defined.** W-5 (widening `classify_stale_executions`) must
not precede it; nothing was implemented or reordered.

### 7.3 W-6 — escalated from "ladder order" to a proven classification inversion

`exchange_executor.py:1236-1256`, read directly this phase:

| Rung | Match | Class | `error_code` | `retryable` |
|---|---|---|---|---|
| 1 | `insufficient` \| `balance` | `InsufficientFundsError` | `INSUFFICIENT_FUNDS` | False |
| 2 | `rate limit` \| `too many requests` | `RateLimitError` | `RATE_LIMIT` | True |
| **3** | **`symbol` \| `market`** | **`InvalidSymbolError`** | **`INVALID_SYMBOL`** | **False** |
| **4** | **`network` \| `timeout` \| `connection`** | **`NetworkError`** | **`NETWORK_ERROR`** | **True** |
| 5 | `authentication` \| `apikey` \| `key` | `AuthenticationError` | `AUTH_ERROR` | False |

Class definitions confirmed at `:285-313`. **A ccxt timeout whose message embeds `symbol=`
— which ccxt messages routinely do — is classified `InvalidSymbolError` with
`retryable=False`, where the correct answer is `NetworkError` with `retryable=True`.** Rung 3
shadows rung 4 and **inverts the retry flag**. This is stronger than D4's framing, which
established the ordering but not the flag inversion.

Two precisions, so the severity is not overstated:

- `place_order` (`:1690-1699`) **discards `retryable`** — `OrderResult` has no such field —
  so today the order path collapses every class to `status="rejected"` either way. The
  immediate harm is to the **recorded classification and `error_code`**, and to any future
  consumer of `retryable`, not to the current branch decision.
- Rung 5's `"key" in error_str` is extremely broad (it matches any message containing the
  word "key", and substrings such as "monkey"). It sits below network, so its blast radius
  is smaller, but it is the same defect class in the same ladder. **New observation.**

`exchange_executor.py` hash `81ED5679BB93AFB0` — unchanged, W-6 untouched.
`PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md` concerns a **different** classifier
(0 references to `_handle_ccxt_error`, `W-6` or `exchange_executor`). **NOT GRANTED;** no
edit.

### 7.4 TENANT-01 — OPEN, and the register's characterisation is confirmed

- **`FORCE ROW LEVEL SECURITY` is applied nowhere.** The single occurrence across all
  migrations is a **comment** at `026_fee_records_row_level_security.sql:75`. So "RLS
  enabled-not-forced" (`PHASE11A_LAUNCH_GATE_REGISTER.md:26`) holds.
- **`tenant_db_context.py` remains unwired:** **zero** references from
  `core/dependencies.py`, `main.py` or any router. The only GUC write in the application is
  an inline `text("SET LOCAL app.current_tenant_id = :tenant_id")` at
  `transactional_execution_manager.py:225` — one module doing it directly, not the shared
  helper.
- It **compounds D6-C**: the vault's service-role client bypasses RLS, so the RLS policy on
  `exchange_keys` constrains client traffic but not the credential read.

**OPEN. Not closed, not re-scored.** No policy, role or migration inspected beyond source;
no production access.

### 7.5 QUOTA-01 — OPEN; implementation exists, current verification does not

- `exchange_connection_slot_guard` defined at `subscription_dependencies.py:1036`, with
  **one** call site: `routers/exchange.py:428`.
- Five test files exist: `test_quota01_enforcement_is_wired.py`,
  `test_quota01_exchange_slot_is_atomic.py`,
  `test_quota01_hard_enforcer_is_deprecated_in_code.py`,
  `test_quota01_metered_reservation_is_hard.py`, and the concurrent
  `test_a20_quota_refusal_semantics.py`.
- **Implemented ≠ verified.** None was executed this phase, and a historical result is not
  counted as current verification. The atomicity and metered-reservation suites are
  PostgreSQL-shaped by name; whether they execute was not measured.
- **Isolation note:** the only slot-guard call site lives in `routers/exchange.py`, which is
  one of the **22 staged entries** belonging to another workstream. Any QUOTA-01 change
  there would touch staged work and needs that owner's coordination.

**OPEN.** No commit, no deployment, no CI dispatch.

---

## 8. Files changed and tests executed

| | |
|---|---|
| Source files changed | **0** |
| Test files changed | **0** |
| Migrations / CI / deployment / shared config changed | **0** |
| Files written | **1** — this report |
| Tests executed | **NONE** |
| Authorization basis for changes | n/a — no approval exists for any D10 scope |

D5's suite last ran in D7 at these exact hashes (51 passed / 0 failed / 0 skipped,
PostgreSQL included). That is **historical** and is **not** counted as current
verification; nothing changed for it to verify.

---

## 9. Preservation audit and remaining risks

| Check | Result |
|---|---|
| `HEAD` / branch / divergence / worktrees / unmerged | `074b772a…` / `main` / `0 0` / 1 / 0 — unchanged ✔ |
| Staged index | 22 entries, digest `2e994018…7152` ✔ |
| All 13 inspected targets | hash-identical to D8/D9 ✔ |
| Staged, unstaged, untracked work | preserved (262 entries) ✔ |
| Stage / commit / push / reset / clean / stash / branch switch | **none** |
| Database query, Redis start, exchange contact, CI dispatch, deployment | **none** |
| Secrets read | **none.** `exchange_keys.json`, `.env` and backups were not opened; only variable *names* and reader code were inspected |
| Authoritative registers edited | **none** |
| Operational certification / paper score | **unchanged** — OFF / NOT CERTIFIED, 53% |

**Remaining risks, unchanged in substance:**

1. **D6-D is a live-path risk, not just a W-1b gate.** `master_executor.py:127` reads the
   same `global_state.vault`; with a mock, `keys["api_key"]` at `:255-257` raises
   `TypeError` on the live order path.
2. Credentials would become resident in the API process with **no eviction owner** —
   `release_exchange_executor` has no caller and does not disconnect.
3. Adjudication remains unreachable end to end: an executor cannot be obtained (W-1b), and
   if obtained would be unconnected (D6-B).
4. W-6 silently records timeouts as determinate `INVALID_SYMBOL` rejections with
   `retryable=False`.
5. The portfolio cache is silently nonfunctional while reporting `ACTIVE`.
6. TENANT-01: RLS enabled but never forced; the tenant GUC helper is unwired.

---

## 10. Smallest next action that unlocks the most safe progress

**Deliver Packet 1 (D6-D) to the platform/deployment owner.** One type name and two
booleans per environment.

It is the smallest possible response, it needs no database and no production data, and it is
the only item that simultaneously:

- closes W-1b gate 1 of 4;
- supplies the fact D6-A question 5 (mock-fatal policy) depends on;
- settles whether the **live order path** currently carries a `TypeError` at
  `master_executor.py:255-257`;
- tells the portfolio-cache owner whether D7-2 option 2 is even viable.

Second-highest, and fully parallel: **Packet 6 (D7-1)** — three line deletions plus one
`Signature.bind` test, on files with no unstaged diff, fully specified in §5 and reversible
in one revert.

---

**0 source/test files modified · 0 tests executed · 0 new decisions · W-1b OPEN (4 gates
unsupplied) · W-3, W-4/W-5, W-6, TENANT-01, QUOTA-01 all OPEN · no register edited, no
packet duplicated · shared `HEAD` and the 22-entry staged index preserved byte-for-byte ·
**live trading OFF / NOT CERTIFIED**, paper track 53% unrevised.**
