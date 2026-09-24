/**
 * `standing-prose` — the seeded budget. retail-ui-simplification task 12.1.
 * Requirements 7.1, 7.4, 7.5. design.md §5.5, Property 8.
 *
 * ===========================================================================
 * WHAT A NUMBER IN HERE IS, AND WHAT IT IS NOT
 * ===========================================================================
 * One entry per page, holding the number of characters of STANDING PROSE that
 * page renders today. Standing prose is defined mechanically in
 * `standing-prose.test.js` and the definition is design.md §5.5's: the text, in
 * document order, that renders BEFORE the first element carrying a
 * `data-region` attribute.
 *
 * The count is taken by RENDERING the page, never by scanning its source.
 * Requirement 7.5 is explicit about why: "IF the count falls without the
 * rendered output changing, THEN the clause is not satisfied". A source scan
 * cannot tell a sentence that renders from one sitting behind a closed
 * disclosure, and moving a sentence out of a `const` into inline JSX would take
 * a source count down without moving one character off the screen. So this is
 * the one guard in this directory that is not a `source-scan.js` guard.
 *
 * ===========================================================================
 * WHAT A FALLING NUMBER HERE MAY NEVER BE BOUGHT WITH
 * ===========================================================================
 * This budget is the instrument that will be used to JUSTIFY moving text, so
 * its scope is the safeguard, and the scope is narrow on purpose:
 *
 *   * A `design/pageFields.js` `reason` renders INSIDE a `data-region`, so it is
 *     never counted here and can never be "reduced" to satisfy this guard.
 *     `standing-prose.test.js` asserts that directly — no declared reason string
 *     appears in any page's standing prose — so the safeguard is checked rather
 *     than promised.
 *   * `usePanelState`'s `unavailable` copy renders inside the panel it is about,
 *     which is inside a region. Same protection, same reason.
 *   * Anything that renders in ONE STATE ONLY — an error state, a confirmation,
 *     a disclosure's body — is not standing prose. Standing prose is what a
 *     trader sees whether or not they asked for it.
 *
 * A reduction achieved by touching any of those is a Requirement 19.3 / 19.6
 * violation and this file is not evidence for it.
 *
 * ===========================================================================
 * THE FIXTURE IS THE ZERO-DATA ACCOUNT, AND THAT IS A DECISION
 * ===========================================================================
 * Every page is measured against a fresh account: reads answer 2xx with no rows.
 * Four of the eight pages carry no `data-region` node at all today
 * (`SignalTrace`, `TradeHistory`, `Strategies`, `StrategyMarketplace` — they are
 * the pages tasks 5-7 migrate), so on those the whole rendered text is standing
 * by the §5.5 definition. Measured against a fixture WITH rows, their numbers
 * would be a function of how many rows the fixture held rather than of the page,
 * and the ratchet would move every time a fixture was edited. Zero rows makes
 * the number a property of the page. It is also the same posture
 * `freshAccount.test.jsx` takes, so the two files describe one screen.
 *
 * ===========================================================================
 * A PAGE THAT REACHES ZERO KEEPS ITS ENTRY, AT 0
 * ===========================================================================
 * The two sibling guards disagree and each is right for its own subject:
 * `absolute-font-sizes` DELETES an entry that reaches zero (Requirement 1.5
 * says so, and a cleared file is then held at zero by the unbudgeted check),
 * while `no-colour-literals` KEEPS entries and asserts them exactly.
 *
 * This guard follows the colour guard and KEEPS the entry. Two reasons:
 *
 *   1. The key set is not "whatever the scan found" — it is Requirement 7.4's
 *      ENUMERATED eight pages. Deleting an entry would silently shorten a list
 *      an approved requirement fixes, and `names Requirement 7.4's eight pages`
 *      would then fail rather than the thing it is watching for.
 *   2. Requirement 7.1's target is 400 characters, not 0. There is no
 *      "cleared" state to graduate out of: a page at 0 standing prose is still a
 *      page whose prose can come back, and the only thing that can catch that
 *      coming back is an entry at 0 asserted in both directions.
 *
 * ===========================================================================
 * TASK 12.3 LANDED AND `LiveTrading` DID NOT MOVE. THAT IS THE CORRECT RESULT
 * ===========================================================================
 * Task 12.3 stated its own regression assertion as "`LiveTrading`'s rendered
 * count fails before it is lowered in this same commit and passes after". It
 * does not, it cannot, and the number below was deliberately left at 135 when
 * the task landed. The measurement, so the next reader does not re-derive it:
 *
 *   * §5.5 defines standing prose POSITIONALLY — the text before the first
 *     element carrying `data-region`. Nothing in that definition is about which
 *     constant a sentence came from.
 *   * `LiveTrading.jsx`'s first `data-region` in document order is
 *     `DEPLOYMENT_FIELD`, on the Deployments panel. Both caveats render INSIDE
 *     that panel, several nodes after the anchor.
 *   * So the 671 characters task 12.3 moved (`REGISTRY_CAVEAT` 384 and
 *     `SELECTION_CAVEAT` 287 — the plan's 242 for the second one is stale) were
 *     never part of the 135. Moving them behind a `ds/Accordion` changes what a
 *     trader reads and changes this number by zero.
 *
 * The plan's assertion was written against a definition this seed does not use:
 * "prose above the data" read as "panel-level prose", where §5.5 says "before the
 * first `data-region` node". Both are defensible readings of Requirement 7.1;
 * only one of them is the one the budget was seeded with, and §5.5 is an approved
 * document. Widening the definition here to make the assertion true would
 * re-seed a guard committed one commit earlier and silently move all eight
 * pages' numbers — the `absolute-font-sizes` failure mode, in a file whose whole
 * job is to be a ratchet. Raised as a finding; the definition is untouched.
 *
 * What that leaves is that a claim about one page's rendering is asserted where
 * it belongs: `liveTrading.test.jsx`'s four task-12.3 declarations pin the split
 * itself — the operative half unconditional, the explanation absent from the tree
 * while closed, the toggle named and reporting `aria-expanded`, and both
 * disclosures EXPANDED in the empty and partial states. Requirement 7.1's target
 * is 400 and `LiveTrading` was already inside it at 135 before the task ran, so
 * nothing about the budget was load-bearing for this work.
 *
 * Tasks 12.4 and 12.5 are in the same position for `SignalTrace` and
 * `Dashboard`'s panel prose. Expect their numbers not to move either.
 *
 * ===========================================================================
 * PROVENANCE OF THE SEED
 * ===========================================================================
 * Requirement 7.4 and design.md §1.7 seed this list from a SOURCE-CONSTANT
 * measurement — how many user-facing `const` strings each page declares and how
 * many characters they hold. That measurement is recorded below as
 * `SOURCE_CONSTANT_SEED`, because it is the number the requirement was written
 * against and the reason these eight pages are the eight.
 *
 * It is NOT what this budget holds. A rendered count and a source-constant count
 * measure different things and cannot agree:
 *
 *   * The rendered count includes text the page composes inline — headings,
 *     control labels, an eyebrow, a summary line — none of which is a `const`.
 *   * It excludes every declared constant that renders below the first region,
 *     in one state only, or not at all on a fresh account.
 *   * On the four pages with no `data-region` node it includes the page's whole
 *     text, empty-state copy included.
 *
 * The measured numbers are in `STANDING_PROSE_BUDGET`. Where they differ from
 * `SOURCE_CONSTANT_SEED`, the measured one is the one asserted — that is what
 * "verify by rendering" means, and the difference is the point rather than a
 * discrepancy to reconcile.
 *
 * EVERY ONE OF THE EIGHT DIFFERS. The rendered count against the §1.7 seed:
 *
 *   page                  §1.7 seed   rendered   why
 *   LiveTrading                3175        135   anchored high; the two caveats
 *                                                render inside the first region
 *   SignalTrace                1351        541   unanchored; whole page, no rows
 *   Dashboard                  1225        168   anchored above the alert band
 *   Portfolio                   774         91   anchored at the tier-1 container
 *   TradeHistory                584        816   unanchored; whole page, no rows
 *   Strategies                  420        656   unanchored; whole page, no rows
 *   StrategyMarketplace         186        498   unanchored; whole page, no rows
 *   Backtester                  157        101   anchored at the configuration column
 *
 * The four anchored pages measure LESS than their seed and the four unanchored
 * ones MORE, and both directions come from the same cause: §5.5 anchors the count
 * on the first `data-region` node, and whether a page has one at all is exactly
 * what cause 2 of requirements §1.4 is about. The four that measure more are the
 * four with no anchor.
 */

