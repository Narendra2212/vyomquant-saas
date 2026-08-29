/**
 * graphValidation.js — the builder's validation state machine, its marker projection and
 * the four status-strip states. Task 3.10, Requirements 8.8, 8.9, 8.10, 8.11.
 *
 * Everything here is pure. The page (`pages/StrategyBuilder.jsx`) owns the timer, the
 * request and the React state; this module owns the rules, so they can be asserted without
 * a DOM and cannot quietly differ between the canvas, the issue list and the status strip.
 *
 * The backend is authoritative
 * ----------------------------
 * `POST /api/strategies/validate` (`backend_app/routers/strategies.py`) always answers
 * **200**, even for an unexecutable graph: an invalid strategy is a valid question with a
 * negative answer. Its body is `ValidationReport.to_dict()`:
 *
 *   { valid, dag_hash, validation_state, errors[], warnings[], summary, stages,
 *     pending_stages, execution_order, execution_levels, registry_version,
 *     validator_version, execution_path, node_types, stats }
 *
 * and every entry of `errors[]` / `warnings[]` is `schema.make_issue`:
 *
 *   { code, severity, node_id, edge_id, field, message, expected, actual, fix_hint }
 *
 * No text in this module paraphrases an issue. `fix_hint` and `message` are rendered
 * verbatim (Requirement 8.9); the only strings authored here describe the *client's* own
 * state — "not validated yet", "validating", "the validation request failed" — which is
 * information the backend cannot supply because it has not been asked yet.
 *
 * The state machine (Requirement 8.10)
 * ------------------------------------
 *   unvalidated ──edit──▶ unvalidated        (a further edit restarts the debounce)
 *   unvalidated ──400 ms─▶ validating
 *   validating  ──report──▶ valid | invalid
 *   validating  ──failure─▶ unavailable      (the last known report is kept, not wiped)
 *   any         ──edit────▶ unvalidated      (immediately, before any request)
 *
 * A response is accepted only when **both** the request id and the graph key it was issued
 * for still match. The request id catches a superseded request; the graph key catches the
 * case the id cannot see — a request issued for graph A, then an edit to B before the
 * debounce re-fires, then A's answer arriving. Landing A's report on B would mark nodes
 * from a graph nobody is looking at. Both checks count the drop in `discarded` rather than
 * failing silently.
 *
 * Why the key excludes `ui`
 * -------------------------
 * `semanticGraphKey` hashes the canonical graph with `ui` removed, matching the backend's
 * `compute_dag_hash` (Property 4: the identity hash is invariant under presentation-only
 * change). Dragging a node is not an edit to the strategy, so it neither marks the graph
 * unvalidated nor spends a request per pixel. It also keeps the marker write-back off the
 * request path: markers live in `node.data.validation`, which is not part of the key, so
 * marking cannot trigger the validation that produced the marker.
 */

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

/** The client-side validation states. `unavailable` is a client state, not a verdict. */
export const VALIDATION_STATES = Object.freeze({
  UNVALIDATED: 'unvalidated',
  VALIDATING: 'validating',
  VALID: 'valid',
  INVALID: 'invalid',
  UNAVAILABLE: 'unavailable',
});

/** Requirement 8.10: validation is requested 400 ms after the author's last edit. */
export const VALIDATION_DEBOUNCE_MS = 400;

export const SEVERITY_ERROR = 'error';
export const SEVERITY_WARNING = 'warning';

/**
 * Warnings that mean the server overrode a value the client supplied (Requirement 6.13).
 * They are surfaced, never hidden: silently accepting a recomputation is how a client and
 * a server come to disagree about what a strategy is.
 */
export const SERVER_OVERRIDE_CODES = Object.freeze([
  'PORT_CONTRACT_RECOMPUTED',
  'EDGE_TYPE_RECOMPUTED',
  'CATEGORY_REMAPPED',
  'VALIDATION_STATE_RECOMPUTED',
]);

/** Feed states, exactly the vocabulary in `design.md § Data preview and honesty`. */
export const FEED_STATES = Object.freeze({
  LIVE: 'LIVE',
  DELAYED: 'DELAYED',
  STALE: 'STALE',
  DISCONNECTED: 'DISCONNECTED',
  INSUFFICIENT_DATA: 'INSUFFICIENT_DATA',
  /** Not one of the five: the honest answer when nothing has been observed at all. */
  UNKNOWN: 'UNKNOWN',
});

/** Training states. `NOT_REQUIRED` is a fact about the graph; `UNKNOWN` is an admission. */
export const TRAINING_STATES = Object.freeze({
  NOT_REQUIRED: 'NOT_REQUIRED',
  UNKNOWN: 'UNKNOWN',
  QUEUED: 'QUEUED',
  RUNNING: 'RUNNING',
  COMPLETED: 'COMPLETED',
  FAILED: 'FAILED',
  CANCELLED: 'CANCELLED',
});

/** Save states. `UNSAVED` means "not saved yet", which is not the same as "saved". */
export const SAVE_STATES = Object.freeze({
  UNSAVED: 'UNSAVED',
  SAVING: 'SAVING',
  SAVED: 'SAVED',
  REFUSED: 'REFUSED',
  FAILED: 'FAILED',
});

const isPlainObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** Fail closed, exactly as the backend validator's severity normalisation does. */
export const normaliseSeverity = (value) => {
  const text = typeof value === 'string' ? value.trim().toLowerCase() : '';
  return text.startsWith('warn') ? SEVERITY_WARNING : SEVERITY_ERROR;
};

// ---------------------------------------------------------------------------
// Graph identity
// ---------------------------------------------------------------------------

/** Deterministic JSON: object keys sorted, so key order cannot fake a change. */
const stableStringify = (value) => {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  if (isPlainObject(value)) {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value === undefined ? null : value);
};

/**
 * A stable key for the *semantics* of a canonical graph.
 *
 * `ui` is excluded (presentation only), and so is the envelope's `name` / `strategy_id`:
 * renaming a strategy does not change what it does, and keying on it would spend a
 * validation request per keystroke in the name field.
 *
 * @param {object|null} graph A canonical graph from `toCanonical`, or null.
 * @returns {string|null} null when there is no graph to key.
 */
export function semanticGraphKey(graph) {
  if (!isPlainObject(graph)) return null;
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  return stableStringify({
    schema_version: graph.schema_version ?? null,
    nodes: nodes.map((node) => ({
      id: node.id,
      block_id: node.block_id,
      category: node.category,
      params: node.params ?? {},
      inputs: node.inputs ?? [],
      outputs: node.outputs ?? [],
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      source_port: edge.source_port,
      target: edge.target,
      target_port: edge.target_port,
      type: edge.type ?? null,
    })),
  });
}

// ---------------------------------------------------------------------------
// Report projection
// ---------------------------------------------------------------------------

/**
 * Every issue in a report, in report order, with the severity taken from the **bucket** it
 * arrived in rather than from its own `severity` string.
 *
 * The bucket is the backend's verdict: `report.valid` is `not errors`, so an entry in
 * `errors[]` is blocking whatever its severity field says. The string is still normalised
 * and kept, so a mismatch is visible rather than resolved behind the author's back.
 */
export function reportIssues(report) {
  if (!isPlainObject(report)) return [];
  const collect = (bucket, severity) =>
    (Array.isArray(report[bucket]) ? report[bucket] : [])
      .filter(isPlainObject)
      .map((issue) => ({
        ...issue,
        severity,
        declared_severity: normaliseSeverity(issue.severity),
        server_override: SERVER_OVERRIDE_CODES.includes(issue.code),
      }));
  return [...collect('errors', SEVERITY_ERROR), ...collect('warnings', SEVERITY_WARNING)];
}

const emptyMarker = (scope, id) => ({
  scope,
  id,
  severity: null,
  errorCount: 0,
  warningCount: 0,
  count: 0,
  overrideCount: 0,
  codes: [],
  issues: [],
  source: 'backend',
});

const addToMarker = (marker, issue) => {
  marker.issues.push(issue);
  marker.count += 1;
  if (issue.severity === SEVERITY_ERROR) marker.errorCount += 1;
  else marker.warningCount += 1;
  if (issue.server_override) marker.overrideCount += 1;
  if (issue.code && !marker.codes.includes(issue.code)) marker.codes.push(issue.code);
  // An error anywhere on the node outranks a warning: the node is blocking.
  marker.severity = marker.errorCount > 0 ? SEVERITY_ERROR : SEVERITY_WARNING;
  return marker;
};

/**
 * Split a report into what the canvas and the issue list each need (Requirement 8.10).
 *
 * * `nodes[node_id]` and `edges[edge_id]` — one marker per named node and per named edge,
 *   carrying its severity, its issue count and its issues. An issue naming **both** a node
 *   and an edge is attributed to both, because both are named.
 * * `graph` — issues naming neither, such as `MISSING_REQUIRED_CATEGORY`. These are the
 *   ones a naive per-node projection drops on the floor; they are kept as their own list so
 *   the UI has to render them somewhere.
 * * `overrides` — the server-recomputation warnings, called out separately.
 *
 * @param {object|null} report A `ValidationReport.to_dict()` body, or null.
 */
export function collectMarkers(report) {
  const issues = reportIssues(report);
  const nodes = {};
  const edges = {};
  const graph = [];
  const overrides = [];

  for (const issue of issues) {
    const nodeId = typeof issue.node_id === 'string' && issue.node_id !== '' ? issue.node_id : null;
    const edgeId = typeof issue.edge_id === 'string' && issue.edge_id !== '' ? issue.edge_id : null;

    if (issue.server_override) overrides.push(issue);

    if (nodeId !== null) {
      if (!nodes[nodeId]) nodes[nodeId] = emptyMarker('node', nodeId);
      addToMarker(nodes[nodeId], issue);
    }
    if (edgeId !== null) {
      if (!edges[edgeId]) edges[edgeId] = emptyMarker('edge', edgeId);
      addToMarker(edges[edgeId], issue);
    }
    if (nodeId === null && edgeId === null) graph.push(issue);
  }

  return {
    nodes,
    edges,
    graph,
    overrides,
    issues,
    errorCount: issues.filter((issue) => issue.severity === SEVERITY_ERROR).length,
    warningCount: issues.filter((issue) => issue.severity === SEVERITY_WARNING).length,
  };
}

/**
 * A comparable fingerprint of one marker.
 *
 * The render-loop guard: the canvas is only handed a new node or edge object when this
 * string changes. Returning a fresh array unconditionally from a marker effect is what
 * produced the infinite loop the previous task had to fix.
 */
