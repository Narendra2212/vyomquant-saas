/**
 * Risk Management API Module
 * 
 * Endpoints: /api/risk/*
 */
import { get, post, put } from '../../apiClient';

/**
 * @typedef {Object} RiskConfig
 * @property {number} max_daily_loss
 * @property {number} max_positions
 * @property {number} max_leverage
 * @property {Array} kill_switches
 */

/**
 * @typedef {Object} RiskConfigUpdate
 * @property {number} max_daily_loss
 * @property {number} max_positions
 * @property {number} max_leverage
 * @property {Object} kill_switches
 */

/**
 * @typedef {Object} MarginHealth
 * @property {number} margin_ratio
 * @property {number} free_margin
 * @property {number} risk_score
 */

export const riskApi = {
  /**
   * Get risk configuration
   * @returns {Promise<RiskConfig>}
   */
  getConfig: () => get('/api/risk/settings'),

  /**
   * Update risk configuration
   * @param {RiskConfigUpdate} config
   * @returns {Promise<{status: string, message: string}>}
   */
  updateConfig: (config) => put('/api/risk/settings', config),

  /**
   * Get strategy-level limits
   * @returns {Promise<any[]>}
   */
  getStrategyLimits: () => get('/api/risk/strategy-limits'),

  /**
   * Update strategy limit
   * @param {number} strategyId
   * @param {Object} payload
   * @returns {Promise<{status: string}>}
   */
  updateStrategyLimit: (strategyId, payload) =>
    put(`/api/risk/strategy-limits/${strategyId}`, payload),

  /**
   * Get margin health metrics
   * @returns {Promise<MarginHealth>}
   */
  getMarginHealth: () => get('/api/risk/account-health'),

  /**
   * Get account health
   * @returns {Promise<MarginHealth>}
   */
  getAccountHealth: () => get('/api/risk/account-health'),

  /**
   * Activate kill switch
   * @param {Object} params
   * @param {string} [params.scope="user"]
   * @param {string} params.confirm_code
   * @returns {Promise<{status: string, message: string}>}
   */
  killSwitch: (params) => post('/api/risk/kill-switch', params),
};

// Legacy compatibility
export const riskEndpoints = riskApi;
