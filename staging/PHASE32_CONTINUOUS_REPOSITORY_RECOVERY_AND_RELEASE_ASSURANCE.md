# Phase 32 — Continuous Repository Recovery, Release-Gate Integration, Multi-Platform Image Provenance, Security Pipeline Repair

**Destination verified ABSENT before writing.** **0 production files · 0 files under `tests/` ·
0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 governance records · 0 commits ·
0 pushes · 0 staged entries · 0 CI dispatched · 0 deployments · 0 stashes mutated.**
1 new file: this report.

> ## DO NOT RELEASE · DEPLOYMENT BLOCKED
> **LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Executive outcome

Authorization remains **zero on every governed target**, so no governed file was edited. The
phase's output is new measurement, and the principal result **reverses the recommendation that
Phases 28–31 had converged on**.

> ### Phase 18 must NOT be promoted as-is. Its mandatory-job inventory is 9-for-9 wrong, and its own test suite is structurally incapable of detecting that.
>
> `REQUIRED_WORKFLOWS` configures mandatory jobs by **workflow job ID**
> (`sast-scan`, `unit-tests`, …). Its own docstring (`:60-61`) states job names *"must match the
> API's `name`, i.e. the `name:` field if present, otherwise the job id."* **Every job in both
> workflows has a `name:` field**, so the API never reports the id.
>
> **Cross-checked against live API data: 9 of 9 configured identifiers have NO MATCH.**
>
> Promoted unchanged, the verifier would **refuse every release, including a fully green one.**

| # | Result | Status |
|---|---|---|
| **1** | **Phase 18 job-identity defect confirmed, 9/9 unmatched** against live API job names from runs `38061973739` and `38132697285`. | **MEASURED** |
| **2** | **Proven why its 28 passing tests cannot catch it:** the fixture at `:62` builds synthetic jobs *from `REQUIRED_WORKFLOWS` itself*, and **no test reads any workflow YAML** (15 "yaml" hits, all `REQUIRED_WORKFLOWS`, zero `.github` references). | **MEASURED** |
| **3** | **`ADVISORY_JOBS` is also incomplete:** live `01 PR Check` runs `D5' Real-Redis Diagnostics (non-blocking)`, which appears in neither the mandatory nor the advisory list. | **MEASURED** |
| **4** | **A fourth deployment surface, with a different defect:** blue-green takes an operator-supplied `workflow_dispatch` image input, validates only non-emptiness, and feeds it to `kubectl set image`; the promote path re-reads the live cluster image behind an `\|\| echo` fallback. | **OBSERVED** |

**Direction of failure matters.** Phase 17's defect **admits** a run with a skipped mandatory
job (fails open). Phase 18's defect **refuses everything** (fails closed). Phase 18 is still the
better foundation — its job-level conclusion logic is sound and verified — but it is **not
promotable until the identity mapping is fixed**.

---

## 2. Entry / exit integrity manifest

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Branch · divergence | `main` · **0/0** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| Staged · unmerged | 0 · 0 | **0 · 0** |
| Files differing from `HEAD` | **2** | **2 — the same two** |
| Worktrees · stashes | 1 · **2** | 1 · **2 — untouched** |
| Porcelain | 153 | 154 (+1 = this report) |

Tree hash identical at entry and exit, and to Phases 17, 21–23, 26–28. **No tracked file has
changed across Phases 17 → 32.**

