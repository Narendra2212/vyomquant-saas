/**
 * @fileoverview A fresh account — how many empty panels, and how many primary actions.
 *
 * retail-ui-simplification task 12.2. Requirements 8.1, 8.2, 8.3, 8.5, 18.1, 19.4.
 * design.md §5.5 (last row), §9.3, Property 9, Property 10.
 *
 * ===========================================================================
 * WHAT THIS FILE IS FOR
 * ===========================================================================
 * Requirement 8.5 asks for a test that "renders each page against a zero-data
 * fixture and asserts the count of visible empty panels and the count of primary
 * actions, so that 8.1 and 8.2 are measurements rather than screenshots".
 * Nothing counted either one before this file.
 *
 * Written BEFORE any panel's weight changes (design.md §5.1, Decision D2). The
 * numbers below are TODAY's, taken by rendering the tree as it is, so tasks 12.6
 * and 12.7 each have a number to move and the move is observed rather than
 * asserted. A file written after the change would only ever have seen the state
 * it was copied from.
 *
 * ===========================================================================
 * THE FACTS THIS FILE VERIFIES RATHER THAN ASSUMES
 * ===========================================================================
 * design.md §9.3 states four things about `Dashboard.jsx` on a fresh account.
 * Three of them are DOM facts and are asserted here directly; the fourth is what
 * the seeded counts are for.
 *
 *   1. It renders **7** `ds/Panel` instances. Confirmed: 7 in the DOM.
 *   2. **6** of them carry an empty-state branch. Confirmed the way it matters —
 *      on a zero-data account 6 of the 7 resolve to `empty` and the seventh does
 *      not, which is the same claim taken from the rendered tree rather than from
 *      a reading of the JSX.
 *   3. All six resolve **simultaneously**. Confirmed: they are counted in one
 *      render of one settled page, not one panel at a time.
 *   4. They resolve **at equal weight, with no single element marked as the next
 *      thing to do**. Confirmed as a count: zero elements on the page present as
 *      a primary action. That is the number task 12.6 raises to one.
 *
 * ===========================================================================
 * WHAT "PRESENTS AS A PRIMARY ACTION" MEANS HERE, AND WHY
 * ===========================================================================
 * `[data-ds="command-button"][data-ds-intent="primary"]` — the design system's
 * own published marker for "the one thing this surface wants the trader to do".
 * `ds/CommandButton:275` writes it on the rendered control, and its docblock says
 * why it is there rather than inferred: "The distinction that does matter is
 * carried where it is actually read: `data-ds-intent` on the rendered control".
 *
 * Read from a data attribute and never from a class string. That is the same rule
 * `dashboard-kill-switch.test.jsx:176` follows for the environment badge — data
 * attributes and text, never class strings — and it is what stops this count from
 * breaking on a Tailwind edit that changes nothing a trader can see.
 *
 * **The consequence, named rather than left implicit.** `ds/ActionControl`
 * publishes no such attribute. It takes an `intent` and renders
 * `border-brand text-brand` for `primary`, and `ds/EmptyState` hands it
 * `intent="primary"` for every `no-data` panel — so on a fresh Dashboard there
 * are six action controls styled as primary and, by this count, zero primary
 * actions. That is not an oversight in the count; it is the distinction
 * Requirement 8.1 is about. Six elements marked identically mark nothing: none of
 * them is "the one element carrying a primary action" because there is no *one*.
 * The count that matters is of an element DISTINGUISHED from its neighbours, and
 * `data-ds-intent="primary"` on a `ds/CommandButton` is the only distinction the
 * design system publishes.
 *
 * So the two numbers are counted separately and both are seeded: `emptyActions`
 * is how many actions the empty panels offer (six on Dashboard — Requirement
 * 14.1 is already satisfied and §9.3 says so), and `primaryActions` is how many
 * of them are marked as the next thing to do (zero). Task 12.6 moves the second
 * to one WITHOUT lowering the first: §9.3's "the other five keep their action and
 * lose the emphasis", and Requirement 19.4's "reduced weight is not collapse",
 * are the same sentence read from two directions, and two counts are what make
 * them separable.
 *
 * ===========================================================================
 * WHAT THIS FILE MUST NOT WEAKEN
 * ===========================================================================
 * `ds/EmptyState.test.jsx` already enforces the copy half AT THE PRIMITIVE, in
 * development, by a throw: `renders what is missing, why it matters and the next
 * action`; `throws in development for each missing field`; `rejects copy that is
 * present but blank`; `requires a clear-filters action for no-match`; and the
 * two-variant `EMPTY_VARIANTS` assertion. So "you have none of these" and "a
 * filter is hiding them" cannot be collapsed, and Requirement 8.3's distinction
 * is held one level down from here.
 *
 * Nothing below weakens any of it, and `every empty panel still names what is
 * missing, why it matters and what to do` re-reads the primitive's own
 * `EMPTY_VARIANTS` and `REQUIRED_EMPTY_FIELDS` exports rather than restating
 * them, so a change to the primitive's contract reaches this file rather than
 * passing beside it. `empty` also stays distinguishable from `unavailable`
 * (Requirement 19.4): `no panel reports a fresh account as unavailable` asserts
 * that a zero-data account renders the state that says "you have none of these"
 * and never the one that says "we could not read this".
 *
 * ===========================================================================
 * THE FIXTURE, AND WHAT IS REAL
 * ===========================================================================
 * One zero-data fixture per read: every endpoint answers 2xx with no rows. That
 * is `dashboard-tier1.test.jsx`'s posture — its header says its fixtures hold an
 * empty account "precisely so that tier 1 is the only thing on screen" — and the
 * same posture `standing-prose.test.js` takes, so the three files describe one
 * screen.
 *
 * Real: every `components/ds/*` primitive, `hooks/usePanelState`,
 * `design/pageFields`, `design/pageHierarchy`, `design/errorCopy`, and all five
 * pages. Doubles: the HTTP transport (so no request leaves the process and the
 * fixture chooses what the server answered), the WebSocket client, the lazily
 * imported `ds/Chart` — recharts needs layout APIs jsdom does not implement — and
 * `pages/StrategyBuilder`, an alternate view `pages/Strategies` can swap itself
 * for and which a fresh account never reaches.
 *
 * ===========================================================================
 * BLIND SPOTS
 * ===========================================================================
 *   1. **"Collapsed" is not measurable here.** Requirement 8.1 permits the
 *      remaining panels to be "at reduced visual weight OR collapsed", and jsdom
 *      has no geometry. A panel inside a closed `ds/Accordion` is not in the DOM
 *      at all, so it would leave `emptyPanels` — which is right; one behind a
 *      `hidden` attribute or a `max-h-0` class would not, which is wrong. If task
 *      12.7 takes the second route this file needs an extra clause.
 *   2. **Which action is primary is a [JUDGEMENT]** that goes to the requester
 *      (§9.3). This file makes "exactly one" checkable and decides nothing about
 *      which one it is.
 */

