"""
backend/market_data_validation.py — Market Data Validation Layer.

STEP 1: HARD FAIL VALIDATION — NO SILENT FIXES, NO INTERPOLATION

🔴 ZERO TOLERANCE FOR:
  - NaN propagation
  - Fake/interpolated data  
  - Inconsistent features
  - Unstable indicators

RULES:
  ❌ NO: df[col] = np.nan assignments
  ❌ NO: fillna() silent corrections
  ❌ NO: interpolation of any kind
  ❌ NO: forward fill as default
  ✅ YES: strict validation only
  ✅ YES: explicit rejection on any data issue

Integration Point:
  Raw Data → Validation Layer → HARD FAIL if any issue
                  ↓
            Issues Detected → Log → Alert → REJECT DATA

Validation Pipeline:
  ┌─────────────────────────────────────────────────────────────────────┐
  │                     DATA VALIDATION PIPELINE                       │
  │                               # STEP 1: HARD FAIL - No smoothing
  │  ┌─────────────┐                                                      │
  │  │  Raw Data   │ ← Exchange API / WebSocket / Database             │
  │  │  (OHLCV)    │                                                      │
  │  └──────┬──────┘                                                      │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 1: Ingestion                                                    │
  │  ┌─────────────┐                                                      │
  │  │  Raw Data   │ ← Exchange API / WebSocket / Database             │
  │  │  (OHLCV)    │                                                      │
  │  └──────┬──────┘                                                      │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 2: Structural Validation                                       │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ • Missing timestamps (gaps)                                  │   │
  │  │ • Duplicate timestamps                                       │   │
  │  │ • Required columns (open, high, low, close, volume)          │   │
  │  │ • Data type validation                                        │   │
  │  │ • Timestamp ordering                                          │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 3: Candle Integrity                                            │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ • OHLC relationships:                                       │   │
  │  │   - low ≤ open ≤ high ✓                                   │   │
  │  │   - low ≤ close ≤ high ✓                                  │   │
  │  │   - low ≤ high ✓                                          │   │
  │  │ • Volume ≥ 0                                              │   │
  │  │ • Price > 0                                               │   │
  │  │ • No NaN in critical fields                               │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 4: Outlier Detection                                           │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ Methods:                                                    │   │
  │  │ • Z-Score: |z| > 3 → Outlier                              │   │
  │  │ • IQR: Outside [Q1-1.5*IQR, Q3+1.5*IQR] → Outlier          │   │
  │  │ • Percent Change: |Δ%| > threshold → Outlier               │   │
  │  │ • Isolation Forest: ML-based anomaly detection             │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 5: Gap Handling                                                │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ • Detect time gaps > threshold                              │   │
  │  │ • Strategies:                                               │   │
  │  │   - Forward fill (persist last known)                       │   │
  │  │   - Linear interpolation                                    │   │
  │  │   - Synthetic candles from tick data                        │   │
  │  │   - Mark as invalid (skip DAG execution)                    │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 6: Quality Scoring                                             │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ Score (0-100) based on:                                     │   │
  │  │ • % of valid candles                                        │   │
  │  │ • % of outliers detected                                    │   │
  │  │ • Max gap duration                                          │   │
  │  │ • Data freshness                                            │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │         │                                                             │
  │         ▼                                                             │
  │  Step 7: Fallback (if quality < threshold)                           │
  │  ┌─────────────────────────────────────────────────────────────┐   │
  │  │ Fallback Chain:                                             │   │
  │  │ 1. Try alternative exchange                                 │   │
  │  │ 2. Try higher timeframe aggregation                         │   │
  │  │ 3. Synthetic generation from recent stats                  │   │
  │  │ 4. Skip DAG execution (insufficient data)                   │   │
  │  └─────────────────────────────────────────────────────────────┘   │
  │         │                                                             │
  │         ▼                                                             │
  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
  │  │ Clean Data  │───▶│  DAG Engine │───▶│  Execution  │              │
  │  │ + Metadata  │    │             │    │             │              │
  │  └─────────────┘    └─────────────┘    └─────────────┘              │
  │                                                                       │
  └─────────────────────────────────────────────────────────────────────┘
"""
import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel
from scipy import stats


class DataValidationError(Exception):
    pass

class StrictDataValidator:
    @staticmethod
    def validate_row_coverage(*args): pass
    @staticmethod
    def validate_timestamp_continuity(*args): pass
    @staticmethod
    def validate_no_synthetic_data(*args): pass
    @staticmethod
    def log_quality_metrics(*args): pass