export function markerSignature(marker) {
  if (!marker) return '';
  return [marker.source, marker.severity, marker.count, marker.codes.join('+')].join('|');
}

/** Marker markup text: severity and count in words, never colour alone. */
export function markerLabel(marker) {
  if (!marker) return '';
  const parts = [];
  if (marker.errorCount > 0) {
    parts.push(`${marker.errorCount} error${marker.errorCount === 1 ? '' : 's'}`);
  }
  if (marker.warningCount > 0) {
    parts.push(`${marker.warningCount} warning${marker.warningCount === 1 ? '' : 's'}`);
  }
  return parts.join(', ');
}

// ---------------------------------------------------------------------------
// The state machine
// ---------------------------------------------------------------------------

/** @returns {object} The initial machine state. */
export function initialValidationState() {
  return {
    state: VALIDATION_STATES.UNVALIDATED,
    /** The key of the graph currently on the canvas. */
    graphKey: null,
    /** The last request id issued. Monotonic; 0 means none. */
    requestId: 0,
    /** The key the in-flight request was issued for, or null. */
    pendingKey: null,
    /** The last report that landed, even when the graph has since changed. */
    report: null,
    /** The key `report` describes. Compare with `graphKey` before trusting it. */
    reportKey: null,
    reportAt: null,
    /** A failed request, kept alongside `report` rather than replacing it. */
    error: null,
    /** Responses dropped as superseded. Diagnostic, and asserted by the tests. */
    discarded: 0,
    /** Requests actually issued. The debounce assertion counts this. */
    requests: 0,
    /** Why no request can be made, when that is the case. */
    unserializable: null,
  };
}

/**
 * The transition function. Total: an action it does not recognise leaves the state alone.
 *
 * @param {object} state
 * @param {object} action One of:
 *   `{type:'edit', graphKey}` — the canvas changed semantically.
 *   `{type:'unserializable', issue}` — the canvas cannot be expressed canonically.
 *   `{type:'request', requestId, graphKey}` — the debounce fired.
 *   `{type:'report', requestId, graphKey, report, at}` — a response arrived.
 *   `{type:'failed', requestId, error}` — the request failed.
 */
export function validationReducer(state, action) {
  switch (action.type) {
    case 'edit': {
      const graphKey = action.graphKey ?? null;
      if (graphKey === state.graphKey && state.unserializable === null) return state;
      return {
        ...state,
        // Requirement 8.10: unvalidated the moment the graph changes, before any request.
        state: VALIDATION_STATES.UNVALIDATED,
        graphKey,
        pendingKey: null,
        unserializable: null,
        // `report` and `reportKey` survive on purpose. `reportKey !== graphKey` is how the
        // UI knows the report it holds describes an earlier version of this graph.
      };
    }

    case 'unserializable':
      return {
        ...state,
        state: VALIDATION_STATES.UNVALIDATED,
        graphKey: null,
        pendingKey: null,
        unserializable: action.issue ?? null,
      };

    case 'request':
      return {
        ...state,
        state: VALIDATION_STATES.VALIDATING,
        requestId: action.requestId,
        pendingKey: action.graphKey ?? null,
        requests: state.requests + 1,
        error: null,
      };

    case 'report': {
      // Superseded by a newer request, or answered for a graph that has since changed.
      if (action.requestId !== state.requestId || action.graphKey !== state.graphKey) {
        return { ...state, discarded: state.discarded + 1 };
      }
      const report = action.report;
      return {
        ...state,
        state: report && report.valid ? VALIDATION_STATES.VALID : VALIDATION_STATES.INVALID,
        report,
        reportKey: action.graphKey,
        reportAt: action.at ?? null,
        pendingKey: null,
        error: null,
      };
    }

    case 'failed': {
      if (action.requestId !== state.requestId) {
        return { ...state, discarded: state.discarded + 1 };
      }
      return {
        ...state,
        state: VALIDATION_STATES.UNAVAILABLE,
        pendingKey: null,
        // `report` is deliberately untouched: a transport failure is not a verdict, and
        // wiping the last known report would lose information the author still needs.
        error: action.error ?? null,
      };
    }

    default:
      return state;
  }
}

/** True when the held report describes the graph on the canvas right now. */
export const reportAppliesToCanvas = (state) =>
  state.report !== null && state.reportKey !== null && state.reportKey === state.graphKey;

/**
 * The validation summary line (Requirement 8.11).
 *
 * Counts come from the report; the shape of the sentence states which of the five machine
 * states produced it, so "not checked yet" can never read as "checked and fine".
 */
