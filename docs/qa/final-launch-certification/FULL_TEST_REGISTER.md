# Full Test Register

Every component was re-tested in this audit; no earlier result was carried over.

**Status values:**
- PASS / FAIL
- BLOCKED: needs an input the rules forbid fabricating
- NOT IMPLEMENTED / NOT VERIFIED / NOT APPLICABLE

**Severity:** P0 is a launch blocker; P3 is cosmetic or defence in depth.

**Identities used:**
- QA Seller: a free-plan account, `claude-launch-…@vyomquant-audit.invalid`, labelled QA.
- Anonymous: the public anon key only.

QA Subscriber and QA Referral identities could not be created because signup is broken (AUTH-SMTP-01).

## 0. Environment and identities

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| ENV-01 | Production reachable, health/ready | PASS | | `/api/health/ready` 200 |
| ENV-02 | `VYOMQUANT_LIVE_SANDBOX_ONLY=true`, Testnet only | PASS | | Task definition env; every venue call uses testnet |
| ENV-03 | Email signup for a new identity | **FAIL** | **P0** | Supabase `/auth/v1/signup` 500 "Error sending confirmation email"; auth log SMTP `535 Authentication credentials invalid` (AUTH-SMTP-01) |
| ENV-04 | Google OAuth sign-in | PASS | | Existing OAuth users sign in |
| ENV-05 | AWS / CloudWatch access for the audit | BLOCKED | | STS: expired credentials (retried 2026-11-20) |

## 1. Exchange credentials

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| EX-01 | Keys stored encrypted (Fernet) with a sandbox flag; no plaintext column | PASS | | Schema plus row inspection (values not read) |
| EX-02 | Customer reads of another tenant's keys | PASS | | RLS: 0 rows; IDOR sweep |
| EX-03 | Key-mutation routes need AAL2 (MFA) | PASS | | `require_aal2` on connect/rotate/delete (code plus test suite) |
| EX-04 | Connect and save a Testnet key as the QA identity | BLOCKED | | Needs the owner's Testnet key and MFA in the secure UI |

## 2. Strategy Builder (conventional and ML/DL)

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| SB-01 | List / read own strategies, versions | PASS | | `/api/strategies`, `/versions` 200 |
| SB-02 | Free plan strategy quota enforced (1) | PASS | | `/api/billing/plan` usage 2 of 1 (legacy rows) and create refused |
| SB-03 | Registry endpoints (blocks, indicators, features, models, timeframes) | PASS | | All 200 |
| SB-04 | Other tenant's strategy sub-routes | PASS* | P3 | No data leaked, but 200 with an empty list instead of 404 (IDOR-404-01) |
| SB-05 | ML/DL strategy authoring on Free | PASS | | ML features gated (403 `ML_NOT_INCLUDED`) |

## 3. ML training

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| ML-01 | Plan gate on Free | PASS | | POST training/jobs: 403 `ML_NOT_INCLUDED` |
| ML-02 | List needs `version_id` | PASS | | 422 on missing param |
| ML-03 | Training job lifecycle (queue → run → artifact) | NOT VERIFIED | | `training_jobs` has 0 rows ever in prod; needs a Pro QA account |

## 4. Backtests (3 runs plus boundaries)

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| BT-01 | Run A, defaults, 2026-08-01..08-15 1m SOL/USDT | PASS* | P2 | Completed in the database (id 456b7e87, 65 s). The HTTP call returned **504 at 60 s** (BT-504-01). 1 trade, +0.0678 %. |
| BT-02 | Run B, 4× commission / slippage | PASS | | id d9904fb4: config echoes 0.004/0.002; fees $7.76; return −0.6068 % (worse than A, as expected) |
| BT-03 | Run C, $25k capital, tighter risk | PASS* | P2 | id fd9db405: capital applied (final 24,952.60). Risk params accepted but **not applied**, and this is disclosed in `configuration_not_applied` (BT-RISK-01). |
| BT-04 | Bar cap disclosed | PASS | P3 | `truncated_by_cap: true`; the window ends about 7 days in; disclosed in `data_coverage` |
| BT-05 | Negative commission or capital, missing date, extra fields | PASS | | 422 with field-level details |
| BT-06 | start > end, or a future window | PASS | | 422 `BACKTEST_NOT_RUNNABLE` (0 bars) |
| BT-07 | Free monthly quota (10) enforced | PASS | | 403 `BACKTEST_LIMIT_REACHED` |
| BT-08 | A rejected request does not consume quota | **FAIL** | P2 | Meter at 10/10 with 5 runs stored: the reserving dependency runs before body validation (QUOTA-422-01, open) |
| BT-09 | Quota durable across a Redis flush | **FAIL → fixed** | P2 | `plan_usage_ledger` had 0 rows platform-wide (QUOTA-LEDGER-01, fixed d783934a) |
| BT-10 | Backtest of an unknown or other strategy | PASS | | Refused (quota first, then 404); no data |

