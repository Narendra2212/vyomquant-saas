# Phase 23 — Release-Chain Integrity, Correctness Route, Exact-Digest Deployment, Security-Gate Closure

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Nothing overwritten.

> ## DISPOSITION: **DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · DEPLOYMENT BLOCKED**
> **0 files modified.** No implementation grant exists for any lane. **No implementation was authorized.**

Differential report. Phases 16–22 are reused, not restated. Phase 23's new contribution is
**Lane K, answered in full from source** — the question of why the later security scans were
skipped, which no prior phase investigated.

| # | New this phase | Severity |
|---|---|---|
| 1 | **`sast-scan` discards its evidence artifact on failure** — its upload step lacks `if: always()`, unlike `iac-scan`'s, so the Bandit report is lost exactly when it is needed | **HIGH — new** |
| 2 | **The second IaC scan has never run.** `aerora_quant_platform/infrastructure/` has **zero** scan coverage, because the preceding blocking step fails first | **HIGH — new** |
| 3 | **The Safety dependency check can never fail the gate** — `\|\| echo "Safety advisory logged"` makes it non-blocking by construction, and it is additionally skipped after Bandit fails | **MEDIUM — new** |
| 4 | **The MEDIUM backlog report is never produced when it matters** — the non-blocking report step is skipped after the blocking step fails | **MEDIUM — new** |
| 5 | `terraform` is **absent** locally, so Terraform `fmt`/`validate` are blocked too — not only Trivy | **environment** |

---

## 1. Entry and exit manifest

`VER` Byte-identical at entry and exit.

| Measurement | Value |
|---|---|
| branch / `HEAD` | `main` / **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** |
| `origin/main` / divergence | identical / **0 / 0** |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — unchanged |
| staged / unstaged / unmerged | **0 / 2 / 0** |
| stash / worktrees | **2** / **1** — neither stash applied, dropped, popped or rewritten |

**Concurrent modifications preserved byte-for-byte** (hashes re-measured this phase):

| Path | Blob |
|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

### 1.1 Execution environment

| Tool | State |
|---|---|
| Python | **3.12.10** |
| Bandit | **1.9.4** (matches the CI wheel) |
| `gh` | present |
| **`trivy`** | **ABSENT** |
| **`docker`** | **ABSENT** |
| **`terraform`** | **ABSENT** — newly checked; blocks `fmt`/`validate`, not just scanning |
| PostgreSQL 5432 / Redis 6379 | **closed** (reused, unchanged) |

### 1.2 Authorization matrix

| Lane | Targets | Decision ID | Status |
|---|---|---|---|
| A correctness route | `01-pr-check.yml` / branch protection | P16-B1/B2 | **OWNER DECISION REQUIRED** |
| B image provenance | `02-build.yml`, deploy script, `ecs.tf`, task-def JSON | P21-D1 / P22-A1 | **NOT GRANTED** |
| C required-job verifier | `scripts/require_security_scan.py` | P22-B1 | **NOT GRANTED** |
| D frontend gate | `06-frontend-deploy.yml` + tests | P16-A1…A4 | **NOT GRANTED** |
| E Bandit | 3 migrations + gate test | — | **NOT GRANTED** |
| F D7-1 | `market.py`, `orders.py` + tests | P17-C1 | **NOT GRANTED** |
| G D7-2 | `portfolio_cache_updater.py` + test | P17-G1 | **NOT GRANTED** |
| H W-6 | `exchange_executor.py` + tests | — | **NOT GRANTED** |
| I Terraform / Trivy | Terraform roots | — | **NOT GRANTED** + tools **ABSENT** |
| J tenant / Redis | — | — | **NOT GRANTED** + services **ABSENT** |
| **K security workflow** | `05-security.yml` | **P23-K1 (new)** | **NOT GRANTED** |
| L release contract | design only | — | **read-only, completed** |

`VER` W1D ledger `:800-804` all `NOT GRANTED`; no row covers a workflow, script, Terraform file,
JSON task definition, migration, router or test. Per the brief I am **not re-requesting grants the
ledger already settles** — §7 lists each lane's smallest outstanding decision once.

---

## 2. Lane K — why the later security scans were skipped (NEW, answered in full)

