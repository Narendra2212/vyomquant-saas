"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: ml_models.py                                         ║
║                                                                          ║
║  All ML/DL model blocks for the drag-and-drop strategy builder.          ║
║  Pure computation — zero HTTP, WebSocket, or database code here.         ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  ML-1  DL _generate_target returned len=n, not n-1 → shape mismatch     ║
║  ML-2  live_inference received full matrix (1000,N) not single row       ║
║  ML-3  No model versioning — retrain overwrites file bot uses            ║
║  ML-4  Training blocks event loop — must use asyncio.to_thread           ║
║  ML-5  Path traversal via raw user_id in filename                        ║
║  ML-6  No input shape validation before inference                        ║
║  ML-7  DL live_inference no length check on sequence slice               ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import importlib
import importlib.util
import logging
import os
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    import joblib
except ImportError:
    import pickle as joblib

import numpy as np

logger = logging.getLogger("MLModels")

# AVAILABLE_ML_MODELS and AVAILABLE_DL_MODELS are no longer hand-maintained parallel
# lists. They are DERIVED from MODEL_SPECS at the bottom of this module, filtered by a
# real per-library import probe. See "MODEL DESCRIPTOR REGISTRY" below.
#
# Why: the hand-maintained lists were one half of defect SB-04 — `catboost` and
# `autoencoder` were runnable on the backend but unselectable in the UI because a second,
# shorter list existed elsewhere. One source, derived, no drift.

# Import ML safety infrastructure for institutional-grade safety
try:
    from backend_app.core.ml_safety import (DeterministicEnforcer,
                                            DeviceManager,
                                            InferenceTimeoutGuard,
                                            MemoryMonitor, SafeModelLoader,
                                            TrainingIsolator)
    ML_SAFETY_AVAILABLE = True
    logger.info("ML safety infrastructure loaded successfully")
except ImportError:
    ML_SAFETY_AVAILABLE = False
    logger.warning("ML safety infrastructure not available - running in unsafe mode")


# ══════════════════════════════════════════════════════════════════════════
#  SECURITY UTILITY
# ══════════════════════════════════════════════════════════════════════════


def _safe_filename(user_id: str, strategy_name: str) -> str:
    """
    FIX ML-5: Sanitize user-supplied strings before using in file paths.
    Removes all characters that are not alphanumeric, dash, or underscore.
    Prevents path traversal attacks like user_id = '../../../etc/passwd'.
    """
    safe_uid = re.sub(r"[^a-zA-Z0-9\-]", "_", str(user_id))[:64]
    safe_name = re.sub(r"[^a-zA-Z0-9\-]", "_", str(strategy_name))[:64]
    return safe_uid, safe_name


# ══════════════════════════════════════════════════════════════════════════
#  BASE CLASS — TREE MODELS (XGBoost, LightGBM, RandomForest, CatBoost)
# ══════════════════════════════════════════════════════════════════════════


class TreeStrategyBlock(ABC):
    """
    Base for all sklearn-compatible tree models.
    Holds the model in RAM after load_model_to_memory() — zero disk I/O per tick.
    """

    def __init__(self, models_dir: str = "user_strategies/"):
        self.models_dir = models_dir
        self.active_model = None  # Model in RAM after load
        self.active_path = None  # Path this instance is pinned to
        os.makedirs(self.models_dir, exist_ok=True)

    def load_model_to_memory(self, strategy_path: str):
        """
        Called ONCE by BotRunner during startup.
        Pins the model to a specific file version — retraining a new file
        does NOT affect a running bot until it is explicitly restarted.
        FIX ML-3: Store the path so the caller knows which version is live.
        
        SAFETY: Uses SafeModelLoader for checksum validation and corruption detection.
        """
        if not os.path.exists(strategy_path):
            raise FileNotFoundError(f"Model file not found: {strategy_path}")
        
        if ML_SAFETY_AVAILABLE:
            self.active_model = SafeModelLoader.load_model(
                strategy_path,
                validate_integrity=True
            )
            logger.info(f"Model loaded safely into RAM: {strategy_path}")
        else:
            self.active_model = joblib.load(strategy_path)  # nosec: B301
            logger.info(f"Model loaded into RAM: {strategy_path}")
        
        self.active_path = strategy_path

    def live_inference(self, feature_matrix: np.ndarray) -> float:
        """
        Called every tick. Uses only the LAST row of the matrix.
        FIX ML-2: Original passed the full (1000,N) matrix.
                  predict_proba expects (1,N) — now takes feature_matrix[-1:]
        FIX ML-6: Validates shape before calling model.
        
        SAFETY: Enforces deterministic inference, timeout guards, and memory bounds.
        Returns: float probability [0.0, 1.0] — confidence of a BUY signal.
        """
        if self.active_model is None:
            raise RuntimeError(
                "Model not in memory. Call load_model_to_memory() first."
            )

        # SAFETY: Ensure deterministic inference for replay correctness
        if ML_SAFETY_AVAILABLE:
            DeterministicEnforcer.ensure_deterministic()

        # Take only the last row; reshape to (1, n_features)
        single_row = feature_matrix[-1:].reshape(1, -1)  # FIX ML-2

        # Validate feature count matches what the model was trained on
        expected_features = getattr(self.active_model, "n_features_in_", None)
        if expected_features and single_row.shape[1] != expected_features:
            raise ValueError(
                f"Feature mismatch: model expects {expected_features} features, "
                f"got {single_row.shape[1]}."
            )

        if np.any(np.isnan(single_row)):
            logger.warning(
                "NaN in live feature row — indicators still warming up. Returning 0.5."
            )
            return 0.5  # Neutral — do not trade during warmup

        # SAFETY: Validate tensor size
        if ML_SAFETY_AVAILABLE:
            tensor_size_mb = single_row.nbytes / (1024 * 1024)
            try:
                MemoryMonitor.validate_tensor_size(tensor_size_mb)
            except Exception as e:
                logger.warning(f"Tensor size validation failed: {e}")

        # SAFETY: Execute inference with timeout guard
        def _predict():
            return float(self.active_model.predict_proba(single_row)[0][1])
        
        if ML_SAFETY_AVAILABLE:
            try:
                return InferenceTimeoutGuard.execute_with_timeout(_predict, timeout=2.0)
            except Exception as e:
                logger.error(f"ML inference timeout or error: {e}")
                return 0.5  # Neutral on failure
        else:
            return _predict()

    def _generate_target(
        self, 
        close_prices: np.ndarray, 
        horizon: int = 5,
        mode: str = "classification",
        threshold: float = 0.001
    ) -> np.ndarray:
        """
        Generate target for ML training.
        
        Args:
            close_prices: Array of closing prices
            horizon: Number of bars ahead to predict (default: 5)
            mode: "classification" or "regression"
            threshold: Classification threshold for return (e.g., 0.001 = 0.1%)
            
        Returns:
            Target array:
            - classification: 0 (down), 1 (flat), 2 (up) based on threshold
            - regression: raw future returns
            
        Target shape: n-horizon (last 'horizon' bars have no future)
        """
        n = len(close_prices)
        if n <= horizon:
            return np.array([])
        
        # Calculate future returns: (price[t+horizon] - price[t]) / price[t]
        future_returns = (close_prices[horizon:] - close_prices[:-horizon]) / close_prices[:-horizon]
        
        if mode == "regression":
            # Return raw future returns
            logger.info(f"[TARGET] Regression mode: horizon={horizon}, returns range=[{future_returns.min():.4f}, {future_returns.max():.4f}]")
            return future_returns
        
        elif mode == "classification":
            # Convert returns to classes based on threshold
            # 0: down (return < -threshold)
            # 1: flat (|return| <= threshold)
            # 2: up (return > threshold)
            target = np.zeros(len(future_returns), dtype=int)
            target[future_returns > threshold] = 2  # Up
            target[(future_returns >= -threshold) & (future_returns <= threshold)] = 1  # Flat
            # Down stays 0
            
            up_pct = np.mean(target == 2) * 100
            flat_pct = np.mean(target == 1) * 100
            down_pct = np.mean(target == 0) * 100
            logger.info(f"[TARGET] Classification mode: horizon={horizon}, threshold={threshold:.4f}")
            logger.info(f"[TARGET] Class distribution: UP={up_pct:.1f}%, FLAT={flat_pct:.1f}%, DOWN={down_pct:.1f}%")
            
            return target
        
        else:
            raise ValueError(f"Unknown mode: {mode}. Use 'classification' or 'regression'")

    def _prepare_data(
        self,
        master_matrix: np.ndarray,
        indicator_names: list,
        user_selected_indicators: list,
        horizon: int = 5,
        mode: str = "classification",
        threshold: float = 0.001,
    ):
        """
        Slices and cleans the feature matrix for training.
        
        Args:
            master_matrix: Full feature matrix
            indicator_names: List of all indicator names
            user_selected_indicators: Indicators to use for training
            horizon: Number of bars ahead to predict (default: 5)
            mode: "classification" or "regression"
            threshold: Classification threshold for returns
        """
        if len(user_selected_indicators) < 5:
            raise ValueError(
                f"Minimum 5 indicators required for reliable ML signals. "
                f"Got {len(user_selected_indicators)}."
            )
        missing = [n for n in user_selected_indicators if n not in indicator_names]
        if missing:
            raise ValueError(f"Indicators not found in master_matrix: {missing}")

        selected_idx = [indicator_names.index(n) for n in user_selected_indicators]

        # Target generation with horizon
        close_col = master_matrix[:, 0]  # Close is column 0 by convention
        y = self._generate_target(close_col, horizon=horizon, mode=mode, threshold=threshold)
        
        # Slice features: drop last 'horizon' rows (they have no target)
        X = master_matrix[:-horizon, selected_idx]
        
        # Ensure X and y have same length
        min_len = min(len(X), len(y))
        X = X[:min_len]
        y = y[:min_len]

        # FIX: Slice from first fully-warmed-up row to preserve time order
        valid_mask = ~np.isnan(X).any(axis=1)
        if not valid_mask.any():
            raise ValueError("All rows contain NaN — indicators have not warmed up.")
        first_valid = int(np.where(valid_mask)[0][0])

        X_clean = X[first_valid:]
        y_clean = y[first_valid:]

        if len(X_clean) < 100:
            raise ValueError(
                f"Only {len(X_clean)} clean rows after NaN removal. "
                f"Need at least 100. Provide more historical data."
            )

        logger.info(f"[PREPARE] X shape: {X_clean.shape}, y shape: {y_clean.shape}, horizon: {horizon}, mode: {mode}")
        
        return X_clean, y_clean

    @abstractmethod
    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        """Train and save. Returns the absolute path to the saved model file."""


