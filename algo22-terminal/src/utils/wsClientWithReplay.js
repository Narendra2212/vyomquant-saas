/**
 * WebSocket Client with Reconnect Replay Support
 * 
 * Enhanced WebSocket client that:
 * - Tracks last event timestamps per channel
 * - Automatically requests replay on reconnect
 * - Maintains sequence ordering
 * - Handles reconnection with exponential backoff
 * 
 * @module utils/wsClientWithReplay
 */

import { WS_CHANNELS, isValidChannel } from '../constants/wsChannels';
import { ChannelEventDedupCache } from './eventDedupCache';

/**
 * Event tracking state per channel with strict ordering validation
 */
class ChannelEventTracker {
  constructor() {
    this.lastTimestamp = null;
    this.lastSequenceId = null;
    this.eventCount = 0;
    this.missedEvents = [];
    this.reconnectTimestamp = null;
    this.outOfOrderCount = 0;
    this.gapCount = 0;
  }

  update(timestamp, sequenceId) {
    this.lastTimestamp = timestamp;
    this.lastSequenceId = sequenceId;
    this.eventCount++;
  }

  markReconnect() {
    // Store the last known position before reconnect
    this.reconnectTimestamp = Date.now();
    this.missedEvents = [];
  }

  recordMissed(event) {
    this.missedEvents.push(event);
  }

  /**
   * Validate event sequence for strict ordering
   * Returns: { isValid, isDuplicate, isOutOfOrder, isGap, expectedSeq }
   */
  validateSequence(sequenceId) {
    if (this.lastSequenceId === null) {
      // First event, accept it
      return { isValid: true, isDuplicate: false, isOutOfOrder: false, isGap: false, expectedSeq: null };
    }

    const expectedSeq = this.lastSequenceId + 1;

    if (sequenceId === this.lastSequenceId) {
      // Exact duplicate
      return { isValid: false, isDuplicate: true, isOutOfOrder: false, isGap: false, expectedSeq };
    }

    if (sequenceId < expectedSeq) {
      // Out-of-order (stale replay)
      this.outOfOrderCount++;
      return { isValid: false, isDuplicate: false, isOutOfOrder: true, isGap: false, expectedSeq };
    }

    if (sequenceId > expectedSeq) {
      // Gap detected
      this.gapCount++;
      return { isValid: false, isDuplicate: false, isOutOfOrder: false, isGap: true, expectedSeq, gapSize: sequenceId - expectedSeq };
    }

    // Perfect sequence
    return { isValid: true, isDuplicate: false, isOutOfOrder: false, isGap: false, expectedSeq };
  }

  getStats() {
    return {
      eventCount: this.eventCount,
      lastSequenceId: this.lastSequenceId,
      outOfOrderCount: this.outOfOrderCount,
      gapCount: this.gapCount,
      missedEventCount: this.missedEvents.length
    };
  }
}

/**
 * WebSocket Client with automatic reconnect and replay
 */
export class WSClientWithReplay {
  constructor(options = {}) {
    this.url = options.url || null;
    this.token = options.token || null;
    this.tenantId = options.tenantId || 'default';
    
    // Connection state
    this.ws = null;
    this.isConnected = false;
    this.isReconnecting = false;
    this.connectionId = null;
    
    // Reconnection config
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = options.maxReconnectAttempts || 10;
    this.reconnectDelay = options.reconnectDelay || 1000;
    this.maxReconnectDelay = options.maxReconnectDelay || 30000;
    this.reconnectBackoffMultiplier = options.reconnectBackoffMultiplier || 1.5;
    
    // Event tracking
    this.channelTrackers = new Map();
    this.subscriptions = new Map(); // channel -> callback
    
    // Message handling
    this.messageQueue = [];
    this.maxQueueSize = options.maxQueueSize || 1000;
    
    // Replay config
    this.replayWindowMs = options.replayWindowMs || 300000; // 5 minutes
    this.maxReplayEvents = options.maxReplayEvents || 500;
    
    // Event handlers
    this.onConnectCallback = null;
    this.onDisconnectCallback = null;
    this.onErrorCallback = null;
    this.onReplayCompleteCallback = null;
    
    // Ping/pong
    this.pingInterval = null;
    this.lastPongReceived = null;
    this.heartbeatTimeoutMs = options.heartbeatTimeoutMs || 60000;
    
    // Deduplication cache
    this.dedupCache = new ChannelEventDedupCache({
      maxSizePerChannel: 1000,
      defaultTTL: 300000 // 5 minutes
    });
    
    // Initialize trackers for all channels
    Object.values(WS_CHANNELS).forEach(channel => {
      if (typeof channel === 'string') {
        this.channelTrackers.set(channel, new ChannelEventTracker());
      }
    });
  }

