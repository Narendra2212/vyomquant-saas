/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CENTRALIZED API CLIENT
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Provides a centralized HTTP client with built-in:
 * - Base URL configuration
 * - Authentication token injection
 * - Request/response interceptors
 * - Error handling
 * - Type-safe helper methods
 * - Request deduplication
 * - Retry logic with exponential backoff
 * - In-memory caching
 * - Request ID tracking
 *
 * ═══════════════════════════════════════════════════════════════════════════
 */

import axios from 'axios';
import * as Sentry from "@sentry/react";
import { CONFIG } from './config.js';
import { supabase } from './supabase.js';
import { resetGlobalDedupCache } from './utils/eventDedupCache.js';

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * STRUCTURED API ERROR CLASS
 * ═══════════════════════════════════════════════════════════════════════════
 */
export class ApiError extends Error {
  constructor(message, config) {
    super(message);
    this.name = 'ApiError';
    this.url = config?.url;
    this.method = config?.method?.toUpperCase();
    this.status = config?.status;
    this.statusText = config?.statusText;
    this.data = config?.data;
    this.requestId = config?.requestId;
    this.timestamp = new Date().toISOString();

    // Error categorization
    if (this.status >= 500) {
      this.category = 'SERVER_ERROR';
    } else if (this.status === 401 || this.status === 403) {
      this.category = 'AUTH_ERROR';
    } else if (this.status >= 400) {
      this.category = 'CLIENT_ERROR';
    } else if (!this.status) {
      this.category = 'NETWORK_ERROR';
    } else {
      this.category = 'UNKNOWN_ERROR';
    }
  }

  /**
   * Check if error is retryable
   */
  isRetryable() {
    return this.category === 'NETWORK_ERROR' ||
      this.category === 'SERVER_ERROR' ||
      this.status === 429; // Rate limit
  }

  /**
   * Get user-friendly error message
   * Probes the normalized APIErrorResponse fields in priority order:
   *   message (primary) → detail (backward-compat) → error (code fallback)
   */
  getUserMessage() {
    switch (this.category) {
      case 'AUTH_ERROR':
        return 'Authentication failed. Please log in again.';
      case 'NETWORK_ERROR':
        return 'Network connection failed. Please check your internet connection.';
      case 'SERVER_ERROR':
        return 'Server error occurred. Please try again later.';
      case 'CLIENT_ERROR':
        return (
          this.data?.message ||
          (typeof this.data?.detail === 'string' ? this.data.detail : null) ||
          this.data?.error ||
          'Request failed. Please check your input.'
        );
      default:
        return 'An unexpected error occurred. Please try again.';
    }
  }

  /**
   * Log error details for debugging
   */
  log() {
    console.error('═══════════════════════════════════════════════════════════');
    console.error(`❌ API ERROR: ${this.category}`);
    console.error(`   URL: ${this.method} ${this.url}`);
    console.error(`   Status: ${this.status} ${this.statusText || ''}`);
    console.error(`   Request ID: ${this.requestId || 'N/A'}`);
    console.error(`   Timestamp: ${this.timestamp}`);
    console.error(`   Message: ${this.message}`);
    if (this.data) {
      console.error(`   Response Data:`, this.data);
    }
    console.error('═══════════════════════════════════════════════════════════');
  }

  /**
   * Convert to JSON for serialization
   */
  toJSON() {
    return {
      name: this.name,
      message: this.message,
      category: this.category,
      status: this.status,
      statusText: this.statusText,
      url: this.url,
      method: this.method,
      requestId: this.requestId,
      timestamp: this.timestamp,
      data: this.data,
      userMessage: this.getUserMessage(),
      retryable: this.isRetryable()
    };
  }
}

const API_BASE = CONFIG.apiBaseUrl;
const REQUEST_TIMEOUT = CONFIG.REQUEST_TIMEOUT;
const MAX_RETRIES = 3;
const RETRY_DELAY_BASE = 1000; // 1 second base delay
const CACHE_TTL = 60000; // 60 second cache TTL
const IDEMPOTENCY_TTL = 300000; // 5 minute idempotency key TTL
const MAX_CACHE_SIZE = 100; // Max cache entries
const MAX_PENDING_REQUESTS = 50; // Max concurrent pending requests
const MAX_IDEMPOTENCY_KEYS = 100; // Max stored idempotency keys

// Circuit breaker configuration
const CIRCUIT_BREAKER_THRESHOLD = 5; // Failures before opening
const CIRCUIT_BREAKER_COOLDOWN = 10000; // 10 seconds cooldown
const MAX_ENDPOINT_METRICS = 50; // Max endpoints to track
const MAX_CIRCUIT_BREAKERS = 50; // Max circuit breakers to track
const LATENCY_WINDOW_SIZE = 50; // Rolling window for latency

/**
 * Simple in-memory cache for GET requests with size limit
 */
const cache = new Map();

/**
 * Idempotency key storage for retries with expiration
 */
const idempotencyKeys = new Map();

/**
 * Get stable session identifier from sessionStorage using crypto.randomUUID()
 */
const getSessionId = () => {
  let sessionId = sessionStorage.getItem('api_session_id');
  if (!sessionId) {
    sessionId = `sess_${crypto.randomUUID()}`;
    sessionStorage.setItem('api_session_id', sessionId);
  }
  return sessionId;
};

