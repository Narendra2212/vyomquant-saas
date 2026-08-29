/**
 * nodePreview.js — the inspector's node-preview state machine and its render projection.
 * Task 5.7, Requirements 24.7 and 24.8.
 *
 * Everything here is pure. The page (`pages/StrategyBuilder.jsx`) owns the timer, the request
 * and the React state; this module owns the rules, so they can be asserted without a DOM and
 * cannot quietly differ between the inspector and anything else that later renders a preview.
 * Same division as `lib/graphValidation.js`, for the same reason.
 *
 * The backend computes the preview. This module transports it
 * ----------------------------------------------------------
 * `POST /api/strategy-operations/strategies/{strategy_id}/nodes/{node_id}/preview`
 * (`backend_app/routers/strategy_operations.py`) runs the previewed node's upstream closure
 * through the real validator, the real `StrategyCompiler`, the real `plan_to_engine_graph` and
 * the real `DAGEngine`, over a server-bounded historical window — that *is* Requirement 24.7.
 *
 * So there is no arithmetic in this file. Not a rolling mean, not a lag, not a comparison
 * against a threshold. A preview recomputed in the client would be a second answer to "what
 * does this block produce?", visible at exactly the moment an author decides whether to
 * deploy, and free to drift from the one that trades — SB-01 with a nicer font. The only
 * numbers this module touches are the ones the response already carried, and the only thing
 * it does to them is decide whether they can be shown as a numeral (see `formatPreviewValue`).
 *
 * Response shape consumed, verbatim from the endpoint:
 *
 *   { strategy_id, node_id, block_id, category, graph_source, dag_hash, compiler_version,
 *     market: { symbol, timeframe },
 *     window: { requested, bars, max_bars, min_bars, sample_rows, node_warmup_bars,
 *               needed_bars, clamped, available_bars, warmup_bars, plan_warmup_bars,
 *               warmup_exceeds_window, first_timestamp, last_timestamp },
 *     executed_nodes: [node_id],
 *     outputs: [ { name, port_type, produced, kind, … } ],
 *     issues: [ { code, node_id, … } ],
 *     computed_at }
 *
 * An output is one of four `kind`s, and each is transported as it is:
 *
 *   `series`          { length, index[], values[], empty_values }
 *   `feature_matrix`  { length, columns[], column_count, sampled_columns[], sample_truncated,
 *                       column_warmup{}, provenance{}, warmup_offset, index[], values[][] }
 *   `scalar`          { value }
 *   `unrenderable`    { python_type, message }
 *
 * `null` is a value, and it is not zero
 * -------------------------------------
 * The endpoint sends `null` for a bar with no value, because NaN is not a JSON number and the
 * warmup region genuinely has no value. Rendering that as `0` would put a number an author
 * could compare against a threshold where there is none. `formatPreviewValue` returns the
 * `EMPTY_VALUE_TEXT` placeholder, and `emptyValueCount` is carried through so the count is
 * stated rather than left to be eyeballed.
 *
 * One message source
 * ------------------
 * A refusal's text is the backend's own `detail.message` and `detail.hint`, rendered verbatim
 * — the same rule `ParameterForm` follows for `fix_hint` (Requirement 8.9). The only strings
 * authored here describe the *client's* state ("no preview requested yet", "the preview
 * request failed"), which is information the backend cannot supply because it was not asked.
 *
 * Fail closed
 * -----------
 * Zero outputs unless the state is `ready`, and a malformed body is an error rather than an
 * empty preview: "this block produced nothing" and "nobody could tell you what this block
 * produced" are different facts, and only one of them is about the strategy.
 */

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

/** The preview's observable states. */
export const PREVIEW_STATES = Object.freeze({
  /** No preview has been asked for, or the selection changed and the old one was dropped. */
  IDLE: 'idle',
  LOADING: 'loading',
  READY: 'ready',
  ERROR: 'error',
});

