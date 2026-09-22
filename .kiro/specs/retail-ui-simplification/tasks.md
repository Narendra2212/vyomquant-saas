# Implementation Plan

## Overview

The instruction this plan serves, quoted because every task below is answerable to it:

> "remove all unneeded text in the UI make it minimalistic without losing any feature"
>
> "try to make it simple as much as possible for the user"

The audience is a retail crypto trader, not a quant engineer. **Simplicity is the objective;
preservation is the constraint.** Both, not one. A plan that serves only the first ships a screen
that hides a failed read behind a plausible number. A plan that serves only the second ships the
tree exactly as it is.

`design.md` already resolved the central tension, and the resolution is what makes this plan
executable: **the verbose prose is TRUE and LOAD-BEARING.** `LiveTrading.jsx:1324`'s
`REGISTRY_CAVEAT` — 384 characters across four sentences — warns that the deployment list can omit
a strategy that is actually running, because the endpoint reports what one backend process holds in
memory and a deployment started by an earlier worker or before a restart is absent from it. A
trader who reads that list as complete can double-deploy. So the remedy in every task below is
**progressive disclosure, not deletion**: the text stays reachable, it stops standing on the page,
and every distinction it draws survives the move.

### The three causes, and what each remedy may touch

`requirements.md`'s central claim, restated as work. The expensive failure mode of this pass is
treating the three as one problem (`design.md §1.1`).

| # | Cause | Shape of the remedy | Tasks |
| --- | --- | --- | --- |
| 1 | **Typography was never centralised.** Colour was ratcheted to zero on eleven pages and held there; font size was left to each page's author. 451 absolute sizes across 14 files, in four syntaxes. | Mechanical. Adopt the seven steps that already exist; add the guard nobody wrote. Touches style objects and class strings. **No copy, no structure.** | 1, 3, 8, 11 |
| 2 | **Eleven of twenty-two pages never adopted the convention**, and are outside the a11y ratchet entirely. Marketplace is a twelfth case — inside the enforced set, styled from the previous generation. | Migration, not redesign. Adopt `usePanelState`, `pageFields`, `ds/*`, `errorCopy`. **Add nothing.** | 4, 5, 6, 7 |
| 3 | **The adopted pages are consistent, verbose and dense.** Live Trading's standing prose, the badge's four stacked treatments, Paper Trading's explanations below the legibility floor. | Copy and hierarchy. Move prose behind disclosure; keep every distinction. **Touches no figure, no reason, no confirmation.** | 2, 12 |

### Commit count and ordering

The task order **is** `design.md §4.3`'s commit table. Nothing is reordered; the seven new test
artefacts are *inserted* ahead of the change each guards, which is exactly what Decision D2
authorises ("Requirement 22's ordering is adjusted only by insertion, never by reordering").

| §4.3 # | Commit | Task | Alone? |
| --- | --- | --- | --- |
| 1 | The font-size ratchet, seeded | 1.1 → 1.2 | **Yes** |
| 2 | Badge treatment + the three label declarations | 2.2, guarded by 2.1 | **Yes** |
| 3 | `ds/Chart.jsx` tick and axis-label sizes | 3.2, guarded by 3.1 | **Yes** |
| 4 | Accessibility scope extension + D7's derived total | 4.1 | **Yes** |
| 5–6 | Marketplace visual generation + the four a11y findings | 5.2, 5.3, guarded by 5.1 | 12 lands with 10 |
| 7 | Marketplace read path | 6.1 | **Yes** |
| 8–18 | The eleven unmigrated pages, in Requirement 4.5's order | 7.2–7.11 (ten), guarded by 7.1; the eleventh is `Landing.jsx`, which Requirement 4.5 routes to Requirement 14 → task 9.2 | **Yes, each** |
| 19–21 | `PaperTrading`, `StrategyBuilder`, `StrategyDetail` (Req 4.6) | 8.1, 8.2, 8.3 | **Yes, each** |
| 22–23 | Dead code (Req 14) | 9.1, 9.2 | **Yes** (Req 14.3) |
| 24+ | Landing surface token migration (Req 15), gated on Req 13 | 11.1, 11.2, 11.3 | per file |
| last | Standing prose and fresh-account hierarchy (Req 7, 8) | 12.3–12.7, guarded by 12.1, 12.2 | **Yes, each** |
| — | The landing investigation (§8), parallel from day one, no page diff | 10.1, 10.2, 10.3 | n/a |

§4.1 counts the pass at **at least twenty-five commits**: 23 numbered before the landing surface,
plus up to 14 landing-file commits under Requirement 15, plus the five closing prose and hierarchy
commits. Every one is shippable on its own (Requirement 22.1) and lowers whichever budget it
cleared in the same commit (Requirement 22.2).

### Hard constraints — the non-negotiables every task carries

These are P0s. A task that satisfies its own clause and breaks one of these is reverted, not merged
with a note. Each relevant task repeats the subset that bears on it; the full set is here once.

- **`design/pageFields.js`'s `reason` strings may NOT be shortened, deleted, or replaced by one
  generic sentence** (Requirement 19.3, `design.md §6`). A simplification pass's instinct is to read
  them as clutter: they are sentences, they are often the longest strings on a panel, they render in
  the state a trader visits least, and §2.4's own procedure sends every one of them *up* to
  `--text-body`. The canonical case is declared in the tree: `pageFields.js`'s
  `exchangeApiLatencyMs` entry (path `health.exchange_api_latency_ms`, `absence: UNMEASURABLE`)
  carries the note, at `:415`, that **it must never render as `0 ms`** — a zero-latency exchange call
  is not a thing, so the figure would be read as a claim about a working connection at the exact
  moment nothing has been measured. `risk.open_positions_count` fails the same way for the same
  reason: a count of zero is the safest-looking figure a broken positions read could publish.
- **A genuine `0.0` still renders `0.0`. An unavailable figure still renders the marker WITH its
  reason.** The marker says *that* a figure is missing; the reason says *why*. `pageFields.js` states
  the point itself: "not available" with no reason tells a trader exactly as much as a blank cell.
- **The reviewer's test, from §6.2, applied to every string in every diff:** remove it — can a
  trader still tell this figure apart from a figure that is genuinely zero, and still tell why? If
  no, the string is load-bearing and the edit is a Requirement 19.3 violation. Mechanically: a string
  reachable from a `pageFields.js` entry's `reason` or from `usePanelState`'s `unavailable` branch is
  untouchable; everything else is Requirement 7's.
- **No feature, control, route, request, or confirmation is removed.** Minimalism here is weight and
  placement, never capability (Requirements 16.2–16.5, out-of-scope item 3). Every
  `ds/ConfirmDialog`, the deploy confirmation, and the live-deployment statement that real orders
  will be placed keep their required explicit action. A confirmation is friction on purpose.
- **Every control keeps its accessible name and its keyboard path** (Requirements 6.4, 20.1–20.3).
  A control that becomes an icon keeps a text alternative; a control that moves behind a disclosure
  stays reachable from the tab order, and the disclosure carries an accessible name and a
  programmatic expanded state. `src/components/ds/**` stays at exactly zero `jsx-a11y` findings, at
  `error` rather than `warn`.
- **No state becomes colour-only and no state becomes nothing** (Requirement 20.5). `usePanelState`'s
  `empty`, `unavailable` and `error` are three different facts about the trader's account and are
  never collapsed in pursuit of fewer states on screen (Requirement 19.4). Reduced weight is not
  collapse: the state name, its copy and its action all stay.
- **No second design system** (Requirement 17). No new `ds/*` primitive, no new `usePanelState`
  state, no new `src/design/` module, no page-local `C` / `COLORS` / `THEME` object, no eighth type
  step. If a page cannot be built from the existing set, that is a finding to raise and the page
  waits.
- **`api-paths.budget.js` does not change at all** (Requirement 18.3). A diff to it means Requirement
  16.2 was violated. No diff under `src/api/`, `src/websocketClient.js` or `backend_app/`
  (Requirement 16.7).
- **`aerora_quant_platform/frontend_app/algo22-terminal` is never modified** (Requirement 21). §2's
  mapping is exactly the kind of work that produces a codemod, and a codemod is exactly what lands in
  both trees, so the `git diff` path check belongs on every commit rather than on the pass.

### What must land alone

`design.md §4.5`, adopted unchanged. Each of these is its own commit with `git commit -q -m`, so
reverting it reverts nothing else.

| Change | Task | Why alone |
| --- | --- | --- |
| The ratchet seed | 1.2 | It touches no source. Bundled with a page edit, a reader cannot tell whether a number is today's count or the post-edit count, and "seeded from the tree" stops being checkable by re-running the scan. |
| The badge | 2.2 | One file, eight pages, six primitives. Riding with a page makes any regression on the other seven bisect to the wrong commit. |
| `ds/Chart.jsx` | 3.2 | Same, for every chart on eleven pages. |
| The a11y scope extension | 4.1 | It changes lint severity for 25+ files at once. |
| The Marketplace waiver deletion | 5.3 | `eslint-rules/a11y-ratchet.js` is a shared list and Requirement 12.2 requires deletion rather than lowering. Inside a large page diff a reader cannot tell whether the four findings were fixed or the entry was dropped. |
| Requirement 14's deletions | 9.1, 9.2 | Requirement 14.3 says so: confirm by search that nothing imports the file, and keep it separate from any behavioural change. |
| Any `tokens.css` / `design/tokens.js` edit | — (none scheduled; O1 option A would be one) | Requirement 22.3. `tokens.generated.test.js` makes a token edit global; bundled with a page edit, a bisect lands on the page and the cause is app-wide. |
| Each page | 7.2–7.11, 8.1–8.3 | Requirement 22.1. |

### Rules that apply to every task

- **Tooling is not interchangeable, and every substitution below has a recorded failure.** PowerShell
  on Windows, run from `algo22-terminal/`.
  `node node_modules/vitest/vitest.mjs --run <path> --fileParallelism=false` — always scoped to a
  named file, **never the bare suite**, which exceeds 25 minutes (Requirement 18.5).
  `node node_modules/eslint/bin/eslint.js src` — `npx` swallows stdout, so a count read through it is
  not a count. `--fileParallelism=false` is passed explicitly even though `vitest.config.js` already
  sets it, so that no CLI override reintroduces the contention the render-heavy `tests/unit/ds` files
  depend on avoiding.
- **`git commit -q -m` takes ONE `-m` on a SINGLE LINE.** A multi-line `-m` and `git commit -F <file>`
  both fail silently on this machine, which is how a commit can appear to succeed and not exist.
- **Every commit ends with two path checks**, not one: `git diff --name-only` shows no path under
  `src/api/`, `src/websocketClient.js`, `backend_app/` or
  `aerora_quant_platform/frontend_app/algo22-terminal`, and `api-paths.budget.js` is unchanged.
- **A failing guard is never skipped, widened, or deferred** (Requirement 18.6). Where a count moved
  in the intended direction, the budget is lowered in the same commit.
- **Every task names the requirement clauses it satisfies** in a `_Requirements:_` trailer, and names
  its regression test plus the assertion that must fail before the change and pass after.
- **Tasks marked `*` are optional for launch:** their driving clause is P2 or P3 on
  `requirements.md`'s severity scale. Optional does **not** mean reorderable — if a marked task lands
  at all, it lands at its §4.3 position, because the ordering argument is about bisect and
  work-in-flight, not about severity. Every unmarked task is required.
- **§2.5's three-part proxy stands in for every "did it overflow?" question**, because this pass has
  no build and jsdom has no geometry: the file's ratchet count decreases, the diff adds no
  `fontSize` / `text-[…px]` / fixed px `width`|`height` on a text container, and the page's own tests
  plus `tests/unit/ds/` stay green. The proxy cannot see a clipped chip. It can see every way of
  faking the fix.

## Tasks

- [ ] 1. Commit 1 — the font-size ratchet, seeded (Requirement 1.3, 1.4, 1.5)

  **Files:** `tests/unit/guards/absolute-font-sizes.test.js` (new),
  `tests/unit/guards/absolute-font-sizes.budget.js` (new). No source file.

  This is commit one because the interval is what needs protecting, not the endpoint: the pass clears
  fourteen files and migrates eleven more, and every commit in between touches JSX that could grow a
  `fontSize`. Colour had a ratchet and reached zero on eleven pages; font size had a convention and
  reached 451. The mechanism that worked is the one to copy — §3 mirrors
  `no-colour-literals.{budget,test}.js` line for line, on the same `source-scan.js` helper.

  - [ ] 1.1 Write the detection and the self-tests
    - Create `tests/unit/guards/absolute-font-sizes.test.js` importing `SRC`, `collect`, `isTestFile`,
      `list`, `relToSrc`, `stripComments` from `./source-scan.js`. **Not `maskStrings`, not `codeOnly`**
      — `text-[10px]` lives inside a `className` string, so masking strings would blind the guard to
      the entire Tailwind syntax, exactly as `no-colour-literals` leaves strings intact for `"#ef4444"`
    - `SCAN_ROOTS = ['pages', 'components', 'lib']` and `SCANNED_EXTENSIONS = ['.js', '.jsx', '.ts',
      '.tsx', '.css']`, the colour guard's. Import `TOKEN_LAYER_FILES` from the colour budget rather
      than re-declaring it: a file whose job is to declare font sizes cannot be policed by a rule that
      forbids them
    - Four patterns, §3.2 verbatim — `FONT_SIZE_NUMERIC`, `FONT_SIZE_QUOTED`, `TAILWIND_ARBITRARY`,
      `FONT_SIZE_PROP`. The fourth exists because `PaperTrading.jsx` holds 8 `fontSize={9}` recharts
      axis props that none of Requirement 1.2's three named syntaxes reaches. Keep the
      `(?<![\w$])` lookbehind (it stops `baseFontSize:` counting; `\b` does not fire between `e` and
      `F` and only looks correct) and the `(?![\d.])` lookahead (a fractional px reports as uncounted
      rather than mis-parsed)
    - Capture the digits, not just the count, so the failure message names the distribution:
      `PaperTrading.jsx — 51 absolute sizes (9×31, 11×10, 10×8, 8×1, 16×1)`. §2's procedure takes the
      number as evidence about the role, so a message carrying the distribution shortens the work
    - Carry `lineComments: path.extname(full) !== '.css'` verbatim — `//` is not a comment in CSS and
      `text-[10px]` can appear in an `@apply`
    - Write §3.3's five self-tests exactly as specified: a size named in a comment is ignored; a size on
      a line that also carries a comment still counts; an apostrophe in JSX text does not swallow the
      file; a relative or tokenised size does not count; a neighbouring numeric style property
      (`lineHeight: 1.2`, `letterSpacing: 2`, `borderRadius: 12`, `size={9}`, `const baseFontSize = 11`)
      does not count
    - **Comment blanking is not optional here and this is the reason:** §2.4 requires every migrated
      call site to carry `// fontSize: 9 → --text-body (sentence)`. Without `stripComments`, a file
      cleared correctly and documented as Requirement 2.4 demands would measure *the same count it
      started with*, and the only way to pass would be to delete the record. `ds/SectionHeader.jsx`
      already proves it: 2 before stripping, 0 after, because `:17`'s docblock records the values
      `PanelTitle` used to hardcode
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`
    - **Regression test and assertion:** the five self-tests in this file. **Fails before:** with any one
      of the four patterns deleted or its lookbehind widened to `\b`, a self-test fails — which is the
      point, since a silently broken pattern makes the whole guard pass vacuously and the seeded numbers
      drift into fiction. **Passes after:** all five green against the real `source-scan.js`
    - _Requirements: 1.2, 1.3, 18.1_

  - [ ] 1.2 **MUST LAND ALONE** — seed the budget and switch on the six assertions **[BLOCKED: OPEN DECISION O1]**
    - Create `tests/unit/guards/absolute-font-sizes.budget.js` by *running* 1.1's patterns over the
      three roots and writing down what they measure. Requirement 1.4's 18 seeded entries, **plus
      §3.5's two corrections**: `PaperTrading.jsx` is **51, not 43** (the eight axis props), and eleven
      files under `components/` carry **155** sizes with no entry — `SupportCenter` 69,
      `DeploymentConsole` 19, `NotificationCenter` 17, `ResearchConsole` 17, `common/primitives` 12,
      `FirstTradeWizard` 7, `DeployPreflightPanel` 5, `waitlist/WaitlistForm` 5, **`ds/Chart` 2**,
      `download/DownloadPage` 1, `admin/AdminDashboard` 1
    - Without those eleven entries, `accounts for every file that still carries an absolute size` fails
      on the guard's own first commit, which Requirement 22.4 requires to be green. Narrowing
      `SCAN_ROOTS` to match the 18-entry seed instead would leave `ds/Chart.jsx:722` permanently
      invisible — one declaration that sets the axis tick size on every chart on all eleven migrated
      pages — which is the same asymmetry §1.2 diagnoses as the cause of this whole requirement
    - Write §3.4's six assertions: `is well formed`; `names only files that still exist`; `accounts for
      every file that still carries an absolute size`; `holds every file at or below its budget`;
      `requires progress to be recorded, not banked`; `has an entry for every file it claims to track,
      and no strays`. Asserted **exactly, in both directions** — under a `<=` assertion a contributor
      could clear forty sizes, leave the budget high, and the next contributor could put forty back
      without CI noticing
    - Implement Requirement 1.5's inversion: **an entry that reaches zero is deleted, not left at `0`.**
      A cleared file is then held at zero by `accounts for every file…` firing on it as *unbudgeted*.
      The unbudgeted failure message must distinguish the two cases it now covers — a previously cleared
      file where a size has come back (re-adding an entry is not the remedy; express it as a
      `Type_Scale` step) from a genuinely new file carrying debt (seed an entry with the change that
      clears it)
    - Add §10.3's **non-vacuity fixed point** as an assertion rather than a claim: running the four
      patterns over the three roots reproduces every one of §1.1's per-page numbers exactly — `Profile`
      79, `StrategyDetail` 61, `ExchangeManager` 52, `Landing` 41, `Billing` 39, `StrategyMarketplace`
      30, `AuthPage` 29, `Wizard` 24, `RiskSettings` 16, `LegalPage` 15, `TwoFA` 12, `SecurityLogs` 7,
      `UpdatePasswordPage` 3, and the four landing entries 12/2/1/1. Every other guard under
      `tests/unit/guards/` has such a fixed point and `dead-tailwind.test.js` is the one that cannot
      (see Notes)
    - Quote §3.6's four blind spots into the file's header so the guard is not trusted past them: the one
      off-scale rem site (`PaperTrading.jsx:2355`, `'1.25rem'`), Tailwind's 434 built-in size classes
      (**O1**), `src/App.jsx`'s 6 sizes outside the three roots, and computed sizes
    - **Must not be lost:** nothing — this task touches no source file, no copy and no figure. That is
      precisely why it lands alone
    - **BLOCKED.** §7.6 flags O1 as *needing the requester's decision before commit 1 is seeded, because
      the seed's shape depends on the answer.* §3.5's two corrections to Requirement 1.4 also need
      acknowledgement, since they change a seeded list in an approved document. **Default, to be
      confirmed:** px-only per 1.1's four patterns, seed extended by the eleven `components/` entries,
      `PaperTrading` at 51 — which is O1 option B's shape and the one §7.6 recommends
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`
    - **Regression test and assertion:** this file's own six assertions. **Fails before:** with the
      eleven `components/` entries omitted, `accounts for every file that still carries an absolute
      size` fails naming `components/SupportCenter.jsx` and ten others. **Passes after:** green on the
      first commit, seeded at today's counts, having asserted the tree as it is (Requirement 22.4)
    - _Requirements: 1.3, 1.4, 1.5, 18.1, 22.2, 22.4_

