/**
 * Exchange API Module
 * 
 * Endpoints: /api/exchanges/*
 */
import { get, post, del } from '../../apiClient';

/**
 * @typedef {Object} ExchangeKeys
 * @property {string} exchange_id
 * @property {string} api_key
 * @property {string} secret_key
 * @property {string} [password]
 * @property {string} [label]
 */

/**
 * @typedef {Object} ExchangeTestResult
 * @property {string} status
 * @property {string} message
 * @property {number} [usdt_balance]
 */

/**
 * @typedef {Object} ExchangeConnection
 * @property {string} exchange_id
 * @property {string} masked_key
 * @property {string} connected_at
 * @property {string} status
 */

export const exchangeApi = {
  /**
   * Get all CCXT-supported exchanges
   * @returns {Promise<{supported: string[]}>}
   */
  getSupported: () => get('/api/exchanges/supported'),

  /**
   * Test exchange connection with provided credentials
   * @param {Object} creds
   * @param {string} creds.exchange_id
   * @param {string} creds.api_key
   * @param {string} creds.secret_key
   * @param {string} [creds.password]
   * @returns {Promise<ExchangeTestResult>}
   */
  testConnection: (creds) => post('/api/exchanges/test', creds),

  /**
   * Save encrypted exchange keys
   * @param {ExchangeKeys} creds
   * @returns {Promise<{status: string, message: string}>}
   */
  saveKeys: (creds) => post('/api/exchanges/keys', creds),

  /**
   * List user's connected exchanges
   * @returns {Promise<ExchangeConnection[]>}
   */
  list: () => get('/api/exchanges/'),

  /**
   * Delete an exchange connection
   * @param {string} exchangeId
   * @returns {Promise<{status: string, message: string}>}
   */
  delete: (exchangeId) => del(`/api/exchanges/${exchangeId}`),
};

// Legacy compatibility
export const exchangeEndpoints = exchangeApi;
