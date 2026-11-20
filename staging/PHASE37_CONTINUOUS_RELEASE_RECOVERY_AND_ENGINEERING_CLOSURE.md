# Phase 37 — Continuous Release Recovery and Engineering Closure

**Destination verified ABSENT before writing.** No existing report, prototype, probe, log or
generated artifact overwritten.

**0 governed files changed** — no source, test, workflow, Terraform, migration, script or
governance record. **2 new artifacts** under `staging/`. No stage, commit, push, reset, stash
operation, worktree mutation, branch switch, CI dispatch, PR, deploy, publish, rollback,
cloud/ECR/S3/CloudFront mutation, Terraform apply, database change, `kubectl` invocation, or trade.
No secret, token, DSN or credential read or printed.

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Executive outcome

Authorization is still zero, rechecked at source. Rather than restate eleven phases of findings, I
reconciled the concurrent Phase 29–36 artifacts first and then worked only the gaps. Four results
are new; one settles a disagreement between concurrent sessions; one corrects a census framing.

| # | Result | Class |
|---|---|---|
| **1** | **`infra/deploy.sh` traced in full — the one lane no phase had opened.** It is **not an image-provenance surface at all** (no image identity, no task-definition registration, no service update). But its *"Post-Deployment Verification Engine"* has **zero failure paths** and ends with a hardcoded `Readiness Score: 100%`, and its **AWS account-identity check is a warning, not a gate**. | **NEW** |
| **2** | **Three Terraform state postures, not two.** The nested root has **remote state with no lock and no explicit encryption**, in a different bucket *and* region. It is also precisely the root whose Trivy scan never executes. | **NEW** |
| **3** | **The `02-build.yml:78` disagreement between concurrent sessions is settled by measurement.** Phase 36's "LATENT/ORPHANED" is correct; Phase 32/33's unqualified "BROKEN" overstates it. | **NEW — resolves conflict** |
| **4** | **All four `scripts/deploy.sh` defects independently confirmed** at line anchors, plus one mechanism no phase stated: `set -e` is active and still cannot catch the health-check failure. | **CONFIRMED + extended** |
| **5** | **A false-green reporting pattern now has three independent instances** across unrelated subsystems. | **NEW synthesis** |

**Tests executed: 31 passed, exit 0.**

---

## 2. Fresh preflight

