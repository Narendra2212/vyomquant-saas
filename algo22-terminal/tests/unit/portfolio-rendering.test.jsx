/**
 * @fileoverview Portfolio Page CSS and Typography Validation
 * Static analysis test to verify Portfolio.jsx uses proper CSS values and typography tokens
 * @version 1.0.0
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Portfolio from '../../src/pages/Portfolio';
import * as portfolioModule from '../../src/api/modules/portfolio';
import * as paperModule from '../../src/api/modules/paper';

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
    
    // Check for proper Tailwind typography classes
    const expectedClasses = [
      'text-heading-lg',
      'text-caption-sm', 
      'text-body',
      'text-caption',
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

  const stubLiveReads = ({ summary, positions }) => {
    vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockImplementation(() => summary());
    vi.spyOn(portfolioModule.portfolioApi, 'getOpenPositions').mockImplementation(() => positions());
    vi.spyOn(portfolioModule.portfolioApi, 'getPositions').mockImplementation(() => positions());
    vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue([]);
    vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue([]);
    vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue([]);
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a figure the response did not carry as text, never as 0 or $0.00', async () => {
    // The body arrived and carried no figures at all - the case the removed `?? 0` / `?? 100000`
    // fallbacks used to turn into a displayed balance.
    stubLiveReads({
      summary: () => Promise.resolve({ account: {} }),
      positions: () => Promise.resolve([]),
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
      positions: () => Promise.reject(new Error('positions upstream timed out')),
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
      positions: () => Promise.resolve([]),
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
      positions: () => Promise.resolve([]),
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
});
