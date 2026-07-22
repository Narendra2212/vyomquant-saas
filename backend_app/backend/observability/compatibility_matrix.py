
"""
Observability Compatibility Matrix

Defines compatibility between original and optimized observability systems.
Ensures safe integration without conflicts.

Author: Senior Low-Latency Trading Infrastructure Engineer
"""
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("compatibility_matrix")


class CompatibilityLevel(Enum):
    """Compatibility levels between modules."""
    FULL = "full"          # Fully compatible, can run together
    PARTIAL = "partial"      # Compatible with limitations
    CONFLICT = "conflict"    # Direct conflicts, cannot run together
    UNKNOWN = "unknown"      # Compatibility not tested


@dataclass
class ModuleCompatibility:
    """Compatibility information for a module."""
    module_name: str
    original_version: str
    optimized_version: str
    compatibility_level: CompatibilityLevel
    conflicts: List[str]
    limitations: List[str]
    migration_path: str
    rollback_path: str
    notes: str


class ObservabilityCompatibilityMatrix:
    """Compatibility matrix for observability modules."""
    
    def __init__(self):
        self.compatibility_matrix = self._build_compatibility_matrix()
        logger.info("Compatibility matrix initialized")
    
    def _build_compatibility_matrix(self) -> Dict[str, ModuleCompatibility]:
        """Build the complete compatibility matrix."""
        return {
            'metrics_exporter': ModuleCompatibility(
                module_name='metrics_exporter',
                original_version='1.0.0',
                optimized_version='2.0.0-hft',
                compatibility_level=CompatibilityLevel.CONFLICT,
                conflicts=[
                    'Port collision (8080)',
                    'Duplicate Prometheus registry',
                    'Memory overhead from both systems',
                    'Metric naming conflicts'
                ],
                limitations=[
                    'Cannot run both simultaneously',
                    'Require port isolation',
                    'Need registry separation'
                ],
                migration_path='Replace import: from backend.observability.optimized_metrics_exporter import optimized_metrics_collector',
                rollback_path='Replace import: from backend.observability.metrics_exporter import metrics_collector',
                notes='Optimized version provides HFT-specific features: microsecond precision, adaptive sampling, cardinality limits'
            ),
            
            'health_checks': ModuleCompatibility(
                module_name='health_checks',
                original_version='1.0.0',
                optimized_version='2.0.0-hft',
                compatibility_level=CompatibilityLevel.PARTIAL,
                conflicts=[
                    'Different timeout configurations',
                    'Async vs sync execution models',
                    'Cache TTL differences'
                ],
                limitations=[
                    'Different performance characteristics',
                    'Separate caching mechanisms',
                    'Distinct health check endpoints'
                ],
                migration_path='Replace import: from backend.observability.optimized_health_checks import optimized_health_check_manager',
                rollback_path='Replace import: from backend.observability.health_checks import health_check_manager',
                notes='Optimized version provides non-blocking checks, adaptive timeouts, better HFT performance'
            ),
            
            'opentelemetry_tracing': ModuleCompatibility(
                module_name='opentelemetry_tracing',
                original_version='1.0.0',
                optimized_version='2.0.0-hft',
                compatibility_level=CompatibilityLevel.CONFLICT,
                conflicts=[
                    'Multiple tracer providers',
                    'Sampling rate conflicts',
                    'Span processor conflicts',
                    'Exporter endpoint conflicts'
                ],
                limitations=[
                    'Cannot run both simultaneously',
                    'Different sampling strategies',
                    'Separate span processors'
                ],
                migration_path='Replace import: from backend.observability.optimized_tracing import get_optimized_trading_tracer',
                rollback_path='Replace import: from backend.observability.opentelemetry_tracing import get_trading_tracer',
                notes='Optimized version provides adaptive sampling, high-frequency optimization, memory leak prevention'
            ),
            
            'logging': ModuleCompatibility(
                module_name='logging',
                original_version='1.0.0',
                optimized_version='2.0.0-hft',
                compatibility_level=CompatibilityLevel.PARTIAL,
                conflicts=[
                    'Handler conflicts on root logger',
                    'Different formatters',
                    'Async vs sync I/O patterns'
                ],
                limitations=[
                    'Different performance characteristics',
                    'Separate log queues',
                    'Distinct formatting approaches'
                ],
                migration_path='Replace import: from backend.observability.optimized_logging import setup_optimized_logging',
                rollback_path='Keep original import: from backend.logging_config import setup_logging',
                notes='Optimized version provides async logging, batch processing, non-blocking I/O'
            ),
            
            'websocket_integration': ModuleCompatibility(
                module_name='websocket_integration',
                original_version='1.0.0',
                optimized_version='2.0.0-hft',
                compatibility_level=CompatibilityLevel.FULL,
                conflicts=[],
                limitations=[
                    'Requires adapter pattern for clean integration',
                    'Performance characteristics differ'
                ],
                migration_path='Use adapter pattern to route to optimized or original based on mode',
                rollback_path='Remove adapter, use direct integration',
                notes='WebSocket clients can work with both systems using adapter pattern'
            ),
            
            'telemetry_engine': ModuleCompatibility(
                module_name='telemetry_engine',
                original_version='1.0.0',
                optimized_version='2.0.0-hft',
                compatibility_level=CompatibilityLevel.FULL,
                conflicts=[],
                limitations=[
                    'Different time-series backends',
                    'Separate metric collection',
                    'Distinct query interfaces'
                ],
                migration_path='Keep both systems, use QuestDB for time-series, Prometheus for metrics',
                rollback_path='Disable optimized metrics, use only QuestDB',
                notes='Telemetry engine serves different purpose - can coexist with optimized metrics'
            )
        }
    
    def get_compatibility(self, module_name: str) -> Optional[ModuleCompatibility]:
        """Get compatibility information for a specific module."""
        return self.compatibility_matrix.get(module_name)
    
    def check_conflicts(self, modules: List[str]) -> List[str]:
        """Check for conflicts between multiple modules."""
        conflicts = []
        
        for module in modules:
            compat = self.get_compatibility(module)
            if compat:
                conflicts.extend(compat.conflicts)
        
        # Remove duplicates
        return list(set(conflicts))
    
    def get_migration_plan(self, module_name: str) -> Optional[str]:
        """Get migration plan for a specific module."""
        compat = self.get_compatibility(module_name)
        return compat.migration_path if compat else None
    
    def get_rollback_plan(self, module_name: str) -> Optional[str]:
        """Get rollback plan for a specific module."""
        compat = self.get_compatibility(module_name)
        return compat.rollback_path if compat else None
    
    def can_run_together(self, modules: List[str]) -> bool:
        """Check if multiple modules can run together."""
        for module in modules:
            compat = self.get_compatibility(module)
            if compat and compat.compatibility_level == CompatibilityLevel.CONFLICT:
                return False
        return True
    
    def get_safe_combinations(self) -> List[List[str]]:
        """Get safe combinations of modules that can run together."""
        safe_combinations = []
        
        # Original system
        safe_combinations.append([
            'metrics_exporter',
            'health_checks', 
            'opentelemetry_tracing',
            'logging'
        ])
        
        # Optimized system
        safe_combinations.append([
            'metrics_exporter',  # optimized version
            'health_checks',     # optimized version
            'opentelemetry_tracing',  # optimized version
            'logging'            # optimized version
        ])
        
        # Hybrid combinations
        safe_combinations.append([
            'telemetry_engine',  # Keep original
            'metrics_exporter',  # Use optimized
            'websocket_integration'
        ])
        
        safe_combinations.append([
            'telemetry_engine',  # Keep original
            'logging',           # Use optimized for performance
            'websocket_integration'
        ])
        
        return safe_combinations
    
    def get_environment_variables(self) -> Dict[str, str]:
        """Get required environment variables for migration."""
        return {
            # Migration control
            'OBSERVABILITY_MODE': 'original|optimized|hybrid|fallback',
            'MIGRATION_ENABLED': 'true|false',
            'MIGRATION_DRY_RUN': 'true|false',
            
            # Metrics configuration
            'PROMETHEUS_PORT': '8080|8081',  # Original | Optimized
            'PROMETHEUS_ENABLED': 'true|false',
            'METRICS_BATCH_SIZE': '100',
            'METRICS_SAMPLE_RATE': '1.0|0.01',  # Original | Optimized
            
            # Health check configuration
            'HEALTH_CHECK_TIMEOUT': '30.0|2.0',  # Original | Optimized
            'HEALTH_CHECK_CACHE_TTL': '10.0|2.0',  # Original | Optimized
            
            # Tracing configuration
            'TRACING_SAMPLE_RATE': '1.0|0.01',  # Original | Optimized
            'TRACING_STRATEGY': 'always|adaptive',
            'TRACING_MAX_SPANS': '10000|1000',  # Original | Optimized
            
            # Logging configuration
            'LOGGING_MODE': 'sync|async',
            'LOGGING_BATCH_SIZE': '1|100',  # Original | Optimized
            'LOGGING_QUEUE_SIZE': '0|10000',  # Original | Optimized
            
            # Fallback configuration
            'ROLLBACK_ON_FAILURE': 'true|false',
            'PERFORMANCE_THRESHOLD': '1.2',
            'HEALTH_CHECK_INTERVAL': '5.0'
        }
    
    def validate_configuration(self, config: Dict[str, str]) -> List[str]:
        """Validate migration configuration."""
        errors = []
        
        # Check required variables
        required_vars = ['OBSERVABILITY_MODE', 'MIGRATION_ENABLED']
        for var in required_vars:
            if var not in config:
                errors.append(f"Missing required environment variable: {var}")
        
        # Validate mode
        if 'OBSERVABILITY_MODE' in config:
            valid_modes = ['original', 'optimized', 'hybrid', 'fallback']
            if config['OBSERVABILITY_MODE'] not in valid_modes:
                errors.append(f"Invalid OBSERVABILITY_MODE: {config['OBSERVABILITY_MODE']}")
        
        # Validate ports
        if 'PROMETHEUS_PORT' in config:
            try:
                port = int(config['PROMETHEUS_PORT'])
                if port < 1024 or port > 65535:
                    errors.append(f"Invalid PROMETHEUS_PORT: {port}")
            except ValueError:
                errors.append(f"Invalid PROMETHEUS_PORT format: {config['PROMETHEUS_PORT']}")
        
        # Validate numeric values
        numeric_vars = [
            'METRICS_SAMPLE_RATE', 'TRACING_SAMPLE_RATE', 
            'PERFORMANCE_THRESHOLD', 'HEALTH_CHECK_INTERVAL'
        ]
        
        for var in numeric_vars:
            if var in config:
                try:
                    float_val = float(config[var])
                    if var in ['METRICS_SAMPLE_RATE', 'TRACING_SAMPLE_RATE']:
                        if not (0.0 <= float_val <= 1.0):
                            errors.append(f"Invalid {var}: must be between 0.0 and 1.0")
                    elif var == 'PERFORMANCE_THRESHOLD':
                        if not (1.0 <= float_val <= 5.0):
                            errors.append(f"Invalid {var}: must be between 1.0 and 5.0")
                    elif var == 'HEALTH_CHECK_INTERVAL':
                        if not (1.0 <= float_val <= 300.0):
                            errors.append(f"Invalid {var}: must be between 1.0 and 300.0")
                except ValueError:
                    errors.append(f"Invalid {var} format: {config[var]}")
        
        return errors
    
    def get_migration_checklist(self) -> Dict[str, List[str]]:
        """Get migration checklist for each module."""
        return {
            'pre_migration': [
                'Create backup of current configuration',
                'Verify all environment variables are set',
                'Test rollback procedures in staging',
                'Document current performance baseline',
                'Prepare monitoring for migration process',
                'Notify stakeholders of planned migration',
                'Schedule maintenance window if needed'
            ],
            
            'migration': [
                'Stop original observability services gracefully',
                'Deploy optimized observability modules',
                'Update import statements in application code',
                'Verify all services start correctly',
                'Run health checks on new system',
                'Monitor performance metrics',
                'Validate all endpoints are accessible',
                'Check for error logs or warnings'
            ],
            
            'post_migration': [
                'Compare performance against baseline',
                'Run full system integration tests',
                'Validate all monitoring dashboards',
                'Check alert configurations',
                'Document any configuration changes',
                'Update runbooks and procedures',
                'Monitor for 24-48 hours for stability'
            ],
            
            'rollback': [
                'Stop optimized observability services',
                'Restore original configuration',
                'Revert import statements',
                'Restart original services',
                'Verify system functionality',
                'Document rollback reasons',
                'Analyze root cause of migration failure'
            ]
        }
    
    def generate_migration_script(self, target_mode: str) -> str:
        """Generate migration script for target mode."""
        script = f"""#!/bin/bash
# Migration Script: {target_mode.upper()}
# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}

set -e

echo "Starting migration to {target_mode} observability..."

# Pre-migration checks
echo "Performing pre-migration checks..."
if [ ! -f ".env.backup" ]; then
    echo "Creating backup of current configuration..."
    cp .env .env.backup
fi

# Environment setup
echo "Setting up environment variables..."
export OBSERVABILITY_MODE="{target_mode}"
export MIGRATION_ENABLED="true"
export MIGRATION_START_TIME=$(date +%s)

# Migration execution
echo "Executing migration..."
case "{target_mode}" in
    "optimized")
        echo "Migrating to optimized observability..."
        export PROMETHEUS_PORT="8081"
        export PROMETHEUS_ENABLED="true"
        export METRICS_SAMPLE_RATE="0.01"
        export TRACING_SAMPLE_RATE="0.01"
        export TRACING_STRATEGY="adaptive"
        export LOGGING_MODE="async"
        export LOGGING_BATCH_SIZE="100"
        export LOGGING_QUEUE_SIZE="10000"
        export HEALTH_CHECK_TIMEOUT="2.0"
        export HEALTH_CHECK_CACHE_TTL="2.0"
        ;;
    "original")
        echo "Rolling back to original observability..."
        export PROMETHEUS_PORT="8080"
        export PROMETHEUS_ENABLED="true"
        export METRICS_SAMPLE_RATE="1.0"
        export TRACING_SAMPLE_RATE="1.0"
        export TRACING_STRATEGY="always"
        export LOGGING_MODE="sync"
        export LOGGING_BATCH_SIZE="1"
        export LOGGING_QUEUE_SIZE="0"
        export HEALTH_CHECK_TIMEOUT="30.0"
        export HEALTH_CHECK_CACHE_TTL="10.0"
        ;;
    "hybrid")
        echo "Setting up hybrid observability..."
        export PROMETHEUS_PORT="8080"
        export PROMETHEUS_ENABLED="true"
        export METRICS_SAMPLE_RATE="0.1"
        export TRACING_SAMPLE_RATE="0.1"
        export LOGGING_MODE="async"
        export LOGGING_BATCH_SIZE="50"
        export LOGGING_QUEUE_SIZE="5000"
        ;;
    *)
        echo "Unknown target mode: {target_mode}"
        exit 1
        ;;
esac

# Service restart
echo "Restarting services..."
docker-compose restart backend

# Post-migration validation
echo "Performing post-migration validation..."
sleep 30

# Health check
echo "Checking system health..."
curl -f http://localhost:8000/health || {{
    echo "Health check failed!"
    exit 1
}}

echo "Migration to {target_mode} completed successfully!"
echo "Migration duration: $(($(date +%s) - MIGRATION_START_TIME)) seconds"
"""
        return script


# Global compatibility matrix
compatibility_matrix = ObservabilityCompatibilityMatrix()
