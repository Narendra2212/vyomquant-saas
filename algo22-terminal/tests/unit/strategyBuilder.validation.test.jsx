/**
 * Validation-wiring tests for `src/pages/StrategyBuilder.jsx` (task 3.10,
 * Requirements 8.8, 8.9, 8.10, 8.11).
 *
 * What is under test is the contract between the canvas and the authoritative validator:
 *
 * * an edit marks the graph unvalidated at once, and exactly one
 *   `POST /api/strategies/validate` is issued 400 ms after the *last* edit of a burst
 *   (Requirement 8.10);
 * * the endpoint always answers **HTTP 200**, so the verdict is read from the body: a 200
 *   carrying `valid: false` is invalid, and a 200 carrying no boolean `valid` is not a verdict
 *   at all;
 * * every node and every connection the report names is marked with that target's severity and
 *   issue count, and an issue naming neither is still rendered (Requirements 8.8, 8.9);
 * * `fix_hint` is rendered verbatim — asserted character for character, including punctuation a
 *   client-side rewrite would flatten;
 * * a response that arrives after a further edit is discarded rather than allowed to mark a
 *   graph nobody is looking at;
 * * a transport failure fails closed to "unavailable" and never to a locally invented verdict;
 * * the status strip carries all four states, each saying what it knows (Requirement 8.11).
 *
 * The axios instance is the only stub. `registryClient`, `canonicalGraph`, `graphValidation`,
 * the reducer, the debounce and the real React Flow canvas are all the shipped code.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ReactFlowProvider } from 'reactflow';

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

import { DRAG_BLOCK_ID_MIME, StrategyBuilderCanvas } from '../../src/pages/StrategyBuilder';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { UndoRedoProvider } from '../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../src/contexts/ValidationContext';
import { toCanonical } from '../../src/lib/canonicalGraph';
import { VALIDATION_DEBOUNCE_MS } from '../../src/lib/graphValidation';

// ---------------------------------------------------------------------------
// Registry fixture
// ---------------------------------------------------------------------------

const CATEGORIES = [
  { id: 'DATA', display_name: 'Market Data', order: 1 },
  { id: 'INDICATOR', display_name: 'Indicators', order: 2 },
  { id: 'MATH', display_name: 'Math', order: 3 },
  { id: 'LOGIC', display_name: 'Logic', order: 4 },
  { id: 'FEATURE_ENGINEERING', display_name: 'Feature Engineering', order: 5 },
  { id: 'ML_DL', display_name: 'ML / DL Models', order: 6 },
  { id: 'ACTION', display_name: 'Actions', order: 7 },
];

const param = (key, type, extra = {}) => ({
  key,
  label: key,
  type,
  required: false,
  default: null,
  min: null,
  max: null,
  step: null,
  options: null,
  options_source: null,
  unit: null,
  example: null,
  help: '',
  depends_on: [],
  affects_warmup: false,
  ...extra,
});

const OHLCV_FEED = {
  block_id: 'ohlcv_feed',
  display_name: 'OHLCV Feed',
  category: 'DATA',
  description: 'Validated candles for one market and timeframe.',
  inputs: [],
  outputs: [{ port: 'candles', type: 'OHLCV_FRAME' }],
  params: [
    param('symbol', 'SYMBOL', { required: true, example: 'BTC/USDT' }),
    param('timeframe', 'TIMEFRAME', { required: true, example: '15m' }),
  ],
  allowed_predecessor_categories: [],
  allowed_successor_categories: ['INDICATOR', 'ACTION'],
  execution_semantics: 'STATEFUL',
  runtime_ref: 'module:ohlcv_feed',
};

const SMA = {
  block_id: 'sma',
  display_name: 'SMA',
  category: 'INDICATOR',
  description: 'Simple moving average.',
  inputs: [{ port: 'series', type: 'OHLCV_FRAME', required: true }],
  outputs: [{ port: 'value', type: 'SCALAR_SERIES' }],
  params: [param('window', 'INT', { default: 14 })],
  allowed_predecessor_categories: ['DATA'],
  allowed_successor_categories: ['LOGIC', 'ACTION'],
  execution_semantics: 'STATEFUL',
  runtime_ref: 'module:sma',
};

const MARKET_BUY = {
  block_id: 'market_buy',
  display_name: 'Market Buy',
  category: 'ACTION',
  description: 'Submits a market buy order.',
  inputs: [{ port: 'signal', type: 'OHLCV_FRAME', required: true }],
  outputs: [],
  params: [param('quantity', 'NUMBER', { required: true })],
  allowed_predecessor_categories: ['DATA', 'LOGIC'],
  allowed_successor_categories: [],
  execution_semantics: 'TERMINAL',
  runtime_ref: 'module:market_buy',
};

const registryPayload = {
  registry_version: 'r_val_0001',
  registry_schema_version: 1,
  port_types: ['OHLCV_FRAME', 'PRICE_SERIES', 'SCALAR_SERIES', 'BOOLEAN_SERIES', 'FEATURE_MATRIX', 'PREDICTION', 'SIGNAL', 'TRADE_INTENT', 'SCALAR'],
  categories: CATEGORIES,
  blocks: [OHLCV_FEED, SMA, MARKET_BUY],
  compatibility_matrix: { OHLCV_FRAME: ['OHLCV_FRAME'], SCALAR_SERIES: ['SCALAR_SERIES'] },
};

const served = () => ({ status: 200, data: registryPayload, headers: { etag: '"r_val_0001"' } });

// ---------------------------------------------------------------------------
// Graph fixture and the report the backend answers with
// ---------------------------------------------------------------------------

const canvasNode = (id, descriptor, params) => ({
  id,
  type: descriptor.block_id,
  position: { x: 0, y: 0 },
  data: {
    block_id: descriptor.block_id,
    category: descriptor.category,
    label: descriptor.display_name,
    params,
    inputs: descriptor.inputs,
    outputs: descriptor.outputs,
    descriptor,
  },
});

/** DATA → ACTION, one connection with a known id, so the report can name both. */
const GRAPH = toCanonical(
  [
    canvasNode('n-data', OHLCV_FEED, { symbol: 'ETH/USDT', timeframe: '15m' }),
    canvasNode('n-action', MARKET_BUY, { quantity: 5 }),
  ],
  [{ id: 'e-1', source: 'n-data', sourceHandle: 'candles', target: 'n-action', targetHandle: 'signal' }],
  { name: 'Marked Strategy' },
);

