/**
 * Node-preview wiring tests for the inspector (task 5.7, Requirements 24.7 and 24.8).
 *
 * What is under test is the contract between the inspector and the one endpoint that can
 * answer "what does this block produce?":
 *
 * * the preview is **requested**, never computed — one
 *   `POST /api/strategy-operations/strategies/{id}/nodes/{node}/preview` per press, carrying the
 *   canvas graph as `blueprint` and nothing about an exchange (SB-06, Requirement 12.1);
 * * the values on screen are the values the response carried, and a bar the response left
 *   `null` renders as the empty placeholder rather than as `0`;
 * * a FEATURE_ENGINEERING node's preview names **every** produced column, and says so when the
 *   value sample is narrower than the column list (Requirement 24.8);
 * * the bounded window is stated, including the cap when it applied (Requirement 24.7);
 * * the numeric conditions the run recorded are rendered, which is the answer to "why is this
 *   bar empty?" (Requirement 20.4);
 * * a refusal shows the backend's own message and hint verbatim, with no numbers left on
 *   screen beside them;
 * * an unsaved strategy is explained rather than silently disabled, and no request is made;
 * * selecting a different node drops the held preview instead of relabelling it.
 *
 * The axios instance is the only stub. `registryClient`, `canonicalGraph`, `nodePreview`, the
 * reducer, the projection, `NodePreview` and the real React Flow canvas are the shipped code.
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
import { EMPTY_VALUE_TEXT } from '../../src/lib/nodePreview';

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

const FEAT_LAG = {
  block_id: 'feat_lag',
  display_name: 'Lag',
  category: 'FEATURE_ENGINEERING',
  description: 'Past values of a series at the chosen offsets.',
  inputs: [{ port: 'series', type: 'SCALAR_SERIES', required: true }],
  outputs: [{ port: 'matrix', type: 'FEATURE_MATRIX' }],
  params: [param('lags', 'MULTISELECT', { required: true, default: [1, 2, 3] })],
  allowed_predecessor_categories: ['DATA', 'INDICATOR'],
  allowed_successor_categories: ['ML_DL'],
  execution_semantics: 'SERIES_MAP',
  runtime_ref: 'FeatureEngine.compute_lag_features',
};

const MARKET_BUY = {
  block_id: 'action_buy_market',
  display_name: 'Market Buy',
  category: 'ACTION',
  description: 'Submits a market buy order.',
  inputs: [{ port: 'signal', type: 'SIGNAL', required: true }],
  outputs: [],
  params: [param('quantity', 'NUMBER', { required: true })],
  allowed_predecessor_categories: ['LOGIC'],
  allowed_successor_categories: [],
  execution_semantics: 'TERMINAL',
  runtime_ref: 'module:action_buy_market',
};

const registryPayload = {
  registry_version: 'r_prev_0001',
  registry_schema_version: 1,
  port_types: ['OHLCV_FRAME', 'PRICE_SERIES', 'SCALAR_SERIES', 'BOOLEAN_SERIES', 'FEATURE_MATRIX', 'PREDICTION', 'SIGNAL', 'TRADE_INTENT', 'SCALAR'],
  categories: CATEGORIES,
  blocks: [OHLCV_FEED, EMA, FEAT_LAG, MARKET_BUY],
  compatibility_matrix: {
    PRICE_SERIES: ['PRICE_SERIES', 'SCALAR_SERIES'],
    SCALAR_SERIES: ['SCALAR_SERIES'],
    FEATURE_MATRIX: ['FEATURE_MATRIX'],
  },
};

const served = () => ({ status: 200, data: registryPayload, headers: { etag: '"r_prev_0001"' } });

// ---------------------------------------------------------------------------
// Graph fixture: a saved strategy, so the preview has a strategy id to address
// ---------------------------------------------------------------------------

const STRATEGY_ID = 'stg_preview_1';

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
    canvasNode('n-lag', FEAT_LAG, { lags: [1, 2, 3] }),
  ],
  [
    { id: 'e-1', source: 'n-data', sourceHandle: 'close', target: 'n-ema', targetHandle: 'series' },
    { id: 'e-2', source: 'n-ema', sourceHandle: 'value', target: 'n-lag', targetHandle: 'series' },
  ],
  { name: 'Previewed Strategy' },
);

const SAVED_STRATEGY = { id: STRATEGY_ID, name: 'Previewed Strategy', graph_json: GRAPH };
const UNSAVED_STRATEGY = { id: null, name: 'Previewed Strategy', graph_json: GRAPH };

// ---------------------------------------------------------------------------
// Preview responses, in the endpoint's wire shape
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

/** An EMA preview whose first sampled bar is warmup: `null`, and it must not read as zero. */
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
      index: ['2024-03-01T16:25:00', '2024-03-01T16:30:00', '2024-03-01T16:35:00'],
      values: [null, 3421.5, 3422.75],
      empty_values: 1,
    },
  ],
  issues: [],
  computed_at: '2024-03-01T16:36:00+00:00',
};

