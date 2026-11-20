# Trading Execution Audit

**Scope:** strategy → backtest → paper → Binance Testnet → live gate → fills → Signal Trace / Trade History → marketplace history. All exchange traffic was Testnet (`VYOMQUANT_LIVE_SANDBOX_ONLY=true`). No production keys were used and no real-money order was placed.

## 1. Live execution gate

`require_live_authorization` (REQUIRED_CHECKS) is the only path to a live order. It was re-read and re-tested:

| Check | Result |
|---|---|
| Exchange key present, decryptable, sandbox-flagged | PASS: the deploy is refused without a key |
| Plan includes `live_trading` | PASS at the gate. **FAIL per order (LIVE-ENT-01):** the entitlement was asserted only when the deployment started. A plan that lapsed or downgraded afterwards kept trading live until restart. |
| Kill switch, risk verdict | PASS: kill switch is absolute; reducing orders now bypass only the opening limits (RISK-REDUCE-01) |
| Browser cannot create a live deployment | PASS: `guard_strategy_deployments_browser_roles` keeps browser INSERTs paper-only |
| Browser cannot re-activate a stopped deployment past the bot quota | **FAIL, open (STRAT-QUOTA-01, P2):** an owner can PATCH `strategy_deployments.status` to `active` through PostgREST. This is a bot-count quota bypass, not a live-execution bypass; the order path still runs the full gate. |

**LIVE-ENT-01 fix** (`master_executor.py`): before every non-paper submission, `live_trading_entitlement_holds(user_id)` runs.
- It fails closed: any read error means "no".
- If it returns false, the order is skipped and a `REJECT_TIER_LIMIT` audit signal is recorded, so Signal Trace shows why.
- Test: `tests/test_live_ent01_live_entitlement_is_checked_per_order.py`.

## 2. Paper engine

- **PAPER-EXP-01 (P1, fixed).** `_seed_paper_engine_from_ledger` added the ledger's open position to the in-memory engine on every restart, without replacing it.
  - Evidence: the QA account held 1,122.6 SOL (~$80k) against a $5k free-plan cap, growing by about $5k per ECS task restart.
  - The seed now sets `recovered_exposure_coins = max(0, open_long_size(symbol))`. It is a value, not an increment.
- **RISK-REDUCE-01 (P1, fixed).** `place_order` applied the drawdown, daily-loss, consecutive-loss and utilisation guards to every order, so a trader at 80.14 % utilisation could not sell to reduce.
  - Reducing orders (qty ≤ position, opposite side) now skip the opening limits.
  - The kill switch still blocks everything.
  - 8 tests; 5 fail without the fix.
- **PT-05 (P0, fixed under SEC-DEF-01).** `paper_apply_fill_atomic` accepted any `p_user_id` from any signed-in user. It now goes through the `sec_def01_assert_caller_is` wrapper.

## 3. Order watchdog and reconciliation

- **TXN-IDLE-01 (P1, fixed).** The watchdog's monitoring and reconciliation loops share one SQLAlchemy session and never ended the read transaction between cycles.
  - Evidence: production showed a backend `idle in transaction` for 2 h 27 m. Migration 079's `ALTER TABLE` queued behind it and would have blocked every later query on `execution_records`.
  - Handled live with `pg_cancel_backend`, then `pg_terminate_backend` and `lock_timeout=3s`.
  - Fix: `_end_cycle_transaction()` commits at the end of every cycle, resetting the session on failure.
  - Test: `tests/test_txn_idle01_watchdog_ends_every_cycle.py`.
- Sentry corroborates the history: VYOMQUANT-API-PRODUCTION-18 / 19 (`PendingRollbackError`, 6,700 events on 11-14) and 2E (11-19 21:26, at the moment of the terminate).

## 4. Fills, environment and history

- **EXEC-ENV-01 (P1, fixed).** `execution_records` did not record whether a fill was Testnet. The marketplace's 24 h "live history" gate (`MP_LIVE_HISTORY`) counted any row with an `exchange_id`, so Testnet trading qualified a strategy as live-proven. FROM already had 15.7 h.
  - Migration **079** adds `is_sandbox boolean` (nullable; applied to production).
  - `mark_submitted` stamps the column.
  - The eligibility gate requires `is_sandbox = false`, so NULL (legacy) rows do not count.
- **Trade History:** reads `execution_records`.
  - Fees are converted to quote; a placeholder fee from the ACCEPTED state is ignored.
  - Realised P&L is computed by average cost per (strategy, market), with fees in the cost basis.
  - A sell with no basis in history reports `null`, never a guess.
  - Re-read and PASS.
- **Signal Trace:** own signals only; other-tenant ids return 404; latency measured. PASS.

## 5. Backtester (as an execution model)

| Run | Config | Result |
|---|---|---|
| A | defaults, 10,000 USDT | 1 trade, +0.0678 %, max DD 0.354 % |
| B | 4× commission (0.004) and slippage (0.002) | 1 trade, fees $7.76, −0.6068 % |
| C | 25,000 USDT, tighter risk | 1 trade, −0.1896 %, final 24,952.60 |

- Costs and capital are applied; B is strictly worse than A, as it must be.
- All three windows were cut at the 10,000-bar cap (`truncated_by_cap: true`, about 7 days of 1 m data). This is disclosed.
- **BT-RISK-01 (P2, open):** `spread`, `risk_per_trade`, `max_drawdown` and `daily_loss_limit` are accepted but not simulated. Sizing is `trade_size_pct = 0.1`. This is disclosed in `configuration_not_applied`, but a trader would reasonably expect them applied.
- **BT-504-01 (P2, open):** the synchronous `execute` returns a gateway 504 at 60 s while the run completes server-side (run A finished in 65 s). The async start endpoint from the merged commit `f3323dbd` (PR #354) is the remedy; the UI should use it for windows over about 30 s.

## 6. Not verified here (BLOCKED)

QA-identity Testnet connect, deploy, fill and stop (TN-05).
- Needs the owner's Testnet API key, entered with MFA in the secure UI.
- The audit may not create, borrow or bypass either.
- The owner's own Testnet deployment was observed: it resumed through the orphan sweep and its fills reached Trade History.
