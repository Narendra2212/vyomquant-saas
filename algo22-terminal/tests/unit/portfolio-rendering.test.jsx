/**
 * @fileoverview Portfolio page — source discipline, absence states, tier 1 and tiers 2/3.
 *
 * Four suites, in the order they were written:
 *
 *   1. **Source discipline.** Static assertions over `Portfolio.jsx`'s text. This suite used
 *      to pin the page's inline-style palette — it REQUIRED `#10B981`, `#EF4444`,
 *      `fontFamily: "monospace"` and `role="list"` to be present, and required at least one
 *      `padding: "20px"`. Every one of those was the defect the migration removes, so at task
 *      16.2 the suite is inverted: the same file is now asserted to carry NO colour literal,
 *      NO inline font declaration, NO redundant ARIA role and no static recharts import. Same
 *      technique, opposite claim, and the claim is now the one the guards make globally.
 *   2. **Absence-state rendering** (Requirements 28.4, 28.5, 14.5) — what the page shows when
 *      it has no figure to show, rendered rather than read from source.
 *   3. **Tier 1** (task 16.1, Requirements 10.1, 10.2, 19.2, 19.3).
 *   4. **Tiers 2 and 3** (task 16.2, Requirements 10.3, 10.4, 14.5, 15.5).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { act, render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import Portfolio from '../../src/pages/Portfolio';
import * as portfolioModule from '../../src/api/modules/portfolio';
import * as paperModule from '../../src/api/modules/paper';
import * as dashboardModule from '../../src/api/modules/dashboard';
import wsClient from '../../src/websocketClient';
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

/**
 * `ds/Chart` render counts, and the props it was handed.
 *
 * `vi.hoisted` because the `vi.mock` factory below is hoisted above the imports and closes
 * over this object. The factory itself runs late — `Portfolio.jsx` reaches `ds/Chart` through
 * `lazy(() => import(...))`, so the module is not requested until a chart panel renders.
 *
 * Counting renders is the point, not a convenience: §13.2's guarantee is that a WebSocket
 * frame cannot re-enter recharts, and the only way to assert that is to know how many times
 * the component was entered.
 */
const charts = vi.hoisted(() => ({ renders: 0, calls: [] }));

vi.mock('../../src/components/ds/Chart', () => {
  /*
   * A stub, not the real `ds/Chart`: recharts needs layout APIs jsdom does not implement, and
   * the chart's own behaviour (axis labels, the keyboard cursor, the legend biconditional) is
   * `tests/unit/ds/Chart.test.jsx`'s subject. What this page owes is the CONTRACT it passes —
   * a supported `kind`, both axis labels, a declared series with a `token` and no colour prop
   * — and the rendered attributes below carry all of it.
   */
  const Stub = (props) => {
    charts.renders += 1;
    charts.calls.push(props);
    return (
      <figure
        data-testid="chart"
        data-chart-kind={props.kind}
        data-chart-points={Array.isArray(props.data) ? props.data.length : -1}
        data-chart-x-label={props.xAxis?.label}
        data-chart-y-label={props.yAxis?.label}
        data-chart-series={(props.series ?? []).map((s) => `${s.key}:${s.token}`).join(',')}
      />
    );
  };
  return { __esModule: true, Chart: Stub, default: Stub };
});

/** The page source, read once. Every static assertion below reads this. */
const SOURCE = (() => {
  const fs = require('fs');
  const path = require('path');
  return fs.readFileSync(path.resolve(__dirname, '../../src/pages/Portfolio.jsx'), 'utf-8');
})();

/**
 * Comments stripped, the way both colour guards strip them.
 *
 * The docblock explains why the pie's five-colour palette went and names the literals it
 * carried, so a raw `includes('#10B981')` would match prose about a colour that is gone. The
 * guards in `tests/unit/guards/` solve this the same way and for the same reason.
 */
const CODE = SOURCE
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/[^\n]*/g, '$1');

