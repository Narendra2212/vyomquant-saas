import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { endpoints } from '../api';
// Task 10.8. `extractErrorMessage` and `getErrorType` are deleted from
// `ui-legacy/primitives.jsx`: the first fell through to `JSON.stringify(detail)` and then
// `err.message`, so a backend traceback reached the screen verbatim (Requirement 14.4). The
// words now come from `design/errorCopy.js` and no branch below reads `err.message`.
import { resolveCategory } from '../design/errorCopy';
import { errorLine } from '../design/errorLine';
import { useDataPipeline } from './DataPipelineContext';
import { useIndicatorEngine } from './IndicatorEngineContext';
import { useLogicEngine } from './LogicEngineContext';

export const StrategyEngineContext = createContext(null);

export const useStrategyEngine = () => {
  const context = useContext(StrategyEngineContext);
  if (!context) throw new Error("useStrategyEngine must be used within StrategyEngineProvider");
  return context;
};

export const StrategyEngineProvider = ({ children }) => {
  const [executionCache, setExecutionCache] = useState(new Map());
  const [isExecuting, setIsExecuting] = useState(false);
  const [executionError, setExecutionError] = useState(null);
  const [lastExecution, setLastExecution] = useState(null);

  // Generate unique execution ID using CSPRNG (replaces Math.random)
  const generateExecutionId = useCallback(() => {
    return `exec_${crypto.randomUUID()}`;
  }, []);

  // Parse node graph into execution plan (STEP 1)
  const parseGraphToExecutionPlan = useCallback((nodes, edges, options = {}) => {
    const errors = [];
    const warnings = [];

    // Validate graph structure
    if (!nodes || nodes.length === 0) {
      errors.push({ type: 'graph', message: 'No nodes in strategy graph' });
      return { valid: false, errors, plan: null };
    }

    // Find data source node (must have exactly one)
    const sourceNodes = nodes.filter(n => n.type === 'source');
    if (sourceNodes.length === 0) {
      errors.push({ type: 'source', message: 'No data source node found' });
    } else if (sourceNodes.length > 1) {
      warnings.push({ type: 'source', message: 'Multiple data sources found, using first one' });
    }

    const sourceNode = sourceNodes[0];

    /**
     * The market a plan trades is read from the source node's own params, and a missing one is
     * an error naming the field (SB-06, Requirements 12.3 and 12.4).
     *
     * This used to read `symbol || 'BTC/USDT'`, `timeframe || '1h'` and `exchange ||
     * 'binance'` — the same silent-default defect task 3.9 removed from the save path and task
     * 7.3 removed from the selectors, left behind on this legacy plan builder. Three separate
     * problems with it: an unset symbol produced a plan trading a market nobody chose; an unset
     * timeframe produced a plan reading bars nobody chose; and `exchange` is not a strategy
     * property at all — the DATA descriptor publishes no exchange parameter, and exchange
     * identity is a deployment binding (Requirements 12.1, 12.2). All three are gone; nothing
     * substitutes for them.
     */
    let dataSource = null;
    if (sourceNode) {
      const params = sourceNode.data?.params || {};
      const symbol = typeof params.symbol === 'string' && params.symbol.trim() !== '' ? params.symbol : null;
      const timeframe =
        typeof params.timeframe === 'string' && params.timeframe.trim() !== '' ? params.timeframe : null;

      if (symbol === null) {
        errors.push({
          type: 'source',
          field: 'symbol',
          nodeId: sourceNode.id,
          message: 'The data source block names no symbol, so this plan trades no market. Choose one.',
        });
      }
      if (timeframe === null) {
        errors.push({
          type: 'source',
          field: 'timeframe',
          nodeId: sourceNode.id,
          message: 'The data source block names no timeframe, so this plan reads no bar interval. Choose one.',
        });
      }

      dataSource = {
        nodeId: sourceNode.id,
        type: 'ccxt',
        symbol,
        timeframe,
        startDate: params.start_date,
        endDate: params.end_date
      };
    }

    // Build adjacency list for edge traversal
    const adjacency = {};
    edges.forEach(edge => {
      if (!adjacency[edge.source]) adjacency[edge.source] = [];
      adjacency[edge.source].push(edge.target);
    });

    // Collect indicator nodes
    const indicatorNodes = nodes.filter(n => n.type === 'indicator');
    const indicators = indicatorNodes.map(node => {
      const params = node.data?.params || {};
      return {
        nodeId: node.id,
        name: node.data?.label?.toLowerCase().replace(/\s+/g, '_'),
        params: {
          period: params.window || params.period || 14,
          ...params
        },
        outputKey: params.output || 'value',
        dependencies: adjacency[node.id] || []
      };
    });

    // Collect logic nodes
    const logicNodes = nodes.filter(n => n.type === 'logic');
    const logic = logicNodes.map(node => {
      const params = node.data?.params || {};
      return {
        nodeId: node.id,
        type: params.condition_type || 'comparison',
        condition: {
          left: {
            source: 'indicator',
            name: params.left_indicator,
            key: params.left_output || 'value'
          },
          operator: params.operator || '>',
          right: params.right_type === 'indicator' ? {
            source: 'indicator',
            name: params.right_indicator,
            key: params.right_output || 'value'
          } : {
            source: 'constant',
            value: parseFloat(params.right_constant) || 0
          }
        },
        signals: {
          onTrue: params.signal_on_true || 'BUY',
          onFalse: params.signal_on_false || 'NONE'
        },
        minConfidence: parseFloat(params.min_confidence) || 0.5,
        dependencies: adjacency[node.id] || []
      };
    });

    // Collect action/execution nodes
    const actionNodes = nodes.filter(n => n.type === 'action');
    const executionRules = actionNodes.map(node => {
      const params = node.data?.params || {};
      return {
        nodeId: node.id,
        action: node.data?.label?.toLowerCase().replace(/\s+/g, '_'),
        params: {
          sizePct: parseFloat(params.size_pct) || 10,
          orderType: params.order_type || 'MARKET',
          trailPct: parseFloat(params.trail_pct) || 2.0
        },
        dependencies: adjacency[node.id] || []
      };
    });

    // Validate all nodes are connected (have at least one edge or are source)
    const connectedNodeIds = new Set();
    edges.forEach(edge => {
      connectedNodeIds.add(edge.source);
      connectedNodeIds.add(edge.target);
    });

    const disconnectedNodes = nodes.filter(n =>
      n.type !== 'source' && !connectedNodeIds.has(n.id)
    );

    if (disconnectedNodes.length > 0) {
      errors.push({
        type: 'connectivity',
        message: `${disconnectedNodes.length} nodes are not connected`,
        nodes: disconnectedNodes.map(n => n.id)
      });
    }

    // Check for circular dependencies
    const visited = new Set();
    const recursionStack = new Set();

    const hasCycle = (nodeId) => {
      visited.add(nodeId);
      recursionStack.add(nodeId);

      const neighbors = adjacency[nodeId] || [];
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor) && hasCycle(neighbor)) {
          return true;
        } else if (recursionStack.has(neighbor)) {
          return true;
        }
      }

      recursionStack.delete(nodeId);
      return false;
    };

    for (const node of nodes) {
      if (!visited.has(node.id)) {
        if (hasCycle(node.id)) {
          errors.push({ type: 'cycle', message: 'Circular dependency detected in strategy graph' });
          break;
        }
      }
    }

    // Check required params
    nodes.forEach(node => {
      const params = node.data?.params || {};
      if (node.type === 'indicator') {
        if (!params.window && !params.period) {
          warnings.push({ type: 'params', nodeId: node.id, message: `${node.data?.label} using default period` });
        }
      }
    });

    // Build execution plan (STEP 2)
    const executionPlan = {
      version: '1.0',
      executionId: generateExecutionId(),
      timestamp: new Date().toISOString(),
      metadata: {
        strategyName: options.strategyName || 'Unnamed Strategy',
        mode: options.mode || 'backtest',
        totalNodes: nodes.length,
        nodeTypes: nodes.reduce((acc, n) => {
          acc[n.type] = (acc[n.type] || 0) + 1;
          return acc;
        }, {})
      },
      pipeline: {
        dataSource,
        indicators,
        logic,
        executionRules
      },
      graph: {
        nodes: nodes.map(n => ({ id: n.id, type: n.type, label: n.data?.label })),
        edges: edges.map(e => ({ source: e.source, target: e.target }))
      }
    };

    return {
      valid: errors.length === 0,
      errors,
      warnings,
      plan: executionPlan
    };
  }, [generateExecutionId]);

  // Execute strategy via single API call (STEP 3)
  const executeStrategy = useCallback(async (executionPlan, options = {}) => {
    if (!executionPlan) {
      throw new Error('No execution plan provided');
    }

    const cacheKey = `exec_${JSON.stringify(executionPlan.pipeline)}`;

    // Check cache (STEP 8)
    if (executionCache.has(cacheKey) && !options.skipCache) {
      console.log('Using cached execution results');
      return executionCache.get(cacheKey);
    }

    setIsExecuting(true);
    setExecutionError(null);

    try {
      // Single API call to backend
      const response = await post('/strategy/execute', {
        plan: executionPlan,
        options: {
          mode: options.mode || 'backtest',
          generateCharts: options.generateCharts !== false,
          generateMetrics: options.generateMetrics !== false,
          ...options
        }
      });

      if (!response) {
        throw new Error('Empty response from strategy engine');
      }

      // Map backend output (STEP 4)
      const executionResult = {
        executionId: executionPlan.executionId,
        status: response.status || 'completed',
        signals: response.signals || [],
        charts: response.charts || {},
        metrics: response.metrics || {},
        trades: response.trades || [],
        performance: response.performance || {},
        metadata: {
          executedAt: new Date().toISOString(),
          backendVersion: response.version,
          ...response.metadata
        }
      };

      // Cache results
      setExecutionCache(prev => new Map(prev).set(cacheKey, executionResult));
      setLastExecution(executionResult);

      return executionResult;
    } catch (err) {
      // STEP 11: the failure in authored words, from `design/errorCopy.js`.
      const errorMsg = errorLine(err, 'builder');
      /**
       * `type` was `getErrorType`'s HTTP-status vocabulary — `validation`, `missing_data`,
       * `backend_failure`, `auth`, `rate_limit`, `unknown`. It is now `resolveCategory`'s:
       * `RATE_LIMIT`, `NETWORK_ERROR`, `SERVER_ERROR`, `AUTH_ERROR`, `CLIENT_ERROR`, with
       * `UNKNOWN_ERROR` — `ApiError`'s own name for an unclassified failure — standing where
       * `getErrorType` said `unknown`, so the field stays total.
       *
       * The vocabulary swap breaks no branch: `executionError` leaves this provider only on the
       * context value, and nothing in `src/` calls `useStrategyEngine`, so there is no consumer
       * reading these strings. `grep` for the six old labels finds them nowhere but the deleted
       * function itself. The field is kept rather than dropped because removing it would change
       * the state shape, and task 10.8 is a message-translation change and nothing else.
       */
      const errorType = resolveCategory(err) ?? 'UNKNOWN_ERROR';

      setExecutionError({
        type: errorType,
        message: errorMsg,
        executionId: executionPlan.executionId,
        timestamp: new Date().toISOString()
      });

      throw new Error(errorMsg);
    } finally {
      setIsExecuting(false);
    }
  }, [executionCache]);

  // Validate before execution (STEP 6)
  const validateStrategy = useCallback((nodes, edges) => {
    const { valid, errors, warnings } = parseGraphToExecutionPlan(nodes, edges);
    return { valid, errors, warnings };
  }, [parseGraphToExecutionPlan]);

  // Clear execution cache
  const clearExecutionCache = useCallback(() => {
    setExecutionCache(new Map());
  }, []);

  const value = {
    parseGraphToExecutionPlan,
    executeStrategy,
    validateStrategy,
    executionCache,
    clearExecutionCache,
    isExecuting,
    executionError,
    lastExecution,
    generateExecutionId
  };

  return (
    <StrategyEngineContext.Provider value={value}>
      {children}
    </StrategyEngineContext.Provider>
  );
};

// Helper: Validate conditions
