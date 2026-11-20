# Phase 14 — Main Stabilization, Security Gate Triage, CI Evidence, Critical Defect Closure

**No production, test, workflow, Terraform or configuration file was changed. No CI dispatched. No
commit, push, merge, revert, reset, stash or clean. No database, Redis, exchange, credential or
production access.** Destination verified absent before writing.

**LIVE TRADING: OFF / NOT CERTIFIED.**

---

## 1. Executive summary

**Both security gate failures are now fully diagnosed, and the Bandit one has a root cause nobody
had identified: the repository's own Bandit gate test scans a single file while CI scans the whole
tree.**

| Finding | Verdict |
|---|---|
| **Bandit — 9 × B608, all MEDIUM** | **All 9 are false positives on safety grounds, but legitimately red.** Every site validates its interpolated identifier through `_validate_table_name` (`^[a-zA-Z0-9_]{1,64}$`, raises, never sanitises) and binds the only user-controlled value as a parameter. They are unsuppressed and sit in `backend_app/alembic/versions/`, a path the repo's own gate test never scans |
| **Trivy `AWS-0054` — ALB not HTTPS** | **False positive with an enforced compensating control.** `alb.tf` redirects :80 → :443 when a custom domain exists, and a `lifecycle.precondition` *refuses the apply* for prod without it. Trivy cannot evaluate `dynamic` blocks or preconditions |
| **Trivy `AWS-0104` ×4 — unrestricted egress** | **Real but conventional.** 3 of 4 are the standard pattern (tasks must reach ECR/Secrets Manager/venues). **The `aws_security_group.redis` egress is genuinely unnecessary** and is the one safe tightening |
| **`01 PR Check` at HEAD** | **NEVER RAN.** No test, Redis, PostgreSQL or flake8 evidence exists at `ecd23a9e` |
| **Nothing was deployed** | `03 Deploy`'s gate refused correctly; ECS deploy **skipped** |

**I reproduced the Bandit failure locally, exactly** — same tool version, same command, same exit
code, same finding count. That is the only gate evidence in this phase that is executed rather than
inferred.

**Authorization unchanged: no grant exists for any code, test, workflow or Terraform change.** Every
remediation below is a proposal.

---

## 2. Baseline and preservation audit

| Invariant | Entry | Exit |
|---|---|---|
| Branch / HEAD | `main` / `ecd23a9e934ec58a7b1f0e5db638da8e865e8187` | unchanged |
| Parent | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | unchanged |
| `origin/main` | `ecd23a9e` — **0 ahead / 0 behind; HEAD is pushed** | unchanged |
| `git write-tree` | `4b45a7633f76cd8ab5be023c1e45af7f1a63c398` | unchanged |
| Staged / unmerged | 0 / 0 | 0 / 0 |
| Unstaged tracked | 2 | 2 |
| Worktrees | 1 | 1 |

**No newer commit or run has changed the risk.** `ecd23a9e` is still HEAD and still pushed.

The two dirty files are another workstream's and were **not touched**:
`staging/PHASE11B_A23E11_AUTHORIZATION_DELTA.md`, `tests/test_drift_classification.py`.

Artifacts created, all under `staging/`, all new: `p14_sec_failed.log`, `p14_bandit_local.json`,
`p14_bandit_stderr.txt`, and this report. **No `reports/` directory was created** — the local Bandit
run was redirected into `staging/` precisely so it could not collide with the CI output path.

---

## 3. Workstream A — security triage

### 3.1 Bandit: evidence

CI configuration, `.github/workflows/05-security.yml:35-36`:

```
bandit -r backend_app -ll -f json -o reports/bandit-report.json
```

`-ll` = report MEDIUM and above. No `|| true`, no `--baseline`, no exclusions. Bandit exits 1 on any
reported finding, so the step fails on a single MEDIUM.

**Reproduced locally, identical:**

