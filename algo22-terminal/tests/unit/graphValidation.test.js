/**
 * Tests for `src/lib/graphValidation.js` — task 3.10, Requirements 8.8, 8.9, 8.10, 8.11.
 *
 * The rules asserted here are the ones the canvas cannot be trusted to enforce on its own:
 *
 * * a response is accepted only when **both** the request id and the graph key it was issued
 *   for still match, so a debounced request answered after a further edit cannot mark a graph
 *   nobody is looking at (Requirement 8.10);
 * * `POST /api/strategies/validate` always answers HTTP 200, so the verdict is read from the
 *   body: `valid: false` is `invalid`, and a body carrying no boolean `valid` is not a verdict
 *   at all;
 * * severity is the canonical lower-case `error` / `warning`, failing closed to `error` for
 *   anything unrecognised (Requirement 8.8);
 * * `fix_hint` travels verbatim — no function in this module rewrites, trims or substitutes
 *   backend text (Requirement 8.9);
 * * a marker signature changes only when the marker content changes, which is what lets the
 *   page skip re-allocating nodes and so avoid re-triggering its own validation;
 * * the four status-strip states report what is known and say "unknown" when nothing is
 *   (Requirement 8.11).
 */

import { describe, it, expect } from 'vitest';
import {
  FEED_STATES,
  SEVERITY_ERROR,
  SEVERITY_WARNING,
  TRAINING_STATES,
  VALIDATION_DEBOUNCE_MS,
  VALIDATION_STATES,
  collectMarkers,
  deriveFeedState,
  deriveTrainingState,
  expectedIntervalMs,
  initialValidationState,
  markerLabel,
  markerSignature,
  normaliseSeverity,
  reportAppliesToCanvas,
  reportIssues,
  semanticGraphKey,
  validationReducer,
  validationSummary,
} from '../../src/lib/graphValidation';
import { toCanonical } from '../../src/lib/canonicalGraph';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A `fix_hint` with punctuation a "prettifier" would mangle. Asserted character for character. */
const SYMBOL_HINT = 'Set period to 14 — the standard RSI window — then re-check.';
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

/** A report in the exact shape `ValidationReport.to_dict()` serves. */
const REPORT = {
  valid: false,
  dag_hash: 'dag_a',
  validation_state: 'INVALID',
  errors: [
    issue({ code: 'PARAM_OUT_OF_RANGE', node_id: 'n-data', field: 'window', message: 'RSI period must be between 2 and 500. Got 0.', expected: { min: 2, max: 500 }, actual: 0, fix_hint: SYMBOL_HINT }),
    issue({ code: 'EDGE_TYPE_MISMATCH', edge_id: 'e-1', message: 'OHLCV_FRAME cannot feed a SIGNAL input.', fix_hint: EDGE_HINT }),
    issue({ code: 'MISSING_REQUIRED_CATEGORY', message: 'This strategy declares no ACTION block.', fix_hint: GRAPH_HINT }),
  ],
  warnings: [
    issue({ code: 'WARMUP_EXCEEDS_HISTORY', severity: 'warning', node_id: 'n-data', message: 'This strategy needs 226 warmup bars. The selected range provides 180.', expected: 226, actual: 180, fix_hint: WARMUP_HINT }),
  ],
  summary: { node_count: 2, edge_count: 1, warmup_bars: 226 },
};

const canvasNode = (id, blockId, category, params, position = { x: 0, y: 0 }) => ({
  id,
  type: blockId,
  position,
  data: { block_id: blockId, category, label: blockId, params, inputs: [], outputs: [] },
});

// ---------------------------------------------------------------------------
// Severity vocabulary (Requirement 8.8)
// ---------------------------------------------------------------------------

