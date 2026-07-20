/**
 * Event Deduplication Cache
 * 
 * Prevents duplicate event processing during WebSocket reconnect replay.
 * Uses bounded LRU cache with TTL cleanup.
 * 
 * @module utils/eventDedupCache
 */

/**
 * LRU Cache with TTL support for event deduplication
 */
export class EventDedupCache {
  constructor(options = {}) {
    this.maxSize = options.maxSize || 5000;
    this.defaultTTL = options.defaultTTL || 300000; // 5 minutes default
    
    // Map of event_id -> { timestamp, expiresAt }
    this.cache = new Map();
    
    // Track order for LRU eviction
    this.accessOrder = [];
    
    // Cleanup interval
    this.cleanupInterval = null;
    this.cleanupIntervalMs = options.cleanupIntervalMs || 60000; // 1 minute
    
    // Stats
    this.stats = {
      hits: 0,
      misses: 0,
      evictions: 0,
      totalAdded: 0
    };
    
    this._startCleanup();
  }

  /**
   * Check if event ID exists in cache
   */
  has(eventId) {
    if (!eventId) return false;
    
    const entry = this.cache.get(eventId);
    
    if (!entry) {
      this.stats.misses++;
      return false;
    }
    
    // Check if expired
    if (Date.now() > entry.expiresAt) {
      this.cache.delete(eventId);
      this._removeFromAccessOrder(eventId);
      this.stats.misses++;
      return false;
    }
    
    // Update access order (LRU)
    this._updateAccessOrder(eventId);
    this.stats.hits++;
    return true;
  }

  /**
   * Add event ID to cache
   */
  add(eventId, ttl = null) {
    if (!eventId) return false;
    
    // Already exists - just update access order
    if (this.cache.has(eventId)) {
      this._updateAccessOrder(eventId);
      return true;
    }
    
    // Evict oldest if at capacity
    if (this.cache.size >= this.maxSize) {
      this._evictLRU();
    }
    
    // Add to cache
    const effectiveTTL = ttl || this.defaultTTL;
    this.cache.set(eventId, {
      timestamp: Date.now(),
      expiresAt: Date.now() + effectiveTTL
    });
    
    this.accessOrder.push(eventId);
    this.stats.totalAdded++;
    
    return true;
  }

  /**
   * Mark event as seen (has + add combined)
   * Returns true if event was already seen (duplicate)
   */
  markSeen(eventId, ttl = null) {
    if (this.has(eventId)) {
      return true; // Duplicate
    }
    this.add(eventId, ttl);
    return false; // New event
  }

  /**
   * Get cache statistics
   */
  getStats() {
    return {
      ...this.stats,
      size: this.cache.size,
      maxSize: this.maxSize,
      hitRate: this.stats.hits / (this.stats.hits + this.stats.misses) || 0
    };
  }

  /**
   * Clear all entries
   */
  clear() {
    this.cache.clear();
    this.accessOrder = [];
    this.stats = {
      hits: 0,
      misses: 0,
      evictions: 0,
      totalAdded: 0
    };
  }

  /**
   * Destroy cache and cleanup
   */
  destroy() {
    this._stopCleanup();
    this.clear();
  }

  // =========================================================================
  // Private Methods
  // =========================================================================

  /**
   * Update access order for LRU
   */
  _updateAccessOrder(eventId) {
    const index = this.accessOrder.indexOf(eventId);
    if (index > -1) {
      this.accessOrder.splice(index, 1);
    }
    this.accessOrder.push(eventId);
  }

  /**
   * Remove from access order
   */
  _removeFromAccessOrder(eventId) {
    const index = this.accessOrder.indexOf(eventId);
    if (index > -1) {
      this.accessOrder.splice(index, 1);
    }
  }

  /**
   * Evict least recently used entry
   */
  _evictLRU() {
    if (this.accessOrder.length === 0) return;
    
    const oldestId = this.accessOrder.shift();
    this.cache.delete(oldestId);
    this.stats.evictions++;
  }

  /**
   * Start TTL cleanup interval
   */
  _startCleanup() {
    this.cleanupInterval = setInterval(() => {
      this._cleanupExpired();
    }, this.cleanupIntervalMs);
  }

  /**
   * Stop cleanup interval
   */
  _stopCleanup() {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }
  }

  /**
   * Remove expired entries
   */
  _cleanupExpired() {
    const now = Date.now();
    const expiredIds = [];
    
    for (const [eventId, entry] of this.cache.entries()) {
      if (now > entry.expiresAt) {
        expiredIds.push(eventId);
      }
    }
    
    expiredIds.forEach(id => {
      this.cache.delete(id);
      this._removeFromAccessOrder(id);
    });
    
    if (expiredIds.length > 0) {
      console.log(`[DedupCache] Cleaned up ${expiredIds.length} expired entries`);
    }
  }
}

/**
 * Channel-specific dedup cache
 * Maintains separate cache per WebSocket channel
 */
export class ChannelEventDedupCache {
  constructor(options = {}) {
    this.maxSizePerChannel = options.maxSizePerChannel || 1000;
    this.defaultTTL = options.defaultTTL || 300000;
    
    // Map of channel -> EventDedupCache
    this.caches = new Map();
  }

  /**
   * Get or create cache for channel
   */
  _getCache(channel) {
    if (!this.caches.has(channel)) {
      this.caches.set(channel, new EventDedupCache({
        maxSize: this.maxSizePerChannel,
        defaultTTL: this.defaultTTL
      }));
    }
    return this.caches.get(channel);
  }

  /**
   * Check if event is duplicate for channel
   */
  isDuplicate(channel, eventId, ttl = null) {
    return this._getCache(channel).markSeen(eventId, ttl);
  }

  /**
   * Check if event exists (without marking as seen)
   */
  has(channel, eventId) {
    return this._getCache(channel).has(eventId);
  }

  /**
   * Mark event as seen
   */
  markSeen(channel, eventId, ttl = null) {
    return this._getCache(channel).add(eventId, ttl);
  }

  /**
   * Get stats for all channels
   */
  getStats() {
    const stats = {};
    for (const [channel, cache] of this.caches.entries()) {
      stats[channel] = cache.getStats();
    }
    return stats;
  }

  /**
   * Clear all caches
   */
  clear() {
    this.caches.forEach(cache => cache.clear());
  }

  /**
   * Destroy all caches
   */
  destroy() {
    this.caches.forEach(cache => cache.destroy());
    this.caches.clear();
  }
}

// Global singleton instance
let globalDedupCache = null;

/**
 * Get global dedup cache instance
 */
export function getGlobalDedupCache() {
  if (!globalDedupCache) {
    globalDedupCache = new ChannelEventDedupCache();
  }
  return globalDedupCache;
}

/**
 * Reset global dedup cache
 */
export function resetGlobalDedupCache() {
  if (globalDedupCache) {
    globalDedupCache.destroy();
    globalDedupCache = null;
  }
}

// Convenience function for quick dedup check
export function isDuplicateEvent(channel, eventId) {
  return getGlobalDedupCache().isDuplicate(channel, eventId);
}

export default EventDedupCache;
