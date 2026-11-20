# TRADING SAFETY AUDIT

**Scope:** order creation and cancellation paths, the live/paper boundary, tenant and strategy ownership, risk
and capital gates, precision, idempotency, kill switch, worker retry safety, accounting invariants.
**Date:** 2026-10-07
**Method:** static reading plus execution of the live-trading gate matrix. No production access. Live trading was
not enabled at any point.

---

## 1. HEADLINE: LIVE TRADING IS OFF, AND IT TAKES A CODE CHANGE TO TURN ON

Executed via `staging/verify_live_trading_off.py` → `staging/live_gate_final.log`, `EXIT=0`.

| Case | `VYOMQUANT_MODE` | enable flag | Observed verdict |
|---|---|---|---|
| production as deployed | `paper` | unset | BLOCKED |
| flag set, mode not | `paper` | `true` | BLOCKED |
| mode set, flag not | `live` | unset | BLOCKED |
| deprecated alias | `paper` | `AERORA_ENABLE_LIVE_TRADING=true` | BLOCKED |
| **both switches enabling** | **`live`** | **`true`** | **BLOCKED** |

The last row is decisive and is the reason this is a PASS rather than a "configured off". With both documented
enabling switches set to their enabling values, execution is **still** refused — by
`ExecutionFlags.LIVE_TRADING_ENABLED`, which is `bool = False` at class definition
(`core/safety_config.py:114-116`) and re-zeroed at module import (`:273-280`), read from **no environment
variable at all**.

```
LIVE TRADING: OFF / NOT CERTIFIED
```

OFF is established by execution. **NOT CERTIFIED** because of the findings in §4-§8 below: the gate being shut is
not the same as the machinery behind it being safe to open.

### 1.1 The boot gate is a genuine double opt-in

`main.py:236-243`, at **module import scope** (not inside `lifespan`):

```python
env_mode = get_vyomquant_mode("safe").lower()
if   env_mode == "live":  ExecutionFlags.enable_live_trading()
elif env_mode == "paper": ExecutionFlags.enable_paper_trading()
else:                     SafetyMonitor.assert_safe_mode()
```

`enable_live_trading()` (`safety_config.py:179-203`) independently re-derives both variables and raises
`RuntimeError` unless `VYOMQUANT_MODE == "live"` **and** `VYOMQUANT_ENABLE_LIVE_TRADING == "true"`. A malformed
mode (`"Live"`, `"live "`, `"production"`) crashes the import via `assert_safe_mode`. Unset ⇒ `"safe"` ⇒ neither
flag armed. **Fails closed.** PASS.

---

## 2. THE ORDER FUNNEL

One production path:

```
BotRunner._inner_loop               (backend/master_executor.py)
  → ExecutionEngine.execute_with_idempotency   (core/execution_engine.py:434)
    → _execute_trade_internal                  (:818)
      → CCXTExchangeExecutor.place_order       (backend/exchange_executor.py:882)
        → self._exchange.create_order(...)     (:1043)
```

Gate order inside `execute_with_idempotency`, verified by reading:

| # | Gate | Line | Fails closed? |
|---|---|---|---|
| 1 | environment agreement (`assert_live_environment` / `assert_simulation_environment_matches`) | `:570-593` | yes — raises |
| 2 | `source != "bot_runner"` → blocked | `:607-615` | yes |
| 3 | **global kill switch** | `:619-644` | yes — *check failure also blocks* |
| 4 | strategy exists **and is owned** | `:646-655` | yes — exception ⇒ reject |
| 5 | idempotency check | `:665+` | — |
| 6 | atomic claim | `:700-710` | yes — lost race ⇒ skip |
| 7 | notional ceiling | `:935-965` | yes — unreadable equity ⇒ refuse |
| 8 | anti-bypass token + venue minimums | `exchange_executor.py:904`, `:925-990` | yes — raises |

Steps 3, 4 and 7 all block on *failure to evaluate*, not merely on a negative result. That is the right
direction and is worth crediting explicitly.

---

## 3. WHAT IS SOLID

| Control | Mechanism | Verdict |
|---|---|---|
| Manual HTTP order submission | `routers/orders.py:696` `execute_order_blocked`, `:726` `create_order_blocked` — both raise | **PASS** |
| Direct executor bypass | `verify_and_consume_token` (`exchange_executor.py:708-735`) raises `ValueError("Bypass attempt detected…")` if the HMAC token or exec id is missing; token cleared before use so it cannot be replayed | **PASS** |
| Strategy ownership | `_validate_strategy_exists` (`execution_engine.py:1837-1901`): `SELECT 1 FROM strategies WHERE id = :id AND user_id = :tid`; blocked-name list; **fails closed on any exception** | **PASS** |
| `test_`-prefix bypass | gated on `_synthetic_strategy_ids_allowed()` (`:40-55`) which **fails closed on unset `ENV`**; applied at both call sites so they cannot disagree | **PASS** |
| Atomic execution claim | `claim_execution` (`core/models/execution_record.py:610-700`): `UPDATE … SET status='executing' WHERE execution_id=… AND tenant_id=… AND status IN ('PENDING','pending') RETURNING *` | **PASS** |
| Venue-side idempotency | `clientOrderId = execution_id` (`exchange_executor.py:1014-1021`) — deterministic platform key forwarded to the venue | **PASS** (with §7 caveat) |
| Venue minimums | min-amount and min-notional checked **before** send, with figures named (`:925-990`) | **PASS** |
| Precision | `Decimal` throughout `execution_engine.py` (`Position`, `unrealized_pnl`, notional); `normalize_size`/`normalize_price` per venue; truncation-to-zero refused with `BELOW_PRECISION` | **PASS** |
| Paper accounting invariant | zero-tolerance `total_equity == available + locked + position_market_value`, recomputed not incremented | **PASS** |
| Kill switch check failure | blocks (`execution_engine.py:636-644`) | **PASS** |
| Redis-unreachable kill switch | fail-closed local latch with 10s re-probe so a blip does not deadlock the platform (`global_safety.py:160-230`) | **PASS** |

---

## 4. FINDING TRADE-01 — CAPITAL CHECK IS READ-THEN-COMPARE (P1)

The gateway's one risk bound is the notional ceiling (`execution_engine.py:935-965`):

```python
_notional = abs(size) * abs(price or 0)
_ceiling  = _equity * _max_leverage
if _notional > _ceiling: refuse
```

It fails closed on unreadable equity — good. But **nothing is reserved.** `self.current_equity` is in-process
state; nothing is decremented between the comparison (`:962`) and `place_order` (`:977-983`). Exposure is
updated **after** the fill (`master_executor.py:646-654`), in process memory.

Searched for `with_for_update` / `SELECT … FOR UPDATE` / an atomic decrement anywhere on this path: **none**.

The cross-process serialization that does exist is **per-execution-id, not per-capital**: the Redis
`SET nx ex=30` lock keyed `execution:{user}:{symbol}:{execution_id}` (`master_executor.py:549-553`) plus
`claim_execution`. Both deduplicate *the same* order.

**So a TOCTOU window exists.** Two concurrent submissions for the same tenant with *different* parameters
(different quantity, price, or time bucket) derive **different** `execution_id`s, take **different** Redis locks,
read the same un-decremented equity, and can both pass the ceiling. The only remaining backstop is the venue's
own balance rejection — which `place_order`'s own comment at `:958-960` acknowledges.

**Verified by code reading. Not demonstrated empirically** — confirming the window needs two concurrent workers,
which was not run (see `staging/PERFORMANCE_AUDIT.md`). Reported as a FAIL on the control's design, not as an
observed loss.

---

## 5. FINDING TRADE-02 — THE KILL SWITCH EXPIRES (P1)

`activate()` writes the Redis key with `ex=86400` (`global_safety.py` ~`:259`). After 24 hours the key is gone,
`is_active()` takes its *"key does not exist → NOT active (safe default)"* branch (`:206-210`), and **trading
silently resumes**. No renewal loop was found.

