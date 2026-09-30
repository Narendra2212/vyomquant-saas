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
 * @property {string} [exchange_status]
 * @property {string[]} [permissions]
 */

/**
 * @typedef {Object} ExchangeConnection
 * @property {string} id
 * @property {string} exchange_id
 * @property {string} name
 * @property {string} masked_key
 * @property {string} status
 * @property {string[]} permissions
 * @property {number} bot_count
 * @property {number} strategy_count
 * @property {string} account_type
 * @property {string[]} enabled_features
 * @property {?string} connected_at
 *   When the credential row was written — `exchange_keys.created_at`, which is when the
 *   connection was established. Nullable: a row that carries no timestamp reports none, and
 *   `design/pageFields.js`'s `connectedAt` entry renders the marker rather than a word.
 * @property {string} subscription_tier
 * @property {string} health
 *
 * NO `last_sync` ON THIS RESPONSE
 * ------------------------------
 * It used to be declared here, and `routers/exchange.py` used to fill it from the same column
 * as `connected_at` — so a row-creation time was presented as a synchronisation time. Nothing
 * in the system records when a venue was last synchronised, so the field is gone from both
 * sides rather than carrying a value that asserts a sync that never happened. The `last_sync`
 * the dashboard renders is a DIFFERENT read (`dashboard_aggregation_service.get_exchange_health`)
 * and is unaffected by this.
 */

export const exchangeApi = {
  /**
   * Get all CCXT-supported exchanges with full metadata
   * @returns {Promise<{exchanges: Object[], total: number}>}
   */
  getSupported: () => get('/api/exchanges/supported'),

  /**
   * Get authentication schema for a specific exchange
   * @param {string} exchangeId
   * @returns {Promise<Object>}
   */
  getAuthSchema: (exchangeId) => get(`/api/exchanges/schema/${exchangeId}`),

  /**
   * Test exchange connection with provided credentials (before saving)
   * @param {Object} creds
   * @param {string} creds.exchange_id
   * @param {string} creds.api_key
   * @param {string} creds.secret_key
   * @param {string} [creds.password]
   * @returns {Promise<ExchangeTestResult>}
   */
  testConnection: (creds) => post('/api/exchanges/test', creds),

  /**
   * Test connection for already-stored exchange keys
   * @param {Object} creds
   * @param {string} creds.exchange_id
   * @returns {Promise<ExchangeTestResult>}
   */
  testStoredConnection: (creds) => post('/api/exchanges/test-stored', creds),

  /**
   * Save encrypted exchange keys
   * @param {ExchangeKeys} creds
   * @returns {Promise<{status: string, message: string}>}
   */
  saveKeys: (creds) => post('/api/exchanges/keys', creds),

  /**
   * List user's connected exchanges with full metadata
   * @returns {Promise<ExchangeConnection[]>}
   */
  list: () => get('/api/exchanges/'),

  /**
   * Delete an exchange connection
   * @param {string} exchangeId
   * @returns {Promise<{status: string, message: string}>}
   */
  delete: (exchangeId) => del(`/api/exchanges/${exchangeId}`),

  /**
   * Reconnect an existing exchange connection
   * @param {string} exchangeId
   * @returns {Promise<Object>}
   */
  reconnect: (exchangeId) => post(`/api/exchanges/connections/${exchangeId}/reconnect`),

  /**
   * Get dynamic connection schema
   * @param {string} exchangeId
   * @returns {Promise<Object>}
   */
  getConnectionSchema: (exchangeId) => get(`/api/exchanges/${exchangeId}/connection-schema`),

  /**
   * List connections alias
   * @returns {Promise<ExchangeConnection[]>}
   */
  listConnections: () => get('/api/exchanges/connections'),
};

// Legacy compatibility
export const exchangeEndpoints = exchangeApi;

