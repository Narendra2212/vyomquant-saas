# Phase 17 — Release Gate Closure, Correctness Evidence, Critical Defect Remediation

**Date:** 2026-10-11 · **Destination verified ABSENT before writing.** Overwrites nothing; does not touch the concurrent
`PHASE17_D_P15_4_RELEASE_GATE_AND_PARALLEL_CLOSURE.md`, `PHASE18_*`, `PHASE19_*` or either Phase 16 report.

> ## DISPOSITION: **BLOCKED** · **DO NOT RELEASE**
> **0 files modified.** No grant exists for any source, test, workflow, Terraform, migration or governance change.
>
> **LIVE TRADING: OFF / NOT CERTIFIED · TENANT ISOLATION: NOT CERTIFIED**
> **Backend deployment: BLOCKED · Frontend deployment: BLOCKED · Production infrastructure changes: NOT AUTHORIZED**

This phase is **differential**. It does not restate Phases 13–16. It contains three source-verified
results that did not previously exist, and **one correction to my own Phase 16 report**.

| # | New this phase | Status |
|---|---|---|
| 1 | **D7-1 call-site census corrected** — 3 invalid sites, not 4. `exchange.py` is **clean** | **VERIFIED** |
| 2 | **D7-2 confirmed by executed import** — the module does not exist *and* the class name is wrong | **VERIFIED** |
| 3 | **Trivy `AWS-0104` Redis disposition proven from source** — differential, not assumed | **VERIFIED** |

---

## 1. Entry and exit manifests

`VER` Identical at entry and exit. Nothing I did altered the repository.

| Measurement | Entry | Exit |
|---|---|---|
| `HEAD` / `origin/main` / divergence | `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` / identical / **0 / 0** | identical |
| **`git write-tree`** | **`4b45a7633f76cd8ab5be023c1e45af7f1a63c398`** | **identical** |
| staged / unstaged / unmerged | **0 / 2 / 0** | **0 / 2 / 0** |
| untracked | 82 | 82+ (this report and its probes) |
| stash / worktrees | **2** / **1** | **2** / **1** |

### 1.1 Preservation — pre-existing modifications, byte-identical

| Path | Blob (entry **and** exit) | Owner |
|---|---|---|
| `staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` | `0c9981b2817e47b4faa915b69de204ff78f87af7` | another session |
| `tests/test_drift_classification.py` | `99a2917ac0a65761ef3dfe9299ed1df87647552a` | A29 ledger test 6 |

### 1.2 Target files — all confirmed untouched by **anyone**, at entry and exit

`VER` Re-measured this phase, not reused:

| Path | Blob |
|---|---|
| `.github/workflows/01-pr-check.yml` | `91d55a379115c88edd2fd6be01871b82a75ef498` |
| `.github/workflows/03-deploy.yml` | `eb8078eac06268447d60f26ef7a97a537ceed988` |
| `.github/workflows/05-security.yml` | `e2d9ccc6f951d39d2189b924cc2d3efa2ce207fe` |
| `.github/workflows/06-frontend-deploy.yml` | `285cc2d19b2b77ac9ea3e572ebe849a2ca91fde9` |
| `scripts/require_security_scan.py` | `a1c8dd2d48fbc744301e95fc99429e4e1efd35da` |
| `tests/test_bandit_sast_gate_is_green.py` | `a7deff99c9e54beee70c115607d538a638561b87` |
| `backend_app/routers/market.py` | `16f0377cb9f6c85ef0fb21ce3df37940bd27628e` |
| `backend_app/backend/api_key_vault.py` | `74a0059411467442b6aaa3d11d0d89af4d00fe65` |
| `backend_app/backend/portfolio_cache_updater.py` | `a74d76448a15150f703f05d0e43b83fd8470007b` |
| `terraform/security_groups.tf` | `87c9d131051d5e51e1001d0b19325b23cc1855d0` |
| `backend_app/backend/exchange_executor.py` | `df4fd42a151015124ae7eb3743733107106c413e` |

