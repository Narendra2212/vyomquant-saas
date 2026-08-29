/**
 * ═══════════════════════════════════════════════════════════════════════════
 * SIGNAL TRACE REALTIME — which channels the page holds, and what a frame means
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * trading-lifecycle-integration task 19.1. Requirements 17.4, 18.1, 18.2, 18.6, 18.7,
 * 18.8, plus 18.3's "a refusal must be visible" for the refusal it records.
 *
 * WHAT THIS FILE IS FOR
 * ---------------------
 * Two questions, both pure, both answerable without a socket:
 *
 * 1. **Which channels does the Signal_Trace_Page subscribe to?** `SIGNAL_FAMILY` is
 *    scoped to `deployment_id` (`ws_channels.py`, task 14.1), so a page filtered by
 *    *strategy* — which is exactly how Requirement 17.4's entry path from the
 *    Strategies_Page arrives — holds one channel per deployment that strategy currently
 *    has, not one channel for the strategy. Composing that list is the first half of
 *    this module.
 * 2. **What does one frame do to what is on screen?** A `signal.generated` /
 *    `signal.status_changed` / `signal.snapshot` frame carries the signal's own
 *    `to_public_dict()` payload (`ws_channels.signal_frame`), so applying it is a
 *    projection, not a computation: nothing here decides a lifecycle state, a status
 *    word or an ordering. That is the second half.
 *
 * WHY THE REDUCER LIVES HERE AND NOT IN THE HOOK
 * ----------------------------------------------
 * Same reason as `lib/builderRealtime.js`, whose split this mirrors: a frame arriving is
 * a function from (state, frame) to state, so duplicate delivery, an unknown frame type
 * and a refused subscription are all assertable without a socket, a timer or a rendered
 * tree. `hooks/useSignalTraceRealtime.js` owns only the connection lifecycle.
 *
 * WHAT TASK 19.2 ADDED
 * --------------------
 * Content-key dedup ({@link applyFrame}) and the snapshot settlement that keeps a
 * reconnect's REST re-read from being overwritten by pre-drop pushed state
 * ({@link signalRealtimeReducer}'s `snapshot` action). Sequence-gap handling is NOT here
 * and is not duplicated here: `websocketClient.js`'s existing
 * `expectedSequence`/`messageBuffer`/`requestMessageReplay` mechanism runs before a frame
 * ever reaches this module, and a second copy of it would be a second thing to keep in
 * agreement with the server's counter.
 *
 * WHAT IS DELIBERATELY NOT HERE YET
 * ---------------------------------
 * The connection-status *indicator* is task 19.3's; this module reports the state, and
 * renders nothing.
 */

import {
  OWNED_CHANNELS,
  OWNED_CHANNEL_EVENTS,
  SUBSCRIPTION_REFUSED,
  ownedChannel,
  parseOwnedChannel,
} from '../constants/wsChannels';
import { deploymentBindingState } from './strategyHealth';

// ── The connection, as this page reports it ────────────────────────────────────────────

/**
 * The realtime connection states. The same four `lib/builderRealtime.js` reports, and
 * the same words, because Requirement 18.6 defines this page's reconnect behaviour by
 * reference to that contract — two vocabularies for one connection would be two things
 * to keep in agreement.
 *
 * `UNAVAILABLE` is "this page has no connection to lose": no token, or no deployment to
 * subscribe to. Collapsing it into `DISCONNECTED` would report a dropped connection
 * where there was never one and hide the actual reason behind a reconnect message that
 * cannot succeed.
 */
export const SIGNAL_REALTIME_STATES = Object.freeze({
  CONNECTED: 'CONNECTED',
  CONNECTING: 'CONNECTING',
  DISCONNECTED: 'DISCONNECTED',
  UNAVAILABLE: 'UNAVAILABLE',
});

/** `websocketClient`'s status vocabulary mapped onto the four above. */
const SOCKET_STATUS_TO_REALTIME = Object.freeze({
  connected: SIGNAL_REALTIME_STATES.CONNECTED,
  connecting: SIGNAL_REALTIME_STATES.CONNECTING,
  disconnected: SIGNAL_REALTIME_STATES.DISCONNECTED,
  error: SIGNAL_REALTIME_STATES.DISCONNECTED,
  failed: SIGNAL_REALTIME_STATES.DISCONNECTED,
});