const lagPreview = (columns, sampled) => ({
  ...EMA_PREVIEW,
  node_id: 'n-lag',
  block_id: 'feat_lag',
  category: 'FEATURE_ENGINEERING',
  executed_nodes: ['n-data', 'n-ema', 'n-lag'],
  outputs: [
    {
      name: 'matrix',
      port_type: 'FEATURE_MATRIX',
      produced: true,
      kind: 'feature_matrix',
      length: 200,
      columns,
      column_count: columns.length,
      sampled_columns: sampled,
      sample_truncated: sampled.length < columns.length,
      column_warmup: Object.fromEntries(columns.map((name, i) => [name, i + 1])),
      provenance: Object.fromEntries(columns.map((name) => [name, 'n-lag'])),
      warmup_offset: columns.length,
      index: ['2024-03-01T16:30:00', '2024-03-01T16:35:00'],
      values: [sampled.map(() => null), sampled.map((_, i) => 3400 + i)],
      empty_values: sampled.length,
    },
  ],
});

const VALID_REPORT = {
  valid: true,
  dag_hash: 'dag_ok',
  validation_state: 'VALID',
  errors: [],
  warnings: [],
  summary: { node_count: 3, edge_count: 2, warmup_bars: 60 },
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

class DragEventStub extends window.MouseEvent {
  constructor(type, init = {}) {
    super(type, init);
    this.dataTransfer = init.dataTransfer ?? null;
  }
}

const PREVIEW_URL = (nodeId, strategyId = STRATEGY_ID) =>
  `/api/strategy-operations/strategies/${strategyId}/nodes/${nodeId}/preview`;

/** Every preview call, each with the URL and the body sent. */
let previewCalls = [];

const answerPreviewWith = (bodyOrFactory, { reject = null } = {}) => {
  mockClient.post.mockImplementation((url, body) => {
    if (url.includes('/preview')) {
      previewCalls.push({ url, body });
      if (reject !== null) return Promise.reject(reject);
      const answer = typeof bodyOrFactory === 'function' ? bodyOrFactory(url, body) : bodyOrFactory;
      return Promise.resolve(answer);
    }
    if (url === '/api/strategies/validate') return Promise.resolve(VALID_REPORT);
    return Promise.resolve({ status: 'ok', id: STRATEGY_ID });
  });
};

const renderBuilder = (initialStrategy = SAVED_STRATEGY) =>
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

/** Click the node with `nodeId`, which is how an author opens it in the inspector. */
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

beforeEach(() => {
  global.ResizeObserver = ResizeObserverStub;
  window.DragEvent = DragEventStub;
  window.localStorage.clear();
  previewCalls = [];
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  mockClient.get.mockResolvedValue(served());
  answerPreviewWith(EMA_PREVIEW);
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. The preview is requested, not computed (Requirement 24.7)
// ---------------------------------------------------------------------------

describe('the inspector asks the backend for the preview', () => {
  it('asks nothing until the author presses Preview', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');

    expect(screen.getByTestId('preview-idle')).toBeTruthy();
    expect(previewCalls).toHaveLength(0);
  });

  it('issues one request to the node preview endpoint, carrying the canvas graph', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');

    pressPreview();

    await waitFor(() => expect(previewCalls).toHaveLength(1));
    const [{ url, body }] = previewCalls;
    expect(url).toBe(PREVIEW_URL('n-ema'));
    // The canvas as it stands, not the saved version: the author is looking at the canvas.
    expect(body.blueprint.schema_version).toBe(2);
    expect(body.blueprint.nodes.map((node) => node.block_id)).toContain('ema');
  });

  it('sends nothing about an exchange or a credential (SB-06, Requirement 12.1)', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();
    await waitFor(() => expect(previewCalls).toHaveLength(1));

    const sent = JSON.stringify(previewCalls[0].body).toLowerCase();
    for (const token of ['exchange', 'api_key', 'apikey', 'secret', 'passphrase', 'binance']) {
      expect(sent).not.toContain(token);
    }
    // And no window count either: the bound is the server's to choose and to cap.
    expect(Object.keys(previewCalls[0].body)).toEqual(['blueprint']);
  });

  it('renders the values the response carried, in the order it sent them', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const table = await screen.findByTestId('preview-series-value');
    expect(table.textContent).toContain('3421.5');
    expect(table.textContent).toContain('3422.75');
    expect(screen.getByTestId('preview-body').dataset.nodeId).toBe('n-ema');
  });

  it('renders a bar with no value as empty rather than as zero', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const first = await screen.findByTestId('preview-value-value-0');
    expect(first.dataset.empty).toBe('true');
    expect(first.textContent).toBe(EMPTY_VALUE_TEXT);
    expect(first.textContent).not.toBe('0');
    expect(screen.getByTestId('preview-empty-count-value').textContent).toContain('1');
  });

  it('states the bounded window, and the warmup that makes the leading bars empty', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const window = await screen.findByTestId('preview-window');
    expect(window.textContent).toContain('Last 200 bars');
    expect(window.textContent).toContain('60 warmup bars');
  });

  it('names the cap when the server reduced the window', async () => {
    answerPreviewWith({
      ...EMA_PREVIEW,
      window: { ...WINDOW, requested: 100000, bars: 1200, clamped: true },
    });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const window = await screen.findByTestId('preview-window');
    expect(window.textContent).toContain('at most 1200 bars');
  });

  it('shows the market the DATA block names, and no venue', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const market = await screen.findByTestId('preview-market');
    expect(market.textContent).toContain('ETH/USDT');
    expect(market.textContent).toContain('5m');
  });

  it('reports a port that produced nothing rather than leaving it out', async () => {
    answerPreviewWith({
      ...EMA_PREVIEW,
      outputs: [
        ...EMA_PREVIEW.outputs,
        { name: 'signal', port_type: 'SCALAR_SERIES', produced: false },
      ],
    });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const port = await screen.findByTestId('preview-output-signal');
    expect(port.dataset.produced).toBe('false');
    expect(port.textContent).toContain('produced no value');
  });
});