/**
 * Safe get pattern for cache reads - prevents race conditions
 */
const safeGet = (map, key) => {
  try {
    return map.get(key);
  } catch (error) {
    return undefined;
  }
};

/**
 * Enforce size limit on Map with LRU-style eviction
 * Promotes key to end on every access for true LRU behavior
 * Atomic operations to prevent race conditions
 */
const enforceSizeLimit = (map, maxSize, keyToPromote = null) => {
  // Atomic operation: promote and evict in single transaction
  if (keyToPromote && map.has(keyToPromote)) {
    const value = safeGet(map, keyToPromote);
    if (value !== undefined) {
      // Delete first, then set to ensure atomicity
      map.delete(keyToPromote);
      map.set(keyToPromote, value);
    }
  }

  // Atomic eviction: check and delete in single operation
  if (map.size >= maxSize) {
    const firstKey = map.keys().next().value;
    if (firstKey && firstKey !== keyToPromote) {
      map.delete(firstKey);
    }
  }
};

/**
 * Generate unique request ID using CSPRNG (crypto.randomUUID)
 * Replaces Math.random() which is not cryptographically secure.
 */
const generateRequestId = () => {
  return `req_${crypto.randomUUID()}`;
};

/**
 * Generate stable idempotency key from payload with session scoping
 */
const generateIdempotencyKey = (url, data = {}) => {
  const sessionId = getSessionId();
  const payloadStr = JSON.stringify(data);
  const hash = simpleHash(`${sessionId}_${url}_${payloadStr}`);
  return `idemp_${hash}`;
};

/**
 * Simple hash function for idempotency keys
 */
const simpleHash = (str) => {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32bit integer
  }
  return Math.abs(hash).toString(36);
};

/**
 * Normalize query params for consistent cache keys
 */
const normalizeParams = (params = {}) => {
  const normalized = {};
  const keys = Object.keys(params).sort();
  for (const key of keys) {
    normalized[key] = params[key];
  }
  return normalized;
};

/**
 * Normalize endpoint by stripping query params
 */
const normalizeEndpoint = (url) => {
  return url.split('?')[0];
};

/**
 * Get cache key from URL and normalized params
 */
const getCacheKey = (url, params = {}) => {
  const normalized = normalizeParams(params);
  const queryString = new URLSearchParams(normalized).toString();
  return `${url}${queryString ? `?${queryString}` : ''}`;
};

/**
 * Check if cache entry is valid
 */
const isCacheValid = (entry) => {
  return entry && Date.now() - entry.timestamp < CACHE_TTL;
};

/**
 * Invalidate cache entries matching pattern (safe fallback)
 */
const invalidateCache = (pattern) => {
  try {
    if (!pattern || pattern === '/' || pattern === '') {
      // Fallback: clear all cache if pattern is too broad
      cache.clear();
      return;
    }
    for (const key of cache.keys()) {
      if (key.includes(pattern)) {
        cache.delete(key);
      }
    }
  } catch (error) {
    // Safe fallback: clear all cache on error
    cache.clear();
  }
};

/**
 * Pending requests map for deduplication with size limit
 */
const pendingRequests = new Map();

/**
 * Circuit breaker state per endpoint
 */
const circuitBreakers = new Map();

/**
 * Circuit breaker states
 */
const CircuitState = {
  CLOSED: 'closed', // Normal operation
  OPEN: 'open',     // Blocking requests
  HALF_OPEN: 'half_open' // Testing if service recovered
};

/**
 * Get or create circuit breaker for endpoint with size limit
 */
const getCircuitBreaker = (endpoint) => {
  if (!circuitBreakers.has(endpoint)) {
    // Enforce size limit
    if (circuitBreakers.size >= MAX_CIRCUIT_BREAKERS) {
      const firstKey = circuitBreakers.keys().next().value;
      circuitBreakers.delete(firstKey);
    }
    circuitBreakers.set(endpoint, {
      state: CircuitState.CLOSED,
      failureCount: 0,
      lastFailureTime: null,
      nextAttemptTime: null,
      testInProgress: false
    });
  }
  return circuitBreakers.get(endpoint);
};

/**
 * Check if error should count for circuit breaker (only 5xx and 429)
 */
const shouldCountForCircuitBreaker = (error) => {
  if (error.response) {
    const status = error.response.status;
    // Count 5xx server errors and 429 rate limit
    return status >= 500 || status === 429;
  }
  // Count network errors (no response)
  if (error.request && !error.response) {
    return true;
  }
  return false;
};

/**
 * Check if circuit is open for endpoint
 * Sets testInProgress flag when allowing HALF_OPEN request
 */
const isCircuitOpen = (endpoint) => {
  const breaker = getCircuitBreaker(endpoint);

  if (breaker.state === CircuitState.CLOSED) {
    return false;
  }

  if (breaker.state === CircuitState.OPEN) {
    // Check if cooldown period has passed
    if (Date.now() >= breaker.nextAttemptTime) {
      breaker.state = CircuitState.HALF_OPEN;
      breaker.testInProgress = false;
      return false;
    }
    return true;
  }

  // HALF_OPEN state allows only one test request at a time
  if (breaker.state === CircuitState.HALF_OPEN) {
    if (breaker.testInProgress) {
      return true; // Block if test already in progress
    }
    // Allow this request and mark test as in progress
    breaker.testInProgress = true;
    return false;
  }

  return false;
};

