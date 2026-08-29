"""
╔══════════════════════════════════════════════════════════════════════════╗
║  FEATURE ENGINEERING PIPELINE                                            ║
║                                                                          ║
║  Advanced feature extraction for ML models                               ║
║  - No data leakage (only uses past data)                                 ║
║  - Efficient numpy-based computation                                     ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger("FeatureEngineering")


# ══════════════════════════════════════════════════════════════════════════
#  FEATURE MATRIX CONTAINER
#
#  A FEATURE_MATRIX port payload carries its own timestamp index, its column
#  names and a per-column warmup offset. Downstream blocks (feat_concat in
#  particular) align on the timestamp index, never on row position: position
#  alignment silently shifts a feature by one bar relative to its label.
#
#  The type itself lives in strategy_dag/feature_matrix.py, which is the
#  canonical port contract: the validator, the compiler, the DAG runtime and the
#  training pipeline all read it, and none of them may import this engine. Two
#  FeatureMatrix classes would be the same dual-source-of-truth defect as the two
#  DAG compilers (SB-01), so this module re-exports the one contract rather than
#  restating it. The names below stay importable from here for every existing
#  caller; the alignment algebra is imported under a private alias and delegated
#  to by FeatureEngine.concat_feature_matrices / select_feature_columns, so the
#  declared FEATURE_SPECS runtime_refs keep resolving.
# ══════════════════════════════════════════════════════════════════════════

from backend_app.backend.strategy_dag.feature_matrix import (  # noqa: E402
    FeatureAlignmentError,
    FeatureColumnError,
    FeatureIndexError,
    FeatureMatrix,
    FeatureMatrixError,
    FeatureShapeError,
    build_feature_matrix,
)
from backend_app.backend.strategy_dag.feature_matrix import (  # noqa: E402
    concat_matrices as _concat_matrices,
)
from backend_app.backend.strategy_dag.feature_matrix import (  # noqa: E402
    select_columns as _select_columns,
)


class FeatureEngine:
    """Advanced feature engineering with no data leakage."""
    
    @staticmethod
    def compute_log_returns(prices: np.ndarray) -> np.ndarray:
        """
        Compute log returns: ln(price[t] / price[t-1])
        First value is NaN (no previous price)
        """
        log_returns = np.diff(np.log(prices), prepend=np.log(prices[0]))
        log_returns[0] = np.nan  # No previous price for first value
        return log_returns
    
    @staticmethod
    def compute_volatility(
        log_returns: np.ndarray,
        window: int = 20,
        annualize: bool = False,
        bars_per_year: int = 365 * 24,
    ) -> np.ndarray:
        """
        Compute rolling volatility (standard deviation of log returns)
        First (window-1) values are NaN

        annualize scales by sqrt(bars_per_year); it is off by default so the
        historical return shape of this function is unchanged.
        """
        # Using convolution for rolling std
        n = len(log_returns)
        volatility = np.full(n, np.nan)
        
        for i in range(window, n):
            window_data = log_returns[i-window+1:i+1]
            if not np.all(np.isnan(window_data)):
                volatility[i] = np.nanstd(window_data)
        
        if annualize:
            volatility = volatility * np.sqrt(float(bars_per_year))
        
        return volatility
    
    @staticmethod
    def compute_volume_features(volume: np.ndarray, window: int = 20) -> tuple:
        """
        Compute volume-based features
        - Volume rolling mean
        - Volume rolling std
        - Volume ratio (current / rolling mean)
        """
        n = len(volume)
        vol_mean = np.full(n, np.nan)
        vol_std = np.full(n, np.nan)
        vol_ratio = np.full(n, np.nan)
        
        for i in range(window, n):
            window_vol = volume[i-window+1:i+1]
            vol_mean[i] = np.mean(window_vol)
            vol_std[i] = np.std(window_vol)
            if vol_mean[i] > 0:
                vol_ratio[i] = volume[i] / vol_mean[i]
        
        return vol_mean, vol_std, vol_ratio
    
    @staticmethod
    def compute_price_momentum(prices: np.ndarray, windows: list = [5, 10, 20]) -> np.ndarray:
        """
        Compute price momentum features
        - Returns over different time windows
        """
        n = len(prices)
        momentum_features = []
        
        for window in windows:
            momentum = np.full(n, np.nan)
            for i in range(window, n):
                if prices[i-window] > 0:
                    momentum[i] = (prices[i] - prices[i-window]) / prices[i-window]
            momentum_features.append(momentum)
        
        return np.column_stack(momentum_features) if momentum_features else np.array([])
    
    @staticmethod
    def compute_lag_features(data: np.ndarray, lags: list = [1, 2, 3]) -> np.ndarray:
        """
        Compute lag features (t-1, t-2, t-3)
        No data leakage - only past values
        """
        n = len(data)
        lag_features = []
        
        for lag in lags:
            lagged = np.full(n, np.nan)
            lagged[lag:] = data[:-lag]
            lag_features.append(lagged)
        
        return np.column_stack(lag_features) if lag_features else np.array([])
    
    @staticmethod
    def compute_rolling_stats(data: np.ndarray, window: int = 20) -> tuple:
        """
        Compute rolling mean and standard deviation
        """
        n = len(data)
        rolling_mean = np.full(n, np.nan)
        rolling_std = np.full(n, np.nan)
        
        for i in range(window, n):
            window_data = data[i-window+1:i+1]
            rolling_mean[i] = np.mean(window_data)
            rolling_std[i] = np.std(window_data)
        
        return rolling_mean, rolling_std
    
    @staticmethod
    def compute_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
        """
        Compute RSI (Relative Strength Index)
        """
        n = len(prices)
        if n < period + 1:
            return np.full(n, 50.0)
        
        delta = np.diff(prices, prepend=prices[0])
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        
        # Use simple rolling mean
        avg_gain = np.full(n, np.nan)
        avg_loss = np.full(n, np.nan)
        
        for i in range(period, n):
            avg_gain[i] = np.mean(gain[i-period+1:i+1])
            avg_loss[i] = np.mean(loss[i-period+1:i+1])
        
        rs = avg_gain / (avg_loss + 1e-9)
        rsi = 100 - (100 / (1 + rs))
        rsi[:period] = 50.0
        
        return rsi
    
    @staticmethod
    def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
        """
        Compute Exponential Moving Average
        """
        n = len(prices)
        ema = np.full(n, np.nan)
        ema[0] = prices[0]
        multiplier = 2 / (period + 1)
        
        for i in range(1, n):
            ema[i] = (prices[i] * multiplier) + (ema[i-1] * (1 - multiplier))
        
        return ema
    
    @staticmethod
    def compute_macd(prices: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple:
        """
        Compute MACD and signal line
        """
        ema_fast = FeatureEngine.compute_ema(prices, fast)
        ema_slow = FeatureEngine.compute_ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = FeatureEngine.compute_ema(macd_line, signal)
        
        return macd_line, signal_line, macd_line - signal_line  # macd, signal, histogram
    
    # ──────────────────────────────────────────────────────────────────────
    #  Individually addressable feature primitives (FEATURE_SPECS runtimes).
    #  Every one of these reads bar t and earlier only.
    # ──────────────────────────────────────────────────────────────────────
    
    @staticmethod
    def compute_returns(prices: np.ndarray, periods: Sequence[int] = (1,)) -> np.ndarray:
        """
        Simple returns over one or more lookback periods:
        (price[t] - price[t-p]) / price[t-p]
        First p values of each column are NaN. Past data only.
        """
        prices = np.asarray(prices, dtype=float)
        n = len(prices)
        columns = []
        
        for period in periods:
            period = int(period)
            if period < 1:
                raise ValueError(f"returns period must be >= 1, got {period}")
            out = np.full(n, np.nan)
            if n > period:
                previous = prices[:-period]
                with np.errstate(divide="ignore", invalid="ignore"):
                    out[period:] = np.where(previous != 0, (prices[period:] - previous) / previous, np.nan)
            columns.append(out)
        
        return np.column_stack(columns) if columns else np.array([])
    
    @staticmethod
    def compute_rolling_mean(data: np.ndarray, window: int = 20) -> np.ndarray:
        """Rolling mean over the trailing `window` bars. First (window-1) values NaN."""
        data = np.asarray(data, dtype=float)
        n = len(data)
        out = np.full(n, np.nan)
        for i in range(window - 1, n):
            out[i] = np.mean(data[i - window + 1:i + 1])
        return out
    
    @staticmethod
    def compute_rolling_std(data: np.ndarray, window: int = 20, ddof: int = 0) -> np.ndarray:
        """Rolling standard deviation over the trailing `window` bars."""
        data = np.asarray(data, dtype=float)
        n = len(data)
        out = np.full(n, np.nan)
        if window - ddof <= 0:
            raise ValueError(f"window ({window}) must exceed ddof ({ddof})")
        for i in range(window - 1, n):
            out[i] = np.std(data[i - window + 1:i + 1], ddof=ddof)
        return out
    
    @staticmethod
    def compute_zscore(data: np.ndarray, window: int = 20, mode: str = "rolling") -> np.ndarray:
        """
        Z-score of a series.
        
        mode='rolling'   trailing window only (leak-safe)
        mode='expanding' all bars up to and including t (leak-safe)
        mode='global'    whole-series mean/std. Representable so a graph can be
                         *rejected* for it (GLOBAL_STATISTIC_LEAK); it reads bars
                         after t and must never reach a model.
        """
        data = np.asarray(data, dtype=float)
        n = len(data)
        out = np.full(n, np.nan)
        
        if mode == "global":
            mean = np.nanmean(data)
            std = np.nanstd(data)
            if std > 0:
                out = (data - mean) / std
            return out
        
        if mode == "expanding":
            for i in range(1, n):
                segment = data[:i + 1]
                std = np.nanstd(segment)
                if std > 0:
                    out[i] = (data[i] - np.nanmean(segment)) / std
            return out
        
        if mode != "rolling":
            raise ValueError(f"unknown zscore mode: {mode}")
        
        for i in range(window - 1, n):
            segment = data[i - window + 1:i + 1]
            std = np.nanstd(segment)
            if std > 0:
                out[i] = (data[i] - np.nanmean(segment)) / std
        return out
    
    @staticmethod
    def compute_normalize(
        data: np.ndarray,
        window: int = 20,
        method: str = "minmax",
        mode: str = "rolling",
    ) -> np.ndarray:
        """
        Scale a series into a comparable range using trailing-window statistics.
        
        method='minmax'  (x - min) / (max - min) over the window
        method='robust'  (x - median) / IQR over the window
        mode='global'    whole-series statistics: representable so validation can
                         reject it (GLOBAL_STATISTIC_LEAK), never leak-safe.
        """
        data = np.asarray(data, dtype=float)
        n = len(data)
        out = np.full(n, np.nan)
        
        def _scale(segment: np.ndarray, value: float) -> float:
            if method == "minmax":
                low, high = np.nanmin(segment), np.nanmax(segment)
                spread = high - low
                return (value - low) / spread if spread > 0 else np.nan
            if method == "robust":
                median = np.nanmedian(segment)
                iqr = np.nanpercentile(segment, 75) - np.nanpercentile(segment, 25)
                return (value - median) / iqr if iqr > 0 else np.nan
            raise ValueError(f"unknown normalize method: {method}")
        
        if mode == "global":
            for i in range(n):
                out[i] = _scale(data, data[i])
            return out
        
        if mode != "rolling":
            raise ValueError(f"unknown normalize mode: {mode}")
        
        for i in range(window - 1, n):
            out[i] = _scale(data[i - window + 1:i + 1], data[i])
        return out
    
    @staticmethod
    def compute_standardize(
        matrix: "FeatureMatrix",
        fit_range: Optional[Tuple[int, int]] = None,
        fit_on: str = "train_split",
    ) -> "FeatureMatrix":
        """
        Standardize every column of a FeatureMatrix with a scaler fit on the
        training split only.
        
        `fit_range` is the half-open [start, end) row range of the train split.
        It is mandatory: fitting on all rows would leak validation and test
        statistics into training, which is why this block is classified
        REVIEW_REQUIRED.
        """
        if fit_on != "train_split":
            raise ValueError(
                f"fit_on must be 'train_split' (got {fit_on!r}); fitting on any "
                "wider range leaks out-of-sample statistics"
            )
        if fit_range is None:
            raise ValueError(
                "compute_standardize requires an explicit train-split fit_range; "
                "refusing to fit the scaler on the full series"
            )
        
        start, end = int(fit_range[0]), int(fit_range[1])
        if not 0 <= start < end <= matrix.n_rows:
            raise ValueError(f"invalid fit_range {fit_range} for {matrix.n_rows} rows")
        
        fitted = matrix.values[start:end, :]
        means = np.nanmean(fitted, axis=0)
        stds = np.nanstd(fitted, axis=0)
        safe = np.where(stds > 0, stds, np.nan)
        values = (matrix.values - means) / safe
        
        return FeatureMatrix(
            index=matrix.index,
            columns=[f"{name}_std" for name in matrix.columns],
            values=values,
            column_warmup={
                f"{name}_std": matrix.column_warmup.get(name, matrix.warmup_offset)
                for name in matrix.columns
            },
            provenance={
                f"{name}_std": matrix.provenance.get(name, "")
                for name in matrix.columns
                if matrix.provenance.get(name)
            },
        )
    
    @staticmethod
    def compute_time_features(
        timestamps: np.ndarray,
        parts: Sequence[str] = ("hour", "dow"),
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Calendar features derived from the bar timestamp itself.
        
        parts: hour (0-23 UTC), dow (0=Monday), dom (1-31), month (1-12),
        session (0=asia, 1=europe, 2=us, 3=off-hours, by UTC hour).
        No lookback, no leakage: a bar's own timestamp is known at that bar.
        """
        stamps = np.asarray(timestamps)
        if np.issubdtype(stamps.dtype, np.datetime64):
            seconds = stamps.astype("datetime64[s]")
        else:
            raw = stamps.astype("int64")
            unit = "ms" if raw.size and np.max(np.abs(raw)) > 100_000_000_000 else "s"
            seconds = raw.astype(f"datetime64[{unit}]").astype("datetime64[s]")
        
        days = seconds.astype("datetime64[D]")
        months = seconds.astype("datetime64[M]")
        hour = ((seconds - days) / np.timedelta64(1, "h")).astype(float)
        
        available = {
            "hour": hour,
            # 1970-01-01 was a Thursday, so Monday-indexed dow needs +3.
            "dow": ((days.astype("int64") + 3) % 7).astype(float),
            "dom": ((days - months).astype("int64") + 1).astype(float),
            "month": ((months.astype("int64") % 12) + 1).astype(float),
            "session": np.select(
                [hour < 7, hour < 12, hour < 20],
                [0.0, 1.0, 2.0],
                default=3.0,
            ),
        }
        
        columns = []
        series = []
        for part in parts:
            if part not in available:
                raise ValueError(f"unknown time part: {part}")
            columns.append(f"time_{part}")
            series.append(available[part])
        
        values = np.column_stack(series) if series else np.empty((len(stamps), 0))
        return values, columns
    
    @staticmethod
    def compute_price_transform(
        open_: Optional[np.ndarray],
        high: Optional[np.ndarray],
        low: Optional[np.ndarray],
        close: np.ndarray,
        transform: str = "hlc3",
    ) -> np.ndarray:
        """
        Single-bar price transforms. Bar t only, so no lookback and no leakage.
        hl2, hlc3, ohlc4, typical (= hlc3), log (= ln(close)).
        """
        close = np.asarray(close, dtype=float)
        
        if transform == "log":
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(close > 0, np.log(close), np.nan)
        
        if transform == "hl2":
            return (np.asarray(high, dtype=float) + np.asarray(low, dtype=float)) / 2.0
        if transform in ("hlc3", "typical"):
            return (
                np.asarray(high, dtype=float)
                + np.asarray(low, dtype=float)
                + close
            ) / 3.0
        if transform == "ohlc4":
            return (
                np.asarray(open_, dtype=float)
                + np.asarray(high, dtype=float)
                + np.asarray(low, dtype=float)
                + close
            ) / 4.0
        
        raise ValueError(f"unknown price transform: {transform}")
    
    @staticmethod
    def concat_feature_matrices(matrices: Sequence["FeatureMatrix"]) -> "FeatureMatrix":
        """
        Merge 2..N FeatureMatrix inputs into one, aligned on the timestamp index.
        
        The runtime for feat_concat. Alignment is by timestamp, never by row
        position: two matrices whose warmups differ have different leading NaN
        counts, and positional stacking would shift a feature by one or more bars
        relative to its label.
        
        Delegates to strategy_dag.feature_matrix.concat_matrices, which is the one
        implementation of the join and the one place the mapping is verified. This
        method exists because FEATURE_SPECS declares
        runtime_ref="FeatureEngine.concat_feature_matrices"; it adds no behaviour.
        """
        return _concat_matrices(matrices)
    
    @staticmethod
    def select_feature_columns(
        matrix: "FeatureMatrix",
        columns: Sequence[str],
    ) -> "FeatureMatrix":
        """
        Keep only the named columns, in the order requested. Unknown column names
        are an error rather than a silent drop.
        
        The runtime for feat_select; delegates to
        strategy_dag.feature_matrix.select_columns.
        """
        return _select_columns(matrix, columns)
    
    @classmethod
    def create_feature_matrix(
        cls,
        prices: np.ndarray,
        volumes: np.ndarray = None,
        include_indicators: bool = True,
        include_returns: bool = True,
        include_volatility: bool = True,
        include_volume_features: bool = True,
        include_momentum: bool = True,
        include_lags: bool = True,
        include_rolling: bool = True,
    ) -> tuple:
        """
        Create complete feature matrix with all features
        
        Returns:
            (feature_matrix, feature_names)
        """
        features = []
        feature_names = []
        
        # 1. Technical Indicators (RSI, EMA, MACD)
        if include_indicators:
            rsi = cls.compute_rsi(prices, 14)
            ema_20 = cls.compute_ema(prices, 20)
            ema_50 = cls.compute_ema(prices, 50)
            macd, macd_signal, macd_hist = cls.compute_macd(prices)
            
            features.extend([rsi, ema_20, ema_50, macd, macd_signal, macd_hist])
            feature_names.extend(['rsi', 'ema_20', 'ema_50', 'macd', 'macd_signal', 'macd_hist'])
        
        # 2. Log Returns
        if include_returns:
            log_returns = cls.compute_log_returns(prices)
            features.append(log_returns)
            feature_names.append('log_returns')
        
        # 3. Volatility (rolling std of returns)
        if include_volatility:
            log_returns = cls.compute_log_returns(prices) if not include_returns else features[feature_names.index('log_returns')]
            vol_20 = cls.compute_volatility(log_returns, 20)
            vol_50 = cls.compute_volatility(log_returns, 50)
            features.extend([vol_20, vol_50])
            feature_names.extend(['volatility_20', 'volatility_50'])
        
        # 4. Volume Features
        if include_volume_features and volumes is not None:
            vol_mean, vol_std, vol_ratio = cls.compute_volume_features(volumes, 20)
            features.extend([vol_mean, vol_std, vol_ratio])
            feature_names.extend(['volume_mean', 'volume_std', 'volume_ratio'])
        
        # 5. Price Momentum
        if include_momentum:
            momentum = cls.compute_price_momentum(prices, [5, 10, 20])
            if momentum.size > 0:
                features.extend([momentum[:, i] for i in range(momentum.shape[1])])
                feature_names.extend(['momentum_5', 'momentum_10', 'momentum_20'])
        
        # 6. Lag Features (prices)
        if include_lags:
            price_lags = cls.compute_lag_features(prices, [1, 2, 3])
            if price_lags.size > 0:
                features.extend([price_lags[:, i] for i in range(price_lags.shape[1])])
                feature_names.extend(['price_lag_1', 'price_lag_2', 'price_lag_3'])
            
            # Lag returns
            if include_returns:
                log_returns = features[feature_names.index('log_returns')]
                return_lags = cls.compute_lag_features(log_returns, [1, 2, 3])
                if return_lags.size > 0:
                    features.extend([return_lags[:, i] for i in range(return_lags.shape[1])])
                    feature_names.extend(['returns_lag_1', 'returns_lag_2', 'returns_lag_3'])
        
        # 7. Rolling Statistics (price)
        if include_rolling:
            roll_mean_20, roll_std_20 = cls.compute_rolling_stats(prices, 20)
            roll_mean_50, roll_std_50 = cls.compute_rolling_stats(prices, 50)
            features.extend([roll_mean_20, roll_std_20, roll_mean_50, roll_std_50])
            feature_names.extend(['price_roll_mean_20', 'price_roll_std_20', 
                                  'price_roll_mean_50', 'price_roll_std_50'])
        
        # Stack all features
        feature_matrix = np.column_stack(features)
        
        logger.info(f"Created feature matrix with {len(feature_names)} features: {feature_names}")
        
        return feature_matrix, feature_names


