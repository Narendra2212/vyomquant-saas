/**
 * Save-path tests for `src/pages/StrategyBuilder.jsx` (tasks 3.4 and 3.9, defect SB-06).
 *
 * The rule under test is a financial-safety rule, not a style preference. The pre-fix save
 * path did:
 *
 *     const sourceNode = serNodes.find((n) => n.type === 'ccxt_asset_feed');
 *     const pair = sourceNode?.params?.symbol || 'BTC/USDT';
 *     const timeframe = sourceNode?.params?.timeframe || '15m';
 *     …
 *     const payload = { …, exchange: 'binance', symbol: pair, timeframe };
 *
 * The palette never produced a node of type `ccxt_asset_feed`, so `sourceNode` was always
 * undefined and both fallbacks fired in normal operation: every saved strategy claimed to
 * trade BTC/USDT at 15m on Binance regardless of what the author configured.
 *
 * So: no `exchange` key on the payload, no `"binance"` / `"BTC/USDT"` / `"15m"` literal
 * anywhere on the save path, market identity read from the DATA node's own validated params,
 * and a refusal that names the node and the field when either is unset.
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

import {
  MARKET_IDENTITY_FIELDS,
  SaveValidationError,
  StrategyBuilderCanvas,
  buildSavePayload,
  resolveMarketIdentity,
} from '../../src/pages/StrategyBuilder';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { UndoRedoProvider } from '../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../src/contexts/ValidationContext';
import { toCanonical } from '../../src/lib/canonicalGraph';

// ---------------------------------------------------------------------------
// Registry fixture: a DATA block and an ACTION block, wired
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
  allowed_successor_categories: ['ACTION', 'INDICATOR'],
  execution_semantics: 'STATEFUL',
  runtime_ref: 'module:ohlcv_feed',
};

const MARKET_BUY = {
  block_id: 'market_buy',
  display_name: 'Market Buy',
  category: 'ACTION',
  description: 'Submits a market buy order.',
  inputs: [{ port: 'signal', type: 'OHLCV_FRAME', required: true }],
  outputs: [],
  params: [param('quantity', 'NUMBER', { required: true }), param('quantity_type', 'SELECT', { required: true, options: ['percent', 'base'] })],
  allowed_predecessor_categories: ['DATA', 'LOGIC'],
  allowed_successor_categories: [],
  execution_semantics: 'TERMINAL',
  runtime_ref: 'module:market_buy',
};

const registryPayload = {
  registry_version: 'r_save_0001',
  registry_schema_version: 1,
  port_types: ['OHLCV_FRAME', 'PRICE_SERIES', 'SCALAR_SERIES', 'BOOLEAN_SERIES', 'FEATURE_MATRIX', 'PREDICTION', 'SIGNAL', 'TRADE_INTENT', 'SCALAR'],
  categories: CATEGORIES,
  blocks: [OHLCV_FEED, MARKET_BUY],
  compatibility_matrix: { OHLCV_FRAME: ['OHLCV_FRAME'], SIGNAL: ['TRADE_INTENT'] },
};

const served = () => ({ status: 200, data: registryPayload, headers: { etag: '"r_save_0001"' } });

/** A canvas node carrying its descriptor, exactly as `createNodeFromDescriptor` builds one. */
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

/** A DATA → ACTION graph. `dataParams` decides whether the market is fully named. */
const graphFor = (dataParams) =>
  toCanonical(
    [
      canvasNode('n-data', OHLCV_FEED, dataParams),
      canvasNode('n-action', MARKET_BUY, { quantity: 5, quantity_type: 'percent' }),
    ],
    [
      {
        id: 'e-1',
        source: 'n-data',
        sourceHandle: 'candles',
        target: 'n-action',
        targetHandle: 'signal',
      },
    ],
    { name: 'Golden Strategy' },
  );

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const renderBuilder = (initialStrategy) =>
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

