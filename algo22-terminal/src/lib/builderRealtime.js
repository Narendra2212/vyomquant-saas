/**
 * ═══════════════════════════════════════════════════════════════════════════
 * BUILDER REALTIME — the projection of pushed frames onto what the canvas draws
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * strategy-builder task 8.5. Requirements 20.12, 21.6, 23.1-23.6.
 *
 * WHAT THIS FILE IS FOR
 * ---------------------
 * The backend decides four things this module renders and re-derives none of them:
 *
 * * a node's runtime state — `NOT_READY`, `AWAITING_MODEL`, `WARMING`, `READY` — and
 *   its bar counts, from task 8.4's `PlanRuntimeState.to_dict()`
 *   (`backend_app/backend/dag_engine.py`);
 * * whether a version's canvas is locked, from task 8.3's `canvas_state`
 *   (`backend_app/backend/strategy_lifecycle.py`);
 * * how far a training job has got, from the `training.progress` frame the worker
 *   publishes;
 * * whether a subscription was refused, and why (Requirement 21.6).
 *
 * So there is no threshold, no state machine and no lock rule in this file. It is a
 * reducer over frames and a set of translation tables from the backend's words to
 * display words. `RUNTIME_STATE_LABELS` is keyed by the backend's label precisely so a
 * vocabulary this build does not know renders as unknown rather than as a fifth state —
 * and never as `READY`.
 *
 * WHY THE REDUCER LIVES HERE AND NOT IN THE HOOK
 * ----------------------------------------------
 * Everything below is pure. A frame arriving is a function from (state, frame) to
 * state, which means the interesting behaviour — a warming node with a bar count, a
 * refusal reaching the view that asked for the channel, an unknown label refusing to
 * read as ready — is assertable without a socket, a timer or a rendered tree. The hook
 * (`hooks/useBuilderRealtime.js`) owns only the connection lifecycle.
 */

import {
  OWNED_CHANNELS,
  OWNED_CHANNEL_EVENTS,
  SUBSCRIPTION_REFUSED,
  ownedChannel,
  parseOwnedChannel,
} from '../constants/wsChannels';

/**
 * The realtime connection as the builder reports it.
 *
 * `DISCONNECTED` is Requirement 23.4's word, kept literal. `UNAVAILABLE` is the
 * separate, honest state for "this session has no realtime connection to lose" — no
 * authenticated token, so nothing was ever opened. Collapsing the two would have the
 * strip report a dropped connection where there was never one, and would hide the
 * actual reason (sign in) behind a reconnect message that will never succeed.
 */
export const REALTIME_STATES = Object.freeze({
  CONNECTED: 'CONNECTED',
  CONNECTING: 'CONNECTING',
  DISCONNECTED: 'DISCONNECTED',
  UNAVAILABLE: 'UNAVAILABLE',
});

/** Display words for the connection state. Text, so colour is never the only signal. */
export const REALTIME_LABELS = Object.freeze({
  [REALTIME_STATES.CONNECTED]: 'Live',
  [REALTIME_STATES.CONNECTING]: 'Connecting',
  [REALTIME_STATES.DISCONNECTED]: 'Disconnected',
  [REALTIME_STATES.UNAVAILABLE]: 'Not connected',
});

/**
 * `websocketClient`'s status vocabulary mapped onto the four above.
 *
 * `error` and `failed` both mean the socket is not carrying frames, so both read
 * `DISCONNECTED` — which is what Requirement 23.4 asks the builder to report, and is
 * true in a way "error" is not: an error the client recovered from silently would
 * otherwise leave the strip claiming a fault that no longer exists.
 */
const SOCKET_STATUS_TO_REALTIME = Object.freeze({
  connected: REALTIME_STATES.CONNECTED,
  connecting: REALTIME_STATES.CONNECTING,
  disconnected: REALTIME_STATES.DISCONNECTED,
  error: REALTIME_STATES.DISCONNECTED,
  failed: REALTIME_STATES.DISCONNECTED,
});

/**
 * Project one of `websocketClient`'s statuses onto a reported realtime state.
 * @param {string} status
 * @returns {string}
 */
export function realtimeStateFromSocket(status) {
  return SOCKET_STATUS_TO_REALTIME[status] || REALTIME_STATES.DISCONNECTED;
}

/**
 * The four runtime labels, translated for display. Requirement 20.12 names exactly
 * these four facts: "warming with a bar count, ready, awaiting a model and training".
 *
 * The keys are `dag_engine.RUNTIME_STATES`. Nothing here decides which one is true;
 * a label absent from this table is rendered as unknown (see `nodeRuntimeView`).
 */
export const RUNTIME_STATE_LABELS = Object.freeze({
  NOT_READY: 'Not ready',
  AWAITING_MODEL: 'Awaiting model',
  WARMING: 'Warming',
  READY: 'Ready',
});

