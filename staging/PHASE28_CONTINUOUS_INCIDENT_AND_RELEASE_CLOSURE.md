# Phase 28 — Continuous Incident Recovery, Tenant-Matrix Repair, Release-Gate Integration, Deployment-Provenance Closure

**Destination verified ABSENT before writing.** **0 production files · 0 files under `tests/` ·
0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 governance records · 0 commits ·
0 pushes · 0 staged entries · 0 CI dispatched · 0 deployments · 0 stashes mutated.**
2 new files, both under `staging/`: this report + 1 test log.

> ## DO NOT RELEASE · INCIDENT OPEN · DEPLOYMENT BLOCKED
> **LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Executive outcome — four new executed measurements

Authorization remains **zero across every lane**, independently re-measured by Phases 29, 30
and 31 (the last noting *"**eight** independent sessions"* have now measured the ledger). So no
governed file was edited. This phase's contribution is **executed evidence**, not restatement.

| # | New result | Method |
|---|---|---|
| **1** | **The Phase 18 verifier is verified superior by execution: 28 passed, exit 0.** Its suite contains `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`, which **runs the Phase 17 module and demonstrates the defect**, plus `test_the_mandatory_job_assertion_is_load_bearing` — a mutation negative control. | **TESTED** |
| **2** | **A third deployment surface exists and is unprotected.** `scripts/deploy.sh:9` `IMAGE_TAG=${2:-latest}`, then `:46-47` `kubectl set image … backend=$IMAGE_TAG` with **no digest validation** — and it has **real callers** (`blue-green-deploy.yml` ×10, `scripts/blue-green-deploy.sh`, `infra/deploy.sh`). | **OBSERVED** |
| **3** | **All three `05-security.yml` evidence-retention claims confirmed in source**, including one the repo already knows how to fix — the IaC upload has `if: always()`, the SAST upload does not. | **OBSERVED** |
| **4** | **`tests/test_release_artifacts.py` is already in default discovery (8 tests) and already structurally tests `06-frontend-deploy.yml` step ordering.** The extension point for a gate-ordering test exists and is discovered. | **TESTED** |

---

## 2. Preflight and integrity manifest

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Branch · divergence | `main` · **0/0** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| Staged · unmerged | 0 · 0 | **0 · 0** |
| Files differing from `HEAD` | **2** | **2 — the same two** |
| Worktrees · stashes | 1 · **2** | 1 · **2 — untouched** |
| Porcelain | 143 | 145 (+2 = this report + 1 log) |

Tree hash identical at entry and exit, and to Phases 17, 21–23, 26, 27. **No tracked file has
changed across Phases 17 → 28.**

