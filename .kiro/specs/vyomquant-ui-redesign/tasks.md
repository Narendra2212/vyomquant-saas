# Implementation Plan: VyomQuant UI Redesign (v1)

## Overview

This plan implements `design.md` against `algo22-terminal/` in the **exact M1→M9 order of
`design.md §14.2`**, because that order was chosen so the app stays shippable at every step and
never looks half-migrated (`§14.3`, `§14.4`). Implementation language: **JavaScript / JSX** for the
frontend (the design states this in its Overview), **Python** for the six backend read-projection
changes in `§16`.

The six backend changes **BC-1…BC-6 are in scope**. They are all additive read-projection
changes: none touches order execution, risk-control, strategy-versioning, auth or billing logic
(`§16`, `§17.1`, Requirement 19.1). They land in Task 12, **before** every frontend task that
consumes them, so no field ships its `not-available` state as a permanent outcome:

| Change | Landed by | Consumed by |
| --- | --- | --- |
| BC-1 current drawdown | 12.1 | 16.1 Portfolio tier 1, 19.1 Dashboard tier 1 |
| BC-2 `degraded` positions marker | 12.2 | 13.1 Portfolio positions, 19.1 Dashboard |
| BC-3 `last_signal_at` | 12.3 | 17.1 Strategies table |
| BC-4 `last_execution_at` | 12.4 | 17.1 Strategies table |
| BC-5 lifetime `realized_pnl` | 12.5 | 16.1 Portfolio tier 1 |
| BC-6 `POSITION_UPDATED` event | 12.6 | 21.1 Signal Trace stage 9 |

The `not-available` marker therefore remains only where `design.md §7` marks a field **❌**
(per-deployment realised P&L, `§7.5`) — a gap with no registered backend change — and where a
server genuinely reports nothing (`null` latency, `null` liquidation price, absent
`dag_nodes` past the trace store's retention window).

### Ordering hazards resolved in this plan

Three places where the design's step boundaries would break a shippable intermediate state, and
the decomposition this plan uses instead:

1. **`api/modules/portfolio.js`.** `§14.2` places "remove the dead `portfolio.js` methods" in M5,
   but `Portfolio.jsx:302` calls `getOpenPositions()` with a `getPositions()` fallback. Removing
   all eight in M5 turns a 404 into a `TypeError`. Task 10.10 removes the **six** methods with no
   call site; Task 13.1 removes the remaining two in the same change that re-points the read at
   `/api/dashboard`.
2. **`index.css`'s three hardcoded `@utility` blocks** (`kpi-card`, `table-container`,
   `input-institutional` — the "sixth surface set" of `§1.1 G5½`). `grep` confirms **zero**
   `className` call sites for all three, so Task 1.4 deletes them in M1 rather than deferring to
   M3 as `§3.2` implies. Nothing to re-point.
3. **`no-native-dialogs` scope.** `§15.1` states the guard asserts zero
   `window.confirm/alert/prompt` across `src/pages/**` and `src/components/**`, but `TwoFA.jsx`
   and `NotificationCenter.jsx` are explicitly out of scope (`§17.2`) and each holds one
   `window.confirm`. Task 10.11 ships the guard with a two-file out-of-scope allowlist that is
   itself asserted to be exactly those two files, so a new native dialog anywhere else fails CI.

### Rules that apply to every task

- **No task modifies order execution, risk-control, strategy-versioning, auth or billing logic**
  (Requirement 19.1, `§17.1`). BC-1's replacement of `risk.py::/status`'s literal
  `"drawdown_pct": 0.0` with an honestly-computed value or `null` is a read projection, not a
  control change.
- **No task leaves a `TODO`, `FIXME`, "coming soon", non-functional button, link or dropdown
  behind** (Requirement 19.4). Where a task removes a placeholder or a dead control, it says so.
- **No page authors a colour literal, and no page imports `C` that did not already import it.**
  `C` may only shrink (`§3.4`).
- **No new dependency except `fast-check`** (`§17.3`). Four unused dependencies are removed.
- **Every page task lowers that page's entries in the `legacy-c-budget` and `no-colour-literals`
  budgets in the same change**, so `§14.4`'s monotonic-progress guarantee is checkable.
- Tasks marked `*` are optional test-writing tasks and can be skipped for a faster MVP; every
  other task is required.

## Tasks

- [ ] 1. M1 — Token foundation: one source of truth, calm by default

  - [x] 1.1 Create `algo22-terminal/src/styles/tokens.css` as the sole `@theme` token source
    - Author every group in `design.md §3.2` verbatim: surface, line, content, brand, semantic
      status (live, connected, profit, loss, error, warning, guidance, neutral — each with a
      `-wash`), environment (live/paper/backtest), typography (`--font-sans`, `--font-mono`, the
      seven-step `--text-*` scale with line heights), the 4px spacing scale, radius, the three
      shadow levels with **no coloured glows**, the two transitions, four breakpoints, seven
      z-index steps, and the focus-ring triplet
    - Keep `--color-accent-profit` / `--color-accent-loss` as **aliases** of
      `var(--color-status-profit)` / `var(--color-status-loss)` so the out-of-scope landing page
      keeps compiling while ceasing to diverge (`§3.2`)
    - Add the single `:where(a, button, input, select, textarea, summary, [tabindex]):focus-visible`
      rule and the `prefers-reduced-motion` block from `§3.2`
    - Annotate `--color-content-muted` as NON-TEXT ONLY (3.2:1) in a comment
    - _Requirements: 1.1, 1.4, 18.2_

  - [x] 1.2 Create `scripts/gen-tokens.mjs` and the generated `src/design/tokens.js`
    - Parse the `@theme` block's `--name: value;` pairs, group by prefix, emit the frozen nested
      `token` object plus the `cssVar(path)` helper exactly as `§3.3` sketches, with the
      `AUTO-GENERATED … DO NOT EDIT` header; no dependencies
    - Support `--check`, which exits non-zero when `design/tokens.js` is stale
    - Add `"tokens": "node scripts/gen-tokens.mjs"` and
      `"prebuild": "node scripts/gen-tokens.mjs --check"` to `algo22-terminal/package.json`
    - _Requirements: 1.1_

  - [x] 1.3 Rewrite `C` in `src/components/ui-legacy/primitives.jsx` as a derived frozen shim
    - Replace every literal with a `token.*` reference per `§3.4`: `bg/bg0…bg4`, `border*`,
      `cyan/accent*/blue`, `profit/green*`, `loss/red*`, `t1…t4`, one amber for both `warning`
      and `gold`, `purple`/`orange` mapped to neutral/warning, `shadow*`, `space`, `radius`
    - Set every `C.glow.*` and `C.gradient.*` to `'none'` — this is what satisfies Requirement 1.5
      app-wide on the first commit with zero page edits (`§3.4`)
    - `Object.freeze` the object and add the `vyom/no-new-legacy-token` ESLint rule (or an
      equivalent `no-restricted-syntax` entry in `eslint.config.js`) so a new key is a lint error
    - _Requirements: 1.1, 1.4, 1.5_

  - [-] 1.4 Rewire `src/index.css` onto the single source
    - Reduce it to `@import "tailwindcss"; @import "./styles/tokens.css";` plus base resets
    - Delete the duplicated "existing app palette" and "landing page design system" palettes —
      profit and loss currently have two different values in this one file (`§1.1 G2`)
    - Delete the `kpi-card`, `table-container` and `input-institutional` `@utility` blocks (the
      sixth surface set, `§1.1 G5½`); `grep` confirms zero `className` call sites, so this
      removes a competing palette with no re-pointing
    - Move the `* { box-sizing }` reset out of `AppShell`'s inline `<style>` into `tokens.css`
    - _Requirements: 1.1_

  - [-] 1.5 Delete `algo22-terminal/tailwind.config.js`
    - It is never loaded: `package.json` pins Tailwind v4 with `@tailwindcss/postcss`, `index.css`
      has no `@config`, and `grep` finds `@config` nowhere in `src/` (`§1.2`, verified against the
      built stylesheet)
    - Its type scale and shadow definitions are already carried by `tokens.css` from task 1.1
    - _Requirements: 1.1_

  - [-] 1.6 Delete the two page-local `C` objects
    - `src/components/SupportCenter.jsx` (`bg #010608`) and
      `src/components/NotificationCenter.jsx` (`bg #0a0a0a`) each declare their own `C`; replace
      both with an import of the shim
    - Layouts are unchanged — these pages are out of scope for redesign; only the token source is
      corrected, so backgrounds become `#080A0E` (`§17.2`)
    - _Requirements: 1.1_

  - [x] 1.7 Dependency and build-tooling hygiene
    - Remove `ag-grid-community`, `ag-grid-react`, `react-grid-layout` and `lightweight-charts`
      from `algo22-terminal/package.json` — `grep` finds zero imports of any of them in `src/`
      (`§13.3`, `§16`) — and delete their `manualChunks` entries from `vite.config.js`
    - Add `fast-check` as a devDependency, pinned; it is the only new dependency this design
      permits (`§17.3`) and every property task below needs it
    - Delete the committed build detritus at the `algo22-terminal` root: `bundle-analysis.html`
      (1.4MB), the 17 `fix_*.cjs`/`fix_app.js` scripts, `rescue.cjs`, `simple_direct_fix.cjs`,
      `comprehensive_fix.cjs`, `report.txt`, `vitest-run.log`, or gitignore them
    - _Requirements: 19.4_

  - [x] 1.8 Reconcile the design documentation with this initiative's direction
    - Add the superseding banner from `design.md`'s "What this design is not" to the top of
      `ALGO22_UX_MASTER_AUDIT.md`, `IMPLEMENTATION_ROADMAP.md` and `FINAL_UX_CERTIFICATION.md`,
      so a future contributor does not reintroduce dopamine loops, leaderboards, achievement
      tiers or ticker "aliveness" into a live-trading cockpit
    - Amend `DESIGN_SYSTEM_V2.md` per `§3.5`: withdraw the "Token Source of Truth: `primitives.jsx`
      exports `const C`" directive in favour of `styles/tokens.css`; replace the "hover states add
      glow" / "subtle micro-animations" directives with `§4.3`'s calm-by-default rule; correct the
      contrast figures from 7.94:1 and 14.2:1 to the measured **11.2:1** and **16.8:1** and add
      `#5A6578` at 3.2:1 with its non-text-only annotation
    - _Requirements: 1.1, 1.5_

  - [ ]* 1.9 Write the `tokens.generated` CI guard
    - `algo22-terminal/tests/unit/guards/tokens.generated.test.js`: run `scripts/gen-tokens.mjs`
      in-process against `styles/tokens.css` and assert the output equals the committed
      `design/tokens.js` byte for byte, so a stale mirror fails CI (`§15.1`)
    - _Requirements: 1.1_

  - [ ]* 1.10 Write the `no-colour-literals` CI guard with its decreasing allowlist budget
    - `tests/unit/guards/no-colour-literals.test.js`: zero hex / `rgb()` / `hsl()` literals in
      `src/pages/**` and `src/components/**`, excluding `styles/tokens.css`, `design/tokens.js`
      and a checked-in allowlist of out-of-scope files
    - Assert the allowlist is a **decreasing budget**: each entry carries a count, the test fails
      if any file exceeds its count, and it also fails if a file is *under* its count without the
      budget being lowered — that is what makes `§14.4`'s monotonic progress a check rather than
      a promise
    - Seed the budget from the current counts in `Dashboard.jsx`, `SignalTraceVisualization.jsx`,
      `TradeHistory.jsx` and `ErrorBoundary.jsx` (`§1.1 G5`)
    - _Requirements: 1.1, 1.3_

  - [ ]* 1.11 Write the `no-local-tokens` CI guard
    - `tests/unit/guards/no-local-tokens.test.js`: no `const C =`, `const COLORS =` or
      `const THEME =` anywhere in `src/` except `ui-legacy/primitives.jsx` (`§15.1`)
    - _Requirements: 1.1_

  - [ ]* 1.12 Write the `legacy-c-budget` CI guard with its per-step decreasing budget
    - `tests/unit/guards/legacy-c-budget.test.js`: count `C.` references per file and assert each
      is `<=` a checked-in per-file budget, seeded from today's counts (`PaperTrading.jsx` 191,
      `StrategyBuilder.jsx` 148, `SignalTrace.jsx` 92, `Backtester.jsx` 66, `Portfolio.jsx` 5,
      `Strategies.jsx` 1, plus the 178 inside `primitives.jsx` itself)
    - Every migration step below lowers the budget for the files it touches; the guard reaches
      zero at task 27.2, after which the guard file is deleted with the shim
    - _Requirements: 1.1, 1.3_

- [~] 2. Checkpoint — M1 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - Expected visible change, which the release note must state so it is not read as a regression:
    every glow and gradient wash disappears app-wide, two ambers become one, `purple`/`orange`/
    `#2962FF` leave the palette, and focus rings appear on every control.

