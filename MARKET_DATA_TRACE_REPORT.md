# MARKET DATA TRACE REPORT

## Objective
Identify exactly how the DAG Executor sources trading pair symbols during backtest initialization in `_run_backtest_sync()`.

## Audit Evidence

### Source Code Mapping
In `routers/strategies.py`, under the `_run_backtest_sync` function:
```python
if dag_config and dag_config.get("nodes"):
    # === DAG MODE (PRIMARY) ===
    symbols = dag_config.get("symbols", ["BTCUSDT"])
```

### Analysis of Expected Sources
1. **`request.symbols`**: IGNORED in DAG mode. It is only read under the fallback `legacy` execution logic.
2. **`input` nodes**: IGNORED. The backtesting loop does not inspect `dag_nodes` to extract `params.symbol` or `symbol` properties from `input`-type nodes before fetching market data.
3. **`dag.symbols`**: REQUIRED. The engine strictly requires `symbols` to be defined as an array at the root of the `dag` configuration object. If missing, it silently defaults to `["BTCUSDT"]`.

## Conclusion
The frontend UI currently packs symbol configurations natively into the `input` node parameters. However, the backend compiler requires the symbols to be hoisted out and provided explicitly at the top-level `dag.symbols` list. This represents a minor architectural mismatch.
