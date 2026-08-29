/**
 * nodeTrace.js — the inspector's execution-trace projection. Task 9.3, Requirement 24.6.
 *
 * "THE Strategy_Builder SHALL display the recorded execution trace for a selected node,
 * comprising that node's inputs, outputs, duration and recorded failures."
 *
 * Nothing here records, measures or decides anything
 * -------------------------------------------------
 * `design.md § Observability` is explicit: "`dag_engine.ExecutionTracer` already records
 * per-node inputs, outputs, duration and failures, and `signal_trace_engine.py` records signal
 * provenance. Both are reused". There is therefore no third trace store, and there is no
 * fourth one in this file either. This module is a **projection**: it renames the wire fields
 * to the shape a React component reads, and it formats a recorded shape as text. It does not
 * time anything, does not sum durations (the endpoint already did, and a node started twice in
 * one run is why that sum is not "the last entry"), does not decide whether a node failed and
 * does not compose the sentence that explains a run.
 *
 * That last point is the same rule `NodePreview` follows for `detail.message` and
 * `ParameterForm` for `fix_hint`: the sentence is the server's, rendered verbatim. Every fact
 * in `summary` — which node the runtime stopped at, whether this node was reached at all,
 * which numeric condition it recorded — is a fact about a run only the server observed, so
 * composing it here would be composing it from four fields, in a browser, out of a vocabulary
 * the engine owns.
 *
 * Where the trace comes from
 * --------------------------
 * It rides the node-preview response, on both of its outcomes:
 *
 *   200 → `body.trace`
 *   422 `PREVIEW_EXECUTION_FAILED` → `error.data.detail.trace`
 *
 * The second is the one that matters most. A run that produced nothing is exactly the run
 * whose recorded inputs, duration and failure answer "why did nothing happen?", and the
 * `ExecutionTracer` holds them whether or not `execute_dag` returned. So both paths are
 * projected by the same function: a trace that appeared only on success would be missing from
 * every case an author actually needs it for.
 *
 * There is deliberately **no second request** and no channel of its own. An `ExecutionTracer`
 * is populated by a run and lives as long as the engine that ran it; a `GET .../trace` would
 * have to re-execute to have anything to report, which is two answers to one question — SB-01's
 * shape — and would cost a second ownership resolution and a second rate limit. The *live*
 * half of "why did nothing happen?" is already published by task 8.5 on
 * `deployment.{deployment_id}` and projected by `builderRealtime.nodeRuntimeView`; the panel
 * renders that reading beside the recorded trace rather than subscribing to anything new.
 *
 * Wire shape consumed, verbatim from `_node_trace_payload`:
 *
 *   { node_id, status, recorded, duration_ms, summary,
 *     executions: [ { node_id, node_type, status, duration_ms, error_message, recorded_at,
 *                     inputs: [ { port, type, shape } ], output: { type, shape } } ],
 *     failures: [ …same entry shape… ],
 *     blocking_failure: entry|null,
 *     conditions: [ { node_id, code, bar, bars_affected, detail, display } ],
 *     executed_nodes: [ node_id ],
 *     signal_provenance: { available, traces_seen, records: [ … ], message } }
 *
 * Fail soft, not closed — and the distinction is deliberate
 * --------------------------------------------------------
 * `projectPreview` throws on a malformed body, because a preview that cannot be read must not
 * render as a block that produces nothing. A trace is a **diagnostic about** that preview, and
 * throwing here would take the values off screen to report that the explanation was
 * unreadable. So an absent or malformed trace projects to `null` and the panel says no trace
 * was recorded. "Nothing was recorded" and "this block produced nothing" stay different
 * statements, which is the property that actually matters.
 *
 * Nothing identifying or secret can travel through here
 * ----------------------------------------------------
 * Every field is copied by **name**. The projection is an allow-list by construction, so an
 * `exchange`, `api_key` or `exchange_account_id` key added to a payload upstream is dropped
 * rather than rendered (SB-06, Requirement 12.1) — asserted in `tests/unit/nodeTrace.test.js`
 * rather than left to review. The backend allow-lists the same fields on its side; this is the
 * second of the two, not a substitute for it.
 */

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

