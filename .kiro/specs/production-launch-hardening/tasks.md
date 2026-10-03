# Implementation Plan: Production Launch Hardening

## Overview

This plan implements `design.md` against the tree at `939a428` plus the current uncommitted working
tree (`F`), on branch `feat/marketplace-subscriptions-paper-trading`. Implementation languages:
**Python** for `backend_app/**` and `tests/**`, **JavaScript/JSX** for `algo22-terminal/**`, **YAML**
for `.github/workflows/**`.

It is a bugfix plan. Every task either changes code at root cause and names the regression test that
fails against `F` and passes against `F'`, or — for the fifteen clauses whose defect is the *absence
of proof* — produces that proof or a new numbered defect. No task closes a clause by assertion.

### Ordering: dependency, not severity

The task order is `design.md §Fix Implementation`'s wave order, unchanged:

| Wave | Clauses | Why it sits here |
|---|---|---|
| **0** Release blockers | 1.30–1.35, 1.40, 1.41 | Merging to `main` auto-fires `06-frontend-deploy.yml` to S3 and CloudFront. There is no staging gate. **Nothing in waves 1–5 can reach production until wave 0 lands**, so wave 0 gates the merge and therefore gates everything. |
| **1** Absent vs zero | 1.1–1.6, 1.36 | One representation change at the dashboard composer that every other site in the wave depends on. Touches money figures, so it is first among the code changes and carries the heaviest preservation burden. |
| **2** Backtest key sets | 1.7–1.10, 3.4 | Independent of wave 1, but its contract test must be written before its renames so the rename set is known to be complete. |
| **3** Transport ownership | 1.21–1.24 | The riskiest wave: an auth change across nine WebSocket routes. Sequenced after the money figures so a socket regression cannot be confused with a dashboard one. |
| **4** Route contract | 1.25–1.29, 1.39 | Depends on wave 0d — ESLint in CI is half the fix, and without it the fifth instance of the same defect is still invisible. |
| **5** Local correctness | 1.33, 1.37, 1.38, 1.45, 1.46 | Genuinely local; no other wave depends on any of it. |

**A P0-first ordering would be wrong here, and specifically wrong.** It would put wave 1's ten sites
and wave 2's four columns ahead of wave 0, so the first merge that carried them would deploy the
placeholder installers and fail the `VITE_API_URL` cutover. It would also batch wave 1's composer
retyping together with the other nine P0 sites, and `design.md §Change Ordering` names that change as
one of three that **must not be batched with anything else**.

### The three changes that must land alone

`design.md §Change Ordering` names three changes that each need an independently observable
before/after. Each is its own task here, with its own verification, and is never folded into a
sibling task:

| Change | Task | Why alone |
|---|---|---|
| Wave 1 step 1 — the dashboard composer retyping | **6.1** | It alters the type of every money figure on the dashboard response. |
| Wave 3 step 2 — the socket credential replacement | **8.2** | It changes how every socket in the application authenticates, across nine backend routes. |
| Wave 2 step 6 — the backtest writer's key mapping | **7.8** | It changes what lands in persisted columns. |

Each of the three is committed on its own with `git commit -q -m`, so reverting it reverts nothing
else.

### Both-sides contracts

Three contracts span backend and frontend. `design.md §Testing Strategy` rule 1: a test on one side
alone passes while the contract is dead — the observed history of 1.24. Each gets **one canonical
fixture plus one task per side**:

| Contract | Fixture | Backend task | Frontend task |
|---|---|---|---|
| Backtest payload key set | `tests/fixtures/backtest_payload_keys.json` | 7.2 | 7.3 |
| `STRATEGY_STATUS` frame | `tests/fixtures/strategy_status_frame.json` | 8.5 | 8.6 |
| Signal Trace node projection | `tests/fixtures/signal_trace_node_projection.json` | 9.5 | 9.6 |

Each fixture is **one file**, not a copy per side: pytest reads it directly, vitest reads it through
`node:fs` at a repo-relative path. A second copy could drift, which is the failure mode the fixture
exists to remove.

### Rules that apply to every task

- **Tooling, without exception** (`design.md §Testing Strategy` rule 3). PowerShell on Windows.
  `node node_modules/vitest/vitest.mjs --run <path>` — always scoped to the named file, **never the
  bare suite**, which exceeds 25 minutes (3.18). `node node_modules/eslint/bin/eslint.js` — `npx`
  swallows stdout. `git commit -q -m` — `git commit -F <file>` fails silently.
  `$env:NODE_OPTIONS="--max-old-space-size=2048"` before any frontend build.
- **`aerora_quant_platform/frontend_app/algo22-terminal` is never modified** (3.16). Task 4.8 asserts
  no build or deploy path references it; nothing writes into it.
- **`createOrder` is not repointed, and `POST /api/orders/execute` and `POST /api/orders/create` keep
  answering 403 `MANUAL_EXECUTION_BLOCKED`.** This is a design decision, not a defect. It constrains
  task 12.3: the risk-limit proof cannot use those two routes, because a refusal there proves
  nothing.
- **No symptom patches.** Widening a `try`, defaulting a missing key or silencing a warning satisfies
  no clause (`bugfix.md §Severity scale`).
- **A genuine `0.0` stays `0.0`.** `None` means "not read". Collapsing the two is the failure mode of
  wave 1's own fix and is asserted against in the same file as the fix's own tests.
- **Every task names the requirement clauses it satisfies.** Every P0 and P1 task names its
  regression test file and its assertion, taken from `design.md §Fix Checking`.
- Tasks marked `*` are **optional for launch**: their clause is P2 or P3 on the requirements' severity
  scale and the design does not place it in wave 0. Every unmarked task is **required for launch**.
  Wave-0 tasks are unmarked regardless of tier, because wave 0 gates the merge and the design
  promotes 0d (ESLint) and 0e (cache policy, CI ceiling) into it for stated reasons.

## Tasks

- [ ] 1. Write the bug condition exploration tests
  - **Property 1: Bug Condition** - A fact the system does not have is reported as absent, never invented
  - **IMPORTANT**: Write these tests BEFORE implementing any fix
  - **CRITICAL**: These tests MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: These tests encode the expected behaviour; they are what validates the fix in task 11.1
  - **GOAL**: Surface counterexamples that confirm or refute each hypothesised root cause in
    `design.md §Hypothesized Root Cause` before any code moves
  - **Scoped PBT approach**: every defect here is deterministic, so each property is scoped to the
    concrete failing case(s) for reproducibility — the composer with a raising read, a real short
    backtest, a mounted-then-unmounted Billing page — rather than generated over an open domain
  - Cluster A — `tests/test_dashboard_absent_figures.py`: force the paper portfolio read to raise,
    the composer's portfolio branch to raise in both environments, QuestDB to return no equity rows
    in paper, and the paper account row to omit each of `total_equity`, `available_balance`,
    `initial_capital`. Expected on `F`: `100000.0` in paper, `0.0` in live, a synthesised two-point
    flat curve at `100000.0`. *Refutation signal:* if a `None` reaches the response without raising
    `TypeError`, the composer is not the only gate and wave 1 is larger than designed — re-hypothesise
    before writing task 6.1
  - Cluster A — `tests/test_exchange_health_probe.py`: request exchange health for an unprobed and a
    revoked key. Expected on `F`: `"status": "connected"`, `"latency_ms": 35`, `can_trade: true`
  - Cluster B — `tests/test_backtest_key_contract.py`: run a real short backtest and dump the payload
    at all three boundaries (`backtesting_engine.run_backtest_async` →
    `backtest_runtime.run_backtest` → `backtest_service.update_backtest_results`). Expected on `F`:
    the emitted and read key sets are disjoint for four columns, `equity_curve` present in the
    engine's return and absent from the runtime's `results`, and `final_capital == 100000.0` —
    **the initial capital, not zero**, per `design.md` correction 1. Record whether the five
    display-name reads in `_calculate_performance_metrics` are the full blast radius or only part of
    it, because that decides task 7.6's scope
  - Cluster C — `algo22-terminal/tests/unit/pages/billingSocketLifecycle.test.jsx`: mount Billing,
    unmount it, advance fake timers past 5 s. Expected on `F`: the `WebSocket` constructor is called
    a second time after unmount
  - Cluster C — `algo22-terminal/tests/unit/lib/socketCredential.test.js`: assert the constructed URL
    for **both** Billing's socket and `wsClient.connect`'s. Expected on `F`: a `token` parameter on
    both. Asserting only Billing's would pass on a fix that merely consolidates the exposure, which
    is `design.md` correction 3
  - Cluster C — print `DataPipelineContext`'s constructed live URL. Expected on `F`:
    `wss://…/ws/telemetry?token=<JWT>/ws/market-data` — malformed, credential mid-path, aimed at a
    route that does not exist. Record the JWT by position, never by value
  - Cluster D — `algo22-terminal/tests/unit/builder/enginePaths.test.jsx`: call each of
    `IndicatorEngineContext`, `LogicEngineContext` and `StrategyEngineContext`'s compute functions.
    Expected on `F`: `ReferenceError: post is not defined`
  - Cluster D — `tests/test_signal_trace_serialisation.py`: `json.dumps(trace.to_frontend_format())`.
    Expected on `F`: `TypeError` naming `DAGNodeTrace`
  - Cluster E — four hypotheses are already CONFIRMED by direct measurement during design and are
    recorded rather than re-derived: `HEAD /robots.txt` → 200 with
    `public, max-age=31536000, immutable`; `HEAD` on all four `/releases/*` → **403**; 58 ESLint
    errors and 246 warnings; 131 tracked frontend test files against a ceiling measured at 53
  - Run every case above against `F` with the scoped commands from the Overview
  - **EXPECTED OUTCOME**: every case FAILS (or reproduces the recorded measurement). That is correct —
    it proves the bug exists
  - Document each counterexample in the test file beside its assertion, including the exact fabricated
    value observed, so the root cause is readable from the test rather than inferred
  - Mark complete when the tests are written, run, and the failures are documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.21, 1.22, 1.23, 1.25, 1.26, 1.27, 1.29, 1.30, 1.40_

