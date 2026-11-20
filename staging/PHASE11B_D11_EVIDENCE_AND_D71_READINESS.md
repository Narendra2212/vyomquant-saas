# Phase 11B-D11 — Parallel Evidence Collection, D7-1 Readiness, Verification

**0 source files modified · 0 test files modified · 3 read-only evidence files written.**
D7-1 authorization **ABSENT**, so no patch was applied. All independent work was carried to
completion.

**Net new this pass:** QUOTA-01 moved from *implemented, unverified* to **TESTED** (50
passed / 5 skipped, named gap); the D7-1 patch specification moved from *written* to
**machine-verified** by AST extraction plus `Signature.bind` execution; W-6's test coverage
was measured and is **zero in the default lane**.

**W-1b OPEN · live trading OFF / NOT CERTIFIED · paper track 53% unrevised.**

State vocabulary used strictly below: **OBSERVED** (read from source) · **TESTED** (a test
executed this pass) · **APPROVED** (an authoritative grant exists) · **IMPLEMENTED** (code
changed) · **DEPLOYED** (running in a deployed environment).

---

## 1. Baseline and concurrent-drift comparison

| Property | D10 | **Now** | Drift |
|---|---|---|---|
| Branch / `HEAD` | `main` / `074b772a…` | `main` / `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **none** |
| `origin/main...HEAD` · worktrees · unmerged | `0 0` · 1 · 0 | **`0 0` · 1 · 0** | none |
| Staged entries / digest | 22 / `2e994018…7152` | **22 / matches** | **none** |
| Working tree | 262 | 262 | none |
| 13 inspected targets | — | **drifted = 0 / 13** | **none** |

D10's findings were revalidated by fingerprint, not assumed. Because every hash matches,
the source findings are reused; everything labelled new below was measured this pass.

**Test configuration, read from the repository rather than assumed** (`pytest.ini`):
`testpaths = tests`, `addopts = -v --tb=short`, `pythonpath = . aerora_quant_backend_updated_final1 backend`,
`norecursedirs` excludes `tests/perf`. Markers `realaudit`, `property_scoreboard`,
`frontend_bundle`. Interpreter `.venv\Scripts\python.exe` 3.12.10, pytest 9.1.1.

**Lint/format/type reality:** no `pyproject.toml`, `setup.cfg`, `.flake8`, `mypy.ini`,
`tox.ini` or `.pre-commit-config.yaml` exists. `.github/workflows/01-pr-check.yml:43-47`
records that **flake8 is the blocking check** (`static-audit`), while `black` and `isort`
run with `continue-on-error: true` and have **never** passed — "`black backend_app` would
rewrite 350 files in one commit". So the applicable established check for any D7-1 patch is
**flake8**, not black (§5.3).

---

## 2. Decision intake — one new artifact, zero new decisions

Scanned only artifacts created after D10 (14:36). No repository-wide approval sweep.

| Artifact | Written | Hits on D11 items | Self-declared verdict |
|---|---|---|---|
| `PHASE11B_A28_LEDGER_GATE_DELTA.md` | 14:54:02 | **0** | "**OUTCOME: D-A27-1 NOT AUTHORIZED. The ledger was not edited.**" · "Live trading OFF / NOT CERTIFIED" · "PostgreSQL integration UNVERIFIED" |

Assessed against the intake criteria: no decision-maker, no record of authority, covers none
of D11's items, no co-approvals, and it classifies itself as a refusal of a **different**
workstream's request. **Every D10 blocker carries forward unchanged.**

---

## 3. D6-D — evidence request complete, evidence NOT received

### 3.1 The two fallback emitters, re-verified in source (OBSERVED)

| Emitter | Location | Fires when |
|---|---|---|
| `Failed to import APIKeyVault from api_key_vault: {e}` | `core/state.py:63-64`, inside `safe_import` | the bare-name module import fails → returns `lambda *a, **k: MockEngine("MockAPIKeyVault")` |
| `DEV_MODE: APIKeyVault initialization failed, using mock: {e}` | `core/state.py:108-109`, inside `_safe_init` | `APIKeyVault()` raises → returns `MockEngine("MockAPIKeyVault")` |

These are the **only two** silent-substitution emitters on this path, so the two booleans
cover both failure modes, and `type(app_state.vault).__name__` settles the outcome
regardless of cause. `hasattr` cannot be used: `MockEngine.__getattr__` (`state.py:51-55`)
answers **any** attribute with a noop returning `None`.

Access path re-verified: `master_executor.py:127` `self.vault = global_state.vault` →
`:252` `load_decrypted_keys(...)` → `:255-257` `keys["api_key"]`.

### 3.2 Copy-ready request

> **To the platform / deployment owner.** For **each** relevant environment, report exactly
> five fields and nothing else:
>
> | Field | Value |
> |---|---|
> | environment identifier | e.g. `staging` / `production` |
> | observation time (UTC) | |
> | `type(app_state.vault).__name__` | expected `APIKeyVault`; `MockEngine` = silently replaced |
> | `Failed to import APIKeyVault` emitted? | `true` / `false` |
> | `APIKeyVault initialization failed, using mock` emitted? | `true` / `false` |
>
> **Do not include any credential, token, environment-variable value, tenant id, user id,
> email or production data.** All three observations are type/log facts and expose no secret.

### 3.3 Status and limitations

**AWAITING EXTERNAL EVIDENCE.** No deployed access exists in this environment and no
response was received. **W-1b gate 1 is NOT satisfied and no observation was fabricated.**

Limitations to state when the evidence does arrive: it is a **point-in-time** reading, so a
process restarted after a configuration change may differ; it describes the object, not
whether any code path has yet called it; and it cannot distinguish an import failure caused
by a missing wheel from one caused by a container path difference. A **present but malformed
Fernet key** would pass the deployed credential check (`config.py:415-422`, `:496`) and
still yield a mock — so a `MockEngine` result with **both** booleans `false` would point at
`create_client` or `Fernet` construction, not at the environment variables.

Once supplied: `MockEngine` in a deployed environment ⇒ `load_decrypted_keys` returns `None`
⇒ `master_executor.py:255-257` raises `TypeError: 'NoneType' object is not subscriptable`
**on the live order path**. `APIKeyVault` ⇒ that specific `TypeError` is excluded, and W-1b
gate 1 closes. No authoritative register will be updated without explicit permission.

---

## 4. D7-1 — authorization determination

**ABSENT.** No grant exists for the three router call sites in any authoritative record.
The newest register (`PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md` §7.1) states "Approvals —
all withheld, none newly granted", "**Total granted: 0**", and D11's one new artifact (§2)
refuses a different request. The readiness report and the patch specification are **not**
authorization.

**Consequence applied:** no source or test file was edited. The specification below was
instead carried to the strongest state achievable without editing — machine-verified.

---

## 5. D7-1 — patch-ready specification, now machine-verified

### 5.1 Source-aware extraction and binding (NEW this pass)

`staging/p11bd11_d71_bind_probe.py` extracts every `*.load_decrypted_keys(...)` call with
`ast`, then binds each extracted call against the **real**
`inspect.signature(APIKeyVault.load_decrypted_keys)`. It never instantiates the vault, so no
Supabase client, environment variable or credential is touched. Output:
`staging/p11bd11_bindprobe.txt`, EXIT=0.

```
REAL SIGNATURE: load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict
  parameters: ['self', 'user_id', 'exchange_id']

  TypeError: got an unexpected keyword argument 'access_token'   routers/orders.py:789   positional=2 keywords=access_token
  TypeError: got an unexpected keyword argument 'access_token'   routers/orders.py:1095  positional=2 keywords=access_token
  TypeError: got an unexpected keyword argument 'access_token'   routers/market.py:50    positional=2 keywords=access_token
  BINDS                                                          routers/exchange.py:993            keywords=-
  BINDS                                                          api_ws/ws_routes.py:814            keywords=-
  BINDS                                                          backend/master_executor.py:252     keywords=-
  BINDS                                                          backend/portfolio_cache_updater.py:160  keywords=-

