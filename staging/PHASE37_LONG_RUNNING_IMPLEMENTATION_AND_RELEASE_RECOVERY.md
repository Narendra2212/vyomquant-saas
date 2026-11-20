# Phase 37 — Long-Running Implementation & Release Recovery: Deployment False-Green Reproduced

**Destination verified ABSENT before writing. No prior report, prototype, stash or worktree altered.**

---

## A. Executive verdict

| | |
|---|---|
| **What materially changed** | **Nothing in the repository.** Grants remain zero, so **0 governed files were edited.** |
| **What was newly established** | **The `scripts/deploy.sh` false-green defect is now reproduced by execution**, not asserted: five failed health-check attempts, no success observed, **exit 0**, success banner printed. Plus missing-config tolerance and the `latest` default, both executed. |
| **Release readiness** | **Not improved.** This phase improved *evidence*, not posture. |

`VER` **Authorization: zero grants.** Ledger `7e238620bc22f0ad246f0ebc373b7032c6b28ef0`,
`**GRANTED**` count **0**, `:802` = `NOT GRANTED`. No grant was inferred from this prompt, its
example wording, a prior prompt, a report, or silence.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## B. Integrity and authorization

| Property | Entry | Exit |
|---|---|---|
| `HEAD` / `origin/main` / divergence | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / same / **`0 0`** | **unchanged** |
| `git reflog -1` | `ecd23a9e` | unchanged |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |
| Ledger hash | `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | unchanged |

**Concurrent modifications preserved byte-for-byte** — hashes match the Phase 36 baseline exactly:

| Path | Hash |
|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

Tree hash reused from Phase 35/36 (`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`); inputs unchanged, so
not recomputed — and when it was computed it used a redirected object directory so **nothing was
written to the shared Git object database**.

**Target hashes measured this phase:** `scripts/deploy.sh` =
`e06423f2b3b075add617778c662146c0c4754657` (git blob), sha256 of the worktree bytes
`be374b3b684816a8121e3c19cc411aeca4043c25f77d6e0626529d3fc2c3b89d`, **unchanged before and after the
probe**. `infra/deploy.sh` = `915dcca935826498db67eff771be8efb23e1929c`.

**Governed files changed: NONE.** Files created: 3, all new, all under `staging/` —
`probe_p37_deploy_false_green.py`, `p37_deploy_false_green.txt`, this report.

**Environment:** Python 3.12.10 · pytest · bandit 1.9.4 · PyYAML 6.0.3 · **bash present** ·
GitHub CLI (read-only) · **Docker / Trivy / Terraform / kubectl / PostgreSQL / Redis ABSENT**.

---

## C. Findings

### C.1 `scripts/deploy.sh` — false green REPRODUCED BY EXECUTION

The defect is structural and now demonstrated. Source (committed blob, 82 lines):

```bash
set -e                                              # :6
IMAGE_TAG=${2:-latest}                              # :9
export $(grep -v '^#' .env.$ENVIRONMENT | xargs)    # :21
for i in {1..5}; do                                 # :62
    if curl -sf $HEALTH_URL > /dev/null; then       # :63
        echo "   ✓ Health check passed"; break      # :64-65
    fi
    echo "   ⏳ Health check attempt $i/5..."        # :67
    sleep 10