- [ ] 2. Commit 2 — the environment badge: four stacked treatments on 23 characters (Requirement 9)

  **Files:** `tests/unit/ds/TradingEnvironmentBadge.test.jsx` (new),
  `src/components/ds/TradingEnvironmentBadge.jsx`, `src/components/ds/Panel.jsx`,
  `src/components/ds/StrategyStatus.jsx`.

  `TradingEnvironmentBadge.jsx:230` applies `font-mono font-bold uppercase tracking-wide` over the
  `text-micro` every variant already carries, so `ENVIRONMENT UNCONFIRMED` reaches a retail reader as
  23 characters of 10px monospace bold caps with added letter spacing. One className, four stacked
  treatments. It goes second — cheap, high leverage, and the reason is bisect rather than risk: the
  treatment reaches **eight** pages and **six** primitives (§4.2's re-measurement; `SignalTrace.jsx`
  references it nowhere), and via `ds/Panel` and `ds/PageHeader` effectively every page in
  `In_Scope_Pages`. Going last would not shrink that blast radius by one page; it would only place the
  badge diff after eleven page diffs, at which point a badge regression and a page regression are
  indistinguishable.

  - [ ] 2.1 Write `tests/unit/ds/TradingEnvironmentBadge.test.jsx` first, and observe it pass against the unedited primitive
    - **There is no badge test today.** `tests/unit/ds/` holds 26 files and none of them is this one, so
      §4.2's stated precondition — "read the badge's test file first" — cannot be read. What covers the
      badge today is indirect: `dashboard-kill-switch.test.jsx:176` reads it through `data-environment`,
      `data-environment-border`, `data-environment-icon`, the `span.sr-only + span` label and `title`
      — **data attributes and text, never class strings** — and `liveTrading.test.jsx:287` covers
      `ENVIRONMENT UNCONFIRMED`. So the four treatment axes are asserted nowhere, and
      `SIMULATED · SERVER LABEL UNAVAILABLE` (declared at `:108`, its reason recorded at `:61`–`:64`)
      is rendered by **no test in the suite**
    - Block 1 — **both null arms render, and render differently**, asserted as a pair so a collapse fails
      rather than half-fails: `environment == null && isSimulated` →
      `SIMULATED · SERVER LABEL UNAVAILABLE`; `environment == null && !isSimulated` →
      `ENVIRONMENT UNCONFIRMED`; `data-environment` distinct on each
    - Block 2 — **neither arm resolves to `LIVE` or `PAPER`.** This is `semantic.js:236`'s rule expressed
      as an assertion: defaulting to LIVE is alarmist, defaulting to PAPER is dangerous, so the server's
      `null` is rendered rather than resolved
    - Block 3 — every variant keeps its screen-reader prefix and its `title` long form, so Requirement
      20.1's accessible name survives a treatment change
    - Block 4 — the four treatment axes read as classes on the label element, **seeded at today's four**,
      with a comment recording that this is the one assertion in the suite intended to be edited by task
      2.2. That is what makes 2.2's edit observed rather than assumed
    - **Must not be lost:** the two unresolved labels are different *information*, not two spellings of
      one fact — `TradingEnvironmentBadge.jsx:62` records it. Only one of them would fail today if they
      were collapsed, which is §5.8's most consequential hole and the reason this file is written before
      the edit
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/ds/TradingEnvironmentBadge.test.jsx --fileParallelism=false`
    - **Regression test and assertion:** this file. **Fails before:** delete the
      `SIMULATED · SERVER LABEL UNAVAILABLE` arm from the primitive and block 1 fails — today nothing in
      the suite does. **Passes after:** all four blocks green against the unedited primitive, block 4
      recording the treatment as it is
    - _Requirements: 9.1, 9.2, 18.2, 20.1_

  - [ ]* 2.2 **MUST LAND ALONE** — reduce the treatment, keep every distinction
    - `TradingEnvironmentBadge.jsx:230`: stop stacking four axes on one string. Keep the axis that
      carries meaning for a server-supplied literal and drop the ones that only add weight — the
      variant's `text-micro` and its `data-*` attributes stay untouched
    - The wording lives at exactly three declaration sites and Requirement 9.3 requires them to move
      together: `Panel.jsx:126`, `TradingEnvironmentBadge.jsx:113`, `StrategyStatus.jsx:201`. A fourth
      spelling on a page is a new defect, not a variant
    - **Must not be lost, absolutely:** `ENVIRONMENT UNCONFIRMED` stays distinguishable from
      `SIMULATED · SERVER LABEL UNAVAILABLE`, and **neither may be resolved to `LIVE` or `PAPER`**.
      Wording may shorten only where the shorter wording draws every distinction the longer one drew.
      Every variant keeps its screen-reader prefix, its `title` long form and its `data-environment`
      value — `dashboard-kill-switch.test.jsx` reads all of them and must keep passing unchanged
    - **Optional for launch: Requirement 9 is P2** (the treatment is measured, the wording is a
      judgement). Its *position* is not optional: §4.2 places it before every page commit so that a
      later page regression bisects to that page
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/ds/TradingEnvironmentBadge.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/liveTrading.test.jsx` and
      `tests/unit/dashboard-kill-switch.test.jsx`. Three files because the badge reaches eight pages and
      six primitives. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** `tests/unit/ds/TradingEnvironmentBadge.test.jsx` block 4 —
      **fails before** (it is seeded at the four stacked axes) and **passes after** the treatment is
      reduced, with blocks 1–3 green in both states. Plus `liveTrading.test.jsx:287` and
      `dashboard-kill-switch.test.jsx:176` green unchanged, which is what proves the labels and the
      accessible names survived
    - _Requirements: 9.1, 9.2, 9.3, 18.2, 20.1, 20.5_

- [ ] 3. Commit 3 — `ds/Chart.jsx`'s tick and axis-label sizes (Requirement 1.1, 1.2, 2.2)

  **Files:** `tests/unit/ds/Chart.test.jsx`, `src/components/ds/Chart.jsx`,
  `tests/unit/guards/absolute-font-sizes.budget.js`.

  Same argument as the badge, one step weaker: `Chart.jsx:722` (`TICK`) and `:729`
  (`AXIS_LABEL_STYLE`) are two `fontSize: 10` declarations sitting behind every chart on eleven pages,
  and `:719`'s own comment already says "axis ticks are text". Placing this after the page work would
  mean `PaperTrading.jsx`'s eight `fontSize={9}` axis props get resolved twice — once on the page, once
  when the primitive moves.

  - [ ] 3.1 Add the size assertion to `tests/unit/ds/Chart.test.jsx` and observe it fail
    - `Chart.test.jsx` contains no `fontSize`, no `TICK` and no `AXIS_LABEL_STYLE` reference today; its
      strictest assertion in that region is `it('rejects a kind it does not understand')`
    - One `it`, over the **exported constants** rather than rendered SVG, because recharts is stubbed in
      every page test that touches a chart and the value's correctness is a property of the declaration:
      `TICK.fontSize` and `AXIS_LABEL_STYLE.fontSize` are read from `token.text.micro`, not written as
      `10`
    - Note the name in the assertion: §3.5 calls the second constant `AXIS_LABEL` and the file calls it
      `AXIS_LABEL_STYLE`. The file wins
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/ds/Chart.test.jsx --fileParallelism=false`
    - **Regression test and assertion:** the new `it`. **Fails before:** both constants are the literal
      `10`. **Passes after:** task 3.2. Every other assertion in the file stays green in both states
    - _Requirements: 1.1, 1.2, 18.2_

  - [ ] 3.2 **MUST LAND ALONE** — read both sizes from the token
    - `Chart.jsx:722` and `:729`: `fontSize: 10` → `fontSize: token.text.micro`. `token.text.micro` is
      `'0.625rem'` — which is what `tokens.css:69`'s own annotation (`labels, chips`) says the axis tick
      treatment should read, and it means a trader who raised their browser default sees the axis move
      with everything else
    - Lower `components/ds/Chart.jsx` from 2 to 0 in the font-size budget — which under Requirement 1.5
      means **deleting the entry**, in this same commit
    - **Must not be lost:** `fill: token.content.secondary` on both (6.2:1 contrast, recorded at `:719`),
      `fontFamily: token.font.mono` on `TICK` and `token.font.sans` on `AXIS_LABEL_STYLE` — the mono/sans
      split is Requirement 3.1's rule already correctly applied and is not part of this edit
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/ds/Chart.test.jsx --fileParallelism=false`,
      then `tests/unit/guards/absolute-font-sizes.test.js`
    - **Regression test and assertion:** 3.1's `it` passes. And the ratchet's `requires progress to be
      recorded, not banked` **fails before** the budget entry is deleted and **passes after** — which is
      the assertion that makes "cleared" mean "recorded as cleared"
    - _Requirements: 1.1, 1.2, 1.5, 2.1, 2.2, 18.2, 22.2_

