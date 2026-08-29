/**
 * Blocking-training-message tests for `src/lib/graphValidation.js` and
 * `src/pages/StrategyBuilder.jsx` (task 6.6, Requirement 14.9 — and Requirements 14.3,
 * 14.4, 14.6 and 16.3 for the quantities each reason carries).
 *
 * Requirement 14.9 reads: "THE Strategy_Builder SHALL display each blocking training message
 * with its required quantity and its available quantity." Two halves are under test:
 *
 * 1. **`deriveTrainingBlocks`** reads the quantities out of the payload the admission gate
 *    produced, per reason, and pairs them. It recomputes nothing — the minimum feature-column
 *    count, the minimum row count, the reserved fractions and the embargo are
 *    `ml_training_policy`'s arithmetic, the server has already done it, and a client that did
 *    it again would eventually disagree with the gate that actually decides. A block with no
 *    numeric requirement is still returned, because hiding a block is worse than rendering it
 *    without figures and inventing figures for it is worse than both.
 * 2. **The builder renders each one with BOTH numbers**, in one string so the two cannot be
 *    rendered apart, and the server's own sentence verbatim rather than a second wording of
 *    the same rule.
 *
 * The gold tone rather than red is deliberate and matches what happened: on a blocked path
 * the strategy IS saved and the version is a real immutable version; it is training that was
 * refused, and **no training job row exists** (Requirements 14.3, 14.4, 14.7, 14.8).
 *
 * The axios instance is the only stub. `graphValidation`, `registryClient`, `canonicalGraph`
 * and the real builder tree are all the shipped code.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
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
import { TRAINING_BLOCK_REASONS, deriveTrainingBlocks } from '../../src/lib/graphValidation';

// ---------------------------------------------------------------------------
// Payload fixtures — the shapes the backend actually produces
// ---------------------------------------------------------------------------

/** `schema.make_issue`: `expected` is the required quantity, `actual` the available one. */
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

/**
 * The save response's `training` field on a blocked path — `strategy_service`'s own shape:
 * `{ state: 'BLOCKED', required, job_id: null, jobs: [], ...TrainingBlocked.to_dict() }`,
 * whose `detail` is `GateVerdict.to_dict()`.
 *
 * `GateVerdict.message()`'s exact wording is used verbatim, including the thousands
 * separators, because the panel must render the server's sentence rather than a paraphrase.
 */
const ML_REQUIREMENTS_BLOCK = {
  state: 'BLOCKED',
  required: true,
  job_id: null,
  jobs: [],
  blocked: true,
  reason: 'ML_REQUIREMENTS',
  message:
    'Training cannot start. Required: 5 feature columns and 5,000 usable rows. ' +
    'Available: 3 feature columns and 1,240 rows.',
  detail: {
    ok: false,
    block_id: 'xgboost',
    node_id: 'n-model',
    required: { columns: 5, rows: 5000 },
    available: { columns: 3, rows: 1240 },
    issues: [
      issue({
        code: 'INSUFFICIENT_FEATURE_COLUMNS',
        node_id: 'n-model',
        field: 'usable_feature_columns',
        message: 'Training cannot start. Required: 5 feature columns. Available: 3.',
        expected: 5,
        actual: 3,
        fix_hint: "Add 2 more feature column(s) to the pipeline feeding 'XGBoost'.",
      }),
      issue({
        code: 'INSUFFICIENT_ROWS',
        node_id: 'n-model',
        field: 'usable_rows',
        message: 'Training cannot start. Required: 5,000 usable rows. Available: 1,240.',
        expected: 5000,
        actual: 1240,
        fix_hint: 'Widen the history range, use a shorter timeframe, or reduce the reserved validation and test fractions.',
      }),
    ],
    message:
      'Training cannot start. Required: 5 feature columns and 5,000 usable rows. ' +
      'Available: 3 feature columns and 1,240 rows.',
  },
};

/** A sequence model: Requirement 14.6 adds the training split and the window. */
const SEQUENCE_BLOCK = {
  state: 'BLOCKED',
  reason: 'ML_REQUIREMENTS',
  message: 'Training cannot start.',
  detail: {
    node_id: 'n-lstm',
    block_id: 'lstm',
    required: { columns: 5, rows: 5000, train_rows: 1160, sequence_length: 60 },
    available: { columns: 8, rows: 4000, train_rows: 800, sequence_length: 60 },
    issues: [],
  },
};

