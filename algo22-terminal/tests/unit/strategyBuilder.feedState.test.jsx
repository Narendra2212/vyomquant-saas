/**
 * Feed-state wiring tests for the builder's status strip (task 7.11, Requirements 19.7,
 * 19.8, 19.9, 19.10).
 *
 * What is under test is the contract between the strip and the one endpoint that can answer
 * "is what I am looking at current?" —
 * `GET /api/strategy-operations/strategies/{id}/data-quality` (task 7.4):
 *
 * * the reading is **fetched**, never derived: one authenticated GET per saved strategy,
 *   against that path and no near-miss of it;
 * * Requirement 19.8's display clause is on screen — the age of the last event **together
 *   with** the expected interval — as the server's own `display` sentence **verbatim**, the
 *   convention `ParameterForm` (`fix_hint`) and `NodePreview` (`detail.message`) already
 *   follow, with the figures behind it carried alongside;
 * * nothing is reclassified in the client: the 1.5x / 3x boundaries and the five-state
 *   vocabulary appear nowhere in the projection, so a report cannot be relabelled by a second
 *   copy of the arithmetic that would eventually disagree with `feed_state.py`;
 * * an unsaved canvas is `NOT_APPLICABLE` — no request, and not an error;
 * * and the part that matters most: **fail-closed**. With no reading, a read in flight, an
 *   error, or a body carrying no feed report, the strip reads `UNKNOWN` and never `LIVE`.
 *   `UNKNOWN` stays distinguishable from the five server states, so "nobody has measured
 *   this" cannot be mistaken for "measured, and healthy".
 *
 * The axios instance is the only stub. `registryClient`, `canonicalGraph`, `graphValidation`,
 * `dataQuality`, the real React Flow canvas and the strip itself are the shipped code.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, cleanup } from '@testing-library/react';

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
  patch: (...args) => mockClient.patch(...args),
}));

import { FEED_READ_INTERVAL_MS } from '../../src/pages/StrategyBuilder';
import {
  DATA_QUALITY_LIMIT_PER_MINUTE,
  DATA_QUALITY_PATH,
  dataQualityApi,
} from '../../src/api/modules/dataQuality';
import { FEED_STATES, feedStateFromReport } from '../../src/lib/graphValidation';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { toCanonical } from '../../src/lib/canonicalGraph';
import {
  descriptor,
  installBuilderStubs,
  renderBuilder,
  served,
  waitForRegistry,
} from './helpers/registryFixture';

// ---------------------------------------------------------------------------
// Fixtures: a saved strategy naming one market, and the endpoint's wire shape
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_feed_1';
const SYMBOL = 'ETH/USDT';
const TIMEFRAME = '5m';

/** The five states the server may report, as Requirement 19.6 fixes them. */
const SERVER_STATES = Object.freeze([
  'LIVE',
  'DELAYED',
  'STALE',
  'DISCONNECTED',
  'INSUFFICIENT_DATA',
]);

const OHLCV = descriptor('ohlcv_feed', 'DATA');

const canvasNode = (id, block, params) => ({
  id,
  type: block.block_id,
  position: { x: 0, y: 0 },
  data: {
    block_id: block.block_id,
    category: block.category,
    label: block.display_name,
    params,
    inputs: block.inputs,
    outputs: block.outputs,
    descriptor: block,
  },
});

const GRAPH = toCanonical(
  [canvasNode('n-data', OHLCV, { symbol: SYMBOL, timeframe: TIMEFRAME })],
  [],
  { name: 'Fed Strategy' },
);

const SAVED_STRATEGY = { id: STRATEGY_ID, name: 'Fed Strategy', graph_json: GRAPH };
const UNSAVED_STRATEGY = { id: null, name: 'Fed Strategy', graph_json: GRAPH };

/**
 * One `FeedStateReport.to_dict()`, in `backend_app/backend/feed_state.py`'s wire shape.
 *
 * The default is a **delayed** feed, because Requirement 19.8's display clause is stated
 * about exactly that case: 12m 30s of age against a 5m bar, which the server has already
 * rendered into its `display` sentence.
 */
