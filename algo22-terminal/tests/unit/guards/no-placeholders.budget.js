/**
 * The `no-placeholders` allowlist — vyomquant-ui-redesign task 10.12.
 * Requirement 19.4. design.md §1.9, §7.3, §15.1.
 *
 * ---------------------------------------------------------------------------
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * A checked-in count, per file, of how many placeholders that file is still
 * allowed to contain. `no-placeholders.test.js` measures the real counts and
 * asserts each entry matches **exactly**:
 *
 *   * over entry  -> a new placeholder was introduced. Fix it.
 *   * under entry -> placeholders were removed and this file was not updated.
 *                    Lower the number here, in the same commit.
 *
 * Asserting exactly, in both directions, is the same rule the two sibling
 * budgets use and it is there for the same reason: under a `<=` assertion a
 * contributor could clear a file, leave the number where it was, and the next
 * contributor could put the placeholder back with nothing objecting. Progress has
 * to be *recorded* to count.
 *
 * ---------------------------------------------------------------------------
 * HOW IT DIFFERS FROM THE TWO BUDGETS IT COPIES ITS SHAPE FROM
 * ---------------------------------------------------------------------------
 * `no-colour-literals.budget.js` and `legacy-c.budget.js` are per-file ledgers:
 * nearly every file in the scan has an entry, because nearly every file has
 * colour in it, and the entries come down as the migration proceeds. This file
 * is an ALLOWLIST. The default for every one of the 136 scanned files is zero,
 * listed or not, and the test's "accounts for every file that carries a
 * placeholder" is what enforces that default. The list below is short because
 * the tree is nearly clean, not because the guard is narrow.
 *
 * Two consequences of the allowlist form, both asserted:
 *
 *   * An out-of-scope entry may never read `0`. Zero is what an ABSENT entry
 *     already means, so a `0` here would be an allowance nobody needs — and one
 *     a placeholder could creep back into. When an entry reaches zero it is
 *     deleted, not kept. (The in-scope group is the opposite case: there, `0` is
 *     the rule itself, and the entry exists to state which files the rule
 *     ranges over.)
 *   * There is no ratchet narrative here and no migration scheduled to lower
 *     these numbers. Requirement 19.4 has no phased admission the way
 *     Requirement 1.3 has design.md §14.4's; the resting state is zero
 *     everywhere and the entries below are debts in files this spec does not
 *     rebuild.
 *
 * ---------------------------------------------------------------------------
 * MEASURED FROM THE TREE ON THE DAY TASK 10.12 LANDED
 * ---------------------------------------------------------------------------
 * One file, one placeholder, in `src/pages/**`, `src/components/**` and
 * `src/lib/**` together — 136 files scanned, tests and the token layer excluded.
 * Measured by the guard's own `findPlaceholders`, not estimated.
 *
 * That is a real result and worth stating plainly: **no in-scope page carries a
 * placeholder.** design.md §1.9's audit found exactly two Requirement 19.4
 * violations and both are already resolved — task 27.1 removed
 * `pages/StrategyDetail.jsx`'s `Audit history coming soon` tab per §7.3's
 * decision, and task 8.6 rebuilt `components/Sidebar.jsx` against
 * `shell/navigation.js`, which took the external `docs` target out of the
 * primary nav. So this guard is not cleaning anything up. It is closing the door
 * behind work that is already done, which is exactly what task 28's "all eight
 * structural guards read clean" needs it for.
 *
 * ---------------------------------------------------------------------------
 * WHAT WAS SEARCHED FOR AND DID NOT APPEAR
 * ---------------------------------------------------------------------------
 * Recorded because a one-entry allowlist looks like a guard that is not looking
 * hard enough, and the way to answer that is to say what was looked for:
 *
 *   * `TODO` and `FIXME` — zero across the whole of `src/`, comments included,
 *     not merely zero in the three scanned roots. The project already keeps this
 *     standard; rule 2 records it.
 *   * `under construction`, `not implemented`, `not yet implemented`, `to be
 *     implemented`, `work in progress`, `stay tuned`, `lorem ipsum` — zero in
 *     rendered copy. `components/shell/AccountMenu.jsx:53` contains the words
 *     "has not implemented" in a comment explaining a refusal, which is a
 *     documented non-match; see the test's rule 1 comment cases.
 *   * An empty arrow-function handler, and an `href` or `to` of exactly `"#"` —
 *     zero. Worth knowing for §1.9's second finding: the `docs` nav entry was a
 *     `window.open` to an external host, not a dead `#`, so rule 3 would not
 *     have caught it even before task 8.6. `shell/navigation.js` and task
 *     10.13's Property 37 are what hold that shape now.
 */

/**
 * The eleven pages design.md gives a page task in M6-M9 — the set Requirement 19.4's
 * "any in-scope page" names. Every entry here must be `0`, and the test asserts both that
 * the committed number is `0` and that the file measures `0`.
 *
 * Unlike the other two budgets, a `0` here is not a record of a literal that was removed;
 * ten of the eleven pages never carried a placeholder at all. The entries exist so the
 * emptiness assertion has a declared set to range over, and so that a page cannot be
 * quietly dropped from it — `IN_SCOPE_PAGES` in the test compares against the spec's list.
 *
 * `pages/StrategyDetail.jsx` is the one entry with a history: §1.9 found
 * `Audit history coming soon` at line 1179 and §7.3 chose removal over wiring the tab up,
 * because Signal Trace already owns the per-strategy signal / order / execution record and
 * serves it from `GET /api/signal-trace/signals?strategy_id=`. Task 27.1 removed it and left
 * the reasoning in a comment at the end of the file, which is the case rule 1's comment
 * stripping exists for — that note describes "one centred line of placeholder text" and must
 * not be counted as one.
 *
 * Paths are relative to `src/`, forward-slashed, matching the spec's notation.
 */