describe('severity is canonical lower case and fails closed', () => {
  it('recognises the two canonical values', () => {
    expect(normaliseSeverity('error')).toBe(SEVERITY_ERROR);
    expect(normaliseSeverity('warning')).toBe(SEVERITY_WARNING);
    expect(SEVERITY_ERROR).toBe('error');
    expect(SEVERITY_WARNING).toBe('warning');
  });

  it('normalises case and surrounding space', () => {
    expect(normaliseSeverity('  WARNING ')).toBe(SEVERITY_WARNING);
    expect(normaliseSeverity('Warn')).toBe(SEVERITY_WARNING);
    expect(normaliseSeverity(' Error')).toBe(SEVERITY_ERROR);
  });

  it('fails closed to error on anything unrecognised', () => {
    for (const value of [undefined, null, '', 'info', 'notice', 'CRITICAL', 42, {}, []]) {
      expect(normaliseSeverity(value)).toBe(SEVERITY_ERROR);
    }
  });
});

// ---------------------------------------------------------------------------
// Graph identity (the second half of the stale-response key)
// ---------------------------------------------------------------------------

describe('semanticGraphKey keys the strategy, not its presentation', () => {
  const nodes = [
    canvasNode('n-data', 'ohlcv_feed', 'DATA', { symbol: 'ETH/USDT', timeframe: '4h' }),
    canvasNode('n-action', 'market_buy', 'ACTION', { quantity: 5 }),
  ];
  const edges = [{ id: 'e-1', source: 'n-data', sourceHandle: 'candles', target: 'n-action', targetHandle: 'signal' }];

  it('is invariant under a presentation-only change', () => {
    const moved = [{ ...nodes[0], position: { x: 900, y: -40 } }, nodes[1]];
    expect(semanticGraphKey(toCanonical(moved, edges))).toBe(semanticGraphKey(toCanonical(nodes, edges)));
  });

  it('is invariant under a marker write-back, so marking cannot trigger validation', () => {
    const marked = [
      { ...nodes[0], data: { ...nodes[0].data, validation: collectMarkers(REPORT).nodes['n-data'], unvalidated: true } },
      nodes[1],
    ];
    expect(semanticGraphKey(toCanonical(marked, edges))).toBe(semanticGraphKey(toCanonical(nodes, edges)));
  });

  it('is invariant under a rename', () => {
    expect(semanticGraphKey(toCanonical(nodes, edges, { name: 'A' })))
      .toBe(semanticGraphKey(toCanonical(nodes, edges, { name: 'B' })));
  });

  it('changes when a parameter changes', () => {
    const edited = [
      { ...nodes[0], data: { ...nodes[0].data, params: { symbol: 'ETH/USDT', timeframe: '1h' } } },
      nodes[1],
    ];
    expect(semanticGraphKey(toCanonical(edited, edges))).not.toBe(semanticGraphKey(toCanonical(nodes, edges)));
  });

  it('changes when a connection is added, and does not depend on key order', () => {
    expect(semanticGraphKey(toCanonical(nodes, []))).not.toBe(semanticGraphKey(toCanonical(nodes, edges)));
    expect(semanticGraphKey({ schema_version: 2, nodes: [], edges: [] }))
      .toBe(semanticGraphKey({ edges: [], nodes: [], schema_version: 2 }));
  });

  it('returns null for something that is not a graph', () => {
    expect(semanticGraphKey(null)).toBeNull();
    expect(semanticGraphKey('graph')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Marking nodes and edges (Requirements 8.8, 8.9)
// ---------------------------------------------------------------------------

describe('collectMarkers marks every node and edge the report names', () => {
  const markers = collectMarkers(REPORT);

  it('marks the named node with its severity and issue count', () => {
    const marker = markers.nodes['n-data'];
    expect(marker.severity).toBe(SEVERITY_ERROR);
    expect(marker.count).toBe(2);
    expect(marker.errorCount).toBe(1);
    expect(marker.warningCount).toBe(1);
    expect(marker.codes).toEqual(['PARAM_OUT_OF_RANGE', 'WARMUP_EXCEEDS_HISTORY']);
  });

  it('marks the named edge with its severity and issue count', () => {
    const marker = markers.edges['e-1'];
    expect(marker.severity).toBe(SEVERITY_ERROR);
    expect(marker.count).toBe(1);
    expect(marker.issues[0].fix_hint).toBe(EDGE_HINT);
  });

  it('keeps an issue that names neither a node nor an edge instead of dropping it', () => {
    expect(markers.graph.map((entry) => entry.code)).toEqual(['MISSING_REQUIRED_CATEGORY']);
    expect(markers.graph[0].fix_hint).toBe(GRAPH_HINT);
  });

  it('renders fix_hint verbatim — every issue keeps the backend string character for character', () => {
    const hints = markers.issues.map((entry) => entry.fix_hint);
    expect(hints).toEqual([SYMBOL_HINT, EDGE_HINT, GRAPH_HINT, WARMUP_HINT]);
  });

  it('counts errors and warnings across the whole report', () => {
    expect(markers.errorCount).toBe(3);
    expect(markers.warningCount).toBe(1);
  });

  it('takes severity from the bucket, so an errors[] entry blocks whatever its string says', () => {
    const mislabelled = { valid: false, errors: [issue({ node_id: 'n-1', severity: 'warning' })], warnings: [] };
    const marker = collectMarkers(mislabelled).nodes['n-1'];
    expect(marker.severity).toBe(SEVERITY_ERROR);
    expect(marker.issues[0].severity).toBe(SEVERITY_ERROR);
    // The mismatch is kept rather than resolved out of sight.
    expect(marker.issues[0].declared_severity).toBe(SEVERITY_WARNING);
  });

  it('lets an error on a node outrank a warning on the same node', () => {
    const both = {
      valid: false,
      errors: [issue({ code: 'E', node_id: 'n-1' })],
      warnings: [issue({ code: 'W', node_id: 'n-1', severity: 'warning' })],
    };
    expect(collectMarkers(both).nodes['n-1'].severity).toBe(SEVERITY_ERROR);
  });

  it('attributes an issue naming both a node and an edge to both', () => {
    const shared = { valid: false, errors: [issue({ code: 'X', node_id: 'n-1', edge_id: 'e-1' })], warnings: [] };
    const marked = collectMarkers(shared);
    expect(marked.nodes['n-1'].count).toBe(1);
    expect(marked.edges['e-1'].count).toBe(1);
    expect(marked.graph).toEqual([]);
  });

  it('calls out a warning that means the server overrode a client value', () => {
    const overridden = {
      valid: true,
      errors: [],
      warnings: [issue({ code: 'EDGE_TYPE_RECOMPUTED', severity: 'warning', edge_id: 'e-1' })],
    };
    const marked = collectMarkers(overridden);
    expect(marked.overrides.map((entry) => entry.code)).toEqual(['EDGE_TYPE_RECOMPUTED']);
    expect(marked.edges['e-1'].overrideCount).toBe(1);
  });

  it('treats no report as no markers rather than as a clean bill of health', () => {
    const empty = collectMarkers(null);
    expect(empty.issues).toEqual([]);
    expect(empty.nodes).toEqual({});
    expect(empty.edges).toEqual({});
    expect(reportIssues(undefined)).toEqual([]);
  });

  it('ignores a malformed entry inside the buckets instead of throwing', () => {
    const messy = { valid: false, errors: [null, 'boom', issue({ node_id: 'n-1' })], warnings: undefined };
    expect(collectMarkers(messy).nodes['n-1'].count).toBe(1);
  });
});

describe('markerLabel states severity and count in words', () => {
  it('spells out both counts', () => {
    expect(markerLabel(collectMarkers(REPORT).nodes['n-data'])).toBe('1 error, 1 warning');
  });

  it('singular and plural are both correct, and no marker is no text', () => {
    const many = {
      valid: false,
      errors: [issue({ code: 'A', node_id: 'n' }), issue({ code: 'B', node_id: 'n' })],
      warnings: [],
    };
    expect(markerLabel(collectMarkers(many).nodes.n)).toBe('2 errors');
    expect(markerLabel(null)).toBe('');
  });
});

describe('markerSignature changes only when the marker content changes', () => {
  it('is equal for two markers built from the same report', () => {
    expect(markerSignature(collectMarkers(REPORT).nodes['n-data']))
      .toBe(markerSignature(collectMarkers(JSON.parse(JSON.stringify(REPORT))).nodes['n-data']));
  });

  it('differs when the count, the severity or the codes differ', () => {
    const base = collectMarkers(REPORT).nodes['n-data'];
    const fewer = collectMarkers({ valid: false, errors: [REPORT.errors[0]], warnings: [] }).nodes['n-data'];
    expect(markerSignature(fewer)).not.toBe(markerSignature(base));
    expect(markerSignature(null)).toBe('');
  });
});

// ---------------------------------------------------------------------------
// The state machine (Requirement 8.10)
// ---------------------------------------------------------------------------

describe('the validation state machine', () => {
  it('debounces at 400 ms, as the requirement states', () => {
    expect(VALIDATION_DEBOUNCE_MS).toBe(400);
  });

  it('starts unvalidated with no report', () => {
    const state = initialValidationState();
    expect(state.state).toBe(VALIDATION_STATES.UNVALIDATED);
    expect(state.report).toBeNull();
    expect(reportAppliesToCanvas(state)).toBe(false);
  });

  it('marks the graph unvalidated on edit, before any request is made', () => {
    const state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    expect(state.state).toBe(VALIDATION_STATES.UNVALIDATED);
    expect(state.graphKey).toBe('A');
    expect(state.requests).toBe(0);
  });

  it('reads the verdict from the body, not from the transport', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    expect(state.state).toBe(VALIDATION_STATES.VALIDATING);

    const invalid = validationReducer(state, { type: 'report', requestId: 1, graphKey: 'A', report: REPORT });
    expect(invalid.state).toBe(VALIDATION_STATES.INVALID);
    expect(reportAppliesToCanvas(invalid)).toBe(true);

    const valid = validationReducer(state, {
      type: 'report',
      requestId: 1,
      graphKey: 'A',
      report: { valid: true, errors: [], warnings: [], summary: { node_count: 2, edge_count: 1, warmup_bars: 0 } },
    });
    expect(valid.state).toBe(VALIDATION_STATES.VALID);
  });

  it('discards a response whose request id has been superseded', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 2, graphKey: 'A' });

    const late = validationReducer(state, { type: 'report', requestId: 1, graphKey: 'A', report: REPORT });
    expect(late.discarded).toBe(1);
    expect(late.report).toBeNull();
    expect(late.state).toBe(VALIDATION_STATES.VALIDATING);

    const answered = validationReducer(late, { type: 'report', requestId: 2, graphKey: 'A', report: REPORT });
    expect(answered.report).toBe(REPORT);
    expect(answered.discarded).toBe(1);
  });

  it('discards a response issued for a graph that has since changed, even at the current id', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    state = validationReducer(state, { type: 'edit', graphKey: 'B' });

    const late = validationReducer(state, { type: 'report', requestId: 1, graphKey: 'A', report: REPORT });
    expect(late.discarded).toBe(1);
    expect(late.report).toBeNull();
    expect(late.state).toBe(VALIDATION_STATES.UNVALIDATED);
  });

  it('keeps a report the canvas has outgrown, but stops treating it as current', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    state = validationReducer(state, { type: 'report', requestId: 1, graphKey: 'A', report: REPORT });
    expect(reportAppliesToCanvas(state)).toBe(true);

    state = validationReducer(state, { type: 'edit', graphKey: 'B' });
    expect(state.state).toBe(VALIDATION_STATES.UNVALIDATED);
    expect(state.report).toBe(REPORT);
    expect(reportAppliesToCanvas(state)).toBe(false);
  });

  it('fails closed on a transport failure and never invents a verdict', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    state = validationReducer(state, { type: 'failed', requestId: 1, error: { code: 'NETWORK', message: 'no route' } });

    expect(state.state).toBe(VALIDATION_STATES.UNAVAILABLE);
    expect(state.state).not.toBe(VALIDATION_STATES.VALID);
    expect(state.error.code).toBe('NETWORK');
    expect(state.report).toBeNull();
  });

  it('does not let a superseded failure overwrite a newer request', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 2, graphKey: 'A' });
    state = validationReducer(state, { type: 'failed', requestId: 1, error: { code: 'NETWORK' } });

    expect(state.state).toBe(VALIDATION_STATES.VALIDATING);
    expect(state.discarded).toBe(1);
  });

  it('keeps the last report through a failure rather than wiping it', () => {
    let state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    state = validationReducer(state, { type: 'request', requestId: 1, graphKey: 'A' });
    state = validationReducer(state, { type: 'report', requestId: 1, graphKey: 'A', report: REPORT });
    state = validationReducer(state, { type: 'request', requestId: 2, graphKey: 'A' });
    state = validationReducer(state, { type: 'failed', requestId: 2, error: { code: 'NETWORK' } });

    expect(state.state).toBe(VALIDATION_STATES.UNAVAILABLE);
    expect(state.report).toBe(REPORT);
  });

  it('returns the same state object for an edit that changed nothing, and for an unknown action', () => {
    const state = validationReducer(initialValidationState(), { type: 'edit', graphKey: 'A' });
    expect(validationReducer(state, { type: 'edit', graphKey: 'A' })).toBe(state);
    expect(validationReducer(state, { type: 'nonsense' })).toBe(state);
  });

  it('reports a canvas that cannot be serialized as unvalidated, never as valid', () => {
    const state = validationReducer(initialValidationState(), {
      type: 'unserializable',
      issue: { code: 'BLOCK_ID_MISSING', message: "node 'n-1' carries no block_id" },
    });
    expect(state.state).toBe(VALIDATION_STATES.UNVALIDATED);
    expect(state.graphKey).toBeNull();
    expect(validationSummary(state, collectMarkers(null)).headline).toContain('Not validated');
  });
});

