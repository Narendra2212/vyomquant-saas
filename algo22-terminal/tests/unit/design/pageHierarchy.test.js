/**
 * ═══════════════════════════════════════════════════════════════════════════
 * `design/pageHierarchy` — vyomquant-ui-redesign task 16.1
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * design.md §18's `PageHierarchy` / `TierField`, §7.1, §7.4, §7.5, §7.6.
 * Requirements 3.1, 3.2, 3.4, 6.2, 6.3, 7.1, 7.2, 7.3, 10.1, 10.2.
 *
 * The structural assertions over the declaration itself: that it is inert and enumerable,
 * that every tier key is a `pageFields` field for the same page, that the tier/untiered
 * partition over `pageFields` is total, and that Requirement 10.2's drawdown is in
 * Portfolio's tier 1 rather than below it.
 *
 * Property 4 — every tier-*n* element precedes every tier-*(n+1)* element in a RENDERED
 * page, and no tier-1 element renders outside the single tier-1 container — is task 16.3's
 * and renders things. Nothing here renders anything.
 */

import { describe, expect, it } from 'vitest';

import {
  DEPRECATED_PATHS,
  PAGES,
  PAGE_FIELDS_BY_PAGE,
  PAGE_FIELD_BY_KEY,
  pageFieldKey,
} from '../../../src/design/pageFields';
import {
  METRIC_TIER_ATTRIBUTE,
  PAGE_HIERARCHIES,
  PAGE_HIERARCHY_BY_PAGE,
  TIERED_PAGES,
  TIERS,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
  UNTIERED_FIELDS_BY_PAGE,
  tierSelector,
} from '../../../src/design/pageHierarchy';

const hasText = (value) => typeof value === 'string' && value.trim() !== '';

/** The keys of one page's tier *n*, in declaration order. */
const keysInTier = (page, tier) =>
  PAGE_HIERARCHY_BY_PAGE[page].tiers.filter((t) => t.tier === tier).map((t) => t.key);

describe('pageHierarchy: the four pages §7 tiers, and only those', () => {
  it('declares Portfolio, Dashboard, Live Trading and Backtester', () => {
    expect(TIERED_PAGES).toEqual([
      PAGES.PORTFOLIO,
      PAGES.DASHBOARD,
      PAGES.LIVE_TRADING,
      PAGES.BACKTESTER,
    ]);
    // Portfolio leads: task 16.1 renders it and 16.3 seeds Property 4 with it.
    expect(TIERED_PAGES[0]).toBe(PAGES.PORTFOLIO);
  });

  it('answers `undefined` for a page §7 gives no tiers, rather than an empty list', () => {
    // Strategies, Trade History and Signal Trace have field lists and no priority
    // hierarchy. An empty tier list would let a caller run Property 4 over one of them and
    // read something into the pass.
    for (const page of [PAGES.STRATEGIES, PAGES.TRADE_HISTORY, PAGES.SIGNAL_TRACE]) {
      expect(PAGE_HIERARCHY_BY_PAGE[page]).toBeUndefined();
      expect(TIERED_PAGES).not.toContain(page);
    }
  });

  it('is inert: every export is frozen data, and no tier entry holds a function', () => {
    // Property 4 enumerates this and renders payloads against it. A getter whose answer
    // depends on when it is called cannot be enumerated once and trusted.
    expect(Object.isFrozen(PAGE_HIERARCHIES)).toBe(true);
    expect(Object.isFrozen(PAGE_HIERARCHY_BY_PAGE)).toBe(true);
    expect(Object.isFrozen(UNTIERED_FIELDS_BY_PAGE)).toBe(true);
    expect(Object.isFrozen(TIERS)).toBe(true);

    for (const hierarchy of PAGE_HIERARCHIES) {
      expect(Object.isFrozen(hierarchy)).toBe(true);
      expect(Object.isFrozen(hierarchy.tiers)).toBe(true);
      for (const entry of hierarchy.tiers) {
        expect(Object.isFrozen(entry)).toBe(true);
        for (const value of Object.values(entry)) {
          expect(typeof value).not.toBe('function');
        }
      }
    }
  });
});