/**
 * Project one of `websocketClient`'s statuses onto a reported realtime state.
 * An unknown status reads `DISCONNECTED`: a status this build cannot read is not
 * evidence that frames are arriving.
 *
 * @param {string} status
 * @returns {string}
 */
export function signalRealtimeStateFromSocket(status) {
  return SOCKET_STATUS_TO_REALTIME[status] || SIGNAL_REALTIME_STATES.DISCONNECTED;
}

// ── Which deployments, and therefore which channels ───────────────────────────────────

/**
 * The binding states a deployment can be in and still produce signals.
 *
 * These are `strategy_lifecycle.STOPPABLE_BINDING_STATES` — the same three the backend
 * calls a strategy's *active* deployments when it refuses to archive it (Requirement
 * 3.1). Reused rather than restated so "which deployments does this strategy currently
 * have" has one answer on both sides. `DEPLOYING` is included on purpose: a deployment
 * that is bound but not yet confirmed running is precisely the one whose first signal a
 * watching user is waiting for, and subscribing after the fact would miss it.
 *
 * A `STOPPED` or `FAILED` deployment is not subscribed to. Its signals are still in the
 * list (the REST read is unaffected); it simply will not publish any more.
 */
export const ACTIVE_BINDING_STATES = Object.freeze(['DEPLOYING', 'RUNNING', 'PAUSED']);

const ACTIVE_BINDING_STATE_SET = new Set(ACTIVE_BINDING_STATES);

/**
 * Whether a `strategy_deployments.status` spelling names an active deployment.
 *
 * The spelling → binding-state map is `lib/strategyHealth.js`'s, which is itself a
 * transcription of `strategy_lifecycle._STATUS_TO_BINDING_STATE`. An unreadable spelling
 * is **not** active: subscribing on a status we cannot read would ask the server for a
 * channel on the strength of a guess.
 *
 * @param {string|null|undefined} status
 * @returns {boolean}
 */
export function isActiveDeploymentStatus(status) {
  const state = deploymentBindingState(status);
  return state !== null && ACTIVE_BINDING_STATE_SET.has(state);
}

const text = (value) => (typeof value === 'string' ? value.trim() : '');

/**
 * The active deployment ids in a `GET /api/strategy-operations/strategies/{id}/deployments`
 * body, in the order the server listed them.
 *
 * A row whose status is absent is treated as **active**: the endpoint's contract is that
 * it lists the strategy's deployments, and dropping one because a field we use only as a
 * filter is missing would silently stop watching a live deployment. A row whose status is
 * present and names a stopped/failed state is dropped, because that is a positive
 * statement that it has nothing left to publish.
 *
 * @param {Object|null|undefined} body
 * @returns {string[]}
 */
export function activeDeploymentIds(body) {
  const rows = Array.isArray(body?.deployments) ? body.deployments : [];
  const ids = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const id = text(row.deployment_id) || text(row.id);
    if (!id || ids.includes(id)) continue;
    const status = text(row.status) || text(row.state);
    if (status !== '' && !isActiveDeploymentStatus(status)) continue;
    ids.push(id);
  }
  return ids;
}

/**
 * The deployment ids named by the signals already on screen.
 *
 * This is the second source, and it is not redundant. The deployment listing above is
 * served from `deployment_manager`'s in-process registry, which does not contain a
 * deployment started through `deploy_version` (see `strategy_operations.get_deployment`'s
 * own note on `runtime_attached`), and an unfiltered Signal_Trace_Page has no strategy to
 * ask about at all. A signal row states its own `deployment_id`, so a deployment that has
 * produced a visible signal is a deployment this page can subscribe to whatever the
 * registry says.
 *
 * @param {Array<Object>|null|undefined} signals
 * @returns {string[]}
 */
export function deploymentIdsFromSignals(signals) {
  const rows = Array.isArray(signals) ? signals : [];
  const ids = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const id = text(row.deployment_id);
    if (!id || ids.includes(id)) continue;
    ids.push(id);
  }
  return ids;
}

