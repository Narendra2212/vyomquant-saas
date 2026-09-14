/**
 * @fileoverview Live Trading — the page shell, the LIVE strip and tier 1.
 *
 * vyomquant-ui-redesign task 20.1 part A. design.md §7.5, §8.1, §8.2, §11.1.
 * Requirements 1.3, 7.1, 7.4, 14.5, 19.3.
 *
 * WHAT IS PINNED HERE
 * ===================
 *   1. **Requirement 7.1's six figures, in the declared order, inside ONE container.** The
 *      order IS the requirement — "is it running, and against what?" — so it is asserted
 *      from the rendered DOM rather than from the JSX, and no tier-1 figure is allowed
 *      outside the single container.
 *   2. **`account` renders the marker, never a value.** Its declared verdict is
 *      `UNAVAILABLE`: nothing on either read reports which exchange account is trading, and
 *      a masked number assembled client-side would be an invented identifier for a
 *      real-money account.
 *   3. **An absent `positions[].environment` renders `ENVIRONMENT UNCONFIRMED`.** §8.1
 *      resolves the environment only from a server field; defaulting to LIVE is alarmist and
 *      defaulting to PAPER is dangerous.
 *   4. **The LIVE strip is on the page** (Requirement 1.3).
 *   5. **A failed read is ONE page-level error state with retry, and no table** —
 *      Requirement 14.5's case. Both reads feed one statement, so half of it is not shown.
 *   6. **The venue-key status and this browser's socket are two separately labelled
 *      readings.** `exchange.exchanges[].status` is a hardcoded constant in the aggregation
 *      service and `useConnectionStatus()` is a fact about this browser, so neither is
 *      evidence the exchange is reachable and the page must not merge them.
 *
 * Tiers 2 and 3 are later parts of task 20.1 and are asserted nowhere below.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import LiveTrading from '../../src/pages/LiveTrading';
import * as dashboardModule from '../../src/api/modules/dashboard';
import { strategiesApi } from '../../src/api/modules/strategies';
import { ApiError } from '../../src/apiClient';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../src/design/pageFields';
import { PAGE_HIERARCHY_BY_PAGE, tierSelector } from '../../src/design/pageHierarchy';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION, AND THE TWO FIXTURES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** §7.5's tier 1, read from the declaration rather than retyped. */
const TIER_ONE = PAGE_HIERARCHY_BY_PAGE[PAGES.LIVE_TRADING].tiers.filter((e) => e.tier === 1);

const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.LIVE_TRADING, field })];

/**
 * A `GET /api/dashboard` body: one configured venue key and one open position.
 *
 * The position's `environment` is the ONLY environment field the page may read for its
 * tier-1 figure, so the fixture's top-level `environment` deliberately disagrees with
 * nothing — it is there because the real payload carries it, and a page reading it instead
 * would pass this file's third test with the wrong field.
 */
const dashboardBody = ({ exchanges, positions } = {}) => ({
  environment: 'live',
  exchange: {
    total_exchanges: 1,
    connected_exchanges: 1,
    exchanges: exchanges ?? [
      { exchange_id: 'binance', status: 'connected', latency_ms: 35, last_sync: null },
    ],
  },
  positions: positions ?? [
    { id: 'p1', symbol: 'BTC/USDT', environment: 'live', contracts: 0.25 },
  ],
  executions: [],
  degraded: null,
});

/** `GET /api/strategies` → `{strategies, total, ...}`. One strategy, named and versioned. */
const strategiesBody = ({ strategies } = {}) => ({
  strategies: strategies ?? [
    { id: 's1', name: 'Momentum Breakout', version: 4, symbol: 'BTC/USDT' },
  ],
  total: 1,
});

const readDashboard = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');
const readStrategies = () => vi.spyOn(strategiesApi, 'list');

const mount = () => render(<MemoryRouter><LiveTrading /></MemoryRouter>);

