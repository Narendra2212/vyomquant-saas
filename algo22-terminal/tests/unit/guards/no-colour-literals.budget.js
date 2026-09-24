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
 * `components/ui-legacy/primitives.jsx` went at task 27.2's final stage B with
 * the shim, and `components/DesktopOnlyOverlay.jsx` went at task 27.3.
 *
 * A new entry goes in ONE of the two groups below, and the test asserts the two
 * are disjoint and cover the whole budget, so "which group" is a decision that
 * has to be made rather than skipped.
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
 *
 * ---------------------------------------------------------------------------
 * SCOPE WIDENED TO `src/lib/**` AT TASK 23.3
 * ---------------------------------------------------------------------------
 * Task 1.10's two roots are where colour is *rendered*, which is why they were
 * seeded first. They are not where colour can only ever be. `lib/blockRegistry.js`
 * carries 8 `C.` references in the other budget precisely because a pure module
 * decided a palette, and a hue chosen in `lib/` and merely spread by a component
 * would sit outside this guard's view entirely.
 *
 * All 20 files under `src/lib/` measure `0` under `countColourLiterals` as this
 * root is added, so widening changes no committed number and fails nothing. It
 * means the next `lib/` module that reaches for a hex has to say so here. The
 * test's stray-entry failure names this as the deliberate move rather than
 * dropping the entry, and the entry it was added for — `lib/drawdownSeries.js` —
 * is at the bottom of the list.
 *
 * ---------------------------------------------------------------------------
 * SPLIT INTO TWO GROUPS AT TASK 27.3
 * ---------------------------------------------------------------------------
 * Until this task the budget was one flat map and every entry was governed by
 * the same rule: hold at the recorded number, lower it when the file improves.
 * That is a ratchet, and a ratchet still has a resting position — `pages/
 * ExchangeManager.jsx: 128` can sit at 128 forever without failing anything,
 * which is correct, because no task in this spec is scheduled to lower it.
 *
 * The problem is that the same leniency covered the pages the spec DID promise
 * to clear. Once `pages/Dashboard.jsx` reaches `0` the entry holds it at `0`,
 * but nothing said it had to get there, and nothing says the next in-scope page
 * added to this file has to either. Requirement 1.3 is a statement about a
 * specific, enumerated set of pages, and the guard could not see that set.
 *
 * So the map is now two maps:
 *
 *   IN_SCOPE   — the pages design.md gives a page task in M6-M9, plus the
 *                shared surfaces those tasks built or cleared on the way. The
 *                test asserts EVERY entry here is `0`. There is no headroom to
 *                hold; the number is the constant zero.
 *   OUT_OF_SCOPE — everything else. Budgeted so it cannot grow, with no
 *                promise in this spec that it shrinks.
 *
 * `COLOUR_LITERAL_BUDGET` is the two spread together and is what every existing
 * assertion still reads, so the ratchet is unchanged for both groups; the
 * emptiness assertion is added on top of it, not in place of it.
 *
 * Two consequences worth stating, because they are the whole point:
 *
 *   * An in-scope entry can never be raised above `0`, not even with a reason in
 *     the PR. The only way to legitimise a literal on an in-scope page is to
 *     move the page out of scope, which means editing the spec's own M6-M9 list.
 *   * The in-scope group is where a NEW in-scope page's entry goes, and it can
 *     only be seeded at `0`. A page cannot enter this tree carrying a palette.
 *
 * WHAT DECIDED THE SPLIT. The in-scope list is design.md's M6-M9 page tasks, not
 * a judgement made here: Dashboard (19.1/19.2), Portfolio (16.1/16.2),
 * Strategies (17.1/17.2), Trade History (15.1), Live Trading (20.1), Signal
 * Trace (21.4a), Backtester (23.1/23.2), Strategy Builder (24.1), Paper Trading
 * (25.1), Strategy Marketplace (26.1) and Strategy Detail (27.1). The
 * non-page files in the in-scope group are there because an in-scope task is
 * what put them at `0` — see the note above that sub-block.
 *
 * `components/ui/**` and `components/ui-legacy/**` are OUT of scope here even
 * though task 6.6 is scheduled to move `ui/Badge.jsx`'s 40 #10B981/#EF4444
 * occurrences to the trading palette. Out-of-scope does not mean unscheduled; it
 * means this guard does not assert the file reads zero. Badge will still be a
 * ratchet at whatever number 6.6 leaves it at.
 *
 * ---------------------------------------------------------------------------
 * THE ONE NON-ZERO IN-SCOPE ENTRY, AND WHY THE ASSERTION IS SKIPPED
 * ---------------------------------------------------------------------------
 * `pages/StrategyMarketplace.jsx` reads 182 as this split lands. Task 26.1 took
 * the subscription-state element — the one thing on the page Requirement 13
 * governs — down to zero literals; the remaining 182 are the catalogue and
 * detail chrome, which no task in M9 has rebuilt yet.
 *
 * The assertion is therefore committed as `it.skip` with the reason recorded in
 * the test. It was NOT made to pass by deleting the entry or lowering it: the
 * in-scope set becomes empty because the page is migrated, not because the list
 * was edited. Un-skip it in the same commit that takes
 * `pages/StrategyMarketplace.jsx` to `0` — that one edit is the entire
 * precondition, and the structural assertions beside it (total, disjoint, names
 * the eleven pages, in-scope entries are never raised) run today.
 */

/**
 * The pages design.md gives a page task in M6-M9, plus the shared surfaces those
 * tasks built or cleared. Every entry here must be `0` — see the SPLIT note above.
 *
 * Paths are relative to `src/`, forward-slashed, matching the spec's notation.
 */
