# Phase 33 — Deployment Script Failure Semantics, Release-Gate Identity, Engineering Closure

**Destination verified ABSENT before writing.** **0 production files · 0 workflows · 0 files under
`tests/` · 0 Terraform · 0 migrations · 0 deployment scripts · 0 governance records · 0 approvals
created · 0 CI dispatched · 0 cloud mutations · 0 deployments.** New files are `staging/phase33_*`.

> ## Executive outcome
>
> **Authorization remains zero.** Fresh read of `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`
> (now 56,397 bytes — a concurrent session edited it): **0 bare `GRANTED`, 5 `NOT GRANTED`.** No
> governed target edited. HEAD `ecd23a9e…`, tree **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** —
> recomputed, and identical to the Phase 31/32 value.
>
> **1. The blue-green audit Phase 32 skipped is done, with five conclusive executed findings.**
> `scripts/blue-green-deploy.sh` (377 lines) was run against stateful stubs. The worst two:
> **`switch` cuts production traffic with zero health probes and exits 0**, and **`promote`
> destroys the only rollback target** — it overwrites blue's image with green's and scales green
> to 0, so neither slot then holds the previous version.
>
> **2. A correction to my own Phase 18 prototype.** Measured from live YAML: **all 11 jobs declare
> an explicit `name:`, so `job_id == display` for 0 of 11.** The Phase 18 gate configures **job
> IDs** while the API reports **display names** — installed as written it would find none of its 9
> mandatory jobs and **refuse every run, including a fully green one.** My 28 passing tests did not
> catch this because the fixtures generated job names from the verifier's own configured keys —
> precisely the self-referential fixture defect this brief warns about. **The Phase 18 prototype is
> NOT ready to copy.**
>
> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

---

## 1. Preflight and integrity

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| HEAD tree | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** (recomputed, not assumed) | unchanged |
| Upstream · ahead/behind | `origin/main` · `0 0` | unchanged |
| Staged / unmerged | **0 / 0** | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Untracked (`-uall`) | 177 | 183 (+6, all `staging/phase33_*`) |
| Worktrees / stash | 1 / **2** | 1 / **2 — preserved, not inspected, not applied** |

Concurrent work preserved: `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`,
`tests/test_drift_classification.py`. The ledger itself changed size between Phase 32 and this
phase (55,999 → 56,397 bytes) — a concurrent edit, **left untouched**, and re-read rather than
assumed. No prior report, log or probe overwritten.

**Tooling:** Python 3.12.10 · pytest 9.1.1 · PyYAML 6.0.3 · Bandit 1.9.4 · **bash (WSL) PRESENT** ·
node/npm/aws CLI present (aws **never invoked**). **ABSENT → BLOCKED:** docker, trivy, terraform,
kubectl, safety, pip-audit, psql, redis-cli.

---

## 2. Authorization

```
staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md   56,397 bytes
  bare GRANTED = 0        NOT GRANTED = 5     (AP-W1D-1 … AP-W1D-5)
```

| Lane | Targets | Classification |
|---|---|---|
| Deployment scripts | `scripts/deploy.sh`, `blue-green-deploy.sh`, `infra/deploy.sh` | **NOT AUTHORIZED** → READ-ONLY + stubbed proof |
| Release gate | `phase18_release_gate.py` → `scripts/`, workflows | **NOT AUTHORIZED** (`D-P15-4`) |
| Correctness trigger | `01-pr-check.yml` | **OWNER DECISION REQUIRED** (`D-P15-3`) |
| Security workflow | `05-security.yml` | **NOT AUTHORIZED** |
| Safety threshold | policy | **OWNER DECISION REQUIRED** — no in-repo authority; **not invented** |
| Image provenance | `02-build.yml`, `ecs.tf`, helpers | **NOT AUTHORIZED** |
| Bandit parity | gate test + 3 migrations | **NOT AUTHORIZED** (`D-P15-1`) |
| Vault / workers / retry / tenant / Redis / frontend | respective targets | **NOT AUTHORIZED** |
| Tenant-RLS runtime · Redis · Terraform · Trivy | — | **BLOCKED BY ENVIRONMENT** |