# Convenience function for backward compatibility
def prepare_features_advanced(prices: np.ndarray, volumes: np.ndarray = None) -> np.ndarray:
    """
    Prepare advanced features for ML model (backward compatible)
    """
    feature_matrix, _ = FeatureEngine.create_feature_matrix(
        prices=prices,
        volumes=volumes,
        include_indicators=True,
        include_returns=True,
        include_volatility=True,
        include_volume_features=(volumes is not None),
        include_momentum=True,
        include_lags=True,
        include_rolling=True,
    )
    return feature_matrix

# ══════════════════════════════════════════════════════════════════════════
#  FEATURE BLOCK DESCRIPTORS  (FEATURE_SPECS)
#
#  Each FeatureEngine capability is exposed here as an individually
#  addressable block so the backend-authoritative block registry can publish a
#  populated FEATURE_ENGINEERING category (defect SB-03: the category exists in
#  the frontend palette with zero blocks registered in it).
#
#  This section is declarative only. It adds addressability; it does not change
#  create_feature_matrix or any existing computation.
# ══════════════════════════════════════════════════════════════════════════


class ParamType(str, Enum):
    """Control type of a block parameter, as consumed by the UI form generator."""

    NUMBER = "NUMBER"
    INTEGER = "INTEGER"
    TEXT = "TEXT"
    SELECT = "SELECT"
    MULTISELECT = "MULTISELECT"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    SYMBOL = "SYMBOL"
    TIMEFRAME = "TIMEFRAME"


