"""
backend/services/logging_config.py — Centralized logging configuration.
"""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the specified name."""
    logger = logging.getLogger(name)
    
    # Set default level if not already set
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
    
    # Add console handler if no handlers exist
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s — %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    
    return logger