export function validationSummary(state, markers) {
  const summary = isPlainObject(state.report) && isPlainObject(state.report.summary)
    ? state.report.summary
    : {};
  const counts = [];
  if (markers && markers.errorCount > 0) {
    counts.push(`${markers.errorCount} error${markers.errorCount === 1 ? '' : 's'}`);
  }
  if (markers && markers.warningCount > 0) {
    counts.push(`${markers.warningCount} warning${markers.warningCount === 1 ? '' : 's'}`);
  }
  const shape = [
    Number.isFinite(summary.node_count) ? `${summary.node_count} nodes` : null,
    Number.isFinite(summary.edge_count) ? `${summary.edge_count} connections` : null,
    Number.isFinite(summary.warmup_bars) ? `${summary.warmup_bars} warmup bars` : null,
  ]
    .filter(Boolean)
    .join(', ');

  if (state.unserializable !== null) {
    return {
      state: state.state,
      headline: 'Not validated — this canvas cannot be sent for validation',
      detail: state.unserializable.message || '',
    };
  }

  switch (state.state) {
    case VALIDATION_STATES.VALIDATING:
      return { state: state.state, headline: 'Validating…', detail: '' };

    case VALIDATION_STATES.VALID:
      return {
        state: state.state,
        headline: counts.length ? `Valid · ${counts.join(', ')}` : 'Valid',
        detail: shape,
      };

    case VALIDATION_STATES.INVALID:
      return {
        state: state.state,
        headline: `Invalid · ${counts.length ? counts.join(', ') : 'see the issues below'}`,
        detail: shape,
      };

    case VALIDATION_STATES.UNAVAILABLE:
      return {
        state: state.state,
        headline: 'Validation unavailable',
        detail:
          (state.error && state.error.message ? state.error.message : 'The validation request failed.') +
          (state.report !== null
            ? ' The report below is the last one received and may not describe the current graph.'
            : ' No report has been received for this graph.'),
      };

    default:
      return {
        state: VALIDATION_STATES.UNVALIDATED,
        headline:
          state.report === null
            ? 'Not validated yet'
            : 'Not validated — edited since the last check',
        detail: state.report === null ? '' : shape,
      };
  }
}

// ---------------------------------------------------------------------------
// Feed state (Requirement 8.11, and the honesty table in design.md)
// ---------------------------------------------------------------------------

const INTERVAL_UNITS = { s: 1000, m: 60000, h: 3600000, d: 86400000, w: 604800000 };

/**
 * The expected bar interval of a timeframe, in milliseconds, or null when the string cannot
 * be parsed. Null propagates to `UNKNOWN`: an unparseable timeframe means the age of the
 * last candle cannot be judged, and a feed whose age cannot be judged is never `LIVE`.
 */
export function expectedIntervalMs(timeframe) {
  if (typeof timeframe !== 'string') return null;
  const match = /^(\d+)\s*([smhdw])$/i.exec(timeframe.trim());
  if (match === null) return null;
  const amount = Number.parseInt(match[1], 10);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  return amount * INTERVAL_UNITS[match[2].toLowerCase()];
}