The "safe default" is correct for a key that was never set; it is wrong for a key that was set deliberately and
then expired. An operator who pulls the kill switch and goes home gets trading back the next day with no
notification.

Compounding items, same area:

- **`enforce_kill_switch()` (`:430-443`) has no call site** in `backend_app/`. The only enforcement is the inline
  block in `execution_engine`.
- **The per-user kill switch is process-local and not on the live path.** `routers/risk.py:68-70`
  `is_user_kill_switched` reads an in-process dict `_user_kill_switch_state` (`:44`) set by admin endpoints
  (`:517`, `:565`). It is consulted in `paper_trading_service.py:1238` and by dashboards, but **not** in
  `execute_with_idempotency`. Under multiple workers a toggle on worker A is invisible to worker B, and it does
  not gate live orders at all.
- The anti-bypass HMAC secret is a **hardcoded literal** (`global_safety.py:445`). It is a bypass guard, not a
  security boundary — correct as scoped, but it should not be mistaken for one.

Auto-triggers *are* wired (`consistency_checker.py:382-389` on position mismatch; `global_safety.py:360-415` for
unhealthy exchange, reconciliation mismatch, duplicate order, portfolio inconsistency), which makes the expiry
more consequential: an automatic activation also expires.

---

## 6. FINDING TRADE-03 — A SECOND, UN-GUARDED EXCHANGE WRITE (P2)

`backend_app/backend/transactional_execution_manager.py:1072`:

```python
result = await exchange.create_order(symbol, type_, side, amount, price)
```

inside `execute_order` (`:836`). This does **not** go through `place_order`, so it receives:

- no anti-bypass validation token,
- no `clientOrderId` (therefore **no venue-side idempotency**),
- no venue minimum-amount / minimum-notional pre-check,
- and the file does not appear in a `kill_switch` grep of `backend_app/`.

So the claim "one chokepoint" is **not** true as written.

**Mitigating, and the reason this is P2 rather than P1:** `alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py:43-45`
records that this class's only constructor is `backend/exchange_websocket_listener.py:343`, and that **nothing
starts that listener**. So it is probably dead code. "Probably dead" is not "cannot execute", and a second
`create_order` with none of the guards is the kind of thing that gets revived.

**The function body was not read**, so its internal guards are **unverified**. Flagged rather than assumed safe
*or* assumed unsafe.

---

## 7. IDEMPOTENCY AND RETRY SAFETY

### 7.1 What works

- `clientOrderId` is the platform's deterministic `execution_id` (`exchange_executor.py:1014-1021`), so a ccxt
  internal retry or a lost-response retry **within the same bucket** is rejected by the venue as a duplicate.
- `claim_execution` is a genuine atomic conditional UPDATE. The row is `EXECUTING` **before** the exchange call
  and only moves to `COMPLETED`/`FAILED` after. On restart, the same `execution_id` reads `EXECUTING` and is
  skipped. PASS.

### 7.2 The bucketing caveat

`generate_execution_id` derives the key from (tenant, strategy, symbol, **bucketed timestamp**, side, qty, price)
with `execution_interval_minutes` defaulting to 5. A retry that lands in a **later bucket** produces a different
`execution_id` → a different `clientOrderId` → **a new order at the venue**, and it also sidesteps the DB
dedupe. So retry safety is time-boxed, not absolute.

### 7.3 No reaper for stale `EXECUTING` rows

Searched; none found. A crash after claiming but before the exchange call leaves that `execution_id` blocked
indefinitely, with the true venue state unknown until `reconciliation_worker` resolves it. **Absence of
evidence** — not exhaustively proven absent, but not found.

### 7.4 An implicit invariant

`if self._consumed_execution_id:` (`exchange_executor.py:1020`) means a falsy exec id yields an order with **no
venue-side idempotency**. `verify_and_consume_token` would have raised earlier in that case, so it is currently
unreachable — but the invariant is implicit rather than asserted.

### 7.5 Parallel idempotency layer not used by the production path

`core/distributed_idempotency.py:455-545` (atomic Lua check-and-set with owner-token release) is used by
`exchange_executor.place_order_with_idempotency` (`:1190-1256`) and `signal_service`, but **not** by the
`BotRunner` path. Two mechanisms, one unused where it matters.

---

## 8. THE PAPER / LIVE BOUNDARY — AMBER

Three facts must agree, and agreement is only partly enforced:

1. `execute_with_idempotency` (`:570-593`) selects its guard: a "declared simulator" (engine states
   `PAPER`/`BACKTEST` **and** holds no executor) gets `assert_simulation_environment_matches`; everything else
   gets `assert_live_environment`. Both raise rather than return a verdict, resolve by exact spelling, and never
   default to `LIVE` (`execution_environment.py:152-171`).
2. `_execute_trade_internal` (`:860-890`) refuses **both** mismatch directions, and both are real:
   - PAPER/BACKTEST engine *holding* an executor → would place a real order;
   - LIVE engine *without* an executor → would simulate the fill and **report it as filled**, which the source
     comment correctly identifies as the more dangerous direction because it fails silently and optimistically.
3. The branch itself is `if _has_executor:` (`:891`) — **the live/paper fork is the presence of an injected
   object**, not the environment field.

Two caveats that keep this AMBER rather than PASS:

- **Enforcement is opt-in.** The mismatch refusals are gated on `_stated = getattr(self,
  'execution_environment', None) is not None` (`:866`). The module's own docstring (`:160-165`) states that
  **65 of 71 construction sites do not state an environment**. For those, the agreement check is skipped
  entirely and the branch is decided purely by executor presence.
- **An omitted per-call `execution_environment` labels the order `LIVE`** (`:490-495`, `:1917-1930`), which
  `assert_live_environment` then passes. `execute_trade` (`:1904`) was fixed to forward the engine's own stated
  value and to omit the kwarg when unstated (`:1943-1947`), and `master_executor.py:629-633` now passes
  `PAPER if self.paper_trading else LIVE`. But the default remains permissive for any new caller.

Also: **the `VYOMQUANT_MODE` / `ExecutionFlags` gate never runs on the order path.**
`SafetyMonitor.check_execution_allowed` appears only in `routers/portfolio.py:322`, `routers/orders.py:825`,
`:946`, and `backend/dag_engine.py:1293`/`:1526`/`:4765`. Grepping `core/execution_engine.py` and
`backend/exchange_executor.py` for `check_execution_allowed` or `LIVE_TRADING_ENABLED` returns **nothing**. So
the mode is a boot-time posture, not a per-order guard. A process booted in `safe` mode would still place a real
order if it reached `_execute_trade_internal` with an executor and a `LIVE` label.

And `validate_safety_config_integrity()` (`safety_config.py:18-55`) **has no call site** and compares against the
string `"live_trading"`, which is not the `"live"` value the system actually uses — so even if wired, its
production assertion could never fire.

There is also a **second, divergent definition of "live"**: `core/feature_flags.py:41`
`LIVE_TRADING_ENABLED: bool = get_vyomquant_mode("") == "live"` — import-time, case-sensitive, **not**
conditioned on the enable flag. No reader was found, so it is currently inert, but it is a second source of
truth that disagrees with `safety_config` by design.

---

## 9. RISK GATE PLACEMENT — AMBER

**Ordering is correct for the one production caller.** `master_executor.py:445-457` calls
`risk_manager.validate_trade_request(...)`; `:527-529` `continue`s the tick on a non-`APPROVED` verdict;
submission is at `:621-634`. Check-before-submit holds.

**But the gateway has no risk engine**, and says so explicitly at `execution_engine.py:893-920`:

> *"`execute_with_idempotency` contains NO risk evaluation… Risk is enforced one level up, in
> `BotRunner._inner_loop`… it means the property 'a live order has passed risk' is a property of ONE CALLER
> rather than of the gateway."*

Two consequences the source itself draws out:

- `execute_trade` **hardcodes `source="bot_runner"`** (`:1950`). So the `source != "bot_runner"` guard
  (§2 step 2) authenticates nothing — it only blocks callers honest enough to say they are not the bot runner.
  Any future caller inherits bot-runner authorisation **without** inheriting the risk check that is supposed to
  accompany it.
