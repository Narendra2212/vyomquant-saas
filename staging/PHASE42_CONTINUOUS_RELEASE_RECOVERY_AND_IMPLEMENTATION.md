# Phase 42 — Continuous Release Recovery: Harness Calibration Attempt and an Honest Downgrade

**Destination verified ABSENT before writing. No prior report, probe or artifact overwritten.**

---

## 1. Executive verdict

| | |
|---|---|
| **Governed files changed** | **0** — zero applicable grants |
| **What materially improved** | A **machine-checked static structural confirmation** of the `deploy.sh` defect that requires no shell, and a **root-cause diagnosis** of why the shell harness cannot be trusted on this host |
| **What got worse (honestly)** | **Phase 37's executed reproduction is DOWNGRADED to `HISTORICAL, NOT REVALIDATED`.** `bash` is non-functional on this host now, so it cannot be re-executed |
| **Harness calibration (§21 requirement)** | **NOT ACHIEVED — BLOCKED.** No valid positive control was obtained |
| **Release decision** | **DO NOT RELEASE** |

`VER` **Authorization: zero grants.** Ledger `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`,
`**GRANTED**` = **0**, `:802` = `NOT GRANTED`. No grant inferred from this prompt, its example
wording, a prior prompt, a report, a role label, or silence.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 2. Preflight

| Property | Entry | Exit |
|---|---|---|
| `HEAD` / `origin/main` / divergence | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / same / **`0 0`** | **unchanged** |
| `git reflog -1` | `ecd23a9e` | unchanged |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |
| Ledger blob | `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | unchanged |

**Concurrent modifications preserved byte-for-byte**, hashes identical to the Phase 36/37 baseline:
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` = `0c9981b2817e47b4faa915b69de204ff78f87af7` ·
`tests/test_drift_classification.py` = `99a2917ac0a65761ef3dfe9299ed1df87647552a`.

**Target under test:** `scripts/deploy.sh` — committed blob
`e06423f2b3b075add617778c662146c0c4754657`; worktree sha256
`be374b3b684816a8121e3c19cc411aeca4043c25f77d6e0626529d3fc2c3b89d`, **identical before and after
the probe**.

`VER` **Line-ending configuration:** `core.autocrlf=true`, `core.eol` unset, **no `.gitattributes`**.
This is why the worktree copy is CRLF while the committed blob is LF — the distinction the brief
requires in §14 and which Phase 37 recorded.

---

## 3. Authorization

| Lane | Governed paths | Status |
|---|---|---|
| `scripts/deploy.sh` repair | `scripts/deploy.sh` | **BLOCKED — NO GRANT** |
| `infra/deploy.sh` repair | `infra/deploy.sh` | **BLOCKED — NO GRANT** |
| Blue-green lifecycle | `scripts/blue-green-deploy.sh` | **BLOCKED — NO GRANT** |
| Image provenance | `02-build.yml`, `03-deploy.yml`, `ecs_deploy_and_diagnose.py`, task defs, Terraform | **BLOCKED — NO GRANT** |
| Release verifier | `staging/phase18_release_gate.py` → `scripts/`, `tests/` | **BLOCKED — NO GRANT** + owner decision |
| Security workflow / Bandit parity | `05-security.yml`, gate test | **BLOCKED — NO GRANT** |
| Tenant / vault / W-6 / workers | respective modules | **BLOCKED — NO GRANT** |
| Terraform roots | 3 roots | **BLOCKED — NO GRANT** + tooling absent |

**Applied-change scope: none.** Artifacts created: 3, all new, all under `staging/` —
`probe_p42_deploy_harness_calibrated.py`, `p42_deploy_harness_calibrated.txt`, this report.

---

## 4. Deployment false-green — calibration attempt, and the downgrade

### 4.1 What I set out to fix

Phase 37 reproduced the all-fail false green but could not establish a valid **all-pass control**
because stub resolution was ambiguous. §3.4/§21 require that the harness discriminate before its
results are trusted. So this phase rebuilt the harness with two additions:

1. **No Windows path in `PATH`.** Bash computes the stub directory itself:
   `bash -c 'export PATH="$PWD/stubbin:$PATH"; exec ./deploy.sh'`.
2. **Stub marker files.** Every stub appends its argv to a marker file, so invocation is
   **proven**, not assumed — directly answering the brief's "confirm command resolution using a
   known stub marker".

### 4.2 Root cause of the ambiguity — `bash` is a non-functional WSL stub