/**
 * Milliseconds after the last edit before a held preview is re-requested.
 *
 * The same 400 ms the validator uses (`VALIDATION_DEBOUNCE_MS`), and deliberately the same
 * number: both are answers about the graph on the canvas, and a preview that refreshed on a
 * different rhythm would show values for one version of the graph beside a verdict for
 * another. Editing a parameter costs one preview, not one per keystroke.
 */
export const PREVIEW_DEBOUNCE_MS = 400;

/** What a bar with no value reads as. Never `0`, and never blank. */
export const EMPTY_VALUE_TEXT = '—';

/** The four output shapes the endpoint transports. */
export const OUTPUT_KINDS = Object.freeze({
  SERIES: 'series',
  FEATURE_MATRIX: 'feature_matrix',
  SCALAR: 'scalar',
  UNRENDERABLE: 'unrenderable',
});

/** The category whose preview must name its produced columns (Requirement 24.8). */
export const FEATURE_CATEGORY = 'FEATURE_ENGINEERING';

const isPlainObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const nonEmptyString = (value) =>
  typeof value === 'string' && value.trim() !== '' ? value.trim() : null;

/** A finite number, or null. Anything else — including a numeric string — is not a value. */
const finiteNumber = (value) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const asList = (value) => (Array.isArray(value) ? value : []);

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

/**
 * The identity of one preview: which strategy, which node, and which version of the graph.
 *
 * The graph key is part of it because a preview is a statement about a specific graph. Change
 * a window from 20 to 50 and the held preview describes something that is no longer on the
 * canvas; keying on the graph is what lets that be detected rather than left on screen.
 *
 * @param {string|null} strategyId
 * @param {string|null} nodeId
 * @param {string|null} graphKey `semanticGraphKey(graph)` from `lib/graphValidation.js`.
 * @returns {string|null} null when any part is missing, i.e. when no preview is possible.
 */
export function previewKey(strategyId, nodeId, graphKey) {
  const strategy = nonEmptyString(strategyId);
  const node = nonEmptyString(nodeId);
  const graph = nonEmptyString(graphKey);
  if (strategy === null || node === null || graph === null) return null;
  return `${strategy}\u0000${node}\u0000${graph}`;
}

// ---------------------------------------------------------------------------
// Why a preview cannot be asked for yet
// ---------------------------------------------------------------------------

/**
 * Whether a preview can be requested, and if not, the reason in the author's terms.
 *
 * The endpoint is addressed by strategy id, so an unsaved canvas has nothing to address. That
 * is stated plainly rather than expressed as a greyed-out button with no explanation: "save
 * the strategy first" is actionable, a disabled control is not.
 *
 * @param {{strategyId: string|null, nodeId: string|null, graphKey: string|null,
 *   validationState: string|null}} input
 * @returns {{available: boolean, reason: string|null, code: string|null}}
 */
export function previewAvailability({
  strategyId = null,
  nodeId = null,
  graphKey = null,
  validationState = null,
} = {}) {
  if (nonEmptyString(nodeId) === null) {
    return {
      available: false,
      code: 'PREVIEW_NO_NODE_SELECTED',
      reason: 'Select a block to preview what it produces.',
    };
  }
  if (nonEmptyString(graphKey) === null) {
    return {
      available: false,
      code: 'PREVIEW_GRAPH_UNSERIALIZABLE',
      reason: 'This canvas cannot be serialized, so there is no graph to compute a preview from.',
    };
  }
  if (nonEmptyString(strategyId) === null) {
    return {
      available: false,
      code: 'PREVIEW_STRATEGY_UNSAVED',
      reason:
        'Save this strategy first. A preview is computed by the runtime against a saved ' +
        'strategy, so there is nothing to address until it has an id.',
    };
  }
  if (validationState === 'invalid') {
    return {
      available: false,
      code: 'PREVIEW_GRAPH_INVALID',
      reason:
        'This graph has validation errors. A preview is computed by the runtime, so a graph ' +
        'the runtime would refuse has no preview.',
    };
  }
  return { available: true, code: null, reason: null };
}