done                                                # :69   ← no success flag, no post-loop check
kubectl get pods …                                  # :72
echo "🎉 Deployment to $ENVIRONMENT complete!"      # :77   ← UNCONDITIONAL
```

`curl -sf` failing **inside an `if` condition** does not trip `set -e`, and the loop records no
outcome, so control reaches `:77` regardless.

**Executed result** — `python staging/probe_p37_deploy_false_green.py`, exit 0, output retained at
`staging/p37_deploy_false_green.txt`:

| Case | exit | success banner | "Health check passed" | attempts | image |
|---|---|---|---|---|---|
| all health checks fail | **0** | **True** | **False** | **5** | `latest` |
| `.env.staging` ABSENT | **0** | **True** | False | 5 | `latest` |
| no image argument | 0 | True | False | 5 | **`latest`** |

> **Five health-check attempts occurred, a pass was never observed, and the script nonetheless
> exited 0 and announced `Deployment to staging complete!`.** That is the false-green defect,
> executed rather than inferred.

**Also confirmed by execution:** a **missing `.env.staging` is silently tolerated** (exit 0, banner
printed), and `IMAGE_TAG` defaults to the bare mutable **`latest`** — not a repository-qualified
immutable digest.

**What I explicitly do NOT claim.** Phase 32 reported that exit code and output were *identical*
between all-pass and all-fail. **I could not verify the all-pass half.** My intended success control
also showed 5 attempts, meaning the stubbed `curl` was not exercised as designed; a follow-up probe
of bash's PATH resolution for Windows-style directories returned no output, leaving stub resolution
**ambiguous in this environment**. So Phase 32's "identical" claim is **partially corroborated** —
the failure half is proven, the success half is **UNVERIFIED here**. The defect does not depend on
that control: five observed failures followed by exit 0 and a success banner is sufficient.

**Scope note:** the probe copied the script to a temp directory and ran it against stubs. **No
cluster was contacted, no deployment performed, and `scripts/deploy.sh` was not modified** — its
sha256 is identical before and after.

### C.2 Two false negatives of my own, diagnosed and corrected mid-probe

Recorded because each would have produced a wrong conclusion.

| # | Symptom | Diagnosis | Correction |
|---|---|---|---|
| 1 | All four cases exit **2**, 0 attempts, no banner → "false green NOT reproduced" | `VER` The worktree copy carries **82 CRLF pairs** (`core.autocrlf`); bash reports `line 5: $'\r': command not found` and `set: - : invalid option`, aborting before the loop. **A local checkout artifact, not a repository defect** — the committed blob is LF, exactly as Phase 32 noted | Harness now sources the **committed bytes** via `git show HEAD:scripts/deploy.sh` |
| 2 | Exit **0** but empty output → banner `False`, attempts `0` | `UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f` — the script emits emoji, and `subprocess(text=True)` on Windows silently emptied the capture | Capture **bytes**, decode UTF-8 with `errors="replace"` |

Either defect alone would have let me report "defect not reproduced" against a real defect.

### C.3 `infra/deploy.sh` — source-verified, claims mostly CONFIRMED

| Phase 32 claim | Status | Evidence |
|---|---|---|
| Prints `DEPLOYMENT COMPLETE & VERIFIED` | **CONFIRMED** | `:133` |
| Hardcoded readiness score `100%` | **CONFIRMED** | `:139` `echo "[SUMMARY] Readiness Score:  100%"` |
| Warn-only wrong-account guard | **CONFIRMED** | `:62-63` compares `$ACCOUNT_ID` to `EXPECTED_ACCOUNT` then `echo "[WARNING] …"` — **no `exit`** |
| Warn-only checks with no pass/fail assertion | **CONFIRMED** | `:103` `[WARN] ALB State is:` · `:121` `[WARN] ECS Service Status: $RUNNING_COUNT/$DESIRED_COUNT` |
| Fail-closed permission preflight | **CONFIRMED — working, preserve it** | `:68-71`, four `aws … \|\| { echo "[FAIL] …"; exit 1; }` |

**Not executed**, per the brief's prohibition on running it against infrastructure — source review
only. Whether `:133`/`:139` are strictly unconditional was **not** fully established: my attempt to
read the surrounding block was interrupted, so I record it as **UNKNOWN** rather than assert it.

### C.4 Carried forward (hash-verified, reused with provenance)

| Item | Status |
|---|---|
| Bandit census: **56 directives / 17 files / 54 colon-form / 2 space-form / 0 bare** | **CONFIRMED** (Phase 36, independently measured there) |
| Bandit parity gap: CI `-r backend_app -ll` vs gate test's single module | **CONFIRMED, unresolved** |
| `transactional_execution_manager.py`: 13 B608-scoped suppressions | **CONFIRMED** |
| Release-gate trigger deadlock (`01-pr-check.yml` `pull_request`-only) | **CONFIRMED, owner decision** |
| Job-ID vs API display-name mapping defect | **UNRESOLVED** |
| `05-security.yml`: second IaC root unreachable, SAST upload lacks `if: always()`, Safety skipped-then-non-blocking | **CONFIRMED, unresolved** |
| `02-build.yml:78` tag-as-digest fallback; `03-deploy.yml:168-172` fail-closed guard | **CONFIRMED — preserve the guard** |
| ECR tag immutability | **UNKNOWN** — no `aws_ecr_repository` declaration in any root |
| Terraform roots: 3 (`terraform/`, `terraform/redis-only/`, `aerora_quant_platform/infrastructure/terraform/`); `infra/terraform/` **does not exist** | **CONFIRMED** |
| SG-collision hypothesis | **WITHDRAWN**, not revived |
| Tenant isolation 4-of-150 | **UNRESOLVED — PostgreSQL ABSENT**, neither reproduced nor refuted |
| D7-1 / D6-D-D7-2 / W-6 | **CONFIRMED, unresolved**; D6-D precedes D7-2 |
| Frontend published at `ecd23a9e` with `05 Security` red | **historical fact**; current edge state **UNKNOWN** |

---

## D. Implementation inventory

**No code change was made — zero applicable grants.** Nothing in this report is presented as an
implemented fix. The proposed `deploy.sh` repair, for whenever it is granted, is:

| Requirement | Minimal change |
|---|---|
| Exhausted health checks must exit nonzero | track success in the loop; after `done`, `if [ -z "$ok" ]; then echo "…failed"; exit 1; fi` |
| Success banner only after real success | move `:77` behind that check |
| Missing config fails explicitly | test `-r ".env.$ENVIRONMENT"` before `:21` and exit nonzero |
| Config parsing is safe | replace `export $(grep … | xargs)` with a line-wise loop handling blanks, comments, quotes and spaces |
| No mutable-tag fallback | require a repository-qualified `sha256:` digest; reject empty/`latest`-only |

Each needs a stub-based regression test plus the negative control already demonstrated here.

---

## E. Test ledger

| Command | Exit | Result | Notes |
|---|---|---|---|
| `python staging/probe_p37_deploy_false_green.py` (3rd run) | **0** | **false green REPRODUCED**; 4 cases, 5 attempts each, exit 0 + banner | authoritative run |
| same, 1st run | 0 | **INVALID** — exit 2, 0 attempts | CRLF artifact (C.2) |
| same, 2nd run | 0 | **INVALID** — empty capture | charmap artifact (C.2) |
| `bash ./d.sh` on a worktree copy | — | `$'\r': command not found`, `set: - : invalid option` | CRLF diagnosis |
| CRLF byte census of `scripts/deploy.sh` | 0 | **82 CRLF pairs**, 2571 bytes | — |
| `Select-String` over `infra/deploy.sh`, `scripts/deploy.sh` | 0 | C.1, C.3 line evidence | — |
| bash PATH-resolution probe for stubs | 0 | **no output — inconclusive** | stub resolution ambiguous |

**Reused, not re-run** (source, config, command and tool version hash-verified unchanged):
`tests/test_bandit_sast_gate_is_green.py` **3 passed / 103.44s** (Phase 36); phase18 **28 passed**,
phase17 **42 passed**, `tests/test_release_artifacts.py` **8 passed**, bounded 8-file regression
**202 passed / 5 skipped / 1 xfailed** (Phase 35). The 5 skips are real-Redis; the xfail is strict.
**Neither counts as a pass.**

**NOT RUN, with reasons:** full default suite (times out); Trivy, Terraform, Docker, kubectl,
PostgreSQL, Redis (**ABSENT**); `infra/deploy.sh` execution (**prohibited**); tenant matrix
(**PostgreSQL absent**); every implementation-acceptance test (**no grant — no change exists to
validate**).

**Negative controls — honest status:** *failed health check cannot produce success* →
**EXECUTED, and it FAILS today** (that is the defect). *Missing config rejected* → **EXECUTED, and
it is tolerated today.** *Mutable-only image rejected* → **EXECUTED, `latest` accepted today.**
All remaining controls in §13 of the brief → **NOT RUN** (no authorized change to control).

---

## F. Owner decisions still blocking

> **1 · Deployment-script failure semantics — NEWLY EVIDENCED, now the cheapest high-value grant.**
> *"I authorize repairing `scripts/deploy.sh` so an exhausted health-check sequence exits nonzero,
> the success banner is conditional, missing required config fails explicitly, config parsing is
> quote/space-safe, and a mutable-only image reference is rejected; plus stub-based regression tests.
> No deployment is performed."* **Justification is executed, not theoretical (C.1).**
>
> **2 · `infra/deploy.sh` verification honesty.** *"I authorize making `DEPLOYMENT COMPLETE &
> VERIFIED` and the readiness score conditional on real checks, and deciding whether the
> wrong-account guard at `:62-63` must fail closed."* The fail-closed permission preflight at
> `:68-71` must be preserved.
>
> **3 · Correctness-workflow architecture (still gates the verifier lanes).** Push-triggered
> correctness workflow / extend `01-pr-check.yml` to pushes / PR-only with explicit merge-commit
> evidence binding / separate release-authorization stage over immutable evidence.
>
> **4 · Security scan reachability + evidence retention** — `05-security.yml`: reachable second IaC
> root, `if: always()` on the SAST upload, Safety independent of Bandit, report-conditional upload.
>
> **5 · Safety policy** — mandatory or advisory, **and** independent of Bandit either way.
>
> **6 · Bandit parity** — extend the gate test to CI's recursive scope with a negative control. **No
> suppression change requested**: all 56 are rule-scoped and reviewed.
>
> **7 · Build digest** — `02-build.yml:78`; adopt the `03-deploy.yml:168-172` pattern.
>
> **8 · Operational facts the repository cannot answer** — which deploy scripts are supported entry
> points; whether `ecs-task-definition-full.json` is ever applied; whether **ECR tag immutability**
> is enabled.
>
> **9 · Remaining** — D7-1 (only the three `load_decrypted_keys` callers); D6-D before D7-2; W-6
> classification-only with no retry; tenant isolation on a disposable non-owner service, **FORCE RLS
> not first**.

---

## G. Final integrity and prohibited-operation checklist

**All confirmed NOT performed:** stage, commit, push, reset, stash, pop, drop, clean, revert, merge,
rebase, branch switch, history rewrite, worktree change. No CI dispatch, PR, branch-protection or
repository-settings change. No deploy, rollback, asset publication, CloudFront invalidation, DNS
change, ECR mutation, task-definition registration, `terraform apply`, provisioning, production
database mutation, real `kubectl` operation, exchange order, or live-trading enablement. No
credential, token, decrypted key, connection string or sensitive environment value printed.

No gate weakened; no assertion, severity threshold, anti-vacuity check, strict xfail, RLS
requirement, provenance check or fail-closed behaviour altered; no suppression added or removed; no
skipped or absent check reported as a pass.

**Preserved and verified at exit:** both concurrent modifications at their exact entry hashes, all
untracked artifacts, every prior report and prototype, 2 stashes, 1 worktree,
`scripts/deploy.sh` byte-identical.

| Lane | Status |
|---|---|
| `scripts/deploy.sh` false green | **DISPROVEN-AS-SAFE / DEFECT REPRODUCED** — blocked by missing grant |
| `infra/deploy.sh` verification honesty | **CONFIRMED (source)** — blocked by missing grant |
| Release-gate identity + trigger | **OWNER DECISION REQUIRED** |
| Security scan reachability / Safety | **BLOCKED — NO GRANT** / **OWNER DECISION** |
| Bandit parity | **BLOCKED — NO GRANT** (scope-reduced request) |
| Image provenance / frontend gate | **BLOCKED — NO GRANT**; **NOT ESTABLISHED** |
| D7-1 / D6-D-D7-2 / W-6 | **BLOCKED — NO GRANT** |
| Tenant isolation | **BLOCKED — TOOL/SERVICE ABSENT**; **NOT CERTIFIED** |
| Redis / workers / Terraform | **BLOCKED — TOOL/SERVICE ABSENT** |

**Release readiness did not improve.** One previously theoretical deployment defect is now
demonstrated, which strengthens the case for the smallest outstanding grant but changes no gate.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**
