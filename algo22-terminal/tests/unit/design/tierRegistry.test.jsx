/**
 * @fileoverview The tier registry's smoke test — Portfolio's entry mounts and its tiers land.
 *
 * vyomquant-ui-redesign task 16.3a, for `tests/unit/design/tierRegistry.jsx`. design.md §7.6,
 * §18. Requirements 10.1, 10.2, 10.3.
 *
 * NOT Property 4. There is no `fast-check` here, no generator and no 300-run assertion: task
 * 16.3b writes those. What this file establishes is that the registry's contract is honoured and
 * that its one entry works — because a property test whose adapter silently renders nothing
 * passes every clause over an empty node list, and does it in green.
 *
 * So the five claims below are the ones part (b) gets to assume:
 *
 *   1. the registry holds Portfolio and only Portfolio, and names the three pages it does not
 *      hold with the tasks that add them;
 *   2. the entry satisfies the contract, and its payload paths agree with `pageFields`;
 *   3. `payload()` places absent, `null` and zero readings where the page reads them;
 *   4. `mount()` resolves with all three of Portfolio's tier containers on screen, one tier-1
 *      container among them;
 *   5. that tier-1 container holds exactly the eight tier-1 figures `pageHierarchy` declares.
 *
 * EVERY IDENTITY IS SPELLED HERE
 * ------------------------------
 * The eight `{key, label}` pairs, the eight read paths and the page's three tiers are written
 * out as literals below and compared against the declaration. Reading them out of
 * `pageHierarchy` instead would make this file agree with any renaming, including a wrong one —
 * `tests/unit/lib/signalTraceStages.property.test.js`'s convention.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { within } from '@testing-library/react';

import {
  ABSENT,
  PENDING_TIER_PAGES,
  PORTFOLIO_PAYLOAD_PATHS,
  PORTFOLIO_TIER_ENTRY,
  TIER_REGISTRY,
  TIER_REGISTRY_BY_PAGE,
  assertTierEntry,
  declaredTiersOf,
  tierFieldsOf,
} from './tierRegistry';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../../src/design/pageFields';
import { PAGE_HIERARCHIES, tierSelector } from '../../../src/design/pageHierarchy';

/*
 * Portfolio's one declared `requiredMocks` entry, installed at FILE scope because `vi.mock` is
 * hoisted per file. This is the shape every consumer of the registry copies — part (b) and tasks
 * 19.5, 20.4 and 23.5 add one of these per page they register and change nothing else.
 *
 * A stub, not `ds/Chart`: recharts needs layout APIs jsdom does not implement, and the page
 * reaches the module through `lazy(() => import(...))`. Nothing here asserts anything about a
 * chart's interior — that is `tests/unit/ds/Chart.test.jsx`'s subject.
 */
vi.mock('../../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/** §7.6's tier 1, spelled out. Eight entries, because "Realised P&L" is two quantities. */
const PORTFOLIO_TIER_ONE = Object.freeze([
  Object.freeze({ key: 'totalValue', label: 'Total value' }),
  Object.freeze({ key: 'availableBalance', label: 'Available' }),
  Object.freeze({ key: 'investedCapital', label: 'Invested (capital in use)' }),
  Object.freeze({ key: 'unrealisedPnl', label: 'Unrealised P&L' }),
  Object.freeze({ key: 'realisedPnlToday', label: 'Realised P&L (today)' }),
  Object.freeze({ key: 'lifetimeRealizedPnl', label: 'Realised P&L (lifetime)' }),
  Object.freeze({ key: 'totalExposure', label: 'Total exposure' }),
  Object.freeze({ key: 'currentDrawdown', label: 'Current drawdown' }),
]);

/**
 * Where each tier-1 field is read from on `GET /api/dashboard`, spelled out.
 *
 * `investedCapital` is absent from this map on purpose: `pageFields` declares it DERIVED with no
 * `path`, and its entry in `PORTFOLIO_PAYLOAD_PATHS` points at `overview.used_balance`, the input
 * the server derives it from. Asserted separately below, because "the fixture writes a field
 * where the declaration says it is read" and "the fixture writes a derived field at its input"
 * are two different claims.
 */
const PORTFOLIO_DECLARED_PATHS = Object.freeze({
  totalValue: 'overview.total_value',
  availableBalance: 'overview.available_balance',
  unrealisedPnl: 'overview.unrealized_pnl',
  realisedPnlToday: 'overview.today_realized_pnl',
  lifetimeRealizedPnl: 'overview.realized_pnl',
  totalExposure: 'overview.total_exposure',
  currentDrawdown: 'risk.current_drawdown_pct_v2',
});

/** One Portfolio field's declaration. */
const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.PORTFOLIO, field })];

