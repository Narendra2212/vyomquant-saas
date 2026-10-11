# Phase 30 — Continuous Release-Safety Integration, Provenance Enforcement, Security Coverage Repair, and Reliability Closure

**Destination verified ABSENT before writing.** No existing report or artifact overwritten.

**0 production files · 0 workflows · 0 scripts · 0 Terraform · 0 migrations · 0 tests ·
0 governance records changed. 1 artifact written (this file).** No commit, stage, push, stash
operation, CI dispatch, deploy, publish, rollback, provisioning, cloud mutation, or credential read.

> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED**

---

## 0. Headline — the §3.1 audit found a defect that makes Phase 29's plan wrong

**Phase 29 (mine) concluded the remaining release-gate work was "integration, not implementation."
That conclusion is incorrect, and this phase's prototype audit is why.**

`staging/phase18_release_gate.py` declares `01 PR Check` **mandatory** in `REQUIRED_WORKFLOWS`
(jobs `static-audit`, `unit-tests`, `paper-mode-tests`, `database-tests`, …), and its policy rule 1
is *"at least one run exists for that exact SHA"* — otherwise refuse.

`SRC` `.github/workflows/01-pr-check.yml` lines 4-5:

```yaml
on:
  pull_request:
```

**`pull_request` is its only trigger.** No `push`, no `workflow_dispatch`.

`03-deploy.yml` triggers on `workflow_run` of `02 Build`, which runs `on: push`. So a SHA that
reaches `main` by push — which is how `ecd23a9e` arrived — **can never have an `01 PR Check` run.**

**Therefore promoting this verifier into the backend deploy path as-is would refuse every
push-triggered deployment, permanently.** It is not a copy-and-wire task. This is precisely the
hazard §3.1 names: *"Do not merely copy a known defect into production paths."*

**The correct sequence is now three steps, not one:**

1. **First** make a correctness lane reachable on the push path — add `push` to `01-pr-check.yml`,
   or create a dedicated push-triggered correctness workflow, or scope the `01 PR Check` requirement
   to PR-merge flows. Each has real cost: that workflow carries `provisioning-and-isolation`, which
   provisions a database, so running the whole thing on every push is not free.
2. **Then** promote the verifier and its tests.
3. **Then** wire both workflows with the checkout ref pinned to `head_sha`.

Doing step 2 first produces a deadlocked pipeline, and a deadlocked gate is how an override gets
added under pressure — which is how gates die. Note the prototype's own authors anticipated the
adjacent trap: they left `provisioning-and-isolation` in `ADVISORY_JOBS` precisely because making it
release-mandatory is an owner decision. That is also why **tenant isolation cannot be inferred as
certified from this gate passing** (§3.5 of the brief, and §7 below).

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
| porcelain | 140 at entry → 141 at exit (+1 = this report) |
| Phase 30 destination | **ABSENT** before writing |