```
.venv\Scripts\python.exe -m bandit -r backend_app -ll -f json -o staging\p14_bandit_local.json
exit code: 1          (CI: ##[error]Process completed with exit code 1)
bandit 1.9.4          (CI: bandit-1.9.4-py3-none-any.whl)
results: 9            severities: 9 MEDIUM, 0 HIGH
confidence: 8 LOW, 1 MEDIUM        nosec applied to these sites: 0
```

All 9 are **B608 — "Possible SQL injection vector through string-based query construction"**, in
three Alembic migrations:

| File | Lines |
|---|---|
| `backend_app/alembic/versions/d4e5f6a7b8c9_bug049_normalize_user_id_to_uuid.py` | 193, 198, 209, 467, 475 |
| `backend_app/alembic/versions/b2c3d4e5f6a7_positions_canonical_shape.py` | 224, 262 |
| `backend_app/alembic/versions/a1b2c3d4e5f6_normalize_tenant_id_to_uuid.py` | 212, 222 |

One informational CI line, not a failure: *"nosec encountered (B104), but no failed test on file
`backend_app/backend/ws_server.py:72`"*.

### 3.2 Bandit: each finding traced to source — not vulnerabilities

Every site has the same shape. `d4e5f6a7b8c9…:189-203`:

```python
    table = _validate_table_name(raw_table)
    column = _validate_table_name(raw_column)

    total = conn.execute(
        sa.text(f'SELECT count(*) FROM public."{table}" WHERE "{column}" IS NOT NULL')
    ).scalar()

    bad = conn.execute(
        sa.text(f'SELECT count(*) FROM public."{table}" '
                f'WHERE "{column}" IS NOT NULL AND "{column}"::text !~* :re'),
        {"re": UUID_REGEX},                      # <-- the only value is BOUND
    ).scalar()
```

The validator, `d4e5f6a7b8c9…:145-158`, read directly:

```python
def _validate_table_name(table_name: str) -> str:
    """Refuse anything that is not a bare SQL identifier. Raises; never sanitises."""
    s = str(table_name)
    if re.match(r"^[a-zA-Z0-9_]{1,64}$", s):
        return s
    raise ValueError(f"not a SQL identifier: {s!r}")
```

**A strict allowlist that raises rather than cleans.** No quote, semicolon, whitespace, parenthesis,
comment marker or any other SQL metacharacter can pass. The values reaching interpolation are
therefore validated identifiers; the only user-influenced value (`UUID_REGEX`) is a bound parameter.

Identifiers **cannot** be bound parameters in SQL, so validated interpolation is the correct
construction — which is exactly the shape B608 cannot distinguish from concatenated user input.
These are also Alembic migrations driven by a module-level literal target list under operator
control, not request input.

**Assessment: 9 false positives. Zero real vulnerabilities. Zero introduced by the launch commit**
in the sense of new unsafe code — the files predate it and the pattern is the repo's own documented
one.

### 3.3 Bandit: the root cause nobody had found

`tests/test_bandit_sast_gate_is_green.py` exists and documents this exact gate. Its history, from
its own module docstring: at commit `16d31982` the gate reported **4 B608 findings, all in
`backend_app/backend/transactional_execution_manager.py`** (:692, :746, :792, :1144). Those were
suppressed with `# nosec B608` plus a reason — the mechanism Bandit provides — and the premise was
held two ways:

| Test | Scope | What it proves |
|---|---|---|
| `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` | **one file** — `GATED_MODULE = backend_app/backend/transactional_execution_manager.py` (`:68`, `:103`) | Bandit is silent **on that module** |
| `test_every_interpolation_into_sql_is_constrained_before_it_gets_there` | **whole tree** — `BACKEND_APP.rglob("*.py")` (`:143`) | every interpolation is *constrained* (AST; `# nosec` is invisible to it) |

Its docstring is candid about the narrowing: *"scoped to one file so it runs in about a second
instead of the 5m44s a full `-r backend_app` sweep takes. The AST test below is what covers the rest
of the tree."*

