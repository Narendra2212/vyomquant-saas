"""
Live Trading Engine for Paper Trading
Runs trading logic continuously in real-time
"""

import time
import threading
from typing import Dict, List, Callable, Optional
from datetime import datetime
import pandas as pd

from backend_app.core.portfolio_engine import PortfolioEngine
from backend_app.core.execution_engine import ExecutionEngine
from backend_app.core.risk_engine import RiskEngine
from backend_app.strategies.aggregator import StrategyAggregator


class LiveTradingEngine:
    """
    Live trading engine that runs strategies continuously on real-time data.
    
    Features:
    - Multi-symbol portfolio trading
    - Real-time signal generation
    - Risk management checks
    - Position tracking and execution
    - Graceful start/stop
    """
    
    def __init__(
        self,
        strategies: List,
        symbols: List[str],
        portfolio: PortfolioEngine,
        risk: RiskEngine,
        execution: ExecutionEngine,
        fetch_data: Callable[[str], pd.DataFrame],
        aggregator: Optional[StrategyAggregator] = None,
        ml_model_path: Optional[str] = None
    ):
        """
        Initialize LiveTradingEngine.
        
        Args:
            strategies: List of strategy instances
            symbols: List of symbols to trade
            portfolio: PortfolioEngine instance
            risk: RiskEngine instance
            execution: ExecutionEngine instance
            fetch_data: Function to fetch data for a symbol
            aggregator: Optional StrategyAggregator (created if not provided)
            ml_model_path: Optional path to ML model file for inference
        """
        self.strategies = strategies
        self.symbols = symbols
        self.portfolio = portfolio
        self.risk = risk
        self.execution = execution
        self.fetch_data = fetch_data
        self.ml_model_path = ml_model_path
        self.ml_model = None
        
        # Load ML model if path provided
        if ml_model_path:
            self._load_ml_model()
        
        # Create aggregator if multiple strategies
        if aggregator:
            self.aggregator = aggregator
        elif len(strategies) > 1:
            self.aggregator = StrategyAggregator(strategies)
        else:
            self.aggregator = strategies[0] if strategies else None
        
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.interval = 10  # Default interval in seconds
        self.start_time: Optional[datetime] = None
        self.iteration_count = 0
        
        # Statistics
        self.errors = []
        self.trades_executed = 0
        self.ml_signals = []  # Track ML signals
    
    def _load_ml_model(self):
        """Load ML model from file."""
        try:
            from backend_app.backend.ml_models import create_ml_block
            self.ml_model = create_ml_block(self.ml_model_path, models_dir="test_models/")
            self.ml_model.load_model_to_memory(self.ml_model_path)
            print(f"✅ ML model loaded: {self.ml_model_path}")
        except Exception as e:
            print(f"❌ Failed to load ML model: {e}")
            self.ml_model = None
    
    def _prepare_features(self, df: pd.DataFrame) -> np.ndarray:
        """
        Prepare advanced features for ML model prediction.
        
        Args:
            df: DataFrame containing OHLCV data
            
        Returns:
            Feature matrix for ML model (25 features)
        """
        from backend_app.backend.feature_engineering import FeatureEngine
        
        close = df["close"].values
        volume = df.get("volume", pd.Series(np.ones(len(df)))).values
        
        # Use advanced feature engineering with all features
        feature_matrix, _ = FeatureEngine.create_feature_matrix(
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
    
    # ----------------------------------
    # START ENGINE
    # ----------------------------------
    def start(self, interval: int = 10) -> bool:
        """
        Start the live trading engine in a background thread.
        
        Args:
            interval: Seconds between trading cycles (default: 10)
            
        Returns:
            True if started successfully, False otherwise
        """
        if self.running:
            print("⚠️ Engine already running")
            return False
        
        self.interval = interval
        self.running = True
        self.start_time = datetime.now()
        
        # Start in background thread
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        print(f"🚀 Live Trading Engine started")
        print(f"   Symbols: {self.symbols}")
        print(f"   Interval: {interval}s")
        print(f"   Initial Equity: {self.risk.current_equity:.2f}")
        
        return True
    
    # ----------------------------------
    # MAIN LOOP
    # ----------------------------------
    def _run_loop(self) -> None:
        """Main trading loop - runs continuously until stopped"""
        while self.running:
            self.iteration_count += 1
            cycle_start = time.time()
            
            try:
                self._trading_cycle()
                
            except Exception as e:
                error_msg = f"Cycle {self.iteration_count}: {str(e)}"
                self.errors.append((datetime.now(), error_msg))
                print(f"🔥 ERROR: {error_msg}")
                
                # Stop if too many errors
                if len(self.errors) > 10:
                    print("⚠️ Too many errors, stopping engine")
                    self.stop()
                    return
            
            # Sleep for remaining interval time
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.interval - elapsed)
            time.sleep(sleep_time)
    
    # ----------------------------------
    # TRADING CYCLE
    # ----------------------------------
    def _trading_cycle(self) -> None:
        """Execute one trading cycle"""
        print(f"\n📊 Cycle #{self.iteration_count} - {datetime.now().strftime('%H:%M:%S')}")
        
        # Fetch market data for all symbols
        market_data: Dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            try:
                df = self.fetch_data(symbol)
                if df is not None and len(df) > 0:
                    market_data[symbol] = df
            except Exception as e:
                print(f"⚠️ Failed to fetch {symbol}: {e}")
        
        if not market_data:
            print("⚠️ No market data available")
            return
        
        # Generate signals for each symbol
        signals: Dict[str, float] = {}
        for symbol, df in market_data.items():
            try:
                if len(df) < 50:  # Minimum for ML indicators
                    print(f"⚠️ Insufficient data for {symbol}: {len(df)} rows")
                    continue
                
                # Use ML model if available
                if self.ml_model is not None:
                    print(f"🤖 Running ML inference for {symbol}...")
                    
                    # Prepare features
                    features = self._prepare_features(df)
                    
                    # Run inference
                    ml_signal = self.ml_model.live_inference(features)
                    
                    print(f"🤖 LIVE SIGNAL: {ml_signal:.4f}")
                    
                    # Track signal
                    self.ml_signals.append({
                        "cycle": self.iteration_count,
                        "symbol": symbol,
                        "signal": ml_signal,
                        "timestamp": datetime.now().isoformat()
                    })
                    
                    # Convert to entry signal (1.0 if > 0.5)
                    signals[symbol] = 1.0 if ml_signal > 0.5 else 0.0
                    
                else:
                    # Use traditional strategy
                    entries, exits = self.aggregator.generate_signals(df)
                    
                    # Current signal (1.0 = entry, 0.0 = no signal)
                    if entries.iloc[-1]:
                        signals[symbol] = 1.0
                    else:
                        signals[symbol] = 0.0
                    
            except Exception as e:
                print(f"⚠️ Signal error for {symbol}: {e}")
                signals[symbol] = 0.0
        
        # Skip if no signals
        if not signals:
            print("⚠️ No signals generated")
            return
        
        # Check risk limits before trading
        if not self.risk.can_trade():
            print("🚫 Risk limits exceeded - skipping trading")
            return
        
        # Allocate capital based on signals
        allocation = self.portfolio.allocate(signals)
        print(f"💰 Allocations: {allocation}")
        
        # Execute trades
        for symbol, capital in allocation.items():
            if symbol not in market_data:
                continue
            
            try:
                current_price = market_data[symbol]["close"].iloc[-1]
                has_position = symbol in self.execution.get_positions()
                
                # Entry: signal present, no position
                if signals.get(symbol, 0) > 0 and not has_position:
                    size = capital / current_price
                    if size > 0:
                        success, msg = self.execution.open_position(
                            symbol, current_price, size, "long"
                        )
                        if success:
                            self.trades_executed += 1
                            self.portfolio.update_position(symbol, size, current_price)
                            print(f"  ✅ OPEN {symbol}: size={size:.4f}, price={current_price:.2f}")
                        else:
                            print(f"  ❌ OPEN failed {symbol}: {msg}")
                
                # Exit: no signal, has position
                elif signals.get(symbol, 0) == 0 and has_position:
                    pnl, msg = self.execution.close_position(symbol, current_price)
                    self.portfolio.close_position(symbol)
                    self.risk.update_equity(pnl)
                    self.trades_executed += 1
                    print(f"  ✅ CLOSE {symbol}: PnL={pnl:.2f}")
                    
            except Exception as e:
                print(f"⚠️ Execution error for {symbol}: {e}")
        
        # Print status
        print(f"💵 Equity: {self.risk.current_equity:.2f} | Drawdown: {self.risk.get_drawdown():.2%}")
        print(f"📈 Trades: {self.trades_executed} | Positions: {len(self.execution.get_positions())}")
    
    # ----------------------------------
    # STOP ENGINE
    # ----------------------------------
    def stop(self) -> bool:
        """
        Stop the live trading engine gracefully.
        
        Returns:
            True if stopped successfully
        """
        if not self.running:
            print("⚠️ Engine not running")
            return False
        
        self.running = False
        print("🛑 Stopping Live Trading Engine...")
        
        # Wait for thread to finish (max 5 seconds)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        # Close all positions
        self._close_all_positions()
        
        # Print summary
        self._print_summary()
        
        return True
    
    # ----------------------------------
    # CLOSE ALL POSITIONS
    # ----------------------------------
    def _close_all_positions(self) -> None:
        """Close all open positions at current prices"""
        print("📉 Closing all positions...")
        
        for symbol in list(self.execution.get_positions().keys()):
            try:
                # Fetch current price
                df = self.fetch_data(symbol)
                if df is not None and len(df) > 0:
                    current_price = df["close"].iloc[-1]
                    pnl, msg = self.execution.close_position(symbol, current_price)
                    self.risk.update_equity(pnl)
                    print(f"  CLOSED {symbol}: PnL={pnl:.2f}")
            except Exception as e:
                print(f"⚠️ Failed to close {symbol}: {e}")
    
    # ----------------------------------
    # PRINT SUMMARY
    # ----------------------------------
    def _print_summary(self) -> None:
        """Print trading session summary"""
        stats = self.execution.get_stats()
        
        print("\n" + "=" * 50)
        print("📊 TRADING SESSION SUMMARY")
        print("=" * 50)
        
        if self.start_time:
            duration = datetime.now() - self.start_time
            print(f"⏱️ Duration: {duration}")
        
        print(f"🔄 Cycles: {self.iteration_count}")
        print(f"📈 Total Trades: {stats['total_trades']}")
        print(f"🏆 Win Rate: {stats['win_rate']:.2%}")
        print(f"💰 Total PnL: {stats['total_pnl']:.2f}")
        print(f"💵 Final Equity: {stats['current_equity']:.2f}")
        print(f"📉 Max Drawdown: {stats['drawdown_pct']:.2%}")
        print(f"💸 Total Fees: {stats['total_commission']:.2f}")
        
        if self.errors:
            print(f"⚠️ Errors: {len(self.errors)}")
        
        print("=" * 50)
    
    # ----------------------------------
    # GET STATUS
    # ----------------------------------
    def get_status(self) -> Dict:
        """
        Get current engine status.
        
        Returns:
            Dictionary with current status
        """
        return {
            "running": self.running,
            "iteration_count": self.iteration_count,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "interval": self.interval,
            "trades_executed": self.trades_executed,
            "error_count": len(self.errors),
            "equity": self.risk.current_equity,
            "drawdown_pct": self.risk.get_drawdown(),
            "positions": len(self.execution.get_positions()),
            "can_trade": self.risk.can_trade()
        }
    
    # ----------------------------------
    # GET POSITIONS
    # ----------------------------------
    def get_positions(self) -> Dict:
        """
        Get current open positions.
        
        Returns:
            Dictionary of positions
        """
        return self.execution.get_positions()