class ExecutionSemantics(str, Enum):
    """How the runtime must drive the block."""

    SERIES_MAP = "SERIES_MAP"
    WINDOWED = "WINDOWED"
    STATEFUL = "STATEFUL"
    TERMINAL = "TERMINAL"


class LeakageRisk(str, Enum):
    """
    Leakage classification published for every FEATURE_ENGINEERING descriptor.

    NONE             reads bar t and earlier only, on every parameterisation
    LOW              leak-safe as configured, but a leaky configuration exists
                     (global statistics) which validation must reject
    REVIEW_REQUIRED  cannot be proven leak-safe from the graph alone; must not
                     sit on a path into a model without an explicit fit range
    """

    NONE = "NONE"
    LOW = "LOW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class FeaturePort:
    """One input or output port of a feature block."""

    name: str
    type: str
    required: bool = True
    variadic: bool = False
    description: str = ""


@dataclass(frozen=True)
class FeatureParamSpec:
    """Declarative description of one feature block parameter."""

    key: str
    label: str
    type: ParamType
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
    #: Names where a SELECT / MULTISELECT option set comes from when it cannot be
    #: enumerated at declaration time - `feat_select`'s columns are the upstream
    #: matrix's columns, known only once the graph is wired. Declared here rather
    #: than left as a bare `options=None`, so the registry can tell "resolved from
    #: the graph" apart from "option set forgotten".
    options_source: Optional[str] = None


