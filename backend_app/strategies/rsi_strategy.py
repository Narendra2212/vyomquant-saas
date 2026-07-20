"""
RSI-based trading strategy implementation with ML model integration.
"""

import numpy as np
import pandas as pd
from typing import Dict, Any, Tuple
from backend_app.strategies.base import BaseStrategy


class RSIStrategy(BaseStrategy):
    """RSI (Relative Strength Index) trading strategy with ML model prediction."""
    
    def __init__(self, params: Dict[str, Any] = None):
        """
        Initialize RSI strategy with parameters.
        
        Args:
            params: Dictionary containing:
                - period: RSI period (default: 14)
                - entry_threshold: RSI level for entry (default: 45)
                - exit_threshold: RSI level for exit (default: 55)
                - model_path: Path to ML model file (optional)
                - use_ml: Whether to use ML model for signals (default: False)
        """
        super().__init__(params)
        self.period = self.params.get("period", 14)
        self.entry_threshold = self.params.get("entry_threshold", 45)
        self.exit_threshold = self.params.get("exit_threshold", 55)
        self.model_path = self.params.get("model_path", None)
        self.use_ml = self.params.get("use_ml", False)
        self.buy_threshold = self.params.get("buy_threshold", 0.6)
        self.sell_threshold = self.params.get("sell_threshold", 0.4)
        self.model = None
        
        # Load ML model if path provided and use_ml is True
        if self.use_ml and self.model_path:
            self._load_ml_model()
    
    def _load_ml_model(self):
        """Load ML model from file."""
        try:
            from backend_app.backend.ml_models import create_ml_block
            self.model = create_ml_block(self.model_path, models_dir="test_models/")
            self.model.load_model_to_memory(self.model_path)
            print(f"✅ ML model loaded: {self.model_path}")
        except Exception as e:
            print(f"❌ Failed to load ML model: {e}")
            self.use_ml = False
    
    def _prepare_features(self, data: pd.DataFrame) -> np.ndarray:
        """
        Prepare advanced features for ML model prediction.
        
        Args:
            data: DataFrame containing OHLCV data
            
        Returns:
            Feature matrix for ML model (25 features)
        """
        from backend_app.backend.feature_engineering import FeatureEngine
        
        close = data["close"].values
        volume = data.get("volume", pd.Series(np.ones(len(data)))).values
        
        # Use advanced feature engineering
        feature_matrix, self.feature_names = FeatureEngine.create_feature_matrix(
            prices=close,
            volumes=volume,
            include_indicators=True,
            include_returns=True,
            include_volatility=True,
            include_volume_features=True,
            include_momentum=True,
            include_lags=True,
            include_rolling=True,
        )
        
        return feature_matrix
    
    def _calculate_rsi(self, closes: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate RSI as pandas Series.
        
        Args:
            closes: Series of closing prices
            period: RSI calculation period
            
        Returns:
            Series of RSI values
        """
        if len(closes) < period + 1:
            return pd.Series([50.0] * len(closes), index=closes.index)
        
        deltas = closes.diff()
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gains = pd.Series(gains).rolling(window=period).mean()
        avg_losses = pd.Series(losses).rolling(window=period).mean()
        
        rs = np.divide(avg_gains, avg_losses, out=np.zeros_like(avg_gains), where=avg_losses != 0)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        
        # Fill initial values with 50
        rsi[:period] = 50.0
        
        return rsi
    
    def generate_signals(self, data: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """
        Generate entry and exit signals based on RSI or ML model prediction.
        
        Args:
            data: DataFrame containing OHLCV data with 'close' column
            
        Returns:
            Tuple of (entries, exits) as pandas Series with boolean values
        """
        close = data["close"]
        
        if self.use_ml and self.model is not None:
            # Use ML model for signals with improved thresholding
            print("🤖 Using ML model for signal generation")
            
            # Prepare features
            features = self._prepare_features(data)
            
            # Get predictions
            predictions = []
            for i in range(len(features)):
                if i < 50:  # Need enough history for indicators
                    predictions.append(0.5)
                else:
                    pred = self.model.live_inference(features[:i+1])
                    predictions.append(pred)
            
            predictions = np.array(predictions)
            
            print(f"🤖 FILTERED SIGNAL: {predictions[-1]:.4f}")
            
            # Improved thresholding logic
            # Entry: prediction > buy_threshold (strong bullish signal)
            entries = pd.Series(predictions > self.buy_threshold, index=data.index)
            
            # Exit: prediction < sell_threshold (strong bearish signal)
            exits = pd.Series(predictions < self.sell_threshold, index=data.index)
            
            # No trade zone: between sell_threshold and buy_threshold
            no_trade = (predictions >= self.sell_threshold) & (predictions <= self.buy_threshold)
            print(f"   Buy threshold: {self.buy_threshold}, Sell threshold: {self.sell_threshold}")
            print(f"   No trade zone: {no_trade.sum()} signals")
            
        else:
            # Use traditional RSI threshold logic
            print("📊 Using RSI threshold logic")
            
            # Calculate RSI using simplified logic
            delta = close.diff()
            
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            
            rs = gain / (loss + 1e-9)
            rsi = 100 - (100 / (1 + rs))
            
            # Entry: oversold → RSI < 30
            entries = rsi < 30
            
            # Exit: overbought → RSI > 70
            exits = rsi > 70
        
        # Remove consecutive duplicates
        entries = entries & (~entries.shift(1).fillna(False))
        exits = exits & (~exits.shift(1).fillna(False))
        
        return entries, exits
