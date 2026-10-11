# Phase 28 — Security Evidence, Release Integrity: Prerequisite Recheck and New Independent Evidence

**Destination verified ABSENT before writing. No prior report, prototype, stash or worktree altered.**

> ## Outcome: grants unchanged → **0 governed files edited**. But this phase is not a repeat of
> Phase 27: it produces **five new source-verified findings** and **corrects two of my own earlier
> conclusions**, one of which would have caused active harm if implemented in the order I proposed.
>
> `VER` Grant recheck: ledger `**GRANTED**` = **0**; `:802` = `NOT GRANTED`; no newer authorization
> delta. Entry state byte-identical to Phase 27.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

---

## 1. Entry / exit integrity — identical

| Property | Entry | Exit |
|---|---|---|
| Root / branch / `HEAD` | `C:/aerora_quant_backend_updated_final1` / `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / divergence | `ecd23a9e…` / **`0 0`** | unchanged |
| `git reflog -1` | `ecd23a9e` | **unchanged** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |

The only tracked modifications remain `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (` M`) and the
other session's `tests/test_drift_classification.py` (` M`) — preserved, not staged, not reverted.
No index-, ref-, stash-, worktree- or object-database-mutating command was used for measurement.

**Authorization status: every lane `NOT GRANTED`.** No lane was edited; nothing below is described
as implemented.

---

## 2. NEW — the unscanned IaC root declares secret infrastructure

Phase 27 established that `aerora_quant_platform/infrastructure/` (37 scannable files) is never
scanned because its blocking Trivy step has no failure-independent condition and the `terraform/`
scan ahead of it always fails. **This phase establishes what is in that tree.**

`VER` `aerora_quant_platform/infrastructure/terraform/main.tf`, 185 lines:

```
:2    terraform {
:10     backend "s3" {                                  ← remote state in S3
:17   provider "aws" {
:167  resource "aws_cloudwatch_log_group" "aerora" {
:177  resource "aws_secretsmanager_secret"  "aerora" {   ← secret infrastructure
```

The nested scan is configured for **`misconfig,secret`**.

> **So a Trivy invocation configured to detect secrets has never executed over the one tree in this
> repository that declares an AWS Secrets Manager secret and an S3 state backend.** This is not a
> coverage gap in the abstract — the scanner class that never runs is precisely the one matched to
> the content it would inspect.

This materially raises G-1's priority beyond Phase 27's framing and is the strongest single argument
for granting it first.

---

## 3. NEW — the Safety check is defective twice over, verified verbatim

Phase 27 recorded the `|| echo` failure-swallowing. This phase confirms the **second**, independent
defect: step ordering. `05-security.yml` lines 35–45, three consecutive steps in one job, **none
carrying any condition**:

```
35  - name: Run Bandit Scan
36    run: bandit -r backend_app -ll -f json -o reports/bandit-report.json
38  - name: Run Safety Check
39    run: safety check --json --output reports/safety-report.json || echo "Safety advisory logged"
41  - name: Upload Security Reports
42    uses: actions/upload-artifact@v4
```

Because Bandit exits 1 (9 × B608), default step semantics skip everything after it. Therefore:

| Step | When Bandit fails | When Safety runs |
|---|---|---|
| `:38` Safety | **skipped — the dependency scan never executes** | `\|\| echo` forces exit 0 — **cannot fail the job** |
| `:41` Upload | **skipped — all evidence discarded** | — |

> **Safety is ineffective in both branches: skipped when Bandit fails, non-blocking when it runs.**
> A fix that only removes `|| echo` would still leave it unreachable on every run where Bandit is
> red — which is every run today. **Both defects must be addressed together**, which Phase 27's
> request treated as two separable items.

---

## 4. NEW — promoting either verifier today would deadlock the push release path

This is the most consequential finding of the phase, and it **corrects my own Phase 24/27
prioritisation**.

`VER` Measured:

| Fact | Value |
|---|---|
| `phase17_frontend_release_gate.py:80` | `REQUIRED_WORKFLOWS = ("05 Security", "01 PR Check")` |
| `phase18_release_gate.py:62` | `REQUIRED_WORKFLOWS: dict` — run-level **and** job-level both required |
| `01-pr-check.yml` triggers | `pull_request` = **1**, `push` = **0**, `workflow_dispatch` = **0** |

A SHA reaching `main` by direct push **can never have an `01 PR Check` run**. Both verifiers require
one. Therefore:

> **Promoting either verifier into the push-triggered release path would refuse every push-triggered
> deployment permanently. That is a deadlock, not a gate.**

**Correction to my own sequencing.** My Phase 24 and Phase 27 authorization tables ordered verifier
promotion (G-4) and the frontend gate (G-5) *ahead of* the correctness-route decision (G-6), with
G-5 merely "depends on G-6". That ordering was wrong: **G-6 is a hard prerequisite, not a
dependency to note.** Granting G-4/G-5 first would produce a dead pipeline. This independently
confirms the Phase 30 audit's deadlock finding.

### 4.1 Correction — Phase 18 *does* have job-level verification

My Phase 23 report concluded that job-level checking was "absent in **both** implementations"
(`/jobs` refs = 0 in each). **That was measured against Phase 17 and an older Phase 18 state and is
now outdated.** `phase18_release_gate.py` (311 lines) states its policy explicitly:

```
:26  THE PHASE 18 POLICY — run-level AND job-level, both required
:28  For a candidate SHA, for every workflow in REQUIRED_WORKFLOWS:
:31  3. every job named in MANDATORY_JOBS for that workflow exists in that run AND concluded …
```

and it carries a test, `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`, that
**demonstrates the Phase 17 defect**. So Phase 18 supersedes Phase 17 on the job-level axis, and
**Phase 18 is the correct promotion candidate** — not Phase 17, which several of my earlier reports
recommended.

### 4.2 New detail worth carrying: the `workflow_run` checkout trap

`phase18_release_gate.py:42-44` records a hazard none of my reports captured: a
`workflow_run`-triggered run checks out the **default branch** by default, so
`actions/checkout` must pin `ref: ${{ github.event.workflow_run.head_sha }}` or **the gate validates
one commit while the job publishes another**. Any gate wired via `workflow_run` must carry that pin,
or exact-SHA verification is defeated at the checkout rather than in the verifier.

---

## 5. NEW — a third deploy path defaults to `latest`, and ECR immutability is unknowable from the tree

`VER` `scripts/deploy.sh`:

```
:9    IMAGE_TAG=${2:-latest}
:46   kubectl set image deployment/trading-backend \
:47     backend=$IMAGE_TAG \
```

A **Kubernetes** deployment path that defaults to the mutable `latest` tag and never resolves a
digest. This is distinct from the ECS path (`ecs_deploy_and_diagnose.py:94`) and the frontend path,
so **three independent release routes default to or pin a mutable alias.** Per the brief I did not
assume this script is obsolete; I note only that its *operational referencing* is unestablished and
is an owner question.

`VER` ECR immutability:

```
image_tag_mutability references across terraform/ and aerora_quant_platform/infrastructure/ : 0
aws_ecr_repository   references across the same roots                                        : 0
```

The ECR repository is **not declared in Terraform at all** — it is managed outside this repository.
**Therefore tag immutability cannot be established from the repository tree, and I record it as
UNKNOWN.** I do not claim it is mutable, and I did not query AWS.

---

## 6. Evidence reused with provenance, not re-run

Inputs verified unchanged (HEAD, file hashes, tool versions, configuration), so per §13 these were
**not** repeated:

| Evidence | Value | Provenance |
|---|---|---|
| Tree-wide Bandit | exit 1, **9 × B608 MEDIUM**, bandit 1.9.4, `-r backend_app -ll` | Phase 24, re-confirmed inputs |
| `test_perf.py` suppressions | two **inert** `# nosec: B608` at `:18`, `:27`; not among the nine | Phase 27 |
| Phase 17 prototype | 42 pass when named explicitly | Phase 23 |
| Frontend-gate structural assertions | 7/7 with negative controls | Phase 19 |
| Bandit parity negative control | 4/4 | Phase 19 |
| Quota / debounce / drift | 60 passed 5 skipped · 9 passed 1 xfailed · 140 passed | Phase 19/24 |
| Terraform Redis collision | **premise withdrawn** — separate VPCs, `redis-only` uses local state | Phase 26/27 |

**No new test was executed this phase**, because every lane that would require one is ungranted and
the read-only findings above are source measurements, not test outcomes. **Nothing is reported as
passing that did not run.**

---

## 7. Status matrix