/**
 * Record success for circuit breaker
 */
const recordCircuitSuccess = (endpoint) => {
  const breaker = getCircuitBreaker(endpoint);
  breaker.failureCount = 0;
  breaker.state = CircuitState.CLOSED;
  breaker.testInProgress = false;
};

/**
 * Record failure for circuit breaker (only if applicable)
 */
const recordCircuitFailure = (endpoint, error) => {
  if (!shouldCountForCircuitBreaker(error)) {
    return; // Don't count 4xx client errors
  }

  const breaker = getCircuitBreaker(endpoint);
  breaker.failureCount++;
  breaker.lastFailureTime = Date.now();

  if (breaker.state === CircuitState.HALF_OPEN) {
    // Test failed, go back to OPEN
    breaker.state = CircuitState.OPEN;
    breaker.nextAttemptTime = Date.now() + CIRCUIT_BREAKER_COOLDOWN;
    breaker.testInProgress = false;
  } else if (breaker.failureCount >= CIRCUIT_BREAKER_THRESHOLD) {
    // Threshold reached, open circuit
    breaker.state = CircuitState.OPEN;
    breaker.nextAttemptTime = Date.now() + CIRCUIT_BREAKER_COOLDOWN;
  }
};

/**
 * Request metrics for observability with per-endpoint tracking
 */
const metrics = {
  totalRequests: 0,
  totalFailures: 0,
  totalRetries: 0,
  cacheHits: 0,
  cacheMisses: 0,
  endpoints: new Map() // { endpoint: { requests, failures, latencyWindow: [] } }
};

/**
 * Get or create endpoint metrics with size limit
 */
const getEndpointMetrics = (endpoint) => {
  if (!metrics.endpoints.has(endpoint)) {
    // Enforce size limit
    if (metrics.endpoints.size >= MAX_ENDPOINT_METRICS) {
      const firstKey = metrics.endpoints.keys().next().value;
      metrics.endpoints.delete(firstKey);
    }
    metrics.endpoints.set(endpoint, {
      requests: 0,
      failures: 0,
      latencyWindow: []
    });
  }
  return metrics.endpoints.get(endpoint);
};

/**
 * Compute p95 from latency window using ceil-based calculation
 */
const computeP95 = (latencies) => {
  if (latencies.length === 0) return 0;
  const sorted = [...latencies].sort((a, b) => a - b);
  const index = Math.ceil(sorted.length * 0.95) - 1;
  return sorted[Math.max(0, Math.min(index, sorted.length - 1))];
};

/**
 * Record request for endpoint with rolling latency window
 */
const recordRequest = (endpoint, latency, success = true) => {
  metrics.totalRequests++;
  const endpointMetrics = getEndpointMetrics(endpoint);
  endpointMetrics.requests++;

  if (latency !== undefined) {
    endpointMetrics.latencyWindow.push(latency);
    // Keep only last N latencies
    if (endpointMetrics.latencyWindow.length > LATENCY_WINDOW_SIZE) {
      endpointMetrics.latencyWindow.shift();
    }
  }

  if (!success) {
    metrics.totalFailures++;
    endpointMetrics.failures++;
  }
};

/**
 * Get current metrics (read-only) with p95 computation
 */
export const getMetrics = () => {
  const endpointMetrics = {};
  for (const [endpoint, data] of metrics.endpoints.entries()) {
    const latencies = data.latencyWindow;
    const avgLatency = latencies.length > 0
      ? latencies.reduce((sum, val) => sum + val, 0) / latencies.length
      : 0;
    const p95Latency = computeP95(latencies);

    endpointMetrics[endpoint] = {
      requests: data.requests,
      failures: data.failures,
      avgLatency,
      p95Latency,
      failureRate: data.requests > 0 ? data.failures / data.requests : 0
    };
  }

  return {
    totalRequests: metrics.totalRequests,
    totalFailures: metrics.totalFailures,
    totalRetries: metrics.totalRetries,
    cacheHits: metrics.cacheHits,
    cacheMisses: metrics.cacheMisses,
    endpoints: endpointMetrics
  };
};

/**
 * Clean up expired idempotency keys
 */
const cleanupExpiredIdempotencyKeys = () => {
  const now = Date.now();
  for (const [url, entry] of idempotencyKeys.entries()) {
    if (now - entry.timestamp > IDEMPOTENCY_TTL) {
      idempotencyKeys.delete(url);
    }
  }
};

/**
 * Clean up expired cache entries
 */
const cleanupExpiredCacheEntries = () => {
  for (const [key, entry] of cache.entries()) {
    if (!isCacheValid(entry)) {
      cache.delete(key);
    }
  }
};

/**
 * Periodic cleanup (runs every 5 minutes) - created only once using globalThis
 */
const startCleanupInterval = () => {
  if (!globalThis.__apiClientCleanupInterval__) {
    globalThis.__apiClientCleanupInterval__ = setInterval(() => {
      cleanupExpiredIdempotencyKeys();
      cleanupExpiredCacheEntries();
    }, 300000);
  }
};

// Start cleanup interval on module load
startCleanupInterval();

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * AUTH UTILITIES (MUST BE BEFORE AXIOS INTERCEPTORS)
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * Clear all in-memory API cache, pending requests, and session keys.
 * Must be called on logout / user switch to prevent cross-tenant data leaks.
 */
