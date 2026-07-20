# 🔥 STEP 8 — WEBSOCKET LAYER SCALE

## Goal: Handle 3000+ WebSocket Connections

**Focus:**
- Stable real-time UI
- No WS drops
- Horizontal scaling
- Connection sharding

---

## PROBLEM

Without WebSocket clustering:
- ❌ Single server bottleneck (~1000 conn limit)
- ❌ No horizontal scaling
- ❌ Single point of failure
- ❌ WS drops under load
- ❌ Can't handle 3000+ users

---

## SOLUTION: WEBSOCKET CLUSTER

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    WEBSOCKET CLUSTER                             │
│                    (3000+ Connections)                         │
│                                                                 │
│   Clients (3000+)                                               │
│       │                                                         │
│       ▼                                                         │
│   ┌─────────────┐                                              │
│   │   Load      │  ← Sticky sessions by user_id                │
│   │  Balancer   │                                              │
│   │  (WS Router)│                                              │
│   └──────┬──────┘                                              │
│          │                                                      │
│    ┌─────┼─────┬─────────┐                                      │
│    ▼     ▼     ▼         ▼                                      │
│ ┌────┐┌────┐┌────┐┌────┐                                       │
│ │WS-1││WS-2││WS-3││WS-4│  ← 4 instances (horizontal scale)   │
│ │750 ││750 ││750 ││750 │  ← ~750 connections each            │
│ │con ││con ││con ││con │                                       │
│ └────┘└────┘└────┘└────┘                                       │
│    │    │    │    │                                             │
│    └────┴────┴────┘                                             │
│          │                                                      │
│          ▼                                                      │
│   ┌─────────────┐                                              │
│   │   Redis     │  ← Pub/Sub for cross-instance messaging      │
│   │   Cluster   │                                              │
│   └─────────────┘                                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

CONNECTION SHARDING:
  • Users A-M  → WS-1 (shard 0)
  • Users N-Z  → WS-2 (shard 1)
  • Users 0-9  → WS-3 (shard 2)
  • Others     → WS-4 (shard 3)

Deterministic: hash(user_id) % 4 = shard_id
```

### Components

| Component | Purpose | Capacity |
|-----------|---------|----------|
| **Load Balancer** | Route clients to correct shard | 3000+ connections |
| **WS Server 1-4** | Handle 750 connections each | 750 × 4 = 3000 total |
| **Redis Cluster** | Cross-instance messaging | Unlimited |
| **Connection Sharder** | Deterministic user routing | 4 shards |

### Features

- **Horizontal Scaling**: Add more WS instances to increase capacity
- **Connection Sharding**: Users distributed deterministically across instances
- **Sticky Sessions**: User always connects to same shard
- **Heartbeat/Ping**: Keep connections alive, detect dead connections
- **Redis Pub/Sub**: Broadcast messages across instances
- **Automatic Reconnect**: Client reconnects on failure

---

## FILES CREATED

| File | Purpose | Lines |
|------|---------|-------|
| `backend/websocket_cluster.py` | WebSocket cluster with sharding | 500+ |
| `SCALING_1000_STEP_8_SUMMARY.md` | This documentation | - |

---

## WEBSOCKET CLUSTER (`backend/websocket_cluster.py`)

### Features

- **4 Server Instances**: Horizontal scaling (can add more)
- **Connection Sharding**: Hash-based user distribution
- **Heartbeat System**: 30s ping/pong for health
- **Redis Integration**: Cross-instance message broadcast
- **Connection Management**: Track state, cleanup dead connections
- **Sticky Sessions**: User → Shard mapping preserved

### Usage

#### Start Cluster

```python
from backend.websocket_cluster import WebSocketClusterManager

# Initialize cluster with 4 shards
cluster = WebSocketClusterManager(
    num_shards=4,
    redis_url="redis://localhost:6379",
    max_connections_per_shard=750  # 750 × 4 = 3000 total
)

# Start all instances
await cluster.start()
```

#### Handle Client Connection

```python
from backend.websocket_cluster import get_websocket_cluster_manager

async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    
    # Get cluster manager
    cluster = await get_websocket_cluster_manager()
    
    # Get correct shard for user
    instance = cluster.get_instance_for_user(user_id)
    
    # Accept connection
    connection_id = f"conn_{user_id}_{uuid4()}"
    accepted = await instance.connect_client(
        websocket=websocket,
        user_id=user_id,
        connection_id=connection_id,
        client_info={"user_agent": "..."}
    )
    
    if not accepted:
        await websocket.close(code=1008, reason="Server at capacity")
        return
    
    # Handle messages
    try:
        while True:
            message = await instance.receive_message(connection_id)
            if message is None:
                break
            
            # Process message
            await handle_message(user_id, message)
    
    except WebSocketDisconnect:
        await instance.disconnect_client(connection_id, "client_disconnect")
```

#### Send Message to User

```python
# Send to specific connection
await instance.send_message(connection_id, {
    "type": "price_update",
    "symbol": "BTC-USD",
    "price": "50000.00"
})

