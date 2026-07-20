# EQUITY CURVE CERTIFICATION

## Objective
Trace the `equity_curve` data structure from the backend DAG simulation to the frontend UI components and verify its integrity across the serialization boundary.

## Backend Trace (`routers/strategies.py` & `core/execution_engine.py`)
In `routers/strategies.py`, the backtest handler attempts to construct the equity curve from the `ExecutionEngine.get_stats()` response:
```python
        # Build equity curve
        equity_curve = []
        if 'equity_history' in stats:
            equity_curve = [
                {"time": i, "value": float(eq)}
                for i, eq in enumerate(stats['equity_history'])
            ]
        else:
            equity_curve = [
                {"time": 0, "value": float(initial_capital)},
                {"time": 1, "value": float(stats['current_equity'])}
            ]
```

However, auditing `ExecutionEngine.get_stats()` reveals that **`equity_history` is never returned**. `ExecutionEngine` tracks `current_equity` and `peak_equity`, but lacks an internal array to track equity per-tick.
Consequently, the DAG backtest engine *always* falls back to the hardcoded two-point line: `[initial_capital, current_equity]`.

## Frontend Trace (`src/types/api.types.ts`)
The frontend defines the interface correctly:
```typescript
export interface EquityPoint {
  time: number | string;
  value: number;
}
export interface BacktestResponse {
  // ...
  final_equity: number;
  equity: EquityPoint[];
}
```

## Verdict: ❌ FAILED
The equity curve structurally survives serialization as a `List[Dict[str, float]]` mapped to `EquityPoint[]`. However, the backend fails to actually track per-tick equity history during the `min_length` simulation loop in `_run_backtest_sync`, rendering the equity curve functionally broken. To fix this, an `equity_history` array must be appended to on each loop iteration inside `_run_backtest_sync` and appended to the final payload.