export const clearApiCache = () => {
  cache.clear();
  pendingRequests.clear();
  idempotencyKeys.clear();
  circuitBreakers.clear();
  sessionStorage.removeItem('api_session_id');
  try {
    resetGlobalDedupCache();
  } catch (e) {
    // Suppress if not initialized
  }
};

/**
 * Logout utility - clears token and triggers app-wide logout
 * Can be called manually from UI components
 */
export const logout = async () => {
  try {
    await supabase.auth.signOut();
  } catch (e) {
    console.error("Error signing out of Supabase:", e);
  }
  sessionStorage.removeItem("token");
  clearApiCache();
  window.dispatchEvent(new CustomEvent('auth-logout'));

  // Clear websocket session state if it exists
  const wsClient = window.wsClient;
  if (wsClient && wsClient.disconnect) {
    wsClient.disconnect();
  }
};

// Listen for Supabase token refreshes and propagate them
supabase.auth.onAuthStateChange((event, session) => {
  if (event === 'TOKEN_REFRESHED' || event === 'SIGNED_IN') {
    if (session?.access_token) {
      sessionStorage.setItem("token", session.access_token);
      // Propagate to websocket layer if client is available
      if (window.wsClient && window.wsClient.isConnected) {
        // Many WS clients handle reconnects or token updates dynamically
        window.wsClient.token = session.access_token;
        if (window.wsClient.ws && window.wsClient.ws.readyState === WebSocket.OPEN) {
          window.wsClient.ws.send(JSON.stringify({ action: 'auth', token: session.access_token }));
        }
      }
    }
  } else if (event === 'SIGNED_OUT') {
    sessionStorage.removeItem("token");
    clearApiCache();
    window.dispatchEvent(new CustomEvent('auth-logout'));
  }
});

/**
 * Check if user is authenticated
 * @returns {boolean}
 */
export const isAuthenticated = () => {
  const token = sessionStorage.getItem("token");
  return !!token;
};

/**
 * Get current auth token
 * @returns {string|null}
 */
export const getToken = () => {
  return sessionStorage.getItem("token");
};

/**
 * Create axios instance with base configuration
 */
const client = axios.create({
  baseURL: API_BASE,
  headers: {
    "Content-Type": "application/json",
  },
  timeout: REQUEST_TIMEOUT,
});

/**
 * Request interceptor - Add authentication token and request ID
 */
client.interceptors.request.use(
  (config) => {
    const token = sessionStorage.getItem("token");
    // Always attach token if present (including dev_bypass for E2E tests)
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
      if (import.meta.env.DEV && import.meta.env.VITE_DEBUG_AUTH === "true") {
        console.log("🔐 TOKEN ATTACHED");
      }
    }
    // F-03 REMEDIATION (Phase 7B): API request URL logging is dev-only.
    // Production builds must not emit request URLs to the browser console.
    if (import.meta.env.DEV) {
      console.log("📡 REQUEST:", config.url);
    }
    // Add request ID for observability
    const requestId = generateRequestId();
    config.headers['X-Request-ID'] = requestId;
    config.metadata = { requestId, startTime: Date.now() };

    // Add stable idempotency key for mutating operations
    if (config.method !== 'get' && config.method !== 'GET') {
      const data = config.data || {};
      const idempKey = generateIdempotencyKey(config.url, data);

      // Enforce size limit
      enforceSizeLimit(idempotencyKeys, MAX_IDEMPOTENCY_KEYS);

      // Store with expiration for potential retries
      idempotencyKeys.set(config.url, {
        key: idempKey,
        timestamp: Date.now()
      });
      config.headers['Idempotency-Key'] = idempKey;
    }

    return config;
  },
  (error) => Promise.reject(error)
);

/**
 * Response interceptor - Handle common errors, auth lifecycle, and cleanup
 */
client.interceptors.response.use(
  (response) => {
    // Clear idempotency key after successful response
    const url = response.config?.url;
    if (url && idempotencyKeys.has(url)) {
      idempotencyKeys.delete(url);
    }

    // Track duration
    const duration = Date.now() - (response.config?.metadata?.startTime || Date.now());
    const requestId = response.config?.metadata?.requestId;

    if (import.meta.env.PROD && duration > 1000) {
      logError('SLOW_API_REQUEST', {
        url,
        method: response.config?.method?.toUpperCase(),
        duration,
        requestId
      });
    }

    return response;
  },
  (error) => {
    const requestId = error.config?.metadata?.requestId;
    const url = error.config?.url;
    const method = error.config?.method;
    const duration = Date.now() - (error.config?.metadata?.startTime || Date.now());

    // Build error configuration
    const errorConfig = {
      url,
      method,
      requestId,
      status: error.response?.status,
      statusText: error.response?.statusText,
      data: error.response?.data
    };

    // Create structured error
    // Priority: message (new normalized field) → detail (legacy string) → axios message
    const apiError = new ApiError(
      error.response?.data?.message ||
      (typeof error.response?.data?.detail === 'string' ? error.response.data.detail : null) ||
      error.response?.data?.error ||
      error.message ||
      'Request failed',
      errorConfig
    );

    // Log the error
    apiError.log();

    if (error.response) {
      const status = error.response.status;

      // Authentication errors - trigger logout, clear cache, and notify app
      if (status === 401) {
      // F-11 REMEDIATION (Phase 7B): Never log auth response body in production.
      // Error body may contain diagnostic info that aids credential theft.
      if (import.meta.env.DEV) {
        console.error(`🔒 Auth error 401 Unauthorized (dev only):`, status);
      }
        sessionStorage.removeItem("token");
        clearApiCache();
        window.dispatchEvent(new CustomEvent('auth-expired'));
      }

      // Server errors - log to error tracking in production
      if (status >= 500) {
        if (import.meta.env.PROD) {
          logError('API_SERVER_ERROR', apiError.toJSON());
        }
      }

      // Client errors - log to error tracking in production
      if (status >= 400 && status < 500 && status !== 401 && status !== 403) {
        if (import.meta.env.PROD) {
          logError('API_CLIENT_ERROR', apiError.toJSON());
        }
      }
    } else if (error.request) {
      // Network error
      if (import.meta.env.PROD) {
        logError('API_NETWORK_ERROR', apiError.toJSON());
      }
    }

    // Clear idempotency key on error
    if (url && idempotencyKeys.has(url)) {
      idempotencyKeys.delete(url);
    }

    // THROW the error - don't swallow it!
    return Promise.reject(apiError);
  }
);

