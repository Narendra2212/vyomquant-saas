# TIMEFRAME TRACE REPORT

## Objective
Trace the engine's mechanism for resolving candle resolution mapping and timeframe alignment in `_run_backtest_sync()`.

## Audit Evidence

### Source Code Mapping
In `routers/strategies.py`, under the `_run_backtest_sync` function:
```python
if dag_config and dag_config.get("nodes"):
    # === DAG MODE (PRIMARY) ===
    timeframe = dag_config.get("timeframe", "1h")
```

### Analysis of Expected Sources
1. **`dag.timeframe`**: REQUIRED. The `dag` object must provide a root property named `timeframe`.
2. **Fallback Logic**: If `dag.timeframe` is `None` or omitted, the system falls back to `"1h"`.
3. **Input Nodes**: IGNORED. The parser completely bypasses the `timeframe` property specified inside the individual ReactFlow `input` nodes (e.g. `"15m"`).

## Conclusion
Similar to `symbols`, `timeframe` is strictly interpreted as a global configuration parameter under the root `dag` object, rather than a per-node feature. The frontend should hoist the values extracted from the `input` node and emit them at the root level of the payload.
