# Phase 32 — Continuous Implementation, Security Closure, Release Provenance

**Destination verified ABSENT before writing.** **0 production files · 0 workflows · 0 files under
`tests/` · 0 Terraform · 0 migrations · 0 deployment scripts · 0 governance records · 0 approvals
created · 0 CI dispatched · 0 cloud mutations · 0 deployments.** New files are `staging/phase32_*`.

> ## 1. Executive outcome
>
> **Authorization is zero at the authoritative source.** The ledger
> `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` contains **0 bare `GRANTED`** and **5
> `NOT GRANTED`**; rows `:800-804` (`AP-W1D-1`…`-5`) are each `NOT GRANTED`. No governed target was
> edited. Tree hash is **byte-identical to Phase 31**, so twelve-plus phases have produced no
> implementation.
>
> **What this phase adds that no prior phase has: EXECUTED proof, not source review.** `bash` is
> available on this host, so `scripts/deploy.sh` was run against stubbed `kubectl`/`curl`/`sleep` in
> a throwaway directory. Result:
>
> **With all five health checks failing, the script exits 0 and prints
> "🎉 Deployment to staging complete!" — byte-identical behaviour to the all-checks-pass control.
> The success signal carries no information.**
>
> **NEW defect found by execution:** a **missing `.env.staging` is silently tolerated** and the
> deploy proceeds without configuration. Prior phases flagged the unsafe *parsing*; nobody reported
> that a *missing file* does not stop the run.
>
> **Sharper characterisation of `infra/deploy.sh`:** **not one of its four post-deployment checks
> can produce a non-zero exit.** Two are `[WARN]`-only; two are `[INFO]`-only and have no pass/fail
> branch at all. It then prints `DEPLOYMENT COMPLETE & VERIFIED` and a hardcoded
> `Readiness Score: 100%`.
>
> **One correction issued:** the CRLF in `scripts/deploy.sh` is a **local checkout artifact, not a
> repository defect** — committed blobs are pure LF. Recorded so nobody "fixes" a non-defect.
>
> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
> TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

---

## 2. Preflight and integrity

| Property | Entry | Exit |
|---|---|---|
| Root / branch | `C:/aerora_quant_backend_updated_final1` / `main` | unchanged |
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Tree | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — identical to the Phase 31 baseline | unchanged |
| `origin/main` · divergence | `ecd23a9e…` · `0 0` | unchanged |
| Staged / unmerged | **0 / 0** | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Untracked (`-uall`) | 164 | 170 (+6, all `staging/phase32_*`) |
| Worktrees / stash | 1 / **2** | 1 / **2 — preserved, not inspected, not applied** |