/**
 * Sanitize sensitive data from logs
 */
const sanitizeData = (data) => {
  if (!data || typeof data !== 'object') return data;

  const sensitiveKeys = ['password', 'secret', 'token', 'apiKey', 'api_key', 'secret_key', 'authorization', 'bearer'];
  const sanitized = { ...data };

  for (const key in sanitized) {
    if (typeof key === 'string') {
      const lowerKey = key.toLowerCase();
      if (sensitiveKeys.some(sensitive => lowerKey.includes(sensitive))) {
        sanitized[key] = '[REDACTED]';
      } else if (typeof sanitized[key] === 'object' && sanitized[key] !== null) {
        sanitized[key] = sanitizeData(sanitized[key]);
      }
    }
  }

  return sanitized;
};

/**
 * Structured logging function
 */
const logError = (type, data) => {
  const logEntry = {
    timestamp: new Date().toISOString(),
    type,
    ...sanitizeData(data),
    environment: import.meta.env.MODE || 'development'
  };

  // Capture to Sentry
  Sentry.captureMessage(`API Error [${type}]: ${data.message || data.error || 'Request failed'}`, {
    level: "error",
    tags: { type: "api_error", api_type: type },
    extra: logEntry
  });

  if (import.meta.env.PROD) {
    // In production, send to error tracking service
    if (typeof window !== 'undefined' && window.__ERROR_TRACKING__) {
      window.__ERROR_TRACKING__(logEntry);
    }
    // Also log to console in production for debugging (sanitized)
    console.error('[API Error]', JSON.stringify(logEntry));
  } else {
    console.error(`[${type}]`, data);
  }
};

/**
 * Log successful request for observability
 */
const logSuccess = (url, duration, method = 'GET') => {
  // Duration recorded in response interceptor and endpoint metrics
};

/**
 * Check if error is retryable
 */
const isRetryableError = (error) => {
  // Don't retry canceled requests (multiple cancel patterns)
  if (error.code === 'ECONNABORTED' ||
    error.message?.includes('canceled') ||
    error.name === 'CanceledError') {
    return false;
  }

  // Don't retry on 4xx client errors (except 408 Request Timeout and 429 Too Many Requests)
  if (error.response) {
    const status = error.response.status;
    if (status >= 400 && status < 500 && status !== 408 && status !== 429) {
      return false;
    }
    // Retry on 5xx server errors
    if (status >= 500) {
      return true;
    }
  }
  // Retry on network errors (no response)
  if (error.request && !error.response) {
    return true;
  }
  return false;
};

/**
 * Retry with exponential backoff (only for retryable errors) with logging
 * Checks circuit breaker before each retry attempt
 * Rethrows the caught error once retries are exhausted or the circuit opens
 */
const retryWithBackoff = async (fn, retries = MAX_RETRIES, attempt = 0, requestId = null, endpoint = null) => {
  try {
    return await fn();
  } catch (error) {
    if (retries <= 0 || !isRetryableError(error)) {
      throw error;
    }

    // Check circuit breaker before retry
    if (endpoint && isCircuitOpen(endpoint)) {
      if (import.meta.env.PROD) {
        logError('CIRCUIT_BREAKER_OPEN_RETRY', {
          endpoint,
          message: 'Circuit breaker is open, aborting retry'
        });
      }
      throw error; // Don't retry if circuit is open
    }

    const attemptNumber = MAX_RETRIES - retries + 1;
    const delay = RETRY_DELAY_BASE * Math.pow(2, MAX_RETRIES - retries);

    // Track retry metric
    metrics.totalRetries++;

    if (import.meta.env.PROD) {
      logError('API_RETRY_ATTEMPT', {
        requestId,
        endpoint,
        attempt: attemptNumber,
        maxRetries: MAX_RETRIES,
        delay,
        error: error.message,
        status: error.response?.status
      });
    }

    await new Promise(resolve => setTimeout(resolve, delay));

    return retryWithBackoff(fn, retries - 1, attemptNumber, requestId, endpoint);
  }
};

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * HTTP METHOD HELPERS
 * ═══════════════════════════════════════════════════════════════════════════
 */