The ledger was **not** modified. Nothing was inferred from this prompt, a prior phase, a
recommendation, a passing prototype suite, or silence.

---

## 3. Blue-green deployment audit — the Phase 32 gap, now closed

Harness `staging/phase33_bluegreen_harness.sh` → log `staging/phase33_bluegreen_harness.txt`.
Script under test sha1 `5791dce3c53ab620c9890ca11d739550ad6bc8e9`, 377 lines, **read only**.
`kubectl`, `curl`, `sleep` are stubs on `PATH`; execution is confined to `mktemp -d`.
**No cluster, no network, no cloud, no traffic change, no real rollback.**

| # | Scenario | Executed observation | Finding |
|---|---|---|---|
| 1 | `deploy` with the **first** health probe failing | exit 1 after **exactly 1 probe** | **`MAX_RETRIES=10` performs ZERO retries** |
| 2 | `deploy` with all probes succeeding | **10 consecutive** successes required; 10 × `sleep 2` | the "retry" loop is inverted (§3.1) |
| 3 | **`switch` standalone** | traffic patch issued, **0 health probes**, **exit 0** | **traffic cuts to an unvalidated pod and reports success** |
| 4 | `promote` with green active | blue image overwritten with green's; **green scaled to 0**; exit 0 | **the rollback target is destroyed** |
| 5 | `rollback` with blue at 0 ready replicas | **"Rollback complete" printed, exit 0** | **success claimed with no ready pods** |

### 3.1 The inverted validation loop (`:130-145`)

```bash
while [ $RETRY -lt $MAX_RETRIES ]; do
    if kubectl exec ... curl -sf .../health; then
        log_success "Health check passed ($((RETRY+1))/$MAX_RETRIES)"
        RETRY=$((RETRY+1)); sleep 2
    else
        log_error "Health check failed"; exit 1
    fi
done
```

`RETRY` increments **only on success** and the first failure exits 1. The names say *retry*; the
semantics are **"must pass 10 times consecutively"**. Two consequences, both measured: a single
transient probe failure on a warming pod aborts the deployment (scenario 1), and the success path
costs ~20 s of mandatory sleeps (scenario 2). This is the exact inverse of `deploy.sh`, which never
fails at all.

### 3.2 `switch_traffic` does not validate (`:160-194`)

`deploy_green` calls `validate_green` at `:100`, but `switch_traffic` only reads the current colour
and patches the service selector. `switch` is a **separate subcommand** (`:343`), so an operator can
invoke it at any time — after green has degraded, or without ever running `deploy`. Scenario 3
confirms: **0 health probes, patch issued, exit 0.**

Its "verification" is also near-tautological: it patches `/spec/selector/color` then reads
`/spec/selector/color` back. That confirms kubectl applied the patch; it says nothing about whether
green serves traffic.

### 3.3 `promote` removes the ability to roll back (`:231-273`)

`set image` blue ← green's image → `scale` blue → `rollout status` (this one **does** gate) →
patch selector to blue → **`scale deployment/…-green --replicas=0`**. After promotion blue holds the
*new* version and green holds nothing. `rollback()` only patches the selector back to blue — which
is now the new version — so **there is no in-script path to the previous version**. The script never
uses `kubectl rollout undo`.

### 3.4 `rollback` never checks whether the target can serve (`:200-228`)

Scenario 5: with blue at 0 replicas / 0 ready, rollback patches the selector, confirms the label,
prints "✅ Rollback complete" and exits 0. The two `readyReplicas` reads in the call log belong to
`show_status`, which runs **after** the success claim and only prints.

### 3.5 What is better than `deploy.sh`