  /**
   * Connect to WebSocket server
   */
  connect(url, token) {
    if (url) this.url = url;
    if (token) this.token = token;
    
    if (!this.url) {
      throw new Error('WebSocket URL not provided');
    }
    
    if (this.ws?.readyState === WebSocket.OPEN) {
      console.warn('[WSClient] Already connected');
      return Promise.resolve();
    }
    
    return new Promise((resolve, reject) => {
      try {
        // Build URL with auth token
        const wsUrl = new URL(this.url);
        if (this.token) {
          wsUrl.searchParams.set('token', this.token);
        }
        
        this.ws = new WebSocket(wsUrl.toString());
        
        this.ws.onopen = () => {
          console.log('[WSClient] Connected');
          this.isConnected = true;
          this.isReconnecting = false;
          this.reconnectAttempts = 0;
          this.lastPongReceived = Date.now();
          
          // Start heartbeat
          this._startHeartbeat();
          
          // Flush message queue
          this._flushQueue();
          
          // Request replay for all subscribed channels
          this._requestReplayForAllChannels();
          
          if (this.onConnectCallback) {
            this.onConnectCallback();
          }
          
          resolve();
        };
        
        this.ws.onmessage = (event) => {
          this._handleMessage(event.data);
        };
        
        this.ws.onclose = (event) => {
          console.log(`[WSClient] Disconnected: ${event.code} ${event.reason}`);
          this.isConnected = false;
          this._stopHeartbeat();
          
          if (this.onDisconnectCallback) {
            this.onDisconnectCallback(event);
          }
          
          // Auto-reconnect if not intentional close
          if (!event.wasClean && this.reconnectAttempts < this.maxReconnectAttempts) {
            this._scheduleReconnect();
          }
        };
        
        this.ws.onerror = (error) => {
          console.error('[WSClient] WebSocket error:', error);
          
          if (this.onErrorCallback) {
            this.onErrorCallback(error);
          }
          
          reject(error);
        };
        
      } catch (error) {
        reject(error);
      }
    });
  }

  /**
   * Disconnect from WebSocket server
   */
  disconnect() {
    this._stopHeartbeat();
    
    if (this.ws) {
      // Clear reconnect timeout if any
      if (this.reconnectTimeout) {
        clearTimeout(this.reconnectTimeout);
        this.reconnectTimeout = null;
      }
      
      // Close connection
      if (this.ws.readyState === WebSocket.OPEN) {
        this.ws.close(1000, 'Client disconnect');
      }
      
      this.ws = null;
      this.isConnected = false;
    }
  }

  /**
   * Subscribe to a channel
   */
  subscribe(channel, callback) {
    // Validate channel
    if (!isValidChannel(channel)) {
      console.error(`[WSClient] Invalid channel: ${channel}`);
      throw new Error(`Invalid WebSocket channel: ${channel}`);
    }
    
    // Store subscription
    if (!this.subscriptions.has(channel)) {
      this.subscriptions.set(channel, new Set());
    }
    this.subscriptions.get(channel).add(callback);
    
    // Send subscribe message if connected
    if (this.isConnected) {
      this._send({
        type: 'subscribe',
        channel: channel
      });
    }
    
    // Return unsubscribe function
    return () => {
      this.unsubscribe(channel, callback);
    };
  }

  /**
   * Unsubscribe from a channel
   */
  unsubscribe(channel, callback) {
    const callbacks = this.subscriptions.get(channel);
    if (callbacks) {
      callbacks.delete(callback);
      
      // Send unsubscribe if no more callbacks
      if (callbacks.size === 0 && this.isConnected) {
        this._send({
          type: 'unsubscribe',
          channel: channel
        });
      }
    }
  }

