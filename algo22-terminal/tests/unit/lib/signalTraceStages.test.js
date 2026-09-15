/**
 * `lib/signalTraceStages.js` — vyomquant-ui-redesign task 21.1. design.md §10.1, §10.2.
 * Requirements 9.1, 9.2, 19.3.
 *
 * The example suite. Tasks 21.2 and 21.3 add Properties 15 and 16 over the whole input space;
 * this file pins the specific answers the requirements and the design name, so a change that
 * keeps the properties true while (say) reclassifying an empty `dag_nodes` as `pending` still
 * fails here.
 *
 * Payload shapes are the backend's, read off `signal_service.build_signal_detail`:
 * `trace.dag_nodes` is `{source, nodes}` (the wrapper matters), `trace.ml_inference` is
 * `{source, applicable, detail}`, `trace.execution` is `{source, outcome, exchange_response}`,
 * and `timeline` is a list of `{event, timestamp, data}` over the six names
 * `signal_service.SIGNAL_TIMELINE_EVENTS` declares.
 */

import { describe, expect, it } from 'vitest';

import {
  EVENT_EXCHANGE_RESPONSE,
  EVENT_EXECUTED,
  EVENT_ORDER_CREATED,
  EVENT_POSITION_UPDATED,
  EVENT_RISK_EVALUATED,
  EVENT_SIGNAL_GENERATED,
  REASON_DEGRADED_FALLBACK,
  REASON_ML_NOT_APPLICABLE,
  REASON_NOT_YET_REACHED,
  REASON_NO_LOGIC_NODE,
  REASON_TRACE_RETENTION,
  SIGNAL_TIMELINE_EVENTS,
  SIGNAL_TRACE_STAGES,
  SIGNAL_TRACE_STAGE_IDS,
  STAGE_BLOCKED,
  STAGE_COMPLETE,
  STAGE_EXECUTION,
  STAGE_INDICATORS,
  STAGE_LOGIC,
  STAGE_MARKET_DATA,
  STAGE_MODEL,
  STAGE_NOT_APPLICABLE,
  STAGE_NOT_AVAILABLE,
  STAGE_ORDER_DECISION,
  STAGE_PENDING,
  STAGE_POSITION_UPDATE,
  STAGE_SIGNAL,
  STAGE_STATES,
  STAGE_SUBMISSION,
  buildSignalTraceStages,
  signalTraceDegradation,
} from '../../../src/lib/signalTraceStages';

/** The nine ids, in canonical order, spelled out rather than read from the module. */
const CANONICAL_IDS = [
  'market-data',
  'indicators',
  'model',
  'logic',
  'signal',
  'order-decision',
  'submission',
  'execution',
  'position-update',
];

const stageById = (result, id) => result.stages.find((stage) => stage.id === id);

const event = (name, timestamp, data = {}) => ({ event: name, timestamp, data });

/** A dag node in `_dag_node_entry`'s shape, with only the keys a test needs. */
const node = (overrides) => ({
  node_id: 'n1',
  source: 'signal_trace_engine',
  node_type: 'INDICATOR',
  status: 'pass',
  execution_ms: null,
  error: null,
  ...overrides,
});

/** A signal that generated, passed risk, submitted, filled and moved a position. */
const fullPayload = () => ({
  signal: {
    decision: 'BUY',
    market_info: { symbol: 'BTC/USDT', timeframe: '15m' },
    indicators: { rsi_14: 28.4, ema_50: 63120 },
  },
  trace: {
    dag_nodes: {
      source: 'signal_trace_engine',
      nodes: [
        node({ node_id: 'rsi', node_type: 'INDICATOR', execution_ms: 7 }),
        node({ node_id: 'gate', node_type: 'LOGIC', execution_ms: 1 }),
      ],
    },
    ml_inference: { source: 'signal_trace_engine', applicable: true, detail: { model_id: 'm-1', confidence: 0.71 } },
    risk_validation: { source: 'signals_row', detail: { passed: true, reason: null, position_size: 0.014 } },
    execution: {
      source: 'signals_row',
      outcome: {
        order_id: 'o-1',
        execution_price: 63118,
        filled_quantity: 0.014,
        latency_ms: 86,
        failure_reason: null,
        executed_at: '2024-05-01T12:04:03Z',
      },
      exchange_response: { status: 'FILLED' },
    },
  },
  timeline: [
    event(EVENT_SIGNAL_GENERATED, '2024-05-01T12:04:00Z', { decision: 'BUY' }),
    event(EVENT_RISK_EVALUATED, '2024-05-01T12:04:01Z', { risk_passed: true, position_size: 0.014 }),
    event(EVENT_ORDER_CREATED, '2024-05-01T12:04:01Z', { order_id: 'o-1', quantity: 0.014 }),
    event(EVENT_EXCHANGE_RESPONSE, '2024-05-01T12:04:02Z', { exchange_order_id: 'x-9F2', order_status: 'FILLED' }),
    event(EVENT_EXECUTED, '2024-05-01T12:04:03Z', { trade_id: 't-1', pnl: 4.2 }),
    event(EVENT_POSITION_UPDATED, '2024-05-01T12:04:03Z', {
      symbol: 'BTC/USDT',
      direction: 'BUY',
      quantity_delta: 0.014,
      average_price: 63118,
      resulting_position: null,
      not_available: ['resulting_position'],
      not_available_reason: 'The execution update reports the position CHANGE only.',
    }),
  ],
  lifecycle_state_source: 'canonical',
  degraded: null,
});