export const IN_SCOPE_COLOUR_LITERAL_BUDGET = Object.freeze({
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
  // New at task 20.2, which moved §7.8 (4)'s three shared surfaces out of
  // `pages/LiveTrading.jsx` so that `pages/PaperTrading.jsx` can render the SAME components
  // at task 25.2 (Requirement 12.1). All four entered the tree clean: the panels are
  // `ds/Panel` + `ds/Metric` + `ds/TradingEnvironmentBadge` and every hue comes from those,
  // and `liveFrame.js` renders nothing at all. Seeded at `0` for the reason
  // `pages/LiveTrading.jsx: 0` is — an in-scope file with no entry is a file whose
  // cleanliness nothing is holding, and a hand-painted LIVE chip on a shared panel would be
  // wrong on two pages rather than one.
  'components/trading/PositionsPanel.jsx': 0,
  'components/trading/OrdersPanel.jsx': 0,
  'components/trading/ExecutionsPanel.jsx': 0,
  'components/trading/liveFrame.js': 0,
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

  // -- In-scope pages, migrated M6-M9 (continued) ---------------------------
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
  // M9. 4 -> 3 at task 23.1, which rebuilt the configuration flow only. The one that went
  // was `color: "#000"` on the old run button's cyan fill; the trigger is a
  // `ds/CommandButton` now and takes no colour.
  //
  // 3 -> 0 at task 23.2, which rebuilt the RESULT region. The three were the running
  // overlay's `rgba(1,6,8,0.5)` scrim — gone with the overlay itself, since a run in flight
  // is now `ds/Panel state="loading"` over the whole region rather than a wash over a chart
  // drawn from the previous run — and the `rgba(255,255,255,0.05)` row rule the saved-history
  // and trade tables shared, which is `ds/DataTable`'s own tokened rule now. The page holds
  // no palette of its own: the two curves take their hue from `ds/Chart`'s series tokens.
  'pages/Backtester.jsx': 0,
  // 3 -> 0 at task 24.1. All three were `PremiumNodeWrapper`'s node shadows: a
  // `rgba(0,212,255,0.28)` selection glow and two `rgba(0,0,0,…)` drop shadows, each with a
  // hand-tuned alpha that no other node in the app shared. The node now takes
  // `token.shadow.raised` and `token.shadow.panel`, and its selection ring is
  // `token.brand.base` — so a canvas node's elevation is the same decision as every other
  // raised surface in the app rather than a fourth opinion about black.
  'pages/StrategyBuilder.jsx': 0,
  // New at task 24.4b: §9.3's four validation surfaces, lifted out of the page so the
  // builder composes them rather than repainting a band per call site. It entered the tree
  // clean — the three band surfaces are `ds/Alert`, the destructive one is
  // `ds/ConfirmDialog`, and the one hue it names (the provenance rail's) is asked of
  // `design/semantic.js`'s `statusToken` with the same state key the alert is given, so the
  // rail cannot end up a different colour from the band it is attached to. Seeded at `0`
  // for the reason `pages/StrategyBuilder.jsx: 0` stays there: this is the module eight of
  // that page's bands are about to point at, so a literal here would be a literal on all
  // eight.
  //
  // `tests/unit/builder/validationSurfaces.test.jsx` deliberately has NO entry: this guard
  // scans `src/pages`, `src/components` and `src/lib`, so an entry under `tests/` would
  // fail both `names only files that still exist` and the stray-entry check.
  'components/builder/validationSurfaces.jsx': 0,
  // 1 -> 0 at task 25.1, part 1. The single literal was `SimulatedTag`'s
  // `rgba(168,85,247,0.10)` wash — a 10% tint of the retired `C.purple`, hand-mixed because
  // the shim had no wash to read. The tag takes `ENVIRONMENT.PAPER.wash` now, and its hue and
  // border style come from the same `env.paper` treatment, so the per-figure marker and the
  // page-level `ds/TradingEnvironmentBadge` that replaced this page's own header label are one
  // decision rather than two.
  //
  // NOTE ON THE TASK TEXT: task 25.1 and design.md §7.8 (3) both say "127 inline styles" for
  // this page. That is the `style={{…}}` count, not the colour-literal count, and this guard
  // measures the latter — the page only ever carried ONE literal, which is what this entry
  // said. The 127 `style` attributes are real and are what the retoken half of 25.1 removes;
  // they are not colour literals and were never in this budget's view.
  'pages/PaperTrading.jsx': 0,
  // Subscription-state elements only (Requirement 13); the rest of the page is
  // out of scope, which is why the number is this large.
  //
  // 188 -> 182 at task 26.1, part 2, which repointed the subscription indicator onto
  // `design/subscriptionState`. The six were that one element's whole palette: the panel's
  // `#080A0D` fill and `#202938` rule, the clock glyph's `#00D4FF`, `#8B949E` twice on the
  // label and the date, and `#E6EDF3` on the state word. The panel is now
  // `bg-surface-canvas` / `border-line-default` / `text-content-secondary` / `text-brand`, and
  // the state word is `ds/StatusBadge`, which asks `statusToken` for the hue — so the one
  // element on this page that paints a STATE names no colour at all. The remaining 182 were
  // catalogue and detail chrome, out of Requirement 1.3's scope for this page.
  //
  // 182 -> 0 at task 27.3, which took that chrome to the token layer as well, so the last
  // in-scope page holds no palette of its own. This page carried no inline `style` at all —
  // all 182 were a hex inside an arbitrary-value Tailwind class — so the change is a mapping
  // rather than a rebuild, and the whole map is recorded in the page's own header where a
  // reader can check it against `tokens.css`. Fourteen distinct values across four families:
  //
  //   * SURFACES — 32. `#080A0D` (16 of its 17, the nested tiles and pill fills), `#131722`
  //     (10, the card and panel fills), `#0d1117` (2, the two gradients' far stop),
  //     `#08090c` (1, the page canvas) and `#1A222C` (1, the tag fill) become
  //     `surface-canvas`, `surface-panel` and `surface-inset`. Two of the 33 `#202938` go
  //     here too, not to a line token: they were a border value used as a disabled control's
  //     FILL, which is the inset surface.
  //   * LINES — 31. The other `#202938`, all of it the default line token.
  //   * CONTENT — 69. `#8B949E` (48) is `content-secondary`; `#E6EDF3` (17), `#e2e8f0` (1)
  //     and `#C9D1D9` (2) are `content-primary`; and the one `#080A0D` that was TEXT, on the
  //     amber Featured chip, is `content-inverse` rather than a surface token.
  //   * BRAND AND STATE — 50. `#00D4FF` (22) is the brand token. `#26A69A` (10) and
  //     `#EF5350` (9) split by the group `statusToken` would return for what each site
  //     marks — profit/loss for the signed return figures, live for the Subscribe action and
  //     the Subscribed button, connected for the success notice, error for the failure notice
  //     and the load-error banner. `#FFB74D` (9) has no token of its own: `tokens.css`
  //     retires it into `--color-status-warning`, which is where all nine go.
  //
  // The two `0 0 20px rgba(…)` glows on the Subscribe / Clone action are NOT in these
  // numbers and never were — `FUNCTIONAL_COLOUR`'s lookbehind rejects `rgba(` preceded by
  // the `_` of a Tailwind arbitrary value, so a `shadow-[…_rgba(38,166,154,0.3)]` measured as
  // zero literals. They are gone anyway, replaced by the raised elevation token:
  // Requirement 1.5 retires coloured glows and `tokens.css` declares no coloured shadow to
  // migrate them to. Worth recording because it is a gap in this guard's reach rather than a
  // gap in the page — a colour inside an arbitrary-value utility whose preceding character is
  // `_` is invisible here.
  //
  // THREE HUE DIVERGENCES PRESERVED AND REPORTED, NOT SILENTLY RESOLVED. Task 27.3 reads the
  // colour out of the token layer; it does not re-decide the design. So where the page's own
  // hue disagreed with what `design/semantic.js` would pick, the hue stayed and the
  // disagreement was written down: nine decorative uses of a state hue — the amber Featured
  // chip, the two Sharpe figures, the three star ratings, the Featured-section heading glyph
  // and the hero's Trending count, all in the warning amber, plus the hero's Featured count
  // in the profit green. None of the nine marks a state, and Requirement 1.5 spends colour on
  // state. (The hero's third count is brand cyan, which is not a state hue and so is not a
  // divergence; nor is the historical-results statement, which is a genuine caution.) Then the
  // per-environment chip, brand cyan for BACKTEST, PAPER and LIVE alike where `ENVIRONMENT`
  // gives three distinct treatments; and the signed figures' `value >= 0`, which renders a
  // flat 0.00% in profit green where `pnlToken` calls zero neutral. All three are in the
  // page's header. Resolving any of them means changing markup or a threshold, which is a
  // different task from reading the colour out of the token layer.
  //
  // The `0` entry stays, for the reason `pages/Dashboard.jsx: 0` gives: it records that the
  // page is clean and holds it there. An entry is deleted only when the file is.
  'pages/StrategyMarketplace.jsx': 0,
  // M9. 4 -> 0 at task 27.1, and the entry moves up here out of the deferred block below,
  // because the page is no longer deferred.
  //
  // All four were `RevenueTab`'s, and none of them was in the palette at all: `#4ade80`
  // twice on Total Earnings and "Net Revenue (90%)", `#60a5fa` on Monthly Recurring Revenue
  // and `#f87171` on Platform Fee Paid — a Tailwind green/blue/red trio, not this app's
  // `#26A69A`/`#EF5350`. Two now come from `design/tokens.js` (`status.profit.fg` on the
  // owner's share, `status.loss.fg` on the platform fee) and the other two are gone with the
  // figures they coloured: every field that tab read is absent from
  // `GET /api/library/creator/analytics`, so the four cards were rendering `|| 0` and the
  // blue "recurring revenue" and the green "net (90%)" were quantities the server never
  // reports. The tab reads the per-currency `earnings[]` ledger instead. There is no token
  // for the blue and none is invented: a figure with no state takes `content.primary`.
  //
  // Cleared with this page's 121 `C.` references in the same change, as §14.4 requires of a
  // page task. The `0` stays rather than being deleted, for the reason `pages/Strategies.jsx:
  // 0` gives.
  'pages/StrategyDetail.jsx': 0,

  // -- Pages retail-ui-simplification cleared, joining from the deferred block --
  //
  // These were `OUT_OF_SCOPE` entries because vyomquant-ui-redesign scheduled no task for
  // them — §14.4's "retokened by M1, layouts stay older". retail-ui-simplification
  // Requirement 4.5 schedules them, so each one moves up here as its page task takes it to
  // `0`, which is exactly the move task 27.1 made for `pages/StrategyDetail.jsx` above and
  // for the same reason: a page with a task is no longer a deferred page, and the out-of-
  // scope group is the one where a `1` would be legal. Moving up recruits
  // `holds no colour literal in any in-scope file`, which reads the MEASURED count and so
  // catches a literal that is really in the tree even if this number is left at `0`.
  //
  // 53 -> 0 at retail-ui-simplification task 7.2, in the same commit that deleted this
  // page's font-size entry (16 -> 0). Fourteen distinct values; eight were a second
  // spelling of a token that already existed (#080a0e, #0c1017, #1e293b, #334155, #f8fafc
  // and #e2e8f0, #94a3b8, #64748b, #00d4ff) and six needed a decision Requirement 5.3 puts
  // on `design/semantic.js` rather than on the nearest hue:
  //
  //   #10b981 / #059669  the switch's ON state, its border, the armed-guard glyph and the
  //                      dirty Save button -> `token.status.live.fg`, the group
  //                      `statusToken('active')` returns. `live`/`connected`/`profit` are
  //                      one green in `tokens.css`; `live` is the name that describes an
  //                      ARMED guard rather than a profitable position. Same retirement
  //                      `pages/StrategyMarketplace.jsx` recorded for the same hue.
  //   #374151 / #4b5563  the switch's OFF track and border -> `line.strong` / `line.default`.
  //                      An unarmed guard is an inactive control, not a warning state, and
  //                      must not borrow one.
  //   #ffffff            the switch knob -> `content.primary`. Pure white is not in the
  //                      palette.
  //   #ef4444 / #38bdf8  the toast's error and info arms — GONE rather than mapped, with the
  //   + 2 hand-mixed     two `rgba(0,0,0,…)` shadows: `ds/Alert` derives its hue from
  //     shadows          `severity` and `token.shadow.panel` is the declared elevation.
  //
  // NO HUE DIVERGENCE IS CARRIED FORWARD from this page, unlike Marketplace's three. Nothing
  // here paints a profit or a loss, so `pnlToken` is not reached at all; the only semantic
  // question was armed-versus-unarmed and it is answered above.
  'pages/RiskSettings.jsx': 0,
  // 12 -> 0 at retail-ui-simplification task 7.3, in the same commit that deleted this page's
  // font-size entry (12 -> 0) and took its inline `monospace` declarations from 10 to 2.
  //
  // EIGHT OF THE TWELVE WENT WITH TWO BLOCKS, NOT ONE AT A TIME. The error banner's
  // `rgba(239,68,68,0.1)` wash, `rgba(239,68,68,0.3)` border, `#fca5a5` text and `#ef4444`
  // glyph, and the success banner's `rgba(16,185,129,0.1)`, `rgba(16,185,129,0.3)`, `#6ee7b7`
  // and `#10b981`, all became `ds/Alert`, which derives hue, icon and live-region role from
  // `severity` and accepts no colour. None of those eight was in the palette at all — they are
  // Tailwind's red and emerald ramps rather than this app's `#EF5350` / `#26A69A` — so mapping
  // them one for one would have meant re-deciding two states' hues by hand inside a
  // typography commit. `ds/Alert` has no `success` severity and one was NOT added to a shared
  // primitive from a page commit: a completed verification reports at `info` with its sentence
  // unchanged.
  //
  // THE OTHER FOUR EACH NEEDED A DECISION:
  //
  //   rgba(0,212,255,0.08)  the header medallion's wash -> `token.brand.wash`, the declared
  //                         10% brand and the nearest thing that exists.
  //   rgba(0,212,255,0.2)   its ring -> `line.strong`. **There is no 20% brand border token**,
  //                         and this is the construct task 5.2 already decided on Marketplace's
  //                         featured card. Same resolution, so the two pages do not diverge.
  //   rgba(0,0,0,0.4)       the QR panel's shadow -> `token.shadow.raised`
  //                         (`0 4px 12px rgba(0,0,0,0.35)`), two units from a declared one.
  //   #ffffff               **FUNCTIONAL, and the one literal here that took thought.** A QR
  //                         reader needs a light quiet zone or it cannot find the finder
  //                         patterns, so this is not a surface that can take `surface-panel`.
  //                         It takes `content.primary` (#F0F2F5), the lightest value the
  //                         palette declares, which holds ~18:1 against the black modules
  //                         against the ~3:1 a camera needs. Pure white is not reintroduced.
  //
  // `token.status.profit.fg` on the copy button's confirmed state already read the token layer
  // and is untouched: a secret on the clipboard is a completed action, which is
  // `statusToken('ok')`'s group, so the name was right as well as the value.
  'pages/TwoFA.jsx': 0,
  // 3 -> 0 at retail-ui-simplification task 7.4, in the same commit that deleted this page's
  // font-size entry (7 -> 0).
  //
  // All three were the error banner's — `rgba(239,68,68,0.1)` wash, `rgba(239,68,68,0.3)`
  // border, `#fca5a5` text — and all three left WITH the banner, which is now `ds/Panel`'s
  // `error` state driven by `usePanelState`. None was in the palette: Tailwind's red ramp, not
  // this app's `#EF5350`, so a one-for-one mapping would have re-decided the error hue by hand
  // inside a typography commit.
  //
  // TWO OFF-PALETTE CONSTRUCTS THIS GUARD CANNOT SEE WENT WITH THEM, recorded because a scan
  // will not find them for the next reader and because both are worth recognising elsewhere:
  //
  //   `border: 1px solid ${token.line.default}15` on every table row — a token with two hex
  //   digits concatenated onto it, i.e. a hand-mixed 8% alpha wearing a token's name. There is
  //   no `#` in the source, so HEX_LITERAL never matched it. `ds/DataTable` owns row separation
  //   now. This is the same class of blind spot the Marketplace entry records for a colour
  //   inside an arbitrary-value utility.
  //
  //   `placeholder:text-slate-600` on the search input — Tailwind's own slate ramp, which is
  //   not this palette at all and which no budget here governs (O1 put the built-in classes out
  //   of the font-size ratchet's scope; the colour guard never saw this one because it carries
  //   no literal). It becomes `placeholder:text-content-muted`.
  'pages/SecurityLogs.jsx': 0,
  // 5 -> 0 at retail-ui-simplification task 7.6, in the same commit that deleted this page's
  // font-size entry (24 -> 0). The first-run surface, so this is the page where an off-palette
  // hue is seen first by a new retail account.
  //
  // Four of the five were two values spelled twice, and both are already declared:
  //
  //   `rgba(0,212,255,0.12)` on the CURRENT progress step's circle and
  //   `rgba(0,212,255,0.05)` on the RECOMMENDED plan card — two hand-mixed alphas of the brand
  //   hue at two strengths, where `--color-brand-wash` (`rgba(0,212,255,0.10)`) is the declared
  //   one. Both read `bg-brand-wash` now, so the page has one brand wash rather than two.
  //
  //   `#000` twice — the glyph on a completed step's brand-filled circle, and the RECOMMENDED
  //   ribbon's text on the same fill. Pure black is not in the palette; `content.inverse` is
  //   `#080A0E` and is what `components/ui/Button.jsx`'s own `text-[#080A0E]` was reaching for.
  //
  // The fifth needed `design/semantic.js` rather than a hue match, which is Requirement 5.3's
  // clause: `rgba(0,255,136,0.15)` bordered a security row whose setting was satisfied.
  // `#00FF88` is the retired green this palette replaced with `#26A69A`, and
  // `statusToken('active')` returns the `live` group — the name that describes a protection
  // that is ON, exactly as task 7.2 argued for an armed kill switch. It becomes
  // `border-status-live`. Nothing on this page paints a profit or a loss, so `pnlToken` is not
  // reached here at all.
  //
  // TWO OFF-PALETTE CONSTRUCTS NEITHER GUARD CAN SEE WENT WITH THEM: `borderRadius: 18` on the
  // card and `borderRadius: 20` on the ribbon, both off the declared radius scale entirely —
  // `--radius-xl` stops at 12. That is the same construct `pages/UpdatePasswordPage.jsx`
  // recorded at task 7.5, and the reason it is written down is that a scan will not find it.
  'pages/Wizard.jsx': 0,
  // 3 -> 0 at retail-ui-simplification task 7.7, in the same commit that deleted this page's
  // font-size entry (15 -> 0).
  //
  // Two of the three were one visual idea between them: `rgba(14,19,25,0.75)` on the card and
  // `0 20px 40px rgba(0,0,0,0.5)` under it — a translucent dark pane lifted off the page by a
  // shadow twice the depth of anything declared. `tokens.css` declares three elevations, no
  // translucent surface, and its own annotation for the set is "no coloured glows. Calm by
  // default", so both resolve to the declared pair: `bg-surface-raised` and `shadow-raised`.
  //
  // The third needed a decision rather than a lookup, and it is recorded because the VALUE
  // moves while the NAME is exact. `rgba(0,0,0,0.2)` washed the reading pane DARKER than the
  // card; `--color-surface-inset` (`#1A202C`) is marginally LIGHTER than
  // `--color-surface-raised` (`#151821`). The token layer's own comment names `inset` for
  // "inputs, wells", which is what the element is, so the declared name is followed rather
  // than the old value matched — Requirement 5.3's rule pointed at a surface instead of a hue.
  //
  // ONE HUE DELIBERATELY NOT RE-DECIDED: the `⚠️ High-Risk Investment Warning` heading read
  // `token.status.loss.fg` and now reads `text-status-loss` — the same `#EF5350`, through the
  // same token, as the utility class step 5 asks for. It is arguably a *warning* rather than a
  // *loss* and `--color-status-warning` is amber, so renaming it would change what a trader
  // sees on a risk disclosure. Requirement 5.3 says raise it rather than pick; a typography
  // commit is not where an existing risk hue gets swapped.
  //
  // FOUR OFF-PALETTE CONSTRUCTS NEITHER GUARD CAN SEE WENT WITH THEM, recorded because a scan
  // will not find them for the next reader:
  //
  //   `backdropFilter: 'blur(20px)'` on the card — a glassmorphic wash the token layer does
  //   not declare anywhere. It blurred the background the first literal supplied; with the
  //   surface opaque there is nothing left for it to do and it is not reproduced.
  //
  //   `${token.brand.base}15` on the active tab's fill and `${token.line.default}80` on the
  //   well's border — tokens with hex-alpha digits concatenated onto them, i.e. hand-mixed 8%
  //   and 50% alphas wearing a token's name, with no `#` anywhere so HEX_LITERAL never matched
  //   either. The first goes with the tab strip, whose selected state `ds/Tabs` owns (a 2px
  //   `--color-brand` underline plus the label in `--color-brand`, marked on `aria-selected`
  //   as well as in hue); the second becomes a plain `border-line-default`. Same blind spot
  //   `pages/SecurityLogs.jsx` recorded at 7.4 and `pages/UpdatePasswordPage.jsx` at 7.5.
  //
  //   `fontFamily: 'monospace'` twice — on the whole card and again on the tab buttons, so
  //   four legal documents rendered in a monospace stack. Requirement 3.1 reserves monospace
  //   for values whose alignment or literalness carries meaning; a refund policy is prose.
  'pages/LegalPage.jsx': 0,
  // 7 -> 0 at retail-ui-simplification task 7.8, in the same commit that deleted this page's
  // font-size entry (79 -> 0, the largest single-file count in the tree).
  //
  // FIVE OF THE SEVEN WERE ONE HUE, SPELLED FOUR WAYS ACROSS FOUR PILLS.
  // `rgba(38, 166, 154, 0.12)` and `rgba(38, 166, 154, 0.3)` on the *ACCOUNT ACTIVE* pill,
  // `rgba(38,166,154,0.1)` on *Verified*, `rgba(38,166,154,0.12)` on *PROTECTED* and
  // `rgba(38,166,154,0.1)` on *20% RECURRING* — all of them `#26A69A`, which is
  // `token.status.live.fg` / `.connected.fg` / `.profit.fg`, and whose declared 12% wash is
  // exactly the value being hand-mixed in two of the five.
  //
  // None of the five is mapped by hand, and that is Requirement 5.3 rather than convenience:
  //
  //   * The one pill that survives AS a pill — *Verified* — becomes `ds/StatusBadge`, which
  //     takes the server's state and resolves the hue through `statusToken`.
  //     `design/semantic.js` is the only module that knows which token means "confirmed", and
  //     the page now passes `'ok'` or `'pending'` rather than picking a green.
  //   * The other three pills DO NOT SURVIVE, because all three were fabrications. *ACCOUNT
  //     ACTIVE* and *PROTECTED* were static JSX — a green dot and a tick asserting an account
  //     standing and a security posture nothing reports — and *20% RECURRING* was a
  //     commission rate `ReferralStatsResponse` does not carry. Their literals go with the
  //     claim rather than being retokened into it, which is the only outcome in which
  //     clearing a colour entry does not also preserve the thing it was colouring.
  //
  // The remaining two are the notification switch's knob and its shadow: `#fff` ->
  // `token.content.primary` (pure white is not in the palette) and
  // `boxShadow: '0 1px 3px rgba(0,0,0,0.3)'` -> `token.shadow.panel`. Both are the same
  // substitution task 7.2 made on `pages/RiskSettings.jsx`'s identical switch knob one
  // commit into this group, which is the second time that construct has been retokened and
  // the reason `ds/` having no switch primitive is recorded as a Requirement 4.4 finding on
  // both pages rather than resolved on either.
  //
  // THREE OFF-PALETTE CONSTRUCTS THIS GUARD CANNOT SEE went with them, written down because a
  // scan will not find them for the next reader: `${token.brand.base}33`,
  // `${token.brand.base}15` and `${token.brand.base}40` — tokens with two hex digits
  // concatenated on, i.e. three different hand-mixed alphas wearing a token's name. None
  // carries a `#`, so `HEX_LITERAL` never matched any of them. `token.brand.wash` is the
  // declared wash and is what all three become. This is the fourth page in this spec to carry
  // that construct (7.4, 7.5, 7.7 and now 7.8), which makes it the most common thing the
  // colour ratchet is structurally blind to.
  'pages/Profile.jsx': 0,
  // 9 -> 0 at retail-ui-simplification task 7.9, in the same commit that deleted this page's
  // font-size entry (39 -> 0). A MONEY SURFACE, and the only entry in this group where two of
  // the literals belonged to another company.
  //
  //   #1a1f71 / #003087  **Visa's brand blues, on every card brand.** A two-stop gradient
  //                      painted behind a generic `CreditCard` glyph whatever the stored
  //                      method actually was, so a Mastercard or an Amex rendered in Visa's
  //                      colours. REMOVED rather than retokened: there is no token for another
  //                      company's identity and there should not be one. The tile is
  //                      `surface-inset` with the glyph in `content-secondary`, and the brand
  //                      is stated in WORDS beside it — the one channel that cannot be wrong
  //                      about which network the card is on.
  //   #f59e0b            the 70%-of-quota usage bar. **It is `--color-status-warning` exactly**
  //                      — the same six digits the token layer declares — so this is a token
  //                      spelled as a number, and `bg-status-warning` is the value it already
  //                      was. `getUsageColor`'s whole three-branch body goes with it: the three
  //                      hues are `statusToken`'s `loss` / `warning` / `profit` groups now.
  //   #000 ×3            the *RECOMMENDED* ribbon, the *CURRENT* ribbon and the *Subscribe*
  //                      button, all three text-on-brand -> `content.inverse` (#080A0E). Pure
  //                      black is not in the palette; this is the third page in this group to
  //                      carry that exact substitution (7.6, 7.7 and now 7.9), each time as
  //                      text sitting on a brand-filled surface.
  //   #fff ×2            the payment-failure button's label and the card glyph ->
  //                      `content.primary` (#F0F2F5), the same substitution task 7.3 made on
  //                      `TwoFA.jsx`'s switch knob.
  //   #00000088          the currency dropdown's shadow -> `token.shadow.overlay`. A 53% black
  //                      at a depth the token layer does not declare at all; `shadow.overlay`
  //                      is the declared elevation for a floating layer, and this is the first
  //                      page in the group to need that one rather than `shadow.panel` or
  //                      `shadow.raised`.
  //
  // BOTH OF THIS PAGE'S TWO GRADIENTS GO, which is the pair requirements §1.10 counts as
  // "Billing 2". The second was `linear-gradient(135deg, surface.raised, surface.panel)` on the
  // current-plan card — a hand-built elevation between two declared surfaces, which is what
  // `ds/Panel` is. Requirement 10.1's rule is that a gradient on a trading surface needs a
  // declared token or it does not belong, and neither of these had one.
  //
  // FOURTEEN OFF-PALETTE CONSTRUCTS THIS GUARD CANNOT SEE went with them — the largest such
  // cohort in this spec so far, and all one construct: `${token.status.loss.fg}12`, `…44`,
  // `…10`, `…55`, `…35`, `${token.status.profit.fg}12`, `…44`, `…20`, `…50`,
  // `${token.brand.base}15`, `…40`, `…20`, `…50` and `${token.line.default}20`. Each is a token
  // with two hex digits concatenated on, i.e. fourteen hand-mixed alphas across three hues
  // wearing a token's name; none carries a `#`, so `HEX_LITERAL` never matched one of them.
  // `token.brand.wash` and the declared `status.*.wash` values are what they become, or they
  // leave with the element that was painting them. This is the fifth page in this spec to carry
  // the construct (7.4, 7.5, 7.7, 7.8 and now 7.9) and by far the heaviest, which is the
  // strongest evidence yet that the ratchet's blindness to it is worth a guard of its own.
  'pages/Billing.jsx': 0,
  // 128 -> 0 at retail-ui-simplification task 7.10, in the same commit that deleted this
  // page's font-size entry (52 -> 0). **THE LARGEST SINGLE ENTRY THIS BUDGET EVER HELD** — 48%
  // of its remaining 268, and the one this file's header named as the case for a ratchet
  // having a resting position, which it stopped being the moment Requirement 4.5 scheduled the
  // page. Twenty-four distinct values over 905 lines, one every seven, and the whole visual
  // layer was hand-built: no `token` import, no utility class and no `ds/` primitive in the
  // file. Mapped by the group the colour MARKS rather than by nearest hue (Requirement 5.3):
  //
  //   #64748b ×19  labels and secondary text -> `content-secondary` (#8B95A5, 6.2:1 on panel).
  //                NOT `content-muted`: `tokens.css:30` marks that one NON-TEXT ONLY at 3.2:1
  //                and every one of these 19 was body or label text. The largest single-value
  //                cohort on any page in this spec.
  //   #334155 ×17  card, input and divider borders -> `line-default`
  //   #f1f5f9 ×15  primary text -> `content-primary`
  //   #1e293b ×13  card and input surfaces -> `surface-panel` / `surface-inset`, mostly
  //                through `ds/Panel` and `ds/Field`, which own both
  //   #94a3b8 ×12  secondary text -> `content-secondary`
  //   #3b82f6 ×9   the selection and link hue -> `brand` (#00D4FF). Fourteen of the sixteen
  //   #3b82f620 ×5 leave with the elements they painted; the two that stay are the selected
  //   #3b82f650 ×1 venue row's wash and text, which are `brand-wash` and `text-brand`.
  //   #3b82f610 ×1
  //   #10b981 ×9   **THE FABRICATED GREEN, and the reason this entry is not a retoken.** Seven
  //   #10b98120 ×3 of the twelve painted a *Connected* dot, a *Connected* word, a *Healthy*
  //                grade or a per-card *healthy* value — NONE of which was a measurement, all
  //                four being constants in `routers/exchange.py` — so they are REMOVED WITH
  //                THE CLAIM rather than pointed at `--color-status-connected`. Retokening
  //                them would have kept the false assertion and changed its shade, which is
  //                the one outcome a colour pass on a venue page must not produce. The rest
  //                are the *Save keys* button and the success toast, now
  //                `ds/CommandButton`'s primary intent and `ds/Alert`'s `info` severity.
  //   #0f172a ×7   the page canvas and the inset wells -> the page background declaration is
  //                REMOVED (the shell owns the canvas, which is why no migrated page sets one)
  //                and the wells are `surface-inset`
  //   #ef4444 ×4   the destructive control, the required-field asterisk and the error toast ->
  //   #ef444450 ×1 `ds/CommandButton`'s `destructive` intent, `ds/Field`'s `required` (which
  //   #ef444410 ×1 renders the WORD "Required" instead of an asterisk, so the hue has nothing
  //   #ef444420 ×1 left to carry) and `ds/Alert`'s `error` severity. The token is #EF5350.
  //   #475569 ×3   secondary control borders -> `line-strong`
  //   #8b5cf6 ×1   **violet, and there is no violet in the palette.** It tinted the *Health*
  //   #8b5cf620 ×1 tile's shield glyph — the tile that was the fabrication — so both leave
  //                with it and NOTHING is substituted: `design/semantic.js` has no group that
  //                means "health", which is the Requirement 5.3 question, and the honest
  //                answer on this page is that health is not reported at all.
  //   #f59e0b ×1   the *Supported* tile's glyph. **It is `--color-status-warning` exactly** —
  //   #f59e0b20 ×1 the same six digits the token layer declares, the same coincidence
  //                `pages/Billing.jsx`'s quota bar carried — but a count of AVAILABLE VENUES
  //                is not a warning, so it is NOT retokened to the group whose value it
  //                happens to share. The glyph is `content-secondary`, which is what a
  //                decorative icon beside a labelled figure is (Requirement 1.5).
  //   #374151 ×1   the disabled *Save keys* background -> `ds/CommandButton`'s disabled
  //                treatment, which also renders the `disabledReason` the control never had
  //   #ffffff ×1   the *Save keys* label -> `content-primary` (#F0F2F5). Pure white is not in
  //                the palette; the fourth page in this group to carry that substitution
  //                (7.3, 7.6/7.7, 7.9 and now 7.10).
  //   rgba() ×1    the toast's `0 10px 40px rgba(0,0,0,0.4)` -> `ds/Alert`, which carries no
  //                shadow at all: it is an inline strip rather than a floating layer, so the
  //                declared elevation it needs is none.
  //
  // UNUSUALLY, ALL FIFTEEN HAND-MIXED ALPHAS WERE COUNTED. The eight-digit form this page uses
  // (`#3b82f620`) is a hex literal and `HEX_LITERAL` matches it, unlike the
  // `${token.brand.base}20` form `pages/Billing.jsx` carried fourteen of, which has no `#` and
  // was invisible to this guard. Five pages in this spec have now hidden alphas behind a token
  // name; this is the first where the ratchet could see them, and the difference is that the
  // page predates the token layer rather than misusing it.
  //
  // THIRTY-NINE OFF-SCALE CONSTRUCTS THIS GUARD CANNOT SEE went with them, and the shape is new
  // for this spec: twenty-eight of them are RADII SPELLED AS NUMBERS — `borderRadius: 8` ×18 is
  // `--radius-lg`, `borderRadius: 12` ×9 is `--radius-xl`, `borderRadius: 6` is `--radius-md` —
  // with one genuinely off the declared scale (`borderRadius: 10`), plus
  // `transition: "all 0.2s"` ×5 against `--transition-base`'s `180ms cubic-bezier(…)`, one
  // `backdropFilter: "blur(10px)"` the token layer does not declare at all, `opacity: 0.3` ×2
  // dimming a glyph to a contrast the palette has no name for, and `letterSpacing: 1` ×2. Every
  // one leaves with its element; the radii onto `ds/Panel`, `ds/Field` and `ds/CommandButton`,
  // which own them.
  'pages/ExchangeManager.jsx': 0,
  // 30 -> 0 at retail-ui-simplification task 7.11, in the same commit that deleted this
  // page's font-size entry (29 -> 0). **THE LAST PAGE IN REQUIREMENT 4.5's GROUP**, and the
  // one where the literals were least about trading and most about a screen built before the
  // token layer existed: a gradient tile, a background glow, a hand-mixed card shadow, two
  // RETIRED palettes' worth of banner colour and another company's logo. Mapped by the group
  // the colour MARKS rather than by nearest hue (Requirement 5.3):
  //
  //   #EF5350 ×4  **the palette's own loss red, spelled as a number.** `status.error.fg` is
  //               that exact value. Two were the strength verdict's *Weak* colour and two
  //               were the form's inline messages; the messages are `text-status-error` and
  //               the verdict is `ds/StatusBadge` now.
  //   #F59E0B ×1  the *Fair* verdict. **It is `--color-status-warning` exactly** — the third
  //               page in this spec to spell that token as a number (7.9's quota bar, 7.10's
  //               venue count) — and here the GROUP is right as well as the value, which the
  //               other two were not: a password the form will take but would rather not is
  //               what `warning` means.
  //   #10B981 ×6  **NOT in the palette.** Tailwind's emerald, where this app's green is
  //               #26A69A. Five painted the four met-requirement ticks and *Passwords match*,
  //               one painted *Strong*; all six come from `statusToken('ok')` now, so three
  //               states' hues are chosen by `design/semantic.js` and not by this page.
  //   rgba() ×2   the background glow's two radial washes — see the gradients below. One is a
  //               violet and **there is no violet in the palette**, which is the Requirement
  //               5.3 question answered the way task 7.10 answered it: nothing is
  //               substituted, because nothing declared means it.
  //   rgba() ×1   `0 40px 80px rgba(0,0,0,0.7)` under the card -> `token.shadow.overlay`
  //               (`0 16px 48px rgba(0,0,0,0.60)`), the largest declared elevation. A
  //               hand-mixed shadow half again as deep as any the token layer declares.
  //   #00d4ff     the header medallion's gradient. #00d4ff IS `token.brand.base`; #0055ff is
  //   #0055ff     a blue the palette does not have. Both leave with the gradient.
  //   rgba() ×1   `0 0 20px rgba(0,212,255,0.25)`, the medallion's glow. REMOVED and NOTHING
  //               replaces it: `tokens.css`'s elevation block says *no coloured glows. Calm
  //               by default (Req 1.5)* and its alias block records `--animate-pulse-glow`
  //               as "not carried forward" for the same reason.
  //   #000 ×2     the medallion glyph, on that gradient. Pure black is not in the palette;
  //               the glyph is `brand.base` on `brand.wash` now, which is task 7.3's
  //               resolution for the same medallion on the two-factor screen. Fifth page in
  //               this group to substitute a pure black or white (7.3, 7.6/7.7, 7.9, 7.10).
  //   rgba() ×1   `rgba(255,255,255,0.05)` behind the Google command ->
  //               `ds/CommandButton`'s `secondary` intent, which owns its own surface.
  //   #4285F4     **Google's four brand colours, in an inline logo mark.** The second entry
  //   #34A853     in this group whose literals belonged to another company, and task 7.9
  //   #FBBC05     settled it: *there is no token for another company's identity and there
  //   #EA4335     should not be one.* They leave with a DUPLICATED command (see below) and
  //               the provider is stated in WORDS on the survivor, which is the one channel
  //               that cannot be wrong about which provider it is.
  //   #FF3D00     the failure band's text, wash and border. **The RETIRED pre-M1
  //   rgba() ×2   orange-red**, not the current #EF5350.
  //   #00C853     the confirmation band's text, wash and border. **The RETIRED pre-M1
  //   rgba() ×2   green.** Both bands are `ds/Alert` now, which derives its hue, icon and
  //               live-region role from `severity` and takes no colour at all — so six of
  //               the thirty were two states' hues that would have had to be re-decided by
  //               hand if they had been migrated one for one.
  //
  // ALL THREE OF THIS PAGE'S GRADIENTS GO, which is requirements §1.10's "AuthPage 3", and
  // per design.md D5 each removal names its replacement. The two radial washes were halves of
  // ONE contentless `position: absolute; inset: 0` element with `pointerEvents: "none"`;
  // nothing replaces them, and what is underneath is `surface.canvas`, the flat page the shell
  // already declares. The `linear-gradient(135deg, …)` on the header medallion becomes
  // `brand.wash` flat with `line.strong` where a semi-transparent brand ring used to be — the
  // same resolution task 5.2 gave Marketplace's featured card and task 7.3 gave this
  // medallion's twin, so three surfaces resolve one construct one way.
  //
  // ONE CONTROL COUNT CHANGED, AND IT IS A DEDUPLICATION. The page rendered the Google command
  // TWICE — once above the credential fields, once below the submit — same handler, same
  // label, two treatments and two dividers. They are one control now, keeping the label, the
  // `data-testid` two tests bind to, the in-flight text and the disabled-while-busy behaviour.
  // Requirement 16 forbids removing a control; this removes a DUPLICATE of one, which is the
  // move task 5.2 made when two card renderers carrying the same mistake became one.
  'pages/AuthPage.jsx': 0,

  // -- Shared surfaces an in-scope task cleared or created ------------------
  // These are not pages and so are not on design.md's M6-M9 page list, but each one is
  // here because an IN-SCOPE task is what put it at `0`, and each is rendered by an
  // in-scope page. A literal creeping back into one of them is a literal on the pages
  // above, which is the thing the emptiness assertion exists to prevent — so they are
  // governed by it rather than parked in the out-of-scope group where a `1` would be legal.
  // Their individual notes are with them.
  //
  //   components/Sidebar.jsx, components/TopBar.jsx  — cleared by tasks 8.6 / 8.7 (M4 shell)
  //   components/ErrorBoundary.jsx                   — rewritten by task 6.25 (M3)
  //   components/SignalTraceVisualization.jsx        — cleared by task 21.6 (M8)
  //   components/trading/*                           — created by task 20.2 (M8)
  //   components/builder/validationSurfaces.jsx      — created by task 24.4b (M9)
  //   lib/drawdownSeries.js                          — created by task 23.3 (M9)
  //
  // The first four of those sit further up this object, beside the page whose task cleared
  // them. The three below were in the out-of-scope blocks before task 27.3 and moved here.
  //
  // Cleared by task 8.6, which rebuilt it against `shell/navigation.js`. The five were
  // the brand tile's `linear-gradient(135deg,#00d4ff,#0055ff)` and its `#000` glyph, the
  // notification badge's `#000`, and the active row's `rgba(0,212,255,0.07)`. The
  // gradient went for a second reason: Requirement 1.5 retires gradient washes.
  'components/Sidebar.jsx': 0,
  // Cleared by task 8.7. The one literal was the notification badge's `#000` text on a
  // `C.cyan` circle; the badge now takes `text-brand` on `bg-surface-raised`.
  'components/TopBar.jsx': 0,
  // New at task 23.3, along with the `lib` scan root that makes the entry real — see the
  // SCOPE note above. The drawdown curve's derivation from the real equity curve
  // (design.md §7.4, Requirement 6.3) holds no colour: it answers with numbers and lets
  // `ds/Chart` decide what they look like. Entered at `0` and held there, for the reason
  // `components/Sidebar.jsx: 0` keeps its entry — a file with no entry is a file whose
  // cleanliness nothing is holding.
  'lib/drawdownSeries.js': 0,
});

