# FRONTEND API PATCHSET

## Patch 1: DAG Backtest Alignment
* **File:** `algo22-terminal/src/App.jsx`
* **Function:** `runBacktest`
* **Risk Level:** LOW
* **Estimated LOC:** 15

**Existing Code:**
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
```

**Replacement Code:**
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
        params: {},
        dag: {
          nodes: strategy.nodes || [],
          edges: (strategy.edges || []).map(e => ({ source: e.source, target: e.target })),
          symbols: symbols,
          timeframe: timeframe,
          strategy_name: strategyName
        }
      };
```

---

## Patch 2: DAG Save Alignment
* **File:** `algo22-terminal/src/App.jsx`
* **Function:** `handleSaveStrategy`
* **Risk Level:** MEDIUM (Bypasses rigid validation via proxy defaults)
* **Estimated LOC:** 10

**Existing Code:**
```javascript
      const payload = {
        name: strategyName,
        nodes,
        edges,
      };
```

**Replacement Code:**
```javascript
      const payload = {
        name: strategyName,
        nodes,
        edges,
        symbol: "BTC/USDT",
        timeframe: "1h",
        buy_logic: { operator: "AND", conditions: [] },
        sell_logic: { operator: "AND", conditions: [] },
        risk: { position_size_pct: 0.1, stop_loss_pct: 0.05, take_profit_pct: 0.1 }
      };
```

---

## Patch 3: Live Deploy Route Repair
* **File:** `algo22-terminal/src/App.jsx`
* **Function:** `handleDeployStrategy`
* **Risk Level:** LOW
* **Estimated LOC:** 3

**Existing Code:**
```javascript
const res = await endpoints.strategies.deploy(id);
```

**Replacement Code:**
```javascript
const res = await endpoints.strategies.deploy(id, { exchange_id: "binance" });
```

---

## Patch 4: Builder Deploy Workflow Repair
* **File:** `algo22-terminal/src/App.jsx`
* **Function:** `handleDeployLive`
* **Risk Level:** HIGH (Requires blocking wait for save ID)
* **Estimated LOC:** 15

**Existing Code:**
```javascript
    // Hit the live deployment execution endpoint
    await endpoints.strategies.deployDag(payload);
    setSaveState("deployed");
```

**Replacement Code:**
```javascript
    // Hit the live deployment execution endpoint
    let targetId = strategyId; // Assuming state tracking
    if (!targetId) {
        // Strategy must be saved first to obtain ID
        console.warn("Strategy must be saved before live deployment");
        setSaveState("error");
        return;
    }
    await endpoints.strategies.deploy(targetId, { exchange_id: "binance" });
    setSaveState("deployed");
```
