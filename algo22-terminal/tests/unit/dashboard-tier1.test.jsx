/**
 * @fileoverview Dashboard — the single read, the one failure state and tier 1.
 *
 * vyomquant-ui-redesign task 19.1 part A. design.md §7.1, §11.1.
 * Requirements 3.1, 3.4, 3.6, 14.5, 19.3.
 *
 * WHAT IS PINNED HERE, AND WHY EACH CLAIM IS THE ONE WORTH PINNING
 * ===============================================================
 *   1. **One read.** `dashboardApi.getDashboard` is called once per question, with the
 *      environment and the equity window the page's two controls select. The period control
 *      used to issue a second `getDashboard` of its own, which is what made a second failure
 *      surface possible for one figure.
 *   2. **One tier-1 container, four figures, in declared order** (Requirement 3.4). The
 *      containment clause is asserted from the rendered DOM rather than from the JSX, because
 *      "no second row of equally-weighted tier-1 cards" is a claim about where an element is.
 *      Property 4 (task 19.5) generalises it over arbitrary payloads.
 *   3. **An absent figure is a marker with a reason, never a zero** (Requirement 14.5). The
 *      drawdown is the case that matters: BC-1's `current_drawdown_pct_v2` is `null` when no
 *      drawdown can be measured, and its deprecated neighbour `current_drawdown_pct` — which
 *      publishes today's RETURN — is on the same body and must not be read.
 *   4. **`overview.today_pnl` is one field.** Asserted twice: once where the server's figure
 *      disagrees with `today_realized_pnl + unrealized_pnl`, and once where the server sent
 *      the two parts but not the total. A page that recomputed would pass neither.
 *   5. **A 503 is ONE page-level error state with retry** (Requirement 3.6), with no
 *      per-panel error, no panel at all, and nothing left on screen from the read that
 *      worked (Requirement 14.5).
 *
 * Part B rebuilds tier 2 onto `ds/Panel` and `ds/DataTable`; the tier-2 zones are covered by
 * `dashboard_phase2a_ui`, `dashboard_phase2c_safety_realtime` and `dashboard_phase2d_polish`
 * and are deliberately not re-asserted here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Dashboard from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import { ApiError } from '../../src/apiClient';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../src/design/pageFields';
import { PAGE_HIERARCHY_BY_PAGE, tierSelector } from '../../src/design/pageHierarchy';

/*
 * recharts is stubbed for the same reason `dashboard_phase2a_ui` stubs it: the equity curve
 * is still the pre-part-B `AreaChart` and recharts needs layout APIs jsdom does not
 * implement. Nothing below asserts anything about the chart.
 */
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div data-testid="responsive-container">{children}</div>,
  AreaChart: ({ children }) => <div data-testid="area-chart">{children}</div>,
  Area: () => <div data-testid="area" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
}));

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION, AND THE FIXTURE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** §7.1's tier 1, read from the declaration rather than retyped. */
const TIER_ONE = PAGE_HIERARCHY_BY_PAGE[PAGES.DASHBOARD].tiers.filter((e) => e.tier === 1);

const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DASHBOARD, field })];

/**
 * A `GET /api/dashboard` body.
 *
 * The four tier-1 paths carry values that disagree with each other on purpose: `today_pnl`
 * (400) is NOT `today_realized_pnl + unrealized_pnl` (550), and `current_drawdown_pct_v2`
 * (3.2) is not `current_drawdown_pct` (1.22). A page reading the wrong field, or deriving a
 * figure it was handed, renders a number that appears nowhere in the declaration.
 */
const body = ({ overview = {}, risk = {} } = {}) => ({
  environment: 'live',
  overview: {
    total_value: 45250,
    total_equity: 45250,
    today_pnl: 400,
    today_realized_pnl: 250,
    unrealized_pnl: 300,
    cumulative_pnl: 5250,
    currency: 'USDT',
    ...overview,
  },
  positions: [],
  executions: [],
  degraded: null,
  risk: {
    current_drawdown_pct_v2: 3.2,
    current_drawdown_pct: 1.22,
    open_positions_count: 0,
    circuit_breaker_armed: true,
    kill_switch_active: false,
    ...risk,
  },
  health: { exchange_api_latency_ms: null, order_state_sync_status: 'active' },
  exchange: { total_exchanges: 0, connected_exchanges: 0, exchanges: [] },
  strategies: { total: 0, active: 0, items: [] },
  recent_activity: { signals: [], insights: [] },
  equity_curve: [],
});

const read = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');