/**
 * GET request helper with caching, deduplication, retry, and circuit breaker
 *
 * `config.cache === false` opts one call out of the 60-second response cache — it is
 * neither read from nor written to. That exists for endpoints whose whole purpose is to
 * report a value that changes underneath a still-open UI: the deployment preflight
 * (`GET .../deploy/preflight`) is polled every two seconds precisely so a condition that
 * has just started failing disables the Deploy button (Requirement 13.6), and a cached
 * answer would make that poll a no-op for a minute. Deduplication of genuinely concurrent
 * identical requests still applies; only the stored-response cache is bypassed, so an
 * opted-out poll also cannot evict other pages' cached reads from the LRU.
 *
 * @param {string} url - API endpoint path
 * @param {Object} [config] - Axios config (params, headers, etc.), plus `cache: false`
 * @returns {Promise<any>} Response data (never undefined, returns null on error)
 */
export const get = (url, config = {}) => {
  const params = config.params || {};
  const useCache = config.cache !== false;
  const cacheKey = getCacheKey(url, params);
  const endpoint = normalizeEndpoint(url);

  // Check circuit breaker
  if (isCircuitOpen(endpoint)) {
    // Return cached data if available when circuit is open
    const cached = safeGet(cache, cacheKey);
    if (useCache && isCacheValid(cached)) {
      metrics.cacheHits++;
      enforceSizeLimit(cache, MAX_CACHE_SIZE, cacheKey);
      return Promise.resolve(cached.data);
    }

    if (import.meta.env.PROD) {
      logError('CIRCUIT_BREAKER_OPEN', {
        endpoint,
        message: 'Circuit breaker is open, blocking request'
      });
    }
    // Circuit breaker open - throw error so UI knows what happened
    const circuitError = new ApiError(
      'Service temporarily unavailable due to repeated failures. Please try again later.',
      {
        url,
        method: 'GET',
        status: 503,
        statusText: 'Service Unavailable (Circuit Open)',
        requestId
      }
    );
    circuitError.log();
    return Promise.reject(circuitError);
  }

  // Check cache first (promote to end for LRU)
  const cached = safeGet(cache, cacheKey);
  if (useCache && isCacheValid(cached)) {
    metrics.cacheHits++;
    enforceSizeLimit(cache, MAX_CACHE_SIZE, cacheKey);
    return Promise.resolve(cached.data);
  }

  metrics.cacheMisses++;

  // Check for pending request (deduplication)
  if (pendingRequests.has(cacheKey)) {
    return pendingRequests.get(cacheKey);
  }

  // Enforce size limit before creating new request
  enforceSizeLimit(pendingRequests, MAX_PENDING_REQUESTS);
  enforceSizeLimit(cache, MAX_CACHE_SIZE);

  // Generate request ID for this call
  const requestId = generateRequestId();

  // Create new request
  const requestPromise = retryWithBackoff(async () => {
    const startTime = Date.now();
    const response = await client.get(url, config);
    const latency = Date.now() - startTime;

    // Record success metrics
    recordRequest(endpoint, latency, true);
    recordCircuitSuccess(endpoint);

    // Cache successful responses
    if (useCache && response.data !== null && response.data !== undefined) {
      enforceSizeLimit(cache, MAX_CACHE_SIZE, cacheKey);
      cache.set(cacheKey, {
        data: response.data,
        timestamp: Date.now()
      });
    }

    // Log latency
    logSuccess(url, latency, 'GET');

    return response.data ?? null;
  }, MAX_RETRIES, 0, requestId, endpoint).catch(error => {
    // Record failure metrics
    recordRequest(endpoint, undefined, false);
    recordCircuitFailure(endpoint, error);

    if (import.meta.env.PROD) {
      logError('GET_REQUEST_FAILED', {
        url,
        params,
        error: error.message,
        status: error.response?.status,
        requestId
      });
    } else {
      console.error(`GET ${url} failed:`, error);
    }
    // RE-THROW the error - don't swallow it!
    // The response interceptor already created an ApiError, so just re-throw
    throw error;
  }).finally(() => {
    // Remove from pending requests
    pendingRequests.delete(cacheKey);
    // Reset testInProgress flag for HALF_OPEN state
    const breaker = getCircuitBreaker(endpoint);
    if (breaker.state === CircuitState.HALF_OPEN) {
      breaker.testInProgress = false;
    }
  });

  pendingRequests.set(cacheKey, requestPromise);
  return requestPromise;
};

/**
 * POST request helper with cache invalidation and circuit breaker
 * @param {string} url - API endpoint path
 * @param {Object} [data] - Request body
 * @param {Object} [config] - Axios config (headers, etc.)
 * @returns {Promise<any>} Response data (never undefined, returns null on error)
 */