/** "4m 12s" — the concrete age the panel shows instead of a colour. */
export function formatAge(ms) {
  if (!Number.isFinite(ms) || ms < 0) return 'unknown';
  const total = Math.floor(ms / 1000);
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}m`);
  if (!days && !hours) parts.push(`${seconds}s`);
  return parts.join(' ');
}

/**
 * The feed state, reported literally.
 *
 * @param {object|null} observation What is actually known about the feed:
 *   `{ connected, lastEventAt, timeframe, expectedIntervalMs, availableBars, warmupBars }`.
 *   **null means nothing has been observed**, which is reported as `UNKNOWN` with
 *   `known: false` — never as a passing state. This is the local derivation, kept for a
 *   caller that holds a direct observation of a feed. The builder's own strip does not use
 *   it for its reading: task 7.11 wires `GET /strategy-operations/strategies/{id}/data-quality`
 *   and projects that response through `feedStateFromReport` below, so the state, the
 *   thresholds and the sentence on screen are the server's. What `deriveFeedState(null, …)`
 *   still supplies is the wording of the "nothing has been read" branch, which
 *   `feedStateFromReport` reuses rather than rewording.
 * @param {{now?: number, market?: {symbol?: string, timeframe?: string}}} [context]
 */
export function deriveFeedState(observation, { now = Date.now(), market = null } = {}) {
  const symbol = market && typeof market.symbol === 'string' && market.symbol.trim() !== ''
    ? market.symbol.trim()
    : null;
  const timeframe =
    (observation && typeof observation.timeframe === 'string' ? observation.timeframe : null) ||
    (market && typeof market.timeframe === 'string' ? market.timeframe : null);

  const unknown = (detail) => ({
    state: FEED_STATES.UNKNOWN,
    known: false,
    label: 'No feed',
    detail,
    ageMs: null,
    expectedIntervalMs: null,
  });

  if (symbol === null || timeframe === null || timeframe.trim() === '') {
    return unknown(
      'No market is fully named on a DATA block yet, so there is no feed to report on.',
    );
  }

  const marketLabel = `${symbol} ${timeframe}`;

  if (!isPlainObject(observation)) {
    return unknown(
      `Nothing has been observed for ${marketLabel} in the builder. Feed health is reported ` +
        'once a deployment or a data-quality reading exists; until then this is unknown, not healthy.',
    );
  }

  const interval = Number.isFinite(observation.expectedIntervalMs)
    ? observation.expectedIntervalMs
    : expectedIntervalMs(timeframe);

  if (observation.connected === false) {
    return {
      state: FEED_STATES.DISCONNECTED,
      known: true,
      label: 'Disconnected',
      detail: `The ${marketLabel} feed is not connected.`,
      ageMs: null,
      expectedIntervalMs: interval,
    };
  }

  if (
    Number.isFinite(observation.availableBars) &&
    Number.isFinite(observation.warmupBars) &&
    observation.availableBars < observation.warmupBars
  ) {
    return {
      state: FEED_STATES.INSUFFICIENT_DATA,
      known: true,
      label: 'Insufficient data',
      detail:
        `${marketLabel} has ${observation.availableBars} bars; this strategy needs ` +
        `${observation.warmupBars} warmup bars.`,
      ageMs: null,
      expectedIntervalMs: interval,
    };
  }

  if (!Number.isFinite(observation.lastEventAt) || !Number.isFinite(interval) || interval <= 0) {
    return unknown(
      `The age of the last ${marketLabel} candle cannot be established, so the feed is not ` +
        'reported as live.',
    );
  }

  const ageMs = now - observation.lastEventAt;
  const detail = `last candle ${formatAge(ageMs)} ago, expected every ${formatAge(interval)}`;

  if (ageMs > interval * 3) {
    return { state: FEED_STATES.STALE, known: true, label: 'Stale', detail, ageMs, expectedIntervalMs: interval };
  }
  if (ageMs > interval * 1.5) {
    return { state: FEED_STATES.DELAYED, known: true, label: 'Delayed', detail, ageMs, expectedIntervalMs: interval };
  }
  return { state: FEED_STATES.LIVE, known: true, label: 'Live', detail, ageMs, expectedIntervalMs: interval };
}

// ---------------------------------------------------------------------------
// Feed state as the backend reports it (task 7.11, Requirements 19.7, 19.8, 19.9, 19.10)
// ---------------------------------------------------------------------------

/**
 * The outcome of one read of `GET /api/strategy-operations/strategies/{id}/data-quality`.
 *
 * `NOT_APPLICABLE` is the unsaved canvas: there is no version, so there is no configured data
 * source to report on. It is deliberately **not** an error — nothing failed — and equally not a
 * pass, because no feed was measured.
 */
export const FEED_READ_STATES = Object.freeze({
  NOT_APPLICABLE: 'NOT_APPLICABLE',
  LOADING: 'LOADING',
  REPORTED: 'REPORTED',
  FAILED: 'FAILED',
});

/**
 * The five server states, spelled for a human. A label is a translation of the server's word,
 * never a re-derivation of it: anything outside this table is `UNKNOWN`, so a vocabulary the
 * client does not recognise cannot be rendered as a state — least of all as a passing one.
 */
const FEED_STATE_LABELS = Object.freeze({
  [FEED_STATES.LIVE]: 'Live',
  [FEED_STATES.DELAYED]: 'Delayed',
  [FEED_STATES.STALE]: 'Stale',
  [FEED_STATES.DISCONNECTED]: 'Disconnected',
  [FEED_STATES.INSUFFICIENT_DATA]: 'Insufficient data',
});

const marketLabelOf = (market) => {
  if (!isPlainObject(market)) return null;
  const symbol = typeof market.symbol === 'string' ? market.symbol.trim() : '';
  const timeframe = typeof market.timeframe === 'string' ? market.timeframe.trim() : '';
  if (symbol === '' || timeframe === '') return null;
  return `${symbol} ${timeframe}`;
};

/**
 * Project one data-quality read onto the status strip.
 *
 * **Nothing here classifies a feed.** The state, the reason, both thresholds, the age and the
 * sentence on screen are read off the response; `feed_state.py` owns the 1.5x / 3x boundaries,
 * the precedence between a stopped feed and an unfilled warmup, and the wording. A second copy
 * of that arithmetic in the client would eventually disagree with the classifier that actually
 * decides, and the disagreement would be invisible.
 *
 * `detail` is the server's `display` sentence **verbatim** — "Last candle 4m 12s ago, expected
 * every 5m" — which is the whole of Requirement 19.8's display clause: the age of the last
 * event together with the expected interval, as figures rather than a colour. The same
 * convention as `ParameterForm`'s `fix_hint` and `NodePreview`'s `detail.message`.
 *
 * Fail-closed, in every branch that is not a report: no reading, a read in flight, a failed
 * read, an unparseable body, a state word this client does not know, or a report about a
 * different market all produce `UNKNOWN` with `known: false`. `UNKNOWN` is not one of the five
 * and is never `LIVE`.
 *
 * @param {{status?: string, body?: object|null, error?: object|null}|null} read
 * @param {{market?: {symbol: string|null, timeframe: string|null}|null}} [context] The market
 *   the *canvas* names. A reading describes the **saved** version's data source, so when the
 *   two disagree the reading is reported as belonging to the version, not to this graph.
 */
export function feedStateFromReport(read, { market = null } = {}) {
  const status = isPlainObject(read) && typeof read.status === 'string' ? read.status : null;
  const canvasMarket = marketLabelOf(market);

  const unknown = (detail, extra = {}) => ({
    state: FEED_STATES.UNKNOWN,
    known: false,
    label: 'No feed',
    detail,
    display: null,
    reason: null,
    ageSeconds: null,
    ageText: null,
    expectedIntervalSeconds: null,
    barsMissing: null,
    reportedMarket: null,
    ...extra,
  });

  // Nothing has been read at all, and the unsaved canvas. Both are "not applicable, and
  // therefore unknown": the existing local derivation already says exactly that, including for
  // a graph that names no market yet, so it is reused rather than reworded.
  if (status === null || status === FEED_READ_STATES.NOT_APPLICABLE) {
    const base = deriveFeedState(null, { market });
    const because =
      status === FEED_READ_STATES.NOT_APPLICABLE
        ? ' This strategy has not been saved, so it has no version whose configured data source' +
          ' could be read — not applicable, which is not the same as healthy.'
        : '';
    return unknown(`${base.detail}${because}`);
  }

  if (status === FEED_READ_STATES.LOADING) {
    return unknown(
      `Reading the feed state${canvasMarket === null ? '' : ` for ${canvasMarket}`}. Until the ` +
        'reading lands this is unknown, not healthy.',
      { label: 'Checking…' },
    );
  }

  if (status === FEED_READ_STATES.FAILED) {
    const error = isPlainObject(read.error) ? read.error : {};
    const httpStatus = Number.isFinite(error.status) ? error.status : null;
    const message =
      typeof error.message === 'string' && error.message.trim() !== ''
        ? error.message.trim()
        : 'The feed-state request failed.';
    return unknown(
      `${message}${httpStatus === null ? '' : ` (HTTP ${httpStatus})`} No feed state has been ` +
        'reported, so this is unknown, not healthy.',
      { label: 'Unavailable' },
    );
  }

  const body = isPlainObject(read.body) ? read.body : null;
  const report = body !== null && isPlainObject(body.feed) ? body.feed : null;
  if (report === null) {
    return unknown(
      'The feed-state response carried no feed report, so nothing about the feed is known. ' +
        'This is unknown, not healthy.',
    );
  }

  const reportedMarket = marketLabelOf(body.market);
  const numeric = (value) => (Number.isFinite(value) ? value : null);
  const figures = {
    reason: typeof report.reason === 'string' ? report.reason : null,
    ageSeconds: numeric(report.age_seconds),
    ageText: typeof report.age_text === 'string' ? report.age_text : null,
    expectedIntervalSeconds: numeric(report.expected_interval_seconds),
    barsMissing: numeric(report.bars_missing),
    reportedMarket,
  };
  const display =
    typeof report.display === 'string' && report.display.trim() !== ''
      ? report.display.trim()
      : null;

  const label = FEED_STATE_LABELS[report.state];
  if (label === undefined) {
    // A word outside the closed vocabulary. Reported as unknown and quoted, rather than shown
    // as a sixth state — and never guessed at from the figures beside it.
    return unknown(
      `The feed state was reported as "${String(report.state)}", which is not one of the five ` +
        'states this builder knows, so it is not interpreted. This is unknown, not healthy.',
      figures,
    );
  }

  // The reading is about the saved version's market. If the canvas now names another one, the
  // figures are true but they are not this graph's feed, and attributing them to it would be
  // the same class of error as labelling a stale feed `LIVE`.
  if (canvasMarket !== null && reportedMarket !== null && canvasMarket !== reportedMarket) {
    return unknown(
      `The reading describes the saved version's market (${reportedMarket}), not the market now ` +
        `on the canvas (${canvasMarket}), so it is not reported as this graph's feed state: ` +
        `unknown, not healthy.${display === null ? '' : ` The reading itself: "${display}"`}`,
      figures,
    );
  }

  return {
    state: report.state,
    known: true,
    label,
    // Requirement 19.8's display clause, in the server's own words.
    detail: display === null ? 'The feed state was reported without a description.' : display,
    display,
    ...figures,
  };
}

