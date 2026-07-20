# DAG Task Queue System

**Date:** May 1, 2026

---

## Overview

Built a **production-grade task queue system** for DAG execution with per-user isolation and concurrent task limiting.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DAG TASK QUEUE SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  SUBMIT TASK                                                                │
│  POST /api/dag/tasks/submit                                                │
│  ├── Hard quota enforcement (sessions, nodes, symbols)                     │
│  ├── Create task: task_id, tenant_id, priority                            │
│  └── Add to tenant queue: user:{id}:dag:task_queue                         │
│                                                                              │
│                               ↓                                            │
│                                                                              │
│  REDIS QUEUE (Sorted Set)                                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  user:user_123:dag:task_queue                                      │    │
│  │                                                                      │    │
│  │  Score = priority * 10^12 + timestamp                               │    │
│  │  task_abc: 5000000000000 (priority 5)                               │    │
│  │  task_xyz: 3000000000000 (priority 3) ← First                         │    │
│  │  task_def: 1000000000000 (priority 1) ← Critical                     │    │
│  └──────────────────────────────────┬───────────────────────────────────┘    │
│                                     │                                        │
│                                     ▼                                        │
│  WORKER CLAIMS TASK                                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  DAGWorker(worker-1)                                                 │    │
│  │  1. Poll queue (every 1s)                                           │    │
│  │  2. Check concurrency: user:{id}:dag:tasks:active < 3              │    │
│  │  3. Claim: ZREM task from queue                                       │    │
│  │  4. Execute with progress updates                                     │    │
│  │  5. Complete: SREM from active set                                      │    │
│  └──────────────────────────────────┬───────────────────────────────────┘    │
│                                     │                                        │
│                                     ▼                                        │
│  TASK LIFECYCLE                                                             │
│  PENDING → ASSIGNED → RUNNING → COMPLETED/FAILED/CANCELLED                  │
│                                                                              │
│  Progress: 0% → 10% → 50% → 90% → 100%                                      │
│  Status Updates: WebSocket or polling via GET /tasks/status/{id}            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture

### Key Features

| Feature | Implementation | Benefit |
|---------|---------------|---------|
| **Per-User Queues** | `user:{id}:dag:task_queue` | Tenant isolation |
| **Priority Scheduling** | Score = priority × 10¹² + timestamp | Critical tasks first |
| **Concurrent Limits** | `user:{id}:dag:tasks:active` set | Max 3 per tenant |
| **Fair Scheduling** | Round-robin across tenants | Prevents starvation |
| **Retry Logic** | Exponential backoff (2ⁿ seconds) | Auto-recovery |
| **Dead Letter Queue** | `dag:task_queue:dead_letter` | Failed task inspection |

---

## Task Lifecycle

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐
│  PENDING │───▶│ ASSIGNED  │───▶│ RUNNING  │───▶│COMPLETED │
└──────────┘    └───────────┘    └──────────┘    └──────────┘
     │              │               │               │
     │              │               │               │
     ▼              ▼               ▼               ▼
  Queue         Worker           Progress        Results
  (Sorted       claims           updates         stored
  Set)          task             (0-100%)        in Redis

Alternate paths:
  RUNNING ──▶ CANCELLED (user cancel)
  RUNNING ──▶ FAILED ──▶ RETRY (if retry_count < max)
  RUNNING ──▶ FAILED ──▶ DEAD_LETTER (max retries exceeded)
```

---

## Redis Key Structure

```
# Task Queues
user:{tenant_id}:dag:task_queue           # Sorted set: pending tasks
user:{tenant_id}:dag:tasks:active        # Set: running task IDs
dag:global_task_queue                     # Alternative: global queue
dag:task_queue:scheduled                  # Sorted set: future tasks
dag:task_queue:dead_letter                # List: failed permanently

# Task Data
task:{task_id}:data                       # Hash: full task config
task:{task_id}:status                     # String: current status
task:{task_id}:progress                   # String: 0-100
task:{task_id}:result                     # Hash: execution results
task:{task_id}:logs                       # List: execution logs

# Worker Management
dag:workers:registry                      # Set: active workers
dag:worker:{worker_id}:tasks              # Set: tasks per worker

# Tenant History
user:{tenant_id}:dag:tasks:history        # List: completed tasks
```

---

## API Endpoints

### Submit Task

```http
POST /api/dag/tasks/submit
Content-Type: application/json
Authorization: Bearer {token}

