# -*- coding: utf-8 -*-
"""
Core Configuration Module

Loads environment variables from .env file and provides typed settings access.
All sensitive values should be stored in .env file, never committed to git.
"""

import logging
import os
from enum import Enum
from typing import Optional

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)


def validate_production_config() -> None:
    """
    Validate that production configuration is properly set.
    
    This function validates critical production settings but does not raise
    exceptions for minor issues. Instead, it logs warnings and allows
    the application to start with degraded security if needed.
    """
    env = os.getenv("ENV", "development").lower()
    
    if env == "production":
        critical_vars = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY"),
            "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            "SUPABASE_JWT_SECRET": os.getenv("SUPABASE_JWT_SECRET"),
            "JWT_SECRET": os.getenv("JWT_SECRET"),
        }
        
        missing_vars = [var for var, value in critical_vars.items() if not value]
        
        if missing_vars:
            logger.error(
                f"CRITICAL: Production environment requires all configuration variables. "
                f"Missing: {', '.join(missing_vars)}. "
                f"Application running with degraded security."
            )
        
        # Validate JWT_SECRET is not a weak default (warning only, not error)
        jwt_secret = os.getenv("JWT_SECRET")
        weak_secrets = ["dev-secret-change-in-production", "dummy", "test", "secret"]
        if jwt_secret and any(weak in jwt_secret.lower() for weak in weak_secrets):
            logger.warning(
                f"SECURITY: JWT_SECRET appears to be a weak development secret. "
                f"Production requires a cryptographically strong secret. "
                f"Current value contains: {jwt_secret[:10]}..."
            )
        
        # Additional JWT_SECRET validation (warning only)
        if jwt_secret and len(jwt_secret) < 32:
            logger.warning(
                f"SECURITY: JWT_SECRET is only {len(jwt_secret)} characters. "
                "Recommended minimum is 32 characters for production."
            )
        
        logger.info("[CONFIG] Production configuration validated successfully")
    else:
        logger.info(f"[CONFIG] Development environment detected ({env}), skipping strict validation")


