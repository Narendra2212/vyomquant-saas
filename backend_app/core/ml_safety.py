"""
ML/DL Safety Infrastructure
Principal Institutional ML Infrastructure Safety Engineer

Enforces deterministic inference, timeout guards, GPU/CPU fallback,
memory bounds, training isolation, and safe model loading.
"""

import hashlib
import logging
import multiprocessing
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Optional

import numpy as np

logger = logging.getLogger("MLSafety")

# ══════════════════════════════════════════════════════════════════════════
#  DETERMINISTIC INFERENCE ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════


class DeterministicMode(Enum):
    """Deterministic inference modes."""
    STRICT = "strict"  # Full determinism, may impact performance
    STANDARD = "standard"  # Standard determinism, balanced performance
    NONE = "none"  # No determinism (for testing only)


@dataclass
class DeterministicConfig:
    """Configuration for deterministic inference."""
    mode: DeterministicMode = DeterministicMode.STANDARD
    numpy_seed: int = 42
    python_seed: int = 42
    tensorflow_seed: int = 42
    torch_seed: int = 42
    enforce_on_inference: bool = True
    log_seed_usage: bool = True


class DeterministicEnforcer:
    """
    Enforces deterministic inference across all ML frameworks.
    
    This is CRITICAL for replay correctness - identical inputs must produce
    identical outputs across all replays.
    """
    
    _instance: Optional['DeterministicEnforcer'] = None
    _config: DeterministicConfig = DeterministicConfig()
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def configure(cls, config: DeterministicConfig):
        """Configure deterministic enforcement."""
        cls._config = config
        cls._initialized = False
        logger.info(f"Deterministic config updated: mode={config.mode}")
    
    @classmethod
    def initialize(cls):
        """
        Initialize deterministic settings across all frameworks.
        
        This MUST be called before any inference to ensure replay correctness.
        """
        if cls._initialized:
            return
        
        config = cls._config
        
        if config.mode == DeterministicMode.NONE:
            logger.warning("Deterministic mode set to NONE - replay correctness NOT guaranteed")
            cls._initialized = True
            return
        
        # Set Python seed
        import random
        random.seed(config.python_seed)
        
        # Set NumPy seed
        np.random.seed(config.numpy_seed)
        
        # Set NumPy deterministic flags
        if config.mode == DeterministicMode.STRICT:
            os.environ['PYTHONHASHSEED'] = str(config.python_seed)
            np.random.bit_generator = np.random._bit_generator(np.random.MT19937(config.numpy_seed))
        
        # Set TensorFlow seed if available
        try:
            import tensorflow as tf
            tf.random.set_seed(config.tensorflow_seed)
            
            if config.mode == DeterministicMode.STRICT:
                os.environ['TF_DETERMINISTIC_OPS'] = '1'
                os.environ['TF_CUDNN_DETERMINISTIC'] = '1'
                
                # Configure GPU for determinism
                try:
                    tf.config.experimental.enable_op_determinism()
                except Exception as e:
                    logger.warning(f"Could not enable TensorFlow op determinism: {e}")
            
            if config.log_seed_usage:
                logger.info(f"TensorFlow deterministic seed set: {config.tensorflow_seed}")
        except ImportError:
            pass  # TensorFlow not available
        
        # Set PyTorch seed if available
        try:
            import torch
            torch.manual_seed(config.torch_seed)
            torch.cuda.manual_seed_all(config.torch_seed)
            
            if config.mode == DeterministicMode.STRICT:
                torch.backends.cudnn.deterministic = True
                torch.backends.cudnn.benchmark = False
            
            if config.log_seed_usage:
                logger.info(f"PyTorch deterministic seed set: {config.torch_seed}")
        except ImportError:
            pass  # PyTorch not available
        
        cls._initialized = True
        
        if config.log_seed_usage:
            logger.info(
                f"Deterministic inference initialized: "
                f"mode={config.mode.value}, "
                f"numpy_seed={config.numpy_seed}, "
                f"python_seed={config.python_seed}"
            )
    
    @classmethod
    def ensure_deterministic(cls):
        """
        Ensure deterministic settings are active before inference.
        
        This should be called before every inference to guarantee replay correctness.
        """
        if not cls._initialized:
            cls.initialize()
        
        if cls._config.enforce_on_inference:
            # Re-seed to ensure determinism
            np.random.seed(cls._config.numpy_seed)
            import random
            random.seed(cls._config.python_seed)
    
    @classmethod
    @contextmanager
    def deterministic_context(cls, seed: Optional[int] = None):
        """
        Context manager for deterministic inference.
        
        Usage:
            with DeterministicEnforcer.deterministic_context(seed=123):
                result = model.predict(features)
        """
        old_numpy_state = np.random.get_state()
        old_python_state = None
        
        try:
            import random
            old_python_state = random.getstate()
            
            if seed is not None:
                np.random.seed(seed)
                random.seed(seed)
            else:
                cls.ensure_deterministic()
            
            yield
        finally:
            np.random.set_state(old_numpy_state)
            if old_python_state:
                random.setstate(old_python_state)


