# STEP 8.10: Staging Environment Guide

## Safe Testing with Paper Trading

This guide covers the staging environment for safe testing of the trading platform without risking real money.

---

## 🎯 What is Staging?

The staging environment is a complete replica of the production system with one key difference: **all trading is simulated**. 

- **Paper trading balance**: $100,000 (virtual)
- **Simulated exchange**: Realistic fills, latency, and commissions
- **Real-time market data**: Simulated price movements
- **Full pipeline testing**: Signal → Validation → Execution
- **No real money at risk**

---

## 📁 Files

| File | Purpose |
|------|---------|
| `.env.staging` | Staging configuration |
| `docker-compose.staging.yml` | Staging stack |
| `backend/exchange_simulator.py` | Paper trading engine |
| `Dockerfile.simulator` | Exchange simulator image |
| `tests/staging/test_full_pipeline.py` | Full pipeline tests |

---

## 🚀 Quick Start

### 1. Start Staging Environment

```bash
# Start all staging services
docker-compose -f docker-compose.staging.yml up -d

# Check services are running
docker-compose -f docker-compose.staging.yml ps
```

### 2. Access Staging Endpoints

| Service | URL | Notes |
|---------|-----|-------|
| Staging Backend | http://localhost:8001 | Paper trading mode |
| Paper Exchange | http://localhost:8081 | Simulated exchange |
| Staging Prometheus | http://localhost:9091 | Metrics |
| Staging Grafana | http://localhost:3001 | admin/staging-admin |

### 3. Run Full Pipeline Tests

```bash
# Run all staging tests
docker-compose -f docker-compose.staging.yml run --rm test-runner

# Or run locally with pytest
cd tests/staging
pytest test_full_pipeline.py -v

# Run specific test
pytest test_full_pipeline.py::TestFullPipeline::test_order_placement -v
```

### 4. Check Paper Trading Balance

```bash
# Get balance
curl http://localhost:8081/balance

# Example response:
{
  "balance": 100000.0,
  "equity": 100000.0,
  "initial_balance": 100000.0,
  "pnl": 0.0,
  "pnl_pct": 0.0
}
```

### 5. Place Paper Trade

```bash
# Buy 1 BTC
curl -X POST http://localhost:8081/orders \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC-USD",
    "side": "buy",
    "quantity": 1.0,
    "order_type": "market"
  }'

# Check positions
curl http://localhost:8081/positions

# Check updated balance
curl http://localhost:8081/balance
```

### 6. Reset Paper Trading Account

```bash
# Reset balance back to $100,000
curl -X POST http://localhost:8081/reset
```

---

## 🧪 Testing Scenarios

### Test 1: Full Order Flow

```bash
#!/bin/bash
# test_order_flow.sh

echo "Testing full order flow..."

# 1. Check balance
echo "Initial balance:"
curl -s http://localhost:8081/balance | jq '.'

# 2. Place buy order
echo -e "\nPlacing buy order..."
ORDER=$(curl -s -X POST http://localhost:8081/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC-USD", "side": "buy", "quantity": 0.5, "order_type": "market"}')
echo $ORDER | jq '.'

# 3. Check position
echo -e "\nPosition after buy:"
curl -s http://localhost:8081/positions | jq '.'

# 4. Place sell order
echo -e "\nPlacing sell order..."
curl -s -X POST http://localhost:8081/orders \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC-USD", "side": "sell", "quantity": 0.5, "order_type": "market"}' | jq '.'

# 5. Final balance
echo -e "\nFinal balance:"
curl -s http://localhost:8081/balance | jq '.'

echo -e "\n✅ Order flow test complete!"
```

### Test 2: Circuit Breaker Testing

```python
# test_circuit_breaker.py
import requests

BASE = "http://localhost:8001"

# Test 1: Check circuit breaker status
r = requests.get(f"{BASE}/health")
print(f"Circuit state: {r.json().get('circuit_breaker', {}).get('state', 'unknown')}")

# Test 2: Simulate multiple failures
for i in range(10):
    # These should eventually trigger circuit breaker
    r = requests.get(f"{BASE}/health")
    
print("Check if circuit breaker opened after failures")
```

### Test 3: WebSocket Data Flow

```python
# test_websocket.py
import asyncio
import aiohttp

async def test_ws():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('ws://localhost:8082/ws') as ws:
            await ws.send_json({
                "action": "subscribe",
                "symbols": ["BTC-USD", "ETH-USD"]
            })
            
            for i in range(5):
                msg = await ws.receive()
                print(f"Tick {i}: {msg.data}")
                await asyncio.sleep(1)

asyncio.run(test_ws())
```

### Test 4: Load Testing

```bash
# Simple load test with ab (Apache Bench)
ab -n 1000 -c 10 http://localhost:8001/health

# Or use wrk
wrk -t4 -c100 -d30s http://localhost:8001/health
```

---

## 🔧 Configuration

### Environment Variables

Key settings in `.env.staging`:

```bash
# Paper trading
PAPER_TRADING_BALANCE_USD=100000.0
EXCHANGE_MODE=paper

# Relaxed circuit breaker (for testing)
CIRCUIT_BREAKER_DAILY_LOSS_THRESHOLD=10.0
CIRCUIT_BREAKER_DRAWDOWN_THRESHOLD=20.0
CIRCUIT_BREAKER_CONSECUTIVE_FAILURES=10

# Relaxed ExecutionGuard
EXECUTION_GUARD_MAX_POSITION_SIZE_PCT=50.0
EXECUTION_GUARD_MAX_DAILY_TRADES=1000

# Reduced latency for faster tests
MOCK_LATENCY_MS=50

# Feature flags
FEATURE_DEBUG_ENDPOINTS_ENABLED=true
FEATURE_PAPER_TRADING_BALANCE_RESET=true
```