# ══════════════════════════════════════════════════════════════════════════
#  TREE MODEL IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════


class XGBoostStrategyBlock(TreeStrategyBlock):

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        import xgboost as xgb

        X_clean, y_clean = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)  # FIX ML-5

        # SAFETY: Use TrainingIsolator for process isolation
        def _train():
            model = xgb.XGBClassifier(
                n_estimators=150,
                learning_rate=0.05,
                max_depth=4,
                subsample=0.8,
                random_state=42,
                eval_metric="logloss",
                n_jobs=2,  # FIX: cap CPU usage for multi-tenant server
                tree_method="hist",  # Faster on large datasets
            )
            model.fit(X_clean, y_clean)
            return model

        if ML_SAFETY_AVAILABLE:
            try:
                model = TrainingIsolator.run_isolated_training(_train)
            except Exception as e:
                logger.error(f"Isolated training failed, falling back to current process: {e}")
                model = _train()
        else:
            model = _train()

        # FIX ML-3: Include a UUID version tag so old bots keep their pinned path
        version = uuid.uuid4().hex[:8]
        filename = f"user_{safe_uid}_xgb_{safe_name}_{version}.pkl"
        path = os.path.join(self.models_dir, filename)
        joblib.dump(model, path)
        logger.info(f"XGBoost model saved: {path}")
        return path


class LightGBMStrategyBlock(TreeStrategyBlock):

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        import lightgbm as lgb

        X_clean, y_clean = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)

        model = lgb.LGBMClassifier(
            n_estimators=150,
            learning_rate=0.05,
            max_depth=5,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            n_jobs=2,
            verbose=-1,
        )
        model.fit(X_clean, y_clean)

        version = uuid.uuid4().hex[:8]
        filename = f"user_{safe_uid}_lgb_{safe_name}_{version}.pkl"
        path = os.path.join(self.models_dir, filename)
        joblib.dump(model, path)
        logger.info(f"LightGBM model saved: {path}")
        return path


class RandomForestStrategyBlock(TreeStrategyBlock):

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        from sklearn.ensemble import RandomForestClassifier

        X_clean, y_clean = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)

        model = RandomForestClassifier(
            n_estimators=150,
            max_depth=6,
            min_samples_split=5,
            max_features="sqrt",
            random_state=42,
            n_jobs=2,
        )
        model.fit(X_clean, y_clean)

        version = uuid.uuid4().hex[:8]
        filename = f"user_{safe_uid}_rf_{safe_name}_{version}.pkl"
        path = os.path.join(self.models_dir, filename)
        joblib.dump(model, path)
        logger.info(f"RandomForest model saved: {path}")
        return path


class CatBoostStrategyBlock(TreeStrategyBlock):

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        from catboost import CatBoostClassifier

        X_clean, y_clean = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)

        model = CatBoostClassifier(
            iterations=150,
            learning_rate=0.05,
            depth=5,
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
            thread_count=2,
        )
        model.fit(X_clean, y_clean)

        version = uuid.uuid4().hex[:8]
        filename = f"user_{safe_uid}_cat_{safe_name}_{version}.pkl"
        path = os.path.join(self.models_dir, filename)
        joblib.dump(model, path)
        logger.info(f"CatBoost model saved: {path}")
        return path


# ══════════════════════════════════════════════════════════════════════════
#  BASE CLASS — DEEP LEARNING SEQUENCE MODELS (LSTM, GRU, Transformer)
# ══════════════════════════════════════════════════════════════════════════