@dataclass(frozen=True)
class FeatureSpec:
    """
    Descriptor for one addressable FEATURE_ENGINEERING block.

    Field names mirror `BlockDescriptor` so the registry can adapt a FeatureSpec
    without renaming anything.
    """

    block_id: str
    display_name: str
    description: str
    inputs: Tuple[FeaturePort, ...]
    outputs: Tuple[FeaturePort, ...]
    params: Tuple[FeatureParamSpec, ...]
    warmup_fn: Callable[[Mapping[str, Any]], int]
    runtime_ref: str
    leakage_risk: LeakageRisk
    execution_semantics: ExecutionSemantics = ExecutionSemantics.WINDOWED
    category: str = "FEATURE_ENGINEERING"
    allowed_predecessor_categories: Tuple[str, ...] = ()
    allowed_successor_categories: Tuple[str, ...] = (
        "FEATURE_ENGINEERING",
        "ML_DL",
        "MATH",
        "LOGIC",
    )
    serialization: str = "NONE"
    capability_flags: Tuple[str, ...] = ()
    version: str = "1.0.0"

    def param(self, key: str) -> Optional[FeatureParamSpec]:
        """Look up one ParamSpec by key."""
        for spec in self.params:
            if spec.key == key:
                return spec
        return None

    def default_params(self) -> Dict[str, Any]:
        """The parameter map a freshly dropped node starts with."""
        return {spec.key: spec.default for spec in self.params}

    def warmup(self, params: Optional[Mapping[str, Any]] = None) -> int:
        """Bars of history this block consumes before its output is trustworthy."""
        resolved = self.default_params()
        if params:
            resolved.update({k: v for k, v in params.items() if v is not None})
        return int(self.warmup_fn(resolved))