/**
 * One list of deployment ids from several sources, first occurrence winning, duplicates
 * and unusable entries dropped.
 *
 * @param {...(Array<string>|null|undefined)} lists
 * @returns {string[]}
 */
export function mergeDeploymentIds(...lists) {
  const ids = [];
  for (const list of lists) {
    if (!Array.isArray(list)) continue;
    for (const entry of list) {
      const id = text(entry);
      if (!id || ids.includes(id)) continue;
      ids.push(id);
    }
  }
  return ids;
}

/**
 * `signal.{deployment_id}` for one deployment, or `null` when the id could not appear in
 * a channel name.
 *
 * Null rather than a throw, for `ownedChannel`'s own reason: a page that has not learned
 * any deployment yet is a normal state of the view, not an error, and a malformed id must
 * contribute nothing rather than produce a name the server would refuse.
 *
 * @param {string} deploymentId
 * @returns {string|null}
 */
export function signalChannel(deploymentId) {
  return ownedChannel(OWNED_CHANNELS.SIGNAL, deploymentId);
}

/**
 * The channels the Signal_Trace_Page holds for a set of deployments (Requirement 18.2).
 *
 * One channel per deployment, in the order the ids were given, without duplicates. This
 * is the whole of Requirement 17.4's strategy → channels step: the strategy contributes
 * its deployments and each deployment contributes its own owner-scoped channel.
 *
 * @param {Array<string>|null|undefined} deploymentIds
 * @returns {string[]}
 */
export function signalTraceChannels(deploymentIds) {
  const channels = [];
  for (const id of Array.isArray(deploymentIds) ? deploymentIds : []) {
    const channel = signalChannel(id);
    if (channel && !channels.includes(channel)) channels.push(channel);
  }
  return channels;
}

/** The deployment id a `signal.{id}` channel name is about, or `null`. */
export function deploymentIdOfChannel(channel) {
  const parsed = parseOwnedChannel(channel);
  if (!parsed || parsed.family.namespace !== OWNED_CHANNELS.SIGNAL.namespace) return null;
  return parsed.resourceId;
}

// ── The frames ────────────────────────────────────────────────────────────────────────

/** The three frame types `signal.{deployment_id}` carries (`ws_channels.SignalEvent`). */
export const SIGNAL_FRAME_TYPES = Object.freeze([
  OWNED_CHANNEL_EVENTS.SIGNAL_GENERATED,
  OWNED_CHANNEL_EVENTS.SIGNAL_STATUS_CHANGED,
  OWNED_CHANNEL_EVENTS.SIGNAL_SNAPSHOT,
]);

const SIGNAL_FRAME_TYPE_SET = new Set(SIGNAL_FRAME_TYPES);

/**
 * Nothing has arrived yet.
 *
 * Empty rather than optimistic: no signals, no refusals, no last-frame time, and a
 * connection reported as `UNAVAILABLE`. A page that has heard nothing must look like a
 * page that has heard nothing.
 */
export const INITIAL_SIGNAL_REALTIME = Object.freeze({
  status: SIGNAL_REALTIME_STATES.UNAVAILABLE,
  /** `signal_id -> the latest signal payload pushed for it`, verbatim from the frame. */
  signals: Object.freeze({}),
  /** Every applied frame's identity, oldest first, for reporting and for tests. */
  applied: Object.freeze([]),
  /**
   * `dedup_key -> when that state change was first applied` (task 19.2, Requirement 18.4).
   *
   * The content-level ledger, and the reason it is a map rather than a scan of `applied`:
   * every frame is checked against it, and a page watching a busy deployment for an hour
   * would otherwise pay the length of its own history per frame.
   */
  appliedKeys: Object.freeze({}),
  /**
   * How many frames were discarded because their content key had already been applied.
   *
   * Counted rather than stored: the frames themselves carry nothing new by definition, and
   * a reconnect-replay loop would make an unbounded list of them. The count is what says
   * "dedup is doing something", which is what Requirement 18.4's tests assert against.
   */
  duplicatesDiscarded: 0,
  /** `channel -> the refusal frame`, so Requirement 18.3 can reach the screen. */
  refusals: Object.freeze({}),
  /** When the last frame of any kind arrived. */
  lastFrameAt: null,
});