`VER` `05-security.yml` read this phase. It defines **three independent jobs with no `needs:`
between them**: `sast-scan`, `container-security`, `iac-scan`. So the jobs do not block each
other — the skipping happens **inside** jobs, at step level.

### 2.1 `sast-scan` — evidence is destroyed by the failure it should document

| Step | `if:` / guard | Outcome when Bandit fails |
|---|---|---|
| `Run Bandit Scan` (`:36`) | none | **FAILS** (bandit exits 1 on any MEDIUM+) |
| `Run Safety Check` (`:39`) | none | **SKIPPED** |
| `Upload Security Reports` (`:42`) | **no `if: always()`** | **SKIPPED** |

GitHub Actions aborts a job's remaining steps once a step fails (default `if: success()`).

> **So the moment Bandit finds anything, the Safety check never runs and `reports/` is never
> uploaded.** The Bandit JSON — the artifact needed to triage the very failure that occurred — is
> discarded. This is why earlier phases had to reproduce Bandit locally instead of downloading the
> CI report.

**The asymmetry is the proof it is an oversight, not a policy:** `iac-scan`'s upload step **does**
carry `if: always()` (`:158`). One job preserves evidence on failure; the other throws it away.

### 2.2 `Run Safety Check` can never gate anything

`:40` — `safety check --json --output reports/safety-report.json || echo "Safety advisory logged"`.

The `|| echo` makes the step **always succeed**. So dependency findings are **non-blocking by
construction**, independent of the skip. Two separate reasons Safety produces no gate signal:
it cannot fail, and after a Bandit failure it does not even run.

### 2.3 `iac-scan` — the second infrastructure scan has never run

| Step | Guard | Outcome |
|---|---|---|
| `Trivy IaC scan — terraform/` (blocking, CRITICAL) (`:120`) | `exit-code: '1'` | **FAILS** |
| `Trivy IaC scan — aerora_quant_platform/infrastructure/` (blocking, CRITICAL) (`:131`) | none | **SKIPPED — never evaluated** |
| `Trivy IaC report — MEDIUM, non-blocking` (`:144`) | `exit-code: '0'` | **SKIPPED** |
| `Upload IaC Scan Report` (`:156`) | **`if: always()`** | **RUNS** — but uploads a JSON that was never generated |

Two consequences:

1. **`aerora_quant_platform/infrastructure/` has zero scan coverage.** Its blocking CRITICAL scan
   has never executed, so whether that root contains CRITICAL findings is **unknown** — not clean.
2. **The MEDIUM backlog report is never produced when it is needed.** Its own comment says it
   exists *"so the backlog is visible and can be ratcheted down rather than discovered later"*, but
   it is skipped precisely when the blocking scan fails.

### 2.4 Does the workflow report these as missing evidence? **No.**

Skipped steps appear only as "skipped" in the run UI. Nothing emits "evidence not collected", and
the `iac-scan` upload succeeds with an absent report, which **looks like** evidence exists. The
release gate is unaffected in the safe direction — `require_security_scan.py` keys on the
**workflow-run conclusion**, which is `failure`, so the backend deploy correctly refuses. The
defect is **evidence loss and silent non-coverage**, not a gate bypass.

> **P23-K1 — owner request (NOT applied).** Add `if: always()` to `sast-scan`'s upload so the
> Bandit report survives a failure. Add `if: always()` to the second IaC blocking scan and to the
> MEDIUM report step so all three scopes are measured every run and the job still fails if any
> blocking scan fails. Decide whether Safety should gate; if yes, drop the `|| echo`. **Do not**
> use `continue-on-error` — that would convert failures into successes. Owner: SRE / security
> workflow owner.

---

## 3. Lanes A–D — reused, unchanged, decision-ready

All governing hashes byte-identical to Phase 22 `VER`, so these are **REUSED FROM IDENTICAL HASH**.

**A — correctness route: OWNER DECISION REQUIRED.** `01 PR Check` remains `pull_request`-only.
Recommendation unchanged and it is the smaller architecture: **PR-only advancement of `main`**,
which needs **zero workflow edits** and uses the gate as designed. The alternative —
`push: [main]` plus `workflow_dispatch` — costs a duplicate pipeline per PR-merge and needs a
`concurrency` group. Either way the blocking fact is unchanged: a push-built candidate currently
**cannot** obtain correctness evidence, so any gate requiring `01 PR Check` would refuse every
such candidate (correctly fail-closed, but releasing nothing).

