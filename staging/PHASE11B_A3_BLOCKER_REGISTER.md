# PHASE 11B-A3 — BLOCKER REGISTER (RECONCILIATION INDEX)

**This is not a new register and does not define readiness.** The authoritative finding
register is `staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md` + `PHASE11_READINESS_EVIDENCE.json`
(`P11-*` IDs); the authoritative per-track gates are `staging/PHASE11A_LAUNCH_GATE_REGISTER.md`.

A3 adds exactly two things:

1. **§1 — contradictions** between existing registers that must be resolved before any
   scorecard is trusted. These are the highest-value output of this baseline.
2. **§3 — A3-new findings** with fresh `A3-*` IDs, for things no existing register carries.

Everything else is a pointer. **No existing status was changed by A3.**

Severity: **P0** blocks any launch · **P1** blocks the stated track · **P2** should fix.
Status: VERIFIED · PARTIAL · BLOCKED · FAILED · NOT CLASSIFIED.

---

## 1. Contradictions between registers — RESOLVE BEFORE TRUSTING ANY SCORECARD

Each row is the same finding with incompatible statuses in different files. Until resolved,
any aggregate status is unsound.

| # | Finding | Register A | Register B | A3 assessment | Owner |
|---|---|---|---|---|---|
| **C-1** | **MONEY-04** — Razorpay INR credited as USD, ~83× overstated | `FINAL_LAUNCH_SCORECARD.md`: **`OPEN (P0-class), not re-measured in Phase 7`** | `PHASE11A_READINESS_SCORECARD.json`: `fee_and_money_integrity: "MET"`, *"MONEY-04 closed Phase 8"*; `PHASE11_READINESS_EVIDENCE.json`: `RISK-15 verified`, *"that claim is stale"* | **Likely FIXED** (two independent later registers agree, citing `billing.py:564-780` `_exact_minor_units`). Scorecard is stale. **Not independently re-verified by A3.** A P0-class money finding must not be left contradictory. | Billing owner |
| **C-2** | **CONN-01** — pool key omits the credential | `FINAL_LAUNCH_SCORECARD.md`: `P2 … REPORTED (new, Phase 7)` | `PHASE11A_LAUNCH_GATE_REGISTER.md`: **`MET (fixed Phase 10)`** | **FIXED**, at two layers, but **the fix is uncommitted** (`routers/exchange.py` `MM`, `connection_engine.py` ` M`, tests `??`). Scorecard stale. | WS-C |
| **C-3** | **PAPER-01** — BotRunner paper fills never reach `paper_*` tables | `FINAL_LAUNCH_SCORECARD.md` classification block: **`PAPER-01: CLOSED`** | `PHASE11_READINESS_EVIDENCE.json`: `PERS-11 defect` → **`P11-H19`**, and listed as a **paper-launch gating condition** | **Unresolved and material** — this is the difference between paper-launch GO and NO-GO. Two registers disagree on a gating item. | Paper-trading owner |
| **C-4** | **TRADE-01** (capital check TOCTOU) and **TRADE-02** (kill switch self-expires) | `FINAL_LAUNCH_SCORECARD.md`: both **`OPEN (P1)`** | `PHASE11_LIVE_TRADING_READINESS_AUDIT.md` §2.4: both **FIXED**, TRADE-01 verification PG-gated/skipped, TRADE-02 *36 tests passed* | TRADE-02 **FIXED+verified**. TRADE-01 **FIXED in code, UNVERIFIED** (= `P11-U01`). Scorecard stale on both. | Risk owner |
| **C-5** | **WORK-03** — cross-pod duplicate `start_bot` | `FINAL_LAUNCH_SCORECARD.md`: **`FIXED`**, 39 tests incl. 2- and 4-process races | `PHASE11_READINESS_EVIDENCE.json`: `CONC-05` **`unverified`/critical** → **`P11-U03`** (*"`test_work03_bot_ownership_claims_postgres.py` skipped"*) | **Both true, and the gap is the point:** fixed and once proven under real PG, but **not reproducible today** because the PG lane never runs. Classify `PARTIAL`, not `FIXED`. | Platform |
| **C-6** | **EXCH-01** — **ID collision** | `PHASE11A` §B: `EXCH-01 bitget` **`MITIGATED`** (P0) | `PHASE11_READINESS_EVIDENCE.json`: `EXCH-01 = "Exchange library exactly pinned"` **`verified`** | **Two different findings share one ID.** Must be renamed before either is reported. | Coordinator |
| **C-7** | **TEST-05** — **ID collision** | `FINAL_LAUNCH_SCORECARD.md`: **`FIXED`** — Hypothesis non-vacuity census defect | `PHASE11_READINESS_EVIDENCE.json`: `TEST-05 = "Production verification scripts have run against production"` **`defect`/critical** → `P11-B10` | **Two different findings share one ID**, one FIXED and one CRITICAL. Highest-risk collision in the corpus. | Coordinator |
| **C-8** | **Paper-track verdict** | `FINAL_LAUNCH_SCORECARD.md`: **`PAPER-ONLY: NO-GO`** | `PHASE11_READINESS_EVIDENCE.json`: `paper_only_launch: "CONDITIONAL"` (6 named conditions); `PHASE11A`: paper **53 %** evidence-backed | Three different answers for one track. **A3 records the paper track as NOT CLASSIFIED** until reconciled. | Coordinator |
| **C-9** | **TENANT-02** / `fee_records` RLS | `FINAL_LAUNCH_SCORECARD.md`: `OPEN (P1, NEW)` | `PHASE11_READINESS_EVIDENCE.json`: `P11-U06` — migration **written, untracked, unapplied** | Consistent in substance. **The migration is one of 11 untracked SQL files** (§6 of the baseline) — so it is neither applied nor reviewable in CI. | Security + DB |
| **C-10** | **QUOTA-01** real-Redis | `PHASE11A`: `QUOTA-HARD-LIMIT` **`MET (logic) / UNVERIFIED (real Redis)`** | `PHASE11_READINESS_EVIDENCE.json`: **`P11-U04`** — *"the production atomic path has never executed because `MockRedisClient.eval_lua` ignores the script"* | Consistent. `P11-U04` is the stronger statement and should be the one quoted: it is not merely unverified, the **Lua script has never run**. | WS-C / platform |

