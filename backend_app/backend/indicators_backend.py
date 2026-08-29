"""
Aerora compatibility indicator library.

This module is intentionally valid and self-contained so the trading backend
can boot reliably. The implementations are lightweight NumPy versions that
return array-shaped outputs compatible with the rest of the codebase.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

EPS = 1e-12

# NOTE: `AVAILABLE_INDICATORS` is no longer a hand-maintained list. It is derived
# from `INDICATOR_SPECS` at the bottom of this module, so an implementation can
# never again be runnable but unselectable (defect SB-04).


def _as_float_array(x):
    return np.asarray(x, dtype=np.float64)


def _nan_array(n):
    return np.full(int(n), np.nan, dtype=np.float64)


def _rolling_mean(a, window):
    a = _as_float_array(a)
    n = len(a)
    out = _nan_array(n)
    if window < 1 or n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.mean(a[i - window + 1 : i + 1])
    return out


def _rolling_std(a, window, ddof=0):
    a = _as_float_array(a)
    n = len(a)
    out = _nan_array(n)
    if window < 2 or n < window:
        return out
    for i in range(window - 1, n):
        out[i] = np.std(a[i - window + 1 : i + 1], ddof=ddof)
    return out


def _ema_after_warmup(a, window):
    """EMA of a series that itself begins with a warmup region.

    ``ema`` seeds from the *first* ``window`` values, so one leading NaN NaNs the seed and
    the recursion then carries that NaN to the end of the series. Smoothing the defined tail
    and leaving the warmup NaN is the honest answer: the smoothed series starts once it has
    ``window`` defined observations, and the bars before that stay undefined rather than
    being replaced by a substituted number that a comparison would treat as real.

    Only ``adx`` needs this today - it is the one indicator that smooths a series which is
    itself an EMA ratio. ``macd`` and ``trix`` substitute zero for their inner warmup
    instead; that is their existing published behaviour and is not changed here.
    """
    a = _as_float_array(a)
    out = _nan_array(len(a))
    defined = np.flatnonzero(~np.isnan(a))
    if defined.size == 0:
        return out
    start = int(defined[0])
    out[start:] = ema(a[start:], int(window))
    return out


def shift_array(arr, periods, fill_value=np.nan):
    arr = _as_float_array(arr)
    n = len(arr)
    out = np.full(n, fill_value, dtype=np.float64)
    periods = int(periods)
    if periods > 0:
        out[periods:] = arr[:-periods]
    elif periods < 0:
        out[:periods] = arr[-periods:]
    else:
        out[:] = arr.copy()
    return out


def sma(close, window=20):
    return _rolling_mean(close, int(window))


def ema(close, window=14):
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    window = int(window)
    if window < 1 or n < window:
        return out
    out[window - 1] = np.mean(close[:window])
    k = 2.0 / (window + 1.0)
    for i in range(window, n):
        out[i] = (close[i] - out[i - 1]) * k + out[i - 1]
    return out


def wma(close, window=9):
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    window = int(window)
    if window < 1 or n < window:
        return out
    weights = np.arange(1.0, window + 1.0)
    weight_sum = np.sum(weights)
    for i in range(window - 1, n):
        out[i] = np.sum(close[i - window + 1 : i + 1] * weights) / weight_sum
    return out


def hma(close, window=14):
    close = _as_float_array(close)
    window = int(window)
    half = max(1, window // 2)
    sqrt_w = max(1, int(np.sqrt(window)))
    return wma(2 * wma(close, half) - wma(close, window), sqrt_w)


def rsi(close, window=14):
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    window = int(window)
    if window < 1 or n <= window:
        return out
    diff = np.diff(close)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = np.mean(gains[:window])
    avg_loss = np.mean(losses[:window])
    rs = avg_gain / (avg_loss + EPS)
    out[window] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(window + 1, n):
        avg_gain = (avg_gain * (window - 1) + gains[i - 1]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i - 1]) / window
        rs = avg_gain / (avg_loss + EPS)
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(close, fast=12, slow=26, signal=9):
    close = _as_float_array(close)
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(np.where(np.isnan(macd_line), 0.0, macd_line), signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    tr = _nan_array(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    return ema(tr, int(window))


def bollinger_bands(close, window=20, num_std=2.0):
    close = _as_float_array(close)
    mid = sma(close, int(window))
    std = _rolling_std(close, int(window), ddof=0)
    upper = mid + float(num_std) * std
    lower = mid - float(num_std) * std
    denom = np.where(np.abs(upper - lower) < EPS, np.nan, upper - lower)
    pb = (close - lower) / denom
    bw = (upper - lower) / (mid + EPS)
    return mid, lower, upper, bw, pb


def stochastic(high, low, close, k_window=14, d_window=3):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    k = _nan_array(n)
    k_window = int(k_window)
    for i in range(k_window - 1, n):
        hh = np.max(high[i - k_window + 1 : i + 1])
        ll = np.min(low[i - k_window + 1 : i + 1])
        k[i] = 100.0 * (close[i] - ll) / (hh - ll + EPS)
    d = sma(k, int(d_window))
    return k, d


def cci(high, low, close, window=20):
    tp = (_as_float_array(high) + _as_float_array(low) + _as_float_array(close)) / 3.0
    sma_tp = sma(tp, int(window))
    md = _nan_array(len(tp))
    for i in range(int(window) - 1, len(tp)):
        md[i] = np.mean(np.abs(tp[i - int(window) + 1 : i + 1] - sma_tp[i]))
    return (tp - sma_tp) / (0.015 * md + EPS)


def williams_r(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    out = _nan_array(n)
    for i in range(int(window) - 1, n):
        hh = np.max(high[i - int(window) + 1 : i + 1])
        ll = np.min(low[i - int(window) + 1 : i + 1])
        out[i] = -100.0 * (hh - close[i]) / (hh - ll + EPS)
    return out


def obv(close, volume):
    close = _as_float_array(close)
    volume = _as_float_array(volume)
    n = len(close)
    out = _nan_array(n)
    if n == 0:
        return out
    out[0] = volume[0]
    for i in range(1, n):
        if close[i] > close[i - 1]:
            out[i] = out[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            out[i] = out[i - 1] - volume[i]
        else:
            out[i] = out[i - 1]
    return out


def mfi(high, low, close, volume, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    volume = _as_float_array(volume)
    tp = (high + low + close) / 3.0
    mf = tp * volume
    pos = np.where(tp[1:] > tp[:-1], mf[1:], 0.0)
    neg = np.where(tp[1:] < tp[:-1], mf[1:], 0.0)
    out = _nan_array(len(close))
    w = int(window)
    for i in range(w, len(close)):
        p = np.sum(pos[i - w : i])
        n_ = np.sum(neg[i - w : i])
        out[i] = 100.0 - (100.0 / (1.0 + (p / (n_ + EPS))))
    return out


def adx(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    plus_dm = _nan_array(n)
    minus_dm = _nan_array(n)
    tr = _nan_array(n)
    tr[0] = high[0] - low[0]
    # Bar 0 has no previous bar, so its directional movement is zero by definition - the
    # same treatment ``tr[0]`` already gets on the line above. Leaving it NaN made
    # ``ema(plus_dm, window)`` seed from a window containing a NaN, which NaNs the seed and
    # then the whole recursion: ``adx``, ``plus_di`` and ``minus_di`` came back 100% NaN for
    # every input length. That was invisible while ``dag_engine.IndicatorExecutor`` had no
    # ADX branch and silently computed RSI(14) instead; it surfaces the moment the executor
    # is re-pointed here (strategy-builder task 5.4).
    plus_dm[0] = 0.0
    minus_dm[0] = 0.0
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0
        tr[i] = max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    atr_v = ema(tr, int(window))
    pdi = 100.0 * ema(plus_dm, int(window)) / (atr_v + EPS)
    mdi = 100.0 * ema(minus_dm, int(window)) / (atr_v + EPS)
    # DX is undefined while +DI and -DI are still warming, so its EMA has to start after
    # that warmup rather than seed from it - see :func:`_ema_after_warmup`.
    adx_v = _ema_after_warmup(100.0 * np.abs(pdi - mdi) / (pdi + mdi + EPS), int(window))
    return adx_v, pdi, mdi


def supertrend(high, low, close, window=10, multiplier=3.0):
    atr_v = atr(high, low, close, int(window))
    hl2 = (_as_float_array(high) + _as_float_array(low)) / 2.0
    upper = hl2 + float(multiplier) * atr_v
    lower = hl2 - float(multiplier) * atr_v
    direction = _nan_array(len(close))
    trend = _nan_array(len(close))
    direction[:] = 1.0
    for i in range(1, len(close)):
        if close[i] > upper[i - 1]:
            direction[i] = 1.0
        elif close[i] < lower[i - 1]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]
        trend[i] = lower[i] if direction[i] > 0 else upper[i]
    return trend, direction


def trix(close, window=15):
    close = _as_float_array(close)
    e1 = ema(close, int(window))
    e2 = ema(np.where(np.isnan(e1), 0.0, e1), int(window))
    e3 = ema(np.where(np.isnan(e2), 0.0, e2), int(window))
    out = _nan_array(len(close))
    for i in range(1, len(close)):
        if abs(e3[i - 1]) > EPS:
            out[i] = 100.0 * (e3[i] - e3[i - 1]) / e3[i - 1]
    return out


def vortex_indicator(high, low, close, window=14):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    vm_plus = _nan_array(n)
    vm_minus = _nan_array(n)
    tr = _nan_array(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        vm_plus[i] = abs(high[i] - low[i - 1])
        vm_minus[i] = abs(low[i] - high[i - 1])
        tr[i] = max(
            high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])
        )
    out_p = _nan_array(n)
    out_m = _nan_array(n)
    w = int(window)
    for i in range(w - 1, n):
        tr_sum = np.sum(tr[i - w + 1 : i + 1]) + EPS
        out_p[i] = np.sum(vm_plus[i - w + 1 : i + 1]) / tr_sum
        out_m[i] = np.sum(vm_minus[i - w + 1 : i + 1]) / tr_sum
    return out_p, out_m


def choppiness_index(high, low, close, window=14):
    atr_v = atr(high, low, close, int(window))
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(close)
    out = _nan_array(n)
    for i in range(int(window) - 1, n):
        tr_sum = np.sum(atr_v[i - int(window) + 1 : i + 1])
        hh = np.max(high[i - int(window) + 1 : i + 1])
        ll = np.min(low[i - int(window) + 1 : i + 1])
        out[i] = (
            100.0 * np.log10((tr_sum + EPS) / (hh - ll + EPS)) / np.log10(float(window))
        )
    return out


def awesome_oscillator(high, low, fast_w=5, slow_w=34):
    median = (_as_float_array(high) + _as_float_array(low)) / 2.0
    return sma(median, int(fast_w)) - sma(median, int(slow_w))


def fisher_transform(high, low, window=10):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(high)
    out = _nan_array(n)
    hl2 = (high + low) / 2.0
    for i in range(int(window) - 1, n):
        h = np.max(hl2[i - int(window) + 1 : i + 1])
        low = np.min(hl2[i - int(window) + 1 : i + 1])
        value = 2.0 * ((hl2[i] - low) / (h - low + EPS) - 0.5)
        value = np.clip(value, -0.999, 0.999)
        out[i] = 0.5 * np.log((1 + value) / (1 - value))
    return out


def rolling_z_score(close, window=20):
    close = _as_float_array(close)
    mean = sma(close, int(window))
    std = _rolling_std(close, int(window), ddof=0)
    return (close - mean) / (std + EPS)


def historical_volatility(close, window=20, periods_per_year=252):
    close = _as_float_array(close)
    ret = np.diff(np.log(close + EPS), prepend=np.nan)
    return _rolling_std(ret, int(window), ddof=1) * np.sqrt(float(periods_per_year))


def rolling_vwap(high, low, close, volume, window=20):
    tp = (_as_float_array(high) + _as_float_array(low) + _as_float_array(close)) / 3.0
    volume = _as_float_array(volume)
    n = len(close)
    out = _nan_array(n)
    w = int(window)
    for i in range(w - 1, n):
        vol = np.sum(volume[i - w + 1 : i + 1]) + EPS
        out[i] = np.sum(tp[i - w + 1 : i + 1] * volume[i - w + 1 : i + 1]) / vol
    return out


def momentum(close, window=10):
    close = _as_float_array(close)
    out = _nan_array(len(close))
    w = int(window)
    for i in range(w, len(close)):
        out[i] = close[i] - close[i - w]
    return out


def roc(close, window=10):
    close = _as_float_array(close)
    out = _nan_array(len(close))
    w = int(window)
    for i in range(w, len(close)):
        out[i] = 100.0 * (close[i] - close[i - w]) / (close[i - w] + EPS)
    return out


def donchian_channel(high, low, window=20):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(high)
    upper = _nan_array(n)
    lower = _nan_array(n)
    mid = _nan_array(n)
    w = int(window)
    for i in range(w - 1, n):
        upper[i] = np.max(high[i - w + 1 : i + 1])
        lower[i] = np.min(low[i - w + 1 : i + 1])
        mid[i] = (upper[i] + lower[i]) / 2.0
    return upper, lower, mid


def keltner_channels(high, low, close, window=20, multiplier=2.0):
    mid = ema(close, int(window))
    atr_v = atr(high, low, close, int(window))
    upper = mid + float(multiplier) * atr_v
    lower = mid - float(multiplier) * atr_v
    return mid, upper, lower


def ichimoku_cloud(high, low, close, tenkan=9, kijun=26, senkou_b=52):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(close)
    tenkan_line = _nan_array(n)
    kijun_line = _nan_array(n)
    span_a = _nan_array(n)
    span_b = _nan_array(n)
    chikou = shift_array(close, -int(kijun))
    for i in range(int(tenkan) - 1, n):
        tenkan_line[i] = (
            np.max(high[i - int(tenkan) + 1 : i + 1])
            + np.min(low[i - int(tenkan) + 1 : i + 1])
        ) / 2.0
    for i in range(int(kijun) - 1, n):
        kijun_line[i] = (
            np.max(high[i - int(kijun) + 1 : i + 1])
            + np.min(low[i - int(kijun) + 1 : i + 1])
        ) / 2.0
    for i in range(n):
        if not np.isnan(tenkan_line[i]) and not np.isnan(kijun_line[i]):
            j = i + int(kijun)
            if j < n:
                span_a[j] = (tenkan_line[i] + kijun_line[i]) / 2.0
    for i in range(int(senkou_b) - 1, n):
        j = i + int(kijun)
        if j < n:
            span_b[j] = (
                np.max(high[i - int(senkou_b) + 1 : i + 1])
                + np.min(low[i - int(senkou_b) + 1 : i + 1])
            ) / 2.0
    return tenkan_line, kijun_line, span_a, span_b, chikou


def cmf(high, low, close, volume, window=20):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    volume = _as_float_array(volume)
    n = len(close)
    out = _nan_array(n)
    mf = ((close - low) - (high - close)) / (high - low + EPS) * volume
    w = int(window)
    for i in range(w - 1, n):
        out[i] = np.sum(mf[i - w + 1 : i + 1]) / (
            np.sum(volume[i - w + 1 : i + 1]) + EPS
        )
    return out


def psar(high, low, close, step=0.02, max_step=0.2):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(close)
    out = _nan_array(n)
    if n == 0:
        return out
    bull = True
    af = float(step)
    ep = high[0]
    sar = low[0]
    out[0] = sar
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            if low[i] < sar:
                bull = False
                sar = ep
                ep = low[i]
                af = float(step)
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + float(step), float(max_step))
        else:
            if high[i] > sar:
                bull = True
                sar = ep
                ep = high[i]
                af = float(step)
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + float(step), float(max_step))
        out[i] = sar
    return out


def fibonacci_rolling(high, low, window=20):
    high = _as_float_array(high)
    low = _as_float_array(low)
    n = len(high)
    w = int(window)
    l_0 = _nan_array(n)
    l_236 = _nan_array(n)
    l_382 = _nan_array(n)
    l_500 = _nan_array(n)
    l_618 = _nan_array(n)
    l_786 = _nan_array(n)
    l_100 = _nan_array(n)
    for i in range(w - 1, n):
        swing_high = np.max(high[i - w + 1 : i + 1])
        swing_low = np.min(low[i - w + 1 : i + 1])
        diff = swing_high - swing_low
        l_0[i] = swing_low
        l_236[i] = swing_low + 0.236 * diff
        l_382[i] = swing_low + 0.382 * diff
        l_500[i] = swing_low + 0.500 * diff
        l_618[i] = swing_low + 0.618 * diff
        l_786[i] = swing_low + 0.786 * diff
        l_100[i] = swing_high
    return l_0, l_236, l_382, l_500, l_618, l_786, l_100


def pivot_standard(high, low, close):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    pp = _nan_array(n)
    r1 = _nan_array(n)
    r2 = _nan_array(n)
    r3 = _nan_array(n)
    s1 = _nan_array(n)
    s2 = _nan_array(n)
    s3 = _nan_array(n)
    for i in range(1, n):
        prev_h = high[i - 1]
        prev_l = low[i - 1]
        prev_c = close[i - 1]
        pp[i] = (prev_h + prev_l + prev_c) / 3.0
        r1[i] = 2 * pp[i] - prev_l
        s1[i] = 2 * pp[i] - prev_h
        r2[i] = pp[i] + (prev_h - prev_l)
        s2[i] = pp[i] - (prev_h - prev_l)
        r3[i] = prev_h + 2 * (pp[i] - prev_l)
        s3[i] = prev_l - 2 * (prev_h - pp[i])
    return pp, r1, r2, r3, s1, s2, s3


def pivot_camarilla(high, low, close):
    high = _as_float_array(high)
    low = _as_float_array(low)
    close = _as_float_array(close)
    n = len(close)
    pp = _nan_array(n)
    r1 = _nan_array(n)
    r2 = _nan_array(n)
    r3 = _nan_array(n)
    r4 = _nan_array(n)
    s1 = _nan_array(n)
    s2 = _nan_array(n)
    s3 = _nan_array(n)
    s4 = _nan_array(n)
    for i in range(1, n):
        rng = high[i - 1] - low[i - 1]
        c = close[i - 1]
        pp[i] = (high[i - 1] + low[i - 1] + c) / 3.0
        r1[i] = c + (rng * 1.1 / 12.0)
        r2[i] = c + (rng * 1.1 / 6.0)
        r3[i] = c + (rng * 1.1 / 4.0)
        r4[i] = c + (rng * 1.1 / 2.0)
        s1[i] = c - (rng * 1.1 / 12.0)
        s2[i] = c - (rng * 1.1 / 6.0)
        s3[i] = c - (rng * 1.1 / 4.0)
        s4[i] = c - (rng * 1.1 / 2.0)
    return pp, r1, r2, r3, r4, s1, s2, s3, s4


# aliases used by the rest of the codebase
bb = bollinger_bands
stochastic_oscillator = stochastic
vortex = vortex_indicator
chop = choppiness_index
ao = awesome_oscillator
fisher = fisher_transform
hv = historical_volatility


# ===========================================================================
# Indicator descriptor specs
# ===========================================================================
#
# Every implementation above gains a machine-readable spec. The registry
# (``strategy_dag/registry.py``) assembles its INDICATOR descriptors from
# ``INDICATOR_SPECS`` and never from a parallel list, which is what closes
# defect SB-04 (``wma`` and ``hma`` runnable but unselectable).
#
# Three invariants hold for every spec, and each is covered by a test:
#
#   1. ``runtime_ref`` resolves to a real callable in this module. It is
#      asserted at import time, so a typo fails loudly instead of advertising
#      a block that cannot run.
#   2. ``inputs`` are declared in the order the runtime callable accepts them
#      positionally, and there is exactly one input port per required
#      positional argument. The executor can therefore bind inputs by
#      position: ``fn(*inputs, **spec.runtime_kwargs(params))``.
#   3. ``outputs`` declare one port per distinct value the callable returns,
#      in return-tuple order. Multi-output indicators (``macd``,
#      ``bollinger_bands``, ``stochastic``, ``supertrend``, ``adx``,
#      ``vortex_indicator``, ``ichimoku_cloud``, ``donchian_channel``,
#      ``keltner_channels``, ``fibonacci_rolling``, ``pivot_standard``,
#      ``pivot_camarilla``) expose every output rather than one collapsed
#      series.
#
# Port type strings are the *values* of the canonical ``PortType`` enum in
# ``strategy_dag/schema.py``; they are kept as plain strings here so this
# module stays dependency-free and importable on its own.

MODULE_REF = "indicators_backend"

_PRICE = "PRICE_SERIES"
_SCALAR = "SCALAR_SERIES"
_BOOL = "BOOLEAN_SERIES"

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

_INT = "INTEGER"
_NUM = "NUMBER"
_SELECT = "SELECT"

PARAM_TYPES = frozenset(
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

# leakage_risk vocabulary
LEAKAGE_NONE = "NONE"
LEAKAGE_LOW = "LOW"
LEAKAGE_REVIEW_REQUIRED = "REVIEW_REQUIRED"

LEAKAGE_LEVELS = (LEAKAGE_NONE, LEAKAGE_LOW, LEAKAGE_REVIEW_REQUIRED)

# execution_semantics vocabulary
SEMANTICS_SERIES_MAP = "SERIES_MAP"
SEMANTICS_WINDOWED = "WINDOWED"
SEMANTICS_STATEFUL = "STATEFUL"
SEMANTICS_TERMINAL = "TERMINAL"

EXECUTION_SEMANTICS = (
    SEMANTICS_SERIES_MAP,
    SEMANTICS_WINDOWED,
    SEMANTICS_STATEFUL,
    SEMANTICS_TERMINAL,
)

PRICE_SOURCES = ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4")


@dataclass(frozen=True)
class Port:
    """A single input or output port of an indicator block.

    ``leakage_risk`` is per-port rather than per-block because an indicator can
    have one forward-looking output among otherwise safe ones — Ichimoku's
    ``chikou`` is exactly that case.
    """

    name: str
    type: str
    required: bool = True
    variadic: bool = False
    description: str = ""
    leakage_risk: str = LEAKAGE_NONE

    def __post_init__(self) -> None:
        if self.type not in CANONICAL_PORT_TYPES:
            raise ValueError(
                f"Port '{self.name}' declares unknown port type '{self.type}'"
            )
        if self.leakage_risk not in LEAKAGE_LEVELS:
            raise ValueError(
                f"Port '{self.name}' declares unknown leakage_risk "
                f"'{self.leakage_risk}'"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "variadic": self.variadic,
            "description": self.description,
            "leakage_risk": self.leakage_risk,
        }


@dataclass(frozen=True)
class ParamSpec:
    """Declarative parameter contract, read by both the UI form generator and
    the backend validator, so a form can never offer a value the backend
    rejects and the backend never depends on the form having checked.

    ``forward_to_runtime`` is False for parameters the runtime callable does
    not accept as a keyword argument (``source`` selects which upstream series
    is wired in; it is resolved by the compiler, not by the indicator).
    """

    key: str
    label: str
    type: str
    required: bool = True
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
    forward_to_runtime: bool = True

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"Param '{self.key}' declares unknown type '{self.type}'")
        if self.type in ("SELECT", "MULTISELECT") and not self.options:
            raise ValueError(f"Param '{self.key}' is {self.type} but declares no options")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"Param '{self.key}' has min > max")

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
            "options": list(self.options) if self.options else None,
            "unit": self.unit,
            "example": self.example,
            "help": self.help,
            "depends_on": list(self.depends_on),
            "affects_warmup": self.affects_warmup,
        }


@dataclass(frozen=True)
class IndicatorSpec:
    """Machine-readable descriptor for one indicator implementation."""

    block_id: str
    display_name: str
    runtime_ref: str
    inputs: Tuple[Port, ...]
    outputs: Tuple[Port, ...]
    params: Tuple[ParamSpec, ...]
    warmup_fn: Callable[[Dict[str, Any]], int]
    description: str = ""
    execution_semantics: str = SEMANTICS_WINDOWED
    validate: Optional[Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = None
    version: str = "1.0.0"

    def __post_init__(self) -> None:
        if self.execution_semantics not in EXECUTION_SEMANTICS:
            raise ValueError(
                f"Indicator '{self.block_id}' declares unknown execution_semantics "
                f"'{self.execution_semantics}'"
            )
        if not self.outputs:
            raise ValueError(f"Indicator '{self.block_id}' declares no output ports")
        _reject_duplicates(self.block_id, "input", [p.name for p in self.inputs])
        _reject_duplicates(self.block_id, "output", [p.name for p in self.outputs])
        _reject_duplicates(self.block_id, "param", [p.key for p in self.params])

    # -- derived views ---------------------------------------------------
    @property
    def leakage_risk(self) -> str:
        """Block-level leakage risk: the strongest risk any output carries."""
        return max(
            (p.leakage_risk for p in self.outputs),
            key=LEAKAGE_LEVELS.index,
            default=LEAKAGE_NONE,
        )

    @property
    def leaky_outputs(self) -> Tuple[str, ...]:
        return tuple(
            p.name for p in self.outputs if p.leakage_risk == LEAKAGE_REVIEW_REQUIRED
        )

    def input_port(self, name: str) -> Optional[Port]:
        return next((p for p in self.inputs if p.name == name), None)

    def output_port(self, name: str) -> Optional[Port]:
        return next((p for p in self.outputs if p.name == name), None)

    def param(self, key: str) -> Optional[ParamSpec]:
        return next((p for p in self.params if p.key == key), None)

    def defaults(self) -> Dict[str, Any]:
        return {p.key: p.default for p in self.params if p.default is not None}

    def runtime_kwargs(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Params the runtime callable actually accepts, defaults filled in."""
        supplied = dict(params or {})
        resolved: Dict[str, Any] = {}
        for spec in self.params:
            if not spec.forward_to_runtime:
                continue
            if spec.key in supplied:
                resolved[spec.key] = supplied[spec.key]
            elif spec.default is not None:
                resolved[spec.key] = spec.default
        return resolved

    def resolve_runtime(self) -> Callable[..., Any]:
        return _resolve_runtime_ref(self.runtime_ref)

    def warmup(self, params: Optional[Dict[str, Any]] = None) -> int:
        return int(self.warmup_fn(dict(params or {})))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "display_name": self.display_name,
            "category": "INDICATOR",
            "description": self.description,
            "runtime_ref": self.runtime_ref,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "params": [p.to_dict() for p in self.params],
            "execution_semantics": self.execution_semantics,
            "leakage_risk": self.leakage_risk,
            "version": self.version,
        }