**The gap is now exact.** The AST test checks **safety**; it does not check **Bandit's silence**. The
Bandit test checks silence on **one module**. The 9 alembic sites:

* **pass** the AST test — they satisfy its RULE 2 (`col = _validate_table_name(c)`), and
  `SQL_SAFE_VALIDATORS` (`:79`) already contains `_validate_table_name`;
* **are not suppressed** — they never received the `# nosec B608` the four original sites got;
* **are scanned by CI** but **not** by the one-file Bandit test.

So the suite is green while the gate is red, and the suite cannot see it. The docstring even
anticipates the class — it records that the AST test *"turned up TEN interpolation sites bandit never
reported at all… three in an alembic RLS migration"* — but the converse (sites Bandit reports that
the one-file test cannot see) was not covered.

### 3.4 Bandit: smallest safe remediation

Two parts. **Neither loosens the gate**, and the repo's own docstring explicitly rules out `-lll`,
`|| true` and `--baseline` — I am not proposing any of them.

| # | Change | Purpose | Risk |
|---|---|---|---|
| **A1** | Add `# nosec B608` **with the per-site reason** to the 9 lines in the 3 migrations, matching the established `transactional_execution_manager.py` precedent | makes the gate green honestly — the premise is already proven by the AST test and §3.2 | none to runtime; these are comments |
| **A2** | Broaden `test_bandit_reports_nothing_in_the_module_the_gate_failed_on` to run the gate's **actual** command (`-r backend_app`), or add a second test that does, accepting the ~5m44s cost (or marking it slow) | makes the gate's own redness **detectable in the suite** — closes the structural gap, not just this instance | test runtime only |

**A2 is the one that matters.** A1 alone fixes today's nine; the same divergence would recur with the
next unsuppressed MEDIUM anywhere outside that one module. The `# nosec` annotations are justified
only because A2 (plus the existing AST test) keeps the premise under test — suppression without
detection is what the docstring warns against.

Regression test: A2 **is** the regression test. Local validation is fully possible and costs nothing
beyond runtime — Bandit needs no credentials or network. **Authorization required:** a grant covering
the 3 migration files and `tests/test_bandit_sast_gate_is_green.py`.

### 3.5 Trivy IaC: evidence

`.github/workflows/05-security.yml:120-128` — `trivy-action@v0.36.0`, `scan-ref: terraform`,
`scanners: misconfig,secret`, `severity: CRITICAL`, `exit-code: 1`.

Per-file failure counts from the run log: `alb.tf` 1, `redis-only/main.tf` 1, `security_groups.tf` 3;
`cloudfront.tf`, `cloudtrail.tf`, `ecs.tf`, `secrets.tf`, `vpc.tf` all **0**. **Total 5 CRITICAL, two
distinct rules. No secret findings.**

### 3.6 `AWS-0054` — "Listener for ALB does not use HTTPS" · `alb.tf:49-93` · **false positive**

Read directly. `aws_lb_listener.http` is port 80/HTTP **by design**, with two mutually exclusive
dynamic actions:

```hcl
  dynamic "default_action" {                      # enable_custom_domain == true
    for_each = var.enable_custom_domain ? [1] : []
    content { type = "redirect"
              redirect { port = "443" protocol = "HTTPS" status_code = "HTTP_301" } }
  }
  dynamic "default_action" {                      # enable_custom_domain == false
    for_each = var.enable_custom_domain ? [] : [1]
    content { type = "forward" target_group_arn = aws_lb_target_group.frontend.arn }
  }
  lifecycle {
    precondition {
      condition = var.enable_custom_domain || !contains(["prod","production"], var.environment)
      error_message = "A prod/production environment requires enable_custom_domain=true. ..."
    }
  }
```

With a custom domain the listener is a 301 to HTTPS. Without one it serves plaintext — and the
`lifecycle.precondition` **refuses the apply** for `prod`/`production`. The in-file comment records
this as control **SC-7** and explains why an unconditional redirect would be worse (it would point
:80 at a :443 listener that does not exist).

