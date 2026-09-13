/**
 * @fileoverview Portfolio Page CSS and Typography Validation
 * Static analysis test to verify Portfolio.jsx uses proper CSS values and typography tokens
 * @version 1.0.0
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import Portfolio from '../../src/pages/Portfolio';
import * as portfolioModule from '../../src/api/modules/portfolio';
import * as paperModule from '../../src/api/modules/paper';
import * as dashboardModule from '../../src/api/modules/dashboard';
import {
  PAGES,
  PAGE_FIELD_BY_KEY,
  pageFieldKey,
} from '../../src/design/pageFields';
import {
  PAGE_HIERARCHY_BY_PAGE,
  tierSelector,
} from '../../src/design/pageHierarchy';
import { UNREPORTED_REASON } from '../../src/design/reported';

// Recharts renders through layout APIs jsdom does not implement; the charts are not what these
// tests are about, so they are stubbed to plain elements.
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  AreaChart: ({ children }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  PieChart: ({ children }) => <div data-testid="pie-chart">{children}</div>,
  Pie: ({ children }) => <div data-testid="pie">{children}</div>,
  Cell: () => <div data-testid="cell" />,
  CartesianGrid: () => <div data-testid="grid" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

describe('Portfolio Page CSS and Typography Validation', () => {
  
  it('should check that no invalid fontSize patterns exist in source', () => {
    // Read the source file content for validation
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for invalid fontSize patterns from the original code
    const invalidFontSizePatterns = [
      /fontSize:\s*9\b/,   // fontSize: 9 (no units)
      /fontSize:\s*20\b/,  // fontSize: 20 (no units)
      /fontSize:\s*12\b/,  // fontSize: 12 (no units)
      /fontSize:\s*10\b/,  // fontSize: 10 (no units)
      /fontSize:\s*8\b/,   // fontSize: 8 (no units)
    ];
    
    invalidFontSizePatterns.forEach(pattern => {
      const matches = portfolioContent.match(pattern);
      expect(matches).toBeNull();
      if (matches) {
        console.error(`Found invalid fontSize pattern: ${pattern}`);
      }
    });
  });

  it('should verify Tailwind typography classes are used', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for proper Tailwind typography classes.
    //
    // These are the live steps of the `styles/tokens.css` scale. The names this test used to
    // assert - `text-heading-lg`, `text-caption-sm` and `text-caption` - were declared only in
    // the deleted `tailwind.config.js`, which Tailwind v4 never loaded, so they compiled to
    // nothing and the elements carrying them rendered at the inherited size. Task 3.1 re-pointed
    // them onto this scale by intended size: 18px -> `text-section`, 10px and 9px -> `text-micro`.
    //
    // `text-section` is no longer among them: task 16.1 replaced the four tier-1 cards that
    // carried it with `ds/Metric tier={1}`, which selects `--text-figure` inside the primitive.
    // A page-level class name for a tier-1 figure is exactly what that mapping replaces, so
    // asserting one here would hold the page to the thing the rebuild removed.
    const expectedClasses = [
      'text-body',
      'text-micro',
    ];
    
    expectedClasses.forEach(className => {
      expect(portfolioContent).toContain(className);
    });
  });

  it('should verify no invalid unitless spacing values', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for invalid spacing patterns from the original code
    const invalidSpacingPatterns = [
      /padding:\s*20\b/,    // padding: 20 (no units)
      /gap:\s*10\b/,         // gap: 10 (no units)
      /marginBottom:\s*16\b/, // marginBottom: 16 (no units)
      /gap:\s*12\b/,         // gap: 12 (no units)
      /marginBottom:\s*12\b/, // marginBottom: 12 (no units)
      /gap:\s*5\b/,          // gap: 5 (no units)
      /gap:\s*8\b/,          // gap: 8 (no units)
    ];
    
    invalidSpacingPatterns.forEach(pattern => {
      const matches = portfolioContent.match(pattern);
      expect(matches).toBeNull();
      if (matches) {
        console.error(`Found invalid spacing pattern: ${pattern}`);
      }
    });
  });

  it('should verify proper CSS units in inline styles', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check that spacing values have proper units
    const validSpacingPatterns = [
      /padding:\s*["']?\d+px["']?/,  // padding: "20px" or padding: 20px
      /gap:\s*["']?\d+px["']?/,       // gap: "10px" or gap: 10px
      /marginBottom:\s*["']?\d+px["']?/, // marginBottom: "16px" or marginBottom: 16px
    ];
    
    // At least some spacing should have proper units
    const hasValidSpacing = validSpacingPatterns.some(pattern => 
      portfolioContent.match(pattern)
    );
    expect(hasValidSpacing).toBe(true);
  });

  it('should verify semantic color tokens are used', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for semantic color tokens
    expect(portfolioContent).toContain('#10B981'); // accent-profit
    expect(portfolioContent).toContain('#EF4444'); // accent-loss
  });

  it('should verify semantic HTML structure', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for semantic ul/li elements
    expect(portfolioContent).toContain('<ul');
    expect(portfolioContent).toContain('role="list"');
    expect(portfolioContent).toContain('<li');
    expect(portfolioContent).toContain('role="listitem"');
  });

  it('should verify loading states with Activity spinner', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for loading indicator components
    expect(portfolioContent).toContain('Activity');
    expect(portfolioContent).toContain('animate-spin');
  });

  it('should verify improved empty states', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for better empty state messaging
    expect(portfolioContent).toMatch(/No.*data available/i);
    expect(portfolioContent).toMatch(/Connect.*exchange/i);
    expect(portfolioContent).toMatch(/Open positions/i);
    expect(portfolioContent).toMatch(/Trade history/i);
  });

  it('should verify monospace font usage for numeric values', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check that numeric values use monospace font
    const monospacePattern = /fontFamily:\s*["']?monospace["']?/g;
    const monospaceMatches = portfolioContent.match(monospacePattern);
    expect(monospaceMatches).toBeTruthy();
    expect(monospaceMatches.length).toBeGreaterThan(2); // Should have multiple instances
  });

  it('carries no trend arrow for a figure that reports no trend', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');

    // This test used to require `TrendingUp` AND `TrendingDown`, which pinned the `SignIcon`
    // beside each tier-1 card. Task 16.1 deleted it: an arrow drawn from `value >= 0` reports
    // the sign of the figure it sits beside, which the figure already carries, and the absent
    // case drew a red downward arrow for a number nobody had. Requirement 1.5 spends colour
    // and shape on state, risk and required action - a portfolio balance is none of the three.
    expect(portfolioContent).not.toContain('TrendingDown');
    // The element, not the word: the comment where the component used to be names it, which is
    // how a reader finds out what replaced it.
    expect(portfolioContent).not.toMatch(/<SignIcon/);
    // `TrendingUp` stays: it is the equity-curve region's empty-state illustration, which is
    // task 16.2's, not a per-figure sign.
    expect(portfolioContent).toContain('TrendingUp');
  });
});

/**
 * Requirement 28.4 / 28.5 - what the page renders when it has no figure to render.
 *
 * The checks above are static: they read the source text. They cannot tell a displayed `$0.00`
 * apart from a figure nobody read, which is precisely the confusion Requirement 28.5 exists to
 * prevent, so these render the page instead and read the screen.
 *
 * Three guarantees are pinned here, one per test:
 *
 *   1. a figure the response did not carry renders as words, never as `0` or `$0.00`;
 *   2. a read that did not complete is distinguishable on screen both from a genuinely empty
 *      ledger and from a genuine zero;
 *   3. in PAPER the simulated indicator sits inside the same region as the figures it qualifies.
 */
