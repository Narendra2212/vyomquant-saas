# PERFORMANCE RUNTIME PROOF

## Execution
Request executed against local backend server at `127.0.0.1:8000` via isolated HTTP script authenticated with a live JWT token.

## Request
`GET /api/analytics/performance?days=30`

## Result
**Status:** `200`

**Response Body:**
```json
{
  "period_days": 30,
  "total_trades": 0,
  "winning_trades": 0,
  "win_rate": 0,
  "total_pnl": 0,
  "avg_pnl": 0,
  "sharpe_ratio": 0
}
```

## Validation
The endpoint correctly gracefully handles empty data states when QuestDB connects but yields zero trades, returning the requested padded zero-values structure instead of hanging or returning HTTP 404.