/** Requirement 16.3: the requested value together with the permitted value. */
const CAP_BLOCK = {
  error: 'TRAINING_BLOCKED',
  job_created: false,
  reason: 'CAP_EXCEEDED',
  message: 'Requested 500 epochs; this plan permits 100.',
  detail: { cap: 'epochs', unit: 'epochs', requested: 500, allowed: 100, issues: [] },
};

/** `training_bar_budget`'s own figures. */
const DATASET_BLOCK = {
  state: 'BLOCKED',
  reason: 'DATASET',
  message: 'This graph needs a longer window than one training fetch may request.',
  detail: { window: { target_bars: 42000, max_bars: 20000 }, issues: [] },
};

/** A reason that expresses no numeric threshold: rendered as the message plus its issues. */
const FEATURES_BLOCK = {
  state: 'BLOCKED',
  reason: 'FEATURES',
  message: 'The produced feature set failed the feature schema check.',
  detail: {
    node_id: 'n-model',
    issues: [
      issue({
        code: 'FEATURE_ALL_NAN',
        node_id: 'n-model',
        field: 'ema_20_lag_6',
        message: "Column 'ema_20_lag_6' is entirely empty over the training window.",
        fix_hint: 'Reduce the lag, or widen the history range.',
      }),
    ],
  },
};

// ---------------------------------------------------------------------------
// 1. The projection
// ---------------------------------------------------------------------------

