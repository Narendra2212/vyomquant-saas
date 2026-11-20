# FINAL PRE-LAUNCH BUGS

**Audit date:** 2026-10-05
**Target:** Aerora / VyomQuant crypto algorithmic trading SaaS
**Scope:** `backend_app/` (391 files, 252,600 LOC), `algo22-terminal/src/` (207 files), `tests/` (415 files, 12,509 tests)
**Method:** code inspection, then reproduction by execution. Every defect marked `REPRODUCED` was made to happen, not inferred.

---

## How to read this document

| Field | Meaning |
|---|---|
| **Severity** | P0 = launch blocker. P1 = launch blocker if it touches money, trading, auth, authz, data integrity, billing or execution. P2 = fix soon. P3 = cosmetic / hygiene. |
| **Evidence** | `REPRODUCED` = made to occur by running code. `READ` = established by inspection only. `BLOCKED` = could not be verified in this environment. |
| **Status** | `FIXED` = code changed and a regression test passes. `OPEN` = not fixed. `RETRACTED` = I claimed it and was wrong. |

One entry below is **RETRACTED**. It is left in place rather than deleted, because a pre-launch audit that silently drops its own errors cannot be audited itself.

---

## Summary

| Severity | Found | Fixed | Open |
|---|---|---|---|
| P0 | 6 | 6 | 0 |
| P1 | 11 | 6 | 5 |
| P2 | 7 | 2 | 5 |
| P3 | 3 | 0 | 3 |
| **Total** | **27** | **14** | **13** |

Retracted: 1 (BUG-R01).

**All P0s are fixed.** The five open P1s are BUG-012 (short `JWT_SECRET` only warns), BUG-013 (legacy deploy endpoint), BUG-014 (`ExecutionGuard` not in the order path — partially mitigated), BUG-016 (notional not debited) and the residual cross-currency fee summation noted under BUG-017. Of those, **BUG-016 is the one that touches money and is not mitigated**, and it is the single most important remaining item.

**Note on BUG-027.** It was found late, while auditing production configuration rather than the cache, and it is the second most serious finding in this document — because it silently defeated the fix made earlier for BUG-003. A fail-closed exception handler protects nothing when the failure mode is a confident wrong answer instead of an error.

---

# P0 — LAUNCH BLOCKERS

---

## BUG-001 — One tenant's live orders execute on another tenant's exchange account

```
ID            BUG-001
Severity      P0
Component     Exchange execution / multi-tenancy
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`get_exchange_executor` memoised exchange clients in a process-global dict keyed `f"{exchange_id}:{sandbox}"`. That key contains nothing identifying the account. Its only production caller passes the API key and secret it has just decrypted from the vault for one specific user. In a worker process that had already started a Binance bot for trader A, trader B's call matched A's cache entry and was handed back A's client — still holding A's credentials.

`CCXTExchangeExecutor.place_order` signs with `self._api_key` / `self._api_secret`. So this is not a disclosure of data; it is a disclosure of *funds*. Every order trader B's strategy produced would have been placed on trader A's exchange account, against A's balance, and would have appeared in A's trade history and PnL.

**Steps**
1. Call `get_exchange_executor(exchange_id="binance", api_key="A_KEY", api_secret="A_SECRET", sandbox=False)`.
2. Call it again with `api_key="B_KEY", api_secret="B_SECRET"`.

**Expected** Two distinct executors, each holding its own caller's credentials.

**Actual**
```
a is b                 -> True
b._api_key             -> 'USER_A_KEY_AAAAAAAA'
b._api_secret          -> 'USER_A_SECRET_AAAAAAAA'
```

**Root cause**
The cache identity omitted the account. `connection_engine.get_or_create_exchange` — the *other* pool in the same codebase — had this right the whole time: it keys on `sha256(json.dumps([user_id, exchange_id]))`. The two pools diverged and only one was correct.

**File** `backend_app/backend/exchange_executor.py` (`get_exchange_executor`), caller `backend_app/backend/master_executor.py` (`BotRunner._recover_live_state`)

**Fix**
`user_id` is now a **required, keyword-only** parameter. Keyword-only so it cannot be passed positionally into a credential slot; required with no default because a default would silently recreate the defect for any caller that forgot it. The key is a SHA-256 digest over a length-prefixed encoding of `(user_id, exchange_id, mode, api_key, api_secret, password)`:

- Length prefixing means `("a","bc")` and `("ab","c")` cannot collide.
- Including the credentials means a trader who **rotates** a compromised key gets a fresh client rather than one still holding the revoked secret — without this, every order would be rejected by the venue until the worker restarted, with no way for the trader to recover.
- A returned cache hit additionally re-checks that the held credentials match the requested ones, and logs `CRITICAL` and rebuilds if not. One string comparison per order is worth it when being wrong means trading someone else's account.

Added `release_exchange_executor` so a stopped bot stops pinning its decrypted secret in process memory.

**Regression test** `tests/test_exchange_executor_cache_is_per_tenant.py` (12 tests)

---

## BUG-002 — Any holder of `JWT_SECRET` can authenticate as any user, with operator role

```
ID            BUG-002
Severity      P0
Component     Authentication / authorization
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`decode_token_local` verifies Supabase ES256 tokens against the JWKS endpoint. When that fails it falls back to a branch for the test suite's own HS256 tokens, gated on the environment:

```python
current_env = (os.environ.get("ENV") or getattr(settings, "ENV", "") or "").lower()
if current_env in ("testing", "test", "development", "dev", "local"):
```

`core/config.py` declares `ENV: str = os.getenv("ENV", "development")`. **A deployment that never set `ENV` therefore resolved to `"development"`, which is a member of that set.**

### Exposure — corrected after checking the deployment artifacts

My first write-up of this said "the fallback was live in production by default". **That overstated it, and the correction matters.** Both ECS task definitions in the repository set `ENV=production` explicitly:

```
ecs-task-definition-full.json / vyomquant-api   ENV = production
new_task_def.json             / vyomquant-api   ENV = production
```

So in the ECS deployment the gate was closed and the forged-token path was **not** reachable. `tests/test_no_hs256_fallback_in_production.py` already argued exactly this, and it was right.

What the gate actually depended on was **one environment variable being present on every deployment surface, with nothing behind it**. Any surface that omitted it — a new environment, a hand-rolled task revision, a local process pointed at a production database, a container run for a one-off migration — had no authentication at all. A control whose entire strength is "we remembered to set a variable" is a single point of failure, and this one's failure mode is total.

Severity stays **P0** on blast radius, with exposure recorded as **contingent and probably not realised in the ECS deployment**. The fix converts it from a configuration dependency into a property of the code.

Why this is total rather than partial:

- `sub` is the identity every router scopes its queries by, so an arbitrary `sub` is impersonation of any account — including reading its portfolio and placing orders with its exchange keys.
- `get_admin_user` / `get_operator_user` read `app_metadata.role`, which the forger also controls. The same token reaches the global kill switch and user-status administration.
- The signing key is `JWT_SECRET` / `SUPABASE_JWT_SECRET` — an ordinary application secret that lives in `.env` files, CI configuration and ECS task definitions. Supabase's ES256 private key never leaves Supabase. The fallback demoted the trust root from a key nobody here holds to a key many people here hold.

**Steps**
1. Unset `ENV` and `ENVIRONMENT`.
2. Mint `HS256({"sub": <any uuid>, "aud": "authenticated", "iss": "algo22-test", "exp": <future>, "app_metadata": {"role": "operator"}, "aal": "aal2"}, JWT_SECRET)`.
3. Call `decode_token_local(token)`.

**Expected** Rejection. A production deployment must not accept a token it can mint itself.

**Actual**
```
settings.ENV              -> 'development'
RESULT: TOKEN ACCEPTED
   sub                    -> '11111111-2222-3333-4444-555555555555'
   app_metadata.role      -> 'operator'
   aal                    -> 'aal2'
```

**Root cause**
The gate consulted `settings.ENV`, whose **default** is indistinguishable from a deliberate setting. Conflating "nobody configured this" with "this is a development box" is the whole defect.

**File** `backend_app/core/auth_middleware.py`

**Fix**
New `_test_token_fallback_allowed()`, two narrowings:

1. **Only the real environment variables are read.** `settings.ENV` is not consulted. An unset or blank value returns `False`.
2. **Only `testing` and `test` are admitted.** `development`, `dev` and `local` are removed. `tests/conftest.py` sets `ENV=testing` at import time, so the suite is unaffected; a developer working against real Supabase uses real Supabase tokens and never needed this path.

`_decode_test_hs256_token` now shares the same gate rather than carrying its own copy of the list — the drift between those two copies is how the defect survived. This follows a precedent already in the codebase: `core/execution_engine.py`'s `_SYNTHETIC_STRATEGY_ID_ENVS` made exactly this narrowing, for exactly this reason.

**Regression test** `tests/test_test_token_fallback_fails_closed.py` (18 tests)

**Related, not fixed:** the resolved `JWT_SECRET` in this environment is **24 characters** — below RFC 7518's 32-byte minimum for HS256 — and `config.py` only emits a warning. See BUG-012.

---

## BUG-003 — A Redis outage converts order retries into duplicate live orders

```
ID            BUG-003
Severity      P0
Component     Order idempotency
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`IdempotentExchangeSubmission.check_duplicate_submission` ended:

```python
except Exception as e:
    logger.error(f"Duplicate check failed: {e}")
    # Fail safe: assume not duplicate to prevent blocking
    return False