Trivy's `AWS-0054` evaluates the static `protocol = "HTTP"` attribute; it cannot resolve a `dynamic`
block or a `lifecycle.precondition`. **Exploitability: none in prod — the apply is refused.** The
compensating control is stronger than the rule.

**Remediation: none to the infrastructure.** The honest options are (a) a documented, justified
`trivy:ignore:AWS-0054` on that resource citing the precondition, or (b) a `.trivyignore` entry with
the same justification. Both need infrastructure-owner approval, and (b) is the broader hammer.
**Do not "fix" this by making the redirect unconditional** — that breaks non-domain deploys.

### 3.7 `AWS-0104` ×4 — "unrestricted egress" · **real, mostly conventional, one worth fixing**

| Resource | Site | Assessment |
|---|---|---|
| `aws_security_group.alb` | `security_groups.tf:26` (via `:22-27`) | conventional — ALB forwards to targets |
| `aws_security_group.ecs_tasks` | `security_groups.tf:56` (via `:52-57`) | conventional and **load-bearing** — tasks must reach ECR, Secrets Manager, Supabase and the venues |
| **`aws_security_group.redis`** | **`security_groups.tf:77` (via `:73-78`)** | **unnecessary.** Ingress is correctly `6379` from `ecs_tasks` only; ElastiCache initiates no outbound connections. Egress `0.0.0.0/0` protocol `-1` serves nothing |
| `aws_security_group.redis` (standalone stack) | `redis-only/main.tf:62` (via `:58-63`) | same, same reasoning |

All four are `protocol = "-1"`, `from_port = 0`, `to_port = 0`, `cidr_blocks = ["0.0.0.0/0"]`.

**Exploitability.** No credential, data or privileged-service exposure by itself — egress rules do not
admit inbound traffic, and all four groups have tight ingress. The risk is **post-compromise
exfiltration latitude**: a compromised task could reach any address. Restricting `ecs_tasks` egress
properly requires VPC endpoints for ECR/Secrets Manager/CloudWatch plus an allowlist for the venues
and Supabase — a real infrastructure project, not a config tweak.

**Smallest safe remediation: the two Redis groups only.** Removing or narrowing their egress is
near-zero-risk and clears 2 of the 5 CRITICALs. The ALB and `ecs_tasks` rules should be **accepted
and documented**, not tightened blind. **Local validation is not possible** (no Trivy installed, and
`terraform plan` needs AWS credentials). **Authorization: infrastructure owner, per-file scope.**

---

## 4. Workstream B — CI and test evidence

### 4.1 Run matrix at `ecd23a9e`

| Workflow | Conclusion | Run ID |
|---|---|---|
| `02 Build` | success | 38129699471 |
| `06 Frontend Deploy` | success | 38129699497 |
| `07 Desktop Build (native, unsigned)` | success | 38129699516 |
| **`05 Security`** | **failure** | 38129699454; retried 38132697285 — **failed again** |
| **`03 Deploy`** | **failure** | 38130273012 |
| **`01 PR Check`** | **NOT RUN at this SHA** | latest is 38061973739 at `f5b99e57` — different commit, and it failed |

Job-level detail:

```
05 Security  38132697285
  Trivy Container Vulnerability Scanner        success
  Bandit SAST & Dependency Security Scan       FAILURE -> "Run Bandit Scan"
  Trivy IaC Misconfiguration & Secret Scan     FAILURE -> "Trivy IaC scan - terraform/ (blocking, CRITICAL)"

03 Deploy    38130273012
  Pre-Deployment Validation Gate               FAILURE -> "Require the security scan to have passed
                                                           for this commit"
  ECS Fargate Deploy & Self-Healing Diagnostic SKIPPED
```

**The gates behaved correctly — they refused, and nothing was deployed.**

### 4.2 Why `01 PR Check` did not run, and why I did not force it

