# Phase 20 — Release Safety Enforcement, Critical Defect Closure, Parallel Verification

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Overwrites nothing. Does not touch
`PHASE17_*`, `PHASE18_*`, `PHASE19_*`, either `PHASE16_*` report, or any concurrent artifact.

> ## DISPOSITION: **BLOCKED** · **DO NOT RELEASE**
> **0 files modified.** No implementation grant exists for any of lanes A–I.
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED**

This report is **differential**. Phases 17 and 18 are not restated; both are cited and their
hash-matched evidence reused. Phase 20 contributes **three source-verified results that did not
previously exist**, and closes one lane item analytically.

| # | New this phase | Lane | Status |
|---|---|---|---|
| 1 | **`backend_app/test_perf.py` fully dispositioned** — both interpolated values are local literals; the two `# nosec` are stale and their removal cannot change the gate verdict | C3 | **VERIFIED — closed analytically** |
| 2 | **The repository's workflow-policy test convention established from source** — plain-text assertions, not YAML parsing | A | **VERIFIED** |
| 3 | **`ExchangeError.retryable` consumer census re-run independently** — 2 constructor writes, exactly 1 production read | G | **VERIFIED** |

---

## 1. Entry and exit manifests

`VER` Byte-identical at entry and exit. The repository is exactly as I found it.

| Measurement | Entry | Exit |
|---|---|---|
| branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | identical |
| `origin/main` / divergence | `ecd23a9e…` / **0 / 0** | identical |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** | **identical** |
| staged / unstaged / unmerged | **0 / 2 / 0** | **0 / 2 / 0** |
| untracked | 88 | 94 (this report + probes) |
| stash / worktrees | **2** / **1** | **2** / **1** |

**The candidate is still `ecd23a9e`** — verified, not assumed.

### 1.1 Concurrent work preserved byte-for-byte

| Path | Blob (entry **and** exit) |
|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` |

Neither staged, edited, reverted nor reformatted.

### 1.2 Lane targets — all `clean` at exit

| Blob | Path |
|---|---|
| `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` | `.github/workflows/06-frontend-deploy.yml` |
| `91d55a379115c88edd2fd6be01871b82a75ef498` | `.github/workflows/01-pr-check.yml` |
| `eb8078eac06268447d60f26ef7a97a537ceed988` | `.github/workflows/03-deploy.yml` |
| `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | `.github/workflows/05-security.yml` |
| `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` | `scripts/require_security_scan.py` |
| `1c836dad6c235efb1d652a7e72c507958082987b` | `tests/test_nightly_audit_workflow.py` |
| `a7deff99c9e54beee70c115607d538a638561b87` | `tests/test_bandit_sast_gate_is_green.py` |
| `ab3618ebeffe39d229566a312ae86a7709820d4f` | `backend_app/test_perf.py` |
| `16f0377cb9f6c85ef0fb21ce3df37940bd27628e` | `backend_app/routers/market.py` |
| `ff2d34bf030a3086d2af63729c025bbef87ca045` | `backend_app/routers/orders.py` |
| `4b40973568edfa4dc24a8416819f872eaf18d54c` | `backend_app/routers/exchange.py` |
| `a74d76448a15150f703f05d0e43b83fd8470007b` | `backend_app/backend/portfolio_cache_updater.py` |
| `df4fd42a151015124ae7eb3743733107106c413e` | `backend_app/backend/exchange_executor.py` |
| `74a0059411467442b6aaa3d11d0d89af4d00fe65` | `backend_app/backend/api_key_vault.py` |
| `87c9d131051d5e51e1001d0b19325b23cc1855d0` | `terraform/security_groups.tf` |
| `cdead9289f209c581df2828a598c214f59048e76` | `terraform/alb.tf` |
| `48b9471035b77d4f85b9b5140dae8b37830b4a01` | `terraform/redis-only/main.tf` |
| `7e238620bc22f0ad246f0ebc373b7032c6b28ef0` | W1D ledger — `:802` still `NOT GRANTED` |