| Measurement | Value | vs Phase 30 baseline |
|---|---|---|
| repository root | `C:/aerora_quant_backend_updated_final1` | — |
| branch / upstream | `main` / `origin/main` | same |
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **identical** |
| `HEAD^{tree}` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| divergence | **0 ahead / 0 behind** | same |
| staged / unmerged | **0 / 0** | same |
| tracked modifications | **2 — the same two** | same |
| stashes / worktrees | **2 / 1** — neither applied, popped, dropped | same |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` | same |
| Phase 37 destination | **ABSENT** before writing | — |

Concurrent work preserved byte-for-byte: `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`
= `0c9981b2817e47b4faa915b69de204ff78f87af7`; `tests/test_drift_classification.py`
= `99a2917ac0a65761ef3dfe9299ed1df87647552a`.

### 2.1 Environment — probed this phase

| Tool / service | State |
|---|---|
| Python 3.12.10, pytest 9.1.1, PyYAML, `gh`, Bandit 1.9.4, node/npm | **AVAILABLE** |
| **`docker`, `trivy`, `terraform`, `kubectl`, `safety`, `pip-audit`** | **ABSENT** |
| **PostgreSQL** 5432 / 51805 / 51815, **Redis** 6379 | **all closed**; `REDIS*` / `AERORA_TEST_*` unset |

### 2.2 Authorization — rechecked at source

`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804` → `AP-W1D-1` … `AP-W1D-5`
**all `NOT GRANTED`**. `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` → **"Grants on record | 0"**.
No `CODEOWNERS` / `OWNERS` / `MAINTAINERS`.

The only affirmative grant in history, `AP-W1D-3`, was issued by
`PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` scoped to four named drift-classifier files and
**fully consumed at A29**. Ledger line 802 is stale with respect to that record but is not a live
grant. **No Phase 37 target falls inside it.** Per §1.2 this prompt is not a grant, and the
`"I authorize…"` blocks in prior reports are requests authored by the requesting sessions. No
governance record was edited.

| Lane | Governed targets | Status |
|---|---|---|
| Gate identity + branch policy | `staging/phase18_*` → `scripts/`, `tests/` | **NOT AUTHORIZED** |
| Correctness-evidence architecture | `01-pr-check.yml`, required-check policy | **OWNER DECISION REQUIRED** |
| Security workflow | `05-security.yml` | **NOT AUTHORIZED** + Safety policy undecided |
| Image provenance / digest binding | `02/03-*.yml`, deploy scripts, `ecs.tf`, task JSON | **NOT AUTHORIZED** |
| K8s / blue-green / `infra/` | `scripts/deploy.sh`, `blue-green-deploy.sh`, `infra/*.sh` | **NOT AUTHORIZED** + status UNKNOWN |
| Diagnostics | `scripts/require_security_scan.py` | **NOT AUTHORIZED** |
| Bandit parity | gate test + 3 migrations | **NOT AUTHORIZED** |
| D7-1 / D7-2 / workers | routers, updater, `core/state.py`, `main.py` | **NOT AUTHORIZED** + policy/evidence-blocked |
| Tenant / PostgreSQL / Redis | dependencies, RLS migrations, `redis_manager.py` | **NOT AUTHORIZED** + **BLOCKED** |
| Terraform / frontend | all roots, `06-frontend-deploy.yml` | **NOT AUTHORIZED** + **BLOCKED** |

---

## 3. Artifact reconciliation and deduplication map

Reconciled before producing anything, per §2. All Phase 29–36 artifacts exist, are **untracked**,
and are outside default discovery (`pytest.ini` → `testpaths = tests`).

| Artifact | Hash / state | Establishes | Phase 37 action |
|---|---|---|---|
| `phase31_test_gate_identity_contract.py` | present, 23 cases | corrected key→display-name mapping; **proves the fix**; branch-policy contradiction on real payloads | **re-executed** (§7) |
| `phase31_6c5660a1_runs.json`, `phase31_prcheck_runs.json` | present | real captured run records | reused |
| `phase31_workflow_invariants.py` + test | present, 21 cases, 5 violations | INV-1…INV-5 over live YAML | **not duplicated** |
| `phase18_release_gate.py` / its test | `5c70dbbf…` / `3dd57519…` | the prototype and its self-referential fixtures | **not modified, not promoted** |
| `phase17_*` pair | `4c38728f…` / `13b5e6cd…` | fail-open negative control | preserved |
| `p33_job_inventory.py` + log | present | inverse direction: 11 jobs, 0 unclassified, 0 dangling | reused |
| `PHASE30_END_TO_END_RELEASE_RECOVERY.md` | present | the only phase that executed suites (8 / 42 / bandit) | reused |
| `PHASE32`, `PHASE33`, `PHASE36` | present | identity measurement, `nosec` census supersession, A/B/C/D matrix | reconciled, §4 settles one conflict |

**Lanes already thoroughly covered — deliberately not re-investigated:** `ecs-task-definition-full.json`
and its runbook; the quota/tier fixture masking 4 of 150 tenant cells
(`tests/property/test_tenant_isolation_matrix.py`, `a60cd00c…`); the frontend bundle checksum; the
worker inventory; the `nosec` census; the Bandit nine. Evidence reuse basis: identical source
blobs, identical tool versions, unchanged commands.

---

## 4. Settling the inter-session disagreement on `02-build.yml:78`

Phase 32 §6.1 and Phase 33 §7 record *"build → immutable digest | **BROKEN**"* with no qualifier.
Phase 36 §5 link 3 downgrades it to *"**LATENT/ORPHANED** … not currently exploitable."* Measured
this phase:

```
image_digest references in .github/workflows : 4   (all inside 02-build.yml: :10, :12, :37, :81)
uses: ./.github/workflows invocations         : 0
needs.<job>.outputs references                : 0
```

`02-build.yml` declares `image_digest` as a `workflow_call` output (`:10-12`) fed from its job
output (`:37`), written at `:81`. **Nothing calls it as a reusable workflow, and `03-deploy.yml`
chains by `workflow_run`, which does not carry job outputs.** So the value is written and read by
nothing.

**Disposition: Phase 36 is correct.** The `|| echo "$IMAGE_TAG"` fallback is a **latent** trap to
fix *before* anything consumes the output, not today's active exposure. Phase 32/33's unqualified
"BROKEN" overstates it, and the distinction matters for prioritisation: fixing `:78` alone changes
nothing observable.

**One caveat that keeps it from being purely cosmetic:** `02-build.yml:82` echoes
`"=== Pushed Image Digest: $DIGEST ==="` into the build log. On a lookup failure an operator
reading that log copies a **tag** while the label says *digest*. That is a human-path exposure, not
an automated one, and it is the reason the fallback should still be removed.

The **active** provenance exposure is unchanged and elsewhere: `03-deploy.yml:168-174` resolves a
real digest, is genuinely fail-closed on empty/`None`, and then discards it because `$DIGEST` is
step-local; `ecs_deploy_and_diagnose.py:94` defaults `IMAGE_TAG` to `latest` and `:901` builds a
tag URI.

---

## 5. `infra/deploy.sh` — the one open lane, now traced

Phase 32 recorded it as *"inspected, not traced further"*; Phase 36 listed it as
*"historical index only … UNKNOWN"*; Phases 30 and 33 do not mention it. Read in full this phase.

### 5.1 It is not an image-provenance surface — census correction

`set -euo pipefail` at `:5`. The script orchestrates three siblings — `acm.sh` (certificate),
`alb.sh` (load balancer and routing), `ecs.sh` (target-group attachment) — then runs four
post-deployment checks. Measured: **`IMAGE_TAG`, `register-task-definition`, `update-service`,
`docker push`: no substantive occurrence.** It never selects, builds, registers or deploys a
container image.

**So it should not be counted among the mutable-tag deployment surfaces.** Phase 36's census row
left its default column as `—` and status `UNKNOWN`, which was the right caution; this phase can
now say definitively that it carries **no image identity at all**. The four image-bearing surfaces
remain `03-deploy.yml` → `ecs_deploy_and_diagnose.py`, `ecs-task-definition-full.json`,
`scripts/deploy.sh`, and `scripts/blue-green-deploy.sh`.

### 5.2 But its verification engine cannot fail — new

| Phase | Mechanism | Outcome on failure |
|---|---|---|
| Pre-flight gates 1–4 (`:38-75`) | AWS CLI present, Python present, STS identity resolvable, four IAM permissions | **hard `exit 1`** — 7 occurrences, correct |
| **Account identity (`:62-63`)** | `if [ "$ACCOUNT_ID" != "$EXPECTED_ACCOUNT" ]` | **`[WARNING]`, continues** |
| Check 1 ALB state (`:100-105`) | `!= active` | `[WARN]`, continues |
| Check 2 target health (`:108-112`) | — | `[INFO]` only, no assertion |
| Check 3 ECS stability (`:117-122`) | `running < desired` | `[WARN]`, continues |
| Check 4 endpoint health (`:126-130`) | `curl … \|\| echo "000"` | prints the code, **including `000`**, no assertion |
| Conclusion (`:133-140`) | — | prints **"DEPLOYMENT COMPLETE & VERIFIED"** and **`[SUMMARY] Readiness Score:  100%`** (`:139`) |

Two findings, both new:

1. **`Readiness Score: 100%` at `:139` is a hardcoded literal**, printed unconditionally after any
   number of `[WARN]`s. A run in which the ALB is not active, no targets are registered, zero of
   N tasks are running and `/health` returns `000` still prints *"DEPLOYMENT COMPLETE & VERIFIED"*
   and *"Readiness Score: 100%"*. The section is titled *"Post-Deployment Verification Engine"*;
   measured, it verifies nothing in a blocking sense — **3 `[WARN]` paths, 0 `exit 1`** in the
   verification phase.
2. **The account check is the one soft gate among hard ones.** Seven lesser conditions hard-exit,
   while provisioning into the **wrong AWS account** only warns. Given this script creates
   certificates, load balancers and target-group attachments, that is the inversion worth naming.

Two lesser observations, recorded without inflation: AWS CLI JSON is parsed by interpolating output
into a Python `-c` triple-quoted literal (`:59`, `:118-119`) — fragile rather than exploitable,
since the data originates from AWS STS/ECS; and `:14` hardcodes a developer-machine path
(`/c/aerora_quant_backend_updated_final1/python_portable/python.exe`) inside an infrastructure
orchestrator.

**Operational status remains `UNKNOWN`** — no workflow references it, which is not proof it is
dead given the documented manual runbooks. **No `aws` or `bash` command from this script was
executed.**

### 5.3 `scripts/deploy.sh` — all four defects independently confirmed

| Defect | Anchor | Confirmed |
|---|---|---|
| mutable default | `:9` `IMAGE_TAG=${2:-latest}` | yes |
| unsafe word splitting | `:21` `export $(grep -v '^#' .env.$ENVIRONMENT \| xargs)` — unquoted | yes |
| bare tag to `kubectl` | `:46-47` `kubectl set image deployment/trading-backend backend=$IMAGE_TAG` — **no registry, no repository** | yes |
| health check cannot fail | `:62` `for i in {1..5}` … `:63` `if curl -sf` … `:65` `break`; no `exit` on exhaustion; `:77` `echo "🎉 Deployment to $ENVIRONMENT complete!"` **unconditional** | yes |

**Mechanism no prior phase stated:** `set -e` **is** active (`:6`), and it still cannot catch this.
`curl -sf` sits inside an `if` condition, where a non-zero status is the tested value rather than
an error, so `errexit` is suppressed by design. The loop therefore exhausts silently and execution
falls through to the success banner. Fixing this needs an explicit `exit 1` after the loop — `set -e`
is not a substitute.

---

## 6. Three Terraform state postures, not two — new

Phase 30 §13 characterised the picture as two roots: `terraform/` remote-and-locked,
`terraform/redis-only/` local. Measured across all three roots this phase:

| Root | `backend "s3"` | `dynamodb_table` | explicit `encrypt` | Posture |
|---|---|---|---|---|
| `terraform/providers.tf` | **1** | **2** | **1** | remote, **locked**, encrypted |
| `terraform/redis-only/main.tf` | **0** | 0 | 0 | **local state** on production ElastiCache |
| **`aerora_quant_platform/infrastructure/terraform/main.tf`** | **1** | **0** | **0** | **remote, UNLOCKED, no explicit encryption** |

The nested root (185 lines, measured; Phase 36 recorded 178) declares at `:10-14`:

```hcl
backend "s3" {
  bucket = "aerora-terraform-state"
  key    = "production/terraform.tfstate"
  region = "us-east-1"
}
```

Three things follow, none previously stated:

1. **A different bucket *and* a different region** from `terraform/`'s `vyomquant-terraform-state`.
   This is a **third, independent state location**, not a variant of the first.
2. **Remote-but-unlocked is the worst of the three postures.** Local state at least cannot be raced
   by another machine. A shared S3 bucket with no DynamoDB lock invites concurrent applies that
   interleave and corrupt state, while *looking* properly managed. The backend also sets no
   explicit `encrypt`.
3. `:177` declares `aws_secretsmanager_secret "aerora"`, name `aerora/production`,
   `recovery_window_in_days = 7`. Only the **declaration** was read; **no secret value was
   resolved or printed.**

**This connects to the IaC-reachability defect.** `05-security.yml`'s `iac-scan` runs two blocking
Trivy steps sequentially with no `if: always()`; `terraform/` fails on 5 CRITICALs, so the second
step — whose `scan-ref` is exactly this nested root, with `scanners: 'misconfig,secret'` — **never
executes**. The root with the weakest state posture and a Secrets Manager declaration is precisely
the one the scanner never reaches, and its zero findings are indistinguishable from never having
run. `terraform` and `trivy` are **ABSENT**, so no scan, `fmt`, `validate`, `plan` or `init` was
attempted and **no scanner result is claimed**.

The withdrawn Redis security-group VPC-collision finding **stays withdrawn** — not revived, no new
evidence. `AWS-0054`'s false-positive analysis and the ALB `lifecycle.precondition` are preserved.

---

## 7. Gate identity, branch policy, and executed tests

### 7.1 Executed this phase

```
pytest staging/phase31_test_gate_identity_contract.py tests/test_release_artifacts.py -q
→ 31 passed in 18.91s   EXIT 0
```

23 cases from the gate identity contract (including 2 negative controls) plus the 8 tracked
release-artifact tests. Re-run rather than reused because the combined invocation had never been
executed and the tracked suite is the one that exercises real workflow structure under default
discovery.

### 7.2 The identity defect and its fix — status

| Item | State |
|---|---|
| Prototype configures job **YAML keys**; API returns **display names**; 9/9 unmatchable | **CONFIRMED** (Phases 31, 32, 36 independently) |
| Its 28 tests cannot detect it — fixtures synthesise the API name from the configured key | **CONFIRMED** |
| Corrected explicit key → expected-display-name mapping, validated against live YAML | **PROVEN** — admits all-green, refuses the real `ecd23a9e` payload, 6 non-success conclusions, unfinished, missing; 2 negative controls |
| Inventory is complete against YAML — 11 jobs, 0 unclassified, 0 dangling | **CONFIRMED** (Phase 33's self-correction) |
| Expected job set is **SHA-dependent** — `D5' Real-Redis Diagnostics` live at a PR SHA but not declared at HEAD | **CONFIRMED** (Phase 33 §4.2) — any repair must resolve expectations from the *candidate* SHA's YAML |
| **Promotion** | **CONTRAINDICATED** until identity mapping, branch policy and SHA-scoped resolution all land |

### 7.3 Branch policy — the second deadlock

`01-pr-check.yml` is `on: [pull_request]` only. A `pull_request` run reports the PR's feature
branch, never `main`, while the prototype sets `ALLOWED_BRANCHES = ("main", "master")` **and**
requires `01 PR Check`. The two settings are mutually unsatisfiable, demonstrated against the real
captured run for `6c5660a1` — an ancestor of `main` that carries both a `pull_request` and a `push`
run at the same SHA. The contrast isolates the cause: the push-triggered `05 Security` run at that
same SHA reports `main` and **is** admitted.

Main's history is linear single-parent (`ecd23a9e ← 074b772a ← 6c5660a1`), i.e. fast-forward — so
PR evidence *can* bind to an on-main SHA here, which a squash-merge repository could not do. Two
things still block Option A: the branch-policy conflict, and `01 PR Check` failing on every recent
run. **No option selected; no override or fallback added.**

### 7.4 Test discovery

`pytest.ini` → `testpaths = tests`, `python_files = test_*.py *_test.py`. All `staging/phase*`
suites are outside default collection — **70+ maintained tests CI never runs** (28 + 42, plus my
23). Remedy is promotion of genuine suites into `tests/`, **not** broadening discovery into
`staging/` (now ~190 reports, probes and generated logs). Full default collection previously
measured at **14,693 tests / exit 0**; not re-run this phase as nothing in `tests/` changed.

---

## 8. Remaining lanes — reconciled, not re-derived

| Lane | Status | Position |
|---|---|---|
| **Security workflow** | **FAIL / NOT AUTHORIZED** | `:41` SAST upload lacks `if: always()` (while `iac-scan`'s upload has it — the asymmetry is the tell); `:39` Safety `\|\| echo` cannot fail **and** sits after Bandit in the same job so it is skipped when Bandit fails — **two independent defects**; `:30` scanners unpinned; second IaC root unreachable (§6). **No authoritative Safety policy exists anywhere in the repo** — threshold not invented |
| **Diagnostics** | **NOT AUTHORIZED** | `require_security_scan.py:15-16` claims the workflow is "red only for CRITICAL findings" — false, Bandit `-ll` fails on MEDIUM; `:123-124` asserts a CRITICAL container vulnerability, yet at `ecd23a9e` the **container job passed** while Bandit and IaC failed. Refusal correct, explanation unsupported |
| **Bandit** | **FAIL (gate) / safe (code)** | 9 × B608 MEDIUM, exit 1, in three Alembic migrations; gate test scans one module — **the most heavily suppressed file in the tree (13 bare `nosec`)**. CI scope: 56 directives / 17 files, **54 bare**. Nine reviewed sites say nothing about the other 54. Reused at identical hashes and tool version |
| **D7-1** | **NOT AUTHORIZED + policy-blocked** | 3 invalid `access_token=` callers vs `load_decrypted_keys(self, user_id, exchange_id)`; `exchange.py:993` correct; **14 legitimate `access_token` sites must survive**. `market.py`'s broad `except` turns a vault failure into **silent anonymous market data**. Keys-absent policy not invented |
| **D7-2** | **NOT AUTHORIZED + evidence-blocked** | `portfolio_cache_updater.py:151` imports a non-existent module, **outside** the inner `try`, failing every cycle for every user while health reports `ACTIVE`. Needs redacted platform boot evidence; no speculative class substituted |
| **Workers** | **CURRENT SOURCE** | `ExchangeReconciliationService`, `MarketplaceExpiryWorker`: **0 references in `main.py`**. No retries wired; no UNKNOWN-exit policy invented |
| **Tenant / PostgreSQL** | **BLOCKED / NOT CERTIFIED** | `FORCE ROW LEVEL SECURITY` only in a SQL comment, zero executable statements; `dependencies.py` has 0 tenant-context references; GUC setters off the request path; app role owns the tables so policies are inert on the ORM path; `tenant_db_context.py` correct and unwired. 4 of 150 matrix cells masked by an entitlement 403, anti-vacuity control correctly refusing. **FORCE RLS not enabled in any form**; service closed |
| **Redis** | **BLOCKED** | 6379 closed; opt-in suites **SKIPPED, not passed**; strict xfail preserved; no mock presented as atomicity evidence |
| **Frontend** | **FAIL / NOT CERTIFIED** | `06-frontend-deploy.yml`: `needs:` = 0, push + `workflow_dispatch` both ungated; S3 syncs and CloudFront invalidation downstream of nothing. Size-gate ordering holds (part of the 8 passing tests). Published bundle `index-EYjpdKQA.js` / `6f12b52c…` is a **build-time** checksum; **current S3 and CloudFront bytes UNKNOWN** — not inspected, not authorized |
| **ECR immutability** | **UNKNOWN** | `aws_ecr_repository` / `image_tag_mutability`: **0 in any `.tf`**. `docs/history/AWS_DEPLOYMENT_GUIDE.md:24-28` creates five repositories with **no `--image-tag-mutability`** — documentary, **not live state**. Not queried |

### 8.1 A false-green pattern with three independent instances

Worth naming because the instances are in unrelated subsystems and each was found separately:

| Site | Reports success when |
|---|---|
| `scripts/deploy.sh:62-77` | all five health checks fail |
| **`infra/deploy.sh:133-139`** | every post-deployment check warns |
| `portfolio_cache_updater.py` → `main.py:466` | the worker fails every cycle for every user |

Same failure mode each time: the success banner is unconditional and the checks above it are
non-blocking. This is a design habit, not three coincidences, and it is why "the script printed
complete" is not evidence anywhere in this repository.

---

## 9. Commands and results

| Command | Class | Result |
|---|---|---|
| `git rev-parse` / `rev-list` / `status` / `stash list` / `worktree list` / `reflog -1` / `hash-object` | TESTED THIS PHASE | **PASS** — §2 |
| ledger + decision-packet grant recheck | TESTED THIS PHASE | **PASS** — 5 × NOT GRANTED, 0 grants |
| **`pytest staging/phase31_test_gate_identity_contract.py tests/test_release_artifacts.py -q`** | TESTED THIS PHASE | **31 passed, exit 0, 18.91 s** |
| `image_digest` / reusable-workflow / `needs.*.outputs` census | TESTED THIS PHASE | **PASS (conflict settled)** — 4 / 0 / 0 (§4) |
| `infra/deploy.sh` full trace + gate/warning census | TESTED THIS PHASE | **PASS (defects found)** — 7 `exit 1`, 3 `[WARN]`, `:139` literal (§5) |
| `scripts/deploy.sh` anchor verification | TESTED THIS PHASE | **PASS (confirmed)** — `:6`, `:9`, `:21`, `:46-47`, `:62-65`, `:77` |
| three-root Terraform backend census | TESTED THIS PHASE | **PASS (new finding)** — §6 |
| nested root `main.tf` read (185 lines) | TESTED THIS PHASE | **PASS** — backend `:10-14`, secret `:177` |
| port probes 5432 / 51805 / 51815 / 6379 | TESTED THIS PHASE | **all closed** |
| Phase 18 / 17 verifier suites (28 / 42) | REUSED VERIFIED EVIDENCE | hashes unchanged |
| Bandit at CI scope (exit 1, 9 × B608 MEDIUM) | REUSED VERIFIED EVIDENCE | command, source, version unchanged |
| `p33_job_inventory.py` (11 jobs, 0 unclassified) | REUSED VERIFIED EVIDENCE | inputs unchanged |
| Default collection (14,693 / exit 0) | REUSED VERIFIED EVIDENCE | nothing in `tests/` changed |
| Trivy both IaC roots · container scan · `terraform fmt`/`validate`/`plan` | **BLOCKED** | tools absent |
| Tenant-isolation runtime matrix · real-Redis suites | **BLOCKED** | services closed |
| Redis opt-in suite | **SKIPPED** | opt-in unset, port closed |
| `01 PR Check` at `ecd23a9e` | **NOT RUN** | `pull_request`-only, undispatchable; dispatch unauthorized |
| ECS / K8s / blue-green execution; ECR query; S3 / CloudFront read | **NOT RUN** | not authorized; `kubectl` absent |

**No diff to review — no governed file changed.** No threshold lowered, no suppression added, no
`continue-on-error` introduced, no test weakened, no anti-vacuity control or strict xfail altered,
no blocked/skipped/not-run check recorded as a pass. `03-deploy.yml`'s fail-closed digest refusal
intact.

---

## 10. Owner decisions in dependency order

1. **Correctness-evidence architecture (A/B/C/D).** Gates the verifier, the frontend gate and all
   correctness evidence. Option A is viable here *because* main is fast-forward, but needs the
   branch-policy fix and triage of the failing `01 PR Check`. Option C provisions a database per
   push.
2. **Authorize the verifier's three-part repair** — key→display-name mapping, branch policy, and
   SHA-scoped expectation resolution — **with YAML-contract tests replacing the masking fixtures.**
   Promotion today deadlocks deployment twice over.
3. **Safety: blocking or advisory, and at what severity.** No policy exists in the repository.
   Both execution defects need fixing either way; the policy only decides failure semantics.
4. **Evidence retention** — `if: always()` on the SAST upload; make the second IaC root run
   independently. No policy decision needed; smallest safe change in the backlog.
5. **Bind the digest end to end**; remove the `:78` fallback **before** wiring a consumer (§4).
6. **Is the Kubernetes / blue-green path live?** Determines whether §5.3's defects are live
   exposure or dead code. One sentence from the platform owner.
7. **`infra/deploy.sh`: is it a supported path?** If yes, its verification engine needs real
   failure paths and its account check needs to become a gate (§5.2).
8. **Declare ECR `image_tag_mutability`, or supply the live setting.**
9. **The third Terraform state location** — accept or remediate unlocked remote state on
   `aerora-terraform-state` (§6).
10. **Disposition of 54 bare `nosec`** in CI scope; the nine B608 sites.
11. **Keys-absent market-data policy** (D7-1); **redacted vault boot evidence** (D7-2).
12. **Frontend incident containment.** Not performed or initiated.
13. **Disposable PostgreSQL, Redis, Trivy/Docker/Terraform** — unblocks every certification lane.

---

## 11. Final integrity and status matrix

**Files written: 2** — `staging/phase37_tests.txt` (captured output) and this report. Both new, both
untracked, both under `staging/`.

| Check | Result |
|---|---|
| `HEAD` / `origin/main` / divergence | `ecd23a9e…` / identical / **0-0** |
| `HEAD^{tree}` | `4b45a763…` — **unchanged entry and exit** |
| staged / unmerged / stashes / worktrees | **0 / 0 / 2 / 1** |
| tracked modifications | **2 — the same two, blobs identical** |
| prototypes, reports, probes, logs | **none overwritten, none moved, none promoted** |
| secrets in any artifact | **none** — the nested root's Secrets Manager entry was read as a **declaration**; no value resolved |
| prohibited Git / CI / cloud / DB / trading operation | **none** |

| Dimension | Status |
|---|---|
| Authorization | **NOT AUTHORIZED** (0 grants, rechecked at source) |
| Source modifications | **NOT AUTHORIZED** (0 governed files) |
| Gate identity contract | **PARTIAL** — defect confirmed, **fix proven in staging**, not installed |
| Branch / correctness-evidence architecture | **BLOCKED** — owner decision 1 |
| Security workflow completeness | **FAIL** — one IaC root unreachable, SAST evidence lost, Safety skipped *and* non-blocking |
| Image provenance | **NOT ESTABLISHED** — digest verified then discarded; `:78` fallback **latent** (§4) |
| ECR immutability | **UNKNOWN** — undeclared in tree; documentary evidence only |
| ECS deployment safety | **PARTIAL** — digest-existence check fail-closed; deploys by tag |
| Kubernetes / blue-green safety | **FAIL** (defects confirmed) · operational status **UNKNOWN** |
| `infra/deploy.sh` | **FAIL** — verification engine has no failure path; account check is a warning; **not** an image surface |
| Frontend publication safety | **FAIL** — no release-evidence gate; current published bytes **UNKNOWN** |
| Bandit parity | **FAIL** — gate red; 9 sites safe but unannotated; 54 bare suppressions undispositioned |
| Key-vault correctness (D7-1) | **FAIL** — 3 invalid callers; silent anonymous market data |
| Worker lifecycle (D7-2, inventory) | **FAIL** — false-green `ACTIVE`; 2 workers unstarted |
| Tenant isolation | **BLOCKED / NOT CERTIFIED** |
| PostgreSQL / Redis integration | **BLOCKED** — services closed; skips are not passes |
| Terraform / Trivy validation | **BLOCKED** — tools absent; **new**: 3 state postures, one unlocked-remote |
| Test execution | **PASS** — 31 passed, exit 0 |
| Default discovery | **PARTIAL** — 70+ maintained tests outside CI |
| Release readiness | **BLOCKED — DO NOT RELEASE** |
| Live trading certification | **NOT CERTIFIED / OFF** |

---

*Phase 37 closed. Tree unchanged at entry and exit; both stashes and the worktree intact. 0 of 10
lanes authorized, so 0 governed files changed. The one previously untraced lane is now traced, one
inter-session disagreement is settled by measurement, and the Terraform state picture is corrected
from two postures to three.*

***A script that prints "complete" is not a deployment. A hardcoded 100% is not a readiness score.
An unlocked remote state is not managed state. A skipped scan is not a pass, and absent tools are
not passed scans.***
