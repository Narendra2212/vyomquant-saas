# Phase 25 — Authorized Critical Fixes, Security-Gate Parity, End-to-End Release Integrity

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Nothing overwritten.

> ## DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED
>
> **0 files changed. No implementation was authorized, so none is reported.**
>
> `VER` Grant recheck this phase — ledger `:800-804`: `AP-W1D-1` … `AP-W1D-5` **all `NOT GRANTED`**.
> No row covers a workflow, script, Terraform file, migration, router or test.
> Per the brief's exit rule: no fix is claimed on the basis of a proposal or a passing test.

Short by design. Priorities A, B, D, E and the parallel lanes are **unchanged since Phases 22–24**
at identical hashes and are **reused, not re-derived**. This phase executed the one item the brief
asked for that had never actually been run.

---

## 1. Entry / exit manifest

`VER` Identical at entry and exit.

| Measurement | Value |
|---|---|
| `HEAD` / `origin/main` / divergence | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / identical / **0 / 0** |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — unchanged |
| staged / unstaged / unmerged / tracked | **0 / 2 / 0 / 5705** |
| stash / worktrees / reflog | **2** / **1** / `ecd23a9e commit: launch phase 1` |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

**Tools:** Python 3.12.10, pytest 9.1.1, Bandit 1.9.4, `gh` present.
**`trivy`, `docker`, `terraform` — rechecked, all ABSENT.** PostgreSQL/Redis unavailable. No scan,
`fmt`, `validate` or integration run was performed or is claimed.

---

## 2. Priority C — the prototype's invisibility, now measured (NEW)

Phase 22 **inferred** that `staging/phase17_test_frontend_release_gate.py` is not collected. This
phase **measured it**, and found **two independent causes** — one of which I had not identified.

`VER` `pytest.ini`:

```
testpaths =
    tests
python_files = test_*.py *_test.py
norecursedirs = *.egg .* _darcs build CVS dist node_modules venv {arch} tests/perf
```

| Cause | Evidence |
|---|---|
| **1. `testpaths = tests`** | A bare `pytest` never walks `staging/` at all. **Newly identified** — Phase 22 cited only the filename |
| **2. `python_files = test_*.py *_test.py`** | The file is `phase17_test_frontend_release_gate.py` — it begins `phase17_`, so it matches **neither** pattern. Note `staging` is **not** in `norecursedirs`, so the filename alone is disqualifying |

`VER` Measured, with exit codes:

| Probe | Command | Result |
|---|---|---|
| **A** | `pytest --collect-only -q staging/` | **collected 0 items**, **exit 5** (“no tests collected”) — even when pointed directly at the directory |
| **B** | `pytest --collect-only -q staging/phase17_test_frontend_release_gate.py` | **42 tests collected**, **exit 0** — an explicit file path bypasses `python_files`, which is how the owning session ran it |
| **C** | `pytest staging/phase17_test_frontend_release_gate.py -q --no-header -p no:cacheprovider` | **42 passed in 1.19s**, **exit 0** (wall 2.4 s) — **executed by me this phase** |

> **The suite is invisible twice over** and runs only when a human names the file explicitly. A
> default CI run would never execute it, so **it is not regression protection**: the gate logic
> could regress silently and no suite would fail.
>
> **42 passing mocked cases prove verifier logic, not release safety.** They use mocked GitHub API
> responses; they establish no registry, CI-runtime, or production fact.

**Smallest fix, and it is purely mechanical:** relocating the implementation to `scripts/` and the
tests to `tests/test_frontend_release_gate.py` satisfies both `testpaths` and `python_files` with
no logic change. That requires a grant for those two paths.

---

## 3. Priorities A, B, D, E and parallel lanes — reused, unchanged

All governing hashes byte-identical to Phases 22–24 `VER`; nothing re-derived, nothing re-scanned.

| Priority / lane | Status | Smallest outstanding decision |
|---|---|---|
| **A** digest fail-open (`02-build.yml`) | **NOT GRANTED** | Remove `2>/dev/null` + `\|\| echo "$IMAGE_TAG"`; fail on empty/`None`/malformed. **Leave `03-deploy.yml`'s empty-digest refusal intact** |
| **B** Bandit parity | **NOT GRANTED** | Widen the gate test to CI's `-r backend_app -ll` scope; keep the AST test as a complement; keep the negative control. The 9 B608 dispositions need separate review |
| **C** verifier integration | **NOT GRANTED** | Relocate to `scripts/` + `tests/` (§2); then add paginated job-level checks |
| **D** frontend gate + correctness route | **OWNER DECISION REQUIRED** | *"`main` advances by PR only"* **or** grant `push: [main]` on `01-pr-check.yml`. **The route must be settled first**, else a gate requiring `01 PR Check` blocks every push candidate |
| **E** immutable artifact selection | **NOT GRANTED** | Reject unset/empty `IMAGE_TAG`; carry a verified digest; gate `:latest` promotion; 6 API/worker refs; QuestDB policy kept separate |
| Security-workflow evidence | **NOT GRANTED** | `if: always()` on `sast-scan`'s upload, the 2nd IaC scan, the MEDIUM report. **Not** `continue-on-error`/`\|\| echo` |
| D7-1 | **NOT GRANTED** | 3 lines only — `market.py:53`, `orders.py:792`, `:1098`. **14 legitimate `access_token` sites must survive** (Phase 24 §3); `exchange.py:993` correct |
| D6-D → D7-2 | **NOT GRANTED** | D6-D first: establish the vault singleton/type. No `KeyVault`→`APIKeyVault` substitution; no second vault |
| W-6 | **NOT GRANTED** | Classification only. **No retry, backoff or submission change** |
| Terraform / Trivy | **BLOCKED** (tools absent) + NOT GRANTED | Collision premise **withdrawn** (Phase 24 §2 — different VPCs). Real hazard: `redis-only` manages production ElastiCache with **local state** |
| Tenant / PostgreSQL / Redis / workers | **BLOCKED** (services absent) | Disposable non-owner/non-superuser PostgreSQL + isolated Redis. **FORCE RLS stays off.** W-4/W-5 not inferred |