| Lane | Status | Evidence class |
|---|---|---|
| G-1 security evidence / IaC coverage | **NOT GRANTED** — scope now larger (§2, §3) | source-verified |
| G-2 Safety policy | **owner decision outstanding**; now a **two-part** fix (§3) | source-verified |
| G-3 build digest fallback | **NOT GRANTED**; remedy proven in-repo at `03-deploy.yml:168-172` | source-verified |
| G-4 verifier promotion | **NOT GRANTED and must not precede G-6** (§4); **Phase 18 is the candidate**, not Phase 17 | source-verified |
| G-5 frontend gate | **NOT GRANTED**; blocked behind G-6 | structural tests (prior) |
| G-6 correctness route | **OWNER DECISION — now a hard prerequisite** | source-verified |
| Image provenance | **NOT ESTABLISHED** — three paths default to a mutable alias; **ECR immutability UNKNOWN** (§5) | source-verified; **live ECR/ECS unverified** |
| Bandit parity | **NOT GRANTED** | scanner, reused |
| D7-1 / D7-2 / W-6 | **NOT GRANTED**; D6-D remains D7-2's prerequisite | source-verified |
| Terraform / Trivy | **BLOCKED — tooling absent.** No scan run or claimed | not run |
| Tenant / PostgreSQL / Redis | **BLOCKED — services absent. NOT CERTIFIED** | source-verified only |

---

## 8. Prioritised owner decisions — reordered by this phase's findings

> **1 · G-6 correctness route — now FIRST, because it unblocks and de-risks G-4/G-5.**
> *"Releasable commits obtain correctness evidence via ⟨**PR-only advancement of `main`**⟩ /
> ⟨**a dedicated push-triggered correctness workflow**⟩ / ⟨**extend `01-pr-check.yml` to pushes**,
> accepting its database-provisioning cost⟩."* **Until this is answered, promoting any verifier
> deadlocks the push release path (§4).**
>
> **2 · G-1 security evidence — highest security value.** Paths:
> `.github/workflows/05-security.yml` + new `tests/test_security_workflow_structure.py`. Allowed:
> make the `aerora_quant_platform/infrastructure/` scan reachable independently of the `terraform/`
> scan result; add `if: always()` to the SAST upload; condition the IaC report upload on the report
> existing. **Scope now includes §3's ordering defect: Safety must become reachable, not merely
> blocking.** Prohibited: `continue-on-error`, `|| true`, severity reduction, scope narrowing.
>
> **3 · G-2 Safety policy — two parts, not one.** *"`safety check` is ⟨**mandatory**⟩ / ⟨**advisory**⟩,
> **and** it must run independently of the Bandit result either way."*
>
> **4 · G-3 build digest.** `02-build.yml:78` only — adopt the `03-deploy.yml:168-172` pattern.
>
> **5 · Image provenance.** Add `scripts/deploy.sh:9` to the previously requested set
> (`ecs_deploy_and_diagnose.py:94`, six `terraform/ecs.tf` pins, `ecs-task-definition-full.json`).
> **Separate owner question: is `scripts/deploy.sh` operationally referenced, and is ECR tag
> immutability enabled?** Neither is answerable from the repository.
>
> **6 · Remaining lanes** — Bandit parity (parity test before any suppression), D7-1 (only
> `market.py:53`, `orders.py:792`, `orders.py:1098`), D6-D before D7-2, W-6 classification-only,
> tenant isolation (disposable non-owner services; **FORCE RLS not first**).

---

## 9. Final integrity review

| Check | Result |
|---|---|
| Authorization ledger rechecked | **0 grants** |
| Files changed | **1** — this report. **0 governed files** |
| `git diff --check` | not applicable — no tracked file was modified by this phase |
| Concurrent modifications / stashes / worktrees / prior reports | **preserved unchanged** |
| Prohibited Git operation | **none** |
| CI dispatch · cloud mutation · deployment · production DB change · live trade | **none** |
| Secrets, DSNs, tokens, key material | **none read, printed or reproduced** |
| Blocked checks labelled passed | **none** — Trivy/Terraform/PostgreSQL/Redis all marked BLOCKED |
| Report destination absent before writing | **verified** |

**Next critical dependency: G-6, then G-1.** G-6 moved ahead of G-1 in sequence — not in security
value, but because granting the verifier lanes before it would install a deadlock. G-1 remains the
highest-value security grant, and §2 strengthens it: the secret scanner has never run over the only
tree declaring a Secrets Manager secret.

No gate was weakened, no finding suppressed, no threshold lowered, no exclusion added, no test
deleted, and no skipped, blocked or unrun check reinterpreted as a pass. Source-verified, unit,
mock-based, CI-observed, and absent integration/production evidence are labelled separately
throughout.