// ---------------------------------------------------------------------------
// Projection: the response, in the shape the inspector renders
// ---------------------------------------------------------------------------

/** Raised when a preview body does not carry a preview. */
export class PreviewProjectionError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = 'PreviewProjectionError';
    this.code = 'PREVIEW_MALFORMED';
    this.details = details;
  }
}

/**
 * One value, as text.
 *
 * `null` — the wire form of a bar with no value — becomes {@link EMPTY_VALUE_TEXT}. So does
 * anything that is not a finite number, because a preview showing `NaN` or `Infinity` as a
 * numeral would be showing something JSON cannot even carry.
 *
 * Significant digits, not decimal places: an indicator's output can be 0.00004 (a return) or
 * 68000 (a price), and a fixed `toFixed(2)` renders the first as `0.00` — a number that reads
 * as zero and is not.
 *
 * @param {number|null} value
 * @param {{digits?: number}} [options]
 * @returns {string}
 */
export function formatPreviewValue(value, { digits = 6 } = {}) {
  const number = finiteNumber(value);
  if (number === null) return EMPTY_VALUE_TEXT;
  if (Number.isInteger(number) && Math.abs(number) < 1e15) return String(number);
  return Number(number.toPrecision(digits)).toString();
}

/** True when this output carries a FEATURE_MATRIX (Requirement 24.8's subject). */
export const isFeatureMatrixOutput = (output) =>
  isPlainObject(output) && output.kind === OUTPUT_KINDS.FEATURE_MATRIX;

const projectSeries = (raw) => {
  const values = asList(raw.values).map(finiteNumber);
  const index = asList(raw.index).map((label) => nonEmptyString(label));
  return {
    kind: OUTPUT_KINDS.SERIES,
    length: finiteNumber(raw.length) ?? values.length,
    sampleRows: values.length,
    emptyValueCount: finiteNumber(raw.empty_values) ?? values.filter((v) => v === null).length,
    /** One row per sampled bar, newest last — the order the endpoint sent. */
    rows: values.map((value, position) => ({
      index: index[position] ?? null,
      value,
      text: formatPreviewValue(value),
      empty: value === null,
    })),
  };
};

const projectFeatureMatrix = (raw) => {
  // Every produced column *name* (Requirement 24.8), not only the sampled ones: the names are
  // how an author tells `lag_1` from `lag_3`, and truncating them would remove the half of the
  // requirement that costs nothing to serve.
  const columns = asList(raw.columns).map((name) => nonEmptyString(name)).filter(Boolean);
  const sampledColumns = asList(raw.sampled_columns)
    .map((name) => nonEmptyString(name))
    .filter(Boolean);
  const index = asList(raw.index).map((label) => nonEmptyString(label));
  const columnWarmup = isPlainObject(raw.column_warmup) ? raw.column_warmup : {};
  const provenance = isPlainObject(raw.provenance) ? raw.provenance : {};

  const rows = asList(raw.values).map((rowValues, position) => {
    const cells = asList(rowValues).map((value, column) => {
      const number = finiteNumber(value);
      return {
        column: sampledColumns[column] ?? null,
        value: number,
        text: formatPreviewValue(number),
        empty: number === null,
      };
    });
    return { index: index[position] ?? null, cells };
  });

  return {
    kind: OUTPUT_KINDS.FEATURE_MATRIX,
    length: finiteNumber(raw.length) ?? rows.length,
    sampleRows: rows.length,
    columns,
    columnCount: finiteNumber(raw.column_count) ?? columns.length,
    sampledColumns,
    /** The endpoint states this; it is rendered rather than inferred from lengths. */
    sampleTruncated: raw.sample_truncated === true,
    /** Per-column warmup, so "this column is empty here" has a stated reason. */
    columnWarmup: columns.map((name) => ({
      column: name,
      warmup: finiteNumber(columnWarmup[name]),
      producedBy: nonEmptyString(provenance[name]),
    })),
    warmupOffset: finiteNumber(raw.warmup_offset),
    emptyValueCount:
      finiteNumber(raw.empty_values) ??
      rows.reduce((total, row) => total + row.cells.filter((cell) => cell.empty).length, 0),
    rows,
  };
};