/**
 * The statuses `_node_trace_payload` reports for a node.
 *
 * `NOT_EXECUTED` is a different fact from a node that ran and produced nothing, and the two
 * must not render alike: one means "look upstream", the other means "look at this block".
 */
export const TRACE_STATUSES = Object.freeze({
  SUCCESS: 'success',
  FAIL: 'fail',
  PENDING: 'pending',
  NOT_EXECUTED: 'not_executed',
});

/** Display words. Text, so a status is never carried by colour alone. */
export const TRACE_STATUS_LABELS = Object.freeze({
  [TRACE_STATUSES.SUCCESS]: 'Executed',
  [TRACE_STATUSES.FAIL]: 'Failed',
  [TRACE_STATUSES.PENDING]: 'Started, no outcome recorded',
  [TRACE_STATUSES.NOT_EXECUTED]: 'Did not execute',
});

/** What an unrecorded figure reads as. Never `0`: a missing duration is not a fast one. */
export const UNRECORDED_TEXT = '—';

const isPlainObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const nonEmptyString = (value) =>
  typeof value === 'string' && value.trim() !== '' ? value.trim() : null;

const finiteNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const asList = (value) => (Array.isArray(value) ? value : []);

// ---------------------------------------------------------------------------
// Shapes, as text
// ---------------------------------------------------------------------------

/**
 * One recorded shape as a label.
 *
 * `ExecutionTracer` records a shape as a small dict — `{"type": "Series", "length": 300}`,
 * `{"type": "DataFrame", "shape": [300, 12]}`, `{"type": "scalar", "value": 4}` — because that
 * is what a recorder can write without knowing what a renderer wants. This turns it into the
 * one line a table cell holds. It reads the dict's own keys and invents nothing: an unfamiliar
 * shape renders as its `type`, and a shape with no type at all renders as unrecorded, because
 * guessing would put a claim about a value where the run recorded none.
 *
 * @param {object|null} shape A recorded `input_shape` / `output_shape` entry.
 * @returns {string}
 */
export function describeShape(shape) {
  if (!isPlainObject(shape)) return UNRECORDED_TEXT;
  const type = nonEmptyString(shape.type);
  if (type === null) return UNRECORDED_TEXT;

  const length = finiteNumber(shape.length);
  if (length !== null) return `${type}[${length}]`;

  const dims = asList(shape.shape).map(finiteNumber).filter((n) => n !== null);
  if (dims.length > 0) return `${type}[${dims.join('\u00d7')}]`;

  // A scalar records the value itself, and it is the one shape whose *value* is the shape.
  if (Object.prototype.hasOwnProperty.call(shape, 'value')) {
    const value = shape.value;
    if (value === null) return `${type} ${UNRECORDED_TEXT}`;
    if (typeof value === 'number') {
      return Number.isFinite(value) ? `${type} ${value}` : `${type} ${UNRECORDED_TEXT}`;
    }
    if (typeof value === 'boolean') return `${type} ${value ? 'true' : 'false'}`;
    return type;
  }
  return type;
}

/**
 * A duration as text. Milliseconds, the unit the tracer records in.
 *
 * `null` is not `0`. A node that was never timed and a node that took no measurable time are
 * different facts, and only one of them means the block ran.
 *
 * @param {number|null} durationMs
 * @returns {string}
 */
export function describeDuration(durationMs) {
  const value = finiteNumber(durationMs);
  if (value === null) return UNRECORDED_TEXT;
  return `${value} ms`;
}

// ---------------------------------------------------------------------------
// Projection
// ---------------------------------------------------------------------------

/**
 * One bound input: which port, from which upstream node, what type, what shape.
 * Requirement 24.6's "inputs".
 *
 * Both halves travel because they answer different questions. `port` is the one an author
 * asks — "which of this block's inputs was that" — and a ULID is not an answer to it; `source`
 * is the node the value came from, which is what makes a trace of a graph followable.
 */