/**
 * design.md §1.7's source-constant measurement, exactly as Requirement 7.4 seeds
 * it. `constants` is the number of user-facing string constants the page
 * declares; `characters` is their total length.
 *
 * Kept for two jobs and no others: it records where the requirement's numbers
 * came from, and `names Requirement 7.4's eight pages` compares this key set
 * against the budget's so neither list can quietly shed a page.
 */
export const SOURCE_CONSTANT_SEED = Object.freeze({
  'pages/LiveTrading.jsx': Object.freeze({ constants: 29, characters: 3175 }),
  'pages/SignalTrace.jsx': Object.freeze({ constants: 14, characters: 1351 }),
  'pages/Dashboard.jsx': Object.freeze({ constants: 13, characters: 1225 }),
  'pages/Portfolio.jsx': Object.freeze({ constants: 5, characters: 774 }),
  'pages/TradeHistory.jsx': Object.freeze({ constants: 3, characters: 584 }),
  'pages/Strategies.jsx': Object.freeze({ constants: 7, characters: 420 }),
  'pages/StrategyMarketplace.jsx': Object.freeze({ constants: 3, characters: 186 }),
  'pages/Backtester.jsx': Object.freeze({ constants: 3, characters: 157 }),
});

/**
 * Requirement 7.1's threshold. 400 characters, "chosen, not derived" — the
 * requirement's own note says so. Nothing in this file enforces it as a ceiling:
 * five of the eight pages are already inside it and the three that are not are
 * what tasks 12.3-12.5 move. It is here so the distance to the target is
 * reportable from the guard rather than recalculated by hand.
 */