// ---------------------------------------------------------------------------
// The status strip (Requirement 8.11)
// ---------------------------------------------------------------------------

describe('the validation summary never reads as a pass when nothing was checked', () => {
  const withReport = (report, graphKey = 'A') => ({
    ...initialValidationState(),
    state: report && report.valid ? VALIDATION_STATES.VALID : VALIDATION_STATES.INVALID,
    graphKey,
    reportKey: graphKey,
    report,
  });

  it('says so when no check has happened', () => {
    const summary = validationSummary(initialValidationState(), collectMarkers(null));
    expect(summary.state).toBe(VALIDATION_STATES.UNVALIDATED);
    expect(summary.headline).toBe('Not validated yet');
    expect(summary.headline.toLowerCase()).not.toContain('valid ·');
  });

  it('distinguishes "never checked" from "edited since the check"', () => {
    const edited = { ...withReport(REPORT), state: VALIDATION_STATES.UNVALIDATED, graphKey: 'B' };
    expect(validationSummary(edited, collectMarkers(REPORT)).headline).toContain('edited since the last check');
  });

  it('counts the errors and warnings, and reports the graph shape', () => {
    const summary = validationSummary(withReport(REPORT), collectMarkers(REPORT));
    expect(summary.state).toBe(VALIDATION_STATES.INVALID);
    expect(summary.headline).toContain('3 errors');
    expect(summary.headline).toContain('1 warning');
    expect(summary.detail).toBe('2 nodes, 1 connections, 226 warmup bars');
  });

  it('reports a warnings-only report as valid (Requirement 8.5)', () => {
    const warningsOnly = {
      valid: true,
      errors: [],
      warnings: [REPORT.warnings[0]],
      summary: { node_count: 2, edge_count: 1, warmup_bars: 226 },
    };
    const summary = validationSummary(withReport(warningsOnly), collectMarkers(warningsOnly));
    expect(summary.state).toBe(VALIDATION_STATES.VALID);
    expect(summary.headline).toContain('1 warning');
  });

  it('says validation is unavailable, and says the held report may be old', () => {
    const state = { ...withReport(REPORT), state: VALIDATION_STATES.UNAVAILABLE, error: { message: 'no route' } };
    const summary = validationSummary(state, collectMarkers(REPORT));
    expect(summary.headline).toBe('Validation unavailable');
    expect(summary.detail).toContain('no route');
    expect(summary.detail).toContain('may not describe the current graph');
  });
});

