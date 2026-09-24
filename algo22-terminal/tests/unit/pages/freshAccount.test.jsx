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
 * ---------------------------------------------------------------------------
 * TASK 12.6 HAS SINCE MOVED DASHBOARD'S ROW, AND IT WAS OBSERVED MOVING
 * ---------------------------------------------------------------------------
 * `pages/Dashboard.jsx` was seeded at 7 panels / 6 empty / 6 actions / 0 primary
 * and now reads 4 / 3 / 3 / 1. The seed was confirmed failing against the page as
 * it stood before that commit, which is the whole reason it was written first —
 * the note on the row itself records what each number did and why.
 *
 * ---------------------------------------------------------------------------
 * AND TASK 12.7'S FIRST HALF HAS MOVED PORTFOLIO'S, OBSERVED THE SAME WAY
 * ---------------------------------------------------------------------------
 * `pages/Portfolio.jsx` was seeded at 5 panels / 4 empty / 4 actions / 0 primary
 * and now reads 2 / 1 / 1 / 1, confirmed failing against the page as it stood
 * before that commit. The note on the row itself records what each number did.
 *
 * `pages/Strategies.jsx` is the one page that needed nothing: it was seeded at
 * one primary action and two empty panels, which is inside Requirement 8.2's cap,
 * so 12.7 verified it and left the file unedited.
 *
 * `pages/TradeHistory.jsx` and `pages/LiveTrading.jsx` are still at their task
 * 12.2 seeds. Both are already inside 8.2's cap and both are at zero primary
 * actions, so what is left is 8.1 alone. Task 12.7's second half is the commit
 * that moves them and this paragraph is how its diff is scoped.
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
 * All four are §9.3's reading of the page BEFORE task 12.6. Three of them are now
 * true only with the disclosure open, and that is what `keeps the three collapsed
 * zones' state, copy and action one keypress away` asserts — the claims did not
 * stop holding, they moved behind one toggle.
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
 *      `hidden` attribute or a `max-h-0` class would not, which is wrong. Tasks
 *      12.6 and 12.7 both took the first route, so Dashboard's and Portfolio's
 *      lowered counts are real collapses and each one's opened census proves the
 *      three panels are still there. If the rest of 12.7 takes the second route
 *      on either of the last two pages, this file needs an extra clause:
 *      `emptyPanels` would stay high and REDUCED WEIGHT would be the thing
 *      nothing here can see.
 *   2. **Which action is primary is a [JUDGEMENT]** that goes to the requester
 *      (§9.3). This file makes "exactly one" checkable and decides nothing about
 *      which one it is.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  /*
   * MOVED BY TASK 12.6, WHICH IS THE COMMIT THIS ROW EXISTS TO OBSERVE.
   *
   * Seeded at `panels: 7, emptyPanels: 6, emptyActions: 6, primaryActions: 0` — the tree as
   * task 12.2 found it, and the four numbers §9.3 describes in prose. Task 12.6 emphasised
   * ONE of the six actions and put the three downstream zones behind one disclosure:
   *
   *   primaryActions 0 → 1   the fleet panel's `ds/CommandButton intent="primary"`, and it
   *                          is the only one on the page. Requirement 8.1.
   *   emptyPanels    6 → 3   Requirement 8.2's cap, met exactly. Row 1 is what a fresh
   *                          account reads; signals, orders and equity are behind a
   *                          `ds/Accordion`, which unmounts while closed.
   *   panels         7 → 4   the same three, counted as panels rather than as empty panels.
   *   emptyActions   6 → 3   one per empty panel STILL ON SCREEN. The other three did not
   *                          lose their action — `the three collapsed zones keep their state,
   *                          their copy and their action` below opens the disclosure and
   *                          counts all six back, which is the assertion that tells a
   *                          collapse apart from a deletion (Requirement 19.4).
   */
  'pages/Dashboard.jsx': Object.freeze({
    panels: 4, emptyPanels: 3, emptyActions: 3, primaryActions: 1,
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
  /*
   * MOVED BY TASK 12.7's FIRST HALF, WHICH IS THE COMMIT THIS ROW EXISTS TO OBSERVE.
   *
   * Seeded at `panels: 5, emptyPanels: 4, emptyActions: 4, primaryActions: 0` — the tree as task
   * 12.2 found it. The page's five panels are the tier-1 summary (the one with no empty branch),
   * open positions, allocation, the equity curve and daily P&L; the last four all resolve `empty`
   * on a zero-data account at once, and none of them was marked as the next thing to do. Task
   * 12.7 emphasised ONE of the four actions and put the three history panels behind one
   * disclosure:
   *
   *   primaryActions 0 → 1   the positions panel's `ds/CommandButton intent="primary"`
   *                          to `/app/strategies`, and it is the only one on the page. The three
   *                          panels that declare that same destination are the ones that
   *                          collapse; the one that stays is the one whose absence is a fact of
   *                          its own. Requirement 8.1.
   *   emptyPanels    4 → 1   Requirement 8.2's cap of 3, met with room. Allocation, equity and
   *                          daily P&L are behind a `ds/Accordion`, which unmounts while closed,
   *                          and only while all three are `empty` together.
   *   panels         5 → 2   the same three, counted as panels rather than as empty panels. The
   *                          tier-3 CONTAINER still renders — the disclosure is inside it, so the
   *                          page's declared tier order is untouched (Property 4).
   *   emptyActions   4 → 1   one per empty panel STILL ON SCREEN. The other three did not lose
   *                          their action — `keeps the three collapsed history panels' state,
   *                          copy and action one keypress away` below opens the disclosure and
   *                          counts all four back, which is the assertion that tells a collapse
   *                          apart from a deletion (Requirement 19.4).
   */
  'pages/Portfolio.jsx': Object.freeze({
    panels: 2, emptyPanels: 1, emptyActions: 1, primaryActions: 1,
  }),
  /*
   * MOVED BY TASK 12.7's SECOND HALF, AND ONLY IN ONE OF THE FOUR NUMBERS.
   *
   * Seeded at `panels: 2, emptyPanels: 1, emptyActions: 1, primaryActions: 0` — the tree as task
   * 12.2 found it. This page was ALREADY inside Requirement 8.2's cap: two panels, the ledger
   * summary (which has no empty branch and resolves `ready`) and the trades table, and only the
   * second presents as empty on a zero-data account. So 8.2 asked for nothing here and nothing
   * was collapsed — no `ds/Accordion` was added, `emptyPanels` and `emptyActions` did not move,
   * and there is no opened census below this row because there is no disclosure to open.
   *
   *   primaryActions 0 → 1   the trades panel's `ds/CommandButton intent="primary"` to
   *                          `/app/strategies`, rendered only while that panel is
   *                          `PANEL_STATES.EMPTY`, and it is the only one on the page.
   *                          Requirement 8.1.
   *   panels         2 → 2   unchanged.
   *   emptyPanels    1 → 1   unchanged, and already inside 8.2's cap of 3.
   *   emptyActions   1 → 1   unchanged. The empty state keeps its own `Review your strategies`
   *                          action beside the emphasised one (Requirement 19.4).
   */
  'pages/TradeHistory.jsx': Object.freeze({
    panels: 2, emptyPanels: 1, emptyActions: 1, primaryActions: 1,
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

  it('holds Requirement 8.2\'s cap on all five, and shows 8.1 is what the rest of 12.7 is for', () => {
    /*
     * WHAT THIS CLAUSE ASSERTED BEFORE, AND WHY IT HAD TO CHANGE IN THIS COMMIT.
     *
     * It read `over.length > 0` over the pages with more than three empty panels: non-vacuity
     * for the direction of travel, so that the closing commits could not be describing a
     * finished state. `pages/Portfolio.jsx` at 4 was the last page over the cap, so the moment
     * task 12.7's first half landed that premise stopped being true — and a clause whose premise
     * is false is not a guard that was widened, it is a guard that has been DISCHARGED.
     *
     * So the 8.2 half is replaced by the stronger claim, asserted rather than assumed: all five
     * pages are now inside the cap of 3, and `toBeLessThanOrEqual` on every row is what stops
     * the count creeping back up on a page nobody is editing.
     *
     * The non-vacuity moves to the number that is still moving. Requirement 8.1 wants exactly
     * one primary action per page and `pages/TradeHistory.jsx` and `pages/LiveTrading.jsx` are
     * still at zero, which is the other half of task 12.7 and is why those two rows are
     * untouched here. When that commit lands, THIS clause is the one that fails and the seeds
     * below it are the ones that move.
     */
    const over = Object.entries(FRESH_ACCOUNT)
      .filter(([, seed]) => seed.emptyPanels > 3)
      .map(([file]) => `${file} (${FRESH_ACCOUNT[file].emptyPanels})`);

    expect(
      over,
      'Requirement 8.2 caps simultaneously-visible empty panels at 3. These pages are over it,\n'
        + 'which after tasks 12.6 and 12.7 means a panel came back rather than that one is\n'
        + `pending:\n${over.map((row) => `  ${row}`).join('\n')}`,
    ).toEqual([]);

    const unmarked = Object.entries(FRESH_ACCOUNT)
      .filter(([, seed]) => seed.primaryActions !== 1)
      .map(([file]) => `${file} (${FRESH_ACCOUNT[file].primaryActions})`);

    expect(
      unmarked.length,
      'Every page already marks exactly one next action, so Requirement 8.1 is met everywhere\n'
        + 'and the rest of task 12.7 has nothing to do. Either that commit has landed — in which\n'
        + 'case this clause is the one to discharge, the way the 8.2 half above was — or a seed\n'
        + 'was written from the finished state rather than measured.',
    ).toBeGreaterThan(0);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 3. design.md §9.3's four claims about Dashboard, verified
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('a fresh account: Dashboard is §9.3\'s case', () => {
  const dashboard = () => measured('pages/Dashboard.jsx');

  it('holds Requirement 8.2\'s cap of 3 visible empty panels', () => {
    // §9.3's first three claims were "7 panels, 6 empty, all at once", and task 12.6 is what
    // changed the second and third. What is left of the first is asserted here: three panels
    // stand on a fresh account, all three of them present as empty, and the fourth — the
    // account summary — is the one with no empty branch. The three that are gone from this
    // count are collapsed, not deleted; the last test in this block is where that is shown.
    const census = dashboard();
    expect(census.emptyPanels).toBeLessThanOrEqual(3);
    expect(census.panels).toBe(4);
    expect(census.emptyPanels).toBe(3);
    expect(census.panels - census.emptyPanels).toBe(1);
    // One `ds/EmptyState` per empty panel — no panel is empty without saying so, and none
    // renders two.
    expect(census.emptyStates).toBe(census.emptyPanels);
  });

  it('marks exactly one of them as the next thing to do', () => {
    // §9.3's fourth claim, inverted by task 12.6: six actions offered identically became one
    // marked action beside the rest. This fails at 0 (nothing is the next thing to do) and at
    // 2 (two next actions on a fresh account is the same defect as none — Requirement 8.1).
    const census = dashboard();
    expect(census.primaryActions).toBe(1);
    // And the emphasis did not come out of an action's hide: every empty panel on screen
    // still offers one.
    expect(census.emptyActions).toBe(census.emptyStates);
  });

  it('keeps the three collapsed zones\' state, copy and action one keypress away', async () => {
    /*
     * REQUIREMENT 19.4, AND THE ONE THING THE COUNTS ABOVE CANNOT SEE.
     *
     * `emptyPanels: 3` is what a collapse and a deletion both look like from outside. So this
     * opens the disclosure and counts again: all 7 panels come back, all 6 present as empty,
     * all 6 name an action, and the primary action is still the only one. "Reduced weight is
     * not collapse — the state name, its copy and its action all stay" is that pair of
     * censuses, not a sentence in a docblock.
     *
     * Reached the way a keyboard user reaches it (Requirement 6.4): the toggle is a `button`
     * in a heading, so it has an accessible name and it is in the tab order, and this test
     * finds it by that name rather than by a class or a test id.
     */
    primeReads();
    const { container } = render(<MemoryRouter><Dashboard /></MemoryRouter>);

    await waitFor(
      () => {
        expect(
          container.querySelectorAll('[data-panel-state="loading"],[data-panel-state="idle"]')
            .length,
        ).toBe(0);
      },
      { timeout: 10000 },
    );

    const closed = censusOf(container);
    expect(closed.emptyPanels).toBe(3);

    const toggle = screen.getByRole('button', { name: /signals, orders and equity/i });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(toggle.getAttribute('aria-expanded')).toBe('true');
    });

    const open = censusOf(container);
    expect(
      {
        panels: open.panels,
        emptyPanels: open.emptyPanels,
        emptyActions: open.emptyActions,
        primaryActions: open.primaryActions,
      },
      `opened, Dashboard shows panel states ${open.states.join(', ')} and empty variants `
        + `${open.variants.join(', ') || '(none)'}.\n\n`
        + 'All 7 panels and all 6 empty actions must be behind that one toggle. If the opened\n'
        + 'counts are lower than the seed task 12.2 took, a panel was REMOVED rather than\n'
        + 'collapsed, which Requirement 19.4 forbids however few states it leaves on screen.',
    ).toEqual({ panels: 7, emptyPanels: 6, emptyActions: 6, primaryActions: 1 });

    // And opening it did not turn an absence into a failure: the three that came back say
    // the trader has none of these, not that a read could not be made (Requirement 8.3).
    expect(open.states).not.toContain('unavailable');
    expect(open.states).not.toContain('error');

    cleanup();
    vi.restoreAllMocks();
  }, 30000);
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 3b. Portfolio's collapse, opened — task 12.7's first half
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('a fresh account: Portfolio\'s three history panels are collapsed, not deleted', () => {
  it('keeps the three collapsed history panels\' state, copy and action one keypress away', async () => {
    /*
     * REQUIREMENT 19.4, AND THE ONE THING THE COUNTS ABOVE CANNOT SEE.
     *
     * `emptyPanels: 1` is what a collapse and a deletion both look like from outside, and
     * `emptyActions: 1` is what "three actions were de-emphasised" and "three actions were
     * removed" both look like. So this opens the disclosure and counts again: all 5 panels come
     * back, all 4 present as empty, all 4 name an action, and the primary action is still the
     * only one. "Reduced weight is not collapse — the state name, its copy and its action all
     * stay" is that pair of censuses, not a sentence in a docblock.
     *
     * Reached the way a keyboard user reaches it (Requirement 6.4): `ds/Accordion`'s toggle is a
     * `button` in a heading, so it has an accessible name and it is in the tab order, and this
     * finds it by that name rather than by a class or a test id.
     */
    primeReads();
    const { container } = render(<MemoryRouter><Portfolio /></MemoryRouter>);

    await waitFor(
      () => {
        expect(
          container.querySelectorAll('[data-panel-state="loading"],[data-panel-state="idle"]')
            .length,
        ).toBe(0);
      },
      { timeout: 10000 },
    );

    const closed = censusOf(container);
    expect(closed.emptyPanels).toBe(1);
    expect(closed.primaryActions).toBe(1);

    const toggle = screen.getByRole('button', { name: /allocation, equity and daily p&l/i });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(toggle);
    await waitFor(() => {
      expect(toggle.getAttribute('aria-expanded')).toBe('true');
    });

    const open = censusOf(container);
    expect(
      {
        panels: open.panels,
        emptyPanels: open.emptyPanels,
        emptyActions: open.emptyActions,
        primaryActions: open.primaryActions,
      },
      `opened, Portfolio shows panel states ${open.states.join(', ')} and empty variants `
        + `${open.variants.join(', ') || '(none)'}.\n\n`
        + 'All 5 panels and all 4 empty actions must be behind that one toggle. If the opened\n'
        + 'counts are lower than the seed task 12.2 took, a panel was REMOVED rather than\n'
        + 'collapsed, which Requirement 19.4 forbids however few states it leaves on screen.',
    ).toEqual({ panels: 5, emptyPanels: 4, emptyActions: 4, primaryActions: 1 });

    // And opening it did not turn an absence into a failure. The three that came back say the
    // trader has none of these — not that a read could not be made, and not that the figures are
    // unavailable on this ledger, which is what the paper account's three tier-3 panels report
    // and is a different fact (Requirement 8.3). The collapse condition accepts
    // `PANEL_STATES.EMPTY` and nothing else, so neither state can be hidden by it.
    expect(open.states).not.toContain('unavailable');
    expect(open.states).not.toContain('error');

    cleanup();
    vi.restoreAllMocks();
  }, 30000);
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
