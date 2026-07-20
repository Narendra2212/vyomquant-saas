"""
backend/feature_validator.py — Strict Feature Validation System

Validates ML feature data against training schema before inference.
Prevents silent model corruption from mismatched features.

🚨 CRITICAL SAFETY COMPONENT — DO NOT MODIFY WITHOUT APPROVAL
"""

import logging
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

logger = logging.getLogger("FeatureValidator")


class FeatureValidationError(Exception):
    """
    Raised when feature validation fails.
    
    This is a HARD FAILURE - ML inference cannot proceed with invalid features.
    """
    pass


class ModelMismatchError(Exception):
    """
    Raised when model and features are incompatible.
    
    Examples: wrong feature count, incompatible shapes, etc.
    """
    pass


@dataclass
class FeatureSchema:
    """
    Strict schema for ML features.
    
    Defines expected features, count, and preprocessing requirements.
    Must match the schema used during training exactly.
    """
    expected_features: List[str]
    expected_count: int
    scaler: Optional[Any] = None  # sklearn StandardScaler or similar
    feature_dtypes: Optional[Dict[str, str]] = None
    feature_ranges: Optional[Dict[str, Tuple[float, float]]] = None
    
    def __post_init__(self):
        """Validate schema consistency."""
        if len(self.expected_features) != self.expected_count:
            raise ValueError(
                f"Schema inconsistency: expected_count={self.expected_count} "
                f"but len(expected_features)={len(self.expected_features)}"
            )