// ---------------------------------------------------------------------------
// Training state (Requirement 8.11)
// ---------------------------------------------------------------------------

const TRAINING_LABELS = {
  QUEUED: 'Queued',
  RUNNING: 'Running',
  COMPLETED: 'Completed',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
};

/**
 * The training state.
 *
 * `mlNodeCount === 0` is a genuine, knowable answer — "not required", the same verdict the
 * save path reaches when a graph declares no ML node. A graph that *does* declare one, with
 * no job observed, is `UNKNOWN`: the training endpoints arrive in Phase 6, and reporting
 * "ready" for a model that was never trained would be the fake green this requirement
 * exists to prevent.
 *
 * @param {{mlNodeCount?: number, job?: object|null}} input
 */
export function deriveTrainingState({ mlNodeCount = 0, job = null } = {}) {
  if (!Number.isFinite(mlNodeCount) || mlNodeCount <= 0) {
    return {
      state: TRAINING_STATES.NOT_REQUIRED,
      known: true,
      label: 'Not required',
      detail: 'This strategy declares no ML or DL block, so nothing needs training.',
    };
  }

  if (!isPlainObject(job)) {
    return {
      state: TRAINING_STATES.UNKNOWN,
      known: false,
      label: 'Unknown',
      detail:
        `${mlNodeCount} ML/DL block${mlNodeCount === 1 ? '' : 's'} require training. No training ` +
        'job has been observed for this graph, so its training state is unknown.',
    };
  }

  const status = typeof job.status === 'string' ? job.status.trim().toUpperCase() : '';
  const label = TRAINING_LABELS[status];
  if (label === undefined) {
    return {
      state: TRAINING_STATES.UNKNOWN,
      known: false,
      label: 'Unknown',
      detail: `The training job reports status ${JSON.stringify(job.status)}, which this build does not recognise.`,
    };
  }

  // `epoch_current` is the column name `GET /training/jobs/{job_id}` reports (task 6.6, and
  // 004d's own column); `current_epoch` is what this function accepted before that endpoint
  // existed. Both are read, so a client written against either shape keeps working.
  const currentEpoch = Number.isFinite(job.epoch_current) ? job.epoch_current : job.current_epoch;
  const epochs =
    Number.isFinite(currentEpoch) && Number.isFinite(job.epochs_total)
      ? ` epoch ${currentEpoch}/${job.epochs_total}`
      : '';
  const reason = status === 'FAILED' && job.failure_reason ? ` — ${job.failure_reason}` : '';
  // Requirements 15.5 and 15.6: the backend reports `eta_seconds` as null until at least
  // three epochs have completed and their durations are stable. The builder shows
  // "estimating…" for that absence rather than a number, and never computes its own
  // estimate from elapsed time — that is precisely the fabricated progress the requirement
  // forbids, and the client has no epoch durations to compute one from anyway.
  const eta =
    status === 'RUNNING'
      ? Number.isFinite(job.eta_seconds)
        ? ` · ${formatAge(job.eta_seconds * 1000)} remaining`
        : ' · ETA estimating…'
      : '';

  return {
    state: TRAINING_STATES[status],
    known: true,
    label,
    detail: `Training job ${job.id || job.job_id || ''}${epochs}${eta}${reason}`.trim(),
  };
}