Concurrent work preserved: `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` and
`tests/test_drift_classification.py` (both other workstreams'). Reports through **Phase 37B**
(23:57) were read; **none overwritten**.

### 2.1 Tooling

| Tool | State |
|---|---|
| Python 3.12.10 · pytest 9.1.1 · PyYAML 6.0.3 · Bandit 1.9.4 | **PRESENT** |
| **bash** (`C:\Windows\system32\bash.exe`, WSL) | **PRESENT** — enabled §4 |
| node / npm / aws CLI | PRESENT (aws **not invoked**) |
| docker · trivy · terraform · kubectl · safety · pip-audit · psql · redis-cli | **ABSENT → BLOCKED** |

---

## 3. Authorization

Read from the authoritative ledger, not from reports.

```
staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md  (55,999 bytes)
  bare GRANTED = 0        NOT GRANTED = 5
  :800 AP-W1D-1  DB owner                NOT GRANTED
  :801 AP-W1D-2  DB owner                NOT GRANTED
  :802 AP-W1D-3  Code owner              NOT GRANTED
  :803 AP-W1D-4  DB owner + code owner   NOT GRANTED
  :804 AP-W1D-5  Coordinator + DB owner  NOT GRANTED
```

| Lane | Targets | Status |
|---|---|---|
| Release verifier | `phase18_release_gate.py` → `scripts/` | **NOT GRANTED** (`D-P15-4`, request only) |
| Correctness trigger | `01-pr-check.yml` | **NOT GRANTED** (`D-P15-3`) |
| Security workflow | `05-security.yml` | **NOT GRANTED** — no decision ID exists |
| Image provenance | `02-build.yml`, `ecs.tf`, deploy scripts | **NOT GRANTED** |
| Bandit parity | gate test + 3 migrations | **NOT GRANTED** (`D-P15-1`) |
| Deployment scripts | `scripts/deploy.sh`, `blue-green-deploy.sh`, `infra/deploy.sh` | **NOT GRANTED** |
| Key vault / workers / tenant / Redis / frontend / test discovery | respective targets | **NOT GRANTED** |

Rejected as authorization: this prompt, prior phase titles, passing prototype suites, reports
counting grants, recommendations, and absence of objection. The ledger was **not** modified.

---

## 4. EXECUTED evidence — `scripts/deploy.sh` false green

Harness: `staging/phase32_deploy_sh_harness.sh` → log `staging/phase32_deploy_sh_harness.txt`.
Script under test sha1 `ffa8eb8b5e4ce60bed2f06949d7ef34bcd3b7962`, **read only, never modified**.
`kubectl`, `curl` and `sleep` are stubs on `PATH`; the script runs from `mktemp -d` with a dummy
env file. **No cluster, no cloud, no network.**

| Scenario | Observation | Verdict |
|---|---|---|
| **1 — all 5 health checks fail** | `curl` invoked **5/5** times (stub call log); `Health check passed` never printed; **exit 0**; **success banner printed** | **FALSE GREEN — CONFIRMED BY EXECUTION** |
| **2 — health check succeeds (control)** | exit 0, banner printed | identical to scenario 1 |
| **3 — image identity** | `kubectl set image deployment/trading-backend backend=latest -n staging` | **bare mutable tag; no registry, no repository, no digest** |
| **4 — `.env.staging` absent** | **exit 0**, and execution **reached** pre-deployment checks | **NEW — missing config silently tolerated** |

### 4.1 Mechanisms, from source

- **False green** (`deploy.sh:59-66`): `for i in {1..5}; do if curl -sf "$HEALTH_URL" …; then … break; fi; … done`.
  `curl` sits inside an `if`, so `set -e` (`:7`) cannot fire, and **no exit-status check follows the
  loop**. The banner at `:72` is unconditional. Scenarios 1 and 2 therefore produce the same exit
  code and the same final line.
- **NEW, scenario 4** (`:20`): `export $(grep -v '^#' .env.$ENVIRONMENT | xargs)`. When the file is
  absent `grep` fails, but the failing status belongs to the command substitution; `export` with no
  arguments returns 0, so `set -e` never triggers. The deploy continues **with no configuration
  loaded**. This is distinct from the previously-reported unsafe *parsing* of an existing file.
- **Image identity** (`:44-46`): `backend=$IMAGE_TAG` with `IMAGE_TAG=${2:-latest}` (`:10`).
  `kubectl set image` expects `container=IMAGE[:TAG]`; `backend=latest` names no repository at all.

### 4.2 Correction — CRLF is NOT a repository defect

The first harness run returned **exit 127 for every scenario**: this Windows worktree has
`core.autocrlf=true`, so `#!/bin/bash\r` is an invalid interpreter and nothing ran.

Verified against the object store — **committed blobs are pure LF**:

| File | blob bytes | CR | LF |
|---|---|---|---|
| `scripts/deploy.sh` | 2,489 | **0** | 82 |
| `scripts/blue-green-deploy.sh` | 12,277 | **0** | 377 |
| `infra/deploy.sh` | 6,995 | **0** | 140 |

So CI on Linux gets LF and is unaffected. The harness strips CR to test the script's **logic**
rather than this machine's line endings. **No line-ending change is warranted** — recorded
explicitly so a later session does not "fix" a non-defect. The only real consequence is local: these
scripts cannot be executed directly from a Windows checkout.

---

## 5. `infra/deploy.sh` — the verification engine cannot fail

Phase 37's claims **CONFIRMED at source**, and stated more precisely than "false green". Reading
`infra/deploy.sh:94-141`:

| Check | Line | Failure handling | Can it fail the script? |
|---|---|---|---|
| 1 ALB state | `:103` | `echo "[WARN] ALB State is: $ALB_STATE"` | **No** |
| 2 Target-group health | `:110` | `echo "[INFO] Backend Target Health: …"` — **no pass/fail branch**; empty renders "No Targets Registered Yet" | **No** |
| 3 ECS stability | `:121` | `echo "[WARN] ECS Service Status: $RUNNING_COUNT/$DESIRED_COUNT"` | **No** |
| 4 Endpoint `/health` | `:128` | `echo "[INFO] ALB Direct /health Check (HTTP Code): $HTTP_CODE"` — code printed, **never tested**; `\|\| echo "000"` on total failure | **No** |
| Banner | `:133` | `DEPLOYMENT COMPLETE & VERIFIED` — unconditional | — |
| Readiness | `:139` | `[SUMMARY] Readiness Score:  100%` — **a literal string, not computed** | — |

**Not one of the four post-deployment checks can produce a non-zero exit.** `set -euo pipefail` is
set at `:5`, but every check is wrapped so it cannot trip. Separately confirmed: the **wrong-account
guard warns rather than exits** (`:62-63`, `[WARNING] Current AWS Account … differs from expected`),
while the **permission preflight does fail closed** (`:68-71`, four `exit 1` paths). So the script is
not uniformly permissive — its *preconditions* are enforced and its *verification* is not.

**NOT executed, deliberately.** Stubbing `aws` would prevent real calls, but this script's purpose is
infrastructure mutation and it sources `acm.sh`/`alb.sh`/`ecs.sh`. The source is unambiguous — four
plain `echo`s with no conditional exit — so execution would add no evidence and carry needless risk.
**Classification: source-verified, NOT executed.**

---

## 6. Phase 31 reconciliation

Inputs byte-identical (tree `4b45a763…`), so expensive scans were **not** repeated. The checker and
all prototype suites were re-run because they are cheap and answer the reconciliation question.

```
python staging/phase31_workflow_invariants.py                      EXIT=1 — 5 violations
pytest staging/phase31_… staging/phase18_… staging/phase17_…       EXIT=0 — 91 passed, 18.13 s
```

All five invariants **remain active and unrepaired**:

| # | Invariant | Current location | Effect |
|---|---|---|---|
| 1 | SAST upload skippable | `05-security.yml` `sast-scan` upload at `:41-46`, no `if: always()`; Bandit at `:36` can fail | the report explaining the blocked deploy is destroyed by that failure |
| 2 | Safety swallowed **and** skipped | `:39` `\|\| echo "Safety advisory logged"`; ordered after Bandit | doubly non-enforcing |
| 3 | Tag-as-digest | `02-build.yml:78` `\|\| echo "$IMAGE_TAG"`, `2>/dev/null` hides cause | `image_digest` can hold a 40-hex tag |
| 4 | Ungated mutable tag | `02-build.yml:76` `docker push …:latest`, no gate dependency | unvalidated image on the tag infra resolves |
| 5 | Digest unconsumed | `03-deploy.yml` has **zero** `image_digest` references; re-resolves by tag | identity chain is tag-based end to end |

### 6.1 Checker self-trust — the four questions the brief raises

| Question | Answer |
|---|---|
| Can it count a workflow's own declared output as a consumer? | **No — fixed in Phase 31.** A consumer must be a *different* workflow. `test_inv5_does_not_count_a_workflow_as_its_own_consumer` is the regression test |
| Does it mistake `pip install bandit` for a scan? | **No — fixed in Phase 31.** `_INSTALL_RE` excludes install steps |
| Is nested YAML quoting parsed correctly? | **Yes**, once fixtures are properly quoted — an unquoted `\|\| :` makes `: ` a mapping indicator. Three fixtures were corrected in Phase 31 |
| Does a negative control actually fail? | **Yes.** Every invariant has a corrected-form fixture asserted silent; 21/21 pass |

---

## 7. Lanes reconciled without re-derivation

Hashes unchanged, so prior findings stand and are **not** re-proved.

| Lane | State | Evidence class |
|---|---|---|
| Release gate wiring | `06-frontend-deploy.yml` `285cc2d1…` ungated; Phase 18 gate (job-level) + Phase 17 (run-level, unsafe) remain **PROTOTYPE ONLY** | unit-tested, **not integration-certified** |
| Trigger architecture (A–D) | `01-pr-check.yml` is `pull_request`-only → cannot produce evidence for a direct-push SHA. Options A–D remain an **owner decision**; no trigger change made, no workflow dispatched | source-verified |
| Bandit parity | CI exit 1 / 9 MEDIUM B608 vs gate test exit 0 / 3 passed (single-module scope). **NOT re-scanned** — inputs identical | historical, valid |
| Image provenance | INV-3/4/5 above; `ecs.tf` and both scripts default to `latest` | source-verified |
| Terraform roots / state posture | **BLOCKED** — terraform and Trivy absent. No `init`, no state access, no lock, no plan, no apply | BLOCKED |
| Key vault D7-1 | 3 `access_token=` sites vs a 2-positional signature; `market.py` can serve authenticated users anonymous data | source-verified |
| Worker lifecycle D7-2 | `portfolio_cache_updater.py` `a74d7644…` unchanged | historical |
| Tenant / RLS | **NOT CERTIFIED** — no PostgreSQL. `FORCE RLS` **not** proposed in isolation | BLOCKED |
| Redis / quota atomicity | **NOT CERTIFIED** — no Redis, no Docker. 5 real-Redis tests remain **SKIPPED, not passed**. Atomicity **not claimed** from mocks | BLOCKED |
| Frontend publication | ungated; a prior build checksum does **not** establish bytes currently served, and none were fetched | source-verified |
| Blue-green helper | `blue-green-deploy.sh` 12,277 bytes read for line endings only; **NOT analysed this phase** | NOT RUN |
| W-6 retry | misclassification confirmed earlier; `retryable` read only by a log line. **No retry introduced** | historical |

---

## 8. Verification matrix

| Command | Exit | Version | Result | Artifact |
|---|---|---|---|---|
| `bash staging/phase32_deploy_sh_harness.sh …` | 0 | WSL bash | **4 unsafe behaviours confirmed by execution** | `phase32_deploy_sh_harness.txt` |
| same, attempt 1 | 1 | | exit 127 — CRLF; led to §4.2 correction | superseded |
| `python staging/phase31_workflow_invariants.py` | **1** | PyYAML 6.0.3 | **5 violations** | `phase32_invariants_recheck.txt` |
| `pytest` ×3 prototype suites | **0** | pytest 9.1.1 | **91 passed**, 18.13 s | `phase32_prototype_suites.txt` |
| `git cat-file blob` ×3 | 0 | | committed blobs pure LF | §4.2 |
| ledger grant scan | 0 | | 0 GRANTED / 5 NOT GRANTED | §3 |

**BLOCKED / SKIPPED / NOT RUN:** Trivy scans, Terraform validate/plan, `kubectl` operations,
PostgreSQL tenant-isolation runtime, Redis integration, Safety, pip-audit, Docker/ECR provenance,
live GitHub Actions gate integration, `01 PR Check` (dispatch forbidden), `infra/deploy.sh`
execution (deliberate, §5), `blue-green-deploy.sh` analysis (not reached), live frontend bytes.
**No missing scanner was counted as a pass; no skip was counted as a pass.**

---

## 9. Findings disposition

**Corrected this phase:** the CRLF-as-defect reading (§4.2) — committed blobs are LF.

**Newly established by execution:** `deploy.sh` exits 0 with all health checks failed **and** prints
the success banner (§4); missing `.env.staging` is silently tolerated (§4, new).

**Confirmed, sharper than before:** `infra/deploy.sh` — none of four post-deployment checks can fail;
two are INFO-only with no branch; readiness is a literal (§5).

**Confirmed unchanged:** all five Phase 31 invariants (§6).

**Unresolved:** trigger architecture (owner decision); Safety blocking policy — re-confirmed that
**no authoritative severity threshold or triage record exists**, so one was **not invented**.

---

## 10. Owner decisions, in dependency order

| # | Decision | Smallest action | Blocker |
|---|---|---|---|
| **1** | `scripts/deploy.sh` failure path | check the health loop's outcome and exit non-zero; gate the banner; require an explicit fully-qualified image or digest; fail when the env file is absent | no grant; owner **UNIDENTIFIED** |
| **2** | `infra/deploy.sh` verification | make the four checks fail closed; make account identity a hard precondition; compute readiness or delete the figure | no grant |
| **3** | `05-security.yml` evidence + enforcement | add `if: always()` to the SAST upload (matching `iac-scan:158`); remove the Safety `\|\| echo` | no grant |
| **4** | Safety blocking policy | decide the severity threshold — **no authority exists in-repo** | policy decision |
| **5** | Image provenance | drop the tag fallback, validate `sha256:`, consume the digest downstream, stop the ungated `latest` push | no grant, no decision ID |
| **6** | `D-P15-4` + `D-P15-3` | grant both; install the **Phase 18** gate (not Phase 17's run-only one) with the `workflow_run` `ref:` pin | no grant |
| **7** | `D-P15-1` Bandit parity | whole-tree scope, negative control, keep the AST guard, 9 rule-specific dispositions | no grant |
| 8 | Tenant/RLS, Redis, Terraform state | grants **plus** PostgreSQL / Redis / terraform+Trivy | grant + infrastructure |

---

## 11. Final integrity verification

| Check | Result |
|---|---|
| `HEAD` / tree / branch / divergence | `ecd23a9e…` / `4b45a763…` / `main` / `0 0` — **all unchanged** |
| Staged / unmerged | **0 / 0** |
| Tracked files modified by this phase | **0** |
| Governed-path status (`.github/ scripts/ terraform/ tests/ backend_app/ infra/`) | only `tests/test_drift_classification.py` — the other workstream's pre-existing change |
| Stashes / worktrees | 2 / 1 — intact |
| Prior reports, logs, prototypes | none overwritten; destination verified absent before writing |
| Secrets | none printed. The gate suites' token is the literal `"fake-token-not-a-credential"`; the harness env file contains `FAKE_SETTING=dummy_value` |
| Forbidden operations | **none** — no add/commit/push/reset/clean/stash/pop/drop/revert/merge/rebase/branch change/worktree change; no CI dispatch, PR, or settings change; no AWS/ECR/S3/CloudFront/DNS mutation, no deploy, rollback, `terraform apply`, database mutation, `kubectl`, or trade. `aws` CLI present but **never invoked** |

**Files created — 6:** `phase32_deploy_sh_harness.sh`, `phase32_deploy_sh_harness.txt`,
`phase32_invariants_recheck.txt`, `phase32_prototype_suites.txt`, this report. (Plus the harness's
temp dir, removed by its own `trap`.)

**Installed fixes: none. Prototypes: the Phase 17/18/31 suites and this phase's harness — all
`PROTOTYPE ONLY`. Design-only: every item in §10.**

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED ·
TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.**

### Next actionable sequence

1. Grant §10.1 and §10.3 — both are small, local, and independently testable; §10.1 now has an
   executed reproduction to verify the fix against.
2. Grant §10.6 together with §10.2.
3. Resolve §10.4 (policy) and the trigger architecture.
4. Provision PostgreSQL, Redis, terraform and Trivy to lift the four BLOCKED lanes.
