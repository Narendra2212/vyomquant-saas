/**
 * The `no-colour-literals` decreasing budget — vyomquant-ui-redesign task 1.10.
 * Requirements 1.1, 1.3. design.md §1.1 G5, §14.4, §15.1.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * A checked-in count, per file, of how many hex / `rgb()` / `hsl()` colour
 * literals that file is still allowed to contain. `no-colour-literals.test.js`
 * measures the real counts and asserts each file matches its entry **exactly**:
 *
 *   * over budget  -> a new literal was introduced. Fix it.
 *   * under budget -> literals were removed but this file was not updated.
 *                     Lower the number here in the same commit.
 *
 * The second half is the point. design.md §14.4 accepts that Requirement 1.3 is
 * only fully true at the end of M9, on the promise that "every step in between
 * strictly increases primitive coverage ... the count of raw <table>/hex
 * literals in in-scope pages may only decrease". A `<=` assertion would let a
 * contributor remove forty literals and leave the budget at its old value, so
 * the next contributor could silently put forty back. Requiring the recorded
 * number to move is what turns that promise into a ratchet.
 *
 * ---------------------------------------------------------------------------
 * HOW TO CHANGE IT
 * ---------------------------------------------------------------------------
 * Lowering a number is routine and is part of the migration task that touched
 * the file — the failure message tells you the new value. Raising a number, or
 * adding an entry, means a new literal entered the tree; that reverses §14.4
 * and needs a reason in the PR description.
 *
 * An entry reaching `0` is expected and stays (task 19.2 takes Dashboard.jsx to
 * zero, for instance). Delete an entry only when the file itself is deleted —
 * `components/ui-legacy/primitives.jsx` goes at task 27.2 with the shim.
 *
 * ---------------------------------------------------------------------------
 * SEEDED FROM THE TREE ON THE DAY TASK 1.10 LANDED
 * ---------------------------------------------------------------------------
 * 38 files, 1467 literals. Measured, not estimated — see the test's
 * `countColourLiterals`, which strips comments first so that a retired value
 * mentioned in a `/* was #FFB74D *\/` note is not counted as a live colour.
 *
 * Scope is `src/pages/**` and `src/components/**`, per task 1.10 and §15.1.
 * `src/index.css`, `src/styles/tokens.css` and `src/design/tokens.js` are the
 * token layer and are not scanned — see the scope note in the test file.
 */