---

## 2. Pointers — the P0/P1 set, unchanged, with today's evidence state

No status here is A3's; each cites its register. Added column: **can it be verified today?**

### P0 — blocks any launch

| ID | Finding | Register status | Verifiable today? |
|---|---|---|---|
| `P11-B06` | ALB/container health checks cannot drain a dependency-broken task | critical, ops | **Yes** — repo-only, 0.5 d |
| `P11-B08` / **A3-01** | No **test** gate between a push to `main` and production deploy | critical, ops | **Yes** — see A3-01 for the exact mechanism |
| `P11-B07` | Zero alarms, metrics filters, SNS topics, backup resources; no restore drill | critical, ops | Partly — Terraform is in repo; a drill needs a non-prod target |
| `P11-B09` | RLS inert on 9 execution tables; no tenant context on any request path (= **TENANT-01**) | critical, security | **Only with the PG lane** → needs `AERORA_TEST_DATABASE_URL` |
| `P11-B01` | **No venue has ever received an order.** 0/12 scenarios, 0 sandbox orders | critical, exchange | **No** — needs testnet credentials |
| `P11-B02` | `clientOrderId` venue mapping unverified — **sole** duplicate-order defence | critical, execution | **No** — needs credentials (scenario 8, run first) |
| `P11-B03` | Daily-loss and drawdown **not enforced at the order gateway** | critical, risk | **Yes** — code change + tests |
| `P11-B04` | Authoritative equity never reconciled against venue balance | critical, risk | **No** — depends on B01/B03 |
| `P11-B05` | Restart recovery does not rediscover resting venue orders (= **EXCH-07**) | critical, persistence | Partly — code now, proof needs a venue |
| `P11-B10` | Production schema never verified against the repository | critical, persistence | **No** — needs authorized read-only production access |
| **RECON-01** | Venue↔internal reconciliation **not operational**; `ExchangeReconciliationService` has **zero callers**, **no leader election**; the scheduler that *is* running polls a table **no detector writes** | P1 paper / **P0 live** | Partly — leader-election + 2-process test now; venue proof later |
| **EXCH-01** (bitget) | `set_sandbox_mode` inert → sandbox flag routes to production | **MITIGATED** (4 independent blocks) | **Yes** — already verified, 50 tests |

### P1 — blocks the stated track