Recorded for balance: `deploy_green` **requires** an image argument and exits 1 when it is empty
(`:68-71`) — no `latest` default. `validate_green`'s readiness check fails closed (`:147-151`).
`rollout status` is unguarded, so `set -e` applies. The defects are in *validation semantics* and
*rollback safety*, not uniformly across the script.

### 3.6 Image reference — same shape as `deploy.sh`

`:86-88` `kubectl set image deployment/$GREEN_DEPLOYMENT backend=$IMAGE_TAG`. Whatever the operator
passes is used verbatim as the container image. **No registry/repository validation, no digest
requirement, no rejection of a bare tag.** Per §2 this is not authorized to change; the policy
question (fully-qualified reference vs immutable digest) is an owner decision and was **not
invented**.

---

## 4. Release-gate job identity — correcting my own prototype

`staging/phase33_job_identity.py` → `staging/phase33_job_identity.txt`, resolving each job's API
display name from live YAML via `job_spec.get("name", job_id)`.

### 4.1 Measured inventory — 11 jobs, 0 identity matches

| Workflow | job_id | API display name |
|---|---|---|
| `05-security.yml` | `sast-scan` | `Bandit SAST & Dependency Security Scan` |
| | `container-security` | `Trivy Container Vulnerability Scanner` |
| | `iac-scan` | `Trivy IaC Misconfiguration & Secret Scan` |
| `01-pr-check.yml` | `static-audit` | `Code Quality & Static Audit` |
| | `format-check` | `Format Backlog (report only)` — **JOB `continue-on-error: true`** + 2 such steps |
| | `unit-tests` | `Unit & Integration Tests` — `needs=static-audit` |
| | `paper-mode-tests` | `Execution Tests (production posture)` — `needs=static-audit` |
| | `database-tests` | `Database Tests (PostgreSQL)` — `needs=static-audit` |
| | `provisioning-and-isolation` | `Provisioning, Privilege Hardening & Tenant Isolation` — `needs=static-audit` |
| | `docker-check` | `Docker Build Validation` — `needs=static-audit` |
| | `frontend-tests` | `Frontend Build & Unit Tests` |

**`job_id == display` for 0 of 11.** Every job declares an explicit `name:`.

### 4.2 The consequence, stated plainly

`phase18_release_gate.REQUIRED_WORKFLOWS` configures **job IDs**:
`['sast-scan','container-security','iac-scan']` and
`['static-audit','unit-tests','paper-mode-tests','database-tests','docker-check','frontend-tests']`.
The Actions jobs API reports display names. **Intersection: empty.**

Installed as written, the gate would raise *"contains no job named 'sast-scan'"* for **every**
candidate — including one where all 9 mandatory jobs genuinely passed. That is a deadlock, not a
safe default, and it independently reproduces Phase 37B's result from live YAML.

**Why my 28 Phase 18 tests missed it.** `phase18_test_release_gate.py` builds job fixtures from
`gate.REQUIRED_WORKFLOWS` itself, so the fake API returns display names equal to the configured
keys. The suite validated the policy against its own vocabulary and could not detect a vocabulary
mismatch with reality. **This is the self-referential fixture defect the brief names, and it was in
my own work.** Phase 18's claim that landing the gate "is a copy, not a design exercise" was wrong
and is withdrawn.

The fix is one line of resolution — `expected = spec.get("name", job_id)` sourced from the YAML **at
the candidate SHA** — plus replacing the fixtures with real-YAML contract tests. **Not implemented:
`phase18_release_gate.py` is a staging prototype but the repair belongs with the authorized gate
work, and fixing a prototype in isolation would re-create the same "tested but not installed"
illusion.** Recorded as the first obligation of the `D-P15-4` grant.

### 4.3 Mandatory/advisory classification — holds up

9 mandatory (3 + 6) and 2 advisory, consistent with the brief's expectation. `format-check` is
correctly advisory: **job-level** `continue-on-error: true` and nothing depends on it.
`provisioning-and-isolation` has no `continue-on-error` and is a real gating job in CI; whether it
is *release*-mandatory remains an **owner decision** and was not promoted into policy.

