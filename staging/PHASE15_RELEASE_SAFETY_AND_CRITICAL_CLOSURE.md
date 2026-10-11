# Phase 15 — Release Safety Remediation, CI Gate Enforcement, Parallel Critical Closure

**Date:** 2026-10-11 · **Destination verified ABSENT before writing** (`PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md` did not exist; not owned by another task).
**0 production files changed · 0 test files changed · 0 workflow files changed · 0 governance records changed · 0 approvals created.**

> ## LIVE TRADING: OFF / NOT CERTIFIED
>
> **Release recommendation: DO NOT RELEASE.** Not because of an unfixable defect, but because
> **no correctness evidence exists for the candidate commit**, and the frontend release lane has
> **no gate at all**. Both are fixable with small, well-understood changes — neither of which I
> am authorized to make.

| Dimension | Status |
|---|---|
| **Source correctness** | **UNVERIFIED for `ecd23a9e`** — no test job has ever run on it |
| **Security gate** | **RED** — 9 Bandit B608 (re-validated) + 1 Trivy IaC CRITICAL (unresolved) |
| **Test coverage** | local only; 0 CI-executed on the candidate SHA |
| **Deployment** | backend **NOT deployed** (gate held); **frontend IS deployed** |
| **Tenant certification** | **NOT CERTIFIED** |

---

## 1. Baseline and preservation

`VER` Revalidated this pass, not inherited:

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` / upstream / divergence | `ecd23a9e…` / `origin/main` / **0 / 0** |
| worktrees | **1** — `C:/aerora_quant_backend_updated_final1 ecd23a9e [main]` |
| staged / unstaged / untracked | **0 / 2 / 56** |
| unstaged paths | ` M staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (another session) · ` M tests/test_drift_classification.py` (mine, A29) |

**HEAD has not moved since A23E16**, so that report's findings describe the current commit. The
A29 test-6 work is intact and still unstaged.

### 1.1 Preservation — every file I must not touch, hashed at exit

| Path | Blob |
|---|---|
| `…/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | `5e9f85ce998eef0cf383ec24759e1c6782ac323a` |
| `…/b2c3d4e5f6a7_positions_canonical_shape.py` | `ac12c2d980d54d1eeb2dcf9be8921476a7b032a6` |
| `…/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | `bb9b629835c3eddf1156ea53692bce10b4e6d9b1` |
| `tests/test_bandit_sast_gate_is_green.py` | `a7deff99c9e54beee70c115607d538a638561b87` |
| `.github/workflows/01-pr-check.yml` | `91d55a379115c88edd2fd6be01871b82a75ef498` |
| `.github/workflows/05-security.yml` | `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` |
| `.github/workflows/06-frontend-deploy.yml` | `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` |
| `.github/workflows/03-deploy.yml` | `eb8078eac06268447d60f26ef7a97a537ceed988` |
| `backend_app/backend/redis_manager.py` | `d7b0e811b615ee13ba089b5cbe9d790161176de2` |
| `backend_app/backend/exchange_executor.py` | `df4fd42a151015124ae7eb3743733107106c413e` |
| W1D ledger | `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` |

**All unchanged.** No git write command was issued: no add, commit, push, merge, reset, stash,
clean, revert, checkout or branch change. No deployment, no CDN purge, no DNS change, no
infrastructure mutation, no database connection, no exchange contact, no secret printed.

### 1.2 Authorization re-derivation

`VER` Re-read the registers and the newest records. **Zero implementation grants exist.** The
concurrent `PHASE11B_A23E17_EXECUTED_EVIDENCE_AND_W6_MEASUREMENT.md` (19:52, newest) states
*"0 implementations authorized, 0 production files changed, 0 test files created"* and tabulates
`ABSENT` for Redis A23 P1, D7-1, W-6, TENANT-01/D6-C, W-3, W-4/W-5 and D7-2.

The only live grant remains **`AP-W1D-3`** (W1D-12 §1, Code owner) over four classifier files —
already fully consumed. **Nothing in this prompt is treated as approval**, and no permission was
inferred from a commit, a push, a role label, a prior prompt, or another workstream's grant.

