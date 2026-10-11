# FINAL PRE-LAUNCH TEST REPORT

**Audit date:** 2026-10-05
**Target:** Aerora / VyomQuant — crypto algorithmic trading SaaS
**Auditor role:** Lead QA Architect / SDET / Trading-Systems / Security / Financial Systems QA
**Companion documents:** `FINAL_PRE_LAUNCH_BUGS.md`, `POST_LAUNCH_IMPROVEMENTS.md`

---

## Executive Summary

### Overall status

# NOT READY FOR LAUNCH

Six P0 defects were found and fixed. Five P1 defects remain open, **one of which touches money and is not mitigated**. Nine critical subsystems — including the live order path itself — could not be verified in this environment at all.

**The six P0 defects were not theoretical.** Each was reproduced by execution:

1. **One tenant's live orders would have executed on another tenant's exchange account.** The executor cache was keyed by exchange and mode but not by account, so in a worker that had already served one trader on Binance, the next trader was handed the first trader's API credentials. Reproduced: `a is b -> True`, `b._api_key -> 'USER_A_KEY'`.

2. **Any holder of `JWT_SECRET` could authenticate as any user with operator role.** The test-token fallback was gated on an `ENV` check that resolved to `"development"` whenever `ENV` was unset. Reproduced: a self-signed token accepted with an arbitrary `sub`, `app_metadata.role='operator'` and `aal='aal2'`. *Exposure correction below.*

3. **A Redis outage turned order retries into duplicate live orders.** The duplicate check returned "not a duplicate" on any store error, under a comment calling it fail-safe.

4. **An ambiguous submission failure released its idempotency claim**, so a lost response after the venue had accepted an order was retried as a second real order.

5. **Order normalization crashed on every tick-size venue** (Binance, Kraken, Coinbase) and silently ignored venue precision on every order, because the market lookup used a different symbol spelling than the dictionary it indexed.

6. **When Redis was unavailable, the distributed lock silently became a per-process dictionary.** A comparison of the *trading mode* (`safe`/`paper`/`live`) against the string `"production"` — a value of the separate `ENV` variable — could never be false, so an in-memory mock was substituted in every environment. Every pod then won its own `SET NX`, and both the execution lock and the idempotency claim became no-ops across pods **while reporting success**.

All six are fixed with regression tests that fail against the original code.

**Finding 6 deserves particular attention: it silently defeated finding 3's fix.** The fail-closed handling added for the duplicate check defends against a store that *errors*. This path *lies successfully*, so no exception is raised and that handling never fires. It was found while auditing production configuration, not while auditing the cache.

### Two corrections to my own findings

A pre-launch audit that does not publish its own errors cannot be audited.

**BUG-R01 — retracted entirely.** I claimed CCXT ignores `config['sandbox']`, making testnet orders hit production with real funds. **False.** Execution against the pinned CCXT 4.3.92 showed `Exchange.__init__` reads `safe_bool_2(self.options, 'sandbox', 'testnet', False)` and calls `set_sandbox_mode`. Both forms resolve to `https://testnet.binance.vision/api/v3`. Sandbox works. What survived is a P2 fragility about the `options` dict being replaced wholesale.

**BUG-002 — exposure overstated.** I wrote that the forged-token path "was live in production by default". Both ECS task definitions in fact set `ENV=production` explicitly, so the gate was closed in that deployment, and `tests/test_no_hs256_fallback_in_production.py` already argued exactly this — correctly. What the gate actually depended on was one environment variable being present on *every* deployment surface with nothing behind it. Severity remains P0 on blast radius; exposure is recorded as contingent and probably not realised in ECS.

### What is genuinely in good shape

This is not a weak codebase, and the audit should say so plainly:

- **`backend/paper/paper_accounting.py` is an exemplary financial module.** Decimal-only, floats refused at the door, an exact zero-tolerance equity identity, and cash derived as the residual of the position-value change so that Δequity ≡ −fee by construction for all five position cases. I attempted to break the identity and could not.
- **`paper_simulator`'s precision handling is correct**, including refusing to guess at CCXT's ambiguous precision value rather than picking a convention. It became the template for the P0-005 fix.
- **The anti-bypass token on `place_order` works** and is genuinely single-use.
- **Admin privilege checks are correct** — `app_metadata.role` only, with `user_metadata` explicitly excluded and the reason documented.
- **Credential encryption is sound** — `MultiFernet` over `MASTER_ENCRYPTION_KEYS` with rotation support, fail-fast on missing keys, decryption per-request in RAM.
- **Credential-verification error handling is well done** — ccxt exceptions are classified against the real exception hierarchy into stable codes, with the provider's text going to the operator log and never to the caller.
- **Reads are never swallowed into fabricated absences.** Several routers return `503 ..._READ_FAILED` rather than an empty list, on the stated principle that "you have no X" is a claim about the account that a failed read does not establish.
- **12,412 of 12,509 tests pass.** The suite is large, fast-failing in the right places, and its tests are unusually well-reasoned — several carry multi-paragraph explanations of the specific defect they exist to catch.

The defects found are concentrated in a specific seam: **the legacy `core/execution_engine.py` + `core/risk_engine.py` + `exchange_executor.py` stack**, which has not received the same discipline as the paper stack beside it. That is a tractable, well-bounded problem.