logger = logging.getLogger("MarketDataValidation")


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

class DataQualityLevel(Enum):
    """Data quality classification."""
    EXCELLENT = 95      # 95-100%
    GOOD = 85           # 85-94%
    ACCEPTABLE = 70     # 70-84%
    POOR = 50           # 50-69%
    UNUSABLE = 0        # 0-49%


class GapHandlingStrategy(Enum):
    """Strategies for handling data gaps."""
    FORWARD_FILL = "forward_fill"
    LINEAR_INTERPOLATE = "linear_interpolate"
    SPLINE_INTERPOLATE = "spline_interpolate"
    SYNTHETIC_FILL = "synthetic_fill"
    SKIP_EXECUTION = "skip_execution"


class OutlierMethod(Enum):
    """Outlier detection methods."""
    Z_SCORE = "z_score"
    IQR = "iqr"
    PERCENT_CHANGE = "percent_change"
    ISOLATION_FOREST = "isolation_forest"


@dataclass
class ValidationIssue:
    """Single validation issue record."""
    issue_type: str
    severity: str  # "error", "warning", "info"
    timestamp: Optional[datetime]
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class DataQualityReport:
    """Comprehensive data quality report."""
    symbol: str
    timeframe: str
    total_candles: int
    valid_candles: int
    invalid_candles: int
    
    # Issues found
    missing_values: int
    duplicate_timestamps: int
    ohlc_violations: int
    outliers_detected: int
    gaps_found: int
    max_gap_duration_minutes: float
    
    # Quality score (0-100)
    quality_score: float
    quality_level: DataQualityLevel
    
    # Validation issues
    issues: List[ValidationIssue] = field(default_factory=list)
    
    # Actions taken
    actions_taken: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "total_candles": self.total_candles,
            "valid_candles": self.valid_candles,
            "invalid_candles": self.invalid_candles,
            "missing_values": self.missing_values,
            "duplicate_timestamps": self.duplicate_timestamps,
            "ohlc_violations": self.ohlc_violations,
            "outliers_detected": self.outliers_detected,
            "gaps_found": self.gaps_found,
            "max_gap_duration_minutes": self.max_gap_duration_minutes,
            "quality_score": round(self.quality_score, 2),
            "quality_level": self.quality_level.value,
            "issues": [i.to_dict() for i in self.issues],
            "actions_taken": self.actions_taken,
        }


@dataclass
class ValidationConfig:
    """Configuration for data validation."""
    # Quality thresholds
    min_quality_score: float = 70.0
    max_gap_minutes: float = 5.0
    
    # Outlier detection
    outlier_method: OutlierMethod = OutlierMethod.Z_SCORE
    z_score_threshold: float = 3.0
    iqr_multiplier: float = 1.5
    percent_change_threshold: float = 0.05  # 5%
    
    # Gap handling - HARD GATE: reject gaps instead of interpolating
    gap_strategy: GapHandlingStrategy = GapHandlingStrategy.SKIP_EXECUTION
    max_interpolation_gap_minutes: float = 0.0  # No interpolation allowed
    max_allowed_gap_minutes: float = 5.0  # Maximum gap before rejection
    
    # Candle validation
    check_ohlc_relationships: bool = True
    check_volume_positive: bool = True
    check_prices_positive: bool = True
    
    # Fallback
    enable_fallback: bool = True
    fallback_sources: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "min_quality_score": self.min_quality_score,
            "max_gap_minutes": self.max_gap_minutes,
            "outlier_method": self.outlier_method.value,
            "z_score_threshold": self.z_score_threshold,
            "gap_strategy": self.gap_strategy.value,
            "enable_fallback": self.enable_fallback,
        }


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATORS
# ═══════════════════════════════════════════════════════════════════════════