def _reject_duplicates(block_id: str, kind: str, names: Sequence[str]) -> None:
    seen = set()
    for name in names:
        if name in seen:
            raise ValueError(
                f"Indicator '{block_id}' declares duplicate {kind} '{name}'"
            )
        seen.add(name)


def _resolve_runtime_ref(runtime_ref: str) -> Callable[..., Any]:
    """Resolve ``"indicators_backend.ema"`` to the callable in this module."""
    module_name, _, attr = runtime_ref.rpartition(".")
    if module_name != MODULE_REF:
        raise ValueError(
            f"runtime_ref '{runtime_ref}' does not belong to '{MODULE_REF}'"
        )
    fn = globals().get(attr)
    if not callable(fn):
        raise ValueError(f"runtime_ref '{runtime_ref}' does not resolve to a callable")
    return fn


def resolve_indicator_runtime_ref(runtime_ref: str) -> Callable[..., Any]:
    """Public resolver: ``"indicators_backend.ema"`` -> the callable in this module.

    The INDICATOR counterpart of ``feature_engineering.resolve_feature_runtime``,
    ``ml_models.resolve_model_runtime`` and
    ``strategy_dag.block_specs.resolve_block_runtime``: every family resolves a
    ``runtime_ref`` through the module that owns its implementations, so
    ``registry.build_registry()`` never has to know how a reference is addressed.

    Raises ``ValueError`` naming the offending reference, which is what lets registry
    assembly fail startup rather than advertising a block it cannot run (Requirements
    4.7, 4.8).
    """
    return _resolve_runtime_ref(runtime_ref)


