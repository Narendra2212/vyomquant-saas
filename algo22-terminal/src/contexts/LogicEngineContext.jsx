import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { endpoints } from '../api';
import { useIndicatorEngine } from './IndicatorEngineContext';
import { validateConditions } from '../utils/engineHelpers';

export const LogicEngineContext = createContext(null);

export const useLogicEngine = () => {
  const context = useContext(LogicEngineContext);
  if (!context) throw new Error("useLogicEngine must be used within LogicEngineProvider");
  return context;
};

export const LogicEngineProvider = ({ children }) => {
  const [logicCache, setLogicCache] = useState(new Map());
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [evaluationError, setEvaluationError] = useState(null);
  const [lastSignal, setLastSignal] = useState(null);

  // Generate cache key for logic evaluation
  const generateLogicCacheKey = useCallback((conditions, indicatorsData) => {
    const conditionsHash = JSON.stringify(conditions);
    const dataHash = Object.keys(indicatorsData).sort().join(',');
    return `${conditionsHash}_${dataHash}`;
  }, []);

  // Evaluate conditions and generate signal
  const evaluateLogic = useCallback(async (conditions, indicatorsData, options = {}) => {
    if (!conditions || conditions.length === 0) {
      return { signal: 'NONE', confidence: 0, reason: 'No conditions defined' };
    }

    const cacheKey = generateLogicCacheKey(conditions, indicatorsData);

    // Check cache
    if (logicCache.has(cacheKey) && !options.skipCache) {
      return logicCache.get(cacheKey);
    }

    setIsEvaluating(true);
    setEvaluationError(null);

    try {
      // Validate conditions before sending
      const validationErrors = validateConditions(conditions, indicatorsData);
      if (validationErrors.length > 0) {
        throw new Error(`Validation failed: ${validationErrors.map(e => e.message).join(', ')}`);
      }

      // Call backend logic engine
      const response = await post('/logic/evaluate', {
        conditions,
        indicators: indicatorsData,
        options: {
          mode: options.mode || 'strict',
          minConfidence: options.minConfidence || 0.5,
          timestamp: new Date().toISOString()
        }
      });

      if (!response?.signal) {
        throw new Error('Invalid response from logic engine');
      }

      const result = {
        signal: response.signal, // 'BUY', 'SELL', 'NONE'
        confidence: response.confidence || 0,
        triggeredConditions: response.triggeredConditions || [],
        metadata: {
          evaluatedAt: new Date().toISOString(),
          conditionsCount: conditions.length,
          indicatorsUsed: Object.keys(indicatorsData),
          ...response.metadata
        }
      };

      // Cache result
      setLogicCache(prev => new Map(prev).set(cacheKey, result));
      setLastSignal(result);

      return result;
    } catch (err) {
      // 🔴 STEP 11: Extract clear error message
      const errorMsg = extractErrorMessage(err, 'Logic evaluation failed');
      setEvaluationError({
        message: errorMsg,
        conditions,
        timestamp: new Date().toISOString()
      });
      return { signal: 'NONE', confidence: 0, error: errorMsg };
    } finally {
      setIsEvaluating(false);
    }
  }, [logicCache, generateLogicCacheKey]);

  // Evaluate multiple logic nodes in batch
  const evaluateLogicBatch = useCallback(async (logicRequests) => {
    const results = {};

    for (const request of logicRequests) {
      const { nodeId, conditions, indicatorsData } = request;
      try {
        const result = await evaluateLogic(conditions, indicatorsData);
        results[nodeId] = result;
      } catch (err) {
        results[nodeId] = { signal: 'NONE', confidence: 0, error: err.message };
      }
    }

    return results;
  }, [evaluateLogic]);

  // Clear logic cache
  const clearLogicCache = useCallback(() => {
    setLogicCache(new Map());
  }, []);

  // Get available operators for UI
  const getAvailableOperators = useCallback(() => {
    return {
      comparison: [
        { value: '>', label: 'Greater Than', description: 'Value > Threshold' },
        { value: '<', label: 'Less Than', description: 'Value < Threshold' },
        { value: '>=', label: 'Greater or Equal', description: 'Value >= Threshold' },
        { value: '<=', label: 'Less or Equal', description: 'Value <= Threshold' },
        { value: '==', label: 'Equals', description: 'Value == Threshold' }
      ],
      crossover: [
        { value: 'crosses_above', label: 'Crosses Above', description: 'Series A crosses above Series B' },
        { value: 'crosses_below', label: 'Crosses Below', description: 'Series A crosses below Series B' }
      ],
      threshold: [
        { value: 'above_threshold', label: 'Above Threshold', description: 'Value > Constant' },
        { value: 'below_threshold', label: 'Below Threshold', description: 'Value < Constant' }
      ]
    };
  }, []);

  // Get condition template for UI
  const getConditionTemplate = useCallback((type = 'comparison') => {
    const templates = {
      comparison: {
        type: 'comparison',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: '>',
        right: { source: 'constant', value: 0 }
      },
      crossover: {
        type: 'crossover',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: 'crosses_above',
        right: { source: 'indicator', name: '', key: 'value' }
      },
      threshold: {
        type: 'threshold',
        left: { source: 'indicator', name: '', key: 'value' },
        operator: 'above_threshold',
        right: { source: 'constant', value: 70 }
      }
    };
    return templates[type] || templates.comparison;
  }, []);

  const value = {
    evaluateLogic,
    evaluateLogicBatch,
    logicCache,
    clearLogicCache,
    isEvaluating,
    evaluationError,
    lastSignal,
    getAvailableOperators,
    getConditionTemplate
  };

  return (
    <LogicEngineContext.Provider value={value}>
      {children}
    </LogicEngineContext.Provider>
  );
};

// ═══════════════════════════════════════════════════════════════════
//  STRATEGY ENGINE - Graph Parser & Execution Orchestration
// ═══════════════════════════════════════════════════════════════════

