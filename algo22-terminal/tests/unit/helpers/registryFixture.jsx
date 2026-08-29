/**
 * registryFixture.jsx — the shared harness for the two palette regression suites
 * (`palette.antifallback.test.jsx`, task 3.12; `palette.categories.test.jsx`, task 3.13).
 *
 * It exists so both suites drive the palette through the *same* boundary as the rest of the
 * builder tests: the axios instance is the only stub, and `registryClient`, the palette, the
 * canvas and `canonicalGraph` are the shipped code. The pieces here are the ones
 * `strategyBuilder.validation.test.jsx` established — the `registry-state` cell, the
 * `ResizeObserver` and `DragEvent` stubs, the
 * `MemoryRouter`/`ReactFlowProvider`/`UndoRedoProvider`/`ValidationProvider` wrapper — lifted
 * once instead of retyped twice.
 *
 * `vi.mock('../../src/apiClient')` stays in each test file: the factory is hoisted above the
 * imports of the file that declares it, and the mock applies to that file's whole module
 * graph, so a helper imported here still sees the stub.
 *
 * The block catalogue below is the **real** one. It was read from the running backend
 * (`build_registry().to_dict()`), which serves 100 blocks: DATA 3, INDICATOR 33, MATH 18,
 * LOGIC 14, FEATURE_ENGINEERING 15, ML_DL 8, ACTION 9. Faithful ids matter for these two
 * suites specifically: SB-04 was a *drift* defect, and a fixture of invented names could not
 * show that the palette now offers exactly what the engine can run.
 *
 * Nothing here implements production behaviour. It is wire data plus two DOM stubs jsdom
 * lacks.
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ReactFlowProvider } from 'reactflow';

import { DRAG_BLOCK_ID_MIME, StrategyBuilderCanvas } from '../../../src/pages/StrategyBuilder';
import { UndoRedoProvider } from '../../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../../src/contexts/ValidationContext';

// ---------------------------------------------------------------------------
// The served vocabulary
// ---------------------------------------------------------------------------

/** `categories[]` as the backend serves it: ascending, gapless, all seven. */
export const CATEGORIES = Object.freeze([
  { id: 'DATA', display_name: 'Market Data', order: 1 },
  { id: 'INDICATOR', display_name: 'Indicators', order: 2 },
  { id: 'MATH', display_name: 'Math', order: 3 },
  { id: 'LOGIC', display_name: 'Logic', order: 4 },
  { id: 'FEATURE_ENGINEERING', display_name: 'Feature Engineering', order: 5 },
  { id: 'ML_DL', display_name: 'ML / DL Models', order: 6 },
  { id: 'ACTION', display_name: 'Actions', order: 7 },
]);

export const PORT_TYPES = Object.freeze([
  'OHLCV_FRAME',
  'PRICE_SERIES',
  'SCALAR_SERIES',
  'BOOLEAN_SERIES',
  'FEATURE_MATRIX',
  'PREDICTION',
  'SIGNAL',
  'TRADE_INTENT',
  'SCALAR',
]);

export const COMPATIBILITY_MATRIX = Object.freeze({
  OHLCV_FRAME: ['OHLCV_FRAME', 'PRICE_SERIES', 'FEATURE_MATRIX'],
  PRICE_SERIES: ['PRICE_SERIES', 'SCALAR_SERIES'],
  SCALAR_SERIES: ['SCALAR_SERIES', 'BOOLEAN_SERIES'],
  BOOLEAN_SERIES: ['BOOLEAN_SERIES', 'SIGNAL'],
  FEATURE_MATRIX: ['FEATURE_MATRIX'],
  PREDICTION: ['PREDICTION', 'SCALAR_SERIES'],
  SIGNAL: ['SIGNAL', 'TRADE_INTENT'],
  TRADE_INTENT: [],
  SCALAR: ['SCALAR', 'SCALAR_SERIES'],
});