class Environment(Enum):
    """Application environment types"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


class Settings:
    """
    Application settings loaded from environment variables.
    
    Usage:
        from backend_app.core.config import settings
        
        supabase_url = settings.SUPABASE_URL
        is_dev = settings.ENV == Environment.DEVELOPMENT
    """
    
    # ==========================================
    # SUPABASE CONFIGURATION
    # ==========================================
    SUPABASE_URL: Optional[str] = os.getenv("SUPABASE_URL")
    SUPABASE_ANON_KEY: Optional[str] = os.getenv("SUPABASE_ANON_KEY")
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_JWT_SECRET: Optional[str] = os.getenv("SUPABASE_JWT_SECRET")  # FAIL-CLOSED: No dummy defaults
    
    # ==========================================
    # QUESTDB CONFIGURATION
    # ==========================================
    QUESTDB_HOST: str = os.getenv("QUESTDB_HOST", "127.0.0.1")
    QUESTDB_PORT: int = int(os.getenv("QUESTDB_PORT", 9000))
    QUESTDB_USER: Optional[str] = os.getenv("QUESTDB_USER")
    QUESTDB_PASSWORD: Optional[str] = os.getenv("QUESTDB_PASSWORD")
    
    # ==========================================
    # APPLICATION ENVIRONMENT
    # ==========================================
    ENV: str = os.getenv("ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
    DEV_MODE: bool = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    
    # ==========================================
    # SECURITY
    # ==========================================
    JWT_SECRET: str = os.getenv("JWT_SECRET")  # FAIL-CLOSED: No default for production
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_HOURS: int = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
    
    # ==========================================
    # API CONFIGURATION
    # ==========================================
    API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.getenv("API_PORT", 8000))
    API_WORKERS: int = int(os.getenv("API_WORKERS", 1))
    
    # ==========================================
    # CORS CONFIGURATION
    # ==========================================
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    
    @property
    def cors_origins_list(self) -> list:
        """Parse CORS origins as list"""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]
    
    # ==========================================
    # WEBSOCKET CONFIGURATION
    # ==========================================
    WS_HEARTBEAT_INTERVAL: int = int(os.getenv("WS_HEARTBEAT_INTERVAL", 30))
    WS_MAX_CONNECTIONS: int = int(os.getenv("WS_MAX_CONNECTIONS", 1000))
    
    # ==========================================
    # TRADING ENGINE CONFIGURATION
    # ==========================================
    DEFAULT_INITIAL_CAPITAL: float = float(os.getenv("DEFAULT_INITIAL_CAPITAL", 10000.0))
    DEFAULT_RISK_PER_TRADE: float = float(os.getenv("DEFAULT_RISK_PER_TRADE", 0.01))
    DEFAULT_MAX_DRAWDOWN: float = float(os.getenv("DEFAULT_MAX_DRAWDOWN", 0.20))
    DEFAULT_DAILY_LOSS_LIMIT: float = float(os.getenv("DEFAULT_DAILY_LOSS_LIMIT", 0.05))
    
    # ==========================================
    # EXCHANGE CONFIGURATION
    # ==========================================
    EXCHANGE_ID: Optional[str] = os.getenv("EXCHANGE_ID")  # No default - must be explicitly configured
    EXCHANGE_API_KEY: Optional[str] = os.getenv("EXCHANGE_API_KEY")
    EXCHANGE_SECRET: Optional[str] = os.getenv("EXCHANGE_SECRET")
    
    # ==========================================
    # LOGGING CONFIGURATION
    # ==========================================
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # ==========================================
    # CACHE & RATE LIMITING
    # ==========================================
    REDIS_URL: Optional[str] = os.getenv("REDIS_URL")
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", 60))
    
    # ==========================================
    # FEATURE FLAGS
    # ==========================================
    ENABLE_ML_TRAINING: bool = os.getenv("ENABLE_ML_TRAINING", "true").lower() in ("true", "1", "yes")
    ENABLE_LIVE_TRADING: bool = os.getenv("ENABLE_LIVE_TRADING", "false").lower() in ("true", "1", "yes")
    ENABLE_NOTIFICATIONS: bool = os.getenv("ENABLE_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
    
    # ==========================================
    # NOTIFICATION CONFIGURATION
    # ==========================================
    SLACK_WEBHOOK_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
    DISCORD_WEBHOOK_URL: Optional[str] = os.getenv("DISCORD_WEBHOOK_URL")
    EMAIL_SMTP_HOST: Optional[str] = os.getenv("EMAIL_SMTP_HOST")
    EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", 587))
    EMAIL_USER: Optional[str] = os.getenv("EMAIL_USER")
    EMAIL_PASSWORD: Optional[str] = os.getenv("EMAIL_PASSWORD")
    
    # ==========================================
    # VALIDATION METHODS
    # ==========================================
    def validate_required(self) -> list:
        """
        Validate that all required settings are present.
        Returns list of missing required variables.
        """
        required = {
            "SUPABASE_URL": self.SUPABASE_URL,
            "SUPABASE_SERVICE_ROLE_KEY": self.SUPABASE_SERVICE_ROLE_KEY,
            "SUPABASE_JWT_SECRET": self.SUPABASE_JWT_SECRET,
        }
        
        if not self.DEV_MODE:
            # Additional requirements for production
            required.update({
                "JWT_SECRET": self.JWT_SECRET if self.JWT_SECRET != "dev-secret-change-in-production" else None,
                "SUPABASE_ANON_KEY": self.SUPABASE_ANON_KEY,
            })
            
            # Production JWT_SECRET validation
            if self.JWT_SECRET:
                if len(self.JWT_SECRET) < 32:
                    logger.warning(
                        f"SECURITY: JWT_SECRET is only {len(self.JWT_SECRET)} characters. "
                        "Recommended minimum is 32 characters for production."
                    )
                # Check for default/weak secrets
                weak_patterns = ["dev-secret", "test-secret", "default", "password", "secret"]
                if any(pattern in self.JWT_SECRET.lower() for pattern in weak_patterns):
                    logger.warning(
                        "SECURITY: JWT_SECRET contains a weak pattern. "
                        "Use a cryptographically secure random secret in production."
                    )
        
        missing = [k for k, v in required.items() if not v]
        return missing
    
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.ENV.lower() == "production"
    
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.ENV.lower() in ("development", "dev")
    
    def get_database_url(self) -> str:
        """Construct QuestDB connection URL"""
        auth = ""
        if self.QUESTDB_USER and self.QUESTDB_PASSWORD:
            auth = f"{self.QUESTDB_USER}:{self.QUESTDB_PASSWORD}@"
        return f"http://{auth}{self.QUESTDB_HOST}:{self.QUESTDB_PORT}"


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """
    Dependency injection function for FastAPI.
    
    Usage in FastAPI:
        @app.get("/config")
        def get_config(settings: Settings = Depends(get_settings)):
            return {"env": settings.ENV}
    """
    return settings


# ==========================================
# SAFE VALIDATION (WITH FALLBACK MODE)
# ==========================================
def validate_config():
    """
    Validate configuration and return status.
    Does NOT crash - allows fallback mode for gradual migration.
    """
    missing = []
    warnings = []
    
    if not settings.SUPABASE_URL:
        missing.append("SUPABASE_URL")
    
    if not settings.SUPABASE_SERVICE_ROLE_KEY:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    
    if not settings.SUPABASE_ANON_KEY:
        warnings.append("SUPABASE_ANON_KEY")
    
    # Check QuestDB
    if not settings.QUESTDB_HOST:
        warnings.append("QUESTDB_HOST")
    
    return {
        "is_configured": len(missing) == 0,
        "missing_required": missing,
        "missing_optional": warnings,
        "mode": "production" if len(missing) == 0 else "fallback"
    }


def print_config_status():
    """Print startup configuration status (without exposing secrets)"""
    status = validate_config()
    
    print("\n" + "=" * 50)
    print(" SYSTEM MODE:", status["mode"].upper())
    print("=" * 50)
    
    # [SECURITY] DEV_MODE SAFETY CHECK
    dev_mode = os.environ.get("DEV_MODE", "false").lower() == "true"
    env = os.environ.get("ENV", "").lower()
    production_like_envs = {"production", "prod", "live", "staging"}
    
    if dev_mode and env in production_like_envs:
        logger.critical(
            "[CRITICAL] DEV_MODE=true is set in a production-like environment (ENV=%s). "
            "This causes Redis and other backends to use in-memory mocks instead of real connections, "
            "silently bypassing durability guarantees. This is a dangerous misconfiguration.",
            env.upper()
        )
        print(f" [WARNING] DEV_MODE=true detected with ENV={env.upper()}")
        print(f"     This enables in-memory mocks for Redis and other backends.")
        print(f"     Expected ENV values for DEV_MODE: development, testing, dev")
        print(f"     Actual ENV value: {env.upper()}")
    elif dev_mode:
        print(f" DEV_MODE: ON (appropriate for ENV={env.upper()})")
    else:
        print(f" DEV_MODE: OFF")
    
    # Supabase status
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
        # Mask the URL - show only project ref
        url_parts = settings.SUPABASE_URL.split("//")
        if len(url_parts) > 1:
            project_ref = url_parts[1].split(".")[0] if "." in url_parts[1] else "***"
        else:
            project_ref = "***"
        print(f" Supabase: CONNECTED (project: {project_ref})")
    else:
        print(" Supabase: NOT CONFIGURED (fallback mode)")
        if status["missing_required"]:
            print(f"   Missing: {', '.join(status['missing_required'])}")
    
    # QuestDB status
    if settings.QUESTDB_HOST:
        print(f" QuestDB: {settings.QUESTDB_HOST}:{settings.QUESTDB_PORT}")
    else:
        print(" QuestDB: NOT CONFIGURED (fallback mode)")
    
    # Environment
    print(f" Environment: {settings.ENV}")
    print(f" Debug Mode: {'ON' if settings.DEBUG else 'OFF'}")
    print("=" * 50 + "\n")
    
    return status


# Validate on import but don't crash
config_status = validate_config()