export const STANDING_PROSE_TARGET = 400;

/**
 * MEASURED, by rendering each page against a zero-data fixture on the tree at
 * retail-ui-simplification task 12.1. Asserted EXACTLY, in both directions:
 *
 *   over budget  — standing prose was ADDED. Move it behind a disclosure
 *                  (Requirement 7.2) rather than raising the number.
 *   under budget — standing prose was REMOVED and the number was left where it
 *                  was. Lower it in the same commit, or the headroom stays open
 *                  for the prose to creep back unnoticed (Requirement 22.2).
 *
 * `standing-prose.test.js` reports the page and the delta on either failure.
 */
export const STANDING_PROSE_BUDGET = Object.freeze({
  /*
   * ANCHORED — these four carry a `data-region` node, so the count is the text above it.
   *
   * On all four that is the `ds/PageHeader` band, because the first declared slot sits high
   * in the tree: `LiveTrading.jsx:2701`'s deployment panel, `Dashboard.jsx`'s alert band,
   * `Portfolio.jsx:1215`'s tier-1 container, `Backtester.jsx:1678`'s configuration column.
   * The prose these pages declare as constants renders INSIDE those regions and is
   * therefore outside this count — blind spot 1 in `standing-prose.test.js`'s header, which
   * is where the consequence for tasks 12.3-12.5 is recorded.
   */
  // UNCHANGED BY TASK 12.3, ON PURPOSE. The 671 characters that task moved behind a
  // `ds/Accordion` render inside the `data-region="deployment"` panel, so they were never in
  // this number. See the header section on it before concluding the task under-delivered.
  'pages/LiveTrading.jsx': 135,
  'pages/Dashboard.jsx': 168,
  'pages/Backtester.jsx': 101,
  'pages/Portfolio.jsx': 91,

  /*
   * UNANCHORED — no `data-region` anywhere in these four, so by §5.5's definition their
   * whole rendered text is standing. Measured on a zero-data account, so these are chrome,
   * prose and empty-state copy with no rows in them. Tasks 5-7 migrate all four, and each
   * one's first `data-region` will take most of its number away with it.
   */
  'pages/TradeHistory.jsx': 816,
  'pages/Strategies.jsx': 656,
  'pages/SignalTrace.jsx': 541,
  'pages/StrategyMarketplace.jsx': 498,
});

export default STANDING_PROSE_BUDGET;