---

## 2. Workstream A — the nine Bandit B608 findings

### 2.1 Re-validated at the current HEAD

`VER` Ran CI's exact command — `bandit -r backend_app -ll -f json` — over the whole current tree:

```
findings 9 · SEVERITY.HIGH 0 · SEVERITY.MEDIUM 9 · all B608 · exit 1
```

Identical nine locations to A23E16, so the finding set is stable:

| File | Lines |
|---|---|
| `a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |
| `b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |

### 2.2 Site analysis — what is actually interpolated

`VER` All three migrations define an **identical** validator, and its docstring states the intent:

```python
def _validate_table_name(table_name: str) -> str:
    """Refuse anything that is not a bare SQL identifier. Raises; never sanitises."""
    s = str(table_name)
    if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
        return s
    raise ValueError(f"not a SQL identifier: {s!r}")
```

Every flagged site interpolates **only** values that have passed through it:

| Site class | Evidence |
|---|---|
| table / column names | `table = _validate_table_name(raw_table)`; `column = _validate_table_name(raw_column)` |
| policy / role / `cmd` names | `a1b2…:156-180` — policy name, table, each role, and even `cmd` all validated |
| column **type** names | `b2c3…:212-213` — *"Both go through the validator, not just the column name: `sqltype` is interpolated into the same statement and is equally an identifier."* |
| **actual data** | **parameter-bound**, not interpolated: `sa.text(… "!~* :re"), {"re": UUID_REGEX}` |

**The allowlist `^[a-zA-Z0-9_]{1,64}$` excludes every SQL metacharacter** — no quote, semicolon,
space, hyphen or comment token can survive it — and identifiers are additionally double-quoted
(`public."{table}"`). Injection through a validated identifier is not reachable.

**Identifiers cannot be parameter-bound.** `SELECT … FROM :table` is not valid SQL in
PostgreSQL. The migrations say so themselves: *"Table, column and policy names cannot be
bound."* So parameterization is **not an available remedy** at these nine sites; validation plus
quoting is the correct one, and it is already implemented.

**One minor robustness nit, reported without inflation:** the validator uses `re.match` with an
explicit `$`, and Python's `$` also matches before a trailing newline — so `"users\n"` would
pass. It is **not** a security defect (a newline inside a quoted identifier merely names a
non-existent table, and `"` cannot appear), but `re.fullmatch` would be strictly more precise.
Worth folding into any authorized edit; not a reason for one.

### 2.3 The AST guard — confirmed covering, and nosec-immune

The brief asked me to confirm the AST test actually constrains these sites and cannot be
bypassed by a suppression comment. `VER` Both hold:

- **Coverage:** `_python_modules()` → `BACKEND_APP.rglob("*.py")`, excluding only `__pycache__`
  and `.venv`. That **includes `backend_app/alembic/versions/`**, so all three migrations are
  walked by `test_every_interpolation_into_sql_is_constrained_before_it_gets_there`.
- **Nosec-immunity, from the test's own docstring:** *"A `# nosec` comment is **invisible to the
  AST**, so it cannot silence this one, and it covers sites that do not exist yet."*
- **These files are why its rules exist.** RULE 2 is the validated-call rule; the test's internal
  note cites *"an alembic migration validates `column = _validate_table_name(raw_column)` inside
  every one of its functions (RULE 2, safe in each)"*, and **RULE 6 was added specifically** for
  a migration's module-level `_TARGETS` literal.
- **The migrations were written knowing this.** Their docstrings say:
  *"`tests/test_bandit_sast_gate_is_green.py` treats a call to `_validate_table_name` as the one
  way an interpolated expression may reach a statement, and that test caught this file.
  **The gate is right.**"*
- **It passes:** `pytest tests/test_bandit_sast_gate_is_green.py` → **3 passed** in 68.54s,
  exit 0 `VER`.

### 2.4 Disposition of all nine sites

