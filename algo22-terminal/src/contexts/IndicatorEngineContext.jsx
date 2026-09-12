import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { endpoints } from '../api';
// Task 10.8. `extractErrorMessage` is deleted from `ui-legacy/primitives.jsx` — it fell through
// to `JSON.stringify(detail)` and then `err.message`, so a backend traceback reached the screen
// verbatim (Requirement 14.4). The words now come from `design/errorCopy.js`.
import { errorLine } from '../design/errorLine';
import { useDataPipeline } from './DataPipelineContext';
import { getMinDataLength } from '../utils/engineHelpers';

export const IndicatorEngineContext = createContext(null);

export const useIndicatorEngine = () => {
  const context = useContext(IndicatorEngineContext);
  if (!context) throw new Error("useIndicatorEngine must be used within IndicatorEngineProvider");
  return context;
};

export const IndicatorEngineProvider = ({ children }) => {
  // Cache for indicator results: key = "indicator_symbol_timeframe_params_hash"
  const [indicatorCache, setIndicatorCache] = useState(new Map());
  const [isComputing, setIsComputing] = useState(false);
  const [computationError, setComputationError] = useState(null);
  const [lastComputation, setLastComputation] = useState(null);

  // Generate cache key for indicator computation
  const generateCacheKey = useCallback((indicator, symbol, timeframe, params, dataLength) => {
    const paramsHash = JSON.stringify(params);
    return `${indicator}_${symbol}_${timeframe}_${paramsHash}_${dataLength}`;
  }, []);

  // Compute single indicator via backend
  const computeIndicator = useCallback(async (indicator, ohlcvData, params = {}, options = {}) => {
    if (!ohlcvData || ohlcvData.length === 0) {
      throw new Error("No OHLCV data provided for indicator computation");
    }

    const { symbol = 'unknown', timeframe = '1h' } = options;
    const cacheKey = generateCacheKey(indicator, symbol, timeframe, params, ohlcvData.length);

    // Check cache
    if (indicatorCache.has(cacheKey) && !options.skipCache) {
      return indicatorCache.get(cacheKey);
    }

    setIsComputing(true);
    setComputationError(null);

    try {
      // Standardize OHLCV input format
      const standardizedData = ohlcvData.map(candle => ({
        timestamp: candle.timestamp || candle[0],
        open: parseFloat(candle.open || candle[1]),
        high: parseFloat(candle.high || candle[2]),
        low: parseFloat(candle.low || candle[3]),
        close: parseFloat(candle.close || candle[4]),
        volume: parseFloat(candle.volume || candle[5] || 0)
      }));

      // Validate minimum data length
      const minLength = getMinDataLength(indicator, params);
      if (standardizedData.length < minLength) {
        throw new Error(`Not enough data for ${indicator.toUpperCase()} (need ${minLength} candles, got ${standardizedData.length})`);
      }

      // Call backend indicator engine
      const response = await post('/indicator/compute', {
        indicator: indicator.toLowerCase(),
        params,
        data: standardizedData,
        options: {
          symbol,
          timeframe,
          validate: true,
          handle_nan: true
        }
      });

      if (!response?.result) {
        throw new Error(`Invalid response from indicator engine for ${indicator}`);
      }

      // Standardize output format
      const result = {
        indicator: indicator.toLowerCase(),
        params,
        symbol,
        timeframe,
        data: response.result,
        metadata: {
          computedAt: new Date().toISOString(),
          dataPoints: standardizedData.length,
          outputKeys: Object.keys(response.result),
          ...response.metadata
        }
      };

      // Cache result
      setIndicatorCache(prev => new Map(prev).set(cacheKey, result));
      setLastComputation(result);

      return result;
    } catch (err) {
      // STEP 11: the failure in authored words, from `design/errorCopy.js`. The `builder` context
      // is the last-resort copy family, which is what the bare `throw new Error(...)` branches
      // above land on — `translateError` never reads the message off them.
      const errorMsg = errorLine(err, 'builder');
      setComputationError({
        indicator,
        message: errorMsg,
        params,
        timestamp: new Date().toISOString()
      });
      throw new Error(errorMsg);
    } finally {
      setIsComputing(false);
    }
  }, [indicatorCache, generateCacheKey]);

  // Compute multiple indicators in batch
  const computeIndicatorsBatch = useCallback(async (requests, ohlcvData) => {
    const results = {};

    for (const request of requests) {
      const { indicator, params, outputKey } = request;
      try {
        const result = await computeIndicator(indicator, ohlcvData, params);
        results[indicator] = outputKey ? result.data[outputKey] : result.data;
      } catch (err) {
        results[indicator] = { error: err.message };
      }
    }

    return results;
  }, [computeIndicator]);

  // Clear indicator cache
  const clearIndicatorCache = useCallback(() => {
    setIndicatorCache(new Map());
  }, []);

  // Get available outputs for multi-output indicators
  const getAvailableOutputs = useCallback((indicator) => {
    const multiOutputIndicators = {
      macd: ['macd', 'signal', 'histogram'],
      bollinger: ['upper', 'middle', 'lower'],
      bb: ['upper', 'middle', 'lower'],
      stochastic: ['k', 'd'],
      rsi: ['value'],
      ema: ['value'],
      sma: ['value'],
      atr: ['value'],
      obv: ['value'],
      vwap: ['value']
    };
    return multiOutputIndicators[indicator.toLowerCase()] || ['value'];
  }, []);

  // Validate indicator parameters
  const validateIndicatorParams = useCallback((indicator, params) => {
    const errors = [];

    const validations = {
      rsi: { period: { min: 2, max: 100, required: true } },
      macd: {
        fast: { min: 2, max: 50, required: true },
        slow: { min: 5, max: 200, required: true },
        signal: { min: 2, max: 50, required: true }
      },
      ema: { period: { min: 2, max: 200, required: true } },
      sma: { period: { min: 2, max: 200, required: true } },
      bollinger: {
        period: { min: 2, max: 100, required: true },
        stdDev: { min: 0.5, max: 5, required: true }
      },
      atr: { period: { min: 2, max: 100, required: true } }
    };

    const indicatorValidations = validations[indicator.toLowerCase()];
    if (indicatorValidations) {
      Object.entries(indicatorValidations).forEach(([param, rules]) => {
        const value = params[param];
        if (rules.required && (value === undefined || value === null)) {
          errors.push({ param, message: `${param} is required` });
        }
        if (value !== undefined && rules.min !== undefined && value < rules.min) {
          errors.push({ param, message: `${param} must be >= ${rules.min}` });
        }
        if (value !== undefined && rules.max !== undefined && value > rules.max) {
          errors.push({ param, message: `${param} must be <= ${rules.max}` });
        }
      });
    }

    return errors;
  }, []);

  const value = {
    computeIndicator,
    computeIndicatorsBatch,
    indicatorCache,
    clearIndicatorCache,
    isComputing,
    computationError,
    lastComputation,
    getAvailableOutputs,
    validateIndicatorParams
  };

  return (
    <IndicatorEngineContext.Provider value={value}>
      {children}
    </IndicatorEngineContext.Provider>
  );
};

// Helper: Get minimum data length required for indicator