`VER` `bash` resolves to `C:\Windows\system32\bash.exe` (WSL). Measured this phase:

```
try1: exit=1  "The distribution failed to start. Error code: 6, failure step: 2
               Error code: Bash/Service/CreateInstance/E_FAIL"
try2: exit=1  identical
try3: exit=1  identical
bash -n -c 'true' : exit=1
```

**Three of three trivial invocations fail. The WSL distribution cannot start, so no shell code
executes at all.** Earlier in this campaign bash *did* work — Phase 37 captured genuine bash
diagnostics (`$'\r': command not found`, `set: - : invalid option`) — so this is an
**environment degradation during the campaign**, not a pre-existing condition.

### 4.3 Harness result — NOT CALIBRATED, and why it must not be misread

| case | exit | banner | hc-pass | attempts | curl calls | kubectl calls |
|---|---|---|---|---|---|---|
| REAL all health fail | 1 | False | False | 0 | **0** | **0** |
| REAL first health passes | 1 | False | False | 0 | **0** | **0** |
| REAL transient then passes | 1 | False | False | 0 | **0** | **0** |
| REAL `.env` absent | 1 | False | False | 0 | **0** | **0** |
| REAL no image arg | 1 | False | False | 0 | **0** | **0** |
| SAFE all health fail | 1 | False | False | 0 | **0** | **0** |
| SAFE first health passes | 1 | False | False | 0 | **0** | **0** |

| Calibration gate | Result |
|---|---|
| STUB PROOF | **FAIL** — 0 curl, 0 kubectl invocations |
| POSITIVE CONTROL | **NOT RUN** |
| RETRY CONTROL | **NOT RUN** |
| NEGATIVE CONTROL (real must fail) | **NOT RUN** |
| DISCRIMINATION (safe vs unsafe) | **FAIL** — identical output for both |
| **HARNESS CALIBRATED** | **False** |

> **The uniform `exit 1` is WSL refusing to start, not the script running.** Read naively, this
> table says "the defect does not reproduce." That reading would be wrong.

**This is the methodological payoff of the marker design.** Without the stub markers, seven cases
exiting 1 with no success banner would have looked like a clean "defect disproven" result. The
markers showed **0 invocations**, which is only possible if the script never ran. **That is the
third harness-validity defect in this lane across three phases** — CRLF (Phase 37), charmap
decoding (Phase 37), and now a dead WSL distribution — **each of which would have produced a
false negative.**

### 4.4 Required downgrade of Phase 37

Per the brief's §1.1 taxonomy:

| Finding | Phase 37 status | **Phase 42 status** |
|---|---|---|
| All-fail → exit 0 + success banner | executed reproduction | **HISTORICAL, NOT REVALIDATED** |
| `.env.staging` absent tolerated | executed | **HISTORICAL, NOT REVALIDATED** |
| Image defaults to `latest` | executed | **HISTORICAL, NOT REVALIDATED** (static evidence below still holds) |
| All-pass control | invalid | **NOT RUN — BLOCKED** |

I am **not** claiming the defect is disproven. I am recording that **it cannot be re-executed on
this host**, which is a weaker evidential position than Phase 37 asserted.

### 4.5 Static structural confirmation — requires no shell

Machine-checked against the **committed** blob, so it is unaffected by WSL or CRLF:

```
health-check loop present            : True
success flag tracked inside the loop : False
success banner at                    : line 77
banner guarded by health outcome     : NO
```

Combined with the source text — the loop at `:62-69` records no outcome, and `curl -sf` failing
**inside an `if` condition** does not trip `set -e` — the control-flow defect is established
**statically**. `IMAGE_TAG=${2:-latest}` at `:9` is likewise plain source fact.

**What static evidence cannot establish:** the actual runtime exit code, or whether a mutating
`kubectl set image` is reached. Those require a working shell. **Classified `BLOCKED`.**

---

## 5. Deployment fixes — unapplied patch package

**Status: NOT APPLIED. No grant.** Bound to committed blob
`e06423f2b3b075add617778c662146c0c4754657` (sha256
`be374b3b684816a8121e3c19cc411aeca4043c25f77d6e0626529d3fc2c3b89d`).