```

`False` means "not a duplicate", so the caller proceeds to submit. The comment calls this fail-safe. It is fail-**open** on the only question the module exists to answer. The module is named `idempotent_exchange_submission`; a Redis outage disabled precisely the property it is named after.

Not blocking is not a virtue here. An order that was recorded but never submitted is recoverable by a human. An unintended second position is not.

**Steps**
1. Replace the Redis client with one whose `exists()` raises `ConnectionError`.
2. Call `check_duplicate_submission("client_abc", "tenant-1")`.

**Expected** Refusal — "I cannot determine whether this already exists, so nothing will be sent."

**Actual** `False` — reported as a new order, cleared to submit.

**Secondary defect in the same path (also fixed):** the gate was a read (`exists`) followed by a separate write (`setex`). Two workers consuming the same signal both read "new", both write, both submit. With N pods on one queue that window is reached routinely, producing N real orders for one signal.

**Root cause**
Two separate errors of direction: a fail-open exception handler, and a non-atomic test-and-set.

**File** `backend_app/backend/distributed_execution/idempotent_exchange_submission.py`

**Fix**
- New `IdempotencyUnavailable` exception. `check_duplicate_submission` now **raises** rather than returning a verdict, because a verdict can be ignored and the one being ignored was "I don't know".
- New `claim_submission()` — a single `SET key value NX EX ttl`. Exactly one caller gets `True`.
- The submission path uses the atomic claim and returns `error="IDEMPOTENCY_UNAVAILABLE"` with "Nothing was sent to the exchange" when the store is unreachable.
- Payload recording is now explicitly bookkeeping, separated from the gate, so a failure to record the payload cannot permit a second submission.

**Regression test** `tests/test_idempotent_submission_fails_closed.py` (9 tests, including a 20-way concurrent claim asserting exactly one winner)

---

## BUG-004 — An ambiguous submission failure released its idempotency claim

```
ID            BUG-004
Severity      P0
Component     Order idempotency / exchange reconciliation
Evidence      REPRODUCED
Status        FIXED
```

**Description**
The exchange-submission `except` block called `_remove_submission_record(...)` under the comment "allow retry". The exception that lands there includes the case that matters most: **the request reached the venue, the venue accepted the order, and the response was lost** to a timeout or a dropped connection. Releasing the claim there means the retry places a second real order.

An exception is not evidence that nothing happened. It is evidence that we do not know what happened, and "unknown" must be resolved by reconciling against the venue — not by assuming the optimistic branch.

**Steps**
1. Make the execution engine raise `TimeoutError` during submission.
2. Call `submit_order_idempotent(...)`.
3. Call it again with identical arguments (same deterministic `client_order_id`).

**Expected** The retry is refused as a duplicate, and the first result is flagged for reconciliation.

**Actual** The claim was gone; the retry proceeded to submit again.

**File** `backend_app/backend/distributed_execution/idempotent_exchange_submission.py`

**Fix**
The claim is **retained** on an ambiguous failure and the result carries `requires_reconciliation: True`. The claim expires on its own after the 24-hour TTL, so nothing is stranded permanently — it just stops an automatic retry from doubling a position inside the window where ambiguity is live.

**Regression test** `tests/test_idempotent_submission_fails_closed.py::test_a_lost_response_keeps_the_claim_so_a_retry_cannot_double_the_order`

---

## BUG-005 — Order normalization crashed on every tick-size venue, and silently ignored venue precision

```
ID            BUG-005
Severity      P0
Component     Exchange execution / order precision
Evidence      REPRODUCED
Status        FIXED
```

**Description**
Two independent defects in the same two functions, both on the single code path that reaches a real exchange.

**(a) Tick-size precision raised `TypeError`.** CCXT reports `market["precision"]["price"|"amount"]` under two conventions — a count of decimal places, or the step itself. Binance, Kraken, Coinbase and Bitfinex are step venues. The code assumed a count and built its quantizer as `"0." + "0" * precision`:

```
normalize_size('BTC/USDT', 0.123456789)
-> TypeError: can't multiply sequence by non-int of type 'float'
```

This fires **inside `place_order`, on the live path, after the idempotency claim has already been taken** — so the strategy's intent is consumed and no order exists.

**(b) The market lookup never matched.** `self._markets` is keyed by CCXT's unified symbol (`"BTC/USDT"`) but `place_order` passed the raw symbol it was given (`"BTCUSDT"`). Every lookup missed and both normalizers fell through to their 8-decimal default — silently:

```
normalize_size('BTCUSDT',  0.123456789) -> 0.12345678   <- silent 8dp fallback
normalize_size('BTC/USDT', 0.123456789) -> 0.123        <- the venue's real precision
```

So order precision on the live path was either a crash or quietly wrong for every order ever placed.

**Root cause**
A precision figure was assumed to be in one of CCXT's two conventions without checking, and a lookup key was constructed in a different spelling from the dictionary it indexed.

**File** `backend_app/backend/exchange_executor.py` (`ExchangeNormalizer`)

**Fix**
Ported the resolution rules the paper path already had (`backend/paper/paper_simulator._decimal_places`), including its **refusal to guess** at the ambiguous value `1` — which means one decimal place under one convention and none under the other, and the two differ in which orders the venue accepts:

| precision | places |
|---|---|
| `0` | 0 (unambiguous) |
| `0 < v < 1` | the tick's decimal exponent (`0.01` → 2, `1e-08` → 8) |
| `v > 1`, integral | the count itself |
| `1` | **refused** — ambiguous |

`_market_for()` resolves both the raw and the unified spelling. Floats are read via `Decimal(repr(v))` so `0.01` is exactly one hundredth. Rounding stays `ROUND_DOWN` throughout — truncating can only make an order smaller, whereas rounding up can exceed the balance and breach a limit that was checked pre-rounding.

**Regression test** `tests/test_order_normalization_and_venue_idempotency.py` (27 tests)

---

# P1

---

## BUG-027 — When Redis is unavailable, the distributed lock silently becomes a per-process dict

```
ID            BUG-027
Severity      P0
Component     Cache / distributed locking / order idempotency
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`SharedRedisManager._get_active_cache` ended:

```python
from backend_app.core.safety_config import get_vyomquant_mode
if get_vyomquant_mode() != "production":
    return self._mock_client
return None
```

`get_vyomquant_mode()` returns `VYOMQUANT_MODE`, whose documented values are `safe` / `paper` / `live` (`core/deployment_config.py`). **`"production"` is a value of the separate `ENV` variable.** The comparison was between two different vocabularies, could never be false, and the `return None` beneath it was unreachable.

So whenever Redis was unavailable, in *any* environment including production, the manager returned `MockRedisClient` — an in-memory `dict` on a module-global instance, i.e. **per process**.

**Why this is a trading defect and not a caching one.** This manager backs two controls that must be global rather than per-process:

- `master_executor`'s execution lock — `set(f"execution:{user}:{symbol}:{execution_id}", nx=True, ex=30)`
- `IdempotentExchangeSubmission.claim_submission`'s `SET NX` claim (BUG-003's fix)

Every pod wins its own `NX`, so both controls silently become no-ops across pods **while reporting success**. That last clause is the sharp edge: **no exception is raised, so the fail-closed handling added for BUG-003 never fires.** It defends against a store that *errors*; this one *lies successfully*. N pods consuming one signal produce N real orders.

**Steps**
Set `ENV=production`, `VYOMQUANT_MODE=paper`, `DEV_MODE=false` (exactly the ECS task definition), make Redis unreachable, and call `_get_active_cache()`.

**Expected** `None` — no cache, so locks and claims refuse.

**Actual**
```
get_vyomquant_mode()                 = 'paper'
get_vyomquant_mode() != "production" -> True
_get_active_cache() returned         : MockRedisClient
is it the in-memory mock?            : True
backing store                        : dict, per process
```

**Root cause**
A category confusion between the *trading mode* (`safe`/`paper`/`live`) and the *deployment environment* (`development`/`production`). The two have disjoint vocabularies, so the comparison was vacuous.

**File** `backend_app/core/cache/redis_manager.py`

**Fix**
New `_mock_cache_is_acceptable()`, which reads only `DEV_MODE` and the real `ENV`/`ENVIRONMENT` variables — never the trading mode — and admits only `{development, dev, local, test, testing}`, returning `False` when unset. An unavailable cache in any other environment now returns `None` and logs an error.

The downstream behaviour is correct without further change: `set()` returns `False` when there is no client, so `BotRunner` logs "Could not acquire execution lock" and skips the tick, and `claim_submission` refuses. Both are the right answer to "I cannot coordinate with the other workers."

**Regression test** `tests/test_redis_fallback_does_not_fake_a_distributed_lock.py` (15 tests, including that the trading mode has no influence whatever its value, and that two `MockRedisClient` instances do not share state — which is *why* it cannot serve as a lock)

**Note on discovery.** This was found while auditing production configuration, not while auditing the cache. It is the most important finding after BUG-001, because it defeats a fix made earlier in the same audit — a reminder that a fail-closed exception handler is worthless if the failure mode is a confident wrong answer rather than an error.

---

## BUG-006 — No venue-side idempotency: a lost response becomes a second real order

```
ID            BUG-006
Severity      P1  (money / duplicate execution)
Component     Exchange execution
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`place_order` built its CCXT parameters with only `symbol`, `type`, `side`, `amount` and optionally `price`/`stopPrice`. **No `clientOrderId` was ever sent.** The venue therefore could not recognise a repeat, so a CCXT-internal retry, or any retry after a lost response, became a genuine second order.

`ExecutionEngine.execute_trade` accepts a `metadata` argument (callers pass `{"client_order_id": ...}` into it) and **drops it entirely** — it is never forwarded to `execute_with_idempotency`. So the platform computed an idempotency key, carried it as far as the wrapper, and threw it away.

The platform's database-level idempotency (`execution_records` PK on `execution_id`) protects against *this system* submitting twice. It cannot protect against the *HTTP layer* delivering one submission twice.

**File** `backend_app/backend/exchange_executor.py`, `backend_app/core/execution_engine.py`

**Fix**
`verify_and_consume_token` now retains the verified `execution_id` on `self._consumed_execution_id` for the duration of that one order, and `place_order` forwards it as `params["clientOrderId"]`. The *token* is still consumed and single-use; only the id survives — it is not a secret, it is deterministic, and it is already written to `execution_records`. The venue now enforces the same uniqueness the database does.

**Regression test** `tests/test_order_normalization_and_venue_idempotency.py::test_the_execution_id_is_forwarded_as_a_client_order_id` + `::test_the_validation_token_is_still_single_use`

---

## BUG-007 — Every stop and take-profit order raised `TypeError` before reaching the venue

```
ID            BUG-007
Severity      P1  (trading — a stop loss that cannot be placed is a risk control that does not exist)
Component     Exchange execution
Evidence      READ, fixed with a passing test
Status        FIXED
```

**Description**
`place_order` called `self._exchange.create_order(**params)` with a `stopPrice` key in `params`. CCXT's signature is `create_order(symbol, type, side, amount, price=None, params={})` — there is no `stopPrice` parameter. Any order carrying a stop price raised `TypeError` before a request was made. Trigger prices belong **inside** the `params` dict, which is where every venue's unified implementation reads them from.

Compounding it, `map_order_type` returns `"stop_market"`, `"stop_limit"`, `"take_profit_market"`, `"take_profit_limit"` — none of which are CCXT unified order types.

This matters beyond the feature: a stop loss is a risk control. One that cannot be submitted is not a degraded feature, it is an absent safety net.

**File** `backend_app/backend/exchange_executor.py`

**Fix**
`create_order` is now called **positionally** with `params` last, and trigger prices go into `params`.

**Regression test** `tests/test_order_normalization_and_venue_idempotency.py::test_a_stop_order_puts_its_trigger_in_params_and_does_not_raise`, `::test_create_order_is_called_positionally_with_params_last`

---

## BUG-008 — Venue minimum quantity and minimum notional were never enforced

```
ID            BUG-008
Severity      P1  (trading — silent loss of strategy intent)
Component     Exchange execution
Evidence      READ, fixed with a passing test
Status        FIXED
```

**Description**
`limits.amount.min` and `limits.cost.min` were faithfully read from CCXT and carried as far as `asset_universe`, then compared against nothing. There was no enforcement site anywhere in `backend_app/`.

An order below a venue minimum is rejected by the venue — but by then the idempotency claim has been taken and the execution record says an attempt was made. The strategy's intent is consumed and the position never opens, silently.

Truncation makes this reachable rather than theoretical: `normalize_size` rounds **down**, so a size just above the minimum can land just below it.

**File** `backend_app/backend/exchange_executor.py`

**Fix**
Three checks before any venue call, each naming the figure and the shortfall rather than surfacing the venue's opaque rejection:

- `BELOW_PRECISION` — truncated to a non-tradable quantity
- `BELOW_MIN_QUANTITY` — below `limits.amount.min`
- `BELOW_MIN_NOTIONAL` — below `limits.cost.min`

Also added amount-step snapping: truncating to N decimals is not the same as snapping to a step. With a step of `0.003`, `0.004` truncates to `0.004` and is then rejected.

A venue that publishes no minimum has `None` returned, not a substituted zero.

**Regression test** `tests/test_order_normalization_and_venue_idempotency.py` (5 minimum-enforcement tests)

---

## BUG-009 — The daily-loss kill switch could not fire

```
ID            BUG-009
Severity      P1  (risk control)
Component     Risk engine
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`RiskEngine.__init__` converts every monetary field to `Decimal`. But `update_equity` declared its parameter as `float` and did `self.current_equity += pnl`. `Decimal + float` raises `TypeError` in Python, so the only calls that ever worked were the ones that happened to pass an `int`.

```
RiskEngine(initial_capital=10000).update_equity(-500.0)
-> TypeError: unsupported operand type(s) for +=: 'decimal.Decimal' and 'float'
```

And `reset_daily_loss` assigned `0.0` — a float — into the Decimal accumulator, so even a working caller broke on the first loss after a day boundary:

```
after reset_daily_loss(): type(daily_loss) = float
then update_equity(Decimal('-500'))
-> TypeError: unsupported operand type(s) for +=: 'float' and 'decimal.Decimal'
```

`daily_loss` is the value `check_daily_loss` compares against `daily_loss_limit`, and `can_trade` gates on that comparison. **An accumulator that raises instead of accumulating is a kill switch that never fires**: the exception propagates out of whatever was recording the loss, the loss is never added, and the limit is never reached however far the account falls.

**File** `backend_app/core/risk_engine.py`

**Fix**
`update_equity` normalises any numeric form to `Decimal` before touching an accumulator. `reset_daily_loss` assigns `Decimal("0")`. Normalising at the boundary rather than demanding `Decimal` from every caller is the narrower change — the accumulators stay exact and no call site has to be found and edited to avoid a crash.

**Regression test** `tests/test_risk_engine_accumulators_are_decimal.py` (20 tests, including the full accumulate → reset → accumulate sequence and the kill switch actually blocking)

---

## BUG-010 — Risk-based position sizing was unbounded by the account