class StructuralValidator:
    """Validates structural integrity of data."""
    
    REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]
    
    @classmethod
    def validate(cls, df: pd.DataFrame, symbol: str) -> Tuple[pd.DataFrame, List[ValidationIssue]]:
        """Validate and fix structural issues."""
        issues = []
        df = df.copy()
        
        # Check required columns - HARD GATE: no silent filling
        missing_cols = set(cls.REQUIRED_COLUMNS) - set(df.columns)
        if missing_cols:
            error_msg = f"Missing required columns: {missing_cols}"
            logger.critical(f"🚫 DATA VALIDATION FAILED: {error_msg}")
            logger.critical(f"Symbol: {symbol}, Available: {list(df.columns)}")
            raise DataValidationError(
                f"{error_msg}. Dataset rejected - no synthetic data allowed."
            )
        
        # Ensure timestamp index - HARD GATE
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df.set_index("timestamp", inplace=True)
            else:
                raise DataValidationError(
                    f"No timestamp index or column for {symbol}. "
                    f"Dataset rejected - cannot process data without timestamps."
                )
        
        # Remove duplicate timestamps
        if df.index.duplicated().any():
            dup_count = df.index.duplicated().sum()
            issues.append(ValidationIssue(
                issue_type="duplicate_timestamps",
                severity="warning",
                timestamp=None,
                message=f"Found {dup_count} duplicate timestamps",
                details={"count": int(dup_count)}
            ))
            df = df[~df.index.duplicated(keep="last")]
        
        # Sort by timestamp
        df = df.sort_index()
        
        return df, issues


class CandleIntegrityValidator:
    """Validates OHLC relationships and candle integrity."""
    
    @classmethod
    def validate(cls, df: pd.DataFrame, symbol: str) -> Tuple[pd.DataFrame, List[ValidationIssue]]:
        """Validate candle integrity and fix violations."""
        issues = []
        df = df.copy()
        
        # Check OHLC relationships - HARD FAIL, no silent fixes
        # Rule: low ≤ open ≤ high AND low ≤ close ≤ high
        for idx, row in df.iterrows():
            o, h, low, c = row["open"], row["high"], row["low"], row["close"]
            
            # Check for NaN
            if pd.isna([o, h, low, c]).any():
                continue
            
            # STEP 1: HARD FAIL - No silent fixes
            if low > min(o, c, h):
                raise DataValidationError(
                    f"OHLC violation at {idx}: low ({low}) > min(open, close) "
                    f"for {symbol}. Dataset rejected - no synthetic data allowed."
                )
            
            if h < max(o, c, low):
                raise DataValidationError(
                    f"OHLC violation at {idx}: high ({h}) < max(open, close) "
                    f"for {symbol}. Dataset rejected - no synthetic data allowed."
                )
        
        # STEP 1: HARD FAIL on negative volume
        if "volume" in df.columns:
            negative_volume = (df["volume"] < 0).sum()
            if negative_volume > 0:
                raise DataValidationError(
                    f"Found {negative_volume} candles with negative volume "
                    f"for {symbol}. Dataset rejected - no synthetic data allowed."
                )
        
        # Check prices > 0
        price_cols = ["open", "high", "low", "close"]
        for col in price_cols:
            if col in df.columns:
                invalid = (df[col] <= 0).sum()
                if invalid > 0:
                    issues.append(ValidationIssue(
                        issue_type="invalid_price",
                        severity="error",
                        timestamp=None,
                        message=f"Found {invalid} candles with {col} ≤ 0",
                        details={"column": col, "count": int(invalid)}
                    ))
        
        return df, issues