### Launch recommendation

**Do not launch in 2 days.** A 3–5 day delay addressing the three money-touching P1s and standing up a testnet verification environment would change the verdict. The specific blockers are enumerated in **Exact Launch Blockers** below.

---

## System Architecture Tested

### Component inventory

| Layer | Implementation | Scale |
|---|---|---|
| Backend | FastAPI, Python 3.12 | 391 files, 252,600 LOC |
| Frontend | React / Vite | 207 JS/JSX files |
| API surface | 27 routers | `backend_app/routers/` |
| Database | Supabase (PostgreSQL) + SQLAlchemy; SQLite in test | Alembic + numbered SQL migrations |
| Cache / locks | Redis (`SharedRedisManager` singleton) | |
| Exchange connectivity | CCXT 4.3.92 REST + CCXT Pro WebSocket | |
| Market data | `mds/` service, `ccxt.pro` watch_* | |
| Strategy execution | `strategy_dag/` DAG engine → Trade_Intents | |
| Live execution | `master_executor.BotRunner` → `ExecutionEngine` gateway → `CCXTExchangeExecutor` | |
| Paper execution | `backend/paper/` — structurally separate stack | |
| Backtesting | `backtesting_engine.py` on vectorbt | |
| Risk | `core/risk_engine.py`, `core/risk_manager.py`, `backend/execution_guard.py` | |
| ML/DL | `ml_*` modules, model versioning with signed artifact tokens | |
| Marketplace | `backend/marketplace/` — listings, entitlement resolver, settlement | |
| Billing | Stripe + Razorpay, IP + signature + timestamp validated webhooks | |
| Workers | `workers/command_worker.py`, `tee/`, distributed execution | |
| Deployment | Docker, ECS task definitions, Terraform, 6 GitHub Actions workflows | |

### Order-execution map (the safety-critical finding)

Exactly **one** line in the repository issues a real order to a venue:

```
backend_app/backend/exchange_executor.py   await self._exchange.create_order(...)
  ↑ guarded by verify_and_consume_token (single-use HMAC token)
  ↑ CCXTExchangeExecutor.place_order
  ↑ ExecutionEngine._execute_trade_internal        ← branch guard + NEW notional ceiling
  ↑ ExecutionEngine.execute_with_idempotency       ← THE GATEWAY
  ↑ BotRunner._inner_loop                          ← risk evaluation lives HERE, not in the gateway
```

Gateway checks, in execution order: execution environment → caller source must be `bot_runner` → global kill switch → strategy exists and is owned → idempotency check → atomic claim → execute.

**Finding:** the gateway contains **no risk evaluation**. Verified by AST search for `ExecutionGuard`, `RiskEngine`, `validate_trade`, `validate_trade_request` across both gateway methods — zero matches. Risk is enforced by the single production caller. See BUG-014.

Two other paths could reach a venue:
- `transactional_execution_manager._execute_on_exchange` — no token, no environment guard, no risk. Inert only because its sole instantiation omits the adapter.
- `idempotent_exchange_submission.submit_order_idempotent` — built a LIVE engine with `source="bot_runner"` and zero risk checks. Had no callers. **Now sealed** (BUG-015).

### Paper / live separation

Two different mechanisms, with different strength:

- **The `Paper_Session` stack is structurally separate** and cannot reach a venue — it imports no ccxt and no executor, and writes to `paper_orders` / `paper_fills`. This is the strong form.
- **The `BotRunner` path separates by runtime enum.** `paper_trading` is a required keyword-only constructor argument and both publishers refuse a payload that omits it, which is good. But for the 65 of 71 `ExecutionEngine` construction sites that do not state their environment, the entire separation is `exchange_executor is None` — a null dependency rather than a control.

**Consequence found:** a paper bot started through `routers/strategies.py` now has its orders **refused** at the gateway rather than simulated. That is Requirement 13.9 working as written, but it means paper trading via `BotRunner` produces no executions. The code comments acknowledge this: *"Routing this loop's paper mode there is the remaining work."* See **Missing Features**.

---

## Test Environment

| Item | Value |
|---|---|
| OS | Windows (win32), PowerShell |
| Python | 3.12.10 (`.venv`) |
| CCXT | 4.3.92 (pinned) |
| Database | SQLite (`DATABASE_URL` blanked by `tests/conftest.py`) |
| Redis | In-memory stand-in (`REDIS_URL=""`) |
| Supabase | **Unreachable** |
| Network | **No outbound connectivity** |
| `ENV` | `testing` |
| `VYOMQUANT_MODE` | `safe` |

**This environment materially constrains the audit.** No exchange testnet, no live market data, no Supabase, no Redis, and `VYOMQUANT_MODE=safe` blocks ML inference. Everything in **Tests Blocked** follows from this, and none of it should be read as a pass.

---

## Test Data and Test Users

Isolated synthetic users were used throughout. No real credentials, no real funds, no live venue was contacted.

| Identity | Use |
|---|---|
| `aaaaaaaa-0000-0000-0000-000000000001` | Tenant A — executor cache isolation |
| `bbbbbbbb-0000-0000-0000-000000000002` | Tenant B — cross-tenant credential leak proof |
| `11111111-2222-3333-4444-555555555555` | Forged-token impersonation target |
| `tenant-0001` | Idempotency claim contention |