**All ten release-path files are unchanged despite six concurrent phase reports** (`PHASE13`,
two further `PHASE16` variants, `PHASE17_D_P15_4`, `PHASE18`, `PHASE19`). That is itself the
strongest available evidence that **no grant has reached any session.** Because every hash
matches, the Phase 16 gate design and the Phase 14/16 Bandit and Trivy analyses remain valid and
are **REUSED FROM IDENTICAL HASH** rather than re-derived.

### 1.3 Authorization manifest

| Grant | Scope | State |
|---|---|---|
| `AP-W1D-3` (W1D-12 §1, Code owner) | four classifier files | **GRANTED — fully consumed at A29** |
| Everything else (A–G below) | — | **ABSENT** |

A plan, a prior recommendation, a passing test, a report, a preflight request, or this prompt is
**not** a code-change grant. Nothing was treated as one.

---

## 2. Workstream C — D7-1: **correction to my own Phase 16 report**

### 2.1 The correction

Phase 16 §6 stated: *"Same pattern at `routers/orders.py:789` and `:1095`, **and
`routers/exchange.py:993`**."* **The `exchange.py` part was wrong.** I inferred it from a trailing
comma in a grep window instead of reading the call. `VER` Read directly this phase —
`backend_app/routers/exchange.py:993-995`:

```python
keys = vault.load_decrypted_keys(
    user["id"],
    body.exchange_id,
)
```

**Two positional arguments. No `access_token`.** A repository-wide search for `access_token`
across `{orders,exchange,market}.py` returns **zero** matches in `exchange.py` `VER`.

### 2.2 The corrected census — exactly three invalid sites

| # | Site | Argument | Verdict |
|---|---|---|---|
| 1 | `backend_app/routers/market.py:53` | `access_token=user.get("access_token")` | **INVALID** |
| 2 | `backend_app/routers/orders.py:792` | `access_token=user.get("access_token")` | **INVALID** |
| 3 | `backend_app/routers/orders.py:1098` | `access_token=user.get("access_token")` | **INVALID** |
| — | `backend_app/routers/exchange.py:993` | two positional | **CORRECT — do not touch** |
| — | `master_executor.py:252`, `api_ws/ws_routes.py:814`, `portfolio_cache_updater.py:161` | two positional | **CORRECT** |

Production signature `VER` — `backend_app/backend/api_key_vault.py:177`:
`def load_decrypted_keys(self, user_id: str, exchange_id: str) -> dict:`

**Why this correction matters:** a patch applied from my Phase 16 target list would have modified
a correct call site. The authorized target list is **three** sites in **two** files.

### 2.3 Severity, unchanged and still the most serious application defect found

`market.py:49-58` wraps the invalid call in `try: … except Exception:` whose handler returns an
**unauthenticated public connection**. So the `TypeError` fires for every user on every request
and is silently rendered as anonymous market data — indistinguishable from "this user has no
keys". `orders.py` has no such anonymising fallback, so there the same defect surfaces as a
failure rather than a silent downgrade.

**Masking mechanism** `VER`: `tests/test_market_none_keys_fix.py:13` and `:19` define doubles as
`load_decrypted_keys(self, user_id, exchange_id, access_token=None)` — accepting the parameter
production rejects. Three other doubles match production correctly.

> **P17-C1 — owner request (NOT applied).** Remove the three `access_token=` arguments; align
> `test_market_none_keys_fix.py`'s two doubles to the real signature; add an
> `inspect.signature`-based regression test binding mock to production; and narrow `market.py`'s
> bare `except Exception` so a vault/auth error is distinguishable from a legitimate
> no-credentials case. **Do not touch `exchange.py`.** Files: `routers/market.py`,
> `routers/orders.py`, `tests/test_market_none_keys_fix.py`. Owner: routers code owner.
> **Out of scope:** inventing an anonymous-fallback policy — that is a product/security contract
> decision, not a bug fix.