**All release-path blobs match the values Phases 16–18 recorded**, so their analyses are **REUSED
FROM IDENTICAL HASH** rather than re-derived. No concurrent session has modified any of them
despite eight phase reports now existing.

### 1.3 Authorization matrix

| Lane | Target files | Grant | Owner needed |
|---|---|---|---|
| A frontend gate | `06-frontend-deploy.yml` + new test | **ABSENT** | workflow / release owner |
| B correctness route | `01-pr-check.yml` **or** branch protection | **ABSENT** | coordinator + release owner |
| C Bandit | 3 migrations, gate test, `test_perf.py` | **ABSENT** | code owner |
| D Trivy | `security_groups.tf`, `redis-only/main.tf`, `alb.tf` | **ABSENT** | infrastructure owner |
| E D7-1 | `market.py`, `orders.py`, 1 test | **ABSENT** | router owner |
| F D7-2 | `portfolio_cache_updater.py` + 1 test | **ABSENT** | portfolio-cache owner |
| G W-6 | `exchange_executor.py` + tests | **ABSENT** | execution-state owner |
| H tenant / Redis / quota | — | **ABSENT** + no environment | tenancy owner |
| I migrations / workers | — | **ABSENT** | code owner |

`VER` W1D ledger `:800-804` — all five rows `NOT GRANTED`; **no row covers a workflow, Terraform,
migration, router or test file.** `AP-W1D-3` (the one historical grant) was consumed at A29.
Nothing in this prompt, a prior phase, a passing test, a successful deployment, or the existence of
`require_security_scan.py` was treated as authorization.

---

## 2. Lane C3 — `backend_app/test_perf.py`: closed analytically

The brief asked whether the `# nosec` annotations there are stale. `VER` Read in full — and the
answer is more interesting than "stale":

```python
uid  = "52384fe1-c1dd-4540-89e4-5c36dd8a2bf8"    # module-local string LITERAL
days = 30                                         # int literal
query = (
    "SELECT "                      # nosec: B608   ← line 18
    ...
    f"WHERE user_id = '{uid}' "
    f"AND timestamp > dateadd('d', -{days}, now());"
)                                  # nosec: B608   ← line 27
```

| Question | Answer |
|---|---|
| Are the interpolated values user-controlled? | **No.** `uid` is a hardcoded UUID *string literal*; `days` is the literal `30`. Both assigned in the same function, three lines above. |
| Is this a security defect? | **No.** No external input reaches the statement. |
| Why does the AST safety guard pass? | Correctly — a name bound to a string literal is constrained under its RULE 5, so there is nothing to flag. **This is the guard working, not a gap.** |
| Are the two `# nosec: B608` stale? | **Yes.** Bandit reports **no** B608 at those lines, which is exactly why it emits *"nosec encountered (B608), but no failed test on file backend_app\test_perf.py:25"* and `:26`. They suppress nothing. |
| Does removing them change the gate verdict? | **No.** The messages are `WARNING`-level, not findings. Bandit's exit code is driven solely by the **nine** B608 findings in the three Alembic migrations. |

> **Lane C3 disposition: the annotations are safely removable pure hygiene, and removing them
> has ZERO effect on the gate.** This is worth stating precisely, because "remove the stale nosec"
> could otherwise be mistaken for progress toward a green gate. It is not.

Two incidental observations, recorded without inflation:

- `backend_app/test_perf.py` is a `test_`-named developer scratch module sitting in the
  **production package tree**, not under `tests/`. It is inside Bandit's and the AST guard's scope
  purely because of where it lives.
- Its module-level `load_dotenv('d:/aerora_quant_backend_updated_final1/backend_app/.env')`
  hardcodes a **`d:/` absolute path** that does not exist on this machine (the repo is on `c:/`).
  Relocation or deletion is a tidy-up for the owner, not a security item.