## 5. Paper trading

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| PT-01 | Account, positions, orders, trades, sessions read | PASS | | All 200, own rows only |
| PT-02 | Exposure survives a restart without growing | **FAIL → fixed** | P1 | 1,122.6 SOL (~$80k) against a $5k cap; +~$5k per restart (PAPER-EXP-01) |
| PT-03 | Capital-utilisation limit on opening orders | PASS | | Refused at 80.14 % > 80 % |
| PT-04 | Reducing / closing order allowed over the limit | **FAIL → fixed** | P1 | Sell blocked at 80.14 % (RISK-REDUCE-01) |
| PT-05 | Paper fill RPC cannot write another user's ledger | **FAIL → fixed** | P0 | SEC-DEF-01; 42501 after migration 078 |
| PT-06 | Paper trades carry strategy name and slippage | PASS | | Earlier fix re-verified in `/api/paper/trades` |

## 6. Binance Testnet deploy and lifecycle

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| TN-01 | Live gate REQUIRED_CHECKS (key, sandbox, entitlement, risk, kill switch) | PASS | | Code plus a refused deploy without a key |
| TN-02 | Entitlement re-checked per live order | **FAIL → fixed** | P1 | LIVE-ENT-01 |
| TN-03 | Browser INSERT of a live deployment | PASS | | Guard trigger: browser roles paper-only |
| TN-04 | REST status flip bypasses the bot quota | **FAIL** | P2 | `strategy_deployments.status` writable by owner (STRAT-QUOTA-01, open) |
| TN-05 | Deploy, run, stop on Testnet with the QA identity | BLOCKED | | EX-04 |
| TN-06 | Orphan resume after a restart | PASS | | Sweep resumed owner's testnet deployment (logs) |

## 7. Live trading, Signal Trace, Trade History

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| LT-01 | Kill switch blocks new orders; recover requires confirm | PASS | | `/api/risk/kill-switch` contract; risk status reflects it |
| LT-02 | Signal Trace list / detail / timeline / export, own only | PASS | | 200; other tenant id 404 |
| LT-03 | Trade History live ledger from `execution_records`, fees in quote, realised P&L | PASS | | Earlier fixes re-read; PnL walk verified |
| LT-04 | Testnet fills excluded from marketplace live history | **FAIL → fixed** | P1 | EXEC-ENV-01, migration 079 |
| LT-05 | Watchdog does not hold idle transactions | **FAIL → fixed** | P1 | TXN-IDLE-01; 2 h 27 m idle-in-transaction observed |

## 8. Marketplace

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| MP-01 | Browse, my-strategies, subscriber analytics, creator payouts | PASS | | 200, own data |
| MP-02 | Publish / subscribe gated by plan | PASS | | Free: publish 0, subscribe 0 quotas |
| MP-03 | Listing eligibility needs 24 h of live, non-sandbox history | PASS (after 079) | | `.eq("is_sandbox", False)`; NULL excluded |
| MP-04 | Seller → subscriber end to end | BLOCKED | | No second identity (ENV-03); 0 public listings |

## 9. Dashboard and real time

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| DB-01 | `/api/dashboard`, overview, strategies | PASS | | 200 |
| DB-02 | Crawler render of all app pages, no console errors | PASS | | Playwright crawl |
| DB-03 | WebSocket telemetry | PASS* | P3 | Connects; Sentry shows occasional 1005 closes |

## 10. Billing (test mode) and cancellation

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| BL-01 | Plans, entitlements, invoices, payment methods, currency | PASS | | 200. Localised ₹499 → $5.34 is correct. The top-level `fx_rate: 1.0` is misleading metadata (P3 FX-META-01). |
| BL-02 | Webhook unsigned / forged | PASS | | 403 (IP allowlist); HMAC behind it |
| BL-03 | Test-mode checkout | BLOCKED | | Production deliberately refuses `rzp_test_` keys; real charges are forbidden |
| BL-04 | Cancel needs MFA | NOT APPLICABLE | | By design (SEC-15): stopping billing is never MFA-gated; resume is |
| BL-05 | Cancel with no paid subscription | **FAIL → fixed** | P2 | 200 and status `cancelled` written on a free profile (BILL-CANCEL-01; state reverted) |

