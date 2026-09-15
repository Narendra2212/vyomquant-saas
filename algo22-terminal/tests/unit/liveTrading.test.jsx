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
 * AND WHAT PART B ADDS (task 20.1b — tier 2, Requirements 7.2, 7.4, 12.2, 14.4)
 * =============================================================================
 *   7. **Requirement 7.2's seven figures, in the declared order, inside ONE container that
 *      follows every tier-1 element.** Same shape as (1), because the tier order IS §7.5's
 *      answer to "is it running?" then "what is the position doing?".
 *   8. **`degraded` non-null renders the server's own sentence and NO flat position.**
 *      `positions: []` is what a failed positions read answers as well as an account holding
 *      nothing, and BC-2's marker is the only thing that separates them.
 *   9. **The realised-P&L pair.** The account-wide figure renders under its full declared
 *      label and the per-deployment field renders its own marker beside it. Both
 *      `overview.today_realized_pnl` and BC-5's `overview.realized_pnl` are account-wide
 *      sums, so attributing either to a deployment is the fabrication.
 *  10. **A spot position's liquidation distance renders the marker with the SPOT reason.** A
 *      blank cell beside a leveraged position reads as "no liquidation risk", so the reason
 *      has to say which absence this is.
 *  11. **The tier-2 panel carries an environment chip** (Requirements 7.4, 12.2).
 *
 * AND WHAT PART D ADDS (task 20.1d — the deployment selector, Requirements 14.1, 19.4)
 * =====================================================================================
 *  17. **The selector renders ABOVE tier 1 and carries NO tier.** `pageHierarchy` registers
 *      `deployment` as untiered precisely because it is a control that chooses what the tiers
 *      describe, so a tier attribute anywhere inside it would put a control into Property 4's
 *      ordering of figures.
 *  18. **Two strategies' deployments appear as ONE union, at one call per strategy.** The read
 *      is per strategy — "there is no single read that returns every deployment a trader
 *      has" — and an absent field on a record is the marker, never `0` and never the cheerful
 *      `"healthy"` default this codebase has already been caught inventing for that field.
 *  19. **A selection re-scopes `strategy`, `market` and `latestSignal` and NOTHING else.** The
 *      account-wide slots keep their figures, their labels and their hints word for word,
 *      because `overview.today_realized_pnl` and `risk.risk_level` are account-wide sums and
 *      `positions[]` carries no deployment key. Inventing an attribution there is the
 *      fabrication this page is built around.
 *  20. **One strategy's failed read keeps the others' rows, and is disclosed.** Erasing every
 *      deployment because one strategy's read failed is part C's erasure one level down.
 *  21. **Zero deployments renders `ds/EmptyState`, not an empty table**, quoting the declared
 *      reason as what the server actually answered (Requirement 14.1).
 *  22. **The in-process-registry incompleteness is on the surface, once**, and says what to do
 *      about it rather than only admitting it.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import LiveTrading from '../../src/pages/LiveTrading';
import * as dashboardModule from '../../src/api/modules/dashboard';
import { ordersApi } from '../../src/api/modules/orders';
import { strategiesApi } from '../../src/api/modules/strategies';
import { ApiError } from '../../src/apiClient';
// Task 20.3: the authored copy a failed mutation must render INSTEAD of `err.message`, taken
// from the module that owns it rather than retyped here (Requirement 14.4).
import { CATEGORY_COPY } from '../../src/design/errorCopy';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../src/design/pageFields';
import {
  PAGE_HIERARCHY_BY_PAGE,
  UNTIERED_FIELDS_BY_PAGE,
  tierSelector,
} from '../../src/design/pageHierarchy';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION, AND THE TWO FIXTURES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/** §7.5's tiers, read from the declaration rather than retyped. */
const TIER_ONE = PAGE_HIERARCHY_BY_PAGE[PAGES.LIVE_TRADING].tiers.filter((e) => e.tier === 1);
const TIER_TWO = PAGE_HIERARCHY_BY_PAGE[PAGES.LIVE_TRADING].tiers.filter((e) => e.tier === 2);
const TIER_THREE = PAGE_HIERARCHY_BY_PAGE[PAGES.LIVE_TRADING].tiers.filter((e) => e.tier === 3);

const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.LIVE_TRADING, field })];

/**
 * A `GET /api/dashboard` body: one configured venue key and one open position.
 *
 * The position's `environment` is the ONLY environment field the page may read for its
 * tier-1 figure, so the fixture's top-level `environment` deliberately disagrees with
 * nothing — it is there because the real payload carries it, and a page reading it instead
 * would pass this file's third test with the wrong field.
 */
