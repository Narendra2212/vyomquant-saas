/**
 * signalTraceStages.js — the canonical nine-stage projection of one signal trace
 * (vyomquant-ui-redesign task 21.1; design.md §10.1, §10.2; Requirements 9.1, 9.2, 19.3).
 *
 * THE INVERSION IS THE WHOLE DESIGN
 * =================================
 * The nine stages are built from {@link SIGNAL_TRACE_STAGES} — the canonical list — and the
 * payload is then *attached* to them. The payload is never iterated to decide which rows
 * exist. That single inversion is what makes two acceptance criteria structural rather than
 * a thing a loop has to remember:
 *
 *   * Requirement 9.1's "in order" holds because the order is the order of a frozen literal
 *     in this file, not the order events happened to arrive in.
 *   * Requirement 9.2's "renders the stage as not-available rather than omitting it" holds
 *     because there is no code path that can drop a row. A stage with nothing behind it
 *     still gets a row; it just gets a different state.
 *
 * The two failure modes of the payload-inward shape are therefore both impossible here: an
 * unknown event type cannot add a tenth row (nothing reads the event list for row identity),
 * and a missing event cannot remove one of the nine.
 *
 * `components/SignalTraceVisualization.jsx` does the opposite today: it renders a row per
 * entry of a `trace.pipeline` array, seven stages deep, so a payload with six entries showed
 * six stages. That is the shape this module replaces (design.md §10.1).
 *
 * WHAT ACTUALLY BACKS EACH STAGE, VERIFIED AGAINST THE BACKEND
 * ===========================================================
 * `GET /api/signal-trace/signals/{id}` returns
 * `{signal, trace, lifecycle_transitions, timeline, lifecycle_state_source, degraded}`
 * (`backend_app/backend/signal_service.py::build_signal_detail`). `trace` carries four
 * labelled sections, each tagged with the `source` it was read from (`signal_trace_engine`
 * or `signals_row`):
 *
 *   * `trace.dag_nodes`   → `{source, nodes: [...]}`  — note the wrapper: `nodes` is the array.
 *   * `trace.ml_inference` → `{source, applicable, detail}`
 *   * `trace.risk_validation` → `{source, detail}`
 *   * `trace.execution`  → `{source, outcome, exchange_response}`
 *
 * `timeline` is a list of `{event, timestamp, data}`. Its vocabulary is closed and is
 * declared server-side as `signal_service.SIGNAL_TIMELINE_EVENTS`; the six spellings in
 * {@link SIGNAL_TIMELINE_EVENTS} below are transcribed from it, not from design.md, because
 * a client-side guess at an event name is the one mistake that can never fire.
 *
 * | # | stage             | backing                                                        |
 * |---|-------------------|----------------------------------------------------------------|
 * | 1 | Market data       | `signal.market_info`                                           |
 * | 2 | Indicators        | `signal.indicators` + `trace.dag_nodes.nodes`                   |
 * | 3 | Model             | `trace.ml_inference` (`applicable: false` is a *fact*)          |
 * | 4 | Logic             | `LOGIC`-category `dag_nodes` + `signal.decision`                |
 * | 5 | Signal            | `SIGNAL_GENERATED` + `signal.decision`                          |
 * | 6 | Order decision    | `RISK_EVALUATED` + `ORDER_CREATED` + `trace.risk_validation`     |
 * | 7 | Submission        | `EXCHANGE_RESPONSE`                                             |
 * | 8 | Execution         | `EXECUTED` + `trace.execution`                                  |
 * | 9 | Position update   | `POSITION_UPDATED` — BC-6, landed in task 12.6                  |
 *
 * STAGE 9 IS A REAL STAGE NOW
 * ===========================
 * design.md §10.1 registered stage 9 as BC-6: nothing in the signal-trace domain recorded a
 * position transition, so the stage was specified to render `not-available` permanently. Task
 * 12.6 closed it. `signal_service.POSITION_UPDATED_EVENT` is `"POSITION_UPDATED"`, emitted by
 * `_position_updated_event` and gated on the same `executed_at` column `EXECUTED` is gated
 * on — so the two events are present or absent *together*, and `POSITION_UPDATED` sorts
 * immediately after `EXECUTED` (equal timestamps, stable sort). This module therefore reads
 * the event rather than hardcoding a not-available, and stage 9 falls out of the general
 * rules like every other timeline-backed stage.
 *
 * Its `data` reports the position CHANGE only, and names what it could not report in
 * `data.not_available` with a one-sentence `data.not_available_reason`. That reason is the
 * server's own text and is surfaced verbatim rather than restated here.
 *
 * `pending` VERSUS `not-available`, WHICH IS THE DISTINCTION THAT KEEPS BITING
 * ==========================================================================
 * `pending` claims *"this has not happened yet"* — a promise about the future. `not-available`
 * claims *"this cannot be read"*. Rendering `pending` forever for a stage that can never
 * occur is a lie the page would tell on every blocked signal, so:
 *
 *   * **Stage 4 with an empty `dag_nodes`** is `not-available` with the retention reason, not
 *     `pending`. `signal_trace_engine.SignalTraceEngine.__init__` takes
 *     `retention_seconds: float = 3600` and its sweeper drops any trace older than that, so a
 *     signal from two hours ago legitimately has no node record. Nothing is coming; the record
 *     existed and was swept. `routers/signal_trace.py` says the same in prose: the engine is
 *     "an in-memory store with an hour's retention and a signal older than that legitimately
 *     has no record there."
 *
 *   * **Stages after a `blocked` stage** are `not-available`, not `pending`. A signal refused
 *     at risk evaluation will never be submitted, never fill and never move a position — the
 *     lifecycle is over. design.md §10.2 already forecloses `pending` here in its own
 *     definition ("no backing record, and an earlier stage is `complete` while **no stage is
 *     blocked**"); this module supplies the state that definition leaves open, with a reason
 *     naming the stage that stopped the signal. `not-applicable` would be wrong for the same
 *     reason it is reserved below: nobody said these stages do not apply, and they would have
 *     applied had the signal passed.
 *
 *   * **`not-applicable` is only ever the server's own statement** — `ml_inference.applicable
 *     === false`, meaning the strategy version has no ML node and the model was not consulted.
 *     That is a fact about the strategy, not a gap in the record. No other stage can reach
 *     this state, which is what makes design.md §10.2's "occurs exactly when the server said
 *     so" checkable (Property 16).
 *
 * WHAT THIS MODULE DOES NOT DO
 * ============================
 * No React, no `window`, no network, no clock, no colour. It reports `degraded` and
 * `lifecycle_state_source` and it reports which state each stage is in; the page decides that
 * `blocked` reads as `status.error` and that a degraded trace gets a `status.warning` note.
 * Latency is reported only where the server reports a number — summed `execution_ms` over the
 * nodes behind a stage, and `execution.outcome.latency_ms` for stage 8. Every other stage
 * reports `null` rather than a plausible-looking zero.
 *
 * TOTAL OVER GARBAGE
 * ==================
 * Every exported function answers for every input. `null`, a string, an array where an object
 * belongs, a `timeline` that is an object, an event with no `event` key, an unknown event
 * name: all produce the nine stages. Nothing throws, and every returned object is frozen.
 */

