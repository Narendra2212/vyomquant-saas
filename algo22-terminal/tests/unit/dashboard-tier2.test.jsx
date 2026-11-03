/**
 * @fileoverview Dashboard — tier 2, BC-2's two arms, and the figures that must never read `0`.
 *
 * vyomquant-ui-redesign task 19.1 part B. design.md §7.1, §11.1, §13.3.
 * Requirements 3.2, 3.5, 14.1, 14.5, 19.3.
 *
 * WHY THIS IS A SIBLING OF `dashboard-tier1.test.jsx` AND NOT MORE OF IT
 * =====================================================================
 * That file is named for tier 1, its header states that the tier-2 zones are "deliberately
 * not re-asserted here", and its fixture carries an empty account so that nothing below the
 * summary row is in the DOM at all. Appending tier 2 to it would falsify the name and the
 * header, and every tier-2 case needs a payload with rows in it. So the two files split the
 * way §7.1 splits: tier 1's four figures and the one read there, tier 2's regions here.
 *
 * WHAT IS PINNED HERE
 * ===================
 *   1. **Every declared tier-2 element renders, and every one of them follows every tier-1
 *      element in document order** (Requirements 3.1, 3.2). Read from
 *      `pageHierarchy.js` rather than listed here, so a region added to the declaration and
 *      not to the page fails. §7.1 has no tier 3 and the declaration has no tier-3 entry;
 *      the absence is asserted, never a tier-3 element.
 *   2. **BC-2, both arms** (Requirement 14.5). `degraded === null` is a HEALTHY reading and
 *      renders no marker and no alert — `positions: []` then means an account holding
 *      nothing, which is the empty state. `degraded.positions === "unreadable"` renders the
 *      server's own reason and NO table, because an empty table there states that the
 *      account holds nothing.
 *   3. **`risk.open_positions_count === null` is the marker, never `0`** — a count of zero
 *      is the safest-looking figure a broken positions read could publish. Both arms are
 *      asserted, because "renders the marker" is only meaningful beside a `0` that renders
 *      as a figure when the server really reported one.
 *   4. **`health.exchange_api_latency_ms === null` is the marker, never `0 ms`**, on the
 *      payload `pageFields` describes: the null figure with
 *      `exchange_api_latency_status: "unavailable"` beside it. The measured arm is asserted
 *      too, so the absence is a distinction and not a page that never renders latency.
 *   5. **The top five, and a way to the rest** (§7.1). Five rows out of seven, and a real
 *      `<a href="/app/portfolio">` that names its destination.
 *   6. **The two per-venue fields render as the real measurements `get_exchange_data` now
 *      produces, and only fall back to "not reported" where the probe genuinely measured
 *      nothing.** `status` and `latency_ms` used to be a hardcoded `"connected"` and a
 *      hardcoded `35` in the aggregation service; `pageFields`' `exchangeHealth` note now
 *      records that both are read off a real per-venue Redis probe, so `ds/ExchangeStatus`
 *      is passed `connectionState` and `latencyMs` and renders them as readings.
 *
 * The kill switch, its modal and the Requirement 3.3 alert strip are task 19.2's and are
 * not asserted here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import Dashboard from '../../src/pages/Dashboard';
import * as dashboardModule from '../../src/api/modules/dashboard';
import { PAGES, PAGE_FIELD_BY_KEY, pageFieldKey } from '../../src/design/pageFields';
import { PAGE_HIERARCHY_BY_PAGE, tierSelector } from '../../src/design/pageHierarchy';

/*
 * `ds/Chart` is stubbed, not recharts.
 *
 * Task 19.1b put the equity curve behind `lazy(() => import('../components/ds/Chart'))`, so
 * the page no longer imports recharts and a `vi.mock('recharts', ...)` would have to enumerate
 * every export `ds/Chart` uses in order for the lazy chunk to resolve at all. Stubbing the
 * module the page actually imports is `portfolio-rendering.test.jsx`'s approach since task
 * 16.2 and is the right boundary: the chart's own behaviour is `tests/unit/ds/Chart.test.jsx`'s
 * subject, and what this page owes is the contract it passes — which the attributes below
 * carry, so the equity region is still a decidable element in the DOM.
 */
