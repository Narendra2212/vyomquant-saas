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

- [ ] 4. Wave 0 — Release blockers. Gates the merge, therefore gates everything

  Nothing in waves 1–5 reaches production until this wave lands, because the merge to `main` that
  carries them auto-fires `06-frontend-deploy.yml` to S3 and CloudFront with no staging gate. Wave 0
  is the smallest set that makes that merge safe. Every item is a repository or workflow change and
  none of them needs the deploy to run, so the whole wave is verifiable on the feature branch.

  - [ ] 4.1 Commit the `ALLOWED_API_HOSTS` allow-list
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

  - [ ] 4.2 Delete the three fake installers and add the `releases/` size gate
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

  - [ ] 4.3 Withdraw the download surface through the existing not-available convention
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

  - [ ] 4.4 Commit the reviewed working tree
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

  - [ ] 4.5 Run ESLint in CI and drive errors to zero
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

  - [ ] 4.6 Narrow the immutable cache policy to content-hashed paths
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

  - [ ] 4.7 Re-measure the `01-pr-check.yml` ceiling against the suite it actually guards
    - **File:** `.github/workflows/01-pr-check.yml:132`
    - `timeout-minutes: 40` and its justification comment were written for a **53-file** frontend
      suite. That suite is now **131 files**, run sequentially by design. The sharper finding than
      1.41's own wording: 1.41 cites "494 test files" which is repo-wide (484 tracked, by
      measurement), while the comment it criticises sizes the *frontend* suite specifically
    - Record the observed wall clock of the 131-file frontend suite in the comment, with the file count
      it was measured at, and set the ceiling from that measurement rather than keeping 40
    - Measure by running the frontend files scoped in batches with
      `node node_modules/vitest/vitest.mjs --run <path>`, summing the observed durations. **Do not run
      the bare full suite to obtain the number** — it exceeds 25 minutes and 3.18 forbids it
    - **Regression test:** the measured duration and test count recorded in the workflow comment
    - _Bug_Condition: isBugCondition(X) arm 5 — a release gate sized against a tree that no longer exists_
    - _Expected_Behavior: expectedBehavior(result) — the ceiling is justified by a stated measurement at a stated test count_
    - _Preservation: 3.18 — the suite is still invoked scoped, never bare_
    - _Requirements: 1.41, 2.41, 3.18_

  - [ ] 4.8 Mark the stale duplicate frontend dead
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

- [ ] 5. Checkpoint — wave 0 verified on the feature branch
  - `pytest tests/test_migration_tooling.py tests/test_cors_configuration.py tests/test_release_artifacts.py` passes
  - `node node_modules/eslint/bin/eslint.js src` reports zero errors
  - The `releases/` size gate has been observed **failing** against a deliberately undersized file
  - `git status` is clean apart from the intentionally untracked `releases/` and `dist/` paths
  - Ensure all tests pass, ask the user if questions arise

- [ ] 6. Wave 1 — Absent vs zero. One representation, ten sites

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

  - [ ] 6.1 **MUST LAND ALONE** — the composer stops coercing
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

  - [ ] 6.2 Remove the `100000.0` defaults from `get_portfolio_overview`'s paper branch
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

  - [ ] 6.3 Replace the composer's environment-dependent exception literal
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

  - [ ] 6.4 Delete `get_equity_curve`'s paper synthesis branch
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

  - [ ] 6.5 Derive exchange health from a probe, and `can_trade` from credential validity
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
    - **Regression test:** `tests/test_dashboard_absent_figures.py::test_recent_signals_carry_strategy`
      — the field is present and non-null for a signal from a known strategy. (Not from
      `design.md §Fix Checking`, which names files for P0 and P1 only)
    - _Bug_Condition: isBugCondition(X) arm 1 — the response omits a fact the query could have carried_
    - _Expected_Behavior: expectedBehavior(result) — the strategy id and name are present, or explicitly null_
    - _Preservation: 3.9 — the route's existing ownership predicate and rate limit are unchanged_
    - _Requirements: 1.36, 2.36, 3.9_

  - [ ] 6.7 Confirm the route disposition in `routers/dashboard.py`
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

  - [ ] 6.8 Adopt the null figures on the frontend
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

