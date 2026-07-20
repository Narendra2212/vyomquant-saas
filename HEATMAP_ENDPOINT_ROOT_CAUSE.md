# HEATMAP ENDPOINT ROOT CAUSE

## Execution Trace
`routers/portfolio.py` -> `TelemetryEngine.execute_query()` -> TypeError

## Failing Line Number
`backend_app/backend/telemetry_engine.py:174`
```python
        for attempt in range(max_retries):
```

## Stack Trace Details
```text
TypeError: 'list' object cannot be interpreted as an integer
```

## Variable Types
- **Expected Type for `max_retries`**: `int` (default `3`)
- **Actual Type Supplied**: `list` (`[safe_uid, int(months)]`)

## Root Cause
In `backend_app/routers/portfolio.py`, the `pnl_heatmap` endpoint incorrectly calls `telemetry.execute_query(sql_string, list_of_params)`. 

```python
    result = await telemetry.execute_query(
        "SELECT trunc(timestamp, 'd') AS date, sum(pnl) AS pnl_usd "
        "FROM executions WHERE user_id = ? "
        "AND timestamp > dateadd('M', -?, now()) "
        "GROUP BY 1 ORDER BY 1;",
        [safe_uid, int(months)]
    )
```

However, `TelemetryEngine.execute_query` does not support parameterized bindings in its signature:
```python
    async def execute_query(self, sql_query: str, max_retries: int = 3) -> Optional[dict]:
```
Because Python lacks strict runtime type checking, the `[safe_uid, int(months)]` list is passed as the second positional argument (`max_retries`), crashing the `range(max_retries)` loop.

## Required Fixes
1. Modify `routers/portfolio.py` to use Python string concatenation/formatting for the SQL query instead of attempting to pass binding arrays, matching the pattern used in `routers/analytics.py`.
