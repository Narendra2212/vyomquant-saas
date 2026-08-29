/**
 * Tests for `src/lib/nodePreview.js` — the inspector's preview projection and state machine
 * (task 5.7, Requirements 24.7, 24.8).
 *
 * What is under test
 * ------------------
 * * **The projection transports and does not compute.** Every number in the render model is a
 *   number the response carried; the module is asserted to hold no arithmetic, structurally, so
 *   a later edit that "helpfully" normalises a series in the client is a red test rather than a
 *   second answer to what a block produces (SB-01 in a new location).
 * * **`null` is not zero.** A bar with no value reads as the empty placeholder, never `0`, and
 *   the count of empty values is carried rather than left to be eyeballed.
 * * **Requirement 24.8**: every produced column name survives, including when the value sample
 *   is truncated, and the truncation is stated.
 * * **Requirement 24.7's bound**: the window facts, including the cap and whether it applied,
 *   are rendered from what the server said and never recomputed.
 * * **The state machine**: a late answer for a node nobody is looking at is discarded, and an
 *   error drops the held preview rather than leaving stale numbers beside a fresh graph.
 * * **One message source**: a refusal's text is the backend's own `detail.message` /
 *   `detail.hint`, verbatim.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  EMPTY_VALUE_TEXT,
  OUTPUT_KINDS,
  PREVIEW_DEBOUNCE_MS,
  PREVIEW_STATES,
  PreviewProjectionError,
  describeWindow,
  formatPreviewValue,
  initialPreviewState,
  isFeatureMatrixOutput,
  previewAppliesToSelection,
  previewAvailability,
  previewError,
  previewKey,
  previewReducer,
  projectPreview,
} from '../../src/lib/nodePreview';
import { VALIDATION_DEBOUNCE_MS } from '../../src/lib/graphValidation';

// ---------------------------------------------------------------------------
// Fixtures: bodies in exactly the endpoint's wire shape
// ---------------------------------------------------------------------------

const seriesBody = (overrides = {}) => ({
  strategy_id: 'stg_1',
  node_id: 'n-ema',
  block_id: 'ema',
  category: 'INDICATOR',
  graph_source: 'request',
  dag_hash: 'd_abc',
  compiler_version: '2.1.0',
  market: { symbol: 'ETH/USDT', timeframe: '5m' },
  window: {
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
    first_timestamp: '2024-01-01T00:00:00',
    last_timestamp: '2024-01-01T16:35:00',
  },
  executed_nodes: ['n-data', 'n-ema'],
  outputs: [
    {
      name: 'value',
      port_type: 'SCALAR_SERIES',
      produced: true,
      kind: 'series',
      length: 200,
      index: ['2024-01-01T16:25:00', '2024-01-01T16:30:00', '2024-01-01T16:35:00'],
      values: [null, 100.5, 101.25],
      empty_values: 1,
    },
  ],
  issues: [],
  computed_at: '2024-01-01T16:36:00+00:00',
  ...overrides,
});

const matrixBody = (columns, sampled) => ({
  ...seriesBody(),
  node_id: 'n-lag',
  block_id: 'feat_lag',
  category: 'FEATURE_ENGINEERING',
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
      index: ['2024-01-01T16:30:00', '2024-01-01T16:35:00'],
      values: [sampled.map(() => null), sampled.map((_, i) => i + 0.5)],
      empty_values: sampled.length,
    },
  ],
});

// ---------------------------------------------------------------------------
// The projection carries what the response said
// ---------------------------------------------------------------------------

describe('projectPreview', () => {
  it('carries a series exactly as sent, newest last', () => {
    const model = projectPreview(seriesBody());
    const output = model.outputs[0];

    expect(output.name).toBe('value');
    expect(output.portType).toBe('SCALAR_SERIES');
    expect(output.produced).toBe(true);
    expect(output.kind).toBe(OUTPUT_KINDS.SERIES);
    expect(output.length).toBe(200);
    expect(output.rows.map((row) => row.value)).toEqual([null, 100.5, 101.25]);
    expect(output.rows.map((row) => row.index)).toEqual([
      '2024-01-01T16:25:00',
      '2024-01-01T16:30:00',
      '2024-01-01T16:35:00',
    ]);
  });

  it('reads an empty bar as empty rather than as zero', () => {
    const output = projectPreview(seriesBody()).outputs[0];

    expect(output.rows[0].empty).toBe(true);
    expect(output.rows[0].value).toBeNull();
    expect(output.rows[0].text).toBe(EMPTY_VALUE_TEXT);
    // The one thing it must never be: a number an author could compare to a threshold.
    expect(output.rows[0].text).not.toBe('0');
    expect(output.emptyValueCount).toBe(1);
  });

  it('treats a non-finite value as empty, because JSON cannot carry one', () => {
    const body = seriesBody();
    body.outputs[0].values = [Number.NaN, Number.POSITIVE_INFINITY, '3', 4];
    body.outputs[0].empty_values = 3;

    const rows = projectPreview(body).outputs[0].rows;

    expect(rows.map((row) => row.value)).toEqual([null, null, null, 4]);
  });

  it('reports a port that produced nothing rather than omitting it', () => {
    const body = seriesBody();
    body.outputs.push({ name: 'signal', port_type: 'SCALAR_SERIES', produced: false });

    const model = projectPreview(body);

    expect(model.outputs.map((output) => output.name)).toEqual(['value', 'signal']);
    expect(model.outputs[1].produced).toBe(false);
    expect(model.outputs[1].rows).toEqual([]);
  });

  it('carries the window facts the server stated, and recomputes none of them', () => {
    const body = seriesBody();
    body.window.requested = 100000;
    body.window.bars = 1200;
    body.window.clamped = true;

    const { window } = projectPreview(body);

    expect(window.bars).toBe(1200);
    expect(window.maxBars).toBe(1200);
    expect(window.requestedBars).toBe(100000);
    expect(window.clamped).toBe(true);
    expect(window.warmupBars).toBe(60);
    expect(window.planWarmupBars).toBe(60);
    expect(window.warmupExceedsWindow).toBe(false);
  });

  it('carries the recorded numeric conditions for the node (Requirement 20.4)', () => {
    const issue = {
      code: 'DIVISION_BY_ZERO',
      node_id: 'n-div',
      bars_affected: 200,
      message: 'Division by a value at or near zero produced no value on 200 bars.',
    };
    const model = projectPreview(seriesBody({ issues: [issue] }));

    expect(model.issues).toEqual([issue]);
  });

  it('refuses a body that carries no preview instead of rendering an empty one', () => {
    // "This block produced nothing" and "nobody could tell you" are different facts.
    expect(() => projectPreview(null)).toThrow(PreviewProjectionError);
    expect(() => projectPreview({ node_id: 'n-1' })).toThrow(/outputs/);
    expect(() => projectPreview({ outputs: [] })).toThrow(/node_id/);
    expect(() => projectPreview(seriesBody({ outputs: [{ name: 'v', produced: true, kind: 'wat' }] })))
      .toThrow(/unknown kind/);
  });

  it('freezes the model, so no consumer can edit a value on its way to the screen', () => {
    const model = projectPreview(seriesBody());

    expect(Object.isFrozen(model)).toBe(true);
    expect(Object.isFrozen(model.outputs)).toBe(true);
    expect(Object.isFrozen(model.window)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Requirement 24.8
// ---------------------------------------------------------------------------

describe('a FEATURE_ENGINEERING preview (Requirement 24.8)', () => {
  it('carries every produced column name with its per-column warmup', () => {
    const columns = ['lag_1', 'lag_2', 'lag_3'];
    const output = projectPreview(matrixBody(columns, columns)).outputs[0];

    expect(isFeatureMatrixOutput(output)).toBe(true);
    expect(output.columns).toEqual(columns);
    expect(output.columnCount).toBe(3);
    expect(output.columnWarmup).toEqual([
      { column: 'lag_1', warmup: 1, producedBy: 'n-lag' },
      { column: 'lag_2', warmup: 2, producedBy: 'n-lag' },
      { column: 'lag_3', warmup: 3, producedBy: 'n-lag' },
    ]);
  });

  it('samples values column by column, labelled with the column that produced them', () => {
    const columns = ['lag_1', 'lag_2'];
    const output = projectPreview(matrixBody(columns, columns)).outputs[0];

    expect(output.rows).toHaveLength(2);
    expect(output.rows[0].cells.map((cell) => cell.column)).toEqual(columns);
    expect(output.rows[0].cells.every((cell) => cell.empty)).toBe(true);
    expect(output.rows[1].cells.map((cell) => cell.value)).toEqual([0.5, 1.5]);
  });

  it('keeps every name when the value sample is truncated, and says it was', () => {
    // A wide matrix is the case the requirement is really about: the names cost nothing to
    // serve and are the half an author reads, so they are never the part that gets dropped.
    const columns = Array.from({ length: 18 }, (_, i) => `lag_${i + 1}`);
    const sampled = columns.slice(0, 12);
    const output = projectPreview(matrixBody(columns, sampled)).outputs[0];

    expect(output.columns).toHaveLength(18);
    expect(output.columnWarmup).toHaveLength(18);
    expect(output.sampledColumns).toEqual(sampled);
    expect(output.sampleTruncated).toBe(true);
    expect(output.rows[0].cells).toHaveLength(12);
  });
});

// ---------------------------------------------------------------------------
// Value formatting
// ---------------------------------------------------------------------------

describe('formatPreviewValue', () => {
  it('renders an absent value as the placeholder, never as a numeral', () => {
    for (const absent of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY, '5', {}]) {
      expect(formatPreviewValue(absent)).toBe(EMPTY_VALUE_TEXT);
    }
  });

  it('keeps a small magnitude visible instead of rounding it to zero', () => {
    // `toFixed(2)` would render this as "0.00" — a number that reads as nothing and is not.
    expect(formatPreviewValue(0.00004123)).toBe('0.00004123');
    expect(formatPreviewValue(0)).toBe('0');
  });

  it('keeps an integer an integer and bounds the digits of a long float', () => {
    expect(formatPreviewValue(68000)).toBe('68000');
    expect(formatPreviewValue(1 / 3)).toBe('0.333333');
  });
});

describe('describeWindow', () => {
  it('names the bar count and the range', () => {
    const { window } = projectPreview(seriesBody());

    expect(describeWindow(window)).toContain('Last 200 bars');
    expect(describeWindow(window)).toContain('2024-01-01T16:35:00');
  });

  it('states the cap whenever it applied, so a shortened window is not silent', () => {
    const body = seriesBody();
    body.window.requested = 100000;
    body.window.bars = 1200;
    body.window.clamped = true;

    const text = describeWindow(projectPreview(body).window);

    expect(text).toContain('at most 1200 bars');
    expect(text).toContain('100000');
  });

  it('explains an all-empty preview as warmup rather than leaving it a mystery', () => {
    const body = seriesBody();
    body.window.warmup_exceeds_window = true;

    expect(describeWindow(projectPreview(body).window)).toContain('warmup covers the whole window');
  });

  it('says so when there is no window rather than inventing one', () => {
    expect(describeWindow(null)).toBe('Window unknown.');
  });
});

// ---------------------------------------------------------------------------
// Availability: why a preview cannot be asked for
// ---------------------------------------------------------------------------

describe('previewAvailability', () => {
  const base = { strategyId: 'stg_1', nodeId: 'n-1', graphKey: 'k1', validationState: 'valid' };

  it('is available for a saved strategy, a selected node and a serializable canvas', () => {
    expect(previewAvailability(base)).toEqual({ available: true, code: null, reason: null });
  });

  it('explains an unsaved strategy instead of only disabling the control', () => {
    const verdict = previewAvailability({ ...base, strategyId: null });

    expect(verdict.available).toBe(false);
    expect(verdict.code).toBe('PREVIEW_STRATEGY_UNSAVED');
    expect(verdict.reason).toMatch(/Save this strategy first/);
  });

  it('refuses on an invalid graph, for the same reason the endpoint does', () => {
    const verdict = previewAvailability({ ...base, validationState: 'invalid' });

    expect(verdict.code).toBe('PREVIEW_GRAPH_INVALID');
  });

  it('names the missing piece for no selection and for an unserializable canvas', () => {
    expect(previewAvailability({ ...base, nodeId: null }).code).toBe('PREVIEW_NO_NODE_SELECTED');
    expect(previewAvailability({ ...base, graphKey: null }).code).toBe('PREVIEW_GRAPH_UNSERIALIZABLE');
  });
});

describe('previewKey', () => {
  it('is null unless the strategy, the node and the graph are all known', () => {
    expect(previewKey('s', 'n', 'g')).toBe('s\u0000n\u0000g');
    expect(previewKey(null, 'n', 'g')).toBeNull();
    expect(previewKey('s', '', 'g')).toBeNull();
    expect(previewKey('s', 'n', null)).toBeNull();
  });

  it('changes when the graph changes, because a preview is about one graph', () => {
    expect(previewKey('s', 'n', 'g1')).not.toBe(previewKey('s', 'n', 'g2'));
  });
});

// ---------------------------------------------------------------------------
// The state machine
// ---------------------------------------------------------------------------

describe('previewReducer', () => {
  const ready = () => {
    let state = previewReducer(initialPreviewState(), { type: 'select', key: 'k1' });
    state = previewReducer(state, { type: 'request', requestId: 1, key: 'k1' });
    return previewReducer(state, {
      type: 'result',
      requestId: 1,
      key: 'k1',
      preview: projectPreview(seriesBody()),
      at: 10,
    });
  };

  it('starts with nothing requested and nothing held', () => {
    const state = initialPreviewState();

    expect(state.state).toBe(PREVIEW_STATES.IDLE);
    expect(state.preview).toBeNull();
    expect(state.requested).toBe(false);
  });

  it('holds a preview only for the selection it was computed for', () => {
    const state = ready();

    expect(state.state).toBe(PREVIEW_STATES.READY);
    expect(previewAppliesToSelection(state)).toBe(true);
    expect(state.preview.nodeId).toBe('n-ema');
  });

  it('drops the held preview when the selection or the graph changes', () => {
    // Not kept and labelled stale: a series under the wrong block's name is worse than none.
    const state = previewReducer(ready(), { type: 'select', key: 'k2' });

    expect(state.state).toBe(PREVIEW_STATES.IDLE);
    expect(state.preview).toBeNull();
    expect(previewAppliesToSelection(state)).toBe(false);
  });

  it('keeps the held preview when a re-render reports the same selection', () => {
    const first = ready();
    const again = previewReducer(first, { type: 'select', key: 'k1' });

    expect(again).toBe(first);
  });

  it('discards an answer whose request was superseded', () => {
    let state = previewReducer(initialPreviewState(), { type: 'select', key: 'k1' });
    state = previewReducer(state, { type: 'request', requestId: 1, key: 'k1' });
    state = previewReducer(state, { type: 'request', requestId: 2, key: 'k1' });
    const landed = previewReducer(state, {
      type: 'result',
      requestId: 1,
      key: 'k1',
      preview: projectPreview(seriesBody()),
    });

    expect(landed.state).toBe(PREVIEW_STATES.LOADING);
    expect(landed.preview).toBeNull();
    expect(landed.discarded).toBe(1);
  });

  it('discards an answer for a node nobody is looking at any more', () => {
    // The case the request id alone cannot see: issued for A, then a click on B, then A lands.
    let state = previewReducer(initialPreviewState(), { type: 'select', key: 'kA' });
    state = previewReducer(state, { type: 'request', requestId: 1, key: 'kA' });
    state = previewReducer(state, { type: 'select', key: 'kB' });
    const landed = previewReducer(state, {
      type: 'result',
      requestId: 1,
      key: 'kA',
      preview: projectPreview(seriesBody()),
    });

    expect(landed.preview).toBeNull();
    expect(landed.discarded).toBe(1);
  });

  it('fails closed: an error leaves no numbers on screen', () => {
    const state = previewReducer(ready(), {
      type: 'failed',
      requestId: 1,
      key: 'k1',
      error: { code: 'PREVIEW_REFUSED', message: 'no' },
    });

    expect(state.state).toBe(PREVIEW_STATES.ERROR);
    expect(state.preview).toBeNull();
    expect(previewAppliesToSelection(state)).toBe(false);
  });

  it('ignores a request issued for a selection that has already moved on', () => {
    let state = previewReducer(initialPreviewState(), { type: 'select', key: 'k1' });
    state = previewReducer(state, { type: 'request', requestId: 1, key: 'kOld' });

    expect(state.state).toBe(PREVIEW_STATES.IDLE);
    expect(state.discarded).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// One message source
// ---------------------------------------------------------------------------

describe('previewError', () => {
  it("uses the backend's own message and hint, verbatim", () => {
    const message =
      'This node needs 1800 warmup bars before its first trustworthy value, which needs a ' +
      'window of 3650 bars. A preview reads at most 1200.';
    const hint = 'Reduce the lookback on this block or on one feeding it, or run a backtest.';
    const classified = previewError({
      status: 422,
      data: { detail: { error: 'PREVIEW_WARMUP_EXCEEDS_WINDOW', message, hint } },
    });

    expect(classified.code).toBe('PREVIEW_WARMUP_EXCEEDS_WINDOW');
    expect(classified.message).toBe(message);
    expect(classified.hint).toBe(hint);
    // A refusal about this graph is not fixed by asking again.
    expect(classified.retryable).toBe(false);
  });

  it('separates a dead session from a broken preview', () => {
    const classified = previewError({ status: 401 });

    expect(classified.code).toBe('PREVIEW_UNAUTHENTICATED');
    expect(classified.authExpired).toBe(true);
    expect(classified.retryable).toBe(false);
  });

  it('marks a 5xx and a network failure retryable', () => {
    expect(previewError({ status: 503 }).retryable).toBe(true);
    expect(previewError({ message: 'socket hang up' }).code).toBe('PREVIEW_NETWORK_ERROR');
    expect(previewError({ message: 'socket hang up' }).retryable).toBe(true);
  });

  it('reports a rate limit as its own state', () => {
    expect(previewError({ status: 429 }).code).toBe('PREVIEW_RATE_LIMITED');
  });

  it('reports a malformed body as the client-side problem it is', () => {
    const classified = previewError(new PreviewProjectionError('no outputs'));

    expect(classified.code).toBe('PREVIEW_MALFORMED');
    expect(classified.retryable).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// The structural half: no second evaluator in the client either
// ---------------------------------------------------------------------------

describe('the module computes nothing', () => {
  const source = readFileSync(
    path.resolve(__dirname, '..', '..', 'src', 'lib', 'nodePreview.js'),
    'utf8',
  );
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

  it('holds no arithmetic that could produce a value of its own', () => {
    // Requirement 24.7 is a property of the *code path*. A client-side rolling mean would
    // satisfy every assertion above on the day it was written and drift the day after, so the
    // absence is asserted structurally rather than left to this file's prose.
    for (const token of [
      'Math.pow',
      'Math.exp',
      'Math.log',
      'Math.sqrt',
      '.reduce((sum',
      'movingAverage',
      'rollingMean',
    ]) {
      expect(code).not.toContain(token);
    }
  });

  it('imports nothing: it is a projection, so it has no client and no engine', () => {
    expect(code).not.toMatch(/^\s*import\s/m);
  });

  it('refreshes on the same rhythm as validation, so the two cannot disagree by timing', () => {
    expect(PREVIEW_DEBOUNCE_MS).toBe(VALIDATION_DEBOUNCE_MS);
  });
});