  /**
   * Request replay for a specific channel
   * 
   * @param {string} channel - Channel to replay
   * @param {string} sinceTimestamp - ISO timestamp to replay from
   * @param {number} sinceSequenceId - Sequence ID to replay from (for gap recovery)
   */
  requestReplay(channel, sinceTimestamp = null, sinceSequenceId = null) {
    if (!isValidChannel(channel)) {
      console.error(`[WSClient] Invalid channel for replay: ${channel}`);
      return;
    }
    
    const replayRequest = {
      type: 'replay_request',
      channel: channel
    };
    
    // Prefer sequence-based replay for strict ordering
    if (sinceSequenceId !== null && sinceSequenceId !== undefined) {
      replayRequest.since_sequence_id = sinceSequenceId;
    } else if (sinceTimestamp) {
      replayRequest.since = sinceTimestamp;
    } else {
      // Get last known position
      const tracker = this.channelTrackers.get(channel);
      if (tracker?.lastSequenceId !== null && tracker?.lastSequenceId !== undefined) {
        // Use sequence-based replay if we have sequence tracking
        replayRequest.since_sequence_id = tracker.lastSequenceId;
      } else if (tracker?.lastTimestamp) {
        // Fallback to timestamp-based replay
        replayRequest.since = tracker.lastTimestamp;
      } else {
        // Default to 5 minutes ago
        replayRequest.since = new Date(Date.now() - this.replayWindowMs).toISOString();
      }
    }
    
    this._send(replayRequest);
    
    console.log(`[WSClient] Requested replay for ${channel}`, 
      replayRequest.since_sequence_id !== undefined 
        ? `since sequence ${replayRequest.since_sequence_id}`
        : `since ${replayRequest.since}`
    );
  }

  /**
   * Get tracker state for a channel
   */
  getChannelState(channel) {
    return this.channelTrackers.get(channel);
  }

  /**
   * Set connection event handlers
   */
  onConnect(callback) {
    this.onConnectCallback = callback;
  }

  onDisconnect(callback) {
    this.onDisconnectCallback = callback;
  }

  onError(callback) {
    this.onErrorCallback = callback;
  }

  onReplayComplete(callback) {
    this.onReplayCompleteCallback = callback;
  }

  // =========================================================================
  // Private Methods
  // =========================================================================

  /**
   * Handle incoming WebSocket message
   */
  _handleMessage(data) {
    try {
      const message = JSON.parse(data);
      
      // Handle different message types
      switch (message.type) {
        case 'pong':
          this.lastPongReceived = Date.now();
          break;
          
        case 'subscribed':
          console.log(`[WSClient] Subscribed to ${message.channel}`);
          break;
          
        case 'unsubscribed':
          console.log(`[WSClient] Unsubscribed from ${message.channel}`);
          break;
          
        case 'replay_complete':
          this._handleReplayComplete(message);
          break;
          
        case 'error':
          console.error(`[WSClient] Server error: ${message.message}`);
          break;
          
        default:
          // Regular event message
          this._handleEvent(message);
      }
    } catch (err) {
      console.error('[WSClient] Failed to parse message:', err);
    }
  }

  /**
   * Handle event message with deduplication and ordering validation
   */
  _handleEvent(message) {
    const { channel, timestamp, message_id, event_id, sequence_id } = message;
    
    // Deduplication check
    // Use event_id if available, fall back to message_id, then sequence_id
    const dedupId = event_id || message_id || (sequence_id ? `${channel}:${sequence_id}` : null);
    
    if (dedupId) {
      if (this.dedupCache.isDuplicate(channel, dedupId)) {
        console.log(`[WSClient] Dropping duplicate event: ${dedupId} on ${channel}`);
        return; // Drop duplicate
      }
    }
    
    // Get tracker for ordering validation
    const tracker = this.channelTrackers.get(channel);
    
    // Strict ordering validation (if sequence_id available)
    if (sequence_id !== null && sequence_id !== undefined && tracker) {
      const lastSeq = tracker.lastSequenceId;
      
      if (lastSeq !== null) {
        const expectedSeq = lastSeq + 1;
        
        if (sequence_id < expectedSeq) {
          // Out-of-order event (stale replay)
          console.warn(`[WSClient] Rejecting out-of-order event: ${sequence_id} < ${expectedSeq} on ${channel}`);
          return; // Drop stale event
        }
        
        if (sequence_id > expectedSeq) {
          // Gap detected - missing events
          const gapSize = sequence_id - expectedSeq;
          console.warn(`[WSClient] Sequence gap detected on ${channel}: expected ${expectedSeq}, got ${sequence_id} (gap: ${gapSize})`);
          
          // Record missed events
          for (let i = expectedSeq; i < sequence_id; i++) {
            tracker.recordMissed({ sequence_id: i, channel });
          }
          
          // Trigger automatic replay for missing events
          this._handleSequenceGap(channel, expectedSeq, sequence_id);
        }
      }
      
      // Update tracker with sequence_id
      tracker.update(timestamp, sequence_id);
    } else if (tracker) {
      // Fallback: update tracker without sequence validation
      tracker.update(timestamp, sequence_id || message_id);
    }
    
    // Notify subscribers
    const callbacks = this.subscriptions.get(channel);
    if (callbacks) {
      callbacks.forEach(callback => {
        try {
          callback(message);
        } catch (err) {
          console.error(`[WSClient] Subscriber error for ${channel}:`, err);
        }
      });
    }
  }
  
