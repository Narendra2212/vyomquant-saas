# Phase 24 — Release Incident Containment, Immutable Image Provenance, Gate Enforcement, and Parallel Critical Closure

**Destination verified ABSENT before writing.** Preserves all prior artifacts, including both
Phase 21 reports.

**0 production files · 0 tests · 0 workflows · 0 scripts · 0 Terraform · 0 migrations ·
0 governance records changed. 1 artifact written (this file).** No commit, stage, push, stash
operation, CI dispatch, deploy, publish, rollback, provisioning, registry retag, database mutation,
or credential read.

> **DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED**

**This phase produced executed test evidence for the first time in several phases.** Two results are
new and material:

1. **Lane B — a third, previously unreported provenance fail-open.** `02-build.yml:78` suppresses the
   digest lookup and **substitutes the image *tag* when the digest is unavailable**, then publishes
   that value as the workflow's `image_digest` output. So the declared "digest" can be a mutable tag.
2. **Lane E — the Bandit scope mismatch is now proven by execution, not inference.** The same tool
   version CI uses returns **9 findings / exit 1** tree-wide and **0 findings / exit 0** on the single
   module the gate test inspects. A negative control confirms the scanner does catch an introduced
   finding.

---

## 1. Preflight

### 1.1 Repository state

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| `origin/main` divergence | **0 ahead / 0 behind** |
| staged / unmerged | **0 / 0** |
| `git reflog -1` | `ecd23a9e commit: launch phase 1` — no commit, reset, checkout or branch switch |
| worktrees | 1 |
| porcelain (collapsed) | 113 at entry → 114 at exit (+1 = this report) |

**Stashes — identified, neither applied nor dropped:**

```
stash@{0}: filter-branch: rewrite
stash@{1}: WIP on main: c673409 Release v0.1.0: Production deployment, marketplace
           integration, desktop support, and authentication fixes
```

