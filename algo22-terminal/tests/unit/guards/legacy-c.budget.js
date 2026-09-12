/**
 * The `legacy-c` decreasing budget — vyomquant-ui-redesign task 1.12.
 * Requirements 1.1, 1.3. design.md §3.4, §14.4, §15.1.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * A checked-in count, per file, of how many `C.` references — member accesses
 * on the `ui-legacy/primitives.jsx` compatibility shim — that file still
 * contains. `legacy-c-budget.test.js` measures the real counts and asserts each
 * file matches its entry **exactly**:
 *
 *   * over budget  -> a new `C.` reference was added. Use a token class instead.
 *   * under budget -> references were removed but this file was not updated.
 *                     Lower the number here in the same commit.
 *
 * ---------------------------------------------------------------------------
 * THIS FILE IS EXPECTED TO SHRINK, AND LOWERING IT IS PART OF THE WORK
 * ---------------------------------------------------------------------------
 * Every page-migration task below task 1.12 lowers the entries for the files it
 * touches. That is not clean-up to be done later; it is part of the task, in the
 * same commit, and the guard fails until it is done. The failure message states
 * the new number, so there is nothing to work out.
 *
 * The budget reaches zero at task 27.2, which deletes the shim. At that point
 * `legacy-c-budget.test.js` and this file are deleted along with it — the guard
 * has no reason to outlive the thing it measures. Until then, an entry reaching
 * `0` is legal and stays: it records that a file is clean and must remain clean.
 *
 * Raising a number, or adding an entry, means new `C.` usage entered the tree.
 * That reverses §14.4's monotonic-progress rule and needs a reason in the PR.
 * Delete an entry only when the file itself is deleted.
 *
 * ---------------------------------------------------------------------------
 * SEEDED FROM THE TREE ON THE DAY TASK 1.12 LANDED
 * ---------------------------------------------------------------------------
 * 32 files, 2050 references. Measured, not estimated. The six page counts task
 * 1.12 quotes (PaperTrading 191, StrategyBuilder 148, SignalTrace 92,
 * Backtester 66, Portfolio 5, Strategies 1) and the 178 inside the shim itself
 * all reproduce exactly under the test's `countLegacyC`, which is how that
 * counting method was validated — see the test file's METHOD note.
 *
 * Scope is all of `src/**` (not just the in-scope pages), restricted to
 * JavaScript — `.js`, `.jsx`, `.ts`, `.tsx` — because `C.` is a JavaScript
 * member expression. `styles/tokens.css` mentions `C.bg0`, `C.t1` and friends
 * eleven times in explanatory comments; those are documentation, not references,
 * and are excluded by both the file-type restriction and the comment strip.
 *
 * ---------------------------------------------------------------------------
 * RE-MEASURED AFTER M2 (tasks 3.1, 3.2, 3.3)
 * ---------------------------------------------------------------------------
 * Those three tasks edited 14 files. Nine carry an entry below — `App.jsx`,
 * `Sidebar.jsx`, `TopBar.jsx`, `Strategies.jsx`, `StrategyDetail.jsx`,
 * `Portfolio.jsx`, `StrategyBuilder.jsx`, `builder/NodeTrace.jsx`,
 * `builder/NodePreview.jsx` — and the other five (`Dashboard.jsx`,
 * `ui/Card.jsx`, `builder/AssetSelector.jsx`, `builder/ParameterForm.jsx`,
 * `builder/TimeframeSelector.jsx`) read the shim not at all. Every edit swapped a
 * dead `className` for a live one or moved a font declaration; none added or
 * removed a `C.` reference. All 32 numbers below re-measure exactly, so none of
 * them moved. Recorded because "still passing" and "re-checked against the
 * current tree" are different claims.
 */

