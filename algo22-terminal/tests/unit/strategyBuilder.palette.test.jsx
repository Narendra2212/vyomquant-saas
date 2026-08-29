/**
 * Palette tests for `src/pages/StrategyBuilder.jsx` (task 3.6).
 *
 * What is under test is the contract the palette owes the author:
 *
 * * every served category renders, in `categories[].order` — all seven, so
 *   FEATURE_ENGINEERING can never be an empty section again (SB-03, Requirement 4.11);
 * * search matches display name, block id, description and category, across every
 *   category rather than only indicators/ML/DL (Requirement 4.13);
 * * each entry shows its input and output port types (Requirement 4.14);
 * * a registry failure renders an explicit error panel with a focusable retry button and
 *   **zero** block entries — never a bundled or cached-forever list (Requirement 4.12);
 * * a node created from the palette carries `data.block_id` and `data.category`, so
 *   `toCanonical` succeeds. That is the join between tasks 3.6 and 3.4: without the
 *   descriptor on the node, serialization raises `BLOCK_ID_MISSING`.
 *
 * The axios instance is stubbed because it is the network boundary. Everything else —
 * `registryClient`, the palette, `canonicalGraph` — is the real implementation.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ReactFlowProvider } from 'reactflow';

const { mockClient } = vi.hoisted(() => ({ mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: vi.fn(),
  patch: vi.fn(),
}));

import { StrategyBuilderCanvas, createNodeFromDescriptor, defaultParamsFor, matchesPaletteQuery } from '../../src/pages/StrategyBuilder';
import { loadRegistry, resetRegistryClient, getDescriptor } from '../../src/lib/registryClient';
import { UndoRedoProvider } from '../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../src/contexts/ValidationContext';
import { toCanonical } from '../../src/lib/canonicalGraph';

// ---------------------------------------------------------------------------
// Fixture: the wire shape `BlockRegistry.to_dict()` serves, all seven categories
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

const descriptor = (blockId, category, extra = {}) => ({
  block_id: blockId,
  display_name: blockId.toUpperCase(),
  category,
  description: `${blockId} in ${category}`,
  inputs: [],
  outputs: [],
  params: [],
  allowed_predecessor_categories: [],
  allowed_successor_categories: [],
  execution_semantics: 'STATELESS',
  runtime_ref: `module:${blockId}`,
  ...extra,
});

const OHLCV_FEED = descriptor('ohlcv_feed', 'DATA', {
  display_name: 'OHLCV Feed',
  description: 'Validated candles for one market and timeframe.',
  outputs: [{ port: 'candles', type: 'OHLCV_FRAME' }],
  allowed_successor_categories: ['INDICATOR', 'FEATURE_ENGINEERING', 'MATH', 'ML_DL'],
  params: [
    { key: 'symbol', label: 'Symbol', type: 'SYMBOL', required: true, default: null, example: 'BTC/USDT', help: '', options: null, min: null, max: null, step: null, unit: null, depends_on: [], affects_warmup: false },
    { key: 'timeframe', label: 'Timeframe', type: 'TIMEFRAME', required: true, default: null, example: '15m', help: '', options: null, min: null, max: null, step: null, unit: null, depends_on: [], affects_warmup: false },
    { key: 'market_type', label: 'Market type', type: 'SELECT', required: true, default: 'spot', options: ['spot', 'swap'], example: 'spot', help: '', min: null, max: null, step: null, unit: null, depends_on: [], affects_warmup: false },
  ],
});

const BLOCKS = [
  OHLCV_FEED,
  descriptor('sma', 'INDICATOR', {
    display_name: 'SMA',
    description: 'Simple moving average of a price series.',
    inputs: [{ port: 'series', type: 'PRICE_SERIES', required: true }],
    outputs: [{ port: 'value', type: 'SCALAR_SERIES' }],
  }),
  descriptor('wma', 'INDICATOR', { description: 'Weighted moving average.' }),
  descriptor('hma', 'INDICATOR', { description: 'Hull moving average.' }),
  descriptor('add', 'MATH', { description: 'Adds two series together.' }),
  descriptor('compare', 'LOGIC', { description: 'Compares two series.' }),
  descriptor('rolling_zscore', 'FEATURE_ENGINEERING', { description: 'Rolling z-score of a series.' }),
  descriptor('rolling_mean', 'FEATURE_ENGINEERING', { description: 'Rolling mean feature column.' }),
  descriptor('catboost', 'ML_DL', { description: 'Gradient boosting classifier.' }),
  descriptor('autoencoder', 'ML_DL', { description: 'Reconstruction-error anomaly model.' }),
  descriptor('market_buy', 'ACTION', { description: 'Submits a market buy order.', execution_semantics: 'TERMINAL' }),
];

const payload = () => ({
  registry_version: 'r_test_0001',
  registry_schema_version: 1,
  port_types: [
    'OHLCV_FRAME', 'PRICE_SERIES', 'SCALAR_SERIES', 'BOOLEAN_SERIES', 'FEATURE_MATRIX',
    'PREDICTION', 'SIGNAL', 'TRADE_INTENT', 'SCALAR',
  ],
  categories: CATEGORIES.map((category) => ({ ...category })),
  blocks: BLOCKS.map((block) => ({ ...block })),
  compatibility_matrix: {
    OHLCV_FRAME: ['OHLCV_FRAME', 'PRICE_SERIES'],
    PRICE_SERIES: ['PRICE_SERIES', 'SCALAR_SERIES'],
    SCALAR_SERIES: ['SCALAR_SERIES'],
    BOOLEAN_SERIES: ['BOOLEAN_SERIES', 'SIGNAL'],
    FEATURE_MATRIX: ['FEATURE_MATRIX'],
    PREDICTION: ['PREDICTION', 'SCALAR_SERIES'],
    SIGNAL: ['SIGNAL', 'TRADE_INTENT'],
    TRADE_INTENT: [],
    SCALAR: ['SCALAR', 'SCALAR_SERIES'],
  },
});

const served = () => ({ status: 200, data: payload(), headers: { etag: '"r_test_0001"' } });

const failed = (status) => {
  const error = new Error(`Request failed with status code ${status}`);
  error.status = status;
  error.response = { status, data: { detail: 'boom' } };
  return error;
};

// reactflow measures its container; jsdom has no ResizeObserver.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const renderBuilder = () =>
  render(
    <MemoryRouter>
      <ReactFlowProvider>
        <UndoRedoProvider>
          <ValidationProvider>
            <StrategyBuilderCanvas />
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
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. All seven categories, in order
// ---------------------------------------------------------------------------

describe('the palette renders the registry, all seven categories in order', () => {
  it('renders every served category in categories[].order', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();

    await waitFor(() => expect(screen.getAllByTestId('palette-category').length).toBe(7));

    const rendered = screen.getAllByTestId('palette-category');
    expect(rendered.map((section) => section.dataset.categoryId)).toEqual([
      'DATA',
      'INDICATOR',
      'MATH',
      'LOGIC',
      'FEATURE_ENGINEERING',
      'ML_DL',
      'ACTION',
    ]);
    // Rendered in the served order, and the served order is ascending.
    expect(rendered.map((section) => Number(section.dataset.categoryOrder))).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it('populates FEATURE_ENGINEERING and offers the SB-04 blocks', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();

    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));

    const ids = screen.getAllByTestId('palette-block').map((entry) => entry.dataset.blockId);
    // SB-03: the section is no longer permanently empty.
    expect(ids).toContain('rolling_zscore');
    expect(ids).toContain('rolling_mean');
    // SB-04: blocks the engine implements and the old palette could not offer.
    for (const blockId of ['wma', 'hma', 'catboost', 'autoencoder']) {
      expect(ids).toContain(blockId);
    }
  });

  it('shows each entry input and output port types as chips', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();

    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));

    const sma = screen.getAllByTestId('palette-block').find((entry) => entry.dataset.blockId === 'sma');
    const chips = [...sma.querySelectorAll('[data-testid="port-chip"]')];
    expect(chips).toHaveLength(2);

    const input = chips.find((chip) => chip.dataset.portDirection === 'in');
    const output = chips.find((chip) => chip.dataset.portDirection === 'out');
    // The type is in the text, not only in a colour, and the port name is in the title.
    expect(input.textContent).toContain('PRICE_SERIES');
    expect(input.getAttribute('title')).toContain('series');
    expect(output.textContent).toContain('SCALAR_SERIES');
    expect(output.getAttribute('title')).toContain('value');
  });
});

// ---------------------------------------------------------------------------
// 2. Search, across every category
// ---------------------------------------------------------------------------

describe('palette search matches name, block id, description and category', () => {
  it('is a labelled input', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();

    const input = await screen.findByLabelText('Search blocks');
    expect(input.tagName).toBe('INPUT');
    expect(input.id).toBe('palette-search');
  });

  it('matches on block id in a category the old search never looked at', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();
    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));

    fireEvent.change(screen.getByLabelText('Search blocks'), { target: { value: 'rolling_zscore' } });

    const matches = screen.getAllByTestId('palette-block');
    expect(matches).toHaveLength(1);
    expect(matches[0].dataset.blockId).toBe('rolling_zscore');
    expect(matches[0].dataset.category).toBe('FEATURE_ENGINEERING');
  });

  it('matches on description text', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();
    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));

    fireEvent.change(screen.getByLabelText('Search blocks'), { target: { value: 'anomaly' } });

    const matches = screen.getAllByTestId('palette-block');
    expect(matches).toHaveLength(1);
    expect(matches[0].dataset.blockId).toBe('autoencoder');
  });

  it('matches on category, and reaches DATA, MATH and ACTION as readily as INDICATOR', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();
    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));

    const search = screen.getByLabelText('Search blocks');
    for (const [query, expected] of [
      ['feature engineering', ['rolling_zscore', 'rolling_mean']],
      ['ACTION', ['market_buy']],
      ['MATH', ['add']],
      ['DATA', ['ohlcv_feed']],
    ]) {
      fireEvent.change(search, { target: { value: query } });
      expect(screen.getAllByTestId('palette-block').map((entry) => entry.dataset.blockId)).toEqual(expected);
    }
  });

  it('is answered by one pure matcher, so the four fields cannot drift apart', () => {
    const block = descriptor('feat_lag', 'FEATURE_ENGINEERING', {
      display_name: 'Lagged value',
      description: 'Shifts a column backwards in time.',
    });
    expect(matchesPaletteQuery(block, 'lagged')).toBe(true);
    expect(matchesPaletteQuery(block, 'feat_lag')).toBe(true);
    expect(matchesPaletteQuery(block, 'backwards')).toBe(true);
    expect(matchesPaletteQuery(block, 'FEATURE_ENGINEERING')).toBe(true);
    expect(matchesPaletteQuery(block, 'Feature Engineering', 'Feature Engineering')).toBe(true);
    expect(matchesPaletteQuery(block, 'catboost')).toBe(false);
    expect(matchesPaletteQuery(block, '')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. Fail closed (Requirement 4.12)
// ---------------------------------------------------------------------------

describe('a registry failure renders an error panel and zero blocks', () => {
  it('renders the error panel with a real retry button and no block entries on a 500', async () => {
    mockClient.get.mockRejectedValue(failed(500));
    renderBuilder();

    const panel = await screen.findByTestId('palette-error');
    expect(panel.getAttribute('role')).toBe('alert');
    expect(panel.dataset.errorCode).toBe('REGISTRY_UNAVAILABLE');

    // Zero block entries. Not a bundled list, not a stale one.
    expect(screen.queryAllByTestId('palette-block')).toHaveLength(0);
    expect(screen.queryAllByTestId('palette-category')).toHaveLength(0);

    const retry = screen.getByTestId('palette-retry');
    expect(retry.tagName).toBe('BUTTON');
    expect(retry.getAttribute('type')).toBe('button');
    expect(retry.disabled).toBe(false);
    retry.focus();
    expect(document.activeElement).toBe(retry);
  });

  it('retries for real, and populates once the registry answers', async () => {
    mockClient.get.mockRejectedValueOnce(failed(503));
    renderBuilder();

    await screen.findByTestId('palette-error');
    expect(screen.queryAllByTestId('palette-block')).toHaveLength(0);

    mockClient.get.mockResolvedValueOnce(served());
    fireEvent.click(screen.getByTestId('palette-retry'));

    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));
    expect(screen.queryByTestId('palette-error')).toBeNull();
    expect(mockClient.get).toHaveBeenCalledTimes(2);
  });

  it('separates an expired session from a broken registry', async () => {
    mockClient.get.mockRejectedValue(failed(401));
    renderBuilder();

    const panel = await screen.findByTestId('palette-error');
    expect(panel.dataset.errorCode).toBe('REGISTRY_UNAUTHENTICATED');
    expect(panel.textContent).toMatch(/signed in/i);
    expect(screen.queryAllByTestId('palette-block')).toHaveLength(0);
  });

  it('shows zero blocks for a 200 that carries an empty catalogue', async () => {
    mockClient.get.mockResolvedValue({ status: 200, data: { ...payload(), blocks: [] }, headers: {} });
    renderBuilder();

    const panel = await screen.findByTestId('palette-error');
    expect(panel.dataset.errorCode).toBe('REGISTRY_EMPTY');
    expect(screen.queryAllByTestId('palette-block')).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 4. The join with task 3.4: a created node can be serialized
// ---------------------------------------------------------------------------

describe('a node created from the palette carries its registry descriptor', () => {
  it('records block_id and category, so toCanonical succeeds', async () => {
    mockClient.get.mockResolvedValue(served());
    await loadRegistry();

    const node = createNodeFromDescriptor(getDescriptor('sma'), { id: 'n-1', position: { x: 10, y: 20 } });

    expect(node.data.block_id).toBe('sma');
    expect(node.data.category).toBe('INDICATOR');
    expect(node.data.inputs).toEqual([{ port: 'series', type: 'PRICE_SERIES', required: true }]);
    expect(node.data.outputs).toEqual([{ port: 'value', type: 'SCALAR_SERIES' }]);

    const graph = toCanonical([node], []);
    expect(graph.nodes).toHaveLength(1);
    expect(graph.nodes[0].block_id).toBe('sma');
    expect(graph.nodes[0].category).toBe('INDICATOR');
    expect(graph.nodes[0].ui.position).toEqual({ x: 10, y: 20 });
  });

  it('would raise BLOCK_ID_MISSING for the node the old palette produced', () => {
    // The pre-fix handleDrop built `{ id, type, position, data: { label, params } }` — the
    // block key on `node.type` and no descriptor anywhere. This is why 3.6 and 3.4 are one
    // change: the save path cannot be re-pointed before the palette stamps the descriptor.
    const legacyNode = { id: 'n-1', type: 'sma', position: { x: 0, y: 0 }, data: { label: 'SMA', params: {} } };
    expect(() => toCanonical([legacyNode], [])).toThrowError(/block_id/);
  });

  it('copies benign defaults and never prefills a behaviour-changing parameter', () => {
    const params = defaultParamsFor(OHLCV_FEED);
    // A declared default is shown...
    expect(params.market_type).toBe('spot');
    // ...but symbol and timeframe are published with no default on purpose (Req 5.4), and
    // a prefilled symbol is a trade on a market nobody chose (SB-06).
    expect(params).not.toHaveProperty('symbol');
    expect(params).not.toHaveProperty('timeframe');
    expect(JSON.stringify(params)).not.toContain('BTC/USDT');
    expect(JSON.stringify(params)).not.toContain('15m');
  });
});

// ---------------------------------------------------------------------------
// 5. The deprecated endpoint is unreachable
// ---------------------------------------------------------------------------

describe('the deprecated block endpoint is no longer called', () => {
  it('requests the registry endpoint and never /api/strategies/blocks', async () => {
    mockClient.get.mockResolvedValue(served());
    renderBuilder();

    await waitFor(() => expect(screen.getAllByTestId('palette-block').length).toBe(BLOCKS.length));

    const requested = mockClient.get.mock.calls.map((call) => call[0]);
    expect(requested).toContain('/api/strategy-operations/registry/blocks');
    expect(requested.some((url) => String(url).includes('/api/strategies/blocks'))).toBe(false);
  });

  it('offers no api-layer method that could reach the alias', async () => {
    const { strategiesApi } = await import('../../src/api/modules/strategies');
    expect(strategiesApi.getBlocks).toBeUndefined();
    expect(typeof strategiesApi.compile).toBe('function');
  });
});
