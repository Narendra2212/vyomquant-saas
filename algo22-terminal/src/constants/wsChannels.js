/**
 * WebSocket Channel Constants
 * 
 * Single source of truth for WebSocket event channels.
 * Aligned with backend ws_channels.py
 * 
 * @module constants/wsChannels
 */

/**
 * WebSocket channel definitions.
 * These MUST match the backend ChannelType enum exactly.
 */
export const WS_CHANNELS = {
  /** Bot health and connectivity status */
  BOT_STATUS: 'bot_status',
  
  /** Signal trace visualization pipeline */
  SIGNAL_TRACE: 'signal_trace',
  
  /** Order execution events (fills, rejects, errors) */
  EXECUTION_EVENTS: 'execution_events',
  
  /** Risk management events (blocks, warnings, limits) */
  RISK_EVENTS: 'risk_events',
  
  /** Strategy deployment events (start, success, fail) */
  DEPLOYMENT_EVENTS: 'deployment_events',
  
  /** Infrastructure health (admin-only) */
  INFRASTRUCTURE: 'infrastructure',
  
  /** Legacy aliases for backward compatibility (deprecated) */
  get SIGNAL() { console.warn('WS_CHANNELS.SIGNAL is deprecated, use SIGNAL_TRACE'); return 'signal_trace'; },
  get INFRA() { console.warn('WS_CHANNELS.INFRA is deprecated, use BOT_STATUS'); return 'bot_status'; }
};

/**
 * Valid channel names for runtime validation.
 */
export const VALID_CHANNELS = new Set([
  WS_CHANNELS.BOT_STATUS,
  WS_CHANNELS.SIGNAL_TRACE,
  WS_CHANNELS.EXECUTION_EVENTS,
  WS_CHANNELS.RISK_EVENTS,
  WS_CHANNELS.DEPLOYMENT_EVENTS,
  WS_CHANNELS.INFRASTRUCTURE
]);

/**
 * Event types per channel.
 */
export const EVENT_TYPES = {
  [WS_CHANNELS.BOT_STATUS]: {
    BOT_HEALTH: 'bot_health',
    BOT_CONNECTED: 'bot_connected',
    BOT_DISCONNECTED: 'bot_disconnected',
    BOT_ERROR: 'bot_error',
    HEARTBEAT: 'heartbeat'
  },
  
  [WS_CHANNELS.SIGNAL_TRACE]: {
    SIGNAL_RECEIVED: 'signal_received',
    SIGNAL_VALIDATED: 'signal_validated',
    SIGNAL_RISK_CHECKED: 'signal_risk_checked',
    SIGNAL_EXECUTED: 'signal_executed',
    SIGNAL_REJECTED: 'signal_rejected',
    SIGNAL_FAILED: 'signal_failed'
  },
  
  [WS_CHANNELS.EXECUTION_EVENTS]: {
    ORDER_SUBMITTED: 'order_submitted',
    ORDER_FILLED: 'order_filled',
    ORDER_PARTIAL: 'order_partial',
    ORDER_REJECTED: 'order_rejected',
    ORDER_ERROR: 'order_error'
  },
  
  [WS_CHANNELS.RISK_EVENTS]: {
    RISK_BLOCK: 'risk_block',
    RISK_WARNING: 'risk_warning',
    KILL_SWITCH: 'kill_switch',
    POSITION_LIMIT: 'position_limit',
    DRAWDOWN_ALERT: 'drawdown_alert'
  },
  
  [WS_CHANNELS.DEPLOYMENT_EVENTS]: {
    DEPLOY_STARTED: 'deploy_started',
    DEPLOY_SUCCESS: 'deploy_success',
    DEPLOY_FAILED: 'deploy_failed',
    BOT_STARTED: 'bot_started',
    BOT_STOPPED: 'bot_stopped'
  }
};

/**
 * Validate a channel name.
 * 
 * @param {string} channel - Channel to validate
 * @returns {boolean} True if valid
 */
export function isValidChannel(channel) {
  return VALID_CHANNELS.has(channel);
}

