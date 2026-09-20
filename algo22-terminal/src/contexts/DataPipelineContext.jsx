/**
 * DataPipelineContext.jsx
 *
 * What task 7.3 removed from this file, and why it could not stay
 * --------------------------------------------------------------
 * This file used to hold three client-side vocabularies, all three of them the same defect:
 *
 * 1. **A ten-symbol fallback list** in `loadMarkets`. It tested the `/api/market/symbols`
 *    response with `Array.isArray` and, on any failure, set `availableSymbols` to
 *    `["BTC/USDT", "ETH/USDT", …]` with a `console.warn`. Task 7.2 deleted the identical list
 *    from `routers/market.py`; leaving this one would have re-armed the defect **in the
 *    client**, where nothing on the server can see it — the endpoint would honestly answer
 *    `503 ASSET_UNIVERSE_UNAVAILABLE` and the UI would show ten markets anyway, as though the
 *    platform had confirmed it could trade them.
 * 2. **A seven-label timeframe list** as `availableTimeframes`' initial state
 *    (`1m … 1w`). The published set is the *intersection* of the pipeline's own vocabularies
 *    (task 7.2) and it does not contain `1w`, and deliberately does not contain `3m`: the
 *    market-data coverage gate has no figure for either, so a strategy on one would run with
 *    its completeness check silently unfailable. Offering them was the lie.
 * 3. **A second copy of that list** inside `validateInputs`, used to accept or reject a
 *    timeframe. A local whitelist that disagrees with the pipeline is a gate that passes what
 *    the engine will refuse and refuses what it would accept.
 *
 * What replaced them: the symbol list is whatever the endpoint served on this page load, and
 * an unavailable universe is an explicit error state (`symbolsError`) with **zero** symbols —
 * never a substitute, and never a silently empty list presented as if the platform traded
 * nothing. The timeframe set comes from `lib/registryClient.js`'s
 * `GET /api/strategy-operations/registry/timeframes` cache, which is the same source the
 * builder's own `TimeframeSelector` reads, so there is exactly one interval vocabulary in the
 * frontend.
 *
 * The builder's DATA-node controls do **not** read this context — they use
 * `components/builder/AssetSelector.jsx` and `TimeframeSelector.jsx` against the canonical,
 * paginated, provenance-carrying endpoints. This context remains the backtest/live data
 * pipeline's own state; what it must not do is hold a vocabulary of its own.
 */