---

## 3. Workstream G — D7-2: confirmed by **executed import**, and it is a two-part defect

The brief asked me to verify the current import path rather than trust prior line numbers. `VER`
`backend_app/backend/portfolio_cache_updater.py:149-161`:

```python
from backend_app.backend.connection_engine import get_or_create_exchange
from backend_app.backend.key_manager import KeyVault      # ← line 151
...
try:
    vault = KeyVault()                                    # ← line 159
    keys = vault.load_decrypted_keys(user_id, exchange_id)
```

Three facts, each measured:

| Check | Result |
|---|---|
| `backend_app/backend/key_manager.py` exists? | **NO** — recursive search for `key_manager*` returns **zero files** repo-wide |
| Executed import probe | **`ModuleNotFoundError: No module named 'backend_app.backend.key_manager'`** |
| Does `api_key_vault.py` define `KeyVault`? | **NO** — it defines **`class APIKeyVault`** (`:62`) |

> **Two independent errors, so a one-line path fix is insufficient.** The module path is wrong
> *and* the class name is wrong. Correcting only the module would raise `ImportError: cannot
> import name 'KeyVault'`.

**The import sits at method scope, before the `try:` at line 158**, so it is **not** caught by the
method's own handler — it propagates out of every invocation. This is the mechanism behind the
recorded "false-green health": the service can report `ACTIVE` at startup while the update method
has never once succeeded.

> **P17-G1 — owner request (NOT applied).** Change line 151 to
> `from backend_app.backend.api_key_vault import APIKeyVault` and line 159 to `APIKeyVault()`,
> **or** confirm an intended `KeyVault` compatibility alias and add it deliberately. Add a test
> that imports and instantiates the updater's dependency so a `ModuleNotFoundError` cannot hide
> behind an `ACTIVE` health status. Keep this **separate** from any health/heartbeat redesign, as
> the brief directs. Owner: the cache/worker owner.

---

## 4. Workstream E — Trivy: Redis disposition proven, not assumed

The brief warned against assuming all five findings share a disposition. `VER`
`terraform/security_groups.tf` read directly:

| Resource | Ingress | Egress | Egress necessary? |
|---|---|---|---|
| `aws_security_group.alb` (`:22`) | 80 + 443 from `0.0.0.0/0` | `-1`, `0.0.0.0/0` | **Yes** — must reach target groups |
| `aws_security_group.ecs_tasks` (`:52`) | 8000-8080 from ALB SG; `self = true` internal | `-1`, `0.0.0.0/0` | **Yes, load-bearing** — ECR, Secrets Manager, Supabase, venues, monitoring. **Do not restrict without VPC endpoints** |
| **`aws_security_group.redis` (`:73`)** | **tcp 6379 from `ecs_tasks` SG only** — correctly tight | **`-1`, `0.0.0.0/0`, no `description`** | **NO** |

> **Redis is a connection *target*, not an initiator.** Its ingress is already correctly scoped to
> the ECS task security group on 6379 alone. A cache cluster has no need to originate traffic to
> the internet, so the `0.0.0.0/0` egress is **genuinely unnecessary** — the only one of the four
> `AWS-0104` sites that is safe to tighten on its own evidence. The concurrent report's fifth site,
> `redis-only/main.tf:62`, is the same resource shape in the standalone module.

**The gate remains RED regardless.** `AWS-0054` plus the two conventional `AWS-0104` findings still
trip a blocking CRITICAL step. Removing the two Redis findings is **not** gate closure — a point
worth stating because the arithmetic invites the opposite conclusion.

**Scan status: NOT RUN.** Trivy is not installed and docker is absent `VER`, so no scan was
executed and none is claimed. No Terraform was modified and none applied.

---