call sites found : 7      bind OK : 4      bind TypeError : 3
```

Line numbers above are the **AST call-node** lines; the `access_token=` literal sits at
`orders.py:792`, `orders.py:1098`, `market.py:53`. No call site uses `**kwargs`, so the
analysis is decidable for all seven.

**This upgrades D10's status**: the defect count and the exoneration of the four valid sites
are now measured from the real source and the real signature, not asserted.

### 5.2 The proposed test assertions, executed

```
  OK      valid two-argument form binds
  OK      access_token= raises TypeError: got an unexpected keyword argument 'access_token'
```

So the design detects exactly the three defects, clears the four valid sites, and requires
none of the three environment variables `APIKeyVault.__init__` demands. Critically it does
**not** reproduce the defect in a permissive double — which is how the defect survived:
`tests/test_market_none_keys_fix.py:13` declares
`load_decrypted_keys(self, user_id, exchange_id, access_token=None)`, so **the double
matches the broken calls while the real class does not.** That double is **not** to be
modified.

### 5.3 flake8 baseline for the target files (NEW this pass)

`.venv\Scripts\python.exe -m flake8 backend_app/routers/orders.py backend_app/routers/market.py`
→ EXIT=1, **224 pre-existing findings** (130 × E501, 84 × W293, 2 × E305, 2 × W291,
2 × E302, 2 × E131, 1 × F401, 1 × E303). Recorded as
`staging/p11bd11_flake8.txt`.

Only one finding sits anywhere near the patch — `market.py:58:1 W293 blank line contains
whitespace` — and it is pre-existing and two lines below the guard. **The patch deletes
three lines and adds none to these files, so it cannot introduce a flake8 finding**; the
`market.py` finding would simply shift from `:58` to `:57`.

### 5.4 Exact specification, ready to apply on grant

| Step | File | Action |
|---|---|---|
| 1 | `backend_app/routers/orders.py:792` | delete `access_token=user.get("access_token"),` |
| 2 | `backend_app/routers/orders.py:1098` | delete the same line |
| 3 | `backend_app/routers/market.py:53` | delete the same line |
| 4 | `tests/test_d71_vault_signature_compatibility.py` *(new path, requires claiming in the grant)* | AST-extract every `load_decrypted_keys` call site and `SIG.bind` each; assert the valid two-arg form binds and `access_token=` raises `TypeError` |

**Preserve:** `market.py:56` `if not keys or not isinstance(keys, dict):`; the four valid
two-argument sites; the real two-parameter vault signature. **Do not:** add `access_token`
to the vault, change any credential contract, touch the existing double, or extend into
D7-2. Rollback: revert three deletions, delete one test file.
Isolation: `orders.py`, `market.py` and the new path have **no unstaged diff**.

---

## 6. Test commands, outcomes, skips, limitations

All with `$env:AERORA_TEST_DATABASE_URL` pointed at the provisioned local cluster
(`bootstrap_pg.py verify` → `PROVISIONED public_tables=89`, read-only subcommand; the
cluster was **not** started, stopped or re-pointed).

### 6.1 QUOTA-01 — NEW current verification

```
.venv\Scripts\python.exe -m pytest tests/test_quota01_enforcement_is_wired.py `
  tests/test_quota01_exchange_slot_is_atomic.py `
  tests/test_quota01_hard_enforcer_is_deprecated_in_code.py `
  tests/test_quota01_metered_reservation_is_hard.py `
  -q --no-header --tb=line -rfs --timeout=900
