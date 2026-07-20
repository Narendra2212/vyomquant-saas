/**
 * Leaderboard API Module
 * 
 * Endpoints: /api/leaderboard/* (PUBLIC - NO AUTH)
 */
import { publicGet } from '../../apiClient';

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

/**
 * @typedef {Object} LeaderboardResponse
 * @property {LeaderboardEntry[]} leaderboard
 */

export const leaderboardApi = {
  /**
   * Get leaderboard data (public endpoint - no auth required)
   * @param {string} [period="30d"] - Time filter: "1d", "7d", "30d", "all"
   * @returns {Promise<LeaderboardResponse>}
   */
  getLeaderboard: (period = "30d") =>
    publicGet('/api/leaderboard', { period }),
};

// Legacy compatibility
export const leaderboardEndpoints = leaderboardApi;
