import React, { createContext, useContext, useState, useCallback } from 'react';
// Task 10.8. `extractErrorMessage` and `getErrorType` are deleted from
// `ui-legacy/primitives.jsx`: the first fell through to `JSON.stringify(detail)` and then
// `err.message`, so a backend traceback reached the screen verbatim (Requirement 14.4). The
// words now come from `design/errorCopy.js` and no branch below reads `err.message`.
import { errorLine } from '../design/errorLine';

/**
 * ═══════════════════════════════════════════════════════════════════════════
 * STRATEGY ENGINE — local plan building. No network call path.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `production-launch-hardening` task **9.2**. Requirements 1.27, 2.27. Preservation 3.7.
 *
 * `executeStrategy` used to `await post('/strategy/execute', …)`, with the same two independent
 * defects as its two sibling contexts: `post` was never imported, so the expression raised
 * `ReferenceError: post is not defined` before a request existed; and `/strategy/execute` is
 * declared by no route on this backend. `IndicatorEngineContext.jsx`'s header carries the full
 * argument. The disposition is removal because nothing in `src/` calls `useStrategyEngine` —
 * which the comment that used to sit inside `executeStrategy` already said.
 *
 * THIS FILE IS THE ONE OF THE THREE WITH REAL LOCAL COMPUTATION, AND IT IS KEPT
 * ---------------------------------------------------------------------------
 * `parseGraphToExecutionPlan` below builds the whole execution plan from the canvas with no
 * network at all: the data source, the indicator list, the logic conditions, the execution
 * rules, the graph projection, plus connectivity and cycle validation. It is unchanged, byte
 * for byte, including SB-06 — an unset symbol or timeframe is an error naming the field, never
 * a substituted default (tasks 3.9 and 7.3, Requirements 12.3 and 12.4).
 *
 * What never existed locally is the part that *runs* the plan. That is the half removed.
 *
 * REMOVED WITH THE CALL PATH
 * --------------------------
 *   * `executionCache` / `clearExecutionCache` and `lastExecution` — written only from a
 *     response, so with no response the cache could never hold an entry and its lookup branch
 *     was unreachable.
 *   * the response mapping onto `{signals, charts, metrics, trades, performance}`.
 *   * `executionError.type` and the `resolveCategory` import that produced it. That field held
 *     `ApiError`'s HTTP-status vocabulary, and with no HTTP request no `ApiError` can arrive, so
 *     it could only ever have read `UNKNOWN_ERROR`. Task 10.8 kept it deliberately, on the
 *     grounds that a message-translation change should not alter a state shape; this task
 *     changes the call path, so it goes.
 *   * `isExecuting` as a state cell — published as the constant `false`, because with no request
 *     there is no in-flight period.
 */

export const StrategyEngineContext = createContext(null);

export const useStrategyEngine = () => {
  const context = useContext(StrategyEngineContext);
  if (!context) throw new Error("useStrategyEngine must be used within StrategyEngineProvider");
  return context;
};

export const StrategyEngineProvider = ({ children }) => {
  const [executionError, setExecutionError] = useState(null);

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

  /**
   * Refuse to run a plan — there is nothing here that runs one.
   *
   * Always rejects, and always with a line `design/errorCopy.js` authors. Two distinct causes,
   * carried on `cause` rather than in the copy: no plan was handed over at all, or there is no
   * execution engine this client can reach. Building the plan is `parseGraphToExecutionPlan`'s
   * job and it still does it locally.
   *
   * @param {Object} executionPlan - The plan `parseGraphToExecutionPlan` produced.
   * @returns {Promise<never>}
   */
  const executeStrategy = useCallback(async (executionPlan) => {
    const cause = executionPlan
      ? new Error('No strategy execution engine is reachable from the client')
      : new Error('No execution plan provided');

    const message = errorLine(cause, 'builder');
    setExecutionError({
      message,
      executionId: executionPlan?.executionId ?? null,
      timestamp: new Date().toISOString(),
    });

    throw new Error(message, { cause });
  }, []);

  // Validate before execution (STEP 6)
  const validateStrategy = useCallback((nodes, edges) => {
    const { valid, errors, warnings } = parseGraphToExecutionPlan(nodes, edges);
    return { valid, errors, warnings };
  }, [parseGraphToExecutionPlan]);

  const value = {
    parseGraphToExecutionPlan,
    executeStrategy,
    validateStrategy,
    // Constant, not a state cell: see the header. Published so a refusal cannot leave the
    // builder showing a spinner.
    isExecuting: false,
    executionError,
    generateExecutionId
  };

  return (
    <StrategyEngineContext.Provider value={value}>
      {children}
    </StrategyEngineContext.Provider>
  );
};

// Helper: Validate conditions