Synthetic credentials (`USER_A_KEY` / `USER_B_KEY`) were deliberately distinguishable so that a leak would be unambiguous in the assertion output.

**The six-user matrix the brief requested (`USER_A_NORMAL` … `USER_RESTRICTED`) could not be created.** User provisioning runs through Supabase Auth, which is unreachable. Full tenant-isolation testing against live RLS is therefore **BLOCKED — NOT VERIFIED**; what was done instead was a static authorization review of all 27 routers plus executable proof of the one isolation defect that did not need a database.

---

## Results by area

Legend: **PASS** = executed and verified · **FAIL** = defect found · **PARTIAL** = some paths verified · **BLOCKED** = not verifiable here

| # | Area | Verdict | Notes |
|---|---|---|---|
| 1 | Landing page | BLOCKED | Static review only; no browser automation run |
| 2 | Authentication | **FAIL → FIXED** | BUG-002: forged-token acceptance. 735 auth/jwt/token/security tests pass after fix |
| 3 | Authorization | PARTIAL | 27 routers reviewed; no unauthenticated user-data endpoint found. Live RLS BLOCKED |
| 4 | Profile | BLOCKED | Needs Supabase |
| 5 | Exchange account management | PARTIAL | Encryption verified by read; credential verification path sound; **BUG-001 found and fixed** |
| 6 | 110-exchange audit | **FAIL** | See below — 110 advertised, 7 tradable |
| 7 | Strategy builder | PARTIAL | DAG validation tests pass; no drag-and-drop UI automation |
| 8 | ML/DL workflow | BLOCKED | `VYOMQUANT_MODE=safe` blocks `ml_inference` |
| 9 | Backtesting | **FAIL → FIXED** | BUG-011: Sharpe mis-annualized on every timeframe |
| 10 | Paper trading | PARTIAL | `paper_accounting` verified sound. `BotRunner` paper path refuses orders |
| 11 | Live trading | BLOCKED | No testnet credentials, no network |
| 12 | Strategy deployment | PARTIAL | Versioned route works; **BUG-013** legacy route cannot deploy builder strategies |
| 13 | Live trading page | BLOCKED | Needs running deployments |
| 14 | Signal engine | PARTIAL | Unit-level pass; no live feed |
| 15 | Signal traces | PARTIAL | Audit-trail tests pass |
| 16 | Trade history | BLOCKED | Needs DB |
| 17 | Portfolio | PARTIAL | Paper identity exact; **BUG-016** legacy engine identity broken |
| 18 | Risk management | **FAIL → PARTIALLY FIXED** | BUG-009 kill switch could not fire; BUG-010 unbounded sizing; **BUG-014 guard not wired** |
| 19 | Execution engine | **FAIL → FIXED** | Single gateway confirmed; BUG-015 latent bypass sealed |
| 20 | Order idempotency | **FAIL → FIXED** | BUG-003, BUG-004, BUG-006 |
| 21 | Exchange reconciliation | BLOCKED | Needs a venue |
| 22 | Financial testing | **FAIL → PARTIALLY FIXED** | See Financial Findings |
| 23 | Billing | BLOCKED | Needs provider sandboxes |
| 24 | Marketplace | PARTIAL | Entitlement tests pass; BUG-026 containment false positive |
| 25 | Dashboard | PARTIAL | Dashboard tests pass; no end-to-end number reconciliation |
| 26 | Notifications | PARTIAL | Dispatch tests pass |
| 27 | Security | PARTIAL | BUG-002 found and fixed; see Security Findings |
| 28 | API testing | PARTIAL | Validation sweep runs; 1 failure is Supabase-dependent |
| 29 | WebSocket | PARTIAL | Auth and channel-authorization tests pass; no load test |
| 30 | Database | PARTIAL | Schema/migration tests pass on SQLite. **Production schema not verifiable** |
| 31 | Backend | PASS | 12,412 tests pass |
| 32 | Load testing | **BLOCKED** | Not performed — no environment |
| 33 | Stress testing | **BLOCKED** | Not performed |
| 34 | Chaos / failure | PARTIAL | Redis-outage and lost-response paths tested at unit level by the new suites |
| 35 | UI testing | BLOCKED | Playwright present; no browser run |
| 36 | Real-time data | BLOCKED | No feed |
| 37 | Time / date | PARTIAL | UTC use verified by read; DST not exercised |
| 38 | Data precision | **FAIL → FIXED** | BUG-005 |
| 39 | Regression | PASS | 12,577 / 12,672 pass; 0 of the 17 failures attributable to this audit. See Regression Results |
| 47 | Production configuration | PARTIAL | Mostly correct; `VYOMQUANT_MODE=paper` means live trading is off as deployed. See below |

---

## Exchange Integration Testing

The brief asks whether ~110 exchanges are supported. **Quantified by execution against the pinned CCXT:**