**Concurrency.** The only non-`staging/` modification is `tests/test_drift_classification.py`
(` M`, another session's work) — **not touched.** Re-verified: **no workflow, script, Terraform
file, migration, router, test or governance record is modified anywhere in the tree.** Phase 29's
report still corresponds to this HEAD.

**Tooling (discovered):** `python` ✔ · `.venv` ✔ · `pytest` ✔ · **`bandit` 1.9.4** ✔ ·
`node`/`npm` ✔ · **`docker` ✘ · `trivy` ✘ · `terraform` ✘** · PostgreSQL `:5432` closed ·
Redis `:6379` closed. No credential, DSN or environment value was printed; `staging/pg_dsn.txt`
remains unopened.

**Authorization — re-read from the current checkout.** Ledger `:800-804` → `AP-W1D-1` … `AP-W1D-5`
**all NOT GRANTED**; `A23E6` → **0 ticked / 12 unticked**; no `CODEOWNERS` / `OWNERS` /
`MAINTAINERS`. Only live grant: **`AP-W1D-3`**, four drift-classifier files, none a Phase 30 target.

Per §1.1 of this brief, Phase 29's requests and sample wording are **not grants**, and no grant was
issued this session. **Every implementation lane is unauthorized; no governed target was edited.**
Per §1.2 that did not stop the phase — four lanes were advanced with executed evidence below.

---

## 2. Executed evidence

All read-only. Repository state unchanged by every command.

| # | Command | Result |
|---|---|---|
| 1 | `pytest staging\phase18_test_release_gate.py -q` | **PASS — 28 passed**, exit 0 |
| 2 | `pytest staging\phase17_test_frontend_release_gate.py -q` | **PASS — 42 passed**, exit 0 |
| 3 | `pytest tests\test_release_artifacts.py -q` | **PASS — 8 passed in 4.53s**, exit 0 |
| 4 | `01-pr-check.yml` trigger + job-name census | **PASS** — `pull_request` only; 8 jobs enumerated |
| 5 | Second IaC root location + content census | **PASS** — found, characterised (§4) |
| 6 | `05-security.yml` structural re-read | **PASS** — Phase 29's four findings still current |

**Correction to Phase 29's count: there are 70 passing gate tests outside CI, not 28** — 28 in
`phase18_test_release_gate.py` plus 42 in `phase17_test_frontend_release_gate.py`. Both suites
collect and pass when invoked explicitly, and `pytest.ini`'s `testpaths = tests` means **CI runs
neither.**

**`tests/test_release_artifacts.py` passes (8/8)**, so the existing frontend artifact-plausibility
gate and its ordering assertion — *"the gate must block the sync, not follow it"* — currently hold.
That is the working precedent the frontend security gate should extend rather than replace. Per §11
of the brief: this proves the **size/plausibility** check blocks publication; it does **not** prove
any security-evidence check does, because none exists on that path.

---

## 3. Priority 1 — release verifier: audited, not promoted

### 3.1 Audit result

The prototype is strong. Verified present: exact `head_sha` matching with mismatch refusal;
run-level **and** per-job conclusion checks requiring explicit `success`; refusal of `skipped`,
`cancelled`, `failure`, `neutral`, absent, unfinished and malformed; pagination-ambiguity refusal on
**both** the runs and jobs listings; HTTP and URL error refusal; empty-policy refusal; branch
allow-list; **no override path**; the `workflow_run` checkout trap documented with the required
`ref:` pin; reviewable `ADVISORY_JOBS` with stated reasons; and a test that demonstrates the earlier
gate's defect against the earlier module.

**Defects found — one blocking, one design tradeoff:**

| # | Finding | Severity |
|---|---|---|
| **P18-1** | `01 PR Check` is mandatory but is `pull_request`-only, so the gate refuses every push-triggered deploy (§0) | **BLOCKING — must be fixed before promotion** |
| P18-2 | Pagination is handled by **refusing** when a listing fills the page (`len(items) >= 100`) rather than fetching further pages. Fail-closed and correct for safety, but a SHA accumulating ≥100 runs, or a run with ≥100 jobs, would refuse permanently | design tradeoff — document, or implement real pagination |

P18-1 must be repaired in the promoted copy with a regression test, not carried forward.

### 3.2–3.4 Promotion and wiring

**NOT PERFORMED — unauthorized.** §3.2 of the brief is conditional on the destination paths being
authorized; they are not. No file was copied into `scripts/` or `tests/`, no workflow was wired, and
the `staging/` prototypes were left in place as evidence.

### 3.5 Advisory-job policy

**UNRESOLVED, and deliberately left so.** `format-check` carries `continue-on-error: true` and
nothing depends on it. `provisioning-and-isolation` provisions a database; whether it is
release-mandatory is an owner decision. I did not reclassify either. **No claim of full
certification is made, and tenant isolation is not inferred from the gate's other jobs passing.**

---

## 4. Priority 3 — security-workflow coverage: findings re-verified and sharpened

Phase 29's four findings are **all still current** at this HEAD. One is now materially sharper.

### 4.1 The unscanned IaC root declares a secret store

`iac-scan` runs two sequential blocking steps, neither with `if: always()`. `terraform/` fails
first (5 CRITICALs), so the second step never executes. The second root's actual content, located
this phase:

```
aerora_quant_platform\infrastructure\terraform\main.tf      178 lines
  terraform { backend "s3" { ... } }
  provider "aws" { ... }
  resource "aws_cloudwatch_log_group"     "aerora"
  resource "aws_secretsmanager_secret"    "aerora"
```

So the never-executed scan targets **178 lines of real infrastructure with an S3 remote backend
that declares a Secrets Manager secret** — and the step configured to examine it specifies
`scanners: 'misconfig,secret'`, exactly the scanner classes that matter for a secret store
(CMK encryption, rotation, embedded material). Its zero findings today are indistinguishable from
its never having run.

`scan-ref: 'aerora_quant_platform/infrastructure'` is correctly targeted — Trivy's `fs` scan is
recursive, so the nested `terraform/` subdirectory would be reached. The step is right; it is simply
unreachable.

### 4.2–4.4 Unchanged and confirmed

- `sast-scan`'s report upload has **no `if: always()`** (unlike `iac-scan`'s), so the Bandit report
  is discarded exactly when there is something to triage.
- `Run Safety Check` sits **after** `Run Bandit Scan` in the **same job**, so the dependency audit
  is **skipped entirely** whenever Bandit fails — which it does.
- When it does run, `|| echo "Safety advisory logged"` means it **cannot fail**.

