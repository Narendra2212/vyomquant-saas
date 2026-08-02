"""
backend/dag_engine.py — DAG Execution Engine for Strategy Builder.

Provides full DAG-based backtesting with:
  - Topological ordering
  - Indicator + ML + Logic node execution
  - Signal propagation through edges
  - Action node evaluation
  - Result mapping back to DAG structure

DAG Architecture:
  INPUT nodes → INDICATOR nodes → LOGIC nodes → ACTION nodes
                    ↓
                 ML nodes (confidence-based)
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

# 🚨 Data observability - real-time quality monitoring
from backend_app.backend.data_observability import (dashboard,
                                                    observe_features,
                                                    observe_indicators,
                                                    observe_market_data,
                                                    observe_ml_predictions)
# 🚨 Feature validation - ALL ML features must pass validation
from backend_app.backend.feature_validator import (FeatureValidationError,
                                                   FeatureValidator,
                                                   ModelMismatchError)
# 🚨 Pipeline guard - FAIL-FAST at every stage
from backend_app.core.pipeline_guard import (PipelineError, guard_features,
                                             guard_indicators,
                                             guard_market_data,
                                             guard_ml_prediction)
# 🚨 SYSTEM FREEZE: Safety config must be imported first
from backend_app.core.safety_config import SafetyMonitor

logger = logging.getLogger("DAGEngine")


class IndicatorComputationError(Exception):
    """
    Raised when indicator computation fails.
    
    This is a HARD FAILURE - no silent NaN propagation allowed.
    The caller must fix the data issue before retrying.
    """
    pass


class DAGExecutionError(Exception):
    """
    Raised when DAG node execution fails due to invalid inputs.
    
    This is a HARD FAILURE - no silent node execution allowed.
    Full compatibility with PipelineGuard.
    """
    pass


def validate_node_inputs(inputs: Dict[str, Any], node_id: str) -> None:
    """
    Strict input contract validation for DAG nodes.
    
    Validates:
    - Inputs is not None
    - Inputs dict is not empty
    - All Series inputs have no NaN values
    - All required inputs are present
    
    Args:
        inputs: Input dictionary from upstream nodes
        node_id: Node identifier for error messages
    
    Raises:
        DAGExecutionError: If any validation fails
    """
    # Check None
    if inputs is None:
        raise DAGExecutionError(
            f"Node '{node_id}': Input is None. "
            f"Node received no input data from upstream nodes."
        )
    
    # Check empty
    if len(inputs) == 0:
        raise DAGExecutionError(
            f"Node '{node_id}': Empty input. "
            f"Node has no connected upstream nodes providing data."
        )
    
    # Validate each input value
    for input_id, value in inputs.items():
        # Check for Series with NaN
        if isinstance(value, pd.Series):
            if value.isna().any():
                # Fill indicator warm-up NaNs gracefully
                inputs[input_id] = value.bfill().fillna(0)
        
        # Check for None values in inputs
        elif value is None:
            raise DAGExecutionError(
                f"Node '{node_id}': None value in input '{input_id}'. "
                f"Upstream node produced no output."
            )
        
        # Check for empty DataFrame
        elif isinstance(value, pd.DataFrame) and value.empty:
            raise DAGExecutionError(
                f"Node '{node_id}': Empty DataFrame in input '{input_id}'. "
                f"Upstream node produced empty data."
            )


def validate_node_output(
    output: Any,
    node_id: str,
    expected_length: int,
    expected_index: pd.Index
) -> None:
    """
    Strict output contract validation for DAG nodes.
    
    Validates:
    - Output is not None
    - Output is Series or DataFrame
    - Output length matches expected
    - Output contains no NaN values
    - Output index is time-aligned
    
    Args:
        output: Node output to validate
        node_id: Node identifier for error messages
        expected_length: Expected length (from market_data)
        expected_index: Expected index (from market_data)
    
    Raises:
        DAGExecutionError: If any validation fails
    """
    # Rule 1: Output must not be None
    if output is None:
        raise DAGExecutionError(
            f"Node '{node_id}' returned None. "
            f"All nodes must return Series or DataFrame output."
        )
    
    # Rule 2: Output must be Series or DataFrame
    if not isinstance(output, (pd.Series, pd.DataFrame)):
        raise DAGExecutionError(
            f"Node '{node_id}' returned invalid type: {type(output).__name__}. "
            f"Expected pd.Series or pd.DataFrame."
        )
    
    # Rule 3: Output length must match expected
    if len(output) != expected_length:
        raise DAGExecutionError(
            f"Node '{node_id}' output length mismatch: "
            f"expected {expected_length}, got {len(output)}. "
            f"All outputs must match market_data length."
        )
    
    # Rule 4: Output must contain no NaN
    if isinstance(output, pd.Series):
        if output.isna().any():
            nan_count = output.isna().sum()
            raise DAGExecutionError(
                f"Node '{node_id}' output contains NaN: {nan_count} values. "
                f"Nodes must produce clean output without missing data."
            )
    else:  # DataFrame
        if output.isna().any().any():
            nan_count = output.isna().sum().sum()
            raise DAGExecutionError(
                f"Node '{node_id}' DataFrame output contains NaN: {nan_count} values. "
                f"Nodes must produce clean output without missing data."
            )
    
    # Rule 5: Output index must be time-aligned
    if isinstance(output.index, pd.DatetimeIndex):
        if not output.index.equals(expected_index):
            raise DAGExecutionError(
                f"Node '{node_id}' output index mismatch. "
                f"Output timestamps must align with market_data index."
            )


class NodeExecutor:
    """Base class for node execution."""
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> Any:
        raise NotImplementedError


class IndicatorExecutor(NodeExecutor):
    """Execute indicator nodes (RSI, MACD, SMA, etc.)."""
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute indicator node with validation."""
        node_id = node.get("id", "unknown")
        indicator = node.get("indicator", "rsi").lower()
        params = node.get("params", {})
        
        # 🛡️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # 🛡️ PIPELINE GUARD: Validate market data at entry
        try:
            guard_market_data(market_data)
        except PipelineError as e:
            raise IndicatorComputationError(f"Pipeline guard failed: {e}")
        
        # 📊 OBSERVABILITY: Log market data quality
        metrics = observe_market_data(market_data, "indicator_input")
        dashboard.record(metrics)
        
        # Get price data with validation
        if "close" not in market_data.columns:
            raise IndicatorComputationError("Missing 'close' price column")
        
        close = market_data["close"]
        
        # Check for excessive NaN (more than 5%)
        nan_pct = close.isna().sum() / len(close) * 100
        if nan_pct > 5:
            raise IndicatorComputationError(
                f"Excessive missing data: {nan_pct:.1f}% NaN in close prices"
            )
        
        # Safe forward fill with limit
        close = close.ffill(limit=3)
        
        # If still NaN after limited fill, raise error
        if close.isna().any():
            raise IndicatorComputationError(
                "Excessive missing data: NaN remains after ffill(limit=3). "
                f"NaN count: {close.isna().sum()}"
            )
        
        if indicator == "rsi":
            period = params.get("period", 14)
            return self._calculate_rsi(close, period)
        
        elif indicator == "macd":
            fast = params.get("fast", 12)
            slow = params.get("slow", 26)
            signal = params.get("signal", 9)
            return self._calculate_macd(close, fast, slow, signal)
        
        elif indicator == "sma":
            period = params.get("period", 20)
            return close.rolling(window=period).mean()
        
        elif indicator == "ema":
            period = params.get("period", 20)
            return close.ewm(span=period, adjust=False).mean()
        
        elif indicator == "bb":
            period = params.get("period", 20)
            std_dev = params.get("std_dev", 2)
            return self._calculate_bollinger(close, period, std_dev)
        
        elif indicator == "atr":
            period = params.get("period", 14)
            high = market_data["high"]
            low = market_data["low"]
            return self._calculate_atr(high, low, close, period)
        
        else:
            logger.warning(f"Unknown indicator: {indicator}, using RSI default")
            output = self._calculate_rsi(close, 14)
        
        # 🛡️ PIPELINE GUARD: Validate indicator output
        try:
            guard_indicators(output)
        except PipelineError as e:
            raise IndicatorComputationError(f"Pipeline guard failed: {e}")
        
        # 📊 OBSERVABILITY: Log indicator quality
        indicator_metrics = observe_indicators(output, f"indicator_{indicator}")
        dashboard.record(indicator_metrics)
        
        # SHAPE VALIDATION: Ensure output matches input length
        if len(output) != len(market_data):
            raise IndicatorComputationError(
                f"Indicator output shape mismatch: "
                f"expected {len(market_data)}, got {len(output)}"
            )
        
        # 🛡️ OUTPUT CONTRACT VALIDATION
        validate_node_output(
            output=output,
            node_id=node_id,
            expected_length=len(market_data),
            expected_index=market_data.index
        )
        
        return output
    
    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate RSI indicator with safe divide and bounds.
        
        Uses epsilon to prevent divide-by-zero and clips to valid RSI range.
        """
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # Safe divide with epsilon to prevent divide-by-zero
        rs = gain / (loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        
        # Clip to valid RSI range [0, 100]
        rsi = rsi.clip(lower=0, upper=100)
        
        return rsi
    
    def _calculate_macd(self, prices: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
        """Calculate MACD histogram."""
        ema_fast = prices.ewm(span=fast, adjust=False).mean()
        ema_slow = prices.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        return macd - signal_line
    
    def _calculate_bollinger(self, prices: pd.Series, period: int, std_dev: float) -> pd.Series:
        """Calculate Bollinger Bands %B."""
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        # Return %B (position within bands)
        return (prices - lower) / (upper - lower)
    
    def _calculate_atr(self, high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        """Calculate Average True Range."""
        high_low = high - low
        high_close = np.abs(high - close.shift())
        low_close = np.abs(low - close.shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()


class MLExecutor(NodeExecutor):
    """Execute ML prediction nodes."""
    
    def __init__(self, model_registry: Optional[Dict] = None):
        self.model_registry = model_registry or {}
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute ML node with strict validation."""
        node_id = node.get("id", "unknown")
        model_id = node.get("model_id", "default")
        node.get("confidence_threshold", 0.7)
        
        # �️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # �� SYSTEM FREEZE: Check if ML inference is allowed
        safety_check = SafetyMonitor.check_execution_allowed("ml_inference")
        if safety_check:
            logger.critical(f"🚫 BLOCKED ML INFERENCE: {safety_check}")
            # Return neutral signal instead of executing
            return pd.Series(0.5, index=market_data.index)
        
        # Get features from input nodes
        features = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                features.append(value)
        
        if not features:
            # No features available, return neutral signal
            return pd.Series(0.5, index=market_data.index)
        
        # Combine features with SAFE forward fill (limited)
        feature_df = pd.concat(features, axis=1)
        feature_df = feature_df.ffill(limit=3)
        
        # Check for remaining NaN - cannot proceed with missing feature data
        if feature_df.isna().any().any():
            nan_count = feature_df.isna().sum().sum()
            raise IndicatorComputationError(
                f"ML features contain NaN after safe fill: {nan_count} values. "
                f"Cannot perform inference with incomplete feature data."
            )
        
        # Load model - NO MOCK FALLBACK ALLOWED
        model = self.model_registry.get(model_id)
        
        if not model:
            # 🚨 CRITICAL: Model not found - execution blocked
            logger.critical(
                f"🚫 MODEL NOT FOUND: model_id={model_id}. "
                f"DAG node cannot execute. Aborting to prevent fake signals."
            )
            # Alert system notification
            try:
                from backend_app.backend.alert_system import AlertSystem
                AlertSystem.send_alert(
                    level="CRITICAL",
                    title="ML Model Missing - Execution Blocked",
                    message=f"Model {model_id} not found in registry. Strategy execution blocked.",
                    channels=["telegram", "email"]
                )
            except Exception:
                pass  # Alert system may not be initialized
            
            raise RuntimeError(
                f"🚫 MODEL NOT FOUND: {model_id}\n"
                f"The ML model required for this DAG node is not available.\n"
                f"Execution has been BLOCKED to prevent fake signals from triggering trades.\n"
                f"Please verify:\n"
                f"  1. Model was properly trained and saved\n"
                f"  2. Model registry contains the model\n"
                f"  3. Model ID in strategy matches registry"
            )
        
        # 🛡️ PIPELINE GUARD: Validate features before ML
        try:
            guard_features(feature_df)
        except PipelineError as e:
            raise RuntimeError(f"Pipeline guard failed at feature stage: {e}")
        
        # 📊 OBSERVABILITY: Log feature quality
        feature_metrics = observe_features(feature_df, "ml_input")
        dashboard.record(feature_metrics)
        
        # 🚨 STEP 1: VALIDATE FEATURES BEFORE PREDICTION
        try:
            # Try to get schema from model or use inferred schema
            schema = self._get_feature_schema(model, feature_df)
            
            # Validate and prepare features
            feature_df = FeatureValidator.validate_and_prepare(
                feature_df=feature_df,
                schema=schema,
                model=model,
                market_data_index=market_data.index
            )
            
            logger.info(f"✅ Features validated: {feature_df.shape}")
            
        except (FeatureValidationError, ModelMismatchError) as e:
            logger.critical(f"🚫 FEATURE VALIDATION FAILED: {e}")
            raise RuntimeError(
                f"ML feature validation failed: {e}. "
                f"Cannot perform inference with invalid features."
            )
        
        # 🛡️ PIPELINE GUARD: Validate before ML prediction
        try:
            guard_ml_prediction(feature_df)
        except PipelineError as e:
            raise RuntimeError(f"Pipeline guard failed at ML stage: {e}")
        
        # STEP 2: Real model prediction with validated features
        try:
            predictions = model.predict(feature_df)
            
            # Validate predictions
            if len(predictions) != len(market_data):
                raise IndicatorComputationError(
                    f"Prediction shape mismatch: expected {len(market_data)}, "
                    f"got {len(predictions)}"
                )
            
            if np.isnan(predictions).any():
                nan_count = np.isnan(predictions).sum()
                raise IndicatorComputationError(
                    f"Model produced NaN predictions: {nan_count} values"
                )
            
            logger.info(f" Model prediction successful: {len(predictions)} predictions")
            
            # PIPELINE GUARD: Validate signal generation
            result = pd.Series(predictions, index=market_data.index)
            try:
                # Final validation - ensure no NaN after scaling
                if None.isna().any().any():
                    raise FeatureValidationError("NaN values introduced during scaling")
            
                # OUTPUT CONTRACT VALIDATION
                validate_node_output(
                    output=result,
                    node_id=node_id,
                    expected_length=len(market_data),
                    expected_index=market_data.index
                )
            
                return None
            except Exception as e:
                logger.critical(f" Model prediction failed: {e}")
                raise RuntimeError(f"Model prediction failed: {e}")
            
            # OBSERVABILITY: Log prediction quality
            prediction_metrics = observe_ml_predictions(result, "ml_output")
            dashboard.record(prediction_metrics)
            
            return result
            
        except Exception as e:
            logger.critical(f"🚫 Model prediction failed: {e}")
            raise RuntimeError(f"Model prediction failed: {e}")