const feedReport = (extra = {}) => ({
  state: 'DELAYED',
  reason: 'AGE_BEYOND_DELAYED_THRESHOLD',
  display: 'Last candle 12m 30s ago, expected every 5m.',
  age_seconds: 750,
  age_text: '12m 30s',
  last_event_at: '2024-03-01T16:23:30+00:00',
  timeframe: TIMEFRAME,
  expected_interval_seconds: 300,
  measured: true,
  delayed_at_intervals: 1.5,
  stale_at_intervals: 3,
  delayed_after_seconds: 450,
  stale_after_seconds: 900,
  connected: true,
  available_bars: 400,
  warmup_bars: 60,
  bars_missing: null,
  age_state: 'DELAYED',
  observation_source: 'connection_monitor',
  observed_at: '2024-03-01T16:36:00+00:00',
  detail: { connections_matched: 1, bars_countable: true },
  ...extra,
});

const dataQualityBody = ({
  feed = feedReport(),
  market = { symbol: SYMBOL, timeframe: TIMEFRAME },
  ...rest
} = {}) => ({
  strategy_id: STRATEGY_ID,
  version_id: 'ver_feed_1',
  version: 3,
  dag_hash: 'dag_feed',
  compiler_version: '2.1.0',
  market,
  warmup_bars: 60,
  feed,
  data_quality: {
    available: true,
    status: 'PASS',
    window: { bars: 400, covers_warmup: true },
  },
  computed_at: '2024-03-01T16:36:00+00:00',
  ...rest,
});

const VALID_REPORT = {
  valid: true,
  dag_hash: 'dag_feed',
  validation_state: 'VALID',
  errors: [],
  warnings: [],
  summary: { node_count: 1, edge_count: 0, warmup_bars: 60 },
};

