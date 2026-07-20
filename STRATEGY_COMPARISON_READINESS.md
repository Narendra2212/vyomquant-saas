# STRATEGY COMPARISON READINESS

## Objective
Audit the capability of the platform to natively compare two differing quantitative strategies side-by-side.

## Backend Audit (`routers/strategies.py`)
An audit of the backend API routes confirms that **no `/compare` endpoint exists**. The strategy engine operates purely on single-strategy evaluation loops.
* `POST /backtest`: Evaluates a single payload.
* `GET /strategies`: Retrieves a list of saved strategies, but does not provide multi-strategy comparison metadata.
* `GET /stats`: Returns global user statistics, not delta-comparisons.

## Frontend Audit (`algo22-terminal`)
An audit of the frontend React architecture verifies that there is **no Strategy Comparison component**. The UI allows users to view a list of strategies and open a single strategy in the DAG builder/backtester. It is not currently possible to overlay two equity curves or generate a matrix of `Strategy A` vs `Strategy B` performance metrics.

## Verdict: ❌ FAILED
The strategy comparison capability is completely unbuilt. Implementing this feature requires:
1. **Backend**: A new `/api/strategies/compare` endpoint that accepts an array of `strategy_id`s, executes parallel backtests, and returns comparative delta metrics (e.g. `Strategy A outperformed Strategy B by 14.2%`).
2. **Frontend**: A dedicated React split-view component capable of parsing comparative payloads and rendering multi-series charting logic via Recharts.
