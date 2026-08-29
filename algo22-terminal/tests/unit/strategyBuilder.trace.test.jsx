/**
 * Execution-trace wiring tests for the inspector (task 9.3, Requirement 24.6).
 *
 * "THE Strategy_Builder SHALL display the recorded execution trace for a selected node,
 * comprising that node's inputs, outputs, duration and recorded failures."
 *
 * What is under test is the contract between the inspector and the trace the runtime already
 * recorded:
 *
 * * the node's bound inputs, its output, its duration and the run's recorded failures are on
 *   screen, as the endpoint published them;
 * * **the refused run shows its trace too** — a run that produced nothing is exactly the run
 *   whose trace answers "why did nothing happen?", and `PREVIEW_EXECUTION_FAILED` carries one;
 * * an upstream failure is named as upstream, so the author is pointed at the block to fix
 *   rather than the block selected;
 * * **no second request**: the trace rides the preview response, so there is no trace endpoint,
 *   no second ownership resolution and no second rate limit;
 * * selecting a different node drops the trace instead of relabelling it;
 * * nothing about an exchange or a credential reaches the screen (SB-06, Requirement 12.1).
 *
 * The axios instance is the only stub. `registryClient`, `canonicalGraph`, `nodeTrace`,
 * `NodeTrace`, `NodePreview` and the real React Flow canvas are the shipped code.
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

import { StrategyBuilderCanvas } from '../../src/pages/StrategyBuilder';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { UndoRedoProvider } from '../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../src/contexts/ValidationContext';
import { toCanonical } from '../../src/lib/canonicalGraph';

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
  outputs: [{ port: 'close', type: 'PRICE_SERIES' }],
  params: [
    param('symbol', 'SYMBOL', { required: true, example: 'BTC/USDT' }),
    param('timeframe', 'TIMEFRAME', { required: true, example: '15m' }),
  ],
  allowed_predecessor_categories: [],
  allowed_successor_categories: ['INDICATOR', 'FEATURE_ENGINEERING', 'ACTION'],
  execution_semantics: 'STATEFUL',
  runtime_ref: 'module:ohlcv_feed',
};

const EMA = {
  block_id: 'ema',
  display_name: 'EMA',
  category: 'INDICATOR',
  description: 'Exponential moving average.',
  inputs: [{ port: 'series', type: 'PRICE_SERIES', required: true }],
  outputs: [{ port: 'value', type: 'SCALAR_SERIES' }],
  params: [param('window', 'INTEGER', { default: 20 })],
  allowed_predecessor_categories: ['DATA'],
  allowed_successor_categories: ['LOGIC', 'ACTION'],
  execution_semantics: 'STATEFUL',
  runtime_ref: 'indicators_backend.ema',
};

const registryPayload = {
  registry_version: 'r_trace_0001',
  registry_schema_version: 1,
  port_types: ['OHLCV_FRAME', 'PRICE_SERIES', 'SCALAR_SERIES', 'SCALAR'],
  categories: CATEGORIES,
  blocks: [OHLCV_FEED, EMA],
  compatibility_matrix: {
    PRICE_SERIES: ['PRICE_SERIES', 'SCALAR_SERIES'],
    SCALAR_SERIES: ['SCALAR_SERIES'],
  },
};

const served = () => ({ status: 200, data: registryPayload, headers: { etag: '"r_trace_0001"' } });

// ---------------------------------------------------------------------------
// Graph fixture
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_trace_1';

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

const GRAPH = toCanonical(
  [
    canvasNode('n-data', OHLCV_FEED, { symbol: 'ETH/USDT', timeframe: '5m' }),
    canvasNode('n-ema', EMA, { window: 20 }),
  ],
  [{ id: 'e-1', source: 'n-data', sourceHandle: 'close', target: 'n-ema', targetHandle: 'series' }],
  { name: 'Traced Strategy' },
);

const SAVED_STRATEGY = { id: STRATEGY_ID, name: 'Traced Strategy', graph_json: GRAPH };

// ---------------------------------------------------------------------------
// Responses, in the endpoint's wire shape
// ---------------------------------------------------------------------------

const WINDOW = {
  requested: null,
  bars: 200,
  max_bars: 1200,
  min_bars: 50,
  sample_rows: 50,
  node_warmup_bars: 60,
  needed_bars: 170,
  clamped: false,
  available_bars: 200,
  warmup_bars: 60,
  plan_warmup_bars: 60,
  warmup_exceeds_window: false,
  first_timestamp: '2024-03-01T00:00:00',
  last_timestamp: '2024-03-01T16:35:00',
};

const traceEntry = (overrides = {}) => ({
  node_id: 'n-ema',
  node_type: 'indicator',
  status: 'success',
  duration_ms: 12.5,
  error_message: null,
  recorded_at: '2024-03-01T16:36:00',
  inputs: [
    {
      port: 'series',
      source: 'n-data',
      type: 'Series[float64]',
      shape: { type: 'Series', length: 200 },
    },
    { port: 'window', source: 'n-const', type: 'int', shape: { type: 'scalar', value: 20 } },
  ],
  output: { type: 'Series[float64]', shape: { type: 'Series', length: 200 } },
  ...overrides,
});

const SUCCESS_TRACE = {
  node_id: 'n-ema',
  status: 'success',
  recorded: true,
  duration_ms: 12.5,
  executions: [traceEntry()],
  failures: [],
  blocking_failure: null,
  conditions: [
    {
      node_id: 'n-ema',
      code: 'NON_FINITE_INPUT',
      bar: 34,
      bars_affected: 3,
      detail: 'input carried inf',
      display: 'NON_FINITE_INPUT on 3 bars from bar 34: input carried inf',
    },
  ],
  executed_nodes: ['n-data', 'n-ema'],
  summary:
    'This block executed in 12.5 ms and recorded 1 numeric condition, the first of which is ' +
    'NON_FINITE_INPUT on 3 bars from bar 34: input carried inf. That is why bars are empty ' +
    'rather than wrong.',
  signal_provenance: {
    available: true,
    traces_seen: 0,
    records: [],
    message:
      'No live signal has been traced for this block. Signal provenance is recorded while a ' +
      'deployment runs; a preview is not a deployment.',
  },
};

const EMA_PREVIEW = {
  strategy_id: STRATEGY_ID,
  node_id: 'n-ema',
  block_id: 'ema',
  category: 'INDICATOR',
  graph_source: 'request',
  dag_hash: 'd_ema',
  compiler_version: '2.1.0',
  market: { symbol: 'ETH/USDT', timeframe: '5m' },
  window: WINDOW,
  executed_nodes: ['n-data', 'n-ema'],
  outputs: [
    {
      name: 'value',
      port_type: 'SCALAR_SERIES',
      produced: true,
      kind: 'series',
      length: 200,
      index: ['2024-03-01T16:30:00', '2024-03-01T16:35:00'],
      values: [null, 3421.5],
      empty_values: 1,
    },
  ],
  issues: [],
  trace: SUCCESS_TRACE,
  computed_at: '2024-03-01T16:36:00+00:00',
};

/** The 422 the endpoint sends when the run itself failed, trace and all. */
const EXECUTION_FAILED = {
  status: 422,
  data: {
    detail: {
      error: 'PREVIEW_EXECUTION_FAILED',
      message: "Node 'n-data': the feed returned no candles",
      failure: 'DAGExecutionError',
      node_id: 'n-ema',
      issues: [],
      trace: {
        node_id: 'n-ema',
        status: 'not_executed',
        recorded: false,
        duration_ms: null,
        executions: [],
        failures: [
          traceEntry({
            node_id: 'n-data',
            node_type: 'data',
            status: 'fail',
            duration_ms: 2.25,
            error_message: 'the feed returned no candles',
            inputs: [],
            output: { type: 'NoneType', shape: { type: 'NoneType' } },
          }),
        ],
        blocking_failure: traceEntry({
          node_id: 'n-data',
          node_type: 'data',
          status: 'fail',
          duration_ms: 2.25,
          error_message: 'the feed returned no candles',
          inputs: [],
          output: { type: 'NoneType', shape: { type: 'NoneType' } },
        }),
        conditions: [],
        executed_nodes: ['n-data', 'n-ema'],
        summary:
          "This block did not execute. The run stopped at 'n-data': the feed returned no candles",
        signal_provenance: {
          available: true,
          traces_seen: 0,
          records: [],
          message: 'No live signal has been traced for this block.',
        },
      },
    },
  },
};