```

**EXIT=0 — 50 passed, 0 failed, 5 skipped** (53.4 s) · `staging/p11bd11_quota01.txt`

All five skips share one reason, quoted from the suite: `AERORA_TEST_REDIS_URL is not set.
Real-Redis coverage is opt-in … A skip here is NOT a pass.`

**QUOTA-01 is therefore TESTED on the in-process substrate, with real-Redis coverage
UNEXECUTED (5 tests).** No Redis server was started. This is current verification of the
implementation; it is **not** closure of the launch gate (§7.4).

### 6.2 Adjacent quota and classifier suites

```
.venv\Scripts\python.exe -m pytest tests/test_a20_quota_refusal_semantics.py `
  tests/exchange_sandbox/test_sandbox_order_lifecycle.py `
  -q --no-header --tb=line -rfs --timeout=900
```

**EXIT=0 — 10 passed, 16 skipped** (7.5 s) · `staging/p11bd11_adj.txt`

| File | Result |
|---|---|
| `test_a20_quota_refusal_semantics.py` (concurrent workstream's) | **10 passed** |
| `tests/exchange_sandbox/test_sandbox_order_lifecycle.py` | **16 skipped — the entire file** |

The 16 skips are credential-gated: "exchange sandbox certification is opt-in: set
`AERORA_EXCHANGE_SANDBOX_CERT=1`". **This is the measurement that matters for W-6** (§7.1).

### 6.3 Read-only probes

| Command | Outcome |
|---|---|
| `python staging/p11bd11_d71_bind_probe.py` | EXIT=0 · 7 sites, 4 bind, 3 `TypeError` |
| `python -m flake8 backend_app/routers/orders.py backend_app/routers/market.py` | EXIT=1 · 224 pre-existing findings |
| `python staging/bootstrap_pg.py verify` | `PROVISIONED public_tables=89` |

### 6.4 Environmental limitations

- **No PostgreSQL integration coverage is claimed** for QUOTA-01. The suites passed with the
  DSN available, but their 5 skips are Redis-gated, not PG-gated, and no suite in this pass
  asserted PG integration.
- Real-Redis: **UNEXECUTED** (no server; starting one is not authorized).
- Sandbox/venue: **UNEXECUTED** (no credentials). No exchange was contacted.
- `type`/`mypy`: no project configuration exists; not part of the established workflow.
- `black`/`isort`: `continue-on-error` in CI and failing repo-wide; not applicable as a gate.
- D5's 18-test suite was **not** re-run — nothing changed for it to verify, and the D7 result
  is historical, not current.

---

## 7. Evidence-only assessment

### 7.1 W-6 — OBSERVED, and its coverage is zero in the default lane (NEW)

Ladder re-read at `exchange_executor.py:1236-1256`; classes at `:285-313`:

| Rung | Match | Class | `error_code` | `retryable` |
|---|---|---|---|---|
| 3 | **`symbol` \| `market`** | `InvalidSymbolError` | `INVALID_SYMBOL` | **False** |
| 4 | **`network` \| `timeout` \| `connection`** | `NetworkError` | `NETWORK_ERROR` | **True** |

Rung 3 precedes rung 4, so a ccxt timeout whose message embeds `symbol=` is classified
`InvalidSymbolError` with the retry flag **inverted**. Rung 5's `"key" in error_str` is
additionally broad enough to match any message containing the word "key".

**Coverage measured this pass:** the only tests referencing `_handle_ccxt_error` live in
`tests/exchange_sandbox/test_sandbox_order_lifecycle.py` (`:394, 560, 569, 581, 600, 740`)
and `test_sandbox_routing_contract.py:215`. The one classification test is
`test_09b_invalid_symbol_is_classified_terminal_not_retryable` (`:590`) — it asserts the
**correct** behaviour for a *genuine* invalid symbol, so it passes both before and after a
W-6 fix. **And the whole file skipped: 16/16** (§6.2). So W-6 has **no executing coverage
whatsoever**, and no test anywhere covers a timeout containing `symbol`, or the `"key"`
breadth.

Proposed regression cases (not written, not authorized): a message
`"RequestTimeout: binance GET /api/v3/order?symbol=BTCUSDT timed out"` must classify
`NetworkError` / `NETWORK_ERROR` / `retryable=True`; a genuine `"Invalid symbol BTCUSDT"`
must still classify `InvalidSymbolError`; `"monkey patch failed"` must not classify
`AuthenticationError`. Minimal correction: classify on the ccxt exception **type**, with the
substring ladder as a fallback only. **NOT GRANTED — classifier untouched**,
`exchange_executor.py` still `81ED5679BB93AFB0`.

### 7.2 TENANT-01 — OPEN (OBSERVED; production state unobserved)

- `FORCE ROW LEVEL SECURITY` appears **once** in all migrations, and it is a **comment**
  (`026_fee_records_row_level_security.sql:75`). Applied nowhere.
- `tenant_db_context.py` has **zero** consumers in `core/dependencies.py`, `main.py` or any
  router. The only GUC write in the application is inline:
  `transactional_execution_manager.py:225` `text("SET LOCAL app.current_tenant_id = :tenant_id")`.
- The vault holds a **service-role** client (`api_key_vault.py:80`), which bypasses RLS — so
  the `exchange_keys` RLS policy constrains client traffic but not the credential read. This
  compounds D6-C.

Confirms `PHASE11A_LAUNCH_GATE_REGISTER.md:26`. **No policy inspected beyond source, no
production access, no change. OPEN, not re-scored.**

### 7.3 D6-C — SQL caveat retained

The counts-only SQL stands, with the caveat carried forward verbatim: `exchange_keys` is
unique on `(user_id, exchange_id)`, so a tenant holding keys on two venues yields two join
rows — **`tenants_with_keys` counts matched rows, not distinct tenants**, unless reformulated
with `DISTINCT` or `EXISTS`. Only `tenants_without_keys = 0` is load-bearing and it is
unaffected. The JWT claim-divergence half remains **unmeasurable from application tables**
(`dependencies.py:316`) and needs auth/platform evidence. **No query executed.**

### 7.4 QUOTA-01 — TESTED, gate still OPEN

Implementation: `exchange_connection_slot_guard` at `subscription_dependencies.py:1036`,
single call site `routers/exchange.py:428`. Verification: §6.1 — **50 passed / 5 skipped**.

**The gate does not close.** Three reasons: 5 real-Redis tests did not execute; the launch
register holds QUOTA-01 open on its own criteria, which this pass has no authority to
revise; and the only call site lives in `routers/exchange.py`, one of the **22 staged
entries** owned by a concurrent workstream, so any change there needs coordination. **TESTED
≠ APPROVED ≠ DEPLOYED.**

### 7.5 W-1b, W-3, W-4/W-5, D7-2 — carried forward unchanged

| Item | State |
|---|---|
| **W-1b** | **OPEN.** Gate 1 (D6-D) awaiting evidence · gate 2 (D6-C) awaiting two owners · gates 3–4 (D6-A, D6-B) not decided. No executor lifecycle approval inferred. |
| **W-3** | **NOT GRANTED** (watchdog + execution-state). Targets re-measured in D10: `order_watchdog.py:370`, `:712`, `event_router.py:135`. Coalesce-never-swap and migration 030's no-FK rule preserved. |
| **W-4 → W-5** | Ordering preserved. No `UNKNOWN` exit rule exists; `resolve_stale_execution`'s CAS is still `EXECUTING`-only. W-5 not implemented. |
| **D7-2** | **No design chosen.** The three options stand as compared in D10 §6; the missing import and its per-user swallowing are unchanged, so the portfolio cache remains silently nonfunctional while `/health/services` reports `ACTIVE`. |

---

## 8. Preservation audit

| Check | Initial | Final | Result |
|---|---|---|---|
| `HEAD` / branch / divergence | `074b772a…` / `main` / `0 0` | identical | ✔ |
| Worktrees / unmerged | 1 / 0 | 1 / 0 | ✔ |
| Staged entries / digest | 22 / `2e994018…7152` | **22 / matches** | ✔ |
| 13 inspected targets | — | **drifted 0 / 13** | ✔ |
| Working tree | 262 | 262 + 3 new staging files | expected |

**Files created (3, all read-only evidence under `staging/`):**
`p11bd11_d71_bind_probe.py` (probe, retained as provenance; not collected by pytest — its
name does not match `test_*`), `p11bd11_quota01.txt`, `p11bd11_adj.txt`,
`p11bd11_bindprobe.txt`, `p11bd11_flake8.txt`, plus this report.

**Files changed: 0 source, 0 test, 0 migration, 0 CI, 0 deployment, 0 shared config.**

| Negative confirmation | Result |
|---|---|
| Secrets opened (`exchange_keys.json`, `.env`, backups) | **no** — only variable names and reader code |
| Production credentials used | **no** |
| Database contacted | **no query.** `bootstrap_pg.py verify` is the read-only subcommand; no cluster start/stop; `pg_dsn.txt` not rewritten |
| Exchange contacted | **no** |
| Redis started | **no** |
| Deployment / CI dispatched | **no** |
| Stage / commit / push / reset / clean / stash / branch switch | **none** |
| Authoritative register edited · decision packet duplicated | **none** · **none** |
| Operational certification · paper score | **unchanged** — OFF / NOT CERTIFIED · 53% |

**Deviations: none.** No unexplained drift was found.

---

## 9. Next actions

**Highest value — deliver the §3.2 request to the platform/deployment owner.** Five fields
per environment, no secrets, no database. It is the only item that simultaneously closes
W-1b gate 1, supplies the fact D6-A's mock-fatal question depends on, settles whether the
**live order path** currently carries a `TypeError` at `master_executor.py:255-257`, and
tells the portfolio-cache owner whether D7-2 option 2 is viable at all.

**Safely parallel, no dependency on the above:**

| Action | Owner | Ready state |
|---|---|---|
| Grant D7-1 | router owner | spec **machine-verified** (§5); 3 deletions + 1 test; flake8-neutral; no unstaged diff on targets |
| Packet 2 + Packet 3 (D6-C) | DB owner **and** auth/platform | counts-only SQL with the §7.3 caveat; claim divergence needs auth evidence |
| Decide D7-2 | portfolio-cache + credential owner | three options compared; no new path created |
| Grant W-3 | watchdog **and** execution-state | targets re-measured; coalesce-never-swap |
| Grant W-6 | execution-state + coordinator | regression cases drafted (§7.1); coverage measured at **zero** |
| Enable the real-Redis lane for QUOTA-01's 5 skips | coordinator / release owner | the only remaining QUOTA-01 test gap |

---

**0 source/test files modified · D7-1 ABSENT authorization, spec machine-verified ·
QUOTA-01 TESTED (50 passed / 5 skipped, real-Redis UNEXECUTED) · W-6 coverage measured at
zero · D6-D awaiting external evidence, W-1b gate 1 NOT satisfied · W-1b, W-3, W-4/W-5,
W-6, TENANT-01, QUOTA-01 all OPEN · shared `HEAD` and the 22-entry staged index preserved
byte-for-byte · **live trading OFF / NOT CERTIFIED**, paper track 53% unrevised.**