// ---------------------------------------------------------------------------
// Blocking training messages (Requirement 14.9)
// ---------------------------------------------------------------------------

/**
 * The block reasons the training admission path returns. `strategy_service`'s own
 * `REASON_*` constants, so the builder renders one vocabulary rather than a second that
 * drifts from the server's.
 */
export const TRAINING_BLOCK_REASONS = Object.freeze({
  DATA_SOURCE_UNRESOLVED: 'DATA_SOURCE_UNRESOLVED',
  DATA_UNAVAILABLE: 'DATA_UNAVAILABLE',
  DATA_QUALITY: 'DATA_QUALITY',
  FEATURES: 'FEATURES',
  DATASET: 'DATASET',
  ML_REQUIREMENTS: 'ML_REQUIREMENTS',
  MODEL_UNPUBLISHED: 'MODEL_UNPUBLISHED',
  CAP_EXCEEDED: 'CAP_EXCEEDED',
});

/** Thousands separators, matching `ml_training_policy.format_thousands`. */
const formatQuantity = (value) => {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value.toLocaleString('en-US') : null;
  }
  if (typeof value === 'boolean') return String(value);
  const text = String(value).trim();
  return text === '' ? null : text;
};

/**
 * One `required` / `available` pair, ready to render.
 *
 * Both sides come from the backend payload. Nothing here recomputes a threshold: the
 * minimum feature-column count, the minimum row count, the reserved fractions and the
 * embargo are `ml_training_policy`'s arithmetic, the server has already done it, and a
 * client that did it again would eventually disagree with the gate that actually decides.
 */
const quantityPair = (label, required, available) => {
  const requiredText = formatQuantity(required);
  const availableText = formatQuantity(available);
  // BOTH sides, or nothing. A pair with one side missing would either need a fabricated
  // number on the other or the word "unknown" where the backend simply does not express a
  // numeric requirement — and Requirement 14.9 asks for the required quantity *and* the
  // available quantity. A reason that carries neither is rendered as the server's own
  // sentence instead, which already states what went wrong.
  if (requiredText === null || availableText === null) return null;
  return {
    label,
    required: requiredText,
    available: availableText,
    // One string, so the two quantities cannot be rendered apart.
    text: `${label} — required ${requiredText}, available ${availableText}`,
  };
};

/**
 * The blocking training messages in a save or job-creation response, each with its
 * required quantity and its available quantity. Requirement 14.9.
 *
 * Accepts either shape the backend produces, because they are the same payload reached two
 * ways:
 *
 * * the save response's `training` field when `state === 'BLOCKED'`
 *   (`POST /strategy-operations/strategies/{id}/versions`, task 6.3); and
 * * the `detail` of the **422** from `POST /strategy-operations/training/jobs`, which is
 *   `{ error: 'TRAINING_BLOCKED', reason, message, detail, job_created: false }`.
 *
 * Both carry `{ reason, message, detail }`, and this reads the quantities out of `detail`
 * per reason:
 *
 * | reason | required quantity | available quantity |
 * |---|---|---|
 * | `ML_REQUIREMENTS` | `detail.required.columns` / `.rows` / `.train_rows` / `.sequence_length` | the same keys of `detail.available` |
 * | `CAP_EXCEEDED` | `detail.requested` — what the author asked for | `detail.allowed` — what the plan permits |
 * | `DATASET` | `detail.window.target_bars` | `detail.window.max_bars` |
 *
 * `FEATURES`, `DATA_QUALITY`, `MODEL_UNPUBLISHED`, `DATA_SOURCE_UNRESOLVED` and
 * `DATA_UNAVAILABLE` express no numeric requirement, so they are rendered as the server's
 * own message plus `issues`. A block with no quantities is still returned rather than
 * dropped — hiding a block is worse than rendering it without figures, and inventing
 * figures for it is worse than both.
 *
 * @param {object|null} payload A `training` field, or a 422 `detail`, or null.
 * @returns {Array<{reason: string, message: string, nodeId: string|null, blockId: string|null,
 *   quantities: Array<{label: string, required: string, available: string, text: string}>,
 *   fixHint: string|null, jobCreated: boolean,
 *   issues: Array<{code: string|null, message: string, fixHint: string|null, key: string,
 *     quantity: {label: string, required: string, available: string, text: string}|null}>}>}
 */