/**
 * `fix_hint` strings with punctuation a paraphrase would not survive: an em dash, a straight
 * apostrophe, a colon. Asserted verbatim (Requirement 8.9).
 */
const NODE_HINT = 'Set period to 14 — the standard RSI window — then re-check.';
const EDGE_HINT = "Insert a Logic block between 'n-data' and 'n-action'.";
const GRAPH_HINT = 'Add an ACTION block: a strategy that places no order cannot be deployed.';
const WARMUP_HINT = 'Widen the backtest range to at least 226 bars.';

const issue = (extra) => ({
  code: 'CODE',
  severity: 'error',
  node_id: null,
  edge_id: null,
  field: null,
  message: 'message',
  expected: null,
  actual: null,
  fix_hint: '',
  ...extra,
});

/** HTTP 200, `valid: false`. The verdict lives in the body, not in the status code. */
const INVALID_REPORT = {
  valid: false,
  dag_hash: 'dag_a',
  validation_state: 'INVALID',
  errors: [
    issue({
      code: 'PARAM_OUT_OF_RANGE',
      node_id: 'n-data',
      field: 'window',
      message: 'RSI period must be between 2 and 500. Got 0.',
      expected: { min: 2, max: 500 },
      actual: 0,
      fix_hint: NODE_HINT,
    }),
    issue({ code: 'EDGE_TYPE_MISMATCH', edge_id: 'e-1', message: 'OHLCV_FRAME cannot feed a SIGNAL input.', fix_hint: EDGE_HINT }),
    issue({ code: 'MISSING_REQUIRED_CATEGORY', message: 'This strategy declares no ACTION block.', fix_hint: GRAPH_HINT }),
  ],
  warnings: [
    issue({
      code: 'WARMUP_EXCEEDS_HISTORY',
      severity: 'warning',
      node_id: 'n-data',
      message: 'This strategy needs 226 warmup bars. The selected range provides 180.',
      expected: 226,
      actual: 180,
      fix_hint: WARMUP_HINT,
    }),
  ],
  summary: { node_count: 2, edge_count: 1, warmup_bars: 226 },
};

