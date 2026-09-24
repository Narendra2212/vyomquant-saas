/**
 * `standing-prose` — retail-ui-simplification task 12.1.
 * Requirements 7.1, 7.4, 7.5, 18.1. design.md §5.5, §9.2, Property 8.
 *
 * ===========================================================================
 * WHAT THIS GUARD IS FOR
 * ===========================================================================
 * Requirement 7.4 asks for a seeded per-page count of STANDING PROSE — the
 * user-facing sentences a page renders unconditionally, the ones a trader gets
 * whether or not they asked for them — and requires it to only decrease. This
 * file is that count; `standing-prose.budget.js` is where it is recorded.
 *
 * Written BEFORE the prose moves (design.md §5.1, Decision D2). A budget first
 * written against the tree it is meant to protect has never been observed doing
 * anything: it passes on the commit that introduces it because it was copied
 * from the state it is describing. Seeded here, on the unmoved tree, tasks
 * 12.3-12.5 each have a number to move and each has to move it in the same
 * commit as the text (Requirement 22.2).
 *
 * ===========================================================================
 * WHY THIS IS NOT A `source-scan.js` GUARD, UNLIKE THE OTHER FIVE
 * ===========================================================================
 * Requirement 7.5: "THE reduction SHALL NOT be achieved by moving a sentence
 * from a `const` into inline JSX. IF the count falls without the rendered output
 * changing, THEN the clause is not satisfied, and the test SHALL count rendered
 * text rather than source constants for this reason."
 *
 * A source scan cannot distinguish a sentence that renders from one sitting
 * behind a closed `ds/Accordion`, which is exactly the remedy Requirement 7.2
 * prescribes — so a source-scanning version of this guard would fail the
 * intended fix and pass the fake one. It has both directions wrong. So this file
 * RENDERS each of Requirement 7.4's eight pages and measures the DOM.
 *
 * ===========================================================================
 * THE MECHANICAL DEFINITION OF "STANDING", FROM design.md §5.5
 * ===========================================================================
 * "Above the first data element" needs a mechanical definition or two
 * implementers count different things. §5.5's, adopted verbatim:
 *
 *   > the first element carrying a `data-region` attribute, which
 *   > `pageFields`-declared slots already have on every migrated page
 *   > (`liveTrading.test.jsx:245` walks them, and `dashboard-tier2.test.jsx`
 *   > reads them through `tierSelector`). Text in document order before that
 *   > node is standing prose; text after it is not.
 *
 * That is what makes Requirement 7.1's 400 characters a count rather than an
 * opinion. Two consequences are worth stating because they are what keep the
 * number honest:
 *
 *   1. **A page with NO `data-region` node counts its whole rendered text.**
 *      Vacuously, every node precedes a node that is not there. Four of the eight
 *      pages are in that position today — `SignalTrace`, `TradeHistory`,
 *      `Strategies`, `StrategyMarketplace`, the pages tasks 5-7 migrate — so
 *      their seeded numbers are large and will fall sharply when their slots gain
 *      `data-region`. The alternative reading, "no anchor means nothing is
 *      standing", was rejected: under it a page could zero its budget by DELETING
 *      `data-region` attributes, which is a fake fix this guard would report as
 *      progress. `a page cannot lower its count by losing the anchor` below pins
 *      the chosen direction.
 *   2. **A `pageFields` `reason` is never counted.** A reason renders inside the
 *      panel or metric it is about, which is inside a region, which is after the
 *      anchor. So the budget is scoped away from every load-bearing string BY
 *      CONSTRUCTION rather than by a reviewer remembering to check.
 *      `counts no declared pageFields reason` asserts it rather than claiming it.
 *
 * ===========================================================================
 * WHAT IS COUNTED, PRECISELY
 * ===========================================================================
 * Every text node before the anchor, in document order, excluding `<script>`,
 * `<style>` and `<noscript>`, joined with a newline, then whitespace-collapsed
 * and trimmed. The length of that string is the count.
 *
 * Collapsing is not cosmetic: JSX indentation reaches the DOM, and without it the
 * number would be a measure of how the file is formatted. The join means two
 * adjacent text nodes cost one separator character between them, which is
 * deliberate — they are two runs of text on screen, and merging them would
 * under-count a page that composes a sentence from several expressions.
 *
 * Headings, eyebrows and control labels before the anchor ARE counted. The §5.5
 * definition is positional and that is its whole value; a definition that
 * excluded "chrome" would need a judgement about what chrome is, which is the
 * thing this guard exists to avoid.
 *
 * ===========================================================================
 * THE FIXTURE IS THE ZERO-DATA ACCOUNT
 * ===========================================================================
 * Each page is rendered against a fresh account: every read answers 2xx with no
 * rows. On the four pages with no anchor the count would otherwise be a function
 * of how many rows the fixture carried, and the ratchet would move whenever a
 * fixture was edited. It is also the posture `freshAccount.test.jsx` takes, so
 * the two files describe one screen rather than two.
 *
 * Nothing this guard measures is mocked. Real: every `components/ds/*` primitive,
 * `hooks/usePanelState`, `design/pageFields`, `design/errorCopy`,
 * `design/pageHierarchy`, and all eight pages. Doubles: the HTTP transport (so no
 * request leaves the process and the fixture chooses what the server answered),
 * the WebSocket client, the lazily-imported `ds/Chart` — recharts needs layout
 * APIs jsdom does not implement — and `pages/StrategyBuilder`, an alternate view
 * `pages/Strategies` can swap itself for and which a fresh account never reaches.
 *
 * ===========================================================================
 * THE BLIND SPOTS, QUOTED HERE SO THE GUARD IS NOT TRUSTED PAST THEM
 * ===========================================================================
 * The four sibling budgets each record theirs (`absolute-font-sizes` quotes §3.6's
 * four). These are this one's, and the first is consequential enough that it
 * needs the requester's attention rather than only a note.
 *
 *   1. **On a migrated page, panel-level prose is AFTER the anchor and is
 *      therefore not counted.** `LiveTrading.jsx:2710` and `:2718` — the 384-
 *      character `REGISTRY_CAVEAT` and the 242-character `SELECTION_CAVEAT`, the
 *      626 characters task 12.3 exists to move — render INSIDE the `ds/Panel`
 *      that carries `data-region={DEPLOYMENT_FIELD}` at `:2701`, which is the
 *      first anchor on the page in document order. So under §5.5's definition,
 *      applied as written, they are not standing prose and LiveTrading measures
 *      135 characters rather than 3175. The same holds for Dashboard (168),
 *      Portfolio (91) and Backtester (101): on those four pages the anchor is
 *      high in the tree and what this guard measures is the `ds/PageHeader`
 *      region above it.
 *
 *      That is the definition doing exactly what it was specified to do, and it
 *      is recorded rather than worked around: §5.5 is an approved document and
 *      widening the definition here would be this file deciding a spec question.
 *      But it means task 12.3's stated regression assertion — "LiveTrading's
 *      rendered count falls and the budget is lowered in the same commit" —
 *      cannot hold as written, because moving those two paragraphs behind a
 *      disclosure moves text that is already outside the count. Tasks 12.4 and
 *      12.5 are in the same position for SignalTrace and Dashboard's panel prose.
 *      Raised as a finding; nothing here presumes the answer.
 *   2. **`title` and `aria-label` are not text nodes**, so a sentence carried as a
 *      tooltip attribute is invisible to this count. That is right for a
 *      `pageFields` reason (which is what `ds/Metric` puts in a `title`) and
 *      wrong for anything else, and there is no mechanical way to tell them apart.
 *   3. **One fixture, one viewport-free render.** jsdom has no geometry, so this
 *      cannot see prose that wraps to four lines on a narrow screen, and the
 *      count is of the fresh-account state only — a page that grows a paragraph
 *      only once the trader has rows is not measured here.
 *   4. **`StrategyMarketplace` has no `PAGES` entry**, so the
 *      `counts no declared pageFields reason` safeguard cannot run for it. That
 *      is design.md §5.6's finding about the page, and task 6.1 is what changes it.
 *
 * ===========================================================================
 * WHY THERE IS NO JSX IN THIS FILE
 * ===========================================================================
 * `React.createElement`, not JSX, because task 12.1 names this file
 * `standing-prose.test.js` and every other `.js` under `tests/` is JSX-free. The
 * JSX pipeline is configured per extension; a `.js` file that needs a transform
 * its siblings do not is a file that can stop being collected for a reason
 * nothing in it explains.
 */