- `backend/execution_guard.py`, which describes itself as *"the FINAL CONTROL LAYER that validates EVERY trade
  before execution"*, is reached only from the two **cancel** endpoints — never from the submit path.

The notional ceiling is explicitly **not** a substitute: it will not catch an order that breaches
risk-per-trade, the daily loss limit, or the open-position count. Its stated purpose is the category that loses
an account in one order — a sizing bug (the audit that introduced it found `RiskEngine.calculate_position_size`
returning 125× equity on a low-ATR asset), a decimal shift, a replayed signal, or a base/quote unit mix-up. It is
skipped for market orders, where `price <= 0` makes notional incomputable (`:957-960`) — documented, not silent.

---

## 10. EXCHANGE CONNECTION OWNERSHIP — NOT VERIFIED

`place_order` (`exchange_executor.py:882-890`) takes **no** `tenant_id`/`user_id` argument. The executor is bound
to credentials at construction time, so "these keys belong to this tenant" is a property of the construction
site, not re-checked per order. `CCXTExchangeExecutor` construction sites
(`connection_engine.get_or_create_exchange`) were **not enumerated**, so per-tenant credential scoping is
**unverified**.

Mitigating: `tests/test_exchange_executor_cache_is_per_tenant.py` exists and passed in the baseline, which is
indirect evidence the cache is tenant-keyed. Not treated as confirmation of the construction sites themselves.

---

## 11. ACCOUNTING INVARIANT

| Environment | Invariant | Enforcement | Verdict |
|---|---|---|---|
| Paper | `total_equity == available_balance + locked_balance + position_market_value` | zero tolerance, recomputed per call, `InvariantViolation` raised inside the caller's transaction | **PASS** |
| Live | — | no equivalent module located | **NOT CLASSIFIED** |

Detail in `staging/MONEY_FLOW_AUDIT.md` §6. The paper engine refuses `float` inputs outright and never
increments equity — both correct, both rare. The absence of a demonstrated live counterpart is a certification
blocker, not a current risk, because live is off.

---

## 12. TRADING VERDICT

```
TRADING: NO-GO   (for live)
TRADING: GO      (for paper-only, with the caveats below)
LIVE:    OFF / NOT CERTIFIED
```

**Paper-only GO** rests on: live execution verified refused in all five permutations and requiring a code change;
manual order submission disabled; direct executor bypass impossible; strategy ownership tenant-scoped and
fail-closed; atomic execution claim; and a zero-tolerance paper accounting invariant.

Caveats that ride along even for paper-only:
- the per-user kill switch does not work across workers (process-local dict);
- the global kill switch self-expires after 24h;
- no reaper for stale `EXECUTING` rows.

**Live NO-GO / NOT CERTIFIED** rests on:

| # | Blocker |
|---|---|
| 1 | Capital/notional check has no reservation → concurrent distinct orders can both pass (TRADE-01) |
| 2 | Kill switch self-expires after 24h; `enforce_kill_switch()` unused; per-user switch absent from the live path (TRADE-02) |
| 3 | A second `create_order` exists with no token, no `clientOrderId`, no venue minimums, no kill-switch check (TRADE-03) |
| 4 | Environment agreement is opt-in; 65/71 construction sites do not state an environment |
| 5 | No risk engine in the gateway; `source="bot_runner"` is self-asserted, so the risk check is a property of one caller |
| 6 | `VYOMQUANT_MODE` is never consulted on the order path; `validate_safety_config_integrity()` is dead and could not fire anyway |
| 7 | No live accounting invariant demonstrated |
| 8 | Exchange-connection ownership not re-checked at submission; construction sites unverified |
| 9 | Retry idempotency is time-boxed by a 5-minute bucket |

**Do not enable live trading** until 1-7 are closed and 8-9 are explicitly accepted or fixed. The current state
is safe precisely *because* the gate requires a code change — that property should not be weakened to a config
flag before the above are addressed.


---

# ADDENDUM (REMEDIATION PHASE) — THE LIVE-TRADING CONCLUSION IN THIS DOCUMENT IS WITHDRAWN

Everything above is retained as the historical record. This addendum corrects it; where
the two disagree, the addendum is authoritative.

## What this document claimed

That live customer trading is OFF, verified 5/5, and that the decisive case — both
`VYOMQUANT_MODE=live` and `VYOMQUANT_ENABLE_LIVE_TRADING=true` — is refused by
`ExecutionFlags.LIVE_TRADING_ENABLED`, a hardcoded `bool = False` read from no environment
variable, so that enabling live trading requires a code change rather than a
configuration change.

## Why that is wrong

`staging/verify_live_trading_off.py` invokes exactly one thing:
`SafetyMonitor.check_execution_allowed("live_order")`. That function is **not on the path
that places an order.**

Proven by AST scan of the chain to a venue — `staging/verify_live_gate_on_real_path.py`,
output `staging/live_gate_real_RUNH8.txt`, nonce-verified:

```
backend_app/backend/master_executor.py:357   _inner_loop()                 -> NO GATE REFERENCE
backend_app/core/execution_engine.py:434     execute_with_idempotency()    -> NO GATE REFERENCE
backend_app/core/execution_engine.py:815     _execute_trade_internal()     -> NO GATE REFERENCE
backend_app/backend/exchange_executor.py:747 place_order()                 -> NO GATE REFERENCE

ANY LIVE-TRADING GATE ON THE ORDER PATH: False
```

`check_execution_allowed` has six call sites, none of them on the bot path:
`dag_engine.py:1280`, `:1512`, `:4498`, `routers/orders.py:808` (cancel), `:929`
(cancel-all), `routers/portfolio.py:292` (close-all). `ExecutionFlags` is read only by that
function and by `assert_safe_mode`. The scan also confirmed `enforce_kill_switch` and
`validate_safety_config_integrity` have **zero** call sites.

So "5/5 REFUSED" is a true statement about the function that was called, and says nothing
about whether a live order can be placed. The audit verified a control sitting beside the
order path and reported it as the control on it — the same "exists but is never reached"
defect class this audit was commissioned to find.

## What actually keeps live trading off

| Control | Kind | Strength |
|---|---|---|
| `routers/strategies.py:2104`, `:2120`, `:3883` hardcode `paper_trading=True` | literal | Absolute, for the ordinary / legacy / DAG deploy routes only |
| `Feature.LIVE_TRADING` entitlement (`strategy_service.py:1847-1850`) | entitlement | **Weak** — granted on four paid tiers (`core/subscription_engine.py:461,514,576,645`) |
| `LIVE_REQUIRES_EXCHANGE_ACCOUNT` (`deployment_binding.py:1203`) | precondition | Holds until a user connects an account |
| `BINDING_NOT_STORABLE` (`deployment_binding.py:2019`) | schema | Incidental |
| Global kill switch (`core/execution_engine.py:~623-645`) | **on-path, fails closed** | Real — and the only on-path safety gate |
| ECS pins `VYOMQUANT_MODE=paper` | configuration | Does **not** gate `BotRunner`'s live branch, because `ExecutionFlags` is off-path |

`strategy_service.py:2061` and `:2959` compute `paper_trading=(mode != MODE_LIVE)`, so
`deploy_version` with `mode="live"` builds a LIVE `BotRunner` holding a real
`CCXTExchangeExecutor`.

**Live trading is held off by configuration, entitlement and the absence of a connected
exchange account — not by a code gate on the order path.** `VYOMQUANT_MODE=live` plus the
confirmation flag does not, by itself, place an order; but neither does it get refused by
the control this document credited. Enabling live trading on the gated `deploy_version`
path is a **configuration and account** change, not the code change claimed above.

## What the order path does enforce

AST-confirmed present in `execute_with_idempotency`: `assert_live_environment`,
`assert_simulation_environment_matches`, `get_global_kill_switch`/`is_active`,
`_validate_strategy_exists`, `check_idempotent_execution`, `claim_execution`. Plus, in
`_execute_trade_internal`: the branch-point agreement assertion, the notional ceiling
(fails closed on unreadable equity), and the anti-bypass validation token.

