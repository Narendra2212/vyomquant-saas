# Phase 27 — Security Evidence & Release Integrity: Prerequisite Recheck and Authorization Request

**Destination verified ABSENT before writing. No prior report overwritten.**

> ## Outcome: prerequisites rechecked and UNCHANGED. 0 files changed. Implementation remains blocked.
>
> Phase 26 concluded *"0 files changed, no grant exists, and permitted verification is now
> exhausted."* The brief for this phase requires me to test whether that remains true rather than
> restate it. **I tested it. It does.** This report is therefore deliberately short: a prerequisite
> recheck, **two corrections to my own earlier reports**, and a prioritized authorization request.

**DO NOT RELEASE · DEPLOYMENT BLOCKED · LIVE TRADING OFF / NOT CERTIFIED · TENANT ISOLATION NOT
CERTIFIED.**

---

## 1. Entry / exit integrity manifest — identical

| Property | Entry | Exit |
|---|---|---|
| Branch / `HEAD` | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | **unchanged** |
| `origin/main` / divergence | `ecd23a9e…` / **`0 0`** | unchanged |
| `git reflog -1` | `ecd23a9e` | **unchanged** |
| staged / unstaged / unmerged / tracked | 0 / 2 / 0 / 5705 | **0 / 2 / 0 / 5705** |
| Worktrees / stash | 1 / 2 | **1 / 2, unmutated** |

**The only tracked modifications, preserved byte-for-byte and neither staged nor reverted:**
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md` (` M`), `tests/test_drift_classification.py` (` M`).

Per the brief's §1.1 prohibition, **no index-, ref-, stash-, worktree- or object-database-mutating
command was used for measurement** — `git write-tree` was deliberately not computed. Integrity is
established instead from `staged = 0`, `unmerged = 0`, `tracked = 5705`, an unchanged reflog head,
and the tracked-modification set being identical to entry.

---

## 2. Prerequisite recheck — the actual work of this phase

`VER` **Authorization:** ledger `**GRANTED**` occurrences = **0**; `:800-804` (`AP-W1D-1`…`-5`) all
`NOT GRANTED`; `:802` = `NOT GRANTED`; `A23E11` still *"no authorization arrived"*. No newer
authorization delta exists.

`VER` **No concurrent implementation.** All 14 governed targets are `clean` against HEAD:

| Hash (12) | Path | Hash (12) | Path |
|---|---|---|---|
| `e2d9ccc6f951` | `05-security.yml` | `a1c8dd2d48fb` | `require_security_scan.py` |
| `91d55a379115` | `01-pr-check.yml` | `cec975b267e8` | `ecs_deploy_and_diagnose.py` |
| `42c7696eb7cb` | `02-build.yml` | `4c38728fb360` | `phase17_frontend_release_gate.py` |
| `eb8078eac062` | `03-deploy.yml` | `a6f0a3d4bed3` | `pytest.ini` |
| `285cc2d19b2b` | `06-frontend-deploy.yml` | `7d05ee95c777` | `terraform/ecs.tf` |
| `dad405d6124d` | `ecs-task-definition-full.json` | `f167f0e8c239` | `backend_app/core/state.py` |
| `a7deff99c9e5` | `test_bandit_sast_gate_is_green.py` | `ab3618ebeffe` | `backend_app/test_perf.py` |

**Conclusion: no prerequisite has changed.** Every lane in §4 remains blocked on the same missing
grant, so no lane was edited and nothing is presented as implemented.

### 2.1 Findings adopted from Phase 25/26 rather than re-derived

Phase 26's matrix is more precise than my own prior work in four places; I adopt and attribute it:

- `require_security_scan.py:15-16, :123-124` — **its documentation and operator diagnosis are
  factually wrong** while the gate itself refuses correctly.
- `portfolio_cache_updater.py:151,159` — the `key_manager` **module** is absent **and** the
  `KeyVault` **class** is absent. Two defects, not one.
- `exchange_executor.py:1225-1257` — **3 of 8** matrix cases misclassified (measured).
- **The Terraform Redis security-group collision premise is WITHDRAWN** — the roots target separate
  VPCs and `redis-only` uses **local state** for production. I had treated the collision as live in
  an earlier phase; that concern is retired.

---

## 3. Two corrections to my own earlier reports

### 3.1 `pytest.ini` — I misread `testpaths`, so I understated the discovery defect

My Phase 23 report asserted *"the directory isn't the obstacle — `norecursedirs` doesn't exclude
`staging/` and `testpaths` is empty. The filename is."* **That was wrong.** `testpaths` is a
continuation-line value:

```
1  [pytest]
2  testpaths =
3      tests
5  python_files = test_*.py *_test.py
```

`VER` `testpaths = tests`. So there are **two independent causes** of the Phase 17 prototype being
invisible to default discovery, and fixing either alone is insufficient:

| # | Cause | Effect |
|---|---|---|
| 1 | `testpaths = tests` | a bare `pytest` never searches `staging/` |
| 2 | `python_files = test_*.py *_test.py` | `phase17_test_frontend_release_gate.py` matches **neither** pattern |

Phase 25/26's "2 causes" is correct and mine was not. **Both must be addressed by any relocation
grant** — moving the file into `tests/` without also renaming it to `test_*.py` would still leave it
uncollected.

### 3.2 `backend_app/test_perf.py` has **two** stale suppressions, not one

`VER` Measured:

```
:18   "SELECT "  # nosec: B608
:27   )          # nosec: B608
```

Two annotations, both `# nosec: B608`. My Phase 24 run reported bandit warning at `:26`
(statement-start versus annotation line, adjacent to `:27`); Phase 18 had reported a different
finding entirely (`B104` at `ws_server.py:72`). The brief's "two" is correct.

