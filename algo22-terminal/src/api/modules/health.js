/**
 * Health API Module
 * 
 * Endpoints: /api/health/*
 */
import { get } from '../../apiClient';

/**
 * @typedef {Object} HealthCheckResult
 * @property {string} status - "healthy", "degraded", or "degraded_fallback"
 * @property {number} timestamp
 * @property {Object} checks
 * @property {Object} checks.redis_primary
 * @property {Object} checks.redis_replica
 * @property {string} message
 */

export const healthApi = {
  /**
   * Get comprehensive system health check
   * @returns {Promise<HealthCheckResult>}
   */
  getHealth: () => get('/api/health'),

  /**
   * Get readiness status
   * @returns {Promise<{ready: boolean, timestamp: number, status: string}>}
   */
  getReadiness: () => get('/api/health/ready'),

  /**
   * Get liveness status
   * @returns {Promise<{alive: boolean, timestamp: number, status: string}>}
   */
  getLiveness: () => get('/api/health/live'),

  /**
   * Get detailed Redis health
   * @returns {Promise<Object>}
   */
  getRedisHealth: () => get('/api/health/redis'),
};

// Legacy compatibility
export const healthEndpoints = healthApi;