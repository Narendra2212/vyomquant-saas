# Phase 21 — Cross-Phase Reconciliation, Release Gate Enforcement, Critical Defect Closure

**Destination verified ABSENT before writing.** **0 production files · 0 test files ·
0 workflows · 0 Terraform · 0 migrations · 0 governance records · 0 commits · 0 pushes ·
0 staged entries · 0 CI dispatched · 0 services started · 0 stashes altered.**
1 artifact written: this file.

> ## DISPOSITION: **DO NOT RELEASE**
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED**
> **Backend deployment: BLOCKED · Frontend deployment: BLOCKED**

---

## 1. Executive summary

Authorization is unchanged at **zero grants**, now corroborated by seven independent reports
and proved by git rather than by their own claims. This phase's value is therefore new
measurement, and four results matter — **two of which correct my own prior Phase 17 report.**

| # | Result | Status |
|---|---|---|
| **1** | **The Trivy `AWS-0104` count discrepancy is RESOLVED: there are 4, not 3.** My Phase 17 report searched only `security_groups.tf` and missed a fourth egress block in a **separate root module**, `terraform/redis-only/main.tf:58`. **Phase 15's count of 4 was correct; my count of 3 was wrong.** | **RESOLVED** |
| **2** | **The brief's "two Redis egress rules" was RIGHT, and my Phase 17 "correction" of it was WRONG.** There are two distinct `aws_security_group "redis"` resources, in two root modules. I retract the Phase 17 statement. | **RETRACTED** |
| **3** | **New finding: the two Redis security groups collide on name.** `redis-only` hardcodes `vyomquant-redis-sg-production`; the main module computes `vyomquant-redis-sg-${var.environment}`. At `environment = "production"` both root modules try to create the same SG name in the same VPC. | **NEW** |
| **4** | **Phase 20's ECR claim verified first-hand:** `02-build.yml` has **no `needs:`**, triggers on `push: [main, master]`, and pushes **both** `:$IMAGE_TAG` **and** `:latest` (`:72`, `:75-76`). An unvalidated image claims `:latest` on every push to main. | **VERIFIED** |

Additionally: all **9** Bandit B608 findings reviewed **individually** (§7.2); the two stashes
**characterized** for the first time (§2.2); and all defect lanes revalidated as still open.

Vocabulary: **MEASURED** (probe this pass) · **VERIFIED** (independent claim confirmed
first-hand) · **REUSED** (prior evidence, inputs hash-checked) · **RETRACTED** (prior claim of
mine withdrawn) · **NOT GRANTED**.

---

## 2. Fresh entry / exit manifest

| Property | Entry | **Exit** |
|---|---|---|
| `HEAD` | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| Branch · `origin/main` | `main` · `ecd23a9e…` identical | unchanged |
| Ahead / behind | **0 / 0** | 0 / 0 |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | **identical** |
| Staged · unmerged | 0 · 0 | **0 · 0** |
| Files differing from `HEAD` | **2** | **2 — the same two** |
| Worktrees | 1 | 1 |
| **Stash entries** | **2** | **2 — untouched** |
| Porcelain | 98 | 99 (+1 = this report) |

`git write-tree` is **byte-identical at entry and exit**, and identical to the value Phase 17
recorded. **This proves, independently of any report's self-assessment, that no tracked file
has changed across Phases 17 → 21.**

### 2.1 Concurrent modifications preserved

| Path | State | Disposition |
|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | ` M` vs `HEAD` | **not touched** |
| `tests/test_drift_classification.py` | ` M` vs `HEAD` (+143/−0) | **not touched** — another session's W1C-02 / `AP-W1D-3` work |

Every `phase17_*`, Phase 18, 19 and 20 artifact was **read only**. No report was overwritten;
the Phase 21 destination was verified absent first.

### 2.2 The two stashes — characterized for the first time

Phase 20 recorded the stash **count** (*"2 (pre-existing)"*, *"2 — untouched"*). No phase has
characterized their **contents**. Done here, read-only, and **neither was applied, dropped or
modified**.