const asObject = (value) =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value : null;

/**
 * The content-level dedup key the server puts on every frame
 * (`f"{signal_id}:{order_lifecycle_state}"`, `ws_channels.signal_dedup_key`).
 *
 * Read off the frame rather than recomputed, and only reconstructed from the frame's own
 * `signal_id`/`order_lifecycle_state` when the server did not send it. Task 19.2 is what
 * *uses* this to discard an already-applied state change; 19.1 only records it.
 *
 * @param {Object} frame
 * @returns {string}
 */
export function frameDedupKey(frame) {
  const sent = text(frame?.dedup_key);
  if (sent !== '') return sent;
  const signalId = text(frame?.signal_id) || text(asObject(frame?.signal)?.id);
  const state = text(frame?.order_lifecycle_state);
  if (signalId === '') return '';
  return `${signalId}:${state}`;
}

/** The signal payload a frame carries, or `null`. `signal` is the server's field name. */
export function frameSignal(frame) {
  const signal = asObject(frame?.signal);
  if (!signal) return null;
  const id = text(signal.id) || text(frame?.signal_id);
  if (id === '') return null;
  return signal;
}

/**
 * Reduce one pushed frame, one status transition, one reset, or one settled snapshot.
 *
 * @param {Object} state
 * @param {{type: string, frame?: Object, status?: string, at?: number}} action
 * @returns {Object}
 */
export function signalRealtimeReducer(state = INITIAL_SIGNAL_REALTIME, action = {}) {
  switch (action.type) {
    case 'status': {
      if (state.status === action.status) return state;
      return { ...state, status: action.status };
    }

    case 'reset':
      // A filter change, or the page closing. Nothing carries over: the frames describe
      // the deployments that were subscribed to, and keeping them across a change would
      // attribute one strategy's signals to another. The connection itself survives,
      // because it is shared and was not the thing that changed.
      return { ...INITIAL_SIGNAL_REALTIME, status: state.status };

    /*
      A snapshot read that was requested at `action.at` has come back and the caller has
      already put its rows on screen. Task 19.2, Requirements 18.5 and 18.6.

      WHY THE OVERLAY HAS TO BE DROPPED HERE
      --------------------------------------
      `mergeRealtimeSignals` applies the pushed overlay ON TOP of the rows the server
      returned, so an overlay entry that predates the snapshot would override the very
      state the snapshot was requested to learn. Concretely: FILLED is pushed, the socket
      drops, the signal reaches CLOSED while it is down, the snapshot returns CLOSED — and
      without this the page would keep rendering FILLED forever, because no further frame
      is coming for a transition that already happened. That is precisely the "assuming no
      updates were missed" Requirement 18.6 forbids, arriving through the back door.

      ONLY WHAT THE SNAPSHOT SUPERSEDES
      ---------------------------------
      Entries applied at or before `at` are dropped; an entry applied AFTER the request was
      issued is kept, because a frame that arrived while the read was in flight may well be
      newer than the read and dropping it would lose a state change (Requirement 18.5's
      "in the order in which they were actually persisted").

      `appliedKeys` deliberately SURVIVES. A snapshot carries the same states the frames
      did, so a state change the client has applied is a state change it must not apply
      again if the server replays it on the new connection (Requirement 18.4) — and the new
      connection's `seq` restarts at 1, so `seq` cannot catch that. Requirement 23.6's
      per-channel sequence and this content key fail in different directions, which is why
      both exist.
    */
    case 'snapshot': {
      const at = Number.isFinite(action.at) ? action.at : Date.now();
      const supersededSignals = supersededOverlay(state, at);
      if (supersededSignals === state.signals) return state;
      return { ...state, signals: supersededSignals };
    }

    case 'frame': {
      const frame = asObject(action.frame);
      if (!frame) return state;
      const at = Number.isFinite(action.at) ? action.at : Date.now();
      const next = applyFrame(state, frame, at);
      return next === state ? state : { ...next, lastFrameAt: at };
    }

    default:
      return state;
  }
}

