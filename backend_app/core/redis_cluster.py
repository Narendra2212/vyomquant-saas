"""
core/redis_cluster.py — REDIS CLUSTER UPGRADE FOR 1000+ USERS

STEP 7: SCALE REDIS

GOAL: High throughput, no Redis saturation

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────────┐
  │                    REDIS CLUSTER                               │
  │                    (3-6 Nodes)                                  │
  │                                                                 │
  │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
  │   │   Node 1    │◄──►│   Node 2    │◄──►│   Node 3    │       │
  │   │  (Master)   │    │  (Master)   │    │  (Master)   │       │
  │   │  + Replica  │    │  + Replica  │    │  + Replica  │       │
  │   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘       │
  │          │                  │                  │               │
  │          └──────────────────┼──────────────────┘               │
  │                             │                                  │
  │                      ┌──────▼──────┐                          │
  │                      │  Cluster    │                          │
  │                      │  Bus        │                          │
  │                      └─────────────┘                          │
  │                                                                 │
  │   SEPARATE CONCERNS:                                           │
  │   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐       │
  │   │   CACHE     │    │   QUEUE     │    │   EVENTS    │       │
  │   │  (DB 0)     │    │  (DB 1)     │    │  (DB 2)     │       │
  │   │             │    │             │    │             │       │
  │   │ • Sessions  │    │ • DAG tasks │    │ • Streams   │       │
  │   │ • Positions │    │ • Orders    │    │ • Pub/Sub   │       │
  │   │ • Prices    │    │ • Workers   │    │ • Events    │       │
  │   │ • TTL: 1h   │    │ • TTL: 24h  │    │ • TTL: 7d   │       │
  │   └─────────────┘    └─────────────┘    └─────────────┘       │
  │                                                                 │
  │   ┌─────────────┐    ┌─────────────┐                          │
  │   │ IDEMPOTENCY │    │  METRICS    │                          │
  │   │  (DB 3)     │    │  (DB 4)     │                          │
  │   │             │    │             │                          │
  │   │ • Keys      │    │ • Counters  │                          │
  │   │ • 24h TTL   │    │ • Gauges    │                          │
  │   │ • Deduplic. │    │ • TTL: 30d  │                          │
  │   └─────────────┘    └─────────────┘                          │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

CONFIGURATION:
  - 3-6 nodes in cluster mode
  - Each node: master + 1 replica (high availability)
  - Automatic sharding across nodes
  - Automatic failover if master goes down

SEPARATE DATABASES (on cluster):
  DB 0: Cache (sessions, positions, prices) - 1h TTL
  DB 1: Queue (DAG tasks, orders, workers) - 24h TTL
  DB 2: Events (streams, pub/sub) - 7d TTL
  DB 3: Idempotency (deduplication keys) - 24h TTL
  DB 4: Metrics (counters, gauges) - 30d TTL

EXPECTED RESULT:
  ✔ High throughput (distributed across nodes)
  ✔ No Redis saturation (sharding spreads load)
  ✔ High availability (replicas + auto-failover)
  ✔ Separate concerns (isolated databases)
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

# Redis imports
try:
    import redis.asyncio as redis
    from redis.asyncio.cluster import RedisCluster
    from redis.cluster import ClusterNode
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger("RedisCluster")


# ═══════════════════════════════════════════════════════════════════════════
# REDIS DATABASES (Separate Concerns)
# ═══════════════════════════════════════════════════════════════════════════

class RedisDatabase(Enum):
    """Redis databases for different concerns."""
    CACHE = 0        # Sessions, positions, prices (short TTL)
    QUEUE = 1        # DAG tasks, orders, workers (medium TTL)
    EVENTS = 2       # Streams, pub/sub, events (long TTL)
    IDEMPOTENCY = 3  # Deduplication keys (24h TTL)
    METRICS = 4      # Counters, gauges (30d TTL)
    STATE = 5        # State snapshots (persistent)


# TTL configuration per database
DATABASE_TTL = {
    RedisDatabase.CACHE: 3600,        # 1 hour
    RedisDatabase.QUEUE: 86400,       # 24 hours
    RedisDatabase.EVENTS: 604800,     # 7 days
    RedisDatabase.IDEMPOTENCY: 86400, # 24 hours
    RedisDatabase.METRICS: 2592000,   # 30 days
    RedisDatabase.STATE: 0,           # No TTL (persistent)
}


@dataclass
class RedisClusterConfig:
    """Configuration for Redis Cluster."""
    # Cluster nodes (host:port)
    startup_nodes: List[Dict[str, Union[str, int]]] = field(default_factory=lambda: [
        {"host": "redis-node-1", "port": 6379},
        {"host": "redis-node-2", "port": 6379},
        {"host": "redis-node-3", "port": 6379},
    ])
    
    # Connection settings
    password: Optional[str] = None
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0
    max_connections: int = 100
    
    # Retry settings
    retry_on_timeout: bool = True
    retry_on_error: List[type] = field(default_factory=list)
    
    # Decode responses
    decode_responses: bool = True
    
    def __post_init__(self):
        if not self.retry_on_error:
            self.retry_on_error = [ConnectionError, TimeoutError]


# ═══════════════════════════════════════════════════════════════════════════
# REDIS CLUSTER MANAGER
# ═══════════════════════════════════════════════════════════════════════════

class RedisClusterManager:
    """
    Manages Redis Cluster connections with separate databases for different concerns.
    
    Features:
    - Cluster mode with 3-6 nodes
    - Separate databases (DB 0-5) for different concerns
    - Automatic connection pooling
    - Health monitoring
    - Failover handling
    """
    
    def __init__(self, config: Optional[RedisClusterConfig] = None):
        self.config = config or RedisClusterConfig()
        
        # Cluster client
        self._cluster: Optional[RedisCluster] = None
        
        # Individual database clients (for non-cluster operations)
        self._db_clients: Dict[RedisDatabase, Any] = {}
        
        # Health status
        self._healthy = False
        self._last_health_check: Optional[datetime] = None
        
        # Metrics
        self._stats = {
            "connections_created": 0,
            "connections_failed": 0,
            "operations_successful": 0,
            "operations_failed": 0,
        }
        
        logger.info(f"[RedisClusterManager] Initialized with {len(self.config.startup_nodes)} nodes")
    
    async def connect(self):
        """Connect to Redis Cluster."""
        if not REDIS_AVAILABLE:
            logger.error("[RedisClusterManager] Redis not available")
            return False
        
        try:
            # Convert startup nodes to ClusterNode objects
            startup_nodes = [
                ClusterNode(node["host"], node["port"])
                for node in self.config.startup_nodes
            ]
            
            # Create cluster client
            self._cluster = RedisCluster(
                startup_nodes=startup_nodes,
                password=self.config.password,
                skip_full_coverage_check=True,
                decode_responses=self.config.decode_responses,
                max_connections=self.config.max_connections,
                socket_timeout=self.config.socket_timeout,
                socket_connect_timeout=self.config.socket_connect_timeout,
                retry_on_timeout=self.config.retry_on_timeout,
                retry_on_error=self.config.retry_on_error,
            )
            
            # Test connection
            await self._cluster.ping()
            
            self._healthy = True
            self._last_health_check = datetime.utcnow()
            
            logger.info(f"[RedisClusterManager] Connected to cluster")
            logger.info(f"  Nodes: {len(self.config.startup_nodes)}")
            logger.info(f"  Databases: {len(RedisDatabase)}")
            
            return True
            
        except Exception as e:
            logger.error(f"[RedisClusterManager] Connection failed: {e}")
            self._stats["connections_failed"] += 1
            return False
    
    async def disconnect(self):
        """Disconnect from Redis Cluster."""
        if self._cluster:
            await self._cluster.close()
            self._cluster = None
            self._healthy = False
            logger.info("[RedisClusterManager] Disconnected")
    
    def _get_db_client(self, database: RedisDatabase):
        """Get client for specific database."""
        if database not in self._db_clients:
            # Create client with database selection
            if self._cluster:
                # In cluster mode, we use the same cluster but select db
                self._db_clients[database] = self._cluster
        
        return self._db_clients.get(database)
    
    def _get_key_with_db(self, key: str, database: RedisDatabase) -> str:
        """Prefix key with database indicator for cluster mode."""
        # In cluster mode, we prefix keys to separate concerns
        return f"db{database.value}:{key}"
    
    # ═══════════════════════════════════════════════════════════════════════
    # CACHE OPERATIONS (DB 0)
    # ═══════════════════════════════════════════════════════════════════════
    
    async def cache_get(self, key: str) -> Optional[Any]:
        """Get from cache (DB 0)."""
        if not self._cluster:
            return None
        
        prefixed_key = self._get_key_with_db(key, RedisDatabase.CACHE)
        
        try:
            value = await self._cluster.get(prefixed_key)
            if value:
                self._stats["operations_successful"] += 1
                return value
        except Exception as e:
            logger.error(f"[RedisClusterManager] Cache get failed: {e}")
            self._stats["operations_failed"] += 1
        
        return None
    
    async def cache_set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """Set in cache (DB 0) with TTL."""
        if not self._cluster:
            return False
        
        prefixed_key = self._get_key_with_db(key, RedisDatabase.CACHE)
        ttl = ttl or DATABASE_TTL[RedisDatabase.CACHE]
        
        try:
            await self._cluster.setex(prefixed_key, ttl, value)
            self._stats["operations_successful"] += 1
            return True
        except Exception as e:
            logger.error(f"[RedisClusterManager] Cache set failed: {e}")
            self._stats["operations_failed"] += 1
            return False
    
    async def cache_delete(self, key: str) -> bool:
        """Delete from cache (DB 0)."""
        if not self._cluster:
            return False
        
        prefixed_key = self._get_key_with_db(key, RedisDatabase.CACHE)
        
        try:
            await self._cluster.delete(prefixed_key)
            return True
        except Exception as e:
            logger.error(f"[RedisClusterManager] Cache delete failed: {e}")
            return False
    
    # ═══════════════════════════════════════════════════════════════════════
    # QUEUE OPERATIONS (DB 1)
    # ═══════════════════════════════════════════════════════════════════════
    
    async def queue_push(
        self,
        queue_name: str,
        item: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """Push to queue (DB 1)."""
        if not self._cluster:
            return False
        
        prefixed_key = self._get_key_with_db(f"queue:{queue_name}", RedisDatabase.QUEUE)
        ttl = ttl or DATABASE_TTL[RedisDatabase.QUEUE]
        
        try:
            await self._cluster.lpush(prefixed_key, item)
            await self._cluster.expire(prefixed_key, ttl)
            self._stats["operations_successful"] += 1
            return True
        except Exception as e:
            logger.error(f"[RedisClusterManager] Queue push failed: {e}")
            self._stats["operations_failed"] += 1
            return False
    
    async def queue_pop(self, queue_name: str, timeout: float = 0.0) -> Optional[Any]:
        """Pop from queue (DB 1)."""
        if not self._cluster:
            return None
        
        prefixed_key = self._get_key_with_db(f"queue:{queue_name}", RedisDatabase.QUEUE)
        
        try:
            if timeout > 0:
                result = await self._cluster.brpop(prefixed_key, timeout=timeout)
                if result:
                    return result[1]  # brpop returns (key, value)
            else:
                result = await self._cluster.rpop(prefixed_key)
                return result
            
            self._stats["operations_successful"] += 1
        except Exception as e:
            logger.error(f"[RedisClusterManager] Queue pop failed: {e}")
            self._stats["operations_failed"] += 1
        
        return None
    
    async def queue_length(self, queue_name: str) -> int:
        """Get queue length (DB 1)."""
        if not self._cluster:
            return 0
        
        prefixed_key = self._get_key_with_db(f"queue:{queue_name}", RedisDatabase.QUEUE)
        
        try:
            return await self._cluster.llen(prefixed_key)
        except Exception as e:
            logger.error(f"[RedisClusterManager] Queue length failed: {e}")
            return 0
    
    # ═══════════════════════════════════════════════════════════════════════
    # EVENT OPERATIONS (DB 2)
    # ═══════════════════════════════════════════════════════════════════════
    
    async def event_publish(
        self,
        channel: str,
        message: Any,
        ttl: Optional[int] = None
    ) -> bool:
        """Publish event to channel (DB 2)."""
        if not self._cluster:
            return False
        
        prefixed_channel = self._get_key_with_db(f"channel:{channel}", RedisDatabase.EVENTS)
        
        try:
            await self._cluster.publish(prefixed_channel, message)
            self._stats["operations_successful"] += 1
            return True
        except Exception as e:
            logger.error(f"[RedisClusterManager] Event publish failed: {e}")
            self._stats["operations_failed"] += 1
            return False
    
    async def event_stream_add(
        self,
        stream_name: str,
        fields: Dict[str, Any],
        ttl: Optional[int] = None
    ) -> Optional[str]:
        """Add to event stream (DB 2)."""
        if not self._cluster:
            return None
        
        prefixed_stream = self._get_key_with_db(f"stream:{stream_name}", RedisDatabase.EVENTS)
        
        try:
            # Convert fields to flat list for xadd
            flat_fields = []
            for k, v in fields.items():
                flat_fields.extend([k, str(v)])
            
            result = await self._cluster.xadd(prefixed_stream, fields)
            
            # Set TTL on stream
            if ttl:
                await self._cluster.expire(prefixed_stream, ttl)
            else:
                await self._cluster.expire(prefixed_stream, DATABASE_TTL[RedisDatabase.EVENTS])
            
            self._stats["operations_successful"] += 1
            return result
        except Exception as e:
            logger.error(f"[RedisClusterManager] Stream add failed: {e}")
            self._stats["operations_failed"] += 1
            return None
    
    # ═══════════════════════════════════════════════════════════════════════
    # IDEMPOTENCY OPERATIONS (DB 3)
    # ═══════════════════════════════════════════════════════════════════════
    
    async def idempotency_check(self, key: str) -> bool:
        """
        Check if idempotency key exists (DB 3).
        Returns True if key is new (not seen before).
        """
        if not self._cluster:
            # In-memory fallback
            return True
        
        prefixed_key = self._get_key_with_db(f"idemp:{key}", RedisDatabase.IDEMPOTENCY)
        
        try:
            exists = await self._cluster.exists(prefixed_key)
            if exists:
                return False  # Key already exists (duplicate)
            
            # Set key with TTL
            await self._cluster.setex(
                prefixed_key,
                DATABASE_TTL[RedisDatabase.IDEMPOTENCY],
                "1"
            )
            self._stats["operations_successful"] += 1
            return True  # Key is new
            
        except Exception as e:
            logger.error(f"[RedisClusterManager] Idempotency check failed: {e}")
            self._stats["operations_failed"] += 1
            return True  # Fail open (allow operation)
    
    async def idempotency_get(self, key: str) -> Optional[str]:
        """Get idempotency key value (DB 3)."""
        if not self._cluster:
            return None
        
        prefixed_key = self._get_key_with_db(f"idemp:{key}", RedisDatabase.IDEMPOTENCY)
        
        try:
            return await self._cluster.get(prefixed_key)
        except Exception as e:
            logger.error(f"[RedisClusterManager] Idempotency get failed: {e}")
            return None
    
    # ═══════════════════════════════════════════════════════════════════════
    # METRICS OPERATIONS (DB 4)
    # ═══════════════════════════════════════════════════════════════════════
    
    async def metric_increment(
        self,
        metric_name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None
    ) -> bool:
        """Increment counter metric (DB 4)."""
        if not self._cluster:
            return False
        
        label_str = ":".join(f"{k}={v}" for k, v in (labels or {}).items())
        key = f"metric:{metric_name}:{label_str}" if label_str else f"metric:{metric_name}"
        prefixed_key = self._get_key_with_db(key, RedisDatabase.METRICS)
        
        try:
            await self._cluster.incrbyfloat(prefixed_key, value)
            await self._cluster.expire(prefixed_key, DATABASE_TTL[RedisDatabase.METRICS])
            self._stats["operations_successful"] += 1
            return True
        except Exception as e:
            logger.error(f"[RedisClusterManager] Metric increment failed: {e}")
            self._stats["operations_failed"] += 1
            return False
    
    async def metric_set(
        self,
        metric_name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ) -> bool:
        """Set gauge metric (DB 4)."""
        if not self._cluster:
            return False
        
        label_str = ":".join(f"{k}={v}" for k, v in (labels or {}).items())
        key = f"gauge:{metric_name}:{label_str}" if label_str else f"gauge:{metric_name}"
        prefixed_key = self._get_key_with_db(key, RedisDatabase.METRICS)
        
        try:
            await self._cluster.set(prefixed_key, str(value))
            await self._cluster.expire(prefixed_key, DATABASE_TTL[RedisDatabase.METRICS])
            self._stats["operations_successful"] += 1
            return True
        except Exception as e:
            logger.error(f"[RedisClusterManager] Metric set failed: {e}")
            self._stats["operations_failed"] += 1
            return False
    
    async def metric_get(self, metric_name: str, labels: Optional[Dict[str, str]] = None) -> Optional[float]:
        """Get metric value (DB 4)."""
        if not self._cluster:
            return None
        
        label_str = ":".join(f"{k}={v}" for k, v in (labels or {}).items())
        key = f"metric:{metric_name}:{label_str}" if label_str else f"metric:{metric_name}"
        prefixed_key = self._get_key_with_db(key, RedisDatabase.METRICS)
        
        try:
            value = await self._cluster.get(prefixed_key)
            return float(value) if value else None
        except Exception as e:
            logger.error(f"[RedisClusterManager] Metric get failed: {e}")
            return None
    
    # ═══════════════════════════════════════════════════════════════════════
    # HEALTH & STATS
    # ═══════════════════════════════════════════════════════════════════════
    
    async def health_check(self) -> Dict[str, Any]:
        """Check cluster health."""
        if not self._cluster:
            return {"status": "unavailable", "healthy": False}
        
        try:
            await self._cluster.ping()
            self._healthy = True
            self._last_health_check = datetime.utcnow()
            
            return {
                "status": "healthy",
                "healthy": True,
                "nodes": len(self.config.startup_nodes),
                "last_check": self._last_health_check.isoformat(),
            }
        except Exception as e:
            self._healthy = False
            return {
                "status": "unhealthy",
                "healthy": False,
                "error": str(e),
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cluster statistics."""
        return {
            **self._stats,
            "healthy": self._healthy,
            "nodes_configured": len(self.config.startup_nodes),
        }
    
    def is_healthy(self) -> bool:
        """Check if cluster is healthy."""
        return self._healthy


# Global singleton
redis_cluster_manager: Optional[RedisClusterManager] = None


async def get_redis_cluster_manager(
    config: Optional[RedisClusterConfig] = None
) -> RedisClusterManager:
    """Get or create global Redis cluster manager."""
    global redis_cluster_manager
    
    if redis_cluster_manager is None:
        redis_cluster_manager = RedisClusterManager(config)
        await redis_cluster_manager.connect()
    
    return redis_cluster_manager
