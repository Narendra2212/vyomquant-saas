/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CENTRALIZED WEBSOCKET CLIENT
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Provides a centralized WebSocket client with built-in:
 * - Automatic reconnection
 * - Authentication token injection
 * - Event subscription management
 * - Type-safe message handling
 * - Connection status tracking
 *
 * ═══════════════════════════════════════════════════════════════════════════
 */

import { CONFIG } from './config';
const WS_BASE = CONFIG.wsBaseUrl;

class WebSocketClient {
  constructor() {
    this.ws = null;
    this.url = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 1000;
    this.subscriptions = new Map(); // event_type -> Set of callbacks
    this.connectionStatus = 'disconnected';
    this.messageQueue = []; // Queue messages while disconnected
    this.reconnectEnabled = true; // Flag to control reconnection
    this.reconnectTimeoutId = null; // Track reconnect timeout for cleanup
    this.heartbeatInterval = null; // Heartbeat timer
    this.heartbeatTimeout = null; // Heartbeat response timeout
    this.lastPongTime = Date.now(); // Track last pong received
    this.heartbeatIntervalMs = 30000; // 30 seconds heartbeat
    this.heartbeatTimeoutMs = 10000; // 10 seconds to expect pong
    this.maxReconnectDelay = 30000; // Max 30 seconds between reconnects
    
    // 🔴 STEP 4: WebSocket reliability with sequence numbers
    this.lastSequenceNumber = 0; // Last received sequence number
    this.missedMessages = []; // Track missed messages for replay
    this.expectedSequence = 1; // Expected next sequence number
    this.messageBuffer = new Map(); // Buffer out-of-order messages
    this.MAX_BUFFER_SIZE = 100; // Max messages to buffer
  }

  /**
   * Connect to WebSocket server
   * @param {string} path - WebSocket endpoint path (default: /ws/telemetry)
   */
  connect(path = '/ws/telemetry') {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log('WebSocket already connected');
      return;
    }

    const token = sessionStorage.getItem('token');
    const hasQuery = path.includes('?');
    const tokenParam = token ? `${hasQuery ? '&' : '?'}token=${encodeURIComponent(token)}` : '';
    this.url = `${WS_BASE}${path}${tokenParam}`;
    console.log(`Connecting to WebSocket: ${this.url.replace(/token=[^&]+/, 'token=[REDACTED]')}`);