---

## 3. Lane A — the workflow-policy test convention, established from source

The brief directs any new gate test to "follow existing workflow-policy test conventions."
`VER` `tests/test_nightly_audit_workflow.py` is the only such precedent, and it is deliberately
simple:

```python
workflow_path = pathlib.Path(".github/workflows/04-nightly-audit.yml")
assert workflow_path.exists(), "... file missing"
content = workflow_path.read_text(encoding="utf-8")
assert "pip-audit" in content, "Nightly workflow missing pip-audit step"
assert "cd algo22-terminal/src-tauri" in content, "..."
```

**The convention is `pathlib` + `read_text` + substring assertions on the raw YAML text, with an
explanatory message on every assert. No `yaml.safe_load`, no job-graph parsing.**

That matters for the Lane A design, and it cuts both ways:

- **Follow it** for existence and wiring checks — it is the house style and needs no new dependency.
- **But substring assertions cannot express the two load-bearing properties** a release gate needs:
  that **every** publishing job declares `needs: <gate>`, and that no *unguarded* publication path
  exists. A `needs:` substring can be present while a second publishing job omits it.

> **Recommendation for whoever holds the grant:** keep the house style for the simple assertions
> (gate script invoked, `DEPLOY_SHA: ${{ github.sha }}` pinned, `ALLOW_UNSCANNED: 'false'`,
> `actions: read` present), and add **`yaml.safe_load` for the dependency-graph assertions only** —
> enumerate every job that performs an S3 sync or CloudFront invalidation and assert each one
> transitively depends on the gate. State the deviation from convention in the test's docstring so
> it reads as deliberate. Negative control: delete the `needs:` from one publishing job and show the
> test fails.

Lane A's full gate design, the two GitHub Actions traps (`workflow_run` sets `GITHUB_SHA` to the
**default-branch head**; `workflows: [A, B]` fires when **either** completes), the
`require_security_scan.py` reusability analysis and the ten-case matrix are in
`PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md` §3 and are unchanged.

**Not implemented — grant ABSENT.** `06-frontend-deploy.yml` is `clean`. A concurrent session holds
this lane (`PHASE17_D_P15_4_…`, `PHASE18_…`); **file ownership respected, nothing touched.**

---

## 4. Lane G — `retryable` consumer census, re-run independently

The brief asked whether `retryable` has acquired consumers since the prior report. `VER`
Repository-wide search of `backend_app/` and `scripts/`:

| Site | Kind |
|---|---|
| `backend_app/backend/exchange_executor.py:92` | constructor **write** (`self.retryable = retryable`) |
| `backend_app/backend/exchange_simulator.py:67` | constructor **write** in the simulator's shadow class |
| **`backend_app/backend/exchange_executor.py:1687`** | **the only read** — an f-string in a log line: `f"retryable={error.retryable}"` |

**No new consumers.** Two writes, exactly one read, and that read is a log field.

> This independently reproduces Phase 18 §11's conclusion and I adopt its careful framing: W-6 is
> **misclassification and a corrupted error contract, not demonstrated retry suppression.** No
> control-flow branch reads `retryable`, so fixing the classifier changes *recorded* outcomes and
> telemetry severity, not retry behaviour. Correspondingly **no retry was enabled, and a retryable
> classification is not licence to retry a non-idempotent order.**

`exchange_executor.py` is `clean` at `df4fd42a…`, so the measured 3-of-8 matrix and the third
shadowing defect at `:1225-1257` are **REUSED FROM IDENTICAL HASH** (A23E17, Phase 18). Re-running
against byte-identical source would duplicate evidence, which the brief forbids.

---

## 5. Lanes B, D, E, F, H, I — status, cited not restated

All governing hashes identical → **REUSED FROM IDENTICAL HASH**.