export const post = (url, data = {}, config = {}) => {
  const endpoint = normalizeEndpoint(url);
  const startTime = Date.now();

  // Check circuit breaker
  if (isCircuitOpen(endpoint)) {
    if (import.meta.env.PROD) {
      logError('CIRCUIT_BREAKER_OPEN', {
        endpoint,
        message: 'Circuit breaker is open, blocking request'
      });
    }
    // Circuit breaker open - throw error
    const circuitError = new ApiError(
      'Service temporarily unavailable due to repeated failures. Please try again later.',
      {
        url,
        method: 'POST',
        status: 503,
        statusText: 'Service Unavailable (Circuit Open)',
        requestId: generateRequestId()
      }
    );
    circuitError.log();
    return Promise.reject(circuitError);
  }

  return client.post(url, data, config)
    .then(response => {
      const latency = Date.now() - startTime;
      const requestId = response.config?.metadata?.requestId;

      // Record success metrics
      recordRequest(endpoint, latency, true);
      recordCircuitSuccess(endpoint);

      logSuccess(url, latency, 'POST');

      // Invalidate cache after successful mutation
      invalidateCache(url.split('/').slice(0, -1).join('/'));

      return response.data ?? null;
    })
    .catch(error => {
      // Record failure metrics
      recordRequest(endpoint, undefined, false);
      recordCircuitFailure(endpoint, error);

      if (import.meta.env.PROD) {
        logError('POST_REQUEST_FAILED', {
          url,
          error: error.message,
          status: error.response?.status,
          requestId: error.config?.metadata?.requestId,
          duration: Date.now() - startTime
        });
      } else {
        console.error(`POST ${url} failed:`, error);
      }
      // RE-THROW the error - don't swallow it!
      throw error;
    })
    .finally(() => {
      // Reset testInProgress flag for HALF_OPEN state
      const breaker = getCircuitBreaker(endpoint);
      if (breaker.state === CircuitState.HALF_OPEN) {
        breaker.testInProgress = false;
      }
    });
};

/**
 * PUT request helper with cache invalidation and circuit breaker
 * @param {string} url - API endpoint path
 * @param {Object} [data] - Request body
 * @param {Object} [config] - Axios config (headers, etc.)
 * @returns {Promise<any>} Response data (never undefined, returns null on error)
 */
export const put = (url, data = {}, config = {}) => {
  const endpoint = normalizeEndpoint(url);
  const startTime = Date.now();

  // Check circuit breaker
  if (isCircuitOpen(endpoint)) {
    if (import.meta.env.PROD) {
      logError('CIRCUIT_BREAKER_OPEN', {
        endpoint,
        message: 'Circuit breaker is open, blocking request'
      });
    }
    // Circuit breaker open - throw error
    const circuitError = new ApiError(
      'Service temporarily unavailable due to repeated failures. Please try again later.',
      {
        url,
        method: 'PUT',
        status: 503,
        statusText: 'Service Unavailable (Circuit Open)',
        // The circuit opened before a request was issued, so there is no axios error to
        // read a requestId from. Mint one, matching `post` above.
        requestId: generateRequestId()
      }
    );
    circuitError.log();
    return Promise.reject(circuitError);
  }

  return client.put(url, data, config)
    .then(response => {
      const latency = Date.now() - startTime;

      // Record success metrics
      recordRequest(endpoint, latency, true);
      recordCircuitSuccess(endpoint);

      logSuccess(url, latency, 'PUT');

      // Invalidate cache after successful mutation
      invalidateCache(url.split('/').slice(0, -1).join('/'));

      return response.data ?? null;
    })
    .catch(error => {
      // Record failure metrics
      recordRequest(endpoint, undefined, false);
      recordCircuitFailure(endpoint, error);

      if (import.meta.env.PROD) {
        logError('PUT_REQUEST_FAILED', {
          url,
          error: error.message,
          status: error.response?.status,
          requestId: error.config?.metadata?.requestId,
          duration: Date.now() - startTime
        });
      } else {
        console.error(`PUT ${url} failed:`, error);
      }
      // RE-THROW the error - don't swallow it!
      throw error;
    })
    .finally(() => {
      // Reset testInProgress flag for HALF_OPEN state
      const breaker = getCircuitBreaker(endpoint);
      if (breaker.state === CircuitState.HALF_OPEN) {
        breaker.testInProgress = false;
      }
    });
};

/**
 * DELETE request helper with cache invalidation and circuit breaker
 * @param {string} url - API endpoint path
 * @param {Object} [config] - Axios config (params, headers, etc.)
 * @returns {Promise<any>} Response data
 */
export const del = (url, config = {}) => {
  const endpoint = normalizeEndpoint(url);
  const startTime = Date.now();

  // Check circuit breaker
  if (isCircuitOpen(endpoint)) {
    if (import.meta.env.PROD) {
      logError('CIRCUIT_BREAKER_OPEN', {
        endpoint,
        message: 'Circuit breaker is open, blocking request'
      });
    }
    // Circuit breaker open - throw error
    const circuitError = new ApiError(
      'Service temporarily unavailable due to repeated failures. Please try again later.',
      {
        url,
        method: 'DELETE',
        status: 503,
        statusText: 'Service Unavailable (Circuit Open)',
        // The circuit opened before a request was issued, so there is no axios error to
        // read a requestId from. Mint one, matching `post` above.
        requestId: generateRequestId()
      }
    );
    circuitError.log();
    return Promise.reject(circuitError);
  }

  return client.delete(url, config)
    .then(response => {
      const latency = Date.now() - startTime;

      // Record success metrics
      recordRequest(endpoint, latency, true);
      recordCircuitSuccess(endpoint);

      logSuccess(url, latency, 'DELETE');

      // Invalidate cache after successful mutation
      invalidateCache(url.split('/').slice(0, -1).join('/'));

      return response.data ?? null;
    })
    .catch(error => {
      // Record failure metrics
      recordRequest(endpoint, undefined, false);
      recordCircuitFailure(endpoint, error);

      if (import.meta.env.PROD) {
        logError('DELETE_REQUEST_FAILED', {
          url,
          error: error.message,
          status: error.response?.status,
          requestId: error.config?.metadata?.requestId,
          duration: Date.now() - startTime
        });
      } else {
        console.error(`DELETE ${url} failed:`, error);
      }
      // RE-THROW the error - don't swallow it!
      throw error;
    })
    .finally(() => {
      // Reset testInProgress flag for HALF_OPEN state
      const breaker = getCircuitBreaker(endpoint);
      if (breaker.state === CircuitState.HALF_OPEN) {
        breaker.testInProgress = false;
      }
    });
};