- [ ] 3. M2 — Type scale and shell font

  - [~] 3.1 Replace the 143 dead typography classes with live token classes
    - `tailwind.config.js` was never loaded, so `text-micro`, `text-caption`, `text-caption-sm`,
      `text-body`, `text-body-sm`, `text-body-lg`, `text-heading*`, `shadow-glow` and
      `shadow-glow-green` compile to nothing today and every one of those 143 elements renders at
      the inherited size (`§1.2`, verified against `dist/assets/index-CO7MvKd_.css`)
    - Re-point all 143 onto the `tokens.css` scale in: `pages/StrategyBuilder.jsx` (49),
      `components/builder/AssetSelector.jsx` (20), `pages/Portfolio.jsx` (18),
      `components/builder/NodeTrace.jsx` (17), `components/builder/NodePreview.jsx` (17),
      `components/builder/ParameterForm.jsx` (16), `components/builder/TimeframeSelector.jsx` (6)
    - Remove `hover:shadow-glow` from `components/ui/Card.jsx` — it is inert and Requirement 1.5
      retires coloured glows
    - _Requirements: 1.1, 1.5_

  - [~] 3.2 Move font loading into `algo22-terminal/index.html`
    - Add `<link rel="preconnect">` plus stylesheet links for Inter and JetBrains Mono
    - Delete the `@import` of IBM Plex Mono from the `<style>` tag rendered inside `AppShell` —
      an `@import` inside a rendered `<style>` blocks and re-evaluates on every shell render
      (`§6.6`)
    - _Requirements: 1.1_

  - [~] 3.3 Switch the shell default from monospace to Inter, reserving mono for numerics
    - Remove `fontFamily: "'IBM Plex Mono', 'Fira Code', monospace"` from the `AppShell` wrapper in
      `src/App.jsx`; the shell default becomes `--font-sans`
    - Apply `--font-mono` only to numeric cells, identifiers, timestamps and code — which is what
      `DESIGN_SYSTEM_V2.md` already specified and what `§6.6` restores
    - _Requirements: 1.1_

  - [ ]* 3.4 Write the `dead-tailwind` CI guard
    - `tests/unit/guards/dead-tailwind.test.js`: extract every `className` token used in `src/`
      and assert each resolves in the built stylesheet under `dist/assets/*.css`; the test runs
      after `npm run build` and skips with an explicit message when no build output is present
    - This is the test that would have caught `§1.2`, and it is only enforceable now that the 143
      dead classes are gone
    - _Requirements: 1.1_

- [~] 4. Checkpoint — M2 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - Expected visible change: 143 elements gain their intended size and the app stops rendering
    entirely in monospace.

- [ ] 5. M3 — Semantic layer, error translation and hooks

  - [~] 5.1 Create `src/design/semantic.js` — the one state→colour mapping
    - `statusToken(state)` with the flattened `VOCABULARY` table from `§4.1`, total over any input:
      an unrecognised value, empty string, `null` or non-string resolves to `neutral`, never
      `undefined`
    - `pnlToken(value)` treating **zero as neutral** — `PnLBadge`'s current `value >= 0` renders a
      flat position in profit green (`§4.1`)
    - `ENVIRONMENT` for LIVE/PAPER/BACKTEST with label, long form, icon and border style, plus
      `environmentTreatment(value)` returning `null` when the server did not say, and
      `resolveEnvironment({serverEnvironment, isSimulated, backtestContext})` from `§8.1` with no
      default branch
    - Export the stage-band map from `§9.1` (five bands over the backend's seven `BlockCategory`
      values, unknown category → a neutral sixth "Unresolved" band)
    - This is the only module in the app that may map a state to a colour
    - _Requirements: 1.4, 4.1, 7.4, 8.5, 12.3_
    - _Property: P1, P22_

  - [ ]* 5.2 Write the property test for the status mapping
    - **Property 1: The status colour mapping is total and its semantic groups are distinct**
    - **Validates: Requirements 1.4**
    - Generate the declared vocabulary plus arbitrary strings, the empty string, `null` and
      non-strings; assert every result is a defined `{group, fg, wash}` whose tokens exist in
      `design/tokens.js`, that out-of-vocabulary input is always `neutral`, and that the six
      requirement-named groups resolve to six **distinct group names**, each backed by a declared
      `token.status.*` entry — so a consumer can always tell them apart even where two share a hue
    - Minimum 100 iterations; tag `Feature: vyomquant-ui-redesign, Property 1`

  - [~] 5.3 Create `src/design/errorCopy.js` with `translateError`
    - `CODE_COPY` for every backend error code the in-scope pages can receive and `CATEGORY_COPY`
      for the five `ApiError` categories, both verbatim from `§12`; resolution order is
      code → `detail.reasons[]` → WebSocket refusal code → category → context default
    - Implement the `FORBIDDEN` scrubber (bare HTTP status, any `*Error`/`*Exception` identifier,
      V8 stack frames, Python traceback markers, internal `/api/` URLs): throw in dev, degrade to
      category copy in production
    - Attach `supportRef` from `error.requestId`, which `apiClient.js` already generates
    - Reuse `apiClient.js`'s existing `ApiError` (`status`, `data`, `requestId`, `category`,
      `isRetryable()`) rather than reimplementing classification (`§1.13`)
    - _Requirements: 14.3, 14.4_
    - _Property: P27_

  - [ ]* 5.4 Write the property test for error translation
    - **Property 27: Translated error copy never leaks internals**
    - **Validates: Requirements 14.4**
    - Generate `ApiError` instances across every status and known backend code, network failures,
      WebSocket refusals, and thrown `Error` objects carrying stacks, Python tracebacks and
      internal endpoint URLs; assert the headline and detail contain no bare HTTP status, no
      identifier ending `Error`/`Exception`, no stack frame, no traceback marker and no `/api/` URL
    - Minimum 100 iterations

  - [~] 5.5 Create `src/hooks/usePanelState.js`
    - Return `{state, data, error, refetch, lastUpdated}` over the eight states in `§11.1`
      (`idle`, `loading`, `ready`, `refreshing`, `empty`, `error`, `unavailable`, `unauthorised`)
    - An `unavailable` reason string short-circuits to that state **without issuing a request** —
      this is how a capability gap is expressed (Requirement 19.3)
    - On failure, set `data` to `null`. This is the deliberate opposite of `usePolling`'s current
      behaviour in `primitives.jsx`, which keeps the last `data` and merely sets `error`, so a page
      renders the previous tick's P&L under an error indicator (Requirement 14.5)
    - `refetch` is a stable `useCallback` with no `data` dependency, so the interval is set once
      (`§13.2`)
    - Generalise `paperTradingFormat.js`'s existing `PANEL_STATES` and its server-error→state
      classification rather than replacing them — that module already has eight states and a
      passing suite (`§1.13`, `§11.1`)
    - _Requirements: 14.1, 14.2, 14.3, 14.5, 19.3_
    - _Property: P26_

  - [~] 5.6 Create `src/hooks/useConnectionStatus.js`
    - Seed from `wsClient.getStatus()`, re-seed on mount to absorb a transition that raced mount,
      then subscribe via `wsClient.onStatusChange` and return its unsubscribe (`§6.5`)
    - Push-based, so Requirement 2.6's 5-second bound is met by the transition itself with no
      interval that could be slower than the bound
    - _Requirements: 2.5, 2.6_

  - [~] 5.7 Create `src/hooks/useLiveChannel.js`
    - One `wsClient.subscribe` per channel regardless of subscriber count, via a module-level
      registry keyed on channel; return only `selector(message)` and re-render only when the
      selected value is not `Object.is`-equal to the previous one (`§13.2a`)
    - Document that `selector` must be module-scope or `useCallback`-stable
    - _Requirements: 2.6, 7.5_

  - [~] 5.8 Create `src/design/notificationPolicy.js`
    - The `NOTIFIABLE` allowlist of exactly the seven Requirement 16.1 categories with severity and
      copy functions, plus `notificationFor(event)` returning `null` for anything unmapped, and
      `normaliseEventKey` mapping the backend's `notifications.category` + `type` pair and the
      WebSocket event types onto those seven keys (`§11.5`)
    - Default-closed: an event type added to the backend tomorrow cannot toast without being added
      here first
    - _Requirements: 16.1, 16.2_