**Concurrent modifications preserved, hashes unchanged:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` = `0c9981b2817e47b4faa915b69de204ff78f87af7`;
`tests/test_drift_classification.py` = `99a2917ac0a65761ef3dfe9299ed1df87647552a`.

**Prototype hashes (inspected, not edited):**
`staging/phase18_release_gate.py` = `5c70dbbfb882036badade2ff01b4bf687595ff4e`;
`staging/phase18_test_release_gate.py` = `3dd575192edbb8df26f3bcee0ba6fef7992e0483`.

**Stashes:** `stash@{0}` `filter-branch: rewrite` backup; `stash@{1}` `99ceb9b0…`, orphaned
pre-rewrite WIP. **Neither applied, dropped, popped, renamed or rewritten** — and its
disposition decision is tracked without touching it.

**Tool availability (probed, non-mutating):** `gh` **available** · `docker` **ABSENT** ·
`trivy` **ABSENT** · `terraform` **ABSENT** · `kubectl` **ABSENT**.

---

## 3. Authorization ledger and workstream matrix

Ledger `:800-804` (`AP-W1D-1` … `AP-W1D-5`) **all `NOT GRANTED`**. `G-1`…`G-6`, `D-P15-*`,
`P22-B1`, `P23-K1` remain **requests**. `AP-W1D-3` stays confined to its own classifier files.

Per §1.2: this prompt is not a grant, and the `"I authorize…"` templates drafted in earlier
phases are requests. **No governance record was edited.**

| WS | Targets | Grant | Status this phase |
|---|---|---|---|
| **A** release verifier | `staging/phase18_*` → `scripts/`, `tests/` | **NO GRANT** | **New decisive defect found (§4).** Promotion recommendation **reversed**. |
| **B** frontend gate | `06-frontend-deploy.yml`, `tests/test_release_artifacts.py` | **NO GRANT** | Specified; extension point confirmed (§5) |
| **B3** incident containment | S3 / CloudFront / DNS | **OWNER DECISION PENDING** | No action; evidence unchanged |
| **C** image identity (ECS, K8s, blue-green) | `02/03-*.yml`, 3 scripts, `ecs.tf`, task JSON | **NO GRANT** | **Fourth surface found (§6)** |
| **D** security evidence retention | `05-security.yml` | **NO GRANT** | 3 defects confirmed previously; unchanged |
| **E** verifier diagnostics | `scripts/require_security_scan.py` | **NO GRANT** | Mis-attribution confirmed; unchanged |
| **F** Bandit parity | gate test, 3 migrations | **NO GRANT** | Inputs unchanged → reused |
| **G** D7-1 | `routers/{orders,market}.py` | **NO GRANT** | Unchanged |
| **H** D6-D → D7-2 | `core/state.py`, updater | **NO GRANT** | Unchanged; D7-2 still gated behind D6-D |
| **I** tenant matrix / RLS | matrix fixture, policies | **NO GRANT** | Diagnosis unchanged |
| **J** Redis / retries / workers | quota tests, classifier | **NO GRANT / TOOL** | Redis absent → **SKIPPED** |
| **K** Terraform | `terraform/**` | **NO GRANT / TOOL** | `terraform`+`trivy` absent → **BLOCKED** |
| **L** test discovery | `tests/` | **NO GRANT** | Discovery facts confirmed (§5) |
| **M** consumer census | read-only | n/a | **Extended — 4 surfaces (§6)** |

---

## 4. Workstream A — the Phase 18 defect, measured

### 4.1 Configured versus actual

`staging/phase18_release_gate.py:62-66`:

```python
REQUIRED_WORKFLOWS: dict = {
    "05 Security": ("sast-scan", "container-security", "iac-scan"),
    "01 PR Check": ("static-audit", "unit-tests", "paper-mode-tests", "database-tests",
                    "docker-check", "frontend-tests"),
}
```

Real job identity, resolved from workflow YAML **and** cross-checked against live API jobs:

| Workflow | Job id (configured) | `name:` in YAML = API `name` | Match? |
|---|---|---|---|
| 05 Security | `sast-scan` | `Bandit SAST & Dependency Security Scan` | **NO** |
| 05 Security | `container-security` | `Trivy Container Vulnerability Scanner` | **NO** |
| 05 Security | `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` | **NO** |
| 01 PR Check | `static-audit` | `Code Quality & Static Audit` | **NO** |
| 01 PR Check | `unit-tests` | `Unit & Integration Tests` | **NO** |
| 01 PR Check | `paper-mode-tests` | `Execution Tests (production posture)` | **NO** |
| 01 PR Check | `database-tests` | `Database Tests (PostgreSQL)` | **NO** |
| 01 PR Check | `docker-check` | `Docker Build Validation` | **NO** |
| 01 PR Check | `frontend-tests` | `Frontend Build & Unit Tests` | **NO** |

```
TOTAL configured mandatory jobs: 9
UNMATCHED against live API names: 9
```

Live API names actually returned:

- **01 PR Check** (`38061973739`): `Frontend Build & Unit Tests` · `Code Quality & Static Audit` ·
  `Format Backlog (report only)` · `Unit & Integration Tests` ·
  `D5' Real-Redis Diagnostics (non-blocking)` · `Database Tests (PostgreSQL)` ·
  `Execution Tests (production posture)` · `Docker Build Validation`
- **05 Security** (`38132697285`): `Trivy Container Vulnerability Scanner` ·
  `Bandit SAST & Dependency Security Scan` · `Trivy IaC Misconfiguration & Secret Scan`

**Consequence.** Every mandatory-job lookup misses, so the gate raises `GateRefusal` on every
candidate. It is a **permanent deny** — safe in direction, but unconditionally non-functional.

### 4.2 Why 28 passing tests prove nothing about this

`staging/phase18_test_release_gate.py:59-62`:

```python
"""Every mandatory job of every required workflow, all green."""
for i, (workflow, mandatory) in enumerate(gate.REQUIRED_WORKFLOWS.items()):
    jobs[1000 + i] = [_job(n) for n in mandatory]
```

The fixture synthesises the fake API response **from `REQUIRED_WORKFLOWS` itself**, copying each
configured identifier into the job's `name` field. The suite therefore asserts
**self-consistency**: whatever is configured is exactly what the fake API returns. The mapping
to reality is never exercised.

Confirmed by search: 15 matches for `yaml|\.github|workflows` in that file, and **every one is
`REQUIRED_WORKFLOWS`**. **No test reads a real workflow file.** This is precisely the
"synthetic fixtures that copy internal identifiers into display-name fields" failure mode the
brief names.

Phase 28's measurement (28 passed, exit 0) was accurate and is not withdrawn — but it is now
correctly scoped: it validates **conclusion handling**, not **job identity**.

### 4.3 A third, smaller gap in the same inventory

`ADVISORY_JOBS = ("format-check", "provisioning-and-isolation")`. Live `01 PR Check` also runs
**`D5' Real-Redis Diagnostics (non-blocking)`**, which appears in neither list. So the inventory
is incomplete against reality in both directions. Its own name says non-blocking, but **that is
an owner classification, not mine to assume**, and `provisioning-and-isolation` remains the
explicitly unresolved decision the module already documents.

### 4.4 Repair specification (NOT IMPLEMENTED — no grant)

1. Replace the identifier tuples with an explicit **three-way mapping**: workflow → job id →
   expected API `name`, derived from the workflow YAML rather than hand-copied.
2. **Resolve names from the YAML at test time** so a renamed `name:` field fails a test instead
   of silently breaking the gate.
3. Reject duplicate, ambiguous, missing, renamed or unexpected mandatory jobs; **fail closed
   when identity cannot be established** — distinct from "job concluded badly".
4. Never treat a matching display name as proof the job executed correctly.
5. **Preserve** per-job conclusion checks, exact-SHA binding, pagination refusal, bounded wait,
   branch allow-list, API-error refusal, problem aggregation, and explicit handling of absent /
   skipped / cancelled / failed / neutral / unfinished.
6. Add the real-workflow contract tests enumerated in the brief §A3 — notably "every configured
   mandatory job resolves to a real workflow job" and "no required identifier is silently
   unmatched", **either of which would have caught this**.
7. Keep the Phase 17 negative control.

**Revised recommendation: do not promote Phase 18 until §4.4(1)–(3) land.** Promoting it now
would replace a fail-open gate with a gate that blocks all releases — a different outage, not a
fix.

---

## 5. Workstreams B and L — frontend gate and test discovery

`06-frontend-deploy.yml` (hash `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9`, unchanged): triggers
`push [main, master]` + `workflow_dispatch`; **`needs:` = 0**; AWS credentials at step 11; S3
syncs `:289`/`:299`/`:307`; CloudFront invalidation `:329`. **No gate on any path; manual
dispatch equally ungated.** Nothing published or invalidated.

**Discovery facts (confirmed):** `pytest.ini` sets `testpaths = tests`, so **all
`staging/phase1*` prototypes are outside default collection** — their passes are prototype
evidence, never CI coverage. `tests/test_release_artifacts.py` **is** collected (8 tests) and
already asserts workflow ordering via `test_size_gate_runs_after_build_and_before_s3_sync`,
with `WORKFLOW` resolved at `:34`. **That remains the correct, already-discovered home** for the
10 gate assertions in brief §B2, using structural YAML parsing rather than line numbers.

**Incident containment: PENDING.** No authoritative decision exists. Evidence unchanged —
published SHA `ecd23a9e`, bundle `index-EYjpdKQA.js` /
`6f12b52cfb5801511667900db75d0cde13baadc576013d2d9f869e9bc6324312`, build→upload byte identity
verified by the workflow, **no retained artifact**, newest of 60 runs. Current CloudFront edge
bytes and S3 contents **UNKNOWN** — not inspected, not authorized. `05 Security` does **not**
scan frontend JavaScript, so neither its failure nor a pass would speak to frontend safety.

---

## 6. Workstream C / M — image provenance across four surfaces

| # | Surface | Image identity | Validation |
|---|---|---|---|
| 1 | `02-build.yml:72`, `:75-76` | builds and pushes `:$IMAGE_TAG` **and** `:latest`, **before any gate** | none |
| 1 | `02-build.yml:78` | `… \|\| echo "$IMAGE_TAG"` | **tag substituted for digest** |
| 2 | `03-deploy.yml:168-173` | `… \|\| echo ""` then `exit 1` | **fail-closed — PRESERVED** |
| 2 | `03-deploy.yml:174` → `ecs_deploy_and_diagnose.py:901` | digest **echoed, never exported**; URI built from `IMAGE_TAG` | **deploys by tag** |
| 3 | `scripts/deploy.sh:9`, `:46-47` | **`IMAGE_TAG=${2:-latest}`** → `kubectl set image … backend=$IMAGE_TAG` | **none; defaults to a mutable alias** |
| **4** | **`scripts/blue-green-deploy.sh:66-70`** | `local IMAGE_TAG=$1`; refuses empty — *"Image tag required"* | **non-empty only; no digest format check** |
| **4** | **`:86-87`** | `kubectl set image deployment/$GREEN_DEPLOYMENT backend=$IMAGE_TAG` — **unquoted** | none |
| **4** | **`:247-248` promote** | `backend=$GREEN_IMAGE`, read back from the cluster at `:54` via `kubectl get … jsonpath … \|\| echo` | **propagates whatever is live, behind a fallback that can yield empty** |
| **4** | **`blue-green-deploy.yml:71`** | `./scripts/blue-green-deploy.sh deploy "${{ github.event.inputs.image_tag }}"` | **operator-supplied `workflow_dispatch` input, ungated** |

**Surface 4 is new this phase and its defect profile differs from surface 3.** `deploy.sh`
defaults to `latest`; blue-green has **no default but no approval either** — an arbitrary
operator-typed tag reaches `kubectl set image` after only a non-empty check. The *promote* step
is worse in kind: it reads the current green image out of the cluster and re-applies it to blue,
so the deployed identity is whatever is already running rather than anything approved, and the
`|| echo` fallback at `:54` can produce an empty value.

`infra/deploy.sh` is a *"Master Orchestrator & Pre/Post-Flight Verification Engine"* under
`set -euo pipefail` — inspected, not traced further this phase.

**Source-level consumers: CONFIRMED (4). Active production deployment: UNKNOWN** for surfaces 3
and 4 — `kubectl` is **ABSENT** locally and **no `kubectl` was invoked**. Per the brief, absence
of a recent run is not evidence of dormancy, and I do not declare these legacy to avoid fixing
them.

### 6.1 Provenance chain

| Link | State |
|---|---|
| approved SHA → build | present, **ungated** |
| build → immutable digest | **BROKEN** — `02-build.yml:78` tag fallback |
| digest → deployment input | **BROKEN** — echoed then discarded |
| input → ECS workload | **BROKEN** — deploys by tag |
| input → K8s (`deploy.sh`) | **BROKEN** — `latest` default, no validation |
| input → K8s (blue-green) | **BROKEN** — ungated dispatch input; promote re-reads live state |
| ECR tag immutability | **UNKNOWN** — no `aws_ecr_repository` in any Terraform root |

**IMAGE PROVENANCE NOT ESTABLISHED.** No image built, pushed, pulled, retagged, promoted or
deleted; no task definition registered; no rollback performed.

---

## 7. Workstreams D–K — status

| WS | Position (reused where inputs unchanged) | Status |
|---|---|---|
| **D** security evidence | `05-security.yml`: `:39` `safety … \|\| echo` **suppresses exit** → advisory in fact; `:41` SAST upload **lacks `if: always()`** → evidence lost when Bandit fails; `:131` second IaC root **lacks `if: always()`** → skipped when `terraform/` fails. `:156-158` IaC upload **does** have it — the asymmetry is the tell. Scanner installs at `:30` **unpinned** (`pip install bandit safety`). No `pip-audit` step despite the `:91` comment. | **NO GRANT** |
| **E** verifier diagnostics | `require_security_scan.py` preserves exact-SHA binding, missing-scan / `cancelled` / `skipped` / API-error refusal, newest-run selection, bounded wait, and `ALLOW_UNSCANNED` **cannot override a failed scan** — all **preserved**. Its docstring claim that the workflow is *"red only for CRITICAL findings"* is **false**: measured jobs were Container **success**, Bandit **failure**, IaC **failure**, so it would attribute a Bandit/IaC failure to a container CVE. Pagination **latent** (`total_count = 6` vs `per_page = 100`). | **NO GRANT** |
| **F** Bandit | **9 × B608 MEDIUM** in 3 alembic migrations; **bandit 1.9.4 / Python 3.12.10**. Gate test scoped to **one file**; AST companion checks a **different property** than B608. All 9 reviewed: validator-constrained identifiers or module literal constants, values parameter-bound. **9 structurally safe · 9 live findings · 0 suppressed · 0 owner-accepted.** Source hashes, command, config and tool version unchanged → **NOT re-run**, prior evidence reused per §1.1. The wider 62-`nosec` census is **not** generalised from the 9 reviewed sites. | **NO GRANT** |
| **G** D7-1 | Three invalid `access_token=` args to `load_decrypted_keys(self, user_id, exchange_id)`: `orders.py:792` (**unguarded → 500**), `orders.py:1098` (`except` → `return []`), `market.py:53` (`except` → **unauthenticated public fallback**). `exchange.py` **correct — unchanged**. Legitimate at `risk.py:313,325,407` and `paper_trading.py` ×8 — **no global replacement**. `market.py:56` guard preserved; keys-absent policy **not invented**. | **NO GRANT** |
| **H** D6-D → D7-2 | `APIKeyVault.__init__(self)` takes **zero args** and **fail-fast raises** `EnvironmentError`; a naive `KeyVault`→`APIKeyVault` swap raises **inside the same `try`**, yielding an identical user-visible outcome. The correct target is the initialized `app_state.vault`, previously reported a `MockEngine`. **No speculative instantiation; no second vault.** D7-2 stays gated behind verified D6-D. | **NO GRANT** |
| **I** tenant matrix / RLS | Matrix hash `a60cd00c…` unchanged → CI diagnosis still applicable: **4 of 150 cells**, `POST /clone` + `/checkout`, `check_strategy_quota` as a FastAPI dependency (`library.py:2340-2344`) runs before ownership lookups (`:2437`, `:2445`); entitlement 403 masks the branch and the **anti-vacuity control correctly refuses**. Production ordering **not changed** (`:2352` documents it as closing a write-bypass). All 150 cells, 3 assertions, anti-vacuity control and pinned gaps **preserved**. RLS: `apply_tenant_context` has **0 external consumers**; 3 competing identity expressions; permissive OR-combined policy families; FORCE **not enabled** — a zero-row result under a privileged role is **not** certification. | **NOT CERTIFIED** |
| **J** Redis / retries / workers | Redis env unset, 6379 **CLOSED**, no `redis-server`, **no Docker** → 5 opt-in tests **SKIPPED, not passed**; strict XFAIL **preserved**. W-6: **10 of 20 asserted misclassified**; `retryable` **log-only**, 0 control-flow consumers; **no retries added around financial side effects**. `UNKNOWN` exit policy **not invented** (CAS pinned `'EXECUTING'`, `execution_record.py:1488`) → **W-5 behind W-4, W-3 behind W-6**. | **BLOCKED / NO GRANT** |
| **K** Terraform | 4 unrestricted egress blocks (`security_groups.tf:22/52/73`, `redis-only/main.tf:58`). Redis SG names identical by default; **the collision claim stays WITHDRAWN** — conditional on same-VPC, and `redis-only` adopts a hardcoded `vpc-0f3e7b3f243fbc133` while the main root *creates* `aws_vpc.main`. `redis-only` has **no backend block → local state**, 0 variables. **`AWS-0054` false positive** — `alb.tf` `dynamic` redirect + `lifecycle.precondition` **preserved**. `terraform` and `trivy` **ABSENT** → `fmt`, `validate` and both IaC scans **BLOCKED**. No egress removed, no state touched, no apply. | **BLOCKED / NO GRANT** |

---

## 8. Commands, exit codes, and check classification

| Check | Result |
|---|---|
| `git rev-parse` / `write-tree` / `status` / `stash list` / `worktree list` / `hash-object` | **PASS** — §2 |
| `gh api /actions/runs/38061973739/jobs` | **PASS** — 8 live job names |
| `gh api /actions/runs/38132697285/jobs` | **PASS** — 3 live job names |
| Configured-vs-live job identity cross-check | **FAIL (defect found)** — **9/9 unmatched** |
| Phase 18 YAML job-name extraction (both workflows) | **PASS** — 11 jobs resolved |
| Phase 18 fixture-derivation analysis | **PASS** — self-referential at `:62`; 0 `.github` reads |
| Deployment-wrapper argument census | **PASS** — 4th surface identified |
| Phase 18 verifier suite | **NOT RUN** — hashes `5c70dbbf…` / `3dd57519…` unchanged since Phase 28's 28/28; re-running adds nothing |
| Tenant matrix | **NOT RUN** — inputs unchanged; reproduction needs the ungranted fixture change |
| Aggregate pytest | **NOT RUN** — nothing modified |
| Bandit at CI scope | **NOT RUN** — command/source/version unchanged |
| Trivy IaC (both roots) | **BLOCKED** — `trivy` absent |
| `terraform fmt -check` / `validate` | **BLOCKED** — `terraform` absent |
| Container scan | **BLOCKED** — `docker` absent |
| Kubernetes paths | **BLOCKED** — `kubectl` absent; and would not be run against a cluster regardless |
| Redis opt-in suite (5 tests) | **SKIPPED** — opt-in unset, 6379 closed |
| Gate validates the same release identity | **FAIL** — frontend ungated; verifier workflow-level only; **and Phase 18's identity map is broken** |
| Digest propagation build → deploy | **FAIL** — §6.1 |

**No diff to review — no file was changed.** No test, threshold, invariant or fail-closed guard
was weakened. **No blocked, skipped or not-run check is reported as passing.**

### 8.1 Negative controls

| Control | State |
|---|---|
| `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped` | **retained** in `staging/`, previously executed |
| `test_the_mandatory_job_assertion_is_load_bearing` (mutation) | **retained**; passes — but §4.2 shows it mutates against synthetic names, so it cannot detect the identity defect |
| Matrix anti-vacuity control | **retained and effective** — it is what surfaced the 4 failing cells |
| Strict XFAIL contract | **retained** |
| `03-deploy.yml` empty-digest refusal | **retained** |
| ALB `lifecycle.precondition` | **retained** |

---

## 9. Historical claims: confirmed, superseded, withdrawn

| Claim | Disposition |
|---|---|
| Phase 18 verifier passes 28/28 | **CONFIRMED** (Phase 28) — but **scope corrected**: validates conclusions, not identity |
| "Promote Phase 18" (Phases 28–31) | **SUPERSEDED** — §4; promotion before the mapping fix would deny all releases |
| Phase 17 admits a skipped mandatory job | **CONFIRMED** |
| `05-security.yml` three evidence defects | **CONFIRMED** |
| Verifier mis-attributes failures to container CVEs | **CONFIRMED** |
| Tenant matrix: 4/150, entitlement masks ownership | **CONFIRMED**, inputs unchanged |
| Redis SG name collision | **WITHDRAWN** (Phase 22) — remains withdrawn, not restated as a defect |
| `AWS-0054` | **CONFIRMED false positive**, control preserved |
| `scripts/deploy.sh` `latest` default | **CONFIRMED**, and **extended** — a 4th surface exists |

---

## 10. Outstanding owner decisions — smallest grant first

| # | Decision | Owner | Minimum action |
|---|---|---|---|
| **1** | **Frontend containment** | **release owner** | One answer; the build is **live** and unverified |
| **2** | **Fix Phase 18's job mapping, then promote** (§4.4) | **code owner** | Grant for `staging/phase18_release_gate.py` + a real-workflow contract test under `tests/`. **Promotion without the fix is now known-harmful.** |
| **3** | **Mandatory-job inventory** — by API `name`, incl. `D5' Real-Redis Diagnostics` and `provisioning-and-isolation` | **security owner** | A list; blocks §4.4 |
| **4** | **Tenant identity** — choose 1 of 3 expressions | **code owner** | Costs no code |
| **5** | Tenant matrix fixture grant | **code owner** | Unlocks 2 of 150 cells |
| **6** | Frontend gate `D-P15-4` + tests in `test_release_artifacts.py` | **workflow/release owner** | Extension point already exists |
| **7** | **Is `scripts/deploy.sh` / blue-green an active supported path?** | **infra/release owner** | Yes/no, then an edit grant |
| **8** | May an ungated build publish `:latest`? ECR ownership + mutability policy | **release + infra owner** | — |
| **9** | `G-2` build digest; digest propagation; task-definition pinning | **release owner** | — |
| **10** | Safety severity threshold and blocking/advisory policy (currently advisory **by `\|\| echo`**) | **security owner** | — |
| **11** | `P23-K1` evidence retention (`if: always()` on `:41`, independent `:131`); scanner version pinning | **security owner** | — |
| **12** | D7-1; **D6-D evidence before D7-2**; W-6; Bandit parity; 5 Trivy CRITICAL dispositions | code / security owners | — |
| **13** | `UNKNOWN` exit policy; retry/idempotency design; isolated Redis; required egress destinations; orphaned `stash@{1}` disposition | execution-state / infra / coordinator / code owners | — |

**Independence:** every lane is blocked only by its own grant or an absent tool. Nothing waits
on another lane's completion, so decision #1 does not gate the rest.

---

## 11. Prohibited-operation checklist

| Prohibited | Performed? |
|---|---|
| Stage, commit, push, reset, stash, pop, drop, clean, revert, merge, rebase, switch branches, rewrite history, alter worktrees | **No** — tree hash identical; 2 stashes and 1 worktree at entry **and** exit |
| Dispatch CI, create PRs, change repository settings or branch protection | **No** — only `gh api` GET / `gh run view` reads |
| Deploy, roll back, republish, invalidate CloudFront, change DNS, mutate ECR, **invoke `kubectl`**, apply Terraform, provision infrastructure, mutate production databases | **No** — `docker`/`trivy`/`terraform`/`kubectl` all absent, and none would have been pointed at a real target |
| Place exchange orders / enable live trading | **No** |
| Inspect or print secrets, credentials, tokens, DSNs, decrypted keys, sensitive headers | **No** |
| Weaken a threshold, test invariant, mandatory-job condition, tenant assertion, **anti-vacuity check**, strict XFAIL, or fail-closed guard | **No** — all preserved and enumerated in §8.1 |
| Suppress a finding to obtain green | **No** — 0 suppressions added |
| Edit governance records to manufacture permission | **No** |
| Overwrite a report, prototype, untracked file or evidence artifact | **No** — destination verified absent; both prototypes intact |
| Represent blocked / skipped / not-run / advisory as passing | **No** — §8 separates each |
| Claim tenant isolation certified | **No** |

---

## 12. Final release assessment

| Gate | Evidence for `ecd23a9e` | Status |
|---|---|---|
| Correctness | `01 PR Check` **never ran** at this SHA; 6 of 6 recent runs failed on the matrix test (§7 WS-I) | **ABSENT · RED** |
| Security scan | `05 Security` **failure** ×2 — Container ✓, **Bandit ✗, IaC ✗** | **RED** |
| Frontend gate | `needs:` = 0; **published anyway; bundle is LIVE** | **ABSENT · BREACHED** |
| Release verifier | Phase 17 **fails open**; Phase 18 **would deny all** (§4) | **NOT FIT FOR PROMOTION** |
| Backend gate | `03 Deploy` **refused** | **WORKING** |
| Image provenance | 4 surfaces, every link broken; ECR immutability **UNKNOWN** | **NOT ESTABLISHED** |
| Terraform / IaC | 5 CRITICAL, 0 accepted; scanners **absent** | **RED / BLOCKED** |
| Tenant isolation | 0 context consumers; identity unchosen; 2 of 150 cells unobservable | **NOT CERTIFIED** |
| Redis / quota | 5 tests **SKIPPED** | **UNPROVEN** |

**No release certification.** A passing local test is not a successful deployment; a
structurally correct workflow is not a live scan; a verified build checksum is not current edge
bytes; and zero foreign rows under a privileged role is not tenant isolation.

The backend gate remains the one control demonstrated effective end to end — and this phase
shows the **replacement** release gate is not yet safe to adopt.

---

*Phase 32 closed. Tree hash unchanged at entry and exit. Both stashes and the worktree intact.
0 of 14 lanes authorized; 0 governed files changed. Principal result: the Phase 18 verifier's
mandatory-job inventory is 9-for-9 mismatched against live GitHub Actions job names, its test
suite cannot detect that because the fixture derives from the configuration, and the
promotion recommendation carried by Phases 28–31 is therefore **superseded**. A fourth
deployment surface was also identified.*

***DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.***
