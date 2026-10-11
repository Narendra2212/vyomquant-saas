# Phase 27 — Frontend Incident Closure and Continuous Release Recovery

**Destination verified ABSENT before writing.** **0 production files · 0 test files ·
0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 governance records · 0 commits ·
0 pushes · 0 staged entries · 0 CI dispatched · 0 deployments · 0 stashes mutated.**
1 new file: this report.

> ## DISPOSITION
> **DO NOT RELEASE · INCIDENT OPEN · DEPLOYMENT BLOCKED**
> **LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Headline result — the CI-only failure is diagnosed, and it is not what it looked like

Phase 26 retrieved the failing test's **name** but not its traceback, and recorded the failure
as *"CI-environment-dependent and not reproducible"*, with a Hypothesis falsifying example as
the leading hypothesis. **This phase retrieved the full traceback. That hypothesis was wrong.**

```
tests/property/test_tenant_isolation_matrix.py:2738:
    assert not failures, (
E   AssertionError: Requirement 21.8: 4 of the 150 attempted cells failed one of the
E   three assertions.
```

**4 of 150 cells failed — two endpoints, two assertion types each:**

| Endpoint | Recorded (pinned) answers | Observed in CI |
|---|---|---|
| `POST /api/library/{library_id}/checkout` | `('409 MARKETPLACE_LISTING_NOT_PURCHASABLE reason=not_published', '404 …')` | `('403 Marketplace subscriptions start with Trader.', '403 …same…')` |
| `POST /api/library/{library_id}/clone` | `('404 Strategy not available for cloning.', '404 Library strategy not found.')` | `("403 You've reached your Free capacity for active strategies.", "403 …same…")` |

Plus, for both endpoints, the test's **anti-vacuity control** fired:

> *"the OWNER of the record is answered exactly what an identifier that exists in no tenant is
> answered, so the equality this cell asserts would hold for a reason other than tenant
> isolation."*

### 1.1 Root cause, confirmed in source

`backend_app/routers/library.py:2340-2344`:

```python
async def clone_strategy(
    ...
    _strategy_quota=Depends(check_strategy_quota),
```