export function deriveTrainingBlocks(payload) {
  if (!isPlainObject(payload)) return [];
  // `state === 'BLOCKED'` (save response) or `blocked === true` (`TrainingBlocked.to_dict`)
  // or `error === 'TRAINING_BLOCKED'` (the 422 detail). Anything else is not a block, and a
  // queued or not-required training half must not render as one.
  const isBlock =
    payload.state === 'BLOCKED' || payload.blocked === true || payload.error === 'TRAINING_BLOCKED';
  if (!isBlock) return [];

  const reason = typeof payload.reason === 'string' ? payload.reason : '';
  const message = typeof payload.message === 'string' ? payload.message : '';
  const detail = isPlainObject(payload.detail) ? payload.detail : {};
  const required = isPlainObject(detail.required) ? detail.required : {};
  const available = isPlainObject(detail.available) ? detail.available : {};
  const window = isPlainObject(detail.window) ? detail.window : {};

  const quantities = [];
  const push = (pair) => {
    if (pair) quantities.push(pair);
  };

  if (reason === TRAINING_BLOCK_REASONS.ML_REQUIREMENTS) {
    // Requirements 14.3 and 14.4: both dimensions, always, even when only one fell short.
    // The gate returns both for exactly this reason, so the author is not told about one
    // half and left to discover the other on the next attempt.
    push(quantityPair('feature columns', required.columns, available.columns));
    push(quantityPair('usable rows', required.rows, available.rows));
    // Requirement 14.6: a sequence model's training split. Present only when the gate
    // measured it, and read from the payload rather than derived from a sequence length
    // this client would have to look up.
    push(quantityPair('training rows', required.train_rows, available.train_rows));
    push(quantityPair('sequence length', required.sequence_length, available.sequence_length));
  } else if (reason === TRAINING_BLOCK_REASONS.CAP_EXCEEDED) {
    // Requirement 16.3: the requested value together with the permitted value, for the one
    // cap that was exceeded. "Available" is the permitted value here — that is what the
    // author may have.
    const unit = typeof detail.unit === 'string' && detail.unit ? ` (${detail.unit})` : '';
    push(quantityPair(`${detail.cap || 'cap'}${unit}`, detail.requested, detail.allowed));
  } else if (reason === TRAINING_BLOCK_REASONS.DATASET) {
    // `training_bar_budget`'s own figures: the window this graph needs before its models
    // have enough usable rows, against the ceiling on a single training fetch.
    push(quantityPair('bars in the window', window.target_bars, window.max_bars));
  }
  // `FEATURES`, `DATA_QUALITY`, `MODEL_UNPUBLISHED`, `DATA_SOURCE_UNRESOLVED` and
  // `DATA_UNAVAILABLE` express no numeric requirement — a feature schema failure, a POOR
  // feed and an unpublished model block are not shortfalls against a threshold. They are
  // rendered as the server's message plus `issues`, each of which carries its own
  // `expected` / `actual` pair when the backend measured one.

  // Each gate issue is `schema.make_issue`: `{ code, severity, node_id, field, message,
  // expected, actual, fix_hint }`. `expected` is the required quantity and `actual` is the
  // available one, so an issue is rendered with both — which is Requirement 14.9 applied per
  // message rather than only to the block as a whole. The message and the fix hint are
  // verbatim; no text here paraphrases an issue (Requirement 8.9's rule, kept).
  const issues = Array.isArray(detail.issues)
    ? detail.issues
        .map((issue, index) => {
          if (typeof issue === 'string') {
            return { code: null, message: issue, quantity: null, fixHint: null, key: `issue-${index}` };
          }
          if (!isPlainObject(issue)) return null;
          const field = typeof issue.field === 'string' && issue.field ? issue.field : 'quantity';
          const quantity = quantityPair(field, issue.expected, issue.actual);
          const message = issue.message || issue.code || '';
          if (!message && !quantity) return null;
          return {
            code: typeof issue.code === 'string' ? issue.code : null,
            message: String(message),
            quantity,
            fixHint: typeof issue.fix_hint === 'string' && issue.fix_hint ? issue.fix_hint : null,
            key: `${issue.code || 'issue'}-${issue.node_id || ''}-${field}-${index}`,
          };
        })
        .filter((issue) => issue !== null)
    : [];

  return [
    {
      reason: reason || 'TRAINING_BLOCKED',
      // The server's own sentence, verbatim. `GateVerdict.message()` already reads
      // "Training cannot start. Required: 5 feature columns and 5,000 usable rows.
      // Available: 3 feature columns and 1,240 rows." — paraphrasing it here would be a
      // second wording of one rule.
      message: message || detail.message || 'Training was blocked.',
      nodeId: typeof detail.node_id === 'string' ? detail.node_id : null,
      blockId: typeof detail.block_id === 'string' ? detail.block_id : null,
      quantities,
      fixHint: typeof detail.fix_hint === 'string' && detail.fix_hint ? detail.fix_hint : null,
      issues,
      // No job row exists on any blocked path (Requirements 14.3, 14.4, 14.7, 14.8), and
      // the response says so explicitly. Surfaced so the builder can state it rather than
      // leaving an author wondering whether something is queued.
      jobCreated: payload.job_created === true || Boolean(payload.job_id),
    },
  ];
}
