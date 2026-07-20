# 🔥 STEP 8 — METRICS FOR SCALE MONITORING

## Goal: Track Load for 500 Users (≈150 Active)

**Focus:**
- Real-time scaling visibility
- Load tracking
- Scaling decision support

---

## PROBLEM

Without scaling metrics:
- ❌ No visibility into system load
- ❌ Can't predict when to scale
- ❌ Reactive instead of proactive scaling
- ❌ No historical trend data
- ❌ Blind to bottlenecks

---

## SOLUTION: SCALING METRICS

### Metrics Tracked

| Metric | Type | Purpose |
|--------|------|---------|
| **active_users** | Gauge | Currently logged-in users |
| **ws_connections** | Gauge | WebSocket server connections |
| **tasks_queue_size** | Gauge | Pending tasks (dag, execution, portfolio) |
| **execution_latency** | Gauge | Avg latency per worker type |

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                 SCALING METRICS COLLECTOR                    │
│                                                              │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │ Active      │ │ WebSocket   │ │ Task Queue  │           │
│  │ Users       │ │ Connections │ │ Sizes       │           │
│  │ (Gauge)     │ │ (Gauge)     │ │ (Gauge)     │           │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘           │
│         │               │               │                   │
│         └───────────────┼───────────────┘                   │
│                         │                                   │
│                  ┌──────▼──────┐                            │
│                  │  History    │  ← 60 min history          │
│                  │  Buffer     │                            │
│                  └──────┬──────┘                            │
│                         │                                   │
│                  ┌──────▼──────┐                            │
│                  │  Prometheus │  ← /metrics endpoint        │
│                  │  Export     │                            │
│                  └─────────────┘                            │
└─────────────────────────────────────────────────────────────┘
```

---

## FILES CREATED/UPDATED

| File | Purpose | Lines |
|------|---------|-------|
| `core/scaling_metrics.py` | Scaling metrics collector | 400+ |
| `backend/metrics.py` | Updated with scaling metrics | Modified |
| `SCALING_STEP_8_SUMMARY.md` | This documentation | - |

---

## SCALING METRICS (`core/scaling_metrics.py`)

### Features

- **Real-time tracking**: Gauges updated continuously
- **History**: 60-minute circular buffers for trends
- **Thresholds**: Warning and critical levels
- **Trend analysis**: Increasing/decreasing/stable detection
- **Scaling recommendations**: Auto-generated scaling advice
- **Prometheus format**: Compatible with monitoring stack

### Usage

```python
from core.scaling_metrics import scaling_metrics

# Update metrics
scaling_metrics.set_active_users(147)
scaling_metrics.set_ws_connections(142)
scaling_metrics.set_queue_size("dag", 23)
scaling_metrics.set_queue_size("execution", 5)
scaling_metrics.set_queue_size("portfolio", 12)
scaling_metrics.set_execution_latency("order", 0.45)

# Get Prometheus format
prometheus_text = scaling_metrics.get_prometheus_metrics()

# Get snapshot
snapshot = scaling_metrics.get_snapshot()

# Get scaling recommendation
rec = scaling_metrics.get_scaling_recommendation()
if rec["needs_scaling"]:
    for r in rec["recommendations"]:
        print(f"Scale up: {r['message']}")
```

### Alert Thresholds

| Metric | Warning | Critical | Action |
|--------|---------|----------|--------|
| active_users | 140 | 180 | Add backend instances |
| ws_connections | 140 | 180 | Add WebSocket servers |
| queue_size | 50 | 100 | Add workers |
| execution_latency | 1.0s | 5.0s | Add workers |

### Prometheus Output

```
# HELP active_users_total Current number of active users
# TYPE active_users_total gauge
active_users_total 147

# HELP ws_server_connections_total Current WebSocket server connections
# TYPE ws_server_connections_total gauge
ws_server_connections_total 142

# HELP tasks_queue_size Current number of tasks in queue
# TYPE tasks_queue_size gauge
tasks_queue_size{queue_type="dag"} 23
tasks_queue_size{queue_type="execution"} 5
tasks_queue_size{queue_type="portfolio"} 12

# HELP execution_latency_seconds Average execution latency
# TYPE execution_latency_seconds gauge
execution_latency_seconds{executor="dag"} 0.234
execution_latency_seconds{executor="order"} 0.451
execution_latency_seconds{executor="position"} 0.892
```

---

## METRICS ENDPOINTS

### FastAPI Integration

```python
from fastapi import FastAPI
from core.scaling_metrics import scaling_metrics
from backend.metrics import get_prometheus_metrics

app = FastAPI()