/** The one runtime label that means "this node can produce values". */
export const RUNTIME_READY = 'READY';

/** Training is a job state, not a runtime label, and gets its own word. */
export const RUNTIME_TRAINING_LABEL = 'Training';

/** The training statuses that mean a job is still going. */
const LIVE_TRAINING_EVENTS = new Set([
  OWNED_CHANNEL_EVENTS.TRAINING_QUEUED,
  OWNED_CHANNEL_EVENTS.TRAINING_PROGRESS,
  OWNED_CHANNEL_EVENTS.TRAINING_CANCEL_REQUESTED,
]);

/**
 * Nothing has arrived yet.
 *
 * Every field is empty rather than optimistic: no runtime state (so no node reads as
 * ready), no canvas verdict (so the lock is neither asserted nor denied), no training,
 * no refusals. A builder that has heard nothing must look like a builder that has heard
 * nothing.
 */
export const INITIAL_REALTIME = Object.freeze({
  status: REALTIME_STATES.UNAVAILABLE,
  /** The last `deployment.runtime_state` payload, verbatim. */
  runtime: null,
  /** The last `strategy.canvas_state` payload, verbatim. */
  canvasState: null,
  /** The last `strategy.lifecycle` payload. */
  lifecycle: null,
  /** The last `deployment.state` payload. */
  deployment: null,
  /** The last `deployment.guard_trip` payload — kept, because a trip is not a state. */
  guardTrip: null,
  /** The last `validation.report` / `validation.failed` payload. */
  validation: null,
  /** `node_id -> the last training frame for that node`. */
  training: {},
  /** `channel -> the refusal frame`, so Requirement 21.6 reaches the screen. */
  refusals: {},
  /** When the last frame of any kind arrived, for "nothing since" reporting. */
  lastFrameAt: null,
});

const asObject = (value) => (value && typeof value === 'object' ? value : null);

/**
 * Reduce one pushed frame, or one status transition, into the realtime state.
 *
 * Unknown frame types are IGNORED rather than stored: a frame this build cannot read is
 * not information about the strategy, and putting it somewhere a renderer might find it
 * is how an unknown state becomes a rendered one.
 *
 * @param {Object} state
 * @param {{type: string, frame?: Object, status?: string, at?: number}} action
 * @returns {Object}
 */
export function realtimeReducer(state = INITIAL_REALTIME, action = {}) {
  switch (action.type) {
    case 'status': {
      const status = action.status;
      if (state.status === status) return state;
      return { ...state, status };
    }

    case 'reset':
      // A strategy change, or the last view closing. Nothing carries over: frames
      // describe one strategy and one deployment, and keeping them across a change
      // would attribute one strategy's runtime state to another.
      return { ...INITIAL_REALTIME, status: state.status };

    case 'frame': {
      const frame = asObject(action.frame);
      if (!frame) return state;
      const at = Number.isFinite(action.at) ? action.at : Date.now();
      const next = reduceFrame(state, frame, at);
      return next === state ? state : { ...next, lastFrameAt: at };
    }

    default:
      return state;
  }
}

function reduceFrame(state, frame, at) {
  const type = typeof frame.type === 'string' ? frame.type : '';

  if (type === SUBSCRIPTION_REFUSED) {
    const channel = typeof frame.channel === 'string' ? frame.channel : '';
    if (!channel) return state;
    return {
      ...state,
      refusals: {
        ...state.refusals,
        [channel]: {
          channel,
          code: typeof frame.code === 'string' ? frame.code : '',
          reason: typeof frame.reason === 'string' ? frame.reason : '',
          at,
        },
      },
    };
  }

  switch (type) {
    case OWNED_CHANNEL_EVENTS.DEPLOYMENT_RUNTIME_STATE: {
      const runtime = asObject(frame.runtime_state);
      // A frame with no payload is not an empty runtime state; it is a broken frame,
      // and replacing a good reading with it would blank the canvas.
      if (!runtime) return state;
      return { ...state, runtime, deploymentId: frame.deployment_id || null };
    }

    case OWNED_CHANNEL_EVENTS.STRATEGY_CANVAS_STATE: {
      const canvasState = asObject(frame.canvas_state);
      if (!canvasState) return state;
      return { ...state, canvasState };
    }

    case OWNED_CHANNEL_EVENTS.STRATEGY_LIFECYCLE:
      return { ...state, lifecycle: { ...frame } };

    case OWNED_CHANNEL_EVENTS.DEPLOYMENT_STATE:
      return { ...state, deployment: { ...frame } };

    case OWNED_CHANNEL_EVENTS.DEPLOYMENT_GUARD_TRIP:
      // Kept alongside the state rather than replacing it: a guard trip is the reason a
      // deployment stopped, and the stop arrives as its own frame.
      return { ...state, guardTrip: { ...frame } };

    case OWNED_CHANNEL_EVENTS.VALIDATION_REPORT:
    case OWNED_CHANNEL_EVENTS.VALIDATION_FAILED:
      return { ...state, validation: { ...frame } };

    case OWNED_CHANNEL_EVENTS.TRAINING_QUEUED:
    case OWNED_CHANNEL_EVENTS.TRAINING_PROGRESS:
    case OWNED_CHANNEL_EVENTS.TRAINING_COMPLETED:
    case OWNED_CHANNEL_EVENTS.TRAINING_FAILED:
    case OWNED_CHANNEL_EVENTS.TRAINING_CANCELLED:
    case OWNED_CHANNEL_EVENTS.TRAINING_CANCEL_REQUESTED: {
      const nodeId = typeof frame.node_id === 'string' ? frame.node_id : '';
      if (!nodeId) return state;
      return {
        ...state,
        training: { ...state.training, [nodeId]: { ...frame, at } },
      };
    }

    default:
      return state;
  }
}