const VALID_REPORT = {
  valid: true,
  dag_hash: 'dag_b',
  validation_state: 'VALID',
  errors: [],
  warnings: [],
  summary: { node_count: 2, edge_count: 1, warmup_bars: 30 },
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/**
 * jsdom implements no `DragEvent`, so `fireEvent.drop` falls back to a plain `Event` and drops
 * `clientX` / `clientY` — which would give the new node a `NaN` position and make the canvas
 * unserializable for reasons that have nothing to do with the code under test. This stub is
 * only the pointer coordinates a real drop carries; it adds no behaviour.
 */
class DragEventStub extends window.MouseEvent {
  constructor(type, init = {}) {
    super(type, init);
    this.dataTransfer = init.dataTransfer ?? null;
  }
}

/** Every validate call, each with the body sent and a handle to answer it later. */
let validateCalls = [];

const answerValidateWith = (report) => {
  mockClient.post.mockImplementation((url, body) => {
    if (url === '/api/strategies/validate') {
      validateCalls.push({ body });
      return Promise.resolve(report);
    }
    return Promise.resolve({ status: 'ok', id: 'strategy-1' });
  });
};

/** Validate calls are parked until the test answers them, so a race can be built on purpose. */
const deferValidate = () => {
  mockClient.post.mockImplementation((url, body) => {
    if (url === '/api/strategies/validate') {
      const call = { body };
      call.promise = new Promise((resolve, reject) => {
        call.resolve = resolve;
        call.reject = reject;
      });
      validateCalls.push(call);
      return call.promise;
    }
    return Promise.resolve({ status: 'ok', id: 'strategy-1' });
  });
};

const renderBuilder = (initialStrategy = null) =>
  render(
    <MemoryRouter>
      <ReactFlowProvider>
        <UndoRedoProvider>
          <ValidationProvider>
            <StrategyBuilderCanvas initialStrategy={initialStrategy} />
          </ValidationProvider>
        </UndoRedoProvider>
      </ReactFlowProvider>
    </MemoryRouter>,
  );

const registryReady = () =>
  waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));

/** Drop a palette block onto the canvas: a semantic edit, made the way an author makes one. */
const dropBlock = (blockId) => {
  fireEvent.drop(screen.getByTestId('canvas'), {
    clientX: 120,
    clientY: 90,
    dataTransfer: { getData: (mime) => (mime === DRAG_BLOCK_ID_MIME ? blockId : '') },
  });
};

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

const requestCounter = () => screen.getByTestId('validation-requests');