class DeepLearningStrategyBlock(ABC):
    """
    Base for all TensorFlow Keras sequence models.
    Requires StandardScaler to be saved alongside the model.
    """

    def __init__(self, models_dir: str = "user_strategies/", time_steps: int = 60):
        self.models_dir = models_dir
        self.time_steps = time_steps
        self.active_model = None
        self.active_scaler = None
        self.active_path = None
        os.makedirs(self.models_dir, exist_ok=True)

    def load_model_to_memory(self, strategy_base_name: str):
        """
        Loads model + scaler from disk into RAM.
        FIX ML-3: Pinned to the specific base_name version at startup.
        
        SAFETY: Uses SafeModelLoader for checksum validation and DeviceManager for GPU/CPU fallback.
        """
        import tensorflow as tf

        model_path = os.path.join(self.models_dir, f"{strategy_base_name}.keras")
        scaler_path = os.path.join(self.models_dir, f"{strategy_base_name}_scaler.pkl")

        if not os.path.exists(model_path) or not os.path.exists(scaler_path):
            raise FileNotFoundError(
                f"Model or scaler file missing for '{strategy_base_name}'. "
                "Train the model first."
            )
        
        # SAFETY: Configure GPU/CPU device selection
        if ML_SAFETY_AVAILABLE:
            DeviceManager.configure_tensorflow_gpu()
        
        # SAFETY: Load model with device context
        if ML_SAFETY_AVAILABLE:
            try:
                with tf.device(DeviceManager.get_tensorflow_device()):
                    self.active_model = tf.keras.models.load_model(model_path)
                logger.info(f"DL model loaded on {DeviceManager.get_device().value}: {strategy_base_name}")
            except Exception as e:
                logger.error(f"Failed to load model on selected device: {e}")
                # Fallback to CPU
                with tf.device("/CPU:0"):
                    self.active_model = tf.keras.models.load_model(model_path)
                logger.warning(f"DL model loaded on CPU fallback: {strategy_base_name}")
        else:
            self.active_model = tf.keras.models.load_model(model_path)
            logger.info(f"DL model loaded into RAM (unsafe mode): {strategy_base_name}")
        
        self.active_scaler = joblib.load(scaler_path)  # nosec: B301
        self.active_path = strategy_base_name

    def live_inference(self, feature_matrix: np.ndarray) -> float:
        """
        Runs inference on the last `time_steps` rows of the feature matrix.
        FIX ML-2: Takes only the required sequence window, not the full matrix.
        FIX ML-7: Validates that the matrix has enough rows for the sequence.
        
        SAFETY: Enforces deterministic inference, timeout guards, and memory bounds.
        Returns: float confidence [0.0, 1.0]
        """
        if self.active_model is None or self.active_scaler is None:
            raise RuntimeError("Model not loaded. Call load_model_to_memory() first.")

        # SAFETY: Ensure deterministic inference for replay correctness
        if ML_SAFETY_AVAILABLE:
            DeterministicEnforcer.ensure_deterministic()

        # FIX ML-7: Validate sequence length
        if len(feature_matrix) < self.time_steps:
            logger.warning(
                f"Insufficient history: need {self.time_steps} rows, "
                f"have {len(feature_matrix)}. Returning 0.5 (neutral)."
            )
            return 0.5

        # Extract the last `time_steps` rows (the required input window)
        sequence = feature_matrix[-self.time_steps :]  # FIX ML-2

        if np.any(np.isnan(sequence)):
            logger.warning("NaN in live sequence — warmup incomplete. Returning 0.5.")
            return 0.5

        # Scale and reshape to (1, time_steps, n_features)
        scaled = self.active_scaler.transform(sequence)
        x_3d = scaled.reshape(1, self.time_steps, scaled.shape[1])

        # SAFETY: Validate tensor size
        if ML_SAFETY_AVAILABLE:
            tensor_size_mb = x_3d.nbytes / (1024 * 1024)
            try:
                MemoryMonitor.validate_tensor_size(tensor_size_mb)
            except Exception as e:
                logger.warning(f"Tensor size validation failed: {e}")

        # SAFETY: Execute inference with timeout guard and device context
        import tensorflow as tf
        
        def _predict():
            with tf.device(DeviceManager.get_tensorflow_device() if ML_SAFETY_AVAILABLE else "/CPU:0"):
                return float(self.active_model.predict(x_3d, verbose=0)[0][0])
        
        if ML_SAFETY_AVAILABLE:
            try:
                return InferenceTimeoutGuard.execute_with_timeout(_predict, timeout=2.0)
            except Exception as e:
                logger.error(f"DL inference timeout or error: {e}")
                return 0.5  # Neutral on failure
        else:
            return _predict()

    def _generate_target(
        self, 
        close_prices: np.ndarray,
        horizon: int = 5,
        mode: str = "classification",
        threshold: float = 0.001
    ) -> np.ndarray:
        """
        Generate target for deep learning models.
        
        Args:
            close_prices: Array of closing prices
            horizon: Number of bars ahead to predict (default: 5)
            mode: "classification" or "regression"
            threshold: Classification threshold for returns
            
        Returns:
            Target array (same as TreeStrategyBlock for consistency)
        """
        n = len(close_prices)
        if n <= horizon:
            return np.array([])
        
        # Calculate future returns: (price[t+horizon] - price[t]) / price[t]
        future_returns = (close_prices[horizon:] - close_prices[:-horizon]) / close_prices[:-horizon]
        
        if mode == "regression":
            logger.info(f"[DL_TARGET] Regression mode: horizon={horizon}, returns range=[{future_returns.min():.4f}, {future_returns.max():.4f}]")
            return future_returns
        
        elif mode == "classification":
            # Convert returns to classes: 0=down, 1=flat, 2=up
            target = np.zeros(len(future_returns), dtype=int)
            target[future_returns > threshold] = 2
            target[(future_returns >= -threshold) & (future_returns <= threshold)] = 1
            
            up_pct = np.mean(target == 2) * 100
            flat_pct = np.mean(target == 1) * 100
            down_pct = np.mean(target == 0) * 100
            logger.info(f"[DL_TARGET] Classification: UP={up_pct:.1f}%, FLAT={flat_pct:.1f}%, DOWN={down_pct:.1f}%")
            
            return target
        
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def _create_sequences(self, features: np.ndarray, target: np.ndarray):
        """Creates overlapping (X, y) sequence pairs for LSTM/GRU training."""
        X_seq, y_seq = [], []
        # Need features[i:i+time_steps] with target at i+time_steps
        for i in range(len(features) - self.time_steps):
            X_seq.append(features[i : i + self.time_steps])
            y_seq.append(target[i + self.time_steps - 1])  # aligned target
        return np.array(X_seq), np.array(y_seq)

    def _prepare_data(
        self, 
        master_matrix, 
        indicator_names, 
        user_selected_indicators,
        horizon: int = 5,
        mode: str = "classification",
        threshold: float = 0.001
    ):
        from sklearn.preprocessing import StandardScaler

        if len(user_selected_indicators) < 5:
            raise ValueError(
                f"Minimum 5 indicators required. Got {len(user_selected_indicators)}."
            )

        selected_idx = [indicator_names.index(n) for n in user_selected_indicators]
        X_full = master_matrix[:, selected_idx]  # All rows, selected cols
        close_col = master_matrix[:, 0]

        # Generate target with horizon
        y_full = self._generate_target(close_col, horizon=horizon, mode=mode, threshold=threshold)
        
        # Drop last 'horizon' rows to match target length
        X_full = X_full[:-horizon]
        
        # Ensure same length
        min_len = min(len(X_full), len(y_full))
        X_full = X_full[:min_len]
        y_full = y_full[:min_len]

        # Strip NaN warmup rows
        valid_mask = ~np.isnan(X_full).any(axis=1)
        first_valid = (
            int(np.where(valid_mask)[0][0]) if valid_mask.any() else len(X_full)
        )
        X_clean = X_full[first_valid:]
        y_clean = y_full[first_valid:]

        if len(X_clean) < self.time_steps + 50:
            raise ValueError(
                f"Need at least {self.time_steps + 50} clean rows. "
                f"Got {len(X_clean)}. Provide more historical data."
            )

        logger.info(f"[DL_PREPARE] X shape: {X_clean.shape}, y shape: {y_clean.shape}, sequences will be created with time_steps={self.time_steps}")
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_clean)
        X_3d, y_3d = self._create_sequences(X_scaled, y_clean)
        
        logger.info(f"[DL_PREPARE] Final X_3d shape: {X_3d.shape}, y_3d shape: {y_3d.shape}")
        
        return X_3d, y_3d, scaler

    @abstractmethod
    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        """Train and save. Returns base_name string for load_model_to_memory()."""


# ══════════════════════════════════════════════════════════════════════════
#  DEEP LEARNING IMPLEMENTATIONS
# ══════════════════════════════════════════════════════════════════════════


class LSTMStrategyBlock(DeepLearningStrategyBlock):
    """
    Stacked LSTM for sequence pattern recognition.
    NOTE: Training must be called via asyncio.to_thread() from FastAPI
          to prevent blocking the event loop. (FIX ML-4)
    """

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        from tensorflow.keras.layers import LSTM, Dense, Dropout, Input
        from tensorflow.keras.models import Sequential

        X_3d, y_3d, scaler = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)

        model = Sequential(
            [
                Input(shape=(self.time_steps, X_3d.shape[2])),
                LSTM(64, return_sequences=True),
                Dropout(0.2),
                LSTM(32, return_sequences=False),
                Dropout(0.2),
                Dense(16, activation="relu"),
                Dense(1, activation="sigmoid"),
            ]
        )
        model.compile(
            optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"]
        )

        # FIX ML-4 note: call this from asyncio.to_thread() in the API layer
        model.fit(X_3d, y_3d, epochs=10, batch_size=64, validation_split=0.1, verbose=0)

        version = uuid.uuid4().hex[:8]
        base_name = f"user_{safe_uid}_lstm_{safe_name}_{version}"
        model.save(os.path.join(self.models_dir, f"{base_name}.keras"))
        joblib.dump(scaler, os.path.join(self.models_dir, f"{base_name}_scaler.pkl"))
        logger.info(f"LSTM model saved: {base_name}")
        return base_name


