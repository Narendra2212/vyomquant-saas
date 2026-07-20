# TRADE HISTORY SOURCE AUDIT

## Source Information
**Target File**: `backend_app/backend/telemetry_engine.py`
**Class**: `TelemetryEngine`
**Function**: `get_trade_history`
**Line Range**: 391-421

## Router Details
**Target File**: `backend_app/routers/portfolio.py`
**Endpoint**: `GET /api/portfolio/recent-transactions`
**Function**: `get_recent_transactions`
**Line Range**: 185-232

## Runtime Flow
1. An execution payload is routed to `/api/portfolio/recent-transactions`.
2. Evaluates query parameters `limit` (max 200) and `days` (max 90).
3. The authenticated `user_id` is passed through `_safe_uid()`.
4. It directly executes an SQL Query through `TelemetryEngine` (QuestDB HTTP API):
```sql
SELECT timestamp, symbol, side, amount, price, pnl, fee, order_type 
FROM executions 
WHERE user_id = ? 
AND timestamp > dateadd('d', -?, now()) 
ORDER BY timestamp DESC 
LIMIT ?;
```
5. Maps the columns to rows and manually appends `"type": "trade"` to every entry.

## Actual Data Structure
The returned JSON payload takes the shape:
```json
{
  "transactions": [
    {
      "timestamp": "2023-10-01T12:00:00Z",
      "symbol": "BTC/USDT",
      "side": "buy",
      "amount": 0.15,
      "price": 27000.5,
      "pnl": 0.0,
      "fee": 0.12,
      "order_type": "market",
      "type": "trade"
    }
  ],
  "count": 1,
  "period_days": 7,
  "generated_at": "2023-10-01T12:05:00.000000"
}
```