// ── The canonical list is the projection (Requirements 9.1, 9.2) ─────────────────────────

describe('the nine stages are structural', () => {
  it('declares nine stages, numbered 1..9, in Requirement 9.1 order', () => {
    expect(SIGNAL_TRACE_STAGE_IDS).toEqual(CANONICAL_IDS);
    expect(SIGNAL_TRACE_STAGES.map((stage) => stage.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(Object.isFrozen(SIGNAL_TRACE_STAGES)).toBe(true);
    expect(Object.isFrozen(SIGNAL_TRACE_STAGE_IDS)).toBe(true);
  });

  it('returns all nine, in order, for an empty payload', () => {
    const result = buildSignalTraceStages({});
    expect(result.stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
    expect(result.stages).toHaveLength(9);
  });

  it('returns all nine, in order, for a partial payload', () => {
    const result = buildSignalTraceStages({
      signal: { decision: 'BUY', market_info: { symbol: 'ETH/USDT' } },
      timeline: [event(EVENT_SIGNAL_GENERATED, '2024-05-01T12:00:00Z', { decision: 'BUY' })],
    });
    expect(result.stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
    expect(stageById(result, STAGE_MARKET_DATA).state).toBe(STAGE_COMPLETE);
    expect(stageById(result, STAGE_SIGNAL).state).toBe(STAGE_COMPLETE);
    expect(stageById(result, STAGE_SUBMISSION).state).toBe(STAGE_PENDING);
  });

  it('returns all nine, in order, for a full payload', () => {
    const result = buildSignalTraceStages(fullPayload());
    expect(result.stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
    expect(result.stages.every((stage) => STAGE_STATES.includes(stage.state))).toBe(true);
  });

  it('adds no stage for an unknown event type and removes none for a missing one', () => {
    const baseline = buildSignalTraceStages(fullPayload());

    const withUnknown = fullPayload();
    withUnknown.timeline.push(
      event('POSITION_LIQUIDATED', '2024-05-01T12:05:00Z'),
      event('position_updated', '2024-05-01T12:05:01Z'), // the lower-case spelling never fires
      event('MANUAL_RECONCILIATION_REQUIRED', '2024-05-01T12:05:02Z'),
      event('', '2024-05-01T12:05:03Z'),
      { timestamp: '2024-05-01T12:05:04Z' },
    );
    const unknownResult = buildSignalTraceStages(withUnknown);

    expect(unknownResult.stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
    expect(unknownResult.stages.map((stage) => stage.state)).toEqual(
      baseline.stages.map((stage) => stage.state),
    );

    // And dropping every event still leaves nine rows.
    const noEvents = fullPayload();
    noEvents.timeline = [];
    expect(buildSignalTraceStages(noEvents).stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
  });

  it('collapses a duplicated event rather than emitting a second row', () => {
    const duplicated = fullPayload();
    duplicated.timeline = [...duplicated.timeline, ...duplicated.timeline];
    const result = buildSignalTraceStages(duplicated);
    expect(result.stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
  });

  it('ignores payload order entirely', () => {
    const reversed = fullPayload();
    reversed.timeline = [...reversed.timeline].reverse();
    expect(buildSignalTraceStages(reversed).stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
  });
});

// ── The five states (design.md §10.2) ───────────────────────────────────────────────────

describe('the five-state model', () => {
  it('names exactly the five states', () => {
    expect([...STAGE_STATES]).toEqual([
      'complete',
      'blocked',
      'pending',
      'not-applicable',
      'not-available',
    ]);
  });

  it('reaches complete for every stage of a signal that ran to a position update', () => {
    const result = buildSignalTraceStages(fullPayload());
    expect(result.stages.map((stage) => stage.state)).toEqual(Array(9).fill(STAGE_COMPLETE));
  });

  it('reaches blocked at stage 6 from the timeline risk verdict, with the server reason', () => {
    const payload = fullPayload();
    payload.timeline = payload.timeline.slice(0, 2);
    payload.timeline[1] = event(EVENT_RISK_EVALUATED, '2024-05-01T12:04:01Z', {
      risk_passed: false,
      risk_reason: 'Daily loss limit reached',
    });
    payload.trace.risk_validation = { source: 'signals_row', detail: { passed: false, reason: 'Daily loss limit reached' } };
    payload.trace.execution.outcome.failure_reason = null;

    const stage = stageById(buildSignalTraceStages(payload), STAGE_ORDER_DECISION);
    expect(stage.state).toBe(STAGE_BLOCKED);
    expect(stage.reason).toBe('Daily loss limit reached');
  });

  it('reaches blocked at stage 6 from the section blocked flag alone', () => {
    const payload = fullPayload();
    payload.timeline = payload.timeline.slice(0, 1);
    payload.trace.risk_validation = { source: 'signal_trace_engine', detail: { blocked: true, reason: 'Exposure cap' } };
    payload.trace.execution.outcome.failure_reason = null;

    const stage = stageById(buildSignalTraceStages(payload), STAGE_ORDER_DECISION);
    expect(stage.state).toBe(STAGE_BLOCKED);
    expect(stage.reason).toBe('Exposure cap');
  });

  it('reaches blocked at stage 7 on a REJECTED order status', () => {
    const payload = fullPayload();
    payload.timeline = payload.timeline.slice(0, 4);
    payload.timeline[3] = event(EVENT_EXCHANGE_RESPONSE, '2024-05-01T12:04:02Z', {
      exchange_order_id: 'x-9F2',
      order_status: 'REJECTED',
    });
    payload.trace.execution.outcome.failure_reason = null;

    const stage = stageById(buildSignalTraceStages(payload), STAGE_SUBMISSION);
    expect(stage.state).toBe(STAGE_BLOCKED);
    expect(stage.reason).toBe('REJECTED');
  });

  it('reaches pending only for a stage that has not happened yet', () => {
    const payload = fullPayload();
    payload.timeline = payload.timeline.slice(0, 3); // through ORDER_CREATED
    payload.trace.execution.outcome.failure_reason = null;
    const result = buildSignalTraceStages(payload);

    const submission = stageById(result, STAGE_SUBMISSION);
    expect(submission.state).toBe(STAGE_PENDING);
    expect(submission.reason).toBe(REASON_NOT_YET_REACHED);
    expect(submission.backed).toBe(false);
    expect(stageById(result, STAGE_EXECUTION).state).toBe(STAGE_PENDING);
    expect(stageById(result, STAGE_POSITION_UPDATE).state).toBe(STAGE_PENDING);
  });

  it('reaches not-applicable for ml_inference.applicable === false, never not-available', () => {
    const payload = fullPayload();
    payload.trace.ml_inference = { source: 'signals_row', applicable: false, detail: null };

    const stage = stageById(buildSignalTraceStages(payload), STAGE_MODEL);
    expect(stage.state).toBe(STAGE_NOT_APPLICABLE);
    expect(stage.state).not.toBe(STAGE_NOT_AVAILABLE);
    expect(stage.state).not.toBe(STAGE_PENDING);
    expect(stage.reason).toBe(REASON_ML_NOT_APPLICABLE);
    // The server said so, so the stage IS backed — that is what makes it not a gap.
    expect(stage.backed).toBe(true);
  });

  it('does not reach not-applicable from an absent ml_inference section', () => {
    const payload = fullPayload();
    delete payload.trace.ml_inference;
    expect(stageById(buildSignalTraceStages(payload), STAGE_MODEL).state).toBe(STAGE_PENDING);
  });

  it('is the only stage that can be not-applicable', () => {
    const payloads = [{}, fullPayload()];
    for (const payload of payloads) {
      const notApplicable = buildSignalTraceStages(payload).stages.filter(
        (stage) => stage.state === STAGE_NOT_APPLICABLE,
      );
      expect(notApplicable.map((stage) => stage.id)).not.toContain(STAGE_LOGIC);
      expect(notApplicable.map((stage) => stage.id)).not.toContain(STAGE_POSITION_UPDATE);
    }
  });

  it('reaches not-available at stage 4 with the retention reason when dag_nodes is empty', () => {
    const payload = fullPayload();
    payload.trace.dag_nodes = { source: 'signals_row', nodes: [] };

    const stage = stageById(buildSignalTraceStages(payload), STAGE_LOGIC);
    expect(stage.state).toBe(STAGE_NOT_AVAILABLE);
    expect(stage.state).not.toBe(STAGE_PENDING);
    expect(stage.reason).toBe(REASON_TRACE_RETENTION);
    expect(stage.reason).toMatch(/about an hour/);
  });

  it('gives stage 4 a different reason when nodes were retained but none is LOGIC', () => {
    const payload = fullPayload();
    payload.trace.dag_nodes = { source: 'signal_trace_engine', nodes: [node({ node_type: 'INDICATOR' })] };
    payload.signal.decision = null;

    const stage = stageById(buildSignalTraceStages(payload), STAGE_LOGIC);
    expect(stage.state).toBe(STAGE_NOT_AVAILABLE);
    expect(stage.reason).toBe(REASON_NO_LOGIC_NODE);
  });

  it('completes stage 4 on signal.decision when nodes exist but carry no LOGIC node', () => {
    const payload = fullPayload();
    payload.trace.dag_nodes = { source: 'signals_row', nodes: [node({ node_type: 'INDICATOR' })] };

    const stage = stageById(buildSignalTraceStages(payload), STAGE_LOGIC);
    expect(stage.state).toBe(STAGE_COMPLETE);
    expect(stage.backed).toBe(true);
  });

  it('reaches every one of the five states across the suite', () => {
    const seen = new Set();

    const complete = buildSignalTraceStages(fullPayload());
    complete.stages.forEach((stage) => seen.add(stage.state));

    const notApplicable = fullPayload();
    notApplicable.trace.ml_inference = { source: 'signals_row', applicable: false, detail: null };
    buildSignalTraceStages(notApplicable).stages.forEach((stage) => seen.add(stage.state));

    const pending = fullPayload();
    pending.timeline = pending.timeline.slice(0, 1);
    pending.trace.execution.outcome.failure_reason = null;
    buildSignalTraceStages(pending).stages.forEach((stage) => seen.add(stage.state));

    const blocked = fullPayload();
    blocked.timeline = [
      blocked.timeline[0],
      event(EVENT_RISK_EVALUATED, '2024-05-01T12:04:01Z', { risk_passed: false, risk_reason: 'Cap' }),
    ];
    blocked.trace.risk_validation = { source: 'signals_row', detail: { passed: false, reason: 'Cap' } };
    blocked.trace.execution.outcome.failure_reason = null;
    buildSignalTraceStages(blocked).stages.forEach((stage) => seen.add(stage.state));

    expect([...seen].sort()).toEqual([...STAGE_STATES].sort());
  });
});

// ── A blocked signal's stages 6–9 (the pending-forever trap) ─────────────────────────────

describe('stages downstream of a block', () => {
  it('renders them not-available naming the blocking stage, never pending forever', () => {
    const payload = fullPayload();
    payload.timeline = [
      payload.timeline[0],
      event(EVENT_RISK_EVALUATED, '2024-05-01T12:04:01Z', {
        risk_passed: false,
        risk_reason: 'Daily loss limit reached',
      }),
    ];
    payload.trace.risk_validation = { source: 'signals_row', detail: { passed: false, reason: 'Daily loss limit reached' } };
    payload.trace.execution.outcome.failure_reason = null;

    const result = buildSignalTraceStages(payload);
    expect(stageById(result, STAGE_ORDER_DECISION).state).toBe(STAGE_BLOCKED);

    for (const id of [STAGE_SUBMISSION, STAGE_EXECUTION, STAGE_POSITION_UPDATE]) {
      const stage = stageById(result, id);
      expect(stage.state).toBe(STAGE_NOT_AVAILABLE);
      expect(stage.state).not.toBe(STAGE_PENDING);
      expect(stage.reason).toContain('Order decision');
      expect(stage.reason).toContain('will never occur');
    }
  });

  it('leaves stages before the block alone', () => {
    const payload = fullPayload();
    payload.timeline = [
      payload.timeline[0],
      event(EVENT_RISK_EVALUATED, '2024-05-01T12:04:01Z', { risk_passed: false, risk_reason: 'Cap' }),
    ];
    payload.trace.risk_validation = { source: 'signals_row', detail: { passed: false } };
    payload.trace.execution.outcome.failure_reason = null;

    const result = buildSignalTraceStages(payload);
    for (const id of [STAGE_MARKET_DATA, STAGE_INDICATORS, STAGE_MODEL, STAGE_LOGIC, STAGE_SIGNAL]) {
      expect(stageById(result, id).state).toBe(STAGE_COMPLETE);
    }
  });

  it('blocks stage 8 on a failure_reason with no EXECUTED event', () => {
    const payload = fullPayload();
    payload.timeline = payload.timeline.slice(0, 4);
    payload.trace.execution.outcome.failure_reason = 'Insufficient balance at venue';

    const stage = stageById(buildSignalTraceStages(payload), STAGE_EXECUTION);
    expect(stage.state).toBe(STAGE_BLOCKED);
    expect(stage.reason).toBe('Insufficient balance at venue');
    expect(stageById(buildSignalTraceStages(payload), STAGE_POSITION_UPDATE).state).toBe(
      STAGE_NOT_AVAILABLE,
    );
  });
});

// ── Stage 9 is BC-6's real event (task 12.6) ─────────────────────────────────────────────

describe('stage 9 reads POSITION_UPDATED', () => {
  it('names the six backend event spellings', () => {
    expect([...SIGNAL_TIMELINE_EVENTS]).toEqual([
      'SIGNAL_GENERATED',
      'RISK_EVALUATED',
      'ORDER_CREATED',
      'EXCHANGE_RESPONSE',
      'EXECUTED',
      'POSITION_UPDATED',
    ]);
  });

  it('completes from the event, carrying the change and the server not-available reason', () => {
    const stage = stageById(buildSignalTraceStages(fullPayload()), STAGE_POSITION_UPDATE);
    expect(stage.state).toBe(STAGE_COMPLETE);
    expect(stage.backed).toBe(true);
    expect(stage.timestamp).toBe('2024-05-01T12:04:03Z');
    expect(stage.summary).toContain('BTC/USDT');
    expect(stage.reason).toBe('The execution update reports the position CHANGE only.');
    expect(stage.detail.not_available).toEqual(['resulting_position']);
  });

  it('is not a hardcoded not-available: absent event alone decides', () => {
    const payload = fullPayload();
    payload.timeline = payload.timeline.filter((entry) => entry.event !== EVENT_POSITION_UPDATED);
    expect(stageById(buildSignalTraceStages(payload), STAGE_POSITION_UPDATE).state).toBe(
      STAGE_PENDING,
    );
  });
});

// ── degraded and lifecycle_state_source (Requirement 19.3) ───────────────────────────────

describe('degradation is reported, not styled', () => {
  it('reports no degradation when the server sends degraded: null', () => {
    const result = buildSignalTraceStages(fullPayload());
    expect(result.degraded).toBeNull();
    expect(result.isDegraded).toBe(false);
    expect(result.lifecycleStateSource).toBe('canonical');
  });

  it('surfaces the server reason, the migration and the unrepresentable states', () => {
    const payload = fullPayload();
    payload.lifecycle_state_source = 'legacy_status_map';
    payload.degraded = {
      migration: '005b_signal_lifecycle_and_idempotency.sql',
      reason: 'public.signals does not carry order_lifecycle_state.',
      unrepresentable_lifecycle_states: ['GENERATED', 'PARTIALLY_EXECUTED', 'CLOSED'],
    };

    const result = buildSignalTraceStages(payload);
    expect(result.isDegraded).toBe(true);
    expect(result.lifecycleStateSource).toBe('legacy_status_map');
    expect(result.degraded.message).toBe('public.signals does not carry order_lifecycle_state.');
    expect(result.degraded.migration).toBe('005b_signal_lifecycle_and_idempotency.sql');
    expect(result.degraded.unrepresentableLifecycleStates).toEqual([
      'GENERATED',
      'PARTIALLY_EXECUTED',
      'CLOSED',
    ]);
    expect(result.degraded.lifecycleStateSource).toBe('legacy_status_map');
  });

  it('falls back to the design sentence when degraded carries no reason', () => {
    expect(signalTraceDegradation({ degraded: {} }).message).toBe(REASON_DEGRADED_FALLBACK);
  });

  it('carries no colour and no styling decision', () => {
    const serialised = JSON.stringify(buildSignalTraceStages(fullPayload()));
    expect(serialised).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(serialised).not.toMatch(/rgba?\(/i);
  });
});

// ── Total over garbage ──────────────────────────────────────────────────────────────────

describe('total over garbage', () => {
  const GARBAGE = [
    undefined,
    null,
    0,
    -1,
    NaN,
    '',
    'signal',
    true,
    false,
    [],
    [1, 2, 3],
    {},
    { signal: null, trace: null, timeline: null, degraded: null },
    { signal: 'nope', trace: 'nope', timeline: 'nope', degraded: 'nope' },
    { signal: [], trace: [], timeline: {}, degraded: [] },
    { timeline: [null, undefined, 0, 'EXECUTED', [], {}] },
    { trace: { dag_nodes: [], ml_inference: [], risk_validation: 7, execution: null } },
    { trace: { dag_nodes: { nodes: 'nope' }, ml_inference: { applicable: 'false' } } },
    { trace: { dag_nodes: { nodes: [null, 1, 'x'] } } },
    { signal: { market_info: [], indicators: [], decision: 42 } },
    { signal: { market_info: {}, indicators: {} } },
    { degraded: { unrepresentable_lifecycle_states: 'nope' } },
    { degraded: { reason: '   ', migration: 5 } },
    { lifecycle_state_source: 99 },
    { timeline: [{ event: EVENT_EXECUTED, timestamp: 5, data: 'nope' }] },
    { timeline: [{ event: EVENT_POSITION_UPDATED, data: null }] },
    { timeline: [{ event: EVENT_ORDER_CREATED, data: { order_id: {} } }] },
  ];

  it('answers with nine ordered stages for every garbage input, and never throws', () => {
    for (const input of GARBAGE) {
      const result = buildSignalTraceStages(input);
      expect(result.stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
      expect(result.stages.every((stage) => STAGE_STATES.includes(stage.state))).toBe(true);
      expect(typeof result.isDegraded).toBe('boolean');
    }
  });

  it('never throws from signalTraceDegradation either', () => {
    for (const input of GARBAGE) {
      expect(() => signalTraceDegradation(input)).not.toThrow();
    }
  });

  it('freezes what it returns, so a page cannot mutate the projection', () => {
    const result = buildSignalTraceStages(fullPayload());
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.stages)).toBe(true);
    expect(result.stages.every((stage) => Object.isFrozen(stage))).toBe(true);
    expect(Object.isFrozen(signalTraceDegradation({ degraded: { reason: 'x' } }))).toBe(true);
  });

  it('does not mutate the payload it was given', () => {
    const payload = fullPayload();
    const before = JSON.stringify(payload);
    buildSignalTraceStages(payload);
    expect(JSON.stringify(payload)).toBe(before);
  });

  it('reports a null latency rather than a fabricated zero', () => {
    const payload = fullPayload();
    payload.trace.dag_nodes.nodes = [node({ node_type: 'LOGIC', execution_ms: null })];
    payload.trace.execution.outcome.latency_ms = null;
    const result = buildSignalTraceStages(payload);
    expect(stageById(result, STAGE_LOGIC).latencyMs).toBeNull();
    expect(stageById(result, STAGE_EXECUTION).latencyMs).toBeNull();
    expect(stageById(result, STAGE_MARKET_DATA).latencyMs).toBeNull();
  });

  it('sums reported node latency for the stages that have nodes behind them', () => {
    const result = buildSignalTraceStages(fullPayload());
    expect(stageById(result, STAGE_INDICATORS).latencyMs).toBe(8);
    expect(stageById(result, STAGE_LOGIC).latencyMs).toBe(1);
    expect(stageById(result, STAGE_EXECUTION).latencyMs).toBe(86);
  });
});