- [ ] 7. Wave 2 — Backtest key sets. One contract, three boundaries

  **Files:** `backend_app/backend/backtesting_engine.py`, `backend_app/backend/backtest_runtime.py`,
  `backend_app/backend/backtest_service.py`.

  Landing order within the wave matters: **the contract test is written first and is expected to
  fail**, because it is what tells us the rename set is complete. Four vocabularies cross three
  boundaries — engine keys, runtime keys, VectorBT *display* names, DB column names — with no
  validation at any of them and `dict.get(key, 0)` at the last one.

  - [ ] 7.1 Declare the payload's key set once — the shared fixture
    - Add a module-level frozenset of emitted keys beside the engine, and derive the writer's read-key
      set from it. Without this the contract test is a second hand-maintained list, which is the defect
      one layer up
    - Emit `tests/fixtures/backtest_payload_keys.json` from that declaration. **One file, read by both
      sides:** pytest reads it directly, vitest reads it through `node:fs` at a repo-relative path. A
      per-side copy could drift, which is what the fixture exists to prevent
    - The backend test asserts the JSON matches the Python declaration, so the declaration stays the
      single source and the JSON cannot go stale
    - _Requirements: 2.7, 2.8_

  - [ ] 7.2 Backend side of the key-set contract
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

  - [ ] 7.3 Frontend side of the key-set contract
    - Create `algo22-terminal/tests/unit/pages/backtesterNetPnl.test.jsx`, reading
      `tests/fixtures/backtest_payload_keys.json` — **the same file** task 7.2 asserts against
    - **Regression test and assertion:** the rendered Net P&L field is present and numeric for a
      completed backtest; every figure the results surface renders is named in the fixture's key set,
      so a column the writer cannot persist cannot be rendered
    - Run scoped: `node node_modules/vitest/vitest.mjs --run tests/unit/pages/backtesterNetPnl.test.jsx`
    - **Why both sides:** a backend-only key-set test passes while the results screen still renders a
      field nothing persists, and a frontend-only test passes while the column stores `0`
    - _Requirements: 1.10, 2.10, 3.7_

  - [ ] 7.4 Emit `total_pnl` from the engine
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

  - [ ] 7.5 Move the equity curve in band
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

  - [ ] 7.6 Stop reading VectorBT display names in `backtest_runtime`
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

  - [ ] 7.7 Fix the `{**stats, **performance_metrics}` precedence
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

  - [ ] 7.8 **MUST LAND ALONE** — the writer's key mapping
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

  - [ ] 7.9 Confirm `update_backtest_results` keeps its ownership behaviour
    - **Nothing in this wave touches the `user_id` predicate.** It was added because its absence made
      this a cross-tenant write and an existence oracle, and it is the precedent task 12.4's sweep
      generalises
    - Still scoped by `user_id` as well as `id`; still returns `{}` indistinguishably for a non-owned
      and a nonexistent id; still degrades gracefully when `006_backtest_evidence_columns.sql` is
      unapplied
    - **Regression test:** the existing ownership tests, re-run scoped and green
    - _Preservation: 3.5_
    - _Requirements: 3.5_

