"""
backend/data_pipeline_validator.py — Data Pipeline Integrity Validator (STEP 6)

═══════════════════════════════════════════════════════════════════════════════
STEP 6: DATA PIPELINE INTEGRITY CHECK
═══════════════════════════════════════════════════════════════════════════════

TRACE: Market Data → Indicators → ML → Signals

VALIDATE:
  - Data shape consistency
  - No NaN anywhere
  - No column mismatch
  - Time alignment correct

ADD:
  assert len(indicator_output) == len(input_data)

EXPECTED RESULT:
  ✅ Consistent signal generation
  ✅ No shape mismatches
  ✅ No silent data corruption
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class DataPipelineError(Exception):
    """Raised when data pipeline integrity check fails."""
    pass


@dataclass
class PipelineCheckpoint:
    """Records pipeline state at a checkpoint."""
    stage: str
    row_count: int
    column_count: int
    has_nan: bool
    timestamp: Optional[pd.Timestamp] = None


class DataPipelineValidator:
    """
    STEP 6: Validates data integrity throughout the pipeline.
    
    Pipeline Flow:
      Market Data → Indicators → ML Features → Signals
    
    Each stage must maintain:
      - Row count consistency (no data loss/gain)
      - No NaN values
      - Column alignment
      - Time index alignment
    """
    
    def __init__(self):
        self.checkpoints: List[PipelineCheckpoint] = []
        self._original_index: Optional[pd.Index] = None
        self._original_length: int = 0
    
    def validate_input_data(
        self,
        data: pd.DataFrame,
        required_columns: Optional[List[str]] = None,
        stage_name: str = "input"
    ) -> pd.DataFrame:
        """
        STEP 6.1: Validate raw market data input.
        
        Raises:
            DataPipelineError: If validation fails
        """
        # Check for empty data
        if len(data) == 0:
            raise DataPipelineError(
                f"[{stage_name}] Empty dataset - no market data available"
            )
        
        # Check for NaN in input
        if data.isna().any().any():
            nan_cols = data.columns[data.isna().any()].tolist()
            raise DataPipelineError(
                f"[{stage_name}] NaN detected in columns: {nan_cols}. "
                f"Input data must be clean before processing."
            )
        
        # Check required columns
        if required_columns:
            missing = set(required_columns) - set(data.columns)
            if missing:
                raise DataPipelineError(
                    f"[{stage_name}] Missing required columns: {missing}"
                )
        
        # Store original state
        self._original_index = data.index.copy()
        self._original_length = len(data)
        
        # Record checkpoint
        self._record_checkpoint(stage_name, data)
        
        logger.info(f"✅ [{stage_name}] Validated: {len(data)} rows, {len(data.columns)} cols")
        return data
    
    def validate_indicator_output(
        self,
        indicators: pd.DataFrame,
        input_data: pd.DataFrame,
        indicator_names: List[str],
        stage_name: str = "indicators"
    ) -> pd.DataFrame:
        """
        STEP 6.2: Validate indicator computation output.
        
        Critical Checks:
          - Row count must match input (no data loss)
          - No NaN in output (NaN handling done in computation)
          - Index alignment preserved
        """
        # CRITICAL: Shape consistency
        if len(indicators) != len(input_data):
            raise DataPipelineError(
                f"[{stage_name}] SHAPE MISMATCH: "
                f"indicator_output has {len(indicators)} rows, "
                f"but input_data has {len(input_data)} rows. "
                f"Data loss detected - pipeline integrity compromised."
            )
        
        # CRITICAL: No NaN in output
        if indicators.isna().any().any():
            nan_cols = indicators.columns[indicators.isna().any()].tolist()
            raise DataPipelineError(
                f"[{stage_name}] NaN detected in indicator output columns: {nan_cols}. "
                f"Indicators must not produce NaN values."
            )
        
        # CRITICAL: Index alignment
        if not indicators.index.equals(input_data.index):
            raise DataPipelineError(
                f"[{stage_name}] TIME ALIGNMENT ERROR: "
                f"indicator index does not match input index. "
                f"Time series alignment compromised."
            )
        
        # Check expected columns exist
        missing = set(indicator_names) - set(indicators.columns)
        if missing:
            raise DataPipelineError(
                f"[{stage_name}] Missing indicator columns: {missing}"
            )
        
        self._record_checkpoint(stage_name, indicators)
        
        logger.info(
            f"✅ [{stage_name}] Validated: {len(indicators)} rows, "
            f"{len(indicators.columns)} indicators"
        )
        return indicators
    
    def validate_ml_features(
        self,
        features: pd.DataFrame,
        indicator_data: pd.DataFrame,
        expected_columns: List[str],
        stage_name: str = "ml_features"
    ) -> pd.DataFrame:
        """
        STEP 6.3: Validate ML feature preparation.
        
        Critical Checks:
          - Feature count matches model expectation
          - No NaN (NaN should have been handled earlier)
          - Column order is strict
        """
        # CRITICAL: Feature dimension
        if len(features.columns) != len(expected_columns):
            raise DataPipelineError(
                f"[{stage_name}] FEATURE DIMENSION MISMATCH: "
                f"expected {len(expected_columns)} features, "
                f"got {len(features.columns)}. "
                f"Model input shape compromised."
            )
        
        # CRITICAL: No NaN
        if features.isna().any().any():
            nan_cols = features.columns[features.isna().any()].tolist()
            raise DataPipelineError(
                f"[{stage_name}] NaN in ML features: {nan_cols}. "
                f"Features must be clean for model inference."
            )
        
        # CRITICAL: Column order
        actual_cols = list(features.columns)
        if actual_cols != expected_columns:
            raise DataPipelineError(
                f"[{stage_name}] COLUMN ORDER MISMATCH: "
                f"expected {expected_columns}, got {actual_cols}. "
                f"Model expects strict column ordering."
            )
        
        # Row count consistency (may be less due to horizon slicing)
        if len(features) > len(indicator_data):
            raise DataPipelineError(
                f"[{stage_name}] FEATURE ROW COUNT ERROR: "
                f"features ({len(features)}) > indicators ({len(indicator_data)}). "
                f"Data leakage or corruption."
            )
        
        self._record_checkpoint(stage_name, features)
        
        logger.info(
            f"✅ [{stage_name}] Validated: {len(features)} rows, "
            f"{len(features.columns)} features"
        )
        return features
    
    def validate_signals(
        self,
        signals: pd.Series,
        input_data: pd.DataFrame,
        stage_name: str = "signals"
    ) -> pd.Series:
        """
        STEP 6.4: Validate generated signals.
        
        Critical Checks:
          - Signal count matches input
          - No NaN signals
          - Valid signal values
        """
        # CRITICAL: Row count match
        if len(signals) != len(input_data):
            raise DataPipelineError(
                f"[{stage_name}] SIGNAL COUNT MISMATCH: "
                f"signals ({len(signals)}) != input ({len(input_data)}). "
                f"Cannot align signals to candles."
            )
        
        # CRITICAL: No NaN signals
        if signals.isna().any():
            nan_count = signals.isna().sum()
            raise DataPipelineError(
                f"[{stage_name}] NaN signals detected: {nan_count} values. "
                f"Signals must be valid trade decisions."
            )
        
        # Check for valid signal values (e.g., -1, 0, 1 for sell/hold/buy)
        unique_vals = signals.unique()
        invalid = set(unique_vals) - {-1, 0, 1, -1.0, 0.0, 1.0, True, False}
        if invalid:
            raise DataPipelineError(
                f"[{stage_name}] Invalid signal values: {invalid}. "
                f"Expected: -1 (sell), 0 (hold), 1 (buy)"
            )
        
        self._record_checkpoint(stage_name, signals.to_frame())
        
        logger.info(f"✅ [{stage_name}] Validated: {len(signals)} signals")
        return signals
    
    def _record_checkpoint(self, stage: str, data: pd.DataFrame):
        """Record pipeline checkpoint."""
        checkpoint = PipelineCheckpoint(
            stage=stage,
            row_count=len(data),
            column_count=len(data.columns) if hasattr(data, 'columns') else 1,
            has_nan=data.isna().any().any() if hasattr(data, 'isna') else False,
            timestamp=data.index[-1] if hasattr(data, 'index') and len(data) > 0 else None
        )
        self.checkpoints.append(checkpoint)
    
    def get_pipeline_report(self) -> str:
        """Generate pipeline integrity report."""
        lines = ["\n═══════════════════════════════════════════════════════════════════"]
        lines.append("STEP 6: DATA PIPELINE INTEGRITY REPORT")
        lines.append("═══════════════════════════════════════════════════════════════════")
        
        for i, cp in enumerate(self.checkpoints):
            lines.append(f"\n{i+1}. [{cp.stage}]")
            lines.append(f"   Rows: {cp.row_count}")
            lines.append(f"   Cols: {cp.column_count}")
            lines.append(f"   NaN:  {'❌ YES' if cp.has_nan else '✅ No'}")
            if cp.timestamp:
                lines.append(f"   Time: {cp.timestamp}")
        
        # Check consistency
        if len(self.checkpoints) >= 2:
            first = self.checkpoints[0]
            last = self.checkpoints[-1]
            if first.row_count != last.row_count:
                lines.append(f"\n⚠️  ROW COUNT DRIFT: {first.row_count} → {last.row_count}")
        
        lines.append("\n═══════════════════════════════════════════════════════════════════")
        return "\n".join(lines)


# Convenience functions for inline validation

def assert_shape_match(
    output_data: pd.DataFrame,
    input_data: pd.DataFrame,
    stage: str = "pipeline"
) -> None:
    """
    STEP 6: Assert that output shape matches input shape.
    
    Raises:
        DataPipelineError: If shape mismatch detected
    """
    if len(output_data) != len(input_data):
        raise DataPipelineError(
            f"[{stage}] SHAPE MISMATCH: "
            f"output has {len(output_data)} rows, "
            f"input has {len(input_data)} rows"
        )


def assert_no_nan(
    data: pd.DataFrame,
    stage: str = "pipeline"
) -> None:
    """
    STEP 6: Assert no NaN values in data.
    
    Raises:
        DataPipelineError: If NaN detected
    """
    if data.isna().any().any():
        nan_cols = data.columns[data.isna().any()].tolist()
        raise DataPipelineError(
            f"[{stage}] NaN detected in columns: {nan_cols}"
        )


def assert_column_match(
    data: pd.DataFrame,
    expected_columns: List[str],
    stage: str = "pipeline"
) -> None:
    """
    STEP 6: Assert columns match expected exactly.
    
    Raises:
        DataPipelineError: If column mismatch
    """
    actual = list(data.columns)
    if actual != expected_columns:
        raise DataPipelineError(
            f"[{stage}] COLUMN MISMATCH: expected {expected_columns}, got {actual}"
        )


def assert_time_aligned(
    data: pd.DataFrame,
    reference_index: pd.Index,
    stage: str = "pipeline"
) -> None:
    """
    STEP 6: Assert time index alignment.
    
    Raises:
        DataPipelineError: If index misaligned
    """
    if not data.index.equals(reference_index):
        raise DataPipelineError(
            f"[{stage}] TIME ALIGNMENT ERROR: index does not match reference"
        )


# Global validator instance
pipeline_validator = DataPipelineValidator()