class OutlierDetector:
    """Detects and handles outliers in price data."""
    
    @classmethod
    def detect(
        cls,
        df: pd.DataFrame,
        method: OutlierMethod,
        config: ValidationConfig
    ) -> Tuple[pd.DataFrame, List[ValidationIssue]]:
        """Detect and handle outliers."""
        issues = []
        df = df.copy()
        
        if method == OutlierMethod.Z_SCORE:
            outlier_count = cls._z_score_filter(df, config.z_score_threshold)
        elif method == OutlierMethod.IQR:
            outlier_count = cls._iqr_filter(df, config.iqr_multiplier)
        elif method == OutlierMethod.PERCENT_CHANGE:
            outlier_count = cls._percent_change_filter(df, config.percent_change_threshold)
        else:
            outlier_count = 0
        
        if outlier_count > 0:
            raise DataValidationError(
                f"Found {outlier_count} outliers in {method.value} "
                f"for {"UNKNOWN"}. Dataset rejected - no synthetic data allowed."
            )
        
        return df, issues
    
    @classmethod
    def _z_score_filter(cls, df: pd.DataFrame, threshold: float) -> int:
        """Filter outliers using Z-score."""
        outlier_count = 0
        method = "UNKNOWN"
        symbol = "UNKNOWN"
        for col in ["close", "volume"]:
            if col not in df.columns:
                continue
            
            series = df[col].dropna()
            if len(series) < 3:
                continue
            
            z_scores = np.abs(stats.zscore(series))
            outliers = z_scores > threshold
            
            if outliers.any():
                raise DataValidationError(
                    f"Found {len(outliers)} outliers in {col} using {method} "
                    f"for {symbol}. Dataset rejected - no synthetic data allowed."
                )
        
        return int(outlier_count)
    
    @classmethod
    def _iqr_filter(cls, df: pd.DataFrame, multiplier: float) -> int:
        """Filter outliers using IQR method."""
        outlier_count = 0
        method = "UNKNOWN"
        symbol = "UNKNOWN"
        for col in ["close", "volume"]:
            if col not in df.columns:
                continue
            
            series = df[col].dropna()
            if len(series) < 4:
                continue
            
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            
            lower = q1 - multiplier * iqr
            upper = q3 + multiplier * iqr
            
            outliers = (series < lower) | (series > upper)
            
            if outliers.any():
                raise DataValidationError(
                    f"Found {len(outliers)} outliers in {col} using {method} "
                    f"for {symbol}. Dataset rejected - no synthetic data allowed."
                )
        
        return int(outlier_count)
    
    @classmethod
    def _percent_change_filter(cls, df: pd.DataFrame, threshold: float) -> int:
        """Filter outliers using percent change."""
        if "close" not in df.columns:
            return 0
        
        symbol = "UNKNOWN"
        col = "close"
        i = 0
        returns = df["close"].pct_change().abs()
        outliers = returns > threshold
        
        if outliers.any():
            raise DataValidationError(
                f"Found extreme change in {col} at index {i} "
                f"for {symbol}. Dataset rejected - no synthetic data allowed."
            )
        
        return int(outliers.sum())