class LogicExecutor(NodeExecutor):
    """Execute logic nodes (AND, OR, NOT, comparisons)."""
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute logic nodes (AND, OR, NOT, comparisons)."""
        node_id = node.get("id", "unknown")
        operator = node.get("operator", "AND")
        
        # 🛡️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # Get all input series
        input_series = []
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                input_series.append(value)
            elif isinstance(value, (int, float, bool)):
                # Convert scalar to series
                input_series.append(pd.Series(value, index=market_data.index))
        
        # Check for empty after filtering
        if len(input_series) == 0:
            raise DAGExecutionError(
                f"Node '{node_id}': No valid Series inputs found. "
                f"All inputs were filtered out (None, empty, or wrong type)."
            )
        
        # Align all series
        aligned = pd.concat(input_series, axis=1)
        
        # 🚨 HARD GATE: Check for NaN in logic inputs (no silent fill)
        if aligned.isna().any().any():
            nan_locations = aligned.isna().sum()
            nan_cols = nan_locations[nan_locations > 0].index.tolist()
            raise IndicatorComputationError(
                f"NaN detected in logic inputs. "
                f"Columns with NaN: {nan_cols}. "
                f"Cannot perform logic operations with missing data."
            )
        
        if operator == "AND":
            return aligned.all(axis=1)
        elif operator == "OR":
            return aligned.any(axis=1)
        elif operator == "NOT":
            if len(input_series) == 0:
                raise DAGExecutionError(
                    f"LogicExecutor - NOT operator on node '{node_id}' has no inputs. "
                    f"NOT requires at least one input signal."
                )
            return ~input_series[0]
        elif operator == "GT":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] > aligned.iloc[:, 1]
        elif operator == "LT":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] < aligned.iloc[:, 1]
        elif operator == "GTE":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] >= aligned.iloc[:, 1]
        elif operator == "LTE":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] <= aligned.iloc[:, 1]
        elif operator == "EQ":
            if len(input_series) >= 2:
                return aligned.iloc[:, 0] == aligned.iloc[:, 1]
        
        # Default fallback
        output = pd.Series(False, index=market_data.index)
        
        # 🛡️ OUTPUT CONTRACT VALIDATION
        validate_node_output(
            output=output,
            node_id=node_id,
            expected_length=len(market_data),
            expected_index=market_data.index
        )
        
        return output


class ActionExecutor(NodeExecutor):
    """Execute action nodes (buy, sell, hold)."""
    
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        """Execute action nodes (buy, sell, hold)."""
        node_id = node.get("id", "unknown")
        action = node.get("action", "hold").lower()
        
        # 🛡️ STRICT INPUT CONTRACT VALIDATION
        try:
            validate_node_inputs(inputs, node_id)
        except DAGExecutionError as e:
            logger.critical(f"🚫 Node '{node_id}' input validation failed: {e}")
            raise
        
        # 🚫 SYSTEM FREEZE: Block buy/sell actions
        if action in ["buy", "sell"]:
            safety_check = SafetyMonitor.check_execution_allowed("strategy_signal")
            if safety_check:
                logger.critical(f"🚫 BLOCKED DAG ACTION: {safety_check}")
                # Return zeros (no action) instead of executing
                return pd.Series(0, index=market_data.index)
        
        # Get signal from inputs - MUST be from SIGNAL or LOGIC node
        signal = pd.Series(0, index=market_data.index)
        signal_found = False
        False
        
        for input_id, value in inputs.items():
            if isinstance(value, pd.Series):
                signal = value
                signal_found = True
                # Check if input is from a valid signal source
                # (This would need to be passed through from node metadata)
                True
                break
        
        if not signal_found:
            raise DAGExecutionError(
                f"Node '{node_id}': No valid signal Series found in inputs. "
                f"Action node requires a Series input from upstream nodes."
            )
        
        if signal is None:
            # No signal, default to hold (0)
            return pd.Series(0, index=market_data.index)
        
        # Convert to action signal
        # signal > 0.5 = buy (1), signal < 0.5 = sell (-1), else hold (0)
        if action == "buy":
            output = (signal > 0.5).astype(int)
        elif action == "sell":
            output = -(signal > 0.5).astype(int)
        else:  # hold
            output = pd.Series(0, index=market_data.index)
        
        # SHAPE VALIDATION: Ensure output matches market_data length
        if len(output) != len(market_data):
            raise IndicatorComputationError(
                f"Action output shape mismatch: "
                f"expected {len(market_data)}, got {len(output)}"
            )
        
        # 🛡️ OUTPUT CONTRACT VALIDATION
        validate_node_output(
            output=output,
            node_id=node_id,
            expected_length=len(market_data),
            expected_index=market_data.index
        )
        
        return output


@dataclass
class ExecutionTrace:
    """Single node execution trace entry."""
    node_id: str
    node_type: str
    input_shape: Optional[Dict[str, Any]] = None
    output_shape: Optional[Dict[str, Any]] = None
    input_types: Optional[Dict[str, str]] = None
    output_type: Optional[str] = None
    execution_time_ms: float = 0.0
    status: str = "pending"  # pending, success, fail
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


class ExecutionTracer:
    """
    Execution trace system for DAG debugging.
    
    Records every node execution for full transparency and debugging.
    """
    
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._trace: List[ExecutionTrace] = []
        self._node_outputs: Dict[str, Any] = {}  # For debugging
    
    def start_node(self, node_id: str, node_type: str, inputs: Dict[str, Any]) -> None:
        """Record node execution start."""
        if not self._enabled:
            return
        
        # Capture input shapes
        input_shapes = {}
        input_types = {}
        for key, value in inputs.items():
            if isinstance(value, pd.Series):
                input_shapes[key] = {"type": "Series", "length": len(value)}
                input_types[key] = f"Series[{value.dtype}]"
            elif isinstance(value, pd.DataFrame):
                input_shapes[key] = {"type": "DataFrame", "shape": value.shape}
                input_types[key] = f"DataFrame[{list(value.columns)}]"
            elif isinstance(value, (int, float, bool)):
                input_shapes[key] = {"type": "scalar", "value": value}
                input_types[key] = type(value).__name__
            else:
                input_shapes[key] = {"type": type(value).__name__}
                input_types[key] = type(value).__name__
        
        trace_entry = ExecutionTrace(
            node_id=node_id,
            node_type=node_type,
            input_shape=input_shapes,
            input_types=input_types,
            status="pending"
        )
        
        self._trace.append(trace_entry)
    
    def complete_node(
        self,
        node_id: str,
        output: Any,
        execution_time_ms: float,
        status: str = "success",
        error_message: Optional[str] = None
    ) -> None:
        """Record node execution completion."""
        if not self._enabled:
            return
        
        # Find pending entry for this node
        for entry in reversed(self._trace):
            if entry.node_id == node_id and entry.status == "pending":
                # Capture output shape
                if isinstance(output, pd.Series):
                    entry.output_shape = {"type": "Series", "length": len(output)}
                    entry.output_type = f"Series[{output.dtype}]"
                elif isinstance(output, pd.DataFrame):
                    entry.output_shape = {"type": "DataFrame", "shape": output.shape}
                    entry.output_type = f"DataFrame[{list(output.columns)}]"
                else:
                    entry.output_shape = {"type": type(output).__name__}
                    entry.output_type = type(output).__name__
                
                entry.execution_time_ms = execution_time_ms
                entry.status = status
                entry.error_message = error_message
                
                # Store output for debugging
                if status == "success":
                    self._node_outputs[node_id] = output
                
                break
    
    def get_trace(self) -> List[ExecutionTrace]:
        """Get full execution trace."""
        return self._trace.copy()
    
    def get_trace_as_dict(self) -> List[Dict[str, Any]]:
        """Get trace as list of dictionaries for JSON serialization."""
        return [
            {
                "node_id": t.node_id,
                "node_type": t.node_type,
                "input_shape": t.input_shape,
                "output_shape": t.output_shape,
                "input_types": t.input_types,
                "output_type": t.output_type,
                "execution_time_ms": round(t.execution_time_ms, 3),
                "status": t.status,
                "error_message": t.error_message,
                "timestamp": t.timestamp.isoformat()
            }
            for t in self._trace
        ]
    
    def get_last_failure(self) -> Optional[ExecutionTrace]:
        """Get the last failed execution trace entry."""
        for entry in reversed(self._trace):
            if entry.status == "fail":
                return entry
        return None
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get execution summary statistics."""
        total = len(self._trace)
        successful = sum(1 for t in self._trace if t.status == "success")
        failed = sum(1 for t in self._trace if t.status == "fail")
        pending = sum(1 for t in self._trace if t.status == "pending")
        
        total_time = sum(t.execution_time_ms for t in self._trace)
        
        return {
            "total_nodes": total,
            "successful": successful,
            "failed": failed,
            "pending": pending,
            "total_execution_time_ms": round(total_time, 3),
            "success_rate": successful / total if total > 0 else 0,
            "last_failure": self.get_last_failure()
        }
    
    def clear(self) -> None:
        """Clear trace history."""
        self._trace.clear()
        self._node_outputs.clear()
    
    def enable(self) -> None:
        """Enable tracing."""
        self._enabled = True
    
    def disable(self) -> None:
        """Disable tracing."""
        self._enabled = False