# ── shared port shapes ────────────────────────────────────────────────────

_MATRIX_OUT = (
    FeaturePort("matrix", "FEATURE_MATRIX", description="Feature columns with timestamp index"),
)
_SERIES_IN = (
    FeaturePort("series", "SCALAR_SERIES", description="Series to derive features from"),
)
_MATRIX_IN = (
    FeaturePort("matrix", "FEATURE_MATRIX", description="Feature matrix to transform"),
)
_FRAME_IN = (
    FeaturePort("frame", "OHLCV_FRAME", description="Candle frame"),
)

# A SCALAR_SERIES port can only be fed by a series producer; a FEATURE_MATRIX
# port can only be fed by another feature block. These sets are the R5 category
# adjacency check, derived from the port types rather than restated by hand.
_SERIES_PREDECESSORS = ("DATA", "INDICATOR", "MATH")
_MATRIX_PREDECESSORS = ("FEATURE_ENGINEERING",)
_FRAME_PREDECESSORS = ("DATA",)


# ── warmup functions ──────────────────────────────────────────────────────

def _warmup_zero(params: Mapping[str, Any]) -> int:
    """No history consumed: the block reads bar t only."""
    return 0


def _warmup_one(params: Mapping[str, Any]) -> int:
    """One previous bar consumed (differencing)."""
    return 1