**Both are stale in the specific sense bandit reports:** *"nosec encountered (B608), but no failed
test on file"* — there is no B608 finding at those locations for the annotation to suppress. They
are inert, and removing them changes no scan verdict. **They are not among the nine live B608
findings** (which are in the three alembic migrations). I did not modify them — no grant.

---

## 4. Prioritized authorization request — exact paths, grouped by independent lane

Ordered by the brief's §13 priority: security-evidence integrity first, because an unscanned
infrastructure root and discarded failure evidence make every other security result
uninterpretable.

### G-1 · Security evidence and IaC coverage — **highest priority**

| | |
|---|---|
| **Paths** | `.github/workflows/05-security.yml`; new `tests/test_security_workflow_structure.py` |
| **Allowed** | (a) make the `aerora_quant_platform/infrastructure/` scan at `:131` run independently of the `terraform/` scan at `:120`, preserving `exit-code: '1'` on both; (b) add `if: always()` to the SAST upload at `:42`; (c) condition the IaC report upload at `:156` on its report existing, and the report step at `:144` on a valid checkout rather than on the prior scan passing |
| **Required tests** | both roots present; both blocking; second root runs when first fails; neither silently skippable; missing scanner ≠ success; job and workflow conclusions still fail |
| **Negative controls** | remove the second root; remove its failure-independent condition; weaken `exit-code`; each must fail the test |
| **Prohibited** | `continue-on-error`, unconditional success, severity reduction, scope narrowing, any change to mandatory failure semantics |
| **Why first** | `aerora_quant_platform/infrastructure/` — **37 scannable files including `docker-compose.production.yml`** — has **never** been scanned, and the failure is invisible because the first root already reports red |

### G-2 · Safety policy — **owner decision, not a code grant**

> *"`safety check` in `05-security.yml:39` is ⟨**mandatory** — remove the `|| echo` so a dependency
> vulnerability fails the job⟩ / ⟨**advisory** — rename the step, keep the fallback, and record it as
> non-blocking in the release contract⟩."*

Currently fail-open (`|| echo` forces exit 0) while presented as mandatory. **I did not decide this.**

### G-3 · Build-side digest fallback

| | |
|---|---|
| **Paths** | `.github/workflows/02-build.yml` (line 78 only) |
| **Allowed** | replace `\|\| echo "$IMAGE_TAG"` with the fail-closed pattern **already proven in-repo** at `03-deploy.yml:168-172` (`\|\| echo ""`, then refuse on empty/`None`) |
| **Tests** | failed registry query, empty output, literal `None`, malformed digest, SHA mismatch |
| **Prohibited** | weakening `03-deploy.yml`'s existing guard; any AWS query, push, retag, promotion or deploy |
| **Note** | severity is bounded — the deploy **re-queries** ECR rather than consuming the build's `image_digest` output |

### G-4 · Verifier relocation **and** job-level evidence

| | |
|---|---|
| **Paths** | `staging/phase17_frontend_release_gate.py` → `scripts/`; `staging/phase17_test_frontend_release_gate.py` → `tests/test_frontend_release_gate.py`; `scripts/require_security_scan.py` |
| **Allowed** | relocate preserving decision semantics (**both** §3.1 causes must be fixed); add `/jobs` inspection requiring every explicitly-listed mandatory job to conclude `success`; correct the wrong documentation at `require_security_scan.py:15-16, :123-124` |
| **Tests** | the existing 42 scenarios **plus** pagination, mandatory-job failed/skipped/missing, advisory-job non-promotion, and discovery-by-default |
| **Prohibited** | promoting optional jobs to mandatory without a policy decision; weakening the missing-scan refusal |

### G-5 · Frontend publication gate — **depends on G-6**

Gate job + `needs:` on every publish path + `DEPLOY_SHA: ${{ github.sha }}` + no override +
explicit `permissions: actions: read` + structural tests. Assertion set validated **7/7** with
negative controls in Phase 19. **A gate cannot retroactively contain the publication already made at
`ecd23a9e`;** no rollback, republish, S3 or CloudFront action is requested.