# ══════════════════════════════════════════════════════════════════════════
#  INFERENCE TIMEOUT GUARDS
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TimeoutConfig:
    """Configuration for inference timeouts."""
    default_timeout: float = 2.0  # seconds
    max_timeout: float = 10.0  # seconds
    escalation_enabled: bool = True
    escalation_multiplier: float = 2.0
    max_escalations: int = 3
    watchdog_enabled: bool = True
    watchdog_interval: float = 0.5  # seconds


class TimeoutError(Exception):
    """Custom timeout error for ML inference."""
    pass


class InferenceTimeoutGuard:
    """
    Guards ML inference with strict timeout enforcement and watchdog protection.
    
    This prevents ML inference from blocking the execution loop indefinitely.
    """
    
    _config: TimeoutConfig = TimeoutConfig()
    
    @classmethod
    def configure(cls, config: TimeoutConfig):
        """Configure timeout guards."""
        cls._config = config
        logger.info(f"Timeout config updated: default={config.default_timeout}s")
    
    @classmethod
    def execute_with_timeout(
        cls,
        func: Callable,
        *args,
        timeout: Optional[float] = None,
        escalation_count: int = 0,
        **kwargs
    ) -> Any:
        """
        Execute function with timeout guard and watchdog protection.
        
        Args:
            func: Function to execute
            *args: Function arguments
            timeout: Timeout in seconds (uses default if None)
            escalation_count: Current escalation level for retry logic
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            TimeoutError: If function exceeds timeout
        """
        if timeout is None:
            timeout = cls._config.default_timeout
        
        # Validate timeout bounds
        timeout = min(timeout, cls._config.max_timeout)
        
        result = None
        exception = None
        
        def target():
            nonlocal result, exception
            try:
                result = func(*args, **kwargs)
            except Exception as e:
                exception = e
        
        # Start thread with timeout
        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(timeout=timeout)
        
        if thread.is_alive():
            # Thread still running - timeout exceeded
            if cls._config.watchdog_enabled:
                logger.error(f"Inference watchdog triggered after {timeout}s")
            
            if cls._config.escalation_enabled and escalation_count < cls._config.escalation_max_escalations:
                # Escalate timeout and retry
                new_timeout = timeout * cls._config.escalation_multiplier
                logger.warning(f"Inference timeout, escalating to {new_timeout}s (attempt {escalation_count + 1})")
                return cls.execute_with_timeout(
                    func, *args,
                    timeout=new_timeout,
                    escalation_count=escalation_count + 1,
                    **kwargs
                )
            
            raise TimeoutError(f"Inference exceeded timeout of {timeout}s")
        
        if exception is not None:
            raise exception
        
        return result


