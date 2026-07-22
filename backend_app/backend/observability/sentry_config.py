"""
Institutional-Grade Sentry Integration Configuration
===================================================
Features:
  - Sentry SDK initialization with failover.
  - Transaction tracing (10% sample rate).
  - Profiling (100% sample rate).
  - Sensitive data scrubbing (PII, credentials, API keys).
"""

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("sentry_integration")

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
    logger.warning("sentry-sdk package is not installed. Sentry tracing is disabled.")

def before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Scrub sensitive credentials (e.g. API keys, passwords, bearer tokens) 
    before sending data to Sentry.
    """
    # Scrub headers
    request = event.get("request", {})
    headers = request.get("headers", {})
    sensitive_headers = {"authorization", "x-api-key", "cookie", "x-simulated-trading"}
    
    for header in list(headers.keys()):
        if header.lower() in sensitive_headers:
            headers[header] = "[SCRUBBED]"
            
    # Scrub request body / context data
    data = request.get("data")
    if isinstance(data, dict):
        sensitive_keys = {"password", "secret", "api_key", "token", "pass_env", "key_env", "secret_env"}
        for k in data:
            if any(s in k.lower() for s in sensitive_keys):
                data[k] = "[SCRUBBED]"
                
    # Filter out local connection warnings / benign reconnect issues to prevent noise
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type, exc_value, _ = exc_info
        if exc_type.__name__ in ("ConnectionResetError", "asyncio.TimeoutError"):
            # Sample reconnect errors more strictly or drop if desired
            pass
            
    return event

def initialize_sentry(
    dsn: Optional[str] = None,
    environment: str = "production",
    release: str = "1.0.0",
    traces_sample_rate: float = 0.1,    # 10% transaction tracing
    profiles_sample_rate: float = 1.0,  # 100% profile tracing
):
    """
    Initialize Sentry with tracing and profiling capabilities.
    """
    if not SENTRY_AVAILABLE:
        return False
        
    dsn = dsn or os.getenv("SENTRY_DSN")
    if not dsn:
        logger.warning("Sentry DSN not provided in environment. Sentry integration is inactive.")
        return False
        
    try:
        sentry_logging = LoggingIntegration(
            level=logging.INFO,        # Capture info and above as breadcrumbs
            event_level=logging.ERROR  # Send errors as events
        )
        
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            profiles_sample_rate=profiles_sample_rate,
            before_send=before_send,
            integrations=[
                FastApiIntegration(),
                sentry_logging,
            ],
            # Do not send personally identifiable information
            send_default_pii=False,
        )
        logger.info(f"Sentry initialized successfully | Environment: {environment} | Traces Sample Rate: {traces_sample_rate} | Profiling Sample Rate: {profiles_sample_rate}")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")
        return False