/**
 * Every block the backend registry publishes, by category, in served order.
 *
 * Read from `build_registry().to_dict()`. The counts are the contract Requirement 4.2
 * (every category populated) and Requirement 4.3 (at least 15 FEATURE_ENGINEERING blocks)
 * are stated in, and `wma`, `hma`, `catboost` and `autoencoder` are the four blocks
 * SB-04 made unselectable.
 */
export const REGISTRY_BLOCK_IDS = Object.freeze({
  DATA: Object.freeze(['ohlcv_feed', 'live_ticker', 'orderbook_imbalance']),
  INDICATOR: Object.freeze([
    'sma', 'ema', 'wma', 'hma', 'rsi', 'macd', 'atr', 'bollinger_bands', 'stochastic', 'cci',
    'williams_r', 'obv', 'mfi', 'adx', 'supertrend', 'trix', 'vortex_indicator',
    'choppiness_index', 'awesome_oscillator', 'fisher_transform', 'rolling_z_score',
    'historical_volatility', 'rolling_vwap', 'momentum', 'roc', 'donchian_channel',
    'keltner_channels', 'ichimoku_cloud', 'cmf', 'psar', 'fibonacci_rolling',
    'pivot_standard', 'pivot_camarilla',
  ]),
  MATH: Object.freeze([
    'add', 'subtract', 'multiply', 'divide', 'modulo', 'min', 'max', 'abs', 'round', 'floor',
    'ceil', 'sqrt', 'log', 'exp', 'negate', 'clamp', 'constant', 'shift',
  ]),
  LOGIC: Object.freeze([
    'and', 'or', 'not', 'gt', 'lt', 'gte', 'lte', 'eq', 'neq', 'cross_above', 'cross_below',
    'between', 'if_then_else', 'to_signal',
  ]),
  FEATURE_ENGINEERING: Object.freeze([
    'feat_lag', 'feat_returns', 'feat_log_returns', 'feat_rolling_mean', 'feat_rolling_std',
    'feat_volatility', 'feat_momentum', 'feat_zscore', 'feat_normalize', 'feat_standardize',
    'feat_time', 'feat_volume', 'feat_price_transform', 'feat_concat', 'feat_select',
  ]),
  ML_DL: Object.freeze([
    'xgboost', 'lightgbm', 'random_forest', 'catboost', 'lstm', 'gru', 'transformer',
    'autoencoder',
  ]),
  ACTION: Object.freeze([
    'action_buy_market', 'action_sell_market', 'action_close_position', 'action_buy_limit',
    'action_sell_limit', 'action_stop_market', 'action_stop_limit',
    'action_take_profit_market', 'action_take_profit_limit',
  ]),
});

/** All 100 ids, in category order. */
export const ALL_BLOCK_IDS = Object.freeze(
  CATEGORIES.flatMap((category) => REGISTRY_BLOCK_IDS[category.id]),
);

// ---------------------------------------------------------------------------
// Descriptor construction
// ---------------------------------------------------------------------------

