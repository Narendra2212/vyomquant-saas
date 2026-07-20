# BACKTEST WORKFLOW REALITY CHECK

## Current Code
**File:** `algo22-terminal/src/App.jsx`
**Lines:** 6220-6255
```javascript
      const payload = {
        strategies,
        symbols,
        timeframe,
        initial_capital: Number(initialCapital),
        trade_size_pct: Number(tradeSizePct) / 100,
        stop_loss_pct: Number(stopLossPct) / 100,
        take_profit_pct: Number(takeProfitPct) / 100,
        ml_threshold: Number(mlThreshold),
        params: {
          dag_nodes: strategy.nodes || [],
          dag_edges: (strategy.edges || []).map(e => ({ source: e.source, target: e.target })),
          strategy_name: strategyName,
        }
      };

      const data = await endpoints.strategies.backtest(payload);
      setResults(data);
```

## Payload Structure
The ReactFlow arrays (`strategy.nodes` and `strategy.edges`) are directly stuffed into `payload.params.dag_nodes` and `payload.params.dag_edges`.

## Endpoint
* `endpoints.strategies.backtest(payload)` maps to `POST /api/strategies/backtest` inside `strategies.js` (Line 104).

## Response Flow
The backend response is correctly captured in `data` and passed into `setResults(data)`, allowing the frontend to render valid backtest metrics (assuming the backend successfully runs).

## Contract Compliance
* **dag field presence:** ABSENT. The top-level `dag` object required by `DAGConfig` is completely missing.
* **params.dag_nodes presence:** PRESENT. The frontend nests the DAG inside `params` which the backend explicitly ignores.

## Verdict
**FAIL**
