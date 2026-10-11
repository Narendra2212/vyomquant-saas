# Phase 16 — Frontend Release Gate, Current-SHA Verification, Security Gate Closure

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Does **not** overwrite the concurrent
`staging/PHASE16_SECURITY_RELEASE_AND_CRITICAL_CLOSURE.md` (37,112 B, 20:16:52), which is a different
session's report at a different path.

> ## FINAL DISPOSITION: **BLOCKED**
>
> **Release recommendation: DO NOT RELEASE.**
>
> **0 files modified** — no grant exists for any source, test, workflow, Terraform, migration or
> governance change. Every remediation below is a patch plan with an owner request.
>
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED**

**What this phase adds that no prior report contains:**

1. **The actual gate design, with the two GitHub-Actions semantics traps that make the obvious
   implementation unsound** — verified against official documentation, not assumed (§3).
2. **A reusability analysis of `scripts/require_security_scan.py`**, the file the brief asks the
   gate to reuse. No prior phase opened it. It is reusable, with three concrete gaps — one of
   which is a correctness hole for gating on `01 PR Check` (§3.3).
3. **D7-1 re-derived at current code, and it is worse than recorded**: authenticated market data
   fails for **every user, on every request**, and silently degrades to anonymous public data (§6).

---

## 1. Preflight baseline and preservation manifest

`VER` All values measured this phase. Nothing reused unverified.

| Measurement | Value |
|---|---|
| toplevel / branch | `C:/aerora_quant_backend_updated_final1` / `main` |
| **HEAD (release candidate)** | **`ecd23a9e934ec58a7b1f0e5db638da8e865e8187`** — *"launch phase 1"* |
| parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| upstream / `origin/main` / ahead-behind | `origin/main` / `ecd23a9e…` / **0 / 0** |
| worktrees | **1** — `C:/aerora_quant_backend_updated_final1 ecd23a9e [main]` |
| staged / unstaged / untracked / unmerged | **0 / 2 / 73 / 0** |
| tracked index entries | **5705** |
| index digest (A14 method) | `c6b0342488a551b814838431a33347aa9f4fe37cbfa73bad15e16139e5d3e142` |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** — identical at entry and exit |
| stash entries | **2** — untouched |

`git write-tree` was run because the brief requires it. It writes only unreferenced tree objects;
it does not mutate the index, HEAD, refs or the worktree.

### 1.1 Preservation manifest — pre-existing modified files, NOT touched

