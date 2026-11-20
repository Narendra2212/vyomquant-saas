# Phase 28 — Release Recovery Execution, Security Diagnostics, Evidence-Preserving CI, Closure

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Nothing overwritten.

> ## 1. Executive outcome
>
> **0 files changed. No grant exists. Per §14 this report is one ranked authorization request, not another backlog.**
>
> `VER` Grant recheck: ledger `:800-804` — `AP-W1D-1` … `AP-W1D-5` **all `NOT GRANTED`**.
> `VER` **Eight** apparent grant-language hits were classified: **all eight are copy-ready
> *request* templates** authored by the requesting sessions. **Affirmative grants: 0.**
>
> **DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT CERTIFIED**

---

## 2. Entry / exit integrity manifest

`VER` Entry = exit on every measurement.

| Measurement | Value |
|---|---|
| branch / `HEAD` / `origin/main` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / identical |
| divergence / reflog | **0 / 0** / `ecd23a9e commit: launch phase 1` |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — unchanged |
| staged / unstaged / unmerged / tracked | **0 / 2 / 0 / 5705** |
| untracked | 126 (concurrent sessions' artifacts) |
| stash / worktrees | **2** / **1** — untouched |
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

**Tools:** Python 3.12.10, pytest 9.1.1, Bandit 1.9.4, `gh` present.
**`trivy`, `docker`, `terraform` ABSENT.** PostgreSQL/Redis unavailable.

### 2.1 Source hashes inspected

| Blob | Path |
|---|---|
| `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` | `scripts/require_security_scan.py` |
| `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | `.github/workflows/05-security.yml` |
| `91d55a379115c88edd2fd6be01871b82a75ef498` | `.github/workflows/01-pr-check.yml` |
| `42c7696eb7cb993b8942b7f0053bf7490e3e5edd` | `.github/workflows/02-build.yml` |
| `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` | `.github/workflows/06-frontend-deploy.yml` |
| **`4c38728fb360801ef9ee46a34e0b8db407095d04`** | `staging/phase17_frontend_release_gate.py` — **first recorded** |
| **`13b5e6cd1f12aa313bbf706b8eec09fec33a1928`** | `staging/phase17_test_frontend_release_gate.py` — **first recorded** |
| `a6f0a3d4bed34dc7b8eefccc087681f81fcb81e5` | `pytest.ini` |

---

## 3. Authorization matrix

| Lane | Exact paths | Verified grant | Permitted ops | Remaining dependency |
|---|---|---|---|---|
| A verifier docs | `scripts/require_security_scan.py:15-16, :123-124` | **NOT GRANTED** | read-only | **G-6** |
| B security evidence | `.github/workflows/05-security.yml` | **NOT GRANTED** | read-only | **G-4** + Safety policy |
| C verifier integration | `scripts/`, `tests/`, `staging/phase17_*` | **NOT GRANTED** | read-only, run tests | **G-3** |
| D correctness route | `01-pr-check.yml` / branch protection | **OWNER DECISION** | read-only | **G-1** |
| E frontend gate | `06-frontend-deploy.yml` | **NOT GRANTED** | read-only | **G-1 first** |
| F image provenance | `02-build.yml`, deploy script, `ecs.tf`, task-def JSON | **NOT GRANTED** | read-only | **G-2** |
| G Bandit parity | gate test, 3 migrations, `test_perf.py` | **NOT GRANTED** | read-only | disposition review |
| H D7-1 | `market.py`, `orders.py` | **NOT GRANTED** | read-only | **G-5** |
| I D6-D → D7-2 | `state.py`, `portfolio_cache_updater.py` | **NOT GRANTED** | read-only | D6-D first |
| J W-6 | `exchange_executor.py` | **NOT GRANTED** | read-only | classification only |
| K infra / tenant / Redis | Terraform roots, tenant context | **NOT GRANTED** | read-only | tools + services absent |

### 3.1 The eight grant-language hits — all classified as requests

`VER` Each inspected in context. **None is an approval.**

| Hit | Why it is not a grant |
|---|---|
| `A23E14:154` | Blockquote request; its own text: *"This assignment confers no implementation permission"* |
| `PHASE16:520` | Under the heading `## 9. Owner decisions — copy-ready` |
| `PHASE18:540` | Blocker table, column *"Smallest next action"*, prefixed `Grant G1 (§4.3).` |
| `PHASE19:424`, `:426` | Blocker table, column *"Next action"* |
| `PHASE23:262`, `:271` | Numbered blockquote request items |
| `PHASE24:284` | Numbered blockquote request, *"Lane E — new, highest value"* |

This is the documented false-positive class: a regex for `I authorize…` matches every report that
*proposes* a grant. Consistent with the 181 licence-text `GRANTED` hits (A6) and the
`W1D10:360` blockquote template. **The ledger remains the only Class-A register.**

---

## 4. Tests actually executed this phase

I re-ran one suite rather than reusing Phase 25's result, because the prototype lives in
`staging/` under another session's ownership and **no prior hash existed for the implementation
file** — so reuse would have been unverifiable.

| Command | Exit | Result | Duration |
|---|---|---|---|
| `pytest staging/phase17_test_frontend_release_gate.py -q --no-header -p no:cacheprovider` | **0** | **42 passed** | 6.71 s |
| `pytest --collect-only -q staging/ -p no:cacheprovider` | **5** | **0 collected** | 8.40 s |

**Verifier logic still passes on current code; the suite is still invisible to default discovery.**
Both now anchored to recorded blobs (§2.1), so future reuse is verifiable.

**NOT RUN and not claimed:** Bandit (inputs unchanged since the executed Phase 22/24 measurement),
Trivy, Terraform `fmt`/`validate`, CI dispatch, PR creation, AWS/ECR/ECS query, deployment,
database, Redis, network.

**Mocked GitHub API — these 42 cases prove verifier logic, not release safety.**

---

## 5. Status of every priority (reused at verified hashes; nothing re-measured)

| Priority | Finding | Status |
|---|---|---|
| **A** verifier docs | `:15-16` claims *"red only for CRITICAL findings"*; `:123-124` asserts *"a CRITICAL container vulnerability is present. Fix the pin"*. Both false — Bandit `-ll` fails on **MEDIUM**, and on `ecd23a9e` Bandit + Trivy IaC failed while the **container scan passed**. Documentation/diagnostics defect, **not** a gate hole | **NOT GRANTED** |
| **B** security evidence | `sast-scan` upload lacks `if: always()`; 2nd IaC root (`aerora_quant_platform/infrastructure/`) **never scanned**; MEDIUM report skipped; Safety non-blocking via `\|\| echo` | **NOT GRANTED** |
| **C** verifier integration | Two independent invisibility causes: `testpaths = tests` and `python_files` mismatch. Run-level-only gate; conditional `frontend-tests` makes skipped-job admission reachable | **NOT GRANTED** |
| **D** correctness route | `01 PR Check` `pull_request`-only — the sole deadlock | **OWNER DECISION** |
| **E** frontend gate | No gate; published `ecd23a9e` while security failed. Incident **open**; exploitation and bundle contents **UNKNOWN** | **NOT GRANTED** |
| **F** image provenance | Digest output unreachable; `:latest` pre-gate; 9 refs / 4 sources; deploy by tag; ECR immutability undeclared. **`03-deploy.yml:168-174` fail-closed — preserve** | **NOT GRANTED** |
| **G** Bandit | 9 × B608 MEDIUM, exit 1; gate test scoped to one module; `test_perf.py` stale `nosec` = zero gate effect | **NOT GRANTED** |
| **H** D7-1 | 3 invalid sites; **14 legitimate `access_token` sites must survive**; `exchange.py:993` correct | **NOT GRANTED** |
| **I** D6-D → D7-2 | `key_manager` absent **and** class is `APIKeyVault`; mock-vault fallback unresolved | **NOT GRANTED** |
| **J** W-6 | 3/8 misclassified; `retryable` = 2 writes + 1 log read. **No retry introduced** | **NOT GRANTED** |
| **K** infra / tenant | Collision premise withdrawn (separate VPCs); `redis-only` manages production with **local state**. **FORCE RLS off** | **BLOCKED** |

---

## 6. The single ranked authorization request

Ranked by (unblocked value ÷ risk). Each is independently actionable.

> **① G-1 — correctness route.** *Unblocks C, D, E. Zero workflow edits for the first option.*
> *"`main` advances by pull request only, with `01 PR Check` a required check."*
> — **or** — *"I authorize adding `push: branches: [main]` and `workflow_dispatch` to
> `.github/workflows/01-pr-check.yml`, with a `concurrency` group."*
>
> **② G-6 — verifier documentation.** *Lowest risk in the backlog: two text blocks, no logic.*
> *"I authorize correcting the threshold description at `scripts/require_security_scan.py:15-16`
> and the refusal text at `:123-124` to name all three blocking sources (Bandit MEDIUM+, Trivy
> container CRITICAL, Trivy IaC CRITICAL) and to stop asserting a container vulnerability. No
> behavioural change."*
>
> **③ G-4 — security evidence.** *Restores the evidence needed to triage the current red gate.*
> *"I authorize adding `if: always()` to the SAST report upload, making the
> `aerora_quant_platform/infrastructure/` IaC scan run independently of the `terraform/` outcome,
> and preserving the MEDIUM IaC report — without `continue-on-error` or shell fallbacks. Safety is
> ⟨blocking / advisory⟩."*
>
> **④ G-3 — collect the verifier that already works.** *42 cases pass but never run in CI.*
> *"I authorize moving `staging/phase17_frontend_release_gate.py` to `scripts/` and its tests to
> `tests/test_frontend_release_gate.py`, then adding paginated mandatory-job verification."*
>
> **⑤ G-2 — build digest.** *"I authorize editing `.github/workflows/02-build.yml` to delete
> `2>/dev/null` and `|| echo "$IMAGE_TAG"` and fail on an empty/`None`/malformed digest.
> `03-deploy.yml` must not be modified."*
>
> **⑥ G-5 — D7-1.** *"I authorize removing the `access_token=` argument at `market.py:53`,
> `orders.py:792` and `orders.py:1098` only, plus a signature-binding test."*

**Owner decisions still required and not inferable:** the Safety blocking/advisory policy; the
market-data keys-absent policy (anonymous fallback); the mandatory-job inventory; the ECR
immutability owner; and the `redis-only` local-state risk acceptance.

---

## 7. Evidence classification

| Class | Status |
|---|---|
| Verified source configuration | §3.1, §5 — current, at recorded hashes |
| **Unit / mock evidence** | **42 passed this phase** — mocked API; verifier logic only |
| Structural workflow evidence | reused at verified hashes |
| Integration evidence | **BLOCKED** — PostgreSQL, Redis, docker absent |
| CI runtime evidence | **NOT RUN** — no dispatch authorized |
| Registry / Terraform state / live ECS | **NOT RUN** — no access, none authorized |
| Published frontend bundle contents | **UNKNOWN** — manifest never supplied |
| **Release certification** | **NONE** |

---

## 8. Residual risk and next critical dependency

**Next critical dependency: ① G-1.** It is the only item that unblocks others, and the
pull-request option needs no file edits at all.

**Residual risks, unchanged and all owner-gated:** an ungated frontend publication path that has
already fired; a mutable `:latest` advanced before any gate and consumed by nine references; a
release verifier whose refusal text misdirects an operator mid-incident; a security workflow that
discards evidence precisely when a scan fails and has never scanned one infrastructure root; and
tenant isolation with no integration evidence.

**Release recommendation: DO NOT RELEASE.** No applicable gate has passing, same-release-identity
evidence.

---

## 9. Repository integrity

`VER` **Files changed: 0** — source, test, workflow, Terraform, script, migration, governance all
zero. `write-tree` identical entry and exit; tracked tree unmoved at 5705. Both preserved
concurrent files byte-identical. Both stashes untouched. No prior report, prototype, log or
artifact overwritten.

No git write command (no stage, commit, push, merge, rebase, reset, checkout, clean, revert,
history rewrite, stash apply/pop/drop). No CI dispatch, PR, branch-protection or repository-setting
change. No deploy, rollback, republish, cache purge, DNS change, `terraform apply`, infrastructure
provisioning, image build/push/pull/retag/promote/delete, production migration, database/role/grant
mutation, tenant-policy change, FORCE RLS, live trading or exchange order. No secret, token, DSN,
signed payload or decrypted key material read or printed. No threshold weakened, no finding
suppressed, no unavailable check converted to a pass.

**An unchanged tree is not verification. A skipped scan is not a pass. Absent tools are not passed
scans. Mocked tests are not release evidence. Source inspection is not production evidence.**
