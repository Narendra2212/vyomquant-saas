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
  // M8. 92 -> 0 at task 21.4a. The 92 were the shim's text, surface and border values
  // spread across the filter grid's eight inline-styled inputs, the eleven-column row
  // template, the two detail cards and the timeline's rail — all of which the rebuild
  // replaced with `ds/*` primitives and token utility classes. The page imports nothing
  // from `components/ui-legacy/primitives` any more, so there is no shim reference left
  // to reintroduce by copying a neighbouring line.
  'pages/SignalTrace.jsx': 0,
  'pages/Backtester.jsx': 66,
  // M7. 5 -> 0 at task 16.2. The 5 were one line: `const COLORS = [C.orange,
  // C.purple, C.cyan, C.gold, C.t3]` — five entries rendering four colours, since
  // M1 collapsed `C.orange`/`C.gold` onto the one amber and `C.purple` onto
  // neutral. It is not repaired with five distinct tokens: `styles/tokens.css` has
  // no fifth CATEGORICAL hue (every non-brand hue already means live, profit, loss,
  // warning or paper), and `ds/Chart` has no `pie` kind and no colour prop. The
  // allocation is a bar chart with one `brand` series instead, so assets are told
  // apart by position on a labelled axis and no palette is needed. The array is
  // gone, the `C` import with it, and `no-local-tokens`' one scheduled exception
  // clears on the same edit — exactly as task 1.11 predicted.
  'pages/Portfolio.jsx': 0,
  // 1 -> 0 at task 17.1. The single reference was `color: C.t3` on the page-level "Loading
  // strategies..." div, and the rebuild has no page-level loading state to put it in:
  // `ds/Panel` owns §11.1's eight states for the table region, so the skeleton and the
  // header arrive together. The `C` import went with it.
  'pages/Strategies.jsx': 0,
  // M7. Added at 0 by task 15.1 rather than lowered: the page imported `C` from the shim
  // but never read a member off it, so it never carried a reference for the seeding pass
  // to record. The rebuild dropped the import along with `SectionH` and `Tag2`, and the
  // entry is here for the reason `Sidebar.jsx: 0` and `TopBar.jsx: 0` are — it records
  // that the file is clean and holds it there until the shim is deleted (task 27.2).
  'pages/TradeHistory.jsx': 0,

  // -- Files beyond task 1.12's list ---------------------------------------
  // Task 1.12 seeds from the in-scope pages. These carry 1470 further
  // references between them. They are deferred pages, the landing page and the
  // shell, all retokened by M1 but not restructured (§14.4). They are budgeted
  // rather than ignored because the shim cannot be deleted at task 27.2 while
  // any of them still reads from it.
  'components/SupportCenter.jsx': 160,
  'pages/Profile.jsx': 160,
  // `components/DashboardUpgrades.jsx: 135` stood here until task 19.4 deleted the file.
  // It is removed rather than lowered to `0`, per this header's rule: an entry reaching
  // zero records that a file is clean and must stay clean, but a deleted file has no
  // source to measure, and `names only files that still exist` fails on an entry that
  // points at nothing. The 135 references left with the 786 lines that held them —
  // gamified upgrade prompts no module imported (design.md §7.1, Requirement 1.5) — so
  // this is 135 fewer call sites standing between here and task 27.2's deletion of the
  // shim, taken without migrating anything.
  'pages/Landing.jsx': 122,
  // 122 -> 121 at task 10.4. The one reference was `AuditTab`'s
  // `style={{ textAlign: "center", color: C.t3 }}` — the whole component was a single
  // centred placeholder line, and it went with the tab that reached it (design.md §1.9,
  // §7.3, Requirement 19.4). The four `ConfirmDialog`s that replaced this page's four
  // `window.confirm` calls in the same task add none: `ds/ConfirmDialog` is tokens-only,
  // so a confirmation surface costs zero shim references. The page's other 121 are
  // untouched and are task 27.1's, which takes this entry to zero.
  'pages/StrategyDetail.jsx': 121,
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
