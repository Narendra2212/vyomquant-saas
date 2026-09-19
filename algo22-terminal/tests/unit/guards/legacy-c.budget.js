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
  // 188 -> 137 at task 25.1, PART 2, which took the 51 that sat ABOVE the component body: the
  // eleven shared module-level `style` objects (22) and the seven shared presentation components
  // (29). Nothing was restructured. Every reference was swapped for the token it already
  // resolved to, the markup and the reads are untouched, `PanelBody` still renders §11.1's eight
  // states and `PanelNotice` still its notice chrome, and `pages/__tests__/PaperTrading.test.jsx`
  // passes byte-unchanged at 49/49. `SimulatedTag` and `TableFrame` were already at 0 and stayed.
  //
  // FORTY-SIX OF THE 51 ARE NON-STATE and read `token.*` directly, exactly as 24.4a/b and 24.4's
  // two follow-up parts did: `content.primary` / `secondary` / `muted`, `line.default` /
  // `strong`, `surface.panel` / `raised` / `inset`, `brand.base`, `shadow.raised`, `radius.sm` /
  // `md` / `lg` and `space['2']` / `['4']` / `['5']`. `C.t3` and `C.t4` do not need collapsing
  // here the way they did on the builder — this page never read `C.t4`, so its two greys were
  // always `content.secondary` and `content.muted`.
  //
  // THE OTHER FIVE ARE STATE → HUE and go through `statusToken`. Four were `TONE_COLOR`, a map
  // of this page's four tone words onto `C.profit` / `C.warning` / `C.loss` / `C.t2`; it is now
  // `TONE_STATUS`, a frozen tone -> `statusToken` group map read through one `toneColour` helper,
  // which is the shape 24.4 part 1 established for its five status-cell maps. `muted` maps to
  // `idle` rather than to a grey of its own because `token.status.neutral.fg` and
  // `token.content.secondary` are the same value (`#8B95A5`), so no fourth grey was invented.
  // An unnamed tone gives `null`, which preserves each call site's existing fallback.
  //
  // ONE HUE IS DELIBERATELY *NOT* THE ONE `statusToken` WOULD PICK, and it is recorded on the
  // component: `StaleNote` is the WARNING hue, where §4.1 puts `stale` in the ERROR group. That
  // is preserved, not chosen — a figure whose price stopped refreshing is a condition of the
  // data, the note says so in words and prints the last validated instant, and escalating it to
  // the red this page uses for a failed read is a design decision. `FEED_TONE` on the builder
  // records the same divergence for the same reason.
  //
  // ONE VALUE HAS NO TOKEN IN THE UNIT IT IS NEEDED IN. `PAGE_PADDING` is the only token on this
  // page read ARITHMETICALLY — `clientWidth - 2 * PAGE_PADDING` seeds the layout measurement —
  // and `token.space['5']` is `'1.25rem'`, which that expression would turn into `NaN`. It is
  // `Math.round(parseFloat(token.space['5']) * 16)`, the same projection `primitives.jsx`'s
  // `legacyPx` makes for the whole `C.space` scale (and names this very expression as its
  // reason); the 16 is the browser default root font size, which `tokens.css` does not declare
  // and which is therefore not a token. The value is unchanged — `C.space.xl` was this same 20.
  // Alongside it, `ChartTooltip` keeps `line.strong` rather than the panels' `line.default`: a
  // tooltip floats over the series it describes, and that is the brighter-rule case 24.4 part 2
  // recorded for the palette's retry button.
  //
  // 137 -> 77 at task 25.1, PART 3, which took the two MECHANICAL families the note at the foot of
  // part 2 named: the thirteen `Card` call sites (28) and the four chart regions (32). Nothing was
  // restructured — `Card` is still `Card` and the charts are still recharts, because swapping them
  // for `ds/Panel` and `ds/Chart` is structural and is not this pass's. Every reference was swapped
  // for the token it already resolved to, and `pages/__tests__/PaperTrading.test.jsx` passes
  // byte-unchanged at 49/49. The edit is line-for-line in place: the page is the same 3465 lines it
  // was before it, so every range in the breakdown below is unshifted.
  //
  // THE THIRTEEN CARDS WERE ONE PAIR OF VALUES REPEATED — `background: C.bg2` and
  // `borderColor: C.border`, now `token.surface.raised` and `token.line.default`. Eleven were
  // byte-identical in two shapes (seven `className="mb-4"`, four `minWidth: 0` chart cards) and
  // are byte-identical still.
  //
  // TWO OF THE THIRTEEN CARRY STATE IN THE BORDER AND KEEP THEIR TERNARY EXACTLY. The `Last stop`
  // card's border is `stopReport.complete === true ? line.default : `${warning}66`` and the live
  // stream's is ``channelRefusal ? `${loss}66` : line.default``. Both conditions, both operand
  // orders and both `66` alpha suffixes are untouched; only the SOURCE of each hue moved, from
  // `C.warning` / `C.loss` to `statusToken('warning').fg` / `statusToken('loss').fg`. A border that
  // reports an incomplete stop or a refused channel is the panel's one visual signal for it, so
  // collapsing either arm would have been a behaviour change, not a retoken.
  //
  // THE FOUR CHARTS ARE FOUR NEAR-COPIES OF ONE SET OF DECISIONS, and the 32 split five ways:
  //
  //   12  CHROME. The `CartesianGrid` stroke and the two axis strokes, byte-identical in all four,
  //       now `token.line.default` and `token.content.muted`.
  //    3  PROSE. The equity and PnL below-chart notes and the price chart's marker legend row, all
  //       `token.content.muted`. (Drawdown has no note of its own.)
  //    2  SPACING. The PnL/drawdown two-track grid's `gap` and `marginBottom` — see below.
  //   11  STATE → HUE, through `statusToken`: equity's two gradient stops, its `Area` stroke and
  //       its series-boundary `ReferenceLine` (`'profit'`, `'profit'`, `'warning'`); drawdown's two
  //       stops and its `Area` (`'loss'`); and the price chart's two fill markers with their two
  //       legend arrows. The markers read `'buy'` / `'sell'`, which §4.1's vocabulary maps onto the
  //       same profit and loss groups and which say what a marker actually reports — a direction,
  //       not a verdict on the trade.
  //    4  SERIES IDENTITY, which is NOT state and so takes no `state -> hue` map. The PnL chart's
  //       `Realized` / `Unrealized` / `Total` are not three states of one thing and the price
  //       chart's `Close` is not a state at all; they need only to stay distinguishable. They read
  //       `token.brand.base`, `token.status.neutral.fg`, `statusToken('profit').fg` and
  //       `token.brand.base` — the exact values `C.accent`, `C.purple`, `C.profit` and `C.accent`
  //       already resolved to, so no chart changed on screen. Colour is not the only axis carrying
  //       the PnL distinction: the three keep their existing `strokeDasharray` (solid, `5 3`,
  //       `1 3`) and the note under the chart states it in words, so a reader who cannot separate
  //       the three hues is unaffected either way.
  //
  // ONE PAIR HAS NO TOKEN IN THE UNIT IT IS NEEDED IN, for the same reason `PAGE_PADDING` did not.
  // The PnL/drawdown two-track grid's `gap` and `marginBottom` were `C.space.md`, which the shim
  // projects to the NUMBER 12 (`legacyPx(token.space['3'])`); they are `token.space['3']` — the
  // string `'0.75rem'` — which React writes as `0.75rem` where it wrote `12px`. Those are the same
  // length at the browser default root size, and it is the substitution part 2 already made for
  // `C.space.lg` -> `token.space['4']` and `C.space.sm` -> `token.space['2']`, so the page's spacing
  // has one source. The remaining `C.space.md` reads outside the charts are the final pass's.
  //
  // NO HUE DIVERGENCE WAS INTRODUCED BY THIS PART. Every one of the 60 is the value the shim
  // already gave it. The page's one recorded divergence is still `StaleNote`'s amber, from part 2.
  //
  // 77 -> 0 at task 25.1, the FINAL PASS, which took every reference that was left. Counted by
  // shim name, because after part 3 the region split said nothing the name does not: 42 TEXT TONES
  // (`C.t1` 19, `C.t2` 12, `C.t3` 11 -> `token.content.primary` / `secondary` / `muted`), 16
  // SPACINGS (`C.space.md` 14, `C.space.lg` 2 -> `token.space['3']` / `['4']`), 7 SURFACE, RULE AND
  // RADIUS reads (`C.border` 4, `C.radius.sm` 2, `C.bg0` 1 -> `token.line.default`,
  // `token.radius.sm`, `token.surface.canvas`) and 12 STATE HUES (`C.warning` 6, `C.loss` 5,
  // `C.profit` 1 -> `statusToken`). Nothing was restructured, no read changed, no markup moved,
  // and `pages/__tests__/PaperTrading.test.jsx` passes byte-unchanged at 49/49.
  //
  // THE `C` IMPORT IS GONE from the file. With no reference left there is nothing to import, and a
  // lingering import is how one gets reintroduced by copying a neighbouring line. `PanelTitle` and
  // `Spinner` stay on that import — they are components from the same module, not colours.
  //
  // THE TWO P&L CELLS WERE THE ONE JUDGEMENT IN THE PASS, AND THEY STAYED ON `statusToken`.
  // `ds/PnLDisplay` owns exactly their decision — sign -> hue on a money figure — but adopting it
  // is a restructure rather than a retoken: it formats through `ds/Metric`'s `formatFigure` where
  // these cells format through `formatMoneyDecimal`, which shifts the server's exact decimal digit
  // for digit; it renders the not-available marker where they render `NOT_COMPUTED` /
  // `NOT_REPORTED`; and it wraps the figure in a `<span>` of its own instead of leaving a text node
  // in the cell. It would also repick both hues, because `pnlToken` reads zero as neutral and any
  // gain as profit, where `unrealized` leaves a gain in `content.primary` and `realized` prints a
  // `+` on zero in the profit hue. What each cell displays is unchanged; only the source of its hue
  // moved. The reason is recorded on the columns themselves.
  //
  // NO HUE DIVERGENCE AND NO MISSING TOKEN WERE ADDED BY THIS PART. Every one of the 77 is the
  // value the shim already gave it, in the same unit or in one React writes identically — `12px`
  // from `token.space['3']`'s `0.75rem`, `4px` from `token.radius.sm`. The page's two recorded
  // exceptions are still part 2's: `StaleNote`'s amber, and `PAGE_PADDING`'s arithmetic on a rem.
  //
  // THE PAGE NOW READS THE SHIM NOWHERE. The five `C.` mentions a plain `grep` still finds in it
  // are prose inside comments recording what a value used to be, which `findLegacyC` excludes by
  // construction — and it is `findLegacyC` this 0 is measured with.
  'pages/PaperTrading.jsx': 0,
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
  // 160 -> 0 at task 27.2, batch 2, and the `C` import is deleted rather than narrowed for
  // the reason `pages/LegalPage.jsx`'s was: `C` was the only specifier this file took from
  // the shim, so the specifier and the module both go and `token` is read straight from
  // `design/tokens.js`. Every one of the 160 is the value the shim already gave it, so
  // nothing on this page changed on screen.
  //
  // By shim name: `C.border` 34 -> `token.line.default`; `C.cyan` 23 -> `token.brand.base`;
  // `C.t1` 23, `C.t2` 20, `C.t3` 19 -> `token.content.primary` / `secondary` / `muted`;
  // `C.bg3` 19, `C.bg2` 11, `C.bg1` 1 -> `token.surface.inset` / `raised` / `panel`;
  // `C.green` 5 -> `token.status.profit.fg`; `C.red` 5 -> `token.status.loss.fg`. No
  // `C.space.*`, `C.radius.*`, `C.shadow*`, `C.glow.*` or `C.gradient.*` read on the page,
  // so neither the rem-string substitution nor the dead-declaration deletion arose here.
  //
  // THE TEN STATE HUES STAY OFF `statusToken`, AND THAT IS PRESERVED RATHER THAN CHOSEN.
  // Every `C.green` / `C.red` read on this page is a ticket-lifecycle affordance, not a
  // status verdict: the Reopen and Close buttons, the created-ticket success banner, the
  // load/submit error banner and its dismiss control. There is no frozen `state -> group`
  // map in the file to route them through — what colour a ticket gets is decided by
  // `STATUS_CONFIG` and `PRIORITY_CONFIG`, two module-level maps of raw `rgba()`/hex pairs
  // that were never shim reads and so are not this batch's to take. Both ternaries keep
  // their condition, operand order and `40` alpha suffix; only the source of each hue moved.
  //
  // THIS PAGE'S 37 `no-colour-literals` ENTRIES ARE UNTOUCHED, and they are why the two
  // config maps above stand: `STATUS_CONFIG`'s six wash/text pairs, `PRIORITY_CONFIG`'s
  // four, the seven hand-mixed `rgba(0, 212, 255, …)` cyans, the staff-comment tint and the
  // `#000` button labels all predate the shim. None was a `C.` read, none is introduced, and
  // the count is the same 37 before and after.
  'components/SupportCenter.jsx': 0,
  // 160 -> 0 at task 27.2, batch 3, and this is the first entry in the series whose `C`
  // import is NARROWED rather than deleted. `C` was one of three specifiers this page took
  // from the shim and the other two — `SectionH` and `Inp` — are components, not colours, so
  // the module import stays for them and `token` is read from `design/tokens.js` beside it.
  // Re-homing those two is a later pass's. Every one of the 160 is the value the shim already
  // gave it, so nothing on this page changed on screen.
  //
  // By shim name: `C.t3` 35 -> `token.content.muted`; `C.border` 23 -> `token.line.default`;
  // `C.t1` 20, `C.t2` 19 -> `token.content.primary` / `secondary`; `C.green` 16 ->
  // `token.status.profit.fg`; `C.accent` 14 and `C.cyan` 7 -> `token.brand.base` (one hue in
  // the shim, collapsed onto it here rather than a second brand value being invented to keep
  // the two names apart); `C.bg2` 12, `C.bg3` 9 -> `token.surface.raised` / `inset`; `C.red`
  // 4 -> `token.status.loss.fg`; `C.gold` 1 -> `token.status.warning.fg`. No `C.space.*`,
  // `C.radius.*`, `C.shadow*`, `C.glow.*` or `C.gradient.*` read on the page, so neither the
  // rem-string substitution nor the dead-declaration deletion arose here.
  //
  // THE 21 STATUS HUES STAY OFF `statusToken`, AND THAT IS PRESERVED RATHER THAN CHOSEN, on
  // the same terms as `components/SupportCenter.jsx` above: there is no frozen
  // `state -> group` map in this file to route them through, and inventing one would be a
  // restructure. What the 21 paint is account chrome rather than a status verdict — the
  // account-active badge and its dot, the verified-email chip, the PROTECTED chip, the MFA
  // `Configured` label and its dot, the isolated-margin note and its dot, the three
  // copy-confirmation ticks, the subscription-active label and its dot, the two referral
  // chips and the active-referral figure (green); the sync-error glyph and its heading and
  // the notification panel's `RISK & CIRCUIT BREAKERS` category label (red); the
  // lifetime-earnings figure (gold). Every ternary among them keeps its condition and its
  // operand order; only the source of each hue moved.
  //
  // THE ONE SIGNED FIGURE IS THE ONE JUDGEMENT, AND IT STAYED WHERE IT WAS. The total-P&L
  // read is `(stats?.total_pnl || 0) >= 0 ? profit : loss`, which is exactly what
  // `design/semantic.js`'s `pnlToken` and `ds/PnLDisplay` own — but adopting either is a
  // restructure, not a retoken, and `pnlToken` reads zero as NEUTRAL where this ternary
  // paints a flat figure profit green. Repicking that hue is a design decision. Recorded for
  // the reason `pages/PaperTrading.jsx`'s two P&L cells are.
  //
  // THE LIFETIME-EARNINGS FIGURE KEEPS A DEAD FALLBACK. It read `C.gold || C.accent`, and
  // `C.gold` is a non-empty string, so the right arm has never been reachable. It is
  // `token.status.warning.fg || token.brand.base` now — both operands in place, because
  // collapsing a no-op is a code change and this is a retoken, exactly as
  // `pages/LegalPage.jsx`'s no-op back-button hover was left standing.
  //
  // THIS PAGE'S 7 `no-colour-literals` ENTRIES ARE UNTOUCHED: the five hand-mixed
  // `rgba(38, 166, 154, …)` values (four washes behind the green chips above, plus the
  // account-active chip's rule) and the notification toggle's `#fff` knob with its
  // `rgba(0,0,0,0.3)` shadow. None was a `C.` read, none is introduced, and the count is the
  // same 7 before and after.
  'pages/Profile.jsx': 0,
  // `components/DashboardUpgrades.jsx: 135` stood here until task 19.4 deleted the file.
  // It is removed rather than lowered to `0`, per this header's rule: an entry reaching
  // zero records that a file is clean and must stay clean, but a deleted file has no
  // source to measure, and `names only files that still exist` fails on an entry that
  // points at nothing. The 135 references left with the 786 lines that held them —
  // gamified upgrade prompts no module imported (design.md §7.1, Requirement 1.5) — so
  // this is 135 fewer call sites standing between here and task 27.2's deletion of the
  // shim, taken without migrating anything.
  // 122 -> 0 at task 27.2, batch 3, and the `C` import is deleted rather than narrowed for the
  // reason `pages/LegalPage.jsx`'s and `components/SupportCenter.jsx`'s were: `C` was the only
  // specifier this file took from the shim, so the specifier and the module both go and `token`
  // is read straight from `design/tokens.js`.
  //
  // 114 OF THE 122 ARE STRAIGHT SUBSTITUTIONS, each one the value the shim already gave it. By
  // shim name: `C.accent` 25 and `C.cyan` 1 (the hero's radial wash) -> `token.brand.base`;
  // `C.border` 22 -> `token.line.default`; `C.t1` 16, `C.t2` 13 -> `token.content.primary` /
  // `secondary`; `C.t3` 14 and `C.t4` 1 (the copyright line) -> `token.content.muted`, which
  // the shim already held as one grey; `C.gold` 7 (the Elite plan's border and badge, the
  // five-star fill) -> `token.status.warning.fg`; `C.bg2` 5 -> `token.surface.raised`; `C.bg0`
  // 3 and `C.bg` 2 -> `token.surface.canvas`, likewise one surface in the shim; `C.bg3` 3 (the
  // demo modal's three fields) -> `token.surface.inset`; `C.profit` 2 ->
  // `token.status.profit.fg`. No `C.space.*`, `C.radius.*` or `C.shadow*` read on the page.
  //
  // THE OTHER EIGHT ARE THE TREE'S LAST `C.glow` READS, AND THEY ARE DELETED RATHER THAN
  // RETOKENED: `C.glow.accent` 4, `C.glow.gold` 1, the one computed `C.glow[…]`, and the
  // `C.profit` and `C.gold` that made up that computed key. `glow.*` has been the literal
  // string `'none'` since M1 (Requirement 1.5), so there was no hue to carry across. With
  // these gone, the only `C.glow` and `C.gradient` reads left in `src/` are inside the shim.
  //
  // ONE OF THE FIVE DECLARATIONS WAS WHOLLY DEAD AND IS GONE ENTIRELY: the pricing card's
  // `const glowShadow = isElite ? C.glow.gold : (isPro ? C.glow.accent : "none")` — three arms
  // of one retired value.
  //
  // THE OTHER FOUR SAT INSIDE COMPOUND `box-shadow` LISTS THAT ALSO CARRY LIVE SHADOWS, so the
  // dead TERM goes and the declaration stays. Deleting those four outright would have taken
  // six of this page's 18 `no-colour-literals` entries with it, and that guard asserts its
  // counts in BOTH directions. The count is the same 18 before and after, and no colour
  // literal was introduced.
  //
  // REMOVING THE DEAD TERM ALSO MAKES THOSE LISTS VALID CSS FOR THE FIRST TIME, which is worth
  // stating rather than glossing: `box-shadow` takes `none` OR a shadow list, never `none`
  // inside one, so every list that interpolated a retired glow was being rejected whole.
  // `LandingFeatureCard` is unchanged either way — a rejected hover value left the resting
  // shadow standing, and that is the value its hovered arm now names in as many words, which
  // is also why its two arms hold identical text and are left as two arms (collapsing them
  // would move two of the 18). `LandingPricingCard`'s hover is the one difference on the page:
  // its `0 30px 60px rgba(0,0,0,0.4)` renders now where the rejected list left the resting
  // `0 20px 40px rgba(0,0,0,0.2)` in place. The `Start Free` CTA is unaffected — its visible
  // pulse comes from the `ctaPulse` keyframes (four of the 25 `C.accent` reads), and a running
  // animation outranks the inline declaration those three sites were writing.
  //
  // THAT ONE DIFFERENCE IS RECORDED RATHER THAN CHASED, because nothing renders this file.
  // Its own header has said since before this spec that it is unmounted and that
  // `components/landing/LandingPage.jsx` is the routed `/` page; it also reads six identifiers
  // it never imports (`useNavigate`, `GitBranch`, `TrendingUp`, `Bot`, `ChevronUp`,
  // `ChevronDown`), so mounting it would throw before any shadow was painted. Deleting it is
  // not this task's to take: task 27.2 is the shim's deletion, and a page that reads no shim
  // value no longer stands in the way of it.
  'pages/Landing.jsx': 0,
  // 122 -> 121 at task 10.4. The one reference was `AuditTab`'s
  // `style={{ textAlign: "center", color: C.t3 }}` — the whole component was a single
  // centred placeholder line, and it went with the tab that reached it (design.md §1.9,
  // §7.3, Requirement 19.4). The four `ConfirmDialog`s that replaced this page's four
  // `window.confirm` calls in the same task add none: `ds/ConfirmDialog` is tokens-only,
  // so a confirmation surface costs zero shim references.
  //
  // 121 -> 0 at task 27.1, and the `C` specifier is gone from the import with them: with no
  // reference left there is nothing to import, and a lingering specifier is how the next
  // contributor reintroduces one by copying the line beside it. `Tag2` and `StatusDot`
  // remain imported from the same module — they are components, not colours, and task 27.2
  // owns re-pointing them.
  //
  // The 121 were, by region: 26 in the page shell (header row, action-error banner, the
  // identity card and the tab strip), 8 in `OverviewTab`, 12 in `DeploymentsTab` and
  // `BacktestsTab`, 24 in `VersionsTab`, 17 in `MetricsTab`, 10 in `RiskTab` and
  // `ConfigurationTab`, 17 in `ExecutionsTab`, 8 in `SignalsTab`, 3 in the three pointer
  // tabs, 2 in `MarketplaceTab` and 16 in `SubscribersTab` / `RevenueTab`. Non-state values
  // read `token.content.*` / `token.line.default` / `token.surface.inset` /
  // `token.brand.base` directly (`C.t3` and `C.t4` both resolved to `content.muted` and are
  // collapsed onto it rather than a fourth grey); the signed figures go through
  // `design/semantic.js`'s `pnlToken`, which is also the correction of the old `>= 0`
  // ternaries — those painted a flat figure profit green and an UNREAD one loss red.
  'pages/StrategyDetail.jsx': 0,
  // 111 -> 0 at task 27.2, batch 4. The `C` specifier is NARROWED out of the import rather
  // than the import deleted: `SectionH`, `PanelTitle` and `Tag2` stay on it — components from
  // the same module, not colours, and a later pass rehomes them. `token` is read straight
  // from `design/tokens.js`. Every one of the 111 is the value the shim already gave it.
  //
  // By shim name: `C.cyan` 26 -> `token.brand.base`; `C.red` 16 -> `token.status.loss.fg`;
  // `C.t3` 15, `C.t1` 7, `C.t2` 5 -> `token.content.muted` / `primary` / `secondary`;
  // `C.border` 13 -> `token.line.default`; `C.green` 12 -> `token.status.profit.fg`;
  // `C.bg3` 8, `C.bg1` 4, `C.bg2` 4, `C.bg0` 1 -> `token.surface.inset` / `panel` / `raised`
  // / `canvas`. No `C.space.*`, `C.radius.*`, `C.shadow*`, `C.glow.*` or `C.gradient.*` read
  // here, so neither the rem substitution nor the dead-declaration case arose, and no
  // `no-colour-literals` entry moved.
  'pages/Billing.jsx': 0,
  // 77 -> 0 at task 27.2, batch 2, on the same terms as `components/SupportCenter.jsx`
  // above — one `C` import, the file's only specifier from the shim, deleted. Every one of
  // the 77 is the value the shim already gave it.
  //
  // By shim name: `C.cyan` 24 and `C.accent` 1 -> `token.brand.base` (the shim collapsed
  // both onto the one brand hue, and they are collapsed here rather than a second brand
  // value being invented to keep the two names apart — the lone `C.accent` was the loading
  // spinner, every `C.cyan` the unread/active affordance); `C.t3` 12, `C.t2` 6, `C.t1` 4 ->
  // `token.content.muted` / `secondary` / `primary`; `C.red` 10 -> `token.status.loss.fg`;
  // `C.border` 7 -> `token.line.default`; `C.bg2` 5, `C.bg` 2, `C.bg3` 1 ->
  // `token.surface.raised` / `canvas` / `inset`; `C.green` 4 -> `token.status.profit.fg`;
  // `C.orange` 1 -> `token.status.warning.fg`. No `C.space.*`, `C.radius.*` or `C.shadow*`
  // read here either.
  //
  // `SEVERITY_COLORS` KEEPS ITS SHAPE AND ITS FOUR ENTRIES, for the reason
  // `components/builder/NodeTrace.jsx`'s `STATUS_COLOUR` kept its. It is already a frozen
  // `severity -> colour` map, so there was nothing to restructure — only the source of each
  // hue moved, to `token.brand.base` / `status.warning.fg` / `status.loss.fg` /
  // `status.loss.fg`. It is deliberately NOT repointed onto `design/semantic.js`'s
  // `statusToken`: `info` reads the BRAND hue here rather than a status hue, and `critical`
  // and `emergency` share one red where `statusToken` would give the two severities distinct
  // groups. Preserved, not chosen; this is a retoken.
  //
  // THE THREE LIVE/OFFLINE TERNARIES ARE UNCHANGED apart from the hue source. The WebSocket
  // pill's wash, its `40`-suffixed rule, its dot and its label all read
  // `wsConnected ? profit : loss`, with the same condition and the same operand order they
  // had as `C.green` / `C.red`.
  //
  // THE ONE `no-colour-literals` ENTRY IS UNTOUCHED — the `#000` on the unread-count badge,
  // which was never a `C.` read. 1 before, 1 after.
  'components/NotificationCenter.jsx': 0,
  // 60 -> 0 at task 27.2, batch 4. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Tag2` and `PanelTitle` stay on it, as components. `token` is read from
  // `design/tokens.js`. Every one of the 60 is the value the shim already gave it.
  //
  // By shim name: `C.t3` 13, `C.t1` 6, `C.t2` 1 -> `token.content.muted` / `primary` /
  // `secondary`; `C.border` 8 -> `token.line.default`; `C.red` 8 -> `token.status.loss.fg`;
  // `C.green` 7 and `C.profit` 1 -> `token.status.profit.fg`; `C.bg3` 6, `C.bg2` 4 ->
  // `token.surface.inset` / `raised`; `C.cyan` 4 and `C.accent` 1 -> `token.brand.base`
  // (the shim held both names as the one brand hue); `C.warning` 1 ->
  // `token.status.warning.fg`. No `C.space.*`, `C.radius.*`, `C.shadow*`, `C.glow.*` or
  // `C.gradient.*` read here, and no `no-colour-literals` entry moved.
  'components/ResearchConsole.jsx': 0,
  // 57 -> 0 at task 27.2, batch 5a. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Inp` stays on it, as a component. `token` is read from `design/tokens.js`.
  // Every one of the 57 is the value the shim already gave it. By shim name: `C.t3` 19,
  // `C.t2` 6, `C.t1` 5 -> `token.content.muted` / `secondary` / `primary`; `C.border` 12 ->
  // `token.line.default`; `C.cyan` 8 -> `token.brand.base`; `C.bg3` 5, `C.bg0` 1, `C.bg2` 1 ->
  // `token.surface.inset` / `canvas` / `raised`. No `C.space.*`, `C.radius.*`, `C.shadow*`,
  // `C.glow.*` or `C.gradient.*` read here, and no `no-colour-literals` entry moved.
  'pages/AuthPage.jsx': 0,
  // 56 -> 0 at task 27.2, batch 5a. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Inp` stays on it, as a component. `token` is read from `design/tokens.js`.
  // Every one of the 56 is the value the shim already gave it. By shim name: `C.cyan` 12 ->
  // `token.brand.base`; `C.t1` 9, `C.t2` 8, `C.t3` 6 -> `token.content.primary` / `secondary` /
  // `muted`; `C.border` 8 -> `token.line.default`; `C.green` 5 -> `token.status.profit.fg`;
  // `C.bg3` 4, `C.bg1` 2, `C.bg0` 1, `C.bg2` 1 -> `token.surface.inset` / `panel` / `canvas` /
  // `raised`. No `C.space.*`, `C.radius.*`, `C.shadow*`, `C.glow.*` or `C.gradient.*` read
  // here, and no `no-colour-literals` entry moved.
  'pages/Wizard.jsx': 0,
  // 38 -> 0 at task 27.2, batch 4. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Tag2` and `PanelTitle` stay on it, as components. `token` is read from
  // `design/tokens.js`. Every one of the 38 is the value the shim already gave it.
  //
  // By shim name: `C.t3` 12, `C.t1` 9, `C.t2` 1 -> `token.content.muted` / `primary` /
  // `secondary`; `C.bg3` 7 -> `token.surface.inset`; `C.cyan` 3 -> `token.brand.base`;
  // `C.warning` 2 -> `token.status.warning.fg`; `C.border` 2 -> `token.line.default`;
  // `C.green` 1 -> `token.status.profit.fg`; `C.red` 1 -> `token.status.loss.fg`. No
  // `C.space.*`, `C.radius.*`, `C.shadow*`, `C.glow.*` or `C.gradient.*` read here, and no
  // `no-colour-literals` entry moved.
  'components/DeploymentConsole.jsx': 0,
  // 36 -> 0 at task 27.2, batch 1. The `C` import is deleted, not narrowed: this page
  // imported `C` and nothing else from the shim, so the specifier and the module both go
  // and `token` is read straight from `design/tokens.js`. Every one of the 36 is the value
  // the shim already gave it, and no colour literal was introduced, so this page's three
  // `no-colour-literals` entries are untouched.
  //
  // By shim name: `C.accent` 14 (the eleven uppercase section headings, the Shield glyph,
  // the active tab's `${C.accent}15` wash and its 2px rule) -> `token.brand.base`; `C.t1`
  // 7 (the four tab bodies' base colour, the page `<h1>`, the active tab label, the back
  // button's hover) -> `token.content.primary`; `C.t2` 3 -> `token.content.secondary`;
  // `C.t3` 1 (the inactive tab label) -> `token.content.muted`; `C.loss` 1 (the risk
  // tab's high-risk heading) -> `token.status.loss.fg`; `C.border` 4 (the card rule, the
  // back button's, the tab strip's, the content panel's `${C.border}80`) ->
  // `token.line.default`; `C.bg3` 2 and `C.bg4` 1 -> `token.surface.inset`.
  //
  // THE THREE NUMERIC `C.radius.*` READS IN THE TREE ARE ALL ON THIS PAGE, and all three
  // are a plain `borderRadius`, so they take the token STRING safely: `C.radius.xl` was
  // the number 12 (`legacyPx('12px')`) and `token.radius.xl` is `'12px'`, which React
  // writes identically; likewise `md` (6 -> `'6px'`) and `lg` (8 -> `'8px'`). No
  // arithmetic reads either of them, so the `Math.round(parseFloat(...) * 16)` projection
  // `pages/PaperTrading.jsx`'s `PAGE_PADDING` needed is not needed here.
  //
  // THE BACK BUTTON'S HOVER IS A NO-OP AND STAYS ONE. `onMouseEnter` set the background
  // to `C.bg4` where `onMouseLeave` set it to `C.bg3`, and the shim collapsed both onto
  // `token.surface.inset` — so the hover has changed only the label colour since M1. Both
  // handlers now say `token.surface.inset` in as many words. Repairing it would be a
  // design decision; this is a retoken.
  'pages/LegalPage.jsx': 0,
  // 31 -> 0 at task 27.2, batch 5b. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Spinner` stays on it, as a component. `token` is read from
  // `design/tokens.js`. Every one of the 31 is the value the shim already gave it. By shim
  // name: `C.cyan` 7, `C.blue` 1 -> `token.brand.base` (the shim held both names as the one
  // brand hue, so the launcher's `linear-gradient(135deg, …)` was already a flat fill);
  // `C.border` 5 -> `token.line.default`; `C.t1` 3, `C.t2` 3, `C.t3` 3, `C.t4` 2 ->
  // `token.content.primary` / `secondary` / `muted` (`t3` and `t4` collapse, as on every page
  // that read both); `C.bg3` 2, `C.bg4` 2, `C.bg2` 2, `C.bg1` 1 -> `token.surface.inset` /
  // `raised` / `panel`.
  'components/CopilotChat.jsx': 0,
  // 30 -> 0 at task 27.2, batch 1, and the `C` import goes with them for the same reason
  // `LegalPage.jsx`'s did. By shim name: `C.t1` 2, `C.t2` 3, `C.t3` 4, `C.t4` 1 ->
  // `token.content.primary` / `secondary` / `muted` (`C.t3` and `C.t4` collapse onto
  // `content.muted`, as they do on every page that read both — here `C.t4` was the Reset
  // Authenticator link and `C.t3` its Back-to-QR sibling, which have always been the same
  // grey); `C.cyan` 6 ->
  // `token.brand.base`; `C.green` 1 (the copied-secret tick) ->
  // `token.status.profit.fg`; `C.border` 5 -> `token.line.default`; `C.bg0` 1, `C.bg1` 1,
  // `C.bg2` 1, `C.bg3` 3, `C.bg4` 2 -> `token.surface.canvas` / `panel` / `raised` /
  // `inset` (`bg3` and `bg4` are one surface in the shim and stay one here).
  //
  // THIS PAGE'S 12 `no-colour-literals` ENTRIES ARE UNTOUCHED: the ShieldCheck halo's two
  // hand-mixed cyans, the error and success alerts' four washes and four hex text/glyph
  // colours, and the QR plate's `#ffffff` with its shadow. All twelve are hand-mixed rgba
  // and hex that predate the shim; none of them was a `C.` read, so none of them is this
  // batch's to take, and the count is the same 12 before and after.
  'pages/TwoFA.jsx': 0,
  // Re-pointed onto the token type scale by task 3.1.
  // 27 -> 0 at task 27.2, batch 1. Presentation-only component, one `C` import, deleted.
  // By shim name: `C.t1` 1 (`STATUS_COLOUR`'s SUCCESS arm), `C.t2` 6, `C.t3` 11 ->
  // `token.content.primary` / `secondary` / `muted`; `C.red` 4 (the FAIL status, the
  // execution error, the blocking failure, the per-run failure list) ->
  // `token.status.loss.fg`; `C.gold` 2 (the PENDING status, the recorded numeric
  // conditions) -> `token.status.warning.fg`; `C.border` 3 (the two section rules and the
  // execution panel's left rail) -> `token.line.default`.
  //
  // `STATUS_COLOUR` KEEPS ITS SHAPE AND ITS FOUR HUES. It is already a frozen
  // `state -> colour` map of the four `TRACE_STATUSES`, which is the shape 24.4's follow-up
  // established for the builder's status cells, so there was nothing to restructure — only
  // the source of each hue moved. It is deliberately NOT repointed onto
  // `design/semantic.js`'s `statusToken`: `SUCCESS` reads `content.primary` here rather
  // than the profit hue (a trace that ran is not a trade that made money), and
  // `NOT_EXECUTED` reads `content.muted`, neither of which `statusToken` would pick.
  // Preserved, not chosen.
  //
  // NO `no-colour-literals` ENTRY, BEFORE OR AFTER. `components/builder/` IS scanned by
  // that guard, so this file being absent from its budget is a live claim rather than an
  // omission, and it still holds: every value here is a `token.*` read.
  'components/builder/NodeTrace.jsx': 0,
  // 24 -> 0 at task 27.2, batch 1, on the same terms as its sibling above — one `C`
  // import, deleted, and no `no-colour-literals` entry before or after. By shim name:
  // `C.t1` 2 (a present numeric cell, a present scalar), `C.t2` 1 (the column chips'
  // label), `C.t3` 15 -> `token.content.primary` / `secondary` / `muted`; `C.gold` 3 (the truncated-sample
  // note, an unrenderable output's message, the engine's recorded conditions) ->
  // `token.status.warning.fg`; `C.red` 1 (the classified failure) ->
  // `token.status.loss.fg`; `C.border` 2 (the section rule, the column chips) ->
  // `token.line.default`.
  //
  // THE EMPTY-VALUE PAIR IS THE ONE DECISION AND IT IS UNCHANGED. `ValueCell` and the
  // scalar both read `cell.empty ? C.t3 : C.t1`, now `token.content.muted` :
  // `token.content.primary` — the same two greys, the same condition, the same operand
  // order. An empty bar is not a status, so it takes no `statusToken` group; the `aria-label`
  // and `EMPTY_VALUE_TEXT` are what actually report it, and neither moved.
  'components/builder/NodePreview.jsx': 0,
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
  // 23 -> 0 at task 27.2, batch 1. One `C` import, deleted. By shim name: `C.accent` 6
  // (the progress rail's fill, the active step's disc and its glow, the STEP n-of-5
  // micro-label, the Complete Step button and its `onMouseLeave` restore) and
  // `C.accentHover` 1 (that button's `onMouseEnter`) -> `token.brand.base` /
  // `token.brand.hover`; `C.t1` 3, `C.t2` 2, `C.t3` 3 -> `token.content.primary` /
  // `secondary` / `muted`; `C.bg` 1, `C.bg2` 2, `C.bg3` 2 -> `token.surface.canvas` /
  // `raised` / `inset`; `C.border` 1 -> `token.line.default`; `C.borderLight` 1 ->
  // `token.line.strong` (the active-step panel's deliberately brighter rule, kept as
  // `line.strong` rather than flattened onto `line.default` — the same brighter-rule case
  // `pages/StrategyBuilder.jsx`'s retry button records).
  //
  // THE 23rd WAS `C.success`, WHICH THE SHIM DOES NOT DECLARE, so it has always evaluated
  // to `undefined` and a completed step has never had a fill — it shows the card surface
  // through, and the `CheckCircle` glyph is what distinguishes it. The ternary now says
  // `isCompleted ? undefined : …` in as many words, with the reason on the line. Giving it
  // a hue would be a design decision and a visible change; this is a retoken, so it is
  // preserved and recorded instead. It is also why the guard counted 23 rather than 22:
  // `findLegacyC` measures member ACCESSES on the shim, not resolved values, so a read of
  // a key the shim never had is a reference like any other and cleared like any other.
  //
  // THIS FILE'S 3 `no-colour-literals` ENTRIES ARE UNTOUCHED — the card's
  // `rgba(0,0,0,0.3)` drop shadow and the two `#fff` glyph colours (the active/completed
  // disc, the Complete Step button). None of the three was a `C.` read, so none of them is
  // this batch's to take, and the count is the same 3 before and after.
  'components/FirstTradeWizard.jsx': 0,
  // Cleared by task 8.6: rebuilt against `shell/navigation.js`, with colour taken from
  // `cssVar()` and token utility classes instead of the shim.
  'components/Sidebar.jsx': 0,
  // 21 -> 0 at task 27.2, batch 5b. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `SectionH`, `PanelTitle` and `Tag2` stay on it, as components. `token` is
  // read from `design/tokens.js`. Every one of the 21 is the value the shim already gave it.
  // By shim name: `C.t3` 7, `C.t2` 2, `C.t1` 2 -> `token.content.muted` / `secondary` /
  // `primary`; `C.border` 3 -> `token.line.default`; `C.cyan` 2 -> `token.brand.base`;
  // `C.red` 2 -> `token.status.loss.fg`; `C.green` 2 -> `token.status.profit.fg`; `C.purple` 1
  // -> `token.status.neutral.fg`. The two state ternaries — the failed-attempts summary card
  // and the failed-event row — keep condition and operand order; only the hue's source moved.
  'pages/SecurityLogs.jsx': 0,
  // The `AppShell` wrapper and the auth gates live here (task 3.3 touches it).
  // 14 -> 13 at task 8.5: the shell's `return` became a CSS grid on
  // `bg-surface-canvas`, which retired the wrapper's `background: C.bg0`. The
  // remaining 13 are all in `UpdatePasswordPage` (7) and `AdminGuard` (6) — both
  // outside task 8.5's scope, which is why `C` is still imported here.
  // Task 8.5's second half (the two route `Suspense` fallbacks, the narrowed
  // `'navigate'` bridge, the deleted `TENANT_ID`) moved all 13 down by 109 lines
  // without touching one of them, so this number is unchanged and only the spot
  // check's line array in `legacy-c-budget.test.js` moved.
  //
  // 13 -> 0 at task 27.2, batch 5b. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Inp`, `ToastContainer` and `LoadingProvider` stay on it. `token` is read
  // from `design/tokens.js`. Routing, the auth gates, `AdminGuard` and the toast `aria-live`
  // region are untouched. By shim name: `C.bg0` 3, `C.bg2` 1 -> `token.surface.canvas` /
  // `raised`; `C.t1` 2, `C.t3` 2 -> `token.content.primary` / `muted`; `C.green` 2 ->
  // `token.status.profit.fg`; `C.red` 2 -> `token.status.loss.fg`; `C.border` 1 ->
  // `token.line.default`. Every one is the value the shim already gave it. The spot check's
  // hand-enumeration is retired in the same commit and kept there as history.
  'App.jsx': 0,
  // Cleared by task 8.7: rebuilt against `shell/navigation.js` and `ds/`, with every
  // colour coming from a token utility class. The `LiveStatusV2` import went with the
  // hardcoded `status="running"` (§1.3), and `C` went with it.
  'components/TopBar.jsx': 0,
  // `components/DesktopOnlyOverlay.jsx: 9` stood here until task 27.3 deleted the file.
  // Removed rather than lowered to `0`, for the reason given at
  // `components/DashboardUpgrades.jsx` above: a deleted file has no source to measure and
  // `names only files that still exist` fails on an entry pointing at nothing.
  //
  // `shell/ResponsiveGate.jsx` (task 8.4) superseded the component and task 8.5 rewired
  // `App.jsx` onto it, so nothing had imported the overlay for the whole of M4-M9. The 9
  // were `C.bg2` and `C.bg3` on the card and its inner box, `C.border` twice, `C.cyan`
  // twice on the icon row and the "Minimum width: 1000px" line, and `C.t1` / `C.t2` /
  // `C.t3` on the heading and the two paragraphs — a whole surface's palette, none of it
  // reachable. So this is 9 fewer call sites standing between here and task 27.2, taken
  // by deleting dead code rather than by migrating anything.
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
  // 7 -> 0 at task 27.2, batch 5a. The `C` specifier is NARROWED out of the import, not the
  // import deleted: `Inp` stays on it, as a component. `token` is read from `design/tokens.js`.
  // Every one of the 7 is the value the shim already gave it. By shim name: `C.green` 2 ->
  // `token.status.profit.fg`; `C.t1` 1 -> `token.content.primary`; `C.red` 1 ->
  // `token.status.loss.fg`; `C.bg0` 1, `C.bg2` 1 -> `token.surface.canvas` / `raised`;
  // `C.border` 1 -> `token.line.default`. No `C.space.*`, `C.radius.*`, `C.shadow*`,
  // `C.glow.*` or `C.gradient.*` read here, and no `no-colour-literals` entry moved.
  'pages/UpdatePasswordPage.jsx': 0,
  // 6 -> 0 at task 27.2, batch 5b, the last of the shim's consumers. The `C` specifier is
  // NARROWED out of the import, not the import deleted: `SectionH`, `PanelTitle`, `Inp`,
  // `Toast`, `ToastContainer`, `ProgressBar` and `RiskMeter` all stay on it, as components.
  // `token` is read from `design/tokens.js`. All 6 sit in `marginData`, three in its
  // `useState` seed and the same three in the socket update that replaces it: `C.green` 2 ->
  // `token.status.profit.fg`; `C.cyan` 2 -> `token.brand.base`; `C.orange` 2 ->
  // `token.status.warning.fg` (the shim held `orange` and `gold` as the one amber). Every one
  // is the value the shim already gave it, so the three meters render unchanged.
  'pages/RiskSettings.jsx': 0,
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
  // New at task 26.1 — §7.9's 7 -> 4 subscription-state collapse, declared once
  // (Requirements 13.1, 13.3). A pure mapping module: no JSX, no import of the shim, and
  // the one hue it names is asked of `design/semantic.js`'s `statusToken` with the semantic
  // state word §7.9's table declares, so the badge cannot end up a colour the design
  // document did not pick. Entered at `0` and held there for the reason
  // `lib/drawdownSeries.js: 0` is — a file with no entry is a file whose cleanliness
  // nothing is holding.
  //
  // NOTE that this file gets NO `no-colour-literals` entry. That guard's `SCAN_ROOTS` is
  // `['pages', 'components', 'lib']` — widened to include `lib` at task 23.3 and NOT
  // widened to `design`, where `design/tokens.js` is named a token-layer file and excluded
  // outright. An entry for a `design/` file would be a stray and that guard's
  // "no strays" assertion would fail on it. This budget is the one that covers all of
  // `src/`, so it is the one that holds this file clean.
  'design/subscriptionState.js': 0,
});

/**
 * The shim this budget measures. When this file is gone, the guard is finished:
 * delete `legacy-c-budget.test.js` and this file (task 27.2). The test asserts
 * the shim still exists so that its removal fails loudly here rather than
 * leaving a guard that measures nothing and passes forever.
 */
export const SHIM_PATH = 'components/ui-legacy/primitives.jsx';
