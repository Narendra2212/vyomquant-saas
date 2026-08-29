/**
 * api/modules/paper.js — Paper Trading API Module
 */

import { get, post, del } from '../../apiClient';

export const paperApi = {
  /**
   * Get virtual paper trading account details
   */
  getAccount: () => get('/api/paper/account'),

  /**
   * Reset virtual paper trading account balance
   * @param {number} capital - New starting capital (default 100,000)
   */
  resetAccount: (capital = 100000.0) => post('/api/paper/account/reset', { capital }),

  /**
   * Get open paper trading positions
   */
  getPositions: () => get('/api/paper/positions'),

  /**
   * Get list of paper orders
   * @param {string} [status] - Filter by status (OPEN, FILLED, CANCELLED)
   */
  getOrders: (status) => get(`/api/paper/orders${status ? `?status=${status}` : ''}`),

  /**
   * Place a new paper trading order
   * @param {Object} order - { symbol, side, order_type, quantity, price, strategy_id }
   */
  placeOrder: (order) => post('/api/paper/orders', order),

  /**
   * Cancel an open paper limit order
   * @param {string} orderId
   */
  cancelOrder: (orderId) => del(`/api/paper/orders/${orderId}`),

  /**
   * Get trade execution fills history
   * @param {number} [limit=50]
   */
  getTrades: (limit = 50) => get(`/api/paper/trades?limit=${limit}`),

  /**
   * Get comprehensive paper trading performance summary
   */
  getSummary: () => get('/api/paper/summary'),
};