class GRUStrategyBlock(DeepLearningStrategyBlock):

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        from tensorflow.keras.layers import GRU, Dense, Dropout, Input
        from tensorflow.keras.models import Sequential

        X_3d, y_3d, scaler = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)

        model = Sequential(
            [
                Input(shape=(self.time_steps, X_3d.shape[2])),
                GRU(64, return_sequences=True),
                Dropout(0.2),
                GRU(32, return_sequences=False),
                Dropout(0.2),
                Dense(16, activation="relu"),
                Dense(1, activation="sigmoid"),
            ]
        )
        model.compile(
            optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"]
        )
        model.fit(X_3d, y_3d, epochs=10, batch_size=64, validation_split=0.1, verbose=0)

        version = uuid.uuid4().hex[:8]
        base_name = f"user_{safe_uid}_gru_{safe_name}_{version}"
        model.save(os.path.join(self.models_dir, f"{base_name}.keras"))
        joblib.dump(scaler, os.path.join(self.models_dir, f"{base_name}_scaler.pkl"))
        logger.info(f"GRU model saved: {base_name}")
        return base_name


class TransformerStrategyBlock(DeepLearningStrategyBlock):

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        from tensorflow.keras.layers import (Dense, Dropout,
                                             GlobalAveragePooling1D, Input,
                                             LayerNormalization,
                                             MultiHeadAttention)
        from tensorflow.keras.models import Model

        X_3d, y_3d, scaler = self._prepare_data(
            master_matrix, indicator_names, user_selected_indicators
        )
        n_features = X_3d.shape[2]
        safe_uid, safe_name = _safe_filename(user_id, strategy_name)

        inputs = Input(shape=(self.time_steps, n_features))
        x = MultiHeadAttention(num_heads=4, key_dim=16)(inputs, inputs)
        x = LayerNormalization()(x)
        x = GlobalAveragePooling1D()(x)
        x = Dense(32, activation="relu")(x)
        x = Dropout(0.2)(x)
        outputs = Dense(1, activation="sigmoid")(x)

        model = Model(inputs=inputs, outputs=outputs)
        model.compile(
            optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"]
        )
        model.fit(X_3d, y_3d, epochs=10, batch_size=64, validation_split=0.1, verbose=0)

        version = uuid.uuid4().hex[:8]
        base_name = f"user_{safe_uid}_transformer_{safe_name}_{version}"
        model.save(os.path.join(self.models_dir, f"{base_name}.keras"))
        joblib.dump(scaler, os.path.join(self.models_dir, f"{base_name}_scaler.pkl"))
        logger.info(f"Transformer model saved: {base_name}")
        return base_name


# ══════════════════════════════════════════════════════════════════════════
#  AUTOENCODER — Unsupervised Anomaly Detection
# ══════════════════════════════════════════════════════════════════════════


