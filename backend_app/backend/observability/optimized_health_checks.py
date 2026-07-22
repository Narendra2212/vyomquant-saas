"""
HFT-Optimized Health Check System

Ultra-low latency health monitoring optimized for high-frequency trading.
Addresses all blocking, async safety, and performance issues.

Author: Senior Institutional Systems Architect
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("optimized_health_checks")


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


class OptimizedHealthChecker:
    """Base class for optimized health checkers."""
    
    def __init__(self, name: str, check_type: CheckType, timeout: float = 2.0):
        self.name = name
        self.check_type = check_type
        self.timeout = timeout
        
        # Async-safe statistics
        self._stats_lock = threading.Lock()
        self.check_count = 0
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_check: Optional[HealthCheckResult] = None
        
        # Performance optimization
        self._cache_ttl = 5.0  # 5 seconds cache
        self._cached_result: Optional[HealthCheckResult] = None
        self._cache_time: Optional[float] = None
        
        # Thread pool for blocking operations
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"health_{name}")
    
    async def check(self, tenant_id: Optional[str] = None, use_cache: bool = True) -> HealthCheckResult:
        """Perform health check with caching and timeout."""
        current_time = time.time()
        
        # Check cache
        if (use_cache and 
            self._cached_result is not None and 
            self._cache_time is not None and
            current_time - self._cache_time < self._cache_ttl):
            return self._cached_result
        
        start_time = time.time()
        
        try:
            # Run check with timeout in thread pool
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    self._perform_check_sync,
                    tenant_id
                ),
                timeout=self.timeout
            )
            
            result.duration_ms = (time.time() - start_time) * 1000
            
            # Update statistics thread-safely
            with self._stats_lock:
                self.check_count += 1
                if result.status != HealthStatus.HEALTHY:
                    self.failure_count += 1
                    self.last_failure_time = result.timestamp
                
                self.last_check = result
            
            # Cache result
            self._cached_result = result
            self._cache_time = current_time
            
            return result
            
        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            error_result = HealthCheckResult(
                check_name=self.name,
                check_type=self.check_type,
                status=HealthStatus.UNHEALTHY,
                message=f"Health check timeout after {self.timeout}s",
                duration_ms=duration_ms,
                tenant_id=tenant_id
            )
            
            self._update_stats_on_error(error_result)
            return error_result
            
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
            
            self._update_stats_on_error(error_result)
            logger.error(f"Health check {self.name} failed: {e}")
            return error_result
    
    def _update_stats_on_error(self, result: HealthCheckResult):
        """Update statistics on error (thread-safe)."""
        with self._stats_lock:
            self.check_count += 1
            self.failure_count += 1
            self.last_failure_time = result.timestamp
            self.last_check = result
    
    def _perform_check_sync(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Override this method in subclasses (synchronous)."""
        raise NotImplementedError
    
    def get_success_rate(self) -> float:
        """Get success rate percentage (thread-safe)."""
        with self._stats_lock:
            if self.check_count == 0:
                return 0.0
            return ((self.check_count - self.failure_count) / self.check_count) * 100
    
    def shutdown(self):
        """Shutdown thread pool."""
        self.executor.shutdown(wait=True)


class OptimizedDatabaseHealthChecker(OptimizedHealthChecker):
    """Optimized database health checker with minimal blocking."""
    
    def __init__(self, name: str, connection_pool, check_query: str = "SELECT 1"):
        super().__init__(name, CheckType.DATABASE, timeout=1.0)
        self.connection_pool = connection_pool
        self.check_query = check_query
    
    def _perform_check_sync(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check database connectivity with minimal blocking."""
        start_time = time.time()
        
        try:
            # Use connection timeout and query timeout
            async def check_connection():
                async with self.connection_pool.acquire(timeout=0.5) as conn:
                    await conn.execute(self.check_query)
                    await asyncio.wait_for(conn.fetchone(), timeout=0.5)
            
            # Run async check in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(check_connection())
            finally:
                loop.close()
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Performance thresholds for HFT
            if duration_ms > 100:  # 100ms threshold
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
                    "pool_size": getattr(self.connection_pool, 'size', 0)
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


class OptimizedRedisHealthChecker(OptimizedHealthChecker):
    """Optimized Redis health checker with minimal overhead."""
    
    def __init__(self, name: str, redis_client):
        super().__init__(name, CheckType.CACHE, timeout=0.5)
        self.redis_client = redis_client
    
    def _perform_check_sync(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check Redis health with minimal blocking."""
        start_time = time.time()
        
        try:
            # Use async Redis operations
            async def check_redis():
                # Ping with timeout
                await asyncio.wait_for(self.redis_client.ping(), timeout=0.3)
                
                # Quick read/write test
                test_key = f"health_check_{int(time.time())}"
                await asyncio.wait_for(
                    self.redis_client.set(test_key, "test", ex=5), 
                    timeout=0.2
                )
                value = await asyncio.wait_for(
                    self.redis_client.get(test_key), 
                    timeout=0.2
                )
                await asyncio.wait_for(
                    self.redis_client.delete(test_key), 
                    timeout=0.2
                )
                return value
            
            # Run in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                value = loop.run_until_complete(check_redis())
            finally:
                loop.close()
            
            duration_ms = (time.time() - start_time) * 1000
            
            if value != b"test":
                raise Exception("Redis read/write test failed")
            
            # HFT performance thresholds
            if duration_ms > 50:  # 50ms threshold
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
                    "response_time_ms": duration_ms
                },
                tenant_id=tenant_id
            )
            
        except Exception as e:
            return HealthCheckResult(
                check_name=self.name,
                check_type=CheckType.CACHE,
                status=HealthStatus.UNHEALTHY,
                message=f"Redis check failed: {str(e)}",
                tenant_id=tenant_id
            )