### G-6 · Correctness route — **owner decision**

> *"Releasable commits obtain correctness evidence via ⟨**PR-only advancement of `main`** through
> branch protection — recommended, closes frontend and backend together⟩ / ⟨**a push-candidate
> correctness workflow**⟩."* Either must treat a skipped mandatory job as not-success. **No
> repository setting was changed or read beyond authorized read-only inspection.**

### G-7 · Remaining lanes, unchanged scope

| Lane | Paths | Gate |
|---|---|---|
| Bandit parity | gate test, then the 3 alembic migrations; `test_perf.py:18,27` stale suppressions | parity test **before** any suppression |
| D7-1 | **only** `market.py:53`, `orders.py:792`, `orders.py:1098` + the test double | must not touch the 9 legitimate `access_token` callers |
| **D6-D before D7-2** | redacted boot evidence: `type(app_state.vault).__name__` + whether the import or init fallback logged | `MockEngine.__getattr__` returns a noop `None`, so a naive `get_vault()` swap converts a caught, logged `ImportError` into a silent null |
| W-6 | `exchange_executor.py:1225-1257` | classification only — **no retry, backoff or resubmission**; `.retryable` has zero control-flow readers |
| Terraform | both roots | collision premise **withdrawn**; ownership boundary first; `terraform`/`trivy` **absent locally** |
| Tenant / PG / Redis | tenant context, RLS | disposable non-owner, non-superuser services required; **FORCE RLS must not be enabled first** |

---

## 5. Checks executed this phase

| Check | Result |
|---|---|
| `git rev-parse` / `status` / `reflog` / `ls-files` / `worktree list` / `stash list` | **executed, passed** — §1 |
| Ledger + A23E11 grant recheck | **executed** — 0 grants |
| 14 governed-target hash + clean/dirty | **executed, passed** — all clean |
| `pytest.ini` verbatim read | **executed** — §3.1 correction |
| `Select-String backend_app/test_perf.py nosec` | **executed** — §3.2, two annotations |
| Phase 25/26 report inspection | **executed** — §2.1 |
| Bandit full-tree scan | **not re-run** — inputs unchanged since Phase 24 (bandit 1.9.4, exit 1, 9 × B608 MEDIUM); reused with provenance |
| Trivy / Terraform scans | **blocked — tooling absent.** No scan claimed |
| PostgreSQL / Redis integration | **blocked — services absent** |
| Any implementation + its regression tests | **not run — authorization absent** |
| Unbounded full-tree collection | **deliberately not attempted** — timed out at 120 s in an earlier phase; discovery answered from `pytest.ini` instead |

**One command aborted:** an initial combined manifest query was interrupted before returning; re-run
in two parts, both successful. **One method defect of mine repeated and noted:**
`Get-ChildItem -Filter 'PHASE2[5-7]*'` returned nothing because `-Filter` does not support regex
classes — the same mistake I made in Phase 23; results were obtained by sorted enumeration instead.

No secret, DSN, token, signed payload or credential was read, searched for, printed or reproduced.
No production system was accessed.

---

## 6. Final posture

| Item | State | Evidence class |
|---|---|---|
| Release | **DO NOT RELEASE · DEPLOYMENT BLOCKED** | — |
| IaC scan coverage | **1 of 2 roots; second root never scanned** | source-verified |
| SAST evidence on failure | **discarded** | source-verified |
| Safety check | **fail-open, awaiting owner disposition** | source-verified |
| Image provenance | **fail-open in build; fail-closed in deploy** | source-verified; **live ECR/ECS UNVERIFIED** |
| Frontend publication | **ungated; already published at `ecd23a9e`** | CI-observed |
| Backend deployment | **fail-closed and working — refused this commit** | CI-observed |
| Job-level gate evidence | **absent in both verifier implementations** | source-verified |
| Verifier tests | **42 pass when named explicitly; invisible to default discovery (2 causes)** | unit only |
| Bandit | **9 live B608 MEDIUM; 2 inert suppressions in `test_perf.py`** | scanner, reused |
| Terraform / Trivy | **scan BLOCKED — tooling absent** | not run |
| PostgreSQL | **UNVERIFIED** | not run |
| **TENANT ISOLATION** | **NOT CERTIFIED** | source-verified only |
| **LIVE TRADING** | **OFF / NOT CERTIFIED** | — |

**Next critical dependency: G-1.** Until both IaC roots are actually scanned and failure evidence
survives, no security result in this repository can be interpreted — which makes every downstream
gate decision unsound regardless of how the other lanes resolve.

No gate was weakened, no finding suppressed, no threshold lowered, no test deleted, no skip or xfail
reinterpreted as a pass, no governed file modified, and nothing described as implemented that was
not. Unit evidence, CI-observed evidence, and absent integration/production evidence are labelled
separately throughout.