beforeEach(() => {
  global.ResizeObserver = ResizeObserverStub;
  window.localStorage.clear();
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  mockClient.get.mockResolvedValue(served());
  mockClient.post.mockResolvedValue({ status: 'compiled', dag_hash: 'h', execution_graph: {}, id: 'strategy-1' });
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. Market identity comes from the DATA node
// ---------------------------------------------------------------------------

describe('market identity is read from the DATA node, never substituted', () => {
  it('returns the DATA node symbol and timeframe', () => {
    const market = resolveMarketIdentity(graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }));
    expect(market).toEqual({ symbol: 'ETH/USDT', timeframe: '4h' });
  });

  it('names the node and the field when the symbol is unset', () => {
    let raised = null;
    try {
      resolveMarketIdentity(graphFor({ timeframe: '4h' }));
    } catch (error) {
      raised = error;
    }

    expect(raised).toBeInstanceOf(SaveValidationError);
    expect(raised.issues).toHaveLength(1);
    const [issue] = raised.issues;
    expect(issue.code).toBe('PARAM_REQUIRED_MISSING');
    expect(issue.severity).toBe('error');
    expect(issue.node_id).toBe('n-data');
    expect(issue.field).toBe('symbol');
    expect(issue.message).toContain('n-data');
    expect(issue.message).toContain('ohlcv_feed');
    expect(issue.fix_hint).not.toBe('');
    // The refusal is a refusal, not a substitution.
    expect(JSON.stringify(raised.issues)).not.toContain('BTC/USDT');
  });

  it('names the node and the field when the timeframe is unset', () => {
    expect(() => resolveMarketIdentity(graphFor({ symbol: 'ETH/USDT' }))).toThrowError(/timeframe/);
    try {
      resolveMarketIdentity(graphFor({ symbol: 'ETH/USDT' }));
    } catch (error) {
      expect(error.issues[0].field).toBe('timeframe');
      expect(error.issues[0].node_id).toBe('n-data');
      expect(JSON.stringify(error.issues)).not.toContain('15m');
    }
  });

  it('reports both fields at once, and an empty string is unset', () => {
    try {
      resolveMarketIdentity(graphFor({ symbol: '   ', timeframe: '' }));
      throw new Error('expected a refusal');
    } catch (error) {
      expect(error).toBeInstanceOf(SaveValidationError);
      expect(error.issues.map((issue) => issue.field).sort()).toEqual([...MARKET_IDENTITY_FIELDS].sort());
    }
  });

  it('refuses a graph with no DATA node instead of assuming a market', () => {
    const graph = toCanonical([canvasNode('n-action', MARKET_BUY, { quantity: 1, quantity_type: 'percent' })], []);
    try {
      resolveMarketIdentity(graph);
      throw new Error('expected a refusal');
    } catch (error) {
      expect(error).toBeInstanceOf(SaveValidationError);
      expect(error.issues[0].code).toBe('MISSING_REQUIRED_CATEGORY');
      expect(JSON.stringify(error.issues)).not.toContain('BTC/USDT');
    }
  });
});

// ---------------------------------------------------------------------------
// 2. The payload itself
// ---------------------------------------------------------------------------

describe('the save payload carries no exchange and no invented market', () => {
  it('holds the DATA node values and no exchange key', () => {
    const graph = graphFor({ symbol: 'SOL/USDT', timeframe: '1h' });
    const payload = buildSavePayload(graph, resolveMarketIdentity(graph), { name: 'Golden Strategy' });

    expect(payload).not.toHaveProperty('exchange');
    expect(payload).not.toHaveProperty('exchange_id');
    expect(payload.symbol).toBe('SOL/USDT');
    expect(payload.timeframe).toBe('1h');
    expect(payload.graph_json.schema_version).toBe(2);
    expect(payload.nodes.map((node) => node.block_id)).toEqual(['ohlcv_feed', 'market_buy']);

    const serialized = JSON.stringify(payload);
    for (const literal of ['binance', 'BTC/USDT', '15m', 'ccxt_asset_feed']) {
      expect(serialized).not.toContain(literal);
    }
  });

  it('emits every node of every category, DATA included (SB-05)', () => {
    const graph = graphFor({ symbol: 'SOL/USDT', timeframe: '1h' });
    const payload = buildSavePayload(graph, resolveMarketIdentity(graph), { name: 'Golden Strategy' });
    // The old serializer's type filter dropped DATA nodes outright, so the saved strategy had
    // no market at all and the fallback filled one in.
    expect(payload.nodes.filter((node) => node.category === 'DATA')).toHaveLength(1);
    expect(payload.edges[0]).toMatchObject({ source_port: 'candles', target_port: 'signal' });
  });
});