`check_strategy_quota` is a **FastAPI dependency**, so it executes **before the handler body** —
and therefore before the ownership/existence lookups that raise `"Library strategy not found."`
(`:2437`) and `"Strategy not available for cloning."` (`:2445`). The checkout endpoint has the
same shape against a subscription-tier gate (`:2359`: *"rules say marketplace SUBSCRIPTIONS
start with Trader"*).

**When the test user is at the Free active-strategy cap, or lacks the Trader tier, the
entitlement gate answers first — identically — for the owner's identifier, a foreign
identifier, and an identifier that exists in no tenant.** The "foreign is indistinguishable
from absent" property then holds *vacuously*, and the test correctly refuses to accept it.

**The ordering is deliberate, and I am not proposing to change it.** `library.py:2352` records
why: *"`check_strategy_quota` closes a real bypass: a clone writes a `strategies` row the
caller…"*. Moving the quota check after the ownership lookup would reopen that bypass.

### 1.2 What this is, and what it is not

| Claim | Verdict |
|---|---|
| A cross-tenant data leak | **NO.** Nothing of another tenant was returned. All identifiers are denied identically. |
| A Hypothesis falsifying example | **NO.** Deterministic given entitlement state; no `Falsifying example` appears anywhere in the log (searched: 0 occurrences). |
| A flaky/random-ordering failure | **NO.** |
| **A test-validity failure caused by entitlement state** | **YES.** The matrix cannot *observe* the ownership decision for these 2 of 150 cells while the gate short-circuits. |
| **An environment difference between CI and local** | **YES**, and now explained: CI's test user has hit the Free cap / lacks Trader; locally it has not. |

**Why Phase 26's two local runs passed** is now explained: entitlement fixture state, not the
Hypothesis corpus. Phase 26's corpus experiment was a reasonable control that correctly ruled
out the wrong hypothesis.

### 1.3 Consequence for certification

**Tenant isolation remains NOT CERTIFIED**, and the reason is now precise rather than vague:
148 of 150 cells are evidenced, and **2 cells are unobservable** because an entitlement gate
masks the ownership decision. That is a genuine coverage hole in the isolation proof for
`POST /clone` and `POST /checkout`, independent of whether the product behaviour is safe.

**The fix is in the test fixture, not the router** — grant the matrix's probe user sufficient
entitlement (Trader tier, headroom under the strategy cap) so the ownership branch is reached
and the cell becomes observable. That is a **test-file change and is NOT GRANTED** (§3).
**I did not weaken, skip, xfail, de-scope or delete the property.**

---

## 2. Entry and exit integrity manifest

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Branch · divergence | `main` · **0/0** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| Staged · unmerged | 0 · 0 | **0 · 0** |
| Files differing from `HEAD` | **2** | **2 — the same two** |
| Worktrees · stashes | 1 · **2** | 1 · **2 — untouched** |
| Porcelain | 139 | 140 (+1 = this report) |

Tree hash identical at entry and exit, and to Phases 17, 21, 22, 23 and 26. **No tracked file
has changed across Phases 17 → 27.**

**Concurrent modifications — hashes recorded, unchanged:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` = `0c9981b2817e47b4faa915b69de204ff78f87af7`;
`tests/test_drift_classification.py` = `99a2917ac0a65761ef3dfe9299ed1df87647552a`. **Neither touched.**

**Target hashes captured before inspection** (none edited):
`tests/property/test_tenant_isolation_matrix.py` = `a60cd00c4558b386de61e045b08bf9b5ebfaf69d`;
`.github/workflows/06-frontend-deploy.yml` = `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9`;
`scripts/require_security_scan.py` = `a1c8dd2d48fbc744301e95fc99429e4e1efd35da`.

**Stashes:** `stash@{0}` = `filter-branch: rewrite` backup (not a stash); `stash@{1}` =
`99ceb9b0…`, 18 files, orphaned pre-rewrite WIP. **Neither applied, dropped, popped or renamed.**

**Tooling rechecked:** `gh` authenticated · `bandit` 1.9.4 / Python 3.12.10 · **`trivy` ABSENT**
· **`docker` ABSENT** · Redis 6379 **CLOSED**, env unset · PostgreSQL **51815 OPEN** (shared
`pg_dsn.txt` says 51805 — **not edited**) · AWS / Terraform state **no access**.

---

## 3. Authorization matrix

| WS | Targets | Grant | Owner | Ops used |
|---|---|---|---|---|
| **A** tenant matrix test | `tests/property/test_tenant_isolation_matrix.py` | **NOT GRANTED** | code owner | read-only + 1 local test run |
| **B** incident evidence | S3 / CloudFront / DNS | **NOT GRANTED** | release owner | read-only CI queries |
| **C** frontend gate | `06-frontend-deploy.yml`, `tests/` | **NOT GRANTED** (`D-P15-4` is a *request*) | workflow/release owner | read-only |
| **D** verifier | `scripts/require_security_scan.py` | **NOT GRANTED** (`P22-B1`) | security owner | read-only |
| **E** image provenance | `02/03-*.yml`, deploy script, `terraform/ecs.tf` | **NOT GRANTED** | release + infra owner | read-only |
| **F** security evidence | `05-security.yml` | **NOT GRANTED** (`P23-K1`) | security owner | read-only |
| **G** Bandit parity | gate test, 3 migrations | **NOT GRANTED** | code owner | read-only |
| **H** D7-1 | `routers/{market,orders}.py` | **NOT GRANTED** (`D-13-1`) | code owner | read-only |
| **I** D6-D → D7-2 | `core/state.py`, updater | **NOT GRANTED** | code owner + platform | read-only |
| **J** tenant identity / RLS | `core/tenant_db_context.py`, policies | **NOT GRANTED** | code + DB owner | read-only |
| **K** W-6 / Redis / workers | classifier, quota tests | **NOT GRANTED** | code / infra owner | read-only |
| **L** Terraform / Trivy | `terraform/**` | **NOT GRANTED** | security + infra owner | read-only |

**Files actually changed: none.** Ledger rows `:800-804` (`AP-W1D-1` … `AP-W1D-5`) all
**`NOT GRANTED`**; no row covers a workflow, script, test or Terraform path. `AP-W1D-3` remains
confined to its own classifier files.

**Per §2.2 of the brief:** the copy-ready `"I authorize…"` templates I drafted in Phase 26 §11
are **requests, not grants**, and are not treated as authorization here. No governance record
was edited.

---

## 4. Workstream A — evidence retrieval and reproduction

### 4.1 Retrieval (NEW — the gap Phase 26 left open)

| Attempt | Result |
|---|---|
| `gh api /actions/jobs/114242427268/logs` | **0 bytes** — the endpoint returns a redirect `gh api` does not follow to a file |
| `gh run view 38061973739 --log-failed` | **13,312 lines** — contains the full `FAILURES` section (§1) |

Marker census in the retrieved log: `FAILURES` **28** · `Falsifying example` **0** ·
`short test summary` **0** · `Process completed with exit code` **0**. The absent summary is why
Phase 26's search missed the traceback — pytest's `FAILURES` block is present, but the
trailing summary line is not within the retained portion.

**The job and its log are still accessible; nothing has expired.** Failing step: `Run Unit
Tests` (step 6) of job `114242427268`.

### 4.2 Reproduction status

| Run | Command | Result |
|---|---|---|
| Phase 26 #1 | `-p no:randomly --tb=short -q` | 1 passed, 37.89s, exit 0 |
| Phase 26 #2 | fresh `HYPOTHESIS_DATABASE_FILE`, `-o addopts=""` | 1 passed, 47.41s, exit 0 |
| **Phase 27** | **no new local run** — the mechanism is now known from the traceback and source; repeating an identical command with identical inputs adds no diagnostic information (brief §15) | **NOT RUN, by design** |

**The failure is now understood without local reproduction.** Reproducing it would require
putting the local fixture user at the Free active-strategy cap and below Trader tier — which
means **editing the test fixture**, and that is ungranted. The diagnosis rests on the CI
traceback plus source confirmation of the dependency ordering, which together are sufficient.

### 4.3 Fix specification (NOT IMPLEMENTED — ungranted)

1. In the matrix fixture, provision the probe owner with **Trader tier** and **headroom under
   the active-strategy cap**, so `POST /clone` and `POST /checkout` reach their ownership
   branch and the two cells become observable.
2. Keep the pinned `REQUIREMENT_21_4_GAPS` entries; re-record them **only** once the ownership
   branch is actually reached. The test's own guidance applies: *"If the two answers are now
   identical, delete the entry from `REQUIREMENT_21_4_GAPS`"* — which must not be done while the
   identity is an entitlement 403.
3. **Do not reorder** `check_strategy_quota` relative to the ownership lookup — `library.py:2352`
   documents that ordering as closing a real write-bypass.
4. **Preserve the anti-vacuity control.** It is the reason this defect was caught at all.
5. Add a deterministic regression case asserting that, **with sufficient entitlement**, the
   owner's answer differs from the absent-identifier answer on both endpoints.
6. Negative control: with entitlement deliberately exhausted, the anti-vacuity control must
   still fire.

**Not done and not proposed:** removing the property, lowering its assertions, narrowing its
strategies, excluding it from CI, marking it advisory, or converting it to xfail.

---

## 5. Workstream B — incident evidence and owner decision

Evidence re-confirmed; **no new measurement was required** (CI history for this run is
unchanged since Phase 26, and the run remains accessible).

| Item | Value |
|---|---|
| Published SHA | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| Publishing run | `38129699497`, conclusion `success`, `2026-10-11T09:48:45Z` → `09:50:13Z` |
| Bundle filename | `index-EYjpdKQA.js` |
| Bundle SHA256 (full) | `6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312` |
| Bundle size | 509,689 bytes |
| Upload | 156 files, ~3.7 MiB → `s3://vyomquant-frontend/` |
| Build→deploy integrity | **VERIFIED** — `Deployed Bundle SHA256` re-derived and equal |
| Retained artifact | **NONE** — `/artifacts` returns `total_count = 0` |
| Superseded since | **No** — newest of 60 `06 Frontend Deploy` runs |
| Same-SHA `05 Security` | **failure** ×2 |
| Same-SHA `01 PR Check` | **never ran** |

**Rollback-candidate caveat, unchanged and load-bearing:** `6c5660a1` (security **green**) and
`eee73265` (security **red**) published **byte-identical** content, SHA256
`13dd072f9178e0d7ebeaa4b738e402e854d3c4f6de72fe39b99904a5d295620f`. Selecting the last
green-security SHA is therefore **not** selecting a frontend-security-verified artifact.
`6c5660a1` also **failed** `01 PR Check`.

**Scope of what `05 Security` inspects** — Bandit on `backend_app`, Trivy on the container
image, Trivy on IaC. **It does not inspect frontend JavaScript.** Its failure is therefore not
evidence of a frontend defect, and its passing would not have been evidence of frontend safety.
The checks that *do* examine the bundle — artifact plausibility, Supabase config, API-host
allow-list, CSP/header, WebSocket origin — **all passed**, and all run **before** the S3 sync.

**UNKNOWN:** current CloudFront edge bytes; whether anything else wrote to the bucket since.
**S3/CloudFront were not inspected — unauthorized.**

**Owner decision: still PENDING. No decision record exists.** No rollback, republish, workflow
freeze, S3/CloudFront/DNS operation or asset mutation was performed; none is authorized. The
decision request remains as drafted in Phase 26 §11.1, with the artifact identities above.

**The incident is NOT closed.** A correct future gate does not close it — these are separate
matters, per the brief.

---

## 6. Workstreams C–F — release controls (read-only)

| WS | Current state | Status |
|---|---|---|
| **C** frontend gate | `06-frontend-deploy.yml` (hash `285cc2d1…`): triggers `push [main, master]` + `workflow_dispatch`; **`needs:` count = 0**; AWS credentials configured at step 11; S3 sync steps 12/`:289`/`:299`/`:307`; CloudFront invalidation `:329`. **No gate on any path; `workflow_dispatch` equally ungated.** | **NOT IMPLEMENTED — ungranted** |
| **D** verifier | `require_security_scan.py` (hash `a1c8dd2d…`): preserves exact-SHA binding, missing-scan refusal, `cancelled`/`skipped` refusal, API-error refusal, newest-run selection, bounded wait, and **`ALLOW_UNSCANNED` cannot override a failed scan**. **All preserved; nothing weakened.** Gap: reads workflow conclusion only. Measured jobs at `ecd23a9e` — Container **success**, Bandit **failure**, IaC **failure** — so its docstring claim that the workflow *"is red only for CRITICAL findings"* is **inaccurate**. Pagination remains **latent** (`total_count = 6` vs `per_page=100`). | **NOT IMPLEMENTED — ungranted** |
| **E** image provenance | `02-build.yml:72` builds `:$IMAGE_TAG` **and** `:latest`; `:75-76` pushes both **before any gate**; `:78` `… \|\| echo "$IMAGE_TAG"` **substitutes the tag for a digest**. `03-deploy.yml:168-173` `… \|\| echo ""` then `exit 1` — **genuinely fail-closed, PRESERVED**; `:174` **echoes** the digest only; `ecs_deploy_and_diagnose.py:901` builds the URI from **`IMAGE_TAG`** → **deploys by tag**. No `aws_ecr_repository` in any Terraform root → **tag immutability UNKNOWN, not assumed**. | **NOT IMPLEMENTED — ungranted** |
| **F** security evidence | `05-security.yml` has **no `continue-on-error`** → its steps are blocking. Two independent blocking IaC scans (`scan-ref: terraform`, `scan-ref: aerora_quant_platform/infrastructure`) plus a non-blocking CRITICAL/HIGH/MEDIUM report. Proposal already filed as `P23-K1`; referenced, not duplicated. Safety mandatory-vs-advisory **undecided**. | **NOT IMPLEMENTED — ungranted** |

**Mandatory-job inventory was not invented.** The factual inventory measured at `ecd23a9e` is:
`Trivy Container Vulnerability Scanner`, `Bandit SAST & Dependency Security Scan`,
`Trivy IaC Misconfiguration & Secret Scan`. Which of these are *mandatory* is an owner decision.

---

## 7. Workstreams G–L — backend, tenant, infrastructure (read-only)

| WS | State | Status |
|---|---|---|
| **G** Bandit | CI `bandit -r backend_app -ll`, no `continue-on-error`. **9 × B608 MEDIUM** in 3 alembic migrations. **bandit 1.9.4 / Python 3.12.10** (rechecked). Gate test scoped to **one file**; AST companion proves validator-constrained identifiers — a **different property** than B608 detects. All 9 reviewed: validator-constrained identifiers or module literal constants, values parameter-bound. **9 structurally safe · 9 still scanner findings · 0 suppressed · 0 owner-accepted.** Scan **NOT re-run** — command, source and version unchanged. | **NOT GRANTED** |
| **H** D7-1 | Three invalid `access_token=` args to `load_decrypted_keys(self, user_id, exchange_id)`: `orders.py:792` (**unguarded → 500**), `orders.py:1098` (`except` → `return []`), `market.py:53` (`except` → **unauthenticated public fallback**). `routers/exchange.py` **correct — unchanged**. Legitimate at `risk.py:313,325,407` and `paper_trading.py` ×8 — **no global replacement**. `market.py:56` keys-absent guard **preserved**; policy **not invented**. | **NOT GRANTED** |
| **I** D6-D → D7-2 | `APIKeyVault.__init__(self)` takes **zero args** but **fail-fast raises** `EnvironmentError`. A naive `KeyVault`→`APIKeyVault` swap raises **inside the same `try`** — identical user-visible outcome. Correct target is the initialized `app_state.vault`, which per D6-D is a `MockEngine`. **D7-2 must not precede D6-D.** No second vault; no per-cycle key derivation. | **NOT GRANTED** |
| **J** tenant identity / RLS | `apply_tenant_context` has **zero external consumers**. Three competing identity expressions (`dependencies.py:318`, `orders.py:819/940`, `distributed_execution.py:296`) agree only because no token carries a `tenant_id` claim — **owner choice still required**. Two **permissively OR-combined** policy families; `execution_records` predicate open. FORCE experiment REUSED: ENABLE+unset GUC → 2 rows; **ENABLE+FORCE+unset GUC → 0 rows silently**; FORCE+correct GUC → 1 row. **FORCE NOT enabled or prepared.** Local superuser result is **not** certification — local `postgres` is `rolsuper`/`rolbypassrls`. **New this phase: §1 identifies 2 of 150 matrix cells as unobservable**, which is an additional, concrete reason isolation is uncertified. | **NOT CERTIFIED** |
| **K** W-6 / Redis / workers | **10 of 20 asserted misclassified** (source hash unchanged → REUSED). `retryable` **log-only** — 15 production occurrences, **0 control flow**. **No retries, backoff, duplicate submission or order-control flow introduced.** Redaction intact. Redis env unset, 6379 **CLOSED**, no `redis-server`, no Docker → 5 tests **SKIP, not passes**; strict XFAIL preserved. `UNKNOWN` exit policy **not invented** (CAS pinned `'EXECUTING'`, `execution_record.py:1488`) → **W-5 behind W-4; W-3 behind W-6**. | **NOT GRANTED / BLOCKED** |
| **L** Terraform / Trivy | 4 unrestricted egress blocks: `security_groups.tf:22/52/73` + `redis-only/main.tf:58`. Redis SG names **identical by default**; **collision conditional on same VPC — UNVERIFIED** (main root *creates* `aws_vpc.main`; `redis-only` adopts hardcoded `vpc-0f3e7b3f243fbc133`). `redis-only` has **no backend block → local state**, 0 variables. **`AWS-0054` false positive** — `alb.tf` `dynamic` redirect + `lifecycle.precondition`; **compensating control preserved**. **Trivy ABSENT → NOT RUN. 0 of 5 accepted. No egress rule removed.** | **NOT GRANTED · NOT RUN** |

---

## 8. Verification results

| Check | Result |
|---|---|
| Entry/exit tree hash identical | **PASS** |
| Concurrent file hashes unchanged | **PASS** |
| Stashes / worktrees intact | **PASS** (2 · 1) |
| Destination absent before writing | **PASS** |
| CI traceback retrieved | **PASS** — §1 |
| Tenant-isolation failure mechanism identified | **PASS** — §1.1, source-confirmed |
| Local reproduction of the CI failure | **NOT RUN** — mechanism known; reproduction needs an ungranted fixture change (§4.2) |
| Tenant-isolation property + siblings re-run | **NOT RUN** — unchanged inputs; Phase 26 result reused |
| Aggregate pytest | **NOT RUN** — nothing was modified |
| Release-gate test discovery | **N/A** — no gate test exists under `tests/`; the Phase 17 prototype is in `staging/`, outside `pytest.ini`'s `testpaths = tests` |
| Bandit at CI scope | **NOT RUN** — unchanged command/source/version; prior findings reused |
| Trivy / Terraform validate | **BLOCKED** — `trivy` and `docker` absent |
| Every mandatory gate checks the same release identity | **FAIL** — frontend has no gate; verifier checks workflow-level only |
| Frontend cannot reach AWS credentials before a gate | **FAIL** — no gate exists upstream of step 11 |
| Digest propagation build → deploy | **FAIL** — digest echoed then discarded; deploy by tag |
| Diff inspected for regressions / scope creep | **PASS** — there is no diff |

**A missing executable is not a pass; no mock was presented as production evidence.**

---

## 9. Remaining blockers and the highest-value dependency

### Highest-value unresolved dependency

> **The release owner's frontend containment decision (§5).** It is the only blocker where a
> build is **live** and unverified, it needs no engineering work, and every other lane can
> proceed independently of it.

### Owner decisions required

| # | Decision | Owner |
|---|---|---|
| **1** | Frontend containment: rollback vs forward-fix (§5) — plus separate execution authorization if rollback is chosen | **release owner** |
| **2** | Grant for the matrix fixture change (§4.3) — the only thing blocking 2 of 150 isolation cells | **code owner** |
| **3** | Tenant identity: choose **one** of three expressions (§7 WS-J) — costs no code, unblocks all context wiring | **code owner** |
| **4** | `D-P15-4` frontend gate; `D-P15-3` correctness route; whether ungated builds may advance `:latest` | **workflow / release owner** |
| **5** | `P22-B1` verifier job-level checks + the mandatory-job inventory (§6) | **security owner** |
| **6** | `P23-K1` security-evidence retention; Safety mandatory vs advisory | **security owner** |
| **7** | All 5 Trivy CRITICAL dispositions; ECR repository ownership and tag-immutability policy | **security + infra owner** |
| **8** | `aws_vpc.main` identity; whether `redis-only` was applied and where its state lives | **infra owner** |
| **9** | D7-1; **D6-D before D7-2**; W-6; Bandit parity | **code owner** |
| **10** | D6-C 3 integers; D6-D 4 redacted boot fields; 1 claim-divergence integer | **DB / platform / auth owners** |
| **11** | `UNKNOWN` exit policy; isolated Redis; disposition of orphaned `stash@{1}` | execution-state / coordinator / code owners |

### Work that can continue independently of decision #1

Workstreams D, E, F, G, H, I, K, L are all independent of the incident decision and are blocked
**only** on their own grants. Nothing in this phase is blocked on another lane's completion.

---

## 10. Production evidence and release certification — separate statements

**Production evidence.** The only production-adjacent facts established are read-only CI
observations: a frontend publication occurred at `ecd23a9e`, its bundle identity is
`6f12b52c…4312`, and the workflow self-verified build→deploy byte integrity. **No production
system was inspected or mutated.** Current CloudFront edge state, S3 object state, ECR
repository policy, Terraform state and live ECS state are all **UNKNOWN**.

**Release certification.** **NONE.** No mandatory gate is evidenced as passing for release
identity `ecd23a9e`: correctness **never ran** (and 6 of 6 recent `01 PR Check` runs failed on
the matrix test diagnosed in §1), security **failed**, the frontend gate **does not exist and
was bypassed**, and image provenance is **not established**. The backend gate **worked** and
refused — that is the one control demonstrated effective end to end.

---

## 11. Unauthorized-operation checklist

| Prohibited | Performed? |
|---|---|
| Stage, commit, push, reset, clean, stash, pop, drop, merge, rebase, revert, switch branches, rewrite history, change worktrees | **No** — tree hash identical; 2 stashes at entry **and** exit |
| Dispatch CI, create PRs, modify branch protection or repository settings | **No** — all `gh` use was `list` / `view` / `GET`; branch protection not inspected |
| Roll back, republish, freeze workflows, modify S3/CloudFront, invalidate caches, change DNS, mutate cloud resources | **No** |
| Build, push, retag, promote or delete registry images | **No** |
| `terraform apply` / alter security groups | **No** |
| Execute production migrations; mutate production DBs, roles, grants or tenant policies | **No** |
| **Enable or prepare FORCE RLS** | **No** |
| Place exchange orders / enable live trading | **No** |
| Read or print credentials, tokens, keys, passwords, decrypted material or DSNs | **No** |
| Start/reconfigure shared services; edit `pg_dsn.txt` | **No** |
| Weaken the backend gate, suppress findings, remove egress rules, lower thresholds, add `\|\| true` | **No** — `03-deploy.yml` digest refusal, the ALB precondition, the XFAIL contract and the matrix anti-vacuity control all **preserved** |
| Weaken, skip, xfail or de-scope the tenant-isolation property | **No** — diagnosed, not disarmed |
| Overwrite concurrent reports, untracked files, stashes, worktrees or evidence | **No** |
| Claim a skipped/unexecuted test or absent scanner passed | **No** — Trivy/Docker **ABSENT → NOT RUN**; 5 Redis tests are **skips**; unchanged-input suites marked **NOT RUN** |
| Claim tenant isolation certified | **No** — **NOT CERTIFIED** |

---

*Phase 27 closed. Tree hash unchanged at entry and exit. Both stashes untouched. 0 of 12 lanes
authorized; 0 files changed. Principal new result: the CI-only tenant-isolation failure is fully
diagnosed — 4 of 150 cells, two endpoints, caused by an entitlement gate executing ahead of the
ownership check as a FastAPI dependency, with the test's anti-vacuity control correctly refusing
a vacuous pass. Phase 26's Hypothesis hypothesis is withdrawn.*

***DO NOT RELEASE · INCIDENT OPEN · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.***