/**
 * What to draw on one node (Requirement 20.12).
 *
 * Precedence, and the reason for it: a **training** job is reported ahead of the
 * runtime label. A node whose model is being trained is `AWAITING_MODEL` at the same
 * time — correctly, since there is no active version yet — and both statements are
 * true, but only one of them tells the author what is happening and that waiting will
 * fix it. `AWAITING_MODEL` with no job running is the one that means "go and train
 * this", and that is exactly when it is shown.
 *
 * `known: false` is returned when no runtime frame has arrived, or when the label is
 * one this build does not recognise. Neither is ever reported as ready: a node nobody
 * has evaluated is not a node that is fine.
 *
 * @param {Object} realtime
 * @param {string} nodeId
 * @returns {{known: boolean, state: string|null, label: string, detail: string,
 *            barsSeen: number|null, barsNeeded: number|null, barsRemaining: number|null,
 *            missing: string[], training: Object|null}}
 */
export function nodeRuntimeView(realtime, nodeId) {
  const empty = {
    known: false,
    state: null,
    label: '',
    detail: '',
    barsSeen: null,
    barsNeeded: null,
    barsRemaining: null,
    missing: [],
    training: null,
  };
  if (!realtime || typeof nodeId !== 'string' || nodeId === '') return empty;

  const job = trainingView(realtime, nodeId);
  const runtime = asObject(realtime.runtime);
  const nodes = runtime ? asObject(runtime.nodes) : null;
  const node = nodes ? asObject(nodes[nodeId]) : null;

  if (job && job.running) {
    return {
      ...empty,
      known: true,
      state: 'TRAINING',
      label: RUNTIME_TRAINING_LABEL,
      detail: job.detail,
      training: job,
    };
  }

  if (!node) return { ...empty, training: job };

  const label = RUNTIME_STATE_LABELS[node.state];
  if (!label) {
    // A label this build does not know. Reported as unknown, with the raw word, so an
    // operator can see what arrived — and never mapped onto one of the four.
    return {
      ...empty,
      state: typeof node.state === 'string' ? node.state : null,
      detail: typeof node.state === 'string' ? `Unrecognised runtime state ${node.state}` : '',
      training: job,
    };
  }

  const barsSeen = Number.isFinite(runtime.bars_seen) ? runtime.bars_seen : null;
  const barsNeeded = Number.isFinite(node.bars_needed) ? node.bars_needed : null;
  const barsRemaining = Number.isFinite(node.bars_remaining) ? node.bars_remaining : null;
  const missing = Array.isArray(node.missing) ? node.missing.filter((name) => typeof name === 'string') : [];

  return {
    known: true,
    state: node.state,
    label,
    detail: runtimeDetail(node, { barsSeen, barsNeeded, barsRemaining, missing }),
    barsSeen,
    barsNeeded,
    barsRemaining,
    missing,
    training: job,
  };
}

/**
 * The sentence beside the label.
 *
 * The figures are the server's. `warmup n/m` is `design.md`'s own wording for the
 * warming state, and both numbers are printed because one of them alone is not a
 * progress report — "warming, 12 bars to go" gives no sense of how long that is.
 */
function runtimeDetail(node, { barsSeen, barsNeeded, barsRemaining, missing }) {
  if (node.state === 'WARMING') {
    if (barsSeen === null || barsNeeded === null) return 'Warming up.';
    const remaining = barsRemaining === null ? '' : ` — ${barsRemaining} to go`;
    return `warmup ${barsSeen}/${barsNeeded} bars${remaining}`;
  }
  if (node.state === 'NOT_READY') {
    if (missing.length === 0) return 'This block has no value yet.';
    return `Waiting on ${missing.join(', ')}.`;
  }
  if (node.state === 'AWAITING_MODEL') {
    return 'No verified model is bound to this block yet.';
  }
  return '';
}