import { existsSync } from 'node:fs';
import path from 'node:path';

import { cleanup, render, waitFor } from '@testing-library/react';
import { createElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import {
  SOURCE_CONSTANT_SEED,
  STANDING_PROSE_BUDGET,
  STANDING_PROSE_TARGET,
} from './standing-prose.budget.js';
import { SRC, list } from './source-scan.js';

const BUDGET_FILE = 'tests/unit/guards/standing-prose.budget.js';

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE DOUBLES
 * ══════════════════════════════════════════════════════════════════════════════════════ */

/**
 * The HTTP transport. `vi.hoisted` because the `vi.mock` factory below is hoisted above
 * these imports and closes over this object.
 *
 * Every `src/api/modules/*` function is REAL and routes through this, so a read this file
 * forgot to prime still cannot reach the network: it resolves `{}`, which
 * `usePanelState.isEmptyPayload` reads as an empty collection. An overlooked read
 * therefore renders an empty panel rather than an error state, and an error state is the
 * one outcome that would make a standing-prose count meaningless — a failed read replaces
 * the body, so every region disappears and the whole page becomes "standing".
 */
const transport = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  publicGet: vi.fn(),
  /**
   * The DEFAULT export — the axios instance — is a second transport and has to be stubbed
   * separately, because the named helpers above unwrap the body and it does not.
   *
   * `lib/registryClient.js`, `api/modules/assets.js` and `api/modules/dataQuality.js` all
   * `import client from '../apiClient'` and read `response.headers` / `response.data`
   * themselves. Left real, `registryClient` issues
   * `GET /api/strategy-operations/registry/timeframes` through jsdom's XHR the moment
   * `Backtester`'s `TimeframeSelector` mounts, and the run depends on a socket that is not
   * there. So this answers an axios-shaped 200 with nothing in it.
   */
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

vi.mock('../../../src/components/ds/Chart', async () => {
  const { createElement: h } = await import('react');
  const Stub = (props) =>
    h('figure', { 'data-testid': 'chart', 'data-chart-kind': props.kind });
  return { __esModule: true, Chart: Stub, default: Stub };
});

vi.mock('../../../src/pages/StrategyBuilder', async () => {
  const { createElement: h } = await import('react');
  return { __esModule: true, default: () => h('div', { 'data-testid': 'builder-stub' }) };
});

import { dashboardApi } from '../../../src/api/modules/dashboard';
import { dataQualityApi } from '../../../src/api/modules/dataQuality';
import { exchangeApi } from '../../../src/api/modules/exchange';
import { libraryApi } from '../../../src/api/modules/library';
import { ordersApi } from '../../../src/api/modules/orders';
import { paperApi } from '../../../src/api/modules/paper';
import { portfolioApi } from '../../../src/api/modules/portfolio';
import { strategiesApi } from '../../../src/api/modules/strategies';
import { PAGE_FIELDS_BY_PAGE, PAGES } from '../../../src/design/pageFields';
import Backtester from '../../../src/pages/Backtester';
import Dashboard from '../../../src/pages/Dashboard';
import LiveTrading from '../../../src/pages/LiveTrading';
import Portfolio from '../../../src/pages/Portfolio';
import SignalTrace from '../../../src/pages/SignalTrace';
import Strategies from '../../../src/pages/Strategies';
import StrategyMarketplace from '../../../src/pages/StrategyMarketplace';
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
 * Prime every read the eight pages issue on mount, each with the shape its own endpoint
 * publishes and no rows in it.
 *
 * Spied on the module objects rather than by mocking `src/api`: `api`, `endpoints` and the
 * default export are all built from these same objects (`src/api/index.js`), so one spy
 * covers every import form the eight pages use between them.
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
  vi.spyOn(strategiesApi, 'listBacktests').mockResolvedValue({ backtests: [], total: 0 });

  vi.spyOn(ordersApi, 'getOpenOrders').mockResolvedValue([]);
  vi.spyOn(ordersApi, 'getHistory').mockResolvedValue([]);

  // BARE ARRAYS, not envelopes. `Portfolio.jsx:991`, `:1008` and `:1026` each check
  // `Array.isArray(res.value)` and report an error state otherwise — the allocation route
  // "answers a BARE ARRAY of `{asset, value_usd, pct}`" and the other two are the same
  // shape. An envelope here puts three chart panels into `error`, which is not what a fresh
  // account looks like.
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
  for (const method of ['featured', 'featuredPublic', 'trending', 'trendingPublic',
    'categories', 'categoriesPublic']) {
    vi.spyOn(libraryApi, method).mockResolvedValue([]);
  }
  for (const method of ['browse', 'browsePublic']) {
    vi.spyOn(libraryApi, method).mockResolvedValue({ items: [], total: 0 });
  }

  vi.spyOn(dataQualityApi, 'forBacktestWindow').mockResolvedValue({});
};