const VALID_REPORT = {
  valid: true,
  dag_hash: 'dag_ok',
  validation_state: 'VALID',
  errors: [],
  warnings: [],
  summary: { node_count: 2, edge_count: 1, warmup_bars: 60 },
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

let previewCalls = [];
let otherPosts = [];
let gets = [];

const answerPreviewWith = (body, { reject = null } = {}) => {
  mockClient.post.mockImplementation((url, payload) => {
    if (url.includes('/preview')) {
      previewCalls.push({ url, body: payload });
      if (reject !== null) return Promise.reject(reject);
      return Promise.resolve(body);
    }
    if (url === '/api/strategies/validate') return Promise.resolve(VALID_REPORT);
    otherPosts.push(url);
    return Promise.resolve({ status: 'ok', id: STRATEGY_ID });
  });
};

const renderBuilder = () =>
  render(
    <MemoryRouter>
      <ReactFlowProvider>
        <UndoRedoProvider>
          <ValidationProvider>
            <StrategyBuilderCanvas initialStrategy={SAVED_STRATEGY} />
          </ValidationProvider>
        </UndoRedoProvider>
      </ReactFlowProvider>
    </MemoryRouter>,
  );

const registryReady = () =>
  waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));

const selectNode = async (nodeId) => {
  const node = await waitFor(() => {
    const found = document.querySelector(`[data-id="${nodeId}"]`);
    if (found === null) throw new Error(`node ${nodeId} is not on the canvas yet`);
    return found;
  });
  fireEvent.click(node);
  return node;
};