| # | File:line | Interpolated | Externally influenced? | Correct remedy |
|---|---|---|---|---|
| 1-2 | `a1b2…:212, 222` | table, column (validated) | no — from `_TARGETS`/`pg_policies`, validated | `# nosec B608` + rationale |
| 3-4 | `b2c3…:224, 262` | column name + **SQL type** (both validated) | no — from `_COLUMNS` literal | `# nosec B608` + rationale |
| 5-9 | `d4e5…:193, 198, 209, 467, 475` | table, column (validated); data bound via `:re` | no | `# nosec B608` + rationale |

**All nine: identifier interpolation, validated by a strict allowlist, independently proven
constrained by a passing AST guard that a `nosec` cannot silence.** Suppression here is not
gate-dodging — it is the project's own documented mechanism, and the stronger proof remains in
force. **Parameterization is inapplicable. No safer SQL construction exists for identifiers.**

### 2.5 Authorization — ABSENT. Not applied.

No grant covers these three migrations or the test. **I did not edit them** (hashes in §1.1).

> **Copy-ready request — D-P15-1.** *"Authorized: add `# nosec B608` with a one-line rationale at
> the nine sites listed in §2.4 of `PHASE15_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md`, in
> `a1b2c3d4e5f6_…`, `b2c3d4e5f6a7_…` and `d4e5f6a7b8c9_…`. No logic change. Validation required:
> `bandit -r backend_app -ll` exits 0, and `pytest tests/test_bandit_sast_gate_is_green.py`
> stays green (3 passed)."*
> Owner: the author of those three migrations. Optional extra: tighten `re.match` → `re.fullmatch`
> (§2.2).

---

## 3. Workstream B — Trivy IaC CRITICAL: **UNRESOLVED**, with the limitation stated exactly

**I did not identify the rule ID, resource, file or line, and I will not guess it.**

`VER` Evidence limitations, all three checked:

| Avenue | Result |
|---|---|
| CI job log retrieval | **FAILED** — `gh run view --log` timed out twice (the extractor at `staging/p11b_a23e16_seclogs.py` is redaction-safe but never returned) |
| Trivy locally | **NOT INSTALLED** (`Get-Command trivy` → absent) |
| Docker (to run Trivy in a container) | **ABSENT** — so no approved local mechanism exists |

`VER` Scanner configuration, from `05-security.yml`:

| Setting | Value |
|---|---|
| action | `aquasecurity/trivy-action@v0.36.0` (**pinned**) |
| the blocking step | `Trivy IaC scan — terraform/ (blocking, CRITICAL)` |
| `scan-type` / `scan-ref` | `fs` / `terraform` |
| `scanners` | `misconfig,secret` |
| `severity` / `exit-code` | `CRITICAL` / `1` |
| second blocking step | same, `scan-ref: aerora_quant_platform/infrastructure` |
| non-blocking report | `CRITICAL,HIGH,MEDIUM` → `reports/trivy-iac-report.json`, `exit-code: 0` |

**Two configuration facts materially change how this failure should be read, and both come from
the workflow itself:**

1. **This is the first Terraform scan this repository has ever run.** The job's own comment:
   *"Nothing in this repository inspected Terraform… the ~20 `.tf` files… went unexamined by any
   of them."* `05-security.yml` was one of the 22 newly-staged paths committed by
   `ecd23a9e`, so the IaC job did not exist on any prior run.
2. **The action was simultaneously pinned, replacing a master-tracking version**, and the authors
   anticipated exactly this: *"NOTE ON FIRST RUN: this pins a specific Trivy version where the
   job previously tracked master, so the vulnerability database and check set may differ from the
   last green run. The gate is unchanged (CRITICAL, exit-code 1); **if it fails, read it as a
   finding to triage rather than as a regression from this pin.**"*

So the failure is **expected-class, not anomalous** — a new scanner pointed at never-scanned
content. The workflow comment even names what a prior manual review found in that gap, which is
the natural candidate set: *"a weak ALB TLS policy, a plaintext listener reachable in production,
a wildcard `kms:Decrypt` resource and local Terraform state"* — across 14 `.tf` files including
`alb.tf`, `iam.tf`, `secrets.tf`, `cloudfront.tf`, `waf.tf`.