/** The `ds/Metric` roots inside one element, by the declared per-figure attribute. */
const metricRootsIn = (element) => [...element.querySelectorAll('[data-metric-tier]')];

let mounted = null;

const mountPortfolio = async (values) => {
  mounted = await PORTFOLIO_TIER_ENTRY.mount(PORTFOLIO_TIER_ENTRY.payload(values));
  return mounted;
};

afterEach(() => {
  if (mounted !== null) mounted.unmount();
  mounted = null;
});

describe('the Property 4 tier registry', () => {
  it('holds Portfolio and names the three pages it does not hold yet', () => {
    // Seeded with Portfolio (task 16.3). One adapter against four declared hierarchies, and the
    // gap is DATA rather than an omission — without `PENDING_TIER_PAGES`, "Property 4 covers
    // Portfolio" and "Property 4 covers every tiered page" look the same from inside the suite.
    expect(TIER_REGISTRY.map((entry) => entry.page)).toEqual(['portfolio']);
    expect(PAGE_HIERARCHIES).toHaveLength(4);

    expect(PENDING_TIER_PAGES.map(({ page, task }) => `${page}:${task}`))
      .toEqual(['dashboard:19.5', 'live-trading:20.4', 'backtester:23.5']);
    // Registered and pending partition the four tiered pages: no page is in both lists and none
    // is in neither, so adding an entry without deleting its pending record fails here.
    expect([...TIER_REGISTRY.map((e) => e.page), ...PENDING_TIER_PAGES.map((p) => p.page)].sort())
      .toEqual(PAGE_HIERARCHIES.map((h) => h.page).sort());

    expect(TIER_REGISTRY_BY_PAGE[PAGES.PORTFOLIO]).toBe(PORTFOLIO_TIER_ENTRY);
    // `undefined`, not an empty adapter, for a page nobody has registered — the same discipline
    // `PAGE_HIERARCHY_BY_PAGE` keeps for the three untiered pages.
    expect(TIER_REGISTRY_BY_PAGE[PAGES.DASHBOARD]).toBeUndefined();
    expect(TIER_REGISTRY_BY_PAGE[PAGES.STRATEGIES]).toBeUndefined();
  });

  it('satisfies the entry contract, and pairs Portfolio with Portfolio\'s hierarchy', () => {
    for (const entry of TIER_REGISTRY) expect(() => assertTierEntry(entry)).not.toThrow();

    expect(PORTFOLIO_TIER_ENTRY.page).toBe('portfolio');
    expect(PORTFOLIO_TIER_ENTRY.hierarchy.page).toBe('portfolio');
    expect(declaredTiersOf(PORTFOLIO_TIER_ENTRY.hierarchy)).toEqual([1, 2, 3]);
    expect(PORTFOLIO_TIER_ENTRY.payloadFields)
      .toEqual(PORTFOLIO_TIER_ONE.map(({ key }) => key));

    // The declared file-scope mock, named with its reason. The stub installed above is that
    // declaration honoured, which is why this file can mount the page at all.
    expect(PORTFOLIO_TIER_ENTRY.requiredMocks.map((m) => m.module))
      .toEqual(['src/components/ds/Chart']);
    for (const { why } of PORTFOLIO_TIER_ENTRY.requiredMocks) expect(why.trim()).not.toBe('');

    // A mispaired entry is the failure mode worth a test of its own: it selects tier containers
    // for a page it did not render, and every Property 4 clause then passes over nothing.
    expect(() => assertTierEntry({ ...PORTFOLIO_TIER_ENTRY, page: PAGES.DASHBOARD }))
      .toThrow(/carries the hierarchy for/);
  });

  it('writes each generable field where `pageFields` says the page reads it', () => {
    for (const [field, path] of Object.entries(PORTFOLIO_DECLARED_PATHS)) {
      expect(declared(field).path, `${field}'s declared path`).toBe(path);
      expect(PORTFOLIO_PAYLOAD_PATHS[field], `${field}'s payload path`).toBe(path);
    }
    // The derived one. No backend field is named "invested capital" (§7.6): `pageFields` gives
    // it no path and lists `overview.used_balance` first among its inputs, so that is where a
    // generated reading for it belongs.
    expect(declared('investedCapital').path).toBeNull();
    expect(declared('investedCapital').inputs[0]).toBe('overview.used_balance');
    expect(PORTFOLIO_PAYLOAD_PATHS.investedCapital).toBe('overview.used_balance');

    expect(Object.keys(PORTFOLIO_PAYLOAD_PATHS).sort())
      .toEqual([...Object.keys(PORTFOLIO_DECLARED_PATHS), 'investedCapital'].sort());
  });

  it('places absent, `null` and zero readings on the body the page reads', () => {
    const healthy = PORTFOLIO_TIER_ENTRY.payload();
    expect(healthy.dashboard.overview.total_value).toBe(65000);
    expect(healthy.dashboard.risk.current_drawdown_pct_v2).toBe(3.2);

    // ABSENT deletes the key. Not `undefined` — a body with no `total_value` and a body with
    // `total_value: undefined` are different objects, and only the first is what a server that
    // omitted a figure sends.
    const gone = PORTFOLIO_TIER_ENTRY.payload({ totalValue: ABSENT });
    expect(Object.hasOwn(gone.dashboard.overview, 'total_value')).toBe(false);

    const reported = PORTFOLIO_TIER_ENTRY.payload({
      currentDrawdown: null,
      totalExposure: 0,
      investedCapital: 'not a number',
    });
    expect(reported.dashboard.risk.current_drawdown_pct_v2).toBeNull();
    expect(reported.dashboard.overview.total_exposure).toBe(0);
    expect(reported.dashboard.overview.used_balance).toBe('not a number');
    // One payload per call: a shared fixture object would let one generated run contaminate the
    // next, which over 300 runs is a failure nobody can reproduce.
    expect(PORTFOLIO_TIER_ENTRY.payload().dashboard.overview.total_exposure).toBe(30000);

    expect(() => PORTFOLIO_TIER_ENTRY.payload({ sharpe: 1 }))
      .toThrow(/not one of its payloadFields/);
  });

  it('mounts Portfolio with all three tier containers on screen, one of them tier 1', async () => {
    const page = await mountPortfolio();

    for (const tier of [1, 2, 3]) {
      expect(page.tierContainer(tier), `tier ${tier} container`).not.toBeNull();
    }
    // Property 4's second clause depends on there being exactly one tier-1 container to be
    // outside of, so the count is asserted here rather than assumed there.
    expect(document.querySelectorAll(tierSelector(PAGES.PORTFOLIO, 1))).toHaveLength(1);
    // Marked for THIS page. A selector that matched any `[data-page-tier="1"]` would collide the
    // moment a second page joins the registry.
    expect(page.tierContainer(1).getAttribute('data-page')).toBe('portfolio');

    // Document order, which is the only thing Property 4's first clause is about.
    // `querySelectorAll` returns document order, so the tiers of the marked containers read in
    // that order ARE the page's tier sequence — a tier-1 container placed below tier 2 shows up
    // here as `[2, 1, 3]`.
    expect([...document.querySelectorAll('[data-page-tier]')]
      .map((element) => Number(element.getAttribute('data-page-tier'))))
      .toEqual([1, 2, 3]);
  });

  it('renders the eight tier-1 figures §7.6 declares inside that tier-1 container', async () => {
    const page = await mountPortfolio();
    const container = page.tierContainer(1);

    // The declaration and the spelled-out list agree, field for field and label for label. This
    // is the cross-check that makes the loop below meaningful: without it the loop would look
    // for whatever the module currently says, which is not a test.
    expect(tierFieldsOf(PORTFOLIO_TIER_ENTRY.hierarchy, 1).map(({ key, label }) => ({ key, label })))
      .toEqual(PORTFOLIO_TIER_ONE.map(({ key, label }) => ({ key, label })));

    for (const { key, label } of PORTFOLIO_TIER_ONE) {
      expect(declared(key).label, `${key}'s declared label`).toBe(label);
      const roots = within(container).getAllByText(label)
        .map((node) => node.closest('[data-metric-tier]'))
        .filter(Boolean);
      expect(roots.length, `${label} matched ${roots.length} figures in tier 1`).toBe(1);
      expect(roots[0].getAttribute('data-metric-tier')).toBe('1');
    }

    // Eight figures and no ninth. Requirement 10.1 orders these above everything else and 10.2
    // puts the drawdown among them, so a figure that arrived in this container without a
    // declaration is a change to what tier 1 claims.
    expect(metricRootsIn(container)).toHaveLength(8);
    for (const root of metricRootsIn(container)) {
      expect(root.getAttribute('data-metric-tier')).toBe('1');
    }

    // …and every tier-1 figure ON THE PAGE is one of them. `metrics()` is the whole document in
    // order, which is what part (b) walks.
    const tierOneOnPage = page.metrics().filter(({ tier }) => tier === 1);
    expect(tierOneOnPage).toHaveLength(8);
    for (const { element } of tierOneOnPage) expect(container.contains(element)).toBe(true);
  });
});
