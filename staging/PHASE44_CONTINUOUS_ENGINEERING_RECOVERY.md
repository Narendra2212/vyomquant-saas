# Phase 44 — Shell-Environment Restoration and Calibrated Deployment Verification

**Posture (unchanged):** `DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED`

**Implementation status: BLOCKED.** The authorization ledger
`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` (blob `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`)
contains **0** occurrences of `**GRANTED**` and **5** of `**NOT GRANTED**`, rechecked at the
start and end of this phase. **No governed file was modified.** All three deployment scripts
are byte-identical to their pre-phase hashes (verified below). This prompt did not grant
permission to edit governed targets, and none was found elsewhere.

---

## 1. Headline: Phase 42's blocking conclusion was wrong, and the lane is now open

Phase 42 marked the deployment-verification lane **BLOCKED** on the finding that no POSIX
shell was available. That conclusion was **incorrect**. It rested on inspecting only `bash`
as resolved from `PATH`, which is `C:\Windows\system32\bash.exe` — a WSL launcher whose
distribution fails to start (`Bash/Service/CreateInstance/E_FAIL`, 3 of 3 trivial
invocations exit 1).

A fully working shell ships inside the portable Git installation already in this tree:

| Path | Result |
| --- | --- |
| `C:\aerora_quant_backend_updated_final1\Git\bin\bash.exe` | `GNU bash, version 5.3.15(1)-release (x86_64-pc-cygwin)` — **WORKING** |
| `Git\usr\bin\bash.exe`, `Git\usr\bin\sh.exe` | also present |
| `C:\Windows\system32\bash.exe` | WSL launcher, **BROKEN** |

Consequence: three deployment scripts that had never been executed under test are now
covered by calibrated harnesses. **Status: currently reproduced.**

---

## 2. Calibration first — no script result is admissible until controls pass

Every harness in this phase self-validates before interpreting any script output, and every
stub writes a marker file on invocation. A case with **zero stub invocations never started
the script and is reported NOT RUN regardless of its exit code.**

| Harness | Controls | Verdict |
| --- | --- | --- |
| `probe_p44_deploy_harness_gitbash.py` | 5/5 pass | `HARNESS CALIBRATED: True` |
| `probe_p44_infra_deploy_harness.py` | 4/4 pass | `CALIBRATED: True` |
| `probe_p44_bluegreen_harness.py` | 4/4 pass | `CALIBRATED: True` |

This discipline earned its keep twice more in this phase:

- The `infra/deploy.sh` injection case first came back `exit 2, 0 stub calls`. My own bash
  `export K=V;` construction broke on the payload's single quotes, so bash exited before
  starting the script. Had I trusted the exit code I would have reported "no injection" —
  a false negative. Scenario values now pass through the process environment.
- In the blue-green harness, the empty-tag guard case legitimately records **0** kubectl
  calls, because the script correctly refuses at `:68-71` *before* any kubectl call. The
  blanket "0 calls ⇒ NOT RUN" rule mislabelled that as a harness failure and dragged the
  calibration flag to False. The rule now carries an explicit per-case exemption. **A
  marker rule that cries wolf gets ignored; this is a correction to the method, not just
  to one case.**

Methodological note for future phases: `local VAR=$(failing_cmd)` does **not** trip
`set -e` (measured: continues, `VAR` empty, exit 0), whereas a plain `VAR=$(failing_cmd)`
**does** abort (measured). This asymmetry is the mechanism behind several findings below.

---

## 3. `scripts/deploy.sh` — Phase 37 re-upgraded to CURRENTLY REPRODUCED

Blob `e06423f2b3b075add617778c662146c0c4754657`; worktree sha256
`be374b3b684816a8121e3c19cc411aeca4043c25f77d6e0626529d3fc2c3b89d`, unchanged.
Tested against the **committed** blob, not the worktree copy (the worktree has 82 CRLF
pairs under `core.autocrlf=true` with no `.gitattributes`; bash dies on `set -e\r`).

Phase 37 was downgraded to *historical, not revalidated* by Phase 42. It is now
**currently reproduced**, and this time with the positive control that both Phase 37 and
Phase 42 lacked:

| case | exit | banner | hc-pass | attempts | curl | kubectl | image |
| --- | --- | --- | --- | --- | --- | --- | --- |
| all health fail | 0 | True | False | 5 | 5 | 5 | latest |
| **first health pass (positive control)** | **0** | **True** | **True** | **0** | **1** | **5** | latest |
| transient → pass (retry control) | 0 | True | True | 2 | 3 | 5 | latest |
| `.env` absent | 0 | True | True | 0 | 1 | 5 | latest |
| no image argument | 0 | True | True | 0 | 1 | 5 | latest |
| SAFE patch, all health fail | **1** | **False** | False | 5 | 5 | 4 | latest |
| SAFE patch, first health pass | 0 | True | True | 0 | 1 | 5 | latest |

- **The defect:** total health-check failure and immediate success produce an *identical*
  observable outcome — exit 0 and `🎉 Deployment to $ENVIRONMENT complete!`. The exit code
  and the banner carry **zero information** about health. (`:62-69` has no success flag and
  no post-loop check; the banner at `:77` is unguarded.)
- **Missing config is silent:** with `.env.staging` absent, `:21` fails inside
  `export $(grep … | xargs)`; because `export` with no arguments returns 0, `set -e` does
  not fire. Exit 0, banner printed.
- **Mutable tag:** `IMAGE_TAG=${2:-latest}` (`:9`) — every case resolved to `latest`.
- **The proposed fix discriminates correctly.** A temp-only `HEALTH_OK` flag plus a
  post-loop check turns all-fail into exit 1 with no banner, while leaving the success path
  at exit 0. The `kubectl` count drops 5 → 4 in the all-fail case, confirming the guard
  sits exactly at the right line. *(Applied to a temp copy only; the repository file is
  unchanged and no grant exists to apply it.)*

### 3.1 Ordered call trace — mutation precedes verification, and nothing rolls back

| # | all-health-fail case |
| --- | --- |
| 1 | `kubectl cluster-info` |
| 2 | `kubectl get namespace staging` |
| **3** | **`kubectl set image deployment/trading-backend backend=v1.2.3 -n staging` ← MUTATION** |
| 4 | `kubectl rollout status … --timeout=300s` |
| **5** | **`curl -sf …/health` ← first verification** |
| 6–14 | 4 further failed `curl` + `sleep 10` pairs |
| 15 | `kubectl get pods …` |

The cluster is mutated at step 3, four steps before anything is verified, and **no rollback
is attempted** after five consecutive failures — step 15 is a read.

**Bounding the claim honestly:** step 4 (`kubectl rollout status`) is a real gate. In a live
cluster an unpullable image would fail there and `set -e` would abort before the health
loop. The realistic exposure is therefore the case where **pods start but the application
`/health` endpoint fails** — bad config, unreachable database, a dependency down. Then
`rollout status` passes, all five health checks fail, and the script reports success and
leaves the broken revision serving traffic. That is the scenario this reproduces.

---

## 4. `infra/deploy.sh` — a verification engine whose results are discarded

Blob `915dcca935826498db67eff771be8efb23e1929c`; worktree sha256
`1d931af6527e2645160e89fe86809501df1702544bd4d847ca0298823767cc8a`, unchanged. 140 lines.

**Isolation, since the real AWS CLI *is* installed** at
`C:\Users\aa\aws_cli_extract\Amazon\AWSCLIV2\aws.exe`: a stub `aws` was placed first on
`PATH` and proven to shadow the real binary by a control asserting both `command -v` and a
`STUB` version string; every invocation was recorded and a case with no recorded
invocations is reported NOT RUN; credentials and config were redirected to nonexistent
temp paths and IMDS disabled. The provisioning sub-scripts `acm.sh`/`alb.sh`/`ecs.sh` were
stubbed in the temp `SCRIPT_DIR`. **No real AWS call, no network, no infrastructure change.**