**I am recording these as candidates, not as the finding.** Per the brief I did not infer it from
which files changed. **Nothing was disabled, no severity lowered, no ignore rule added.**

> **Copy-ready request — D-P15-2.** *"Supply the `Trivy IaC scan — terraform/` step output from
> run 38129699454 (or re-run `05 Security` and attach it), or authorize installing Trivy
> v0.36.0 locally to reproduce `trivy fs --scanners misconfig,secret --severity CRITICAL
> terraform`."* Either yields the rule ID, resource and line in one step. Owner: infrastructure /
> SRE.

---

## 4. Workstreams C, D, E — the release path, audited from current definitions

### 4.1 Trigger and gate matrix `VER`

| Workflow | Triggers | Ran on `ecd23a9e`? | Gate before it acts |
|---|---|---|---|
| **`01 PR Check`** | **`pull_request`** only (`branches: [main, master]`, `types: [opened, synchronize, reopened]`) | **NO RUN** | n/a — it *is* the correctness gate |
| `02 Build` | `push` | success | **none** — builds and pushes to ECR |
| `03 Deploy` | `workflow_run: ["02 Build"] completed` + `workflow_dispatch` | **failure** | **`require_security_scan.py`, SHA-pinned** |
| `05 Security` | `push`, `pull_request`, `schedule` | **failure** ×2 | n/a |
| **`06 Frontend Deploy`** | **`push`** (`branches: [main, master]`, `paths: algo22-terminal/**`) + `workflow_dispatch` | **success** | **NONE** |
| `07 Desktop Build` | `push`, `pull_request`, `workflow_dispatch` | success | none |
| `04 Nightly Audit` | `schedule`, `workflow_dispatch` | no run | n/a |

### 4.2 Dependency graph

```
commit ──push──> 02 Build ──workflow_run──> 03 Deploy
                                              └─ pre-deployment-validation
                                                   require_security_scan.py
                                                   ?head_sha=<DEPLOY_SHA>  ← SHA-PINNED
                                                     └─needs─> ecs-deployment   [SKIPPED]
                                                               IMAGE_TAG=<same SHA>

commit ──push──> 05 Security  ──────────────────────────────> [FAILURE]
                  (Bandit, Trivy image, Trivy IaC x2)

commit ──push──> 06 Frontend Deploy ──> S3 + CloudFront      [DEPLOYED]
                  ^^^^ no needs:, no workflow_run, no security check ^^^^

commit ──PR?──>  01 PR Check  [NEVER RAN: no push trigger]
                  provisioning-and-isolation · PG lane · RLS ·
                  ORM conformance · schema drift · unit+integration
```

### 4.3 The backend gate is sound — and it worked

Worth stating plainly, because it is the part that is *right*:

- `pre-deployment-validation` runs only if `workflow_run.conclusion == 'success'` or manual.
- `DEPLOY_SHA: ${{ github.event.workflow_run.head_sha || github.sha }}` and
  `require_security_scan.py` queries `GET /repos/{repo}/actions/runs?head_sha=…` for `05 Security`
  **on that exact commit** — so a stale green run on a different SHA **cannot** satisfy it.
- `ecs-deployment` declares `needs: pre-deployment-validation` → **fail-closed**.
- `IMAGE_TAG` is the same SHA → the artifact is tied to the commit that was gated.

`VER` Result on `ecd23a9e`: gate **failed** at *"Require the security scan to have passed for
this commit"*, ECS job **skipped**. **No backend reached production.** This is correct behaviour.

One documented bypass exists: `workflow_dispatch` input `allow_unscanned` → `ALLOW_UNSCANNED`.
A deliberate break-glass, noted so it is not forgotten; **not exercised**, and no evidence it was.

### 4.4 The two real gaps

**Gap 1 — `06 Frontend Deploy` has no gate whatsoever.** `VER` Its definition contains **no
`needs:`, no `workflow_run` dependency, and no reference to `01 PR Check`, `05 Security` or
`require_security_scan.py`**. It fires on `push` and publishes to S3 + CloudFront. On
`ecd23a9e` it **succeeded** while `05 Security` was failing and no correctness job had run.
**A UI is live from a commit with zero correctness evidence and a red security gate.**