const pressPreview = () => fireEvent.click(screen.getByTestId('preview-run'));

const tracePanel = () => screen.getByTestId('node-trace');

beforeEach(() => {
  global.ResizeObserver = ResizeObserverStub;
  window.localStorage.clear();
  previewCalls = [];
  otherPosts = [];
  gets = [];
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  mockClient.get.mockImplementation((url) => {
    gets.push(url);
    return Promise.resolve(served());
  });
  answerPreviewWith(EMA_PREVIEW);
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. Nothing recorded is stated as such
// ---------------------------------------------------------------------------

describe('before anything has run', () => {
  it('says no execution has been recorded, rather than showing an empty trace', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');

    expect(tracePanel().getAttribute('data-state')).toBe('absent');
    expect(screen.getByTestId('trace-absent')).toBeTruthy();
    expect(previewCalls).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 2. Requirement 24.6 on the successful path
// ---------------------------------------------------------------------------

describe('the recorded trace for the selected node', () => {
  it('shows the bound inputs with their recorded types and shapes', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    const series = screen.getByTestId('trace-input-series');
    expect(series.textContent).toContain('series');
    expect(series.textContent).toContain('Series[float64]');
    expect(series.textContent).toContain('Series[200]');
    // The upstream node is available without being in the line: the port is the name on the
    // author's own block, and a ULID is not what makes the row readable.
    expect(series.getAttribute('data-source')).toBe('n-data');

    const window_ = screen.getByTestId('trace-input-window');
    expect(window_.textContent).toContain('scalar 20');
  });

  it('shows the recorded output and duration, from the tracer’s own figures', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    expect(screen.getByTestId('trace-output-0').textContent).toContain('Series[200]');
    expect(screen.getByTestId('trace-duration').textContent).toContain('12.5 ms');
    expect(screen.getByTestId('trace-execution-duration-0').textContent).toBe('12.5 ms');
  });

  it('shows the server’s summary sentence verbatim', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    expect(screen.getByTestId('trace-summary').textContent).toBe(SUCCESS_TRACE.summary);
  });

  it('shows each recorded numeric condition — why a bar is empty rather than wrong', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    expect(screen.getByTestId('trace-condition-NON_FINITE_INPUT').textContent).toBe(
      'NON_FINITE_INPUT on 3 bars from bar 34: input carried inf',
    );
  });

  it('states that live provenance is a different question, with the endpoint’s sentence', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    expect(screen.getByTestId('trace-provenance-message').textContent).toContain(
      'a preview is not a deployment',
    );
  });
});