| Lane | State | Key fact |
|---|---|---|
| **B** correctness route | **BLOCKED** | `01 PR Check` is `pull_request`-only (`push: 0`, `workflow_dispatch: 0`). Its `frontend-tests` job is itself conditional on `steps.changes.outputs.frontend == 'true'`, so even on a PR it can skip — reinforcing that a run-level `success` is not correctness evidence |
| **D** Trivy | **DECISION-READY**, 0 of 5 fixed | Redis egress (`security_groups.tf:73`, `redis-only/main.tf:62`) is the only disposition safe on its own evidence — Redis is a connection *target* with ingress already scoped to the ECS SG on 6379. **Removing those two does not close a gate that still has three blocking findings.** Trivy **NOT RUN** (absent, docker absent) |
| **E** D7-1 | **DECISION-READY** | Exactly **3** invalid `access_token=` args — `market.py:53`, `orders.py:792`, `orders.py:1098`. **`exchange.py:993` is CORRECT — do not touch** (my Phase 17 correction, re-confirmed: `exchange.py` blob unchanged) |
| **F** D7-2 | **DECISION-READY** | Two independent errors: `backend_app.backend.key_manager` does not exist (`ModuleNotFoundError`, executed probe) **and** the class is `APIKeyVault`, not `KeyVault`. A module-path-only fix still fails |
| **H** tenant / Redis / quota | **BLOCKED — environment** | Redis 6379 closed, 0 `REDIS*` vars, docker absent, PostgreSQL 5432 closed. 33 tracked `*postgres*.py`, **0 executed on the candidate**. **FORCE RLS not enabled** — correctly, context unverified |
| **I** migrations / workers | **BLOCKED** | Baseline preserved at `e6069946…`; no recapture (`AP-W1D-4` NOT GRANTED). Reconciliation service still unwired (`main.py` clean). W-4 UNKNOWN-exit **not invented**; W-5 recovery remains blocked behind it |

---

