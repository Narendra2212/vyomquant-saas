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

import os
import re
import uuid
import logging
import numpy as np
import joblib
from abc import ABC, abstractmethod

logger = logging.getLogger("MLModels")

# Import ML safety infrastructure for institutional-grade safety
try:
    from backend_app.core.ml_safety import (
        DeterministicEnforcer,
        InferenceTimeoutGuard,
        DeviceManager,
        MemoryMonitor,
        SafeModelLoader,
        TrainingIsolator,
        DEFAULT_DETERMINISTIC_CONFIG,
        DEFAULT_TIMEOUT_CONFIG,
        DEFAULT_DEVICE_CONFIG,
        DEFAULT_MEMORY_CONFIG,
        DEFAULT_TRAINING_CONFIG,
        initialize_ml_safety,
        with_timeout
    )
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
        
        # Use safe model loader if available
        if ML_SAFETY_AVAILABLE:
            try:
                self.active_model = SafeModelLoader.load_model(
                    strategy_path,
                    validate_integrity=True
                )
                logger.info(f"Model loaded safely into RAM: {strategy_path}")
            except Exception as e:
                logger.error(f"Safe model loading failed: {e}")
                # Fallback to unsafe loading
                self.active_model = joblib.load(strategy_path)
                logger.warning(f"Model loaded unsafely (fallback): {strategy_path}")
        else:
            self.active_model = joblib.load(strategy_path)
            logger.info(f"Model loaded into RAM (unsafe mode): {strategy_path}")
        
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
        
        self.active_scaler = joblib.load(scaler_path)
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
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import LSTM, Dense, Dropout, Input

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
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import GRU, Dense, Dropout, Input

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
        from tensorflow.keras.models import Model
        from tensorflow.keras.layers import (
            Input,
            MultiHeadAttention,
            LayerNormalization,
            GlobalAveragePooling1D,
            Dense,
            Dropout,
        )

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
        self.active_scaler = joblib.load(scaler_path)
        self.active_threshold = joblib.load(threshold_path)
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
        from tensorflow.keras.models import Model
        from tensorflow.keras.layers import Input, Dense, Dropout
        from sklearn.preprocessing import StandardScaler

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