describe('the feed state is reported, never assumed', () => {
  const market = { symbol: 'ETH/USDT', timeframe: '15m' };

  it('parses the timeframes the pipeline publishes, and refuses nonsense', () => {
    expect(expectedIntervalMs('15m')).toBe(900000);
    expect(expectedIntervalMs('1h')).toBe(3600000);
    expect(expectedIntervalMs('1d')).toBe(86400000);
    expect(expectedIntervalMs('soon')).toBeNull();
    expect(expectedIntervalMs('0m')).toBeNull();
  });

  it('is UNKNOWN with nothing observed, not LIVE', () => {
    const feed = deriveFeedState(null, { market });
    expect(feed.state).toBe(FEED_STATES.UNKNOWN);
    expect(feed.known).toBe(false);
    expect(feed.state).not.toBe(FEED_STATES.LIVE);
  });

  it('is UNKNOWN when the graph names no market yet', () => {
    expect(deriveFeedState(null, { market: { symbol: null, timeframe: null } }).state).toBe(FEED_STATES.UNKNOWN);
  });

  it('reports LIVE, DELAYED and STALE against the expected bar interval', () => {
    const now = 1_700_000_000_000;
    const at = (ageMs) => deriveFeedState({ connected: true, lastEventAt: now - ageMs }, { now, market });
    // A 15m bar: live inside 1.5 intervals, delayed beyond that, stale beyond 3.
    expect(at(60_000).state).toBe(FEED_STATES.LIVE);
    expect(at(25 * 60_000).state).toBe(FEED_STATES.DELAYED);
    expect(at(60 * 60_000).state).toBe(FEED_STATES.STALE);
    expect(at(60_000).detail).toContain('expected every');
  });

  it('reports DISCONNECTED and INSUFFICIENT_DATA literally', () => {
    expect(deriveFeedState({ connected: false }, { market }).state).toBe(FEED_STATES.DISCONNECTED);
    const short = deriveFeedState({ connected: true, availableBars: 180, warmupBars: 226 }, { market });
    expect(short.state).toBe(FEED_STATES.INSUFFICIENT_DATA);
    expect(short.detail).toContain('226');
  });

  it('is UNKNOWN when the age of the last candle cannot be established', () => {
    expect(deriveFeedState({ connected: true }, { market }).state).toBe(FEED_STATES.UNKNOWN);
  });
});