**Concurrent work.** The only non-`staging/` modification is `tests/test_drift_classification.py`
(` M`, another session's W1C-02 work) — **not touched.**

**Decisive negative, re-verified this phase:** there is **no modification to any workflow, script,
Terraform file, migration, router, test or governance record** anywhere in the tree. **No lane has
been implemented by any session.** Nothing was raced or overwritten.

### 1.2 Tool availability — discovered, not assumed

| Tool | State | Consequence |
|---|---|---|
| `python` + `.venv\Scripts\python.exe` | **FOUND** | Lane E executable |
| **`bandit` 1.9.4** (py 3.12.10) | **FOUND — the exact version CI uses** | Lane E scans run (§4) |
| `node`, `npm` | **FOUND** | frontend build theoretically runnable; not needed for any authorized lane |
| `docker` | **ABSENT** | container scan impossible |
| `trivy` | **ABSENT** | **Lane J BLOCKED** — no scan, no result claimed |
| `terraform` | **ABSENT** | no `plan`/`validate`; Lane B's Terraform assertions are source-only |

PostgreSQL `127.0.0.1:5432` and Redis `127.0.0.1:6379` were previously confirmed closed; no
`REDIS*`/`AERORA_TEST_REDIS_URL` variables exist. `staging/pg_dsn.txt` is gitignored and **was not
opened.**

### 1.3 Authorization matrix

Independently re-read. `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804` → `AP-W1D-1` … `AP-W1D-5`
**all NOT GRANTED**. `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` → **0 ticked / 12 unticked**.
No `CODEOWNERS` / `OWNERS` / `MAINTAINERS` exists.

**The only live grant remains `AP-W1D-3`** (W1D-12 §1, Code owner role), scoped to four
drift-classifier files — **none of which is a Phase 24 target.**

| Lane | Target files | Grant | Permitted here |
|---|---|---|---|
| A — frontend incident | `06-frontend-deploy.yml`, `01-pr-check.yml` | **NOT AUTHORIZED** | read-only trace; decision package |
| B — image provenance | `02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py`, `terraform/ecs.tf` | **NOT AUTHORIZED** | source trace; consumer census |
| C — verifier | `require_security_scan.py` + tests | **NOT AUTHORIZED** | read-only review |
| D — release DAG | workflow files | **NOT AUTHORIZED** | graph analysis |
| E — Bandit | 3 migrations + `tests/test_bandit_sast_gate_is_green.py` | **NOT AUTHORIZED** | **scans executed read-only; isolated negative control** |
| F — D7-1 | routers + test double | **NOT AUTHORIZED** + policy decision open | source trace |
| G — D7-2 | `portfolio_cache_updater.py`, `core/state.py` | **NOT AUTHORIZED** | source trace |
| H — tenant / RLS | session, dependency, RLS migrations | **NOT AUTHORIZED** + **BLOCKED (no PostgreSQL)** | static analysis |
| I — Redis / workers | `redis_manager.py`, `main.py` | **NOT AUTHORIZED** + **BLOCKED (no Redis)** | static analysis |
| J — Terraform / Trivy | `alb.tf`, `security_groups.tf`, `redis-only/main.tf` | **NOT AUTHORIZED** + **BLOCKED (no Trivy/Docker)** | source-only disposition |

**No lane is `IMPLEMENTED`.** No grant was inferred from this prompt, a prior plan, a role title,
another agent's activity, an unmodified tree, or silence. Per the brief this did not halt the phase:
Lane E was carried to executed evidence and Lane B produced a new verified defect.

---

## 2. Lane B — immutable image provenance (highest technical priority)

### 2.1 NEW: the digest is not necessarily a digest

`SRC` `.github/workflows/02-build.yml`:

```bash
:78  DIGEST=$(aws ecr describe-images --repository-name $ECR_REPOSITORY \
       --image-ids imageTag=$IMAGE_TAG --query 'imageDetails[0].imageDigest' \
       --output text 2>/dev/null || echo "$IMAGE_TAG")
:81  echo "image_digest=$DIGEST" >> $GITHUB_OUTPUT
```

and that output is exported from the workflow:

```yaml
:10-12  image_digest:
          description: 'Pushed image digest'
          value: ${{ jobs.build-push.outputs.image_digest }}
```

**Three fail-open properties in one line:** `2>/dev/null` discards the error; `|| echo "$IMAGE_TAG"`
**substitutes the tag** when the lookup fails; and the result is published under the name
`image_digest`. Any consumer that trusts `image_digest` for provenance may therefore be comparing a
**mutable tag** while believing it has an immutable digest. **This is the item the brief asked be
verified — confirmed, and it was not in the prior reports.**

### 2.2 Verified `latest` consumers — actual runtime consumers, not examples

`SRC` Census across scripts, workflows and Terraform:

| Consumer | Evidence | Class |
|---|---|---|
| `scripts/ecs_deploy_and_diagnose.py:94` | `IMAGE_TAG = os.getenv("IMAGE_TAG", "latest")`; `:901` `image_uri = f"{ECR_REGISTRY}/vyomquant-api:{IMAGE_TAG}"` | **runtime — fail-open default** |
| `terraform/ecs.tf:28` | `vyomquant-api:latest` | **runtime task definition** |
| `terraform/ecs.tf:126` | `vyomquant-tee:latest` | runtime task definition |
| `terraform/ecs.tf:198` | `vyomquant-mds:latest` | runtime task definition |
| `terraform/ecs.tf:284` | `vyomquant-websocket:latest` | runtime task definition |
| `terraform/ecs.tf:378` | `vyomquant-simulator:latest` | runtime task definition |
| `terraform/ecs.tf:478` | `vyomquant-api:latest` — *"Workers use the backend image"* | runtime task definition |
| `02-build.yml:72,76` | builds and pushes `:latest` pre-gate | **producer** |

**Answer to the brief's required determinations:**

| Question | Answer |
|---|---|
| Is image identity mutable anywhere in an actual deployment path? | **YES** — seven runtime consumers |
| Can an empty image tag resolve to `latest`? | **YES** — `os.getenv("IMAGE_TAG", "latest")` |
| Does Terraform still declare gated services on `latest`? | **YES — six task definitions** |
| Do digest lookup failures fail closed? | **NO** — they substitute the tag (§2.1) |
| Is the validated digest bound to the deployed task definition? | **NO** — no digest is carried or compared end to end |

### 2.3 Is a SHA tag sufficient here?

**Not established.** ECR tag immutability is a per-repository registry setting, and no Terraform or
documentation in the tree configures `image_tag_mutability` for `vyomquant-api`. Until that is
evidenced, a SHA tag must be treated as **mutable**, so the SHA tag alone is not a sufficient
provenance anchor — which is why C-4 (digest comparison) matters rather than being optional polish.
**Missing evidence:** the ECR repository's `imageTagMutability` setting, from the infrastructure
owner.

### 2.4 Minimal architecture — NOT APPLIED

| # | Change | File |
|---|---|---|
| B-1 | Make `IMAGE_TAG` required; exit non-zero when unset/empty/whitespace | `ecs_deploy_and_diagnose.py:94` |
| B-2 | **Remove the `|| echo "$IMAGE_TAG"` fallback and the `2>/dev/null`; fail closed** when the digest is missing or ambiguous | `02-build.yml:78` |
| B-3 | Carry the validated digest from build → gate → deploy and **compare** it before registering a task definition | build + verifier + deploy script |
| B-4 | Pin all six task definitions to an explicit immutable image variable, or document a per-service exception | `terraform/ecs.tf` |
| B-5 | Stop promoting `:latest` from an ungated build, or promote only post-gate | `02-build.yml:72,76` |
| B-6 | Ensure rollback targets a previously approved digest, not whatever `latest` resolves to | deploy script |

**Acceptance matrix** (none executed — `terraform` and `docker` absent, and the files are
unauthorized): empty/whitespace `IMAGE_TAG`; invalid or ambiguous identity; digest lookup API
failure, empty result, authorization failure; a SHA tag resolving to an unexpected digest; a
`latest` update between validation and deployment; `terraform plan` across all six definitions; a
deployed digest differing from the approved digest; worker and non-API services; and rollback to an
approved digest.

---

## 3. Lane A — frontend incident: containment decision package

### 3.1 Established vs unknown

| Item | State | Evidence |
|---|---|---|
| Publishing workflow and job | **ESTABLISHED** | `06-frontend-deploy.yml`, job `deploy-frontend`; three `aws s3 sync dist/ s3://vyomquant-frontend/ --delete` + `aws cloudfront create-invalidation --paths "/*"` |
| No gate exists on that path | **ESTABLISHED** | `on: push` to `main`/`master` for `algo22-terminal/**`; **no `workflow_run`, no `needs:`** |
| Published SHA | **ESTABLISHED** as `ecd23a9e` | `CI` `gh run list --workflow=06-frontend-deploy.yml` → `conclusion: success`, `headSha: ecd23a9e…`, `event: push` |
| `05 Security` state at that SHA | **ESTABLISHED: failure ×2** | `CI` prior queries, run `38132697285` |
| Correctness checks at that SHA | **ESTABLISHED: never ran** | `01 PR Check` is `pull_request`-only; no run at this SHA |
| Bundle secret sweep ran? | **ESTABLISHED: NO** | sweep lives only in `01-pr-check.yml`'s `frontend-tests`; triggers are mutually exclusive with the publish |
| Dependency audit (`Run Safety Check`) | **`PRIOR` (concurrent Phase 21): did not execute** | not independently re-derived here; recorded as `PRIOR` |
| PyJWT CVE-2026-102268 relevance | **CONTEXT ONLY** | documented in `require_security_scan.py`'s docstring as a *realized past* incident. **It is not evidence about `ecd23a9e`** |
| Current exposure / exploitability / compromise | **UNKNOWN — NOT ESTABLISHED** | no dependency manifest of the published bundle was retrieved; no exploitation evidence exists or was sought |
| Subsequent publication or remediation | **UNKNOWN** | not determined |

**A missing scan is not proof of exploitation, and a successful publication is not a safe release.**
Both are stated explicitly because the asymmetry is easy to lose: what is proven is an **absent
control**, not a **realized compromise**.

### 3.2 Owner containment options — none executed

| Option | Action | Risk / verification |
|---|---|---|
| **1. Freeze** | Disable `06 Frontend Deploy`, or require PRs for `algo22-terminal/**` via branch protection | Lowest risk; stops recurrence immediately; does not address already-published assets. Verify by confirming no further runs on push |
| **2. Roll back** | Point CloudFront/S3 at the last SHA with a green `05 Security` | Addresses live assets but is a production mutation; needs a known-good SHA identified first, and may regress intended UI changes |
| **3. Verify in place** | Retrieve the published bundle's dependency manifest and run the dependency audit against it offline | Non-mutating; converts the UNKNOWN in §3.1 into evidence. **Requires the artifact or owner-supplied manifest** |
| **4. Re-publish gated** | Fix the dependency findings, then publish through a gate | Correct end state; depends on Lane A forward fix |

**Recommended sequence, for the owner to decide:** Option 1 now (stops the bleeding, no production
mutation), then Option 3 (replaces the exposure unknown with evidence), then 2 or 4 as that evidence
dictates. **No rollback, S3 mutation, CloudFront invalidation, or publication freeze was performed
or initiated.**

### 3.3 Forward-fix acceptance checklist

No publish job runs without **both** security and correctness evidence for the **exact** candidate
SHA; job-level conclusions checked, not just run-level; the bundle sweep cannot be deselected or
skipped while publication proceeds; checkout and artifact pinned to the validated SHA; all `s3 sync`
and CloudFront steps downstream of the gate via `needs:`; missing/failed/cancelled/skipped/stale/
unqueryable evidence refuses; and a `workflow_run` trigger listing several workflows enforces the
required AND **inside the job** rather than assuming the trigger waits.

---

## 4. Lane E — Bandit parity: EXECUTED EVIDENCE

All three runs used `.venv\Scripts\python.exe -m bandit` — **version 1.9.4**, matching the
`bandit-1.9.4-py3-none-any.whl` CI installs. Output written to `%TEMP%`, **never into the
repository**; all fixtures deleted afterwards.

### 4.1 CI-equivalent scan — reproduces the gate failure

```
.venv\Scripts\python.exe -m bandit -r backend_app -ll -f json -o %TEMP%\p24_bandit.json
→ EXIT 1
```

| Metric | Value |
|---|---|
| Findings at `-ll` | **9** |
| Test IDs | **B608 × 9** |
| Severity | **9 MEDIUM, 0 HIGH** (160 LOW below threshold) |

| File | Findings |
|---|---|
| `backend_app\alembic\versions\d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | **5** |
| `backend_app\alembic\versions\a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | **2** |
| `backend_app\alembic\versions\b2c3d4e5f6a7_positions_canonical_shape.py` | **2** |

**Status: FAIL (reproduced).** This independently confirms Phase 14's historical figures at the
current source — now first-hand rather than cited.

### 4.2 Negative control — the gate does detect an introduced finding

An isolated fixture outside the repository containing
`conn.execute("SELECT * FROM " + t + " WHERE x = 1")`:

```
.venv\Scripts\python.exe -m bandit -r %TEMP%\p24_negctl -ll -f json
→ EXIT 1 · findings=1 · B608 MEDIUM
```

**Status: PASS.** The scanner and threshold genuinely catch a deliberately introduced SQL-injection
pattern, so a widened gate test would be a real regression detector rather than a formality.

### 4.3 The scope mismatch, proven by execution

```
.venv\Scripts\python.exe -m bandit backend_app\backend\transactional_execution_manager.py -ll
→ EXIT 0 · findings=0
```

| Scan scope | Findings | Exit | Verdict |
|---|---|---|---|
| `-r backend_app` — **CI's scope** | **9** | **1** | **FAIL** |
| `transactional_execution_manager.py` — **the gate test's scope** | **0** | **0** | **PASS** |

**This is the defect, measured.** The repository's gate test passes on the module it inspects while
CI fails on the tree. Previous phases described this divergence; it is now demonstrated side by side
with the same tool and options. The AST companion test is tree-wide but reasons about SQL
construction rather than Bandit's verdict, so it cannot close the gap either.

### 4.4 NEW: stale suppressions

The tree-wide run emitted:

```
[tester] WARNING  nosec encountered (B608), but no failed test on file backend_app\test_perf.py:25
[tester] WARNING  nosec encountered (B608), but no failed test on file backend_app\test_perf.py:26
```

**Two `# nosec B608` annotations in `backend_app/test_perf.py` suppress nothing** — no finding
exists at those lines. They are stale. Directly relevant to the brief's requirement that the gate
test detect unintended `nosec`: a parity test should also assert that every suppression is
load-bearing, or stale ones will accumulate and mask a future real finding.

### 4.5 Disposition of the nine findings

Unchanged and **not re-litigated**: each site validates its interpolated identifier through
`_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises, never sanitises) and binds user-controlled
values as parameters — **safe but legitimately red**, because unsuppressed and in CI's scope. The
remedy requires widening the gate test (the part that matters) and a per-site documented
disposition. `|| true`, lowered severity, blanket suppression and concealing baselines remain
excluded. **Grant ABSENT — no file changed.**

---

## 5. Lanes C, D, F–J — status

| Lane | Status | Position |
|---|---|---|
| **C — verifier** | **INVESTIGATED — OWNER DECISION REQUIRED** | Preserve: exact-SHA matching, missing-evidence refusal, bounded wait, refusal on failed/cancelled/skipped, API-error refusal, `needs:` on the gate. Close: **job-level conclusions** (run-level `success` with a skipped mandatory job currently passes); **pagination** beyond `per_page=100` for runs *and* jobs; **configured required-workflow list** instead of one mutable display name; **`allow_unscanned`** — verified to excuse only a *missing* scan, never a failed one, but it has **no approval control**, and any claimed Environment protection must be verified to actually enforce. `actions: read` is already documented as required in `03-deploy.yml`'s header comment |
| **D — release DAG** | **INVESTIGATED** | `01 PR Check` is `pull_request`-only, so **no correctness evidence gates either release path**. Backend requires `05 Security` only; frontend requires nothing. A gate is not enforced merely because YAML contains a job — dependency edges, conditions, triggers and publish dependencies must all bind, which currently they do not for the frontend |
| **F — D7-1** | **INVESTIGATED — OWNER DECISION REQUIRED** | Signature `load_decrypted_keys(self, user_id, exchange_id)`; three invalid callers (`orders.py:789`, `orders.py:1095`, `market.py:50`); `exchange.py:993` correct. **Not a repo-wide replacement** — paper-trading APIs may legitimately accept `access_token`. `test_market_none_keys_fix.py`'s double declares `access_token=None`, which is what hides the defect. The market route's broad `except` can convert a vault/auth failure into silent anonymous market access; **the keys-absent policy is a product/security decision and was not invented.** No key material read or logged |
| **G — D7-2** | **INVESTIGATED — OWNER DECISION REQUIRED** | `portfolio_cache_updater.py:151` imports the non-existent `backend_app.backend.key_manager`; the import is **outside** the function's own `try`, so it is caught in the per-user loop and fails **deterministically for every user every cycle** while health reports `ACTIVE`. Fix must reuse the single initialized vault — the real `APIKeyVault` requires configured encryption keys, so a second instance is unsafe. `core/state.py`'s separate import and the deployed object's identity **cannot be established locally**; **required evidence: redacted deployment boot logs from the platform owner.** Minimal import fix kept separate from health redesign |
| **H — tenant / RLS** | **BLOCKED — ENVIRONMENT AND AUTHORIZATION** | Nine RLS policies keyed on `app.current_tenant_id`; **`FORCE ROW LEVEL SECURITY` exists only as a SQL comment** (`026…:75`), zero executable statements; `dependencies.py` has **0** tenant-context references; only two GUC setters, both off the request path; fallback DSN role is `postgres`, the table owner, so policies are **inert** on the ORM path; `tenant_db_context.py` is correct (`set_config(..., is_local=true)` in an explicit transaction — the only pooler-safe form under `pool_mode=transaction`) and **unwired**. **FORCE RLS not enabled in any form**; enabling it before context wiring would make legitimate reads return zero rows. Owner/superuser tests would not be proof. **NOT CERTIFIED** |
| **I — Redis / workers** | **BLOCKED — ENVIRONMENT** | Redis closed; **no integration test run; skips are not passes.** Historical 60/5 and 18/2 counts **not assumed to still apply** and not reproduced. A23E6 **0/12 ticked**; `redis_manager.py` unchanged at blob `d7b0e811b615…`; `tests/test_a23_redis_pool_lifecycle.py` **ABSENT**; strict xfail contract preserved. `ExchangeReconciliationService` and `MarketplaceExpiryWorker` have **0 references in `main.py`**; `ReconciliationScheduler`/`OrderWatchdog` reported active elsewhere and **not re-verified here**. **W-4 UNKNOWN-exit policy not invented; W-5 not begun; no retries wired** |
| **J — Terraform / Trivy** | **BLOCKED — TOOLING** | **Trivy and Docker both ABSENT, so no scan was run and no result is claimed.** Source-only disposition of the five previously verified CRITICALs (`alb.tf` 1, `redis-only/main.tf` 1, `security_groups.tf` 3; **`cloudtrail.tf` zero**): `AWS-0054` appears mitigated by a `lifecycle.precondition`, **unverified under the deployed configuration**; `AWS-0104` ×4 — three conventional, Redis egress is the candidate, but **Redis topology, replication and failover connectivity were not inspected, so no rule should be removed on a static assumption.** Tightening both Redis rules **does not close the gate**. SC-9 remains separate and non-blocking |

---

## 6. Executed checks — PASS / FAIL / BLOCKED / NOT RUN

| Check | Result |
|---|---|
| Bandit CI-equivalent `-r backend_app -ll` | **FAIL (reproduced)** — exit 1, 9 × B608 MEDIUM |
| Bandit negative control, isolated fixture | **PASS** — exit 1, 1 × B608 detected |
| Bandit single-module (gate test scope) | **PASS** — exit 0, 0 findings → **proves the mismatch** |
| Git state / stash / concurrency inventory | **PASS** — all exit 0 |
| `latest` consumer census (scripts, workflows, Terraform) | **PASS** — 7 runtime consumers + 1 producer identified |
| Digest-fallback verification `02-build.yml:78` | **PASS** — fail-open confirmed |
| Trivy IaC scan | **BLOCKED** — tool absent |
| Container scan | **BLOCKED** — Docker absent |
| `terraform plan` / `validate` | **BLOCKED** — tool absent |
| PostgreSQL / tenant-isolation integration | **BLOCKED** — service closed |
| Real-Redis integration | **BLOCKED** — service closed |
| Frontend build + bundle sweep | **NOT RUN** — not required by any authorized lane |
| CI dispatch for new evidence | **NOT RUN** — not authorized |
| Published-bundle dependency manifest | **UNKNOWN** — artifact not retrievable locally (§3.1) |

**Evidence classes kept distinct:** `SRC` source inspection · **executed local test** (§4 only) ·
`CI` prior read-only run queries at an unchanged HEAD · `PRIOR` other sessions' findings, labelled ·
owner-provided evidence: **none received**. §4's results are isolated-fixture and local-scan
evidence; **they do not constitute release certification or prove production workflow
configuration.**

---

## 7. Prioritized owner decisions

| # | Decision | Owner | Exact request |
|---|---|---|---|
| **1** | **Frontend incident containment** | release owner | Choose §3.2 option 1 now; then option 3 to resolve the exposure unknown. **No production change may be made without this decision** |
| **2** | **B-2: digest fail-open** | release owner | Grant `02-build.yml`. Remove `2>/dev/null` and `|| echo "$IMAGE_TAG"`; fail closed. Smallest change with the largest provenance effect |
| **3** | **B-1: deploy-script default** | release owner | Grant `scripts/ecs_deploy_and_diagnose.py`. Make `IMAGE_TAG` required |
| **4** | **B-4: six Terraform task definitions** | infrastructure owner | Grant `terraform/ecs.tf`; pin to an immutable image contract |
| **5** | **ECR tag mutability evidence** | infrastructure owner | Supply `imageTagMutability` for `vyomquant-api` (§2.3) — determines whether a SHA tag is a valid anchor |
| **6** | **E: widen the gate test + stale `nosec`** | code owner | Grant 3 migrations + `tests/test_bandit_sast_gate_is_green.py`. §4 supplies the executed evidence and a working negative control |
| **7** | **C: verifier hardening** | release owner | Job-level checks, pagination, configured workflow list, real approval control for `allow_unscanned` |
| **8** | **D: bind correctness evidence to both release paths** | release owner | Grant the workflow files |
| **9** | **F / G** | router owner; portfolio-cache + platform owner | Grants, plus the keys-absent policy decision and redacted boot evidence |
| **10** | **H / I / J environments** | CI, DB, platform, infrastructure owners | Disposable PostgreSQL and Redis; Trivy/Docker availability; open a PR at `ecd23a9e` to run the provisioning lane |

---

## 8. Release disposition

| Item | Status |
|---|---|
| **Frontend publication** | **BLOCKED / INCIDENT CONTAINMENT DECISION REQUIRED** — `ecd23a9e` assets are live; the gate was absent, not merely mistimed; **exposure remains UNKNOWN, not disproven** |
| **Backend deployment** | **BLOCKED** — `05 Security` red at this SHA; `03 Deploy` correctly refuses. Additionally requires **no correctness evidence**, and **artifact provenance is not established** |
| **Image provenance** | **NOT ESTABLISHED.** Verified consumers: **7 runtime** (1 script default + 6 Terraform task definitions) plus an ungated `:latest` producer. **Remaining bypasses:** empty `IMAGE_TAG` → `latest`; `terraform apply` or a task-definition redeploy → `latest`; scale-out pulling `latest`; and a tag masquerading as a digest (§2.1). **0 fixes implemented — all unauthorized** |
| **Tenant isolation** | **NOT CERTIFIED** — policies inert on the ORM path; no isolated runtime evidence |
| **PostgreSQL / real Redis** | **UNVERIFIED** — services closed; lanes never ran at this SHA; skips are not passes |
| **Live trading** | **OFF / NOT CERTIFIED** |
| **Overall** | **DO NOT RELEASE** |

No gate was weakened, no finding silenced, no severity lowered, no exclusion or baseline added, no CI
evidence fabricated, and no skipped or blocked check recorded as a pass. `FORCE ROW LEVEL SECURITY`
was not enabled. All concurrent work is preserved: both stash entries intact and unapplied, the
other session's `tests/test_drift_classification.py` untouched, and every prior artifact — including
both Phase 21 reports — unmodified.

**Continuation point.** Every remaining lane needs either a grant or infrastructure. The two items
whose evidence is already complete enough to act on the moment they are authorized are **B-2** (one
line in `02-build.yml`) and **Lane E's gate-test widening** (§4 provides the reproduction, the
negative control, and the stale-`nosec` finding).