| case | exit | `DEPLOYMENT COMPLETE & VERIFIED` | `Readiness Score: 100%` | sub-scripts |
| --- | --- | --- | --- | --- |
| all signals healthy (positive control) | 0 | True | True | 3 |
| auth empty (negative control) | 1 | False | False | 0 |
| missing `ecs:DescribeServices` (negative control) | 1 | False | False | 0 |
| `acm.sh` fails | 1 | False | False | 1 |
| wrong AWS account | 0 | **True** | **True** | 3 |
| **all four verification signals BAD** | **0** | **True** | **True** | 3 |
| identity parse failure | 0 | True | True | 3 |

**Findings (currently reproduced):**

- **I-1 — the verification engine is decorative.** With ALB state `UNKNOWN`, target health
  `unhealthy`, ECS tasks `0/4`, and HTTP `000`, the script prints
  `DEPLOYMENT COMPLETE & VERIFIED` and `[SUMMARY] Readiness Score:  100%` and exits 0. All
  four checks are computed and then discarded (`:103`, `:110`, `:121`, `:128` are
  `[WARN]`/`[INFO]` only; the banner at `:133` and the score at `:139` are unconditional).
  `Readiness Score:  100%` is a **hardcoded literal**, not a computed value.
- **I-2 — the production-account guard is advisory.** `:62-63` warns on an account mismatch
  and continues to full completion. A run against the wrong AWS account is indistinguishable
  in exit code and final banner from a correct one.
- **I-3 — identity verification fails open.** `:59` parses the account id with
  `|| echo ""`; a parse failure yields an empty id, which trivially mismatches at `:62` and
  so degrades to the same advisory warning. Exit 0, banner, 100%.
- **I-4 — pre-flight gates are genuinely fail-closed.** Absent authentication and each
  missing IAM permission abort with exit 1, **zero sub-scripts invoked** (`:53-56`,
  `:68-71`). A failing `acm.sh` also aborts, as `set -euo pipefail` requires. **These are
  passes and should be preserved by any fix.**

### 4.1 AWS output is interpolated into Python source — three sites

`:59`, `:115` and `:116` embed command output into a Python program via a triple-quoted
literal, e.g. `$PYTHON_BIN -c "import json; print(json.loads('''$CALLER_IDENTITY''')…)"`.
At `:115`/`:116` the interpolated value is the **entire** `describe-services` JSON.

- **Demonstrated (CWE-95):** a crafted `sts get-caller-identity` response broke out of the
  literal and executed attacker-supplied code — the probe wrote a marker file instead of
  doing anything harmful. The same breakout succeeded independently at the `:115`/`:116`
  sink. Payload contained no `"`, `$` or backtick, so it survived the bash double-quoted
  context unchanged.
- **Realistic impact without code execution:** a `'''` sequence anywhere in the ECS JSON
  makes both count parses fail; `|| echo "0"` then yields `0/0`, producing
  `[WARN] ECS Service Status: 0/0 Tasks Running` — and, per I-1, still exit 0 and
  `Readiness Score: 100%`.
- **Severity, stated honestly:** exploitability requires attacker influence over AWS CLI
  stdout — a shadowed or compromised `aws` binary, or a tampered API response — not an
  unauthenticated remote attacker. **I did not identify an attacker-controllable AWS field
  that admits `'''`, and I did not exhaustively enumerate the `describe-services` schema.**
  The sink is real and demonstrable; the delivery path is **not established**.

---

## 5. `scripts/blue-green-deploy.sh` — `"unknown"` reaches a write

Worktree sha256 `105de9d6e70c35a8282fc3b60265e6130324c65182170216bfbf177f2430868e`,
unchanged. `kubectl` is **not installed** on this machine; a recording stub was used and
mutating verbs were captured separately. The open question from prior phases — can the
`|| echo "unknown"` read helpers at `:50`/`:54`/`:58` reach a write? — is now answered:
**yes.**

**B-1 (currently reproduced).** With the service selector readable as `green` but the green
*deployment* read failing, `promote()` takes `GREEN_IMAGE="unknown"` from `:236` and writes
it straight to the **blue** deployment — the stable rollback target — with no validation:

```
WRITE set-image  set image deployment/trading-backend-blue backend=unknown -n …
WRITE scale      scale deployment/trading-backend-blue --replicas=3 -n …
WRITE patch      patch service trading-backend-active … "value": "blue"
WRITE scale      scale deployment/trading-backend-green --replicas=0 -n …
```

