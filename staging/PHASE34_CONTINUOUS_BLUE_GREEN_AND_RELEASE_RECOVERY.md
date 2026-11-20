# Phase 34 — Blue-Green Safety, Harness Calibration, and Bandit Suppression Semantics

**Destination verified ABSENT before writing.** **0 production files · 0 workflows · 0 files under
`tests/` · 0 Terraform · 0 migrations · 0 deployment scripts · 0 governance records · 0 approvals
created · 0 CI dispatched · 0 cloud mutations · 0 deployments.** New files are `staging/phase34_*`.

> ## Executive outcome
>
> **Authorization remains zero.** Ledger `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`,
> blob **`7e238620bc22f0ad246f0ebc373b7032c6b28ef0`**, 56,397 bytes: **0 bare `GRANTED`, 5
> `NOT GRANTED`.** No governed target edited. HEAD `ecd23a9e…`, tree
> **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — recomputed, unchanged.
>
> **Two results, one of which is a disproof of my own hypothesis.**
>
> **1. HARNESS CALIBRATED — Phase 33's blue-green findings are now earned.** §2.2 required a
> negative control proving the harness can tell a safe implementation from an unsafe one. Phase 33
> had none. Built and run: the real script behaves **identically whether green is healthy or
> unhealthy** (exit 0, **0 health probes**, selector patched both times), while a two-line
> demo-fixed variant **refuses with 0 selector patches** and still permits a healthy switch.
> **HARNESS VALID.**
>
> **2. DISPROVEN BY REPRODUCIBLE EVIDENCE — the colon-form `# nosec` concern is unfounded.**
> Bandit's documented syntax is space-separated, so I hypothesised that the repository's 54
> `# nosec: B608` directives might degrade to bare suppressions covering every rule. Tested with
> controls: **the colon form scopes correctly.** An unrelated B602 finding annotated
> `# nosec: B608` is still reported. **Phase 36's census reading holds** and the 54 directives
> suppress B608 only.
>
> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

---

## 1. Entry / exit integrity

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| HEAD tree | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` (recomputed) | unchanged |
| Upstream · ahead/behind | `origin/main` · `0 0` | unchanged |
| Staged / unmerged | **0 / 0** | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Untracked (`-uall`) | 185 | 191 (+6, all `staging/phase34_*`) |
| Worktrees / stash | 1 / **2** | 1 / **2 — preserved, not inspected, not applied** |
| Ledger blob | `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | unchanged |

Concurrent work preserved: `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`,
`tests/test_drift_classification.py`. No prior report, log or prototype overwritten. A concurrent
`staging/PHASE35_FULL_SYSTEM_IMPLEMENTATION_AND_RELEASE_RECOVERY.md` exists and was **not touched**.

**Tooling:** Python 3.12.10 · pytest 9.1.1 · Bandit **1.9.4** · PyYAML 6.0.3 · bash (WSL) +
`/usr/bin/python3` · node/npm/aws CLI present (**aws never invoked**).
**ABSENT → BLOCKED:** docker, trivy, terraform, kubectl, safety, pip-audit, psql, redis-cli.
`exchange_keys.json` is open in the editor and was **deliberately not read**.

---

## 2. Authorization matrix

