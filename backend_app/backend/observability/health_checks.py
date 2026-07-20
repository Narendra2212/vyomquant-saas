"""
Institutional-Grade Health Check System

Production-ready health monitoring for distributed trading system.
Provides comprehensive health status for all critical components.

Author: Senior Institutional Systems Architect
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import logging

logger = logging.getLogger("health_checks")


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class CheckType(Enum):
    """Types of health checks."""
    DATABASE = "database"
    CACHE = "cache"
    EXTERNAL_API = "external_api"
    WEB_SOCKET = "websocket"
    QUEUE = "queue"
    WORKER = "worker"
    SYSTEM = "system"
    CUSTOM = "custom"


@dataclass
class HealthCheckResult:
    """Result of a health check."""
    check_name: str
    check_type: CheckType
    status: HealthStatus
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = None
    duration_ms: Optional[float] = None
    tenant_id: Optional[str] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class HealthChecker:
    """Base class for health checkers."""
    
    def __init__(self, name: str, check_type: CheckType):
        self.name = name
        self.check_type = check_type
        self.last_check: Optional[HealthCheckResult] = None
        self.check_count = 0
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
    
    async def check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Perform health check."""
        start_time = time.time()
        
        try:
            result = await self._perform_check(tenant_id)
            result.duration_ms = (time.time() - start_time) * 1000
            
            # Update statistics
            self.check_count += 1
            if result.status != HealthStatus.HEALTHY:
                self.failure_count += 1
                self.last_failure_time = result.timestamp
            
            self.last_check = result
            return result
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            error_result = HealthCheckResult(
                check_name=self.name,
                check_type=self.check_type,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check failed: {str(e)}",
                duration_ms=duration_ms,
                tenant_id=tenant_id
            )
            
            self.check_count += 1
            self.failure_count += 1
            self.last_failure_time = error_result.timestamp
            self.last_check = error_result
            
            logger.error(f"Health check {self.name} failed: {e}")
            return error_result
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Override this method in subclasses."""
        raise NotImplementedError
    
    def get_success_rate(self) -> float:
        """Get success rate percentage."""
        if self.check_count == 0:
            return 0.0
        return ((self.check_count - self.failure_count) / self.check_count) * 100


class DatabaseHealthChecker(HealthChecker):
    """Database health checker."""
    
    def __init__(self, name: str, connection_pool, check_query: str = "SELECT 1"):
        super().__init__(name, CheckType.DATABASE)
        self.connection_pool = connection_pool
        self.check_query = check_query
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check database connectivity and performance."""
        start_time = time.time()
        
        try:
            # Test database connection
            async with self.connection_pool.acquire() as conn:
                result = await conn.execute(self.check_query)
                await conn.fetchone()
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Check performance thresholds
            if duration_ms > 1000:  # 1 second threshold
                status = HealthStatus.DEGRADED
                message = f"Database response slow: {duration_ms:.2f}ms"
            else:
                status = HealthStatus.HEALTHY
                message = f"Database healthy: {duration_ms:.2f}ms"
            
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.DATABASE,
                status=status,
                message=message,
                details={
                    "query": self.check_query,
                    "duration_ms": duration_ms,
                    "pool_size": self.connection_pool.size,
                    "active_connections": getattr(self.connection_pool, 'size', 0)
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.DATABASE,
                status=HealthStatus.UNHEALTHY,
                message=f"Database connection failed: {str(e)}",
                tenant_id=tenant_id
            )