/**
 * The overlay with every entry the snapshot requested at `at` supersedes removed.
 *
 * An entry is superseded when the last frame that touched that signal arrived at or before
 * the snapshot was requested. `applied` is the record of when that was — read backwards,
 * because the last mention of a signal is the one that decides.
 *
 * Returns `state.signals` itself when nothing is superseded, so the reducer can hand back
 * the same state and React can skip the render.
 *
 * @param {Object} state
 * @param {number} at
 * @returns {Object}
 */
function supersededOverlay(state, at) {
  const overlay = asObject(state.signals) || {};
  const ids = Object.keys(overlay);
  if (ids.length === 0) return state.signals;

  const lastTouchedAt = new Map();
  for (const entry of Array.isArray(state.applied) ? state.applied : []) {
    if (entry && typeof entry.signalId === 'string') lastTouchedAt.set(entry.signalId, entry.at);
  }

  const kept = {};
  let dropped = 0;
  for (const id of ids) {
    const touched = lastTouchedAt.get(id);
    // An entry with no recorded arrival time cannot be shown to be newer than the read, so
    // the read wins: the server's own row is the authority the snapshot was asked for.
    if (Number.isFinite(touched) && touched > at) kept[id] = overlay[id];
    else dropped += 1;
  }
  return dropped === 0 ? state.signals : kept;
}

/**
 * One frame applied — or discarded as a state change already applied.
 *
 * A frame type this build does not know is IGNORED rather than stored: it is not
 * information about a signal, and putting it where a renderer might find it is how an
 * unknown state becomes a rendered one.
 *
 * DEDUP IS THE SECOND OF TWO, NOT THE ONLY ONE (task 19.2, Requirements 18.4, 23.6)
 * --------------------------------------------------------------------------------
 * `websocketClient.js`'s `expectedSequence`/`messageBuffer`/`requestMessageReplay`
 * mechanism has already run by the time a frame reaches this function, and it is left
 * exactly as it is: it catches network-level duplication and reordering by `seq`, per
 * connection. What it cannot catch is a reconnect — the server's per-channel counter
 * restarts at 1 for a channel's first subscriber (`ws_channels.seed_signal_sequence`,
 * `restart=True`) at the same moment the client's `expectedSequence` does, so a replayed
 * state change on the new connection carries a `seq` that looks like fresh history.
 *
 * So the content key (`f"{signal_id}:{order_lifecycle_state}"`, `ws_channels.signal_dedup_key`)
 * decides here, independently of `seq`: a state change whose key is already in
 * `appliedKeys` is discarded without being re-applied. The two checks fail in different
 * directions, which is the whole reason the design carries both.
 *
 * WHAT THIS DOES NOT DISTINGUISH, AND THAT IS THE SPECIFIED BEHAVIOUR
 * ------------------------------------------------------------------
 * The key names a signal and a lifecycle state, so two frames reporting the same signal in
 * the same state are the same event as far as this page is concerned — including the case
 * where the second one carries a changed field the key does not mention (a second partial
 * fill under `PARTIALLY_FILLED`, say). Requirement 18.4 defines duplicate detection on
 * exactly that identity ("signal id combined with status/version"), and the server builds
 * the key from exactly those two fields, so widening it here would put the two sides of the
 * boundary into disagreement about what a replay is.
 */
function applyFrame(state, frame, at) {
  const type = text(frame.type);

  if (type === SUBSCRIPTION_REFUSED) {
    const channel = text(frame.channel);
    if (channel === '') return state;
    return {
      ...state,
      refusals: {
        ...state.refusals,
        [channel]: {
          channel,
          deploymentId: deploymentIdOfChannel(channel),
          code: text(frame.code),
          reason: text(frame.reason) || text(frame.message),
          at,
        },
      },
    };
  }

  if (!SIGNAL_FRAME_TYPE_SET.has(type)) return state;

  const signal = frameSignal(frame);
  if (!signal) return state;

  const id = text(signal.id) || text(frame.signal_id);
  const dedupKey = frameDedupKey(frame);
  const appliedKeys = asObject(state.appliedKeys) || {};

  if (dedupKey !== '' && Object.prototype.hasOwnProperty.call(appliedKeys, dedupKey)) {
    // Counted, not applied. `signals` and `applied` keep their identities, so nothing
    // downstream of them re-renders and no second event can reach the screen for one
    // underlying status change (Requirement 18.4).
    return { ...state, duplicatesDiscarded: (state.duplicatesDiscarded || 0) + 1 };
  }

  return {
    ...state,
    signals: { ...state.signals, [id]: signal },
    applied: [
      ...state.applied,
      {
        type,
        signalId: id,
        dedupKey,
        seq: Number.isFinite(frame.seq) ? frame.seq : null,
        deploymentId: text(frame.deployment_id) || deploymentIdOfChannel(frame.channel),
        at,
      },
    ],
    // A frame with no usable content key (the server always sends one; a hand-rolled or
    // older producer might not) is applied but not recorded as applied, because a key of
    // `''` would make every such frame a duplicate of every other.
    appliedKeys: dedupKey === '' ? appliedKeys : { ...appliedKeys, [dedupKey]: at },
  };
}

