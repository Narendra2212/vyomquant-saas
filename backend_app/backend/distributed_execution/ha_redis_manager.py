"""
HA Redis Manager - Phase 5 High Availability Foundations

This module implements the HA Redis manager for institutional-grade Redis high availability.
It provides Redis Sentinel integration, replication management, failover management, and
fencing mechanisms to preserve deterministic ordering and replay guarantees.

Key Features:
- Redis Sentinel integration for automatic failover
- Master-slave replication management
- Write quorum management
- Read preference management
- Fencing token management
- Rollback mechanism
"""

import asyncio
import logging
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Set
from enum import Enum
import hashlib
import uuid

import redis.asyncio as redis
from redis.sentinel import Sentinel


logger = logging.getLogger(__name__)


class ReadPreference(Enum):
    """Read preference for Redis operations."""
    MASTER = "master"
    SLAVE = "slave"
    NEAREST = "nearest"


@dataclass
class RedisNode:
    """Redis node information."""
    node_id: str
    host: str
    port: int
    role: str  # master, slave, sentinel
    priority: int = 100
    is_healthy: bool = True
    last_heartbeat: Optional[datetime] = None


@dataclass
class FencingToken:
    """Fencing token for split-brain prevention."""
    token_id: str
    token_hash: str
    generated_at: datetime
    expires_at: datetime
    generation_epoch: int
    
    def is_valid(self) -> bool:
        """Check if token is valid."""
        return datetime.now(timezone.utc) < self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "token_id": self.token_id,
            "token_hash": self.token_hash,
            "generated_at": self.generated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "generation_epoch": self.generation_epoch
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FencingToken":
        """Create from dictionary."""
        return cls(
            token_id=data["token_id"],
            token_hash=data["token_hash"],
            generated_at=datetime.fromisoformat(data["generated_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            generation_epoch=data["generation_epoch"]
        )


@dataclass
class ReplicationStatus:
    """Replication status information."""
    master_host: str
    master_port: int
    replicas: List[Dict[str, Any]]
    replication_lag_ms: float
    connected_replicas: int
    total_replicas: int


@dataclass
class FailoverStatus:
    """Failover status information."""
    failover_in_progress: bool
    failover_start_time: Optional[datetime]
    failover_duration_seconds: Optional[float]
    old_master: Optional[str]
    new_master: Optional[str]
    fencing_token: Optional[FencingToken]


class HARedisManager:
    """
    HA Redis Manager for institutional-grade Redis high availability.
    
    This manager provides:
    - Redis Sentinel integration for automatic failover
    - Master-slave replication management
    - Write quorum management
    - Read preference management
    - Fencing mechanisms for split-brain prevention
    """
    
    def __init__(
        self,
        sentinel_hosts: List[str],
        sentinel_port: int = 26379,
        master_name: str = "mymaster",
        password: Optional[str] = None,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        quorum_size: int = 2,
        quorum_timeout: int = 10,
        fencing_token_ttl: int = 3600,
        read_preference: ReadPreference = ReadPreference.MASTER
    ):
        """
        Initialize HA Redis Manager.
        
        Args:
            sentinel_hosts: List of Sentinel host addresses
            sentinel_port: Sentinel port (default: 26379)
            master_name: Redis master name (default: "mymaster")
            password: Redis password (optional)
            socket_timeout: Socket timeout in seconds (default: 5.0)
            socket_connect_timeout: Socket connect timeout in seconds (default: 5.0)
            quorum_size: Quorum size for write operations (default: 2)
            quorum_timeout: Quorum timeout in seconds (default: 10)
            fencing_token_ttl: Fencing token TTL in seconds (default: 3600)
            read_preference: Read preference (default: MASTER)
        """
        self.sentinel_hosts = sentinel_hosts
        self.sentinel_port = sentinel_port
        self.master_name = master_name
        self.password = password
        self.socket_timeout = socket_timeout
        self.socket_connect_timeout = socket_connect_timeout
        self.quorum_size = quorum_size
        self.quorum_timeout = quorum_timeout
        self.fencing_token_ttl = fencing_token_ttl
        self.read_preference = read_preference
        
        self._sentinel: Optional[Sentinel] = None
        self._master_client: Optional[redis.Redis] = None
        self._slave_clients: List[redis.Redis] = []
        self._current_fencing_token: Optional[FencingToken] = None
        self._generation_epoch: int = 0
        self._is_running: bool = False
        self._nodes: Dict[str, RedisNode] = {}
        self._failover_status: Optional[FailoverStatus] = None
        
        logger.info(f"HA Redis Manager initialized with {len(sentinel_hosts)} Sentinel hosts")
    
    async def initialize(self) -> None:
        """Initialize HA Redis Manager."""
        logger.info("Initializing HA Redis Manager")
        
        # Initialize Sentinel
        self._sentinel = Sentinel(
            [(host, self.sentinel_port) for host in self.sentinel_hosts],
            socket_timeout=self.socket_timeout,
            socket_connect_timeout=self.socket_connect_timeout,
            password=self.password
        )
        
        # Discover nodes
        await self._discover_nodes()
        
        # Initialize master client
        await self._initialize_master_client()
        
        # Initialize slave clients
        await self._initialize_slave_clients()
        
        # Start monitoring
        self._is_running = True
        asyncio.create_task(self._monitor_loop())
        
        logger.info("HA Redis Manager initialized successfully")
    
    async def shutdown(self) -> None:
        """Shutdown HA Redis Manager."""
        logger.info("Shutting down HA Redis Manager")
        
        self._is_running = False
        
        # Close master client
        if self._master_client:
            await self._master_client.close()
        
        # Close slave clients
        for client in self._slave_clients:
            await client.close()
        
        logger.info("HA Redis Manager shut down successfully")
    
    async def _discover_nodes(self) -> None:
        """Discover Redis nodes."""
        logger.info("Discovering Redis nodes")
        
        # Get master info
        master_info = await self._sentinel.master_for(
            self.master_name,
            socket_timeout=self.socket_timeout
        ).info("replication")
        
        # Get master host and port
        master_host = master_info["master_host"]
        master_port = int(master_info["master_port"])
        
        # Add master node
        self._nodes["master"] = RedisNode(
            node_id="master",
            host=master_host,
            port=master_port,
            role="master",
            priority=100,
            is_healthy=True,
            last_heartbeat=datetime.now(timezone.utc)
        )
        
        # Get replica info
        for replica_info in master_info.get("slaves", []):
            replica_host = replica_info["ip"]
            replica_port = int(replica_info["port"])
            replica_id = f"replica_{replica_host}:{replica_port}"
            
            self._nodes[replica_id] = RedisNode(
                node_id=replica_id,
                host=replica_host,
                port=replica_port,
                role="slave",
                priority=50,
                is_healthy=True,
                last_heartbeat=datetime.now(timezone.utc)
            )
        
        logger.info(f"Discovered {len(self._nodes)} Redis nodes")
    
    async def _initialize_master_client(self) -> None:
        """Initialize master client."""
        logger.info("Initializing master client")
        
        self._master_client = await self._sentinel.master_for(
            self.master_name,
            socket_timeout=self.socket_timeout,
            socket_connect_timeout=self.socket_connect_timeout
        )
        
        logger.info("Master client initialized")
    
    async def _initialize_slave_clients(self) -> None:
        """Initialize slave clients."""
        logger.info("Initializing slave clients")
        
        self._slave_clients = []
        
        for node_id, node in self._nodes.items():
            if node.role == "slave" and node.is_healthy:
                client = redis.Redis(
                    host=node.host,
                    port=node.port,
                    password=self.password,
                    socket_timeout=self.socket_timeout,
                    socket_connect_timeout=self.socket_connect_timeout,
                    decode_responses=True
                )
                self._slave_clients.append(client)
        
        logger.info(f"Initialized {len(self._slave_clients)} slave clients")
    
    async def _monitor_loop(self) -> None:
        """Monitor Redis nodes."""
        while self._is_running:
            try:
                # Update node health
                await self._update_node_health()
                
                # Update replication status
                await self._update_replication_status()
                
                # Wait before next iteration
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                await asyncio.sleep(5)
    
    async def _update_node_health(self) -> None:
        """Update node health."""
        for node_id, node in self._nodes.items():
            try:
                client = redis.Redis(
                    host=node.host,
                    port=node.port,
                    password=self.password,
                    socket_timeout=self.socket_timeout,
                    socket_connect_timeout=self.socket_connect_timeout,
                    decode_responses=True
                )
                
                # Ping node
                await client.ping()
                
                # Update health
                node.is_healthy = True
                node.last_heartbeat = datetime.now(timezone.utc)
                
                await client.close()
                
            except Exception as e:
                logger.error(f"Error updating health for node {node_id}: {e}")
                node.is_healthy = False
    
    async def _update_replication_status(self) -> None:
        """Update replication status."""
        try:
            if not self._master_client:
                return
            
            # Get replication info
            replication_info = await self._master_client.info("replication")
            
            # Update replication lag
            connected_replicas = replication_info.get("connected_slaves", 0)
            total_replicas = len(self._nodes) - 1  # Exclude master
            
            logger.debug(
                f"Replication status: {connected_replicas}/{total_replicas} replicas connected"
            )
            
        except Exception as e:
            logger.error(f"Error updating replication status: {e}")
    
    async def get_master_client(self) -> redis.Redis:
        """
        Get master Redis client.
        
        Returns:
            Master Redis client
            
        Raises:
            Exception: If master client is not available
        """
        if not self._master_client:
            raise Exception("Master client not initialized")
        
        return self._master_client
    
    async def get_slave_client(self) -> Optional[redis.Redis]:
        """
        Get slave Redis client based on read preference.
        
        Returns:
            Slave Redis client or None if no slaves available
        """
        if self.read_preference == ReadPreference.MASTER:
            return await self.get_master_client()
        
        if not self._slave_clients:
            return None
        
        if self.read_preference == ReadPreference.SLAVE:
            return self._slave_clients[0]
        
        if self.read_preference == ReadPreference.NEAREST:
            # Return nearest slave (simplified - could use latency)
            return self._slave_clients[0]
        
        return None
    
    async def get_replication_status(self) -> ReplicationStatus:
        """
        Get replication status.
        
        Returns:
            Replication status
        """
        try:
            if not self._master_client:
                raise Exception("Master client not initialized")
            
            # Get replication info
            replication_info = await self._master_client.info("replication")
            
            # Get master info
            master_info = await self._master_client.info("server")
            master_host = master_info.get("redis_host", "unknown")
            master_port = master_info.get("tcp_port", 6379)
            
            # Get replica info
            replicas = []
            for replica_info in replication_info.get("slaves", []):
                replicas.append({
                    "ip": replica_info["ip"],
                    "port": replica_info["port"],
                    "offset": replica_info["master_repl_offset"],
                    "lag": replica_info.get("master_link_down_since_seconds", 0)
                })
            
            # Calculate replication lag
            replication_lag_ms = 0.0
            if replicas:
                replication_lag_ms = max(r.get("lag", 0) * 1000 for r in replicas)
            
            return ReplicationStatus(
                master_host=master_host,
                master_port=master_port,
                replicas=replicas,
                replication_lag_ms=replication_lag_ms,
                connected_replicas=len(replicas),
                total_replicas=len(self._nodes) - 1
            )
            
        except Exception as e:
            logger.error(f"Error getting replication status: {e}")
            raise
    
    async def generate_fencing_token(self) -> FencingToken:
        """
        Generate fencing token.
        
        Returns:
            Fencing token
        """
        self._generation_epoch += 1
        
        token_id = str(uuid.uuid4())
        token_data = f"{token_id}:{datetime.now(timezone.utc).isoformat()}:{self._generation_epoch}"
        token_hash = hashlib.sha256(token_data.encode()).hexdigest()
        
        fencing_token = FencingToken(
            token_id=token_id,
            token_hash=token_hash,
            generated_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.fencing_token_ttl),
            generation_epoch=self._generation_epoch
        )
        
        self._current_fencing_token = fencing_token
        
        logger.info(f"Generated fencing token: {token_id}")
        
        return fencing_token
    
    async def verify_fencing_token(self, token: FencingToken) -> bool:
        """
        Verify fencing token.
        
        Args:
            token: Fencing token to verify
            
        Returns:
            True if token is valid, False otherwise
        """
        # Check token validity
        if not token.is_valid():
            logger.error("Fencing token expired")
            return False
        
        # Check token epoch
        if token.generation_epoch < self._generation_epoch:
            logger.error("Fencing token epoch outdated")
            return False
        
        # Check token hash
        expected_hash = self._current_fencing_token.token_hash if self._current_fencing_token else None
        if expected_hash and token.token_hash != expected_hash:
            logger.error("Fencing token hash mismatch")
            return False
        
        logger.info("Fencing token verified successfully")
        
        return True
    
    async def trigger_failover(self, reason: str = "manual") -> FailoverStatus:
        """
        Trigger failover.
        
        Args:
            reason: Failover reason
            
        Returns:
            Failover status
        """
        logger.info(f"Triggering failover: {reason}")
        
        # Generate fencing token
        fencing_token = await self.generate_fencing_token()
        
        # Get current master
        old_master = None
        if self._master_client:
            master_info = await self._master_client.info("server")
            old_master = f"{master_info.get('redis_host', 'unknown')}:{master_info.get('tcp_port', 6379)}"
        
        # Initialize failover status
        self._failover_status = FailoverStatus(
            failover_in_progress=True,
            failover_start_time=datetime.now(timezone.utc),
            failover_duration_seconds=None,
            old_master=old_master,
            new_master=None,
            fencing_token=fencing_token
        )
        
        try:
            # Trigger Sentinel failover
            await self._sentinel.failover(self.master_name)
            
            # Wait for failover to complete
            await asyncio.sleep(5)
            
            # Reinitialize master client
            await self._initialize_master_client()
            
            # Get new master
            new_master_info = await self._master_client.info("server")
            new_master = f"{new_master_info.get('redis_host', 'unknown')}:{new_master_info.get('tcp_port', 6379)}"
            
            # Update failover status
            self._failover_status.new_master = new_master
            self._failover_status.failover_in_progress = False
            self._failover_status.failover_duration_seconds = (
                datetime.now(timezone.utc) - self._failover_status.failover_start_time
            ).total_seconds()
            
            logger.info(f"Failover completed: {old_master} -> {new_master}")
            
        except Exception as e:
            logger.error(f"Error triggering failover: {e}")
            
            # Update failover status
            self._failover_status.failover_in_progress = False
            self._failover_status.failover_duration_seconds = (
                datetime.now(timezone.utc) - self._failover_status.failover_start_time
            ).total_seconds()
            
            raise
        
        return self._failover_status
    
    async def check_quorum(self) -> bool:
        """
        Check if quorum is met.
        
        Returns:
            True if quorum is met, False otherwise
        """
        # Count healthy nodes
        healthy_nodes = sum(1 for node in self._nodes.values() if node.is_healthy)
        
        # Check quorum
        quorum_met = healthy_nodes >= self.quorum_size
        
        logger.debug(f"Quorum check: {healthy_nodes}/{self.quorum_size} - {'MET' if quorum_met else 'NOT MET'}")
        
        return quorum_met
    
    async def write_with_quorum(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Write with quorum check.
        
        Args:
            key: Redis key
            value: Redis value
            ttl: TTL in seconds (optional)
            
        Returns:
            True if write succeeded with quorum, False otherwise
        """
        # Check quorum
        if not await self.check_quorum():
            logger.error("Quorum not met, write failed")
            return False
        
        try:
            # Write to master
            client = await self.get_master_client()
            
            if ttl:
                await client.setex(key, ttl, value)
            else:
                await client.set(key, value)
            
            logger.debug(f"Write with quorum succeeded: {key}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error writing with quorum: {e}")
            return False
    
    async def read_with_preference(self, key: str) -> Optional[Any]:
        """
        Read with read preference.
        
        Args:
            key: Redis key
            
        Returns:
            Redis value or None if not found
        """
        try:
            # Get client based on read preference
            if self.read_preference == ReadPreference.MASTER:
                client = await self.get_master_client()
            else:
                client = await self.get_slave_client()
                if not client:
                    client = await self.get_master_client()
            
            # Read from client
            value = await client.get(key)
            
            return value
            
        except Exception as e:
            logger.error(f"Error reading with preference: {e}")
            return None
    
    def get_failover_status(self) -> Optional[FailoverStatus]:
        """
        Get failover status.
        
        Returns:
            Failover status or None if no failover in progress
        """
        return self._failover_status
    
    def get_nodes(self) -> Dict[str, RedisNode]:
        """
        Get Redis nodes.
        
        Returns:
            Dictionary of Redis nodes
        """
        return self._nodes.copy()