// ---------------------------------------------------------------------------
// 3. Through the page: the request the backend actually receives
// ---------------------------------------------------------------------------

describe('saving from the page', () => {
  it('sends the DATA node market and no exchange identity', async () => {
    renderBuilder({ id: null, name: 'Golden Strategy', graph_json: graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }) });

    await waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));

    const save = await screen.findByRole('button', { name: 'Save' });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);

    await waitFor(() =>
      expect(mockClient.post.mock.calls.some(([url]) => url === '/api/strategies')).toBe(true),
    );

    const [, compileBody] = mockClient.post.mock.calls.find(([url]) => url === '/api/strategies/compile');
    expect(compileBody.blueprint.schema_version).toBe(2);
    expect(compileBody.blueprint.nodes).toHaveLength(2);

    const [, saveBody] = mockClient.post.mock.calls.find(([url]) => url === '/api/strategies');
    expect(saveBody).not.toHaveProperty('exchange');
    expect(saveBody.symbol).toBe('ETH/USDT');
    expect(saveBody.timeframe).toBe('4h');

    const everySentBody = JSON.stringify(mockClient.post.mock.calls);
    for (const literal of ['binance', 'BTC/USDT', '"15m"', 'ccxt_asset_feed']) {
      expect(everySentBody).not.toContain(literal);
    }
  });

  it('refuses a save whose DATA node has no symbol, naming the node and the field', async () => {
    renderBuilder({ id: null, name: 'Half configured', graph_json: graphFor({ timeframe: '4h' }) });

    await waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));

    const save = await screen.findByRole('button', { name: 'Save' });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);

    const panel = await screen.findByTestId('save-issues');
    const named = [...panel.querySelectorAll('[data-node-id]')];
    expect(named.length).toBeGreaterThan(0);
    expect(named.some((row) => row.dataset.nodeId === 'n-data' && row.dataset.field === 'symbol')).toBe(true);
    expect(panel.textContent).toContain('n-data');
    expect(panel.textContent).not.toContain('BTC/USDT');

    // Nothing was compiled and nothing was saved.
    //
    // Asserted per endpoint rather than as `not.toHaveBeenCalled()`: since task 3.10 the page
    // also posts to `/api/strategies/validate` 400 ms after the last edit, and it *should* —
    // the backend is authoritative about validity, and asking it is not compiling or saving.
    // The rule under test is that the refused save writes nothing.
    const posted = mockClient.post.mock.calls.map(([url]) => url);
    expect(posted).not.toContain('/api/strategies/compile');
    expect(posted).not.toContain('/api/strategies');
    expect(screen.getByTestId('save-state').textContent).toMatch(/refused/i);
  });

  it('counts the unset required parameters in the status strip', async () => {
    renderBuilder({ id: null, name: 'Half configured', graph_json: graphFor({ symbol: 'ETH/USDT' }) });

    await waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));
    await waitFor(() =>
      expect(screen.getByTestId('blocking-count').textContent).toContain('1 required parameter'),
    );
  });
});

/*
 * LAUNCH AUDIT — a server refusal of the save is SHOWN, not swallowed.
 * Production: an account over its plan's strategy limit clicked Save and nothing visible
 * happened; the 403 reason only reached a status-line string. These reject the way `apiClient`
 * really does — an `ApiError`-shaped object with `status` and `data`, and no `.response`.
 */
