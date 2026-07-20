# EQUITY CURVE SOURCE AUDIT

## Source Information
**Target File**: `backend_app/backend/telemetry_engine.py`
**Class**: `TelemetryEngine`
**Function**: `get_equity_curve`
**Line Range**: 368-389

## Runtime Flow
1. API router (`backend_app/routers/portfolio.py` -> `equity_curve`) accepts a `days` query parameter.
2. The user ID is validated through `_safe_uid` injection protection.
3. The engine computes `limit = days * 96` (assuming 15-minute intervals, 96 intervals per 24 hours).
4. The backend constructs a QuestDB SQL query: `SELECT timestamp, equity FROM equity_curve WHERE user_id = ? ORDER BY timestamp ASC LIMIT -?;`
5. Calls `self.execute_query(query)` to dispatch over HTTP REST to QuestDB.
6. Maps the response's `dataset` array using the `columns` schema.

## Actual Data Structure
The returned list of dictionaries strictly matches:
```json
[
  {
    "timestamp": "2023-10-01T12:00:00.000000Z",
    "equity": 10543.22
  },
  {
    "timestamp": "2023-10-01T12:15:00.000000Z",
    "equity": 10560.10
  }
]
```

## Router Exposure
**Target File**: `backend_app/routers/portfolio.py`
**Endpoint**: `GET /api/portfolio/equity-curve`
**Line Range**: 120-142