  /**
   * Handle sequence gap by requesting replay
   */
  _handleSequenceGap(channel, expectedSeq, receivedSeq) {
    const gapSize = receivedSeq - expectedSeq;
    
    console.log(`[WSClient] Auto-requesting replay for gap on ${channel}: missing ${gapSize} events`);
    
    // Request replay starting from expected sequence
    // Backend will replay all events since the last known sequence
    const tracker = this.channelTrackers.get(channel);
    if (tracker && tracker.lastSequenceId !== null) {
      // Request replay since the last valid sequence we have
      this.requestReplay(channel, null, tracker.lastSequenceId);
    }
  }

  /**
   * Handle replay completion
   */
  _handleReplayComplete(message) {
    const { channel, events_replayed } = message;
    
    console.log(`[WSClient] Replay complete for ${channel}: ${events_replayed} events`);
    
    if (this.onReplayCompleteCallback) {
      this.onReplayCompleteCallback(channel, events_replayed);
    }
  }

  /**
   * Request replay for all subscribed channels
   */
  _requestReplayForAllChannels() {
    this.subscriptions.forEach((callbacks, channel) => {
      if (callbacks.size > 0) {
        this.requestReplay(channel);
      }
    });
  }

  /**
   * Send message to server
   */
  _send(message) {
    if (this.isConnected && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    } else {
      // Queue message for later
      if (this.messageQueue.length < this.maxQueueSize) {
        this.messageQueue.push(message);
      } else {
        console.warn('[WSClient] Message queue full, dropping message');
      }
    }
  }

  /**
   * Flush queued messages
   */
  _flushQueue() {
    while (this.messageQueue.length > 0) {
      const message = this.messageQueue.shift();
      this._send(message);
    }
  }

  /**
   * Start heartbeat ping
   */
  _startHeartbeat() {
    this.pingInterval = setInterval(() => {
      if (this.isConnected) {
        // Check if we missed pongs
        const timeSinceLastPong = Date.now() - this.lastPongReceived;
        if (timeSinceLastPong > this.heartbeatTimeoutMs) {
          console.warn('[WSClient] Heartbeat timeout, reconnecting...');
          this.ws?.close();
          return;
        }
        
        // Send ping
        this._send({
          type: 'ping',
          timestamp: new Date().toISOString()
        });
      }
    }, 30000); // Ping every 30 seconds
  }

  /**
   * Stop heartbeat
   */
  _stopHeartbeat() {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
  }

  /**
   * Schedule reconnection with exponential backoff
   */
  _scheduleReconnect() {
    if (this.isReconnecting) return;
    
    this.isReconnecting = true;
    this.reconnectAttempts++;
    
    // Calculate delay with exponential backoff
    const delay = Math.min(
      this.reconnectDelay * Math.pow(this.reconnectBackoffMultiplier, this.reconnectAttempts - 1),
      this.maxReconnectDelay
    );
    
    console.log(`[WSClient] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    
    // Mark trackers for reconnect
    this.channelTrackers.forEach(tracker => tracker.markReconnect());
    
    this.reconnectTimeout = setTimeout(() => {
      this.connect().catch(err => {
        console.error('[WSClient] Reconnect failed:', err);
        this.isReconnecting = false;
      });
    }, delay);
  }
}

/**
 * Singleton instance for app-wide use
 */
let defaultClient = null;

export function getWSClient(url, token) {
  if (!defaultClient) {
    defaultClient = new WSClientWithReplay();
    if (url && token) {
      defaultClient.connect(url, token);
    }
  }
  return defaultClient;
}

export function resetWSClient() {
  if (defaultClient) {
    defaultClient.disconnect();
    defaultClient = null;
  }
}

export default WSClientWithReplay;