import { cleanup, render, waitFor } from '@testing-library/react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DOUBLES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * The HTTP transport. `vi.hoisted` because the `vi.mock` factory below is hoisted above
 * these imports and closes over this object.
 *
 * Every `src/api/modules/*` function is REAL and routes through this, so a read this file
 * forgot to prime still cannot reach the network: it resolves `{}`, which
 * `usePanelState.isEmptyPayload` reads as an empty collection — the same state a fresh
 * account is in. `raw` is the DEFAULT export, the axios instance, which has to be stubbed
 * separately because the named helpers unwrap the body and it does not:
 * `lib/registryClient.js` reads `response.headers` itself and, left real, issues
 * `GET /api/strategy-operations/registry/timeframes` over jsdom's XHR.
 */
const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  publicGet: vi.fn(),
  raw: vi.fn(),
}));

vi.mock('../../../src/apiClient', async (importOriginal) => {
  // `ApiError` and the rest stay real: `design/errorCopy.js` classifies by reading them.
  const actual = await importOriginal();
  const client = {
    get: (...args) => transport.raw(...args),
    post: (...args) => transport.raw(...args),
    put: (...args) => transport.raw(...args),
    delete: (...args) => transport.raw(...args),
    patch: (...args) => transport.raw(...args),
    request: (...args) => transport.raw(...args),
    defaults: { headers: {} },
    interceptors: { request: { use: () => 0 }, response: { use: () => 0 } },
  };
  return {
    ...actual,
    default: client,
    get: (...args) => transport.get(...args),
    post: (...args) => transport.post(...args),
    put: (...args) => transport.put(...args),
    del: (...args) => transport.del(...args),
    patch: (...args) => transport.patch(...args),
    publicGet: (...args) => transport.publicGet(...args),
  };
});