// ── The five states ─────────────────────────────────────────────────────────────────────

/** A backing record exists and reports success. */
export const STAGE_COMPLETE = 'complete';

/** A backing record exists and reports refusal or rejection. */
export const STAGE_BLOCKED = 'blocked';

/** No backing record, the signal is still in flight, and this stage has not been reached. */
export const STAGE_PENDING = 'pending';

/** The server said the stage does not apply — `ml_inference.applicable === false`, only. */
export const STAGE_NOT_APPLICABLE = 'not-applicable';

/** The record cannot be read: swept by retention, or belongs to a lifecycle that ended. */
export const STAGE_NOT_AVAILABLE = 'not-available';

/** design.md §10.2's five states. A stage is always in exactly one of them. */
export const STAGE_STATES = Object.freeze([
  STAGE_COMPLETE,
  STAGE_BLOCKED,
  STAGE_PENDING,
  STAGE_NOT_APPLICABLE,
  STAGE_NOT_AVAILABLE,
]);

// ── The closed timeline vocabulary, transcribed from the backend ─────────────────────────

export const EVENT_SIGNAL_GENERATED = 'SIGNAL_GENERATED';
export const EVENT_RISK_EVALUATED = 'RISK_EVALUATED';
export const EVENT_ORDER_CREATED = 'ORDER_CREATED';
export const EVENT_EXCHANGE_RESPONSE = 'EXCHANGE_RESPONSE';
export const EVENT_EXECUTED = 'EXECUTED';