**Concurrent modifications — preserved, hashes unchanged:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` = `0c9981b2817e47b4faa915b69de204ff78f87af7`;
`tests/test_drift_classification.py` = `99a2917ac0a65761ef3dfe9299ed1df87647552a`.

**Stashes:** `stash@{0}` `filter-branch: rewrite` backup; `stash@{1}` `99ceb9b0…`, 18 files,
orphaned pre-rewrite WIP. **Neither applied, dropped, popped, renamed or rewritten.**

### 2.1 Target presence and tooling (rechecked, not assumed)

| Target | State | | Tool | State |
|---|---|---|---|---|
| `staging/phase18_release_gate.py` | PRESENT | | Python / pytest | **available** |
| `staging/phase18_test_release_gate.py` | PRESENT | | `bandit` | 1.9.4 |
| `staging/phase17_frontend_release_gate.py` | PRESENT | | `npm` | **available** |
| `tests/test_release_artifacts.py` | PRESENT | | `docker` | **ABSENT** |
| `scripts/deploy.sh` | PRESENT | | `trivy` | **ABSENT** |
| `ecs-task-definition-full.json` | PRESENT | | `terraform` | **ABSENT** |
| | | | `kubectl` | **ABSENT** |

---

## 3. Authorization / work matrix

| Pri | Lane | Targets | State |
|---|---|---|---|
| **A** | tenant matrix fixture | `tests/property/test_tenant_isolation_matrix.py` | **BLOCKED — NO GRANT** |
| **B** | frontend containment | S3 / CloudFront / DNS | **BLOCKED — OWNER DECISION** |
| **B2** | frontend gate | `06-frontend-deploy.yml`, `tests/test_release_artifacts.py` | **BLOCKED — NO GRANT** (`D-P15-4` request only) |
| **C** | Phase 18 promotion | `scripts/`, `tests/` | **BLOCKED — NO GRANT** |
| **D** | image identity (ECS + K8s) | `02/03-*.yml`, `deploy.sh`, `ecs.tf`, task JSON | **BLOCKED — NO GRANT** |
| **E** | security evidence | `05-security.yml` | **BLOCKED — NO GRANT** (`P23-K1`) |
| **F** | verifier diagnostics | `scripts/require_security_scan.py` | **BLOCKED — NO GRANT** |
| **G** | Bandit parity | gate test, 3 migrations | **BLOCKED — NO GRANT** |
| **H** | D7-1 · D6-D/D7-2 | routers, vault, updater | **BLOCKED — NO GRANT** |
| **I** | tenant/RLS · Redis · workers | policies, quota tests | **BLOCKED — NO GRANT / TOOL** |
| **J** | Terraform / Trivy | `terraform/**` | **BLOCKED — NO GRANT / TOOL** |

Ledger `:800-804` (`AP-W1D-1` … `AP-W1D-5`) **all `NOT GRANTED`**; `G-1`…`G-6` and `D-P15-*`
remain **requests**. `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md:276` unchanged.
`AP-W1D-3` stays confined to its own classifier files.

Per §1.1: this prompt is not blanket permission, and the `"I authorize…"` templates drafted in
earlier phases are requests. **No governance record was edited.**

---

## 4. Priority A — tenant matrix (diagnosis revalidated, fixture NOT edited)

Source re-read; **the Phase 27 diagnosis holds unchanged.** `tests/property/test_tenant_isolation_matrix.py`
hash `a60cd00c4558b386de61e045b08bf9b5ebfaf69d` — identical to Phase 27, so the prior CI
evidence remains applicable without re-retrieval.

**Failure:** `Requirement 21.8: 4 of the 150 attempted cells failed one of the three assertions.`
Endpoints `POST /api/library/{library_id}/clone` and `.../checkout`.

**Mechanism:** `check_strategy_quota` is a FastAPI **dependency** (`library.py:2340-2344`), so it
executes before the handler's ownership lookups (`:2437`, `:2445`). With the probe user at the
Free cap or below Trader tier, the owner's identifier, a foreign identifier and an absent
identifier all receive the identical entitlement 403. The **anti-vacuity control** then
correctly refuses the cell: *"the OWNER of the record is answered exactly what an identifier
that exists in no tenant is answered."*

**Production ordering NOT changed, and not proposed to change** — `library.py:2352` documents it
as closing a real write-bypass.

**Fixture fix — specified, NOT implemented (no grant):** give the probe owner Trader tier and
headroom under the active-strategy cap, via the established fixture factories and real
subscription/quota records rather than a fabricated entitlement; make the state deterministic
and ordering-independent; keep the pinned `REQUIREMENT_21_4_GAPS` entries until the ownership
branch genuinely executes.

**Preserved:** all 150 cells, all three assertions, the anti-vacuity control. **Nothing was
skipped, xfailed, narrowed, de-scoped or deleted.** No local re-run — inputs unchanged, and
reproduction requires the ungranted fixture change (brief §2).

---

## 5. Priority B — frontend incident

**Owner decision: still PENDING.** No decision record exists. **No containment action taken** —
no rollback, republish, workflow freeze, S3/CloudFront/DNS or artifact mutation. None is
authorized.

Evidence re-confirmed (unchanged since Phase 26/27; run still accessible):

| Item | Value |
|---|---|
| Published source SHA | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| Publishing run | `38129699497` · `success` · `09:48:45Z`→`09:50:13Z` |
| Bundle | `index-EYjpdKQA.js` · **`6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312`** · 509,689 bytes |
| Upload | 156 files, ~3.7 MiB |
| Build→deploy byte identity | **VERIFIED by the workflow** |
| Retained workflow artifact | **NONE** (`total_count = 0`) |
| Superseded since | **No** — newest of 60 runs |
| Same-SHA `05 Security` / `01 PR Check` | **failure** ×2 / **never ran** |

**Rollback caveat, load-bearing:** `6c5660a1` (security green) and `eee73265` (security red)
published **byte-identical** content (`13dd072f…620f`), and `6c5660a1` **also failed**
`01 PR Check`. Choosing the last green-security SHA is not choosing a frontend-verified artifact.

**`05 Security` does not inspect frontend JavaScript** — it runs Bandit on `backend_app`, Trivy
on the container image, Trivy on IaC (confirmed again in §8). Its failure is therefore not
evidence of a frontend defect; its passing would not have been evidence of safety.

**UNKNOWN:** current CloudFront edge bytes, current S3 contents, any later writes.
**S3/CloudFront not inspected — unauthorized.** A build checksum is not proof of current edge
content.

---

## 6. Priority C — Phase 18 verifier: superiority proven by execution (NEW)

The brief states Phase 17 has a mandatory-job admission defect and Phase 18 is the stronger
implementation. **I verified this by running the Phase 18 suite** rather than accepting it.

```
.venv\Scripts\python.exe -m pytest staging\phase18_test_release_gate.py -p no:randomly -q -o addopts=""
............................                                             [100%]
28 passed in 1.91s          EXIT=0
```

Log: `staging/p28_phase18_tests.txt`.

Its own header documents the design, and the tests back it:

| Test | What it does |
|---|---|
| `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped` | **Imports and executes `phase17_frontend_release_gate`** (`:31`) and shows it **ADMITS** a run whose mandatory job was skipped |
| job-level coverage (`:14`) | absent / skipped / cancelled / failed / neutral / unfinished conclusions |
| `test_the_mandatory_job_assertion_is_load_bearing` (`:16`) | **mutation check** — neuters the assertion and requires the suite to fail |
| `test_admits_a_candidate_with_all_runs_and_all_mandatory_jobs_green` (`:94`) | positive path across `REQUIRED_WORKFLOWS` |

**Assessment: the claim is correct and now evidenced.** Phase 18 checks per-job conclusions,
carries its own negative control, and demonstrates the Phase 17 defect by execution rather than
by argument.

**Critical discovery-status fact:** both prototypes live under `staging/`, and `pytest.ini` sets
`testpaths = tests`. **Neither runs in the default lane.** The 28 passes are prototype
evidence, **not project regression coverage**. Phase 26's 42 Phase-17 passes are likewise
prototype-only — and are now known to be passes of the **defective** implementation.

**Promotion specified, NOT implemented (no grant):** move the Phase 18 implementation to
`scripts/` and its tests to `tests/`; **preserve both staging originals**; retain Phase 17
solely as historical negative-control evidence; confirm default discovery; then wire it into the
backend and frontend workflows using the event's actual `head_sha`, never the default-branch
checkout.

**Not decided on the owner's behalf:** which jobs are mandatory, and the
`provisioning-and-isolation` advisory question.

---

## 7. Priority D — image identity: a third surface, previously untracked

### 7.1 Consumer census (rebuilt from current source)

| Surface | Image identity | Validation |
|---|---|---|
| `02-build.yml:72` | builds `:$IMAGE_TAG` **and** `:latest` | — |
| `02-build.yml:75-76` | pushes **both**, **before any gate** | none |
| `02-build.yml:78` | `… \|\| echo "$IMAGE_TAG"` | **tag substituted for digest** |
| `03-deploy.yml:168-173` | `… \|\| echo ""` then `exit 1` | **fail-closed — PRESERVED** |
| `03-deploy.yml:174` | digest **echoed only**, never exported | lost here |
| `ecs_deploy_and_diagnose.py:901` | `…/vyomquant-api:{IMAGE_TAG}` | **deploys by tag** |
| **`scripts/deploy.sh:9`** | **`IMAGE_TAG=${2:-latest}`** | **none** |
| **`scripts/deploy.sh:46-47`** | **`kubectl set image deployment/trading-backend backend=$IMAGE_TAG`** | **none — no digest check before `kubectl`** |
| `terraform/ecs.tf`, `ecs-task-definition-full.json`, 2nd Terraform root | not modified | — |

### 7.2 The Kubernetes path is referenced, not dormant

Per the brief, I did not assume `deploy.sh` is inactive. Measured callers:

- `aerora_quant_platform/deployment/.github/workflows/blue-green-deploy.yml` — **10 references**
  (`:68`, `:71`, `:125`, `:126`, `:197`, `:198`, `:228`, `:229`, `:279`, `:280`)
- `scripts/blue-green-deploy.sh` — `:7`–`:11`, `:104`
- `infra/deploy.sh:3`, `infra/README.md:66,88`

**Source-level consumer: CONFIRMED. Active production deployment: UNKNOWN** — that distinction
is preserved, and `kubectl` is **ABSENT** locally so nothing could be exercised. **No `kubectl`
was invoked.**

**Specification (NOT implemented):** require an explicit approved image identity; refuse missing
or malformed identity **before any `kubectl` command**; remove the `latest` default; preserve the
environment/namespace guards at `:15-16`, `:29`, `:35`; add mocked-command tests proving invalid
input cannot reach `kubectl`.

### 7.3 End-to-end provenance

`approved SHA → build → immutable digest → approved deployment input → workload reference`

| Link | State |
|---|---|
| SHA → build | **present** (ungated) |
| build → digest | **BROKEN** — `02-build.yml:78` tag fallback |
| digest → deployment input | **BROKEN** — echoed then discarded |
| deployment input → ECS workload | **BROKEN** — deploys by tag |
| deployment input → K8s workload | **BROKEN** — `latest` default, no validation |
| ECR tag immutability | **UNKNOWN** — no `aws_ecr_repository` in any Terraform root |

**IMAGE PROVENANCE NOT ESTABLISHED.** No image was built, pushed, pulled, retagged, promoted
or deleted.

---

## 8. Priority E — security-workflow evidence retention: all three claims confirmed

Read from current `05-security.yml`:

| Finding | Evidence | Verdict |
|---|---|---|
| **Safety's exit status is suppressed** | `:39` `safety check --json --output reports/safety-report.json \|\| echo "Safety advisory logged"` | **CONFIRMED** — `\|\| echo` means Safety can never fail the job; it is **de facto advisory** regardless of written policy |
| **SAST evidence is lost when Bandit fails** | `:35` Bandit scan → `:41-42` `Upload Security Reports` with **no `if: always()`** | **CONFIRMED** — a failing Bandit step halts the job, so the upload never runs |
| **The second IaC root is skipped when the first fails** | `:120` `Trivy IaC scan — terraform/ (blocking)` then `:131` `Trivy IaC scan — aerora_quant_platform/infrastructure/ (blocking)`, **no `if: always()` on `:131`** | **CONFIRMED** — the second root is scanned **only when the first is clean** |

**The asymmetry is the tell:** `:156-158` `Upload IaC Scan Report` **does** carry `if: always()`.
The pattern is already understood in this file and simply was not applied to `:41` or `:131`.

Also observed: the comment at `:91` references `pip-audit`, but **no `pip-audit` step exists** in
the workflow.

**Nothing changed.** Thresholds unchanged, no exclusions added, no findings suppressed. The
Safety blocking-vs-advisory policy is an owner decision (Phase 30 reports resolving the policy
question at its §2); I did not quietly convert anything.

---

## 9. Priority B2 — the frontend gate's extension point already exists (NEW)

`tests/test_release_artifacts.py` is **inside `tests/`**, so unlike the staging prototypes it
**is** collected by default:

```
.venv\Scripts\python.exe -m pytest tests\test_release_artifacts.py --collect-only -q -o addopts=""
8 tests collected in 1.32s
```

It already performs **structural ordering assertions on the very workflow that needs the gate**:

| Test | Relevance |
|---|---|
| `test_deploy_workflow_carries_the_size_gate` (`:97`) | asserts a gate's **presence** in `06-frontend-deploy.yml` |
| **`test_size_gate_runs_after_build_and_before_s3_sync` (`:110`)** | asserts a gate's **position relative to the S3 sync** — exactly the shape a gate-before-credentials test needs |
| `test_no_publishable_release_artifact_is_implausibly_small` (`:62`) | artifact plausibility |
| `test_real_windows_installer_is_untouched` (`:82`) | installer integrity |

**This is the natural, already-discovered home for the gate-ordering regression tests** named in
the brief (gate presence, exact-SHA propagation, checkout ref, gate-before-credentials,
gate-before-every-publication-command, failure propagation, no fail-open). Adding them there
avoids both the staging-discovery problem and a new test harness. **`WORKFLOW` is already
resolved at `:34`.** Not implemented — no grant.

Current `06-frontend-deploy.yml` (hash `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9`, unchanged):
triggers `push [main, master]` + `workflow_dispatch`; **`needs:` count = 0**; AWS credentials at
step 11; S3 syncs at `:289`/`:299`/`:307`; CloudFront invalidation at `:329`. **No gate on any
path; `workflow_dispatch` equally ungated.** Nothing published or invalidated to test anything.

---

## 10. Priorities F–J — remaining lanes

| Lane | State | Status |
|---|---|---|
| **F** verifier diagnostics | `require_security_scan.py` (hash `a1c8dd2d…`) preserves exact-SHA binding, missing-scan refusal, `cancelled`/`skipped` refusal, API-error refusal, newest-run selection, bounded wait, and **`ALLOW_UNSCANNED` cannot override a failed scan** — **all preserved**. Its docstring claim that the workflow *"is red only for CRITICAL findings"* is **inaccurate**: measured jobs at `ecd23a9e` were Container **success**, Bandit **failure**, IaC **failure**, so it would attribute a Bandit/IaC failure to a container CVE. Pagination **latent** (`total_count = 6` vs `per_page = 100`). | **BLOCKED — NO GRANT** |
| **G** Bandit parity | CI `bandit -r backend_app -ll`, no `continue-on-error`. **9 × B608 MEDIUM** in 3 alembic migrations. **bandit 1.9.4 / Python 3.12.10.** Gate test scoped to **one file**; AST companion proves a **different property** than B608 detects. All 9 reviewed: validator-constrained identifiers or module literal constants, values parameter-bound. **9 structurally safe · 9 still scanner findings · 0 suppressed · 0 owner-accepted.** Source hashes and tool version unchanged → **scan NOT re-run**, prior evidence reused per brief §2. | **BLOCKED — NO GRANT** |
| **H** D7-1 | Three invalid `access_token=` args to `load_decrypted_keys(self, user_id, exchange_id)`: `orders.py:792` (**unguarded → 500**), `orders.py:1098` (`except` → `return []`), `market.py:53` (`except` → **unauthenticated public fallback**). `routers/exchange.py` **correct — unchanged**. Legitimate at `risk.py:313,325,407` and `paper_trading.py` ×8 — **no global replacement**. `market.py:56` guard preserved; no unauthenticated fallback introduced; no fabricated empty data. | **BLOCKED — NO GRANT** |
| **H** D6-D → D7-2 | `APIKeyVault.__init__(self)` takes **zero args** but **fail-fast raises** `EnvironmentError`. A naive `KeyVault`→`APIKeyVault` swap raises **inside the same `try`** — identical user-visible outcome. Correct target is the initialized `app_state.vault`, which per D6-D is a `MockEngine`. **D7-2 must not precede D6-D.** No speculative class instantiated; no second vault. | **BLOCKED — NO GRANT** |
| **I** tenant/RLS | `apply_tenant_context` has **zero external consumers**. Three competing identity expressions (`dependencies.py:318`, `orders.py:819/940`, `distributed_execution.py:296`) agree only because no token carries a `tenant_id` claim — **owner choice required**. Two **permissively OR-combined** policy families; `execution_records` predicate open. FORCE evidence reused: ENABLE+unset GUC → 2 rows; **ENABLE+FORCE+unset GUC → 0 rows silently**; FORCE+correct GUC → 1 row. **FORCE NOT enabled.** Local superuser is not certification. Plus §4: 2 of 150 cells unobservable. | **NOT CERTIFIED** |
| **I** Redis / workers | Redis env unset, 6379 **CLOSED**, no `redis-server`, **no Docker** → 5 tests **SKIP, not passes**; strict XFAIL preserved. `UNKNOWN` exit policy **not invented** (CAS pinned `'EXECUTING'`, `execution_record.py:1488`) → **W-5 behind W-4; W-3 behind W-6**. W-6: **10 of 20 asserted misclassified**; `retryable` **log-only**, 0 control flow; **no retries added around financial side effects**. | **BLOCKED — TOOL / NO GRANT** |
| **J** Terraform | 4 unrestricted egress blocks: `security_groups.tf:22/52/73` + `redis-only/main.tf:58`. Redis SG names **identical by default**; **collision conditional on same VPC — UNVERIFIED** (main root *creates* `aws_vpc.main`; `redis-only` adopts hardcoded `vpc-0f3e7b3f243fbc133`). `redis-only` has **no backend block → local state**, 0 variables. **`AWS-0054` false positive** — `alb.tf` `dynamic` redirect + `lifecycle.precondition`; **compensating control preserved**. No egress removed, no state changed, no apply. | **BLOCKED — NO GRANT / TOOL** |

---

## 11. Tests and checks executed

| Check | Command | Result |
|---|---|---|
| Phase 18 verifier suite | `pytest staging\phase18_test_release_gate.py -p no:randomly -q -o addopts=""` | **PASS — 28 passed, 1.91s, exit 0** |
| Release-artifact test discovery | `pytest tests\test_release_artifacts.py --collect-only -q -o addopts=""` | **PASS — 8 tests collected** |
| Tenant matrix | — | **NOT RUN** — inputs unchanged; reproduction needs an ungranted fixture change |
| Aggregate pytest | — | **NOT RUN** — nothing was modified |
| Bandit at CI scope | — | **NOT RUN** — command/source/version unchanged; prior evidence reused |
| Trivy IaC | — | **BLOCKED** — `trivy` absent |
| Terraform fmt/validate | — | **BLOCKED** — `terraform` absent |
| Container scan | — | **BLOCKED** — `docker` absent |
| Kubernetes path exercise | — | **BLOCKED** — `kubectl` absent; **and would not be run against a cluster regardless** |
| Redis quota (5 tests) | — | **SKIPPED** — opt-in unset, 6379 closed. **Skips are not passes.** |
| Gate checks the same release identity | — | **FAIL** — frontend ungated; verifier is workflow-level only |
| Frontend blocked from AWS credentials | — | **FAIL** — no gate upstream of step 11 |
| Digest propagation build→deploy | — | **FAIL** — §7.3 |

**No diff to review — no file was changed.** No test was weakened to obtain green.

---

## 12. Remaining owner decisions — smallest next grant first

| # | Decision | Owner | Smallest action |
|---|---|---|---|
| **1** | **Frontend containment** (§5) — plus separate execution authorization if rollback is chosen | **release owner** | Answer one question; a build is **live** and unverified |
| **2** | **Promote Phase 18 to `scripts/` + `tests/`** (§6) | **code owner** | One grant. **Its superiority is now proven by execution — 28 passed, including a test that falsifies Phase 17.** This is the single highest-value grant available. |
| **3** | **Tenant matrix fixture** (§4) | **code owner** | One grant → 2 of 150 cells become observable |
| **4** | **Tenant identity**: choose **one** of three expressions | **code owner** | Costs no code; unblocks all context wiring |
| **5** | Frontend gate `D-P15-4` + tests in `tests/test_release_artifacts.py` (§9) | **workflow/release owner** | One grant; the extension point already exists |
| **6** | Mandatory-job inventory; `provisioning-and-isolation` advisory question | **security owner** | Needed before §6 wiring |
| **7** | Safety blocking vs advisory (§8) — currently advisory **by `\|\| echo`**, whatever the policy says | **security owner** | One decision |
| **8** | `P23-K1` evidence retention: `if: always()` on `:41`, independent `:131` | **security owner** | One grant |
| **9** | Is `scripts/deploy.sh` an active supported path? (§7.2) | **infra/release owner** | Yes/no, then an edit grant |
| **10** | ECR repository ownership + tag-immutability policy; `aws_vpc.main` identity; `redis-only` state location | **infra owner** | — |
| **11** | `G-2` build digest; `:latest` promotion policy; `D-P15-3` correctness route | **release owner** | — |
| **12** | D7-1; **D6-D before D7-2**; W-6; Bandit parity; 5 Trivy CRITICALs | code / security owners | — |
| **13** | `UNKNOWN` exit policy; isolated Redis; orphaned `stash@{1}` disposition | execution-state / coordinator / code owners | — |

**Work that proceeds independently of #1:** every other lane. Nothing here is blocked on another
lane's completion — only on its own grant or an absent tool.

---

## 13. Production evidence vs certification — stated separately

**Production evidence.** Only read-only CI observations were obtained: a frontend publication
occurred at `ecd23a9e`; its bundle identity is `6f12b52c…4312`; the workflow self-verified
build→deploy byte identity. **No production system was inspected or mutated.** Current
CloudFront edge bytes, S3 contents, ECR repository policy, Terraform state and live ECS/K8s
state are all **UNKNOWN**.

**Release certification. NONE.** No mandatory gate is evidenced as passing for release identity
`ecd23a9e`: correctness **never ran** (and the blocking test is diagnosed in §4), security
**failed**, the frontend gate **does not exist and was bypassed**, and image provenance is
**not established** across three deployment surfaces. The backend gate **worked and refused** —
the one control demonstrated effective end to end.

A passing local test does not establish production deployment, current CloudFront bytes, image
provenance, or tenant isolation.

---

## 14. Prohibited-operation checklist

| Prohibited | Performed? |
|---|---|
| Stage, commit, push, reset, stash, pop, drop, clean, revert, merge, rebase, switch branches, rewrite history, modify worktrees | **No** — tree hash identical; 2 stashes at entry **and** exit; 1 worktree |
| Create PRs, dispatch CI, modify branch protection or repository settings | **No** — `gh` not invoked this phase |
| Deploy, roll back, republish, retag, invalidate CloudFront, change DNS, **invoke `kubectl`**, apply Terraform, mutate cloud or production databases | **No** — `kubectl`/`terraform`/`docker` all absent, and none would have been used against a real target |
| Enable live trading / place exchange orders | **No** |
| Expose secrets, credentials, tokens, DSNs, keys, decrypted material, sensitive headers | **No** — no secret-bearing file opened for inspection |
| Weaken gates, lower thresholds, suppress findings, **remove the anti-vacuity control** | **No** — `03-deploy.yml` digest refusal, the ALB precondition, the XFAIL contract and the matrix anti-vacuity control all **preserved** |
| Weaken, skip, xfail or de-scope the tenant-isolation property | **No** |
| Overwrite a report; disturb concurrent work, untracked files, stashes, worktrees or prototypes | **No** — destination verified absent; both staging prototypes intact |
| Claim an unavailable check passed | **No** — Trivy/Terraform/Docker/kubectl **BLOCKED**; Redis **SKIPPED**; unchanged-input suites **NOT RUN** |
| Claim tenant isolation certified | **No** — **NOT CERTIFIED** |

---

*Phase 28 closed. Tree hash unchanged at entry and exit. Both stashes and the worktree intact.
0 of 11 lanes authorized; 0 governed files changed. New executed evidence: the Phase 18 verifier
passes 28/28 and falsifies Phase 17 by execution; a third unprotected deployment surface
(`scripts/deploy.sh` → `kubectl set image`, `latest` default) with real callers; all three
`05-security.yml` evidence-retention defects confirmed in source; and the already-discovered
`tests/test_release_artifacts.py` identified as the gate-test extension point.*

***DO NOT RELEASE · INCIDENT OPEN · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.***