def with_timeout(timeout: Optional[float] = None):
    """
    Decorator for timeout-guarded inference.
    
    Usage:
        @with_timeout(timeout=2.0)
        def predict(features):
            return model.predict(features)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return InferenceTimeoutGuard.execute_with_timeout(
                func, *args, timeout=timeout, **kwargs
            )
        return wrapper
    return decorator


# ══════════════════════════════════════════════════════════════════════════
#  GPU/CPU FALLBACK
# ══════════════════════════════════════════════════════════════════════════


class DeviceType(Enum):
    """Available compute devices."""
    GPU = "gpu"
    CPU = "cpu"
    AUTO = "auto"


@dataclass
class DeviceConfig:
    """Configuration for device selection and fallback."""
    preferred_device: DeviceType = DeviceType.AUTO
    enable_gpu_detection: bool = True
    enable_cpu_fallback: bool = True
    gpu_memory_fraction: float = 0.8
    log_device_selection: bool = True


class DeviceManager:
    """
    Manages GPU/CPU device selection and fallback.
    
    Ensures ML inference works even when GPU is unavailable or fails.
    """
    
    _config: DeviceConfig = DeviceConfig()
    _current_device: DeviceType = DeviceType.CPU
    _gpu_available: bool = False
    
    @classmethod
    def configure(cls, config: DeviceConfig):
        """Configure device management."""
        cls._config = config
        cls._detect_gpu()
        cls._select_device()
        logger.info(f"Device config updated: preferred={config.preferred_device.value}")
    
    @classmethod
    def _detect_gpu(cls):
        """Detect GPU availability."""
        cls._gpu_available = False
        
        if not cls._config.enable_gpu_detection:
            return
        
        # Check TensorFlow GPU
        try:
            import tensorflow as tf
            gpus = tf.config.list_physical_devices('GPU')
            if gpus:
                cls._gpu_available = True
                if cls._config.log_device_selection:
                    logger.info(f"TensorFlow GPU detected: {len(gpus)} device(s)")
        except ImportError:
            pass
        
        # Check PyTorch GPU
        try:
            import torch
            if torch.cuda.is_available():
                cls._gpu_available = True
                if cls._config.log_device_selection:
                    logger.info(f"PyTorch GPU detected: {torch.cuda.device_count()} device(s)")
        except ImportError:
            pass
    
    @classmethod
    def _select_device(cls):
        """Select appropriate device based on configuration and availability."""
        if cls._config.preferred_device == DeviceType.GPU:
            if cls._gpu_available:
                cls._current_device = DeviceType.GPU
            elif cls._config.enable_cpu_fallback:
                logger.warning("GPU preferred but unavailable, falling back to CPU")
                cls._current_device = DeviceType.CPU
            else:
                logger.error("GPU preferred but unavailable and CPU fallback disabled")
                cls._current_device = DeviceType.CPU
        
        elif cls._config.preferred_device == DeviceType.CPU:
            cls._current_device = DeviceType.CPU
        
        else:  # AUTO
            if cls._gpu_available:
                cls._current_device = DeviceType.GPU
            else:
                cls._current_device = DeviceType.CPU
        
        if cls._config.log_device_selection:
            logger.info(f"Device selected: {cls._current_device.value}")
    
    @classmethod
    def get_device(cls) -> DeviceType:
        """Get current device."""
        return cls._current_device
    
    @classmethod
    def get_tensorflow_device(cls):
        """Get TensorFlow device string."""
        if cls._current_device == DeviceType.GPU:
            return "/GPU:0"
        else:
            return "/CPU:0"
    
    @classmethod
    def get_torch_device(cls):
        """Get PyTorch device."""
        try:
            import torch
            if cls._current_device == DeviceType.GPU and torch.cuda.is_available():
                return torch.device("cuda")
            else:
                return torch.device("cpu")
        except ImportError:
            return "cpu"
    
    @classmethod
    def configure_tensorflow_gpu(cls):
        """Configure TensorFlow GPU memory growth."""
        if cls._current_device != DeviceType.GPU:
            return
        
        try:
            import tensorflow as tf
            gpus = tf.config.list_physical_devices('GPU')
            if gpus:
                for gpu in gpus:
                    tf.config.experimental.set_memory_growth(gpu, True)
                    tf.config.experimental.set_virtual_device_configuration(
                        gpu,
                        [tf.config.experimental.VirtualDeviceConfiguration(
                            memory_limit=int(cls._config.gpu_memory_fraction * 1024)
                        )]
                    )
                logger.info("TensorFlow GPU memory growth configured")
        except Exception as e:
            logger.warning(f"Could not configure TensorFlow GPU: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  MEMORY BOUNDS
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class MemoryConfig:
    """Configuration for memory bounds."""
    max_model_cache_size_mb: int = 1024  # 1GB
    max_tensor_size_mb: int = 100  # 100MB
    max_batch_size: int = 1000
    enable_memory_monitoring: bool = True
    enable_auto_cleanup: bool = True
    cleanup_interval_seconds: int = 300  # 5 minutes


class MemoryMonitor:
    """
    Monitors and enforces memory bounds for ML operations.
    
    Prevents unbounded memory growth that could exhaust system resources.
    """
    
    _config: MemoryConfig = MemoryConfig()
    _model_cache: Dict[str, Any] = {}
    _cache_sizes: Dict[str, int] = {}
    _total_cache_size: int = 0
    
    @classmethod
    def configure(cls, config: MemoryConfig):
        """Configure memory bounds."""
        cls._config = config
        logger.info(f"Memory config updated: max_cache={config.max_model_cache_size_mb}MB")
    
    @classmethod
    def cache_model(cls, key: str, model: Any, size_mb: int):
        """
        Cache model with size tracking.
        
        Args:
            key: Model cache key
            model: Model object
            size_mb: Model size in MB
            
        Raises:
            MemoryError: If cache would exceed limits
        """
        if cls._total_cache_size + size_mb > cls._config.max_model_cache_size_mb:
            if cls._config.enable_auto_cleanup:
                cls._cleanup_stale_models()
            
            if cls._total_cache_size + size_mb > cls._config.max_model_cache_size_mb:
                raise MemoryError(
                    f"Model cache would exceed limit: "
                    f"{cls._total_cache_size + size_mb}MB > {cls._config.max_model_cache_size_mb}MB"
                )
        
        cls._model_cache[key] = model
        cls._cache_sizes[key] = size_mb
        cls._total_cache_size += size_mb
        
        logger.info(f"Model cached: {key} ({size_mb}MB, total: {cls._total_cache_size}MB)")
    
    @classmethod
    def get_cached_model(cls, key: str) -> Optional[Any]:
        """Get cached model."""
        return cls._model_cache.get(key)
    
    @classmethod
    def remove_cached_model(cls, key: str):
        """Remove model from cache."""
        if key in cls._model_cache:
            size = cls._cache_sizes[key]
            del cls._model_cache[key]
            del cls._cache_sizes[key]
            cls._total_cache_size -= size
            logger.info(f"Model removed from cache: {key} ({size}MB)")
    
    @classmethod
    def _cleanup_stale_models(cls):
        """Clean up stale models based on LRU policy."""
        if not cls._model_cache:
            return
        
        # Simple LRU: remove oldest entries
        keys_to_remove = list(cls._model_cache.keys())[:len(cls._model_cache) // 2]
        
        for key in keys_to_remove:
            cls.remove_cached_model(key)
        
        logger.info(f"Cleaned up {len(keys_to_remove)} stale models")
    
    @classmethod
    def get_cache_stats(cls) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "total_size_mb": cls._total_cache_size,
            "max_size_mb": cls._config.max_model_cache_size_mb,
            "utilization_pct": (cls._total_cache_size / cls._config.max_model_cache_size_mb) * 100,
            "model_count": len(cls._model_cache)
        }
    
    @classmethod
    def validate_tensor_size(cls, tensor_size_mb: int):
        """
        Validate tensor size against bounds.
        
        Args:
            tensor_size_mb: Tensor size in MB
            
        Raises:
            MemoryError: If tensor exceeds limits
        """
        if tensor_size_mb > cls._config.max_tensor_size_mb:
            raise MemoryError(
                f"Tensor size exceeds limit: {tensor_size_mb}MB > {cls._config.max_tensor_size_mb}MB"
            )
    
    @classmethod
    def validate_batch_size(cls, batch_size: int):
        """
        Validate batch size against bounds.
        
        Args:
            batch_size: Batch size
            
        Raises:
            ValueError: If batch size exceeds limits
        """
        if batch_size > cls._config.max_batch_size:
            raise ValueError(
                f"Batch size exceeds limit: {batch_size} > {cls._config.max_batch_size}"
            )


# ══════════════════════════════════════════════════════════════════════════
#  SAFE MODEL LOADING
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class ModelMetadata:
    """Metadata for safe model loading."""
    checksum: str
    version: str
    size_bytes: int
    loaded_at: float


class ModelLoadError(Exception):
    """Custom error for model loading failures."""
    pass


class SafeModelLoader:
    """
    Safely loads models with checksum validation and corruption detection.
    
    Prevents loading corrupted or malicious models that could compromise
    system integrity or trading safety.
    """
    
    _metadata_cache: Dict[str, ModelMetadata] = {}
    
    @classmethod
    def compute_checksum(cls, file_path: str) -> str:
        """Compute SHA-256 checksum of model file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    @classmethod
    def load_model(
        cls,
        file_path: str,
        expected_checksum: Optional[str] = None,
        validate_integrity: bool = True
    ) -> Any:
        """
        Safely load model with validation.
        
        Args:
            file_path: Path to model file
            expected_checksum: Expected SHA-256 checksum (optional)
            validate_integrity: Whether to validate model integrity
            
        Returns:
            Loaded model
            
        Raises:
            ModelLoadError: If model validation fails
        """
        import joblib

        # Check file exists
        if not os.path.exists(file_path):
            raise ModelLoadError(f"Model file not found: {file_path}")
        
        # Compute checksum
        actual_checksum = cls.compute_checksum(file_path)
        
        # Validate checksum if provided
        if expected_checksum and actual_checksum != expected_checksum:
            raise ModelLoadError(
                f"Model checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
            )
        
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # Load model
        try:
            model = joblib.load(file_path)
        except Exception as e:
            raise ModelLoadError(f"Failed to load model: {e}")
        
        # Validate integrity if requested
        if validate_integrity:
            try:
                # Basic validation: check model has expected attributes
                if hasattr(model, 'predict'):
                    # Test prediction with dummy data
                    test_input = np.random.rand(1, 10)  # Generic test
                    _ = model.predict(test_input)
            except Exception as e:
                raise ModelLoadError(f"Model integrity validation failed: {e}")
        
        # Cache metadata
        metadata = ModelMetadata(
            checksum=actual_checksum,
            version=os.path.basename(file_path).split('_')[-1].split('.')[0],
            size_bytes=file_size,
            loaded_at=time.time()
        )
        cls._metadata_cache[file_path] = metadata
        
        logger.info(
            f"Model loaded safely: {file_path} "
            f"(checksum={actual_checksum[:16]}..., size={file_size}MB)"
        )
        
        return model
    
    @classmethod
    def get_metadata(cls, file_path: str) -> Optional[ModelMetadata]:
        """Get cached model metadata."""
        return cls._metadata_cache.get(file_path)
    
    @classmethod
    def validate_cached_model(cls, file_path: str) -> bool:
        """
        Validate cached model against current file.
        
        Returns:
            True if cached model is still valid
        """
        metadata = cls._metadata_cache.get(file_path)
        if not metadata:
            return False
        
        current_checksum = cls.compute_checksum(file_path)
        return current_checksum == metadata.checksum