`01 PR Check` is PR-triggered; a direct push to `main` does not fire it. Per the brief I did **not**
change triggers, create an artificial commit, or dispatch a workflow. I inspected the workflow
definition and read existing run records only.

Repository-supported routes, neither taken:

| Route | Viability |
|---|---|
| Open a PR containing `ecd23a9e` | the supported path. `main` is already at that SHA, so a PR needs a branch — a decision, not a mechanical step |
| `workflow_dispatch` | **not available** — `01-pr-check.yml` declares no `workflow_dispatch` trigger, so there is nothing to dispatch |

**Blocker recorded: the commit is on `main` with no PR, and the validating workflow is PR-only.**

### 4.3 Evidence status for `ecd23a9e` — distinguishing the categories precisely

| Evidence | Status |
|---|---|
| Full Python test suite (~11,000) | **NOT RUN at this SHA** |
| `redis:7-alpine` service + real-Redis tests | **NOT RUN at this SHA** |
| PostgreSQL `database-tests` | **NOT RUN at this SHA** |
| `static-audit` / flake8 | **NOT RUN at this SHA** |
| `05 Security` | **RAN — FAILED** |
| Bandit specifically | **RAN — FAILED**; independently **reproduced locally, exit 1, 9 findings** |
| Trivy IaC | **RAN — FAILED**, 5 CRITICAL |
| Container, frontend, desktop builds | **RAN — PASSED** |

**`NOT RUN` is not `PASS`, `SKIPPED` or `XFAIL`.** Nothing in §4 is counted as a pass.

---

## 5. Workstream C — branch risk

`ecd23a9e` is on `origin/main`, carrying 3,723 files, **untested by the unit/integration lane, failing
two security jobs, and undeployed.** `074b772a` is its parent and is an ancestor, so history is
linear and nothing was lost.

Is the security failure a release blocker? **Yes, by the repository's own policy, not by assumption:**
`03-deploy.yml`'s first step is *"Require the security scan to have passed for this commit"*, and it
refused. The deploy pipeline is self-blocking by design.

**Lowest-risk path to a verifiable main, in order:**

1. **Make the gate green honestly** — A1 + A2 (§3.4) for Bandit; accept-and-document or tighten the
   two Redis egress rules for Trivy (§3.6, §3.7). Small, reviewable, no infrastructure change beyond
   two SG rules.
2. **Obtain test evidence** — open a PR carrying the SHA so `01 PR Check` runs the suite, Redis and
   PostgreSQL lanes.
3. **Only then** consider deployment.

**No revert, reset, force-push, branch-protection change or emergency deploy is proposed, and none
was executed.** A revert would be the wrong instrument: the commit is not known-bad, it is
**unverified**, and reverting 3,723 files would destroy work while producing no evidence.

---

## 6. Workstreams D–I — status

Verified against current source this phase; prior-phase detail is not restated.