| Path | HEAD blob | Worktree blob | numstat | Owner |
|---|---|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `6587dd3e88ae…` | `0c9981b2817e47b4faa915b69de204ff78f87af7` | **+2 / −1** | another session — **actively editing** |
| `tests/test_drift_classification.py` | `fcffbe8e0136…` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` | **+143 / −0** | A29 (ledger test 6), unstaged |

Both **byte-identical at entry and exit** `VER`. The A23E11 delta file changed since Phase 15
(it was previously a different blob), confirming a concurrent session is live in it — so it was
not read for mutation and not edited.

### 1.2 Release-path files — all SAME as Phase 15, so prior analysis remains valid

| Path | Blob | vs P15 |
|---|---|---|
| `.github/workflows/01-pr-check.yml` | `91d55a379115c88edd2fd6be01871b82a75ef498` | **SAME** |
| `.github/workflows/03-deploy.yml` | `eb8078eac06268447d60f26ef7a97a537ceed988` | **SAME** |
| `.github/workflows/05-security.yml` | `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` | **SAME** |
| `.github/workflows/06-frontend-deploy.yml` | `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` | **SAME** |
| `scripts/require_security_scan.py` | `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` | newly fingerprinted |
| `tests/test_bandit_sast_gate_is_green.py` | `a7deff99c9e54beee70c115607d538a638561b87` | **SAME** |
| 3 × B608 Alembic migrations | `5e9f85ce…`, `ac12c2d9…`, `bb9b6298…` | **SAME** |
| `terraform/alb.tf` / `redis.tf` / `security_groups.tf` | `cdead928…` / `9afb7c35…` / `87c9d131…` | newly fingerprinted |
| `backend_app/routers/market.py` | `16f0377cb9f6c85ef0fb21ce3df37940bd27628e` | newly fingerprinted |
| `backend_app/backend/api_key_vault.py` | `74a0059411467442b6aaa3d11d0d89af4d00fe65` | newly fingerprinted |

**Correction to the brief's assumption:** `backend_app/core/api_key_vault.py` does **not exist**.
The vault is `backend_app/backend/api_key_vault.py` `VER`.

### 1.3 Concurrent reports — collision avoided

`VER` Newer than Phase 15: `PHASE11B_A23E15_TENANT_CACHE_AND_PARALLEL_REMEDIATION_READINESS.md`
(43,453 B), `PHASE11B_A23E17_EXECUTED_EVIDENCE_AND_W6_MEASUREMENT.md` (40,892 B),
**`PHASE16_SECURITY_RELEASE_AND_CRITICAL_CLOSURE.md`** (37,112 B),
`PHASE15_SECURITY_GATE_CLOSURE_AND_MAIN_VERIFICATION.md` (24,262 B).

Multiple sessions are executing the same Phase 15/16 briefs at different destinations. **My
assigned path was free and is used.** Where a concurrent report already produced `VER` evidence
on identical hashes I **cite rather than re-run**, per the brief's §9 instruction.

---

## 2. Authorization gate — no grant exists

| Workstream | Files it would touch | Grant | Source |
|---|---|---|---|
| A — frontend gate | `06-frontend-deploy.yml`, `scripts/` | **ABSENT** | no owner row anywhere |
| B — correctness trigger | `01-pr-check.yml` | **ABSENT** | shared workflow, A3-01 flags single-owner need |
| C — Bandit | 3 migrations + gate test | **ABSENT** | A23E14/E17, concurrent P16 §3.4 |
| D — Trivy | `security_groups.tf`, `alb.tf`, `redis-only/main.tf` | **ABSENT** | infra owner unidentified |
| E — D7-1 / W-6 / D7-2 / workers | routers, executor, cache updater | **ABSENT** | A23E17 §2 |
| F — PG / tenant / Redis | n/a (environment, not code) | **ABSENT** + no environment | A23E17 §3.6 |

The only live grant in the engagement remains **`AP-W1D-3`** (W1D-12 §1, Code owner) over four
classifier files, already fully consumed at A29. **Nothing in this brief is treated as a grant** —
it is phrased conditionally throughout ("If authorized…", "If no valid grant exists, do not make
the modification").

---

## 3. Workstream A — the frontend release gate

### 3.1 Current state revalidated

`VER` `06-frontend-deploy.yml` blob **SAME as P15**, so the Phase 15 finding stands and the
concurrent P16 report's enumeration (615 lines; `needs:` **0**; `workflow_run` **0**; no real
gate reference — the two textual `conclusion` matches are inside `echo` strings) is adopted on an
unchanged hash.

```
BEFORE (current, verified)

  push to main ─┬─> 02 Build ──workflow_run──> 03 Deploy
                │                               ├─ require_security_scan.py  ?head_sha=<SHA>
                │                               └─ ECS deploy  [needs: gate]      ← FAIL-CLOSED
                │
                ├─> 05 Security ──> (red on ecd23a9e)   ── gates nothing for frontend
                │
                └─> 06 Frontend Deploy ──> S3 + CloudFront   ← NO GATE. PUBLISHED.

      01 PR Check : pull_request ONLY → never ran on ecd23a9e