/* ══════════════════════════════════════════════════════════════════════════════════════
 * The shared fixture and query helpers, used by both render suites below.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * `overview`, as `dashboard_aggregation_service.get_portfolio_overview` answers it.
 *
 * Task 16.1 re-pointed tier 1 at this block: all seven of its money figures and the drawdown
 * beside them come from `GET /api/dashboard`, so a fixture that carries only `positions` is no
 * longer the healthy case for this page. `realized_pnl` is BC-5's LIFETIME sum and is
 * deliberately a different figure from `today_realized_pnl` here, because the whole point of
 * that change is that the two are not interchangeable.
 */
const overviewBody = (overrides = {}) => ({
  total_value: 65000,
  available_balance: 40000,
  used_balance: 25000,
  unrealized_pnl: 1500,
  today_realized_pnl: 800,
  realized_pnl: 12345.5,
  total_exposure: 30000,
  currency: 'USDT',
  ...overrides,
});

/**
 * A healthy `GET /api/dashboard` body, as far as this page reads one.
 *
 * `degraded: null` and a counted `risk.open_positions_count` are the healthy readings, spelled
 * out rather than omitted: the point of BC-2 is that the absent and present cases of these two
 * fields mean different things, so a fixture that leaves them out is not the healthy case.
 *
 * `risk.current_drawdown_pct` is present ALONGSIDE `current_drawdown_pct_v2` and carries a
 * different number on purpose. BC-1 left the deprecated field on the wire for its deprecation
 * window, and it publishes `today_return_pct`; a page reading it would render 12.5% here, so
 * the fixture makes that mistake visible instead of unobservable.
 */
