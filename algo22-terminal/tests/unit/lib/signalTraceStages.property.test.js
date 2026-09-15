/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign — Property 15 and Property 16
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Tasks 21.2 and 21.3. `design.md` §10.1, §10.2.
 *
 *   * **Property 15: The nine trace stages are always all present, in order.**
 *     **Validates: Requirements 9.1**
 *   * **Property 16: A stage's state is a function of its own backing record.**
 *     **Validates: Requirements 9.2**
 *
 * Both are asserted over `src/lib/signalTraceStages.js`, the pure projection
 * `pages/SignalTrace.jsx` renders. `tests/unit/lib/signalTraceStages.test.js` already pins
 * the specific answers the design names — the retention reason on an empty `dag_nodes`, the
 * lower-case `position_updated` spelling never firing, stage 9 reading BC-6's event. This
 * file does the other half: it generalises over the input space, and nothing here restates
 * an example the sibling suite already holds.
 *
 * THE GENERATOR IS THE PROPERTY
 * -----------------------------
 * A presence-and-order property over a projection that cannot lose a row is worthless if the
 * generator cannot *reach* the shapes that would break a payload-inward implementation. So
 * the timeline generator is a discriminated union of seven families, each a real failure
 * mode, and the witness counters at the end of each property assert every one was reached:
 *
 *   * **no events** and **an absent `timeline` key** — nothing to iterate. A loop over the
 *     payload emits zero rows here; the canonical list emits nine.
 *   * **arbitrary and reversed orderings** — the rendered order must be the frozen literal's,
 *     not arrival order.
 *   * **duplicated events** — the same known name twice must not produce a tenth row.
 *   * **unknown event names**, including the lower-case `position_updated` (a *different*
 *     backend's vocabulary, which must never fire), `POSITION_LIQUIDATED`, the empty string
 *     and the prototype keys `__proto__` / `toString` / `constructor` — a bare
 *     `events[name]` index would resolve the last three off `Object.prototype` and light a
 *     stage up on an event nobody sent.
 *   * **whitespace-padded known names** — the module trims before matching, so `' EXECUTED '`
 *     IS the `EXECUTED` event. This is deliberately generated because the trim is easy to
 *     forget when reasoning about which entry backs a stage (see `indexedName` below).
 *   * **entries with no `event` key, and non-object entries** (`null`, `7`, `'EXECUTED'`,
 *     `[]`) — a projection that reads `entry.event` off a string gets `undefined`, and one
 *     that trusts it gets a row named `undefined`.
 *   * **a `timeline` that is not an array**, and **absent `trace` sections** — a degraded or
 *     500 body.
 *
 * WHY P16 IS STATED OVER `backed` AND `state` TOGETHER
 * ---------------------------------------------------
 * `buildSignalTraceStages` resolves in two passes, and the distinction matters for what P16
 * can honestly claim:
 *
 *   * **Pass one** resolves each stage from its own backing record alone. That is where
 *     "a function of its own backing record" lives, and the `backed` flag every stage carries
 *     out of the projection IS pass one's answer.
 *   * **Pass two** settles the stages pass one left unbacked: a stage after a `blocked` stage
 *     becomes `not-available` naming the blocking stage, rather than claiming `pending`
 *     forever on a signal whose lifecycle is over.
 *
 * So the *final* state of an unbacked stage legitimately depends on an earlier stage. P16 is
 * not weakened to accommodate that; it is stated where it is exactly true, as a **partition**:
 *
 *     backed === true   ⟺   state ∈ {complete, blocked, not-applicable}
 *     backed === false  ⟺   state ∈ {pending, not-available}
 *
 * The two sets are disjoint and exhaust `STAGE_STATES`, so `backed` — pass one's answer —
 * *determines which half the state falls in*, for every payload. That implies both claims
 * task 21.3 asks for (no backed stage is ever `pending`; no unbacked stage is ever
 * `complete`) and is strictly stronger than their conjunction, since it also forbids a backed
 * stage rendering `not-available` and an unbacked one rendering `not-applicable`.
 *
 * Pass one's answer is then recovered *exactly* from the public output by `passOneState`
 * below — no reaching inside the module — and the functional claim is asserted directly in
 * its strongest form: **two payloads that agree on one stage's backing record produce the
 * same pass-one state for that stage, however wildly they differ everywhere else.** The
 * residual freedom pass two has inside the unbacked half is not left unasserted either; the
 * "pass two settles" test pins it to the three reasons the design allows and no others,
 * which is also what proves `passOneState`'s reconstruction faithful.
 */

import { describe, expect, it } from 'vitest';
import fc from 'fast-check';

import {
  EVENT_EXCHANGE_RESPONSE,
  EVENT_EXECUTED,
  EVENT_ORDER_CREATED,
  EVENT_POSITION_UPDATED,
  EVENT_RISK_EVALUATED,
  EVENT_SIGNAL_GENERATED,
  REASON_ML_NOT_APPLICABLE,
  REASON_NOT_YET_REACHED,
  REASON_NO_LOGIC_NODE,
  REASON_TRACE_RETENTION,
  SIGNAL_TIMELINE_EVENTS,
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
  reasonLifecycleEnded,
} from '../../../src/lib/signalTraceStages';

/** design.md: "minimum 100 iterations per property"; this repo's convention is 300. */
const RUNS = { numRuns: 300 };

/** The nine ids and names, in canonical order, spelled out rather than read from the module. */
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

const CANONICAL_NAMES = [
  'Market data',
  'Indicators',
  'Model',
  'Logic',
  'Signal',
  'Order decision',
  'Submission',
  'Execution',
  'Position update',
];

/** design.md §10.2's partition, restated here so the property does not import its own answer. */
const BACKED_STATES = [STAGE_COMPLETE, STAGE_BLOCKED, STAGE_NOT_APPLICABLE];
const UNBACKED_STATES = [STAGE_PENDING, STAGE_NOT_AVAILABLE];

const KNOWN_EVENTS = [...SIGNAL_TIMELINE_EVENTS];

/* ══════════════════════════════════════════════════════════════════════════
 * TOTAL READERS — the payload rules, re-derived from the response shape
 * ══════════════════════════════════════════════════════════════════════════ */

/** A plain record. An array is not a record and neither is a string. */
const isRecord = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const asTimeline = (value) => (Array.isArray(value) ? value : []);

/**
 * The known event name a timeline entry indexes to, or `null`.
 *
 * This mirrors the server's contract rather than the module's code: `timeline` is a list of
 * `{event, timestamp, data}`, the vocabulary is `signal_service.SIGNAL_TIMELINE_EVENTS`, and
 * the name is trimmed before matching — so `' EXECUTED '` indexes to `EXECUTED`, which is
 * why the generator emits padded spellings and why the injection helper filters on this
 * rather than on `entry.event === name`.
 */
const indexedName = (entry) => {
  if (!isRecord(entry) || typeof entry.event !== 'string') return null;
  const name = entry.event.trim();
  return KNOWN_EVENTS.includes(name) ? name : null;
};

/** Whether the server itself reported the model stage inapplicable. */
const serverSaidNotApplicable = (payload) => {
  const response = isRecord(payload) ? payload : {};
  const trace = isRecord(response.trace) ? response.trace : {};
  const ml = isRecord(trace.ml_inference) ? trace.ml_inference : null;
  return ml !== null && ml.applicable === false;
};

/**
 * Pass one's answer for a stage, recovered from the public row alone.
 *
 * Pass two rewrites `pending` and nothing else: to `not-available` with
 * {@link reasonLifecycleEnded} downstream of a block, or to `pending` with
 * {@link REASON_NOT_YET_REACHED}. The only `not-available` a *resolver* produces is stage 4's,
 * and it is labelled with one of two reasons of its own. So the inverse is total, and the
 * "pass two settles the unbacked half" test below is what proves it faithful.
 */
const passOneState = (stage) => {
  if (stage.backed) return stage.state;
  const ownNotAvailable =
    stage.state === STAGE_NOT_AVAILABLE &&
    (stage.reason === REASON_TRACE_RETENTION || stage.reason === REASON_NO_LOGIC_NODE);
  return ownNotAvailable ? STAGE_NOT_AVAILABLE : STAGE_PENDING;
};

const stageOf = (result, id) => result.stages.find((stage) => stage.id === id);

/* ══════════════════════════════════════════════════════════════════════════
 * GENERATORS
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * Names that must never back a stage. `position_updated` and `signal_generated` are the
 * lower-case spellings a different backend vocabulary uses; the last four are prototype keys,
 * which a bare `events[name]` index would resolve off `Object.prototype`.
 */
const UNKNOWN_EVENT_NAMES = [
  'position_updated',
  'signal_generated',
  'Executed',
  'POSITION_LIQUIDATED',
  'MANUAL_RECONCILIATION_REQUIRED',
  'ORDER_CANCELLED',
  'RISK_EVALUATION',
  '',
  '   ',
  '__proto__',
  'constructor',
  'toString',
  'hasOwnProperty',
];

const anyText = (...preferred) =>
  fc.oneof(
    { weight: 4, arbitrary: fc.constantFrom(...preferred) },
    { weight: 1, arbitrary: fc.string() },
    { weight: 1, arbitrary: fc.constantFrom(null, '', '   ', 42, true, {}, []) },
  );

const anyFinite = (max) =>
  fc.oneof(
    { weight: 4, arbitrary: fc.double({ min: 0, max, noNaN: true }) },
    { weight: 2, arbitrary: fc.constantFrom(null, 0, NaN, Infinity, '12', {}) },
  );

const anyFlag = (...preferred) =>
  fc.oneof(
    { weight: 5, arbitrary: fc.boolean() },
    { weight: 2, arbitrary: fc.constantFrom(...preferred) },
  );

/** `data` shaped for the event that carries it — this is what makes `blocked` reachable. */
const dataFor = (name) => {
  const generic = fc.oneof(
    { weight: 3, arbitrary: fc.dictionary(fc.string(), fc.integer(), { maxKeys: 3 }) },
    { weight: 2, arbitrary: fc.constantFrom(null, 'nope', [], 7, {}) },
  );
  switch (name) {
    case EVENT_SIGNAL_GENERATED:
      return fc.oneof(
        { weight: 5, arbitrary: fc.record({ decision: anyText('BUY', 'SELL', 'HOLD') }, { requiredKeys: [] }) },
        { weight: 1, arbitrary: generic },
      );
    case EVENT_RISK_EVALUATED:
      return fc.oneof(
        {
          weight: 5,
          arbitrary: fc.record(
            {
              risk_passed: anyFlag('true', 'false', null, 1, 0),
              risk_reason: anyText('Daily loss limit reached', 'Exposure cap'),
              position_size: anyFinite(5),
            },
            { requiredKeys: [] },
          ),
        },
        { weight: 1, arbitrary: generic },
      );
    case EVENT_ORDER_CREATED:
      return fc.oneof(
        {
          weight: 5,
          arbitrary: fc.record(
            { order_id: anyText('o-1', 'o-2'), quantity: anyFinite(3) },
            { requiredKeys: [] },
          ),
        },
        { weight: 1, arbitrary: generic },
      );
    case EVENT_EXCHANGE_RESPONSE:
      return fc.oneof(
        {
          weight: 5,
          arbitrary: fc.record(
            {
              exchange_order_id: anyText('x-9F2'),
              // `REJECTED`/`REFUSED`/`DENIED` block stage 7, and the classifier upper-cases,
              // so the lower-case spelling must block too.
              order_status: anyText('FILLED', 'NEW', 'PARTIALLY_FILLED', 'REJECTED', 'REFUSED', 'DENIED', 'rejected'),
            },
            { requiredKeys: [] },
          ),
        },
        { weight: 1, arbitrary: generic },
      );
    case EVENT_POSITION_UPDATED:
      return fc.oneof(
        {
          weight: 5,
          arbitrary: fc.record(
            {
              symbol: anyText('BTC/USDT', 'ETH/USDT'),
              direction: anyText('BUY', 'SELL'),
              quantity_delta: anyFinite(2),
              not_available: fc.oneof(fc.constant(['resulting_position']), fc.constant([])),
              not_available_reason: anyText('The execution update reports the position CHANGE only.'),
            },
            { requiredKeys: [] },
          ),
        },
        { weight: 1, arbitrary: generic },
      );
    default:
      return generic;
  }
};

const entryFor = (name) =>
  dataFor(typeof name === 'string' ? name.trim() : name).chain((data) =>
    fc
      .record(
        {
          event: fc.constant(name),
          timestamp: anyText('2024-05-01T12:04:00Z', '2024-05-01T12:04:03Z'),
          data: fc.constant(data),
        },
        { requiredKeys: ['event'] },
      ),
  );

/** One timeline entry: mostly a well-formed event record, sometimes not an entry at all. */
const anyEntry = fc.oneof(
  { weight: 6, arbitrary: fc.constantFrom(...KNOWN_EVENTS).chain(entryFor) },
  { weight: 1, arbitrary: fc.constantFrom(...KNOWN_EVENTS).chain((name) => entryFor(`  ${name} `)) },
  { weight: 3, arbitrary: fc.constantFrom(...UNKNOWN_EVENT_NAMES).chain(entryFor) },
  { weight: 1, arbitrary: fc.string().chain(entryFor) },
  // No `event` key at all, and an `event` that is not a string.
  { weight: 1, arbitrary: fc.record({ timestamp: fc.constant('2024-05-01T12:05:00Z'), data: fc.constant({}) }) },
  { weight: 1, arbitrary: fc.constantFrom(42, null, true, {}, []).chain(entryFor) },
  // Not an entry: the list itself is untrusted.
  { weight: 1, arbitrary: fc.constantFrom(null, 7, 'EXECUTED', [], '') },
);

/** A shuffled subset of the six known names — arbitrary orderings, reversed among them. */
const knownEventList = (minLength) =>
  fc
    .shuffledSubarray(KNOWN_EVENTS, { minLength, maxLength: KNOWN_EVENTS.length })
    .chain((names) => (names.length === 0 ? fc.constant([]) : fc.tuple(...names.map(entryFor))));

/**
 * The adversarial tail, fixed rather than sampled, so the `adversarial` family *guarantees*
 * the six shapes below appear together instead of leaving them to the dice.
 */
const ADVERSARIAL_TAIL = Object.freeze([
  Object.freeze({ event: 'position_updated', timestamp: '2024-05-01T12:05:00Z', data: { symbol: 'BTC/USDT' } }),
  Object.freeze({ event: 'POSITION_LIQUIDATED', timestamp: '2024-05-01T12:05:01Z', data: {} }),
  Object.freeze({ event: '__proto__', timestamp: '2024-05-01T12:05:02Z', data: {} }),
  Object.freeze({ event: 'toString', timestamp: '2024-05-01T12:05:03Z', data: {} }),
  Object.freeze({ event: '', timestamp: '2024-05-01T12:05:04Z', data: {} }),
  Object.freeze({ timestamp: '2024-05-01T12:05:05Z', data: { decision: 'BUY' } }),
  null,
  7,
  'EXECUTED',
]);

const NON_ARRAY_TIMELINES = [null, undefined, 'EXECUTED', 42, {}, { 0: 'EXECUTED', length: 1 }];

/** A `timeline`, tagged with the family it came from. `absent` omits the key entirely. */
const anyTimeline = fc.oneof(
  { weight: 5, arbitrary: fc.array(anyEntry, { maxLength: 12 }).map((timeline) => ({ kind: 'arbitrary', timeline })) },
  { weight: 2, arbitrary: knownEventList(1).map((list) => ({ kind: 'duplicated', timeline: [...list, ...list] })) },
  { weight: 2, arbitrary: knownEventList(0).map((list) => ({ kind: 'shuffled', timeline: list })) },
  { weight: 2, arbitrary: knownEventList(0).map((list) => ({ kind: 'reversed', timeline: [...list].reverse() })) },
  {
    weight: 2,
    arbitrary: fc
      .array(anyEntry, { maxLength: 5 })
      .map((list) => ({ kind: 'adversarial', timeline: [...list, ...ADVERSARIAL_TAIL] })),
  },
  { weight: 1, arbitrary: fc.constant({ kind: 'empty', timeline: [] }) },
  { weight: 1, arbitrary: fc.constantFrom(...NON_ARRAY_TIMELINES).map((timeline) => ({ kind: 'not-an-array', timeline })) },
  { weight: 1, arbitrary: fc.constant({ kind: 'absent', timeline: undefined, omit: true }) },
);

/** A `_dag_node_entry`-shaped node. LOGIC and failing statuses are weighted up on purpose. */
const anyNode = fc.record(
  {
    node_id: anyText('rsi', 'gate', 'ml'),
    node_type: fc.oneof(
      { weight: 3, arbitrary: fc.constantFrom('LOGIC', 'logic') },
      { weight: 3, arbitrary: fc.constantFrom('INDICATOR', 'ML', 'ENTRY', 'EXIT') },
      { weight: 1, arbitrary: fc.constantFrom(null, 7, '', 'LOGICAL') },
    ),
    status: fc.oneof(
      { weight: 4, arbitrary: fc.constantFrom('pass', 'ok', 'success') },
      { weight: 3, arbitrary: fc.constantFrom('fail', 'failed', 'error', 'FAILED', 'Error') },
      { weight: 1, arbitrary: fc.constantFrom(null, 0, '', 'failure') },
    ),
    execution_ms: anyFinite(500),
    error: anyText('Indicator window too short'),
  },
  { requiredKeys: [] },
);

const anyNodesList = fc.oneof(
  {
    weight: 6,
    arbitrary: fc.array(
      fc.oneof({ weight: 8, arbitrary: anyNode }, { weight: 1, arbitrary: fc.constantFrom(null, 7, 'node', []) }),
      { maxLength: 5 },
    ),
  },
  { weight: 2, arbitrary: fc.constant([]) },
  { weight: 1, arbitrary: fc.constantFrom(null, 'nope', {}, 42) },
);

const anySection = (shape) =>
  fc.oneof(
    { weight: 8, arbitrary: fc.record(shape, { requiredKeys: [] }) },
    { weight: 1, arbitrary: fc.constantFrom(null, [], 'nope', 42) },
  );

const anySource = anyText('signal_trace_engine', 'signals_row');

const anyDagSection = anySection({ source: anySource, nodes: anyNodesList });

/**
 * `trace.ml_inference`. `applicable: false` is the ONE route to `not-applicable`, so the
 * three families are: the server saying so, the server saying the opposite, and every
 * near-miss that must NOT reach it — the string `'false'`, `0`, `null`, an absent key, and a
 * section that is not a record at all.
 */
const anyMlSection = fc.oneof(
  {
    weight: 3,
    arbitrary: fc.record(
      { source: anySource, applicable: fc.constant(false), detail: fc.constantFrom(null, {}, { model_id: 'm-1' }) },
      { requiredKeys: ['applicable'] },
    ),
  },
  {
    weight: 4,
    arbitrary: fc.record(
      {
        source: anySource,
        applicable: fc.constant(true),
        detail: fc.oneof(
          fc.record(
            { model_id: anyText('m-1'), confidence: anyFinite(1), inference_ms: anyFinite(80) },
            { requiredKeys: [] },
          ),
          fc.constantFrom(null, {}, 'nope', []),
        ),
      },
      { requiredKeys: ['applicable'] },
    ),
  },
  {
    weight: 2,
    arbitrary: fc.record(
      { source: anySource, applicable: fc.constantFrom('false', 0, null, undefined, [], 'no'), detail: fc.constantFrom(null, {}) },
      { requiredKeys: ['applicable'] },
    ),
  },
  { weight: 1, arbitrary: fc.constantFrom(null, [], 'nope', 42, {}) },
);

const anyRiskSection = anySection({
  source: anySource,
  detail: fc.oneof(
    {
      weight: 6,
      arbitrary: fc.record(
        {
          passed: anyFlag(null, 'true'),
          blocked: anyFlag(null, 'yes'),
          reason: anyText('Exposure cap', 'Daily loss limit reached'),
          position_size: anyFinite(4),
        },
        { requiredKeys: [] },
      ),
    },
    { weight: 1, arbitrary: fc.constantFrom(null, {}, 'nope', []) },
  ),
});

const anyExecutionSection = anySection({
  source: anySource,
  outcome: fc.oneof(
    {
      weight: 6,
      arbitrary: fc.record(
        {
          execution_price: anyFinite(70000),
          filled_quantity: anyFinite(2),
          latency_ms: anyFinite(400),
          failure_reason: anyText('Insufficient balance at venue'),
          executed_at: anyText('2024-05-01T12:04:03Z'),
        },
        { requiredKeys: [] },
      ),
    },
    { weight: 1, arbitrary: fc.constantFrom(null, {}, 'nope', []) },
  ),
  exchange_response: fc.constantFrom(null, { status: 'FILLED' }, 'nope'),
});

/** `trace`, whose four sections are each independently present, malformed or absent. */
const anyTrace = fc.oneof(
  {
    weight: 8,
    arbitrary: fc.record(
      {
        dag_nodes: anyDagSection,
        ml_inference: anyMlSection,
        risk_validation: anyRiskSection,
        execution: anyExecutionSection,
      },
      { requiredKeys: [] },
    ),
  },
  { weight: 1, arbitrary: fc.constantFrom(null, [], 'nope', 42, {}) },
);

const anySignal = fc.oneof(
  {
    weight: 8,
    arbitrary: fc.record(
      {
        decision: anyText('BUY', 'SELL', 'HOLD'),
        market_info: fc.oneof(
          { weight: 5, arbitrary: fc.record({ symbol: anyText('BTC/USDT'), timeframe: anyText('15m', '1h') }, { requiredKeys: [] }) },
          { weight: 2, arbitrary: fc.constantFrom(null, {}, [], 'nope') },
        ),
        indicators: fc.oneof(
          { weight: 5, arbitrary: fc.dictionary(fc.constantFrom('rsi_14', 'ema_50', 'atr'), anyFinite(100), { maxKeys: 3 }) },
          { weight: 2, arbitrary: fc.constantFrom(null, {}, [], 'nope') },
        ),
      },
      { requiredKeys: [] },
    ),
  },
  { weight: 1, arbitrary: fc.constantFrom(null, [], 'nope', 42) },
);

const anyDegraded = fc.oneof(
  { weight: 4, arbitrary: fc.constant(null) },
  {
    weight: 3,
    arbitrary: fc.record(
      {
        migration: anyText('005b_signal_lifecycle_and_idempotency.sql'),
        reason: anyText('public.signals does not carry order_lifecycle_state.'),
        unrepresentable_lifecycle_states: fc.oneof(fc.array(anyText('GENERATED', 'CLOSED'), { maxLength: 3 }), fc.constantFrom(null, 'nope')),
      },
      { requiredKeys: [] },
    ),
  },
  { weight: 1, arbitrary: fc.constantFrom([], 'nope', 42) },
);

/** Payloads that are not responses at all. Requirement 9.1 holds over these too. */
const GARBAGE_PAYLOADS = [null, undefined, 0, -1, NaN, '', 'signal', true, false, [], [1, 2, 3], {}];

/**
 * A `GET /api/signal-trace/signals/{id}` body, tagged with the timeline family it carries.
 * Every top-level key is optional, so "absent trace sections" includes an absent `trace`.
 */
const anyPayload = fc.oneof(
  {
    weight: 10,
    arbitrary: anyTimeline.chain((variant) =>
      fc
        .record(
          {
            signal: anySignal,
            trace: anyTrace,
            lifecycle_state_source: anyText('canonical', 'legacy_status_map'),
            degraded: anyDegraded,
          },
          { requiredKeys: [] },
        )
        .map((base) => ({
          kind: variant.kind,
          payload: variant.omit ? base : { ...base, timeline: variant.timeline },
        })),
    ),
  },
  { weight: 1, arbitrary: fc.constantFrom(...GARBAGE_PAYLOADS).map((payload) => ({ kind: 'garbage', payload })) },
  { weight: 1, arbitrary: fc.anything().map((payload) => ({ kind: 'anything', payload })) },
);

/* ══════════════════════════════════════════════════════════════════════════
 * Property 15 — the nine stages are always all present, in order
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Property 15: the nine trace stages are always all present, in order', () => {
  it('renders exactly the canonical nine ids, in canonical order, once each', () => {
    // Witnesses. Each is a shape that breaks a payload-inward projection; a green run that
    // reached none of them would be decoration, so they are asserted after the property.
    const kinds = new Set();
    let emptyTimeline = 0;
    let nonArrayTimeline = 0;
    let absentTrace = 0;
    let unknownEventName = 0;
    let lowerCasePositionUpdated = 0;
    let paddedKnownName = 0;
    let entryWithoutEventKey = 0;
    let nonObjectEntry = 0;
    let duplicatedKnownEvent = 0;

    fc.assert(
      fc.property(anyPayload, ({ kind, payload }) => {
        const result = buildSignalTraceStages(payload);
        const ids = result.stages.map((stage) => stage.id);

        // Requirement 9.1, in one line: the sequence IS the canonical list.
        expect(ids).toEqual(CANONICAL_IDS);
        // …and "exactly once each", which `toEqual` on the ordered list already gives but
        // which is the clause the requirement actually names.
        expect(new Set(ids).size).toBe(9);
        CANONICAL_IDS.forEach((id) => {
          expect(ids.filter((candidate) => candidate === id)).toHaveLength(1);
        });
        // Numbering and naming travel with the order, so a reshuffle cannot hide behind ids.
        expect(result.stages.map((stage) => stage.number)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
        expect(result.stages.map((stage) => stage.name)).toEqual(CANONICAL_NAMES);
        // Every row is in exactly one of the five states — no `undefined` row slipped in.
        result.stages.forEach((stage) => {
          expect(STAGE_STATES, `${stage.id} is in state "${stage.state}"`).toContain(stage.state);
        });

        kinds.add(kind);
        const timeline = isRecord(payload) ? payload.timeline : undefined;
        const entries = asTimeline(timeline);
        if (Array.isArray(timeline) && timeline.length === 0) emptyTimeline += 1;
        if (isRecord(payload) && 'timeline' in payload && !Array.isArray(timeline)) nonArrayTimeline += 1;
        if (!isRecord(payload) || !isRecord(payload.trace)) absentTrace += 1;

        const counted = {};
        entries.forEach((entry) => {
          const name = indexedName(entry);
          if (name === null && isRecord(entry) && typeof entry.event === 'string') unknownEventName += 1;
          if (isRecord(entry) && entry.event === 'position_updated') lowerCasePositionUpdated += 1;
          if (name !== null && entry.event !== name) paddedKnownName += 1;
          if (isRecord(entry) && !('event' in entry)) entryWithoutEventKey += 1;
          if (!isRecord(entry)) nonObjectEntry += 1;
          if (name !== null) counted[name] = (counted[name] ?? 0) + 1;
        });
        if (Object.values(counted).some((count) => count > 1)) duplicatedKnownEvent += 1;
      }),
      RUNS,
    );

    // Non-vacuity: the generator reached every family and every adversarial shape.
    expect([...kinds].sort()).toEqual([
      'absent',
      'adversarial',
      'anything',
      'arbitrary',
      'duplicated',
      'empty',
      'garbage',
      'not-an-array',
      'reversed',
      'shuffled',
    ]);
    expect(emptyTimeline, 'no run had an empty timeline').toBeGreaterThan(0);
    expect(nonArrayTimeline, 'no run had a non-array timeline').toBeGreaterThan(0);
    expect(absentTrace, 'no run had an absent trace section').toBeGreaterThan(0);
    expect(unknownEventName, 'no run carried an unknown event name').toBeGreaterThan(0);
    expect(lowerCasePositionUpdated, 'no run carried the lower-case position_updated').toBeGreaterThan(0);
    expect(paddedKnownName, 'no run carried a whitespace-padded known name').toBeGreaterThan(0);
    expect(entryWithoutEventKey, 'no run carried an entry with no event key').toBeGreaterThan(0);
    expect(nonObjectEntry, 'no run carried a non-object timeline entry').toBeGreaterThan(0);
    expect(duplicatedKnownEvent, 'no run duplicated a known event').toBeGreaterThan(0);
  });

  it('renders the same nine, in the same order, however the timeline is permuted', () => {
    // Order-independence stated directly: the projection of a permutation is the projection.
    // `readPayload` keeps the FIRST occurrence of each known name, so a permutation may
    // legitimately change a stage's `detail`; the sequence of rows may not.
    fc.assert(
      fc.property(anyPayload, fc.integer({ min: 0, max: 64 }), ({ payload }, rotation) => {
        const entries = asTimeline(isRecord(payload) ? payload.timeline : undefined);
        const offset = entries.length === 0 ? 0 : rotation % entries.length;
        const rotated = [...entries.slice(offset), ...entries.slice(0, offset)];

        const permuted = { ...(isRecord(payload) ? payload : {}), timeline: [...rotated].reverse() };
        expect(buildSignalTraceStages(permuted).stages.map((stage) => stage.id)).toEqual(CANONICAL_IDS);
      }),
      RUNS,
    );
  });

  it('never throws, and the ids it returns are the ids the module publishes', () => {
    fc.assert(
      fc.property(anyPayload, ({ payload }) => {
        expect(() => buildSignalTraceStages(payload)).not.toThrow();
      }),
      RUNS,
    );
    // The literal above is the requirement's list; this pins the module's own vocabulary to
    // it, so the property cannot pass by comparing the module against itself.
    expect([
      STAGE_MARKET_DATA,
      STAGE_INDICATORS,
      STAGE_MODEL,
      STAGE_LOGIC,
      STAGE_SIGNAL,
      STAGE_ORDER_DECISION,
      STAGE_SUBMISSION,
      STAGE_EXECUTION,
      STAGE_POSITION_UPDATE,
    ]).toEqual(CANONICAL_IDS);
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Property 16 — a stage's state is a function of its own backing record
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * What backs each stage, per design.md §10.1 — the slice of the response its pass-one state
 * is a function of, and nothing else. Keyed by canonical id, in canonical order, so
 * `Object.keys` doubles as the order check above.
 */
const BACKING_OF = Object.freeze({
  [STAGE_MARKET_DATA]: { signalKeys: ['market_info'], sections: [], events: [] },
  [STAGE_INDICATORS]: { signalKeys: ['indicators'], sections: ['dag_nodes'], events: [] },
  [STAGE_MODEL]: { signalKeys: [], sections: ['ml_inference'], events: [] },
  [STAGE_LOGIC]: { signalKeys: ['decision'], sections: ['dag_nodes'], events: [] },
  [STAGE_SIGNAL]: { signalKeys: ['decision'], sections: [], events: [EVENT_SIGNAL_GENERATED] },
  [STAGE_ORDER_DECISION]: {
    signalKeys: [],
    sections: ['risk_validation'],
    events: [EVENT_RISK_EVALUATED, EVENT_ORDER_CREATED],
  },
  [STAGE_SUBMISSION]: { signalKeys: [], sections: [], events: [EVENT_EXCHANGE_RESPONSE] },
  [STAGE_EXECUTION]: { signalKeys: [], sections: ['execution'], events: [EVENT_EXECUTED] },
  [STAGE_POSITION_UPDATE]: { signalKeys: [], sections: [], events: [EVENT_POSITION_UPDATED] },
});

/**
 * `host`, with one stage's backing record replaced by `donor`'s and everything else left as
 * it was. Non-record `signal`/`trace` and non-array `timeline` are normalised to `{}`/`[]`,
 * which is semantics-preserving — the module reads exactly `null`/`[]` out of them either way
 * — and is unavoidable, since a stage's record cannot be injected into a string.
 *
 * The timeline is rebuilt by dropping every entry that *indexes to* one of the stage's event
 * names and appending `donor`'s first such entry. Position is irrelevant because the index
 * keeps the first occurrence and no other occurrence survives the filter.
 */
const withBacking = (stageId, donor, host) => {
  const spec = BACKING_OF[stageId];
  const donorResponse = isRecord(donor) ? donor : {};
  const hostResponse = isRecord(host) ? host : {};
  const donorSignal = isRecord(donorResponse.signal) ? donorResponse.signal : {};
  const donorTrace = isRecord(donorResponse.trace) ? donorResponse.trace : {};

  const signal = { ...(isRecord(hostResponse.signal) ? hostResponse.signal : {}) };
  spec.signalKeys.forEach((key) => {
    signal[key] = donorSignal[key];
  });

  const trace = { ...(isRecord(hostResponse.trace) ? hostResponse.trace : {}) };
  spec.sections.forEach((section) => {
    trace[section] = donorTrace[section];
  });

  const donorTimeline = asTimeline(donorResponse.timeline);
  const timeline = asTimeline(hostResponse.timeline).filter(
    (entry) => !spec.events.includes(indexedName(entry)),
  );
  spec.events.forEach((name) => {
    const carried = donorTimeline.find((entry) => indexedName(entry) === name);
    if (carried !== undefined) timeline.push(carried);
  });

  return { ...hostResponse, signal, trace, timeline };
};

describe('Property 16: a stage\u2019s state is a function of its own backing record', () => {
  it('partitions the five states by `backed`, with nothing left over', () => {
    // The partition this property is stated over. If a sixth state appeared, or one moved
    // between the halves, the statement below would be about the wrong sets.
    expect([...BACKED_STATES, ...UNBACKED_STATES].sort()).toEqual([...STAGE_STATES].sort());
    expect(BACKED_STATES.filter((state) => UNBACKED_STATES.includes(state))).toEqual([]);
  });

  it('never renders a backed stage pending, nor an unbacked stage complete', () => {
    const seenStates = new Set();
    const backedStates = new Set();
    const unbackedStates = new Set();

    fc.assert(
      fc.property(anyPayload, ({ payload }) => {
        buildSignalTraceStages(payload).stages.forEach((stage) => {
          expect(typeof stage.backed).toBe('boolean');
          const where = `${stage.id} is "${stage.state}" with backed=${stage.backed}`;

          if (stage.backed) {
            expect(BACKED_STATES, where).toContain(stage.state);
            expect(stage.state, where).not.toBe(STAGE_PENDING);
            backedStates.add(stage.state);
          } else {
            expect(UNBACKED_STATES, where).toContain(stage.state);
            expect(stage.state, where).not.toBe(STAGE_COMPLETE);
            unbackedStates.add(stage.state);
          }
          seenStates.add(stage.state);
        });
      }),
      RUNS,
    );

    // Non-vacuity: all five states occurred, and BOTH halves of the partition were exercised
    // in full — otherwise "never pending" could hold because nothing was ever backed.
    expect([...seenStates].sort()).toEqual([...STAGE_STATES].sort());
    expect([...backedStates].sort()).toEqual([...BACKED_STATES].sort());
    expect([...unbackedStates].sort()).toEqual([...UNBACKED_STATES].sort());
  });

  it('reaches not-applicable exactly when the server said so, and only at stage 3', () => {
    let saidSoRuns = 0;
    let nearMissRuns = 0;

    fc.assert(
      fc.property(anyPayload, ({ payload }) => {
        const result = buildSignalTraceStages(payload);
        const notApplicable = result.stages.filter((stage) => stage.state === STAGE_NOT_APPLICABLE);
        const saidSo = serverSaidNotApplicable(payload);

        // Both directions at once: the set is `[model]` when the server said so and empty
        // when it did not, so neither a missed statement nor an invented one can pass.
        expect(notApplicable.map((stage) => stage.id)).toEqual(saidSo ? [STAGE_MODEL] : []);

        if (saidSo) {
          const model = stageOf(result, STAGE_MODEL);
          expect(model.number).toBe(3);
          expect(model.reason).toBe(REASON_ML_NOT_APPLICABLE);
          // The server's own statement IS the record, which is what makes this not a gap.
          expect(model.backed).toBe(true);
          saidSoRuns += 1;
        } else {
          const ml = isRecord(payload) && isRecord(payload.trace) ? payload.trace.ml_inference : undefined;
          if (isRecord(ml) && ml.applicable !== true) nearMissRuns += 1;
        }
      }),
      RUNS,
    );

    // Non-vacuity in both directions: runs where the server said so, and runs carrying a
    // near-miss (`'false'`, `0`, `null`, absent) that must NOT reach `not-applicable`.
    expect(saidSoRuns, 'no run had ml_inference.applicable === false').toBeGreaterThan(0);
    expect(nearMissRuns, 'no run had a near-miss applicable value').toBeGreaterThan(0);
  });

  it('lets pass two settle only the unbacked half, and only three ways', () => {
    // This is what makes `passOneState` a faithful inverse rather than a guess, and it is
    // where the two-pass shape is pinned instead of excused: an unbacked stage is `pending`
    // with the design's label, `not-available` naming the stage that stopped the signal, or
    // stage 4's own `not-available` with one of its two reasons. Nothing else.
    let lifecycleEnded = 0;
    let notYetReached = 0;
    let ownNotAvailable = 0;

    fc.assert(
      fc.property(anyPayload, ({ payload }) => {
        const { stages } = buildSignalTraceStages(payload);
        const blockedAt = stages.findIndex((stage) => stage.state === STAGE_BLOCKED);

        stages.forEach((stage, index) => {
          if (stage.backed) return;
          const where = `${stage.id} is "${stage.state}" because "${stage.reason}"`;

          if (passOneState(stage) === STAGE_NOT_AVAILABLE) {
            // Only stage 4 owns a not-available of its own, and pass two leaves it alone
            // even downstream of a block.
            expect(stage.id, where).toBe(STAGE_LOGIC);
            ownNotAvailable += 1;
            return;
          }
          if (blockedAt !== -1 && index > blockedAt) {
            expect(stage.state, where).toBe(STAGE_NOT_AVAILABLE);
            expect(stage.reason, where).toBe(reasonLifecycleEnded(CANONICAL_NAMES[blockedAt]));
            lifecycleEnded += 1;
          } else {
            expect(stage.state, where).toBe(STAGE_PENDING);
            expect(stage.reason, where).toBe(REASON_NOT_YET_REACHED);
            notYetReached += 1;
          }
        });
      }),
      RUNS,
    );

    expect(lifecycleEnded, 'no unbacked stage was ever downstream of a block').toBeGreaterThan(0);
    expect(notYetReached, 'no unbacked stage was ever pending').toBeGreaterThan(0);
    expect(ownNotAvailable, 'stage 4 never reached its own not-available').toBeGreaterThan(0);
  });

  it('gives the same pass-one state for equal backing records, whatever else differs', () => {
    // The functional claim itself. Two payloads are made to agree on ONE stage's backing
    // record and to differ arbitrarily everywhere else; pass one's answer for that stage
    // must be identical. A resolver that read a neighbour's record would disagree here.
    const changed = new Set();
    const donorStates = new Map();

    CANONICAL_IDS.forEach((stageId) => {
      fc.assert(
        fc.property(anyPayload, anyPayload, ({ payload: donor }, { payload: host }) => {
          const donorAnswer = passOneState(stageOf(buildSignalTraceStages(donor), stageId));
          const hostAnswer = passOneState(stageOf(buildSignalTraceStages(host), stageId));
          const injected = buildSignalTraceStages(withBacking(stageId, donor, host));
          const injectedAnswer = passOneState(stageOf(injected, stageId));

          expect(
            injectedAnswer,
            `${stageId}: pass one answered "${injectedAnswer}" on the donor's record but ` +
              `"${donorAnswer}" on the donor itself, so it read something outside that record`,
          ).toBe(donorAnswer);

          if (hostAnswer !== donorAnswer) changed.add(stageId);
          donorStates.set(stageId, (donorStates.get(stageId) ?? new Set()).add(donorAnswer));
        }),
        RUNS,
      );
    });

    // Non-vacuity: for every stage the injection actually moved the answer at least once —
    // so the assertion is not passing because host and donor happened to agree already —
    // and every stage answered at least two different ways across its runs.
    expect([...changed].sort(), 'injection never changed some stage\u2019s answer').toEqual(
      [...CANONICAL_IDS].sort(),
    );
    CANONICAL_IDS.forEach((stageId) => {
      expect(donorStates.get(stageId).size, `${stageId} only ever answered one way`).toBeGreaterThan(1);
    });
  });
});