| Ref | Subject | Nature |
|---|---|---|
| `stash@{0}` | `filter-branch: rewrite` | **Not a stash.** `git stash show` returns *"not a stash-like commit"*. It is a `git filter-branch` backup that landed in the stash reflog; `refs/original/refs/stash` exists. **Evidence that history was rewritten at some point in this repository's past.** |
| `stash@{1}` | `WIP on main: c673409 Release v0.1.0: …` | A real WIP stash, commit `99ceb9b0e9915c5be7d8d5396da4c73912cabf1f`, holding **18 files**. |

`stash@{1}`'s base commit `c673409` is **NOT an ancestor of `HEAD`** (`git merge-base
--is-ancestor` → false). **The stash is orphaned WIP from a pre-rewrite history.**

Its 18 files include security-critical modules directly overlapping open lanes:
`backend_app/core/credential_vault.py`, `core/tenant_middleware.py`, `core/auth_middleware.py`,
`core/websocket_auth.py`, `core/dependencies.py`, `core/database_pool.py`, `core/rate_limit.py`,
`core/reconciliation_scheduler.py`, `core/unified_execution_engine.py`,
`routers/auth.py`, `routers/user.py`, `routers/distributed_execution.py`, `terraform/ecs.tf`,
`tests/test_end_to_end_api_suite.py`.

**All 18 target paths exist in the working tree**, so the stash holds *alternative* content, not
missing files. Two consequences worth recording:

- **It must not be applied or dropped.** Applying orphaned pre-rewrite content over current
  files would silently revert work; dropping it would destroy the only copy. Both require a
  separate owner decision.
- **Note for the tenant lane:** a stashed `core/tenant_middleware.py` exists. That does **not**
  mean tenant context was ever wired — §8 confirms the current tree still has zero consumers —
  but an owner reviewing the identity contract should know this artifact exists.

### 2.3 Shared resources — unchanged and untouched

| Resource | State | Action |
|---|---|---|
| PostgreSQL | port **51815 OPEN**, 51805 **CLOSED** | none |
| `staging/pg_dsn.txt` | reads `…:51805` — **stale** | **NOT EDITED** (prohibited) |
| Redis | `AERORA_TEST_REDIS_URL` **unset**, 6379 **CLOSED** | none — not started |
| Trivy | **not installed** | not installed |

---

## 3. Reports enumerated and reconciled

All six reports named in the brief are **present**, plus one not named.

| Report | Present | Reconciliation |
|---|---|---|
| `PHASE17_EVIDENCE_RECONCILIATION_AND_CRITICAL_CLOSURE.md` | yes | **Mine. Two claims retracted here** (§6.1, §6.2). |
| `PHASE17_RELEASE_GATE_AND_CRITICAL_DEFECT_CLOSURE.md` | yes | *"DISPOSITION: BLOCKED · DO NOT RELEASE. 0 files modified."* **Agrees.** |
| `PHASE17_D_P15_4_RELEASE_GATE_AND_PARALLEL_CLOSURE.md` | yes | *"`D-P15-4` is NOT GRANTED. It exists only as a request (`PHASE15:313-316`)."* **Agrees — and this is the decisive authorization fact for Lane A.** |
| `PHASE18_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` | yes | *"0 production files · 0 test files · 0 workflows…"* **Agrees.** |
| `PHASE19_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md` | yes | *"Authorizations exercised: none."* **Agrees.** |
| `PHASE11B_A23E17_EXECUTED_EVIDENCE_AND_W6_MEASUREMENT.md` | yes | *"0 implementations authorized."* Preserves the P2 `XFAIL(strict)` tripwire. **Agrees.** |
| `PHASE20_FRONTEND_RELEASE_GATE_AND_CRITICAL_CLOSURE.md` | **yes — not named in the brief** | Completed Phase 19's unfinished `workflow_dispatch` audit. Its ECR claim **verified** (§5.2). |

**Three independently written Phase 17 reports exist.** None supersedes another; they are
treated as parallel evidence, as instructed. Agreement between them is corroboration.

### 3.1 Corroborated vs reused vs contradicted

| Class | Items |
|---|---|
| **Corroborated by ≥3 independent reports** | 0 grants; 0 files modified; `D-P15-4` is a request; frontend has no `needs:`; `01 PR Check` never ran at this HEAD; LIVE TRADING OFF |
| **Reused after hash check** | W-6 matrix (10/20); RLS FORCE experiment (2→0→1 rows); D6-C fixtures (7/7); Bandit 9 findings |
| **Contradicted → re-measured this pass** | **Trivy `AWS-0104` count (3 vs 4)** → resolved to **4** (§6.1); **"two Redis rules"** → **two confirmed** (§6.2) |
| **Verified this pass** | Phase 20's `02-build.yml` `:latest` claim (§5.2) |
| **Explicitly left open** | Whether production actually loads a `MockEngine` (needs boot log); production D6-C counts; token-claim divergence |

### 3.2 Prototype status — not regression coverage

`staging/phase17_frontend_release_gate.py` and `staging/phase17_test_frontend_release_gate.py`
(42 passing) are **prototypes**. `pytest.ini` sets `testpaths = tests`, so **they do not run in
the default lane**. Per the brief, they are not treated as active regression tests. Landing them
in `tests/` requires a grant.

---

## 4. Authorization table

| Lane | Intended files | Grant | Owner | Allowed operations |
|---|---|---|---|---|
| **A** frontend gate | `.github/workflows/06-frontend-deploy.yml`, `tests/` | **NOT GRANTED** — `D-P15-4` is a *request* (`PHASE15:313-316`) | workflow/release owner | **read-only** |
| **B** correctness evidence | `01-pr-check.yml`, `02-build.yml`, branch policy | **NOT GRANTED** — `D-P15-3` undecided | release owner | read-only |
| **C** Bandit parity | `tests/test_bandit_sast_gate_is_green.py`, migrations | **NOT GRANTED** | code owner | read-only |
| **D** Trivy/Terraform | `terraform/**` | **NOT GRANTED** | security + infra owner | read-only |
| **E** D7-1 | `routers/orders.py`, `routers/market.py`, `tests/` | **NOT GRANTED** | code owner | read-only |
| **F** D7-2 / D6-D | `portfolio_cache_updater.py`, `core/state.py` | **NOT GRANTED** | code owner + platform | read-only |
| **G** W-6 | `backend/exchange_executor.py`, `tests/` | **NOT GRANTED** | code owner | read-only |
| **H** tenant/RLS/Redis | `core/tenant_db_context.py`, migrations, DB | **NOT GRANTED** | code + DB owner | read-only |
| **I** migrations/workers | drift classifier, migration 022, `main.py` | **`AP-W1D-3` — scope-limited, NOT applicable** | code owner | read-only |

**`AP-W1D-3` is confined to its own classifier files** (*"No other files are authorized"*). It
authorizes nothing in Lanes A–H. `A23E17:75` warns that A29's three bare `GRANTED` tokens all
resolve to `AP-W1D-3`, so token-counting over-reports grants. Neither this prompt, nor a
decision record, nor a passing prototype is an implementation grant.

**Net: 0 of 9 lanes authorized to modify anything.**

---

## 5. Lanes A and B — release gates

### 5.1 Lane A — frontend security gate (REVALIDATED, read-only)

`needs:` / `continue-on-error` / `require_security_scan` measured across all workflows:

| Workflow | Gate evidence |
|---|---|
| `01-pr-check.yml` | `needs: static-audit` ×5; `continue-on-error: true` ×3 |
| `02-build.yml` | **none** |
| `03-deploy.yml` | `run: python scripts/require_security_scan.py` (`:95`); `needs: pre-deployment-validation` (`:130`) |
| `05-security.yml` | **none** (so its own steps are blocking) |
| **`06-frontend-deploy.yml`** | **`needs:` count = 0** |
| `07-desktop-build.yml` | `needs: build` |

`06-frontend-deploy.yml` triggers on `push: branches: [main, master]` plus
`workflow_dispatch`, and runs `aws s3 sync dist/ s3://…` at `:289`, `:299`, `:307` and
`aws cloudfront create-invalidation` at `:329`. **Nothing gates any of them.**

**The backend gate is correctly wired and was not touched**, per the brief.

### 5.2 Lane B — correctness evidence (Phase 20 claim VERIFIED)

`02-build.yml`, measured first-hand:

```
:14  branches: [main, master]          # push trigger
     (no `needs:` anywhere in the file)
:72  docker build -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG \
                   -t $ECR_REGISTRY/$ECR_REPOSITORY:latest -f Dockerfile .
:75  docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
:76  docker push $ECR_REGISTRY/$ECR_REPOSITORY:latest
```

**Confirmed: on every push to `main`, an image built with no correctness or security gate is
pushed to ECR and claims the `:latest` tag.** Phase 20's finding stands.

`01 PR Check` has **never run at `ecd23a9e`**, so **the release SHA has no correctness evidence
of any kind**. The frontend tests live in a PR-only workflow while publication happens on push.

**`D-P15-3` (PR-gated vs main-branch correctness workflow) remains undecided.** No change made.

> **A structurally correct workflow would not be proof of a safe release.** No workflow test was
> executed here and no release was performed. Workflow structure and deployment evidence are
> reported separately, per the brief.

---

## 6. Lane D — Trivy and Terraform

**Trivy is not installed** (`Get-Command trivy` → false) and installing it was not authorized,
so **the scanner was not run and no scanner pass is claimed**. The count discrepancy was instead
resolved at source level, which is sufficient because the question was *how many unrestricted
egress rules exist*.

### 6.1 The `AWS-0104` count — RESOLVED as 4. My Phase 17 count of 3 was wrong.

A search of **all** `*.tf` under `terraform/` (my Phase 17 pass read only `security_groups.tf`):

| # | Resource | File : line | `cidr_blocks` |
|---|---|---|---|
| 1 | `aws_security_group.alb` | `terraform/security_groups.tf:22` | `0.0.0.0/0` (`:26`) |
| 2 | `aws_security_group.ecs_tasks` | `terraform/security_groups.tf:52` | `0.0.0.0/0` (`:56`) |
| 3 | `aws_security_group.redis` | `terraform/security_groups.tf:73` | `0.0.0.0/0` (`:77`) |
| 4 | **`aws_security_group.redis`** | **`terraform/redis-only/main.tf:58`** | **`0.0.0.0/0` (`:62`)** |

**Phase 15's 4 was correct. My Phase 17 report's 3 was a search-scope error, and the
"unreconciled discrepancy" I recorded there is withdrawn.** The lesson is narrow and worth
stating: `terraform/` contains **more than one root module**, and a single-file read missed one.

Not `AWS-0104`: `vpc.tf:75` and `:96` use singular `cidr_block = "0.0.0.0/0"` — route-table
default routes, not security-group egress.

### 6.2 "Two Redis egress rules" — the brief was right; I retract my Phase 17 correction

In Phase 17 I wrote that the brief's reference to *"the two Redis egress rules"* was incorrect
and that there is only one. **That was wrong.** There are two distinct `aws_security_group
"redis"` resources:

| Module | Name expression | Egress |
|---|---|---|
| `terraform/` (main) | `vyomquant-redis-sg-${var.environment}` | `security_groups.tf:73-78` |
| `terraform/redis-only/` | `vyomquant-redis-sg-production` (hardcoded) | `main.tf:58-63` |

### 6.3 NEW finding — the two Redis security groups collide on name

`terraform/redis-only/main.tf` is a **separate root module** that adopts existing
infrastructure through `data` sources (`data.aws_vpc.existing`, `data.aws_subnet.private_1/2`,
`data.aws_security_group.ecs_tasks` pinned to `sg-0262aed02d8df0356`) and then **creates** its
own `aws_security_group.redis` named `vyomquant-redis-sg-production`.

The main module creates `vyomquant-redis-sg-${var.environment}`. **With
`environment = "production"`, both modules create a security group with the identical name in
the same VPC.** AWS requires SG names to be unique per VPC, so whichever applies second fails —
or, worse, the two modules fight over ownership of one group across applies.

**This is a latent infrastructure conflict independent of Trivy**, and it is the strongest
reason not to treat the two Redis egress rules as one finding to fix once. **No Terraform was
modified and none was applied.** It needs an infra-owner decision about which module owns the
Redis security group.

### 6.4 `AWS-0054` — false positive, compensating control re-verified

`terraform/alb.tf`, `aws_lb_listener.http`: a `dynamic "default_action"` gated on
`var.enable_custom_domain` emitting a **:80 → :443 `HTTP_301` redirect**; a second inverse
`dynamic` block for the documented bootstrap-only plaintext path; and

```hcl
lifecycle {
  precondition {
    condition = var.enable_custom_domain || !contains(["prod", "production"], var.environment)
    ...
  }
}
```

**The apply is refused for prod without a custom domain.** Trivy evaluates neither `dynamic`
blocks nor `lifecycle.precondition`. **Exploitability in prod: none — the apply is refused.**

### 6.5 Disposition of all five CRITICALs

| Finding | Resource | Disposition | Blocking? |
|---|---|---|---|
| `AWS-0054` | `aws_lb_listener.http` | **False positive**, compensating control verified (§6.4) | **Yes** until an owner accepts |
| `AWS-0104` #1 | `aws_security_group.alb` | Real; **needed** — must reach target groups | **Yes** |
| `AWS-0104` #2 | `aws_security_group.ecs_tasks` | Real; **needed** — ECR, Secrets Manager, Supabase, venues, monitoring | **Yes** |
| `AWS-0104` #3 | `aws_security_group.redis` (main) | Real; **candidate** for tightening | **Yes** |
| `AWS-0104` #4 | `aws_security_group.redis` (`redis-only`) | Real; **candidate**; also the name-collision case (§6.3) | **Yes** |

**I do not assert either Redis egress rule is unnecessary.** Whether an ElastiCache node needs
node-initiated egress depends on cluster mode, replication topology and backup destinations —
none of which I verified, and the brief requires evaluating them first. These are candidates
requiring owner confirmation, not safe-by-inspection deletions.

**0 of 5 cleared · 0 Terraform modified · 0 applied · 0 suppressions · 0 threshold changes.**
A partial fix is not presented as a green gate. **DEPLOYMENT remains BLOCKED.**

---

## 7. Lane C — Bandit scanner/test parity

### 7.1 Parity gap and its mechanism (REUSED + confirmed)

CI command, `05-security.yml:36`: `bandit -r backend_app -ll -f json -o
reports/bandit-report.json`. **No `continue-on-error` in `05-security.yml`**, so exit 1 fails
the job. **9 findings, all B608 MEDIUM, 219,319 LOC scanned.**
`tests/test_bandit_sast_gate_is_green.py` → **3 passed in 96.75s**.

Two things combine:

- **The scanner test is scoped to one file, by design.** `GATED_MODULE = backend_app/backend/
  transactional_execution_manager.py`; the docstring states it is *"scoped to one file so it
  runs in about a second instead of the 5m44s a full `-r backend_app` sweep takes."*
- **The AST test asserts a different property than B608 detects.** It proves interpolated
  values are constrained by a validator that **raises**
  (`SQL_SAFE_VALIDATORS = {json_text, _validate_table_name}`). B608 fires on **string-building
  into SQL at all**. So the code can be safe *and* bandit red simultaneously — the present
  state. **The intended coverage handoff cannot close a scanner-parity gap, because it is not
  measuring the scanner's condition.**

### 7.2 All nine findings reviewed individually (MEASURED)

| # | File : line | Construction | Identifier source | Value binding |
|---|---|---|---|---|
| 1 | `a1b2c3d4e5f6…:212` | `SELECT count(*) FROM public.{table} WHERE {column} …!~* :re` | `_validate_table_name` (`:209`) | `:re` bound |
| 2 | `a1b2c3d4e5f6…:222` | `SELECT DISTINCT {column} FROM public.{table} … LIMIT 5` | same validator | `:re` bound |
| 3 | `b2c3d4e5f6a7…:224` | `UPDATE public.positions SET {name} = :v WHERE {name} IS NULL` | module literal constant | `.bindparams(v=…)` |
| 4 | `b2c3d4e5f6a7…:262` | `UPDATE public.positions SET {column} = now() WHERE {column} IS NULL` | module literal constant | n/a — `now()` is SQL, no value |
| 5 | `d4e5f6a7b8c9…:193` | `SELECT count(*) FROM public."{table}" WHERE "{column}" IS NOT NULL` | `_validate_table_name` (`:190`) | none needed |
| 6 | `d4e5f6a7b8c9…:198` | `… WHERE "{column}" IS NOT NULL AND "{column}"::text !~* :re` | same validator | `:re` bound |
| 7 | `d4e5f6a7b8c9…:209` | `SELECT DISTINCT "{column}"::text FROM public."{table}" …` | same validator | `:re` bound |
| 8 | `d4e5f6a7b8c9…:467` | `SELECT count(*) FROM public."{table}" t WHERE … NOT EXISTS …` | same validator | — |
| 9 | `d4e5f6a7b8c9…:475` | `SELECT DISTINCT t."{column}"::text FROM public."{table}" t …` | same validator | — |

`_validate_table_name` occurrences: 16, 5 and 21 in the three files respectively.

**Disposition:** **9 of 9 are safe constructions** — every interpolated fragment is either a
validator-constrained identifier (`^[a-zA-Z0-9_]{1,64}$`, raises, never sanitises) or a
module-level literal constant; every user-influenced value is parameter-bound.
**9 of 9 remain legitimately flagged**, because B608 is a string-building heuristic that does
not model validators. **0 suppressed. 0 accepted by an authorized owner.**

**Safety-reviewed is not dispositioned.** The gate is red and no owner has accepted the risk.
Also measured: `phase17_bandit_stderr.txt` shows ~60 existing `# nosec B608` markers across
eight application modules, all reported *"nosec encountered… but no failed test on file"* —
reviewed sites. **The three migration files are the ones the suppression convention never
reached.** No stale suppression was removed; demonstrating obsolescence requires a grant.

### 7.3 Proposed repair (NOT APPLIED)

1. A whole-tree test running **the exact CI command and threshold** (`-r backend_app -ll`),
   asserting exit 0; marked slow (~5m44s).
2. A **negative control** with a representative unsuppressed B608 that the parity test must
   detect — without it the parity test could pass vacuously.
3. **Retain** both AST/validator tests; they complement parity.
4. Only afterwards, consider narrow owner-authorized per-site suppressions.
5. **Not proposed, and prohibited:** lowering severity, excluding migrations, blanket
   suppressions, `|| true`, baseline regeneration.

**The scanner was not re-run this pass** (identical command, unchanged inputs, prior JSON
retained). **No pass is claimed.**

---

## 8. Lanes E–I — defect lanes, all revalidated as open

| Lane | Finding, revalidated at current HEAD | Status |
|---|---|---|
| **E · D7-1** | Three invalid `access_token=` args to `load_decrypted_keys(self, user_id, exchange_id)`: `orders.py:792` (**unguarded → 500**), `orders.py:1098` (`except` → `return []`, **empty history reads as success**), `market.py:53` (`except` → **unauthenticated public fallback**). `routers/exchange.py` verified **correct — not to be changed.** The same kwarg is **valid** at `risk.py:313,325,407` and `paper_trading.py` ×8; a blanket sweep would break paper trading. `market.py:56` no-keys/partial-key guard must be preserved. The anonymous-fallback contract change needs a **product/security decision**. | **NOT GRANTED** |
| **F1 · D7-2** | `portfolio_cache_updater.py:151` imports nonexistent `backend_app.backend.key_manager.KeyVault`; the call at `:159` already uses the correct 2-arg form. Replacement is **not a path substitution** — class name, constructor and the `MASTER_ENCRYPTION_KEYS` contract must be verified, and the updater may need the initialized `app_state` vault rather than a fresh instance. **No second credential manager will be created.** `/health/services` reports only four services and **excludes** the updater, so overall `status` can read `ok` while the loop fails every cycle. Health redesign is a **separate scope**. | **NOT GRANTED** |
| **F2 · D6-D** | `core/state.py:67` `safe_import("APIKeyVault", "api_key_vault")` — bare name, unresolvable under the repo-root path and `pytest.ini`'s `pythonpath`; `safe_import` then yields `MockEngine`. **This is an import-path finding, not proof production runs a mock.** Redacted platform evidence (2 booleans, type name, `sys.path` membership, per environment) **NOT SUPPLIED**. | **NOT GRANTED** |
| **G · W-6** | Matrix **10 of 20 asserted misclassified** (inputs hash-unchanged → REUSED): transport errors shadowed by `symbol`/`market` in URLs (5/5 venue paths); `"key"` shadowing 8/8 ordinary words; genuine **signature** auth failure → generic; ccxt `RateLimitExceeded` missed (test needs `"rate limit"` **with a space**). Redaction intact at `:1236`. `retryable` re-measured **log-only** — 15 production occurrences, **0 control flow**. Default-lane coverage **zero** (sandbox skips 16/16). **No retry behaviour introduced.** | **NOT GRANTED** |
| **H · tenant/RLS** | `apply_tenant_context` has **zero external consumers** — revalidated. Three conflicting identity expressions (`dependencies.py:318`, `orders.py:819/940`, `distributed_execution.py:296`) agree only because no token carries a `tenant_id` claim. Two permissively **OR-combined** policy families; `execution_records` predicate disagreement unresolved. FORCE experiment REUSED: ENABLE+unset GUC → 2 rows; **ENABLE+FORCE+unset GUC → 0 rows, silently**; FORCE+correct GUC → 1 row. | **NOT CERTIFIED** |
| **H · Redis** | `AERORA_TEST_REDIS_URL` unset, 6379 closed, `redis-server` absent, Docker absent. 5 tests **SKIP** — *"A skip here is NOT a pass."* Gate correctly separates unset→SKIP / configured-but-broken→**FAIL** / reachable→RUN. **Strict XFAIL preserved.** | **SKIPPED, not passed** |
| **H · D6-C** | Query validity **ESTABLISHED** (original fails SQLSTATE `42883`); fixtures **7/7**, counts-only, all integers; **production counts NOT SUPPLIED**; token-claim divergence **NOT SUPPLIED** (unanswerable from application tables). | **EVIDENCE NOT SUPPLIED** |
| **I · W-3/4/5** | `UNKNOWN` has **no authorized exit policy** — recovery CAS pinned `AND upper(status::text) = 'EXECUTING'` (`execution_record.py:1488`), so `UNKNOWN` rows are unreachable. **W-5 blocked behind W-4; W-3 blocked behind W-6.** `venue_order_id` stays an external identifier — migration `030`'s postflight aborts if an FK is added. No semantics invented. | **NOT GRANTED** |
| **I · migrations** | Baseline **not recaptured** — `AP-W1D-4` NOT GRANTED at ledger `:803`. Snapshot must not be regenerated to force green. | **NOT GRANTED** |

> **FORCE RLS was not enabled and no FORCE migration was prepared for execution.** A local
> superuser run must never be read as evidence that FORCE is safe: local `postgres` is
> `rolsuper`/`rolbypassrls` and bypasses RLS unconditionally, returning a false "safe".
> Supabase's `postgres` is not a superuser. **Test-cluster and production evidence are kept
> separate throughout.**

---

## 9. Tests executed this pass

**No pytest run.** No implementation was authorized, so there was nothing new to validate, and
the brief forbids re-running diagnostics whose source, tool versions and environment are
unchanged. Prior counts are cited as prior evidence and **not re-presented as this phase's
results**:

| Prior run | Result | Reuse basis |
|---|---|---|
| `tests/test_bandit_sast_gate_is_green.py` | 3 passed, 96.75s | command + source unchanged |
| `staging/phase17_test_frontend_release_gate.py` | 42 passed, 7.17s | **prototype — not in `testpaths`** |
| W-6 matrix | 21 cases, 10/20 misclassified | `exchange_executor.py` hash unchanged |
| Execution core (Phase 12) | 179 passed / 5 skipped / 0 failed | inputs unchanged |
| D6-C fixtures | 7/7, 0 mismatches | query + fixtures unchanged |
| RLS FORCE experiment | 2 → 0 → 1 rows | source unchanged |

**Tooling measured:** Trivy **absent**; `bandit` present in `.venv`; `gh` present; Docker
**absent**; Redis **absent**; PostgreSQL reachable on **51815** (shared DSN says 51805).

---

## 10. Remaining owner decisions and minimum approvals

| # | Blocker | Owner | Minimum action |
|---|---|---|---|
| 1 | **Keep FORCE RLS blocked** | — | **No action.** Acting is the only way to make this worse. |
| 2 | Redis SG name collision (§6.3) | **Infra owner** | Decide which root module owns `vyomquant-redis-sg-*` |
| 3 | Trivy dispositions (5) | **Security owner** | Accept `AWS-0054` + 2 conventional egress; decide the 2 Redis rules |
| 4 | Orphaned `stash@{1}` (§2.2) | **Code owner** | Decide: preserve, review, or discard 18 files of pre-rewrite WIP |
| 5 | `D-P15-4` frontend gate | **Workflow/release owner** | One grant → smallest fail-closed `needs:` + exact-SHA pin |
| 6 | `D-P15-3` correctness route | **Release owner** | Decide PR-gated vs main-branch workflow |
| 7 | `02-build.yml` `:latest` (§5.2) | **Release owner** | Decide whether unvalidated images may claim `:latest` |
| 8 | D7-1 | **Code owner** | One grant → 3 line deletions + signature-binding test |
| 9 | W-6 | **Code owner** | One grant; must precede W-3 |
| 10 | Bandit parity test | **Code owner** | One grant → 1 test + negative control |
| 11 | Tenant identity contract | **Code owner** | **Choose one of three expressions.** Costs no code; blocks all wiring |
| 12 | D6-C production counts | **Production DB owner** | **3 integers**, counts-only |
| 13 | D6-D boot evidence | **Platform owner** | **4 redacted fields × 2 environments** |
| 14 | Token-claim divergence | **Auth/platform owner** | **1 integer** |
| 15 | `UNKNOWN` exit policy | **Execution-state owner** | Define + test before W-5 |
| 16 | Isolated Redis | **Coordinator/infra** | One grant + an instance |

---

## 11. Final diff review and prohibited-operation confirmation

**Diff review: there is no diff.** `git write-tree` = `4b45a7633f76cd8ab5be023c1e45af7f1a63c398`
at entry **and** exit, identical to Phase 17's recorded value. `git diff HEAD --name-only`
returns the same two concurrent files at entry and exit, neither touched by this phase.

| Prohibited | Performed? |
|---|---|
| Stage, commit, push, merge, reset, stash, clean, revert, rewrite history | **No** — tree hash identical; 0 staged; **2 stashes untouched** |
| Dispatch CI / open pull requests | **No** |
| Deploy, roll back, purge CloudFront, change DNS | **No** |
| Apply Terraform / provision infrastructure | **No** |
| Mutate production databases | **No** — production never contacted |
| **Enable FORCE RLS** | **No** |
| Start or alter shared services | **No** — Redis not started; PostgreSQL not restarted; `pg_dsn.txt` not edited |
| Read or disclose production credentials | **No** — no token, secret, DSN, exchange key or decrypted material read or printed |
| Place live orders / enable live trading | **No** |
| Weaken security thresholds or conceal findings | **No** — 0 suppressions, 0 threshold changes, all 9 Bandit and 5 Trivy findings reported |
| Overwrite concurrent artifacts or another session's report | **No** — destination verified absent; all Phase 17–20 artifacts read-only |
| Claim a skipped or unexecuted test passed | **No** — 5 Redis tests reported as skips; Trivy explicitly **not run**; no pytest run claimed |
| Claim tenant isolation certified | **No** — **NOT CERTIFIED** |

---

## 12. Completion status per lane

| Lane | Outcome |
|---|---|
| A frontend gate | **Blocked** — `D-P15-4` not granted. Revalidated: `needs:` = 0. |
| B correctness | **Blocked** — `D-P15-3` undecided. **New: `:latest` push verified.** |
| C Bandit | **Blocked** — not granted. **All 9 findings individually dispositioned.** |
| D Trivy | **Blocked** — not granted. **Count discrepancy RESOLVED (4); 2 Phase 17 claims retracted; SG name collision found.** |
| E D7-1 | **Blocked** — not granted. Revalidated. |
| F D7-2 / D6-D | **Blocked** — not granted. Revalidated; scopes kept separate. |
| G W-6 | **Blocked** — not granted. Reused on hash check. |
| H tenant/Redis/PG | **Blocked / NOT CERTIFIED.** Skips preserved as skips. |
| I migrations/workers | **Blocked** — `AP-W1D-3` out of scope; `AP-W1D-4` not granted. |

**0 of 9 lanes authorized. 0 files changed. 4 new or corrected measurements delivered.**

---

*Phase 21 closed. Tree hash unchanged at entry and exit. Two stashes untouched.*

***DO NOT RELEASE · LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED ·
DEPLOYMENT: BLOCKED · PRODUCTION EVIDENCE: NOT SUPPLIED.***
