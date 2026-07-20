/**
 * WebSocket Safety Infrastructure
 * Principal Institutional WebSocket Resilience Engineer
 * 
 * Enforces deterministic reconnect, exponential backoff, stale socket cleanup,
 * replay-safe resync, message order validation, tenant revalidation,
 * session revalidation, reconnect storm protection, and safe websocket shutdown.
 */

// ══════════════════════════════════════════════════════════════════════════
//  DETERMINISTIC RECONNECT
// ══════════════════════════════════════════════════════════════════════════

class DeterministicReconnect {
  /**
   * Calculate deterministic reconnect delay with jitter
   * Prevents thundering herd by adding controlled randomness
   */
  static calculateDelay(attempt, baseDelay = 1000, maxDelay = 30000) {
    // Exponential backoff
    const exponentialDelay = baseDelay * Math.pow(2, attempt - 1);
    
    // Cap at max delay
    const cappedDelay = Math.min(exponentialDelay, maxDelay);
    
    // Add jitter for deterministic timing (±25%)
    const jitter = cappedDelay * 0.25;
    const randomJitter = (Math.random() - 0.5) * 2 * jitter;
    
    return Math.max(cappedDelay + randomJitter, baseDelay);
  }
  
  /**
   * Validate reconnect attempt
   * Ensures reconnect is within acceptable bounds
   */
  static validateReconnect(attempt, maxAttempts, lastReconnectTime) {
    if (attempt > maxAttempts) {
      return { valid: false, reason: 'max_attempts_exceeded' };
    }
    
    // Check minimum time between reconnects (rate limiting)
    const minReconnectInterval = 1000; // 1 second minimum
    const timeSinceLastReconnect = Date.now() - lastReconnectTime;
    
    if (timeSinceLastReconnect < minReconnectInterval) {
      return { valid: false, reason: 'rate_limited' };
    }
    
    return { valid: true };
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  EXPONENTIAL BACKOFF
// ══════════════════════════════════════════════════════════════════════════

class ExponentialBackoff {
  constructor(options = {}) {
    this.initialDelay = options.initialDelay || 1000;
    this.maxDelay = options.maxDelay || 30000;
    this.multiplier = options.multiplier || 2;
    this.jitter = options.jitter !== undefined ? options.jitter : true;
    this.maxAttempts = options.maxAttempts || 10;
    this.currentAttempt = 0;
    this.lastDelay = 0;
  }
  
  /**
   * Get next backoff delay
   */
  getNextDelay() {
    this.currentAttempt++;
    
    if (this.currentAttempt > this.maxAttempts) {
      return null; // Give up
    }
    
    let delay = this.initialDelay * Math.pow(this.multiplier, this.currentAttempt - 1);
    delay = Math.min(delay, this.maxDelay);
    
    if (this.jitter) {
      const jitterAmount = delay * 0.25;
      const randomJitter = (Math.random() - 0.5) * 2 * jitterAmount;
      delay += randomJitter;
    }
    
    this.lastDelay = delay;
    return delay;
  }
  
  /**
   * Reset backoff state
   */
  reset() {
    this.currentAttempt = 0;
    this.lastDelay = 0;
  }
  
  /**
   * Get current attempt count
   */
  getCurrentAttempt() {
    return this.currentAttempt;
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  STALE SOCKET CLEANUP
// ══════════════════════════════════════════════════════════════════════════

class StaleSocketCleanup {
  constructor() {
    this.staleSockets = new Map(); // socketId -> { socket, timestamp, cleanupAttempts }
    this.cleanupInterval = 60000; // 1 minute
    this.maxCleanupAttempts = 3;
    this.cleanupTimer = null;
  }
  
  /**
   * Register socket for cleanup tracking
   */
  registerSocket(socketId, socket) {
    this.staleSockets.set(socketId, {
      socket,
      timestamp: Date.now(),
      cleanupAttempts: 0
    });
  }
  
  /**
   * Mark socket as stale
   */
  markStale(socketId) {
    if (this.staleSockets.has(socketId)) {
      const socketData = this.staleSockets.get(socketId);
      socketData.stale = true;
      socketData.staleTimestamp = Date.now();
    }
  }
  
  /**
   * Attempt to cleanup stale socket
   */
  cleanupSocket(socketId) {
    if (!this.staleSockets.has(socketId)) {
      return { success: false, reason: 'not_found' };
    }
    
    const socketData = this.staleSockets.get(socketId);
    
    if (socketData.cleanupAttempts >= this.maxCleanupAttempts) {
      // Give up on this socket
      this.staleSockets.delete(socketId);
      return { success: false, reason: 'max_attempts_exceeded' };
    }
    
    try {
      if (socketData.socket && socketData.socket.readyState !== WebSocket.CLOSED) {
        socketData.socket.close();
      }
      this.staleSockets.delete(socketId);
      return { success: true };
    } catch (error) {
      socketData.cleanupAttempts++;
      return { success: false, reason: 'cleanup_error', error };
    }
  }
  
  /**
   * Start periodic cleanup
   */
  startPeriodicCleanup() {
    if (this.cleanupTimer) {
      return;
    }
    
    this.cleanupTimer = setInterval(() => {
      this.cleanupAllStale();
    }, this.cleanupInterval);
  }
  
  /**
   * Stop periodic cleanup
   */
  stopPeriodicCleanup() {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }
  }
  
  /**
   * Cleanup all stale sockets
   */
  cleanupAllStale() {
    const staleThreshold = 300000; // 5 minutes
    
    for (const [socketId, socketData] of this.staleSockets.entries()) {
      const age = Date.now() - socketData.timestamp;
      
      if (socketData.stale || age > staleThreshold) {
        this.cleanupSocket(socketId);
      }
    }
  }
  
  /**
   * Get stale socket count
   */
  getStaleCount() {
    return this.staleSockets.size;
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  REPLAY-SAFE RESYNC
// ══════════════════════════════════════════════════════════════════════════

class ReplaySafeResync {
  constructor(options = {}) {
    this.replayTimeout = options.replayTimeout || 10000; // 10 seconds
    this.maxReplayAttempts = options.maxReplayAttempts || 3;
    this.replayRequests = new Map(); // requestId -> { timestamp, attempts, resolve, reject }
  }
  
  /**
   * Request message replay with timeout
   */
  requestReplay(fromSequence, socket) {
    return new Promise((resolve, reject) => {
      // Use CSPRNG for replay request IDs (security-sensitive correlation token)
      const requestId = `replay_${crypto.randomUUID()}`;
      
      this.replayRequests.set(requestId, {
        timestamp: Date.now(),
        attempts: 0,
        resolve,
        reject
      });
      
      // Send replay request
      socket.send(JSON.stringify({
        action: 'replay',
        from_sequence: fromSequence,
        request_id: requestId
      }));
      
      // Set timeout
      const timeout = setTimeout(() => {
        this.handleReplayTimeout(requestId);
      }, this.replayTimeout);
      
      this.replayRequests.get(requestId).timeout = timeout;
    });
  }
  
  /**
   * Handle replay response
   */
  handleReplayResponse(requestId, messages) {
    const request = this.replayRequests.get(requestId);
    
    if (!request) {
      return; // Unknown request
    }
    
    clearTimeout(request.timeout);
    this.replayRequests.delete(requestId);
    request.resolve(messages);
  }
  
  /**
   * Handle replay timeout
   */
  handleReplayTimeout(requestId) {
    const request = this.replayRequests.get(requestId);
    
    if (!request) {
      return;
    }
    
    request.attempts++;
    
    if (request.attempts >= this.maxReplayAttempts) {
      this.replayRequests.delete(requestId);
      request.reject(new Error('Replay timeout exceeded'));
    } else {
      // Retry
      clearTimeout(request.timeout);
      request.timeout = setTimeout(() => {
        this.handleReplayTimeout(requestId);
      }, this.replayTimeout);
    }
  }
  
  /**
   * Cancel pending replay request
   */
  cancelReplay(requestId) {
    const request = this.replayRequests.get(requestId);
    
    if (request) {
      clearTimeout(request.timeout);
      this.replayRequests.delete(requestId);
      request.reject(new Error('Replay cancelled'));
    }
  }
  
  /**
   * Get pending replay count
   */
  getPendingCount() {
    return this.replayRequests.size;
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  MESSAGE ORDER VALIDATION
// ══════════════════════════════════════════════════════════════════════════

class MessageOrderValidator {
  constructor(options = {}) {
    this.expectedSequence = 1;
    this.messageBuffer = new Map(); // sequence -> message
    this.maxBufferSize = options.maxBufferSize || 100;
    this.sequenceDriftThreshold = options.sequenceDriftThreshold || 1000;
    this.lastSequenceReset = Date.now();
    this.driftDetected = false;
  }
  
  /**
   * Validate message sequence
   */
  validate(message) {
    if (message.seq === undefined) {
      // No sequence number - accept
      return { valid: true, action: 'accept' };
    }
    
    const msgSeq = message.seq;
    
    // Check for sequence drift
    if (msgSeq > this.expectedSequence + this.sequenceDriftThreshold) {
      this.driftDetected = true;
      return { 
        valid: false, 
        action: 'reset', 
        reason: 'sequence_drift',
        expected: this.expectedSequence,
        received: msgSeq
      };
    }
    
    if (msgSeq < this.expectedSequence) {
      // Duplicate or old message
      return { 
        valid: false, 
        action: 'ignore', 
        reason: 'out_of_order',
        expected: this.expectedSequence,
        received: msgSeq
      };
    }
    
    if (msgSeq > this.expectedSequence) {
      // Gap detected - buffer
      if (this.messageBuffer.size >= this.maxBufferSize) {
        // Buffer overflow - request reset
        return { 
          valid: false, 
          action: 'reset', 
          reason: 'buffer_overflow',
          expected: this.expectedSequence,
          received: msgSeq
        };
      }
      
      this.messageBuffer.set(msgSeq, message);
      return { 
        valid: false, 
        action: 'buffer', 
        reason: 'gap_detected',
        expected: this.expectedSequence,
        received: msgSeq
      };
    }
    
    // Correct sequence
    return { valid: true, action: 'accept' };
  }
  
  /**
   * Advance expected sequence
   */
  advanceSequence() {
    this.expectedSequence++;
  }
  
  /**
   * Reset sequence
   */
  resetSequence() {
    this.expectedSequence = 1;
    this.messageBuffer.clear();
    this.lastSequenceReset = Date.now();
    this.driftDetected = false;
  }
  
  /**
   * Get buffered messages in order
   */
  getBufferedMessages() {
    const messages = [];
    
    while (this.messageBuffer.has(this.expectedSequence)) {
      const message = this.messageBuffer.get(this.expectedSequence);
      this.messageBuffer.delete(this.expectedSequence);
      messages.push(message);
      this.expectedSequence++;
    }
    
    return messages;
  }
  
  /**
   * Get sequence statistics
   */
  getStats() {
    return {
      expectedSequence: this.expectedSequence,
      bufferedCount: this.messageBuffer.size,
      driftDetected: this.driftDetected,
      lastReset: this.lastSequenceReset
    };
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  TENANT REVALIDATION
// ══════════════════════════════════════════════════════════════════════════

class TenantRevalidator {
  constructor() {
    this.currentTenantId = null;
    this.tenantValidationRequired = false;
  }
  
  /**
   * Set current tenant ID
   */
  setTenantId(tenantId) {
    this.currentTenantId = tenantId;
    this.tenantValidationRequired = true;
  }
  
  /**
   * Get current tenant ID
   */
  getTenantId() {
    return this.currentTenantId;
  }
  
  /**
   * Validate tenant on reconnect
   */
  validateOnReconnect(socket) {
    if (!this.currentTenantId) {
      return { valid: false, reason: 'no_tenant_id' };
    }
    
    if (!this.tenantValidationRequired) {
      return { valid: true, reason: 'validation_not_required' };
    }
    
    // Send tenant validation request
    socket.send(JSON.stringify({
      action: 'validate_tenant',
      tenant_id: this.currentTenantId
    }));
    
    return { valid: true, reason: 'validation_requested' };
  }
  
  /**
   * Handle tenant validation response
   */
  handleValidationResponse(response) {
    if (response.valid) {
      this.tenantValidationRequired = false;
      return { valid: true };
    } else {
      this.tenantValidationRequired = true;
      return { valid: false, reason: response.reason || 'validation_failed' };
    }
  }
  
  /**
   * Mark validation as required
   */
  requireValidation() {
    this.tenantValidationRequired = true;
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  SESSION REVALIDATION
// ══════════════════════════════════════════════════════════════════════════

class SessionRevalidator {
  constructor() {
    this.currentToken = null;
    this.tokenExpiry = null;
    this.validationRequired = false;
  }
  
  /**
   * Set current token
   */
  setToken(token, expiry) {
    this.currentToken = token;
    this.tokenExpiry = expiry || (Date.now() + 3600000); // Default 1 hour
    this.validationRequired = true;
  }
  
  /**
   * Get current token
   */
  getToken() {
    return this.currentToken;
  }
  
  /**
   * Check if token is expired
   */
  isTokenExpired() {
    if (!this.tokenExpiry) {
      return false;
    }
    
    return Date.now() > this.tokenExpiry;
  }
  
  /**
   * Validate session on reconnect
   */
  validateOnReconnect(socket) {
    if (!this.currentToken) {
      return { valid: false, reason: 'no_token' };
    }
    
    // Check token expiry
    if (this.isTokenExpired()) {
      return { valid: false, reason: 'token_expired' };
    }
    
    // Send session validation request
    socket.send(JSON.stringify({
      action: 'validate_session',
      token: this.currentToken
    }));
    
    return { valid: true, reason: 'validation_requested' };
  }
  
  /**
   * Handle session validation response
   */
  handleValidationResponse(response) {
    if (response.valid) {
      this.validationRequired = false;
      
      // Update token expiry if provided
      if (response.expiry) {
        this.tokenExpiry = response.expiry;
      }
      
      return { valid: true };
    } else {
      this.validationRequired = true;
      return { valid: false, reason: response.reason || 'validation_failed' };
    }
  }
  
  /**
   * Mark validation as required
   */
  requireValidation() {
    this.validationRequired = true;
  }
  
  /**
   * Refresh token
   */
  async refreshToken(refreshCallback) {
    try {
      const newToken = await refreshCallback();
      this.setToken(newToken.token, newToken.expiry);
      return { success: true };
    } catch (error) {
      return { success: false, error };
    }
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  RECONNECT STORM PROTECTION
// ══════════════════════════════════════════════════════════════════════════

class ReconnectStormProtection {
  constructor(options = {}) {
    this.maxConcurrentReconnects = options.maxConcurrentReconnects || 3;
    this.reconnectRateLimit = options.reconnectRateLimit || 10000; // 10 seconds
    this.activeReconnects = new Set();
    this.reconnectHistory = []; // Array of timestamps
    this.stormDetected = false;
    this.stormThreshold = options.stormThreshold || 5; // 5 reconnects in 10 seconds
  }
  
  /**
   * Request reconnect permission
   */
  requestReconnect() {
    // Check storm status
    if (this.stormDetected) {
      return { allowed: false, reason: 'storm_detected' };
    }
    
    // Check concurrent reconnects
    if (this.activeReconnects.size >= this.maxConcurrentReconnects) {
      return { allowed: false, reason: 'max_concurrent' };
    }
    
    // Check rate limit
    const now = Date.now();
    this.reconnectHistory = this.reconnectHistory.filter(
      timestamp => now - timestamp < this.reconnectRateLimit
    );
    
    if (this.reconnectHistory.length >= this.stormThreshold) {
      this.stormDetected = true;
      this.scheduleStormReset();
      return { allowed: false, reason: 'rate_limited' };
    }
    
    // Allow reconnect
    // Use CSPRNG for reconnect IDs (security-sensitive session token)
    const reconnectId = `reconnect_${crypto.randomUUID()}`;
    this.activeReconnects.add(reconnectId);
    this.reconnectHistory.push(now);
    
    return { allowed: true, reconnectId };
  }
  
  /**
   * Complete reconnect
   */
  completeReconnect(reconnectId) {
    this.activeReconnects.delete(reconnectId);
  }
  
  /**
   * Schedule storm reset
   */
  scheduleStormReset() {
    setTimeout(() => {
      this.stormDetected = false;
      this.reconnectHistory = [];
    }, this.reconnectRateLimit * 2);
  }
  
  /**
   * Get storm status
   */
  getStormStatus() {
    return {
      stormDetected: this.stormDetected,
      activeReconnects: this.activeReconnects.size,
      recentReconnects: this.reconnectHistory.length
    };
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  SAFE WEBSOCKET SHUTDOWN
// ══════════════════════════════════════════════════════════════════════════

class SafeWebSocketShutdown {
  constructor() {
    this.shutdownInProgress = false;
    this.shutdownCallbacks = new Set();
  }
  
  /**
   * Initiate safe shutdown
   */
  async shutdown(socket, options = {}) {
    if (this.shutdownInProgress) {
      return { success: false, reason: 'shutdown_in_progress' };
    }
    
    this.shutdownInProgress = true;
    
    try {
      // 1. Disable reconnect
      if (options.disableReconnect !== false) {
        socket.reconnectEnabled = false;
      }
      
      // 2. Clear pending reconnect
      if (socket.reconnectTimeoutId) {
        clearTimeout(socket.reconnectTimeoutId);
        socket.reconnectTimeoutId = null;
      }
      
      // 3. Stop heartbeat
      if (socket.stopHeartbeat) {
        socket.stopHeartbeat();
      }
      
      // 4. Flush message queue
      if (socket.flushMessageQueue) {
        socket.flushMessageQueue();
      }
      
      // 5. Cancel pending replays
      if (socket.cancelAllReplays) {
        socket.cancelAllReplays();
      }
      
      // 6. Close socket with code
      const closeCode = options.closeCode || 1000; // Normal closure
      const closeReason = options.closeReason || 'Safe shutdown';
      
      if (socket.ws && socket.ws.readyState === WebSocket.OPEN) {
        socket.ws.close(closeCode, closeReason);
      }
      
      // 7. Clear references
      socket.ws = null;
      socket.connectionStatus = 'disconnected';
      
      // 8. Call shutdown callbacks
      this.shutdownCallbacks.forEach(callback => {
        try {
          callback();
        } catch (error) {
          console.error('Shutdown callback error:', error);
        }
      });
      
      this.shutdownInProgress = false;
      
      return { success: true };
    } catch (error) {
      this.shutdownInProgress = false;
      return { success: false, reason: 'shutdown_error', error };
    }
  }
  
  /**
   * Register shutdown callback
   */
  onShutdown(callback) {
    this.shutdownCallbacks.add(callback);
    
    // Return unsubscribe function
    return () => {
      this.shutdownCallbacks.delete(callback);
    };
  }
  
  /**
   * Get shutdown status
   */
  getShutdownStatus() {
    return {
      shutdownInProgress: this.shutdownInProgress,
      callbackCount: this.shutdownCallbacks.size
    };
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  EXPORTS
// ══════════════════════════════════════════════════════════════════════════

export {
  DeterministicReconnect,
  ExponentialBackoff,
  StaleSocketCleanup,
  ReplaySafeResync,
  MessageOrderValidator,
  TenantRevalidator,
  SessionRevalidator,
  ReconnectStormProtection,
  SafeWebSocketShutdown
};