- [ ] 6. M3 — The `components/ds/` primitives

  - [~] 6.1 Build `Panel`, `LoadingState`, `EmptyState`, `ErrorState` and `Skeleton`
    - `ds/Panel.jsx` takes `{title, environment, state, loading, empty, error, unavailable}` and is
      the only thing that decides what a panel shows (`§11.1`). Children are **not rendered** in
      `error`, `empty` or `unavailable`; `refreshing` is reachable only from `ready`
    - `Panel` throws in development when a panel declaring money content omits `environment`
      (`§7.5`) — this is what makes Requirements 7.4 and 12.2 structural
    - `ds/LoadingState.jsx` with `kind ∈ skeleton-table | skeleton-cards | skeleton-chart |
      skeleton-metric | inline | button | page`, each sized from the same row/column config the
      content uses so arrival shifts nothing; absorbs `Spinner`, `LoadingOverlay`, `SkeletonLine`,
      `SkeletonCard`, `SkeletonTable`
    - `ds/EmptyState.jsx` with `headline`, `body` and `action` **all required** (throw in dev), and
      `variant ∈ no-data | no-match` where `no-match` requires `clearFiltersAction`; static icon,
      no `float` animation, no emoji
    - `ds/ErrorState.jsx` renders only `translateError(error, context)` output and shows retry only
      when the translation reports the failure retryable; it never renders `error.message`
    - _Requirements: 3.5, 3.6, 6.6, 10.4, 11.5, 14.1, 14.2, 14.3, 14.5, 19.3_
    - _Property: P26, P12_

  - [ ]* 6.2 Write the property test for the panel state contract
    - **Property 26: A panel's state fully determines what it renders, and a failure discards prior data**
    - **Validates: Requirements 3.5, 3.6, 6.6, 10.4, 14.1, 14.2, 14.3, 14.5**
    - Generate panel descriptors and sequences of read outcomes; assert empty renders all three of
      explanation/significance/next action, loading renders the declared variant, error renders a
      retry affordance iff the translation says retryable, and that after a successful read
      followed by a failed read no figure from the successful payload and no zero-as-value remains
    - Minimum 100 iterations

  - [ ]* 6.3 Write the property test for money-panel environment indicators
    - **Property 12: Every money-bearing panel carries an environment indicator**
    - **Validates: Requirements 7.4, 12.2**
    - Generate payloads producing arbitrary panel sets; assert every panel whose contents include
      position, order or P&L data contains an indicator whose accessible text names its
      environment, and that a panel whose server payload reported no environment states
      "unconfirmed" rather than naming one
    - Minimum 100 iterations

  - [~] 6.4 Build `Metric`, `PnLDisplay` and the `Reported<T>` accessor
    - `ds/Metric.jsx` per `§5.1`: `label` always visible, `tier ∈ 1|2|3` selecting
      `--text-figure`/`--text-title`/`--text-body`, `format`, `precision`, optional `state`,
      `unavailable` + `unavailableReason`, `hint`
    - `value == null` or `unavailable` renders the **not-available marker** — an em-dash in
      `content-muted`, `aria-label` of `"{label}: not available"`, reason in a tooltip. It never
      renders `0`. This one leaf behaviour is what makes Requirements 14.5 and 19.3 enforceable
      without per-call-site discipline
    - Accept either a raw value or a `Reported<T>` union (`§18`) and render the unavailable arm's
      reason
    - `ds/PnLDisplay.jsx` replaces `PnLBadge`: mono, `tabular-nums`, explicit sign, colour from
      `pnlToken` (zero → neutral), **no glow, no `pulseGlow`, no `textShadow`**
    - _Requirements: 4.1, 8.1, 11.1, 14.5, 19.3_
    - _Property: P5_

  - [ ]* 6.5 Write the property test for declared-field rendering
    - **Property 5: Every declared field renders, and an unsupplied field renders as not available**
    - **Validates: Requirements 4.1, 8.1, 11.1, 19.3**
    - Generate rows, result payloads and deploy configurations against declared field lists; assert
      exactly one cell or figure per declared field, and that an unsupplied field renders the
      not-available marker with a non-empty reason — never a zero, never an empty string, never a
      default such as `"healthy"`
    - Minimum 100 iterations

  - [~] 6.6 Build `StatusBadge`, `StrategyStatus`, `RiskIndicator` and `ExchangeStatus`
    - `StatusBadge` consolidates `Tag2`, `StatusDot` and `ui/Badge.jsx`'s status variants; takes
      `state` through `statusToken` and **never accepts a colour prop**
    - `StrategyStatus` absorbs `LiveStatus`/`LiveStatusV2` and `_normalizeStatus` from
      `Strategies.jsx`, and **imports** `computeStrategyHealth` from `lib/strategyHealth.js`
      rather than reimplementing it — it correctly returns `undetermined` for a row carrying
      neither a deployment nor a backtest record, and that must not regress (`§7.2`)
    - `RiskIndicator` replaces `RiskMeter` and `ProgressBar`'s risk usage; prefers the server's
      `risk_level` and derives from `utilizationPct` only when absent; no glow
    - `ExchangeStatus` absorbs `Dashboard.jsx`'s inline exchange-health blocks; `latencyMs == null`
      renders not-available, never `0 ms`
    - _Requirements: 1.2, 1.4, 1.5_

  - [~] 6.7 Build `TradingEnvironmentBadge`
    - Deduplicate the two verbatim `SimulatedIndicator` definitions in `pages/Portfolio.jsx` and
      `pages/TradeHistory.jsx` into `ds/TradingEnvironmentBadge.jsx` (`§1.13`, `§5.3`)
    - `{environment, isSimulated, variant ∈ chip|strip|inline, announce}`; render the `§8.2`
      treatment matrix — four independent axes (hue, label, icon, border style)
    - `environment == null && !isSimulated` renders `ENVIRONMENT UNCONFIRMED`;
      `environment == null && isSimulated` renders `SIMULATED · SERVER LABEL UNAVAILABLE`,
      preserving `TradeHistory.jsx`'s existing honest copy. Never a guessed environment
    - _Requirements: 7.4, 8.5, 12.2, 12.3_
    - _Property: P22, P12_

  - [ ]* 6.8 Write the property test for environment distinctness
    - **Property 22: The three environment treatments are pairwise distinct on multiple axes**
    - **Validates: Requirements 8.5, 12.3**
    - For any pair of distinct environments, assert the resolved treatments differ in hue, label
      text, icon **and** border style, so no two are distinguishable by colour alone
    - Minimum 100 iterations

  - [~] 6.9 Build `ds/DataTable.jsx`
    - The full `§11.3` contract: declarative `columns` with `align ∈ text|numeric`, `sortable`,
      `format`, `render`, `width`, `priority`; controlled `sort`; `page`/`pageSize`/`totalCount`;
      `stickyHeader`; `density`; `onRowClick`/`rowHref`; **required** `caption`
    - Alignment is a column property, not a per-cell decision — `numeric` yields right alignment,
      `tabular-nums` and `--font-mono`
    - Sortable headers are `<button>`s with `aria-sort`; comparators chosen by `format`; sort is
      stable
    - Below `--breakpoint-laptop`, `priority: 3` columns are hidden and surfaced in a per-row
      expand; the table sits in an `overflow-x: auto` wrapper with `scrollbar-gutter: stable` and
      is never clipped
    - Pagination only — **no virtualization in v1** and no new dependency (`§11.3`)
    - Rows are memoised on `getRowId(row)` plus a shallow compare of projected cell values, so one
      tick re-renders one `<tr>` (`§13.2c`)
    - Replaces `Table`/`TableHead`/`TableHeader`/`TableRow`/`TableCell` and the raw `<table>`
      markup in `TradeHistory`, `Strategies`, `Portfolio` and `PaperTrading`
    - _Requirements: 11.1, 11.3, 11.4, 11.6, 15.4, 17.2, 18.1_
    - _Property: P19, P20, P29_

  - [ ]* 6.10 Write the property test for column alignment
    - **Property 19: Column alignment is determined solely by the column declaration**
    - **Validates: Requirements 11.3, 15.4**
    - Minimum 100 iterations over generated row sets and column configurations

  - [ ]* 6.11 Write the property test for pagination
    - **Property 20: Pagination partitions the row set exactly once**
    - **Validates: Requirements 11.4**
    - Assert no page renders more nodes than `pageSize` and that walking every page yields each row
      id exactly once, none lost, none duplicated; minimum 100 iterations

  - [ ]* 6.12 Write the property test for the empty-state variant
    - **Property 21: The empty-state variant discriminates no-data from no-match**
    - **Validates: Requirements 11.5**
    - Assert the no-match variant always offers a clear-filters action; minimum 100 iterations

  - [ ]* 6.13 Write the property test for sorting
    - **Property 29: Sorting is a stable, reversible ordering of the same rows**
    - **Validates: Requirements 15.4**
    - Assert the rendered order is a permutation ordered by the column's comparator, equal keys
      retain input order, and toggling direction twice restores the original; 100 iterations

  - [~] 6.14 Build `ds/FilterBar.jsx`
    - `{filters, values, onChange, search, onSearchChange, searchPlaceholder, resultCount,
      totalCount, actions}` with a labelled search input and a `role="status"` live region reading
      `{resultCount} of {totalCount}` — that region is what lets `EmptyState` distinguish
      Requirement 11.5's two cases (`§5.1`)
    - Replaces the filter chips in `TradeHistory`, `Strategies` and `SignalTrace`
    - _Requirements: 11.2, 11.5_

  - [~] 6.15 Build `ds/Field.jsx` and the advanced-settings accordion
    - `label` is a **required** prop and throws in dev without it; the component never falls back to
      the placeholder as an accessible name, which is what `Inp` in `primitives.jsx` does today
      (`§11.2`)
    - `error` renders inline, linked by `aria-describedby`, with `aria-invalid` on the input, on the
      offending field alone; validation runs on blur and submit, not per keystroke
    - `disabled && !disabledReason` throws in dev; the reason renders as visible help text **and**
      goes into the accessible description
    - Numeric inputs use `inputMode="decimal"` and preserve the typed string until parse, following
      `paperTradingFormat.js::parseCapitalToMinor`'s never-round discipline
    - `ds/Accordion` wrapper with `defaultOpen={false}` for advanced settings, driven by a per-form
      declared advanced-field set so the collapsed set is data and therefore checkable
    - _Requirements: 15.1, 15.2, 15.3, 15.6_
    - _Property: P28, P31_

  - [ ]* 6.16 Write the property test for form fields
    - **Property 28: Every form field is labelled, and every disabled control states why**
    - **Validates: Requirements 15.1, 15.2, 15.3**
    - Assert the label text is non-empty and differs from the placeholder, the validation message
      links to that field alone, and a non-empty accessible reason exists iff the control is
      disabled; minimum 100 iterations

  - [ ]* 6.17 Write the property test for advanced-field collapse
    - **Property 31: Advanced fields, and only advanced fields, start collapsed**
    - **Validates: Requirements 15.6**
    - Minimum 100 iterations over generated form descriptors

  - [~] 6.18 Build `ds/Chart.jsx`
    - Wrap recharts with **required** `xAxis.label` and `yAxis.label`, `legend="auto"` rendering the
      legend iff `series.length > 1`, and a tooltip reachable by hover **and** keyboard: the
      container is focusable, Arrow keys move a data cursor, and the focused point is announced via
      an `aria-live` region (`§11.4`)
    - Series colours come from `semantic.js` only; no gradients, no glow; retoken and reuse
      `CustomTooltip` from `primitives.jsx`
    - A chart with no data renders the parent `Panel`'s empty state, not empty axes
    - Lazy-load the module so recharts stays out of the chunks for Strategies, Trade History and
      Signal Trace (`§13.3`)
    - _Requirements: 15.5_
    - _Property: P30_

  - [ ]* 6.19 Write the property test for charts
    - **Property 30: Charts label both axes and show a legend exactly when multi-series**
    - **Validates: Requirements 15.5**
    - Minimum 100 iterations over generated series configurations

  - [~] 6.20 Build `ds/ConfirmDialog.jsx`, `ds/Drawer.jsx`, the overlay registry and `useFocusTrap`
    - `ConfirmDialog` per `§5.1`: `{open, onCancel, onConfirm, title, intent ∈
      destructive|live|neutral, environment, review[], acknowledgement, confirmLabel, cancelLabel,
      busy, error}`; the `review` grid is the Requirement 8.1 surface and `acknowledgement` gates
      confirm
    - `hooks/useFocusTrap.js`: collect focusable descendants, cycle Tab/Shift+Tab within them,
      initial focus on **cancel**, restore focus to the trigger on close, mark the rest of the app
      `aria-hidden`, Escape cancels; `role="dialog"`, `aria-modal`, `aria-labelledby`,
      `aria-describedby`
    - `components/ds/overlayRegistry.js` permits exactly **one** open overlay: a second `open` is a
      dev-time error and a production no-op (`§11.6`)
    - Clamp to `max-height: calc(100dvh - 2 * var(--spacing-8))` with an internal scroll region and
      `max-width: min(560px, calc(100vw - 2 * var(--spacing-4)))`
    - This component does not exist in any form today — all six current confirmations are native
      browser dialogs (`§1.8`)
    - _Requirements: 7.6, 8.1, 8.2, 8.3, 8.5, 17.3, 18.3_
    - _Property: P34, P35_

  - [ ]* 6.21 Write the property test for the overlay registry
    - **Property 34: At most one overlay is open and it fits the viewport**
    - **Validates: Requirements 17.3**
    - Generate sequences of open/close requests across dialogs and drawers; assert at most one is
      open at any point and the open one's bounding box lies within the viewport at every supported
      width; minimum 100 iterations

  - [ ]* 6.22 Write the property test for the focus trap
    - **Property 35: Keyboard focus cannot leave an open modal**
    - **Validates: Requirements 18.3**
    - Generate Tab/Shift+Tab sequences of arbitrary length; assert the active element is always a
      descendant, initial focus is the cancel action, and focus returns to the opener on close;
      minimum 100 iterations

  - [~] 6.23 Build the remaining primitives and the `ds/index.js` barrel
    - `PageHeader` (`title`, `subtitle`, `environment`, `breadcrumb`, `actions`, `meta`) reserving a
      **fixed 64px block** whether or not subtitle/meta are present, so a route change cannot shift
      the content region (`§5.1`, `§6.2`)
    - `SectionHeader` (renames and retokens `PanelTitle`), `Tabs`, `Tooltip`, `Alert`, `Breadcrumb`
    - `CommandButton` wrapping `components/ui/Button`: `intent ∈ primary|secondary|ghost|
      destructive|live`, `loading`/`loadingLabel`, and `disabledReason` **required** when
      `disabled` — throwing in dev is how Requirement 15.3 becomes impossible to forget; icon-only
      buttons throw without `aria-label`
    - `ds/index.js` re-exporting every primitive except the lazy `Chart`
    - _Requirements: 1.2, 2.2, 15.3, 18.4, 19.4_

  - [~] 6.24 Retoken `components/ui/{Button,Badge,Card,Accordion}.jsx` in place
    - Replace their hardcoded palettes with token references; keep the existing prop aliases
      (`v`/`variant`, `sz`/`size`, `cls`/`className`, `Icon`/`icon`) because ~150 call sites
      including the out-of-scope landing page depend on them (`§2.2`)
    - `ui/Badge.jsx`'s `#10B981`/`#EF4444` become the trading palette `#26A69A`/`#EF5350`
    - Parameterise `Card.jsx`'s hardcoded `p-6` onto the spacing tokens so `ds/Panel` and `ui/Card`
      share padding and mixed density cannot appear mid-migration (`§14.4`)
    - Remove the `translateY(-2px)` hover lift and `scale(1.02)` tag hover; border colour change
      only (`§4.3`)
    - _Requirements: 1.1, 1.5_

  - [~] 6.25 Rewrite `src/components/ErrorBoundary.jsx`
    - It currently prints `error.stack` and `errorInfo.componentStack` into the DOM (`§1.7`), which
      Requirement 14.4 forbids; render `translateError` output plus the Sentry event id instead and
      send the stacks to Sentry only
    - Keep the "Copy Error" button but copy `{eventId, timestamp, route}` rather than the stack
    - Move its hardcoded palette (`#010608 #6b9bb8 #ff4757 #ff6b81 #4a5568`) onto tokens and lower
      its `no-colour-literals` budget entry to zero
    - _Requirements: 14.3, 14.4_

  - [~] 6.26 Delete the decorative primitives from `ui-legacy/primitives.jsx`
    - Remove `PremiumCard` outright, remove `MiniSparkline`'s gradient fill (keep the polyline),
      remove `EmptyState`'s `float` animation, and retoken `LiveLogStream`'s chrome (`§5.3`)
    - Replace the `💡` and `⚠` emoji in UI copy with lucide icons (`§4.3`)
    - Lower the `legacy-c-budget` entry for `primitives.jsx` by the amount this removes
    - _Requirements: 1.5_

  - [~] 6.27 Raise the accessibility lint rules from warn to error
    - In `algo22-terminal/eslint.config.js`, set `eslint-plugin-jsx-a11y-x` rules to `error` for
      `src/pages/**` and `src/components/ds/**` (the plugin is already a devDependency) and fix
      what it reports in `ds/` — no `div onClick` on any interactive element (`§11.7`)
    - _Requirements: 18.1, 18.4_

- [~] 7. Checkpoint — M3 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - Every primitive is unit- and property-tested **before** any page depends on it. Expected visible
    change is small: `ui/Badge`'s green/red shift to the trading palette and the error boundary
    stops showing stack traces.