| Lane | Target scope | Grant | Classification |
|---|---|---|---|
| Blue-green safety | `scripts/blue-green-deploy.sh` | none | **BLOCKED — NO GRANT** |
| `deploy.sh` failure path | `scripts/deploy.sh` | none | **BLOCKED — NO GRANT** |
| `infra/deploy.sh` verification | `infra/deploy.sh` | none | **BLOCKED — NO GRANT** |
| Release verifier | `staging/phase18_release_gate.py`, workflows | `D-P15-4` request only | **BLOCKED — NO GRANT** |
| Trigger architecture | `01-pr-check.yml` | `D-P15-3` request only | **BLOCKED — OWNER DECISION** |
| INV-1…INV-5 | `05-security.yml`, `02-build.yml` | none | **BLOCKED — NO GRANT** |
| Safety threshold | policy | — | **BLOCKED — OWNER DECISION** (no in-repo authority) |
| Bandit parity | `tests/test_bandit_sast_gate_is_green.py` + 3 migrations | `D-P15-1` request only | **BLOCKED — NO GRANT** |
| Image provenance / frontend gate | build + deploy chain | none | **BLOCKED — NO GRANT** |
| Vault D7-1 / D7-2 / W-6 | `market.py`, `orders.py`, updater, classifier | none | **BLOCKED — NO GRANT** |
| Tenant / RLS runtime | tenant context, RLS | none | **BLOCKED — NO GRANT + TOOL/SERVICE ABSENT** |
| Redis / workers | `redis_manager.py`, workers | none | **BLOCKED — TOOL/SERVICE ABSENT** |
| Terraform (3 roots) | `terraform/`, `terraform/redis-only/`, `aerora_quant_platform/infrastructure/terraform/` | none | **BLOCKED — NO GRANT + TOOL ABSENT** |

The ledger was **not** modified. Nothing inferred from this prompt, a prior phase, a recommendation,
a quoted template, a passing prototype suite, or silence.

---

## 3. Blue-green harness calibration — the Phase 33 gap closed

§2.2 is explicit: *"A test harness that is stateless or fails to model selector and replica state
must be marked invalid until corrected."* Phase 33's harness became stateful mid-phase but was
**never shown to have discriminating power**. That is now tested.

Harness: `staging/phase34_bluegreen_negative_control.sh` → `staging/phase34_negctl.txt`.
Real script sha1 `5791dce3c53ab620c9890ca11d739550ad6bc8e9`, **read only**. Variant B is a patched
**copy in a temp directory**; the repository file is untouched. `kubectl`/`curl`/`sleep` stubbed,
selector state persisted. No cluster, no network, no traffic change.

| Variant / condition | exit | health probes | selector patches |
|---|---|---|---|
| **A — real script, green healthy** | 0 | **0** | 1 |
| **A — real script, green UNHEALTHY** | **0** | **0** | **1** |
| **B — demo-fixed, green healthy** | 0 | 11 | 1 |
| **B — demo-fixed, green UNHEALTHY** | **1** | 1 | **0** |

Three conclusions, all executed:

1. **The real `switch` path is blind to candidate health.** Rows A1 and A2 are identical: exit 0,
   zero probes, selector patched. An unhealthy candidate receives production traffic and the script
   reports success.
2. **A two-line change fixes it** — calling `validate_green` before the selector patch. Row B2:
   refuses, exit 1, and critically **0 selector patches**, so no traffic is ever exposed.
3. **The fix is not merely a hard block.** Row B1 still permits a healthy switch. A harness that
   only proved "B refuses everything" would be worthless.

**HARNESS VALID.** Phase 33's five blue-green findings therefore rest on a calibrated instrument
rather than an assumed one.

### 3.1 A dependency the calibration exposed

Row B1 shows **11 probes** for one healthy switch: `validate_green`'s ten consecutive successes
plus the readiness check. The demo fix inherits the inverted retry loop (§3.2), so **the switch-
validation fix and the retry-semantics fix are independent and both required** — fixing only the
former makes every switch pay ~20 s of mandatory sleeps and still abort on one transient probe
failure.

### 3.2 Phase 33 findings, reconfirmed against current source

Script unchanged this phase (sha1 and `git hash-object` both stable). Carried forward:

| Finding | Status |
|---|---|
| `switch` exposes an unvalidated candidate, exit 0 | **CONFIRMED + calibrated (§3)** |
| `promote` overwrites blue with green's image and scales green to 0 — rollback target destroyed | CONFIRMED (Phase 33 execution) |
| `validate_green` increments `RETRY` on success, exits on first failure → `MAX_RETRIES=10` performs **zero** retries | CONFIRMED |
| `rollback` prints "Rollback complete", exit 0, with blue at 0 ready replicas | CONFIRMED |
| Safeguards that do work: `deploy_green` requires an image (no `latest` default); readiness check and `rollout status` fail closed | CONFIRMED |

