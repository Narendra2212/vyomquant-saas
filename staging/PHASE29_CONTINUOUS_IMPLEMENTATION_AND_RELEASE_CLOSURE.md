# Phase 29 — Continuous Authorized Implementation, Release Provenance Repair, CI Gate Closure, and Backend Reliability

**Destination verified ABSENT before writing.** No existing artifact overwritten.

**0 production files · 0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 governance records
changed. 1 artifact written (this file).** No commit, stage, push, stash operation, CI dispatch,
deploy, publish, rollback, provisioning, registry action, or credential read.

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED**

---

## 0. The headline: the verifier already exists, passes 28 tests, and CI cannot see it

**`staging/phase18_release_gate.py` is a complete release-evidence verifier that closes every gap
Phases 20–24 identified, and `staging/phase18_test_release_gate.py` passes 28/28 — executed this
phase.** Both live under `staging/`, which `pytest.ini`'s `testpaths = tests` excludes, so **CI has
never run them and no workflow invokes them.**

```
.venv\Scripts\python.exe -m pytest staging\phase18_test_release_gate.py -q
→ 28 passed in 1.54s      (exit 0)
```

Measured against the gaps I and other sessions re-derived independently:

| Gap | Phase where I re-derived it | Prototype status |
|---|---|---|
| Run-level conclusion only; a **skipped mandatory job** passes | P20 B-3, P24 Lane C | **CLOSED** — `_assert_mandatory_jobs_passed()`; requires explicit `success`; `skipped` / `cancelled` / `failure` / `neutral` / absent all refuse. *"a skip is not a pass."* |
| Pagination beyond `per_page=100` | P20 B-4, P24 Lane C | **CLOSED** — `_get_list()` refuses ambiguous pagination on **both** the runs and the jobs listings |
| Only one mutable workflow display name | P24 Lane C | **CLOSED** — `REQUIRED_WORKFLOWS` covers `05 Security` (`sast-scan`, `container-security`, `iac-scan`) **and** `01 PR Check` (`static-audit`, `unit-tests`, `paper-mode-tests`, `database-tests`, …) |
| **Backend deploy requires no correctness evidence** | P20 B-1 | **CLOSED** — `01 PR Check` is mandatory in the policy |
| `allow_unscanned` override with no approval control | P20 B-4 | **CLOSED** — *"No override exists."* |
| Empty policy would admit everything | — | **CLOSED** — refuses; *"an empty policy is a misconfiguration, not a pass"* |
| Evidence from a different SHA | P20 | **CLOSED** — `head_sha` mismatch refuses |
| `workflow_run` checkout trap | P20, P24 | **DOCUMENTED** — requires `ref: ${{ github.event.workflow_run.head_sha }}` |
| Malformed payload, HTTP/URL error, unfinished status, branch allow-list | P24 | **CLOSED** |