describe('Portfolio page source discipline', () => {
  it('declares no colour literal of any kind', () => {
    // The `no-colour-literals` budget for this file is 0 as of task 16.2, and this is that
    // claim restated where a reader of this page's tests will see it. Hex first, then the
    // functional forms — the ledger toggle's `rgba(16, 185, 129, 0.2)` and the heatmap's
    // `#10B98155` were both in this file until this task.
    expect(CODE).not.toMatch(/#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])/);
    expect(CODE).not.toMatch(/(?<![\w$-])(?:rgba?|hsla?)\s*\(/);
  });

  it('reads no token off the legacy `C` shim', () => {
    // `legacy-c` 5 -> 0. The five were one line — `const COLORS = [C.orange, C.purple,
    // C.cyan, C.gold, C.t3]` — five entries rendering four colours, because M1 collapsed
    // `C.orange` and `C.gold` onto the one amber. The array is gone rather than repaired:
    // `ds/Chart` has no `pie` kind and no colour prop, so the allocation is a bar chart with
    // one `brand` series and needs no categorical palette at all.
    expect(CODE).not.toMatch(/(?<![\w$])C\./);
    expect(CODE).not.toMatch(/\bconst\s+COLORS\s*=/);
    // …and the import went with it, which is what lets the shim be deleted at task 27.2.
    expect(CODE).not.toMatch(/from\s+["'][^"']*ui-legacy\/primitives["']/);
  });

  it('declares no inline style, font or fontSize', () => {
    // This suite used to REQUIRE `fontFamily: "monospace"` more than twice and at least one
    // `padding: "20px"`. Both are now defects: `--font-mono` belongs to the primitives that
    // render figures (`ds/Metric`, `ds/DataTable`'s numeric columns, `ds/PnLDisplay`), and a
    // page that sets its own font is a page that can disagree with them.
    expect(CODE).not.toMatch(/fontFamily\s*:/);
    expect(CODE).not.toMatch(/fontSize\s*:/);
    // No `style={{ … }}` anywhere: 82 inline style objects left this file at task 16.2.
    expect(CODE).not.toMatch(/style=\{\{/);
  });

  it('carries no redundant ARIA role, which is what cleared the a11y waiver', () => {
    // The two `no-redundant-roles` findings `A11Y_PAGE_WAIVERS` held for this page were the
    // allocation legend's `<ul role="list">` and `<li role="listitem">`. The legend is gone —
    // the allocation's tabular equivalent is a `ds/DataTable` — so the waiver entry is
    // deleted and this file lints at `error` with every unwaived page.
    expect(CODE).not.toMatch(/role="list"/);
    expect(CODE).not.toMatch(/role="listitem"/);
    expect(CODE).not.toMatch(/<ul\b/);
  });

  it('imports recharts only through the lazy `ds/Chart` boundary', () => {
    // A static `from 'recharts'` anywhere in the entry graph hoists `vendor-recharts` into it
    // and defeats §13.3's split for every page that does not chart.
    // `tests/unit/ds/Chart.test.jsx` pins the global list; this is the same claim for the one
    // file this task owns.
    expect(CODE).not.toMatch(/from\s+["']recharts["']/);
    expect(CODE).toMatch(/lazy\(\s*\(\)\s*=>\s*import\(["'][^"']*ds\/Chart["']\)\s*\)/);
  });

  it('imports every other `ds/` primitive by path, never through the barrel', () => {
    // §13.3: the barrel is fine for ergonomics BECAUSE `ds/index.js` omits `Chart`. But this
    // page's own imports stay explicit, so a reader can see the primitive set at a glance.
    expect(CODE).not.toMatch(/from\s+["']\.\.\/components\/ds["']/);
    for (const primitive of ['Alert', 'DataTable', 'Metric', 'Panel', 'PnLDisplay']) {
      expect(CODE, `${primitive} is imported by path`)
        .toMatch(new RegExp(`from ["']\\.\\./components/ds/${primitive}["']`));
    }
    // `EmptyState` and `ErrorState` are deliberately NOT imported here. Both are reached
    // through `ds/Panel`'s `empty` / `error` configuration, which is what makes §11.1's state
    // contract structural: the panel decides what a region shows, so a page cannot render an
    // empty state next to a table or an error state over stale figures.
    expect(CODE).not.toMatch(/from ["']\.\.\/components\/ds\/(Empty|Error)State["']/);
  });

  it('subscribes to no WebSocket channel, which is half of §13.2\'s guarantee', () => {
    // The structural half. There is no import that could deliver a frame to this page's
    // state, so no tick can reach the three charts however the component re-renders. The
    // behavioural half is asserted by rendering — see the tier-2/3 suite.
    expect(CODE).not.toMatch(/websocketClient/);
    expect(CODE).not.toMatch(/wsClient/);
    expect(CODE).not.toMatch(/useLiveChannel/);
    expect(CODE).not.toMatch(/setInterval/);
  });

  it('uses the live steps of the token type scale', () => {
    // The names this test originally asserted — `text-heading-lg`, `text-caption-sm`,
    // `text-caption` — were declared only in the deleted `tailwind.config.js`, which Tailwind
    // v4 never loaded, so they compiled to nothing and their 18 elements rendered at the
    // inherited size (design.md §1.2). `text-micro` is a live step of `styles/tokens.css`'s
    // scale; the figure sizes now come from `ds/Metric`'s `tier`, which is the mapping that
    // replaced a page-level class name for a tier-1 figure.
    expect(CODE).toContain('text-micro');
    for (const dead of ['text-heading', 'text-caption', 'text-body-lg', 'shadow-glow']) {
      expect(CODE, `${dead} was never a real class`).not.toContain(dead);
    }
  });

  it('carries no trend arrow for a figure that reports no trend', () => {
    // This test used to require `TrendingUp` AND `TrendingDown`, which pinned the `SignIcon`
    // beside each tier-1 card. Task 16.1 deleted it: an arrow drawn from `value >= 0` reports
    // the sign of the figure it sits beside, which the figure already carries, and the absent
    // case drew a red downward arrow for a number nobody had. Requirement 1.5 spends colour
    // and shape on state, risk and required action - a portfolio balance is none of the three.
    expect(CODE).not.toContain('TrendingDown');
    expect(CODE).not.toMatch(/<SignIcon/);
    // `TrendingUp` stays: it is the equity-curve panel's `EmptyState` illustration, which is
    // one static icon for an absent region rather than a per-figure sign.
    expect(CODE).toContain('TrendingUp');
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

/**
 * The `ds/Metric` root for a label, wherever it is on the page.
 *
 * `getAllByText` then filtered to the nodes that really are inside a metric, because task 16.2
 * put a `DataTable` column header reading "Unrealised P&L" on the same page as the tier-1
 * figure of that name. Two elements carry the text; exactly one of them is a figure.
 */
const metricFor = (label) => {
  const roots = screen.getAllByText(label)
    .map((node) => node.closest('[data-metric-tier]'))
    .filter(Boolean);
  expect(roots.length, `${label} matched ${roots.length} metrics`).toBe(1);
  return roots[0];
};

/** One of the page's marked regions, by the `data-region` its `ds/Panel` carries. */
const region = (name) => document.querySelector(`[data-region="${name}"]`);

/** The page's tier-2 and tier-3 containers. `null` when the region did not render. */
const tierContainer = (tier) => document.querySelector(tierSelector(PAGES.PORTFOLIO, tier));

/** Every `ds/Chart` stub on the page, in document order. */
const chartFigures = () => [...document.querySelectorAll('[data-testid="chart"]')];

/**
 * The positions table's `<tr>` for one market.
 *
 * `screen.getByText(market)` is ambiguous as of task 16.2: the market also appears in the
 * summary's "Largest position" figure above the table, which is the point of that figure. The
 * cell is the one inside a `<td>`.
 */
const positionRow = (market) => {
  const cell = screen.getAllByText(market).find((node) => node.closest('td') !== null);
  expect(cell, `no table cell holds ${market}`).toBeDefined();
  return cell.closest('tr');
};

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
 * Selected by its `data-region` rather than by `data-panel-money`: task 16.2 added three more
 * `money` panels (positions, equity curve, daily P&L), so the money attribute no longer
 * identifies one panel. `data-region` is passed by the page for exactly this reason.
 */
const tierOnePanel = () => region('tier-1');

/** The positions panel — tier 2's `ds/Panel`, in every state. */
const positionsPanel = () => region('positions');

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
const stubLiveReads = ({ dashboard, allocation = [], equityCurve = [], heatmap = [] }) => {
  vi.spyOn(portfolioModule.portfolioApi, 'getSummary').mockImplementation(() => {
    throw new Error('Portfolio must not read GET /api/portfolio/summary (§7.6, task 16.1).');
  });
  vi.spyOn(dashboardModule.dashboardApi, 'getDashboard').mockImplementation(() => dashboard());
  // The three tier-3 reads answer BARE ARRAYS — no envelope — which is what `pageFields`
  // records for all three and what the page's readers expect.
  vi.spyOn(portfolioModule.portfolioApi, 'getEquityCurve').mockResolvedValue(equityCurve);
  vi.spyOn(portfolioModule.portfolioApi, 'getAllocation').mockResolvedValue(allocation);
  vi.spyOn(portfolioModule.portfolioApi, 'getHeatmap').mockResolvedValue(heatmap);
};

/** One `NormalizedPosition`, as `dashboard_aggregation_service` publishes it. */
const livePosition = (overrides = {}) => ({
  id: 'pos_binance_btc_usdt',
  symbol: 'BTC/USDT',
  exchange_id: 'binance',
  side: 'long',
  contracts: 0.5,
  entry_price: 60000,
  mark_price: 63000,
  notional: 31500,
  leverage: 5,
  unrealized_pnl: 1500,
  liquidation_price: 48000,
  margin: 6300,
  margin_type: 'cross',
  ...overrides,
});

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
    //
    // `getAllByText`, because task 16.2 put the positions region on `ds/ErrorState` too: one
    // dashboard read serves both regions, so one rejection puts both in `error` and both render
    // the `portfolio` context copy.
    await waitFor(() =>
      expect(screen.getAllByText('Could not load your portfolio').length).toBeGreaterThan(0));
    expect(tierOneContainer()).toBeNull();
    // Requirement 14.4: the translation is what a trader reads, so the rejection's own message
    // reaches NEITHER region. Task 13.1 rendered the transport error verbatim in the positions
    // region; `ds/ErrorState` renders only `translateError` output, which is what 16.2 changes.
    expect(tierOnePanel().textContent).not.toMatch(/positions upstream timed out/);
    expect(positionsPanel().textContent).not.toMatch(/positions upstream timed out/);
    expect(positionsPanel().dataset.panelState).toBe('error');
    // The decisive assertion: a failed positions read must NOT claim the account holds nothing,
    // and must not render a table — empty or otherwise (Requirement 14.5).
    expect(screen.queryByText('No open positions')).toBeNull();
    expect(positionsPanel().querySelectorAll('table')).toHaveLength(0);
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
    // Requirement 10.4's empty state, and it is a claim about the account rather than about the
    // read — which is why the failed case above must not be able to produce it.
    expect(screen.getByText('No open positions')).toBeDefined();
    expect(positionsPanel().dataset.panelState).toBe('empty');
    expect(screen.queryByText('Could not load your portfolio')).toBeNull();
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

    // The ledger switch is a labelled radio group as of task 16.2, not two `<button>`s — the
    // selected one of which was a control that did nothing when pressed (Requirement 19.4).
    fireEvent.click(screen.getByRole('radio', { name: /paper/i }));

    await waitFor(() =>
      expect(figureOf(declared('totalValue').label)).toContain('25,000.00'));

    expect(dashboardModule.dashboardApi.getDashboard)
      .toHaveBeenCalledWith({ environment: 'paper' });
    expect(tierOnePanel().querySelector('[data-panel-environment]').dataset.panelEnvironment)
      .toBe('PAPER');
    // The denomination is the one the server reported for the account on screen.
    expect(figureOf(declared('totalValue').label)).toContain('USD');

    // And the positions region, further down the page, carries its OWN label — read from the
    // `GET /api/paper/positions` envelope's `execution_environment` / `is_simulated`, not from
    // the switch above and not from the route (Requirement 28.5, design.md §8.2). A reader who
    // has scrolled to the positions cannot see the page header.
    const badge = positionsPanel().querySelector('[data-environment]');
    expect(badge.dataset.environment).toBe('PAPER');
    expect(positionsPanel().querySelector('[data-panel-environment]').dataset.panelEnvironment)
      .toBe('PAPER');
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
      positions: [livePosition()],
    })));
    stubLiveReads({ dashboard });

    render(<Portfolio />);

    // The normalised position renders, from the endpoint that actually serves positions to a
    // trader (design.md §7.6).
    await waitFor(() => expect(positionsPanel().querySelector('table')).not.toBeNull());
    // The whole row, so the normalisation off `contracts` / `entry_price` / `mark_price` /
    // `unrealized_pnl` is pinned rather than only the symbol. Grouped, never rounded, by
    // `ds/DataTable`'s own formatter — which keeps what the server sent to the right of the
    // point rather than padding it, so an integer entry price reads as one.
    const row = positionRow('BTC/USDT');
    expect(row.textContent).toContain('0.5');
    expect(row.textContent).toContain('60,000');
    expect(row.textContent).toContain('63,000');
    expect(row.textContent).toContain('1,500.00');
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
    //
    // Task 16.2 moved it into a `ds/Alert` above the panel rather than dropping it:
    // `ds/ErrorState` renders only `translateError` output and that translation carries no
    // free-form server message by design (Requirement 14.4), so the alert is the only place
    // the server's account of WHICH environment failed and WHY can still reach the screen.
    await waitFor(() => expect(screen.getByText(reason)).toBeDefined());
    expect(region('positions-degraded')).not.toBeNull();
    // The decisive assertion, and the reason this task exists: the page must not state that the
    // account holds nothing, and must not render the table's empty body either.
    expect(screen.queryByText('No open positions')).toBeNull();
    expect(positionsPanel().dataset.panelState).toBe('error');
    // Not one `<table>` anywhere on the page: the positions region is in `error`, and with the
    // three tier-3 reads answering `[]` their panels are `empty`, so no table exists in the DOM
    // at all. An unreadable read must not produce one, empty or otherwise.
    expect(document.querySelectorAll('table')).toHaveLength(0);
    // The summary above the table goes with it. `risk.open_positions_count` is null here, and
    // a summary of a ledger nobody read is four figures nobody measured.
    expect(region('positions-summary')).toBeNull();
    // The failure is announced, not silent — `ds/Alert severity="warning"` is `role="status"`.
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
    await waitFor(() => expect(screen.getByText('No open positions')).toBeDefined());
    expect(positionsPanel().dataset.panelState).toBe('empty');
    // The server supplied no account of a failure, so there is no alert to render.
    expect(region('positions-degraded')).toBeNull();
    expect(screen.queryByText('Could not load your portfolio')).toBeNull();
    // Requirement 14.1: what is missing, why it matters, and the next action — all three, which
    // `ds/EmptyState` will not render without. Scoped to the positions panel: the three tier-3
    // panels have empty states of their own, and each offers its own next action.
    expect(screen.getByText(/exposed to the market right now/)).toBeDefined();
    expect(within(positionsPanel()).getByRole('link', { name: 'Review strategies' }))
      .toBeDefined();
  });

  it('renders a null open_positions_count as not-available rather than 0', async () => {
    // The narrow case the two tests above do not cover: the positions list read fine, so the
    // ledger renders, but the count the server reports for it is null. BC-2 publishes `null`
    // there and never `0`, so the page may not print a total it did not receive.
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        positions: [livePosition({
          id: 'pos_binance_eth_usdt',
          symbol: 'ETH/USDT',
          side: 'short',
          contracts: 2,
          entry_price: 3200,
          mark_price: 3100,
          notional: 6200,
          unrealized_pnl: 200,
        })],
        openPositionsCount: null,
      })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(positionsPanel().querySelector('table')).not.toBeNull());
    expect(positionRow('ETH/USDT')).not.toBeNull();
    // Task 16.2 moved the count out of a heading and into Requirement 10.3's summary row, as a
    // `ds/Metric`. `null` renders that primitive's marker with a reason, never a 0.
    const count = metricFor('Positions');
    expect(count.dataset.metricAvailable).toBe('false');
    expect(count.querySelector('[data-metric-marker="not-available"]').getAttribute('title'))
      .toMatch(/did not report a count/);
    expect(count.textContent).not.toMatch(/\b0\b/);
    // Not a failure either — the list was read, so the table is shown and no alert is.
    expect(positionsPanel().dataset.panelState).toBe('ready');
    expect(region('positions-degraded')).toBeNull();
    expect(positionsPanel().querySelectorAll('tbody tr')).toHaveLength(1);
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

/* ══════════════════════════════════════════════════════════════════════════════════════
 * Task 16.2 — tiers 2 and 3 (Requirements 10.3, 10.4, 14.5, 15.5)
 *
 * Five claims, one per test, and each is the thing the task exists to make true rather than
 * a restatement of what the primitives already guarantee:
 *
 *   1. the summary region PRECEDES the detail table (Requirement 10.3 is a document-order
 *      claim, so it is asserted on document order);
 *   2. a `null` liquidation price renders the marker carrying `pageFields`' OWN reason —
 *      "Not applicable — spot positions have no liquidation price" — rather than a bare dash
 *      that could be read as a level nobody fetched;
 *   3. a failed positions read renders `ErrorState` and ZERO `<table>` elements
 *      (Requirement 14.5: never an empty table for a failure);
 *   4. zero positions renders `EmptyState` — the opposite fact, distinguishable on screen;
 *   5. a WebSocket frame does not re-render the charts (§13.2).
 *
 * Property 4 (task 16.3) generalises the tier-ordering clause over arbitrary payloads and
 * task 16.4 owns the rest of the example set; neither is written here.
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('Portfolio tiers 2 and 3 (task 16.2)', () => {
  beforeEach(() => {
    cleanup();
    vi.restoreAllMocks();
    charts.renders = 0;
    charts.calls = [];
  });

  it('renders the summary region ABOVE the detail table (Requirement 10.3)', async () => {
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        positions: [
          livePosition(),
          livePosition({ id: 'p2', symbol: 'ETH/USDT', side: 'short', notional: 6200 }),
        ],
        openPositionsCount: 2,
      })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(region('positions-summary')).not.toBeNull());

    const summary = region('positions-summary');
    const table = positionsPanel().querySelector('table');
    expect(table).not.toBeNull();

    // The claim, on document order rather than on which JSX block a reviewer remembers.
     
    expect(summary.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();

    // Both inside the ONE tier-2 container, so Property 4's ordering clause has something to
    // hold: a summary outside it would precede tier 2 rather than being part of it.
    const tierTwo = tierContainer(2);
    expect(document.querySelectorAll(tierSelector(PAGES.PORTFOLIO, 2))).toHaveLength(1);
    expect(tierTwo.contains(summary)).toBe(true);
    expect(tierTwo.contains(table)).toBe(true);

    // And tier 2 precedes tier 3, which is the band boundary either side of it.
     
    expect(tierContainer(1).compareDocumentPosition(tierTwo) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
     
    expect(tierTwo.compareDocumentPosition(tierContainer(3)) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();

    // The four Requirement 10.3 figures, each derived from the rows below it. One long and one
    // short, net exposure 31,500 − 6,200, and the larger of the two notionals names the market.
    // `toContain`, because `ds/Metric` renders `hint` as screen-reader text inside the same
    // element — every one of these four figures is derived, and each says so.
    expect(figureOf('Positions')).toContain('2');
    expect(figureOf('Long / short')).toContain('1 long / 1 short');
    expect(figureOf('Net exposure')).toContain('25,300.00');
    expect(figureOf('Largest position')).toContain('BTC/USDT');
    for (const label of ['Long / short', 'Net exposure', 'Largest position']) {
      expect(metricFor(label).textContent, `${label} states its derivation`)
        .toContain('Derived from the open positions listed below.');
    }

    // §7.6's ten columns, and no eleventh. The venue column the old table defaulted to
    // "binance" is deliberately not among them.
    //
    // "Expand row" is filtered out: it is `ds/DataTable`'s own `laptop:hidden` expander header,
    // which exists because three of these columns are `priority: 3` and leave the row below
    // `--breakpoint-laptop` (Requirement 17.2). It is the primitive's column, not the page's.
    const headers = [...table.querySelectorAll('thead th')]
      .map((th) => th.textContent.trim())
      .filter((text) => text !== 'Expand row');
    expect(headers).toEqual([
      'Market', 'Side', 'Size', 'Entry', 'Mark', 'Notional',
      'Leverage', 'Unrealised P&L', 'Liquidation', 'Margin',
    ]);
    // Requirement 11.3: alignment is a COLUMN property. The eight quantities are `numeric`,
    // which is what makes it consistent by construction rather than per cell.
    const numeric = [...table.querySelectorAll('tbody td[data-align="numeric"]')];
    expect(numeric.length).toBe(8 * 2);
  });

  it('renders a null liquidation price as the marker carrying its declared reason', async () => {
    // A spot position: the venue reports no liquidation level because there is none to report.
    // `pageFields` records this as `absence: 'unmeasurable'` and PERMANENT — no backend change
    // resolves it — with the sentence the cell renders.
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        positions: [livePosition({ liquidation_price: null })],
        openPositionsCount: 1,
      })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(positionsPanel().querySelector('table')).not.toBeNull());
    const row = positionRow('BTC/USDT');
    const cell = row.querySelector('[data-column-key="liquidationPrice"]');

    const marker = cell.querySelector('[data-metric-marker="not-available"]');
    expect(marker, 'a null liquidation price rendered no marker').not.toBeNull();

    // The reason is READ from the declaration, not written at the call site — which is the
    // difference between a dash a trader has to interpret and a cell that says a spot position
    // cannot be liquidated. `ds/DataTable`'s built-in marker carries no reason at all, so this
    // column declares its own `render`.
    const entry = declared('positionLiquidationPrice');
    expect(entry.reason).toMatch(/spot positions have no liquidation price/);
    expect(marker.getAttribute('title')).toBe(entry.reason);
    expect(marker.getAttribute('aria-label')).toBe(`${entry.label}: not available`);

    // Never a zero, and never blank. A liquidation price of 0 is a claim that the position is
    // already liquidated.
    expect(cell.textContent).not.toMatch(/\b0\b/);
    // The rest of the row is unaffected — one absent field is not an unreadable position.
    expect(row.querySelector('[data-column-key="markPrice"]').textContent).toContain('63,000');
  });

  it('renders ErrorState and NO table at all for a failed positions read', async () => {
    stubLiveReads({ dashboard: () => Promise.reject(new Error('redis read timed out')) });

    render(<Portfolio />);

    await waitFor(() => expect(positionsPanel()).not.toBeNull());
    await waitFor(() => expect(positionsPanel().dataset.panelState).toBe('error'));

    // Requirement 14.5, as an absence: `ds/Panel` renders no children outside
    // `ready`/`refreshing`, so the table is not in the DOM rather than in it and empty.
    expect(positionsPanel().querySelectorAll('table')).toHaveLength(0);
    expect(document.querySelectorAll('table')).toHaveLength(0);
    expect(region('positions-summary')).toBeNull();

    // `ds/ErrorState`: translated copy plus a retry, and never the rejection's own message
    // (Requirement 14.4).
    expect(positionsPanel().textContent).not.toMatch(/redis read timed out/);
    expect(positionsPanel().textContent).toMatch(/Could not load your portfolio/);
    // The retry is live, not decorative (Requirement 19.4): `ds/ErrorState` renders it only
    // when the translation reports the failure retryable, and `portfolio` context copy does.
    expect(within(positionsPanel()).getByRole('button', { name: /try again/i })).toBeDefined();
    expect(positionsPanel().querySelector('[data-error-retryable]').dataset.errorRetryable)
      .toBe('true');

    // And the empty state is unreachable from here: a failed read makes no claim about the
    // account, so nothing on screen may say the account holds nothing.
    expect(screen.queryByText('No open positions')).toBeNull();
  });

  it('renders EmptyState — not an empty table — for zero positions', async () => {
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({ positions: [], openPositionsCount: 0 })),
    });

    render(<Portfolio />);

    await waitFor(() => expect(screen.getByText('No open positions')).toBeDefined());

    expect(positionsPanel().dataset.panelState).toBe('empty');
    expect(positionsPanel().querySelector('[data-empty-variant]').dataset.emptyVariant)
      .toBe('no-data');
    // A summary of nothing is nothing, and a table with no rows is the `<td colSpan>No trades
    // found</td>` `ds/EmptyState` replaces.
    expect(positionsPanel().querySelectorAll('table')).toHaveLength(0);
    expect(region('positions-summary')).toBeNull();
    // Requirement 14.1's three parts. `ds/EmptyState` throws in development without all three,
    // so their presence is structural — what is asserted here is that this page's copy says
    // something true about a portfolio rather than "No data".
    expect(screen.getByText('No open positions')).toBeDefined();
    expect(screen.getByText(/deployed strategy opens one/)).toBeDefined();
    expect(within(positionsPanel()).getByRole('link', { name: 'Review strategies' }))
      .toBeDefined();
  });

  it('does not re-render the charts when a WebSocket frame arrives (§13.2)', async () => {
    stubLiveReads({
      dashboard: () => Promise.resolve(dashboardBody({
        positions: [livePosition()],
        openPositionsCount: 1,
      })),
      allocation: [
        { asset: 'BTC', value_usd: 31500, pct: 70 },
        { asset: 'USDT', value_usd: 13500, pct: 30 },
      ],
      equityCurve: [
        { timestamp: '2024-03-11T00:00:00Z', equity: 60000 },
        { timestamp: '2024-03-12T00:00:00Z', equity: 65000 },
      ],
      heatmap: [
        { date: '2024-03-11', pnl_usd: 800 },
        { date: '2024-03-12', pnl_usd: -150 },
      ],
    });

    render(<Portfolio />);

    // All three tier-3 regions go through `ds/Chart`, and both axis labels are present on each
    // — Requirement 15.5's half that is a required prop.
    await waitFor(() => expect(chartFigures()).toHaveLength(3));
    const labels = chartFigures().map((figure) => [
      figure.dataset.chartKind,
      figure.dataset.chartXLabel,
      figure.dataset.chartYLabel,
    ]);
    expect(labels).toEqual([
      ['bar', 'Asset', 'Share of portfolio (%)'],
      ['area', 'Date', 'Equity (USDT)'],
      ['bar', 'Date', 'Realised P&L (USDT)'],
    ]);
    // The allocation's one `brand` series is what replaced `const COLORS = [C.orange, C.purple,
    // C.cyan, C.gold, C.t3]` — five entries that resolved to four colours after M1. There is no
    // fifth distinct CATEGORICAL hue in `styles/tokens.css` to repair it with, and `ds/Chart`
    // has no `pie` kind and no colour prop, so assets are told apart by position on a labelled
    // axis instead. One series, one declared token, no palette.
    expect(chartFigures()[0].dataset.chartSeries).toBe('pct:brand');
    expect(chartFigures().every((f) => f.dataset.chartSeries.split(',').length === 1)).toBe(true);

    const before = charts.renders;
    expect(before).toBeGreaterThanOrEqual(3);

    // A real frame, through the real client, on the two channels that carry position and P&L
    // movement. Recharts re-renders are the most expensive thing on a trading page and an
    // equity curve is a historical series — there is no requirement that it be tick-live.
    await act(async () => {
      wsClient.handleMessage({
        data: JSON.stringify({
          type: 'POSITION_UPDATED',
          payload: { symbol: 'BTC/USDT', unrealized_pnl: 9999, mark_price: 70000 },
        }),
      });
      wsClient.handleMessage({
        data: JSON.stringify({ type: 'pnl', payload: { symbol: 'BTC/USDT', pnl: 4242 } }),
      });
      await Promise.resolve();
    });

    expect(charts.renders, 'a WebSocket frame re-entered ds/Chart').toBe(before);
    // The charts still show what the REST read reported, unchanged by the frame.
    expect(chartFigures()[1].dataset.chartPoints).toBe('2');

    // An explicit period change IS allowed to refresh them — it is a new REST read, which is
    // the only other thing §13.2 permits.
    fireEvent.click(screen.getByRole('radio', { name: '30D' }));
    await waitFor(() => expect(charts.renders).toBeGreaterThan(before));
    expect(portfolioModule.portfolioApi.getEquityCurve).toHaveBeenLastCalledWith(30);
    expect(portfolioModule.portfolioApi.getHeatmap).toHaveBeenLastCalledWith(1);
  });

  it('reports the three history reads as unavailable on the paper ledger', async () => {
    // Requirement 19.3, and the deletion of a fabricated row: this page used to synthesise
    // `{asset: 'USD (Simulated)', percentage: 100}` for paper, with the account's equity as its
    // value. `/api/portfolio/allocation`, `/equity-curve` and `/heatmap` serve the live account
    // only, so the honest answer is that the capability does not exist here.
    stubLiveReads({ dashboard: () => Promise.resolve(dashboardBody()) });
    vi.spyOn(paperModule.paperApi, 'getPositions').mockResolvedValue({
      positions: [],
      count: 0,
      execution_environment: 'paper',
      is_simulated: true,
    });

    render(<Portfolio />);

    await waitFor(() => expect(tierOneContainer()).not.toBeNull());
    fireEvent.click(screen.getByRole('radio', { name: /paper/i }));

    await waitFor(() => expect(region('allocation').dataset.panelState).toBe('unavailable'));
    for (const name of ['allocation', 'equity-curve', 'daily-pnl']) {
      expect(region(name).dataset.panelState, `${name} on paper`).toBe('unavailable');
      expect(region(name).textContent).toMatch(/serve the live account only/);
    }
    // No chart, and no fabricated 100% row for one to plot.
    expect(chartFigures()).toHaveLength(0);
    expect(screen.queryByText(/USD \(Simulated\)/)).toBeNull();
  });
});