- [ ] 4. Commit 4 — accessibility enforcement reaches every page a trader can open (Requirement 6)

  - [ ] 4.1 **MUST LAND ALONE** — extend `IN_SCOPE_PAGES`, seed the new waivers, and derive the total
    - **Files:** `tests/unit/guards/a11y-ratchet.test.js`, `eslint-rules/a11y-ratchet.js`
    - Extend `IN_SCOPE_PAGES` to every file under `src/pages/` plus the 14 rendered `Landing_Surface`
      sections, so `jsx-a11y` lints them at **error**. Today the eleven `Unmigrated_Pages` are not
      linted at all, which is why a keyboard-only trader can reach Risk Settings and find a kill switch
      they cannot operate
    - Record each newly-linted page's outstanding count in `eslint-rules/a11y-ratchet.js` **with the
      task that clears it** (Requirement 6.2). Every later page commit deletes its own entry rather than
      lowering it to `0`
    - This lands before the pages it newly lints, not inside one of them: turning 25+ files to error
      inside a page commit puts the other 24 files' findings in that page's PR
    - **Decision D7, in this same commit because it goes red the moment the list widens.**
      `a11y-ratchet.test.js`'s last test ends `expect(waived).toBe(4)` — a checked-in total whose stated
      purpose is that "the milestone that inherits this debt cannot be surprised by its size".
      Extending the list moves `waived` off 4, and so does task 5.3's deletion. Replace it with the two
      assertions it was standing in for: `expect(measured).toBe(waived)` stays (it holds in both
      directions on any list, empty included), and the historical account moves into the comment block
      that already carries 25 → 23 → 21 → 18 → 8 → 4, extended with 4 → 0. Requirement 12.3's "IF the
      empty case is not handled, THEN that is a defect in the guard and SHALL be fixed rather than
      worked around by leaving a `0` entry" is discharged by exactly this edit
    - `it('waives only in-scope pages, so every entry has a task that clears it')` is compatible by
      construction: it asserts every waived file is *in* the list, and this commit widens the list
      before adding entries
    - **Must not be lost:** `src/components/ds/**` stays at exactly zero findings, at `error` rather
      than `warn` (Requirements 6.3, 20.4). The waiver list only grows here and only shrinks under
      Requirement 12 — no page gains a finding
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/a11y-ratchet.test.js --fileParallelism=false`.
      This is the one guard with a real execution cost: it runs ESLint in-process over `src/pages/**`
      and `src/components/ds/**` at import time, which is also why it *measures* rather than declares
      its numbers
    - **Regression test and assertion:** `a11y-ratchet.test.js` — `holds every unwaived in-scope page at
      zero findings` and `${file} holds exactly ${entry.count}`. **Fails before:** with the list widened
      and no entries seeded, every newly-linted page with findings fails as unwaived, and
      `expect(waived).toBe(4)` fails on the stale total. **Passes after:** every waiver's count is the
      measured count and `ds/**` is at zero
    - _Requirements: 6.1, 6.2, 6.3, 12.3, 18.1, 20.3, 20.4_

- [ ] 5. Commits 5–6 — Marketplace: the product's shopfront, one generation behind (Requirements 10, 12, 3.4)

  **Files:** `tests/unit/pages/strategyMarketplace.test.jsx` (new), `src/pages/StrategyMarketplace.jsx`,
  `eslint-rules/a11y-ratchet.js`, `tests/unit/guards/absolute-font-sizes.budget.js`.

  This page is where a retail trader decides whether to pay for someone else's strategy. Requirement 10
  and Requirement 3.4 are both subtraction, and **a page that is only subtracted from is a page that
  stops selling anything**, which is its own failure. So every decoration removed below names what
  takes its place (§7's framing, Decision D5).

  Requirements 10 and 12 are one edit wearing two numbers: `:664`'s `onClick` on a `div` is one of the
  four a11y findings, and you cannot rebuild the card onto a `ds/*` primitive and leave the bare handler
  behind. Splitting them means writing the same finding twice and leaving the waiver at a number that no
  longer describes the file. Requirement 11 is the separate axis (task 6) because it is the only
  Marketplace change with a behavioural surface.

  - [ ] 5.1 Write `tests/unit/pages/strategyMarketplace.test.jsx` first, and observe it fail on the current file
    - **No frontend test renders this page.** Searching `tests/` for it returns five source-scanning
      guards holding an entry for its path and nothing that mounts it. Its behavioural coverage is
      `tests/unit/design/subscriptionState.test.js`, which covers the *module* Requirement 10.6 forbids
      reimplementing and never the page
    - Four blocks, matching the four axes. (1) The card is keyboard-activatable and carries an accessible
      name (Requirement 12.4). (2) The four failure classes render four different pieces of copy resolved
      through `errorCopy.js`, and none of them is `err.message` (Requirements 11.2, 11.3). (3) The
      `42703` case renders `unavailable` naming what cannot be shown, not an empty catalogue
      (Requirement 11.4). (4) `featured` and `trending` remain distinguishable after task 5.2's
      replacement of the ribbon
    - **This file is the reason commits 5–7 can be split at all**: without it Requirement 11's
      behavioural change has no assertion of its own and Requirement 16's no-functional-change
      constraint is a reviewer's opinion
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/strategyMarketplace.test.jsx --fileParallelism=false`
    - **Regression test and assertion:** this file. **Fails before, on all four blocks:** the cards are
      `div`s with `onClick` and no `role`/`tabIndex`/key handler (`:664`, `:729`); all four failure
      classes resolve to the one string at `:382`; a schema fault presents as an empty catalogue; the
      only `featured`/`trending` distinction is the `bg-status-warning` ribbon. **Passes after:** tasks
      5.2, 5.3 and 6.1 — blocks 1 and 4 after 5.2–5.3, blocks 2 and 3 after 6.1
    - _Requirements: 10.5, 11.2, 11.3, 11.4, 12.4, 18.1, 20.1_

  - [ ] 5.2 Commit 5 — the visual generation: 71 off-scale sizes, 69 `font-mono`, 25 `uppercase`, four decorations **[BLOCKED past axis 1: OPEN DECISION O1]**
    - **Axis 1 — the 30 `text-[Npx]` classes** move onto the `Type_Scale` by §7.1's cohort table. The
      syntax differs from a page-level `fontSize`; the role question does not. Chips and labels →
      `micro`. **Two cohorts grow and they are the ones that matter:** `:628`'s historical-results
      statement (`text-[11px]`, carrying `data-testid="historical-results-statement"` precisely so it can
      be asserted) is a safety statement about backtested figures and goes to **`body`**; `:607`'s *No
      figures recorded for this condition.* is an account of an absence and goes to **`body`**. So does
      `:553`'s environment description and the empty-condition sentence at `:612`
    - **Axis 2 — 69 `font-mono` and 25 `uppercase`,** resolved by Requirement 3.1 and recorded per §7.2
      (which is the ledger Requirement 3.2 asks for; a 94-row table would be a second design system in
      table form). **Keep** monospace on figures in a grid (`:687`–`:713`, `:750`–`:771` — numerals a
      reader compares down a column) and on identifiers: prices (`:713`, `:901`), rating (`:781`),
      published date (`:986`), the server-supplied category/difficulty/validation words (`:742`,
      `:871`–`:881`). **Convert every sentence:** `:553`, `:607`, `:628`, `:1041`'s hero paragraph,
      `:1067`'s error line, and the hero's three count captions at `:1045`–`:1051`. A `font-mono
      text-sm` paragraph is the single strongest signal on the page that it belongs to the previous
      generation. Drop `uppercase` from the two- and three-word *section names* — `Your subscription`
      (`:828`), `Per-condition results` (`:586`), `Recent reviews` (`:993`) — which gain word-shape
      recognition and lose nothing. **Keep** it on the environment word at `:548`/`:578`: that is
      `LIVE`/`PAPER`/`BACKTEST` as the server spells it, not a transform of one
    - **Axis 3 — the decorative styling the token layer does not declare (Decision D5, every removal
      names its replacement).** `:665`'s gradient fill → `--color-surface-panel` flat with
      `--color-line-strong` as the border, and `--shadow-raised` against the ordinary card's
      `--shadow-panel` carrying the emphasis the gradient carried. `:667`'s 128px corner wash → deleted
      outright, the one element with no replacement, because it carried no information at any opacity.
      `rounded-2xl` at `:665`, `:865`, `:1032` → `--radius-xl` (12px); `rounded-full` is `--radius-full`
      and is untouched. `:1033`'s `blur-3xl` hero glow → deleted; the hero keeps its weight from
      `ds/PageHeader`'s `--text-page` title plus the three count captions. (**§7.3's re-measurement:** the
      glow is at `:1033`, not `:1034` as Requirement 10.3 and §1.6 both state; `:1034` is the
      `relative z-10` wrapper below it)
    - **The "Featured" ribbon — Decision D6, and this one is not a styling decision.** `library.py:674`
      shows `featured` is **editorial**: `is_featured` is set by an operator, ordered by `published_at`,
      derived from no figure. `:759` shows `trending` has an exact basis: `clone_count` desc then
      `avg_rating` desc. The current treatment says the opposite of both — `bg-status-warning` is the hue
      `semantic.js` reserves for a state a trader should act on, and `Sparkles` plus a gold pill beside a
      Sharpe ratio reads as a claim about quality that the server does not make. Replace with three
      declared means, in order: a `ds/SectionHeader` **stating the basis** (*Selected by VyomQuant*,
      *Most cloned*) — this is where Requirement 10.5 is satisfied, once per section rather than implied
      once per card; elevation and border rather than hue (Requirement 20.5 forbids a state carried by
      colour alone, and this replaces a hue with a shape); and a `ds/StatusBadge` labelled *Selected*
      with a `ds/Tooltip` saying the listing was chosen by VyomQuant and that the selection is not a
      statement about performance. **Default: keep the badge**, because Requirement 16.3 forbids changing
      the set of things a trader can see and silently dropping a distinction the server draws is closer
      to that than keeping it in a calmer form. Removing the per-card marker entirely is Requirement
      10.5's other permitted arm and needs the requester's sign-off — flagged in Blocked, defaulting to
      keep
    - Render the cards through `ds/*` primitives; the subscription state continues to resolve through
      `design/subscriptionState.js`, which **is not reimplemented** (Requirement 10.6)
    - **Must not be lost:** the page requests exactly what it requests today (Requirement 10.7 — a visual
      rebuild of a 1,210-line page is where a request shape quietly changes, so `api-paths.budget.js`
      must be byte-identical afterwards). `:628`'s `data-testid` survives the size change. `:607`'s
      absence account is `design.md §6`'s column 2 — an account of a missing figure — and may be moved or
      enlarged, never shortened. The page stays at **0** colour literals (`no-colour-literals.budget.js:517`
      asserts it exactly), so the rebuild cannot reach for a hex to replace a gradient
    - **BLOCKED past axis 1.** §7.6: "nothing in §7 proceeds past axis 1 without it." The page's real
      off-scale total is **71**, not 30 — 41 Tailwind built-ins (`text-4xl font-black` on the hero `<h1>`
      at `:1038`, `text-3xl` on the detail price at `:901`, `text-xl` on the card title at `:679`,
      `text-lg` on the four featured-card metrics) are the page's entire visual hierarchy and not one of
      them is a declared step. They scale with the reader and they are not `Type_Scale` steps, which is
      the distinction Requirement 1 conflates
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/strategyMarketplace.test.jsx --fileParallelism=false`,
      then `tests/unit/design/subscriptionState.test.js`, then
      `tests/unit/guards/absolute-font-sizes.test.js` and `tests/unit/guards/no-colour-literals.test.js`.
      Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** 5.1's block 4 — `featured` and `trending` distinguishable —
      **fails before** (the only distinction is the removed ribbon's hue) and **passes after**. Plus the
      ratchet: `pages/StrategyMarketplace.jsx`'s entry falls and is deleted at zero, and `requires
      progress to be recorded, not banked` fails if the sizes go and the entry stays
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 16.3, 18.3, 20.5, 22.2_

  - [ ] 5.3 Commit 6 — the four a11y findings, and the waiver line deleted **[BLOCKED: O1, as 5.2]**
    - **Files:** `src/pages/StrategyMarketplace.jsx`, `eslint-rules/a11y-ratchet.js`
    - The four findings are two on each of two elements and both elements are the same mistake: `:664`'s
      featured card and `:729`'s catalogue card are `div`s with `onClick`, `cursor-pointer` (`:665`,
      `:731`), no `role`, no `tabIndex` and no key handler —
      `click-events-have-key-events` + `no-static-element-interactions`, twice. There is no third
      interactive `div` in the file and nothing sets `role="button"`, so the count of 4 is fully
      accounted for
    - Fix them the way every other in-scope page did: `ds/DataTable`'s row activation is the precedent
      Requirement 12.4 names, and `a11y-ratchet.js`'s own comment block records that `SignalTrace`'s last
      two findings were this identical pair on a signal row
    - **Delete** the waiver entry at `eslint-rules/a11y-ratchet.js:227` — not lower it to `0`. A cleared
      page belongs at `error` with every other unwaived page and deleting the line is what puts it there
    - Lands **with** 5.2, per §4.3's "12 with 10". 5.3 never lags behind a release: a card rebuilt onto a
      primitive with the bare handler still attached is the worst of both states
    - **Must not be lost:** the card still navigates on activation to exactly the same detail view, by
      pointer *and* by keyboard. No route changes, no request changes (Requirements 16.2, 16.3)
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/a11y-ratchet.test.js --fileParallelism=false`,
      then `tests/unit/pages/strategyMarketplace.test.jsx`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** `a11y-ratchet.test.js` — with the entry deleted and the findings
      unfixed, `holds every unwaived in-scope page at zero findings` **fails** naming
      `src/pages/StrategyMarketplace.jsx` with 4; with both done it **passes**, and
      `expect(measured).toBe(waived)` holds on the now-empty list, which is the empty-case behaviour
      Requirement 12.3 asks for and task 4.1 made possible. Plus 5.1's block 1
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 18.1, 20.1, 20.3, 20.4_

- [ ] 6. Commit 7 — Marketplace tells the trader which of four things went wrong (Requirement 11)

  - [ ] 6.1 **MUST LAND ALONE** — the read path joins the convention **[BLOCKED: O1, per §7.6]**
    - **Files:** `src/pages/StrategyMarketplace.jsx`
    - `:380`–`:382` is one `catch`, one `console.error(err)`, one string — *Failed to load marketplace
      data. Please try again.* — for every failure mode, rendered at `:1067` in a hand-styled
      `bg-status-error/20` box with an `AlertTriangle` and `font-mono text-sm`. Four distinct facts
      collapse into it: the network did not answer, the server faulted, the caller is not authenticated,
      the caller is not entitled. The trader's next action differs in each — retry, wait, sign in,
      upgrade
    - The migration is mechanical and adds nothing: the `loading`/`error` pair → `usePanelState`, one of
      its eight states; `console.error(err)` → the convention's reporting path (Requirement 11.3 forbids
      logging in place of handling); the single string → `translateError(err, 'marketplace')` returning
      `{headline, detail, retryable, action, supportRef}`; the hand-styled box → `ds/Alert` or
      `ds/Panel`'s `error` state
    - **The `42703` case, precisely.** With migration `007` unapplied the projection raises PostgreSQL
      `42703`, and the backend already refuses to swallow it: `library.py:720` and `:785` raise
      `MarketplaceError(MARKETPLACE_READ_FAILED)` rather than a zero-filled 200. So the code the client
      needs is already on the wire and the current page discards it. Render `unavailable` **naming what
      cannot be shown** — "no strategies exist" and "we cannot read the catalogue" are different facts
      and the second is not the trader's to act on
    - **Must not be lost:** `translateError` returns a headline from an authored table, never one derived
      from the error — no `err.message`, no `statusText` naming an exception class, no internal `/api/`
      path reaches the screen. `empty` stays distinguishable from `unavailable` stays distinguishable
      from `error` (Requirement 19.4). **This task does not fix production Marketplace** (Requirement
      11.5): applying `007` is a data-layer change outside this spec, and 11.4 changes only what the
      trader is told while it is unapplied
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/strategyMarketplace.test.jsx --fileParallelism=false`,
      then `tests/unit/design/errorCopy.property.test.js`, then `tests/unit/hooks/usePanelState.test.js`.
      Then confirm `api-paths.budget.js` is unchanged
    - **Regression test and assertion:** 5.1's blocks 2 and 3. **Fails before:** all four failure classes
      render the same string and the `42703` case renders an empty catalogue. **Passes after:** four
      different pieces of authored copy, none of them `err.message`, and `unavailable` on the schema
      fault. `errorCopy.property.test.js` stays green in both states — it proves `translateError` is safe,
      and 5.1 is what proves the page calls it
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 16.2, 18.1, 19.4_

- [ ] 7. Commits 8–17 — the ten unmigrated pages adopt the convention, smallest first (Requirements 4, 5, 6)

  Requirement 4.5's order exactly: `RiskSettings` → `TwoFA` → `SecurityLogs` → `UpdatePasswordPage` →
  `Wizard` → `LegalPage` → `Profile` → `Billing` → `ExchangeManager` → `AuthPage`. Smallest first, so
  the convention's fit is tested on a 438-line page before an 1,181-line one. The eleventh page in
  Requirement 4.5's list is `pages/Landing.jsx`, which that clause routes to Requirement 14 — it is
  discharged by task 9.2, not migrated.

  **Every page in this group takes the same five steps**, and the steps are stated once here rather
  than eleven times below:

  1. Resolve every read through `usePanelState`, rendering exactly one of its eight states. Delete the
     page's own `loading`/`error` boolean pair (Requirement 4.1).
  2. Declare every trader-visible figure in `design/pageFields.js` with its source path **and its
     not-available reason**, and render it through `design/reported.js` — `fromNullable(read(body,
     path), entry.reason)`, the pattern `pageFields.js`'s own header documents — rather than reading the
     response body directly (Requirement 4.2).
  3. Replace hand-built status indicators, metrics, tables, empty, loading and error conditions with the
     corresponding `components/ds/*` primitive (Requirement 4.3).
  4. Resolve every absolute font size by §2.4's nine-question procedure, **recording each resolution as
     a comment on the line** in the form `// fontSize: 9 → --text-body (sentence)` (Requirement 2.4).
     Lower the font-size budget entry, deleting it at zero.
  5. Read every colour from `design/tokens.js` or a token utility class and lower
     `no-colour-literals.budget.js` **in the same commit** (Requirements 5.1, 5.2, 22.2). Where a
     literal has no semantic equivalent in `design/semantic.js`, **raise the mapping question rather
     than picking the nearest token** — `semantic.js` is the only module that knows which token means
     "profitable" (Requirement 5.3).

  **What must not be lost on any page in this group:** every `reason` string added in step 2 is
  load-bearing from the moment it exists and may never be shortened to a marker or replaced by a
  generic sentence (Requirement 19.3). A genuine `0`, `0.0` or empty collection renders as that value;
  an unavailable figure renders the marker **with** its reason (Requirements 19.1, 19.2). No control,
  route, request or confirmation is removed (Requirement 16). Every control keeps its accessible name
  and its keyboard path (Requirements 6.4, 20.1–20.3). **No new primitive, no new `usePanelState`
  state, no new `design/` module** — if a page appears to need one, record the need and raise it; a
  primitive added for one page is how the second convention starts (Requirements 4.4, 17.1–17.3).

  **Verification, for every page in this group:**
  `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
  then the same command for `tests/unit/guards/no-colour-literals.test.js`,
  `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js` and
  `tests/unit/design/pageFields.test.js`, plus that page's own test file where one exists (named per
  task). Then `node node_modules/eslint/bin/eslint.js src`.

  **The regression assertion, for every page in this group**, is the pair that makes "cleared" mean
  "recorded as cleared": the font-size budget and `no-colour-literals.budget.js` are asserted **exactly
  in both directions**, so clearing the page without lowering its entries **fails** `requires progress
  to be recorded, not banked`, and lowering an entry without clearing the page **fails** `holds every
  file at or below its budget`. Both pass only when the page and its two entries move together. Second,
  `a11y-ratchet.test.js`: the page's waiver entry from task 4.1 is deleted in this same commit, so
  `holds every unwaived in-scope page at zero findings` **fails before** the findings are fixed and
  **passes after**. Six of these ten pages have no test file of their own today, which is exactly why
  the measured guards are the instrument rather than a claim.

  - [ ] 7.1 Write `tests/unit/pages/riskSettingsKillSwitch.test.jsx` before RiskSettings is touched
    - **Nothing renders `RiskSettings.jsx` today and it is not linted at all.**
      `tests/unit/dashboard-kill-switch.test.jsx` covers the *Dashboard's* halt confirmation, not Risk
      Settings' toggles. Requirement 6.5 asks for a test rather than a manual check and is right to: a
      kill switch reachable only by mouse is the one control where the gap is not an inconvenience
    - Render the page; reach **every** kill-switch control by `getByRole` with an accessible name;
      operate it by `keyDown` rather than `click`; assert the request the control issues is the same one
      the pointer path issues
    - Written **before** the migration so it is observed passing against the unmigrated page and again
      after — Requirement 6.5's "before and after their migration" asks for exactly that, and a test
      first written against the tree it is meant to protect has never been observed doing anything
    - **Must not be lost:** the switch's confirmation step, if it has one, is untouched — Requirement
      16.5 puts every `ds/ConfirmDialog` out of reach of this pass
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/riskSettingsKillSwitch.test.jsx --fileParallelism=false`
    - **Regression test and assertion:** this file. **Fails before** if any kill-switch control has no
      accessible name or no keyboard path — which is the open question, since the page has never been
      linted. **Passes after:** it passes against the unmigrated page (recorded as the baseline) and
      against the migrated one, with the *same* request asserted on both paths in both states
    - _Requirements: 6.5, 18.1, 20.1, 20.3_

  - [ ] 7.2 Commit 8 — `RiskSettings.jsx` (438 lines, 16 sizes, 53 colour literals)
    - The five steps above. The 53 literals are the second-largest block in the tree and this is the
      smallest page carrying them, which is why Requirement 4.5 starts here
    - **Must not be lost:** the kill-switch controls' accessible names, keyboard paths and issued
      requests — 7.1 asserts all three, before and after
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/riskSettingsKillSwitch.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/guards/absolute-font-sizes.test.js`,
      `tests/unit/guards/no-colour-literals.test.js`, `tests/unit/guards/a11y-ratchet.test.js`,
      `tests/unit/guards/no-local-tokens.test.js` and `tests/unit/design/pageFields.test.js`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 16 → 0 with
      the entry **deleted**, colour 53 → 0. `requires progress to be recorded, not banked` **fails
      before** both entries move; `holds every file at or below its budget` **fails** if an entry moves
      without the page being cleared; `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at
      zero findings` **fails before** this page's task-4.1 waiver entry is deleted with its findings
      fixed. All three **pass after**. Plus 7.1's file green in **both** states — before and after the
      migration, with the same request asserted on the pointer and keyboard paths in each
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.2, 6.5, 19.1, 19.2, 19.3, 22.1, 22.2_

  - [ ] 7.3 Commit 9 — `TwoFA.jsx` (397 lines, 12 sizes, 12 colour literals, 10 inline `monospace`)
    - The five steps. The 10 inline `monospace` declarations are resolved against Requirement 3.1: a
      TOTP code and a recovery code are **identifiers** and keep monospace; the instructions around them
      are prose and do not
    - **Must not be lost:** every step of the enrolment and verification flow, and every error the page
      can report about a wrong code. Requirement 16.1 puts authentication logic out of scope entirely —
      this is presentation only
    - **Page test:** none exists for this page, which is why the measured guards are the instrument
      rather than a claim
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then the same command for `tests/unit/guards/no-colour-literals.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js` and
      `tests/unit/design/pageFields.test.js`. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 12 → 0 with
      the entry **deleted**, colour 12 → 0. `requires progress to be recorded, not banked` **fails
      before** both entries move; `holds every file at or below its budget` **fails** if an entry moves
      without the page being cleared; `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at
      zero findings` **fails before** this page's task-4.1 waiver entry is deleted with its findings
      fixed. All three **pass after**
    - _Requirements: 3.1, 3.2, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 16.1, 22.1, 22.2_

  - [ ] 7.4 Commit 10 — `SecurityLogs.jsx` (189 lines, 7 sizes, 3 colour literals)
    - The five steps. The log table is `ds/DataTable`'s shape — a value in a row, in a column with
      siblings, where vertical alignment across rows carries meaning — so its cells resolve to
      `--text-small` (Q6) and its headers to `--text-micro` (Q4, which catches a `<th>` first,
      deliberately)
    - **Must not be lost:** every column, every row, and the timestamp's exact rendering. A truncation
      with a `title` attribute is permitted for identifiers only and never for prose
    - **Page test:** none exists for this page; the measured guards are the instrument
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then the same command for `tests/unit/guards/no-colour-literals.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js`,
      `tests/unit/design/pageFields.test.js` and `tests/unit/ds/DataTable.test.jsx`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 7 → 0 with the
      entry **deleted**, colour 3 → 0. `requires progress to be recorded, not banked` **fails before**
      both entries move; `holds every file at or below its budget` **fails** if an entry moves without the
      page being cleared; `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at zero findings`
      **fails before** this page's task-4.1 waiver entry is deleted with its findings fixed. All three
      **pass after**. `DataTable.test.jsx` green is what proves the table kept its columns and their
      alignment semantics
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 22.1, 22.2_

  - [ ] 7.5 Commit 11 — `UpdatePasswordPage.jsx` (79 lines, 3 sizes)
    - The five steps. The smallest file in the tree and the cheapest test of the convention's fit
    - **Must not be lost:** every validation message the form can render, and the form's keyboard path
    - **Page test:** none exists for this page; the measured guards are the instrument
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then the same command for `tests/unit/guards/a11y-ratchet.test.js`,
      `tests/unit/guards/no-local-tokens.test.js` and `tests/unit/design/pageFields.test.js`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** font size 3 → 0 with the entry **deleted**. `requires progress
      to be recorded, not banked` **fails before** the entry moves and `accounts for every file that
      still carries an absolute size` **fails** if a size comes back into the cleared file; both **pass
      after**. This page has no colour-literal entry, so the colour half is the *absence* of a new entry.
      Plus `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at zero findings`, which **fails
      before** the task-4.1 waiver entry is deleted with its findings fixed
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 6.2, 22.1, 22.2_

  - [ ] 7.6 Commit 12 — `Wizard.jsx` (250 lines, 24 sizes, 5 colour literals)
    - The five steps. This is a first-run surface — one of the first screens a new retail account sees —
      so §2.4's Q1 matters here more than anywhere: a wizard is mostly sentences, and sentences go to
      `--text-body` or larger
    - **Must not be lost:** every step, every back/next path, and every explanation of what the step
      does. Requirement 8's one-next-action work is task 12; this task moves no step and removes no
      copy
    - **Page test:** none exists for this page; the measured guards are the instrument
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then the same command for `tests/unit/guards/no-colour-literals.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js` and
      `tests/unit/design/pageFields.test.js`. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 24 → 0 with
      the entry **deleted**, colour 5 → 0. `requires progress to be recorded, not banked` **fails
      before** both entries move; `holds every file at or below its budget` **fails** if an entry moves
      without the page being cleared; `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at
      zero findings` **fails before** this page's task-4.1 waiver entry is deleted with its findings
      fixed. All three **pass after**
    - _Requirements: 2.3, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 22.1, 22.2_

  - [ ] 7.7 Commit 13 — `LegalPage.jsx` (146 lines, 15 quoted `'Npx'` sizes, 3 colour literals, 12 `uppercase`)
    - The five steps. The only file in the tree whose sizes are **all** the quoted `fontSize: 'Npx'`
      syntax, so it is the one page that exercises `FONT_SIZE_QUOTED` end to end
    - The 12 `uppercase` occurrences go against Requirement 3.3's three-word rule — a legal heading
      longer than three words loses the skim and gains nothing
    - **Must not be lost:** the legal text, verbatim. Every character of it. This page is the clearest
      case in the tree where "remove unneeded text" does not reach the content
    - **Page test:** none exists for this page; the measured guards are the instrument
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then the same command for `tests/unit/guards/no-colour-literals.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js` and `tests/unit/guards/no-local-tokens.test.js`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 15 → 0 with
      the entry **deleted**, colour 3 → 0. `requires progress to be recorded, not banked` **fails
      before** both entries move; `holds every file at or below its budget` **fails** if an entry moves
      without the page being cleared; `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at
      zero findings` **fails before** this page's task-4.1 waiver entry is deleted with its findings
      fixed. All three **pass after**. This page is also the one that exercises `FONT_SIZE_QUOTED`: if
      task 1.1's quoted pattern were broken, this entry would have seeded at 0 and this commit would
      measure no change at all
    - _Requirements: 3.3, 4.1, 4.3, 4.4, 5.1, 5.2, 6.2, 19.6, 22.1, 22.2_

  - [ ] 7.8 Commit 14 — `Profile.jsx` (999 lines, 79 sizes, 7 colour literals, 51 inline `monospace`)
    - The five steps. **79 sizes is the largest single-file count in the tree** and 51 inline
      `monospace` declarations is the second largest, so this is where §2.4's procedure and Requirement
      3.1's rule are both under the most load
    - **Must not be lost:** every field, every saved-state indicator, and every API-key affordance's
      confirmation. Key material is referenced by name, never echoed
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/profile_phase5b_remediation.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/profile_phase5e_trader_ux.test.jsx`,
      `tests/unit/guards/absolute-font-sizes.test.js`, `tests/unit/guards/no-colour-literals.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js` and
      `tests/unit/design/pageFields.test.js`. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 79 → 0 with
      the entry **deleted**, colour 7 → 0. `requires progress to be recorded, not banked` **fails
      before** both entries move; `holds every file at or below its budget` **fails** if an entry moves
      without the page being cleared; `a11y-ratchet.test.js`'s `holds every unwaived in-scope page at
      zero findings` **fails before** this page's task-4.1 waiver entry is deleted with its findings
      fixed. All three **pass after**, with both `profile_phase5*` files green unchanged in both states
    - _Requirements: 3.1, 3.2, 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 22.1, 22.2_

  - [ ] 7.9 Commit 15 — `Billing.jsx` (840 lines, 39 sizes, 9 colour literals, 28 inline `monospace`)
    - The five steps. Money figures: every one gets a `pageFields.js` entry with a reason, and
      `design/reported.js` renders it — **a genuine `0.00` invoice renders `0.00`, and a figure that
      could not be read renders the marker with its reason.** This is the page where collapsing the two
      would be most plausible and most expensive
    - **Must not be lost:** every plan, price, period and invoice line; the INR presentation; and every
      confirmation on a billing action (Requirement 16.1 puts billing logic out of scope — presentation
      only)
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/billingSocketLifecycle.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/guards/absolute-font-sizes.test.js`,
      `tests/unit/guards/no-colour-literals.test.js`, `tests/unit/guards/a11y-ratchet.test.js`,
      `tests/unit/guards/no-local-tokens.test.js`, `tests/unit/design/pageFields.test.js`,
      `tests/unit/design/reported.test.js` and `tests/unit/ds/Metric.test.jsx`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 39 → 0 with
      the entry **deleted**, colour 9 → 0, with `requires progress to be recorded, not banked` failing
      before and passing after, and `a11y-ratchet.test.js`'s
      `holds every unwaived in-scope page at zero findings` doing the same for the waiver entry. Plus
      `reported.test.js` — "a zero is a reading and is never treated as an absence", its header's own
      words — and `ds/Metric.test.jsx`'s `it never renders 0 for a value it was not given`, both green
      **unchanged**. On a billing page those two are the assertions that matter most, and neither may be
      edited to make this commit pass
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 16.1, 19.1, 19.2, 19.3, 22.1, 22.2_

  - [ ] 7.10 Commit 16 — `ExchangeManager.jsx` (905 lines, 52 sizes, **128 colour literals**)
    - The five steps. 128 literals is **48% of the tree's remaining 268** and
      `no-colour-literals.budget.js`'s own header names this entry as a ratchet's resting position, so
      this is the largest colour diff in the pass
    - **Must not be lost:** the venue health distinctions. `pageFields.js`'s `exchangeHealth` note
      records that two of the four per-venue fields are constants in the aggregation service — `status`
      always `"connected"`, `latency_ms` always `35` — so neither is passed to the primitive, and
      **`exchange_api_latency_ms` must never render as `0 ms`** when it is `null`. A zero-latency
      exchange call is not a thing; the figure would be read as a claim about a working connection at
      the moment nothing has been measured
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/exchange_manager_phase6.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/ds/ExchangeStatus.test.jsx`,
      `tests/unit/guards/absolute-font-sizes.test.js`, `tests/unit/guards/no-colour-literals.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js` and
      `tests/unit/design/pageFields.test.js`. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 52 → 0 with
      the entry **deleted**, colour **128 → 0**, the largest single entry in the colour budget.
      `requires progress to be recorded, not banked` **fails before** both entries move and `holds every
      file at or below its budget` **fails** if an entry moves without the page being cleared; both
      **pass after**, as does `a11y-ratchet.test.js` once the waiver entry is deleted with its findings
      fixed. `ExchangeStatus.test.jsx` green **unchanged** is the load-bearing half: its header records
      that `0 ms` on the strength of a `null` "would be a fabricated reading a trader would act on"
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.2, 19.1, 19.2, 19.3, 19.5, 22.1, 22.2_

  - [ ] 7.11 Commit 17 — `AuthPage.jsx` (1,181 lines, 29 sizes, 30 colour literals, 29 inline `monospace`, 3 gradients)
    - The five steps, plus the three gradients: `tokens.css` declares no gradient, so they resolve to
      the flat surfaces it does declare. The largest page in the group and therefore last
    - **Must not be lost:** every auth path — password, OAuth, OTP — every error the page can report,
      and the redirect origin behaviour. Requirement 16.1 puts authentication logic out of scope;
      Requirement 16.3 puts route paths and guard conditions out of scope
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/auth_oauth.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/auth_otp.test.jsx`,
      `tests/unit/pages/authRedirectOrigin.test.jsx`, `tests/unit/guards/absolute-font-sizes.test.js`,
      `tests/unit/guards/no-colour-literals.test.js`, `tests/unit/guards/a11y-ratchet.test.js`,
      `tests/unit/guards/no-local-tokens.test.js` and `tests/unit/guards/nav-contract.test.js`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the group pair at this page's numbers — font size 29 → 0 with
      the entry **deleted**, colour 30 → 0, `requires progress to be recorded, not banked` failing before
      and passing after, and `a11y-ratchet.test.js` doing the same for the waiver entry. The three auth
      files and `nav-contract.test.js` green **unchanged** is what proves no route, redirect or guard
      condition moved (Requirement 16.3)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 10.1, 16.1, 16.3, 22.1, 22.2_

- [ ] 8. Commits 19–21 — the three in-scope pages a "unmigrated pages" scope would have skipped (Requirement 4.6)

  `PaperTrading.jsx` (1 convention reference), `StrategyBuilder.jsx` (0) and `StrategyDetail.jsx` (0)
  are inside `In_Scope_Pages`, are at **zero** colour literals, and hold **104 of the 401** unitless
  sizes between them. Requirement 4.6 exists for exactly this reason, and every rule in this plan is
  keyed on a *measured property of the file* rather than on which list the file is on. The five steps
  from task 7 apply unchanged, and so do task 7's group verification and group regression assertion.

  - [ ] 8.1 Commit 19 — `PaperTrading.jsx` (3,527 lines, **51** sizes, 22 inline `monospace`)
    - Resolve the 51 by §2.6's cohort table, which is this plan's worked example and is not re-derived
      here: four 9px label styles + the `Explainer` eyebrow at `:751` → **`micro`**; **seventeen 9px
      hints, notes, footnotes and explanations → `body`** (+44%); `:3367`'s mixed-role container **loses
      its `fontSize` entirely** and its children split `micro`/`body`; `fieldStyle` (`:449`, 11px form
      control) → `body`; the two 11px table values → `small` (no change); `:713`'s unit suffix → one step
      below its figure, moving when the figure moves; six 11px sentences → `body`; three 10px status
      pills → `micro` (no change); five 10px prose blocks including `:3515`'s `role="alert"` line →
      `body`; `:558`'s 8px `SimulatedTag` → `micro` (+25%, and its 9px `FlaskConical` glyph follows to 10
      to stay optically matched); `:2882`'s 16px latency figure → `section` (panel-level value, tie
      resolves up); `:2355`'s `'1.25rem'` page `<h1>` → `page`
    - **41 of 43 sizes are at or below 11px and the floor is 10px, so almost every resolution is upward.
      A pass that produces no layout changes here has not done the work** — it has found a way to keep
      the current sizes while renaming them. Yield in this order: remove `whiteSpace: 'nowrap'` (`:569`,
      `:471`); spend padding or gap **from the declared spacing scale** (`:568`'s `padding: '1px 5px'` is
      already off-grid and moving it onto the grid is a fix, not a cost); reduce columns at that
      breakpoint using the machinery the page already has (`gridColumns(220)` at `:2713`, the
      `singleColumn` flag); truncate with a `title` attribute **only for identifiers**, never for prose.
      Forbidden as yields: re-adding a px size, an eighth step, or `transform: scale()`/`zoom` on a text
      container — both of the last two reintroduce device-pixel pinning through a door the ratchet cannot
      see
    - **`:707`'s conditional is the task's load-bearing case and it is where §2 and §6 meet.**
      `fontSize: missing ? 13 : 18` looks like two roles and is one: both arms render the panel's
      reported value, and the only difference is *which string* — the figure when the read succeeded, the
      not-available marker plus its reason when it did not. The 13 exists because the reason is longer
      than a number and 18px overflowed; `wordBreak: 'break-all'` at `:709` is the standing evidence that
      it overflowed anyway. **So the reason was not cut — it was set 28% smaller than the figure it
      stands in for, and the trader who most needs to read it reads it at the smallest size on the
      panel.** The conditional collapses to **one step, the value's step**, not to 13; the reason string
      itself is untouched; `wordBreak: 'break-all'` goes with the conditional, because a reason that
      breaks mid-word is a sentence a retail reader will not finish; and the layout yields by mechanisms
      1 and 2 above
    - The eight `fontSize={9}` recharts axis props at `:3197`, `:3198`, `:3263`, `:3264`, `:3308`,
      `:3309`, `:3346`, `:3347` take the declaration commit 3 already moved onto the token, not a second
      number chosen here: express them as `token.text.micro`, the same value `ds/Chart.jsx:722`/`:729`
      now reads. **Commit 3 is this task's prerequisite** — §2.6 records that the page task cannot
      satisfy Requirement 2.1 for these call sites by itself, and resolving them before the primitive
      moves means resolving them twice
    - **Must not be lost, and this page is where the risk is concentrated:** the five multi-sentence
      explanations at `:2885`, `:3283`, `:3407`, `:3434`, `:3488` (**not** `:2887`/`:3285`/`:3409`/
      `:3436`/`:3490` as Requirement 2.3 states — §7's correction 7 re-measured them two lines earlier)
      exist because `production-launch-hardening` required this page to explain its own honesty: that a
      negative latency means the clocks disagree and is shown as recorded, that three series are
      distinguishable without relying on colour, that no price is carried forward and none is
      synthesised. **They grow. They are not shortened, moved out of reach, or paraphrased.** The
      absence arm renders its declared reason in full rather than a truncation
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then `tests/unit/design/pageFields.test.js`, `tests/unit/design/reported.test.js`,
      `tests/unit/ds/Metric.test.jsx` and `tests/unit/ds/Chart.test.jsx`. Then
      `node node_modules/eslint/bin/eslint.js src`. **No page test file exists for `PaperTrading.jsx`**
      — the availability suites above are the instrument, per §6.4's stated proxy
    - **Regression test and assertion:** the ratchet — `pages/PaperTrading.jsx` falls from 51 and its
      entry is **deleted** at zero; `requires progress to be recorded, not banked` fails if the sizes go
      and the entry stays, and `accounts for every file that still carries an absolute size` fails if one
      comes back. Plus the §6.4 source check in this same commit: the file carries no `fontSize` at
      `:707` and no `wordBreak` at `:709`. Plus `pageFields.test.js`'s
      `describe('pageFields: Requirement 19.3 — every absence carries a reason')` green unchanged — it
      fails if a reason is shortened to a marker, deleted, or added to a never-absent field
    - _Requirements: 1.1, 1.2, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 4.6, 18.2, 19.1, 19.2, 19.3, 19.5, 19.6, 22.1, 22.2_

  - [ ] 8.2 Commit 20 — `StrategyBuilder.jsx` (4,609 lines, 0 sizes, 14 inline `monospace`, 13 `uppercase`)
    - Task 7's five steps, minus step 4 — the file carries **no** absolute font size, which is why its
      debt is convention (0 references) and typography-by-family rather than typography-by-size
    - The 14 inline `monospace` declarations and 13 `uppercase` occurrences resolve against Requirements
      3.1 and 3.3: node ids, expressions and server-supplied codes keep monospace; the builder's
      explanatory copy does not, and an `uppercase` label longer than three words loses the skim
    - **Touches no canvas behaviour.** Out-of-scope item 5 holds `StrategyBuilder.jsx` to Requirements
      1, 3 and 4.6 only — typography and convention. The graph editing model is a spec of its own
    - **Must not be lost:** every node, every connection rule, every validation message, and the review
      mode's suppression behaviour. `stripComments`' own history is a warning attached to this file
      specifically: an earlier tokeniser read the apostrophe in its JSX prose as a string opener and lost
      33 of its 148 `C.` references
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/strategyBuilder.validation.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/strategyBuilder.reviewMode.test.jsx`,
      `tests/unit/strategyBuilder.palette.test.jsx`, `tests/unit/builder.architecture.test.js`,
      `tests/unit/guards/absolute-font-sizes.test.js`, `tests/unit/guards/no-local-tokens.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js` and `tests/unit/design/pageFields.test.js` — then the
      remaining eight `tests/unit/strategyBuilder.*.test.jsx` files, one scoped run each, never as a
      batch. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** task 7's group assertion, minus the font-size half (the file has
      no entry and must not acquire one — `accounts for every file that still carries an absolute size`
      **fails** if this migration introduces one). `no-local-tokens.test.js` is the other direction:
      4,609 lines is where a page-local `C` object is most tempting
    - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 4.6, 17.4, 18.1, 22.1_

  - [ ] 8.3 Commit 21 — `StrategyDetail.jsx` (1,786 lines, **61** sizes, 28 inline `monospace`)
    - Task 7's five steps. 61 sizes is the second-largest count in the tree, and the page is at zero
      colour literals already, so this commit is almost entirely §2.4's procedure
    - **Must not be lost:** every performance figure's availability state. A strategy detail page is
      where a trader decides whether to deploy, so a figure that could not be read must not arrive as a
      plausible number — the marker with its reason, or nothing
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/strategyDetail.test.jsx --fileParallelism=false`,
      then the same command for `tests/unit/guards/absolute-font-sizes.test.js`,
      `tests/unit/guards/a11y-ratchet.test.js`, `tests/unit/guards/no-local-tokens.test.js`,
      `tests/unit/design/pageFields.test.js`, `tests/unit/design/reported.test.js` and
      `tests/unit/ds/Metric.test.jsx`. Then `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** the ratchet — `pages/StrategyDetail.jsx` falls from 61 and its
      entry is **deleted** at zero; `requires progress to be recorded, not banked` **fails before** the
      entry moves and `accounts for every file that still carries an absolute size` **fails** if a size
      comes back; both **pass after**. The page is already at 0 colour literals, so
      `no-colour-literals.test.js`'s exact assertion holding at 0 is the other direction — a hex reached
      for during the retoken fails it. `strategyDetail.test.jsx`, `reported.test.js` and
      `ds/Metric.test.jsx` green **unchanged** is what proves no figure's availability state moved
    - _Requirements: 1.1, 1.2, 1.5, 2.1, 2.2, 3.1, 4.1, 4.2, 4.3, 4.4, 4.6, 19.1, 19.2, 19.3, 22.1, 22.2_

- [ ] 9. Commits 22–23 — dead code, so the file you read is the file that renders (Requirement 14)

  Requirement 14.3 requires these to be their own commits, separate from any behavioural change, and to
  confirm by search that no import, test or route references the file first. §5.7 found the coupling
  that makes a careless deletion break two other files, and it is named entry by entry below.

  - [ ]* 9.1 Commit 22 — delete seven unreferenced landing components, retain the eighth with a header
    - **Files:** `src/components/landing/{AICopilot,BacktestingDemo,Features,MetricsBar,PaperTradingDemo,PortfolioAnalytics,StrategyBuilderDemo}.jsx` (deleted),
      `src/components/landing/ScreenshotComingSoon.jsx` (retained, header added),
      `tests/unit/guards/no-colour-literals.budget.js`,
      `tests/unit/guards/absolute-font-sizes.budget.js`
    - **Decision D3: `ScreenshotComingSoon.jsx` takes Requirement 14.1's second arm and the other seven
      take its first.** `no-placeholders.test.js`'s `it('finds the placeholder that is really in the
      tree')` uses that file as its non-vacuity fixture — it asserts the file exists, that the scan
      reached it, and that `found.found.copy` equals `['Coming Soon']`, and its own comment calls it "THE
      FIXED POINT". Deleting it removes the only proof that the placeholder guard is not vacuous, at
      which point every count could be 0 and only the under-budget direction would object. The file keeps
      a header stating why an unrendered component is retained, naming the guard and quoting that `it`
    - Delete `'components/landing/PortfolioAnalytics.jsx': 2` from `no-colour-literals.budget.js` **in
      this same commit** — `names only files that still exist` fails otherwise. `ScreenshotComingSoon`'s
      entry of 2 stays, because the file stays
    - Run the search first and record it: no import, no test, no route references any of the seven
    - **Must not be lost:** nothing a visitor sees. That is the whole claim of this task, and the search
      is what substantiates it. `ScreenshotComingSoon.jsx`'s `Screenshot Coming Soon` string never
      reached the screen — nothing imports it — which is also why deleting it would look free and would
      not be
    - **Optional for launch: Requirement 14 is P3.** Dead code no trader currently reaches
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/no-placeholders.test.js --fileParallelism=false`,
      then `tests/unit/guards/no-colour-literals.test.js`, then
      `tests/unit/landing_page_pricing_crash_regression.test.jsx`
    - **Regression test and assertion:** `no-placeholders.test.js`'s `finds the placeholder that is
      really in the tree` — **fails** if `ScreenshotComingSoon.jsx` is deleted with the other seven, which
      is precisely the mistake this task exists to not make, and **passes** with it retained. Plus
      `no-colour-literals.test.js`'s `names only files that still exist` — **fails before** the
      `PortfolioAnalytics` entry is removed and **passes after**
    - _Requirements: 14.1, 14.3, 14.4, 18.1, 18.3, 22.1, 22.2_

  - [ ]* 9.2 Commit 23 — delete `src/pages/Landing.jsx` and its two budget entries
    - **Files:** `src/pages/Landing.jsx` (deleted), `tests/unit/guards/no-colour-literals.budget.js`,
      `tests/unit/guards/absolute-font-sizes.budget.js`
    - The file carries its own `DEPRECATED / UNMOUNTED — LEGACY LANDING PAGE` header and is routed
      nowhere: `App.jsx:44` lazy-imports `./components/landing/LandingPage` and `:590` routes that at
      `/`. Counting 41 font sizes and 18 colour literals against a file nothing renders spends both
      ratchets on nothing (Requirement 14.2)
    - Delete `'pages/Landing.jsx': 18` from `no-colour-literals.budget.js` and `Landing`'s 41 from the
      font-size budget, **in this same commit**
    - This is Requirement 4.5's eleventh page. It is discharged here rather than migrated, which is what
      that clause's "(`Landing.jsx`, per Requirement 14)" parenthesis means
    - **Must not be lost:** nothing. But record the finding this deletion settles, because it is the
      standing hypothesis for the whole landing report: **if the requester's two recent fixes landed in
      this file, they changed nothing a visitor sees**, and "still broken" is the expected outcome of a
      correct fix applied to a dead file. Task 10.3 records the `git log` result; deleting the file is
      what stops it happening a third time, and a header alone already failed to
    - **Optional for launch: Requirement 14 is P3**
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/no-colour-literals.test.js --fileParallelism=false`,
      then `tests/unit/guards/absolute-font-sizes.test.js`, then `tests/unit/guards/nav-contract.test.js`
    - **Regression test and assertion:** `no-colour-literals.test.js`'s `names only files that still
      exist` — **fails before** the entry is deleted (it names a file that is gone) and **passes after**.
      Same assertion in the font-size guard for the 41. `nav-contract.test.js` green proves no route
      referenced the file
    - _Requirements: 14.2, 14.3, 18.1, 18.3, 22.1, 22.2_

- [ ] 10. The landing investigation — cheapest-first, running alongside commit 1, producing no page diff (Requirement 13)

  **[UNVERIFIED] throughout.** Requirement 13 asks for a reproduction and this task performs one. It
  asserts no defect and invents no defect list: static reading of all 14 rendered sections found no
  render-blocking fault, and the honest version of that is a recorded null result rather than a rewrite
  of a working page. It runs in parallel from day one because it has the longest lead time, it is the
  only **[UNVERIFIED]** item in the spec, and an investigation that emits no commit has no ordering
  constraint. Requirement 13.5 makes "no symptom reproduces" a legitimate result.

  - [ ] 10.1 Step 1 — repair the three assertions that cannot fail, then run the end-to-end test scoped
    - **Files:** `tests/unit/landing_page_pricing_crash_regression.test.jsx`
    - Three assertions in `renders entire LandingPage end-to-end without crashing` are
      `expect(container.querySelector('#pricing')).toBeDefined()` and the same for `#architecture` and
      `#waitlist`. **`null` is defined, so all three pass when the selector returns nothing.** Replace
      `toBeDefined()` with `not.toBeNull()` on all three — one line each
    - Of the four claims in that test, only `getByText('Infrastructure Tiers')` can throw today, and it
      throws for a missing *section*, not a missing anchor. A silently absent anchor is one of
      Requirement 13.3's five candidate symptoms and the test currently cannot tell us
    - This is **not** a landing change. It belongs with the investigation, before any content edit,
      because it changes what the rest of the investigation may assume
    - **Must not be lost:** the INR pricing contract this file already pins — four tiers at
      ₹0/₹499/₹999/₹2,499, four `.card-surface` cards under `#pricing`, `Recommended` on Pro Quant, no
      currency selector, `api.billing.getPlans` not called, and the annual arithmetic. That contract
      exists because the section once formatted whatever `GET /api/billing/plans` returned and a null
      price threw on `toLocaleString`
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/landing_page_pricing_crash_regression.test.jsx --fileParallelism=false`
    - **Regression test and assertion:** this file. **Fails before** the repair *only if an anchor is
      genuinely absent* — which is the measurement being taken, and the fork: a failure on an anchor is a
      navigation failure found for the cost of one line, filed under 13.6 with this test as its
      regression test; a failure on mount is a render failure and the stack names the section; a pass
      rules out both **in jsdom** and reduces Requirement 13.3's five candidates to three. **Passes
      after:** the repaired assertions hold, or the outcome is filed
    - _Requirements: 13.1, 13.2, 13.3, 13.6, 15.5, 18.1_

  - [ ] 10.2 Step 2 — mount each of the 13 sections in isolation
    - **Files:** `tests/unit/landing/landingSections.test.jsx` (new)
    - A throw-free tree does not mean a throw-free section: `LandingPage.jsx` may render a section inside
      a boundary, or a section may render nothing when a prop is absent — **and nothing is what a visitor
      reports as broken.** An empty `<section>` throws nothing, so step 1 cannot see it
    - One `it` per section — `Navbar`, `Hero`, `TrustSection`, `ScreenshotsSection`, `HowItWorks`,
      `ModernTradingSection`, `SecuritySection`, `FounderSection`, `DownloadSection`, `Pricing`, `FAQ`,
      `Waitlist`, `FinalCTA`, `Footer` — each asserting the section renders at least one element carrying
      its own text
    - **Must not be lost:** `DownloadSection`'s withdrawal. `tests/unit/pages/downloadSurface.test.jsx`
      asserts every installer card states unavailable **with its declared reason**, that no request is
      issued (proved at the hook and at both surfaces, with `fetch`, `XMLHttpRequest` and
      `HTMLAnchorElement.click` all recording), and that no size or checksum travels with the marker.
      Nothing in this task re-advertises an installer
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/landing/landingSections.test.jsx --fileParallelism=false`,
      then `tests/unit/pages/downloadSurface.test.jsx`
    - **Regression test and assertion:** this file. **The fork:** all 13 render content → the fault is
      not "a section is missing", and combined with 10.1 the symptom is presentational or environmental
      rather than structural. One renders empty → that section is the subject, filed under 13.6 with this
      `it` as its regression test, which **fails before** the fix and **passes after**
    - _Requirements: 13.1, 13.2, 13.3, 13.6, 18.1_

  - [ ] 10.3 Step 3 — settle the standing hypothesis in `git`, record all three outcomes in the test files, and put the one specific question **[BLOCKED past here: the symptom]**
    - **Files:** `tests/unit/landing/landingSections.test.jsx` (header),
      `tests/unit/landing_page_pricing_crash_regression.test.jsx` (header)
    - Run and record: `git log --oneline -- algo22-terminal/src/pages/Landing.jsx` and
      `git log --oneline -- algo22-terminal/src/components/landing/`. Two commits touching the first path
      and none touching the second, in the relevant window, settles §8.3's hypothesis: the fixes landed
      in the dead file. Half of it is settled already —
      `landing_page_pricing_crash_regression.test.jsx` asserts the **live** `Pricing.jsx` is INR-only
      with no toggle, so that half did reach the live surface. Whether the import fixes did is the open
      half
    - **It explains why a fix had no effect; it says nothing about what the original symptom is.** Even
      fully confirmed, steps 1 and 2 still had to run
    - Write the three recorded outcomes into the two test-file headers, which is where this repository
      already keeps its records — every guard under `tests/unit/guards/` carries its measurement history
      in its own header, and `downloadSurface.test.jsx` carries the mechanism behind the last asset
      failure in its
    - Then put step 3's question, which steps 1 and 2 exist to make answerable in one exchange: *the tree
      mounts, all 13 sections render content, the anchors exist and pricing is intact — so which of a
      layout fault at a viewport, a missing or 403 asset, or incorrect content is it, and at what width?*
      Asking third rather than first is deliberate; asking first risks a second round of "it looks
      broken"
    - **BLOCKED past this point.** §8.2's step 4 (the deployed bundle — `HEAD` on the referenced asset
      paths, checking for the 403 class of fault the four installers already had once, which
      `aws s3 sync dist/ --delete` produces by deleting any prefix `dist/` does not carry) and step 5
      (the viewport sweep — the landing surface is the one surface a visitor reaches on a phone, since
      `DesktopOnlyOverlay` gates the app below 1000px) both need the requester's answer first. Both are
      invisible to every test in the repository
    - **If no symptom reproduces, that is the result** (Requirement 13.5). The recording says so — the
      tree mounts, the sections render, the anchors and pricing are intact, no symptom reproduced at the
      viewports tried — and Requirements 14 and 15 proceed on their own merits. What must **not** happen
      is the pass inventing a defect list. **[JUDGEMENT] on how much is enough:** steps 1 and 2 executed
      and recorded, the `git log` check recorded, and the requester asked once with the specific question
      above. Three recorded outcomes and one specific question is the bar. It cannot prove no fault
      exists; it can prove the investigation was not skipped
    - **Must not be lost:** Requirement 13.4's four already-checked items stay checked and are not
      re-covered — the legacy aliases resolve (`tokens.css:150`, `:152`, `:162`),
      `ScreenshotComingSoon.jsx`'s placeholder is unreachable, `DownloadSection`'s installer links were
      already withdrawn to `ds/Panel`'s `unavailable` state, and pricing is INR-only
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/landing/landingSections.test.jsx --fileParallelism=false`,
      then `tests/unit/landing_page_pricing_crash_regression.test.jsx`. Both files must still pass with
      their headers rewritten
    - **Regression test and assertion:** no new assertion — this task's deliverable is the recording plus
      the question, and the two files' existing assertions must stay green across the header edit, which
      is what proves the recording did not quietly change a test
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

- [ ] 11. Commits 24+ — the landing surface joins the convention, one file per commit (Requirement 15)

  **Gated on task 10.3 being recorded**, not on a symptom being found (Requirement 13.5). Requirement
  15.4 is absolute about the scope: **this covers the token layer only.** The sections are not
  restructured, reordered or rewritten, and any content or layout change is filed separately under
  Requirement 13.6.

  - [ ]* 11.1 The four files carrying `text-[Npx]`, one commit each
    - **Files:** `src/components/landing/ScreenshotsSection.jsx` (12),
      `src/components/landing/Hero.jsx` (2), `src/components/landing/DownloadSection.jsx` (1),
      `src/components/landing/HowItWorks.jsx` (1), plus `tests/unit/guards/absolute-font-sizes.budget.js`
    - The 16 classes move onto the `Type_Scale` by §2.4's procedure, which runs on rendered text and not
      on class strings: a marketing headline is a `heading`, a caption is a `label`, a sentence is a
      `sentence` and goes to `--text-body` or larger
    - Each file's budget entry is lowered in its own commit and **deleted** at zero (Requirement 1.5)
    - **Optional for launch: Requirement 15 is P2**
    - **Must not be lost:** every word of marketing copy — this requirement is a token migration and
      touches no content (Requirement 15.4). `DownloadSection`'s unavailable cards keep their declared
      reasons
    - **Verification:** per commit, `node node_modules/vitest/vitest.mjs --run tests/unit/guards/absolute-font-sizes.test.js --fileParallelism=false`,
      then `tests/unit/landing_page_pricing_crash_regression.test.jsx`,
      `tests/unit/landing/landingSections.test.jsx` and `tests/unit/pages/downloadSurface.test.jsx`
    - **Regression test and assertion:** the ratchet's both-direction pair per file — `requires progress
      to be recorded, not banked` **fails** if the classes go and the entry stays; `accounts for every
      file that still carries an absolute size` **fails** if a size comes back into a cleared file. Plus
      10.2's `it` for that section, green unchanged, which is what proves the section still renders
      content
    - _Requirements: 1.1, 1.2, 1.5, 15.2, 15.4, 18.1, 22.1, 22.2_

  - [ ]* 11.2 The legacy aliases, per file across the 14 rendered sections
    - **Files:** the 14 `src/components/landing/` sections, one commit each, plus
      `tests/unit/guards/no-colour-literals.budget.js` where an entry exists
    - Replace `accent-cyan`, `text-muted` and `accent-cyan-dim` with the tokens they alias, so the
      aliases can eventually be retired from `tokens.css`. **They are not broken today** — `tokens.css:150`,
      `:152` and `:162` keep them as aliases onto `--color-brand-*` and `--color-content-*`, and
      Requirement 13.4 records that as checked. This clause stops new use of them; deleting them from
      `tokens.css` is a later change gated on every consumer having moved (out-of-scope item 7)
    - Express spacing, radius and shadow through the token layer in the same per-file commit
    - **Optional for launch: Requirement 15 is P2**
    - **Must not be lost:** the rendered appearance, since an alias and its target are the same value.
      A visible change here means the wrong token was picked
    - **Verification:** per commit, `node node_modules/vitest/vitest.mjs --run tests/unit/guards/no-colour-literals.test.js --fileParallelism=false`,
      then `tests/unit/landing/landingSections.test.jsx`. **`dead-tailwind.test.js` is the guard that
      actually answers "does this class resolve", and it needs a build** — see Notes
    - **Regression test and assertion:** `no-colour-literals.test.js` asserted exactly in both
      directions per file, plus 10.2's `it` for that section green unchanged. **The build-dependent half
      is `dead-tailwind.test.js` in CI**, whose own header records that this surface is where a
      mistyped class silently renders nothing
    - _Requirements: 15.1, 15.4, 18.1, 18.3, 18.4, 22.1, 22.2_

  - [ ]* 11.3 The 9 gradients, against Requirement 10.1's rule
    - **Files:** the landing sections carrying them; `src/styles/tokens.css` **only if** a marketing
      token is declared, in which case that edit is its own commit (Requirement 22.3)
    - Review each against Requirement 10.1's rule. Where a gradient is judged appropriate on a marketing
      surface in a way it is not on a trading surface, **declare it in `tokens.css` as a named marketing
      token** rather than authoring it inline, so the exception is visible and countable
    - This is the one place the marketing-surface exception is permitted. §7.3 rejected it for
      Marketplace specifically: that page is inside the authenticated shell, is in `In_Scope_Pages`, and
      is where a trader commits money
    - A `tokens.css` edit triggers `tokens.generated.test.js` and `prebuild`'s
      `scripts/gen-tokens.mjs --check`, so it lands alone and `npm run tokens` runs with it
    - **Optional for launch: Requirement 15 is P2**
    - **Must not be lost:** nothing removed silently. A gradient that goes is replaced by a declared
      flat surface, exactly as §7.3's table does for Marketplace
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/tokens.generated.test.js --fileParallelism=false`,
      then `tests/unit/landing/landingSections.test.jsx`
    - **Regression test and assertion:** `tokens.generated.test.js` — **fails** the moment
      `src/design/tokens.js` is stale relative to `src/styles/tokens.css`, which is the failure mode this
      commit meets first, and **passes** once the generator has run. Requirement 18.4 names it for
      exactly this reason
    - _Requirements: 10.1, 15.3, 15.4, 18.4, 22.3_

- [ ] 12. Last — standing prose and the fresh-account hierarchy (Requirements 7, 8)

  These go last because they are the pass's only requirements whose target is explicitly a judgement —
  Requirement 7's 400 characters is "chosen, not derived" and Requirement 8's one-next-action target is
  measured against a proxy. They are also the changes most likely to be *judged* a regression, so they
  go against a tree where nothing else is in flight and every mechanical gain has already shipped. And
  a revert of a copy commit must not be able to take a migration with it; last means it cannot.

  **This is where the user's instruction lands most directly, and where the constraint bites hardest.**
  The prose being moved is true and safety-relevant. Requirement 7.2 forbids deleting it or shortening
  it in a way that changes what it asserts; Requirement 19.6 requires it to stay reachable and
  unchanged in substance. **Moving is permitted. Paraphrasing-away is not.**

  - [ ]* 12.1 Write `tests/unit/guards/standing-prose.{budget,test}.js` before any prose moves
    - **Files:** `tests/unit/guards/standing-prose.budget.js` (new),
      `tests/unit/guards/standing-prose.test.js` (new)
    - Nothing measures this today. Requirement 7.5 requires the count to be taken on **rendered text
      rather than source constants**, so this cannot be a `source-scan.js` guard like the other five: it
      renders each of the eight pages against a fixture and sums the text of the nodes standing before
      the first data element
    - **The mechanical definition of "above the first data element", so two implementers count the same
      thing:** the first element carrying a `data-region` attribute, which every `pageFields`-declared
      slot on a migrated page already has (`liveTrading.test.jsx:245` walks them and
      `dashboard-tier2.test.jsx` reads them through `tierSelector`). Text in document order before that
      node is standing prose; text after it is not. That is what makes Requirement 7.1's 400 characters a
      count rather than an opinion
    - Seed at §1.7's measured counts: `LiveTrading` 3175, `SignalTrace` 1351, `Dashboard` 1225,
      `Portfolio` 774, `TradeHistory` 584, `Strategies` 420, `StrategyMarketplace` 186, `Backtester` 157.
      Asserted so it can only decrease
    - **This definition also scopes the budget away from every load-bearing string by construction:** a
      `pageFields.js` `reason` renders *inside* a `data-region`, so it is never counted and can never be
      "reduced" to satisfy this guard. That is deliberate and is stated in the file's header
    - **Must not be lost:** this guard is the instrument that will be used to justify moving text, so its
      scope is the safeguard. It counts only prose that renders **unconditionally, above the first data
      element** — never a `reason`, never `usePanelState`'s `unavailable` copy, never anything that
      renders in one state only. A falling number here must never be achievable by touching any of those,
      and the header says so in those words
    - **Optional for launch: Requirement 7 is P2**
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/standing-prose.test.js --fileParallelism=false`
    - **Regression test and assertion:** this file. **Fails before** if the seed is wrong in either
      direction, which is the measurement being taken. **Passes after:** green at today's counts, so
      every later task in this group has a number to move. Written first so it is observed passing
      against the unmoved tree and observed *falling* afterwards — a budget first written against the
      state it is meant to measure a change against has measured nothing
    - _Requirements: 7.1, 7.4, 7.5, 18.1_

  - [ ] 12.2 Write `tests/unit/pages/freshAccount.test.jsx` before any panel's weight changes
    - **Files:** `tests/unit/pages/freshAccount.test.jsx` (new)
    - Nothing counts visible empty panels today. `tests/unit/dashboard-tier1.test.jsx` already carries
      the zero-data fixture — its header says the fixtures hold an empty account "precisely so that tier
      1 is the only thing on screen" — so the harness exists and the assertion does not
    - Render Dashboard plus Requirement 8.4's four (`Strategies`, `Portfolio`, `TradeHistory`,
      `LiveTrading`) against a zero-data fixture and assert **the count of visible empty panels and the
      count of primary actions** on each. Seeded at today's numbers, so the change is observed
    - **The test does not decide which action is primary.** That is a **[JUDGEMENT]** that goes to the
      requester (see Blocked). The property makes "exactly one" checkable
    - **Must not be lost:** `empty` stays distinguishable from `unavailable`. `ds/EmptyState.test.jsx`
      already enforces the copy half at the primitive — `renders what is missing, why it matters and the
      next action`, `throws in development for each missing field`, `rejects copy that is present but
      blank`, and `requires a clear-filters action for no-match` — so "you have none of these" and "a
      filter is hiding them" cannot be collapsed. This file must not weaken any of that
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/freshAccount.test.jsx --fileParallelism=false`,
      then `tests/unit/ds/EmptyState.test.jsx` and `tests/unit/hooks/usePanelState.test.js`
    - **Regression test and assertion:** this file. **Fails before** task 12.6 on Dashboard: 6 empty
      panels resolve simultaneously at equal weight with no element marked as the next thing to do — so
      the primary-action count is not 1 and the empty-panel count exceeds 3. **Passes after** 12.6 and
      12.7, with the panel-count assertion seeded at today's numbers in between so the file is green when
      it lands and red only where the work has not happened yet
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 18.1, 19.4_

  - [ ]* 12.3 **Progressive disclosure on Live Trading** — 626 characters stop standing between a trader and "what is running"
    - **Files:** `src/pages/LiveTrading.jsx`, `tests/unit/guards/standing-prose.budget.js`
    - `:1324`'s `REGISTRY_CAVEAT` is 384 characters across four sentences and `:1332`'s
      `SELECTION_CAVEAT` is 242, and both render unconditionally as `<p>` elements above the deployment
      list at `:2712` and `:2720`
    - **Stays visible, always:** the operative sentence — *a deployment that is absent from this list is
      UNKNOWN, not stopped* — and the operative half of `SELECTION_CAVEAT`, that selecting a deployment
      re-scopes some things below and not others
    - **Moves behind a disclosure:** *why* the list can be incomplete (the in-process registry, the
      restart, the other worker), and *which* four things selection re-scopes and which three it does not
    - Mechanism: `ds/Accordion` or `ds/Tooltip` on the operative sentence. Both exist and both are
      already keyboard-operable — `Accordion.test.jsx`'s `defaults to closed, and defaultOpen is the only
      way out` reads `aria-expanded` off `getByRole('button', …)`, and `Tooltip.test.jsx` covers
      `opens on focus and closes on blur`, `closes on Escape and reopens on the next focus`, and
      `adds a tab stop to a span, which cannot otherwise take focus`. **Requirement 17.3 forbids a new
      primitive and none is needed.** No animation, no new `--animate-*` token, no height transition
      (Decision D9: the only declared motion tokens are `--transition-fast` and `--transition-base`, and
      a disclosure opens — it does not slide)
    - **Two hard constraints, both already asserted.** `liveTrading.test.jsx:1041` asserts
      `[data-selector-caveat="in-process-registry"]` appears **exactly once**, so the attribute moves
      onto whatever renders the caveat rather than being dropped — and rewriting that assertion to
      tolerate zero is the exact failure Requirement 7.5 names. And the substance is unchanged:
      Requirement 19.6 permits moving and forbids paraphrasing shorter
    - **Where this may not go.** `LiveTrading.jsx:2574` records that the empty rendering deliberately
      uses `ds/EmptyState` as a *child* rather than `ds/Panel`'s `empty` state, because `empty` renders no
      children and the panel would then say "nothing is running" with the sentence explaining why that
      may be false suppressed. A disclosure that is closed in the `empty` state reproduces that trap one
      level up. **So: in the states where the list is empty or partial, the operative sentence renders
      expanded.** The disclosure collapses only where there are rows and the list is complete
    - **Must not be lost:** the caveat is true and safety-relevant — `LiveTrading.jsx:114` records that
      the endpoint reports only what the current backend process holds in memory, and the file's own
      words are that the response is "to stop the list from being read as exhaustive". **A trader who
      reads the list as complete can double-deploy.** Every distinction both caveats draw survives the
      move, reachable by keyboard, with an accessible name and a programmatic expanded state
    - **Optional for launch: Requirement 7 is P2**
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/liveTrading.test.jsx --fileParallelism=false`,
      then `tests/unit/guards/standing-prose.test.js`, `tests/unit/ds/Accordion.test.jsx`,
      `tests/unit/ds/Tooltip.test.jsx` and `tests/unit/pages/freshAccount.test.jsx`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** `standing-prose.test.js` — `LiveTrading`'s rendered count
      **fails before** it is lowered in this same commit (progress recorded, not banked) and **passes
      after**. `liveTrading.test.jsx:1041` — the `data-selector-caveat` count of exactly 1 — must be green
      **before and after, unchanged**: it is the assertion that proves the attribute travelled rather
      than being dropped, and it is the one assertion in this task that may not be edited
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.4, 17.3, 19.6, 20.1, 20.2, 22.1, 22.2_

  - [ ]* 12.4 Move `SignalTrace.jsx`'s standing prose behind disclosure (1351 characters in 14 constants)
    - **Files:** `src/pages/SignalTrace.jsx`, `tests/unit/guards/standing-prose.budget.js`
    - Same mechanism and same constraints as 12.3. The longest constant is
      `REASON_NO_EXCHANGE_RESPONSE` at 253 characters
    - **Must not be lost:** every `REASON_*` constant is an account of why a signal did not reach an
      exchange. That is `design.md §6`'s column 2 — prose whose job is to account for a specific outcome
      — and §6.2's test settles it: remove the string and a trader can no longer tell one failure from
      another. **These move; they are not shortened**, and where one is the explanation *of* an absence
      it stays in the state it describes rather than behind a closed disclosure
    - **Optional for launch: Requirement 7 is P2**
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/standing-prose.test.js --fileParallelism=false`,
      then `tests/unit/signalTraceConnection.test.jsx`, `tests/unit/signalTraceDedup.test.jsx`,
      `tests/unit/signalTraceRealtime.test.jsx` and
      `tests/unit/pages/signalTraceExpansion.property.test.jsx`
    - **Regression test and assertion:** `standing-prose.test.js` — `SignalTrace` falls from 1351 and the
      budget is lowered in the same commit; **fails before** the entry moves, **passes after**. The four
      SignalTrace test files green unchanged is what proves no reason string lost its content
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 19.6, 20.2, 22.1, 22.2_

  - [ ]* 12.5 Move `Dashboard.jsx`'s standing prose behind disclosure (1225 characters in 13 constants)
    - **Files:** `src/pages/Dashboard.jsx`, `tests/unit/guards/standing-prose.budget.js`
    - Same mechanism and same constraints as 12.3
    - **Must not be lost:** the halt confirmation's copy in full. `dashboard-kill-switch.test.jsx`
      asserts the review grid `renders the not-available marker, never a '0', for a figure the server did
      not report` — including that the grid contains no `0` at all when
      `risk.open_positions_count` is `null`, because a count of zero is the safest-looking figure a broken
      positions read could publish. **Requirement 16.5 puts every confirmation out of reach of this
      pass**: no step of it is reworded, moved behind a disclosure, or given less weight
    - **Optional for launch: Requirement 7 is P2**
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/guards/standing-prose.test.js --fileParallelism=false`,
      then `tests/unit/dashboard-kill-switch.test.jsx`, `tests/unit/dashboard-tier1.test.jsx`,
      `tests/unit/dashboard-tier2.test.jsx` and `tests/unit/dashboard-alert-strip.test.jsx`
    - **Regression test and assertion:** `standing-prose.test.js` — `Dashboard` falls from 1225 with its
      entry lowered in the same commit. `dashboard-tier2.test.jsx`'s claim 4 —
      `health.exchange_api_latency_ms === null` is the marker, **never `0 ms`**, with the measured arm
      asserted too "so the absence is a distinction and not a page that never renders latency" — green
      unchanged in both states. That second half is the part a simplification pass can break without
      touching the first
    - _Requirements: 7.1, 7.2, 7.4, 7.5, 16.5, 19.1, 19.2, 19.5, 22.1, 22.2_

  - [ ] 12.6 One next action on a fresh Dashboard (Requirement 8.1, 8.2)
    - **Files:** `src/pages/Dashboard.jsx`, `src/design/pageHierarchy.js` (ordering only, if needed)
    - `Dashboard.jsx` renders 7 `ds/Panel` instances, 6 with an empty branch, and on a fresh account all
      6 resolve to `empty` simultaneously at equal weight with nothing marked as the next thing to do.
      **The primitive already enforces the copy half** — every empty panel already names an action; the
      problem is that six of them do, equally. So this is hierarchy work, not copy work
    - One panel's action is primary. The other five **keep their action** and lose the emphasis —
      `ds/CommandButton`'s non-primary treatment, or a text link, both declared
    - Panels beyond the first three collapse: `ds/Accordion`, or ordering so that the three that matter
      on a fresh account are the three rendered — a `pageHierarchy` question, **not a new mechanism** and
      not a new `ds/*` variant (Requirement 17.2)
    - **Must not be lost:** Requirement 8.3's distinction, verbatim. A panel that is empty because the
      trader has nothing yet is not a panel that could not be read, and Requirement 19.4 forbids
      collapsing them "in pursuit of fewer states on screen". **Reduced weight is not collapse: the state
      name, its copy and its action all stay**, and every one of the five de-emphasised actions keeps its
      accessible name and its keyboard path (Requirement 6.4)
    - **Which panel is primary is a [JUDGEMENT]** and goes to the requester (see Blocked). The proxy is a
      count, not an opinion, and 12.2 is the count
    - **Verification:** `node node_modules/vitest/vitest.mjs --run tests/unit/pages/freshAccount.test.jsx --fileParallelism=false`,
      then `tests/unit/dashboard-tier1.test.jsx`, `tests/unit/dashboard-tier2.test.jsx`,
      `tests/unit/ds/EmptyState.test.jsx` and `tests/unit/hooks/usePanelState.test.js`. Then
      `node node_modules/eslint/bin/eslint.js src`
    - **Regression test and assertion:** `freshAccount.test.jsx` — on Dashboard's zero-data fixture the
      primary-action count is 1 and the visible empty-panel count is ≤ 3. **Fails before** (6 empty
      panels, no single primary) and **passes after**. `EmptyState.test.jsx`'s two-variant
      `EMPTY_VARIANTS` assertion and `requires a clear-filters action for no-match` green unchanged is
      what proves no state was collapsed
    - _Requirements: 8.1, 8.2, 8.3, 17.2, 19.4, 20.1, 20.5, 22.1_

  - [ ] 12.7 The same rule on `Strategies`, `Portfolio`, `TradeHistory` and `LiveTrading` (Requirement 8.4)
    - **Files:** `src/pages/Strategies.jsx`, `src/pages/Portfolio.jsx`, `src/pages/TradeHistory.jsx`,
      `src/pages/LiveTrading.jsx` — **one commit each** (Requirement 22.1)
    - Same hierarchy work as 12.6, page by page: exactly one primary action on a fresh account, no more
      than three simultaneously-visible empty panels, every de-emphasised action still present and still
      reachable
    - **Must not be lost:** on `LiveTrading`, the interaction with 12.3 is the trap — the operative
      caveat sentence renders **expanded** in exactly the states this task is reducing the weight of, so
      an empty deployment list must not end up saying "nothing is running" with the sentence explaining
      why that may be false collapsed behind a disclosure. `LiveTrading.jsx:2574` already records the
      one-level-down version of this mistake having been avoided once
    - **Verification:** per commit, `node node_modules/vitest/vitest.mjs --run tests/unit/pages/freshAccount.test.jsx --fileParallelism=false`,
      then that page's own file — `tests/unit/strategies-table.test.jsx`,
      `tests/unit/portfolio-rendering.test.jsx`, `tests/unit/pages/tradeHistoryFilters.test.js`,
      `tests/unit/liveTrading.test.jsx` — then `tests/unit/ds/EmptyState.test.jsx`
    - **Regression test and assertion:** `freshAccount.test.jsx` per page — one primary action, ≤ 3
      visible empty panels; **fails before** each page's change and **passes after**. Each page's own
      test file green unchanged. On `LiveTrading`, `liveTrading.test.jsx:1041`'s exact count of 1 again,
      which is what ties 12.3 and 12.7 together
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 19.4, 20.1, 22.1_

- [ ] 13. Checkpoint — every guard and design suite green, scoped, and the path checks on every commit

  - [ ] 13.1 Re-run the named suites scoped and confirm the per-commit constraints
    - **Verification:** `node node_modules/vitest/vitest.mjs --run <path> --fileParallelism=false` once
      per file, over every file under `tests/unit/guards/` (16 existing + the two new), every file under
      `tests/unit/design/`, and every file under `tests/unit/ds/`. **Never the bare suite** — it exceeds
      25 minutes (Requirement 18.5), which is why even the duration measurement is taken in scoped
      batches and summed. Then `node node_modules/eslint/bin/eslint.js src`
    - Confirm, for every commit in the pass: `git diff --name-only` shows no path under `src/api/`,
      `src/websocketClient.js`, `backend_app/` (Requirement 16.7) or
      `aerora_quant_platform/frontend_app/algo22-terminal` (Requirement 21.3), and
      `git diff --exit-code algo22-terminal/tests/unit/guards/api-paths.budget.js` reports no change
      (Requirement 18.3 — a diff there means Requirement 16.2 was violated)
    - Confirm no new file appeared under `src/components/ds/` or `src/design/`, and that
      `no-local-tokens.test.js` is green — no page-local `C` / `COLORS` / `THEME` object (Requirement
      17.1–17.4)
    - Confirm `no-placeholders`, `no-native-dialogs` and `no-colour-literals` only decreased, and that
      every cleared font-size entry was **deleted** rather than left at `0`
    - **Regression test and assertion:** `tests/unit/design/pageFields.test.js`,
      `tests/unit/design/reported.test.js` and `tests/unit/ds/Metric.test.jsx` green **unchanged** — no
      edit to any of the three. That is Requirement 19.7's own condition: the availability suites pass
      unchanged and Property P5 (a failed read renders no figure and no zero) holds across every declared
      entry. If one of those three files had to be edited to make this pass green, a reason was shortened
      or a zero was collapsed somewhere upstream
    - _Requirements: 16.2, 16.7, 17.1, 17.2, 17.3, 17.4, 18.1, 18.2, 18.3, 18.5, 18.6, 19.7, 21.1, 21.3_

  - [ ] 13.2 Take the build-dependent checks in CI, and do not report them as verified locally
    - Five things genuinely need a build and none of them is verified by anything above.
      **1.** Class resolution — `dead-tailwind.test.js`. This pass rewrites class names on 14 files plus
      Marketplace plus the landing surface, the largest class-name churn since the redesign, and that
      guard exists for exactly this churn: its own header records nine live dead classes found the day it
      landed, seven of them on `StrategyMarketplace.jsx` — arbitrary-value classes with the opacity slash
      missing (`bg-[#00D4FF]5` for `bg-[#00D4FF]/5`), so the hero glow, both notice banners' wash and the
      subscribe and clone buttons' hover state rendered nothing in the file §1.1 calls "already fully
      Tailwind". **2.** Requirement 2.1's actual behaviour — whether text scales when the browser default
      moves from 16px to 20px needs a real browser; the ratchet proves no px value remains and cannot
      prove the rem values render proportionally. **3.** Requirement 2.5's overflow question, at a
      viewport. **4.** O1 option A's blast radius, only visible in built CSS. **5.** Requirement 13's
      steps 4 and 5
    - `.github/workflows/01-pr-check.yml`'s `frontend-tests` job runs `npm run build` immediately before
      the unit tests, so **CI is where this pass gets checked**. Every commit must reach that workflow
      green before it is called verified
    - **Verification:** in CI, `npm run build` then
      `node node_modules/vitest/vitest.mjs --run tests/unit/guards/dead-tailwind.test.js --fileParallelism=false`
      — in that order, because the reverse reports green having checked nothing. Locally, record the skip
      rather than the pass
    - **Disk is currently too low to build locally** — see Notes. Nothing above depends on a build, and
      nothing above is reported as covering the five items here
    - **Regression test and assertion:** `dead-tailwind.test.js` in CI, after a build. Locally it
      **skips with a notice** and reports green having checked nothing, so a local green on that file is
      not a result
    - _Requirements: 2.1, 2.5, 18.4, 18.5_

## Notes

- **Tooling is not interchangeable here, and every substitution below has a recorded failure.**
  PowerShell on Windows, run from `algo22-terminal/`.
  `node node_modules/vitest/vitest.mjs --run <path> --fileParallelism=false` — always scoped to a named
  file, **never the bare suite**, which exceeds 25 minutes (Requirement 18.5) and is why even
  `production-launch-hardening`'s own duration measurement is taken in scoped batches and summed.
  `node node_modules/eslint/bin/eslint.js src` — `npx` swallows stdout, so an error count read through
  it is not a count. `--fileParallelism=false` is passed explicitly even though
  `algo22-terminal/vitest.config.js` already sets `fileParallelism: false` / `maxWorkers: 1`: parallel
  fork workers fail their startup handshake on constrained hosts and whole files then silently never
  run, the render-heavy `tests/unit/ds` files depend on it, and an explicit flag is what stops a CLI
  override reintroducing the contention.
- **`git commit -q -m` takes ONE `-m` on a SINGLE LINE.** A multi-line `-m` and `git commit -F <file>`
  both fail silently on this machine, which is how a commit can appear to succeed and not exist. Every
  "MUST LAND ALONE" task is committed that way, on its own, so reverting it reverts nothing else.
- **`tests/unit/guards/dead-tailwind.test.js` reads `dist/assets/*.css` and SKIPS when unbuilt, so it
  must run AFTER a build or it reports green having checked nothing.** With no build, `STYLESHEETS` is
  empty, `HAS_BUILD` is false, and `needsBuild()` calls `ctx.skip()` with the one-line reason
  `no dist/assets/*.css — run \`npm run build\` first` — producing a green file and `6 skipped`. Its
  header is explicit about why it skips rather than fails (a missing artefact is not a Requirement 1.1
  violation, and `npm test` must not silently require `npm run build`) and why it does not pass quietly
  ("which would let a CI job that forgot to build report green on a guard that checked nothing"). The
  skip notice travels with each test, but **a reader scanning for red sees green.** It is the only guard
  with this property: every other file under `tests/unit/guards/` reads `src/` or the repository root
  and carries a non-vacuity assertion, and task 1.2 gives the new ratchet its own.
- **Disk is currently too low to build.** No production bundle can be produced during this pass, so
  `dead-tailwind.test.js`, Requirement 2.1's real scaling behaviour, Requirement 2.5's overflow question,
  O1 option A's blast radius and Requirement 13's steps 4–5 are all deferred to CI or to the requester
  (task 13.2). Nothing else in this plan depends on a build:
  `tokens.generated.test.js` compares `src/design/tokens.js` against `src/styles/tokens.css` through the
  generator, all three in `src/`, and `package.json`'s `prebuild` runs `scripts/gen-tokens.mjs --check`
  as a second gate — so a token edit that forgets `npm run tokens` fails scoped, immediately, with a
  message naming the staleness.
- **`aerora_quant_platform/frontend_app/algo22-terminal` is never modified** (Requirement 21). The tree
  contains a near-identical `src/pages/` there, and §2's mapping is exactly the kind of work that
  produces a codemod — a codemod is exactly what lands in both trees. The `git diff` path check belongs
  on **every** commit, not on the pass (task 13.1).
- **No test in this repository can observe a rendered font size, and this is stated once so no task is
  read as covering it.** jsdom applies no Tailwind CSS — `ds/DataTable.test.jsx`'s header says so
  directly and explains that it reads `data-align`, `data-column-key`, `data-row-id` and `data-priority`
  rather than computed styles for that reason. None of the 919 declarations across
  `tests/unit/{guards,design,ds}/` asserts a rendered size. The only font-size assertion anywhere is
  `tests/unit/portfolio-rendering.test.jsx:116` — `it('declares no inline style, font or fontSize')`,
  which reads `Portfolio.jsx`'s *source text*. That is the proof that §3's guard is the only available
  shape for this axis: the property is not observable at render time, so it has to be asserted over
  source, and §2.5's three-part proxy carries the rest.
- **Growth is the normal case, not the exception.** 41 of `PaperTrading.jsx`'s 43 sizes sit at or below
  11px and the floor is 10px, so almost every resolution is upward and the layout is what yields
  (Requirement 2.5). **A pass that produces no layout changes has not done the work.** A shrink is legal
  but rare; it is illegal when it is chosen to avoid a layout change, and the tell is a shrink appearing
  in the same diff as an overflow that would otherwise have needed a column removed.
- **Two corrections to `requirements.md` are carried by this plan rather than applied silently**, both
  from `design.md §3.5` and both needing the requester's acknowledgement before task 1.2 lands:
  `PaperTrading.jsx` measures **51**, not 43 (the eight `fontSize={9}` recharts axis props, a fourth
  syntax none of Requirement 1.2's three named patterns reaches), and **eleven files under
  `components/` carry 155 sizes** with no seed entry — including `ds/Chart.jsx`'s 2, the one declaration
  behind every chart on eleven pages. Five further corrections are recorded in `design.md`'s own
  corrections table; the two that change a task's content are named inline: `:477` is an 11px table cell
  rather than a 9px label (task 8.1), and Requirement 2.3's five explanation call sites are two lines
  earlier than cited — `:2885`, `:3283`, `:3407`, `:3434`, `:3488` (task 8.1).
- **`optional for launch` is not `reorderable`.** A task marked `*` has a P2 or P3 driving clause, so
  launch does not block on it. Its position in §4.3's order still holds, because the ordering argument is
  about bisect and work-in-flight — the badge going first costs nothing and going last would make every
  later page regression ambiguous.
- **The list is in dependency and diagnostic order, not severity order.** §4.1 and §4.2 give the full
  argument: the ratchet is commit 1 because the interval between commit 3 and commit 9 is what needs
  protecting and a guard first written against a clean tree is a guard whose patterns were never
  exercised; the badge is commit 2 because its blast radius is already covered by a suite that exists
  today, so the largest-blast-radius-last rule's premise does not hold.

## Blocked

Two open items block named tasks. Neither is an unfinished task, and neither becomes unblocked by being
re-run.

### OPEN DECISION O1 — Tailwind's 434 built-in size classes (`design.md §7.6`, §3.6 blind spot 2)

`src/` carries **434** occurrences of `text-xs` / `text-sm` / `text-base` / `text-lg` / `text-xl` /
`text-2xl` and their siblings outside tests — **41 in `StrategyMarketplace.jsx`**, 43 in
`ScreenshotsSection.jsx`. They resolve: `tokens.css:14`'s `@theme static` block declares the seven steps
but does not reset the namespace with `--text-*: initial`, so Tailwind v4's defaults are *extended*
rather than replaced and the built stylesheet emits `.text-xs` alongside `.text-micro`.

This is a gap in Requirement 1 rather than in the guard, because Requirement 1 conflates two
properties: *does this size scale with the reader?* (Requirements 2.1, 2.2 — the px ratchet measures it
exactly) and *is this size one of the seven steps?* (Requirements 1.1, 1.2 — the px ratchet does not
measure it at all). `text-xs` is 0.75rem: it scales, it clears the floor, and it is not a step.

| Blocks | Why |
| --- | --- |
| **1.2** — seeding the ratchet | §7.6: the decision is needed "before commit 1 is seeded, **because the seed's shape depends on the answer**". It decides what the budget counts: px only (options B, C) or px plus the built-ins (option B's second budget), and whether a `tokens.css` reset lands first (option A). |
| **5.2, 5.3** — Marketplace commits 5–6 | §7.6: "**nothing in §7 proceeds past axis 1 without it**". Marketplace's real off-scale count is **71**, not the 30 Requirement 10.4 names. The 41 built-ins are the page's entire visual hierarchy — `text-4xl font-black` on the hero `<h1>` at `:1038`, `text-3xl` on the detail price at `:901`, `text-xl` on the card title at `:679`, `text-lg` on the four featured-card metrics — and "cleared" cannot be defined for this page until the decision lands. |
| **6.1** — Marketplace commit 7 | Same clause. §7.6: option "B is the only option that lets commits **5–7** land at all without either a global token change or a narrowed requirement." 6.1 declares no size itself, so under B or C it unblocks immediately; under A it waits for the token-layer commit. |

The three options and their costs are in §7.6 and are not re-litigated here. **§7.6's recommendation,
for the record and not as a decision: option B**, scoped so Marketplace's entry is 71 from the start,
with option A filed as the follow-up it is. Option A breaks all 434 call sites at once, app-wide,
including the eleven pages that are otherwise finished — which is not a per-page increment and collides
with Requirement 22.1 — and its blast radius is only visible in built CSS, which this pass cannot
produce.

### The landing symptom — blocks `design.md §8` past step 3

Requirement 13 is the spec's only **[UNVERIFIED]** requirement. Tasks 10.1 (step 1), 10.2 (step 2) and
10.3 (step 3's `git log` check, the recording, and the question) all proceed now and need nothing from
the requester. **§8.2's steps 4 and 5 do not:**

- **Step 4, the deployed bundle** — `HEAD` on the referenced asset paths, checking whether the reported
  symptom is a 403 like the four installers were. That fault has a known mechanism on this surface:
  `aws s3 sync dist/ --delete` deletes any prefix `dist/` does not carry, so a directory CI never builds
  is removed from the bucket on every deploy. It is invisible to every test in the repository.
- **Step 5, a viewport sweep** — `DesktopOnlyOverlay` gates the app below 1000px and the landing surface
  is outside that gate, so it is the one surface a visitor reaches on a phone. *Identifying* a mobile
  layout fault is Requirement 13's job even though fixing it is out-of-scope item 6, and that
  distinction has to be recorded rather than assumed.

Both need the specific answer task 10.3 asks for: which of a layout fault at a viewport, a missing or
403 asset, or incorrect content, and at what width. **Task 11 (Requirement 15) is gated on 10.3 being
recorded, not on a symptom being found** — Requirement 13.5 makes a failed reproduction a result, and
Requirements 14 and 15 then proceed on their own merits.

### Two judgements that need sign-off inside their task, and default rather than block

- **Task 5.2 — whether the per-card "Selected" badge is kept.** Requirement 10.5's second arm permits
  removing the per-card marker and letting the section heading carry the basis; it loses one thing, that
  a card reached from search or the full catalogue then looks identical whether or not it is featured.
  **Defaults to keeping the badge** (Decision D6), because Requirement 16.3 forbids changing the set of
  things a trader can see and silently dropping a distinction the server draws is closer to that than
  keeping it in a calmer form.
- **Task 12.6 — which Dashboard panel's action is primary.** A **[JUDGEMENT]** that goes to the
  requester. The proxy is not: task 12.2 makes "exactly one" a count rather than an opinion, so the
  property is checkable before the choice is made.

## Task Dependency Graph

### The spine

```text
  1 (RATCHET · seeded, alone) ─┬─▶ 2 ─▶ 3 ─▶ 4 ─▶ 5 ─▶ 6 ─┬─▶ 7 (ten pages) ─┬─▶ 8 ─▶ 9 ─┐
                               │      ▲              O1    │                  │           │
                               │      └── 3 gates 8.1      └── 4 gates 7.x    │           ▼
                               │                                             │          11 ─▶ 12 ─▶ 13
                               │                                             │           ▲
 10 (LANDING INVESTIGATION) ───┘  parallel from day one, no page diff  ───────┴───────────┘
    10.1 ─▶ 10.2 ─▶ 10.3 ─▶ [steps 4–5 BLOCKED: the symptom]        10.3 gates 11
```

- **1 is first and alone.** It touches no source, so it costs nothing to land — and seeded at today's
  counts it asserts the tree as it is and is green on the first commit (Requirement 22.4). Landing it
  last, seeded at zero, would provide no protection during the only period when protection matters, and
  a guard first written against a clean tree is a guard whose patterns were never exercised:
  `no-colour-literals`' `href="#features"` false positive (`#fea` is three valid hex digits) was found
  because that guard ran against a dirty tree on the day it landed.
- **10 ∥ everything.** The investigation depends on no commit and emits no page diff, so it has no
  ordering constraint and starts alongside task 1. It has the longest lead time and is the only
  **[UNVERIFIED]** item; sequencing it last would treat it as a change and risk the pass ending with the
  one thing the requester actually asked about unanswered.
- **3 gates 8.1.** `ds/Chart.jsx` holds the chart-tick treatment for every chart on eleven pages, so
  `PaperTrading.jsx`'s eight axis props take the declaration commit 3 moved onto the token. Reversed,
  they get resolved twice.
- **4 gates 7.2–7.11.** The a11y scope extension turns `jsx-a11y` to error on 25+ files at once and
  seeds each newly-linted page's waiver with the task that clears it; every page commit after it deletes
  its own entry. Landing the extension inside a page commit would put the other 24 files' findings in
  that page's PR.
- **10.3 gates 11.** Requirement 15's token migration is gated on Requirement 13's recording, not on a
  symptom — a recorded null result unblocks it (Requirement 13.5).
- **12 is last, and that is a revert property rather than a preference.** Requirements 7 and 8 are the
  only changes whose target is explicitly a judgement, they are the ones most likely to be *judged* a
  regression, and a revert of a copy commit must not be able to take a migration with it.

### Inside each group

```text
 1  RATCHET                   1.1 ──▶ 1.2 ▲          1.2 BLOCKED by O1 (the seed's shape)
 2  BADGE                     2.1 ──▶ 2.2 ▲          D2: the test precedes the edit and block 4 is seeded at today's four
 3  ds/Chart.jsx              3.1 ──▶ 3.2 ▲          D2: the `it` is observed failing on `fontSize: 10`
 4  A11Y SCOPE                4.1 ▲                  one commit: the scope extension and D7's derived total go red together

 5  MARKETPLACE 10 + 12       5.1 ──▶ { 5.2 ──▶ 5.3 } 5.2, 5.3 BLOCKED past axis 1 by O1
                              5.2 and 5.3 land as a pair (§4.3's "12 with 10"): a card rebuilt onto a
                              primitive with the bare `onClick` div still attached is the worst of both
 6  MARKETPLACE 11            6.1 ▲                   after 5.3; BLOCKED by O1 per §7.6

 7  TEN PAGES                 7.1 ──▶ 7.2 ──▶ 7.3 ──▶ … ──▶ 7.11    strictly serial: Requirement 4.5's order,
                              smallest first, so the convention's fit is tested on 438 lines before 1,181.
                              7.1 precedes 7.2 (Req 6.5: "before and after their migration")

 8  REQ 4.6's THREE           8.1 ∥ 8.2 ∥ 8.3         no interdependency; 8.1 needs 3
 9  DEAD CODE                 9.1 ∥ 9.2               both alone (Req 14.3); 9.2 is Req 4.5's eleventh page

10  INVESTIGATION             10.1 ──▶ 10.2 ──▶ 10.3   cheapest-first, and each step's outcome is a fork:
                              a positive result at any step stops the sequence and files under 13.6
11  LANDING TOKENS            11.1 ∥ 11.2 ∥ 11.3       per file, one commit each; 11.3 alone if it edits tokens.css
12  PROSE + HIERARCHY         { 12.1 ∥ 12.2 } ──▶ { 12.3 ∥ 12.4 ∥ 12.5 } ──▶ 12.6 ──▶ 12.7
                              D2 again: both budgets are seeded and observed green before anything moves.
                              12.7's LiveTrading commit is where 12.3's expanded-in-empty rule is retested
13  CHECKPOINT                13.1 ──▶ 13.2            13.2 is CI's, not this machine's
```

```text
 legend   ──▶   must complete before
           ∥    no dependency — may run concurrently
           ▲    MUST LAND ALONE: its own commit, `git commit -q -m` with one -m on one line
                (1.2, 2.2, 3.2, 4.1, 5.3, 6.1, and every task in 7, 8 and 9)
           O1   blocked on OPEN DECISION O1 — see Blocked
```

### Machine-readable form

Each wave is the **earliest** position a task can be scheduled. Serialising in list order is always
valid; the reverse is not.

```json
{
  "waves": [
    { "id": 0,  "tasks": ["1.1", "10.1"] },
    { "id": 1,  "tasks": ["1.2", "10.2"] },
    { "id": 2,  "tasks": ["2.1", "10.3"] },
    { "id": 3,  "tasks": ["2.2"] },
    { "id": 4,  "tasks": ["3.1"] },
    { "id": 5,  "tasks": ["3.2"] },
    { "id": 6,  "tasks": ["4.1"] },
    { "id": 7,  "tasks": ["5.1"] },
    { "id": 8,  "tasks": ["5.2"] },
    { "id": 9,  "tasks": ["5.3"] },
    { "id": 10, "tasks": ["6.1"] },
    { "id": 11, "tasks": ["7.1", "8.1", "8.2", "8.3", "9.1", "9.2", "11.1", "11.2", "11.3"] },
    { "id": 12, "tasks": ["7.2"] },
    { "id": 13, "tasks": ["7.3"] },
    { "id": 14, "tasks": ["7.4"] },
    { "id": 15, "tasks": ["7.5"] },
    { "id": 16, "tasks": ["7.6"] },
    { "id": 17, "tasks": ["7.7"] },
    { "id": 18, "tasks": ["7.8"] },
    { "id": 19, "tasks": ["7.9"] },
    { "id": 20, "tasks": ["7.10"] },
    { "id": 21, "tasks": ["7.11"] },
    { "id": 22, "tasks": ["12.1", "12.2"] },
    { "id": 23, "tasks": ["12.3", "12.4", "12.5"] },
    { "id": 24, "tasks": ["12.6"] },
    { "id": 25, "tasks": ["12.7"] },
    { "id": 26, "tasks": ["13.1"] },
    { "id": 27, "tasks": ["13.2"] }
  ],
  "blocked": {
    "O1": ["1.2", "5.2", "5.3", "6.1"],
    "landing-symptom": ["design.md §8 steps 4 and 5"]
  },
  "optional": ["2.2", "9.1", "9.2", "11.1", "11.2", "11.3", "12.1", "12.3", "12.4", "12.5"],
  "mustLandAlone": ["1.2", "2.2", "3.2", "4.1", "5.3", "6.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "8.1", "8.2", "8.3", "9.1", "9.2"]
}
```
