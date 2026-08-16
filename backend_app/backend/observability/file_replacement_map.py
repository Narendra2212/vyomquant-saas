
"""
File Replacement Map for Safe Observability Migration

Defines exact file replacements and import changes needed for migration.
Ensures no code duplication and clean transitions.

Author: Senior Low-Latency Trading Infrastructure Engineer
"""
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("file_replacement_map")


class ReplacementType(Enum):
    """Types of file replacements."""
    DIRECT_REPLACE = "direct_replace"      # Complete file replacement
    IMPORT_ALIAS = "import_alias"          # Use import aliasing
    CONDITIONAL_IMPORT = "conditional_import" # Environment-based imports
    ADAPTER_PATTERN = "adapter_pattern"   # Use adapter pattern


@dataclass
class FileReplacement:
    """File replacement specification."""
    original_file: str
    optimized_file: str
    replacement_type: ReplacementType
    import_changes: List[str]
    rollback_changes: List[str]
    dependencies: List[str]
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    notes: str


class ObservabilityFileReplacementMap:
    """Complete file replacement map for observability migration."""
    
    def __init__(self):
        self.replacement_map = self._build_replacement_map()
        self.import_map = self._build_import_map()
        self.rollback_map = self._build_rollback_map()
        logger.info("File replacement map initialized")
    
    def _build_replacement_map(self) -> Dict[str, FileReplacement]:
        """Build complete file replacement map."""
        return {
            # Metrics Exporter
            'metrics_exporter': FileReplacement(
                original_file='backend/observability/metrics_exporter.py',
                optimized_file='backend/observability/optimized_metrics_exporter.py',
                replacement_type=ReplacementType.DIRECT_REPLACE,
                import_changes=[
                    'from backend.observability.optimized_metrics_exporter import optimized_metrics_collector',
                    'from backend.observability.optimized_metrics_exporter import HFTExecutionMetrics',
                    'from backend.observability.optimized_metrics_exporter import HFTWebSocketMetrics'
                ],
                rollback_changes=[
                    'from backend.observability.metrics_exporter import metrics_collector',
                    'from backend.observability.metrics_exporter import ExecutionMetrics',
                    'from backend.observability.metrics_exporter import WebSocketMetrics'
                ],
                dependencies=['prometheus-client', 'psutil'],
                risk_level='CRITICAL',
                notes='Complete replacement with HFT optimizations. Port conflict requires careful management.'
            ),
            
            # Health Checks
            'health_checks': FileReplacement(
                original_file='backend/observability/health_checks.py',
                optimized_file='backend/observability/optimized_health_checks.py',
                replacement_type=ReplacementType.DIRECT_REPLACE,
                import_changes=[
                    'from backend.observability.optimized_health_checks import optimized_health_check_manager',
                    'from backend.observability.optimized_health_checks import OptimizedDatabaseHealthChecker',
                    'from backend.observability.optimized_health_checks import OptimizedRedisHealthChecker'
                ],
                rollback_changes=[
                    'from backend.observability.health_checks import health_check_manager',
                    'from backend.observability.health_checks import DatabaseHealthChecker',
                    'from backend.observability.health_checks import RedisHealthChecker'
                ],
                dependencies=['psutil'],
                risk_level='HIGH',
                notes='Non-blocking health checks with caching. Timeout changes affect monitoring.'
            ),
            
            # OpenTelemetry Tracing
            'opentelemetry_tracing': FileReplacement(
                original_file='backend/observability/opentelemetry_tracing.py',
                optimized_file='backend/observability/optimized_tracing.py',
                replacement_type=ReplacementType.DIRECT_REPLACE,
                import_changes=[
                    'from backend.observability.optimized_tracing import get_optimized_trading_tracer',
                    'from backend.observability.optimized_tracing import initialize_optimized_tracing',
                    'from backend.observability.optimized_tracing import trace_execution_optimized'
                ],
                rollback_changes=[
                    'from backend.observability.opentelemetry_tracing import get_trading_tracer',
                    'from backend.observability.opentelemetry_tracing import initialize_tracing',
                    'from backend.observability.opentelemetry_tracing import trace_execution'
                ],
                dependencies=['opentelemetry-api', 'opentelemetry-sdk', 'opentelemetry-exporter-otlp-proto-grpc'],
                risk_level='CRITICAL',
                notes='Adaptive sampling with HFT optimizations. Sampling rate changes affect tracing coverage.'
            ),
            
            # Logging System
            'logging_config': FileReplacement(
                original_file='backend/logging_config.py',
                optimized_file='backend/observability/optimized_logging.py',
                replacement_type=ReplacementType.IMPORT_ALIAS,
                import_changes=[
                    'from backend.observability.optimized_logging import setup_optimized_logging',
                    'from backend.observability.optimized_logging import get_optimized_logger',
                    'from backend.observability.optimized_logging import log_trade_optimized'
                ],
                rollback_changes=[
                    'from backend.logging_config import setup_logging',
                    'from backend.logging_config import get_logger',
                    'from backend.logging_config import log_trade'
                ],
                dependencies=['aiohttp'],
                risk_level='MEDIUM',
                notes='Async logging with batching. Can coexist with original logging during migration.'
            ),
            
            # Application Entry Points
            'main_app': FileReplacement(
                original_file='backend/main.py',
                optimized_file='backend/main.py',  # Same file, different imports
                replacement_type=ReplacementType.CONDITIONAL_IMPORT,
                import_changes=[
                    '# Conditional imports based on OBSERVABILITY_MODE',
                    'import os',
                    'if os.getenv("OBSERVABILITY_MODE") == "optimized":',
                    '    from backend.observability.optimized_metrics_exporter import optimized_metrics_collector as metrics_collector',
                    '    from backend.observability.optimized_health_checks import optimized_health_check_manager as health_check_manager',
                    'else:',
                    '    from backend.observability.metrics_exporter import metrics_collector',
                    '    from backend.observability.health_checks import health_check_manager'
                ],
                rollback_changes=[
                    'Remove conditional imports',
                    'Restore original import statements'
                ],
                dependencies=['os'],
                risk_level='HIGH',
                notes='Main application entry point needs environment-based import switching.'
            ),
            
            # Docker Configuration
            'docker_compose': FileReplacement(
                original_file='docker-compose.monitoring.yml',
                optimized_file='docker-compose.monitoring.yml',  # Same file, different config
                replacement_type=ReplacementType.CONDITIONAL_IMPORT,
                import_changes=[
                    '# Add optimized observability services',
                    'jaeger:',
                    '  image: jaegertracing/all-in-one:latest',
                    '  ports:',
                    '    - "16686:16686"',  # UI
                    '    - "4317:4317"',  # OTLP gRPC
                    '    - "4318:4318"',  # OTLP HTTP
                    '  environment:',
                    '    - COLLECTOR_OTLP_ENABLED=true',
                    'loki:',
                    '  image: grafana/loki:latest',
                    '  ports:',
                    '    - "3100:3100"',
                    'promtail:',
                    '  image: grafana/promtail:latest'
                ],
                rollback_changes=[
                    'Remove optimized services from docker-compose',
                    'Restore original configuration'
                ],
                dependencies=['docker'],
                risk_level='LOW',
                notes='Add Jaeger with OTLP enabled (Jaeger native support). OTLP ports 4317/4318 required for trace export.'
            ),
            
            # Environment Configuration
            'environment': FileReplacement(
                original_file='.env',
                optimized_file='.env',  # Same file, different values
                replacement_type=ReplacementType.CONDITIONAL_IMPORT,
                import_changes=[
                    '# Optimized observability settings',
                    'OBSERVABILITY_MODE=optimized',
                    'PROMETHEUS_PORT=8081',
                    'TRACING_SAMPLE_RATE=0.01',
                    'LOGGING_MODE=async'
                ],
                rollback_changes=[
                    'Set OBSERVABILITY_MODE=original',
                    'Restore original environment variables'
                ],
                dependencies=[],
                risk_level='LOW',
                notes='Environment variables control which observability system is active.'
            )
        }
    
    def _build_import_map(self) -> Dict[str, Dict[str, str]]:
        """Build import mapping for conditional imports."""
        return {
            'original': {
                'metrics_collector': 'backend.observability.metrics_exporter.metrics_collector',
                'health_check_manager': 'backend.observability.health_checks.health_check_manager',
                'trading_tracer': 'backend.observability.opentelemetry_tracing.get_trading_tracer',
                'setup_logging': 'backend.logging_config.setup_logging',
                'get_logger': 'backend.logging_config.get_logger'
            },
            'optimized': {
                'metrics_collector': 'backend.observability.optimized_metrics_exporter.optimized_metrics_collector',
                'health_check_manager': 'backend.observability.optimized_health_checks.optimized_health_check_manager',
                'trading_tracer': 'backend.observability.optimized_tracing.get_optimized_trading_tracer',
                'setup_logging': 'backend.observability.optimized_logging.setup_optimized_logging',
                'get_logger': 'backend.observability.optimized_logging.get_optimized_logger'
            }
        }
    
    def _build_rollback_map(self) -> Dict[str, List[str]]:
        """Build rollback procedures for each module."""
        return {
            'metrics_exporter': [
                '1. Stop optimized metrics collector: await optimized_metrics_collector.stop()',
                '2. Remove optimized import: from backend.observability.optimized_metrics_exporter import...',
                '3. Restore original import: from backend.observability.metrics_exporter import...',
                '4. Restart services: docker-compose restart backend'
            ],
            'health_checks': [
                '1. Stop optimized health checks: await optimized_health_check_manager.shutdown()',
                '2. Remove optimized import statements',
                '3. Restore original import statements',
                '4. Restart services'
            ],
            'opentelemetry_tracing': [
                '1. Stop optimized tracing: await shutdown_optimized_tracing()',
                '2. Remove optimized imports',
                '3. Restore original imports',
                '4. Clear any cached tracing data'
            ],
            'logging_config': [
                '1. Stop async logging: await shutdown_optimized_logging()',
                '2. Remove optimized imports',
                '3. Restore original imports',
                '4. Restart application'
            ],
            'main_app': [
                '1. Set OBSERVABILITY_MODE=original',
                '2. Remove conditional import logic',
                '3. Restore original import statements',
                '4. Restart application'
            ],
            'docker_compose': [
                '1. Remove optimized services from docker-compose.yml',
                '2. docker-compose down',
                '3. docker-compose up -d',
                '4. Verify service health'
            ],
            'environment': [
                '1. Set OBSERVABILITY_MODE=original',
                '2. Restore original .env from backup',
                '3. Restart services with new environment'
            ]
        }
    
    def get_replacement(self, module_name: str) -> Optional[FileReplacement]:
        """Get replacement specification for a module."""
        return self.replacement_map.get(module_name)
    
    def get_import_path(self, module_name: str, mode: str = 'original') -> Optional[str]:
        """Get import path for a module based on mode."""
        import_map = self.import_map.get(mode)
        if import_map:
            return import_map.get(module_name)
        return None
    
    def get_rollback_procedure(self, module_name: str) -> List[str]:
        """Get rollback procedure for a module."""
        return self.rollback_map.get(module_name, [])
    
    def get_all_replacements(self) -> Dict[str, FileReplacement]:
        """Get all replacement specifications."""
        return self.replacement_map
    
    def get_critical_replacements(self) -> List[str]:
        """Get list of critical replacements requiring special care."""
        critical = []
        for module, replacement in self.replacement_map.items():
            if replacement.risk_level == 'CRITICAL':
                critical.append(module)
        return critical
    
    def get_replacement_order(self) -> List[str]:
        """Get recommended order for applying replacements."""
        return [
            'environment',      # 1. Set environment variables
            'docker_compose',   # 2. Update infrastructure
            'logging_config',   # 3. Update logging (low risk)
            'health_checks',     # 4. Update health checks (high risk)
            'metrics_exporter', # 5. Update metrics (critical risk)
            'opentelemetry_tracing', # 6. Update tracing (critical risk)
            'main_app'          # 7. Update application entry point
        ]
    
    def generate_replacement_script(self, module_name: str, target_mode: str = 'optimized') -> str:
        """Generate replacement script for a specific module."""
        replacement = self.get_replacement(module_name)
        if not replacement:
            return f"# No replacement found for module: {module_name}"
        
        rep_type = str(replacement.replacement_type.value)
        orig_file = str(replacement.original_file)
        opt_file = str(replacement.optimized_file)
        
        parts = [
            "#!/bin/bash",
            f"# Replacement: {module_name} -> {target_mode}",
            "set -e",
            f'echo "Starting replacement: {module_name}"',
            f'if [ -f "{orig_file}" ]; then',
            '    echo "Backing up..."',
            f'    cp "{orig_file}" "{orig_file}.backup.$(date +%Y%m%d_%H%M%S)"',
            "fi",
            f'case "{rep_type}" in',
            '    "direct_replace")',
            f'        if [ -f "{opt_file}" ]; then',
            f'            cp "{opt_file}" "{orig_file}"',
            '        fi',
            '        ;;',
            'esac',
            f'echo "Done: {module_name}"'
        ]
        return "\n".join(parts)
    
    def generate_rollback_script(self, module_name: str) -> str:
        """Generate rollback script for a specific module."""
        rollback_steps = self.get_rollback_procedure(module_name)
        if not rollback_steps:
            return f"# No rollback procedure found for module: {module_name}"
        
        script = f"""#!/bin/bash
# Rollback Script: {module_name}
# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}

set -e

echo "Starting rollback: {module_name}"

# Rollback steps
"""
        
        for i, step in enumerate(rollback_steps, 1):
            script += f"""
# Step {i}: {step}
echo "Executing step {i}..."
{step}
"""
        
        script += """
echo "Rollback completed for {module_name}"
"""
        return script


# Global file replacement map
file_replacement_map = ObservabilityFileReplacementMap()
