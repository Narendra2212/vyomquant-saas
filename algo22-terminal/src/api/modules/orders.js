/**
 * Orders API Module
 * 
 * Endpoints: /api/orders/*
 */
import { get, post, put, del } from '../../apiClient';

/**
 * @typedef {Object} OrderRequest
 * @property {string} symbol - Trading symbol (e.g., BTCUSDT)
 * @property {string} side - Order side: 'buy' or 'sell'
 * @property {string} type - Order type: 'market', 'limit', 'stop', 'stop_limit'
 * @property {number} quantity - Order quantity
 * @property {number} [price] - Order price (required for limit orders)
 * @property {number} [stopPrice] - Stop price (required for stop orders)
 * @property {number} [timeInForce] - Time in force: 'GTC', 'IOC', 'FOK'
 * @property {Object} [params] - Additional exchange-specific parameters
 */

/**
 * @typedef {Object} OrderResponse
 * @property {string} orderId - Order ID
 * @property {string} symbol - Trading symbol
 * @property {string} side - Order side
 * @property {string} type - Order type
 * @property {string} status - Order status
 * @property {number} quantity - Order quantity
 * @property {number} [price] - Order price
 * @property {number} [filled] - Filled quantity
 * @property {number} [remaining] - Remaining quantity
 * @property {string} timestamp - Order timestamp
 */

export const ordersApi = {
  /**
   * Create a new order
   * @param {OrderRequest} orderData - Order details
   * @returns {Promise<OrderResponse>}
   */
  createOrder: async (orderData) => {
    return post('/api/orders', orderData);
  },

  /**
   * Get order by ID
   * @param {string} orderId - Order ID
   * @returns {Promise<OrderResponse>}
   */
  getOrder: async (orderId) => {
    return get(`/api/orders/${orderId}`);
  },

  /**
   * Get all orders for current user
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
   * @param {string} [filters.status] - Filter by status
   * @param {number} [filters.limit] - Limit results
   * @returns {Promise<OrderResponse[]>}
   */
  getOrders: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/orders?${params}`);
  },

  /**
   * Cancel an order
   * @param {string} orderId - Order ID
   * @returns {Promise<{success: boolean, message: string}>}
   */
  cancelOrder: async (orderId) => {
    return del(`/api/orders/${orderId}`);
  },

  /**
   * Cancel all orders
   * @param {Object} [options] - Optional parameters
   * @param {string} [options.symbol] - Cancel orders for specific symbol only
   * @returns {Promise<{success: boolean, cancelled: number}>}
   */
  cancelAllOrders: async (options = {}) => {
    const params = new URLSearchParams(options);
    return del(`/api/orders?${params}`);
  },

  /**
   * Get order history
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
   * @param {string} [filters.startDate] - Start date
   * @param {string} [filters.endDate] - End date
   * @param {number} [filters.limit] - Limit results
   * @returns {Promise<OrderResponse[]>}
   */
  getOrderHistory: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/orders/history?${params}`);
  },

  getHistory: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/orders/history?${params}`);
  },

  /**
   * Get open orders
   * @param {string} [symbol] - Filter by symbol
   * @returns {Promise<OrderResponse[]>}
   */
  getOpenOrders: async (symbol) => {
    const params = symbol ? new URLSearchParams({ symbol }) : '';
    return get(`/api/orders/open?${params}`);
  },

  /**
   * Get closed orders
   * @param {Object} [filters] - Optional filters
   * @param {string} [filters.symbol] - Filter by symbol
   * @param {number} [filters.limit] - Limit results
   * @returns {Promise<OrderResponse[]>}
   */
  getClosedOrders: async (filters = {}) => {
    const params = new URLSearchParams(filters);
    return get(`/api/orders/closed?${params}`);
  },

  /**
   * Get order trades
   * @param {string} orderId - Order ID
   * @returns {Promise<Array>}
   */
  getOrderTrades: async (orderId) => {
    return get(`/api/orders/${orderId}/trades`);
  },

  /**
   * Update order (if supported by exchange)
   * @param {string} orderId - Order ID
   * @param {Object} updates - Order updates
   * @param {number} [updates.price] - New price
   * @param {number} [updates.quantity] - New quantity
   * @returns {Promise<OrderResponse>}
   */
  updateOrder: async (orderId, updates) => {
    return put(`/api/orders/${orderId}`, updates);
  },
};