// ---------------------------------------------------------------------------
// 3. The branch that matters most: the run that produced nothing
// ---------------------------------------------------------------------------

describe('a refused run still shows its trace', () => {
  it('renders the trace the 422 carried, beside the refusal', async () => {
    answerPreviewWith(null, { reject: EXECUTION_FAILED });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(screen.getByTestId('preview-error')).toBeTruthy());

    // The preview reports the refusal with the backend's own message …
    expect(screen.getByTestId('preview-error').textContent).toContain(
      'the feed returned no candles',
    );
    // … and the trace reports why nothing happened, which is the whole point of 24.6.
    expect(tracePanel().getAttribute('data-state')).toBe('recorded');
    expect(tracePanel().getAttribute('data-status')).toBe('not_executed');
    expect(screen.getByTestId('trace-summary').textContent).toContain("The run stopped at 'n-data'");
  });

  it('names the upstream block as upstream, not the selected one', async () => {
    answerPreviewWith(null, { reject: EXECUTION_FAILED });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(screen.getByTestId('trace-blocking-failure')).toBeTruthy());

    const blocking = screen.getByTestId('trace-blocking-failure');
    expect(blocking.getAttribute('data-upstream')).toBe('true');
    expect(blocking.getAttribute('data-failed-node')).toBe('n-data');
    expect(blocking.textContent).toContain('the feed returned no candles');
  });

  it('says the block did not execute, which is not "produced nothing"', async () => {
    answerPreviewWith(null, { reject: EXECUTION_FAILED });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(screen.getByTestId('trace-not-executed')).toBeTruthy());

    expect(screen.getByTestId('trace-status').textContent).toBe('Did not execute');
    expect(screen.getByTestId('trace-duration').textContent).toContain('\u2014');
    expect(screen.getByTestId('trace-duration').textContent).not.toContain('0 ms');
  });

  it('shows no trace for a refusal that executed nothing', async () => {
    // A graph the validator refused never reached an executor, so there is nothing recorded.
    answerPreviewWith(null, {
      reject: {
        status: 422,
        data: { detail: { error: 'STRATEGY_VALIDATION_FAILED', message: 'window must be positive' } },
      },
    });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(screen.getByTestId('preview-error')).toBeTruthy());

    expect(tracePanel().getAttribute('data-state')).toBe('absent');
  });
});

// ---------------------------------------------------------------------------
// 4. One request, and no store of its own
// ---------------------------------------------------------------------------

describe('the trace rides the preview response', () => {
  it('costs no second request, so there is no second ownership check to get wrong', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    // A trace path segment, not the substring: this fixture's own strategy id contains the
    // word, and matching that would make the assertion pass for the wrong reason.
    const namesATraceRoute = (url) => typeof url === 'string' && /\/traces?\b/.test(url);

    expect(previewCalls).toHaveLength(1);
    expect(otherPosts.filter(namesATraceRoute)).toEqual([]);
    expect(gets.filter(namesATraceRoute)).toEqual([]);
  });

  it('drops the trace when the author selects a different block', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();
    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    await selectNode('n-data');

    // A trace is a statement about one run of one block. Relabelling it would be worse than
    // showing nothing.
    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('absent'));
  });
});

// ---------------------------------------------------------------------------
// 5. SB-06 / Requirement 12.1
// ---------------------------------------------------------------------------

describe('nothing about an exchange or a credential reaches the screen', () => {
  it('renders no venue and no credential even when a payload carries one', async () => {
    answerPreviewWith({
      ...EMA_PREVIEW,
      trace: {
        ...SUCCESS_TRACE,
        exchange: 'LEAKED_VENUE',
        api_key: 'LEAKED_KEY',
        exchange_account_id: 'LEAKED_ACCOUNT',
      },
    });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await waitFor(() => expect(tracePanel().getAttribute('data-state')).toBe('recorded'));

    const rendered = tracePanel().textContent;
    for (const leak of ['LEAKED_VENUE', 'LEAKED_KEY', 'LEAKED_ACCOUNT']) {
      expect(rendered).not.toContain(leak);
    }
    // The market is fine — it is the graph's own DATA parameter, not a venue.
    expect(rendered).toContain('12.5 ms');
  });
});