| Measure | Count | Source |
|---|---|---|
| Advertised by `GET /api/exchanges/supported` | **110** | `len(ccxt.exchanges)`, served verbatim |
| Can establish a connection | **71** | `len(ccxt.pro.exchanges)` — `ConnectionEngine` guards on `hasattr(ccxt.pro, id)` |
| **Can place an order** | **7** | `ExchangeExecutorFactory.SUPPORTED_EXCHANGES` |
| Described by the certification registry | 8 | A *different* set — includes `gateio`, `bitfinex`; **omits `bitget`** |
| Have a connection form defined | 6 | `ExchangeConnectionSchemaRegistry` |
| **Present in all three registries** | **6** | binance, bybit, coinbase, kraken, okx, kucoin |
| Have per-exchange adapter code | **0** | One generic `CCXTExchangeExecutor` |

**The "110" figure has no basis in integration work.** It is the length of the CCXT exchange list, re-served by an API endpoint. Any of the 103 non-tradable ids raises `ValueError("Unsupported exchange: ...")` the moment a strategy tries to trade it, and 39 of them cannot even complete credential verification.

Registry contradictions found:
- `bitget` is tradable per the factory but unknown to the certification registry, so `validate_deployment_preflight` refuses it as "Level 0" while the directory advertises it.
- `gateio` and `bitfinex` are certified Level 3 / Level 2 but the factory rejects them.
- `okx` is marked "Level 5 production certified" while the repository's own comment records that `okx.parse_market` raises `TypeError` on this CCXT version and **markets do not load**.

Also absent across all venues: `fetch_positions`, `fetch_trading_fees`, and (until this audit) any min-notional enforcement.

**Good news that de-escalated two suspected defects:** none of the 7 tradable venues reports `has['uid']` or `has['password']` on CCXT 4.3.92, so the silently-dropped `uid` field and the passphrase-collection gap are **not live** for the tradable set.

**Recommendation:** state the supported count as **7** (or 6 verified) in all product copy, and either gate `/api/exchanges/supported` to the tradable set or label the remainder explicitly as not tradable. Launching on a "110 exchanges" claim is a misrepresentation risk independent of any code defect.

---

## Financial Findings

| Finding | Severity | Status |
|---|---|---|
| Sharpe annualized by √252 on every timeframe (≈20× error on 5m bars) | P1 | **FIXED** |
| Daily-loss accumulator raised `TypeError`; kill switch could not fire | P1 | **FIXED** |
| ATR sizing unbounded — reproduced at 125× equity notional | P1 | **FIXED** |
| Order precision crashed or silently wrong on every order | P0 | **FIXED** |
| Venue min-quantity / min-notional never enforced | P1 | **FIXED** |
| Third-asset fees (BNB) counted 1:1 against quote — ~1000× understated | P1 | **FIXED** |
| PnL of exactly zero persisted as NULL | P2 | **FIXED** |
| **`open_position` debits only the fee, never the notional** | **P1** | **OPEN — launch blocker** |
| `calculate_net_pnl` / `get_fee_summary` still sum fees across currencies | P2 | OPEN |
| Zero average entry price → 100% phantom profit | P2 | OPEN |
| Process-global Decimal context contended between modules | P2 | OPEN |
| No maker/taker fee schedule exists; flat 0.001 hardcoded | P2 | OPEN |
| Leverage is configured, validated and reported — and never applied | P2 | OPEN |

**The equity identity does not hold in the legacy engine.** `cash + positions ≠ equity`, because opening a position moves only the fee. Reproduced:

```
notional committed   -> 5000.0
equity actually moved -> 5.000000000000   (the fee only)
```

Two consequences beyond the arithmetic: drawdown is computed on realized-only equity, so **an open position down 40% shows zero drawdown** and the max-drawdown kill switch cannot fire on open risk; and the "insufficient capital" check passes repeatedly, so N positions can each be worth up to the full account.

The correct model already exists one directory away in `paper_accounting.py` and is the convergence target.

---

## Security Findings

| Finding | Severity | Status |
|---|---|---|
| Forged HS256 token accepted when `ENV` unset → impersonation + operator escalation | **P0** | **FIXED** |
| Cross-tenant exchange credential reuse → trading another account's funds | **P0** | **FIXED** |
| Redis outage substitutes a per-process dict for the distributed lock | **P0** | **FIXED** |
| `JWT_SECRET` under the HS256 minimum length is only a warning (24 chars observed) | P1 | OPEN |
| RLS policies keyed on a GUC nothing sets — inert | P2 | OPEN |
| `require_aal2` implemented, applied to zero endpoints — no MFA on key or billing mutation | P2 | OPEN |
| `/metrics` unauthenticated | P2 | OPEN |
| `copilot.py` conditional ownership check before a delete filtered by id alone | P2 | OPEN |
| `copilot.py` passes the user dict where an access token is expected | P2 | OPEN |

Tested and found **sound**: algorithm-confusion resistance (ES256 JWKS trust root, `alg` header never used to select a key), admin privilege via `app_metadata` only, credential redaction before logging exception text, webhook IP + signature + timestamp validation, rate limiting with stacked per-caller and per-address windows, and the absence of any `if env == dev: return fake_user` pattern.

Not attempted, and therefore not cleared: SQL injection, XSS, SSRF, command injection, path traversal and malicious upload testing all require a running application with a reachable database. **BLOCKED — NOT VERIFIED.**

---

## Trading Safety Findings

