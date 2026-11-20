# QUOTA ENFORCEMENT MAP — Aerora / VyomQuant

Required deliverable for QUOTA-01.

---

## 0. HEADLINE — THE AUDIT FINDING WAS TRUE BUT MISLEADING

The audit reported: *"15 `HardQuotaEnforcer` methods with zero real call sites."* That is
**factually correct and remains correct.** The inference drawn from it — that the
product's quotas are unenforced — is **wrong**.

The product enforces quotas through a **different, fully wired, fail-closed mechanism**:
`backend_app/core/subscription_dependencies.py`, carried as FastAPI `Depends(...)` on the
actual mutation routes. `HardQuotaEnforcer` is a **parallel, unwired, second
implementation** of overlapping limits.

> **Wiring `HardQuotaEnforcer` would be a defect, not a fix.** It would produce double
> enforcement from two different limit sources (`TenantQuota` vs the subscription plan
> catalogue), so a user could be refused by one and permitted by the other depending on
> which ran first.

This is the same shape as TENANT-01: the prescribed remediation would have made things
worse, and measuring first is what revealed it.

---

## 1. THE ENFORCED QUOTAS

Enforcement point is a FastAPI dependency, so it runs **before the handler body** —
before the side effect — structurally, not by convention. That is the invariant the brief
asked for: *a quota breach must prevent the side effect.*

| Quota | Resource | Enforcement point (`Depends`) | Before side effect? | Fails closed? |
|---|---|---|---|---|
| Strategy count | `strategies` | `check_strategy_quota` — `routers/strategies.py:1152` (create), `routers/library.py:2199` (clone) | **Yes** (dependency) | **Yes** — 503 `ENTITLEMENT_USAGE_UNREADABLE` |
| Bots / deployments | `strategy_deployments` | `check_bot_quota` — `routers/strategies.py:2012` (deploy) | **Yes** | **Yes** |
| Backtests | `backtests` | `check_backtest_quota` — `routers/strategies.py:2860`, `routers/strategy_operations.py:1345`, `:1577` | **Yes** | **Yes** |
| ML training | training jobs | `check_ml_quota` — `routers/strategies.py:2475`, `routers/strategy_operations.py:5930` | **Yes** | **Yes** |
| Optimization runs | optimizer | `check_optimization_quota` — `routers/strategies.py:3472`, `:3541`, `:3601`, `routers/strategy_operations.py:2227` | **Yes** | **Yes** |
| Marketplace publishes | listings | `check_marketplace_publish_quota` — `routers/library.py:1893` | **Yes** | **Yes** |
| Marketplace subscriptions | subscriptions | `check_marketplace_subscription_quota` — `routers/library.py:2834` | **Yes** | **Yes** |
| Exchange connections | `exchange_keys` | `check_exchange_connection_slot` — called in `routers/exchange.py` | **Yes** | **Yes** |
| ML model slots | `model_versions` | `check_ml_model_slot` (helper form) | **Yes** | **Yes** |
| Strategy versions | `strategy_versions` | `check_strategy_version_quota` (helper form) | **Yes** | **Yes** |
| Live-trading feature | — | `require_live_trading` — `routers/strategies.py:2012` | **Yes** | n/a (feature gate) |
| ML-training feature | — | `require_ml_training` — `strategies.py:2475`, `strategy_operations.py:5930` | **Yes** | n/a |
| Optimization feature | — | `require_optimization` — 4 sites | **Yes** | n/a |
| Marketplace publish/subscribe features | — | `require_marketplace_publish` / `require_marketplace_subscribe` | **Yes** | n/a |

### The fail-closed behaviour, verbatim

`core/subscription_dependencies.py:649-654`:

```python
except UsageReadFailed as exc:
    # A figure that could not be read is not a figure of zero. 503, not a pass.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "ENTITLEMENT_USAGE_UNREADABLE", "message": str(exc)},
    )
```

Repeated at `:807`, `:823`, `:858`. This is exactly the property the brief demanded: *if
the usage state cannot be determined safely, the protected operation must fail closed.*
It was already there.

### Identity provenance

Every one of these takes `user: dict = Depends(get_current_user)` — the identity is
**token-derived**, never caller-supplied. There is no quota dependency that accepts a
user or tenant id from a request body, path or query parameter.

---

## 2. THE 15 `HardQuotaEnforcer` METHODS

All at `backend_app/core/hard_quota_enforcer.py`; singleton at `:706`. **Zero invocations
anywhere.** Two instances are constructed and never used:
`core/entitlement_engine.py:224`, `core/dag_task_queue.py:349`.

