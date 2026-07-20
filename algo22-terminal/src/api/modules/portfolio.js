/**
 * Portfolio API Module
 * 
 * Endpoints: /api/portfolio/*
 */
import { get, post, put, del } from '../../apiClient';

/**
 * @typedef {Object} Position
 * @property {string} positionId - Position ID
 * @property {string} symbol - Trading symbol
 * @property {string} side - Position side: 'long' or 'short'
 * @property {number} size - Position size
 * @property {number} entryPrice - Average entry price
 * @property {number} currentPrice - Current market price
 * @property {number} unrealizedPnl - Unrealized profit/loss
 * @property {number} realizedPnl - Realized profit/loss
 * @property {string} timestamp - Position timestamp
 */

/**
 * @typedef {Object} PortfolioSummary
 * @property {number} totalValue - Total portfolio value
 * @property {number} totalPnl - Total profit/loss
 * @property {number} availableBalance - Available balance
 * @property {number} usedBalance - Used balance
 * @property {number} marginUsed - Margin used
 * @property {number} marginLevel - Margin level
 * @property {Position[]} positions - Open positions
 */

export const portfolioApi = {
  /**
   * Get portfolio summary
   * @returns {Promise<PortfolioSummary>}
   */
  getSummary: async () => {
    return get('/api/portfolio/summary');
  },

  /**
   * Get all positions
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
 * @param {string} [filters.side] - Filter by side
   * @returns {Promise<Position[]>}
   */
  getPositions: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/portfolio/positions?${params}`);
  },

  /**
   * Get position by ID
   * @param {string} positionId - Position ID
   * @returns {Promise<Position>}
   */
  getPosition: async (positionId) => {
    return get(`/api/portfolio/positions/${positionId}`);
  },

  /**
   * Close a position
   * @param {string} positionId - Position ID
   * @param {Object} [options] - Optional parameters
   * @param {number} [options.quantity] - Quantity to close (default: all)
   * @returns {Promise<{success: boolean, message: string}>}
   */
  closePosition: async (positionId, options = {}) => {
    return post(`/api/portfolio/positions/${positionId}/close`, options);
  },

  /**
   * Get position history
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
   * @param {string} [filters.startDate] - Start date
   * @param {string} [filters.endDate] - End date
   * @param {number} [filters.limit] - Limit results
   * @returns {Promise<Position[]>}
   */
  getPositionHistory: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/portfolio/positions/history?${params}`);
  },

  /**
   * Get portfolio balance
   * @returns {Promise<{balance: number, available: number, used: number}>}
   */
  getBalance: async () => {
    return get('/api/portfolio/balance');
  },

  /**
   * Get portfolio PnL
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.startDate] - Start date
   * @param {string} [filters.endDate] - End date
   * @returns {Promise<{realizedPnl: number, unrealizedPnl: number, totalPnl: number}>}
   */
  getPnL: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/portfolio/pnl?${params}`);
  },

  /**
   * Get portfolio performance metrics
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.period] - Time period: '1d', '1w', '1m', '1y'
   * @returns {Promise<Object>}
   */
  getPerformance: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/portfolio/performance?${params}`);
  },

  /**
   * Get open positions only
   * @returns {Promise<Position[]>}
   */
  getOpenPositions: async () => {
    return get('/api/portfolio/positions/open');
  },

  /**
   * Get closed positions only
   * @param {Object} [filters] - Optional filters
   * @param {number} [filters.limit] - Limit results
   * @returns {Promise<Position[]>}
   */
  getClosedPositions: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/portfolio/positions/closed?${params}`);
  },
};