This is a different and larger exposure than the backend A3-01 issue, because here there is no
gate to fail — not a gate consulting the wrong SHA.

**Gap 2 — `01 PR Check` cannot observe a direct push.** `pull_request`-only, so the PostgreSQL
lane, `provisioning-and-isolation`, tenant-isolation/RLS, ORM conformance, schema drift and all
unit/integration jobs **have never executed on committed code**. Every downstream evidence claim
is unobtainable by construction while this holds.

### 4.5 Proposals — NOT applied (no workflow grant)

Workflow files are shared infrastructure; A3-01 records `01-pr-check.yml` as the
highest-contention file needing one owner. I edited nothing.

> **D-P15-3 — correctness trigger.** Preferred: **route release-bound changes through pull
> requests** (zero workflow change, uses the gate as designed). If direct pushes to `main` must
> stay supported, add to `01-pr-check.yml`:
> ```yaml
> on:
>   pull_request: { branches: [main, master], types: [opened, synchronize, reopened] }
>   push:         { branches: [main] }
> ```
> Assess before merging: job count/cost on push, `concurrency` grouping to avoid duplicate
> expensive runs, and that `provisioning-and-isolation`'s services still provision.
>
> **D-P15-4 — frontend gate.** Convert `06 Frontend Deploy` to the pattern `03 Deploy` already
> proves: trigger on `workflow_run` of the correctness+security workflows, gate on
> `workflow_run.conclusion == 'success'`, and verify **`head_sha`** so an older green run for a
> different commit cannot satisfy it; keep `workflow_dispatch` for manual release. Fail closed
> when a required run is **missing**, not just failed. Reuse `scripts/require_security_scan.py`
> rather than writing a second checker.
> Owner for both: the SRE/coordinator who owns the shared workflows.

**No deployment, rollback, CDN purge, DNS or production change was made or proposed as an action.**

---

## 5. Workstreams F–J — status with grants explicitly identified

`VER` All verified `ABSENT` of grants this pass. The concurrent `A23E17` (19:52) **already
executed** the measurements for these lanes; per the brief I did not duplicate active probes, and
I reuse its results only where the governing hashes are unchanged — which I checked.

| Lane | Grant | Status | Evidence |
|---|---|---|---|
| **F · D7-1 vault signature** | **ABSENT** (D11 §4; A23E13) | reproduced post-commit, 7/4/3 | A23E17 §3.2 |
| **G · W-6 classifier** | **ABSENT** (D10 §7) | **TESTED** — 3/8 misclassified **measured**, matching the 3/8 prediction; a **third** shadowing defect found at `exchange_executor.py:1225-1257` | A23E17 §3.1, §4. `exchange_executor.py` unchanged at `df4fd42a…` `VER` |
| **H · QUOTA-01** | n/a (no change proposed) | **TESTED in-process** at this HEAD — 43 passed / 5 skipped; **real-Redis UNEXECUTED** | A23E17 §3.3 |
| **I · TENANT-01 / D6-C** | **ABSENT**; both owner packets "NOT SUPPLIED" | **NOT CERTIFIED** — 33 tracked `*postgres*.py` files, **0 executed on this SHA**; D6-C correction verified already applied | §4.1; A23E17 §5 |
| **J · A23 Redis** | **ABSENT** — A23E6 re-measured **12 ☐ empty, 0 marked** | not implemented; lifecycle test path still absent | `redis_manager.py` unchanged at `d7b0e811…` `VER` |

`VER` Environment, which bounds H and I regardless of authorization: Redis `127.0.0.1:6379`
**closed**, **0** `REDIS*`/`AERORA_TEST_REDIS_URL` variables set, **docker absent**, PostgreSQL
`127.0.0.1:5432` **closed**. `staging/pg_dsn.txt` exists, is gitignored, and **was not opened**.
**No Redis URL was fabricated, no guard weakened, and no skip counted as a pass.**