/**
 * Requirement 7.4's eight pages, each with the component that mounts it.
 *
 * `page` is the `design/pageFields.js` key where there is one — `StrategyMarketplace` has
 * no `PAGES` entry, which is design.md §5.6's finding about that page rather than an
 * omission here.
 */
const SUITES = Object.freeze([
  { file: 'pages/LiveTrading.jsx', page: PAGES.LIVE_TRADING, component: LiveTrading },
  { file: 'pages/SignalTrace.jsx', page: PAGES.SIGNAL_TRACE, component: SignalTrace },
  { file: 'pages/Dashboard.jsx', page: PAGES.DASHBOARD, component: Dashboard },
  { file: 'pages/Portfolio.jsx', page: PAGES.PORTFOLIO, component: Portfolio },
  { file: 'pages/TradeHistory.jsx', page: PAGES.TRADE_HISTORY, component: TradeHistory },
  { file: 'pages/Strategies.jsx', page: PAGES.STRATEGIES, component: Strategies },
  { file: 'pages/StrategyMarketplace.jsx', page: null, component: StrategyMarketplace },
  { file: 'pages/Backtester.jsx', page: PAGES.BACKTESTER, component: Backtester },
]);

/* ══════════════════════════════════════════════════════════════════════════════════════
 * THE COUNTING METHOD
 * ══════════════════════════════════════════════════════════════════════════════════════ */