## 11. Support

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| SP-01 | Create ticket (both UI workflows share `createTicket`) | PASS | | 200, ticket c33d3685 `[QA-CERT]` |
| SP-02 | Secret in ticket body refused | PASS | | 400 |
| SP-03 | Validation | PASS | | 422 |
| SP-04 | List / view / comment / close | PASS | | 200; close raised a notification |
| SP-05 | Staff routes as a customer; other tenant's ticket | PASS | | 403 / 404 |

## 12. Risk settings, margin health, allocations

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| RK-01 | Save and read back | PASS | | 250 / 5 / 2 persisted |
| RK-02 | Platform ceiling clamp (max positions 5) | PASS | | Requested 7 → 5 (RISK-03) |
| RK-03 | Invalid values | PASS | | 422 ×4 |
| RK-04 | Partial save keeps other fields | **FAIL → fixed** | P2 | 250 → 500 (RISK-PUT-01) |
| RK-05 | Each change notifies | **FAIL → fixed** | P2 | NOTIF-RISK-01 |
| RK-06 | Margin / account health consistent with guard | **FAIL** | P2 | account-health exposure 0.0 while guard reports 80.14 % utilisation (MARGIN-01, open) |
| RK-07 | Capital allocations (internal) | PASS | | 403 to customers |
| RK-08 | Max Daily Loss vs Daily Loss Limit duplication | PASS* | P3 | Two names across pages for one stored field (UX) |

## 13. Profile and referral

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| PR-01 | Profile read / update; platform columns guarded | PASS | | Guard trigger: 403 on tier, overrides, balance, add-ons |
| PR-02 | Referral code / profile / stats | PASS | | CLAUDEBDA7 |
| PR-03 | `?ref=` signup attributes | **FAIL → fixed** | P1 | REF-ATTR-01 |
| PR-04 | Payout request for another user | **FAIL → fixed** | P0 | SEC-DEF-01 |
| PR-05 | Referral E2E with a second identity | BLOCKED | | ENV-03 |

## 14. Notifications

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| NT-01 | List, unread count, mark read | PASS | | 3 → 2 unread |
| NT-02 | Other tenant's notification read / delete | PASS | | 404 |
| NT-03 | No email to real users during the audit | PASS | | SMTP broken anyway; no sends triggered |

## 15. Security and observability

| ID | Test | Result | Sev | Evidence |
|---|---|---|---|---|
| SEC-01 | RLS enabled on all tables | PASS | | 90 / 90 |
| SEC-02 | SECURITY DEFINER RPCs check the caller | **FAIL → fixed** | P0 | SEC-DEF-01 |
| SEC-02b | Repository-provisioned schema: profile insert and DB-credentialed RPC calls after 078 | **FAIL → fixed** | P1 | 239 CI-lane failures (all `SEC-DEF-01` from `trigger_create_referral_code`) → migration 080 → lane 1,399 pass; 5 remaining fail identically on the pre-078 schema |
| SEC-03 | Admin / internal routes as a customer | PASS | | 28 × 403 |
| SEC-04 | Invalid timeframe / unknown copilot session | FAIL | P3 | 500 instead of 422 / 404 |
| SEC-05 | Supabase advisor warnings | FAIL | P3 | 34 mutable `search_path`; leaked-password protection off; GraphQL exposed |
| OBS-01 | Sentry ingestion | PASS | | Audit probes visible within seconds |
| OBS-02 | CloudWatch | BLOCKED | | ENV-05 |

## Suites

| Suite | Result |
|---|---|
| Frontend vitest (198 files) | 4,056 / 4,056 pass after a fresh `npm run build`. The one failure seen first was the dead-tailwind guard reading a stale `dist/` from Nov 15. |
| Backend pytest (targeted, this audit's areas) | quota 1,016, risk 550, billing 955, support / notifications 113: all pass |
| Backend pytest (full) | see `RELEASE_SIGNOFF.md` |
| PG-gated, every file the CI workflow names (local PG16, fresh provision with 078–080) | 1,399 passed, 5 failed. All 5 fail identically on the pre-078 schema and are CI-deselected or pre-existing. |