| Property | Verdict |
|---|---|
| Single auditable execution gateway | **PASS** — one `create_order` call, one gateway, one anti-bypass token |
| Duplicate live execution risk | **RESOLVED** — atomic `SET NX` claim, fail-closed store, venue-side `clientOrderId`, claim retained on ambiguous failure |
| Paper / live separation | **PARTIAL** — structural for `Paper_Session`; a runtime enum plus a null dependency for `BotRunner` |
| Risk enforced server-side | **PARTIAL** — enforced by the caller, not by the gateway. Bounded by the new notional ceiling |
| Risk bypass | **RESOLVED for the reachable set** — the one unrisked path is sealed |
| Kill switch | **PASS** for the global switch; daily-loss switch was inoperative and is **FIXED** |
| Exchange reconciliation | **BLOCKED** |
| Stop-loss orders reach the venue | **FIXED** — previously `TypeError` on every stop order |

---

## Production Configuration Audit

Read from the ECS task definitions. Full detail in `FINAL_PRE_LAUNCH_BUGS.md`; no secret values were reproduced.

**Mostly correct, and better than typical.** `ENV=production`, real CORS origins with no wildcard, `FEATURE_DEBUG_ENDPOINTS_ENABLED=false`, `FEATURE_MOCK_EXCHANGE_ENABLED=false`, `RATE_LIMIT_ENABLED=true`, `LOG_LEVEL=INFO`, all ten secrets delivered via ECS `secrets` (Secrets Manager) rather than plaintext environment, API docs auto-disabled in production, and `.env` neither tracked nor ever committed with `.gitignore` coverage that is genuinely thorough.

### FINDING — production is configured for paper trading, not live

```
VYOMQUANT_MODE = paper          (both task definitions)
VYOMQUANT_ENABLE_LIVE_TRADING   (not set)
```

`safety_config.py` blocks `operation == "live_order"` in paper mode, and `main.py` arms live trading only for `env_mode == "live"`. `FEATURE_TRADING_ENABLED=true` does **not** override this — it is a different flag at a different layer, and the pairing reads as though live trading were enabled when it is not.

**As deployed, live trading is off.** Either that is deliberate — in which case "live trading" must not appear in launch marketing — or the flip to `live` is planned for launch day.

**If it is the latter, that flip would be the first time the live order path ever executed**, in production, with real customer funds, on a path that until this audit contained a cross-tenant credential leak, stop orders that raised `TypeError` before reaching the venue, order normalization that crashed on Binance-style precision, no venue-side idempotency, and a distributed lock that degraded to a per-process dict. Whichever reading applies, the live-mode flip must not be that path's first exercise.

### Also noted

- `PRODUCTION_ROUTER_ENABLED=False`, while `feature_flags.py` defaults it to `True`. Confirm intent.
- `CORS_ORIGINS` defaults to localhost, so a new environment that omits it rejects the real frontend. Fail-closed is the right direction, but it presents as a total outage rather than a configuration error.
- `td15.json` and `td22.json` are UTF-16 and could not be parsed. **Unaudited** — confirm they are not deployed.
- The deprecated `AERORA_MODE` fallback still exists in `safety_config.py` despite `_DEPRECATED_AERORA_MODE_REMOVED=true`.

---

## Performance Findings

**No load or stress testing was performed.** There is no environment in which to perform it: no reachable database, no Redis, no market feed, no deployed application.

Bottlenecks identified by inspection, for whoever runs the load tests:

- `RateLimiter(requests_per_second=5.0)` is hardcoded per venue and **per process**, so the effective rate is 5 × pod count. Not distributed.
- `_executors` and `_exchange_pool` are unbounded process-global dicts. `release_exchange_executor` was added by this audit; nothing calls it yet.
- `PROFILE_CACHE_TTL` (60 s) means a frozen account stays active for up to a minute — documented and deliberate, but it is a security propagation delay under load.
- The full test suite takes 78 minutes serially; `pytest-xdist` is not installed.

---

## Tests Blocked — NOT VERIFIED

These are **not passes**. Each requires infrastructure this environment does not have.

| # | Blocked area | Blocker | Risk if launched unverified |
|---|---|---|---|
| 1 | Live / testnet order placement end to end | No exchange credentials, no network | **HIGH** — the money path has never been exercised against a real venue in this audit |
| 2 | Exchange reconciliation (internal vs venue order/position/balance) | No venue | **HIGH** |
| 3 | ML training, persistence, reload and inference | `VYOMQUANT_MODE=safe` blocks `ml_inference` | **HIGH** — ML strategy blocks are a headline feature |
| 4 | Tenant isolation against live RLS | Supabase unreachable | **HIGH** |
| 5 | Billing: checkout, renewal, webhook idempotency, entitlement | No Stripe/Razorpay sandbox | **HIGH** |
| 6 | Load and stress testing | No deployed environment | **MEDIUM-HIGH** |
| 7 | WebSocket behaviour under volume, reconnect storms, cross-user leakage | No running server | MEDIUM |
| 8 | Browser / UI / responsive / console-error testing | No browser automation run | MEDIUM |
| 9 | Real-time market data normalization, stale ticks, out-of-order events | No feed | MEDIUM |
| 10 | Production schema verification | Live DB unreachable | **HIGH** — see below |
| 11 | Injection / XSS / SSRF / traversal / upload testing | No running app | MEDIUM |
| 12 | Migration from a clean and from a realistic existing database | No PostgreSQL | MEDIUM |