/** Both reads resolving, which is the state five of the six tests below start from. */
const bothRead = (overrides = {}) => {
  readDashboard().mockResolvedValue(overrides.dashboard ?? dashboardBody());
  readStrategies().mockResolvedValue(overrides.strategies ?? strategiesBody());
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS — `dashboard-tier1.test.jsx`'s shape
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const tierOneContainer = () => document.querySelector(tierSelector(PAGES.LIVE_TRADING, 1));

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
 * TIER 1 — Requirement 7.1
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('LiveTrading tier 1 (task 20.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders the six declared fields in declaration order, inside ONE container', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // ONE container. A second one would make a second row of equally-weighted tier-1
    // figures possible again however the first one looks.
    expect(document.querySelectorAll(tierSelector(PAGES.LIVE_TRADING, 1))).toHaveLength(1);

    const container = tierOneContainer();
    expect(TIER_ONE).toHaveLength(6);

    // Document order over the six declared slots. Each carries `data-region` spelled as its
    // `pageFields` key — including the environment slot, which is a
    // `ds/TradingEnvironmentBadge` rather than a `ds/Metric` (§8.2).
    const slots = [...container.querySelectorAll('[data-region]')];
    expect(slots.map((node) => node.getAttribute('data-region')))
      .toEqual(TIER_ONE.map((entry) => entry.key));

    // Every declared label is rendered, and nothing outside this container claims tier 1.
    for (const { label } of TIER_ONE) {
      expect(screen.getAllByText(label).length, `${label} did not render`).toBeGreaterThan(0);
    }
    const figures = document.querySelectorAll('[data-metric-tier="1"]');
    expect(figures.length).toBeGreaterThan(0);
    for (const figure of figures) {
      expect(container.contains(figure), 'a tier-1 figure rendered outside the container')
        .toBe(true);
    }
  });

  it('renders `account` as the marker with its declared reason, and the rest from their declared paths', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // `account` — declared `verdict: UNAVAILABLE`. No path is read and no value is shown:
    // nothing reports which exchange account is trading.
    const accountLabel = declared('account').label;
    expect(metricFor(accountLabel).dataset.metricAvailable).toBe('false');
    expect(figureOf(accountLabel)).toBeNull();
    expect(markerIn(accountLabel).getAttribute('title')).toBe(declared('account').reason);
    // The marker keeps its place in the row (Requirement 19.3's state is an element).
    expect(tierOneContainer().contains(metricFor(accountLabel))).toBe(true);

    // The four readings, each from its declared path.
    expect(figureOf(declared('connectionState').label)).toContain('connected');
    expect(figureOf(declared('exchange').label)).toContain('binance');
    expect(figureOf(declared('market').label)).toContain('BTC/USDT');
    // `strategies[].name` with `strategies[].version` — the strategy's CURRENT version, and
    // not labelled a deployed one anywhere on the page.
    expect(figureOf(declared('strategy').label)).toContain('Momentum Breakout');
    expect(figureOf(declared('strategy').label)).toContain('v4');
    expect(document.body.textContent.toLowerCase()).not.toContain('deployed version');
  });

  it('renders ENVIRONMENT UNCONFIRMED when no position carries an environment', async () => {
    // §8.1: resolved only from a server field. The body still says `environment: 'live'` at
    // the top level and the route is the live ledger — neither is this field's source, and
    // neither may stand in for it.
    bothRead({
      dashboard: dashboardBody({
        positions: [{ id: 'p1', symbol: 'BTC/USDT', contracts: 0.25 }],
      }),
    });

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const slot = tierOneContainer().querySelector('[data-region="tradingEnvironment"]');
    const badge = slot.querySelector('[data-environment]');
    expect(badge.getAttribute('data-environment')).toBe('UNCONFIRMED');
    expect(badge.textContent).toContain('ENVIRONMENT UNCONFIRMED');
    // Not a default in either direction: no PAPER, and no LIVE borrowed from the strip.
    expect(badge.textContent).not.toContain('PAPER');
    expect(badge.textContent).not.toContain('LIVE');
  });

  it('renders the LIVE strip under the page header (Requirement 1.3)', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    const strip = document.querySelector('[data-environment-variant="strip"]');
    expect(strip).not.toBeNull();
    expect(strip.getAttribute('data-environment')).toBe('LIVE');
    // Announced once, and it is the page's statement about the ROUTE — so it is outside
    // both the header and the tier-1 container.
    expect(strip.getAttribute('role')).toBe('status');
    expect(strip.closest('[data-ds="page-header"]')).toBeNull();
    expect(tierOneContainer().contains(strip)).toBe(false);
  });

  it('reports the venue-key status and this browser\'s socket as two separate readings', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());

    // The declared figure, inside the tier container, with the caveat on it: the status is
    // a constant in the aggregation service, so it confirms a key is configured.
    const connection = metricFor(declared('connectionState').label);
    expect(tierOneContainer().contains(connection)).toBe(true);
    expect(connection.querySelector('[title]').getAttribute('title'))
      .toContain('not that the venue answered');

    // This browser's socket — a different fact, its own label, and NOT a tier-1 figure.
    const client = document.querySelector('[data-client-reading="browser-socket"]');
    expect(client).not.toBeNull();
    expect(client.textContent).toContain('This browser\'s stream');
    expect(client.querySelector('[data-metric-tier]')).toBeNull();
    expect(tierOneContainer().contains(client)).toBe(false);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * TWO READS, ONE FAILURE STATE — Requirement 14.5
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('LiveTrading — one failure state for two reads (task 20.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders one page-level error state with retry, and no table, when a read fails', async () => {
    const dashboard = readDashboard()
      .mockRejectedValueOnce(new ApiError('The dashboard read failed.', { status: 503 }))
      .mockResolvedValue(dashboardBody());
    const strategies = readStrategies().mockResolvedValue(strategiesBody());

    mount();

    await waitFor(() => expect(document.querySelectorAll('[role="alert"]')).toHaveLength(1));

    const alert = document.querySelector('[role="alert"]');
    expect(alert.getAttribute('data-region')).toBe('page-error');
    expect(alert.getAttribute('data-error-retryable')).toBe('true');

    // The body is replaced, not banner-ed: no tier container, no figure, no marker, no
    // table — and nothing from the read that DID succeed is left on screen.
    expect(tierOneContainer()).toBeNull();
    expect(document.querySelectorAll('[data-metric-tier]')).toHaveLength(0);
    expect(document.querySelectorAll('[data-metric-marker="not-available"]')).toHaveLength(0);
    expect(document.querySelectorAll('table')).toHaveLength(0);
    expect(document.querySelectorAll('[data-panel-state]')).toHaveLength(0);
    expect(document.body.textContent).not.toContain('Momentum Breakout');

    // The retry re-issues BOTH reads: the six figures are one statement about one running
    // thing, so recovering half of it would not answer Requirement 7.1.
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());
    expect(dashboard).toHaveBeenCalledTimes(2);
    expect(strategies).toHaveBeenCalledTimes(2);
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
  });
});