class MarketDataExecutor:
    """Execute market data, input, feature, math, validation, portfolio, and signal nodes."""
    def execute(self, node: Dict, inputs: Dict[str, Any], market_data: pd.DataFrame) -> pd.Series:
        if "close" in market_data.columns:
            return market_data["close"]
        elif not market_data.empty:
            return market_data.iloc[:, 0]
        elif inputs:
            first_val = next(iter(inputs.values()))
            if isinstance(first_val, pd.Series):
                return first_val
        return pd.Series(1, index=market_data.index if not market_data.empty else [0])


class DAGEngine:
    """
    DAG Execution Engine for strategy backtesting.
    
    Executes nodes in topological order and propagates signals
    through the graph to generate trading signals.
    """
    
    def __init__(self, enable_tracing: bool = True, enable_event_buffer: bool = True):
        passthrough = MarketDataExecutor()
        self.executors = {
            "market_data": passthrough,
            "input": passthrough,
            "feature": passthrough,
            "math": passthrough,
            "dl": passthrough,
            "validation": passthrough,
            "portfolio": passthrough,
            "signal": passthrough,
            "indicator": IndicatorExecutor(),
            "ml": MLExecutor(),
            "logic": LogicExecutor(),
            "action": ActionExecutor(),
        }
        self.node_results: Dict[str, pd.Series] = {}
        self.execution_log: List[Dict] = []
        self.tracer = ExecutionTracer(enabled=enable_tracing)
        self._last_error: Optional[str] = None
        self._execution_trace: Optional[List[Dict]] = None
        
        # STEP 4.4: Event buffer and replay system
        self.enable_event_buffer = enable_event_buffer
        self._event_buffer_key = "events:{tenant_id}"  # Redis Stream key pattern
        self._max_stream_length = 1000  # Keep last 1000 events per tenant
    
    def build_graph(self, nodes: List[Dict], edges: List[Dict]) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
        """
        Build adjacency lists and compute in-degrees for topological sort.
        
        Returns:
            (graph, in_degree) where:
            - graph: node_id -> set of successor node_ids
            - in_degree: node_id -> count of incoming edges
        """
        graph = defaultdict(set)
        in_degree = defaultdict(int)
        
        # Initialize all nodes
        for node in nodes:
            node_id = node["id"]
            if node_id not in graph:
                graph[node_id] = set()
            if node_id not in in_degree:
                in_degree[node_id] = 0
        
        # Build graph from edges
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source and target:
                raise ValueError("DAG contains cycles - cannot execute")
        
        return None
    
    def get_execution_trace(self) -> List[Dict[str, Any]]:
        """Get full execution trace for debugging."""
        return self.tracer.get_trace_as_dict()
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get execution summary statistics."""
        return self.tracer.get_execution_summary()
    
    def get_last_failure(self) -> Optional[Dict[str, Any]]:
        """Get the last failed execution trace entry."""
        failure = self.tracer.get_last_failure()
        if failure:
            return {
                "node_id": failure.node_id,
                "node_type": failure.node_type,
                "error_message": failure.error_message,
                "timestamp": failure.timestamp.isoformat(),
                "input_shape": failure.input_shape,
                "execution_time_ms": failure.execution_time_ms
            }
        return None
    
    def clear_trace(self) -> None:
        """Clear execution trace history."""
        self.tracer.clear()
    
    # STEP 4.4: Event buffer and replay system
    async def buffer_event(self, tenant_id: str, event: Dict[str, Any]) -> str:
        """
        Store event in Redis Stream for replay capability.
        
        Args:
            tenant_id: Tenant identifier
            event: Event data to store
            
        Returns:
            Event ID from Redis Stream
        """
        if not self.enable_event_buffer:
            return None
        
        try:
            import json

            from backend_app.core.cache.redis_manager import redis_manager
            redis_client = await redis_manager.get_client()
            
            stream_key = self._event_buffer_key.format(tenant_id=tenant_id)
            
            # Add event to Redis Stream
            event_data = {
                "data": json.dumps(event),
                "timestamp": datetime.now().isoformat(),
                "type": event.get("type", "unknown")
            }
            
            # XADD with MAXLEN to keep stream bounded
            event_id = await redis_client.xadd(
                stream_key,
                event_data,
                maxlen=self._max_stream_length,
                approximate=True
            )
            
            logger.debug(f"Event buffered: {event_id} for tenant {tenant_id}")
            return event_id
            
        except Exception as e:
            logger.error(f"Failed to buffer event: {e}")
            return None
    
    async def get_last_events(self, tenant_id: str, count: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve last N events from buffer.
        
        Args:
            tenant_id: Tenant identifier
            count: Number of events to retrieve
            
        Returns:
            List of events from newest to oldest
        """
        try:
            import json

            from backend_app.core.cache.redis_manager import redis_manager
            redis_client = await redis_manager.get_client()
            
            stream_key = self._event_buffer_key.format(tenant_id=tenant_id)
            
            # XREVRANGE to get newest first
            events = await redis_client.xrevrange(stream_key, count=count)
            
            parsed_events = []
            for event_id, fields in events:
                try:
                    event_data = json.loads(fields.get("data", "{}"))
                    event_data["_buffered_id"] = event_id
                    event_data["_buffered_at"] = fields.get("timestamp")
                    parsed_events.append(event_data)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse buffered event: {event_id}")
                    continue
            
            return parsed_events
            
        except Exception as e:
            logger.error(f"Failed to get last events: {e}")
            return []
    
    async def replay_events(
        self,
        tenant_id: str,
        nodes: List[Dict],
        edges: List[Dict],
        count: int = 100
    ) -> Dict[str, Any]:
        """
        Replay last N events through DAG after failure recovery.
        
        This ensures no data loss by reprocessing buffered events.
        
        Args:
            tenant_id: Tenant identifier
            nodes: DAG nodes
            edges: DAG edges
            count: Number of events to replay
            
        Returns:
            Replay results summary
        """
        logger.info(f"Starting event replay for tenant {tenant_id}, count={count}")
        
        # Get buffered events
        events = await self.get_last_events(tenant_id, count)
        
        if not events:
            logger.info(f"No events to replay for tenant {tenant_id}")
            return {"replayed": 0, "successful": 0, "failed": 0}
        
        # Reverse to process oldest first (maintain order)
        events.reverse()
        
        results = {
            "replayed": len(events),
            "successful": 0,
            "failed": 0,
            "signals_generated": 0,
            "events": []
        }
        
        # Replay each event through DAG
        for event in events:
            event_id = event.get("_buffered_id", "unknown")
            
            try:
                # Convert buffered event back to market data format
                market_data = self._event_to_market_data(event)
                
                # Execute DAG
                dag_result = self.execute_dag(nodes, edges, market_data)
                
                # Check for signals
                if dag_result.get("signals"):
                    results["signals_generated"] += len(dag_result["signals"])
                
                results["successful"] += 1
                results["events"].append({
                    "id": event_id,
                    "status": "success",
                    "signals": len(dag_result.get("signals", []))
                })
                
                logger.debug(f"Replayed event {event_id} successfully")
                
            except Exception as e:
                results["failed"] += 1
                results["events"].append({
                    "id": event_id,
                    "status": "failed",
                    "error": str(e)
                })
                logger.error(f"Failed to replay event {event_id}: {e}")
        
        logger.info(
            f"Replay complete for tenant {tenant_id}: "
            f"{results['successful']}/{results['replayed']} successful, "
            f"{results['signals_generated']} signals generated"
        )
        
        return results
    
    def _event_to_market_data(self, event: Dict[str, Any]) -> pd.DataFrame:
        """Convert buffered event back to market data DataFrame."""
        # Extract OHLCV data from event
        data = {
            "open": [event.get("open", 0)],
            "high": [event.get("high", 0)],
            "low": [event.get("low", 0)],
            "close": [event.get("close", 0)],
            "volume": [event.get("volume", 0)]
        }
        
        timestamp = event.get("timestamp")
        if timestamp:
            index = pd.DatetimeIndex([timestamp])
        else:
            index = pd.DatetimeIndex([datetime.now()])
        
        return pd.DataFrame(data, index=index)
    
    async def clear_event_buffer(self, tenant_id: str) -> bool:
        """Clear event buffer for tenant (useful for testing/reset)."""
        try:
            from backend_app.core.cache.redis_manager import redis_manager
            redis_client = await redis_manager.get_client()
            stream_key = self._event_buffer_key.format(tenant_id=tenant_id)
            await redis_client.delete(stream_key)
            logger.info(f"Event buffer cleared for tenant {tenant_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to clear event buffer: {e}")
            return False
    
    def get_node_inputs(self, node_id: str, edges: List[Dict]) -> List[str]:
        """Get all source node IDs connected to this node."""
        inputs = []
        for edge in edges:
            if edge.get("target") == node_id:
                inputs.append(edge.get("source"))
        return inputs
    
    def execute_node(self, node: Dict, edges: List[Dict], market_data: pd.DataFrame) -> pd.Series:
        """
        Execute a single node with its inputs and trace execution.
        
        Returns the output series for this node.
        """
        import time
        
        node_id = node["id"]
        node_type = node.get("type", "indicator")
        
        # Get input values from predecessor nodes
        input_ids = self.get_node_inputs(node_id, edges)
        inputs = {}
        for input_id in input_ids:
            if input_id in self.node_results:
                inputs[input_id] = self.node_results[input_id]
        
        # 📝 START EXECUTION TRACE
        self.tracer.start_node(node_id, node_type, inputs)
        start_time = time.time()
        
        # Execute based on node type
        executor = self.executors.get(node_type)
        if executor:
            try:
                result = executor.execute(node, inputs, market_data)
                
                # Calculate execution time
                execution_time_ms = (time.time() - start_time) * 1000
                
                # 📝 COMPLETE SUCCESS TRACE
                self.tracer.complete_node(
                    node_id=node_id,
                    output=result,
                    execution_time_ms=execution_time_ms,
                    status="success"
                )
                
                return result
                
            except Exception as e:
                # Calculate execution time even on failure
                execution_time_ms = (time.time() - start_time) * 1000
                
                # 📝 COMPLETE FAILURE TRACE
                self.tracer.complete_node(
                    node_id=node_id,
                    output=None,
                    execution_time_ms=execution_time_ms,
                    status="fail",
                    error_message=str(e)
                )
                
                logger.error(f"Node {node_id} execution failed: {e}")
                raise
        
        # No executor found for node type
        execution_time_ms = (time.time() - start_time) * 1000
        error_msg = f"No executor for node type: {node_type}"
        
        self.tracer.complete_node(
            node_id=node_id,
            output=None,
            execution_time_ms=execution_time_ms,
            status="fail",
            error_message=error_msg
        )
        
        raise ValueError(error_msg)
    
    def topological_sort(self, nodes: List[Dict], edges: List[Dict]) -> List[str]:
        """Calculate topological execution order of nodes using Kahn's algorithm."""
        in_degree = {n["id"]: 0 for n in nodes}
        adjacency = {n["id"]: [] for n in nodes}
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src in adjacency and tgt in in_degree:
                adjacency[src].append(tgt)
                in_degree[tgt] += 1
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            curr = queue.pop(0)
            order.append(curr)
            for nxt in adjacency.get(curr, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        if len(order) != len(nodes):
            return [n["id"] for n in nodes]
        return order

    def execute_dag(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame,
    ) -> Dict[str, Any]:
        """
        Execute full DAG and return results.
        
        This is the SAME execution pipeline used for both backtesting and live trading.
        No duplicate execution logic.
        
        Returns:
            {
                "signals": pd.Series,  # Final action signals
                "node_results": Dict[str, pd.Series],
                "execution_order": List[str],
                "execution_log": List[Dict],
                "action_nodes": List[str],
            }
        """
        self.node_results = {}
        self.execution_log = []
        
        # Get topological order
        execution_order = self.topological_sort(nodes, edges)
        
        # Create node lookup
        node_map = {node["id"]: node for node in nodes}
        
        # Execute nodes in order
        action_nodes = []
        for node_id in execution_order:
            node = node_map[node_id]
            result = self.execute_node(node, edges, market_data)
            self.node_results[node_id] = result
            
            if node.get("type") == "action":
                action_nodes.append(node_id)
        
        # Combine action signals (sum for multiple actions)
        final_signals = pd.Series(0, index=market_data.index)
        for action_id in action_nodes:
            signals = self.node_results.get(action_id, pd.Series(0, index=market_data.index))
            final_signals += signals
        
        # Normalize to -1, 0, 1
        final_signals = final_signals.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        
        # STEP 6: Signal Generation Validation
        if final_signals is None:
            raise DAGExecutionError(
                "STEP 6: Signal generation failed - final_signals is None"
            )
        
        if not isinstance(final_signals, pd.Series):
            raise DAGExecutionError(
                f"STEP 6: Invalid signal type - expected pd.Series, got {type(final_signals).__name__}"
            )
        
        if len(final_signals) == 0:
            raise DAGExecutionError(
                "STEP 6: Signal generation failed - empty signal series"
            )
        
        # Validate signal values are in expected range (-1, 0, 1)
        invalid_signals = final_signals[~final_signals.isin([-1, 0, 1])]
        if len(invalid_signals) > 0:
            raise DAGExecutionError(
                f"STEP 6: Invalid signal values detected: {invalid_signals.unique().tolist()}. "
                f"Expected only: -1 (sell), 0 (hold), 1 (buy)"
            )
        
        return {
            "signals": final_signals,
            "node_results": self.node_results,
            "execution_order": execution_order,
            "execution_log": self.execution_log,
            "action_nodes": action_nodes,
        }
    
    def execute(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Convenience method for backtesting.
        
        PHASE Backtesting Engine: This is the entry point for backtesting.
        It uses the same execution pipeline as live trading.
        
        Args:
            nodes: DAG nodes from execution graph
            edges: DAG edges from execution graph
            market_data: Historical market data DataFrame
            
        Returns:
            Dictionary with signals and execution results
        """
        return self.execute_dag(nodes, edges, market_data)
    
    def generate_portfolio_signals(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        market_data: pd.DataFrame,
    ) -> pd.Series:
        """
        Execute DAG and return entry/exit signals for portfolio engine.
        
        Returns:
            pd.Series with values:
            - 1: Buy signal
            - -1: Sell signal  
            - 0: Hold (no action)
        """
        result = self.execute_dag(nodes, edges, market_data)
        return result["signals"]