def _warmup_window(params: Mapping[str, Any]) -> int:
    return int(params.get("window") or 0)


def _warmup_window_plus_one(params: Mapping[str, Any]) -> int:
    """Window of returns, and the returns themselves cost one bar."""
    return int(params.get("window") or 0) + 1


def _warmup_max_lags(params: Mapping[str, Any]) -> int:
    lags = params.get("lags") or [0]
    return int(max(int(lag) for lag in lags))


def _warmup_max_periods(params: Mapping[str, Any]) -> int:
    periods = params.get("periods") or [0]
    return int(max(int(period) for period in periods))


def _warmup_max_windows(params: Mapping[str, Any]) -> int:
    windows = params.get("windows") or [0]
    return int(max(int(window) for window in windows))


# ── cross-field validators ────────────────────────────────────────────────

def _validate_rolling_std(params: Mapping[str, Any]) -> List[str]:
    """ddof must leave at least one degree of freedom inside the window."""
    window = int(params.get("window") or 0)
    ddof = int(params.get("ddof") or 0)
    if window - ddof <= 0:
        return [f"window ({window}) must be greater than ddof ({ddof})"]
    return []


# ── the 15 feature blocks ─────────────────────────────────────────────────

FEATURE_SPECS: Tuple[FeatureSpec, ...] = (
    FeatureSpec(
        block_id="feat_lag",
        display_name="Lag",
        description="Past values of a series at the chosen offsets (t-1, t-2, ...).",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="lags",
                label="Lags",
                type=ParamType.MULTISELECT,
                required=True,
                default=[1, 2, 3],
                min=1,
                max=100,
                options=tuple(range(1, 101)),
                unit="bars",
                example=[1, 2, 3],
                help="How many bars back each lag column reads. Past values only.",
                affects_warmup=True,
            ),
        ),
        warmup_fn=_warmup_max_lags,
        runtime_ref="FeatureEngine.compute_lag_features",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_returns",
        display_name="Returns",
        description="Simple percentage change over one or more lookback periods.",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="periods",
                label="Periods",
                type=ParamType.MULTISELECT,
                required=True,
                default=[1],
                min=1,
                max=100,
                options=tuple(range(1, 101)),
                unit="bars",
                example=[1, 5],
                help="Each period produces one return column: (p[t] - p[t-n]) / p[t-n].",
                affects_warmup=True,
            ),
        ),
        warmup_fn=_warmup_max_periods,
        runtime_ref="FeatureEngine.compute_returns",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_log_returns",
        display_name="Log Returns",
        description="Natural log return of consecutive bars: ln(p[t] / p[t-1]).",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(),
        warmup_fn=_warmup_one,
        runtime_ref="FeatureEngine.compute_log_returns",
        leakage_risk=LeakageRisk.NONE,
        execution_semantics=ExecutionSemantics.SERIES_MAP,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_rolling_mean",
        display_name="Rolling Mean",
        description="Mean of the trailing window, ending at the current bar.",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="window",
                label="Window",
                type=ParamType.INTEGER,
                required=True,
                default=20,
                min=2,
                max=500,
                step=1,
                unit="bars",
                example=20,
                help="Bars averaged, inclusive of the current bar.",
                affects_warmup=True,
            ),
        ),
        warmup_fn=_warmup_window,
        runtime_ref="FeatureEngine.compute_rolling_mean",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_rolling_std",
        display_name="Rolling Std Dev",
        description="Standard deviation of the trailing window, ending at the current bar.",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="window",
                label="Window",
                type=ParamType.INTEGER,
                required=True,
                default=20,
                min=2,
                max=500,
                step=1,
                unit="bars",
                example=20,
                help="Bars in the deviation window, inclusive of the current bar.",
                affects_warmup=True,
            ),
            FeatureParamSpec(
                key="ddof",
                label="Delta degrees of freedom",
                type=ParamType.INTEGER,
                required=False,
                default=0,
                min=0,
                max=1,
                step=1,
                example=0,
                help="0 for population deviation, 1 for the sample estimate.",
                depends_on=("window",),
            ),
        ),
        warmup_fn=_warmup_window,
        runtime_ref="FeatureEngine.compute_rolling_std",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_volatility",
        display_name="Volatility",
        description="Rolling standard deviation of log returns, optionally annualized.",
        inputs=(
            FeaturePort(
                "series",
                "SCALAR_SERIES",
                description="Log-return series (feed a Log Returns block, not raw prices)",
            ),
        ),
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="window",
                label="Window",
                type=ParamType.INTEGER,
                required=True,
                default=20,
                min=2,
                max=500,
                step=1,
                unit="bars",
                example=20,
                help="Bars of returns in the volatility window.",
                affects_warmup=True,
            ),
            FeatureParamSpec(
                key="annualize",
                label="Annualize",
                type=ParamType.BOOLEAN,
                required=False,
                default=False,
                example=False,
                help="Scale by the square root of bars per year.",
            ),
        ),
        warmup_fn=_warmup_window_plus_one,
        runtime_ref="FeatureEngine.compute_volatility",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=("DATA", "INDICATOR", "MATH", "FEATURE_ENGINEERING"),
    ),
    FeatureSpec(
        block_id="feat_momentum",
        display_name="Momentum",
        description="Rate of change of a series over several lookback windows.",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="windows",
                label="Windows",
                type=ParamType.MULTISELECT,
                required=True,
                default=[5, 10, 20],
                min=1,
                max=500,
                options=tuple(range(1, 501)),
                unit="bars",
                example=[5, 10, 20],
                help="One momentum column per window: (p[t] - p[t-n]) / p[t-n].",
                affects_warmup=True,
            ),
        ),
        warmup_fn=_warmup_max_windows,
        runtime_ref="FeatureEngine.compute_price_momentum",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_zscore",
        display_name="Z-Score",
        description=(
            "Distance from the mean in standard deviations, computed over a "
            "trailing window or expanding history."
        ),
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="window",
                label="Window",
                type=ParamType.INTEGER,
                required=True,
                default=20,
                min=2,
                max=500,
                step=1,
                unit="bars",
                example=20,
                help="Bars used for the mean and deviation in rolling mode.",
                affects_warmup=True,
            ),
            FeatureParamSpec(
                key="mode",
                label="Statistics mode",
                type=ParamType.SELECT,
                required=True,
                default="rolling",
                options=("rolling", "expanding", "global"),
                example="rolling",
                help=(
                    "rolling and expanding read past bars only. global reads the "
                    "whole series and is rejected on any path into a model."
                ),
                depends_on=("window",),
            ),
        ),
        warmup_fn=_warmup_window,
        runtime_ref="FeatureEngine.compute_zscore",
        leakage_risk=LeakageRisk.LOW,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_normalize",
        display_name="Normalize",
        description="Rescale a series using trailing-window minmax or robust statistics.",
        inputs=_SERIES_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="window",
                label="Window",
                type=ParamType.INTEGER,
                required=True,
                default=20,
                min=2,
                max=500,
                step=1,
                unit="bars",
                example=20,
                help="Bars used for the scaling statistics.",
                affects_warmup=True,
            ),
            FeatureParamSpec(
                key="method",
                label="Method",
                type=ParamType.SELECT,
                required=True,
                default="minmax",
                options=("minmax", "robust"),
                example="minmax",
                help="minmax scales by window range; robust scales by median and IQR.",
            ),
            FeatureParamSpec(
                key="mode",
                label="Statistics mode",
                type=ParamType.SELECT,
                required=True,
                default="rolling",
                options=("rolling", "global"),
                example="rolling",
                help=(
                    "rolling reads past bars only. global reads the whole series "
                    "and is rejected on any path into a model."
                ),
                depends_on=("window",),
            ),
        ),
        warmup_fn=_warmup_window,
        runtime_ref="FeatureEngine.compute_normalize",
        leakage_risk=LeakageRisk.LOW,
        allowed_predecessor_categories=_SERIES_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_standardize",
        display_name="Standardize",
        description=(
            "Zero-mean unit-variance scaling of a whole feature matrix, with the "
            "scaler fit on the training split only."
        ),
        inputs=_MATRIX_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="fit_on",
                label="Fit on",
                type=ParamType.SELECT,
                required=True,
                default="train_split",
                options=("train_split",),
                example="train_split",
                help=(
                    "The scaler is fit on the training rows only. Fitting on the "
                    "full series would leak validation and test statistics."
                ),
            ),
        ),
        warmup_fn=_warmup_zero,
        runtime_ref="FeatureEngine.compute_standardize",
        leakage_risk=LeakageRisk.REVIEW_REQUIRED,
        execution_semantics=ExecutionSemantics.STATEFUL,
        allowed_predecessor_categories=_MATRIX_PREDECESSORS,
        capability_flags=("fit_on_train_split",),
    ),
    FeatureSpec(
        block_id="feat_time",
        display_name="Time Features",
        description="Calendar features from the bar timestamp: hour, weekday, month, session.",
        inputs=_FRAME_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="parts",
                label="Parts",
                type=ParamType.MULTISELECT,
                required=True,
                default=["hour", "dow"],
                options=("hour", "dow", "dom", "month", "session"),
                example=["hour", "dow", "session"],
                help="hour 0-23 UTC, dow 0=Monday, dom 1-31, month 1-12, session by UTC hour.",
            ),
        ),
        warmup_fn=_warmup_zero,
        runtime_ref="FeatureEngine.compute_time_features",
        leakage_risk=LeakageRisk.NONE,
        execution_semantics=ExecutionSemantics.SERIES_MAP,
        allowed_predecessor_categories=_FRAME_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_volume",
        display_name="Volume Features",
        description="Rolling volume mean, deviation and current-to-mean ratio.",
        inputs=(
            FeaturePort("volume", "SCALAR_SERIES", description="Volume series"),
        ),
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="window",
                label="Window",
                type=ParamType.INTEGER,
                required=True,
                default=20,
                min=2,
                max=500,
                step=1,
                unit="bars",
                example=20,
                help="Bars in the volume window, inclusive of the current bar.",
                affects_warmup=True,
            ),
        ),
        warmup_fn=_warmup_window,
        runtime_ref="FeatureEngine.compute_volume_features",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=("DATA",),
    ),
    FeatureSpec(
        block_id="feat_price_transform",
        display_name="Price Transform",
        description="Single-bar price blends: hl2, hlc3, ohlc4, typical price, log price.",
        inputs=_FRAME_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="transform",
                label="Transform",
                type=ParamType.SELECT,
                required=True,
                default="hlc3",
                options=("hl2", "hlc3", "ohlc4", "typical", "log"),
                example="hlc3",
                help="Computed from the current bar only, so no history is consumed.",
            ),
        ),
        warmup_fn=_warmup_zero,
        runtime_ref="FeatureEngine.compute_price_transform",
        leakage_risk=LeakageRisk.NONE,
        execution_semantics=ExecutionSemantics.SERIES_MAP,
        allowed_predecessor_categories=_FRAME_PREDECESSORS,
    ),
    FeatureSpec(
        block_id="feat_concat",
        display_name="Concat Features",
        description=(
            "Merge two or more feature matrices into one, aligned on the timestamp "
            "index rather than on row position."
        ),
        inputs=(
            FeaturePort(
                "matrix",
                "FEATURE_MATRIX",
                required=True,
                variadic=True,
                description="Two or more feature matrices (2..N)",
            ),
        ),
        outputs=_MATRIX_OUT,
        params=(),
        # Warmup is not a function of this block's own params: it is the worst
        # warmup among its inputs, resolved by the compiler from the upstream
        # nodes. The block itself consumes no extra history.
        warmup_fn=_warmup_zero,
        runtime_ref="FeatureEngine.concat_feature_matrices",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_MATRIX_PREDECESSORS,
        capability_flags=("warmup_from_inputs",),
    ),
    FeatureSpec(
        block_id="feat_select",
        display_name="Select Features",
        description="Keep a chosen subset of feature columns, in the chosen order.",
        inputs=_MATRIX_IN,
        outputs=_MATRIX_OUT,
        params=(
            FeatureParamSpec(
                key="columns",
                label="Columns",
                type=ParamType.MULTISELECT,
                required=True,
                default=None,
                options=None,  # resolved at edit time from the upstream matrix
                options_source="upstream_matrix_columns",
                example=["log_returns", "volatility_20"],
                help="Column names to keep. Unknown names are rejected, never dropped.",
            ),
        ),
        warmup_fn=_warmup_zero,
        runtime_ref="FeatureEngine.select_feature_columns",
        leakage_risk=LeakageRisk.NONE,
        allowed_predecessor_categories=_MATRIX_PREDECESSORS,
    ),
)