const projectOutput = (raw) => {
  if (!isPlainObject(raw)) {
    throw new PreviewProjectionError('A preview output entry is not an object', { received: raw });
  }
  const name = nonEmptyString(raw.name);
  if (name === null) {
    throw new PreviewProjectionError('A preview output carries no port name', { received: raw });
  }
  const common = {
    name,
    portType: nonEmptyString(raw.port_type),
    // A port that produced nothing is rendered as such. A missing port and an empty port are
    // different facts, and the endpoint distinguishes them, so this does too.
    produced: raw.produced === true,
  };
  if (!common.produced) {
    return { ...common, kind: null, rows: [], sampleRows: 0, emptyValueCount: 0 };
  }
  switch (raw.kind) {
    case OUTPUT_KINDS.SERIES:
      return { ...common, ...projectSeries(raw) };
    case OUTPUT_KINDS.FEATURE_MATRIX:
      return { ...common, ...projectFeatureMatrix(raw) };
    case OUTPUT_KINDS.SCALAR: {
      const value = finiteNumber(raw.value);
      return {
        ...common,
        kind: OUTPUT_KINDS.SCALAR,
        length: 1,
        sampleRows: 1,
        value,
        text: formatPreviewValue(value),
        emptyValueCount: value === null ? 1 : 0,
        rows: [],
      };
    }
    case OUTPUT_KINDS.UNRENDERABLE:
      return {
        ...common,
        kind: OUTPUT_KINDS.UNRENDERABLE,
        // The endpoint's own sentence, verbatim.
        message: nonEmptyString(raw.message),
        pythonType: nonEmptyString(raw.python_type),
        rows: [],
        sampleRows: 0,
        emptyValueCount: 0,
      };
    default:
      throw new PreviewProjectionError(
        `A preview output declares an unknown kind '${raw.kind}'`,
        { port: name, received: raw.kind },
      );
  }
}

/**
 * Project a preview response into the render model, or throw.
 *
 * @param {object} body The endpoint's 200 body.
 * @returns {object} The frozen render model.
 * @throws {PreviewProjectionError} When the body carries no preview.
 */
export function projectPreview(body) {
  if (!isPlainObject(body)) {
    throw new PreviewProjectionError('The preview response is not an object', {
      received: typeof body,
    });
  }
  const nodeId = nonEmptyString(body.node_id);
  if (nodeId === null) {
    throw new PreviewProjectionError("The preview response carries no 'node_id'");
  }
  if (!Array.isArray(body.outputs)) {
    throw new PreviewProjectionError("The preview response carries no 'outputs' list", {
      received: typeof body.outputs,
    });
  }

  const window = isPlainObject(body.window) ? body.window : {};
  const market = isPlainObject(body.market) ? body.market : {};

  return Object.freeze({
    nodeId,
    strategyId: nonEmptyString(body.strategy_id),
    blockId: nonEmptyString(body.block_id),
    category: nonEmptyString(body.category),
    graphSource: nonEmptyString(body.graph_source),
    dagHash: nonEmptyString(body.dag_hash),
    market: Object.freeze({
      symbol: nonEmptyString(market.symbol),
      timeframe: nonEmptyString(market.timeframe),
    }),
    window: Object.freeze({
      bars: finiteNumber(window.bars),
      maxBars: finiteNumber(window.max_bars),
      requestedBars: finiteNumber(window.requested),
      clamped: window.clamped === true,
      availableBars: finiteNumber(window.available_bars),
      warmupBars: finiteNumber(window.warmup_bars),
      planWarmupBars: finiteNumber(window.plan_warmup_bars),
      warmupExceedsWindow: window.warmup_exceeds_window === true,
      firstTimestamp: nonEmptyString(window.first_timestamp),
      lastTimestamp: nonEmptyString(window.last_timestamp),
    }),
    executedNodes: Object.freeze(
      asList(body.executed_nodes).map((id) => nonEmptyString(id)).filter(Boolean),
    ),
    outputs: Object.freeze(body.outputs.map(projectOutput)),
    /** Requirement 20.4: the numeric conditions the run recorded against this node. */
    issues: Object.freeze(asList(body.issues).filter(isPlainObject)),
    computedAt: nonEmptyString(body.computed_at),
  });
}

