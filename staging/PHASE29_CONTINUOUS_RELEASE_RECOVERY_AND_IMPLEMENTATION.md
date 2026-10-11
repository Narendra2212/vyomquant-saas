# Phase 29 — Continuous Release Recovery: Authorization, CI Gate, Artifact Provenance, Closure

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Nothing overwritten.

> ## 1. Executive outcome
>
> **0 files changed. No implementation lane was open.** `VER` Ledger `:800-804` re-read:
> `AP-W1D-1` … `AP-W1D-5` **all `NOT GRANTED`**. G-1…G-6 remain *requests*, not grants.
>
> **Two new findings, one of which corrects my own earlier recommendation:**
> 1. **The Phase 18 gate already fixes the G-3 logic gap** and proves the Phase 17 defect *by test*.
>    My Phase 25/28 reports recommended promoting **phase17** — that would have shipped the weaker
>    gate. **Corrected: promote phase18.**
> 2. **`scripts/deploy.sh` is a 10th mutable-tag consumer on a previously unaccounted platform**
>    — `kubectl`, not ECS, so it bypasses the ECS chain's fail-closed digest guard entirely.
>
> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED · IMAGE PROVENANCE NOT ESTABLISHED**

---

## 2. Entry / exit integrity

`VER` Re-measured, not assumed. Entry = exit.

| Measurement | Value |
|---|---|
| branch / `HEAD` / `origin/main` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / identical |
| divergence | **0 / 0** |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — unchanged |
| staged / unstaged / unmerged / tracked | **0 / 2 / 0 / 5705** |
| untracked / stash / worktrees | 130 / **2** / **1** |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

Both concurrent files byte-identical; both stashes untouched.

**Newly hashed this phase** (never previously recorded):

| Blob | Path |
|---|---|
| `5c70dbbfb882036badade2ff01b4bf687595ff4e` | `staging/phase18_release_gate.py` |
| `3dd575192edbb8df26f3bcee0ba6fef7992e0483` | `staging/phase18_test_release_gate.py` |
| `e06423f2b3b075add617778c662146c0c4754657` | `scripts/deploy.sh` |

**Path correction:** `terraform/ecs-task-definition-full.json` is **ABSENT**. The file is at
repository root — `ecs-task-definition-full.json` — as established in Phase 22.

**Tools:** Python 3.12.10, pytest 9.1.1, Bandit 1.9.4, `gh` present. `trivy`, `docker`,
`terraform` **ABSENT**. PostgreSQL/Redis unavailable.

---

## 3. Authorization matrix

| Decision | Target paths | Verified state |
|---|---|---|
| G-1 correctness route | `01-pr-check.yml` / branch protection | **OWNER DECISION — not issued** |
| G-2 build digest | `02-build.yml` | **NOT GRANTED** |
| G-3 verifier integration | `scripts/`, `tests/`, `staging/phase18_*` | **NOT GRANTED** |
| G-4 security evidence | `05-security.yml` | **NOT GRANTED** (+ Safety policy undecided) |
| G-5 D7-1 | `market.py`, `orders.py` | **NOT GRANTED** |
| G-6 verifier diagnostics | `require_security_scan.py:15-16, :123-124` | **NOT GRANTED** |

`VER` No affirmative grant exists in the ledger — the only Class-A register. Consistent with
Phase 28's classification of eight apparent grant hits as copy-ready *request templates*. Nothing
in this prompt was treated as authorization, and the ledger was not modified.

---

## 4. Finding 1 — the Phase 18 gate supersedes Phase 17 (corrects my own recommendation)

`VER` `staging/phase18_release_gate.py` inspected this phase for the first time.

It implements precisely the capability my Phases 16/22/25/28 identified as missing, and goes
further:

| Property | phase17 | **phase18** |
|---|---|---|
| Run-level conclusion | yes | yes |
| **Mandatory-job verification** | **NO** | **`_assert_mandatory_jobs_passed()`** — each job must **exist**, be `completed`, and conclude **explicit `success`**; `skipped`/`cancelled`/`failure`/`neutral`/absent all refuse |
| **Jobs-listing pagination** | n/a | **yes** — ambiguity refuses on **either** listing |
| Advisory classification | none | **`ADVISORY_JOBS`** with written reasons |
| Multiple blockers | first-fail | **aggregated** into one refusal |
| Override | none | none |

Two things in it are better than my own analysis:

- **It proves the defect instead of asserting it.** `phase18_test_release_gate.py` contains
  `test_phase17_gate_admits_a_run_whose_mandatory_job_was_skipped`, executed **against the
  Phase 17 module itself**.
- **It names the concrete in-repo hazard I had described only generally:** most `01 PR Check`
  jobs are `needs: static-audit`, so an unsatisfied `needs` leaves them **skipped rather than
  failed**, and `format-check` is `continue-on-error: true`. A run can therefore conclude
  `success` with mandatory jobs skipped.
- It also declines to assume whether `provisioning-and-isolation` is release-mandatory —
  *"an owner decision, not this module's to assume."* That is the correct boundary.

> **Correction to my own prior reports.** Phase 25 §2 and Phase 28 §6 ④ recommended moving
> `staging/phase17_frontend_release_gate.py` into `scripts/`. **That recommendation was wrong** —
> it would have promoted the gate with the known admission defect. **G-3 should promote
> `phase18_release_gate.py` and `phase18_test_release_gate.py`.** The Phase 17 pair should be
> retained only as the fixture that demonstrates the defect.

