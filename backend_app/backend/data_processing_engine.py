"""
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 — BACKEND: data_processing_engine.py                            ║
║                                                                          ║
║  Numba tick→candle aggregator + circular buffer for ML feature matrix.   ║
║  Pure computation — zero HTTP/WS code.                                   ║
║                                                                          ║
║  BUGS FIXED:                                                             ║
║  DP-1  warm_up_history set bar_starts to bar OPEN not CLOSE timestamp   ║
║  DP-2  get_historical_matrix_for_ml: IndexError on unknown timeframe     ║
║  DP-3  process_live_tick: no guard against double ms vs seconds input    ║
║  DP-4  No thread-safety for concurrent tick processing                   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import logging
import threading

import numpy as np
from numba import njit

logger = logging.getLogger("DataProcessorEngine")


# ══════════════════════════════════════════════════════════════════════════
#  NUMBA CORE — C-speed tick aggregation
# ══════════════════════════════════════════════════════════════════════════


@njit(cache=True)
def _process_tick_numba(
    sym_id,
    timestamp,
    price,
    volume,
    bar_starts,
    opens,
    highs,
    lows,
    closes,
    volumes,
    timeframes,
):
    """
    Aggregates raw ticks into OHLCV bars for multiple timeframes simultaneously.
    Returns a matrix of completed bars this tick triggered.
    Uses a while loop to fill ghost candles during data gaps (FIX from original).
    """
    num_tf = len(timeframes)
    completed_bars = np.zeros((num_tf * 10, 7), dtype=np.float64)
    count = 0

    for tf_idx in range(num_tf):
        tf_sec = timeframes[tf_idx]
        bar_start = bar_starts[sym_id, tf_idx]

        # First bar ever for this symbol+timeframe
        if bar_start == 0:
            aligned = (timestamp // tf_sec) * tf_sec
            bar_starts[sym_id, tf_idx] = aligned
            opens[sym_id, tf_idx] = price
            highs[sym_id, tf_idx] = price
            lows[sym_id, tf_idx] = price
            closes[sym_id, tf_idx] = price
            volumes[sym_id, tf_idx] = volume
            continue

        # Bar rollover — use while to fill any data gaps with ghost candles
        while timestamp >= bar_starts[sym_id, tf_idx] + tf_sec:
            completed_bars[count, 0] = tf_idx
            completed_bars[count, 1] = bar_starts[sym_id, tf_idx]
            completed_bars[count, 2] = opens[sym_id, tf_idx]
            completed_bars[count, 3] = highs[sym_id, tf_idx]
            completed_bars[count, 4] = lows[sym_id, tf_idx]
            completed_bars[count, 5] = closes[sym_id, tf_idx]
            completed_bars[count, 6] = volumes[sym_id, tf_idx]
            count += 1

            bar_starts[sym_id, tf_idx] += tf_sec
            last_close = closes[sym_id, tf_idx]
            opens[sym_id, tf_idx] = last_close
            highs[sym_id, tf_idx] = last_close
            lows[sym_id, tf_idx] = last_close
            closes[sym_id, tf_idx] = last_close
            volumes[sym_id, tf_idx] = 0.0

        # Normal tick update inside the forming candle
        if price > highs[sym_id, tf_idx]:
            highs[sym_id, tf_idx] = price
        if price < lows[sym_id, tf_idx]:
            lows[sym_id, tf_idx] = price
        closes[sym_id, tf_idx] = price
        volumes[sym_id, tf_idx] += volume

    return completed_bars[:count]


# ══════════════════════════════════════════════════════════════════════════
#  PYTHON WRAPPER — Circular Buffer + Thread Safety
# ══════════════════════════════════════════════════════════════════════════


class DataProcessorEngine:
    """
    Manages a circular OHLCV buffer per (symbol, timeframe) pair.
    Thread-safe: each call to process_live_tick acquires a per-symbol lock.
    """

    def __init__(
        self, symbols_list: list, timeframes_sec: list, max_history: int = 1_000
    ):
        self.num_symbols = len(symbols_list)
        self.num_timeframes = len(timeframes_sec)
        self.max_history = max_history
        self.timeframes = np.array(timeframes_sec, dtype=np.int64)
        self.symbol_map = {sym: i for i, sym in enumerate(symbols_list)}

        # Numba live state
        self.bar_starts = np.zeros(
            (self.num_symbols, self.num_timeframes), dtype=np.int64
        )
        self.opens = np.zeros((self.num_symbols, self.num_timeframes), dtype=np.float64)
        self.highs = np.zeros((self.num_symbols, self.num_timeframes), dtype=np.float64)
        self.lows = np.zeros((self.num_symbols, self.num_timeframes), dtype=np.float64)
        self.closes = np.zeros(
            (self.num_symbols, self.num_timeframes), dtype=np.float64
        )
        self.volumes = np.zeros(
            (self.num_symbols, self.num_timeframes), dtype=np.float64
        )

        # Circular buffer: [sym, tf, position, OHLCV(6)]
        self.history = np.zeros(
            (self.num_symbols, self.num_timeframes, max_history, 6), dtype=np.float64
        )
        self.history_counts = np.zeros(
            (self.num_symbols, self.num_timeframes), dtype=np.int32
        )
        self.pointers = np.zeros(
            (self.num_symbols, self.num_timeframes), dtype=np.int32
        )

        # FIX DP-4: Per-symbol lock to prevent concurrent tick corruption
        self._locks = {sym: threading.Lock() for sym in symbols_list}

    # ── Warm-up ────────────────────────────────────────────────────────────

    def warm_up_history(self, symbol: str, timeframe_sec: int, ccxt_ohlcv: list):
        """
        Loads historical OHLCV bars from CCXT into the circular buffer.
        FIX DP-1: Sets bar_starts to bar CLOSE time (open + tf_sec),
                  not bar OPEN time. This prevents forming-bar duplication
                  after a bot restart.
        """
        sym_id = self._resolve_symbol(symbol)
        tf_idx = self._resolve_timeframe(timeframe_sec)
        if sym_id is None or tf_idx is None:
            return

        bars = ccxt_ohlcv[-self.max_history :]
        if not bars:
            return

        for bar in bars:
            ts_sec = int(bar[0] / 1000)
            bar_arr = np.array(
                [ts_sec, bar[1], bar[2], bar[3], bar[4], bar[5]], dtype=np.float64
            )
            ptr = self.pointers[sym_id, tf_idx]
            self.history[sym_id, tf_idx, ptr] = bar_arr
            self.pointers[sym_id, tf_idx] = (ptr + 1) % self.max_history
            if self.history_counts[sym_id, tf_idx] < self.max_history:
                self.history_counts[sym_id, tf_idx] += 1

        # FIX DP-1: point bar_start at the END of the last historical bar
        last_bar_open_ts = int(bars[-1][0] / 1000)
        self.bar_starts[sym_id, tf_idx] = last_bar_open_ts + timeframe_sec  # ← FIX

        # Set forming-bar state from last historical bar
        self.opens[sym_id, tf_idx] = float(bars[-1][1])
        self.highs[sym_id, tf_idx] = float(bars[-1][2])
        self.lows[sym_id, tf_idx] = float(bars[-1][3])
        self.closes[sym_id, tf_idx] = float(bars[-1][4])
        self.volumes[sym_id, tf_idx] = float(bars[-1][5])

        logger.info(f"Warmed up {symbol}/{timeframe_sec}s with {len(bars)} bars.")

    # ── Live Tick ──────────────────────────────────────────────────────────

    def process_live_tick(
        self, symbol: str, timestamp_ms: int, price: float, volume: float
    ) -> list:
        """
        Feeds a raw trade tick into the Numba aggregator.
        FIX DP-3: Auto-detects if timestamp is in ms or seconds.
        FIX DP-4: Thread-safe via per-symbol lock.
        Returns list of completed candle dicts (may be empty).
        """
        sym_id = self._resolve_symbol(symbol)
        if sym_id is None:
            return []

        # FIX DP-3: normalise to seconds regardless of exchange precision
        if timestamp_ms > 9_999_999_999:
            timestamp_sec = int(timestamp_ms / 1000)
        else:
            timestamp_sec = int(timestamp_ms)

        with self._locks.get(symbol, threading.Lock()):
            finished = _process_tick_numba(
                sym_id,
                timestamp_sec,
                float(price),
                float(volume),
                self.bar_starts,
                self.opens,
                self.highs,
                self.lows,
                self.closes,
                self.volumes,
                self.timeframes,
            )

            results = []
            for row in finished:
                tf_idx = int(row[0])
                bar_data = row[1:7]

                # Insert into circular buffer
                ptr = self.pointers[sym_id, tf_idx]
                self.history[sym_id, tf_idx, ptr] = bar_data
                self.pointers[sym_id, tf_idx] = (ptr + 1) % self.max_history
                if self.history_counts[sym_id, tf_idx] < self.max_history:
                    self.history_counts[sym_id, tf_idx] += 1

                results.append(
                    {
                        "symbol": symbol,
                        "timeframe_sec": int(self.timeframes[tf_idx]),
                        "timestamp": int(bar_data[0]),
                        "open": float(bar_data[1]),
                        "high": float(bar_data[2]),
                        "low": float(bar_data[3]),
                        "close": float(bar_data[4]),
                        "volume": float(bar_data[5]),
                    }
                )

        return results

    # ── History Retrieval ──────────────────────────────────────────────────

    def get_historical_matrix_for_ml(
        self, symbol: str, timeframe_sec: int
    ) -> np.ndarray:
        """
        Unwinds the circular buffer into chronologically ordered array.
        FIX DP-2: Returns empty array (not IndexError) if timeframe not registered.
        Shape: (n_bars, 6) — [timestamp, open, high, low, close, volume]
        """
        sym_id = self._resolve_symbol(symbol)
        tf_idx = self._resolve_timeframe(timeframe_sec)

        if sym_id is None or tf_idx is None:
            logger.warning(f"Unknown symbol '{symbol}' or timeframe {timeframe_sec}s.")
            return np.empty((0, 6), dtype=np.float64)  # FIX DP-2

        count = int(self.history_counts[sym_id, tf_idx])
        if count == 0:
            return np.empty((0, 6), dtype=np.float64)

        ptr = int(self.pointers[sym_id, tf_idx])

        if count < self.max_history:
            return self.history[sym_id, tf_idx, :ptr].copy()
        else:
            # Unwrap the circle: oldest is at ptr, newest is at ptr-1
            return np.concatenate(
                [
                    self.history[sym_id, tf_idx, ptr:],
                    self.history[sym_id, tf_idx, :ptr],
                ]
            )

    def get_latest_close(self, symbol: str, timeframe_sec: int) -> float | None:
        """Returns the close price of the most recently completed bar."""
        matrix = self.get_historical_matrix_for_ml(symbol, timeframe_sec)
        if len(matrix) == 0:
            return None
        return float(matrix[-1, 4])  # column 4 = close

    # ── Helpers ────────────────────────────────────────────────────────────

    def _resolve_symbol(self, symbol: str) -> int | None:
        idx = self.symbol_map.get(symbol)
        if idx is None:
            logger.error(f"Symbol '{symbol}' not registered in DataProcessorEngine.")
        return idx

    def _resolve_timeframe(self, timeframe_sec: int) -> int | None:
        """FIX DP-2: Returns None instead of crashing with IndexError."""
        matches = np.where(self.timeframes == timeframe_sec)[0]
        if len(matches) == 0:
            logger.error(
                f"Timeframe {timeframe_sec}s not registered. "
                f"Registered: {self.timeframes.tolist()}"
            )
            return None
        return int(matches[0])