describe('the training state is reported, never assumed', () => {
  it('is NOT_REQUIRED when the graph declares no ML block', () => {
    const training = deriveTrainingState({ mlNodeCount: 0 });
    expect(training.state).toBe(TRAINING_STATES.NOT_REQUIRED);
    expect(training.known).toBe(true);
  });

  it('is UNKNOWN when an ML block is present and no job has been observed', () => {
    const training = deriveTrainingState({ mlNodeCount: 2, job: null });
    expect(training.state).toBe(TRAINING_STATES.UNKNOWN);
    expect(training.known).toBe(false);
    expect(training.detail).toContain('2 ML/DL blocks');
  });

  it('reports an observed job, with its epoch counter', () => {
    const training = deriveTrainingState({
      mlNodeCount: 1,
      job: { id: 'job-7', status: 'running', current_epoch: 3, epochs_total: 10 },
    });
    expect(training.state).toBe(TRAINING_STATES.RUNNING);
    expect(training.detail).toContain('epoch 3/10');
  });

  it('is UNKNOWN for a status this build does not recognise', () => {
    const training = deriveTrainingState({ mlNodeCount: 1, job: { status: 'ALMOST_DONE' } });
    expect(training.state).toBe(TRAINING_STATES.UNKNOWN);
    expect(training.known).toBe(false);
  });
});