| ID | Finding | Register status | Verifiable today? |
|---|---|---|---|
| `P11-U01..U07` | 7 **unverified critical controls** — capacity reservation, idempotency DDL, bot ownership, real-Redis quota/Lua, safety audit, `fee_records` RLS, position lifecycle | implemented, **zero executed evidence** | **Yes for all 7** — root cause is the test lane, ~1 engineer-day |
| `QUOTA-01` | Real-Redis unverified (`D5′`), atomic release (`D9`), production over-limit population (`D3`), DB backstop (`D2`) | **OPEN** | D5′ **yes in CI**; D9 yes; D3 needs authorization; D2 needs a decision |
| `AUTH-03` | `require_aal2` on exactly two handlers; absent from billing checkout, verify-payment, payment-methods, referral payouts, library routes; `/kill-all` password-only | `OPEN` / `P11-H10` | **Yes** |
| `EXCH-02` | `exchange_class(config)` outside the `try` → raw ccxt `NotSupported` bypasses `_handle_ccxt_error` | OPEN | **Yes** |
| `EXCH-08` | Venue-reported fees extracted then **dropped**; PnL uses a flat `fee_rate=0.001` | OPEN | **Yes** |
| `EXCH-12` | WebSocket fill listener **never started**; 4 reconnect defects; calls `get_or_create_exchange` with no credentials for a private channel | OPEN | **Yes** |
| `MKT-01` | Partial refunds record nothing; a partially refunded buyer keeps full access | OPEN (requirement gap) | Needs a product decision |
| `TRADE-08` | Idempotency key is **time-boxed by a 5-minute bucket** — a late retry becomes a new venue order | `OPEN`, **carried by no Phase 11 ID** | **Yes** — and it compounds `P11-B02` |
| **OKX ccxt defect** | `parse_market` `TypeError` on a pre-open instrument breaks `load_markets` on the pinned 4.3.92; fixed in 4.5.85 | OPEN, dependency triage | Needs dependency approval + full regression |

---

## 3. A3-new findings

| ID | Sev | Component | Finding | Evidence | Acceptance test | Owner | Status |
|---|---|---|---|---|---|---|---|
| **A3-01** | **P0** | CI / release | **The release gate tests security, not correctness.** `push → 02 Build → (workflow_run) → 03 Deploy`. `pre-deployment-validation` gates fail-closed on **`05 Security`** only (`scripts/require_security_scan.py`). It never consults `01 PR Check`, and `01 PR Check` triggers on **`pull_request` only** — so a direct push to `main` produces no test run for the gate to find. | `.github/workflows/0{1,2,3}-*.yml` parsed; triggers quoted in the baseline §5/§7 | A commit pushed to `main` with a failing suite must **not** reach `ecs-deployment` | SRE + coordinator (shared workflow) | **VERIFIED (defect confirmed)** |
| **A3-02** | **P0** | CI | **The host job for every gated lane is already red.** `Unit & Integration Tests` has **39 FAILED** tests and **0 of the last 10 runs succeeded**. Any future per-test verification (D5′, PG lane) must be read per-test, because the job colour cannot become green on its own. | `gh run view 37346853502`; evidence in `staging/p11b_a2_unit_job_failure.txt` | The 39 failures triaged to named owners; job green or every failure classified | Per-area owners | **VERIFIED (defect confirmed)**, root causes **NOT CLASSIFIED** |
| **A3-03** | **P1** | Release integrity | **A large share of the remediation is on no remote ref.** 132 untracked + 98 unstaged paths, including **11 numbered SQL migrations (022–032)**, `tests/exchange_sandbox/`, the entire D5′ set, and ~100 test files. CI cannot see any of it; several "FIXED" statuses are unreviewable. | `git status` 252 entries; `git ls-files` per path | An authorized, workstream-scoped commit plan exists and lands the fixes in reviewable units | Coordinator | **BLOCKED** (needs commit authorization) |
| **A3-04** | **P1** | Register integrity | **Ten register contradictions (§1), including two ID collisions** (`EXCH-01`, `TEST-05`) where one ID carries both a FIXED and a CRITICAL finding, and three different verdicts for the paper track. | §1 of this document | One register owns each ID; no ID carries two findings; one verdict per track | Coordinator | **VERIFIED (defect confirmed)** |
| **A3-05** | **P2** | DB / migrations | **Alembic head not resolvable from source by this probe** (0 heads computed over 12 revisions, implying a form my scan missed). Combined with `P11-U02` (the `execution_records` uniqueness DDL **never read**), the migration graph is not currently established. | `staging/p11b_a3_baseline_probe.txt` §2 | `alembic heads` returns exactly one head against a provisioned test DB, and the uniqueness constraint is read and tested | DB owner | **PARTIAL** |

---

## 4. What A3 deliberately did **not** do

* Did not change any existing status in any register.
* Did not create a competing readiness definition or a blended percentage.
* Did not edit application code, `tests/conftest.py`, `01-pr-check.yml`, or another
  workstream's files.
* Did not duplicate `staging/PHASE11B_A3_CI_RUN_EVIDENCE.md`, which a concurrent tab already
  produced for the D5′ CI question (decision: **UNVERIFIED**).
* Did not re-verify C-1 (MONEY-04). A P0-class money finding with contradictory statuses
  needs its owner, not a fourth opinion from a baseline pass.