describe('deriveTrainingBlocks reads the quantities the gate measured', () => {
  it('pairs both dimensions for ML_REQUIREMENTS, even though only one fell short', () => {
    // The gate returns both for exactly this reason, so the author is not told about one
    // half and left to discover the other on the next attempt.
    const [block] = deriveTrainingBlocks(ML_REQUIREMENTS_BLOCK);

    expect(block.reason).toBe(TRAINING_BLOCK_REASONS.ML_REQUIREMENTS);
    expect(block.nodeId).toBe('n-model');
    expect(block.blockId).toBe('xgboost');
    expect(block.quantities.map((q) => q.label)).toEqual(['feature columns', 'usable rows']);
    expect(block.quantities[0]).toMatchObject({ required: '5', available: '3' });
    expect(block.quantities[1]).toMatchObject({ required: '5,000', available: '1,240' });
  });

  it('renders the server sentence verbatim rather than a second wording of the rule', () => {
    const [block] = deriveTrainingBlocks(ML_REQUIREMENTS_BLOCK);

    expect(block.message).toBe(ML_REQUIREMENTS_BLOCK.message);
  });

  it('carries the sequence window and training split when the gate measured them', () => {
    const [block] = deriveTrainingBlocks(SEQUENCE_BLOCK);

    const labels = block.quantities.map((q) => q.label);
    expect(labels).toEqual(['feature columns', 'usable rows', 'training rows', 'sequence length']);
    // Requirement 14.6: the sequence length together with the available training row count.
    expect(block.quantities[2]).toMatchObject({ required: '1,160', available: '800' });
    expect(block.quantities[3]).toMatchObject({ required: '60', available: '60' });
  });

  it('pairs requested against permitted for a cap', () => {
    const [block] = deriveTrainingBlocks(CAP_BLOCK);

    expect(block.quantities).toHaveLength(1);
    expect(block.quantities[0]).toMatchObject({
      label: 'epochs (epochs)',
      required: '500',
      available: '100',
    });
  });

  it('pairs the needed window against the fetch ceiling for a dataset block', () => {
    const [block] = deriveTrainingBlocks(DATASET_BLOCK);

    expect(block.quantities[0]).toMatchObject({ required: '42,000', available: '20,000' });
  });

  it('still returns a block that expresses no numeric requirement', () => {
    // Hiding a block is worse than rendering it without figures; inventing figures for it
    // is worse than both.
    const [block] = deriveTrainingBlocks(FEATURES_BLOCK);

    expect(block.reason).toBe(TRAINING_BLOCK_REASONS.FEATURES);
    expect(block.quantities).toEqual([]);
    expect(block.message).toBe(FEATURES_BLOCK.message);
    expect(block.issues).toHaveLength(1);
    expect(block.issues[0].message).toContain('ema_20_lag_6');
    expect(block.issues[0].fixHint).toBe('Reduce the lag, or widen the history range.');
  });

  it('renders each issue with its own required and available quantity', () => {
    // Requirement 14.9 applied per message rather than only to the block as a whole.
    const [block] = deriveTrainingBlocks(ML_REQUIREMENTS_BLOCK);

    expect(block.issues.map((i) => i.quantity.text)).toEqual([
      'usable_feature_columns — required 5, available 3',
      'usable_rows — required 5,000, available 1,240',
    ]);
  });

  it('holds the two numbers in one string so they cannot be rendered apart', () => {
    const [block] = deriveTrainingBlocks(ML_REQUIREMENTS_BLOCK);

    for (const quantity of block.quantities) {
      expect(quantity.text).toContain(quantity.required);
      expect(quantity.text).toContain(quantity.available);
    }
  });

  it('drops a pair with only one side rather than inventing the other', () => {
    const partial = deriveTrainingBlocks({
      state: 'BLOCKED',
      reason: 'ML_REQUIREMENTS',
      message: 'Training cannot start.',
      detail: { required: { columns: 5 }, available: {}, issues: [] },
    });

    expect(partial[0].quantities).toEqual([]);
    // And nothing was substituted for the missing half.
    expect(JSON.stringify(partial)).not.toContain('unknown');
  });

  it('recomputes no threshold — the numbers are the payload\'s, whatever they say', () => {
    // Deliberately inconsistent figures. A client that re-derived the requirement would
    // "correct" these and then disagree with the gate that actually refused the request.
    const [block] = deriveTrainingBlocks({
      state: 'BLOCKED',
      reason: 'ML_REQUIREMENTS',
      message: 'Training cannot start.',
      detail: { required: { columns: 2, rows: 7 }, available: { columns: 900, rows: 9000 }, issues: [] },
    });

    expect(block.quantities[0]).toMatchObject({ required: '2', available: '900' });
    expect(block.quantities[1]).toMatchObject({ required: '7', available: '9,000' });
  });

  it('states that no training job was created', () => {
    // No job row exists on any blocked path, and the response says so explicitly.
    expect(deriveTrainingBlocks(ML_REQUIREMENTS_BLOCK)[0].jobCreated).toBe(false);
    expect(deriveTrainingBlocks(CAP_BLOCK)[0].jobCreated).toBe(false);
  });

  it('is not a block when nothing was blocked', () => {
    // A queued or not-required training half must not render as a refusal.
    expect(deriveTrainingBlocks(null)).toEqual([]);
    expect(deriveTrainingBlocks({ state: 'QUEUED', job_id: 'job_1', jobs: [{}] })).toEqual([]);
    expect(deriveTrainingBlocks({ state: 'NOT_REQUIRED', required: false })).toEqual([]);
    expect(deriveTrainingBlocks({ state: 'UNAVAILABLE' })).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 2. The builder renders both quantities
// ---------------------------------------------------------------------------

const CATEGORIES = [
  { id: 'DATA', display_name: 'Market Data', order: 1 },
  { id: 'INDICATOR', display_name: 'Indicators', order: 2 },
  { id: 'FEATURE_ENGINEERING', display_name: 'Feature Engineering', order: 3 },
  { id: 'ML_DL', display_name: 'ML / DL Models', order: 4 },
  { id: 'ACTION', display_name: 'Actions', order: 5 },
];

const OHLCV_FEED = {
  block_id: 'ohlcv_feed',
  display_name: 'OHLCV Feed',
  category: 'DATA',
  description: 'Validated candles for one market and timeframe.',
  inputs: [],
  outputs: [{ port: 'candles', type: 'OHLCV_FRAME' }],
  params: [],
  allowed_predecessor_categories: [],
  allowed_successor_categories: ['INDICATOR', 'ACTION'],
  execution_semantics: 'STATEFUL',
  runtime_ref: 'module:ohlcv_feed',
};

const registryPayload = {
  registry_version: 'r_block_0001',
  registry_schema_version: 1,
  port_types: ['OHLCV_FRAME', 'PREDICTION', 'SIGNAL', 'TRADE_INTENT', 'SCALAR'],
  categories: CATEGORIES,
  blocks: [OHLCV_FEED],
  compatibility_matrix: { OHLCV_FRAME: ['OHLCV_FRAME'] },
};

const served = () => ({ status: 200, data: registryPayload, headers: { etag: '"r_block_0001"' } });

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const renderBuilder = (trainingBlock) =>
  render(
    <MemoryRouter>
      <ReactFlowProvider>
        <UndoRedoProvider>
          <ValidationProvider>
            <StrategyBuilderCanvas trainingBlock={trainingBlock} />
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

describe('the builder displays each blocking training message with both quantities', () => {
  it('renders one panel per block, announced to assistive technology', async () => {
    renderBuilder(ML_REQUIREMENTS_BLOCK);

    const panel = await screen.findByTestId('training-blocks');
    expect(panel.getAttribute('role')).toBe('alert');
    expect(panel.getAttribute('data-block-count')).toBe('1');
    expect(screen.getAllByTestId('training-block')).toHaveLength(1);
  });

  it('renders the required AND the available quantity for every dimension', async () => {
    renderBuilder(ML_REQUIREMENTS_BLOCK);
    await screen.findByTestId('training-blocks');

    const quantities = screen.getAllByTestId('training-block-quantity');
    expect(quantities).toHaveLength(2);

    const columns = quantities.find((node) => node.dataset.label === 'feature columns');
    expect(columns.dataset.required).toBe('5');
    expect(columns.dataset.available).toBe('3');
    expect(columns.textContent).toContain('required 5');
    expect(columns.textContent).toContain('available 3');

    const rows = quantities.find((node) => node.dataset.label === 'usable rows');
    expect(rows.dataset.required).toBe('5,000');
    expect(rows.dataset.available).toBe('1,240');
    expect(rows.textContent).toContain('required 5,000');
    expect(rows.textContent).toContain('available 1,240');
  });

  it('renders the gate sentence character for character', async () => {
    renderBuilder(ML_REQUIREMENTS_BLOCK);
    await screen.findByTestId('training-blocks');

    // Verbatim, including the thousands separators and the full stops. A paraphrase here
    // would be a second wording of one rule.
    expect(screen.getByTestId('training-block-message').textContent).toContain(
      'Training cannot start. Required: 5 feature columns and 5,000 usable rows. ' +
        'Available: 3 feature columns and 1,240 rows.',
    );
  });

  it('names the node and the block the shortfall belongs to', async () => {
    renderBuilder(ML_REQUIREMENTS_BLOCK);
    await screen.findByTestId('training-blocks');

    const block = screen.getByTestId('training-block');
    expect(block.dataset.reason).toBe('ML_REQUIREMENTS');
    expect(block.dataset.nodeId).toBe('n-model');
    expect(block.dataset.blockId).toBe('xgboost');
    expect(block.dataset.quantityCount).toBe('2');
  });

  it('renders each issue with its own pair and its fix hint verbatim', async () => {
    renderBuilder(ML_REQUIREMENTS_BLOCK);
    await screen.findByTestId('training-blocks');

    const issues = screen.getAllByTestId('training-block-issue');
    expect(issues).toHaveLength(2);
    expect(issues[0].dataset.code).toBe('INSUFFICIENT_FEATURE_COLUMNS');
    expect(issues[0].textContent).toContain('usable_feature_columns — required 5, available 3');
    expect(issues[0].textContent).toContain(
      "Add 2 more feature column(s) to the pipeline feeding 'XGBoost'.",
    );
  });

  it('states that nothing is queued, because no job row was created', async () => {
    renderBuilder(ML_REQUIREMENTS_BLOCK);
    await screen.findByTestId('training-blocks');

    expect(screen.getByTestId('training-block-no-job').textContent).toContain(
      'No training job was created',
    );
  });

  it('renders a cap refusal as requested against permitted', async () => {
    renderBuilder(CAP_BLOCK);
    await screen.findByTestId('training-blocks');

    const quantity = screen.getByTestId('training-block-quantity');
    expect(quantity.dataset.required).toBe('500');
    expect(quantity.dataset.available).toBe('100');
  });

  it('renders a sequence shortfall with all four pairs', async () => {
    renderBuilder(SEQUENCE_BLOCK);
    await screen.findByTestId('training-blocks');

    expect(screen.getAllByTestId('training-block-quantity').map((n) => n.dataset.label)).toEqual([
      'feature columns',
      'usable rows',
      'training rows',
      'sequence length',
    ]);
  });

  it('renders a reason with no numeric requirement as the message and its issues', async () => {
    renderBuilder(FEATURES_BLOCK);
    await screen.findByTestId('training-blocks');

    expect(screen.queryAllByTestId('training-block-quantity')).toHaveLength(0);
    expect(screen.getByTestId('training-block-message').textContent).toContain(
      'The produced feature set failed the feature schema check.',
    );
    expect(screen.getByTestId('training-block-issue').textContent).toContain('ema_20_lag_6');
  });

  it('renders no panel when training was not blocked', async () => {
    renderBuilder({ state: 'QUEUED', job_id: 'job_1', jobs: [{ id: 'job_1' }] });
    await waitFor(() =>
      expect(screen.getByTestId('registry-state').textContent).toContain('ready'),
    );

    expect(screen.queryByTestId('training-blocks')).toBeNull();
  });

  it('renders no panel at all when nothing was reported', async () => {
    renderBuilder(null);
    await waitFor(() =>
      expect(screen.getByTestId('registry-state').textContent).toContain('ready'),
    );

    expect(screen.queryByTestId('training-blocks')).toBeNull();
  });
});
