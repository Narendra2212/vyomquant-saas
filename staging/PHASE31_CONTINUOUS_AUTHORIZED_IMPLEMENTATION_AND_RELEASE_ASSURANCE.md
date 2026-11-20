# Phase 31 — Continuous Authorized Implementation Sprint: Release Assurance

**Destination verified ABSENT before writing.** **0 production files · 0 workflows · 0 files under
`tests/` · 0 Terraform · 0 migrations · 0 scripts · 0 governance records · 0 approvals created ·
0 CI dispatched · 0 cloud mutations · 0 deployments.** All new files are `staging/phase31_*`.

> ## OUTCOME
>
> **No grant exists for any governed target.** Verified against the live ledger and corroborated by
> **eight** independent sessions, each of which measured `GRANTED` occurrences in the ledger as
> **0** (PHASE23 `:42`, PHASE24 `:8`, PHASE27 `:39`, PHASE28 `:80`, PHASE29 `:86`, plus PHASE19/20
> and A23E6's 0-of-12 form). Every governed file is **byte-identical to Phase 18** — so across
> Phases 19–30, **twelve phases, zero implementation**. That is the central reconciliation fact.
>
> **Delivered instead of a thirteenth recommendation: an executable, YAML-parsing invariant
> checker that reports 5 real violations, with 21 tests including a negative control for every
> invariant.**
>
> **TWO NEW SOURCE-CONFIRMED DEFECTS**
> - **INV-1** — `05-security.yml`'s SAST job uploads its reports **without `if: always()`**, so when
>   Bandit fails the upload is skipped and **the evidence explaining why every deploy is blocked is
>   destroyed by the failure it would document.** The sibling `iac-scan` job *does* have
>   `if: always()` at `:158`, which shows this is an oversight, not a policy.
> - **INV-5** — `02-build.yml` exports `image_digest`, and **no workflow ever reads it.**
>   `03-deploy.yml` contains zero `image_digest` references and re-resolves the image by tag. The
>   build-to-deploy identity chain is **tag-based end to end**; the digest output is decorative.
>
> **TWO HISTORICAL WORRIES CORRECTED** (§5.3): Bandit's exit code is **not** swallowed — only
> Safety's is; and the Trivy IaC scan is **not** one chained command — two separate blocking steps
> cover both Terraform roots.
>
> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

---

## A. Entry / exit integrity

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` · divergence | `ecd23a9e…` · `0 0` | unchanged |
| Latest reflog | `ecd23a9e commit: launch phase 1` (2026-10-11 15:13:47) | unchanged |
| Staged / unmerged | **0 / 0** | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Untracked (`-uall`) | 128 | 134 (+6, all `staging/phase31_*` + this report) |
| Worktrees / stash | 1 / **2** | 1 / **2 — both preserved, neither inspected nor applied** |

**Concurrent work observed and preserved.** The two unstaged files are other workstreams':
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`, `tests/test_drift_classification.py`. Fifteen
concurrent Phase 22–29 reports were read and **none overwritten**.

### Governed-target hashes — identical at entry and exit, and identical to Phase 18

| Hash | Path |
|---|---|
| `42c7696eb7cb993b8942b7f0053bf7490e3e5edd` | `.github/workflows/02-build.yml` |
| `eb8078eac06268447d60f26ef7a97a537ceed988` | `.github/workflows/03-deploy.yml` |
| `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | `.github/workflows/05-security.yml` |
| `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` | `.github/workflows/06-frontend-deploy.yml` |
| `91d55a379115c88edd2fd6be01871b82a75ef498` | `.github/workflows/01-pr-check.yml` |
| `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` | `scripts/require_security_scan.py` |
| `a7deff99c9e54beee70c115607d538a638561b87` | `tests/test_bandit_sast_gate_is_green.py` |
| `7d05ee95c77731dce1f88f4be1a080e5d22272d2` | `terraform/ecs.tf` |
| `a74d76448a15150f703f05d0e43b83fd8470007b` | `backend_app/backend/portfolio_cache_updater.py` |

Newly baselined for future sessions: `scripts/deploy.sh` `e06423f2b3b075add617778c662146c0c4754657` ·
`scripts/ecs_deploy_and_diagnose.py` `cec975b267e844e6c770a865258bae0f1e9edfc7` ·
`backend_app/routers/market.py` `16f0377cb9f6c85ef0fb21ce3df37940bd27628e`.

**No failed preservation check.** Because every input is unchanged, the Bandit whole-tree scan
(≈96 s), W-6 probe, D7-1 bind probe and QUOTA-01 suite were **not re-run** — reuse is valid and
re-running would recreate identical evidence.

---

## B. Authorization matrix

| Lane | Targets | Decision ID | Status | Outcome |
|---|---|---|---|---|
| A frontend gate | `06-frontend-deploy.yml` | `D-P15-4` | **NOT GRANTED** (request only) | not edited; proposal extended |
| B correctness evidence | `01-pr-check.yml` | `D-P15-3` | **NOT GRANTED** | not edited |
| C verifier | `scripts/require_security_scan.py` | — | **NOT GRANTED** | not edited |
| D image provenance | `02-build.yml`, `ecs.tf`, deploy scripts | none exists | **NOT GRANTED** | not edited; **INV-3/INV-5 confirmed** |
| E Bandit parity | gate test + 3 migrations | `D-P15-1` | **NOT GRANTED** | not edited |
| E′ security enforcement | `05-security.yml` | none exists | **NOT GRANTED** | not edited; **INV-1/INV-2 confirmed** |
| F D7-1 vault | `market.py`, `orders.py` | — | **NOT GRANTED** | not edited |
| G D7-2 updater | `portfolio_cache_updater.py` | — | **NOT GRANTED** | not edited |
| H tenant / RLS | tenant context, RLS migration | — | **NOT GRANTED** + no PostgreSQL | not edited |
| I Redis / quota / workers | `redis_manager.py` | — | **NOT GRANTED** + no Redis | not edited |
| J Terraform / Trivy | `alb.tf`, `security_groups.tf` | — | **NOT GRANTED** + no Trivy | not edited |
| K test architecture | `pytest.ini`, `tests/` | — | **NOT GRANTED** | not edited |
| L CI diagnostics | read-only | n/a | n/a | `gh run list` — no new runs |

Every bare `GRANTED` token in Phases 22–29 was inspected individually: **all are reports counting
grants and finding zero.** Rejected as authorization: requests, prior phase titles, passing test
suites, preservation ticks, reports, repository write access, and this prompt.

---

## C. Implementation results

Nothing governed was changed. One new, executable, tested artifact set was produced.

### C.1 `staging/phase31_workflow_invariants.py` — five invariants, YAML-parsed

The brief warns against *"brittle tests that merely search for a string without proving the
relevant configuration semantics"*. Each check resolves the job/step graph with PyYAML 6.0.3 and
reasons structurally. **INV-1 is undecidable by substring search**: whether an upload is reachable
depends on which *earlier steps in the same job* can fail.

| Invariant | What it proves |
|---|---|
| INV-1 evidence-retention | an upload preceded by a failure-propagating scan must be `if: always()` |
| INV-2 mandatory-scan-not-swallowed | a scanner step must not end in `\|\| echo` / `\|\| true` / `\|\| :` |
| INV-3 no-tag-as-digest | a `*digest*` output must not fall back to a tag |
| INV-4 no-ungated-mutable-tag-push | a `:latest` push requires a gate dependency |
| INV-5 digest-is-consumed | an exported digest must actually be read downstream |

### C.2 The five violations, measured

```
.venv\Scripts\python.exe staging\phase31_workflow_invariants.py   EXIT=1 — 5 violation(s)
```

| # | Invariant | Location | Confirmed defect |
|---|---|---|---|
| 1 | **INV-1** | `05-security.yml` · `sast-scan / Upload Security Reports` (`:41-46`) | **NEW.** No `if: always()`, and `Run Bandit Scan` (`:36`) can fail the job. Bandit **does** fail — 9 MEDIUM B608, measured. So the upload is skipped and **the report is destroyed by the failure it documents** |
| 2 | **INV-2** | `05-security.yml` · `sast-scan / Run Safety Check` (`:39`) | `safety check … \|\| echo "Safety advisory logged"` — exit status discarded, so a dependency-vulnerability finding **cannot fail the job** |
| 3 | **INV-3** | `02-build.yml` · `build-push` (`:78`) | `DIGEST=$(aws ecr describe-images … 2>/dev/null \|\| echo "$IMAGE_TAG")` then `image_digest=$DIGEST`. On any lookup failure a field named **`image_digest` holds a 40-hex mutable tag**, and `2>/dev/null` hides the cause. A real digest is `sha256:<64 hex>` — trivially distinguishable, and nothing validates it |
| 4 | **INV-4** | `02-build.yml` · `build-push` (`:76`) | `docker push …:latest` with no gate dependency. Confirms the Phase 18 finding against current source |
| 5 | **INV-5** | `02-build.yml` → nothing | **NEW.** `image_digest` is exported; `03-deploy.yml` has **zero** `image_digest` references and re-resolves by `imageTag=$IMAGE_TAG`. The identity chain is **tag-based end to end** |

### C.3 The compound interaction — worse than the sum

GitHub Actions skips later steps in a job once one fails, absent `if: always()`. So in `sast-scan`:

**Bandit fails (`:36`) → `Run Safety Check` (`:38`) is SKIPPED, never runs at all → `Upload Security
Reports` (`:41`) is SKIPPED.**

Dependency scanning is therefore **doubly** non-enforcing: swallowed by `|| echo` when it runs, and
not run at all whenever Bandit fails first. And this is the mechanism that explains an earlier
observation — Phase 14 had to reproduce Bandit *locally* because the CI artifact that would have
explained the red gate was never uploaded.

### C.4 Two bugs in my own work, found and fixed in-pass

Recorded rather than hidden, because both are the vacuous-pass class this engagement guards against.

1. **INV-5 was inert against its own target.** My first implementation counted
   `jobs.<id>.outputs.image_digest` as consumption — but a workflow *re-exporting its own output*
   matches that. `02-build.yml` does exactly this, so the check returned 0 findings on the one shape
   it was written for. Fixed by requiring a consumer to be a **different** workflow;
   `test_inv5_does_not_count_a_workflow_as_its_own_consumer` is the regression test.
2. **Three test-fixture bugs**: two YAML documents invalid because an unquoted `|| :` and a nested
   `"` make `: ` a mapping indicator; and a filter matching `pip install bandit safety` as a Bandit
   invocation. The third prompted a checker fix too — `_is_scanner_step` now excludes install steps,
   so INV-1's operator-facing message no longer names an install step as a blocking scan.

---

## D. Verification matrix

| Command | Exit | Version | Result | Artifact |
|---|---|---|---|---|
| `python staging/phase31_workflow_invariants.py` | **1** | PyYAML 6.0.3 | **5 violations** | `staging/phase31_invariants.txt` |
| `pytest staging/phase31_test_workflow_invariants.py -q` | **0** | pytest 9.1.1 | **21 passed**, 3.61 s | `staging/phase31_invariant_tests.txt` |
| same, attempts 1–2 | 1 | | 18→20 passed, 3→1 failed — all my own bugs (§C.4) | superseded |
| `pytest staging/phase18_test_release_gate.py staging/phase17_test_frontend_release_gate.py -q` | **0** | | **70 passed**, 7.52 s | `staging/phase31_gate_rerun.txt` |
| `gh run list` (read-only) | 0 | | no new runs since Phase 18 | — |
| Read-only `git` state + hash capture | 0 | | §A | — |

**Evidence class, stated separately as the brief requires:** everything above is **source
inspection** (§C.2 locations) and **unit tests over synthetic fixtures** (§C.1–C.2 checker). There is
**no integration evidence** and **no live-system evidence** in this phase. A checker that parses
workflow YAML proves what the YAML says; it does not prove runner behaviour.

**NOT RUN / BLOCKED / SKIPPED — honestly labelled:**

| Check | State | Reason |
|---|---|---|
| Bandit whole-tree | **NOT RE-RUN** | all inputs byte-identical; Phase 18 result (exit 1, 9 MEDIUM B608) remains valid |
| PostgreSQL / tenant-RLS runtime | **BLOCKED** | no isolated PostgreSQL |
| Redis / QUOTA-01 real-service | **BLOCKED** | no Redis, no Docker; the 5 real-Redis tests stay **SKIPPED, not passed** |
| Trivy IaC / container | **BLOCKED** | Trivy not installed locally. **An unavailable scanner is not a clean scan** |
| `terraform validate` / `fmt` | **BLOCKED** | no provider plugins, no AWS credentials |
| `01 PR Check` | **NOT RUN** | `pull_request`-only, and dispatching CI is forbidden |
| Live GitHub Actions gate integration | **NOT VERIFIED** | cannot be exercised locally; the gate proposals are **unit-tested, not integration-certified** |
| Docker / ECR / artifact-level provenance | **NOT VERIFIED** | no Docker, no AWS; no cloud mutation permitted |

---

## E. Unresolved critical risks

| Risk | State | Smallest unblocking action |
|---|---|---|
| **Release-gate wiring** | `06-frontend-deploy.yml` has no gate. Implementation written, **91 tests** across Phase 17/18/31 proposals | grant `D-P15-4` **+** `D-P15-3`; use the **Phase 18** gate — Phase 17's is run-only and unsafe |
| **Mandatory-job verification** | absent from the live pipeline; implemented in the Phase 18 proposal | same grant |
| **Mutable `latest`** | INV-4 confirmed; `ecs.tf` and both deploy scripts default to `latest` | remove the ungated push or gate it; no decision ID exists |
| **Immutable digest propagation** | **INV-3 + INV-5** — digest may be a tag, and is read by nobody | validate `sha256:` format, drop the fallback, consume the output downstream |
| **Dependency-scan enforcement** | **INV-2** — `\|\| echo` discards the verdict | remove the fallback, keep report generation separate from the verdict |
| **Security-report retention** | **INV-1** — reports lost exactly when they matter | add `if: always()` to the `sast-scan` upload, matching `iac-scan:158` |
| **Bandit scope parity** | CI exit 1 / 9 findings vs gate test exit 0 / 3 passed — single-module scope | grant `D-P15-1`: whole-tree scope, negative control, keep the AST guard, 9 rule-specific dispositions |
| **D7-1 authentication** | 3 invalid `access_token=` sites; `market.py` serves authenticated users anonymous data | D7-1 grant |
| **D7-2 updater lifecycle** | `a74d7644…` unchanged; not re-investigated this phase | D7-2 grant |
| **W-6 retry safety** | misclassification confirmed; `retryable` read only by a log line | W-6 grant. **No retry invented** |
| **Tenant / RLS runtime** | **NOT CERTIFIED** | grant + isolated PostgreSQL. Wire context **before** `FORCE RLS` |
| **Redis / quota atomicity** | **NOT CERTIFIED** — not claimable from in-process mocks | authorized dedicated Redis or CI lane |
| **Terraform / Trivy** | historical dispositions **provisional**; scanner unavailable | run the configured scan where Trivy exists |
| **Frontend publication control** | ungated; already published once from a security-red commit | `D-P15-4` |

### E.1 Corrections to historical claims

Both verified against current source and pinned by tests:

- **Bandit's exit code is NOT swallowed.** Only the Safety step is. Bandit at `:36` carries no `||`
  and genuinely reds `05 Security`. `test_the_bandit_scan_itself_is_still_blocking` asserts it.
- **The Trivy IaC scan is NOT one chained command.** `05-security.yml` runs **two separate blocking
  steps** — `:120-128` over `terraform` and `:131-139` over `aerora_quant_platform/infrastructure`,
  both `exit-code: 1`, `severity: CRITICAL`. The "only the first root is scanned" concern does not
  apply here. `test_both_terraform_roots_are_scanned_blocking` pins it against regression.

---

## F. Final disposition

**Implemented:** nothing governed. One new checker plus its test suite, both under `staging/`.

**Tested:** 21 invariant tests (every invariant with a negative control) · 70 prior gate tests
re-run green · 5 live-workflow violations measured.

**Source-reviewed only:** the five violation sites; D7-1/D7-2/W-6/tenant/Redis findings carried
forward at unchanged hashes.

**Blocked by authorization:** every lane A–K. Zero grants, across eight concurring sessions.

**Blocked by unavailable infrastructure:** PostgreSQL, Redis, Docker, Trivy, Terraform providers,
AWS, venue credentials, CI dispatch.

**Not verified in a real integration environment:** all gate logic, all image provenance, all
runtime isolation. The checker proves what the workflow YAML says — not what a runner does.

**Is release or deployment certification justified by the actual evidence? No.**
`ecd23a9e` has a failing security workflow whose evidence artifact is destroyed by its own failure,
no obtainable correctness evidence, an ungated frontend path that already published, a mutable image
tag pointing at the refused artifact, and a digest field that can hold a tag and that nothing reads.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

### Files created — 6, all new, all `staging/phase31_*` plus this report

`phase31_workflow_invariants.py` · `phase31_test_workflow_invariants.py` ·
`phase31_invariants.txt` · `phase31_invariant_tests.txt` · `phase31_gate_rerun.txt` · this file.
No credential, token, key, decrypted secret or connection string appears in any of them; the gate
suites' token is the literal `"fake-token-not-a-credential"`.

**Forbidden operations — none performed:** no `add`/`commit`/`push`/`reset`/`clean`/`stash`/branch
change/rebase/merge/revert/worktree change; no CI dispatch, PR, or repository-setting change; no AWS
or ECR mutation, no deploy, rollback, `terraform apply`, security-group change, or production
database change; no live exchange connectivity or order submission.