/**
 * Assert channel is valid, throw if not.
 * 
 * @param {string} channel - Channel to validate
 * @param {string} context - Context for error message
 * @throws {Error} If channel is invalid
 */
export function assertValidChannel(channel, context = 'subscription') {
  if (!isValidChannel(channel)) {
    console.error(`[WebSocket] Unknown websocket channel ${context}: "${channel}"`);
    console.error(`[WebSocket] Valid channels: ${Array.from(VALID_CHANNELS).join(', ')}`);
    throw new Error(`Invalid WebSocket channel: "${channel}"`);
  }
}

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * THE PARAMETERISED, OWNER-SCOPED CHANNELS
 * strategy-builder task 8.5. Requirements 20.12, 21.5, 21.6, 23.1-23.6.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Mirrors `backend_app/backend/ws_channels.py`'s `OWNED_CHANNEL_FAMILIES`, the way the
 * rest of this file mirrors `ChannelType`.
 *
 * These are NOT added to `WS_CHANNELS` / `VALID_CHANNELS`, for the same reason the
 * backend keeps them out of `ChannelType`: that vocabulary is a closed set of fixed
 * names, `assertValidChannel` throws on anything outside it, and there is one of these
 * channels per resource. A parameterised name in a fixed-name set would make
 * `createWSMessage` reject every frame the builder receives.
 *
 * Authorisation is not here and cannot be: the browser does not know who owns a
 * strategy. The server resolves the owner and may answer with a
 * `subscription_refused` frame, which `websocketClient` hands to whoever asked for the
 * channel. A well-formed name is not an authorised one.
 */

/** What an id may look like inside a channel name — the backend's rule, verbatim. */
const RESOURCE_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * The six families. `resource` is the field name the server puts the id under in every
 * frame, so a reader takes it from a named field rather than by re-parsing the channel.
 *
 * `SIGNAL` is trading-lifecycle-integration task 14.1's `SIGNAL_FAMILY`, transcribed:
 * `OwnedChannelFamily(namespace="signal", resource="deployment_id")`. It is scoped to a
 * deployment rather than to a strategy or a user for the reason `design.md` gives — a
 * deployment already resolves to exactly one owner, so
 * `core/websocket_auth.authorize_channel_subscription`'s existing `deployment_id`
 * ownership lookup answers it with no new owner-resolution path. A page filtered by
 * strategy therefore holds one of these per deployment rather than one channel for the
 * strategy.
 */
export const OWNED_CHANNELS = Object.freeze({
  TRAINING: Object.freeze({ namespace: 'training', resource: 'job_id' }),
  BUILDER_VALIDATION: Object.freeze({ namespace: 'builder.validation', resource: 'strategy_id' }),
  STRATEGY: Object.freeze({ namespace: 'strategy', resource: 'strategy_id' }),
  DEPLOYMENT: Object.freeze({ namespace: 'deployment', resource: 'deployment_id' }),
  EXECUTION: Object.freeze({ namespace: 'execution', resource: 'deployment_id' }),
  SIGNAL: Object.freeze({ namespace: 'signal', resource: 'deployment_id' }),
});

/** Longest namespace first, so `builder.validation` is never split at its first dot. */
const OWNED_FAMILY_LIST = Object.freeze(
  Object.values(OWNED_CHANNELS)
    .slice()
    .sort((a, b) => b.namespace.length - a.namespace.length),
);

/**
 * `{namespace}.{resourceId}`, or null when `resourceId` is not a routable id.
 *
 * Null rather than a throw: a caller composing a channel for a strategy that has not
 * been saved yet has no id, and that is a normal state of the canvas rather than an
 * error. A thrown exception there would have to be caught at every call site.
 *
 * @param {{namespace: string}} family
 * @param {string} resourceId
 * @returns {string|null}
 */
export function ownedChannel(family, resourceId) {
  if (!family || typeof family.namespace !== 'string') return null;
  const id = typeof resourceId === 'string' ? resourceId.trim() : '';
  if (!RESOURCE_ID_PATTERN.test(id)) return null;
  return `${family.namespace}.${id}`;
}