class FeatureValidator:
    """
    Central feature validation service.
    
    All features must pass strict validation before ML inference.
    """
    
    @staticmethod
    def validate_features(
        feature_df: pd.DataFrame,
        schema: FeatureSchema,
        market_data_index: Optional[pd.Index] = None
    ) -> pd.DataFrame:
        """
        Validate features against strict schema.
        
        Args:
            feature_df: Feature DataFrame to validate
            schema: Expected feature schema
            market_data_index: Optional index for alignment check
        
        Returns:
            pd.DataFrame: Validated (and possibly scaled) features
        
        Raises:
            FeatureValidationError: If any validation rule fails
            ModelMismatchError: If model compatibility check fails
        """
        logger.debug(f"Validating features: shape={feature_df.shape}, schema={schema.expected_count}")
        
        # Rule 1: No NaN values allowed
        if feature_df.isna().any().any():
            nan_count = feature_df.isna().sum().sum()
            nan_cols = feature_df.columns[feature_df.isna().any()].tolist()
            logger.critical(
                f"🚫 FEATURE VALIDATION FAILED: NaN values detected. "
                f"Count: {nan_count}, Columns: {nan_cols}"
            )
            raise FeatureValidationError(
                f"NaN values detected in features: {nan_count} total. "
                f"Columns with NaN: {nan_cols}. "
                f"ML inference cannot proceed with missing feature data."
            )
        
        # Rule 2: Column names MUST match training schema exactly
        actual_cols = set(feature_df.columns)
        expected_cols = set(schema.expected_features)
        
        missing_cols = expected_cols - actual_cols
        extra_cols = actual_cols - expected_cols
        
        if missing_cols:
            logger.critical(
                f"🚫 FEATURE VALIDATION FAILED: Missing columns. "
                f"Missing: {sorted(missing_cols)}"
            )
            raise FeatureValidationError(
                f"Missing required feature columns: {sorted(missing_cols)}. "
                f"Expected: {sorted(schema.expected_features)}. "
                f"Got: {sorted(feature_df.columns.tolist())}"
            )
        
        if extra_cols:
            logger.warning(
                f"⚠️ Extra columns in features (will be ignored): {sorted(extra_cols)}"
            )
            # Remove extra columns to match schema
            feature_df = feature_df[schema.expected_features]
        
        # Ensure column order matches schema
        feature_df = feature_df[schema.expected_features]
        
        # Rule 3: Feature count EXACT match
        if feature_df.shape[1] != schema.expected_count:
            logger.critical(
                f"🚫 FEATURE VALIDATION FAILED: Feature count mismatch. "
                f"Expected: {schema.expected_count}, Got: {feature_df.shape[1]}"
            )
            raise FeatureValidationError(
                f"Feature count mismatch: expected {schema.expected_count}, "
                f"got {feature_df.shape[1]}. "
                f"Model will produce garbage output with wrong feature count."
            )
        
        # Rule 4: Index aligned with market_data (if provided)
        if market_data_index is not None:
            if not feature_df.index.equals(market_data_index):
                logger.critical(
                    f"🚫 FEATURE VALIDATION FAILED: Index misalignment. "
                    f"Feature index length: {len(feature_df)}, "
                    f"Market data length: {len(market_data_index)}"
                )
                raise FeatureValidationError(
                    f"Feature index does not align with market data. "
                    f"Features: {len(feature_df)}, Market: {len(market_data_index)}. "
                    f"Prediction timestamps will be incorrect."
                )
        
        # Rule 5: No infinite values
        if np.isinf(feature_df.values).any():
            inf_count = np.isinf(feature_df.values).sum()
            logger.critical(
                f"🚫 FEATURE VALIDATION FAILED: Infinite values detected. "
                f"Count: {inf_count}"
            )
            raise FeatureValidationError(
                f"Infinite values detected in features: {inf_count} values. "
                f"ML models cannot handle infinity."
            )
        
        # Rule 6: Check data types (if specified)
        if schema.feature_dtypes:
            for col, expected_dtype in schema.feature_dtypes.items():
                if col in feature_df.columns:
                    actual_dtype = str(feature_df[col].dtype)
                    if not actual_dtype.startswith(expected_dtype):
                        logger.warning(
                            f"⚠️ Feature '{col}' dtype mismatch: "
                            f"expected {expected_dtype}, got {actual_dtype}"
                        )
        
        # Rule 7: Check value ranges (if specified)
        if schema.feature_ranges:
            for col, (min_val, max_val) in schema.feature_ranges.items():
                if col in feature_df.columns:
                    col_min = feature_df[col].min()
                    col_max = feature_df[col].max()
                    if col_min < min_val or col_max > max_val:
                        logger.warning(
                            f"⚠️ Feature '{col}' range violation: "
                            f"[{col_min:.4f}, {col_max:.4f}] not in [{min_val}, {max_val}]"
                        )
        
        logger.info(f"✅ Feature validation passed: {feature_df.shape}")
        return feature_df
    
    @staticmethod
    def apply_scaling(
        feature_df: pd.DataFrame,
        schema: FeatureSchema
    ) -> pd.DataFrame:
        """
        Apply scaler if provided in schema.
        
        Args:
            feature_df: Validated feature DataFrame
            schema: Feature schema with scaler
        
        Returns:
            pd.DataFrame: Scaled features
        """
        if schema.scaler is None:
            return feature_df
        
        try:
            # Apply scaler
            scaled_values = schema.scaler.transform(feature_df)
            scaled_df = pd.DataFrame(
                scaled_values,
                columns=feature_df.columns,
                index=feature_df.index
            )
            logger.debug(f"Applied scaling to {len(feature_df.columns)} features")
            return scaled_df
        except Exception as e:
            logger.critical(f"🚫 Scaling failed: {e}")
            raise FeatureValidationError(f"Feature scaling failed: {e}")
    
    @staticmethod
    def check_model_compatibility(
        model: Any,
        feature_df: pd.DataFrame
    ) -> None:
        """
        Check if model and features are compatible.
        
        Args:
            model: Trained ML model
            feature_df: Feature DataFrame
        
        Raises:
            ModelMismatchError: If incompatible
        """
        # Check n_features_in_ (sklearn standard)
        if hasattr(model, 'n_features_in_'):
            expected_features = model.n_features_in_
            actual_features = feature_df.shape[1]
            
            if expected_features != actual_features:
                logger.critical(
                    f"🚫 MODEL MISMATCH: n_features_in_ mismatch. "
                    f"Model expects: {expected_features}, Got: {actual_features}"
                )
                raise ModelMismatchError(
                    f"Model expects {expected_features} features, "
                    f"but received {actual_features}. "
                    f"Training/inference feature mismatch."
                )
        
        # Check feature_names_in_ (sklearn 1.0+)
        if hasattr(model, 'feature_names_in_'):
            model_features = set(model.feature_names_in_)
            actual_features = set(feature_df.columns)
            
            if model_features != actual_features:
                missing = model_features - actual_features
                extra = actual_features - model_features
                logger.critical(
                    f"🚫 MODEL MISMATCH: feature_names_in_ mismatch. "
                    f"Missing: {missing}, Extra: {extra}"
                )
                raise ModelMismatchError(
                    f"Feature name mismatch. "
                    f"Missing: {missing}, Extra: {extra}"
                )
        
        logger.debug("✅ Model compatibility check passed")
    
    @staticmethod
    def validate_and_prepare(
        feature_df: pd.DataFrame,
        schema: FeatureSchema,
        model: Any,
        market_data_index: Optional[pd.Index] = None
    ) -> pd.DataFrame:
        """
        Full validation and preparation pipeline.
        
        Args:
            feature_df: Raw feature DataFrame
            schema: Feature schema
            model: ML model for compatibility check
            market_data_index: Optional market data index
        
        Returns:
            pd.DataFrame: Validated and prepared features
        """
        # Step 1: Validate features against schema
        validated = FeatureValidator.validate_features(
            feature_df, schema, market_data_index
        )
        
        # Step 2: Check model compatibility
        FeatureValidator.check_model_compatibility(model, validated)
        
        # Step 3: Apply scaling
        prepared = FeatureValidator.apply_scaling(validated, schema)
        
        # Final validation - ensure no NaN after scaling
        if prepared.isna().any().any():
            raise FeatureValidationError("NaN values introduced during scaling")
        
        return prepared


