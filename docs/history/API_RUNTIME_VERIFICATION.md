# API RUNTIME VERIFICATION

## Execution Details
Backend API queries executed via isolated HTTP client against `127.0.0.1:8000` with valid JWT token.

## 1. Equity Curve
**Request:** `GET /api/portfolio/equity-curve?days=90`
**Status:** 200
**Response Body:**
```json
[]
```

## 2. Performance
**Request:** `GET /api/analytics/performance?days=30`
**Status:** Timeout / Error
**Response Body:**
```text
Error fetching: <Timeout or Server Unreachable>
```
*Note: The backend failed to resolve this query, indicating a possible internal database (QuestDB) unavailability.*

## 3. Recent Transactions
**Request:** `GET /api/portfolio/recent-transactions?limit=50&days=30`
**Status:** 200
**Response Body:**
```json
{
  "transactions": [],
  "count": 0,
  "period_days": 30,
  "generated_at": "2026-06-24T09:14:04.403928"
}
```

## 4. Heatmap
**Request:** `GET /api/portfolio/heatmap?months=3`
**Status:** 500
**Response Body:**
```json
{
  "error": "'list' object cannot be interpreted as an integer",
  "detail": "Internal server error"
}
```
*Note: The backend threw a Python TypeError internally on this route.*
