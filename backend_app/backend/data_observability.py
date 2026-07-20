"""
backend/data_observability.py — Data Observability System

Real-time visibility into data quality with logging and alerting.
Detects pipeline issues early before they cause bad trades.

🚨 CRITICAL MONITORING COMPONENT — DO NOT MODIFY WITHOUT APPROVAL
"""

import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

logger = logging.getLogger("DataObservability")


@dataclass
class DataQualityMetrics:
    """Data quality metrics for a pipeline stage."""
    stage: str
    timestamp: datetime
    
    # NaN metrics
    nan_ratio: float = 0.0
    nan_count: int = 0
    total_values: int = 0
    
    # Feature distribution
    feature_mean: Optional[float] = None
    feature_std: Optional[float] = None
    feature_min: Optional[float] = None
    feature_max: Optional[float] = None
    
    # Data freshness
    last_timestamp: Optional[datetime] = None
    data_delay_seconds: Optional[float] = None
    
    # Gap metrics
    gap_count: int = 0
    max_gap_seconds: Optional[float] = None
    missing_rows: int = 0
    missing_rows_pct: float = 0.0
    
    # Status
    passed: bool = True
    violations: List[str] = field(default_factory=list)


class DataObservability:
    """
    Data observability system for pipeline monitoring.
    
    Tracks metrics, detects anomalies, and sends alerts.
    """
    
    # Alert thresholds
    NAN_RATIO_THRESHOLD: float = 0.05  # 5%
    MISSING_ROWS_THRESHOLD: float = 0.02  # 2%
    DATA_DELAY_THRESHOLD_SECONDS: float = 300  # 5 minutes
    MAX_GAP_THRESHOLD_SECONDS: float = 300  # 5 minutes
    
    @staticmethod
    def log_stage_metrics(
        stage: str,
        data: Any,
        symbol: Optional[str] = None,
        expected_timestamp: Optional[datetime] = None
    ) -> DataQualityMetrics:
        """
        Log comprehensive metrics for a pipeline stage.
        
        Args:
            stage: Pipeline stage name
            data: Data to analyze (DataFrame, Series, or array)
            symbol: Trading symbol (optional)
            expected_timestamp: Expected current timestamp (optional)
        
        Returns:
            DataQualityMetrics: Collected metrics
        """
        metrics = DataQualityMetrics(
            stage=stage,
            timestamp=datetime.now()
        )
        
        try:
            # Analyze based on data type
            if isinstance(data, pd.DataFrame):
                DataObservability._analyze_dataframe(data, metrics, symbol)
            elif isinstance(data, pd.Series):
                DataObservability._analyze_series(data, metrics, symbol)
            elif isinstance(data, np.ndarray):
                DataObservability._analyze_array(data, metrics)
            
            # Calculate data freshness
            if expected_timestamp and metrics.last_timestamp:
                metrics.data_delay_seconds = (
                    expected_timestamp - metrics.last_timestamp
                ).total_seconds()
            
            # Check alert conditions
            DataObservability._check_alert_conditions(metrics, symbol)
            
            # Log summary
            DataObservability._log_summary(metrics, symbol)
            
        except Exception as e:
            logger.error(f"Error logging metrics for {stage}: {e}")
            metrics.passed = False
            metrics.violations.append(f"metrics_collection_failed: {e}")
        
        return metrics
    
    @staticmethod
    def _analyze_dataframe(
        df: pd.DataFrame,
        metrics: DataQualityMetrics,
        symbol: Optional[str] = None
    ) -> None:
        """Analyze DataFrame metrics."""
        # NaN metrics
        metrics.total_values = df.shape[0] * df.shape[1]
        metrics.nan_count = df.isna().sum().sum()
        metrics.nan_ratio = (
            metrics.nan_count / metrics.total_values 
            if metrics.total_values > 0 else 0
        )
        
        # Feature distribution (for numeric columns)
        numeric_df = df.select_dtypes(include=[np.number])
        if not numeric_df.empty:
            metrics.feature_mean = numeric_df.mean().mean()
            metrics.feature_std = numeric_df.std().mean()
            metrics.feature_min = numeric_df.min().min()
            metrics.feature_max = numeric_df.max().max()
        
        # Data freshness
        if isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
            metrics.last_timestamp = df.index.max()
        
        # Gap analysis
        if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
            gaps = df.index.to_series().diff().dropna()
            gap_seconds = gaps.dt.total_seconds()
            
            metrics.gap_count = (gap_seconds > 60).sum()  # Gaps > 1 minute
            metrics.max_gap_seconds = gap_seconds.max()
    
    @staticmethod
    def _analyze_series(
        series: pd.Series,
        metrics: DataQualityMetrics,
        symbol: Optional[str] = None
    ) -> None:
        """Analyze Series metrics."""
        # NaN metrics
        metrics.total_values = len(series)
        metrics.nan_count = series.isna().sum()
        metrics.nan_ratio = (
            metrics.nan_count / metrics.total_values 
            if metrics.total_values > 0 else 0
        )
        
        # Feature distribution
        if pd.api.types.is_numeric_dtype(series):
            metrics.feature_mean = series.mean()
            metrics.feature_std = series.std()
            metrics.feature_min = series.min()
            metrics.feature_max = series.max()
        
        # Data freshness
        if isinstance(series.index, pd.DatetimeIndex) and len(series) > 0:
            metrics.last_timestamp = series.index.max()
    
    @staticmethod
    def _analyze_array(
        arr: np.ndarray,
        metrics: DataQualityMetrics
    ) -> None:
        """Analyze numpy array metrics."""
        # NaN metrics
        metrics.total_values = arr.size
        metrics.nan_count = np.isnan(arr).sum()
        metrics.nan_ratio = (
            metrics.nan_count / metrics.total_values 
            if metrics.total_values > 0 else 0
        )
        
        # Feature distribution
        if np.issubdtype(arr.dtype, np.number):
            metrics.feature_mean = np.nanmean(arr)
            metrics.feature_std = np.nanstd(arr)
            metrics.feature_min = np.nanmin(arr)
            metrics.feature_max = np.nanmax(arr)
    
    @staticmethod
    def _check_alert_conditions(
        metrics: DataQualityMetrics,
        symbol: Optional[str] = None
    ) -> None:
        """Check if metrics violate alert thresholds."""
        violations = []
        
        # NaN ratio check
        if metrics.nan_ratio > DataObservability.NAN_RATIO_THRESHOLD:
            violations.append(
                f"nan_ratio: {metrics.nan_ratio*100:.1f}% > "
                f"{DataObservability.NAN_RATIO_THRESHOLD*100:.1f}%"
            )
        
        # Missing rows check
        if metrics.missing_rows_pct > DataObservability.MISSING_ROWS_THRESHOLD * 100:
            violations.append(
                f"missing_rows: {metrics.missing_rows_pct:.1f}% > "
                f"{DataObservability.MISSING_ROWS_THRESHOLD*100:.1f}%"
            )
        
        # Data delay check
        if metrics.data_delay_seconds and metrics.data_delay_seconds > DataObservability.DATA_DELAY_THRESHOLD_SECONDS:
            violations.append(
                f"data_delay: {metrics.data_delay_seconds:.0f}s > "
                f"{DataObservability.DATA_DELAY_THRESHOLD_SECONDS:.0f}s"
            )
        
        # Max gap check
        if metrics.max_gap_seconds and metrics.max_gap_seconds > DataObservability.MAX_GAP_THRESHOLD_SECONDS:
            violations.append(
                f"max_gap: {metrics.max_gap_seconds:.0f}s > "
                f"{DataObservability.MAX_GAP_THRESHOLD_SECONDS:.0f}s"
            )
        
        metrics.violations = violations
        metrics.passed = len(violations) == 0
        
        # Send alerts if violations detected
        if violations:
            DataObservability._send_alert(metrics, symbol, violations)
    
    @staticmethod
    def _send_alert(
        metrics: DataQualityMetrics,
        symbol: Optional[str],
        violations: List[str]
    ) -> None:
        """Send alert for data quality violations."""
        try:
            from backend_app.backend.alert_system import AlertSystem
            
            title = f"Data Quality Alert: {metrics.stage}"
            if symbol:
                title += f" ({symbol})"
            
            message = (
                f"Stage: {metrics.stage}\n"
                f"Timestamp: {metrics.timestamp}\n"
                f"Violations: {', '.join(violations)}\n"
                f"NaN Ratio: {metrics.nan_ratio*100:.2f}%\n"
            )
            
            if symbol:
                message += f"Symbol: {symbol}\n"
            
            if metrics.data_delay_seconds:
                message += f"Data Delay: {metrics.data_delay_seconds:.0f}s\n"
            
            AlertSystem.send_alert(
                level="WARNING" if metrics.nan_ratio < 0.1 else "CRITICAL",
                title=title,
                message=message,
                channels=["telegram", "email"]
            )
            
            logger.warning(f"🚨 Data quality alert sent for {metrics.stage}: {violations}")
            
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")
    
    @staticmethod
    def _log_summary(
        metrics: DataQualityMetrics,
        symbol: Optional[str] = None
    ) -> None:
        """Log metrics summary."""
        symbol_str = f" [{symbol}]" if symbol else ""
        
        if metrics.passed:
            logger.info(
                f"📊 {metrics.stage}{symbol_str}: "
                f"nan={metrics.nan_ratio*100:.2f}%, "
                f"mean={metrics.feature_mean:.4f if metrics.feature_mean else 'N/A'}, "
                f"gaps={metrics.gap_count}"
            )
        else:
            logger.warning(
                f"🚨 {metrics.stage}{symbol_str}: "
                f"VIOLATIONS: {', '.join(metrics.violations)}"
            )