…then prints `[SUCCESS] ✅ Promotion complete`, `Blue is now running the new version`, a
success notification, and **exits 0**. The only guard at `:238` checks the active *colour*,
never the image. The precondition is a realistic post-incident state: the service selector
still says `green` while the green Deployment object is missing or unreadable (deleted by a
prior failed deploy, or RBAC that grants services but not deployments).

**Blast radius, bounded by measurement (B-10).** Forcing `kubectl rollout status` (`:257`)
to fail aborts the function after **2** writes, and the traffic patch is **not** reached:

| | writes | traffic patched to the bad blue | green scaled to 0 | exit |
| --- | --- | --- | --- | --- |
| `rollout status` succeeds | 4 | **yes** | **yes** | **0 + `[SUCCESS]`** |
| `rollout status` fails | 2 | no | no | 1 |

So the **unconditional, demonstrated** damage is overwriting the stable deployment's image
with a non-image string. Escalation to a full outage (traffic moved onto the broken blue
*and* the known-good green destroyed, reported as success) additionally requires
`rollout status` to report success. **Whether a real cluster's `rollout status` would pass
or fail here is not determined by this probe** and is an owner question.

**Other results:**

- **B-2** `switch` with an unreadable selector logs the fabricated assertion
  `[INFO] Current active: BLUE`, patches the service anyway (1 write), then detects the
  unchanged selector and exits 1.
- **B-3** `rollback` with an unreadable selector likewise patches blind (1 write), then
  correctly reports `Rollback failed!` and exits 1.
- **B-5 / B-6 — `validate_green()` is genuinely fail-closed.** A health failure at
  iteration 1, at iteration 5, or a readiness failure each abort with exit 1. The happy
  path requires **10** consecutive passes (`:131-142`). **This is the control
  `scripts/deploy.sh` is missing, already present in the same repository.**
- **B-7** `deploy latest` is accepted — only an *empty* tag is refused (`:68-69`). Fourth
  mutable-tag route, consistent with the unresolved provenance lane.
- **B-8** `:91 local BLUE_REPLICAS=$(kubectl get …)` is unguarded; per the `local`/`set -e`
  asymmetry it yields an empty string, and the harness recorded
  `scale deployment/trading-backend-green --replicas=` — an empty replica count reaching a
  write. *The exit 0 for this case is an artifact of the stub accepting it; a real kubectl
  would reject `--replicas=`, so the practical impact is a confusing abort, not corruption.*
- **B-9** an accepted-but-ineffective patch is detected and reported (exit 1).
- Guard controls performed **zero** writes, as required.

---

## 6. Evidence labels

| Item | Label |
| --- | --- |
| Git Bash available and working; Phase 42's shell conclusion wrong | **currently reproduced** |
| All three harnesses calibrated (controls + stub markers + positive controls) | **test passed** |
| `scripts/deploy.sh` false green, incl. valid positive control | **currently reproduced** (re-upgraded from *historical*) |
| `scripts/deploy.sh` mutation-before-verification, no rollback | **currently reproduced** |
| `infra/deploy.sh` I-1 … I-4 | **currently reproduced** |
| `infra/deploy.sh` Python-source injection sink at `:59`, `:115`, `:116` | **currently reproduced** (sink); delivery path **not established** |
| `scripts/blue-green-deploy.sh` B-1 … B-10 | **currently reproduced** |
| Real-cluster behaviour of `kubectl rollout status` on a bogus image | **not run / owner decision required** |
| Operational liveness of all three scripts (0 automated callers found) | **owner decision required** |
| Trivy / Terraform / Docker / kubectl / PostgreSQL / Redis lanes | **not run** (tools absent) |
| Tenant isolation matrix (4-of-150) | **not run** — neither reproduced nor refuted |
| Any repair to any governed file | **blocked** — 0 grants |

---

## 7. Exit integrity

