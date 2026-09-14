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
 * An entry reaching `0` is expected and stays (task 3.5 lowers Dashboard.jsx to
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
  // with no token source at all. Task 3.5 takes this to 0.
  'pages/Dashboard.jsx': 315,
  // Material Design palette (#2196F3 #00BCD4 #FFAB00 #9C27B0 #FF5722 #00C853
  // #607D8B) plus GitHub greys. Rewritten by task 20.x.
  'components/SignalTraceVisualization.jsx': 138,
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
  'pages/SignalTrace.jsx': 20,
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
  'components/DashboardUpgrades.jsx': 6,
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
