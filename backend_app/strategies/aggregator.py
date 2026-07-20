"""
Multi-Strategy Signal Aggregator
Combines multiple strategy signals into one final decision using majority voting
"""

import pandas as pd
from typing import List


class StrategyAggregator:
    """
    Aggregates signals from multiple strategies using majority voting.
    
    For each timestamp, the final signal is determined by:
    - Entry: if majority of strategies say "enter"
    - Exit: if majority of strategies say "exit"
    """
    
    def __init__(self, strategies: List):
        """
        Initialize with list of strategy instances.
        
        Args:
            strategies: List of instantiated strategy objects
        """
        self.strategies = strategies
    
    def generate_signals(self, df: pd.DataFrame):
        """
        Generate combined entry/exit signals from all strategies.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            entries: pd.Series of boolean entry signals
            exits: pd.Series of boolean exit signals
        """
        all_entries = []
        all_exits = []
        
        # Collect signals from each strategy
        for strategy in self.strategies:
            entries, exits = strategy.generate_signals(df)
            all_entries.append(entries.astype(int))
            all_exits.append(exits.astype(int))
        
        # Combine into DataFrames for easier aggregation
        entries_df = pd.concat(all_entries, axis=1)
        exits_df = pd.concat(all_exits, axis=1)
        
        # Majority voting: signal is true if >50% of strategies agree
        final_entries = entries_df.sum(axis=1) > (len(self.strategies) / 2)
        final_exits = exits_df.sum(axis=1) > (len(self.strategies) / 2)
        
        return final_entries, final_exits