class RedisHealthChecker(HealthChecker):
    """Redis health checker."""
    
    def __init__(self, name: str, redis_client):
        super().__init__(name, CheckType.CACHE)
        self.redis_client = redis_client
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check Redis connectivity and performance."""
        start_time = time.time()
        
        try:
            # Test Redis connection
            await self.redis_client.ping()
            
            # Test basic operations
            test_key = f"health_check_{int(time.time())}"
            await self.redis_client.set(test_key, "test", ex=10)
            value = await self.redis_client.get(test_key)
            await self.redis_client.delete(test_key)
            
            duration_ms = (time.time() - start_time) * 1000
            
            if value != b"test":
                raise Exception("Redis read/write test failed")
            
            # Get Redis info
            info = await self.redis_client.info()
            
            # Check performance thresholds
            if duration_ms > 500:  # 500ms threshold
                status = HealthStatus.DEGRADED
                message = f"Redis response slow: {duration_ms:.2f}ms"
            else:
                status = HealthStatus.HEALTHY
                message = f"Redis healthy: {duration_ms:.2f}ms"
            
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.CACHE,
                status=status,
                message=message,
                details={
                    "duration_ms": duration_ms,
                    "connected_clients": info.get('connected_clients', 0),
                    "used_memory": info.get('used_memory', 0),
                    "keyspace_hits": info.get('keyspace_hits', 0),
                    "keyspace_misses": info.get('keyspace_misses', 0)
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.CACHE,
                status=HealthStatus.UNHEALTHY,
                message=f"Redis connection failed: {str(e)}",
                tenant_id=tenant_id
            )


class WebSocketHealthChecker(HealthChecker):
    """WebSocket health checker."""
    
    def __init__(self, name: str, websocket_manager):
        super().__init__(name, CheckType.WEB_SOCKET)
        self.websocket_manager = websocket_manager
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check WebSocket service health."""
        try:
            # Get connection statistics
            stats = await self.websocket_manager.get_stats()
            
            # Check if WebSocket manager is responsive
            if not stats.get('running', False):
                return HealthCheckResult(
                    check_name=self.name,
                    check_type=CheckType.WEB_SOCKET,
                    status=HealthStatus.UNHEALTHY,
                    message="WebSocket manager not running",
                    tenant_id=tenant_id
                )
            
            total_connections = sum(
                len(connections) for connections in stats.get('tenant_connections', {}).values()
            )
            
            # Check connection thresholds
            if total_connections > 10000:  # High connection count might indicate issues
                status = HealthStatus.DEGRADED
                message = f"High connection count: {total_connections}"
            else:
                status = HealthStatus.HEALTHY
                message = f"WebSocket healthy: {total_connections} connections"
            
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.WEB_SOCKET,
                status=status,
                message=message,
                details={
                    "total_connections": total_connections,
                    "tenant_connections": len(stats.get('tenant_connections', {})),
                    "running": stats.get('running', False),
                    "uptime": stats.get('uptime', 0)
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.WEB_SOCKET,
                status=HealthStatus.UNHEALTHY,
                message=f"WebSocket health check failed: {str(e)}",
                tenant_id=tenant_id
            )


class QueueHealthChecker(HealthChecker):
    """Queue health checker."""
    
    def __init__(self, name: str, queue_manager):
        super().__init__(name, CheckType.QUEUE)
        self.queue_manager = queue_manager
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check queue system health."""
        try:
            # Get queue statistics
            stats = await self.queue_manager.get_queue_stats()
            
            # Check queue depths
            max_depth = 0
            total_depth = 0
            for queue_name, depth in stats.get('queue_depths', {}).items():
                total_depth += depth
                max_depth = max(max_depth, depth)
            
            # Check saturation
            max_saturation = 0
            for queue_name, saturation in stats.get('queue_saturations', {}).items():
                max_saturation = max(max_saturation, saturation)
            
            # Determine health status
            if max_saturation > 90:
                status = HealthStatus.UNHEALTHY
                message = f"Queue saturation critical: {max_saturation:.1f}%"
            elif max_saturation > 70:
                status = HealthStatus.DEGRADED
                message = f"Queue saturation elevated: {max_saturation:.1f}%"
            elif max_depth > 1000:
                status = HealthStatus.DEGRADED
                message = f"High queue depth: {max_depth}"
            else:
                status = HealthStatus.HEALTHY
                message = f"Queues healthy: {total_depth} total depth"
            
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.QUEUE,
                status=status,
                message=message,
                details={
                    "total_depth": total_depth,
                    "max_depth": max_depth,
                    "max_saturation": max_saturation,
                    "queue_count": len(stats.get('queue_depths', {})),
                    "stats": stats
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.QUEUE,
                status=HealthStatus.UNHEALTHY,
                message=f"Queue health check failed: {str(e)}",
                tenant_id=tenant_id
            )


class WorkerHealthChecker(HealthChecker):
    """Worker health checker."""
    
    def __init__(self, name: str, worker_pool):
        super().__init__(name, CheckType.WORKER)
        self.worker_pool = worker_pool
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check worker pool health."""
        try:
            # Get worker pool status
            status = self.worker_pool.get_pool_status()
            
            total_workers = len(status.get('workers', []))
            active_workers = sum(
                1 for worker in status.get('workers', [])
                if worker.get('state') == 'IDLE' or worker.get('state') == 'PROCESSING'
            )
            
            # Check worker health
            if active_workers == 0:
                health_status = HealthStatus.UNHEALTHY
                message = "No active workers"
            elif active_workers < total_workers * 0.5:
                health_status = HealthStatus.DEGRADED
                message = f"Only {active_workers}/{total_workers} workers active"
            else:
                health_status = HealthStatus.HEALTHY
                message = f"Workers healthy: {active_workers}/{total_workers} active"
            
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.WORKER,
                status=health_status,
                message=message,
                details={
                    "total_workers": total_workers,
                    "active_workers": active_workers,
                    "running": status.get('running', False),
                    "worker_states": {
                        worker.get('state', 'unknown'): 
                        sum(1 for w in status.get('workers', []) if w.get('state') == worker.get('state'))
                        for worker in status.get('workers', [])
                    }
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.WORKER,
                status=HealthStatus.UNHEALTHY,
                message=f"Worker health check failed: {str(e)}",
                tenant_id=tenant_id
            )


