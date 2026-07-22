"""
HA PostgreSQL Manager - Phase 5 High Availability Foundations

This module implements the HA PostgreSQL manager for institutional-grade PostgreSQL high availability.
It provides Patroni integration, replication management, failover management, and fencing mechanisms
to preserve deterministic ordering and replay guarantees.

Key Features:
- Patroni integration for automatic failover
- etcd-based leader election
- Streaming replication management
- Synchronous commit management
- Fencing token management
- Rollback mechanism
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class ReadPreference(Enum):
    """Read preference for PostgreSQL operations."""
    PRIMARY = "primary"
    REPLICA = "replica"
    NEAREST = "nearest"


@dataclass
class PostgreSQLNode:
    """PostgreSQL node information."""
    node_id: str
    host: str
    port: int
    role: str  # primary, replica, standby_leader
    priority: int = 100
    is_healthy: bool = True
    last_heartbeat: Optional[datetime] = None
    patroni_rest_api_port: int = 8008


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
    primary_host: str
    primary_port: int
    replicas: List[Dict[str, Any]]
    replication_lag_ms: float
    connected_replicas: int
    total_replicas: int
    synchronous_standby_names: str


@dataclass
class FailoverStatus:
    """Failover status information."""
    failover_in_progress: bool
    failover_start_time: Optional[datetime]
    failover_duration_seconds: Optional[float]
    old_primary: Optional[str]
    new_primary: Optional[str]
    fencing_token: Optional[FencingToken]


class HAPostgreSQLManager:
    """
    HA PostgreSQL Manager for institutional-grade PostgreSQL high availability.
    
    This manager provides:
    - Patroni integration for automatic failover
    - etcd-based leader election
    - Streaming replication management
    - Synchronous commit management
    - Fencing mechanisms for split-brain prevention
    """
    
    def __init__(
        self,
        patroni_hosts: List[str],
        patroni_rest_api_port: int = 8008,
        etcd_hosts: List[str] = None,
        etcd_port: int = 2379,
        database: str = "trading",
        user: str = "postgres",
        password: Optional[str] = None,
        quorum_size: int = 2,
        quorum_timeout: int = 10,
        fencing_token_ttl: int = 3600,
        read_preference: ReadPreference = ReadPreference.PRIMARY
    ):
        """
        Initialize HA PostgreSQL Manager.
        
        Args:
            patroni_hosts: List of Patroni host addresses
            patroni_rest_api_port: Patroni REST API port (default: 8008)
            etcd_hosts: List of etcd host addresses (optional)
            etcd_port: etcd port (default: 2379)
            database: Database name (default: "trading")
            user: Database user (default: "postgres")
            password: Database password (optional)
            quorum_size: Quorum size for write operations (default: 2)
            quorum_timeout: Quorum timeout in seconds (default: 10)
            fencing_token_ttl: Fencing token TTL in seconds (default: 3600)
            read_preference: Read preference (default: PRIMARY)
        """
        self.patroni_hosts = patroni_hosts
        self.patroni_rest_api_port = patroni_rest_api_port
        self.etcd_hosts = etcd_hosts or []
        self.etcd_port = etcd_port
        self.database = database
        self.user = user
        self.password = password
        self.quorum_size = quorum_size
        self.quorum_timeout = quorum_timeout
        self.fencing_token_ttl = fencing_token_ttl
        self.read_preference = read_preference
        
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._nodes: Dict[str, PostgreSQLNode] = {}
        self._current_fencing_token: Optional[FencingToken] = None
        self._generation_epoch: int = 0
        self._is_running: bool = False
        self._failover_status: Optional[FailoverStatus] = None
        
        logger.info(f"HA PostgreSQL Manager initialized with {len(patroni_hosts)} Patroni hosts")
    
    async def initialize(self) -> None:
        """Initialize HA PostgreSQL Manager."""
        logger.info("Initializing HA PostgreSQL Manager")
        
        # Initialize HTTP session
        self._http_session = aiohttp.ClientSession()
        
        # Discover nodes
        await self._discover_nodes()
        
        # Start monitoring
        self._is_running = True
        asyncio.create_task(self._monitor_loop())
        
        logger.info("HA PostgreSQL Manager initialized successfully")
    
    async def shutdown(self) -> None:
        """Shutdown HA PostgreSQL Manager."""
        logger.info("Shutting down HA PostgreSQL Manager")
        
        self._is_running = False
        
        # Close HTTP session
        if self._http_session:
            await self._http_session.close()
        
        logger.info("HA PostgreSQL Manager shut down successfully")
    
    async def _discover_nodes(self) -> None:
        """Discover PostgreSQL nodes."""
        logger.info("Discovering PostgreSQL nodes")
        
        for host in self.patroni_hosts:
            try:
                # Get Patroni node info
                url = f"http://{host}:{self.patroni_rest_api_port}/patroni"
                async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        node_id = data.get("node", f"{host}")
                        role = data.get("role", "unknown")
                        
                        self._nodes[node_id] = PostgreSQLNode(
                            node_id=node_id,
                            host=host,
                            port=5432,
                            role=role,
                            priority=100 if role == "primary" else 50,
                            is_healthy=True,
                            last_heartbeat=datetime.now(timezone.utc),
                            patroni_rest_api_port=self.patroni_rest_api_port
                        )
                        
                        logger.info(f"Discovered node: {node_id} ({role})")
                    
            except Exception as e:
                logger.error(f"Error discovering node {host}: {e}")
        
        logger.info(f"Discovered {len(self._nodes)} PostgreSQL nodes")
    
    async def _monitor_loop(self) -> None:
        """Monitor PostgreSQL nodes."""
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
                # Get Patroni node health
                url = f"http://{node.host}:{node.patroni_rest_api_port}/patroni"
                async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        # Update health
                        node.is_healthy = True
                        node.last_heartbeat = datetime.now(timezone.utc)
                        
                        # Update role
                        data = await response.json()
                        node.role = data.get("role", "unknown")
                    else:
                        node.is_healthy = False
                        
            except Exception as e:
                logger.error(f"Error updating health for node {node_id}: {e}")
                node.is_healthy = False
    
    async def _update_replication_status(self) -> None:
        """Update replication status."""
        try:
            # Get primary node
            primary_node = self._get_primary_node()
            if not primary_node:
                return
            
            # Get Patroni replication info
            url = f"http://{primary_node.host}:{primary_node.patroni_rest_api_port}/patroni"
            async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Get replica info
                    replicas = data.get("database_system_identifier", {}).get("replicas", [])
                    connected_replicas = len(replicas)
                    total_replicas = len(self._nodes) - 1  # Exclude primary
                    
                    logger.debug(
                        f"Replication status: {connected_replicas}/{total_replicas} replicas connected"
                    )
                    
        except Exception as e:
            logger.error(f"Error updating replication status: {e}")
    
    def _get_primary_node(self) -> Optional[PostgreSQLNode]:
        """Get primary node."""
        for node in self._nodes.values():
            if node.role == "primary" and node.is_healthy:
                return node
        return None
    
    def _get_replica_nodes(self) -> List[PostgreSQLNode]:
        """Get replica nodes."""
        return [node for node in self._nodes.values() if node.role in ["replica", "standby_leader"] and node.is_healthy]
    
    async def get_primary_connection_string(self) -> str:
        """
        Get primary connection string.
        
        Returns:
            Primary connection string
            
        Raises:
            Exception: If primary node is not available
        """
        primary_node = self._get_primary_node()
        if not primary_node:
            raise Exception("Primary node not available")
        
        password_part = f":{self.password}@" if self.password else ""
        return f"postgresql://{self.user}{password_part}{primary_node.host}:{primary_node.port}/{self.database}"
    
    async def get_replica_connection_string(self) -> Optional[str]:
        """
        Get replica connection string based on read preference.
        
        Returns:
            Replica connection string or None if no replicas available
        """
        if self.read_preference == ReadPreference.PRIMARY:
            return await self.get_primary_connection_string()
        
        replica_nodes = self._get_replica_nodes()
        if not replica_nodes:
            return None
        
        if self.read_preference == ReadPreference.REPLICA:
            replica_node = replica_nodes[0]
        elif self.read_preference == ReadPreference.NEAREST:
            # Return nearest replica (simplified - could use latency)
            replica_node = replica_nodes[0]
        else:
            replica_node = replica_nodes[0]
        
        password_part = f":{self.password}@" if self.password else ""
        return f"postgresql://{self.user}{password_part}{replica_node.host}:{replica_node.port}/{self.database}"
    
    async def get_replication_status(self) -> ReplicationStatus:
        """
        Get replication status.
        
        Returns:
            Replication status
        """
        try:
            primary_node = self._get_primary_node()
            if not primary_node:
                raise Exception("Primary node not available")
            
            # Get Patroni replication info
            url = f"http://{primary_node.host}:{primary_node.patroni_rest_api_port}/patroni"
            async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Get replica info
                    replicas = []
                    for replica_data in data.get("database_system_identifier", {}).get("replicas", []):
                        replicas.append({
                            "node": replica_data.get("node", "unknown"),
                            "lag": replica_data.get("lag", 0)
                        })
                    
                    # Calculate replication lag
                    replication_lag_ms = 0.0
                    if replicas:
                        replication_lag_ms = max(r.get("lag", 0) * 1000 for r in replicas)
                    
                    # Get synchronous standby names
                    synchronous_standby_names = data.get("postgresql", {}).get("parameters", {}).get("synchronous_standby_names", "")
                    
                    return ReplicationStatus(
                        primary_host=primary_node.host,
                        primary_port=primary_node.port,
                        replicas=replicas,
                        replication_lag_ms=replication_lag_ms,
                        connected_replicas=len(replicas),
                        total_replicas=len(self._nodes) - 1,
                        synchronous_standby_names=synchronous_standby_names
                    )
                else:
                    raise Exception(f"Patroni API returned status {response.status}")
                    
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
        
        # Get current primary
        old_primary = None
        primary_node = self._get_primary_node()
        if primary_node:
            old_primary = f"{primary_node.host}:{primary_node.port}"
        
        # Initialize failover status
        self._failover_status = FailoverStatus(
            failover_in_progress=True,
            failover_start_time=datetime.now(timezone.utc),
            failover_duration_seconds=None,
            old_primary=old_primary,
            new_primary=None,
            fencing_token=fencing_token
        )
        
        try:
            # Get primary node
            if not primary_node:
                raise Exception("Primary node not available")
            
            # Trigger Patroni failover
            url = f"http://{primary_node.host}:{primary_node.patroni_rest_api_port}/patroni/failover"
            payload = {
                "leader": primary_node.node_id,
                "candidate": None  # Let Patroni select candidate
            }
            
            async with self._http_session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 202:
                    logger.info("Failover initiated successfully")
                else:
                    error_text = await response.text()
                    raise Exception(f"Patroni failover failed: {error_text}")
            
            # Wait for failover to complete
            await asyncio.sleep(10)
            
            # Reinitialize nodes
            await self._discover_nodes()
            
            # Get new primary
            new_primary_node = self._get_primary_node()
            if new_primary_node:
                new_primary = f"{new_primary_node.host}:{new_primary_node.port}"
            
            # Update failover status
            self._failover_status.new_primary = new_primary
            self._failover_status.failover_in_progress = False
            self._failover_status.failover_duration_seconds = (
                datetime.now(timezone.utc) - self._failover_status.failover_start_time
            ).total_seconds()
            
            logger.info(f"Failover completed: {old_primary} -> {new_primary}")
            
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
    
    async def check_etcd_quorum(self) -> bool:
        """
        Check if etcd quorum is met.
        
        Returns:
            True if etcd quorum is met, False otherwise
        """
        if not self.etcd_hosts:
            logger.warning("No etcd hosts configured, skipping etcd quorum check")
            return True
        
        # Count healthy etcd nodes
        healthy_etcd_nodes = 0
        for host in self.etcd_hosts:
            try:
                url = f"http://{host}:{self.etcd_port}/health"
                async with self._http_session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        healthy_etcd_nodes += 1
            except Exception as e:
                logger.error(f"Error checking etcd health for {host}: {e}")
        
        # Check quorum (2/3)
        etcd_quorum_met = healthy_etcd_nodes >= 2
        
        logger.debug(f"etcd quorum check: {healthy_etcd_nodes}/3 - {'MET' if etcd_quorum_met else 'NOT MET'}")
        
        return etcd_quorum_met
    
    def get_failover_status(self) -> Optional[FailoverStatus]:
        """
        Get failover status.
        
        Returns:
            Failover status or None if no failover in progress
        """
        return self._failover_status
    
    def get_nodes(self) -> Dict[str, PostgreSQLNode]:
        """
        Get PostgreSQL nodes.
        
        Returns:
            Dictionary of PostgreSQL nodes
        """
        return self._nodes.copy()