describe('pageHierarchy: §18\'s TierField shape', () => {
  it.each(
    PAGE_HIERARCHIES.flatMap((h) => h.tiers.map((t) => [`${h.page}/${t.key}`, h.page, t])),
  )('%s carries exactly {tier, key, label}', (_id, page, entry) => {
    expect(Object.keys(entry).sort()).toEqual(['key', 'label', 'tier']);
    expect(TIERS).toContain(entry.tier);
    expect(hasText(entry.key)).toBe(true);
    expect(hasText(entry.label)).toBe(true);
    expect(Object.values(PAGES)).toContain(page);
  });

  it.each(PAGE_HIERARCHIES.map((h) => [h.page, h]))(
    '%s declares its tiers in ascending order, so the list reads as the layout',
    (_page, hierarchy) => {
      const tiers = hierarchy.tiers.map((t) => t.tier);
      expect(tiers).toEqual([...tiers].sort((a, b) => a - b));
    },
  );

  it.each(PAGE_HIERARCHIES.map((h) => [h.page, h]))(
    '%s has a tier 1, and names it once per field',
    (_page, hierarchy) => {
      const tierOne = hierarchy.tiers.filter((t) => t.tier === 1);
      expect(tierOne.length).toBeGreaterThan(0);
      const keys = hierarchy.tiers.map((t) => t.key);
      expect(new Set(keys).size).toBe(keys.length);
    },
  );
});

describe('pageHierarchy: it lines up with pageFields', () => {
  it.each(
    PAGE_HIERARCHIES.flatMap((h) => h.tiers.map((t) => [`${h.page}/${t.key}`, h.page, t])),
  )('%s names a field pageFields declares, with pageFields\' own label', (_id, page, entry) => {
    const declared = PAGE_FIELD_BY_KEY[pageFieldKey({ page, field: entry.key })];
    expect(declared, `${page} tier ${entry.tier} names "${entry.key}", which pageFields does not declare`)
      .toBeDefined();
    // The label is READ from the declaration, not retyped: two spellings of one figure's
    // label are two figures as far as a test is concerned.
    expect(entry.label).toBe(declared.label);
  });

  it.each(TIERED_PAGES.map((page) => [page]))(
    '%s partitions every pageFields entry into exactly one tier or the untiered list',
    (page) => {
      const declared = PAGE_FIELDS_BY_PAGE[page].map((f) => f.field);
      const tiered = PAGE_HIERARCHY_BY_PAGE[page].tiers.map((t) => t.key);
      const untiered = UNTIERED_FIELDS_BY_PAGE[page].map((u) => u.field);

      expect([...tiered, ...untiered].sort()).toEqual([...declared].sort());
      // Disjoint: a field cannot be both placed and deliberately unplaced.
      expect(tiered.filter((key) => untiered.includes(key))).toEqual([]);
    },
  );

  it.each(
    PAGE_HIERARCHIES.flatMap((h) => h.untiered.map((u) => [`${h.page}/${u.field}`, u])),
  )('%s states why it has no tier', (_id, entry) => {
    expect(Object.keys(entry).sort()).toEqual(['field', 'label', 'reason']);
    expect(hasText(entry.reason)).toBe(true);
    // An omission and a decision look identical without one, so the reason is the entry's
    // whole justification for existing and a placeholder defeats it.
    expect(entry.reason.length).toBeGreaterThan(40);
  });

  it('places no deprecated path in any tier', () => {
    // BC-1's `risk.current_drawdown_pct` publishes `today_return_pct`, so a profitable day
    // renders as a positive drawdown. `pageFields` reads none of them; a tier naming a field
    // that did would put the wrong figure in tier 1 of two pages.
    const offenders = PAGE_HIERARCHIES.flatMap((h) =>
      h.tiers
        .map((t) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: h.page, field: t.key })])
        .filter((f) => f && DEPRECATED_PATHS.includes(f.path))
        .map((f) => pageFieldKey(f)));
    expect(offenders).toEqual([]);
  });
});