AST-confirmed **absent**: `validate_trade`, `validate_trade_request`, `ExecutionGuard`,
`check_execution_allowed`, `ExecutionFlags`.

## Classification impact

`TRADING LIVE: OFF / NOT CERTIFIED` remains correct, and **NOT CERTIFIED is now the
operative half**. New finding **TRADE-04 (P0, OPEN)**. Recorded in
`staging/REMEDIATION_REPORT.md` section 0.


---

# ADDENDUM 2 — TRADE-04 AND TRADE-05 ARE NOW FIXED

Addendum 1's withdrawal stands and is not erased. This records the remediation.

## There is now a gate on the order path

`backend_app/core/live_execution_gate.py`. The gateway performs 13 named checks
(`REQUIRED_CHECKS`), mints an HMAC-signed `LiveOrderAuthorization` bound to one order,
and `CCXTExchangeExecutor.place_order` — the only method in the repository whose body
calls `self._exchange.create_order` — refuses to proceed without a valid authorisation
covering the complete set for that exact order and venue, then consumes it.

The artefact shape is deliberate. The withdrawn control was a flag, and a flag read in
the wrong place fails open; a **missing artefact fails closed**. The check set is
re-verified at the boundary, not only at issue, so a valid signature over a partial set is
still refused.

## Risk is now a gateway property (TRADE-05)

`execute_with_idempotency` refuses a live order whose `risk_approved` is not `True`.
`BotRunner` passes its real verdict; `execute_trade` does not forward one, so the second
door that inherited `source="bot_runner"` authorisation without the risk check can no
longer place a live order.

## Evidence — the oracle is the exchange client, not a gate function

`tests/test_trade04_live_order_gate_at_venue_boundary.py`, **31 passed**. Driven through
the real chain to a recording `create_order`, with a **mandatory positive control** (a
fully authorised order does reach the venue) so the negative rows are meaningful. Eight
prerequisite-removal cases, five boundary-bypass cases, all 13 required checks dropped
individually, and the second door — every one of them: `create_order` not called.

Regression: `staging/exec_regress_RUNP5.txt` 245 passed;
`staging/collateral_RUNR7.txt` 268 passed; `staging/collateral_RUNS8.txt` 181 passed.

Five pre-existing tests that asserted a live order proceeds were **updated to satisfy the
new precondition**, not disabled, with the reason recorded inline.

## Classification unchanged

```
TRADING LIVE: OFF / NOT CERTIFIED
```

Fixing the boundary makes the architecture safe by construction rather than by
coincidence. It is not certification. What changed is that live is now off because a
control on the order path says so.

---
---

# PHASE 6 ADDENDUM — PAPER EXECUTION, ISOLATION, AND ONE TEST THAT CALLS BINANCE

Earlier parts of this document stand unchanged. This addendum records what Phase 6 measured.

## PAPER-01 (P1) — BotRunner paper fills never reached the paper ledger · **FIXED**

The gap was a missing call, not a wrong branch: `backend_app/backend/master_executor.py` had
**zero import edges** to `backend_app/backend/paper/`. The `ExecutionEngine` PAPER branch
mutated `self.cash`, `self.positions` and `self._marks` and wrote **no table**, so a bot could
report fills all day while `paper_orders`, `paper_fills` and `paper_positions` — the tables the
six `/api/paper/*` endpoints and `PaperTrading.jsx` read — stayed empty.

Closed by an optional `paper_ledger` seam on `ExecutionEngine` that routes a bot fill through
the **existing canonical** `PaperTradingService.place_order` on the default paper account, and
by `_reconcile_from_paper_ledger()`, which makes the engine's in-process figures a *copy* of
persisted state rather than a parallel model. A ledger refusal refuses the execution. Full
design, decisions and rejected alternatives: `staging/PAPER_TRADING_CERTIFICATION.md`.

Proven against real PostgreSQL 16.2: **26 passed** for the fill/accounting matrix, **39
passed** with the isolation suite, 0 skipped, 0 xfailed.

## Paper/live isolation — the venue boundary, re-inventoried by receiver

10 call sites that look like exchange writes. Classified by **receiver**, because
`create_order` on a local repository is not a venue write:

| Class | n | Sites |
|---|---|---|
| **VENUE** | 4 | `exchange_executor.py:1081 create_order`, `:1147 cancel_order` (receiver `_exchange`); `transactional_execution_manager.py:1137 create_order`, `:1050 cancel_order` (receiver `exchange`) |
| LOCAL | 5 | `reconciliation_engine.py:754/756`, `database_scaling.py:617`, `order_state_engine.py:745`, `paper_trading.py:249` |
| UNREACHABLE | 1 | `routers/orders.py:915` — `_build_execution_engine` (`orders.py:373`) raises `HTTPException(403, DIRECT_EXECUTION_BLOCKED)` **unconditionally**, and `ExecutionEngine` has **no `cancel_order` method** |

`tests/test_paper01_paper_live_isolation.py` (**13 passed**) exercises the real production
construction path far enough to prove the negatives: in paper mode no exchange adapter is
constructed, the credential vault is never touched, and no live `create_order`/`cancel_order`
is reachable. A **live-branch negative control** is included, so the paper assertions are not
vacuously true — the LIVE branch does construct an executor.

**This is isolation by construction, not isolation observed against a venue.** See the
sandbox entry below.

## PAPER-02 (P2) — an unfundable market buy strands an open order · **OPEN**

A market buy the paper account cannot fund leaves `status` `ACCEPTED`→`OPEN`,
`filled_quantity=0`, `rejection_reason=NULL`, and the call is classified
`PAPER_INVARIANT_VIOLATION` (500). Requirement 16.5 wants a `REJECTED` order carrying a
reason. Pre-existing, reachable from `POST /api/paper/orders`, **not fixed** — the fix is a
product decision about 16.5's contract.

## Sell convention — documented, deliberately not changed

Measured on the canonical path: a sell with **no position** opens a **SHORT**, and an
**oversized** sell **reverses through zero**. The pre-fix BotRunner was long-only — its sell
branch answered `"No position found for {symbol}"`. `REDUCE_ONLY_SELLS = True` preserves the
long-only semantics for bots. Adopting the canonical shorting behaviour was rejected: it would
grant every paper bot, including spot strategies never evaluated short, the ability to short as
a side effect of a persistence fix.

## ENV-01 (P3) — the test suite makes an authenticated call to the live Binance API · **OPEN**

`tests/test_exchange_safety_fix.py::test_connection_pooling_safety` contains no mocking. It
calls `get_or_create_exchange("user_123", "binance", "key1", "secret1")`, which reaches
`ConnectionEngine.connect()` → `await self.exchange.load_markets()` over the real network:

```
GET https://api.binance.com/sapi/v1/capital/config/getall?timestamp=…&signature=…
requestHeaders {'X-MBX-APIKEY': 'key1', …}
ccxt.base.errors.AuthenticationError: binance {"code":-2008,"msg":"Invalid Api-Key ID."}
```

**No order is placed** — `load_markets` and the capital-config read are both reads — and the
key is the literal string `key1`, so there is no credential exposure. But it belongs in this
document for one reason: **a full regression run is not network-isolated and does reach a
production venue endpoint.** An audit that certifies "no venue is reached in paper mode" has to
say that the suite proving it makes an outbound authenticated request to Binance.

The test can never pass anywhere: with network it authenticates and fails; without network it
raises `ExchangeNotAvailable` from the same call. Disposition and owner:
`staging/FULL_REGRESSION_FAILURE_DISPOSITION.md` § 9.

## Sandbox end-to-end

```
PAPER SANDBOX E2E BLOCKED — NO SAFE SANDBOX CREDENTIAL
```

`exchange_keys.json` is an explicit placeholder (`# REDACTED`, body `{}`); neither `.env` nor
`backend_app/.env` holds an exchange, testnet or sandbox key; no CI workflow sets `TESTNET`,
`SANDBOX` or `BINANCE_API*`; `core/config.py` has no sandbox handling. No credential was
invented.