### Simulator Settings

```python
# backend/exchange_simulator.py settings

exchange = PaperTradingExchange(
    initial_balance=100000.0,      # Starting balance
    latency_ms=50,                 # Simulated latency
    fill_probability=0.95,         # 95% fill rate
    commission_rate=0.001,         # 0.1% commission
    enable_websocket=True,         # Enable WS streaming
)
```

---

## 📊 Monitoring Staging

### Prometheus (Staging)

```bash
# Query staging metrics
curl 'http://localhost:9091/api/v1/query?query=trades_executed_total'

# Check backend health
curl 'http://localhost:9091/api/v1/query?query=up{job="staging-backend"}'
```

### Grafana Dashboards (Staging)

Access: http://localhost:3001 (admin/staging-admin)

Staging-specific dashboards:
- Staging Trading Performance
- Paper Trading Balance/PnL
- Test Execution Metrics

### Logs

```bash
# Backend logs
docker-compose -f docker-compose.staging.yml logs -f backend-staging

# Exchange simulator logs
docker-compose -f docker-compose.staging.yml logs -f exchange-simulator

# All staging logs
docker-compose -f docker-compose.staging.yml logs -f
```

---

## 🔄 CI/CD Integration

### GitHub Actions - Staging Tests

```yaml
# .github/workflows/staging-tests.yml
name: Staging Tests

on:
  push:
    branches: [develop, feature/*]

jobs:
  staging-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Start staging environment
        run: docker-compose -f docker-compose.staging.yml up -d
      
      - name: Wait for services
        run: sleep 30
      
      - name: Run health checks
        run: |
          curl -f http://localhost:8001/health
          curl -f http://localhost:8081/health
      
      - name: Run full pipeline tests
        run: |
          docker-compose -f docker-compose.staging.yml \
            run --rm test-runner pytest tests/staging/ -v
      
      - name: Cleanup
        if: always()
        run: docker-compose -f docker-compose.staging.yml down -v
```

---

## 🧹 Cleanup

```bash
# Stop all staging services
docker-compose -f docker-compose.staging.yml down

# Remove volumes (data will be lost)
docker-compose -f docker-compose.staging.yml down -v

# Remove images
docker-compose -f docker-compose.staging.yml down --rmi local
```

---

## 🐛 Troubleshooting

### Issue: Services won't start

```bash
# Check logs
docker-compose -f docker-compose.staging.yml logs backend-staging
docker-compose -f docker-compose.staging.yml logs exchange-simulator

# Check ports
lsof -i :8001
lsof -i :8081

# Restart
docker-compose -f docker-compose.staging.yml restart
```

### Issue: Tests fail

```bash
# Check if services are healthy
curl http://localhost:8001/health
curl http://localhost:8081/health

# Run tests with more verbosity
pytest tests/staging/test_full_pipeline.py -vvs

# Check Redis
docker-compose -f docker-compose.staging.yml exec redis-staging redis-cli ping
```

### Issue: Balance not updating

```bash
# Check exchange simulator is responding
curl http://localhost:8081/health

# Reset paper trading account
curl -X POST http://localhost:8081/reset

# Check order history
curl http://localhost:8081/orders/history
```

---

## 📋 Test Checklist

Before deploying to production, verify:

- [ ] Health checks pass (`/health`, `/health/ready`, `/health/live`)
- [ ] Paper trading balance initializes to $100,000
- [ ] Orders can be placed and filled
- [ ] Positions are tracked correctly
- [ ] Balance updates after trades
- [ ] PnL is calculated correctly
- [ ] Circuit breaker responds to thresholds
- [ ] WebSocket streams market data
- [ ] Metrics are collected in Prometheus
- [ ] Alerts fire in Alertmanager
- [ ] All 15 tests in `test_full_pipeline.py` pass

---

## 🎓 Learning Exercises

### Exercise 1: Test Circuit Breaker

1. Configure very low thresholds in `.env.staging`
2. Run multiple failing orders
3. Verify circuit opens
4. Verify trading stops
5. Wait for cooldown and verify circuit closes

### Exercise 2: Test Failover

1. Start staging environment
2. Kill Redis primary: `docker kill redis-staging`
3. Verify backend switches to replica
4. Verify trading continues (read-only)
5. Restart Redis primary

### Exercise 3: Test Liquidation

1. Configure low daily loss threshold
2. Place losing trades until threshold hit
3. Verify auto-liquidation triggers
4. Verify positions closed, orders cancelled

---

## 📞 Support

For issues with staging:

1. Check logs: `docker-compose -f docker-compose.staging.yml logs`
2. Verify configuration: `cat .env.staging`
3. Reset environment: `docker-compose -f docker-compose.staging.yml down -v && docker-compose -f docker-compose.staging.yml up -d`
4. Run health checks: `curl http://localhost:8001/health`

---

## 🎉 Summary

The staging environment provides:

- ✅ **Safe testing** - No real money at risk
- ✅ **Full pipeline** - Test complete trading flow
- ✅ **Real-time simulation** - Market data, fills, latency
- ✅ **All safety features** - Circuit breaker, liquidation, failover
- ✅ **Easy reset** - Start fresh anytime
- ✅ **CI/CD ready** - Automated testing

**Status**: Ready for testing!