const dashboardBody = ({ exchanges, positions, overview, risk, degraded } = {}) => ({
  environment: 'live',
  exchange: {
    total_exchanges: 1,
    connected_exchanges: 1,
    exchanges: exchanges ?? [
      { exchange_id: 'binance', status: 'connected', latency_ms: 35, last_sync: null },
    ],
  },
  /*
   * ONE open position, with every field tier 2's seven slots declare a path or an input
   * into. `market_type: 'future'` and a `liquidation_price` are what make the derived
   * distance computable at all — the spot case is its own test below.
   *
   * Long, so the distance is ((62000 - 49600) / 62000) × 100 = 20.0%.
   */
  positions: positions ?? [
    {
      id: 'p1',
      symbol: 'BTC/USDT',
      environment: 'live',
      market_type: 'future',
      side: 'long',
      contracts: 0.25,
      entry_price: 61000,
      mark_price: 62000,
      notional: 15500,
      unrealized_pnl: 250.5,
      unrealized_pnl_pct: 1.64,
      liquidation_price: 49600,
    },
  ],
  // The two ACCOUNT-WIDE blocks. Neither is per deployment, and tier 2 says so.
  overview: overview ?? { today_realized_pnl: 412.75, currency: 'USDT' },
  risk: risk ?? { risk_level: 'elevated' },
  executions: [],
  degraded: degraded ?? null,
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
/** The third read (task 20.1c). The response root IS the ccxt order array — no envelope. */
const readOrders = () => vi.spyOn(ordersApi, 'getOpenOrders');
/** The fourth read (task 20.1d), and it is PER STRATEGY: one call per strategy id. */
const readDeployments = () => vi.spyOn(strategiesApi, 'listDeployments');

/** `{strategy_id, deployments, total}` — the shape `list_deployments` answers. */
const deploymentsBody = (strategyId, deployments = []) => ({
  strategy_id: strategyId,
  deployments,
  total: deployments.length,
});

/** The default fan-out answer: this strategy holds none. */
const noDeployments = (strategyId) => Promise.resolve(deploymentsBody(strategyId, []));

const mount = () => render(<MemoryRouter><LiveTrading /></MemoryRouter>);

/**
 * All four reads resolving, which is the state most of the tests below start from.
 *
 * The orders read defaults to `[]` — a venue holding no open order — and the deployment
 * fan-out to no deployment per strategy, so no test that is not about tier 3 has an order in
 * its DOM, no test that is not about the selector has a deployment row in its DOM, and none of
 * them reaches the network. `overrides.deployments` is a function of the strategy id, because
 * the read is per strategy and the union's whole subject is what the several answers together
 * do (and do not) contain.
 */
const bothRead = (overrides = {}) => {
  readDashboard().mockResolvedValue(overrides.dashboard ?? dashboardBody());
  readStrategies().mockResolvedValue(overrides.strategies ?? strategiesBody());
  readOrders().mockResolvedValue(overrides.orders ?? []);
  readDeployments().mockImplementation(overrides.deployments ?? noDeployments);
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS — `dashboard-tier1.test.jsx`'s shape
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const tierOneContainer = () => document.querySelector(tierSelector(PAGES.LIVE_TRADING, 1));
const tierTwoContainer = () => document.querySelector(tierSelector(PAGES.LIVE_TRADING, 2));
const tierThreeContainer = () => document.querySelector(tierSelector(PAGES.LIVE_TRADING, 3));

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
    // Mocked because the retry below recovers tier 1, which reports a venue, which issues the
    // third read (task 20.1c). Unmocked it would reach the network from jsdom. The deployment
    // fan-out (task 20.1d) is mocked for the same reason: the recovered strategies read gives
    // it a strategy id to ask about.
    readOrders().mockResolvedValue([]);
    readDeployments().mockImplementation(noDeployments);

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

/* ══════════════════════════════════════════════════════════════════════════════════════
 * TIER 2 — Requirement 7.2, and the realised-P&L distinction Requirement 14.5 turns on
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('LiveTrading tier 2 (task 20.1b)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders the seven declared fields in order, in ONE container, after every tier-1 element', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierTwoContainer()).not.toBeNull());

    // ONE container, so a second row of tier-2 money figures cannot appear beside it.
    expect(document.querySelectorAll(tierSelector(PAGES.LIVE_TRADING, 2))).toHaveLength(1);
    expect(TIER_TWO).toHaveLength(7);

    // Document order over the seven declared slots, each `data-region` spelled as its
    // `pageFields` key — including `realisedPnlPerDeployment`, whose marker keeps its
    // declared place in the row (Requirement 19.3's state is a rendered element).
    const slots = [...tierTwoContainer().querySelectorAll('[data-region]')];
    expect(slots.map((node) => node.getAttribute('data-region')))
      .toEqual(TIER_TWO.map((entry) => entry.key));

    // Tier 2 FOLLOWS tier 1: the tier order is the requirement, so it is asserted from the
    // rendered DOM and not from the JSX. Every tier-1 element precedes this container.
    const documentOrder = [...document.querySelectorAll('*')];
    const tierTwoAt = documentOrder.indexOf(tierTwoContainer());
    expect(documentOrder.indexOf(tierOneContainer())).toBeLessThan(tierTwoAt);
    for (const element of tierOneContainer().querySelectorAll('*')) {
      expect(documentOrder.indexOf(element)).toBeLessThan(tierTwoAt);
    }

    // And nothing outside this container claims tier 2.
    const figures = document.querySelectorAll('[data-metric-tier="2"]');
    expect(figures.length).toBe(7);
    for (const figure of figures) {
      expect(tierTwoContainer().contains(figure), 'a tier-2 figure rendered outside the container')
        .toBe(true);
    }
  });

  it('reads every tier-2 figure from its declared path, and derives the liquidation distance', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierTwoContainer()).not.toBeNull());

    // `positions[].contracts` with `positions[].side` — the pair, never a size carrying a
    // side borrowed from another position.
    expect(figureOf(declared('position').label)).toContain('long 0.25');
    // `positions[].unrealized_pnl`, `positions[].notional`, `risk.risk_level`.
    expect(figureOf(declared('unrealisedPnl').label)).toContain('250.50');
    expect(figureOf(declared('exposure').label)).toContain('15,500.00');
    expect(figureOf(declared('riskState').label)).toContain('elevated');
    // DERIVED from `mark_price` and `liquidation_price`: ((62000 − 49600) / 62000) × 100.
    expect(figureOf(declared('liquidationDistance').label)).toContain('20.0%');
    // No zero anywhere: every slot is either one of these readings or a marker.
    expect(document.querySelectorAll('[data-page-tier="2"] [data-metric-available="true"]'))
      .toHaveLength(6);
  });

  it('renders the account-wide realised P&L under its full label, and the per-deployment field as a marker', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierTwoContainer()).not.toBeNull());

    // The account figure IS shown — and the label is part of the declaration, because the
    // same number under a bare "Realised P&L" beside a deployment reads as that
    // deployment's.
    const accountLabel = declared('realisedPnlAccount').label;
    expect(accountLabel).toBe('Realised P&L (account, today)');
    expect(figureOf(accountLabel)).toContain('412.75');

    // The per-deployment field is permanently unavailable and renders ITS OWN reason. It is
    // not the account figure repeated, and it is not blank.
    const perDeploymentLabel = declared('realisedPnlPerDeployment').label;
    const perDeployment = metricFor(perDeploymentLabel);
    expect(perDeployment.dataset.metricAvailable).toBe('false');
    expect(figureOf(perDeploymentLabel)).toBeNull();
    expect(markerIn(perDeploymentLabel).getAttribute('title'))
      .toBe(declared('realisedPnlPerDeployment').reason);
    expect(markerIn(perDeploymentLabel).getAttribute('title'))
      .toContain('not per deployment');

    // Both keep their declared places, in declared order, in the same row.
    const regions = [...tierTwoContainer().querySelectorAll('[data-region]')]
      .map((node) => node.getAttribute('data-region'));
    expect(regions.indexOf('realisedPnlAccount')).toBeLessThan(
      regions.indexOf('realisedPnlPerDeployment'),
    );
  });

  it('renders the server\'s own reason and NO flat position when `degraded` is set', async () => {
    // BC-2: a 200 whose positions read failed. `positions: []` here is NOT an account
    // holding nothing, and the marker is the only thing that says so.
    const serverReason = 'Binance rejected the positions read for the live account: invalid API key.';
    bothRead({
      dashboard: dashboardBody({
        positions: [],
        degraded: { positions: 'unreadable', environment: 'live', reason: serverReason },
      }),
    });

    mount();

    await waitFor(() => expect(tierTwoContainer()).not.toBeNull());

    // The server's account of the failure, verbatim: `translateError` carries no free-form
    // message by design, and the server knows which environment failed (Requirement 14.4).
    const degraded = document.querySelector('[data-positions-read="degraded"]');
    expect(degraded).not.toBeNull();
    expect(degraded.textContent).toContain(serverReason);

    // No flat position: the position slot is the marker, carrying that same sentence — not
    // a size, not a side, and not a zero.
    const positionLabel = declared('position').label;
    expect(metricFor(positionLabel).dataset.metricAvailable).toBe('false');
    expect(figureOf(positionLabel)).toBeNull();
    expect(markerIn(positionLabel).getAttribute('title')).toBe(serverReason);
    expect(tierTwoContainer().textContent).not.toContain('0.25');
    // The same gate covers the other two figures off those rows, for the same reason.
    expect(markerIn(declared('unrealisedPnl').label).getAttribute('title')).toBe(serverReason);
    expect(markerIn(declared('exposure').label).getAttribute('title')).toBe(serverReason);

    // The account-wide figures are NOT gated on the positions marker: they do not come off
    // that list, and suppressing them would report a failure the server did not report.
    expect(figureOf(declared('realisedPnlAccount').label)).toContain('412.75');
    expect(figureOf(declared('riskState').label)).toContain('elevated');
  });

  it('renders the spot reason on the liquidation distance, not a blank cell', async () => {
    // `liquidation_price` is `null` for every spot position. That is a permanent absence,
    // not a failed read, and the reason has to say WHICH absence it is.
    bothRead({
      dashboard: dashboardBody({
        positions: [{
          id: 'p1',
          symbol: 'BTC/USDT',
          environment: 'live',
          market_type: 'spot',
          side: 'long',
          contracts: 1.5,
          mark_price: 62000,
          notional: 93000,
          unrealized_pnl: 120,
          liquidation_price: null,
        }],
      }),
    });

    mount();

    await waitFor(() => expect(tierTwoContainer()).not.toBeNull());

    const label = declared('liquidationDistance').label;
    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(figureOf(label)).toBeNull();
    // The declared sentence, which is the spot one — and it names spot, so a trader cannot
    // read the marker as "this leveraged position has no liquidation risk".
    expect(markerIn(label).getAttribute('title')).toBe(declared('liquidationDistance').reason);
    expect(markerIn(label).getAttribute('title')).toContain('spot');

    // The rest of the row still reads: a spot position has a size, a P&L and an exposure.
    expect(figureOf(declared('position').label)).toContain('long 1.5');
    expect(figureOf(declared('exposure').label)).toContain('93,000.00');
  });

  it('carries an environment chip on the tier-2 money panel (Requirements 7.4, 12.2)', async () => {
    bothRead();

    mount();

    await waitFor(() => expect(tierTwoContainer()).not.toBeNull());

    const panel = document.querySelector('[data-region="tier-2"]');
    expect(panel).not.toBeNull();
    // `money` declared, so `ds/Panel` would have thrown in development had the environment
    // been omitted rather than decided.
    expect(panel.getAttribute('data-panel-money')).toBe('true');
    expect(panel.contains(tierTwoContainer())).toBe(true);

    // §8.2's own badge, from the environment the SERVER labelled the positions with.
    const chip = panel.querySelector('[data-environment-variant="chip"]');
    expect(chip).not.toBeNull();
    expect(chip.getAttribute('data-environment')).toBe('LIVE');
    // The page's announcing instance is the strip, so this one does not announce again.
    expect(chip.getAttribute('role')).toBeNull();
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * TIER 3 — Requirement 7.3, and the two things it must not do
 * ══════════════════════════════════════════════════════════════════════════════════════
 *
 * 12. **Requirement 7.3's three figures, in the declared order, inside ONE container that
 *     follows every tier-2 element.** Same shape as (1) and (7): the tier order IS §7.5's
 *     sequence of questions, and the third of them is "what happened last?".
 * 13. **A signal belonging to another strategy renders the MARKER, not the signal.**
 *     `recent_activity.signals` is account-wide and its declaration says to filter by
 *     strategy id first. Rendering the account's newest signal under "Latest signal" beside
 *     one strategy's name would state that this strategy produced it — the attribution error
 *     this page exists to avoid.
 * 14. **The open-orders read is issued WITH the venue.** `GET /api/orders/open` declares
 *     `exchange_id` as a required query parameter, so the call as `ordersApi` used to write
 *     it answered 422 for everyone. The venue is tier 1's own `exchange` reading.
 * 15. **No single reported venue means NO REQUEST and a marker.** There is no venue whose
 *     order book could be asked, and `"binance"` assumed here would query another account's
 *     keys.
 * 16. **A failed orders read fails IN PLACE.** It feeds one slot, so it takes one slot's
 *     marker with it — the position, the P&L and the risk state were read successfully and
 *     stay on screen. That is the one difference between this read and the two that share the
 *     page-level error state.
 */

describe('LiveTrading tier 3 (task 20.1c)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  /** One ccxt open order, as `DataEngine.fetch_open_orders` returns them — a bare array. */
  const openOrder = (overrides = {}) => ({
    id: 'o1',
    symbol: 'BTC/USDT',
    side: 'buy',
    type: 'limit',
    amount: 0.1,
    price: 60000,
    status: 'open',
    timestamp: 1780000000000,
    datetime: '2026-06-01T10:00:00.000Z',
    ...overrides,
  });

  it('renders the three declared fields in order, in ONE container, after every tier-2 element', async () => {
    bothRead({ orders: [openOrder()] });

    mount();

    await waitFor(() => expect(tierThreeContainer()).not.toBeNull());

    // ONE container, so a second activity row cannot appear beside it.
    expect(document.querySelectorAll(tierSelector(PAGES.LIVE_TRADING, 3))).toHaveLength(1);
    expect(TIER_THREE).toHaveLength(3);

    // Document order over the three declared slots, each `data-region` spelled as its
    // `pageFields` key.
    const slots = [...tierThreeContainer().querySelectorAll('[data-region]')];
    expect(slots.map((node) => node.getAttribute('data-region')))
      .toEqual(TIER_THREE.map((entry) => entry.key));

    // Tier 3 FOLLOWS tier 2, asserted from the rendered DOM rather than from the JSX.
    const documentOrder = [...document.querySelectorAll('*')];
    const tierThreeAt = documentOrder.indexOf(tierThreeContainer());
    expect(documentOrder.indexOf(tierTwoContainer())).toBeLessThan(tierThreeAt);
    for (const element of tierTwoContainer().querySelectorAll('*')) {
      expect(documentOrder.indexOf(element)).toBeLessThan(tierThreeAt);
    }

    // And nothing outside this container claims tier 3.
    const figures = document.querySelectorAll('[data-metric-tier="3"]');
    expect(figures.length).toBe(3);
    for (const figure of figures) {
      expect(tierThreeContainer().contains(figure), 'a tier-3 figure rendered outside the container')
        .toBe(true);
    }
  });

  it('renders the marker for a signal that belongs to another strategy, never the account\'s newest', async () => {
    // Two signals on the ACCOUNT, newest first, exactly as `get_recent_signals` orders them.
    // The newest belongs to another strategy; the older one is this strategy's.
    const dashboard = dashboardBody();
    bothRead({
      dashboard: {
        ...dashboard,
        recent_activity: {
          signals: [
            { id: 'sig-2', strategy_id: 'other', time: '2026-06-01T12:00:00Z', text: 'Signal SELL: ETH/USDT on BINANCE (Risk: APPROVED)' },
            { id: 'sig-1', strategy_id: 's1', time: '2026-06-01T09:00:00Z', text: 'Signal BUY: BTC/USDT on BINANCE (Risk: APPROVED)' },
          ],
          insights: [],
          executions: [],
        },
      },
    });

    mount();

    await waitFor(() => expect(tierThreeContainer()).not.toBeNull());

    // `strategies[].id` is `s1`, so the newest of THIS strategy's signals is the older row.
    // The account's newest — the other strategy's — appears nowhere on the page.
    const label = declared('latestSignal').label;
    expect(figureOf(label)).toContain('Signal BUY: BTC/USDT');
    expect(document.body.textContent).not.toContain('Signal SELL: ETH/USDT');

    // And with the account's signals all attributed elsewhere, the slot is the marker
    // rather than the newest of them.
    cleanup();
    vi.restoreAllMocks();
    bothRead({
      dashboard: {
        ...dashboard,
        recent_activity: {
          signals: [
            { id: 'sig-2', strategy_id: 'other', time: '2026-06-01T12:00:00Z', text: 'Signal SELL: ETH/USDT on BINANCE (Risk: APPROVED)' },
          ],
          insights: [],
          executions: [],
        },
      },
    });

    mount();

    await waitFor(() => expect(tierThreeContainer()).not.toBeNull());

    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(figureOf(label)).toBeNull();
    expect(document.body.textContent).not.toContain('Signal SELL: ETH/USDT');
    // The reason says signals exist and belong to another strategy — not that the account
    // has none, which is what the declared reason would have claimed.
    expect(markerIn(label).getAttribute('title')).toContain('another');
    expect(markerIn(label).getAttribute('title')).not.toBe(declared('latestSignal').reason);
  });

  it('issues the open-orders read WITH the venue tier 1 reports, and reads the bare array', async () => {
    bothRead({ orders: [openOrder({ id: 'o-old', timestamp: 1770000000000, side: 'sell', amount: 2 }), openOrder()] });

    mount();

    await waitFor(() => expect(tierThreeContainer()).not.toBeNull());

    // THE FIX: `exchange_id` is a required query parameter on `GET /api/orders/open`, and the
    // venue is `exchange.exchanges[].exchange_id` — the same reading tier 1 shows.
    const orders = ordersApi.getOpenOrders;
    expect(orders).toHaveBeenCalled();
    for (const call of orders.mock.calls) {
      expect(call[1]).toBe('binance');
    }

    // The response ROOT is the order array, and "latest" is decided by the reported time:
    // the newer `timestamp` wins over the array's own order.
    expect(figureOf(declared('latestOrder').label)).toContain('buy 0.1 BTC/USDT');
    expect(figureOf(declared('latestOrder').label)).not.toContain('sell');
  });

  it('issues NO open-orders request and renders the marker when no single venue is reported', async () => {
    // Two configured venues: `soleReport` shows no single one, so there is no venue whose
    // order book could be asked. Assuming the first would query the wrong keys.
    bothRead({
      dashboard: dashboardBody({
        exchanges: [
          { exchange_id: 'binance', status: 'connected', latency_ms: 35, last_sync: null },
          { exchange_id: 'kraken', status: 'connected', latency_ms: 41, last_sync: null },
        ],
      }),
    });

    mount();

    await waitFor(() => expect(tierThreeContainer()).not.toBeNull());

    expect(ordersApi.getOpenOrders).not.toHaveBeenCalled();

    const label = declared('latestOrder').label;
    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(figureOf(label)).toBeNull();
    // The reason is the non-attempt, not the declared "could not be read": no venue answered
    // badly, because none was asked.
    expect(markerIn(label).getAttribute('title')).toContain('no venue');
    expect(markerIn(label).getAttribute('title')).not.toBe(declared('latestOrder').reason);
  });

  it('fails the orders read IN PLACE, leaving tier 1 and tier 2 reporting', async () => {
    bothRead();
    readOrders().mockRejectedValue(new ApiError('The venue rejected the read.', { status: 503 }));

    mount();

    await waitFor(() => expect(tierThreeContainer()).not.toBeNull());

    // One slot's marker, carrying the DECLARED reason — this is the arm it describes.
    const label = declared('latestOrder').label;
    expect(metricFor(label).dataset.metricAvailable).toBe('false');
    expect(markerIn(label).getAttribute('title')).toBe(declared('latestOrder').reason);

    // NOT the page-level error state: no second `ds/ErrorState`, and the figures that were
    // read successfully are still on screen. Suppressing a real position because a venue's
    // order book failed would withhold a true statement about real money.
    expect(document.querySelector('[data-region="page-error"]')).toBeNull();
    expect(figureOf(declared('position').label)).toContain('long 0.25');
    expect(figureOf(declared('exposure').label)).toContain('15,500.00');
    expect(figureOf(declared('riskState').label)).toContain('elevated');
    expect(figureOf(declared('exchange').label)).toContain('binance');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DEPLOYMENT SELECTOR — untiered, above tier 1 (task 20.1d)
 * ══════════════════════════════════════════════════════════════════════════════════════
 *
 * Items 17-22 of the file docblock. The two facts every test here turns on:
 *
 *   * `strategiesApi.listDeployments` is PER STRATEGY, so the selector is the union over the
 *     strategies the page already lists — N calls, and a partial answer is a real outcome.
 *   * The union's rows can re-scope only what a strategy id can re-scope. The account-wide
 *     figures stay account-wide, and the tests assert their LABELS as well as their values,
 *     because relabelling one of them is how a per-deployment claim would appear.
 */

describe('LiveTrading deployment selector (task 20.1d)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  /** One deployment record, as `list_deployments` publishes them. */
  const deployment = (overrides = {}) => ({
    deployment_id: 'dep-1',
    status: 'running',
    environment: 'live',
    worker: 'worker-a',
    started_at: '2026-06-01T08:00:00.000Z',
    health: 'healthy',
    ...overrides,
  });

  /** Two strategies, so the union is a union rather than one call's answer. */
  const twoStrategies = () => strategiesBody({
    strategies: [
      { id: 's1', name: 'Momentum Breakout', version: 4, symbol: 'BTC/USDT' },
      { id: 's2', name: 'Mean Reversion', version: 2, symbol: 'ETH/USDT' },
    ],
  });

  /** Two signals on the ACCOUNT, one per strategy, newest first. */
  const twoSignals = () => ({
    ...dashboardBody(),
    recent_activity: {
      signals: [
        { id: 'sig-2', strategy_id: 's2', time: '2026-06-01T12:00:00Z', text: 'Signal SELL: ETH/USDT on BINANCE' },
        { id: 'sig-1', strategy_id: 's1', time: '2026-06-01T09:00:00Z', text: 'Signal BUY: BTC/USDT on BINANCE' },
      ],
      insights: [],
      executions: [],
    },
  });

  const selectorPanel = () => document.querySelector('[data-region="deployment"]');
  const selectorRows = () =>
    [...document.querySelectorAll('[data-region="deployment"] tbody tr[data-row-id]')];
  const rowFor = (text) => selectorRows().find((row) => row.textContent.includes(text));

  it('renders the selector ABOVE tier 1, carrying the untiered `deployment` marker and no tier', async () => {
    bothRead({ deployments: (id) => Promise.resolve(deploymentsBody(id, [deployment()])) });

    mount();

    await waitFor(() => expect(selectorRows()).toHaveLength(1));

    // The declaration's own verdict: `deployment` is registered UNTIERED, and the reason it
    // gives is that the selector sits above tier 1.
    expect(UNTIERED_FIELDS_BY_PAGE[PAGES.LIVE_TRADING].map((entry) => entry.field))
      .toContain('deployment');

    const panel = selectorPanel();
    expect(panel).not.toBeNull();
    // NO tier, anywhere inside it — not on the region and not on a figure.
    expect(panel.getAttribute('data-page-tier')).toBeNull();
    expect(panel.querySelector('[data-page-tier]')).toBeNull();
    expect(panel.querySelector('[data-metric-tier]')).toBeNull();

    // ABOVE tier 1, asserted from the rendered DOM rather than from the JSX: every element of
    // the selector precedes the tier-1 container.
    const documentOrder = [...document.querySelectorAll('*')];
    const tierOneAt = documentOrder.indexOf(tierOneContainer());
    expect(documentOrder.indexOf(panel)).toBeLessThan(tierOneAt);
    for (const element of panel.querySelectorAll('*')) {
      expect(documentOrder.indexOf(element)).toBeLessThan(tierOneAt);
    }
  });

  it('lists two strategies\' deployments as ONE union, one call per strategy, absent fields as the marker', async () => {
    bothRead({
      strategies: twoStrategies(),
      deployments: (id) => Promise.resolve(deploymentsBody(id, [
        // s2's record reports no `health` and no `worker`, which is the case that must render
        // the marker rather than `0` or a cheerful default.
        id === 's2'
          ? { deployment_id: 'dep-s2', status: 'running', environment: 'live', started_at: null }
          : deployment({ deployment_id: 'dep-s1' }),
      ])),
    });

    mount();

    await waitFor(() => expect(selectorRows()).toHaveLength(2));

    // PER STRATEGY: one call each, issued once — not once per render.
    expect(strategiesApi.listDeployments).toHaveBeenCalledTimes(2);
    expect(strategiesApi.listDeployments.mock.calls.map((call) => call[0]).sort())
      .toEqual(['s1', 's2']);

    // One table holding both strategies' rows.
    expect(selectorPanel().querySelectorAll('table')).toHaveLength(1);
    expect(rowFor('dep-s1')).not.toBeUndefined();
    expect(rowFor('dep-s2')).not.toBeUndefined();

    // The six columns are the read's own keys, in the order the record carries them.
    const headers = [...selectorPanel().querySelectorAll('thead th[data-column-key]')];
    expect(headers.map((node) => node.getAttribute('data-column-key')))
      .toEqual(['deployment_id', 'status', 'environment', 'worker', 'started_at', 'health']);

    // Absent → the marker. Never `0`, never `"healthy"` assumed.
    for (const key of ['worker', 'started_at', 'health']) {
      const cell = rowFor('dep-s2').querySelector(`[data-column-key="${key}"]`);
      expect(cell.textContent, `${key} did not render the marker`).toContain('—');
      expect(cell.textContent).not.toContain('0');
      expect(cell.textContent.toLowerCase()).not.toContain('healthy');
    }
  });

  it('re-scopes strategy, market and the latest signal on selection, and leaves the account-wide figures alone', async () => {
    bothRead({
      dashboard: twoSignals(),
      strategies: twoStrategies(),
      deployments: (id) => Promise.resolve(
        deploymentsBody(id, id === 's2' ? [deployment({ deployment_id: 'dep-s2' })] : []),
      ),
    });

    mount();

    await waitFor(() => expect(selectorRows()).toHaveLength(1));

    // BEFORE the selection: two strategies are reported, so there is no single one to name and
    // no strategy id to match a signal against. Markers, not an arbitrary pick.
    expect(figureOf(declared('strategy').label)).toBeNull();
    expect(figureOf(declared('market').label)).toBeNull();
    expect(figureOf(declared('latestSignal').label)).toBeNull();

    // The account-wide readings, recorded before the click so the labels and hints can be
    // compared afterwards rather than described twice.
    const riskLabel = declared('riskState').label;
    const riskHint = metricFor(riskLabel).querySelector('[title]').getAttribute('title');

    fireEvent.click(selectorRows()[0]);

    // AFTER: the row names `s2`, so the strategy, its market and the signal filter are that
    // strategy's — and the other strategy's signal is not on the page.
    await waitFor(() => expect(figureOf(declared('strategy').label)).toContain('Mean Reversion'));
    expect(figureOf(declared('strategy').label)).not.toContain('Momentum');
    expect(figureOf(declared('market').label)).toContain('ETH/USDT');
    expect(figureOf(declared('latestSignal').label)).toContain('Signal SELL: ETH/USDT');
    expect(document.body.textContent).not.toContain('Signal BUY: BTC/USDT');
    expect(document.querySelector('[data-selected-deployment]').getAttribute('data-selected-deployment'))
      .toBe('dep-s2');

    // NOT re-scoped, and the labels prove it: both are account-wide, and `positions[]` carries
    // no deployment key at all. Same figures, same labels, same hint as before the click.
    expect(declared('realisedPnlAccount').label).toBe('Realised P&L (account, today)');
    expect(figureOf(declared('realisedPnlAccount').label)).toContain('412.75');
    expect(figureOf(riskLabel)).toContain('elevated');
    expect(metricFor(riskLabel).querySelector('[title]').getAttribute('title')).toBe(riskHint);
    expect(riskHint).toContain('WHOLE ACCOUNT');
    expect(figureOf(declared('position').label)).toContain('long 0.25');
    // And realised P&L per deployment is STILL the marker: a selection does not make an
    // account-wide sum attributable to one deployment.
    expect(metricFor(declared('realisedPnlPerDeployment').label).dataset.metricAvailable)
      .toBe('false');

    // A selection is not a new question for the fan-out, so it re-issues nothing.
    expect(strategiesApi.listDeployments).toHaveBeenCalledTimes(2);
  });

  it('keeps the strategies that answered when one strategy\'s deployment read fails', async () => {
    bothRead({
      strategies: twoStrategies(),
      deployments: (id) => (id === 's1'
        ? Promise.reject(new ApiError('The deployment read failed.', { status: 503 }))
        : Promise.resolve(deploymentsBody(id, [deployment({ deployment_id: 'dep-s2' })]))),
    });

    mount();

    await waitFor(() => expect(selectorRows()).toHaveLength(1));

    // s2's row survives s1's failure. Rejecting the whole union on the first failure would
    // hide every deployment a trader has because one strategy's read timed out.
    expect(rowFor('dep-s2')).not.toBeUndefined();

    // And the partial answer is DISCLOSED, naming the strategy, so a short list is never read
    // as a complete one.
    const partial = document.querySelector('[data-deployments-read="partial"]');
    expect(partial).not.toBeNull();
    expect(partial.textContent).toContain('1 of 2');
    expect(partial.textContent).toContain('s1');

    // Not the page-level branch: the figures that were read successfully are still on screen.
    expect(document.querySelector('[data-region="page-error"]')).toBeNull();
    expect(figureOf(declared('exposure').label)).toContain('15,500.00');
    expect(figureOf(declared('riskState').label)).toContain('elevated');
  });

  it('renders an EmptyState rather than an empty table when no deployment is reported', async () => {
    // The default fan-out: one strategy, asked, answering with no deployment.
    bothRead();

    mount();

    await waitFor(() => expect(document.querySelector('[data-deployments-empty]')).not.toBeNull());

    // NO table: a header row over nothing cannot say which emptiness this is, and a selector
    // that selects nothing is the dead control Requirement 19.4 forbids.
    expect(selectorPanel().querySelector('table')).toBeNull();

    const empty = document.querySelector('[data-deployments-empty]');
    expect(empty.getAttribute('data-empty-variant')).toBe('no-data');
    // The DECLARED reason, quoted as what the server actually answered — once per strategy,
    // because that is how the read is scoped.
    expect(empty.textContent).toContain(declared('deployment').reason);
    // Requirement 14.1's third field, which is the one everybody omits.
    expect(empty.querySelector('a, button')).not.toBeNull();
  });

  it('states the in-process-registry incompleteness on the surface, once', async () => {
    bothRead({ deployments: (id) => Promise.resolve(deploymentsBody(id, [deployment()])) });

    mount();

    await waitFor(() => expect(selectorRows()).toHaveLength(1));

    const caveats = document.querySelectorAll('[data-selector-caveat="in-process-registry"]');
    expect(caveats).toHaveLength(1);
    // The endpoint lists an in-process worker registry, so it can answer with fewer
    // deployments than `strategy_deployments` holds — and the sentence says what to DO about
    // that, which is the half that stops a duplicate live deployment.
    expect(caveats[0].textContent).toContain('may be incomplete');
    expect(caveats[0].textContent).toContain('UNKNOWN rather than stopped');
    // A sentence, not an alert: a permanent property of the read dressed as a warning is the
    // banner that stops being read.
    expect(caveats[0].closest('[data-alert-severity]')).toBeNull();
    // Above the rows it qualifies.
    const documentOrder = [...document.querySelectorAll('*')];
    expect(documentOrder.indexOf(caveats[0]))
      .toBeLessThan(documentOrder.indexOf(selectorRows()[0]));
  });

  it('surfaces a row whose own environment disagrees with the LIVE route', async () => {
    bothRead({
      deployments: (id) => Promise.resolve(deploymentsBody(id, [
        deployment({ deployment_id: 'dep-paper', environment: 'paper' }),
      ])),
    });

    mount();

    await waitFor(() => expect(selectorRows()).toHaveLength(1));

    // The row's `environment` is the deployment record's OWN, and it contradicts this route.
    // Surfaced, not hidden: a trader about to scope the page by it should read that first.
    const disagreement = document.querySelector('[data-deployments-environment="disagrees"]');
    expect(disagreement).not.toBeNull();
    expect(disagreement.textContent).toContain('dep-paper');
    expect(disagreement.textContent).toContain('paper');

    // The strip is unaffected: it states what the ROUTE is, and that claim is still true.
    expect(document.querySelector('[data-environment-variant="strip"]').getAttribute('data-environment'))
      .toBe('LIVE');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE THREE DESTRUCTIVE ACTIONS (task 20.3)
 * ══════════════════════════════════════════════════════════════════════════════════════
 *
 * design.md §8.3, §8.4. Requirements 7.6, 8.1, 8.3, 8.5, 14.4, 19.1, 19.4. Property 13.
 *
 * WHAT IS PINNED HERE, AND WHY EACH ONE IS WORTH A TEST
 * ====================================================
 *  23. **No request is issued before the confirmation, for all three actions.** This is
 *      Property 13, and it is asserted as a property of the page's STRUCTURE: the control is
 *      clicked, the dialog is on screen, and the mutation spy has not been called. Under a
 *      `window.confirm` this could only ever have been a property of what `confirm()`
 *      returned.
 *  24. **Cancel-all is unreachable until the acknowledgement is ticked**, and reachable
 *      immediately afterwards. The confirm control is `disabled` AND activating it issues
 *      nothing — `ds/ConfirmDialog` enforces the gate in both places, and a test that only
 *      read the attribute would pass against a styled-but-live button.
 *  25. **Stop and cancel-one carry NO acknowledgement.** The asymmetry is the safety
 *      property, not an omission: spending a checkbox on a risk-REDUCING action is how a
 *      trader learns to tick one without reading it, which is what makes it worthless on the
 *      bulk irreversible one. Asserted as the absence of the acknowledgement region, so a
 *      later "consistency" edit that adds one fails here.
 *  26. **Each mutation is called with what its ROUTER requires.** `POST /api/orders/cancel/
 *      {order_id}` needs the id, the market and the venue; `POST /api/orders/cancel-all`
 *      needs the venue; `POST /api/deployments/{id}/stop` needs the deployment id. Two of
 *      those client methods were addressing routes that do not exist before this task.
 *  27. **A failed mutation renders authored copy and never `err.message`** (Requirement
 *      14.4), inside the dialog, which stays open over the review a trader was reading.
 *  28. **A control whose endpoint cannot be addressed is not rendered at all.** A row that
 *      reports no deployment id, and an account with no single venue, are both cases where a
 *      button would 404 or 422 against a live account.
 *  29. **No manual-order affordance exists** (Requirement 19.1): nothing on the page places,
 *      amends or modifies anything.
 */

describe('LiveTrading destructive actions (task 20.3)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  /** One deployment record with a server-reported id — the stop's path parameter. */
  const deployment = (overrides = {}) => ({
    deployment_id: 'dep-1',
    status: 'running',
    environment: 'live',
    worker: 'worker-a',
    started_at: '2026-06-01T08:00:00.000Z',
    health: 'healthy',
    ...overrides,
  });

  /** One ccxt open order, as the venue reports it: an id and a symbol, which cancel needs. */
  const openOrder = (overrides = {}) => ({
    id: 'ord-1',
    symbol: 'BTC/USDT',
    side: 'buy',
    amount: 0.1,
    price: 60000,
    timestamp: 1780000000000,
    ...overrides,
  });

  /** The page with one deployment and one open order — every control addressable. */
  const readyWithControls = (overrides = {}) => bothRead({
    orders: [openOrder()],
    deployments: (id) => Promise.resolve(deploymentsBody(id, [deployment()])),
    ...overrides,
  });

  const dialog = () => document.querySelector('[data-ds="confirm-dialog"]');
  const confirmButton = () => document.querySelector('[data-ds="confirm-dialog-confirm"]');
  const acknowledgement = () =>
    document.querySelector('[data-ds="confirm-dialog-acknowledgement"]');
  const reviewText = () =>
    document.querySelector('[data-ds="confirm-dialog-review"]').textContent;

  const control = (action) => document.querySelector(`[data-live-action="${action}"]`);
  const selectorRow = () =>
    document.querySelector('[data-region="deployment"] tbody tr[data-row-id]');

  /** Mount, wait for all four reads, and select the one deployment row. */
  const mountAndSelect = async () => {
    mount();
    await waitFor(() => expect(selectorRow()).not.toBeNull());
    fireEvent.click(selectorRow());
    await waitFor(() => expect(control('stop-deployment')).not.toBeNull());
  };

  it('opens a dialog and issues NOTHING when Stop deployment is clicked, then stops on confirm', async () => {
    readyWithControls();
    const stop = vi.spyOn(strategiesApi, 'stopDeployment').mockResolvedValue({
      status: 'stopped', deployment_id: 'dep-1', idempotent: false,
    });

    await mountAndSelect();

    fireEvent.click(control('stop-deployment'));

    // ── Property 13 ──────────────────────────────────────────────────────────────────
    // The dialog is up and the mutation has NOT been called. The button's whole job is to
    // set dialog state; there is no code path from it to the request.
    await waitFor(() => expect(dialog()).not.toBeNull());
    expect(stop).not.toHaveBeenCalled();

    // The review grid names the three things §7.5 makes this page about, read off the page's
    // own tier figures rather than from a second source.
    expect(reviewText()).toContain('dep-1');
    expect(reviewText()).toContain('Momentum Breakout');
    expect(reviewText()).toContain('BTC/USDT');
    expect(reviewText()).toContain('long 0.25');

    // And the copy states what the endpoint does NOT do. `transition_deployment` moves the
    // binding, writes the reason and moves the version; it neither closes a position nor
    // cancels a resting order, and the response reports neither — so the dialog says so
    // instead of claiming an outcome nothing reports.
    const body = document.querySelector('[data-ds="confirm-dialog-body"]').textContent;
    expect(body).toContain('does NOT report closing your open position');
    expect(body).toContain('Not closed by this action');

    // The confirm action is the ONLY caller, and it sends the path id plus the reason the
    // router preserves on the row and in the audit trail.
    fireEvent.click(confirmButton());

    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
    expect(stop).toHaveBeenCalledWith('dep-1', expect.stringContaining('Live Trading'));
  });

  it('opens a dialog and issues NOTHING when Cancel live order is clicked, then cancels with the id, market and venue', async () => {
    readyWithControls();
    const cancel = vi.spyOn(ordersApi, 'cancelOrder').mockResolvedValue({ status: 'canceled' });

    mount();
    await waitFor(() => expect(control('cancel-order')).not.toBeNull());

    fireEvent.click(control('cancel-order'));

    await waitFor(() => expect(dialog()).not.toBeNull());
    expect(cancel).not.toHaveBeenCalled();

    // The order named is the one tier 3 reports — same row, one definition of "newest".
    expect(reviewText()).toContain('ord-1');
    expect(reviewText()).toContain('BTC/USDT');
    expect(reviewText()).toContain('binance');

    fireEvent.click(confirmButton());

    // `POST /api/orders/cancel/{order_id}` declares `exchange_id` as a required query
    // parameter and a body of `{order_id, symbol}`. All three travel, in that order.
    await waitFor(() => expect(cancel).toHaveBeenCalledTimes(1));
    expect(cancel).toHaveBeenCalledWith('ord-1', 'BTC/USDT', 'binance');
  });

  it('gates Cancel all orders behind the acknowledgement, and issues nothing until it is ticked', async () => {
    readyWithControls();
    const cancelAll = vi.spyOn(ordersApi, 'cancelAllOrders')
      .mockResolvedValue({ status: 'ok', cancelled: {} });

    mount();
    await waitFor(() => expect(control('cancel-all-orders')).not.toBeNull());

    fireEvent.click(control('cancel-all-orders'));

    await waitFor(() => expect(dialog()).not.toBeNull());
    expect(cancelAll).not.toHaveBeenCalled();

    // §8.4's acknowledgement, on the ONE action here that earns one: bulk, every market, and
    // nothing re-places what it removes.
    expect(acknowledgement()).not.toBeNull();
    expect(acknowledgement().textContent).toContain('every order resting at this venue');

    // Unreachable: the control is disabled AND activating it issues nothing. Reading only the
    // attribute would pass against a button that was styled instead of gated.
    expect(confirmButton().disabled).toBe(true);
    fireEvent.click(confirmButton());
    confirmButton().click();
    expect(cancelAll).not.toHaveBeenCalled();

    // Ticked, and only now.
    fireEvent.click(acknowledgement().querySelector('input[type="checkbox"]'));
    expect(confirmButton().disabled).toBe(false);

    fireEvent.click(confirmButton());

    // The venue, and NO symbol: an absent `symbol` is how `cancel_all` is told "every
    // market", which is what this control says it does.
    await waitFor(() => expect(cancelAll).toHaveBeenCalledTimes(1));
    expect(cancelAll).toHaveBeenCalledWith('binance');
  });

  it('gives stop and cancel-one NO acknowledgement, and puts the LIVE badge on all three', async () => {
    readyWithControls();
    vi.spyOn(strategiesApi, 'stopDeployment').mockResolvedValue({ status: 'stopped' });

    await mountAndSelect();

    // Stop: risk-REDUCING and reversible by deploying again, so no gate. The environment
    // badge IS there — it states which LEDGER is being acted on, which is a different claim
    // from the risk class of the action (Requirement 8.5).
    fireEvent.click(control('stop-deployment'));
    await waitFor(() => expect(dialog()).not.toBeNull());
    expect(acknowledgement()).toBeNull();
    expect(confirmButton().disabled).toBe(false);
    expect(dialog().querySelector('[data-ds="environment-strip"]').getAttribute('data-environment'))
      .toBe('LIVE');
    fireEvent.click(document.querySelector('[data-ds="confirm-dialog-cancel"]'));
    await waitFor(() => expect(dialog()).toBeNull());

    // Cancel one: a single named order, re-placeable by the strategy that placed it.
    fireEvent.click(control('cancel-order'));
    await waitFor(() => expect(dialog()).not.toBeNull());
    expect(acknowledgement()).toBeNull();
    expect(confirmButton().disabled).toBe(false);
    expect(dialog().querySelector('[data-ds="environment-strip"]').getAttribute('data-environment'))
      .toBe('LIVE');
    fireEvent.click(document.querySelector('[data-ds="confirm-dialog-cancel"]'));
    await waitFor(() => expect(dialog()).toBeNull());

    // Cancel all: the one that has it.
    fireEvent.click(control('cancel-all-orders'));
    await waitFor(() => expect(dialog()).not.toBeNull());
    expect(acknowledgement()).not.toBeNull();
    expect(dialog().querySelector('[data-ds="environment-strip"]').getAttribute('data-environment'))
      .toBe('LIVE');
  });

  it('renders authored copy — never `err.message` — when a mutation fails, and keeps the dialog open', async () => {
    readyWithControls();
    // A refusal carrying everything Requirement 14.4 forbids on a screen: a status code, an
    // error class name and a stack frame.
    vi.spyOn(strategiesApi, 'stopDeployment').mockRejectedValue(
      new ApiError('DEPLOYMENT_STOP_FAILED at Object.stop (/app/routers/x.py:12:3)', {
        status: 503,
      }),
    );

    await mountAndSelect();

    fireEvent.click(control('stop-deployment'));
    await waitFor(() => expect(dialog()).not.toBeNull());
    fireEvent.click(confirmButton());

    const failure = await waitFor(() => {
      const node = document.querySelector('[data-ds="dialog-error"]');
      expect(node).not.toBeNull();
      return node;
    });

    // `translateError`'s output and nothing else.
    expect(failure.textContent).toContain(CATEGORY_COPY.SERVER_ERROR.headline);
    expect(failure.textContent).not.toContain('DEPLOYMENT_STOP_FAILED');
    expect(failure.textContent).not.toContain('routers/x.py');
    expect(document.body.textContent).not.toContain('503');

    // The dialog stays open over the review the trader was reading: §8.3's failed state
    // returns to Review, where the confirm control is the retry. And it is a `ds/` rendering,
    // not a hand-rolled box on the page.
    expect(dialog()).not.toBeNull();
    expect(reviewText()).toContain('dep-1');
    expect(confirmButton()).not.toBeNull();
  });

  it('renders no control whose endpoint cannot be addressed, and no manual-order affordance', async () => {
    // A deployment row that reports NO id — `deploymentRow` gives it this page's synthetic
    // union key, which addresses nothing — and a venue that reports two exchanges, so tier 1
    // names no single one and the open-orders read was never issued.
    bothRead({
      dashboard: dashboardBody({
        exchanges: [
          { exchange_id: 'binance', status: 'connected' },
          { exchange_id: 'kraken', status: 'connected' },
        ],
      }),
      deployments: (id) => Promise.resolve(deploymentsBody(id, [
        { status: 'running', environment: 'live', worker: 'worker-a', health: 'healthy' },
      ])),
    });

    mount();
    await waitFor(() => expect(selectorRow()).not.toBeNull());
    fireEvent.click(selectorRow());

    // Selected — the page re-scoped — and still no Stop: there is no id to put in the path.
    await waitFor(() =>
      expect(document.querySelector('[data-selected-deployment]')).not.toBeNull());
    expect(control('stop-deployment')).toBeNull();

    // No venue, so no `exchange_id`: neither order control is rendered, and the group they
    // would have sat in is absent too.
    expect(control('cancel-order')).toBeNull();
    expect(control('cancel-all-orders')).toBeNull();
    expect(document.querySelector('[data-live-actions="orders"]')).toBeNull();

    // Requirement 19.1: this task added stop and cancel only. Nothing here places, amends or
    // modifies an order or a position.
    const controls = [...document.querySelectorAll('button, a')]
      .map((node) => (node.textContent ?? '').toLowerCase());
    for (const forbidden of ['place order', 'new order', 'amend', 'modify position', 'close position']) {
      expect(controls.some((label) => label.includes(forbidden)), `${forbidden} is rendered`)
        .toBe(false);
    }
  });
});