It also excludes two jobs deliberately and reviewably in `ADVISORY_JOBS`: `format-check`
(`continue-on-error: true`, nothing depends on it) and `provisioning-and-isolation` (making it
release-mandatory is recorded as an owner decision, not the module's to assume). And it carries
`test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`, which demonstrates the earlier
gate's defect against the earlier module rather than asserting it in prose.

**Consequence for the remaining work: it is integration, not implementation.** The outstanding
engineering is to promote this module into `scripts/`, its tests into `tests/`, and wire two
workflows to call it with the checkout ref pinned. That is a far smaller and better-specified grant
than "implement a release gate", and it is the single highest-leverage request in §6.

**Precedent already exists for the test half.** `tests/test_release_artifacts.py` is a **collected,
passing** test that asserts workflow structure *and* ordering on the frontend deploy workflow —
`test_deploy_workflow_carries_the_size_gate()` and `test_size_gate_runs_after_build_and_before_s3_sync()`
(*"Ordering is the whole point: the gate must block the sync, not follow it."*). So the frontend
security gate's regression tests need no new infrastructure; they extend an established pattern in
a file CI already runs. That file also documents a **realized** frontend-publish defect: 64/67/69-byte
text files named `.AppImage` / `.deb` / `.dmg` under `algo22-terminal/public/releases/` were
publishable as desktop installers because Vite copies `public/` into `dist/` and CI syncs `dist/`
to S3.

---

## 1. Preflight

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / `ecd23a9e` |
| `origin/main` divergence | **0 ahead / 0 behind** |
| staged / unmerged | **0 / 0** |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` — no commit, reset, checkout, branch switch |
| stashes | **2 — identified, neither applied nor dropped** |
| worktrees | 1 |
| porcelain | 128 at entry → 129 at exit (+1 = this report) |
| Phase 29 destination | **ABSENT** before writing |

**Concurrency.** The only non-`staging/` modification is `tests/test_drift_classification.py`
(` M`, another session's work) — **not touched.** Verified again: **no workflow, script, Terraform
file, migration, router, test or governance record is modified anywhere in the tree.**

**Tooling (discovered, not assumed):** `python` ✔ · `.venv` ✔ · **`bandit` 1.9.4** ✔ (the exact CI
version) · `pytest` ✔ · `node`/`npm` ✔ · **`docker` ✘ · `trivy` ✘ · `terraform` ✘** ·
PostgreSQL `:5432` closed · Redis `:6379` closed. `staging/pg_dsn.txt` is gitignored and **was not
opened.**

**Authorization.** Re-read this phase: ledger `:800-804` → `AP-W1D-1` … `AP-W1D-5` **all NOT
GRANTED**; `A23E6` → **0 ticked / 12 unticked**; no `CODEOWNERS` / `OWNERS` / `MAINTAINERS`. The only
live grant remains **`AP-W1D-3`**, scoped to four drift-classifier files — none a Phase 29 target.

Per this brief's own rule — *"Do not treat the quoted sample grant in a report as if it were issued
anew"* — the sample wording I offered in the previous turn is **not** authorization, and it was not
restated. **Every implementation lane is therefore unauthorized, and no protected file was edited.**
Per the brief, that did not stop the phase: three previously unexamined lanes were carried to
verified conclusions below.

---

## 2. Priority I — security-workflow coverage: 4 of 4 claims VERIFIED

`SRC` Read `.github/workflows/05-security.yml` in full. Three jobs: `sast-scan`,
`container-security`, `iac-scan`.

### 2.1 An entire IaC root is currently unscanned

`iac-scan` runs two **sequential blocking** steps, neither carrying `if: always()`:

```yaml
- name: Trivy IaC scan — terraform/ (blocking, CRITICAL)                  exit-code: '1'
- name: Trivy IaC scan — aerora_quant_platform/infrastructure/ (blocking) exit-code: '1'
```

GitHub Actions skips subsequent steps once a step fails. `terraform/` **currently fails** with 5
CRITICALs (verified in Phase 24), so **the second root is never reached.**
`aerora_quant_platform/infrastructure` **exists** and contains **1 `.tf` file (`main.tf`)** — so
there is a real IaC root with **zero scan coverage**, and its emptiness of findings is
indistinguishable from its never having run. **VERIFIED.**

### 2.2 The Bandit artifact upload is skipped exactly when it is needed

`sast-scan` step order:

```yaml
- name: Run Bandit Scan     bandit -r backend_app -ll -f json -o reports/bandit-report.json
- name: Run Safety Check    safety check ... || echo "Safety advisory logged"
- name: Upload Security Reports   uses: actions/upload-artifact@v4      # ← NO if: always()
```

Bandit exits 1 (9 findings — reproduced in Phase 24 and again below). Because the upload step has
**no `if: always()`**, the report artifact is **not retained** precisely when there is something to
triage. Note the asymmetry: `iac-scan`'s upload **does** carry `if: always()`. **VERIFIED.**

### 2.3 The dependency audit never runs when Bandit fails — the causal chain

`Run Safety Check` sits **after** `Run Bandit Scan` in the **same job**. Bandit fails ⇒ **Safety is
skipped entirely.**

This is the structural explanation for the concurrent Phase 21's observation that `Run Safety Check`
did not execute at `ecd23a9e`. It was not a scheduling accident: **the dependency audit is
unreachable whenever the SAST step in front of it fails.** **VERIFIED.**

### 2.4 And when it does run, it cannot fail

```bash
safety check --json --output reports/safety-report.json || echo "Safety advisory logged"
```

The `|| echo` swallows every non-zero exit. So the dependency scanner is either **skipped** (§2.3)
or **non-blocking** (§2.4) — **in no configuration does it gate**, while the job is named
*"Bandit SAST & **Dependency Security Scan**"*. **VERIFIED.**

**Why this matters beyond tidiness.** `scripts/require_security_scan.py`'s own docstring records a
**realized** incident: PyJWT 2.13.0 (CVE-2026-102268, a key-confusion signature bypass permitting
token forgery) *"shipped to production while `05 Security` was red, and stayed there until the pin
was bumped."* The scanner whose job is to catch exactly that class **cannot block by construction**.
§2.1–2.4 are four independent reasons the security workflow's *reported* coverage exceeds its
*actual* coverage.

**Policy caveat, per the brief:** whether Safety *should* block is an owner decision. I am reporting
the conflict between the job's name and its behaviour, **not** changing enforcement.

---

## 3. Priority J — verifier diagnostics: claim VERIFIED

`SRC` `scripts/require_security_scan.py`, failure branch:

```python
print("  REFUSING: %s concluded %r for %s." % (WORKFLOW_NAME, conclusion, sha[:8]))
print("  05-security.yml runs Trivy at severity CRITICAL with exit-code 1, so this means a")
print("  CRITICAL container vulnerability is present. Fix the pin and push again.")
```

**The diagnosis is wrong whenever the failure is not the container scan.** `05 Security` has three
jobs; at `ecd23a9e` the failures were **Bandit SAST** and **Trivy IaC** while **Trivy Container
passed** (`CI` verified in Phase 24, run `38132697285`). So the operator is told to *"fix the pin"*
when the actual causes were 9 × B608 findings and 5 IaC misconfigurations. The script also inspects
only run-level conclusion, so it has no job-level data with which to be accurate.

**Disposition:** the refusal itself is correct and must be preserved; only the explanatory text and
the missing job-level detail are defective. Fixing it is strictly additive to safety.
**Grant ABSENT — not changed.**

---

## 4. Priority K — regression-suite discoverability: VERIFIED, with executed evidence

`SRC` `pytest.ini` sets `testpaths = tests`. Four gate artifacts live outside it:

```
staging/phase17_frontend_release_gate.py
staging/phase17_test_frontend_release_gate.py
staging/phase18_release_gate.py
staging/phase18_test_release_gate.py
```

Executed this phase:

| Command | Result |
|---|---|
| `pytest --collect-only -q staging\phase18_test_release_gate.py` | **28 tests collected**, exit 0 |
| `pytest staging\phase18_test_release_gate.py -q` | **28 passed in 1.54s**, exit 0 |

**So 28 passing tests — including load-bearing negative controls
(`test_the_mandatory_job_assertion_is_load_bearing`,
`test_the_run_conclusion_assertion_is_load_bearing`) — are invisible to CI.** Collected-and-passing
when invoked explicitly; never executed by the default lane or any workflow.

**Caution the brief rightly flags:** the fix is **not** to broaden `testpaths` to include
`staging/`. That directory holds 188 reports, probes, logs and generated artifacts; collecting it
would sweep in experiments and is how a test suite becomes untrustworthy. The correct move is to
**promote the genuine suites into `tests/`** and leave the historical evidence where it is. No
report may be relocated or deleted.

---

## 5. Phase 24 findings — currency check

`HEAD` is byte-identical to Phase 24's (`ecd23a9e`, reflog unchanged, no source file modified), so
prior source findings remain applicable. Re-verified selectively rather than wholesale:

| Finding | Status |
|---|---|
| `02-build.yml:78` digest fail-open — `2>/dev/null \|\| echo "$IMAGE_TAG"` publishes a **tag** as `image_digest` | **CURRENT** (re-read this phase) |
| `ecs_deploy_and_diagnose.py:94` — `os.getenv("IMAGE_TAG", "latest")` | **CURRENT** |
| `terraform/ecs.tf` — six task definitions on `:latest`, incl. workers at `:478` | **CURRENT** |
| `06-frontend-deploy.yml` — `on: push`, no `workflow_run`, no `needs:`, 3 × `s3 sync --delete` + `/*` invalidation | **CURRENT** |
| Bandit — 9 × B608 MEDIUM / exit 1 tree-wide vs 0 / exit 0 single-module | **CURRENT** (Phase 24 executed; source unchanged, so not re-run) |
| Two stale `# nosec B608` in `backend_app/test_perf.py` | **CURRENT** |
| D7-1 — three `access_token=` callers; `exchange.py:993` correct | **CURRENT** |
| D7-2 — import of non-existent `backend_app.backend.key_manager`, outside the inner `try` | **CURRENT** |
| ECR `imageTagMutability` | **UNKNOWN** — not configured anywhere in the tree; still owner evidence |

**None stale. None resolved. None disproved.**

---

## 6. Owner decisions — reordered by leverage

The §0 discovery changes the priority order. Integration now beats implementation.

| # | Request | Files | Why first |
|---|---|---|---|
| **1** | **Promote the existing verifier** — `staging/phase18_release_gate.py` → `scripts/`, `staging/phase18_test_release_gate.py` → `tests/`; wire `03-deploy.yml` and `06-frontend-deploy.yml` to call it with `ref: ${{ github.event.workflow_run.head_sha }}` | 2 new files under `scripts/`+`tests/`, 2 workflows | **Closes B-1, B-3, B-4, D-1…D-4 and the frontend gate in one change, with 28 tests already green.** The largest safety gain per line changed in the engagement |
| **2** | **`02-build.yml:78`** — delete `2>/dev/null` and `\|\| echo "$IMAGE_TAG"`; validate digest format; fail closed | 1 workflow | One line; stops a tag masquerading as a digest |
| **3** | **`05-security.yml`** — add `if: always()` to `sast-scan`'s upload; move `Run Safety Check` ahead of Bandit **or** into its own job; make the second IaC step reachable (`if: always()` or a separate job) | 1 workflow | Restores three checks that silently do not run (§2). Needs the Safety blocking/non-blocking **policy decision** |
| **4** | **`ecs_deploy_and_diagnose.py`** — require `IMAGE_TAG`; reject empty/whitespace/malformed before any mutation | 1 script | Removes the fail-open default |
| **5** | **`terraform/ecs.tf`** — pin six task definitions to the approved identity | 1 Terraform file | Needs infrastructure owner + `imageTagMutability` evidence |
| **6** | **Bandit parity** — widen the gate test to CI's recursive scope; add the negative control; assert suppressions are load-bearing | gate test + 3 migrations | Phase 24 supplies the reproduction and a working negative control |
| **7** | **`require_security_scan.py` diagnostics** | 1 script | Additive to safety; strictly a correctness fix (§3) |
| **8** | **D7-1 / D7-2** | 3 routers/tests; 1 updater | D7-1 also needs the keys-absent **policy decision** |
| **9** | **Frontend incident containment** | — | **Production decision, owner only.** Freeze or roll back; neither performed nor initiated |
| **10** | **H / I / J environments** | — | Disposable PostgreSQL + Redis; Trivy/Docker; a PR at `ecd23a9e` to run the provisioning lane |

---

## 7. Checks executed this phase

| Check | Result |
|---|---|
| `pytest staging\phase18_test_release_gate.py -q` | **PASS** — 28 passed, exit 0 |
| `pytest --collect-only staging\phase18_test_release_gate.py` | **PASS** — 28 collected, exit 0 |
| Git state / stash / concurrency inventory | **PASS** |
| `05-security.yml` full structural read | **PASS** — 4/4 Priority I claims verified |
| `require_security_scan.py` diagnostic text review | **PASS** — defect confirmed |
| Second IaC root existence + `.tf` census | **PASS** — exists, 1 file |
| `tests/test_release_artifacts.py` pattern review | **PASS** — collected precedent found |
| Trivy IaC / container scan | **BLOCKED** — `trivy` and `docker` absent |
| `terraform fmt -check` / `validate` / `plan` | **BLOCKED** — `terraform` absent |
| PostgreSQL / tenant-isolation integration | **BLOCKED** — service closed |
| Real-Redis integration | **BLOCKED** — service closed |
| Bandit CI-equivalent recursive scan | **NOT RUN** — executed in Phase 24 at identical source and tool version; re-running would add no evidence |
| Frontend build + bundle sweep | **NOT RUN** — no authorized lane requires it |
| CI dispatch | **NOT RUN** — not authorized |
| Published-bundle dependency manifest | **UNKNOWN** — not retrievable locally |

**Evidence classes:** `SRC` source inspection · **executed local test** (§0, §4) · `CI` prior
read-only run queries at an unchanged HEAD · `PRIOR` other sessions' findings, labelled · owner
infrastructure evidence: **none received**. The §0/§4 results are local-suite evidence; **they do
not prove production workflow configuration and are not release certification.**

---

## 8. Integrity and release disposition

**Files changed: 1 — this report.** No protected file edited; no governance record altered to
manufacture authorization; no report relocated or deleted. Diff contains no secrets, no credentials,
no DSNs, no key material. Both stash entries intact and unapplied; the other session's
`tests/test_drift_classification.py` untouched; all 188 prior artifacts preserved. No commit, push,
CI dispatch, deployment, publication, rollback, or production mutation occurred.

| Item | Status |
|---|---|
| **Frontend release gate** | **NOT CERTIFIED** — publish path still has no `workflow_run`/`needs:`; the `ecd23a9e` assets are live and were published without security verification. **Exposure remains UNKNOWN, not disproven** |
| **Backend release** | **BLOCKED** — `05 Security` red at this SHA; `03 Deploy` correctly refuses; correctness evidence still not required |
| **Image provenance** | **NOT ESTABLISHED** — digest fail-open, `latest` default, six unpinned task definitions; validated identity not bound to deployment; `imageTagMutability` unknown |
| **Security workflow coverage** | **DEFICIENT (new, §2)** — one IaC root unscanned, Bandit artifact not retained on failure, dependency audit unreachable then non-blocking |
| **Tenant isolation** | **NOT CERTIFIED** |
| **PostgreSQL / real Redis** | **UNVERIFIED** — services closed; skips are not passes |
| **Live trading** | **OFF / NOT CERTIFIED** |
| **Overall** | **DO NOT RELEASE** |

No threshold was lowered, no finding suppressed, no exclusion or baseline added, no `|| true`
introduced, no skipped or blocked check recorded as a pass, and no claim of a passing check that did
not run. `FORCE ROW LEVEL SECURITY` was not enabled in any form.

**Continuation point.** Request 1 in §6 is the decisive one: the verifier is written, closes every
identified gap, and passes 28 tests. It needs promotion out of `staging/` and two workflow call
sites — nothing more.