{
  "dag_config": {
    "nodes": [...],
    "edges": [...],
    "symbols": ["BTCUSDT", "ETHUSDT"]
  },
  "priority": 3,
  "scheduled_for": null
}

Response:
{
  "task_id": "task_abc123",
  "tenant_id": "user_123",
  "status": "pending",
  "queue_position": 2,
  "estimated_start": null
}
```

### Get Task Status

```http
GET /api/dag/tasks/status/{task_id}
Authorization: Bearer {token}

Response:
{
  "task_id": "task_abc123",
  "status": "running",
  "progress": 65.0,
  "created_at": "2026-05-01T12:00:00",
  "started_at": "2026-05-01T12:00:05",
  "completed_at": null,
  "retry_count": 0,
  "result": null,
  "error": null
}
```

### List Tasks

```http
GET /api/dag/tasks/list?status=running&limit=10
Authorization: Bearer {token}

Response:
{
  "tasks": [...],
  "total": 15,
  "pending": 3,
  "running": 2,
  "completed": 10
}
```

### Cancel Task

```http
POST /api/dag/tasks/cancel/{task_id}
Authorization: Bearer {token}

Response:
{
  "task_id": "task_abc123",
  "cancelled": true,
  "message": "Task cancelled successfully"
}
```

### Queue Statistics

```http
GET /api/dag/tasks/stats
Authorization: Bearer {token}

Response:
{
  "tenant_id": "user_123",
  "pending_count": 5,
  "running_count": 2,
  "completed_count": 50,
  "failed_count": 3,
  "dead_letter_count": 0,
  "max_concurrent": 3
}
```

### Worker Management (Admin)

```http
GET /api/dag/tasks/workers
Authorization: Bearer {admin_token}

Response:
{
  "total_workers": 4,
  "active_workers": 4,
  "idle_workers": 2,
  "busy_workers": 2
}

POST /api/dag/tasks/workers/start?count=4
POST /api/dag/tasks/workers/stop
```

### WebSocket Progress Updates

```javascript
// Client-side
const ws = new WebSocket(
  'wss://api.example.com/api/dag/tasks/ws/{task_id}'
);

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  
  if (data.type === 'progress') {
    console.log(`Progress: ${data.data.progress}%`);
    updateProgressBar(data.data.progress);
  }
  
  if (data.type === 'final') {
    console.log('Task completed:', data.data);
    ws.close();
  }
};
```

---

## Usage Examples

### Example 1: Submit and Monitor Task

```python
import requests
import asyncio

async def run_dag_with_monitoring():
    # Submit task
    response = requests.post(
        "http://api.example.com/api/dag/tasks/submit",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "dag_config": {
                "nodes": [
                    {"id": "input", "type": "input"},
                    {"id": "rsi", "type": "indicator", "params": {"period": 14}},
                    {"id": "signal", "type": "logic"},
                ],
                "edges": [
                    {"from": "input", "to": "rsi"},
                    {"from": "rsi", "to": "signal"},
                ],
                "symbols": ["BTCUSDT"],
                "timeframe": "1h",
            },
            "priority": 3,  # High priority
        }
    )
    
    task = response.json()
    task_id = task["task_id"]
    print(f"Task submitted: {task_id}")
    
    # Poll for status
    while True:
        status_resp = requests.get(
            f"http://api.example.com/api/dag/tasks/status/{task_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        status = status_resp.json()
        
        print(f"Status: {status['status']}, Progress: {status.get('progress', 0)}%")
        
        if status['status'] in ['completed', 'failed', 'cancelled']:
            break
        
        await asyncio.sleep(2)
    
    return status

# Run
result = asyncio.run(run_dag_with_monitoring())
print(f"Final result: {result['result']}")
```

### Example 2: Scheduled Task

```python
from datetime import datetime, timedelta

# Schedule task for 1 hour from now
scheduled_time = datetime.utcnow() + timedelta(hours=1)

response = requests.post(
    "http://api.example.com/api/dag/tasks/submit",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "dag_config": {...},
        "priority": 5,
        "scheduled_for": scheduled_time.isoformat(),
    }
)

task_id = response.json()["task_id"]
print(f"Task scheduled for {scheduled_time}: {task_id}")
```

### Example 3: WebSocket Real-Time Updates

```python
import asyncio
import websockets
import json