const projectInput = (raw) => {
  const entry = isPlainObject(raw) ? raw : {};
  const shape = isPlainObject(entry.shape) ? entry.shape : null;
  const port = nonEmptyString(entry.port);
  const source = nonEmptyString(entry.source);
  return {
    port,
    /** The upstream node the value came from — what makes a trace of a graph followable. */
    source,
    /**
     * What the row is called on screen. The port when the run recorded one, else the source.
     * A recorded input with no port is a legacy plain-dict call site: the row is kept and
     * labelled by its source rather than dropped or given an invented port name.
     */
    label: port ?? source ?? UNRECORDED_TEXT,
    type: nonEmptyString(entry.type),
    shape,
    shapeText: describeShape(shape),
  };
};

/** The recorded output. Requirement 24.6's "outputs". */
const projectOutput = (raw) => {
  const source = isPlainObject(raw) ? raw : {};
  const shape = isPlainObject(source.shape) ? source.shape : null;
  return {
    type: nonEmptyString(source.type),
    shape,
    shapeText: describeShape(shape),
  };
};

/**
 * One traced execution of one node.
 *
 * A list rather than a single entry, because one node can be started more than once in a run
 * and reporting only the last would hide a first attempt that failed — the reason
 * `ExecutionTracer.entries_for_node` returns a tuple.
 */
const projectExecution = (raw) => {
  const source = isPlainObject(raw) ? raw : {};
  return {
    nodeId: nonEmptyString(source.node_id),
    nodeType: nonEmptyString(source.node_type),
    status: nonEmptyString(source.status),
    durationMs: finiteNumber(source.duration_ms),
    durationText: describeDuration(source.duration_ms),
    // The engine's own message, verbatim. Requirement 24.6's "recorded failures" is a
    // statement the runtime made; paraphrasing it would give the author a second account.
    errorMessage: nonEmptyString(source.error_message),
    recordedAt: nonEmptyString(source.recorded_at),
    inputs: asList(source.inputs).map(projectInput),
    output: projectOutput(source.output),
  };
};

/** One recorded numeric condition, with the sentence the endpoint composed for it. */
const projectCondition = (raw) => {
  const source = isPlainObject(raw) ? raw : {};
  return {
    nodeId: nonEmptyString(source.node_id),
    code: nonEmptyString(source.code),
    bar: finiteNumber(source.bar),
    barsAffected: finiteNumber(source.bars_affected),
    detail: nonEmptyString(source.detail),
    /** The endpoint's sentence. `code` is the fallback, not a reconstruction of it. */
    display: nonEmptyString(source.display) || nonEmptyString(source.code) || '',
  };
};

/** One node execution inside a live signal trace (`signal_trace_engine`'s `DAGNodeTrace`). */
const projectProvenanceExecution = (raw) => {
  const source = isPlainObject(raw) ? raw : {};
  const io = (entry) => {
    const item = isPlainObject(entry) ? entry : {};
    return {
      key: nonEmptyString(item.key),
      value: nonEmptyString(item.value),
      dtype: nonEmptyString(item.dtype),
    };
  };
  return {
    nodeType: nonEmptyString(source.node_type),
    label: nonEmptyString(source.label),
    durationMs: finiteNumber(source.duration_ms),
    durationText: describeDuration(source.duration_ms),
    status: nonEmptyString(source.status),
    errorMessage: nonEmptyString(source.error_message),
    cacheHit: source.cache_hit === true,
    inputs: asList(source.inputs).map(io),
    outputs: asList(source.outputs).map(io),
  };
};

/**
 * What `signal_trace_engine` recorded for this node on live signals.
 *
 * A separate question about the same block: the recorded trace above is a preview run the
 * author just asked for, and this is what happened when the strategy was actually running.
 * "Not available" is a first-class answer with a sentence, never an absent key — a store that
 * has not been started and a block that never fired are both legitimate, and neither is an
 * error.
 *
 * No venue travels here. `SignalTraceRecord` carries an `exchange` and the endpoint
 * deliberately does not read it (SB-06); this projection names the fields it accepts, so it
 * could not render one even if a payload grew it.
 */
