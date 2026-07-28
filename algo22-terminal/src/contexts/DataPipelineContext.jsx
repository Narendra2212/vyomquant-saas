import React, { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { api, endpoints, get, post } from '../api';
import wsClient from '../websocketClient';
import { getTimeframeMs, getMaxHistoryForTimeframe } from '../utils/engineHelpers';

export const DataPipelineContext = createContext(null);

export const useDataPipeline = () => {
  const context = useContext(DataPipelineContext);
  if (!context) throw new Error("useDataPipeline must be used within DataPipelineProvider");
  return context;
};

export const DataPipelineProvider = ({ children, mode = 'backtest' }) => {
  // Mode: 'backtest' | 'live'
  const [pipelineMode, setPipelineMode] = useState(mode);

  // Exchange from vault (no user selection needed)
  const [activeExchange, setActiveExchange] = useState(null);
  const [isLoadingExchange, setIsLoadingExchange] = useState(true);
  const [availableTimeframes, setAvailableTimeframes] = useState(["1m", "5m", "15m", "1h", "4h", "1d"]);

  // Symbol discovery
  const [availableSymbols, setAvailableSymbols] = useState([]);
  const [symbolSearchQuery, setSymbolSearchQuery] = useState("");
  const [isLoadingSymbols, setIsLoadingSymbols] = useState(false);

  // OHLCV data cache
  const [ohlcCache, setOhlcCache] = useState(new Map());
  const [isFetchingData, setIsFetchingData] = useState(false);
  const [fetchProgress, setFetchProgress] = useState(0);

  // Validation state
  const [validationErrors, setValidationErrors] = useState([]);
  const [dataAvailability, setDataAvailability] = useState({});

  // WebSocket for live mode
  const [liveData, setLiveData] = useState(null);
  const [isLiveConnected, setIsLiveConnected] = useState(false);

  // Fetch connected exchange from vault
  useEffect(() => {
    const fetchActiveExchange = async () => {
      setIsLoadingExchange(true);
      try {
        // Try to fetch from user's exchange vault/connected accounts
        const accounts = await endpoints.exchange?.getAccounts?.() ||
          await endpoints.user?.getConnectedExchanges?.() ||
          await get('/api/exchange/accounts');

        if (accounts && accounts.length > 0) {
          // Get first active exchange
          const active = accounts.find(a => a.status === 'active' || a.is_connected) || accounts[0];
          setActiveExchange({
            id: active.id,
            name: active.exchange || active.name || 'binance',
            apiKey: active.api_key_present || active.has_api_key,
            isTestnet: active.is_testnet || false
          });
        } else {
          // Fallback to binance if no connected exchange
          setActiveExchange({ id: 'default', name: 'binance', isDefault: true });
        }
      } catch (err) {
        console.warn("Failed to fetch exchange accounts, using fallback:", err);
        setActiveExchange({ id: 'default', name: 'binance', isDefault: true });
      } finally {
        setIsLoadingExchange(false);
      }
    };

    fetchActiveExchange();
  }, []);

  // Load markets from CCXT
  const loadMarkets = useCallback(async (exchangeName = null) => {
    const exchange = exchangeName || activeExchange?.name || 'binance';
    setIsLoadingSymbols(true);

    try {
      // Call backend market symbols route
      const symbols = await endpoints.market.getSymbols();

      if (symbols && Array.isArray(symbols)) {
        setAvailableSymbols(symbols);
        return symbols;
      }
    } catch (err) {
      console.error("Failed to load markets:", err);
      // Fallback to hardcoded popular pairs
      const fallback = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "ADA/USDT", "DOGE/USDT", "MATIC/USDT", "DOT/USDT", "LTC/USDT"];
      setAvailableSymbols(fallback);
      return fallback;
    } finally {
      setIsLoadingSymbols(false);
    }
  }, [activeExchange]);

  // Fetch OHLCV data - uses backend /data/historical endpoint
  const fetchOHLCV = useCallback(async (symbol, timeframe, startDate, endDate, options = {}) => {
    const cacheKey = `${symbol}-${timeframe}-${startDate}-${endDate}`;

    // Check cache first
    if (ohlcCache.has(cacheKey) && !options.skipCache) {
      return ohlcCache.get(cacheKey);
    }

    setIsFetchingData(true);
    setFetchProgress(0);

    try {
      // Use new backend endpoint for historical data
      const response = await get('/data/historical', {
        params: {
          symbol,
          timeframe,
          start_date: startDate,
          end_date: endDate,
          exchange: activeExchange?.name || 'binance'
        }
      });

      if (!response?.data || !Array.isArray(response.data)) {
        throw new Error("Invalid data format received from server");
      }

      // Normalize to standardized OHLCV format
      const normalized = response.data.map(candle => ({
        timestamp: candle.timestamp || candle[0],
        datetime: new Date(candle.timestamp || candle[0]).toISOString(),
        open: parseFloat(candle.open || candle[1]),
        high: parseFloat(candle.high || candle[2]),
        low: parseFloat(candle.low || candle[3]),
        close: parseFloat(candle.close || candle[4]),
        volume: parseFloat(candle.volume || candle[5])
      }));

      setFetchProgress(100);

      // Cache the result
      const result = {
        symbol,
        timeframe,
        startDate,
        endDate,
        data: normalized,
        count: normalized.length,
        source: 'historical_api',
        fetchedAt: new Date().toISOString()
      };

      setOhlcCache(prev => new Map(prev).set(cacheKey, result));

      return result;
    } catch (err) {
      console.error("OHLCV fetch failed:", err);
      throw new Error(`Failed to fetch historical data: ${err.message}`);
    } finally {
      setIsFetchingData(false);
      setTimeout(() => setFetchProgress(0), 500);
    }
  }, [activeExchange, ohlcCache]);

  // Fetch historical data using pagination (fallback method)
  const fetchHistoricalPaginated = useCallback(async (symbol, timeframe, startDate, endDate) => {
    const cacheKey = `${symbol}-${timeframe}-${startDate}-${endDate}-paginated`;

    if (ohlcCache.has(cacheKey)) {
      return ohlcCache.get(cacheKey);
    }

    setIsFetchingData(true);
    setFetchProgress(0);

    try {
      const exchange = activeExchange?.name || 'binance';
      const allData = [];

      const since = new Date(startDate).getTime();
      const until = new Date(endDate).getTime();

      if (since >= until) {
        throw new Error("Start date must be before end date");
      }

      // Use CCXT proxy for paginated fetch
      let currentSince = since;
      const maxIterations = 100;
      let iterations = 0;

      while (currentSince < until && iterations < maxIterations) {
        // Use endpoints.market.getMarketData instead of missing CCXT route
        const data = await endpoints.market.getMarketData(symbol, timeframe, 1000);

        if (!data || data.length === 0) break;

        const normalized = data.map(candle => ({
          timestamp: candle.timestamp || candle[0],
          datetime: new Date(candle.timestamp || candle[0]).toISOString(),
          open: parseFloat(candle.open || candle[1]),
          high: parseFloat(candle.high || candle[2]),
          low: parseFloat(candle.low || candle[3]),
          close: parseFloat(candle.close || candle[4]),
          volume: parseFloat(candle.volume || candle[5])
        }));

        allData.push(...normalized);

        const progress = Math.min(100, (currentSince - since) / (until - since) * 100);
        setFetchProgress(progress);

        const lastCandle = normalized[normalized.length - 1];
        currentSince = lastCandle.timestamp + getTimeframeMs(timeframe);

        iterations++;

        if (iterations < maxIterations) {
          await new Promise(r => setTimeout(r, 100));
        }
      }

      const result = {
        symbol,
        timeframe,
        startDate,
        endDate,
        data: allData,
        count: allData.length,
        source: 'ccxt_paginated',
        fetchedAt: new Date().toISOString()
      };

      setOhlcCache(prev => new Map(prev).set(cacheKey, result));

      return result;
    } catch (err) {
      console.error("Paginated fetch failed:", err);
      throw err;
    } finally {
      setIsFetchingData(false);
      setFetchProgress(0);
    }
  }, [activeExchange, ohlcCache]);

  // Fetch orderbook imbalance from backend
  const fetchOrderbookImbalance = useCallback(async (symbol, depth = 20) => {
    try {
      // Use new backend endpoint
      const response = await get('/data/orderbook', {
        params: {
          symbol,
          depth,
          exchange: activeExchange?.name || 'binance'
        }
      });

      if (!response?.data) {
        return null;
      }

      const data = response.data;

      // Standardized orderbook imbalance format
      return {
        symbol: data.symbol || symbol,
        timestamp: data.timestamp || Date.now(),
        bidVolume: data.bid_volume || data.bidVolume || 0,
        askVolume: data.ask_volume || data.askVolume || 0,
        imbalance: data.imbalance || 0,
        spread: data.spread || 0,
        midPrice: data.mid_price || data.midPrice || 0
      };
    } catch (err) {
      console.error("Orderbook fetch failed:", err);
      return null;
    }
  }, [activeExchange]);

  // Connect to live WebSocket for real-time data
  const connectLiveData = useCallback((symbol, timeframe) => {
    if (pipelineMode !== 'live') return null;

    setIsLiveConnected(true);

    // WebSocket connection for live tick data
    const wsUrl = `${wsClient.url}/ws/market-data`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('Live data WebSocket connected');
      ws.send(JSON.stringify({
        action: 'subscribe',
        symbol,
        timeframe,
        exchange: activeExchange?.name || 'binance'
      }));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'candle' || data.type === 'tick') {
        setLiveData({
          timestamp: data.timestamp,
          open: data.open,
          high: data.high,
          low: data.low,
          close: data.close,
          volume: data.volume,
          source: 'websocket'
        });
      }
    };

    ws.onerror = (err) => {
      console.error('Live WebSocket error:', err);
      setIsLiveConnected(false);
    };

    ws.onclose = () => {
      setIsLiveConnected(false);
    };

    return () => {
      ws.close();
      setIsLiveConnected(false);
    };
  }, [pipelineMode, activeExchange]);

  // Clear cache
  const clearCache = useCallback(() => {
    setOhlcCache(new Map());
  }, []);

  // Validate pipeline inputs
  const validateInputs = useCallback((symbol, timeframe, startDate, endDate) => {
    const errors = [];

    if (!symbol || !availableSymbols.includes(symbol)) {
      errors.push({ field: 'symbol', message: 'Invalid or unavailable symbol' });
    }

    const validTimeframes = ["1m", "5m", "15m", "1h", "4h", "1d"];
    if (!validTimeframes.includes(timeframe)) {
      errors.push({ field: 'timeframe', message: 'Invalid timeframe' });
    }

    const start = new Date(startDate);
    const end = new Date(endDate);

    if (isNaN(start.getTime()) || isNaN(end.getTime())) {
      errors.push({ field: 'dateRange', message: 'Invalid date format' });
    } else if (start >= end) {
      errors.push({ field: 'dateRange', message: 'Start date must be before end date' });
    }

    // Check if data is available for the range
    const maxHistory = getMaxHistoryForTimeframe(timeframe);
    const daysRequested = (end - start) / (1000 * 60 * 60 * 24);
    if (daysRequested > maxHistory) {
      errors.push({ field: 'dateRange', message: `Max ${maxHistory} days available for ${timeframe}` });
    }

    setValidationErrors(errors);
    return errors.length === 0;
  }, [availableSymbols]);

  const value = {
    // Mode
    mode: pipelineMode,
    setMode: setPipelineMode,

    // Exchange
    activeExchange,
    isLoadingExchange,
    availableTimeframes,

    // Symbols
    availableSymbols,
    symbolSearchQuery,
    setSymbolSearchQuery,
    isLoadingSymbols,
    loadMarkets,

    // Data fetching
    fetchOHLCV,
    fetchHistoricalPaginated,
    fetchOrderbookImbalance,
    connectLiveData,

    // Cache
    ohlcCache,
    isFetchingData,
    fetchProgress,
    clearCache,

    // Live data
    liveData,
    isLiveConnected,

    // Validation
    validateInputs,
    validationErrors,
    dataAvailability
  };

  return (
    <DataPipelineContext.Provider value={value}>
      {children}
    </DataPipelineContext.Provider>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  INDICATOR ENGINE - Backend-Driven Computation
// ═══════════════════════════════════════════════════════════════════

