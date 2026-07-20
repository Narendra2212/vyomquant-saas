# Post-Deployment Validation Plan

Generated: 2026-06-18  
Run after: Railway backend is live + Vercel frontend is deployed

---

## Backend Tests (Railway)

Replace `<BACKEND_URL>` with your Railway service URL (e.g. `https://algo22-backend.railway.app`).

---

### Test 1 — Liveness Probe
```bash
curl -f https://<BACKEND_URL>/health/live
# Expected: {"status": "alive"}
# Pass condition: HTTP 200
```

---

### Test 2 — Full Health Check
```bash
curl https://<BACKEND_URL>/health
# Expected:
# {
#   "status": "ok",
#   "mode": "production",
#   "services": {
#     "redis": "connected",
#     "questdb": "fallback",       <-- acceptable if QuestDB not deployed
#     "supabase": "connected",
#     "fleet": "online"
#   }
# }
# Pass condition: redis=connected, supabase=connected
```

---

### Test 3 — Runtime Services Health
```bash
curl https://<BACKEND_URL>/health/services
# Expected:
# {
#   "status": "ok",
#   "summary": {"active": 4, "inactive": 0, "failed": 0},
#   "services": {
#     "consistency_checker": "ACTIVE",
#     "order_watchdog": "ACTIVE",
#     "pnl_engine": "ACTIVE",
#     "reconciliation_scheduler": "ACTIVE"
#   }
# }
```

---

### Test 4 — API Docs
```bash
curl -f https://<BACKEND_URL>/docs
# Expected: HTML page with Swagger UI
# Pass condition: HTTP 200, Content-Type: text/html
```

---

### Test 5 — Prometheus Metrics
```bash
curl https://<BACKEND_URL>/metrics
# Expected: Prometheus text format
# Pass condition: Contains "http_requests_total"
```

---

### Test 6 — Register User
```bash
curl -X POST https://<BACKEND_URL>/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@aerora.io",
    "password": "TestPass123!",
    "full_name": "Test User"
  }'
# Expected: {"user": {...}, "session": {...}} or {"message": "User created"}
# Pass condition: HTTP 200 or 201
```

---

### Test 7 — Login User
```bash
curl -X POST https://<BACKEND_URL>/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@aerora.io",
    "password": "TestPass123!"
  }'
# Expected: {"access_token": "...", "token_type": "bearer"}
# Pass condition: HTTP 200, access_token present
# Save token: export TOKEN=<access_token>
```

---

### Test 8 — Create Strategy
```bash
curl -X POST https://<BACKEND_URL>/api/strategies \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "RSI Test Strategy",
    "description": "Post-deploy smoke test",
    "strategy_type": "RSI",
    "parameters": {"period": 14, "overbought": 70, "oversold": 30}
  }'
# Expected: {"id": "...", "name": "RSI Test Strategy", "status": "active"}
# Pass condition: HTTP 200 or 201
```

---

### Test 9 — Portfolio Check
```bash
curl https://<BACKEND_URL>/api/portfolio \
  -H "Authorization: Bearer $TOKEN"
# Expected: Portfolio object with positions array
# Pass condition: HTTP 200
```

---

### Test 10 — Risk Check
```bash
curl https://<BACKEND_URL>/api/risk \
  -H "Authorization: Bearer $TOKEN"
# Expected: Risk metrics object
# Pass condition: HTTP 200
```

---

### Test 11 — Generate Paper Trade
```bash
curl -X POST https://<BACKEND_URL>/api/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC/USDT",
    "side": "buy",
    "order_type": "market",
    "quantity": 0.001,
    "paper_trade": true
  }'
# Expected: Order object with status "paper_filled" or "pending"
# Pass condition: HTTP 200/201, AERORA_MODE=paper enforced
```

---

### Test 12 — Verify Execution Record
```bash
# After paper trade above, verify execution record was saved
curl https://<BACKEND_URL>/api/orders \
  -H "Authorization: Bearer $TOKEN"
# Expected: Array containing the paper trade order
# Pass condition: Order from Test 11 visible
```

---

### Test 13 — WebSocket Connection
```javascript
// Browser console or wscat:
// wscat -c wss://<BACKEND_URL>/ws/market
const ws = new WebSocket('wss://<BACKEND_URL>/ws/market');
ws.onopen = () => console.log('Connected');
ws.onmessage = (e) => console.log('Message:', e.data);
// Pass condition: Connection established, heartbeat received
```

---

## Frontend Tests (Vercel)

```bash
# 1. Verify Vercel deployment
curl -f https://<VERCEL_URL>
# Expected: HTML with React app shell

# 2. Verify no broken imports
# Open browser DevTools → Console — check for errors

# 3. Verify API calls route to Railway
# Open browser DevTools → Network — filter XHR — check requests go to Railway URL
```

---

## Pass Criteria Summary

| Test | Required | Blocking |
|------|---------|---------|
| `/health/live` returns `alive` | ✅ Yes | ✅ Yes |
| `/health` shows `supabase=connected` | ✅ Yes | ✅ Yes |
| `/health` shows `redis=connected` | ✅ Yes | ✅ Yes |
| `/docs` renders | ✅ Yes | ❌ No |
| `/metrics` returns Prometheus data | ✅ Yes | ❌ No |
| Register + Login works | ✅ Yes | ✅ Yes |
| Paper trade executes | ✅ Yes | ✅ Yes |
| Execution record saved | ✅ Yes | ✅ Yes |
| WebSocket connects | ✅ Yes | ❌ No |
| Frontend loads | ✅ Yes | ✅ Yes |
