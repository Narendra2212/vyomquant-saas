"""
core/pipeline_guard.py — Global Fail-Fast Pipeline Control

Validates data at every pipeline stage to prevent silent degradation.
Ensures every failure is explicit and debugging becomes trivial.

🚨 CRITICAL SAFETY COMPONENT — DO NOT MODIFY WITHOUT APPROVAL
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("PipelineGuard")


class PipelineError(Exception):
    """
    Raised when pipeline data validation fails.
    
    This is a HARD FAILURE - the pipeline cannot continue with invalid data.
    The caller must fix the data issue before retrying.
    """
    pass


class PipelineStage:
    """Pipeline stage identifiers."""
    MARKET_DATA_LOAD = "market_data_load"
    INDICATOR_COMPUTATION = "indicator_computation"
    FEATURE_ENGINEERING = "feature_engineering"
    ML_PREDICTION = "ml_prediction"
    SIGNAL_GENERATION = "signal_generation"
    EXECUTION = "execution"


@dataclass
class PipelineCheckpoint:
    """Checkpoint for pipeline state tracking."""
    stage: str
    timestamp: datetime
    data_shape: Optional[tuple] = None
    data_quality: Optional[Dict[str, Any]] = None
    passed: bool = True
    error_message: Optional[str] = None


class PipelineGuard:
    """
    Global fail-fast pipeline control.
    
    Validates data at every stage to prevent silent degradation.
    All validation failures raise PipelineError immediately.
    """
    
    # Track checkpoints for debugging
    _checkpoints: List[PipelineCheckpoint] = []
    _enabled: bool = True
    
    @classmethod
    def enable(cls) -> None:
        """Enable pipeline guard globally."""
        cls._enabled = True
        logger.info("✅ PipelineGuard enabled")
    
    @classmethod
    def disable(cls) -> None:
        """Disable pipeline guard (DANGEROUS - use only in emergencies)."""
        cls._enabled = False
        logger.critical("🚨 PipelineGuard DISABLED - pipeline may silently fail")
    
    @classmethod
    def is_enabled(cls) -> bool:
        """Check if pipeline guard is enabled."""
        return cls._enabled
    
    @classmethod
    def validate_stage(cls, stage_name: str, data: Any) -> None:
        """
        Validate data at a pipeline stage.
        
        Args:
            stage_name: Name of the pipeline stage
            data: Data to validate
        
        Raises:
            PipelineError: If data is invalid
        """
        if not cls._enabled:
            logger.warning(f"PipelineGuard disabled at {stage_name} - skipping validation")
            return
        
        checkpoint = PipelineCheckpoint(
            stage=stage_name,
            timestamp=datetime.now()
        )
        
        try:
            # Rule 1: Data must not be None
            if data is None:
                error_msg = f"{stage_name}: None data received"
                logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
                checkpoint.passed = False
                checkpoint.error_message = error_msg
                cls._checkpoints.append(checkpoint)
                raise PipelineError(error_msg)
            
            # Rule 2: DataFrame-specific validation
            if isinstance(data, pd.DataFrame):
                cls._validate_dataframe(stage_name, data, checkpoint)
            
            # Rule 3: Series-specific validation
            elif isinstance(data, pd.Series):
                cls._validate_series(stage_name, data, checkpoint)
            
            # Rule 4: Array-like validation
            elif isinstance(data, np.ndarray):
                cls._validate_array(stage_name, data, checkpoint)
            
            # Record successful checkpoint
            checkpoint.passed = True
            cls._checkpoints.append(checkpoint)
            
            logger.debug(f"✅ Pipeline stage validated: {stage_name}")
            
        except PipelineError:
            raise
        except Exception as e:
            error_msg = f"{stage_name}: Unexpected validation error: {e}"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            checkpoint.passed = False
            checkpoint.error_message = error_msg
            cls._checkpoints.append(checkpoint)
            raise PipelineError(error_msg) from e
    
    @classmethod
    def _validate_dataframe(
        cls,
        stage_name: str,
        df: pd.DataFrame,
        checkpoint: PipelineCheckpoint
    ) -> None:
        """Validate DataFrame-specific requirements."""
        checkpoint.data_shape = df.shape
        
        # Check empty
        if df.empty:
            error_msg = f"{stage_name}: Empty DataFrame"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check all NaN
        if df.isna().all().all():
            error_msg = f"{stage_name}: All values are NaN"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check excessive NaN (>50%)
        nan_pct = df.isna().sum().sum() / (df.shape[0] * df.shape[1]) * 100
        if nan_pct > 50:
            error_msg = f"{stage_name}: Excessive NaN values ({nan_pct:.1f}%)"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check duplicate index
        if isinstance(df.index, pd.DatetimeIndex) and df.index.duplicated().any():
            dup_count = df.index.duplicated().sum()
            error_msg = f"{stage_name}: Duplicate timestamps detected ({dup_count})"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Record quality metrics
        checkpoint.data_quality = {
            "rows": len(df),
            "columns": len(df.columns),
            "nan_pct": nan_pct,
            "memory_mb": df.memory_usage(deep=True).sum() / 1024 / 1024
        }
    
    @classmethod
    def _validate_series(
        cls,
        stage_name: str,
        series: pd.Series,
        checkpoint: PipelineCheckpoint
    ) -> None:
        """Validate Series-specific requirements."""
        checkpoint.data_shape = (len(series),)
        
        # Check empty
        if len(series) == 0:
            error_msg = f"{stage_name}: Empty Series"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check all NaN
        if series.isna().all():
            error_msg = f"{stage_name}: All values are NaN"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check excessive NaN (>50%)
        nan_pct = series.isna().sum() / len(series) * 100
        if nan_pct > 50:
            error_msg = f"{stage_name}: Excessive NaN values ({nan_pct:.1f}%)"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Record quality metrics
        checkpoint.data_quality = {
            "length": len(series),
            "nan_pct": nan_pct,
            "dtype": str(series.dtype)
        }
    
    @classmethod
    def _validate_array(
        cls,
        stage_name: str,
        arr: np.ndarray,
        checkpoint: PipelineCheckpoint
    ) -> None:
        """Validate numpy array requirements."""
        checkpoint.data_shape = arr.shape
        
        # Check empty
        if arr.size == 0:
            error_msg = f"{stage_name}: Empty array"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check all NaN
        if np.isnan(arr).all():
            error_msg = f"{stage_name}: All values are NaN"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
        
        # Check all infinite
        if np.isinf(arr).all():
            error_msg = f"{stage_name}: All values are infinite"
            logger.critical(f"🚫 PIPELINE ERROR: {error_msg}")
            raise PipelineError(error_msg)
    
    @classmethod
    def get_checkpoints(cls) -> List[PipelineCheckpoint]:
        """Get all recorded checkpoints for debugging."""
        return cls._checkpoints.copy()
    
    @classmethod
    def clear_checkpoints(cls) -> None:
        """Clear checkpoint history."""
        cls._checkpoints.clear()
    
    @classmethod
    def get_last_failure(cls) -> Optional[PipelineCheckpoint]:
        """Get the last failed checkpoint."""
        for cp in reversed(cls._checkpoints):
            if not cp.passed:
                return cp
        return None
    
    @classmethod
    def generate_report(cls) -> Dict[str, Any]:
        """Generate pipeline validation report."""
        total = len(cls._checkpoints)
        passed = sum(1 for cp in cls._checkpoints if cp.passed)
        failed = total - passed
        
        return {
            "total_checkpoints": total,
            "passed": passed,
            "failed": failed,
            "success_rate": passed / total if total > 0 else 0,
            "last_failure": cls.get_last_failure(),
            "all_checkpoints": cls._checkpoints
        }


# Convenience functions for common validation points
def guard_market_data(data: Any) -> None:
    """Validate market data after load."""
    PipelineGuard.validate_stage(PipelineStage.MARKET_DATA_LOAD, data)


def guard_indicators(data: Any) -> None:
    """Validate data after indicator computation."""
    PipelineGuard.validate_stage(PipelineStage.INDICATOR_COMPUTATION, data)


def guard_features(data: Any) -> None:
    """Validate data after feature engineering."""
    PipelineGuard.validate_stage(PipelineStage.FEATURE_ENGINEERING, data)


def guard_ml_prediction(data: Any) -> None:
    """Validate before ML prediction."""
    PipelineGuard.validate_stage(PipelineStage.ML_PREDICTION, data)


def guard_signal_generation(data: Any) -> None:
    """Validate before signal generation."""
    PipelineGuard.validate_stage(PipelineStage.SIGNAL_GENERATION, data)


def guard_execution(data: Any) -> None:
    """Validate before execution."""
    PipelineGuard.validate_stage(PipelineStage.EXECUTION, data)


# Decorator for automatic validation
def validate_pipeline_stage(stage_name: str):
    """
    Decorator to validate function output.
    
    Example:
        @validate_pipeline_stage("indicator_computation")
        def compute_indicators(data):
            return compute(data)
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            PipelineGuard.validate_stage(stage_name, result)
            return result
        return wrapper
    return decorator