**B — image provenance.** The Phase 22 chain stands: digest outputs unreachable (`workflow_run`
carries no job outputs; zero `uses: ./.github/workflows` references), `:latest` advanced pre-gate,
9 mutable-tag references across 4 sources, deploy resolves by **tag** not digest, ECR immutability
undeclared. **Carried forward explicitly: `03-deploy.yml:168-174` is fail-closed and correct and
must not be touched** — only `02-build.yml:78`'s tag-as-digest fallback is defective.

**C — required-job evidence.** `require_security_scan.py` unchanged; run-level only. The reachable
hole is unchanged: `01 PR Check`'s `frontend-tests` is conditional, so a backend-only commit yields
run-level `success` with mandatory frontend tests skipped.

**D — frontend gate.** `06-frontend-deploy.yml` unchanged and still ungated. Publication correctly
left blocked rather than half-gated, since A is unresolved. **No claim is made that gating would
contain already-published assets** — containment, rollback and cache invalidation are separate
operational decisions needing separate authorization, and none was taken.

---

## 4. Lanes E–J — status

| Lane | State | Note |
|---|---|---|
| **E** Bandit | **NOT GRANTED** | 9 × B608 unchanged; inputs byte-identical so **not re-scanned**. Gate-test scope mismatch is the item that matters |
| **F** D7-1 | **NOT GRANTED** | 3 invalid `access_token=` — `market.py:53`, `orders.py:792`, `:1098`. **`exchange.py:993` correct, untouched.** A permissive double is explicitly not the fix |
| **G** D7-2 | **NOT GRANTED** | `key_manager` absent **and** class is `APIKeyVault` — two errors; a path-only fix still fails |
| **H** W-6 | **NOT GRANTED** | `retryable` = 2 constructor writes + 1 log read. **No retry invented or implied** |
| **I** Terraform / Trivy | **BLOCKED** | **`trivy`, `docker` and `terraform` all ABSENT** — no scan, no `fmt`, no `validate`. Prior 5-finding list is **reused, not rescanned**, and is not presented as a new scan |
| **J** tenant / Redis | **BLOCKED** | PostgreSQL and Redis closed. **FORCE RLS not enabled.** Stashes untouched; not used as an implementation shortcut. W-4 not invented; W-5 blocked |

---

## 5. Lane L — the release contract, and the one real deadlock

Combining every lane, the required order is:

```
  A  correctness route decided ─────────────┐
        (PR-only, or push-triggered)        │
                                            ▼
  C  verifier gains required-JOB checks ──> D  frontend gate (needs A's evidence to exist)
                                            │
  B  digest contract: build → promote ──────┼──> backend deploy (already SHA-gated, correct)
     (only after gates pass for that SHA)   │
                                            ▼
  K  security workflow collects all evidence every run
  E  Bandit gate green honestly ────────────┴──> 05 Security can go green
  I  Trivy findings dispositioned ──────────┘
```

**The one genuine deadlock, stated precisely:** D requires correctness evidence from `01 PR Check`;
`01 PR Check` cannot run on a push; `02 Build` and `06 Frontend Deploy` both fire on push. So
**today a push-built candidate can publish (frontend, ungated) but can never be validated.** The
deadlock breaks only at **A**, which is why A is the first priority and not merely the first
listed. No circularity exists once A is decided; the chain is acyclic.

Secondary constraint: `workflow_run` supports at most **three** chained levels. Current depth is
`02 Build → 03 Deploy` (2). Adding the frontend gate as a **parallel** consumer stays inside the
limit; adding it as a further link would not.

---

## 6. Evidence classes, kept strictly separate

| Class | Status |
|---|---|
| **Verified source configuration** | §2 (Lane K) this phase; §3 reused from identical hashes |
| Unit / mock evidence | **none executed this phase** |
| Integration evidence | **BLOCKED** — PostgreSQL, Redis, docker absent |
| **Registry (ECR) state** | **NOT RUN** — no AWS access, none authorized |
| **Terraform state** | **NOT RUN** — no state access; `terraform` binary absent |
| **Live cloud (ECS) state** | **NOT RUN** — not authorized |
| **Release certification** | **NONE** |