describe('pageHierarchy: Portfolio (Requirements 10.1, 10.2)', () => {
  const tierOne = keysInTier(PAGES.PORTFOLIO, 1);

  it('holds Requirement 10.1\'s six figures in tier 1', () => {
    // "Realised P&L" is two entries: today's window and BC-5's lifetime sum. Both are
    // tier 1 and `pageFields` labels them apart, because the same words over two
    // quantities is the confusion §7.6 records.
    expect(tierOne).toEqual([
      'totalValue',
      'availableBalance',
      'investedCapital',
      'unrealisedPnl',
      'realisedPnlToday',
      'lifetimeRealizedPnl',
      'totalExposure',
      'currentDrawdown',
    ]);
  });

  it('puts current drawdown IN tier 1, not in a lower section (Requirement 10.2)', () => {
    expect(tierOne).toContain('currentDrawdown');
    expect(keysInTier(PAGES.PORTFOLIO, 2)).not.toContain('currentDrawdown');
    expect(keysInTier(PAGES.PORTFOLIO, 3)).not.toContain('currentDrawdown');
  });

  it('reads the drawdown from BC-1\'s v2 field', () => {
    const entry = PAGE_FIELD_BY_KEY[
      pageFieldKey({ page: PAGES.PORTFOLIO, field: 'currentDrawdown' })
    ];
    expect(entry.path).toBe('risk.current_drawdown_pct_v2');
    expect(entry.backendChange).toBe('BC-1');
  });

  it('keeps the positions regions in tier 2 and the charts in tier 3', () => {
    expect(keysInTier(PAGES.PORTFOLIO, 2)).toEqual(['openPositions', 'positionLiquidationPrice']);
    expect(keysInTier(PAGES.PORTFOLIO, 3)).toEqual(['allocation', 'equityCurve', 'heatmap']);
  });
});

describe('pageHierarchy: the three pages tasks 19.5, 20.4 and 23.5 register', () => {
  it('gives the Dashboard exactly four tier-1 figures (Requirement 3.4)', () => {
    // One row, four figures. Requirement 3.4 forbids a second row of equally-weighted
    // tier-1 cards here — the opposite constraint to Portfolio's eight, which is why the
    // number is asserted per page rather than as a rule about tiers.
    expect(keysInTier(PAGES.DASHBOARD, 1)).toEqual([
      'portfolioValue',
      'todayPnl',
      'totalPnl',
      'currentDrawdown',
    ]);
  });

  it('leaves the Requirement 3.3 alert strip untiered, with its reason', () => {
    const untiered = UNTIERED_FIELDS_BY_PAGE[PAGES.DASHBOARD];
    expect(untiered.map((u) => u.field)).toEqual(['alertCondition']);
    expect(untiered[0].reason).toMatch(/above tier 1/i);
  });

  it('gives Live Trading Requirement 7.1/7.2/7.3\'s three rows', () => {
    expect(keysInTier(PAGES.LIVE_TRADING, 1)).toEqual([
      'connectionState',
      'exchange',
      'account',
      'strategy',
      'market',
      'tradingEnvironment',
    ]);
    expect(keysInTier(PAGES.LIVE_TRADING, 3)).toEqual([
      'latestSignal',
      'latestOrder',
      'executionStatus',
    ]);
    // The selector chooses which deployment the tiers describe; it is above them.
    expect(UNTIERED_FIELDS_BY_PAGE[PAGES.LIVE_TRADING].map((u) => u.field))
      .toEqual(['deployment', 'deploymentStopped']);
  });

  it('gives the Backtester Requirement 6.2\'s six result figures', () => {
    expect(keysInTier(PAGES.BACKTESTER, 1)).toEqual([
      'totalReturn',
      'netPnl',
      'maxDrawdown',
      'sharpe',
      'winRate',
      'tradeCount',
    ]);
    expect(keysInTier(PAGES.BACKTESTER, 2)).toEqual(['equityCurve', 'drawdownCurve']);
    // The tiers describe the result region; §7.4's configuration form precedes all three
    // under Requirement 6.1, which is a separate contract.
    expect(PAGE_HIERARCHY_BY_PAGE[PAGES.BACKTESTER].note).toMatch(/Tier 3/);
  });
});

describe('pageHierarchy: the attributes Property 4 selects on', () => {
  it('names one attribute for the page and one for the tier', () => {
    expect(TIER_PAGE_ATTRIBUTE).toBe('data-page');
    expect(TIER_ATTRIBUTE).toBe('data-page-tier');
    // `ds/Metric`'s own per-figure attribute, re-exported so pages and the property test
    // spell it once.
    expect(METRIC_TIER_ATTRIBUTE).toBe('data-metric-tier');
  });

  it('composes a selector that names one page\'s one container', () => {
    expect(tierSelector(PAGES.PORTFOLIO, 1)).toBe('[data-page="portfolio"][data-page-tier="1"]');
    expect(tierSelector(PAGES.DASHBOARD, 2)).toBe('[data-page="dashboard"][data-page-tier="2"]');
  });
});