So the dependency scanner never gates, in either configuration, while the job is named
*"Bandit SAST & **Dependency Security Scan**"*. **The Safety blocking/advisory policy remains an
unresolved owner decision and was not changed.**

---

## 5. Priority 2 — provenance chain, with missing links named

Per §4.4 of the brief, the chain traced explicitly. All `SRC`, re-verified at this HEAD.

| Link | Evidence | Binding |
|---|---|---|
| approved source SHA → build run | `02-build.yml` `on: push`; `IMAGE_TAG: ${{ github.sha }}` | **present**, but the build is **ungated** — no test or scan precedes it |
| build run → pushed image digest | `:78` `DIGEST=$(aws ecr describe-images … 2>/dev/null \|\| echo "$IMAGE_TAG")`; `:81` exports it as `image_digest` | **BROKEN** — on lookup failure a **tag is published as the digest** |
| digest → deployment input | `03-deploy.yml` passes `IMAGE_TAG`, not a digest; `require_security_scan.py` verifies a **SHA**, never an image identity | **ABSENT** — no digest is carried or compared |
| deployment input → ECS task definition | `ecs_deploy_and_diagnose.py:94` `os.getenv("IMAGE_TAG", "latest")`; `:901` builds `…/vyomquant-api:{IMAGE_TAG}` | **BROKEN** — fail-open default to `latest` |
| ECS task definition (declared state) | `terraform/ecs.tf` — six definitions on `:latest`, incl. workers at `:478` | **BROKEN** — mutable tag is the Terraform desired state |
| registry tag immutability | nothing in the tree configures `imageTagMutability` | **UNKNOWN** — owner evidence required |

**Provenance is NOT ESTABLISHED.** Four of six links are broken or absent and one is unknown. A
matching source SHA, a successful build, or a passing gate does **not** establish that the deployed
image is the one that was scanned.

---

## 6. Priorities 4–6 — reconciled

| Lane | Status | Position |
|---|---|---|
| **Diagnostic fidelity** | **INVESTIGATED — unauthorized** | `require_security_scan.py` tells the operator a failed `05 Security` means *"a CRITICAL container vulnerability is present. Fix the pin"*. At `ecd23a9e` the Container scan **passed**; Bandit and Trivy IaC failed. The script reads only run-level conclusion, so it holds no job-level evidence with which to be accurate. The refusal is correct and must be preserved; only the explanation is defective. Fixing it is strictly additive to safety |
| **Bandit parity** | **INVESTIGATED — unauthorized; scan NOT RE-RUN** | Source and tool version are identical to Phase 24's execution (bandit 1.9.4), so per §1.2 and §7 of the brief prior evidence is reused rather than regenerated: **9 × B608 MEDIUM / exit 1** tree-wide versus **0 / exit 0** on the single module the gate test inspects, plus a passing negative control and two stale `# nosec B608` in `backend_app/test_perf.py` |
| **D7-1 call sites** | **INVESTIGATED — unauthorized** | Three `access_token=` callers (`orders.py:789`, `orders.py:1095`, `market.py:50`) against `load_decrypted_keys(self, user_id, exchange_id)`; `exchange.py:993` is the correct reference. The market route's broad `except` can convert a vault/auth failure into silent anonymous market access. **The keys-absent policy is unresolved and was not invented** |
| **D7-2 vault import** | **INVESTIGATED — unauthorized, and dependency-blocked** | `portfolio_cache_updater.py:151` imports the non-existent `backend_app.backend.key_manager`, **outside** the inner `try`, so it fails deterministically for every user every cycle while health reports `ACTIVE`. Per §8.2 I did **not** substitute a speculative class or instantiate `APIKeyVault` directly — its constructor requires configured encryption keys. The authoritative initialized vault and the deployed runtime type **cannot be established locally**; redacted boot evidence from the platform owner is required |

---

## 7. Priorities 7–10 — blocked lanes, with exact blockers

