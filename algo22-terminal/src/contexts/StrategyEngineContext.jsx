import React, { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';
import { endpoints } from '../api';
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
    const dataSource = sourceNode ? {
      nodeId: sourceNode.id,
      type: 'ccxt',
      symbol: sourceNode.data?.params?.symbol || 'BTC/USDT',
      timeframe: sourceNode.data?.params?.timeframe || '1h',
      exchange: sourceNode.data?.params?.exchange || 'binance',
      startDate: sourceNode.data?.params?.start_date,
      endDate: sourceNode.data?.params?.end_date
    } : null;

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
      // 🔴 STEP 11: Extract clear error message from backend response
      const errorMsg = extractErrorMessage(err, 'Strategy execution failed');
      const errorType = getErrorType(err);

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