    try {
      this.ws = new WebSocket(this.url);
      this.connectionStatus = 'connecting';

      this.ws.onopen = () => this.handleOpen();
      this.ws.onmessage = (event) => this.handleMessage(event);
      this.ws.onerror = (error) => this.handleError(error);
      this.ws.onclose = () => this.handleClose();
    } catch (error) {
      console.error('WebSocket connection error:', error);
      this.connectionStatus = 'error';
      // Clean up failed connection attempt
      if (this.ws) {
        try {
          this.ws.close();
        } catch (e) {
          // Ignore close errors
        }
        this.ws = null;
      }
      this.scheduleReconnect();
    }
  }

  /**
   * Handle WebSocket open event
   */
  handleOpen() {
    console.log('🟢 WebSocket connected');
    this.connectionStatus = 'connected';
    this.reconnectAttempts = 0;
    this.lastPongTime = Date.now();

    // Clear any pending reconnect timeout
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    // 🔴 STEP 4: Request message replay on reconnect to catch missed updates
    if (this.expectedSequence > 1) {
      this.requestMessageReplay(this.expectedSequence);
    }

    // Authenticate if token exists
    const token = sessionStorage.getItem('token');
    if (token && token !== 'dev_bypass') {
      this.send({ action: 'auth', token });
    }

    // Send queued messages
    this.flushMessageQueue();

    // Start heartbeat
    this.startHeartbeat();
  }

  /**
   * Handle incoming WebSocket message
   * @param {MessageEvent} event 
   */
  handleMessage(event) {
    try {
      const message = JSON.parse(event.data);
      
      // 🔴 STEP 4: Message validation - reject invalid messages
      if (!message || typeof message !== 'object') {
        console.error('🔴 WebSocket: Invalid message format (not an object)');
        return;
      }
      
      if (!message.type && !message.event_type) {
        console.error('🔴 WebSocket: Invalid message - missing type/event_type');
        return;
      }
      
      const eventType = message.type || message.event_type;

      // Handle pong response
      if (eventType === 'pong' || message.action === 'pong') {
        this.lastPongTime = Date.now();
        clearTimeout(this.heartbeatTimeout);
        this.heartbeatTimeout = null;
        return;
      }

      // 🔴 STEP 4: Sequence number validation for ordered delivery
      if (message.seq !== undefined) {
        const msgSeq = message.seq;
        
        if (msgSeq < this.expectedSequence) {
          // Duplicate or old message - ignore
          console.warn(`WebSocket: Out-of-order message (seq ${msgSeq}, expected ${this.expectedSequence})`);
          return;
        }
        
        if (msgSeq > this.expectedSequence) {
          // Gap detected - buffer and request replay
          console.error(`🔴 WebSocket: Message gap detected! Missing ${this.expectedSequence} to ${msgSeq - 1}`);
          this.messageBuffer.set(msgSeq, { message, eventType });
          this.requestMessageReplay(this.expectedSequence);
          return;
        }
        
        // Correct sequence - process and check buffer
        this.expectedSequence = msgSeq + 1;
        this.processMessage(message, eventType);
        
        // Process any buffered messages that are now in order
        this.processBufferedMessages();
      } else {
        // No sequence number - process normally (backward compatibility)
        this.processMessage(message, eventType);
      }

      // Log all messages for debugging (except frequent heartbeats)
      if (eventType !== 'ping' && eventType !== 'pong') {
        console.log('📨 WebSocket message:', eventType, message);
      }
    } catch (error) {
      console.error('WebSocket message parse error:', error, 'Raw data:', event.data?.substring(0, 200));
    }
  }

  /**
   * 🔴 STEP 4: Process validated message
   */
  processMessage(message, eventType) {
    // Route message to subscribed callbacks
    if (eventType && this.subscriptions.has(eventType)) {
      const callbacks = this.subscriptions.get(eventType);
      callbacks.forEach(callback => {
        try {
          callback(message);
        } catch (error) {
          console.error(`Error in callback for ${eventType}:`, error);
        }
      });
    }
  }

  /**
   * 🔴 STEP 4: Process buffered messages that are now in order
   */
  processBufferedMessages() {
    while (this.messageBuffer.has(this.expectedSequence)) {
      const { message, eventType } = this.messageBuffer.get(this.expectedSequence);
      this.messageBuffer.delete(this.expectedSequence);
      this.processMessage(message, eventType);
      this.expectedSequence++;
    }
    
    // Prevent buffer from growing too large
    if (this.messageBuffer.size > this.MAX_BUFFER_SIZE) {
      console.error('� WebSocket: Message buffer overflow - clearing old messages');
      const oldestSeq = Math.min(...this.messageBuffer.keys());
      this.messageBuffer.delete(oldestSeq);
    }
  }

  /**
   * 🔴 STEP 4: Request replay of missed messages
   */
  requestMessageReplay(fromSequence) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.send({
        action: 'replay',
        from_sequence: fromSequence
      });
      console.log(`📨 WebSocket: Requesting message replay from sequence ${fromSequence}`);
    }
  }

  /**
   * Handle WebSocket error
   * @param {Event} error 
   */
  handleError(error) {
    console.error('WebSocket error:', error);
    this.connectionStatus = 'error';
    // Error often precedes close, but trigger reconnect if not already closing
    if (this.ws && this.ws.readyState !== WebSocket.CLOSING && this.ws.readyState !== WebSocket.CLOSED) {
      console.log('Forcing reconnect due to WebSocket error');
      this.ws.close();
    }
  }

  /**
   * Handle WebSocket close
   */
  handleClose(event) {
    console.log(`🔴 WebSocket disconnected (code: ${event?.code || 'unknown'}, reason: ${event?.reason || 'none'})`);
    this.connectionStatus = 'disconnected';
    this.stopHeartbeat();
    this.ws = null;

    // Attempt reconnection if enabled
    if (this.reconnectEnabled) {
      this.scheduleReconnect();
    }
  }

  /**
   * Schedule reconnection attempt
   */
  scheduleReconnect() {
    if (!this.reconnectEnabled) {
      console.log('Reconnection disabled, skipping reconnect');
      return;
    }

    // Clear any existing reconnect timeout
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(`Max reconnection attempts (${this.maxReconnectAttempts}) reached. Giving up.`);
      this.connectionStatus = 'failed';
      return;
    }

    this.reconnectAttempts++;
    // Exponential backoff with max delay cap
    const delay = Math.min(
      this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1),
      this.maxReconnectDelay
    );

    console.log(`⏱️ Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

    this.reconnectTimeoutId = setTimeout(() => {
      this.reconnectTimeoutId = null;
      // Extract path from stored URL
      const path = this.url ? new URL(this.url).pathname : '/ws/telemetry';
      this.connect(path);
    }, delay);
  }

  /**
   * Start heartbeat/ping-pong to detect stale connections
   */
  startHeartbeat() {
    this.stopHeartbeat(); // Clear any existing heartbeat
    
    this.heartbeatInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // Check if we haven't received a pong in too long
        const timeSinceLastPong = Date.now() - this.lastPongTime;
        if (timeSinceLastPong > this.heartbeatIntervalMs + this.heartbeatTimeoutMs) {
          console.warn(`No pong received for ${timeSinceLastPong}ms, connection may be stale`);
          // Force reconnection
          this.ws.close();
          return;
        }

        // Send ping
        this.send({ action: 'ping', timestamp: Date.now() });
        
        // Set timeout for pong response
        this.heartbeatTimeout = setTimeout(() => {
          console.warn('Heartbeat pong timeout, closing connection');
          if (this.ws) {
            this.ws.close();
          }
        }, this.heartbeatTimeoutMs);
      }
    }, this.heartbeatIntervalMs);
  }

  /**
   * Stop heartbeat timers
   */
  stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
    if (this.heartbeatTimeout) {
      clearTimeout(this.heartbeatTimeout);
      this.heartbeatTimeout = null;
    }
  }

  /**
   * Send message to WebSocket server
   * @param {Object} data - Message data to send
   */
  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    } else {
      // Queue message for when connection is established
      this.messageQueue.push(data);
      console.log('Message queued (not connected)');
    }
  }

  /**
   * Flush queued messages
   */
  flushMessageQueue() {
    while (this.messageQueue.length > 0) {
      const message = this.messageQueue.shift();
      this.send(message);
    }
  }

  /**
   * Subscribe to an event type
   * @param {string} eventType - Event type to subscribe to
   * @param {Function} callback - Callback function for messages
   * @returns {Function} Unsubscribe function
   */
  subscribe(eventType, callback) {
    if (!this.subscriptions.has(eventType)) {
      this.subscriptions.set(eventType, new Set());
    }
    
    this.subscriptions.get(eventType).add(callback);
    console.log(`Subscribed to: ${eventType}`);

    // Return unsubscribe function
    return () => this.unsubscribe(eventType, callback);
  }

  /**
   * Unsubscribe from an event type
   * @param {string} eventType - Event type to unsubscribe from
   * @param {Function} callback - Callback function to remove
   */
  unsubscribe(eventType, callback) {
    if (this.subscriptions.has(eventType)) {
      const callbacks = this.subscriptions.get(eventType);
      callbacks.delete(callback);
      
      if (callbacks.size === 0) {
        this.subscriptions.delete(eventType);
        console.log(`Unsubscribed from: ${eventType}`);
      }
    }
  }

  /**
   * Disconnect WebSocket
   */
  disconnect() {
    // Disable reconnection first to prevent auto-reconnect
    this.reconnectEnabled = false;
    
    // Clear any pending reconnect
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }
    
    // Stop heartbeat
    this.stopHeartbeat();
    
    // Close WebSocket
    if (this.ws) {
      try {
        this.ws.close();
      } catch (e) {
        // Ignore close errors
      }
      this.ws = null;
    }
    
    this.connectionStatus = 'disconnected';
    this.subscriptions.clear();
    this.messageQueue = [];
    this.reconnectAttempts = 0;
    
    console.log('🔌 WebSocket disconnected cleanly');
  }

  /**
   * Disable reconnection attempts
   */
  disableReconnect() {
    this.reconnectEnabled = false;
  }

  /**
   * Enable reconnection attempts
   */
  enableReconnect() {
    this.reconnectEnabled = true;
  }

  /**
   * Get current connection status
   * @returns {string} Connection status
   */
  getStatus() {
    return this.connectionStatus;
  }

  /**
   * Check if connected
   * @returns {boolean}
   */
  isConnected() {
    return this.connectionStatus === 'connected';
  }
}

// Create singleton instance
const wsClient = new WebSocketClient();

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CONVENIENCE SUBSCRIPTION METHODS
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * 🔴 STEP 8: Subscribe to order updates (matches backend event type)
 * @param {Function} callback - Callback function for order updates
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribeOrders = (callback) => {
  return wsClient.subscribe('orders', callback);
};

/**
 * 🔴 STEP 8: Subscribe to PnL updates (matches backend event type)
 * @param {Function} callback - Callback function for PnL updates
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribePnL = (callback) => {
  return wsClient.subscribe('pnl', callback);
};

/**
 * 🔴 STEP 8: Subscribe to position updates (matches backend event type)
 * @param {Function} callback - Callback function for position updates
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribePositions = (callback) => {
  return wsClient.subscribe('positions', callback);
};

/**
 * Subscribe to strategy status events
 * @param {Function} callback - Callback function for strategy status changes
 * @returns {Function} Unsubscribe function
 */
wsClient.subscribeStrategyStatus = (callback) => {
  return wsClient.subscribe('STRATEGY_STATUS', callback);
};

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * TELEMETRY ADAPTER LAYER
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * Adapts strict backend WebSocket telemetry payloads into the flat format expected by the UI.
 * @param {Object} event - The raw WebSocket event
 * @returns {Object} The flattened event
 */
export function normalizeTelemetryEvent(event) {
  if (!event || !event.payload) return event;
  
  const payload = event.payload;
  const normalized = {
    ...event,
    ...payload, // Flatten payload keys into root
    original_payload: payload // Preserve original
  };
  
  // Specific key mappings requested by the dashboard
  if (payload.signal !== undefined) normalized.signal_type = payload.signal;
  if (payload.uptime !== undefined) normalized.uptime_seconds = payload.uptime;
  
  return normalized;
}

export default wsClient;