async def monitor_task_websocket(task_id, token):
    uri = f"wss://api.example.com/api/dag/tasks/ws/{task_id}"
    
    async with websockets.connect(uri) as ws:
        # Authenticate
        await ws.send(json.dumps({"token": token}))
        
        async for message in ws:
            data = json.loads(message)
            
            if data["type"] == "progress":
                progress = data["data"]["progress"]
                print(f"\rProgress: {progress}%", end="", flush=True)
                
            elif data["type"] == "final":
                print(f"\nTask completed: {data['data']['status']}")
                if data['data'].get('result'):
                    print(f"Result: {data['data']['result']}")
                break

# Run
asyncio.run(monitor_task_websocket("task_abc123", token))
```

### Example 4: Batch Task Submission

```python
import asyncio

async def submit_batch_dags():
    """Submit multiple DAGs with different priorities."""
    
    symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT"]
    tasks = []
    
    for i, symbol in enumerate(symbols):
        response = requests.post(
            "http://api.example.com/api/dag/tasks/submit",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "dag_config": {
                    "nodes": [...],
                    "symbols": [symbol],
                },
                "priority": i + 5,  # Increasing priority
            }
        )
        tasks.append(response.json()["task_id"])
    
    print(f"Submitted {len(tasks)} tasks")
    
    # Wait for all to complete
    while True:
        all_completed = True
        for task_id in tasks:
            status = requests.get(
                f"http://api.example.com/api/dag/tasks/status/{task_id}",
                headers={"Authorization": f"Bearer {token}"}
            ).json()
            
            if status["status"] not in ["completed", "failed", "cancelled"]:
                all_completed = False
                break
        
        if all_completed:
            break
        
        await asyncio.sleep(2)
    
    print("All tasks completed!")

asyncio.run(submit_batch_dags())
```

---

## Integration with Quota System

The task queue **hard enforces quotas** at submission time:

```python
@router.post("/submit")
async def submit_task(request, tenant):
    ctx = EnforcementContext(tenant=tenant, operation="submit_dag_task")
    
    # Hard quota checks
    await hard_quota_enforcer.enforce_dag_session_limit(tenant, ctx)
    await hard_quota_enforcer.enforce_dag_node_limit(tenant, len(nodes), ctx)
    await hard_quota_enforcer.enforce_symbol_limit(tenant, len(symbols), ctx)
    
    # If all pass, submit to queue
    task = await dag_task_queue.submit_task(tenant, dag_config)
    return task
```

**Benefit:** Even if API is called directly (bypassing middleware), quotas are enforced.

---

## Worker Pool

### Configuration

```python
# backend/dag_worker.py
worker_pool = WorkerPool(num_workers=4)

# Each worker:
# - Polls queues every 1 second
# - Processes one task at a time
# - Updates progress (0-100%)
# - Handles retries and errors
```

### Worker Lifecycle

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  IDLE   │───▶│ POLLING │───▶│CLAIMING │───▶│RUNNING  │
└─────────┘    └─────────┘    └─────────┘    └─────────┘
   ▲                                           │
   └───────────────────────────────────────────┘
                    (task complete/failed)
```

---

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `core/dag_task_queue.py` | ~700 | Task queue manager with Redis backend |
| `backend/dag_worker.py` | ~250 | Async worker for task execution |
| `routers/dag_tasks.py` | ~350 | API endpoints for task management |
| `DAG_TASK_QUEUE.md` | ~450 | Documentation (this file) |

---

## Summary

### ✅ Features Implemented

- ✅ **Per-user queues** with Redis sorted sets
- ✅ **Priority scheduling** (1=critical, 10=background)
- ✅ **Max 3 concurrent tasks** per user (configurable)
- ✅ **Fair scheduling** across tenants
- ✅ **Retry logic** with exponential backoff
- ✅ **Dead letter queue** for failed tasks
- ✅ **Progress tracking** (0-100%)
- ✅ **WebSocket updates** for real-time monitoring
- ✅ **Hard quota enforcement** at submission
- ✅ **Task cancellation** support

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/submit` | POST | Submit DAG task |
| `/status/{id}` | GET | Get task status |
| `/list` | GET | List tenant tasks |
| `/cancel/{id}` | POST | Cancel task |
| `/stats` | GET | Queue statistics |
| `/workers` | GET | Worker stats (admin) |
| `/ws/{id}` | WS | Real-time progress |

**Status: DAG Task Queue System Complete**