// ---------------------------------------------------------------------------
// 2. Requirement 24.8 — a FEATURE_ENGINEERING node's columns
// ---------------------------------------------------------------------------

describe('a FEATURE_ENGINEERING preview names its produced columns', () => {
  it('lists every produced column name with its warmup', async () => {
    answerPreviewWith(lagPreview(['lag_1', 'lag_2', 'lag_3'], ['lag_1', 'lag_2', 'lag_3']));
    renderBuilder();
    await registryReady();
    await selectNode('n-lag');
    pressPreview();

    const columns = await screen.findByTestId('preview-columns-matrix');
    expect(columns.textContent).toContain('lag_1');
    expect(columns.textContent).toContain('lag_2');
    expect(columns.textContent).toContain('lag_3');
    expect(screen.getByTestId('preview-column-lag_2').dataset.warmup).toBe('2');
    expect(screen.getByTestId('preview-body').dataset.category).toBe('FEATURE_ENGINEERING');
  });

  it('samples values under the column that produced them', async () => {
    answerPreviewWith(lagPreview(['lag_1', 'lag_2'], ['lag_1', 'lag_2']));
    renderBuilder();
    await registryReady();
    await selectNode('n-lag');
    pressPreview();

    // Row 0 is warmup — empty in both columns. Row 1 carries the produced values.
    expect((await screen.findByTestId('preview-cell-lag_1-0')).dataset.empty).toBe('true');
    expect(screen.getByTestId('preview-cell-lag_1-1').textContent).toBe('3400');
    expect(screen.getByTestId('preview-cell-lag_2-1').textContent).toBe('3401');
  });

  it('keeps every name when the value sample is narrower, and says it was', async () => {
    const columns = Array.from({ length: 18 }, (_, i) => `lag_${i + 1}`);
    answerPreviewWith(lagPreview(columns, columns.slice(0, 12)));
    renderBuilder();
    await registryReady();
    await selectNode('n-lag');
    pressPreview();

    const list = await screen.findByTestId('preview-columns-matrix');
    expect(list.querySelectorAll('li')).toHaveLength(18);
    expect(screen.getByTestId('preview-column-lag_18')).toBeTruthy();
    expect(screen.getByTestId('preview-truncated-matrix').textContent).toContain('12 of 18');
  });
});