| # | Invariant restored | Minimal change |
|---|---|---|
| 1 | Exhausted health checks must exit nonzero | add `HEALTH_OK=0`; set `=1` on success; after `done`, `if [ "$HEALTH_OK" -ne 1 ]; then echo …; exit 1; fi` |
| 2 | Success banner conditional | `:77` becomes reachable only past that guard |
| 3 | Required config fails explicitly | test `-r ".env.$ENVIRONMENT"` before `:21`, exit nonzero |
| 4 | Safe config parsing | replace `export $(grep -v '^#' … | xargs)` with a line-wise reader handling blanks, comments, quoted values, spaces and empty values — the current form mangles `KEY="a b c"` into three words |
| 5 | Immutable image identity | reject empty/mutable-only references; require a repository-qualified `sha256:` digest; no `latest` fallback |

The exact safe variant was written and exercised **only inside the temp harness** (never in the
repository) and is retained in `staging/probe_p42_deploy_harness_calibrated.py` as
`SAFE_PATCH_OLD`/`SAFE_PATCH_NEW` for whoever receives the grant. **Its behaviour is unvalidated**
because the harness could not run.

---

## 6–13. Remaining lanes

No lane was editable. Status carried forward, hash-verified, with provenance:

| Lane | Status | Evidence class |
|---|---|---|
| `infra/deploy.sh` — `DEPLOYMENT COMPLETE & VERIFIED` `:133`, hardcoded `100%` `:139`, warn-only account guard `:62-63`, warn-only ALB `:103` / ECS `:121`, **fail-closed permission preflight `:68-71` to preserve** | **HASH-VERIFIED AND REUSED** (blob `915dcca935826498db67eff771be8efb23e1929c`) | source |
| Blue-green lifecycle — no `blue-green-deploy.yml`; script refuses an empty tag; `|| echo "unknown"` on read helpers; rollback/promote branches **not fully traced** | **HISTORICAL + partially traced**; scenario matrix **NOT RUN** (shell blocked) | source |
| Image provenance — `02-build.yml:78` tag-as-digest fallback; `03-deploy.yml:168-172` fail-closed guard **preserve**; `latest` in six `terraform/ecs.tf` refs + task-definition JSON; `scripts/deploy.sh:9`; **ECR immutability UNKNOWN** (0 `aws_ecr_repository` declarations) | **HASH-VERIFIED AND REUSED** | source |
| Release verifier — Phase 18 stronger (run+job level, 28 tests); job-ID vs API display-name mapping unresolved; `01-pr-check.yml` `pull_request`-only ⇒ **promotion would deadlock the push path** | **HASH-VERIFIED AND REUSED**; owner decision | unit |
| Security workflow — second IaC root unreachable after first failure; SAST upload lacks `if: always()`; Safety skipped-after-Bandit **and** `|| echo` non-blocking; nested root declares `backend "s3"` + `aws_secretsmanager_secret` | **HASH-VERIFIED AND REUSED** | source |
| Bandit — **56 directives / 17 files / 54 colon-form / 2 space-form / 0 bare**; 13 B608-scoped in `transactional_execution_manager.py`; parity gap: CI `-r backend_app -ll` vs single-module gate test | **HASH-VERIFIED AND REUSED** (gate test 3 passed / 103.44s, Phase 36) | scanner |
| Tenant isolation | **BLOCKED — PostgreSQL absent. NOT CERTIFIED.** 4-of-150 neither reproduced nor refuted | — |
| Workers / vault / W-6 — D7-1 three invalid callers vs nine legitimate; **D6-D precedes D7-2** (`MockEngine.__getattr__` returns a noop `None`); `.retryable` has no control-flow consumer | **HASH-VERIFIED AND REUSED** | source |
| Redis / queues | **BLOCKED — Redis absent** | — |
| Terraform — 3 roots (`terraform/`, `terraform/redis-only/`, `aerora_quant_platform/infrastructure/terraform/`); **`infra/terraform/` confirmed absent**; SG-collision hypothesis **remains withdrawn** | **CURRENTLY REPRODUCED** (root list re-measured) | source |
| Frontend — published at `ecd23a9e` with `05 Security` red is **historical**; current edge state **UNKNOWN**; artifact-size floor 8/8 and **not** release authorization | **HISTORICAL** | CI-observed |

---

## 14. Test ledger

| Command | Exit | Result | Evidence type |
|---|---|---|---|
| `python staging/probe_p42_deploy_harness_calibrated.py` | 0 | **HARNESS NOT CALIBRATED** — 7 cases, 0 stub invocations | invalid — environment failure |
| `bash -c 'echo BASH_OK'` ×3 | 1,1,1 | `distribution failed to start … E_FAIL` | **BLOCKED** |
| `bash -n -c 'true'` | 1 | same | **BLOCKED** |
| Static structural check of committed blob | 0 | loop present, **no success flag**, banner line 77 **unguarded** | static, shell-independent |
| `git config core.autocrlf` / `core.eol` / `.gitattributes` | 0 | `true` / unset / absent | config |
| Terraform root enumeration | 0 | 3 roots; `infra/terraform` absent | source |
| Ledger + grant recheck | 0 | 0 grants | governance |