```
ID            BUG-010
Severity      P1  (risk control)
Component     Risk engine
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`calculate_position_size` returns `risk_amount / stop_distance`. The formula is dimensionally correct, but its magnitude is governed by the asset's volatility, not by the account — and the function shipped that number with no reference to the balance it would be spent from.

Worked example, reproduced:

> equity 10,000 with `risk_per_trade` 1% gives a 100 risk budget. BTC at 50,000 with ATR 2 and multiplier 2 gives a stop distance of 4, hence **25 BTC = 1,250,000 notional = 125× the account**.

The lower the volatility, the larger the breach. A low-ATR regime is exactly when a strategy is most likely to size up.

**File** `backend_app/core/risk_engine.py`

**Fix**
A notional ceiling as a fraction of current equity (`max_position_notional_pct`, default `1.0` — a position may be at most the whole account). Expressed as a fraction so it scales; binds the notional because that is what has to be funded. The default cannot make an otherwise-fundable size smaller; it only removes the unbounded tail. `None` disables it for a caller with its own downstream bound.

**Regression test** `tests/test_risk_engine_accumulators_are_decimal.py` (5 sizing tests, including that a normal size is **not** reduced)

---

## BUG-011 — Sharpe ratio was annualized by the equities trading-day count on every timeframe

```
ID            BUG-011
Severity      P1  (financial reporting — the figure the marketplace ranks strategies by)
Component     Backtesting engine
Evidence      REPRODUCED
Status        FIXED
```

**Description**
```python
sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)
```

252 is the number of trading days in an equities year. The annualization factor for a return series is `sqrt(observations per year)`, so that constant is correct only if a bar is one trading day. The engine accepts **fifteen** timeframes and defaults to `5m`.

| timeframe | correct factor | used | understated by |
|---|---|---|---|
| 1h | √8760 ≈ 93.6 | √252 ≈ 15.9 | ~5.9× |
| 5m | √105120 ≈ 324 | √252 ≈ 15.9 | ~20× |

And crypto trades continuously — 365×24, no weekend gap, no exchange close. So **daily bars are 365, not 252**; the equities convention never applied to this asset class at all.

This is not cosmetic. The figure is comparable: the marketplace ranks and displays strategies on it, so intraday strategies were systematically mis-ranked against daily ones, in the direction that makes a good strategy look mediocre.

**File** `backend_app/backend/backtesting_engine.py`

**Fix**
A `BARS_PER_YEAR` table and `annualization_factor(timeframe)`, which **raises** on an unrecognised timeframe rather than falling back — a silent default is how one hardcoded constant went unnoticed for so long, because a wrong factor produces a plausible-looking number that nothing downstream can detect.

**Regression test** `tests/test_sharpe_annualization_matches_timeframe.py` (14 tests, including a monotonicity check that catches a transposed table and an AST check that the literal `252` is gone from the computation)

**Discovered by this fix:** `tests/test_backtest_key_contract.py` asserted the engine's Sharpe equals the persisted row's, comparing **two different runs** whose fixtures deliberately use different timeframes (`1m` vs the package's `15m`). The hardcoded 252 made Sharpe accidentally timeframe-independent, so that cross-run equality held by coincidence. Arithmetic confirms nothing else changed: `sqrt(525600)/sqrt(35040) = 3.873`, and the observed divergence was `96.5654/24.9331 = 3.873` exactly. `sharpe_ratio` was moved out of the cross-run tuple — joining `calmar_ratio` and `sortino_ratio`, which that file already excluded for this identical reason — and replaced with a **same-run** preservation test, which is the stricter reading of the requirement.

---

## BUG-012 — `JWT_SECRET` below the HS256 minimum length is only a warning

```
ID            BUG-012
Severity      P1  (authentication)
Component     Configuration
Evidence      REPRODUCED (24 characters resolved in this environment)
Status        OPEN
```

**Description**
`config.py` checks `if len(self.JWT_SECRET) < 32` and emits `logger.warning`. The process starts. PyJWT itself raises `InsecureKeyLengthWarning: The HMAC key is 24 bytes long, which is below the minimum recommended length of 32 bytes for SHA256` (RFC 7518 §3.2).

With BUG-002 fixed, this secret no longer signs production authentication tokens, which is what reduces it from P0 to P1. It remains in use for other HMAC purposes and a short secret is materially easier to brute force.

**File** `backend_app/core/config.py`

**Recommended fix** Refuse to start when `ENV=production` and the secret is under 32 bytes. This is a configuration change plus a one-line severity change, but it can prevent a deployment from booting, so it needs an owner decision rather than an audit edit.

---

## BUG-013 — The legacy deploy endpoint cannot deploy any strategy built in the builder

```
ID            BUG-013
Severity      P1  (dead API surface; 4 failing tests assert it works)
Component     Strategy deployment
Evidence      REPRODUCED (4 test failures)
Status        OPEN — assessed, not fixed
```

**Description**
The canonical save path stores `StrategyGraph.to_dict()` — `schema_version, strategy_id, version, name, nodes, edges, metadata, validation_state`. `StrategyGraph` deliberately has no `exchange` field. `POST /api/strategies/{id}/deploy` hands that blueprint to `fleet.start_bot`, which needs `exchange_id`, `buy_logic`, `sell_logic` and `risk`. So the route refuses:

```
DeployBlueprintIncomplete: The stored blueprint for strategy ... cannot start a bot:
it is missing ['exchange_id', 'buy_logic', 'sell_logic', 'risk'].
```

**The guard itself is correct and well-built** — `assert_blueprint_startable` refuses *before* inserting a `strategy_deployments` row, and explicitly declines to substitute a default venue ("a deployment whose venue nobody chose").

**Severity is narrower than it first appears.** The frontend has already migrated: `pages/Strategies.jsx` and `components/deploy/DeployConfirmation.jsx` both call `deployVersion` → `POST /api/strategies/{id}/versions/{version}/deploy`, the gated route that records a full Deployment_Binding. `api/modules/strategies.js` documents the legacy path as superseded, "which binds nothing and does not travel that gate". **The user-facing deploy button works.**

What remains is a mounted, documented, reachable API endpoint that cannot succeed for the primary strategy type, and four tests that assert it can.

**Files** `backend_app/backend/strategy_service.py`, `backend_app/routers/strategies.py`, `tests/test_strategy_status_publish.py`

**Recommended fix** Either remove the legacy route and re-point its four tests at the versioned route, or make it return a `409` naming the versioned route. Not done here because deleting a public endpoint is a product decision, and the tests encode an expectation whose owner should confirm which route they meant.

---

## BUG-014 — `ExecutionGuard` is not wired into the order-placement path

```
ID            BUG-014
Severity      P1  (risk enforcement architecture)
Component     Execution gateway / risk
Evidence      REPRODUCED (AST search of the gateway found no risk call)
Status        PARTIALLY MITIGATED — see BUG-015
```

**Description**
`backend/execution_guard.py` describes itself as *"the FINAL CONTROL LAYER that validates EVERY trade before execution"* and implements roughly ten validations including duplicate-order detection, order-size limits, circuit breakers and risk-engine status.

It is called from exactly two places: `routers/portfolio.py` (close positions) and `routers/orders.py` (cancel, cancel-all). **It is never called from the path that places an order.**

Searching `execute_with_idempotency` and `_execute_trade_internal` for `ExecutionGuard`, `RiskEngine`, `validate_trade` or `validate_trade_request` returns nothing. The gateway's checks are: execution environment, caller source, global kill switch, strategy existence, idempotency, and the claim.

Risk is enforced one level up, in `BotRunner._inner_loop`, which calls `risk_manager.validate_trade_request` and `continue`s on a non-approved verdict. That is the only production caller, **so there is no live bypass today.** But it makes "this order passed risk" a property of one caller rather than of the gateway — and `execute_trade` hardcodes `source="bot_runner"`, so any new caller inherits the authorisation without inheriting the check.

**File** `backend_app/core/execution_engine.py`, `backend_app/backend/execution_guard.py`

**Status** Mitigated by BUG-015's notional ceiling and by sealing the one unrisked path. Full integration of the risk engine into the gateway is a design change, not a patch, and is recorded in `POST_LAUNCH_IMPROVEMENTS.md` as the highest-priority architectural item.

---

## BUG-015 — A latent path could place an unrisk-checked live order with bot-runner authorisation

```
ID            BUG-015
Severity      P1  (latent risk bypass)
Component     Distributed execution
Evidence      REPRODUCED
Status        FIXED
```

**Description**
`submit_order_idempotent` builds an `ExecutionEngine` declared `LIVE` and calls `execute_trade`, which hardcodes `source="bot_runner"` — the gateway's authorisation check. It therefore inherits the bot runner's permission to place live orders **without** inheriting the risk evaluation that accompanies it. There is no risk call anywhere in the function.

A repository-wide AST search confirmed **nothing in `backend_app/` calls it**, so the bypass was latent rather than live.

**Fix (two parts)**

1. **The path is sealed.** It returns `error="RISK_EVALUATION_REQUIRED"` with "Nothing was sent to the exchange." A path that can place an unrisk-checked live order and has no users is pure downside; wiring it up correctly means routing it through the same risk evaluation the bot runner performs, which is a design change and the wrong thing to attempt under launch pressure for something nobody calls. The idempotency machinery above it is left intact and independently tested — that is the reusable part.

2. **A notional ceiling now sits in the gateway itself.** On the LIVE branch, an order's notional must not exceed `equity × max_leverage` (default 1), and it **fails closed** if equity cannot be read. This is deliberately the weakest useful bound: it cannot false-refuse a correctly sized order, which is what makes it safe to add to the live path. It is not a risk engine and does not claim to be — it will not catch an order that merely breaches risk-per-trade or the daily loss limit. What it does catch is the category that loses an account in a single order: a sizing bug (see BUG-010 — 125× equity), a decimal shift, a replayed signal, or a base/quote unit mix-up.

**Regression test** `tests/test_live_orders_are_bounded_by_account_equity.py` (14 tests) + a new AST assertion added to the existing `tests/test_execution_paths_that_claim_an_exchange.py` that the refusal is present — a stronger property than "has no callers", which the next commit could change.

---

## BUG-016 — Opening a position never debits the notional

```
ID            BUG-016
Severity      P1  (financial correctness)
Component     Execution engine (paper / backtest accounting)
Evidence      REPRODUCED
Status        OPEN
```

**Description**
`ExecutionEngine.open_position` checks `if total_cost > self.current_equity: return False, "Insufficient capital"` — and then debits only the **fee**:

```
open_position(BTCUSDT, price=50000, size=0.1) -> ok=True
notional committed            -> 5000.0
equity before                 -> 100000.0
equity after                  -> 99995.000000000000
equity actually moved         -> 5.000000000000   (the fee only)
```

Three consequences:

1. `cash + positions != equity`. The identity does not hold in this engine.
2. `get_drawdown()` measures drawdown on realized-only equity, so **an open position down 40% shows zero drawdown** and the max-drawdown kill switch cannot fire on open risk.
3. The "Insufficient capital" check passes again for the next symbol, so N positions can each be worth up to the full equity. The only aggregate brake is `max_open_trades = 5`, which caps exposure at ~500% of equity by accident rather than by design.

**File** `backend_app/core/execution_engine.py`

**Why not fixed here** This changes the equity trajectory of every paper run and every backtest, which is the headline number on the Backtester screen and in the marketplace listing evidence. Correcting it is right, but it will move published performance figures and needs to land with its own verification pass, not inside a launch audit. `backend/paper/paper_accounting.py` already implements the correct model (cash as the residual of the position-value change, exact zero-tolerance equity identity) and is the reference to converge on.

**Note** The live path is unaffected: it does not use this accounting, and BUG-015's ceiling now bounds live notional independently.

---

## BUG-017 — A fee paid in a third asset is counted at 1:1 against the quote currency

```
ID            BUG-017
Severity      P1  (financial correctness)
Component     Fee engine
Evidence      REPRODUCED
Status        FIXED
```

**Description**
```python
if fee_asset.upper() == base_asset.upper():
    fee_cost_in_quote = fee_amount * price
else:
    # Fee paid in quote asset (e.g. USDT) or third asset
    fee_cost_in_quote = fee_amount