// ---------------------------------------------------------------------------
// Sentences about the window — the client's own state, so authored here
// ---------------------------------------------------------------------------

/**
 * What the bounded window was, in words.
 *
 * The cap is stated whenever it applied, because a silently reduced window is a preview of
 * something the author did not ask for. `plural` handling is deliberate: "1 bars" reads as a
 * bug in the thing being inspected.
 *
 * @param {object|null} window A projected `window`.
 * @returns {string}
 */
export function describeWindow(window) {
  if (!isPlainObject(window) || window.bars === null) return 'Window unknown.';
  const bars = window.bars;
  const parts = [`Last ${bars} ${bars === 1 ? 'bar' : 'bars'}`];
  if (window.firstTimestamp !== null && window.lastTimestamp !== null) {
    parts.push(`${window.firstTimestamp} to ${window.lastTimestamp}`);
  }
  const sentence = `${parts.join(', ')}.`;

  const notes = [];
  if (window.clamped && window.maxBars !== null) {
    notes.push(
      `A preview reads at most ${window.maxBars} bars, so the requested ` +
        `${window.requestedBars} was reduced.`,
    );
  }
  if (window.warmupBars !== null && window.warmupBars > 0) {
    notes.push(`This block needs ${window.warmupBars} warmup bars before its first value.`);
  }
  if (window.warmupExceedsWindow) {
    notes.push('The warmup covers the whole window, which is why every value is empty.');
  }
  return [sentence, ...notes].join(' ');
}

/**
 * Classify a failed preview request into a code and the text to show.
 *
 * The backend's own `detail.message` and `detail.hint` are used verbatim when present — it
 * knows why it refused, and paraphrasing would give the author a second, vaguer account of a
 * refusal it already explained. Only when there is no such text does this author its own,
 * and then only about the transport.
 *
 * @param {any} error A rejected axios-style error, or a `PreviewProjectionError`.
 * @returns {{code: string, status: number|null, message: string, hint: string|null,
 *   retryable: boolean, authExpired: boolean}}
 */
export function previewError(error) {
  if (error instanceof PreviewProjectionError) {
    return {
      code: error.code,
      status: 200,
      message: error.message,
      hint: null,
      retryable: false,
      authExpired: false,
    };
  }

  const status = error?.status ?? error?.response?.status ?? null;
  const data = error?.data ?? error?.response?.data ?? null;
  const detail = isPlainObject(data) ? (isPlainObject(data.detail) ? data.detail : data) : null;
  const backendCode = detail ? nonEmptyString(detail.error) : null;
  const backendMessage = detail ? nonEmptyString(detail.message) : null;
  const backendHint = detail ? nonEmptyString(detail.hint) : null;

  if (status === 401) {
    return {
      code: 'PREVIEW_UNAUTHENTICATED',
      status,
      message:
        'The session is no longer authenticated. Signing in again is the fix, not retrying.',
      hint: null,
      retryable: false,
      authExpired: true,
    };
  }
  if (status === 429) {
    return {
      code: backendCode || 'PREVIEW_RATE_LIMITED',
      status,
      message: backendMessage || 'Previews are rate limited. Wait a moment and retry.',
      hint: backendHint,
      retryable: true,
      authExpired: false,
    };
  }
  if (backendMessage !== null) {
    return {
      code: backendCode || 'PREVIEW_REFUSED',
      status,
      message: backendMessage,
      hint: backendHint,
      // A refusal about this graph is not fixed by asking again; a 5xx might be.
      retryable: status === null || status >= 500,
      authExpired: false,
    };
  }
  if (status !== null && status >= 500) {
    return {
      code: 'PREVIEW_UNAVAILABLE',
      status,
      message: `The preview service is unavailable (HTTP ${status}).`,
      hint: null,
      retryable: true,
      authExpired: false,
    };
  }
  if (status !== null) {
    return {
      code: 'PREVIEW_REQUEST_FAILED',
      status,
      message: `The preview request failed (HTTP ${status}).`,
      hint: null,
      retryable: true,
      authExpired: false,
    };
  }
  return {
    code: 'PREVIEW_NETWORK_ERROR',
    status: null,
    message: `The preview could not be reached: ${error?.message || 'network error'}`,
    hint: null,
    retryable: true,
    authExpired: false,
  };
}