/** The rejection `apiClient`'s interceptor produces: an `ApiError`-shaped failure. */
const apiFailure = (status, message) => {
  const error = new Error(message);
  error.status = status;
  error.data = { error: 'DATA_QUALITY_UNAVAILABLE', message };
  return error;
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/** Every data-quality read, with the URL and the axios config it was given. */
let dataQualityCalls = [];

/** What the next data-quality read answers. Replaced per test. */
let answerDataQuality = () => Promise.resolve({ status: 200, data: dataQualityBody() });

const routeRequests = () => {
  mockClient.get.mockImplementation((url, config) => {
    if (typeof url === 'string' && url.includes('/data-quality')) {
      dataQualityCalls.push({ url, config });
      return answerDataQuality(url, config);
    }
    // Everything else on this page is the registry.
    return Promise.resolve(served());
  });
  mockClient.post.mockImplementation((url) => {
    if (typeof url === 'string' && url.includes('/validate')) return Promise.resolve(VALID_REPORT);
    return Promise.resolve({ status: 'ok', id: STRATEGY_ID });
  });
};

const feedCell = () => screen.getByTestId('feed-state');
const feedSentence = () => screen.queryByTestId('feed-display');

/** Wait until the strip has stopped saying "no reading yet" one way or the other. */
const settledFeed = (state) =>
  waitFor(() => expect(feedCell().dataset.state).toBe(state), { timeout: 5000 });

beforeEach(() => {
  installBuilderStubs();
  dataQualityCalls = [];
  answerDataQuality = () => Promise.resolve({ status: 200, data: dataQualityBody() });
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  mockClient.del.mockReset();
  resetRegistryClient();
  routeRequests();
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. The reading is fetched from the endpoint that owns it
// ---------------------------------------------------------------------------

describe('the builder reads feed state from the data-quality endpoint', () => {
  it('issues one authenticated GET for a saved strategy, against the declared path', async () => {
    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');

    await waitFor(() => expect(dataQualityCalls.length).toBeGreaterThan(0));
    expect(dataQualityCalls[0].url).toBe(DATA_QUALITY_PATH(STRATEGY_ID));
    expect(dataQualityCalls[0].url).toContain(STRATEGY_ID);

    // One read per mount, not a burst: the interval is what refreshes it.
    await settledFeed('DELAYED');
    expect(dataQualityCalls).toHaveLength(1);

    // Read-only. Nothing writes to this path, on any verb.
    for (const spy of [mockClient.post, mockClient.put, mockClient.del, mockClient.patch]) {
      for (const call of spy.mock.calls) {
        expect(String(call[0])).not.toContain('/data-quality');
      }
    }
  });

  it('aborts a read the canvas has moved on from, rather than letting it land late', async () => {
    let held;
    answerDataQuality = () => new Promise((resolve) => { held = resolve; });

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await waitFor(() => expect(dataQualityCalls.length).toBe(1));

    const { signal } = dataQualityCalls[0].config;
    expect(signal).toBeTruthy();
    expect(signal.aborted).toBe(false);

    cleanup();
    expect(signal.aborted).toBe(true);
    if (held) held({ status: 200, data: dataQualityBody() });
  });

  it('asks nothing for an unsaved canvas, and calls that not applicable rather than failed', async () => {
    renderBuilder({ initialStrategy: UNSAVED_STRATEGY });
    await waitForRegistry('ready');
    await waitFor(() => expect(screen.getByTestId('feed-state')).toBeTruthy());

    expect(dataQualityCalls).toHaveLength(0);
    expect(feedCell().dataset.state).toBe(FEED_STATES.UNKNOWN);
    expect(feedCell().dataset.known).toBe('false');
    expect(feedCell().getAttribute('title')).toContain('not applicable');
    // Not an error, and not a passing state either.
    expect(feedCell().textContent).not.toContain('Unavailable');
    expect(feedCell().dataset.state).not.toBe('LIVE');
  });

  it('refreshes well inside the endpoint rate limit', () => {
    expect(DATA_QUALITY_LIMIT_PER_MINUTE).toBe(60);
    const requestsPerMinute = 60000 / FEED_READ_INTERVAL_MS;
    expect(requestsPerMinute).toBeLessThanOrEqual(DATA_QUALITY_LIMIT_PER_MINUTE);
    // A comfortable margin, not a value that only just fits: several open builders, or a
    // refresh landing beside a save, must still be nowhere near the limit.
    expect(requestsPerMinute * 4).toBeLessThanOrEqual(DATA_QUALITY_LIMIT_PER_MINUTE);
  });

  it('refuses to build a request without a saved id, instead of sending `undefined`', async () => {
    await expect(dataQualityApi.forStrategy('')).rejects.toThrow(/saved strategy id/);
    await expect(dataQualityApi.forStrategy(null)).rejects.toThrow(/saved strategy id/);
    expect(dataQualityCalls).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Requirement 19.8: the age together with the expected interval, on screen
// ---------------------------------------------------------------------------

describe('the delayed feed shows its age together with the expected interval', () => {
  it("renders the server's display sentence verbatim, carrying both figures", async () => {
    const report = feedReport();
    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed('DELAYED');

    const sentence = await waitFor(() => {
      const found = feedSentence();
      if (found === null) throw new Error('the feed sentence is not rendered yet');
      return found;
    });

    // Verbatim: the classifier that decided the state is the one quoted, character for
    // character, rather than a sentence re-composed here from the numbers.
    expect(sentence.textContent).toBe(report.display);
    // Both halves of the clause are present, as figures rather than a colour.
    expect(sentence.textContent).toContain('12m 30s');
    expect(sentence.textContent).toContain('every 5m');
    // And the numbers behind the sentence travel with it.
    expect(sentence.dataset.ageSeconds).toBe('750');
    expect(sentence.dataset.expectedIntervalSeconds).toBe('300');

    expect(feedCell().textContent).toContain('Delayed');
    expect(feedCell().dataset.known).toBe('true');
  });

  it('states the state in text inside the live region, not by colour alone', async () => {
    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed('DELAYED');

    const strip = screen.getByTestId('status-strip');
    expect(strip.getAttribute('role')).toBe('status');
    expect(strip.getAttribute('aria-live')).toBe('polite');
    // A screen reader receives the word and the sentence, both as text.
    expect(strip.textContent).toContain('Delayed');
    expect(strip.textContent).toContain('Last candle 12m 30s ago, expected every 5m.');
  });

  it('reports a warmup shortfall with the counts the server measured', async () => {
    answerDataQuality = () =>
      Promise.resolve({
        status: 200,
        data: dataQualityBody({
          feed: feedReport({
            state: 'INSUFFICIENT_DATA',
            reason: 'WARMUP_NOT_MET',
            display: 'Last candle 30s ago, expected every 5m. 40 of 60 warmup bars, 20 short.',
            age_seconds: 30,
            age_text: '30s',
            available_bars: 40,
            bars_missing: 20,
          }),
        }),
      });

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed('INSUFFICIENT_DATA');

    expect(feedCell().textContent).toContain('Insufficient data');
    expect(feedSentence().textContent).toBe(
      'Last candle 30s ago, expected every 5m. 40 of 60 warmup bars, 20 short.',
    );
  });
});

// ---------------------------------------------------------------------------
// 3. Nothing is reclassified in the client
// ---------------------------------------------------------------------------

describe('the client classifies nothing', () => {
  it('shows the reported state even where naive client-side arithmetic would disagree', async () => {
    // 30s of age against a 5m bar is inside 1.5 intervals, so a client recomputing the
    // boundaries would call this LIVE. The server called it DELAYED — it can see the
    // connection, which the client cannot — and the server's word is what is shown.
    answerDataQuality = () =>
      Promise.resolve({
        status: 200,
        data: dataQualityBody({
          feed: feedReport({
            state: 'DELAYED',
            display: 'Last candle 30s ago, expected every 5m.',
            age_seconds: 30,
            age_text: '30s',
          }),
        }),
      });

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed('DELAYED');

    expect(feedCell().textContent).toContain('Delayed');
    expect(feedCell().textContent).not.toContain('Live');
    expect(feedSentence().textContent).toBe('Last candle 30s ago, expected every 5m.');
  });

  it('holds no threshold and no state vocabulary in the projection itself', () => {
    // Comments are stripped first, deliberately: the projection's prose *names* the states it
    // refuses to re-derive ("labelling a stale feed `LIVE`"), and prose about a defect is not
    // the defect. What must be absent is executable code.
    const source = feedStateFromReport
      .toString()
      .split('\n')
      .filter((line) => !line.trim().startsWith('//'))
      .join('\n');

    // The 1.5x / 3x boundaries live in `feed_state.py`. A copy here is a second classifier.
    expect(source).not.toMatch(/1\.5/);
    expect(source).not.toMatch(/\b3\b/);
    expect(source).not.toMatch(/delayed_at_intervals|stale_at_intervals/);
    // Nor is any of the five words written into the projection: the label table is a
    // translation of what arrived, and an unrecognised word falls through to UNKNOWN.
    for (const state of SERVER_STATES) {
      expect(source).not.toContain(state);
    }
  });

  it('renders a reported LIVE feed as live, so the fail-closed branches mean something', async () => {
    answerDataQuality = () =>
      Promise.resolve({
        status: 200,
        data: dataQualityBody({
          feed: feedReport({
            state: 'LIVE',
            reason: 'FRESH',
            display: 'Last candle 20s ago, expected every 5m.',
            age_seconds: 20,
            age_text: '20s',
            age_state: 'LIVE',
          }),
        }),
      });

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed('LIVE');

    expect(feedCell().dataset.known).toBe('true');
    expect(feedCell().textContent).toContain('Live');
    expect(feedSentence().textContent).toBe('Last candle 20s ago, expected every 5m.');
  });
});

// ---------------------------------------------------------------------------
// 4. Fail-closed: no reading is never a passing reading
// ---------------------------------------------------------------------------

describe('the strip fails closed', () => {
  it('is UNKNOWN, not LIVE, while the read is in flight', async () => {
    answerDataQuality = () => new Promise(() => {});

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await waitFor(() => expect(dataQualityCalls.length).toBe(1));

    expect(feedCell().dataset.state).toBe(FEED_STATES.UNKNOWN);
    expect(feedCell().dataset.state).not.toBe('LIVE');
    expect(feedCell().dataset.known).toBe('false');
    expect(feedCell().textContent).toContain('Checking');
    expect(feedCell().getAttribute('title')).toContain('unknown, not healthy');
    // No sentence, because no age has been reported: an empty strip beats a guessed one.
    expect(feedSentence()).toBeNull();
  });

  it('is UNKNOWN, not LIVE, when the read fails', async () => {
    answerDataQuality = () => Promise.reject(apiFailure(500, 'Feed state could not be read'));

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed(FEED_STATES.UNKNOWN);

    expect(feedCell().dataset.known).toBe('false');
    expect(feedCell().textContent).toContain('Unavailable');
    const title = feedCell().getAttribute('title');
    expect(title).toContain('Feed state could not be read');
    expect(title).toContain('HTTP 500');
    expect(title).toContain('unknown, not healthy');
    expect(feedSentence()).toBeNull();
  });

  it('is UNKNOWN, not LIVE, when the limiter refuses the read', async () => {
    answerDataQuality = () => Promise.reject(apiFailure(429, 'Too many requests'));

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed(FEED_STATES.UNKNOWN);

    expect(feedCell().dataset.known).toBe('false');
    expect(feedCell().getAttribute('title')).toContain('HTTP 429');
  });

  it('is UNKNOWN, not LIVE, when the body carries no feed report', async () => {
    answerDataQuality = () =>
      Promise.resolve({ status: 200, data: { strategy_id: STRATEGY_ID, data_quality: {} } });

    renderBuilder({ initialStrategy: SAVED_STRATEGY });
    await waitForRegistry('ready');
    await settledFeed(FEED_STATES.UNKNOWN);

    expect(feedCell().dataset.known).toBe('false');
    expect(feedCell().getAttribute('title')).toContain('unknown, not healthy');
    expect(feedSentence()).toBeNull();
  });

  it('keeps UNKNOWN distinguishable from every state the server can report', () => {
    expect(SERVER_STATES).not.toContain(FEED_STATES.UNKNOWN);
    expect(FEED_STATES.UNKNOWN).toBe('UNKNOWN');
    for (const state of SERVER_STATES) {
      expect(FEED_STATES[state]).toBe(state);
    }
  });

  it('quotes a state word it does not know instead of rendering a sixth state', () => {
    const feed = feedStateFromReport(
      { status: 'REPORTED', body: dataQualityBody({ feed: feedReport({ state: 'HEALTHY' }) }) },
      { market: { symbol: SYMBOL, timeframe: TIMEFRAME } },
    );
    expect(feed.state).toBe(FEED_STATES.UNKNOWN);
    expect(feed.known).toBe(false);
    expect(feed.detail).toContain('"HEALTHY"');
    expect(feed.detail).toContain('unknown, not healthy');
  });

  it('will not attribute a reading about one market to another', () => {
    const feed = feedStateFromReport(
      {
        status: 'REPORTED',
        body: dataQualityBody({ market: { symbol: 'BTC/USDT', timeframe: '1h' } }),
      },
      { market: { symbol: SYMBOL, timeframe: TIMEFRAME } },
    );
    expect(feed.state).toBe(FEED_STATES.UNKNOWN);
    expect(feed.known).toBe(false);
    expect(feed.detail).toContain('BTC/USDT 1h');
    expect(feed.detail).toContain('unknown, not healthy');
  });

  it('reports nothing read at all as unknown rather than as healthy', () => {
    const feed = feedStateFromReport(null, { market: { symbol: SYMBOL, timeframe: TIMEFRAME } });
    expect(feed.state).toBe(FEED_STATES.UNKNOWN);
    expect(feed.known).toBe(false);
    expect(feed.detail).toContain('unknown, not healthy');
  });
});