**Reused, not re-run** (source, test, config and tool-version inputs hash-verified unchanged):
gate test **3 passed / 103.44s** (Phase 36); phase18 **28 passed**; phase17 **42 passed**;
`tests/test_release_artifacts.py` **8 passed**; bounded 8-file regression **202 passed / 5 skipped /
1 xfailed** (Phase 35). The 5 skips are real-Redis; the xfail is strict. **Neither is a pass.**

**NOT RUN / BLOCKED:** every shell test (**bash non-functional**); `infra/deploy.sh` and blue-green
matrices (**shell blocked**, and execution against infrastructure prohibited); tenant matrix
(**PostgreSQL absent**); Redis integration (**absent**); Trivy / Terraform / Docker / kubectl
(**absent**); full default suite (**times out**); all implementation-acceptance tests (**no grant —
no change exists to validate**).

**Negative controls:** deployment safe-vs-unsafe discrimination → **NOT RUN (blocked)**. All others
→ **NOT RUN**, no authorized change to control.

---

## 15–17. Owner decisions and blockers

> **1 · `scripts/deploy.sh` repair (§5).** Still the cheapest high-value grant. Justification is now
> **static-structural plus Phase 37 historical**, not currently executed — state that honestly when
> approving.
>
> **2 · A working POSIX shell** for deployment-script validation. **This is a new blocker.** WSL's
> distribution fails to start, so no shell test can run here. Options: repair WSL, install Git Bash,
> or run the harness in CI. Without one, §21's calibration requirement cannot be met by anyone on
> this host.
>
> **3 · Correctness-workflow architecture** — push-triggered workflow / extend `01-pr-check.yml` to
> pushes / PR-only with explicit merge-commit evidence binding / separate release-authorization
> stage. Gates the verifier lanes.
>
> **4 · Security scan reachability + evidence retention** (`05-security.yml`), and **5 · Safety
> policy** (mandatory vs advisory, **and** independent of Bandit either way).
>
> **6 · Bandit parity** — extend the gate test to CI's recursive scope with a negative control. **No
> suppression change requested**: all 56 are rule-scoped and reviewed.
>
> **7 · Build digest** — `02-build.yml:78`, adopting the `03-deploy.yml:168-172` pattern.
>
> **8 · Operational facts the repository cannot answer** — which deploy scripts are supported entry
> points; whether `ecs-task-definition-full.json` is ever applied; whether **ECR tag immutability**
> is enabled.
>
> **9 · Remaining** — `infra/deploy.sh` honesty + account-guard policy; D7-1 (only the three
> `load_decrypted_keys` callers); D6-D before D7-2; W-6 classification-only; tenant isolation on a
> disposable non-owner service, **FORCE RLS not first**.

---

## 18. Repository integrity and prohibited-operation checklist

**All confirmed NOT performed:** stage, commit, push, reset, stash, pop, drop, clean, revert, merge,
rebase, branch switch, history rewrite, worktree change. No CI dispatch, PR, repository-settings or
branch-protection change. No deploy, rollback, traffic promotion, asset publication, CDN
invalidation, DNS change. No ECR, ECS task-definition, IAM, S3, cloud, Kubernetes, Terraform-state
or production-database mutation. No `terraform apply`, mutating `kubectl`, production migration, or
trading order. Live trading not enabled. **`exchange_keys.json` was not opened** despite being in
the editor's open set; no credential, token, DSN, decrypted key or sensitive environment value was
read or printed.

No gate weakened; no assertion, threshold, anti-vacuity check, strict xfail, RLS requirement or
fail-closed behaviour altered; no suppression added or removed; **no skipped, blocked or unrun check
reported as a pass**.

**Preserved and verified at exit:** both concurrent modifications at their exact entry hashes, all
untracked artifacts, every prior report and probe, 2 stashes, 1 worktree, `scripts/deploy.sh`
byte-identical.

**Release posture unchanged: DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED
· TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED.** Engineering evidence improved
in one respect (static confirmation) and weakened in another (loss of executable reproduction);
neither changes any gate.