class OptimizedWebSocketHealthChecker(OptimizedHealthChecker):
    """Optimized WebSocket health checker."""
    
    def __init__(self, name: str, websocket_manager):
        super().__init__(name, CheckType.WEB_SOCKET, timeout=1.0)
        self.websocket_manager = websocket_manager
    
    def _perform_check_sync(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check WebSocket service health."""
        try:
            # Get stats without blocking
            if hasattr(self.websocket_manager, 'get_stats_sync'):
                stats = self.websocket_manager.get_stats_sync()
            else:
                # Fallback to async check
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    stats = loop.run_until_complete(self.websocket_manager.get_stats())
                finally:
                    loop.close()
            
            # Quick health check
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
            
            # HFT thresholds
            if total_connections > 5000:  # High connection count
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
                    "running": stats.get('running', False)
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


class OptimizedSystemHealthChecker(OptimizedHealthChecker):
    """Optimized system health checker with non-blocking calls."""
    
    def __init__(self, name: str):
        super().__init__(name, CheckType.SYSTEM, timeout=2.0)
        
        # Cached system info to avoid repeated calls
        self._cpu_count = None
        self._memory_total = None
        self._disk_total = None
        self._init_cached_values()
    
    def _init_cached_values(self):
        """Initialize cached system values once."""
        try:
            import psutil
            self._cpu_count = psutil.cpu_count()
            self._memory_total = psutil.virtual_memory().total
            self._disk_total = psutil.disk_usage('/').total
        except Exception as e:
            logger.warning(f"Failed to initialize system metrics: {e}")
    
    def _perform_check_sync(self, tenant_id: Optional[str] = None) -> HealthCheckResult:
        """Check system resources with non-blocking calls."""
        try:
            import psutil

            # Use non-blocking CPU measurement
            cpu_percent = psutil.cpu_percent(interval=None)  # Non-blocking
            
            # Memory check (non-blocking)
            memory = psutil.virtual_memory()
            
            # Disk check (cached total, non-blocking used)
            disk = psutil.disk_usage('/')
            
            # Determine health status with HFT thresholds
            issues = []
            
            if cpu_percent > 95:
                issues.append(f"Critical CPU: {cpu_percent:.1f}%")
            elif cpu_percent > 85:
                issues.append(f"High CPU: {cpu_percent:.1f}%")
            
            if memory.percent > 95:
                issues.append(f"Critical memory: {memory.percent:.1f}%")
            elif memory.percent > 85:
                issues.append(f"High memory: {memory.percent:.1f}%")
            
            if disk.percent > 98:
                issues.append(f"Critical disk: {disk.percent:.1f}%")
            elif disk.percent > 90:
                issues.append(f"High disk: {disk.percent:.1f}%")
            
            if issues:
                if any("Critical" in issue for issue in issues):
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
                    "cpu_count": self._cpu_count
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


class OptimizedHealthCheckManager:
    """Optimized health check manager with minimal overhead."""
    
    def __init__(self):
        self.checkers: List[OptimizedHealthChecker] = []
        self.check_timeout = 5.0  # Reduced timeout for HFT
        self.cache_ttl = 2.0  # Shorter cache for responsiveness
        
        # Async-safe caching
        self._cache_lock = asyncio.Lock()
        self._cached_results: Optional[List[HealthCheckResult]] = None
        self._cache_time: Optional[float] = None
        
        # Performance optimization
        self._max_concurrent_checks = 10
        self._semaphore = asyncio.Semaphore(self._max_concurrent_checks)
    
    def add_checker(self, checker: OptimizedHealthChecker):
        """Add a health checker."""
        self.checkers.append(checker)
        logger.info(f"Added optimized health checker: {checker.name}")
    
    def remove_checker(self, name: str) -> bool:
        """Remove a health checker by name."""
        for i, checker in enumerate(self.checkers):
            if checker.name == name:
                checker.shutdown()
                del self.checkers[i]
                logger.info(f"Removed optimized health checker: {name}")
                return True
        return False
    
    async def check_all(self, tenant_id: Optional[str] = None, 
                        use_cache: bool = True) -> List[HealthCheckResult]:
        """Run all health checks with concurrency control."""
        current_time = time.time()
        
        # Check cache
        if (use_cache and 
            self._cached_results is not None and 
            self._cache_time is not None and
            current_time - self._cache_time < self.cache_ttl):
            return self._cached_results
        
        # Run checks with semaphore to limit concurrency
        async def run_check(checker):
            async with self._semaphore:
                return await checker.check(tenant_id, use_cache=True)
        
        try:
            # Run all checks with timeout
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[run_check(checker) for checker in self.checkers],
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
            async with self._cache_lock:
                self._cached_results = processed_results
                self._cache_time = current_time
            
            return processed_results
            
        except asyncio.TimeoutError:
            logger.error("Optimized health checks timed out")
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
                "check_count": sum(c.check_count for c in self.checkers),
                "avg_success_rate": sum(c.get_success_rate() for c in self.checkers) / len(self.checkers) if self.checkers else 0
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
                "last_failure_time": checker.last_failure_time.isoformat() if checker.last_failure_time else None,
                "cache_ttl": checker._cache_ttl
            }
            for checker in self.checkers
        }
    
    async def shutdown(self):
        """Shutdown all checkers."""
        for checker in self.checkers:
            checker.shutdown()
        logger.info("Optimized health check manager shutdown complete")


# Global optimized health check manager
optimized_health_check_manager = OptimizedHealthCheckManager()
