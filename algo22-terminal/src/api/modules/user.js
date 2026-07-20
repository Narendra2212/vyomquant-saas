/**
 * User API Module
 * 
 * Endpoints: /api/user/* and /api/*
 */
import { get, put, publicGet } from '../../apiClient';

/**
 * @typedef {Object} UserProfile
 * @property {string} id
 * @property {string} username
 * @property {string} email
 * @property {string} [avatar_url]
 * @property {string} [bio]
 * @property {string} [telegram_id]
 */

/**
 * @typedef {Object} ReferralStats
 * @property {number} total_referrals
 * @property {number} active_subs
 * @property {number} total_earned
 * @property {number} pending_payout
 * @property {string} referral_link
 */

/**
 * @typedef {Object} UserStats
 * @property {number} total_trades
 * @property {number} total_pnl
 * @property {number} win_rate
 * @property {number} active_bots
 * @property {number} total_strategies
 */

/**
 * @typedef {Object} LeaderboardEntry
 * @property {number} rank
 * @property {string} name
 * @property {string} strat
 * @property {number} pnl
 * @property {number} dd
 * @property {number} trades
 * @property {string} sub
 */

export const userApi = {
  /**
   * Get user profile
   * @returns {Promise<UserProfile>}
   */
  getProfile: () => get('/api/user/profile'),

  /**
   * Update user profile
   * @param {Partial<UserProfile>} data
   * @returns {Promise<{status: string}>}
   */
  updateProfile: (data) => put('/api/user/profile', data),

  /**
   * Get user stats
   * @returns {Promise<UserStats>}
   */
  getStats: () => get('/api/stats'),

  /**
   * Get referral statistics
   * @returns {Promise<ReferralStats>}
   */
  getReferralStats: () => get('/api/referral/stats'),

  /**
   * Get leaderboard (public)
   * @param {string} [timeframe="30d"] - "all", "30d", "7d", "today"
   * @returns {Promise<LeaderboardEntry[]>}
   */
  getLeaderboard: (timeframe = "30d") =>
    publicGet('/api/leaderboard', { timeframe }),

  /**
   * Get security logs
   * @param {number} [limit=50]
   * @returns {Promise<any[]>}
   */
  getSecurityLogs: (limit = 50) =>
    get('/api/security/logs', { params: { limit } }),

  /**
   * Get performance metrics
   * @param {number} days
   */
  getPerformance: (days = 30) => get('/api/analytics/performance', { params: { days } }),

  /**
   * Get recent transactions
   * @param {number} limit
   * @param {number} days
   */
  getRecentTransactions: (limit = 50, days = 30) => get('/api/portfolio/recent-transactions', { params: { limit, days } }),

  /**
   * Get heatmap
   * @param {number} months
   */
  getHeatmap: (months = 3) => get('/api/portfolio/heatmap', { params: { months } }),

  /**
   * Get equity curve
   * @param {number} days
   */
  getEquityCurve: (days = 90) => get('/api/portfolio/equity-curve', { params: { days } }),
};

// Legacy compatibility
export const userEndpoints = userApi;