/**
 * PATCH request helper with cache invalidation and circuit breaker
 * @param {string} url - API endpoint path
 * @param {Object} [data] - Request body
 * @param {Object} [config] - Axios config (headers, etc.)
 * @returns {Promise<any>} Response data
 */
export const patch = (url, data = {}, config = {}) => {
  const endpoint = normalizeEndpoint(url);
  const startTime = Date.now();

  // Check circuit breaker
  if (isCircuitOpen(endpoint)) {
    if (import.meta.env.PROD) {
      logError('CIRCUIT_BREAKER_OPEN', {
        endpoint,
        message: 'Circuit breaker is open, blocking request'
      });
    }
    // Circuit breaker open - throw error
    const circuitError = new ApiError(
      'Service temporarily unavailable due to repeated failures. Please try again later.',
      {
        url,
        method: 'PATCH',
        status: 503,
        statusText: 'Service Unavailable (Circuit Open)',
        // The circuit opened before a request was issued, so there is no axios error to
        // read a requestId from. Mint one, matching `post` above.
        requestId: generateRequestId()
      }
    );
    circuitError.log();
    return Promise.reject(circuitError);
  }

  return client.patch(url, data, config)
    .then(response => {
      const latency = Date.now() - startTime;

      // Record success metrics
      recordRequest(endpoint, latency, true);
      recordCircuitSuccess(endpoint);

      logSuccess(url, latency, 'PATCH');

      // Invalidate cache after successful mutation
      invalidateCache(url.split('/').slice(0, -1).join('/'));

      return response.data ?? null;
    })
    .catch(error => {
      // Record failure metrics
      recordRequest(endpoint, undefined, false);
      recordCircuitFailure(endpoint, error);

      if (import.meta.env.PROD) {
        logError('PATCH_REQUEST_FAILED', {
          url,
          error: error.message,
          status: error.response?.status,
          requestId: error.config?.metadata?.requestId,
          duration: Date.now() - startTime
        });
      } else {
        console.error(`PATCH ${url} failed:`, error);
      }
      // RE-THROW the error - don't swallow it!
      throw error;
    })
    .finally(() => {
      // Reset testInProgress flag for HALF_OPEN state
      const breaker = getCircuitBreaker(endpoint);
      if (breaker.state === CircuitState.HALF_OPEN) {
        breaker.testInProgress = false;
      }
    });
};

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * PUBLIC API CLIENT (NO AUTH)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * For public endpoints that don't require authentication.
 * Uses native fetch without Authorization header.
 */

/**
 * Public GET request (no auth)
 * @param {string} url - API endpoint path
 * @param {Object} [params] - Query parameters
 * @returns {Promise<any>} Response data
 */
export const publicGet = async (url, params = {}) => {
  const queryString = new URLSearchParams(params).toString();
  const fullUrl = `${API_BASE}${url}${queryString ? `?${queryString}` : ''}`;

  const response = await fetch(fullUrl, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const error = new ApiError(
      errorData.message ||
      (typeof errorData.detail === 'string' ? errorData.detail : null) ||
      `HTTP error! status: ${response.status}`,
      {
        url: fullUrl,
        method: 'GET',
        status: response.status,
        statusText: response.statusText,
        data: errorData
      }
    );
    error.log();
    throw error;
  }

  const data = await response.json();
  return data;
};

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * CONNECTION TEST FUNCTION
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Validates:
 * - Base URL correctness (http://127.0.0.1:8000)
 * - Auth header injection (Authorization: Bearer <token>)
 * - JSON formatting (Content-Type: application/json)
 * - Error handling (401 → logout, 500 → show error)
 */

export async function testConnection() {
  console.log("🧪 Testing API Connection...");
  console.log(`   Base URL: ${API_BASE}`);
  console.log(`   Auth Token: ${getToken() ? 'Present' : 'MISSING'}`);

  try {
    const res = await post("/api/strategies/backtest", {
      strategies: ["rsi"],
      symbol: "BTCUSDT"
    });

    console.log("✅ Connection test SUCCESS:");
    console.log("   Response:", res);
    return { success: true, data: res };
  } catch (error) {
    console.error("❌ Connection test FAILED:");
    console.error("   Error:", error.message);

    if (error.response) {
      const status = error.response.status;
      if (status === 401) {
        console.error("   → Auth token invalid or expired");
      } else if (status >= 500) {
        console.error("   → Server error (500+)");
      } else {
        console.error(`   → HTTP ${status}`);
      }
    } else if (error.request) {
      console.error("   → Network error (server unreachable)");
    }

    return { success: false, error: error.message, status: error.response?.status };
  }
}

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * EXPORT RAW CLIENT FOR ADVANCED USAGE
 * ═══════════════════════════════════════════════════════════════════════════
 */

export default client;
