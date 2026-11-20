# Phase 36 — Continuous Engineering Closure: Suppression Census Correction and Release Assurance

**Destination verified ABSENT before writing. No Phase 17/18/32/33/35 artifact overwritten.**

> ## Outcome
>
> `VER` **Grants: zero.** Ledger `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`, `**GRANTED**` = **0**,
> `:802` = `NOT GRANTED`. **0 governed files changed** — no edits manufactured.
>
> Substantive result: an **independent suppression census that corrects the cited Phase 33 figures in
> both directions**, one hypothesis **raised and withdrawn inside the phase**, and one factual
> correction to this brief's target list.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 1. Entry / exit integrity — identical

| Property | Entry | Exit |
|---|---|---|
| `HEAD` / `origin/main` / divergence | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / same / **`0 0`** | **unchanged** |
| `git reflog -1` | `ecd23a9e` | unchanged |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |
| Ledger hash | `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | unchanged |

**Concurrent work preserved byte-for-byte:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` = `0c9981b2817e47b4faa915b69de204ff78f87af7` ·
`tests/test_drift_classification.py` = `99a2917ac0a65761ef3dfe9299ed1df87647552a`.

Tree hash reused from Phase 35 (`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`), computed there with
`GIT_OBJECT_DIRECTORY` redirected so **nothing was written to the shared object database**; inputs
unchanged this phase, so it was not recomputed.

**Environment:** Python 3.12.10 · pytest · bandit **1.9.4** · PyYAML 6.0.3 · GitHub CLI (read-only) ·
**Docker / Trivy / Terraform / PostgreSQL / Redis ABSENT**.

---

## 2. Authorization state — NOT GRANTED for every governed target

Rechecked against the live ledger this phase. No decision record, delta, or owner approval has
appeared. Every lane below is **NOT GRANTED**: verifier promotion, `01-pr-check.yml`,
`05-security.yml`, `06-frontend-deploy.yml`, `02-build.yml`, `03-deploy.yml`,
`require_security_scan.py`, `ecs_deploy_and_diagnose.py`, the deploy scripts, `terraform/*`, the
Bandit gate test and migrations, D7-1 / D7-2 / W-6 targets, tenant context and RLS.

No authority was inferred from this prompt, a prior prompt, report wording, an example grant, or
silence. The ledger was neither modified nor read for modification.

---

## 3. Workstream C — suppression census, independently measured and CORRECTED

This is the phase's main contribution. The brief cites Phase 33 as finding *"56 directives across 17
files, 54 bare suppressions, and 13 bare suppressions in `transactional_execution_manager.py`."*
I measured it myself over `backend_app/**/*.py`.

### 3.1 Raw measurement, then the correction to my own first number

| Metric | My raw count | Corrected | Phase 33 (cited) | Verdict |
|---|---|---|---|---|
| Directives | 62 | **56** | 56 | **CONFIRMED** after correction |
| Files | 18 | **17** | 17 | **CONFIRMED** after correction |
| Rule-scoped | 56 | **56** | — | — |
| **Bare** | 6 | **0** | **54** | **CORRECTED** |
| `transactional_execution_manager.py` total | 13 | **13** | 13 | **CONFIRMED** |
| …of which **bare** | 0 | **0** | **13** | **CORRECTED** |

**My own 6 "bare" suppressions were false positives from my regex.** All six matched the substring
`nosec` inside the word **"nanosecond"**:

```
backtest_runtime.py:906   # … reads ``0..239`` as NANOSECONDS since the epoch …
backtest_runtime.py:932, :1028, :1032, :1091   (same, "nanoseconds"/"nanosecond index")
telemetry_engine.py:384   timestamp_ns = int(time.time() * 1_000_000_000)  # nanosecond precision
```

Excluding those six reconciles my count **exactly** to Phase 33's 56 directives / 17 files.

### 3.2 Why "54 bare" is a misclassification, proven by execution

`VER` Syntax-form distribution across the 56 real directives:

| Form | Count |
|---|---|
| `# nosec: B608` (**colon**) | **54** |
| `# nosec B608` (space) | **2** |
| bare, no rule id | **0** |

