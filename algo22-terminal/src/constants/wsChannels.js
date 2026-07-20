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