**Not re-executed:** the full 21-scenario matrix §2.2 enumerates. Scenarios covering missing
resources, rollout timeout, image-identity drift and TOCTOU races were **NOT RUN** — they test
behaviour of a fix that does not exist and cannot be authorized this phase. The calibrated harness
is the prerequisite; the matrix belongs with the grant.

---

## 4. Bandit suppression semantics — hypothesis disproven

### 4.1 Census re-verified independently

Measured over the CI scope (`backend_app/` recursive), matching Phase 36 exactly:

| Form | Count |
|---|---|
| colon `# nosec: Bxxx` | **54** |
| space `# nosec Bxxx` | **2** |
| **bare `# nosec`** | **0** |
| **total** | **56** across **17** files |

Top file: `backend_app/backend/transactional_execution_manager.py` — **13**, as corrected. Then
`telemetry_engine.py` 8, `metrics_service.py` 7, `dashboard_aggregation_service.py` 5,
`ml_models.py` 4, `routers/portfolio.py` 4. The earlier "54 bare" classification was wrong; **0 are
bare.** One of the two space-form hits is prose inside a comment discussing suppressions, not a
functional directive — so functional space-form directives number **1**.

### 4.2 The hypothesis, and why it was worth testing

Bandit's documented syntax is `# nosec` or `# nosec B101,B602` — **space** separated. The colon form
is undocumented. If Bandit matched `# nosec` and then failed to parse `: B608` as an ID list, the
directive would degrade to a **bare** nosec suppressing **every** rule on its line — making 54 lines
across 17 files blanket suppressions. "Rule-scoped, therefore fine" would be a false reassurance,
exactly the reading §9 warns against.

### 4.3 The experiment and its controls

`staging/phase34_nosec_form_probe.py` → `staging/phase34_nosec_form_probe.txt`. Six fixtures in a
temp directory (removed afterwards; **nothing under `backend_app/` touched**), scanned with the CI
threshold `-ll`. B608 is MEDIUM and B602 HIGH, so both clear the threshold.

| Fixture | Rule | Reported? |
|---|---|---|
| `c1` B608, no directive | B608 | **YES** (control: findings are detected) |
| `c2` B608 + `# nosec B608` (space) | B608 | no (control: documented form suppresses) |
| `s1` B608 + `# nosec: B608` (**colon**) | B608 | **no** → colon form does suppress its named rule |
| `c3` B602, no directive | B602 | **YES** (control) |
| **`s2` B602 + `# nosec: B608`** | B602 | **YES** → **does NOT over-suppress** |
| `c4` B602 + `# nosec B608` (space) | B602 | YES (consistent) |

All three controls behaved as required, so the experiment is valid.

### 4.4 Verdict

**DISPROVEN BY REPRODUCIBLE EVIDENCE.** Bandit 1.9.4 parses the colon form and scopes it to the
named rule. `# nosec: B608` suppresses B608 and nothing else. **Phase 36's corrected census reading
stands**, and no re-annotation of the 54 directives is warranted on scoping grounds.

Two caveats kept explicit, per §9: scoping is **not** justification — whether each of the 54 sites
is *safe* still depends on its query construction, and that per-site review needs the `D-P15-1`
grant. And this result is specific to **Bandit 1.9.4**; an undocumented form is not a stable
contract, so a version bump could change it. Recorded as the reason to prefer the documented space
form in any authorized future edit.

---

## 5. Lanes reconciled without re-derivation

Source hashes unchanged since Phase 33, so prior evidence stands and expensive checks were **not**
repeated (Bandit whole-tree ≈96 s, W-6 probe, D7-1 bind probe, QUOTA-01 suite, workflow invariant
checker, 91 prototype gate tests).

