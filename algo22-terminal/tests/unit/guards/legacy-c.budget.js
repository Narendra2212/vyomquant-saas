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
  //
  // 191 -> 188 at task 25.1, PART 1, which took the environment indicator only. The three that
  // went are all of `SimulatedTag`'s: `color: C.purple`, `` border: `1px solid ${C.purple}66` ``
  // and `borderRadius: C.radius.sm`. They are replaced by `ENVIRONMENT.PAPER`'s `fg`, `wash` and
  // `border` read from `design/semantic.js` plus a `rounded-sm` utility class, which also clears
  // this page's single `no-colour-literals` entry (the hand-mixed `rgba(168,85,247,0.10)` wash).
  // The page-level label — the one that read `Simulated — no live order is ever placed` beside the
  // `<h1>` — is `ds/TradingEnvironmentBadge environment="PAPER" variant="strip" announce` now, and
  // it carried no shim reference of its own, so the swap itself is worth 0 of the 3.
  //
  // THE PER-FIGURE LABELS ARE DELIBERATELY *NOT* THE BADGE, and this is a real conflict rather
  // than an unfinished edit. §7.8 (1) says to replace this page's labels with
  // `TradingEnvironmentBadge environment="PAPER"`. The badge's PAPER label is `PAPER TRADING`
  // (`ENVIRONMENT.PAPER.label`, §8.2's declared table, also asserted by
  // `tests/unit/dashboard_phase2a_ui.test.jsx`), and `src/pages/__tests__/PaperTrading.test.jsx`
  // asserts the EXACT string `Simulated` inside each of the twelve figure regions, the five
  // titled panels and the tables' cards — `getAllByText('Simulated')`, which matches an element's
  // own text nodes and so cannot be satisfied by `PAPER TRADING`. That suite must keep passing
  // unchanged, so the sixteen per-figure seats keep the word and take the badge's OTHER three
  // axes instead: the `env.paper` hue, its wash, its dashed border and its `FlaskConical` glyph.
  // The page-level seat is the one place the two agree on text, because the badge's `strip`
  // variant renders `ENVIRONMENT.PAPER.long`, which IS that header's copy character for
  // character. Reconciling the rest needs either a relabelled badge (which rewrites §8.2 for
  // every other consumer) or an edited test; neither is part 1's to take.
  //
  // WHERE THE REMAINING 188 ARE. Measured with this guard's own `findLegacyC`, grouped by region,
  // line ranges as of this commit. Nothing below is estimated and the parts sum to 188.
  //
  //   22  module-level style objects, 318-433 — `TONE_COLOR` 4 (319-322), `PAGE_PADDING` 1 (333),
  //       `panelStyle` 4 (336-339), `labelStyle` 1 (344), `fieldStyle` 4 (356-362), `thStyle` 1
  //       (367), `tdStyle` 1 (378), `tableCaptionStyle` 1 (388), `stackedRowStyle` 3 (405-407),
  //       `stackedLabelStyle` 1 (413), `stackedValueStyle` 1 (423). Eleven frozen `style` objects
  //       shared by the whole page; the cheapest region to take and the one that unblocks the
  //       tables and the form at once.
  //   29  the small presentation components, 434-966 — `StatusPill` 3 (506-508), `StaleNote` 1
  //       (532), `Figure` 5 (574-619), `PanelNotice` 5 (644-668), `PanelBody` 6 (714-737),
  //       `DataTable` 3 (883-909), `ChartTooltip` 6 (939-954). `SimulatedTag` (462-493) and
  //       `TableFrame` (848-877) are now at 0. `PanelBody` is §11.1's eight states and
  //       `PanelNotice` its notice chrome — both are `ds/Panel`'s job, and neither may lose a
  //       state in the move.
  //   11  the five tables' column definitions, 1886-2044 — `positionColumns` 3 (1907-1913),
  //       `orderColumns` 1 (1938), `tradeColumns` 3 (1964-1972), `signalColumns` 1 (1984),
  //       `executionColumns` 3 (2010-2038). Per-cell profit/loss tones inside `render`
  //       functions, so these are `PnLDisplay`'s and `statusToken`'s, not a wrapper's.
  //    2  the page shell, 2224-2225 — `background: C.bg0`, `color: C.t1` on the root `<div>`.
  //    7  the header, 2239-2305 — the row's two spacings (2240), the `<h1>` colour (2243), the
  //       subtitle (2247) and the reconnecting indicator's 3 (2272-2274). The environment
  //       statement at 2306-2327 is the badge and holds none.
  //   16  session controls, 2328-2581 — the card 2 (2329), the field grid's spacings 2
  //       (2345-2346), a help note 1 (2369), the capital field's error border and help text 4
  //       (2441-2444) and the two operation `borderTop` dividers with a note 7 (2473-2542).
  //   17  the stop / reset report, 2582-2669 — the stop card 3 (2584, one arm of which is a
  //       conditional `complete` border), its definition list 1 (2601) and six `C.t1` value
  //       tones (2605-2628); the reset card 2 (2639), its list 1 (2648) and four more (2652-2664).
  //   10  the status strip, 2670-2796 — the grid's spacing 2 (2677-2678) and the four cells'
  //       label and value tones 8 (2698-2773).
  //   14  the live event stream, 2797-2916 — the card and its refused-channel border 3 (2798), a
  //       refusal notice chip 3 (2833-2835), the empty note 1 (2860), and the retention footer's
  //       rule, spacings and three inline counts 7 (2891-2908).
  //    3  the "not computed" note, 2917-2940 — `${C.warning}55` border, a spacing and the text.
  //    2  the figures grid, 2941-3062 — the grid's `gap` and `marginBottom` only. The twelve
  //       `Figure`s inside it hold none of their own; their colour comes from the component
  //       above, which is why that 5 is worth more than this 2.
  //   10  the equity curve, 3063-3130 — the card 2 (3064), the fill gradient's two stops 2
  //       (3080-3081), the grid and two axes 3 (3084-3086), the starting-capital `ReferenceLine`
  //       and the `Area` stroke 2 (3107, 3111) and the below-chart note 1 (3115).
  //   19  PnL and drawdown, 3131-3217 — the two-track grid 2 (3135); the PnL card 2 (3140), its
  //       grid and axes 3 (3150-3152), its three `Line` strokes 3 (3166-3168) and its note 1
  //       (3171); the drawdown card 2 (3179), its two gradient stops 2 (3191-3192), its grid and
  //       axes 3 (3195-3197) and its `Area` stroke 1 (3211). The near-duplication is the point:
  //       one `ds/Chart` call site takes both.
  //   11  the price series and trade markers, 3218-3269 — the card 2 (3219), the grid and axes 3
  //       (3233-3235), the close line and two fill markers 3 (3250-3252) and the legend row with
  //       its two arrows 3 (3255-3260).
  //    3  open positions, 3270-3296 — the card 2 (3271) and the below-table note 1 (3290).
  //    3  open orders, 3297-3322 — the card 2 (3298) and the below-table note 1 (3317).
  //    2  completed trades, 3323-3345 — the card only (3324).
  //    7  signal stream and execution events, 3346-end — the two-track grid 1 (3350), the two
  //       cards 4 (3352, 3378), the signal note 1 (3371) and the `role="alert"` error line 1
  //       (3398).
  //
  // The largest single win left is the 22 + 29 above the component body: 51 of the 188 sit in
  // eleven shared `style` objects and seven shared components, and every card, table and figure
  // region below reads from them. The eleven `Card` + `PanelTitle` call sites are the next 40-odd
  // between them, and they are all the same two lines — `background: C.bg2, borderColor: C.border`
  // — which is one `ds/Panel` swap repeated, not eleven decisions.
  'pages/PaperTrading.jsx': 188,
  // 148 -> 120 at task 24.1, which applied §9.1's five-stage visual grammar to the canvas.
  // The 28 that went are the canvas's own: `PremiumNodeWrapper`'s body, border, text and
  // two shadow colours, the node's stage strip, block id, marker, runtime and lock tones,
  // both handle rings, the port-dim fill, `runtimeTone`'s four states, the edge stroke on
  // creation and on a validation verdict, the React Flow `Background` and `MiniMap`
  // colours, and the empty-canvas hint. The palette, the toolbar, the banner stack and the
  // inspector still hold the rest — tasks 24.2 and 24.4 own those.
  //
  // 120 -> 114 at task 24.2a, which made the inspector a sibling grid track. The six that
  // went are the three track wrappers' own surfaces and rules: the palette panel's
  // background and right rule, the inspector panel's background and left rule, the
  // inspector header's bottom rule, and the "click a node to inspect" placeholder the
  // collapsed track no longer has anywhere to render. The palette's block rows, the
  // toolbar, the banner stack and the inspector's body still hold the remaining 114 —
  // task 24.4 owns zeroing the page.
  //
  // 114 -> 111 at task 24.4a, which moved the refused-connection reason off the full-width
  // top-of-page banner and onto a transient callout anchored at the drop point plus a
  // persistent entry in the validation issue list. The three that went are that banner's
  // whole palette — `${C.gold}20` wash, `1px solid ${C.gold}` rule, `C.gold` text — and it
  // is deleted rather than retokened, because the surface it painted no longer exists. Both
  // replacements are token-only: the callout is `ds/Alert severity="guidance"` (§9.3's
  // guidance row, which owns the hue) and the issue-list group reads `token.content.muted`
  // and `token.line.default` directly. Task 24.4b owns the other four banners and the last
  // 111.
  //
  // 111 -> 79 at task 24.4b, which repointed the banner stack onto
  // `components/builder/validationSurfaces`. The 32 that went are the eight full-width bands'
  // entire palette — each one was a `${C.gold}20` or `${C.red}20` wash, a `1px solid C.gold` /
  // `C.red` bottom rule and a `C.gold` / `C.red` monospace text colour, repeated per band and
  // again on every nested row inside `subscription-refusals`, `save-issues` and
  // `training-blocks` — plus the in-panel stale-report note, `SEVERITY_COLOUR`'s two entries,
  // the `server-override` line and the three `C.t3` detail tones in the training rows. All of
  // it is gone rather than retokened: `ValidationSurface` and `DeployedLockNotice` derive the
  // hue, the wash, the border style, the icon and the live-region role from `surface` +
  // `provenance`, so the page names no colour for any of the eight. The four `C.t3`/`C.gold`
  // survivors in that region were already inside rows the surfaces now own and read
  // `token.content.muted` and `statusToken(...)` directly.
  //
  // 79 -> 43 at task 24.4's follow-up, part 1, which took the four SEVERITY-LOOKUP regions. The
  // 36 that went are the status strip's 16, `ValidationIssueRow` and `ValidationIssuePanel`'s
  // 12, `StatusCell`'s 4 and `ApiSyncIndicator`'s 4.
  //
  // Eleven of the 36 were `C.red` / `C.green` / `C.gold` read straight off the shim by a ternary
  // chain per status cell — validation, feed, save, training and realtime each deciding a hue
  // from its own state vocabulary in place. They are now five frozen `state -> statusToken group`
  // maps (`VALIDATION_TONE`, `FEED_TONE`, `SAVE_TONE`, `TRAINING_TONE`, `REALTIME_TONE`) read
  // through one `cellTone` helper, so §4.1's mapping is the only thing that turns any of those
  // states into a colour and a state the strip deliberately does NOT colour — `validating`,
  // `SAVING`, `COMPLETED`, `CONNECTING` — is visibly absent from a map rather than the tail of an
  // `else`. The other 25 are `token.content.*`, `token.line.default` and `token.surface.raised`
  // read directly, exactly as 24.4a/b did for the non-state cases.
  //
  // TWO HUES ARE DELIBERATELY *NOT* THE ONE `statusToken` WOULD PICK, and both are recorded in
  // the maps: `FEED_TONE` gives `STALE` / `DISCONNECTED` the warning hue and `REALTIME_TONE`
  // gives `DISCONNECTED` the warning hue, where `statusToken('stale')` and
  // `statusToken('disconnected')` are both the ERROR hue. That is preserved, not chosen — this is
  // a retoken, and escalating a dropped feed or socket from amber to red is a design decision.
  //
  // 43 -> 0 at task 24.4's follow-up, PART 2, which finished the page. The 43 were the palette's
  // search field (7), registry-error panel (7), block rows (5), category headers (4), retry button
  // (3) and its loading and empty notes (2); the page shell's background with the toolbar's
  // surface, bottom rule and three dividers (6); the port chips' border, label tone and fill (3);
  // and the inspector's `Block` heading and category line (2), its "no descriptor" note (1) and
  // its node-status marker row (3).
  //
  // Forty of the 43 were non-state and are `token.content.primary` / `secondary` / `muted`,
  // `token.line.default` / `strong` and `token.surface.panel` / `raised` / `inset` read directly,
  // exactly as 24.4a/b and part 1 did. As in part 1, `C.t3` and `C.t4` collapse onto
  // `token.content.muted` rather than a fourth grey being invented to keep two shim names apart.
  //
  // The other three — the node-status marker row's `C.red` / `C.gold` / `C.t3` in one expression —
  // were the last severity lookup on the page, and they go through
  // `statusToken(surfaceTreatment(severity).tokenState).fg`, the mechanism part 1 established, so
  // the inspector's word for a node's verdict and the marker the canvas draws for it are one
  // decision. `surfaceTreatment`'s fallback surface is `warning`, which reproduces the old
  // `: C.gold` arm exactly; no validation at all stays `content.muted`.
  //
  // TWO VALUES HAVE NO TOKEN AND ARE RECORDED RATHER THAN REPLACED. The registry-error panel's
  // wash is `${fg}12` — an 8-digit-hex 7% error tint — and `token.status.error.wash` is 12%, so
  // switching to it would visibly strengthen the panel. It keeps the composition it had, with
  // `statusToken('error').fg` as the source instead of `C.red`. Likewise `C.borderLight` on the
  // retry button is `token.line.strong`, not `line.default`: it is a deliberately brighter rule on
  // the one interactive control in that panel, and flattening it would be a design change.
  //
  // The `C` IMPORT IS GONE from this page. With zero references there is nothing left to import,
  // and a lingering import is how a reference gets reintroduced by copying a neighbouring line —
  // the reason `pages/SignalTrace.jsx: 0` and `pages/Backtester.jsx: 0` record the same thing. The
  // entry stays at `0` and holds the page clean until task 27.2 deletes the shim; `Inp`, `Tag2`
  // and `PanelTitle` are still imported from `primitives`, and those are components, not colours.
  'pages/StrategyBuilder.jsx': 0,
  // M8. 92 -> 0 at task 21.4a. The 92 were the shim's text, surface and border values
  // spread across the filter grid's eight inline-styled inputs, the eleven-column row
  // template, the two detail cards and the timeline's rail — all of which the rebuild
  // replaced with `ds/*` primitives and token utility classes. The page imports nothing
  // from `components/ui-legacy/primitives` any more, so there is no shim reference left
  // to reintroduce by copying a neighbouring line.
  'pages/SignalTrace.jsx': 0,
  // M9. 66 -> 42 at task 23.1, which rebuilt the CONFIGURATION flow only. The 24 that went
  // were the config column's own: the four uppercase `C.t2` micro-labels, the three
  // inline-styled `<select>`/`<input>` surfaces (`C.bg3` + `C.border` + `C.t1` each), the
  // run button's `C.cyan` fill, the ML slider's `accentColor`, and the removed
  // data-validation panel's `${C.red}12` wash with its issue and warning rows. Every
  // control in that column is a `ds/Field`, a `ds/Panel` or a `ds/CommandButton` now.
  //
  // 42 -> 0 at task 23.2, which rebuilt the RESULT region on §7.4's three declared tiers.
  // The 42 were all of it: the eight inline-styled metric cards, the equity chart's own
  // `C.green` stroke and two-stop gradient, `C.border` grid and `CustomTooltip`, and the
  // saved-history and trade tables' per-cell `C.t1`/`C.t2`/`C.t3`/`C.green`/`C.red`
  // colouring. Tier 1 is six `ds/Metric`s, tier 2 two `ds/Chart`s (which own the series
  // palette), tier 3 a `ds/Tabs` over a `ds/DataTable`, and the region's states are
  // `ds/Panel`'s — so the page imports nothing from `components/ui-legacy/primitives` any
  // more and there is no shim reference left to reintroduce by copying a neighbouring line.
  'pages/Backtester.jsx': 0,
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
  // New at task 24.4b: §9.3's four validation surfaces as their own module. It entered
  // the tree clean and imports the shim not at all — the three band surfaces are
  // `ds/Alert`, the destructive one is `ds/ConfirmDialog`, and the only hue it names
  // comes from `design/semantic.js`'s `statusToken`. Seeded at `0` for the reason
  // `components/Sidebar.jsx: 0` keeps its entry: a file with no entry is a file whose
  // cleanliness nothing is holding, and this is the module the builder's eight `C.gold` /
  // `C.red` bands are about to be repointed onto — a shim reference reaching it would
  // spread to every one of them at once.
  //
  // `tests/unit/builder/validationSurfaces.test.jsx` deliberately has NO entry here: this
  // guard scans `src/**` only, so an entry naming a path under `tests/` fails both the
  // `names only files that still exist` and the stray-entry checks below.
  'components/builder/validationSurfaces.jsx': 0,
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
  //
  // 8 -> 0 at task 24.1. The eight were one hue per category, chosen here: a `lib/` module
  // deciding a palette, which is exactly what the note under `no-colour-literals`'s two
  // roots warns about. `CATEGORY_PRESENTATION` is now *derived* from `design/semantic.js`'s
  // stage band table (§9.1), so the icon and colour the palette draws and the ones the
  // canvas draws are one decision, and the module imports nothing from the shim.
  'lib/blockRegistry.js': 0,
  // New at task 23.3 — the drawdown curve's derivation from the real equity curve
  // (design.md §7.4, Requirement 6.3). A pure data module: no JSX, no import of the shim,
  // nothing to render a colour with. Entered at `0` and held there for the reason
  // `components/Sidebar.jsx: 0` keeps its entry — a file with no entry is a file whose
  // cleanliness nothing is holding, and `lib/blockRegistry.js` above is the standing proof
  // that a `lib/` module can end up reaching for the shim.
  'lib/drawdownSeries.js': 0,
  'pages/UpdatePasswordPage.jsx': 7,
  'pages/RiskSettings.jsx': 6,
  // New at task 20.2 — §7.8 (4)'s three shared trading panels, extracted from
  // `pages/LiveTrading.jsx` so `pages/PaperTrading.jsx` can render the same components at
  // task 25.2. None of the four imports the shim: every colour comes from `ds/` primitives
  // and token utility classes. Entered at `0` and held there, for the reason
  // `components/Sidebar.jsx: 0` and `components/TopBar.jsx: 0` keep theirs — a file with no
  // entry is a file whose cleanliness nothing is holding.
  'components/trading/PositionsPanel.jsx': 0,
  'components/trading/OrdersPanel.jsx': 0,
  'components/trading/ExecutionsPanel.jsx': 0,
  'components/trading/liveFrame.js': 0,
});

/**
 * The shim this budget measures. When this file is gone, the guard is finished:
 * delete `legacy-c-budget.test.js` and this file (task 27.2). The test asserts
 * the shim still exists so that its removal fails loudly here rather than
 * leaving a guard that measures nothing and passes forever.
 */
export const SHIM_PATH = 'components/ui-legacy/primitives.jsx';