**`TRADING PAPER` therefore remains NOT CLASSIFIED**, and `TRADING LIVE` remains OFF and
unchanged — nothing in Phase 6 touched the live gate, and `master_executor`'s LIVE branch
passes no paper ledger.

## Also noted — see `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`

**ENV-04.** `PRODUCTION_ROUTER_ENABLED` defaults to **`"True"`** at
`core/feature_flags.py:39` and `core/safety_config.py:131` (two copies of the same line), so
its *absence* mounts `execution_router` at `/api/execution` (`main.py:717`). The
`feature_flags.py` docstring says the flag is `False (unverified safety)`; four ECS task
definitions set `"False"`; `deployment_go_no_go.md` says `True`. Observed live in this audit's
own process: `INFO:Algo22: Production execution router ENABLED`.

This compounds TRADE-06, already recorded above: `validate_safety_config_integrity()` has zero
call sites and is the only thing that would ever have *required*
`PRODUCTION_ROUTER_ENABLED=true` in production, so the contradiction has never been exercised.

---

# PHASE 7 ADDENDUM — ENV-04 RECONCILED, TRADE-06 RE-STATED

> Everything above is the Phase 6 record, preserved as written.

---

## ENV-04 · **FIXED** — and the fix removed a contradictory safety assertion

The Phase 6 note above recorded that `PRODUCTION_ROUTER_ENABLED` defaulted to `"True"` at
`core/feature_flags.py:39` and `core/safety_config.py:131`, so its *absence* mounted
`execution_router` at `/api/execution`. That is now closed:

* **One authoritative definition**, `safety_config.production_router_enabled()`, read by both
  `ExecutionFlags` classes. Default **off**; `PRODUCTION_ROUTER_ENABLED=true` is required to
  mount, in every environment.
* The enabling vocabulary was **not** widened — `1` / `yes` / `on` do not enable, but an
  unrecognised value is refused **and logged by name**, so a typo cannot be mistaken for an
  enablement.
* All nine previously-disagreeing sources now state the same value. The nine-cell matrix
  (production/staging/development × unset/false/true) is in
  `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`; **45 passed** in
  `tests/test_env04_production_router_requires_explicit_enablement.py`.

### The measurement that changed what the fix had to be

The code default was **not** what mounted the router on this machine. `backend_app/.env` —
git-ignored, and loaded by `core/config.py` *before* `safety_config` reads the flag — carried
`PRODUCTION_ROUTER_ENABLED=True`. With the variable popped from the process environment, the
predicate still answered `True` and `/api/execution/signal` was still mounted
(`staging/probe_env04_mount.py`). Flipping the code default alone would have changed nothing
locally while the report claimed otherwise. The file was aligned to `False` with a comment
recording why. ENV-03 and ENV-04 turned out to be the same file seen twice.

### TRADE-06: the contradictory assertion inside the dead validator

`validate_safety_config_integrity()` contained:

```python
production_router = os.getenv("PRODUCTION_ROUTER_ENABLED", "true").lower()
if production_router != "true":
    raise ValueError("CRITICAL: Production router disabled. ..."
                     "Production requires PRODUCTION_ROUTER_ENABLED=true")
```

It treated a **disabled** execution router as evidence of environment-variable tampering and
refused to start — the exact opposite of what the `feature_flags` docstring, `.env.example`
and all four shipped task definitions say. Both directions could not be right, and the one
worth keeping is the one that does not *require* an execution surface to be mounted. Had
this function ever been called, it would have refused to boot every deployment this
repository ships.

**That assertion is removed**, with the reasoning recorded in the function's docstring.

**The function remains uncalled, and remains documented as uncalled.** It must not be wired
as-is: its live-trading branch compares `VYOMQUANT_MODE` / `AERORA_MODE` against
`"live_trading"`, which is the `SystemMode` **enum** value and not a value either variable
ever holds — the trading modes are `safe` / `paper` / `live`. TRADE-06 therefore stands
unchanged as a finding; what changed is that it no longer contains an assertion pointing the
wrong way.

`config.validate_production_config()` was found to be a **second** uncalled validator of the
same shape (see `staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`). Both are now documented as
uncalled, and both have a test pinning the pair so the code and the note cannot drift apart.

---

## What is behind `/api/execution`, stated accurately

`execution_router` exposes exactly one endpoint, `POST /api/execution/signal`. It constructs
`ExecutionEngine()` with **no `portfolio_state`** on the line *before* its `try`, so it
raises `ValueError` and returns **500 on every request** without reaching any venue —
established in `tests/test_execution_paths_that_claim_an_exchange.py`, not newly claimed
here.

So **ENV-04 was a configuration-coherence and fail-open-direction defect, not a live-order
exposure**, and it is not reported as one. The direction still matters: the endpoint is an
order-submission surface by construction, and one repair to that constructor is all that
separates it from `execute_trade`.

---

## Live trading, unchanged

`TRADING LIVE: OFF / NOT CERTIFIED`. Nothing in this phase enabled live execution, installed
a live credential, placed an order at a production exchange, or performed any production DDL.
`core/live_execution_gate.py`'s 13 `REQUIRED_CHECKS` and the HMAC `LiveOrderAuthorization`
are untouched. The ENV-04 change moves the execution router's default in the **restrictive**
direction only.

The one new finding in this area is **CONN-01** (`staging/ENVIRONMENT_PRECEDENCE_AUDIT.md`):
the exchange connection pool keys on `sha256([user_id, exchange_id])` and omits the
credential, so a credential rotation through `POST /api/exchange/keys` does not take effect
in-process until the connection is deleted or the process restarts. Per-user isolation
holds; rotation is silently ineffective. Reported, not fixed.

---

# PHASE 8 — 2026-10-09. TRADE-01, TRADE-02, AND THREE NEW FINDINGS

**Substrate: LOCAL PostgreSQL 16.2 (pgserver). No Redis server. No venue credential. No
live trading was enabled, no exchange credential was added, no real order was placed, and
no production execution path was turned on.**

---

## TRADE-01 — two distinct executions could both pass the same ceiling — **CLOSED**

### The defect

The only capital bound on the live order path was a read-then-compare inside
`_execute_trade_internal`:

```python
_equity   = Decimal(str(self.current_equity))   # PROCESS MEMORY
_ceiling  = _equity * _max_leverage
_notional = abs(size) * abs(price or 0)
if _notional > _ceiling:  refuse
```

Nothing was reserved, decremented or locked. There was no `FOR UPDATE`, no advisory lock
and no exposure table anywhere on the path — searched for, not assumed. The only atomic
predicate was `claim_execution`, whose `UPDATE … WHERE status = 'PENDING'` guarantees that
one worker executes a **given** execution and says nothing about two **different** ones.

And two orders are easy to make different. `generate_execution_id` folds quantity and price
into the canonical string, so two orders differing in either take a different execution id
**and** a different Redis lock key. They clear the identical ceiling independently and both
proceed. Two orders at 60% of the ceiling commit 120% of it.

### The reservation

`ExecutionRecordRepository.claim_execution_within_capacity`, called from
`execute_with_idempotency` for live-bound orders. One transaction does three things in
order:

1. `pg_advisory_xact_lock` keyed on the tenant — the serialisation point that did not
   exist. Transaction-scoped, so a crashed worker cannot hold an account shut.
2. `SUM(ABS(size::numeric) * ABS(NULLIF(price,'')::numeric))` over the tenant's
   `EXECUTING` rows, excluding this execution, newer than a 300-second stale cutoff.
3. Refuse if `committed + requested > ceiling`; otherwise perform the **same** claim
   `claim_execution` performs.

**The reservation IS the `executing` row.** No new table and no second piece of state that
can leak if the process dies between writing it and claiming.

Three decisions worth recording because the obvious choice was wrong in each case:

* **`EXECUTING` only, not `PENDING`.** The first implementation summed both, on the
  reasoning that a pending order is an intent not yet refused. That **deadlocks**: two
  recorded intents of 60% each see the other's notional and both are refused, so the
  account can never use capacity it has until the stale cutoff expires. Two of the new
  tests caught it. Excluding `PENDING` is safe because `PENDING → EXECUTING` happens
  nowhere except the claim, under the lock, after the SUM — so a second order must take the
  lock and be measured against the first one's `EXECUTING` row.
* **`COMPLETED` excluded.** A filled order's exposure belongs to equity and position
  accounting, which `current_equity` already reflects. Counting it here as well would
  double-count it and shrink capacity monotonically over the account's lifetime.
* **The casts are required, not defensive.** `execution_records.size` and `price` are
  `character varying` on this schema, so `ABS(size)` fails outright with
  `function abs(character varying) does not exist`. Measured before writing the SQL.

There is deliberately **no dialect-agnostic fallback**: a fallback would be a claim with no
capacity check, which is the defect. On a non-PostgreSQL dialect the method reuses
`core.runtime_env.local_state_is_acceptable()` — the same detector
`core/bot_ownership.claim_bot` uses for a missing claim store, so the two cannot drift — and
either warns and delegates (declared local environment) or refuses (anything else).

The per-order ceiling is **unchanged and still runs**. This adds a bound across concurrent
orders; it does not replace the one on each.

### Evidence

`tests/test_trade01_concurrent_exposure_postgres.py` — **28 passed**, local PostgreSQL,
genuinely multi-connection.

The defect is demonstrated as a **passing** test before the fix is demonstrated at all:
`test_without_the_reservation_two_rows_both_reach_executing` races the *original*
`claim_execution` from two separate connections and asserts the database ends up holding
more than the account's fundable notional in `EXECUTING` rows. Without that, the next test's
result would be tautological.

Then: two connections released by a `threading.Barrier` admit exactly one; ten concurrent
attempts at 25% of the ceiling admit **exactly four**; a refusal leaks no capacity; `FAILED`
releases it; `COMPLETED` never holds it; a one-hour-old `EXECUTING` row is released while a
one-second-old one is **not** (the negative control that stops the stale cutoff from
swallowing live exposure); idempotency holds sequentially and under a five-connection race;
the advisory lock is released on both the success and the refusal path, asserted from
`pg_locks`; one tenant cannot block or consume another's capacity.

The live-order path is never enabled in any of it — the exchange stub raises on any call at
all, so a regression that let an order through fails loudly rather than reaching a venue.

`tests/regression/baseline/live_order_path.json` was re-recorded through the sanctioned
`_baseline_note` process. The collaborator sequence changes by **exactly one entry in the
same position** (`claim_execution` → `claim_execution_within_capacity`); four of the five
scenarios are byte-identical.

---

## TRADE-02 — the kill switch — **CLOSED**, and the finding was wrong about the mechanism

### Correction to this document

TRADE-02 was recorded here as "the kill switch may not be enforced on the live order path".
That was the least of it, and it was partly wrong: **the live path does consult the switch**,
inline in `execution_engine.py`, and the new suite drives the real `GlobalKillSwitch`
through the real `ExecutionEngine` to prove the order is blocked with the venue never
called.

A finding that points at the wrong mechanism is worse than a vague one, because it directs
remediation at code that was already correct. Recorded as `CORR-P8-04`.

### What was actually wrong — three things

**1. KILL-01 (new, P1, FIXED) — the switch failed OPEN.** `_redis_get` caught every
exception and returned `None`, so `None` meant **both** "Redis answered and the key is not
set" **and** "Redis could not be read". The fail-closed `except` at the bottom of
`is_active()` was therefore **unreachable** for a connection error: control fell through to
`if value is None: return False`, and a Redis outage reported the kill switch as NOT ACTIVE.
A *triggered* switch stopped blocking the moment Redis went away — the exact opposite of the
module's own documented fail-closed policy, on the one control whose entire purpose is to
stop trading.

It was broader than an outage. `redis_manager` is a `__getattr__` proxy that raises
`AttributeError` when the manager was never initialised, so **any worker that had not
brought Redis up also read "switch off"**.

**2. The mirror image — permanent lockout.** Once the local latch engaged, recovery lifted
it only `if probe_result is not None`. The resting state of a healthy system *is* `None`
(key absent). So a fully recovered Redis with no kill-switch key could **never** lift the
latch, contradicting the same docstring's promise that transient failures "do NOT
permanently deadlock the platform". Same root cause, opposite direction.

Both are fixed by making the three outcomes distinguishable: a value, a genuine absence
(`None`), or `RedisUnavailable` raised. Recovery is now decided by whether Redis
**answered**, not by whether the key held a value.

**3. TRADE-02's own residue — an asymmetric TTL.** `activate()` wrote the state key with
`ex=86400` while `deactivate()` wrote the "off" value with **no** expiry. "Trading stopped"
decayed back to "trading permitted" after 24 hours unattended, while "permitted" was durable
forever. A safety control that drifts toward the unsafe state is worse than one that is
absent, because it looks present. Both the state key and its metadata now carry no TTL,
matching this repository's own rule for state keys (`RedisDatabase.STATE: 0  # No TTL`).

### Evidence

`tests/test_trade02_kill_switch_fails_closed.py` — **30 passed**.

A controllable fake Redis with a virtual clock, TTL support, a failure flag and an operation
log, patched over the module attribute `_redis_get` imports at call time.

* **Fail-closed:** an unreachable backend blocks; an uninitialised `redis_manager` blocks; a
  healthy backend with no key permits (control, so the above is not "always True").
* **Two pre-fix negative controls.** `test_the_pre_fix_redis_get_would_fail_open` restores
  the old body verbatim and asserts the switch then reports trading as permitted during an
  outage. `test_the_pre_fix_probe_would_never_lift_the_latch` shows the old recovery
  condition is never taken on a healthy backend. The fail-closed behaviour is therefore
  attributable to this change and not to something incidental.
* **Expiry** asserted from the operation actually issued — `activate()` must issue `set` and
  must **not** issue `setex` — and behaviourally, by advancing the clock past 86,400s and
  finding the switch still engaged. Paired with a control that writes the key the *pre-fix*
  way and shows it **does** expire under the same advance, so the assertion is not vacuous.
* **Multi-process:** two instances sharing one store agree; a fresh instance after a restart
  still sees the switch; two **real OS processes** against a shared file-backed store agree,
  with a negative control against an empty store in the same child run. The store is a file
  rather than a Redis server (none was available), so what this proves is that the class
  derives its answer from the backend and not from process memory.
* **Outage divergence:** the API triggers the switch while Redis is down, the write fails,
  and a second worker with nothing to read **still blocks**. Before the fix it kept trading
  while an operator believed the system was stopped.
* **The boundary:** the real `GlobalKillSwitch` wired into the real `ExecutionEngine` returns
  `status: blocked` and the venue is never called; a **cleared** switch is no longer the
  refuser (and the order is still refused by the next gate down, so a cleared switch is not
  a green light).

### A consequence worth recording on its own

The fix exposed that **the repository's own live-order tests depended on the defect**.
`tests/test_live_risk_gate_enforcement.py`'s two positive tests drive the real engine with
no Redis initialised, and they were green **only because the kill switch failed open** — an
order that previously reached the CCXT mock is now stopped, with the log line naming the
`AttributeError`. Their implicit precondition is now explicit, plus an assertion that the
engine genuinely consulted the switch so the stub cannot hide a removed gate. The refusal
test in the same file passed throughout, which is the consistency check that confirmed the
diagnosis — a refusal test cannot be broken by an extra refusal.

---

## TRADE-10 (new, FIXED) — the documented atomic claim had never executed

> **Numbering note.** This finding was first written up as "TRADE-06", which was already
> taken by the dead-validator finding recorded earlier in this document. It is **TRADE-10**.
> See `CORR-P8-06`.

Found by building the capacity reservation on top of `claim_execution`'s predicate.