## 5. Workstreams A and B — route and gate: unchanged, cited

All five governing files are byte-identical to Phase 16 `VER`, so the analysis is **REUSED FROM
IDENTICAL HASH** and not re-derived:

- `01-pr-check.yml` is `pull_request`-only — no `push`, no `workflow_dispatch`. **There is no
  supported route to obtain correctness evidence for a commit that reached `main` by direct push.**
  `01 PR Check` has **never run** on `ecd23a9e`.
- `06-frontend-deploy.yml` has `needs:` **0** and `workflow_run` **0** — no correctness or
  security dependency. It **published** `ecd23a9e` to S3 + CloudFront (run 38129699497).
- `03 Deploy` correctly blocked the same SHA; the ECS job was **skipped**.
- The Phase 16 gate design stands, including the two documented Actions traps: `workflow_run`
  sets `GITHUB_SHA` to the **default-branch head**, not the triggering commit; and
  `workflows: [A, B]` fires when **either** completes. Both must be handled explicitly or a gate
  will appear green while validating a different commit.
- `require_security_scan.py` is reusable and already fails closed on missing / cancelled /
  skipped / running / API-error / missing-env. Its three gaps stand — and **G2** remains the
  important one: it gates on the **run-level** conclusion, and a run whose mandatory jobs all
  **skip** concludes `success`.

**No gate test was written** — the brief conditions it on authorization, which is absent. The
ten-case matrix is recorded in `PHASE16_FRONTEND_GATE_AND_RELEASE_VERIFICATION.md` §3.5.

A concurrent session is working this same lane (`PHASE17_D_P15_4_RELEASE_GATE_AND_PARALLEL_CLOSURE.md`).
**File ownership respected: I modified none of the five files.**

---

## 6. Workstreams D and F — cited, not duplicated

**D — Bandit.** Governing hashes identical, so **REUSED FROM IDENTICAL HASH**: 9 × B608, all
MEDIUM, 0 HIGH, `nosec` applied to **0** of the sites; every site interpolates an identifier
validated by `^[a-zA-Z0-9_]{1,64}$` (raises, never sanitises) with the only user-influenced value
parameter-bound. Identifiers cannot be bound parameters. The structural cause stands: the one-file
Bandit test proves silence on one module; the AST test proves safety tree-wide but not Bandit's
verdict — **so the suite is green while the gate is red and cannot detect it.** Closure needs both
the per-site annotations **and** widening the gate test to the real `-r backend_app` command.
**NOT RUN this phase** (unchanged inputs); **grant ABSENT**.

**F — Tenant / PostgreSQL / Redis.** **NOT CERTIFIED**, and blocked on environment rather than
decision: Redis `6379` closed, **0** `REDIS*` variables, docker absent, PostgreSQL `5432` closed.
33 tracked `tests/*postgres*.py` files, **0 executed on the candidate**. `FORCE ROW LEVEL
SECURITY` was **not** enabled — correctly, since tenant context remains unverified and the brief
records that enabling it without context can return zero rows while ownership/`BYPASSRLS` can
render policies ineffective. No DSN opened, no database or Redis connected, no credential read.

**G — W-6.** `exchange_executor.py` unchanged at `df4fd42a…` `VER`. The measured matrix (3 of 8
misclassified, plus a third shadowing defect) is **REUSED FROM IDENTICAL HASH** from A23E17; a
rerun against byte-identical source would duplicate evidence, which the brief forbids. **No retry
behaviour was enabled** — the brief is right that a classifier marking an error retryable is not
licence to retry a non-idempotent order. **W-4 UNKNOWN-exit policy: not invented. W-5 recovery:
remains blocked** behind that decision.

---

## 7. Commands run