FEATURE_SPECS_BY_ID: Dict[str, FeatureSpec] = {spec.block_id: spec for spec in FEATURE_SPECS}

# Cross-field rules that ranges and enums cannot express.
FEATURE_PARAM_VALIDATORS: Dict[str, Callable[[Mapping[str, Any]], List[str]]] = {
    "feat_rolling_std": _validate_rolling_std,
}

# Configurations that are representable so that validation can reject them.
# Consumed by the leakage validator (GLOBAL_STATISTIC_LEAK).
GLOBAL_STATISTIC_BLOCKS: Tuple[str, ...] = ("feat_zscore", "feat_normalize")


def get_feature_spec(block_id: str) -> FeatureSpec:
    """Look up one feature descriptor. Raises KeyError for an unknown block."""
    return FEATURE_SPECS_BY_ID[block_id]


def resolve_feature_runtime(runtime_ref: str) -> Callable:
    """
    Resolve a descriptor's `runtime_ref` to the callable that implements it.

    Accepts "FeatureEngine.method" and bare module-level function names. Raises
    so the registry can fail startup naming the offender rather than advertising
    a block it cannot run.
    """
    if runtime_ref.startswith("FeatureEngine."):
        attribute = runtime_ref.split(".", 1)[1]
        target = getattr(FeatureEngine, attribute, None)
    else:
        attribute = runtime_ref.rsplit(".", 1)[-1]
        target = globals().get(attribute)

    if not callable(target):
        raise AttributeError(f"runtime_ref does not resolve to a callable: {runtime_ref}")
    return target