`claim_execution`'s PostgreSQL fast path wrote `SET status = 'executing'` and matched
`WHERE (status = 'PENDING' OR status = 'pending')`. `execution_records.status` is the enum
`executionstatus`, whose labels are **UPPERCASE only**, so PostgreSQL cannot parse either
lowercase literal as a value of that type. The statement raised
`invalid input value for enum executionstatus: "pending"` on **every call**, the `except`
caught it, logged "falling back", and the ORM path silently did the work.

This document recorded that `UPDATE … RETURNING` as "the only atomic predicate on the live
order path". It had never executed there. The single-worker guarantee did hold — the
fallback is also a single guarded `UPDATE` — but the mechanism named in the audit was not
the mechanism doing the work, and every execution claim emitted a warning. Recorded as
`CORR-P8-05`.

Measured directly (`staging/probe_claim_execution_branch.py`):

```
RAW_LOWERCASE_PREDICATE=REJECTED  invalid input value for enum executionstatus: "pending"
RAW_UPPER_CAST_PREDICATE=ACCEPTED
CLAIM_EXECUTION_RESULT claimed=True   FALLBACK_WARNINGS=1
```

Both halves are now uppercase and the predicate is `upper(status::text) = 'PENDING'`, which
is correct whether the column is an enum or a varchar. The regression watches for the
fallback's own warning rather than for the end result, because the end result was correct
throughout.

---

## New findings, OPEN

### KILL-02 (P1) — the kill switch can be reset by anyone who can call it

`GlobalKillSwitch.deactivate(reason, triggered_by)` has no credential, role, operator or
actor parameter. `triggered_by` is free text the caller supplies and nothing verifies it.
Its audit trail is a Redis **list** with 7-day retention — so the record of who stopped and
restarted trading expires, and expires from the same store whose unavailability the switch
is meant to survive.

Pinned as a passing test (`test_reset_is_audited_but_takes_no_authorization` asserts the
signature), so a future change that adds real authorization fails that test and forces this
finding to be updated. Not fixed: adding authorization requires deciding where operator
identity comes from on this path, and guessing would be worse than recording the gap.

### KILL-03 (P1) — no operator-facing path engages the global kill switch

Two measured facts that compound:

* `enforce_kill_switch()` — the module's own documented enforcement helper, which raises
  `ExecutionBlocked` — has **zero call sites** anywhere in `backend_app`, `backend`,
  `scripts` or `core`. It works; nothing uses it. The protection on the order path is the
  inline `is_active()` check instead, which is why orders *are* blocked.
* `POST /api/admin/kill-all` requires `confirm_code == "HALT"`, an operator identity and an
  audit event — and then calls `fleet.shutdown_all()`. **It never touches
  `GlobalKillSwitch`.**

So the audited, authenticated, human-facing "stop everything" control stops the in-process
fleet and leaves the distributed switch untouched, while the distributed switch has no
human-facing control at all. Asserted as a passing test that checks **both** that
`enforce_kill_switch` raises **and** that it has no call sites, with a floor on the number of
files scanned so "none found" is evidence rather than an empty search.

### KILL-04 (P2) — the venue boundary records `"kill_switch"` as a claim, not a re-check

`execution_engine.py` passes the literal string `"kill_switch"` in
`authorize_live_order(checks=[...])`. The gate verifies that the caller **asserted** the
check, not that the check passed — so a switch triggered after the inline `is_active()` call
earlier in the same request is not noticed at the boundary, and the order proceeds carrying
an authorisation naming a check whose state has since changed. The window is short and the
inline check does run, which is why this is P2. Recorded because the gate's value rests
entirely on its checks being verified rather than declared.

### TRADE-11 (P2) — a market order contributes nothing to measured exposure

> **Numbering note.** First written up as "TRADE-07", which was already taken by the
> stale-`EXECUTING`-reaper finding. It is **TRADE-11**. The real TRADE-07 is *partially
> mitigated* by this phase: the capacity reservation's 300-second stale cutoff means a
> stranded `EXECUTING` row stops holding capacity against the account, though the row
> itself remains unclaimable and the finding stands. See `CORR-P8-06`.

`execution_records.price` is nullable and a market order arrives with no usable price, so
`size * price` is `NULL`, `SUM` skips it, and such an order holds no capacity against the
ceiling.

This is the **same** blind spot the pre-existing per-order check already documents, and the
capacity reservation deliberately reproduces it rather than inventing a different rule — two
capital bounds disagreeing about what an order is worth would be worse than one shared
limitation. The venue's own balance check is the only backstop. Pinned as a passing test so
closing it later fails that test and forces this section to be updated. Not fixed: it needs
a reference price source, which is a product decision.

### The emergency policy, stated so it is not assumed

`activate()` cancels nothing and closes nothing. It blocks **new** orders. Resting orders at
the venue and open positions are unaffected. That is deliberate, but it is not what "kill
switch" suggests, so it is pinned by a passing test
(`test_activation_refuses_new_orders_and_does_not_flatten`) — a reader must not assume that
triggering it protects existing exposure.

---

## Live trading status after Phase 8

**OFF / NOT CERTIFIED**, unchanged and deliberately so. The live order path remains
unreachable by construction through several independent refusals, and the Phase 8 work added
a capacity reservation to that path rather than opening it. No credential was added, no
venue was contacted, and `TRADING_PAPER` remains **NOT CLASSIFIED** because venue isolation
here is isolation *by construction* rather than venue-observed sandbox evidence.


# PHASE 9 — 2026-10-10. OPERATIONAL SAFETY (KILL-02/03/04, TRADE-07/11/12)

Phase 9 closed the operational-safety findings this document raised and left open at the end
of Phase 8. The Phase 8 prose above is retained as the historical record of the
pre-remediation state; this section supersedes it where a finding is now closed. Every
finding below was independently re-verified against the code by
`staging/verify_phase9_open_findings.py` (11 checked, 11 remediated/reclassified, 0 still
present) and backed by focused tests, with migrations applied locally only.

## KILL-02 — the kill-switch reset now requires authorization evidence, and the audit is durable — CLOSED

The Phase 8 note above ("Pinned as a passing test `test_reset_is_audited_but_takes_no_authorization`")
described the open state. It is superseded. `GlobalKillSwitch.deactivate` now takes
`operator_id`, `authorization_source` and `acknowledge_holds` as **keyword-only parameters
with no defaults**, so a caller that has not established who is acting, on what authority, and
which independent holds they have reviewed **cannot call the method at all** — the same
structural reasoning TRADE-05 applied to `risk_approved`. `activate()`'s equivalents stay
optional on purpose: engaging a halt is the safe direction and must remain available to the
four automatic triggers that have no operator identity.

A reset is now a thing that can be **refused**: `KillSwitchResetRefused` with codes
`RESET_REASON_MISSING`, `RESET_UNAUTHORIZED`, `RESET_CONTENDED`, `RESET_STATE_UNVERIFIABLE`
and `RESET_HOLDS_UNACKNOWLEDGED`. A new hold-set registry means a reset refuses while any
unacknowledged independent halt is active, and `_redis_smembers` **raises** rather than
returning an empty set, so a Redis outage cannot be read as "no holds". The authoritative
state is written **first** and the local latch cleared **last** — the reverse of the previous
order, which was the split brain its own docstring warned of.

The audit trail is no longer a self-expiring Redis LIST: `core/safety_audit.record_safety_transition`
writes a row (table from migration `028_safety_control_audit.sql`, append-only RLS) for every
**applied and every refused** transition, with **permanent** retention — matching the
repository's only other absolute-retention statements rather than an invented duration.

**The pinned test was inverted, not deleted.** `test_reset_is_audited_but_takes_no_authorization`
is now `test_reset_now_requires_authorization_evidence`. Evidence:
`tests/test_kill03_authoritative_global_halt.py` (42 passed);
`tests/test_trade02_kill_switch_fails_closed.py`.

## KILL-03 — the authoritative global kill switch is now engaged by an operator path and read on the order path — CLOSED