- [x] 8. M4 — Application shell

  - [x] 8.1 Create `src/components/shell/navigation.js`
    - `NAV_GROUPS` with the four workflow groups and **exactly the ten** Requirement 2.1 entries
      (`§6.3`), each with `id`, `label`, `icon`, `path` and a `matches` regex array
    - `activeNavId(pathname)` returning at most one id, matching aliases (`/app/backtester`) and
      child routes (`/app/strategies/:id`) that highlight nothing today (`§1.10`)
    - Adds the three entries that have working routes but no nav entry: `live-trading`, `portfolio`,
      `trades`. Removes `Docs` from primary nav entirely — it is an external link to the stale
      `docs.algo22.io` brand and is omitted rather than shipped broken if the URL is not live
    - _Requirements: 2.1, 2.3, 2.4, 19.4_
    - _Property: P3_

  - [ ]* 8.2 Write the `nav-contract` CI guard
    - `tests/unit/guards/nav-contract.test.js`: assert `NAV_GROUPS` flattens to exactly the ten
      Requirement 2.1 ids, no more and no fewer, and that every entry carries both an icon and a
      non-empty label (`§15.1`)
    - _Requirements: 2.1, 2.4_

  - [ ]* 8.3 Write the property test for active navigation state
    - **Property 3: At most one navigation entry is ever active**
    - **Validates: Requirements 2.3**
    - Generate every in-scope route, every declared alias, child routes with arbitrary appended
      segments, and arbitrary strings; assert at most one id always, and exactly one for any
      in-scope route, alias or child; minimum 100 iterations

  - [x] 8.4 Create `src/components/shell/ResponsiveGate.jsx`, replacing `DesktopOnlyOverlay`
    - Declare `VIEWPORT` and `ROUTE_MIN_VIEWPORT` from `§11.6` — only `/app/builder` needs more
      than tablet
    - ≥1024px: everything available. 768–1023px: the shell collapses the sidebar to a 56px icon
      rail with labels in tooltips **and** in the accessible name, so icon and label are both still
      present per Requirement 2.4. <768px: one honest gate screen with **no blur and no
      partially-rendered app behind it**, stating the minimum width and why
    - This lowers today's hard 1000px blanket gate to 768px and turns it from "the absence of
      behaviour for all pages" into a per-route capability gate (`§1.11`, `§11.6`)
    - _Requirements: 17.1, 17.4_

  - [x] 8.5 Rebuild the shell layout in `src/App.jsx`
    - CSS Grid `grid-template: min-content 1fr / SIDEBAR_WIDTH_PX[sidebarMode] 1fr` (rows then
      columns) with `height: 100%`, replacing the nested flexbox where `Sidebar`'s
      `minHeight: 100vh` and the content column's `overflow: hidden` interact (`§6.2`). Not literal
      tracks: the bar row is `min-content` because `TopBar` renders its disconnected-state `Alert`
      strip below that row, which a fixed track would clip (the row itself is pinned at
      `TOPBAR_HEIGHT_PX` = 56px, matching the sidebar's brand block); the column is tier-driven
      because tablet width collapses the sidebar to a 56px rail (`§11.6`); and `100dvh` belongs on
      `ResponsiveGate`, which establishes viewport height and may add a restriction strip above the
      grid
    - `scrollbar-gutter: stable` and `min-width: 0` on `main` — the gutter fixes the single most
      visible shift in the app today (a short→long route transition moves the whole content column
      left) and `min-width: 0` is the usual cause of shell-level horizontal overflow
    - Replace the route `Suspense` fallback's bare `<div style={{background:'#080A0E'}} />` with
      `<PageHeader title={routeTitle} /><LoadingState kind="skeleton-table" />`, `routeTitle`
      resolved from `navigation.js`. NOT `kind="page"`: `PageSkeleton` already includes its own
      64px header block, so it is the alternative to a real `PageHeader` rather than a companion
      to one, and the pair reserves 128px of header for a 64px page (`§6.2` (4))
    - Narrow the `'navigate'` DOM-event bridge to a `navigation.js` lookup and delete its
      `PATH_MAP`; delete the unused `TENANT_ID` const; keep `window.showToast` (~40 call sites,
      `§6.6`) but make `ToastContainer` `aria-live="polite"` and retokened
    - Mount the `ConfirmDialog` portal at `z-modal` and add a skip-to-content link as the shell's
      first focusable element
    - _Requirements: 2.2, 17.1, 17.3_
    - _Property: P2_

  - [x] 8.6 Rebuild `src/components/Sidebar.jsx` against the new IA
    - Render the four groups from `NAV_GROUPS`; every entry is a react-router `NavLink` producing a
      real `<a>`, not a `<button>` calling `navigate()`, so middle-click, Ctrl-click and native
      focus/hover semantics work (`§6.3`)
    - Active treatment: 2px left rail in `--color-brand`, `--color-brand-wash` background, label in
      `--color-brand`, `aria-current="page"`; 216px is chosen so "Strategy Builder" fits at
      `--text-small` without ellipsis
    - Delete the `handleNavClick` `window.open` branch for `docs` so nav no longer mixes navigation
      with external links
    - Brand block 56px, `AccountMenu` trigger pinned bottom at 56px
    - _Requirements: 2.1, 2.3, 2.4_

  - [x] 8.7 Rebuild `src/components/TopBar.jsx` with a real connection indicator
    - Delete the hardcoded `<LiveStatusV2 status="running" />` on line 61 — the literal string that
      makes the indicator claim LIVE with a pulsing green dot whether or not a socket exists
      (`§1.3`); it is both a Requirement 2.5/2.6 gap and a Requirement 14.5 fabrication today
    - Add `shell/ConnectionStatusIndicator.jsx` reading `useConnectionStatus()` through
      `statusToken`, so an unrecognised status renders neutral with the raw value as its label and
      never "LIVE"
    - On `disconnected`/`error`, render the full-width `Alert` strip below the top bar with the
      `§6.5` copy and a retry calling `wsClient.connect()`; on `reconnecting`, indicator only — a
      strip for a self-healing state would be the noise Requirement 16.2 is about
    - Left: breadcrumb/page context. Right: connection indicator, notification bell, UTC clock
    - _Requirements: 2.5, 2.6, 14.5_

  - [x] 8.8 Add `src/components/shell/AccountMenu.jsx` and one shared unread-count hook
    - The `§6.4` popover keeping the seven deferred routes reachable: Profile, Security log, Billing
      & plan, Exchange accounts, Risk settings, Notifications (with count), Support, Documentation
      (external, `rel="noopener noreferrer"`, **omitted if the URL is not live**), Sign out
    - Reuse `useFocusTrap` and Escape handling from `ConfirmDialog`
    - Add `hooks/useUnreadNotifications.js` so the bell and the menu share one
      `api.notifications.getUnreadCount()` read instead of the two independent fetches
      `Sidebar.jsx` and `TopBar.jsx` make today (`§6.4`)
    - _Requirements: 2.1, 18.3, 19.4_

  - [ ]* 8.9 Write the property test for shell geometry
    - **Property 2: Shell geometry is invariant across route changes**
    - **Validates: Requirements 2.2**
    - For any ordered pair of in-scope routes, assert the sidebar's and top bar's measured width,
      height and grid position are identical after the transition and the content region's declared
      scrollbar gutter is unchanged; minimum 100 iterations

  - [ ]* 8.10 Write the connection-status integration tests
    - `tests/unit/shell/connectionStatus.test.jsx`: mock `wsClient`, assert the indicator seeds from
      `getStatus()`, that a status transition is reflected without any timer advance (the push path
      means Requirement 2.6's 5s bound is met by construction), that a transition racing mount is
      absorbed by the re-seed, and that the disconnected strip appears and offers retry
    - _Requirements: 2.5, 2.6_

  - [ ]* 8.11 Write the responsive property test and the Playwright responsive pass
    - **Property 33: No in-scope page overflows horizontally at any supported width**
    - **Validates: Requirements 17.1, 17.2**
    - vitest sweep: set `window.innerWidth` across 768–1920 for each route in a checked-in page
      registry and assert `container.scrollWidth <= container.clientWidth`; the registry starts with
      the shell and grows as each page task lands, so it never asserts against an unmigrated page
    - Playwright spec in `tests/e2e/responsive.spec.js` using the existing `playwright.config.ts`:
      each in-scope route at 768 / 1024 / 1440 / 1920, asserting no document-level horizontal
      scrollbar. jsdom does not lay out, so only this half catches genuine reflow overflow
    - Also assert the `DataTable` `priority` reduction below the laptop breakpoint exposes a scroll
      container or drops columns, never clips; minimum 100 iterations for the property half

  - [ ]* 8.12 Write the accessibility sweep property test
    - **Property 36: Every interactive control is keyboard-operable, focus-visible and named**
    - **Validates: Requirements 18.1, 18.2, 18.4**
    - Render each entry of a checked-in page registry with mocked reads; assert every `button`,
      link, `input`, `select`, `textarea` and `role="button"` has a non-empty computed accessible
      name, is in tab order unless disabled, produces a computed style delta on focus, and that
      trading-critical actions fire on both Enter and Space
    - Seed the registry with the shell and the `ds/` fixture page; each page task below adds itself,
      which is what keeps the sweep green at every step while its coverage only grows
    - Minimum 100 iterations

- [~] 9. Checkpoint — M4 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - This step is what makes Portfolio and Trade History reachable at all, so it must land before
    their page work in Task 15 and 16 — page work on an unreachable page is wasted (`§14.3`).
  - Accepted and explicit: the seven deferred pages now sit inside the new shell with older
    interiors. They were retokened by M1 so they are not jarring. The release note says so.

- [ ] 10. M5 — Cross-cutting wiring

  - [x] 10.1 Create `src/hooks/useNotificationStream.js` as the single toast transport for backend events
    - Subscribe to `wsClient.subscribe('notification')` plus the relevant channels, pass **every**
      event through `notificationFor`, and call `window.showToast` only on a non-null result
      (`§11.5`)
    - Mount it once in the shell. Pages stop calling `window.showToast` for backend events; they may
      still toast their own action outcomes ("Saved as version 7"), which are not backend events
    - _Requirements: 16.1, 16.2_
    - _Property: P32_

  - [ ]* 10.2 Write the property test for the notification allowlist
    - **Property 32: A notification is raised exactly for allowlisted event categories**
    - **Validates: Requirements 16.1, 16.2**
    - Generate every allowlisted category, every non-allowlisted category the backend can emit
      (`trade strategy risk security billing system support exchange` × `info warning critical
      emergency`) and arbitrary category strings; assert a notification is raised iff the event maps
      onto the allowlist; minimum 100 iterations

  - [~] 10.3 Replace the two native dialogs in `src/pages/Strategies.jsx`
    - `window.confirm(archiveConfirmMessage(name))` → `ConfirmDialog intent="destructive"` with a
      review grid; archive is reversible so no acknowledgement checkbox (`§8.4`)
    - `window.prompt("Enter new strategy name:")` → `ConfirmDialog` containing a labelled `Field`
      with inline validation — `window.prompt` cannot carry a label or a validation message
      (Requirements 15.1, 15.2, `§1.8`)
    - _Requirements: 15.1, 15.2, 18.3, 19.4_
    - _Property: P13_

  - [x] 10.4 Replace the four native dialogs in `src/pages/StrategyDetail.jsx` and delete its placeholder panel
    - All four `window.confirm` calls (deploy, delete, restore version, deploy version) become
      `ConfirmDialog`; the two deploy confirmations route through Task 10.5's flow and **stop
      claiming "Deploy to paper trading?"** when the target is chosen elsewhere — the two pages
      disagree about what deploy means today (`§1.8`)
    - Delete the `Audit history coming soon` panel at line 1179 and the tab that reaches it, linking
      to Signal Trace instead — that page owns the data (`§7.3`, Requirement 19.4)
    - _Requirements: 7.6, 18.3, 19.4_

  - [x] 10.5 Build the `Deploy_Confirmation_Flow`
    - `src/components/deploy/DeployConfirmation.jsx` implementing `§8.3`'s three-step state machine
      inside one `ConfirmDialog`: Configure → Review → (Live only) AckLive → Submitting
    - `Review` renders all eight Requirement 8.1 fields as `Reported<T>`; a field the configuration
      does not carry renders the not-available marker and does not block the flow
    - `AckLive` exists **only** on the Live path and `Submitting` is unreachable from it until the
      acknowledgement is satisfied; the `POST` is issued in the `Submitting` transition, so no code
      path submits earlier. For Paper and Backtest the real-funds statement is not merely hidden —
      it is never constructed
    - Wrap the existing `components/DeployPreflightPanel.jsx`, `lib/deployPreflight.js` and
      `hooks/useDeployPreflight.js` rather than replacing them, and keep posting to the same gated
      route `Strategies.jsx` already uses. **No deployment or gating logic changes**
      (Requirement 19.1)
    - Title, confirm intent and the header `TradingEnvironmentBadge` all differ by environment
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 19.1_
    - _Property: P13, P14_

  - [ ]* 10.6 Write the property test for confirmation gating
    - **Property 13: A destructive action reaches the backend only after explicit confirmation**
    - **Validates: Requirements 7.6, 8.3**
    - Generate invocation sequences over every destructive and live action in `§8.4`'s inventory;
      assert the backend mutation is not called at any point before the confirmation step's explicit
      action is satisfied, and is called exactly once after it; minimum 100 iterations

  - [ ]* 10.7 Write the property test for the real-funds step
    - **Property 14: The real-funds statement appears exactly on the Live path**
    - **Validates: Requirements 8.2, 8.4**
    - Generate `LIVE`, `PAPER`, `BACKTEST`, `null` and arbitrary strings as the target environment;
      assert the acknowledgement step is **constructed** iff the resolved environment is Live;
      minimum 100 iterations

  - [x] 10.8 Delete `extractErrorMessage` and `getErrorType` from `ui-legacy/primitives.jsx`
    - `extractErrorMessage` falls back to `JSON.stringify(detail)` and then `err.message`, which is
      precisely how an axios message or a backend traceback reaches the screen today (`§12`)
    - Re-point every call site at `translateError`
    - _Requirements: 14.3, 14.4_

  - [x] 10.9 Delete `LoadingProvider`'s full-screen blocking overlay
    - It currently covers the whole app whenever any keyed loading state is true, which is the
      opposite of the per-element loading Requirement 14.2 asks for (`§5.1`)
    - Keep `LoadingProvider` itself — removing it would touch out-of-scope pages — and delete only
      its overlay render
    - _Requirements: 14.2_

  - [x] 10.10 Remove the six dead methods from `src/api/modules/portfolio.js`
    - Remove `getPosition`, `closePosition`, `getPositionHistory`, `getBalance`, `getPnL` and
      `getPerformance`: none has a backend route (`§1.4`) and `grep` confirms none has a call site.
      A documented client method that always 404s is a non-functional API surface, and
      `closePosition` in particular is dangerous because it looks like a working position-close
    - `getOpenPositions` and `getPositions` stay until Task 13.1 re-points `Portfolio.jsx:302` in
      the same change — removing them now would turn a 404 into a `TypeError`
    - _Requirements: 19.4_

  - [ ]* 10.11 Write the `no-native-dialogs` CI guard
    - `tests/unit/guards/no-native-dialogs.test.js`: zero `window.confirm` / `window.alert` /
      `window.prompt` in `src/pages/**` and `src/components/**`, with an out-of-scope allowlist of
      exactly `pages/TwoFA.jsx` and `components/NotificationCenter.jsx` (both explicitly deferred by
      `§17.2`), and assert the allowlist contains exactly those two files so a new native dialog
      anywhere else fails
    - Preserve `StrategyMarketplace.test.jsx`'s existing zero-`window.alert` assertions unchanged
    - _Requirements: 18.3, 19.4_

  - [ ]* 10.12 Write the `no-placeholders` CI guard
    - `tests/unit/guards/no-placeholders.test.js`: zero `TODO`, `FIXME` or `coming soon` in the
      in-scope page and in-scope component files. Scoped to in-scope files because
      `components/landing/ScreenshotComingSoon.jsx` is an out-of-scope landing component
    - _Requirements: 19.4_

  - [ ]* 10.13 Write the property test for inert controls
    - **Property 37: No enabled control is inert**
    - **Validates: Requirements 19.4**
    - Over the same page registry as Task 8.12, assert every enabled interactive control has either
      a bound activation handler or a navigation target, and that a control with neither is disabled
      and carries a non-empty reason; minimum 100 iterations

- [~] 11. Checkpoint — M5 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - Deploy and delete now have proper dialogs with focus traps and review grids; routine toasts
    stop; no in-scope page carries a native dialog or a "coming soon" panel.

- [ ] 12. M6 — The six backend read-projection changes (BC-1…BC-6)

  All six are **additive read projections**. None touches order execution, risk-control,
  strategy-versioning, auth or billing logic (`§16`, `§17.1`, Requirement 19.1). Each lands before
  the frontend task that consumes it, so no field's `not-available` state is its permanent outcome.

  - [~] 12.1 BC-1 — compute current drawdown honestly
    - `backend_app/backend/dashboard_aggregation_service.py::get_risk_data` currently sets
      `current_drawdown_pct` from `today_return_pct` (line 1139), and the older path at line 397
      from `abs(portfolio.pnl_pct)`, so a profitable day renders as a positive "drawdown" (`§1.5`)
    - Compute `(peak_equity − current_equity) / peak_equity` from the existing `equity_curve` series
      and publish it as a **new** field `current_drawdown_pct_v2`, leaving the existing field in
      place for its deprecation window
    - In `backend_app/routers/risk.py::get_risk_status`, replace the literal `"drawdown_pct": 0.0`
      with the computed value, or `null` until computed — `null` is honest, `0.0` is not
    - Verification: extend the risk/dashboard projection tests under `tests/` with a rising-then-
      falling equity series asserting the computed drawdown, and a profitable-day series asserting
      the value is `0` rather than a positive return figure
    - _Requirements: 3.1, 10.2, 19.1, 19.2_

  - [~] 12.2 BC-2 — stop swallowing read failures into empties
    - `dashboard_aggregation_service.py::get_open_positions` catches and returns `[]` on a Redis
      failure, so an outage is indistinguishable from "no positions" (`§1.6`)
    - Either let the exception propagate to the existing 503 `DASHBOARD_FETCH_FAILED`, or add a
      `degraded: {positions: 'unreadable'}` marker to the response — the `signal_trace` router
      already uses exactly this `degraded` pattern, so it is an established convention here
    - `backend_app/routers/dashboard.py::get_dashboard_overview` catches every exception and returns
      `total_value: 0.0, today_pnl: 0.0, …`; make it fail loudly the same way. A trader with a broken
      read must not see a zeroed portfolio
    - Verification: a test that forces the Redis read to raise and asserts the response is either a
      503 or carries the `degraded` marker, and never an empty `positions[]` with a 200
    - _Requirements: 14.5, 19.1, 19.2_

  - [~] 12.3 BC-3 — add `last_signal_at` to the strategies list projection
    - The field already exists on the dashboard strategy projection
      (`dashboard_aggregation_service.py:954`) but not on `GET /api/strategies`; add it to the list
      projection in `backend_app/routers/strategies.py` (`§7.2`)
    - Verification: assert the list response carries `last_signal_at` and that it is `null`, not
      absent and not a fabricated timestamp, for a strategy that has never signalled
    - **The producer that was owed here has landed; `last_signal_at` is now a real figure.**
      The debt recorded under this task was that `last_signal_at` was declared by no migration in
      this repository and written by nothing, so BC-3's projection — correct and additive as it was
      — reported `null` on every database permanently, which is the condition Task 12's preamble
      rules out. Two things closed it:
      - **`backend_app/migrations/015_strategy_last_signal_at.sql`** adds
        `public.strategies.last_signal_at TIMESTAMPTZ NULL` — nullable, **no default**, so a
        strategy that has never signalled is `NULL` and never an epoch or a creation time
        (Requirement 19.2). Follows 005a statement for statement: RLS preflight that refuses
        rather than enables, `ADD COLUMN IF NOT EXISTS`, a column-shape assertion (a `DEFAULT
        now()` here would make every new draft claim it had just fired), one transaction,
        additive-only, no back-fill, and a postflight proving policies, triggers, indexes and
        every foreign-key `ON DELETE` action are unchanged. **No index**, unlike 005a: nothing
        filters or orders by this column, so one would add a write to the signal path for no
        read. Applied by hand, like every migration here.
      - **`backend_app/backend/strategy_last_signal.py`** writes it, called from the two places a
        signal row is created — `signal_service._persist_signal` (the live and paper path) and
        `SignalService.create_signal` (the legacy path behind `POST /api/signal-trace/signals`
        and `master_executor`) — **after** the row is confirmed persisted.
    - **Why a stored column and not BC-4's derived `MAX(generated_at)`.** `public.signals` has no
      SQLAlchemy model (`backend_app/core/models/` holds no signals model), so BC-4's route — hand
      a grouped aggregate to a session — has no session; PostgREST's grouped aggregates are off
      unless an operator sets `db-aggregates-enabled`, which nothing here does, so a derived read
      would report `null` on every database whose operator had not flipped an undocumented server
      flag — the same permanent-`null` defect moved from "no column" to "no flag"; and a view over
      `signals` would execute with the view owner's privileges and bypass that table's RLS, since
      `security_invoker` is PostgreSQL 15 and this repository's baseline predates PG14 (007/008/009
      all record that `CREATE OR REPLACE TRIGGER` is unavailable). A trigger loses because it would
      sit inside the signal INSERT's own transaction, taking a lock on `strategies` that
      serialises signals for one strategy and that `statement_timeout` can turn into a failed
      INSERT — a lock wait is not an exception, so an in-trigger handler does not contain it.
    - **The signal write path did not become able to fail.** `schedule_last_signal_at` calls
      `asyncio.create_task` and returns; nothing on the signal path awaits the update, so it can
      neither raise into it nor delay it. `record_last_signal_at` additionally catches
      `BaseException` around every statement and returns a bool. Tested four ways: 015 unapplied
      (`PGRST204`), the `strategies` table unreachable, the producer raising outright, and the
      producer **hanging forever** — the signal is persisted and returned in all four.
    - **Frozen baselines `signal_generation` and `signal_trace_live` now differ by exactly one
      entry each and are deliberately NOT re-recorded** — see the report under this task's
      execution. Both gained `{"call": "postgrest.update", "columns": ["last_signal_at"], "table":
      "strategies"}` in their collaborator sequence, which is precisely the producer this
      follow-up required; nothing else in either capture moved. Re-recording needs a
      `_baseline_note*` authorisation decided by whoever owns those captures, not by this task.
    - _Requirements: 4.1, 19.1, 19.2_

  - [~] 12.4 BC-4 — add `last_execution_at` to the strategies list projection
    - No `last_execution*` field exists on any strategy projection today (verified in `§7.2`);
      source it from the per-strategy max execution timestamp — `execution_records` already computes
      `MAX(created_at) as last_execution_at` for its own summary — via
      `core/models/execution_record.py` into `backend_app/routers/strategies.py`
    - Verification: assert the value for a strategy with executions equals the max execution
      timestamp and is `null` for one without
    - _Requirements: 4.1, 19.1, 19.2_

  - [~] 12.5 BC-5 — expose lifetime `realized_pnl` on the portfolio overview
    - `today_realized_pnl` is today-only and `cumulative_pnl` is total P&L, not realised, so
      Requirement 10.1's "realised P&L" has no honest source (`§7.6`)
    - Add lifetime `realized_pnl` to `dashboard_aggregation_service.py::get_portfolio_overview` as
      the QuestDB `executions.pnl` sum **without** the day filter — the same query already runs with
      one
    - Verification: assert lifetime `realized_pnl` differs from `today_realized_pnl` for a fixture
      with executions on two different days, and that both are present
    - _Requirements: 10.1, 19.1, 19.2_

  - [~] 12.6 BC-6 — record the position change resulting from a signal
    - Nothing in the signal-trace domain records stage 9 today: `PUT /signals/{id}/execution`
      accepts `trade_id`, `pnl` and `realized_pnl` — a P&L outcome, not a position transition
      (`§10.1`)
    - Add a `POSITION_UPDATED` timeline event (or a `resulting_position` snapshot on the trace),
      written at the same point the execution update is written, in
      `backend_app/routers/signal_trace.py` and the `signal_service` that feeds it
    - Verification: assert the timeline for a signal whose execution filled carries exactly one
      `POSITION_UPDATED` event after `EXECUTED`, and that a signal that never executed carries none
    - _Requirements: 9.1, 9.2, 19.1, 19.2_

- [ ] 13. M6 — Frontend data-honesty pass

  - [~] 13.1 Re-point Portfolio's positions read and remove the last two dead API methods
    - `Portfolio.jsx:302` calls `api.portfolio.getOpenPositions().catch(() => getPositions())` and
      **both 404** — `routers/portfolio.py` registers only `/summary`, `/equity-curve`,
      `/allocation`, `/heatmap`, `/recent-transactions`, `/close-all`, and the only other
      `GET /positions` sits behind `get_admin_user` (`§1.4`)
    - Re-point at `dashboardApi.getDashboard({ environment: 'live' })`, which returns a real
      normalised `positions[]`; this is not a new endpoint and not a workaround — it is the endpoint
      that actually serves positions to a trader (`§7.6`). Keep `/summary`, `/equity-curve`,
      `/allocation` and `/heatmap` as-is; paper positions keep `api.paper.getPositions()`
    - Consume BC-2's `degraded` marker: an unreadable positions read renders `Panel state="error"`,
      never an empty table
    - Remove `getOpenPositions` and `getPositions` from `api/modules/portfolio.js`, completing the
      eight-method cleanup begun in Task 10.10
    - _Requirements: 10.3, 14.5, 19.4_

  - [~] 13.2 Stop reading `/api/dashboard/overview` anywhere in the frontend
    - Route every dashboard read through `GET /api/dashboard`, which correctly raises 503
      `DASHBOARD_FETCH_FAILED`; the `/overview` path returns zeros on failure (`§1.6`, `§7.1`)
    - Remove the `/overview` method from the dashboard API module and every call site
    - _Requirements: 3.6, 14.5_

  - [~] 13.3 Declare per-page field availability as data
    - Create `src/design/reported.js` with the `Reported<T>` constructors and accessors from `§18`,
      so a page cannot render a value without destructuring `available`
    - Create `src/design/pageFields.js` declaring, per in-scope page, each Requirement-named field
      with its source path and verdict from `§7`'s tables, now reading the BC-1…BC-6 fields landed
      in Task 12
    - The only entries that remain permanently unavailable are the `§7` **❌** rows with no
      registered backend change — per-deployment realised P&L (rendered as account-wide with the
      explicit label *"Realised P&L (account, today)"* plus a per-deployment not-available marker) —
      and fields a server genuinely reports as `null`: exchange API latency, liquidation price for
      spot positions, and `dag_nodes` past the trace store's ~1h retention
    - Declare the two ⚠️ derivations with their tooltip copy: "Invested (capital in use)" from
      `used_balance`, and the Requirement 3.3 alert condition as the disjunction over
      `exchange.exchanges[].status`, `strategies.items[].status` and `executions[].status`
    - _Requirements: 14.5, 19.2, 19.3_
    - _Property: P5_

  - [ ]* 13.4 Write the data-honesty integration tests
    - `tests/unit/dataHonesty.test.jsx`: for each `pageFields.js` entry, assert a failed read renders
      no figure and no zero; assert `latencyMs: null` renders not-available and never `0 ms`; assert
      a `degraded` positions marker renders an error rather than an empty state; assert every
      not-available marker carries a non-empty reason
    - _Requirements: 14.5, 19.3_

- [~] 14. Checkpoint — M6 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - This step must precede the page-layout steps so each page is restructured **once**, with correct
    availability states already in place, rather than restructured and then corrected (`§14.3`).
  - Deliberate visible change: a small number of figures that read `0.00` today become "—". The
    release note must say so explicitly so it is not mistaken for a regression (`§14.2` M6).

- [ ] 15. M7 — Trade History (`/app/trades`)

  - [~] 15.1 Rebuild `src/pages/TradeHistory.jsx` on `DataTable`
    - `PageHeader` + Live/Paper switch + Export CSV; `TradingEnvironmentBadge variant="strip"` when
      Paper; summary row of Trades / Win rate / Total P&L / Total fees; `FilterBar`; `DataTable` with
      sticky header and pagination at 50 rows/page (`§7.7`)
    - Add the **missing `status` column** from `row.status` / `order_lifecycle_state` (Requirement
      11.1 is unsatisfied today); right-align quantity, price, P&L, fees and slippage via
      `align: 'numeric'` — every column is `textAlign: "left"` today
    - Drop the `#` row-index column: a row index is not information and it was the only reason the
      table needed twelve columns
    - Delete this page's duplicate `SimulatedIndicator` definition in favour of
      `TradingEnvironmentBadge`, preserving its `SIMULATED · SERVER LABEL UNAVAILABLE` copy
    - Data source stays `api.orders.getHistory()` / `api.paper.getTrades(100)`; keep the existing
      `minWidth: 780` inside its `overflowX: auto` wrapper — that is the pattern to generalise
    - Lower this page's `legacy-c-budget` and `no-colour-literals` entries to zero (43 inline styles,
      `#0c1017 #1e293b #64748b`), and add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 11.1, 11.3, 11.4, 11.6, 12.2, 15.4, 17.2_

  - [~] 15.2 Extract the filter and search predicate into `src/pages/tradeHistoryFilters.js`
    - A pure module following `pages/paperTradingFormat.js`'s precedent: the side/outcome filters,
      the market and strategy selects, and the search over market + strategy, combined into one
      predicate, plus `totalCount`/`resultCount` derivation feeding `FilterBar` and the
      `EmptyState` variant decision
    - _Requirements: 11.2, 11.5_
    - _Property: P18_

  - [ ]* 15.3 Write the property test for filtering and search
    - **Property 18: Filtering and search yield exactly the predicate-satisfying rows**
    - **Validates: Requirements 11.2**
    - Generate row sets and filter/query combinations including the empty query, a query matching
      nothing and one matching everything; assert the rendered row id set equals the set satisfying
      the combined predicate; minimum 100 iterations

  - [ ]* 15.4 Write the Trade History example tests
    - Assert the `EmptyState` distinguishes "no trades at all" from "no trades match the current
      filter" and that the no-match case offers clear-filters; assert the Paper environment strip is
      present in paper mode; assert pagination controls appear only when `totalCount > pageSize`
    - _Requirements: 11.4, 11.5, 12.2_