/** BC-6's sixth event (`signal_service.POSITION_UPDATED_EVENT`), task 12.6. */
export const EVENT_POSITION_UPDATED = 'POSITION_UPDATED';

/**
 * `signal_service.SIGNAL_TIMELINE_EVENTS`, transcribed in the backend's derivation order.
 * The server declares this tuple precisely so a consumer names the vocabulary instead of
 * re-deriving it; anything not in here is an unknown event and is ignored for row identity.
 */
export const SIGNAL_TIMELINE_EVENTS = Object.freeze([
  EVENT_SIGNAL_GENERATED,
  EVENT_RISK_EVALUATED,
  EVENT_ORDER_CREATED,
  EVENT_EXCHANGE_RESPONSE,
  EVENT_EXECUTED,
  EVENT_POSITION_UPDATED,
]);

// ── The nine stage ids ──────────────────────────────────────────────────────────────────

export const STAGE_MARKET_DATA = 'market-data';
export const STAGE_INDICATORS = 'indicators';
export const STAGE_MODEL = 'model';
export const STAGE_LOGIC = 'logic';
export const STAGE_SIGNAL = 'signal';
export const STAGE_ORDER_DECISION = 'order-decision';
export const STAGE_SUBMISSION = 'submission';
export const STAGE_EXECUTION = 'execution';
export const STAGE_POSITION_UPDATE = 'position-update';

/**
 * Requirement 9.1's nine stages, in Requirement 9.1's order. THIS LIST IS THE PROJECTION:
 * {@link buildSignalTraceStages} maps over it and returns one row per entry, always, in this
 * order, whatever the payload says.
 */
export const SIGNAL_TRACE_STAGES = Object.freeze([
  Object.freeze({ id: STAGE_MARKET_DATA, number: 1, name: 'Market data' }),
  Object.freeze({ id: STAGE_INDICATORS, number: 2, name: 'Indicators' }),
  Object.freeze({ id: STAGE_MODEL, number: 3, name: 'Model' }),
  Object.freeze({ id: STAGE_LOGIC, number: 4, name: 'Logic' }),
  Object.freeze({ id: STAGE_SIGNAL, number: 5, name: 'Signal' }),
  Object.freeze({ id: STAGE_ORDER_DECISION, number: 6, name: 'Order decision' }),
  Object.freeze({ id: STAGE_SUBMISSION, number: 7, name: 'Submission' }),
  Object.freeze({ id: STAGE_EXECUTION, number: 8, name: 'Execution' }),
  Object.freeze({ id: STAGE_POSITION_UPDATE, number: 9, name: 'Position update' }),
]);

/** The nine ids, in canonical order. Property 15 compares the rendered sequence to this. */
export const SIGNAL_TRACE_STAGE_IDS = Object.freeze(SIGNAL_TRACE_STAGES.map((s) => s.id));

// ── The reasons, each one sentence, each stated once ─────────────────────────────────────

/**
 * Stage 4's reason when the trace store retains no nodes for this signal. The window is
 * `signal_trace_engine.SignalTraceEngine`'s `retention_seconds: float = 3600`.
 */