The Phase 8 note ("`enforce_kill_switch()` ... has **zero call sites**") is superseded.
`await enforce_kill_switch()` is now the **last statement before `create_order`** in
`exchange_executor.place_order` — its only call site, deliberately the narrowest one.
`POST /api/admin/kill-all` now calls `kill_switch.activate(...)` **before**
`fleet.shutdown_all()` (the order is load-bearing: `shutdown_all` can raise partway and must
not leave the switch disengaged) and reports the switch's resulting authoritative state plus
the active holds in its response. A new `GET /api/admin/kill-switch` exposes the state.
Independently re-verified: 1 call site; admin engages the switch.

## KILL-04 — the venue boundary now re-takes the kill check instead of asserting a stale claim — CLOSED

The same `enforce_kill_switch()` call site closes KILL-04: it **reads live kill state
immediately before the venue write**, so the window between the earlier inline check and the
order is closed by a genuine re-read rather than a recorded `"kill_switch"` claim string.
`enforce_kill_switch` raises `ExecutionBlocked` (not an `OrderResult`), failing closed.
Confirmed by AST that this is a real call node, not a comment.

## TRADE-07 — stale EXECUTING rows now have an age-based recovery that adjudicates rather than resubmits — CLOSED

`ExecutionRecordRepository.classify_stale_executions` buckets rows older than
`RECOVERY_STALE_AFTER_SECONDS` (900s, deliberately longer than the 300s capacity cutoff) into
*failed-before-submission* (→ FAILED), *ambiguous* (reconcile by `clientOrderId`, **never
resubmit**, else UNKNOWN + alert) and *order-id-set* (existing watchdog).
`resolve_stale_execution` transitions **only from EXECUTING**, in one statement, to a narrow
destination set — a phantom fill cannot be asserted from the absence of information.
`order_watchdog.recover_unsubmitted_executions` is wired into the existing 30s loop. An
evidence seam (`submitted_at`) is written in its own committed session before `create_order`,
failing closed if it cannot be. Evidence:
`tests/test_trade07_stale_execution_recovery_postgres.py` (27 passed, real PostgreSQL).

## TRADE-11 — a missing, empty or zero price now fails closed instead of vanishing from measured exposure — CLOSED

The Phase 8 note ("`test_a_market_order_with_no_price_is_documented_not_silently_zero`")
described the deliberately-reproduced blind spot. It is superseded. The capacity SQL still
uses `NULLIF(price,'')::numeric` **but now additionally COUNTS** rows with no usable price
(`price IS NULL OR btrim(price)='' OR NULLIF(btrim(price),'')::numeric=0`) `AS unusable`, and
the reservation **rolls back and refuses** while any exist, because committed exposure is then
unknowable and at least the computed sum. This is strictly fail-closed — it can only refuse
orders that previously passed. `_capacity_bounds_for` applies the matching rule to the
incoming order (a market order with `price<=0` is refused on the live capacity path, since no
market-data valuation policy exists), and `risk_engine` validates price with
`InvalidOperation` handling. The tripwire was **inverted**:
`test_a_priceless_in_flight_row_now_refuses_rather_than_vanishing`,
`test_every_unusable_price_form_refuses`,
`test_an_unusable_price_refuses_rather_than_counting_as_zero`. Verified with AST that the
`price or 0` form is gone from executable engine code (the remaining occurrences are
comments) and that the NULLIF is now a detect-and-refuse guard rather than a vanishing SUM.

## TRADE-12 (NEW) — the venue id no longer collides with the orders foreign key — CLOSED

Found while building the TRADE-07 recovery. `execution_records.order_id` carries a **foreign
key to `orders(id)`**, but the engine was writing the **venue** order id into it — so every
real completion raised `23503` (foreign_key_violation) and left the row stuck in EXECUTING, a
guaranteed producer of exactly the stale-EXECUTING population TRADE-07 remediates. Migration
`030_execution_venue_order_id.sql` adds a dedicated `venue_order_id` column (varchar, nullable,
**no FK** — a third-party identifier can have no local parent) with a partial index;
`update_status` records the venue identifier there, and `order_id` keeps its FK for an order
this platform actually created. The `live_order_path` regression baseline was re-recorded
through the sanctioned `_baseline_note` process: the `update_status` collaborator call swaps
its `order_id` argument for `venue_order_id`, same call, same position in the sequence.

## Dead-code note (positions_canonical_shape listener) — unchanged

The P2 "probably dead" `create_order` on `exchange_websocket_listener` (above) was **not** in
Phase 9's scope and is unchanged. It is noted here only so a reader does not mistake its
silence for a closure.

## Regression

Both lanes (`FULLREG_P9FINAL` CI-comparable, `FULLREG_P9PGFINAL` PostgreSQL) were re-run on
the fully-remediated tree; counts are recalculated and cross-checked in
`FULL_REGRESSION_FAILURE_DISPOSITION.md` and `staging/fullreg_phase9_counts.json`. No live
trading was enabled, no exchange credential invented, no production DDL run.

## Launch classification after Phase 9

* **PAPER-ONLY LAUNCH — NO-GO.** The operational-safety posture is materially stronger, but no
  paper sandbox end-to-end run against a venue has ever been performed, and open findings
  remain (TENANT-01, QUOTA-01, CONN-01, and lower-severity carry-forwards).
* **TRADING LIVE — OFF / NOT CERTIFIED.** Unchanged and deliberately so. A genuine
  live-kill-switch re-read at the venue boundary and an authorised, durable reset are safety
  *by construction*, not live certification.
* **TRADING PAPER — NOT CLASSIFIED.** No venue-sandbox evidence exists in any phase.
* **PRODUCTION — NOT CLASSIFIED.** No production DSN existed in any phase; migrations 028–030
  are verified locally only.


# PHASE 10 — 2026-10-10. RECON-01 (exchange reconciliation not operational), CONN-01

Phase 10 assessed the exchange-reconciliation startup and fixed a connection-lifecycle
defect. Verified by `staging/verify_phase10_open_findings.py`.

## RECON-01 (NEW, OPEN) — venue order/fill reconciliation is not operational

There are three distinct reconciliation components, and they are not the same thing:

1. `core/reconciliation_scheduler.py::ReconciliationScheduler` — a mismatch-ESCALATION loop
   that polls `reconciliation_mismatches` every 30s and kill-switches on CRITICAL. It **is**
   started in `main.py`'s lifespan (`start_reconciliation_scheduler()`), with a visible
   ACTIVE/INACTIVE status, and environment mismatches ARE produced for it
   (`backend/execution_environment.py::_record_mismatch`).
2. `backend/exchange_reconciliation.py::ExchangeReconciliationService` — the component that
   would FETCH orders from the venue and COMPARE them to internal records (i.e. PRODUCE the
   order/fill mismatches). Its `start_reconciliation_loop` has **zero callers** anywhere in
   `backend_app` (measured by AST).
3. `backend/reconciliation_worker.py::reconciliation_worker` — a singleton its own docstring
   records as never started.

So the escalation loop runs, but **nothing performs the venue order/fill comparison**, and
nothing fails loudly about it. This is reported OPEN (P2), **not fixed**: starting the service
needs a live or sandbox exchange, which this engagement forbids. The service DOES fail safe on
exchange errors — `_fetch_exchange_orders` re-raises rather than returning an empty list
(UK-01), so a future wiring will not assume filled. Pinned by
`tests/test_exchange_reconciliation_startup_assessment.py` (4 passed), which FAILS if the
service is wired (so the gap cannot close silently) or if the escalation scheduler stops being
started.

## CONN-01 — FIXED

`store_keys` now evicts the pooled exchange client on a credential rotation (see the
AUTH_RLS_AUDIT.md and MONEY/REMEDIATION Phase 10 notes). A stale credential is no longer
served in-process until restart.

## Classification after Phase 10 (UNCHANGED)

* **TRADING LIVE — OFF / NOT CERTIFIED.** No live trading, no credential, no real order.
* **TRADING PAPER — NOT CLASSIFIED.** No venue-sandbox evidence; RECON-01 means venue
  reconciliation is not even operational, which is a further reason paper cannot be
  classified from local evidence.
* **PRODUCTION — NOT CLASSIFIED.**