export const IN_SCOPE_PLACEHOLDER_BUDGET = Object.freeze({
  'pages/Dashboard.jsx': 0,
  'pages/Portfolio.jsx': 0,
  'pages/Strategies.jsx': 0,
  'pages/TradeHistory.jsx': 0,
  'pages/LiveTrading.jsx': 0,
  'pages/SignalTrace.jsx': 0,
  'pages/Backtester.jsx': 0,
  'pages/StrategyBuilder.jsx': 0,
  'pages/PaperTrading.jsx': 0,
  'pages/StrategyMarketplace.jsx': 0,
  // The one page §1.9 named. Clean since task 27.1 — see the note above.
  'pages/StrategyDetail.jsx': 0,

  // -- Shared surfaces an in-scope task built or cleared --------------------
  // Not pages, so not on design.md's M6-M9 list, but each is rendered BY one of the pages
  // above, which means a placeholder in one of them is a placeholder on those pages. They
  // are governed by the emptiness rule rather than left to the allowlist's default for the
  // same reason `no-colour-literals` moved them into its in-scope group at task 27.3: an
  // absent entry states nothing, and these are the files where the next "coming soon" would
  // be cheapest to add and most expensive to ship.
  //
  // `components/Sidebar.jsx` is §1.9's SECOND finding. The `docs` entry opened
  // `https://docs.algo22.io` from the primary nav — a stale brand and an external target
  // presented as navigation. Task 8.6 rebuilt the file against `shell/navigation.js`, so the
  // nav no longer mixes `window.open` into a route list. Held at `0` here because the
  // sidebar is where a dead entry would reach every page at once.
  'components/Sidebar.jsx': 0,
  'components/TopBar.jsx': 0,
  // Created by task 20.2 and rendered by both `pages/LiveTrading.jsx` and, from task 25.2,
  // `pages/PaperTrading.jsx` — so one placeholder here is one on two in-scope pages.
  'components/trading/PositionsPanel.jsx': 0,
  'components/trading/OrdersPanel.jsx': 0,
  'components/trading/ExecutionsPanel.jsx': 0,
  // Cleared by task 21.6 and rendered by `pages/SignalTrace.jsx`. This is the file whose own
  // header documents the retired seven-stage map, which is the kind of prose rule 1 must not
  // punish.
  'components/SignalTraceVisualization.jsx': 0,
  // Rewritten by task 6.25. The one surface that renders when a page has failed, so a
  // marker left in it would be read by a trader at the worst moment.
  'components/ErrorBoundary.jsx': 0,
  // The four sentences `pages/SignalTrace.jsx` renders for a stage that will never occur —
  // "not available rather than pending, which would claim it is still coming". Held at `0`
  // because this module is Requirement 19.3's remedy written down, and it is one word away
  // from rule 1's first pattern; see the test's not-available cases.
  'lib/signalTraceStages.js': 0,
});

/**
 * Everything else that carries a placeholder. Allowed so it cannot grow, with no promise in
 * this spec that it shrinks, and no emptiness assertion over it.
 *
 * An entry here may not read `0` — see the header. It is deleted when the file is cleaned or
 * removed.
 */
export const OUT_OF_SCOPE_PLACEHOLDER_BUDGET = Object.freeze({
  // The file task 10.12 names as the reason this guard is scoped rather than global: "an
  // out-of-scope landing component". One match, the chip reading `Screenshot Coming Soon`.
  //
  // Its docblock's "placeholder for actual app screenshots" is not a second match — rule 1
  // does not carry the word `placeholder` at all, and the line is a comment besides. Nor is
  // its own name: the component is `ScreenshotComingSoon`, with no separator between the
  // words, and rule 1 requires one. So an import of it is not a placeholder on the importing
  // file, which is the right answer — although nothing imports it today. It is a dead
  // component rendering a dead promise, on the landing page, which index.css's own header and
  // `no-colour-literals.budget.js`'s last group both declare out of scope for v1.
  //
  // This entry is also the guard's LIVENESS FIXED POINT: the test asserts this file measures
  // exactly `['Coming Soon']`, so a scan that stopped reading the tree, or a rule 1 that
  // stopped matching, fails here instead of passing everything in silence. If this file is
  // ever deleted or cleaned, delete the entry — and move the fixed point to whatever real
  // placeholder remains, or, if none does, to a planted example.
  'components/landing/ScreenshotComingSoon.jsx': 1,
});

/**
 * The whole allowlist, and what the budget assertions in `no-placeholders.test.js` read.
 *
 * Spread rather than hand-maintained, so the flat map cannot fall out of step with the
 * groups. The test asserts the two groups are disjoint, which is what makes the spread
 * lossless: an entry declared in both would silently take the out-of-scope number here and
 * escape the in-scope emptiness rule.
 */
export const PLACEHOLDER_BUDGET = Object.freeze({
  ...IN_SCOPE_PLACEHOLDER_BUDGET,
  ...OUT_OF_SCOPE_PLACEHOLDER_BUDGET,
});

/**
 * The token layer. These files declare colour and spacing; they render no copy and hold no
 * controls, so there is nothing here for this guard to find. Named rather than merely
 * omitted, so the test can assert they are outside the scan and the decision stays visible —
 * the same three files `no-colour-literals.budget.js` excludes, for a different reason.
 *
 * None of the three is inside a scan root (`pages`, `components`, `lib`), so today this list
 * filters nothing. It becomes a filter the moment a root is widened over one of them, which
 * is the point: `src/design/**` is the root a future task is most likely to add, and
 * `design/tokens.js` is in it.
 */
export const TOKEN_LAYER_FILES = Object.freeze([
  'styles/tokens.css',
  'design/tokens.js',
  'index.css',
]);