beforeEach(() => {
  global.ResizeObserver = ResizeObserverStub;
  window.DragEvent = DragEventStub;
  window.localStorage.clear();
  validateCalls = [];
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  mockClient.get.mockResolvedValue(served());
  answerValidateWith(VALID_REPORT);
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. Unvalidated on edit, one request 400 ms after the last edit (Requirement 8.10)
// ---------------------------------------------------------------------------

describe('an edit marks the graph unvalidated and schedules one backend check', () => {
  it('starts unvalidated, and asks nothing about an empty canvas', async () => {
    renderBuilder();
    await registryReady();

    const cell = screen.getByTestId('validation-state');
    expect(cell.dataset.state).toBe('unvalidated');
    expect(cell.dataset.known).toBe('false');
    expect(cell.textContent).toContain('Not validated yet');

    await sleep(VALIDATION_DEBOUNCE_MS + 250);
    expect(validateCalls).toHaveLength(0);
  });

  it('marks the graph unvalidated the moment it changes, before any request goes out', async () => {
    renderBuilder();
    await registryReady();

    dropBlock('ohlcv_feed');

    // Synchronously after the edit: unvalidated, and nothing has been asked yet.
    expect(screen.getByTestId('validation-state').dataset.state).toBe('unvalidated');
    expect(validateCalls).toHaveLength(0);
  });

  it('collapses a burst of edits into one request, and sends the canonical graph', async () => {
    renderBuilder();
    await registryReady();

    dropBlock('ohlcv_feed');
    dropBlock('sma');
    dropBlock('market_buy');
    expect(validateCalls).toHaveLength(0);

    await waitFor(() => expect(validateCalls.length).toBeGreaterThan(0), { timeout: 3000 });
    // Let any further timer that a per-edit implementation would have armed fire.
    await sleep(VALIDATION_DEBOUNCE_MS + 400);

    expect(validateCalls).toHaveLength(1);
    expect(requestCounter().dataset.requests).toBe('1');

    const [{ body }] = validateCalls;
    expect(body.schema_version).toBe(2);
    expect(body.nodes.map((node) => node.block_id)).toEqual(['ohlcv_feed', 'sma', 'market_buy']);
  });

  it('does not keep re-validating once a report has landed (no render loop)', async () => {
    answerValidateWith(INVALID_REPORT);
    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();

    await waitFor(() => expect(screen.getByTestId('validation-state').dataset.state).toBe('invalid'), { timeout: 3000 });
    // Markers are written back onto the nodes here. If that write-back re-entered the
    // debounce — new node objects → new graph → new request → new nodes — the count would
    // climb without limit.
    await sleep(3 * VALIDATION_DEBOUNCE_MS);
    expect(validateCalls).toHaveLength(1);
    expect(requestCounter().dataset.requests).toBe('1');
  });
});

// ---------------------------------------------------------------------------
// 2. HTTP 200 is not a verdict; the body is (Requirements 8.8, 8.9)
// ---------------------------------------------------------------------------

describe('a 200 response carrying valid:false marks the graph invalid', () => {
  const renderMarked = async () => {
    answerValidateWith(INVALID_REPORT);
    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();
    await waitFor(() => expect(screen.getByTestId('validation-issues')).toBeTruthy(), { timeout: 3000 });
  };

  it('reads the verdict from the body, not from the status code', async () => {
    await renderMarked();
    const cell = screen.getByTestId('validation-state');
    expect(cell.dataset.state).toBe('invalid');
    expect(cell.dataset.known).toBe('true');
    expect(cell.textContent).toContain('3 errors');
    expect(cell.textContent).toContain('1 warning');
  });

  it('marks the node the report names with its severity and issue count', async () => {
    await renderMarked();
    const group = screen.getByTestId('node-issue-group');
    expect(group.dataset.nodeId).toBe('n-data');
    expect(group.dataset.severity).toBe('error');
    expect(group.dataset.issueCount).toBe('2');
    expect(group.textContent).toContain('1 error, 1 warning');
  });

  it('marks the connection the report names with its severity and issue count', async () => {
    await renderMarked();
    const group = screen.getByTestId('edge-issue-group');
    expect(group.dataset.edgeId).toBe('e-1');
    expect(group.dataset.severity).toBe('error');
    expect(group.dataset.issueCount).toBe('1');
  });

  it('stamps the marker onto the canvas node itself, severity and count as text', async () => {
    await renderMarked();
    // The write-back onto the nodes is one commit behind the report landing.
    const marker = await waitFor(() => {
      const found = document.querySelector('[data-testid="node-marker"][data-node-id="n-data"]');
      expect(found).toBeTruthy();
      return found;
    });
    expect(marker.dataset.severity).toBe('error');
    expect(marker.dataset.issueCount).toBe('2');
    expect(marker.dataset.source).toBe('backend');
    expect(marker.textContent).toContain('1 error, 1 warning');
  });

  it('renders every fix_hint verbatim, punctuation included', async () => {
    await renderMarked();
    const panel = screen.getByTestId('validation-issues');
    for (const hint of [NODE_HINT, EDGE_HINT, GRAPH_HINT, WARMUP_HINT]) {
      expect(panel.textContent).toContain(hint);
    }
    // The backend's message text travels too, alongside its hint.
    expect(panel.textContent).toContain('RSI period must be between 2 and 500. Got 0.');
  });

  it('renders an issue that names neither a node nor a connection instead of dropping it', async () => {
    await renderMarked();
    const graphIssues = screen.getByTestId('graph-issues');
    expect(graphIssues.textContent).toContain('MISSING_REQUIRED_CATEGORY');
    expect(graphIssues.textContent).toContain(GRAPH_HINT);
  });

  it('labels every issue row with its severity and its target', async () => {
    await renderMarked();
    const rows = [...document.querySelectorAll('[data-testid="validation-issue"]')];
    expect(rows).toHaveLength(4);
    expect(rows.filter((row) => row.dataset.severity === 'error')).toHaveLength(3);
    expect(rows.filter((row) => row.dataset.severity === 'warning')).toHaveLength(1);
    expect(rows.map((row) => row.dataset.scope).sort()).toEqual(['edge', 'graph', 'node', 'node']);
  });

  it('lets an issue row reach the block it complains about', async () => {
    await renderMarked();
    const row = document.querySelector('[data-testid="validation-issue"][data-node-id="n-data"]');
    fireEvent.click(row);

    const status = screen.getByTestId('inspector-node-status');
    expect(status.dataset.nodeId).toBe('n-data');
    expect(status.dataset.severity).toBe('error');
    expect(status.dataset.issueCount).toBe('2');
  });

  it('reports a warnings-only report as valid, and still shows the warning', async () => {
    const warningsOnly = {
      ...VALID_REPORT,
      valid: true,
      warnings: [INVALID_REPORT.warnings[0]],
      summary: { node_count: 2, edge_count: 1, warmup_bars: 226 },
    };
    answerValidateWith(warningsOnly);
    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();

    await waitFor(() => expect(screen.getByTestId('validation-state').dataset.state).toBe('valid'), { timeout: 3000 });
    expect(screen.getByTestId('validation-issues').textContent).toContain(WARMUP_HINT);
    expect(screen.getByTestId('node-issue-group').dataset.severity).toBe('warning');
  });
});

// ---------------------------------------------------------------------------
// 3. Stale responses (Requirement 8.10)
// ---------------------------------------------------------------------------

describe('a response for a superseded graph is discarded, not applied', () => {
  it('drops the in-flight answer when the graph changed while it was in flight', async () => {
    deferValidate();
    renderBuilder();
    await registryReady();

    dropBlock('ohlcv_feed');
    await waitFor(() => expect(validateCalls).toHaveLength(1), { timeout: 3000 });
    const first = validateCalls[0];
    expect(first.body.nodes).toHaveLength(1);

    // A further edit while the first request is still open. It supersedes that request.
    dropBlock('market_buy');
    await waitFor(() => expect(validateCalls).toHaveLength(2), { timeout: 3000 });
    expect(validateCalls[1].body.nodes).toHaveLength(2);

    // Now the *older* request answers, marking a node with an error.
    first.resolve({
      ...INVALID_REPORT,
      errors: [issue({ code: 'PARAM_REQUIRED_MISSING', node_id: 'n-1', field: 'symbol', message: 'stale answer', fix_hint: 'stale hint' })],
      warnings: [],
    });

    await waitFor(() => expect(requestCounter().dataset.discarded).toBe('1'), { timeout: 3000 });
    // Nothing from the stale report reached the canvas or the issue list.
    expect(screen.queryByTestId('validation-issues')).toBeNull();
    expect(document.body.textContent).not.toContain('stale hint');
    expect(screen.getByTestId('validation-state').dataset.state).toBe('validating');

    // The current request still lands normally.
    validateCalls[1].resolve(VALID_REPORT);
    await waitFor(() => expect(screen.getByTestId('validation-state').dataset.state).toBe('valid'), { timeout: 3000 });
    expect(requestCounter().dataset.discarded).toBe('1');
  });
});

// ---------------------------------------------------------------------------
// 4. Failing closed
// ---------------------------------------------------------------------------

describe('the frontend fails closed and invents no verdict of its own', () => {
  it('treats a transport failure as unavailable, not as valid', async () => {
    mockClient.post.mockImplementation((url) => {
      if (url === '/api/strategies/validate') {
        return Promise.reject(Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK', status: 503 }));
      }
      return Promise.resolve({ status: 'ok' });
    });

    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();

    const banner = await waitFor(() => screen.getByTestId('validation-unavailable'), { timeout: 3000 });
    expect(banner.dataset.code).toBe('ERR_NETWORK');
    expect(banner.textContent).toContain('Validation unavailable');

    const cell = screen.getByTestId('validation-state');
    expect(cell.dataset.state).toBe('unavailable');
    expect(cell.dataset.known).toBe('false');
    expect(cell.dataset.state).not.toBe('valid');
    // No report was ever received, so nothing is marked.
    expect(screen.queryByTestId('validation-issues')).toBeNull();
  });

  it('treats a 200 carrying no verdict as no verdict', async () => {
    answerValidateWith({ dag_hash: 'dag_a', errors: [], warnings: [] });

    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();

    const banner = await waitFor(() => screen.getByTestId('validation-unavailable'), { timeout: 3000 });
    expect(banner.dataset.code).toBe('VALIDATION_MALFORMED');
    expect(screen.getByTestId('validation-state').dataset.state).toBe('unavailable');
  });
});

// ---------------------------------------------------------------------------
// 5. The status strip (Requirement 8.11)
// ---------------------------------------------------------------------------

describe('the status strip carries the validation, feed, save and training states', () => {
  it('renders all four cells, each stating what is actually known', async () => {
    renderBuilder();
    await registryReady();

    const strip = screen.getByTestId('status-strip');
    expect(strip.getAttribute('aria-live')).toBe('polite');

    const validation = screen.getByTestId('validation-state');
    expect(validation.dataset.state).toBe('unvalidated');
    expect(validation.dataset.known).toBe('false');

    // No DATA block names a market and the builder holds no subscription: unknown, not live.
    const feed = screen.getByTestId('feed-state');
    expect(feed.dataset.state).toBe('UNKNOWN');
    expect(feed.dataset.known).toBe('false');
    expect(feed.dataset.state).not.toBe('LIVE');

    const save = screen.getByTestId('save-state');
    expect(save.dataset.state).toBe('UNSAVED');
    expect(save.dataset.known).toBe('false');
    expect(save.textContent).toContain('Not saved yet');

    // No ML block on the canvas, so "not required" is a knowable answer.
    const training = screen.getByTestId('training-state');
    expect(training.dataset.state).toBe('NOT_REQUIRED');
    expect(training.dataset.known).toBe('true');
  });

  it('reports the feed as unknown even once a market is named, because nothing is observed', async () => {
    answerValidateWith(VALID_REPORT);
    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();

    await waitFor(() => expect(screen.getByTestId('validation-state').dataset.state).toBe('valid'), { timeout: 3000 });
    const feed = screen.getByTestId('feed-state');
    expect(feed.dataset.state).toBe('UNKNOWN');
    expect(feed.dataset.known).toBe('false');
    expect(feed.getAttribute('title')).toContain('unknown, not healthy');
  });

  it('reports an observed feed and an observed training job literally', async () => {
    answerValidateWith(VALID_REPORT);
    render(
      <MemoryRouter>
        <ReactFlowProvider>
          <UndoRedoProvider>
            <ValidationProvider>
              <StrategyBuilderCanvas
                initialStrategy={{ id: null, name: 'Marked Strategy', graph_json: GRAPH }}
                feedObservation={{ connected: true, lastEventAt: Date.now() }}
                trainingJob={{ id: 'job-7', status: 'RUNNING', current_epoch: 2, epochs_total: 8 }}
              />
            </ValidationProvider>
          </UndoRedoProvider>
        </ReactFlowProvider>
      </MemoryRouter>,
    );
    await registryReady();

    await waitFor(() => expect(screen.getByTestId('feed-state').dataset.state).toBe('LIVE'));
    expect(screen.getByTestId('feed-state').dataset.known).toBe('true');

    // The graph declares no ML block, so the job does not turn "not required" into "running":
    // the state reported is a fact about this graph, not about whatever job was handed in.
    expect(screen.getByTestId('training-state').dataset.state).toBe('NOT_REQUIRED');
  });

  it('reports the validation summary counts once a report applies', async () => {
    answerValidateWith(INVALID_REPORT);
    renderBuilder({ id: null, name: 'Marked Strategy', graph_json: GRAPH });
    await registryReady();

    await waitFor(() => expect(screen.getByTestId('validation-state').dataset.state).toBe('invalid'), { timeout: 3000 });
    const cell = screen.getByTestId('validation-state');
    expect(cell.getAttribute('title')).toBe('2 nodes, 1 connections, 226 warmup bars');
  });
});