- [ ] 16. M7 — Portfolio (`/app/portfolio`)

  - [~] 16.1 Create `src/design/pageHierarchy.js` and rebuild Portfolio's tier 1
    - Declare the `PageHierarchy` tier lists from `§18` as data for Portfolio, Dashboard, Live
      Trading and Backtester, so `§7`'s layout contract is assertable rather than implied
    - Render Portfolio tier 1 as a single container holding total value, available balance, invested
      (capital in use), unrealised P&L, realised P&L, total exposure **and current drawdown** —
      Requirement 10.2 puts drawdown in this row, not in a lower section
    - Read available balance from `dashboard.overview.available_balance`, **not** from
      `/api/portfolio/summary`, which does not carry it (`§7.6`); read current drawdown from BC-1 and
      lifetime realised P&L from BC-5, both landed in Task 12
    - Label the `used_balance` derivation "Invested (capital in use)" with the derivation stated in
      its tooltip — labelling it "invested capital" silently would misrepresent it
    - _Requirements: 10.1, 10.2, 19.2, 19.3_
    - _Property: P4_

  - [~] 16.2 Rebuild Portfolio's positions and chart regions
    - Summary region — position count, long/short split, net exposure, largest position — rendered
      **above** the detailed per-position `DataTable` (Requirement 10.3)
    - `DataTable` columns: Market, Side, Size, Entry, Mark, Notional, Leverage, Unrealised P&L,
      Liquidation, Margin; liquidation `null` renders not-available because spot positions
      legitimately have none
    - Allocation, equity curve and heatmap through `ds/Chart`, refreshed on the page's REST read or
      an explicit period change — **never on a WebSocket tick** (`§13.2`)
    - `EmptyState` for zero positions, `ErrorState` for a failed read; never an empty table for a
      failure (Requirement 14.5)
    - Lower this page's `legacy-c-budget` and `no-colour-literals` entries to zero (82 inline styles,
      18 of the dead typography classes already fixed in 3.1) and add the page to the Task 8.11 /
      8.12 / 10.13 registries
    - _Requirements: 10.3, 10.4, 14.5, 15.5_

  - [x]* 16.3 Write the property test for priority tiers
    - **Property 4: Declared priority tier determines document order**
    - **Validates: Requirements 3.1, 3.2, 3.4, 6.2, 6.3, 7.1, 7.2, 7.3, 10.1, 10.2**
    - Build the test over a registry of `{hierarchy, render adapter}` pairs, seeded with Portfolio;
      generate payloads with arbitrary absent, `null`, zero and wrongly-typed fields and assert every
      tier-*n* element precedes every tier-*(n+1)* element in document order and that no tier-1
      element renders outside the page's single tier-1 container
    - Tasks 19.5, 20.4 and 23.5 register the remaining three tiered pages; minimum 100 iterations

  - [ ]* 16.4 Write the Portfolio example tests
    - Assert drawdown renders in the tier-1 container (Requirement 10.2) and not in a lower section;
      assert the summary region precedes the detail table in DOM order; assert BC-1 and BC-5 values
      render as figures now that Task 12 landed
    - _Requirements: 10.1, 10.2, 10.3_