/**
 * Everything else. Budgeted so it cannot grow; no promise in this spec that it shrinks,
 * and no emptiness assertion over it. An entry here may legitimately be non-zero forever.
 *
 * Task 27.3 named this group from the redesign spec's own scope: `Profile`, `Landing`,
 * `Billing`, `AuthPage`, `Wizard`, `TwoFA`, `SecurityLogs`, `RiskSettings`,
 * `ExchangeManager`, `LegalPage`, `SupportCenter`, `NotificationCenter`, `CopilotChat`,
 * `FirstTradeWizard`, `DeployPreflightPanel`, `landing/*`, `ui/*` and `ui-legacy/*`.
 *
 * `common/*` joined that list at task 27.2's final stage A, and it is the same call
 * `ui-legacy/*` got: the module is the shim's eleven surviving components rehomed, so its
 * literals are the ones `ui-legacy/primitives.jsx` was already budgeted for. The set they
 * belong to did not change when their file did.
 */
export const OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET = Object.freeze({
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
  // `components/ui-legacy/primitives.jsx: 18` stood here until task 27.2's FINAL STAGE B
  // deleted the shim. Removed rather than lowered to `0`, per this header and per the
  // precedent `DashboardUpgrades.jsx` and `DesktopOnlyOverlay.jsx` set below: a `0` records
  // that a live file is clean and holds it there, but a deleted file has no source to measure
  // and `names only files that still exist` fails on an entry pointing at nothing.
  //
  // The 18 did not disappear, they MOVED. Stage A had already copied the shim's eleven
  // surviving components into `components/common/primitives.jsx` with their bodies intact, so
  // for the length of one commit the same 18 literals sat in both files and this budget
  // carried both entries. Stage B deleted the original; the entry below is where those 18
  // live now. Nothing was retokened in either stage, and the total this guard reads is
  // unchanged by the deletion — it is 18 lower in a file that is gone and 18 higher in the
  // file that replaced it, which nets to the same number it read before stage A.
  //
  // New at 18 at task 27.2's FINAL STAGE A — the shim's eleven surviving components, rehomed
  // off `ui-legacy` and reading `token.*` directly. Every one of the 18 came across verbatim
  // with the component that owns it:
  //
  //   17 in `Tag2`'s nine-tone map — the `profit`/`green` and `loss`/`red` tones' border and
  //      hover washes (`rgba(0,200,83,…)`, the retired #00C853 green; `rgba(255,61,0,…)`,
  //      #FF3D00) and the `warning`, `purple` and `gold` tones' full background/border/hover
  //      triples (#FFAB00, #7C4DFF, #FFD600). All five hues are off-palette and none has a
  //      token: `statusToken('warning').wash` is a 12% #F59E0B, not a 10% #FFAB00, so
  //      substituting would visibly change the chip.
  //    1 in `Toast`'s `boxShadow: "0 4px 12px rgba(0,0,0,0.4)"`, which is a hand-tuned
  //      elevation rather than `token.shadow.raised`'s `0 4px 12px rgba(0, 0, 0, 0.35)`.
  //
  // They are NOT retokened here, and that is deliberate: stage A is a rehome. Changing five
  // chip hues and a shadow alpha is a colour decision affecting `Tag2`'s seven consuming
  // pages, and it would land in the same diff as fifteen moved import paths, where nobody
  // could review either. `Tag2`'s successor is `ds/StatusBadge`, which derives its hue from a
  // state word and accepts no colour prop — adopting it is what actually clears these 17, per
  // call site, and the module's docblock records that.
  //
  // OUT of scope, exactly as `ui-legacy/*` is and for the same reason: this guard does not
  // assert the file reads zero. It is a ratchet at 18 — the 18 cannot grow, and they come
  // down when the `ds/` adoption happens rather than on a promise made here.
  'components/common/primitives.jsx': 18,

  // -- Shell and cross-cutting components -----------------------------------
  'components/DeployPreflightPanel.jsx': 30,
  // `components/DashboardUpgrades.jsx: 6` stood here until task 19.4 deleted the file.
  // Removed rather than lowered to `0`, per this header: a `0` records that a live file is
  // clean and holds it there, but a deleted file has no source to measure and
  // `names only files that still exist` fails on an entry pointing at nothing.
  //
  // `components/DesktopOnlyOverlay.jsx: 1` stood here until task 27.3 deleted the file, and
  // went the same way for the same reason. The one literal was the scrim's
  // `rgba(8, 10, 14, 0.85)` — a hand-mixed 85% `C.bg0` with the comment `// C.bg0 with
  // opacity` beside it, because the shim exported no scrim. `shell/ResponsiveGate` (task
  // 8.4) superseded the component: it renders a gate screen on token utilities instead of
  // blurring the live app behind 8px, so there is no scrim to mix. Nothing imported the
  // overlay after task 8.5 rewired `App.jsx`.
  //
  // `components/Sidebar.jsx: 0` and `components/TopBar.jsx: 0` stood here too, and moved
  // into the in-scope group at task 27.3 rather than being deleted — see the note on that
  // sub-block. They are live files at `0`, which is the case a `0` entry exists for.
  //
  // `components/CopilotChat.jsx: 3` stood here until `production-launch-hardening` task 9.3
  // deleted the file, and went the way `DashboardUpgrades.jsx` and `DesktopOnlyOverlay.jsx`
  // went for the same reason: removed rather than lowered to `0`, because a deleted file has
  // no source to measure and `names only files that still exist` fails on an entry pointing at
  // nothing. The three literals were the drawer's own hand-mixed shadows and the `#000`
  // foreground on the brand-coloured action button — none of them retokened, all of them gone
  // with the component. It was deleted as the only consumer of `contexts/CopilotContext.jsx`,
  // which that task removed for holding two addresses no router serves; it was mounted
  // nowhere and documented itself as dormant and unmounted.
  'components/FirstTradeWizard.jsx': 3,

  // -- Deferred pages: retokened by M1, layouts stay older (§14.4) ----------
  // `pages/ExchangeManager.jsx: 128` stood here — the largest single entry in this budget,
  // 48% of its remaining 268, and the one this file's own header named as the case for a
  // ratchet having a resting position ("can sit at 128 forever without failing anything,
  // which is correct, because no task in this spec is scheduled to lower it"). That stopped
  // being true when Requirement 4.5 gave the page a task: retail-ui-simplification task 7.10
  // took it to 0 and moved the entry into the in-scope block above, the way task 7.2 moved
  // `pages/RiskSettings.jsx` and task 27.1 moved `pages/StrategyDetail.jsx`. The full hue map
  // is on the entry.
  // `pages/RiskSettings.jsx: 53` stood here until retail-ui-simplification task 7.2 took it
  // to 0 and moved the entry into the in-scope block above, the way task 27.1 moved
  // `pages/StrategyDetail.jsx`. It is not a deferred page any more: Requirement 4.5 gave it
  // a task, and the task landed. The full hue map is on the entry.
  // `pages/AuthPage.jsx: 30` stood here until retail-ui-simplification task 7.11 took it to 0
  // and moved the entry into the in-scope block above — the LAST page in Requirement 4.5's
  // group, so this is the last entry that block gains from it. Four of the thirty were another
  // company's brand colours in an inline logo mark and six were two RETIRED palettes' banner
  // hues; all three of the page's gradients went with them. The full map is on the entry.
  // `pages/TwoFA.jsx: 12` stood here until retail-ui-simplification task 7.3 took it to 0 and
  // moved the entry into the in-scope block above. The full map is on the entry; eight of the
  // twelve left with the two banners that became `ds/Alert`.
  // `pages/Billing.jsx: 9` stood here until retail-ui-simplification task 7.9 took it to 0 and
  // moved the entry into the in-scope block above. Two of the nine were another company's
  // brand colours, painted behind every stored card whatever the card actually was; the full
  // map is on the entry.
  // `pages/Profile.jsx: 7` stood here until retail-ui-simplification task 7.8 took it to 0 and
  // moved the entry into the in-scope block above. Five of the seven were one green, spelled
  // four ways across four pills, and three of those four pills were fabrications that went
  // with their literals rather than being retokened; the full note is on the entry.
  // `pages/Wizard.jsx: 5` stood here until retail-ui-simplification task 7.6 took it to 0 and
  // moved the entry into the in-scope block above. The full map is on the entry; four of the
  // five were the brand wash and pure black, each spelled twice.
  // `pages/StrategyDetail.jsx: 4` stood here until task 27.1 took it to 0 and moved the
  // entry into the in-scope block above. It is not a deferred page any more.
  // `pages/LegalPage.jsx: 3` stood here until retail-ui-simplification task 7.7 took it to 0
  // and moved the entry into the in-scope block above. All three were one visual idea — a
  // translucent pane lifted off the page by a shadow twice the depth the token layer
  // declares; the full note is on the entry.
  // `pages/SecurityLogs.jsx: 3` stood here until retail-ui-simplification task 7.4 took it to 0
  // and moved the entry into the in-scope block above. All three were the error banner's and
  // left with it; the full note is on the entry.
  'components/SupportCenter.jsx': 37,
  'components/NotificationCenter.jsx': 1,

  // -- Landing page: explicitly out of scope for v1 -------------------------
  // index.css's own header calls the landing `@utility` compositions out of
  // scope; these components are the same surface. Budgeted so they cannot
  // grow, with no task scheduled to lower them inside this spec.
  //
  // `pages/Landing.jsx` was here at 18. **Task 9.2 (commit 23) DELETED the file**, so the
  // entry went with it rather than being lowered — it is not a page that cleared, it is a
  // page that stopped existing. The file was routed nowhere (`App.jsx:44` lazy-imports
  // `components/landing/LandingPage` and `:590` routes that at `/`), so counting 18
  // literals against it spent this ratchet on nothing (Requirement 14.2). `names only
  // files that still exist` is the assertion that failed while the file was gone and this
  // line was not. Everything below is the LIVE landing surface and keeps its entry.
  'components/landing/Hero.jsx': 3,
  'components/landing/ScreenshotsSection.jsx': 3,
  // Retained deliberately, unlike the seven unreferenced landing components deleted
  // alongside this entry's removal: this file is `no-placeholders.test.js`'s non-vacuity
  // fixture. See the header on the file itself.
  'components/landing/ScreenshotComingSoon.jsx': 2,
});

/**
 * The whole budget, and what every assertion in `no-colour-literals.test.js` reads. The
 * ratchet is unchanged by task 27.3's split: each file still has exactly one recorded
 * number and still fails in both directions against it. The two groups above add one thing
 * on top — that the in-scope half of this map is all zeroes.
 *
 * Spread rather than hand-maintained, so the flat map cannot fall out of step with the
 * groups. The test asserts the two groups are disjoint, which is what makes the spread
 * lossless: an entry declared in both would silently take the out-of-scope number here.
 */
export const COLOUR_LITERAL_BUDGET = Object.freeze({
  ...IN_SCOPE_COLOUR_LITERAL_BUDGET,
  ...OUT_OF_SCOPE_COLOUR_LITERAL_BUDGET,
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
