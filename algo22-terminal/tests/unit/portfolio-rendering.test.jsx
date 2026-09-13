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
    const expectedClasses = [
      'text-section',
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

  it('should verify trending icons for P&L indicators', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for TrendingUp and TrendingDown icons
    expect(portfolioContent).toContain('TrendingUp');
    expect(portfolioContent).toContain('TrendingDown');
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
describe('Portfolio absence-state rendering', () => {
  /** The nearest CSS-grid ancestor, used to ask whether two nodes share one region. */
  const gridRegionOf = (element) => {
    let node = element;
    while (node && node !== document.body) {
      if (node.style && node.style.display === 'grid') return node;
      node = node.parentElement;
    }
    return null;
  };

  /**
   * A healthy `GET /api/dashboard` body, as far as this page reads one.
   *
   * `degraded: null` and a counted `risk.open_positions_count` are the healthy readings, spelled
   * out rather than omitted: the point of BC-2 is that the absent and present cases of these two
   * fields mean different things, so a fixture that leaves them out is not the healthy case.
   */
  const dashboardBody = ({ positions = [], degraded = null, openPositionsCount } = {}) => ({
    positions,
    degraded,
    risk: {
      open_positions_count: openPositionsCount === undefined ? positions.length : openPositionsCount,
    },
  });

  /**
   * The four live reads this page issues, plus the dashboard read that replaced the two dead
   * `/api/portfolio/positions*` calls in task 13.1.
   *
   * `dashboard` is a thunk answering a whole `GET /api/dashboard` body, not a bare positions
   * array, because `degraded` and `risk.open_positions_count` live beside `positions` on that
   * body and both are part of what this page now reads.
   */
  const stubLiveReads = ({ summary, dashboard }) => {
    vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockImplementation(() => summary());
    vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockImplementation(() => dashboard());
    vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue([]);
    vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue([]);
    vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue([]);
  };

  beforeEach(() => {
    // Explicit rather than relying on Testing Library's auto-cleanup hook: several assertions
    // below are absence assertions (`queryByText(...)).toBeNull()`, "no `<table>` in the ledger"),
    // and a leftover tree from the previous test would make one of those pass or fail for a
    // reason that has nothing to do with the render under test.
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders a figure the response did not carry as text, never as 0 or $0.00', async () => {
    // The body arrived and carried no figures at all - the case the removed `?? 0` / `?? 100000`
    // fallbacks used to turn into a displayed balance.
    stubLiveReads({
      summary: () => Promise.resolve({ account: {} }),
      dashboard: () => Promise.resolve(dashboardBody()),
    });

    render(<Portfolio />);

    await waitFor(() => {
      expect(screen.getAllByText('Not reported').length).toBe(4);
    });
    // Total Equity, Unrealized P&L, Realized P&L and Available Cash are all absent, so no
    // formatted figure - zero or otherwise - may appear for any of them.
    expect(screen.queryByText('$0.00')).toBeNull();
    expect(screen.queryByText('+0.00')).toBeNull();
    expect(screen.queryByText('$100,000.00')).toBeNull();
  });

  it('distinguishes a failed read from an empty ledger and from a genuine zero', async () => {
    // (a) Both reads fail.
    stubLiveReads({
      summary: () => Promise.reject(new Error('summary upstream timed out')),
      dashboard: () => Promise.reject(new Error('positions upstream timed out')),
    });

    const failed = render(<Portfolio />);

    await waitFor(() => {
      expect(screen.getAllByText('Unavailable — read failed').length).toBe(4);
    });
    expect(screen.getByText(/summary upstream timed out/)).toBeDefined();
    expect(screen.getByText(/Open positions could not be read/)).toBeDefined();
    // The decisive assertion: a failed positions read must NOT claim the account holds nothing.
    expect(screen.queryByText(/No open positions currently held/)).toBeNull();
    failed.unmount();

    // (b) The reads succeed and the account genuinely holds nothing and is genuinely at zero.
    vi.restoreAllMocks();
    stubLiveReads({
      summary: () => Promise.resolve({
        account: {
          total_equity: 0,
          unrealized_pnl: 0,
          realized_pnl: 0,
          available_balance: 0,
          pnl_pct: 0,
        },
      }),
      dashboard: () => Promise.resolve(dashboardBody()),
    });

    render(<Portfolio />);

    await waitFor(() => {
      expect(screen.getAllByText('$0.00').length).toBe(2); // equity and cash
    });
    expect(screen.getAllByText('+0.00').length).toBe(2); // unrealized and realized P&L
    expect(screen.getByText('No open positions currently held in LIVE mode.')).toBeDefined();
    // A real zero is neither an absence nor a failure.
    expect(screen.queryByText('Not reported')).toBeNull();
    expect(screen.queryByText('Unavailable — read failed')).toBeNull();
    expect(screen.queryByText(/could not be read/)).toBeNull();
  });

  it('carries the simulated indicator inside the same region as the PAPER figures', async () => {
    stubLiveReads({
      summary: () => Promise.resolve({ account: { total_equity: 65000, available_balance: 40000 } }),
      dashboard: () => Promise.resolve(dashboardBody()),
    });
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

    await waitFor(() => expect(screen.getAllByText('$65,000.00').length).toBe(1));
    // The LIVE branch holds no simulated figures, so it carries no simulated label.
    expect(screen.queryByText(/SIMULATED/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /PAPER/i }));

    await waitFor(() => expect(screen.getAllByText('$25,000.00').length).toBeGreaterThan(0));

    // The indicator must live in the grid that holds the figure cards, not only in the page
    // header: a reader looking at "Total Equity" has to be able to see it without scrolling up.
    const figureGrid = gridRegionOf(screen.getByText('Total Equity'));
    expect(figureGrid).not.toBeNull();
    expect(figureGrid.textContent).toMatch(/SIMULATED/);
    expect(figureGrid.textContent).toContain('$25,000.00');

    // And the positions ledger, further down the page, carries its own.
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
    stubLiveReads({
      summary: () => Promise.resolve({ account: { total_equity: 65000, available_balance: 40000 } }),
      dashboard,
    });

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
      summary: () => Promise.resolve({ account: { total_equity: 65000, available_balance: 40000 } }),
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
    // The summary read succeeded on the same load, so its figures are still shown: a degraded
    // positions read does not blank the rest of the page.
    expect(screen.getByText('$65,000.00')).toBeDefined();
  });

  it('renders the honest empty state for a genuinely empty positions[] with degraded null', async () => {
    stubLiveReads({
      summary: () => Promise.resolve({ account: { total_equity: 65000, available_balance: 40000 } }),
      dashboard: () => Promise.resolve(dashboardBody({ positions: [], openPositionsCount: 0 })),
    });

    render(<Portfolio />);

    // Same `positions: []` as the test above, opposite meaning, because `degraded` is null.
    await waitFor(() =>
      expect(screen.getByText('No open positions currently held in LIVE mode.')).toBeDefined());
    expect(screen.queryByText(/could not be read/)).toBeNull();
    // A counted zero IS a figure, and it renders as one.
    expect(screen.getByText(/Open Positions Ledger/).textContent).toContain('(0)');
    expect(screen.getByText(/Open Positions Ledger/).textContent).not.toContain('not read');
  });

  it('renders a null open_positions_count as not-available rather than 0', async () => {
    // The narrow case the two tests above do not cover: the positions list read fine, so the
    // ledger renders, but the count the server reports for it is null. BC-2 publishes `null`
    // there and never `0`, so the page may not print a total it did not receive.
    stubLiveReads({
      summary: () => Promise.resolve({ account: { total_equity: 65000, available_balance: 40000 } }),
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
    expect(screen.queryByText(/could not be read/)).toBeNull();
    expect(heading.textContent).not.toContain('not read');
  });
});