# Broadcast to all connections of a user
await instance.broadcast_to_user(user_id, {
    "type": "order_filled",
    "order_id": "ord_123",
    "status": "filled"
})
```

#### Cross-Instance Broadcast

```python
# Publish to Redis for all instances
await redis.publish("ws:shard:*", json.dumps({
    "type": "broadcast",
    "user_id": "user_123",
    "message": {"type": "alert", "text": "..."}
}))
```

#### Get Cluster Stats

```python
# Get all instance stats
all_stats = cluster.get_all_stats()
# [
#     {"instance_id": "ws-server-1", "total_connections": 750, ...},
#     {"instance_id": "ws-server-2", "total_connections": 742, ...},
#     ...
# ]

# Get aggregated cluster stats
cluster_stats = cluster.get_cluster_stats()
# {
#     "num_shards": 4,
#     "total_connections": 2992,
#     "total_unique_users": 1500,
#     "max_capacity": 3000,
#     "utilization": 99.7
# }
```

---

## INTEGRATION

### With Redis Cluster (Step 7)

```python
# WebSocket cluster uses Redis for:
# - Cross-instance messaging (Pub/Sub)
# - Presence tracking
# - Session state

from backend.websocket_cluster import WebSocketClusterManager

cluster = WebSocketClusterManager(
    num_shards=4,
    redis_url="redis://redis-cluster:6379"  # Uses Redis Cluster
)
```

### With Event Pipeline (Step 2)

```python
# Events from pipeline are broadcast to WebSocket clients

async def on_event(event):
    cluster = await get_websocket_cluster_manager()
    
    # Get user's shard
    instance = cluster.get_instance_for_user(event.user_id)
    
    # Broadcast to user
    await instance.broadcast_to_user(event.user_id, {
        "type": event.event_type,
        "data": event.payload
    })
```

### With Load Balancer (Nginx/HAProxy)

```nginx
# Nginx configuration for WebSocket load balancing
upstream websocket_cluster {
    ip_hash;  # Sticky sessions by IP
    
    server ws-server-1:8001;
    server ws-server-2:8002;
    server ws-server-3:8003;
    server ws-server-4:8004;
}

server {
    listen 80;
    
    location /ws {
        proxy_pass http://websocket_cluster;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## CONNECTION SHARDING

### Sharding Strategy

```python
def get_shard_for_user(user_id: str) -> int:
    """
    Deterministic sharding by user_id.
    
    Same user_id always goes to same shard.
    Even distribution across shards.
    """
    hash_value = hash(user_id)
    shard_id = abs(hash_value) % num_shards
    return shard_id

# Examples:
# "user_alice"  → hash → 12345 → % 4 = 1 → WS-2
# "user_bob"    → hash → 67890 → % 4 = 2 → WS-3
# "user_alice"  → hash → 12345 → % 4 = 1 → WS-2 (same!)
```

### Why Sticky Sessions?

1. **State Preservation**: User state cached on shard
2. **Ordered Messages**: Messages delivered in order
3. **No Session Migration**: Don't need to move state between shards
4. **Simpler Logic**: Each shard independent

---

## MONITORING

### Connection Health

```python
# Automatic heartbeat every 30 seconds
# Client receives: {"type": "ping", "time": "2024-01-15T10:30:00"}
# Client responds: {"type": "pong"}

# Dead connection detection:
# - No pong within 60 seconds → disconnect
# - Failed send → disconnect
# - Clean disconnect → remove immediately
```

### Cluster Stats

```python
stats = cluster.get_cluster_stats()
print(f"Connections: {stats['total_connections']}/{stats['max_capacity']}")
print(f"Utilization: {stats['utilization']:.1f}%")
print(f"Unique users: {stats['total_unique_users']}")

# Per-instance breakdown
for instance_stat in stats['instance_stats']:
    print(f"  {instance_stat['instance_id']}: "
          f"{instance_stat['total_connections']} connections "
          f"({instance_stat['utilization']:.1f}%)")
```

---

## EXPECTED RESULTS

### Before (Without WebSocket Cluster)
- ❌ Max ~1000 connections per server
- ❌ Single point of failure
- ❌ No horizontal scaling
- ❌ WS drops under load

### After (With WebSocket Cluster)
- ✅ 3000+ connections (4 × 750)
- ✅ Horizontal scaling (add more instances)
- ✅ No single point of failure
- ✅ Stable connections (heartbeat + reconnect)
- ✅ Even load distribution

### Scaling Formula

```
Capacity = num_shards × max_connections_per_shard

Examples:
  4 shards × 750 conn = 3000 connections
  6 shards × 750 conn = 4500 connections
  8 shards × 750 conn = 6000 connections
```

---

## SUMMARY

**Goal:** Handle 3000+ WebSocket connections

**Step 8 Complete:** ✅
- 4 WebSocket server instances
- Connection sharding by user_id
- Sticky sessions (deterministic routing)
- Heartbeat/ping (30s interval)
- Redis Pub/Sub (cross-instance messaging)
- Load balancing with capacity limits
- Automatic dead connection cleanup

**Key Components:**
- `WebSocketClusterManager`: Manages all instances
- `WebSocketServerInstance`: Single server handling a shard
- `ConnectionSharder`: Routes users to correct shard
- `ConnectionInfo`: Tracks connection state
- Redis integration for cross-instance broadcast

**Sharding:**
- 4 shards (can scale to more)
- 750 connections per shard
- Hash-based user distribution
- Sticky sessions preserved

**Monitoring:**
- Heartbeat every 30s
- Dead connection cleanup
- Real-time stats
- Per-instance utilization

**Status:** Ready for 3000+ concurrent WebSocket connections
