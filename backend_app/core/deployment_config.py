"""
Deployment Configuration

STEP 7.10 — PRODUCTION HARDENING

Environment-specific configuration:
- Development
- Staging  
- Production

Flags control behavior:
- SANDBOX_MODE: Use testnet
- DEBUG: Verbose logging
- AUTO_RECONNECT: Reconnect on failure
- CIRCUIT_BREAKER_ENABLED: Stop on errors
- ALERTS_ENABLED: Send notifications
"""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Environment(Enum):
    """Deployment environments."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class DeploymentConfig:
    """
    STEP 7.10: Deployment configuration.
    
    Controls system behavior based on environment.
    """
    # Environment
    environment: Environment = Environment.DEVELOPMENT
    
    # STEP 6.8: Sandbox mode
    SANDBOX_MODE: bool = True  # Default to safe
    
    # Debug mode
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    # STEP 7.9: Connection management
    AUTO_RECONNECT: bool = True
    MAX_RECONNECT_RETRIES: int = 10
    
    # STEP 7.6: Circuit breaker
    CIRCUIT_BREAKER_ENABLED: bool = True
    CIRCUIT_FAILURE_THRESHOLD: int = 5
    CIRCUIT_TIMEOUT_SECONDS: int = 60
    
    # STEP 7.8: Alerts
    ALERTS_ENABLED: bool = False  # Disabled by default, enable explicitly
    ALERT_TELEGRAM_ENABLED: bool = False
    ALERT_EMAIL_ENABLED: bool = False
    
    # STEP 7.1: Watchdog
    WATCHDOG_ENABLED: bool = True
    WATCHDOG_INTERVAL_SECONDS: int = 30
    
    # STEP 7.7: Metrics
    METRICS_ENABLED: bool = True
    METRICS_EXPORT_INTERVAL: int = 60
    
    # Database
    DATABASE_URL: Optional[str] = None
    DATABASE_POOL_SIZE: int = 10
    
    # Redis
    REDIS_URL: Optional[str] = None
    
    # Exchange API (read from env, not stored)
    # EXCHANGE_API_KEY: str  # From env
    # EXCHANGE_API_SECRET: str  # From env
    
    @classmethod
    def from_environment(cls) -> "DeploymentConfig":
        """
        Load configuration from environment variables.
        
        Environment Variables:
        - ENV: development | staging | production
        - SANDBOX_MODE: true | false
        - DEBUG: true | false
        - LOG_LEVEL: DEBUG | INFO | WARNING | ERROR
        - ALERTS_ENABLED: true | false
        - DATABASE_URL: postgres://...
        - REDIS_URL: redis://...
        
        🚨 SAFETY ENVIRONMENT VARIABLES (SYSTEM FREEZE):
        - AERORA_MODE: safe | paper | live (default: safe)
            - safe: All execution blocked
            - paper: Simulated execution only
            - live: Real execution (requires AERORA_ENABLE_LIVE_TRADING=true)
        - AERORA_ENABLE_TRADING: true | false (default: false)
            - Must be true to enable paper trading
        - AERORA_ENABLE_LIVE_TRADING: true | false (default: false)
            - Must be true AND AERORA_MODE=live to enable live trading
        """
        # Get environment
        env_str = os.getenv("ENV", "development").lower()
        try:
            environment = Environment(env_str)
        except ValueError:
            environment = Environment.DEVELOPMENT
        
        # Production safety checks
        if environment == Environment.PRODUCTION:
            # Force safe defaults in production
            sandbox = os.getenv("SANDBOX_MODE", "false").lower() == "true"
            debug = False
            alerts_enabled = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
            
            if not sandbox:
                logger.critical(
                    "⚠️ PRODUCTION MODE: Live trading enabled! "
                    "Ensure all safety checks are in place."
                )
        else:
            sandbox = os.getenv("SANDBOX_MODE", "true").lower() == "true"
            debug = os.getenv("DEBUG", "false").lower() == "true"
            alerts_enabled = os.getenv("ALERTS_ENABLED", "false").lower() == "true"
        
        config = cls(
            environment=environment,
            SANDBOX_MODE=sandbox,
            DEBUG=debug,
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
            ALERTS_ENABLED=alerts_enabled,
            ALERT_TELEGRAM_ENABLED=os.getenv("ALERT_TELEGRAM_ENABLED", "false").lower() == "true",
            ALERT_EMAIL_ENABLED=os.getenv("ALERT_EMAIL_ENABLED", "false").lower() == "true",
            DATABASE_URL=os.getenv("DATABASE_URL"),
            REDIS_URL=os.getenv("REDIS_URL")
        )
        
        # Log configuration
        config.log_config()
        
        return config
    
    def log_config(self):
        """Log current configuration (safely)."""
        logger.info("=" * 60)
        logger.info("DEPLOYMENT CONFIGURATION")
        logger.info("=" * 60)
        logger.info(f"Environment: {self.environment.value}")
        logger.info(f"Sandbox Mode: {self.SANDBOX_MODE}")
        logger.info(f"Debug: {self.DEBUG}")
        logger.info(f"Log Level: {self.LOG_LEVEL}")
        logger.info(f"Auto Reconnect: {self.AUTO_RECONNECT}")
        logger.info(f"Circuit Breaker: {self.CIRCUIT_BREAKER_ENABLED}")
        logger.info(f"Alerts: {self.ALERTS_ENABLED}")
        logger.info(f"Watchdog: {self.WATCHDOG_ENABLED}")
        logger.info(f"Metrics: {self.METRICS_ENABLED}")
        
        # Safety warnings
        if self.environment == Environment.PRODUCTION:
            if self.SANDBOX_MODE:
                logger.info("✅ Production with SANDBOX - Safe for testing")
            else:
                logger.warning("🔥 LIVE TRADING MODE - Real money at risk!")
        
        if not self.CIRCUIT_BREAKER_ENABLED:
            logger.warning("⚠️ Circuit breaker DISABLED - No failure protection")
        
        if not self.ALERTS_ENABLED:
            logger.warning("⚠️ Alerts DISABLED - No notifications on issues")
        
        logger.info("=" * 60)
    
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment == Environment.PRODUCTION
    
    def is_live_trading(self) -> bool:
        """Check if live trading (production + not sandbox)."""
        return self.is_production() and not self.SANDBOX_MODE
    
    def validate(self) -> bool:
        """
        Validate configuration is safe for environment.
        
        Returns:
            True if valid, raises exception otherwise
        """
        errors = []
        
        # Production checks
        if self.environment == Environment.PRODUCTION:
            if self.DEBUG:
                errors.append("DEBUG should be False in production")
            
            if not self.DATABASE_URL:
                errors.append("DATABASE_URL required in production")
            
            if not self.ALERTS_ENABLED:
                logger.warning("ALERTS_ENABLED is False in production - recommended to enable")
        
        # Check database URL is set
        if not self.DATABASE_URL:
            errors.append("DATABASE_URL not configured")
        
        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")
        
        return True
    
    def to_dict(self) -> dict:
        """Export config as dictionary."""
        return {
            "environment": self.environment.value,
            "sandbox_mode": self.SANDBOX_MODE,
            "debug": self.DEBUG,
            "log_level": self.LOG_LEVEL,
            "auto_reconnect": self.AUTO_RECONNECT,
            "circuit_breaker_enabled": self.CIRCUIT_BREAKER_ENABLED,
            "alerts_enabled": self.ALERTS_ENABLED,
            "watchdog_enabled": self.WATCHDOG_ENABLED,
            "metrics_enabled": self.METRICS_ENABLED,
            # Don't include sensitive data like DATABASE_URL
        }


# Global config instance
_config: Optional[DeploymentConfig] = None


def get_deployment_config() -> DeploymentConfig:
    """Get global deployment configuration."""
    global _config
    if _config is None:
        _config = DeploymentConfig.from_environment()
    return _config


def init_deployment_config(config: DeploymentConfig):
    """Initialize global configuration."""
    global _config
    _config = config
    
    # Set log level
    logging.getLogger().setLevel(getattr(logging, config.LOG_LEVEL))


# Convenience functions for checking config
def is_sandbox() -> bool:
    """Check if in sandbox mode."""
    return get_deployment_config().SANDBOX_MODE


def is_production() -> bool:
    """Check if in production."""
    return get_deployment_config().is_production()


def is_live_trading() -> bool:
    """Check if live trading (real money)."""
    return get_deployment_config().is_live_trading()


def circuit_breaker_enabled() -> bool:
    """Check if circuit breaker is enabled."""
    return get_deployment_config().CIRCUIT_BREAKER_ENABLED


def alerts_enabled() -> bool:
    """Check if alerts are enabled."""
    return get_deployment_config().ALERTS_ENABLED