| Lane | Status | Blocker |
|---|---|---|
| **Tenant isolation / PostgreSQL** | **NOT CERTIFIED · BLOCKED** | PostgreSQL `:5432` closed; no authorized database grant. Static position unchanged: nine RLS policies keyed on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` exists only as a SQL comment** (`026…:75`), zero executable statements; `dependencies.py` has **0** tenant-context references; two GUC setters, both off the request path; fallback DSN role is `postgres`, the table owner, so policies are **inert** on the ORM path; `tenant_db_context.py` is correct and **unwired**. **FORCE RLS not enabled in any form.** The brief's newer hypotheses — permissive policies combining with `OR`, tables lacking a tenant predicate, disagreeing identity expressions — were **NOT RUN** this phase and remain open |
| **Redis / workers / retries** | **BLOCKED** | Redis `:6379` closed. **No integration test run; skips are not passes.** Historical counts not assumed current. **No retries wired, no UNKNOWN-exit policy invented**, strict xfail contract preserved. `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`** |
| **Frontend artifact integrity** | **PARTIAL PASS** | `tests/test_release_artifacts.py` **8 passed** — size and ordering checks hold. But no security-evidence check exists on the publish path, and the live `ecd23a9e` artifact identity is **UNKNOWN**: it cannot be established from local evidence, and a passing local build does not make the published bundle safe. No S3/CloudFront inspection or mutation performed |
| **Terraform / Trivy** | **BLOCKED** | `trivy`, `docker`, `terraform` all absent. **No scan run, no `validate`, no `plan`, no result claimed.** Static review is not a substitute. Egress rules untouched; no finding removed or suppressed |

---

## 8. Owner decisions — reordered by the §0 correction

| # | Request | Files | Why this order |
|---|---|---|---|
| **1** | **Decide the push-path correctness lane** — add `push` to `01-pr-check.yml`, create a dedicated push-triggered correctness workflow, or scope the gate's `01 PR Check` requirement. **This is now the prerequisite for everything in lane 1** | 1 workflow (+ policy) | Without it, promoting the verifier **deadlocks all deploys** (§0). Note the cost: that workflow carries `provisioning-and-isolation` |
| **2** | **Promote the verifier** — `phase18_release_gate.py` → `scripts/`, its tests → `tests/`, fix **P18-1** with a regression test, wire both workflows with `ref:` pinned to `head_sha` | 2 new + 2 workflows | 70 tests already green; closes the job-level, pagination, override and correctness-evidence gaps at once |
| **3** | **`02-build.yml:78`** — delete `2>/dev/null` and `\|\| echo "$IMAGE_TAG"`; validate digest shape; fail closed | 1 workflow | One line; stops a tag masquerading as a digest (§5) |
| **4** | **`05-security.yml`** — `if: always()` on `sast-scan`'s upload; separate Safety from Bandit's control flow; make the second IaC root independently reachable | 1 workflow | Restores three checks that silently do not run, one covering a **Secrets Manager** declaration (§4.1) |
| **5** | **Safety blocking policy** | — | Owner decision. Not invented, not changed |
| **6** | **`ecs_deploy_and_diagnose.py`** — require `IMAGE_TAG` | 1 script | Removes the fail-open default |
| **7** | **`terraform/ecs.tf`** + **ECR `imageTagMutability` evidence** | 1 Terraform file | Infrastructure owner; provenance cannot close without the latter |
| **8** | **Bandit parity**, **diagnostics**, **D7-1**, **D7-2** | as §6 | D7-1 needs the keys-absent policy; D7-2 needs boot evidence |
| **9** | **Frontend incident containment** | — | **Production decision, owner only.** Not performed or initiated |
| **10** | **Environments** — disposable PostgreSQL + Redis; Trivy/Docker | — | Unblocks §7 entirely |

---

## 9. Integrity

**Files changed: 1 — this report.** No governed target edited; no governance record altered to
manufacture authorization; no report or prototype moved, rewritten or deleted. Diff contains no
secrets, credentials, DSNs or key material. Both stash entries intact and unapplied; the other
session's `tests/test_drift_classification.py` untouched; all prior artifacts preserved. No commit,
push, CI dispatch, deployment, publication, rollback, or cloud/production mutation occurred.

No threshold lowered, no finding suppressed, no exclusion or baseline added, no `|| true`
introduced, no fake digest or tag-as-digest fallback created, no skipped/blocked/unrun check
recorded as a pass, and no production certification claimed from local tests. `03-deploy.yml`'s
existing fail-closed refusal is intact.

| Item | Status |
|---|---|
| Release verifier | **AUDITED — blocking defect P18-1 found; NOT PROMOTED (unauthorized)** |
| Frontend release gate | **NOT CERTIFIED** — no security evidence on the publish path; size/ordering checks **PASS** |
| Backend release | **BLOCKED** — `05 Security` red; correctness evidence still not required |
| Image provenance | **NOT ESTABLISHED** — 4 of 6 links broken/absent, 1 unknown |
| Security workflow coverage | **DEFICIENT** — one IaC root unscanned (declares a secret store), SAST evidence discarded on failure, dependency audit unreachable then non-blocking |
| Tenant isolation | **NOT CERTIFIED** |
| PostgreSQL / real Redis | **UNVERIFIED** |
| Live trading | **OFF / NOT CERTIFIED** |
| **Overall** | **DO NOT RELEASE** |

**Continuation point.** Request 1 is now the true critical path and it is a **policy decision, not
code**: how correctness evidence becomes available on the push path. Until that is answered, the
verifier cannot be promoted without deadlocking deployment — which is the single most important
thing this phase established, and it reverses Phase 29's recommendation.