---

## 5. Lanes reconciled without re-derivation

Source hashes unchanged since Phase 32, so prior evidence stands and expensive checks were **not**
repeated (Bandit whole-tree ≈96 s, W-6 probe, D7-1 bind probe, QUOTA-01 suite).

| Lane | State | Class |
|---|---|---|
| `scripts/deploy.sh` false green | exit 0 + success banner with all 5 health checks failed; missing `.env.staging` tolerated | **executed** (Phase 32), REUSED |
| `infra/deploy.sh` | none of 4 post-deploy checks can fail; 2 WARN-only, 2 INFO-only; readiness is a literal; wrong-account warns; permission preflight fails closed | source-verified; **not executed, deliberately** |
| Security workflow INV-1/2 | SAST upload lacks `if: always()`; Safety `\|\| echo` and sequenced after Bandit | source-verified |
| Image provenance INV-3/4/5 | tag-as-digest fallback; ungated `latest` push; `image_digest` exported and **never consumed** (`03-deploy.yml` has zero references) | source-verified |
| Bandit parity | CI exit 1 / 9 MEDIUM B608 vs gate test exit 0 / 3 passed (single-module scope) | REUSED EVIDENCE |
| Vault D7-1 | 3 `access_token=` sites vs 2-positional signature; `market.py` can serve authenticated users anonymous data. Missing-keys policy **not invented** | source-verified |
| Worker lifecycle D7-2 | `portfolio_cache_updater.py` `a74d7644…` unchanged | REUSED |
| Financial retry / W-6 | `.retryable` read only by a log line; **no retry added**, `UNKNOWN` preserved | source-verified |
| Tenant / RLS | **NOT CERTIFIED** — no PostgreSQL; `FORCE RLS` not proposed in isolation | **BLOCKED** |
| Redis / workers | **NOT CERTIFIED** — no Redis/Docker; 5 real-Redis tests remain **SKIPPED, not passed** | **BLOCKED** |
| Terraform roots / Trivy | terraform and Trivy absent; no `init`, no state access, no plan, no apply | **BLOCKED** |
| Frontend publication | ungated; no live bytes fetched — current production bytes **UNKNOWN** | source-verified |

---

## 6. Verification matrix

| Command | Exit | Version | Result | Artifact |
|---|---|---|---|---|
| `bash staging/phase33_bluegreen_harness.sh …` | 0 | WSL bash | **5 scenarios, 5 unsafe behaviours confirmed** | `phase33_bluegreen_harness.txt` |
| same, stateless-stub attempt | 0 | | scenarios 3 & 5 inconclusive — fixed by a stateful stub (§7) | superseded |
| `python staging/phase33_job_identity.py` | 0 | PyYAML 6.0.3 | **11 jobs, 0 identity matches** | `phase33_job_identity.txt` |
| ledger grant scan | 0 | | 0 GRANTED / 5 NOT GRANTED | §2 |
| `git rev-parse HEAD^{tree}` | 0 | | `4b45a763…` recomputed | §1 |

**BLOCKED / NOT RUN:** Trivy, Terraform, kubectl, PostgreSQL tenant isolation, Redis integration,
Safety, pip-audit, Docker/ECR provenance, live gate integration, `01 PR Check` dispatch,
`infra/deploy.sh` execution (deliberate), frontend live bytes, npm frontend build (not reached).
**No missing tool counted as a pass; no skip counted as a pass.**

---

## 7. Harness limitation found and corrected mid-phase

The first blue-green run used a **stateless** `kubectl` stub. The script patches the service
selector then re-reads it to verify, so the stub returned the pre-patch colour and scenarios 3 and 5
exited 1 **for a harness reason**, masking what they were testing. Had I reported that run, scenario
5 would have looked like correct fail-closed behaviour.