No skipped test, unavailable scanner or absent tool is reported as a pass. Source inspection is
**not** registry, Terraform-state, live-ECS or release evidence, and is not presented as such.

---

## 7. Commands run

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status --short` / `write-tree` / `stash list` / `worktree list` / `hash-object` | 0 | §1 |
| `python -V`; `python -m bandit --version`; `Get-Command trivy\|docker\|terraform\|gh` | 0 | §1.1 |
| grep step/guard structure of `.github/workflows/05-security.yml` | 0 | **§2 — the new finding** |

**NOT RUN and not claimed:** pytest, the staging prototype suite, Bandit (inputs unchanged), Trivy,
Terraform `fmt`/`validate`, CI dispatch, PR creation, AWS/ECR/ECS query, deployment, database,
Redis, network.

Artifact: `staging/PHASE23_RELEASE_CHAIN_INTEGRITY_AND_GATE_CLOSURE.md` (this report).

---

## 8. Blockers, smallest decision each, and the continuation plan

| # | Blocker | Owner | Smallest decision |
|---|---|---|---|
| **1** | **Correctness route — breaks the only deadlock (§5)** | coordinator + release | *"`main` advances by PR only"* **or** grant `push: [main]` + `workflow_dispatch` on `01-pr-check.yml` |
| **2** | Image provenance fail-open (`:latest` pre-gate, digest discarded, tag-based deploy) | release/workflow + infrastructure | **P22-A1** — and leave `03-deploy.yml` alone |
| **3** | Gate admits skipped mandatory jobs | workflow owner | **P22-B1** — paginated job-level verification |
| **4** | Frontend publishes ungated | workflow / release | Grant the gate + `needs:` from every publishing path (after #1) |
| **5** | **Security workflow loses evidence and never scans one root (§2)** | SRE / security | **P23-K1** — `if: always()` on three steps; decide Safety's gating role |
| **6** | `05 Security` red — Bandit + Trivy | code + infrastructure | Widen gate test, then 9 annotations; 2 Redis removals + 3 documented accepts |
| 7–10 | D7-1 · D7-2 · W-6 · TENANT-01 / A23 / W1C-02 / ledger `:802` | respective owners | Unchanged from Phase 20 §8 |

### Continuation plan — what is left, without repeating anything

1. **#1 is the only item that unblocks others.** Everything in A/C/D waits on it.
2. **#5 is independent, cheap, and newly specified** — three `if: always()` additions.
3. **#2 is independent** of all gate work.
4. **Do not re-investigate:** the B608 nine, the Trivy five, D7-1's census, D7-2's two errors,
   W-6's matrix, `test_perf.py`, the `retryable` census, the workflow-policy test convention, and
   the digest-unreachability chain are all settled with hashes recorded. Re-deriving them adds
   nothing.
5. **Blocked on environment, not analysis:** Lanes I and J. They need `trivy`/`terraform` and a
   disposable PostgreSQL + Redis respectively.

**Candid note on marginal value.** Seven consecutive phases have now ended with 0 files changed
because every lane's implementation is gated on grants the ledger records as `NOT GRANTED`. The
analysis is complete and decision-ready with exact paths, line numbers and test matrices. Further
read-only phases will produce diminishing new evidence; the next useful step is a grant, not
another investigation.

---

## 9. Final diff review

`VER` **Files changed: 0** — source, test, workflow, Terraform, JSON, script, migration,
governance all zero. `git write-tree` identical at entry and exit, so the tracked tree did not
move. Both preserved concurrent files byte-identical. Both stashes untouched. Report created
without overwriting any existing artifact.

No git write command (no stage, commit, push, merge, rebase, reset, checkout, clean, revert,
history rewrite, stash apply/pop/drop). No CI dispatch, PR, branch-protection or repository-setting
change. No deploy, rollback, cache purge, DNS change, `terraform apply`, infrastructure
provisioning, image build/push/pull/retag/promote/delete, production database/role/grant/Redis/
tenant-policy mutation, FORCE RLS, live trading or exchange order. No secret, token, DSN or
decrypted key material read or printed. No security threshold weakened, no finding suppressed, no
evidence fabricated.

### DO NOT RELEASE · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · DEPLOYMENT BLOCKED

An unchanged tree is **not** successful verification; absent tools are **not** passed scans; and
source analysis is **not** live deployment evidence. All three distinctions are preserved above.