@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    scaling = scaling_metrics.get_prometheus_metrics()
    system = get_prometheus_metrics()
    return f"{scaling}\n\n{system}"

@app.get("/api/scaling/status")
async def scaling_status():
    """Get current scaling status and recommendations."""
    return scaling_metrics.get_scaling_recommendation()

@app.get("/api/scaling/snapshot")
async def scaling_snapshot():
    """Get current metrics snapshot."""
    return scaling_metrics.get_snapshot()
```

### Example Response

**GET /api/scaling/status**
```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "needs_scaling": true,
  "recommendations": [
    {
      "metric": "active_users",
      "severity": "critical",
      "message": "Scale up: Add more backend instances",
      "current": 182,
      "threshold": 180
    },
    {
      "metric": "queue_dag",
      "severity": "warning",
      "message": "Scale up: Add dag workers",
      "current": 67,
      "threshold": 50
    }
  ],
  "snapshot": {
    "active_users": 182,
    "ws_connections": 145,
    "queue_sizes": {
      "dag": 67,
      "execution": 8,
      "portfolio": 15
    },
    "execution_latencies": {
      "dag": 0.234,
      "order": 0.451,
      "position": 0.892
    }
  }
}
```

---

## MONITORING DASHBOARD

### Grafana Dashboard

```json
{
  "dashboard": {
    "title": "Scaling Metrics",
    "panels": [
      {
        "title": "Active Users",
        "targets": [{"expr": "active_users_total"}]
      },
      {
        "title": "WebSocket Connections",
        "targets": [{"expr": "ws_server_connections_total"}]
      },
      {
        "title": "Task Queue Sizes",
        "targets": [{"expr": "tasks_queue_size"}]
      },
      {
        "title": "Execution Latency",
        "targets": [{"expr": "execution_latency_seconds"}]
      }
    ]
  }
}
```

---

## AUTOMATED SCALING

### Kubernetes HPA Integration

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: backend-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: trading-backend
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Pods
      pods:
        metric:
          name: active_users_total
        target:
          type: AverageValue
          averageValue: "60"
```

### Custom Metrics Adapter

```python
# metrics_adapter.py
from core.scaling_metrics import scaling_metrics

class CustomMetricsAdapter:
    def get_metric(self, metric_name: str):
        if metric_name == "active_users_total":
            return scaling_metrics.get_active_users()
        elif metric_name == "tasks_queue_size":
            return sum(scaling_metrics._queue_sizes.values())
        # ... etc
```

---

## TESTING

### Test Metrics Collection

```python
import asyncio
from core.scaling_metrics import scaling_metrics

async def test_metrics():
    # Set metrics
    scaling_metrics.set_active_users(150)
    scaling_metrics.set_ws_connections(145)
    scaling_metrics.set_queue_size("dag", 30)
    scaling_metrics.set_execution_latency("order", 0.5)
    
    # Get Prometheus format
    print(scaling_metrics.get_prometheus_metrics())
    
    # Get snapshot
    print(scaling_metrics.get_snapshot())
    
    # Get scaling recommendation
    print(scaling_metrics.get_scaling_recommendation())

asyncio.run(test_metrics())
```

### Load Test

```python
async def load_test_metrics():
    """Simulate high load and verify alerting."""
    
    # Set critical values
    scaling_metrics.set_active_users(190)  # > 180 threshold
    scaling_metrics.set_queue_size("dag", 120)  # > 100 threshold
    
    # Check recommendations
    rec = scaling_metrics.get_scaling_recommendation()
    assert rec["needs_scaling"] is True
    assert len(rec["recommendations"]) >= 2
    
    print("[PASS] Scaling alerts working correctly")

asyncio.run(load_test_metrics())
```

---

## EXPECTED RESULTS

### Before (No Scaling Metrics)
- ❌ No visibility into load
- ❌ Reactive scaling (too late)
- ❌ No trend prediction
- ❌ Missed bottlenecks

### After (With Scaling Metrics)
- ✅ Real-time load visibility
- ✅ Proactive scaling decisions
- ✅ Trend analysis (60-min history)
- ✅ Automatic scaling recommendations
- ✅ Prometheus-compatible monitoring

---

## SUMMARY

**Goal:** Track load for 500 users (150 active)

**Step 8 Complete:** ✅
- Active users metric
- WebSocket connections metric
- Task queue sizes metric
- Execution latency metric
- Threshold-based alerting
- Scaling recommendations
- Prometheus format export

**Metrics:**
- active_users_total (gauge)
- ws_server_connections_total (gauge)
- tasks_queue_size (gauge with labels)
- execution_latency_seconds (gauge with labels)

**Status:** Ready for production monitoring