/** Paths are relative to `src/`, forward-slashed, matching the spec's notation. */
export const LEGACY_C_BUDGET = Object.freeze({
  // -- The shim itself ------------------------------------------------------
  // Reaches 0 only by being deleted (task 27.2), which also deletes the guard.
  // 178 -> 176 at task 10.9: `LoadingProvider`'s full-screen blocking overlay is
  // deleted (design.md §5.1, Requirement 14.2), and it carried exactly two shim
  // references — the scrim's `background: `${C.bg0}80`` and the message row's
  // `color: C.t2`. The provider, its context API and `anyLoading` all stay, so
  // nothing else in the file moved.
  'components/ui-legacy/primitives.jsx': 176,

  // -- In-scope pages, the six task 1.12 names ------------------------------
  // M9 — the two largest and most behaviourally sensitive files, migrated last.
  'pages/PaperTrading.jsx': 191,
  'pages/StrategyBuilder.jsx': 148,
  // M8
  'pages/SignalTrace.jsx': 92,
  'pages/Backtester.jsx': 66,
  // M7. The 5 here are one line: `const COLORS = [C.orange, C.purple, C.cyan,
  // C.gold, C.t3]` — which is also the `no-local-tokens` violation task 1.11
  // catches. Both guards clear on the same edit.
  'pages/Portfolio.jsx': 5,
  'pages/Strategies.jsx': 1,

  // -- Files beyond task 1.12's list ---------------------------------------
  // Task 1.12 seeds from the in-scope pages. These carry 1470 further
  // references between them. They are deferred pages, the landing page and the
  // shell, all retokened by M1 but not restructured (§14.4). They are budgeted
  // rather than ignored because the shim cannot be deleted at task 27.2 while
  // any of them still reads from it.
  'components/SupportCenter.jsx': 160,
  'pages/Profile.jsx': 160,
  'components/DashboardUpgrades.jsx': 135,
  'pages/Landing.jsx': 122,
  'pages/StrategyDetail.jsx': 122,
  'pages/Billing.jsx': 111,
  'components/NotificationCenter.jsx': 77,
  'components/ResearchConsole.jsx': 60,
  'pages/AuthPage.jsx': 57,
  'pages/Wizard.jsx': 56,
  'components/DeploymentConsole.jsx': 38,
  'pages/LegalPage.jsx': 36,
  'components/CopilotChat.jsx': 31,
  'pages/TwoFA.jsx': 30,
  // Re-pointed onto the token type scale by task 3.1.
  'components/builder/NodeTrace.jsx': 27,
  'components/builder/NodePreview.jsx': 24,
  'components/FirstTradeWizard.jsx': 23,
  // Cleared by task 8.6: rebuilt against `shell/navigation.js`, with colour taken from
  // `cssVar()` and token utility classes instead of the shim.
  'components/Sidebar.jsx': 0,
  'pages/SecurityLogs.jsx': 21,
  // The `AppShell` wrapper and the auth gates live here (task 3.3 touches it).
  // 14 -> 13 at task 8.5: the shell's `return` became a CSS grid on
  // `bg-surface-canvas`, which retired the wrapper's `background: C.bg0`. The
  // remaining 13 are all in `UpdatePasswordPage` (7) and `AdminGuard` (6) — both
  // outside task 8.5's scope, which is why `C` is still imported here.
  // Task 8.5's second half (the two route `Suspense` fallbacks, the narrowed
  // `'navigate'` bridge, the deleted `TENANT_ID`) moved all 13 down by 109 lines
  // without touching one of them, so this number is unchanged and only the spot
  // check's line array in `legacy-c-budget.test.js` moved.
  'App.jsx': 13,
  // Cleared by task 8.7: rebuilt against `shell/navigation.js` and `ds/`, with every
  // colour coming from a token utility class. The `LiveStatusV2` import went with the
  // hardcoded `status="running"` (§1.3), and `C` went with it.
  'components/TopBar.jsx': 0,
  // Replaced by `ResponsiveGate` in M4; entry goes with the file.
  'components/DesktopOnlyOverlay.jsx': 9,
  // Not a page: the block-category presentation map. Colours the builder
  // palette, so it clears with the builder work.
  'lib/blockRegistry.js': 8,
  'pages/UpdatePasswordPage.jsx': 7,
  'pages/RiskSettings.jsx': 6,
});

/**
 * The shim this budget measures. When this file is gone, the guard is finished:
 * delete `legacy-c-budget.test.js` and this file (task 27.2). The test asserts
 * the shim still exists so that its removal fails loudly here rather than
 * leaving a guard that measures nothing and passes forever.
 */
export const SHIM_PATH = 'components/ui-legacy/primitives.jsx';