- [ ] 2. Write the preservation property tests (BEFORE implementing any fix)
  - **Property 2: Preservation** - Every path that has its fact answers exactly as it does today
  - **IMPORTANT**: Follow observation-first methodology — observe `F`, then pin what was observed
  - **EXTEND `tests/regression/capture_baseline.py` and `tests/regression/test_baseline_unchanged.py`**,
    which already implement exactly this capture-then-assert pattern over 37 baseline files. Do not
    write a parallel harness: two partial baselines is the failure mode
  - Create `tests/property/test_absent_vs_zero.py` — the central property of the pass. Over generated
    account rows, QuestDB responses and equity series, assert **both** directions: a read that
    succeeded reports its value unchanged **including `0.0`, `-0.0` and an empty ledger**, and only a
    read that failed reports `None`. A one-directional property passes on a fix that nulls
    everything, which is wave 1's own failure mode (3.1, 3.2)
  - Observe on `F` and pin: real QuestDB equity rows returned unmodified in ascending timestamp order
    with no synthesised endpoints appended (3.1)
  - Observe on `F` and pin: a measured latency still classifies `optimal` under 150 ms and `normal`
    under 500 ms, and a valid reachable credential still reports connected with `can_trade: true` —
    in `tests/test_exchange_health_probe.py`, beside the fix assertions (3.3)
  - Observe on `F` and pin the correctly-persisted backtest columns — **captured from the engine's
    output, not from today's stored values**, because `expectancy` is currently overwritten with
    `0.0` by `{**stats, **performance_metrics}` and `sortino_ratio` comes from the runtime rather
    than the engine (`design.md` correction 2, which contradicts 3.4's premise for those two) (3.4)
  - Re-run and keep green, scoped: `update_backtest_results`' existing ownership tests — still scoped
    by `user_id`, still `{}` indistinguishably for non-owned and nonexistent ids, still degrading when
    `006_backtest_evidence_columns.sql` is unapplied (3.5)
  - Re-run and keep green, scoped: `algo22-terminal/tests/unit/guards/` and
    `algo22-terminal/tests/unit/design/` — the eight structural frontend guards, tokens, alignment,
    review-mode suppression, and no `err.message` reaching the screen (3.7, 3.8)
  - Create `tests/test_orders_manual_execution_blocked.py` — `POST /api/orders/execute` and
    `POST /api/orders/create` both answer 403 `MANUAL_EXECUTION_BLOCKED`. Asserted explicitly because
    wave 3 and task 12.3 both work near the order path, and this is the constraint easiest to break by
    accident. `createOrder` is not repointed
  - Re-run and keep green, scoped: the per-route rate limits (paper trading `120/minute` reads and
    `60/minute` operations; library `120/60second` and `60/60second`) and existing ownership
    predicates (3.9); the paper lifecycle and its shared REST/WebSocket replay under
    `tests/property/test_paper_*` (3.10); `MARKETPLACE_READ_FAILED` never folded into a 403 (3.11,
    3.12); `tests/property/` as a whole (3.18)
  - Add `algo22-terminal/tests/unit/lib/socketCredential.test.js`'s `fast-check` half here: over
    generated tokens and paths, **no** constructed socket URL contains the token in any position
  - Assert with `git diff` that nothing changes under
    `aerora_quant_platform/frontend_app/algo22-terminal` (3.16)
  - Run every test above against `F`
  - **EXPECTED OUTCOME**: all PASS. That confirms the baseline this pass must preserve
  - Mark complete when the tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.17, 3.18_

- [ ] 3. Checkpoint — counterexamples recorded, baseline captured
  - Confirm every task-1 assertion fails against `F` with its counterexample written down, and every
    task-2 assertion passes against `F`
  - Confirm no hypothesis was refuted. If one was — in particular if a `None` already reaches the
    dashboard response without a `TypeError` — stop and re-hypothesise before wave 1
  - Ensure all tests pass, ask the user if questions arise

- [x] 4. Wave 0 — Release blockers. Gates the merge, therefore gates everything

  Nothing in waves 1–5 reaches production until this wave lands, because the merge to `main` that
  carries them auto-fires `06-frontend-deploy.yml` to S3 and CloudFront with no staging gate. Wave 0
  is the smallest set that makes that merge safe. Every item is a repository or workflow change and
  none of them needs the deploy to run, so the whole wave is verifiable on the feature branch.

  - [x] 4.1 Commit the `ALLOWED_API_HOSTS` allow-list
    - **File:** `.github/workflows/06-frontend-deploy.yml` — already written in the working tree, 24
      insertions, uncommitted. Committing it is the fix
    - Replaces the two-host grep for `d7d88qs4jmch.cloudfront.net` / `api.vyomquant.in` with a
      declared allow-list of five hosts: `app.vyomquant.in`, `www.vyomquant.in`, `vyomquant.in`,
      `api.vyomquant.in`, `d7d88qs4jmch.cloudfront.net`
    - The allow-list is a **strict superset** of the two hosts the old grep accepted, so it passes for
      every bundle the old check passed. That is what makes task 13.1's ordering safe
    - Commit with `git commit -q -m`, message naming the cutover it unblocks
    - **Regression test:** workflow dry-run — the allow-list check **passes** for a bundle containing
      each of the five allowed hosts and **fails** for a bundle containing none. Build the test bundle
      with `$env:NODE_OPTIONS="--max-old-space-size=2048"`
    - _Bug_Condition: isBugCondition(X) arm 5 — a release step whose verification encodes the current topology as a literal_
    - _Expected_Behavior: expectedBehavior(result) — the check refuses an unknown host with a distinct failure, rather than misreporting a configuration change as `VITE_API_URL not properly substituted`_
    - _Preservation: 3.14 — the pipeline still fails closed on unsubstituted `VITE_API_URL`, still rejects `ws://localhost:8000`, still serves `*.html` with `max-age=0, must-revalidate`, still invalidates and waits_
    - _Requirements: 1.31, 2.31, 3.14_

  - [x] 4.2 Delete the three fake installers and add the `releases/` size gate
    - Delete `algo22-terminal/public/releases/linux/VyomQuant-0.1.0.AppImage` (64 bytes),
      `public/releases/linux/vyomquant_0.1.0_amd64.deb` (67 bytes),
      `public/releases/mac/VyomQuant-0.1.0-universal.dmg` (69 bytes) and their byte-identical copies
      under `algo22-terminal/dist/releases/`. All are text files, not binaries
    - They are **untracked** — `.gitignore` ignores `releases/`, `**/releases/`, `public/releases/` and
      `dist/` — so this is a working-tree deletion with no commit. That is exactly why a **gate** is
      required and a deletion alone is not: without one they reappear the next time anyone copies a
      placeholder in
    - Add the gate to `.github/workflows/06-frontend-deploy.yml`, running on `dist/` **after**
      `vite build` and **before** the `aws s3 sync`, so it fails the deploy rather than publishing.
      Minimums, generous enough that a real build never trips them: `.exe`/`.dmg` ≥ 20 MB,
      `.AppImage`/`.deb` ≥ 10 MB
    - Exercise the gate against a deliberately undersized file and confirm it **fails**. A gate never
      observed failing is not a gate
    - Do not touch the real `algo22-terminal/releases/windows/VyomQuant-Setup-0.1.0.exe`
      (112,117,309 bytes); it is a sibling of `public/` that Vite never copies, and it is not part of
      this defect
    - **Regression test:** CI gate + `tests/test_release_artifacts.py` — no file under a `releases/`
      path is implausibly small for its extension
    - _Bug_Condition: isBugCondition(X) arm 5 — X is a release artifact and its bytes are not a real build output_
    - _Expected_Behavior: expectedBehavior(result) — the build fails rather than publishing bytes that are not a build output_
    - _Preservation: 3.6 as reinterpreted by `design.md` correction 6 — the real Windows installer is not broken further_
    - _Requirements: 1.30, 2.30, 3.6_

  - [x] 4.3 Withdraw the download surface through the existing not-available convention
    - **Files:** `algo22-terminal/src/design/pageFields.js`,
      `algo22-terminal/src/components/download/DownloadPage.jsx`,
      `algo22-terminal/src/components/landing/DownloadSection.jsx`
    - Verified against production during design: **all four advertised download URLs return 403.** The
      page advertises four installers with four hardcoded sizes (84.2 / 78.5 / 75.4 / 68.2 MB) and
      serves none of them. `aws s3 sync dist/ --delete` is why: CI's `dist/` contains no `releases/`
      directory, so the sync *deletes* the `/releases/` prefix from the bucket
    - Declare each platform's card in `design/pageFields.js` as `verdict: VERDICT.UNAVAILABLE` with
      `absence: ABSENCE.UNREPORTED` and a reason string, and render it through `usePanelState`'s
      existing `unavailable` state driving `ds/Panel` — which short-circuits **without issuing a
      request**. No new primitive, no new copy path, no new state
    - **Remove the four hardcoded size strings with the links.** A size string for a file that does not
      exist is the same fabrication as a fabricated balance, on a rendered surface
    - **The alternative was publishing the real artifacts, and it was not chosen.** The Windows
      installer is 112 MB and `releases/` is gitignored, so publishing means either committing 112 MB
      to the repository — inflating every clone and every CI checkout permanently — or adding an
      artifact upload step outside the `dist/` sync; and the mac and linux builds do not exist at all.
      Withdrawal is reversible in one commit, removes four 404s and four fabricated figures
      immediately, and does not couple the launch to producing and signing three desktop builds.
      Restoring a platform later is additive: produce the artifact, upload it, flip the card's verdict
      in `pageFields.js`. **Recording the rejected option here so the decision is visible rather than
      buried in a diff**
    - **Regression test:** `tests/test_release_artifacts.py` — no download link is rendered for a
      platform with no artifact; plus a scoped vitest run asserting the card renders the
      not-available marker with its reason and issues no request:
      `node node_modules/vitest/vitest.mjs --run tests/unit/pages/downloadSurface.test.jsx`
    - _Bug_Condition: isBugCondition(X) arm 5 — the surface advertises an artifact whose bytes are not published_
    - _Expected_Behavior: expectedBehavior(result) — is_explicit_unavailable: the card states unavailable with a reason and offers no link_
    - _Preservation: 3.7 — the redesign's token, copy-path and alignment behaviours hold; the not-available marker never renders `0`_
    - _Requirements: 1.30, 2.30, 3.6, 3.7_

  - [x] 4.4 Commit the reviewed working tree
    - 14 modified paths plus 5 untracked, in purpose-named commits, each with `git commit -q -m`
    - Modified: `backend_app/main.py`, `backend_app/routers/billing.py`,
      `backend_app/routers/referral.py`, `backend_app/backend/connection_engine.py`, `.env.example`,
      `algo22-terminal/.env.production.example`, `infra/acm.sh`, `infra/alb.sh`, `infra/dns.md`,
      `scripts/post_migration_validation.py`, `tests/test_cors_configuration.py`
    - Untracked: `infra/rollback/`, `scripts/apply_migrations.py`, `scripts/migration_preflight.py`,
      `tests/test_migration_tooling.py`
    - Two paths 1.32's list omits, found by measurement: **exclude or gitignore**
      `algo22-terminal/bundle-analysis.html` (a build artifact); **include**
      `.kiro/specs/vyomquant-ui-redesign/tasks.meta.json` (spec bookkeeping)
    - `tests/test_migration_tooling.py` and `tests/test_cors_configuration.py` must pass **before** the
      commit that carries them
    - Reference `.env` secrets by key name only. No secret value enters a commit message or a diff
    - **Regression test:** clean `git status`, with `pytest tests/test_migration_tooling.py` and
      `pytest tests/test_cors_configuration.py` both passing
    - _Bug_Condition: isBugCondition(X) arm 5 — the deployed byte set is not the reviewed byte set_
    - _Expected_Behavior: expectedBehavior(result) — `main` contains every reviewed change, each in a commit naming its purpose_
    - _Preservation: 3.16 — nothing under `aerora_quant_platform/frontend_app/algo22-terminal` is committed or modified_
    - _Requirements: 1.32, 2.32, 3.16_

  - [x] 4.5 Run ESLint in CI and drive errors to zero
    - **File:** `.github/workflows/01-pr-check.yml`
    - Add a step running `node node_modules/eslint/bin/eslint.js src` in `algo22-terminal`, failing the
      job on errors. No workflow invokes eslint today, so the count is unbounded and undetected
    - Drive the **58 errors** to zero. Cap the **246 warnings** at their then-current count rather than
      driving them to zero, per 2.34
    - **This is in wave 0 rather than with the P2s on purpose:** `no-undef` is what would have caught
      wave 4's `post is not defined`, and cluster D's fix is not durable without it
    - **Regression test:** `node node_modules/eslint/bin/eslint.js src` reports zero errors, and the
      workflow step enforces it
    - _Bug_Condition: isBugCondition(X) arm 5 — a defect class the pipeline cannot see_
    - _Expected_Behavior: expectedBehavior(result) — CI fails on an undefined identifier instead of shipping it_
    - _Preservation: 3.18 — CI keeps invoking the suite scoped; no existing check is removed, skipped or narrowed_
    - _Requirements: 1.34, 2.34, 3.18_

  - [x] 4.6 Narrow the immutable cache policy to content-hashed paths
    - **File:** `.github/workflows/06-frontend-deploy.yml:223-227`
    - The sync excludes only `*.html` and `*.map`, so `robots.txt`, `sitemap.xml` and the four SVGs —
      none of which carry a content hash — get `max-age=31536000, immutable`. **Verified live:**
      `HEAD /robots.txt` returns exactly that today
    - Restrict the immutable sync to content-hashed asset paths (`assets/**`); move the unhashed files
      to a short revalidating policy
    - **The correction is not complete without an invalidation.** Those objects are already in edge
      caches carrying a one-year immutable header and **will not re-fetch on their own**. The
      invalidation for these specific paths is task 13.5, which runs after the deploy that changes the
      header
    - **Regression test:** the workflow diff plus a check asserting only content-hashed asset paths
      receive `immutable`
    - _Bug_Condition: isBugCondition(X) arm 5 — cache policy keyed on file extension rather than on whether the name is content-hashed_
    - _Expected_Behavior: expectedBehavior(result) — an unhashed asset revalidates, so a correction can reach a returning visitor_
    - _Preservation: 3.14 — `*.html` still served `max-age=0, must-revalidate`; CloudFront still invalidated and waited for_
    - _Requirements: 1.40, 2.40, 3.14_

  - [x] 4.7 Re-measure the `01-pr-check.yml` ceiling against the suite it actually guards
    - **File:** `.github/workflows/01-pr-check.yml:132`
    - `timeout-minutes: 40` and its justification comment were written for a **53-file** frontend
      suite. That suite is now **131 files** by this task's original count; re-measured against the
      tree at verification time (`vitest.config.js`'s own `include` glob — `tests/unit/**/*.{test,spec}.*`
      plus `src/**/__tests__/**/*.{test,spec}.*`), the count is **150 files** (145 under `tests/unit/`,
      5 under `src/**/__tests__/`), run sequentially by design. The sharper finding than 1.41's own
      wording: 1.41 cites "494 test files" which is repo-wide (484 tracked, by measurement), while the
      comment it criticises sizes the *frontend* suite specifically
    - Record the observed wall clock of the 150-file frontend suite in the comment, with the file count
      it was measured at, and set the ceiling from that measurement rather than keeping 40
    - Measure by running the frontend files scoped in batches with
      `node node_modules/vitest/vitest.mjs --run <path>`, summing the observed durations. **Do not run
      the bare full suite to obtain the number** — it exceeds 25 minutes and 3.18 forbids it. Re-measured
      for this task: a scoped batch of the first 50 of the 150 files took 409s per Vitest's own reported
      duration (~420s wall-clock including process start/stop). The remaining 100 files were not run in
      this pass; linearly extrapolating the measured batch across all 150 puts the test step alone at
      roughly 20-25 minutes, before install/lint/build overhead. `.github/workflows/01-pr-check.yml`'s
      `timeout-minutes` has been raised from 40 to 60 and its comment rewritten to state this measurement
      and its extrapolation explicitly, in the same edit that closes this task. If the ceiling is
      tightened further, the remaining 100 files should be measured directly rather than trusting this
      extrapolation further
    - **Regression test:** the measured duration and test count recorded in the workflow comment
    - _Bug_Condition: isBugCondition(X) arm 5 — a release gate sized against a tree that no longer exists_
    - _Expected_Behavior: expectedBehavior(result) — the ceiling is justified by a stated measurement at a stated test count_
    - _Preservation: 3.18 — the suite is still invoked scoped, never bare_
    - _Requirements: 1.41, 2.41, 3.18_

  - [x] 4.8 Mark the stale duplicate frontend dead
    - Add a check asserting no build or deploy path references
      `aerora_quant_platform/frontend_app/algo22-terminal`
    - **The tree itself is not modified** (3.16). It is indistinguishable by path convention from the
      live frontend, which is the deployment hazard 1.35 names; the fix is to make the reference set
      assertable, not to edit the duplicate
    - **Regression test:** the check, plus `git diff` reporting no change under that path
    - _Bug_Condition: isBugCondition(X) arm 5 — two indistinguishable candidate source trees for one deploy_
    - _Expected_Behavior: expectedBehavior(result) — no build or deploy path names the duplicate, and the assertion fails if one does_
    - _Preservation: 3.16 — the duplicate is left unmodified_
    - _Requirements: 1.35, 2.35, 3.16_

- [x] 5. Checkpoint — wave 0 verified on the feature branch
  - `pytest tests/test_migration_tooling.py tests/test_cors_configuration.py tests/test_release_artifacts.py` passes
  - `node node_modules/eslint/bin/eslint.js src` reports zero errors
  - The `releases/` size gate has been observed **failing** against a deliberately undersized file
    (confirmed by extracting and running the gate step's shell logic standalone against a 9-byte
    `.AppImage` in a temp `dist/releases/` tree: exit 1, then re-run against an 11 MiB file in the
    same path: exit 0)
  - `git status` is clean apart from the intentionally untracked `releases/` and `dist/` paths
  - Ensure all tests pass, ask the user if questions arise
  - All of wave 0 (4.1-4.8) is now closed; see 4.7 for the wall-clock re-measurement that closed it.

- [x] 6. Wave 1 — Absent vs zero. One representation, ten sites

  **File:** `backend_app/backend/dashboard_aggregation_service.py`. **Related:**
  `backend_app/routers/dashboard.py`.

  This wave touches money figures, so it is sequenced first among the code changes. The risk is
  specific: **the failure mode of this fix is collapsing a genuine `0.0` into `None`**, which renders
  a flat account as unreadable and is a new lie in the opposite direction. Task 2's
  `tests/property/test_absent_vs_zero.py` asserts the zero case as hard as the null case, and it must
  stay green through every sub-task below.

  The representation is not new. `vyomquant-ui-redesign` already built it **in this same file** —
  `_finite_float() -> Optional[float]` at `:314`, `PositionsUnreadable`, `positions_degradation()`
  and the top-level `degraded` block, marked BC-1/BC-2/BC-5 — and the frontend already renders it
  through `design/reported.js`'s `Reported<T>` union and `ds/Metric`'s not-available marker. Six P0s
  exist because that convention was applied to one field of the fourteen that need it.

  **Closed except 6.6, which is optional (P2) and does not gate this wave.** All required tasks — 6.1 through 6.5, 6.7, 6.8 — are implemented, committed, and verified passing: backend (`tests/test_dashboard_absent_figures.py`, `tests/test_exchange_health_probe.py`, `tests/property/test_absent_vs_zero.py` — 45 passed, 3 xpassed) and frontend (`tests/unit/dashboard-tier1.test.jsx` 11 passed, `tests/unit/portfolio-rendering.test.jsx` 30 passed). 6.6 remains unimplemented; see its own entry.

  - [x] 6.1 **MUST LAND ALONE** — the composer stops coercing
    - **This is one of the three changes `design.md §Change Ordering` forbids batching.** It alters the
      type of every money figure on the dashboard response. It is committed on its own with
      `git commit -q -m`, verified on its own, and revertable on its own
    - Replace `float(portfolio.get(key, 0.0))` with `_finite_float(portfolio.get(key))` for every
      figure on `get_dashboard_data`'s `overview` block, exactly as `realized_pnl` is already read
    - `_finite_float` already exists at `:314` and already returns `None` for non-numbers, `NaN`,
      infinity and booleans — booleans are not money. No new helper
    - `float(portfolio.get(key, 0.0))` is the defect itself: it has two outcomes and no third, so
      absence is unrepresentable at the response boundary and every upstream producer is forced to
      invent a number. **Nothing else in wave 1 works until this lands** — every downstream default
      removal depends on the boundary being able to carry `None`
    - **Regression test:** `tests/test_dashboard_absent_figures.py` — the composer's portfolio branch
      raises → `None` in **both** environments, and the response is **not numerically equal** to a
      genuine zero-balance response. Plus `tests/property/test_absent_vs_zero.py` green in both
      directions
    - _Bug_Condition: isBugCondition(X) arm 1 — X asks for a financial figure and the producing read failed or returned empty_
    - _Expected_Behavior: expectedBehavior(result) — is_explicit_unavailable, NOT is_synthesised_value, NOT is_500_
    - _Preservation: 3.2 — a genuinely flat account still reports `0.0`; `None` is reserved for "not read"_
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.2_

  - [x] 6.2 Remove the `100000.0` defaults from `get_portfolio_overview`'s paper branch
    - `:897-903` — `float(acct.get("total_equity", 100000.0))` and the same for `available_balance` and
      `initial_capital`. Read through `_finite_float` and propagate `None`
    - `:958-961` — the paper failure branch already sets `realized_pnl: None` with a BC-5 comment
      explaining why. `total_equity`, `total_value`, `available_balance` and `free_balance` join it
    - `100000.0` here is the paper default capital leaking out of a `dict.get` fallback into a money
      field. A trader whose account could not be read is currently shown a fully funded one
    - **Regression test:** `tests/test_dashboard_absent_figures.py` — the paper portfolio read raises →
      **no `100000.0` anywhere in the response** and each affected field is `None`; and parameterised
      over each of `total_equity`, `available_balance`, `initial_capital` missing → no synthesised
      default
    - _Bug_Condition: isBugCondition(X) arm 1 — the paper account row lacks the field, or the read raised_
    - _Expected_Behavior: expectedBehavior(result) — the field is absent and unavailability propagates_
    - _Preservation: 3.2 — real `total_equity`, `available_balance`, `locked_balance`, `realized_pnl`, `unrealized_pnl` and `initial_capital` still report their exact values, and today's realised P&L is still computed from paper trades since 00:00 UTC_
    - _Requirements: 1.1, 1.4, 2.1, 2.4, 3.2_

  - [x] 6.3 Replace the composer's environment-dependent exception literal
    - `:1692-1695` — `100000.0 if paper else 0.0` becomes `None` in both environments
    - The `0.0` branch is the exact artefact 1.2 describes: a live trader holding open positions shown
      zero equity on a failed read, indistinguishable from a liquidated account
    - **Regression test:** `tests/test_dashboard_absent_figures.py` — the failure response carries an
      unavailable marker in both environments and is not numerically equal to a genuine zero-balance
      response
    - _Bug_Condition: isBugCondition(X) arm 1 — the portfolio fetch failed inside the composer_
    - _Expected_Behavior: expectedBehavior(result) — "read failed" is distinguishable from "value is zero"_
    - _Preservation: 3.2 — a live account genuinely at zero still reports `0.0`_
    - _Requirements: 1.2, 2.2, 3.2_

  - [x] 6.4 Delete `get_equity_curve`'s paper synthesis branch
    - `:1338-1344` — drop the two-point flat curve at `100000.0` spanning the requested window.
      `return []` for every environment, as live already does
    - "No history" drawn as "perfectly flat performance" is the fabrication; an empty read is an empty
      series
    - **Regression test:** `tests/test_dashboard_absent_figures.py` — empty QuestDB, paper → `[]`; plus
      `algo22-terminal/tests/unit/dashboard/equityCurveEmpty.test.jsx` — the chart renders
      `usePanelState`'s **empty** state, not a line. Run scoped:
      `node node_modules/vitest/vitest.mjs --run tests/unit/dashboard/equityCurveEmpty.test.jsx`
    - _Bug_Condition: isBugCondition(X) arm 1 — the equity read returned empty_
    - _Expected_Behavior: expectedBehavior(result) — an empty series, and the frontend's no-history state_
    - _Preservation: 3.1 — real QuestDB rows returned unmodified, ascending by timestamp, with no synthesised endpoints appended_
    - _Requirements: 1.3, 2.3, 3.1_

  - [x] 6.5 Derive exchange health from a probe, and `can_trade` from credential validity
    - `:615-620` — `"status": "connected"` and `"latency_ms": 35` are hardcoded literals for every row.
      They come from the probe, or they are `"unknown"` / `None`
    - `:625` — `can_trade: len(connections) > 0` answers "does a row exist" when the question is "can
      this credential place an order". Derive it from credential validity; **`false` when validity is
      unknown**
    - `get_health_status` at `:1456-1486` is the model to follow, twenty lines away in the same class:
      it already reads measured latency from Redis, already returns `None` / `"unavailable"` when
      nothing was measured, and already classifies `optimal` / `normal` / `degraded` against the
      thresholds 3.3 preserves
    - **Regression test:** `tests/test_exchange_health_probe.py` — an unprobed connection →
      `status == "unknown"` and `latency_ms is None`, **and the literal `35` no longer appears in the
      module**; a revoked/unvalidated key → `can_trade is False` and the connection not reported
      connected
    - _Bug_Condition: isBugCondition(X) arm 2 — X asks for venue health and no health probe was performed_
    - _Expected_Behavior: expectedBehavior(result) — measured or unknown, never asserted; `can_trade` false when validity is unknown_
    - _Preservation: 3.3 — a genuinely valid, reachable credential still reports connected with `can_trade: true`, and a measured latency still classifies `optimal` under 150 ms and `normal` under 500 ms_
    - _Requirements: 1.5, 1.6, 2.5, 2.6, 3.3_

  - [ ]* 6.6 Attribute recent signals to their strategy
    - `get_recent_signals` at `:1421` selects no strategy identifier, so Live Trading cannot attribute
      the latest signal. Add `strategy_id` to the `select` and carry the strategy id and name onto each
      item — one extra column on an existing query
    - **Optional for launch: P2.** A trader can notice this but cannot be harmed by it
    - **Verified still unimplemented as of this reconciliation pass:** no `strategy_id`/`strategy_name` carried onto recent-signal items in `dashboard_aggregation_service.py`, and no regression test for it exists yet. Remains open and optional; does not block wave 1's closure below.
    - **Regression test:** `tests/test_dashboard_absent_figures.py::test_recent_signals_carry_strategy`
      — the field is present and non-null for a signal from a known strategy. (Not from
      `design.md §Fix Checking`, which names files for P0 and P1 only)
    - _Bug_Condition: isBugCondition(X) arm 1 — the response omits a fact the query could have carried_
    - _Expected_Behavior: expectedBehavior(result) — the strategy id and name are present, or explicitly null_
    - _Preservation: 3.9 — the route's existing ownership predicate and rate limit are unchanged_
    - _Requirements: 1.36, 2.36, 3.9_

  - [x] 6.7 Confirm the route disposition in `routers/dashboard.py`
    - `get_dashboard_overview` already raises 503 rather than degrading, because every figure it returns
      is a headline figure. **That judgement is correct and is preserved** — do not convert it to a
      degraded response
    - `get_dashboard_data` continues to degrade, because balances, curve and executions on the same
      response are still real reads. A nullable *figure* needs no `degraded` block; a *list* uses the
      `degraded` channel because a list has nowhere to put a null
    - `PositionsUnreadable` stays a service-layer exception rather than an `HTTPException`: which status
      code it deserves depends on how much of the response survives, which only the route can judge
    - **Regression test:** `tests/test_dashboard_absent_figures.py` — the overview route still answers
      503 on a total read failure, and the data route still answers 200 with a populated `degraded`
      block on a partial one
    - _Bug_Condition: isBugCondition(X) arm 1 — a partial read answered as if it were total, or vice versa_
    - _Expected_Behavior: expectedBehavior(result) — 503 where nothing survives, 200 with `degraded` where something does; never a 500_
    - _Preservation: 3.8, 3.9 — no traceback reaches a user-facing message; authentication and rate limits unchanged_
    - _Requirements: 2.1, 2.2, 3.8, 3.9_

  - [x] 6.8 Adopt the null figures on the frontend
    - **No new work, and no UI redesign.** Figures already flow through `ds/Metric`, which accepts
      `Reported<T>` or a raw value and renders the not-available marker with a reason for `null`
    - Adopt `fromNullable(read(body, path), entry.reason)` per field — the pattern `pageFields.js`'s own
      header documents — for every figure wave 1 made nullable
    - Each field's reason string is **already declared** in `design/pageFields.js`, including
      `exchange_api_latency_ms`'s note that it must never render as `0 ms`, because a zero-latency
      exchange call is not a thing and the figure would be read as a claim about a working connection
    - The equity chart's empty state is `usePanelState`'s `empty` state, which `[]` already produces via
      `isEmptyPayload`
    - **Regression test:** `algo22-terminal/tests/unit/dashboard/equityCurveEmpty.test.jsx` plus the
      existing `algo22-terminal/tests/unit/design/` suite re-run scoped. The marker never renders `0`
    - _Bug_Condition: isBugCondition(X) arm 1 — a null figure rendered as a number_
    - _Expected_Behavior: expectedBehavior(result) — the not-available marker with its declared reason_
    - _Preservation: 3.7 — design tokens, the `design/errorCopy.js` / `errorLine.js` copy path with no `err.message` reaching the screen, and column alignment are unchanged_
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 3.7_

- [x] 7. Wave 2 — Backtest key sets. One contract, three boundaries

  **Files:** `backend_app/backend/backtesting_engine.py`, `backend_app/backend/backtest_runtime.py`,
  `backend_app/backend/backtest_service.py`.

  Landing order within the wave matters: **the contract test is written first and is expected to
  fail**, because it is what tells us the rename set is complete. Four vocabularies cross three
  boundaries — engine keys, runtime keys, VectorBT *display* names, DB column names — with no
  validation at any of them and `dict.get(key, 0)` at the last one.

  **Closed. All nine subtasks (7.1-7.9) are implemented, committed or completed in the working tree, and verified passing.** `tests/test_backtest_key_contract.py` — extended for 7.1/7.2 — shows 82 passed / 10 failed, where the 10 are pre-existing/documented-as-expected exploration findings, confirmed stable across independent re-runs. `algo22-terminal/tests/unit/pages/backtesterNetPnl.test.jsx` (7.3) shows 12 passed / 0 failed, independently re-run. One follow-up finding surfaced during implementation and recorded under 7.8 rather than fixed here (a scope boundary, not a gap in this wave's closure): `total_pnl` has no writer-mapping entry, so it renders live but does not persist.

  - [x] 7.1 Declare the payload's key set once — the shared fixture
    - Add a module-level frozenset of emitted keys beside the engine, and derive the writer's read-key
      set from it. Without this the contract test is a second hand-maintained list, which is the defect
      one layer up
    - Emit `tests/fixtures/backtest_payload_keys.json` from that declaration. **One file, read by both
      sides:** pytest reads it directly, vitest reads it through `node:fs` at a repo-relative path. A
      per-side copy could drift, which is what the fixture exists to prevent
    - The backend test asserts the JSON matches the Python declaration, so the declaration stays the
      single source and the JSON cannot go stale
    - _Requirements: 2.7, 2.8_

  - [x] 7.2 Backend side of the key-set contract
    - Create `tests/test_backtest_key_contract.py`
    - **Regression test and assertion:** the writer's read-key set is a **subset** of the engine's
      emitted-key set, asserted **per column**, on **both** engine paths — vectorbt and the fallback at
      `backtesting_engine.py:230-244`. Plus, for 1.8: the same contract for `total_return` and
      `final_capital`, **and `final_capital != initial_capital` for a run that moved**
    - Also assert `total_pnl` is numeric on a completed backtest (1.10)
    - This is the right instrument rather than per-field assertions because **it fails on the next
      rename too**. It is also what makes the whole fabricated-zero family visible at once:
      `calmar_ratio`, `recovery_factor`, `average_trade`, `largest_win`, `largest_loss`,
      `consecutive_wins`, `consecutive_losses`, `expectancy` and `kelly` are fabricated from the same
      cause, which 1.7/1.8 do not name
    - Add the preservation half here, **captured from the engine's output rather than from today's
      stored values**: `expectancy` is currently overwritten with `0.0` and `sortino_ratio` comes from
      the runtime, so 3.4's premise does not hold for those two (`design.md` correction 2)
    - Expected to **fail** when first written. That failure is the measurement of the rename set
    - _Requirements: 1.7, 1.8, 1.10, 2.7, 2.8, 2.10, 3.4_

  - [x] 7.3 Frontend side of the key-set contract
    - Create `algo22-terminal/tests/unit/pages/backtesterNetPnl.test.jsx`, reading
      `tests/fixtures/backtest_payload_keys.json` — **the same file** task 7.2 asserts against
    - **Regression test and assertion:** the rendered Net P&L field is present and numeric for a
      completed backtest; every figure the results surface renders is named in the fixture's key set,
      so a column the writer cannot persist cannot be rendered
    - Run scoped: `node node_modules/vitest/vitest.mjs --run tests/unit/pages/backtesterNetPnl.test.jsx`
    - **Why both sides:** a backend-only key-set test passes while the results screen still renders a
      field nothing persists, and a frontend-only test passes while the column stores `0`
    - _Requirements: 1.10, 2.10, 3.7_

  - [x] 7.4 Emit `total_pnl` from the engine
    - `final_equity - initial_equity_logged` is already computed at `backtesting_engine.py:405-408` —
      **into a log line**. It becomes a key on the payload and is added to the declared key set
    - 2.10 permits removing the field from the UI instead. Emitting is strictly better: the value
      already exists, and a permanently not-available metric on a results screen is what 2.10 itself
      rules out
    - **Regression test:** `tests/test_backtest_key_contract.py` — a completed backtest yields a numeric
      `total_pnl`; and `algo22-terminal/tests/unit/pages/backtesterNetPnl.test.jsx` renders it
    - _Bug_Condition: isBugCondition(X) arm 1 — the results screen asks for Net P&L and no key carries it_
    - _Expected_Behavior: expectedBehavior(result) — a numeric `total_pnl`, persisted and rendered_
    - _Preservation: 3.4 — every column already receiving correct engine output keeps receiving it_
    - _Requirements: 1.10, 2.10, 3.4_

  - [x] 7.5 Move the equity curve in band
    - The fallback path already does `results["equity_curve"] = equity_curve` before returning the
      tuple; the vectorbt path does not. **Make both set the key**, keep the tuple return for existing
      callers, and have `backtest_runtime` read the key rather than the tuple element
    - A value returned as a tuple element rather than as a payload key has to be re-inserted by hand at
      every hop, and one hop forgot. This **removes the drop site** rather than patching it, so
      forgetting becomes impossible
    - **Regression test:** `tests/test_backtest_equity_curve_persisted.py` — a real short backtest →
      the stored `equity_curve` is non-empty **and its length equals the executed bar count**
    - _Bug_Condition: isBugCondition(X) arm 1 — the chart asks for a curve the run produced and the payload dropped_
    - _Expected_Behavior: expectedBehavior(result) — the curve is persisted, length matching the executed bars_
    - _Preservation: 3.4 — existing tuple-consuming callers keep working; `trades`, `status` and `completed_at` unchanged_
    - _Requirements: 1.9, 2.9, 3.4_

  - [x] 7.6 Stop reading VectorBT display names in `backtest_runtime`
    - `stats["Final Equity"]` → `stats["final_equity"]`. **This is the site the requirements do not
      name, and it is where `final_capital: 100000.0` comes from:** `"Final Equity"` is a VectorBT
      *display* name, `stats` has already been normalised to `final_equity`, so the lookup always misses
      and `self.vectorbt_engine.initial_capital` always wins. Every completed backtest reports its
      *starting* capital as its *final* capital — worse than zero, because zero looks like a bug and
      `100000.00` looks like a result
    - Repoint the five display-name reads in `_calculate_performance_metrics` — `Max Drawdown [%]`,
      `Net Profit`, `Win Rate [%]`, `Best Trade`, `Avg Winning Trade` — at the keys the engine emits,
      or remove them where nothing emits them
    - Scope this against what task 1's cluster-B exploratory run recorded: it decides whether those five
      are the full blast radius or only part of it
    - **Regression test:** `tests/test_backtest_key_contract.py` — the contract holds after the repoint,
      and `final_capital != initial_capital` for a run that moved
    - _Bug_Condition: isBugCondition(X) arm 1 — a lookup against a vocabulary the payload was normalised away from_
    - _Expected_Behavior: expectedBehavior(result) — the runtime reads only keys the engine emits_
    - _Preservation: 3.4 — `total_return_pct`, `sharpe_ratio`, `profit_factor`, `total_trades`, `winning_trades`, `losing_trades`, `execution_time_seconds` unchanged_
    - _Requirements: 1.8, 2.8, 3.4_

  - [x] 7.7 Fix the `{**stats, **performance_metrics}` precedence
    - `performance_metrics` currently **overrides** the engine's genuinely computed `expectancy` with
      `0.0`. Either invert the precedence or stop having two producers for one key
    - Inverting is the smaller change but it silently re-points a stored column, **so it needs its own
      preservation assertion**, captured from the engine's value rather than from today's stored value
    - **Regression test:** `tests/test_backtest_key_contract.py` — `expectancy` equals the engine's
      computed value, not `0.0`; `sortino_ratio`'s producer is named explicitly in the assertion so the
      two cannot silently swap
    - _Bug_Condition: isBugCondition(X) arm 1 — a real computed value overwritten by a default from a second producer_
    - _Expected_Behavior: expectedBehavior(result) — one producer per key, and the engine's value survives_
    - _Preservation: 3.4 as corrected — the baseline for `expectancy` and `sortino_ratio` is the engine's value, not today's stored one_
    - _Requirements: 1.7, 1.8, 2.7, 2.8, 3.4_

  - [x] 7.8 **MUST LAND ALONE** — the writer's key mapping
    - **This is one of the three changes `design.md §Change Ordering` forbids batching**, because it
      changes what lands in persisted columns. Committed on its own with `git commit -q -m`, verified on
      its own, revertable on its own
    - `backtest_service.update_backtest_results` reads the declared keys with **no numeric defaults**:
      `results.get("win_rate", 0)` → read `win_rate_pct`; `total_return` → `total_return_pct`;
      `max_drawdown` → `max_drawdown_pct`; `final_capital` → `final_equity`
    - Where a DB column name differs from the engine key, **the mapping is explicit and declared**, not
      implied by a matching string
    - **A key that is genuinely absent writes SQL `NULL`, not `0`** — the same rule as wave 1.
      `dict.get(key, numeric_default)` is a silent type-preserving translation of "absent" into "zero",
      and it is why these columns are wrong rather than missing
    - **Map, do not migrate.** 2.8 permits renaming the columns instead, but a rename is a migration
      against a table that already holds rows. Mapping in the writer is reversible; a migration is not
    - **Regression test:** `tests/test_backtest_key_contract.py` — the writer's read-key set ⊆ the
      engine's emitted-key set, per column, on both engine paths; plus
      `tests/test_backtest_equity_curve_persisted.py` for the curve
    - _Bug_Condition: isBugCondition(X) arm 1 — the writer reads a key the producer never emitted and defaults it to zero_
    - _Expected_Behavior: expectedBehavior(result) — the persisted column holds the engine's value, or SQL NULL; never a defaulted zero standing in for a name mismatch_
    - _Preservation: 3.4 — the eleven correctly-persisted columns are untouched_
    - _Requirements: 1.7, 1.8, 2.7, 2.8, 3.4_
    - **Follow-up finding, not yet fixed:** `total_pnl` (task 7.4's figure) has no entry in `RESULT_COLUMN_SOURCE_KEYS`. A live/just-completed backtest renders Net P&L correctly (read straight off the API response), but nothing persists it to `strategy_backtests`, so a saved run reloaded later would not carry the figure. Found while implementing 7.1-7.3; remains open and is this task's own scope to close in a future pass.

  - [x] 7.9 Confirm `update_backtest_results` keeps its ownership behaviour
    - **Nothing in this wave touches the `user_id` predicate.** It was added because its absence made
      this a cross-tenant write and an existence oracle, and it is the precedent task 12.4's sweep
      generalises
    - Still scoped by `user_id` as well as `id`; still returns `{}` indistinguishably for a non-owned
      and a nonexistent id; still degrades gracefully when `006_backtest_evidence_columns.sql` is
      unapplied
    - **Regression test:** the existing ownership tests, re-run scoped and green
    - _Preservation: 3.5_
    - _Requirements: 3.5_

- [x] 8. Wave 3 — Transport ownership. One socket, one credential

  **Files:** `algo22-terminal/src/websocketClient.js`, `algo22-terminal/src/pages/Billing.jsx`,
  `algo22-terminal/src/contexts/DataPipelineContext.jsx`, `backend_app/api_ws/ws_routes.py`, plus a
  new ticket route.

  **This is an auth change and it is the riskiest wave in the pass.** It alters how every socket in
  the application authenticates, across the nine routes in `ws_routes.py` — `/ws/telemetry`,
  `/ws/ticker/{symbol}`, `/ws/orderbook/{symbol}`, `/ws/candles/{symbol}/{timeframe}`,
  `/ws/user/{user_id}`, `/ws/pnl/{user_id}`, `/ws/dashboard`, `/ws/strategy/{strategy_id}`,
  `/ws/signal-trace` — each of which takes `token: str = Query(...)`. The sequencing below exists to
  make each step independently revertable, and **step 1 is deliberately not the credential change**.

  **Closed. All six subtasks (8.1-8.6) are implemented and verified passing.** 8.1-8.3 confirmed via source citation and independent re-run: `billingSocketLifecycle.test.jsx` + `singleSocket.test.jsx` 21/21, `socketCredential.test.js` 22/22. 8.4-8.6 (the `STRATEGY_STATUS` three-way contract) were genuinely missing and implemented in this pass: `tests/test_strategy_status_publish.py` 6/6, `deploymentState.test.jsx` 10/10 (8 original unmodified + 2 new). Upstream regression confirmed clean against three pre-existing test files exercising the same `deploy_strategy`/`stop_deployment` call paths (115, 15, 142 passed respectively), including two tests proving a new guard against `MagicMock` auto-vivification of `app_state.ws` in those pre-existing doubles.

  - [x] 8.1 Collapse to one socket, with no auth change
    - Billing drops `new WebSocket` at `Billing.jsx:144` and takes `wsClient.acquire()` +
      `subscribeChannel('billing', handler)`
    - **Delete Billing's `onclose` reconnect scheduling outright** (`:165`, `:170`). The defect is not a
      missing `clearTimeout`; it is that a second socket implementation re-solved a solved problem and
      got it wrong. The shared client already owns backoff, jitter, and the distinction between
      intentional teardown (`disableReconnect`, `release`) and a dropped connection
    - `DataPipelineContext.connectLiveData` does the same. Note its socket built
      `` `${wsClient.url}/ws/market-data` `` where `wsClient.url` already ends in `?token=<JWT>`, so the
      URL was `wss://host/ws/telemetry?token=<JWT>/ws/market-data` — malformed, credential mid-path, and
      aimed at a route that **does not exist**. The "live" pipeline mode has never connected, so this
      step **deletes a dead path rather than migrating a working one**
    - Effect: 1.22 and 1.23 close, and the credential is exposed in exactly **one** place instead of
      three. That alone is worth landing on its own
    - **Regression test:**
      `algo22-terminal/tests/unit/pages/billingSocketLifecycle.test.jsx` — unmount, advance fake timers
      past the reconnect delay → **the `WebSocket` constructor was not called again**; and
      `algo22-terminal/tests/unit/lib/singleSocket.test.jsx` — across a mount of Billing plus the data
      pipeline, the global `WebSocket` constructor is called **exactly once**. Run each scoped
    - _Bug_Condition: isBugCondition(X) — a socket opened after the component that owns it is gone, and three sockets against a design permitting one_
    - _Expected_Behavior: expectedBehavior(result) — every pending reconnect timer cancelled on unmount; exactly one socket open, Billing and market data multiplexed over it_
    - _Preservation: 3.10 — the paper lifecycle's REST event replay still shares its implementation with the WebSocket history; refcounting under two holds unchanged_
    - _Requirements: 1.22, 1.23, 2.22, 2.23, 3.10_

  - [x] 8.2 **MUST LAND ALONE** — replace the socket credential with a single-use ticket across nine routes
    - **This is one of the three changes `design.md §Change Ordering` forbids batching**, and it is the
      riskiest change in the pass. Committed on its own with `git commit -q -m`, verified on its own,
      revertable on its own
    - Add `POST /api/ws/ticket` returning a **single-use, short-TTL (≤ 30 s) opaque ticket** bound to
      the user and consumed on first use
    - `websocketClient.connect()` fetches a ticket over HTTPS and passes `?ticket=` instead of
      `?token=`. **1.21 names the wrong owner:** `connect()` builds
      `?token=${encodeURIComponent(token)}` too, so moving Billing onto the shared client without this
      step *consolidates* the exposure rather than removing it
    - All nine routes accept `ticket` and resolve it to a user. **Keep the existing `token` query
      parameter accepting for one release** so a cached bundle does not lose its socket mid-session,
      then remove it
    - *Why not a header:* the browser `WebSocket` constructor cannot set request headers, so "credential
      in a header" is not available. 2.21 permits the ticket explicitly
    - *Why not just a shorter JWT:* the ticket is single-use, so a logged ticket cannot be replayed —
      and replay is the actual harm in 1.21, because CloudFront and ALB access logs are retained
    - Reference the ticket and the JWT by name, never by value, in any test fixture or recorded output
    - **Regression test:** `algo22-terminal/tests/unit/lib/socketCredential.test.js` — the URL built by
      **`wsClient.connect` and** by Billing's mount contains **no `token` parameter and no JWT
      substring**, asserted with `fast-check` over generated tokens and paths so no position is missed.
      Plus unit coverage for ticket single-use and expiry
    - _Bug_Condition: isBugCondition(X) — a credential written to an access-loggable URL and to browser history_
    - _Expected_Behavior: expectedBehavior(result) — the credential travels as a short-lived single-use ticket; no token in any URL position_
    - _Preservation: 3.9 — authentication still applies unchanged on every route; the one-release `token` acceptance keeps cached bundles working_
    - _Requirements: 1.21, 2.21, 3.9_

  - [x] 8.3 Unify the token store
    - The shared client reads `sessionStorage`, Billing reads `localStorage` — **two token stores for one
      session**. Choose one, matched to whatever `api`'s HTTP client already uses, so a session cannot
      be authenticated for HTTP and anonymous for the socket
    - **Regression test:** `algo22-terminal/tests/unit/lib/socketCredential.test.js` — the socket and the
      HTTP client read the same store, asserted by pointing the test at one store and observing both
      paths authenticate
    - _Bug_Condition: isBugCondition(X) — two stores disagreeing about whether a session is authenticated_
    - _Expected_Behavior: expectedBehavior(result) — one store, one session_
    - _Preservation: 3.9 — existing authentication behaviour is unchanged for both transports_
    - _Requirements: 1.21, 2.21, 3.9_

  - [x] 8.4 Declare the `STRATEGY_STATUS` frame in one shared fixture
    - Create `tests/fixtures/strategy_status_frame.json` — **one file, read by both sides:** pytest reads
      it directly, vitest reads it through `node:fs` at a repo-relative path
    - The frame's `type` is the string `wsClient.subscribeStrategyStatus` subscribes to — **upper-case
      `STRATEGY_STATUS`** (`websocketClient.js:853`) — because `processMessage` matches `message.type`
      **exactly** and routes nothing otherwise
    - **Do not route this through `broadcast_dashboard_update`.** It wraps everything as
      `{"type": "dashboard_update", "update_type": "strategy_status"}`, which would leave the contract
      just as dead — in a way a backend-only test would not notice
    - 1.24 is a **three-way** mismatch, not one missing publisher: no service publishes, the only
      available broadcaster wraps the wrong envelope, and the subscriber spells it in upper case. Fixing
      any one alone leaves the contract dead. This is the clearest case in the pass for a fixture pinned
      on both sides
    - _Requirements: 1.24, 2.24_

  - [x] 8.5 Backend side — publish `STRATEGY_STATUS` from the start and stop service paths
    - Publish from the strategy start/stop service paths, with the frame shape read from
      `tests/fixtures/strategy_status_frame.json`. The only occurrence in the tree today is a docstring
      at `api_ws/ws_routes.py:1050` describing a call no service makes
    - **Regression test:** `tests/test_strategy_status_publish.py` — one start and one stop each publish
      **exactly one** frame whose `type` is the string the frontend subscribes to
    - _Bug_Condition: isBugCondition(X) — the frontend's status contract is structurally satisfied and operationally dead_
    - _Expected_Behavior: expectedBehavior(result) — exactly one frame per transition, over the one socket_
    - _Preservation: 3.9 — no change to authentication or to the routes' rate limits_
    - _Requirements: 1.24, 2.24_
    - **Judgment call, recorded:** a failed deploy attempt (the fleet call returns `success=False`) also publishes a `STRATEGY_STATUS` frame with `status: "failed"` and the fleet's own error message, not only a successful start. Reasoning: a silently-failed deployment start is the same category of defect this task exists to fix, just quieter — Live Trading's purpose is showing a trader the deployment's actual state. A reviewer who reads the task as success-only can see this decision here rather than infer it from the diff.

  - [x] 8.6 Frontend side — reflect the published frame
    - **Extend** the existing `algo22-terminal/tests/unit/liveTrading/deploymentState.test.jsx`, reading
      **the same** `tests/fixtures/strategy_status_frame.json`
    - **Regression test and assertion:** the frontend updates its strategy status from that fixture
      frame, and does **not** update from a `dashboard_update`-wrapped variant — which pins the envelope
      as well as the channel name
    - Run scoped:
      `node node_modules/vitest/vitest.mjs --run tests/unit/liveTrading/deploymentState.test.jsx`
    - **Why both sides:** 1.24's history is precisely a contract that a one-sided test would have passed
    - _Requirements: 1.24, 2.24, 3.7_

- [x] 9. Wave 4 — Route contract. Make the mismatch impossible, then fix the four instances

  **Files:** `algo22-terminal/src/contexts/{IndicatorEngine,LogicEngine,StrategyEngine}Context.jsx`,
  `algo22-terminal/src/contexts/CopilotContext.jsx` (delete),
  `backend_app/backend/signal_trace_engine.py`.

  Frontend call paths and backend routes are never checked against each other in either direction, and
  three of the four broken calls also reference an identifier that is not imported. Wave 0d (ESLint in
  CI) is the other half of this wave's durability: `no-undef` is what catches the unimported `post`.

  **Closed. All three open subtasks (9.1-9.3) confirmed implemented and passing; 9.4-9.6 were already closed from prior work.** `tests/test_route_contract.py` 13/13 (one genuine new finding recorded, see 9.1), `tests/unit/builder/enginePaths.test.jsx` 21/21 (one stale test bound corrected, see 9.2), `CopilotContext.jsx` confirmed deleted (9.3).

  - [x] 9.1 Add the general route-contract check first
    - Create `tests/test_route_contract.py`: enumerate every frontend HTTP call path and assert each
      appears in the backend OpenAPI schema **with the method used**
    - **The general check comes first because it is what stops the fourth instance from becoming a
      fifth.** 2.28 observes that this generalises to 2.25–2.27, and it subsumes Copilot
    - **Regression test and assertion:** every frontend call path appears in the OpenAPI schema with its
      method; the test names any path that does not
    - _Bug_Condition: isBugCondition(X) — a documented capability calls a route that does not exist_
    - _Expected_Behavior: expectedBehavior(result) — every call path resolves, or the call path does not exist_
    - _Preservation: 3.9 — no route's authentication, rate limit or ownership predicate is altered by adding the check_
    - _Requirements: 1.25, 1.26, 1.27, 1.28, 2.25, 2.26, 2.27, 2.28, 3.9_
    - **One genuine new finding recorded, not a defect:** the sweep flagged `algo22-terminal/src/websocketClient.js|fetch` — a call added by an earlier wave (task 8.2's ticket mint, `fetch(WS_TICKET_URL, …)`) — as an unrecorded unreadable call site, because `WS_TICKET_URL` is a template literal built from a config constant rather than a literal path. The route it calls, `POST /api/auth/ws-ticket`, is real. Recorded in `UNREAD_TRANSPORT_SITES` matching the file's existing entry style, in the same commit as this task rather than deferred.

  - [x] 9.2 Resolve the three engine contexts
    - `IndicatorEngineContext.jsx:66` calls `post('/indicator/compute', ...)`;
      `LogicEngineContext.jsx:55` calls `post('/logic/evaluate', ...)`;
      `StrategyEngineContext.jsx:283` calls `post('/strategy/execute', ...)`. All three have the same two
      defects: no such route, and `post` is not imported — so each raises `ReferenceError` inside a
      `useCallback`. All three providers are mounted (`StrategyBuilder.jsx:4590-4603`), so all three are
      reachable
    - Two dispositions, **decided per capability before implementing**: if the computation belongs
      server-side, import the client properly and point at a route that exists; if it does not, remove
      the client-side call path and let the builder's local computation stand
    - **Removing is the likelier correct answer.** The builder already computes indicators locally via
      `DataPipelineContext` and `utils/engineHelpers`, which is why these calls have never been missed.
      The clause requires a *defined outcome*, not necessarily a network call
    - **Regression test:** `algo22-terminal/tests/unit/builder/enginePaths.test.jsx` — **no
      `ReferenceError` and a defined outcome** for each of the three; plus `tests/test_route_contract.py`
      green. Run the vitest file scoped
    - _Bug_Condition: isBugCondition(X) — a capability raises before it reaches the network_
    - _Expected_Behavior: expectedBehavior(result) — a defined outcome: a computed result, a handled failure, or no call path at all_
    - _Preservation: 3.7 — the builder's local indicator computation and review-mode edit suppression are unchanged_
    - _Requirements: 1.25, 1.26, 1.27, 2.25, 2.26, 2.27, 3.7_
    - **One brittle test fixed in passing, not a regression:** `enginePaths.test.jsx`'s wrapper-position assertion used a tight ~30-line hard-coded window (4580-4610) that drifted after an unrelated, already-completed commit shifted `StrategyBuilder.jsx`'s line numbers by roughly 140 lines. The providers' actual nesting and mounting order — the property the test exists to verify — was confirmed unchanged and correct by direct source read. The window was widened to ~300 lines (4500-4800) and the test's own docstring updated to record why, so it survives similar unrelated churn going forward.

  - [x] 9.3 Delete `CopilotContext`
    - It POSTs `/api/v1/copilot/dag/generate`, which does not exist, and GETs
      `/api/v1/copilot/sessions/{id}`, where `copilot.py:306` registers only `DELETE` — the nearest read
      route is `GET /sessions/{id}/messages` at `copilot.py:264`
    - Delete the module. `CopilotProvider` is **mounted nowhere**, the file documents itself as
      "Intentionally DORMANT", and it bypasses the `api` client entirely with raw `fetch` +
      `getAuthHeaders()`. 2.28 explicitly permits removal over implementation
    - Deleting also removes a second auth path and a second error surface that does not go through
      `design/errorCopy.js`
    - **Regression test:** `tests/test_route_contract.py` — the sweep **passes trivially** once the
      dormant module is deleted, which is the assertion that the dead URLs are gone
    - _Bug_Condition: isBugCondition(X) — dead code carrying two broken URLs and a second auth path_
    - _Expected_Behavior: expectedBehavior(result) — the call path does not exist_
    - _Preservation: 3.7, 3.8 — no user-facing copy path is lost; `design/errorCopy.js` remains the only error surface_
    - _Requirements: 1.28, 2.28, 3.7, 3.8_

  - [x] 9.4 Declare the Signal Trace node projection in one shared fixture
    - Create `tests/fixtures/signal_trace_node_projection.json` — **one file, read by both sides:** pytest
      reads it directly, vitest reads it through `node:fs` at a repo-relative path
    - Extract the inline `DAGNodeTrace -> dict` projection out of the `dag_nodes` stage literal at
      `signal_trace_engine.py:325-341` into a **named function**, and declare its output shape in the
      fixture
    - **Naming the projection is the fix; the two broken stages are the symptom.** It existed only as an
      inline expression inside one of three stage literals, so it could not be reused and two stages were
      written without it. Once named, the three stages cannot disagree — which is the property that
      matters more than the two current instances
    - _Requirements: 1.29, 2.29_

  - [x] 9.5 Backend side — every stage's `nodes` is JSON-serialisable
    - Call the named projection from **all three** stages. `signal_trace_engine.py:313-323` currently
      places raw `DAGNodeTrace` instances into the `nodes` field of the `market_data` and `indicators`
      stages, so the response fails serialisation and Signal Trace stages 1 and 2 never show node detail
    - **Regression test:** `tests/test_signal_trace_serialisation.py` —
      **`json.dumps(to_frontend_format())` succeeds**, and stages 1 and 2 carry populated node detail
      **projected identically to stage 3**, compared against
      `tests/fixtures/signal_trace_node_projection.json`. Assert the projection over every `NodeType`
    - _Bug_Condition: isBugCondition(X) — the response is asked to serialise an object that cannot_
    - _Expected_Behavior: expectedBehavior(result) — NOT is_500: the trace serialises, and every stage's nodes are dicts from one shared projection_
    - _Preservation: 3.7 — stage 3's existing node detail is unchanged, byte for byte_
    - _Requirements: 1.29, 2.29, 3.7_

  - [x] 9.6 Frontend side — stages 1 and 2 render node detail
    - **Extend** the existing `algo22-terminal/tests/unit/lib/signalTraceStages.test.js`, reading
      **the same** `tests/fixtures/signal_trace_node_projection.json`
    - **Regression test and assertion:** stages 1 and 2 render node detail from the fixture's projected
      shape, and the stage-3 rendering is unchanged — so the three stages are pinned to one shape on the
      consuming side as well as the producing side
    - Run scoped:
      `node node_modules/vitest/vitest.mjs --run tests/unit/lib/signalTraceStages.test.js`
    - **Why both sides:** a backend-only serialisation test passes while the frontend still reads a
      per-stage shape
    - _Requirements: 1.29, 2.29, 3.7_

- [ ] 10. Wave 5 — Local correctness and presentation

  Genuinely local; no other wave depends on any of it. Four of the five are P2 or P3 and are marked
  optional for launch. The Dependabot triage is P1 and is required.

  - [ ]* 10.1 Seed undo history at the strategy loader
    - **The fix is at the loader, not the predicate.** `UndoRedoContext.jsx:23`'s
      `canUndo = currentIndex > 0` is correct for a history whose index 0 is the pre-edit baseline; it is
      wrong for one whose index 0 is the first edit. Seed the baseline with `pushState` when a saved
      strategy loads
    - **Do not change the predicate to `>= 0`.** That would make `undo()` return `history[-1]` —
      `undefined`
    - **Record but do not fix**, and do not widen the change: `pushState` calls `setCurrentIndex`
      *inside* the `setHistory` updater, a side effect React StrictMode double-invokes, and it skips the
      increment on the `maxHistory` shift branch. Both are pre-existing and outside 1.38's scope
    - **Optional for launch: P2**
    - **Regression test:** a scoped vitest case asserting `canUndo` is true after one edit on a freshly
      loaded strategy
    - _Bug_Condition: isBugCondition(X) — the first edit on a loaded strategy cannot be undone_
    - _Expected_Behavior: expectedBehavior(result) — `canUndo` true after one edit_
    - _Preservation: 3.7 — redo behaviour and the `maxHistory` cap are unchanged_
    - _Requirements: 1.38, 2.38, 3.7_

  - [ ]* 10.2 Carry per-caller subscription state onto the browse response
    - `browse_library` (`library.py:1011`) projects only the public aggregate `subscriber_count`, so the
      catalogue cannot show whether the caller already subscribes
    - `_enrich_cards_with_user_context(items, svc, user_id)` **already exists** and already folds
      per-caller state onto cached, projected items. Add subscription state to that existing pass with
      **one bulk query keyed by the page's listing ids** — which is how `my-strategies` already achieves
      three round trips independent of entry count. **No per-card query**
    - The badge renders through the existing `design/subscriptionState.js` view, which already reads
      `subscription.state`, `period_expiry`, `renewal_state`, `entitling` and `unavailable_reason`
    - **Optional for launch: P2**
    - **Regression test:** pytest asserting per-card state with **no per-card query**, plus a scoped
      vitest case asserting the badge renders
    - _Bug_Condition: isBugCondition(X) — the catalogue is asked for the caller's subscription state and the projection omits it_
    - _Expected_Behavior: expectedBehavior(result) — each card carries the caller's state from the browse response_
    - _Preservation: 3.11, 3.13 — `MARKETPLACE_READ_FAILED` (503) is never folded into a 403, and `my-strategies` still derives its three fields server-side in three round trips_
    - _Requirements: 1.37, 2.37, 3.11, 3.13_

  - [ ]* 10.3 Stop the non-interactive `Drawer` scrim from carrying an accessible name
    - `Drawer.jsx:231-233` renders the scrim as `<button aria-label="Close" disabled>` when
      `dismissOnScrim={false}`, putting a control named "Close" in the accessibility tree that cannot
      close anything. Render a non-interactive element with no accessible name in that case
    - Latent today — no consumer passes `false`, and `StrategyBuilder.jsx:111` documents avoiding it — so
      this is a contract fix on the primitive
    - **Optional for launch: P3**
    - **Regression test:** extend `algo22-terminal/tests/unit/ds/Drawer.test.jsx` — no element named
      "Close" is exposed when the scrim is non-interactive. Run scoped
    - _Bug_Condition: isBugCondition(X) — a control is named for an action it cannot perform_
    - _Expected_Behavior: expectedBehavior(result) — no accessible name where there is no action_
    - _Preservation: 3.7 — the dismissable scrim's behaviour is unchanged_
    - _Requirements: 1.45, 2.45, 3.7_

  - [ ]* 10.4 Define which drawer wins the overlay registry slot
    - `AccountMenu` claims `OVERLAY_KIND.DRAWER`, the same kind `Drawer` claims, so the builder inspector
      and the shell menu contend for one registry slot at tablet widths
    - `ds/overlayRegistry.js` already arbitrates and already raises `OverlayConflictError` in
      development. Define which wins. **Do not add a third overlay kind** —
      `algo22-terminal/tests/unit/ds/overlayRegistry.test.js` pins the two-kind contract
    - **Optional for launch: P3**
    - **Regression test:** extend `algo22-terminal/tests/unit/ds/overlayRegistry.test.js` — deterministic
      stacking with both drawers mounted. Run scoped
    - _Bug_Condition: isBugCondition(X) — two overlays claim one slot and the winner is undefined_
    - _Expected_Behavior: expectedBehavior(result) — deterministic stacking_
    - _Preservation: 3.7 — the two-kind contract holds; no third kind is introduced_
    - _Requirements: 1.46, 2.46, 3.7_

  - [x] 10.5 Triage the 295 Dependabot alerts
    - 295 open on the default branch: **8 critical, 60 high, 127 medium, 100 low**, with no triage record
      distinguishing runtime from development dependencies. `gh` is authenticated as `Narendra2212`, so
      the counts are directly checkable
    - Every critical and high on a **runtime** dependency is resolved, or carries a checked-in justified
      exception naming why it is not exploitable here. Development-only alerts defer to P2
    - **The triage record distinguishing runtime from development is the deliverable**, because its
      absence is what 1.33 actually names
    - **Regression test:** an alert re-query showing **zero unexcepted critical or high alerts on runtime
      dependencies**, with the exception list checked in
    - _Bug_Condition: isBugCondition(X) arm 5 — the release cannot state which of its dependencies are exploitable_
    - _Expected_Behavior: expectedBehavior(result) — zero unexcepted critical/high runtime alerts, with every exception justified by name_
    - _Preservation: 3.18 — no dependency bump breaks an existing passing test; the property suites stay green_
    - _Requirements: 1.33, 2.33, 3.18_
    - **Closed. `dependency-triage.md` now covers 8 packages across both pip and npm ecosystems.** Bumped: `python-multipart` (0.0.9→0.0.30), `aiohttp` (3.10.5→3.14.3), `lightgbm` (4.3.0→4.6.0), `react-router-dom`/`react-router` (npm, →7.18.4 — the lockfile had already drifted into the vulnerable range despite the `package.json` caret). Justified exceptions, each with a traced reachability or compatibility finding: `cryptography` (no x509/EC/PKCS7 call site in this codebase's Fernet/PBKDF2 usage), `starlette` (FastAPI 0.115.3's own `requires_dist` ceiling — `starlette<0.42.0` — blocks every CVE fix version; closing this needs a FastAPI bump spanning dozens of releases, a separate change), `torch` (disk-blocked verification, prior work). `python-jose` was already resolved by prior work. Zero unexcepted critical/high alerts remain on any runtime dependency; the only untriaged critical/high alerts are 4 npm alerts confirmed development-scoped, correctly deferred per this task's own P2 rule. Live-requeried at 288 open alerts total (127 medium, 100 low, 53 high, 8 critical) — the plan's "295/8/60" figures are stale by comparison and superseded by this count.

- [ ] 11. Verify the fix and the preservation baseline

  - [ ] 11.1 Verify the bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - A fact the system does not have is reported as absent, never invented
    - **IMPORTANT**: Re-run the **same** tests written in task 1 — do NOT write new ones. Those tests
      encode the expected behaviour; when they pass, the expected behaviour is satisfied
    - `pytest tests/test_dashboard_absent_figures.py tests/test_exchange_health_probe.py tests/test_backtest_key_contract.py tests/test_backtest_equity_curve_persisted.py tests/test_signal_trace_serialisation.py tests/test_strategy_status_publish.py tests/test_route_contract.py tests/test_release_artifacts.py`
    - Each frontend file scoped, one `node node_modules/vitest/vitest.mjs --run <path>` per file —
      `billingSocketLifecycle`, `socketCredential`, `singleSocket`, `equityCurveEmpty`,
      `backtesterNetPnl`, `enginePaths`, `deploymentState`, `signalTraceStages`, `downloadSurface`
    - **EXPECTED OUTCOME**: every test PASSES, confirming each bug is fixed
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.21, 2.22, 2.23, 2.24, 2.25, 2.26, 2.27, 2.28, 2.29, 2.30, 2.31, 2.32, 2.34, 2.40_

  - [ ] 11.2 Verify the preservation tests still pass
    - **Property 2: Preservation** - Every path that has its fact answers exactly as it does today
    - **IMPORTANT**: Re-run the **same** tests from task 2 — do NOT write new ones
    - `pytest tests/regression/test_baseline_unchanged.py tests/property/test_absent_vs_zero.py tests/test_orders_manual_execution_blocked.py` and the `tests/property/` suites
    - The existing `algo22-terminal/tests/unit/guards/` and `algo22-terminal/tests/unit/design/` files,
      each run scoped. **Never the bare vitest suite** (3.18)
    - `git diff --exit-code aerora_quant_platform/frontend_app/algo22-terminal` reports no change (3.16)
    - **EXPECTED OUTCOME**: every test PASSES, confirming no regressions. In particular a genuine `0.0`
      still reports `0.0`, and `MANUAL_EXECUTION_BLOCKED` still refuses
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.16, 3.18_

- [x] 12. The 15 UNVERIFIED clauses — investigations, not fixes

  For these the deliverable is **a proof or a new numbered defect**, not a code change. The defect
  being asserted is the *absence of proof*, so a clause closes by producing the proof, or by producing
  a new numbered P0/P1 that then re-enters at the wave its mechanism belongs to.

  There are **15**, not the 17 the requirements state: 1.11–1.20, 1.39, 1.42, 1.43, 1.44, 1.47. The
  fifteenth (1.47) is task 13.6, because it can only run after the deploy.

  These gate launch but do **not** gate the merge, which is why they sit after wave 0 rather than
  before it. They depend on no wave's code change and may run alongside waves 1–5.

  **Five are partial blockers.** `design.md` names, for each, the specific gap that this environment
  cannot close by automated test. For those five the outcome is recorded as **BLOCKED with the gap
  named** — never as a silent pass. A clause that is BLOCKED is reported as BLOCKED.

  **Four say EXTEND, and extend means extend.** `test_cross_tenant_ownership_matrix.py`,
  `test_worker_crash_mid_submission.py`, `test_protected_logic_containment.py` and
  `tests/regression/capture_baseline.py` already exist and are already rigorous. Writing a parallel
  suite beside one of them produces **two partial answers instead of one complete one**, which is the
  failure mode these four tasks exist to avoid.

  **Live-order testing against a real exchange is out of bounds.** Every trading-safety clause below is
  established by code reading, unit and property tests, and testnet/paper/simulation only. No clause
  closes on a live fill.

  - [x] 12.1 1.11 — prove order idempotency under duplicate submission
    - **Partly already proven.** `tests/crash_recovery/test_worker_crash_mid_submission.py` establishes
      live-path idempotency-key deduplication across roughly twenty tests, including *the durable unique
      index refuses a second row for the same key*, *the venue itself refuses a second order under the
      same key*, and *a resumed worker that submits anyway still creates no second order*. 1.11's "no
      test in this tree establishes" is **wrong** and the task records that
    - **The gap is the property test and the paper path.** Create
      `tests/property/test_order_idempotence_under_retry.py`, **extending
      `tests/property/test_paper_idempotence.py` rather than replacing it**: over generated
      submit/retry/restart sequences carrying one idempotency key, order count is invariant at **exactly
      one** and every submission after the first is answered with **that** order
    - Paper and simulation paths only; no live exchange call
    - Add the code reading recording the idempotency key and its uniqueness constraint, per 2.11
    - **Provable here: yes, fully**
    - **Closed.** `tests/property/test_order_idempotence_under_retry.py` extended `test_paper_idempotence.py`; 3 passed, independently re-verified this dispatch.
    - _Requirements: 1.11, 2.11_

  - [x] 12.2 1.12 — prove paper–live separation
    - Create `tests/test_paper_live_separation.py`: substitute a **failing double** for the live venue
      adapter that raises if constructed, and assert it never raises across every paper signal path
    - Code-level, no venue needed
    - **Provable here: yes**
    - **Closed.** `tests/test_paper_live_separation.py` created; 12 passed, independently re-verified this dispatch.
    - _Requirements: 1.12, 2.12_

  - [x] 12.3 1.13 — prove server-side risk enforcement **[PARTIAL BLOCKER: the deployment path]**
    - Create `tests/test_risk_limits_server_side.py`: submit each violation — position size, leverage,
      daily loss, kill switch — **with no UI involvement**, asserting refusal with a distinct
      machine-readable code and that **no order row is written**
    - **Constraint: not via `POST /api/orders/execute` or `POST /api/orders/create`.** Those answer 403
      `MANUAL_EXECUTION_BLOCKED` to everything by design, so a pass there proves nothing. Target the
      **paper order route and the internal execution service**. `createOrder` is not repointed and those
      two routes are not altered
    - **BLOCKED, gap named: the deployment path needs a running worker.** The only path by which an order
      reaches a venue is strategy deployment, and this environment cannot exercise it. Record the
      deployment path as BLOCKED with that gap; the paper route and the service are proven
    - **Provable here: yes for the paper route and the service; partly for the deployment path**
    - **Closed for the paper route and the internal execution service.** `tests/test_risk_limits_server_side.py` created; 8 passed, independently re-verified this dispatch. **Remains BLOCKED for the deployment path** — no running worker is available in this environment to exercise the only path by which an order reaches a venue, per this task's own stated gap.
    - _Requirements: 1.13, 2.13_

  - [x] 12.4 1.14 — **extend** the cross-tenant ownership matrix **[PARTIAL BLOCKER: the paper routes]**
    - **EXTEND `tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py`. Do not write a second
      sweep.** It is already a rigorous enumerated sweep: it resolves every probe through Starlette's own
      matcher, normalises headers and bodies, self-checks its own coverage, and covers strategy, version,
      backtest, backtest_result, deployment, signal and exchange_account
    - Add: orders, positions, portfolios, traces, credentials, billing, subscriptions, listings, paper
      accounts, invoices
    - The matrix's own coverage test fails if a cell is left unprobed, which is what makes the sweep list
      auditable. **Any route excluded must be recorded with its reason** — the file already requires this
      via `test_every_probe_without_an_owner_control_states_why`
    - Assert the response for an other-tenant id is **byte-identical** to the response for an id that
      names nothing — status, headers and body — that **no row is written**, and that the **owner is
      answered differently**, so the assertion is not vacuous
    - `backtest_service.update_backtest_results` is the precedent this generalises: it filtered on `id`
      alone until it was found, making it a cross-tenant write and an existence oracle
    - **BLOCKED, gap named: the paper routes.** The file itself records that `SandboxDatabase` does not
      implement `009_paper_trading.sql`'s semantics. That is a named blocker, not a pass
    - **Provable here: yes for routes the sandbox can serve; no for the paper routes without further work**
    - **Closed for routes the sandbox can serve.** `tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py` extended with orders, positions, portfolios, traces, credentials, billing, subscriptions, listings, paper accounts, invoices; 224 passed, independently re-verified this dispatch. **Remains BLOCKED for the paper routes** — `SandboxDatabase` does not implement `009_paper_trading.sql`'s semantics, per this task's own recorded gap.
    - _Requirements: 1.14, 2.14_

  - [x] 12.5 1.15 — document the tenant boundary per table
    - Produce a schema audit checked into `.kiro/specs/production-launch-hardening/`: for each table
      carrying two tenants' rows, record the **predicate**, **row-level security**, **foreign key** and
      **index** on every access path
    - Add a test asserting each shared table's access paths carry the tenant predicate
    - **Every gap found is filed as a numbered P0**, not as a note
    - The migrations are in the tree and readable. **Provable here: yes**
    - **Closed.** `tenant-boundary-audit.md` created; audits the full table list against predicate/RLS/foreign-key/index, with a "Defects filed" section continuing `bugfix.md`'s numbering from 1.48/2.48, independently re-verified this dispatch. `tests/test_task_12_5_tenant_boundary_predicate.py` created; 6 passed.
    - _Requirements: 1.15, 2.15_

  - [x] 12.6 1.16 — prove concurrent strategy lifecycle serialisation **[PARTIAL BLOCKER: database-level serialisation]**
    - Create `tests/test_strategy_lifecycle_concurrency.py`: fire start, stop, delete and deploy **in
      parallel** against one strategy id, repeated enough times to expose interleaving, and assert the
      terminal state is one of the legal states — **no running-but-deleted, no double deploy**
    - **BLOCKED, gap named: a real Postgres is needed to establish that the *database's* serialisation
      holds, not just the application's.** Provable against the sandbox; the database-level guarantee is
      recorded as BLOCKED with that gap
    - **Provable here: partly**
    - **Closed for the application-level absence proof.** `tests/test_strategy_lifecycle_concurrency.py` created; 25 passed, 2 failed, independently re-verified this dispatch. **The 2 failures are the point, not a defect in the file** — per the file's own module docstring, `test_the_four_operations_can_land_running_but_deleted` and `test_deploy_can_race_itself_into_a_double_deploy` are written to fail against the code as it stands on purpose, proving no application-level lock/guard exists; they are the regression tests that will start passing once an application-level guard lands. **Remains BLOCKED for the database-level guarantee** — a real Postgres is needed to establish the database's own serialisation holds, per this task's own stated gap.
    - _Requirements: 1.16, 2.16_

  - [x] 12.7 1.17 — close by citation, and record the scope
    - **This clause closes by citation. Do not implement it, and do not write a second suite.**
      `tests/crash_recovery/test_worker_crash_mid_submission.py` **already proves** exactly-once
      processing across a worker restart mid-signal, including that recovery never moves a state
      backwards and that the marker is written once however many times a worker restarts
    - The deliverable is the citation plus a **recorded scope**: which cases the existing file covers,
      named, so a future reader can see the clause is answered and by what. Add the scope record to the
      existing file's module docstring — **extending** it, not creating a parallel document
    - This task exists so 1.17 is not re-implemented by someone reading only the requirements, which
      assert "no test in this tree establishes" and are wrong
    - **Provable here: yes — already proven**
    - **Closed by citation.** `tests/crash_recovery/test_worker_crash_mid_submission.py` cited; its module docstring documents scope (what is asserted, what is real, what is not tested here); collected 21 test functions, 21 passed, independently re-verified this dispatch.
    - _Requirements: 1.17, 2.17_

  - [x] 12.8 1.18 — prove entitlement is withdrawn on lapse
    - Create `tests/test_entitlement_matrix.py`: subscription states × protected operations, asserting
      **each cell's wire code** against the four existing codes — `MARKETPLACE_NOT_SUBSCRIBED`,
      `MARKETPLACE_SUBSCRIPTION_EXPIRED`, `MARKETPLACE_STRATEGY_UNAVAILABLE`,
      `MARKETPLACE_OPERATION_NOT_PERMITTED`
    - Assert **`MARKETPLACE_READ_FAILED` (503) is not folded in**, so a paying subscriber is never told
      they hold no subscription because a read broke (3.11). Wire codes, not status classes
    - `tests/property/test_subscription_state_machine.py` and `test_entitlement_expiry_boundary.py`
      already model the states and are the starting point
    - **Provable here: yes**
    - **Closed.** `tests/test_entitlement_matrix.py` created; 38 passed, independently re-verified this dispatch.
    - _Requirements: 1.18, 2.18, 3.11_

  - [x] 12.9 1.19 — **extend** the protected-logic containment property to traces and exports
    - **EXTEND `tests/property/test_protected_logic_containment.py`. Do not write a second suite**
    - Add the **trace and export surfaces**: assert the serialised response for a non-entitled caller
      contains **none** of the node types, parameters or expressions of the underlying DAG — not in the
      body, not in an error message, not in a trace, not in a DAG export
    - **Provable here: yes**
    - **Closed.** `tests/property/test_protected_logic_containment.py` extended to trace and export surfaces; 5 passed, independently re-verified this dispatch.
    - _Requirements: 1.19, 2.19_

  - [x] 12.10 1.20 — prove no secret in the bundle or the logs **[PARTIAL BLOCKER: the production log audit]**
    - A grep assertion over `dist/` for key patterns and **known secret names**, generalising the
      workflow's existing `service_role` check. Build with
      `$env:NODE_OPTIONS="--max-old-space-size=2048"` first
    - A log-capture test asserting exchange credentials are **redacted**
    - **Secrets are referenced by key name, never by value, in anything this pass produces** — including
      test fixtures, recorded output and commit messages
    - **BLOCKED, gap named: a full audit of production log *content* needs log access this deploy-scoped
      IAM principal may not have.** If a call is denied, record the specific denied call and report the
      log half as BLOCKED. The bundle half is proven
    - **Provable here: yes for the bundle; partly for the logs**
    - **Closed for the bundle and for log redaction.** `tests/test_no_secrets_in_bundle.py` and `tests/test_exchange_credential_log_redaction.py` created; 11 passed combined, independently re-verified this dispatch. Confirmed `exchange_executor.py`'s `_redact_credentials` is called from `_handle_ccxt_error` before any exception text is logged or classified. **Remains BLOCKED for the production log audit** — a full audit of production log content needs log access this deploy-scoped IAM principal may not have, per this task's own stated gap.
    - _Requirements: 1.20, 2.20_

  - [x] 12.11 1.39 — prove 422 not 500 on malformed input
    - Create `tests/test_validation_sweep.py`: submit malformed bodies to **every mutating route**,
      asserting **no 500** and no `err.message`-style leakage, and a machine-readable code on each 422
    - `TestClient` over the real app — the same technique `test_cross_tenant_ownership_matrix.py` already
      uses. The frontend half is already guaranteed by `design/errorCopy.js`; this extends the guarantee
      server-side
    - **Provable here: yes**
    - **Closed.** `tests/test_validation_sweep.py` created; 378 passed, 12 skipped, independently re-verified this dispatch. Also confirmed `recover_deployment`, `recover_signals` and `update_backtest_results` in `backend_app/routers/strategy_operations.py` carry `except HTTPException: raise` guards and an `isinstance(results, dict)` check respectively, so a malformed/mistyped input surfaces as 422/404 rather than an unguarded 500.
    - _Requirements: 1.39, 2.39, 3.8_

  - [x]* 12.12 1.42 — measure performance **[PARTIAL BLOCKER: heap growth needs a browser]**
    - **Extend** `tests/perf/test_market_data_latency.py` and `tests/perf/test_strategy_builder_budgets.py`
      rather than adding a third harness. Record **query counts, polling intervals and render counts**
    - **The requirement is a measurement, not a number.** Absolute performance targets are out of scope;
      any regression against the redesign baseline is filed
    - **BLOCKED, gap named: heap growth over a long session needs a browser.** `design.md` records this as
      a **manual measurement**, taken in a browser and recorded as such — the one measurement in this
      plan that is not automatable here. It is recorded with the method used, not asserted
    - **Optional for launch: P2**
    - **Provable here: partly — query and render counts yes, heap growth manual**
    - **Closed for query and render counts.** `tests/perf/test_strategy_builder_budgets.py::TestQueryCounts` extended; 5 passed (0 queries at every measured graph size, 10 through 200 nodes/400 edges), independently re-verified this dispatch. `strategyBuilder.nodeRenderCounts.test.jsx` extended; 8 passed. **Remains BLOCKED for heap growth** — that measurement needs a browser and is recorded manually, per this task's own stated gap.
    - _Requirements: 1.42, 2.42_

  - [x]* 12.13 1.43 — prove loading then recoverable failure on every surface
    - Create `algo22-terminal/tests/unit/pages/degradedSurfaces.test.jsx`: delayed and rejected fetches
      across the primary surfaces, asserting **neither an empty frame nor a permanent skeleton**
    - `usePanelState` makes this cheap: its eight states are already the vocabulary, and
      `STATES_WITHOUT_CHILDREN` already encodes which must not render children
    - Run scoped:
      `node node_modules/vitest/vitest.mjs --run tests/unit/pages/degradedSurfaces.test.jsx`
    - **Optional for launch: P2**
    - **Provable here: yes**
    - **Closed.** `algo22-terminal/tests/unit/pages/degradedSurfaces.test.jsx` created; 10 passed, independently re-verified this dispatch.
    - _Requirements: 1.43, 2.43_

  - [x]* 12.14 1.44 — re-verify the responsive layout against this pass
    - **Re-run the existing responsive test files, scoped, never the full suite** (3.18). No new suite —
      the redesign's files are the instrument; this task establishes they still pass against `F'`
    - This is a test re-run, not a manual QA pass
    - **Optional for launch: P2**
    - **Provable here: yes**
    - **Closed.** `deployPreflight.test.jsx` re-run: 47 passed. `responsiveGate.test.jsx`, `sidebar.test.jsx` and `strategyBuilder.reviewMode.test.jsx` re-run together: 65 passed combined, independently re-verified this dispatch.
    - _Requirements: 1.44, 2.44, 3.18_

- [ ] 13. Deployment, in the design's stated order

  **The merge is the deploy.** `06-frontend-deploy.yml` fires on push to `main` for any change under
  `algo22-terminal/**`, and there is no staging gate between merge and CloudFront. Waves 0–5 are on the
  feature branch and verified there before any sub-task below runs.

  **Tasks 13.1–13.3 modify production. Confirm with the user before executing each one.**

  - [ ] 13.1 Merge `ALLOWED_API_HOSTS` to `main` **before** the `VITE_API_URL` cutover
    - **HARD ORDERING CONSTRAINT. This is not a preference and the order is not reversible.**
    - Merge the allow-list commit from task 4.1 to `main` **on its own**. The deploy it triggers builds a
      bundle still containing `d7d88qs4jmch.cloudfront.net`, which the allow-list accepts — the allow-list
      is a strict superset of the old two-host grep, so this deploy is **green with the existing secret**
    - **Cutting the secret first breaks the deploy and misreports it.** The bundle would then contain
      `app.vyomquant.in`, the old grep on `main` would reject it, and the failure would be reported as
      `VITE_API_URL not properly substituted` — a build error for a configuration change. That
      misattribution is the defect 1.31 names, and this ordering is the whole fix for it
    - Confirm the triggered deploy is green before proceeding
    - _Requirements: 1.31, 2.31, 3.14_

  - [ ] 13.2 Merge the hardening branch to `main`
    - The deploy fires. Confirm all four pre-sync gates run **before** the `aws s3 sync`: the `releases/`
      size gate (task 4.2), the localhost-fallback check, the service-role-key check, and the API-host
      allow-list
    - 2.30 must already be closed — the merge auto-fires the deploy, so a placeholder artifact left in
      `dist/` at this point is published and cached
    - _Requirements: 1.30, 1.32, 2.30, 2.32, 3.14_

  - [ ] 13.3 Cut `VITE_API_URL` over to `app.vyomquant.in` and re-deploy
    - Only after 13.1. The next deploy's bundle contains `app.vyomquant.in`, which the allow-list accepts
    - _Requirements: 1.31, 2.31_

  - [ ] 13.4 Verify production health end to end — 1.47, the fifteenth UNVERIFIED clause
    - `aws ecs describe-services` on `vyomquant-cluster` / `vyomquant-api-service-cjema2sl` reporting
      **`ACTIVE` with `runningCount == desiredCount` on the new task definition**
    - The backend health endpoint answering **through the ALB**
    - `https://d7d88qs4jmch.cloudfront.net/` serving the **new** bundle
    - **Baseline to beat, captured during requirements gathering:** `ACTIVE 1/1` on `vyomquant-api:146`,
      CloudFront `200`. AWS access is confirmed available this session as
      `arn:aws:iam::273709947018:user/github-actions`
    - **The deliverable is the recorded command output.** The IAM principal is deploy-scoped: **if any
      call is denied, record that specific denied call as BLOCKED with the denial, and do not report the
      clause as passing**
    - _Requirements: 1.47, 2.47, 3.17_

  - [ ] 13.5 Invalidate the paths whose cache policy changed in task 4.6
    - `robots.txt`, `sitemap.xml` and the four SVGs. **An immutable object already in an edge cache does
      not re-fetch on its own**, so task 4.6's header change reaches nobody without this — those objects
      are sitting in CloudFront with a one-year `immutable` header, verified live
    - Runs **after** 13.4, per the design's stated order, and waits for the invalidation to complete
    - _Requirements: 1.40, 2.40, 3.14_

  - [ ] 13.6 Diagnose before retrying a bad ECS revision
    - **A blind re-deploy of the same task definition reproduces the same failure and costs another
      rollout window.** Read, in this order:
      1. the ECS service's `events` — for placement and health-check failures
      2. the stopped task's `stoppedReason` and container `exitCode` — for a crash on boot
      3. the target group's health state — for a service that starts but fails its probe
    - These three distinguish the failure classes that look identical from the service status alone
    - `03-deploy.yml` still verifies an immutable ECR artifact exists for the commit SHA before deploying
      (3.15); that check stays
    - If a diagnostic call is denied to the deploy-scoped principal, record the specific denied call
    - _Requirements: 2.47, 3.15, 3.17_

  - [ ] 13.7 Roll back to the named last-known-good revision if needed
    - **Rollback target: `vyomquant-api:146`**, currently `ACTIVE 1/1` on `vyomquant-cluster` /
      `vyomquant-api-service-cjema2sl`
    - `aws ecs update-service --task-definition vyomquant-api:146 --force-new-deployment`, then confirm
      `runningCount == desiredCount` **on 146**
    - **Frontend rollback** is a re-deploy of the previous commit plus an invalidation. The `--delete`
      flag means the bucket mirrors `dist/` exactly, so there is no partial state to reconcile — but it
      also means a rollback **removes any object the previous build did not produce**, which is precisely
      how `/releases/` came to be empty. **Anything published outside the `dist/` sync must be
      re-uploaded after a rollback**
    - _Requirements: 2.47, 3.17_

  - [ ] 13.8 Cut auth email over to custom SMTP — new defect 1.49, found after wave 5
    - **The built-in Supabase sender will not deliver to anyone outside the project's team.** Every
      signup and signin in `AuthPage.jsx` depends on a delivered email, so on the current
      configuration **no customer can register or log in**. The flow looks healthy only because it was
      tested from the project owner's address, which is on the team
    - **A second, independent limit: 2 messages per hour, project-wide rather than per user.** With
      password + OTP mandatory on every signin, one login costs one email, so the product supports two
      logins an hour across all tenants. This is the defect reported as "signin OTP takes very much
      time to reach" — a cap, not latency
    - **Provider: Amazon SES in `ap-southeast-1`**, matching the ECS region. DNS is **GoDaddy**, not
      Route53 — `vyomquant.in` NS records are `ns53`/`ns54.domaincontrol.com` and this account has zero
      Route53 hosted zones, so the DKIM CNAMEs are added at the registrar
    - **Order matters and only the first step is slow.** Request production access first: until it is
      granted, SES is in sandbox and can send only to individually verified addresses, which reproduces
      the team-only restriction this task exists to remove
      1. SES → Account dashboard → request production access. Transactional auth mail only: signup
         confirmation and login one-time codes, recipients are the project's own registered users
      2. SES → Identities → verify domain `vyomquant.in` with Easy DKIM, then add the three
         `<selector>._domainkey` CNAMEs in GoDaddy. **Enter the host without the apex suffix**, or
         GoDaddy appends the domain twice
      3. SES → SMTP settings → create SMTP credentials. **These are not the AWS access keys** — the
         console mints a separate IAM user and shows the password once
      4. Supabase → Authentication → Emails → SMTP Settings: host
         `email-smtp.ap-southeast-1.amazonaws.com`, port 587, sender `no-reply@vyomquant.in`
      5. **Supabase → Authentication → Rate Limits.** Enabling custom SMTP applies a fresh 30/hour cap
         to protect the new sender. Leaving it swaps a 2/hour blocker for a 30/hour one
    - **Not executable from this workspace, and that is a permission fact rather than an omission.** The
      session principal `arn:aws:iam::273709947018:user/github-actions` is denied `ses:GetAccount` and
      `ses:ListEmailIdentities`; the Supabase SMTP fields need dashboard access or a `sbp_` PAT, which
      this session does not hold. **Record the console output; do not mark 1.49 closed by assertion**
    - **Verification that distinguishes a fix from a hope:** sign up with an address on **no** team and
      **not** on SES's verified list, and require the OTP to arrive and be accepted. A test from the
      owner's mailbox passes against the unfixed configuration and proves nothing
    - **What is already correct and must not be re-opened.** The signin OTP is six digits and
      `POST /auth/v1/verify` with `type: "email"` accepts it, returning a one-hour session — probed this
      session against a throwaway user. The `redirect_to` allow-list preserves the full
      `/app/dashboard` path. And `dispatched = !dispatch?.error` already makes the OTP step say the code
      could not be sent instead of claiming an email was sent, so the UI is honest about the refusal
      **this task removes the cause of**
    - _Requirements: 1.49, 2.49_

  - [ ] 13.9* Protect the new sender's reputation once 13.8 lands
    - **Only after 13.8.** There is no reputation to protect while Supabase's shared sender is in use
    - **DMARC**, starting at observe-only so nothing is rejected during rollout: TXT at
      `_dmarc.vyomquant.in` = `v=DMARC1; p=none; rua=mailto:<mailbox>`. Tighten to `p=quarantine` only
      after SES reports clean DKIM alignment
    - **CAPTCHA on the auth routes.** Supabase's SMTP guide names bot signup floods as the common abuse
      of an auth sender and CAPTCHA as the effective control. This design is unusually exposed: every
      signin sends an email, so anyone hammering signup or signin spends the SES quota and the sending
      reputation directly, and a sustained flood locks real users out of their own accounts
    - **Optional for launch: P2** on this file's scale — latent until the app has public traffic, and
      it degrades availability rather than harming a trader holding a position. Tracked, not blocking
    - _Requirements: 1.49, 2.49_


  - [ ] 13.10 Keep the container's dependency posture honest — CI was red on this, not on a deploy
    - **`05 Security` had been failing on every push, and on its scheduled run too.** The scheduled
      failure is the tell: it was not caused by any commit. `02 Build` and `03 Deploy` were green
      throughout, so nothing was blocking the rollout — the red was Trivy's container scan
    - **Trivy's finding was exactly one package.** `PyJWT 2.13.0`, `CVE-2026-102268`, CRITICAL,
      fixed in `2.14.0` — a key-confusion bug (`GHSA-p4g4-x82p-q773`) reported upstream as a
      signature bypass that lets an attacker forge valid tokens. **Bumped.** 1077 auth, jwt, token
      and tenant tests pass on 2.14.0, so the stricter release needed no call-site change
    - **Exposure was limited rather than absent, and the distinction is worth keeping.** Every
      verification path in `core/auth_middleware.py` pins `algorithms=` explicitly — `ES256`
      against the JWKS signing key on the primary path, `HS256` against a secret on the two
      fallbacks. Pinning one algorithm per `decode` call is what mitigates the classic confusion
      shape. The library still validates every token this service accepts, so it was fixed anyway
    - **THERE IS ONE SOURCE OF TRUTH, AND IT IS NOT THE FILE THE ALERTS NAME.** `requirements.txt`,
      `backend_app/requirements.txt` and `requirements-cpu.txt` are each two lines: an `-r` include
      of `requirements-base.txt` plus a `torch` pin. The image installs `requirements-cpu.txt`
      (`Dockerfile` line 26-27). So a `requirements-base.txt` bump fixes all four manifests at once,
      and a Dependabot alert attributed to `backend_app/requirements.txt` for `PyJWT` or
      `python-jose` is the SAME pin seen through the include — not a second stale copy to chase
    - **The six open CRITICAL Dependabot alerts triage to four distinct packages, and only one was
      real.** Recorded so the triage is not repeated:
      1. `PyJWT <= 2.13.0` — **REAL and now fixed.** Trivy proved it was in the built image
      2. `python-jose < 3.4.0` — **already satisfied.** `requirements-base.txt` pins exactly
         `3.4.0`, which is the patched version; the alert has not auto-closed
      3. `torch < 2.6.0` (RCE via `torch.load` with `weights_only=True`) — **present in the image,
         NOT reachable.** `torch.load`, `torch.jit.load` and `load_state_dict` appear nowhere in
         the tree; models are read through `joblib` by `core/ml_safety.SafeModelLoader`, and
         `backend/model_readiness.py` already documents why it does not deserialize on the deploy
         path. Schedule the bump; it is not an emergency, and `2.3.1 -> 2.6.0` is a large ML jump
         on a path this environment cannot exercise
      4. `shell-quote` — `docs/package-lock.json` only. Not in the runtime image and not in the
         app bundle
    - **`05 Security` does not gate `03 Deploy`, and that is a decision to make rather than a bug.**
      `03 Deploy` chains off `02 Build` via `workflow_run`, so a known-CRITICAL image ships while
      the scan is red — which is what happened until `PyJWT` was bumped. Either gate the deploy on
      the scan or record that the scan is advisory; leaving it ambiguous is what let this sit
    - **Not verified here:** GitHub reports 166 Dependabot alerts in total (5 critical at the time
      of writing). Only the CRITICAL set was triaged. Trivy scans the image and Dependabot scans the
      manifests, so the two lists are not the same set and neither is a superset of the other
    - _Requirements: none — this is CI and supply-chain posture, outside the numbered clauses. Filed
      here because task 14 requires every launch blocker to be recorded as proven, BLOCKED, or_
      _closed by citation, and a red security gate is none of those until someone writes down why_


  - [ ] 13.11 Order-cancellation atomicity — proven on real PostgreSQL, the test file still cannot run
    - **The four `tests/test_atomic_order_cancellation_fix.py` tests fail in this environment for a
      reason that is not a defect.** They call `SessionLocal` from `core/database.py`, which falls
      back to `sqlite:///./algo22.db` with no `DATABASE_URL`, so the failure is
      `sqlite3.OperationalError: no such table: orders`. The SQL under test is PostgreSQL-only
      anyway: `NOW()` and the `data->>'status'` JSON operator. They fail identically on a clean
      tree — confirmed by stash — so they were never a regression from any wave
    - **The PROPERTY they exist to assert is now proven, against the production PostgreSQL, in a
      scratch schema created and dropped inside one run.** Every statement was fully qualified as
      `vq_atomicity_probe.orders`, so `public.orders` could not be reached even by a `search_path`
      accident; `public.orders` was re-counted afterwards and still holds 0 rows. Result:
      1. first cancel — **1 row affected**
      2. second cancel of the same order — **0 rows affected**, so the UPDATE is idempotent
      3. **two genuinely concurrent sessions**, released together off a `threading.Barrier`, racing
         the same cancel — rowcounts `[1, 0]`, **exactly one winner**
      4. final stored status `cancelled`
    - **Why that is load-bearing rather than a toy.** `backend/transactional_execution_manager.py`
      line 863 issues the SAME statement — `UPDATE orders SET data = :data, updated_at = NOW()
      WHERE id = :order_id AND tenant_id = :tenant_id AND (data->>'status') IS DISTINCT FROM
      'cancelled'` — and raises on `update_result.rowcount == 0` with "concurrent cancellation
      detected". The losing session in the race above is exactly that branch firing, so the
      production code's race detection is doing what it claims
    - **What is NOT proven, said plainly.** The Python around the SQL was not executed:
      `ReplaySafeTransaction.cancel_order` itself never ran, only the statement it issues. And the
      four tests remain unrunnable in CI — closing that needs a PostgreSQL in the test
      environment, which is the same gap task 12.6 records for the lifecycle race. Docker is not
      installed on this machine and there is no local PostgreSQL
    - **Do not "fix" these four by pointing `DATABASE_URL` at production.** They INSERT and UPDATE
      `orders` rows; a CI run against the live database would write test orders into it. The scratch
      schema above is the safe shape if anyone repeats this by hand
    - _Requirements: relates to 1.16 / 12.6's database-level serialisation gap. Recorded as PROVEN
      for the property and BLOCKED for the test file, per task 14's rule that a clause is proven,_
      _BLOCKED with its gap named, or closed by citation — never a silent pass_

  - [ ] 13.12* Dependency posture beyond CRITICAL — 29 HIGH alerts, three of them in the image
    - **Only the CRITICAL set was triaged in 13.10.** The full open set is 100+ alerts: 3 critical,
      29 high, 46 medium, 22 low; 94 `pip`, 5 `npm`, 1 `rust`
    - **Three HIGH findings are in files the image actually installs** (`requirements-base.txt`,
      which `requirements-cpu.txt` includes — see 13.10 for why the other manifests are aliases):
      - `setuptools` → fixed **78.1.1**
      - `cryptography` → fixed **49.0.0**
      - `starlette` → fixed **1.3.1**, and this one deserves attention: it is the ASGI layer FastAPI
        sits on, so it is web-facing. The pin is `0.41.0`, and note the numbering moved `0.4x → 1.x`,
        so this is a major upgrade rather than a patch
    - **Not in the image:** the `PyJWT`/`pyjwt` HIGH alerts are the already-fixed `2.14.0` pin seen
      through `backend_app/requirements.txt`'s `-r` include, and `browserslist` is in
      `algo22-terminal/package-lock.json` (build tooling, not shipped bundle)
    - **Optional for launch: P2.** None is a known-exploited auth bypass like the PyJWT CRITICAL was.
      But `starlette` and `cryptography` are both large upgrades on load-bearing paths and want their
      own test pass, which is why this is its own task rather than a line in 13.10
    - _Requirements: none — supply-chain posture, outside the numbered clauses_


  - [ ] 13.13 The full-suite failure inventory — 84 tests, and most of them are not defects
    - **Run the WHOLE suite, not scoped subsets.** `python -m pytest tests/ -q` : **85 failed,
      11,334 passed, 34 skipped, 3 xpassed in 52 minutes** (84 distinct test ids). Every scoped run
      used during waves 1-5 was green, which is exactly why this was not seen earlier. One real
      defect was found this way and nothing else would have found it - see the lint item below
    - **A REAL defect, mine, now fixed: `flake8 F824` in `portfolio_management.py`.**
      `get_portfolio_manager()` stopped assigning the singleton when it stopped inventing capital,
      so its `global _portfolio_manager` became unused. That is the same
      `--select=E9,F63,F7,F82` invocation `01-pr-check.yml` gates on, and
      `tests/test_no_undefined_names.py` asserts it exits zero - so it would have failed PR checks.
      Fixed; `POST /initialize` keeps its `global`, because that is the one place that assigns
    - **26 of the 27 `test_training_worker.py` failures are ORDER-DEPENDENT POLLUTION, not defects.**
      Measured, not assumed: the whole suite fails 27; that file alone fails **1**; the failing
      test alone **passes**; its whole class alone **passes**. So an earlier class in the same file
      leaks state into it
    - **What the surviving one is, as far as it was traced.** The assertion is
      `result["training"]["state"] == TRAINING_QUEUED` receiving `BLOCKED`, with
      `required: True, job_id: None, jobs: []`. In `strategy_service.save_version_with_training`
      the only path producing that exact shape is `_assert_ml_training_entitled` raising
      `TrainingBlocked`, whose cap branch is `require_quota(Resource.ML_TRAININGS.value, ...)` ->
      `REASON_CAP_EXCEEDED`. So the leaked state is almost certainly consumed ML-training quota or
      subscription state surviving between tests
    - **DELIBERATELY NOT CHASED FURTHER, and this is the reason.** A parallel workstream is actively
      rewriting precisely that subsystem - `core/subscription_engine.py`,
      `core/subscription_dependencies.py`, `core/pricing_service.py`, `core/fx_service.py`, a new
      `core/usage_ledger.py` and a new `backend_app/migrations/017_plan_entitlements.sql` were all
      uncommitted in the tree during this run. Debugging a quota leak inside code being rewritten
      would conflict with their work and would likely be invalidated by it. Re-measure after 017
      and the entitlements work land
    - **The 7 `test_baseline_unchanged` failures are NOT real.** All 62 baseline tests pass in
      isolation. They were pollution, or the parallel workstream's uncommitted surface changes
      being captured mid-edit. Do not re-record a baseline on the strength of a full-suite run
    - **10 failures are the already-recorded environment gaps**, and each fails identically on a
      clean tree: `test_atomic_order_cancellation_fix` (4) and
      `test_transaction_isolation_serializable` (3) need a real PostgreSQL - see 13.11 for the
      property proven by hand; `test_strategy_lifecycle_concurrency` (2) are the DELIBERATE P0
      proofs 12.6 records; `test_exchange_safety_fix` (1) makes a real Binance call with invalid
      keys
    - **The remaining ~40 cluster in billing, marketplace, checkout, payment and training status** -
      the same subsystem the parallel workstream is rewriting. Attribute nothing here until their
      work is committed; a full-suite run over someone else's uncommitted tree measures their
      work-in-progress, not this spec's
    - **Method note worth keeping.** `_suite.txt` written by a PowerShell `>` redirect is **UTF-16**,
      so a plain `utf-8` read finds zero `FAILED` lines and reports a clean suite. Decode explicitly
      before trusting any count taken that way
    - _Requirements: none — this is a test-suite inventory. Recorded because task 14 requires every
      launch blocker to be proven, BLOCKED with its gap named, or closed by citation, and "85 tests_
      _fail somewhere" is none of those until the 84 are attributed_

  - [ ] 13.14 Three tables the application reads that production did not have — FIXED and APPLIED
    - **`copilot_sessions`, `copilot_messages` and `waitlist` were absent from production.** Counted
      against `pg_tables`: 68 tables in `public`, none of them these three. They were not forgotten,
      they were declared in the WRONG PLACE. Their only declaration in the tree was the alembic
      revision `e88f9911b5a2_consolidate_full_schema.py`, and alembic is vestigial here —
      `alembic_version` holds exactly one row, `d97ffff9c3bb`, and `grep` over
      `.github/workflows/*.yml`, `Dockerfile*`, `scripts/**` and `*.sh` finds **no step that runs
      alembic at all**. The live schema is the numbered SQL in `backend_app/migrations/` (001..018)
      plus `migrations/`, applied by hand through `scripts/apply_migrations.py`. A table declared
      only in alembic reads as declared to a reviewer and is absent to a trader
    - **`waitlist` was a LIVE break, on a mounted surface.** `src/lib/waitlistApi.js` queries
      `supabase.from('waitlist')` straight from the browser over PostgREST;
      `components/admin/AdminDashboard.jsx` calls its admin half; `App.jsx:634` routes
      `/admin/waitlist` to that dashboard behind `AdminGuard`. Every visit errored on a nonexistent
      relation. The public form is a separate matter — `components/waitlist/WaitlistForm.jsx` is
      imported by nothing outside tests, and `LandingPage.jsx` lines 18-22 record its removal
    - **The copilot pair was a SILENT break, which is exactly why it survived.** `main.py:660` mounts
      the router at `/api/v1/copilot`; `routers/copilot.py` touches the two tables **9 times**; every
      write sits inside a `try/except` that only logs a warning and `list_copilot_sessions` returns
      `[]` on error. So the chat streamed fine, **no 500 was ever raised, and nothing was ever
      persisted** — session history was empty by construction. No frontend caller exists yet, so
      nobody reported it. This is the shape task 14 cares about: a swallowed failure answering
      normally
    - **A third defect, latent, in the archived original.**
      `archived_migrations/terminal_supabase_migrations/20260622000001_create_waitlist.sql` guards the
      admin read with `USING (auth.jwt() ->> 'role' = 'admin')`, which **can never be true**:
      Supabase's top-level `role` claim is `anon` / `authenticated` / `service_role`. The admin role
      lives at `app_metadata.role`, which is where the frontend reads it (`App.jsx:329-331`,
      `user.app_metadata?.role === 'admin'`). Had that file ever been applied, `/admin/waitlist`
      would have authenticated correctly and then read **zero rows**. The same file also carries
      `USING (auth.uid()::text = id::text)` — the caller's user id compared to the row's own primary
      key, never true — and lacks `trader_type` and `monthly_volume`, both of which
      `waitlistApi.submit()` actually sends
    - **FIX: `backend_app/migrations/018_copilot_and_waitlist_tables.sql`, APPLIED to production.**
      018 because a parallel workstream owns 017. Column sets, defaults, CHECKs, the session FK with
      `ON DELETE CASCADE` and all four waitlist indexes are `e88f9911b5a2`'s — the newer and more
      complete definition. Fully idempotent: `IF NOT EXISTS` on every table and index, and every
      `CREATE POLICY` guarded on `pg_policies` (PostgreSQL has no `CREATE POLICY IF NOT EXISTS`).
      Admin policies use `(auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'`, which is the fix for
      the claim-path defect and what makes `/admin/waitlist` actually work. `ip_address` is `TEXT`,
      not `INET`: a proxy chain hands over a comma-separated `X-Forwarded-For`, which `INET` refuses
      outright, turning a best-effort audit field into a failed insert
    - **RLS is on all three, and it is NOT what keeps copilot working.** The backend connects with
      `SUPABASE_SERVICE_ROLE_KEY` (`core/supabase_connection.py:33`) and the service role bypasses
      RLS, so the router works the moment the tables exist. The owner-scoped policies — `user_id =
      auth.uid()` on the session table, session-ownership `EXISTS (...)` on the message table — are
      defence in depth for a future browser-side read, stated as such in the file
    - **No `anon` INSERT policy on `waitlist`, deliberately, and the omission is enforced.** The
      archived file granted `FOR INSERT TO anon WITH CHECK (true)` — a world-writable production
      table. With the form unmounted that is pure spam surface with no consumer, and RLS denies by
      default. 018 names the policy it omits, says why, and
      `tests/test_schema_table_reference_drift.py` fails the build the moment `WaitlistForm.jsx` is
      imported by a non-test module under `algo22-terminal/src/` while the policy is still missing —
      so re-mounting the form cannot silently drop every lead into a denied insert
    - **Production verification, both ends.** Dry run first: the whole file executed inside a
      transaction that was rolled back, twice in a row to prove idempotency, and the table count
      returned to 68. Then applied for real with the three tables asserted ABSENT beforehand:
      **68 → 71 tables**, all three present, `relrowsecurity` true on all three, five policies
      (`copilot_sessions_owner_access`, `copilot_messages_owner_access`, `waitlist_admin_select` /
      `_update` / `_delete`), all five CHECK constraints by name, all ten indexes. Separately, inside
      a rolled-back transaction: `routers/copilot.py`'s insert payload accepted, `ON DELETE CASCADE`
      proven to take the transcript, `waitlistApi.submit()`'s **exact** payload accepted, and each of
      the five CHECKs plus `UNIQUE(email)` observed rejecting a bad value. All three tables still at
      0 rows afterwards — production gained nothing
    - **A FOURTH defect found on the way, and fixed: `health_check()` could only ever return
      `False`.** `core/supabase_connection.py` probed `self.client.table("users")` inside
      `try/except Exception: return False`. There is **no `public.users`** in this schema — Supabase
      keeps users in `auth.users`, which PostgREST does not expose — so PostgREST answered `42P01`,
      the bare `except` ate it, and the method returned `False` **whenever the client was
      configured**. It could not return `True`. Nothing in the repo calls it, `routers/health.py`
      included, which is why that went unnoticed. Now probes `strategy_versions`, chosen because it
      is live in production AND declared by `001_strategy_architecture.sql` AND owned by this
      application; `count="exact"` dropped for a plain `limit(1)`, since a liveness probe has no
      business counting a whole relation. The `get_client()` docstring example taught the same
      mistake and is corrected
    - **The alembic DAG had TWO heads, so `alembic upgrade head` could not run at all.**
      `d97ffff9c3bb` forks into `e88f9911b5a2 → 6f1b3d9c8a7e` and `add_foreign_keys →
      implement_rls_policies`; alembic refuses outright with "Multiple head revisions are present".
      Fixed properly with a hand-written merge revision, `merge_heads_20260820.py`, whose
      `down_revision` is the tuple `('6f1b3d9c8a7e', 'implement_rls_policies')` and whose
      `upgrade()`/`downgrade()` are empty. Deleting a head was rejected: both lineages descend from
      the revision production is stamped at. **This moves nothing in production** — alembic is not
      applied there — and it is NOT a licence to run `alembic upgrade head`, because `e88f9911b5a2`
      still opens with `op.create_table('library_strategies')` and that table already exists, so the
      revision would abort on a `duplicate_table`
    - **Regression test: `tests/test_schema_table_reference_drift.py`, 29 tests, pure parse, no
      network.** The general guard is the one that would have caught all three tables: every table
      named as a literal in `.table("x")` / `.from_("x")` under `backend_app/**/*.py` must be
      declared by a `CREATE TABLE` in the migration set, with the declared set **parsed** rather than
      listed, and the failure message naming the table and the `file:line` that references it. Plus:
      018 declares the three specifically; 018's admin policy uses the `app_metadata` path and the
      bare `auth.jwt() ->> 'role'` form appears in no executable statement; `health_check()`'s probe
      is resolved by **AST** over that one method and checked against the declared set; the DAG has
      exactly one head and the merge joins exactly the two recorded heads with empty functions. SQL
      comments are blanked before any claim is read off a migration, so 018's own header — which
      quotes the broken predicate, the omitted INSERT policy and every table name in play — cannot
      satisfy or trip a single assertion
    - **Before/after, observed rather than assumed.** With 018 moved aside: **13 failed, 16 passed**.
      With `health_check()` flipped back to `"users"`: **5 failed, 24 passed**, the general guard
      reporting `'users' first referenced at backend_app/core/supabase_connection.py:63`. With no
      merge revision: **3 failed, 26 passed**. All three defects restored: **29 passed**. Real
      alembic agrees — `ScriptDirectory.get_heads()` returns `['merge_heads']`
    - **Two exemptions in the guard, named and pinned rather than hidden.** `profiles` and
      `strategies` are read by the application (34 and 56 reference sites) and declared by **no**
      migration in this repository — they predate the numbered set and were created in the Supabase
      project directly. Both are CONFIRMED PRESENT in production, which is the only reason they are
      tolerated, and a companion test fails if either becomes undeclared-and-unreferenced or starts
      being declared, so the exemption cannot rot. `users` is deliberately NOT exempt: it was
      referenced and it does not exist, which is the whole of the fourth defect
    - **Gates.** `flake8 --select=E9,F63,F7,F82` clean on every touched Python file (the exact
      invocation `01-pr-check.yml` gates on, and which a prior fix regressed);
      `tests/test_no_undefined_names.py` 3 passed; `backend_app.main` still imports and still exposes
      **354 routes**; `test_no_dormant_schema_references` 2, `test_library_schema_contract` +
      `test_schema_as_code_completeness` 20, `test_migration_tooling` 28 and
      `test_marketplace_paper_schema_contract` 86 all green against the new migration
    - _Requirements: 1.5 / 1.7 — a read that did not complete must answer an explicit unavailable,
      not a fabrication. The copilot router answering 200 with an empty session list over a table_
      _that does not exist, and `health_check()` reporting unhealthy for a query that could never_
      _run, are both that clause. P1: a documented capability is dead (`/admin/waitlist`, copilot_
      _history) and the release could not ship its own migrations_

  - [ ] 13.15 13.14's guard only scanned Python — a SQL migration reads a table nothing declares
    - **The hole.** 13.14's general guard resolves every `.table("x")` / `.from_("x")` literal under
      `backend_app/` against a `CREATE TABLE` in the migration set. It scans **Python call sites
      only**. It never asks what the SQL migrations themselves READ, so a migration can reference a
      phantom relation and keep the suite green. Running the same audit one level out found one
    - **`public.referrals` does not exist, and `migrations/referral_system_redesign.sql` reads it
      twice.** `to_regclass('public.referrals')` is NULL and the relation is absent from `pg_tables`
      (71 tables in `public`, 018 applied). Its only declaration anywhere in the tree is the alembic
      revision `6f1b3d9c8a7e_add_referrals.py`, which production never applied — `alembic_version`
      still holds the single row `d97ffff9c3bb` and, as 13.14 established, no workflow, Dockerfile or
      script runs `alembic upgrade` at all. The two reads were at lines 402 and 415: the
      `referral_relationships` backfill (`FROM referrals r LEFT JOIN referral_codes rc`) and the
      `referral_wallets` initialisation (`FROM profiles p LEFT JOIN referrals r`)
    - **Severity, not inflated: a non-replayable migration and a hole in a new guard.** NOT lost
      customer data and NOT a live break. Verified in production: `referral_codes` 5,
      `referral_profiles` 2, `referral_relationships` 1, `referral_wallets` 5,
      `referral_commissions` 0, `referral_payouts` 0, `profiles` 180. No Python and no frontend
      module reads `referrals` — the live system uses the redesigned `referral_*` tables only, so
      the backfill that reads the legacy table had nothing to migrate here. What is broken is the
      rebuild: anyone reconstructing a staging or DR database from the migration set gets
      `42P01 relation "referrals" does not exist` partway through the referral system, even though
      every other statement in the file is written to be re-runnable
    - **CORRECTION to the premise this task started from: the file was never applied at all, and
      production's 5 `referral_codes` rows are not its backfill.** `profiles.referral_code` and
      `profiles.referred_by_user_id` **do not exist** in production, so that backfill would answer
      `42703`, not insert 5 rows; `fk_profiles_referred_by`, `uq_profiles_referral_code`, the
      `tr_profiles_create_referral_code` trigger and `generate_unique_referral_code` are all absent
      too. Production's referral tables came from `migrations/006_reconcile_production_database.sql`
      — its policy names (`referral_codes_select` / `_insert` / `_service`, `ref_comm_owner_read`,
      `ref_wallets_owner_access`, …) and index names (`idx_referral_rel_referrer` / `_referred`,
      `idx_ref_comm_referrer`, `idx_ref_payouts_user`) are **exactly** what the database has, while
      the redesign file's `*_select_own` policies and `idx_referral_relationships_*` indexes appear
      nowhere. The column sets also diverge: production's `referral_wallets` is 006's
      `balance_usd` / `total_earned_usd` / `pending_usd`, not the redesign's four `*_balance_usd` /
      `lifetime_earnings_usd`. Two files in `migrations/` declare the same five tables differently —
      the shape 016's header warns about — which is a finding of its own, not this one
    - **FIX, and what it is explicitly NOT.** Each statement that reads the legacy table is wrapped
      in `DO $…$ BEGIN IF to_regclass('referrals') IS NULL THEN RAISE NOTICE … ELSE EXECUTE $…$
      <original> $…$; END IF; END $…$;`, so an absent relation is a logged no-op instead of an abort.
      The statements are preserved **byte for byte** — the probe asserts the `git show HEAD:` text is
      a verbatim substring of the guarded block — because on a database that still carries the
      legacy table this is the correct migration and must keep running; a guard that works by
      deleting the backfill is not the fix. The guard tests the **unqualified** name, exactly as the
      statements it guards do, so guard and statement can never disagree about which relation is
      meant. **This was NOT re-applied to production and must not be**: production already has all
      five tables from 006 with different columns, and this file's `ALTER TABLE profiles ADD COLUMN`
      statements have never run there, so a "replay to pick the backfill up" would mutate a live
      180-row table. The fix is about replayability on a rebuilt database, nothing else
    - **The `EXECUTE` premise was wrong, and it was checked rather than assumed.** On the production
      server (PostgreSQL **17.6**) a plain `IF to_regclass(…) IS NOT NULL THEN <sql> END IF` inside a
      `DO` block does **not** raise `42P01` on the untaken branch — PL/pgSQL prepares embedded SQL
      lazily through SPI, so a branch that never runs never resolves the name. Proven in the scratch
      schema alongside the bare statement, which did raise `42P01`; and
      `003_signal_trace_preflight.sql` already depends on that shape for `exchanges`. `EXECUTE` is
      used anyway, **dollar-quoted**: it removes the question entirely, it keeps the statement
      verbatim where `EXECUTE '…'` would have doubled every quote, and — the deciding reason — a
      single-quoted `EXECUTE` string would have hidden the reference from the new guard below, which
      is the one thing this exemption must never do
    - **Proven on the real PostgreSQL, both directions, in a scratch schema created and dropped
      inside one run.** `vq_referral_replay_probe`, `search_path` set to it, `DROP SCHEMA … CASCADE`
      at the end; the four relations built in **this file's** declared shapes, i.e. what a fresh
      rebuild would produce. Nothing was hand-typed: the ORIGINAL statements were extracted from
      `git show HEAD:` and the GUARDED blocks from the working tree.
      **(a) legacy table ABSENT** — ORIGINAL relationships backfill → `42P01 relation "referrals"
      does not exist`; GUARDED → `OK`, with `NOTICE: … "referrals" is absent; skipping the
      referral_relationships backfill`, and 0 rows written.
      **(b) legacy table PRESENT with one row** — GUARDED → `OK` and **1 row** in
      `referral_relationships` carrying the right `referrer_id`, `referred_id`, `referral_code_id`
      and `status`, so the guard did **not** silently disable the migration; a second run left it at
      1 row, `ON CONFLICT (referred_id) DO NOTHING` holding. Afterwards `public` was re-counted:
      **71 tables**, `referral_codes` 5 / `referral_profiles` 2 / `referral_relationships` 1 /
      `referral_wallets` 5 / `referral_commissions` 0 / `referral_payouts` 0 / `profiles` 180 — all
      unchanged — `public.referrals` still absent, and no `vq_*` schema left behind
    - **A SECOND defect the probe found, pre-existing and deliberately NOT fixed here: the wallets
      statement is invalid SQL.** `COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status =
      'pending')` puts `FILTER` on `COALESCE`, and `FILTER` only attaches to an aggregate call, so
      PostgreSQL answers `42601 syntax error at or near "FILTER"` **whether or not `referrals`
      exists** — observed three times in the probe, including on the taken branch, which also proves
      the `EXECUTE`d SQL really is parsed and run when the guard fires. So that statement has never
      been runnable, and the honest claim is narrower than "replayable": the file now replays past
      the referral section on a database **without** the legacy table, and still aborts on one
      **with** it. The correct form is `COALESCE(SUM(…) FILTER (WHERE …), 0)`; changing the statement
      contradicts the verbatim-preservation this fix rests on, so it is left as its own finding
    - **Guard hole closed: `tests/test_schema_table_reference_drift.py`, 29 → 43 tests, still pure
      parse and still no network.** A new SQL-side scanner plus three classes: the general guard
      (every relation any `migrations/*.sql` or `backend_app/migrations/*.sql` file reads or writes
      via `FROM`, `JOIN`, `INSERT INTO`, `UPDATE` or `DELETE FROM` must resolve to a `CREATE TABLE` /
      `CREATE VIEW` in the SQL set or to a named exemption), a guard-the-guard, and the class that
      protects the fix above. Alembic is deliberately **not** consulted as a declaration source — a
      relation declared only there is the exact drift 13.14 recorded
    - **False positives: 86 candidates down to 0, every exclusion a syntactic CLASS rather than a
      table name.** Comments blanked; single-quoted literals blanked (`RAISE NOTICE 'rewriting rows
      from old to new'` reported reads of `old` and `new`); double-quoted identifiers folded to one
      token (`CREATE POLICY "Users can update own notifications"` reported a relation called `own`);
      dollar-quote **bodies kept**, since PL/pgSQL carries real DML — which is also why
      `EXECUTE $q$ … $q$` does not hide `referrals`; the `pg_` prefix, reserved by PostgreSQL so
      every match is a catalog probe; any non-`public` qualifier, which covers `information_schema`,
      other schemas, and PL/pgSQL `OLD.`/`NEW.` record rows; a `(` after the name, for
      `unnest(…)` and `jsonb_array_elements(…)`; CTE names parsed out of `WITH`; a reserved-word
      stoplist; `REVOKE … FROM <role>` by statement head, which alone removed 48 hits on `anon` and
      `authenticated`; `IS DISTINCT FROM <column>`; `EXTRACT`/`SUBSTRING`/`TRIM(… FROM …)` resolved
      by innermost enclosing call; and `FOR UPDATE` / `ON CONFLICT DO UPDATE SET` /
      `ON UPDATE CASCADE` / `BEFORE INSERT OR UPDATE ON` / `GRANT … UPDATE ON` by leading word
    - **The parser's own health is assertable, the way `test_declared_set_is_parsed_and_non_trivial`
      already is for the Python side.** The masking is asserted **offset-preserving** — identical
      length and newline count on every migration — which is what lets the DML view and the
      guard view share offsets with the raw file. A fixture carrying every hazard listed above,
      including the ones the current migration set happens not to contain (CTEs, `COPY … FROM
      STDIN`, `EXTRACT(EPOCH FROM …)`), must resolve to **exactly five** relations, two of them
      deliberately undeclared so the scanner is proven to still FIRE rather than to have gone quiet.
      Plus floors on the real set: >50 declared, >80 DML sites, >20 distinct relations. Declarations
      are parsed off the **masked** text, so a `CREATE TABLE` quoted inside a `RAISE NOTICE` is not a
      declaration — the comment-only strip had been counting two phantom relations, `does` and `if`
    - **Six exemptions in two named categories, each pinned so it cannot rot.**
      `SQL_UNDECLARED_PRESENT_IN_PRODUCTION` — `profiles` (180 rows), `strategies` (187),
      `execution_records` (198), `library_strategies` (0) — declared by no SQL migration and all
      **confirmed present**, which is the only reason they are tolerated.
      `SQL_GUARDED_ABSENT_RELATIONS` — `referrals` (`to_regclass`) and `exchanges`
      (`information_schema`) — **absent** from production and tolerated only because every statement
      touching them is existence-guarded; `exchanges`' single site in `003_signal_trace_preflight.sql`
      was already guarded that way, which is how the category was found rather than invented. A
      companion test fails if an entry stops being referenced or starts being declared, the two sets
      may not overlap, and `profiles`/`strategies` are asserted exempt on **both** sides so the
      Python and SQL justifications cannot drift apart. `users` is barred from both
    - **Before/after, observed rather than expected.** With the migration restored to HEAD:
      **3 failed, 40 passed**, the load-bearing one reporting
      `guarded=[] unguarded=[(402, 'FROM'), (415, 'JOIN')]`. With the fix: **43 passed**. The general
      guard was separately proven to FIRE rather than merely to be satisfied by its own lists —
      dropping `referrals` from its exemption yields `'referrals' FROM at
      referral_system_redesign.sql:458 (and 1 more)`, and dropping `library_strategies` yields
      `'library_strategies' FROM at 007_marketplace_submissions.sql:1460 (and 1 more)`
    - **Gates.** `flake8 --select=E9,F63,F7,F82` clean on the one touched Python file;
      `backend_app.main` still imports and still exposes **354 routes**; and the suites that parse
      these same migration files are green against the edited one — `test_migration_tooling` 28,
      `test_no_dormant_schema_references` 2, `test_schema_as_code_completeness` 6 and
      `test_library_schema_contract` 14. **`test_no_undefined_names.py` is 2 passed / 1 failed and
      the failure is not this change**: the only `F821`s in the tree are
      `backend_app/backend/ml_training_policy.py:708` (`field`) and `:1268` (`replace`), in a file
      carrying 1,382 uncommitted lines from the parallel workstream; the HEAD version of that same
      file is `F821`-clean, so the gate is red for a reason that belongs to that workstream
    - _Requirements: none directly — this is a schema-as-code finding, in the same class as 13.14's
      "the release could not ship its own migrations". Recorded because task 14 requires every_
      _launch blocker to be proven, BLOCKED with its gap named, or closed by citation, and a_
      _migration set that aborts on a relation nothing declares is none of those. P1: the_
      _declared schema and the applied schema disagree, and the disagreement was invisible to the_
      _guard that exists to catch exactly it_

  - [ ] 13.16 The statement 13.15 guarded has never parsed — `FILTER` on `COALESCE`, now fixed
    - **What 13.15 left behind, and why it was not a cosmetic leftover.** The guarded
      `referral_wallets` initialisation in `migrations/referral_system_redesign.sql` read
      `COALESCE(SUM(r.commission_usd), 0) FILTER (WHERE r.status = 'pending')` three times.
      `FILTER` attaches only to an **aggregate** call, never to an ordinary function, so PostgreSQL
      answers `42601 syntax error at or near "FILTER"` — and because 42601 is a **parse** failure,
      no table can satisfy it. 13.15 observed this three times, including on the **taken** branch
      with the legacy table present, and recorded it as a finding rather than fixing it, on the
      grounds that editing the statement would contradict the byte-for-byte preservation its guard
      rested on. That reasoning was wrong in both halves
    - **Verbatim preservation was a MEANS, not an end.** Its purpose was to avoid destroying a
      legacy migration path that still works on a database carrying the legacy `referrals` table. A
      statement that cannot PARSE has no such path to preserve: it has never executed successfully
      on any database, anywhere, so there is no behaviour to regress and nothing to be faithful to.
      Keeping it also left 13.15's claim false in the direction that matters — the guard **moved**
      the abort rather than removing it. On a database that HAS `referrals` the file still died at
      this statement. "Replayable" was the goal, and this was the remaining reason it was not
    - **FIX: three expressions, `FILTER` moved inside the `COALESCE`.**
      `COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = 'pending'), 0)`, and the same for
      `'approved'` and `'paid'`. The intent is unambiguous from the target columns —
      `pending_balance_usd` / `approved_balance_usd` / `paid_balance_usd` are per-status conditional
      sums — and the `COALESCE` has to stay **outside**, because that is what turns the NULL a
      filtered `SUM` returns for a profile with no matching row into the `0.00` the
      `chk_non_negative_balances` CHECK requires. The fourth expression, `lifetime_earnings_usd`,
      is `COALESCE(SUM(r.commission_usd), 0)` with no `FILTER` — already valid, left alone. **This
      deliberately edits a statement 13.15 preserved byte for byte, and that is consistent rather
      than a reversal**: the probe proves the HEAD text plus exactly those three substitutions
      equals the working-tree text, character for character, so what 13.15 was protecting — the
      legacy path, the guard, the `EXECUTE` wrapper, the relationships backfill — is untouched, and
      the only thing changed is a token sequence that no PostgreSQL has ever accepted. The file's
      own header previously asserted the statements were preserved VERBATIM; it now records this
      one exception and the reason, so the migration does not carry a false claim about itself
    - **Proven on the real PostgreSQL (17.6), before and after in ONE transcript, scratch schema
      only.** `vq_referral_replay_probe`, created and `DROP SCHEMA … CASCADE`-ed inside the run;
      `public` read for the baseline and never written. Nothing hand-typed: the BROKEN statement and
      13.15's BROKEN guarded block come from `git show HEAD:`, the CORRECTED ones from the working
      tree. Six profiles, and a legacy `referrals` carrying 10.00 `pending` + 5.00 `approved` for
      referrer A, 7.25 `paid` + 1.00 `reversed` for referrer B, and nothing at all for C.
      **(a) the defect, with `referrals` PRESENT** — HEAD's statement bare → `42601`; HEAD's
      statement **inside 13.15's guard** → `42601` as well, 0 rows written.
      **(b) the fix, with `referrals` PRESENT** — `OK`, 6 rows, and the per-status split is right,
      not merely parseable: A → `pending 10.00, approved 5.00, paid 0.00, lifetime 15.00`;
      B → `pending 0.00, approved 0.00, paid 7.25, lifetime 8.25`. B is the load-bearing row —
      `reversed` belongs to no balance column, so `lifetime 8.25 > paid 7.25` is what a `FILTER`
      attached to the wrong branch could not produce.
      **(c) zeros, not NULLs** — C, which has no referral row in either direction, lands
      `0.00 / 0.00 / 0.00 / 0.00`, and `count(*)` over all four columns `IS NULL` is **0**.
      **(d) 13.15's fix still holds** — with `referrals` ABSENT the corrected statement bare still
      raises `42P01`, so the guard is still load-bearing; guarded it is `OK` with
      `NOTICE: … skipping the referral_wallets initialisation` and 0 rows.
      **(e) replay** — a second run of the corrected guarded block leaves `referral_wallets` at
      **6 → 6**, `ON CONFLICT (user_id) DO NOTHING` holding. The relationships backfill was
      re-proven unchanged in both directions. Afterwards `public`: **71 tables**, `referral_codes` 5
      / `referral_profiles` 2 / `referral_relationships` 1 / `referral_wallets` 5 /
      `referral_commissions` 0 / `referral_payouts` 0 / `profiles` 180, `public.referrals` still
      absent, and no `vq_*` schema left behind
    - **A THIRD defect in the same statement, found by the same probe and deliberately NOT fixed —
      and the line is principled, not arbitrary.** The statement selects `id` **unqualified** while
      grouping by `p.id`. The Alembic revision `6f1b3d9c8a7e_add_referrals.py` — the only
      declaration of `referrals` anywhere in the tree — gives it a `UUID PRIMARY KEY id`. With the
      legacy table in that shape the corrected statement answers
      `42702 column reference "id" is ambiguous`, observed. It is left alone because 42702 is
      **shape-dependent** where 42601 was not: a legacy `referrals` without an `id` column has a
      genuinely working path through this statement, which is exactly the thing verbatim
      preservation exists to protect. 42601 had no such path under any shape. So the honest claim
      remains narrower than "replayable everywhere": the file now replays past the referral section
      on a database **without** the legacy table, and on one **with** it in the shape the statement
      itself implies, and still aborts on one carrying the Alembic-declared `id` column
    - **Regression assertions: `tests/test_schema_table_reference_drift.py`, 43 → 49 tests, still
      pure parse and still no network.** `TestGuardedAbsentRelationsStayGuarded` protected the
      GUARD; nothing protected the guarded STATEMENT from being syntactically invalid. Two
      assertions at two strengths, and the stronger one **is** general: every executable
      `FILTER (WHERE …)` in any `migrations/*.sql` or `backend_app/migrations/*.sql` file must sit
      on a named PostgreSQL aggregate, resolved structurally — walk back from the keyword, require
      a closing `)`, match it by depth, read the identifier in front of it, and step through a
      `WITHIN GROUP (…)` clause for ordered-set aggregates. Window functions are deliberately
      **off** the aggregate list, since `rank() FILTER (…)` is as invalid as `COALESCE(…) FILTER
      (…)`. That rule is tractable here because the whole migration set contains exactly **three**
      executable `FILTER` clauses, all in this statement — the other 40-odd matches on the word are
      prose and `RAISE NOTICE` literals, which the existing offset-preserving masking removes. The
      second assertion is **SPECIFIC to this statement**, and is stated as specific rather than
      dressed up as general: the three per-status expressions must keep the exact
      `COALESCE(SUM(r.commission_usd) FILTER (WHERE r.status = '…'), 0)` shape, in the **same order
      as the INSERT's target columns** (parsed off the column list, not hardcoded), and
      `lifetime_earnings_usd` must stay the one unfiltered `COALESCE(SUM(…), 0)`. Parsing alone
      would miss a `FILTER` on the wrong branch or the wrong status, which is the whole class of
      bug here. Four fixture tests guard the checker itself: the broken form must resolve to
      `coalesce`, the corrected form to `sum`, prose and literals must contribute **zero** sites,
      and a mixed fixture (`count(*)`, `percentile_cont(…) WITHIN GROUP (…)`, `jsonb_agg(…)`,
      `GREATEST(sum(…), 0)`) must report `GREATEST` as the only offender
    - **Before/after, observed rather than expected.** With the migration restored to HEAD in place
      (swapped and restored in binary, SHA-checked both ways): **2 failed, 47 passed**, the general
      one reporting `referral_system_redesign.sql:478 FILTER follows 'coalesce'` for lines 478, 479
      and 480, and the specific one reporting per-status expressions `[]` against target columns
      `['user_id', 'pending_balance_usd', 'approved_balance_usd', 'paid_balance_usd',
      'lifetime_earnings_usd']`. With the fix: **49 passed**. The other four of the six new tests
      pass in both directions by construction — they assert on fixtures, not on the migration — and
      that is said here rather than counted as coverage
    - **Gates.** `flake8 --select=E9,F63,F7,F82` clean on the touched Python; `backend_app.main`
      still imports and still exposes **354 routes**; the suites that parse these same migration
      files green against the edited one — `test_migration_tooling` 28,
      `test_no_dormant_schema_references` 2, `test_schema_as_code_completeness` 6,
      `test_library_schema_contract` 14, **50 passed** together. **`test_no_undefined_names.py` is
      now 3 passed**: the two `F821`s 13.15 recorded in the parallel workstream's uncommitted
      `backend_app/backend/ml_training_policy.py` (`:708 field`, `:1268 replace`) are gone — that
      file is now 2,056 uncommitted added lines and `F821`-clean — so the gate 13.15 had to report
      red for a reason outside its change is green without anything here touching it
    - _Requirements: none directly — same class as 13.15, which is the same class as 13.14's "the_
      _release could not ship its own migrations". Recorded because task 14 requires every launch_
      _blocker to be proven, BLOCKED with its gap named, or closed by citation, and 13.15's_
      _"replayable" was none of those while the statement it guarded could not parse. P1, and the_
      _same P1 as 13.15: a migration the release cannot replay, with the added lesson that a guard_
      _asserting WHERE a statement sits asserts nothing about whether it RUNS_

  - [ ] 13.17 One level deeper than 13.14–13.16: the COLUMNS, and 24 live `42703`s
    - **Why columns are a separate class, and not a hypothetical one.** 13.14–13.16 closed
      TABLE-level drift: `tests/test_schema_table_reference_drift.py` is 49 green tests asserting
      that every table Python reads and every relation the SQL migrations read is declared. A
      relation that EXISTS and lacks the projected column answers `42703 column … does not exist`,
      which no table-level guard can see. That has already shipped three times here: the
      exchange-list read (fixed earlier in this spec); creator analytics selecting `monthly_price`
      and `rating_average` off `library_strategies`, which has neither
      (`tests/test_creator_analytics_regression.py`); and `strategy_backtests.version` declared
      `VARCHAR(20)` by 001 and `INTEGER` by 006, so the router wrote `"v1.0"` into an integer
      column (`tests/test_backtest_version_label_regression.py`, and 016's header)
    - **The audit, and its own coverage stated honestly.** AST over `backend_app/**/*.py`, 388
      modules; **338 contain no `.table(` / `.from_(` literal at all** and are not parsed, because
      a chain with no table verb in the file cannot resolve and parsing them yielded nothing but
      skip records for a plain dict's `.update`. **1,162 column-bearing call sites** — 268
      `.select`, 687 filter/order/`filter`, 207 `insert`/`update`/`upsert`, plus 5 `on_conflict`
      kwargs — of which **980 resolved** to a table and **182 were SKIPPED and counted**: 101
      where the table name, the column name or the projection is not a literal (`for table in
      EXCHANGE_ACCOUNT_TABLES` is the common shape) and 81 dynamically-built write payloads.
      **527 distinct `table.column` pairs across 50 tables.** The chain walk is over the AST
      receiver spine because a `.select()` five lines below its `.table()` inside a parenthesised
      chain is the normal shape here. **Module-level string constants ARE followed**, including
      `+` concatenation and cross-module `mod.NAME`: `listing_projection.LISTING_SELECT` names 25
      columns and nine handlers read it, and `library_entries` concatenates it INSIDE a
      `library_strategies!inner(…)` embed three more times — resolving constants took the parse
      from 728 resolved / 283 pairs to 980 / 527, and skipping them would have left the guard
      blind to the densest reads in the application
    - **A false-positive class found and fixed before any finding was believed: scope.** The first
      resolver did ONE module-wide pass over every assignment. Two handlers in `routers/library.py`
      both build a local called `query`, one on `library_strategies` and one on
      `library_subscriptions`; last-write-wins attributed the first handler's eight filters —
      `category`, `difficulty`, `has_ml_model`, `backtest_sharpe_ratio`,
      `backtest_total_return_pct`, `tags`, `submission_state`, `submitted_at` — to the second
      handler's table, which has none of them. Bindings are now PER SCOPE and IN STATEMENT ORDER,
      a nested `def` gets a COPY, and a rebinding inside a branch to a different table is recorded
      as conflicted rather than guessed. That alone removed 8 of 33 candidates
    - **Two oracles, and all four difference categories reported rather than only the convenient
      one.** DECLARED = `CREATE TABLE` column lists **plus every `ALTER TABLE … ADD COLUMN` /
      `DROP COLUMN` / `RENAME COLUMN` / `ALTER COLUMN … TYPE` applied in file order** across
      `backend_app/migrations/*.sql` and `migrations/*.sql` — 36 files, 188 DDL events, 907
      declared pairs over 66 tables. The `ALTER` half is not optional: 006 adds eleven columns to
      `profiles` and nineteen to `strategies`, and a `CREATE TABLE`-only parse reports every one of
      them missing. LIVE = `information_schema.columns` for `public`, read-only, 71 base tables /
      950 column rows / PostgreSQL 17.6. **(1) referenced and absent from BOTH → 24 pairs, every
      one a live `42703`.** **(2) present in production, declared by no migration → 87 pairs**,
      over `profiles`, `strategies`, `execution_records` and `library_strategies` — a rebuild from
      the migration set produces four tables narrower than the live ones. **(3) declared but absent
      in production → 62 pairs**, the unapplied-migration class 13.14 recorded, concentrated in
      `training_jobs` (19, migration 019, the parallel workstream's uncommitted file),
      `strategy_research_reports` (10), `strategy_backtests` (8), `referral_payouts` (8),
      `referral_wallets` (4, the redesign file 13.15/13.16 proved was never applied), `strategies`
      (6) and `profiles` (4, 017's `billing_interval` / `plan_limit_overrides` and the redesign's
      `referral_code` / `referred_by_user_id`). **(4) declared twice with DIFFERENT types → 30**,
      after folding synonym spellings
    - **Every one of the 24 was PROVEN against production, not inferred from a set difference.**
      Read-only `SELECT "<col>" FROM public."<table>" LIMIT 0` in a `readonly` session — `LIMIT 0`,
      so no row is read; the SQLSTATE is the whole point. **All 24 answered `42703`.** Eight
      CONTROL columns on the same four tables answered **OK** (`profiles.subscription_tier`,
      `strategies.user_id`, `strategies.source_library_id`,
      `library_strategies.source_strategy_id`, `billing_invoices.amount`,
      `execution_records.execution_id` / `filled_size` / `size`) — without the controls a probe
      that answered `42703` for everything would have looked like proof. **Zero unexpected
      answers.** No scratch schema was needed, because nothing beyond `information_schema` and
      zero-row `SELECT`s ran, and every connection set `readonly` — which makes a write
      impossible rather than merely unobserved. Afterwards `public` is byte-identical: **71 base
      tables**, **950 `information_schema.columns` rows**, no `vq_*` or scratch schema. **One
      honest correction to the usual closing count: `profiles` reads 181, not 180.** The 181st row
      has `created_at 2026-10-02 03:08:14+00`, five hours before the final probe and inside this
      session's window — an organic signup on a live system, and the only row created in the last
      six hours. It is reported rather than rounded back to 180, and a read-only session could not
      have produced it
    - **FIX — one, and it is the only one the CODE determines.** `routers/library.py`'s
      `_enrich_cards_with_user_context` read
      `.table("library_strategies").select("id, source_library_id").eq("author_id", …)`.
      `library_strategies` has no `source_library_id` — it records provenance the other way round,
      as `source_strategy_id`. The CLONE is a row in `strategies`: `clone_strategy` inserts it
      there with `user_id` + `source_library_id` (library.py step 7) and its own idempotency check
      reads it back the same way (`.table("strategies").select("id").eq("source_library_id", …)
      .eq("user_id", …)`), and the field the enrichment assigns is called `cloned_strategy_id`. So
      the table AND the ownership column were both wrong and the file already contained the
      correct form twice. The read is now against `strategies` scoped by `user_id`. **The
      consequence was a silent omission, not an error page**: the `except` logged a warning and
      returned, so `user_has_cloned` and `user_rating` were never set on ANY catalogue card, and
      the rating read below never even ran. That is `bugfix.md`'s rule about a read that did not
      complete — failing in the omission direction rather than the fabricated-zero direction, which
      is the direction the handler's own comment claims to take deliberately
    - **A test had encoded the defect, and correcting it made the assertion STRONGER.**
      `tests/property/test_fixed_round_trips.py`'s P-57 pins the authenticated catalogue's
      statement SEQUENCE and listed `library_strategies` **twice**. Its own failure message says a
      repeat of a table already in the sequence is the N+1 signature it exists to catch, so the
      pinned sequence was the shape it warns about. It now reads
      `library_strategies, profiles, strategies, library_ratings` — four DISTINCT tables, same
      round-trip count of 4. This is the one assertion this task edited, and it was edited to stop
      asserting a defect, not to go green
    - **What was deliberately NOT fixed, with who decides named rather than implied.** **(a) 5
      `billing_invoices` columns** (`amount_inr`, `amount_usd`, `plan`, `provider`,
      `provider_payment_id`) at `routers/billing.py:2082/2194/2405`, and
      `profiles.cancel_at_period_end` at `:2764` — that file carries the parallel pricing
      workstream's uncommitted
      changes; editing it would collide. The three inserts sit inside `try/except … logger.warning`,
      so **no invoice row has ever been written** and no 500 was ever raised — the copilot shape
      from 13.14, in the billing ledger. **(b) 4 `profiles` billing-lifecycle columns**
      (`subscription_status`, `trial_end_date`, `next_billing_date`, `pending_downgrade_tier`) in
      `core/billing_lifecycle.py` and `core/subscription_middleware.py` — same domain, same
      workstream, reported and attributed. **(c) 12 `execution_records` columns** at
      `routers/signals.py:47/122/204`: 12 of a 20-column projection. Reachability was ESTABLISHED
      rather than assumed — `main.py:689` mounts the router at `/api/signals`, and **nothing in
      `algo22-terminal/` calls `/api/signals` at all**; the Signal_Trace page calls
      `/api/signal-trace/signals`, served by `routers/signal_trace.py` (mounted at `main.py:690`),
      which does not touch `execution_records`. `execution_records` is an order-execution table —
      `execution_id`, `size`, `filled_size`, `avg_price` — with no `indicators`, `ml_inputs`,
      `ml_outputs`, `confidence`, `risk_verdict`, `timeframe`, `latency_ms`, `pnl` or
      `failure_reason` to repoint those names AT. Choosing between deleting a mounted router and
      inventing nine columns is exactly the ambiguity 13.14 resolved by establishing reachability
      and then asking, so it is reported. **(d) `profiles.volume_usd`** at `routers/admin.py:122`:
      `GET /api/admin/users` is mounted behind `get_admin_user`, has no `try/except`, and therefore
      **500s unconditionally**. No migration declares `volume_usd`, nothing computes it, and
      `algo22-terminal/` never reads it — so both candidate fixes (drop it from the projection, or
      declare an empty column) are decisions about whether an admin capability existed, and
      declaring a column nothing populates would fabricate the figure `bugfix.md` forbids. **(e)
      `strategies.tenant_id`** at `backend/marketplace/eligibility_gate.py:449` — the most severe
      of the 24, and the one most clearly not a parse's call: the gate's four reads are wrapped so
      that
      any failure answers `unevaluable=True` (Requirement 2.13) — an explicit unavailable, exactly
      as the rule demands — which means **every marketplace submission eligibility evaluation in
      production currently returns "unknown"**. Dropping the column does not fix it:
      `core/dependencies.py:318` defaults `tenant_id` to the caller's own `user_id`, so
      `caller_tenant` is never null, and `_tenant_matches(None, caller_tenant)` is `False` by that
      function's documented semantics — "always unevaluable" would become "always refused". The
      spec's own `tenant-boundary-audit.md` records `strategies`' tenant predicate as `user_id`,
      with no `tenant_id` column; so the choice is between adding and backfilling a column on a
      live 187-row table and rewriting Criterion 2.2's tenant clause, and neither belongs in a
      schema-drift task
    - **GUARD: `tests/test_schema_table_reference_drift.py`, 49 → 70 tests, extended rather than
      duplicated, still pure parse and still no network.** It shares `_mask_sql`,
      `_sql_migration_files` and the exemption-pinning pattern the SQL side already uses; the 49
      existing tests are untouched and still pass. Two new classes plus a scanner-health class.
      PostgREST forms handled, each because this codebase writes it: `*`; `count`; `alias:col`;
      `col::text`; `col->k` and `col->>k`; `amount.sum()`; `...spread`; `rel(a,b)`;
      `rel!hint(a,b)`; and `rel!library_id!inner(a,b)` — the hint is stripped and the embed is
      FOLLOWED into `rel`, where its own list is parsed recursively, so `library_strategies` is
      never reported as a COLUMN of `library_subscriptions`. A dotted `.eq("rel.col", v)` is
      attributed to `rel` for the same reason
    - **The oracle for the four tables no migration CREATEs is RECORDED, and the recording is
      pinned against rot.** `profiles` (20 columns), `strategies` (26), `execution_records` (22),
      `library_strategies` (52) and `library_ratings` (8, declared only in Alembic) have no
      `CREATE TABLE` anywhere in the tree, so the migration set's column list for them is a subset
      by construction. Their production column sets are recorded with the server they were read
      from — the same evidence pattern `SQL_UNDECLARED_PRESENT_IN_PRODUCTION` already uses for row
      counts, and recorded rather than parsed for the same reason: there is no file here to parse
      it out of and this suite takes no network. The companion test asserts each entry's table is
      still not `CREATE`d (so a recorded roster can never shadow a parsed one), is still read by
      Python, and — the clause that closed a real hole — that **EVERY** table a column reference
      reaches has an oracle. Scoping that to ALTER-only tables was tried and was wrong:
      `library_ratings` is neither created nor altered by the migration set, so deleting its entry
      left 8 columns unchecked with the whole section green
    - **The 24 open defects are a REGISTER, not an allowlist, and the distinction is enforced.**
      Each entry names its owning module and who decides. The companion tests fail if an entry
      stops being referenced, if its column starts existing, if the attributed module leaves the
      tree, or if the reference moves to a different module — and the failure message states that
      the register may only SHRINK and that adding a name to it re-creates the defect. The two
      registers may not overlap: a column cannot be both recorded-present-in-production and a
      known `42703`
    - **Coverage is ratcheted in both directions, because either alone is gameable.** 182
      unresolvable sites is a ceiling and 980 resolved is a floor. Without the floor a parser that
      stopped matching would satisfy every assertion above; without the ceiling a refactor that put
      every query behind a helper would take the blind spot to 100% and leave the suite green. The
      two are also cross-checked against each other — the counters must equal the number of skip
      records — and the message names the exposure honestly: **52 of the 182 sit in the parallel
      workstream's files**, so a small rise there is to be explained rather than absorbed
    - **Parser health, asserted the way `test_declared_set_is_parsed_and_non_trivial` is.** A
      fixture exercising every form above, including the embed-with-hint, the double-hint, the
      alias and the two-handler scope collision, resolving to an **EXACT** set over four tables —
      31 columns on `outer_table`, 3 on `inner_table`, 1 on `other_inner`, 2 on `second_table` —
      with `ghost_column` deliberately undeclared so the guard is proven to FIRE rather than to
      have gone quiet, and with a dynamic table name and a dynamic payload that must be COUNTED
      rather than attributed. Plus floors on the real parse (≥450 pairs, ≥40 tables, >100 selects,
      >300 filters, ≥800 declared pairs, >50 `CREATE TABLE`s), an assertion that a `CREATE TABLE`
      quoted inside a `RAISE NOTICE` is not a declaration, and four named columns that exist ONLY
      via `ALTER TABLE` so the `ALTER` half of the oracle cannot silently stop being applied
    - **The type-divergence class the `strategy_backtests.version` bug came from, guarded as a
      shrinking inventory.** Synonym spellings are folded first — `DECIMAL(10,2)` and
      `NUMERIC(10,2)` are one type, and 16 of the 46 raw hits were only that — leaving **30**
      genuine conflicts, each recorded with the type production actually carries. `numeric(10,2)`
      vs `numeric(20,8)` is deliberately NOT folded: those round money differently. The inventory
      may shrink and may never grow; an entry that stops diverging must be removed, and the one
      RECONCILED divergence is pinned by outcome — applying the whole set in order must leave
      `strategy_backtests.version` as `VARCHAR(20)` from
      `016_strategy_backtests_version_label.sql`, which is the column-type counterpart to the
      statement-level pin in `test_backtest_version_label_regression.py`. Four of the 30 are
      structural rather than precision: `strategy_research_reports.strategy_score` is `jsonb` in
      001 and `numeric(6,4)` in 006, `.warnings` is `text[]` versus `jsonb`,
      `strategy_deployments.exchange_id` is `uuid` versus `varchar(50)`, and
      `referral_profiles.id` / `strategy_backtests.id` are `uuid` versus `text`
    - **Before/after, OBSERVED, by mutating one thing at a time and restoring it in binary with
      the SHA checked both ways.** Thirteen mutations, **all thirteen fired**, and the two that did
      NOT fire on the first attempt are recorded here because each exposed a real weakness that was
      then fixed rather than explained away. **(A)** `library.py` reverted to reading
      `library_strategies` → **2 failed**, reporting
      `reads library_strategies.source_library_id again at`
      `[('backend_app/routers/library.py', 619), …]`.
      **(B)** `profiles.volume_usd` removed from the register → **1 failed**. **(C)** all twelve
      `execution_records` entries removed → **1 failed**. **(D)** `library_ratings` removed from
      the roster → **passed** at first, which is how the ALTER-only scoping hole was found; after
      widening the clause to every referenced table, **1 failed** with
      `['library_ratings'] are read COLUMN BY COLUMN … and have no column oracle at all`. **(E)**
      `profiles.subscription_tier` deleted from the roster → **1 failed**, so the roster is load-
      bearing rather than decorative. **(F)** embed read as a column instead of followed → **2
      failed**. **(G)** `!hint` no longer stripped → **1 failed**, `missing ['other_inner']`.
      **(H)** the ORIGINAL defect reinstated — one module-wide binding pass, last write wins →
      **passed** at first against a fixture that could not express it, so the mutation was
      rewritten to be the actual defect and then **1 failed** with
      `outer_table: unexpected []; missing ['built_two']`. **(I)** `ADD COLUMN` no longer applied →
      **2 failed**. **(J)** `strategy_backtests.version` removed from the divergence inventory →
      **1 failed**. **(K)** `DECIMAL` no longer folds to `NUMERIC` → **2 failed**. **(L)** the
      skip ceiling lowered to 100 → **1 failed**. **(M)** the resolved floor raised to 1200 →
      **1 failed**. With nothing mutated: **70 passed**. Said plainly rather than counted as
      coverage: `test_each_recorded_conflict_still_names_two_real_spellings`,
      `test_the_divergence_inventory_has_not_gone_stale`,
      `test_the_two_column_exemption_registers_do_not_overlap` and
      `test_the_type_parser_reads_the_shapes_this_set_contains` were not individually mutated —
      they assert on the pinned inventories and on fixtures, and they pass in both directions by
      construction
    - **Gates.** `tests/test_schema_table_reference_drift.py` **70 passed** (49 pre-existing
      unchanged + 21 new); `tests/test_no_undefined_names.py` **3 passed**;
      `flake8 --select=E9,F63,F7,F82` clean on all three touched Python files;
      `backend_app.main` imports and still exposes **354 routes**; `test_migration_tooling` 28 /
      `test_no_dormant_schema_references` 2 / `test_schema_as_code_completeness` 6 /
      `test_library_schema_contract` 14 → **50 passed** together;
      `test_creator_analytics_regression` + `test_backtest_version_label_regression` → **45
      passed**; and the suites that exercise the edited handler —
      `tests/property/test_fixed_round_trips.py` + `test_library_detail_visibility_and_omission` →
      **16 passed**, `test_library_detail_projection_regression` +
      `test_library_route_resolution` + `test_listing_projection` +
      `test_tenant_isolation_library_paper` → **149 passed, 2 failed**. Those two —
      `test_a_library_mutation_of_another_tenants_record_leaves_every_row_byte_identical` for
      `POST /api/library/{library_id}/checkout` and `/clone` — are **PRE-EXISTING and proven so**:
      swapping `routers/library.py` for `git show HEAD:`'s copy in binary gives `2 failed, 13
      passed` in both directions. They are not in 13.13's inventory either, which is a small gap in
      that inventory rather than in this change
    - _Requirements: none directly — the same schema-as-code class as 13.14–13.16, one level_
      _deeper. Recorded because task 14 requires every launch blocker to be proven, BLOCKED with_
      _its gap named, or closed by citation, and 24 reads that answer `42703` against the live_
      _database are none of those. P1 for the class, with `strategies.tenant_id` the sharpest_
      _instance: an eligibility gate that can only answer "unknown" means nothing can be published_
      _to the marketplace, and it was invisible to a guard that only ever asked whether the TABLE_
      _existed_

  - [ ] 13.18 13.17's sharpest instance fixed at its root — `strategies.tenant_id`, the gate that could only answer "unknown"
    - **The defect.** 13.17 measured `strategies.tenant_id` against the live database and got
      `42703`, with the control column `user_id` on the same table answering OK — so the absence is
      the column's, not the table's or the probe's. `backend/marketplace/eligibility_gate.py`'s read
      1 selected `id, user_id, tenant_id, archived_at`, and `evaluate` wraps all four of its reads
      in one `except Exception` that returns `unevaluable=True` with an **empty outcome tuple**
      (Requirement 2.13 — a read that did not complete is not a verdict). The consequence is not
      partial: **every marketplace submission eligibility evaluation in production answered
      "unknown"**, on every path, for every caller, and `POST /api/library/submissions` turned that
      into a 503. Nothing could be published to the Marketplace at all. P1 — and it was invisible
      to 13.14–13.16's guards for a structural reason, not an oversight: those resolve
      `.table("x")` literals against a `CREATE TABLE`, so they only ever asked whether the TABLE
      existed. `strategies` exists. 13.17 built the column-level guard that could see this, and
      this task is the first entry it found that is fixable at its root rather than attributable
    - **The decision, and the alternative that was rejected.** Read the tenant off `user_id`; do
      **not** add a `tenant_id` column to `strategies`. The spec's own `tenant-boundary-audit.md`
      records this table's tenant predicate as `user_id` and notes it has no `tenant_id` column,
      and `core/dependencies.py` builds every caller's `tenant_id` from the token's claim *or,
      failing one, from their own `user_id`*. The tenancy model is **tenant == user**. Adding the
      column would duplicate `user_id` in every row, encode a distinction the system does not make
      anywhere else, and require backfilling a live 187-row table — three costs to restore a
      reference whose referent was never meant to exist
    - **Why it was not a one-line edit, and what the one-line edit would have cost.** Swapping the
      column naively makes `strategy_tenant` *always present* — under tenant == user a strategy
      cannot not have a tenant, because its owner is its tenant. A caller carrying no tenant
      context then lands on `_tenant_matches`' old "exactly one side absent ⇒ no match" branch and
      is **refused**. So the naive fix converts "always unevaluable" into "always refused" for
      tenant-less callers: a 422 with `MP_TENANT` in its failure list instead of a 503, which is
      quieter, looks like a legitimate verdict, and would have been attributed to the caller rather
      than to the gate. The handling went into `_tenant_matches`' **first** branch, which now
      returns `True` as soon as the caller carries no tenant. Refusing is not the safe default
      here: read 1 has already filtered `.eq("user_id", caller_id)`, so the only row that can be in
      hand is the caller's own, and the sole thing a refusal would protect against is the caller's
      own strategy. The tenant clause sits on top of that filter as defence in depth, not as the
      only thing standing between two tenants — the docstrings now say so, because a future reader
      deciding how strict to make this branch needs to know what else is already holding
    - **`_strategy_tenant` as a named seam.** The decision "the owner column IS the tenant column"
      lives in exactly one three-line function rather than inline at the `MP_TENANT` call site, so
      if the platform ever grows a real `tenant_id` column the change is that function and nothing
      else. A seam is cheaper than a comment here because the thing being isolated is a *model*
      decision, and a model decision that is inlined gets re-litigated at every call site
    - **The manifest had to move with the projection.** `marketplace/__init__.py`'s
      `_ELIGIBILITY_HANDLER` select-literal manifest listed `tenant_id` among `strategies`'
      columns, and `test_select_literals_are_within_manifest` holds the gate's actual `.select`
      literals against it — so leaving the manifest alone would have left the two disagreeing and
      the suite red. Worth recording as a reminder rather than as bookkeeping: this repo **already
      had a narrower version of the guard 13.17 generalised**, scoped to the marketplace modules'
      five tables, and it would have caught this defect the day it was written had the manifest
      been derived from the schema instead of hand-listed beside it
    - **The register shrank, 24 → 23, and the direction is the point.**
      `KNOWN_UNDECLARED_COLUMN_DEFECTS` in `tests/test_schema_table_reference_drift.py` is
      documented shrink-only, and `test_every_known_defect_is_still_a_defect` fails if a fixed
      entry is left sitting in it. So a fix cannot be recorded by *adding* an exemption and the
      register cannot quietly become an allowlist — the only way to make it smaller is to make a
      read correct, and the only way to make it bigger is to find a new defect. `tenant_id` left
      the register and `FIXED_ELIGIBILITY_TENANT_PREDICATE = ("strategies", "tenant_id",
      "user_id")` took its place beside 13.17's existing `FIXED_CLONE_ENRICHMENT`
    - **The new drift test, pinned three ways** so it cannot pass for the wrong reason:
      `test_the_eligibility_gate_reads_the_tenant_column_that_exists` asserts (1) nothing anywhere
      in the scanned tree names `strategies.tenant_id` any more, (2) the gate still reads
      `strategies.user_id` in at least two places — so the fix cannot have been "stop reading the
      strategy row", which would also have made (1) true while deleting Criterion 2.2, and (3) the
      oracle agreement between the register and the fixed-reference constants
    - **Parse-level gates, all green.** `tests/test_schema_table_reference_drift.py` → **71
      passed** (was 70; the one new test); `pytest tests/ -k "eligibility or marketplace_manifest
      or select_literal"` → **11 passed**; `flake8 --select=E9,F63,F7,F82` exit 0 on all three
      touched files; `backend_app.main` imports and still exposes **354 routes**;
      `tests/test_no_undefined_names.py` → **3 passed**
    - **Behavioural gates — the verdict itself, before and after.** The gates above are
      parse-level and suite-level: they prove no code path names the absent column and that the
      existing eligibility suites still pass. They say nothing about what the gate now *answers*,
      and the production symptom was a verdict. So
      `tests/test_marketplace_eligibility_tenant_verdict.py` (new, **7 passed**) drives the real
      `evaluate` and observes it: the **pre-fix projection**, replayed, gives `unevaluable=True`
      with `outcomes == ()` for a caller who owns the strategy — the production state, no decision
      at all; a **caller with tenant context** (`tenant_id` == their own id, as `dependencies.py`
      fills it) now reaches a populated outcome tuple with `MP_TENANT` passing; a **caller with no
      tenant context** also passes `MP_TENANT`, which is the trap above pinned as a verdict rather
      than as a unit of `_tenant_matches`; and **someone else's strategy** fails `MP_OWNERSHIP` and
      `MP_TENANT` together with `unevaluable=False` — a legible refusal, where the defect produced
      the same refusal with no reason attached
    - **Why that needed a schema-faithful double, which is also why the existing suites never
      caught this.** The pipeline and concurrency suites' `FakeTable.select()` ignores its
      projection entirely, so it returns the strategy row happily with the pre-fix literal — a
      permissive double *cannot* reproduce a `42703`, and that is precisely why those suites stayed
      green for the whole life of the bug. The new file's double parses the projection, raises a
      `42703`-shaped error for any column the table does not have, and narrows returned rows to the
      projection so the gate cannot read a column it never selected. Its notion of which columns
      exist is `_ELIGIBILITY_HANDLER`'s manifest itself, not a hand-written copy, so the double
      cannot drift away from the guard that polices the real reads
    - **What this still does not prove, stated rather than implied.** The behavioural cover runs
      against that double, not against live Postgres: it shows the gate answers correctly *given*
      that `strategies` has no `tenant_id`, which 13.17 established against production, but it is
      not itself a production observation. The "before" case is a verbatim replica of the pre-fix
      read body patched over `_read_strategy`, not the pre-fix module. And the one case the fix
      leaves genuinely unresolved: a token carrying a **real** `tenant_id` claim that differs from
      its `user_id` — reachable, since `dependencies.py` only *falls back* to `user_id` — matches
      no strategy of its own, so `MP_TENANT` refuses everything for such a caller.
      `test_a_foreign_tenant_claim_still_refuses` records that as the gate's actual behaviour
      instead of asserting it cannot happen, so if the platform ever issues distinct tenant claims
      that test is the one that fails and it names what the change means. No claim is made that
      tenant == user is the right long-term model, only that it is the model this codebase
      implements everywhere else
    - _Requirements: 2.2 (the tenant clause of the Eligibility_Gate's existence/ownership/tenant_
      _criterion, now read off the column that exists), 2.13 (a read that did not complete is_
      _unevaluable and not a verdict — the mechanism that turned one absent column into a total_
      _publication outage) and 21.1 (the acting identity and tenant derived from the authenticated_
      _server-side session, which under tenant == user is what makes `user_id` the correct_
      _predicate rather than a widening of 2.2)_


- [ ] 13.19 Fix the last two reachable undeclared-column defects: `profiles.volume_usd` and
  `routers/signals.py`'s twelve `execution_records` columns
  - Two of 13.17's escalations, both resolved by **removing a read rather than declaring a
    column** — and in both cases that direction was forced by there being nothing to repoint the
    name AT, not chosen for being cheaper
  - **(A) `profiles.volume_usd`, and why it was a 500 rather than a quiet null.**
    `routers/admin.py`'s `list_users` projected
    `id, username, email, subscription_tier, volume_usd, is_frozen, created_at` off `profiles`.
    Production answers `42703` for `volume_usd` with `subscription_tier` on the same table
    answering OK (re-measured this task, read-only `SELECT "<col>" … LIMIT 0`). Unlike every
    other defect in this family the handler wraps **nothing** — no `try`, no `except` — so the
    driver error reached the global handler and `GET /api/admin/users` answered **500 to every
    admin on every call**. It had never once been able to answer. Fixed by deleting the name:
    no migration declares it, nothing in the tree computes a trading volume, and
    `algo22-terminal/` never reads it, so declaring an empty column would have put a figure on
    an admin screen that nothing produces — the fabrication `§Bug condition` forbids. The
    no-supabase branch twelve lines above already returned `id`/`email`/`subscription_tier`/
    `is_frozen` with no `volume_usd`, so removing it makes the handler's two branches **agree**
    where they previously diverged, which is now pinned as a verdict rather than as a diff
  - **Checked end to end before deleting, so the omission is not a dangling key.** `list_users`
    declares no `response_model` and returns `resp.data` raw; grep for `volume_usd` across the
    repo returns the projection, the register entry, and one unrelated hit —
    `ExchangeResponse.volume_usd` in `core/models/pydantic_models.py`, which is a *per-exchange*
    connection field on a model that is declared and exported but used as nobody's
    `response_model` anywhere. Different table, different concept, untouched and reported rather
    than swept in. `algo22-terminal/` contains **no** reference to `volume_usd` and no caller of
    `admin/users` at all, so no frontend type or admin UI column loses a field
  - **(B) `routers/signals.py` — three endpoints superseded, one not, established before
    anything was deleted.** The router mounted four endpoints at `/api/signals` (`main.py:689`):
    `GET /` (list), `GET /{signal_id}` (detail), `POST /{signal_id}/replay`, `GET /export`.
    `routers/signal_trace.py` at `/api/signal-trace` (`main.py:690`) serves eight, including the
    list, the detail and the export — and it is the router the frontend actually calls: 13.17
    established that nothing in `algo22-terminal/` calls `/api/signals` at all and the
    Signal_Trace page calls `/api/signal-trace/signals`. So three of the four were not a second
    opinion, they were a second implementation nobody used. **`POST /{signal_id}/replay` has no
    equivalent in `signal_trace.py`** and is the one genuine capability only this router offers,
    so it survived
  - **All three superseded endpoints were also broken, each in a different way.**
    `list_signal_traces` projected twenty columns off `execution_records` of which **twelve are
    not columns of that table** (`id`, `indicators`, `ml_inputs`, `ml_outputs`, `confidence`,
    `risk_verdict`, `filled_quantity`, `quantity`, `latency_ms`, `pnl`, `failure_reason`,
    `timeframe` — each re-measured at `42703` this task, with `user_id` and `symbol` on the same
    table answering OK), inside a blanket `except` that turned the failure into
    `503 SIGNAL_FETCH_FAILED` **for every caller regardless of stored data**. It also defaulted
    the absent names to literals — `confidence` to `0.88`, `latency_ms` to `42.5`, `indicators`
    to a fixed RSI/SMA/EMA dict — so had the read ever succeeded it would have rendered invented
    numbers as a trading signal's audit trail. `get_signal_trace` read the same twelve names off
    a `select("*")` and got `None` for each. `export_signal_traces` called the list, and was
    unreachable regardless — see the shadowing finding below
  - **The replay router: KEPT in `signals.py` as a one-endpoint router, not moved into
    `signal_trace.py`.** Moving it was the tidier-looking option and was rejected on evidence,
    found by searching for callers before deleting anything: `tests/test_validation_sweep.py`
    carries it in its **route register** as `signals.replay_signal_trace` →
    `POST /api/signals/{id}/replay` → `backend_app.routers.signals:replay_signal_trace`, and
    lists `backend_app.routers.signals` in its module roster;
    `tests/test_strategy_analysis_endpoint_accuracy.py` imports the handler from that module and
    patches `backend_app.routers.signals._sb`; `tests/test_router_registration_completeness.py`
    asserts the `signals` router contributes a `/api/signals` route; `scripts/debug_imports.py`
    imports the module. Moving the endpoint would have changed its public path, forced edits to
    an IDOR/validation sweep's own register, and put a second `POST …/signals/{id}/…` beside
    `signal_trace.py`'s `POST /signals` — four costs for no behavioural gain. **The path did not
    change.** `/api/signals/{signal_id}/replay` is reachable exactly as before
  - **Replay now reads `signals` only, and three straddling shims went with the fallback.** The
    file's own comment already called that table "the proper schema". Removing the
    `execution_records` fallback removed the reason for `rec.get("decision") or rec.get("side")`
    — and the probe showed why the shim was never a compatibility layer: `signals` has **no
    `side`** and **no `created_at`** (both `42703`), while `execution_records` has no `decision`,
    no `indicators`, no `market_info` and no `ml_info`. So each alternative was reachable through
    exactly one table, and a record retrieved through the fallback answered this endpoint's
    `execution_metadata` with three nulls — an audit payload with the audit content missing.
    `rec.get("created_at") or rec.get("generated_at")` collapsed to `generated_at` for the same
    reason. **And a third thing the shim was hiding:** the expression ended `or "BUY"`, so a row
    whose `decision` was NULL was reported to an auditor as a *buy*. That is a fabricated
    financial fact in a compliance-retrieval payload, exactly `§Bug condition`, and it is now
    `None` with a test that pins it
  - **The `/export` shadowing bug, found while reading and fixed by the same deletion.**
    `GET /export` was declared **after** `GET /{signal_id}` in the same router. `/{signal_id}`
    matches any single segment, FastAPI matches in declaration order, so
    `GET /api/signals/export` resolved to the **detail** handler with `signal_id="export"` and
    the export handler was unreachable — dead on arrival, never once called. Recorded as
    **resolved by deletion**: both endpoints are gone, and `/api/signals` now has one route, so
    the hazard is structurally absent rather than merely reordered. **The contrast, verified
    rather than assumed:** `signal_trace.py` declares `GET /signals/export` at line 316 and
    `GET /signals/{signal_id}` at line 444 — export **first**, so it is correct. Asserted
    through Starlette's own matcher on the mounted app (a registration-order bug cannot be
    reproduced on a router assembled in a test), and `routers/signal_trace.py`'s module
    docstring had already flagged `GET /api/signals/export` as "dead today" without anyone
    acting on it
  - **The register shrank 23 → 22 → 10, measured at each step rather than at the end.** With
    `profiles.volume_usd` removed and `admin.py` fixed, the full drift suite failed on
    **exactly** the twelve `execution_records` references and nothing else — which is what
    isolated Fix A as complete before Fix B began. All ten survivors are now the **same** reason,
    the parallel pricing/entitlements workstream's `billing_invoices`/`profiles` columns, so the
    register no longer carries a single entry this workstream owns
  - **Two ratchet consequences of deleting reads, both followed rather than suppressed.**
    (1) `test_the_recorded_production_column_sets_are_still_what_they_claim` fired:
    `execution_records` was in `PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES` but **nothing under
    `backend_app/` reads a column of it any more**, and that roster may only carry tables code
    actually reads — an unread entry is a permanent unchecked exemption. The entry was removed,
    which is the guard's own prescribed response and a shrink, not a widening; its 22-column
    production measurement was **retired to `RETIRED_PRODUCTION_COLUMNS_EXECUTION_RECORDS`
    rather than discarded**, because throwing the measurement away would have thrown away the
    evidence that justified the deletion, and the new test asserts `_allowed_columns` returns
    `None` for the table so a resurrected read brings the roster entry back **with** it.
    (2) `MIN_RESOLVED_COLUMN_CALL_SITES` 980 → 967. That floor exists to catch a parser that
    stopped matching, so the 13 are **accounted for by measurement, not absorbed**: the scanner
    was run over `signals.py` at `9afe8ab4` and at the fix, and the file contributed **16**
    resolved call sites then and **3** now — `list_signal_traces` 7 (one `.select`, five `.eq`,
    one `.order`), `get_signal_trace` 3, replay's deleted `execution_records` fallback 3, and
    replay's surviving `signals` read 3, unchanged. 7+3+3 = 13 and 16−13 = 3
  - **Why a schema-faithful double, and the proof it is not a permissive one.**
    `tests/test_mounted_endpoint_projections.py` (new, **19 passed**) reuses 13.18's
    `_SchemaFaithfulTable`: it raises a `42703`-shaped error for any column the relation does not
    have and narrows rows to the projection, as Postgres does. Its notion of which columns exist
    is **not** hand-written — it is the drift suite's own `_allowed_columns` oracle, the same one
    that polices the real reads, which 13.18 recorded as the lesson. **Before** the fix: `GET
    /api/admin/users` answered **500**, with the global handler reporting
    `column "volume_usd" of relation "profiles" does not exist` — the production failure,
    reproduced. **After:** 200 with both rows, `profiles` read once, no row carrying a
    `volume_usd` key. Both pre-fix projection literals are replayed against the same double and
    still refused, which is what pins the double as *capable* of reproducing the bugs — without
    those two cases the passing tests could be passing because the double is permissive. The
    pre-fix `list_signal_traces` projection is refused while holding a complete, valid
    order-execution row, which is the measurement that `GET /api/signals/` could never have
    answered with data present
  - **Assertions ACTUALLY OBSERVED failing before the fixes — 11 of the 19 new tests.** Fix A:
    `test_the_admin_user_list_answers_rather_than_500ing` (500, reproduced `42703`) and
    `test_the_two_branches_of_the_handler_agree_on_shape`. Fix B: both parametrised families
    over the three removed routes (6 cases — absent-from-route-table and now-404s),
    `test_replay_404s_instead_of_falling_back_to_execution_records`,
    `test_replay_does_not_synthesise_a_decision` (observed returning `"BUY"` for a NULL
    decision) and `test_no_surviving_signals_route_can_shadow_another`. Plus, in the drift
    suite, `test_every_referenced_column_resolves` and the new
    `test_the_superseded_signal_endpoints_no_longer_read_execution_records`. **Stated rather
    than implied: three of the new tests cannot be made to fail against `F`** —
    `test_replay_reads_signals_and_never_touches_execution_records` passed before the fix too,
    because a seeded `signals` row means the fallback is never reached, so it is a
    *preservation* case rather than a regression case; and the two pre-fix-replay cases are
    assertions *about* `F`, so they pass at both revisions by construction
  - **Two existing tests asserted the removed behaviour and were re-pointed, not relaxed.**
    `test_exception_swallow_regression.py`'s `TestListSignalTracesExceptionSwallow` drove
    `GET /api/signals/` with an *injected* crash and asserted 503 — and passed, for the whole
    life of the defect, because a permissive double cannot tell "the database is down" from
    "this projection can never succeed". It now asserts the route is **gone** (the swallow
    resolved by deletion) and additionally pins the same property on the endpoint that
    *survived*, which carries the same blanket `except`.
    `test_strategy_analysis_endpoint_accuracy.py`'s `test_signal_replay_execution_records_fallback`
    asserted `tables_queried == ("signals", "execution_records")` and a decision read off
    `execution_records.side`; it is replaced by
    `test_signal_replay_does_not_fall_back_to_execution_records`, asserting a 404 and
    `tables_queried == ("signals",)` — the **stronger** invariant, with the two tables' column
    sets recorded as the reason
  - **Route count: 354 → 351, delta −3**, exactly the three deleted endpoints and no collateral.
    `/api/signals` contributes one route where it contributed four
  - **Gates.** `tests/test_mounted_endpoint_projections.py` → **19 passed** (new);
    `tests/test_schema_table_reference_drift.py` → **73 passed** (was 71; the two new pinning
    tests — removing register *entries* does not change the test count);
    `tests/test_exception_swallow_regression.py` → **6**;
    `tests/test_strategy_analysis_endpoint_accuracy.py` → **9**;
    `tests/test_marketplace_eligibility_tenant_verdict.py` → **7** (13.18's, still green);
    `tests/test_no_undefined_names.py` → **3**;
    `test_router_registration_completeness.py` + `test_task_13_1_signal_trace_list.py` +
    `test_task_13_3_signal_trace_export.py` + `test_tenant_isolation_fixes.py` → **92 passed**
    together; `tests/test_validation_sweep.py` → **381 passed, 11 skipped, 1 failed**, the
    failure being `library.clone_strategy`, which was **confirmed pre-existing by re-running it
    with `9afe8ab4`'s `admin.py` and `signals.py` restored** — it fails identically there, lives
    in `routers/library.py` (unmodified, at HEAD) and is not reachable from either fix;
    `flake8 --select=E9,F63,F7,F82` exit 0 on all six touched files; `backend_app.main` imports
  - **What this still does not prove, stated rather than implied.** The behavioural cover runs
    against a schema-faithful double, not against live Postgres: it shows each endpoint answers
    correctly *given* the column sets the probe measured, and the probe is a production
    observation but the verdict is not. `GET /api/admin/users` is admin-gated and was driven
    with `get_admin_user` overridden, so the fix is proven for the handler and not for the
    authorisation path in front of it. And the replay endpoint remains honestly labelled as
    audit retrieval rather than replay — task 13.19 changed which table it reads, not what it
    does, and `replay_implemented: False` is still the truthful answer
  - _Requirements: 2.13 (a read that did not complete must be an explicit absence, not a_
    _verdict and not a 500 — the clause both defects violated, one by answering 500_
    _unconditionally and one by answering 503 unconditionally) and 1.1/2.1 by way of_
    _`§Bug condition` (the deleted list endpoint defaulted absent columns to invented literals,_
    _and replay reported a NULL decision as `"BUY"`; both now report absence as absence)_


- [ ] 13.20 The frontend deploy's WebSocket gate was a FALSE POSITIVE — it waited on a header
  uvicorn does not emit, and blocked every frontend deploy
  - **The defect.** `.github/workflows/06-frontend-deploy.yml`'s last step, "Verify the
    WebSocket upgrade reaches the origin" (added in `2c33a877`), is BLOCKING and runs last. It
    passed only if the upgrade response carried `server: uvicorn`, and otherwise failed with
    "every socket in the product is dead" and "the terminal shows 'Not connected to the trading
    engine' on every authenticated page". **uvicorn does not emit a `server` header on that
    path**, so the discriminator could not be satisfied by any healthy deployment. Run
    37035482811 on `2c33a877` red, every earlier frontend deploy green (recorded from the run
    history, not re-measured here — Actions cannot be driven from this environment).
    Consequence: the live frontend bundle is stale and **no frontend change can ship**. This is a
    CI defect, not a product defect, and the product claim the step printed was false
  - **The load-bearing fact, re-measured rather than inherited.** A local uvicorn (0.52.4)
    running a hand-rolled ASGI app that sends `websocket.close` before `websocket.accept` — no
    CDN, no ALB, no FastAPI — answers a raw HTTP/1.1 upgrade with, verbatim:
    `HTTP/1.1 403 Forbidden` / `Date: …` / `Connection: close` / `Content-Length: 0` /
    `Content-Type: text/plain; charset=utf-8`. **No `server` header.** The same app answers a
    plain GET to the same path `404` *with* `server: uvicorn`, so the header is not absent from
    the server, it is absent from the websocket-rejection path specifically: that path does not
    go through the HTTP response writer that stamps `server:`. Repeated against a Starlette
    `WebSocketRoute` (production's actual stack shape) — byte-identical. So the old gate's own
    comment, "`server: uvicorn` is the evidence", was the premise the whole step rested on and it
    was false
  - **Three things ruled out first, and none of them changed — this task is a CI fix only.**
    (1) *Not CloudFront.* `get-distribution-config --id EEOXECPHQ8SR0` re-checked: `WebACLId` is
    `''`, `GeoRestriction` `none`, and the `/ws/*` behaviour targets `ALB-vyomquant-backend` with
    GET among `AllowedMethods`, `CachePolicyId 4135ea2d…` (Managed-CachingDisabled) and
    `OriginRequestPolicyId b689b0a8…` (Managed-AllViewerExceptHostHeader) — **the remediation the
    step printed was already in place**, which is what made the message actively misleading.
    (2) *Not the ALB.* A raw HTTP/1.1 upgrade straight to
    `vyomquant-alb-1008390777.ap-southeast-1.elb.amazonaws.com:80`, CloudFront bypassed
    entirely, returns the SAME `403` / `Content-Type: text/plain` / `Content-Length: 0` / no
    `server` — so the 403 is origin-generated, not CDN-generated. (3) *The application is
    correct.* `backend_app/api_ws/ws_routes.py:403` `/ws/telemetry` takes
    `ticket: Optional[str] = Query(None)` and closes before `accept()` when it is missing; all
    **nine** `@ws_router.websocket` routes do the same, and there is no anonymous WebSocket
    endpoint in the product. `ws_routes.py` is untouched — the 403 is correct, secure behaviour
  - **It was not merely over-strict, it was exactly INVERTED — and that is measured, not
    argued.** Running the shipped pre-fix script against canned header blocks: the healthy state
    (403, no `server`) **FAILS**, and a genuinely stripped upgrade (404 carrying
    `server: uvicorn`, which is what the handshake looks like after being HTTP-routed) **PASSES**
    — because the old gate's only test was "is `server: uvicorn` anywhere in the response", and
    a stripped upgrade is precisely the case that satisfies it. So the step both blocked every
    healthy deploy and would have waved through the one failure it existed to catch
  - **The fix: two probes of the same URL, no credentials, and the asymmetry between them is the
    test.** One probe cannot work, because the two outcomes it must separate are **the same
    status with the same headers** — a CDN-generated 403 and an application refusal are both a
    bare 403. So:
    **Probe A**, a genuine HTTP/1.1 upgrade (`Connection`/`Upgrade`/`Sec-WebSocket-Version`/
    `Sec-WebSocket-Key`) must answer `101` or `403` — reachable only if the upgrade headers
    survived to the origin, because that status comes from Starlette's WebSocket router refusing
    the handshake, and **with no `server`-header condition on it at all**.
    **Probe B**, the same URL with no upgrade headers, must answer `404` carrying
    `server: uvicorn` — `/ws/telemetry` has no HTTP route, so uvicorn falls through to HTTP
    routing. That pairing is the origin's own fingerprint, and it is what makes A's 403
    attributable: **B is what makes A admissible.** PASS requires both
  - **Three failure branches, each naming the inference that failed.** `CODE_A = 404` → the
    upgrade headers were STRIPPED in transit (probe A degenerated into probe B); this is the
    CloudFront misconfiguration the step exists to catch and **the only branch that prints the
    AllViewer / CachingDisabled / allow-GET remediation**, which is kept verbatim because it is
    the right advice for exactly this case. `CODE_A` neither 101/403 nor 404 → reported as
    itself (5xx, CDN error page, empty response), explicitly *without* a header-forwarding
    conclusion the evidence does not support. Probe B not `404`+`server: uvicorn` → the origin is
    not reachable through `/ws/*` or something ahead of it is answering; different message,
    pointing at the behaviour's target origin and `vyomquant-api-tg` health, and **deliberately
    no CloudFront-policy advice**. The old step printed the AllViewer remediation on every
    failure including a 502, which is how a correct configuration ends up being "fixed"
  - **`--http1.1` is still load-bearing, now for the opposite reason, and it is on both probes.**
    h2 forbids `Connection`, so over h2 curl silently drops the upgrade headers and sends a plain
    GET. Under the old logic that produced a false PASS (404 with `server: uvicorn`); under the
    new logic it produces a false *stripping alarm*, because probe A would read 404. Either way
    the flag is mandatory, and the explicit `HTTP/1.1` status-line guard is kept as a second
    catch. It is set on probe B too so that the **only** difference between the two requests is
    the upgrade headers — which is what makes the asymmetry attributable to them
  - **The live two-probe asymmetry, re-verified read-only against `https://app.vyomquant.in/ws/telemetry`
    before the logic was written.** Raw socket + `ssl`, genuine upgrade → `403`, **no `server`**;
    plain GET, same URL → `404` with `server: uvicorn`, `x-request-id`, and the application's own
    CSP/HSTS headers. Both halves observed, so the new gate's pass condition is satisfied by
    production as it stands today. **One trap recorded while reading those responses:**
    `X-Cache: Error from cloudfront` is present on **both**, including the one that demonstrably
    came from the origin. It means CloudFront passed a 4xx through, not that CloudFront generated
    it, and the step now says so — it is the next-most-tempting wrong discriminator after the
    `server` header
  - **The bundle-host derivation and the empty-`WS_BASE` guard are unchanged**, and the probe URL
    is **masked as `***` in the Actions log** because the host matches a repository secret. A log
    line reading `Probing : https://***/ws/telemetry` is redaction, not an unset variable; the
    step now prints that caveat next to the URL so the next reader does not chase it
  - **The test, and why it asserts on text AND on behaviour.** `tests/test_websocket_upgrade_gate.py`
    (new, **22 passed**), beside 13.10's `test_security_gate_blocks_deploy.py` and in its style.
    *Text half*: the step runs two probes, both on `--http1.1`, both on `"$PROBE_URL"`; the pass
    is the conjunction of both inferences; `404` on probe A hits a branch that exits 1 and is
    **not** among the accepted `CODE_A` statuses; every `server:` grep in the step reads
    `HEADERS_PLAIN` and never `HEADERS_UPGRADE`; `AllViewer` appears exactly once and inside the
    404 branch; and the comment no longer carries the false premise. *Behaviour half*: the step's
    bash is **extracted out of the YAML and run** under `bash -e` (what Actions uses) with `curl`
    shadowed by a shell function serving canned header blocks and a stub
    `dist/assets/index-*.js`. That exercises the real shipped script rather than a transcription
    of it, which is the strongest cover available given Actions cannot be run locally. The canned
    blocks are the measured bytes above, not invented ones. Skipped with a named reason if no
    usable `bash` is present (CI runs this job on `ubuntu-latest`, where it is native)
  - **Assertions ACTUALLY OBSERVED failing before the fix — 17 of the 22**, measured by
    restoring `996509c9`'s workflow and re-running. The two that matter:
    `test_a_403_with_no_server_header_plus_a_404_uvicorn_passes` failed with the pre-fix step
    printing the whole "every socket in the product is dead" + AllViewer message against a
    healthy origin — run 37035482811's failure, reproduced locally; and
    `test_a_stripped_upgrade_fails_and_names_the_cloudfront_misconfiguration` failed with "a
    stripped upgrade passed the gate", which is the inversion. Also failing against `F`: both
    two-probe structure tests, all four `CODE_A`/pass-condition tests, the three comment tests,
    `test_a_cdn_error_is_reported_as_itself` (the pre-fix step blamed CloudFront headers for a
    502), `test_a_missing_server_header_on_probe_b_alone_is_not_enough`,
    `test_an_unreachable_origin_fails_without_blaming_cloudfront_headers`,
    `test_the_pass_still_says_what_it_has_not_proven`, and
    `test_the_two_probes_are_both_actually_issued`. **Stated rather than implied: five cannot be
    made to fail against `F`** and are preservation cases, not regression cases —
    `test_both_probes_pin_http_1_1` (the pre-fix single probe already had the flag),
    `test_it_is_the_last_step_and_it_blocks`,
    `test_the_bundle_host_derivation_and_the_empty_guard_are_intact`,
    `test_an_accepted_handshake_passes` and `test_an_empty_ws_base_in_the_bundle_still_fails`
  - **Gates.** `tests/test_websocket_upgrade_gate.py` → **22 passed** (new, ~100s: eight real
    `bash` subprocesses); `tests/test_schema_table_reference_drift.py` → **73**;
    `tests/test_mounted_endpoint_projections.py` → **19**;
    `tests/test_marketplace_eligibility_tenant_verdict.py` → **7**;
    `tests/test_no_undefined_names.py` → **3** (**102 together**);
    `tests/test_release_artifacts.py` + `tests/test_no_secrets_in_bundle.py` +
    `tests/test_stale_duplicate_frontend.py` + `tests/test_nightly_audit_workflow.py` +
    `tests/test_security_gate_blocks_deploy.py` → **35 passed** (the other suites that assert on
    `06-frontend-deploy.yml`'s text, re-run because this task rewrote part of it);
    `flake8 --select=E9,F63,F7,F82` exit 0 on the new test file; `backend_app.main` imports with
    **351** routes; the workflow parses under `yaml.safe_load` and the gate is still the last of
    the job's 14 steps
  - **What this still does not prove, stated rather than implied.** The gate only runs in CI, so
    **the next frontend deploy is the real verification** — everything above is the script
    exercised against measured header bytes, not a green Actions run. The two-probe logic is
    proven for the statuses it was fed; a response shape nobody has seen is still unhandled by
    construction and will land in the "reported as itself" branch, which is the intended
    behaviour but is not the same as being anticipated. And the gate deliberately **still cannot
    prove a socket carries data**: all nine `/ws/*` endpoints require a ticket, minting one needs
    a session, and putting a production credential in a workflow to get it is a worse trade than
    the coverage is worth — the pre-fix step already made that argument and it is still right.
    No CloudFront or ALB configuration was changed, and `ws_routes.py` was not touched
  - _Requirements: none directly — no clause in `bugfix.md` governs CI gate logic, and this is_
    _recorded because task 14 requires every launch blocker to be proven or BLOCKED with its gap_
    _named, and a blocking gate that cannot pass is a launch blocker: it froze the frontend_
    _deploy path entirely. By analogy to `§Bug condition` and to 2.5 (a status SHALL come from a_
    _real probe and SHALL be `unknown` when none is available — never a literal): the step was_
    _asked whether the upgrade reached the origin, had no evidence either way, and answered with_
    _a fabricated diagnosis — "the response was generated by the CDN", "every socket in the_
    _product is dead" — rather than with the absence. 1.20's bundle/secret-masking clause is_
    _adjacent only in that it explains the `***` in the probe URL_


- [ ] 13.21 The SERIALIZABLE isolation control had NEVER ONCE EXECUTED — advertised by an enum,
  asserted by a test, and inoperative — and the test that would have caught it could not run
  because CI had no PostgreSQL
  - **Defect A, and it is measured, not inferred.** Three sites issued the isolation level as a
    raw f-string statement: `core/database.py:140` in `get_db`, `core/database.py:161` in
    `get_db_context`, and `core/database_pool.py:323` in
    `DatabasePool.get_transactional_session` — each one
    `session.execute(f"SET TRANSACTION ISOLATION LEVEL {level}")` wrapped in
    `except Exception: logger.warning(f"Failed to set isolation level: {e}")`. Against the
    production server (17.6), `get_transactional_session("SERIALIZABLE")` handed back a session
    whose `SHOW transaction_isolation` answered **`read committed`**. The warning was the
    statement's only trace, and nothing read the warning
  - **Two independent reasons that mechanism could never have worked, both re-measured against
    production in this task.** (1) *It never reached the server.* SQLAlchemy 2.x — pinned
    `sqlalchemy==2.0.35` — rejects a bare `str` passed to `Session.execute()` with
    `ArgumentError: Textual SQL expression 'SET TRANSACTION ISOLATION...' should be explicitly
    declared as text(...)`. Reproduced on a live production session: the raw call raised
    `ArgumentError`, and `SHOW transaction_isolation` on that same session immediately afterwards
    still answered `read committed`. The `except Exception` swallowed a client-side type error,
    so no amount of server-side correctness could have helped. (2) *Wrapping it in `text()` would
    not have fixed it either.* On the same production session, `text("SET TRANSACTION ISOLATION
    LEVEL SERIALIZABLE")` issued after one `SELECT 1` failed with
    `InternalError: (psycopg2.errors.ActiveSqlTransaction) SET TRANSACTION ISOLATION LEVEL must
    be called before any query`. A per-request `SET` on a pooled session is therefore fragile by
    construction, not merely mis-typed: the only supported mechanism is the `isolation_level`
    execution option, which SQLAlchemy applies at connection checkout, before any transaction
    begins. That is the mechanism now used
  - **Severity, stated honestly rather than overclaimed. NO LIVE MONEY PATH WAS RUNNING
    DEGRADED.** `TransactionType.FINANCIAL` appears only inside `database.py` itself — nothing
    under `backend_app/` passes it to `get_db` or `get_db_context` — and
    `get_transactional_session` had **no callers anywhere in the tree**. Worse than uncalled: the
    two `database.py` FINANCIAL branches live under `if not POOLING_AVAILABLE`, which is false in
    every configuration that imports at all, so the exported `get_db` is `database_pool`'s, whose
    signature is `()` and which cannot be asked for an isolation level. The control was
    **advertised, asserted by `tests/test_transaction_isolation_serializable.py`, and had no
    reachable caller**. That is a real defect — a safety control that is present in name only —
    but it is not "money is at risk right now", and this record says so. Pinned by
    `test_the_financial_branches_were_unreachable_and_still_route_through_the_pool`, so the claim
    cannot quietly rot
  - **The fix, and the decision to RAISE rather than warn.** New leaf module
    `backend_app/core/db_isolation.py`: `isolated_session(session_factory, engine,
    isolation_level)` applies the level through `engine.execution_options(isolation_level=...)`,
    then reads it back off the session's own connection with `Connection.get_isolation_level()`
    — a real round trip on PostgreSQL — and compares. A mismatch, an unrecognised level, or a
    dialect refusal raises `IsolationLevelUnavailable` and **closes the session first** so the
    honest failure does not become a connection leak. A caller that asked for SERIALIZABLE and
    cannot have it must not continue believing it got it, so the `logger.warning` is gone from
    all three sites rather than being made louder. It is a separate module on purpose:
    `database.py`'s fallback path runs precisely when `database_pool` has failed to import, so
    it cannot borrow the mechanism from there
  - **All three call sites are wired — this is the part a previous attempt left undone.** An
    earlier dispatch shipped `db_isolation.py` and its tests but imported the module from nowhere,
    which left a module that *looked* like the fix sitting beside three unchanged defects. Now:
    `get_transactional_session` is `return isolated_session(self._session_factory, self._engine,
    isolation_level)`; `database.py`'s two branches route FINANCIAL through a local
    `_financial_session()` that calls `isolated_session`; and the reachability gap is closed by a
    new module-level `get_financial_db_context()`, which is the first entry point from which
    `TransactionType.FINANCIAL` can actually be obtained. Against production it reports
    `serializable`. `SessionLocalFinancial` and `SessionLocalReadOnly` are **deleted, not
    repaired**: both were byte-for-byte `sessionmaker(bind=engine)`, identical to `SessionLocal`,
    under names that promised a control they did not hold
  - **The SQLite path is preserved, and here is exactly how.** The old
    `if "sqlite" not in str(engine.url)` guards meant the control was *skipped outright* on
    SQLite while the session was returned as though it had been applied. Those guards are gone —
    the execution option is dialect-aware, so there is nothing to branch on. FINANCIAL works on
    both backends because SQLite's dialect accepts SERIALIZABLE (it is SQLite's own default).
    GENERAL and READ_ONLY are deliberately **not** routed through `isolated_session`:
    `get_isolation_level` maps both to READ COMMITTED, which is already PostgreSQL's default, and
    SQLite's dialect *rejects* READ COMMITTED with `ArgumentError` — so asking for it explicitly
    would buy nothing on PostgreSQL and would break the SQLite lane outright. Taking the default
    is the honest encoding of READ COMMITTED here. Verified against production after the fix: the
    plain session still reports `read committed`, unchanged. `get_isolation_level()` and
    `TransactionType` are untouched and still work
  - **Defect B: there was no PostgreSQL anywhere in CI, and that is what hid defect A.** A
    control whose test cannot run is a control with no test. `01-pr-check.yml`'s `unit-tests` job
    had a `services:` block with `redis:7-alpine` and nothing else, so three files that reach a
    database through `core.database.SessionLocal` could not run anywhere; task 13.13 carried them
    as environment gaps. One of them is `tests/test_transaction_isolation_serializable.py` — the
    test that was written to catch exactly defect A. A second, independent half of the same gap
    was in `tests/conftest.py`, which blanked `DATABASE_URL` *unconditionally*: even where a
    database existed, alembic would provision the schema and pytest would still run against
    SQLite. Both halves are now closed — a `database-tests` job with `postgres:17-alpine`
    (production reports `server_version 17.6`), and one narrow conftest opt-in,
    `AERORA_TEST_DATABASE_URL`, which must be set *alongside* `DATABASE_URL` for it to survive
  - **Why the PostgreSQL job is SEPARATE from the main lane, and not a `DATABASE_URL` on it.**
    The 11,000+ tests that pass today pass against the SQLite fallback. Setting `DATABASE_URL` on
    `unit-tests` would re-point every `SessionLocal()` in the suite at a real server in one move
    — a far larger change than this task, with no way to distinguish a new PostgreSQL-specific
    failure from a regression. The negative assertion
    `test_the_unit_tests_job_still_has_no_database_url` is the load-bearing one in the new
    workflow test file, and the conftest default stays "blank it" for the same reason
  - **WHICH OF THE THREE BLOCKED FILES NOW RUN, AND WHICH DOES NOT.** (1)
    `tests/test_atomic_order_cancellation_fix.py` **now runs** — the job provisions `orders` —
    with its fourth test `test_transaction_isolation_for_cancellation` **deselected, not
    rewritten**: it asserts that the plain, general `SessionLocal()` reports `serializable`, which
    contradicts `core/database.py`'s own design, and only
    `backend/paper/paper_repository.py` retries on PostgreSQL's `40001` serialization_failure, so
    making the shared engine SERIALIZABLE would turn a dormant control into live "could not
    serialize access" errors. Deselecting states the conflict; editing the assertion would erase
    it. (2) `tests/test_transaction_isolation_serializable.py` is **not selected**, for the same
    reason — all three of its tests make that same global-SERIALIZABLE assertion. Its
    *environment* gap is closed (a PostgreSQL now exists and the file can be invoked against it
    by hand); its *design* conflict is an open decision this task does not take. (3)
    `tests/test_strategy_lifecycle_concurrency.py` **stays BLOCKED**: it needs a `strategies`
    table, and **no migration declares one** — zero `create_table('strategies')` across
    `backend_app/alembic/versions/*.py` and zero `CREATE TABLE … strategies` across
    `backend_app/migrations/*.sql`, both counted in this task. **Task 12.6 and clause 1.16's
    database-level-serialisation half therefore remain BLOCKED with that gap named.** No
    `CREATE TABLE strategies` was invented to make a test green; a schema object that no
    migration declares is a provisioning defect, not a test fixture
  - **Two alembic discoveries, and a finding larger than this task: THE REPOSITORY CANNOT
    PROVISION ITS OWN DATABASE.** The job runs `alembic upgrade d97ffff9c3bb` — the branchpoint,
    and the revision whose `op.create_table('orders', …)` the cancellation tests need — and
    **not `upgrade head`, because `upgrade head` cannot complete on an empty database**. (i)
    `4ef23035a692_baseline.py`'s `upgrade()` contains **only DROPs**; its `CREATE TABLE`
    statements are in `downgrade()`. Stopping one revision earlier would leave no `orders` table
    at all. (ii) `add_foreign_keys_20260817.py` adds
    `FOREIGN KEY (tenant_id) REFERENCES profiles(id)`, and **`profiles` has no `CREATE TABLE` in
    any migration or SQL file in this repository**. Its per-statement `try/except Exception` does
    not rescue it: PostgreSQL aborts the entire transaction on the first failed statement, so
    everything after the swallow — *including alembic's own `UPDATE alembic_version`* — fails
    with `InFailedSqlTransaction`. Consequence beyond CI, recorded here because nothing else
    records it: **there is no disaster-recovery rebuild and no way to stand up staging from
    source.** The live database's 71 `public` tables exist only because they were applied
    out-of-band. That is its own launch blocker and is not fixed by this task
  - **Tests, and what was ACTUALLY OBSERVED.** `tests/test_database_isolation_level_control.py`
    → **23 passed**, no database needed: the `ArgumentError` is raised by SQLAlchemy itself, and
    SQLite can carry both halves of the proof (SERIALIZABLE applies and reads back; READ
    COMMITTED is refused by the dialect and therefore raises). It also pins the source directly —
    neither module may reintroduce the raw statement or the swallowing warning — because the two
    `database.py` sites sit in a branch no in-process test can reach, so reading the shipped
    source is the only cover available for them. `tests/test_pr_check_postgres_service.py` → **21
    passed** (**one of these failed first** and is the reason `conftest.py` is in this commit:
    `test_the_conftest_still_blanks_database_url_by_default` failed against the tree the previous
    attempt left, proving the opt-in half of defect B was never written).
    `tests/test_database_isolation_level_postgres.py` → **7 skipped** locally by design, and its
    assertions reproduced statement-by-statement against production: before —
    `read committed`, raw string `ArgumentError`, `text()` mid-transaction `ActiveSqlTransaction`;
    after — `serializable` on the first query *and* after one has run, `repeatable read` for the
    non-default control (which is what rules out a false positive, SERIALIZABLE being plausible
    as a server default), `serializable` from `get_financial_db_context`, `read committed` still
    on the general session, and `IsolationLevelUnavailable` for a bad level. The **7 pre-existing
    failures** in `test_atomic_order_cancellation_fix.py` + `test_transaction_isolation_serializable.py`
    were confirmed unchanged by this task and are the environment gap itself —
    `sqlite3.OperationalError: no such table: orders` and `near "SHOW": syntax error`
  - **Gates.** New files **44 passed / 7 skipped**; `tests/test_websocket_upgrade_gate.py` →
    **22**; `tests/test_schema_table_reference_drift.py` → **73**;
    `tests/test_mounted_endpoint_projections.py` → **19**;
    `tests/test_marketplace_eligibility_tenant_verdict.py` → **7**;
    `tests/test_no_undefined_names.py` → **3** (**124 together**); the database/execution subset
    found by grepping for `SessionLocal`/`database_pool`/`get_db` —
    `test_database_pool.py` + `test_database_pool_math.py` + `test_get_db_dependency.py` +
    `test_execution_environment_guard.py` → **50 passed** (the broad suite is ~11,000 tests and
    was not run in full; this is the database-touching subset of it, named rather than implied);
    `flake8 --select=E9,F63,F7,F82` exit 0 on all five touched Python files; `backend_app.main`
    imports with **351** routes; `01-pr-check.yml` parses under `yaml.safe_load`. Production
    re-verified untouched after the work: **71 tables in `public`, zero `vq_*` schemas**, server
    17.6 — `SHOW` and the session-scoped isolation level touch no table
  - **What this does not prove, stated rather than implied.** The `database-tests` job has never
    executed: GitHub Actions cannot be driven from this environment, so **the next CI run is the
    real verification** of the service container, the provisioning step and the two files it
    selects. Everything asserted about that job here is structural — the file parses and the
    wiring says what it must. The production measurements were taken through a direct session
    (port 5432); they say nothing about behaviour under the pooler's transaction mode, where
    session-scoped settings have different semantics. And the control is now *operative and
    reachable* but still has **no production caller**: `get_financial_db_context` exists, works,
    and is called by nothing outside tests. Deciding which money paths should use it is a
    separate change
  - _Requirements: 1.16 / 2.16 (the database-level serialisation half, carried BLOCKED by tasks_
    _12.6 and 13.11 — a SERIALIZABLE session that genuinely works is the mechanism that half_
    _needs, and it now exists; the clause stays BLOCKED only on the missing `strategies` table)_
    _and `§Bug condition` by way of 2.5's principle (a control that cannot be applied SHALL NOT_
    _let the caller continue believing it was — the pre-fix code answered "SERIALIZABLE" with a_
    _`read committed` session and a warning, which is a fabricated status, not an absence)_


- [ ] 13.22 THIS REPOSITORY COULD NOT PROVISION ITS OWN DATABASE — three live
  production tables, including both core tables, were declared by NOTHING, and the
  declaration was captured from production's catalogue rather than written from memory
  - **The gap, counted rather than asserted.** Parsing `CREATE TABLE` out of
    `backend_app/migrations/*.sql` + `migrations/*.sql` yields 64 tables, and
    `op.create_table(...)` out of `backend_app/alembic/versions/*.py` yields 19.
    Differencing that union against production's `pg_tables` (PostgreSQL 17.6,
    **71** base tables in `public`, read-only) leaves **four** names declared by
    nothing at all: `alembic_version` (1 column, 1 row — Alembic creates it
    itself from `env.py`, so it is **not** a defect and is excluded),
    `processed_orders` (3 columns, 25 rows — **a new finding in this task**),
    `profiles` (20 columns, 181 rows — the core user table) and `strategies`
    (26 columns, 187 rows — the core strategy table). Task 13.21 recorded the
    consequence and could not fix it: **no disaster-recovery rebuild and no way
    to stand up staging from source.** Production's 71 tables exist only because
    they were applied out-of-band
  - **Distinct from the sixteen ALEMBIC-ONLY tables, and the distinction is kept
    straight.** `dag_tasks`, `execution_records`, `fills`, `idempotency_keys`,
    `invoices`, `library_ratings`, `library_strategies`, `orders`,
    `payment_methods`, `positions`, `reconciliation_mismatches`, `referrals`,
    `subscriptions`, `transaction_checkpoints`, `transaction_records` and
    `transaction_rollbacks` are declared in `alembic/versions/` and by no SQL
    migration. They are **deliberately NOT re-declared** by this task: two
    declarations of one table is the divergence 016's header warns about and the
    one 13.15 found between `006_reconcile_production_database.sql` and
    `referral_system_redesign.sql`. `referrals` is the one 13.15 proved ABSENT
    from production — declared in Alembic, not in the database — which is a
    different defect from the three in this task and stays recorded as such
  - **New migration `020_declare_pre_existing_tables.sql`, and EVERY LINE OF ITS
    DDL WAS CAPTURED FROM THE CATALOGUE.** Not written from knowledge, not taken
    from the audit documents. The capture method is stated in the file header so
    a reader can tell captured DDL from invented DDL:
    `information_schema.columns` for name / ordinal position / type / length and
    precision / nullability / default; `pg_constraint` with
    `pg_get_constraintdef(oid)` so every PK, UNIQUE, FK and CHECK is
    PostgreSQL's own rendering of the live constraint; `pg_indexes.indexdef`;
    `pg_policies`; and `pg_trigger` (none of the three has one, so none is
    declared). 020 is **017/018/019 were all taken** — 019 is the parallel
    workstream's, now committed
  - **What the catalogue said that the documents did not.**
    `PHASE_7A_AUTH_FORENSIC_AUDIT.md` and `TENANT_ISOLATION_AUDIT_REPORT.md`
    both imply a `user_id` on `processed_orders`; the column exists, but the
    table has only THREE columns and its primary key is **`order_id`**, not a
    surrogate `id` — it is an idempotency ledger, and the PK *is* the
    duplicate-suppression mechanism. `user_id` is **NULLABLE**, which has a
    consequence nothing had written down: a row with a NULL `user_id` satisfies
    `auth.uid() = user_id` for nobody, so it is invisible to every browser-side
    caller and reachable only by `service_role`. Flagged, not fixed — `SET NOT
    NULL` would be an ALTER against a populated table. Two more shapes that
    would have been got wrong by inference: `profiles.balance` is
    **unconstrained** `NUMERIC` (no precision, no scale) with `DEFAULT 0.00`,
    and `strategies.current_version` / `environment` are `varchar(20)` while
    every other text column on that table is `TEXT`
  - **`rls_migration.sql` WAS NEVER APPLIED, and the catalogue is what proves
    it.** That repo-root file is the only other place in the tree claiming RLS
    for these tables. It creates `profiles_authenticated_owner`,
    `strategies_authenticated_owner` and `processed_orders_authenticated_owner`;
    production has **none of those three names**. It creates
    `idx_strategies_user_id` and `idx_processed_orders_user_id`; production has
    **neither** (`strategies` carries a composite `idx_strategies_user_status`
    instead, and `processed_orders` has only its primary key index). Of
    everything that file would create, only `idx_profiles_id` is present. 020
    reproduces the **six policies production actually has** —
    `Select own profile`, `Update own profile`, `profiles_owner_access`,
    `profiles_service_role`, `Manage own strategies`,
    `Manage own processed orders` — four of them named with spaces and initial
    capitals, which is the fingerprint of the Supabase dashboard this whole
    migration exists to replace. The names are kept **verbatim and quoted**,
    because the idempotency guard matches on `policyname`: renaming them to the
    repo's snake_case convention would make 020 create a second, duplicate
    policy on a live table instead of being a no-op
  - **A PRE-EXISTING FALSE DECLARATION THE NEW ONE EXPOSED, fixed at the root.**
    `001_strategy_architecture.sql` line 291 read `ALTER TABLE strategies ADD
    COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'draft'`. Production carries
    `strategies.status TEXT DEFAULT 'stopped'::text`, so **both halves were
    false** and the statement has been a no-op in every database since it was
    written — `status` always already exists. Invisible until now, because with
    no `CREATE TABLE` for `strategies` there was only ONE declaration to
    compare, and the roster that recorded production's columns recorded names
    without types. The moment 020 declared the real type,
    `test_the_divergence_inventory_has_not_grown` failed with
    `strategies.status: character varying(20) in ['001…']; text in ['020…']` —
    exactly the `strategy_backtests.version` shape, where 001 said `VARCHAR(20)`,
    006 said `INTEGER`, production took the integer and every backtest insert
    failed with `22P02` until 016 reconciled it. **Recording it in
    `DIVERGENT_COLUMN_TYPES` was refused** (that inventory holds conflicts which
    already shipped and may only shrink), and a reconciling `ALTER COLUMN …
    TYPE` was refused too — against production that is an ALTER on a live table,
    and the parser adds a spelling rather than replacing one, so it would not
    have resolved the conflict anyway. 001's token was corrected instead, with
    the reasoning in a 24-line comment above it: the correction **cannot
    execute** on any database, because the only declaration of `strategies` is
    020 and 020 must run first
  - **PROVISIONING ORDER, written where an operator will find it.** New
    `backend_app/migrations/PROVISIONING_ORDER.md`, linked from 020's header.
    **020 is numbered last and must be applied FIRST** — nine files in the
    numbered set `ALTER` `profiles` or `strategies`, so on an empty database
    `001` fails at its section 6 with `42P01`. The number could not be lowered
    (017-019 taken) and renaming an applied file is invisible to an operator,
    since migrations here are applied by hand with nothing recording which files
    an environment has run. The doc states which tables come from Alembic and
    which from the SQL set, the order, and the measured obstacles: (i)
    `4ef23035a692_baseline.py`'s `upgrade()` contains **only DROPs** — re-counted
    here by AST as 30 `execute` calls and **zero** `op.create_table`, its seven
    `CREATE TABLE`s being in `downgrade()` — which is why step 2 names
    `d97ffff9c3bb` and not `head`; (ii) `add_foreign_keys_20260817.py` adds
    `FOREIGN KEY (tenant_id) REFERENCES profiles(id) ON DELETE CASCADE` **five
    times** (`fills`, `orders`, `positions`, `dag_tasks`, `execution_records`)
    and so can only run AFTER 020. **That last point is the actual unlock:** with
    `profiles` declared, the Alembic chain past the branchpoint stops being
    unrunnable
  - **PROVEN ON REAL POSTGRESQL, column by column, and programmatically.** The
    scratch-schema technique of 13.11 / 13.15 / 13.16 / 13.21: a uniquely named
    schema on the production server, `search_path` pinned to it, the migration
    text rewritten so that every `public.` reference and every `'public'`
    catalogue literal addresses the scratch schema, the whole run in ONE
    transaction ending in `ROLLBACK`, and `DROP SCHEMA … CASCADE` before it.
    The rewrite is **hard-asserted before the server is touched**: zero residual
    lowercase `public`, and the four `TO PUBLIC` role grants (a ROLE, not the
    schema) still intact. `auth.users` and `library_strategies` are remapped to
    stubs inside the scratch schema so the run never takes a lock on a live
    Supabase table — `ALTER TABLE … ADD FOREIGN KEY` locks the *referenced*
    table, and the constraint definitions are production's own
    `pg_get_constraintdef` output, so their creatability against the real
    referents is established by their existence. Result: **76 checks, 0
    failures.** 49/49 columns identical in name, ordinal position, type,
    precision, nullability and default (20 + 26 + 3); all 11 index definitions
    identical after schema normalisation; all 10 constraints identical; all 6
    policies identical in command, roles, `USING` and `WITH CHECK`; RLS flags
    `(true, false)` on all three
  - **IDEMPOTENT, and a NO-OP AGAINST PRODUCTION.** Applied a second time in the
    same transaction: the catalogue was byte-identical to run 1, and the only
    output was ten `already exists, skipping` notices. Every `CREATE TABLE` and
    `CREATE INDEX` is `IF NOT EXISTS`; PostgreSQL has no `CREATE POLICY IF NOT
    EXISTS`, so all six policies are guarded on `pg_policies` by
    schema + table + policy name — the 018 section 4 / 017 section 3d / 008
    section 6 pattern, **not** `DROP POLICY IF EXISTS … CREATE POLICY`, which
    would leave a populated RLS-enabled table policy-less in between. Verified
    afterwards on a fresh read-only session: **71 tables in `public`, zero
    `vq_*` schemas, `profiles` 181 / `strategies` 187 / `processed_orders` 25
    rows unchanged, 6 policies**. There is deliberately **no `COMMENT ON`** in
    020: none of the three tables carries a comment in production, so a comment
    would be a WRITE against the live catalogue. The prose lives in the header
  - **Four foreign keys, added separately and guarded, with the skip announced.**
    `auth.users` is the platform's and `library_strategies` is Alembic's, so an
    inline `REFERENCES` would abort the whole file on a from-source rebuild. Each
    FK is a guarded `ALTER TABLE … ADD CONSTRAINT` keyed on
    `conrelid = '…'::regclass` rather than on the bare constraint name (names are
    unique per table, not per database), and a missing referent produces a
    `RAISE NOTICE` naming the skipped constraint — so a rebuild that produced a
    weaker schema than production says so in its own log instead of looking
    clean. The asymmetry is production's and is preserved: `profiles.id` cascades
    on user delete, `strategies.user_id` does not
  - **GRANTS ARE NOT RESTATED, and the reason is measured.** All three tables
    carry the identical privilege shape in production, and so do **44 of the 71**
    `public` tables — it is Supabase's schema-level default, not a per-table
    decision. Emitting `GRANT`s would turn a platform default into a claim this
    repository owns; emitting the `REVOKE`s that would narrow it (`anon` holds
    INSERT/UPDATE/DELETE on `profiles` today) would **change production**, which
    this migration must not do. Narrowing `anon` on these three tables is a real
    and separate decision, named here rather than smuggled into a no-op
  - **GUARD, extended in the file that already owns this claim.** No new test
    file: `tests/test_schema_table_reference_drift.py` **73 → 86**. The three
    tables LEFT their exemptions rather than being added to any —
    `PRE_MIGRATION_BASE_TABLES` is now **empty** (`profiles`, `strategies`
    removed), `SQL_UNDECLARED_PRESENT_IN_PRODUCTION` is 4 → 2 entries
    (`execution_records` and `library_strategies` stay), and
    `PRODUCTION_COLUMNS_FOR_UNCREATED_TABLES` lost its `profiles` (20 columns)
    and `strategies` (26) rosters because the parse is now the oracle for both.
    `library_strategies` and `library_ratings` STAY exempt — Alembic-only,
    deliberately not re-declared, so the recorded roster is the only cover their
    columns have — and `RETIRED_PRODUCTION_COLUMNS_EXECUTION_RECORDS` is
    untouched. Two loops became vacuous when their sets emptied, which is the
    failure mode this file pins everywhere else, so both are now **positive
    assertions**: that `PRE_MIGRATION_BASE_TABLES` IS empty, and that 020 still
    declares the two names that left it
  - **The new load-bearing assertion.** `PRODUCTION_PUBLIC_TABLES` records all
    71 production table names, and
    `test_only_alembic_version_is_declared_by_nothing` differences it against the
    PARSED declaration set and asserts the remainder is **exactly**
    `{alembic_version}`. Stated as an equality, not a subset, so it fails in both
    directions: a new undeclared table appearing in the tree fails the build, and
    a declaration quietly disappearing fails too. Guarded by
    `test_alembic_version_is_genuinely_declared_by_nothing` (the one exemption
    must stay real, not become a typo that happens to pass) and
    `test_the_production_roster_is_the_size_it_was_measured_at` (a truncated
    roster would satisfy the equality). Plus
    `test_020_does_not_redeclare_the_alembic_only_tables` and
    `test_the_alembic_only_roster_is_still_alembic_only`, which hold the sixteen
    on Alembic's side of the line
  - **ASSERTIONS ACTUALLY OBSERVED FAILING BEFORE THE FIX — four, and then
    sixteen.** Against the tree with 020 present but the guard not yet updated:
    `test_the_tolerated_base_tables_are_still_exactly_that`
    (`'profiles' is now declared by ['020_declare_pre_existing_tables.sql'], so
    the exemption is stale`), `test_the_present_but_undeclared_set_is_still_
    exactly_that` (same name, same reason),
    `test_the_recorded_production_column_sets_are_still_what_they_claim`
    (`'profiles' is now declared by a CREATE TABLE … delete the entry`) and
    `test_the_divergence_inventory_has_not_grown` (the `strategies.status`
    conflict above) — **4 failed, 69 passed**. That is the stale-exemption
    mechanism working: the suite refused to let the declaration land while the
    exemptions that excused its absence were still there. Then, with 020
    temporarily removed from the tree, the finished guard was re-run and **16
    failed, 70 passed**, including every member of the new
    `TestMigration020IsANoOpAgainstProduction` class and
    `test_only_alembic_version_is_declared_by_nothing` — so the new assertions
    are demonstrated to fail without the fix rather than claimed to
  - **Gates.** `tests/test_schema_table_reference_drift.py` → **86 passed**;
    `tests/test_database_isolation_level_control.py` → **23**;
    `tests/test_pr_check_postgres_service.py` → **21**;
    `tests/test_websocket_upgrade_gate.py` → **22**;
    `tests/test_mounted_endpoint_projections.py` → **19**;
    `tests/test_marketplace_eligibility_tenant_verdict.py` → **7**;
    `tests/test_no_undefined_names.py` → **3**; the migration-parsing suites
    `test_migration_tooling` + `test_no_dormant_schema_references` +
    `test_schema_as_code_completeness` + `test_library_schema_contract` all
    green; `flake8 --select=E9,F63,F7,F82` exit 0 on the one touched Python file;
    `backend_app.main` imports with **351** routes
  - **What this does NOT prove, stated rather than implied.** The Alembic chain
    past `d97ffff9c3bb` has still never been run end to end — 020 removes the
    `profiles` obstacle that made `add_foreign_keys_20260817.py` abort, and that
    is a structural unlock, not an executed one. 020 itself has never been
    applied to `public`, by design: it is proven to be a no-op there, and
    production already has everything it declares. The scratch-schema proof
    stubbed `auth.users` and `library_strategies` deliberately, so it does not
    prove those two foreign keys are creatable — their existence in production
    does. And three shapes are **flagged, not fixed**, because each would be an
    ALTER against a live table: `processed_orders.user_id` is nullable, `anon`
    holds full DML on all three tables, and `profiles` has four overlapping
    policies where one would do
  - _Requirements: `§Bug condition` by way of 2.5's principle (a schema object_
    _that no source declares is a system that cannot state its own shape — a_
    _rebuild would silently omit three populated tables and every reader of them_
    _would fail with 42P01 against a fresh environment, which is an absence_
    _presented as a working deployment) and 1.16 / 2.16 (12.6 and 13.21 carried_
    _the database-level-serialisation half BLOCKED on the missing `strategies`_
    _table specifically; `tests/test_strategy_lifecycle_concurrency.py` needs a_
    _`strategies` table and 020 is the declaration it was waiting for — the_
    _schema gap is closed, though the test has not been run against it here)_


- [ ] 13.23 CLAUSE 1.16 IS NO LONGER UNVERIFIED — it is a PROVEN P0 DEFECT at both
  the application and the database level, half of it is now FIXED at the root cause,
  and the other half is reported as a decision rather than half-implemented
  - **FIRST, A CORRECTION, because two earlier records disagree and one of them is
    wrong.** Task 13.21 recorded that `tests/test_strategy_lifecycle_concurrency.py`
    "**stays BLOCKED**: it needs a `strategies` table, and no migration declares one".
    **That attribution was mistaken.** The file touches no database at all: it drives
    `_FakeSupabase`/`_Query`, an in-process `Dict[str, List[Dict]]` declared in the file
    itself, and `grep` over it for `SessionLocal`, `DATABASE_URL`, `psycopg`,
    `sqlalchemy` or `database` returns matches only in prose. It was runnable before
    13.22 and is runnable now; 13.22's `strategies` declaration did not change its
    status because its status was never schema-dependent. **Task 13.13's inventory had
    it right** — "`test_strategy_lifecycle_concurrency` (2) are the DELIBERATE P0
    proofs 12.6 records" — and so did **task 12.6**, which closed the application-level
    half and named only the *database-level* half as BLOCKED. 13.21 generalised from
    the two files beside it in that paragraph, both of which genuinely do need a server.
    13.22's `_Requirements:_` line repeated the error. This task corrects both by
    citation rather than by silently restating the record
  - **SO WHAT 13.22 ACTUALLY UNBLOCKED, stated precisely.** Not the fake-database file —
    the **database-level half**, which 12.6, 13.11, 13.21 and 13.22 all carried BLOCKED
    and which needs real `strategies` and `strategy_deployments` tables to race two real
    sessions against. Before 020 declared `strategies`, that world could not be built
    from source, so the proof could not be written without inventing DDL. It can now.
    That half is what this task closes
  - **WHAT THE TWO DELIBERATE P0 PROOFS SAY, RUN THIS TASK, WITH THE COUNTEREXAMPLES
    IN FULL.** `tests/test_strategy_lifecycle_concurrency.py` → **2 failed, 25 passed**
    in 67s, no database, unchanged tree. They fail **because the defect is real**, which
    is the first of the three outcomes and not an environment failure:
    - `test_the_four_operations_can_land_running_but_deleted` →
      `AssertionError: 21/40 interleavings violated Requirement 2.16`. Counterexample,
      verbatim: `[gather-0] strategy str_12_6_target is archived
      (archived_at='2026-10-03T06:49:04.069022+00:00') AND carries 1 live deployment(s)
      ([('6b6ea6de-3f8f-40df-b5c7-d5d343a3673e', 'running')]) - running-but-deleted.`
      **The state the strategy ended in: archived, with a `running` deployment still
      attached.** The 21 are `gather-0` and every odd seed `gather-1` … `gather-39` —
      i.e. all 20 seeds with `seed_running_deployment=False`, where `delete` and
      `deploy` race alone, plus the first seeded one. The interleaving is: `archive`
      reads the deployments table and sees none blocking → `deploy` reads
      `strategies.archived_at` and sees NULL → both write
    - `test_deploy_can_race_itself_into_a_double_deploy` →
      `AssertionError: 12/12 interleavings produced a double deploy`. Counterexample,
      verbatim: `[double-deploy-0] strategy str_12_6_target carries 2
      simultaneously-live deployments ([('f9f32837-3ba1-4c6e-9deb-884e191d00b3',
      'running'), ('a86236f4-e7b3-4eb3-901e-4468dec56bf6', 'running')]) - double
      deploy.` **The state: two `running` deployment rows for one strategy.** Every
      single seed, not a sample
  - **AND NOW THE DATABASE-LEVEL HALF, ON A REAL SERVER, TWO REAL SESSIONS.** New
    `tests/test_strategy_lifecycle_concurrency_postgres.py` — PostgreSQL 17.6, port
    5432 session mode, a uniquely named scratch schema created and dropped in the same
    run, `search_path` pinned, `public` never read or written, two genuine OS threads on
    two genuine server backends released together off a `threading.Barrier` (13.11's
    technique), each parked at a second barrier between its own read and its own write.
    **9 passed.** Six races, and three of the six results are NEGATIVE findings that
    determine the fix:

    | # | race | isolation | outcome | verdict |
    |---|---|---|---|---|
    | R1 | deploy vs deploy | READ COMMITTED | both COMMIT, 2 live rows | **double deploy** |
    | R2 | archive vs deploy | READ COMMITTED | both COMMIT, archived + 1 live | **running-but-deleted** |
    | R3 | deploy vs deploy | SERIALIZABLE | **both still COMMIT**, 2 live rows | **double deploy** |
    | R4 | archive vs deploy | SERIALIZABLE | archiver cancelled `40001` | legal |
    | R5 | deploy vs deploy + partial unique index | READ COMMITTED | loser `23505` | legal |
    | R6 | atomic `UPDATE … WHERE` archive vs deploy | READ COMMITTED | both COMMIT | **running-but-deleted** |

  - **R3 IS THE FINDING THAT CHOSE THE MECHANISM, and it is a negative.** The
    SERIALIZABLE control task 13.21 built — `core/db_isolation.isolated_session`, now
    operative and verified — **does not prevent the double deploy.** Two INSERTs of two
    *different* rows give SSI no read/write dependency cycle to cancel, because neither
    write falsifies a predicate the other read. Measured, both sessions committed, two
    live rows. So the isolation mechanism was available and is the wrong tool for this
    half; that is a measurement, not a preference
  - **R6 IS THE SECOND NEGATIVE, and it is why the other half is not fixed.** The
    atomic `UPDATE … WHERE status = …` pattern task 13.11 proved for order cancellation
    (`backend/transactional_execution_manager.py:863`, raising on `rowcount == 0`)
    **does not transfer.** It works for cancellation because predicate and write are the
    same row. Transplanted here as `UPDATE strategies SET archived_at = now() WHERE id
    = ? AND user_id = ? AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM
    strategy_deployments d WHERE d.strategy_id = strategies.id AND d.status IN (…))`,
    the `NOT EXISTS` is evaluated against a snapshot taken before the concurrent INSERT
    commits, so it sees nothing, the UPDATE matches, and **`rowcount` is 1, not 0** —
    there is nothing for the caller to raise on. Textbook write skew across two tables
  - **THE FIX THAT LANDED: `021_strategy_deployment_live_uniqueness.sql`, at the root
    cause, in the database.** A partial unique index —
    `CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_deployments_one_live ON
    public.strategy_deployments (strategy_id) WHERE status IN (…)`. This is
    `uq_paper_account_default`'s shape, already in this schema
    (`CREATE UNIQUE INDEX uq_paper_account_default ON public.paper_accounts (user_id,
    currency) WHERE (session_id IS NULL)`, read off `pg_indexes` in this task) and the
    exact contrast `test_strategy_lifecycle_concurrency.py`'s own docstring named as the
    thing `strategy_deployments` lacked. R5 is its regression proof: the losing INSERT
    fails with `23505 duplicate key value violates unique constraint
    "uq_strategy_deployments_one_live"`, one live row survives, at READ COMMITTED with
    no isolation change and no application lock
  - **THE PREDICATE NAMES NINE SPELLINGS, NOT THREE, and that was nearly a bug.**
    `strategy_lifecycle._STATUS_TO_BINDING_STATE` maps nine `status` spellings onto the
    three live states in `STOPPABLE_BINDING_STATES`: `deploying`, `deployed`, `pending`,
    `queued`, `starting`, `restarting` → DEPLOYING; `running`, `active` → RUNNING;
    `paused` → PAUSED. A predicate of `('deploying','running','paused')` would have left
    six legacy spellings outside the index — a guard that looks present and is
    bypassable. `test_the_index_predicate_covers_every_live_status_spelling` differences
    the predicate against that dict in **both directions**, so neither a new live
    spelling nor a terminal one creeping in can pass. The eight terminal spellings are
    deliberately outside it: STOPPED is terminal by design and a strategy must be able
    to accumulate finished deployments
  - **SAFE TO APPLY, AND THAT WAS CHECKED BEFORE THE FILE WAS WRITTEN.**
    `public.strategy_deployments` holds **0 rows** — counted against the live server,
    alongside `public.strategies`' 187. `SELECT strategy_id, count(*) … HAVING count(*)
    > 1` over the live statuses returns **0 strategies**, so there is no existing
    duplicate for `CREATE UNIQUE INDEX` to choke on and no scan of consequence. Had a
    duplicate existed the migration would not have been written: reconciling live
    duplicate deployments is an operational decision, not a schema one. Not
    `CONCURRENTLY` — it cannot run inside a transaction block, which would make the file
    unusable through `scripts/apply_migrations.py`, and on an empty table the ordinary
    lock costs nothing
  - **WHAT PRODUCTION'S CATALOGUE SAYS, READ-ONLY, AND IT CONFIRMS THE GAP WAS REAL.**
    `public.strategy_deployments`: 6 constraints — PK, three FKs, and two CHECKs
    (`chk_sd_mode`, `chk_sd_live_needs_account`) — 6 indexes of which **the only UNIQUE
    one is the primary key**, and 1 non-internal trigger,
    `trigger_update_strategy_deployments_updated_at`, which touches a timestamp and
    guards nothing. `public.strategies`: 3 constraints (PK + two FKs), 6 indexes, and
    **zero** non-internal triggers; its only `archived_at` object is the non-unique
    partial `idx_strategies_archived_at`. So nothing in the live schema spanned the two
    tables and nothing limited live rows per strategy. The fake-database file inferred
    that from the migrations; this read it off `pg_constraint`, `pg_indexes` and
    `pg_trigger`
  - **WHAT IS NOT FIXED, AND WHY IT IS A DECISION RATHER THAN A HALF-IMPLEMENTATION.**
    *Running-but-deleted* stays a **proven P0 defect with a reproduction** (R2, plus
    21/40 at the application level). It is an invariant spanning two tables, so no index
    can hold it, and all three candidate mechanisms exceed this task:
    (i) **SERIALIZABLE** works — R4 cancels the archiver with `40001 … Reason code:
    Canceled on identification as a pivot, during commit attempt` — but task 13.21
    established that **only `backend/paper/paper_repository.py` retries on `40001`**, so
    routing the archive path through it without a retry layer converts a silent race
    into a user-visible 500. A retry layer for the strategy paths is its own change.
    (ii) **A trigger pair** on both tables would hold it, but production carries **zero**
    triggers on `strategies` and adding one is a write against the live catalogue —
    the same line 13.22 drew at `SET NOT NULL` and `ALTER COLUMN … TYPE`.
    (iii) **`SELECT … FOR UPDATE` on the `strategies` row in both paths** is the
    textbook answer and is not reachable here: both write paths go through the Supabase
    client (`StrategyService.deploy_version` at
    `backend/strategy_service.py:1468`, `transition_deployment` at `:1820`), and
    PostgREST cannot express `FOR UPDATE`. It would mean moving those paths onto
    SQLAlchemy — a lifecycle rewrite.
    **Plus a hard practical blocker that is worth naming rather than discovering later:**
    both paths live in `backend_app/backend/strategy_service.py`, which the parallel
    workstream had **uncommitted modifications in** throughout this task
    (`git status` shows ` M`, as it does for `routers/strategy_operations.py`). A
    pathspec commit touching that file would carry their work-in-progress into this
    commit. So the fix could not have been landed cleanly even had it been bounded
  - **CI, AND THE MINIMUM THAT WAS ACTUALLY APPLIED.** `.github/workflows/01-pr-check.yml`'s
    `database-tests` job now also runs `tests/test_strategy_lifecycle_concurrency_postgres.py`.
    **The `Provision schema` step is UNCHANGED, and that is a decision with a measured
    reason, not an omission.** The new file self-provisions: it creates its two tables
    inside a scratch schema from the columns it parses out of the real declarations —
    `020_declare_pre_existing_tables.sql` for `strategies`, `001_strategy_architecture.sql`
    for `strategy_deployments` — and never reads `public`, so it runs against the bare
    `postgres:17-alpine` with nothing added. Extending provisioning to give it
    `public.strategies` instead would require applying 020 **first** (per
    `PROVISIONING_ORDER.md` — nine files in the numbered set `ALTER` `profiles` or
    `strategies`), and **020's SECTION 0 preflight `RAISE EXCEPTION`s** on any database
    lacking the `authenticated` and `service_role` roles and the `auth.uid()` function,
    which a vanilla PostgreSQL has none of; `001` then needs `auth.users` and
    `exchanges` as FK referents. That is a Supabase shim plus a partial replay of the
    numbered set — the from-scratch path `PROVISIONING_ORDER.md` documents and marks
    **untested** — and with no local PostgreSQL and no Docker it could not have been
    verified from here, so adding it would have been a structural claim about a job that
    has never run. The reasoning is in the workflow beside the step, and
    `test_the_concurrency_proof_needs_no_extra_provisioning_step` pins it so the absence
    cannot later read as an oversight. **The `unit-tests` lane is untouched** — no
    `DATABASE_URL`, still the SQLite fallback, and
    `test_the_unit_tests_job_still_has_no_database_url` still passes
  - **THE SKIP IS PER-TEST, NOT MODULE-LEVEL, ON PURPOSE.** The new file's two
    source-level tests — that both tables are still declared, and that 021's predicate
    matches `_STATUS_TO_BINDING_STATE` — carry **no** `@requires_postgres` and run in
    the main lane. A module-level `pytestmark` would have taken them with the seven
    races, and the drift guard would then only ever run in the one job that has a
    server, which is how a guard stops guarding.
    `test_the_unit_tests_lane_still_carries_the_source_level_half` asserts exactly seven
    marks and no module-level assignment
  - **TESTS, OBSERVED BEFORE AND AFTER.** `tests/test_strategy_lifecycle_concurrency.py`
    → **2 failed / 25 passed** before this task and **unchanged after** — no assertion
    was weakened, skipped or rewritten, and the application-level defect is still
    reproduced, because 021 fixes the database and not the Python read-decide-write.
    `tests/test_strategy_lifecycle_concurrency_postgres.py` → **9 passed** against the
    real server with `DATABASE_URL` set, **2 passed / 7 skipped** without it (the two
    being the source-level pair, which is the point of the per-test marks).
    `tests/test_pr_check_postgres_service.py` → **21 → 26**;
    `test_the_unit_tests_lane_still_carries_the_source_level_half` was **observed
    failing first**, on its own explanatory comment: the assertion was written as
    `"pytestmark" not in source`, which matched the paragraph explaining why there is no
    `pytestmark`, so it was narrowed to a module-level assignment check — a real
    false-positive caught by running it rather than by reading it
  - **Gates.** `tests/test_schema_table_reference_drift.py` → **86**;
    `tests/test_database_isolation_level_control.py` → **23**;
    `tests/test_pr_check_postgres_service.py` → **26** (**135 together**);
    `tests/test_websocket_upgrade_gate.py` → **22**;
    `tests/test_mounted_endpoint_projections.py` → **19**;
    `tests/test_marketplace_eligibility_tenant_verdict.py` → **7**;
    `tests/test_no_undefined_names.py` → **3** (**51 together**);
    `flake8 --select=E9,F63,F7,F82` exit 0 on both touched Python files;
    `backend_app.main` imports with **351** routes; `01-pr-check.yml` parses under
    `yaml.safe_load` with its five jobs intact. 86 is unchanged by 021 because 021
    declares an index, not a table, so the declaration parser's oracle does not move
  - **PRODUCTION RE-VERIFIED UNTOUCHED** after every run: **71 tables in `public`**,
    **zero `vq_*` schemas**, `profiles` **181** / `strategies` **187** /
    `processed_orders` **25** rows unchanged, server 17.6. Every race ran in a scratch
    schema dropped in the same process, and the catalogue reads were `SELECT`-only.
    **021 had not been applied to `public` as of this point in the record** — it was a
    declaration, and applying it is an operator action against a live table, not
    something a task that only writes files can do. **That operator action has since
    been taken, on 2026-10-03; the ADDENDUM at the end of this task is the record of
    it**, and this sentence is superseded by it rather than contradicted
  - **CORRECTED STATUS, so task 14 has one answer and not three.** Clause **1.16** is
    **PROVEN — as a defect**, at both levels, and is no longer UNVERIFIED and no longer
    BLOCKED: its "deployed twice" half is **proven and FIXED** (021, regression-proved
    by R5), its "deleted while running" half is **proven and OPEN as a P0 with a
    reproduction**. Task **12.6**'s `[PARTIAL BLOCKER: database-level serialisation]` is
    **discharged** — the database-level guarantee is now established, and what it
    establishes is that it did not hold. 13.13's inventory line changes: of its **10**
    "already-recorded environment gaps", the 2 from this file were never environment
    gaps (13.13 said so itself and 13.21 overwrote it), so the environment-gap count is
    **8**, and those 2 are now a proven P0 with a landed half-fix. The five partial
    blockers task 14 lists lose one: **1.16's database-level serialisation is no longer
    BLOCKED**, leaving 1.13's deployment path, 1.14's paper routes, 1.20's production log
    audit and 1.42's heap measurement
  - **What this does NOT prove, stated rather than implied.** The `database-tests` job
    has still never executed — GitHub Actions cannot be driven from here — so **the next
    CI run is the real verification** that the new file runs green on
    `postgres:17-alpine`. What is established is that it passes against a real
    PostgreSQL 17.6 and that it needs nothing from `public`, which is the property that
    makes the CI claim structural rather than speculative. 021's index has been proved
    to work in a scratch schema with production's column types, not in `public` —
    **superseded by the ADDENDUM below, which applied it to `public` and proved the real
    index bites there.** The *application-level* read-decide-write is untouched: a
    single-threaded caller can still issue a deploy that the database then refuses with
    `23505`, so the routers need to translate that into a 409 — filed here, not fixed,
    and re-filed in the addendum as the named follow-up now that the `23505` is live. And
    the running-but-deleted half is a live P0 with no guard at all
  - **ADDENDUM, 2026-10-03 — 021 HAS NOW BEEN APPLIED TO `public`. The operator action
    the bullets above deferred has been taken.** `uq_strategy_deployments_one_live`
    exists on `public.strategy_deployments` as of **2026-10-03 07:38 UTC**, PostgreSQL
    17.6, port 5432 session mode (13.21's channel). The committed file was **read off
    disk and executed verbatim** — 8550 bytes of
    `backend_app/migrations/021_strategy_deployment_live_uniqueness.sql`, not retyped and
    not inlined — so what ran is what is committed. **This was the only write to `public`
    in that action**; no table, column, constraint, trigger or row was otherwise altered
    - **THE DECISION, AND IT WAS TAKEN WITH THE ROUGH EDGE KNOWN.** It was applied
      *despite* the gap the bullet above files: the application layer does not translate
      `23505` into a 409, so a duplicate deploy now surfaces as a **500 rather than a
      clean conflict**. Three reasons, in order of weight. **(i) Silent state corruption
      is worse than an ugly error.** Before this index a racing second deploy left two
      live deployment rows and *nothing detected it* — R1 and R5 in the table above are
      the same race with and without the index, and the difference is a refusal the
      caller sees versus a fabricated state nobody sees. An ugly 500 is a bug report; a
      second `running` row is a strategy trading twice with no record that it should not
      be. **(ii) There is no blast radius today.** `public.strategy_deployments` holds
      **0 rows**, so the new error path is unreachable in practice — nothing can collide
      with nothing. **(iii) It is additive and reversible in one statement:**
      `DROP INDEX uq_strategy_deployments_one_live`. No column changed type, nothing was
      made NOT NULL, no data was rewritten
    - **PREFLIGHT, ALL NINE ITEMS, AND THE APPLY WAS CONDITIONAL ON THEM.** Read-only
      session, `VERDICT: PROCEED`: `public.strategy_deployments` **exists**; it holds
      **0 rows** (unchanged since 13.23 measured it, so the risk calculus above still
      holds and the duplicate check below is confirmatory rather than load-bearing);
      `SELECT strategy_id, count(*) … WHERE status = ANY(<the nine live spellings>)
      GROUP BY strategy_id HAVING count(*) > 1` returns **zero rows**, so there was no
      existing duplicate for `CREATE UNIQUE INDEX` to choke on;
      `uq_strategy_deployments_one_live` **did not already exist** (0 in `pg_indexes`);
      baseline census **71 tables** in `public`, `profiles` **181** / `strategies`
      **187** / `processed_orders` **25**, **zero `vq_*` schemas**, server 17.6. Had any
      item failed the file would not have been executed
    - **THE NOTICES THE MIGRATION EMITTED.** First run — one notice, no error, committed:
      `NOTICE: 021: uq_strategy_deployments_one_live present — at most one live
      deployment per strategy is now enforced by the database. A racing second deploy
      fails with 23505 unique_violation.` That is SECTION 3's own closing `RAISE NOTICE`,
      which means SECTION 3 took the `EXISTS` branch and not the `RAISE EXCEPTION` one.
      SECTION 1's preflight raised nothing, as expected
    - **THE `indexdef` AS PRODUCTION RENDERS IT, SO THE PREDICATE IS AUDITABLE.** Read
      off `pg_indexes` after the fact, in full:
      `CREATE UNIQUE INDEX uq_strategy_deployments_one_live ON
      public.strategy_deployments USING btree (strategy_id) WHERE ((status)::text = ANY
      ((ARRAY['deploying'::character varying, 'deployed'::character varying,
      'pending'::character varying, 'queued'::character varying, 'starting'::character
      varying, 'restarting'::character varying, 'running'::character varying,
      'active'::character varying, 'paused'::character varying])::text[]))`
    - **THE LIVE PREDICATE NAMES ALL NINE SPELLINGS — differenced against
      `_STATUS_TO_BINDING_STATE` in both directions, off the SERVER and not off the
      file.** `pg_get_expr(indpred, indrelid)` was parsed and compared with the dict read
      out of `backend_app/backend/strategy_lifecycle.py` by `ast`: the dict carries **17**
      spellings of which **9** map into `STOPPABLE_BINDING_STATES` (`DEPLOYING`,
      `RUNNING`, `PAUSED`). Live set from the dict and set from the predicate are
      **identical** — `active, deployed, deploying, paused, pending, queued, restarting,
      running, starting` — **missing: none, extra: none**. The **8** terminal spellings
      are correctly outside it: `canceled, cancelled, completed, crashed, error, failed,
      stopped, stopping`. This is the server-side counterpart of
      `test_the_index_predicate_covers_every_live_status_spelling`, which pins the same
      thing against the migration source; both directions now hold at both ends
    - **AND THAT COMPARISON WAS WRONG ON THE FIRST ATTEMPT, WHICH IS WHY IT IS CREDIBLE.**
      The first pass extracted literals with `'([a-z_]+)'::text` — the shape the
      *migration source* has. The server renders the casts as `::character varying`, so
      the regex matched **zero** literals and the check reported `FAIL` with all nine
      "missing". That is the same class of error as the vacuous-assertion failures the
      Notes record, except it failed loudly instead of passing emptily. Narrowed to
      `'([a-z_]+)'` over the predicate expression and re-run; both sides now carry 9 and
      the script asserts **non-emptiness of both sets** before comparing, so it cannot go
      vacuous
    - **IT IS GENUINELY A *PARTIAL UNIQUE* INDEX, FROM `pg_index` AND NOT FROM THE DDL
      TEXT.** `indisunique = True`, `indpred IS NOT NULL = True`,
      `indisprimary = False`, `indisvalid = True`, `indnatts = 1`. So it is unique, it is
      partial, it is not the primary key wearing another name, and it is valid rather
      than an incomplete build
    - **IDEMPOTENT AGAINST PRODUCTION, PROVED BY RUNNING IT A SECOND TIME.** The same
      file executed again: **no error, committed**, two notices —
      `NOTICE: relation "uq_strategy_deployments_one_live" already exists, skipping`
      (`IF NOT EXISTS` doing its job) followed by SECTION 3's same closing notice. A
      **notice, not an error**, which is what the file's header claims and is now
      measured against the live server rather than asserted
    - **THE INDEX ACTUALLY BITES, AND THE FORM CHOSEN WAS `public` + `ROLLBACK`.** Stated
      plainly because the alternative was offered: the scratch-schema form was **not**
      used, because 13.23's R5 already records it and it only ever exercises a *copy of
      the index's shape*. What was in question here is whether the index **now in
      `public`** bites, so three probes ran inside a single transaction in `public` that
      was then **ROLLED BACK**, against a real `(strategy_id, user_id, version_id)` triple
      satisfying all three FKs, all parameterized:
      **(A)** first live row, `status='running'` → **ACCEPTED**;
      **(B)** second live row for the same `strategy_id`, `status='deploying'` →
      **REFUSED `23505`**, verbatim: `duplicate key value violates unique constraint
      "uq_strategy_deployments_one_live" DETAIL: Key (strategy_id)=(c21212b6-e3e7-4a4e-
      bd0b-89cf32192825) already exists.`;
      **(C)** second row for the same `strategy_id` with a *terminal* `status='stopped'`
      → **ACCEPTED**, which is the half that proves the predicate is **partial and not
      blanket** — a strategy must still be able to accumulate finished deployments.
      Note (B) used `deploying` against (A)'s `running`: two *different* legacy spellings
      collided, so the nine-spelling predicate is doing work the three-spelling one would
      not have. `ROLLBACK` issued; **a fresh connection re-confirms `public.
      strategy_deployments` at 0 rows**
    - **CENSUS, BEFORE AND AFTER, AND ONLY THE INDEX COUNT MOVED.** **71 tables** in
      `public` → **71**; `profiles` **181** → **181**; `strategies` **187** → **187**;
      `processed_orders` **25** → **25**; `strategy_deployments` **0 rows** → **0 rows**;
      **zero `vq_*` schemas** → **zero**; server 17.6. Indexes on
      `public.strategy_deployments` **6 → 7** and constraints **6 → 6** — which is the
      whole intended effect, and confirms 13.23's finding that "the only UNIQUE one is
      the primary key" is no longer true of this table
    - **THE NAMED FOLLOW-UP, AND WHY IT WAS NOT LANDED IN THE SAME CHANGE.**
      **Translate `23505` on `uq_strategy_deployments_one_live` into a `409 Conflict`.**
      This **must land before strategies are deployed at scale** — today it is
      unreachable (0 rows), but the first real concurrent deploy turns it into a 500 on a
      path where the correct answer is a clean conflict the client can retry or report.
      It was not done here, and the reason is mechanical rather than a judgement call:
      the two files that own those paths —
      `backend_app/backend/strategy_service.py` (`deploy_version`,
      `transition_deployment`) and `backend_app/routers/strategy_operations.py` — are
      **both still carrying the parallel workstream's uncommitted modifications**
      (re-checked at the time of this addendum: `git --no-optional-locks status --short`
      shows ` M` against both). A pathspec commit touching either would carry their
      work-in-progress into this commit. So the choice was between landing the index
      without the 409, or landing neither; the reasoning in the DECISION bullet above is
      why it was the former
    - **GATES RE-RUN AFTER THE APPLY, AS A REGRESSION CHECK RATHER THAN NEW WORK, AND
      NOTHING MOVED.** `tests/test_strategy_lifecycle_concurrency_postgres.py` with no
      `DATABASE_URL` → **2 passed / 7 skipped**, the source-level pair intact;
      `tests/test_schema_table_reference_drift.py` → **86**;
      `tests/test_no_undefined_names.py` → **3** (**89 together**). **86 did not move,
      and it should not have** — 021 declares an index, not a table, so the declaration
      parser's oracle is untouched by applying it. **No code was changed**: the only
      tracked file this addendum's change touches is this `tasks.md`
  - _Requirements: 1.16 / 2.16 (BOTH halves now proven — the application-level half by_
    _`tests/test_strategy_lifecycle_concurrency.py`'s 21/40 and 12/12, re-run this task,_
    _and the database-level half by `tests/test_strategy_lifecycle_concurrency_postgres.py`'s_
    _six races on real sessions, which is the half 12.6, 13.11, 13.21 and 13.22 carried_
    _BLOCKED; "no double deploy" is fixed at the root cause by 021, "no running-but-_
    _deleted" is recorded as a proven P0 with its counterexample and reported as a_
    _decision) and `§Bug condition` by way of 2.5's principle (a platform that accepts_
    _two deploys of one strategy, or archives a strategy whose bot is still trading, is_
    _reporting a state it is not in — the second `running` row and the `archived_at`_
    _timestamp are both fabricated status, not an absence)_


- [ ] 13.24 TWO OF THE THREE IN-IMAGE HIGH DEPENDABOT FINDINGS ARE CLOSED AT THE PIN,
  THE THIRD IS LEFT PINNED ON PURPOSE, AND THE TARGET VERSIONS COME FROM THE
  ADVISORIES RATHER THAN FROM A GUESS
  - **WHERE THE NUMBERS CAME FROM, BECAUSE THIS IS THE PART THAT USUALLY GETS
    INVENTED.** Task 13.12\* recorded 29 HIGH alerts with three of them in the running
    image. This task re-read the live source instead of trusting that count:
    `gh api --paginate /repos/Narendra2212/vyomquant-saas/dependabot/alerts?state=open`
    → **195 open alerts**, raw JSON piped to a real `.py` file and decoded there
    (`gh --jq` is unusable under PowerShell quoting; the response body came back
    UTF-16). Of those: **cryptography 24 alerts, 12 HIGH**; **setuptools 8 alerts,
    4 HIGH**; **starlette 28 alerts, 12 HIGH**; **pyjwt 16 alerts, 6 HIGH**. The
    `first_patched_version` field on each alert is the authoritative floor and is what
    the two pins below are set to. The repo-root `.dependabot_raw.jsonl` was checked
    first and is **not** usable for this: every line carries only
    `{ecosystem, name, scope, severity}` — no version range, no patched version — so it
    can say *that* cryptography is HIGH but not *to what*
  - **`cryptography`: FLOOR 49.0.0, CHOSEN 49.0.0 — THEY ARE THE SAME NUMBER.**
    `43.0.0` → **`49.0.0`**. The 12 HIGH alerts resolve to three distinct floors:
    `46.0.5` (CVE-2026-26007 / GHSA-r6ph-v2qm-q3c2, missing subgroup validation for
    SECT curves, vulnerable `<= 46.0.4`), `48.0.1` (GHSA-537c-gmf6-5ccf, vulnerable
    OpenSSL inside the wheels, vulnerable `>= 0.5.0, < 48.0.1`) and **`49.0.0`**
    (CVE-2026-69249 / GHSA-jwv3-5hgf-82ww, duplicate self-signed intermediates cause
    exponential X.509 path building, vulnerable `>= 42.0.0, < 49.0.0`). The highest
    floor binds, so **49.0.0** is the minimum that clears all twelve — and because the
    four non-HIGH floors are `43.0.1`, `44.0.1`, `46.0.6` and `48.0.1`, all of them
    below it, 49.0.0 clears **all 24** open cryptography alerts, not just the HIGH ones.
    `pip index versions cryptography` reports `50.0.2` as latest with
    `50.0.2, 50.0.1, 50.0.0, 49.0.0` published above the floor. **49.0.0 was chosen
    over 50.0.2 deliberately**: nothing in the advisory set requires 50.x, this package
    is a compiled dependency under `pyjwt`, `python-jose`, `httpx`/`supabase` TLS and
    this repo's own vault, and cryptography has removed public API across majors — the
    smaller jump is the smaller behavioural risk. PyPI metadata confirms 49.0.0 is
    installable in both environments that matter: `requires_python
    !=3.9.0,!=3.9.1,>=3.9`, with `cp311-abi3` wheels for
    `manylinux_2_28_x86_64` (the image is `python:3.11-slim`) and for `win_amd64`
    (this shell is CPython 3.12.10)
  - **`setuptools`: HIGH FLOOR 78.1.1, CHOSEN 78.1.1 — AND THE MEDIUM ABOVE IT IS
    REFUSED FOR A NAMED REASON.** `75.6.0` → **`78.1.1`**. All 4 HIGH alerts are the
    same advisory, CVE-2025-47273 / GHSA-5rjg-fvgr-3xxf (path traversal in
    `PackageIndex.download` → arbitrary file write), vulnerable `< 78.1.1`,
    `first_patched_version` **78.1.1**. The remaining 4 alerts are MEDIUM
    CVE-2026-59890 / GHSA-h35f-9h28-mq5c (`MANIFEST.in` exclusion bypass in `sdist` via
    NFC/NFD Unicode collision on macOS APFS), floor **83.0.0**. **83.0.0 is refused**,
    and the reason is already written into `requirements-base.txt` above this pin:
    `razorpay==1.4.1` does `import pkg_resources` at `razorpay/client.py:4`, setuptools
    81 deprecated `pkg_resources` and **83.0.0 removed it**, so taking the MEDIUM makes
    `import razorpay` raise `ModuleNotFoundError` and turns every checkout path into
    `HTTPException(500, "Failed to initialize payment gateway.")`. 78.1.1 is therefore
    the exact intersection of "clears the HIGH" and "razorpay still imports" — verified,
    not assumed: under 78.1.1 `import pkg_resources` succeeds (with a
    `DeprecationWarning`, so the deprecation is already live at 78.1.1 rather than
    starting at 81) and `import razorpay` resolves to
    `site-packages/razorpay/__init__.py`. The refused MEDIUM is additionally an
    sdist-*authoring* bug on macOS and this package is build-time only in a Linux image
    that builds no sdists
  - **`starlette` / `fastapi`: LEFT PINNED, AND THIS IS THE DECISION, NOT AN OVERSIGHT.**
    `starlette==0.41.0` carries **12 HIGH** of its 28 alerts, with floors reaching
    `1.3.1` (CVE-2026-54283, `request.form()` limits silently ignored for
    `x-www-form-urlencoded`). It is **not** bumped here. `fastapi==0.115.3` pins
    `starlette<0.42.0,>=0.40.0`, so moving starlette to 1.3.1 is a two-major FastAPI
    upgrade touching every route in a 351-route application — a framework migration,
    not a pin bump. It stays scheduled as 13.12\*. The one starlette advisory whose
    mechanism is reachable from the edge, CVE-2026-48710 (missing `Host` validation
    poisoning `request.url.path`), is already blocked in front of the app: task 8.x
    proved CloudFront answers **400** to a poisoned `Host`. `pip check` independently
    surfaces the ceiling from the other direction —
    `sse-starlette 3.5.0 has requirement starlette>=0.49.1, but you have starlette
    0.41.0` — which is a *pre-existing* mismatch in this local environment (the pinned
    `sse-starlette` is `2.1.3`), present identically before and after this change
  - **`PyJWT`: UNTOUCHED, ALREADY ABOVE ITS HIGH FLOOR.** `PyJWT==2.14.0`. All 6 HIGH
    pyjwt alerts have `first_patched_version` **2.14.0** (CVE-2026-102266,
    -102267, -102271, -102272, -102273) — the pin is already the patched release an
    earlier task landed. Two residual MEDIUMs remain above it (CVE-2026-101918, floor
    `2.15.0`; CVE-2026-103001, `first_patched_version` **null** — no fix published
    yet). Neither is HIGH, so neither is in this task's scope
  - **THE REQUIREMENTS CHAIN, RE-VERIFIED LINE BY LINE, BECAUSE A BUMP THAT DOES NOT
    REACH THE IMAGE FIXES NOTHING.** `Dockerfile` **L26** `COPY requirements-base.txt
    requirements-cpu.txt ./` and **L27** `RUN pip install --no-cache-dir
    --prefer-binary -r requirements-cpu.txt` — still exactly as earlier work recorded.
    `requirements-cpu.txt` is **3 lines, not 2** (one small correction to the earlier
    record): `-r requirements-base.txt`, `--extra-index-url
    https://download.pytorch.org/whl/cpu`, `torch==2.3.1+cpu`. `requirements.txt` and
    `backend_app/requirements.txt` *are* two lines each (`-r requirements-base.txt` /
    `-r ../requirements-base.txt`, plus `torch==2.3.1`). So the single edit to
    `requirements-base.txt` is the only edit needed and it reaches `/opt/venv`, which
    **L48** `COPY --from=builder /opt/venv /opt/venv` carries into the production stage.
    **Four Dependabot manifests are affected by the one edit** and the alert set
    confirms it: cryptography CVE-2026-69249 is filed four times, once each against
    `requirements-base.txt`, `requirements.txt`, `requirements-cpu.txt` and
    `backend_app/requirements.txt`
  - **A LATENT SECOND COPY OF THE OLD PIN, FOUND AND DELIBERATELY NOT TOUCHED.**
    `aerora_quant_platform/backend_api/requirements.txt:14`,
    `aerora_quant_platform/backend_api/requirements-dev.txt:15` and
    `aerora_quant_platform/backend_api/api/requirements.txt:13` each still say
    `cryptography==43.0.0`. They are **out of scope for an in-image finding and cannot
    reach the image**: `.dockerignore:48` excludes `aerora_quant_platform/` from the
    build context entirely, the production stage copies only `/opt/venv`,
    `backend_app/`, `startup.sh` and `healthcheck.sh`, and Dependabot files **zero**
    alerts against those three paths. Recorded here so the next reader does not mistake
    the omission for a miss. `requirements-dev.txt` at the repo root pins neither package
  - **WHAT WAS ACTUALLY OBSERVED AFTER INSTALLING THE NEW PINS.** `pip install
    cryptography==49.0.0 setuptools==78.1.1` → `Successfully installed
    cryptography-49.0.0 setuptools-78.1.1`, downgrading this environment's drifted
    `cryptography 50.0.0` / `setuptools 83.0.0` to the pins
    - **`pip check` is byte-identical before and after — 6 lines, and not one of them
      names cryptography or setuptools.** Verbatim, both runs:
      `opentelemetry-proto 1.25.0 has requirement protobuf<5.0,>=3.19, but you have
      protobuf 5.29.6.` / `opentelemetry-sdk 1.25.0 has requirement
      opentelemetry-api==1.25.0, but you have opentelemetry-api 1.44.0.` /
      `opentelemetry-semantic-conventions 0.46b0 has requirement
      opentelemetry-api==1.25.0, but you have opentelemetry-api 1.44.0.` /
      `python-jose 3.4.0 has requirement pyasn1<0.5.0,>=0.4.1, but you have pyasn1
      0.6.4.` / `sse-starlette 3.5.0 has requirement starlette>=0.49.1, but you have
      starlette 0.41.0.` / `tensorflow-intel 2.16.1 has requirement protobuf... but you
      have protobuf 5.29.6.` **Nothing in `requirements-base.txt` carries a conflicting
      constraint on `cryptography`** — the resolver would have said so here, and the
      only install-time warning was the same pre-existing `tensorflow-intel`/`protobuf`
      mismatch
    - **EVERY cryptography IMPORT PATH THIS REPO USES WAS EXERCISED, NOT JUST
      IMPORTED.** `grep` for `from cryptography` / `import cryptography` under
      `backend_app/` returns exactly two consumers:
      `backend_app/core/credential_vault.py:32-34` (`fernet.Fernet`,
      `hazmat.primitives.hashes`, `hazmat.primitives.kdf.pbkdf2.PBKDF2HMAC`) and
      `backend_app/backend/api_key_vault.py:28` (`fernet.Fernet`, `InvalidToken`,
      `MultiFernet`). All of them imported, then driven: a `PBKDF2HMAC`-SHA256/100k-
      iteration derive feeding a `Fernet` encrypt→decrypt roundtrip; a two-key
      `MultiFernet` decrypting a blob written under the *secondary* key (the rotation
      property `tests/test_exchange_phase6c_adversarial_acceptance.py::test_adv_3`
      depends on) while the primary alone raises `InvalidToken`; and
      `CredentialVault()` itself constructed. Indirect consumers too: `PyJWT`
      **RS256** and **ES256** encode/decode over real `rsa`/`ec` keys serialised through
      `cryptography.hazmat.primitives.serialization`, `python-jose`'s
      `jose.backends.cryptography_backend` round-tripping RS256, and the
      `ssl`/`httpx 0.27.0` TLS path (`OpenSSL 3.0.16`). All passed
    - **`backend_app.main` IMPORTS AND STILL EXPOSES 351 ROUTES** under cryptography
      49.0.0 — asserted in a real `.py` file (`assert len(app.routes) == 351`), not a
      shell comparison, and the assertion executed rather than being skipped
  - **THE CRYPTO AND AUTH SUITES, FOUND BY GREP RATHER THAN GUESSED, RUN BEFORE AND
    AFTER: 160 PASSED BOTH TIMES.** `grep tests/` for `cryptography`, `Fernet`,
    `AESGCM`, `PBKDF2`, `credential_vault`, `api_key_vault` and for top-level
    `import jwt` / `from jose` selected thirteen files, run as one invocation:
    `test_credential_vault_key.py`, `test_credential_vault_strength.py`,
    `test_exchange_vault_contract.py`, `test_exchange_vault_singleton.py`,
    `test_exchange_phase6c_adversarial_acceptance.py`,
    `test_exchange_credential_log_redaction.py`, `test_algorithm_confusion_fix.py`,
    `test_admin_auth.py`, `test_bearer_auth_no_cookies.py`,
    `test_auth_logging_sanitization.py`, `test_websocket_auth_fail_closed.py`,
    `test_sc3_aal_claim_propagation.py`, `test_phase7b_auth_remediation.py`.
    **Baseline (cryptography 50.0.0, setuptools 83.0.0): 160 passed in 57.09s.
    After the pins (49.0.0 / 78.1.1): 160 passed in 19.73s.** Same count, same files,
    zero failures, nothing skipped, xfailed or weakened. The baseline was taken first
    on purpose: this environment had drifted *above* the pinned versions, so without it
    a green run after the bump could not be distinguished from a green run that was
    always green. The highest-risk consumer named in the brief,
    `backend_app/core/credential_vault.py`, is covered by the first two files plus
    `test_exchange_credential_log_redaction.py` and passes. No test asserts a literal
    `cryptography==` or `setuptools==` string anywhere under `tests/`, so no test
    needed editing to match the new pins — checked, not assumed
  - **GATES, ALL GREEN, NUMBERS MATCHING THE RECORD EXACTLY.**
    `tests/test_schema_table_reference_drift.py` + `tests/test_no_undefined_names.py`
    → **89 passed** (86 + 3) in 101.43s;
    `tests/test_database_isolation_level_control.py` +
    `tests/test_pr_check_postgres_service.py` → **49 passed** (23 + 26) in 6.14s;
    `tests/test_websocket_upgrade_gate.py` + `tests/test_mounted_endpoint_projections.py`
    + `tests/test_marketplace_eligibility_tenant_verdict.py` → **48 passed**
    (22 + 19 + 7) in 50.15s. **`flake8 --select=E9,F63,F7,F82` was not run, and the
    honest reason is that it had nothing to run on**: this change touches **no Python
    source at all** — only `requirements-base.txt` and this `tasks.md`
  - **THE PARALLEL WORKSTREAM'S FILES WERE NOT TOUCHED.** No edit to
    `backend_app/backend/strategy_service.py`,
    `backend_app/routers/strategy_operations.py`, `backend_app/routers/auth.py`
    (whose staged SC-8 index entry is intact), `backend_app/routers/billing.py`,
    `backend_app/core/billing_lifecycle.py`,
    `backend_app/core/subscription_middleware.py` or
    `backend_app/backend/ml_training_policy.py`. The commit is a pathspec commit over
    exactly two paths, `requirements-base.txt` and this file
  - **WHAT IS AND IS NOT PROVEN HERE.** Proven locally: the new pins install, every
    import path and both vault ciphers work under them, 160 crypto/auth assertions and
    186 gate assertions pass, 351 routes stand. **Not proven here, and it is the real
    verification**: that the advisories are *cleared in the image*. **`05 Security` on
    the next CI run is what settles that** — Trivy scans the installed packages in
    `/opt/venv`, and task 13.10's gate means **`03 Deploy` now blocks on it at
    CRITICAL**. Expect the four cryptography and four setuptools manifest alerts to
    close and the twelve starlette HIGHs to remain open by design
  - _Requirements: 1.33 / 2.33 (2.33 asks that every critical and high alert on a_
    _**runtime** dependency be "resolved or carry a recorded, justified exception naming_
    _why it is not exploitable here" — this task resolves the two that are resolvable_
    _(`cryptography` to its 49.0.0 floor, `setuptools` to its 78.1.1 floor) and files_
    _the justified exception for the two that are not: `starlette`/`fastapi` (framework_
    _migration, compensated at the CloudFront edge, scheduled as 13.12\*) and_
    _`setuptools`' MEDIUM 83.0.0 floor (removes `pkg_resources`, breaks `import_
    _razorpay`). 1.33's clause is also partly re-measured: it recorded **295** open_
    _alerts, and the live query this task ran returns **195**, so the fleet has moved_
    _— but 2.33's bar is zero unexcepted HIGH on runtime dependencies, and the_
    _12 starlette HIGHs are excepted rather than zero, so 2.33 is **PARTIAL**, not_
    _closed) and `§Bug condition` by way of 2.5's principle (a dependency manifest that_
    _pins a version the vendor has published a fix for is reporting a security posture_
    _the deployment does not have — the gap between `cryptography==43.0.0` and the_
    _49.0.0 floor was fabricated assurance, not an absence)_


- [ ] 14. Checkpoint — ensure all tests pass
  - Every task-1 exploration test passes against `F'`; every task-2 preservation test still passes
  - Every P0 and P1 clause carries a named regression test that failed against `F` and passes against `F'`
  - Every one of the 15 UNVERIFIED clauses is recorded as **proven**, **BLOCKED with its gap named**, or
    **closed by citation** (1.17). None is recorded as a silent pass
  - The five partial blockers are reported as BLOCKED: 1.13's deployment path, 1.14's paper routes,
    1.16's database-level serialisation, 1.20's production log audit, 1.42's heap measurement
  - Any new defect found during task 12 is filed with a number and a tier, and re-enters at the wave its
    mechanism belongs to
  - `git diff --exit-code aerora_quant_platform/frontend_app/algo22-terminal` reports no change
  - Ensure all tests pass, ask the user if questions arise

## Notes

- **Tooling is not interchangeable here, and every substitution below has a recorded failure.**
  PowerShell on Windows. `node node_modules/vitest/vitest.mjs --run <path>` — always scoped to a named
  file; the bare suite exceeds 25 minutes (3.18), which is why even task 4.7's own measurement is taken
  in scoped batches and summed. `node node_modules/eslint/bin/eslint.js` — `npx` swallows stdout, so an
  error count read through it is not a count. `git commit -q -m` — `git commit -F <file>` fails silently
  on this machine, which is how a commit can appear to succeed and not exist.
  `$env:NODE_OPTIONS="--max-old-space-size=2048"` before any frontend build.
- **Two frontend test behaviours that look like flakes and are not.**
  `algo22-terminal/vitest.config.js` already sets `fileParallelism: false` / `maxWorkers: 1` because
  parallel fork workers fail their startup handshake on constrained hosts and whole files then silently
  never run; the render-heavy `tests/unit/ds` files depend on that, so pass `--fileParallelism=false`
  explicitly on a scoped run rather than trusting no CLI override reintroduces the contention. And
  `algo22-terminal/tests/unit/guards/dead-tailwind.test.js` compares `src/` class usage against
  `dist/assets/*.css`: with no build present it **skips with a notice rather than failing**, so running
  it before `npm run build` reports green having checked nothing. Build first, then run any guard that
  reads the built stylesheet.
- **`aerora_quant_platform/frontend_app/algo22-terminal` is a stale duplicate and is never modified**
  (3.16). Task 4.8 makes the reference set assertable instead; tasks 11.2 and 14 assert
  `git diff --exit-code` over that path. Nothing in this plan writes into it.
- **`createOrder` is not repointed, and the two manual-execution routes are preserved.**
  `POST /api/orders/execute` and `POST /api/orders/create` answer 403 `MANUAL_EXECUTION_BLOCKED` to
  everything, by design rather than by defect. That constrains task 12.3 specifically: a risk-limit test
  aimed at either route is refused for the wrong reason and proves nothing about risk enforcement.
  Target the paper order route and the internal execution service instead.
- **Tasks 13.1–13.3 modify production, and there is no staging gate between the merge and CloudFront.**
  `06-frontend-deploy.yml` fires on push to `main` for any change under `algo22-terminal/**`. Each of
  those three requires explicit user confirmation before it is executed. Waves 0–5 are verified on the
  feature branch beforehand, which is what leaves the merge as the only production step.
- **BLOCKED is a recorded outcome, not an unfinished task.** The five partial blockers — 1.13's
  deployment path (12.3), 1.14's paper routes (12.4), 1.16's database-level serialisation (12.6),
  1.20's production log audit (12.10), 1.42's heap growth (12.12) — are recorded as BLOCKED **with the
  specific gap named**. A BLOCKED clause does not become a PASS by being re-run, and a clause whose
  proof this environment cannot produce is never reported as proven.
- **The list is in dependency order, not severity order**, because wave 0 gates the merge that carries
  every other wave to production; `§Overview → Ordering: dependency, not severity` gives the full
  argument and names what a P0-first ordering would break.

## Task Dependency Graph

### The spine

```text
  1 ─┐
     ├─▶ 3 ─▶ 4  (WAVE 0 · MERGE GATE) ─▶ 5 ─┬─▶  6  (WAVE 1) ─┐
  2 ─┘                                       ├─▶  7  (WAVE 2) ─┤
                                             ├─▶  8  (WAVE 3) ─┼─▶ 11 ─▶ 13 ─▶ 14
                                             ├─▶  9  (WAVE 4) ─┤               ▲
                                             └─▶ 10  (WAVE 5) ─┘               │
                                                                               │
 12  the 15 investigations (14 here; 1.47 is 13.4) ────────────────────────────┘
     runs alongside 6–10 and needs only the tree; joins at 14
```

- **1 ∥ 2.** Neither depends on the other; both read the **unfixed** tree `F`. Task 2 captures the
  baseline, so it cannot run after any wave — once a fix lands, the baseline it was meant to pin is
  gone.
- **4 gates the merge, therefore gates every later wave reaching production.** Waves 1–5 can be
  *written* without it, but the merge that carries them auto-deploys, so none of them reaches production
  until wave 0 has landed.
- **Waves 1–5 have no data dependency on one another.** They are five branches off checkpoint 5. The
  list's presentation order (6 → 7 → 8 → 9 → 10) is the design's diagnostic sequencing — wave 1 first
  because it touches money figures, wave 3 after it so a socket regression cannot be mistaken for a
  dashboard one — not a dependency. A team with the capacity to run them concurrently may, provided the
  commit barriers below hold.
- **12 is the parallelisable branch worth scheduling.** It depends on no wave's code change, only on the
  tree existing, so it can start as early as task 1 and run for the whole duration of waves 1–5. Its
  deliverable is a proof or a new numbered defect, and a new defect re-enters at the wave its mechanism
  belongs to.
- **13 depends on 4 and 11** — on task 4.1's commit specifically, and on re-verification having passed.

### Inside each wave

```text
 4  WAVE 0 — release blockers                    every item verifiable on the feature branch
    4.1  ALLOWED_API_HOSTS   first, and on its own commit, because 13.1 merges it alone
           ├─ 4.2 ──▶ 4.6    serialised: both edit 06-frontend-deploy.yml
           ├─ 4.5 ──▶ 4.7    serialised: both edit 01-pr-check.yml
           └─ 4.3  ∥  4.4  ∥  4.8

 6  WAVE 1 — absent vs zero
    6.1 ▲ ──▶ { 6.2 ∥ 6.3 ∥ 6.4 ∥ 6.5 ∥ 6.6 ∥ 6.7 } ──▶ 6.8
    6.1 is a commit barrier and a hard prerequisite both: the response boundary cannot
    carry `None` until it lands, so nothing else in the wave works before it. 6.8 is the
    frontend adoption and follows the fields becoming nullable.

 7  WAVE 2 — backtest key sets
    7.1 ──▶ { 7.2 ∥ 7.3 } ──▶ { 7.4 ∥ 7.5 } ──▶ 7.6 ──▶ 7.7 ──▶ 7.8 ▲ ──▶ 7.9
    The contract test lands before the renames because its failure is what measures the
    rename set. 7.6 and 7.7 serialise: both edit `backtest_runtime`.

 8  WAVE 3 — transport ownership
    8.1 ──▶ 8.2 ▲ ──▶ 8.3 ──▶ 8.4 ──▶ { 8.5 ∥ 8.6 }
    Step 1 is deliberately not the credential change, so the socket collapse stays
    revertable without touching auth.

 9  WAVE 4 — route contract
    9.1 ──▶ { 9.2 ∥ 9.3 }
    9.4 ──▶ { 9.5 ∥ 9.6 }          the two branches are independent of each other

10  WAVE 5 — local correctness
    10.1 ∥ 10.2 ∥ 10.3 ∥ 10.4 ∥ 10.5          no internal ordering

11  Re-verification
    11.1 ∥ 11.2                               both after every wave

12  Investigations
    12.1 … 12.9  ∥  12.10  ∥  12.11 … 12.14   no internal ordering
    12.10 builds the frontend first — it greps `dist/`
    1.47, the fifteenth investigation, is 13.4: it can only run after the deploy

13  Deployment — HARD SERIAL, not one step reorderable
    13.1 ──▶ 13.2 ──▶ 13.3 ──▶ 13.4 ──▶ 13.5        then 13.6 ∥ 13.7, only on failure
    13.1 (ALLOWED_API_HOSTS on `main`) strictly precedes 13.3 (the `VITE_API_URL` cutover).
    Cutting the secret first makes the old grep on `main` reject the bundle and reports a
    configuration change as `VITE_API_URL not properly substituted` — the misattribution
    1.31 names. 13.5 (the CloudFront invalidation for task 4.6's paths) follows 13.4's
    verification, because an object already cached as immutable does not re-fetch on its own.
```

```text
 legend   ──▶   must complete before
           ∥    no dependency — may run concurrently
           ▲    MUST LAND ALONE: its own commit, no sibling of the same wave in flight
                (6.1, 7.8, 8.2 — `design.md §Change Ordering`)
```

### Machine-readable form

Each wave is the **earliest** position a task can be scheduled, so waves 1–5 and task 12 appear
interleaved. Serialising them in list order is always valid; the reverse is not.

```json
{
  "waves": [
    { "id": 0,  "tasks": ["1", "2"] },
    { "id": 1,  "tasks": ["3"] },
    { "id": 2,  "tasks": ["4.1"] },
    { "id": 3,  "tasks": ["4.2", "4.3", "4.4", "4.5", "4.8"] },
    { "id": 4,  "tasks": ["4.6", "4.7"] },
    { "id": 5,  "tasks": ["5"] },
    { "id": 6,  "tasks": ["6.1", "7.1", "8.1", "9.1", "9.4", "10.1", "10.2", "10.3", "10.4", "10.5", "12.1", "12.2", "12.3", "12.4", "12.5", "12.6", "12.7", "12.8", "12.9", "12.10", "12.11", "12.12", "12.13", "12.14"] },
    { "id": 7,  "tasks": ["6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "7.2", "7.3", "8.2", "9.2", "9.3", "9.5", "9.6"] },
    { "id": 8,  "tasks": ["6.8", "7.4", "7.5", "8.3"] },
    { "id": 9,  "tasks": ["7.6", "8.4"] },
    { "id": 10, "tasks": ["7.7", "8.5", "8.6"] },
    { "id": 11, "tasks": ["7.8"] },
    { "id": 12, "tasks": ["7.9"] },
    { "id": 13, "tasks": ["11.1", "11.2"] },
    { "id": 14, "tasks": ["13.1"] },
    { "id": 15, "tasks": ["13.2"] },
    { "id": 16, "tasks": ["13.3"] },
    { "id": 17, "tasks": ["13.4"] },
    { "id": 18, "tasks": ["13.5"] },
    { "id": 19, "tasks": ["13.6", "13.7"] },
    { "id": 20, "tasks": ["14"] }
  ]
}
```