const SKIPPED_TAGS = Object.freeze(['SCRIPT', 'STYLE', 'NOSCRIPT']);

/** Whitespace-collapsed, trimmed. See the header for why collapsing is load-bearing. */
const normalise = (text) => text.replace(/\s+/g, ' ').trim();

/**
 * The standing prose of a rendered subtree, as one normalised string.
 *
 * design.md §5.5's definition: every text node in document order before the first element
 * carrying `data-region`. No anchor means every node precedes it, so the whole subtree
 * counts — see the header for why that direction and not the other one.
 */
export function standingProse(root) {
  const anchor = root.querySelector('[data-region]');
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const parts = [];

  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    // `DOCUMENT_POSITION_FOLLOWING` is set for a node after the anchor AND for one
    // contained by it, so this one comparison closes both ends of the region.
    if (
      anchor !== null
      && (anchor.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0
    ) {
      break;
    }
    if (SKIPPED_TAGS.includes(node.parentNode?.nodeName)) continue;
    parts.push(node.nodeValue ?? '');
  }

  return normalise(parts.join('\n'));
}

/** `{ file, page, characters, text, anchored }` for one page, rendered and settled. */
const measure = async (suite) => {
  primeReads();

  const { container } = render(createElement(MemoryRouter, null, createElement(suite.component)));

  // Settled, not merely mounted: a panel still `loading` or `idle` has not decided what it
  // renders, and a skeleton's label is not the page's prose.
  await waitFor(
    () => {
      expect(
        container.querySelectorAll('[data-panel-state]').length,
        `${suite.file} rendered no ds/Panel, so "settled" cannot be decided`,
      ).toBeGreaterThan(0);
      expect(
        container.querySelectorAll('[data-panel-state="loading"],[data-panel-state="idle"]')
          .length,
        `${suite.file} still has a panel that has not decided what it renders`,
      ).toBe(0);
    },
    { timeout: 10000 },
  );

  const text = standingProse(container);
  const anchored = container.querySelector('[data-region]') !== null;

  cleanup();
  vi.restoreAllMocks();

  return { file: suite.file, page: suite.page, characters: text.length, text, anchored };
};