class ExternalAPIHealthChecker(HealthChecker):
    """External API health checker."""
    
    def __init__(self, name: str, api_url: str, timeout: float = 5.0, headers: Dict[str, str] = None):
        super().__init__(name, CheckType.EXTERNAL_API)
        self.api_url = api_url
        self.timeout = timeout
        self.headers = headers or {}
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check external API health."""
        import aiohttp
        
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.get(self.api_url, headers=self.headers) as response:
                    duration_ms = (time.time() - start_time) * 1000
                    
                    if response.status == 200:
                        if duration_ms > 5000:  # 5 second threshold
                            status = HealthStatus.DEGRADED
                            message = f"API response slow: {duration_ms:.2f}ms"
                        else:
                            status = HealthStatus.HEALTHY
                            message = f"API healthy: {duration_ms:.2f}ms"
                        
                        return HealthCheckResult(
                            check_name=self.name,
                            check_type=CheckType.EXTERNAL_API,
                            status=status,
                            message=message,
                            details={
                                "url": self.api_url,
                                "status_code": response.status,
                                "duration_ms": duration_ms,
                                "headers": dict(response.headers)
                            },
                            tenant_id=tenant_id
                        )
                    else:
                        return HealthCheckResult(
                            check_name=self.name,
                            check_type=CheckType.EXTERNAL_API,
                            status=HealthStatus.UNHEALTHY,
                            message=f"API returned status {response.status}",
                            details={
                                "url": self.api_url,
                                "status_code": response.status,
                                "duration_ms": duration_ms
                            },
                            tenant_id=tenant_id
                        )
                    
        except asyncio.TimeoutError:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.EXTERNAL_API,
                status=HealthStatus.UNHEALTHY,
                message=f"API timeout after {self.timeout}s",
                details={
                    "url": self.api_url,
                    "timeout": self.timeout
                },
                tenant_id=tenant_id
            )
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.EXTERNAL_API,
                status=HealthStatus.UNHEALTHY,
                message=f"API check failed: {str(e)}",
                details={
                    "url": self.api_url,
                    "error": str(e)
                },
                tenant_id=tenant_id
            )


class SystemHealthChecker(HealthChecker):
    """System resource health checker."""
    
    def __init__(self, name: str):
        super().__init__(name, CheckType.SYSTEM)
    
    async def _perform_check(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check system resources."""
        try:
            import psutil
            
            # CPU check
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Memory check
            memory = psutil.virtual_memory()
            
            # Disk check
            disk = psutil.disk_usage('/')
            
            # Determine health status
            issues = []
            
            if cpu_percent > 90:
                issues.append(f"High CPU: {cpu_percent:.1f}%")
            elif cpu_percent > 70:
                issues.append(f"Elevated CPU: {cpu_percent:.1f}%")
            
            if memory.percent > 90:
                issues.append(f"High memory: {memory.percent:.1f}%")
            elif memory.percent > 80:
                issues.append(f"Elevated memory: {memory.percent:.1f}%")
            
            if disk.percent > 95:
                issues.append(f"High disk: {disk.percent:.1f}%")
            elif disk.percent > 85:
                issues.append(f"Elevated disk: {disk.percent:.1f}%")
            
            if issues:
                if any("High" in issue for issue in issues):
                    status = HealthStatus.UNHEALTHY
                else:
                    status = HealthStatus.DEGRADED
                message = "; ".join(issues)
            else:
                status = HealthStatus.HEALTHY
                message = f"System healthy: CPU {cpu_percent:.1f}%, Memory {memory.percent:.1f}%, Disk {disk.percent:.1f}%"
            
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.SYSTEM,
                status=status,
                message=message,
                details={
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "memory_available_gb": memory.available / (1024**3),
                    "disk_percent": disk.percent,
                    "disk_free_gb": disk.free / (1024**3),
                    "load_average": list(psutil.getloadavg()) if hasattr(psutil, 'getloadavg') else None
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.SYSTEM,
                status=HealthStatus.UNHEALTHY,
                message=f"System check failed: {str(e)}",
                tenant_id=tenant_id
            )