| Lane | State | Classification |
|---|---|---|
| `scripts/deploy.sh` | exit 0 + success banner with all 5 health checks failed; missing `.env.staging` tolerated | executed (Ph 32), **REUSED** |
| `infra/deploy.sh` | none of 4 post-deploy checks can fail; 2 WARN-only, 2 INFO-only; readiness is a literal; wrong-account warns; permission preflight fails closed | source-verified; **not executed, deliberately** |
| Release verifier identity | **all 11 jobs declare explicit `name:`; `job_id == display` for 0 of 11.** Phase 18 prototype would refuse every run. Its 28 tests used self-referential fixtures | **CONFIRMED (Ph 33); BLOCKED — NO GRANT** |
| Trigger architecture | `01-pr-check.yml` is `pull_request`-only; 4 options unselected | **BLOCKED — OWNER DECISION** |
| INV-1 SAST retention | upload at `05-security.yml:41-46` lacks `if: always()`; Bandit at `:36` can fail | **BLOCKED — NO GRANT** |
| INV-2 Safety | `\|\| echo` swallows status; sequenced after Bandit so it is also skipped | **BLOCKED — NO GRANT** |
| INV-3 digest fallback | `02-build.yml:78` `\|\| echo "$IMAGE_TAG"`, `2>/dev/null` hides cause | **BLOCKED — NO GRANT** |
| INV-4 `latest` pre-gate | `02-build.yml:76` ungated push | **BLOCKED — NO GRANT** |
| INV-5 digest unconsumed | `03-deploy.yml` has **zero** `image_digest` references | **BLOCKED — NO GRANT** |
| IaC roots | two blocking Trivy steps cover `terraform` and `aerora_quant_platform/infrastructure`; third root `terraform/redis-only` scan coverage **UNKNOWN** without Trivy | **BLOCKED — TOOL ABSENT** |
| Vault D7-1 | 3 `access_token=` sites vs 2-positional signature; `market.py` can serve authenticated users anonymous data; missing-keys policy **not invented** | source-verified |
| D7-2 updater | `portfolio_cache_updater.py` `a74d7644…` unchanged | **REUSED** |
| W-6 / execution safety | `.retryable` read only by a log line; **no retry added**, `UNKNOWN` preserved as terminal-ambiguous | source-verified |
| Tenant / RLS | 4 of 150 cells masked by a quota dependency ordering; **NOT CERTIFIED**, no PostgreSQL | **BLOCKED — TOOL/SERVICE ABSENT** |
| Redis / workers | 5 real-Redis tests remain **SKIPPED, not passed**; atomicity **not claimed** from mocks | **BLOCKED — TOOL/SERVICE ABSENT** |
| Frontend gate | ungated; live S3/CloudFront bytes **UNKNOWN**, none fetched | source-verified |

---

## 6. Verification matrix

| Command | Exit | Version | Result | Artifact |
|---|---|---|---|---|
| `bash …/phase34_bluegreen_negative_control.sh …` | **0** | WSL bash + python3 | **HARNESS VALID** — 4 conditions, unsafe/safe distinguished | `phase34_negctl.txt` |
| same, relative-path attempt | 127 | | terminal path mangling; file and `python3` verified present, re-run via absolute path | superseded |
| `python staging/phase34_nosec_form_probe.py` | **0** | Bandit 1.9.4 | **6 fixtures, 3 controls OK, hypothesis disproven** | `phase34_nosec_form_probe.txt` |
| nosec census over `backend_app/` | 0 | — | 56 / 17 files / 54 colon / 2 space / **0 bare** | §4.1 |
| ledger grant scan | 0 | — | blob `7e238620…`, 0 GRANTED / 5 NOT GRANTED | §2 |
| `git rev-parse HEAD^{tree}` | 0 | — | `4b45a763…` | §1 |

**BLOCKED / NOT RUN, with reasons:** Trivy, Terraform, kubectl, PostgreSQL tenant isolation, Redis
integration, Safety, pip-audit, Docker/ECR provenance, live gate integration, `01 PR Check`
dispatch, `infra/deploy.sh` execution (deliberate), frontend live bytes, npm frontend build (not
reached), the 21-scenario blue-green matrix (§3.2 — tests a fix that cannot be authorized).
**No missing tool counted as a pass; no skip counted as a pass.**

---

## 7. Corrections and withdrawn hypotheses