/** Paths are relative to `src/`, forward-slashed, matching the spec's notation. */
export const COLOUR_LITERAL_BUDGET = Object.freeze({
  // -- §1.1 G5, the four hotspots task 1.10 names explicitly ----------------
  // 240 inline styles, zero `C.` references, Tailwind-default #ef4444/#64748b
  // with no token source at all. Tasks 19.1 and 19.2 take this to 0 between them — that is
  // the M8 rebuild of the page onto the tier hierarchy, and 19.2 owns the two risk-control
  // regions 19.1 is forbidden to touch. (This line read "task 3.5" until the M7
  // checkpoint; M2 stops at 3.4, so the largest entry in either budget was pointed at a
  // task that does not exist and was therefore unowned.)
  //
  // 315 -> 307 at task 19.4, which deleted the layout-density toggle — a Standard/Dense
  // preference persisted to `localStorage vyomquant_dashboard_density` with no requirement
  // behind it, and a second set of spacing values to keep correct on every panel, table and
  // figure the page draws (design.md §7.1). The 8 were that control's alone: the segmented
  // container's `#0f141c` and `#1e293b`, and each of the two buttons' selected-state
  // `#1e293b` / `#f8fafc` / `#64748b` triple. This entry is not 19.1's rebuild starting
  // early — the 76 `isDense ? … : …` ternaries the toggle fed collapsed to their standard
  // branch, which is the spacing the page renders with today, so no committed geometry
  // moved. The other 307 are 19.1's.
  //
  // 307 -> 228 at task 19.1 PART A, which rebuilt the page chrome and tier 1. The 79 that
  // went are those two regions' entire palette:
  //
  //   * the page shell's `#080a0e` / `#e2e8f0` pair, now `bg-surface-canvas` and
  //     `text-content-primary`;
  //   * the whole of the old Zone 1 header — its `rgba(255,255,255,0.06)` rule and `#f8fafc`
  //     `<h1>`, the hand-built LIVE/PAPER toggle (the segmented container, each button's
  //     selected/unselected fill, text, inset ring and glowing status dot) and the
  //     REAL CAPITAL ACTIVE / SIMULATED EXECUTION badges' six — replaced by `ds/PageHeader`,
  //     `ds/TradingEnvironmentBadge` and Portfolio's `ChipRadioGroup`;
  //   * the `Sync` button's `#0f141c` / `#1e293b` / `#94a3b8`, now a `ds/CommandButton`;
  //   * the five capital hero cards — each one's `#0c1017` shell and `#1e293b` border, its
  //     `#64748b` label, its `#f8fafc` figure and its `#10b981`/`#ef4444` sign ternaries,
  //     plus the risk card's three-way `rgba` wash/text/border triple — replaced by ONE
  //     `ds/Panel` holding four `ds/Metric`s, which take their hue from `design/semantic.js`
  //     and accept no colour prop;
  //   * the equity panel's five-button timeframe selector (`#080a0e`, `#1e293b`, `#0284c7`,
  //     `#ffffff`, `#64748b`), which is now the `PageHeader` period control because
  //     `equity_days` is a parameter of the page's one read, and the `#475569` of the
  //     second-read spinner it fed;
  //   * the duplicate "Current Drawdown" row in the risk panel, which read BC-1's deprecated
  //     `current_drawdown_pct` and defaulted to `"0.00%"`.
  //
  // 228 -> 82 at task 19.1 PART B, which rebuilt tier 2 on the `ds/` primitives. The 146
  // that went are the seven old zones' entire palettes, measured per zone against the
  // pre-rebuild file rather than apportioned by hand:
  //
  //   * Zone 3, the open-positions table — 35. Its `#0c1017` shell and `#1e293b` border are
  //     `ds/Panel`; the header, row and cell colours are `ds/DataTable`; the `#10b981` /
  //     `#ef4444` P&L sign ternaries are `ds/PnLDisplay`; the hand-painted long/short pill
  //     is `ds/StatusBadge`; the liquidation-distance threshold hues (red under 10%, amber
  //     under 20%) are gone entirely with Requirement 1.5's calm default — the figure is the
  //     reading. The count above the table is a `ds/Metric`, the no-rows case is
  //     `ds/EmptyState`, and BC-2's degraded arm is `ds/Alert`.
  //   * Zone 7, the recent-executions table — 23. The same `ds/Panel` + `ds/DataTable` +
  //     `ds/PnLDisplay` + `ds/StatusBadge` set, because it was the same table written twice.
  //   * Zone 4, active strategies — 29. Shell to `ds/Panel`, the two counts to `ds/Metric`,
  //     every status and health hue to `ds/StrategyStatus`, the empty case to
  //     `ds/EmptyState`. The per-row Pause / Run button's four literals went with the
  //     control: it called no API, so the row read "paused" while the worker kept trading
  //     (Requirement 19.4's dead control). Deployment control is `/app/strategies`'.
  //   * Zone 6, exchange health — 21. The per-venue connection dot and latency text are
  //     `ds/ExchangeStatus`, which is also why neither is presented as a measurement any
  //     more: both fields are constants in the aggregation service. The account-wide
  //     `can_trade` chip is `ds/StatusBadge` and the two `health` figures are `ds/Metric`.
  //   * Zone 8, the equity curve — 11. The `AreaChart`'s inline stroke/fill/grid/axis
  //     colours are `ds/Chart`'s `series[].token`, resolved through `design/semantic.js`,
  //     and the panel is `ds/Panel` with a `ds/LoadingState kind="skeleton-chart"` under the
  //     `Suspense` boundary the lazy import needs. This is also the change that takes
  //     `pages/Dashboard.jsx` off `Chart.test.jsx`'s recharts importer list.
  //   * Zone 5, the Risk & Safety Matrix — 18, removed rather than migrated. Its three rows
  //     were a daily-loss bar defaulting to `$0.00 / $500.00` for an account nothing had
  //     been read for, an "Open Position Capacity" reading `positions.length /
  //     (max_positions || 10)`, and a second copy of the kill-switch state. None is a §7.1
  //     field, the first two stated limits the server never reported, and `/app/risk` owns
  //     those figures — the page links to it. No risk-control LOGIC is touched.
  //   * Zone 7b, the Operational Insights list — 9, also removed. `recent_activity.insights`
  //     has no `pageFields` entry and no §7.1 row, and two of its three items are prose the
  //     service hardcodes. Its warning-and-worse subset still feeds the alert strip below.
  //
  // Plus, across all seven: the eight zone shells' repeated `#0c1017` / `#1e293b` /
  // `#080a0e` chrome, now one `ds/Panel` each, and the four hand-built "view all" buttons,
  // now `<Link>`s on token utilities.
  //
  // The remaining 82 are TASK 19.2's, and are the two regions Requirement 19.1 forbids this
  // task from touching:
  //
  //   * 59 in the kill switch — 6 in the EMERGENCY HALT / RESUME TRADING trigger, 30 in the
  //     diagnostics popover beside it, 23 in the `window`-level confirmation modal. 19.2
  //     routes the switch through `ds/ConfirmDialog` with an acknowledgement and folds the
  //     popover into the System & exchange health panel.
  //   * 23 in the Requirement 3.3 alert banner — 7 in the kill-switch-active strip, 8 in the
  //     circuit-breaker strip, 8 in the dynamic execution alerts. 19.2 renders the condition
  //     through `ds/Alert`, derived from the disjunction Task 13.3 declares.
  //
  // 82 -> 59 at task 19.2 PART A, which replaced all three bands of that banner with
  // `ds/Alert`, driven by `design/alertCondition.js`. The 23 that went are the banner's
  // entire palette, and the count is exactly the split above because part A's scope was the
  // banner and nothing else:
  //
  //   * the kill-switch strip's `rgba(239,68,68,0.15)` wash, `#ef4444` border, `#ef4444`
  //     icon, `#f8fafc` headline and `#94a3b8` body, plus its Resume Trading button's
  //     `#eab308` fill and `#000` label — 7;
  //   * the circuit-breaker strip's matching `rgba(234,179,8,0.15)` / `#eab308` / `#eab308` /
  //     `#f8fafc` / `#94a3b8` set and its Review Risk Settings button's `#1e293b` fill,
  //     `#334155` border and `#f8fafc` label — 8;
  //   * the dynamic execution rows' two `alert.severity === "critical" ? … : …` ternaries for
  //     the wash and the border (4 literals across the two), the icon's third ternary (2),
  //     the `#f8fafc` message and the `#38bdf8` action link — 8.
  //
  // The hue, the icon, the border style AND the live-region role now come from `ds/Alert`'s
  // `severity`, which is the part worth recording here: those ternaries were choosing
  // `role="alert"` versus `role="status"` by drawing a border colour, so a routine message
  // could interrupt a screen reader mid-sentence (Requirement 16.2). The third band went
  // rather than migrating — it read `recent_activity.insights`, which has no `pageFields`
  // entry and is not one of the condition's three declared inputs.
  //
  // So this entry does NOT reach 0 here, and 59 is not a resting place either: the 59 are the
  // kill switch's alone, and part B takes them to 0 when it routes the switch through
  // `ds/ConfirmDialog` and folds the popover into the health panel.
  //
  // 59 -> 0 at task 19.2 PART B, which did exactly that. The 59 were the kill switch's three
  // surfaces, and the split is the one recorded above:
  //
  //   * the trigger — 6. The halt arm's `rgba(239,68,68,0.15)` fill, `#ef4444` border and
  //     `#ef4444` label, and the resume arm's `rgba(234,179,8,0.2)` / `#eab308` / `#eab308`
  //     set. Both arms are now one `ds/CommandButton intent="destructive"`, which takes its
  //     hue from the intent and accepts no colour prop.
  //   * the diagnostics popover — 30, removed rather than migrated. Three of its five rows
  //     were declared tier-2 fields the System & exchange health panel already reports —
  //     `health.exchange_api_latency_ms`, `health.order_state_sync_status` and the venue
  //     count — and the two that were only there, the WebSocket stream state and the
  //     circuit-breaker reading, moved into that panel as `ds/StatusBadge`es. Three
  //     substituted readings went with it and were NOT carried across: `|| "optimal"` and
  //     `|| "Synchronized"` each stated a grade the server never sent, and
  //     `"Armed / 0 Breaches"` reported a breach count no field carries (Requirement 14.5).
  //     The pill that opened it went too — its dot was colour-only state, and its
  //     `"Live Connected" : "Reconnecting..."` ternary claimed a retry was under way for the
  //     `error` and `failed` states where the client has stopped trying.
  //   * the `window`-level confirmation modal — 23. Its `rgba(0,0,0,0.75)` scrim and
  //     `rgba(0,0,0,0.8)` shadow, its `#0c1017` shell with the `#ef4444`/`#eab308` border
  //     ternary, the icon disc's `rgba(239,68,68,0.2)`/`rgba(234,179,8,0.2)` pair and the
  //     `ShieldAlert`'s own `color=` ternary, the `#f8fafc` title, the `#94a3b8` eyebrow with
  //     its `#10b981`/`#818cf8` LIVE-versus-PAPER ternary, the `#cbd5e1` body, the error
  //     box's `rgba(239,68,68,0.15)`/`#ef4444`/`#f8fafc` triple, and the two footer buttons'
  //     `#1e293b`/`#94a3b8` and `#ef4444`/`#eab308`/`#fff`/`#000` sets. All of it is now
  //     `ds/ConfirmDialog`'s: the scrim, the shadow and the chrome are the dialog's own, the
  //     accent comes from `design/semantic.js` through `intent="destructive"`, the
  //     live-versus-paper distinction comes from `ds/TradingEnvironmentBadge` and
  //     `ENVIRONMENT` through the `environment` prop, and the error box is `ds/Alert`.
  //
  // The `0` entry stays, for the reason `pages/Portfolio.jsx: 0` and `pages/TradeHistory.jsx:
  // 0` do: it records that the page is clean and holds it there. An entry is deleted only
  // when the file is.
  'pages/Dashboard.jsx': 0,
  // New at task 20.1, which gave `/app/live-trading` its own page instead of a second
  // `<Dashboard />`, and entered the tree clean: every colour on it comes from a `ds/`
  // primitive or a token utility class. Seeded at `0` for the reason `pages/Dashboard.jsx: 0`
  // and `pages/Portfolio.jsx: 0` stay there — an in-scope page with no entry is a page whose
  // cleanliness nothing is holding, and this is the page where a hand-painted green
  // CONNECTED chip would be most expensive. Tiers 2 and 3 land on the same primitives.
  'pages/LiveTrading.jsx': 0,
  // 138 -> 0 at task 21.6, which was the largest entry in either budget. The 138 were the
  // Material Design palette (#2196F3 #00BCD4 #FFAB00 #9C27B0 #FF5722 #00C853 #607D8B) and
  // its GitHub surfaces (#0d1117 #30363d #161b22 #8b949e #c9d1d9), spread over four local
  // maps — `PIPELINE_STAGES`, `SIGNAL_STATES`, `ERROR_TYPES`, `SIGNAL_TYPES` — plus ~90
  // inline `style` hues in the renderers. All four maps are gone: state colour comes from
  // `design/semantic.js`'s `statusToken`, the trace and node chips are `ds/StatusBadge`,
  // the error block is `ds/Alert` at `error`, every absent value is `ds/Metric`'s
  // `NotAvailableMarker`, and the surfaces are `bg-surface-*` / `border-line-*` utilities.
  //
  // Five of the seven Material hues were DECORATIVE per-stage accents, which is why the
  // retoken deletes them rather than mapping them: Requirement 1.5 spends colour on state,
  // and a stage's identity is its number, its name and its icon. The same commit replaced
  // the file's own seven-entry stage list with the canonical nine imported from
  // `lib/signalTraceStages.js` (design.md §10.1), so there is one list, not two.
  //
  // The twelve hexes still in the file are all inside its header docblock, which records
  // the retired palette the way this budget file records it. `countColourLiterals` strips
  // comments first, which is exactly the case that provision exists for.
  'components/SignalTraceVisualization.jsx': 0,
  // Cleared by task 15.1, which rebuilt the page on `ds/DataTable`, `ds/FilterBar`,
  // `ds/Panel` and `ds/Metric`. The 62 were the LIVE/PAPER toggle's six literals, the
  // five summary-card hues, the four filter-chip states, the twelve cell colours and the
  // duplicate `SimulatedIndicator`'s two four-literal palettes — every one of which is
  // now a token utility class or comes from `design/semantic.js` inside a primitive.
  'pages/TradeHistory.jsx': 0,
  // Cleared by task 6.25, which rewrote it: the copy is `ds/ErrorState`'s, the
  // chrome is token utilities, and the stack it used to print is Sentry-only.
  'components/ErrorBoundary.jsx': 0,

  // -- In-scope pages, migrated M6-M9 ---------------------------------------
  // Included the single `border-[#ef4444]` arbitrary-value utility task 1.4 recorded, plus
  // 24 further #10b981/#ef4444 occurrences in inline styles.
  //
  // 144 -> 81 at task 17.1, which rebuilt SECTION ONE only — the owner's card grid, the
  // filter dropdown, the four-figure stats row and the archive-refusal box. Those 63 were
  // the card's own palette (the focus ring and banner, the failure banner and its Trace
  // button, the three metric tiles, the timeline and health rows, the progress bar), the
  // status/environment dropdown's per-option `rgba(0,212,255,…)` selection washes, the stats
  // row's four accent hues, the dashed "Create New Strategy" tile and the hand-rolled red
  // alert. Every one is now a token utility class or comes from `design/semantic.js` inside
  // `ds/DataTable`, `ds/StatusBadge`, `ds/StrategyStatus`, `ds/Alert`, `ds/Panel` or
  // `ds/EmptyState`.
  //
  // The remaining 81 are SECTION TWO's — the marketplace ownership cards from
  // `api.library.myStrategies()` — plus the deploy modal's. Task 17.2 owns them; task 17.1
  // was scoped to leave that section's markup, its `ACTION_CATALOG` and its `data-action`
  // attributes byte-for-byte alone, and lowering this to 0 is exactly what that scope
  // forbids.
  //
  // 81 -> 37 at task 17.2, which moved the marketplace OWNERSHIP SECTION onto tokens: the
  // section shell and each entry's card are `ds/Panel`, the failed / unauthorised reads and
  // the non-entitling and unread-paper-session notices are `ds/Alert`, the in-flight state is
  // `ds/LoadingState`, the completed-and-empty read is `ds/EmptyState`, and the two `Tag2`
  // chips are `ds/StatusBadge`. The 44 that went were the section's own panel background and
  // border, its heading and caption greys, the error box's `rgba(239,68,68,0.12)` wash with
  // its border, heading and body reds, the entry cards' `#080a0e`/`#1e293b` shells and names,
  // the two `<dl>` grids' label/value greys, the non-entitling box's
  // `rgba(251,191,36,0.12)`/`#fbbf24`/`#fde68a` triple, the two disclosure panels' rules, and
  // the action-result line's `#fca5a5`/`#94a3b8` pair. Every hue that is left in the section
  // comes from `design/semantic.js` inside a primitive.
  //
  // 37 -> 0 at task 17.2's last part, which rebuilt the DEPLOY MODAL on `ds/ConfirmDialog`.
  // Those 37 were that modal's alone: the `rgba(1,6,8,0.85)` scrim and `rgba(0,0,0,0.6)`
  // shadow, the `#0c1017`/`#1e293b` dialog chrome, the `#00d4ff` eyebrow and `#f8fafc` title,
  // the deploy-error box's `rgba(255,46,84,0.1)`/`#ef4444`, the account picker's palette, and
  // the account / Execution Mode / Capital / Trade Size controls' `#94a3b8`/`#080a0e`/
  // `#1e293b`/`#f8fafc`/`#10b981`. Every one is now a token: the scrim, the shadow and the
  // dialog chrome are `ds/ConfirmDialog`'s, the live-versus-paper hue comes from
  // `design/semantic.js`'s `ENVIRONMENT` through the dialog's `intent` and
  // `ds/TradingEnvironmentBadge`, the error box is `ds/Alert`, and the four controls are
  // `ds/Field`. Cleared with this page's three `a11y-ratchet` findings in the same change —
  // all three were `label-has-associated-control` on those same controls — so the waiver line
  // in `eslint-rules/a11y-ratchet.js` is deleted rather than lowered.
  //
  // The `0` entry stays, for the reason `pages/Portfolio.jsx: 0` and `pages/TradeHistory.jsx:
  // 0` do: it records that the page is clean and holds it there. An entry is deleted only
  // when the file is.
  'pages/Strategies.jsx': 0,
  // 112 -> 86 at task 16.1, which rebuilt tier 1 only; 86 -> 0 at task 16.2, which rebuilt
  // tiers 2 and 3. The 86 were the positions ledger's own palette (the two side chips, the
  // signed P&L cell, the row rules and the venue chip), the equity curve's `#10B981` stroke
  // and its `<linearGradient>` stops, the allocation legend's slate/white text, the P&L
  // heatmap's six `#10B981aa`/`#EF444455` cell washes and their six borders, the page canvas
  // and the LIVE/PAPER toggle's twelve. Every one is now a token utility class, or comes from
  // `design/semantic.js` inside `ds/DataTable`, `ds/StatusBadge`, `ds/PnLDisplay`, `ds/Alert`
  // or `ds/Chart`'s series palette. Cleared with this page's five `C.` references in the same
  // change, as §14.4 requires of a page task.
  'pages/Portfolio.jsx': 0,
  // 20 -> 0 at task 21.4a, which rebuilt the page on `ds/PageHeader`, `ds/FilterBar`,
  // `ds/DataTable`, `ds/Panel`, `ds/Alert`, `ds/StatusBadge` and `ds/Metric`'s
  // not-available marker. The 20 were the connection indicator's four presentations
  // (a hue, a border wash and a background per state, six literals) plus the refusal
  // banner's border/background/text, the new-signal strip's wash, and the row cells'
  // hardcoded cyan/green/red. Every hue now comes from `statusToken`, including the
  // four connection states — they name `connected`, `reconnecting`, `disconnected`
  // and the null-neutral arm, so the page holds no palette of its own.
  'pages/SignalTrace.jsx': 0,
  'pages/Backtester.jsx': 4,
  'pages/StrategyBuilder.jsx': 3,
  'pages/PaperTrading.jsx': 1,
  // Subscription-state elements only (Requirement 13); the rest of the page is
  // out of scope, which is why the number is this large.
  'pages/StrategyMarketplace.jsx': 188,

  // -- Shared primitives, retokened in M3 -----------------------------------
  // 40 of these 68 are #10B981 (24) and #EF4444 (16), across the
  // profit/green/success/running/deployed/active and loss/red/danger/failed
  // entries of `variants` and `dotColors`, written as arbitrary-value utilities
  // (`bg-[#10B981]/10`). Task 6.6 moves them to #26A69A/#EF5350 (§13.2). They
  // are budgeted, not excluded, precisely because they are scheduled.
  // (Task 1.4's audit recorded 25 for this file; 40 is the measured occurrence
  // count. See the FINDINGS note in the test file.)
  'components/ui/Badge.jsx': 68,
  'components/ui/Button.jsx': 5,
  // The `C` shim. Its top-level values are derived from tokens.css, but `Badge`
  // and `Tag` still carry off-palette rgba() washes inline — see the test's
  // FINDINGS note. Entry is deleted with the file at task 27.2.
  'components/ui-legacy/primitives.jsx': 18,

  // -- Shell and cross-cutting components -----------------------------------
  'components/DeployPreflightPanel.jsx': 30,
  // `components/DashboardUpgrades.jsx: 6` stood here until task 19.4 deleted the file.
  // Removed rather than lowered to `0`, per this header: a `0` records that a live file is
  // clean and holds it there, but a deleted file has no source to measure and
  // `names only files that still exist` fails on an entry pointing at nothing.
  // Cleared by task 8.6, which rebuilt it against `shell/navigation.js`. The five were
  // the brand tile's `linear-gradient(135deg,#00d4ff,#0055ff)` and its `#000` glyph, the
  // notification badge's `#000`, and the active row's `rgba(0,212,255,0.07)`. The
  // gradient went for a second reason: Requirement 1.5 retires gradient washes.
  'components/Sidebar.jsx': 0,
  'components/CopilotChat.jsx': 3,
  'components/FirstTradeWizard.jsx': 3,
  // Replaced by `ResponsiveGate` in M4 (task 12.x); entry goes with the file.
  'components/DesktopOnlyOverlay.jsx': 1,
  // Cleared by task 8.7. The one literal was the notification badge's `#000` text on a
  // `C.cyan` circle; the badge now takes `text-brand` on `bg-surface-raised`.
  'components/TopBar.jsx': 0,

  // -- Deferred pages: retokened by M1, layouts stay older (§14.4) ----------
  'pages/ExchangeManager.jsx': 128,
  'pages/RiskSettings.jsx': 53,
  'pages/AuthPage.jsx': 30,
  'pages/TwoFA.jsx': 12,
  'pages/Billing.jsx': 9,
  'pages/Profile.jsx': 7,
  'pages/Wizard.jsx': 5,
  'pages/StrategyDetail.jsx': 4,
  'pages/LegalPage.jsx': 3,
  'pages/SecurityLogs.jsx': 3,
  'components/SupportCenter.jsx': 37,
  'components/NotificationCenter.jsx': 1,

  // -- Landing page: explicitly out of scope for v1 -------------------------
  // index.css's own header calls the landing `@utility` compositions out of
  // scope; these components are the same surface. Budgeted so they cannot
  // grow, with no task scheduled to lower them inside this spec.
  'pages/Landing.jsx': 18,
  'components/landing/Hero.jsx': 3,
  'components/landing/ScreenshotsSection.jsx': 3,
  'components/landing/PortfolioAnalytics.jsx': 2,
  'components/landing/ScreenshotComingSoon.jsx': 2,
});

/**
 * The token layer. These files *declare* colour, so a guard that forbade
 * literals in them would forbid the thing they exist to do. Named here rather
 * than merely omitted, so the test can assert they are outside the scan and the
 * decision stays visible. Task 1.10 names the first two; `index.css` is the
 * third by the same argument — see the scope note in the test file.
 */
export const TOKEN_LAYER_FILES = Object.freeze([
  'styles/tokens.css',
  'design/tokens.js',
  'index.css',
]);