> **D-P15-5.** For QUOTA-01 and TENANT-01 the blocker is an environment, not a decision: either
> authorize the CI path (which requires D-P15-3 first, or a PR) or provision a disposable
> non-production PostgreSQL + Redis. **Tenant isolation remains NOT CERTIFIED** until those 33
> gated files execute somewhere real.

---

## 6. CI evidence for the candidate SHA

| Run ID | Workflow | Trigger / ref | Checked-out SHA | Conclusion |
|---|---|---|---|---|
| 38129699471 | 02 Build | push / main | `ecd23a9e` | **success** |
| 38129699454 | 05 Security | push / main | `ecd23a9e` | **failure** — Bandit; Trivy IaC CRITICAL |
| 38132697285 | 05 Security | schedule | `ecd23a9e` | **failure** — identical, reproducible |
| 38130273012 | 03 Deploy | workflow_run | `ecd23a9e` | **failure** at the gate; ECS **skipped** |
| 38129699497 | 06 Frontend Deploy | push / main | `ecd23a9e` | **success — published** |
| 38129699516 | 07 Desktop Build | push / main | `ecd23a9e` | success (4 jobs) |
| — | **01 PR Check** | — | — | **NO RUN** |

**Did these actually execute?**

| Job class | Executed on `ecd23a9e`? |
|---|---|
| unit / integration | **NO** |
| PostgreSQL lane · `provisioning-and-isolation` | **NO** |
| Redis-backed | **NO** |
| schema drift | **NO** |
| tenant isolation / RLS | **NO** |
| ORM conformance | **NO** |
| static audit (Bandit / Trivy) | **YES — failed** |
| container build / frontend / desktop | YES — succeeded |

**No run is treated as valid for this commit on the strength of a different SHA.** The last
`01 PR Check` was run 38061973739 on `f5b99e57` (a PR head, 2026-10-10) — **not** this candidate.

### 6.1 Tests executed locally this pass