// ── Projecting the pushed state onto the rendered list ────────────────────────────────

/**
 * The rendered rows with every pushed update applied in place.
 *
 * In place, and only in place: a row keeps its position, so the descending
 * generation-time order the server sorted by (Requirement 17.1) is not re-derived on the
 * client from a field that may be absent. A pushed signal the list does not contain is
 * NOT inserted here — see {@link unlistedSignalIds} for why.
 *
 * The pushed payload and the list row are the same projection
 * (`signal_service.Signal.to_public_dict()`, which is what both
 * `GET /api/signal-trace/signals` and `ws_channels.signal_frame` carry), so the update is
 * a merge of two readings of one record rather than a translation between two shapes.
 * The row's own fields are kept where the frame does not mention them.
 *
 * @param {Array<Object>|null|undefined} rows
 * @param {Object|null|undefined} pushed `signal_id -> payload`, i.e. `realtime.signals`.
 * @returns {Array<Object>} `rows` itself when nothing applied, so React can skip the render.
 */
export function mergeRealtimeSignals(rows, pushed) {
  const list = Array.isArray(rows) ? rows : [];
  const updates = asObject(pushed);
  if (!updates || list.length === 0) return list;

  let changed = false;
  const merged = list.map((row) => {
    const id = text(row?.id);
    const update = id === '' ? null : asObject(updates[id]);
    if (!update) return row;
    changed = true;
    return { ...row, ...update };
  });
  return changed ? merged : list;
}

/**
 * The ids of signals that have been pushed but are not on the rendered page.
 *
 * Reported rather than inserted, and this is a correctness choice rather than a shortcut.
 * The list the user is looking at is one page of a server-side query with up to thirteen
 * filter categories and a server-side sort; the client cannot decide whether a newly
 * generated signal belongs on *this* page without re-implementing that query, and a
 * client-side guess would either hide a signal that matches (Requirement 17.1: "SHALL NOT
 * omit a signal that was actually generated") or show one that does not. So the page
 * states that new signals exist and offers the read that answers correctly — which is the
 * same snapshot read Requirement 18.6 already defines.
 *
 * @param {Array<Object>|null|undefined} rows
 * @param {Object|null|undefined} pushed `signal_id -> payload`, i.e. `realtime.signals`.
 * @returns {string[]}
 */
export function unlistedSignalIds(rows, pushed) {
  const updates = asObject(pushed);
  if (!updates) return [];
  const present = new Set(
    (Array.isArray(rows) ? rows : []).map((row) => text(row?.id)).filter((id) => id !== ''),
  );
  return Object.keys(updates).filter((id) => !present.has(id));
}

/**
 * Every refusal, oldest first, so Requirement 18.3's "surface a visible error state to
 * the user rather than silently displaying no data" has something to render.
 *
 * @param {Object} realtime
 * @returns {Array<{channel: string, deploymentId: string|null, code: string, reason: string}>}
 */
export function signalRefusalList(realtime) {
  const refusals = asObject(realtime?.refusals);
  if (!refusals) return [];
  return Object.values(refusals)
    .filter((entry) => entry && typeof entry.channel === 'string')
    .sort((a, b) => (a.at || 0) - (b.at || 0));
}

/** Re-exported so a caller has one import for the channel helpers it needs. */
export { OWNED_CHANNELS, OWNED_CHANNEL_EVENTS, SUBSCRIPTION_REFUSED, ownedChannel };