| # | Method | Line | Overlaps an enforced quota? | Disposition |
|---|---|---|---|---|
| 1 | `enforce_dag_session_limit` | 194 | no | DAG sessions — see §4 gap |
| 2 | `enforce_dag_node_limit` | 217 | no | §4 gap |
| 3 | `enforce_symbol_limit` | 240 | no | §4 gap |
| 4 | `enforce_indicator_limit` | 263 | no | §4 gap |
| 5 | `enforce_backtest_parallel_limit` | 286 | partially — `check_backtest_quota` caps count, not parallelism | §4 gap (parallelism) |
| 6 | `enforce_order_rate_limit` | 313 | no | §4 gap — **highest value** |
| 7 | `enforce_daily_trade_limit` | 341 | no | §4 gap |
| 8 | `enforce_active_order_limit` | 370 | no | §4 gap |
| 9 | `track_order_creation` | ~400 | n/a (tracking) | dead with the above |
| 10 | `enforce_position_limit` | 417 | no | §4 gap |
| 11 | `enforce_capital_limit` | 440 | partially — the gateway's notional ceiling bounds a single order | §4 gap (aggregate) |
| 12 | `update_allocated_capital` | ~470 | n/a (tracking) | dead with the above |
| 13 | `enforce_leverage_limit` | 488 | partially — `max_leverage` is read by the notional ceiling | §4 gap (per-request) |
| 14 | `enforce_concentration_limit` | 512 | no | §4 gap |
| 15 | `enforce_storage_limit` | 548 | no | §4 gap |
| 16 | `enforce_websocket_limit` | 586 | no | §4 gap |

**Recommendation: deprecate the class.** Not because the limits are worthless — several
in §4 are real gaps — but because:

1. Its limit source is `TenantQuota` (`core/tenant.py`), a *different* catalogue from the
   subscription plans that the wired path reads. Two sources of truth for the same number
   is the bug.
2. `core/dependencies.py:318` collapses `tenant_id` to `user_id`, so its tenant model is
   the same one TENANT-01 concluded is vestigial.
3. Its usage counting reads Redis (`redis_manager.scard`, `.smembers`, `.hget`), which is
   **fail-soft** — so a Redis outage would make every usage count read as zero and every
   quota pass. That is fail-**open**, the opposite of the wired path's 503.

Point 3 is decisive: wiring it as written would introduce a fail-open quota path
alongside a fail-closed one.

---

## 3. CONCURRENCY

| Quota | Could two concurrent requests both observe capacity? | Database backstop |
|---|---|---|
| **Bots / deployments** | The dependency alone: yes. | **No — two backstops exist.** `uq_strategy_deployments_one_live` (UNIQUE on `strategy_id` for live statuses) and, since WORK-03, `pk_bot_ownership_claims` (PRIMARY KEY on `user_id, symbol`). Proven by execution: 27 tests incl. 4-process races, `staging/work03_RUNH8.txt` |
| Exchange connections | yes | not established — see §4 |
| Strategies | yes | **none found** — see §4 |
| Backtests / ML / optimization | yes | none; these are rate-ish limits on queued work, so a small overshoot is not a safety or money event |
| Marketplace subscriptions | yes | `uq_referral_relationships_referred`-style constraints exist elsewhere; not verified for subscriptions |

The safety-critical one — **can two concurrent requests start two bots** — is closed at
the database level and verified under real concurrency, including across separate OS
processes. That is the quota whose breach costs money.

---

## 4. REMAINING GAPS (OPEN, NOT CLOSED)

Stated honestly rather than folded into a green tick:

| Gap | Severity | Note |
|---|---|---|
| **Order rate / daily trades / active orders** | P2 | `enforce_order_rate_limit`, `enforce_daily_trade_limit`, `enforce_active_order_limit` are unwired, and no equivalent exists on the wired path. Mitigated today by live trading being OFF and by the per-route SlowAPI limits; becomes material the moment live trading is certified |
| **Aggregate capital / leverage / concentration** | P2 | The gateway's notional ceiling bounds ONE order against equity (`core/execution_engine.py`); nothing bounds the portfolio in aggregate |
| **Strategy-count race** | P3 | Two concurrent creates could both pass `check_strategy_quota`. No DB constraint found. Overshoot is one row, not money |
| **Exchange-connection race** | P3 | Same shape, and the limit is small (`max_api_slots` defaults to 1), so an overshoot is plausible |
| **DAG session/node/symbol/indicator limits** | P3 | Unwired; these are resource-consumption limits, not safety or money |
| **Storage / websocket limits** | P3 | Unwired |

None of these is a money-integrity or authentication defect. The one that would be —
duplicate bot execution — is closed by WORK-03.

---

## 5. STATUS

```
QUOTA-01: DOWNGRADED from P1 to P2, NOT CLOSED
```

**Downgraded** because the product's contractual quotas *are* enforced, before the side
effect, with token-derived identity and a fail-closed 503 on unreadable usage — and
because the prescribed fix (wire `HardQuotaEnforcer`) would introduce a fail-open path
and a second source of truth.

**Not closed** because the §4 gaps are real, `HardQuotaEnforcer` has not actually been
deprecated in code, and the strategy/exchange-connection races are unaddressed.