export const REASON_TRACE_RETENTION =
  'The trace store retains node-level records for about an hour, so this signal is older ' +
  'than its own logic trace. The record existed and has been swept, so it is not available ' +
  'rather than still to come.';

/** Stage 4's reason when nodes were retained but none of them is a `LOGIC`-category node. */
export const REASON_NO_LOGIC_NODE =
  'The retained node trace carries no LOGIC-category node and the signal records no ' +
  'decision, so there is no record of the logic evaluation to show.';

/** Stage 3's reason when the server reports `ml_inference.applicable === false`. */
export const REASON_ML_NOT_APPLICABLE =
  'This strategy version has no ML node, so no model was consulted. That is a fact about ' +
  'the strategy, not a missing record.';

/** design.md §10.2's `pending` label — the stage has not been reached yet. */
export const REASON_NOT_YET_REACHED = 'Not yet reached.';

/** design.md §10.3's degraded note, used when the server sends no `degraded.reason`. */
export const REASON_DEGRADED_FALLBACK =
  'Part of this trace is reconstructed from the signal record because the trace store no ' +
  'longer retains it.';

/**
 * Why a stage downstream of a `blocked` stage is `not-available`. Takes the blocking stage's
 * name so the page renders which stage ended the lifecycle, not a generic sentence.
 *
 * @param {string} blockedStageName
 * @returns {string}
 */
export function reasonLifecycleEnded(blockedStageName) {
  return (
    `The signal was stopped at ${blockedStageName}, so this stage will never occur. It is ` +
    'not available rather than pending, which would claim it is still coming.'
  );
}

// ── Total readers ───────────────────────────────────────────────────────────────────────

/** A plain record, or `null`. An array is not a record and neither is a string. */
const asRecord = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/** An array, or `[]`. A `timeline` the server could not build is not a crash here. */
const asArray = (value) => (Array.isArray(value) ? value : []);