| WS | Finding | Authorization | Action taken |
|---|---|---|---|
| **D — D7-1** | 3 invalid `access_token=` call sites confirmed at `orders.py:789-793` (unguarded → 500), `orders.py:1095-1099` (masked → `[]`), `market.py:50-54` (masked → **anonymous market data**). Git history: **one signature form ever**, 2 params; method and broken callers entered in the same commit `6c5660a1`, so **deleting the kwarg loses no behaviour** | **ABSENT** | patch plan + 6-case test matrix prepared in A23E13 §4; **no file touched** |
| **E — W-6** | `_handle_ccxt_error` `exchange_executor.py:1225-1257`; symbol/market branch (`:1246`) precedes timeout (`:1249`); bare `"key"` at `:1252`. **Repository-wide: the only production read of `ExchangeError.retryable` is the log line at `:1687`** — `replay_safe_transaction_guard.py:304` and `paper_simulator.py:1691` use their own predicates. **Not retry suppression** | **ABSENT** | plan prepared; **no file touched** |
| **F — QUOTA-01** | Local `REDIS_URL`/`AERORA_TEST_REDIS_URL`/`REDIS_HOST`/`REDIS_PORT` **all unset**; 6379 unreachable. CI **does** provision `redis:7-alpine`, ephemeral and job-exclusive, with the committed `/15`→`/0` fix (`AERORA_TEST_REDIS_URL: redis://localhost:6379/0`) — the workflow comment documents that `get_redis_url(db)` appends `?db=` which redis-py resolves **over** the path. Four suites present | n/a | **real-Redis UNEXECUTED** — no URL invented, no fixture altered, no suite pointed at an unknown target |
| **G — TENANT-01** | `FORCE ROW LEVEL SECURITY`: **zero executable SQL statements** tree-wide; prose only. Scorecard `tenant_force_rls: OPEN`; live instance recorded `relrowsecurity=true, relforcerowsecurity=false`; `tenant_db_context.py` unwired | n/a | **static only. NOT CERTIFIED.** No DB connection. Any tenant count must use `COUNT(DISTINCT user_id)`/`EXISTS`; not run |
| **H — A23** | A23E6 form **0 ticked / 12 unticked**, unmodified since commit. **0 of 8 gates.** `ReconciliationScheduler` started (`main.py:456`), `OrderWatchdog` started (`:432`); `ExchangeReconciliationService` **not started**; `MarketplaceExpiryWorker`/`expiry_sweep` **zero matches in `main.py`** | **ABSENT** | nothing created or edited |
| **I — test quality** | **The §3.3 Bandit gate-scope gap is this workstream's finding.** Also carried: `tests/test_market_none_keys_fix.py:13,19` declares doubles **with** `access_token=None`, matching the broken call while the real class does not; no test binds the real vault signature | n/a | identified; no test weakened or removed |

---

## 7. Commands run

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `gh run list --limit 10 --json …` | 0 | run matrix §4.1 |
| 2 | `gh run view 38132697285 --log-failed` | 0 | 965-line log → `staging/p14_sec_failed.log` |
| 3 | `gh run view 38130273012 --json jobs` | 0 | deploy-gate failure §4.1 |
| 4 | **`python -m bandit -r backend_app -ll -f json -o staging\p14_bandit_local.json`** | **1** | **9 findings, 9 MEDIUM, all B608 — reproduces CI exactly** |
| 5 | `git log -S "def load_decrypted_keys" / -S "access_token=user.get"` | 0 | both → `6c5660a1` |
| 6 | `git rev-parse` / `write-tree` / `status` / `hash-object` | 0 | §2 |

**No pytest run.** No implementation was authorized, so there is nothing new to validate, and I claim
**no pass, fail, skip or xfail**. `test_r6_d` remains `XFAIL(strict)`, untouched.

Environmental limits: no Trivy locally; no Redis; no PostgreSQL; no AWS credentials; no sandbox
credentials; `01 PR Check` undispatchable (§4.2).

---

## 8. File inventory

**Changed:** `staging/PHASE14_MAIN_STABILIZATION_AND_CRITICAL_CLOSURE.md`,
`staging/p14_sec_failed.log`, `staging/p14_bandit_local.json`, `staging/p14_bandit_stderr.txt`,
`staging/p12e13_ci.json`. **All untracked, all under `staging/`.**

**Deliberately untouched** (before = after; no hash moved):

| File | Reason |
|---|---|
| 3 alembic migrations (`a1b2c3d4e5f6…`, `b2c3d4e5f6a7…`, `d4e5f6a7b8c9…`) | A1 grant absent |
| `tests/test_bandit_sast_gate_is_green.py` | A2 grant absent |
| `terraform/alb.tf`, `security_groups.tf`, `redis-only/main.tf` | infrastructure grant absent |
| `.github/workflows/05-security.yml`, `01-pr-check.yml` | **never** modify a gate to make CI pass |
| `backend_app/routers/orders.py`, `market.py` | D7-1 grant absent |
| `backend_app/backend/exchange_executor.py` | W-6 grant absent |
| `backend_app/backend/redis_manager.py` | `D-A23C-1` unresolved |
| `tests/test_a23_redis_pool_lifecycle.py` | `D-A23C-7` unresolved — still **ABSENT** |
| `backend_app/main.py` | 3 contending workstreams, no coordinator |
| `staging/PHASE11B_A23E11_…md`, `tests/test_drift_classification.py` | **another session's work — preserved** |

