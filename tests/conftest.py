"""Pytest configuration for test environment setup."""
import os

# Set ENV=testing at module import time BEFORE any backend imports
# This ensures rate limiter uses in-memory storage
os.environ["ENV"] = "testing"

# Clear REDIS_URL to force in-memory rate limiting even if set in environment
os.environ["REDIS_URL"] = ""

# Set DEV_MODE for testing
os.environ["DEV_MODE"] = "true"

# Set JWT secrets for testing
os.environ["JWT_SECRET"] = "dev-secret-change-in-production"
os.environ["SUPABASE_JWT_SECRET"] = "dev-secret-change-in-production"

# Set DEFAULT_EXCHANGE for websocket tests
os.environ["DEFAULT_EXCHANGE"] = "binance"

# Set VYOMQUANT_MODE=safe to override .env file's paper mode and ensure SQLite fallback
os.environ["VYOMQUANT_MODE"] = "safe"

# Clear DATABASE_URL to prevent any .env or .env.example placeholder from being used
os.environ["DATABASE_URL"] = ""

# Must be set BEFORE any backend imports to override .env file
