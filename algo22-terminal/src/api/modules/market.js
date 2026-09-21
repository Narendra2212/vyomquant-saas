/**
 * Market Data API Module
 * 
 * Endpoints: /api/market/*
 */
import { get } from '../../apiClient';

/**
 * @typedef {Object} CandleData
 * @property {number} timestamp
 * @property {number} open
 * @property {number} high
 * @property {number} low
 * @property {number} close
 * @property {number} volume
 */

/**
 * @typedef {Object} OrderBookEntry
 * @property {number} price
 * @property {number} size
 */

/**
 * @typedef {Object} OrderBookSnapshot
 * @property {OrderBookEntry[]} asks
 * @property {OrderBookEntry[]} bids
 */

/**
 * @typedef {Object} TickerSnapshot
 * @property {string} symbol
 * @property {number} bid
 * @property {number} ask
 * @property {number} last
 * @property {number} [change]
 * @property {number} [volume]
 */

/**
 * @typedef {Object} FundingRate
 * @property {string} symbol
 * @property {number} fundingRate
 * @property {string} [nextFundingTime]
 */

import { post } from '../../apiClient';

export const marketApi = {
  /**
   * Get OHLCV candle data
   * @param {string} symbol - Trading pair (e.g., "BTC/USDT")
   * @param {string} [timeframe="5m"] - Candle timeframe
   * @param {number} [limit=200] - Number of candles (1-1000)
   * @returns {Promise<CandleData[]>}
   */
  getCandles: (symbol, timeframe = "5m", limit = 200) =>
    get(`/api/market/candles/${encodeURIComponent(symbol)}/${timeframe}`, { params: { limit } }),

  /**
   * Get order book snapshot
   * @param {string} symbol - Trading pair
   * @param {number} [limit=20] - Depth (1-100)
   * @returns {Promise<OrderBookSnapshot>}
   */
  getOrderBook: (symbol, limit = 20) =>
    get(`/api/market/orderbook/${encodeURIComponent(symbol)}`, { params: { limit } }),

  /**
   * Get ticker snapshot (bid/ask/last)
   * @param {string} symbol - Trading pair
   * @returns {Promise<TickerSnapshot>}
   */
  getTicker: (symbol) =>
    get(`/api/market/ticker/${encodeURIComponent(symbol)}`),

  /**
   * Get funding rate (for perpetuals)
   * @param {string} symbol - Trading pair
   * @returns {Promise<FundingRate>}
   */
  getFundingRate: (symbol) =>
    get(`/api/market/funding/${encodeURIComponent(symbol)}`),

  /**
   * Get available trading symbols
   * @returns {Promise<string[]>}
   */
  getSymbols: () => get('/api/market/symbols'),

  /**
   * Alias for candles endpoint (frontend compatibility)
   * @param {string} symbol
   * @param {string} [timeframe="5m"]
   * @param {number} [limit=200]
   * @returns {Promise<CandleData[]>}
   */
  getMarketData: (symbol, timeframe = "5m", limit = 200) =>
    get(`/api/market/data/${encodeURIComponent(symbol)}/${timeframe}`, { params: { limit } }),

  /*
   * `haltStrategies` was deleted here. It POSTed `/api/strategies/stop`, which no router
   * declares: `routers/strategies.py` has `POST /{strategy_id}/stop` and no `POST /stop`,
   * and no `POST /{strategy_id}` for the literal `stop` to fall into either. There is no
   * fleet-wide halt endpoint at all, so this had no correct address to be repointed at.
   *
   * THE REAL HALT IS `POST /api/risk/kill-switch`. `riskApi.killSwitch` already reaches it
   * and `pages/Dashboard.jsx` already calls it, behind a `ds/ConfirmDialog`
   * acknowledgement. Anything that needs an emergency stop goes there.
   *
   * It is deleted rather than repointed because it had no caller: it was dead code shaped
   * like a safety control, and the failure mode of leaving it was someone wiring an
   * "emergency halt" button to a call that reports success while halting nothing.
   */

  /**
   * Close all positions (emergency liquidation)
   * @returns {Promise<{status: string}>}
   */
  closeAllPositions: () => {
    console.log("📡 API CALL:", '/api/portfolio/close-all');
    return post('/api/portfolio/close-all');
  },
};

// Legacy compatibility
export const marketEndpoints = marketApi;
