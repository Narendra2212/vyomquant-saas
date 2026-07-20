# WebSocket Scale Hardening Summary

## Overview
Comprehensive scalability hardening for the algo monitoring WebSocket layer to support:
- 1000+ concurrent users
- Many concurrent bots
- High event throughput

## 1. Subscription Cleanup

### Backend: `backend/ws_event_stream.py`

**Stale Connection Cleanup:**
```python
async def cleanup_stale_connections(self, timeout_seconds: float = 60.0) -> int:
    # Removes connections with no ping received
    # Runs every 30 seconds
```

**Disconnect Cleanup:**
```python
async def unregister_connection(self, connection_id: str) -> bool:
    # Removes from all subscription sets
    # Cleans up tenant connection counts
```

**Backpressure Tracking Cleanup:**
```python
# Clean up dropped_messages and slow_consumer_count for disconnected clients
for cid in list(self._dropped_messages.keys()):
    if cid not in active_ids:
        del self._dropped_messages[cid]
```

## 2. Backpressure Protection

### Bounded Async Queues
```python
max_queue_size: int = 1000  # Per client message queue
```

### Slow Consumer Detection
```python
# Check backpressure
queue_size = client.message_queue.qsize()

if queue_size >= self._max_queue_size:
    # Slow consumer - queue full
    self._dropped_messages[client.connection_id] += 1
    self._slow_consumer_count[client.connection_id] += 1
    
    # Disconnect persistent slow consumers (>100 drops)
    if self._slow_consumer_count[client.connection_id] > 100:
        logger.error(f"Disconnecting slow consumer {client.connection_id}")
        await self.subscriptions.unregister_connection(client.connection_id)
```

### Drop Policy
- Drop messages when queue is full
- Track drop counts per client
- Disconnect clients with >100 drops
- Log at 80% capacity warning

## 3. Memory Hard Limits

### Per-Client Limits
```python
max_channels_per_client: int = 10
max_queue_size: int = 1000
```

### Per-Tenant Limits
```python
max_connections_per_tenant: int = 100
```

### Replay Limits
```python
replay_buffer_size: int = 500        # Events per channel
max_replay_events: int = 500         # Per replay request
max_replay_per_minute: int = 5       # Rate limit
```

### Deduplication Cache
```javascript
maxSize: 5000         // Global event IDs
maxSizePerChannel: 1000  // Per channel
defaultTTL: 300000    // 5 minutes
```

## 4. Rate Limiting

### Replay Request Rate Limiting
```python
# Window-based rate limiting
max_replay_per_minute: int = 5
rate_limit_window_seconds: float = 60.0

async with self._rate_limit_lock:
    rate_data = self._replay_rate_limits.get(conn_id, {"count": 0, "last_replay_time": 0})
    
    # Reset if outside window
    if (now - rate_data["last_replay_time"]) > self._rate_limit_window:
        rate_data = {"count": 0, "last_replay_time": now}
    
    # Check limit
    if rate_data["count"] >= self._max_replay_per_minute:
        await websocket.send(json.dumps({
            "type": "error",
            "message": f"Rate limit exceeded: max {self._max_replay_per_minute} replays per {self._rate_limit_window}s"
        }))
        return
```

### Subscription Spam Protection
```python
if current_subs >= self._max_channels_per_client:
    return {
        "success": False,
        "error": f"Maximum {self._max_channels_per_client} channels allowed per client"
    }
```

## 5. Heartbeat Hardening

### Stale Socket Detection
```python
heartbeat_interval: float = 30.0   # Send ping every 30s
heartbeat_timeout: float = 60.0       # Disconnect if no pong for 60s

async def _heartbeat_loop(self):
    while self._running:
        await asyncio.sleep(self._heartbeat_interval)
        
        for client in connections:
            await self._send_ping(client.websocket)
            
            # Check staleness
            if client.is_stale(self._heartbeat_timeout):
                logger.warning(f"Stale connection {client.connection_id}")
                await self.subscriptions.unregister_connection(client.connection_id)
```

