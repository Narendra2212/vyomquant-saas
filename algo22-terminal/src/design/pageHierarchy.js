/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/pageHierarchy.js — §7's priority tiers, as data
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 16.1. design.md §18's `PageHierarchy` / `TierField`,
 * §7.1, §7.4, §7.5, §7.6. Requirements 3.1, 3.2, 3.4, 6.2, 6.3, 7.1, 7.2, 7.3,
 * 10.1, 10.2. Property P4.
 *
 * §7 draws each page as an ASCII box with `TIER 1`, `TIER 2` and `TIER 3` bands, and the
 * requirements behind those bands are ordering claims: Requirement 10.1 asks for six
 * figures "as the highest-visual-priority elements", 10.2 asks for drawdown "alongside
 * the elements described in 10.1 rather than in a separate, lower-priority section", 3.4
 * asks for one row and not two. A box drawing cannot be asserted, so today those claims
 * are satisfied by whoever last edited the JSX remembering them — which is how drawdown
 * ended up below the summary row on Portfolio in the first place.
 *
 * This module is the same four box drawings as data. It is the sibling of
 * `design/pageFields.js`: that one declares WHERE each figure's value comes from and what
 * a page may render when there is none, this one declares WHERE ON THE PAGE it goes.
 *
 * WHAT A TIER IS, AND WHAT IT IS NOT
 * ----------------------------------
 * A tier is a claim about **document order and grouping**, not about type size. `ds/Metric`
 * already maps `tier` to `--text-figure` / `--text-title` / `--text-body`, and that mapping
 * is a rendering detail a designer may revisit. What may not be revisited without changing
 * a requirement is that every tier-*n* element precedes every tier-*(n+1)* element, and
 * that a page has exactly ONE tier-1 container — Property 4's two clauses.
 *
 * HOW A PAGE CONSUMES IT
 * ----------------------
 * By marking its containers, not by mapping over the list. A page renders its own JSX; what
 * it takes from here is the attribute pair that makes the region identifiable:
 *
 *     import { PAGES } from '../design/pageFields';
 *     import { TIER_PAGE_ATTRIBUTE, TIER_ATTRIBUTE } from '../design/pageHierarchy';
 *
 *     <div {...{ [TIER_PAGE_ATTRIBUTE]: PAGES.PORTFOLIO, [TIER_ATTRIBUTE]: 1 }}
 *          className="grid grid-cols-4 gap-4">
 *       ... every tier-1 figure, and nothing else ...
 *     </div>
 *
 * `ds/Metric` writes `data-metric-tier` on each figure it renders, so Property 4 (task
 * 16.3) can select every tier-1 figure on the page and assert each one is inside the single
 * `[data-page-tier="1"]` container. Neither attribute carries styling; both exist to make a
 * layout claim decidable from the rendered DOM rather than from a reviewer's memory.
 *
 * DATA, NOT FUNCTIONS
 * -------------------
 * Every export is a frozen value, for the reason `pageFields.js` gives: Property 4 has to
 * enumerate the tiers of a page it was handed and render a payload against them, and it can
 * only do that if the declaration is inert. The one exported function, {@link tierSelector},
 * composes a CSS selector out of the two attribute names and decides nothing.
 *
 * EVERY `key` IS A `pageFields` FIELD NAME — BY CONSTRUCTION
 * ---------------------------------------------------------
 * A tier entry's `key` is the `field` of the `pageFields.js` entry for the same page, and
 * its `label` is READ from that entry rather than retyped. Two declarations of one figure
 * that spell its label differently are two figures as far as a test is concerned, and the
 * cross-check that catches it is only possible if the names line up. The local builder
 * throws at import time for a `page`/`key` pair `pageFields` does not declare, so a typo is
 * a loud module-load failure rather than a tier that silently matches nothing.
 *
 * WHY SOME DECLARED FIELDS CARRY NO TIER
 * --------------------------------------
 * Four `pageFields` entries are deliberately untiered, and they are declared as such in
 * {@link UNTIERED_FIELDS_BY_PAGE} rather than left out silently — an omission and a decision
 * look identical in a filtered list. Each names its reason. The pattern in all four cases is
 * the same: the element renders ABOVE tier 1 (the Requirement 3.3 alert strip, the Live
 * Trading deployment selector) or is not a rendered element at all (a push channel, a
 * panel's read-state marker), and either way giving it a tier would make Property 4 assert
 * something false.
 *
 * WHAT IS NOT DECLARED HERE
 * -------------------------
 * The three pages with `pageFields` entries and no tiered layout in §7: Strategies (§7.2 is
 * one `DataTable` — its columns are a field list, which is Property 5's business, not a
 * priority hierarchy), Trade History (§7.7, the same) and Signal Trace (§10's nine stages
 * are an ordered sequence with its own property, P15). Page chrome — `PageHeader`, the
 * environment switch, `FilterBar`, an action row — is outside the hierarchy on every page:
 * it is not a figure and it is not what the requirements order.
 */

import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from './pageFields';

/** §18's `TierField.tier` domain. Three bands, and there is no tier 0 and no tier 4. */
export const TIERS = Object.freeze([1, 2, 3]);

/**
 * The attribute naming which page a tier container belongs to.
 *
 * Carried alongside the tier because Property 4 runs over a registry of pages and a
 * selector for "the tier-1 container" has to mean one element, not one per page mounted in
 * the same document.
 */
export const TIER_PAGE_ATTRIBUTE = 'data-page';

/** The attribute marking a tier container. Its value is the tier: `"1"`, `"2"`, `"3"`. */
export const TIER_ATTRIBUTE = 'data-page-tier';

/**
 * `ds/Metric`'s own per-figure attribute, named here so the property test and the pages
 * spell it once.
 *
 * It is not declared by this module — `ds/Metric` writes it whether or not anything reads
 * it — but Property 4's second clause ("no tier-1 element renders outside the page's single
 * tier-1 container") is decided by comparing the elements carrying THIS attribute against
 * the containers carrying {@link TIER_ATTRIBUTE}, so the two names belong in one place.
 */
export const METRIC_TIER_ATTRIBUTE = 'data-metric-tier';

/**
 * The CSS selector for one page's tier container, or for its tier-*n* figures.
 *
 * The only function this module exports. It composes the two attribute names above and
 * decides nothing about layout — it exists so a test, a page and a future page all spell
 * `[data-page="portfolio"][data-page-tier="1"]` identically.
 *
 * @param {string} page A `PAGES` value.
 * @param {1|2|3} tier
 * @returns {string}
 */
export const tierSelector = (page, tier) =>
  `[${TIER_PAGE_ATTRIBUTE}="${page}"][${TIER_ATTRIBUTE}="${tier}"]`;

/*
 * ── The entry shape ───────────────────────────────────────────────────────
 *
 * §18: `TierField = {tier, key, label}`. Exactly those three keys leave this module, and
 * `label` is not a parameter — it is read from the `pageFields` entry named by
 * `page`/`key`, so the label a tier declares and the label the page renders cannot drift.
 *
 * The throw is the cross-check, performed at import rather than in a test: a tier naming a
 * field that does not exist matches no element, and a property test over it passes for the
 * wrong reason.
 */
const tierFieldFor = (page) => (tier, key) => {
  const entry = PAGE_FIELD_BY_KEY[pageFieldKey({ page, field: key })];
  if (!entry) {
    throw new Error(
      `pageHierarchy: ${page} declares tier ${tier} field "${key}", which `
        + 'design/pageFields.js does not declare. Every tier key is a `pageFields` `field` '
        + 'name for the same page — add the field there first, with its source path and its '
        + 'verdict, or fix the spelling here.',
    );
  }
  if (!TIERS.includes(tier)) {
    throw new Error(
      `pageHierarchy: ${page}/${key} declares tier ${JSON.stringify(tier)}; §18 gives `
        + `${TIERS.join(', ')} and nothing else.`,
    );
  }
  return Object.freeze({ tier, key, label: entry.label });
};

/*
 * ── The untiered shape ────────────────────────────────────────────────────
 *
 * `{field, reason}`. Validated the same way, because "this field has no tier" is only a
 * meaningful statement about a field that exists.
 */
const untieredFor = (page) => (field, reason) => {
  const entry = PAGE_FIELD_BY_KEY[pageFieldKey({ page, field })];
  if (!entry) {
    throw new Error(
      `pageHierarchy: ${page} records "${field}" as untiered, but design/pageFields.js `
        + 'does not declare it. An untiered entry excludes a declared field from Property '
        + '4; there is nothing to exclude here.',
    );
  }
  return Object.freeze({ field, label: entry.label, reason });
};

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.6 Portfolio (Requirements 10.1, 10.2) — /app/portfolio
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ┌─ TIER 1 ── Total value │ Available │ Invested │ Unrealised P&L │ Realised P&L │
 * │            Total exposure │ Current drawdown
 * ├─ TIER 2 ── Open positions: summary above detail (Requirement 10.3)
 * └─ TIER 3 ── Allocation │ Equity curve │ P&L heatmap
 *
 * EIGHT tier-1 entries for the seven figures §7.6 draws, because "Realised P&L" is two
 * figures and always was: `today_realized_pnl` is today's window and BC-5's `realized_pnl`
 * is the lifetime sum, and `pageFields` labels them apart for the reason §7.6's table gives
 * — one of them scoped and the other not would be the same words over different quantities.
 *
 * `currentDrawdown` is IN TIER 1. That is the whole of Requirement 10.2 and the specific
 * thing task 16.1 fixes: it used to render below, in the risk region, where a figure a
 * trader reads to decide whether to cut size is a scroll away from the exposure it
 * qualifies.
 *
 * `positionLiquidationPrice` is a column of the tier-2 table rather than a region of its
 * own. It is declared because Property 4 asks where every declared field renders, and the
 * answer for a cell is "inside its table", which is a tier-2 answer.
 */
const portfolioTier = tierFieldFor(PAGES.PORTFOLIO);

const PORTFOLIO_HIERARCHY = Object.freeze({
  page: PAGES.PORTFOLIO,
  requirements: Object.freeze(['10.1', '10.2', '10.3']),
  tiers: Object.freeze([
    portfolioTier(1, 'totalValue'),
    portfolioTier(1, 'availableBalance'),
    portfolioTier(1, 'investedCapital'),
    portfolioTier(1, 'unrealisedPnl'),
    portfolioTier(1, 'realisedPnlToday'),
    portfolioTier(1, 'lifetimeRealizedPnl'),
    portfolioTier(1, 'totalExposure'),
    portfolioTier(1, 'currentDrawdown'),
    portfolioTier(2, 'openPositions'),
    portfolioTier(2, 'positionLiquidationPrice'),
    portfolioTier(3, 'allocation'),
    portfolioTier(3, 'equityCurve'),
    portfolioTier(3, 'heatmap'),
  ]),
  untiered: Object.freeze([]),
  note: 'Tier 1 is one container holding all eight figures — two rows of four tracks, one '
    + 'region. Requirement 10.1 orders the figures above everything else and 10.2 puts the '
    + 'drawdown among them, so a second container for "risk" would satisfy neither however '
    + 'it was placed.',
});

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.1 Dashboard (Requirements 3.1, 3.2, 3.4) — /app/dashboard
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ┌─ Alert strip (conditional, Requirement 3.3) — untiered, see below
 * ├─ TIER 1 ── Portfolio value │ Today's P&L │ Total P&L │ Current drawdown
 * ├─ TIER 2 ── Active strategies │ Open positions │ System & exchange health
 * └─ TIER 2 ── Recent signals & orders │ Equity curve
 *
 * Requirement 3.4 — "no second row of equally-weighted tier-1 metric cards" — is why this
 * page's tier 1 has exactly four entries and why task 19.5 asserts no payload can produce a
 * fifth. Note that this is the opposite constraint to Portfolio's: there, eight figures in
 * one container is what the requirement asks for. The tier is the grouping claim; the number
 * of rows inside it is a per-page requirement, not a property of tiers.
 *
 * §7.1's layout has no tier 3, and one is not invented here to fill the shape.
 */
const dashboardTier = tierFieldFor(PAGES.DASHBOARD);
const dashboardUntiered = untieredFor(PAGES.DASHBOARD);

const DASHBOARD_HIERARCHY = Object.freeze({
  page: PAGES.DASHBOARD,
  requirements: Object.freeze(['3.1', '3.2', '3.4']),
  tiers: Object.freeze([
    dashboardTier(1, 'portfolioValue'),
    dashboardTier(1, 'todayPnl'),
    dashboardTier(1, 'totalPnl'),
    dashboardTier(1, 'currentDrawdown'),
    dashboardTier(2, 'activeStrategyCount'),
    dashboardTier(2, 'openPositions'),
    dashboardTier(2, 'openPositionsCount'),
    dashboardTier(2, 'positionsDegraded'),
    dashboardTier(2, 'exchangeHealth'),
    dashboardTier(2, 'exchangeApiLatencyMs'),
    dashboardTier(2, 'orderStateSync'),
    dashboardTier(2, 'recentSignals'),
    dashboardTier(2, 'recentOrders'),
    dashboardTier(2, 'equityCurve'),
  ]),
  untiered: Object.freeze([
    dashboardUntiered(
      'alertCondition',
      'Requirement 3.3 puts the alert strip ABOVE tier 1, so a tier would make Property 4 '
        + 'false whichever number it was given: tier 2 precedes tier 1 in document order, '
        + 'and tier 1 would put a conditional full-width strip inside the single four-figure '
        + 'container Requirement 3.4 is about. It is page chrome, like the header.',
    ),
  ]),
  note: 'Tier 1 is a single `grid-template-columns: repeat(4, 1fr)` container and `Metric '
    + 'tier={1}` appears nowhere else on the page, which is how §7.1 satisfies Requirement '
    + '3.4 structurally rather than by review.',
});

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.5 Live Trading (Requirements 7.1, 7.2, 7.3) — /app/live-trading
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ┌─ Deployment selector — untiered, see below
 * ├─ TIER 1 ── Connection │ Exchange │ Account │ Strategy (v) │ Market │ Environment
 * ├─ TIER 2 ── Position │ Entry │ Mark │ Unrealised P&L │ Realised P&L │ Exposure │ Risk
 * └─ TIER 3 ── Latest signal │ Latest order │ Execution status
 *
 * The three tiers are PER SELECTED DEPLOYMENT, which is why the selector above them carries
 * no tier: it chooses which deployment the tiers describe. Requirement 7.1's row answers "is
 * it actually running?", 7.2's "what is the position doing?", 7.3's "what happened last?" —
 * an order that is the requirement, not a preference.
 *
 * `realisedPnlPerDeployment` is tier 2 and is permanently not-available: `pageFields` has it
 * as ❌ because `overview.today_realized_pnl` is account-wide. It is declared in the tier so
 * the marker has a place in the row — Requirement 19.3's state is a rendered element and
 * Property 4 orders it like any other.
 */
const liveTradingTier = tierFieldFor(PAGES.LIVE_TRADING);
const liveTradingUntiered = untieredFor(PAGES.LIVE_TRADING);

const LIVE_TRADING_HIERARCHY = Object.freeze({
  page: PAGES.LIVE_TRADING,
  requirements: Object.freeze(['7.1', '7.2', '7.3']),
  tiers: Object.freeze([
    liveTradingTier(1, 'connectionState'),
    liveTradingTier(1, 'exchange'),
    liveTradingTier(1, 'account'),
    liveTradingTier(1, 'strategy'),
    liveTradingTier(1, 'market'),
    liveTradingTier(1, 'tradingEnvironment'),
    liveTradingTier(2, 'position'),
    liveTradingTier(2, 'unrealisedPnl'),
    liveTradingTier(2, 'realisedPnlAccount'),
    liveTradingTier(2, 'realisedPnlPerDeployment'),
    liveTradingTier(2, 'exposure'),
    liveTradingTier(2, 'riskState'),
    liveTradingTier(2, 'liquidationDistance'),
    liveTradingTier(3, 'latestSignal'),
    liveTradingTier(3, 'latestOrder'),
    liveTradingTier(3, 'executionStatus'),
  ]),
  untiered: Object.freeze([
    liveTradingUntiered(
      'deployment',
      'The selector ABOVE tier 1. It is a control that chooses which deployment the three '
        + 'tiers describe, not one of the figures they order.',
    ),
    liveTradingUntiered(
      'deploymentStopped',
      'A `STRATEGY_STATUS` push (Requirement 7.5), not a figure with a place in the layout. '
        + 'It changes what tier 1 and tier 2 report; it renders no element of its own.',
    ),
  ]),
  note: 'Requirement 7.4 is enforced by `ds/Panel`, not by this declaration: a panel '
    + 'declaring `money` without an `environment` throws in development, so a tier-2 panel '
    + 'cannot ship without its badge.',
});

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * §7.4 Backtester (Requirements 6.2, 6.3) — /app/backtest
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ┌─ TIER 1 ── Total return │ Net P&L │ Max drawdown │ Sharpe │ Win rate │ Trades
 * ├─ TIER 2 ── Equity curve │ Drawdown curve
 * └─ TIER 3 ── Tabs: Trades │ Monthly returns │ Extended statistics (collapsed, Req 6.4)
 *
 * THE TIERS DESCRIBE THE RESULT REGION ONLY. §7.4's configuration form — strategy, market
 * and data, period, capital and risk, then the run action — precedes all three, and its
 * order is Requirement 6.1's separate contract. Task 23.5 registers this page against
 * Property 4 over the result region; a property run that included the form would be
 * asserting 6.1 with 6.2's mechanism.
 *
 * Requirement 6.6 is why every tier-1 entry here is absent-able: a run that failed part-way
 * renders NO tier-1 figures rather than six zeros, so the tier can be empty and the ordering
 * claim still has to hold.
 */
const backtesterTier = tierFieldFor(PAGES.BACKTESTER);

const BACKTESTER_HIERARCHY = Object.freeze({
  page: PAGES.BACKTESTER,
  requirements: Object.freeze(['6.2', '6.3', '6.4']),
  tiers: Object.freeze([
    backtesterTier(1, 'totalReturn'),
    backtesterTier(1, 'netPnl'),
    backtesterTier(1, 'maxDrawdown'),
    backtesterTier(1, 'sharpe'),
    backtesterTier(1, 'winRate'),
    backtesterTier(1, 'tradeCount'),
    backtesterTier(2, 'equityCurve'),
    backtesterTier(2, 'drawdownCurve'),
    backtesterTier(3, 'tradeList'),
    backtesterTier(3, 'fees'),
  ]),
  untiered: Object.freeze([]),
  note: 'Tier 3 is the collapsed `Tabs` block (Requirement 6.4). Collapsed is a disclosure '
    + 'state, not an absence: the tab list itself is rendered and ordered, which is what '
    + 'Property 4 sees.',
});

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * The declaration, and the indexes over it
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * The four tiered pages, in the order §7 presents them.
 *
 * Portfolio leads because task 16.1 renders it and task 16.3 seeds Property 4 with it;
 * tasks 19.5, 20.4 and 23.5 register the other three, which are declared here now so that
 * those tasks add a render adapter and nothing else.
 */
export const PAGE_HIERARCHIES = Object.freeze([
  PORTFOLIO_HIERARCHY,
  DASHBOARD_HIERARCHY,
  LIVE_TRADING_HIERARCHY,
  BACKTESTER_HIERARCHY,
]);

/**
 * One page's hierarchy, keyed by `PAGES` value.
 *
 * Only the four tiered pages have a key. A lookup for `strategies` is `undefined` on
 * purpose: those pages have field lists and no priority hierarchy, and answering with an
 * empty tier list would let a caller assert Property 4 over a page §7 never tiered and
 * conclude something from the pass.
 */
export const PAGE_HIERARCHY_BY_PAGE = Object.freeze(
  Object.fromEntries(PAGE_HIERARCHIES.map((h) => [h.page, h])),
);

/** The pages with a declared hierarchy, for a test that iterates them. */
export const TIERED_PAGES = Object.freeze(PAGE_HIERARCHIES.map((h) => h.page));

/**
 * Every declared field that deliberately has no tier, with the reason, page by page.
 *
 * The complement of the tier lists over `pageFields`: a field of a tiered page is in
 * exactly one tier or in exactly one of these lists, and a test asserts that partition is
 * total. Without it, a field dropped from a tier by accident is indistinguishable from one
 * excluded on purpose.
 */
export const UNTIERED_FIELDS_BY_PAGE = Object.freeze(
  Object.fromEntries(PAGE_HIERARCHIES.map((h) => [h.page, h.untiered])),
);