| Check | Value |
| --- | --- |
| `HEAD` = `origin/main` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` |
| branch / divergence | `main` / `0 0` |
| reflog head | `ecd23a9e HEAD@{0}: commit: launch phase 1` |
| staged / unstaged / unmerged | 0 / 2 / 0 |
| tracked files / worktrees / stashes | 5705 / 1 / 2 |
| ledger blob · `**GRANTED**` · `**NOT GRANTED**` | `7e238620…` · **0** · 5 |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (concurrent) | `0c9981b2817e47b4faa915b69de204ff78f87af7` — unchanged |
| `tests/test_drift_classification.py` (concurrent) | `99a2917ac0a65761ef3dfe9299ed1df87647552a` — unchanged |
| `scripts/deploy.sh` | `be374b3b…b89d` — byte-identical |
| `infra/deploy.sh` | `1d931af6…cc8a` — byte-identical |
| `scripts/blue-green-deploy.sh` | `105de9d6…868e` — byte-identical |

No git mutation of any kind was performed: no stage, commit, push, reset, stash, clean,
revert, merge, rebase, branch switch, history rewrite or worktree change. No CI dispatch,
no PR, no deploy, no cloud or Kubernetes mutation, no production database access, no live
trading. `exchange_keys.json`, decrypted credentials, private keys, tokens and sensitive
environment files were not opened.

**Concurrent-session observation.** Nine files (`p38_*.py/.txt`,
`PHASE38_CONTINUOUS_IMPLEMENTATION_AND_RELEASE_RECOVERY.md`,
`PHASE42_SUPPLY_CHAIN_AND_CONTINUOUS_RELEASE_RECOVERY.md`) were written into `staging/`
between 01:32 and 01:57 — interleaved with this phase's own runs, the last of them *after*
my final probe. **I did not write any of them.** Destination-absence was verified
immediately before every write in this phase, including a final recheck of this report's
filename. The two tracked concurrent files retain the hashes above. This is direct evidence
that the destination-absence rule is a live necessity, not a formality.

### Artifacts written this phase (all destinations verified absent first)

`staging/probe_p44_deploy_harness_gitbash.py` · `staging/p44_deploy_harness_gitbash.txt` ·
`staging/probe_p44_infra_deploy_harness.py` · `staging/p44_infra_deploy_harness.txt` ·
`staging/probe_p44_bluegreen_harness.py` · `staging/p44_bluegreen_harness.txt` ·
`staging/probe_p44_call_order.py` · `staging/p44_call_order.txt` · this report.

---

## 8. Grant requests, in priority order

1. **`scripts/deploy.sh`** — track the health outcome, guard the success banner, fail
   explicitly on missing config, parse `.env` safely, and reject a mutable-only image
   reference. The patch is already proven to discriminate correctly (§3). Pattern to copy
   is in this same repository: `validate_green()` at `blue-green-deploy.sh:131-142`.
2. **`scripts/blue-green-deploy.sh:238`** — reject `GREEN_IMAGE` values that are `unknown`
   or empty *before* the `:247` write, and validate the `:91`/`:252` replica reads. Highest
   severity of the three, because the write lands on the rollback target.
3. **`infra/deploy.sh`** — gate the `:133` banner and the `:139` score on the four signals
   already computed; make the `:62` account mismatch fail closed; replace the three
   `python -c` string interpolations with `--query`/`stdin` so AWS output never becomes
   Python source. Preserve the fail-closed pre-flight gates (I-4).
4. **G-1 `05-security.yml`** — restore reachability of the second IaC scan, add
   `if: always()` to the artifact uploads, and make Safety independent of Bandit.
5. **`02-build.yml:78`** — adopt the fail-closed digest pattern already at
   `03-deploy.yml:168-172` instead of `|| echo "$IMAGE_TAG"`.
6. **Correctness-route decision before verifier promotion.** Promoting either release-gate
   prototype first would deadlock every push deployment, since both require `01 PR Check`,
   which is `pull_request`-only.

**Owner decisions outstanding:** whether these three scripts are live or dead (no automated
caller was found for any of them across 191 files in `.github/workflows`, `scripts`,
`infra` — scope-limited); whether Safety is mandatory or advisory; ECR tag immutability
(`image_tag_mutability` has 0 references in Terraform); and whether a real cluster's
`rollout status` would catch the `unknown` image in §5.