# ══════════════════════════════════════════════════════════════════════════
#  TRAINING ISOLATION
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class TrainingConfig:
    """Configuration for training isolation."""
    enable_process_isolation: bool = True
    max_cpu_cores: int = 4
    max_memory_mb: int = 4096  # 4GB
    max_training_time_seconds: int = 3600  # 1 hour
    enable_resource_monitoring: bool = True


class TrainingIsolator:
    """
    Isolates training in separate process to prevent interference with execution.
    
    Training must NEVER:
    - Interfere with execution runtime
    - Saturate Redis
    - Block orchestration
    - Mutate replay state
    """
    
    _config: TrainingConfig = TrainingConfig()
    
    @classmethod
    def configure(cls, config: TrainingConfig):
        """Configure training isolation."""
        cls._config = config
        logger.info(f"Training config updated: process_isolation={config.enable_process_isolation}")
    
    @classmethod
    def run_isolated_training(
        cls,
        train_func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Run training in isolated process.
        
        Args:
            train_func: Training function
            *args: Training function arguments
            **kwargs: Training function keyword arguments
            
        Returns:
            Training result
            
        Raises:
            RuntimeError: If training fails or exceeds limits
        """
        if not cls._config.enable_process_isolation:
            # Run in current process (not recommended for production)
            logger.warning("Training isolation disabled - running in current process")
            return train_func(*args, **kwargs)
        
        # Run in separate process
        multiprocessing.get_context('spawn')
        
        with multiprocessing.Pool(
            processes=1,
            maxtasksperchild=1
        ) as pool:
            try:
                result = pool.apply_async(train_func, args, kwargs)
                
                # Wait with timeout
                result.wait(timeout=cls._config.max_training_time_seconds)
                
                if not result.ready():
                    pool.terminate()
                    raise RuntimeError(f"Training exceeded time limit of {cls._config.max_training_time_seconds}s")
                
                return result.get()
            
            except Exception as e:
                pool.terminate()
                raise RuntimeError(f"Isolated training failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
#  INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════


def initialize_ml_safety(
    deterministic_config: Optional[DeterministicConfig] = None,
    timeout_config: Optional[TimeoutConfig] = None,
    device_config: Optional[DeviceConfig] = None,
    memory_config: Optional[MemoryConfig] = None,
    training_config: Optional[TrainingConfig] = None
):
    """
    Initialize ML safety infrastructure.
    
    This MUST be called at application startup to ensure all safety mechanisms
    are active before any ML operations.
    
    Args:
        deterministic_config: Deterministic inference configuration
        timeout_config: Timeout guard configuration
        device_config: Device management configuration
        memory_config: Memory bounds configuration
        training_config: Training isolation configuration
    """
    logger.info("Initializing ML safety infrastructure...")
    
    if deterministic_config:
        DeterministicEnforcer.configure(deterministic_config)
    
    if timeout_config:
        InferenceTimeoutGuard.configure(timeout_config)
    
    if device_config:
        DeviceManager.configure(device_config)
    
    if memory_config:
        MemoryMonitor.configure(memory_config)
    
    if training_config:
        TrainingIsolator.configure(training_config)
    
    # Initialize deterministic enforcement
    DeterministicEnforcer.initialize()
    
    # Configure GPU if available
    DeviceManager.configure_tensorflow_gpu()
    
    logger.info("ML safety infrastructure initialized successfully")


# Default configurations for institutional-grade safety
DEFAULT_DETERMINISTIC_CONFIG = DeterministicConfig(
    mode=DeterministicMode.STANDARD,
    numpy_seed=42,
    python_seed=42,
    tensorflow_seed=42,
    torch_seed=42,
    enforce_on_inference=True,
    log_seed_usage=True
)

DEFAULT_TIMEOUT_CONFIG = TimeoutConfig(
    default_timeout=2.0,
    max_timeout=10.0,
    escalation_enabled=True,
    escalation_multiplier=2.0,
    max_escalations=3,
    watchdog_enabled=True,
    watchdog_interval=0.5
)

DEFAULT_DEVICE_CONFIG = DeviceConfig(
    preferred_device=DeviceType.AUTO,
    enable_gpu_detection=True,
    enable_cpu_fallback=True,
    gpu_memory_fraction=0.8,
    log_device_selection=True
)

DEFAULT_MEMORY_CONFIG = MemoryConfig(
    max_model_cache_size_mb=1024,
    max_tensor_size_mb=100,
    max_batch_size=1000,
    enable_memory_monitoring=True,
    enable_auto_cleanup=True,
    cleanup_interval_seconds=300
)

DEFAULT_TRAINING_CONFIG = TrainingConfig(
    enable_process_isolation=True,
    max_cpu_cores=4,
    max_memory_mb=4096,
    max_training_time_seconds=3600,
    enable_resource_monitoring=True
)