- [ ] 8. Wave 3 — Transport ownership. One socket, one credential

  **Files:** `algo22-terminal/src/websocketClient.js`, `algo22-terminal/src/pages/Billing.jsx`,
  `algo22-terminal/src/contexts/DataPipelineContext.jsx`, `backend_app/api_ws/ws_routes.py`, plus a
  new ticket route.

  **This is an auth change and it is the riskiest wave in the pass.** It alters how every socket in
  the application authenticates, across the nine routes in `ws_routes.py` — `/ws/telemetry`,
  `/ws/ticker/{symbol}`, `/ws/orderbook/{symbol}`, `/ws/candles/{symbol}/{timeframe}`,
  `/ws/user/{user_id}`, `/ws/pnl/{user_id}`, `/ws/dashboard`, `/ws/strategy/{strategy_id}`,
  `/ws/signal-trace` — each of which takes `token: str = Query(...)`. The sequencing below exists to
  make each step independently revertable, and **step 1 is deliberately not the credential change**.

  - [ ] 8.1 Collapse to one socket, with no auth change
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

  - [-] 8.2 **MUST LAND ALONE** — replace the socket credential with a single-use ticket across nine routes
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

  - [ ] 8.3 Unify the token store
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

  - [ ] 8.4 Declare the `STRATEGY_STATUS` frame in one shared fixture
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

  - [ ] 8.5 Backend side — publish `STRATEGY_STATUS` from the start and stop service paths
    - Publish from the strategy start/stop service paths, with the frame shape read from
      `tests/fixtures/strategy_status_frame.json`. The only occurrence in the tree today is a docstring
      at `api_ws/ws_routes.py:1050` describing a call no service makes
    - **Regression test:** `tests/test_strategy_status_publish.py` — one start and one stop each publish
      **exactly one** frame whose `type` is the string the frontend subscribes to
    - _Bug_Condition: isBugCondition(X) — the frontend's status contract is structurally satisfied and operationally dead_
    - _Expected_Behavior: expectedBehavior(result) — exactly one frame per transition, over the one socket_
    - _Preservation: 3.9 — no change to authentication or to the routes' rate limits_
    - _Requirements: 1.24, 2.24_

  - [ ] 8.6 Frontend side — reflect the published frame
    - **Extend** the existing `algo22-terminal/tests/unit/liveTrading/deploymentState.test.jsx`, reading
      **the same** `tests/fixtures/strategy_status_frame.json`
    - **Regression test and assertion:** the frontend updates its strategy status from that fixture
      frame, and does **not** update from a `dashboard_update`-wrapped variant — which pins the envelope
      as well as the channel name
    - Run scoped:
      `node node_modules/vitest/vitest.mjs --run tests/unit/liveTrading/deploymentState.test.jsx`
    - **Why both sides:** 1.24's history is precisely a contract that a one-sided test would have passed
    - _Requirements: 1.24, 2.24, 3.7_

- [ ] 9. Wave 4 — Route contract. Make the mismatch impossible, then fix the four instances

  **Files:** `algo22-terminal/src/contexts/{IndicatorEngine,LogicEngine,StrategyEngine}Context.jsx`,
  `algo22-terminal/src/contexts/CopilotContext.jsx` (delete),
  `backend_app/backend/signal_trace_engine.py`.

  Frontend call paths and backend routes are never checked against each other in either direction, and
  three of the four broken calls also reference an identifier that is not imported. Wave 0d (ESLint in
  CI) is the other half of this wave's durability: `no-undef` is what catches the unimported `post`.

  - [ ] 9.1 Add the general route-contract check first
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

  - [ ] 9.2 Resolve the three engine contexts
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

  - [ ] 9.3 Delete `CopilotContext`
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

  - [ ] 10.5 Triage the 295 Dependabot alerts
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