```

### 3.2 Two semantics traps that make the obvious design unsound

The brief instructs me to inspect GitHub Actions event semantics before choosing a trigger. I did,
against the official documentation — and the naive design is wrong in two independent ways.

**Trap 1 — `workflow_run` checks out the WRONG COMMIT by default.** The official event reference
states that for `workflow_run`, `GITHUB_SHA` is the **last commit on the default branch** and
`GITHUB_REF` is the **default branch** — *not* the triggering commit.
([Events that trigger workflows](https://docs.github.com/en/actions/reference/events-that-trigger-workflows))
`actions/checkout` documents the same: it defaults to the ref/SHA that triggered the workflow, and
**otherwise the default branch**
([actions/checkout README](https://github.com/actions/checkout/blob/main/README.md)).

> So a `workflow_run`-triggered frontend deploy with a bare `actions/checkout` would **validate
> one SHA and publish another** whenever `main` advanced in between. That silently breaks
> requirement A2.7 (artifact provenance) while *appearing* gated. The build must pin
> `ref: ${{ github.event.workflow_run.head_sha }}`, and the deployed artifact must carry the same
> SHA.

**Trap 2 — `workflows: [A, B]` means OR, not AND.** The same reference states that if multiple
workflows are specified, **only one of them needs to run** to fire the event.

> So `workflows: ["01 PR Check", "05 Security"]` does **not** mean "both passed". It means "fire
> when either finishes". The AND must be enforced **inside the job** by querying the API per
> workflow — which is exactly what `require_security_scan.py` already does, and precisely why
> extending it beats writing a second checker.

Three further documented facts that shape the design:

- *"This event will only trigger a workflow run if the workflow file exists on the default
  branch."* → the gate cannot be tested from a feature branch alone.
- *"A workflow run is triggered regardless of the conclusion of the previous workflow."* → an
  explicit `if: github.event.workflow_run.conclusion == 'success'` is mandatory.
- *"The workflow started by the `workflow_run` event is able to access secrets and write tokens,
  even if the previous workflow was not"*, with an explicit warning about cache poisoning and
  unintended secret access. → material here, because the frontend lane holds AWS credentials.
  The gate must not check out or execute untrusted upstream content.
- `workflow_run` cannot chain more than **three levels**. Current depth is `02 Build → 03 Deploy`
  (2). Adding the frontend as a *parallel* consumer stays within the limit.

*Content from the cited sources was paraphrased for compliance with licensing restrictions.*

### 3.3 `scripts/require_security_scan.py` — reusability assessment

`VER` Read in full this phase (blob `a1c8dd2d…`). **No prior phase opened it.** It already
satisfies most of the brief's gate requirements:

| Requirement | Met? | Mechanism |
|---|---|---|
| A2.1 exact-SHA scoping | **YES** | `/repos/{repo}/actions/runs?head_sha={sha}&per_page=100`, filtered by workflow name |
| A2.4 no stale green from another SHA | **YES** | structurally impossible — the query is SHA-keyed |
| A2.3 missing run fails closed | **YES** | *"Nobody scanned this commit, which is not evidence that it is safe."* |
| cancelled / skipped fail closed | **YES** | explicit `conclusion in ("cancelled","skipped") → 1` |
| still-running fails closed | **YES** | waits `SECURITY_GATE_WAIT_SECONDS` (900), then refuses |
| API error fails closed | **YES** | `HTTPError → return 1`, *"refusing rather than guessing"* |
| missing env fails closed | **YES** | token/repo/SHA all required |
| A2.6 manual control, not silent bypass | **YES** | `ALLOW_UNSCANNED`, *"recorded here on purpose"* |

**Three gaps that must be closed before it can gate the frontend:**

| # | Gap | Consequence | Fix |
|---|---|---|---|
| **G1** | `WORKFLOW_NAME = "05 Security"` is a **module constant** | cannot require `01 PR Check` too | parameterise to a list via env, **defaulting to `"05 Security"`** so backend behaviour is bit-for-bit unchanged |
| **G2** | Gates on the **run-level** `conclusion` only | **a run whose jobs all SKIP concludes `success`.** For `01 PR Check`, a path-filtered or conditional test job that skips would satisfy a run-level gate while testing nothing | additionally require named **jobs** to have `conclusion == "success"` via `/actions/runs/{id}/jobs` |
| **G3** | `per_page=100`, no pagination loop | bounded, implausible for one SHA, but unhandled | add a `page` loop or assert `total_count <= 100` |

**G2 is the important one** — it is the same class of error as the Bandit scope divergence: a gate
that looks green because it is asking a question too coarse to see the failure.

### 3.4 Patch plan — NOT applied (no grant)

| # | Change | Risk |
|---|---|---|
| **P16-A1** | Extend `require_security_scan.py` per **G1/G2/G3**, default-preserving | low; backend path unchanged by default |
| **P16-A2** | Add a `frontend-release-gate` job to `06-frontend-deploy.yml`: trigger `workflow_run` on `["02 Build"]` (the existing `push` consumer), `if: conclusion == 'success'`, run the extended verifier for **both** `05 Security` and `01 PR Check` against `workflow_run.head_sha`, and `needs:` it from every publishing job | medium — workflow semantics; needs the §3.2 pins |
| **P16-A3** | Pin every build/publish step to `ref: ${{ github.event.workflow_run.head_sha }}`; keep `workflow_dispatch` with an explicit, logged override input mirroring `allow_unscanned` | medium |
| **P16-A4** | Declare `permissions: { actions: read, contents: read }` on the gate job | low — **without `actions: read` the API returns 403 and the gate fails closed permanently**, as `03-deploy.yml`'s own comment records |

**Deliberately not proposed:** cross-workflow `needs:` (not expressible), a second security
checker, or `paths:`-filtered gating (a frontend-only commit must still be validated).

### 3.5 Workstream A3 — gate test matrix, designed not executed

No grant, so no test file was created. The matrix the gate must satisfy, all mockable with no
production secrets:

| Case | Required outcome |
|---|---|
| both workflows success on the exact SHA | **PASS** |
| success recorded only on an **older** SHA | **FAIL** (stale evidence) |
| `01 PR Check` run missing entirely | **FAIL** |
| `01 PR Check` present, run `success`, **test job skipped** | **FAIL** — the G2 case |
| either workflow failed / cancelled / still running | **FAIL** |
| SHA mismatch between gate and publish step | **FAIL** |
| unexpected branch or event | **FAIL** |
| manual dispatch without evidence and without the override | **FAIL** |
| API 403 / 5xx / malformed JSON | **FAIL** |
| >100 runs for one SHA (pagination) | **FAIL or correctly paged**, never silently pass |

A mocked pass here demonstrates **gate logic**, not production release safety. That distinction is
preserved.

---

## 4. Workstream B — correctness evidence for the candidate

`VER` `01-pr-check.yml` unchanged: `on: pull_request: branches:[main, master], types:[opened,
synchronize, reopened]`. **No `push`, no `workflow_dispatch`.** `provisioning-and-isolation` is
defined inside it.

**Same-SHA provenance for `ecd23a9e`** (`VER` from the run list; no run invented):

| Workflow | Run ID | Event | SHA | Conclusion | Valid for candidate? |
|---|---|---|---|---|---|
| 02 Build | 38129699471 | push | `ecd23a9e` | success | yes — but builds only |
| 05 Security | 38129699454 | push | `ecd23a9e` | **failure** | yes |
| 05 Security | 38132697285 | schedule | `ecd23a9e` | **failure** | yes — reproducible |
| 03 Deploy | 38130273012 | workflow_run | `ecd23a9e` | **failure** at gate; ECS **skipped** | yes |
| 06 Frontend Deploy | 38129699497 | push | `ecd23a9e` | **success — PUBLISHED** | yes |
| 07 Desktop Build | 38129699516 | push | `ecd23a9e` | success | yes |
| **01 PR Check** | **—** | — | — | **NEVER RAN** | **no evidence exists** |

Unit/integration, PostgreSQL, Redis, schema-drift, tenant-isolation/RLS and ORM-conformance jobs:
**none executed on the candidate.** The last `01 PR Check` was 38061973739 on `f5b99e57`, a PR
head — **not** this candidate. A successful build or frontend job is not correctness evidence.

**Structural blocker, exactly stated:** `01 PR Check` has no `push` and no `workflow_dispatch`
trigger, so there is **no supported route** to obtain correctness evidence for a commit that
reached `main` by direct push — without either a workflow change or a PR. I opened no PR, created
no branch, and changed no trigger.

> **P16-B1 (preferred, zero workflow change):** route release-bound changes through pull requests.
> **P16-B2 (if direct pushes must remain):** add `push: branches: [main]` **plus**
> `workflow_dispatch` (the latter also makes the candidate re-testable on demand). Assess first:
> duplicate cost against the `pull_request` run, a `concurrency` group to cancel superseded runs,
> and that the PostgreSQL/Redis services still provision on the `push` event.

---

## 5. Workstreams C and D — cited, not duplicated

All relevant hashes are **SAME as Phase 15** `VER`, and two concurrent reports already produced
executed evidence. Per the brief I did not re-run them.

**C — Bandit.** 9 × B608, all MEDIUM, 0 HIGH, `bandit 1.9.4`, exit 1, `nosec` applied to **0** of
the sites. All nine interpolate **validated identifiers** (`^[a-zA-Z0-9_]{1,64}$`, raises, never
sanitises) with the only user-influenced value (`UUID_REGEX`) **parameter-bound**. Identifiers
cannot be bound parameters, so validated interpolation is the correct construction. The structural
cause stands: the one-file Bandit test proves silence on one module; the AST test proves *safety*
tree-wide but not Bandit's *verdict* — **so the suite is green while the gate is red and cannot
see it.** Remedy requires both the nine annotations **and** widening the gate test. `REC` Phase 14
§3.3-3.4, concurrent P16 §3.3-3.4. **Grant ABSENT.**

**D — Trivy.** Five blocking sites, not four — the concurrent P16 report found a fifth:

| Rule | Resource | Site | Disposition |
|---|---|---|---|
| `AWS-0054` | `aws_lb_listener.http` | `alb.tf:52` | **false positive** — `lifecycle.precondition` refuses the prod apply; Trivy cannot evaluate `dynamic` blocks or preconditions |
| `AWS-0104` | `aws_security_group.alb` | `security_groups.tf:26` | real, conventional — accept + document |
| `AWS-0104` | `aws_security_group.ecs_tasks` | `security_groups.tf:56` | real, **load-bearing** (ECR, Secrets Manager, venues) — accept + document |
| `AWS-0104` | `aws_security_group.redis` | `security_groups.tf:77` | **real and unnecessary — safe to tighten** |
| `AWS-0104` | `aws_security_group.redis` | `redis-only/main.tf:62` | **real and unnecessary — safe to tighten** |

**The gate stays RED** even if both Redis egress rules are tightened, because `AWS-0054` and the
two conventional `AWS-0104` findings still trip a CRITICAL blocking step. Removing two findings is
not gate closure. Trivy is not installed locally and docker is absent, so I ran no scan and
confirmed nothing new; infrastructure was not touched. **Grant ABSENT.**

---

## 6. Workstream E1 — D7-1 re-derived, and it is worse than recorded

`VER` At current code, not historical line numbers.

**Production signature** — `backend_app/backend/api_key_vault.py:177`:

```python
def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:
```

**Two parameters. No `access_token`.** Call site — `backend_app/routers/market.py:49-54`:

```python
try:
    keys = vault.load_decrypted_keys(
        user["id"],
        exchange_id,
        access_token=user.get("access_token"),   # ← no such parameter
    )
    ...