// ---------------------------------------------------------------------------
// 3. Requirement 20.4 — why the bar is empty
// ---------------------------------------------------------------------------

describe('the recorded numeric conditions are shown', () => {
  it('renders the condition the run recorded against this node', async () => {
    const message = 'Division by a value at or near zero produced no value on 200 bars.';
    answerPreviewWith({
      ...EMA_PREVIEW,
      issues: [{ code: 'DIVISION_BY_ZERO', node_id: 'n-ema', bars_affected: 200, message }],
    });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const issue = await screen.findByTestId('preview-issue-DIVISION_BY_ZERO');
    expect(issue.textContent).toBe(message);
  });

  it('shows no issue list for a healthy node', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await screen.findByTestId('preview-body');
    expect(screen.queryByTestId('preview-issues')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 4. Refusals, and the controls around them
// ---------------------------------------------------------------------------

describe('a refused preview', () => {
  it("shows the backend's own message and hint, verbatim", async () => {
    const message =
      'This node needs 1800 warmup bars before its first trustworthy value, which needs a ' +
      'window of 3650 bars. A preview reads at most 1200.';
    const hint = 'Reduce the lookback on this block or on one feeding it, or run a backtest.';
    answerPreviewWith(null, {
      reject: {
        status: 422,
        data: { detail: { error: 'PREVIEW_WARMUP_EXCEEDS_WINDOW', message, hint } },
      },
    });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    const panel = await screen.findByTestId('preview-error');
    expect(panel.dataset.code).toBe('PREVIEW_WARMUP_EXCEEDS_WINDOW');
    expect(panel.textContent).toContain(message);
    expect(screen.getByTestId('preview-error-hint').textContent).toBe(hint);
    // A refusal about this graph is not retried by pressing again.
    expect(screen.queryByTestId('preview-retry')).toBeNull();
    // And no numbers are left on screen beside the refusal.
    expect(screen.queryByTestId('preview-body')).toBeNull();
  });

  it('offers a retry for a transport failure, which is the kind that can succeed', async () => {
    answerPreviewWith(null, { reject: { status: 503, data: {} } });
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();

    await screen.findByTestId('preview-error');
    expect(screen.getByTestId('preview-retry')).toBeTruthy();
  });

  it('explains an unsaved strategy instead of only disabling the control', async () => {
    renderBuilder(UNSAVED_STRATEGY);
    await registryReady();
    await selectNode('n-ema');

    const notice = screen.getByTestId('preview-unavailable');
    expect(notice.dataset.code).toBe('PREVIEW_STRATEGY_UNSAVED');
    expect(notice.textContent).toMatch(/Save this strategy first/);
    expect(screen.getByTestId('preview-run').disabled).toBe(true);

    pressPreview();
    expect(previewCalls).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 5. A preview belongs to one node and one graph
// ---------------------------------------------------------------------------

describe('a held preview is dropped when it stops applying', () => {
  it('drops it when the author selects a different node', async () => {
    answerPreviewWith((url) => (url.includes('n-lag')
      ? lagPreview(['lag_1'], ['lag_1'])
      : EMA_PREVIEW));
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();
    await screen.findByTestId('preview-series-value');

    await selectNode('n-lag');

    // Not relabelled stale: an EMA series under the Lag block's name has no residual value.
    expect(screen.queryByTestId('preview-series-value')).toBeNull();
    expect(screen.getByTestId('preview-idle')).toBeTruthy();
    expect(screen.getByTestId('node-preview').dataset.state).toBe('idle');
  });

  it('drops it when the graph is edited', async () => {
    renderBuilder();
    await registryReady();
    await selectNode('n-ema');
    pressPreview();
    await screen.findByTestId('preview-series-value');

    // A semantic edit: a new block on the canvas. The preview described the previous graph.
    fireEvent.drop(screen.getByTestId('canvas'), {
      clientX: 200,
      clientY: 140,
      dataTransfer: { getData: (mime) => (mime === DRAG_BLOCK_ID_MIME ? 'ema' : '') },
    });

    await waitFor(() => expect(screen.queryByTestId('preview-series-value')).toBeNull());
  });
});
