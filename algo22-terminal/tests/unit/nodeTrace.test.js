/**
 * Tests for `src/lib/nodeTrace.js` — the inspector's execution-trace projection
 * (task 9.3, Requirement 24.6).
 *
 * What is under test
 * ------------------
 * * **Requirement 24.6's four subjects survive the wire**: the node's inputs (one row per
 *   bound port, with the recorded type and shape), its output, its duration and every recorded
 *   failure.
 * * **The projection records, measures and decides nothing.** Every figure in the render model
 *   is a figure the response carried. The absence of arithmetic is asserted structurally, so a
 *   later edit that re-sums durations or re-derives a status in the browser is a red test
 *   rather than a fourth opinion about a run only the server observed.
 * * **`null` is not zero.** An untimed execution reads as the unrecorded placeholder, never
 *   `0 ms`: a node nobody timed and a node that took no measurable time are different facts.
 * * **`not_executed` is not `success` and is not "produced nothing".** The three render
 *   distinctly, because the block to look at is different in each case.
 * * **An upstream failure is reported as upstream**, from the two ids the endpoint sent — the
 *   choice of which failure is blocking stays the endpoint's.
 * * **One message source**: the summary sentence and each condition's `display` are the
 *   server's, verbatim.
 * * **Fail soft, not closed**: an absent or malformed trace projects to `null`, so a diagnostic
 *   that could not be read never takes the preview off screen.
 * * **Nothing identifying or secret can travel**: the projection is an allow-list by
 *   construction, swept over every credential and venue key SB-06 forbids.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import {
  TRACE_STATUSES,
  TRACE_STATUS_LABELS,
  UNRECORDED_TEXT,
  describeDuration,
  describeShape,
  hasTrace,
  projectNodeTrace,
  traceFromPreviewBody,
  traceFromPreviewFailure,
} from '../../src/lib/nodeTrace';

// ---------------------------------------------------------------------------
// Fixtures: exactly the shape `_node_trace_payload` publishes
// ---------------------------------------------------------------------------

const entry = (overrides = {}) => ({
  node_id: 'n-ema',
  node_type: 'indicator',
  status: 'success',
  duration_ms: 12.5,
  error_message: null,
  recorded_at: '2024-01-01T00:00:00',
  inputs: [
    {
      port: 'series',
      source: 'n-data',
      type: 'Series[float64]',
      shape: { type: 'Series', length: 300 },
    },
    { port: 'window', source: 'n-const', type: 'int', shape: { type: 'scalar', value: 20 } },
  ],
  output: { type: 'Series[float64]', shape: { type: 'Series', length: 300 } },
  ...overrides,
});

const tracePayload = (overrides = {}) => ({
  node_id: 'n-ema',
  status: 'success',
  recorded: true,
  duration_ms: 12.5,
  executions: [entry()],
  failures: [],
  blocking_failure: null,
  conditions: [],
  executed_nodes: ['n-data', 'n-ema'],
  summary: 'This block executed in 12.5 ms and recorded no failure.',
  signal_provenance: {
    available: true,
    traces_seen: 0,
    records: [],
    message:
      'No live signal has been traced for this block. Signal provenance is recorded while a ' +
      'deployment runs; a preview is not a deployment.',
  },
  ...overrides,
});

// ---------------------------------------------------------------------------
// Requirement 24.6: inputs, outputs, duration, failures
// ---------------------------------------------------------------------------

describe('Requirement 24.6: the recorded trace for a selected node', () => {
  it('carries one row per bound input port, with its recorded type and shape', () => {
    const trace = projectNodeTrace(tracePayload());

    expect(trace.executions).toHaveLength(1);
    expect(trace.executions[0].inputs).toEqual([
      {
        port: 'series',
        source: 'n-data',
        label: 'series',
        type: 'Series[float64]',
        shape: { type: 'Series', length: 300 },
        shapeText: 'Series[300]',
      },
      {
        port: 'window',
        source: 'n-const',
        label: 'window',
        type: 'int',
        shape: { type: 'scalar', value: 20 },
        shapeText: 'scalar 20',
      },
    ]);
  });

  it('keeps an input the run bound to no declared port, labelled by its source', () => {
    // A legacy plain-dict call site records no port view. Dropping the row would lose an
    // input the run genuinely bound; inventing a port name for it would be worse.
    const trace = projectNodeTrace(
      tracePayload({
        executions: [
          entry({
            inputs: [{ port: null, source: 'n-legacy', type: 'int', shape: { type: 'scalar', value: 3 } }],
          }),
        ],
      }),
    );

    const [input] = trace.executions[0].inputs;
    expect(input.port).toBeNull();
    expect(input.source).toBe('n-legacy');
    expect(input.label).toBe('n-legacy');
  });

  it('carries the recorded output', () => {
    const trace = projectNodeTrace(tracePayload());

    expect(trace.executions[0].output).toEqual({
      type: 'Series[float64]',
      shape: { type: 'Series', length: 300 },
      shapeText: 'Series[300]',
    });
  });

  it("carries the endpoint's summed duration, not a figure of its own", () => {
    // Two executions of one node: the endpoint sums them, because a node started twice in a
    // run spent time twice. The projection reports what it was given.
    const trace = projectNodeTrace(
      tracePayload({
        duration_ms: 20.25,
        executions: [entry({ duration_ms: 12.5 }), entry({ duration_ms: 7.75 })],
      }),
    );

    expect(trace.durationMs).toBe(20.25);
    expect(trace.durationText).toBe('20.25 ms');
    // …and it is the endpoint's figure even when it disagrees with the entries, because the
    // entries are not the source of truth for it.
    expect(trace.executions.map((item) => item.durationMs)).toEqual([12.5, 7.75]);
  });

  it('carries every failure the run recorded, not only this node’s', () => {
    const upstream = entry({
      node_id: 'n-data',
      status: 'fail',
      error_message: 'No candles for BTC/USDT at 15m',
      duration_ms: 3.0,
    });
    const trace = projectNodeTrace(
      tracePayload({
        status: 'not_executed',
        recorded: false,
        executions: [],
        failures: [upstream],
        blocking_failure: upstream,
        duration_ms: null,
      }),
    );

    expect(trace.failures).toHaveLength(1);
    expect(trace.failures[0].nodeId).toBe('n-data');
    expect(trace.failures[0].errorMessage).toBe('No candles for BTC/USDT at 15m');
  });

  it('reports a blocking failure somewhere else as upstream', () => {
    const upstream = entry({ node_id: 'n-data', status: 'fail', error_message: 'boom' });
    const trace = projectNodeTrace(
      tracePayload({ executions: [], failures: [upstream], blocking_failure: upstream }),
    );

    expect(trace.blockedUpstream).toBe(true);
    expect(trace.blockingFailure.nodeId).toBe('n-data');
  });

  it('reports this node’s own failure as its own', () => {
    const own = entry({ status: 'fail', error_message: 'window must be positive' });
    const trace = projectNodeTrace(
      tracePayload({ status: 'fail', executions: [own], failures: [own], blocking_failure: own }),
    );

    expect(trace.blockedUpstream).toBe(false);
    expect(trace.blockingFailure.errorMessage).toBe('window must be positive');
  });
});

// ---------------------------------------------------------------------------
// "Did not execute" is its own fact
// ---------------------------------------------------------------------------

describe('the status vocabulary', () => {
  it('distinguishes a node that never ran from one that ran', () => {
    const notExecuted = projectNodeTrace(
      tracePayload({ status: 'not_executed', recorded: false, executions: [], duration_ms: null }),
    );
    const ran = projectNodeTrace(tracePayload());

    expect(notExecuted.status).toBe(TRACE_STATUSES.NOT_EXECUTED);
    expect(notExecuted.recorded).toBe(false);
    expect(notExecuted.executions).toEqual([]);
    expect(ran.status).toBe(TRACE_STATUSES.SUCCESS);
    expect(ran.recorded).toBe(true);
    expect(notExecuted.statusLabel).not.toBe(ran.statusLabel);
  });

  it('has a word for each of the four statuses, and renders an unknown one raw', () => {
    for (const status of Object.values(TRACE_STATUSES)) {
      expect(TRACE_STATUS_LABELS[status]).toBeTruthy();
      expect(projectNodeTrace(tracePayload({ status })).statusLabel).toBe(
        TRACE_STATUS_LABELS[status],
      );
    }
    // A status word this build does not know is shown as it arrived, never mapped onto one of
    // the four. A trace that read `success` because the client did not recognise `aborted`
    // would be a passing verdict nobody made.
    const unknown = projectNodeTrace(tracePayload({ status: 'aborted' }));
    expect(unknown.status).toBe('aborted');
    expect(unknown.statusLabel).toBe('aborted');
  });

  it('defaults an absent status to not_executed rather than to success', () => {
    const trace = projectNodeTrace(tracePayload({ status: null }));

    expect(trace.status).toBe(TRACE_STATUSES.NOT_EXECUTED);
  });
});

// ---------------------------------------------------------------------------
// Unrecorded is not zero
// ---------------------------------------------------------------------------

describe('an unrecorded figure', () => {
  it('reads as the placeholder, never as 0 ms', () => {
    expect(describeDuration(null)).toBe(UNRECORDED_TEXT);
    expect(describeDuration(undefined)).toBe(UNRECORDED_TEXT);
    expect(describeDuration('12')).toBe(UNRECORDED_TEXT);
    expect(describeDuration(Number.NaN)).toBe(UNRECORDED_TEXT);
    expect(describeDuration(Number.POSITIVE_INFINITY)).toBe(UNRECORDED_TEXT);
    // A genuine zero is a number, and it is reported as one.
    expect(describeDuration(0)).toBe('0 ms');
  });

  it('leaves the duration unrecorded when the endpoint recorded none', () => {
    const trace = projectNodeTrace(tracePayload({ duration_ms: null }));

    expect(trace.durationMs).toBeNull();
    expect(trace.durationText).toBe(UNRECORDED_TEXT);
  });
});

// ---------------------------------------------------------------------------
// Shapes, as the tracer records them
// ---------------------------------------------------------------------------

describe('describeShape', () => {
  it('reads each shape the tracer writes', () => {
    expect(describeShape({ type: 'Series', length: 300 })).toBe('Series[300]');
    expect(describeShape({ type: 'DataFrame', shape: [300, 12] })).toBe('DataFrame[300\u00d712]');
    expect(describeShape({ type: 'scalar', value: 20 })).toBe('scalar 20');
    expect(describeShape({ type: 'scalar', value: true })).toBe('scalar true');
    expect(describeShape({ type: 'NoneType' })).toBe('NoneType');
  });

  it('never invents a figure for a shape the run did not record', () => {
    expect(describeShape(null)).toBe(UNRECORDED_TEXT);
    expect(describeShape(undefined)).toBe(UNRECORDED_TEXT);
    expect(describeShape([])).toBe(UNRECORDED_TEXT);
    expect(describeShape({})).toBe(UNRECORDED_TEXT);
    expect(describeShape({ length: 300 })).toBe(UNRECORDED_TEXT);
    // A scalar whose value the recorder could not represent is the type and no number.
    expect(describeShape({ type: 'scalar', value: null })).toBe(`scalar ${UNRECORDED_TEXT}`);
    expect(describeShape({ type: 'scalar', value: Number.NaN })).toBe(`scalar ${UNRECORDED_TEXT}`);
  });
});

// ---------------------------------------------------------------------------
// One message source
// ---------------------------------------------------------------------------

describe('every sentence on screen is the server’s', () => {
  it('renders the summary verbatim', () => {
    const summary =
      "This block did not execute. The run stopped at 'n-data': No candles for BTC/USDT at 15m";
    const trace = projectNodeTrace(tracePayload({ summary }));

    expect(trace.summary).toBe(summary);
  });

  it('renders each recorded condition with the sentence the endpoint composed', () => {
    const trace = projectNodeTrace(
      tracePayload({
        conditions: [
          {
            node_id: 'n-div',
            code: 'DIVISION_BY_ZERO',
            bar: 34,
            bars_affected: 3,
            detail: 'denominator was zero',
            display: 'DIVISION_BY_ZERO on 3 bars from bar 34: denominator was zero',
          },
        ],
      }),
    );

    expect(trace.conditions[0].display).toBe(
      'DIVISION_BY_ZERO on 3 bars from bar 34: denominator was zero',
    );
    expect(trace.conditions[0].bar).toBe(34);
    expect(trace.conditions[0].barsAffected).toBe(3);
  });

  it('falls back to the code when a record carries no sentence, and reconstructs nothing', () => {
    const trace = projectNodeTrace(
      tracePayload({
        conditions: [{ code: 'NON_FINITE', bar: 7, bars_affected: 1, detail: 'inf produced' }],
      }),
    );

    // The code alone, not a sentence assembled in the browser out of four fields.
    expect(trace.conditions[0].display).toBe('NON_FINITE');
  });

  it('has no sentence of its own when the endpoint sent none', () => {
    expect(projectNodeTrace(tracePayload({ summary: null })).summary).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Fail soft
// ---------------------------------------------------------------------------

describe('an unreadable trace', () => {
  it('projects to null rather than throwing', () => {
    // Throwing would take the preview's values off screen to report that the *explanation* was
    // unreadable. "Nothing was recorded" and "this block produced nothing" stay separate.
    for (const raw of [null, undefined, 'trace', 42, [], {}, { status: 'success' }]) {
      expect(projectNodeTrace(raw)).toBeNull();
    }
  });

  it('survives every field being absent as long as the node is named', () => {
    const trace = projectNodeTrace({ node_id: 'n-ema' });

    expect(trace.nodeId).toBe('n-ema');
    expect(trace.status).toBe(TRACE_STATUSES.NOT_EXECUTED);
    expect(trace.executions).toEqual([]);
    expect(trace.failures).toEqual([]);
    expect(trace.conditions).toEqual([]);
    expect(trace.executedNodes).toEqual([]);
    expect(trace.blockingFailure).toBeNull();
    expect(trace.durationText).toBe(UNRECORDED_TEXT);
    expect(trace.signalProvenance.available).toBe(false);
  });

  it('survives a malformed entry inside an otherwise readable trace', () => {
    const trace = projectNodeTrace(
      tracePayload({ executions: [null, entry({ inputs: 'not a list', output: 7 })] }),
    );

    expect(trace.executions).toHaveLength(2);
    expect(trace.executions[0].inputs).toEqual([]);
    expect(trace.executions[1].inputs).toEqual([]);
    expect(trace.executions[1].output.shapeText).toBe(UNRECORDED_TEXT);
  });

  it('is what hasTrace answers about', () => {
    expect(hasTrace(projectNodeTrace(tracePayload()))).toBe(true);
    expect(hasTrace(null)).toBe(false);
    expect(hasTrace({})).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Both wire paths
// ---------------------------------------------------------------------------

describe('where the trace is read from', () => {
  it('reads a 200 body', () => {
    expect(traceFromPreviewBody({ node_id: 'n-ema', trace: tracePayload() }).nodeId).toBe('n-ema');
    expect(traceFromPreviewBody({ node_id: 'n-ema' })).toBeNull();
    expect(traceFromPreviewBody(null)).toBeNull();
  });

  it('reads a refusal that carries one — the branch that matters most', () => {
    // A run that produced nothing is exactly the run whose trace answers "why did nothing
    // happen?", and `PREVIEW_EXECUTION_FAILED` is the endpoint's branch for it.
    const failed = {
      status: 422,
      data: {
        detail: {
          error: 'PREVIEW_EXECUTION_FAILED',
          message: 'Node n-div: division by zero',
          trace: tracePayload({ status: 'fail' }),
        },
      },
    };

    expect(traceFromPreviewFailure(failed).status).toBe(TRACE_STATUSES.FAIL);
  });

  it('reads the axios-nested shape too, so the two cannot disagree', () => {
    const failed = { response: { status: 422, data: { detail: { trace: tracePayload() } } } };

    expect(traceFromPreviewFailure(failed).nodeId).toBe('n-ema');
  });

  it('reads a body whose detail is the payload itself', () => {
    const failed = { status: 422, data: { trace: tracePayload() } };

    expect(traceFromPreviewFailure(failed).nodeId).toBe('n-ema');
  });

  it('answers null for a refusal that executed nothing', () => {
    // A graph the validator refused never reached an executor, so there is nothing recorded to
    // show, and saying so is honest rather than a gap.
    for (const error of [
      null,
      new Error('network down'),
      { status: 422, data: { detail: { error: 'STRATEGY_VALIDATION_FAILED' } } },
      { status: 503, data: { detail: { error: 'PREVIEW_DATA_UNAVAILABLE' } } },
    ]) {
      expect(traceFromPreviewFailure(error)).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// Live signal provenance
// ---------------------------------------------------------------------------

describe('live signal provenance', () => {
  it('projects the records signal_trace_engine held', () => {
    const trace = projectNodeTrace(
      tracePayload({
        signal_provenance: {
          available: true,
          traces_seen: 2,
          message: '1 live signal trace(s) recorded for this block.',
          records: [
            {
              trace_id: 't_1',
              recorded_at: '2024-01-01T00:05:00',
              status: 'executed',
              final_decision: 'BUY',
              symbol: 'BTC/USDT',
              latency_ms: 42.0,
              executions: [
                {
                  node_type: 'indicator',
                  label: 'EMA 20',
                  duration_ms: 1.5,
                  status: 'success',
                  error_message: null,
                  cache_hit: true,
                  inputs: [{ key: 'series', value: '…', dtype: 'Series' }],
                  outputs: [{ key: 'value', value: '68000.0', dtype: 'float' }],
                },
              ],
            },
          ],
        },
      }),
    );

    expect(trace.signalProvenance.available).toBe(true);
    expect(trace.signalProvenance.tracesSeen).toBe(2);
    expect(trace.signalProvenance.records[0].symbol).toBe('BTC/USDT');
    expect(trace.signalProvenance.records[0].executions[0].cacheHit).toBe(true);
    expect(trace.signalProvenance.records[0].executions[0].durationText).toBe('1.5 ms');
  });

  it('carries the honest empty answer as a sentence, not as an absent key', () => {
    const trace = projectNodeTrace(
      tracePayload({
        signal_provenance: {
          available: false,
          records: [],
          traces_seen: 0,
          message: 'The signal trace store could not be read, so no live provenance is shown.',
        },
      }),
    );

    expect(trace.signalProvenance.available).toBe(false);
    expect(trace.signalProvenance.records).toEqual([]);
    expect(trace.signalProvenance.message).toContain('could not be read');
  });
});

// ---------------------------------------------------------------------------
// SB-06 / Requirement 12.1: no credential and no venue can travel
// ---------------------------------------------------------------------------

describe('nothing identifying or secret reaches the render model', () => {
  const FORBIDDEN = [
    'exchange',
    'exchange_id',
    'exchange_account_id',
    'api_key',
    'api_secret',
    'secret',
    'passphrase',
    'password',
    'token',
    'credential',
    'venue',
  ];

  it('drops every credential and venue key, wherever it is added upstream', () => {
    // The projection is an allow-list by construction, so this is a sweep over the places a
    // key could be introduced rather than a review convention. Each value is a marker string
    // that could only appear in the output if the key had been copied.
    const poison = (base) => {
      const out = { ...base };
      for (const key of FORBIDDEN) out[key] = `LEAK_${key.toUpperCase()}`;
      return out;
    };

    const payload = poison(
      tracePayload({
        executions: [
          poison(
            entry({
              inputs: [
                poison({
                  port: 'series',
                  source: 'n-data',
                  type: 'Series',
                  shape: { type: 'Series', length: 4 },
                }),
              ],
              output: poison({ type: 'Series', shape: { type: 'Series', length: 4 } }),
            }),
          ),
        ],
        failures: [poison(entry({ status: 'fail', error_message: 'boom' }))],
        blocking_failure: poison(entry({ status: 'fail', error_message: 'boom' })),
        conditions: [poison({ code: 'NON_FINITE', display: 'NON_FINITE on 1 bar' })],
        signal_provenance: poison({
          available: true,
          traces_seen: 1,
          message: 'one',
          records: [
            poison({
              trace_id: 't_1',
              symbol: 'BTC/USDT',
              executions: [poison({ node_type: 'indicator', duration_ms: 1 })],
            }),
          ],
        }),
      }),
    );

    const rendered = JSON.stringify(projectNodeTrace(payload));
    for (const key of FORBIDDEN) {
      expect(rendered).not.toContain(`LEAK_${key.toUpperCase()}`);
    }
    // …and the shape is still the one the panel renders, so this did not pass by projecting
    // nothing at all.
    const trace = projectNodeTrace(payload);
    expect(trace.executions[0].inputs[0].port).toBe('series');
    expect(trace.signalProvenance.records[0].symbol).toBe('BTC/USDT');
  });
});

// ---------------------------------------------------------------------------
// The structural half: no fourth opinion about the run
// ---------------------------------------------------------------------------

describe('the module records and computes nothing', () => {
  const source = readFileSync(
    path.resolve(__dirname, '..', '..', 'src', 'lib', 'nodeTrace.js'),
    'utf8',
  );
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

  it('starts no clock and keeps no store of its own', () => {
    // `design.md` names two trace stores and says both are reused. A third one recording in the
    // browser would be a store nobody could correlate with a run, and a timer here would be
    // measuring the network rather than the node.
    for (const token of [
      'performance.now',
      'Date.now',
      'new Date(',
      'setTimeout',
      'setInterval',
      'localStorage',
      'sessionStorage',
    ]) {
      expect(code).not.toContain(token);
    }
  });

  it('sums nothing and re-derives no status', () => {
    for (const token of ['.reduce(', 'durationMs +', '+= ', 'Math.max', 'Math.min']) {
      expect(code).not.toContain(token);
    }
  });

  it('imports nothing: it is a projection, so it has no client and no engine', () => {
    expect(code).not.toMatch(/^\s*import\s/m);
  });
});