class HealthCheckManager:
    """Manages all health checks and provides aggregated health status."""
    
    def __init__(self):
        self.checkers: List[HealthChecker] = []
        self.check_timeout = 30.0  # Maximum time for all checks
        self.cache_ttl = 10.0  # Cache results for 10 seconds
        self._cached_results: Optional[List[HealthCheckResult]] = None
        self._cache_time: Optional[float] = None
    
    def add_checker(self, checker: HealthChecker):
        """Add a health checker."""
        self.checkers.append(checker)
        logger.info(f"Added health checker: {checker.name}")
    
    def remove_checker(self, name: str) -> bool:
        """Remove a health checker by name."""
        for i, checker in enumerate(self.checkers):
            if checker.name == name:
                del self.checkers[i]
                logger.info(f"Removed health checker: {name}")
                return True
        return False
    
    async def check_all(self, tenant_id: Optional[str] = None, 
                        use_cache: bool = True) -> List[HealthCheckResult]:
        """Run all health checks."""
        current_time = time.time()
        
        # Check cache
        if (use_cache and 
            self._cached_results is not None and 
            self._cache_time is not None and
            current_time - self._cache_time < self.cache_ttl):
            return self._cached_results
        
        # Run all checks with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[checker.check(tenant_id) for checker in self.checkers],
                    return_exceptions=True
                ),
                timeout=self.check_timeout
            )
            
            # Process results
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    processed_results.append(HealthCheckResult(
                        check_name=self.checkers[i].name,
                        check_type=self.checkers[i].check_type,
                        status=HealthStatus.UNHEALTHY,
                        message=f"Check failed: {str(result)}",
                        tenant_id=tenant_id
                    ))
                else:
                    processed_results.append(result)
            
            # Cache results
            self._cached_results = processed_results
            self._cache_time = current_time
            
            return processed_results
            
        except asyncio.TimeoutError:
            logger.error("Health checks timed out")
            return [HealthCheckResult(
                check_name="timeout",
                check_type=CheckType.CUSTOM,
                status=HealthStatus.UNHEALTHY,
                message=f"Health checks timed out after {self.check_timeout}s",
                tenant_id=tenant_id
            )]
    
    async def get_overall_health(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Get overall health status."""
        results = await self.check_all(tenant_id)
        
        if not results:
            return HealthCheckResult(
                check_name="overall",
                check_type=CheckType.CUSTOM,
                status=HealthStatus.UNKNOWN,
                message="No health checks configured"
            )
        
        # Determine overall status
        unhealthy_count = sum(1 for r in results if r.status == HealthStatus.UNHEALTHY)
        degraded_count = sum(1 for r in results if r.status == HealthStatus.DEGRADED)
        healthy_count = sum(1 for r in results if r.status == HealthStatus.HEALTHY)
        
        if unhealthy_count > 0:
            overall_status = HealthStatus.UNHEALTHY
            message = f"{unhealthy_count} checks failed, {degraded_count} degraded, {healthy_count} healthy"
        elif degraded_count > 0:
            overall_status = HealthStatus.DEGRADED
            message = f"{degraded_count} checks degraded, {healthy_count} healthy"
        else:
            overall_status = HealthStatus.HEALTHY
            message = f"All {healthy_count} checks healthy"
        
        return HealthCheckResult(
            check_name="overall",
            check_type=CheckType.CUSTOM,
            status=overall_status,
            message=message,
            details={
                "total_checks": len(results),
                "healthy": healthy_count,
                "degraded": degraded_count,
                "unhealthy": unhealthy_count,
                "checks": [asdict(r) for r in results]
            },
            tenant_id=tenant_id
        )
    
    def get_checker_stats(self) -> Dict[str, Any]:
        """Get statistics for all checkers."""
        return {
            checker.name: {
                "check_type": checker.check_type.value,
                "check_count": checker.check_count,
                "failure_count": checker.failure_count,
                "success_rate": checker.get_success_rate(),
                "last_check": asdict(checker.last_check) if checker.last_check else None,
                "last_failure_time": checker.last_failure_time.isoformat() if checker.last_failure_time else None
            }
            for checker in self.checkers
        }


# Global health check manager
health_check_manager = HealthCheckManager()
