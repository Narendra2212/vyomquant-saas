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

import numpy as np

logger = logging.getLogger("FeatureEngineering")


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
    def compute_volatility(log_returns: np.ndarray, window: int = 20) -> np.ndarray:
        """
        Compute rolling volatility (standard deviation of log returns)
        First (window-1) values are NaN
        """
        # Using convolution for rolling std
        n = len(log_returns)
        volatility = np.full(n, np.nan)
        
        for i in range(window, n):
            window_data = log_returns[i-window+1:i+1]
            if not np.all(np.isnan(window_data)):
                volatility[i] = np.nanstd(window_data)
        
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