except Exception:
    # Public fallback — market data doesn't need auth
    bridge = ConnectionEngine(exchange_id=exchange_id)
    exchange = await bridge.connect()
    return DataEngine(exchange)
```

> **The call raises `TypeError` unconditionally — for every user, on every request — and the bare
> `except Exception` silently swallows it and returns an *unauthenticated public* connection.**
>
> So authenticated market data has **never worked**, and the failure is indistinguishable from
> "this user has no keys". This is exactly the condition the brief names: an authentication or
> vault error must not silently return anonymous market data. It does.

Same pattern at `routers/orders.py:789` and `:1095`, and `routers/exchange.py:993`. Correct
two-argument callers exist at `master_executor.py:252`, `api_ws/ws_routes.py:814`,
`portfolio_cache_updater.py:160`.

**Why the suite never caught it** `VER` — the test doubles disagree with production:

| Test double | Signature | Effect |
|---|---|---|
| `tests/test_market_none_keys_fix.py:13, :19` | `(self, user_id, exchange_id, access_token=None)` | **accepts the invalid kwarg → masks the defect** |
| `tests/test_no_credential_in_api_response_body.py:351` | `(self, user_id: str, exchange_id: str)` | matches production |
| `tests/test_live_deployment_mode_propagation.py:165` | `(self, user_id, exchange_id)` | matches production |
| `tests/test_paper01_paper_live_isolation.py:244` | `(self, *args, **kwargs)` | permissive |

The one test file written *about* this code path is the one whose mock makes the bug invisible.

> **P16-E1 (not applied — grant ABSENT).** Delete the three invalid `access_token=` arguments;
> align `test_market_none_keys_fix.py`'s doubles to the real two-parameter signature; add a
> signature-binding regression test using `inspect.signature` so a double can never again accept a
> parameter production rejects; and **narrow `market.py`'s bare `except Exception`** so a vault or
> auth error is distinguishable from "no keys" rather than silently anonymising. Files:
> `routers/market.py`, `routers/orders.py`, `routers/exchange.py`, `tests/test_market_none_keys_fix.py`.
> **Out of scope** unless separately authorized: the policy question of what market data *should*
> do when keys are absent.

### 6.1 W-6, D7-2, workers — cited

`REC` A23E17: **W-6 TESTED** — 3 of 8 classifications wrong, *measured* (first execution in the
repo), plus a third shadowing defect at `exchange_executor.py:1225-1257`; `exchange_executor.py`
unchanged at `df4fd42a…` `VER`. D7-2 and worker lifecycle: no grant, no new evidence; the
reconciliation service remains unwired (`main.py` unchanged at `a3849d4a…` `VER`), and I did not
implement W-5 recovery, which the brief correctly gates behind the unresolved W-4/UNKNOWN-exit
decision.

---

## 7. Workstream F — PostgreSQL, tenant isolation, Redis

`REC` A23E17 §3.6, consistent with my own environment checks: Redis `127.0.0.1:6379` **closed**,
**0** `REDIS*`/`AERORA_TEST_REDIS_URL` variables, **docker absent**, PostgreSQL `127.0.0.1:5432`
**closed**. `staging/pg_dsn.txt` exists, is gitignored, and **was not opened**.

- **33** tracked `tests/*postgres*.py` files, **0 executed on the candidate**.
- QUOTA-01 is TESTED on the in-process substrate only — **real-Redis UNEXECUTED**. A mocked or
  in-process quota test does not establish real-Redis correctness.
- **TENANT ISOLATION: NOT CERTIFIED.** Static policy inspection cannot certify it, and
  `FORCE ROW LEVEL SECURITY` was not enabled — correctly, since tenant context remains unverified.

No DSN was opened, no database or Redis connected, no credential read. **This is an environment
blocker, not a decision blocker:** it resolves either via the authorized CI path (which needs
**P16-B1/B2** first) or a provisioned disposable non-production pair.

---

## 8. Tests executed this phase

| Command | Exit | Result |
|---|---|---|
| `python staging/phase16_preflight.py` | 0 | baseline, manifest, hashes, `write-tree` |
| `git write-tree` (×2, entry and exit) | 0 | `4b45a763…` both times |

**No pytest run, no Bandit run, no Trivy run, no CI dispatch.** Deliberate: every governing hash
is byte-identical to Phase 15 and to the concurrent reports that executed these suites, and the
brief instructs me not to re-run completed tests merely to duplicate evidence when source hashes
and environment assumptions are demonstrably identical. The results I rely on are tagged `REC`
with their source; the results I measured are tagged `VER`.

Artifacts created, all new, all uniquely named:

```
staging/PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md   this report
staging/phase16_preflight.py / phase16_preflight.txt        baseline + preservation manifest
```

---

## 9. Blocked workstreams and the exact missing authorization

| # | Workstream | Missing | Risk |
|---|---|---|---|
| **1** | **A — frontend gate** (P16-A1…A4) | workflow-owner grant for `06-frontend-deploy.yml` + `scripts/require_security_scan.py` | **CRITICAL** — a lane that publishes to production with no validation, and already did |
| **2** | **B — correctness route** (P16-B1/B2) | decision: PRs-only, **or** a grant for `01-pr-check.yml` | **CRITICAL** — no evidence is obtainable for any shipped commit until resolved |
| **3** | **E1 — D7-1** (P16-E1) | grant for 3 routers + 1 test file | **HIGH** — authenticated market data silently anonymous for all users |
| 4 | C — Bandit (annotations **+** gate-test widening) | grant for 3 migrations + gate test | HIGH — blocks every backend deploy |
| 5 | D — Trivy (2 narrow Redis tightenings + 3 documented accepts) | infrastructure-owner grant | HIGH — gate stays RED regardless |
| 6 | E2 — W-6 | own grant | MEDIUM — measured 3/8 wrong |
| 7 | F — PG / tenant / Redis | environment via #2, or a provisioned pair | HIGH — tenant isolation uncertifiable |
| 8 | A23 Redis, W1C-02 scope, D5, D-A27-1 | carried forward | MEDIUM |

### Highest-value next action

> **Grant P16-B1 or P16-B2 first.** Everything else is unverifiable without it: the frontend gate
> (#1) is designed to require `01 PR Check`, and a gate cannot require a workflow that can never
> run on the commit being gated. Routing releases through PRs costs one process change and zero
> workflow edits.
>
> Then **P16-A1…A4** — and note §3.2: implement them with the `head_sha` pin and the in-job AND,
> or the gate will look green while validating a different commit.

---

## 10. Final status and completion check

| Dimension | Status |
|---|---|
| Source correctness | **UNVERIFIED on `ecd23a9e`** — 0 correctness jobs ever ran on it |
| Security gate | **RED** — Bandit 9 × B608; Trivy 5 blocking sites |
| Test coverage | local/historical only; **0** CI-executed on the candidate; 33 PG files at 0 |
| Deployment | backend **NOT deployed** (gate held); **frontend PUBLISHED, ungated** |
| Tenant certification | **NOT CERTIFIED** |
| Live trading | **OFF / NOT CERTIFIED** |
| Files modified this phase | **0** source · **0** test · **0** workflow · **0** Terraform · **0** governance |
| Approvals created | **0** |
| **Disposition** | **BLOCKED** |

`VER` Exit state identical to entry: `HEAD` `ecd23a9e…`, `origin/main` identical, 0/0,
`write-tree` `4b45a763…`, staged **0**, unmerged **0**, tracked 5705, stash 2; both pre-existing
modified files byte-identical; every release-path, migration, Terraform and router file unchanged.

No git write command was issued (no add, commit, push, merge, rebase, reset, stash, clean, revert,
branch change). No deploy, rollback, CloudFront purge, DNS change, Terraform apply, database
mutation, exchange-credential access or live-trading enablement. No CI run was dispatched,
fabricated, or claimed to apply to a SHA it did not test. No report, log or concurrent artifact was
overwritten.

**Release rule unmet on all three counts:** required evidence does not exist for the exact
candidate; the frontend gate does not enforce the policy; and the authorized release process has
not permitted deployment. **DO NOT RELEASE.**