# Convenience function for quick validation
def validate_features(
    feature_df: pd.DataFrame,
    schema: FeatureSchema,
    model: Optional[Any] = None,
    market_data_index: Optional[pd.Index] = None
) -> pd.DataFrame:
    """
    Quick validation function.
    
    Example:
        >>> from backend.feature_validator import validate_features, FeatureSchema
        >>> schema = FeatureSchema(
        ...     expected_features=["rsi", "macd", "volume"],
        ...     expected_count=3
        ... )
        >>> validated = validate_features(features_df, schema, model)
        >>> predictions = model.predict(validated)
    
    Raises:
        FeatureValidationError: If validation fails
        ModelMismatchError: If model incompatible
    """
    return FeatureValidator.validate_and_prepare(
        feature_df, schema, model, market_data_index
    )


# Schema registry for model version management
class FeatureSchemaRegistry:
    """
    Registry for managing feature schemas by model version.
    """
    
    def __init__(self):
        self._schemas: Dict[str, FeatureSchema] = {}
    
    def register(self, model_id: str, schema: FeatureSchema) -> None:
        """Register a schema for a model."""
        self._schemas[model_id] = schema
        logger.info(f"Registered schema for model: {model_id}")
    
    def get(self, model_id: str) -> Optional[FeatureSchema]:
        """Get schema for a model."""
        return self._schemas.get(model_id)
    
    def validate_for_model(
        self,
        model_id: str,
        feature_df: pd.DataFrame,
        model: Any,
        market_data_index: Optional[pd.Index] = None
    ) -> pd.DataFrame:
        """
        Validate features using registered schema for model.
        
        Raises:
            FeatureValidationError: If no schema registered or validation fails
        """
        schema = self.get(model_id)
        if schema is None:
            raise FeatureValidationError(
                f"No feature schema registered for model: {model_id}. "
                f"Cannot validate features without schema."
            )
        
        return FeatureValidator.validate_and_prepare(
            feature_df, schema, model, market_data_index
        )


# Global registry instance
_schema_registry = FeatureSchemaRegistry()


def register_feature_schema(model_id: str, schema: FeatureSchema) -> None:
    """Register a feature schema globally."""
    _schema_registry.register(model_id, schema)


def get_feature_schema(model_id: str) -> Optional[FeatureSchema]:
    """Get a registered feature schema."""
    return _schema_registry.get(model_id)


def validate_for_model(
    model_id: str,
    feature_df: pd.DataFrame,
    model: Any,
    market_data_index: Optional[pd.Index] = None
) -> pd.DataFrame:
    """Validate features for a specific model using registered schema."""
    return _schema_registry.validate_for_model(
        model_id, feature_df, model, market_data_index
    )