**Item 10 deserves separate emphasis.** `migrations/020_declare_pre_existing_tables.sql` states that `execution_records`, `idempotency_keys`, `orders`, `positions` and `fills` are declared only in Alembic and by no SQL migration, and `alembic/versions/merge_heads_20260820.py` states that **no workflow, Dockerfile or script runs `alembic upgrade`** — the live schema is applied by hand. Whether the `execution_records` primary key on `execution_id` — the database-level guarantee behind order idempotency — actually exists in production **cannot be determined from this repository.** Verify it directly before launch; if it is absent, the idempotency story has no backstop.

---

## Fixes Applied

Nine production files changed, 1,131 insertions. Nine new test files, 139 new tests, all passing.

| # | Fix | File | Tests |
|---|---|---|---|
| 1 | Per-tenant executor cache; required keyword-only `user_id`; credential-fingerprinted key; cache-hit credential re-check; `release_exchange_executor` | `backend/exchange_executor.py`, `backend/master_executor.py` | 12 |
| 2 | `_test_token_fallback_allowed()` — reads only real env vars, admits only `testing`/`test`, fails closed; shared by both decoders | `core/auth_middleware.py` | 18 |
| 3 | `IdempotencyUnavailable`; fail-closed duplicate check; atomic `SET NX` claim; claim retained on ambiguous failure | `backend/distributed_execution/idempotent_exchange_submission.py` | 9 |
| 4 | Dual CCXT precision conventions with the ambiguous value refused; symbol-spelling-tolerant market lookup; step snapping; min-qty / min-notional enforcement; positional `create_order`; `clientOrderId` forwarded | `backend/exchange_executor.py` | 27 |
| 5 | Decimal normalization in `update_equity`; `Decimal("0")` on reset; notional cap on ATR sizing | `core/risk_engine.py` | 20 |
| 6 | `BARS_PER_YEAR` + `annualization_factor()`; timeframe-aware Sharpe | `backend/backtesting_engine.py` | 14 |
| 7 | Notional ceiling on the live branch, failing closed; unrisked submission path sealed | `core/execution_engine.py`, `.../idempotent_exchange_submission.py` | 14 |
| 8 | Fee denomination split into base / quote / third asset, with the unconvertible case reported absent rather than 1:1; zero-valued PnL no longer persisted as NULL | `backend/fee_engine.py` | 10 |
| 9 | In-memory cache no longer substituted for Redis outside dev/test; gate reads the deployment environment, never the trading mode | `core/cache/redis_manager.py` | 15 |

### Two existing tests corrected — scope fixes, not weakened assertions

Both are recorded here because weakening a test to obtain a pass is explicitly forbidden, and both changes must be auditable.

**`tests/test_execution_paths_that_claim_an_exchange.py`** — `_call_sites()` gained `production_only=True` and the reachability test uses it. My new tests deliberately *call* `submit_order_idempotent` to prove it refuses, and counting a test that demonstrates safety as evidence of reachability makes the measurement say the opposite of the truth. The question asked of shipped code is unchanged, and I **added** `test_the_idempotent_submission_path_refuses_live_orders_outright`, which asserts the refusal itself — a stronger property than "has no callers", since the next commit can add a caller.