const dashboardBody = ({
  positions = [],
  degraded = null,
  openPositionsCount,
  overview = overviewBody(),
  drawdown = 3.2,
} = {}) => ({
  positions,
  degraded,
  overview,
  risk: {
    open_positions_count: openPositionsCount === undefined ? positions.length : openPositionsCount,
    current_drawdown_pct_v2: drawdown,
    current_drawdown_pct: 12.5,
  },
});

/** §7.6's tier 1, read from the declaration rather than restated. */
const TIER_ONE = PAGE_HIERARCHY_BY_PAGE[PAGES.PORTFOLIO].tiers.filter((t) => t.tier === 1);

/** One portfolio field's declaration, for its label and its reason. */
const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.PORTFOLIO, field })];

/** The page's single tier-1 container. `null` when the row did not render at all. */
const tierOneContainer = () => document.querySelector(tierSelector(PAGES.PORTFOLIO, 1));

/** The `ds/Metric` root for a label, wherever it is on the page. */
const metricFor = (label) => screen.getByText(label).closest('[data-metric-tier]');

/** The figure text of one metric, or `null` when it rendered the not-available marker. */
const figureOf = (label) => {
  const metric = metricFor(label);
  return metric.getAttribute('data-metric-available') === 'true'
    ? metric.textContent.replace(label, '').trim()
    : null;
};

/** The not-available marker inside one metric, or `null`. */
const markerOf = (label) =>
  metricFor(label).querySelector('[data-metric-marker="not-available"]');

/**
 * Tier 1's `ds/Panel`, in every state including the ones that render no container.
 *
 * The only `money` panel on the page — the positions ledger and the charts are still
 * `ui/Card` until task 16.2 — so the attribute identifies it without depending on a class.
 */
const tierOnePanel = () => document.querySelector('section[data-panel-money="true"]');

/**
 * The three live reads this page issues after task 16.1, plus the dashboard read that
 * replaced the two dead `/api/portfolio/positions*` calls in task 13.1.
 *
 * `dashboard` is a thunk answering a whole `GET /api/dashboard` body, not a bare positions
 * array: `overview`, `risk`, `degraded` and `positions` all live on that one body, and tier 1
 * and the positions ledger are two view models built from it.
 *
 * `portfolioApi.getSummary` is spied but given no behaviour beyond a rejection, because the
 * page must not call it any more (§7.6: `/summary` carries neither `available_balance` nor a
 * drawdown, so tier 1 cannot be assembled from it). A call would fail the test that asserts
 * the call count, not silently succeed.
 */
const stubLiveReads = ({ dashboard }) => {
  vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockImplementation(() => {
    throw new Error('Portfolio must not read GET /api/portfolio/summary (§7.6, task 16.1).');
  });
  vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockImplementation(() => dashboard());
  vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue([]);
  vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue([]);
  vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue([]);
};