- [ ] 17. M7 — Strategies (`/app/strategies`)

  - [~] 17.1 Rebuild `src/pages/Strategies.jsx` as a table
    - Replace the card grid with `DataTable`, which satisfies Requirement 4.5 by construction:
      columns Name, Status, Version, Market, Deployment, Performance, Risk, Last signal, Last
      execution, Updated (`§7.2`)
    - Render `last_signal_at` from BC-3 and `last_execution_at` from BC-4, both landed in Task 12
    - Status through `StrategyStatus`; risk through `computeStrategyHealth`, whose `undetermined`
      result renders not-available — the old `row.health ?? "healthy"` default must not regress
    - Performance figures render not-available when `null`, **not** `0`
    - `FilterBar` with status segments, an environment select, search, and the `n of m` count
    - `EmptyState` headline "No strategies yet", body explaining nothing trades until one is
      deployed, action → `/app/builder`
    - Keep both reads as-is (`endpoints.strategies.list()` and `api.library.myStrategies()`); this is
      a presentation change only
    - Lower this page's `legacy-c-budget` and `no-colour-literals` entries to zero (121 inline
      styles) and add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 4.1, 4.4, 4.5, 14.5_
    - _Property: P5_

  - [~] 17.2 Render the action set from `allowed_actions` with the destructive partition
    - Preserve the existing `entry.allowed_actions` × `ACTION_CATALOG` mapping **verbatim** — it is
      structurally incapable of offering an action the server did not return (`§7.2`)
    - Inline: Backtest, Edit, Duplicate, View, Signal Trace. Below a divider in the row's overflow
      menu: `Deploy live` (`intent="live"`) and `Delete` (`intent="destructive"`), each requiring a
      `ConfirmDialog`. `Deploy paper` is **not** separated — it is not a live transition
      (Requirement 4.3)
    - _Requirements: 4.2, 4.3, 7.6_
    - _Property: P6, P7_

  - [ ]* 17.3 Write the property test for the rendered action set
    - **Property 6: The rendered action set never exceeds what the server permitted**
    - **Validates: Requirements 4.2**
    - Generate `allowed_actions` lists including unknown members, duplicates and the empty list;
      assert the rendered id set is a subset of both the input and the client catalogue; 100 iterations

  - [ ]* 17.4 Write the property test for the destructive partition
    - **Property 7: Destructive and live-transition actions are partitioned into the separated group**
    - **Validates: Requirements 4.3**
    - Assert the partition of rendered actions exactly matches the classifier: every accepted action
      is in the separated group and no rejected action is; minimum 100 iterations

- [~] 18. Checkpoint — M7 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - The three table-led pages exercise `DataTable` hardest and will surface its bugs before the more
    complex monitoring pages depend on it (`§14.3`), so this checkpoint is what unblocks Task 19.

- [ ] 19. M8 — Dashboard (`/app/dashboard`)

  - [~] 19.1 Rebuild `src/pages/Dashboard.jsx` on the tier hierarchy
    - Tier 1 as a single `grid-template-columns: repeat(4, 1fr)` container holding portfolio value,
      today's P&L, total P&L and current drawdown — Requirement 3.4 is satisfied structurally because
      `Metric tier={1}` is only ever used inside that one container (`§7.1`)
    - Current drawdown reads BC-1's field, landed in Task 12.1
    - Tier 2: active strategies, open positions (top 5 + "View all"), system & exchange health via
      `ExchangeStatus`, recent signals & orders, equity curve
    - Single read through `dashboardApi.getDashboard({environment, equity_days})`; a 503 renders **one**
      page-level `ErrorState` with retry, because one read means one failure and per-panel errors would
      imply independent reads that do not exist. No panel falls back to a cached value
    - Exchange API latency `null` renders not-available, never `0 ms`
    - This page has 240 inline styles and **zero** `C.` references — it uses Tailwind-default
      `#ef4444`/`#64748b` with no token source at all (`§1.1 G5`). Lower its `no-colour-literals`
      budget entry to zero and add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6, 14.5_
    - _Property: P4, P12_

  - [~] 19.2 Add the Requirement 3.3 alert strip
    - Render an `Alert` summarising the condition **above** the tier-2 elements when any exchange
      connection, live strategy or order submission is in an error or disconnected state, derived from
      the disjunction over `exchange.exchanges[].status`, `strategies.items[].status` and
      `executions[].status` declared in Task 13.3
    - Keep the kill switch — it is a risk control and Requirement 19.1 forbids touching its logic — but
      route it through `ConfirmDialog intent="destructive"` with an acknowledgement, since it halts all
      trading (`§8.4`)
    - _Requirements: 3.3, 7.6, 19.1_

  - [~] 19.3 Apply tick isolation to the Dashboard
    - Move `wsClient` subscriptions from the page root into the leaves that render the value:
      `PnLDisplay` subscribes to `pnl` filtered to its symbol, `StrategyStatus` to `STRATEGY_STATUS`
      filtered to its strategy id, `ExchangeStatus` to exchange health (`§13.2b`)
    - The page root retains only **structural** data — which positions and strategies exist — which
      changes on a REST read, not on a tick
    - Memoise every primitive receiving a live value with primitive props only
    - Exclude `equity_curve` from tick updates entirely: recharts re-renders are the most expensive
      thing on this page and no requirement asks for a tick-live equity curve
    - Replace this page's `usePolling` usage with `usePanelState` — `usePolling` lists `data` in its
      `fetch` dependency array, so it tears down and recreates its interval on every tick (`§1.12`)
    - _Requirements: 2.6, 7.5_

  - [~] 19.4 Remove the gamified and unrequired surfaces from the Dashboard
    - Delete `src/components/DashboardUpgrades.jsx` (786 lines, 135 `C.` refs, gamified upgrade
      prompts) and its import
    - Delete the layout-density toggle and its `vyomquant_dashboard_density` localStorage key — a
      preference with no requirement behind it that adds a second layout to maintain (`§7.1`); a
      lingering value is harmlessly ignored
    - Remove the kill-switch modal's `window`-level styling in favour of `ConfirmDialog`
    - _Requirements: 1.5, 19.4_

  - [ ]* 19.5 Extend the tier-order property to the Dashboard
    - **Property 4: Declared priority tier determines document order**
    - **Validates: Requirements 3.1, 3.2, 3.4**
    - Register the Dashboard hierarchy and render adapter in the Task 16.3 registry; additionally
      assert no second row of equally-weighted tier-1 metric cards can be produced for any payload

  - [ ]* 19.6 Write the tick-isolation regression test
    - `tests/unit/dashboard/tickIsolation.test.jsx`: push a `pnl` tick for one symbol and assert only
      the components selecting that symbol re-render (spy on render counts), that chart components do
      not re-render, and that a poll does not recreate its interval
    - _Requirements: 2.6_