# Convenience functions for common observability points
def observe_market_data(
    data: pd.DataFrame,
    symbol: str,
    expected_timestamp: Optional[datetime] = None
) -> DataQualityMetrics:
    """Observe market data quality."""
    return DataObservability.log_stage_metrics(
        "market_data",
        data,
        symbol,
        expected_timestamp
    )


def observe_indicators(
    data: pd.Series,
    symbol: str
) -> DataQualityMetrics:
    """Observe indicator computation quality."""
    return DataObservability.log_stage_metrics(
        "indicators",
        data,
        symbol
    )


def observe_features(
    data: pd.DataFrame,
    symbol: str
) -> DataQualityMetrics:
    """Observe feature engineering quality."""
    return DataObservability.log_stage_metrics(
        "features",
        data,
        symbol
    )


def observe_ml_predictions(
    data: pd.Series,
    symbol: str
) -> DataQualityMetrics:
    """Observe ML prediction quality."""
    return DataObservability.log_stage_metrics(
        "ml_predictions",
        data,
        symbol
    )


class DataQualityDashboard:
    """
    Dashboard for viewing data quality over time.
    """
    
    def __init__(self):
        self._history: List[DataQualityMetrics] = []
    
    def record(self, metrics: DataQualityMetrics) -> None:
        """Record metrics to history."""
        self._history.append(metrics)
    
    def get_stage_summary(self, stage: str) -> Dict[str, Any]:
        """Get summary for a specific stage."""
        stage_metrics = [m for m in self._history if m.stage == stage]
        
        if not stage_metrics:
            return {"error": "No metrics found for stage"}
        
        recent = stage_metrics[-100:]  # Last 100 records
        
        return {
            "stage": stage,
            "total_records": len(stage_metrics),
            "recent_records": len(recent),
            "avg_nan_ratio": np.mean([m.nan_ratio for m in recent]),
            "avg_data_delay": np.mean([
                m.data_delay_seconds for m in recent 
                if m.data_delay_seconds
            ]),
            "failure_rate": sum(1 for m in recent if not m.passed) / len(recent),
            "latest_timestamp": max(m.timestamp for m in recent)
        }
    
    def get_violations_report(self) -> List[Dict[str, Any]]:
        """Get report of all violations."""
        violations = []
        
        for m in self._history:
            if not m.passed:
                violations.append({
                    "timestamp": m.timestamp,
                    "stage": m.stage,
                    "nan_ratio": m.nan_ratio,
                    "violations": m.violations
                })
        
        return violations


# Global dashboard instance
dashboard = DataQualityDashboard()