vi.mock('../../../src/websocketClient', () => {
  const unsubscribe = () => {};
  const client = {
    subscribe: vi.fn(() => unsubscribe),
    subscribeChannel: vi.fn(() => unsubscribe),
    subscribePnL: vi.fn(() => unsubscribe),
    subscribeStrategyStatus: vi.fn(() => unsubscribe),
    onOpen: vi.fn(() => unsubscribe),
    onStatusChange: vi.fn(() => unsubscribe),
    acquire: vi.fn(),
    release: vi.fn(),
    send: vi.fn(),
    getStatus: vi.fn(() => 'disconnected'),
    isConnected: vi.fn(() => false),
  };
  return { __esModule: true, default: client, wsClient: client };
});

vi.mock('../../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

vi.mock('../../../src/pages/StrategyBuilder', () => ({
  __esModule: true,
  default: () => <div data-testid="builder-stub" />,
}));

import { dashboardApi } from '../../../src/api/modules/dashboard';
import { exchangeApi } from '../../../src/api/modules/exchange';
import { libraryApi } from '../../../src/api/modules/library';
import { ordersApi } from '../../../src/api/modules/orders';
import { paperApi } from '../../../src/api/modules/paper';
import { portfolioApi } from '../../../src/api/modules/portfolio';
import { strategiesApi } from '../../../src/api/modules/strategies';
import { EMPTY_VARIANTS, REQUIRED_EMPTY_FIELDS } from '../../../src/components/ds/EmptyState';
import Dashboard from '../../../src/pages/Dashboard';
import LiveTrading from '../../../src/pages/LiveTrading';
import Portfolio from '../../../src/pages/Portfolio';
import Strategies from '../../../src/pages/Strategies';
import TradeHistory from '../../../src/pages/TradeHistory';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE ZERO-DATA FIXTURE
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * `GET /api/dashboard` for an account that has done nothing: no venue, no position, no
 * execution, no strategy, no equity history.
 *
 * The `null`s are the fields whose absence is a reading rather than a zero —
 * `risk.current_drawdown_pct_v2` with no equity series, `health.exchange_api_latency_ms`
 * with nothing measured. `pageFields.js:415` records why the latter must never render
 * `0 ms`, and a fixture that sent `0` here would assert the opposite of the declaration.
 *
 * `kill_switch_active: false` and `circuit_breaker_armed: true` are the resting pair: a
 * fresh account is not halted, so no alert band renders and nothing on the page is
 * competing with the empty panels for attention.
 */
const freshDashboard = () => ({
  environment: 'live',
  overview: {
    total_value: 0,
    total_equity: 0,
    available_balance: 0,
    today_pnl: 0,
    today_realized_pnl: 0,
    unrealized_pnl: 0,
    cumulative_pnl: 0,
    realized_pnl: 0,
    currency: 'USDT',
  },
  positions: [],
  executions: [],
  degraded: null,
  risk: {
    current_drawdown_pct_v2: null,
    open_positions_count: 0,
    circuit_breaker_armed: true,
    kill_switch_active: false,
    risk_level: 'low',
  },
  health: { exchange_api_latency_ms: null, order_state_sync_status: 'active' },
  exchange: { total_exchanges: 0, connected_exchanges: 0, exchanges: [] },
  strategies: { total: 0, active: 0, items: [] },
  recent_activity: { signals: [], insights: [] },
  equity_curve: [],
});

/**
 * Prime every read the five pages issue on mount, each with the shape its own endpoint
 * publishes and no rows in it.
 *
 * Spied on the module objects rather than by mocking `src/api`: `api`, `endpoints` and the
 * default export are all built from these same objects (`src/api/index.js`), so one spy
 * covers every import form the five pages use between them.
 */
const primeReads = () => {
  transport.get.mockResolvedValue({});
  transport.publicGet.mockResolvedValue({});
  transport.raw.mockResolvedValue({ status: 200, statusText: 'OK', headers: {}, data: {} });
  for (const fn of [transport.post, transport.put, transport.del, transport.patch]) {
    fn.mockRejectedValue(new Error('no mutation is issued on mount'));
  }

  vi.spyOn(dashboardApi, 'getDashboard').mockResolvedValue(freshDashboard());

  vi.spyOn(strategiesApi, 'list').mockResolvedValue({ strategies: [], total: 0 });
  vi.spyOn(strategiesApi, 'listDeployments').mockImplementation((strategyId) =>
    Promise.resolve({ strategy_id: strategyId, deployments: [], total: 0 }));

  vi.spyOn(ordersApi, 'getOpenOrders').mockResolvedValue([]);
  vi.spyOn(ordersApi, 'getHistory').mockResolvedValue([]);

  // BARE ARRAYS, not envelopes. `Portfolio.jsx:991`, `:1008` and `:1026` each check
  // `Array.isArray(res.value)` and report an error state otherwise — the allocation route
  // "answers a BARE ARRAY of `{asset, value_usd, pct}`" and the other two are the same
  // shape. An envelope here puts three chart panels into `error`, and an error panel is not
  // an empty one: Requirement 19.4 keeps those two states apart and so must the fixture.
  vi.spyOn(portfolioApi, 'getAllocation').mockResolvedValue([]);
  vi.spyOn(portfolioApi, 'getEquityCurve').mockResolvedValue([]);
  vi.spyOn(portfolioApi, 'getHeatmap').mockResolvedValue([]);

  vi.spyOn(paperApi, 'getPositions').mockResolvedValue({
    positions: [],
    count: 0,
    execution_environment: 'paper',
    is_simulated: true,
  });
  vi.spyOn(paperApi, 'getTrades').mockResolvedValue({
    trades: [],
    count: 0,
    execution_environment: 'paper',
    is_simulated: true,
  });

  vi.spyOn(exchangeApi, 'list').mockResolvedValue([]);

  vi.spyOn(libraryApi, 'myStrategies').mockResolvedValue({ items: [], total: 0 });
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE SEED — today's numbers, measured on the tree at task 12.2
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * `Dashboard` plus Requirement 8.4's four, each with what a fresh account sees.
 *
 *   panels         every `ds/Panel` in the DOM, whatever state it resolved to.
 *   emptyPanels    panels that PRESENT as empty — `[data-panel-state="empty"]` or a panel
 *                  containing a `[data-empty-variant]`. Requirement 8.2's count, capped at
 *                  3 by 8.2, which is what task 12.6 (Dashboard) and 12.7 (the other four)
 *                  bring it down to. `censusOf` records why the second half of that
 *                  definition is needed and which panel needs it.
 *   emptyActions   the next actions those empty panels offer, one per `ds/EmptyState`.
 *                  Requirement 14.1's half, already satisfied — this is seeded so that
 *                  task 12.6 can be seen to reduce the EMPHASIS without removing an
 *                  action (Requirement 19.4: reduced weight is not collapse).
 *   primaryActions elements marked as the next thing to do —
 *                  `[data-ds="command-button"][data-ds-intent="primary"]`. Requirement
 *                  8.1 wants exactly one. Dashboard has none today, which is §9.3's
 *                  "no element marked as the next thing to do" as a number.
 *
 * Asserted EXACTLY. A `<=` on `emptyPanels` would let the count creep back up after task
 * 12.6 lowered it, and a `>=` on `primaryActions` would let a second one appear — and two
 * primary actions on a fresh account is the same defect as none.
 */
const FRESH_ACCOUNT = Object.freeze({
  'pages/Dashboard.jsx': Object.freeze({
    panels: 7, emptyPanels: 6, emptyActions: 6, primaryActions: 0,
  }),
  /*
   * `Strategies` is the one page already at Requirement 8.1's target: `:1778`'s
   * `<CommandButton intent="primary" icon={Plus}>` is one element marked as the next thing
   * to do, and it is the only one. Both of its panels present as empty, which is inside 8.2's
   * cap of 3. Seeded, not celebrated — task 12.7 still has to keep it there.
   */
  'pages/Strategies.jsx': Object.freeze({
    panels: 2, emptyPanels: 2, emptyActions: 2, primaryActions: 1,
  }),
  'pages/Portfolio.jsx': Object.freeze({
    panels: 5, emptyPanels: 4, emptyActions: 4, primaryActions: 0,
  }),
  'pages/TradeHistory.jsx': Object.freeze({
    panels: 2, emptyPanels: 1, emptyActions: 1, primaryActions: 0,
  }),
  /*
   * `LiveTrading` has FIVE panels and only ONE presents as empty, which looks like a page
   * already inside Requirement 8.2 and is worth reading correctly: its tier-2 and tier-3
   * panels are projections of ONE read whose body is not empty (the dashboard payload carries
   * `overview`, `risk` and `health` whether or not there is a position), so `usePanelState`
   * resolves them `ready` and they render a table with no rows rather than an `ds/EmptyState`.
   * The one that does present as empty is the deployment selector — `[]` deployments, which
   * task 20.1d gave an `EmptyState` quoting the declared reason.
   *
   * So 8.2's cap is met here and 8.1's is not: five panels on screen, none of them marked as
   * the next thing to do. That is what task 12.7 changes on this page, and it is a different
   * change from Dashboard's.
   */
  'pages/LiveTrading.jsx': Object.freeze({
    panels: 5, emptyPanels: 1, emptyActions: 1, primaryActions: 0,
  }),
});

const SUITES = Object.freeze([
  { file: 'pages/Dashboard.jsx', element: <Dashboard /> },
  { file: 'pages/Strategies.jsx', element: <Strategies /> },
  { file: 'pages/Portfolio.jsx', element: <Portfolio /> },
  { file: 'pages/TradeHistory.jsx', element: <TradeHistory /> },
  { file: 'pages/LiveTrading.jsx', element: <LiveTrading /> },
]);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE CENSUS
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const PRIMARY_ACTION = '[data-ds="command-button"][data-ds-intent="primary"]';

/** One action control inside an empty state: `ds/ActionControl` renders a link or a button. */
const ACTION_CONTROL = 'a[href], button';

/**
 * What one settled page shows a fresh account.
 *
 * `states` and `variants` are carried alongside the counts so a failure can say WHICH
 * panel changed rather than only that the total moved.
 */
const censusOf = (root) => {
  const panels = [...root.querySelectorAll('[data-panel-state]')];
  const emptyStates = [...root.querySelectorAll('[data-empty-variant]')];

  /*
   * "PRESENTS AS EMPTY", NOT "IS IN THE `empty` STATE", AND THE DIFFERENCE IS MEASURED.
   *
   * `[data-panel-state="empty"]` alone under-counts, and Dashboard is the case that proves
   * it: five of its panels resolve to `empty` through `usePanelState`, and the sixth —
   * exchange health at `:2624` — is `ready` and renders `<EmptyState {...HEALTH_EMPTY} />`
   * as its own child, because the read succeeded and answered a list with no venue in it.
   * A trader cannot tell those two apart and Requirement 8.2 is about what is on screen, so
   * a count that saw five would be measuring the hook rather than the page.
   *
   * `ds/EmptyState` publishing `data-empty-variant` is what makes this assertable rather
   * than eyeballed, which is exactly the job that attribute was given.
   */
  const presentsAsEmpty = panels.filter(
    (panel) => panel.dataset.panelState === 'empty'
      || panel.querySelector('[data-empty-variant]') !== null,
  );

  return {
    panels: panels.length,
    emptyPanels: presentsAsEmpty.length,
    emptyActions: emptyStates.reduce(
      (total, state) => total + state.querySelectorAll(ACTION_CONTROL).length,
      0,
    ),
    primaryActions: root.querySelectorAll(PRIMARY_ACTION).length,
    states: panels.map((p) => p.dataset.panelState).sort(),
    variants: emptyStates.map((s) => s.dataset.emptyVariant),
    emptyStates: emptyStates.length,
  };
};

const measure = async (suite) => {
  primeReads();

  const { container } = render(<MemoryRouter>{suite.element}</MemoryRouter>);

  // Settled, not merely mounted: a panel still `loading` or `idle` has not decided whether
  // it is empty, and counting one that has not decided is counting a skeleton.
  await waitFor(
    () => {
      expect(
        container.querySelectorAll('[data-panel-state]').length,
        `${suite.file} rendered no ds/Panel at all`,
      ).toBeGreaterThan(0);
      expect(
        container.querySelectorAll('[data-panel-state="loading"],[data-panel-state="idle"]')
          .length,
        `${suite.file} still has a panel that has not decided what it renders`,
      ).toBe(0);
    },
    { timeout: 10000 },
  );

  const census = censusOf(container);

  cleanup();
  vi.restoreAllMocks();

  return census;
};

/** Filled by `beforeAll`, keyed as `FRESH_ACCOUNT` is. */
const MEASURED = new Map();

beforeAll(async () => {
  // Sequential on purpose. Five pages mounted concurrently would share one jsdom document
  // and one set of module spies, so one page's fixture would be visible to another.
  for (const suite of SUITES) {
    MEASURED.set(suite.file, await measure(suite));
  }
}, 180000);

afterAll(() => {
  cleanup();
  vi.restoreAllMocks();
});

const measured = (file) => {
  const row = MEASURED.get(file);
  expect(row, `${file} was never measured — the harness did not render it`).toBeDefined();
  return row;
};

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 1. The measurement is live
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('a fresh account: the measurement is live', () => {
  it('rendered Dashboard and Requirement 8.4\'s four, and measured each one', () => {
    // Without this, an exception inside `beforeAll` — or a harness that rendered nothing —
    // would leave every count below passing over an empty map.
    expect(MEASURED.size).toBe(SUITES.length);
    expect([...MEASURED.keys()].sort()).toEqual(Object.keys(FRESH_ACCOUNT).sort());

    for (const suite of SUITES) {
      expect(measured(suite.file).panels, `${suite.file} rendered no panel`)
        .toBeGreaterThan(0);
    }
  });

  it('is measuring a fresh account, not a populated one', () => {
    // The fixture is the subject, so it has to be checked: a fixture that accidentally
    // carried rows would report a low empty-panel count and the file would pass by
    // measuring the wrong screen. Every one of the five pages has at least one panel that
    // resolved to `empty`, which only a zero-data account produces.
    for (const suite of SUITES) {
      expect(measured(suite.file).emptyPanels, `${suite.file} has no empty panel`)
        .toBeGreaterThan(0);
    }
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 2. Requirement 8.5's two counts, seeded exactly
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('a fresh account: the seeded counts', () => {
  for (const [file, seed] of Object.entries(FRESH_ACCOUNT)) {
    it(`${file} shows ${seed.emptyPanels} empty panels and ${seed.primaryActions} primary actions`, () => {
      const census = measured(file);

      expect(
        {
          panels: census.panels,
          emptyPanels: census.emptyPanels,
          emptyActions: census.emptyActions,
          primaryActions: census.primaryActions,
        },
        `${file} on a fresh account: panel states ${census.states.join(', ')}\n`
          + `empty variants ${census.variants.join(', ') || '(none)'}\n\n`
          + `If \`emptyPanels\` FELL and \`primaryActions\` ROSE to 1, this is tasks 12.6/12.7\n`
          + `landing — update the seed in this file IN THAT COMMIT (Requirement 22.2).\n`
          + `If \`emptyActions\` fell with it, an action was REMOVED rather than de-emphasised,\n`
          + `which Requirement 19.4 forbids: reduced weight is not collapse, and the state\n`
          + `name, its copy and its action all stay.\n`
          + `If \`primaryActions\` is above 1, a fresh account has two next actions, which is\n`
          + `the same defect as none (Requirement 8.1).`,
      ).toEqual({
        panels: seed.panels,
        emptyPanels: seed.emptyPanels,
        emptyActions: seed.emptyActions,
        primaryActions: seed.primaryActions,
      });
    });
  }

  it('shows Requirement 8.2 is not met yet on any of the five, which is what 12.6 and 12.7 are for', () => {
    // Non-vacuity for the direction of travel: if every page were already inside the cap,
    // the two closing commits would have nothing to do and these seeds would be describing
    // a finished state rather than a starting one.
    const over = Object.entries(FRESH_ACCOUNT)
      .filter(([, seed]) => seed.emptyPanels > 3)
      .map(([file]) => file);

    expect(over.length, `pages within Requirement 8.2's cap of 3 already: ${
      Object.keys(FRESH_ACCOUNT).filter((f) => FRESH_ACCOUNT[f].emptyPanels <= 3).join(', ')
    }`).toBeGreaterThan(0);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 3. design.md §9.3's four claims about Dashboard, verified
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('a fresh account: Dashboard is §9.3\'s case', () => {
  const dashboard = () => measured('pages/Dashboard.jsx');

  it('renders 7 panels, 6 of which resolve to empty, all at once', () => {
    // §9.3's first three claims, taken from one render of one settled page. The seventh
    // panel is the one with no empty branch.
    const census = dashboard();
    expect(census.panels).toBe(7);
    expect(census.emptyPanels).toBe(6);
    expect(census.panels - census.emptyPanels).toBe(1);
    // One `ds/EmptyState` per empty panel — no panel is empty without saying so, and none
    // renders two.
    expect(census.emptyStates).toBe(census.emptyPanels);
  });

  it('marks none of the six as the next thing to do', () => {
    // §9.3's fourth claim as a number: six actions on screen, none of them distinguished.
    // This is the assertion task 12.6 turns from 0 to 1, and the one that fails the moment
    // a second panel is also marked primary.
    const census = dashboard();
    expect(census.emptyActions).toBe(6);
    expect(census.primaryActions).toBe(0);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 4. What must not be lost — Requirements 8.3 and 19.4
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('a fresh account: empty is not unavailable, and every empty panel still explains itself', () => {
  it('reports no panel as unavailable, because a fresh account is readable', () => {
    // Requirement 19.4 and Property 9. `empty` means "you have none of these"; `unavailable`
    // means "we could not read this". A zero-data account is the first, and a page that
    // reported the second would be claiming a failed read that did not happen. Asserted
    // here so a later commit cannot buy a lower empty-panel count by re-labelling a panel.
    const mislabelled = SUITES.filter((suite) =>
      measured(suite.file).states.includes('unavailable')
      || measured(suite.file).states.includes('error'))
      .map((suite) => `${suite.file} — ${measured(suite.file).states.join(', ')}`);

    expect(
      mislabelled,
      'A zero-data account is EMPTY, not unavailable and not failed. These pages report\n'
        + 'otherwise, so either the fixture is wrong or the page is collapsing two states\n'
        + `Requirement 19.4 keeps apart:\n${mislabelled.map((r) => `  ${r}`).join('\n')}`,
    ).toEqual([]);
  });

  it('renders only the two declared empty variants, read from the primitive', () => {
    // Requirement 11.5 / 8.3's split, re-read from `ds/EmptyState`'s own export rather than
    // retyped, so a third variant added there reaches this file.
    expect(EMPTY_VARIANTS).toEqual(['no-data', 'no-match']);
    expect(REQUIRED_EMPTY_FIELDS).toEqual(['headline', 'body', 'action']);

    for (const suite of SUITES) {
      for (const variant of measured(suite.file).variants) {
        expect(EMPTY_VARIANTS, `${suite.file} rendered variant ${variant}`).toContain(variant);
      }
    }
  });

  it('gives every empty panel a next action, on every one of the five pages', () => {
    // `ds/EmptyState` throws in development for a missing `headline`, `body` or `action`, so
    // the copy half is already held at the primitive — every panel that rendered at all has
    // all three. What this adds is the COUNT: one action per empty panel, on every page, so
    // a later commit that de-emphasises five actions cannot quietly delete one of them.
    for (const suite of SUITES) {
      const census = measured(suite.file);
      expect(census.emptyActions, `${suite.file}: ${census.emptyStates} empty states, `
        + `${census.emptyActions} actions`).toBe(census.emptyStates);
    }
  });
});