const projectProvenance = (raw) => {
  const source = isPlainObject(raw) ? raw : {};
  return {
    available: source.available === true,
    tracesSeen: finiteNumber(source.traces_seen) ?? 0,
    /** The endpoint's own sentence, rendered verbatim. */
    message: nonEmptyString(source.message),
    records: asList(source.records)
      .filter(isPlainObject)
      .map((record) => ({
        traceId: nonEmptyString(record.trace_id),
        recordedAt: nonEmptyString(record.recorded_at),
        status: nonEmptyString(record.status),
        finalDecision: nonEmptyString(record.final_decision),
        /** The market, from the record. Never a venue. */
        symbol: nonEmptyString(record.symbol),
        latencyMs: finiteNumber(record.latency_ms),
        executions: asList(record.executions).map(projectProvenanceExecution),
      })),
  };
};

/**
 * Project a `trace` payload into the render model, or `null`.
 *
 * @param {object|null|undefined} raw The endpoint's `trace` object.
 * @returns {object|null} The frozen render model, or `null` when there is no readable trace.
 */
export function projectNodeTrace(raw) {
  if (!isPlainObject(raw)) return null;
  const nodeId = nonEmptyString(raw.node_id);
  if (nodeId === null) return null;

  const status = nonEmptyString(raw.status) || TRACE_STATUSES.NOT_EXECUTED;
  const executions = asList(raw.executions).map(projectExecution);
  const failures = asList(raw.failures).map(projectExecution);
  const blocking = isPlainObject(raw.blocking_failure)
    ? projectExecution(raw.blocking_failure)
    : null;

  return Object.freeze({
    nodeId,
    /** The endpoint's verdict about this node, not one re-derived from the entry list. */
    status,
    statusLabel: TRACE_STATUS_LABELS[status] || status,
    /** Whether the tracer holds any entry at all for this node. */
    recorded: raw.recorded === true,
    /** Summed by the endpoint across every entry, because a node can run twice. */
    durationMs: finiteNumber(raw.duration_ms),
    durationText: describeDuration(raw.duration_ms),
    /** The server's sentence. Rendered verbatim; see the module docblock. */
    summary: nonEmptyString(raw.summary) || '',
    executions: Object.freeze(executions),
    /** The whole run's failures, so an upstream one is visible from the node it starved. */
    failures: Object.freeze(failures),
    /** The earliest failure at or upstream of this node — the one an author can act on. */
    blockingFailure: blocking,
    /**
     * True when the thing that went wrong was somewhere else. The comparison is between two
     * ids the endpoint sent; the *choice* of which failure is blocking stays the endpoint's,
     * made against the compiler's execution order.
     */
    blockedUpstream: blocking !== null && blocking.nodeId !== nodeId,
    conditions: Object.freeze(asList(raw.conditions).map(projectCondition)),
    executedNodes: Object.freeze(
      asList(raw.executed_nodes).map((id) => nonEmptyString(id)).filter(Boolean),
    ),
    signalProvenance: Object.freeze(projectProvenance(raw.signal_provenance)),
  });
}

/**
 * The trace on a successful preview response.
 *
 * @param {object|null} body The endpoint's 200 body.
 * @returns {object|null}
 */
export function traceFromPreviewBody(body) {
  return isPlainObject(body) ? projectNodeTrace(body.trace) : null;
}

/**
 * The trace on a refused preview.
 *
 * Only `PREVIEW_EXECUTION_FAILED` carries one — a graph the validator refused never reached an
 * executor, so there is nothing recorded to show, and saying "no trace" there is honest rather
 * than a gap. Both axios shapes are read (`error.data` and `error.response.data`), the same two
 * `previewError` reads, so the two cannot disagree about where a body lives.
 *
 * @param {any} error A rejected request.
 * @returns {object|null}
 */
export function traceFromPreviewFailure(error) {
  const data = error?.data ?? error?.response?.data ?? null;
  if (!isPlainObject(data)) return null;
  const detail = isPlainObject(data.detail) ? data.detail : data;
  return projectNodeTrace(detail.trace);
}

/**
 * Whether there is anything worth showing for the selected node.
 *
 * A trace with no entries, no failures and no conditions still has a `summary` — "this block
 * did not execute, and the run recorded no failure. Nothing upstream of it produced a value to
 * work from" is an answer, and it is frequently *the* answer. So this is true for any readable
 * trace: the panel's job is to say what was recorded, including that nothing was.
 *
 * @param {object|null} trace A projected trace.
 * @returns {boolean}
 */
export const hasTrace = (trace) => isPlainObject(trace) && typeof trace.nodeId === 'string';