| Command | Exit | Result |
|---|---|---|
| `git rev-parse` / `status --short` / `write-tree` / `hash-object` (entry + exit) | 0 | manifests §1 |
| `git worktree list`, `git stash list` | 0 | 1 worktree, 2 stashes |
| recursive search for `key_manager*` | 0 | **zero files** |
| `python -c` import probe for `backend_app.backend.key_manager` | 0 | **`ModuleNotFoundError`** |
| targeted greps: `access_token` in 3 routers; `security_groups.tf` rules; vault signature | 0 | §2, §3, §4 |

**No pytest, Bandit, Trivy, CI dispatch, database, Redis, exchange or network call.** The import
probe imports one module only and has no side effects beyond import.

Artifacts, all new and uniquely named:
`staging/PHASE17_RELEASE_GATE_AND_CRITICAL_DEFECT_CLOSURE.md` (this report).

---

## 8. Status matrix

| Workstream | State | Evidence class |
|---|---|---|
| A — correctness route | **BLOCKED** — no supported route; owner decision needed | REUSED FROM IDENTICAL HASH |
| B — frontend gate | **DECISION-READY** — design + 10-case matrix complete; grant ABSENT | REUSED FROM IDENTICAL HASH |
| **C — D7-1** | **DECISION-READY, census corrected** — 3 sites in 2 files | **VERIFIED** |
| D — Bandit | **DECISION-READY** — needs annotations **and** gate-test widening | REUSED / NOT RUN |
| E — Trivy | **DECISION-READY** — Redis disposition proven; gate stays RED | **VERIFIED** (source) / scan **NOT RUN** |
| F — tenant / PG / Redis | **BLOCKED** — environment absent | **NOT CERTIFIED** |
| **G — D7-2** | **DECISION-READY** — two-part defect proven by executed import | **VERIFIED** |
| G — W-6 | **DECISION-READY** | REUSED FROM IDENTICAL HASH |
| G — W-4 / W-5 | **BLOCKED** by an unmade policy decision | — |

### Minimal next approvals, by owner

| Owner role | Request | Unblocks |
|---|---|---|
| Release/SRE owner | **P16-B1** route releases through PRs (zero workflow edits) **or** grant `01-pr-check.yml` triggers | A, B, D, F — everything evidence-related |
| Workflow owner | **P16-A1…A4** gate, built with the `head_sha` pin and in-job AND | B |
| Routers code owner | **P17-C1** — 3 sites in `market.py` / `orders.py` + 2 test doubles | C |
| Cache/worker owner | **P17-G1** — import path **and** class name | G |
| Migrations owner | Bandit annotations + gate-test widening | D |
| Infrastructure owner | Narrow Redis egress ×2; documented accepts ×3 | E |

**Highest leverage remains the correctness route.** A gate cannot require a workflow that can
never run on the commit being gated, and no amount of local green substitutes for evidence on the
shipped SHA.

---

## 9. Final dispositions — unchanged, because no evidence supports changing them

| Item | Status |
|---|---|
| **LIVE TRADING** | **OFF / NOT CERTIFIED** |
| **TENANT ISOLATION** | **NOT CERTIFIED** |
| Backend deployment | **BLOCKED** — security gate RED on the candidate SHA |
| Frontend deployment | **BLOCKED** — no gate exists; it already published ungated |
| Production infrastructure changes | **NOT AUTHORIZED** |
| Source correctness on `ecd23a9e` | **UNVERIFIED** — 0 correctness jobs ever ran |
| Files modified this phase | **0** source · **0** test · **0** workflow · **0** Terraform · **0** governance |
| **Overall** | **DO NOT RELEASE** |

No git write command was issued (no stage, commit, push, merge, rebase, reset, stash, clean,
revert, branch switch). No deploy, rollback, CDN purge, DNS change, Terraform apply, infrastructure
provisioning or production mutation. No exchange credential, production secret or gitignored DSN
was read. No CI run was dispatched, fabricated, or claimed for a SHA it did not test. No report,
log or concurrent artifact was overwritten. No skipped or unavailable test is reported as passed.