- [ ] 12. The 15 UNVERIFIED clauses — investigations, not fixes

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

  - [ ] 12.1 1.11 — prove order idempotency under duplicate submission
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
    - _Requirements: 1.11, 2.11_

  - [ ] 12.2 1.12 — prove paper–live separation
    - Create `tests/test_paper_live_separation.py`: substitute a **failing double** for the live venue
      adapter that raises if constructed, and assert it never raises across every paper signal path
    - Code-level, no venue needed
    - **Provable here: yes**
    - _Requirements: 1.12, 2.12_

  - [ ] 12.3 1.13 — prove server-side risk enforcement **[PARTIAL BLOCKER: the deployment path]**
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
    - _Requirements: 1.13, 2.13_

  - [ ] 12.4 1.14 — **extend** the cross-tenant ownership matrix **[PARTIAL BLOCKER: the paper routes]**
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
    - _Requirements: 1.14, 2.14_

  - [ ] 12.5 1.15 — document the tenant boundary per table
    - Produce a schema audit checked into `.kiro/specs/production-launch-hardening/`: for each table
      carrying two tenants' rows, record the **predicate**, **row-level security**, **foreign key** and
      **index** on every access path
    - Add a test asserting each shared table's access paths carry the tenant predicate
    - **Every gap found is filed as a numbered P0**, not as a note
    - The migrations are in the tree and readable. **Provable here: yes**
    - _Requirements: 1.15, 2.15_

  - [ ] 12.6 1.16 — prove concurrent strategy lifecycle serialisation **[PARTIAL BLOCKER: database-level serialisation]**
    - Create `tests/test_strategy_lifecycle_concurrency.py`: fire start, stop, delete and deploy **in
      parallel** against one strategy id, repeated enough times to expose interleaving, and assert the
      terminal state is one of the legal states — **no running-but-deleted, no double deploy**
    - **BLOCKED, gap named: a real Postgres is needed to establish that the *database's* serialisation
      holds, not just the application's.** Provable against the sandbox; the database-level guarantee is
      recorded as BLOCKED with that gap
    - **Provable here: partly**
    - _Requirements: 1.16, 2.16_

  - [ ] 12.7 1.17 — close by citation, and record the scope
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
    - _Requirements: 1.17, 2.17_

  - [ ] 12.8 1.18 — prove entitlement is withdrawn on lapse
    - Create `tests/test_entitlement_matrix.py`: subscription states × protected operations, asserting
      **each cell's wire code** against the four existing codes — `MARKETPLACE_NOT_SUBSCRIBED`,
      `MARKETPLACE_SUBSCRIPTION_EXPIRED`, `MARKETPLACE_STRATEGY_UNAVAILABLE`,
      `MARKETPLACE_OPERATION_NOT_PERMITTED`
    - Assert **`MARKETPLACE_READ_FAILED` (503) is not folded in**, so a paying subscriber is never told
      they hold no subscription because a read broke (3.11). Wire codes, not status classes
    - `tests/property/test_subscription_state_machine.py` and `test_entitlement_expiry_boundary.py`
      already model the states and are the starting point
    - **Provable here: yes**
    - _Requirements: 1.18, 2.18, 3.11_

  - [ ] 12.9 1.19 — **extend** the protected-logic containment property to traces and exports
    - **EXTEND `tests/property/test_protected_logic_containment.py`. Do not write a second suite**
    - Add the **trace and export surfaces**: assert the serialised response for a non-entitled caller
      contains **none** of the node types, parameters or expressions of the underlying DAG — not in the
      body, not in an error message, not in a trace, not in a DAG export
    - **Provable here: yes**
    - _Requirements: 1.19, 2.19_

  - [ ] 12.10 1.20 — prove no secret in the bundle or the logs **[PARTIAL BLOCKER: the production log audit]**
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
    - _Requirements: 1.20, 2.20_

  - [ ] 12.11 1.39 — prove 422 not 500 on malformed input
    - Create `tests/test_validation_sweep.py`: submit malformed bodies to **every mutating route**,
      asserting **no 500** and no `err.message`-style leakage, and a machine-readable code on each 422
    - `TestClient` over the real app — the same technique `test_cross_tenant_ownership_matrix.py` already
      uses. The frontend half is already guaranteed by `design/errorCopy.js`; this extends the guarantee
      server-side
    - **Provable here: yes**
    - _Requirements: 1.39, 2.39, 3.8_

  - [ ]* 12.12 1.42 — measure performance **[PARTIAL BLOCKER: heap growth needs a browser]**
    - **Extend** `tests/perf/test_market_data_latency.py` and `tests/perf/test_strategy_builder_budgets.py`
      rather than adding a third harness. Record **query counts, polling intervals and render counts**
    - **The requirement is a measurement, not a number.** Absolute performance targets are out of scope;
      any regression against the redesign baseline is filed
    - **BLOCKED, gap named: heap growth over a long session needs a browser.** `design.md` records this as
      a **manual measurement**, taken in a browser and recorded as such — the one measurement in this
      plan that is not automatable here. It is recorded with the method used, not asserted
    - **Optional for launch: P2**
    - **Provable here: partly — query and render counts yes, heap growth manual**
    - _Requirements: 1.42, 2.42_

  - [ ]* 12.13 1.43 — prove loading then recoverable failure on every surface
    - Create `algo22-terminal/tests/unit/pages/degradedSurfaces.test.jsx`: delayed and rejected fetches
      across the primary surfaces, asserting **neither an empty frame nor a permanent skeleton**
    - `usePanelState` makes this cheap: its eight states are already the vocabulary, and
      `STATES_WITHOUT_CHILDREN` already encodes which must not render children
    - Run scoped:
      `node node_modules/vitest/vitest.mjs --run tests/unit/pages/degradedSurfaces.test.jsx`
    - **Optional for launch: P2**
    - **Provable here: yes**
    - _Requirements: 1.43, 2.43_

  - [ ]* 12.14 1.44 — re-verify the responsive layout against this pass
    - **Re-run the existing responsive test files, scoped, never the full suite** (3.18). No new suite —
      the redesign's files are the instrument; this task establishes they still pass against `F'`
    - This is a test re-run, not a manual QA pass
    - **Optional for launch: P2**
    - **Provable here: yes**
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