describe('server refusals of the save are visible', () => {
  const apiError = (status, detail) =>
    Object.assign(new Error('Request failed'), { name: 'ApiError', status, data: { detail } });

  const rejectSaveWith = (error) => {
    mockClient.post.mockImplementation((url) =>
      url === '/api/strategies'
        ? Promise.reject(error)
        : Promise.resolve({ status: 'compiled', dag_hash: 'h', execution_graph: {} }),
    );
  };

  const clickSave = async () => {
    await waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));
    const save = await screen.findByRole('button', { name: 'Save' });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);
  };

  it('a plan-limit refusal renders the upgrade prompt with the server reason', async () => {
    rejectSaveWith(apiError(403, {
      code: 'QUOTA_EXCEEDED',
      message: 'Your Free plan allows 1 strategy. You have 27.',
      upgrade_message: 'Trader lets you run up to 3 strategies.',
      required_plan: 'trader',
      cta_label: 'Upgrade to Trader',
      current: 27,
      limit: 1,
    }));
    renderBuilder({ id: null, name: 'Over limit', graph_json: graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }) });
    await clickSave();

    const prompt = await screen.findByTestId('save-refusal');
    expect(prompt.textContent).toContain('Your Free plan allows 1 strategy');
    expect(prompt.textContent).toContain('Upgrade to Trader');
    expect(screen.getByTestId('save-state').textContent).toMatch(/refused/i);
  });

  it('a server graph refusal (422) lists each issue in the not-saved panel', async () => {
    rejectSaveWith(apiError(422, {
      error: 'STRATEGY_GRAPH_INVALID',
      message: '1 error',
      errors: [{
        code: 'EDGE_TYPE_MISMATCH', severity: 'error', node_id: 'n-data', edge_id: null,
        field: 'close', message: 'Port types do not match.', fix_hint: 'Connect a PRICE_SERIES.',
      }],
    }));
    renderBuilder({ id: null, name: 'Bad graph', graph_json: graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }) });
    await clickSave();

    const panel = await screen.findByTestId('save-issues');
    expect(panel.textContent).toContain('Port types do not match.');
    expect(panel.querySelector('[data-code="EDGE_TYPE_MISMATCH"]')).not.toBeNull();
  });

  it('any other failure states the server sentence in the status strip', async () => {
    rejectSaveWith(apiError(500, 'Strategy storage is temporarily unavailable.'));
    renderBuilder({ id: null, name: 'Outage', graph_json: graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }) });
    await clickSave();

    await waitFor(() =>
      expect(screen.getByTestId('save-state').textContent).toContain('Strategy storage is temporarily unavailable.'),
    );
  });
});

describe('after a successful save the header says so', () => {
  it('says Not saved yet before any save', async () => {
    renderBuilder({ id: null, name: 'Fresh', graph_json: graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }) });
    await waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));
    expect(screen.getByTestId('builder-save-chip').textContent).toBe('Not saved yet');
  });

  it('shows the saved version and enables Backtest and Deploy', async () => {
    // The shape POST /api/strategies really answers: the strategy row with the version fields
    // appended (`created.update(version_result)` in routers/strategies.py).
    mockClient.post.mockImplementation((url) =>
      url === '/api/strategies'
        ? Promise.resolve({ id: 'strategy-1', name: 'Saved one', version_saved: true, version: 'v1.0', version_id: 'ver-1' })
        : Promise.resolve({ status: 'compiled', dag_hash: 'h', execution_graph: {} }),
    );
    renderBuilder({ id: null, name: 'Saved one', graph_json: graphFor({ symbol: 'ETH/USDT', timeframe: '4h' }) });
    await waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));
    const save = await screen.findByRole('button', { name: 'Save' });
    await waitFor(() => expect(save.disabled).toBe(false));
    fireEvent.click(save);

    await waitFor(() => expect(screen.getByTestId('save-state').textContent).toMatch(/Saved/));
    await waitFor(() => expect(document.body.textContent).toContain('Version v1.0'));
    // The save state is stated in words beside the name, not only in a toast that vanishes.
    expect(screen.getByTestId('builder-save-chip').textContent).toBe('Saved');
    expect(screen.getByTestId('builder-save-chip').dataset.saveState).toBe('saved');
    expect(document.body.textContent).not.toContain('Save this strategy before backtesting');
    expect(document.body.textContent).not.toContain('Save this strategy before deploying');
  });
});