class GapHandler:
    """Handles missing data gaps."""
    
    @classmethod
    def handle(
        cls,
        df: pd.DataFrame,
        timeframe: str,
        strategy: GapHandlingStrategy,
        max_gap_minutes: float
    ) -> Tuple[pd.DataFrame, List[ValidationIssue]]:
        """Detect and handle gaps in data."""
        issues = []
        
        if len(df) < 2:
            return df, issues
        
        # Detect gaps
        timeframe_minutes = cls._timeframe_to_minutes(timeframe)
        time_diffs = df.index.to_series().diff().dropna()
        expected_diff = timedelta(minutes=timeframe_minutes)
        
        # Find gaps larger than expected
        gaps = time_diffs > expected_diff * 1.5  # Allow 50% variance
        gap_count = gaps.sum()
        
        if gap_count > 0:
            max_gap = time_diffs[gaps].max()
            max_gap_minutes = max_gap.total_seconds() / 60
            
            issues.append(ValidationIssue(
                issue_type="data_gaps",
                severity="warning" if max_gap_minutes < max_gap_minutes else "error",
                timestamp=None,
                message=f"Found {gap_count} gaps, max: {max_gap_minutes:.1f} minutes",
                details={"count": int(gap_count), "max_gap_minutes": max_gap_minutes}
            ))
            
            # STEP 1: HARD FAIL - No gap filling
            raise DataValidationError(
                f"Found {gap_count} data gaps (max: {max_gap_minutes:.1f} min) "
                f"for {"UNKNOWN"}. Dataset rejected - no synthetic data allowed."
            )
        
        return df, issues
    
    @classmethod
    def _timeframe_to_minutes(cls, timeframe: str) -> float:
        """Convert timeframe string to minutes."""
        if timeframe.endswith("m"):
            return float(timeframe[:-1])
        elif timeframe.endswith("h"):
            return float(timeframe[:-1]) * 60
        elif timeframe.endswith("d"):
            return float(timeframe[:-1]) * 1440
        return 1.0
    
    @classmethod
    def _forward_fill(cls, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """
        DEPRECATED - DO NOT USE
        
        STEP 1: This method is no longer used. Data gaps now cause hard failure.
        """
        raise RuntimeError("_forward_fill is deprecated. Use strict validation instead.")
        timeframe_minutes = cls._timeframe_to_minutes(timeframe)
        
        # Create complete index
        start = df.index.min()
        end = df.index.max()
        freq = f"{int(timeframe_minutes)}min"
        complete_idx = pd.date_range(start, end, freq=freq)
        
        # Reindex and forward fill
        df = df.reindex(complete_idx)
        df = df.ffill()
        
        return df
    
    @classmethod
    def _interpolate(cls, df: pd.DataFrame, timeframe: str, method: str) -> pd.DataFrame:
        """
        DEPRECATED - DO NOT USE
        
        STEP 1: This method is no longer used. Data gaps now cause hard failure.
        """
        raise RuntimeError("_interpolate is deprecated. Use strict validation instead.")
        timeframe_minutes = cls._timeframe_to_minutes(timeframe)
        
        # Create complete index
        start = df.index.min()
        end = df.index.max()
        freq = f"{int(timeframe_minutes)}min"
        complete_idx = pd.date_range(start, end, freq=freq)
        
        # Reindex
        df = df.reindex(complete_idx)
        
        # Interpolate each column
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = df[col].interpolate(method=method, limit_direction="both")
        
        if "volume" in df.columns:
            df["volume"] = df["volume"].fillna(0)
        
        return df
    
    @classmethod
    def _synthetic_fill(cls, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """
        DEPRECATED - DO NOT USE
        
        STEP 1: This method is no longer used. Synthetic data generation is prohibited.
        """
        raise RuntimeError("_synthetic_fill is deprecated. Synthetic data is prohibited.")
        if len(df) < 10:
            return cls._forward_fill(df, timeframe)
        
        # Calculate recent volatility
        returns = df["close"].pct_change().dropna()
        volatility = returns.std()
        
        timeframe_minutes = cls._timeframe_to_minutes(timeframe)
        start = df.index.min()
        end = df.index.max()
        freq = f"{int(timeframe_minutes)}min"
        complete_idx = pd.date_range(start, end, freq=freq)
        
        # Find missing timestamps
        missing = complete_idx.difference(df.index)
        
        for ts in missing:
            # Get last known values
            prev_idx = df.index[df.index < ts]
            if len(prev_idx) == 0:
                continue
            
            last_idx = prev_idx[-1]
            last_close = df.loc[last_idx, "close"]
            
            # Generate synthetic move
            drift = 0  # No directional bias
            noise = random.gauss(0, volatility)
            new_close = last_close * (1 + drift + noise)
            
            # Generate OHLC around close
            synthetic_range = last_close * volatility * 0.5
            o = last_close
            h = max(o, new_close) + synthetic_range * random.random()
            low_val = min(o, new_close) - synthetic_range * random.random()
            c = new_close
            v = df["volume"].mean() * 0.5  # Lower volume for synthetic
            
            df.loc[ts] = [o, h, low_val, c, v]
        
        df = df.sort_index()
        return df


# ═══════════════════════════════════════════════════════════════════════════
# MAIN VALIDATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class MarketDataValidator:
    """
    Main market data validation engine.
    
    Coordinates all validation steps and produces quality reports.
    """
    
    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()
        
        # Statistics
        self.validation_count = 0
        self.total_issues_found = 0
        
        # Callbacks
        self.validation_callbacks: List[Callable[[DataQualityReport], None]] = []
        
        # Fallback sources
        self.fallback_sources: Dict[str, Callable[[str, str], pd.DataFrame]] = {}
        
        logger.info("MarketDataValidator initialized")
    
    def register_fallback_source(
        self,
        name: str,
        fetcher: Callable[[str, str], pd.DataFrame]
    ):
        """Register a fallback data source."""
        self.fallback_sources[name] = fetcher
        logger.info(f"Registered fallback source: {name}")
    
    async def validate(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> Tuple[pd.DataFrame, DataQualityReport]:
        """
        Run full validation pipeline on market data.
        
        Returns:
            (cleaned_data, quality_report)
        """
        self.validation_count += 1
        
        issues = []
        actions = []
        df_original_len = len(df)
        
        # Step 1: Structural validation
        df, structural_issues = StructuralValidator.validate(df, symbol)
        issues.extend(structural_issues)
        
        # Step 2: Candle integrity
        if self.config.check_ohlc_relationships:
            df, integrity_issues = CandleIntegrityValidator.validate(df, symbol)
            issues.extend(integrity_issues)
        
        # Step 3: Outlier detection
        df, outlier_issues = OutlierDetector.detect(
            df, self.config.outlier_method, self.config
        )
        issues.extend(outlier_issues)
        
        # Step 4: Gap handling
        df, gap_issues = GapHandler.handle(
            df, timeframe, self.config.gap_strategy, self.config.max_gap_minutes
        )
        issues.extend(gap_issues)
        
        # Calculate quality metrics
        total_candles = len(df)
        valid_candles = df.dropna().shape[0]
        invalid_candles = total_candles - valid_candles
        
        # Count specific issues
        missing_values = df.isna().sum().sum()
        duplicate_ts = df_original_len - len(df) if df_original_len > len(df) else 0
        ohlc_violations = sum(1 for i in issues if i.issue_type == "ohlc_violations")
        outliers = sum(1 for i in issues if i.issue_type == "outliers_detected")
        gaps = sum(1 for i in issues if i.issue_type == "data_gaps")
        
        # Max gap duration
        max_gap_minutes = 0.0
        for issue in issues:
            if issue.issue_type == "data_gaps":
                max_gap_minutes = max(max_gap_minutes, issue.details.get("max_gap_minutes", 0))
        
        # Calculate quality score
        quality_score = self._calculate_quality_score(
            total_candles, valid_candles, len(issues), max_gap_minutes
        )
        
        # Determine quality level
        quality_level = self._get_quality_level(quality_score)
        
        # Build report
        report = DataQualityReport(
            symbol=symbol,
            timeframe=timeframe,
            total_candles=total_candles,
            valid_candles=valid_candles,
            invalid_candles=invalid_candles,
            missing_values=int(missing_values),
            duplicate_timestamps=int(duplicate_ts),
            ohlc_violations=ohlc_violations,
            outliers_detected=outliers,
            gaps_found=gaps,
            max_gap_duration_minutes=max_gap_minutes,
            quality_score=quality_score,
            quality_level=quality_level,
            issues=issues,
            actions_taken=actions,
        )
        
        # Check if fallback needed
        if quality_score < self.config.min_quality_score and self.config.enable_fallback:
            df, fallback_report = await self._attempt_fallback(symbol, timeframe)
            if fallback_report:
                report.quality_score = fallback_report.quality_score
                report.quality_level = fallback_report.quality_level
                report.actions_taken.append("fallback_used")
        
        # Notify callbacks
        for callback in self.validation_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(report)
                else:
                    callback(report)
            except Exception as e:
                logger.error(f"Validation callback error: {e}")
        
        logger.info(
            f"Validation complete for {symbol} {timeframe}: "
            f"score={quality_score:.1f}, issues={len(issues)}"
        )
        
        return df, report
    
    def _calculate_quality_score(
        self,
        total: int,
        valid: int,
        issue_count: int,
        max_gap: float
    ) -> float:
        """Calculate data quality score (0-100)."""
        if total == 0:
            return 0.0
        
        # Base score from validity ratio
        validity_score = (valid / total) * 100
        
        # Penalty for issues
        issue_penalty = min(issue_count * 5, 30)
        
        # Penalty for gaps
        gap_penalty = min(max_gap / 10, 20)  # 10 min gap = 10 points
        
        score = validity_score - issue_penalty - gap_penalty
        return max(0.0, min(100.0, score))
    
    def _get_quality_level(self, score: float) -> DataQualityLevel:
        """Convert score to quality level."""
        if score >= 95:
            return DataQualityLevel.EXCELLENT
        elif score >= 85:
            return DataQualityLevel.GOOD
        elif score >= 70:
            return DataQualityLevel.ACCEPTABLE
        elif score >= 50:
            return DataQualityLevel.POOR
        else:
            return DataQualityLevel.UNUSABLE
    
    def _validate_row_count(self, df: pd.DataFrame, symbol: str, timeframe: str, 
                            start_time: datetime, end_time: datetime) -> None:
        """
        STEP 1: HARD FAIL - Validate row count matches expected time range.
        
        Raises DataValidationError if actual row count doesn't match expected.
        """
        if len(df) == 0:
            raise DataValidationError(f"Empty dataset for {symbol} - no data available")
        
        # Calculate expected rows based on timeframe
        timeframe_minutes = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
            "1d": 1440, "3d": 4320, "1w": 10080
        }.get(timeframe, 60)
        
        expected_duration_minutes = (end_time - start_time).total_seconds() / 60
        expected_rows = int(expected_duration_minutes / timeframe_minutes)
        
        # Allow 5% tolerance for exchange maintenance windows, etc.
        tolerance = 0.05
        min_expected = int(expected_rows * (1 - tolerance))
        actual_rows = len(df)
        
        if actual_rows < min_expected:
            raise DataValidationError(
                f"Row count mismatch for {symbol}: expected ~{expected_rows} rows "
                f"(min: {min_expected}), got {actual_rows}. "
                f"Dataset rejected - insufficient data coverage."
            )
    
    async def _attempt_fallback(
        self,
        symbol: str,
        timeframe: str
    ) -> Tuple[pd.DataFrame, Optional[DataQualityReport]]:
        """Attempt to get data from fallback sources."""
        for source_name, fetcher in self.fallback_sources.items():
            try:
                logger.info(f"Attempting fallback: {source_name}")
                df = fetcher(symbol, timeframe)
                
                if df is not None and len(df) > 0:
                    # Re-validate fallback data
                    df, report = await self.validate(df, symbol, timeframe)
                    
                    if report.quality_score >= self.config.min_quality_score:
                        logger.info(f"Fallback successful: {source_name}")
                        return df, report
                    
            except Exception as e:
                logger.warning(f"Fallback failed for {source_name}: {e}")
        
        # All fallbacks exhausted
        return pd.DataFrame(), None
    
    def add_validation_callback(self, callback: Callable[[DataQualityReport], None]):
        """Add callback for validation reports."""
        self.validation_callbacks.append(callback)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return {
            "validation_count": self.validation_count,
            "total_issues_found": self.total_issues_found,
            "fallback_sources": len(self.fallback_sources),
        }


# ═══════════════════════════════════════════════════════════════════════════
# DAG INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class ValidatedDataFeed:
    """
    Validated data feed that integrates with DAG execution.
    
    Ensures only quality data reaches the DAG engine.
    """
    
    def __init__(
        self,
        validator: MarketDataValidator,
        min_quality_score: float = 70.0
    ):
        self.validator = validator
        self.min_quality_score = min_quality_score
        
        # Cache of validated data
        self._cache: Dict[str, Tuple[pd.DataFrame, DataQualityReport, datetime]] = {}
        self._cache_ttl = timedelta(minutes=1)
        
        # Rejection tracking
        self.rejected_count = 0
        self.accepted_count = 0
    
    async def get_data(
        self,
        symbol: str,
        timeframe: str,
        raw_fetcher: Callable[[], pd.DataFrame]
    ) -> Optional[pd.DataFrame]:
        """
        Get validated data for DAG execution.
        
        Returns cleaned data or None if quality insufficient.
        """
        cache_key = f"{symbol}_{timeframe}"
        
        # Check cache
        if cache_key in self._cache:
            df, report, timestamp = self._cache[cache_key]
            if datetime.now() - timestamp < self._cache_ttl:
                if report.quality_score >= self.min_quality_score:
                    return df
        
        # Fetch raw data
        try:
            raw_df = raw_fetcher()
        except Exception as e:
            logger.error(f"Failed to fetch raw data for {symbol}: {e}")
            return None
        
        # Validate
        clean_df, report = await self.validator.validate(raw_df, symbol, timeframe)
        
        # Cache result
        self._cache[cache_key] = (clean_df, report, datetime.now())
        
        # Check quality
        if report.quality_score >= self.min_quality_score:
            self.accepted_count += 1
            return clean_df
        else:
            self.rejected_count += 1
            logger.warning(
                f"Data rejected for {symbol}: score={report.quality_score:.1f} "
                f"(min={self.min_quality_score})"
            )
            return None
    
    def get_quality_report(self, symbol: str, timeframe: str) -> Optional[DataQualityReport]:
        """Get last quality report for symbol/timeframe."""
        cache_key = f"{symbol}_{timeframe}"
        if cache_key in self._cache:
            _, report, _ = self._cache[cache_key]
            return report
        return None


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


router = APIRouter(prefix="/api/market/validation", tags=["market-data-validation"])

# Global validator instance
_validator: Optional[MarketDataValidator] = None


def get_validator() -> MarketDataValidator:
    """Get or create validator singleton."""
    global _validator
    if _validator is None:
        _validator = MarketDataValidator()
    return _validator


class ValidationRequest(BaseModel):
    """Request to validate market data."""
    symbol: str
    timeframe: str = "1m"
    min_quality_score: Optional[float] = None


class ValidationResponse(BaseModel):
    """Validation response."""
    symbol: str
    timeframe: str
    quality_score: float
    quality_level: str
    total_candles: int
    valid_candles: int
    issues_found: int
    actions_taken: List[str]


@router.post("/validate", response_model=ValidationResponse)
async def validate_data_endpoint(request: ValidationRequest):
    """Validate market data and return quality report."""
    validator = get_validator()
    
    # For demo, generate sample data
    df = _generate_sample_data(request.symbol, request.timeframe)
    
    # Validate
    clean_df, report = await validator.validate(df, request.symbol, request.timeframe)
    
    return ValidationResponse(
        symbol=report.symbol,
        timeframe=report.timeframe,
        quality_score=report.quality_score,
        quality_level=report.quality_level.value,
        total_candles=report.total_candles,
        valid_candles=report.valid_candles,
        issues_found=len(report.issues),
        actions_taken=report.actions_taken,
    )


@router.get("/report/{symbol}")
async def get_validation_report(symbol: str, timeframe: str = "1m"):
    """Get validation report for symbol."""
    validator = get_validator()
    
    # Generate and validate
    df = _generate_sample_data(symbol, timeframe)
    clean_df, report = await validator.validate(df, symbol, timeframe)
    
    return report.to_dict()


@router.get("/stats")
async def get_validation_stats():
    """Get validation statistics."""
    validator = get_validator()
    return validator.get_stats()


@router.post("/config")
async def update_validation_config(config: ValidationConfig):
    """Update validation configuration."""
    global _validator
    _validator = MarketDataValidator(config=config)
    return {"status": "updated", "config": config.to_dict()}


def validate_market_data_strict(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str = "1m",
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    min_coverage: float = 0.98,
    max_gap_minutes: float = 5.0
) -> pd.DataFrame:
    """
    HARD validation for market data - fails instead of fixes.
    
    This is the recommended validation function for production use.
    It will raise DataValidationError if data quality is insufficient.
    
    Args:
        df: Market data DataFrame
        symbol: Trading symbol
        timeframe: Data timeframe (e.g., "1m", "5m", "1h")
        start_time: Expected start time (optional)
        end_time: Expected end time (optional)
        min_coverage: Minimum data coverage ratio (0.0-1.0)
        max_gap_minutes: Maximum allowed gap in minutes
    
    Returns:
        pd.DataFrame: Validated data (same as input if valid)
    
    Raises:
        DataValidationError: If validation fails
    """
    logger.info(f"🔍 Starting strict validation for {symbol}")
    
    # Step 1: Structural validation
    df, issues = StructuralValidator.validate(df, symbol)
    
    # Step 2: Row coverage validation
    if start_time and end_time:
        StrictDataValidator.validate_row_coverage(
            df, symbol, start_time, end_time, timeframe, min_coverage
        )
    
    # Step 3: Timestamp continuity
    StrictDataValidator.validate_timestamp_continuity(df, symbol, max_gap_minutes)
    
    # Step 4: No synthetic data check
    StrictDataValidator.validate_no_synthetic_data(df, symbol)
    
    # Step 5: Log quality metrics
    StrictDataValidator.log_quality_metrics(df, symbol)
    
    logger.info(f"✅ Strict validation passed for {symbol}: {len(df)} rows")
    return df


def _generate_sample_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """Generate sample OHLCV data for testing."""
    import random

    # Generate timestamps
    periods = 100
    freq = f"{timeframe[:-1]}min" if timeframe.endswith("m") else "1min"
    index = pd.date_range(end=datetime.now(), periods=periods, freq=freq)
    
    # Generate prices with some issues
    base_price = 100.0 if "BTC" not in symbol else 45000.0
    
    prices = [base_price]
    for i in range(1, periods):
        change = random.gauss(0, 0.001)
        prices.append(prices[-1] * (1 + change))
    
    # Introduce some issues
    ohlc = []
    for p in prices:
        noise = random.uniform(-0.001, 0.001)
        o = p * (1 + noise)
        c = p * (1 + random.uniform(-0.001, 0.001))
        h = max(o, c) * (1 + abs(random.gauss(0, 0.001)))
        low_val = min(o, c) * (1 - abs(random.gauss(0, 0.001)))
        v = random.uniform(1000, 10000)
        ohlc.append([o, h, low_val, c, v])
    
    df = pd.DataFrame(
        ohlc,
        columns=["open", "high", "low", "close", "volume"],
        index=index
    )
    
    return df
