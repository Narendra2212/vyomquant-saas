"""Backend engine package.

STEP 5: Portfolio Cache Layer
=============================
Portfolio cache updater ensures low-latency portfolio access:
  exchange → updater → Redis → ExecutionGuard

Not: execution → exchange (high latency)

STEP 6: Data Pipeline Integrity
===============================
Validates data integrity throughout the pipeline:
  Market Data → Indicators → ML → Signals

IMPORTANT: No runtime imports here to avoid circular dependencies.
Import services directly from submodules:
  - from backend.portfolio_cache_updater import PortfolioCacheUpdater
  - from backend.data_pipeline_validator import DataPipelineValidator
"""

# NOTE: Runtime imports removed to prevent circular dependencies with
# core.cache.redis_manager. Import directly from submodules when needed.

__all__ = []