- [ ] 20. M8 — Live Trading (`/app/live-trading`)

  - [~] 20.1 Create `src/pages/LiveTrading.jsx` and repoint the route
    - The route currently renders `Dashboard`; it becomes its own per-deployment operational view
      (`§7.5`). Update the lazy route in `App.jsx`
    - Deployment selector `DataTable`, then three tiers per deployment: tier 1 connection / exchange /
      account / strategy+version / market / environment; tier 2 position, entry, mark, unrealised P&L,
      realised P&L, exposure, risk; tier 3 latest signal, latest order, execution status; then signal
      and order history linking to Signal Trace
    - Reads: `dashboardApi.getDashboard({environment: 'live'})` for deployments, positions, executions,
      exchange and risk; `api.orders.getOpen()`; `useConnectionStatus()`
    - Per-deployment realised P&L is the one **❌** field with no backend change: render account-wide
      with the explicit label *"Realised P&L (account, today)"* plus a per-deployment not-available
      marker. Mislabelling account-wide as per-deployment would be the fabrication Requirement 14.5
      forbids
    - Liquidation distance via the existing `computeLiquidationDistance`; `null` liq price →
      not-available
    - `TradingEnvironmentBadge variant="strip"` under the header and `variant="chip"` on every panel
      showing position, order or P&L data
    - Add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 14.5_
    - _Property: P4, P12_

  - [x] 20.2 Extract the shared position, order and execution panels
    - Create `src/components/trading/{PositionsPanel,OrdersPanel,ExecutionsPanel}.jsx`, parameterised
      by environment, and consume them from Live Trading. This is the structural reading of
      Requirement 12.1's "same visual language" — Task 25.2 points Paper Trading at the same
      components (`§7.8`)
    - Subscribe at the leaf via `useLiveChannel` per `§13.2`; a `STRATEGY_STATUS` push is what makes
      Requirement 7.5's 5-second bound hold with no polling
    - _Requirements: 7.2, 7.3, 7.5, 12.1_

  - [~] 20.3 Wire the live destructive actions
    - `Stop deployment` → `ConfirmDialog intent="destructive" environment="LIVE"` with a review grid
      naming strategy, market and current position, and copy stating what happens to the open
      position. **No acknowledgement checkbox** — stopping is risk-reducing (`§7.6`, `§8.4`)
    - `Cancel live order` → `ConfirmDialog`, no acknowledgement. `Cancel all orders` → acknowledgement
      **required**, because it is bulk and irreversible
    - No manual-order affordance is added; `POST /api/orders/execute` stays behind its algo-only guard
      (Requirement 19.1)
    - _Requirements: 7.6, 18.1, 19.1_
    - _Property: P13_

  - [ ]* 20.4 Extend the tier-order property to Live Trading
    - **Property 4: Declared priority tier determines document order**
    - **Validates: Requirements 7.1, 7.2, 7.3**
    - Register the Live Trading hierarchy and render adapter in the Task 16.3 registry

  - [x]* 20.5 Write the deployment-state push test
    - `tests/unit/liveTrading/deploymentState.test.jsx`: with fake timers, assert a `STRATEGY_STATUS`
      stop/error/disconnect message updates the tier-1 connection element without advancing timers
      past 5 seconds, satisfying Requirement 7.5 by construction
    - _Requirements: 7.5_

- [ ] 21. M8 — Signal Trace (`/app/signal-trace`)

  - [x] 21.1 Create the canonical nine-stage projection in `src/lib/signalTraceStages.js`
    - Build the nine stages from the **canonical list outward** and then attach whatever the payload
      provides — the inversion is what makes Requirement 9.1's "in order" and 9.2's "rather than
      omitting it" structural, and why an unknown event type cannot add or remove a row (`§10.2`)
    - Map each stage to its real backing per `§10.1`: `signal.market_info`, `signal.indicators` +
      `trace.dag_nodes`, `trace.ml_inference` (whose `applicable: false` is a genuine
      not-applicable), `LOGIC`-category `dag_nodes` + `signal.decision`, and the five timeline event
      types `SIGNAL_GENERATED`, `RISK_EVALUATED`, `ORDER_CREATED`, `EXCHANGE_RESPONSE`, `EXECUTED`
    - **Stage 9 now reads BC-6's `POSITION_UPDATED` event**, landed in Task 12.6, so it renders as a
      real stage rather than the registered not-available state
    - Five-state model: `complete`, `blocked`, `pending`, `not-applicable`, `not-available`. Stage 4
      renders not-available with the retention reason when `dag_nodes` is empty — the trace engine
      retains roughly an hour, so an older signal legitimately has none, and that is not "pending"
    - Surface the response's `degraded` and `lifecycle_state_source` as a `status.warning` note above
      the timeline when degraded; hiding a real server signal would misrepresent the trace
    - _Requirements: 9.1, 9.2, 19.3_
    - _Property: P15, P16_

  - [x]* 21.2 Write the property test for stage presence and order
    - **Property 15: The nine trace stages are always all present, in order**
    - **Validates: Requirements 9.1**
    - Generate payloads with no events, reversed and arbitrary orderings, duplicated events, unknown
      event types and absent trace sections; assert the rendered sequence equals the canonical nine
      ids in canonical order, exactly once each; minimum 100 iterations

  - [x]* 21.3 Write the property test for stage state
    - **Property 16: A stage's state is a function of its own backing record**
    - **Validates: Requirements 9.2**
    - Assert no stage with a backing record is ever `pending` and no stage without one is ever
      `complete`, and that `not-applicable` occurs exactly when the server said so; 100 iterations

  - [x] 21.4 Rebuild `src/pages/SignalTrace.jsx` with collapsed independent rows
    - `PageHeader` + strategy/environment selects + `TradingEnvironmentBadge`; `FilterBar` + a signal
      list `DataTable` (Time, Strategy, Market, Decision, Outcome); then the selected signal's
      timeline
    - Every stage row is collapsed on first render and is an independent `<button aria-expanded>`
      controlling its own region, so expanding one cannot collapse or affect another (`§10.3`)
    - The one-line summary carries stage number, name, human summary and latency **only where the
      response reports one** — stages 2, 3, 4 and 8; stages 1, 5, 6, 7 and 9 render the
      not-available marker in the latency slot, never a `0ms` (`§10.3`)
    - The expanded body carries the technical detail: raw indicator values, per-node
      `dag_nodes.nodes` inputs/outputs — the section is a `{source, nodes}` wrapper, not the array —
      ML confidence and model id, each risk check with its verdict, exchange response fields, fill
      detail, and for stage 9 the position change with the server's `not_available` list and
      `not_available_reason` surfaced verbatim
    - `EmptyState` headline "No signal traces for this strategy", body explaining traces are produced
      when a deployed strategy evaluates market data, action → deploy or start a paper session
    - Add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 9.3, 9.4_
    - _Property: P17_

  - [x]* 21.5 Write the property test for independent expansion
    - **Property 17: Every trace stage starts collapsed and expands independently**
    - **Validates: Requirements 9.3**
    - Assert all nine are collapsed on first render, and for any sequence of expand/collapse
      activations each stage's state equals the parity of its own activations and is unaffected by
      activations on any other stage; minimum 100 iterations

  - [~] 21.6 Retoken `src/components/SignalTraceVisualization.jsx`
    - Replace the Material palette (`#2196F3 #00BCD4 #FFAB00 #9C27B0 #FF5722 #00C853 #607D8B`) and the
      GitHub surfaces (`#0d1117 #30363d`) with `semantic.js` tokens (`§1.1 G5`)
    - Expand `PIPELINE_STAGES` from its current seven entries to the canonical nine from Task 21.1, so
      the component and the page agree on one stage list
    - Lower this file's and `SignalTrace.jsx`'s `legacy-c-budget` and `no-colour-literals` entries to
      zero (98 inline styles, 92 `C.` refs)
    - _Requirements: 1.1, 9.1_

- [~] 22. Checkpoint — M8 is independently shippable
  - Ensure all tests pass, ask the user if questions arise.
  - All three monitoring pages now hold tick isolation, real connection state and honest stage states.

- [ ] 23. M9 — Backtester (`/app/backtest`, `/app/backtester`)

  - [x] 23.1 Rebuild the configuration flow in `src/pages/Backtester.jsx`
    - Four `Panel`s in a single column in the Requirement 6.1 order — strategy (version pinned),
      market & data, period, capital & risk — then the run action (`§7.4`)
    - Reuse the existing `components/builder/AssetSelector.jsx` and `TimeframeSelector.jsx` rather
      than rebuilding market selection; include the data-availability note from `dataQualityApi`
    - Slippage, commission and fill model go in a collapsed advanced accordion (Requirement 15.6)
    - `Run backtest` is `disabled` with a `disabledReason` while running (Requirements 6.5, 15.3)
    - _Requirements: 6.1, 6.5, 15.3, 15.6_

  - [x] 23.2 Rebuild the results region
    - Tier 1: total return, net P&L, max drawdown, Sharpe, win rate, trade count in one row. Tier 2:
      equity curve and drawdown curve. Tier 3: Trades / Monthly returns / Extended statistics behind
      `Tabs`, collapsed by default (`§7.4`)
    - On failure the tier-1 figures are **not rendered at all** rather than rendered as zeros
      (Requirement 6.6); `Panel state="loading"` covers the whole result region while running
    - Keep `mapBacktestExecutionToUI` from `api/modules/strategies.js` as the mapper
    - Lower this page's `legacy-c-budget` and `no-colour-literals` entries to zero (86 inline styles,
      66 `C.` refs) and add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 6.2, 6.3, 6.4, 6.6, 14.5_
    - _Property: P4_

  - [x] 23.3 Derive the drawdown curve in `src/lib/drawdownSeries.js`
    - The backend does not return a drawdown series; compute running peak minus current per point from
      the real equity curve, in `lib/` with a unit test, so it is a derivation from a real series and
      not a fabricated shape (`§7.4`)
    - Include the unit test for the derivation in this task: monotonic-rising series → all zeros;
      a peak-then-trough series → the exact trough depth
    - _Requirements: 6.3, 14.5_

  - [ ]* 23.4 Write the property test for the run control
    - **Property 11: A non-terminal run disables its trigger and shows a loading state**
    - **Validates: Requirements 6.5**
    - Generate every declared lifecycle value plus arbitrary strings; assert the control is disabled
      and a loading state is present iff the state is non-terminal, so it re-enables exactly when the
      run completes or fails; minimum 100 iterations

  - [ ]* 23.5 Extend the tier-order property to the Backtester
    - **Property 4: Declared priority tier determines document order**
    - **Validates: Requirements 6.2, 6.3**
    - Register the Backtester hierarchy and render adapter in the Task 16.3 registry; this completes
      P4's four tiered pages

- [ ] 24. M9 — Strategy Builder (`/app/builder`)

  - [x] 24.1 Apply the five-stage visual grammar to the canvas
    - Add the stage band layer from `§9.1` over the backend's seven `BlockCategory` values in
      `src/lib/blockRegistry.js` and `src/pages/StrategyBuilder.jsx`: Market data / Transform / Logic /
      Model / Action, with a persistent lane header strip above the canvas and a fixed left-to-right
      lane order
    - Each node carries its stage number, category name and stage icon; edges are `line.strong` at
      rest and `brand` when their source or target is selected; node bodies are `surface.raised` for
      **every** stage, so colour is spent only on the stage band edge and validation markers
      (Requirement 1.5)
    - An unknown category resolves to a neutral sixth "Unresolved" band rather than being hidden,
      matching `blockRegistry.js`'s `FALLBACK_PRESENTATION` philosophy — a block the backend says
      exists must be drawable
    - Keep the existing port chips (`data-testid="port-chip"`) and validation markers
    - _Requirements: 5.1, 1.5_

  - [x] 24.2 Make the inspector a sibling grid track
    - Three-track layout: palette 240px / canvas `1fr` / inspector 320px. The inspector has width `0`
      and `display: none` when `selectedNode === null`, so the canvas simply occupies the space and
      nothing overlays it (Requirement 5.3)
    - Selection must not touch the canvas `nodes` array and must not call `fitView`, so React Flow's
      viewport is preserved across selection changes — that is the structural guarantee behind P8
      (`§9.2`)
    - `ParameterForm`'s `BEHAVIOUR_CHANGING_PARAMS` are shown; everything else moves into a collapsed
      Advanced accordion (Requirement 15.6)
    - _Requirements: 5.2, 5.3, 15.6_
    - _Property: P8_

  - [ ]* 24.3 Write the property test for selection
    - **Property 8: Node selection never alters the canvas**
    - **Validates: Requirements 5.2**
    - Generate strategy graphs and select each node; assert the canvas node id set, every node
      position and the viewport transform are identical before and after; minimum 100 iterations

  - [x] 24.4 Rework the four validation surfaces
    - Keep `lib/connectionLegality.js` and `lib/graphValidation.js` **unchanged** — the R1–R8 rules in
      the backend's own evaluation order, reading the served registry, are already correct and already
      wired to `isValidConnection`, `onConnectStart` port dimming, `onConnectEnd` and
      `onReject → setConnectionIssue` (`§1.13`); only fix the stale header comment that claims
      otherwise
    - Move the refusal reason from the full-width top-of-page banner to a transient inline callout
      anchored at the refused drop point, plus a persistent entry in the validation issue list — today
      a trader must look away from their cursor to read why a drag failed
    - Render the reason as two lines **verbatim from the server**: `Cannot connect: {issue.message}`
      then `→ {issue.fix_hint}`, omitting the second line when `fix_hint` is absent rather than
      substituting frontend-authored rule text
    - Separate the four surfaces per `§9.3`: guidance (dashed border, `Info`, `role="status"`),
      saved-data warning (solid, `AlertTriangle`), saved-data error (solid, `AlertOctagon`,
      `role="alert"`), destructive (`ConfirmDialog`, `Trash2`). All five of these meanings render as
      identical `C.gold` monospace banners today
    - Provisional verdicts keep a dashed left rail and backend-confirmed ones a solid rail, preserving
      the existing "(local check — the backend has not validated this version yet)" distinction
    - Replace the `🔒` emoji on the `deployedLock` notice with a `Lock` icon on the warning surface
    - _Requirements: 5.4, 5.5_
    - _Property: P9, P10_

  - [ ]* 24.5 Write the property test for refused connections
    - **Property 9: A refused connection is not created, and its reason names the fix**
    - **Validates: Requirements 5.4**
    - Generate graphs and candidate edges over their nodes and ports; assert a rejected edge leaves
      the edge set unchanged and the surfaced reason contains the issue's `fix_hint` when present or
      its `message` when not; minimum 100 iterations

  - [ ]* 24.6 Write the property test for guidance-surface distinctness
    - **Property 10: Connection guidance is never styled as a destructive or saved-data error**
    - **Validates: Requirements 5.5**
    - Across every rule code and severity, assert the invalid-connection surface resolves to the
      guidance token and never the destructive or error token, and that a saved-data error surface
      never resolves to guidance; minimum 100 iterations

  - [~] 24.7 Implement the tablet review mode
    - Below 1024px (`ROUTE_MIN_VIEWPORT['/app/builder'] === 'LAPTOP'`), the canvas renders with pan,
      pinch/scroll zoom, `fitView` on mount and a zoom control cluster — scrollable and zoomable,
      never clipped (`§11.6`)
    - The palette collapses to a disabled trigger; node creation, connection drawing, deletion and
      parameter editing are off; the inspector opens as a bottom `Drawer` in read-only form
    - A persistent `status.guidance` strip reads *"Review mode. Editing a strategy graph needs a screen
      at least 1024px wide. You can pan, zoom and inspect nodes here."*
    - Save, Backtest and Deploy are `disabled` with a `disabledReason` naming the width requirement
    - This is Requirement 17.4's "restricted editing with a message", and it is strictly better than
      the blanket blur it replaces because a trader on a tablet can still read what a strategy does
    - _Requirements: 17.4, 15.3_

  - [~] 24.8 Wire the builder header actions and the save confirmation
    - `[ Save ] [ Backtest this version ] [ Deploy ]`; `Backtest` navigates to
      `/app/backtest?strategy={id}&version={v}` with the version pre-selected; `Deploy` opens Task
      10.5's flow
    - On save, toast *"Saved as version {v}"* using the version label **returned by the server**, and
      update the header version chip. If the response carries no version label, the toast says
      *"Saved"* rather than guessing a number. No versioning logic changes (Requirement 19.1)
    - Both actions `disabled` with a `disabledReason` when the graph is unsaved or invalid —
      *"Save this strategy before backtesting"* / *"Fix 2 validation errors before deploying"*
    - Lower this page's `legacy-c-budget` and `no-colour-literals` entries to zero (112 inline styles,
      148 `C.` refs) and add the page to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 5.6, 5.7, 15.3, 19.1_

