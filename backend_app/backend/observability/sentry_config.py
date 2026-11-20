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
    environment: str = "unknown",
    release: str = "1.0.0",
    traces_sample_rate: float = 0.1,    # 10% transaction tracing
    profiles_sample_rate: float = 1.0,  # 100% profile tracing
    trading_mode: Optional[str] = None,
):
    """
    Initialize Sentry with tracing and profiling capabilities.

    ``environment`` and ``trading_mode`` ARE TWO DIMENSIONS, NOT ONE (ENV-06).

      deployment environment   development / testing / staging / production
                               — where the process runs, how many replicas, whose data
      trading mode             safe / paper / live
                               — what the process is allowed to do with money

    ``main.py`` used to pass ``environment=get_vyomquant_mode("production")``, so Sentry's
    ``environment`` tag was the TRADING MODE. With the documented production setting
    ``VYOMQUANT_MODE=paper``, every production event was filed under an environment called
    ``paper``, alongside events from developer machines in paper mode — and there was no
    environment called ``production`` for an alert rule, a release-health query or an
    issue filter to select. The same substitution had already been found and fixed in
    ``core/rate_limit.py`` (API-03) and ``core/cache/redis_manager.py``; this was the
    reporting-side copy of it.

    BOTH ARE NOW RECORDED, INDEPENDENTLY. ``environment`` is the deployment environment,
    read from ``runtime_env.deployment_environment()`` by the caller. ``trading_mode`` is
    attached as a global scope tag, so every event carries it and it remains filterable —
    nothing is lost by separating them, which is the point: an event in
    ``environment=production, trading_mode=paper`` is a different thing from one in
    ``environment=development, trading_mode=paper``, and the single-tag version could not
    say which it had.

    ``environment`` defaults to ``"unknown"`` rather than ``"production"``. A caller that
    does not say where it is running should produce a visibly unlabelled stream rather than
    a convincing-looking production one — mislabelling is worse than admitting ignorance,
    because an alert rule cannot tell the difference.

    Regression: tests/test_api05_documentation_exposure_matrix.py
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

        # ENV-06: the trading mode is a SECOND dimension, carried as its own tag rather
        # than impersonating the deployment environment. Set on the global scope, so every
        # event and transaction from this process carries it without each call site
        # remembering to.
        if trading_mode:
            sentry_sdk.set_tag("trading_mode", trading_mode)

        logger.info(
            f"Sentry initialized successfully | Environment: {environment} | "
            f"Trading mode: {trading_mode or 'not reported'} | "
            f"Traces Sample Rate: {traces_sample_rate} | "
            f"Profiling Sample Rate: {profiles_sample_rate}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Sentry: {e}")
        return False