| Item | Disposition |
|---|---|
| "The colon `# nosec:` form may degrade to a bare suppression" | **WITHDRAWN — disproven** (§4.3). Controls validated the experiment |
| "54 bare suppressions" (pre-Phase-36) | Independently re-confirmed as **wrong**: 0 are bare |
| Phase 33 harness discriminating power | Was **assumed**; now **demonstrated** (§3). Phase 33's conclusions are upheld |
| Phase 18 "landing the gate is a copy, not a design exercise" | **Withdrawn in Phase 33** and restated here: the identity fix is the grant's first obligation |
| CRLF in deployment scripts | Remains **not a repository defect** — committed blobs are LF-only; `core.autocrlf=true` is the cause. Absence of `.gitattributes` is a hygiene gap, not a broken script |

---

## 8. Owner decisions and minimum grants, in dependency order

| # | Decision | Minimum grant | Verification already available |
|---|---|---|---|
| **1** | Blue-green `switch` validation | edit `scripts/blue-green-deploy.sh` | **calibrated harness + demonstrated 2-line fix** (§3) |
| **2** | Blue-green retry semantics + rollback retention | same file | independent of #1 (§3.1); `promote`/`rollback` reproductions from Ph 33 |
| **3** | `scripts/deploy.sh` failure path | edit `scripts/deploy.sh` | executed reproduction (Ph 32) |
| **4** | `infra/deploy.sh` verification | edit `infra/deploy.sh` | source-verified |
| **5** | Release gate | `D-P15-4` + `D-P15-3` | **first obligation: the job-identity fix (0/11 match) and real-YAML fixtures** |
| **6** | Security evidence + enforcement | edit `05-security.yml` | INV-1/INV-2 located |
| **7** | Safety blocking threshold | policy | **no in-repo authority** |
| **8** | Image provenance | edit `02-build.yml`, `ecs.tf`, helpers | INV-3/4/5 located |
| **9** | `D-P15-1` Bandit parity | edit gate test + 3 migrations | census settled; **scoping question now closed** (§4.4) — remaining work is per-site justification |
| 10 | Image reference contract (tag vs digest) | policy | governs #1–#4 and #8 |
| 11 | Tenant/RLS · Redis · Terraform | grants **plus** PostgreSQL / Redis / terraform + Trivy | environment first |

---

## 9. Final integrity verification

| Check | Result |
|---|---|
| `HEAD` / tree / branch / upstream | `ecd23a9e…` / `4b45a763…` / `main` / `0 0` — **unchanged** |
| Staged / unmerged | **0 / 0** |
| Tracked files modified by this phase | **0** |
| Governed paths (`.github/ scripts/ terraform/ tests/ backend_app/ infra/`) | only `tests/test_drift_classification.py` — the other workstream's pre-existing change |
| `git diff --check` | clean |
| Ledger blob | `7e238620…` — unchanged, not modified |
| Stashes / worktrees | 2 / 1 — intact, not inspected, not applied |
| Prior reports / logs / prototypes | none overwritten; destination verified absent |
| Secrets | none printed. `exchange_keys.json` **not read**. Probe fixtures contain only synthetic code; no credential, token, DSN or key appears in any artifact |
| **Prohibited operations — none performed** | no add/commit/push/reset/stash/pop/drop/clean/revert/merge/rebase/branch change/history rewrite/worktree change; no CI dispatch, PR, branch-protection or settings change; no deploy, rollback, frontend publish, CloudFront invalidation, DNS, ECR mutation or task-definition registration; no real `kubectl`; no `terraform init/apply/destroy/import`; no production database mutation; no worker activation; no exchange order; no live trading. `aws` CLI present but **never invoked** |

**Files created — 6:** `phase34_nosec_form_probe.py`, `phase34_nosec_form_probe.txt`,
`phase34_bluegreen_negative_control.sh`, `phase34_negctl.txt`, this report. Harness temp
directories removed by their own `trap`/`finally`.

**This phase produced no production code change, because no lane is authorized — stated plainly
rather than implied.** Its deliverables are a **calibrated** test instrument, a **disproven**
hypothesis, and an independently re-verified census. Nothing here certifies runtime, CI, or
live-system behaviour.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**