/** Filled by `beforeAll`, keyed by the budget's file path. */
const MEASURED = new Map();

beforeAll(async () => {
  // Sequential on purpose. Eight pages mounted concurrently would share one jsdom document
  // and one set of module spies, so `primeReads` for one would be visible to another.
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
 * 1. The counting method itself
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('standing-prose: the counting method', () => {
  /** A detached subtree, so these run without React and without a page. */
  const dom = (html) => {
    const host = document.createElement('div');
    host.innerHTML = html;
    return host;
  };

  it('counts the text before the first data-region and nothing after it', () => {
    const host = dom(
      '<p>Standing sentence.</p><div data-region="tier-1"><span>45,250.00</span></div>',
    );
    expect(standingProse(host)).toBe('Standing sentence.');
  });

  it('stops at the first region even when a later one carries more text', () => {
    const host = dom(
      '<h1>Live Trading</h1>'
      + '<div data-region="a"><p>counted nowhere</p></div>'
      + '<p>after the anchor</p>'
      + '<div data-region="b"><p>also nowhere</p></div>',
    );
    expect(standingProse(host)).toBe('Live Trading');
  });

  it('counts text inside a wrapper that precedes the region, at any depth', () => {
    const host = dom(
      '<header><div><span>One</span><em>Two</em></div></header>'
      + '<section data-region="x">ignored</section>',
    );
    // Two text nodes, one separator: they are two runs of text on screen.
    expect(standingProse(host)).toBe('One Two');
  });

  it('counts the WHOLE subtree when no element carries data-region', () => {
    // The direction that cannot be gamed: a page may not zero its budget by deleting the
    // anchor. Four of the eight pages are in this state today.
    const host = dom('<h1>Trade history</h1><table><tr><td>BTC/USDT</td></tr></table>');
    expect(standingProse(host)).toBe('Trade history BTC/USDT');
  });

  it('collapses the whitespace JSX indentation leaves in the DOM', () => {
    const host = dom('<p>\n   A   sentence\n   with   gaps.\n</p><b data-region="r">x</b>');
    expect(standingProse(host)).toBe('A sentence with gaps.');
  });

  it('counts nothing when the region is the first node', () => {
    expect(standingProse(dom('<div data-region="tier-1"><p>figures</p></div>'))).toBe('');
  });

  it('ignores script and style content', () => {
    const host = dom('<style>.a{color:red}</style><p>Prose.</p><b data-region="r">x</b>');
    expect(standingProse(host)).toBe('Prose.');
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 2. Non-vacuity — the measurement actually ran
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('standing-prose: the measurement is live', () => {
  it('rendered all eight pages and measured each one', () => {
    // Without this, an exception inside `beforeAll` — or a harness that silently rendered
    // nothing — would leave every budget comparison below passing over an empty map.
    expect(MEASURED.size).toBe(SUITES.length);
    expect([...MEASURED.keys()].sort()).toEqual(Object.keys(STANDING_PROSE_BUDGET).sort());

    for (const suite of SUITES) {
      const row = measured(suite.file);
      expect(Number.isInteger(row.characters), `${suite.file} measured no integer`).toBe(true);
      expect(row.characters, `${suite.file} rendered no text at all`).toBeGreaterThan(0);
    }
  });

  it('found the data-region anchor on the pages that have one, and names the four that do not', () => {
    // The anchor is what makes the count a count. If `data-region` ever stopped reaching
    // the DOM, every page would fall into the whole-subtree branch at once and every number
    // would jump — this records which side of the definition each page is on, so that jump
    // is reported as a change of kind rather than absorbed as a change of size.
    const anchored = SUITES.filter((s) => measured(s.file).anchored).map((s) => s.file);
    const unanchored = SUITES.filter((s) => !measured(s.file).anchored).map((s) => s.file);

    expect(anchored.sort()).toEqual([
      'pages/Backtester.jsx',
      'pages/Dashboard.jsx',
      'pages/LiveTrading.jsx',
      'pages/Portfolio.jsx',
    ]);
    // These four are outside the accessibility ratchet's original scope too — cause 2 in
    // requirements §1.4. Tasks 5-7 migrate them, and each one that gains a `data-region`
    // lowers its entry here in the same commit.
    expect(unanchored.sort()).toEqual([
      'pages/SignalTrace.jsx',
      'pages/Strategies.jsx',
      'pages/StrategyMarketplace.jsx',
      'pages/TradeHistory.jsx',
    ]);
  });

  it('a page cannot lower its count by losing the anchor', () => {
    // The chosen direction, asserted rather than only argued in the header: take the region
    // away and the count goes UP, never to zero.
    const host = document.createElement('div');
    host.innerHTML = '<p>Standing.</p><div data-region="tier-1"><span>Figures</span></div>';

    const withAnchor = standingProse(host);
    host.querySelector('[data-region]').removeAttribute('data-region');
    const withoutAnchor = standingProse(host);

    expect(withAnchor).toBe('Standing.');
    expect(withoutAnchor.length).toBeGreaterThan(withAnchor.length);
    // And at least one real page is on each side, so neither branch is dead code.
    expect(SUITES.some((s) => measured(s.file).anchored)).toBe(true);
    expect(SUITES.some((s) => !measured(s.file).anchored)).toBe(true);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 3. The budget is a ratchet, asserted in both directions
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('standing-prose: the decreasing budget', () => {
  it('is well formed', () => {
    for (const [file, budget] of Object.entries(STANDING_PROSE_BUDGET)) {
      expect(Number.isInteger(budget), `${file}: ${budget} is not an integer`).toBe(true);
      expect(budget, `${file}: ${budget} is negative`).toBeGreaterThanOrEqual(0);
      expect(file, `${file} must be relative to src/ with forward slashes`).toMatch(
        /^[\w.-]+(?:\/[\w.-]+)*$/,
      );
    }
  });

  it("names Requirement 7.4's eight pages, and only those", () => {
    // The key set is an ENUMERATION from an approved requirement, not whatever a scan
    // found — which is why an entry that reaches zero stays at zero rather than being
    // deleted. The budget file's header records that decision and why.
    expect(Object.keys(STANDING_PROSE_BUDGET).sort())
      .toEqual(Object.keys(SOURCE_CONSTANT_SEED).sort());
    expect(Object.keys(STANDING_PROSE_BUDGET)).toHaveLength(8);
    expect(SUITES.map((s) => s.file).sort()).toEqual(Object.keys(STANDING_PROSE_BUDGET).sort());
  });

  it('names only files that still exist', () => {
    const gone = Object.keys(STANDING_PROSE_BUDGET).filter(
      (file) => !existsSync(path.join(SRC, file)),
    );
    expect(
      gone,
      'These budget entries name pages that are no longer in src/. Requirement 7.4\n'
        + 'enumerates eight pages, so a deletion here is a change to the requirement:\n'
        + `${list(gone)}`,
    ).toEqual([]);
  });

  it('holds every page at or below its budget', () => {
    const over = Object.entries(STANDING_PROSE_BUDGET)
      .filter(([file, budget]) => measured(file).characters > budget)
      .map(([file, budget]) =>
        `${file} — budget ${budget}, rendered ${measured(file).characters} `
        + `(+${measured(file).characters - budget})`);

    expect(
      over,
      'Standing prose was ADDED to pages that are supposed to be shrinking.\n\n'
        + 'Requirement 7.2 is the remedy: move the surplus behind a disclosure — a\n'
        + '`ds/Tooltip`, a `ds/Accordion`, or a `ds/Alert` shown only in the state it\n'
        + 'describes — and do NOT delete it or shorten it in a way that changes what it\n'
        + `asserts. Raising a number in ${BUDGET_FILE} reverses Requirement 7.4's\n`
        + `only-decrease rule.\n${list(over)}`,
    ).toEqual([]);
  });

  it('requires progress to be recorded, not banked', () => {
    const under = Object.entries(STANDING_PROSE_BUDGET)
      .filter(([file, budget]) => measured(file).characters < budget)
      .map(([file, budget]) =>
        `${file} — lower the committed budget from ${budget} to `
        + `${measured(file).characters} (-${budget - measured(file).characters})`);

    expect(
      under,
      'These pages now render less standing prose than their committed budget. Good — but\n'
        + 'the budget has to come down with them, IN THIS COMMIT, or the headroom stays open\n'
        + `for the prose to creep back unnoticed (Requirement 22.2). Edit ${BUDGET_FILE}:\n`
        + `${list(under)}`,
    ).toEqual([]);
  });

  it("reports each page's distance from Requirement 7.1's 400 characters", () => {
    // Not a ceiling — Requirement 7.1's threshold is "chosen, not derived", and the pages
    // outside it stay outside it until tasks 12.3-12.5 land. This asserts only that the
    // target is the number the requirement names and that some page is still outside it,
    // so the three closing commits have something to move.
    expect(STANDING_PROSE_TARGET).toBe(400);

    const outside = Object.keys(STANDING_PROSE_BUDGET).filter(
      (file) => STANDING_PROSE_BUDGET[file] > STANDING_PROSE_TARGET,
    );
    expect(outside.length, `pages over ${STANDING_PROSE_TARGET}: ${outside.join(', ')}`)
      .toBeGreaterThan(0);
  });
});

/* ══════════════════════════════════════════════════════════════════════════════════════
 * 4. The scope safeguard — what a falling number may never be bought with
 * ══════════════════════════════════════════════════════════════════════════════════════ */

describe('standing-prose: the scope is the safeguard', () => {
  it('counts no declared pageFields reason', () => {
    // Requirement 19.3's strings are outside this budget BY CONSTRUCTION — a `reason`
    // renders inside the panel or metric it is about, which is inside a region, which is
    // after the anchor. Asserted rather than claimed, because this guard is the instrument
    // that will be used to justify moving text and a reason must never be movable by it.
    const leaked = [];

    for (const suite of SUITES) {
      if (suite.page === null) continue;
      const { text } = measured(suite.file);
      for (const field of PAGE_FIELDS_BY_PAGE[suite.page] ?? []) {
        const reason = normalise(field.reason ?? '');
        if (reason.length > 0 && text.includes(reason)) {
          leaked.push(`${suite.file} — ${field.field}: ${reason.slice(0, 60)}…`);
        }
      }
    }

    expect(
      leaked,
      'A `pageFields` `reason` is being counted as standing prose. It must not be: this\n'
        + 'budget would then be an argument for shortening it, and Requirement 19.3 forbids\n'
        + 'that absolutely. The reason has to render inside its own region.\n'
        + `${list(leaked)}`,
    ).toEqual([]);
  });

  it('counts no page-level failure copy, because a fixture that failed measures nothing', () => {
    // A failed read replaces the body, so every region disappears and the whole page
    // becomes "standing". The numbers in the budget would then measure the error state
    // rather than the page. No page in this run is in that state.
    const failed = SUITES.filter((suite) =>
      /Try again|could not be read|went wrong/i.test(measured(suite.file).text))
      .map((suite) => `${suite.file} — ${measured(suite.file).text.slice(0, 80)}…`);

    expect(
      failed,
      'These pages rendered a failure above their first region, so their measurement is of\n'
        + 'an error state and not of the page. Prime the read in `primeReads`:\n'
        + `${list(failed)}`,
    ).toEqual([]);
  });
});
