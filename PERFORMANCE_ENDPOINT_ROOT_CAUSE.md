# PERFORMANCE ENDPOINT ROOT CAUSE

## Execution Trace
`routers/analytics.py` -> `TelemetryEngine.execute_query()` -> QuestDB (Port 9000)

## SQL Query Executed
```sql
SELECT COUNT(*) as total_trades, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades, SUM(pnl) as total_pnl, AVG(pnl) as avg_pnl, STDDEV(pnl) as pnl_stddev FROM executions WHERE user_id = '<safe_uid>' AND timestamp > dateadd('D', -<days>, now());
```

## Timeout Location
`backend_app/backend/telemetry_engine.py:182`
```python
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < max_retries - 1:
                    wait = 2**attempt
                    await asyncio.sleep(wait)
```
When QuestDB is unreachable or empty data is returned without QuestDB running, the `execute_query` method attempts 3 retries (1s + 2s + 4s delays), which exceeds the 5-second connection timeout enforced by upstream client callers, resulting in a connection hang or `httpx.ReadTimeout`.

## Exception Trace
```text
QuestDB query completely failed after 3 attempts: Cannot connect to host 127.0.0.1:9000 ssl:default [The remote computer refused the network connection]
```
If QuestDB was reachable but the user had no trades, the backend code currently raises an HTTP 404:
```python
            if total_trades <= 0:
                raise HTTPException(
                    status_code=404,
                    detail="No executed trades found; refusing to return zero analytics.",
                )
```
This 404 fails the success criteria of returning HTTP 200. 

## QuestDB Response Shape (Expected)
```json
{
    "query": "...",
    "columns": [{"name": "total_trades", "type": "LONG"}, ...],
    "dataset": [[0, null, null, null, null]],
    "count": 1
}
```

## Required Fixes
1. Tolerate `execute_query` returning `None` by falling back to zeroed stats instead of throwing a 502/404.
2. If `total_trades <= 0`, return HTTP 200 with zero metrics instead of raising an HTTP 404.
