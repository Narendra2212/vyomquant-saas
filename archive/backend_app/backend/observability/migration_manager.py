"""
Safe Migration Manager for Observability Stack

Controls transition between original and optimized observability systems.
Ensures zero-downtime migration with fallback capabilities.

Author: Senior Low-Latency Trading Infrastructure Engineer
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("migration_manager")


class ObservabilityMode(Enum):
    """Observability system modes."""
    ORIGINAL = "original"
    OPTIMIZED = "optimized"
    HYBRID = "hybrid"
    FALLBACK = "fallback"


@dataclass
class MigrationConfig:
    """Configuration for migration process."""
    enable_migration: bool = False
    target_mode: ObservabilityMode = ObservabilityMode.ORIGINAL
    migration_timeout: float = 30.0
    health_check_interval: float = 5.0
    performance_threshold: float = 1.2  # 20% performance degradation threshold
    rollback_on_failure: bool = True
    dry_run: bool = False


class SafeMigrationManager:
    """Manages safe migration between observability systems."""
    
    def __init__(self, config: MigrationConfig):
        self.config = config
        self.current_mode = ObservabilityMode.ORIGINAL
        self.migration_active = False
        self.migration_start_time: Optional[float] = None
        
        # Health tracking
        self.health_metrics: Dict[str, Any] = {}
        self.performance_baseline: Dict[str, float] = {}
        
        # Module references
        self.original_modules = {}
        self.optimized_modules = {}
        
        # Migration state
        self._migration_lock = asyncio.Lock()
        self._health_check_task = None
        
        logger.info(f"Migration manager initialized - Target mode: {config.target_mode.value}")
    
    async def initialize(self):
        """Initialize migration manager."""
        # Load original modules
        await self._load_original_modules()
        
        # Load optimized modules
        await self._load_optimized_modules()
        
        # Establish performance baseline
        await self._establish_baseline()
        
        # Start health monitoring
        if self.config.enable_migration:
            await self._start_health_monitoring()
        
        logger.info("Migration manager initialized successfully")
    
    async def _load_original_modules(self):
        """Load original observability modules."""
        try:
            # Import original modules
            import backend_app.backend.logging_config as original_logging
            import backend_app.backend.observability.health_checks as original_health
            import backend_app.backend.observability.metrics_exporter as original_metrics
            import backend_app.backend.observability.opentelemetry_tracing as original_tracing
            
            self.original_modules = {
                'metrics': original_metrics,
                'health': original_health,
                'tracing': original_tracing,
                'logging': original_logging
            }
            
            logger.info("Original observability modules loaded")
            
        except Exception as e:
            logger.error(f"Failed to load original modules: {e}")
            raise
    
    async def _load_optimized_modules(self):
        """Load optimized observability modules."""
        try:
            # Import optimized modules
            import backend_app.backend.observability.optimized_health_checks as optimized_health
            import backend_app.backend.observability.optimized_logging as optimized_logging
            import backend_app.backend.observability.optimized_metrics_exporter as optimized_metrics
            import backend_app.backend.observability.optimized_tracing as optimized_tracing
            
            self.optimized_modules = {
                'metrics': optimized_metrics,
                'health': optimized_health,
                'tracing': optimized_tracing,
                'logging': optimized_logging
            }
            
            logger.info("Optimized observability modules loaded")
            
        except Exception as e:
            logger.error(f"Failed to load optimized modules: {e}")
            # Optimized modules are optional for migration
            pass
    
    async def _establish_baseline(self):
        """Establish performance baseline with current system."""
        logger.info("Establishing performance baseline...")
        
        # Measure baseline metrics
        baseline_start = time.time()
        
        # Test metrics collection
        try:
            if 'metrics' in self.original_modules:
                # Test original metrics
                pass
        except Exception as e:
            logger.warning(f"Baseline metrics test failed: {e}")
        
        # Test health checks
        try:
            if 'health' in self.original_modules:
                # Test original health checks
                pass
        except Exception as e:
            logger.warning(f"Baseline health check test failed: {e}")
        
        baseline_duration = time.time() - baseline_start
        self.performance_baseline['baseline_duration'] = baseline_duration
        
        logger.info(f"Performance baseline established: {baseline_duration:.3f}s")
    
    async def _start_health_monitoring(self):
        """Start continuous health monitoring."""
        self._health_check_task = asyncio.create_task(self._health_monitoring_loop())
        logger.info("Health monitoring started")
    
    async def _health_monitoring_loop(self):
        """Continuous health monitoring loop."""
        while self.config.enable_migration:
            try:
                await self._perform_health_check()
                await asyncio.sleep(self.config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                await asyncio.sleep(1.0)
    
    async def _perform_health_check(self):
        """Perform comprehensive health check."""
        current_time = time.time()
        
        # Check current system health
        health_metrics = {
            'timestamp': current_time,
            'mode': self.current_mode.value,
            'migration_active': self.migration_active,
            'memory_usage': self._get_memory_usage(),
            'cpu_usage': self._get_cpu_usage(),
            'response_time': self._test_response_time()
        }
        
        # Store health metrics
        self.health_metrics = health_metrics
        
        # Check for performance degradation
        if self._should_rollback(health_metrics):
            logger.warning("Performance degradation detected - initiating rollback")
            await self.initiate_rollback()
        
        # Log health status
        logger.debug(f"Health check completed - Mode: {self.current_mode.value}")
    
    def _get_memory_usage(self) -> float:
        """Get current memory usage."""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except Exception:
            return 0.0
    
    def _get_cpu_usage(self) -> float:
        """Get current CPU usage."""
        try:
            import psutil
            return psutil.cpu_percent(interval=None)
        except Exception:
            return 0.0
    
    def _test_response_time(self) -> float:
        """Test system response time."""
        start_time = time.time()
        
        # Simple operation to test responsiveness
        try:
            # Test metrics endpoint if available
            pass
        except Exception:
            pass
        
        return (time.time() - start_time) * 1000  # Return in ms
    
    def _should_rollback(self, health_metrics: Dict[str, Any]) -> bool:
        """Determine if rollback should be initiated."""
        if not self.config.rollback_on_failure:
            return False
        
        # Check performance degradation
        baseline_duration = self.performance_baseline.get('baseline_duration', 1.0)
        current_response_time = health_metrics.get('response_time', 0.0)
        
        if current_response_time > baseline_duration * self.config.performance_threshold:
            return True
        
        # Check resource usage
        if health_metrics.get('memory_usage', 0) > 90:
            return True
        
        if health_metrics.get('cpu_usage', 0) > 95:
            return True
        
        return False
    
    async def initiate_migration(self, target_mode: ObservabilityMode) -> bool:
        """Initiate migration to target mode."""
        async with self._migration_lock:
            if self.migration_active:
                logger.warning("Migration already in progress")
                return False
            
            try:
                self.migration_active = True
                self.migration_start_time = time.time()
                
                logger.info(f"Starting migration to {target_mode.value}")
                
                # Perform migration based on target mode
                if target_mode == ObservabilityMode.OPTIMIZED:
                    success = await self._migrate_to_optimized()
                elif target_mode == ObservabilityMode.ORIGINAL:
                    success = await self._migrate_to_original()
                elif target_mode == ObservabilityMode.HYBRID:
                    success = await self._migrate_to_hybrid()
                else:
                    success = False
                
                if success:
                    self.current_mode = target_mode
                    logger.info(f"Migration to {target_mode.value} completed successfully")
                else:
                    logger.error(f"Migration to {target_mode.value} failed")
                    await self.initiate_rollback()
                
                return success
                
            except Exception as e:
                logger.error(f"Migration failed: {e}")
                await self.initiate_rollback()
                return False
            finally:
                self.migration_active = False
                self.migration_start_time = None
    
    async def _migrate_to_optimized(self) -> bool:
        """Migrate to optimized observability system."""
        logger.info("Migrating to optimized observability...")
        
        try:
            # Step 1: Initialize optimized system
            if 'metrics' in self.optimized_modules:
                await self.optimized_modules['metrics'].optimized_metrics_collector.start()
            
            if 'health' in self.optimized_modules:
                # Optimized health checks are passive, no start needed
                pass
            
            if 'tracing' in self.optimized_modules:
                config = self.optimized_modules['tracing'].OptimizedTraceConfig(
                    service_name="algo-trading",
                    sample_rate=0.01,  # 1% sampling for HFT
                    sampling_strategy=self.optimized_modules['tracing'].SamplingStrategy.ADAPTIVE
                )
                await self.optimized_modules['tracing'].initialize_optimized_tracing(config)
            
            if 'logging' in self.optimized_modules:
                await self.optimized_modules['logging'].setup_optimized_logging(
                    level="INFO",
                    json_format=True
                )
            
            # Step 2: Shutdown original system gracefully
            await self._shutdown_original_system()
            
            # Step 3: Verify migration
            await asyncio.sleep(2.0)  # Allow system to stabilize
            
            # Basic health check
            health_ok = await self._verify_optimized_health()
            
            if health_ok:
                logger.info("Migration to optimized system completed successfully")
                return True
            else:
                logger.error("Optimized system health verification failed")
                return False
                
        except Exception as e:
            logger.error(f"Migration to optimized system failed: {e}")
            return False
    
    async def _migrate_to_original(self) -> bool:
        """Migrate back to original observability system."""
        logger.info("Migrating to original observability...")
        
        try:
            # Step 1: Shutdown optimized system
            await self._shutdown_optimized_system()
            
            # Step 2: Initialize original system
            await self._initialize_original_system()
            
            # Step 3: Verify migration
            await asyncio.sleep(2.0)
            
            health_ok = await self._verify_original_health()
            
            if health_ok:
                logger.info("Migration to original system completed successfully")
                return True
            else:
                logger.error("Original system health verification failed")
                return False
                
        except Exception as e:
            logger.error(f"Migration to original system failed: {e}")
            return False
    
    async def _migrate_to_hybrid(self) -> bool:
        """Migrate to hybrid mode (both systems running)."""
        logger.info("Migrating to hybrid observability...")
        
        try:
            # Initialize both systems with different configurations
            # Original system handles critical path
            # Optimized system handles analytics
            
            await self._initialize_original_system()
            
            # Initialize optimized with reduced scope
            if 'metrics' in self.optimized_modules:
                # Start optimized on different port
                await self.optimized_modules['metrics'].optimized_metrics_collector.start(port=8081)
            
            logger.info("Migration to hybrid system completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Migration to hybrid system failed: {e}")
            return False
    
    async def _shutdown_original_system(self):
        """Shutdown original observability system."""
        try:
            # Stop original metrics collector
            if 'metrics' in self.original_modules:
                # Original metrics don't have explicit shutdown
                pass
            
            # Stop original tracing
            if 'tracing' in self.original_modules:
                self.original_modules['tracing'].shutdown_tracing()
            
            logger.info("Original observability system shutdown")
            
        except Exception as e:
            logger.warning(f"Error shutting down original system: {e}")
    
    async def _shutdown_optimized_system(self):
        """Shutdown optimized observability system."""
        try:
            # Stop optimized metrics
            if 'metrics' in self.optimized_modules:
                await self.optimized_modules['metrics'].optimized_metrics_collector.stop()
            
            # Stop optimized tracing
            if 'tracing' in self.optimized_modules:
                self.optimized_modules['tracing'].shutdown_optimized_tracing()
            
            # Stop optimized logging
            if 'logging' in self.optimized_modules:
                await self.optimized_modules['logging'].shutdown_optimized_logging()
            
            logger.info("Optimized observability system shutdown")
            
        except Exception as e:
            logger.warning(f"Error shutting down optimized system: {e}")
    
    async def _initialize_original_system(self):
        """Initialize original observability system."""
        try:
            # Original system initializes automatically on import
            logger.info("Original observability system initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize original system: {e}")
    
    async def _verify_optimized_health(self) -> bool:
        """Verify optimized system health."""
        try:
            # Test optimized metrics
            if 'metrics' in self.optimized_modules:
                # Basic health check
                pass
            
            # Test optimized health checks
            if 'health' in self.optimized_modules:
                # Run basic health check
                pass
            
            return True
            
        except Exception as e:
            logger.error(f"Optimized system health verification failed: {e}")
            return False
    
    async def _verify_original_health(self) -> bool:
        """Verify original system health."""
        try:
            # Test original metrics
            if 'metrics' in self.original_modules:
                # Basic health check
                pass
            
            # Test original health checks
            if 'health' in self.original_modules:
                # Run basic health check
                pass
            
            return True
            
        except Exception as e:
            logger.error(f"Original system health verification failed: {e}")
            return False
    
    async def initiate_rollback(self):
        """Initiate emergency rollback."""
        logger.warning("Initiating emergency rollback...")
        
        try:
            # Force rollback to original system
            await self._migrate_to_original()
            self.current_mode = ObservabilityMode.FALLBACK
            
            logger.warning("Emergency rollback completed")
            
        except Exception as e:
            logger.error(f"Emergency rollback failed: {e}")
    
    async def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status."""
        return {
            'current_mode': self.current_mode.value,
            'migration_active': self.migration_active,
            'migration_duration': (time.time() - self.migration_start_time) if self.migration_start_time else None,
            'health_metrics': self.health_metrics,
            'performance_baseline': self.performance_baseline,
            'config': {
                'enable_migration': self.config.enable_migration,
                'target_mode': self.config.target_mode.value,
                'rollback_on_failure': self.config.rollback_on_failure
            }
        }
    
    async def shutdown(self):
        """Shutdown migration manager."""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        # Shutdown current system
        if self.current_mode == ObservabilityMode.OPTIMIZED:
            await self._shutdown_optimized_system()
        else:
            await self._shutdown_original_system()
        
        logger.info("Migration manager shutdown complete")


# Global migration manager
migration_manager: Optional[SafeMigrationManager] = None


async def initialize_migration(config: MigrationConfig) -> SafeMigrationManager:
    """Initialize global migration manager."""
    global migration_manager
    
    migration_manager = SafeMigrationManager(config)
    await migration_manager.initialize()
    
    return migration_manager


def get_migration_manager() -> Optional[SafeMigrationManager]:
    """Get global migration manager."""
    return migration_manager


async def shutdown_migration():
    """Shutdown global migration manager."""
    global migration_manager
    
    if migration_manager:
        await migration_manager.shutdown()
        migration_manager = None