/** A non-empty trimmed string, or `null`. */
const asText = (value) => {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

/** A finite number, or `null`. `NaN`, `Infinity` and numeric strings are not numbers. */
const asNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/** `true`/`false` only. Anything else is "not reported", which is a third answer. */
const asFlag = (value) => (typeof value === 'boolean' ? value : null);

/** Whether a record has at least one own key. `{}` is not a backing record. */
const hasKeys = (value) => {
  const record = asRecord(value);
  return record !== null && Object.keys(record).length > 0;
};

/** Sum of the finite `execution_ms` values over some nodes, or `null` if none reported one. */
const totalExecutionMs = (nodes) => {
  const reported = nodes.map((node) => asNumber(asRecord(node)?.execution_ms)).filter((ms) => ms !== null);
  return reported.length === 0 ? null : reported.reduce((sum, ms) => sum + ms, 0);
};

/** A DAG node whose `status` reports a failure. `_dag_node_entry` writes the string. */
const nodeFailed = (node) => {
  const status = asText(asRecord(node)?.status)?.toLowerCase();
  return status === 'fail' || status === 'failed' || status === 'error';
};

/** `NodeType.LOGIC` is spelled `"LOGIC"` (`strategy_dag/schema.py`); compared case-insensitively. */
const NODE_TYPE_LOGIC = 'LOGIC';

const isLogicNode = (node) =>
  asText(asRecord(node)?.node_type)?.toUpperCase() === NODE_TYPE_LOGIC;

/** Order statuses that mean the venue refused the order. */
const REJECTED_ORDER_STATUSES = Object.freeze(['REJECTED', 'REFUSED', 'DENIED']);

const isRejectedStatus = (value) => {
  const text = asText(value)?.toUpperCase();
  return text !== null && REJECTED_ORDER_STATUSES.includes(text);
};

// ── The payload, read once ──────────────────────────────────────────────────────────────

/**
 * Everything the nine resolvers below are allowed to read, pulled off the response once so
 * no resolver reaches into the raw payload and no resolver can see a shape the others do not.
 *
 * `timeline` is indexed by event name, restricted to {@link SIGNAL_TIMELINE_EVENTS}. That
 * restriction is where "an unknown event type cannot add a row" is enforced *and* where a
 * duplicate is collapsed: the index keeps the FIRST occurrence of each known name, because
 * the server sorts chronologically and the first is the earliest.
 *
 * @param {unknown} payload
 */
function readPayload(payload) {
  const response = asRecord(payload) ?? {};
  const signal = asRecord(response.signal) ?? {};
  const trace = asRecord(response.trace) ?? {};

  const events = {};
  for (const entry of asArray(response.timeline)) {
    const record = asRecord(entry);
    if (record === null) continue;
    const name = asText(record.event);
    if (name === null || !SIGNAL_TIMELINE_EVENTS.includes(name)) continue;
    if (!Object.prototype.hasOwnProperty.call(events, name)) events[name] = record;
  }

  const dagSection = asRecord(trace.dag_nodes) ?? {};
  const nodes = asArray(dagSection.nodes).filter((node) => asRecord(node) !== null);
  const mlSection = asRecord(trace.ml_inference);
  const riskSection = asRecord(trace.risk_validation) ?? {};
  const executionSection = asRecord(trace.execution) ?? {};

  return {
    signal,
    marketInfo: asRecord(signal.market_info),
    indicators: asRecord(signal.indicators),
    decision: asText(signal.decision),
    events,
    dagSource: asText(dagSection.source),
    nodes,
    logicNodes: nodes.filter(isLogicNode),
    ml: mlSection,
    mlApplicable: mlSection === null ? null : asFlag(mlSection.applicable),
    mlDetail: asRecord(mlSection?.detail),
    riskDetail: asRecord(riskSection.detail) ?? {},
    riskSource: asText(riskSection.source),
    executionOutcome: asRecord(executionSection.outcome) ?? {},
    exchangeResponse: asRecord(executionSection.exchange_response),
  };
}

/** One resolved stage, before the pending/lifecycle pass. */
const resolution = ({ state, backed, summary = null, reason = null, latencyMs = null, timestamp = null, detail = null }) =>
  ({ state, backed, summary, reason, latencyMs, timestamp, detail });

/** A stage with no backing record. The pending/lifecycle pass decides its final state. */
const unbacked = (reason = null) => resolution({ state: STAGE_PENDING, backed: false, reason });

// ── The nine resolvers, one per canonical stage ─────────────────────────────────────────

function resolveMarketData(read) {
  if (!hasKeys(read.marketInfo)) return unbacked();
  const info = read.marketInfo;
  const symbol = asText(info.symbol);
  const timeframe = asText(info.timeframe);
  return resolution({
    state: STAGE_COMPLETE,
    backed: true,
    summary: [symbol, timeframe].filter((part) => part !== null).join(' · ') || null,
    timestamp: asText(read.events[EVENT_SIGNAL_GENERATED]?.timestamp),
    detail: info,
  });
}

function resolveIndicators(read) {
  const backed = hasKeys(read.indicators) || read.nodes.length > 0;
  if (!backed) return unbacked();
  const names = Object.keys(read.indicators ?? {});
  const failed = read.nodes.some(nodeFailed);
  return resolution({
    state: failed ? STAGE_BLOCKED : STAGE_COMPLETE,
    backed: true,
    summary: `${names.length} indicator reading${names.length === 1 ? '' : 's'} · ${read.nodes.length} node${read.nodes.length === 1 ? '' : 's'}`,
    reason: failed ? asText(read.nodes.find(nodeFailed)?.error) : null,
    latencyMs: totalExecutionMs(read.nodes),
    detail: { source: read.dagSource, nodes: read.nodes, readings: read.indicators },
  });
}

/**
 * Stage 3. `applicable === false` is the server's own statement that no model was consulted,
 * so it is the ONE route to `not-applicable` in this module. A missing section is a gap and
 * takes the ordinary unbacked path instead.
 */
function resolveModel(read) {
  if (read.mlApplicable === false) {
    return resolution({
      state: STAGE_NOT_APPLICABLE,
      backed: true,
      reason: REASON_ML_NOT_APPLICABLE,
      detail: read.ml,
    });
  }
  if (read.mlApplicable !== true || !hasKeys(read.mlDetail)) return unbacked();
  const detail = read.mlDetail;
  const confidence = asNumber(detail.confidence);
  const model = asText(detail.model_id) ?? asText(detail.model);
  return resolution({
    state: STAGE_COMPLETE,
    backed: true,
    summary: [model, confidence === null ? null : `confidence ${confidence}`]
      .filter((part) => part !== null)
      .join(' · ') || null,
    latencyMs: asNumber(detail.inference_ms),
    detail,
  });
}

/**
 * Stage 4. The one stage with no dedicated record: it is derived from `LOGIC`-category nodes
 * plus `signal.decision`, either of which is real backing.
 *
 * The empty-`dag_nodes` case is `not-available` with {@link REASON_TRACE_RETENTION} and never
 * `pending` — see the module header. When nodes WERE retained but none is `LOGIC`-category,
 * retention is demonstrably not the explanation, so the reason says what is actually true.
 */
function resolveLogic(read) {
  if (read.logicNodes.length > 0) {
    const failed = read.logicNodes.some(nodeFailed);
    return resolution({
      state: failed ? STAGE_BLOCKED : STAGE_COMPLETE,
      backed: true,
      summary: `${read.logicNodes.length} logic node${read.logicNodes.length === 1 ? '' : 's'}${read.decision === null ? '' : ` → ${read.decision}`}`,
      reason: failed ? asText(read.logicNodes.find(nodeFailed)?.error) : null,
      latencyMs: totalExecutionMs(read.logicNodes),
      detail: { source: read.dagSource, nodes: read.logicNodes, decision: read.decision },
    });
  }

  if (read.nodes.length === 0) {
    return resolution({
      state: STAGE_NOT_AVAILABLE,
      backed: false,
      reason: REASON_TRACE_RETENTION,
    });
  }

  if (read.decision !== null) {
    return resolution({
      state: STAGE_COMPLETE,
      backed: true,
      summary: `Decision ${read.decision}`,
      detail: { source: read.dagSource, nodes: [], decision: read.decision },
    });
  }

  return resolution({ state: STAGE_NOT_AVAILABLE, backed: false, reason: REASON_NO_LOGIC_NODE });
}

function resolveSignal(read) {
  const event = read.events[EVENT_SIGNAL_GENERATED];
  if (!event && read.decision === null) return unbacked();
  const data = asRecord(event?.data) ?? {};
  const decision = asText(data.decision) ?? read.decision;
  return resolution({
    state: STAGE_COMPLETE,
    backed: true,
    summary: decision,
    timestamp: asText(event?.timestamp),
    detail: { event: event ?? null, decision },
  });
}

/**
 * Stage 6. `RISK_EVALUATED` and `ORDER_CREATED` are one stage: the decision to place an order
 * and the order that came of it. A refusal here is what ends the lifecycle for stages 7–9.
 */
function resolveOrderDecision(read) {
  const risk = read.events[EVENT_RISK_EVALUATED];
  const order = read.events[EVENT_ORDER_CREATED];
  const hasSection = hasKeys(read.riskDetail);
  if (!risk && !order && !hasSection) return unbacked();

  const riskData = asRecord(risk?.data) ?? {};
  // Three spellings of the one verdict, in order of authority: the persisted column the
  // timeline carries, then the section's own `passed`, then its `blocked` complement.
  const passed = asFlag(riskData.risk_passed) ?? asFlag(read.riskDetail.passed);
  const blocked = asFlag(read.riskDetail.blocked);
  const refused = passed === false || blocked === true;
  const orderData = asRecord(order?.data) ?? {};
  const orderId = asText(orderData.order_id);

  return resolution({
    state: refused ? STAGE_BLOCKED : STAGE_COMPLETE,
    backed: true,
    summary: refused
      ? 'Refused at risk validation'
      : ['Risk passed', orderId === null ? null : `order ${orderId}`]
          .filter((part) => part !== null)
          .join(' · '),
    reason: refused
      ? asText(riskData.risk_reason) ?? asText(read.riskDetail.reason)
      : null,
    timestamp: asText(order?.timestamp) ?? asText(risk?.timestamp),
    detail: {
      source: read.riskSource,
      risk: read.riskDetail,
      risk_event: risk ?? null,
      order_event: order ?? null,
    },
  });
}

function resolveSubmission(read) {
  const event = read.events[EVENT_EXCHANGE_RESPONSE];
  if (!event) return unbacked();
  const data = asRecord(event.data) ?? {};
  const status = asText(data.order_status);
  const rejected = isRejectedStatus(status);
  return resolution({
    state: rejected ? STAGE_BLOCKED : STAGE_COMPLETE,
    backed: true,
    summary: [asText(data.exchange_order_id), status].filter((part) => part !== null).join(' · ') || null,
    reason: rejected ? status : null,
    timestamp: asText(event.timestamp),
    detail: { event, exchange_response: read.exchangeResponse },
  });
}

/**
 * Stage 8. `EXECUTED` is the fill. With no `EXECUTED` event, a `failure_reason` on the
 * execution outcome is still a backing record — `_execution_outcome_of` reports that field
 * only for a state that actually failed — so a failed execution is `blocked`, not pending.
 */
function resolveExecution(read) {
  const event = read.events[EVENT_EXECUTED];
  const outcome = read.executionOutcome;
  const failureReason = asText(outcome.failure_reason);

  if (!event) {
    if (failureReason === null) return unbacked();
    return resolution({
      state: STAGE_BLOCKED,
      backed: true,
      summary: 'Not executed',
      reason: failureReason,
      latencyMs: asNumber(outcome.latency_ms),
      detail: { event: null, outcome, exchange_response: read.exchangeResponse },
    });
  }

  const filled = asNumber(outcome.filled_quantity);
  const price = asNumber(outcome.execution_price);
  return resolution({
    state: STAGE_COMPLETE,
    backed: true,
    summary: [
      filled === null ? null : `filled ${filled}`,
      price === null ? null : `@ ${price}`,
    ]
      .filter((part) => part !== null)
      .join(' ') || null,
    latencyMs: asNumber(outcome.latency_ms),
    timestamp: asText(event.timestamp) ?? asText(outcome.executed_at),
    detail: { event, outcome, exchange_response: read.exchangeResponse },
  });
}

/**
 * Stage 9, BC-6. Reads `POSITION_UPDATED` — a real event since task 12.6. `data.not_available`
 * names the transition fields the row did not report and `data.not_available_reason` says why
 * in one sentence; both are the server's and are passed through untouched.
 */
function resolvePositionUpdate(read) {
  const event = read.events[EVENT_POSITION_UPDATED];
  if (!event) return unbacked();
  const data = asRecord(event.data) ?? {};
  const symbol = asText(data.symbol);
  const direction = asText(data.direction);
  const delta = asNumber(data.quantity_delta);
  return resolution({
    state: STAGE_COMPLETE,
    backed: true,
    summary: [symbol, direction, delta === null ? null : `Δ ${delta}`]
      .filter((part) => part !== null)
      .join(' · ') || null,
    reason: asText(data.not_available_reason),
    timestamp: asText(event.timestamp),
    detail: data,
  });
}

/** Canonical id → its resolver. Keyed by the id so a missing resolver is a visible hole. */
const RESOLVERS = Object.freeze({
  [STAGE_MARKET_DATA]: resolveMarketData,
  [STAGE_INDICATORS]: resolveIndicators,
  [STAGE_MODEL]: resolveModel,
  [STAGE_LOGIC]: resolveLogic,
  [STAGE_SIGNAL]: resolveSignal,
  [STAGE_ORDER_DECISION]: resolveOrderDecision,
  [STAGE_SUBMISSION]: resolveSubmission,
  [STAGE_EXECUTION]: resolveExecution,
  [STAGE_POSITION_UPDATE]: resolvePositionUpdate,
});

// ── The projection ──────────────────────────────────────────────────────────────────────

/**
 * The degraded note, or `null`. `degraded` is `signal_service._lifecycle_degradation`'s block:
 * `null` when migration 005b is applied, otherwise `{migration, reason,
 * unrepresentable_lifecycle_states}`. The server's own `reason` is preferred over
 * {@link REASON_DEGRADED_FALLBACK}, because the server knows which degradation this is.
 *
 * @param {unknown} payload
 * @returns {{message: string, migration: string|null,
 *   unrepresentableLifecycleStates: ReadonlyArray<string>, lifecycleStateSource: string|null}|null}
 */
export function signalTraceDegradation(payload) {
  const response = asRecord(payload) ?? {};
  const degraded = asRecord(response.degraded);
  if (degraded === null) return null;
  return Object.freeze({
    message: asText(degraded.reason) ?? REASON_DEGRADED_FALLBACK,
    migration: asText(degraded.migration),
    unrepresentableLifecycleStates: Object.freeze(
      asArray(degraded.unrepresentable_lifecycle_states)
        .map(asText)
        .filter((state) => state !== null),
    ),
    lifecycleStateSource: asText(response.lifecycle_state_source),
  });
}

/**
 * The nine stages of one signal trace, plus the response's degradation facts.
 *
 * Built from {@link SIGNAL_TRACE_STAGES} outward: the result always has exactly nine entries,
 * one per canonical id, in canonical order, whatever `payload` is. An unrecognised event
 * cannot add an entry and an absent one cannot remove any.
 *
 * Two passes. The first resolves each stage from its own backing record alone, so a stage's
 * state is a function of what backs it (Property 16). The second settles the unbacked ones,
 * which is the only place stages are read in relation to each other: once a stage is
 * `blocked`, everything after it is `not-available` because the lifecycle ended there;
 * otherwise an unbacked stage is `pending` if an earlier stage completed, and `not-available`
 * with the retention reason where a resolver already said so.
 *
 * Pure: no clock, no network, no module state, no mutation of `payload`.
 *
 * @param {unknown} payload The `GET /api/signal-trace/signals/{id}` body, or anything at all.
 * @returns {{stages: ReadonlyArray<object>, degraded: object|null, isDegraded: boolean,
 *   lifecycleStateSource: string|null}}
 */
export function buildSignalTraceStages(payload) {
  const read = readPayload(payload);
  const resolved = SIGNAL_TRACE_STAGES.map((stage) => RESOLVERS[stage.id](read));

  // Which stage stopped the signal, if one did. Stages after it can never occur.
  const blockedAt = resolved.findIndex((entry) => entry.state === STAGE_BLOCKED);
  const blockedName = blockedAt === -1 ? null : SIGNAL_TRACE_STAGES[blockedAt].name;

  const stages = SIGNAL_TRACE_STAGES.map((stage, index) => {
    const entry = resolved[index];
    let { state, reason } = entry;

    if (state === STAGE_PENDING) {
      if (blockedAt !== -1 && index > blockedAt) {
        state = STAGE_NOT_AVAILABLE;
        reason = reasonLifecycleEnded(blockedName);
      } else {
        reason = REASON_NOT_YET_REACHED;
      }
    }

    return Object.freeze({
      id: stage.id,
      number: stage.number,
      name: stage.name,
      state,
      backed: entry.backed,
      summary: entry.summary,
      reason,
      latencyMs: entry.latencyMs,
      timestamp: entry.timestamp,
      detail: entry.detail,
    });
  });

  const degraded = signalTraceDegradation(payload);
  return Object.freeze({
    stages: Object.freeze(stages),
    degraded,
    isDegraded: degraded !== null,
    lifecycleStateSource: asText(asRecord(payload)?.lifecycle_state_source),
  });
}