**The "54 bare" figure equals exactly the colon-form count.** The likely cause is classifying
`# nosec: B608` as bare because Bandit's documented syntax is space-separated.

**Bandit 1.9.4 does parse the colon form.** Evidence is executional, not inferential: my own Phase 24
run of the exact CI command emitted

```
[tester] WARNING  nosec encountered (B608), but no failed test on file backend_app\test_perf.py:26
```

and `test_perf.py` uses `# nosec: B608`. Bandit reported the rule id `B608`, so it extracted it from
the colon form.

> **Therefore all 56 directives are rule-scoped to a single rule, and none is bare.**

### 3.3 What survives, and what the correction retires

**Survives — the parity gap is real.** The gate test's own docstring states the CI command is
`bandit -r backend_app -ll`, while its first test is
`test_bandit_reports_nothing_in_the_module_the_gate_failed_on` — **one module**. And that module
carries **13 B608 suppressions**, so it can report clean partly *because* those findings are
silenced. Parity between test scope and CI scope is still absent.

**Retired — the severity of that gap.** A **B608-scoped** suppression cannot mask a *different* rule
(B105 hardcoded password, B602 shell injection, and so on). The risk profile of "54 bare
suppressions that could hide anything" is materially different from "56 directives each silencing
exactly one reviewed rule." **The brief's premise that the module "can appear clean because findings
are suppressed" is correct in kind but narrower in scope than the cited figures imply.**

### 3.4 The 13 directives, reviewed in context

All 13 sit on parameterized SQL where only an **identifier** is interpolated and every value is
bound. The in-source rationale at `:1215-1216` states: *"json_text() raises on anything but an
identifier and is called with literals; symbol and tenant_id are bound parameters."* Representative:

```
:750   f"SELECT id FROM positions "                                      # nosec: B608
:751   f"WHERE {_sym_sql} = :symbol AND tenant_id = :tid"                # nosec: B608
:1217  f"SELECT id, data FROM positions "                                # nosec: B608
:1218  f"WHERE tenant_id = :tenant_id AND {_sym_sql} = :symbol"          # nosec: B608
```

Same validated-identifier-plus-bound-parameter construction as the nine alembic B608 findings.
**I added, removed and rewrote nothing** — no grant, and §5 of the brief forbids it.

### 3.5 Hypothesis raised and WITHDRAWN inside this phase

While reviewing the census output I suspected a **missing `f` prefix** at `:1218` — which would have
made `{_sym_sql}` literal text and produced invalid SQL at runtime. **I checked before reporting it,
and it is disproven:** line 1218 does carry the `f` prefix. My census display truncated each line to
its last 70 characters, cutting off the leading `f"`. **The defect does not exist; the artifact was
mine.** Recorded rather than quietly dropped.

---

## 4. Phase 35 claim reconciliation

| Phase 35 claim | Status this phase | Basis |
|---|---|---|
| Zero grants for governed targets | **CONFIRMED** | ledger hash + `**GRANTED**`=0 |
| 280 tests passed / 5 skipped / 1 xfailed | **NOT RECHECKED in full** — inputs unchanged; reused per §11. Two suites re-run below | hash-verified reuse |
| Phase 18 (28 tests) stronger than Phase 17 (42 tests) | **CONFIRMED** | Phase 35 execution; policy re-read |
| Correctness-workflow trigger unresolved | **CONFIRMED** | `01-pr-check.yml` still `pull_request`-only |
| `blue-green-deploy.yml` does not exist | **CONFIRMED** | absent |
| `deploy.sh` defaults `latest`; `blue-green-deploy.sh` refuses empty tag | **CONFIRMED** | Phase 35 source read |
| 3 shell scripts, 0 automated refs in scanned dirs | **CONFIRMED, scope-limited** | 191-file bounded scan |
| `ecs-task-definition-full.json` has no functional consumer | **CONFIRMED** — the one hit is a comment at `01-pr-check.yml:263` | Phase 35 self-correction |
| Frontend gate / security evidence / provenance / tenancy uncertified | **CONFIRMED** | unchanged |

### 4.1 Correction to this brief's target list