const mount = () => render(<MemoryRouter><Dashboard /></MemoryRouter>);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS — the same shape `portfolio-rendering.test.jsx` uses
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const tierOneContainer = () => document.querySelector(tierSelector(PAGES.DASHBOARD, 1));

/** The `ds/Metric` root for a label. Exactly one, or the assertion says how many there were. */
const metricFor = (label) => {
  const roots = screen.getAllByText(label)
    .map((node) => node.closest('[data-metric-tier]'))
    .filter(Boolean);
  expect(roots.length, `${label} matched ${roots.length} metrics`).toBe(1);
  return roots[0];
};

/** The figure text of one metric, or `null` when it rendered the not-available marker. */
const figureOf = (label) => {
  const metric = metricFor(label);
  return metric.getAttribute('data-metric-available') === 'true'
    ? metric.textContent.replace(label, '').trim()
    : null;
};

const markerIn = (label) =>
  metricFor(label).querySelector('[data-metric-marker="not-available"]');

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE ONE READ
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard — the single read (task 19.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('issues one `getDashboard` per question, with the environment and the equity window', async () => {
    const spy = read().mockResolvedValue(body());

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // ONE read for the mounted page, not one per panel — §7.1's whole point.
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ environment: 'live', equity_days: 30 });

    // The period is a parameter of that read. It used to be a control on the equity panel
    // that issued a getDashboard of its own and wrote the series from it.
    fireEvent.click(screen.getByRole('radio', { name: '1W' }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy).toHaveBeenLastCalledWith({ environment: 'live', equity_days: 7 });
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * TIER 1 — Requirements 3.1 and 3.4
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tier 1 (task 19.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders every declared tier-1 field, and no tier-1 figure outside the one container', async () => {
    read().mockResolvedValue(body());

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // ONE container. Requirement 3.4 is "one row of four figures, not two", and two
    // containers would make the second row possible again however the first one looks.
    expect(document.querySelectorAll(tierSelector(PAGES.DASHBOARD, 1))).toHaveLength(1);

    const container = tierOneContainer();
    expect(TIER_ONE).toHaveLength(4);

    for (const { label } of TIER_ONE) {
      const metric = metricFor(label);
      expect(metric, `${label} did not render`).not.toBeNull();
      expect(container.contains(metric), `${label} rendered outside the tier-1 container`)
        .toBe(true);
      expect(metric.dataset.metricTier).toBe('1');
    }

    // And nothing else on the page claims tier 1 — the clause that makes the requirement
    // structural rather than a thing a reviewer checks.
    const figures = document.querySelectorAll('[data-metric-tier="1"]');
    expect(figures).toHaveLength(TIER_ONE.length);
    for (const figure of figures) {
      expect(container.contains(figure)).toBe(true);
    }
  });

  it('renders the four figures in `pageHierarchy` order, from their declared paths', async () => {
    read().mockResolvedValue(body());

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const rendered = [...tierOneContainer().querySelectorAll('[data-metric-tier="1"]')];
    expect(rendered.map((node) => node.querySelector('span').textContent))
      .toEqual(TIER_ONE.map((entry) => entry.label));

    // Each figure is the value at its declared path. `ds/Metric`'s `currency` format renders
    // the grouped digits and puts the denomination in its own span.
    expect(figureOf(declared('portfolioValue').label)).toContain('45,250.00');  // overview.total_value
    expect(figureOf(declared('totalPnl').label)).toContain('5,250.00');         // overview.cumulative_pnl
    expect(figureOf(declared('currentDrawdown').label)).toContain('3.20%');     // risk.*_v2
    // The denomination the server reported, beside the money figures rather than a `$`.
    expect(screen.getAllByText('USDT').length).toBeGreaterThan(0);
  });

  it('reads the drawdown from BC-1\'s v2 field and not its deprecated neighbour', async () => {
    // Both are on the wire: BC-1 left `current_drawdown_pct` in place for its deprecation
    // window, and it publishes `today_return_pct` — so a profitable day reads as a positive
    // drawdown, which is the figure a trader uses to decide whether to cut size.
    read().mockResolvedValue(body());

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    expect(figureOf(declared('currentDrawdown').label)).toContain('3.20%');
    expect(document.body.textContent).not.toContain('1.22');
  });

  it('renders the marker with its declared reason, never `0`, when the drawdown is null', async () => {
    // `null` is what BC-1 answers when no drawdown can be measured — no equity series, one
    // point, or no positive peak. `0.0` means the account is AT its peak and is a reading.
    read().mockResolvedValue(body({ risk: { current_drawdown_pct_v2: null } }));

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const label = declared('currentDrawdown').label;
    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(figureOf(label)).toBeNull();
    expect(markerIn(label).getAttribute('title')).toBe(declared('currentDrawdown').reason);
    // Not a zero, and not the deprecated neighbour's value either.
    expect(metricFor(label).textContent).not.toContain('0.00%');
    expect(document.body.textContent).not.toContain('1.22');
    // The marker is IN the tier-1 container: an absent figure keeps its place in the row
    // (Requirement 19.3's state is a rendered element).
    expect(tierOneContainer().contains(metricFor(label))).toBe(true);
  });

  it('renders `overview.today_pnl` as one field rather than recomputing it', async () => {
    // The fixture's `today_pnl` (400) disagrees with `today_realized_pnl + unrealized_pnl`
    // (550). `pageFields` says why the sum must not be computed here: two definitions of one
    // figure drift, and the server's is the one every other surface quotes.
    read().mockResolvedValue(body());

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    expect(figureOf(declared('todayPnl').label)).toContain('400.00');
    expect(document.body.textContent).not.toContain('550.00');
  });

  it('renders the marker when only today\'s P&L PARTS arrive, rather than their sum', async () => {
    read().mockResolvedValue(body({
      overview: { today_pnl: null, today_realized_pnl: 250, unrealized_pnl: 300 },
    }));

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const label = declared('todayPnl').label;
    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(document.body.textContent).not.toContain('550.00');
  });

  it('renders four markers and no zeros when the read answers a body with nothing in it', async () => {
    // A 2xx carrying no account state is a successful read of nothing, not a failure. Every
    // figure is the marker with its own reason; none of them is `0`.
    read().mockResolvedValue({});

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    for (const { label } of TIER_ONE) {
      expect(metricFor(label).dataset.metricAvailable, `${label} rendered a value`).toBe('false');
    }
    expect(document.querySelectorAll('[data-metric-marker="not-available"]')).toHaveLength(4);
    // No failure was reported, so no error state is claimed either.
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE ONE FAILURE STATE — Requirements 3.6 and 14.5
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard — one read means one failure state (task 19.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  const serviceUnavailable = () =>
    new ApiError('The dashboard read failed.', { status: 503 });

  it('renders exactly one page-level error state with retry when the read 503s', async () => {
    read().mockRejectedValue(serviceUnavailable());

    mount();

    await waitFor(() => expect(document.querySelectorAll('[role="alert"]')).toHaveLength(1));

    // ONE error state for the page. Per-panel errors would imply independent reads that do
    // not exist, so there is no panel on the page at all in this state.
    const alert = document.querySelector('[role="alert"]');
    expect(alert.getAttribute('data-region')).toBe('page-error');
    expect(alert.getAttribute('data-error-retryable')).toBe('true');
    expect(screen.getByRole('button', { name: /try again/i })).toBeDefined();

    // No panel, and therefore no panel-level error.
    expect(document.querySelectorAll('[data-panel-state]')).toHaveLength(0);
    expect(document.querySelectorAll('[data-panel-state="error"]')).toHaveLength(0);
    // No figures and no tables: the body is replaced, not banner-ed.
    expect(document.querySelectorAll('[data-metric-tier]')).toHaveLength(0);
    expect(document.querySelectorAll('table')).toHaveLength(0);
  });

  it('offers the retry, and re-reads through the same one read', async () => {
    const spy = read()
      .mockRejectedValueOnce(serviceUnavailable())
      .mockResolvedValue(body());

    mount();

    await waitFor(() => expect(document.querySelectorAll('[role="alert"]')).toHaveLength(1));

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());
    expect(spy).toHaveBeenCalledTimes(2);
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
    expect(figureOf(declared('portfolioValue').label)).toContain('45,250.00');
  });

  it('drops every figure from the read that worked when a later read fails', async () => {
    // Requirement 14.5's case, end to end: `usePanelState` discards the payload, the page
    // clears the zones projected out of it, and the failure branch renders instead of the
    // body — so there is no markup left that could show the previous reading.
    const spy = read()
      .mockResolvedValueOnce(body())
      .mockRejectedValue(serviceUnavailable());

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());
    expect(figureOf(declared('portfolioValue').label)).toContain('45,250.00');

    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    await waitFor(() => expect(document.querySelectorAll('[role="alert"]')).toHaveLength(1));
    expect(spy).toHaveBeenCalledTimes(2);

    // Nothing from the successful read survives: not the figures, not the denomination.
    expect(tierOneContainer()).toBeNull();
    expect(document.body.textContent).not.toContain('45,250.00');
    expect(document.body.textContent).not.toContain('5,250.00');
    expect(document.querySelectorAll('[data-panel-state]')).toHaveLength(0);
  });
});