| Command | Exit | Result |
|---|---|---|
| `bandit -r backend_app -ll -f json` (CI's exact command, full tree) | **1** | 9 findings · HIGH 0 / MEDIUM 9 · all B608 |
| `pytest tests/test_bandit_sast_gate_is_green.py -q -p no:cacheprovider -rs` | **0** | **3 passed** in 68.54s |

Reused without re-running (inputs byte-identical, confirmed by hash): A29's post-commit
classifier sequence — 140 passed, five-file group 103 passed / 12 skipped / 1 xfailed, mutation
probe 12/12/0. **A green local unit test is not evidence that the CI security gate is green** —
§2.3 is precisely why the one-module gate test and the full-tree command disagree.

---

## 7. Outstanding decisions — ranked by risk

| # | Blocker | Risk | Request |
|---|---|---|---|
| **1** | `01 PR Check` cannot run on `main`; no correctness evidence exists for the released commit | **CRITICAL** | **D-P15-3** |
| **2** | `06 Frontend Deploy` has no security or correctness gate; it already published | **CRITICAL** | **D-P15-4** |
| 3 | Trivy IaC CRITICAL unidentified — first-ever Terraform scan | **HIGH** | **D-P15-2** |
| 4 | 9 B608 block every backend deploy | **HIGH** (release-blocking, low security risk — §2.4) | **D-P15-1** |
| 5 | QUOTA-01 / TENANT-01 have no execution environment | **HIGH** | **D-P15-5** |
| 6 | W-6: 3/8 misclassification measured, third defect found | MEDIUM | own grant |
| 7 | D7-1 three-line deletion, spec ready (D11 §5.4) | MEDIUM | own grant |
| 8 | A23 Redis — 12 empty decision fields | MEDIUM | A23E6 form |
| 9 | W1C-02 scope · D5 · D-A27-1 ledger authority | MEDIUM | carried forward |

### Highest-value next action

> **Grant D-P15-1 and D-P15-4 together.** D-P15-1 is nine comment lines with a passing
> independent AST proof behind it, and it is the only thing standing between the backend and a
> green security gate. D-P15-4 closes the one lane that can publish with no gate at all. Neither
> needs a database, a credential, or new design work — and together they convert the release path
> from "one lane ungated, one lane blocked" into "both lanes gated, both green-able".
>
> **D-P15-3 remains the prerequisite for any *evidence* claim** — until it lands, nothing can be
> verified on the commit that ships.

---

## 8. Completion standard

| Claim | Verdict |
|---|---|
| Source correctness | **UNVERIFIED on `ecd23a9e`** — 0 correctness jobs ran |
| Security gate | **RED** — 9 B608 re-validated; 1 Trivy IaC CRITICAL unresolved |
| Test coverage | local only; **0 CI-executed** on the candidate SHA; 33 PG files at 0 executions |
| Deployment | backend **NOT deployed** (gate held correctly); frontend **IS deployed, ungated** |
| Tenant certification | **NOT CERTIFIED** |
| W1C-02 | **OPEN** — scope decision and finding-owner sign-off both absent |
| **Live trading** | **OFF / NOT CERTIFIED** |
| Files changed by this phase | **0** source · **0** test · **0** workflow · **0** governance |
| Approvals created | **0** |

No green status was manufactured; no CI run invented; no test result, database evidence or
infrastructure access fabricated. Successful `02 Build`, `06 Frontend Deploy` and
`07 Desktop Build` are **not** treated as correctness or security certification. No security gate
was disabled or weakened, and no secret was printed.

---

## 9. Addendum — corrections from `PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md`

Read **after** §1–§8 were written. Phase 14 is a concurrent session that retrieved the CI log my
extractor could not, and it **supersedes two of my statements**. Recording the corrections rather
than leaving stale claims standing. Everything in this section is `REC` — Phase 14's executed
evidence, attributed, not re-derived by me.

### 9.1 Workstream B is **no longer unresolved** — §3 is superseded

My §3 records the Trivy IaC CRITICAL as unidentified and raises **D-P15-2** to obtain the log.
**Phase 14 already obtained it** (`staging/p14_sec_failed.log`) and identified the findings.
**D-P15-2 is withdrawn as unnecessary.**

| Finding | Phase 14's verdict `REC` |
|---|---|
| **`AWS-0054`** — ALB not using HTTPS | **False positive with an enforced compensating control.** `alb.tf` redirects :80 → :443 when a custom domain exists, and a `lifecycle.precondition` **refuses the apply** for prod without it. Trivy evaluates the static `protocol = "HTTP"` attribute and **cannot resolve a `dynamic` block or a `lifecycle.precondition`**. Exploitability in prod: none — the apply is refused |
| **`AWS-0104` ×4** — unrestricted egress | **Real but conventional.** 3 of 4 are the standard pattern (tasks must reach ECR, Secrets Manager, venues). **`aws_security_group.redis` egress is genuinely unnecessary** — the one safe tightening |

Phase 14's remediation guidance, which I endorse and did not act on: a **documented, justified**
`trivy:ignore:AWS-0054` on that resource citing the precondition (narrow) rather than a
`.trivyignore` entry (broader hammer) — and explicitly **do not** make the redirect
unconditional, because that breaks non-domain deploys. Infrastructure-owner approval required.

This also confirms my §3 reading of the configuration context was right — a newly-pinned scanner
over never-scanned Terraform — and that the workflow comment's candidate list ("weak ALB TLS
policy") was on target. But the *identification* is Phase 14's, not mine.

### 9.2 My Bandit analysis was correct but incomplete — Phase 14 found the root cause

My §2 establishes that all nine sites are validated-identifier interpolation covered by a
passing, `nosec`-immune AST guard. Phase 14 reaches the same safety conclusion independently
(same tool version **bandit 1.9.4**, same command, same exit 1, same 9 findings — and it records
`nosec applied to these sites: 0`). It adds the structural cause, which I missed:

**`tests/test_bandit_sast_gate_is_green.py` contains two tests with different scopes, and neither
can see this failure:**

| Test | Scope | What it proves |
|---|---|---|
| `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` | **one file** (`GATED_MODULE`, `:68`, `:103`) | bandit is silent **on that module only** |
| `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` | **whole tree** (`rglob`, `:143`) | every interpolation is **constrained** — but says nothing about bandit's *silence* |

> **The AST test checks safety; it does not check bandit's silence. The bandit test checks silence
> on one module. So the suite is green while the gate is red, and the suite cannot see it.**

That is a sharper statement than my §2.3. I reported the scope divergence as an explanation for
why a local pass proved nothing; Phase 14 correctly frames it as a **detectability gap** in the
repository's own guard. One useful detail I did not have: `SQL_SAFE_VALIDATORS` (`:79`) **already
contains `_validate_table_name`**, which is why the nine sites satisfy RULE 2 — so the AST test
was *designed* to clear exactly this pattern.

**Consequence for my D-P15-1.** My request covers only the nine `# nosec B608` annotations
(Phase 14's **A1**). That is incomplete. Phase 14's **A2** — broaden the bandit test to run the
gate's actual `-r backend_app` command (or add a second test that does, accepting ~5m44s or a
`slow` marker) — is the one that prevents recurrence. **A1 alone fixes today's nine; the same
divergence returns the next time a file lands outside the one-module scope.**

> **D-P15-1 (revised).** Authorize **both**: (A1) `# nosec B608` + per-site reason at the nine
> sites in §2.4; **and (A2)** broaden the bandit gate test to the full-tree command so the
> suite can detect gate redness. Files: the three migrations **plus**
> `tests/test_bandit_sast_gate_is_green.py`. Validation: `bandit -r backend_app -ll` exits 0 and
> the broadened test fails before A1 and passes after. Neither change loosens the gate — the
> repo's own docstring already rules out `-lll`, `|| true` and `--baseline`.

### 9.3 What is *not* changed by Phase 14

Phase 14 independently confirms, matching my §4 and §1.2:

- **`01 PR Check` NEVER RAN at `ecd23a9e`** — "No test, Redis, PostgreSQL or flake8 evidence
  exists at `ecd23a9e`."
- **Nothing was deployed** to ECS — "`03 Deploy`'s gate refused correctly; ECS deploy skipped."
- **"Authorization unchanged: no grant exists for any code, test, workflow or Terraform change.
  Every remediation below is a proposal."**
- The same two dirty files were left untouched by it:
  `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`, `tests/test_drift_classification.py`.

**My §4.4 Gap 1 — the ungated `06 Frontend Deploy` — is not addressed anywhere in Phase 14.**
Searching it for `frontend` / `06-frontend` returns nothing on the deploy lane. So that finding,
and **D-P15-4**, remain this report's distinctive contribution and the **highest-risk open item**:
a lane that publishes to S3 + CloudFront on push with no security or correctness dependency, and
that already published from this commit.

### 9.4 Revised ranking

| # | Blocker | Risk | Request |
|---|---|---|---|
| **1** | `06 Frontend Deploy` has **no gate**; already published from an untested, security-red commit | **CRITICAL** | **D-P15-4** (unique to this report) |
| **2** | `01 PR Check` cannot run on `main` → no correctness evidence for the shipped commit | **CRITICAL** | **D-P15-3** |
| 3 | 9 × B608 block the backend deploy **and** the suite cannot detect the gate's redness | **HIGH** | **D-P15-1 (revised)** — A1 **+** A2 |
| 4 | `aws_security_group.redis` unrestricted egress — the one genuinely unnecessary rule | MEDIUM | infra owner; narrow tightening |
| 5 | `AWS-0054` needs a justified, documented narrow ignore | MEDIUM | infra owner |
| 6 | QUOTA-01 / TENANT-01 have no execution environment | HIGH | **D-P15-5** |
| 7–9 | W-6 · D7-1 · A23 Redis · W1C-02 / D5 / D-A27-1 | MEDIUM | own grants |

**Trivy is no longer an evidence gap.** The remaining evidence gap is singular and unchanged:
**nothing has tested the commit that shipped.**

Status lines in §8 are otherwise unaffected. **LIVE TRADING: OFF / NOT CERTIFIED.**