vi.mock('../../src/components/ds/Chart', () => {
  const Stub = (props) => (
    <figure
      data-testid="chart"
      data-chart-kind={props.kind}
      data-chart-points={Array.isArray(props.data) ? props.data.length : -1}
      data-chart-x-label={props.xAxis?.label}
      data-chart-y-label={props.yAxis?.label}
      data-chart-series={(props.series ?? []).map((s) => `${s.key}:${s.token}`).join(',')}
    />
  );
  return { __esModule: true, Chart: Stub, default: Stub };
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DECLARATION, AND THE FIXTURE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const HIERARCHY = PAGE_HIERARCHY_BY_PAGE[PAGES.DASHBOARD];

/** §7.1's tier 2, read from the declaration rather than retyped. */
const TIER_TWO = HIERARCHY.tiers.filter((entry) => entry.tier === 2);

const declared = (field) => PAGE_FIELD_BY_KEY[pageFieldKey({ page: PAGES.DASHBOARD, field })];

/** One position row, as `dashboard_aggregation_service` publishes it. */
const position = (index, overrides = {}) => ({
  id: `pos_${index}`,
  exchange_id: 'binance',
  environment: 'live',
  symbol: `SYM${index}/USDT`,
  market_type: 'spot',
  margin_type: 'cross',
  side: 'long',
  contracts: 1 + index,
  entry_price: 100 + index,
  mark_price: 110 + index,
  unrealized_pnl: 10,
  unrealized_pnl_pct: 9.09,
  leverage: 1,
  liquidation_price: null,
  ...overrides,
});

/**
 * A `GET /api/dashboard` body with an account that has something in it.
 *
 * `open_positions_count` is the server's own count and is deliberately NOT
 * `positions.length` — the page reads the reported field, and the two disagreeing is how a
 * test can tell which one reached the screen.
 */
const body = ({
  positions = [position(1), position(2, { exchange_id: 'bybit' })],
  degraded = null,
  risk = {},
  health = {},
  exchange = {},
  strategies = {},
  executions = undefined,
  signals = undefined,
  equityCurve = undefined,
} = {}) => ({
  environment: 'live',
  overview: {
    total_value: 45250,
    today_pnl: 400,
    cumulative_pnl: 5250,
    currency: 'USDT',
  },
  positions,
  degraded,
  executions: executions ?? [
    {
      id: 'fill_1',
      symbol: 'SOL/USDT',
      exchange_id: 'binance',
      side: 'buy',
      price: 145.5,
      amount: 10,
      realized_pnl: 12.5,
      timestamp: '2026-08-26T12:00:00Z',
    },
  ],
  risk: {
    current_drawdown_pct_v2: 3.2,
    open_positions_count: positions.length,
    circuit_breaker_armed: true,
    kill_switch_active: false,
    ...risk,
  },
  health: {
    exchange_api_latency_ms: 42,
    exchange_api_latency_status: 'optimal',
    order_state_sync_status: 'synchronized',
    ...health,
  },
  exchange: {
    total_exchanges: 1,
    connected_exchanges: 1,
    can_trade: true,
    // The two constants `pageFields` records: `status` is always "connected" and
    // `latency_ms` always 35, from no measurement.
    exchanges: [
      { exchange_id: 'binance', status: 'connected', latency_ms: 35, last_sync: '2026-08-26T12:00:00Z' },
    ],
    ...exchange,
  },
  strategies: {
    total: 1,
    active: 1,
    paused: 0,
    items: [
      { id: 'strat_1', name: 'BTC Trend Follower', pair: 'BTC/USDT', status: 'running', health: 'healthy' },
    ],
    ...strategies,
  },
  recent_activity: {
    signals: signals ?? [
      { id: 'sig_1', time: '2026-08-26T11:59:00Z', text: 'Long BTC/USDT accepted by risk', type: 'info' },
    ],
    insights: [],
  },
  equity_curve: equityCurve ?? [
    { timestamp: '2026-08-20T00:00:00Z', equity: 44000 },
    { timestamp: '2026-08-26T00:00:00Z', equity: 45250 },
  ],
});

const read = () => vi.spyOn(dashboardModule.dashboardApi, 'getDashboard');

const mount = () => render(<MemoryRouter><Dashboard /></MemoryRouter>);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * DOM HELPERS
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const tierOneContainer = () => document.querySelector(tierSelector(PAGES.DASHBOARD, 1));
const tierTwoContainer = () => document.querySelector(tierSelector(PAGES.DASHBOARD, 2));

/** The element carrying a declared field's `data-region`, or `null`. */
const region = (key) => document.querySelector(`[data-region="${key}"]`);

/** `true` when `first` precedes `second` in document order. */
const precedes = (first, second) =>
  Boolean(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING);

/**
 * Wait until the one read has answered AND the projection effect has run.
 *
 * Waiting for the regions to EXIST is not enough: `data-region` sits on the `ds/Panel`
 * section, which is in the DOM in its loading state too, so a query that stops there reads
 * the skeleton. The settled condition is that the positions panel has left `loading`.
 */
const settled = async () => {
  await waitFor(() => expect(tierTwoContainer()).not.toBeNull());
  await waitFor(() => {
    const panel = region('openPositions');
    expect(panel).not.toBeNull();
    expect(['loading', 'idle']).not.toContain(panel.getAttribute('data-panel-state'));
  });
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * TIER 2 — every declared element, below every tier-1 element
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tier 2 — the declared regions and their order (task 19.1b)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders every declared tier-2 element, inside the one tier-2 container', async () => {
    read().mockResolvedValue(body());

    mount();
    await settled();

    expect(document.querySelectorAll(tierSelector(PAGES.DASHBOARD, 2))).toHaveLength(1);
    // Ten declared regions; the count is read from the declaration so that adding one
    // there without rendering it fails here rather than passing over a shorter list.
    expect(TIER_TWO.length).toBeGreaterThan(0);

    const container = tierTwoContainer();
    for (const { key } of TIER_TWO) {
      const element = region(key);
      expect(element, `tier-2 region "${key}" did not render`).not.toBeNull();
      expect(container.contains(element), `"${key}" rendered outside the tier-2 container`)
        .toBe(true);
    }
  });

  it('puts every tier-2 element after every tier-1 element in document order', async () => {
    read().mockResolvedValue(body());

    mount();
    await settled();

    // Property 4's ordering clause, as an example: the containers first, then every
    // tier-1 FIGURE against every tier-2 REGION, which is the pairwise claim the property
    // generalises over arbitrary payloads (task 19.5).
    expect(precedes(tierOneContainer(), tierTwoContainer())).toBe(true);

    const tierOneFigures = [...document.querySelectorAll('[data-metric-tier="1"]')];
    expect(tierOneFigures).toHaveLength(4);

    for (const figure of tierOneFigures) {
      for (const { key } of TIER_TWO) {
        expect(
          precedes(figure, region(key)),
          `tier-2 region "${key}" rendered before a tier-1 figure`,
        ).toBe(true);
      }
    }
  });

  it('declares and renders no tier 3 — §7.1 has two tiers', async () => {
    // Asserted from the declaration first: this is not "the page happens to have none"
    // but "§7.1 has none, so neither does the DOM".
    expect(HIERARCHY.tiers.filter((entry) => entry.tier === 3)).toHaveLength(0);

    read().mockResolvedValue(body());

    mount();
    await settled();

    expect(document.querySelector(tierSelector(PAGES.DASHBOARD, 3))).toBeNull();
    expect(document.querySelectorAll('[data-metric-tier="3"]')).toHaveLength(0);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * BC-2 — `degraded` IS WHAT SEPARATES "NONE" FROM "COULD NOT READ THEM"
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tier 2 — BC-2, both arms (Requirement 14.5)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('treats `degraded: null` as a healthy reading: no marker, no alert, and `[]` is empty', async () => {
    // Every read behind the response succeeded, so `positions: []` means the account holds
    // nothing. That is an absence of POSITIONS, not an absence of information, and it must
    // not render a not-available marker for the read state.
    read().mockResolvedValue(body({ positions: [], degraded: null, risk: { open_positions_count: 0 } }));

    mount();
    await settled();

    const wrapper = region('positionsDegraded');
    expect(wrapper.getAttribute('data-positions-read')).toBe('complete');
    // No `ds/Alert` anywhere in the region, and no marker claiming the read state is unknown.
    expect(wrapper.querySelector('[data-ds="alert"]')).toBeNull();
    expect(screen.queryByLabelText(`${declared('positionsDegraded').label}: not available`))
      .toBeNull();

    // The panel says what is missing, why it matters and what to do (Requirement 14.1) —
    // it is not a table with no rows.
    const panel = region('openPositions');
    expect(panel.getAttribute('data-panel-state')).toBe('empty');
    expect(panel.querySelector('table')).toBeNull();
    expect(screen.getByText('No open positions')).toBeDefined();

    // `ds/Panel` renders the empty state INSTEAD of its children, so the count metric is
    // not in the DOM on this arm at all — there is no figure standing over an empty state.
    // The reported-`0`-is-a-figure case is asserted below, with rows on screen.
    expect(region('openPositionsCount')).toBeNull();
    // And nothing on the page says the positions could not be read, because they could.
    expect(document.body.textContent).not.toContain('could not be read');
  });

  it('renders the server\'s own reason and NO table when the positions read is unreadable', async () => {
    const reason = 'The live positions cache could not be read for account 4821.';
    read().mockResolvedValue(body({
      positions: [],
      degraded: { positions: 'unreadable', environment: 'live', reason },
      risk: { open_positions_count: null },
    }));

    mount();
    await settled();

    const wrapper = region('positionsDegraded');
    expect(wrapper.getAttribute('data-positions-read')).toBe('degraded');

    // Verbatim, and above the panel it explains: `translateError` carries no free-form
    // message by design (Requirement 14.4), so this sentence is the only account of the
    // failure that can reach the screen.
    const alert = region('positions-degraded');
    expect(alert).not.toBeNull();
    expect(alert.textContent).toContain(reason);
    expect(alert.getAttribute('data-alert-severity')).toBe('warning');
    expect(precedes(alert, region('openPositions'))).toBe(true);

    // NOT the empty state, and no table: an empty table here would state that the account
    // holds nothing, which is exactly what the server said it could not determine.
    const panel = region('openPositions');
    expect(panel.getAttribute('data-panel-state')).toBe('error');
    expect(panel.querySelector('table')).toBeNull();
    expect(screen.queryByText('No open positions')).toBeNull();
    expect(document.body.textContent).not.toContain('This account holds nothing right now');
  });

  it('renders the marker, never `0`, when `risk.open_positions_count` is null', async () => {
    // BC-2's other channel. The positions themselves arrived, so the panel is ready and the
    // count is on screen — which is the only arrangement in which "the count is unknown"
    // and "the count is zero" can be told apart.
    read().mockResolvedValue(body({
      positions: [position(1)],
      risk: { open_positions_count: null },
    }));

    mount();
    await settled();

    const count = region('openPositionsCount');
    expect(count).not.toBeNull();
    expect(count.getAttribute('data-metric-available')).toBe('false');

    const marker = count.querySelector('[data-metric-marker="not-available"]');
    expect(marker).not.toBeNull();
    expect(marker.getAttribute('title')).toBe(declared('openPositionsCount').reason);
    // Not a zero, and not `positions.length` either — the figure is the server's.
    expect(count.textContent).not.toContain('0');
    expect(count.textContent).not.toContain('1');
  });

  it('renders a reported count of `0` as a figure', async () => {
    read().mockResolvedValue(body({
      positions: [position(1)],
      risk: { open_positions_count: 0 },
    }));

    mount();
    await settled();

    const count = region('openPositionsCount');
    expect(count.getAttribute('data-metric-available')).toBe('true');
    expect(count.textContent).toContain('0');
    expect(count.querySelector('[data-metric-marker="not-available"]')).toBeNull();
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * SYSTEM & EXCHANGE HEALTH — the measured figure, and the two constants
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tier 2 — exchange health (Requirements 3.2, 14.5)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders not-available for a null exchange API latency, never `0 ms`', async () => {
    // The payload `pageFields` describes: genuinely `null`, with the status field beside it
    // reading "unavailable". A zero-latency exchange call is not a thing, so `0 ms` would be
    // read as a claim about a working connection.
    read().mockResolvedValue(body({
      health: { exchange_api_latency_ms: null, exchange_api_latency_status: 'unavailable' },
    }));

    mount();
    await settled();

    const latency = region('exchangeApiLatencyMs');
    expect(latency.getAttribute('data-metric-available')).toBe('false');

    const marker = latency.querySelector('[data-metric-marker="not-available"]');
    expect(marker.getAttribute('title')).toBe(declared('exchangeApiLatencyMs').reason);

    // Neither a zero nor a unit beside nothing: `ds/Metric` renders `unit` only next to a
    // real figure, so there is no `— ms` claiming the missing value is a duration.
    expect(latency.textContent).not.toContain('0');
    expect(document.body.textContent).not.toContain('0 ms');
    // And the status the server sent beside the null is not promoted into a reading — in
    // particular not the `|| "optimal"` the old diagnostics row substituted.
    expect(document.body.textContent).not.toContain('optimal');
  });

  it('renders a measured latency as a figure with its unit', async () => {
    read().mockResolvedValue(body({
      health: { exchange_api_latency_ms: 42, exchange_api_latency_status: 'optimal' },
    }));

    mount();
    await settled();

    const latency = region('exchangeApiLatencyMs');
    expect(latency.getAttribute('data-metric-available')).toBe('true');
    expect(latency.textContent).toContain('42');
    expect(latency.textContent).toContain('ms');
  });

  it('presents each per-venue status and latency as the real measurement it now is', async () => {
    // `exchange.exchanges[]` used to carry a constant `status: "connected"` and
    // `latency_ms: 35` for every row because `get_exchange_health` hardcoded both.
    // `get_exchange_data` now derives each from a real per-venue Redis probe, and the row
    // reads both — this fixture gives the two venues genuinely different readings so a
    // rewrite that quietly reintroduced a shared constant would be caught here.
    read().mockResolvedValue(body({
      exchange: {
        can_trade: true,
        exchanges: [
          { exchange_id: 'binance', status: 'connected', latency_ms: 18, last_sync: '2026-08-26T12:00:00Z' },
          { exchange_id: 'bybit', status: 'degraded', latency_ms: 310, last_sync: null },
        ],
      },
    }));

    mount();
    await settled();

    const health = region('exchangeHealth');

    const binance = health.querySelector('[data-exchange="binance"]');
    expect(binance, 'binance did not render').not.toBeNull();
    expect(binance.getAttribute('data-connection-state')).toBe('connected');
    expect(binance.getAttribute('data-latency-reported')).toBe('true');
    expect(binance.textContent).toContain('18 ms');

    const bybit = health.querySelector('[data-exchange="bybit"]');
    expect(bybit, 'bybit did not render').not.toBeNull();
    expect(bybit.getAttribute('data-connection-state')).toBe('degraded');
    expect(bybit.getAttribute('data-latency-reported')).toBe('true');
    expect(bybit.textContent).toContain('310 ms');

    /*
     * THE WS STREAM BADGE IS A DIFFERENT, GENUINELY DIFFERENT, MEASUREMENT.
     *
     * The "Real-time stream" `ds/StatusBadge` reads `wsClient`'s own state word for the
     * browser's live socket, observed by the client that holds it — unrelated to either
     * venue row above. It is excluded from the `'Connected'` check below by construction
     * (it is not inside `[data-exchange]`), so this stays a claim about the venue rows only.
     */
    for (const row of [binance]) {
      expect(row.textContent).toContain('Connected');
    }

    // Neither of the OLD fabricated constants appears anywhere on the panel.
    expect(health.textContent).not.toContain('35 ms');

    // `last_sync` is real and is rendered; the row that carries none says so rather than
    // reading "just now".
    expect(screen.getAllByText('Last sync').length).toBe(2);
    expect(screen.getByLabelText('Last sync: not available')).toBeDefined();
  });

  it('still falls back to "not reported" per-venue latency when a venue genuinely has none measured', async () => {
    // A credential exists but no probe has run for it: the backend reports an honest
    // `latency_ms: null` rather than inventing a figure, and the row renders the marker.
    read().mockResolvedValue(body({
      exchange: {
        can_trade: false,
        exchanges: [
          { exchange_id: 'okx', status: 'unknown', latency_ms: null, last_sync: null },
        ],
      },
    }));

    mount();
    await settled();

    const row = region('exchangeHealth').querySelector('[data-exchange="okx"]');
    expect(row.getAttribute('data-connection-state')).toBe('unknown');
    expect(row.getAttribute('data-latency-reported')).toBe('false');
    expect(row.textContent).not.toContain('0 ms');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * OPEN POSITIONS — the top five, and a way to the rest
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Dashboard tier 2 — the top five positions and the link to the rest (§7.1)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('draws five rows out of seven and links to the full ledger', async () => {
    const seven = [1, 2, 3, 4, 5, 6, 7].map((index) => position(index));
    read().mockResolvedValue(body({ positions: seven, risk: { open_positions_count: 7 } }));

    mount();
    await settled();

    const rows = region('openPositions').querySelectorAll('tbody tr[data-row-id]');
    expect(rows).toHaveLength(5);
    // The five the server returned first, in the order it returned them.
    expect([...rows].map((row) => row.getAttribute('data-row-id')))
      .toEqual(['pos_1', 'pos_2', 'pos_3', 'pos_4', 'pos_5']);

    // The count beside them is still the reported seven: the table is truncated, the figure
    // is not.
    expect(region('openPositionsCount').textContent).toContain('7');

    // A real link, in the tab order, naming where it goes — `anchor-ambiguous-text` rejects
    // a bare "View all", and a `<button onClick={navigate}>` is not a link.
    const link = screen.getByRole('link', { name: /view all in portfolio/i });
    expect(link.getAttribute('href')).toBe('/app/portfolio');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE ABSENCE ACCOUNT — retail-ui-simplification task 12.5
 * Requirements 7.1, 7.2, 7.4, 7.5, 19.1, 19.2, 19.5, 19.6, 20.1, 20.2. design.md §6.1, §6.2.
 * ══════════════════════════════════════════════════════════════════════════════════════
 *
 * WHY THIS EXISTS, AND WHY IT IS NOT A BUDGET ASSERTION
 * ----------------------------------------------------
 * Task 12.5 asked for `pages/Dashboard.jsx`'s "1225 characters in 13 constants" to go
 * behind disclosure. Measured on the tree, the page declares page-level string constants
 * and only a handful are prose:
 *
 *   * `CHIP_CLASSES` and `PANEL_LINK_CLASSES` are Tailwind class lists — not user-facing
 *     text at all.
 *   * `DEFAULT_PERIOD` "1M", `PNL_CHANNEL`, `STRATEGY_STATUS_CHANNEL`,
 *     `EXCHANGE_HEALTH_CHANNEL`, `ALERT_CONDITION_REGION`, `CONDITION_SEVERITY` and
 *     `UNEVALUABLE_SEVERITY` are tokens — a period key, three channel names, a `pageFields`
 *     key and two `ds/Alert` severities.
 *   * `RECOVER_DESCRIPTION` and `HALT_ACKNOWLEDGEMENT_LABEL` are the kill switch's
 *     CONFIRMATION copy, which Requirement 16.5 puts out of reach of this pass entirely.
 *   * `NO_LIQUIDATION_DISTANCE_REASON` is the one left that is design.md §6.1 COLUMN 2 — it
 *     accounts for one specific absent figure and renders beside the marker it explains, in
 *     the state it explains. §6.2's question — remove it, and can a trader still tell this
 *     absence from a different one and still tell why — is answered "no". So nothing moved.
 *
 *     A second such account, `VENUE_CONSTANT_NOTE`, existed here for the per-venue `status`
 *     and `latency_ms` fields while `dashboard_aggregation_service.get_exchange_data`
 *     published a fixed value for both. It is now GONE: that function derives both fields
 *     from a real per-venue Redis probe (`venue_status`, `measured_latency_ms`), so they are
 *     genuine readings passed straight to `ds/ExchangeStatus`, which already carries its own
 *     "not reported" marker and reason for the case a probe measured nothing — no page-level
 *     account was needed on top of that. See the "PER-VENUE CONNECTION STATE AND LATENCY"
 *     block below.
 *
 * The page has no page- or panel-level BACKGROUND prose to move, which is `SignalTrace`'s
 * result in task 12.4 for a different reason. `standing-prose.budget.js` keeps
 * `'pages/Dashboard.jsx': 168` untouched: design.md §5.5 counts the text before the FIRST
 * `data-region` node, which on this page is the alert band above tier 1, and the liquidation
 * account renders inside a tier-2 `ds/DataTable` far below it. It was never in that number.
 *
 * WHAT IS PINNED, IN TWO CLAUSES
 * -----------------------------
 *   1. **The account renders character-for-character at EVERY site that must carry it.**
 *      The literal is retyped here on purpose: Requirement 19.6 forbids paraphrasing
 *      prose shorter, and a substring check passes a tightening. Asserted PER SITE —
 *      per position row — because task 12.4's own first attempt passed under the defect it
 *      was written against by checking page-wide `textContent` while a second render site
 *      still carried the string.
 *   2. **Behind no collapsed control, and inside no hidden subtree.** A `ds/Accordion`
 *      unmounts its closed body, so wrapping it would make it vanish and clause 1 would
 *      catch it. A disclosure that merely HIDES its body would not, so this walks every
 *      `aria-expanded="false"` control and fails if the region it names carries the account.
 *
 * The account's MEASURED arm is asserted beside its absent one, so "renders the account" is
 * a distinction and not a page that never renders the figure: a futures position with a
 * real level and a real mark renders a distance and NO reason.
 */

/** The one §6.1 column-2 account left, verbatim from `pages/Dashboard.jsx`. */
const ABSENCE_ACCOUNTS = Object.freeze([
  Object.freeze({
    where: 'NO_LIQUIDATION_DISTANCE_REASON (Dashboard.jsx:938)',
    text: 'No liquidation distance can be computed — the position is spot, or the venue '
      + 'reported no liquidation price or no mark price.',
  }),
]);

const LIQUIDATION_REASON = ABSENCE_ACCOUNTS[0].text;

/**
 * Clause 2, run over a settled render: no collapsed disclosure stands between a trader and
 * either account, and neither sits inside a `hidden` subtree.
 */
const expectNotBehindADisclosure = (container) => {
  const behind = [];

  for (const button of container.querySelectorAll('button[aria-expanded="false"]')) {
    const controlled = document.getElementById(button.getAttribute('aria-controls') ?? '');
    if (controlled === null) continue;
    for (const account of ABSENCE_ACCOUNTS) {
      if (controlled.textContent.includes(account.text)) {
        behind.push(`${account.where} — behind the collapsed "${button.textContent.trim()}"`);
      }
    }
  }

  for (const account of ABSENCE_ACCOUNTS) {
    const carriers = [...container.querySelectorAll('*')].filter(
      (node) => node.textContent.includes(account.text)
        && [...node.children].every((child) => !child.textContent.includes(account.text)),
    );
    for (const carrier of carriers) {
      if (carrier.closest('[hidden]') !== null) {
        behind.push(`${account.where} — inside a hidden subtree`);
      }
    }
  }

  expect(
    behind,
    'An absence account is reachable only through a disclosure, or is hidden while the\n'
      + 'state it explains is on screen. A string that accounts for one specific absent\n'
      + 'figure has to render beside the marker it explains (design.md §6.2), so it may not\n'
      + 'be moved behind a collapsed control by a simplification pass:\n'
      + `  ${behind.join('\n  ')}`,
  ).toEqual([]);
};

describe('Dashboard — the two absence accounts render beside the markers they explain', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('states why a liquidation distance is absent in every row that has none, and states a real one as a figure', async () => {
    /*
     * Three rows reaching all three outcomes the one sentence has to separate: a SPOT
     * position (no liquidation level exists), a FUTURES position whose venue reported no
     * level, and a futures position with a real level and a real mark. The first two are
     * blank cells for different reasons and the sentence is what names both; the third is
     * the measurement, so the marker is a distinction rather than a column that never fills.
     */
    read().mockResolvedValue(body({
      positions: [
        position(1, { market_type: 'spot', liquidation_price: null }),
        position(2, { market_type: 'future', margin_type: 'isolated', liquidation_price: null }),
        position(3, {
          market_type: 'future',
          margin_type: 'isolated',
          side: 'long',
          mark_price: 110,
          liquidation_price: 90,
        }),
      ],
      risk: { open_positions_count: 3 },
    }));

    const { container } = mount();
    await settled();

    const rows = [...region('openPositions').querySelectorAll('tbody tr[data-row-id]')];
    expect(rows.map((row) => row.getAttribute('data-row-id')))
      .toEqual(['pos_1', 'pos_2', 'pos_3']);

    // Clause 1, PER ROW. `pos_3` is the measured arm and is asserted not to carry it, so a
    // page that printed the reason unconditionally would fail here too.
    const missing = rows
      .filter((row) => !row.textContent.includes(LIQUIDATION_REASON))
      .map((row) => row.getAttribute('data-row-id'));
    expect(
      missing,
      'A row whose liquidation distance is absent renders the marker without the sentence\n'
        + 'that says WHY it is absent, so a spot position and a futures position whose venue\n'
        + 'reported no level read as the same blank cell (Requirement 19.3, design.md §6.2).\n'
        + `  ${ABSENCE_ACCOUNTS[0].where} — absent from row(s) ${missing.join(', ')}`,
    ).toEqual(['pos_3']);

    // The measured arm: a real level over a real mark renders a distance, and no reason.
    expect(rows[2].textContent).not.toContain(LIQUIDATION_REASON);
    expect(rows[2].textContent).toContain('18.2');

    // Two markers, not three: `empty`, `unavailable` and a measurement stay three different
    // facts (Requirements 19.4, 19.5).
    expect(screen.getAllByLabelText('Liquidation distance: not available')).toHaveLength(2);

    expectNotBehindADisclosure(container);
  });

});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * PER-VENUE CONNECTION STATE AND LATENCY — now real measurements (DASH-02)
 * ══════════════════════════════════════════════════════════════════════════════════════
 *
 * `dashboard_aggregation_service.get_exchange_data` used to publish a fixed `"connected"`
 * and a fixed `35` for every venue, from no measurement at all, and `toVenueRow` /
 * `LiveVenueHealth` on this page refused to read either field on that stale premise even
 * after the backend was fixed to derive both from a real per-venue Redis probe
 * (`venue_status`, `measured_latency_ms`). This block pins the corrected wiring: a real
 * measurement renders as one, and a venue the probe genuinely measured nothing for still
 * renders the honest "not reported" marker — never a fabricated value.
 */
describe('Dashboard — per-venue connection state and latency render the real measurement', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('renders each venue\'s real measured status and latency, not a constant', async () => {
    read().mockResolvedValue(body({
      exchange: {
        total_exchanges: 2,
        connected_exchanges: 1,
        can_trade: true,
        exchanges: [
          // A genuine measurement, including one that is NOT "connected" — proof the row
          // reads the probe's own word rather than asserting a connected chip regardless.
          { exchange_id: 'binance', status: 'connected', latency_ms: 18, last_sync: '2026-08-26T12:00:00Z' },
          { exchange_id: 'bybit', status: 'degraded', latency_ms: 420, last_sync: null },
        ],
      },
    }));

    mount();
    await settled();

    const health = region('exchangeHealth');
    const binance = health.querySelector('[data-exchange="binance"]');
    const bybit = health.querySelector('[data-exchange="bybit"]');

    expect(binance.getAttribute('data-connection-state')).toBe('connected');
    expect(binance.getAttribute('data-latency-reported')).toBe('true');
    expect(binance.textContent).toContain('18 ms');

    expect(bybit.getAttribute('data-connection-state')).toBe('degraded');
    expect(bybit.getAttribute('data-latency-reported')).toBe('true');
    expect(bybit.textContent).toContain('420 ms');

    // Neither the old fabricated figures appear anywhere on the panel.
    expect(health.textContent).not.toContain('35 ms');
  });

  it('renders the honest "not reported" marker for a venue the probe measured nothing for, never a fabricated value', async () => {
    read().mockResolvedValue(body({
      exchange: {
        total_exchanges: 1,
        connected_exchanges: 0,
        can_trade: false,
        exchanges: [
          // No probe has ever run for this venue: the backend reports `status: "unknown"`
          // (never fabricated as `"connected"`) and `latency_ms: null` (never `0`).
          { exchange_id: 'kraken', status: 'unknown', latency_ms: null, last_sync: null },
        ],
      },
    }));

    mount();
    await settled();

    const kraken = region('exchangeHealth').querySelector('[data-exchange="kraken"]');
    expect(kraken.getAttribute('data-connection-state')).toBe('unknown');
    expect(kraken.getAttribute('data-latency-reported')).toBe('false');
    // `ds/ExchangeStatus`'s own marker, not an exact match: its accessible name carries the
    // reason sentence after the name (`"Latency: not available. <reason>"`).
    expect(screen.getByLabelText(/^Latency: not available/)).toBeDefined();

    // Not "connected", and not `0 ms` — the two fabrications Requirement 14.5 forbids.
    expect(kraken.textContent).not.toContain('0 ms');
  });

  it('renders a known-invalid credential as such, not as a connected venue', async () => {
    // `venue_status` reports `invalid_credentials` ahead of anything a probe measured, for
    // a venue whose row says the credential cannot trade.
    read().mockResolvedValue(body({
      exchange: {
        total_exchanges: 1,
        connected_exchanges: 0,
        can_trade: false,
        exchanges: [
          { exchange_id: 'okx', status: 'invalid_credentials', latency_ms: null, last_sync: null },
        ],
      },
    }));

    mount();
    await settled();

    const okx = region('exchangeHealth').querySelector('[data-exchange="okx"]');
    expect(okx.getAttribute('data-connection-state')).toBe('invalid_credentials');
  });
});