# --- parameter helpers -----------------------------------------------------


def _int_param(params: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(params.get(key, default))
    except (TypeError, ValueError):
        return int(default)


def _float_param(params: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(params.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _window_param(
    default: int,
    key: str = "window",
    label: str = "Period",
    minimum: int = 2,
    maximum: int = 1000,
    help_text: str = "Number of closed bars in the rolling window.",
) -> ParamSpec:
    return ParamSpec(
        key=key,
        label=label,
        type=_INT,
        required=True,
        default=default,
        min=minimum,
        max=maximum,
        step=1,
        unit="bars",
        example=default,
        help=help_text,
        affects_warmup=True,
    )


def _source_param() -> ParamSpec:
    return ParamSpec(
        key="source",
        label="Price source",
        type=_SELECT,
        required=True,
        default="close",
        options=PRICE_SOURCES,
        example="close",
        help="Which price series the indicator reads when an OHLCV frame is wired in.",
        forward_to_runtime=False,
    )


def _multiplier_param(default: float, label: str = "ATR multiplier") -> ParamSpec:
    return ParamSpec(
        key="multiplier",
        label=label,
        type=_NUM,
        required=True,
        default=default,
        min=0.1,
        max=10.0,
        step=0.1,
        example=default,
        help="Band width as a multiple of ATR.",
    )


# --- warmup helpers --------------------------------------------------------


def _warmup_window(default: int, key: str = "window", factor: int = 1, extra: int = 0):
    def warmup_fn(params: Dict[str, Any]) -> int:
        return max(1, factor * _int_param(params, key, default) + extra)

    return warmup_fn


def _warmup_constant(bars: int):
    def warmup_fn(_params: Dict[str, Any]) -> int:
        return max(1, int(bars))

    return warmup_fn


def _warmup_macd(params: Dict[str, Any]) -> int:
    return max(1, _int_param(params, "slow", 26) + _int_param(params, "signal", 9))


def _warmup_stochastic(params: Dict[str, Any]) -> int:
    return max(
        1, _int_param(params, "k_window", 14) + _int_param(params, "d_window", 3)
    )


def _warmup_awesome_oscillator(params: Dict[str, Any]) -> int:
    return max(_int_param(params, "fast_w", 5), _int_param(params, "slow_w", 34))


def _warmup_hma(params: Dict[str, Any]) -> int:
    window = _int_param(params, "window", 14)
    return max(1, window + int(math.isqrt(max(1, window))))


def _warmup_ichimoku(params: Dict[str, Any]) -> int:
    tenkan = _int_param(params, "tenkan", 9)
    kijun = _int_param(params, "kijun", 26)
    senkou_b = _int_param(params, "senkou_b", 52)
    return max(tenkan, kijun, senkou_b) + kijun


# --- cross-field validation hooks -----------------------------------------


def _issue(
    code: str,
    field_name: str,
    message: str,
    expected: Any,
    actual: Any,
    fix_hint: str,
) -> Dict[str, Any]:
    return {
        "code": code,
        "severity": "ERROR",
        "field": field_name,
        "message": message,
        "expected": expected,
        "actual": actual,
        "fix_hint": fix_hint,
    }


def _validate_macd(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    fast = _int_param(params, "fast", 12)
    slow = _int_param(params, "slow", 26)
    if fast >= slow:
        return [
            _issue(
                "PARAM_CROSS_FIELD_INVALID",
                "fast",
                "MACD needs a fast period shorter than its slow period.",
                expected=f"fast < slow (slow = {slow})",
                actual=f"fast = {fast}",
                fix_hint=f"Set fast below {slow}, for example 12 with slow 26.",
            )
        ]
    return []


def _validate_awesome_oscillator(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    fast = _int_param(params, "fast_w", 5)
    slow = _int_param(params, "slow_w", 34)
    if fast >= slow:
        return [
            _issue(
                "PARAM_CROSS_FIELD_INVALID",
                "fast_w",
                "The awesome oscillator needs a fast period shorter than its slow "
                "period.",
                expected=f"fast_w < slow_w (slow_w = {slow})",
                actual=f"fast_w = {fast}",
                fix_hint=f"Set fast_w below {slow}, for example 5 with slow_w 34.",
            )
        ]
    return []


def _validate_psar(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    step = _float_param(params, "step", 0.02)
    max_step = _float_param(params, "max_step", 0.2)
    if step > max_step:
        return [
            _issue(
                "PARAM_CROSS_FIELD_INVALID",
                "step",
                "The parabolic SAR acceleration step cannot exceed its maximum.",
                expected=f"step <= max_step (max_step = {max_step})",
                actual=f"step = {step}",
                fix_hint=f"Lower step to at most {max_step}, for example 0.02.",
            )
        ]
    return []


# --- the specs -------------------------------------------------------------


def _series_input(description: str = "Price series to smooth.") -> Tuple[Port, ...]:
    return (Port("series", _PRICE, description=description),)


def _hlc_inputs() -> Tuple[Port, ...]:
    return (
        Port("high", _PRICE, description="High price series."),
        Port("low", _PRICE, description="Low price series."),
        Port("close", _PRICE, description="Close price series."),
    )


def _hl_inputs() -> Tuple[Port, ...]:
    return (
        Port("high", _PRICE, description="High price series."),
        Port("low", _PRICE, description="Low price series."),
    )


def _value_output(description: str, port_type: str = _SCALAR) -> Tuple[Port, ...]:
    return (Port("value", port_type, description=description),)


def _volume_port() -> Port:
    return Port("volume", _SCALAR, description="Volume series.")


def _moving_average_spec(
    block_id: str,
    display_name: str,
    default_window: int,
    warmup_fn: Callable[[Dict[str, Any]], int],
    description: str,
    semantics: str = SEMANTICS_WINDOWED,
) -> IndicatorSpec:
    return IndicatorSpec(
        block_id=block_id,
        display_name=display_name,
        runtime_ref=f"{MODULE_REF}.{block_id}",
        inputs=_series_input(),
        outputs=_value_output(f"{display_name} of the input series."),
        params=(_window_param(default_window), _source_param()),
        warmup_fn=warmup_fn,
        description=description,
        execution_semantics=semantics,
    )


#: Ordered descriptor set. ``AVAILABLE_INDICATORS`` is derived from it, so the
#: two can never diverge.
INDICATOR_SPECS: Tuple[IndicatorSpec, ...] = (
    _moving_average_spec(
        "sma",
        "Simple Moving Average",
        20,
        _warmup_window(20),
        "Unweighted mean of the last N closed bars.",
    ),
    _moving_average_spec(
        "ema",
        "Exponential Moving Average",
        14,
        # 3x the window: an EMA is recursive and needs roughly three spans to
        # converge from its seeded value.
        _warmup_window(14, factor=3),
        "Exponentially weighted moving average, seeded with an SMA.",
        semantics=SEMANTICS_STATEFUL,
    ),
    _moving_average_spec(
        "wma",
        "Weighted Moving Average",
        9,
        _warmup_window(9),
        "Linearly weighted moving average; the newest bar carries the most weight.",
    ),
    _moving_average_spec(
        "hma",
        "Hull Moving Average",
        14,
        _warmup_hma,
        "Hull moving average: a WMA of two nested WMAs, smoother and less laggy.",
    ),
    IndicatorSpec(
        block_id="rsi",
        display_name="Relative Strength Index",
        runtime_ref=f"{MODULE_REF}.rsi",
        inputs=_series_input("Price series to measure."),
        outputs=_value_output("Relative strength index, bounded 0..100."),
        params=(_window_param(14, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(14, extra=1),
        description="Momentum oscillator bounded 0..100; classic overbought/oversold.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="macd",
        display_name="MACD",
        runtime_ref=f"{MODULE_REF}.macd",
        inputs=_series_input("Price series to measure."),
        outputs=(
            Port("macd", _SCALAR, description="Fast EMA minus slow EMA."),
            Port("signal", _SCALAR, description="EMA of the MACD line."),
            Port("histogram", _SCALAR, description="MACD line minus signal line."),
        ),
        params=(
            ParamSpec(
                key="fast",
                label="Fast period",
                type=_INT,
                default=12,
                min=2,
                max=200,
                step=1,
                unit="bars",
                example=12,
                help="Span of the fast EMA. Must be shorter than the slow period.",
                affects_warmup=True,
            ),
            ParamSpec(
                key="slow",
                label="Slow period",
                type=_INT,
                default=26,
                min=3,
                max=500,
                step=1,
                unit="bars",
                example=26,
                help="Span of the slow EMA. Must be longer than the fast period.",
                depends_on=("fast",),
                affects_warmup=True,
            ),
            ParamSpec(
                key="signal",
                label="Signal period",
                type=_INT,
                default=9,
                min=2,
                max=100,
                step=1,
                unit="bars",
                example=9,
                help="Span of the EMA applied to the MACD line.",
                affects_warmup=True,
            ),
        ),
        warmup_fn=_warmup_macd,
        description="Moving average convergence/divergence with all three outputs.",
        execution_semantics=SEMANTICS_STATEFUL,
        validate=_validate_macd,
    ),
    IndicatorSpec(
        block_id="atr",
        display_name="Average True Range",
        runtime_ref=f"{MODULE_REF}.atr",
        inputs=_hlc_inputs(),
        outputs=_value_output("Average true range in quote-price units."),
        params=(_window_param(14, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(14, extra=1),
        description="Volatility measure: smoothed true range over N bars.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="bollinger_bands",
        display_name="Bollinger Bands",
        runtime_ref=f"{MODULE_REF}.bollinger_bands",
        inputs=_series_input("Price series to band."),
        # Declaration order matches the return tuple:
        # (mid, lower, upper, bandwidth, percent_b)
        outputs=(
            Port("middle", _PRICE, description="Centre line (SMA of the window)."),
            Port("lower", _PRICE, description="Middle minus N standard deviations."),
            Port("upper", _PRICE, description="Middle plus N standard deviations."),
            Port(
                "bandwidth",
                _SCALAR,
                description="Band width normalised by the centre line.",
            ),
            Port(
                "percent_b",
                _SCALAR,
                description="Where price sits inside the band, 0 at lower, 1 at upper.",
            ),
        ),
        params=(
            _window_param(20, minimum=2, maximum=1000),
            ParamSpec(
                key="num_std",
                label="Standard deviations",
                type=_NUM,
                default=2.0,
                min=0.1,
                max=5.0,
                step=0.1,
                example=2.0,
                help="Band distance from the centre line, in standard deviations.",
            ),
        ),
        warmup_fn=_warmup_window(20),
        description="Volatility bands around a moving average.",
    ),
    IndicatorSpec(
        block_id="stochastic",
        display_name="Stochastic Oscillator",
        runtime_ref=f"{MODULE_REF}.stochastic",
        inputs=_hlc_inputs(),
        outputs=(
            Port("k", _SCALAR, description="%K line, bounded 0..100."),
            Port("d", _SCALAR, description="%D line: SMA of %K."),
        ),
        params=(
            _window_param(
                14,
                key="k_window",
                label="%K period",
                minimum=1,
                maximum=500,
                help_text="Lookback for the raw %K line.",
            ),
            _window_param(
                3,
                key="d_window",
                label="%D smoothing",
                minimum=1,
                maximum=100,
                help_text="SMA length applied to %K to produce %D.",
            ),
        ),
        warmup_fn=_warmup_stochastic,
        description="Where the close sits within its recent high/low range.",
    ),
    IndicatorSpec(
        block_id="cci",
        display_name="Commodity Channel Index",
        runtime_ref=f"{MODULE_REF}.cci",
        inputs=_hlc_inputs(),
        outputs=_value_output("Commodity channel index, unbounded around zero."),
        params=(_window_param(20, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(20, extra=1),
        description="Deviation of typical price from its mean, scaled by mean deviation.",
    ),
    IndicatorSpec(
        block_id="williams_r",
        display_name="Williams %R",
        runtime_ref=f"{MODULE_REF}.williams_r",
        inputs=_hlc_inputs(),
        outputs=_value_output("Williams %R, bounded -100..0."),
        params=(_window_param(14, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(14, extra=1),
        description="Inverted stochastic: distance of the close below the range high.",
    ),
    IndicatorSpec(
        block_id="obv",
        display_name="On-Balance Volume",
        runtime_ref=f"{MODULE_REF}.obv",
        inputs=(
            Port("close", _PRICE, description="Close price series."),
            _volume_port(),
        ),
        outputs=_value_output("Cumulative signed volume."),
        params=(),
        warmup_fn=_warmup_constant(2),
        description="Running total of volume signed by the direction of the close.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="mfi",
        display_name="Money Flow Index",
        runtime_ref=f"{MODULE_REF}.mfi",
        inputs=_hlc_inputs() + (_volume_port(),),
        outputs=_value_output("Money flow index, bounded 0..100."),
        params=(_window_param(14, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(14, extra=1),
        description="Volume-weighted RSI over typical price.",
    ),
    IndicatorSpec(
        block_id="adx",
        display_name="Average Directional Index",
        runtime_ref=f"{MODULE_REF}.adx",
        inputs=_hlc_inputs(),
        # Declaration order matches the return tuple: (adx, +DI, -DI)
        outputs=(
            Port("adx", _SCALAR, description="Trend strength, bounded 0..100."),
            Port("plus_di", _SCALAR, description="Positive directional indicator."),
            Port("minus_di", _SCALAR, description="Negative directional indicator."),
        ),
        params=(_window_param(14, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(14, extra=1),
        description="Trend strength plus both directional indicators.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="supertrend",
        display_name="SuperTrend",
        runtime_ref=f"{MODULE_REF}.supertrend",
        inputs=_hlc_inputs(),
        outputs=(
            Port("trend", _SCALAR, description="Active SuperTrend stop level."),
            # The runtime emits +1.0 / -1.0, not a truth series, so this is a
            # SCALAR_SERIES. Compare it (`direction > 0`) to get a boolean.
            Port(
                "direction",
                _SCALAR,
                description="Trend direction: +1 while long, -1 while short.",
            ),
        ),
        params=(
            _window_param(10, minimum=2, maximum=500),
            _multiplier_param(3.0),
        ),
        warmup_fn=_warmup_window(10),
        description="ATR-banded trailing stop with its direction flag.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="trix",
        display_name="TRIX",
        runtime_ref=f"{MODULE_REF}.trix",
        inputs=_series_input("Price series to measure."),
        outputs=_value_output("Rate of change of a triple-smoothed EMA, in percent."),
        params=(_window_param(15, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(15, factor=3, extra=1),
        description="Percent rate of change of a triple exponential average.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="vortex_indicator",
        display_name="Vortex Indicator",
        runtime_ref=f"{MODULE_REF}.vortex_indicator",
        inputs=_hlc_inputs(),
        outputs=(
            Port("vi_plus", _SCALAR, description="Upward vortex movement, VI+."),
            Port("vi_minus", _SCALAR, description="Downward vortex movement, VI-."),
        ),
        params=(_window_param(14, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(14, extra=1),
        description="Directional trend measure from the two vortex movements.",
    ),
    IndicatorSpec(
        block_id="choppiness_index",
        display_name="Choppiness Index",
        runtime_ref=f"{MODULE_REF}.choppiness_index",
        inputs=_hlc_inputs(),
        outputs=_value_output("Choppiness index, bounded roughly 0..100."),
        params=(_window_param(14, minimum=2, maximum=500),),
        # ATR smoothing plus the rolling sum over the same window.
        warmup_fn=_warmup_window(14, factor=2),
        description="How much the market is ranging rather than trending.",
    ),
    IndicatorSpec(
        block_id="awesome_oscillator",
        display_name="Awesome Oscillator",
        runtime_ref=f"{MODULE_REF}.awesome_oscillator",
        inputs=_hl_inputs(),
        outputs=_value_output("Fast median SMA minus slow median SMA."),
        params=(
            _window_param(
                5,
                key="fast_w",
                label="Fast period",
                minimum=2,
                maximum=200,
                help_text="Fast SMA length over the median price.",
            ),
            _window_param(
                34,
                key="slow_w",
                label="Slow period",
                minimum=3,
                maximum=500,
                help_text="Slow SMA length over the median price.",
            ),
        ),
        warmup_fn=_warmup_awesome_oscillator,
        description="Momentum as the gap between two SMAs of the median price.",
        validate=_validate_awesome_oscillator,
    ),
    IndicatorSpec(
        block_id="fisher_transform",
        display_name="Fisher Transform",
        runtime_ref=f"{MODULE_REF}.fisher_transform",
        inputs=_hl_inputs(),
        outputs=_value_output("Gaussian-shaped transform of the normalised range."),
        params=(_window_param(10, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(10),
        description="Sharpens turning points by normalising price into a Gaussian.",
    ),
    IndicatorSpec(
        block_id="rolling_z_score",
        display_name="Rolling Z-Score",
        runtime_ref=f"{MODULE_REF}.rolling_z_score",
        inputs=_series_input("Price series to standardise."),
        outputs=_value_output("Standard deviations from the rolling mean."),
        params=(_window_param(20, minimum=2, maximum=1000),),
        warmup_fn=_warmup_window(20),
        description="Distance from the rolling mean in rolling standard deviations.",
    ),
    IndicatorSpec(
        block_id="historical_volatility",
        display_name="Historical Volatility",
        runtime_ref=f"{MODULE_REF}.historical_volatility",
        inputs=_series_input("Price series to measure."),
        outputs=_value_output("Annualised standard deviation of log returns."),
        params=(
            _window_param(20, minimum=2, maximum=1000),
            ParamSpec(
                key="periods_per_year",
                label="Periods per year",
                type=_INT,
                default=252,
                min=1,
                max=525600,
                step=1,
                unit="bars/year",
                example=365,
                help="Annualisation factor for the timeframe: 252 for daily equities, "
                "365 for daily crypto, 35040 for 15m crypto.",
            ),
        ),
        warmup_fn=_warmup_window(20, extra=1),
        description="Annualised realised volatility of log returns.",
    ),
    IndicatorSpec(
        block_id="rolling_vwap",
        display_name="Rolling VWAP",
        runtime_ref=f"{MODULE_REF}.rolling_vwap",
        inputs=_hlc_inputs() + (_volume_port(),),
        outputs=_value_output("Volume-weighted average typical price.", _PRICE),
        params=(_window_param(20, minimum=2, maximum=1000),),
        warmup_fn=_warmup_window(20),
        description="Volume-weighted average price over a rolling window.",
    ),
    IndicatorSpec(
        block_id="momentum",
        display_name="Momentum",
        runtime_ref=f"{MODULE_REF}.momentum",
        inputs=_series_input("Price series to measure."),
        outputs=_value_output("Price change over N bars, in quote-price units."),
        params=(_window_param(10, minimum=1, maximum=1000),),
        warmup_fn=_warmup_window(10, extra=1),
        description="Absolute price change over the last N bars.",
    ),
    IndicatorSpec(
        block_id="roc",
        display_name="Rate of Change",
        runtime_ref=f"{MODULE_REF}.roc",
        inputs=_series_input("Price series to measure."),
        outputs=_value_output("Percent price change over N bars."),
        params=(_window_param(10, minimum=1, maximum=1000),),
        warmup_fn=_warmup_window(10, extra=1),
        description="Percent price change over the last N bars.",
    ),
    IndicatorSpec(
        block_id="donchian_channel",
        display_name="Donchian Channel",
        runtime_ref=f"{MODULE_REF}.donchian_channel",
        inputs=_hl_inputs(),
        # Declaration order matches the return tuple: (upper, lower, mid)
        outputs=(
            Port("upper", _PRICE, description="Highest high over the window."),
            Port("lower", _PRICE, description="Lowest low over the window."),
            Port("middle", _PRICE, description="Midpoint of the channel."),
        ),
        params=(_window_param(20, minimum=2, maximum=1000),),
        warmup_fn=_warmup_window(20),
        description="Highest high and lowest low over N bars, plus the midpoint.",
    ),
    IndicatorSpec(
        block_id="keltner_channels",
        display_name="Keltner Channels",
        runtime_ref=f"{MODULE_REF}.keltner_channels",
        inputs=_hlc_inputs(),
        # Declaration order matches the return tuple: (mid, upper, lower)
        outputs=(
            Port("middle", _PRICE, description="EMA centre line."),
            Port("upper", _PRICE, description="Centre line plus N x ATR."),
            Port("lower", _PRICE, description="Centre line minus N x ATR."),
        ),
        params=(
            _window_param(20, minimum=2, maximum=1000),
            _multiplier_param(2.0),
        ),
        warmup_fn=_warmup_window(20),
        description="ATR-based envelope around an EMA.",
        execution_semantics=SEMANTICS_STATEFUL,
    ),
    IndicatorSpec(
        block_id="ichimoku_cloud",
        display_name="Ichimoku Cloud",
        runtime_ref=f"{MODULE_REF}.ichimoku_cloud",
        inputs=_hlc_inputs(),
        # Declaration order matches the return tuple:
        # (tenkan, kijun, senkou_a, senkou_b, chikou)
        outputs=(
            Port("tenkan", _PRICE, description="Conversion line."),
            Port("kijun", _PRICE, description="Base line."),
            Port("senkou_a", _PRICE, description="Leading span A, plotted ahead."),
            Port("senkou_b", _PRICE, description="Leading span B, plotted ahead."),
            # The lagging span read at bar t encodes bar t+kijun. It is fine on a
            # chart and a leak in a model, so it is flagged for the leakage stage
            # to reject when it reaches FEATURE_ENGINEERING or ML_DL.
            Port(
                "chikou",
                _PRICE,
                description="Lagging span: the close shifted forward by the base "
                "period. Reading it at bar t exposes bar t+kijun, so it must not "
                "feed a feature or a model.",
                leakage_risk=LEAKAGE_REVIEW_REQUIRED,
            ),
        ),
        params=(
            _window_param(
                9,
                key="tenkan",
                label="Conversion period",
                minimum=1,
                maximum=200,
                help_text="Lookback for the conversion line (Tenkan-sen).",
            ),
            _window_param(
                26,
                key="kijun",
                label="Base period",
                minimum=1,
                maximum=400,
                help_text="Lookback for the base line (Kijun-sen); also the span shift.",
            ),
            _window_param(
                52,
                key="senkou_b",
                label="Leading span B period",
                minimum=1,
                maximum=600,
                help_text="Lookback for leading span B (Senkou Span B).",
            ),
        ),
        warmup_fn=_warmup_ichimoku,
        description="Full Ichimoku set: both lines, both spans and the lagging span.",
    ),
    IndicatorSpec(
        block_id="cmf",
        display_name="Chaikin Money Flow",
        runtime_ref=f"{MODULE_REF}.cmf",
        inputs=_hlc_inputs() + (_volume_port(),),
        outputs=_value_output("Chaikin money flow, bounded -1..1."),
        params=(_window_param(20, minimum=2, maximum=500),),
        warmup_fn=_warmup_window(20),
        description="Volume-weighted accumulation/distribution over N bars.",
    ),
    IndicatorSpec(
        block_id="psar",
        display_name="Parabolic SAR",
        runtime_ref=f"{MODULE_REF}.psar",
        inputs=_hlc_inputs(),
        # The runtime returns the stop level only; no direction series is
        # produced, so none is advertised.
        outputs=_value_output("Parabolic stop-and-reverse level.", _PRICE),
        params=(
            ParamSpec(
                key="step",
                label="Acceleration step",
                type=_NUM,
                default=0.02,
                min=0.001,
                max=1.0,
                step=0.001,
                example=0.02,
                help="How fast the stop accelerates towards price on each new extreme.",
            ),
            ParamSpec(
                key="max_step",
                label="Maximum acceleration",
                type=_NUM,
                default=0.2,
                min=0.01,
                max=1.0,
                step=0.01,
                example=0.2,
                help="Ceiling for the acceleration factor.",
                depends_on=("step",),
            ),
        ),
        warmup_fn=_warmup_constant(2),
        description="Parabolic stop-and-reverse trailing level.",
        execution_semantics=SEMANTICS_STATEFUL,
        validate=_validate_psar,
    ),
    IndicatorSpec(
        block_id="fibonacci_rolling",
        display_name="Rolling Fibonacci Levels",
        runtime_ref=f"{MODULE_REF}.fibonacci_rolling",
        inputs=_hl_inputs(),
        # Declaration order matches the return tuple, low to high.
        outputs=(
            Port("level_0", _PRICE, description="Swing low, the 0% retracement."),
            Port("level_236", _PRICE, description="23.6% retracement."),
            Port("level_382", _PRICE, description="38.2% retracement."),
            Port("level_500", _PRICE, description="50% retracement."),
            Port("level_618", _PRICE, description="61.8% retracement."),
            Port("level_786", _PRICE, description="78.6% retracement."),
            Port("level_100", _PRICE, description="Swing high, the 100% retracement."),
        ),
        params=(
            _window_param(
                20,
                minimum=2,
                maximum=1000,
                help_text="Swing window the retracement levels are measured over.",
            ),
        ),
        warmup_fn=_warmup_window(20),
        description="Fibonacci retracements of the rolling swing high and low.",
    ),
    IndicatorSpec(
        block_id="pivot_standard",
        display_name="Standard Pivot Points",
        runtime_ref=f"{MODULE_REF}.pivot_standard",
        inputs=_hlc_inputs(),
        # Declaration order matches the return tuple: (pp, r1, r2, r3, s1, s2, s3)
        outputs=(
            Port("pp", _PRICE, description="Pivot point."),
            Port("r1", _PRICE, description="First resistance."),
            Port("r2", _PRICE, description="Second resistance."),
            Port("r3", _PRICE, description="Third resistance."),
            Port("s1", _PRICE, description="First support."),
            Port("s2", _PRICE, description="Second support."),
            Port("s3", _PRICE, description="Third support."),
        ),
        # Derived from the previous closed bar only; no parameters.
        params=(),
        warmup_fn=_warmup_constant(2),
        description="Classic pivot point with three supports and three resistances.",
    ),
    IndicatorSpec(
        block_id="pivot_camarilla",
        display_name="Camarilla Pivot Points",
        runtime_ref=f"{MODULE_REF}.pivot_camarilla",
        inputs=_hlc_inputs(),
        # Declaration order matches the return tuple:
        # (pp, r1, r2, r3, r4, s1, s2, s3, s4)
        outputs=(
            Port("pp", _PRICE, description="Pivot point."),
            Port("r1", _PRICE, description="First resistance."),
            Port("r2", _PRICE, description="Second resistance."),
            Port("r3", _PRICE, description="Third resistance."),
            Port("r4", _PRICE, description="Fourth resistance."),
            Port("s1", _PRICE, description="First support."),
            Port("s2", _PRICE, description="Second support."),
            Port("s3", _PRICE, description="Third support."),
            Port("s4", _PRICE, description="Fourth support."),
        ),
        params=(),
        warmup_fn=_warmup_constant(2),
        description="Camarilla pivot levels derived from the previous bar's range.",
    ),
)


INDICATOR_SPECS_BY_ID: Dict[str, IndicatorSpec] = {
    spec.block_id: spec for spec in INDICATOR_SPECS
}


def get_indicator_spec(block_id: str) -> Optional[IndicatorSpec]:
    """Descriptor for ``block_id``, or None when it is not an indicator."""
    return INDICATOR_SPECS_BY_ID.get(str(block_id).lower())


def resolve_indicator_runtime(block_id: str) -> Callable[..., Any]:
    """Callable behind ``block_id``. Raises KeyError when unknown."""
    spec = get_indicator_spec(block_id)
    if spec is None:
        raise KeyError(f"Unknown indicator block_id '{block_id}'")
    return spec.resolve_runtime()


def indicator_warmup(block_id: str, params: Optional[Dict[str, Any]] = None) -> int:
    """Warmup bars ``block_id`` needs under ``params``."""
    spec = get_indicator_spec(block_id)
    if spec is None:
        raise KeyError(f"Unknown indicator block_id '{block_id}'")
    return spec.warmup(params)


def _required_positional_count(fn: Callable[..., Any]) -> int:
    """How many leading positional arguments ``fn`` requires (its data inputs)."""
    count = 0
    for parameter in inspect.signature(fn).parameters.values():
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            continue
        if parameter.default is inspect.Parameter.empty:
            count += 1
    return count


def _assert_specs_are_runnable() -> None:
    """Fail import when a spec advertises something this module cannot run.

    This is the same guarantee ``build_registry()`` asserts at startup, applied
    one level earlier so a bad descriptor can never reach the registry.
    """
    for spec in INDICATOR_SPECS:
        try:
            fn = spec.resolve_runtime()
        except ValueError as exc:
            raise RuntimeError(
                f"Indicator spec '{spec.block_id}' is not runnable: {exc}"
            ) from exc

        expected_inputs = _required_positional_count(fn)
        if len(spec.inputs) != expected_inputs:
            raise RuntimeError(
                f"Indicator spec '{spec.block_id}' declares {len(spec.inputs)} input "
                f"port(s) but '{spec.runtime_ref}' requires {expected_inputs} "
                "positional argument(s)"
            )

        accepted = set(inspect.signature(fn).parameters)
        for param in spec.params:
            if param.forward_to_runtime and param.key not in accepted:
                raise RuntimeError(
                    f"Indicator spec '{spec.block_id}' forwards unknown param "
                    f"'{param.key}' to '{spec.runtime_ref}'"
                )


_assert_specs_are_runnable()


#: Selectable indicators, derived from the descriptors above. Nothing is listed
#: here that cannot be run, and nothing runnable is missing (defect SB-04).
AVAILABLE_INDICATORS: List[str] = [spec.block_id for spec in INDICATOR_SPECS]