describe('Portfolio absence-state rendering', () => {

  beforeEach(() => {
    // Explicit rather than relying on Testing Library's auto-cleanup hook: several assertions
    // below are absence assertions (`queryByText(...)).toBeNull()`, "no `<table>` in the ledger"),
    // and a leftover tree from the previous test would make one of those pass or fail for a
    // reason that has nothing to do with the render under test.
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders a figure the response did not carry as the marker, never as 0', async () => {
    // The body arrived and carried no figures at all - the case the removed `?? 0` / `?? 100000`
    // fallbacks used to turn into a displayed balance. It is NOT a failed read: the response is
    // readable, so tier 1 renders, with one marker per field and each marker's own reason.
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({ overview: {}, drawdown: null })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const container = tierOneContainer();
    for (const { key, label } of TIER_ONE) {
      const marker = markerOf(label);
      expect(marker, `${label} rendered a figure for a body that carried none`).not.toBeNull();
      // Requirement 19.3: the marker carries the sentence the field's own declaration gives —
      // and `design/reported.UNREPORTED_REASON` for the five whose declaration says the server
      // ALWAYS reports a value (`absence: 'never'`, so `reason: null`). Those five are absent
      // here only because this fixture is a body that carried nothing, which is a case their
      // declaration says cannot happen; the fallback sentence claims nothing about the account,
      // which is the only honest thing to say about a field nobody expected to be missing.
      expect(marker.getAttribute('title')).toBe(declared(key).reason ?? UNREPORTED_REASON);
      expect(marker.getAttribute('title').trim()).not.toBe('');
    }
    // No formatted figure - zero or otherwise - may appear in the row. Asserted on the
    // availability flag rather than on the container's text, because the text includes the
    // screen-reader sentences and those are prose.
    for (const figure of container.querySelectorAll('[data-metric-tier]')) {
      expect(figure.dataset.metricAvailable).toBe('false');
    }
    expect(screen.queryByText('0.00')).toBeNull();
    expect(screen.queryByText('100,000.00')).toBeNull();
  });

  it('distinguishes a failed read from an empty ledger and from a genuine zero', async () => {
    // (a) The one read behind tier 1 and the ledger fails.
    stubLiveReads({
      dashboard: () => Promise.reject(new Error('positions upstream timed out')),
    });

    const failed = render(<Portfolio />);

    // §7.4's rule for a tier-1 row on a failure, applied here: the figures are not rendered at
    // all. Not as zeros, and not as eight markers either - a transport failure is one fact
    // about the read, and eight markers would state it as eight facts about the account.
    await waitFor(() => expect(screen.getByText('Could not load your portfolio')).toBeDefined());
    expect(tierOneContainer()).toBeNull();
    // Requirement 14.4: the translation is what a trader reads, so the rejection's own message
    // does not reach the tier-1 panel. Scoped to that panel because the positions region still
    // renders the transport error verbatim — that region is task 16.2's, and its `ErrorState`
    // conversion is 16.2's too.
    expect(tierOnePanel().textContent).not.toMatch(/positions upstream timed out/);
    expect(screen.getByText(/Open positions could not be read/)).toBeDefined();
    // The decisive assertion: a failed positions read must NOT claim the account holds nothing.
    expect(screen.queryByText(/No open positions currently held/)).toBeNull();
    failed.unmount();

    // (b) The read succeeds and the account genuinely holds nothing and is genuinely at zero.
    vi.restoreAllMocks();
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        overview: overviewBody({
          total_value: 0,
          available_balance: 0,
          used_balance: 0,
          unrealized_pnl: 0,
          today_realized_pnl: 0,
          realized_pnl: 0,
          total_exposure: 0,
        }),
        drawdown: 0,
      })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // A reported zero is a reading and renders as one, for every field in the row.
    for (const { label } of TIER_ONE) {
      expect(markerOf(label), `${label} rendered a marker for a reported zero`).toBeNull();
    }
    expect(figureOf(declared('totalValue').label)).toContain('0.00');
    // A drawdown of 0.0 means the account is AT its peak. That is a reading, not an absence.
    expect(figureOf(declared('currentDrawdown').label)).toContain('0.00%');
    expect(screen.getByText('No open positions currently held in LIVE mode.')).toBeDefined();
    expect(screen.queryByText(/Open positions could not be read/)).toBeNull();
  });

  it('carries the environment badge in the panel holding the PAPER figures', async () => {
    // One stub, both environments, answering per `environment` - which is what the server does:
    // `get_portfolio_overview` branches on it and computes the paper account's own figures.
    // A single body for both would let a LIVE figure render under a PAPER badge, which is the
    // combination Requirement 13.6 forbids.
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody()),
    });
    dashboardModule.dashboardApi.getDashboard.mockImplementation(({ environment }) =>
      Promise.resolve(environment === 'paper'
        ? dashboardBody({ overview: overviewBody({ total_value: 25000, currency: 'USD' }) })
        : dashboardBody()));
    vi.spyOn(paperModule.paperApi, 'getSummary').mockResolvedValue({
      total_equity: 25000,
      unrealized_pnl: 10,
      realized_pnl: 20,
      available_balance: 25000,
      roi_pct: 1,
      execution_environment: 'paper',
      is_simulated: true,
    });
    // The envelope shape `GET /api/paper/positions` actually answers - never a bare array.
    vi.spyOn(paperModule.paperApi, 'getPositions').mockResolvedValue({
      positions: [],
      count: 0,
      execution_environment: 'paper',
      is_simulated: true,
    });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());
    // Requirement 12.2 through `ds/Panel money`: the panel holding the money figures carries
    // the environment, so the row cannot be read without it.
    expect(tierOnePanel().querySelector('[data-panel-environment]').dataset.panelEnvironment)
      .toBe('LIVE');
    expect(figureOf(declared('totalValue').label)).toContain('65,000.00');

    fireEvent.click(screen.getByRole('button', { name: /PAPER/i }));

    await waitFor(() =>
      expect(figureOf(declared('totalValue').label)).toContain('25,000.00'));

    expect(dashboardModule.dashboardApi.getDashboard)
      .toHaveBeenCalledWith({ environment: 'paper' });
    expect(tierOnePanel().querySelector('[data-panel-environment]').dataset.panelEnvironment)
      .toBe('PAPER');
    // The denomination is the one the server reported for the account on screen.
    expect(figureOf(declared('totalValue').label)).toContain('USD');

    // And the positions ledger, further down the page, carries its own label.
    const ledgerHeading = screen.getByText(/Open Positions Ledger/);
    expect(ledgerHeading.parentElement.parentElement.textContent).toMatch(/SIMULATED/);
  });

  /* ═══════════════════════════════════════════════════════════════════════════════════════
   * Task 13.1 — the LIVE positions read, and BC-2's `degraded` marker
   *
   * The re-point is only half the change. The other half is that `GET /api/dashboard` answers
   * `positions: []` for BOTH "no open positions" and "the positions read failed", and publishes
   * a top-level `degraded` marker as the only thing that distinguishes them. A page that renders
   * the list without consulting the marker turns an outage into a claim about the account, which
   * is the exact defect design.md §1.6 records and Requirement 14.5 forbids.
   * ═══════════════════════════════════════════════════════════════════════════════════════ */

  it('reads live positions from GET /api/dashboard and calls neither removed portfolio method', async () => {
    // The methods are gone from the module, not merely unused. `getOpenPositions().catch(() =>
    // getPositions())` 404d twice on every load (design.md §1.4), and a method that cannot
    // succeed is a non-functional API surface (Requirement 19.4). This asserts absence rather
    // than a zero call count, because absence is what makes the call impossible.
    expect(Object.keys(portfolioModule.portfolioApi)).not.toContain('getOpenPositions');
    expect(Object.keys(portfolioModule.portfolioApi)).not.toContain('getPositions');
    expect(portfolioModule.portfolioApi.getOpenPositions).toBeUndefined();
    expect(portfolioModule.portfolioApi.getPositions).toBeUndefined();

    const dashboard = vi.fn(() => Promise.resolve(dashboardBody({
      positions: [{
        id: 'pos_binance_btc_usdt',
        symbol: 'BTC/USDT',
        exchange_id: 'binance',
        side: 'long',
        contracts: 0.5,
        entry_price: 60000,
        mark_price: 63000,
        unrealized_pnl: 1500,
      }],
    })));
    stubLiveReads({ dashboard });

    render(<Portfolio />);

    // The normalised position renders, from the endpoint that actually serves positions to a
    // trader (design.md §7.6).
    const marketCell = await screen.findByText('BTC/USDT');
    expect(screen.getByText('binance')).toBeDefined();
    // The whole row, so the normalisation off `contracts` / `entry_price` / `mark_price` /
    // `unrealized_pnl` is pinned rather than only the symbol.
    const row = marketCell.closest('tr');
    expect(row.textContent).toContain('0.5');
    expect(row.textContent).toContain('60,000.00');
    expect(row.textContent).toContain('63,000.00');
    expect(row.textContent).toContain('1500.00');
    // The environment is named on the request: this page's LIVE branch must not be served the
    // paper account's positions.
    expect(dashboard).toHaveBeenCalledTimes(1);
    expect(dashboardModule.dashboardApi.getDashboard).toHaveBeenCalledWith({ environment: 'live' });
    // And `/api/dashboard/overview` is not a surface any more (task 13.2).
    expect(dashboardModule.dashboardApi.getOverview).toBeUndefined();
  });

  it('renders the server reason, not an empty table, when degraded.positions is "unreadable"', async () => {
    // The response BC-2 produces when the Redis read behind the positions failed: a real
    // `overview`, a real equity curve, `positions: []` - and the marker saying why it is empty.
    const reason =
      'Open positions could not be read for this account, so the empty positions list on this ' +
      'response is the absence of a reading and not the absence of positions (Requirement 14.5). ' +
      'Any position held is still held.';
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        positions: [],
        degraded: { positions: 'unreadable', environment: 'live', reason },
        openPositionsCount: null,
      })),
    });

    render(<Portfolio />);

    // The server's own prose, verbatim. Not a sentence composed on the client.
    await waitFor(() => expect(screen.getByText(reason)).toBeDefined());
    // The decisive assertion, and the reason this task exists: the page must not state that the
    // account holds nothing, and must not render the table's empty body either.
    expect(screen.queryByText(/No open positions currently held/)).toBeNull();
    const heading = screen.getByText(/Open Positions Ledger/);
    // The ledger is the only `<table>` this page renders, so its absence is the assertion:
    // an unreadable read must not produce a table at all, empty or otherwise.
    expect(document.querySelectorAll('table')).toHaveLength(0);
    // The count is not a figure here. `risk.open_positions_count` is null, and the heading says
    // the list was not read rather than showing a 0 nobody measured.
    expect(heading.textContent).toContain('not read');
    expect(heading.textContent).not.toMatch(/\(0\)/);
    // The failure is announced, not silent.
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0);
    // `overview` and `risk` arrived on the same body, so tier 1 still renders: an unreadable
    // positions read degrades one region and blanks nothing else (task 16.1 - the row and the
    // ledger are two view models over one read, and BC-2's marker is scoped to the ledger).
    expect(figureOf(declared('totalValue').label)).toContain('65,000.00');
  });

  it('renders the honest empty state for a genuinely empty positions[] with degraded null', async () => {
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({ positions: [], openPositionsCount: 0 })),
    });

    render(<Portfolio />);

    // Same `positions: []` as the test above, opposite meaning, because `degraded` is null.
    await waitFor(() =>
      expect(screen.getByText('No open positions currently held in LIVE mode.')).toBeDefined());
    // Scoped to the ledger's own sentence: tier 1's markers carry per-field reasons that also
    // contain the words "could not be read", and they are a different statement.
    expect(screen.queryByText(/Open positions could not be read/)).toBeNull();
    // A counted zero IS a figure, and it renders as one.
    expect(screen.getByText(/Open Positions Ledger/).textContent).toContain('(0)');
    expect(screen.getByText(/Open Positions Ledger/).textContent).not.toContain('not read');
  });

  it('renders a null open_positions_count as not-available rather than 0', async () => {
    // The narrow case the two tests above do not cover: the positions list read fine, so the
    // ledger renders, but the count the server reports for it is null. BC-2 publishes `null`
    // there and never `0`, so the page may not print a total it did not receive.
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        positions: [{
          id: 'pos_binance_eth_usdt',
          symbol: 'ETH/USDT',
          exchange_id: 'binance',
          side: 'short',
          contracts: 2,
          entry_price: 3200,
          mark_price: 3100,
          unrealized_pnl: 200,
        }],
        openPositionsCount: null,
      })),
    });

    render(<Portfolio />);

    expect(await screen.findByText('ETH/USDT')).toBeDefined();
    const heading = screen.getByText(/Open Positions Ledger/);
    expect(heading.textContent).toContain('Not reported');
    expect(heading.textContent).not.toMatch(/\(0\)/);
    // Not a failure either - the list was read, so the ledger is shown and no failure notice is.
    expect(screen.queryByText(/Open positions could not be read/)).toBeNull();
    expect(heading.textContent).not.toContain('not read');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * Task 16.1 — tier 1 (Requirements 10.1, 10.2, 19.2, 19.3)
 *
 * Requirement 10.1 asks for six figures as the highest-priority elements and 10.2 asks for
 * the drawdown to be among them "rather than in a separate, lower-priority section". Both are
 * claims about WHERE an element is, so these assertions are about containment and about which
 * backend field each figure came from - not about pixels, which is manual QA's (§15.2).
 *
 * Property 4 (task 16.3) generalises the containment clause over arbitrary payloads, and task
 * 16.4 owns the full example set. What is pinned here is the four things this rebuild is for:
 * the row is one container, the drawdown is in it, BC-1's and BC-5's fields are the ones read,
 * and an absent figure is a marker with a reason rather than a zero.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Portfolio tier 1 (task 16.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders every declared tier-1 field, and none of them outside the one container', async () => {
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody()) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // Exactly one tier-1 container on the page - Property 4's second clause depends on there
    // being one, and two "highest-priority" regions is the layout Requirement 10.1 rules out.
    expect(document.querySelectorAll(tierSelector(PAGES.PORTFOLIO, 1))).toHaveLength(1);

    const container = tierOneContainer();
    // Every declared field is present, by its declared label, inside that container.
    expect(TIER_ONE.length).toBe(8);
    for (const { label } of TIER_ONE) {
      const metric = metricFor(label);
      expect(metric, `${label} did not render`).not.toBeNull();
      expect(container.contains(metric), `${label} rendered outside the tier-1 container`)
        .toBe(true);
      expect(metric.dataset.metricTier).toBe('1');
    }
    // And nothing else on the page claims tier 1.
    const tierOneFigures = document.querySelectorAll('[data-metric-tier="1"]');
    expect(tierOneFigures).toHaveLength(TIER_ONE.length);
    for (const figure of tierOneFigures) {
      expect(container.contains(figure)).toBe(true);
    }
  });

  it('renders current drawdown IN the tier-1 container (Requirement 10.2)', async () => {
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody({ drawdown: 3.2 })) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const label = declared('currentDrawdown').label;
    const container = tierOneContainer();
    // The specific thing task 16.1 fixes: the figure a trader reads to decide whether to cut
    // size sits beside the exposure it qualifies, not in a lower risk section.
    expect(container.contains(metricFor(label))).toBe(true);
    expect(figureOf(label)).toContain('3.20%');
    // One drawdown on the page, and it is the tier-1 one.
    expect(screen.getAllByText(label)).toHaveLength(1);
  });

  it('reads the drawdown from BC-1\'s v2 field and not its deprecated neighbour', async () => {
    // Both are on the fixture body with different values, which is the situation on the wire:
    // BC-1 left `current_drawdown_pct` in place for its deprecation window, and it publishes
    // `today_return_pct`, so a page reading it renders a profitable day as a drawdown.
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody({ drawdown: 3.2 })) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    expect(declared('currentDrawdown').path).toBe('risk.current_drawdown_pct_v2');
    expect(figureOf(declared('currentDrawdown').label)).toContain('3.20%');
    expect(figureOf(declared('currentDrawdown').label)).not.toContain('12.5');
  });

  it('renders a null drawdown as the marker with its declared reason, never as 0%', async () => {
    // BC-1 answers `null` - never 0.0 - when no drawdown can be measured. A 0% drawdown is the
    // reading for an account AT its peak, so publishing it for an unmeasurable one would be a
    // fabricated all-clear.
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody({ drawdown: null })) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const label = declared('currentDrawdown').label;
    const marker = markerOf(label);
    expect(marker).not.toBeNull();
    // Requirement 19.3: the reason is the field's own, and it reaches both a sighted trader
    // (the tooltip) and a screen reader (the accessible name plus the sentence).
    expect(marker.getAttribute('title')).toBe(declared('currentDrawdown').reason);
    expect(marker.getAttribute('aria-label')).toBe(`${label}: not available`);
    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(metricFor(label).textContent).not.toContain('0.00%');
    // The marker is IN the tier-1 container: an unmeasurable drawdown does not move the figure
    // out of the row, it changes what the row's eighth cell says.
    expect(tierOneContainer().contains(metricFor(label))).toBe(true);
    // The rest of the row is unaffected - one absent field is not a failed read.
    expect(figureOf(declared('totalValue').label)).toContain('65,000.00');
  });

  it('renders BC-5\'s lifetime realised P&L as a figure, distinctly from today\'s', async () => {
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody()) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // Two labels, two quantities, both in tier 1. `today_realized_pnl` is today's window;
    // `realized_pnl` is the lifetime sum over the fill ledger; `cumulative_pnl` is neither and
    // is not read here, because it includes the mark-to-market on open positions and calling
    // that "realised" reports unbanked money as banked.
    expect(declared('lifetimeRealizedPnl').path).toBe('overview.realized_pnl');
    expect(declared('lifetimeRealizedPnl').backendChange).toBe('BC-5');
    expect(figureOf(declared('lifetimeRealizedPnl').label)).toContain('12,345.50');
    expect(figureOf(declared('realisedPnlToday').label)).toContain('800.00');
    expect(declared('lifetimeRealizedPnl').label).not.toBe(declared('realisedPnlToday').label);
  });

  it('renders a null lifetime realised P&L as the marker, never as 0', async () => {
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        overview: overviewBody({ realized_pnl: null }),
      })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const label = declared('lifetimeRealizedPnl').label;
    expect(markerOf(label).getAttribute('title'))
      .toBe(declared('lifetimeRealizedPnl').reason);
    expect(metricFor(label).textContent).not.toContain('0.00');
    // Today's figure is a separate read and is unaffected.
    expect(figureOf(declared('realisedPnlToday').label)).toContain('800.00');
  });

  it('reads available balance from the dashboard, never from /api/portfolio/summary', async () => {
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody()) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // §7.6: `GET /api/portfolio/summary` answers `total_equity total_pnl pnl_pct
    // total_exposure` and carries no `available_balance` at all, so the figure can only come
    // from the dashboard read. This page therefore no longer calls it - a request whose body
    // nothing renders is a request nobody should pay for.
    expect(declared('availableBalance').path).toBe('overview.available_balance');
    expect(figureOf(declared('availableBalance').label)).toContain('40,000.00');
    expect(portfolioModule.portfolioApi.getSummary).not.toHaveBeenCalled();
  });

  it('labels the used_balance derivation, and states the derivation in its tooltip', async () => {
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody()) });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // No backend field is named "invested capital". Labelling `used_balance` as one without
    // saying so would misrepresent it: margin locked against a losing position is in use
    // without being invested (§7.6), so the label and the tooltip are part of the declaration.
    const entry = declared('investedCapital');
    expect(entry.label).toBe('Invested (capital in use)');
    expect(entry.derivation).toContain('used_balance');
    expect(figureOf(entry.label)).toContain('25,000.00');
    // `ds/Metric` renders `hint` as the label's tooltip and as screen-reader text.
    const metric = metricFor(entry.label);
    expect(metric.textContent).toContain(entry.tooltip);
    expect(screen.getByText(entry.label).getAttribute('title')).toBe(entry.tooltip);
  });
});