`VER` **`infra/terraform/` does not exist.** The brief names it as a Terraform root to inspect.
Measured:

| Path | State |
|---|---|
| `terraform/` | EXISTS |
| `terraform/redis-only/` | EXISTS |
| `aerora_quant_platform/infrastructure/terraform/` | EXISTS |
| **`infra/terraform/`** | **ABSENT** |

There are **three** Terraform roots, not four. `infra/` contains `deploy.sh` but no Terraform root.

---

## 5. Tests executed this phase

| Command | Exit | Result | Duration | Tool |
|---|---|---|---|---|
| `pytest tests/test_bandit_sast_gate_is_green.py -q --tb=line` | **0** | **3 passed** | 103.44s | pytest / bandit 1.9.4 |
| Suppression census over `backend_app/**/*.py` (`Select-String`) | 0 | 62 raw → **56 real / 17 files / 0 bare** | — | — |
| Syntax-form classification | 0 | 54 colon · 2 space · 0 bare | — | — |
| `:1210-1222` and `:748-752` verbatim read | 0 | missing-`f` hypothesis **disproven** | — | — |
| Terraform root existence | 0 | 3 exist, `infra/terraform` **ABSENT** | — | — |

**Reused, not re-run** (source, config, command and tool version hash-verified unchanged since
Phase 35): phase18 **28 passed**, phase17 **42 passed**, `tests/test_release_artifacts.py`
**8 passed**, bounded 8-file regression **202 passed / 5 skipped / 1 xfailed** (75.52s). The 5 skips
are real-Redis cases; the xfail is the pre-existing strict one. **Neither counts as a pass.**

**NOT RUN, with reason:** full default suite (times out, bounded selection used); Trivy, Terraform,
Docker, PostgreSQL, Redis (**ABSENT — never labelled passed**); every implementation-acceptance test
(**no grant, so no change exists to validate**); tenant matrix (**BLOCKED — PostgreSQL absent**, so
the historical 4-of-150 failure remains neither reproduced nor refuted).

---

## 6. Lane status — labelled per §12

| Lane | Status |
|---|---|
| A — release verifier contract | **BLOCKED (no grant).** Candidate-SHA-versus-HEAD workflow-definition problem and the `D5' Real-Redis Diagnostics` / `provisioning-and-isolation` job-identity asymmetry remain **UNKNOWN** pending authoritative job-classification policy |
| A — trigger architecture | **BLOCKED — owner decision.** `01-pr-check.yml` `pull_request`-only; both verifiers require its evidence; promoting either deadlocks the push path |
| B — scan reachability / evidence retention | **BLOCKED (no grant).** Second IaC root unreachable after first failure; SAST upload lacks `if: always()`; Safety both skipped-after-Bandit and `\|\| echo` non-blocking |
| B — Safety policy | **UNKNOWN — owner decision**, preserved; not chosen |
| **C — Bandit census / parity** | **ADVANCED this phase.** Census corrected (§3); parity gap **CONFIRMED**, severity **narrowed**; gate test **PASS 3/3**; no suppression touched |
| D — frontend gate | **BLOCKED (no grant).** Artifact-size floor **PASS 8/8** (reused) and is **not** release authorization |
| E — deploy scripts / provenance | **BLOCKED (no grant). IMAGE PROVENANCE NOT ESTABLISHED.** ECR immutability **UNKNOWN**; operational liveness of 3 scripts + task-definition JSON **UNKNOWN** |
| F — D7-1 / D6-D-D7-2 / W-6 | **BLOCKED (no grant).** D6-D remains D7-2's prerequisite |
| G — tenant isolation | **BLOCKED / NOT RUN — PostgreSQL absent. NOT CERTIFIED** |
| H — Redis / workers / Terraform / discovery | **BLOCKED — services and scanners absent.** `infra/terraform` **ABSENT** (§4.1); SG-collision premise remains **withdrawn**; `AWS-0054` disposition unchanged |

---

## 7. Owner decisions — minimum grants to unblock