Fixed by persisting the patched colour to a state file and answering selector reads from it. Both
scenarios then became conclusive and **both reversed**: `switch` exits 0 with no validation, and
`rollback` prints "Rollback complete" with 0 ready replicas. Recorded because a stub that cannot
model the state under test produces confidently wrong conclusions.

---

## 8. Owner decisions and minimum grants, in dependency order

| # | Decision | Minimum grant | Note |
|---|---|---|---|
| **1** | Blue-green validation + rollback safety | edit `scripts/blue-green-deploy.sh` | fix the inverted loop; validate before `switch`; keep a recoverable previous version across `promote`; verify rollback target readiness. **5 executed reproductions available to verify against** |
| **2** | `scripts/deploy.sh` failure path | edit `scripts/deploy.sh` | exit non-zero on health exhaustion; gate the banner; require an explicit image; fail on a missing env file |
| **3** | `infra/deploy.sh` verification | edit `infra/deploy.sh` | make required checks fail closed; account identity a hard precondition; compute or delete the readiness figure |
| **4** | Release gate | `D-P15-4` **+** `D-P15-3` | **first obligation: the §4.2 identity fix and real-YAML fixtures.** Do not install the prototype as written |
| **5** | Security evidence + enforcement | edit `05-security.yml` | `if: always()` on the SAST upload; remove the Safety `\|\| echo`; keep IaC roots independently observable |
| **6** | Safety blocking threshold | policy decision | **no in-repo authority exists** |
| **7** | Image provenance | edit `02-build.yml`, `ecs.tf`, helpers | drop the tag fallback, validate `sha256:`, consume the digest, stop the ungated `latest` push |
| **8** | `D-P15-1` Bandit parity | edit gate test + 3 migrations | whole-tree scope, negative control, keep the AST guard, 9 individual dispositions |
| 9 | Image reference contract | policy | fully-qualified tag vs digest — governs items 1, 2 and 7 |
| 10 | Tenant/RLS · Redis · Terraform | grants **plus** PostgreSQL / Redis / terraform + Trivy | environment first |

---

## 9. Final integrity verification

| Check | Result |
|---|---|
| `HEAD` / tree / branch / upstream | `ecd23a9e…` / `4b45a763…` / `main` / `0 0` — **unchanged** |
| Staged / unmerged | **0 / 0** |
| Tracked files modified by this phase | **0** |
| Governed paths (`.github/ scripts/ terraform/ tests/ backend_app/ infra/`) | only `tests/test_drift_classification.py` — the other workstream's pre-existing change |
| `git diff --check` | clean |
| Stashes / worktrees | 2 / 1 — intact |
| Prior reports / logs / probes | none overwritten; destination verified absent |
| Secrets | none printed. Harness env file is `FAKE_SETTING=dummy_value`; gate-suite token is the literal `"fake-token-not-a-credential"` |
| **Prohibited operations — none performed** | no add/commit/push/reset/stash/pop/drop/clean/revert/merge/rebase/branch change/history rewrite/worktree change; no CI dispatch, PR, settings or branch-protection change; no frontend publish, CloudFront invalidation, DNS, ECR mutation, task-definition registration, deploy or rollback; no real `kubectl`; no `terraform init/apply`; no migration, ledger row, role or schema change; no worker activation; no exchange order or live trading; `aws` CLI present but **never invoked** |

**Files created — 6:** `phase33_bluegreen_harness.sh`, `phase33_bluegreen_harness.txt`,
`phase33_job_identity.py`, `phase33_job_identity.txt`, this report. (Harness temp dirs removed by
their own `trap`.)

**Implemented: nothing — no lane authorized. Executed proofs: blue-green (5 scenarios), job identity
(11 jobs). Reused evidence: Phase 32 `deploy.sh` proof and the Bandit/W-6/D7-1 findings at unchanged
hashes. Everything else is source-verified or BLOCKED. No runtime, CI, or live-system certification
is claimed.**

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**