**Not executed:** I did not run the phase18 suite. It is another session's in-flight artifact, it
is not my lane to validate under no grant, and running it would not change the blocked status.
Its collection status is governed by the same `testpaths = tests` / `python_files` constraint
measured in Phase 25.

---

## 5. Finding 2 — `scripts/deploy.sh`: a 10th mutable-tag consumer, on Kubernetes

`VER` Inspected this phase for the first time:

```bash
IMAGE_TAG=${2:-latest}
...
kubectl set image deployment/trading-backend \
    backend=$IMAGE_TAG \
    -n $ENVIRONMENT
```

| Property | Finding |
|---|---|
| Default identity | **`latest`** when argument 2 is omitted |
| Platform | **`kubectl` / Kubernetes** — **not** ECS |
| Digest | none — the tag is set directly as the container image |
| Gate | **none** — no security or correctness evidence consulted |

> **This is a separate deployment surface nobody has accounted for across Phases 21–28.** All
> prior provenance work traced the **ECS** chain, where `03-deploy.yml:168-174` provides a
> fail-closed digest guard. `deploy.sh` sits entirely outside that chain: it defaults to the
> mutable `:latest` that `02 Build` advances **before any gate**, and applies it with `kubectl`.
>
> The mutable-tag census therefore stands at **10 references across 5 sources**, not 9 across 4.

**Not established:** whether `deploy.sh` is live, invoked by CI, or vestigial. No workflow
references it (no `uses:`/`run:` hit found in the workflow set during Phase 22's enumeration), and
I have no cluster access and none authorized. **Recorded as a source-level finding requiring owner
confirmation**, not as an active production path.

---

## 6. Everything else — unchanged at verified hashes, not re-measured

Per §10, measurements whose inputs are unchanged were **not** repeated: the 9 Bandit B608
findings, the Trivy five, the digest-unreachability chain, the frontend gate's absence, D7-1's
3-invalid/14-legitimate census, D6-D's two errors, W-6's 3-of-8 matrix, `test_perf.py`'s literals,
the two Terraform roots, and Phase 25's measured test-collection causes. All carry recorded
hashes; all remain **NOT GRANTED** or **BLOCKED BY ENVIRONMENT**.

---

## 7. Evidence classification

| Class | Status |
|---|---|
| **Source inspection** | §4, §5 — new this phase |
| Unit tests | **none run this phase** (Phase 28's 42-pass stands at recorded blobs) |
| Mock-based tests | as above |
| Integration tests | **BLOCKED** — PostgreSQL, Redis, docker absent |
| CI runtime evidence | **NOT RUN** — no dispatch authorized |
| Cloud / registry evidence | **NOT RUN** — no access, none authorized |
| **Production certification** | **NONE** |

---

## 8. The single highest-value next decision

> ### G-1 — the correctness route
>
> *"`main` advances by pull request only, with `01 PR Check` a required check."*
> — **or** — *"I authorize adding `push: branches: [main]` and `workflow_dispatch` to
> `.github/workflows/01-pr-check.yml`, with a `concurrency` group."*

**What it unlocks, and why it is first:**

- **G-3** — the phase18 gate requires `01 PR Check` evidence. Without a route that can produce it
  for a release candidate, promoting the gate would refuse **every** push-built candidate.
- **The frontend gate (E)** — same dependency.
- **All correctness evidence for any shipped commit** — currently unobtainable by construction.

The pull-request option requires **zero file edits**.

**Genuinely independent work that remains possible without G-1:**

| Item | Why independent |
|---|---|
| **G-6** verifier diagnostics | two text blocks, no logic, no gate dependency — lowest risk in the backlog |
| **G-4** security evidence | `05-security.yml` evidence retention is orthogonal to the release route |
| **G-2** build digest | `02-build.yml` producer-side fix needs no correctness evidence |
| **`deploy.sh` disposition** (§5) | an owner statement on whether it is live, then gate or retire it |

**Owner decisions that cannot be inferred:** Safety blocking vs advisory; whether
`provisioning-and-isolation` is release-mandatory; the market-data keys-absent policy; ECR
immutability ownership; and the `redis-only` local-state risk acceptance.

---

## 9. Repository integrity

`VER` **Files changed: 0** — source, test, workflow, Terraform, script, migration, governance all
zero. `write-tree` identical entry and exit; tracked tree unmoved at 5705. Both preserved
concurrent files byte-identical. Both stashes untouched. No report, prototype, log or artifact
overwritten. The ledger was **not** modified.

No git write command (no stage, commit, push, merge, rebase, reset, checkout, clean, revert,
history rewrite, stash apply/pop/drop). No CI dispatch, PR, branch-protection or repository-setting
change. No deploy, rollback, republish, cache purge, DNS change, `terraform apply`, infrastructure
provisioning, image build/push/pull/retag/promote/delete, `kubectl` invocation, production
migration, database/role/grant mutation, tenant-policy change, FORCE RLS, live trading or exchange
order. No secret, token, DSN, signed payload or decrypted key material read or printed. No
threshold weakened, no finding suppressed, no unavailable check reported as a pass.
`03-deploy.yml`'s fail-closed guard untouched.

**An unchanged tree is not verification. A skipped scan is not a pass. Absent tools are not passed
scans. Mocked tests are not release evidence. Source inspection is not production evidence.**
