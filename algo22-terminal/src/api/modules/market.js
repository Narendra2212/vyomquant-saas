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

  /**
   * Halt all strategies (emergency stop)
   * @returns {Promise<{status: string}>}
   */
  haltStrategies: () => {
    console.log("📡 API CALL:", '/api/strategies/stop (halt)');
    return post('/api/strategies/stop');
  },

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
