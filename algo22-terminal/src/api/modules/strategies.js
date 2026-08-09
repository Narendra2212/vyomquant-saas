/**
 * Strategies API Module
 * 
 * Endpoints: /api/strategies/*
 */
import { get, post, put, del } from '../../apiClient';

/**
 * @typedef {Object} StrategyNode
 * @property {string} id
 * @property {string} type
 * @property {string} label
 * @property {Object} [params]
 */

/**
 * @typedef {Object} BacktestPayload
 * @property {string[]} strategies - Strategy names (e.g., ["rsi", "macd"])
 * @property {string[]} symbols - Trading pairs (e.g., ["BTCUSDT"])
 * @property {string} timeframe
 * @property {number} initial_capital
 * @property {number} trade_size_pct - Decimal (0.1 = 10%)
 * @property {number} stop_loss_pct - Decimal (0.02 = 2%)
 * @property {number} take_profit_pct - Decimal (0.04 = 4%)
 * @property {number} ml_threshold
 * @property {Object} [params] - Extra params including DAG config
 */

/**
 * @typedef {Object} BacktestResult
 * @property {number} total_return_pct
 * @property {number} final_equity
 * @property {number} total_trades
 * @property {number} win_rate_pct
 * @property {number} total_pnl
 * @property {number} max_drawdown_pct
 * @property {number} profit_factor
 * @property {number} sharpe_ratio
 * @property {number} sortino_ratio
 * @property {number} calmar_ratio
 * @property {Array} equity
 */

/**
 * @typedef {Object} DeployResponse
 * @property {string} status
 * @property {string} message
 */

export const strategiesApi = {
  /**
   * Get all strategies
   * @returns {Promise<any[]>}
   */
  list: () => get('/api/strategies'),

  /**
   * Get strategy by ID
   * @param {string} id
   * @returns {Promise<any>}
   */
  getById: (id) => get(`/api/strategies/${id}`),

  /**
   * Save a new strategy
   * @param {Object} payload - Strategy data
   * @returns {Promise<{strategy_id: string, status: string}>}
   */
  create: (payload) => post('/api/strategies', payload),

  /**
   * Update a strategy
   * @param {string} id
   * @param {Object} payload
   * @returns {Promise<any>}
   */
  update: (id, payload) => put(`/api/strategies/${id}`, payload),

  /**
   * Delete a strategy
   * @param {string} id
   * @returns {Promise<{status: string, deleted: string}>}
   */
  delete: (id) => del(`/api/strategies/${id}`),

  /**
   * Deploy/start a strategy
   * @param {string} id
   * @param {Object} [body={}]
   * @param {Object} [options={}] - Optional config including idempotencyKey
   * @returns {Promise<DeployResponse>}
   */
  deploy: (id, body = {}, options = {}) => {
    const headers = {};
    if (options.idempotencyKey) {
      headers['Idempotency-Key'] = options.idempotencyKey;
    }
    return post(`/api/strategies/${id}/deploy`, body, { headers });
  },

  /**
   * Start a strategy (alias for deploy)
   * @param {string} id
   * @param {Object} [body={}]
   * @param {Object} [options={}] - Optional config including idempotencyKey
   * @returns {Promise<DeployResponse>}
   */
  start: (id, body = {}, options = {}) => strategiesApi.deploy(id, body, options),

  /**
   * Stop a strategy
   * @param {string} id
   * @returns {Promise<{status: string}>}
   */
  stop: (id) => post(`/api/strategies/${id}/stop`),

  /**
   * Pause a strategy (alias for stop)
   * @param {string} id
   * @returns {Promise<{status: string}>}
   */
  pause: (id) => post(`/api/strategies/${id}/stop`),

  /**
   * Run backtest
   * @param {BacktestPayload} payload
   * @returns {Promise<BacktestResult>}
   */
  backtest: (payload) => post('/api/strategies/backtest', payload),

  /**
   * Validate DAG-based strategy
   * @param {Object} payload - DAG configuration
   * @returns {Promise<any>}
   */
  validate: (payload) => post('/api/strategies/validate', payload),

  /**
   * Train ML model
   * @param {string} strategyId - Strategy ID
   * @param {Object} params - Training parameters
   * @returns {Promise<{status: string, message: string}>}
   */
  trainMl: (strategyId, params) => post(`/api/strategies/${strategyId}/train`, params),

  /**
   * Get available blocks for Strategy Builder
   * @returns {Promise<{indicators: Array, ml_models: Array, dl_models: Array, total_blocks: number}>}
   */
  getBlocks: () => get('/api/strategies/blocks'),
};

// Legacy compatibility
export const strategyEndpoints = strategiesApi;
export const strategiesEndpoints = strategiesApi;

/**
 * 🔴 STEP 5: Generate unique idempotency key for strategy deployment
 * Prevents duplicate execution when retrying failed requests
 * 
 * @param {string} strategyId - Strategy identifier
 * @returns {string} Unique idempotency key
 */
export const generateIdempotencyKey = (strategyId) => {
  // Use CSPRNG (crypto.randomUUID) — Math.random() is not cryptographically secure
  return `deploy_${strategyId}_${crypto.randomUUID()}`;
};