**`tests/test_backtest_key_contract.py`** — `sharpe_ratio` moved out of `PRESERVED_FROM_ENGINE`. That tuple drives a **cross-run** comparison, and the two fixtures deliberately use different timeframes (`1m` vs the package's `15m`). The hardcoded √252 made Sharpe accidentally timeframe-independent, so the equality held by coincidence. The file already excludes `calmar_ratio` and `sortino_ratio` for exactly this reason. Arithmetic confirms nothing else moved: √525600 ÷ √35040 = 3.873, observed 96.5654 ÷ 24.9331 = 3.873. Replaced with a **same-run** payload-vs-row preservation test, which is the stricter reading of the requirement — the old cross-run check could have passed while a second producer recomputed the value.

---

## Regression Results

### Before and after

Both runs are `pytest tests/ -q` against the full suite. Reproduce with:

```
.venv\Scripts\python.exe -m pytest tests/ -q
```

| Run | Collected | Passed | Failed | Skipped | Duration |
|---|---|---|---|---|---|
| **Baseline** (before any change) | 12,509 | 12,412 | 19 | 70 | 78 min |
| **Final, clean** (finished code, no concurrent edits) | **12,672** | **12,577** | **17** | 70 | 37 min |

Net: **+165 passing, −2 failing, +163 collected.** The final run was executed against the finished code with no concurrent edits.

### Every one of the 17 final failures, classified

| Count | Tests | Cause | Attributable to this audit? |
|---|---|---|---|
| 4 | `test_strategy_status_publish.py` | **BUG-013** — the legacy deploy route cannot deploy a builder-saved strategy. Genuine, pre-existing, in files this audit did not touch | No |
| 7 | `test_task_6_1_backtest_execute.py` | Order-dependent: **all 7 pass in isolation.** Present in the baseline run too | No |
| 1 | `test_task_8_4_runtime_readiness_gate.py` | `VYOMQUANT_MODE=safe` blocks `ml_inference`. **Environment-blocked** | No |
| 1 | `test_validation_sweep.py[library.clone_strategy]` | Supabase unreachable → 500 on a library lookup. **Environment-blocked** | No |
| 1 | `test_protected_logic_containment.py` | **BUG-026** — three-character substring false positive. Pre-existing | No |
| 1 | `test_exchange_safety_fix.py::test_connection_pooling_safety` | Requires live Binance credentials: `AuthenticationError: Invalid Api-Key ID`. **Environment-blocked** | No |
| 1 | `test_absent_vs_zero.py` | Hypothesis coverage floor: `zero_sum_ledger occurred 4 time(s), floor 5`. **Flaky** — passes 4/4 in isolation. New to the failure list, but a statistical shortfall in generated examples, not a behaviour change | No |
| **0** | — | **Caused by this audit's changes** | — |

**Three baseline failures were fixed** as a side effect of the code changes: two in `test_execution_environment_is_stated_not_defaulted.py` and one in `test_risk_limits_server_side.py`.

### Targeted verification

| Selection | Result |
|---|---|
| All 9 new regression suites together | **139 passed** |
| `-k "auth or jwt or token or security"` | 735 passed, 1 skipped |
| `-k "execution or exchange or risk or idempot or backtest or order"` | 2,092 passed |
| `-k "redis or cache or lock or idempot"` (after the Redis fix) | 970 passed, 2 skipped |
| `-k "fee or pnl"` (after the fee fix) | 471 passed |
| The 4 corrected existing test files | all passing |

### Two notes on the failure count

**The 7 order-dependent failures are a real finding, just not a code one.** Tests that pass alone and fail in a 12,672-test run indicate shared state leaking between tests. `tests/conftest.py` already documents several session-scoped singletons that leak. Worth fixing, because it makes the suite's signal unreliable exactly when it is most needed.

**The flaky Hypothesis floor is also worth fixing.** The test's own assertion message gives the right instruction: *"Fix a shortfall by FORCING the case in the generator, never by lowering the floor."* A hard coverage floor over randomly generated examples will flake in CI.

### A note on method

Four of my own new tests failed on first run, and three failed for the same reason: they searched source *text* for a forbidden token while the explanatory comment beside the fix necessarily contained that token. All now parse with `ast` and inspect call targets, `Name` nodes and `Dict` keys instead. The repository's own suite documents this exact trap.

More significantly, **one of my claimed P0s was retracted outright** after execution disproved it, and **one P0's blast radius was overstated** until I read the deployment artifacts. Both corrections are published above. Reading code is not the same as running it — in both directions.

**A note on method.** Four of my own new tests failed on first run. Three failed because they searched source *text* for a forbidden token while the explanatory comment next to the fix necessarily contained that token. All now parse with `ast` and inspect call targets, `Name` nodes and `Dict` keys. The repository's own test suite documents this exact trap — and one of my claimed P0s (BUG-R01) was retracted outright after execution disproved it. Reading code is not the same as running it, in both directions.

---

## Missing Features

| Feature | State |
|---|---|
| Paper trading via `BotRunner` | Orders are **refused** at the gateway. Paper via `Paper_Session` works; the `BotRunner` path produces no executions. The code acknowledges this as "remaining work" |
| `fetch_positions` on any exchange | Not implemented — only a capability flag |
| `fetch_trading_fees` / maker-taker schedule | Not implemented — flat 0.001 hardcoded |
| Leverage application | Configured, validated and reported; never applied to a size |
| Exchange reconciliation loop | `reconciliation_engine.py` exists; not verified running |
| MFA enforcement | Gate implemented, applied nowhere |
| Daily PnL report | Returns hardcoded `'0.0'` placeholders |

---

## Exact Launch Blockers

Everything below must be closed, or explicitly accepted in writing as a risk by the owner, before launch.

### Must fix (code)

1. **BUG-016** — `open_position` must debit the notional. Until then the equity identity is false, drawdown is blind to open risk, and the max-drawdown kill switch cannot fire on an open position. *Money-touching P1, not mitigated. This is the single most important remaining item.*
2. **BUG-012** — refuse to start when `ENV=production` and `JWT_SECRET` is under 32 bytes. The observed secret is 24 characters. *Auth P1.* One line plus a secret rotation.

### Must verify (environment) — currently NOT VERIFIED

4. **Production schema** — confirm the `execution_records` primary key on `execution_id` exists in the live database. Nothing runs `alembic upgrade`. If it is missing, order idempotency has no database backstop.
5. **Testnet order placement end to end** — the money path has not been exercised against a real venue in this audit.
6. **Exchange reconciliation** — internal vs venue order, position and balance.
7. **Billing** — webhook idempotency and entitlement, against provider sandboxes.
8. **Tenant isolation against live RLS** — with the six-user matrix, including the `library.py` service-role paths where Python checks are the only control.
9. **ML train → persist → reload → infer**, with `VYOMQUANT_MODE` permitting inference.

### Must decide (product)

10. **Is launch paper-only or live?** `VYOMQUANT_MODE=paper` is what is deployed, and `VYOMQUANT_ENABLE_LIVE_TRADING` is unset, so live trading is currently off. If launch is paper-only, remove "live trading" from the marketing. If it is not, the mode flip must happen *after* item 5, never as that path's first exercise.
11. **The "110 exchanges" claim.** Seven venues can place an order; six are present in all three internal registries. Correct the product copy or gate the directory endpoint.
12. **Paper trading via `BotRunner`** — either route it to the `Paper_Simulator` or make the UI offer only the `Paper_Session` path, so the feature is not silently inert.
13. **BUG-013** — remove the legacy deploy endpoint or make it refuse with a pointer to the versioned route, and re-point its four tests.

### Explicitly accepted risks (if launching anyway)

- No load or stress testing has been performed. Capacity, latency under concurrency and queue behaviour are unknown.
- `ExecutionGuard` — the self-described "final control layer" — is not in the order-placement path. Risk enforcement depends on `BotRunner` remaining the only caller, bounded by the new notional ceiling.
- Stop orders were broken until this audit and have been fixed but never exercised against a venue. **No stop loss has ever reached an exchange from this system.**
- Redis availability is now a hard dependency for order execution. That is the correct direction — a per-process lock is worse than no lock — but it means a Redis outage stops trading rather than degrading it. Confirm the Redis deployment is highly available, and that "trading halts" is the intended behaviour.
- `td15.json` and `td22.json` could not be parsed and were not audited.

---

## Final Launch Gate

Evaluated honestly against the stated criteria. `VERIFIED` means executed and confirmed; `NOT VERIFIED` is not a pass.

| Gate criterion | Status |
|---|---|
| P0 bugs = 0 | **PASS** — 6 found, 6 fixed, each with a reproducing regression test |
| Critical P1 bugs = 0 | **FAIL** — BUG-016 (notional never debited) is money-touching and unmitigated |
| Critical security vulnerabilities = 0 | **PASS** for those found and fixed; **NOT VERIFIED** for injection / XSS / SSRF / traversal / upload, which need a running app |
| Critical financial calculation errors = 0 | **FAIL** — BUG-016. Six other financial defects fixed |
| Critical trading execution bugs = 0 | **PASS** — cross-tenant credentials, precision crash, stop orders, venue idempotency all fixed |
| Critical authorization bugs = 0 | **PASS** for the static review; **NOT VERIFIED** against live RLS |
| Critical data integrity bugs = 0 | **PASS** — zero-valued PnL no longer persisted as NULL |
| Duplicate live execution risk = resolved | **PASS** — atomic claim, fail-closed store, venue `clientOrderId`, claim retained on ambiguous failure, and the per-process-lock degradation closed |
| Risk bypass = resolved | **PARTIAL** — the one reachable unrisked path is sealed and a notional ceiling added, but `ExecutionGuard` is still not in the order path (BUG-014) |
| Paper/live separation = verified | **PARTIAL** — structural for `Paper_Session`; a runtime enum plus a null dependency for `BotRunner`, whose paper mode now produces no executions at all |
| Exchange reconciliation = verified | **NOT VERIFIED** — needs a venue |
| Billing integrity = verified | **NOT VERIFIED** — needs provider sandboxes |
| Marketplace entitlement = verified | **PARTIAL** — unit tests pass; no end-to-end run |
| Database integrity = verified | **NOT VERIFIED** — the production schema cannot be confirmed from this repository; nothing runs `alembic upgrade` |
| Authentication = verified | **PASS** — forged-token path closed and fails closed on an unset `ENV` |
| API = verified | **PARTIAL** — validation sweep runs; one failure is Supabase-dependent |
| WebSocket = verified | **PARTIAL** — auth and channel authorization pass; no load or leakage test |
| Critical E2E workflows = PASS | **NOT VERIFIED** — the golden path was never executed end to end; no Supabase, no venue, no browser |
| Regression suite = PASS | **PASS** — 12,577 / 12,672, zero regressions attributable to this audit |
| Production configuration = verified | **PARTIAL** — mostly correct, but live trading is off as deployed and two task-definition files could not be parsed |

### Verdict

# NOT READY FOR LAUNCH

Nine gate criteria are `NOT VERIFIED` and one is a clear `FAIL`. The single `FAIL` is one day of work. The nine `NOT VERIFIED` are not engineering work at all — they need a testnet and a staging database.

---

## Final Recommendation

The engineering quality here is high in places — `paper_accounting`, the credential vault, the error classification and much of the test suite are better than typical for a pre-launch product. The defects are not scattered; they cluster in the legacy execution/risk seam that the newer paper stack was built to replace, and that seam is exactly where real money flows.

Two of the five P0s — trading another tenant's account, and authenticating as any user with operator role — are the kind of defect that ends a company rather than causing an incident. Both are now fixed with reproducing regression tests. That is the strongest argument for a short delay rather than a long one: the problems are real, findable, and tractable.

**Recommendation: delay launch by 3–5 days.** Spend it on the three code blockers and on standing up a testnet plus a Supabase staging environment so that items 4–9 can move from NOT VERIFIED to verified. The product can then launch on a claim it can actually support.

**Do not launch while nine critical subsystems — including the live order path itself — have never been exercised.** A clean test suite on SQLite with no network is not evidence that a trading system moves money correctly.