/**
 * Decompose a channel name into `{ family, resourceId }`, or null.
 * @param {string} channel
 * @returns {{family: Object, resourceId: string}|null}
 */
export function parseOwnedChannel(channel) {
  if (typeof channel !== 'string') return null;
  for (const family of OWNED_FAMILY_LIST) {
    const prefix = `${family.namespace}.`;
    if (!channel.startsWith(prefix)) continue;
    const resourceId = channel.slice(prefix.length);
    if (!RESOURCE_ID_PATTERN.test(resourceId)) return null;
    return { family, resourceId };
  }
  return null;
}

/** True when `channel` is a well-formed owner-scoped channel name. */
export function isOwnedChannel(channel) {
  return parseOwnedChannel(channel) !== null;
}

/**
 * The frame types each family carries — the backend's `*_CHANNEL_EVENTS`, verbatim.
 *
 * Listed so a reader is a translation table rather than a `switch` on strings it hopes
 * the server sends. A frame type absent from here is one this build does not know how
 * to render, which is a different thing from a frame that says nothing is happening.
 */
export const OWNED_CHANNEL_EVENTS = Object.freeze({
  VALIDATION_REPORT: 'validation.report',
  VALIDATION_FAILED: 'validation.failed',
  STRATEGY_LIFECYCLE: 'strategy.lifecycle',
  STRATEGY_CANVAS_STATE: 'strategy.canvas_state',
  DEPLOYMENT_STATE: 'deployment.state',
  DEPLOYMENT_GUARD_TRIP: 'deployment.guard_trip',
  DEPLOYMENT_RUNTIME_STATE: 'deployment.runtime_state',
  EXECUTION_INTENT: 'execution.intent',
  EXECUTION_ORDER: 'execution.order',
  EXECUTION_FILL: 'execution.fill',
  EXECUTION_REJECTION: 'execution.rejection',
  TRAINING_QUEUED: 'training.queued',
  TRAINING_PROGRESS: 'training.progress',
  TRAINING_COMPLETED: 'training.completed',
  TRAINING_FAILED: 'training.failed',
  TRAINING_CANCELLED: 'training.cancelled',
  TRAINING_CANCEL_REQUESTED: 'training.cancel_requested',
  // trading-lifecycle-integration task 14.1 — `ws_channels.SignalEvent`, verbatim.
  // Three, and no fourth: a failure/rejection/cancellation IS an
  // `order_lifecycle_state`, so it travels as SIGNAL_STATUS_CHANGED carrying that
  // state rather than as a frame type of its own.
  SIGNAL_GENERATED: 'signal.generated',
  SIGNAL_STATUS_CHANGED: 'signal.status_changed',
  SIGNAL_SNAPSHOT: 'signal.snapshot',
});

/** The frame the server sends when it refuses a subscription (Requirement 21.6). */
export const SUBSCRIPTION_REFUSED = 'subscription_refused';

/**
 * Standard WebSocket message schema.
 */
export const WS_MESSAGE_SCHEMA = {
  type: 'string',           // Event type
  channel: 'string',        // Channel name
  timestamp: 'string',      // ISO 8601 timestamp
  bot_id: 'string|null',    // Associated bot ID
  strategy_id: 'string|null', // Associated strategy ID
  tenant_id: 'string',      // Tenant identifier
  message_id: 'string',     // Unique message ID
  payload: 'object'         // Event-specific data
};

/**
 * Create a standard WebSocket message.
 * 
 * @param {Object} params - Message parameters
 * @returns {Object} Standardized message
 */
export function createWSMessage({
  type,
  channel,
  bot_id = null,
  strategy_id = null,
  tenant_id = 'default',
  payload = {}
}) {
  assertValidChannel(channel, 'in createWSMessage');
  
  return {
    type,
    channel,
    timestamp: new Date().toISOString(),
    bot_id,
    strategy_id,
    tenant_id,
    // Use CSPRNG for message IDs — Math.random() is not cryptographically secure
    message_id: crypto.randomUUID(),
    payload
  };
}

export default WS_CHANNELS;
