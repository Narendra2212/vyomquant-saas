# SERIALIZATION GOLDEN SCHEMA

This defines the exact JSON payload expected by `POST /api/strategies/backtest` and `POST /api/strategies/validate` according to the Pydantic schemas. The frontend must serialize the ReactFlow graph exactly into this format.

```json
{
  "dag": {
    "symbols": ["BTCUSDT"],
    "timeframe": "1h",
    "nodes": [
      {
        "id": "node-1",
        "type": "input",
        "label": "Binance BTC/USDT",
        "symbol": "BTCUSDT",
        "timeframe": "1h"
      },
      {
        "id": "node-2",
        "type": "indicator",
        "label": "RSI 14",
        "indicator": "rsi",
        "params": {
          "period": 14
        }
      },
      {
        "id": "node-3",
        "type": "logic",
        "label": "RSI > 70",
        "operator": "GT"
      },
      {
        "id": "node-4",
        "type": "action",
        "label": "Sell 0.1 BTC",
        "action": "sell",
        "order_type": "market",
        "amount": 0.1
      }
    ],
    "edges": [
      {
        "id": "e1-2",
        "source": "node-1",
        "target": "node-2"
      },
      {
        "id": "e2-3",
        "source": "node-2",
        "target": "node-3"
      },
      {
        "id": "e3-4",
        "source": "node-3",
        "target": "node-4"
      }
    ]
  },
  "symbols": ["BTCUSDT"],
  "timeframe": "1h",
  "initial_capital": 10000.0,
  "trade_size_pct": 0.1,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.04,
  "ml_threshold": 0.0
}
```

## Serialization Rules
1. Every ReactFlow node must be mapped to this array format.
2. ReactFlow's generic `data` object properties (e.g., `node.data.indicator`) must be hoisted up to the root level of the node object in the backend array (e.g., `{"id": "...", "indicator": "rsi"}`).
3. The root `dag` object contains the `nodes` and `edges` arrays.
4. Legacy fields like `strategy_id` or `strategies` array should be omitted since `dag` configuration is provided.