/** A full `ParamSpec`: every field the schema declares, so no form path reads `undefined`. */
export const param = (key, type, extra = {}) => ({
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

/**
 * Per-category port and parameter shape. Plausible rather than exhaustive: the palette reads
 * ports to draw chips and parameters to build the inspector form, and neither behaviour
 * depends on which indicator a descriptor names.
 */
const CATEGORY_SHAPE = {
  DATA: {
    inputs: [],
    outputs: [{ port: 'candles', type: 'OHLCV_FRAME' }],
    params: [
      param('symbol', 'SYMBOL', { required: true, example: 'BTC/USDT' }),
      param('timeframe', 'TIMEFRAME', { required: true, example: '15m' }),
    ],
    allowed_predecessor_categories: [],
    allowed_successor_categories: ['INDICATOR', 'MATH', 'FEATURE_ENGINEERING', 'ML_DL', 'ACTION'],
    execution_semantics: 'STATEFUL',
  },
  INDICATOR: {
    inputs: [{ port: 'series', type: 'PRICE_SERIES', required: true }],
    outputs: [{ port: 'value', type: 'SCALAR_SERIES' }],
    params: [param('window', 'INT', { default: 14, min: 2, max: 500, affects_warmup: true })],
    allowed_predecessor_categories: ['DATA'],
    allowed_successor_categories: ['MATH', 'LOGIC', 'FEATURE_ENGINEERING', 'ML_DL'],
    execution_semantics: 'STATEFUL',
  },
  MATH: {
    inputs: [
      { port: 'a', type: 'SCALAR_SERIES', required: true },
      { port: 'b', type: 'SCALAR_SERIES', required: false },
    ],
    outputs: [{ port: 'value', type: 'SCALAR_SERIES' }],
    params: [],
    allowed_predecessor_categories: ['DATA', 'INDICATOR', 'MATH', 'ML_DL'],
    allowed_successor_categories: ['MATH', 'LOGIC', 'ACTION'],
    execution_semantics: 'STATELESS',
  },
  LOGIC: {
    inputs: [
      { port: 'left', type: 'SCALAR_SERIES', required: true },
      { port: 'right', type: 'SCALAR_SERIES', required: true },
    ],
    outputs: [{ port: 'value', type: 'BOOLEAN_SERIES' }],
    params: [],
    allowed_predecessor_categories: ['INDICATOR', 'MATH', 'LOGIC', 'ML_DL'],
    allowed_successor_categories: ['LOGIC', 'ACTION'],
    execution_semantics: 'STATELESS',
  },
  FEATURE_ENGINEERING: {
    inputs: [{ port: 'frame', type: 'OHLCV_FRAME', required: true }],
    outputs: [{ port: 'features', type: 'FEATURE_MATRIX' }],
    params: [param('window', 'INT', { default: 20, min: 1, max: 500, affects_warmup: true })],
    allowed_predecessor_categories: ['DATA', 'INDICATOR', 'FEATURE_ENGINEERING'],
    allowed_successor_categories: ['FEATURE_ENGINEERING', 'ML_DL'],
    execution_semantics: 'STATEFUL',
  },
  ML_DL: {
    inputs: [{ port: 'features', type: 'FEATURE_MATRIX', required: true }],
    outputs: [{ port: 'prediction', type: 'PREDICTION' }],
    params: [
      param('confidence_threshold', 'NUMBER', { required: true, min: 0, max: 1, step: 0.01 }),
    ],
    allowed_predecessor_categories: ['FEATURE_ENGINEERING'],
    allowed_successor_categories: ['MATH', 'LOGIC', 'ACTION'],
    execution_semantics: 'STATEFUL',
  },
  ACTION: {
    inputs: [{ port: 'signal', type: 'SIGNAL', required: true }],
    outputs: [],
    params: [
      param('quantity', 'NUMBER', { required: true, min: 0 }),
      param('quantity_type', 'SELECT', { required: true, options: ['BASE', 'QUOTE', 'PERCENT'] }),
    ],
    allowed_predecessor_categories: ['LOGIC', 'ML_DL'],
    allowed_successor_categories: [],
    execution_semantics: 'TERMINAL',
  },
};

/** Turn an id into a readable display name: `rolling_z_score` → `Rolling Z Score`. */
const titleCase = (blockId) =>
  blockId
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');

/** One `BlockDescriptor`, in the wire shape `BlockRegistry.to_dict()` serves. */
export const descriptor = (blockId, category, extra = {}) => {
  const shape = CATEGORY_SHAPE[category];
  if (!shape) throw new Error(`registryFixture: no shape for category '${category}'`);
  return {
    block_id: blockId,
    display_name: titleCase(blockId),
    category,
    description: `${titleCase(blockId)} — served from the backend block registry.`,
    inputs: shape.inputs.map((port) => ({ ...port })),
    outputs: shape.outputs.map((port) => ({ ...port })),
    params: shape.params.map((spec) => ({ ...spec })),
    allowed_predecessor_categories: [...shape.allowed_predecessor_categories],
    allowed_successor_categories: [...shape.allowed_successor_categories],
    execution_semantics: shape.execution_semantics,
    runtime_ref: `module:${blockId}`,
    ...extra,
  };
};

/** Every published descriptor, in category order. 100 of them. */
export const allDescriptors = () =>
  CATEGORIES.flatMap((category) =>
    REGISTRY_BLOCK_IDS[category.id].map((blockId) => descriptor(blockId, category.id)),
  );

/**
 * A registry response.
 *
 * @param {{ blocks?: object[], categories?: object[], registryVersion?: string }} [options]
 */
export const registryPayload = ({
  blocks = allDescriptors(),
  categories = CATEGORIES,
  registryVersion = 'r_fixture_0001',
} = {}) => ({
  registry_version: registryVersion,
  registry_schema_version: 1,
  port_types: [...PORT_TYPES],
  categories: categories.map((category) => ({ ...category })),
  blocks: blocks.map((block) => ({ ...block })),
  compatibility_matrix: { ...COMPATIBILITY_MATRIX },
});

/** A `200` carrying `payload`, with the entity tag the backend sends (Requirement 4.15). */
export const served = (payload = registryPayload()) => ({
  status: 200,
  data: payload,
  headers: { etag: `"${payload.registry_version}"` },
});

/** The rejection axios produces for an HTTP failure. */
export const failed = (status, detail = 'registry assembly failed') => {
  const error = new Error(`Request failed with status code ${status}`);
  error.status = status;
  error.response = { status, data: { detail } };
  return error;
};

// ---------------------------------------------------------------------------
// DOM stubs jsdom does not provide
// ---------------------------------------------------------------------------

/** React Flow measures its container; jsdom implements no `ResizeObserver`. */
export class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

/**
 * jsdom implements no `DragEvent`, so `fireEvent.drop` would fall back to a plain `Event` and
 * lose `dataTransfer` along with `clientX` / `clientY`. This stub carries exactly what a real
 * drag carries and adds no behaviour.
 */
export class DragEventStub extends window.MouseEvent {
  constructor(type, init = {}) {
    super(type, init);
    this.dataTransfer = init.dataTransfer ?? null;
  }
}

/** Install the stubs and clear per-test browser state. Call from `beforeEach`. */
export const installBuilderStubs = () => {
  global.ResizeObserver = ResizeObserverStub;
  window.DragEvent = DragEventStub;
  window.localStorage.clear();
};

// ---------------------------------------------------------------------------
// Rendering and interaction
// ---------------------------------------------------------------------------

export const renderBuilder = (props = {}) =>
  render(
    <MemoryRouter>
      <ReactFlowProvider>
        <UndoRedoProvider>
          <ValidationProvider>
            <StrategyBuilderCanvas {...props} />
          </ValidationProvider>
        </UndoRedoProvider>
      </ReactFlowProvider>
    </MemoryRouter>,
  );

/** Wait until the status strip reports the registry in `state` (`ready`, `error`, …). */
export const waitForRegistry = (state) =>
  waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain(state), {
    timeout: 5000,
  });

/** Every rendered palette entry's `block_id`, in render order. */
export const renderedBlockIds = () =>
  screen.queryAllByTestId('palette-block').map((entry) => entry.dataset.blockId);

/** The palette entry for `blockId`, or `undefined`. */
export const paletteEntry = (blockId) =>
  screen.queryAllByTestId('palette-block').find((entry) => entry.dataset.blockId === blockId);

/**
 * Start a drag from a palette entry and return what it wrote to the drag payload.
 * This is the authoring gesture, not a simulated call into the drop handler.
 */
export const startDrag = (entry) => {
  const written = {};
  fireEvent.dragStart(entry, {
    dataTransfer: {
      setData: (mime, value) => {
        written[mime] = value;
      },
      effectAllowed: 'none',
    },
  });
  return written;
};

/** Drop `blockId` onto the canvas, the way a completed drag does. */
export const dropBlockId = (blockId) => {
  fireEvent.drop(screen.getByTestId('canvas'), {
    clientX: 140,
    clientY: 110,
    dataTransfer: { getData: (mime) => (mime === DRAG_BLOCK_ID_MIME ? blockId : '') },
  });
};

export { DRAG_BLOCK_ID_MIME };