```

The comment names the bug. A **BNB fee on a BTC/USDT trade** is a third asset, hits the `else`, and `0.0005 BNB` is recorded as `$0.0005`. BNB-paid fees are the Binance default (incentivised with a 25% discount), and they are understated by roughly three orders of magnitude. There is no price lookup for third assets.

Related in the same file:
- `_parse_symbol` silently falls back to `return sym_clean, "USDT"` for anything it cannot parse.
- `calculate_net_pnl` does `sum(fees)` on raw amounts across assets with no conversion; `get_fee_summary` adds BNB, USDT and BTC fee amounts into one scalar and divides by a USD-ish volume. Both contradict the quote-conversion `record_fee` performs.

**File** `backend_app/backend/fee_engine.py`

**Fix**
The two-branch decision became three, because the original `else` conflated two cases that are not alike:

| fee asset | treatment |
|---|---|
| base asset (BTC fee on BTC/USDT) | converted at the trade's own price |
| **quote asset** (USDT fee on BTC/USDT) | 1:1 — correct, and needs no new data |
| **third asset** (BNB fee on BTC/USDT) | **not converted**; `fee_pct` and `net_pnl` recorded as absent |

`record_fee` has no market-data handle, so no rate is available for a third asset. Rather than invent one, the fee's true amount and true denomination are recorded and the derived figures are reported as **absent** — which follows this codebase's own convention that an unmeasured figure is not replaced with a plausible one. A net PnL of "not computed" is recoverable; one that is wrong by 1000× is not.

A caller that needs third-asset netting converts before calling. The success log renders `pct=not converted` rather than raising on `None * 100`.

**Also fixed in the same function:** `str(x) if x else None` persisted a `Decimal('0')` as NULL, because zero is falsy — making a break-even trade indistinguishable from one whose PnL was never computed (this was filed separately as BUG-022, now also FIXED).

**Still open in the same file:** `calculate_net_pnl` and `get_fee_summary` still sum raw fee amounts across assets with no conversion, which contradicts what `record_fee` now does. Recorded in `POST_LAUNCH_IMPROVEMENTS.md`.

**Regression test** `tests/test_fee_denomination_is_not_assumed.py` (10 tests)

---

# P2

---

## BUG-018 — Row Level Security policies are keyed on a setting nothing sets

```
ID            BUG-018
Severity      P2  (defence in depth; not the live isolation control)
Component     Database
Evidence      READ (strong negative grep; not DB-verified)
Status        OPEN
```

**Description**
`alembic/versions/implement_rls_policies_20260817.py` enables RLS and creates policies on nine tables, all of the form:

```sql
USING (tenant_id = current_setting('app.current_tenant_id', true))
```

**No code in `backend_app/` sets `app.current_tenant_id`.** Grepping for `set_config(` / `current_setting(` returns matches only inside migrations. With the GUC unset the predicate is never true, so for any role that does not bypass RLS these policies deny everything, and for the service-role connection they are bypassed. Either way they provide no isolation today.

Every statement in that migration is wrapped in `try/except` + `print`, so a failed `ALTER` or `CREATE POLICY` does not fail the migration — partial application is silent.

The isolation that **does** function is Supabase-native and `auth.uid()`-based (`backend_app/migrations/020_declare_pre_existing_tables.sql`), and it applies only where the request-scoped (anon-key + user JWT) client is used.

**Material caveat:** `routers/library.py` uses the **service-role** client, which bypasses RLS entirely. For that router the Python-level ownership checks are the *only* isolation control. Those checks were reviewed and found present at the sites examined — including `PATCH /{library_id}/settings`, which I initially suspected and then cleared (it does compare `author_id` and answers an identical 404).

**Recommended fix** Either set the GUC per request (a `tenant_db_context` module appears to exist in the working tree for this purpose) or drop the inert policies so the security model is not misread as having a second layer it does not have.

---

## BUG-019 — MFA enforcement exists and is applied to zero endpoints

```
ID            BUG-019
Severity      P2
Component     Authentication
Evidence      READ
Status        OPEN
```

**Description**
`require_aal2` is implemented, correct, and documented as intended for exchange API key mutation, admin endpoints, password change and billing mutations. It is applied to **no endpoint**. The `aal` claim propagation was fixed previously (and that fix was necessary — the dependency would have rejected everyone), so the gate is now usable; it is simply unused.

Consequence: a stolen session token is sufficient to add or replace exchange API credentials and to change billing, with no second factor.

**File** `backend_app/core/dependencies.py`

---

## BUG-020 — Prometheus metrics endpoint is unauthenticated

```
ID            BUG-020
Severity      P2
Component     Observability / information disclosure
Evidence      READ
Status        OPEN
```

`GET /metrics` carries no auth dependency. Prometheus exposition typically includes per-tenant counters, queue depths and error rates. Restrict at the network or ingress layer, or add an auth dependency.

---

## BUG-021 — The order-placing exchange client sets no CCXT options

```
ID            BUG-021
Severity      P2  (order rejections under clock drift)
Component     Exchange execution
Evidence      READ
Status        OPEN
```

**Description**
`CCXTExchangeExecutor.connect()` sets `config["options"] = {"sandbox": True}` on the sandbox branch and sets **no options at all** otherwise. `connection_engine.py` — used for credential verification — sets `{"defaultType": "spot", "adjustForTimeDifference": True}`.

So the client that *verifies* credentials adjusts for clock skew and the client that *places orders* does not. Binance rejects orders whose timestamp falls outside `recvWindow`, and `adjustForTimeDifference` is the standard mitigation.

Separately, the sandbox branch **replaces** the options dict wholesale rather than merging, so whichever of these two concerns is added second will silently erase the first.

**File** `backend_app/backend/exchange_executor.py`

---

## BUG-022 — A PnL of exactly zero is persisted as NULL

```
ID            BUG-022
Severity      P2  (data integrity)
Component     Fee engine
Evidence      REPRODUCED
Status        FIXED
```

```python
gross_pnl=str(gross_pnl) if gross_pnl else None,
net_pnl=str(net_pnl) if net_pnl else None,
```

`Decimal('0')` is falsy. A break-even trade was stored as NULL — indistinguishable from "never computed". A fee of exactly zero (a full rebate) and a PnL of exactly zero are both real measurements.

**File** `backend_app/backend/fee_engine.py`

**Fix** `is not None` on all three fields. Fixed alongside BUG-017 since it is in the same function.

**Regression test** `tests/test_fee_denomination_is_not_assumed.py::test_a_gross_pnl_of_exactly_zero_is_not_stored_as_null`, `::test_a_net_pnl_of_exactly_zero_is_not_stored_as_null`, `::test_a_zero_fee_still_records_a_zero_percentage`

---

## BUG-023 — A zero average entry price produces 100% phantom profit

```
ID            BUG-023
Severity      P2  (financial correctness; reachability unconfirmed)
Component     Position model
Evidence      READ
Status        OPEN
```

`PositionCalculator.calculate_new_avg_entry` returns `Decimal('0')` when `total_size == 0`. A position whose avg entry persists as `0` makes every later PnL `exit_price × size` — 100% phantom profit. `position_engine` also accepts a fill with no price (`Decimal(fill_price or avg_price or "0")`) — it guards the quantity but not the price.

`paper_accounting._weighted_average_entry` raises `InvalidQuantity` in the same situation. The hardened stack got this right; the legacy one did not.

**Recommended fix** Raise rather than return zero, and reject a non-positive fill price.

---

## BUG-024 — The process-global Decimal context is contended

```
ID            BUG-024
Severity      P2
Component     Financial precision
Evidence      READ
Status        OPEN
```

`core/decimal_utils.py` calls `setcontext(Context(prec=18, ...))` at import. `core/risk_engine.py` sets `getcontext().prec = 28`. Whichever imports last wins for every computation that does not use `localcontext()`. Precision and default rounding therefore depend on module import order.

`paper_accounting` is immune by design — every function uses `with localcontext()`. That is the pattern to adopt elsewhere.

---

# P3

---

## BUG-025 — `place_order_with_idempotency` documents itself as the only allowed method and cannot succeed

```
ID            BUG-025
Severity      P3  (dead and actively misleading)
Component     Exchange execution
Evidence      READ
Status        OPEN
```

Its docstring says *"This is the ONLY allowed method for order placement."* It routes through the Redis idempotency layer but never through `ExecutionEngine`, so no validation token is set and `verify_and_consume_token` raises `"Bypass attempt detected"`. Two blocks in the same file contradict each other, and the one that is wrong is the one a new engineer would follow. No callers. Delete it or fix its docstring.

---

## BUG-026 — A protected-logic containment check matches a three-character substring

```
ID            BUG-026
Severity      P3  (very likely a false positive in the test, not a leak)
Component     Marketplace projection
Evidence      REPRODUCED (test failure)
Status        OPEN
```

```
AssertionError: Protected_Logic token 'vie' appears in the response at $.details.operation
```

`'vie'` is three characters and matches inside ordinary words such as "view". The containment scan appears to use unbounded substring matching. Needs a minimum token length or word-boundary matching. Flagged rather than fixed because the scan is a security control and loosening it deserves its own review.

---

# RETRACTED

---

## BUG-R01 — "CCXT ignores `config['sandbox']`, so testnet orders hit production"

```
ID            BUG-R01
Severity      claimed P0
Status        RETRACTED — the claim was wrong
```

**What I claimed** `CCXTExchangeExecutor.connect()` sets `config["sandbox"] = True` and `config["options"] = {"sandbox": True}`; CCXT honours neither, and the real API is `set_sandbox_mode(True)`. A user selecting testnet would therefore have real orders placed with real funds.

**What is actually true** Verified by execution against the pinned `ccxt 4.3.92`. `Exchange.__init__` contains:

```python
is_sandbox = self.safe_bool_2(self.options, 'sandbox', 'testnet', False)
if is_sandbox:
    self.set_sandbox_mode(is_sandbox)
```

It reads from `self.options`, so `config["options"] = {"sandbox": True}` **is** what arms sandbox mode. Both paths resolve identically:

```
production (no sandbox)                 -> https://api.binance.com/api/v3
config{'sandbox':True} (current code)   -> https://testnet.binance.vision/api/v3
set_sandbox_mode(True) (correct API)    -> https://testnet.binance.vision/api/v3
```

**Sandbox mode works.** My reading of the code was correct; my inference about CCXT's behaviour was not, and only running it settled the matter.

**What survived the retraction** The wholesale replacement of the `options` dict is still a real fragility, recorded separately and at honest severity as **BUG-021 (P2)**.

**Why this entry stays** Four of my own regression tests also failed on first run, three of them because they searched source *text* for a forbidden token while my explanatory comments necessarily contained that token. Reading code is not the same as running it, in both directions — a finding can be invented as easily as it can be missed.

---

# PRODUCTION CONFIGURATION AUDIT

Read from `ecs-task-definition-full.json` and `new_task_def.json`. No secret values are reproduced here — only variable names and non-secret values.

## Verified correct

| Item | Value | Assessment |
|---|---|---|
| `ENV` | `production` | Correct. Closes the BUG-002 gate in this deployment |
| `CORS_ORIGINS` | `https://app.vyomquant.com,https://vyomquant.com` | Real origins, no wildcard |
| CORS credentials handling | `allow_credentials` auto-disabled if `*` present | Good defensive design (`main.py`) |
| `FEATURE_DEBUG_ENDPOINTS_ENABLED` | `false` | Correct |
| `FEATURE_MOCK_EXCHANGE_ENABLED` | `false` | Correct |
| `RATE_LIMIT_ENABLED` | `true` | Correct |
| `LOG_LEVEL` | `INFO` | Correct — not `DEBUG` |
| Secrets delivery | `JWT_SECRET`, `MASTER_ENCRYPTION_KEYS`, `DATABASE_URL`, `SUPABASE_*`, `RAZORPAY_*`, `CREDENTIAL_VAULT_SALT` via ECS `secrets` | Correct — Secrets Manager, not plaintext env |
| `.env` in git | not tracked, 0 commits ever | Correct |
| `.gitignore` | covers `.env`, `.env.*`, `.env.corrupt*`, `.env.backup*` | Thorough |
| API docs in production | `_get_docs_url()` disables when `ENV=production` unless `ENABLE_DOCS` set | Correct |

## FINDING — production is configured for paper trading, not live

```
VYOMQUANT_MODE = paper      (both task definitions)
```

This is not a defect; it is a configuration fact with a large consequence for the launch decision.

`safety_config.py` gates on it:

```python
if env_mode == "paper":
    ...
    if operation == "live_order":
        return "[BLOCKED] EXECUTION BLOCKED: Live execution is not allowed in paper mode"
```

and `main.py` only arms live trading for `env_mode == "live"`. `ExecutionFlags.enable_live_trading()` additionally requires `VYOMQUANT_ENABLE_LIVE_TRADING=true`, which is **not set in either task definition**.

**So as currently deployed, live trading is off.** Note `FEATURE_TRADING_ENABLED=true` does *not* override this — it is a different flag checked at a different layer, and the pairing reads as though live trading were enabled when it is not.

Two readings, and the owner must say which applies:

1. **Deliberate** — launch paper-only first. Then "live trading" must not appear in launch marketing, and the gap should be stated plainly to users.
2. **Not yet flipped** — someone sets `VYOMQUANT_MODE=live` and `VYOMQUANT_ENABLE_LIVE_TRADING=true` on launch day.

**Reading 2 is the dangerous one.** That flip would be the first time the live order path ever executed — in production, with real customer funds — on a path that until this audit contained a cross-tenant credential leak (BUG-001), stop orders that raised `TypeError` before reaching the venue (BUG-007), order normalization that crashed on Binance-style precision (BUG-005), no venue-side idempotency (BUG-006), and a distributed lock that silently degraded to a per-process dict (BUG-027).

**Recommendation:** whichever reading applies, do not make the live-mode flip the first exercise of that path. Exercise it on a testnet first (see the launch blockers in the test report).

## Also noted

| Item | Value | Assessment |
|---|---|---|
| `PRODUCTION_ROUTER_ENABLED` | `False` | Default in `feature_flags.py` is `True`; explicitly disabled here. Confirm this is intended |
| `_DEPRECATED_AERORA_MODE_REMOVED` | `true` | Housekeeping marker; the deprecated `AERORA_MODE` fallback still exists in `safety_config.py` |
| `CORS_ORIGINS` default | localhost origins | If unset in a new environment the API rejects the real frontend. Fail-closed, so correct direction, but it presents as a total outage rather than a config error |
| `td15.json`, `td22.json` | not UTF-8 (UTF-16 BOM) | Could not be parsed. Unaudited — confirm they are not deployed |

---

## Appendix — verification artifacts

| File | Purpose |
|---|---|
| `audit_p0_proof.py` | Reproduces BUG-001, 003, 005, 006, 009, 010, 014, 016 by execution. 7/10 proven on first run; the two misses led to BUG-R01 and to the corrected BUG-002 proof. |
| `audit_recheck.py` | Re-verifies the two claims that did not reproduce. Settled BUG-R01 and confirmed BUG-002 with the correct signing secret. |
| `audit_redis_fallback_proof.py` | Reproduces BUG-027 under the exact ECS task-definition settings. |

All three are temporary audit scaffolding and should be deleted before merge.

## Appendix — regression suites added

| File | Tests | Guards |
|---|---|---|
| `tests/test_exchange_executor_cache_is_per_tenant.py` | 12 | BUG-001 |
| `tests/test_test_token_fallback_fails_closed.py` | 18 | BUG-002 |
| `tests/test_idempotent_submission_fails_closed.py` | 9 | BUG-003, BUG-004 |
| `tests/test_order_normalization_and_venue_idempotency.py` | 27 | BUG-005, BUG-006, BUG-007, BUG-008 |
| `tests/test_risk_engine_accumulators_are_decimal.py` | 20 | BUG-009, BUG-010 |
| `tests/test_sharpe_annualization_matches_timeframe.py` | 14 | BUG-011 |
| `tests/test_live_orders_are_bounded_by_account_equity.py` | 14 | BUG-014 (mitigation), BUG-015 |
| `tests/test_fee_denomination_is_not_assumed.py` | 10 | BUG-017, BUG-022 |
| `tests/test_redis_fallback_does_not_fake_a_distributed_lock.py` | 15 | BUG-027 |
| **Total** | **139** | |

## Appendix — existing tests corrected

Four existing tests were edited. Each was a **scope or location** correction, never a weakened assertion, and each is listed here so the change can be audited.

| File | Change | Why it is not a weakening |
|---|---|---|
| `tests/test_execution_paths_that_claim_an_exchange.py` | `_call_sites()` gained `production_only=True`; the reachability test uses it | My new tests deliberately *call* `submit_order_idempotent` to prove it refuses. Counting a test that demonstrates safety as evidence of reachability inverts the measurement. **Added** `test_the_idempotent_submission_path_refuses_live_orders_outright`, asserting the refusal — stronger than "has no callers" |
| `tests/test_backtest_key_contract.py` | `sharpe_ratio` moved out of the cross-run `PRESERVED_FROM_ENGINE` tuple | That tuple compares two runs whose fixtures use different timeframes (`1m` vs `15m`); the hardcoded √252 made Sharpe accidentally timeframe-independent so the equality held by coincidence. `calmar_ratio` and `sortino_ratio` were already excluded for this identical reason. **Replaced** with a same-run payload-vs-row test, which is stricter |
| `tests/test_no_hs256_fallback_in_production.py` | Four shape assertions follow the gate into `_test_token_fallback_allowed`; ordering assertion now excludes the docstring | The gate moved, not away. The allow-list check is now **stricter** (the list no longer contains `development`/`dev`/`local`). **Added** `TestTheEnvironmentGateFailsClosed` (5 tests), asserting the property this file previously had to assume — that an unset `ENV` does not open the path |
| `tests/test_paper_live_separation.py` | Derives the cache key via `_executor_cache_key` instead of spelling `"kraken:False"`, and passes `user_id` | The old literal encoded the defective key format. Deriving it keeps the "nothing was cached" assertion valid against whatever the current derivation is |

---
---

# ADDENDUM — defects found by RUNNING the system (remediation pass, 6 Oct 2026)

The audit above found defects by reading. This addendum records what was found by standing
up real PostgreSQL 16.2 and real Redis 5.0.14.1 and executing against them. Different
category, larger count: most of these were unreachable by inspection because they live in
a provisioning process that had never been executed end to end.

Full assessment and verdict: `FINAL_LAUNCH_READINESS_REPORT.md`.

Numbering continues from BUG-027.

---

## BUG-028 [P0] — `alembic upgrade head` could not complete, so the tenant-isolation RLS policies were never installed

**Where** `backend_app/alembic/versions/add_foreign_keys_20260817.py`
**Found by** `staging/try_alembic_head.py`, one revision at a time
**Status** FIXED

All seven `ALTER TABLE ... ADD CONSTRAINT` statements ran in one transaction, each wrapped
in `try/except Exception` with a printed warning. The intent is plainly best-effort. But
PostgreSQL marks the whole transaction aborted on a failed statement, so the `except`
caught the error, printed, and the revision then died on alembic's own version bump:

```
Warning: Could not add FK for execution_records.task_id: (DatatypeMismatch)
  Key columns "task_id" and "task_id" are of incompatible types: uuid and character varying.
...
sqlalchemy.exc.InternalError: (psycopg2.errors.InFailedSqlTransaction)
[SQL: UPDATE alembic_version SET version_num='add_foreign_keys' ...]
```

**Why it is P0 and not a migration nit.** `alembic history` puts
`implement_rls_policies` immediately downstream, on the only path to `head`. While this
revision could not commit, the nine tenant-isolation policies it declares were installed on
**no database built by migration** — `orders`, `positions`, `fills`, `dag_tasks`,
`execution_records`, `idempotency_keys`, `transaction_records`,
`transaction_checkpoints`, `reconciliation_mismatches`. The execution and financial tables
had no row filtering at all. Measured before the fix: 0 policies keyed on
`app.current_tenant_id`, all nine tables reporting `RLS DISABLED`.

Note this **corrects BUG-018 above**, which recorded those policies as present but inert.
They were absent. Inert is the state they reach *after* this fix.

**Fix** Each constraint gets its own SAVEPOINT via `connection.begin_nested()` — which is
what the `try/except` was reaching for — plus a `pg_constraint` guard for idempotency. The
revision now commits; 2 of 7 constraints land, 5 warn and are skipped (see BUG-033).

**Precondition before running against production** `implement_rls_policies` now actually
executes, and it enables RLS on nine financial tables. It issues `ENABLE`, not `FORCE`, and
a table's owner is exempt from its own policies unless FORCE is set. Every DSN in the
repository carries username `postgres` and alembic runs under the same `DATABASE_URL`, so
the creating role is the connecting role and the policies would be inert. But the
production DSN is a Secrets Manager reference and is not in the repository, so that cannot
be confirmed from code. **Confirm the production role owns those nine tables, or has
BYPASSRLS, before `upgrade head`.** If it does not, every query against them silently
returns zero rows.

---

## BUG-029 [P0] — pgcrypto is required and never declared; every user signup fails on a database built from this repository

**Where** `migrations/referral_system_redesign.sql` (the dependency),
`backend_app/migrations/009_paper_trading.sql` (the preflight that missed it)
**Found by** `INSERT INTO public.profiles` during RLS test setup
**Status** FIXED

`generate_unique_referral_code()` calls `gen_random_bytes(4)`, which belongs to pgcrypto.
**No file in the repository issued `CREATE EXTENSION`** — the only occurrence of the phrase
was inside an error-message *string* in `009`, telling an operator to run it without
running it. `trigger_create_referral_code` on `public.profiles` calls that function, so:

```
UndefinedFunction: function gen_random_bytes(integer) does not exist
CONTEXT: PL/pgSQL function generate_unique_referral_code() line 10 at assignment
         SQL statement "SELECT create_referral_code_for_user(NEW.id)"
         PL/pgSQL function trigger_create_referral_code() line 4 at PERFORM
[SQL: INSERT INTO public.profiles (id) VALUES (...)]
```

A `profiles` row is created for every user, so signup is broken outright. Production is
unaffected today because Supabase enables pgcrypto by default — which is precisely why the
dependency stayed invisible, and precisely why a disaster-recovery rebuild onto plain
PostgreSQL would not have produced a working system.

**Fix** Declared in three places for three reasons: the substrate in
`scripts/provision_database.py` (the platform layer, with a documented
non-cryptographic fallback for servers shipping no pgcrypto package),
`referral_system_redesign.sql` itself (so the file is self-sufficient, raising a message
that names the consequence rather than a bare `FeatureNotSupported` pointing at a
filesystem path), and `009`'s preflight (so a missing extension stops provisioning early).

---

## BUG-030 [P1] — the pgcrypto preflight checked a function that is in core PostgreSQL

**Where** `backend_app/migrations/009_paper_trading.sql`
**Status** FIXED

The precondition that existed to catch BUG-029 tested `gen_random_uuid()`. That function
has been in **core** PostgreSQL since version 13, so on any modern server the check passes
whether or not pgcrypto is installed. The one function that genuinely requires the
extension went unverified. A guard that cannot fail for its stated reason is worse than no
guard, because it is read as coverage.

**Fix** `gen_random_bytes` is now checked as well, with a message naming what breaks.

---

## BUG-031 [P0] — ambiguous column in the referral-code function; the second independent cause of signup failing

**Where** `migrations/referral_system_redesign.sql`, `generate_unique_referral_code()`
**Status** FIXED

With BUG-029 resolved, the next `profiles` insert failed again:

```
AmbiguousColumn: column reference "code" is ambiguous
```

The function declared a local `code`, and `referral_codes` has a column `code`, so
`WHERE code = code` is ambiguous. Resolving it the other way would not have saved it:
read as column-equals-column it is a tautology, so `NOT EXISTS` is false as soon as one
code exists, all ten attempts fail, and the function ends at its own `RAISE EXCEPTION`.
Two distinct failures from one name collision.

**Fix** Renamed to `v_code`, matching the `v_` convention the file already uses; subqueries
aliased so the comparison cannot be misread again.

---

## BUG-032 [P1] — `6f1b3d9c8a7e` adds a column production already has, and cannot retry

**Where** `backend_app/alembic/versions/6f1b3d9c8a7e_add_referrals.py`
**Status** FIXED

`op.add_column('profiles', 'available_discounts')` fails with `DuplicateColumn` because
`020_declare_pre_existing_tables.sql` — whose stated job is reproducing the tables Supabase
already had — declares it. This would fail against production for the same reason, and
because the transaction aborts, the revision cannot record itself, so every retry repeats
the failure.

**Fix** `sa.inspect` guards on the column, the `referrals` table and its index.
`sa.inspect` rather than `ADD COLUMN IF NOT EXISTS` because the revision branches on
dialect and is expected to run on SQLite too.

---

## BUG-033 [P1] — 5 of 7 foreign keys cannot be created; `tenant_id` is typed two ways across the financial tables

**Where** `backend_app/alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py` (the
declarations), `add_foreign_keys_20260817.py` (where it surfaces)
**Found by** `staging/fk_typecheck.py` — each FK attempted independently under a savepoint
**Status** NOT FIXED. Needs an owner.

| Constraint | Types |
|---|---|
| `fk_execution_records_task_id` | `execution_records.task_id` **uuid** → `dag_tasks.task_id` **varchar(36)** (a primary key) |
| `fk_fills_tenant_id` | `fills.tenant_id` **varchar(36)** → `profiles.id` **uuid** |
| `fk_orders_tenant_id` | `orders.tenant_id` **varchar(36)** → `profiles.id` **uuid** |
| `fk_positions_tenant_id` | `positions.tenant_id` **varchar(36)** → `profiles.id` **uuid** |
| `fk_dag_tasks_tenant_id` | `dag_tasks.tenant_id` **varchar(36)** → `profiles.id` **uuid** |

PostgreSQL has no equality operator between `uuid` and `varchar`, so these cannot be
implemented. **Referential integrity on those `tenant_id` columns does not exist**, and an
orphaned row referencing a deleted profile is not prevented by the database.

**Why it is not fixed here.** The split is schema-wide and the ORM reproduces it
*consistently on each side*: `core/models/execution_tables.py` and
`core/models/dag_task.py` declare `String(36)`; `core/models/execution_record.py`,
`core/models/reconciliation.py` and `backend/fee_engine.py` declare `PG_UUID`.
`profiles.id` is `uuid` because it mirrors Supabase `auth.users.id` and is not ours to
retype. Normalising therefore means retyping columns on four financial tables *and*
changing the ORM declarations that currently match them — a schema decision with an owner,
not a migration repair. The constraints stay declared and keep warning, which is what keeps
the gap visible.

---

## BUG-034 [P0] — four tables holding per-customer financial data have no row filtering

**Where** `invoices`, `payment_methods`, `subscriptions`, `transaction_rollbacks`
**Found by** `staging/exposure_check.py`
**Status** CONFIRMED in the repository. **NOT VERIFIED against production.** Launch blocker
until answered.

| Table | Owner column | RLS | GRANT/REVOKE in any migration |
|---|---|---|---|
| `invoices` | `user_id` | disabled | none |
| `payment_methods` | `user_id` | disabled | none |
| `subscriptions` | `user_id` | disabled | none |
| `transaction_rollbacks` | `tenant_id` | disabled | none |

In Supabase's model `anon` and `authenticated` hold privileges on `public` by default, and
row level security — not table privilege — separates one customer's rows from another's on
the PostgREST path. The repository is consistent with that: migrations 007, 008, 009, 017,
018, 019 and 004d issue careful per-table GRANTs and explicit `REVOKE ALL ... FROM anon`,
while the older tables carry no grant statements because the platform defaults covered
them.

These four have neither. `020` states at line 124 that they are alembic-declared and
deliberately not re-declared there; `implement_rls_policies` covers nine tables and none of
these; `006_reconcile_production_database.sql` enables RLS on **`billing_invoices`**, a
different table from `invoices`.

So on any database built from this repository, a holder of the **anon key** — a public,
browser-shipped credential — can read every customer's invoices, subscriptions and payment
methods.

**Why NOT VERIFIED rather than confirmed exploitable.** RLS may have been enabled on them in
production through the Supabase dashboard rather than by a migration in this tree. That is
plausible and would make the production exposure nil. It is also exactly the kind of thing
that gets assumed. One query settles it:

```sql
SELECT relname, relrowsecurity, relforcerowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND relname IN ('invoices','payment_methods','subscriptions','transaction_rollbacks');
```

**Regression guard** `staging/exposure_check.py` exits non-zero while any table with a
per-customer column lacks both RLS and a REVOKE.

---

## BUG-035 [P0] — the referral payout endpoint cannot succeed for any user

**Where** `backend_app/routers/referral.py`
**Status** NOT FIXED. Needs a schema decision.

The provisioned `referral_wallets` — matching `006`, the file that reconciles against
production — has `balance_usd`, `total_earned_usd`, `pending_usd`. There is no
`approved_balance_usd`. The endpoint reads:

```python
approved_balance = float(wallet.get("approved_balance_usd", 0))
...
if payout_request.amount_usd > approved_balance:
    raise HTTPException(status_code=400, detail="Insufficient approved balance. ...")
```

`select("*")` cannot return a column that does not exist, so the default applies and
`approved_balance` is always `0.0`. With the separate $10 minimum, `amount > 0` is true for
every well-formed request. **Every payout request is answered 400 "Insufficient approved
balance. Available: $0.00", regardless of actual earnings.**

It fails quietly rather than erroring, so it does not appear in logs as an exception — it
looks like a user with no earnings.

**Root cause** `referral_system_redesign.sql` and `006` declare the same five tables with
different column sets; `006` wins because it runs first and the redesign uses
`CREATE TABLE IF NOT EXISTS`. The router was written against the losing shape. The
repository's own `.kiro/specs/production-launch-hardening/tasks.md` records the divergence;
what is new is that a shipped endpoint depends on it.

**Why not fixed** The redesign carries a four-state balance model
(pending/approved/paid/lifetime) that `006`'s three columns cannot express, so this is not
a rename. Choosing the canonical shape with live wallet balances in production is a product
and data decision. **Recommend disabling the endpoint for launch** if the decision cannot
be made first — shipping it broken tells earners they have no balance.

---

## BUG-036 [P2] — a backfill guarded on the wrong precondition

**Where** `migrations/referral_system_redesign.sql`
**Status** FIXED

The `referral_wallets` initialisation was gated on whether `referrals` exists. The real
dependency is the column set it writes. The block had never fired because `referrals` never
existed (BUG-028 kept the revision that creates it unreachable); the moment head became
reachable it fired and failed on `UndefinedColumn: pending_balance_usd`.

**Fix** The guard now tests for the column it writes and skips with a `NOTICE` naming the
competing declaration, rather than converting a schema conflict into a provisioning
failure.

---

## BUG-037 [P1] — no Redis-backed control had ever executed against a real Redis

**Where** `tests/conftest.py` line 13
**Status** Mechanism added; coverage started

`os.environ["REDIS_URL"] = ""` is set unconditionally and deliberately, so the whole suite
runs on the in-memory `MockRedisClient`. That is the right default — 12,000 tests should
not depend on whatever Redis is on a developer's machine — but it means **no Redis-backed
control in this repository had ever run against a real server.**

That matters because `MockRedisClient` is a plain dict on a module-global instance, so it
is per-process and `SET NX` always succeeds. That is BUG-027: the distributed locks behind
`master_executor`'s execution lock and `IdempotentExchangeSubmission.claim_submission`
silently became per-pod no-ops while still reporting success.

**Fix** Added `AERORA_TEST_REDIS_URL` as a second opt-in, following the precedent
`AERORA_TEST_DATABASE_URL` sets. Unset, the new tests skip and the default lane is
untouched. `tests/test_webhook_duplicate_delivery_real_redis.py` is the first Redis-backed
test in this repository to have run against a real server.

---

## BUG-038 [P1] — billing cannot be verified before production, by design

**Where** `backend_app/routers/billing.py`, `_validate_keys`
**Status** NOT FIXABLE without a policy decision

> Live prefixes only, in every environment. There is no test-credential bypass.

Observed: supplying `rzp_test_...` to the webhook handler yields
`500: Razorpay is configured with a TEST key id.` The gate runs before everything else on
the path.

The reasoning is sound for the *entitlement-granting* paths — a test key collects no money,
so honouring one would hand out paid plans for free. Its side effect is that **no billing
path can be exercised in staging or CI at all**, including the webhook, so the revenue path
will execute for the first time against real customer money.

**Also** `tests/test_billing_e2e.py` verified webhook idempotency with
`assert source.count("await redis_manager.delete(idempotency_key)") >= 2`. That establishes
two `delete` calls are written in the file — not that a redelivery is ignored, that the
first delivery is not, that the lock is released when processing raises, or that any of it
works against a real Redis. For payment webhooks, string counting is not a sufficient
standard.

**Closed in part** `tests/test_webhook_duplicate_delivery_real_redis.py` (6 tests) now
exercises the dedup control against the real handler and real Redis, signing the payload
locally with HMAC-SHA256 since that is all the handler verifies. The credential gate is
asserted to still refuse test keys, in the same file, before being stepped around to reach
the control behind it. Settlement, provider interaction and entitlement grant remain
NOT VERIFIED.

---

# Found during the post-remediation closure pass (BUG-039 … BUG-047)

These were found by the verifiers built for BUG-028…038, not by a new audit. Every one of
them was reproduced against a real PostgreSQL 16.2 database provisioned from this
repository, and every fix was shown to be load-bearing by restoring the defect and
watching the test go red.

## BUG-039 [P0] — 22 billing exposures: correct RLS policies that were never switched on
**Where** `migrations/006_reconcile_production_database.sql` PART 11; `invoices`,
`payment_methods`, `subscriptions`
**Status** FIXED

PART 11 creates owner-scoped policies for all three tables and never issues
`ALTER TABLE … ENABLE ROW LEVEL SECURITY`. A policy on a table with RLS disabled is
inert. Measured, not inferred — `staging/billing_exposure_probe.py` adopts each role and
executes the statement:

- `anon` could `SELECT` every row of all three tables;
- `authenticated` could `SELECT`, `UPDATE` and `DELETE` other customers' rows, and
  `INSERT` rows owned by another customer.

22 distinct (role, table, verb) exposures. Reachable: `algo22-terminal/src/supabase.js`
ships the browser a Supabase client built on `VITE_SUPABASE_ANON_KEY`, so a logged-in
customer holds a credential that reaches PostgREST directly.

**Fixed** in `migrations/hardening/999_privilege_hardening.sql` section 1. `ENABLE`, not
`FORCE`: the owner is exempt either way, and `FORCE` would break the SQLAlchemy reads at
`routers/billing.py:1598` and `:2576`. Regression: 27 tests in
`tests/test_billing_data_isolation_postgres.py`. See `BILLING_SECURITY_VERIFICATION.md`.

## BUG-040 [P1] — `anon` could `TRUNCATE public.profiles CASCADE`
**Where** Supabase's default `GRANT ALL` to `anon` and `authenticated`, unrevoked
**Status** FIXED

Row level security does not filter `TRUNCATE`. It is a table-level operation, so a role
holding the privilege empties the table regardless of any `USING` clause. Verified by
executing it: `TRUNCATE public.profiles CASCADE` as `anon` **succeeded**. Also `orders`,
`execution_records`, `referral_wallets`.

**Reachability, stated precisely.** `anon` and `authenticated` are `NOLOGIN`, PostgREST
exposes no `TRUNCATE` verb, and no function in the schema issues one. So this is a latent
blast-radius defect, **not** a confirmed remote exploit, and is reported as the former.
It still mattered: `TRUNCATE` on `profiles` and `TRIGGER` on another user's table are the
two privileges that turn any future SQL-injection or `SECURITY DEFINER` mistake from a
row leak into total data loss.

**Fixed** by `999_privilege_hardening.sql` section 0 — blanket
`REVOKE TRUNCATE, REFERENCES, TRIGGER` plus `ALTER DEFAULT PRIVILEGES` so tables created
later do not re-acquire them — and asserted as a provisioning gate by section 4's second
backstop. Verified by `staging/truncate_probe.py`, exit 0.

## BUG-041 [P1] — the provisioner was not idempotent, in the dangerous direction
**Where** `scripts/provision_database.py`, `SUBSTRATE_DDL`
**Status** FIXED

`SUBSTRATE_DDL` contained `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon,
authenticated`. On run 1 that executed against an empty schema and granted nothing. On
run 2 it executed against 85 tables.

Worse, the migrations' own `REVOKE` statements were correctly *skipped* on run 2 — their
checksums already matched — while the substrate grant ran again because it is setup
rather than a recorded file. **Run 2 ended less secure than run 1**, and nothing reported
it. Measured: 787 privilege rows re-granted.

**Fixed** by removing the blanket grant entirely; `ALTER DEFAULT PRIVILEGES` already
covers the fresh case that grant existed to serve. Rejected alternative — keep the grant
and always re-apply hardening — was measured and still left 185 re-granted rows.
Verified by `scripts/verify_provisioning.py`, which provisions twice and diffs 16
dimensions: `RESULT: REPRODUCIBLE`.

## BUG-042 [P1] — `PositionRepository.create()` silently dropped `exchange_id`
**Where** `backend_app/core/position_model.py`
**Status** FIXED

`create()` accepted `exchange_id` and did not persist it. `core/consistency_checker.py`
filters local positions **by** `exchange_id` when reconciling against the venue, so every
reconciliation run compared the venue's positions against an empty local set — and an
empty local set looks like agreement. A reconciliation control that cannot see the
positions it is reconciling is not a control.

**Fixed** by persisting the column. Regression: 6 tests in
`tests/test_position_lifecycle_postgres.py`.

## BUG-043 [P0] — both referral money functions wrote columns the table did not have
**Where** `migrations/referral_system_redesign.sql` vs
`migrations/006_reconcile_production_database.sql`
**Status** FIXED

Two migrations declare `referral_wallets` with different column sets. 006 runs first and
`CREATE TABLE IF NOT EXISTS` means the redesign's declaration does nothing — so the table
had 006's three columns while the *functions* (the redesign's versions, which run last and
win under `CREATE OR REPLACE`) wrote the redesign's four. Read out of `pg_proc`, not the
files: `staging/which_referral_funcs.py`.

Consequence: `process_referral_commission` and `reverse_referral_commission` both raise
`UndefinedColumn`. **Crediting a referral commission and reversing one on a refund both
failed.**

**Fixed** by a real forward migration in `referral_system_redesign.sql`: add the four
columns, backfill with the mapping written out explicitly because it is money, add the
non-negative CHECK. It **refuses** with a count and a sample if
`total_earned_usd < balance_usd + pending_usd`, because `paid_balance_usd` cannot then be
derived without inventing a figure. Regression: 12 tests in
`tests/test_referral_money_lifecycle_postgres.py`.

**Correction to an earlier version of this fix.** It originally ended by dropping the
three 006 columns, and
`tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements`
rejected that — Requirement 24.7 forbids destructive statements in the migration set. The
test was right. The columns hold money balances, a `DROP` against production is
irreversible, and if the backfill mapped something wrongly the original figures are the
only way to find out. They are now **retained** and inert: nullable, defaulted, written
by no installed function, read by nothing in `backend_app/`.

## BUG-044 [P1] — the referral wallet backfill had never once executed
**Where** `migrations/referral_system_redesign.sql`
**Status** FIXED

An unqualified `SELECT id` inside a join:

```
AmbiguousColumn: column reference "id" is ambiguous
```

Not a regression. That statement had **never run successfully in any environment**,
because nobody had ever applied the migration set from an empty database. Fixed by
qualifying it as `p.id`.

## BUG-045 [P0] — `referral_codes`: a superseded RLS policy that was never dropped
**Where** `migrations/006_reconcile_production_database.sql:569` vs
`migrations/referral_system_redesign.sql:91`
**Status** FIXED

Two overlapping permissive `SELECT` policies on the same table:

| policy | predicate |
| --- | --- |
| `referral_codes_select` (006) | `user_id = auth.uid() OR is_active = true` |
| `referral_codes_select_own` (redesign) | `user_id = auth.uid()` |

Permissive policies are **OR-ed**. The redesign added the correct policy and never
dropped the old one, so the broad predicate still decided every outcome. Since every code
is active, `is_active = true` reduces to `true`: any logged-in customer could run one
unfiltered select and obtain **every referral code in the business with the `user_id` that
owns it**. `REFERRAL_SYSTEM_REDESIGN_REPORT.md` states the intent as "Users can only see
their own referral code"; it was not achieved.

Found by `staging/anon_read_sweep.py authenticated`, which seeds a row owned by customer A
and reads it as customer B:

```
referral_codes      authenticated saw 1 of 1 row(s)
```

Reachable through the browser anon key, as BUG-039.

**Fixed as a pair,** because dropping the policy alone breaks a feature.
`POST /referral/validate` must read *somebody else's* code — that is what validation is —
and it used `get_request_supabase`, a client bound to the caller's JWT, so it depended on
the broad policy.

1. `referral_system_redesign.sql` now drops `referral_codes_select` and
   `referral_codes_insert`.
2. `routers/referral.py` gains `_get_service_client()` and the validate lookup runs as
   `service_role`.

The response is unchanged: `valid`, plus `referrer_id` **for a code the caller already
supplied**. Knowing one code's owner is the feature; enumerating all of them is not.

**Also fixed in passing**: the handler's `except` branch logged `request.referral_code`,
but `request` is the FastAPI `Request` and the body is `request_body`, so the handler
itself raised `AttributeError` and replaced every real error with a misleading one. The
code is no longer logged at all.

## BUG-046 [P1] — `library_ratings`: RLS enabled, policy predicate `true`
**Where** `library_ratings_select_authenticated`
**Status** FIXED

`FOR SELECT TO authenticated USING (true)`. RLS on, no row filter — and
`relrowsecurity = true`, so every inventory check reported the table as secured. That is
worse than an unguarded table, because it looks guarded.

The table is dual-purpose: `rating IS NOT NULL` rows are public marketplace reviews;
`rating IS NULL` rows are **favourites**, which
`tests/property/test_tenant_isolation_matrix.py` classifies `OWNER_SCOPED_LIST`.

**Honest statement of impact.** When found, `rating` was `NOT NULL`, so every row was a
review and the practical loss was limited to data intended to be public. A loaded gun
rather than a fired one — and it became load-bearing immediately, because fixing BUG-047
makes NULL-rating rows possible.

**Fixed** by `999_privilege_hardening.sql` section 3b:
`USING (rating IS NOT NULL OR user_id = auth.uid())`.

## BUG-047 [P1] — `library_ratings.rating NOT NULL` rejected two of its three writers
**Where** `backend_app/routers/library.py` lines 2484, 4159
**Status** FIXED

`rating smallint NOT NULL` with no default, against three documented write paths:

```
staging/library_ratings_probe.py
  rating column: is_nullable=NO  default=(none)
    REJECTED  POST /{id}/favorite  (rating = NULL)      23502 not-null violation
    REJECTED  POST /{id}/clone     (rating key absent)  23502 not-null violation
    ACCEPTED  POST /{id}/rate      (rating = 5)
```

Two failure modes, both bad:

- Favouriting raised, was caught by a bare `except Exception`, and returned
  `HTTPException(500)` — every call, every user. `GET /api/library/favorites` therefore
  always returned empty.
- The clone marker's insert is wrapped in `except _PostgrestAPIError` and logged as
  non-critical, so **cloning succeeded while silently never recording that it happened**.
  `is_verified_clone` was false for every row in the table, permanently.

`tests/test_marketplace_pipeline.py` asserts the clone marker IS written, and passes. It
writes into `patched_db.stores["library_ratings"]`, a Python list, which does not enforce
`NOT NULL`. This is the exact failure mode that motivated provisioning a real database.

**Fixed** by alembic `c3d4e5f6a7b8`: `rating` nullable, plus
`CHECK (rating IS NULL OR rating BETWEEN 1 AND 5)` — a range the original `smallint NOT
NULL` never enforced. `downgrade()` refuses, with a count, if NULL-rating rows exist.

Severity bounded: no shipped UI calls these three endpoints (no match for `favorite` in
any `.js/.jsx/.ts/.tsx` outside `node_modules`), so no customer was seeing the 500. That
bounds it; it does not change it.

## BUG-048 [P2] — `creator_analytics` was missed by the first exposure sweep
**Where** `staging/exposure_check.py`, `OWNER_COLUMNS`
**Status** FIXED

The sweep's list of per-customer column names did not include `creator_id`, so
`creator_analytics` — `creator_id` plus `total_earnings` — was not examined. RLS off,
`GRANT ALL` intact.

Recorded as its own entry because the lesson is about the checker, not the table. The
12-name definition of "per-customer column" now appears in exactly three places, each
with a comment saying they must not drift: `staging/exposure_check.py`,
`staging/permissive_policy_probe.py`, and `999_privilege_hardening.sql` section 4.

## Two defects introduced by this pass, caught by this repository's own gates

Recorded because the gates working is itself evidence, and because a reader comparing
test counts across runs should know why two went red.

**`tests/test_bandit_sast_gate_is_green.py` caught f-string SQL in both new alembic
revisions.** 24 interpolation sites across `a1b2c3d4e5f6` and `b2c3d4e5f6a7`. The gate's
rule is that an interpolated expression must pass through a validator that *raises*.
Fixed by adding `_validate_table_name` to both files and routing every identifier through
it — and by **binding** the column defaults in `b2c3d4e5f6a7` rather than splicing them,
since a default is a value and values can be parameters. Not suppressed; `# nosec` would
not have helped, because the gate reads the AST and ignores suppression comments.

**`tests/test_marketplace_paper_schema_contract.py::test_no_destructive_statements` caught
the `DROP COLUMN` in BUG-043's first fix.** See that entry.

---

# Found while building the production verification tooling (BUG-049 … BUG-051)

These were found by pointing the new production verifiers at the **local remediated**
database first, which is the only responsible way to ship a tool that will be run against
a live customer database. All three are defects in the remediated baseline itself.

That has a consequence worth stating plainly: **a drift check against a defective baseline
reports "no drift" and is still wrong.** Comparing production to this repository answers
"are they the same", not "are they correct". Both questions have to be asked, which is why
`scripts/verify_production_schema.py` runs absolute security checks alongside the
differential one.

## BUG-049 [P1] — `user_id` is declared with three different types; 12 columns can carry no foreign key
**Where** 12 tables across billing, credentials and risk
**Status** OPEN — reported, not fixed

`staging/identity_type_matrix.py` reports 81 identity columns: 43 correct, **12 of the
wrong type**, 26 `uuid` without a declared reference.

The three shapes:

| type | tables |
| --- | --- |
| `uuid` | 43 columns — the canonical shape, matching `profiles.id` and `auth.users.id` |
| `character varying(36)` | `invoices`, `payment_methods`, `subscriptions` |
| `text` | `billing_invoices`, `exchange_connections`, `exchange_keys`, `notification_settings`, `referral_profiles`, `risk_settings`, `risk_settings_audit`, `security_logs`, `strategy_limits` |

A foreign key cannot be created between two different types. This is BUG-033's unfixed
sibling: alembic `a1b2c3d4e5f6` normalised **`tenant_id`** to `uuid` and all 7 foreign
keys then landed. `user_id` was not part of that change, and nothing has declared a
reference from these 12 columns to the customer they belong to.

**IMPACT — integrity and retention, NOT confidentiality.** Stated precisely because the
difference decides the severity:

- a row can name a customer who does not exist;
- deleting a customer does not cascade, because there is no `ON DELETE` to cascade — the
  rows are simply orphaned. For `exchange_keys` that means **encrypted exchange
  credentials outliving the account that owned them**, which is the worst of the twelve;
- RLS still isolates every one of these tables. Their policies cast both sides —
  `(auth.uid())::text = (user_id)::text` — so a type mismatch creates no cross-tenant
  access. Confirmed behaviourally, not assumed: `scripts/verify_production_security.py`
  reports `invoices` and `subscriptions` as `ISOLATED`, meaning the owner reads its own
  row and neither another customer nor `anon` can.

**Not fixed here, deliberately.** Normalising these columns means `ALTER COLUMN … TYPE
uuid USING …::uuid` on tables holding invoices and encrypted credentials. Alembic
`a1b2c3d4e5f6` shows what that migration has to do properly: drop and restore every
dependent policy, and **refuse with a count and a sample** if any value is not a UUID,
rather than guessing. That is a migration against financial data and it needs its own
review, a backup and an approval — which is exactly what the launch gate says to do with
a RED finding.

## BUG-050 [P2] — two bugs in the first version of the overlapping-policy detector
**Where** `staging/overlapping_policy_detail.py`
**Status** FIXED

Recorded because the detector is now cited as evidence, so its own history matters.

1. `d.strip().strip("()")` was peeling the predicate's wrapping parentheses and also ate
   the ones in `auth.uid()`, so `(referrer_id = auth.uid())` became
   `referrer_id = auth.uid` and three correctly-scoped policies were reported as suspect.
2. The hand-written ownership-column list was missing `purchaser_id`, so
   `marketplace_settlements`' `settlements_purchaser_read` — a textbook ownership test —
   was flagged as unrecognised.

Both were false positives, which is the less dangerous direction, but the second has the
same root cause as a false NEGATIVE that mattered: `creator_id` was missing from the same
list on the first sweep and `creator_analytics` went unexamined.

**Fixed structurally rather than by adding two names.** `scripts/owner_columns.py` now
DERIVES the set from `pg_constraint` — any column with a foreign key to `profiles` or
`auth.users` is, by construction, a column naming a customer — and keeps the written list
only as a floor for columns that hold an identity without declaring a reference (which is
all 12 of BUG-049). Running it immediately surfaced three more the floor had never
mentioned: `referred_by_user_id`, `reviewed_by`, `top_creator_id`.

## BUG-051 [P2] — a stale `AERORA_TEST_DATABASE_URL` silently overrode the DSN file
**Where** `staging/dsn.py`
**Status** FIXED

`resolve_dsn()` prefers the environment variable over `staging/pg_dsn.txt`, which is
correct for CI. During this work a stale value inherited from a parent shell pointed a
probe at a server that no longer existed, and the connection was refused — merely
confusing.

The dangerous form of the same mistake is an environment variable naming a **different
live database**: every probe would run, pass, and report on a database nobody intended to
check. An exit-0 "all green" against the wrong target is the worst possible output of this
tooling.

**Fixed** by making `resolve_dsn()` announce which source it used and print the redacted
target, and by having `staging/run_all_probes.py` print `target: …` above its results. A
result is now never readable without the database it describes.

## Calibration defects in the production security probe, found before it was trusted

Not numbered, because none of them was a defect in the product — all four were defects in
the probe, found by running it against a known-good database and refusing to accept its
first answer. Recorded because the probe's output is cited, and a reader is entitled to
know why its numbers moved.

The first run reported **21 tables failing**. All 21 were the probe's fault:

1. **It set only one of the two policy families.** This schema uses
   `request.jwt.claim.sub` (read by `auth.uid()`) *and* `app.current_tenant_id`. Setting
   only the first made every table in the second family report that the owner could not
   see its own row.
2. **It ran the destructive attempts before the positive control.** On any table where
   B's `DELETE` succeeded, A's row was already gone, so "the owner cannot see its own
   row" was a consequence of the previous step rather than a separate finding.
3. **The foreign-key borrow overrode the ownership column.** `_seed` satisfied foreign
   keys by taking whatever parent row existed, so the seeded row ended up owned by a
   third party and the owner's own-row SELECT correctly found nothing.
4. **It only populated NOT NULL columns.** On the blob-shaped tables — `orders` is
   `(id, tenant_id, data, created_at, updated_at)` — `tenant_id` is nullable, so the
   seeded row had a NULL owner and no policy could match it.

After all four were fixed: **58 ISOLATED, 0 LEAK, 10 NO CONTROL, 2 NOT SEEDED.**

The `NO CONTROL` category is the honest residue and is deliberately **not** counted as a
pass. It means every attempt was blocked including the legitimate one, so the probe did
not establish that the policy *discriminates* — and a policy of `USING (false)` would
produce the same output as perfect isolation. Two causes, both reported separately:

- `REFUSED(42501)` on `payment_methods`, `creator_analytics`, `marketplace_analytics`,
  `transaction_rollbacks` — `authenticated` holds no grant at all. Intentional; the
  hardening revokes them and `payment_methods` is served exclusively over SQLAlchemy.
  More restrictive than required, not less.
- `NO ROWS` on `execution_records`, `referral_profiles` and four `paper_*` tables —
  ownership is **indirect**. `paper_orders` is owned through `paper_accounts.user_id`,
  not through its own `user_id`, so a probe scoping by the column on the table itself
  cannot establish ownership. A limitation of a generic probe, reported as such.

---

# BUG-049 closure, and the four defects found while closing it

## BUG-049 [P1] — **FIXED**

Status moves from OPEN to FIXED. 12 customer-identity columns are now `uuid` with a
foreign key to `profiles(id)` and an ON DELETE action chosen per table.

**Enumerated and classified before anything was altered.** `scripts/bug049_enumerate.py`
examined 81 identity-like columns and wrote `BUG049_IDENTITY_MATRIX.json`: 72 class A
(canonical customer identity), 5 class B (tenant), 0 class C (external provider), 4
class D (`paper_*.account_id`, which reference `paper_accounts` and are not a customer),
0 class E, 0 unclassified.

The class C rule is the reason this was a classification exercise and not a
search-and-replace: converting `provider_reference` to a uuid would destroy the only link
to Stripe or Razorpay. It did not fire, and that is worth stating plainly rather than
presenting as a finding.

| ON DELETE | tables | reasoning |
| --- | --- | --- |
| `CASCADE` | `exchange_connections`, `exchange_keys`, `notification_settings`, `referral_profiles`, `risk_settings`, `strategy_limits` | `exchange_keys` and `exchange_connections` are the security cases: an orphaned row is an encrypted exchange API key with no owner, which cannot be audited, rotated or revoked through the product |
| `RESTRICT` | `billing_invoices`, `invoices`, `payment_methods`, `subscriptions`, `risk_settings_audit`, `security_logs` | financial and audit records must SURVIVE customer deletion. All six columns are NOT NULL so `SET NULL` is impossible; `RESTRICT` forces an account-deletion path to handle them explicitly |

`RESTRICT` was chosen only after confirming it breaks nothing: there is **no
account-deletion path in `backend_app/`** — no `DELETE FROM profiles`, no `delete_user`.
It constrains a flow that does not exist yet.

**Verified behaviourally, not by reading DDL.** 76 tests in
`tests/test_bug049_identity_types_postgres.py` (75 pass, 1 documented skip):

- inserting an exchange credential for a nonexistent customer is **rejected**;
- deleting a customer **removes** their exchange credentials;
- deleting a customer who has an invoice is **refused**, and the invoice survives;
- a schema-wide sweep fails if any `user_id`/`tenant_id` is not `uuid`;
- every restored policy **type-checks**, forced via `EXPLAIN`.

The skip is `payment_methods`, where `authenticated` holds no grant so the policy cannot
be exercised from that role. Recorded as a skip with a reason, not a pass.

**Downstream changes this forced**, each of which is a real consequence rather than
test-fixture churn:

- `core/models/billing.py`: `PaymentMethodModel.user_id` was `String(36)` against a now-
  `uuid` column. Changed to `Uuid(as_uuid=False)` — `as_uuid=True` would return
  `uuid.UUID` objects and silently falsify every `row.user_id != user["id"]` comparison
  against a JWT claim, the same trap that would have made `routers/dag_tasks.py:149`
  reject every legitimate owner. `payment_methods.id` was deliberately left
  `character varying(36)`: it is a surrogate key, not a customer identity.
- `tests/test_billing_data_isolation_postgres.py`: six read-back queries changed from
  `SELECT user_id` to `SELECT user_id::text`. Not one assertion relaxed — the failures
  read `"user A sees other owners: [UUID('a0a0...')]"` where `a0a0` **is** user A, so the
  ownership being asserted was exactly right and only the comparison was comparing a
  `UUID` to a `str`.

## BUG-052 [P1] — the first BUG-049 migration could not restore its own policies
**Where** `d4e5f6a7b8c9`, first attempt
**Status** FIXED

`a1b2c3d4e5f6` restored policies verbatim and that was correct there, because every
predicate it moved was already double-cast. These are not. `billing_invoices` carries

```sql
USING ((auth.uid())::text = user_id)
```

— left side cast, column bare. Valid while the column is `text`; the moment it becomes
`uuid`, restoring that verbatim fails with `operator does not exist: text = uuid`.

**The failure behaved correctly**, which is worth recording: alembic's transactional DDL
rolled the entire revision back — 188 policies intact, columns still `text`, revision
unmoved. "Fail clearly and leave the database recoverable" held under a real failure.

Fixed by casting the column in the predicate, producing the same double-cast shape
`invoices` already uses — and only when the server itself rejects the verbatim form with
SQLSTATE 42883. Verbatim remains preferred and is attempted first, so a predicate that
still type-checks is never touched.

Rewriting to a native `auth.uid() = user_id` comparison was considered and **rejected**:
uuid equality normalises case, so a stored uppercase id would start matching its owner
where the text comparison never did. That is arguably better, but it is a behaviour
change, and a type conversion is not the place to silently alter who can see a row.
Carried to `POST_LAUNCH_IMPROVEMENTS.md`.

## BUG-053 [P1] — the BUG-049 migration ran too early to see 9 of its 12 tables
**Where** `scripts/provision_database.py` STEP 0c ordering
**Status** FIXED

`provision_database.py` runs `alembic upgrade head` at STEP 0c, **before** the numbered
SQL set, because `006_reconcile_production_database.sql` ALTERs `execution_records` and
only alembic declares it. At STEP 0c, 9 of the 12 BUG-049 target tables do not exist yet.

The revision's `_table_exists` guard correctly skipped them — and a freshly provisioned
database came out with 9 columns still `text` and 9 foreign keys missing, while the
migrated database had them. Caught by `scripts/verify_production_schema.py`:

```
drift/columns      9 missing, 9 unexpected (baseline 1135, production 1135)
drift/foreign_keys 0 missing, 9 unexpected (baseline 112, production 121)
```

Worth noting what found it: the **differential** check against a baseline, not any
absolute check. The absolute identity-type check passed on the migrated database and
would have passed on the fresh one too, because `identity_type_matrix.py` was run against
the migrated database. Comparison and absolute checking each catch what the other misses.

**Fixed** by `migrations/hardening/998_bug049_identity_types.sql`, applied as an explicit
**STEP 3b** between the SQL set and the privilege hardening. Not by filename ordering —
within `migrations/` files sort numbered-then-alphabetical and a new `zz_*.sql` would
displace it, the same fragility rejected earlier for the hardening itself.

Fresh provision now reports 121 foreign keys and `RESULT: REPRODUCIBLE`; drift reports
`NO DRIFT`.

The duplication between the SQL file and the alembic revision is bounded by
`tests/test_bug049_sql_and_alembic_agree.py` (15 tests), which parses both and fails if
they disagree about the target list, the ON DELETE per table, the foreign-key naming
convention, the UUID pattern, or whether each of the SQL file's three sections covers the
same tables — a table in the conversion list but missing from the precheck would be
converted without its values being inventoried first.

## BUG-054 [P2] — a UTF-8 BOM silently disabled the AST-based safety checks
**Where** `scripts/verify_production_schema.py`, `scripts/verify_production_security.py`
**Status** FIXED

PowerShell 5.1's `Set-Content -Encoding utf8` writes UTF-8 **with a byte-order mark**.
Both production scripts acquired one when they were edited that way.

CPython's import machinery tolerates a BOM, so both scripts ran and produced correct
results — nothing looked wrong. But anything reading the source as text and parsing it
sees U+FEFF as a character:

```
ast.parse(open(path, encoding="utf-8").read())
SyntaxError: invalid non-printable character U+FEFF
```

Every structural safety check in
`tests/test_production_security_probe_rolls_back_postgres.py` depends on that parse —
the checks that prove the production probe contains no reachable `commit()`, never
enables autocommit, and performs its rollback inside a `finally`. All three were unable
to parse their target.

They **failed closed**, because they assert on the parse rather than skipping on error.
A laxer implementation — `try: parse / except: pass` — would have reported the probe as
safe without having examined it. `tests/test_bandit_sast_gate_is_green.py` walks module
ASTs the same way and has the same exposure.

**Fixed** by `staging/strip_bom.py`, which scans 681 source files and removes any BOM,
plus a dedicated test asserting their absence **first**, so the failure says "there is a
BOM" rather than "the commit check could not run". Recorded in the provisioning runbook
as a reason not to edit source through PowerShell.

## BUG-055 [P2] — the policy-shape checker reported the most sensitive table as defective
**Where** `scripts/verify_production_schema.py` check 3e
**Status** FIXED

Check 3e flags a policy on a per-customer table that names no ownership column. It
reported four, three of which were `profiles` — `Select own profile`, `Update own
profile` and `profiles_owner_access`, all `USING (auth.uid() = id)`, which is exactly
correct.

`profiles` is owned by its own `id`, and a bare `id` is deliberately excluded from the
global ownership-column list because including it would make every table in the schema
match. `ALWAYS_PER_CUSTOMER_TABLES` exists for this case and 3e did not honour it.

A false positive, so the less dangerous direction — but a checker that calls the most
sensitive table in the schema defective is a checker whose output gets ignored, and that
is how a true positive gets missed. Fixed, and the fourth
(`marketplace_listings.All users can view published listings`) is now an allowlist entry
with its written justification rather than a recurring red line.