/**
 * How far a training job has got for one node, or null.
 *
 * `detail` carries the epoch counter Requirement 20.12 asks for, from the worker's own
 * `epoch` / `epochs_total` fields. A job whose totals have not arrived reports the
 * status word alone rather than a fabricated "epoch 0 of 0".
 *
 * @param {Object} realtime
 * @param {string} nodeId
 * @returns {{running: boolean, event: string, epoch: number|null, epochsTotal: number|null,
 *            detail: string}|null}
 */
export function trainingView(realtime, nodeId) {
  const frames = realtime ? asObject(realtime.training) : null;
  const frame = frames ? asObject(frames[nodeId]) : null;
  if (!frame) return null;

  const event = typeof frame.type === 'string' ? frame.type : '';
  const epoch = Number.isFinite(frame.epoch) ? frame.epoch : null;
  const epochsTotal = Number.isFinite(frame.epochs_total) ? frame.epochs_total : null;
  const running = LIVE_TRAINING_EVENTS.has(event);

  let detail = '';
  if (event === OWNED_CHANNEL_EVENTS.TRAINING_QUEUED) {
    detail = 'Queued for training.';
  } else if (event === OWNED_CHANNEL_EVENTS.TRAINING_CANCEL_REQUESTED) {
    detail = 'Cancellation requested; the worker has not stopped yet.';
  } else if (epoch !== null && epochsTotal !== null && epochsTotal > 0) {
    detail = `epoch ${epoch}/${epochsTotal}`;
  } else if (event === OWNED_CHANNEL_EVENTS.TRAINING_PROGRESS) {
    detail = 'Training.';
  }

  return { running, event, epoch, epochsTotal, detail };
}

/**
 * The deployed lock (Requirement 9.9), as the backend decided it.
 *
 * `locked` is the server's `read_only` and nothing else — not an inference from a
 * lifecycle word, which is the whole reason task 8.3 publishes this block. Until a
 * verdict arrives `locked` is **false**: this projection must not lock a canvas nobody
 * said was locked, and the save path has its own refusal for a version that turns out
 * to be immutable, so an unlocked canvas cannot commit an illegal write.
 *
 * @param {Object} realtime
 * @returns {{known: boolean, locked: boolean, reason: string, frozenFields: string[],
 *            lifecycleState: string|null}}
 */
export function deployedLockView(realtime) {
  const canvasState = realtime ? asObject(realtime.canvasState) : null;
  if (!canvasState || Object.keys(canvasState).length === 0) {
    return { known: false, locked: false, reason: '', frozenFields: [], lifecycleState: null };
  }
  return {
    known: true,
    locked: canvasState.read_only === true,
    reason: typeof canvasState.reason === 'string' ? canvasState.reason : '',
    frozenFields: Array.isArray(canvasState.frozen_fields) ? canvasState.frozen_fields.slice() : [],
    lifecycleState: typeof canvasState.lifecycle_state === 'string' ? canvasState.lifecycle_state : null,
  };
}

/**
 * Every refusal, oldest first, for reporting (Requirement 21.6).
 * @param {Object} realtime
 * @returns {Array<{channel: string, code: string, reason: string}>}
 */
export function refusalList(realtime) {
  const refusals = realtime ? asObject(realtime.refusals) : null;
  if (!refusals) return [];
  return Object.values(refusals)
    .filter((entry) => entry && typeof entry.channel === 'string')
    .sort((a, b) => (a.at || 0) - (b.at || 0));
}

/**
 * The channels a builder view subscribes for one strategy / deployment / training job.
 *
 * Composed here rather than in the component so that "which channels does the builder
 * open" is one list with one answer, and so an id that cannot form a channel name
 * (an unsaved strategy, an absent deployment) simply contributes nothing instead of
 * producing a name the server would refuse.
 *
 * @param {{strategyId?: string, deploymentId?: string, trainingJobIds?: string[]}} ids
 * @returns {string[]}
 */
export function builderChannels({ strategyId, deploymentId, trainingJobIds } = {}) {
  const channels = [];
  const push = (channel) => {
    if (channel && !channels.includes(channel)) channels.push(channel);
  };

  push(ownedChannel(OWNED_CHANNELS.BUILDER_VALIDATION, strategyId));
  push(ownedChannel(OWNED_CHANNELS.STRATEGY, strategyId));
  push(ownedChannel(OWNED_CHANNELS.DEPLOYMENT, deploymentId));
  push(ownedChannel(OWNED_CHANNELS.EXECUTION, deploymentId));
  for (const jobId of trainingJobIds || []) {
    push(ownedChannel(OWNED_CHANNELS.TRAINING, jobId));
  }
  return channels;
}

/** Re-exported so a caller has one import for the channel helpers it needs. */
export { OWNED_CHANNELS, ownedChannel, parseOwnedChannel };