> **1 · Correctness-workflow architecture (FIRST — gates everything else).** Choose: PR-only `main`
> *with an explicit mechanism binding approved PR-head evidence to the merge commit* / a dedicated
> push-triggered correctness workflow / extend `01-pr-check.yml` to pushes, accepting duplicate runs
> and double database provisioning.
>
> **2 · G-1 security scan reachability + evidence retention** — `05-security.yml` + a structural
> test. Make the `aerora_quant_platform/infrastructure/` scan reachable independently; add
> `if: always()` to the SAST upload; make Safety run independently of Bandit; gate the IaC report
> upload on the report existing. **Prohibited:** `continue-on-error`, `|| true`, severity reduction,
> scope narrowing, fabricated reports.
>
> **3 · Safety policy** — mandatory or advisory, **and** independent of the Bandit result either way.
>
> **4 · Bandit parity — now cheap and well-scoped.** *"I authorize extending
> `tests/test_bandit_sast_gate_is_green.py` to run CI's actual `-r backend_app -ll` scope (marked
> slow), with a negative control."* **No suppression change is requested**: §3 establishes all 56
> are rule-scoped and reviewed, so parity is the gap, not the annotations.
>
> **5 · Build digest** — `02-build.yml:78` only; adopt the `03-deploy.yml:168-172` fail-closed
> pattern. Preserve the deploy guard.
>
> **6 · Verifier promotion** — Phase 18, **after decision 1**; repair API display-name mapping from
> the candidate SHA's YAML; keep prototypes in `staging/`; do not broaden `pytest.ini`.
>
> **7 · Operational questions repository evidence cannot answer** — which of `scripts/deploy.sh`,
> `scripts/blue-green-deploy.sh`, `infra/deploy.sh` are supported entry points; whether
> `ecs-task-definition-full.json` is ever applied; whether **ECR tag immutability** is enabled; which
> digest is source of truth per path.
>
> **8 · Remaining** — D7-1 (only the three `load_decrypted_keys` callers); D6-D before D7-2; W-6
> classification-only with no retry; tenant isolation on a disposable non-owner service, **FORCE RLS
> not first**.

---

## 8. Prohibited-operation checklist — all confirmed NOT performed

No stage, commit, push, reset, stash, pop, drop, clean, revert, merge, rebase, branch switch,
history rewrite, or worktree modification. No CI dispatch, PR, branch-protection or
repository-settings change. No deploy, rollback, asset publication, CloudFront invalidation, DNS
change, ECR mutation, task-definition registration, Terraform apply, infrastructure provisioning,
production database mutation, real Kubernetes operation, exchange order, or live-trading enablement.
No secret, credential, DSN, token, decrypted key or sensitive header printed.

No gate weakened; no assertion, severity threshold, anti-vacuity check, strict xfail, RLS
requirement or fail-closed behaviour altered; no suppression added or removed; no skipped or absent
check reported as a pass.

**Preserved:** both concurrent modifications (hashes §1), all untracked artifacts, every prior
report, both staging prototypes, 2 stashes, 1 worktree.

---

## 9. Release verdict

**DO NOT RELEASE.** Unchanged, and for unchanged reasons:

1. The correctness workflow can still deadlock the push release path.
2. Frontend publication lacks commit-bound authorization and already published `ecd23a9e` with
   `05 Security` red.
3. Security scans remain unaccounted for — the IaC root declaring secret infrastructure is still
   unreachable, SAST evidence is still discarded on failure, Safety is still doubly ineffective.
4. Deployment identity remains mutable or unresolved; **ECR immutability UNKNOWN**.
5. Tenant isolation has no integration evidence — **PostgreSQL absent**.

This phase did not improve release readiness. It improved **evidence quality**: the Bandit lane's
central claim is now measured rather than inherited, its remediation is smaller and better targeted
than previously requested, and two inaccuracies — one in the cited prior census, one of my own —
were caught before they could propagate.

| Final | |
|---|---|
| Governed files changed | **0** — no grant |
| Tests executed | **3 passed** (gate test) + census/verification commands; **prior 280 reused with provenance** |
| Trivy / Terraform / Docker / PostgreSQL / Redis | **NOT RUN — absent** |
| **IMAGE PROVENANCE** | **NOT ESTABLISHED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