**Authorizations relied upon: none.** No grant was exercised.

---

## 9. Release recommendation

**DO NOT DEPLOY. Keep `main` as-is; do not revert.**

Supported by: `03 Deploy` self-blocked at its own security gate; ECS deploy skipped; `05 Security`
failing on two independent scanners; and **no test, Redis, PostgreSQL or flake8 evidence at this
SHA**. The commit is **unverified**, not known-bad — which is why reverting 3,723 files would be the
wrong instrument.

**Paper/retail launch readiness is unchanged by this phase. Live trading remains OFF / NOT
CERTIFIED**, and tenant isolation remains **NOT CERTIFIED**.

---

## 10. Owner decision brief — smallest actions that cut release risk

| # | Decision | Scope | Owner | Why first |
|---|---|---|---|---|
| **1** | Grant **A1 + A2** (§3.4) | 3 alembic files + `tests/test_bandit_sast_gate_is_green.py` | code owner | Clears 1 of 2 failing security jobs. Safety premise already proven (§3.2) and the fix is comments + a test-scope widening. **A2 prevents recurrence** — without it the next unsuppressed MEDIUM goes unnoticed again |
| **2** | Decide Trivy disposition | accept-and-document `AWS-0054` + the 2 conventional `AWS-0104`; tighten the **2 Redis egress rules** | infrastructure owner | Clears the second failing job. The Redis tightening is near-zero-risk; the others need VPC endpoints and should not be rushed |
| **3** | Authorize a PR carrying `ecd23a9e` | branch + PR only, no code change | coordinator | The **only** supported route to test/Redis/PostgreSQL/flake8 evidence (§4.2). Also unblocks QUOTA-01 at no extra cost |
| **4** | Grant **D7-1** | `routers/orders.py`, `routers/market.py` + 1 test | router owner | Highest confirmed functional defect; `market.py:53` silently serves authenticated users anonymous data. Proven to lose no behaviour |
| **5** | Grant **W-6** tests | `exchange_executor.py` + 1 new test file | execution owner | Default lane currently asserts nothing about classification |
| **6** | A23 P1 | register row establishing the role, **then** the grant | code owner | Two steps, per A23E14's precedent finding |

Actions 1–3 need **no** application-code change and together would move `main` from "unverified and
red" to "verified or honestly red with evidence."

---

## 11. Status

| Item | State |
|---|---|
| Bandit gate | **diagnosed + reproduced locally.** 9 false positives; **root cause = one-file test scope (§3.3)**; unfixed, grant ABSENT |
| Trivy IaC gate | **diagnosed.** `AWS-0054` false positive w/ enforced precondition; `AWS-0104` ×4 real-conventional, 2 worth tightening; unfixed, grant ABSENT |
| `03 Deploy` | **FAILED at its own gate; ECS SKIPPED — nothing deployed** |
| `01 PR Check` at HEAD | **NOT RUN** — and PR-only, so undispatchable |
| D7-1 / W-6 | confirmed, unfixed, grants ABSENT |
| QUOTA-01 / real Redis | **UNEXECUTED** — CI infra exists; lane untriggered |
| TENANT-01 | **OPEN, static-only — NOT CERTIFIED** |
| A23 P1 | **NOT STARTED — 0 of 8** |
| Workers | scheduler + watchdog running; expiry worker + `ExchangeReconciliationService` **not started** |
| pytest runs this phase | **0** |
| **Live trading** | **OFF / NOT CERTIFIED** |

No gate was weakened. No approval was inferred from this prompt, from the commit or its push, from
role labels, from commit authorship, from file cleanliness, from CI results, or from silence.