class AutoencoderStrategyBlock:
    """
    Detects market anomalies (flash crashes, manipulation, high volatility).
    Returns a danger_score [0.0, 1.0] — higher means more anomalous.
    """

    def __init__(self, models_dir: str = "user_strategies/"):
        self.models_dir = models_dir
        self.active_model = None
        self.active_scaler = None
        self.active_threshold = None
        os.makedirs(self.models_dir, exist_ok=True)

    def load_model_to_memory(self, strategy_base_name: str):
        import tensorflow as tf

        model_path = os.path.join(self.models_dir, f"{strategy_base_name}.keras")
        scaler_path = os.path.join(self.models_dir, f"{strategy_base_name}_scaler.pkl")
        threshold_path = os.path.join(
            self.models_dir, f"{strategy_base_name}_threshold.pkl"
        )

        for p in [model_path, scaler_path, threshold_path]:
            if not os.path.exists(p):
                raise FileNotFoundError(f"Missing file: {p}")

        self.active_model = tf.keras.models.load_model(model_path)
        self.active_scaler = joblib.load(scaler_path)  # nosec: B301
        self.active_threshold = joblib.load(threshold_path)  # nosec: B301
        logger.info(
            f"Autoencoder loaded: {strategy_base_name} (threshold={self.active_threshold:.6f})"
        )

    def live_inference(self, live_matrix_slice: np.ndarray) -> float:
        """
        FIX ML-2: Takes only the last row for 2D autoencoders.
        Returns danger_score [0.0, 1.0].
        """
        if self.active_model is None:
            raise RuntimeError("Model not loaded.")

        single_row = live_matrix_slice[-1:]  # FIX ML-2: last row only

        if np.any(np.isnan(single_row)):
            return 0.0  # No anomaly detected during warmup

        scaled = self.active_scaler.transform(single_row)
        reconstruction = self.active_model.predict(scaled, verbose=0)

        import tensorflow as tf

        error = float(tf.keras.losses.mse(scaled, reconstruction).numpy()[0])
        danger_score = min(error / max(self.active_threshold, 1e-9), 1.0)
        return danger_score

    def train_custom_strategy(
        self,
        user_id,
        strategy_name,
        master_matrix,
        indicator_names,
        user_selected_indicators,
    ) -> str:
        import tensorflow as tf
        from sklearn.preprocessing import StandardScaler
        from tensorflow.keras.layers import Dense, Dropout, Input
        from tensorflow.keras.models import Model

        safe_uid, safe_name = _safe_filename(user_id, strategy_name)
        selected_idx = [indicator_names.index(n) for n in user_selected_indicators]
        X_full = master_matrix[:, selected_idx]

        valid_mask = ~np.isnan(X_full).any(axis=1)
        X_clean = X_full[valid_mask]

        if len(X_clean) < 100:
            raise ValueError("Insufficient clean data for autoencoder training.")

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_clean)

        input_dim = X_scaled.shape[1]
        bottleneck = max(2, input_dim // 4)
        hidden = max(4, input_dim // 2)

        inputs = Input(shape=(input_dim,))
        encoded = Dropout(0.2)(Dense(hidden, activation="relu")(inputs))
        neck = Dense(bottleneck, activation="relu")(encoded)
        decoded = Dense(hidden, activation="relu")(neck)
        outputs = Dense(input_dim, activation="linear")(decoded)

        model = Model(inputs=inputs, outputs=outputs)
        model.compile(optimizer="adam", loss="mse")
        model.fit(
            X_scaled,
            X_scaled,
            epochs=15,
            batch_size=64,
            validation_split=0.1,
            verbose=0,
        )

        # Set threshold at the 99th percentile of training reconstruction error
        reconstructions = model.predict(X_scaled, verbose=0)
        train_errors = tf.keras.losses.mse(X_scaled, reconstructions).numpy()
        threshold = float(np.percentile(train_errors, 99))

        version = uuid.uuid4().hex[:8]
        base_name = f"user_{safe_uid}_ae_{safe_name}_{version}"
        model.save(os.path.join(self.models_dir, f"{base_name}.keras"))
        joblib.dump(scaler, os.path.join(self.models_dir, f"{base_name}_scaler.pkl"))
        joblib.dump(
            threshold, os.path.join(self.models_dir, f"{base_name}_threshold.pkl")
        )
        logger.info(f"Autoencoder saved: {base_name} (threshold={threshold:.6f})")
        return base_name


# ══════════════════════════════════════════════════════════════════════════
#  MODEL FACTORY — resolves model type from filename prefix
# ══════════════════════════════════════════════════════════════════════════


def create_ml_block(model_path: str, models_dir: str = "user_strategies/"):
    """
    Returns the correct model block instance based on the filename.
    Called by BotRunner._initialize_ml_block() to avoid hardcoded isinstance checks.

    FIX: Original had 3 hardcoded elif branches with no fallback for GRU, RF,
    LightGBM, CatBoost, Autoencoder.
    """
    if not model_path:
        return None

    name = os.path.basename(model_path).lower()

    if "_xgb_" in name:
        return XGBoostStrategyBlock(models_dir)
    if "_lgb_" in name:
        return LightGBMStrategyBlock(models_dir)
    if "_rf_" in name:
        return RandomForestStrategyBlock(models_dir)
    if "_cat_" in name:
        return CatBoostStrategyBlock(models_dir)
    if "_lstm_" in name:
        return LSTMStrategyBlock(models_dir)
    if "_gru_" in name:
        return GRUStrategyBlock(models_dir)
    if "_transformer_" in name:
        return TransformerStrategyBlock(models_dir)
    if "_ae_" in name:
        return AutoencoderStrategyBlock(models_dir)

    logger.warning(f"Unknown model type in filename '{name}'. Defaulting to XGBoost.")
    return XGBoostStrategyBlock(models_dir)


XGBoostBlock = XGBoostStrategyBlock
LightGBMBlock = LightGBMStrategyBlock
RandomForestBlock = RandomForestStrategyBlock
CatBoostBlock = CatBoostStrategyBlock
LSTMBlock = LSTMStrategyBlock
GRUBlock = GRUStrategyBlock


# ══════════════════════════════════════════════════════════════════════════
#  MODEL DESCRIPTOR REGISTRY
#
#  Machine-readable descriptors for every ML/DL block above, so the block
#  registry (strategy_dag/registry.py) can never advertise a model it cannot
#  run, and never omit one it can.
#
#  Values come from `design.md § ML/DL model registry`. Nothing here executes
#  a model, touches the network or the database — this section is pure data
#  plus a safe import probe.
#
#  Closes half of defect SB-04: `catboost` and `autoencoder` are runnable on
#  the backend but were unselectable in the UI, because the palette was fed
#  by a shorter hand-maintained list. AVAILABLE_ML_MODELS / AVAILABLE_DL_MODELS
#  are now derived from MODEL_SPECS, so the two can no longer disagree.
# ══════════════════════════════════════════════════════════════════════════


# ── Vocabularies owned by this module ────────────────────────────────────


class ModelFamily(str, Enum):
    """How a model consumes its training rows. Drives the minimum-data gate."""

    TREE = "TREE"
    SEQUENCE = "SEQUENCE"
    AUTOENCODER = "AUTOENCODER"


class SerializationMode(str, Enum):
    """Artifact format a trained model is persisted in."""

    JOBLIB = "JOBLIB"
    KERAS = "KERAS"
    TORCH = "TORCH"


class EpochUnit(str, Enum):
    """
    What `recommended_epochs` / `max_safe_epochs` actually count.

    The design table mixes units on purpose: boosting rounds, CatBoost
    iterations, forest estimators and gradient-descent epochs are not the same
    thing, and a cap message that says "epochs" for a random forest is a lie.
    """

    ROUNDS = "rounds"
    ITERATIONS = "iterations"
    ESTIMATORS = "estimators"
    EPOCHS = "epochs"


# Port type strings are the *values* of the canonical ``PortType`` enum in
# ``strategy_dag/schema.py``; they are kept as plain strings here so this module
# stays dependency-free and importable on its own, matching the convention in
# ``indicators_backend.py`` and ``feature_engineering.py``. A spelling that
# drifts from the enum fails loudly in ``ModelPort.__post_init__`` and again at
# ``PortType(value)`` during registry assembly.
PORT_FEATURE_MATRIX = "FEATURE_MATRIX"
PORT_PREDICTION = "PREDICTION"
PORT_SCALAR_SERIES = "SCALAR_SERIES"
PORT_BOOLEAN_SERIES = "BOOLEAN_SERIES"

#: Canonical port-type vocabulary (values of ``strategy_dag.schema.PortType``).
CANONICAL_PORT_TYPES = frozenset(
    {
        "OHLCV_FRAME",
        "PRICE_SERIES",
        "SCALAR_SERIES",
        "BOOLEAN_SERIES",
        "FEATURE_MATRIX",
        "PREDICTION",
        "SIGNAL",
        "TRADE_INTENT",
        "SCALAR",
    }
)

# Param types are the values of the canonical ParamSpec type vocabulary.
PARAM_INTEGER = "INTEGER"
PARAM_NUMBER = "NUMBER"
PARAM_SELECT = "SELECT"
PARAM_BOOLEAN = "BOOLEAN"

#: Canonical param-type vocabulary (matches ``feature_engineering.ParamType``).
CANONICAL_PARAM_TYPES = frozenset(
    {
        "NUMBER",
        "INTEGER",
        "TEXT",
        "SELECT",
        "MULTISELECT",
        "BOOLEAN",
        "DATE",
        "SYMBOL",
        "TIMEFRAME",
    }
)

# Platform floor from `design.md § Minimum-data gate`: every model needs at
# least this many usable feature columns, on top of its own declared minimum.
PLATFORM_MIN_FEATURE_COLUMNS = 5

MODEL_SPEC_VERSION = "1.0.0"


# ── Descriptor structures ────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelPort:
    """One input or output port of a model block."""

    name: str
    type: str
    required: bool = True
    variadic: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in CANONICAL_PORT_TYPES:
            raise ValueError(
                f"Port '{self.name}' declares unknown port type '{self.type}'"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "port": self.name,
            "type": self.type,
            "required": self.required,
            "variadic": self.variadic,
            "description": self.description,
        }


@dataclass(frozen=True)
class ModelParamSpec:
    """
    Declarative hyperparameter contract. The UI form generator and the backend
    validator both read this, so a form cannot offer a value the backend rejects.
    """

    key: str
    label: str
    type: str
    required: bool = False
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: Optional[Tuple[Any, ...]] = None
    unit: Optional[str] = None
    example: Any = None
    help: str = ""
    depends_on: Tuple[str, ...] = ()
    affects_warmup: bool = False

    def __post_init__(self) -> None:
        if self.type not in CANONICAL_PARAM_TYPES:
            raise ValueError(
                f"Param '{self.key}' declares unknown param type '{self.type}'"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "options": list(self.options) if self.options is not None else None,
            "unit": self.unit,
            "example": self.example,
            "help": self.help,
            "depends_on": list(self.depends_on),
            "affects_warmup": self.affects_warmup,
        }


@dataclass(frozen=True)
class ValidationRequirements:
    """
    Split geometry a model needs before training is admissible.

    `embargo_bars` here is a per-model floor. Phase 6 raises it to at least the
    longest feature lookback plus the label horizon, measured from the compiled
    plan; it never lowers it.
    """

    val_fraction: float
    test_fraction: float
    embargo_bars: int
    metric: str

    def __post_init__(self) -> None:
        if not 0.0 < self.val_fraction < 1.0:
            raise ValueError(f"val_fraction out of range: {self.val_fraction}")
        if not 0.0 < self.test_fraction < 1.0:
            raise ValueError(f"test_fraction out of range: {self.test_fraction}")
        if self.val_fraction + self.test_fraction >= 1.0:
            raise ValueError(
                "val_fraction + test_fraction must leave a non-empty train split: "
                f"{self.val_fraction} + {self.test_fraction}"
            )
        if self.embargo_bars < 0:
            raise ValueError(f"embargo_bars must be >= 0: {self.embargo_bars}")

    @property
    def reserved_fraction(self) -> float:
        return self.val_fraction + self.test_fraction

    def to_dict(self) -> Dict[str, Any]:
        return {
            "val_fraction": self.val_fraction,
            "test_fraction": self.test_fraction,
            "embargo_bars": self.embargo_bars,
            "metric": self.metric,
        }


@dataclass(frozen=True)
class ModelSpec:
    """
    Everything the registry, the minimum-data gate and the training caps need to
    know about one model block, without importing or running the model.
    """

    block_id: str
    display_name: str
    model_family: ModelFamily
    inputs: Tuple[ModelPort, ...]
    outputs: Tuple[ModelPort, ...]
    min_feature_columns: int
    min_training_rows: int
    sequence_length: Optional[int]
    hyperparameters: Tuple[ModelParamSpec, ...]
    recommended_epochs: int
    max_safe_epochs: int
    default_batch_size: int
    validation_requirements: ValidationRequirements
    can_train: bool
    can_predict: bool
    serialization: SerializationMode
    backend_available: bool
    version: str
    # Supporting fields — not part of the design's minimum struct, but needed so
    # the registry can resolve a runtime and so cap messages state honest units.
    epoch_unit: EpochUnit = EpochUnit.EPOCHS
    runtime_ref: str = ""
    required_modules: Tuple[str, ...] = ()
    description: str = ""

    @property
    def is_sequence(self) -> bool:
        return self.model_family is ModelFamily.SEQUENCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "display_name": self.display_name,
            "model_family": self.model_family.value,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "min_feature_columns": self.min_feature_columns,
            "min_training_rows": self.min_training_rows,
            "sequence_length": self.sequence_length,
            "hyperparameters": [p.to_dict() for p in self.hyperparameters],
            "recommended_epochs": self.recommended_epochs,
            "max_safe_epochs": self.max_safe_epochs,
            "epoch_unit": self.epoch_unit.value,
            "default_batch_size": self.default_batch_size,
            "validation_requirements": self.validation_requirements.to_dict(),
            "can_train": self.can_train,
            "can_predict": self.can_predict,
            "serialization": self.serialization.value,
            "backend_available": self.backend_available,
            "version": self.version,
            "runtime_ref": self.runtime_ref,
            "required_modules": list(self.required_modules),
            "description": self.description,
        }


# ── Import probe ─────────────────────────────────────────────────────────
#
# `backend_available` is measured, never asserted. If catboost or the DL
# framework cannot be resolved in the running image, the block is omitted from
# the derived lists (and, in Phase 1.7, from the registry) instead of being
# offered and then failing at train time.
#
# Default probe is spec resolution (importlib.util.find_spec): it answers "is
# this library present in this image" without paying the multi-second cost of
# executing tensorflow's __init__ at module import. Set
# ML_MODELS_DEEP_IMPORT_PROBE=1 to force a full `import_module` instead, which
# additionally catches a present-but-broken install (bad DLL, ABI mismatch).
#
# Either way the probe cannot raise: a failed probe is False, never an exception.

_DEEP_IMPORT_PROBE = os.getenv("ML_MODELS_DEEP_IMPORT_PROBE", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_probe_cache: Dict[Tuple[str, bool], bool] = {}


def probe_module(module_name: str, deep: Optional[bool] = None) -> bool:
    """
    Return True when `module_name` is importable in this environment.

    Never raises. Results are cached per (module, depth) so registry assembly
    and repeated gate evaluations do not re-pay the cost.
    """
    use_deep = _DEEP_IMPORT_PROBE if deep is None else deep
    cache_key = (module_name, use_deep)
    if cache_key in _probe_cache:
        return _probe_cache[cache_key]

    available = False
    try:
        if use_deep:
            importlib.import_module(module_name)
            available = True
        else:
            available = importlib.util.find_spec(module_name) is not None
    except Exception as exc:  # noqa: BLE001 - a broken library must not break import
        # ImportError, ValueError (namespace edge cases), and anything a
        # third-party __init__ raises all mean the same thing here: unusable.
        logger.debug("Model backend probe failed for %r: %s", module_name, exc)
        available = False

    _probe_cache[cache_key] = available
    return available


def probe_backends(deep: Optional[bool] = None) -> Dict[str, bool]:
    """Probe every library any model block depends on. Diagnostics and tests."""
    modules = sorted({m for spec in MODEL_SPECS.values() for m in spec.required_modules})
    return {m: probe_module(m, deep=deep) for m in modules}


def _backend_available_for(required_modules: Tuple[str, ...], deep: Optional[bool] = None) -> bool:
    return all(probe_module(m, deep=deep) for m in required_modules)


# ── Shared port shapes ───────────────────────────────────────────────────


def _supervised_ports() -> Tuple[Tuple[ModelPort, ...], Tuple[ModelPort, ...]]:
    inputs = (
        ModelPort(
            name="features",
            type=PORT_FEATURE_MATRIX,
            required=True,
            description="Aligned feature matrix with column names and warmup offset.",
        ),
    )
    outputs = (
        ModelPort(
            name="prediction",
            type=PORT_PREDICTION,
            required=False,
            description="Per-bar model output.",
        ),
        ModelPort(
            name="confidence",
            type=PORT_SCALAR_SERIES,
            required=False,
            description="Per-bar confidence in [0, 1].",
        ),
    )
    return inputs, outputs


def _anomaly_ports() -> Tuple[Tuple[ModelPort, ...], Tuple[ModelPort, ...]]:
    inputs = (
        ModelPort(
            name="features",
            type=PORT_FEATURE_MATRIX,
            required=True,
            description="Aligned feature matrix to reconstruct.",
        ),
    )
    outputs = (
        ModelPort(
            name="anomaly_score",
            type=PORT_SCALAR_SERIES,
            required=False,
            description="Reconstruction error scaled against the trained threshold, in [0, 1].",
        ),
        ModelPort(
            name="is_anomaly",
            type=PORT_BOOLEAN_SERIES,
            required=False,
            description="True when the anomaly score exceeds the trained threshold.",
        ),
    )
    return inputs, outputs


# ── Shared hyperparameter fragments ──────────────────────────────────────


def _p_learning_rate(default: float = 0.05) -> ModelParamSpec:
    return ModelParamSpec(
        key="learning_rate",
        label="Learning rate",
        type=PARAM_NUMBER,
        default=default,
        min=0.0001,
        max=0.5,
        step=0.001,
        example=default,
        help="Smaller values train more slowly but generalise better.",
    )


def _p_dropout(default: float = 0.2) -> ModelParamSpec:
    return ModelParamSpec(
        key="dropout",
        label="Dropout",
        type=PARAM_NUMBER,
        default=default,
        min=0.0,
        max=0.7,
        step=0.05,
        example=default,
        help="Fraction of units dropped per step to reduce overfitting.",
    )


def _p_batch_size(default: int = 64) -> ModelParamSpec:
    return ModelParamSpec(
        key="batch_size",
        label="Batch size",
        type=PARAM_INTEGER,
        default=default,
        min=8,
        max=1024,
        step=8,
        unit="rows",
        example=default,
        help="Rows per gradient step.",
    )


def _p_epochs(default: int, maximum: int) -> ModelParamSpec:
    return ModelParamSpec(
        key="epochs",
        label="Epochs",
        type=PARAM_INTEGER,
        default=default,
        min=1,
        max=maximum,
        step=1,
        unit="epochs",
        example=default,
        help=(
            "Full passes over the training split. The backend caps this at "
            f"{maximum}; a higher plan tier cannot exceed that."
        ),
    )


def _p_sequence_length(default: int) -> ModelParamSpec:
    return ModelParamSpec(
        key="sequence_length",
        label="Sequence length",
        type=PARAM_INTEGER,
        default=default,
        min=10,
        max=500,
        step=1,
        unit="bars",
        example=default,
        help="Bars per training sample. Every sample consumes this many rows.",
        affects_warmup=True,
    )


# ── The eight model specs (design.md § ML/DL model registry) ──────────────


def _build_model_specs() -> Dict[str, ModelSpec]:
    tree_in, tree_out = _supervised_ports()
    seq_in, seq_out = _supervised_ports()
    ae_in, ae_out = _anomaly_ports()

    tree_validation = ValidationRequirements(
        val_fraction=0.15, test_fraction=0.15, embargo_bars=10, metric="f1_macro"
    )

    specs: List[ModelSpec] = [
        ModelSpec(
            block_id="xgboost",
            display_name="XGBoost",
            model_family=ModelFamily.TREE,
            inputs=tree_in,
            outputs=tree_out,
            min_feature_columns=5,
            min_training_rows=2000,
            sequence_length=None,
            hyperparameters=(
                ModelParamSpec(
                    key="n_estimators",
                    label="Boosting rounds",
                    type=PARAM_INTEGER,
                    default=200,
                    min=10,
                    max=2000,
                    step=10,
                    unit="rounds",
                    example=200,
                    help="Number of boosting rounds.",
                ),
                _p_learning_rate(0.05),
                ModelParamSpec(
                    key="max_depth",
                    label="Max depth",
                    type=PARAM_INTEGER,
                    default=4,
                    min=2,
                    max=16,
                    step=1,
                    example=4,
                    help="Deeper trees fit more, generalise less.",
                ),
                ModelParamSpec(
                    key="subsample",
                    label="Row subsample",
                    type=PARAM_NUMBER,
                    default=0.8,
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    example=0.8,
                ),
                ModelParamSpec(
                    key="colsample_bytree",
                    label="Column subsample",
                    type=PARAM_NUMBER,
                    default=0.8,
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    example=0.8,
                ),
            ),
            recommended_epochs=200,
            max_safe_epochs=2000,
            epoch_unit=EpochUnit.ROUNDS,
            default_batch_size=0,
            validation_requirements=tree_validation,
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.JOBLIB,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.XGBoostStrategyBlock",
            required_modules=("xgboost",),
            description="Gradient-boosted trees. Strong tabular baseline.",
        ),
        ModelSpec(
            block_id="lightgbm",
            display_name="LightGBM",
            model_family=ModelFamily.TREE,
            inputs=tree_in,
            outputs=tree_out,
            min_feature_columns=5,
            min_training_rows=2000,
            sequence_length=None,
            hyperparameters=(
                ModelParamSpec(
                    key="n_estimators",
                    label="Boosting rounds",
                    type=PARAM_INTEGER,
                    default=200,
                    min=10,
                    max=2000,
                    step=10,
                    unit="rounds",
                    example=200,
                ),
                _p_learning_rate(0.05),
                ModelParamSpec(
                    key="max_depth",
                    label="Max depth",
                    type=PARAM_INTEGER,
                    default=5,
                    min=2,
                    max=16,
                    step=1,
                    example=5,
                ),
                ModelParamSpec(
                    key="num_leaves",
                    label="Leaves per tree",
                    type=PARAM_INTEGER,
                    default=31,
                    min=2,
                    max=512,
                    step=1,
                    example=31,
                    help="Keep below 2**max_depth to avoid overfitting.",
                    depends_on=("max_depth",),
                ),
                ModelParamSpec(
                    key="subsample",
                    label="Row subsample",
                    type=PARAM_NUMBER,
                    default=0.8,
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    example=0.8,
                ),
                ModelParamSpec(
                    key="colsample_bytree",
                    label="Column subsample",
                    type=PARAM_NUMBER,
                    default=0.8,
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    example=0.8,
                ),
            ),
            recommended_epochs=200,
            max_safe_epochs=2000,
            epoch_unit=EpochUnit.ROUNDS,
            default_batch_size=0,
            validation_requirements=tree_validation,
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.JOBLIB,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.LightGBMStrategyBlock",
            required_modules=("lightgbm",),
            description="Histogram-based gradient boosting. Fast on wide feature sets.",
        ),
        ModelSpec(
            block_id="random_forest",
            display_name="Random Forest",
            model_family=ModelFamily.TREE,
            inputs=tree_in,
            outputs=tree_out,
            min_feature_columns=5,
            min_training_rows=2000,
            sequence_length=None,
            hyperparameters=(
                ModelParamSpec(
                    key="n_estimators",
                    label="Trees",
                    type=PARAM_INTEGER,
                    default=100,
                    min=10,
                    max=1000,
                    step=10,
                    unit="trees",
                    example=100,
                    help="Random forests do not train in epochs; this is the forest size.",
                ),
                ModelParamSpec(
                    key="max_depth",
                    label="Max depth",
                    type=PARAM_INTEGER,
                    default=6,
                    min=2,
                    max=32,
                    step=1,
                    example=6,
                ),
                ModelParamSpec(
                    key="min_samples_split",
                    label="Min samples to split",
                    type=PARAM_INTEGER,
                    default=5,
                    min=2,
                    max=100,
                    step=1,
                    example=5,
                ),
                ModelParamSpec(
                    key="max_features",
                    label="Features per split",
                    type=PARAM_SELECT,
                    default="sqrt",
                    options=("sqrt", "log2", "all"),
                    example="sqrt",
                ),
            ),
            # The design table records "n/a (n_estimators 100)": there is no epoch
            # loop, so the recommended and maximum counts are forest sizes.
            recommended_epochs=100,
            max_safe_epochs=1000,
            epoch_unit=EpochUnit.ESTIMATORS,
            default_batch_size=0,
            validation_requirements=tree_validation,
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.JOBLIB,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.RandomForestStrategyBlock",
            required_modules=("sklearn",),
            description="Bagged decision trees. Robust, low-tuning baseline.",
        ),
        ModelSpec(
            block_id="catboost",
            display_name="CatBoost",
            model_family=ModelFamily.TREE,
            inputs=tree_in,
            outputs=tree_out,
            min_feature_columns=5,
            min_training_rows=2000,
            sequence_length=None,
            hyperparameters=(
                ModelParamSpec(
                    key="iterations",
                    label="Iterations",
                    type=PARAM_INTEGER,
                    default=300,
                    min=10,
                    max=3000,
                    step=10,
                    unit="iterations",
                    example=300,
                ),
                _p_learning_rate(0.05),
                ModelParamSpec(
                    key="depth",
                    label="Tree depth",
                    type=PARAM_INTEGER,
                    default=5,
                    min=2,
                    max=12,
                    step=1,
                    example=5,
                ),
                ModelParamSpec(
                    key="l2_leaf_reg",
                    label="L2 leaf regularisation",
                    type=PARAM_NUMBER,
                    default=3.0,
                    min=0.1,
                    max=30.0,
                    step=0.1,
                    example=3.0,
                ),
            ),
            recommended_epochs=300,
            max_safe_epochs=3000,
            epoch_unit=EpochUnit.ITERATIONS,
            default_batch_size=0,
            validation_requirements=tree_validation,
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.JOBLIB,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.CatBoostStrategyBlock",
            required_modules=("catboost",),
            description="Ordered boosting with strong defaults. Half of SB-04.",
        ),
        ModelSpec(
            block_id="lstm",
            display_name="LSTM",
            model_family=ModelFamily.SEQUENCE,
            inputs=seq_in,
            outputs=seq_out,
            min_feature_columns=5,
            min_training_rows=5000,
            sequence_length=60,
            hyperparameters=(
                _p_sequence_length(60),
                ModelParamSpec(
                    key="units_1",
                    label="First layer units",
                    type=PARAM_INTEGER,
                    default=64,
                    min=8,
                    max=512,
                    step=8,
                    example=64,
                ),
                ModelParamSpec(
                    key="units_2",
                    label="Second layer units",
                    type=PARAM_INTEGER,
                    default=32,
                    min=8,
                    max=256,
                    step=8,
                    example=32,
                ),
                _p_dropout(0.2),
                _p_epochs(30, 200),
                _p_batch_size(64),
                _p_learning_rate(0.001),
            ),
            recommended_epochs=30,
            max_safe_epochs=200,
            epoch_unit=EpochUnit.EPOCHS,
            default_batch_size=64,
            validation_requirements=ValidationRequirements(
                val_fraction=0.15, test_fraction=0.15, embargo_bars=60, metric="accuracy"
            ),
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.KERAS,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.LSTMStrategyBlock",
            required_modules=("tensorflow", "sklearn"),
            description="Stacked LSTM over a rolling window of features.",
        ),
        ModelSpec(
            block_id="gru",
            display_name="GRU",
            model_family=ModelFamily.SEQUENCE,
            inputs=seq_in,
            outputs=seq_out,
            min_feature_columns=5,
            min_training_rows=5000,
            sequence_length=60,
            hyperparameters=(
                _p_sequence_length(60),
                ModelParamSpec(
                    key="units_1",
                    label="First layer units",
                    type=PARAM_INTEGER,
                    default=64,
                    min=8,
                    max=512,
                    step=8,
                    example=64,
                ),
                ModelParamSpec(
                    key="units_2",
                    label="Second layer units",
                    type=PARAM_INTEGER,
                    default=32,
                    min=8,
                    max=256,
                    step=8,
                    example=32,
                ),
                _p_dropout(0.2),
                _p_epochs(30, 200),
                _p_batch_size(64),
                _p_learning_rate(0.001),
            ),
            recommended_epochs=30,
            max_safe_epochs=200,
            epoch_unit=EpochUnit.EPOCHS,
            default_batch_size=64,
            validation_requirements=ValidationRequirements(
                val_fraction=0.15, test_fraction=0.15, embargo_bars=60, metric="accuracy"
            ),
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.KERAS,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.GRUStrategyBlock",
            required_modules=("tensorflow", "sklearn"),
            description="Gated recurrent network. Cheaper than LSTM, similar shape.",
        ),
        ModelSpec(
            block_id="transformer",
            display_name="Transformer",
            model_family=ModelFamily.SEQUENCE,
            inputs=seq_in,
            outputs=seq_out,
            min_feature_columns=5,
            min_training_rows=10000,
            sequence_length=120,
            hyperparameters=(
                _p_sequence_length(120),
                ModelParamSpec(
                    key="num_heads",
                    label="Attention heads",
                    type=PARAM_INTEGER,
                    default=4,
                    min=1,
                    max=16,
                    step=1,
                    example=4,
                ),
                ModelParamSpec(
                    key="key_dim",
                    label="Key dimension",
                    type=PARAM_INTEGER,
                    default=16,
                    min=4,
                    max=128,
                    step=4,
                    example=16,
                ),
                ModelParamSpec(
                    key="ff_dim",
                    label="Feed-forward units",
                    type=PARAM_INTEGER,
                    default=32,
                    min=8,
                    max=512,
                    step=8,
                    example=32,
                ),
                _p_dropout(0.2),
                _p_epochs(40, 200),
                _p_batch_size(64),
                _p_learning_rate(0.001),
            ),
            recommended_epochs=40,
            max_safe_epochs=200,
            epoch_unit=EpochUnit.EPOCHS,
            default_batch_size=64,
            validation_requirements=ValidationRequirements(
                val_fraction=0.15,
                test_fraction=0.15,
                embargo_bars=120,
                metric="accuracy",
            ),
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.KERAS,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.TransformerStrategyBlock",
            required_modules=("tensorflow", "sklearn"),
            description="Multi-head attention over a longer window. Needs the most data.",
        ),
        ModelSpec(
            block_id="autoencoder",
            display_name="Autoencoder (anomaly)",
            model_family=ModelFamily.AUTOENCODER,
            inputs=ae_in,
            outputs=ae_out,
            min_feature_columns=5,
            min_training_rows=5000,
            # The design table declares a 60-bar window for this block. The current
            # runtime reconstructs a single row at a time; the declared window is the
            # registry contract the executor is held to, and the value the data gate
            # reserves for.
            sequence_length=60,
            hyperparameters=(
                _p_sequence_length(60),
                ModelParamSpec(
                    key="hidden_ratio",
                    label="Hidden layer ratio",
                    type=PARAM_NUMBER,
                    default=0.5,
                    min=0.1,
                    max=1.0,
                    step=0.05,
                    example=0.5,
                    help="Hidden units as a fraction of the input column count.",
                ),
                ModelParamSpec(
                    key="bottleneck_ratio",
                    label="Bottleneck ratio",
                    type=PARAM_NUMBER,
                    default=0.25,
                    min=0.05,
                    max=0.9,
                    step=0.05,
                    example=0.25,
                    help="Must stay below the hidden layer ratio.",
                    depends_on=("hidden_ratio",),
                ),
                _p_dropout(0.2),
                _p_epochs(50, 300),
                _p_batch_size(64),
                ModelParamSpec(
                    key="anomaly_percentile",
                    label="Anomaly threshold percentile",
                    type=PARAM_NUMBER,
                    default=99.0,
                    min=90.0,
                    max=99.9,
                    step=0.1,
                    unit="%",
                    example=99.0,
                    help="Training reconstruction-error percentile treated as the threshold.",
                ),
            ),
            recommended_epochs=50,
            max_safe_epochs=300,
            epoch_unit=EpochUnit.EPOCHS,
            default_batch_size=64,
            validation_requirements=ValidationRequirements(
                val_fraction=0.15,
                test_fraction=0.15,
                embargo_bars=60,
                metric="reconstruction_mse",
            ),
            can_train=True,
            can_predict=True,
            serialization=SerializationMode.KERAS,
            backend_available=False,
            version=MODEL_SPEC_VERSION,
            runtime_ref="ml_models.AutoencoderStrategyBlock",
            required_modules=("tensorflow", "sklearn"),
            description="Unsupervised anomaly detector. The other half of SB-04.",
        ),
    ]

    return {
        spec.block_id: replace(
            spec, backend_available=_backend_available_for(spec.required_modules)
        )
        for spec in specs
    }


def resolve_model_runtime(runtime_ref: str):
    """
    Resolve a descriptor's `runtime_ref` to the model class that implements it.

    Accepts "ml_models.XGBoostStrategyBlock" and a bare class name. Raises
    AttributeError naming the offender so `build_registry()` fails startup rather
    than advertising a model block the platform cannot run (Requirements 4.7, 4.8).
    This is the ML_DL counterpart of `indicators_backend._resolve_runtime_ref`,
    `feature_engineering.resolve_feature_runtime` and
    `strategy_dag.block_specs.resolve_block_runtime`: every family resolves through
    the module that owns its implementations.
    """
    attribute = str(runtime_ref).rsplit(".", 1)[-1]
    target = globals().get(attribute)
    if not callable(target):
        raise AttributeError(
            f"runtime_ref does not resolve to a callable: {runtime_ref}"
        )
    return target


def _assert_spec_integrity(specs: Dict[str, ModelSpec]) -> None:
    """
    Static self-check on the descriptor table.

    This validates authored data, not the environment, so it either always
    passes or always fails — the same class of problem as a syntax error, and it
    should surface the moment the module is imported rather than at train time.
    """
    for block_id, spec in specs.items():
        if spec.block_id != block_id:
            raise ValueError(f"MODEL_SPECS key {block_id!r} != block_id {spec.block_id!r}")
        if spec.min_feature_columns < PLATFORM_MIN_FEATURE_COLUMNS:
            raise ValueError(
                f"{block_id}: min_feature_columns {spec.min_feature_columns} is below the "
                f"platform floor {PLATFORM_MIN_FEATURE_COLUMNS}"
            )
        if spec.min_training_rows <= 0:
            raise ValueError(f"{block_id}: min_training_rows must be positive")
        if spec.model_family is ModelFamily.TREE:
            if spec.sequence_length is not None:
                raise ValueError(f"{block_id}: TREE models must not declare a sequence length")
        elif not spec.sequence_length or spec.sequence_length <= 0:
            raise ValueError(f"{block_id}: {spec.model_family.value} needs a sequence length")
        if spec.max_safe_epochs < spec.recommended_epochs:
            raise ValueError(
                f"{block_id}: max_safe_epochs {spec.max_safe_epochs} is below "
                f"recommended {spec.recommended_epochs}"
            )
        if not spec.inputs:
            raise ValueError(f"{block_id}: no input ports declared")
        if not spec.outputs:
            raise ValueError(f"{block_id}: no output ports declared")
        if not spec.required_modules:
            raise ValueError(f"{block_id}: no required_modules to probe")
        # runtime_ref must name a real callable in this module
        try:
            resolve_model_runtime(spec.runtime_ref)
        except AttributeError as exc:
            raise ValueError(
                f"{block_id}: runtime_ref {spec.runtime_ref!r} does not resolve to a callable"
            ) from exc


# ── The registry and the derived lists ───────────────────────────────────

MODEL_SPECS: Dict[str, ModelSpec] = _build_model_specs()
_assert_spec_integrity(MODEL_SPECS)

_ML_FAMILIES = (ModelFamily.TREE,)
_DL_FAMILIES = (ModelFamily.SEQUENCE, ModelFamily.AUTOENCODER)


def _derive_available(families: Tuple[ModelFamily, ...]) -> List[str]:
    return [
        spec.block_id
        for spec in MODEL_SPECS.values()
        if spec.model_family in families and spec.backend_available and spec.can_train
    ]


# Derived, not hand-maintained. `catboost` and `autoencoder` appear here the
# moment their libraries are importable — the SB-04 fix.
AVAILABLE_ML_MODELS: List[str] = _derive_available(_ML_FAMILIES)
AVAILABLE_DL_MODELS: List[str] = _derive_available(_DL_FAMILIES)


def get_model_spec(block_id: str) -> Optional[ModelSpec]:
    """Return the spec for `block_id`, or None when it is not a known model block."""
    return MODEL_SPECS.get(block_id)


def available_model_specs() -> List[ModelSpec]:
    """
    Specs whose libraries actually resolve in this image.

    Registry assembly iterates this: an unavailable model is omitted rather than
    advertised and then failing at train time.
    """
    return [spec for spec in MODEL_SPECS.values() if spec.backend_available]


def refresh_backend_availability(deep: Optional[bool] = None) -> Dict[str, bool]:
    """
    Re-probe every model library and update MODEL_SPECS plus the derived lists
    in place, so existing `from ... import AVAILABLE_ML_MODELS` references stay
    valid. Returns block_id -> availability.

    Used by registry assembly and by the SB-04 parity test, which needs to prove
    that a model whose library is absent disappears from the registry.
    """
    _probe_cache.clear()
    for block_id, spec in list(MODEL_SPECS.items()):
        MODEL_SPECS[block_id] = replace(
            spec,
            backend_available=_backend_available_for(spec.required_modules, deep=deep),
        )

    AVAILABLE_ML_MODELS[:] = _derive_available(_ML_FAMILIES)
    AVAILABLE_DL_MODELS[:] = _derive_available(_DL_FAMILIES)

    unavailable = [b for b, s in MODEL_SPECS.items() if not s.backend_available]
    if unavailable:
        logger.info("Model blocks omitted (library not importable): %s", ", ".join(unavailable))

    return {block_id: spec.backend_available for block_id, spec in MODEL_SPECS.items()}