**Frontend incident boundary preserved.** Publication from `ecd23a9e` while security failed and
`01 PR Check` never ran is **established**. Exploitation, compromise, and the current published
bundle contents are **UNKNOWN** — no manifest or dependency report has been supplied. A future gate
would **not** contain or reverse that publication. No freeze, rollback, republish, S3 mutation or
CloudFront invalidation was performed or implied.

---

## 4. Commands run this phase

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status --short` / `write-tree` / `ls-files` / `stash list` / `worktree list` / `reflog -1` / `hash-object` | 0 | §1 |
| ledger `:800-804` read | 0 | **5 × `NOT GRANTED`** |
| `Get-Command trivy\|docker\|terraform` | 0 | all **ABSENT** |
| `pytest --collect-only -q staging/` | **5** | **0 collected** |
| `pytest --collect-only -q staging/phase17_test_frontend_release_gate.py` | **0** | **42 collected** |
| `pytest staging/phase17_test_frontend_release_gate.py -q --no-header -p no:cacheprovider` | **0** | **42 passed** in 1.19 s |

**NOT RUN and not claimed:** Bandit (inputs unchanged since the executed Phase 24 measurement),
Trivy, Terraform `fmt`/`validate`, CI dispatch, PR creation, AWS/ECR/ECS query, deployment,
database, Redis, network.

---

## 5. Evidence classes

| Class | Status |
|---|---|
| **Verified source configuration** | §2 this phase; §3 reused at identical hashes |
| **Unit / mock evidence** | **42 passed** (§2 probe C) — **mocked API; proves verifier logic only** |
| Integration evidence | **BLOCKED** — PostgreSQL, Redis, docker absent |
| CI runtime evidence | **NOT RUN** — no dispatch authorized |
| Registry (ECR) / Terraform state / live ECS | **NOT RUN** — no access, none authorized |
| Published frontend bundle contents | **UNKNOWN** — evidence not supplied |
| **Release certification** | **NONE** |

A negative control passing means the control detects an injected defect; it certifies nothing about
the release.

---

## 6. Copy-ready grant statements for the blocked work

> **G-1 (unblocks C, D):** *"`main` advances by pull request only, with `01 PR Check` as a required
> check"* — **or** *"I authorize adding `push: branches: [main]` and `workflow_dispatch` to
> `.github/workflows/01-pr-check.yml`, with a `concurrency` group."*
>
> **G-2 (Priority A):** *"I authorize editing `.github/workflows/02-build.yml` to remove the digest
> fallback and fail on an unresolved digest. `03-deploy.yml` must not be modified."*
>
> **G-3 (Priority C):** *"I authorize moving the Phase 17 gate implementation to `scripts/` and its
> tests to `tests/test_frontend_release_gate.py`, then adding paginated mandatory-job checks."*
>
> **G-4 (security evidence):** *"I authorize adding `if: always()` to the three evidence steps in
> `.github/workflows/05-security.yml`, and state whether Safety is blocking or advisory."*
>
> **G-5 (D7-1):** *"I authorize removing the `access_token=` argument at `market.py:53`,
> `orders.py:792` and `orders.py:1098` only, plus a signature-binding test."*

---

## 7. Final integrity check

`VER` **Files changed: 0** — source, test, workflow, Terraform, script, migration, governance all
zero. `git write-tree` identical at entry and exit; the tracked tree did not move (5705 entries).
Both preserved concurrent files byte-identical. Both stashes untouched. Report created without
overwriting any artifact.

No git write command (no stage, commit, push, merge, rebase, reset, checkout, clean, revert,
history rewrite, stash apply/pop/drop). No CI dispatch, PR, branch-protection or repository-setting
change. No deploy, rollback, cache purge, DNS change, `terraform apply`, infrastructure
provisioning, image build/push/pull/retag/promote/delete, production database/role/grant/Redis/
tenant-policy mutation, FORCE RLS, live trading or exchange order. No secret, token, DSN or
decrypted key material read or printed. No security threshold weakened, no finding suppressed, no
evidence fabricated. No prior report overwritten.

**An unchanged tree is not verification. A skipped scan is not a pass. Absent tools are not passed
scans. Mocked tests are not release evidence. Source inspection is not production evidence.**