### Missed Pong Detection
```python
def is_stale(self, timeout_seconds: float = 60.0) -> bool:
    """Check if connection is stale (no ping received)."""
    return (time.time() - self.last_ping) > timeout_seconds
```

### Dead Client Cleanup
```python
async def _cleanup_stale_connections(self) -> None:
    while self._running:
        await asyncio.sleep(30)
        removed = await self.subscriptions.cleanup_stale_connections(
            self._heartbeat_timeout
        )
```

## 6. Frontend Cleanup Validation

### Proper useEffect Cleanup Pattern
All components implement proper cleanup:

```javascript
useEffect(() => {
  let unsubscribeSignal = null;
  
  if (typeof wsClient.subscribe === 'function') {
    unsubscribeSignal = wsClient.subscribe('signal_trace', handleSignal);
  }
  
  return () => {
    if (unsubscribeSignal) unsubscribeSignal();
  };
}, [wsClient]);
```

### Components Validated
- ✅ `WebSocketReconnectManager.jsx` - Proper disconnect on unmount
- ✅ `BotMonitoringConsole.jsx` - Unsubscribe on cleanup
- ✅ `SignalTraceVisualization.jsx` - Multiple subscriptions cleaned
- ✅ `SignalTracePanel.jsx` - Event listeners removed
- ✅ `StrategyDashboard.jsx` - Strategy unsubscribe
- ✅ `StrategyControlPanel.jsx` - Cleanup on unmount
- ✅ `StrategyBuilder.jsx` - DAG updates unsubscribe
- ✅ `PositionPanel.jsx` - Position/orders unsubscribe
- ✅ `PortfolioSyncStatus.jsx` - Portfolio/positions cleanup
- ✅ `PortfolioPanel.jsx` - PnL unsubscribe
- ✅ `OrderLifecyclePanel.jsx` - Orders cleanup
- ✅ `NotificationsPage.jsx` - Multiple alert unsubscribes
- ✅ `LiveRiskAlerts.jsx` - Risk events unsubscribe
- ✅ `LatencyMonitor.jsx` - Execution/db latency cleanup
- ✅ `KillSwitchBanner.jsx` - Kill switch unsubscribe
- ✅ `InfrastructureOperations.jsx` - Health/metrics/alert cleanup
- ✅ `EventLogPanel.jsx` - Events unsubscribe

## 7. Connection Layer Limits

### Tenant Connection Limits
```python
# Check tenant connection limits
tenant_conns = await self.subscriptions.get_tenant_connections(tenant_id)
if len(tenant_conns) >= self._max_connections_per_tenant:
    await websocket.send(json.dumps({
        "type": "error",
        "message": f"Maximum {self._max_connections_per_tenant} connections allowed per tenant"
    }))
    await websocket.close(1013, "Tenant connection limit exceeded")
```

## Summary of Limits

| Resource | Limit | Action on Exceed |
|----------|-------|-----------------|
| Channels per client | 10 | Reject subscription |
| Connections per tenant | 100 | Reject connection (1013) |
| Replay per minute | 5 | Rate limit error |
| Message queue per client | 1000 | Drop messages |
| Slow consumer drops | 100 | Disconnect client |
| Replay buffer per channel | 500 | LRU eviction |
| Dedup cache global | 5000 | LRU eviction |
| Heartbeat timeout | 60s | Disconnect stale |

## Monitoring

### Stats Endpoint
```python
def get_stats(self) -> Dict[str, Any]:
    return {
        "active_connections": len(self.subscriptions._connections),
        "subscriptions_by_channel": {...},
        "dropped_messages": dict(self._dropped_messages),
        "slow_consumers": dict(self._slow_consumer_count),
        "rate_limited_replays": len(self._replay_rate_limits)
    }
```

---

**WEBSOCKET SCALE HARDENING COMPLETE**
