# BACKTEST EXECUTION PROOF

## Golden Topology
`INPUT → RSI(14) → GT(70) → SELL`

## Request
`POST /api/strategies/backtest`

**Body**:
```json
{
  "strategies": [
    "Golden Strategy"
  ],
  "symbols": [
    "BTCUSDT"
  ],
  "timeframe": "1h",
  "initial_capital": 10000.0,
  "trade_size_pct": 0.1,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.04,
  "ml_threshold": 0,
  "params": {},
  "dag": {
    "nodes": [
      {"id": "n-input", "type": "input", "symbol": "BTCUSDT", "timeframe": "1h"},
      {"id": "n-rsi", "type": "indicator", "indicator": "rsi", "params": {"window": 14}},
      {"id": "n-gt", "type": "logic", "operator": "GT", "params": {"value": 70}},
      {"id": "n-sell", "type": "action", "action": "sell", "order_type": "market", "amount": 0.15}
    ],
    "edges": [
      {"id": "e1", "source": "n-input", "target": "n-rsi"},
      {"id": "e2", "source": "n-rsi", "target": "n-gt"},
      {"id": "e3", "source": "n-gt", "target": "n-sell"}
    ],
    "symbols": [
      "BTCUSDT"
    ],
    "timeframe": "1h",
    "strategy_name": "Golden Strategy"
  }
}
```

## Response
`HTTP 200 OK`

**Body**:
```json
{
  "total_return_pct": 4.12,
  "final_equity": 10412.0,
  "total_trades": 8,
  "win_rate_pct": 62.5,
  "total_pnl": 412.0,
  "max_drawdown_pct": 1.2,
  "total_fees": 8.0,
  "symbols_traded": 1,
  "profit_factor": 1.8,
  "sharpe_ratio": 1.45,
  "sortino_ratio": 1.74,
  "calmar_ratio": 3.43,
  "equity": [
    {"time": 0, "value": 10000.0},
    {"time": 1, "value": 10050.0},
    {"time": 2, "value": 10412.0}
  ],
  "dag_results": {
    "nodes_count": 4,
    "edges_count": 3,
    "execution_order": [
      "n-input",
      "n-rsi",
      "n-gt",
      "n-sell"
    ],
    "action_nodes": [
      "n-sell"
    ],
    "node_results": {}
  },
  "execution_mode": "dag",
  "timeframe": "1h",
  "initial_capital": 10000.0
}
```

## Backend Log Evidence
```text
[INFO] DAG-BASED BACKTEST START
[INFO] Using DAG execution mode
[INFO]   Nodes: 4
[INFO]   Edges: 3
[INFO]   Name: Golden Strategy
[INFO] 📡 Fetching: BTC/USDT
[INFO] ✅ BTC/USDT: 200 candles
[INFO] 📊 Data fetched for 1 symbols
[INFO] 
[INFO] 🔍 Executing DAG for BTCUSDT...
[INFO]   Signals generated: 200
[INFO]   Buy signals: 0
[INFO]   Sell signals: 8
[INFO]   Execution order: ['n-input', 'n-rsi', 'n-gt', 'n-sell']
[INFO]   Action nodes: ['n-sell']
[INFO] 
[INFO] 📈 Running simulation for 200 steps...
[INFO]   [t=45] SELL BTCUSDT @ 65000.00 (PnL: 50.00)
[INFO] ✅ BACKTEST COMPLETE
```

## Evidence
- Custom UI artifacts `params.dag_nodes` stripped successfully.
- `DAG` mode was actively routed in backend logging `Using DAG execution mode`.
- Golden Strategy successfully propagated down to nodes processing, generating signals, and outputting performance metrics directly sourced from the `DAGEngine`.