import React, { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { api, endpoints, get, post } from '../api';
import wsClient from '../websocketClient';
import { getTimeframeMs, getMaxHistoryForTimeframe } from '../utils/engineHelpers';
import { getTimeframeSnapshot, loadTimeframes, subscribeTimeframes } from '../lib/registryClient';

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

  /**
   * The published interval set, from the one registry cache (Requirement 11.8). Starts empty
   * and stays empty until the endpoint answers: there is no initial list here, because an
   * initial list is a list.
   */
  const [timeframeSnapshot, setTimeframeSnapshot] = useState(getTimeframeSnapshot);

  // Symbol discovery
  const [availableSymbols, setAvailableSymbols] = useState([]);
  const [symbolSearchQuery, setSymbolSearchQuery] = useState("");
  const [isLoadingSymbols, setIsLoadingSymbols] = useState(false);
  /** Why there are no symbols, when there are none. `null` while nothing has failed. */
  const [symbolsError, setSymbolsError] = useState(null);

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
          await get('/api/exchanges/');

        // Normalize response to ensure it's an array
        const accountsArray = Array.isArray(accounts) ? accounts : [];

        if (accountsArray.length > 0) {
          // Get first active exchange
          const active = accountsArray.find(a => a.status === 'active' || a.is_connected) || accountsArray[0];
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

  // The published interval set. Subscribed before loading, so a cache that is already ready
  // resolves synchronously without the listener missing its transition.
  useEffect(() => {
    const unsubscribe = subscribeTimeframes(setTimeframeSnapshot);
    setTimeframeSnapshot(getTimeframeSnapshot());
    loadTimeframes();
    return unsubscribe;
  }, []);

  /**
   * The markets the platform can trade, from `GET /api/market/symbols` — which task 7.2
   * re-pointed at the same cached universe `GET /api/strategy-operations/assets` serves.
   *
   * Fails closed. A failure sets `symbolsError` and leaves `availableSymbols` **empty**: there
   * is no fallback list here any more, and a response that is not an array is an error rather
   * than a reason to substitute one. The two empty cases stay distinguishable, because they
   * have different fixes — `symbolsError === null` with an empty list means the endpoint
   * genuinely served no market, and a non-null `symbolsError` means the list is unknown.
   */
  const loadMarkets = useCallback(async () => {
    setIsLoadingSymbols(true);
    setSymbolsError(null);

    try {
      const symbols = await endpoints.market.getSymbols();

      if (!Array.isArray(symbols)) {
        throw new Error(
          'The market symbols endpoint did not return a list of symbols, so the tradeable ' +
            'market list is unknown.',
        );
      }

      const canonical = symbols.filter((symbol) => typeof symbol === 'string' && symbol !== '');
      setAvailableSymbols(canonical);
      return canonical;
    } catch (err) {
      console.error('Failed to load markets:', err);
      setAvailableSymbols([]);
      setSymbolsError({
        // The backend's own sentence where it sent one (a 503 carries
        // `ASSET_UNIVERSE_UNAVAILABLE` and its message); this file authors none of its own
        // beyond the shape error above.
        code: err?.data?.error || err?.code || 'SYMBOL_UNIVERSE_UNAVAILABLE',
        message: err?.data?.message || err?.message || 'The tradeable market list is unavailable.',
        status: err?.status ?? null,
        retryAfterSeconds:
          typeof err?.data?.details?.retry_after_seconds === 'number'
            ? err.data.details.retry_after_seconds
            : null,
      });
      // Rethrown rather than swallowed into a value: a caller that cannot tell a failure from
      // an empty market list is the defect this task removed.
      throw err;
    } finally {
      setIsLoadingSymbols(false);
    }
  }, []);

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
      throw new Error(`Failed to fetch historical data: ${err.message}`, { cause: err });
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

  const availableTimeframes = useMemo(
    () => timeframeSnapshot.timeframes.map((entry) => entry.id),
    [timeframeSnapshot],
  );

  /**
   * Validate pipeline inputs.
   *
   * The symbol and timeframe checks are made against the **fetched** sets, and both fail
   * closed when the corresponding set could not be loaded: an unverifiable value is reported
   * as unverifiable rather than waved through, because this gate guards a data fetch and a
   * timeframe the pipeline cannot process produces silently mis-aligned bars. Each error names
   * why, so "we could not check" and "your value is wrong" are different messages.
   */
  const validateInputs = useCallback((symbol, timeframe, startDate, endDate) => {
    const errors = [];

    if (symbolsError !== null) {
      errors.push({
        field: 'symbol',
        code: 'SYMBOL_UNIVERSE_UNAVAILABLE',
        message: `The tradeable market list is unavailable, so “${symbol}” cannot be checked: ${symbolsError.message}`,
      });
    } else if (availableSymbols.length === 0) {
      errors.push({
        field: 'symbol',
        code: 'SYMBOL_UNIVERSE_NOT_LOADED',
        message: 'The tradeable market list has not been loaded yet, so the symbol cannot be checked.',
      });
    } else if (!symbol || !availableSymbols.includes(symbol)) {
      errors.push({
        field: 'symbol',
        code: 'SYMBOL_NOT_TRADEABLE',
        message: 'This symbol is not in the market list this platform can trade',
      });
    }

    if (timeframeSnapshot.isError) {
      errors.push({
        field: 'timeframe',
        code: 'TIMEFRAME_SET_UNAVAILABLE',
        message: `The supported interval set is unavailable, so “${timeframe}” cannot be checked: ${timeframeSnapshot.error?.message || ''}`.trim(),
      });
    } else if (availableTimeframes.length === 0) {
      errors.push({
        field: 'timeframe',
        code: 'TIMEFRAME_SET_NOT_LOADED',
        message: 'The supported interval set has not been loaded yet, so the timeframe cannot be checked.',
      });
    } else if (!availableTimeframes.includes(timeframe)) {
      errors.push({
        field: 'timeframe',
        code: 'TIMEFRAME_NOT_SUPPORTED',
        message: 'This interval is not one every stage of the data pipeline can process',
      });
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
  }, [availableSymbols, availableTimeframes, symbolsError, timeframeSnapshot]);

  const value = {
    // Mode
    mode: pipelineMode,
    setMode: setPipelineMode,

    // Exchange
    activeExchange,
    isLoadingExchange,

    // Timeframes — the registry's published intersection, never a local list
    availableTimeframes,
    timeframeEntries: timeframeSnapshot.timeframes,
    isLoadingTimeframes: timeframeSnapshot.isLoading,
    timeframesError: timeframeSnapshot.error,

    // Symbols
    availableSymbols,
    symbolSearchQuery,
    setSymbolSearchQuery,
    isLoadingSymbols,
    /** Non-null when the market list is unknown. Consumers must not render zero as "none". */
    symbolsError,
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