## 6. Commands run, with tool provenance

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status --short` / `write-tree` / `hash-object` / `worktree list` / `stash list` (entry + exit) | 0 | §1 manifests; `write-tree` `4b45a763…` both times |
| `python staging/phase20_exit.py` | 0 | exit manifest, 18 lane targets all `clean` |
| targeted greps: `nosec` in `test_perf.py`; `\.retryable` across `backend_app`+`scripts`; policy-test conventions | 0 | §2, §3, §4 |
| file reads: `test_perf.py` (full), `test_nightly_audit_workflow.py` | — | §2, §3 |

**NOT RUN, and not claimed:** pytest, Bandit, Trivy, Terraform `fmt`/`validate`, CI dispatch,
database, Redis, exchange, network. Bandit and the drift suites were deliberately **not** re-run —
every input is byte-identical to the runs recorded in Phases 15–18, and the brief instructs against
regenerating logs for unchanged work. One command was interrupted (`^C`, exit −1) and was re-run as
a script file; that is recorded rather than hidden.

Artifacts, all new and uniquely named:
`staging/PHASE20_RELEASE_SAFETY_AND_CRITICAL_CLOSURE.md`, `staging/phase20_exit.py`,
`staging/phase20_exit.txt`.

---

## 7. Evidence classification

| Claim | Class |
|---|---|
| `test_perf.py` literals, stale nosec, zero gate effect | **VERIFIED** (source, this phase) |
| Workflow-policy test convention | **VERIFIED** (source, this phase) |
| `retryable`: 2 writes, 1 log read, no new consumers | **VERIFIED** (source, this phase) |
| Entry/exit manifests, 18 clean lane targets | **VERIFIED** (this phase) |
| 9 × B608; gate/test scope divergence | **REUSED FROM IDENTICAL HASH** |
| 5 Trivy findings and dispositions | **REUSED FROM IDENTICAL HASH**; scanner **NOT RUN** |
| D7-1 census; D7-2 two-part defect | **VERIFIED** (Phase 17, hashes unchanged) |
| W-6 3-of-8 matrix | **REUSED FROM IDENTICAL HASH** |
| Correctness evidence on `ecd23a9e` | **NOT RUN — none exists** |
| Tenant isolation; real-Redis quota; PostgreSQL | **NOT CERTIFIED / BLOCKED** |

No skipped or unavailable test is reported as passed. No scanner is reported as passing that was
not executed. No production behaviour is claimed from local analysis.

---

## 8. Blockers and minimal approvals

| # | Blocker | Owner | Minimal next action |
|---|---|---|---|
| **1** | Frontend publishes with **no** security gate — a realized bypass | workflow / release owner | Grant: add a `security-gate` job to `06-frontend-deploy.yml` running `require_security_scan.py` with `DEPLOY_SHA: ${{ github.sha }}`, `ALLOW_UNSCANNED: 'false'`, `permissions: actions: read`, and `needs:` from **every** publishing job (§3) |
| **2** | Correctness structurally unreachable on push | coordinator + release owner | PR-only advancement of `main`, **or** grant `push: [main]` on `01-pr-check.yml` |
| **3** | `05 Security` RED → `main` undeployable | code owner + infrastructure owner | Bandit: widen the gate test **first**, then the 9 annotations. Trivy: remove 2 Redis egress rules, document 3 accepts |
| **4** | No correctness/PostgreSQL evidence at `ecd23a9e` | coordinator | Authorize the PR route; **do not "optimize away" the double `provision_database.py` run** — it is an intentional idempotency guard |
| **5** | TENANT-01 uncertified | tenancy owner | Approve a disposable PostgreSQL; run the tenant lane with a **non-owner, non-`BYPASSRLS`** role; **do not enable FORCE RLS first** |
| **6** | Portfolio cache dead behind false-green health | portfolio-cache owner | Fix module **and** class (§5 F) |
| **7** | D7-1 three invalid kwargs | router owner | 3 deletions in 2 files + align 2 doubles + signature-binding test; **exclude `exchange.py`** |
| **8** | W-6 misclassification | execution-state owner | Ladder reorder + bounded auth matching + tests; **no retry-policy change** |
| **9** | A23 Redis P1 — 0 of 8 gates | code owner | Establish the **role-defining register row first**, then issue the grant |
| **10** | W1C-02 scope, test-6 uncommitted, D5 sign-off, ledger `:802` | code owner | One reply resolving all four |

**Highest leverage: #2.** A gate cannot require a workflow that can never run on the commit it
gates, and no amount of local green substitutes for evidence on the shipped SHA.

---

## 9. Final posture

| Item | Status |
|---|---|
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| Backend deployment | **BLOCKED** — `05 Security` RED on the candidate SHA |
| Frontend deployment | **BLOCKED** — no gate exists; it already published ungated |
| Production infrastructure changes | **NOT AUTHORIZED** |
| Source correctness on `ecd23a9e` | **UNVERIFIED** — 0 correctness jobs ever ran |
| Files modified | **0** source · **0** test · **0** workflow · **0** Terraform · **0** migration · **0** governance |
| **Overall** | **DO NOT RELEASE** |

No git write command (no stage, commit, push, merge, rebase, reset, stash, clean, revert, branch
change, history rewrite). No CI dispatch, PR, deploy, rollback, CDN purge, DNS change,
`terraform apply`, infrastructure provisioning, production database or Redis mutation, FORCE RLS
enablement, exchange-credential use, live order, or live-trading change. No secret, token, DSN or
decrypted key material printed. No scanner threshold weakened, no finding hidden, no skipped test
converted to a pass. No report, log or concurrent artifact overwritten; both pre-existing
modifications byte-identical at exit.

**Continuation note for the next engineer:** lanes A, B, D, E, F, G are **decision-ready** — the
analysis is complete and each needs only its named owner's grant, with the exact file list in §8.
Lane C3 is **closed** (§2). Lanes H and I are **environment-blocked**, not analysis-blocked. No
lane requires repeating the investigation.