// ---------------------------------------------------------------------------
// The state machine
// ---------------------------------------------------------------------------

/** @returns {object} The initial state: nothing requested, nothing held. */
export function initialPreviewState() {
  return {
    state: PREVIEW_STATES.IDLE,
    /** The preview identity currently selected, or null when none is possible. */
    key: null,
    /** The identity the held preview was computed for. */
    previewKey: null,
    preview: null,
    error: null,
    requestId: 0,
    /** Answers dropped because they arrived for a selection nobody is looking at. */
    discarded: 0,
    /** True once the author has asked; a preview is not computed unprompted. */
    requested: false,
    fetchedAt: null,
  };
}

/**
 * The reducer.
 *
 * Actions
 *   `{type:'select', key}`                          selection or graph changed
 *   `{type:'request', requestId, key}`              a request was issued
 *   `{type:'result', requestId, key, preview, at}`  a preview came back
 *   `{type:'failed', requestId, key, error}`        the request failed
 *   `{type:'clear'}`                                back to initial
 *
 * A result is accepted only when **both** the request id and the key it was issued for still
 * match. The id catches a superseded request; the key catches what the id cannot — a request
 * issued for node A, then a click on node B before the answer lands. Rendering A's series
 * under B's name would be worse than showing nothing, so it is counted in `discarded`.
 *
 * A held preview survives a `select` only when the key is unchanged. When it changes, the
 * preview is dropped rather than labelled stale: unlike a validation report — where one
 * edit's worth of staleness is still the only verdict the author has — a series of numbers
 * under the wrong block's name has no residual value at all.
 */
export function previewReducer(state, action) {
  switch (action.type) {
    case 'select': {
      const key = action.key ?? null;
      if (key !== null && key === state.key) return state;
      return {
        ...initialPreviewState(),
        key,
        requestId: state.requestId,
        discarded: state.discarded,
      };
    }
    case 'request': {
      if (action.key !== state.key) {
        return { ...state, discarded: state.discarded + 1 };
      }
      return {
        ...state,
        state: PREVIEW_STATES.LOADING,
        requestId: action.requestId,
        requested: true,
        error: null,
      };
    }
    case 'result': {
      if (action.requestId !== state.requestId || action.key !== state.key) {
        return { ...state, discarded: state.discarded + 1 };
      }
      return {
        ...state,
        state: PREVIEW_STATES.READY,
        preview: action.preview,
        previewKey: action.key,
        error: null,
        fetchedAt: action.at ?? null,
      };
    }
    case 'failed': {
      if (action.requestId !== state.requestId || action.key !== state.key) {
        return { ...state, discarded: state.discarded + 1 };
      }
      return {
        ...state,
        state: PREVIEW_STATES.ERROR,
        // Fail closed: no preview survives an error, so a refused request cannot leave last
        // week's numbers on screen beside this week's graph.
        preview: null,
        previewKey: null,
        error: action.error ?? null,
        fetchedAt: Date.now(),
      };
    }
    case 'clear':
      return initialPreviewState();
    default:
      return state;
  }
}

/** True when the held preview describes the selection on screen right now. */
export const previewAppliesToSelection = (state) =>
  state.state === PREVIEW_STATES.READY &&
  state.preview !== null &&
  state.previewKey !== null &&
  state.previewKey === state.key;