def validate_feature_params(block_id: str, params: Mapping[str, Any]) -> List[str]:
    """
    Check one node's params against its descriptor.

    Returns a list of human-readable problems; an empty list means the params are
    acceptable. Range, enum and required checks come from the ParamSpecs
    themselves, so the UI form and the backend cannot diverge.
    """
    spec = get_feature_spec(block_id)
    issues: List[str] = []

    for param in spec.params:
        value = params.get(param.key)

        if value is None:
            if param.required and param.default is None:
                issues.append(f"{spec.block_id}.{param.key} is required")
            continue

        values = value if isinstance(value, (list, tuple)) else [value]

        if param.options is not None:
            unknown = [v for v in values if v not in param.options]
            if unknown:
                issues.append(f"{spec.block_id}.{param.key} has invalid values: {unknown}")

        if param.type in (ParamType.INTEGER, ParamType.NUMBER, ParamType.MULTISELECT):
            for item in values:
                if not isinstance(item, (int, float)) or isinstance(item, bool):
                    continue
                if param.min is not None and item < param.min:
                    issues.append(f"{spec.block_id}.{param.key} below minimum {param.min}: {item}")
                if param.max is not None and item > param.max:
                    issues.append(f"{spec.block_id}.{param.key} above maximum {param.max}: {item}")

    validator = FEATURE_PARAM_VALIDATORS.get(block_id)
    if validator is not None:
        resolved = spec.default_params()
        resolved.update({k: v for k, v in params.items() if v is not None})
        issues.extend(validator(resolved))

    return issues