- [ ] 25. M9 — Paper Trading (`/app/paper-trading`)

  - [x] 25.1 Retoken `src/pages/PaperTrading.jsx` and deduplicate the environment indicator
    - **Do not restructure this page.** Behaviourally it is the most correct page in the app:
      `paperTradingFormat.js` already implements exact minor-unit money handling, the eight-state
      panel model, server-error→state classification and per-figure simulated labelling with a
      passing suite (`§7.8`)
    - Replace this page's own simulated labels with `TradingEnvironmentBadge environment="PAPER"`,
      preserving the indigo treatment and the text+shape (not colour alone) behaviour, which become
      the `env.paper` token
    - Re-point onto `ds/` primitives and lower this page's `legacy-c-budget` and `no-colour-literals`
      entries to zero (191 `C.` refs, 127 inline styles); add the page to the Task 8.11 / 8.12 / 10.13
      registries
    - `PaperTrading.test.jsx` must keep passing **unchanged**, including its per-figure label
      assertions and its "does not rely on the page header" control
    - _Requirements: 12.2, 12.3, 1.1_
    - _Property: P12, P22_

  - [~] 25.2 Point Paper Trading at the shared trading panels
    - Consume the same `components/trading/{PositionsPanel,OrdersPanel,ExecutionsPanel}` components
      Live Trading uses, parameterised by environment — the structural reading of Requirement 12.1
      (`§7.8`)
    - _Requirements: 12.1_

  - [ ]* 25.3 Write the property test for real-order safety
    - **Property 23: No control on the Paper Trading page can be read as placing a real order**
    - **Validates: Requirements 12.4**
    - Assert no rendered control's action identifier or accessible name matches the real-order
      vocabulary and that no rendered figure region omits its simulated label; minimum 100 iterations

- [ ] 26. M9 — Marketplace subscription states (`/app/marketplace`)

  - [~] 26.1 Implement the 7→4 subscription-state mapping
    - Declare the collapse from `§7.9` once, driven **only** by the server's
      `entry.subscription.state`, and the entitling decision from `entry.entitling` /
      `entry.unavailable_reason`; the frontend computes neither
    - `CANCELLED` maps to **Subscribed** with the `warning` token and the expiry date, because the
      backend keeps the entitlement alive to the unchanged expiry — showing Expired would tell the
      trader they cannot use something they can
    - An unrecognised state **fails closed** to a non-entitling badge
    - `StrategyMarketplace.jsx` is already fully Tailwind (145 `className`, zero inline styles), so
      this is a presentation-mapping change only; the badge re-renders from the next
      `api.library.browse()` / `myStrategies()` result with no polling added (Requirement 13.3)
    - Preserve `StrategyMarketplace.test.jsx`'s existing zero-`window.alert` assertions; add the page
      to the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 13.1, 13.3_
    - _Property: P24_

  - [ ]* 26.2 Write the property test for the subscription mapping
    - **Property 24: The subscription-state mapping is total and yields exactly one badge**
    - **Validates: Requirements 13.1**
    - Generate each of the seven enum values, the absent-row case, `null` and arbitrary strings;
      assert exactly one of the four badge states, that unknown values fail closed to a non-entitling
      badge, and that each listing renders exactly one badge; minimum 100 iterations

  - [ ]* 26.3 Write the property test for protected-field disclosure
    - **Property 25: A non-entitling listing discloses no protected field**
    - **Validates: Requirements 13.2**
    - Generate listing payloads that carry node graphs, parameter values and other protected fields;
      assert no protected value appears anywhere in the rendered output of an entry whose
      server-reported entitlement is false. The backend's `project_subscribed_listing` already
      allow-lists key by key with two runtime subset assertions — this property is the frontend's
      obligation not to render one if it appears, enforced rather than trusted; 100 iterations

- [ ] 27. M9 — Strategy Detail, and the removal of the legacy layer

  - [~] 27.1 Retoken `src/pages/StrategyDetail.jsx`
    - Route through `PageHeader` with a breadcrumb and the `ds/` primitives; the four native dialogs
      and the "coming soon" panel were already removed in Task 10.4
    - Lower this page's `legacy-c-budget` and `no-colour-literals` entries to zero and add the page to
      the Task 8.11 / 8.12 / 10.13 registries
    - _Requirements: 14.1, 14.2, 14.3, 15.1, 19.4_

  - [~] 27.2 Delete the `C` shim and `src/components/ui-legacy/primitives.jsx`
    - Only reachable once every page above has migrated: `C` had 1,296 references across 30 files at
      the start, and the `legacy-c-budget` guard must read zero for every in-scope file before this
      task runs (`§3.4`, `§14.2` M9)
    - Re-point any remaining out-of-scope importer at `ds/` equivalents or at `design/tokens.js`
      directly, then delete the module, the `legacy-c-budget` guard file and the
      `vyom/no-new-legacy-token` lint rule
    - This is what completes Requirement 1.3 — it is only fully satisfiable here (`§14.4`)
    - _Requirements: 1.1, 1.3_

  - [~] 27.3 Delete `DesktopOnlyOverlay` and close out the guard budgets
    - Delete `src/components/DesktopOnlyOverlay.jsx`, superseded by `ResponsiveGate` in Task 8.4
    - Reduce the `no-colour-literals` allowlist to out-of-scope files only and assert the in-scope set
      is empty, so the budget can never grow back
    - _Requirements: 1.1, 1.3, 17.1_

  - [ ]* 27.4 Add the visual-regression baselines
    - Playwright screenshots of each in-scope route at 1440px plus a fixture page rendering every
      primitive's states, using the existing `playwright.config.ts` (`§15.1`)
    - Baselines are approved deliberately at this point rather than earlier, because the intended
      change through M1–M9 is large and the point of the suite is to catch *unintended* change from
      here on
    - _Requirements: 1.2, 17.1_

- [~] 28. Final checkpoint — the full v1 redesign
  - Ensure all tests pass, ask the user if questions arise.
  - All eight structural guards read clean, all 37 properties have a test, `C` and
    `ui-legacy/primitives.jsx` are gone, and Requirements 1.3 and 19.4 are fully satisfied.

## Notes

- Tasks marked `*` are optional test-writing tasks and can be skipped for a faster MVP. Every
  unmarked task is required.
- Each of the nine migration steps is one PR and is independently shippable. Rollback is a revert
  plus a re-deploy, because `.github/workflows/06-frontend-deploy.yml` treats both directions the
  same and no step introduces a schema change or a persisted-state migration (`§14.5`).
- Checkpoints sit exactly on the M1…M9 boundaries. Task 9 (M4) is what makes Portfolio and Trade
  History reachable and therefore gates Tasks 15–16; Task 14 (M6) precedes all page-layout work so
  each page is restructured once; Task 18 (M7) proves `DataTable` before the monitoring pages depend
  on it; Task 27.2 cannot run until every page task above has lowered its `C.` budget to zero.
- The eight structural guards are placed where they first become enforceable: `tokens.generated`,
  `no-colour-literals`, `no-local-tokens` and `legacy-c-budget` in M1; `dead-tailwind` in M2 (it
  needs the 143 dead classes gone and a build to scan); `nav-contract` in M4; `no-native-dialogs` and
  `no-placeholders` in M5. Three of them carry decreasing budgets that every later page task lowers.
- The activities `design.md §15.2` and `§15.3` list — clean-console walks, layout-shift observation,
  the calm-and-density design judgements, the live-trading safety walkthrough, the keyboard-only
  pass, the screen-reader spot check, the Builder-at-800px check and the post-deploy production
  verification — are **not** tasks here. They are manual activities, and each migration step's PR
  checklist carries them. The Playwright responsive and visual-regression automation is codeable and
  is included, in Tasks 8.11 and 27.4.
- Full WCAG conformance cannot be established by any of the automation above; it needs manual
  assistive-technology testing and expert accessibility review, which `design.md` places outside this
  spec's scope.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0,  "tasks": ["1.1", "1.7", "1.8"] },
    { "id": 1,  "tasks": ["1.2"] },
    { "id": 2,  "tasks": ["1.3", "1.4", "1.5"] },
    { "id": 3,  "tasks": ["1.6"] },
    { "id": 4,  "tasks": ["1.9", "1.10", "1.11", "1.12"] },
    { "id": 5,  "tasks": ["3.1", "3.2", "3.3"] },
    { "id": 6,  "tasks": ["3.4"] },
    { "id": 7,  "tasks": ["5.1", "5.3", "5.5", "5.6", "5.7", "5.8"] },
    { "id": 8,  "tasks": ["5.2", "5.4"] },
    { "id": 9,  "tasks": ["6.1", "6.4", "6.6", "6.7", "6.9", "6.14", "6.15", "6.18", "6.20", "6.23", "6.24", "6.25", "6.26", "6.27"] },
    { "id": 10, "tasks": ["6.2", "6.3", "6.5", "6.8", "6.10", "6.11", "6.12", "6.13", "6.16", "6.17", "6.19", "6.21", "6.22"] },
    { "id": 11, "tasks": ["8.1", "8.4"] },
    { "id": 12, "tasks": ["8.5", "8.6", "8.7"] },
    { "id": 13, "tasks": ["8.8"] },
    { "id": 14, "tasks": ["8.2", "8.3", "8.9", "8.10", "8.11", "8.12"] },
    { "id": 15, "tasks": ["10.1", "10.3", "10.4", "10.8", "10.10"] },
    { "id": 16, "tasks": ["10.5", "10.9"] },
    { "id": 17, "tasks": ["10.2", "10.6", "10.7", "10.11", "10.12", "10.13"] },
    { "id": 18, "tasks": ["12.1", "12.3", "12.6"] },
    { "id": 19, "tasks": ["12.2", "12.4"] },
    { "id": 20, "tasks": ["12.5"] },
    { "id": 21, "tasks": ["13.1", "13.2", "13.3"] },
    { "id": 22, "tasks": ["13.4"] },
    { "id": 23, "tasks": ["15.1", "15.2", "16.1", "17.1"] },
    { "id": 24, "tasks": ["15.3", "15.4", "16.2", "17.2"] },
    { "id": 25, "tasks": ["16.3", "16.4", "17.3", "17.4"] },
    { "id": 26, "tasks": ["19.1", "20.1", "20.2", "21.1"] },
    { "id": 27, "tasks": ["19.2", "20.3", "21.4"] },
    { "id": 28, "tasks": ["19.3", "21.6"] },
    { "id": 29, "tasks": ["19.4"] },
    { "id": 30, "tasks": ["19.5", "19.6", "20.4", "20.5", "21.2", "21.3", "21.5"] },
    { "id": 31, "tasks": ["23.1", "24.1", "25.1", "26.1", "27.1"] },
    { "id": 32, "tasks": ["23.2", "24.2", "25.2"] },
    { "id": 33, "tasks": ["23.3", "24.4"] },
    { "id": 34, "tasks": ["24.7"] },
    { "id": 35, "tasks": ["24.8"] },
    { "id": 36, "tasks": ["23.4", "23.5", "24.3", "24.5", "24.6", "25.3", "26.2", "26.3"] },
    { "id": 37, "tasks": ["27.2"] },
    { "id": 38, "tasks": ["27.3"] },
    { "id": 39, "tasks": ["27.4"] }
  ]
}
```
